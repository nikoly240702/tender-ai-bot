"""Сборщик общего пула через интеграционный сервис ЕИС."""
import datetime

import pytest

from tender_sniper.jobs.pool_sweep_eis import (
    MIN_HOURS_LAG,
    TIME_ZONE_OFFSET,
    _to_datetime,
    notice_to_pool_row,
)
from tender_sniper.regions import ALL_REGIONS
from tender_sniper.sources.eis_integration import (
    NOTICE_TYPES,
    build_request,
    parse_notice_card,
)
from tender_sniper.sources.eis_regions import (
    KLADR_TO_REGION,
    codes_for,
    region_code,
    region_name,
)

EP = "http://zakupki.gov.ru/oos/EPtypes/1"
CMN = "http://zakupki.gov.ru/oos/common/1"

NOTICE = (
    f'<ns0:export xmlns:ns0="{EP}" xmlns:ns1="{CMN}">'
    f'<ns0:epNotificationEF2020><ns0:commonInfo>'
    f'<ns0:purchaseNumber>0301300038126000680</ns0:purchaseNumber>'
    f'<ns0:docNumber>№0301300038126000680</ns0:docNumber>'
    f'<ns0:publishDTInEIS>2026-09-21T13:59:38.369+05:00</ns0:publishDTInEIS>'
    f'<ns0:href>https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html</ns0:href>'
    f'<ns0:placingWay><ns1:code>EAP20</ns1:code>'
    f'<ns1:name>Электронный аукцион</ns1:name></ns0:placingWay>'
    f'<ns0:purchaseObjectInfo>Поставка кондиционера</ns0:purchaseObjectInfo>'
    f'</ns0:commonInfo>'
    f'<ns0:purchaseResponsibleInfo><ns0:responsibleOrgInfo>'
    f'<ns1:fullName>ГБУЗ РБ ГКБ № 1</ns1:fullName>'
    f'<ns1:shortName>ГКБ 1</ns1:shortName>'
    f'</ns0:responsibleOrgInfo></ns0:purchaseResponsibleInfo>'
    f'<ns0:notificationInfo>'
    f'<ns0:procedureInfo><ns0:collectingInfo>'
    f'<ns0:startDT>2026-09-21T13:59:38+05:00</ns0:startDT>'
    f'<ns0:endDT>2026-09-29T08:00:00+05:00</ns0:endDT>'
    f'</ns0:collectingInfo></ns0:procedureInfo>'
    f'<ns0:contractConditionsInfo><ns0:maxPriceInfo>'
    f'<ns0:maxPrice>37800.00</ns0:maxPrice>'
    f'</ns0:maxPriceInfo></ns0:contractConditionsInfo>'
    f'<ns0:purchaseObjectsInfo><ns0:notDrugPurchaseObjectsInfo>'
    f'<ns0:purchaseObject>'
    f'<ns0:name>Кондиционер бытовой</ns0:name>'
    f'<ns0:KTRU><ns0:name>Кондиционер бытовой</ns0:name>'
    f'<ns0:OKPD2><ns1:OKPDName>Кондиционеры бытовые</ns1:OKPDName></ns0:OKPD2>'
    f'</ns0:KTRU>'
    f'<ns0:characteristics>'
    f'<ns0:characteristicsUsingTextForm>'
    f'<ns0:name>Обслуживаемая площадь</ns0:name>'
    f'<ns0:characteristicsFillingInstruction>'
    f'<ns0:name>Участник закупки указывает в заявке конкретное значение</ns0:name>'
    f'</ns0:characteristicsFillingInstruction>'
    f'</ns0:characteristicsUsingTextForm>'
    f'</ns0:characteristics>'
    f'</ns0:purchaseObject>'
    f'</ns0:notDrugPurchaseObjectsInfo></ns0:purchaseObjectsInfo>'
    f'</ns0:notificationInfo>'
    f'</ns0:epNotificationEF2020></ns0:export>').encode("utf-8")


@pytest.mark.unit
class TestRegionDirectory:
    def test_every_canonical_region_has_a_code(self):
        """Если у региона нет кода КЛАДР, его закупки не запрашиваются
        вовсе — и совпадения по такому региону молча пропадают."""
        missing = [r for r in ALL_REGIONS if region_code(r) is None]
        assert missing == []

    def test_names_match_canonical_spelling(self):
        """Название из справочника попадает в tender_pool.region и там
        сравнивается с регионом фильтра. Расхождение в букве — потерянное
        совпадение, поэтому сверяем со списком проекта."""
        canonical = set(ALL_REGIONS)
        unknown = sorted(set(KLADR_TO_REGION.values()) - canonical)
        # Новые территории в списке проекта отсутствуют — это ожидаемо и
        # означает лишь, что фильтры по ним не сработают.
        assert unknown == ["Донецкая Народная Республика", "Запорожская область",
                           "Луганская Народная Республика", "Херсонская область"]

    def test_known_codes(self):
        assert region_name("77") == "Москва"
        assert region_name("50") == "Московская область"
        assert region_name("78") == "Санкт-Петербург"

    def test_single_digit_code_is_padded(self):
        assert region_name("1") == "Республика Адыгея"

    def test_unknown_code(self):
        assert region_name("99") is None and region_name("") is None

    def test_codes_for_skips_unknown_names(self):
        assert codes_for(["Москва", "Атлантида", "Москва"]) == ["77", "77"]


@pytest.mark.unit
class TestNoticeTypes:
    def test_all_four_procedure_kinds_are_covered(self):
        """Брать только электронный аукцион нельзя: на замере 21.09.2026
        запрос котировок, электронный запрос и открытый конкурс дают 39%
        уведомлений, и они бы просто не пришли."""
        assert set(NOTICE_TYPES) == {
            "epNotificationEF2020", "epNotificationEZK2020",
            "epNotificationEZT2020", "epNotificationEOK2020"}


@pytest.mark.unit
class TestHourlyRequest:
    def test_hourly_block_is_used_when_hour_given(self):
        xml = build_request("PRIZ", region="77", doc_type="epNotificationEF2020",
                            date=datetime.date(2026, 9, 18), hour=9, token="t")
        assert "<oneHourInfo>" in xml and "<fromHour>9</fromHour>" in xml
        assert "exactDate" not in xml

    def test_timezone_is_a_bare_number(self):
        """Тип timeZoneDifferenceType это [+-]?\\d{1,3}. «+03:00» сервис
        отвергает кодом 28."""
        xml = build_request("PRIZ", region="77", doc_type="epNotificationEF2020",
                            date=datetime.date(2026, 9, 18), hour=9, token="t")
        assert "<offsetTimeZone>3</offsetTimeZone>" in xml

    def test_daily_mode_still_works(self):
        xml = build_request("RGK", region="77", doc_type="contract",
                            date=datetime.date(2026, 9, 18), token="t")
        assert "<exactDate>2026-09-18</exactDate>" in xml
        assert "oneHourInfo" not in xml

    def test_service_lag_is_respected(self):
        """Сервис отказывает в свежих часах: «час должен отставать от
        текущего более чем на 2 часа». Замер: в 13:14 час 12 отвергнут,
        час 11 отдан. Отсюда задержка уведомлений 3-4 часа."""
        assert MIN_HOURS_LAG >= 3


@pytest.mark.unit
class TestParseNoticeCard:
    def test_name_is_the_purchase_subject(self):
        """Предмет закупки, а не способ проведения. Именно из-за этого в
        проекте заводился resolve_tender_name: в заголовке на сайте часто
        стоит «Запрос котировок в электронной форме»."""
        assert parse_notice_card(NOTICE)["name"] == "Поставка кондиционера"

    def test_procedure_type_does_not_leak_into_the_name(self):
        card = parse_notice_card(NOTICE)
        assert card["procedure_type"] == "Электронный аукцион"
        assert card["name"] != card["procedure_type"]

    def test_customer_is_the_organisation_not_the_placing_way(self):
        """И у способа закупки, и у организации тег зовётся name/fullName.
        Поиск по одному имени тега берёт первый попавшийся."""
        assert parse_notice_card(NOTICE)["customer"] == "ГБУЗ РБ ГКБ № 1"

    def test_price_and_dates(self):
        card = parse_notice_card(NOTICE)
        assert card["price"] == 37800.00
        assert card["submission_deadline"].startswith("2026-09-29")

    def test_number_falls_back_to_doc_number(self):
        xml = NOTICE.replace(
            b"<ns0:purchaseNumber>0301300038126000680</ns0:purchaseNumber>", b"")
        assert parse_notice_card(xml)["tender_number"] == "0301300038126000680"

    def test_document_without_number(self):
        xml = (f'<ns0:export xmlns:ns0="{EP}"><ns0:epNotificationEF2020>'
               f'<ns0:commonInfo/></ns0:epNotificationEF2020></ns0:export>').encode()
        assert parse_notice_card(xml) is None


@pytest.mark.unit
class TestPoolRow:
    def test_maps_card_to_pool_columns(self):
        row = notice_to_pool_row(parse_notice_card(NOTICE), "Москва")
        assert row["tender_number"] == "0301300038126000680"
        assert row["region"] == "Москва"
        assert row["law"] == "44"
        assert row["source"] == "eis_integration"

    def test_dates_are_naive(self):
        """tender_pool хранит naive-даты. Смешать aware и naive в одной
        колонке нельзя — сравнение таких значений падает."""
        row = notice_to_pool_row(parse_notice_card(NOTICE), "Москва")
        assert row["published_at"].tzinfo is None
        assert row["submission_deadline"].tzinfo is None

    def test_timezone_is_converted_not_truncated(self):
        """Публикация в 13:59 по +05:00 — это 11:59 по Москве. Простое
        отбрасывание пояса дало бы 13:59 и сдвинуло бы порядок событий."""
        assert _to_datetime("2026-09-21T13:59:38+05:00") == \
            datetime.datetime(2026, 9, 21, 11, 59, 38)

    def test_naive_input_is_left_alone(self):
        assert _to_datetime("2026-09-21T13:59:38") == \
            datetime.datetime(2026, 9, 21, 13, 59, 38)

    def test_garbage_dates(self):
        assert _to_datetime("не дата") is None and _to_datetime(None) is None

    def test_empty_card(self):
        assert notice_to_pool_row(None, "Москва") is None
        assert notice_to_pool_row({}, "Москва") is None


@pytest.mark.unit
class TestDescription:
    """Описание собирается из позиций закупки.

    Замер 21.09.2026: сбор через сервис даёт 96% покрытия (24 тендера из
    25, о которых уведомил мониторинг), но матчинг по одному названию
    нашёл лишь 31 из 1521. Терялось на сопоставлении, не на сборе.
    """

    def test_collects_position_and_category_names(self):
        d = parse_notice_card(NOTICE)["description"]
        assert "Кондиционер бытовой" in d
        assert "Кондиционеры бытовые" in d

    def test_characteristic_labels_are_excluded(self):
        """Наименования характеристик — ярлыки полей, а не предмет: у
        батареек выходило «…; Форма элемента питания; Размер элемента
        питания; Тип элемента питания». Замер 21.09.2026: с ними
        совпадений стало 219 вместо 31, но 76% дали два сборных фильтра,
        а половина жалась к порогу отсечки. Это ложные срабатывания на
        словах «тип», «размер», «форма», а не находки."""
        d = parse_notice_card(NOTICE)["description"]
        assert "Обслуживаемая площадь" not in d

    def test_filling_instructions_are_excluded(self):
        """Инструкция по заполнению заявки лежит в теге с тем же именем
        name, но к предмету закупки отношения не имеет — в описании это
        шум, сбивающий совпадение по ключевым словам."""
        d = parse_notice_card(NOTICE)["description"]
        assert "Участник закупки указывает" not in d

    def test_duplicates_collapse(self):
        """«Кондиционер бытовой» встречается и как позиция, и как КТРУ."""
        d = parse_notice_card(NOTICE)["description"]
        assert d.count("Кондиционер бытовой") == 1

    def test_description_reaches_the_pool_row(self):
        row = notice_to_pool_row(parse_notice_card(NOTICE), "Москва")
        assert row["description"] and "Кондиционер" in row["description"]

    def test_notice_without_positions(self):
        xml = (f'<ns0:export xmlns:ns0="{EP}"><ns0:epNotificationEF2020>'
               f'<ns0:commonInfo><ns0:purchaseNumber>0301300038126000680'
               f'</ns0:purchaseNumber></ns0:commonInfo>'
               f'</ns0:epNotificationEF2020></ns0:export>').encode()
        assert parse_notice_card(xml)["description"] is None
