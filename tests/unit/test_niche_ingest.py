"""Загрузка исторических данных ЕИС: фильтрация, ключи, разбор в строки."""
import datetime

import pytest

from tender_sniper.niche import storage
from tender_sniper.niche.ingest import (
    CFO_REGIONS,
    NMCK_CEILING,
    archive_key,
    keep_procedure,
    month_days,
    parse_month,
    resolve_regions,
)
from tender_sniper.sources.eis_integration import parse_protocol_final

EP = "http://zakupki.gov.ru/oos/EPtypes/1"
CMN = "http://zakupki.gov.ru/oos/common/1"


def notice_xml(purchase_number="0338300003326000140", max_price="917606.00",
               okpd_codes=("21.20",), publish="2026-09-10T20:09:10+12:00",
               quantity_undefined="false"):
    okpd = "".join(
        f'<ns0:OKPD2><ns0:OKPDCode>{c}</ns0:OKPDCode>'
        f'<ns0:OKPDName>Препараты</ns0:OKPDName></ns0:OKPD2>' for c in okpd_codes)
    return (
        f'<ns0:export xmlns:ns0="{EP}" xmlns:ns1="{CMN}">'
        f'<ns0:epNotificationEF2020>'
        f'<ns0:commonInfo>'
        f'<ns0:purchaseNumber>{purchase_number}</ns0:purchaseNumber>'
        f'<ns0:publishDTInEIS>{publish}</ns0:publishDTInEIS>'
        f'<ns0:placingWay><ns1:code>EAP20</ns1:code>'
        f'<ns1:name>Электронный аукцион</ns1:name></ns0:placingWay>'
        f'</ns0:commonInfo>'
        f'<ns0:purchaseObjectsInfo>'
        f'<ns0:quantityUndefined>{quantity_undefined}</ns0:quantityUndefined>'
        f'</ns0:purchaseObjectsInfo>'
        f'<ns0:customer><ns1:INN>4102003181</ns1:INN>'
        f'<ns1:fullName>ГБУЗ КК ВГБ</ns1:fullName></ns0:customer>'
        f'<ns0:contractConditionsInfo><ns0:maxPriceInfo>'
        f'<ns0:maxPrice>{max_price}</ns0:maxPrice>'
        f'<ns0:currency><ns1:code>RUB</ns1:code></ns0:currency>'
        f'</ns0:maxPriceInfo>'
        f'<ns0:IKZInfo><ns0:OKPD2Info>{okpd}</ns0:OKPD2Info>'
        # Внутри ИКЗ лежит свой короткий purchaseNumber — ловушка.
        f'<ns0:purchaseNumber>0150</ns0:purchaseNumber></ns0:IKZInfo>'
        f'</ns0:contractConditionsInfo>'
        f'</ns0:epNotificationEF2020></ns0:export>').encode("utf-8")


def parse(xml_bytes, region="77", source="src"):
    import xml.etree.ElementTree as ET
    return storage.parse_notice(ET.fromstring(xml_bytes.decode("utf-8")),
                                region_code=region, source=source)


@pytest.mark.unit
class TestRegions:
    def test_cfo_group_expands(self):
        assert resolve_regions("ЦФО") == CFO_REGIONS
        assert len(CFO_REGIONS) == 18
        assert "77" in CFO_REGIONS and "50" in CFO_REGIONS

    def test_explicit_codes(self):
        assert resolve_regions("77, 50") == ["77", "50"]

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            resolve_regions("Москва")

    def test_far_east_is_not_in_cfo(self):
        """ДФО и Сибирь из интереса исключены — данные по ним не собираем."""
        assert "41" not in CFO_REGIONS   # Камчатский край
        assert "24" not in CFO_REGIONS   # Красноярский край


@pytest.mark.unit
class TestPeriod:
    def test_month_expands_to_first_and_last_day(self):
        assert parse_month("2026-09") == datetime.date(2026, 9, 1)
        assert parse_month("2026-09", last_day=True) == datetime.date(2026, 9, 30)

    def test_december_does_not_overflow_the_year(self):
        assert parse_month("2026-12", last_day=True) == datetime.date(2026, 12, 31)

    def test_leap_february(self):
        assert parse_month("2028-02", last_day=True) == datetime.date(2028, 2, 29)

    def test_exact_date_is_accepted(self):
        assert parse_month("2026-09-17") == datetime.date(2026, 9, 17)

    def test_day_range_is_inclusive(self):
        days = list(month_days(datetime.date(2026, 9, 1), datetime.date(2026, 9, 3)))
        assert days == [datetime.date(2026, 9, d) for d in (1, 2, 3)]


@pytest.mark.unit
class TestFiltering:
    def test_keeps_procedures_within_ceiling(self):
        assert keep_procedure({"nmck": NMCK_CEILING}) is True
        assert keep_procedure({"nmck": 2_999_999}) is True

    def test_drops_expensive_procedures(self):
        """Фильтруем ДО записи: в базу не должно попадать то, что мы
        заведомо не анализируем."""
        assert keep_procedure({"nmck": NMCK_CEILING + 1}) is False

    def test_unknown_price_is_kept(self):
        """Без НМЦК отбросить нельзя — можно потерять нужную процедуру."""
        assert keep_procedure({"nmck": None}) is True


@pytest.mark.unit
class TestArchiveKey:
    def test_key_is_logical_not_url(self):
        """URL архива одноразовый, с тикетом внутри: как ключ журнала он
        сделал бы повторный запуск бессмысленным — каждый раз новый."""
        key = archive_key("PRIZ", "epNotificationEF2020", "77",
                          datetime.date(2026, 9, 17), 0)
        assert key == "PRIZ/epNotificationEF2020/77/2026-09-17#0"

    def test_same_inputs_give_same_key(self):
        args = ("RGK", "contract", "50", datetime.date(2026, 9, 17), 3)
        assert archive_key(*args) == archive_key(*args)


@pytest.mark.unit
class TestParseNotice:
    def test_procedure_type_is_found(self):
        """Тег зовётся placingWay/name, а не placingWayName: по второму
        имени способ закупки не находился вовсе, и колонка стояла
        пустой. Нашлось скриптом ручной сверки, не тестами."""
        assert parse(notice_xml())["procedure_type"] == "Электронный аукцион"

    def test_extracts_core_fields(self):
        row = parse(notice_xml())
        assert row["purchase_number"] == "0338300003326000140"
        assert row["nmck"] == 917606.00
        assert row["customer_region_code"] == "77"
        assert row["published_at"] == datetime.date(2026, 9, 10)
        assert row["law"] == 44

    def test_short_purchase_number_inside_ikz_is_ignored(self):
        """В ИКЗ свой purchaseNumber из четырёх цифр. Если взять первый
        попавшийся, процедура запишется с номером «0150»."""
        assert parse(notice_xml())["purchase_number"] != "0150"

    def test_okpd2_codes_collected_and_primary_chosen(self):
        row = parse(notice_xml(okpd_codes=("21.20", "21.20", "26.60")))
        assert row["okpd2_codes"] == ["21.20", "26.60"]
        assert row["okpd2_primary"] == "21.20"

    def test_notice_without_okpd2(self):
        row = parse(notice_xml(okpd_codes=()))
        assert row["okpd2_codes"] is None and row["okpd2_primary"] is None

    def test_document_without_usable_number_is_dropped(self):
        assert parse(notice_xml(purchase_number="123")) is None

    def test_indeterminate_volume_is_flagged(self):
        """При неопределённом объёме торгуются СУММЫ ЦЕН ЗА ЕДИНИЦУ, а не
        цена контракта, и сравнивать их с НМЦК нельзя. На реальных данных
        без этого флага встречался «победитель» с 78 млрд против НМЦК в
        1,45 млн, а среднее снижение по нишам уходило в минус тысячи
        процентов. 22% процедур Москвы помечены этим флагом."""
        assert parse(notice_xml(quantity_undefined="true"))["quantity_undefined"] is True

    def test_normal_procedure_is_not_flagged(self):
        assert parse(notice_xml())["quantity_undefined"] is False

    def test_missing_flag_means_normal(self):
        xml = notice_xml().replace(
            b"<ns0:quantityUndefined>false</ns0:quantityUndefined>", b"")
        assert parse(xml)["quantity_undefined"] is False


@pytest.mark.unit
class TestOkpd2Level:
    @pytest.mark.parametrize("code,level", [
        ("21", 2), ("21.20", 4), ("21.20.10", 6), ("21.20.10.134", 6)])
    def test_levels(self, code, level):
        assert storage.okpd2_level(code) == level

    def test_empty(self):
        assert storage.okpd2_level("") is None


@pytest.mark.unit
class TestDateAndNumberParsing:
    def test_iso_datetime_becomes_date(self):
        assert storage.to_date("2026-09-20T20:09:10+12:00") == datetime.date(2026, 9, 20)

    def test_plain_date(self):
        assert storage.to_date("2026-09-15") == datetime.date(2026, 9, 15)

    def test_garbage_is_none_not_an_exception(self):
        assert storage.to_date("не дата") is None
        assert storage.to_date(None) is None
        assert storage.to_decimal("—") is None


@pytest.mark.unit
class TestProtocolToRow:
    PROTO = (
        f'<ns0:export xmlns:ns0="{EP}"><ns0:epProtocolEF2020Final>'
        f'<ns0:commonInfo><ns0:purchaseNumber>0338300003326000140</ns0:purchaseNumber>'
        f'<ns0:publishDTInEIS>2026-09-20T20:09:10+12:00</ns0:publishDTInEIS>'
        f'</ns0:commonInfo><ns0:protocolInfo><ns0:applicationsInfo>'
        f'<ns0:applicationInfo><ns0:commonInfo>'
        f'<ns0:appNumber>1</ns0:appNumber></ns0:commonInfo>'
        f'<ns0:finalPrice>610207.99</ns0:finalPrice>'
        f'<ns0:admittedInfo><ns0:appAdmittedInfo><ns0:admitted>true</ns0:admitted>'
        f'<ns0:appRating>1</ns0:appRating></ns0:appAdmittedInfo></ns0:admittedInfo>'
        f'</ns0:applicationInfo>'
        f'</ns0:applicationsInfo></ns0:protocolInfo>'
        f'</ns0:epProtocolEF2020Final></ns0:export>').encode("utf-8")

    def test_row_carries_counts_and_price(self):
        row = storage.protocol_to_row(parse_protocol_final(self.PROTO), source="s")
        assert row["purchase_number"] == "0338300003326000140"
        assert (row["bids_submitted"], row["bids_admitted"]) == (1, 1)
        assert row["winner_price"] == 610207.99
        assert row["protocol_date"] == datetime.date(2026, 9, 20)

    def test_winner_identity_is_left_empty(self):
        """В протоколе участник обезличен номером заявки — ИНН приходит
        отдельно, из реестра контрактов."""
        row = storage.protocol_to_row(parse_protocol_final(self.PROTO), source="s")
        assert row["winner_inn"] is None and row["winner_name"] is None


@pytest.mark.unit
class TestContractToRow:
    def test_maps_fields(self):
        row = storage.contract_to_row({
            "reg_num": "2774310405926000057",
            "purchase_number": "0373200018826000588",
            "price": "100000.00", "sign_date": "2026-09-15",
            "supplier_inn": "7709568163", "supplier_name": "ГБУ",
            "okpd2": ["95.29.19.221"],
        }, source="s")
        assert row["contract_price"] == 100000.00
        assert row["sign_date"] == datetime.date(2026, 9, 15)
        assert row["okpd2_codes"] == ["95.29.19.221"]

    def test_contract_without_reg_num_is_dropped(self):
        assert storage.contract_to_row({"price": "1"}, source="s") is None


@pytest.mark.unit
class TestUpsert:
    def test_statement_updates_non_key_columns(self):
        """Повторный запуск обязан обновлять, а не падать и не дублировать:
        ЕИС регулярно перевыпускает те же срезы с уточнениями."""
        stmt = storage.upsert(storage.protocol, [{
            "purchase_number": "1", "lot_number": 1, "bids_submitted": 2}])
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "ON CONFLICT" in sql and "DO UPDATE" in sql
        assert "bids_submitted" in sql

    def test_empty_rows_produce_no_statement(self):
        assert storage.upsert(storage.procedure, []) is None


@pytest.mark.unit
class TestDedupeByKey:
    """Один архив ЕИС содержит несколько редакций одного документа.
    PostgreSQL на дубль в пачке отвечает «ON CONFLICT DO UPDATE command
    cannot affect row a second time» и роняет ВЕСЬ архив, а не строку."""

    def test_last_version_wins(self):
        rows = [
            {"purchase_number": "1", "nmck": 100},
            {"purchase_number": "2", "nmck": 200},
            {"purchase_number": "1", "nmck": 999},
        ]
        result = storage.dedupe_by_key(storage.procedure, rows)
        assert len(result) == 2
        assert {r["purchase_number"]: r["nmck"] for r in result} == {"1": 999, "2": 200}

    def test_composite_key_is_respected(self):
        """У протокола ключ составной — разные лоты одной закупки это
        разные строки, схлопывать их нельзя."""
        rows = [
            {"purchase_number": "1", "lot_number": 1, "bids_submitted": 2},
            {"purchase_number": "1", "lot_number": 2, "bids_submitted": 5},
        ]
        assert len(storage.dedupe_by_key(storage.protocol, rows)) == 2

    def test_upsert_applies_dedupe(self):
        rows = [{"purchase_number": "1", "nmck": 1}, {"purchase_number": "1", "nmck": 2}]
        sql = str(storage.upsert(storage.procedure, rows).compile(
            compile_kwargs={"literal_binds": True}))
        assert sql.count("INSERT") == 1
        assert "999" not in sql

    def test_empty_input(self):
        assert storage.dedupe_by_key(storage.procedure, []) == []
