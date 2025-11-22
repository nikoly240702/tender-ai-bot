"""
Модуль для проверки арбитражных дел организаций через RusProfile API.
"""

import requests
from typing import Dict, Any, Optional
import re


class RusProfileChecker:
    """Проверка арбитражных дел через RusProfile."""

    BASE_URL = "https://www.rusprofile.ru"

    def __init__(self):
        """Инициализация чекера."""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def check_arbitration(self, company_name: str) -> Optional[Dict[str, Any]]:
        """
        Проверяет наличие арбитражных дел у организации.

        Args:
            company_name: Название организации

        Returns:
            Словарь с информацией об арбитражных делах или None
        """
        try:
            # Поиск компании
            search_url = f"{self.BASE_URL}/search"
            params = {'query': company_name, 'type': 'ul'}

            response = self.session.get(search_url, params=params, timeout=10)

            if response.status_code != 200:
                print(f"   ⚠️  Ошибка поиска в RusProfile: HTTP {response.status_code}")
                return None

            # Парсинг результатов (упрощенная версия - требует детальный парсинг HTML)
            # В реальной версии нужно парсить HTML с BeautifulSoup

            # Заглушка - возвращаем None, если реальный парсинг не реализован
            print(f"   ℹ️  Проверка RusProfile для '{company_name}' - функционал в разработке")
            return {
                "company_name": company_name,
                "arbitration_count": None,
                "total_amount": None,
                "note": "Автоматическая проверка в разработке. Проверьте вручную на rusprofile.ru"
            }

        except requests.RequestException as e:
            print(f"   ⚠️  Ошибка запроса к RusProfile: {e}")
            return None
        except Exception as e:
            print(f"   ⚠️  Неожиданная ошибка при проверке RusProfile: {e}")
            return None

    def extract_inn_from_text(self, text: str) -> Optional[str]:
        """
        Извлекает ИНН из текста.

        Args:
            text: Текст для поиска ИНН

        Returns:
            ИНН или None
        """
        # Паттерн для ИНН (10 или 12 цифр)
        inn_pattern = r'\b\d{10}(?:\d{2})?\b'
        match = re.search(inn_pattern, text)

        if match:
            return match.group()

        return None


def main():
    """Пример использования."""
    checker = RusProfileChecker()

    # Тест
    result = checker.check_arbitration("УПРАВЛЕНИЕ ГОСУДАРСТВЕННЫХ ЗАКУПОК БРЯНСКОЙ ОБЛАСТИ")

    if result:
        print("\nРезультат проверки:")
        print(f"Компания: {result['company_name']}")
        print(f"Арбитражных дел: {result.get('arbitration_count', 'N/A')}")
        print(f"Общая сумма: {result.get('total_amount', 'N/A')}")
        print(f"Примечание: {result.get('note', '')}")
    else:
        print("Не удалось получить данные")


if __name__ == "__main__":
    main()
