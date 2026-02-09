"""
Google Sheets Sync для Tender Sniper.

Автоматически добавляет строки с тендерами в Google-таблицу пользователя.
"""

import os
import json
import asyncio
import functools
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Маппинг колонок: ключ → (заголовок RU, функция извлечения данных)
COLUMN_DEFINITIONS = {
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
DEFAULT_COLUMNS = ['link', 'name', 'customer', 'region', 'deadline', 'price', 'score', 'status']


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

    def _append_row_sync(self, spreadsheet_id: str, row: List[str], sheet_name: str):
        """Добавляет строку в таблицу (синхронно)."""
        spreadsheet = self._open_spreadsheet(spreadsheet_id)
        worksheet = self._get_or_create_sheet(spreadsheet, sheet_name)
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
                           sheet_name: str = 'Тендеры') -> bool:
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
            row = self._format_row(tender_data, match_data, columns)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, functools.partial(self._append_row_sync, spreadsheet_id, row, sheet_name)
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
    try:
        _sheets_sync_instance = GoogleSheetsSync(creds)
        logger.info(f"GoogleSheetsSync инициализирован, email: {_sheets_sync_instance.get_service_email()}")
        return _sheets_sync_instance
    except Exception as e:
        logger.error(f"Ошибка инициализации GoogleSheetsSync: {e}")
        return None
