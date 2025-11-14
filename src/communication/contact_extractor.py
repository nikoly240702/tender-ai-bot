"""
Модуль для извлечения контактной информации из тендерной документации.
"""

import re
from typing import Dict, List, Optional, Any


class ContactExtractor:
    """Класс для извлечения контактов из текста документации."""

    @staticmethod
    def extract_emails(text: str) -> List[str]:
        """Извлекает email адреса из текста."""
        pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(pattern, text)
        return list(set(emails))  # Убираем дубликаты

    @staticmethod
    def extract_phones(text: str) -> List[str]:
        """Извлекает телефонные номера из текста."""
        patterns = [
            r'\+?7[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}',  # +7 (XXX) XXX-XX-XX
            r'8[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}',      # 8 (XXX) XXX-XX-XX
        ]

        phones = []
        for pattern in patterns:
            found = re.findall(pattern, text)
            phones.extend(found)

        # Нормализуем формат
        normalized = []
        for phone in phones:
            # Убираем все кроме цифр и +
            clean = re.sub(r'[^\d+]', '', phone)
            if len(clean) >= 10:
                normalized.append(phone.strip())

        return list(set(normalized))

    @staticmethod
    def extract_contacts(text: str) -> Dict[str, Any]:
        """
        Извлекает всю контактную информацию из текста.

        Returns:
            Словарь с контактами:
            {
                'emails': [...],
                'phones': [...],
                'contact_persons': [...],
                'raw_contacts': str
            }
        """
        emails = ContactExtractor.extract_emails(text)
        phones = ContactExtractor.extract_phones(text)

        # Ищем секцию с контактами
        contact_section = ""
        contact_keywords = [
            'контакт', 'связь', 'ответственн', 'специалист',
            'телефон', 'email', 'e-mail', 'адрес'
        ]

        lines = text.split('\n')
        in_contact_section = False
        contact_lines = []

        for i, line in enumerate(lines):
            lower_line = line.lower()
            if any(keyword in lower_line for keyword in contact_keywords):
                in_contact_section = True
                contact_lines.append(line)
            elif in_contact_section:
                if line.strip() and (any(keyword in lower_line for keyword in contact_keywords) or
                                     '@' in line or re.search(r'\d{3}', line)):
                    contact_lines.append(line)
                elif not line.strip():
                    continue
                else:
                    in_contact_section = False

        contact_section = '\n'.join(contact_lines[:20])  # Первые 20 строк

        return {
            'emails': emails,
            'phones': phones,
            'raw_contacts': contact_section,
            'has_contacts': len(emails) > 0 or len(phones) > 0
        }


if __name__ == "__main__":
    test_text = """
    Контактное лицо: Иванов Иван Иванович
    Телефон: +7 (495) 123-45-67
    Email: ivanov@company.ru
    Дополнительно: 8-800-555-35-35
    """

    result = ContactExtractor.extract_contacts(test_text)
    print("Emails:", result['emails'])
    print("Phones:", result['phones'])
    print("Section:", result['raw_contacts'])
