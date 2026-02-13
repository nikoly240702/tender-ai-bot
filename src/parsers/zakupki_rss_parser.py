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
import logging
import warnings
import os
import html
import time
from threading import Lock
from bs4 import BeautifulSoup

_log = logging.getLogger(__name__)

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

        # Rate limiting: минимальная задержка между запросами к zakupki.gov.ru
        self.min_request_interval = 2.0  # 2 секунды между запросами
        self.last_request_time = 0
        self.rate_limit_lock = Lock()

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

    def _wait_for_rate_limit(self):
        """
        Ожидание перед запросом для соблюдения rate limit.
        Гарантирует минимальную задержку между запросами к zakupki.gov.ru.
        """
        with self.rate_limit_lock:
            current_time = time.time()
            time_since_last_request = current_time - self.last_request_time

            if time_since_last_request < self.min_request_interval:
                sleep_time = self.min_request_interval - time_since_last_request
                _log.debug(f"   ⏱️  Rate limit: ожидание {sleep_time:.1f}с...")
                time.sleep(sleep_time)

            self.last_request_time = time.time()

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
        _log.debug(f"📡 Получение RSS-фида от zakupki.gov.ru...")
        if tender_type:
            _log.debug(f"   🎯 Фильтр по типу: {tender_type}")

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

            _log.debug(f"   RSS URL: {rss_url[:100]}...")

            # Получаем RSS через requests (обходим SSL проблему)
            try:
                # Соблюдаем rate limit
                self._wait_for_rate_limit()

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
                    # Фильтрация для товаров - исключаем явные услуги и работы
                    name_lower = tender.get('name', '').lower()

                    # Если название начинается с индикатора товаров - НЕ фильтруем
                    # (даже если в описании есть "ремонт", "монтаж" и т.д.)
                    goods_start_indicators = [
                        'поставка', 'закупка', 'приобретение', 'купля',
                        'покупка', 'снабжение'
                    ]
                    is_goods_by_name = any(name_lower.startswith(ind) for ind in goods_start_indicators)

                    if not is_goods_by_name:
                        # Проверяем только НАЗВАНИЕ на индикаторы услуг/работ
                        # (не проверяем summary - слишком много ложных срабатываний)
                        service_work_indicators = [
                            'оказание услуг', 'выполнение работ', 'проведение работ',
                            'оказание услуги', 'выполнение услуг',
                            'услуги по', 'работы по',
                            'медицинские услуги', 'медицинская помощь',
                            'консультирование', 'проектирование',
                            'техническое обслуживание', 'техобслуживание',
                            'сервисное обслуживание',
                        ]

                        is_service_or_work = False
                        for indicator in service_work_indicators:
                            if indicator in name_lower:
                                filtered_count += 1
                                _log.debug(f"   ⛔ Отфильтрован (услуга/работа, найдено '{indicator}'): {tender.get('name', '')[:60]}...")
                                is_service_or_work = True
                                break
                        if is_service_or_work:
                            continue

                elif tender_type == "услуги":
                    # СТРОГАЯ фильтрация для услуг - исключаем товары и работы
                    name_lower = tender.get('name', '').lower()
                    summary_lower = tender.get('summary', '').lower()
                    full_text = name_lower + ' ' + summary_lower

                    # Индикаторы товаров - если они есть явно, это НЕ услуги
                    goods_indicators = [
                        'поставка товар', 'закупка товар', 'приобретение товар',
                        'поставка оборудования', 'закупка оборудования',
                        'поставка материал', 'закупка материал'
                    ]
                    # Индикаторы работ
                    work_indicators = [
                        'выполнение работ', 'строительные работы', 'ремонт',
                        'строительство', 'реконструкция'
                    ]

                    is_goods_or_work = False
                    for indicator in goods_indicators + work_indicators:
                        if indicator in full_text:
                            filtered_count += 1
                            _log.debug(f"   ⛔ Отфильтрован (не услуга, найдено '{indicator}'): {tender.get('name', '')[:60]}...")
                            is_goods_or_work = True
                            break
                    if is_goods_or_work:
                        continue

                elif tender_type == "работы":
                    # СТРОГАЯ фильтрация для работ - исключаем товары и услуги
                    name_lower = tender.get('name', '').lower()
                    summary_lower = tender.get('summary', '').lower()
                    full_text = name_lower + ' ' + summary_lower

                    # Индикаторы товаров
                    goods_indicators = [
                        'поставка товар', 'закупка товар', 'приобретение товар',
                        'поставка оборудования', 'закупка оборудования'
                    ]
                    # Индикаторы услуг
                    service_indicators = [
                        'оказание услуг', 'медицинские услуги', 'консультирование',
                        'услуги по', 'сопровождение'
                    ]

                    is_goods_or_service = False
                    for indicator in goods_indicators + service_indicators:
                        if indicator in full_text:
                            filtered_count += 1
                            _log.debug(f"   ⛔ Отфильтрован (не работа, найдено '{indicator}'): {tender.get('name', '')[:60]}...")
                            is_goods_or_service = True
                            break
                    if is_goods_or_service:
                        continue

                tenders.append(tender)

                # Останавливаемся когда набрали нужное количество
                if len(tenders) >= max_results:
                    break

            _log.debug(f"✓ Получено тендеров из RSS: {len(tenders)}")
            if filtered_count > 0:
                _log.debug(f"   📊 Отфильтровано по типу: {filtered_count}")
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
        elif purchase_stage == "archive":
            # Завершённые закупки (архив)
            params['af'] = 'on'
            params['pc'] = 'on'  # Завершённые
            params['fz44Completed'] = 'on'  # Завершённые 44-ФЗ
            params['fz223Completed'] = 'on'  # Завершённые 223-ФЗ
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
                _log.debug(f"   📍 Фильтр по регионам: {', '.join(regions)} (коды: {', '.join(region_codes)})")

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
                _log.debug(f"   ⚠️  Фильтр по типу ОТКЛЮЧЕН для '{tender_type}'")
                _log.debug(f"      (zakupki.gov.ru часто неправильно классифицирует товары)")
                _log.debug(f"      Будет применена клиентская фильтрация после получения результатов")
            else:
                # Для услуг и работ фильтр работает нормально
                type_code_map = {
                    "работы": "2",      # Выполнение работ
                    "услуги": "3"       # Оказание услуг
                }
                type_code = type_code_map.get(tender_type.lower())
                if type_code:
                    params['purchaseObjectTypeCode'] = type_code
                    _log.debug(f"   ✅ Применен фильтр: purchaseObjectTypeCode={type_code} ({tender_type})")

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

            # Декодируем HTML entities в названии (например, &laquo; → «)
            title = html.unescape(entry.get('title', ''))

            tender = {
                'name': title,
                'url': url,
                'published': entry.get('published', ''),
                'summary': summary,
            }

            # Извлекаем номер из URL или заголовка
            tender['number'] = self._extract_number(entry.get('link', ''))

            # Извлекаем объект закупки из summary (приоритет)
            purchase_object = self._extract_purchase_object(summary)
            if purchase_object:
                # Декодируем HTML entities в объекте закупки
                tender['name'] = html.unescape(purchase_object)

            # Извлекаем тип закупки из summary для client-side фильтрации
            tender_type = self._extract_tender_type(summary)
            if tender_type:
                tender['tender_type'] = tender_type

            # Извлекаем цену из описания (если есть)
            price = self._extract_price_from_summary(summary)
            if price:
                tender['price'] = price
                tender['price_formatted'] = f"{price:,.2f} ₽".replace(',', ' ')

            # Извлекаем заказчика
            customer = self._extract_customer_from_summary(summary)
            if customer:
                tender['customer'] = customer
                # Извлекаем регион из названия заказчика
                region = self._extract_region_from_customer(customer)
                if region:
                    tender['customer_region'] = region

            # Извлекаем дату окончания подачи заявок
            deadline = self._extract_deadline_from_summary(summary)
            if deadline:
                tender['submission_deadline'] = deadline

            # Парсим дату публикации в удобный формат
            if entry.get('published_parsed'):
                tender['published_datetime'] = datetime(*entry.published_parsed[:6])
                # Форматируем дату в русский формат
                tender['published_formatted'] = tender['published_datetime'].strftime('%d.%m.%Y %H:%M')

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
        """
        Извлекает объект закупки из RSS summary.
        Пробует несколько паттернов для разных форматов RSS.
        """
        # Бюрократические фразы которые нужно отфильтровать
        bureaucratic_phrases = [
            'в соответствии с',
            'статьи 93',
            'закона № 44',
            'закона №44',
            'осуществляемая в соответствии',
            'частью 12'
        ]

        def is_valid(text: str) -> bool:
            """Проверяет что текст не бюрократический."""
            if not text or len(text) < 10:
                return False
            text_lower = text.lower()
            return not any(phrase in text_lower for phrase in bureaucratic_phrases)

        # Паттерны для извлечения объекта закупки из RSS summary
        patterns = [
            # Основной паттерн
            r'<strong>Наименование объекта закупки:\s*</strong>([^<]+)',
            # Альтернативный с двоеточием
            r'Наименование объекта закупки:\s*</strong>([^<]+)',
            # Объект закупки
            r'<strong>Объект закупки:\s*</strong>([^<]+)',
            r'Объект закупки:\s*</strong>([^<]+)',
            # Предмет контракта/закупки
            r'<strong>Предмет (?:контракта|закупки):\s*</strong>([^<]+)',
            # Краткое описание
            r'<strong>Краткое описание:\s*</strong>([^<]+)',
            # Наименование товара
            r'<strong>Наименование товара[^:]*:\s*</strong>([^<]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, summary, re.IGNORECASE)
            if match:
                purchase_object = match.group(1).strip()
                # Убираем лишние пробелы и HTML entities
                purchase_object = re.sub(r'\s+', ' ', purchase_object)
                purchase_object = html.unescape(purchase_object)

                if is_valid(purchase_object):
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
        """Извлекает НМЦК (начальную максимальную цену контракта) из описания RSS."""
        # Ищем паттерны цен в тексте - от более точных к менее точным
        patterns = [
            # Паттерн из HTML RSS: "Начальная (максимальная) цена контракта:</strong> 1 234 567,89"
            r'Начальная.*?цена.*?контракта[:\s]*</strong>\s*([0-9\s,.]+)',
            # Простой НМЦК
            r'НМЦК[:\s]+([0-9\s,\.]+)',
            # Начальная цена
            r'Начальная.*?цена[:\s]+([0-9\s,\.]+)',
            # Максимальная цена
            r'Максимальная.*?цена[:\s]+([0-9\s,\.]+)',
            # Цена контракта
            r'цена контракта[:\s]+([0-9\s,\.]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, summary, re.IGNORECASE | re.DOTALL)
            if match:
                price_text = match.group(1).strip()
                try:
                    # Убираем пробелы, заменяем запятую на точку
                    cleaned = re.sub(r'[^\d,.]', '', price_text)
                    cleaned = cleaned.replace(',', '.')
                    price = float(cleaned)
                    # Проверяем что это реальная цена (более 100 руб)
                    if price > 100:
                        return price
                except:
                    continue

        return None

    def _extract_customer_from_summary(self, summary: str) -> Optional[str]:
        """Извлекает заказчика из описания RSS."""
        patterns = [
            r'<strong>Наименование Заказчика:\s*</strong>([^<]+)',
            r'<strong>Заказчик:\s*</strong>([^<]+)',
            r'Заказчик:\s*([^<\n]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, summary, re.IGNORECASE)
            if match:
                customer = match.group(1).strip()
                # Убираем лишние пробелы
                customer = re.sub(r'\s+', ' ', customer)
                return customer

        return None

    def _extract_deadline_from_summary(self, summary: str) -> Optional[str]:
        """Извлекает дату окончания подачи заявок из описания RSS."""
        patterns = [
            # Паттерны с тегами
            r'(?:Окончание подачи заявок|Дата окончания подачи заявок|Срок подачи заявок)[:\s]*</strong>\s*([0-9.]+(?:\s+[0-9:]+)?)',
            r'(?:Окончание подачи заявок|Дата окончания)[:\s]*</strong>\s*([0-9.]+(?:\s+[0-9:]+)?)',
            # Паттерны без тегов
            r'(?:Окончание подачи заявок|Дата окончания подачи заявок|Срок подачи заявок)[:\s]+([0-9.]+(?:\s+[0-9:]+)?)',
            r'(?:Окончание подачи заявок|Дата окончания)[:\s]+([0-9.]+(?:\s+[0-9:]+)?)',
            # Паттерн "до"
            r'до\s+([0-9.]+\s+[0-9:]+)',
            # Полное описание
            r'Дата и время окончания.*?([0-9]{2}\.[0-9]{2}\.[0-9]{4}(?:\s+[0-9:]+)?)',
            # Общий паттерн для любой даты после "окончани"
            r'окончани[ея]\s+[^0-9]*([0-9]{2}\.[0-9]{2}\.[0-9]{4}(?:\s+[0-9]{2}:[0-9]{2})?)',
        ]

        for pattern in patterns:
            match = re.search(pattern, summary, re.IGNORECASE | re.DOTALL)
            if match:
                deadline = match.group(1).strip()
                # Валидация формата даты
                if re.match(r'\d{2}\.\d{2}\.\d{4}', deadline):
                    return deadline

        return None

    def _extract_region_from_customer(self, customer: str) -> Optional[str]:
        """Извлекает регион из названия заказчика."""
        # Список регионов России для поиска
        regions = [
            'Москва', 'Московская область', 'Санкт-Петербург', 'Ленинградская область',
            'Республика Татарстан', 'Татарстан', 'Краснодарский край', 'Свердловская область',
            'Новосибирская область', 'Ростовская область', 'Нижегородская область',
            'Челябинская область', 'Самарская область', 'Республика Башкортостан', 'Башкортостан',
            'Красноярский край', 'Пермский край', 'Воронежская область', 'Волгоградская область',
            'Саратовская область', 'Тюменская область', 'Омская область', 'Кемеровская область',
            'Оренбургская область', 'Иркутская область', 'Алтайский край', 'Приморский край',
            'Ставропольский край', 'Белгородская область', 'Тульская область', 'Калужская область',
            'Ярославская область', 'Владимирская область', 'Рязанская область', 'Тверская область',
            'Брянская область', 'Курская область', 'Липецкая область', 'Тамбовская область',
            'Ханты-Мансийский', 'ХМАО', 'Ямало-Ненецкий', 'ЯНАО',
            'Республика Крым', 'Крым', 'Севастополь',
            'Республика Дагестан', 'Дагестан', 'Чеченская Республика', 'Чечня',
            'Хабаровский край', 'Сахалинская область', 'Камчатский край',
            'Мурманская область', 'Архангельская область', 'Вологодская область',
            'Калининградская область', 'Псковская область', 'Новгородская область',
        ]

        customer_upper = customer.upper()

        for region in regions:
            if region.upper() in customer_upper:
                return region

        # Ищем паттерн "г. Город" или "город Город"
        city_match = re.search(r'(?:г\.|город)\s*([А-Яа-яЁё]+)', customer)
        if city_match:
            return f"г. {city_match.group(1)}"

        return None

    def enrich_tender_from_page(self, tender: Dict[str, Any]) -> Dict[str, Any]:
        """
        Обогащает данные тендера, загружая полную страницу с zakupki.gov.ru.
        Извлекает: НМЦК, дату окончания подачи заявок, адрес заказчика.

        Args:
            tender: Базовые данные тендера из RSS

        Returns:
            Обогащенный тендер с дополнительными полями
        """
        url = tender.get('url', '')
        if not url:
            print(f"   ⚠️ Обогащение: URL отсутствует, пропускаем")
            return tender

        _log.debug(f"   🌐 Загружаем страницу тендера: {url[:80]}...")

        try:
            # Соблюдаем rate limit
            self._wait_for_rate_limit()

            # Используем self.session (уже настроена с прокси)
            response = self.session.get(url, timeout=30, verify=False)  # Увеличили таймаут до 30с
            response.raise_for_status()

            html_content = response.text

            # === Извлекаем НМЦК ===
            if not tender.get('price'):
                price = self._extract_price_from_page(html_content)
                if price:
                    tender['price'] = price
                    tender['price_formatted'] = f"{price:,.2f} ₽".replace(',', ' ')

            # === Извлекаем дату окончания подачи заявок ===
            if not tender.get('submission_deadline'):
                deadline = self._extract_deadline_from_page(html_content)
                if deadline:
                    tender['submission_deadline'] = deadline

            # === Извлекаем адрес и регион заказчика ===
            address_info = self._extract_address_from_page(html_content)
            if address_info:
                tender['customer_address'] = address_info.get('full_address', '')
                # Регион из адреса — только если ещё не определён из названия заказчика
                new_region = address_info.get('region', '')
                if new_region and not tender.get('customer_region'):
                    tender['customer_region'] = new_region
                new_city = address_info.get('city', '')
                if new_city:
                    tender['customer_city'] = new_city

            # === Извлекаем название заказчика если нет ===
            if not tender.get('customer'):
                customer = self._extract_customer_from_page(html_content)
                if customer:
                    tender['customer'] = customer

            # === Извлекаем объект закупки со страницы если в name бюрократия ===
            current_name = tender.get('name', '')
            _log.debug(f"   📋 Проверка названия тендера: {current_name[:80]}...")

            # Проверяем признаки бюрократического названия
            bureaucratic_indicators = [
                'закупка, осуществляемая в соответствии',
                'в соответствии с частью',
                'статьи 93',
                'закона № 44-фз',
                'закона №44-фз'
            ]
            is_bureaucratic = any(indicator in current_name.lower() for indicator in bureaucratic_indicators)

            if is_bureaucratic:
                _log.debug(f"   ⚠️ Обнаружено бюрократическое название, попытка заменить...")
                purchase_object = self._extract_purchase_object_from_page(html_content)

                # Если не нашли на common-info, пробуем вкладку purchase-objects
                if not purchase_object or len(purchase_object) <= 10:
                    purchase_objects_url = url.replace('common-info.html', 'purchase-objects.html')
                    if purchase_objects_url != url:
                        _log.debug(f"   🔄 Пробуем вкладку purchase-objects...")
                        try:
                            self._wait_for_rate_limit()
                            po_response = self.session.get(purchase_objects_url, timeout=30, verify=False)
                            if po_response.status_code == 200:
                                purchase_object = self._extract_purchase_object_from_page(po_response.text)
                        except Exception as e:
                            _log.debug(f"   ⚠️ Ошибка загрузки purchase-objects: {e}")

                if purchase_object and len(purchase_object) > 10:
                    old_name = tender['name']
                    tender['name'] = purchase_object
                    _log.debug(f"   ✅ Заменено название:")
                    _log.debug(f"      Было: {old_name[:80]}...")
                    _log.debug(f"      Стало: {purchase_object[:80]}...")
                else:
                    _log.debug(f"   ⚠️ Объект закупки не извлечен, оставляем исходное название")
            elif len(current_name) < 20:
                _log.debug(f"   ⚠️ Название слишком короткое ({len(current_name)} символов), попытка заменить...")
                purchase_object = self._extract_purchase_object_from_page(html_content)

                # Если не нашли на common-info, пробуем вкладку purchase-objects
                if not purchase_object or len(purchase_object) <= 10:
                    purchase_objects_url = url.replace('common-info.html', 'purchase-objects.html')
                    if purchase_objects_url != url:
                        _log.debug(f"   🔄 Пробуем вкладку purchase-objects...")
                        try:
                            self._wait_for_rate_limit()
                            po_response = self.session.get(purchase_objects_url, timeout=30, verify=False)
                            if po_response.status_code == 200:
                                purchase_object = self._extract_purchase_object_from_page(po_response.text)
                        except Exception as e:
                            _log.debug(f"   ⚠️ Ошибка загрузки purchase-objects: {e}")

                if purchase_object and len(purchase_object) > 10:
                    tender['name'] = purchase_object
                    _log.debug(f"   ✅ Заменено короткое название на: {purchase_object[:60]}...")
                else:
                    _log.debug(f"   ⚠️ Объект закупки не извлечен, оставляем исходное название")
            else:
                _log.debug(f"   ✓ Название в порядке, замена не требуется")

            # Логируем что было извлечено
            _log.debug(f"   ✅ Обогащено: цена={tender.get('price', 'Н/Д')}, дедлайн={tender.get('submission_deadline', 'Н/Д')}, регион={tender.get('customer_region', 'Н/Д')}")

        except requests.exceptions.Timeout:
            print(f"   ⏱️ Таймаут при загрузке страницы тендера")
        except requests.exceptions.RequestException as e:
            print(f"   ⚠️ Ошибка загрузки страницы: {e}")
        except Exception as e:
            print(f"   ⚠️ Ошибка обогащения тендера: {e}")

        return tender

    def _extract_price_from_page(self, html: str) -> Optional[float]:
        """Извлекает НМЦК из HTML страницы тендера."""
        patterns = [
            # Реальный паттерн zakupki.gov.ru: section__title + section__info
            r'Максимальное значение цены контракта\s*</span>\s*<span[^>]*class="section__info"[^>]*>\s*([0-9\s,\.]+)',
            # Паттерн из хедера карточки (cardMainInfo)
            r'Начальная цена.*?cardMainInfo__content[^>]*>\s*([0-9\s,\.]+)',
            r'cardMainInfo__title[^>]*>\s*Начальная цена.*?cardMainInfo__content[^>]*>\s*([0-9\s,\.]+)',
            # Альтернативный паттерн
            r'Начальная \(максимальная\) цена контракта.*?section__info[^>]*>\s*([0-9\s,\.]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                price_text = match.group(1).strip()
                try:
                    cleaned = re.sub(r'[^\d,.]', '', price_text)
                    cleaned = cleaned.replace(',', '.')
                    price = float(cleaned)
                    if price > 100:  # Минимальная валидация
                        return price
                except:
                    continue

        return None

    def _extract_deadline_from_page(self, html: str) -> Optional[str]:
        """Извлекает дату окончания подачи заявок из HTML страницы."""
        # Сначала пробуем найти с временем (полный формат)
        patterns_with_time = [
            # Реальный паттерн zakupki.gov.ru: section__title + section__info
            r'Дата и время окончания срока подачи заявок\s*</span>\s*<span[^>]*class="section__info"[^>]*>\s*(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})',
            # Альтернативные паттерны с временем
            r'окончания срока подачи заявок.*?(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})',
            r'Окончание срока подачи заявок[:\s]*(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})',
            r'Прием заявок до[:\s]*(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})',
        ]

        for pattern in patterns_with_time:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                return f"{match.group(1)} {match.group(2)}"

        # Если не нашли с временем - ищем только дату
        patterns_date_only = [
            # Паттерн из хедера карточки (cardMainInfo) - там только дата без времени
            r'Окончание подачи заявок\s*</span>\s*<span[^>]*cardMainInfo__content[^>]*>\s*(\d{2}\.\d{2}\.\d{4})',
            r'cardMainInfo__title[^>]*>\s*Окончание подачи заявок\s*</span>\s*<span[^>]*>\s*(\d{2}\.\d{2}\.\d{4})',
            # Более гибкие паттерны
            r'Окончание подачи заявок[:\s]*</span>\s*<span[^>]*>\s*(\d{2}\.\d{2}\.\d{4})',
            r'(?:Срок|Дата).*?(?:окончания|подачи).*?заявок[^0-9]*(\d{2}\.\d{2}\.\d{4})',
        ]

        for pattern in patterns_date_only:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1)

        # Широкий fallback: любая дата рядом со словами "подач" или "окончан"
        fallback_patterns = [
            r'(?:подач[иа]\s+заявок|окончани[ея])[^0-9]{0,40}(\d{2}\.\d{2}\.\d{4})',
            r'(\d{2}\.\d{2}\.\d{4})[^0-9]{0,40}(?:подач[иа]\s+заявок|окончани[ея])',
        ]
        for pattern in fallback_patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1)

        return None

    def _extract_address_from_page(self, html: str) -> Optional[Dict[str, str]]:
        """
        Извлекает почтовый адрес заказчика и парсит его на город и регион.

        Пример входа: "670000, Респ Бурятия, г Улан-Удэ, ул Ленина, дом 30"
        Пример выхода: {"city": "г. Улан-Удэ", "region": "Республика Бурятия", "full_address": "..."}
        """
        # Ищем блок с почтовым адресом - реальный паттерн zakupki.gov.ru
        patterns = [
            # section__title + section__info (реальная структура)
            r'Почтовый адрес\s*</span>\s*<span[^>]*class="section__info"[^>]*>\s*([^<]+)',
            r'section__title[^>]*>Почтовый адрес</span>\s*<span[^>]*section__info[^>]*>\s*([^<]+)',
            # Место нахождения как альтернатива
            r'Место нахождения\s*</span>\s*<span[^>]*class="section__info"[^>]*>\s*([^<]+)',
            r'section__title[^>]*>Место нахождения</span>\s*<span[^>]*section__info[^>]*>\s*([^<]+)',
        ]

        address = None
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                address = match.group(1).strip()
                # Очищаем от HTML-сущностей
                address = re.sub(r'&[a-z]+;', ' ', address)
                address = re.sub(r'\s+', ' ', address).strip()
                if len(address) > 10:  # Минимальная валидация
                    break

        if not address:
            return None

        # Парсим адрес
        result = {
            'full_address': address,
            'city': '',
            'region': ''
        }

        # Словарь сокращений регионов
        region_expansions = {
            'респ': 'Республика',
            'обл': 'область',
            'край': 'край',
            'г.ф.з.': '',  # город федерального значения
            'ао': 'АО',
            'аобл': 'автономная область',
        }

        # Разбиваем адрес на части
        parts = [p.strip() for p in address.split(',')]

        city = ''
        region = ''

        for part in parts:
            part_lower = part.lower()

            # Ищем город (форматы: "г Улан-Удэ", "Улан-Удэ г", "город Улан-Удэ", "г. Улан-Удэ")
            if ' г' in part_lower or part_lower.startswith('г ') or part_lower.startswith('г.') or part_lower.endswith(' г') or 'город' in part_lower:
                # Паттерн 1: город в конце (напр: "Прохладный г")
                city_match = re.search(r'([А-Яа-яЁё\-]+)\s*г(?:ород)?\.?$', part, re.IGNORECASE)
                if city_match:
                    city = f"г. {city_match.group(1).strip()}"
                else:
                    # Паттерн 2: город в начале (напр: "г Улан-Удэ", "г. Москва")
                    city_match = re.search(r'^г\.?\s*([А-Яа-яЁё\-]+)', part, re.IGNORECASE)
                    if city_match:
                        city = f"г. {city_match.group(1).strip()}"
                    else:
                        # Паттерн 3: "город Название"
                        city_match = re.search(r'город\s+([А-Яа-яЁё\-]+)', part, re.IGNORECASE)
                        if city_match:
                            city = f"г. {city_match.group(1).strip()}"

            # Ищем регион (республика, область, край)
            if any(word in part_lower for word in ['респ', 'область', 'обл', 'край', 'округ']):
                # Нормализуем название региона
                region_part = part.strip()

                # Расширяем сокращения
                for abbr, full in region_expansions.items():
                    if abbr in region_part.lower():
                        region_part = re.sub(rf'\b{abbr}\.?\b', full, region_part, flags=re.IGNORECASE)

                # Убираем лишние пробелы
                region_part = re.sub(r'\s+', ' ', region_part).strip()

                # Форматируем: если начинается со слова типа "Московская", оставляем как есть
                # Если начинается с "Республика", оставляем как есть
                region = region_part

            # Москва и Санкт-Петербург - особые случаи
            # НЕ перезаписываем если регион уже найден (защита от "ул Петербургская" и т.п.)
            if 'москва' in part_lower and not region:
                city = 'г. Москва'
                region = 'Москва'
            elif not region and ('санкт-петербург' in part_lower
                                 or re.match(r'^(?:г\.?\s*)?петербург$', part_lower.strip())
                                 or part_lower.strip() in ('спб', 'с-петербург', 'с.петербург')):
                # Матчим только если это именно город, а не улица Петербургская и т.п.
                city = 'г. Санкт-Петербург'
                region = 'Санкт-Петербург'
            elif 'севастополь' in part_lower and not region:
                city = 'г. Севастополь'
                region = 'Севастополь'

        result['city'] = city
        result['region'] = region

        # Формируем красивый вывод: "г. Прохладный, Кабардино-Балкарская Республика"
        if city and region and city.replace('г. ', '') not in region:
            result['location'] = f"{city}, {region}"
        elif city:
            result['location'] = city
        elif region:
            result['location'] = region

        return result

    def _extract_customer_from_page(self, html: str) -> Optional[str]:
        """Извлекает название заказчика/организации из HTML страницы."""
        patterns = [
            # Организация, осуществляющая размещение (из хедера cardMainInfo)
            r'Организация,\s*осуществляющая\s*размещение.*?cardMainInfo__content[^>]*>\s*(?:<a[^>]*>)?([^<]+)',
            # section__info вариант
            r'Организация,\s*осуществляющая\s*размещение\s*</span>\s*<span[^>]*class="section__info"[^>]*>\s*([^<]+)',
            # Прямой заказчик
            r'Наименование.*?заказчика.*?section__info[^>]*>\s*([^<]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
            if match:
                customer = match.group(1).strip()
                customer = re.sub(r'\s+', ' ', customer)
                # Валидация: минимум 10 символов, не цифры
                if len(customer) > 10 and not customer.replace(' ', '').replace(',', '').replace('.', '').isdigit():
                    return customer

        return None

    def _extract_purchase_object_from_page(self, html_content: str) -> Optional[str]:
        """
        Извлекает описание объекта закупки из раздела "Информация об объекте закупки" на странице.

        Использует несколько методов:
        1. Regex паттерны для быстрого извлечения из известных мест
        2. BeautifulSoup парсинг для более сложных случаев (таблица позиций)

        Returns:
            Описание объекта закупки или None если не найдено
        """
        _log.debug(f"   🔍 Попытка извлечь объект закупки из страницы...")

        # Бюрократические фразы которые нужно отфильтровать
        bureaucratic_phrases = [
            'в соответствии с',
            'статьи 93',
            'закона № 44',
            'закона №44',
            'осуществляемая в соответствии',
            'частью 12'
        ]

        def is_valid_purchase_object(text: str) -> bool:
            """Проверяет что текст является валидным объектом закупки."""
            if not text or len(text) < 10:
                return False
            text_lower = text.lower()
            return not any(phrase in text_lower for phrase in bureaucratic_phrases)

        def clean_text(text: str) -> str:
            """Очищает текст от лишних пробелов и HTML entities."""
            text = re.sub(r'\s+', ' ', text).strip()
            text = html.unescape(text)
            return text

        # === МЕТОД 1: Regex паттерны ===
        patterns = [
            # Наименование объекта закупки в section__info (с пробелами и переносами)
            r'Наименование объекта закупки\s*</span>\s*<span[^>]*class="section__info"[^>]*>\s*([^<]+)',
            # Объект закупки в section__info
            r'Объект закупки\s*</span>\s*<span[^>]*class="section__info"[^>]*>\s*([^<]+)',
            # cardMainInfo - title + content
            r'<span[^>]*class="cardMainInfo__title"[^>]*>\s*Объект закупки\s*</span>\s*<span[^>]*class="cardMainInfo__content"[^>]*>\s*([^<]+)',
            # Более гибкий паттерн для cardMainInfo
            r'cardMainInfo__title[^>]*>\s*Объект закупки\s*</span>\s*<span[^>]*cardMainInfo__content[^>]*>\s*([^<]+)',
            # В табличной структуре
            r'<td[^>]*>Наименование объекта закупки</td>\s*<td[^>]*>([^<]+)',
            r'<td[^>]*>Объект закупки</td>\s*<td[^>]*>([^<]+)',
            # Общий паттерн
            r'(?:Наименование|Объект)\s+(?:объекта\s+)?закупки[:\s]*</span>\s*<[^>]*>\s*([^<]+)',
        ]

        for i, pattern in enumerate(patterns, 1):
            match = re.search(pattern, html_content, re.IGNORECASE | re.DOTALL)
            if match:
                purchase_object = clean_text(match.group(1))
                _log.debug(f"      ✓ Regex #{i} нашел: {purchase_object[:80]}...")

                if is_valid_purchase_object(purchase_object):
                    _log.debug(f"      ✅ Объект закупки валиден: {purchase_object[:80]}...")
                    return purchase_object
                else:
                    if len(purchase_object) <= 10:
                        _log.debug(f"      ⚠️ Объект слишком короткий (длина: {len(purchase_object)})")
                    else:
                        _log.debug(f"      ⚠️ Объект содержит бюрократические фразы, пропускаем")

        # === МЕТОД 2: BeautifulSoup парсинг ===
        _log.debug(f"      🔄 Regex не нашел, пробуем BeautifulSoup...")

        try:
            soup = BeautifulSoup(html_content, 'html.parser')

            # 2.1 Ищем в cardMainInfo__section
            for section in soup.find_all(class_='cardMainInfo__section'):
                title = section.find(class_='cardMainInfo__title')
                content = section.find(class_='cardMainInfo__content')
                if title and content:
                    title_text = title.get_text(strip=True).lower()
                    if 'объект закупки' in title_text:
                        purchase_object = clean_text(content.get_text(strip=True))
                        _log.debug(f"      ✓ BS cardMainInfo нашел: {purchase_object[:80]}...")
                        if is_valid_purchase_object(purchase_object):
                            _log.debug(f"      ✅ Объект закупки валиден: {purchase_object[:80]}...")
                            return purchase_object

            # 2.2 Ищем в section__title + section__info
            for title_span in soup.find_all(class_='section__title'):
                title_text = title_span.get_text(strip=True).lower()
                if 'наименование объекта закупки' in title_text or 'объект закупки' in title_text:
                    info_span = title_span.find_next_sibling(class_='section__info')
                    if info_span:
                        purchase_object = clean_text(info_span.get_text(strip=True))
                        _log.debug(f"      ✓ BS section__info нашел: {purchase_object[:80]}...")
                        if is_valid_purchase_object(purchase_object):
                            _log.debug(f"      ✅ Объект закупки валиден: {purchase_object[:80]}...")
                            return purchase_object

            # 2.3 Ищем в таблице позиций закупки (fallback)
            # Находим раздел "Информация об объекте закупки"
            obj_header = soup.find('h2', string=re.compile('Информация об объекте закупки', re.I))
            if obj_header:
                # Ищем таблицу в этом разделе
                parent = obj_header.find_parent('div', class_='col')
                if parent:
                    table = parent.find('table', class_='blockInfo__table')
                    if table:
                        # Ищем строку данных (не заголовок)
                        tbody = table.find('tbody')
                        if tbody:
                            first_row = tbody.find('tr', class_='tableBlock__row')
                            if first_row:
                                # Третья колонка (td) содержит наименование товара
                                tds = first_row.find_all('td', class_='tableBlock__col')
                                if len(tds) >= 3:
                                    # Берём текст из третьей колонки (наименование товара)
                                    # Но только первую строку, без характеристик
                                    product_cell = tds[2]
                                    # Получаем только прямой текст, без вложенных div
                                    product_text = ''
                                    for content in product_cell.children:
                                        if isinstance(content, str):
                                            product_text += content
                                        elif content.name not in ['div', 'span', 'table']:
                                            product_text += content.get_text()
                                        # Прерываем после первого текстового блока (до div с характеристиками)
                                        if content.name == 'div':
                                            break

                                    product_name = clean_text(product_text)
                                    if product_name and len(product_name) > 5:
                                        _log.debug(f"      ✓ BS таблица позиций нашла: {product_name[:80]}...")
                                        if is_valid_purchase_object(product_name):
                                            _log.debug(f"      ✅ Объект закупки из таблицы: {product_name[:80]}...")
                                            return product_name

        except Exception as e:
            _log.debug(f"      ⚠️ Ошибка BeautifulSoup: {e}")

        _log.debug(f"      ❌ Объект закупки не найден ни одним методом")

        # Сохраняем debug HTML для анализа проблемных страниц
        try:
            debug_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "output")
            os.makedirs(debug_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            debug_file = os.path.join(debug_dir, f"debug_purchase_object_not_found_{timestamp}.html")
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write(html_content)
            _log.debug(f"      💾 Debug HTML сохранен: {debug_file}")
        except Exception as e:
            _log.debug(f"      ⚠️ Не удалось сохранить debug HTML: {e}")

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
