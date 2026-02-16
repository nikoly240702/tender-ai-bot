"""
Google Sheets Sync для Tender Sniper.

Автоматически добавляет строки с тендерами в Google-таблицу пользователя.
"""

import os
import json
import asyncio
import functools
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Маппинг колонок: ключ → (заголовок RU, функция извлечения данных)
COLUMN_DEFINITIONS = {
    'request_number': ('№ заявки', lambda t, m: ''),  # Заполняется динамически в _append_row_sync
    'link': ('Ссылка', lambda t, m: t.get('url', '')),
    'name': ('Объект закупки', lambda t, m: t.get('name', '')),
    'customer': ('Заказчик', lambda t, m: t.get('customer_name') or t.get('customer', '')),
    'region': ('Локация', lambda t, m: t.get('region') or t.get('customer_region', '')),
    'deadline': ('Срок подачи', lambda t, m: t.get('submission_deadline', '')),
    'price': ('Начальная цена', lambda t, m: _format_price(t.get('price'))),
    'published': ('Дата публикации', lambda t, m: t.get('published_date') or t.get('published', '')),
    'filter_name': ('Фильтр', lambda t, m: m.get('filter_name', '')),
    'score': ('Score', lambda t, m: str(m.get('score', ''))),
    'red_flags': ('Красные флаги', lambda t, m: '; '.join(m.get('red_flags', []))),
    # AI-поля (Premium)
    'ai_delivery_date': ('Дата поставки', lambda t, m: m.get('ai_data', {}).get('execution_description', '')),
    'ai_quantities': ('Кол-во наименований', lambda t, m: m.get('ai_data', {}).get('quantities', '')),
    'ai_contract_security': ('Обеспечение', lambda t, m: m.get('ai_data', {}).get('contract_security', '')),
    'ai_payment_terms': ('Способ оплаты', lambda t, m: m.get('ai_data', {}).get('payment_terms', '')),
    'ai_summary': ('Комментарий (AI)', lambda t, m: m.get('ai_data', {}).get('summary', '')),
    'ai_licenses': ('Лицензии', lambda t, m: m.get('ai_data', {}).get('licenses', '')),
    'ai_experience': ('Требования к опыту', lambda t, m: m.get('ai_data', {}).get('experience_years', '')),
    'status': ('Статус', lambda t, m: ''),  # Пустая колонка для ручного заполнения
}

# Колонки, требующие AI (Premium)
AI_COLUMNS = {'ai_delivery_date', 'ai_quantities', 'ai_contract_security',
              'ai_payment_terms', 'ai_summary', 'ai_licenses', 'ai_experience'}

# Базовые колонки по умолчанию
DEFAULT_COLUMNS = ['request_number', 'link', 'name', 'customer', 'region', 'deadline', 'price', 'score', 'status']


def get_weekly_sheet_name() -> str:
    """Имя листа на основе текущей недели (Пн-Вс)."""
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return f"{monday.strftime('%d.%m')} — {sunday.strftime('%d.%m')}"


def _normalize_date(value: str) -> str:
    """Нормализует дату в формат ДД.ММ.ГГГГ."""
    import re
    from datetime import datetime, timedelta

    if not value:
        return value

    # Уже в формате ДД.ММ.ГГГГ
    if re.match(r'^\d{2}\.\d{2}\.\d{4}$', value):
        return value

    # Формат ГГГГ-ММ-ДД
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', value)
    if m:
        return f"{m.group(3)}.{m.group(2)}.{m.group(1)}"

    # "XX рабочих дней" / "XX календарных дней" / "XX дней"
    m = re.search(r'(\d+)\s*(?:рабочих|календарных)?\s*дн', value)
    if m:
        days = int(m.group(1))
        # Для рабочих дней умножаем на ~1.4
        if 'рабочих' in value:
            target = datetime.now() + timedelta(days=int(days * 1.4))
        else:
            target = datetime.now() + timedelta(days=days)
        return f"{value} (ориент. {target.strftime('%d.%m.%Y')})"

    return value


def flatten_ai_extraction(extraction: Dict[str, Any]) -> Dict[str, Any]:
    """
    Преобразует AI-извлечение в плоский формат для Google Sheets.

    Поддерживает обе схемы:
    - Новую flat: {execution_deadline: str, items_description: str, ...}
    - Старую nested: {deadlines: {...}, items: [...], requirements: {...}, ...}
    """
    if not extraction or extraction.get('error'):
        return {}

    # Определяем формат: если есть execution_deadline (строка) — новый flat
    is_new_format = isinstance(extraction.get('execution_deadline'), str)

    if is_new_format:
        return _flatten_new_format(extraction)
    else:
        return _flatten_old_format(extraction)


def _flatten_new_format(extraction: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten для нового flat-формата — поля уже плоские, просто маппим."""
    flat = {}

    _not_empty = ('Не указано', 'Не удалось определить', 'Не требуются')

    # Сроки поставки
    val = extraction.get('execution_deadline', '')
    if val and val not in _not_empty:
        flat['execution_description'] = _normalize_date(str(val))

    # Количество наименований
    val = extraction.get('items_count', '')
    if val and val not in _not_empty:
        flat['quantities'] = str(val)

    # Обеспечение — собираем в одну строку
    parts = []
    app_sec = extraction.get('application_security', '')
    con_sec = extraction.get('contract_security', '')
    bg = extraction.get('bank_guarantee_allowed', '')
    if app_sec and app_sec not in _not_empty:
        parts.append(f"Заявка: {app_sec}")
    if con_sec and con_sec not in _not_empty:
        parts.append(f"Контракт: {con_sec}")
    if bg == 'Да':
        parts.append("БГ допускается")
    if parts:
        flat['contract_security'] = '; '.join(parts)

    # Оплата — собираем из advance_percent + payment_deadline
    pay_parts = []
    advance = extraction.get('advance_percent', '')
    if advance and advance not in _not_empty and advance != 'Не предусмотрен':
        pay_parts.append(f"Аванс {advance}")
    pay_dl = extraction.get('payment_deadline', '')
    if pay_dl and pay_dl not in _not_empty:
        pay_parts.append(pay_dl)
    if pay_parts:
        flat['payment_terms'] = ', '.join(pay_parts)

    # Summary — структурированный текст для ячейки
    items_desc = extraction.get('items_description', '')
    summary = extraction.get('summary', '')

    parts = []

    # Позиции — как чистый нумерованный список с переносами строк
    if items_desc and items_desc not in _not_empty:
        # Разбиваем "1. X; 2. Y" на отдельные строки
        import re
        items_lines = re.split(r';\s*(?=\d+\.)', items_desc)
        clean_items = []
        for line in items_lines:
            line = line.strip().rstrip(';').strip()
            if line:
                # Убираем "кол-во не указано" / "(количество не указано)" и подобные фразы
                line = re.sub(r'\s*\(кол-во не указано\)', '', line)
                line = re.sub(r'\s*\(количество не указано\)', '', line)
                line = re.sub(r'\s*— кол-во не указано', '', line)
                line = re.sub(r'\s*- кол-во не указано', '', line)
                line = line.strip()
                if line:
                    clean_items.append(line)
        if clean_items:
            parts.append('\n'.join(clean_items))

    # Краткий комментарий
    if summary and summary not in _not_empty:
        parts.append(summary)

    if parts:
        flat['summary'] = '\n\n'.join(parts)

    # Лицензии
    val = extraction.get('licenses_required', '')
    if val and val not in _not_empty:
        flat['licenses'] = str(val)

    # Опыт
    val = extraction.get('experience_required', '')
    if val and val not in _not_empty:
        flat['experience_years'] = str(val)

    return flat


def _flatten_old_format(extraction: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten для старого nested-формата (обратная совместимость)."""
    flat = {}

    # === Товарные позиции ===
    items = extraction.get('items', [])
    items_count = extraction.get('items_count')

    if items and isinstance(items, list):
        count = items_count or len(items)
        flat['quantities'] = str(count)

        item_parts = []
        for item in items[:5]:
            name = item.get('name', '')
            qty = item.get('quantity', '')
            brand = item.get('brand')
            chars = item.get('characteristics', '')

            desc = name
            if qty:
                desc += f" ({qty})"
            if brand:
                desc += f" [{brand}]"
            if chars:
                chars_str = str(chars)
                if len(chars_str) > 100:
                    chars_str = chars_str[:97] + '...'
                desc += f" — {chars_str}"
            item_parts.append(desc)

        original_summary = str(extraction.get('summary', ''))
        items_text = '; '.join(item_parts)
        if original_summary and items_text:
            flat['summary'] = f"{items_text}. {original_summary}"
        elif items_text:
            flat['summary'] = items_text
        elif original_summary:
            flat['summary'] = original_summary
    else:
        specs = extraction.get('technical_specs', {})
        if isinstance(specs, dict):
            quantities = specs.get('quantities', '')
            if quantities:
                flat['quantities'] = str(quantities)
            if not items_count and specs.get('items_count'):
                items_count = specs['items_count']
                flat['quantities'] = str(items_count)

        summary = extraction.get('summary', '')
        if summary:
            summary = str(summary)
            sentences = summary.replace('! ', '. ').split('. ')
            if len(sentences) > 2:
                summary = '. '.join(sentences[:2]) + '.'
            flat['summary'] = summary

    # === Сроки поставки ===
    deadlines = extraction.get('deadlines', {})
    if isinstance(deadlines, dict):
        desc = deadlines.get('execution_description', '')
        days = deadlines.get('execution_days')
        if desc:
            flat['execution_description'] = _normalize_date(str(desc))
        elif days:
            flat['execution_description'] = _normalize_date(f"{days} дней")

    # === Обеспечение ===
    security = extraction.get('contract_security', {})
    if isinstance(security, dict):
        parts = []
        if security.get('application_security_percent'):
            parts.append(f"Заявка: {security['application_security_percent']}%")
        if security.get('contract_security_percent'):
            parts.append(f"Контракт: {security['contract_security_percent']}%")
        if security.get('warranty_security_percent'):
            parts.append(f"Гарантия: {security['warranty_security_percent']}%")
        if security.get('bank_guarantee_allowed') is True:
            parts.append("БГ допускается")
        if parts:
            flat['contract_security'] = '; '.join(parts)

    # === Оплата ===
    payment = extraction.get('payment_terms', {})
    if isinstance(payment, dict):
        parts = []
        if payment.get('advance_percent'):
            parts.append(f"Аванс {payment['advance_percent']}%")
        if payment.get('payment_deadline_days'):
            parts.append(f"оплата {payment['payment_deadline_days']} дней после приёмки")
        elif payment.get('payment_conditions'):
            cond = str(payment['payment_conditions'])
            if len(cond) > 80:
                cond = cond[:77] + '...'
            parts.append(cond)
        if not parts and payment.get('payment_stages'):
            stages = payment['payment_stages']
            if isinstance(stages, list) and stages:
                parts.append('; '.join(str(s) for s in stages[:3]))
        if parts:
            flat['payment_terms'] = ', '.join(parts)

    # === Лицензии ===
    reqs = extraction.get('requirements', {})
    if isinstance(reqs, dict):
        licenses = reqs.get('licenses', [])
        if isinstance(licenses, list) and licenses:
            specific = [lic for lic in licenses if lic and
                        not any(skip in str(lic).lower() for skip in
                                ['соответствие', 'требования законодательства', 'не требуется', 'нет'])]
            if specific:
                flat['licenses'] = '; '.join(str(lic) for lic in specific)

        exp = reqs.get('experience_years')
        if exp is not None and exp != 'null':
            flat['experience_years'] = f"{exp} лет" if isinstance(exp, (int, float)) else str(exp)

    return flat


def _format_price(price) -> str:
    """Форматирует цену для таблицы."""
    if price is None:
        return ''
    try:
        price_num = float(price)
        if price_num >= 1_000_000:
            return f"{price_num:,.0f} ₽".replace(',', ' ')
        return f"{price_num:,.2f} ₽".replace(',', ' ')
    except (ValueError, TypeError):
        return str(price)


class GoogleSheetsSync:
    """Синхронизация тендеров с Google Sheets."""

    def __init__(self, credentials_json: Optional[str] = None):
        """
        Инициализация.

        Args:
            credentials_json: JSON строка с credentials сервисного аккаунта.
                            Если None, читает из env GOOGLE_SERVICE_ACCOUNT_JSON.
        """
        self._credentials_json = credentials_json or os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON', '')
        self._client = None
        self._verified_sheets: set = set()  # (spreadsheet_id, sheet_name) — уже проверены заголовки

    def _get_client(self):
        """Создаёт или возвращает gspread клиент (синхронный)."""
        if self._client is not None:
            return self._client

        if not self._credentials_json:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON не задан")

        import gspread
        from google.oauth2.service_account import Credentials

        creds_data = json.loads(self._credentials_json)
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive.file',
        ]
        creds = Credentials.from_service_account_info(creds_data, scopes=scopes)
        self._client = gspread.authorize(creds)
        return self._client

    def get_service_email(self) -> str:
        """Возвращает email сервисного аккаунта."""
        if not self._credentials_json:
            return ''
        try:
            creds_data = json.loads(self._credentials_json)
            return creds_data.get('client_email', '')
        except (json.JSONDecodeError, KeyError):
            return ''

    def _open_spreadsheet(self, spreadsheet_id: str):
        """Открывает таблицу по ID (синхронно)."""
        client = self._get_client()
        return client.open_by_key(spreadsheet_id)

    def _get_or_create_sheet(self, spreadsheet, sheet_name: str):
        """Получает или создаёт лист с нужным именем."""
        try:
            return spreadsheet.worksheet(sheet_name)
        except Exception:
            return spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=26)

    def _setup_headers_sync(self, spreadsheet_id: str, columns: List[str], sheet_name: str):
        """Создаёт заголовки на листе (синхронно)."""
        spreadsheet = self._open_spreadsheet(spreadsheet_id)
        worksheet = self._get_or_create_sheet(spreadsheet, sheet_name)

        headers = [COLUMN_DEFINITIONS[col][0] for col in columns if col in COLUMN_DEFINITIONS]
        if headers:
            worksheet.update(range_name='A1', values=[headers])
            # Форматируем заголовки жирным
            worksheet.format('A1:Z1', {
                'textFormat': {'bold': True},
                'backgroundColor': {'red': 0.9, 'green': 0.93, 'blue': 0.98}
            })

    def _ensure_headers_exist(self, worksheet, columns: List[str]):
        """Проверяет наличие заголовков и создаёт/обновляет если нужно."""
        headers = [COLUMN_DEFINITIONS[col][0] for col in columns if col in COLUMN_DEFINITIONS]
        if not headers:
            return

        try:
            existing = worksheet.row_values(1)
            if existing == headers:
                return  # Заголовки совпадают
        except Exception:
            pass

        worksheet.update(range_name='A1', values=[headers])
        worksheet.format('A1:Z1', {
            'textFormat': {'bold': True},
            'backgroundColor': {'red': 0.9, 'green': 0.93, 'blue': 0.98}
        })
        logger.info(f"📊 Google Sheets: заголовки обновлены ({len(headers)} колонок)")

    def _append_row_sync(self, spreadsheet_id: str, row: List[str], sheet_name: str,
                         columns: Optional[List[str]] = None):
        """Добавляет строку в таблицу (синхронно)."""
        from datetime import datetime

        spreadsheet = self._open_spreadsheet(spreadsheet_id)
        worksheet = self._get_or_create_sheet(spreadsheet, sheet_name)
        cache_key = (spreadsheet_id, sheet_name)
        if columns and cache_key not in self._verified_sheets:
            self._ensure_headers_exist(worksheet, columns)
            self._verified_sheets.add(cache_key)

        # Заполняем № заявки: "Заявка XXYY" (XX=строка, YY=день)
        if columns and 'request_number' in columns:
            idx = list(columns).index('request_number')
            if idx < len(row):
                existing_rows = len(worksheet.col_values(1))  # строк с данными в колонке A
                next_row = existing_rows + 1
                day = datetime.now().strftime('%d')
                row[idx] = f"{next_row:02d}{day}"

        worksheet.append_row(row, value_input_option='USER_ENTERED')

    def _check_access_sync(self, spreadsheet_id: str) -> bool:
        """Проверяет доступ к таблице (синхронно)."""
        try:
            spreadsheet = self._open_spreadsheet(spreadsheet_id)
            spreadsheet.title  # Проверяем что можем прочитать
            return True
        except Exception:
            return False

    async def check_access(self, spreadsheet_id: str) -> bool:
        """Проверяет доступ к таблице (async)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, functools.partial(self._check_access_sync, spreadsheet_id)
        )

    async def setup_headers(self, spreadsheet_id: str, columns: List[str],
                           sheet_name: str = 'Тендеры'):
        """Создаёт заголовки на листе."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, functools.partial(self._setup_headers_sync, spreadsheet_id, columns, sheet_name)
        )

    async def append_tender(self, spreadsheet_id: str, tender_data: Dict[str, Any],
                           match_data: Dict[str, Any], columns: List[str],
                           sheet_name: str = None) -> bool:
        """
        Добавляет строку с тендером в Google Sheets.

        Args:
            spreadsheet_id: ID таблицы
            tender_data: Данные тендера
            match_data: Данные из match_info + filter_name + ai_data
            columns: Список колонок для заполнения
            sheet_name: Имя листа

        Returns:
            True если успешно
        """
        try:
            if sheet_name is None:
                sheet_name = get_weekly_sheet_name()
            if not columns:
                columns = DEFAULT_COLUMNS
            if 'request_number' not in columns:
                columns = ['request_number'] + list(columns)
            row = self._format_row(tender_data, match_data, columns)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, functools.partial(self._append_row_sync, spreadsheet_id, row, sheet_name, columns)
            )
            logger.info(f"📊 Google Sheets: добавлен тендер {tender_data.get('number', '?')}")
            return True
        except Exception as e:
            logger.error(f"❌ Google Sheets ошибка: {e}")
            return False

    def _format_row(self, tender_data: Dict[str, Any], match_data: Dict[str, Any],
                    columns: List[str]) -> List[str]:
        """Формирует строку данных для таблицы."""
        row = []
        for col in columns:
            if col in COLUMN_DEFINITIONS:
                _, extractor = COLUMN_DEFINITIONS[col]
                try:
                    value = extractor(tender_data, match_data)
                    row.append(str(value) if value is not None else '')
                except Exception:
                    row.append('')
            else:
                row.append('')
        return row


async def enrich_tender_with_ai(tender_number: str, tender_price=None,
                                customer_name: str = '',
                                subscription_tier: str = 'premium') -> Dict[str, Any]:
    """
    Полный AI-обогащение: скачать документацию → извлечь текст → AI анализ → flatten.

    Returns:
        Плоский dict для ai_data, или пустой dict при ошибке.
    """
    try:
        from src.parsers.zakupki_document_downloader import ZakupkiDocumentDownloader
        from src.document_processor.text_extractor import TextExtractor
        from tender_sniper.ai_document_extractor import get_document_extractor

        # 1. Скачиваем документы
        downloader = ZakupkiDocumentDownloader()
        tender_url = (
            f"https://zakupki.gov.ru/epz/order/notice/ea44/view/"
            f"common-info.html?regNumber={tender_number}"
        )

        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: downloader.download_documents(tender_url, tender_number, None)
        )

        if not result or result.get('downloaded', 0) == 0:
            logger.info(f"📄 AI enrichment: нет документов для {tender_number}")
            return {}

        # 2. Извлекаем текст (до 3 файлов)
        combined_text = ""
        for doc_info in result.get('files', [])[:3]:
            doc_path = doc_info.get('path')
            if not doc_path:
                continue
            try:
                extract_result = TextExtractor.extract_text(doc_path)
                if extract_result['text'] and not extract_result['text'].startswith('[Не удалось'):
                    combined_text += f"\n\n=== {extract_result['file_name']} ===\n{extract_result['text']}"
            except Exception as e:
                logger.warning(f"   Не удалось извлечь текст из {doc_path}: {e}")

        if not combined_text:
            logger.info(f"📄 AI enrichment: не удалось извлечь текст для {tender_number}")
            return {}

        # 3. AI анализ
        extractor = get_document_extractor()
        tender_info = {'number': tender_number}
        if tender_price:
            tender_info['price'] = tender_price
        if customer_name:
            tender_info['customer'] = customer_name

        extraction, is_ai = await extractor.extract_from_text(
            combined_text, subscription_tier, tender_info
        )

        if not extraction or extraction.get('error'):
            logger.warning(f"📄 AI enrichment: ошибка анализа для {tender_number}")
            return {}

        # 4. Flatten
        flat = flatten_ai_extraction(extraction)
        logger.info(f"📄 AI enrichment: {tender_number} → {len(flat)} полей")
        return flat

    except ImportError as e:
        logger.warning(f"📄 AI enrichment: модуль не найден: {e}")
        return {}
    except Exception as e:
        logger.error(f"📄 AI enrichment ошибка для {tender_number}: {e}")
        return {}


# Singleton
_sheets_sync_instance: Optional[GoogleSheetsSync] = None


def get_sheets_sync() -> Optional[GoogleSheetsSync]:
    """Возвращает singleton GoogleSheetsSync или None если не настроен."""
    global _sheets_sync_instance
    if _sheets_sync_instance is not None:
        return _sheets_sync_instance
    creds = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON', '')
    if not creds:
        logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON не задан в env")
        return None
    # Поддержка base64-кодированного JSON (для Railway)
    if not creds.startswith('{'):
        import base64
        try:
            creds = base64.b64decode(creds).decode('utf-8')
            logger.info("Google Sheets: JSON декодирован из base64")
        except Exception as e:
            logger.error(f"Ошибка декодирования base64: {e}")
            return None
    try:
        instance = GoogleSheetsSync(creds)
        email = instance.get_service_email()
        if not email:
            logger.error(f"Google Sheets: client_email не найден в JSON (первые 100 символов: {creds[:100]})")
            return None
        _sheets_sync_instance = instance
        logger.info(f"GoogleSheetsSync инициализирован, email: {email}")
        return _sheets_sync_instance
    except Exception as e:
        logger.error(f"Ошибка инициализации GoogleSheetsSync: {e}")
        return None
