"""
AI Document Extractor - извлечение структурированных данных из тендерной документации.

Использует GPT-4o-mini для извлечения ключевой информации из PDF/DOCX файлов.
PREMIUM функция - доступна только для Premium пользователей.
"""

import asyncio
import json
import logging
import os
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime

from tender_sniper.ai_features import AIFeatureGate, format_ai_feature_locked_message

logger = logging.getLogger(__name__)


class TenderDocumentExtractor:
    """
    Извлекает структурированные данные из тендерной документации.

    Особенности:
    - Извлекает требования к участникам
    - Находит условия оплаты и сроки
    - Определяет технические спецификации
    - Выделяет критерии оценки заявок
    - Определяет размер обеспечения
    """

    MODEL = "gpt-4o-mini"
    MAX_INPUT_CHARS = 30000  # ~8k токенов
    MAX_OUTPUT_TOKENS = 3000

    EXTRACTION_PROMPT = """Ты эксперт по анализу тендерной документации госзакупок России.

Извлеки структурированную информацию из документации тендера. Ответ СТРОГО в JSON.

САМОЕ ВАЖНОЕ — заполни "items" максимально подробно! Это главная ценность анализа.

{
    "items_count": число наименований/позиций в спецификации,
    "items": [
        {
            "name": "точное название позиции из спецификации",
            "quantity": "количество с единицей измерения, например '500 шт' или '10 м²'",
            "characteristics": "ВСЕ характеристики: размеры, материал, мощность, цвет, тип, класс и т.д.",
            "brand": "бренд/марка/производитель если указан, иначе null"
        }
    ],
    "submission_deadline": "дата и время окончания подачи заявок: '15.03.2026 10:00' или null",
    "trading_platform": "название ЭТП: 'РТС-тендер', 'Сбербанк-АСТ', 'ЗАО «Сбербанк-АСТ»', 'ЭТП ГПБ' и т.п. или null",
    "deadlines": {
        "execution_description": "'20 рабочих дней' или '01.03.2026'",
        "delivery_address": "адрес ПОСТАВКИ товара (НЕ юридический адрес заказчика!)"
    },
    "requirements": {
        "licenses": ["только КОНКРЕТНЫЕ: 'Лицензия ФСБ', 'СРО' и т.п."],
        "experience_years": число или null,
        "sro_required": true/false
    },
    "payment_terms": {
        "advance_percent": число или null,
        "payment_deadline_days": число или null
    },
    "contract_security": {
        "application_security_percent": число или null,
        "contract_security_percent": число или null,
        "bank_guarantee_allowed": true/false/null
    },
    "quality_standards": ["ГОСТ, ТУ, ISO если упоминаются"],
    "risks": ["конкретные риски"],
    "summary": "макс 2 предложения: ключевые бизнес-условия"
}

ПРАВИЛА:
1. "items" — ОБЯЗАТЕЛЬНО заполни! Извлеки ВСЕ позиции из спецификации/ТЗ (макс 10). Для КАЖДОЙ позиции укажи ВСЕ характеристики которые есть в документе (размеры, материал, цвет, мощность, вес, класс энергоэффективности, тип и т.д.)
2. Если в документе указан бренд, марка, модель или производитель — ОБЯЗАТЕЛЬНО укажи в "brand"
3. "submission_deadline" — дата/время окончания приёма заявок. Ищи фразы: "дата и время окончания подачи заявок", "окончание подачи", "прием заявок до"
4. "trading_platform" — ищи: "электронная площадка", "ЭТП", "оператор электронной площадки"
5. "delivery_address" — это куда везти товар, НЕ юридический адрес заказчика
6. Сроки: "20 рабочих дней" или "01.03.2026". Никаких описательных фраз
7. Лицензии: ТОЛЬКО конкретные названия. НЕ "соответствие требованиям"
8. Проценты — просто число (5, не "5%")
9. Если данных нет — null, пустой массив []

ДОКУМЕНТАЦИЯ ТЕНДЕРА:
"""

    def __init__(self, api_key: Optional[str] = None):
        """
        Инициализация экстрактора.

        Args:
            api_key: OpenAI API key (если None, берётся из OPENAI_API_KEY)
        """
        self.api_key = api_key or os.getenv('OPENAI_API_KEY')
        self._client = None

    @property
    def client(self):
        """Ленивая инициализация OpenAI клиента."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(api_key=self.api_key)
            except ImportError:
                logger.warning("OpenAI библиотека не установлена")
                return None
        return self._client

    async def extract_from_text(
        self,
        document_text: str,
        subscription_tier: str = 'trial',
        tender_info: Optional[Dict[str, Any]] = None
    ) -> Tuple[Dict[str, Any], bool]:
        """
        Извлекает структурированные данные из текста документации.

        Args:
            document_text: Текст документации (уже извлечённый из PDF/DOCX)
            subscription_tier: Тариф пользователя
            tender_info: Дополнительная информация о тендере (номер, цена и т.д.)

        Returns:
            Tuple[Dict, bool]: (извлечённые данные, is_ai_extracted)
        """
        # Проверяем Premium доступ
        gate = AIFeatureGate(subscription_tier)
        if not gate.can_use('summarization'):  # Используем ту же проверку что и для суммаризации
            return ({
                'error': 'premium_required',
                'message': format_ai_feature_locked_message('summarization')
            }, False)

        if not self.api_key or not self.client:
            logger.warning("OpenAI API недоступен")
            return (self._create_fallback_extraction(document_text, tender_info), False)

        # Обрезаем текст если слишком длинный
        if len(document_text) > self.MAX_INPUT_CHARS:
            # Используем SmartDocumentTruncator для умной обрезки
            try:
                from src.analyzers.smart_document_processor import SmartDocumentTruncator
                truncator = SmartDocumentTruncator()
                document_text = truncator.smart_truncate(document_text, self.MAX_INPUT_CHARS)
            except ImportError:
                document_text = document_text[:self.MAX_INPUT_CHARS] + "\n\n[Документ обрезан...]"

        # Добавляем контекст из tender_info
        context = ""
        if tender_info:
            context_parts = []
            if tender_info.get('number'):
                context_parts.append(f"Номер закупки: {tender_info['number']}")
            if tender_info.get('price'):
                context_parts.append(f"НМЦ: {tender_info['price']:,.0f} ₽")
            if tender_info.get('customer'):
                context_parts.append(f"Заказчик: {tender_info['customer']}")
            if context_parts:
                context = "ИНФОРМАЦИЯ О ТЕНДЕРЕ:\n" + "\n".join(context_parts) + "\n\n"

        try:
            response = await self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "user", "content": self.EXTRACTION_PROMPT + context + document_text}
                ],
                max_tokens=self.MAX_OUTPUT_TOKENS,
                temperature=0.1,  # Очень низкая для точности
                response_format={"type": "json_object"}
            )

            result_text = response.choices[0].message.content.strip()

            try:
                extracted_data = json.loads(result_text)
                extracted_data['_meta'] = {
                    'extracted_at': datetime.now().isoformat(),
                    'source': 'ai',
                    'model': self.MODEL,
                    'input_chars': len(document_text)
                }
                logger.info(f"✅ AI-извлечение завершено из {len(document_text)} символов")
                return (extracted_data, True)

            except json.JSONDecodeError as e:
                logger.error(f"Ошибка парсинга JSON от AI: {e}")
                return (self._create_fallback_extraction(document_text, tender_info), False)

        except Exception as e:
            logger.error(f"❌ Ошибка AI-извлечения: {e}")
            return (self._create_fallback_extraction(document_text, tender_info), False)

    def _create_fallback_extraction(
        self,
        document_text: str,
        tender_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Создаёт базовое извлечение без AI (regex-based fallback).

        Args:
            document_text: Текст документации
            tender_info: Информация о тендере

        Returns:
            Базовые извлечённые данные
        """
        import re

        text_lower = document_text.lower()

        # Базовое извлечение через regex
        result = {
            'requirements': {
                'licenses': [],
                'experience_years': None,
                'sro_required': 'сро' in text_lower or 'саморегулируемой' in text_lower,
                'staff_requirements': None,
                'equipment_requirements': None,
                'financial_requirements': None
            },
            'payment_terms': {
                'advance_percent': None,
                'payment_stages': [],
                'payment_deadline_days': None,
                'payment_conditions': None
            },
            'contract_security': {
                'application_security_percent': None,
                'contract_security_percent': None,
                'warranty_security_percent': None,
                'bank_guarantee_allowed': 'банковская гарантия' in text_lower
            },
            'deadlines': {
                'execution_days': None,
                'execution_description': None,
                'delivery_address': None,
                'stages': []
            },
            'evaluation_criteria': {
                'price_weight': None,
                'quality_weight': None,
                'other_criteria': []
            },
            'technical_specs': {
                'main_items': [],
                'quantities': None,
                'quality_standards': [],
                'special_requirements': []
            },
            'risks': [],
            'summary': 'Требуется детальный анализ документации.',
            '_meta': {
                'extracted_at': datetime.now().isoformat(),
                'source': 'fallback',
                'input_chars': len(document_text)
            }
        }

        # Ищем проценты обеспечения
        security_patterns = [
            (r'обеспечение заявки[:\s]+(\d+(?:[.,]\d+)?)\s*%', 'application_security_percent'),
            (r'обеспечение (?:исполнения )?контракта[:\s]+(\d+(?:[.,]\d+)?)\s*%', 'contract_security_percent'),
            (r'гарантийн\w+ обеспечени\w+[:\s]+(\d+(?:[.,]\d+)?)\s*%', 'warranty_security_percent'),
        ]

        for pattern, field in security_patterns:
            match = re.search(pattern, text_lower)
            if match:
                try:
                    result['contract_security'][field] = float(match.group(1).replace(',', '.'))
                except:
                    pass

        # Ищем сроки исполнения
        deadline_patterns = [
            r'срок (?:исполнения|выполнения|поставки)[:\s]+(\d+)\s*(?:календарн\w+|рабочих)?\s*дн',
            r'в течение\s+(\d+)\s*(?:календарн\w+|рабочих)?\s*дн',
        ]

        for pattern in deadline_patterns:
            match = re.search(pattern, text_lower)
            if match:
                try:
                    result['deadlines']['execution_days'] = int(match.group(1))
                    break
                except:
                    pass

        # Ищем лицензии
        license_patterns = [
            'лицензия фсб',
            'лицензия фстэк',
            'лицензия мчс',
            'лицензия минздрав',
            'лицензия ростехнадзор',
            'медицинская лицензия',
            'строительная лицензия',
        ]

        for lic in license_patterns:
            if lic in text_lower:
                result['requirements']['licenses'].append(lic.title())

        # Ищем опыт
        exp_match = re.search(r'опыт\w*\s+(?:работы\s+)?(?:не\s+)?менее\s+(\d+)\s*(?:лет|года)', text_lower)
        if exp_match:
            result['requirements']['experience_years'] = int(exp_match.group(1))

        # Определяем риски
        if result['requirements']['licenses']:
            result['risks'].append(f"Требуются лицензии: {', '.join(result['requirements']['licenses'])}")
        if result['requirements']['sro_required']:
            result['risks'].append("Требуется членство в СРО")
        if result['deadlines']['execution_days'] and result['deadlines']['execution_days'] < 30:
            result['risks'].append(f"Короткий срок исполнения: {result['deadlines']['execution_days']} дней")

        return result

    async def extract_from_file(
        self,
        file_path: str,
        subscription_tier: str = 'trial',
        tender_info: Optional[Dict[str, Any]] = None
    ) -> Tuple[Dict[str, Any], bool]:
        """
        Извлекает данные напрямую из файла документации.

        Args:
            file_path: Путь к файлу (PDF, DOCX, и т.д.)
            subscription_tier: Тариф пользователя
            tender_info: Информация о тендере

        Returns:
            Tuple[Dict, bool]: (извлечённые данные, is_ai_extracted)
        """
        try:
            from src.document_processor.text_extractor import TextExtractor

            # Извлекаем текст из файла
            result = TextExtractor.extract_text(file_path)
            document_text = result['text']

            if not document_text or document_text.startswith('[Не удалось'):
                return ({
                    'error': 'extraction_failed',
                    'message': f"Не удалось извлечь текст из файла: {file_path}"
                }, False)

            return await self.extract_from_text(document_text, subscription_tier, tender_info)

        except Exception as e:
            logger.error(f"Ошибка извлечения из файла {file_path}: {e}")
            return ({
                'error': 'file_error',
                'message': str(e)
            }, False)


def format_extraction_for_telegram(extraction: Dict[str, Any], is_ai: bool) -> str:
    """
    Форматирует извлечённые данные для отображения в Telegram.

    Args:
        extraction: Извлечённые данные
        is_ai: Был ли использован AI

    Returns:
        Отформатированный текст для Telegram
    """
    if extraction.get('error'):
        return extraction.get('message', 'Ошибка извлечения данных')

    lines = []

    # Заголовок
    source = "🤖 AI" if is_ai else "📋 Базовый"
    lines.append(f"<b>📄 Анализ документации</b> ({source})\n")

    # Площадка
    if extraction.get('trading_platform'):
        lines.append(f"<b>🏛 Площадка:</b> {extraction['trading_platform']}")

    # Подача заявок (top-level)
    submission = extraction.get('submission_deadline')
    if submission:
        lines.append(f"<b>⏰ Подача заявок до:</b> {submission}")

    if extraction.get('trading_platform') or submission:
        lines.append("")

    # Товарные позиции (top-level "items" или fallback к old schema)
    items = extraction.get('items', [])
    items_count = extraction.get('items_count')
    # Fallback: старая схема technical_specs.items_details
    if not items:
        specs = extraction.get('technical_specs', {})
        items = specs.get('items_details', [])
        if not items_count:
            items_count = specs.get('items_count')

    if items:
        count_str = f" ({items_count} наим.)" if items_count else ""
        lines.append(f"<b>📦 Товары/работы{count_str}:</b>")
        for item in items[:10]:
            name = item.get('name', '')
            qty = item.get('quantity', '')
            chars = item.get('characteristics', '')
            brand = item.get('brand')

            line_parts = [f"<b>{name}</b>"]
            if qty:
                line_parts.append(f"— {qty}")
            lines.append(f"• {' '.join(line_parts)}")
            if chars:
                lines.append(f"  ↳ {chars[:150]}")
            if brand:
                lines.append(f"  ↳ Бренд: {brand}")
        lines.append("")
    else:
        # Совсем старая схема: main_items
        specs = extraction.get('technical_specs', {})
        if specs.get('main_items'):
            lines.append("<b>📦 Позиции:</b>")
            for item in specs['main_items'][:5]:
                lines.append(f"• {item}")
            if specs.get('quantities'):
                lines.append(f"Кол-во: {specs['quantities']}")
            lines.append("")

    # Сроки
    deadlines = extraction.get('deadlines', {})
    # submission_deadline может быть и внутри deadlines (старая схема)
    if not submission:
        submission = deadlines.get('submission_deadline')
    has_deadlines = any([
        deadlines.get('execution_days'),
        deadlines.get('execution_description'),
        deadlines.get('delivery_address'),
    ])
    if has_deadlines:
        lines.append("<b>📅 Сроки исполнения:</b>")
        if deadlines.get('execution_days'):
            lines.append(f"• Исполнение: {deadlines['execution_days']} дней")
        if deadlines.get('execution_description'):
            lines.append(f"• {deadlines['execution_description'][:100]}")
        if deadlines.get('delivery_address'):
            lines.append(f"• Адрес поставки: {deadlines['delivery_address'][:120]}")
        # Если submission_deadline был только в deadlines и ещё не показан
        if not extraction.get('submission_deadline') and deadlines.get('submission_deadline'):
            lines.append(f"• Подача заявок до: <b>{deadlines['submission_deadline']}</b>")
        lines.append("")

    # Требования
    req = extraction.get('requirements', {})
    if any([req.get('licenses'), req.get('experience_years'), req.get('sro_required')]):
        lines.append("<b>⚠️ Требования к участнику:</b>")
        if req.get('licenses'):
            lines.append(f"• Лицензии: {', '.join(req['licenses'])}")
        if req.get('experience_years'):
            lines.append(f"• Опыт: от {req['experience_years']} лет")
        if req.get('sro_required'):
            lines.append("• Членство в СРО: требуется")
        lines.append("")

    # Обеспечение
    sec = extraction.get('contract_security', {})
    if any([sec.get('application_security_percent'), sec.get('contract_security_percent')]):
        lines.append("<b>💳 Обеспечение:</b>")
        if sec.get('application_security_percent'):
            lines.append(f"• Заявка: {sec['application_security_percent']}%")
        if sec.get('contract_security_percent'):
            lines.append(f"• Контракт: {sec['contract_security_percent']}%")
        if sec.get('bank_guarantee_allowed'):
            lines.append("• Банковская гарантия: допускается")
        lines.append("")

    # Оплата
    pay = extraction.get('payment_terms', {})
    if any([pay.get('advance_percent'), pay.get('payment_deadline_days')]):
        lines.append("<b>💰 Оплата:</b>")
        if pay.get('advance_percent'):
            lines.append(f"• Аванс: {pay['advance_percent']}%")
        if pay.get('payment_deadline_days'):
            lines.append(f"• Срок оплаты: {pay['payment_deadline_days']} дней")
        lines.append("")

    # Стандарты качества (top-level или в specs)
    standards = extraction.get('quality_standards', [])
    if not standards:
        standards = extraction.get('technical_specs', {}).get('quality_standards', [])
    if standards:
        lines.append("<b>📐 Стандарты:</b>")
        lines.append(f"• {', '.join(standards[:5])}")
        lines.append("")

    # Риски
    risks = extraction.get('risks', [])
    if risks:
        lines.append("<b>🚩 Риски:</b>")
        for risk in risks[:5]:
            lines.append(f"• {risk}")
        lines.append("")

    # Резюме
    if extraction.get('summary'):
        lines.append(f"<b>📝 Резюме:</b> {extraction['summary']}")

    return "\n".join(lines)


# Singleton instance
_extractor_instance: Optional[TenderDocumentExtractor] = None


def get_document_extractor() -> TenderDocumentExtractor:
    """Получить singleton экземпляр экстрактора."""
    global _extractor_instance
    if _extractor_instance is None:
        _extractor_instance = TenderDocumentExtractor()
    return _extractor_instance


async def extract_tender_documentation(
    document_text: str,
    subscription_tier: str = 'trial',
    tender_info: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, Any], bool]:
    """
    Удобная функция для извлечения данных из документации.

    Args:
        document_text: Текст документации
        subscription_tier: Тариф пользователя
        tender_info: Информация о тендере

    Returns:
        Tuple[Dict, bool]: (данные, is_ai_extracted)
    """
    extractor = get_document_extractor()
    return await extractor.extract_from_text(document_text, subscription_tier, tender_info)
