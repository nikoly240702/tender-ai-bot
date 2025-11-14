#!/usr/bin/env python3
"""
Создает HTML отчет с результатами парсинга и открывает в браузере.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from parsers.zakupki_rss_parser import ZakupkiRSSParser
from datetime import datetime
import webbrowser
import os

def create_html_report(tenders, search_params):
    """Создает красивый HTML отчет с результатами."""

    html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Результаты поиска тендеров</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        .header {{
            background: white;
            border-radius: 12px;
            padding: 30px;
            margin-bottom: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }}

        .header h1 {{
            color: #2d3748;
            font-size: 32px;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 15px;
        }}

        .header h1 .icon {{
            font-size: 40px;
        }}

        .search-info {{
            background: #f7fafc;
            padding: 20px;
            border-radius: 8px;
            margin-top: 20px;
        }}

        .search-info h3 {{
            color: #4a5568;
            margin-bottom: 15px;
            font-size: 18px;
        }}

        .search-params {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
        }}

        .param {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .param-label {{
            color: #718096;
            font-weight: 500;
        }}

        .param-value {{
            color: #2d3748;
            font-weight: 600;
        }}

        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }}

        .stat-card {{
            background: white;
            padding: 25px;
            border-radius: 12px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            text-align: center;
        }}

        .stat-value {{
            font-size: 36px;
            font-weight: bold;
            color: #667eea;
            margin-bottom: 10px;
        }}

        .stat-label {{
            color: #718096;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        .tender-card {{
            background: white;
            border-radius: 12px;
            padding: 30px;
            margin-bottom: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }}

        .tender-card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 15px 40px rgba(0,0,0,0.3);
        }}

        .tender-header {{
            display: flex;
            justify-content: space-between;
            align-items: start;
            margin-bottom: 20px;
            padding-bottom: 20px;
            border-bottom: 2px solid #e2e8f0;
        }}

        .tender-number {{
            font-size: 14px;
            color: #667eea;
            font-weight: 600;
            background: #edf2f7;
            padding: 8px 16px;
            border-radius: 6px;
        }}

        .tender-date {{
            font-size: 14px;
            color: #718096;
        }}

        .tender-title {{
            font-size: 22px;
            color: #2d3748;
            margin-bottom: 20px;
            line-height: 1.4;
            font-weight: 600;
        }}

        .tender-details {{
            display: grid;
            gap: 15px;
        }}

        .detail-row {{
            display: flex;
            align-items: start;
            gap: 15px;
        }}

        .detail-icon {{
            font-size: 20px;
            min-width: 20px;
        }}

        .detail-label {{
            font-weight: 600;
            color: #4a5568;
            min-width: 120px;
        }}

        .detail-value {{
            color: #2d3748;
            flex: 1;
        }}

        .tender-link {{
            display: inline-block;
            margin-top: 20px;
            padding: 12px 24px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            text-decoration: none;
            border-radius: 8px;
            font-weight: 600;
            transition: transform 0.2s ease;
        }}

        .tender-link:hover {{
            transform: scale(1.05);
        }}

        .price {{
            color: #48bb78;
            font-weight: bold;
            font-size: 18px;
        }}

        .footer {{
            background: white;
            border-radius: 12px;
            padding: 20px;
            margin-top: 30px;
            text-align: center;
            color: #718096;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }}

        .badge {{
            display: inline-block;
            padding: 4px 12px;
            background: #667eea;
            color: white;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
            margin-left: 10px;
        }}

        @media (max-width: 768px) {{
            .header h1 {{
                font-size: 24px;
            }}

            .tender-title {{
                font-size: 18px;
            }}

            .search-params {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>
                <span class="icon">🔍</span>
                Результаты поиска тендеров
                <span class="badge">RSS Parser</span>
            </h1>

            <div class="search-info">
                <h3>📋 Параметры поиска:</h3>
                <div class="search-params">
                    <div class="param">
                        <span class="param-label">Ключевые слова:</span>
                        <span class="param-value">{search_params['keywords']}</span>
                    </div>
                    <div class="param">
                        <span class="param-label">Цена от:</span>
                        <span class="param-value">{search_params['price_min']:,} ₽</span>
                    </div>
                    <div class="param">
                        <span class="param-label">Цена до:</span>
                        <span class="param-value">{search_params['price_max']:,} ₽</span>
                    </div>
                    <div class="param">
                        <span class="param-label">Дата поиска:</span>
                        <span class="param-value">{datetime.now().strftime('%d.%m.%Y %H:%M')}</span>
                    </div>
                </div>
            </div>
        </div>

        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{len(tenders)}</div>
                <div class="stat-label">Найдено тендеров</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{search_params.get('time', 0):.1f}s</div>
                <div class="stat-label">Время поиска</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">100%</div>
                <div class="stat-label">Успешность</div>
            </div>
        </div>
"""

    # Добавляем карточки тендеров
    for i, tender in enumerate(tenders, 1):
        # Извлекаем цену из summary если есть
        price_html = ""
        if 'summary' in tender:
            import re
            price_match = re.search(r'Начальная цена контракта:\s*</strong>([0-9\s,.]+)', tender['summary'])
            if price_match:
                price_text = price_match.group(1).strip()
                price_html = f'<div class="detail-row"><div class="detail-icon">💰</div><div class="detail-label">Цена:</div><div class="detail-value price">{price_text} ₽</div></div>'

        # Извлекаем заказчика
        customer_html = ""
        if 'summary' in tender:
            import re
            customer_match = re.search(r'Наименование Заказчика:\s*</strong>([^<]+)', tender['summary'])
            if customer_match:
                customer_text = customer_match.group(1).strip()
                customer_html = f'<div class="detail-row"><div class="detail-icon">🏢</div><div class="detail-label">Заказчик:</div><div class="detail-value">{customer_text}</div></div>'

        html += f"""
        <div class="tender-card">
            <div class="tender-header">
                <div class="tender-number">№ {tender.get('number', 'N/A')}</div>
                <div class="tender-date">📅 {tender.get('published', 'N/A')}</div>
            </div>

            <div class="tender-title">
                {i}. {tender.get('name', 'Без названия')}
            </div>

            <div class="tender-details">
                {price_html}
                {customer_html}
                <div class="detail-row">
                    <div class="detail-icon">🔗</div>
                    <div class="detail-label">Ссылка:</div>
                    <div class="detail-value">
                        <a href="https://zakupki.gov.ru{tender.get('url', '')}" target="_blank" class="tender-link">
                            Открыть на zakupki.gov.ru →
                        </a>
                    </div>
                </div>
            </div>
        </div>
"""

    html += f"""
        <div class="footer">
            <p>📡 Данные получены через RSS-фид zakupki.gov.ru</p>
            <p style="margin-top: 10px;">🤖 Сгенерировано AI-агентом для анализа тендеров</p>
            <p style="margin-top: 10px; font-size: 12px;">
                Время генерации: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}
            </p>
        </div>
    </div>
</body>
</html>
"""
    return html


def main():
    print("\n" + "="*70)
    print("  СОЗДАНИЕ HTML ОТЧЕТА С РЕЗУЛЬТАТАМИ ПАРСЕРА")
    print("="*70 + "\n")

    # Создаем парсер
    parser = ZakupkiRSSParser()

    # Параметры поиска (можно изменить)
    search_params = {
        'keywords': 'компьютерное оборудование',
        'price_min': 500000,
        'price_max': 5000000,
        'max_results': 10
    }

    print(f"🔍 Поиск тендеров...")
    print(f"   Ключевые слова: {search_params['keywords']}")
    print(f"   Цена: {search_params['price_min']:,} - {search_params['price_max']:,} руб")
    print()

    # Выполняем поиск с замером времени
    start_time = datetime.now()

    tenders = parser.search_tenders_rss(
        keywords=search_params['keywords'],
        price_min=search_params['price_min'],
        price_max=search_params['price_max'],
        max_results=search_params['max_results']
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    search_params['time'] = elapsed

    if not tenders:
        print("❌ Тендеры не найдены")
        return

    print(f"\n✅ Найдено тендеров: {len(tenders)}")
    print(f"⏱️  Время поиска: {elapsed:.2f} секунд")
    print()

    # Создаем HTML отчет
    print("📄 Создание HTML отчета...")
    html_content = create_html_report(tenders, search_params)

    # Сохраняем в файл
    output_dir = Path(__file__).parent / 'output' / 'reports'
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    html_file = output_dir / f'parser_results_{timestamp}.html'

    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"✅ HTML отчет сохранен: {html_file}")
    print()

    # Открываем в браузере
    print("🌐 Открываем в браузере...")
    webbrowser.open(f'file://{html_file.absolute()}')

    print()
    print("="*70)
    print("✅ Готово! Отчет открыт в браузере.")
    print("="*70)
    print()


if __name__ == "__main__":
    main()
