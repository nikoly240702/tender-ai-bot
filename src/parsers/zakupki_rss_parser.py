"""
Парсер RSS-фидов zakupki.gov.ru.
Это ЛЕГАЛЬНЫЙ и стабильный способ получения данных о тендерах.
"""

import feedparser
import requests
from typing import List, Dict, Any, Optional
from datetime import datetime
from urllib.parse import urlencode, quote_plus
import re
import warnings
import os

# Отключаем предупреждения SSL (для zakupki.gov.ru)
warnings.filterwarnings('ignore', message='Unverified HTTPS request')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except:
    pass


class ZakupkiRSSParser:
    """Парсер RSS-фидов для zakupki.gov.ru."""

    BASE_URL = "https://zakupki.gov.ru"
    RSS_BASE = f"{BASE_URL}/epz/order/extendedsearch/rss.html"

    def __init__(self, timeout: int = 60):
        """
        Инициализация RSS парсера.

        Args:
            timeout: Таймаут запросов в секундах
        """
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })

        # Настройка прокси если указан в переменных окружения
        proxy_url = os.getenv('PROXY_URL', '').strip()
        if proxy_url:
            self.session.proxies = {
                'http': proxy_url,
                'https': proxy_url
            }
            print(f"🔐 RSS парсер использует прокси: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")

        # Полное отключение SSL verify для прокси
        self.session.verify = False

        # Настройка SSL контекста для игнорирования ошибок
        import ssl
        from requests.adapters import HTTPAdapter
        from urllib3.util.ssl_ import create_urllib3_context
        from urllib3.util.retry import Retry

        class SSLAdapter(HTTPAdapter):
            """HTTPAdapter с отключенной проверкой SSL."""
            def init_poolmanager(self, *args, **kwargs):
                context = create_urllib3_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                context.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
                kwargs['ssl_context'] = context
                return super().init_poolmanager(*args, **kwargs)

        retry_strategy = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = SSLAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def search_tenders_rss(
        self,
        keywords: Optional[str] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
        max_results: int = 50,
        regions: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Ищет тендеры через RSS-фид zakupki.gov.ru.

        Args:
            keywords: Ключевые слова для поиска
            price_min: Минимальная цена контракта (руб)
            price_max: Максимальная цена контракта (руб)
            max_results: Максимальное количество результатов
            regions: Список регионов для фильтрации

        Returns:
            Список найденных тендеров
        """
        print(f"📡 Получение RSS-фида от zakupki.gov.ru...")

        try:
            # Формируем URL RSS-фида с параметрами
            rss_url = self._build_rss_url(
                keywords=keywords,
                price_min=price_min,
                price_max=price_max,
                regions=regions
            )

            print(f"   RSS URL: {rss_url[:100]}...")

            # Получаем RSS через requests (обходим SSL проблему)
            try:
                response = self.session.get(rss_url, timeout=self.timeout, verify=False)
                response.raise_for_status()
                rss_content = response.content
            except Exception as e:
                print(f"⚠️  Ошибка загрузки RSS через requests: {e}")
                # Пробуем через feedparser напрямую
                rss_content = rss_url

            # Парсим RSS
            feed = feedparser.parse(rss_content)

            if feed.bozo and not feed.entries:
                print(f"⚠️  Ошибка парсинга RSS: {feed.bozo_exception}")

            tenders = []
            for entry in feed.entries[:max_results]:
                tender = self._parse_rss_entry(entry)
                if tender:
                    tenders.append(tender)

            print(f"✓ Получено тендеров из RSS: {len(tenders)}")
            return tenders

        except Exception as e:
            print(f"✗ Ошибка получения RSS: {e}")
            return []

    def _build_rss_url(
        self,
        keywords: Optional[str],
        price_min: Optional[int],
        price_max: Optional[int],
        regions: Optional[List[str]] = None
    ) -> str:
        """Формирует URL для RSS-фида с параметрами поиска."""
        params = {
            'morphology': 'on',
            'search-filter': 'Дате размещения',
            'sortDirection': 'false',
            'sortBy': 'UPDATE_DATE',
            'fz44': 'on',  # 44-ФЗ
            'fz223': 'on',  # 223-ФЗ
            'af': 'on',  # Все этапы
            'currencyIdGeneral': '-1'
        }

        # Ключевые слова (регионы НЕ добавляем в поисковую строку)
        # Фильтрация по регионам всегда происходит после получения результатов
        if keywords:
            params['searchString'] = keywords

        # Ценовой диапазон
        if price_min:
            params['priceFromGeneral'] = str(price_min)
        if price_max:
            params['priceToGeneral'] = str(price_max)

        # Формируем query string с правильным кодированием
        query_string = urlencode(params, quote_via=quote_plus)
        return f"{self.RSS_BASE}?{query_string}"

    def _parse_rss_entry(self, entry) -> Optional[Dict[str, Any]]:
        """Парсит одну запись из RSS-фида."""
        try:
            tender = {
                'name': entry.get('title', ''),
                'url': entry.get('link', ''),
                'published': entry.get('published', ''),
                'summary': entry.get('summary', ''),
            }

            # Извлекаем номер из URL или заголовка
            tender['number'] = self._extract_number(entry.get('link', ''))

            # Извлекаем цену из описания (если есть)
            price = self._extract_price_from_summary(entry.get('summary', ''))
            if price:
                tender['price'] = price
                tender['price_formatted'] = f"{price:,.2f} ₽"

            # Парсим дату
            if entry.get('published_parsed'):
                tender['published_datetime'] = datetime(*entry.published_parsed[:6])

            return tender if tender.get('name') else None

        except Exception as e:
            print(f"   Ошибка парсинга RSS entry: {e}")
            return None

    def _extract_number(self, url: str) -> str:
        """Извлекает номер тендера из URL."""
        match = re.search(r'regNumber=([A-Z0-9]+)', url)
        if match:
            return match.group(1)
        return ""

    def _extract_price_from_summary(self, summary: str) -> Optional[float]:
        """Извлекает цену из описания RSS."""
        # Ищем паттерны цен в тексте
        patterns = [
            r'НМЦК[:\s]+([0-9\s,\.]+)',
            r'цен[а-я]*[:\s]+([0-9\s,\.]+)',
            r'сумм[а-я]*[:\s]+([0-9\s,\.]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, summary, re.IGNORECASE)
            if match:
                price_text = match.group(1)
                try:
                    cleaned = re.sub(r'[^\d,.]', '', price_text)
                    cleaned = cleaned.replace(',', '.')
                    return float(cleaned)
                except:
                    continue

        return None

    def get_tender_categories_rss(self) -> List[str]:
        """
        Возвращает популярные категории тендеров для формирования RSS подписок.

        Returns:
            Список категорий
        """
        return [
            "компьютерное оборудование",
            "офисная техника",
            "программное обеспечение",
            "серверное оборудование",
            "сетевое оборудование",
            "оргтехника",
            "канцелярские товары",
            "мебель",
            "медицинское оборудование",
            "строительные работы"
        ]


def main():
    """Пример использования RSS парсера."""
    parser = ZakupkiRSSParser()

    # Тестовый поиск через RSS
    print("\n" + "="*70)
    print("ТЕСТ RSS ПАРСЕРА ZAKUPKI.GOV.RU")
    print("="*70)

    tenders = parser.search_tenders_rss(
        keywords="компьютерное оборудование",
        price_min=500000,
        price_max=5000000,
        max_results=10
    )

    print(f"\n📊 Результаты:")
    print(f"   Найдено тендеров: {len(tenders)}\n")

    for i, tender in enumerate(tenders[:5], 1):
        print(f"{i}. {tender.get('name', 'Без названия')[:80]}")
        print(f"   Номер: {tender.get('number', 'N/A')}")
        if tender.get('price'):
            print(f"   Цена: {tender.get('price_formatted', 'N/A')}")
        print(f"   URL: {tender.get('url', 'N/A')}")
        print(f"   Дата: {tender.get('published', 'N/A')}")
        print()


if __name__ == "__main__":
    main()
