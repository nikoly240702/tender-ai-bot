"""
Анализатор критериев поиска тендеров.
Преобразует текстовое описание критериев в структурированные параметры через LLM.
"""

import json
from typing import Dict, Any, Optional

try:
    from ..analyzers.tender_analyzer import TenderAnalyzer
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from analyzers.tender_analyzer import TenderAnalyzer


class CriteriaAnalyzer:
    """Анализирует текстовые критерии поиска и извлекает параметры."""

    def __init__(self, tender_analyzer: TenderAnalyzer):
        """
        Инициализация анализатора.

        Args:
            tender_analyzer: Экземпляр TenderAnalyzer для работы с LLM
        """
        self.analyzer = tender_analyzer

    def parse_criteria(self, criteria_text: str) -> Dict[str, Any]:
        """
        Преобразует текстовое описание критериев в структурированные параметры.

        Args:
            criteria_text: Текстовое описание критериев поиска

        Returns:
            Словарь с параметрами поиска:
            {
                'keywords': str,
                'price_min': int,
                'price_max': int,
                'regions': list[str],
                'product_categories': list[str]
            }
        """
        system_prompt = """Ты — эксперт по государственным закупкам РФ, который помогает извлекать критерии поиска тендеров."""

        user_prompt = f"""# ЗАДАЧА
Проанализируй текстовое описание критериев поиска тендеров и извлеки структурированные параметры.

# ТЕКСТОВЫЕ КРИТЕРИИ:
{criteria_text}

# ЧТО НУЖНО ИЗВЛЕЧЬ:

1. **Ключевые слова для поиска** - главные термины, описывающие товары/услуги
   - Примеры: "компьютерное оборудование", "видеокарты", "канцелярские товары"
   - Должны быть достаточно широкими для поиска

2. **Ценовой диапазон**:
   - price_min: минимальная цена контракта в рублях (null если не указано)
   - price_max: максимальная цена контракта в рублях (null если не указано)

3. **Регионы**:
   - Список регионов РФ (города, области)
   - Примеры: ["Москва", "Московская область", "Санкт-Петербург"]
   - Если "вся Россия" или не указано - пустой массив []

4. **Категории товаров/услуг**:
   - Детализированный список того, что ищем
   - Примеры: ["Видеокарты NVIDIA", "Компьютерные мониторы", "Периферийное оборудование"]

# ФОРМАТ ОТВЕТА (JSON):

{{
    "keywords": "основные ключевые слова для поиска",
    "price_min": числовое_значение_или_null,
    "price_max": числовое_значение_или_null,
    "regions": ["регион1", "регион2"] или [],
    "product_categories": ["категория1", "категория2"],
    "search_intent": "краткое описание цели поиска для дальнейшего анализа релевантности"
}}

ВАЖНО:
- Верни ТОЛЬКО валидный JSON
- Если параметр не указан явно - используй разумные значения или null
- Ключевые слова должны быть на русском языке
- Регионы указывать полными названиями

Верни ТОЛЬКО JSON без дополнительного текста."""

        try:
            # Используем быструю модель для простой задачи
            response_text = self.analyzer._make_api_call(
                system_prompt,
                user_prompt,
                response_format="json",
                use_premium=False
            )

            criteria = json.loads(response_text)
            return criteria

        except Exception as e:
            print(f"✗ Ошибка анализа критериев: {e}")
            # Возвращаем базовые критерии
            return {
                'keywords': criteria_text[:100],
                'price_min': None,
                'price_max': None,
                'regions': [],
                'product_categories': [],
                'search_intent': criteria_text
            }

    def analyze_relevance(
        self,
        tender: Dict[str, Any],
        criteria: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Оценивает релевантность найденного тендера критериям поиска.

        Args:
            tender: Данные о тендере
            criteria: Критерии поиска

        Returns:
            Оценка релевантности:
            {
                'score': int (0-100),
                'match_reasons': list[str],
                'concerns': list[str],
                'recommendation': str
            }
        """
        system_prompt = """Ты — эксперт по анализу государственных закупок, который оценивает соответствие тендеров критериям поиска."""

        tender_summary = f"""
Название: {tender.get('name', 'Н/Д')}
Цена: {tender.get('price_formatted', 'Н/Д')}
Заказчик: {tender.get('customer', 'Н/Д')}
Статус: {tender.get('status', 'Н/Д')}
"""

        user_prompt = f"""# ЗАДАЧА
Оцени, насколько найденный тендер соответствует критериям поиска компании.

# КРИТЕРИИ ПОИСКА:
{json.dumps(criteria, ensure_ascii=False, indent=2)}

# НАЙДЕННЫЙ ТЕНДЕР:
{tender_summary}

# ЧТО НУЖНО ОЦЕНИТЬ:

1. **Соответствие предмету закупки**:
   - Совпадает ли с искомыми товарами/услугами?
   - Релевантны ли ключевые слова?

2. **Ценовой диапазон**:
   - Находится ли цена в указанном диапазоне?
   - Адекватна ли цена для данного типа закупки?

3. **Географическое соответствие**:
   - Подходит ли регион заказчика?

4. **Общая привлекательность**:
   - Есть ли смысл участвовать в этом тендере?
   - Какие риски или преимущества?

# ФОРМАТ ОТВЕТА (JSON):

{{
    "score": числовая_оценка_от_0_до_100,
    "match_reasons": [
        "причина 1 почему тендер подходит",
        "причина 2 почему тендер подходит"
    ],
    "concerns": [
        "опасение 1 или несоответствие",
        "опасение 2 или несоответствие"
    ],
    "recommendation": "участвовать / изучить детально / пропустить",
    "summary": "Краткая сводка: стоит ли участвовать и почему (1-2 предложения)"
}}

Критерии оценки:
- 80-100: Отличное соответствие, определенно стоит участвовать
- 60-79: Хорошее соответствие, стоит изучить детально
- 40-59: Среднее соответствие, участие под вопросом
- 0-39: Слабое соответствие, лучше пропустить

ВАЖНО: Верни ТОЛЬКО валидный JSON."""

        try:
            # Используем быструю модель для оценки
            response_text = self.analyzer._make_api_call(
                system_prompt,
                user_prompt,
                response_format="json",
                use_premium=False
            )

            relevance = json.loads(response_text)
            return relevance

        except Exception as e:
            print(f"✗ Ошибка анализа релевантности: {e}")
            return {
                'score': 50,
                'match_reasons': ['Автоматический анализ недоступен'],
                'concerns': ['Требуется ручная проверка'],
                'recommendation': 'изучить детально',
                'summary': 'Требуется ручная проверка соответствия критериям'
            }


def main():
    """Пример использования анализатора критериев."""
    from analyzers.llm_adapter import LLMFactory

    # Создаем анализатор
    llm = LLMFactory.create(provider='openai', api_key='test', model='gpt-4o-mini')
    analyzer = CriteriaAnalyzer(llm)

    # Тестовые критерии
    criteria_text = """
    Ищу тендеры на поставку компьютерного оборудования в Москве и Московской области.
    Интересуют видеокарты, компьютеры, серверное оборудование.
    Цена от 500 тысяч до 5 миллионов рублей.
    """

    print("Анализ критериев...")
    criteria = analyzer.parse_criteria(criteria_text)
    print(json.dumps(criteria, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
