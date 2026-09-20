"""Нормализация ссылки на процедуру ЕИС.

С 19.09.2026 ЕИС отдаёт в RSS склеенный дважды домен (замер 20.09.2026:
50 из 50 записей фида). Хост «zakupki.gov.ruhttps» не резолвится, прокси
отвечает 502, обогащение карточек падает целиком — поток уведомлений
упал с 248 в сутки до одного. Дефект чужой, чинить приходится у себя,
поэтому важно, чтобы починка не трогала корректные ссылки.
"""
import pytest

from src.parsers.zakupki_rss_parser import normalize_tender_url

GOOD = 'https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=0338300003326000140'


@pytest.mark.unit
class TestNormalizeTenderUrl:
    def test_repairs_doubled_origin(self):
        assert normalize_tender_url('https://zakupki.gov.ru' + GOOD) == GOOD

    def test_leaves_correct_url_untouched(self):
        assert normalize_tender_url(GOOD) == GOOD

    def test_leaves_relative_url_untouched(self):
        """Относительную ссылку достраивает вызывающий код, не мы."""
        assert normalize_tender_url('/epz/order/notice/ea20/view/common-info.html') == \
            '/epz/order/notice/ea20/view/common-info.html'

    def test_repairs_triple_prefix(self):
        """На случай, если дефект у ЕИС усугубится."""
        assert normalize_tender_url('https://zakupki.gov.ruhttps://zakupki.gov.ru' + GOOD) == GOOD

    def test_handles_http_and_mixed_schemes(self):
        assert normalize_tender_url('http://zakupki.gov.ru' + GOOD) == GOOD

    def test_empty_and_whitespace(self):
        assert normalize_tender_url('') == ''
        assert normalize_tender_url(None) == ''
        assert normalize_tender_url('  ' + GOOD + ' ') == GOOD

    def test_does_not_eat_url_inside_query(self):
        """Ссылка в параметре запроса — не удвоение домена, резать нельзя."""
        url = GOOD + '&back=https://zakupki.gov.ru/epz/main'
        assert normalize_tender_url(url) == url
