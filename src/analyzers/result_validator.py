"""
Валидатор результатов AI-анализа.
Проверяет качество извлеченных данных и автоматически исправляет ошибки.
"""

import re
from typing import Dict, List, Any, Optional
from datetime import datetime


class ResultValidator:
    """
    Валидация и исправление результатов AI-анализа.

    Проблема: LLM может пропустить важные поля или вернуть некорректный формат
    Решение: Автоматическая проверка и повторный запрос при критичных ошибках
    """

    def __init__(self, llm):
        """
        Args:
            llm: LLM адаптер для повторных запросов при ошибках
        """
        self.llm = llm

    def validate_analysis(
        self,
        result: dict,
        documentation: str
    ) -> dict:
        """
        Полная валидация результата анализа.

        Args:
            result: Результат AI-анализа
            documentation: Исходная документация

        Returns:
            Валидированный и исправленный результат
        """
        print("\n🔍 Валидация результатов анализа...")

        issues = []

        # Проверка 1: Обязательные поля
        issues.extend(self._check_required_fields(result))

        # Проверка 2: Форматы дат
        issues.extend(self._check_date_formats(result))

        # Проверка 3: Числовые значения
        issues.extend(self._check_numeric_values(result))

        # Проверка 4: Полнота товаров
        issues.extend(self._check_products_completeness(result, documentation))

        # Проверка 5: Консистентность данных
        issues.extend(self._check_data_consistency(result))

        # Применяем исправления
        if issues:
            print(f"   Найдено проблем: {len(issues)}")
            result = self._apply_fixes(result, issues, documentation)
        else:
            print("   ✅ Проблем не найдено")

        # Добавляем метаданные валидации
        result['validation'] = {
            'passed': len([i for i in issues if i['severity'] == 'CRITICAL']) == 0,
            'issues': issues,
            'issues_count': len(issues),
            'quality_score': self._calculate_quality_score(issues)
        }

        quality = result['validation']['quality_score']
        if quality >= 90:
            print(f"   ✅ Качество анализа: {quality:.1f}% (отлично)")
        elif quality >= 70:
            print(f"   ⚠️  Качество анализа: {quality:.1f}% (хорошо)")
        else:
            print(f"   ❌ Качество анализа: {quality:.1f}% (требует внимания)")

        return result

    def _check_required_fields(self, result: dict) -> List[dict]:
        """Проверка обязательных полей."""
        issues = []

        required_fields = [
            ('tender_info.name', 'Название тендера'),
            ('tender_info.customer', 'Заказчик'),
            ('tender_info.nmck', 'НМЦК'),
        ]

        for field_path, field_name in required_fields:
            value = self._get_nested_field(result, field_path)
            if value is None or value == "" or value == 0:
                issues.append({
                    'severity': 'CRITICAL',
                    'field': field_path,
                    'field_name': field_name,
                    'issue': f'{field_name} не найдено',
                    'action': 'retry_extraction'
                })

        return issues

    def _check_date_formats(self, result: dict) -> List[dict]:
        """Проверка форматов дат."""
        issues = []

        date_fields = [
            ('tender_info.deadline_submission', 'Срок подачи заявок'),
            ('tender_info.deadline_execution', 'Срок исполнения'),
        ]

        for field_path, field_name in date_fields:
            value = self._get_nested_field(result, field_path)
            if value and not self._is_valid_date(value):
                issues.append({
                    'severity': 'MEDIUM',
                    'field': field_path,
                    'field_name': field_name,
                    'issue': f'Некорректный формат даты: {value}',
                    'action': 'fix_format',
                    'current_value': value
                })

        return issues

    def _check_numeric_values(self, result: dict) -> List[dict]:
        """Проверка числовых значений."""
        issues = []

        # НМЦК должна быть положительным числом
        nmck = self._get_nested_field(result, 'tender_info.nmck')
        if nmck is not None:
            if not isinstance(nmck, (int, float)):
                issues.append({
                    'severity': 'CRITICAL',
                    'field': 'tender_info.nmck',
                    'field_name': 'НМЦК',
                    'issue': 'НМЦК должна быть числом',
                    'action': 'convert_to_number',
                    'current_value': nmck
                })
            elif nmck <= 0:
                issues.append({
                    'severity': 'CRITICAL',
                    'field': 'tender_info.nmck',
                    'field_name': 'НМЦК',
                    'issue': 'НМЦК должна быть положительным числом',
                    'action': 'retry_extraction'
                })

        # Обеспечения (если указаны) должны быть положительными
        guarantees = [
            ('tender_info.guarantee_application', 'Обеспечение заявки'),
            ('tender_info.guarantee_contract', 'Обеспечение контракта'),
        ]

        for field_path, field_name in guarantees:
            value = self._get_nested_field(result, field_path)
            if value is not None and value != 0:
                if not isinstance(value, (int, float)):
                    issues.append({
                        'severity': 'MEDIUM',
                        'field': field_path,
                        'field_name': field_name,
                        'issue': f'{field_name} должно быть числом',
                        'action': 'convert_to_number',
                        'current_value': value
                    })
                elif value < 0:
                    issues.append({
                        'severity': 'MEDIUM',
                        'field': field_path,
                        'field_name': field_name,
                        'issue': f'{field_name} не может быть отрицательным',
                        'action': 'set_to_null'
                    })

        return issues

    def _check_products_completeness(
        self,
        result: dict,
        documentation: str
    ) -> List[dict]:
        """Проверка полноты извлечения товаров."""
        issues = []

        products = self._get_nested_field(result, 'tender_info.products_or_services')

        if not products or len(products) == 0:
            # Проверим, упоминаются ли товары в документации
            if self._document_mentions_products(documentation):
                issues.append({
                    'severity': 'HIGH',
                    'field': 'tender_info.products_or_services',
                    'field_name': 'Товары/услуги',
                    'issue': 'Документация содержит товары, но они не извлечены',
                    'action': 'retry_extraction'
                })

        return issues

    def _check_data_consistency(self, result: dict) -> List[dict]:
        """Проверка консистентности данных."""
        issues = []

        # Проверка: если есть аванс - должен быть процент аванса
        payment_terms = self._get_nested_field(result, 'tender_info.payment_terms')
        if payment_terms and isinstance(payment_terms, dict):
            payment_schedule = payment_terms.get('payment_schedule', '').lower()
            if 'аванс' in payment_schedule or 'предоплат' in payment_schedule:
                if not payment_terms.get('prepayment_percent'):
                    issues.append({
                        'severity': 'MEDIUM',
                        'field': 'tender_info.payment_terms.prepayment_percent',
                        'field_name': 'Процент аванса',
                        'issue': 'Упоминается аванс, но не указан процент',
                        'action': 'clarify'
                    })

        # Проверка: scoring должен быть в диапазоне 0-100
        total_score = self._get_nested_field(result, 'suitability.total_score')
        if total_score is not None:
            if not isinstance(total_score, (int, float)):
                issues.append({
                    'severity': 'MEDIUM',
                    'field': 'suitability.total_score',
                    'field_name': 'Общая оценка',
                    'issue': 'Оценка должна быть числом',
                    'action': 'set_to_zero'
                })
            elif total_score < 0 or total_score > 100:
                issues.append({
                    'severity': 'MEDIUM',
                    'field': 'suitability.total_score',
                    'field_name': 'Общая оценка',
                    'issue': 'Оценка должна быть в диапазоне 0-100',
                    'action': 'clamp_to_range'
                })

        return issues

    def _apply_fixes(
        self,
        result: dict,
        issues: List[dict],
        documentation: str
    ) -> dict:
        """Применяет автоматические исправления."""
        for issue in issues:
            action = issue['action']

            if action == 'retry_extraction':
                # Повторный запрос к LLM для проблемного поля
                field = issue['field']
                field_name = issue['field_name']

                print(f"   🔄 Повторное извлечение: {field_name}")

                retry_prompt = f"""Предыдущий анализ упустил важное поле.

ПОЛЕ: {field_name}
ПУТЬ: {field}

Проанализируй документацию и найди это поле.

ДОКУМЕНТАЦИЯ (первые 30K символов):
{documentation[:30000]}

Верни ТОЛЬКО значение этого поля в формате:
{{"value": "найденное значение"}}

Если не найдено - верни {{"value": null}}
"""

                try:
                    response = self.llm.generate(
                        "Ты эксперт по извлечению данных из документов.",
                        retry_prompt
                    )

                    # Очистка
                    response = response.strip()
                    if response.startswith('```json'):
                        response = response[7:]
                    if response.startswith('```'):
                        response = response[3:]
                    if response.endswith('```'):
                        response = response[:-3]
                    response = response.strip()

                    import json
                    extracted = json.loads(response)
                    value = extracted.get('value')

                    if value:
                        self._set_nested_field(result, field, value)
                        print(f"      ✅ Найдено: {str(value)[:50]}")
                    else:
                        print(f"      ⚠️  Не найдено")

                except Exception as e:
                    print(f"      ❌ Ошибка повторного извлечения: {e}")

            elif action == 'fix_format':
                # Исправление формата даты
                field = issue['field']
                value = self._get_nested_field(result, field)
                fixed_value = self._fix_date_format(value)
                if fixed_value != value:
                    self._set_nested_field(result, field, fixed_value)
                    print(f"   ✅ Исправлен формат даты: {value} → {fixed_value}")

            elif action == 'convert_to_number':
                # Конвертация в число
                field = issue['field']
                value = issue.get('current_value')
                try:
                    # Убираем все кроме цифр, точки и минуса
                    cleaned = re.sub(r'[^\d.-]', '', str(value))
                    numeric_value = float(cleaned)
                    self._set_nested_field(result, field, numeric_value)
                    print(f"   ✅ Конвертировано в число: {value} → {numeric_value}")
                except ValueError:
                    print(f"   ❌ Не удалось конвертировать: {value}")

            elif action == 'set_to_null':
                # Установить null
                field = issue['field']
                self._set_nested_field(result, field, None)
                print(f"   ✅ Установлено null: {field}")

            elif action == 'set_to_zero':
                # Установить 0
                field = issue['field']
                self._set_nested_field(result, field, 0)
                print(f"   ✅ Установлено 0: {field}")

            elif action == 'clamp_to_range':
                # Ограничить диапазоном 0-100
                field = issue['field']
                value = self._get_nested_field(result, field)
                clamped = max(0, min(100, value))
                self._set_nested_field(result, field, clamped)
                print(f"   ✅ Ограничено диапазоном: {value} → {clamped}")

        return result

    def _calculate_quality_score(self, issues: List[dict]) -> float:
        """
        Рассчитывает оценку качества анализа на основе найденных проблем.

        Returns:
            Оценка от 0 до 100
        """
        if not issues:
            return 100.0

        # Веса для разных уровней серьезности
        severity_weights = {
            'CRITICAL': 20,  # -20 баллов за критичную проблему
            'HIGH': 10,      # -10 баллов за важную проблему
            'MEDIUM': 5,     # -5 баллов за среднюю проблему
            'LOW': 2         # -2 балла за низкую проблему
        }

        penalty = sum(
            severity_weights.get(issue['severity'], 5)
            for issue in issues
        )

        score = max(0, 100 - penalty)
        return score

    # Вспомогательные методы

    def _get_nested_field(self, obj: dict, field_path: str) -> Any:
        """Получает значение вложенного поля по пути."""
        keys = field_path.split('.')
        current = obj

        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None

            if current is None:
                return None

        return current

    def _set_nested_field(self, obj: dict, field_path: str, value: Any):
        """Устанавливает значение вложенного поля по пути."""
        keys = field_path.split('.')
        current = obj

        for i, key in enumerate(keys[:-1]):
            if key not in current:
                current[key] = {}
            current = current[key]

        current[keys[-1]] = value

    def _is_valid_date(self, date_str: str) -> bool:
        """Проверяет, является ли строка валидной датой."""
        if not isinstance(date_str, str):
            return False

        # Паттерны дат
        date_patterns = [
            r'\d{4}-\d{2}-\d{2}',  # YYYY-MM-DD
            r'\d{2}\.\d{2}\.\d{4}',  # DD.MM.YYYY
            r'\d{2}/\d{2}/\d{4}',  # DD/MM/YYYY
        ]

        for pattern in date_patterns:
            if re.match(pattern, date_str):
                return True

        return False

    def _fix_date_format(self, date_str: str) -> str:
        """Пытается исправить формат даты на YYYY-MM-DD."""
        if not isinstance(date_str, str):
            return date_str

        # DD.MM.YYYY → YYYY-MM-DD
        match = re.match(r'(\d{2})\.(\d{2})\.(\d{4})', date_str)
        if match:
            day, month, year = match.groups()
            return f"{year}-{month}-{day}"

        # DD/MM/YYYY → YYYY-MM-DD
        match = re.match(r'(\d{2})/(\d{2})/(\d{4})', date_str)
        if match:
            day, month, year = match.groups()
            return f"{year}-{month}-{day}"

        return date_str

    def _document_mentions_products(self, documentation: str) -> bool:
        """Проверяет, упоминаются ли товары/услуги в документации."""
        doc_lower = documentation.lower()

        keywords = [
            'спецификация',
            'перечень товаров',
            'наименование товар',
            'количество',
            'техническое задание',
            'приложение',
            'характеристики товар'
        ]

        for keyword in keywords:
            if keyword in doc_lower:
                return True

        return False


if __name__ == "__main__":
    print("ResultValidator создан успешно!")
    print("Для использования необходимо передать LLM адаптер.")
