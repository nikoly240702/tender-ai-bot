#!/usr/bin/env python3
"""
Упрощенный полный анализ тендеров на поставку бумаги.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from integrated_tender_system import IntegratedTenderSystem
from datetime import datetime

def main():
    print("\n" + "="*70)
    print("  ПОЛНЫЙ АНАЛИЗ ТЕНДЕРОВ НА ПОСТАВКУ БУМАГИ")
    print("="*70 + "\n")

    # Параметры поиска (упрощенные)
    search_query = "поставка бумаги"
    price_min = 10000
    price_max = 1000000
    max_tenders = 5

    print(f"🎯 ПАРАМЕТРЫ ПОИСКА:")
    print(f"   📝 Запрос: {search_query}")
    print(f"   💰 Цена: {price_min:,} - {price_max:,} руб")
    print(f"   🎯 Количество: {max_tenders} тендеров")
    print(f"   🤖 Анализ: ПОЛНЫЙ (с AI)\n")

    # Создаем систему
    system = IntegratedTenderSystem()

    # Запускаем полный цикл с анализом
    result = system.search_and_analyze(
        search_query=search_query,
        price_min=price_min,
        price_max=price_max,
        max_tenders=max_tenders,
        regions=None,  # Без фильтра по региону
        download_documents=True,
        analyze_documents=True  # ВКЛЮЧАЕМ ПОЛНЫЙ AI АНАЛИЗ
    )

    # Итоговая статистика
    print("\n" + "="*70)
    print("  ИТОГОВАЯ СТАТИСТИКА")
    print("="*70)

    suitable_count = sum(
        1 for r in result['results']
        if r.get('analysis_result') and
           r['analysis_result'].get('analysis_summary', {}).get('is_suitable')
    )

    print(f"\n🔍 Найдено тендеров: {result['tenders_found']}")
    print(f"📥 Скачано документов: {sum(len(r['documents_downloaded']) for r in result['results'])}")
    print(f"🤖 Проанализировано: {result['tenders_analyzed']}")
    print(f"✅ Подходит для участия: {suitable_count}")
    print(f"⏱️  Общее время: {result['search_params']['time']:.1f} сек")

    # Выводим краткие результаты по каждому тендеру
    print("\n📊 РЕЗУЛЬТАТЫ АНАЛИЗА:\n")

    for i, r in enumerate(result['results'], 1):
        tender = r['tender_info']
        analysis = r.get('analysis_result', {})

        print(f"{i}. {tender.get('number')}")
        print(f"   📝 {tender.get('name', 'N/A')[:60]}...")
        print(f"   💰 {tender.get('price_formatted', 'N/A')}")

        if analysis and analysis.get('analysis_summary'):
            summary = analysis['analysis_summary']
            suitable = "✅ ПОДХОДИТ" if summary.get('is_suitable') else "❌ НЕ ПОДХОДИТ"
            confidence = summary.get('confidence_score', 0)
            print(f"   {suitable} (уверенность: {confidence:.0f}%)")

            if summary.get('summary_text'):
                print(f"   💬 {summary['summary_text'][:80]}...")
        else:
            print(f"   ⚠️  Анализ не выполнен")

        print()

    print("="*70 + "\n")
    print("✅ АНАЛИЗ ЗАВЕРШЕН")
    print(f"📄 HTML отчет открыт в браузере")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
