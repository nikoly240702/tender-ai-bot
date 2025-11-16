"""
Многоэтапный анализатор тендеров с scoring system.
Решает проблему перегруженных промптов путём разбивки на 6 фокусированных этапов.
"""

import json
import time
from typing import Dict, Any, List, Optional
from .smart_document_processor import SmartDocumentTruncator


class MultiStageAnalyzer:
    """
    Многоэтапный анализ тендерной документации.

    Проблема: Один большой промпт пытается сделать всё сразу → низкая точность
    Решение: 6 фокусированных этапов, каждый решает свою задачу
    """

    def __init__(self, llm_premium, llm_fast):
        """
        Args:
            llm_premium: LLM для критичных задач (этапы 2, 3, 5)
            llm_fast: LLM для простых задач (этапы 1, 4, 6)
        """
        self.llm_premium = llm_premium
        self.llm_fast = llm_fast
        self.truncator = SmartDocumentTruncator()
        self.token_usage = {
            'stage_1': 0,
            'stage_2': 0,
            'stage_3': 0,
            'stage_4': 0,
            'stage_5': 0,
            'stage_6': 0
        }

    def analyze_tender(
        self,
        documentation: str,
        company_profile: dict
    ) -> dict:
        """
        Полный 6-этапный анализ тендера.

        Этапы:
        1. Базовая информация (fast)
        2. Товары/услуги (premium)
        3. Финансовые условия (premium)
        4. Требования (fast)
        5. Scoring - оценка соответствия (premium) ← НОВОЕ!
        6. Риски и рекомендации (fast)

        Returns:
            Полный результат анализа с scoring
        """
        start_time = time.time()
        print("\n" + "="*70)
        print("  🚀 МНОГОЭТАПНЫЙ АНАЛИЗ ТЕНДЕРА")
        print("="*70)

        # Умная обрезка документации
        truncated_doc = self.truncator.smart_truncate(documentation, max_chars=50000)

        # ЭТАП 1: Базовая информация
        print("\n📍 Этап 1/6: Извлечение базовой информации...")
        basic_info = self._extract_basic_info(truncated_doc)
        print(f"   ✅ Название: {basic_info.get('name', 'Не найдено')[:60]}...")
        nmck = basic_info.get('nmck') or 0
        print(f"   ✅ НМЦК: {nmck:,.0f} ₽")

        # ЭТАП 2: Товары и услуги
        print("\n📦 Этап 2/6: Детальный анализ товаров/услуг...")
        products = self._extract_products_detailed(
            truncated_doc,
            hint=basic_info.get('tender_type')
        )
        print(f"   ✅ Найдено позиций: {len(products)}")

        # ЭТАП 3: Финансовые условия
        print("\n💰 Этап 3/6: Анализ финансовых условий...")
        financial = self._analyze_financial_terms(truncated_doc, basic_info.get('nmck'))
        if financial.get('payment_terms'):
            print(f"   ✅ Срок оплаты: {financial['payment_terms'].get('payment_deadline', 'Не найдено')}")
            if financial['payment_terms'].get('prepayment_percent'):
                print(f"   ✅ Аванс: {financial['payment_terms']['prepayment_percent']}%")

        # ЭТАП 4: Требования
        print("\n📋 Этап 4/6: Анализ требований...")
        requirements = self._analyze_requirements(truncated_doc, company_profile)
        total_reqs = (
            len(requirements.get('technical', [])) +
            len(requirements.get('qualification', [])) +
            len(requirements.get('financial', []))
        )
        print(f"   ✅ Всего требований: {total_reqs}")

        # ЭТАП 5: Scoring - оценка соответствия (НОВОЕ!)
        print("\n⭐ Этап 5/6: Оценка соответствия профилю компании...")
        suitability = self._calculate_suitability_score(
            basic_info,
            products,
            financial,
            requirements,
            company_profile
        )
        print(f"   ✅ Общая оценка: {suitability['total_score']}/100")
        print(f"   ✅ Рекомендация: {suitability['recommendation']}")

        # ЭТАП 6: Риски и рекомендации
        print("\n⚠️  Этап 6/6: Анализ рисков...")
        risks = self._analyze_risks(basic_info, financial, requirements, company_profile)
        print(f"   ✅ Выявлено рисков: {len(risks)}")

        # Формируем итоговый результат
        result = {
            "tender_info": {
                **basic_info,
                **financial,
                "products_or_services": products
            },
            "requirements": requirements,
            "suitability": suitability,
            "risks": risks,
            "analysis_metadata": {
                "method": "multi_stage",
                "stages_completed": 6,
                "analysis_time": time.time() - start_time,
                "token_usage": self.token_usage,
                "total_tokens": sum(self.token_usage.values())
            }
        }

        print("\n" + "="*70)
        print(f"  ✅ Анализ завершён за {result['analysis_metadata']['analysis_time']:.1f}с")
        print("="*70 + "\n")

        return result

    def _extract_basic_info(self, documentation: str) -> dict:
        """
        ЭТАП 1: Быстрое извлечение базовой информации.

        Используется: fast модель
        Цель: Получить основные параметры для последующих этапов
        """
        # Короткий промпт, простая задача
        prompt = f"""Извлеки базовую информацию о тендере из документации.

ДОКУМЕНТАЦИЯ (первые 20K символов):
{documentation[:20000]}

ЗАДАЧА: Извлечь следующие поля:
- name: Точное название тендера/закупки
- customer: Наименование заказчика
- nmck: Начальная максимальная цена контракта (число)
- deadline_submission: Срок подачи заявок (дата и время)
- deadline_execution: Срок исполнения контракта
- tender_type: Тип закупки ("товары", "работы", "услуги" или "товары и услуги")
- region: Регион поставки/выполнения работ

ФОРМАТ ОТВЕТА (только JSON, без комментариев):
{{
  "name": "Полное название",
  "customer": "Наименование заказчика",
  "nmck": число,
  "deadline_submission": "YYYY-MM-DD",
  "deadline_execution": "YYYY-MM-DD",
  "tender_type": "товары|работы|услуги",
  "region": "Регион"
}}

Если какое-то поле не найдено - укажи null.
"""

        response = self.llm_fast.generate(
            "Ты эксперт по извлечению данных из тендерной документации.",
            prompt
        )

        try:
            # Очистка от markdown
            response = response.strip()
            if response.startswith('```json'):
                response = response[7:]
            if response.startswith('```'):
                response = response[3:]
            if response.endswith('```'):
                response = response[:-3]
            response = response.strip()

            result = json.loads(response)
            self.token_usage['stage_1'] = len(prompt) + len(response)
            return result
        except json.JSONDecodeError as e:
            print(f"   ⚠️  Ошибка парсинга JSON на этапе 1: {e}")
            return {
                "name": None,
                "customer": None,
                "nmck": None,
                "deadline_submission": None,
                "deadline_execution": None,
                "tender_type": None,
                "region": None
            }

    def _extract_products_detailed(
        self,
        documentation: str,
        hint: str = None
    ) -> List[dict]:
        """
        ЭТАП 2: Детальное извлечение товаров/услуг.

        Используется: premium модель
        Цель: Получить полные спецификации всех позиций
        """
        # Сначала найдём раздел со спецификациями
        spec_section = self.truncator.extract_section_by_keyword(
            documentation,
            keywords=["спецификация", "перечень", "техническое задание", "приложение"],
            max_chars=30000
        )

        if not spec_section:
            spec_section = documentation[:30000]

        prompt = f"""Извлеки ВСЕ товары/услуги из спецификации тендера.

ТИП ЗАКУПКИ: {hint or "неизвестно"}

СПЕЦИФИКАЦИЯ:
{spec_section}

ЗАДАЧА: Для КАЖДОЙ позиции извлечь:
- name: точное наименование
- quantity: количество (число)
- unit: единица измерения (штука, метр, литр и т.д.)
- specifications: все технические характеристики в виде словаря
- raw_description: полное описание из документа

ВАЖНО:
- Извлекай ВСЕ позиции, даже если их много
- Включай все упомянутые характеристики
- Если характеристик нет - specifications должен быть пустым объектом {{}}

ФОРМАТ (только JSON массив):
[
  {{
    "name": "Точное наименование товара/услуги",
    "quantity": число,
    "unit": "штука",
    "specifications": {{
      "параметр1": "значение1",
      "параметр2": "значение2"
    }},
    "raw_description": "Полный текст из документа"
  }}
]

Если товары не найдены - верни пустой массив [].
"""

        response = self.llm_premium.generate(
            "Ты эксперт по извлечению спецификаций товаров из тендерной документации.",
            prompt
        )

        try:
            response = response.strip()
            if response.startswith('```json'):
                response = response[7:]
            if response.startswith('```'):
                response = response[3:]
            if response.endswith('```'):
                response = response[:-3]
            response = response.strip()

            products = json.loads(response)
            self.token_usage['stage_2'] = len(prompt) + len(response)

            if not isinstance(products, list):
                return []

            return products

        except json.JSONDecodeError as e:
            print(f"   ⚠️  Ошибка парсинга JSON на этапе 2: {e}")
            return []

    def _analyze_financial_terms(
        self,
        documentation: str,
        nmck: float = None
    ) -> dict:
        """
        ЭТАП 3: Анализ финансовых условий с Chain-of-Thought.

        Используется: premium модель
        Цель: Точное извлечение условий оплаты и обеспечений
        """
        # Извлекаем раздел с финансовыми условиями
        financial_section = self.truncator.extract_section_by_keyword(
            documentation,
            keywords=["порядок расчет", "условия оплат", "цена контракт", "проект контракт"],
            max_chars=20000
        )

        if not financial_section:
            financial_section = documentation[:20000]

        prompt = f"""Извлеки ТОЧНЫЕ финансовые условия из документации.

НМЦК: {nmck or "не указана"}

ДОКУМЕНТАЦИЯ:
{financial_section}

ПОРЯДОК АНАЛИЗА (Chain-of-Thought):

1. СНАЧАЛА найди раздел об оплате:
   <thinking>
   Ищу разделы: "Порядок расчетов", "Условия оплаты", "Цена контракта"
   Найденный раздел: [укажи точное название]
   </thinking>

2. ЗАТЕМ извлеки срок оплаты:
   <thinking>
   Цитата из текста: "[процитируй точную формулировку]"
   Срок оплаты в днях: [число] [рабочих/календарных] дней
   Момент оплаты: [после чего производится оплата]
   </thinking>

3. ЗАТЕМ проверь наличие аванса:
   <thinking>
   Есть ли упоминание аванса/предоплаты: [да/нет]
   Если да - процент: [число]%
   </thinking>

4. НАКОНЕЦ найди обеспечения:
   <thinking>
   Обеспечение заявки: [сумма или процент]
   Обеспечение контракта: [сумма или процент]
   </thinking>

ИТОГОВЫЙ JSON:
{{
  "payment_terms": {{
    "payment_deadline": "точный срок в днях",
    "payment_moment": "после чего платят",
    "prepayment_percent": число или null,
    "payment_schedule": "описание порядка оплаты"
  }},
  "guarantee_application": число или null,
  "guarantee_contract": число или null
}}

КРИТИЧНО: Найди КОНКРЕТНЫЕ сроки и суммы, процитируй из текста.
"""

        response = self.llm_premium.generate(
            "Ты эксперт по анализу финансовых условий государственных контрактов.",
            prompt
        )

        try:
            # Извлекаем JSON (он после всех <thinking>)
            json_start = response.rfind('{')
            if json_start != -1:
                json_part = response[json_start:]
                json_end = json_part.find('}') + 1
                if json_end > 0:
                    json_str = json_part[:json_end]
                else:
                    json_str = json_part
            else:
                json_str = response

            json_str = json_str.strip()
            if json_str.startswith('```'):
                json_str = json_str[3:]
            if json_str.endswith('```'):
                json_str = json_str[:-3]
            json_str = json_str.strip()

            result = json.loads(json_str)
            self.token_usage['stage_3'] = len(prompt) + len(response)
            return result

        except json.JSONDecodeError as e:
            print(f"   ⚠️  Ошибка парсинга JSON на этапе 3: {e}")
            return {
                "payment_terms": {},
                "guarantee_application": None,
                "guarantee_contract": None
            }

    def _analyze_requirements(
        self,
        documentation: str,
        company_profile: dict
    ) -> dict:
        """
        ЭТАП 4: Анализ требований к участникам.

        Используется: fast модель
        Цель: Извлечь все требования и сгруппировать по категориям
        """
        prompt = f"""Извлеки все требования к участникам тендера.

ДОКУМЕНТАЦИЯ (фрагмент):
{documentation[:30000]}

ЗАДАЧА: Найти и сгруппировать требования по категориям:

1. technical: Технические требования (сертификаты, стандарты, опыт работы с товаром)
2. qualification: Квалификационные требования (опыт, персонал, оборот)
3. financial: Финансовые требования (оборот, отсутствие долгов)
4. documentation: Требуемые документы для заявки

ФОРМАТ (JSON):
{{
  "technical": ["требование 1", "требование 2"],
  "qualification": ["требование 1", "требование 2"],
  "financial": ["требование 1", "требование 2"],
  "documentation": ["документ 1", "документ 2"]
}}

Извлекай только конкретные требования из текста, не придумывай.
"""

        response = self.llm_fast.generate(
            "Ты эксперт по требованиям в государственных закупках.",
            prompt
        )

        try:
            response = response.strip()
            if response.startswith('```json'):
                response = response[7:]
            if response.startswith('```'):
                response = response[3:]
            if response.endswith('```'):
                response = response[:-3]
            response = response.strip()

            result = json.loads(response)
            self.token_usage['stage_4'] = len(prompt) + len(response)
            return result

        except json.JSONDecodeError as e:
            print(f"   ⚠️  Ошибка парсинга JSON на этапе 4: {e}")
            return {
                "technical": [],
                "qualification": [],
                "financial": [],
                "documentation": []
            }

    def _calculate_suitability_score(
        self,
        basic_info: dict,
        products: List[dict],
        financial: dict,
        requirements: dict,
        company_profile: dict
    ) -> dict:
        """
        ЭТАП 5: Scoring - оценка соответствия тендера профилю компании.

        ⭐ НОВАЯ ФУНКЦИЯ! Главная цель Level 2 анализа.

        Используется: premium модель
        Цель: Автоматическая оценка 0-100 баллов и рекомендация
        """
        nmck_value = basic_info.get('nmck') or 0
        guarantee_app = financial.get('guarantee_application') or 'не указано'
        guarantee_contract = financial.get('guarantee_contract') or 'не указано'

        prompt = f"""Оцени соответствие тендера профилю компании по 5 критериям.

ТЕНДЕР:
Название: {basic_info.get('name', 'Не указано')}
НМЦК: {nmck_value:,.0f} ₽
Тип: {basic_info.get('tender_type', 'Не указано')}
Товаров/услуг: {len(products)} позиций
Обеспечение заявки: {guarantee_app}
Обеспечение контракта: {guarantee_contract}

ТРЕБОВАНИЯ:
Технических: {len(requirements.get('technical', []))}
Квалификационных: {len(requirements.get('qualification', []))}
Финансовых: {len(requirements.get('financial', []))}

ПРОФИЛЬ КОМПАНИИ:
{json.dumps(company_profile, ensure_ascii=False, indent=2)}

КРИТЕРИИ ОЦЕНКИ:

1. Соответствие отрасли (0-30 баллов):
   - Товары/услуги соответствуют специализации компании?
   - Компания работает в этой сфере?

2. Финансовые возможности (0-25 баллов):
   - НМЦК не превышает возможности компании?
   - Компания может внести обеспечения?
   - Компания справится с условиями оплаты?

3. Технические возможности (0-25 баллов):
   - Компания может поставить требуемые товары/выполнить работы?
   - Компания соответствует техническим требованиям?

4. География (0-10 баллов):
   - Регион тендера в зоне работы компании?

5. Сроки (0-10 баллов):
   - Компания может выполнить в указанный срок?

ФОРМАТ ОТВЕТА (JSON):
{{
  "total_score": 0-100,
  "breakdown": {{
    "industry_match": 0-30,
    "financial_capacity": 0-25,
    "technical_capability": 0-25,
    "geography": 0-10,
    "timeline": 0-10
  }},
  "reasoning": {{
    "industry_match": "краткое объяснение оценки",
    "financial_capacity": "объяснение",
    "technical_capability": "объяснение",
    "geography": "объяснение",
    "timeline": "объяснение"
  }},
  "recommendation": "participate|consider|skip",
  "confidence": "high|medium|low",
  "red_flags": ["Критичная проблема 1", "Критичная проблема 2"]
}}

РЕКОМЕНДАЦИИ:
- participate (80-100 баллов): Настоятельно рекомендуется участие
- consider (60-79 баллов): Рекомендуется рассмотреть участие
- skip (<60 баллов): Не рекомендуется участие

Будь объективным и честным в оценке.
"""

        response = self.llm_premium.generate(
            "Ты эксперт по оценке тендеров и принятию решений об участии.",
            prompt
        )

        try:
            response = response.strip()
            if response.startswith('```json'):
                response = response[7:]
            if response.startswith('```'):
                response = response[3:]
            if response.endswith('```'):
                response = response[:-3]
            response = response.strip()

            result = json.loads(response)
            self.token_usage['stage_5'] = len(prompt) + len(response)

            # Валидация scoring
            if not isinstance(result.get('total_score'), (int, float)):
                result['total_score'] = 0

            if result['total_score'] < 0:
                result['total_score'] = 0
            elif result['total_score'] > 100:
                result['total_score'] = 100

            return result

        except json.JSONDecodeError as e:
            print(f"   ⚠️  Ошибка парсинга JSON на этапе 5: {e}")
            return {
                "total_score": 0,
                "breakdown": {
                    "industry_match": 0,
                    "financial_capacity": 0,
                    "technical_capability": 0,
                    "geography": 0,
                    "timeline": 0
                },
                "reasoning": {},
                "recommendation": "skip",
                "confidence": "low",
                "red_flags": ["Ошибка анализа"]
            }

    def _analyze_risks(
        self,
        basic_info: dict,
        financial: dict,
        requirements: dict,
        company_profile: dict
    ) -> List[dict]:
        """
        ЭТАП 6: Анализ рисков.

        Используется: fast модель
        Цель: Выявить потенциальные риски участия
        """
        nmck_value = basic_info.get('nmck') or 0
        deadline_exec = basic_info.get('deadline_execution') or 'не указан'
        guarantee_contract = financial.get('guarantee_contract') or 'не указано'
        payment_schedule = financial.get('payment_terms', {}).get('payment_schedule') if isinstance(financial.get('payment_terms'), dict) else 'не указаны'
        if not payment_schedule:
            payment_schedule = 'не указаны'

        prompt = f"""Выяви риски участия в тендере для компании.

ТЕНДЕР:
НМЦК: {nmck_value:,.0f} ₽
Срок исполнения: {deadline_exec}
Обеспечение контракта: {guarantee_contract}
Условия оплаты: {payment_schedule}

ПРОФИЛЬ КОМПАНИИ:
{json.dumps(company_profile, ensure_ascii=False, indent=2)}

ЗАДАЧА: Выявить риски по категориям:
- financial: Финансовые риски
- execution: Риски исполнения контракта
- legal: Юридические риски
- competitive: Конкурентные риски

ФОРМАТ (JSON массив):
[
  {{
    "category": "financial|execution|legal|competitive",
    "description": "Описание риска",
    "severity": "HIGH|MEDIUM|LOW",
    "mitigation": "Как можно снизить риск"
  }}
]

Если рисков нет - верни пустой массив [].
"""

        response = self.llm_fast.generate(
            "Ты эксперт по рискам в государственных закупках.",
            prompt
        )

        try:
            response = response.strip()
            if response.startswith('```json'):
                response = response[7:]
            if response.startswith('```'):
                response = response[3:]
            if response.endswith('```'):
                response = response[:-3]
            response = response.strip()

            risks = json.loads(response)
            self.token_usage['stage_6'] = len(prompt) + len(response)

            if not isinstance(risks, list):
                return []

            return risks

        except json.JSONDecodeError as e:
            print(f"   ⚠️  Ошибка парсинга JSON на этапе 6: {e}")
            return []


if __name__ == "__main__":
    print("MultiStageAnalyzer создан успешно!")
    print("Для использования необходимо передать llm_premium и llm_fast адаптеры.")
