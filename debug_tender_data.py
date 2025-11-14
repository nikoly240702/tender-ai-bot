#!/usr/bin/env python3
"""
Отладочный скрипт для проверки извлеченных данных.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from parsers.zakupki_enhanced_parser import ZakupkiEnhancedParser
from analyzers.tender_analyzer import TenderAnalyzer
from utils.config_loader import ConfigLoader
import json

# Инициализация
config = ConfigLoader()
llm_config = config.get_llm_config()

tender_analyzer = TenderAnalyzer(
    api_key=llm_config['api_key'],
    provider=llm_config['provider'],
    model_fast=llm_config.get('model_fast')
)

enhanced_parser = ZakupkiEnhancedParser(tender_analyzer.llm)

# Ищем 1 тендер
print("\n🔍 Получение тендера для анализа...\n")

tenders = enhanced_parser.search_with_details(
    keywords="компьютерное оборудование",
    price_min=500000,
    price_max=5000000,
    max_results=1,
    extract_details=False
)

if not tenders:
    print("❌ Тендеры не найдены")
    sys.exit(1)

tender = tenders[0]

print("="*70)
print(f"ТЕНДЕР: {tender.get('number')}")
print("="*70)

# Выводим все поля
print("\n📊 ВСЕ ИЗВЛЕЧЕННЫЕ ПОЛЯ:\n")

important_fields = [
    ('number', '📋 Номер'),
    ('name', '📝 Название'),
    ('price_formatted', '💰 Цена'),
    ('customer', '🏢 Заказчик'),
    ('region', '📍 Регион'),
    ('customer_type', '🏛️ Тип заказчика'),
    ('submission_deadline', '⏰ Срок подачи заявок'),
    ('winner_determination_date', '🏆 Определение победителя'),
    ('law', '📜 Закон'),
    ('procedure_type', '🔖 Тип процедуры'),
    ('stage', '⏱️ Этап'),
    ('placement_date', '📅 Размещено'),
    ('update_date', '🔄 Обновлено'),
    ('okpd_codes', '🏷️ ОКПД2'),
    ('ikz', '🔖 ИКЗ'),
    ('payment_terms', '💳 Условия оплаты'),
    ('quantity_info', '📦 Количество'),
]

for field, label in important_fields:
    value = tender.get(field)
    if value:
        if isinstance(value, list):
            value = ', '.join(value)
        print(f"{label}: {value}")
    else:
        print(f"{label}: ❌ НЕ ИЗВЛЕЧЕНО")

# Смотрим на summary (там может быть информация)
print("\n" + "="*70)
print("СЫРЫЕ ДАННЫЕ ИЗ RSS (SUMMARY):")
print("="*70)
summary = tender.get('summary', '')
if summary:
    print(summary[:1000])  # Первые 1000 символов
    print("\n... (обрезано)")
else:
    print("❌ Summary пустой")

# Сохраняем полный тендер в JSON
output_file = Path(__file__).parent / 'output' / 'debug_tender.json'
output_file.parent.mkdir(parents=True, exist_ok=True)

with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(tender, f, indent=2, ensure_ascii=False, default=str)

print(f"\n💾 Полные данные сохранены в: {output_file}")
