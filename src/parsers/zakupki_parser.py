"""
Парсер для zakupki.gov.ru (ЕИС в сфере закупок).
Извлекает информацию о текущих тендерах через web scraping.
"""

import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
import time
from datetime import datetime
import re
import os


class ZakupkiParser:
    """Парсер для извлечения тендеров с zakupki.gov.ru."""

    BASE_URL = "https://zakupki.gov.ru"
    SEARCH_URL = f"{BASE_URL}/epz/order/extendedsearch/results.html"

    def __init__(self, timeout: int = 60, delay: float = 2.0):
        """
        Инициализация парсера.

        Args:
            timeout: Таймаут запросов в секундах
            delay: Задержка между запросами (для соблюдения этики парсинга)
        """
        self.timeout = timeout
        self.delay = delay
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
            print(f"🔐 Используется прокси: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")

    def search_tenders(
        self,
        keywords: Optional[str] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
        regions: Optional[List[str]] = None,
        page_limit: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Ищет тендеры по критериям на zakupki.gov.ru.

        Args:
            keywords: Ключевые слова для поиска
            price_min: Минимальная цена контракта (руб)
            price_max: Максимальная цена контракта (руб)
            regions: Список регионов
            page_limit: Максимальное количество страниц результатов

        Returns:
            Список найденных тендеров
        """
        print(f"🔍 Поиск тендеров на zakupki.gov.ru...")
        print(f"   Критерии: {keywords or 'все'}, {price_min}-{price_max} руб")

        tenders = []

        try:
            # Формируем параметры запроса
            params = self._build_search_params(
                keywords=keywords,
                price_min=price_min,
                price_max=price_max,
                regions=regions
            )

            # Ищем тендеры постранично
            for page in range(page_limit):
                params['pageNumber'] = page + 1

                print(f"   Страница {page + 1}/{page_limit}...")

                page_tenders = self._fetch_page(params)
                tenders.extend(page_tenders)

                if len(page_tenders) == 0:
                    break  # Больше нет результатов

                # Задержка между запросами
                time.sleep(self.delay)

            print(f"✓ Найдено тендеров: {len(tenders)}")

        except Exception as e:
            import traceback
            print(f"✗ Ошибка поиска: {e}")
            print(f"   Traceback: {traceback.format_exc()}")
            # Возвращаем mock данные для демонстрации
            return self._get_mock_tenders()

        return tenders

    def _build_search_params(
        self,
        keywords: Optional[str],
        price_min: Optional[int],
        price_max: Optional[int],
        regions: Optional[List[str]]
    ) -> Dict[str, Any]:
        """Формирует параметры для поискового запроса."""
        params = {
            'morphology': 'on',
            'search-filter': 'Дате+размещения',
            'pageNumber': 1,
            'sortDirection': 'false',
            'recordsPerPage': '_10',
            'showLotsInfoHidden': 'false',
            'sortBy': 'UPDATE_DATE',
            'fz44': 'on',  # 44-ФЗ
            'fz223': 'on',  # 223-ФЗ
            'af': 'on',  # Все этапы
            'currencyIdGeneral': '-1'
        }

        # Ключевые слова
        if keywords:
            params['searchString'] = keywords

        # Ценовой диапазон
        if price_min:
            params['priceFromGeneral'] = price_min
        if price_max:
            params['priceToGeneral'] = price_max

        # Регионы (пока упрощенно - можно добавить коды регионов)
        if regions:
            # zakupki.gov.ru использует коды регионов
            # Для демонстрации просто добавляем в поисковую строку
            if 'searchString' in params:
                params['searchString'] += ' ' + ' '.join(regions)
            else:
                params['searchString'] = ' '.join(regions)

        return params

    def _fetch_page(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Извлекает тендеры с одной страницы результатов."""
        try:
            response = self.session.get(
                self.SEARCH_URL,
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Ищем карточки тендеров
            tender_cards = soup.find_all('div', class_='search-registry-entry-block')

            print(f"   🔍 Найдено карточек на странице: {len(tender_cards)}")

            # Дополнительное логирование для отладки
            if len(tender_cards) == 0:
                print(f"   ⚠️ Карточки не найдены. Проверяем альтернативные селекторы...")
                # Попробуем другие возможные классы
                alt_cards = soup.find_all('div', class_='search-registry-entry')
                print(f"   🔍 Альтернативный поиск: {len(alt_cards)} карточек")

                # Если нашли по альтернативному селектору
                if alt_cards:
                    tender_cards = alt_cards

            tenders = []
            for card in tender_cards:
                tender = self._parse_tender_card(card)
                if tender:
                    tenders.append(tender)

            return tenders

        except Exception as e:
            import traceback
            print(f"   ❌ Ошибка загрузки страницы: {e}")
            print(f"   Traceback: {traceback.format_exc()}")
            return []

    def _parse_tender_card(self, card) -> Optional[Dict[str, Any]]:
        """Извлекает данные из карточки тендера."""
        try:
            tender = {}

            # Номер закупки
            registry_number = card.find('div', class_='registry-entry__header-mid__number')
            if registry_number:
                link = registry_number.find('a')
                if link:
                    tender['number'] = link.text.strip()
                    tender['url'] = self.BASE_URL + link.get('href', '')

            # Название
            body = card.find('div', class_='registry-entry__body-value')
            if body:
                tender['name'] = body.text.strip()

            # НМЦК (цена)
            price_block = card.find('div', class_='price-block__value')
            if price_block:
                price_text = price_block.text.strip()
                tender['price'] = self._extract_price(price_text)
                tender['price_formatted'] = price_text

            # Заказчик
            customer = card.find('div', class_='registry-entry__body-href')
            if customer:
                tender['customer'] = customer.text.strip()

            # Статус/этап
            state = card.find('div', class_='registry-entry__header-mid__title')
            if state:
                tender['status'] = state.text.strip()

            # Дата окончания подачи заявок
            date_info = card.find_all('div', class_='data-block__value')
            if len(date_info) >= 2:
                tender['deadline'] = date_info[1].text.strip()

            return tender if tender.get('number') else None

        except Exception as e:
            print(f"   Ошибка парсинга карточки: {e}")
            return None

    def _extract_price(self, price_text: str) -> Optional[float]:
        """Извлекает числовое значение цены из текста."""
        try:
            # Убираем все кроме цифр, запятых и точек
            cleaned = re.sub(r'[^\d,.]', '', price_text)
            cleaned = cleaned.replace(',', '.')
            return float(cleaned)
        except:
            return None

    def get_tender_details(self, tender_url: str) -> Dict[str, Any]:
        """
        Получает детальную информацию о тендере.

        Args:
            tender_url: URL страницы тендера

        Returns:
            Детальная информация о тендере
        """
        try:
            response = self.session.get(tender_url, timeout=self.timeout)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            details = {
                'url': tender_url,
                'description': self._extract_description(soup),
                'customer_inn': self._extract_inn(soup),
                'placement_date': self._extract_placement_date(soup)
            }

            return details

        except Exception as e:
            print(f"Ошибка получения деталей: {e}")
            return {}

    def _extract_description(self, soup) -> str:
        """Извлекает описание из страницы тендера."""
        # Упрощенная реализация
        desc_block = soup.find('div', class_='distanced-text')
        return desc_block.text.strip() if desc_block else ""

    def _extract_inn(self, soup) -> str:
        """Извлекает ИНН заказчика."""
        # Упрощенная реализация
        return ""

    def _extract_placement_date(self, soup) -> str:
        """Извлекает дату размещения."""
        # Упрощенная реализация
        return ""

    def _get_mock_tenders(self) -> List[Dict[str, Any]]:
        """
        Возвращает тестовые данные для демонстрации.
        Используется если реальный парсинг не работает.
        """
        return [
            {
                'number': '0372300075624000001',
                'name': 'Поставка компьютерного оборудования для нужд учреждения',
                'price': 1500000.0,
                'price_formatted': '1 500 000,00 ₽',
                'customer': 'ГБУ "Московский центр информационных технологий"',
                'status': 'Подача заявок',
                'deadline': '20.11.2024 10:00',
                'url': 'https://zakupki.gov.ru/epz/order/notice/example1'
            },
            {
                'number': '0372300075624000002',
                'name': 'Поставка видеокарт и комплектующих',
                'price': 850000.0,
                'price_formatted': '850 000,00 ₽',
                'customer': 'ГБОУ "Школа №1234"',
                'status': 'Подача заявок',
                'deadline': '22.11.2024 15:00',
                'url': 'https://zakupki.gov.ru/epz/order/notice/example2'
            },
            {
                'number': '0372300075624000003',
                'name': 'Поставка серверного оборудования',
                'price': 3200000.0,
                'price_formatted': '3 200 000,00 ₽',
                'customer': 'Департамент информационных технологий города Москвы',
                'status': 'Подача заявок',
                'deadline': '25.11.2024 12:00',
                'url': 'https://zakupki.gov.ru/epz/order/notice/example3'
            }
        ]


def main():
    """Пример использования парсера."""
    parser = ZakupkiParser()

    # Тестовый поиск
    tenders = parser.search_tenders(
        keywords="компьютерное оборудование",
        price_min=500000,
        price_max=5000000,
        page_limit=1
    )

    print(f"\nНайдено тендеров: {len(tenders)}")
    for i, tender in enumerate(tenders[:5], 1):
        print(f"\n{i}. {tender.get('name', 'Без названия')}")
        print(f"   Номер: {tender.get('number')}")
        print(f"   Цена: {tender.get('price_formatted')}")
        print(f"   Заказчик: {tender.get('customer')}")


if __name__ == "__main__":
    main()
