#!/usr/bin/env python3
"""
Пример использования парсеров для поиска тендеров.
Простой скрипт для быстрого тестирования.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from parsers.zakupki_rss_parser import ZakupkiRSSParser
import json

def main():
    print("\n" + "="*70)
    print("  ПОИСК ТЕНДЕРОВ НА ZAKUPKI.GOV.RU")
    print("="*70 + "\n")

    # Создаем парсер
    parser = ZakupkiRSSParser()

    # Параметры поиска (можно изменить)
    keywords = input("🔍 Введите ключевые слова (или Enter для 'компьютерное оборудование'): ").strip()
    if not keywords:
        keywords = "компьютерное оборудование"

    price_min = input("💰 Минимальная цена (или Enter для 500000): ").strip()
    price_min = int(price_min) if price_min else 500000

    price_max = input("💰 Максимальная цена (или Enter для 5000000): ").strip()
    price_max = int(price_max) if price_max else 5000000

    max_results = input("📊 Максимум результатов (или Enter для 10): ").strip()
    max_results = int(max_results) if max_results else 10

    print("\n" + "─"*70)
    print(f"Поиск: '{keywords}'")
    print(f"Цена: {price_min:,} - {price_max:,} руб")
    print(f"Макс. результатов: {max_results}")
    print("─"*70 + "\n")

    # Выполняем поиск
    tenders = parser.search_tenders_rss(
        keywords=keywords,
        price_min=price_min,
        price_max=price_max,
        max_results=max_results
    )

    if not tenders:
        print("❌ Тендеры не найдены. Попробуйте изменить параметры поиска.")
        return

    print(f"\n✅ Найдено тендеров: {len(tenders)}\n")
    print("="*70 + "\n")

    # Выводим результаты
    for i, tender in enumerate(tenders, 1):
        print(f"{'─'*70}")
        print(f"ТЕНДЕР #{i}")
        print(f"{'─'*70}")
        print(f"📋 Номер:    {tender.get('number', 'N/A')}")
        print(f"📝 Название: {tender.get('name', 'N/A')[:70]}")

        if tender.get('price'):
            print(f"💰 Цена:     {tender.get('price_formatted', 'N/A')}")

        print(f"📅 Дата:     {tender.get('published', 'N/A')}")
        print(f"🔗 URL:      https://zakupki.gov.ru{tender.get('url', 'N/A')}")
        print()

    # Предлагаем сохранить результаты
    save = input("\n💾 Сохранить результаты в JSON? (y/n): ").strip().lower()
    if save == 'y':
        filename = f"tenders_{keywords.replace(' ', '_')}_{len(tenders)}.json"

        # Конвертируем datetime для JSON
        tenders_json = []
        for tender in tenders:
            t = tender.copy()
            if 'published_datetime' in t:
                t['published_datetime'] = str(t['published_datetime'])
            tenders_json.append(t)

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(tenders_json, f, indent=2, ensure_ascii=False)

        print(f"✅ Результаты сохранены в: {filename}")

    print("\n" + "="*70)
    print("Готово! Спасибо за использование.")
    print("="*70 + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Прервано пользователем")
    except Exception as e:
        print(f"\n\n❌ Ошибка: {e}")
