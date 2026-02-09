"""
AI Relevance Checker - сбалансированная проверка семантической релевантности тендеров.

Принцип: Пропускать пограничные тендеры, но отсекать явно нерелевантные.
AI проверяет: 1) соответствие типа (товар/услуга), 2) тематическую релевантность.
"""

import os
import json
import hashlib
import logging
import asyncio
import functools
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from openai import OpenAI

logger = logging.getLogger(__name__)


class AIRelevanceChecker:
    """
    Мягкий AI-проверщик релевантности тендеров.

    Использует разрешающий подход:
    - При сомнениях — одобряет с низким confidence
    - Отклоняет только совершенно нерелевантные тендеры
    - Score используется для сортировки, не фильтрации
    """

    # Модель для проверки (быстрая и дешёвая)
    MODEL = "gpt-4o-mini"

    # Пороги уверенности (мягкие — score для сортировки, не фильтрации)
    CONFIDENCE_THRESHOLD_ACCEPT = 25  # Минимум для одобрения (мягкий)
    CONFIDENCE_THRESHOLD_RECHECK = 10  # Ниже этого — отклоняем (только совсем нерелевантные)

    # Кэш решений (in-memory, для production лучше Redis)
    _cache: Dict[str, Tuple[bool, int, str, datetime]] = {}
    _CACHE_TTL_HOURS = 24

    # Лимиты по тарифам (проверок в день)
    TIER_LIMITS = {
        'trial': 20,
        'basic': 100,
        'premium': 10000,  # Практически безлимит
        'admin': 100000,
    }

    # Счётчики использования (in-memory, для production — в БД)
    _usage_counters: Dict[int, Dict[str, Any]] = {}

    def __init__(self, api_key: str = None):
        """
        Инициализация проверщика.

        Args:
            api_key: OpenAI API ключ (опционально, читает из env)
        """
        self.api_key = api_key or os.getenv('OPENAI_API_KEY')
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key)
        else:
            self.client = None
            logger.warning("⚠️ OpenAI API key not found. AI checks disabled.")

    def _get_cache_key(self, tender_name: str, filter_intent: str) -> str:
        """Генерирует ключ кэша из названия тендера и intent фильтра."""
        content = f"{tender_name.lower().strip()}|{filter_intent.lower().strip()}"
        return hashlib.md5(content.encode()).hexdigest()

    def _get_from_cache(self, cache_key: str) -> Optional[Tuple[bool, int, str]]:
        """Получает решение из кэша если не истекло."""
        if cache_key in self._cache:
            is_relevant, confidence, reason, cached_at = self._cache[cache_key]
            if datetime.now() - cached_at < timedelta(hours=self._CACHE_TTL_HOURS):
                logger.debug(f"   🗄️ Cache hit: {cache_key[:8]}...")
                return (is_relevant, confidence, reason)
            else:
                # Истёк TTL
                del self._cache[cache_key]
        return None

    def _save_to_cache(self, cache_key: str, is_relevant: bool, confidence: int, reason: str):
        """Сохраняет решение в кэш."""
        self._cache[cache_key] = (is_relevant, confidence, reason, datetime.now())

        # Очистка старых записей (простая стратегия)
        if len(self._cache) > 10000:
            # Удаляем самые старые 20%
            sorted_keys = sorted(
                self._cache.keys(),
                key=lambda k: self._cache[k][3]
            )
            for key in sorted_keys[:2000]:
                del self._cache[key]

    def check_quota(self, user_id: int, subscription_tier: str) -> bool:
        """
        Проверяет, есть ли у пользователя квота на AI проверки.

        Args:
            user_id: ID пользователя
            subscription_tier: Тариф подписки

        Returns:
            True если квота есть, False если исчерпана
        """
        today = datetime.now().date().isoformat()

        if user_id not in self._usage_counters:
            self._usage_counters[user_id] = {'date': today, 'count': 0}

        counter = self._usage_counters[user_id]

        # Сброс счётчика в новый день
        if counter['date'] != today:
            counter['date'] = today
            counter['count'] = 0

        limit = self.TIER_LIMITS.get(subscription_tier, self.TIER_LIMITS['trial'])
        return counter['count'] < limit

    def increment_usage(self, user_id: int):
        """Увеличивает счётчик использования."""
        today = datetime.now().date().isoformat()

        if user_id not in self._usage_counters:
            self._usage_counters[user_id] = {'date': today, 'count': 0}

        counter = self._usage_counters[user_id]
        if counter['date'] != today:
            counter['date'] = today
            counter['count'] = 0

        counter['count'] += 1

    def get_usage_stats(self, user_id: int, subscription_tier: str) -> Dict[str, Any]:
        """Возвращает статистику использования."""
        today = datetime.now().date().isoformat()

        if user_id not in self._usage_counters or self._usage_counters[user_id]['date'] != today:
            used = 0
        else:
            used = self._usage_counters[user_id]['count']

        limit = self.TIER_LIMITS.get(subscription_tier, self.TIER_LIMITS['trial'])

        return {
            'used': used,
            'limit': limit,
            'remaining': max(0, limit - used),
            'tier': subscription_tier
        }

    async def generate_filter_intent(
        self,
        filter_name: str,
        keywords: List[str],
        exclude_keywords: List[str] = None
    ) -> str:
        """
        Генерирует детальное описание намерения фильтра.

        Вызывается один раз при создании/обновлении фильтра.
        Сохраняется в БД для последующих проверок.

        Args:
            filter_name: Название фильтра
            keywords: Ключевые слова
            exclude_keywords: Исключающие слова

        Returns:
            Детальное описание intent фильтра
        """
        if not self.client:
            # Fallback без AI
            return f"Поиск тендеров по теме: {filter_name}. Ключевые слова: {', '.join(keywords)}"

        exclude_str = f"\nИсключить: {', '.join(exclude_keywords)}" if exclude_keywords else ""

        prompt = f"""Ты эксперт по государственным закупкам России.

Пользователь создал фильтр для поиска тендеров:
- Название фильтра: "{filter_name}"
- Ключевые слова: {', '.join(keywords)}{exclude_str}

Твоя задача: Опиши ДЕТАЛЬНО, какие именно тендеры ищет пользователь.

Включи:
1. Основная сфера деятельности (IT, строительство, логистика, etc.)
2. Конкретные товары/услуги/работы
3. Что точно НЕ подходит (ложные срабатывания)

Формат ответа — связный текст 2-3 предложения, который поможет
определить, релевантен ли конкретный тендер этому запросу.

Пример для "разработка ПО":
"Пользователь ищет тендеры на разработку программного обеспечения,
включая создание сайтов, мобильных приложений, информационных систем,
автоматизацию бизнес-процессов. НЕ подходят: разработка проектной документации
на строительство, разработка месторождений, разработка охранных зон —
это другие отрасли несмотря на слово 'разработка'."

Напиши intent для данного фильтра:"""

        try:
            response = self.client.chat.completions.create(
                model=self.MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300
            )

            intent = response.choices[0].message.content.strip()
            logger.info(f"✅ Сгенерирован intent для фильтра '{filter_name}': {intent[:100]}...")
            return intent

        except Exception as e:
            logger.error(f"❌ Ошибка генерации intent: {e}")
            # Fallback
            return f"Поиск тендеров по теме: {filter_name}. Ключевые слова: {', '.join(keywords)}"

    async def check_relevance(
        self,
        tender_name: str,
        tender_description: str,
        filter_intent: str,
        filter_keywords: List[str],
        user_id: int = None,
        subscription_tier: str = 'trial',
        tender_types: List[str] = None
    ) -> Dict[str, Any]:
        """
        Проверяет семантическую релевантность тендера фильтру.

        Args:
            tender_name: Название тендера
            tender_description: Описание тендера (может быть пустым)
            filter_intent: Детальное описание намерения фильтра
            filter_keywords: Ключевые слова фильтра (для контекста)
            user_id: ID пользователя (для квоты)
            subscription_tier: Тариф подписки

        Returns:
            {
                'is_relevant': bool,
                'confidence': int (0-100),
                'reason': str,
                'source': 'ai' | 'cache' | 'fallback',
                'quota_remaining': int
            }
        """
        # Проверяем квоту
        if user_id and not self.check_quota(user_id, subscription_tier):
            logger.info(f"   ⚠️ Квота AI исчерпана для user {user_id} ({subscription_tier})")
            return {
                'is_relevant': True,  # При исчерпании квоты — пропускаем (fallback к keyword)
                'confidence': 50,
                'reason': 'Квота AI проверок исчерпана, используется keyword matching',
                'source': 'quota_exceeded',
                'quota_remaining': 0
            }

        # Проверяем кэш
        cache_key = self._get_cache_key(tender_name, filter_intent)
        cached = self._get_from_cache(cache_key)

        if cached:
            is_relevant, confidence, reason = cached
            remaining = self.get_usage_stats(user_id, subscription_tier)['remaining'] if user_id else -1
            return {
                'is_relevant': is_relevant,
                'confidence': confidence,
                'reason': reason,
                'source': 'cache',
                'quota_remaining': remaining
            }

        # Если нет API клиента — fallback
        if not self.client:
            return {
                'is_relevant': True,
                'confidence': 50,
                'reason': 'AI недоступен, используется keyword matching',
                'source': 'fallback',
                'quota_remaining': -1
            }

        # Делаем AI запрос
        try:
            result = await self._call_ai_check(
                tender_name,
                tender_description,
                filter_intent,
                filter_keywords,
                tender_types=tender_types
            )

            # Сохраняем в кэш
            self._save_to_cache(
                cache_key,
                result['is_relevant'],
                result['confidence'],
                result['reason']
            )

            # Увеличиваем счётчик использования
            if user_id:
                self.increment_usage(user_id)

            remaining = self.get_usage_stats(user_id, subscription_tier)['remaining'] if user_id else -1
            result['source'] = 'ai'
            result['quota_remaining'] = remaining

            return result

        except Exception as e:
            logger.error(f"❌ Ошибка AI проверки: {e}")
            return {
                'is_relevant': True,  # При ошибке — пропускаем (лучше показать, чем потерять)
                'confidence': 50,
                'reason': f'Ошибка AI: {str(e)[:50]}',
                'source': 'error',
                'quota_remaining': -1
            }

    async def _call_ai_check(
        self,
        tender_name: str,
        tender_description: str,
        filter_intent: str,
        filter_keywords: List[str],
        tender_types: List[str] = None
    ) -> Dict[str, Any]:
        """Выполняет AI запрос для проверки релевантности."""

        description_text = f"\nОписание: {tender_description[:500]}" if tender_description else ""

        # Формируем блок о типе закупки
        type_instruction = ""
        if tender_types:
            types_str = ', '.join(tender_types)
            if tender_types == ['товары']:
                type_instruction = f"""
ТИП ЗАКУПКИ: Пользователь ищет ТОЛЬКО товары (поставки).
КРИТИЧЕСКИ ВАЖНО: Если тендер — это УСЛУГА или РАБОТА (ремонт, обслуживание, консультирование,
разработка документации, оказание услуг, выполнение работ, сервисное обслуживание, техническое
обслуживание, монтаж, демонтаж, проектирование) — ОТКЛОНИ с confidence 5-10.
Товары = поставка физических предметов (оборудование, техника, материалы, запчасти)."""
            elif tender_types == ['услуги']:
                type_instruction = f"""
ТИП ЗАКУПКИ: Пользователь ищет ТОЛЬКО услуги.
Если тендер — это ТОВАР (поставка оборудования, материалов) — ОТКЛОНИ с confidence 5-10."""
            else:
                type_instruction = f"\nТИП ЗАКУПКИ: {types_str}"

        prompt = f"""Ты эксперт по госзакупкам. Определи, насколько тендер релевантен запросу пользователя.

ЗАПРОС ПОЛЬЗОВАТЕЛЯ:
{filter_intent}

Ключевые слова: {', '.join(filter_keywords)}
{type_instruction}
ТЕНДЕР:
Название: "{tender_name}"{description_text}

ПРИНЦИП ОЦЕНКИ:
1. Сначала проверь ТИП — если тип не совпадает (услуга вместо товара и наоборот), ОТКЛОНИ (confidence 5-10)
2. Затем проверь ТЕМУ — связан ли тендер с запросом по смыслу
3. При сомнениях по теме — скорее одобряй с confidence 30-50%
4. Отклоняй по теме (confidence 5-10) только если тема СОВЕРШЕННО другая

ПРИМЕРЫ ОТКЛОНЕНИЙ ПО ТИПУ (если фильтр ищет товары):
- "Услуга по ремонту офисной техники" → relevant=false, confidence=5, reason="услуга, не товар"
- "Техническое обслуживание компьютеров" → relevant=false, confidence=5, reason="услуга, не товар"
- "Выполнение работ по монтажу оборудования" → relevant=false, confidence=5, reason="работа, не товар"

ПРИМЕРЫ ОТКЛОНЕНИЙ ПО ТЕМЕ:
- "Автомобиль легковой HAVAL" при запросе "компьютеры" → relevant=false, confidence=3
- "Ремонт вооружения, военной техники" при запросе "компьютеры" → relevant=false, confidence=5

Ответь СТРОГО в формате JSON:
{{"relevant": true/false, "confidence": 0-100, "reason": "краткое объяснение на русском"}}"""

        # Синхронный OpenAI клиент — оборачиваем в executor чтобы не блокировать event loop
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            functools.partial(
                self.client.chat.completions.create,
                model=self.MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=150
            )
        )

        response_text = response.choices[0].message.content.strip()

        # Парсим JSON ответ
        try:
            # Ищем JSON в ответе
            import re
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                data = json.loads(json_match.group(0))

                is_relevant = data.get('relevant', False)
                confidence = int(data.get('confidence', 50))
                reason = data.get('reason', 'Нет объяснения')

                # Применяем строгие пороги
                if confidence < self.CONFIDENCE_THRESHOLD_ACCEPT:
                    is_relevant = False
                    if confidence >= self.CONFIDENCE_THRESHOLD_RECHECK:
                        reason = f"Недостаточная уверенность ({confidence}%): {reason}"

                logger.info(f"   🤖 AI: {'✅' if is_relevant else '❌'} ({confidence}%) {reason[:50]}...")

                return {
                    'is_relevant': is_relevant,
                    'confidence': confidence,
                    'reason': reason
                }

        except json.JSONDecodeError as e:
            logger.warning(f"   ⚠️ Не удалось распарсить AI ответ: {response_text[:100]}")

        # Fallback если не удалось распарсить
        return {
            'is_relevant': False,
            'confidence': 0,
            'reason': 'Не удалось определить релевантность'
        }

    async def check_relevance_batch(
        self,
        tenders: List[Dict[str, Any]],
        filter_intent: str,
        filter_keywords: List[str],
        user_id: int = None,
        subscription_tier: str = 'trial'
    ) -> List[Dict[str, Any]]:
        """
        Проверяет релевантность списка тендеров.

        Args:
            tenders: Список тендеров (каждый должен иметь 'name' и опционально 'description')
            filter_intent: Intent фильтра
            filter_keywords: Ключевые слова
            user_id: ID пользователя
            subscription_tier: Тариф

        Returns:
            Список результатов проверки (в том же порядке)
        """
        results = []

        for tender in tenders:
            result = await self.check_relevance(
                tender_name=tender.get('name', ''),
                tender_description=tender.get('description', ''),
                filter_intent=filter_intent,
                filter_keywords=filter_keywords,
                user_id=user_id,
                subscription_tier=subscription_tier
            )
            results.append(result)

            # Если квота исчерпана — остальные без AI проверки
            if result.get('source') == 'quota_exceeded':
                for _ in range(len(tenders) - len(results)):
                    results.append({
                        'is_relevant': True,
                        'confidence': 50,
                        'reason': 'Квота исчерпана',
                        'source': 'quota_exceeded',
                        'quota_remaining': 0
                    })
                break

        return results


# Глобальный экземпляр для использования в приложении
_checker_instance: Optional[AIRelevanceChecker] = None


def get_relevance_checker() -> AIRelevanceChecker:
    """Возвращает глобальный экземпляр AI checker."""
    global _checker_instance
    if _checker_instance is None:
        _checker_instance = AIRelevanceChecker()
    return _checker_instance


# Удобные функции для использования
async def check_tender_relevance(
    tender_name: str,
    filter_intent: str,
    filter_keywords: List[str],
    tender_description: str = "",
    user_id: int = None,
    subscription_tier: str = 'trial',
    tender_types: List[str] = None
) -> Dict[str, Any]:
    """
    Проверяет релевантность тендера (удобная обёртка).

    Returns:
        {'is_relevant': bool, 'confidence': int, 'reason': str, ...}
    """
    checker = get_relevance_checker()
    return await checker.check_relevance(
        tender_name=tender_name,
        tender_description=tender_description,
        filter_intent=filter_intent,
        filter_keywords=filter_keywords,
        user_id=user_id,
        subscription_tier=subscription_tier,
        tender_types=tender_types
    )


async def generate_intent(
    filter_name: str,
    keywords: List[str],
    exclude_keywords: List[str] = None
) -> str:
    """
    Генерирует intent для фильтра (удобная обёртка).

    Returns:
        Строка с описанием intent
    """
    checker = get_relevance_checker()
    return await checker.generate_filter_intent(
        filter_name=filter_name,
        keywords=keywords,
        exclude_keywords=exclude_keywords
    )
