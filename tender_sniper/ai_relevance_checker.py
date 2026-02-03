"""
AI Relevance Checker - строгая проверка семантической релевантности тендеров.

Принцип: Лучше пропустить хороший тендер, чем показать нерелевантный.
Каждый нерелевантный тендер = потеря доверия пользователя.
"""

import os
import json
import hashlib
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from openai import OpenAI

logger = logging.getLogger(__name__)


class AIRelevanceChecker:
    """
    Строгий AI-проверщик релевантности тендеров.

    Использует консервативный подход:
    - При любых сомнениях — отклоняет
    - Требует высокую уверенность для одобрения
    - Объясняет причину решения
    """

    # Модель для проверки (быстрая и дешёвая)
    MODEL = "gpt-4o-mini"

    # Пороги уверенности
    CONFIDENCE_THRESHOLD_ACCEPT = 85  # Минимум для одобрения
    CONFIDENCE_THRESHOLD_RECHECK = 70  # Ниже этого — точно отклоняем

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
        subscription_tier: str = 'trial'
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
                filter_keywords
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
        filter_keywords: List[str]
    ) -> Dict[str, Any]:
        """Выполняет AI запрос для проверки релевантности."""

        description_text = f"\nОписание: {tender_description[:500]}" if tender_description else ""

        prompt = f"""Ты эксперт по госзакупкам с 10-летним опытом. Твоя репутация зависит от качества рекомендаций.

ЗАДАЧА: Определи, релевантен ли тендер запросу пользователя.

ЗАПРОС ПОЛЬЗОВАТЕЛЯ:
{filter_intent}

Ключевые слова: {', '.join(filter_keywords)}

ТЕНДЕР:
Название: "{tender_name}"{description_text}

КРИТЕРИЙ ОЦЕНКИ:
Представь, что клиент платит тебе $100/час за консультации по тендерам.
Ты бы порекомендовал ему этот тендер как соответствующий его запросу?

ВАЖНО - СТРОГИЕ ПРАВИЛА:
- "разработка" НЕ означает IT, если это: проектная документация, охранные зоны, месторождения, нормативы
- "система" НЕ означает IT: пожарная, отопления, охраны, видеонаблюдения, водоснабжения
- "обслуживание" и "сопровождение" систем — это НЕ разработка ПО
- "техническое обслуживание" — это ВСЕГДА не про разработку, даже если касается IT-систем
- "видеонаблюдение", "СКУД", "охрана" — это физическая безопасность, НЕ IT-разработка
- "транспортировка" может быть нерелевантна логистике (отходы, биоматериалы)
- Если тендер про ОБСЛУЖИВАНИЕ/РЕМОНТ/ТЕХПОДДЕРЖКУ — это НЕ разработка
- Если есть ЛЮБЫЕ сомнения — отвечай "не релевантен"

Ответь СТРОГО в формате JSON:
{{"relevant": true/false, "confidence": 0-100, "reason": "краткое объяснение на русском"}}"""

        response = self.client.chat.completions.create(
            model=self.MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,  # Низкая температура для консистентности
            max_tokens=150
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
    subscription_tier: str = 'trial'
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
        subscription_tier=subscription_tier
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
