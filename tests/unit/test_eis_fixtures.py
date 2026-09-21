"""Разбор НАСТОЯЩИХ документов ЕИС, не синтетических.

Остальные тесты работают на фикстурах, которые я написал руками по
образцу. Такие фикстуры проверяют логику, но не защищают от главного
риска: я мог неверно понять структуру документа и построить по своему
пониманию и парсер, и фикстуру — тогда тест зелёный, а разбор неверен.

Здесь лежат три документа, скачанные из сервиса ЕИС 21.09.2026:
извещение и протокол одной процедуры (0338300003326000140) и контракт
из выгрузки по Москве. Значения в проверках сверены с карточкой на
сайте вручную.
"""
import pathlib

import pytest

from tender_sniper.niche import storage
from tender_sniper.sources.eis_integration import (
    parse_contract,
    parse_notice_card,
    parse_protocol_final,
    parse_xml,
)

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "eis"


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.mark.unit
class TestRealNotice:
    XML = None

    @pytest.fixture(autouse=True)
    def _load(self):
        type(self).XML = load("epNotificationEF2020.xml")

    def test_procedure_row(self):
        row = storage.parse_notice(parse_xml(self.XML), region_code="41",
                                   source="fixture")
        assert row["purchase_number"] == "0338300003326000140"
        assert row["nmck"] == 917606.00
        assert row["procedure_type"] == "Электронный аукцион"
        assert row["okpd2_primary"]

    def test_short_number_inside_ikz_is_not_taken(self):
        """В ИКЗ свой purchaseNumber из четырёх цифр."""
        row = storage.parse_notice(parse_xml(self.XML), region_code="41",
                                   source="fixture")
        assert len(row["purchase_number"]) == 19

    def test_pool_card(self):
        card = parse_notice_card(self.XML)
        assert card["tender_number"] == "0338300003326000140"
        assert card["price"] == 917606.00
        assert card["name"]
        assert card["customer"]

    def test_name_is_the_subject_not_the_procedure_kind(self):
        card = parse_notice_card(self.XML)
        assert card["name"] != card["procedure_type"]
        assert "аукцион" not in (card["name"] or "").lower()

    def test_description_has_content_without_field_labels(self):
        card = parse_notice_card(self.XML)
        assert card["description"]
        # Ярлыки характеристик в описание не входят — они дают сборным
        # фильтрам общеупотребительные слова и ложные срабатывания.
        assert "Участник закупки указывает" not in card["description"]


@pytest.mark.unit
class TestRealProtocol:
    def test_two_applications_with_prices(self):
        """Сверено с карточкой: сайт показывал одного участника, в
        протоколе их два — 610 207,99 и 614 796,02."""
        r = parse_protocol_final(load("epProtocolEF2020Final.xml"))
        assert r.purchase_number == "0338300003326000140"
        assert r.bids_submitted == 2
        assert r.bids_admitted == 2
        assert sorted(a.price for a in r.applications) == [610207.99, 614796.02]

    def test_winner_price_matches_the_site(self):
        r = parse_protocol_final(load("epProtocolEF2020Final.xml"))
        assert r.winner_price == 610207.99
        assert r.is_failed is False

    def test_commission_votes_do_not_inflate_bid_count(self):
        """Главная ловушка настоящего документа: тег admitted встречается
        и как решение по заявке, и как голос каждого члена комиссии.
        В этом протоколе комиссия из нескольких человек, и наивный разбор
        насчитал бы заявок кратно больше."""
        xml = load("epProtocolEF2020Final.xml")
        assert xml.count(b"admitted>") > 4    # голосов в документе много
        assert parse_protocol_final(xml).bids_submitted == 2

    def test_protocol_row(self):
        row = storage.protocol_to_row(
            parse_protocol_final(load("epProtocolEF2020Final.xml")),
            source="fixture")
        assert row["bids_submitted"] == 2
        assert row["winner_price"] == 610207.99
        # Победитель обезличен: ИНН появляется только в реестре контрактов.
        assert row["winner_inn"] is None


@pytest.mark.unit
class TestRealContract:
    def test_supplier_and_price(self):
        c = parse_contract(load("contract.xml"))
        assert c["supplier_inn"] and len(c["supplier_inn"]) in (10, 12)
        assert c["price"]
        assert c["reg_num"]

    def test_procedure_number_is_the_notification_number(self):
        """Поле зовётся notificationNumber. По purchaseNumber выходило
        пусто, и контракт не связывался с протоколом — то есть терялся
        ИНН победителя."""
        c = parse_contract(load("contract.xml"))
        assert c["purchase_number"] and len(c["purchase_number"]) == 19
        assert c["purchase_number"] != c["reg_num"]

    def test_okpd2_codes_present(self):
        assert parse_contract(load("contract.xml"))["okpd2"]

    def test_contract_row(self):
        row = storage.contract_to_row(parse_contract(load("contract.xml")),
                                      source="fixture")
        assert row["contract_price"] and row["sign_date"]
