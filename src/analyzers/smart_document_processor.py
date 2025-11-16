"""
Умная обработка документов с приоритизацией разделов.
Решает проблему потери важной информации при обрезке до лимита токенов.
"""

import re
from typing import List, Dict, Tuple


class SmartDocumentTruncator:
    """
    Умная обрезка документации с сохранением важных разделов.

    Проблема: Тупая обрезка до 50K символов теряет критичную информацию
    Решение: Приоритизация разделов и включение самых важных
    """

    # Приоритеты разделов (keyword, priority_score)
    # 1.0 - самый важный, 0.0 - самый низкий
    PRIORITY_SECTIONS = [
        ("проект контракт", 1.0),
        ("договор", 0.95),
        ("техническое задание", 0.9),
        ("спецификация", 0.9),
        ("порядок расчет", 0.85),
        ("условия оплат", 0.85),
        ("обеспечение заявки", 0.8),
        ("обеспечение контракта", 0.8),
        ("требования к участник", 0.8),
        ("критерии оценки", 0.75),
        ("срок исполнения", 0.75),
        ("срок подачи", 0.75),
        ("приложение", 0.6),
        ("перечень", 0.6),
        ("состав", 0.55),
        ("порядок предоставления", 0.5),
    ]

    def smart_truncate(
        self,
        documentation: str,
        max_chars: int = 50000,
        preserve_structure: bool = True
    ) -> str:
        """
        Умная обрезка с сохранением важных разделов.

        Args:
            documentation: Полный текст документации
            max_chars: Максимальное количество символов
            preserve_structure: Сохранять ли порядок разделов

        Returns:
            Обрезанная документация с важными разделами
        """
        # Если документация умещается - возвращаем как есть
        if len(documentation) <= max_chars:
            return documentation

        print(f"\n📄 Умная обрезка: {len(documentation):,} → {max_chars:,} символов")

        # Шаг 1: Разбить на разделы
        sections = self._split_into_sections(documentation)

        if not sections:
            # Fallback: простая обрезка если разделов не найдено
            print("⚠️  Разделы не найдены, используется простая обрезка")
            return documentation[:max_chars]

        print(f"   Найдено разделов: {len(sections)}")

        # Шаг 2: Определить приоритеты
        for section in sections:
            section['priority'] = self._calculate_priority(section['title'])

        # Шаг 3: Сортировать по приоритету
        sections.sort(key=lambda x: x['priority'], reverse=True)

        # Шаг 4: Собрать до лимита
        selected_sections = []
        total_chars = 0

        for section in sections:
            section_length = len(section['content'])

            if total_chars + section_length <= max_chars:
                selected_sections.append(section)
                total_chars += section_length
                print(f"   ✅ {section['title'][:50]:50} | {section_length:6} символов | приоритет: {section['priority']:.2f}")
            elif total_chars < max_chars:
                # Частично включить последний раздел
                remaining = max_chars - total_chars
                truncated_section = section.copy()
                truncated_section['content'] = section['content'][:remaining]
                selected_sections.append(truncated_section)
                total_chars += remaining
                print(f"   ⚠️  {section['title'][:50]:50} | {remaining:6} символов (обрезан)")
                break
            else:
                print(f"   ❌ {section['title'][:50]:50} | пропущен")

        # Шаг 5: Восстановить порядок разделов (если требуется)
        if preserve_structure:
            selected_sections.sort(key=lambda x: x['original_position'])

        result = '\n\n'.join([s['content'] for s in selected_sections])
        print(f"   📊 Итого: {total_chars:,} символов, {len(selected_sections)} разделов")

        return result

    def _split_into_sections(self, text: str) -> List[Dict]:
        """
        Разбивает документ на разделы по заголовкам.

        Паттерны заголовков:
        - "1. НАЗВАНИЕ РАЗДЕЛА"
        - "РАЗДЕЛ 1. НАЗВАНИЕ"
        - "Приложение №1"
        - "I. НАЗВАНИЕ" (римские цифры)
        """
        sections = []

        # Паттерны для заголовков разделов
        section_patterns = [
            r'\n(\d+\.\s*[А-ЯЁ][^\n]{5,100})\n',  # "1. НАЗВАНИЕ РАЗДЕЛА"
            r'\n(РАЗДЕЛ\s+\d+[^\n]{5,100})\n',  # "РАЗДЕЛ 1. НАЗВАНИЕ"
            r'\n(Приложение\s*№?\s*\d+[^\n]{0,100})\n',  # "Приложение №1"
            r'\n([IVX]+\.\s*[А-ЯЁ][^\n]{5,100})\n',  # "I. НАЗВАНИЕ" (римские)
            r'\n([А-ЯЁ][А-ЯЁ\s]{10,100})\n',  # "ПОЛНОСТЬЮ ЗАГЛАВНЫМИ"
        ]

        all_matches = []

        for pattern in section_patterns:
            for match in re.finditer(pattern, text, re.MULTILINE):
                all_matches.append({
                    'title': match.group(1).strip(),
                    'start': match.end(),
                    'position': match.start()
                })

        # Сортируем по позиции в тексте
        all_matches.sort(key=lambda x: x['position'])

        # Убираем дубликаты (один заголовок может совпасть с несколькими паттернами)
        unique_matches = []
        for i, match in enumerate(all_matches):
            if i == 0 or match['position'] != all_matches[i-1]['position']:
                unique_matches.append(match)

        # Формируем разделы
        for i, match in enumerate(unique_matches):
            title = match['title']
            start = match['start']
            end = unique_matches[i+1]['start'] if i+1 < len(unique_matches) else len(text)
            content = text[start:end].strip()

            sections.append({
                'title': title,
                'content': content,
                'original_position': i,
                'priority': 0.5  # Значение по умолчанию
            })

        return sections

    def _calculate_priority(self, title: str) -> float:
        """
        Определяет приоритет раздела по его названию.

        Args:
            title: Название раздела

        Returns:
            Приоритет от 0.0 до 1.0
        """
        title_lower = title.lower()

        # Проверяем все ключевые слова
        for keyword, priority in self.PRIORITY_SECTIONS:
            if keyword in title_lower:
                return priority

        # Если ни одно ключевое слово не найдено
        return 0.4  # Низкий приоритет для неизвестных разделов

    def extract_section_by_keyword(
        self,
        documentation: str,
        keywords: List[str],
        max_chars: int = 20000
    ) -> str:
        """
        Извлекает конкретный раздел по ключевым словам.
        Полезно для целевого извлечения (например, только проект контракта).

        Args:
            documentation: Полный текст
            keywords: Список ключевых слов для поиска
            max_chars: Максимальный размер извлекаемого раздела

        Returns:
            Текст найденного раздела или пустая строка
        """
        sections = self._split_into_sections(documentation)

        for section in sections:
            title_lower = section['title'].lower()
            for keyword in keywords:
                if keyword.lower() in title_lower:
                    content = section['content']
                    if len(content) > max_chars:
                        return content[:max_chars]
                    return content

        # Если раздел не найден по заголовку - ищем в тексте
        for keyword in keywords:
            pattern = rf'({keyword}.*?)(?=\n[А-ЯЁ0-9]+\.|$)'
            match = re.search(pattern, documentation, re.IGNORECASE | re.DOTALL)
            if match:
                content = match.group(1)
                if len(content) > max_chars:
                    return content[:max_chars]
                return content

        return ""

    def get_document_summary(self, documentation: str) -> Dict:
        """
        Получает сводку о документе: размер, количество разделов, важные разделы.

        Args:
            documentation: Текст документации

        Returns:
            Словарь со статистикой
        """
        sections = self._split_into_sections(documentation)

        for section in sections:
            section['priority'] = self._calculate_priority(section['title'])

        # Находим важные разделы (приоритет > 0.7)
        important_sections = [
            s for s in sections if s['priority'] > 0.7
        ]

        return {
            'total_chars': len(documentation),
            'total_sections': len(sections),
            'important_sections_count': len(important_sections),
            'important_sections': [
                {
                    'title': s['title'],
                    'priority': s['priority'],
                    'length': len(s['content'])
                }
                for s in sorted(important_sections, key=lambda x: x['priority'], reverse=True)
            ],
            'all_section_titles': [s['title'] for s in sections]
        }


if __name__ == "__main__":
    # Тестирование
    truncator = SmartDocumentTruncator()

    # Пример документа
    sample_doc = """
1. ОБЩИЕ ПОЛОЖЕНИЯ
Это общие положения тендера.

2. ПРОЕКТ КОНТРАКТА
Порядок расчетов: оплата производится в течение 7 рабочих дней.
Обеспечение контракта: 10% от цены контракта.

3. ТЕХНИЧЕСКОЕ ЗАДАНИЕ
Спецификация товаров:
- Товар 1: характеристики
- Товар 2: характеристики

Приложение №1. ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ
Дополнительная информация.
"""

    print("\n=== ТЕСТ УМНОЙ ОБРЕЗКИ ===")
    print(f"Исходный размер: {len(sample_doc)} символов")

    # Получаем сводку
    summary = truncator.get_document_summary(sample_doc)
    print(f"\nСводка:")
    print(f"  Разделов: {summary['total_sections']}")
    print(f"  Важных разделов: {summary['important_sections_count']}")
    for sec in summary['important_sections']:
        print(f"    - {sec['title']} (приоритет: {sec['priority']:.2f})")

    # Умная обрезка
    truncated = truncator.smart_truncate(sample_doc, max_chars=200)
    print(f"\nОбрезанный размер: {len(truncated)} символов")
    print(f"\nРезультат:\n{truncated}")
