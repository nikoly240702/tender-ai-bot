#!/usr/bin/env python3
"""
Тест отображения AI-анализа в HTML.
"""

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from smart_tender_search import create_enhanced_html_report
import webbrowser

# Загружаем последние результаты анализа
reports_dir = Path(__file__).parent / 'output' / 'reports'
json_files = sorted(reports_dir.glob('tender_report_*.json'), key=lambda x: x.stat().st_mtime, reverse=True)

if not json_files:
    print("❌ Не найдено JSON файлов с результатами анализа")
    sys.exit(1)

print(f"✅ Найдено {len(json_files)} файлов с результатами")
print(f"📄 Используем последний файл: {json_files[0].name}\n")

# Загружаем данные из JSON
with open(json_files[0], 'r', encoding='utf-8') as f:
    analysis_data = json.load(f)

# Создаем тестовый тендер с данными анализа
tender = {
    'number': '0160100009425000055',
    'name': 'Электронный аукцион №0160100009425000055 - Поставка бумаги для печати',
    'price_formatted': '299,925.00 ₽',
    'customer': 'ПРОКУРАТУРА САРАТОВСКОЙ ОБЛАСТИ',
    'region': 'Саратов',
    'customer_type': 'Региональный',
    'submission_deadline': '20.11.2025 09:00',
    'published': 'Wed, 12 Nov 2025 08:49:00 GMT',
    'url': '/epz/order/notice/ea20/view/common-info.html?regNumber=0160100009425000055',
    'law': '223-ФЗ',
    'procedure_type': 'Электронный аукцион',
    'stage': 'Подача заявок',
    'analysis': {
        'suitable': True,
        'confidence': 85.0,
        'summary': 'Тендер на поставку бумаги для офисной техники. Требуется 774 пачки бумаги формата A4.',
        'tender_info': analysis_data.get('tender_info', {}),
        'requirements': analysis_data.get('requirements', {}),
        'gaps': analysis_data.get('gaps', []),
        'questions': analysis_data.get('questions', {}),
        'contacts': analysis_data.get('contacts', {})
    }
}

# Создаем поисковые параметры
search_params = {
    'original_query': 'поставка бумаги',
    'expanded_queries': ['поставка бумаги', 'поставка офисной бумаги', 'бумага для печати'],
    'price_min': 10000,
    'price_max': 1000000,
    'time': 82.7
}

# Генерируем HTML
html_content = create_enhanced_html_report([tender], search_params)

# Сохраняем
output_file = reports_dir / 'test_ai_analysis_display.html'
with open(output_file, 'w', encoding='utf-8') as f:
    f.write(html_content)

print(f"✅ Тестовый HTML отчет создан: {output_file}")
print(f"🌐 Открываем в браузере...\n")

# Открываем в браузере
webbrowser.open(f'file://{output_file.absolute()}')

print("✅ Готово!")
