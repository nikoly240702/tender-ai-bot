#!/usr/bin/env python3
"""
Quick test script to generate a report with the new design
"""
from smart_tender_search import create_enhanced_html_report
from datetime import datetime
from pathlib import Path
import webbrowser

# Sample tender data
sample_tenders = [
    {
        'number': '0337500001425012203',
        'name': 'Поставка медицинских расходных материалов для нужд ГБУЗ',
        'price': 46390000,
        'price_formatted': '46 390 000 ₽',
        'customer': 'Государственное бюджетное учреждение здравоохранения',
        'region': 'Москва',
        'law': '223-ФЗ',
        'procedure_type': 'Электронный аукцион',
        'stage': 'Подача заявок',
        'customer_type': 'Медицина',
        'submission_deadline': '25.11.2025 10:00',
        'winner_determination_date': '28.11.2025',
        'published': '13.11.2025',
        'ikz': '251180099910000000011',
        'okpd_codes': ['32.50.50.190', '32.50.50.110'],
        'url': '/epz/order/notice/ea223/view/common-info.html?regNumber=32415123456',
        'payment_terms': 'Оплата в течение 30 дней после поставки',
        'quantity_info': 'Согласно спецификации',
    },
    {
        'number': '0373100123456789012',
        'name': 'Поставка офисной мебели и оборудования',
        'price': 5200000,
        'price_formatted': '5 200 000 ₽',
        'customer': 'Администрация города',
        'region': 'Санкт-Петербург',
        'law': '44-ФЗ',
        'procedure_type': 'Открытый конкурс',
        'stage': 'Прием заявок',
        'customer_type': 'Муниципальное образование',
        'submission_deadline': '20.11.2025 15:00',
        'published': '12.11.2025',
        'ikz': '251180099910000000012',
        'url': '/223/purchase/public/purchase/info/common-info.html?regNumber=123456789',
    },
    {
        'number': '0373100987654321098',
        'name': 'Разработка и внедрение информационной системы',
        'price': 15800000,
        'price_formatted': '15 800 000 ₽',
        'customer': 'Министерство цифрового развития',
        'region': 'Москва',
        'law': '44-ФЗ',
        'procedure_type': 'Электронный аукцион',
        'stage': 'Подача заявок',
        'customer_type': 'Федеральное ведомство',
        'submission_deadline': '30.11.2025 12:00',
        'published': '13.11.2025',
        'ikz': '251180099910000000013',
        'url': '/epz/order/notice/ea44/view/common-info.html?regNumber=0373100987654321098',
    }
]

# Sample search params
search_params = {
    'original_query': 'медицинские расходные материалы',
    'expanded_queries': [
        'медицинские расходные материалы',
        'медицинские изделия одноразовые',
        'расходники для медучреждений',
        'медицинский инвентарь',
    ],
    'price_min': 500000,
    'price_max': 50000000,
    'regions': ['Москва', 'Санкт-Петербург'],
    'time': 12.5,
}

print("🎨 Генерация отчета с новым дизайном...")

# Generate HTML
html_content = create_enhanced_html_report(sample_tenders, search_params)

# Save report
output_dir = Path(__file__).parent / 'output' / 'reports'
output_dir.mkdir(parents=True, exist_ok=True)

timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
html_file = output_dir / f'redesigned_report_{timestamp}.html'

with open(html_file, 'w', encoding='utf-8') as f:
    f.write(html_content)

print(f"✅ Отчет сохранен: {html_file}")
print(f"📄 Размер: {len(html_content):,} символов")
print("\n🌐 Открываю отчет в браузере...")

# Open in browser
webbrowser.open(f'file://{html_file.absolute()}')

print("\n✨ Готово! Проверьте новый дизайн в браузере.")
