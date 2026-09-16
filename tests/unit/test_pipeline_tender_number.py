"""Нормализация номера тендера при ручном создании карточки.

Регрессия: вставленная целиком ссылка уходила в БД как есть и роняла
создание карточки в 500 (value too long for character varying(40)),
а московские КС не поддерживались вовсе — им подставлялась ссылка на ЕИС.
"""
import pytest

from cabinet.pipeline_service import (
    TENDER_NUMBER_MAX_LEN,
    _fallback_tender_url,
    normalize_tender_number,
)


@pytest.mark.unit
class TestNormalizeTenderNumber:
    def test_plain_eis_reg_number_passes_through(self):
        assert normalize_tender_number('0373100108126000448') == '0373100108126000448'

    def test_eis_url_yields_reg_number(self):
        url = ('https://zakupki.gov.ru/epz/order/notice/ea20/view/'
               'common-info.html?regNumber=0373100108126000448')
        assert normalize_tender_number(url) == '0373100108126000448'

    def test_moscow_url_yields_mos_prefixed_number(self):
        assert normalize_tender_number('https://zakupki.mos.ru/auction/10297580') == 'MOS-10297580'

    def test_short_bare_number_treated_as_moscow(self):
        """Реестровый номер ЕИС — 19 цифр; заметно более короткий — КС Москвы."""
        assert normalize_tender_number('10297580') == 'MOS-10297580'

    def test_explicit_mos_prefix_kept(self):
        assert normalize_tender_number('MOS-10297580') == 'MOS-10297580'
        assert normalize_tender_number('mos-10297580') == 'MOS-10297580'

    def test_surrounding_whitespace_ignored(self):
        assert normalize_tender_number('  0373100108126000448  ') == '0373100108126000448'

    def test_garbage_rejected(self):
        assert normalize_tender_number('просто текст') is None
        assert normalize_tender_number('') is None
        assert normalize_tender_number('   ') is None

    def test_every_accepted_form_fits_the_column(self):
        """Главное свойство: что бы ни вернул нормализатор, оно влезает в
        колонку — иначе снова 500 вместо понятной ошибки."""
        samples = [
            '0373100108126000448',
            'https://zakupki.gov.ru/epz/order/notice/ea20/view/'
            'common-info.html?regNumber=0373100108126000448',
            'https://zakupki.mos.ru/auction/10297580',
            'MOS-10297580',
        ]
        for raw in samples:
            out = normalize_tender_number(raw)
            assert out is not None
            assert len(out) <= TENDER_NUMBER_MAX_LEN, raw


@pytest.mark.unit
class TestFallbackTenderUrl:
    def test_moscow_number_links_to_supplier_portal(self):
        assert _fallback_tender_url('MOS-10297580') == 'https://zakupki.mos.ru/auction/10297580'

    def test_eis_number_links_to_eis(self):
        url = _fallback_tender_url('0373100108126000448')
        assert url.startswith('https://zakupki.gov.ru/')
        assert url.endswith('regNumber=0373100108126000448')
