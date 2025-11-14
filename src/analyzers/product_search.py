"""
Модуль для поиска товаров и определения рыночных цен.
Использует LLM и веб-поиск для определения себестоимости товаров.
"""

import json
import re
from typing import Dict, Any, List, Optional
from analyzers.web_product_finder import WebProductFinder


class ProductSearcher:
    """Класс для поиска товаров и определения их стоимости."""

    def __init__(self, llm_adapter, enable_web_search=True):
        """
        Инициализация поисковика товаров.

        Args:
            llm_adapter: Адаптер для работы с LLM (для веб-поиска)
            enable_web_search: Включить ли реальный веб-поиск товаров
        """
        self.llm = llm_adapter
        self.enable_web_search = enable_web_search
        if enable_web_search:
            self.web_finder = WebProductFinder(llm_adapter)

    def extract_product_specs(self, documentation: str) -> List[Dict[str, Any]]:
        """
        Извлекает спецификации товаров из документации.

        Args:
            documentation: Текст тендерной документации

        Returns:
            Список товаров со спецификациями
        """
        system_prompt = """Ты — эксперт по извлечению технических характеристик товаров из тендерной документации."""

        user_prompt = f"""# ЗАДАЧА
Извлеки из тендерной документации ТОЧНЫЕ спецификации товаров, которые нужно поставить.

# ДОКУМЕНТАЦИЯ
{documentation[:50000]}

# ЧТО НУЖНО НАЙТИ
Для каждого товара найди:
1. Наименование товара
2. Количество
3. Технические характеристики (все упомянутые параметры)
4. Единицу измерения

# ВАЖНО
- Ищи в приложениях, технических заданиях, спецификациях
- Обращай внимание на слова: "Приложение", "Перечень", "Спецификация", "Характеристики"
- Извлекай ВСЕ упомянутые параметры (объем памяти, частота, размеры, версии и т.д.)

# РЕЗУЛЬТАТ
Верни JSON массив товаров:

[
    {{
        "name": "Точное наименование товара",
        "quantity": числовое_значение,
        "unit": "штука|литр|метр|...",
        "specifications": {{
            "параметр1": "значение1",
            "параметр2": "значение2"
        }},
        "raw_description": "Полное текстовое описание из документации"
    }}
]

Если товары не найдены - верни пустой массив []
Верни ТОЛЬКО JSON, без markdown разметки."""

        try:
            response = self.llm.generate(system_prompt, user_prompt)

            # Очистка от markdown
            response = response.strip()
            if response.startswith('```json'):
                response = response[7:]
            if response.startswith('```'):
                response = response[3:]
            if response.endswith('```'):
                response = response[:-3]
            response = response.strip()

            # Парсинг JSON
            products = json.loads(response)
            if not isinstance(products, list):
                return []

            return products

        except Exception as e:
            print(f"Ошибка извлечения спецификаций: {e}")
            return []

    def search_product_price(self, product: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Ищет товар в интернете и определяет примерную рыночную цену.

        Args:
            product: Словарь со спецификациями товара

        Returns:
            Словарь с результатами поиска и ценой
        """
        system_prompt = """Ты — эксперт по поиску и оценке стоимости товаров на российском рынке."""

        specs_text = json.dumps(product.get('specifications', {}), ensure_ascii=False)

        user_prompt = f"""# ЗАДАЧА
Определи примерную РЫНОЧНУЮ ЦЕНУ товара для закупки оптом (не розничную цену!).

# ТОВАР
Наименование: {product.get('name', 'Не указано')}
Количество: {product.get('quantity', 1)} {product.get('unit', 'шт')}

Характеристики:
{specs_text}

Описание:
{product.get('raw_description', '')}

# АНАЛИЗ
1. Определи конкретную категорию товара и типичных производителей
2. На основе характеристик определи примерный класс товара (бюджетный/средний/премиум)
3. Оцени ОПТОВУЮ цену за единицу на российском рынке (не розничную!)
4. Учти, что для государственных закупок обычно закупают средний сегмент
5. Укажи диапазон цен и наиболее вероятную цену

# РЕЗУЛЬТАТ
Верни JSON:

{{
    "product_category": "Категория товара",
    "market_segment": "бюджетный|средний|премиум",
    "typical_manufacturers": ["Производитель 1", "Производитель 2"],
    "price_range": {{
        "min": минимальная_цена_за_единицу,
        "max": максимальная_цена_за_единицу,
        "typical": типичная_цена_за_единицу
    }},
    "total_cost_estimate": типичная_цена * количество,
    "confidence": "high|medium|low",
    "reasoning": "Краткое объяснение оценки цены"
}}

ВАЖНО:
- Указывай ОПТОВЫЕ цены (на 10-30% ниже розничных)
- Цены в рублях
- Будь реалистичным - проверяй на соответствие рынку 2025 года
- Верни ТОЛЬКО JSON без markdown"""

        try:
            response = self.llm.generate(system_prompt, user_prompt)

            # Очистка от markdown
            response = response.strip()
            if response.startswith('```json'):
                response = response[7:]
            if response.startswith('```'):
                response = response[3:]
            if response.endswith('```'):
                response = response[:-3]
            response = response.strip()

            # Парсинг JSON
            price_info = json.loads(response)

            return price_info

        except Exception as e:
            print(f"Ошибка поиска цены: {e}")
            return None

    def estimate_procurement_cost(
        self,
        documentation: str,
        nmck: float
    ) -> Dict[str, Any]:
        """
        Полный расчет себестоимости закупки товаров.

        Args:
            documentation: Текст документации
            nmck: Начальная максимальная цена контракта

        Returns:
            Словарь с детальным расчетом
        """
        # Извлекаем товары
        products = self.extract_product_specs(documentation)

        if not products:
            return {
                'products_found': False,
                'estimated_cost': nmck * 0.90,  # Дефолт: 90% от НМЦК
                'margin_percent': 10.0,
                'confidence': 'low',
                'message': 'Товары не найдены в документации, используется упрощенная оценка'
            }

        # Ищем цены для каждого товара
        total_cost = 0
        products_with_prices = []

        for product in products:
            price_info = self.search_product_price(product)

            # Веб-поиск реальных предложений (если включен)
            web_search_results = None
            if self.enable_web_search and hasattr(self, 'web_finder'):
                try:
                    web_search_results = self.web_finder.find_product_links(product)
                except Exception as e:
                    print(f"  ⚠️  Ошибка веб-поиска: {e}")
                    web_search_results = None

            if price_info:
                product_cost = price_info.get('total_cost_estimate', 0)
                total_cost += product_cost

                products_with_prices.append({
                    'product': product,
                    'price_info': price_info,
                    'web_search': web_search_results
                })
            else:
                # Если цену не нашли - оцениваем пропорционально
                quantity = product.get('quantity', 1)
                estimated_unit_price = (nmck * 0.90) / len(products) / quantity
                estimated_cost = estimated_unit_price * quantity
                total_cost += estimated_cost

                products_with_prices.append({
                    'product': product,
                    'price_info': {
                        'total_cost_estimate': estimated_cost,
                        'confidence': 'low',
                        'reasoning': 'Цена не найдена, использована пропорциональная оценка'
                    },
                    'web_search': web_search_results
                })

        # Добавляем расходы на логистику и обеспечения
        logistics_cost = total_cost * 0.03  # 3% на логистику
        overhead = total_cost * 0.02  # 2% накладные

        total_estimated_cost = total_cost + logistics_cost + overhead

        # Расчет маржи
        margin_amount = nmck - total_estimated_cost
        margin_percent = (margin_amount / nmck * 100) if nmck > 0 else 0

        # Определяем уровень уверенности
        high_confidence_products = sum(
            1 for p in products_with_prices
            if p['price_info'].get('confidence') == 'high'
        )

        if high_confidence_products == len(products_with_prices):
            confidence = 'high'
        elif high_confidence_products >= len(products_with_prices) / 2:
            confidence = 'medium'
        else:
            confidence = 'low'

        return {
            'products_found': True,
            'products_count': len(products),
            'products_with_prices': products_with_prices,
            'cost_breakdown': {
                'products_cost': total_cost,
                'logistics': logistics_cost,
                'overhead': overhead,
                'total': total_estimated_cost
            },
            'estimated_cost': total_estimated_cost,
            'margin_amount': margin_amount,
            'margin_percent': margin_percent,
            'confidence': confidence,
            'nmck': nmck
        }


if __name__ == "__main__":
    # Пример использования
    print("Модуль поиска товаров создан успешно!")
