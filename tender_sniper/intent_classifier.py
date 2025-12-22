"""
GPT Intent Classifier.

Классификация намерения пользователя для оптимизации поиска:
- EXACT: Точный поиск (конкретный артикул, бренд, модель)
- BROAD: Широкий поиск (категория товаров, общие термины)
- HYBRID: Комбинированный поиск (категория + бренд)

Feature flag: intent_classifier (config/features.yaml)
"""

import os
import logging
import json
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class SearchIntent(Enum):
    """Search intent types."""
    EXACT = "exact"        # Точный поиск: конкретный артикул, модель
    BROAD = "broad"        # Широкий поиск: категория товаров
    HYBRID = "hybrid"      # Комбинированный: категория + бренд


@dataclass
class ClassificationResult:
    """Result of intent classification."""
    intent: SearchIntent
    confidence: float
    keywords: List[str]           # Extracted keywords
    brands: List[str]             # Detected brands
    categories: List[str]         # Detected categories
    suggested_synonyms: List[str] # Suggested synonyms to add
    recommended_strategy: str     # Search strategy recommendation


class IntentClassifier:
    """
    GPT-based intent classifier for search optimization.

    Uses GPT-4o-mini for fast, cost-effective classification.
    Falls back to rule-based classification if API unavailable.
    """

    def __init__(self, openai_api_key: str = None):
        """Initialize classifier."""
        self.api_key = openai_api_key or os.getenv('OPENAI_API_KEY', '')
        self._client = None

        if self.api_key:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(api_key=self.api_key)
                logger.info("✅ Intent Classifier initialized with OpenAI")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize OpenAI client: {e}")

    @property
    def is_available(self) -> bool:
        """Check if GPT classification is available."""
        return self._client is not None

    async def classify(self, query: str) -> ClassificationResult:
        """
        Classify user search intent.

        Args:
            query: User's search query (keywords)

        Returns:
            ClassificationResult with intent and metadata
        """
        if self._client:
            try:
                return await self._classify_with_gpt(query)
            except Exception as e:
                logger.warning(f"GPT classification failed, using fallback: {e}")
                return self._classify_with_rules(query)
        else:
            return self._classify_with_rules(query)

    async def _classify_with_gpt(self, query: str) -> ClassificationResult:
        """Classify using GPT-4o-mini."""
        prompt = f"""Проанализируй поисковый запрос для системы государственных закупок и определи намерение пользователя.

Запрос: "{query}"

Ответь строго в JSON формате:
{{
    "intent": "exact|broad|hybrid",
    "confidence": 0.0-1.0,
    "keywords": ["список", "ключевых", "слов"],
    "brands": ["обнаруженные", "бренды"],
    "categories": ["категории", "товаров"],
    "suggested_synonyms": ["синонимы", "для", "расширения"],
    "recommended_strategy": "описание стратегии поиска"
}}

Определения намерений:
- exact: Конкретный товар, артикул, модель (например: "Dell PowerEdge R740", "Atlas Copco GA75")
- broad: Категория товаров (например: "компьютеры", "строительные материалы")
- hybrid: Категория + бренд (например: "серверы HP", "ноутбуки Lenovo")

Правила:
1. Если есть конкретная модель или артикул → exact
2. Если только общие категории → broad
3. Если категория + бренд → hybrid
4. Добавь релевантные синонимы для расширения поиска
5. confidence должна отражать уверенность в классификации"""

        try:
            response = await self._client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Ты эксперт по классификации поисковых запросов для госзакупок."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=500
            )

            content = response.choices[0].message.content

            # Parse JSON response
            # Remove markdown code blocks if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            data = json.loads(content.strip())

            intent_map = {
                "exact": SearchIntent.EXACT,
                "broad": SearchIntent.BROAD,
                "hybrid": SearchIntent.HYBRID
            }

            return ClassificationResult(
                intent=intent_map.get(data.get("intent", "broad"), SearchIntent.BROAD),
                confidence=float(data.get("confidence", 0.5)),
                keywords=data.get("keywords", [query]),
                brands=data.get("brands", []),
                categories=data.get("categories", []),
                suggested_synonyms=data.get("suggested_synonyms", []),
                recommended_strategy=data.get("recommended_strategy", "")
            )

        except Exception as e:
            logger.error(f"GPT classification error: {e}")
            raise

    def _classify_with_rules(self, query: str) -> ClassificationResult:
        """Fallback rule-based classification."""
        query_lower = query.lower()
        words = query_lower.split()

        # Known brand patterns
        known_brands = [
            'dell', 'hp', 'hewlett', 'packard', 'lenovo', 'asus', 'acer',
            'cisco', 'juniper', 'huawei', 'mikrotik', 'ubiquiti',
            'atlas copco', 'komatsu', 'caterpillar', 'hitachi', 'volvo',
            'microsoft', 'oracle', 'sap', 'kaspersky', '1c', '1с',
            'bosch', 'makita', 'hilti', 'dewalt', 'metabo'
        ]

        # Category patterns
        category_words = [
            'компьютер', 'сервер', 'ноутбук', 'принтер', 'монитор',
            'оборудование', 'материалы', 'услуги', 'работы',
            'поставка', 'закупка', 'ремонт', 'обслуживание'
        ]

        # Check for brands
        found_brands = []
        for brand in known_brands:
            if brand in query_lower:
                found_brands.append(brand)

        # Check for categories
        found_categories = []
        for cat in category_words:
            if cat in query_lower:
                found_categories.append(cat)

        # Check for model numbers (alphanumeric patterns)
        has_model = any(
            any(c.isdigit() for c in word) and len(word) > 3
            for word in words
        )

        # Determine intent
        if has_model and len(words) <= 3:
            intent = SearchIntent.EXACT
            confidence = 0.8
            strategy = "Точный поиск по модели/артикулу. Используйте точное соответствие."
        elif found_brands and found_categories:
            intent = SearchIntent.HYBRID
            confidence = 0.75
            strategy = "Комбинированный поиск: бренд + категория. Расширьте синонимами категории."
        elif found_brands and not found_categories:
            intent = SearchIntent.EXACT
            confidence = 0.7
            strategy = "Поиск по бренду. Добавьте типичные продукты этого бренда."
        else:
            intent = SearchIntent.BROAD
            confidence = 0.6
            strategy = "Широкий поиск по категории. Используйте морфологию и синонимы."

        return ClassificationResult(
            intent=intent,
            confidence=confidence,
            keywords=words,
            brands=found_brands,
            categories=found_categories,
            suggested_synonyms=self._get_rule_based_synonyms(words),
            recommended_strategy=strategy
        )

    def _get_rule_based_synonyms(self, words: List[str]) -> List[str]:
        """Get rule-based synonyms for keywords."""
        synonym_map = {
            'компьютер': ['пк', 'пэвм', 'эвм', 'рабочая станция'],
            'сервер': ['серверное оборудование', 'серверная платформа'],
            'ноутбук': ['портативный компьютер', 'лэптоп'],
            'принтер': ['мфу', 'печатающее устройство'],
            'монитор': ['дисплей', 'экран'],
            'ремонт': ['обслуживание', 'восстановление', 'модернизация'],
        }

        synonyms = []
        for word in words:
            if word in synonym_map:
                synonyms.extend(synonym_map[word])

        return synonyms


# Singleton instance
intent_classifier = IntentClassifier()


# Convenience function
async def classify_search_intent(query: str) -> ClassificationResult:
    """Classify search intent for a query."""
    return await intent_classifier.classify(query)


def get_search_strategy(intent: SearchIntent) -> Dict:
    """
    Get search strategy based on intent.

    Returns configuration for search engine.
    """
    strategies = {
        SearchIntent.EXACT: {
            'use_morphology': False,
            'use_synonyms': False,
            'use_ai_expansion': False,
            'fuzzy_matching': False,
            'min_score': 70,
            'description': 'Точный поиск без расширения'
        },
        SearchIntent.BROAD: {
            'use_morphology': True,
            'use_synonyms': True,
            'use_ai_expansion': True,
            'fuzzy_matching': True,
            'min_score': 50,
            'description': 'Широкий поиск с морфологией и синонимами'
        },
        SearchIntent.HYBRID: {
            'use_morphology': True,
            'use_synonyms': True,
            'use_ai_expansion': False,
            'fuzzy_matching': True,
            'min_score': 60,
            'description': 'Комбинированный поиск: точный бренд + расширенная категория'
        }
    }

    return strategies.get(intent, strategies[SearchIntent.BROAD])
