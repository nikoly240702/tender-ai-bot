"""Сборщик общего пула тендеров."""
from datetime import datetime

import pytest

from tender_sniper.jobs.pool_sweep import (
    CARDS_PER_PAGE,
    MAX_PAGES_PER_SWEEP,
    _parse_ru_date,
    _row_from_tender,
)


@pytest.mark.unit
class TestParseRuDate:
    def test_date_only(self):
        assert _parse_ru_date('24.09.2026') == datetime(2026, 9, 24)

    def test_date_with_time(self):
        assert _parse_ru_date('24.09.2026 10:30') == datetime(2026, 9, 24, 10, 30)

    def test_garbage_is_none_not_an_exception(self):
        """Площадка иногда пишет в это поле текст вроде «по запросу» —
        падать из-за одной карточки нельзя, обход должен продолжиться."""
        assert _parse_ru_date('по запросу') is None
        assert _parse_ru_date('') is None
        assert _parse_ru_date(None) is None


@pytest.mark.unit
class TestRowFromTender:
    def test_maps_listing_card(self):
        row = _row_from_tender({
            'number': '0373100108126000448',
            'name': 'Поставка бумаги',
            'customer': 'МБДОУ Детский сад',
            'price': 180000.0,
            'url': 'https://zakupki.gov.ru/...',
            'published': '16.09.2026',
            'submission_deadline': '24.09.2026',
        })
        assert row['tender_number'] == '0373100108126000448'
        assert row['published_at'] == datetime(2026, 9, 16)
        assert row['submission_deadline'] == datetime(2026, 9, 24)
        assert row['source'] == 'eis'

    def test_without_number_is_skipped(self):
        """Строка без номера неотличима от других и не годится как ключ."""
        assert _row_from_tender({'name': 'Что-то без номера'}) is None
        assert _row_from_tender({'number': '   ', 'name': 'x'}) is None

    def test_number_fits_the_column(self):
        row = _row_from_tender({'number': '9' * 60, 'name': 'x'})
        assert len(row['tender_number']) <= 40

    def test_missing_optional_fields_are_tolerated(self):
        """У закупки у единственного поставщика нет срока подачи, у
        некоторых карточек не распознаётся цена — это не повод терять тендер."""
        row = _row_from_tender({'number': '123', 'name': 'x'})
        assert row is not None
        assert row['submission_deadline'] is None
        assert row['price'] is None


@pytest.mark.unit
class TestSweepLimits:
    def test_page_size_matches_what_the_site_returns(self):
        """Площадка отдаёт максимум 200 карточек на страницу, сколько бы ни
        просили — проверено на живой выдаче 17.09.2026."""
        assert CARDS_PER_PAGE == 200

    def test_page_cap_covers_a_full_day(self):
        """~6200 извещений в сутки в рабочем диапазоне цен: потолок страниц
        должен перекрывать суточный объём с запасом."""
        assert MAX_PAGES_PER_SWEEP * CARDS_PER_PAGE >= 6200
