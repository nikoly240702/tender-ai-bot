"""
Интеграции Tender Sniper.

Поддерживаемые интеграции:
- CRM Webhook (отправка тендеров в вашу CRM)
- Google Sheets (автоматический экспорт)
- Email уведомления (дубликат важных тендеров)
"""

import asyncio
import logging
import aiohttp
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# Email настройки (из env)
SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
EMAIL_FROM = os.getenv('EMAIL_FROM', SMTP_USER)


class IntegrationManager:
    """Менеджер интеграций для отправки тендеров в внешние системы."""

    def __init__(self):
        self._http_session = None

    async def get_session(self) -> aiohttp.ClientSession:
        """Получить HTTP сессию."""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._http_session

    async def close(self):
        """Закрыть сессию."""
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    # ============================================
    # CRM WEBHOOK
    # ============================================

    async def send_to_webhook(
        self,
        webhook_url: str,
        tender_data: Dict[str, Any],
        secret_key: Optional[str] = None
    ) -> bool:
        """
        Отправить тендер в CRM через webhook.

        Args:
            webhook_url: URL webhook'а
            tender_data: Данные тендера
            secret_key: Секретный ключ для подписи (опционально)

        Returns:
            bool: Успешность отправки
        """
        try:
            session = await self.get_session()

            # Формируем payload
            payload = {
                "event": "new_tender",
                "timestamp": datetime.utcnow().isoformat(),
                "tender": {
                    "number": tender_data.get('number', ''),
                    "name": tender_data.get('name', ''),
                    "price": tender_data.get('price', 0),
                    "customer": tender_data.get('customer', ''),
                    "region": tender_data.get('region', ''),
                    "deadline": tender_data.get('deadline', ''),
                    "url": tender_data.get('url', ''),
                    "law_type": tender_data.get('law_type', ''),
                    "filter_name": tender_data.get('filter_name', ''),
                    "score": tender_data.get('score', 0),
                }
            }

            headers = {
                "Content-Type": "application/json",
                "User-Agent": "TenderSniper/1.0"
            }

            # Добавляем секретный ключ если есть
            if secret_key:
                import hashlib
                import json
                signature = hashlib.sha256(
                    (json.dumps(payload, sort_keys=True) + secret_key).encode()
                ).hexdigest()
                headers["X-Webhook-Signature"] = signature

            async with session.post(webhook_url, json=payload, headers=headers) as resp:
                if resp.status in (200, 201, 202, 204):
                    logger.info(f"Webhook sent successfully to {webhook_url[:50]}...")
                    return True
                else:
                    logger.warning(f"Webhook failed: {resp.status} - {await resp.text()}")
                    return False

        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return False

    async def test_webhook(self, webhook_url: str) -> Dict[str, Any]:
        """
        Тестовый запрос к webhook.

        Returns:
            dict: {success: bool, message: str, response_time: float}
        """
        import time
        start = time.time()

        try:
            session = await self.get_session()

            test_payload = {
                "event": "test",
                "timestamp": datetime.utcnow().isoformat(),
                "message": "Test connection from Tender Sniper"
            }

            async with session.post(webhook_url, json=test_payload) as resp:
                response_time = time.time() - start

                if resp.status in (200, 201, 202, 204):
                    return {
                        "success": True,
                        "message": f"OK ({resp.status})",
                        "response_time": round(response_time * 1000)
                    }
                else:
                    return {
                        "success": False,
                        "message": f"HTTP {resp.status}",
                        "response_time": round(response_time * 1000)
                    }

        except asyncio.TimeoutError:
            return {"success": False, "message": "Timeout", "response_time": 30000}
        except Exception as e:
            return {"success": False, "message": str(e)[:100], "response_time": 0}

    # ============================================
    # GOOGLE SHEETS
    # ============================================

    async def append_to_google_sheet(
        self,
        spreadsheet_id: str,
        credentials_json: str,
        tender_data: Dict[str, Any],
        sheet_name: str = "Тендеры"
    ) -> bool:
        """
        Добавить тендер в Google Sheets.

        Args:
            spreadsheet_id: ID таблицы Google Sheets
            credentials_json: JSON с сервисным аккаунтом Google
            tender_data: Данные тендера
            sheet_name: Название листа

        Returns:
            bool: Успешность операции
        """
        try:
            # Используем gspread для работы с Google Sheets
            import gspread
            from google.oauth2.service_account import Credentials
            import json

            # Парсим credentials
            creds_dict = json.loads(credentials_json)
            credentials = Credentials.from_service_account_info(
                creds_dict,
                scopes=[
                    'https://www.googleapis.com/auth/spreadsheets',
                    'https://www.googleapis.com/auth/drive'
                ]
            )

            # Подключаемся
            gc = gspread.authorize(credentials)
            sh = gc.open_by_key(spreadsheet_id)

            # Получаем или создаём лист
            try:
                worksheet = sh.worksheet(sheet_name)
            except gspread.WorksheetNotFound:
                worksheet = sh.add_worksheet(title=sheet_name, rows=1000, cols=10)
                # Добавляем заголовки
                worksheet.append_row([
                    "Дата", "Номер", "Название", "Цена", "Заказчик",
                    "Регион", "Дедлайн", "Закон", "Ссылка", "Фильтр"
                ])

            # Форматируем данные
            price = tender_data.get('price', 0)
            if isinstance(price, (int, float)) and price > 0:
                price_str = f"{price:,.0f}".replace(",", " ")
            else:
                price_str = "-"

            row = [
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                tender_data.get('number', ''),
                tender_data.get('name', '')[:100],
                price_str,
                tender_data.get('customer', '')[:50],
                tender_data.get('region', ''),
                tender_data.get('deadline', ''),
                tender_data.get('law_type', ''),
                tender_data.get('url', ''),
                tender_data.get('filter_name', ''),
            ]

            # Добавляем строку
            worksheet.append_row(row)

            logger.info(f"Tender added to Google Sheets: {spreadsheet_id}")
            return True

        except ImportError:
            logger.error("gspread не установлен. Установите: pip install gspread google-auth")
            return False
        except Exception as e:
            logger.error(f"Google Sheets error: {e}")
            return False

    # ============================================
    # EMAIL NOTIFICATIONS
    # ============================================

    async def send_email_notification(
        self,
        to_email: str,
        tender_data: Dict[str, Any],
        subject_prefix: str = "[Tender Sniper]"
    ) -> bool:
        """
        Отправить email уведомление о тендере.

        Args:
            to_email: Email получателя
            tender_data: Данные тендера
            subject_prefix: Префикс темы письма

        Returns:
            bool: Успешность отправки
        """
        if not SMTP_USER or not SMTP_PASSWORD:
            logger.warning("SMTP not configured - email not sent")
            return False

        try:
            # Формируем тему
            tender_name = tender_data.get('name', 'Новый тендер')[:50]
            subject = f"{subject_prefix} {tender_name}"

            # Формируем тело письма
            price = tender_data.get('price', 0)
            if isinstance(price, (int, float)) and price > 0:
                price_str = f"{price:,.0f} ₽".replace(",", " ")
            else:
                price_str = "Не указана"

            html_body = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                    .tender-card {{
                        border: 1px solid #ddd;
                        border-radius: 8px;
                        padding: 20px;
                        max-width: 600px;
                        margin: 20px auto;
                    }}
                    .tender-title {{ color: #333; font-size: 18px; margin-bottom: 15px; }}
                    .tender-info {{ margin: 10px 0; }}
                    .tender-info strong {{ color: #555; }}
                    .tender-price {{ font-size: 20px; color: #28a745; font-weight: bold; }}
                    .tender-link {{
                        display: inline-block;
                        background: #667eea;
                        color: white;
                        padding: 10px 20px;
                        text-decoration: none;
                        border-radius: 5px;
                        margin-top: 15px;
                    }}
                    .footer {{ margin-top: 30px; font-size: 12px; color: #888; }}
                </style>
            </head>
            <body>
                <div class="tender-card">
                    <div class="tender-title">{tender_data.get('name', 'Без названия')}</div>

                    <div class="tender-info">
                        <strong>Номер:</strong> {tender_data.get('number', '-')}
                    </div>

                    <div class="tender-info tender-price">
                        {price_str}
                    </div>

                    <div class="tender-info">
                        <strong>Заказчик:</strong> {tender_data.get('customer', '-')}
                    </div>

                    <div class="tender-info">
                        <strong>Регион:</strong> {tender_data.get('region', '-')}
                    </div>

                    <div class="tender-info">
                        <strong>Дедлайн:</strong> {tender_data.get('deadline', '-')}
                    </div>

                    <div class="tender-info">
                        <strong>Фильтр:</strong> {tender_data.get('filter_name', '-')}
                    </div>

                    <a href="{tender_data.get('url', '#')}" class="tender-link">
                        Открыть тендер
                    </a>
                </div>

                <div class="footer">
                    Это автоматическое уведомление от Tender Sniper.<br>
                    Чтобы отключить email-уведомления, зайдите в настройки бота.
                </div>
            </body>
            </html>
            """

            # Создаём сообщение
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = EMAIL_FROM
            msg['To'] = to_email

            # Текстовая версия
            text_body = f"""
Новый тендер от Tender Sniper

{tender_data.get('name', 'Без названия')}

Номер: {tender_data.get('number', '-')}
Цена: {price_str}
Заказчик: {tender_data.get('customer', '-')}
Регион: {tender_data.get('region', '-')}
Дедлайн: {tender_data.get('deadline', '-')}

Ссылка: {tender_data.get('url', '-')}
            """

            msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_body, 'html', 'utf-8'))

            # Отправляем асинхронно
            def send_sync():
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                    server.starttls()
                    server.login(SMTP_USER, SMTP_PASSWORD)
                    server.send_message(msg)

            await asyncio.to_thread(send_sync)

            logger.info(f"Email sent to {to_email}")
            return True

        except Exception as e:
            logger.error(f"Email error: {e}")
            return False

    async def send_batch_email(
        self,
        to_email: str,
        tenders: List[Dict[str, Any]],
        subject: str = "[Tender Sniper] Новые тендеры"
    ) -> bool:
        """
        Отправить email с несколькими тендерами (дайджест).
        """
        if not SMTP_USER or not SMTP_PASSWORD:
            logger.warning("SMTP not configured - email not sent")
            return False

        if not tenders:
            return False

        try:
            # Формируем HTML для всех тендеров
            tenders_html = ""
            for tender in tenders[:20]:  # Максимум 20 тендеров
                price = tender.get('price', 0)
                if isinstance(price, (int, float)) and price > 0:
                    price_str = f"{price:,.0f} ₽".replace(",", " ")
                else:
                    price_str = "Не указана"

                tenders_html += f"""
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #eee;">
                        <a href="{tender.get('url', '#')}" style="color: #667eea; text-decoration: none;">
                            {tender.get('name', 'Без названия')[:80]}
                        </a>
                    </td>
                    <td style="padding: 10px; border-bottom: 1px solid #eee; text-align: right;">
                        <strong>{price_str}</strong>
                    </td>
                    <td style="padding: 10px; border-bottom: 1px solid #eee;">
                        {tender.get('deadline', '-')}
                    </td>
                </tr>
                """

            html_body = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; }}
                    table {{ width: 100%; border-collapse: collapse; }}
                    th {{ background: #667eea; color: white; padding: 12px; text-align: left; }}
                </style>
            </head>
            <body>
                <h2>Новые тендеры ({len(tenders)})</h2>
                <table>
                    <tr>
                        <th>Название</th>
                        <th>Цена</th>
                        <th>Дедлайн</th>
                    </tr>
                    {tenders_html}
                </table>
                <p style="margin-top: 20px; color: #888; font-size: 12px;">
                    Tender Sniper - автоматический мониторинг тендеров
                </p>
            </body>
            </html>
            """

            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"{subject} ({len(tenders)})"
            msg['From'] = EMAIL_FROM
            msg['To'] = to_email

            msg.attach(MIMEText(html_body, 'html', 'utf-8'))

            def send_sync():
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                    server.starttls()
                    server.login(SMTP_USER, SMTP_PASSWORD)
                    server.send_message(msg)

            await asyncio.to_thread(send_sync)

            logger.info(f"Batch email sent to {to_email} ({len(tenders)} tenders)")
            return True

        except Exception as e:
            logger.error(f"Batch email error: {e}")
            return False


# Глобальный экземпляр
_integration_manager = None


def get_integration_manager() -> IntegrationManager:
    """Получить экземпляр менеджера интеграций."""
    global _integration_manager
    if _integration_manager is None:
        _integration_manager = IntegrationManager()
    return _integration_manager
