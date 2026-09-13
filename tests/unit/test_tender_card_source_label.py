import pytest
from bot.formatters.tender_card import format_tender_card

BASE_TENDER = {
    'number': '0327600003126000023', 'name': 'Поставка бумаги',
    'price': 100000, 'url': 'https://zakupki.gov.ru/x', 'region': 'Москва',
    'customer_name': 'ГБУ Тест',
}
MATCH_INFO = {'score': 50, 'matched_keywords': ['бумага']}


@pytest.mark.unit
class TestSourceLabel:
    def test_no_source_label_key_unchanged(self):
        text, _ = format_tender_card(BASE_TENDER, MATCH_INFO, 'Бумага офисная')
        assert 'Портал поставщиков' not in text

    def test_source_label_shown_when_present(self):
        tender = {**BASE_TENDER, 'number': 'MOS-4271956', 'source_label': 'Портал поставщиков (Москва)'}
        text, _ = format_tender_card(tender, MATCH_INFO, 'Бумага офисная')
        assert 'Портал поставщиков (Москва)' in text
