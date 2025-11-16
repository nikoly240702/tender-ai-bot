"""
Тест нового многоэтапного анализа с scoring system.
Сравнивает старый и новый подходы.
"""

import os
import json
from dotenv import load_dotenv
from src.analyzers.tender_analyzer import TenderAnalyzer
from src.document_processor.text_extractor import TextExtractor

# Загрузка переменных окружения
load_dotenv()

def test_multi_stage_analysis():
    """Тест многоэтапного анализа на реальном тендере."""

    print("=" * 80)
    print("ТЕСТ МНОГОЭТАПНОГО АНАЛИЗА ТЕНДЕРНОЙ ДОКУМЕНТАЦИИ")
    print("=" * 80)

    # Выбираем тендер для теста
    tender_id = "0190200000325014421"
    tender_path = f"./downloaded_tenders/{tender_id}"

    print(f"\n📂 Тестируем на тендере: {tender_id}")

    # Шаг 1: Извлечение документации
    print("\n" + "=" * 80)
    print("ШАГ 1: ИЗВЛЕЧЕНИЕ ДОКУМЕНТАЦИИ")
    print("=" * 80)

    extractor = TextExtractor()

    # Получаем список PDF файлов
    pdf_files = []
    for file in os.listdir(tender_path):
        if file.endswith('.pdf'):
            pdf_files.append(os.path.join(tender_path, file))

    print(f"\nНайдено PDF файлов: {len(pdf_files)}")
    for pdf in pdf_files:
        print(f"  - {os.path.basename(pdf)}")

    # Извлекаем текст из всех PDF
    full_documentation = ""
    for pdf_path in pdf_files:
        print(f"\nИзвлечение: {os.path.basename(pdf_path)}")
        text = extractor.extract_text(pdf_path)
        if text:
            full_documentation += f"\n\n=== {os.path.basename(pdf_path)} ===\n\n{text}"
            print(f"  ✅ Извлечено: {len(text):,} символов")
        else:
            print(f"  ❌ Ошибка извлечения")

    print(f"\n📊 Общий объем документации: {len(full_documentation):,} символов")

    if len(full_documentation) < 100:
        print("❌ Документация слишком короткая или не извлечена")
        return

    # Профиль компании для анализа
    company_profile = {
        "industry": ["Строительство", "ИТ", "Услуги"],
        "regions": ["Москва", "Московская область", "Санкт-Петербург"],
        "annual_revenue": 50_000_000,  # 50 млн руб
        "employees_count": 30,
        "has_certifications": ["ISO 9001"],
        "licenses": ["Строительная лицензия"],
        "max_contract_value": 10_000_000,  # Максимальная сумма контракта
        "min_preparation_days": 5  # Минимум дней на подготовку
    }

    # Шаг 2: НОВЫЙ многоэтапный анализ
    print("\n" + "=" * 80)
    print("ШАГ 2: МНОГОЭТАПНЫЙ АНАЛИЗ (НОВЫЙ ПОДХОД)")
    print("=" * 80)

    analyzer_new = TenderAnalyzer(
        provider="openai",
        api_key=os.getenv("OPENAI_API_KEY"),
        model_premium="gpt-4o",
        model_fast="gpt-4o-mini",
        use_multi_stage=True  # ⭐ Активация нового анализа
    )

    print("\n🚀 Запуск многоэтапного анализа...")
    result_new = analyzer_new.analyze_tender_multi_stage(
        documentation=full_documentation,
        company_profile=company_profile
    )

    # Шаг 3: Вывод результатов
    print("\n" + "=" * 80)
    print("РЕЗУЛЬТАТЫ АНАЛИЗА")
    print("=" * 80)

    # Основная информация
    print("\n📋 ОСНОВНАЯ ИНФОРМАЦИЯ:")
    tender_info = result_new.get('tender_info', {})
    print(f"  Название: {tender_info.get('name', 'N/A')}")
    print(f"  Заказчик: {tender_info.get('customer', 'N/A')}")
    nmck = tender_info.get('nmck') or 0
    print(f"  НМЦК: {nmck:,.2f} руб")
    print(f"  Срок подачи: {tender_info.get('deadline_submission', 'N/A')}")
    print(f"  Срок исполнения: {tender_info.get('deadline_execution', 'N/A')}")

    # Товары/услуги
    products = tender_info.get('products_or_services', [])
    print(f"\n📦 ТОВАРЫ/УСЛУГИ: {len(products)} позиций")
    for i, product in enumerate(products[:3], 1):  # Первые 3
        print(f"  {i}. {product.get('name', 'N/A')}")
        if isinstance(product, dict):
            print(f"     Количество: {product.get('quantity', 'N/A')}")
    if len(products) > 3:
        print(f"  ... и еще {len(products) - 3} позиций")

    # Финансовые условия
    payment_terms = tender_info.get('payment_terms', {})
    print(f"\n💰 ФИНАНСОВЫЕ УСЛОВИЯ:")
    if isinstance(payment_terms, dict):
        print(f"  График платежей: {payment_terms.get('payment_schedule', 'N/A')}")
        print(f"  Аванс: {payment_terms.get('prepayment_percent', 0)}%")
    guarantee_app = tender_info.get('guarantee_application') or 0
    guarantee_contract = tender_info.get('guarantee_contract') or 0
    print(f"  Обеспечение заявки: {guarantee_app:,.2f} руб")
    print(f"  Обеспечение контракта: {guarantee_contract:,.2f} руб")

    # ⭐ SCORING - главная новая функция!
    print("\n" + "=" * 80)
    print("⭐ SCORING SYSTEM - ОЦЕНКА СООТВЕТСТВИЯ")
    print("=" * 80)

    suitability = result_new.get('suitability', {})
    total_score = suitability.get('total_score', 0)
    recommendation = suitability.get('recommendation', 'N/A')
    confidence = suitability.get('confidence', 'N/A')

    # Визуализация оценки
    score_bar = "█" * int(total_score / 5) + "░" * (20 - int(total_score / 5))

    print(f"\n  Общая оценка: {total_score}/100")
    print(f"  [{score_bar}] {total_score}%")
    print(f"  Рекомендация: {recommendation.upper()}")
    print(f"  Уверенность: {confidence}")

    # Детализация оценки
    breakdown = suitability.get('breakdown', {})
    print(f"\n  📊 Детализация оценки:")
    if isinstance(breakdown, dict):
        for criterion, data in breakdown.items():
            if isinstance(data, dict):
                score = data.get('score', 0)
                max_score = data.get('max_score', 100)
                reasoning = data.get('reasoning', 'N/A')
                print(f"\n    {criterion.replace('_', ' ').title()}:")
                print(f"      Баллы: {score}/{max_score}")
                print(f"      Обоснование: {reasoning[:100]}...")

    # Red flags
    red_flags = suitability.get('red_flags', [])
    if red_flags:
        print(f"\n  🚨 КРИТИЧНЫЕ ПРОБЛЕМЫ ({len(red_flags)}):")
        for flag in red_flags:
            print(f"    - {flag}")

    # Валидация
    print("\n" + "=" * 80)
    print("✅ ВАЛИДАЦИЯ РЕЗУЛЬТАТОВ")
    print("=" * 80)

    validation = result_new.get('validation', {})
    quality_score = validation.get('quality_score', 0)
    issues_count = validation.get('issues_count', 0)

    print(f"\n  Качество анализа: {quality_score:.1f}/100")
    print(f"  Найдено проблем: {issues_count}")

    if issues_count > 0:
        issues = validation.get('issues', [])
        print(f"\n  Проблемы:")
        for issue in issues[:5]:  # Первые 5
            severity = issue.get('severity', 'N/A')
            field_name = issue.get('field_name', 'N/A')
            issue_desc = issue.get('issue', 'N/A')
            print(f"    [{severity}] {field_name}: {issue_desc}")

    # Риски
    risks = result_new.get('risks', [])
    if risks:
        print("\n" + "=" * 80)
        print("⚠️  АНАЛИЗ РИСКОВ")
        print("=" * 80)

        print(f"\n  Выявлено рисков: {len(risks)}")
        for risk in risks[:5]:  # Первые 5
            if isinstance(risk, dict):
                print(f"\n    - {risk.get('description', 'N/A')}")
                print(f"      Уровень: {risk.get('level', 'N/A')}")
                print(f"      Митигация: {risk.get('mitigation', 'N/A')}")

    # Итоговая рекомендация
    print("\n" + "=" * 80)
    print("📌 ИТОГОВАЯ РЕКОМЕНДАЦИЯ")
    print("=" * 80)

    if total_score >= 80:
        emoji = "✅"
        text = "ОТЛИЧНЫЙ ТЕНДЕР! Настоятельно рекомендуется участие."
    elif total_score >= 60:
        emoji = "⚠️"
        text = "ПРИЕМЛЕМЫЙ ТЕНДЕР. Рассмотреть возможность участия."
    else:
        emoji = "❌"
        text = "СЛАБОЕ СООТВЕТСТВИЕ. Участие не рекомендуется."

    print(f"\n  {emoji} {text}")
    print(f"\n  Оценка соответствия: {total_score}/100")
    print(f"  Качество анализа: {quality_score:.1f}/100")

    # Сохранение результатов
    print("\n" + "=" * 80)
    print("💾 СОХРАНЕНИЕ РЕЗУЛЬТАТОВ")
    print("=" * 80)

    output_file = f"test_results_{tender_id}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result_new, f, ensure_ascii=False, indent=2)

    print(f"\n  ✅ Результаты сохранены в: {output_file}")
    print(f"  📊 Размер файла: {os.path.getsize(output_file):,} байт")

    print("\n" + "=" * 80)
    print("✅ ТЕСТ ЗАВЕРШЕН")
    print("=" * 80)

    return result_new


if __name__ == "__main__":
    try:
        result = test_multi_stage_analysis()
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
