"""
YooKassa payment client for Tender Sniper.

Handles payment creation, status checking, and webhook processing.
"""

import os
import uuid
import logging
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Настройки YooKassa
YOOKASSA_SHOP_ID = os.getenv('YOOKASSA_SHOP_ID', '')
YOOKASSA_SECRET_KEY = os.getenv('YOOKASSA_SECRET_KEY', '')

# Цены тарифов (в рублях)
TARIFF_PRICES = {
    'basic': 490.00,
    'premium': 990.00,
}

# Названия тарифов для описания платежа
TARIFF_NAMES = {
    'basic': 'Basic (1 месяц)',
    'premium': 'Premium (1 месяц)',
}


class YooKassaClient:
    """Клиент для работы с YooKassa API."""

    def __init__(self):
        """Инициализация клиента."""
        self.shop_id = YOOKASSA_SHOP_ID
        self.secret_key = YOOKASSA_SECRET_KEY
        self._configured = bool(self.shop_id and self.secret_key)

        if self._configured:
            try:
                from yookassa import Configuration
                Configuration.account_id = self.shop_id
                Configuration.secret_key = self.secret_key
                logger.info("YooKassa configured successfully")
            except ImportError:
                logger.warning("yookassa package not installed")
                self._configured = False
        else:
            logger.warning("YooKassa credentials not configured")

    @property
    def is_configured(self) -> bool:
        """Проверка настройки клиента."""
        return self._configured

    def create_payment(
        self,
        telegram_id: int,
        tier: str,
        amount: Optional[float] = None,
        days: Optional[int] = None,
        description: Optional[str] = None,
        return_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Создать платёж в YooKassa.

        Args:
            telegram_id: Telegram ID пользователя
            tier: Тарифный план (basic/premium)
            amount: Сумма платежа (если None, берётся из TARIFF_PRICES)
            days: Количество дней подписки (если None, 30 дней)
            description: Описание платежа
            return_url: URL для возврата после оплаты

        Returns:
            dict: {
                'payment_id': str,
                'url': str (ссылка для оплаты),
                'amount': float,
                'status': str
            }
        """
        if not self._configured:
            logger.error("YooKassa not configured")
            return {'error': 'Payment system not configured'}

        if tier not in TARIFF_PRICES:
            return {'error': f'Invalid tier: {tier}'}

        # Используем переданную сумму или дефолтную
        if amount is None:
            amount = TARIFF_PRICES[tier]

        # Используем переданные дни или дефолт 30
        if days is None:
            days = 30

        # Используем переданное описание или генерируем
        if description is None:
            description = f"Подписка Tender Sniper - {TARIFF_NAMES.get(tier, tier)}"

        try:
            from yookassa import Payment

            idempotence_key = str(uuid.uuid4())

            payment_data = {
                "amount": {
                    "value": f"{amount:.2f}",
                    "currency": "RUB"
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url or "https://t.me/TenderSniperBot"
                },
                "capture": True,
                "description": description,
                "metadata": {
                    "telegram_id": str(telegram_id),
                    "tier": tier,
                    "days": str(days)
                },
                "receipt": {
                    "customer": {
                        "email": f"user_{telegram_id}@tendersniper.ru"
                    },
                    "items": [
                        {
                            "description": description,
                            "quantity": "1.00",
                            "amount": {
                                "value": f"{amount:.2f}",
                                "currency": "RUB"
                            },
                            "vat_code": 1,  # Без НДС
                            "payment_mode": "full_payment",
                            "payment_subject": "service"
                        }
                    ]
                }
            }

            payment = Payment.create(payment_data, idempotence_key)

            logger.info(f"Payment created: {payment.id} for user {telegram_id}, tier {tier}")

            return {
                'payment_id': payment.id,
                'url': payment.confirmation.confirmation_url,
                'amount': amount,
                'status': payment.status
            }

        except Exception as e:
            logger.error(f"Failed to create payment: {e}", exc_info=True)
            return {'error': str(e)}

    def check_status(self, payment_id: str) -> Dict[str, Any]:
        """
        Проверить статус платежа.

        Args:
            payment_id: ID платежа в YooKassa

        Returns:
            dict: {
                'status': str (pending/waiting_for_capture/succeeded/canceled),
                'paid': bool,
                'amount': float,
                'metadata': dict
            }
        """
        if not self._configured:
            return {'error': 'Payment system not configured'}

        try:
            from yookassa import Payment

            payment = Payment.find_one(payment_id)

            return {
                'status': payment.status,
                'paid': payment.paid,
                'amount': float(payment.amount.value),
                'metadata': payment.metadata or {}
            }

        except Exception as e:
            logger.error(f"Failed to check payment status: {e}", exc_info=True)
            return {'error': str(e)}

    @staticmethod
    def parse_webhook(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Парсинг webhook данных от YooKassa.

        Args:
            data: JSON данные от webhook

        Returns:
            dict: {
                'event': str,
                'payment_id': str,
                'status': str,
                'telegram_id': int,
                'tier': str,
                'amount': float
            }
            или None если данные некорректны
        """
        try:
            event = data.get('event')
            obj = data.get('object', {})

            payment_id = obj.get('id')
            status = obj.get('status')
            metadata = obj.get('metadata', {})

            telegram_id = metadata.get('telegram_id')
            tier = metadata.get('tier')
            amount = float(obj.get('amount', {}).get('value', 0))

            if not all([event, payment_id, telegram_id, tier]):
                logger.warning(f"Incomplete webhook data: {data}")
                return None

            return {
                'event': event,
                'payment_id': payment_id,
                'status': status,
                'telegram_id': int(telegram_id),
                'tier': tier,
                'amount': amount
            }

        except Exception as e:
            logger.error(f"Failed to parse webhook: {e}", exc_info=True)
            return None


# Синглтон клиента
_client: Optional[YooKassaClient] = None


def get_yookassa_client() -> YooKassaClient:
    """Получить экземпляр YooKassa клиента."""
    global _client
    if _client is None:
        _client = YooKassaClient()
    return _client
