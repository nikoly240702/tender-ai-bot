"""Разбор выдачи Yandex Search API."""
import base64

import pytest

from tender_sniper.search.yandex_search import (
    SearchResult,
    YandexSearchError,
    _decode_raw,
    is_configured,
    parse_response_xml,
)

SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<yandexsearch version="1.0">
  <response>
    <results>
      <grouping>
        <group>
          <doc>
            <url>https://www.komus.ru/katalog/bumaga-a4/p/123</url>
            <title>Бумага офисная <hlword>А4</hlword> 80 г/м2 500 листов</title>
            <headline>Заголовок для запасного варианта</headline>
            <passages>
              <passage>Цена <hlword>от 420</hlword> руб. за пачку, в наличии</passage>
            </passages>
          </doc>
        </group>
        <group>
          <doc>
            <url>https://vasko.ru/product/456</url>
            <title>Бумага для печати</title>
          </doc>
        </group>
      </grouping>
    </results>
  </response>
</yandexsearch>"""


@pytest.mark.unit
class TestParseResponse:
    def test_extracts_results(self):
        out = parse_response_xml(SAMPLE)
        assert len(out) == 2
        assert out[0].url == 'https://www.komus.ru/katalog/bumaga-a4/p/123'
        assert out[1].url == 'https://vasko.ru/product/456'

    def test_highlighted_words_do_not_truncate_text(self):
        """Яндекс подсвечивает совпадения тегом <hlword> внутри title и
        passage. Если брать только node.text, фраза обрывается на первом
        же выделенном слове."""
        out = parse_response_xml(SAMPLE)
        assert 'А4' in out[0].title
        assert '80 г/м2' in out[0].title, 'текст после <hlword> потерялся'
        assert 'от 420' in out[0].snippet
        assert 'в наличии' in out[0].snippet, 'текст после <hlword> потерялся'

    def test_falls_back_to_headline_without_passages(self):
        out = parse_response_xml(SAMPLE)
        assert out[1].snippet == ''  # у второго нет ни passages, ни headline

    def test_error_response_raises(self):
        xml = ('<yandexsearch><response><error code="55">Неверный ключ'
               '</error></response></yandexsearch>')
        with pytest.raises(YandexSearchError) as e:
            parse_response_xml(xml)
        assert 'Неверный ключ' in str(e.value)

    def test_documents_without_url_are_skipped(self):
        xml = ('<yandexsearch><response><results><grouping><group>'
               '<doc><title>без ссылки</title></doc>'
               '</group></grouping></results></response></yandexsearch>')
        assert parse_response_xml(xml) == []

    def test_empty_results(self):
        xml = '<yandexsearch><response><results><grouping/></results></response></yandexsearch>'
        assert parse_response_xml(xml) == []


@pytest.mark.unit
class TestDecodeRaw:
    def test_decodes_base64(self):
        raw = base64.b64encode('<a>тест</a>'.encode('utf-8')).decode()
        assert _decode_raw(raw) == '<a>тест</a>'

    def test_plain_xml_passes_through(self):
        """Подстраховка: если API когда-нибудь отдаст XML без base64,
        падать из-за этого не стоит."""
        assert '<yandexsearch' in _decode_raw(SAMPLE)


@pytest.mark.unit
class TestDomain:
    def test_strips_www(self):
        r = SearchResult(url='https://www.komus.ru/x', title='', snippet='')
        assert r.domain == 'komus.ru'

    def test_broken_url_does_not_raise(self):
        assert SearchResult(url='не ссылка', title='', snippet='').domain == ''


@pytest.mark.unit
class TestConfiguration:
    def test_reports_missing_credentials(self, monkeypatch):
        monkeypatch.delenv('YANDEX_SEARCH_API_KEY', raising=False)
        monkeypatch.delenv('YANDEX_SEARCH_FOLDER_ID', raising=False)
        assert is_configured() is False

    def test_needs_both_key_and_folder(self, monkeypatch):
        """Ключа без каталога недостаточно — запрос уйдёт в никуда."""
        monkeypatch.setenv('YANDEX_SEARCH_API_KEY', 'k')
        monkeypatch.delenv('YANDEX_SEARCH_FOLDER_ID', raising=False)
        assert is_configured() is False
        monkeypatch.setenv('YANDEX_SEARCH_FOLDER_ID', 'f')
        assert is_configured() is True
