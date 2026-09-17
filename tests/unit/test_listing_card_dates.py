"""Разбор дат в карточке выдачи zakupki.gov.ru.

Регрессия: даты брались по позиции (`date_values[1]` -> submission_deadline),
а на карточке их три — «Размещено», «Обновлено», «Окончание подачи заявок».
В срок подачи попадала дата обновления, то есть почти всегда сегодняшняя,
и сервис отбрасывал живой тендер как просроченный.

Набор подписей зависит от типа процедуры: у закупки у единственного
поставщика срока подачи нет вовсе, поэтому позиционный доступ ненадёжен
принципиально, а не только из-за смещения на единицу.
"""
import pytest
from bs4 import BeautifulSoup


def _parse_dates(card_html: str) -> dict:
    """Повторяет логику разбора дат из ZakupkiRSSParser.search_tenders_html."""
    card = BeautifulSoup(card_html, 'html.parser')
    titles = [t.get_text(strip=True) for t in card.find_all('div', class_='data-block__title')]
    values = [v.get_text(strip=True) for v in card.find_all('div', class_='data-block__value')]
    dates = dict(zip(titles, values))
    out = {}
    if dates.get('Размещено'):
        out['published'] = dates['Размещено']
    elif values:
        out['published'] = values[0]
    for label in ('Окончание подачи заявок', 'Окончание подачи заявки',
                  'Дата окончания подачи заявок'):
        if dates.get(label):
            out['submission_deadline'] = dates[label]
            break
    return out


def _block(title: str, value: str) -> str:
    return f'<div class="data-block"><div class="data-block__title">{title}</div>' \
           f'<div class="data-block__value">{value}</div></div>'


@pytest.mark.unit
class TestListingCardDates:
    def test_deadline_is_not_the_updated_date(self):
        """Главная регрессия: между «Размещено» и сроком стоит «Обновлено»."""
        html = (_block('Размещено', '16.09.2026')
                + _block('Обновлено', '17.09.2026')
                + _block('Окончание подачи заявок', '24.09.2026'))
        got = _parse_dates(html)
        assert got['published'] == '16.09.2026'
        assert got['submission_deadline'] == '24.09.2026'
        assert got['submission_deadline'] != '17.09.2026'

    def test_single_supplier_purchase_has_no_deadline(self):
        """У закупки у единственного поставщика срока подачи нет —
        не должны подставлять вместо него соседнюю дату."""
        html = _block('Размещено', '16.09.2026') + _block('Обновлено', '17.09.2026')
        got = _parse_dates(html)
        assert got['published'] == '16.09.2026'
        assert 'submission_deadline' not in got

    def test_order_change_does_not_break_parsing(self):
        """Порядок блоков — не контракт площадки, разбор идёт по подписи."""
        html = (_block('Окончание подачи заявок', '24.09.2026')
                + _block('Размещено', '16.09.2026'))
        got = _parse_dates(html)
        assert got['published'] == '16.09.2026'
        assert got['submission_deadline'] == '24.09.2026'

    def test_alternative_deadline_label(self):
        html = _block('Размещено', '01.10.2026') + _block('Дата окончания подачи заявок', '09.10.2026')
        assert _parse_dates(html)['submission_deadline'] == '09.10.2026'
