"""
Query Expander - AI расширение пользовательских критериев поиска.

Использует OpenAI для генерации синонимов, связанных терминов и вариаций
пользовательских ключевых слов.
"""

import os
from typing import List, Dict, Any
from openai import OpenAI
import logging

logger = logging.getLogger(__name__)


class QueryExpander:
    """Расширитель поисковых запросов через AI."""

    def __init__(self, api_key: str = None):
        """
        Инициализация расширителя.

        Args:
            api_key: OpenAI API ключ (опционально, читает из env)
        """
        self.api_key = api_key or os.getenv('OPENAI_API_KEY')
        self.client = OpenAI(api_key=self.api_key)

    async def expand_keywords(
        self,
        keywords: List[str],
        context: str = None
    ) -> Dict[str, Any]:
        """
        Расширяет список ключевых слов через AI.

        Args:
            keywords: Исходные ключевые слова от пользователя
            context: Дополнительный контекст (тип товара, регион и т.д.)

        Returns:
            Dict с расширенными критериями:
            {
                'original_keywords': [...],
                'expanded_keywords': [...],
                'synonyms': [...],
                'related_terms': [...],
                'search_query': 'расширенный запрос'
            }
        """
        logger.info(f"🔍 Расширение запроса: {keywords}")

        # Формируем промпт для OpenAI
        prompt = self._build_expansion_prompt(keywords, context)

        try:
            # Запрос к OpenAI
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",  # Быстрая и дешевая модель
                messages=[{
                    "role": "user",
                    "content": prompt
                }],
                temperature=0.3,
                max_tokens=1000
            )

            # Парсим ответ
            expanded = self._parse_expansion_response(response.choices[0].message.content)
            expanded['original_keywords'] = keywords

            logger.info(f"✅ Запрос расширен: {len(expanded['expanded_keywords'])} терминов")
            return expanded

        except Exception as e:
            logger.error(f"❌ Ошибка расширения запроса: {e}")
            # Fallback - возвращаем исходные ключевые слова
            return {
                'original_keywords': keywords,
                'expanded_keywords': keywords,
                'synonyms': [],
                'related_terms': [],
                'search_query': ' '.join(keywords)
            }

    def _build_expansion_prompt(self, keywords: List[str], context: str = None) -> str:
        """Формирует промпт для AI расширения."""
        keywords_str = ', '.join(keywords)

        prompt = f"""Ты - эксперт по государственным закупкам zakupki.gov.ru.

Пользователь ищет тендеры по следующим критериям:
Ключевые слова: {keywords_str}
"""

        if context:
            prompt += f"Контекст: {context}\n"

        prompt += """
Твоя задача - расширить эти критерии для более ТОЧНОГО поиска тендеров.

ВАЖНО: Если запрос содержит узкоспециализированные технические термины (Linux, Active Directory,
двухфакторная аутентификация, LDAP, VPN, и т.п.) - генерируй ТОЛЬКО близкие синонимы и
альтернативные названия ЭТИХ КОНКРЕТНЫХ технологий. НЕ расширяй до общих категорий!

Сгенерируй:
1. SYNONYMS - ТОЛЬКО прямые синонимы и альтернативные названия (3-7 вариантов)
   - Для "Linux" → "Линукс", "Ubuntu", "Astra Linux", "Альт Линукс" (НЕ "программное обеспечение"!)
   - Для "Active Directory" → "AD", "AD DS", "служба каталогов", "LDAP" (НЕ "IT оборудование"!)
   - Для "2FA" → "двухфакторная аутентификация", "MFA", "многофакторная авторизация"

2. RELATED_TERMS - ТОЛЬКО технологии, которые НАПРЯМУЮ связаны (2-4 варианта)
   - НЕ включай общие категории типа "IT оборудование", "программное обеспечение", "антивирус"
   - Включай только конкретные связанные технологии

3. SEARCH_QUERY - оптимальный поисковый запрос

Формат ответа (СТРОГО JSON):
{
  "synonyms": ["синоним1", "синоним2", ...],
  "related_terms": ["термин1", "термин2", ...],
  "search_query": "оптимизированный запрос"
}

КРИТИЧЕСКИ ВАЖНО:
- Для узкоспециализированных запросов - генерируй МИНИМУМ терминов, только самые точные
- НЕ добавляй общие категории типа "IT", "ПО", "оборудование", "техподдержка"
- Если не уверен в синониме - лучше не добавляй его
- Лучше меньше, но точнее!
"""

        return prompt

    def _parse_expansion_response(self, response_text: str) -> Dict[str, Any]:
        """Парсит ответ AI в структурированный формат."""
        import json
        import re

        try:
            # Ищем JSON в ответе
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                data = json.loads(json_match.group(0))

                # Объединяем все расширенные термины
                expanded = []
                expanded.extend(data.get('synonyms', []))
                expanded.extend(data.get('related_terms', []))

                return {
                    'expanded_keywords': list(set(expanded)),  # Уникальные
                    'synonyms': data.get('synonyms', []),
                    'related_terms': data.get('related_terms', []),
                    'search_query': data.get('search_query', '')
                }

        except Exception as e:
            logger.warning(f"⚠️ Ошибка парсинга ответа AI: {e}")

        # Fallback
        return {
            'expanded_keywords': [],
            'synonyms': [],
            'related_terms': [],
            'search_query': response_text[:200]
        }


# Синхронная обертка для использования в боте
def expand_keywords_sync(keywords: List[str], context: str = None) -> Dict[str, Any]:
    """
    Синхронная версия для использования в aiogram handlers.

    Args:
        keywords: Исходные ключевые слова
        context: Дополнительный контекст

    Returns:
        Расширенные критерии
    """
    import asyncio

    expander = QueryExpander()

    # Запускаем в event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(expander.expand_keywords(keywords, context))
