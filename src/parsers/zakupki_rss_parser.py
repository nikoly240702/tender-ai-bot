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

    # Коды регионов для API zakupki.gov.ru
    REGION_CODES = {
        "Москва": "5277335",
        "Санкт-Петербург": "5277384",
        "Московская область": "5277327",
        "Краснодарский край": "5277304",
        "Свердловская область": "5277370",
        "Республика Татарстан": "5277358",
        "Нижегородская область": "5277336",
        "Новосибирская область": "5277340",
        "Ростовская область": "5277362",
        "Самарская область": "5277364",
        "Челябинская область": "5277387",
        "Красноярский край": "5277305",
        "Пермский край": "5277346",
        "Воронежская область": "5277297",
        "Волгоградская область": "5277293",
        "Башкортостан": "5277287",
        "Саратовская область": "5277366",
        "Тюменская область": "5277375",
        "Оренбургская область": "5277343",
        "Омская область": "5277342",
        "Кемеровская область": "5277300",
        "Хабаровский край": "5277310",
        "Иркутская область": "5277299",
        "Ленинградская область": "5277316",
        "Алтайский край": "5277282",
        "Приморский край": "5277307",
        "Ульяновская область": "5277377",
        "Ставропольский край": "5277309",
        "Тульская область": "5277374",
        "Владимирская область": "5277292",
        "Ярославская область": "5277391",
        "Калужская область": "5277301",
        "Калининградская область": "5277302",
        "Томская область": "5277372",
        "Рязанская область": "5277363",
        "Тверская область": "5277371",
        "Липецкая область": "5277317",
        "Пензенская область": "5277345",
        "Курская область": "5277314",
        "Брянская область": "5277290",
        "Белгородская область": "5277288",
        "Архангельская область": "5277284",
        "Смоленская область": "5277368",
        "Вологодская область": "5277294",
        "Курганская область": "5277313",
        "Мурманская область": "5277331",
        "Орловская область": "5277344",
        "Тамбовская область": "5277369",
        "Новгородская область": "5277339",
        "Кировская область": "5277303",
        "Костромская область": "5277311",
        "Псковская область": "5277351",
        "Ивановская область": "5277298",
        "Амурская область": "5277283",
        "Астраханская область": "5277285",
        "Забайкальский край": "5277306",
        "Республика Крым": "9311040",
        "Севастополь": "9310785",
    }

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
        regions: Optional[List[str]] = None,
        tender_type: Optional[str] = None,
        # Новые параметры фильтрации
        law_type: Optional[str] = None,  # "44-ФЗ", "223-ФЗ", "all"
        purchase_stage: Optional[str] = None,  # "submission", "all"
        purchase_method: Optional[str] = None,  # "auction", "tender", "quotation", "all"
        date_from: Optional[str] = None,  # "YYYY-MM-DD"
        date_to: Optional[str] = None,  # "YYYY-MM-DD"
    ) -> List[Dict[str, Any]]:
        """
        Ищет тендеры через RSS-фид zakupki.gov.ru.

        Args:
            keywords: Ключевые слова для поиска
            price_min: Минимальная цена контракта (руб)
            price_max: Максимальная цена контракта (руб)
            max_results: Максимальное количество результатов
            regions: Список регионов для фильтрации
            tender_type: Тип закупки ("товары", "услуги", "работы", None для всех)

        Returns:
            Список найденных тендеров
        """
        print(f"📡 Получение RSS-фида от zakupki.gov.ru...")
        if tender_type:
            print(f"   🎯 Фильтр по типу: {tender_type}")

        try:
            # Формируем URL RSS-фида с параметрами
            rss_url = self._build_rss_url(
                keywords=keywords,
                price_min=price_min,
                price_max=price_max,
                regions=regions,
                tender_type=tender_type,
                law_type=law_type,
                purchase_stage=purchase_stage,
                purchase_method=purchase_method,
                date_from=date_from,
                date_to=date_to
            )

            print(f"   RSS URL: {rss_url[:100]}...")

            # Получаем RSS через requests (обходим SSL проблему)
            try:
                response = self.session.get(rss_url, timeout=self.timeout, verify=False)
                response.raise_for_status()
                rss_content = response.content
            except Exception as e:
                error_msg = str(e)
                print(f"⚠️  Ошибка загрузки RSS через requests: {e}")

                # Диагностика проблемы
                if "SSLEOFError" in error_msg or "SSL" in error_msg:
                    print(f"❌ SSL Ошибка: Не удается установить безопасное соединение")
                    print(f"   Возможные причины:")
                    print(f"   1. Прокси сервер не отвечает или недоступен")
                    print(f"   2. zakupki.gov.ru блокирует соединение")
                    print(f"   3. Проблемы с SSL/TLS конфигурацией")
                elif "Proxy" in error_msg:
                    print(f"❌ Прокси Ошибка: Прокси сервер не работает корректно")
                    print(f"   Проверьте PROXY_URL в .env файле")
                elif "timeout" in error_msg.lower():
                    print(f"❌ Timeout: Сервер не отвечает в течение {self.timeout} секунд")

                print(f"\n💡 Рекомендации:")
                print(f"   • Проверьте доступность zakupki.gov.ru")
                print(f"   • Убедитесь, что прокси сервер работает")
                print(f"   • Попробуйте временно отключить прокси (закомментируйте PROXY_URL в .env)")
                print(f"   • Используйте VPN если zakupki.gov.ru заблокирован\n")

                # Возвращаем пустой список вместо краша
                return []

            # Парсим RSS
            feed = feedparser.parse(rss_content)

            if feed.bozo and not feed.entries:
                print(f"⚠️  Ошибка парсинга RSS: {feed.bozo_exception}")
                return []

            tenders = []
            filtered_count = 0

            # Парсим больше записей, чтобы компенсировать фильтрацию
            # Для товаров берем в 5 раз больше, так как многие будут отфильтрованы
            multiplier = 5 if tender_type == "товары" else 3
            entries_to_check = feed.entries[:max_results * multiplier] if tender_type else feed.entries[:max_results]

            for entry in entries_to_check:
                tender = self._parse_rss_entry(entry)
                if not tender:
                    continue

                # Client-side фильтрация по типу закупки (если указан)
                if tender_type == "товары":
                    # Для товаров используем более умную фильтрацию
                    # Проверяем наличие ключевых слов в названии и описании
                    name_lower = tender.get('name', '').lower()
                    summary_lower = tender.get('summary', '').lower()

                    # Индикаторы товаров
                    goods_indicators = [
                        'поставка', 'закупка', 'приобретение', 'покупка',
                        'товар', 'оборудовани', 'материал', 'изделие',
                        'продукция', 'комплект', 'партия'
                    ]

                    # Индикаторы НЕ товаров (услуги/работы)
                    service_indicators = [
                        'оказание услуг', 'выполнение работ', 'проведение работ',
                        'ремонт', 'монтаж', 'установка', 'обслуживание',
                        'консультирование', 'разработка', 'проектирование'
                    ]

                    # Проверяем индикаторы
                    has_goods_indicator = any(ind in name_lower or ind in summary_lower for ind in goods_indicators)
                    has_service_indicator = any(ind in name_lower or ind in summary_lower for ind in service_indicators)

                    # Фильтруем только явные услуги/работы
                    if has_service_indicator and not has_goods_indicator:
                        filtered_count += 1
                        print(f"   ⚠️ Отфильтрован (услуга/работа): {tender.get('name', '')[:50]}...")
                        continue

                elif tender_type:
                    # Для других типов используем старую логику
                    detected_type = tender.get('tender_type')
                    if detected_type and detected_type != tender_type:
                        filtered_count += 1
                        print(f"   ⚠️ Отфильтрован: {detected_type} != {tender_type}")
                        continue

                tenders.append(tender)

                # Останавливаемся когда набрали нужное количество
                if len(tenders) >= max_results:
                    break

            print(f"✓ Получено тендеров из RSS: {len(tenders)}")
            if filtered_count > 0:
                print(f"   📊 Отфильтровано по типу: {filtered_count}")
            return tenders

        except Exception as e:
            print(f"✗ Ошибка получения RSS: {e}")
            return []

    def _build_rss_url(
        self,
        keywords: Optional[str],
        price_min: Optional[int],
        price_max: Optional[int],
        regions: Optional[List[str]] = None,
        tender_type: Optional[str] = None,
        law_type: Optional[str] = None,
        purchase_stage: Optional[str] = None,
        purchase_method: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> str:
        """Формирует URL для RSS-фида с параметрами поиска."""
        params = {
            'morphology': 'on',
            'search-filter': 'Дате размещения',
            'sortDirection': 'false',
            'sortBy': 'UPDATE_DATE',
            'currencyIdGeneral': '-1'
        }

        # Фильтр по закону (44-ФЗ / 223-ФЗ)
        if law_type == "44-ФЗ":
            params['fz44'] = 'on'
        elif law_type == "223-ФЗ":
            params['fz223'] = 'on'
        else:
            # По умолчанию оба закона
            params['fz44'] = 'on'
            params['fz223'] = 'on'

        # Фильтр по этапу закупки
        if purchase_stage == "submission":
            # Только подача заявок (активные)
            params['af'] = 'on'
            params['ca'] = 'on'  # Подача заявок
        else:
            # Все этапы
            params['af'] = 'on'

        # Фильтр по способу закупки
        if purchase_method:
            method_codes = {
                "auction": "EA44",  # Электронный аукцион
                "tender": "OK44",   # Открытый конкурс
                "quotation": "ZK44",  # Запрос котировок
                "request": "ZP44",  # Запрос предложений
            }
            if purchase_method in method_codes:
                params['placingWayList'] = method_codes[purchase_method]

        # Фильтр по дате публикации
        if date_from:
            params['publishDateFrom'] = date_from
        if date_to:
            params['publishDateTo'] = date_to

        # Ключевые слова
        if keywords:
            params['searchString'] = keywords

        # Фильтр по регионам (через API)
        if regions:
            region_codes = []
            for region in regions:
                code = self.REGION_CODES.get(region)
                if code:
                    region_codes.append(code)
                else:
                    # Пробуем найти частичное совпадение
                    for name, code in self.REGION_CODES.items():
                        if region.lower() in name.lower() or name.lower() in region.lower():
                            region_codes.append(code)
                            break

            if region_codes:
                # zakupki.gov.ru принимает множественные регионы
                params['selectedSubjectsIdNameHidden'] = ','.join(region_codes)
                print(f"   📍 Фильтр по регионам: {', '.join(regions)} (коды: {', '.join(region_codes)})")

        # Ценовой диапазон
        if price_min:
            params['priceFromGeneral'] = str(price_min)
        if price_max:
            params['priceToGeneral'] = str(price_max)

        # Тип закупки через purchaseObjectTypeCode
        # ВАЖНО: Фильтр по типу ОТКЛЮЧЕН для товаров из-за проблем классификации на zakupki.gov.ru
        # Многие товары неправильно помечены как услуги или работы
        if tender_type:
            if tender_type.lower() == "товары":
                # НЕ применяем фильтр для товаров - будем фильтровать на клиенте
                print(f"   ⚠️  Фильтр по типу ОТКЛЮЧЕН для '{tender_type}'")
                print(f"      (zakupki.gov.ru часто неправильно классифицирует товары)")
                print(f"      Будет применена клиентская фильтрация после получения результатов")
            else:
                # Для услуг и работ фильтр работает нормально
                type_code_map = {
                    "работы": "2",      # Выполнение работ
                    "услуги": "3"       # Оказание услуг
                }
                type_code = type_code_map.get(tender_type.lower())
                if type_code:
                    params['purchaseObjectTypeCode'] = type_code
                    print(f"   ✅ Применен фильтр: purchaseObjectTypeCode={type_code} ({tender_type})")

        # Формируем query string с правильным кодированием
        query_string = urlencode(params, quote_via=quote_plus)
        return f"{self.RSS_BASE}?{query_string}"

    def _parse_rss_entry(self, entry) -> Optional[Dict[str, Any]]:
        """Парсит одну запись из RSS-фида."""
        try:
            summary = entry.get('summary', '')

            # Получаем URL и делаем его абсолютным
            url = entry.get('link', '')
            if url and not url.startswith('http'):
                url = f"{self.BASE_URL}{url}"

            tender = {
                'name': entry.get('title', ''),
                'url': url,
                'published': entry.get('published', ''),
                'summary': summary,
            }

            # Извлекаем номер из URL или заголовка
            tender['number'] = self._extract_number(entry.get('link', ''))

            # Извлекаем объект закупки из summary (приоритет)
            purchase_object = self._extract_purchase_object(summary)
            if purchase_object:
                tender['name'] = purchase_object

            # Извлекаем тип закупки из summary для client-side фильтрации
            tender_type = self._extract_tender_type(summary)
            if tender_type:
                tender['tender_type'] = tender_type

            # Извлекаем цену из описания (если есть)
            price = self._extract_price_from_summary(summary)
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

    def _extract_purchase_object(self, summary: str) -> Optional[str]:
        """Извлекает объект закупки из summary."""
        # Ищем "Наименование объекта закупки:" в HTML
        match = re.search(r'<strong>Наименование объекта закупки:\s*</strong>([^<]+)', summary)
        if match:
            purchase_object = match.group(1).strip()
            # Убираем лишние пробелы
            purchase_object = re.sub(r'\s+', ' ', purchase_object)
            return purchase_object
        return None

    def _extract_tender_type(self, summary: str) -> Optional[str]:
        """
        Извлекает тип закупки из summary RSS.
        Возвращает: 'товары', 'работы', 'услуги' или None
        """
        # Ищем различные варианты указания типа в summary
        patterns = [
            r'<strong>Размещение заказа:\s*</strong>([^<]+)',
            r'Поставка товаров',
            r'Выполнение работ',
            r'Оказание услуг',
        ]

        summary_lower = summary.lower()

        # Проверяем явные указания типа
        if 'поставка товар' in summary_lower or 'поставк[ауеи] товар' in summary_lower:
            return 'товары'
        if 'выполнение работ' in summary_lower or 'выполнени[ея] работ' in summary_lower:
            return 'работы'
        if 'оказание услуг' in summary_lower or 'оказани[ея] услуг' in summary_lower:
            return 'услуги'

        return None

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
