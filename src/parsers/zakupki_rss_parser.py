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

    def __init__(self, timeout: int = 30):
        """
        Инициализация RSS парсера.

        Args:
            timeout: Таймаут запросов в секундах
        """
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; TenderBot/1.0)'
        })

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
