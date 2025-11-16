"""
Тест формирования сообщения для Telegram с объектом закупки.
"""

def test_message_formatting():
    """Имитирует формирование сообщения как в bot/handlers/search.py"""

    # Тестовые данные
    tender = {'number': '0190200000325014421'}
    download_result = {'downloaded': 4}

    # Имитируем analysis_result
    analysis_result = {
        'tender_info': {
            'name': 'на поставку сувенирной продукции с нанесением изображения',
            'customer': 'Государственное казенное учреждение «Служба по охране, контролю и регулированию использования биоресурсов Ямало-Ненецкого автономного округа»',
            'nmck': None
        },
        'analysis_summary': {
            'is_suitable': False,
            'confidence_score': 60
        },
        'gaps': [
            {'issue': 'Отсутствие НМЦК', 'category': 'financial'},
            {'issue': 'Неуказанный срок исполнения', 'category': 'execution'}
        ]
    }

    # Формируем сообщение (копия логики из search.py)
    results_text = "✅ <b>AI-АНАЛИЗ ЗАВЕРШЕН</b>\n\n"
    results_text += f"📄 <b>Тендер:</b> {tender.get('number', 'N/A')}\n"
    results_text += f"📥 <b>Документов:</b> {download_result['downloaded']}\n\n"

    # Получаем данные
    summary = analysis_result.get('analysis_summary') or {}
    tender_info = analysis_result.get('tender_info') or {}
    gaps = analysis_result.get('gaps') or []

    # Общая оценка
    is_suitable = summary.get('is_suitable')
    if is_suitable is not None:
        suitability = "✅ Подходит" if is_suitable else "❌ Не подходит"
        results_text += f"<b>Оценка:</b> {suitability}\n"

    confidence = summary.get('confidence_score') or summary.get('confidence')
    if confidence:
        results_text += f"<b>Уверенность:</b> {confidence:.0f}%\n\n"

    # ⭐ НОВОЕ: Объект закупки (название тендера)
    if tender_info and tender_info != {}:
        tender_name = tender_info.get('name', '')
        if tender_name and tender_name != 'N/A':
            if len(tender_name) > 150:
                tender_name = tender_name[:147] + "..."
            results_text += f"<b>📦 Объект закупки:</b>\n{tender_name}\n\n"

    # Информация о заказчике
    if tender_info and tender_info != {}:
        customer = tender_info.get('customer', '')
        if customer and customer != 'N/A':
            if len(customer) > 100:
                customer = customer[:97] + "..."
            results_text += f"<b>🏢 Заказчик:</b> {customer}\n\n"

    # Пробелы
    if gaps and len(gaps) > 0:
        results_text += f"<b>⚠️ Пробелы в документации ({len(gaps)}):</b>\n"
        for i, gap in enumerate(gaps[:3], 1):
            if isinstance(gap, dict):
                gap_text = gap.get('issue', 'Пробел в документации')
                category = gap.get('category', '')
                if category:
                    gap_text = f"[{category.capitalize()}] {gap_text}"
            else:
                gap_text = str(gap)

            if len(gap_text) > 80:
                gap_text = gap_text[:77] + "..."
            results_text += f"{i}. {gap_text}\n"

    return results_text


if __name__ == "__main__":
    print("=" * 80)
    print("ТЕСТ ФОРМИРОВАНИЯ СООБЩЕНИЯ ДЛЯ TELEGRAM")
    print("=" * 80)
    print()

    message = test_message_formatting()

    print(message)

    print()
    print("=" * 80)
    print("✅ ПРОВЕРКА:")
    print("=" * 80)

    # Проверяем, что объект закупки присутствует
    if "📦 Объект закупки:" in message:
        print("✅ Объект закупки отображается")
    else:
        print("❌ Объект закупки НЕ отображается")

    if "на поставку сувенирной продукции" in message:
        print("✅ Название тендера присутствует")
    else:
        print("❌ Название тендера отсутствует")

    if "🏢 Заказчик:" in message:
        print("✅ Заказчик отображается")
    else:
        print("❌ Заказчик НЕ отображается")
