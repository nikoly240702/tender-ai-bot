"""Ссылка на тендер из карточки выдачи ЕИС.

Регрессия 20.09.2026: ЕИС перешёл на абсолютные ссылки в выдаче (10 из
10 в замере). Безусловная склейка с BASE_URL давала хост
«zakupki.gov.ruhttps», прокси не мог его резолвить и отвечал 502 — за
25 минут 1192 несостоявшихся обогащения, поток уведомлений упал с 248
в сутки до одного.
"""
import pytest
from bs4 import BeautifulSoup

from src.parsers.zakupki_parser import ZakupkiParser


def _card(href: str):
    html = f'''
    <div class="registry-entry__form">
      <div class="registry-entry__header-mid__number"><a href="{href}">№ 0338300003326000140</a></div>
      <div class="registry-entry__body-value">Поставка бумаги</div>
    </div>'''
    return BeautifulSoup(html, 'html.parser').find('div', class_='registry-entry__form')


@pytest.mark.unit
class TestTenderCardUrl:
    ABS = 'https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=0338300003326000140'
    REL = '/epz/order/notice/ea20/view/common-info.html?regNumber=0338300003326000140'

    def test_absolute_href_is_used_as_is(self):
        """Текущий формат ЕИС. Склейка здесь ломала загрузку страницы."""
        tender = ZakupkiParser()._parse_tender_card(_card(self.ABS))
        assert tender['url'] == self.ABS

    def test_relative_href_still_gets_the_host(self):
        """Прежний формат должен продолжать работать."""
        tender = ZakupkiParser()._parse_tender_card(_card(self.REL))
        assert tender['url'] == self.ABS

    def test_host_is_never_doubled(self):
        """Тот самый симптом: «zakupki.gov.ruhttps» вместо имени хоста."""
        for href in (self.ABS, self.REL):
            url = ZakupkiParser()._parse_tender_card(_card(href))['url']
            assert url.count('https://') == 1
            assert 'gov.ruhttps' not in url
