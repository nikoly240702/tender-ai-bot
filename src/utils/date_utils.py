"""
Утилиты для работы с датами в тендерах.
"""

from datetime import datetime, timedelta
from typing import Optional
import re


class DateUtils:
    """Утилиты для работы с датами."""

    @staticmethod
    def calculate_days_until_deadline(deadline_str: Optional[str]) -> Optional[int]:
        """
        Вычисляет количество дней до дедлайна подачи заявок.

        Args:
            deadline_str: Строка с датой дедлайна (форматы: "YYYY-MM-DD", "YYYY-MM-DD HH:MM", "DD.MM.YYYY")

        Returns:
            Количество дней до дедлайна или None если не удалось распарсить
        """
        if not deadline_str:
            return None

        try:
            # Пытаемся распарсить разные форматы
            deadline_date = None

            # Формат: YYYY-MM-DD HH:MM
            if re.match(r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}', deadline_str):
                deadline_date = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")

            # Формат: YYYY-MM-DD
            elif re.match(r'\d{4}-\d{2}-\d{2}', deadline_str):
                deadline_date = datetime.strptime(deadline_str, "%Y-%m-%d")

            # Формат: DD.MM.YYYY HH:MM
            elif re.match(r'\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}', deadline_str):
                deadline_date = datetime.strptime(deadline_str, "%d.%m.%Y %H:%M")

            # Формат: DD.MM.YYYY
            elif re.match(r'\d{2}\.\d{2}\.\d{4}', deadline_str):
                deadline_date = datetime.strptime(deadline_str, "%d.%m.%Y")

            if not deadline_date:
                return None

            # Вычисляем разницу
            now = datetime.now()
            delta = deadline_date - now

            # Возвращаем количество полных дней
            return delta.days

        except ValueError as e:
            print(f"   ⚠️  Ошибка парсинга даты '{deadline_str}': {e}")
            return None

    @staticmethod
    def format_deadline_display(deadline_str: Optional[str]) -> str:
        """
        Форматирует дедлайн для отображения с количеством дней.

        Args:
            deadline_str: Строка с датой дедлайна

        Returns:
            Строка вида "03.12.2025 (11 дней)" или просто дату если не удалось вычислить
        """
        if not deadline_str:
            return "Не указано"

        days = DateUtils.calculate_days_until_deadline(deadline_str)

        if days is None:
            return deadline_str

        if days < 0:
            return f"{deadline_str} (истек {abs(days)} дней назад)"
        elif days == 0:
            return f"{deadline_str} (сегодня!)"
        elif days == 1:
            return f"{deadline_str} (завтра)"
        else:
            return f"{deadline_str} ({days} дней)"

    @staticmethod
    def is_deadline_soon(deadline_str: Optional[str], threshold_days: int = 7) -> bool:
        """
        Проверяет, скоро ли истекает дедлайн.

        Args:
            deadline_str: Строка с датой дедлайна
            threshold_days: Порог в днях (по умолчанию 7)

        Returns:
            True если дедлайн истекает в ближайшие threshold_days дней
        """
        days = DateUtils.calculate_days_until_deadline(deadline_str)

        if days is None:
            return False

        return 0 <= days <= threshold_days


def main():
    """Пример использования."""
    print("Примеры работы с датами:\n")

    test_dates = [
        "2025-12-03 18:00",
        "2025-12-03",
        "03.12.2025",
        "2025-11-22 12:00",  # Сегодня
        "2025-11-21",  # Вчера
    ]

    for date_str in test_dates:
        days = DateUtils.calculate_days_until_deadline(date_str)
        formatted = DateUtils.format_deadline_display(date_str)
        is_soon = DateUtils.is_deadline_soon(date_str)

        print(f"Дата: {date_str}")
        print(f"  Дней до дедлайна: {days}")
        print(f"  Форматированный вывод: {formatted}")
        print(f"  Дедлайн скоро: {is_soon}")
        print()


if __name__ == "__main__":
    main()
