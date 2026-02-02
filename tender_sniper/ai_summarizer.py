"""
AI Tender Summarizer - создаёт краткое резюме тендера.

Использует GPT-4o-mini для генерации структурированного резюме
на русском языке с ключевой информацией.

ВАЖНО: Функция доступна только для Premium пользователей.
"""

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple

from tender_sniper.ai_features import AIFeatureGate, has_ai_access, format_ai_feature_locked_message

logger = logging.getLogger(__name__)

# Кэш для резюме (in-memory, очищается при перезапуске)
_summary_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_DAYS = 7


class TenderSummarizer:
    """
    Создаёт краткое резюме тендера из длинного описания.

    Особенности:
    - Использует GPT-4o-mini (дёшево + качественно)
    - Кэширует результаты по MD5 хэшу текста
    - Имеет fallback на простое обрезание
    - Структурированный вывод на русском
    """

    MODEL = "gpt-4o-mini"  # Оптимальный баланс цены и качества
    MAX_INPUT_CHARS = 15000  # ~4k токенов входа
    MAX_OUTPUT_TOKENS = 500  # Ограничение выхода

    SYSTEM_PROMPT = """Ты эксперт по госзакупкам России. Создай краткое резюме тендера на русском языке.

Формат ответа (строго соблюдай):
📋 СУТЬ: [1 предложение - что закупают]
💰 БЮДЖЕТ: [сумма и условия оплаты если указаны]
📅 СРОКИ: [дедлайн подачи, срок исполнения]
⚠️ ТРЕБОВАНИЯ: [ключевые требования к участнику, лицензии, опыт]
🚩 РИСКИ: [потенциальные проблемы если есть, иначе "Не выявлены"]

Важно:
- Будь кратким, каждый пункт - 1-2 предложения
- Выделяй только важную для участника информацию
- Если информация отсутствует - пиши "Не указано"
- Не придумывай информацию"""

    def __init__(self, api_key: Optional[str] = None):
        """
        Инициализация summarizer.

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

    def _get_cache_key(self, text: str) -> str:
        """Генерирует ключ кэша по MD5 хэшу текста."""
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    def _get_from_cache(self, cache_key: str) -> Optional[str]:
        """Получает резюме из кэша если не истёк TTL."""
        if cache_key in _summary_cache:
            entry = _summary_cache[cache_key]
            if datetime.now() - entry['created_at'] < timedelta(days=CACHE_TTL_DAYS):
                logger.debug(f"📦 Резюме из кэша: {cache_key[:8]}...")
                return entry['summary']
            else:
                # Удаляем устаревшую запись
                del _summary_cache[cache_key]
        return None

    def _save_to_cache(self, cache_key: str, summary: str):
        """Сохраняет резюме в кэш."""
        _summary_cache[cache_key] = {
            'summary': summary,
            'created_at': datetime.now()
        }
        # Очистка старых записей (держим не более 1000)
        if len(_summary_cache) > 1000:
            oldest_keys = sorted(
                _summary_cache.keys(),
                key=lambda k: _summary_cache[k]['created_at']
            )[:100]
            for key in oldest_keys:
                del _summary_cache[key]

    def _create_fallback_summary(self, tender_data: Dict[str, Any]) -> str:
        """
        Создаёт простое резюме без AI (fallback).

        Args:
            tender_data: Данные тендера

        Returns:
            Простое текстовое резюме
        """
        name = tender_data.get('name', 'Без названия')
        price = tender_data.get('price')
        deadline = tender_data.get('submission_deadline') or tender_data.get('deadline')
        customer = tender_data.get('customer') or tender_data.get('customer_name', 'Не указан')

        # Обрезаем название если слишком длинное
        if len(name) > 200:
            name = name[:200] + '...'

        price_str = f"{price:,.0f} ₽".replace(',', ' ') if price else "Не указана"

        return f"""📋 СУТЬ: {name}
💰 БЮДЖЕТ: {price_str}
📅 СРОКИ: {deadline or 'Не указаны'}
⚠️ ТРЕБОВАНИЯ: См. документацию тендера
🚩 РИСКИ: Требуется детальный анализ"""

    async def summarize(
        self,
        tender_text: str,
        tender_data: Optional[Dict[str, Any]] = None,
        subscription_tier: str = 'trial'
    ) -> Tuple[str, bool]:
        """
        Создаёт AI-резюме тендера.

        Args:
            tender_text: Полный текст описания тендера
            tender_data: Дополнительные данные тендера (цена, сроки и т.д.)
            subscription_tier: Тариф пользователя (trial, basic, premium)

        Returns:
            Tuple[str, bool]: (резюме, is_ai_generated)
            - Если premium: полное AI-резюме
            - Если не premium: сообщение о необходимости upgrade
        """
        tender_data = tender_data or {}

        # Проверяем доступ к AI функции
        gate = AIFeatureGate(subscription_tier)
        if not gate.can_use('summarization'):
            logger.info(f"AI summarization denied for tier: {subscription_tier}")
            return (format_ai_feature_locked_message('summarization'), False)

        # Проверяем кэш
        cache_key = self._get_cache_key(tender_text)
        cached = self._get_from_cache(cache_key)
        if cached:
            return (cached, True)

        # Если нет API ключа или клиента - используем fallback
        if not self.api_key or not self.client:
            logger.warning("OpenAI API недоступен, используем fallback")
            return (self._create_fallback_summary(tender_data), False)

        # Обрезаем текст если слишком длинный
        if len(tender_text) > self.MAX_INPUT_CHARS:
            tender_text = tender_text[:self.MAX_INPUT_CHARS] + "\n\n[Текст обрезан...]"

        # Добавляем контекст из tender_data
        context_parts = []
        if tender_data.get('price'):
            context_parts.append(f"Начальная цена: {tender_data['price']:,.0f} ₽")
        if tender_data.get('submission_deadline'):
            context_parts.append(f"Срок подачи: {tender_data['submission_deadline']}")
        if tender_data.get('customer'):
            context_parts.append(f"Заказчик: {tender_data['customer']}")

        context = "\n".join(context_parts) if context_parts else ""

        user_message = f"""Проанализируй этот тендер и создай краткое резюме:

{context}

ОПИСАНИЕ ТЕНДЕРА:
{tender_text}"""

        try:
            response = await self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                max_tokens=self.MAX_OUTPUT_TOKENS,
                temperature=0.3  # Низкая температура для стабильности
            )

            summary = response.choices[0].message.content.strip()

            # Кэшируем результат
            self._save_to_cache(cache_key, summary)

            logger.info(f"✅ AI-резюме создано ({len(summary)} символов)")
            return (summary, True)

        except Exception as e:
            logger.error(f"❌ Ошибка AI-суммаризации: {e}")
            return (self._create_fallback_summary(tender_data), False)

    async def summarize_for_digest(
        self,
        tenders: list,
        max_tenders: int = 3
    ) -> str:
        """
        Создаёт краткие резюме для утреннего дайджеста.

        Args:
            tenders: Список тендеров (топ по score)
            max_tenders: Максимум тендеров для резюме

        Returns:
            Текст с резюме топ-тендеров
        """
        if not tenders:
            return "Нет новых тендеров для резюме."

        summaries = []
        for i, tender in enumerate(tenders[:max_tenders], 1):
            name = tender.get('name', 'Без названия')
            price = tender.get('price')
            score = tender.get('score', 0)

            price_str = f"{price:,.0f} ₽".replace(',', ' ') if price else "Цена не указана"

            # Короткое название
            short_name = name[:100] + '...' if len(name) > 100 else name

            summaries.append(
                f"<b>{i}. {short_name}</b>\n"
                f"   💰 {price_str} | 📊 Релевантность: {score}%"
            )

        return "\n\n".join(summaries)


# Singleton instance
_summarizer_instance: Optional[TenderSummarizer] = None


def get_summarizer() -> TenderSummarizer:
    """Получить singleton экземпляр summarizer."""
    global _summarizer_instance
    if _summarizer_instance is None:
        _summarizer_instance = TenderSummarizer()
    return _summarizer_instance


async def summarize_tender(
    tender_text: str,
    tender_data: Dict[str, Any] = None,
    subscription_tier: str = 'trial'
) -> Tuple[str, bool]:
    """
    Удобная функция для получения резюме тендера.

    Args:
        tender_text: Текст описания тендера
        tender_data: Дополнительные данные тендера
        subscription_tier: Тариф пользователя (trial, basic, premium)

    Returns:
        Tuple[str, bool]: (резюме, is_ai_generated)
    """
    summarizer = get_summarizer()
    return await summarizer.summarize(tender_text, tender_data, subscription_tier)


# ============================================
# ПРИМЕР ИСПОЛЬЗОВАНИЯ
# ============================================

async def example_usage():
    """Пример использования AI Summarizer."""
    # Пример текста тендера
    tender_text = """
    Поставка серверного оборудования для нужд Министерства цифрового развития.

    Требуется поставка:
    - Сервер Dell PowerEdge R750 - 10 шт.
    - Система хранения данных Dell EMC PowerStore - 2 шт.
    - Источники бесперебойного питания APC Smart-UPS - 12 шт.

    Требования к участнику:
    - Опыт поставок аналогичного оборудования не менее 3 лет
    - Наличие авторизованного сервисного центра
    - Гарантийное обслуживание 36 месяцев

    Срок поставки: 60 календарных дней
    Место поставки: г. Москва
    """

    tender_data = {
        'price': 45000000,
        'submission_deadline': '15.02.2026',
        'customer': 'Министерство цифрового развития'
    }

    summarizer = TenderSummarizer()
    summary = await summarizer.summarize(tender_text, tender_data)
    print(summary)


if __name__ == '__main__':
    asyncio.run(example_usage())
