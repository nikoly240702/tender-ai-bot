"""
Генератор шаблонов писем для коммуникации с заказчиком.
"""

from typing import Dict, List, Any
from datetime import datetime


class TemplateGenerator:
    """Класс для генерации шаблонов писем."""

    @staticmethod
    def generate_email_template(
        questions: Dict[str, List[str]],
        contacts: Dict[str, Any],
        tender_info: Dict[str, Any],
        company_name: str = "Ваша компания"
    ) -> str:
        """
        Генерирует готовый шаблон письма заказчику.

        Args:
            questions: Словарь с вопросами по категориям
            contacts: Контактная информация заказчика
            tender_info: Информация о тендере
            company_name: Название вашей компании

        Returns:
            Готовый текст письма
        """
        # Извлекаем данные
        tender_name = tender_info.get('name', 'тендер')
        customer = tender_info.get('customer', 'Уважаемый заказчик')

        # Формируем блоки вопросов
        critical_questions = questions.get('critical', [])
        important_questions = questions.get('important', [])
        optional_questions = questions.get('optional', [])

        # Генерируем письмо
        template = f"""Тема: Запрос разъяснений по тендеру "{tender_name}"

{customer},

Представляю компанию "{company_name}". Рассматриваем возможность участия в тендере "{tender_name}".

При изучении документации возникли следующие вопросы, требующие уточнения:
"""

        question_num = 1

        if critical_questions:
            template += "\n=== КРИТИЧНЫЕ ВОПРОСЫ ===\n\n"
            for q in critical_questions:
                template += f"{question_num}. {q}\n\n"
                question_num += 1

        if important_questions:
            template += "\n=== ВАЖНЫЕ ВОПРОСЫ ===\n\n"
            for q in important_questions:
                template += f"{question_num}. {q}\n\n"
                question_num += 1

        if optional_questions:
            template += "\n=== ДОПОЛНИТЕЛЬНЫЕ ВОПРОСЫ ===\n\n"
            for q in optional_questions:
                template += f"{question_num}. {q}\n\n"
                question_num += 1

        template += f"""
Просим предоставить разъяснения по указанным вопросам в соответствии с действующим законодательством.

С уважением,
{company_name}

---
Дата: {datetime.now().strftime('%d.%m.%Y')}
"""

        if contacts.get('emails'):
            template += f"\nОтветить на: {contacts['emails'][0]}"

        return template

    @staticmethod
    def generate_summary_text(
        tender_info: Dict[str, Any],
        score: Dict[str, Any],
        gaps_count: Dict[str, int]
    ) -> str:
        """Генерирует краткую текстовую сводку по тендеру."""

        summary = f"""
=== СВОДКА ПО ТЕНДЕРУ ===

Тендер: {tender_info.get('name', 'Н/Д')}
Заказчик: {tender_info.get('customer', 'Н/Д')}
НМЦК: {tender_info.get('nmck', 0):,.0f} руб.

Оценка: {score.get('total_score', 0):.0f}/100
Готовность к участию: {score.get('readiness_percent', 0):.0f}%

Выявлено пробелов:
- Критичных: {gaps_count.get('critical', 0)}
- Важных: {gaps_count.get('high', 0)}
- Средних: {gaps_count.get('medium', 0)}
- Низких: {gaps_count.get('low', 0)}

Рекомендация: {score.get('recommendation', 'Требуется анализ')}
"""
        return summary


if __name__ == "__main__":
    # Тестовый пример
    questions = {
        'critical': [
            "Уточните сроки поставки оборудования",
            "Требуется ли наличие сертификата ISO 27001?"
        ],
        'important': [
            "Какой состав комиссии для приемки работ?"
        ],
        'optional': [
            "Возможно ли продление срока выполнения?"
        ]
    }

    contacts = {
        'emails': ['tender@company.ru'],
        'phones': ['+7 (495) 123-45-67']
    }

    tender_info = {
        'name': 'Поставка компьютерного оборудования',
        'customer': 'ООО "Заказчик"',
        'nmck': 5000000
    }

    template = TemplateGenerator.generate_email_template(
        questions, contacts, tender_info, "ООО 'Исполнитель'"
    )

    print(template)
