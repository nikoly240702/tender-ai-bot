"""
Генератор HTML отчетов для всех тендеров пользователя.

Создает красивый HTML-файл со всеми тендерами из уведомлений.
"""

import sys
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
import html

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))


def format_price(price: float) -> str:
    """Форматирование цены."""
    if not price:
        return "Не указана"
    return f"{price:,.0f} ₽".replace(',', ' ')


def format_date(date_str: str) -> str:
    """Форматирование даты с русским днем недели."""
    if not date_str:
        return "Не указана"
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))

        # Словарь для перевода дней недели на русский
        weekdays_ru = {
            0: 'Пн',  # Monday
            1: 'Вт',  # Tuesday
            2: 'Ср',  # Wednesday
            3: 'Чт',  # Thursday
            4: 'Пт',  # Friday
            5: 'Сб',  # Saturday
            6: 'Вс'   # Sunday
        }

        weekday_ru = weekdays_ru[dt.weekday()]
        date_str_formatted = dt.strftime('%d.%m.%Y %H:%M')

        return f"{weekday_ru}, {date_str_formatted}"
    except:
        return date_str[:16] if len(date_str) > 16 else date_str


def generate_html_report(
    tenders: List[Dict[str, Any]],
    username: str = "Пользователь",
    total_count: int = None
) -> str:
    """
    Генерация HTML отчета всех тендеров.

    Args:
        tenders: Список тендеров
        username: Имя пользователя
        total_count: Общее количество тендеров (если отображена только часть)

    Returns:
        HTML строка
    """
    if total_count is None:
        total_count = len(tenders)

    # Группировка по фильтрам
    tenders_by_filter = {}
    for tender in tenders:
        filter_name = tender.get('filter_name', 'Без фильтра')
        if filter_name not in tenders_by_filter:
            tenders_by_filter[filter_name] = []
        tenders_by_filter[filter_name].append(tender)

    # HTML шаблон
    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Все мои тендеры - Tender Sniper</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        .header {{
            background: white;
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }}

        .header h1 {{
            color: #667eea;
            font-size: 32px;
            margin-bottom: 10px;
        }}

        .header .subtitle {{
            color: #666;
            font-size: 16px;
        }}

        .stats {{
            display: flex;
            gap: 20px;
            margin-top: 20px;
            flex-wrap: wrap;
        }}

        .stat-card {{
            flex: 1;
            min-width: 150px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px 20px;
            border-radius: 12px;
            text-align: center;
        }}

        .stat-card .value {{
            font-size: 32px;
            font-weight: bold;
            margin-bottom: 5px;
        }}

        .stat-card .label {{
            font-size: 14px;
            opacity: 0.9;
        }}

        .filter-section {{
            background: white;
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }}

        .filter-title {{
            font-size: 24px;
            color: #667eea;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid #f0f0f0;
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .filter-badge {{
            background: #667eea;
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: normal;
        }}

        .tender-card {{
            background: #f8f9fa;
            border-radius: 15px;
            padding: 25px;
            margin-bottom: 15px;
            transition: all 0.3s ease;
            border-left: 4px solid #667eea;
        }}

        .tender-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(102, 126, 234, 0.2);
        }}

        .tender-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 15px;
            gap: 20px;
        }}

        .tender-number {{
            background: #667eea;
            color: white;
            padding: 6px 14px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            white-space: nowrap;
        }}

        .tender-name {{
            font-size: 18px;
            font-weight: 600;
            color: #333;
            margin-bottom: 15px;
            line-height: 1.4;
        }}

        .tender-info {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 12px;
            margin-bottom: 15px;
        }}

        .info-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 14px;
            color: #666;
        }}

        .info-icon {{
            font-size: 18px;
            width: 24px;
        }}

        .price {{
            font-size: 22px;
            font-weight: bold;
            color: #667eea;
            margin: 15px 0;
        }}

        .tender-actions {{
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }}

        .btn {{
            padding: 10px 20px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 600;
            transition: all 0.3s ease;
            display: inline-block;
        }}

        .btn-primary {{
            background: #667eea;
            color: white;
        }}

        .btn-primary:hover {{
            background: #5568d3;
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(102, 126, 234, 0.3);
        }}

        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: #999;
        }}

        .empty-state-icon {{
            font-size: 64px;
            margin-bottom: 20px;
        }}

        .footer {{
            text-align: center;
            color: white;
            margin-top: 40px;
            padding: 20px;
            opacity: 0.9;
        }}

        /* Панель фильтров */
        .filters-panel {{
            background: white;
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }}

        .filters-title {{
            font-size: 20px;
            color: #667eea;
            margin-bottom: 20px;
            font-weight: 600;
        }}

        .filters-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }}

        .filter-group {{
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}

        .filter-label {{
            font-size: 14px;
            font-weight: 600;
            color: #333;
        }}

        .filter-input {{
            padding: 10px 15px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 14px;
            transition: all 0.3s ease;
        }}

        .filter-input:focus {{
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }}

        .filter-select {{
            padding: 10px 15px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 14px;
            background: white;
            cursor: pointer;
            transition: all 0.3s ease;
        }}

        .filter-select:focus {{
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }}

        .filter-actions {{
            display: flex;
            gap: 10px;
            margin-top: 10px;
        }}

        .btn-reset {{
            background: #f0f0f0;
            color: #333;
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
        }}

        .btn-reset:hover {{
            background: #e0e0e0;
        }}

        .results-count {{
            background: #667eea;
            color: white;
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: 600;
            display: inline-block;
        }}

        .tender-card.hidden {{
            display: none;
        }}

        @media (max-width: 768px) {{
            .header h1 {{
                font-size: 24px;
            }}

            .stats {{
                flex-direction: column;
            }}

            .tender-info {{
                grid-template-columns: 1fr;
            }}

            .tender-header {{
                flex-direction: column;
            }}

            .filters-grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Заголовок -->
        <div class="header">
            <h1>🎯 Все мои тендеры</h1>
            <p class="subtitle">Tender Sniper • {html.escape(username)}</p>

            <div class="stats">
                <div class="stat-card">
                    <div class="value">{total_count}</div>
                    <div class="label">Всего тендеров</div>
                </div>
                <div class="stat-card">
                    <div class="value">{len(tenders_by_filter)}</div>
                    <div class="label">Активных фильтров</div>
                </div>
                <div class="stat-card">
                    <div class="value">{len(tenders)}</div>
                    <div class="label">Отображено</div>
                </div>
            </div>
        </div>

        <!-- Панель фильтров -->
        <div class="filters-panel">
            <div class="filters-title">🔍 Фильтры и поиск</div>

            <div class="filters-grid">
                <div class="filter-group">
                    <label class="filter-label">Поиск по названию</label>
                    <input type="text" id="searchInput" class="filter-input" placeholder="Введите ключевые слова...">
                </div>

                <div class="filter-group">
                    <label class="filter-label">Регион</label>
                    <select id="regionFilter" class="filter-select">
                        <option value="">Все регионы</option>
                    </select>
                </div>

                <div class="filter-group">
                    <label class="filter-label">Источник</label>
                    <select id="sourceFilter" class="filter-select">
                        <option value="">Все источники</option>
                        <option value="instant_search">🔍 Мгновенный поиск</option>
                        <option value="automonitoring">🤖 Автомониторинг</option>
                    </select>
                </div>

                <div class="filter-group">
                    <label class="filter-label">Мин. цена (₽)</label>
                    <input type="number" id="minPrice" class="filter-input" placeholder="От...">
                </div>

                <div class="filter-group">
                    <label class="filter-label">Макс. цена (₽)</label>
                    <input type="number" id="maxPrice" class="filter-input" placeholder="До...">
                </div>

                <div class="filter-group">
                    <label class="filter-label">Сортировка</label>
                    <select id="sortBy" class="filter-select">
                        <option value="date-desc">Сначала новые</option>
                        <option value="date-asc">Сначала старые</option>
                        <option value="price-desc">По убыванию цены</option>
                        <option value="price-asc">По возрастанию цены</option>
                    </select>
                </div>

                <div class="filter-group">
                    <label class="filter-label">Фильтр источника</label>
                    <select id="filterSource" class="filter-select">
                        <option value="">Все фильтры</option>
                    </select>
                </div>
            </div>

            <div class="filter-actions">
                <button id="resetFilters" class="btn-reset">🔄 Сбросить фильтры</button>
                <div class="results-count" id="resultsCount">Найдено: {len(tenders)}</div>
            </div>
        </div>
"""

    # Генерируем секции для каждого фильтра
    if tenders:
        for filter_name, filter_tenders in tenders_by_filter.items():
            html_content += f"""
        <!-- Фильтр: {html.escape(filter_name)} -->
        <div class="filter-section">
            <div class="filter-title">
                📋 {html.escape(filter_name)}
                <span class="filter-badge">{len(filter_tenders)} тендеров</span>
            </div>
"""

            for tender in filter_tenders:
                tender_url = tender.get('url', '')
                if tender_url and not tender_url.startswith('http'):
                    tender_url = f"https://zakupki.gov.ru{tender_url}"

                # Подготавливаем данные для фильтрации
                tender_name = tender.get('name', 'Без названия')
                tender_price = tender.get('price', 0) or 0
                tender_region = tender.get('region', 'Не указан')
                tender_date = tender.get('published_date', '')

                tender_source = tender.get('source', 'automonitoring')

                html_content += f"""
            <div class="tender-card"
                 data-name="{html.escape(tender_name.lower())}"
                 data-price="{tender_price}"
                 data-region="{html.escape(tender_region)}"
                 data-filter="{html.escape(filter_name)}"
                 data-source="{html.escape(tender_source)}"
                 data-date="{html.escape(tender_date)}">
                <div class="tender-header">
                    <div class="tender-number">№ {html.escape(tender.get('number', 'N/A'))}</div>
                </div>

                <div class="tender-name">{html.escape(tender.get('name', 'Без названия'))}</div>

                <div class="price">💰 {format_price(tender.get('price'))}</div>

                <div class="tender-info">
                    <div class="info-item">
                        <span class="info-icon">🏢</span>
                        <span>{html.escape(tender.get('customer_name', 'Не указан'))[:60]}</span>
                    </div>
                    <div class="info-item">
                        <span class="info-icon">📍</span>
                        <span>{html.escape(tender.get('region', 'Не указан'))}</span>
                    </div>
                    <div class="info-item">
                        <span class="info-icon">📅</span>
                        <span>Опубликован: {format_date(tender.get('published_date', ''))}</span>
                    </div>
                    <div class="info-item">
                        <span class="info-icon">📮</span>
                        <span>Срок подачи: {format_date(tender.get('submission_deadline', '')) if tender.get('submission_deadline') else 'Не указан'}</span>
                    </div>
                    <div class="info-item">
                        <span class="info-icon">⏰</span>
                        <span>Уведомление: {format_date(tender.get('sent_at', ''))}</span>
                    </div>
                </div>

                <div class="tender-actions">
                    <a href="{html.escape(tender_url)}" class="btn btn-primary" target="_blank">
                        📄 Открыть на zakupki.gov.ru
                    </a>
                </div>
            </div>
"""

            html_content += "        </div>\n"
    else:
        html_content += """
        <div class="filter-section">
            <div class="empty-state">
                <div class="empty-state-icon">📭</div>
                <h2>Пока нет тендеров</h2>
                <p>Создайте фильтры и включите автоматический мониторинг!</p>
            </div>
        </div>
"""

    # Футер
    html_content += f"""
        <div class="footer">
            <p>🤖 Сгенерировано Tender Sniper Bot</p>
            <p>{datetime.now().strftime('%d.%m.%Y %H:%M')}</p>
        </div>
    </div>

    <script>
        // JavaScript для фильтрации и сортировки тендеров
        document.addEventListener('DOMContentLoaded', function() {{
            const searchInput = document.getElementById('searchInput');
            const regionFilter = document.getElementById('regionFilter');
            const sourceFilter = document.getElementById('sourceFilter');
            const minPriceInput = document.getElementById('minPrice');
            const maxPriceInput = document.getElementById('maxPrice');
            const sortBySelect = document.getElementById('sortBy');
            const filterSourceSelect = document.getElementById('filterSource');
            const resetButton = document.getElementById('resetFilters');
            const resultsCount = document.getElementById('resultsCount');
            const tenderCards = Array.from(document.querySelectorAll('.tender-card'));

            // Собираем уникальные регионы и фильтры
            const regions = new Set();
            const filters = new Set();

            tenderCards.forEach(card => {{
                const region = card.dataset.region;
                const filter = card.dataset.filter;
                if (region && region !== 'Не указан') regions.add(region);
                if (filter) filters.add(filter);
            }});

            // Заполняем селекты
            regions.forEach(region => {{
                const option = document.createElement('option');
                option.value = region;
                option.textContent = region;
                regionFilter.appendChild(option);
            }});

            filters.forEach(filter => {{
                const option = document.createElement('option');
                option.value = filter;
                option.textContent = filter;
                filterSourceSelect.appendChild(option);
            }});

            // Функция фильтрации
            function applyFilters() {{
                const searchTerm = searchInput.value.toLowerCase();
                const selectedRegion = regionFilter.value;
                const selectedSource = sourceFilter.value;
                const minPrice = parseFloat(minPriceInput.value) || 0;
                const maxPrice = parseFloat(maxPriceInput.value) || Infinity;
                const selectedFilter = filterSourceSelect.value;

                let visibleCount = 0;
                const visibleCards = [];

                tenderCards.forEach(card => {{
                    const name = card.dataset.name || '';
                    const price = parseFloat(card.dataset.price) || 0;
                    const region = card.dataset.region || '';
                    const source = card.dataset.source || 'automonitoring';
                    const filter = card.dataset.filter || '';

                    let isVisible = true;

                    // Фильтр по поиску
                    if (searchTerm && !name.includes(searchTerm)) {{
                        isVisible = false;
                    }}

                    // Фильтр по региону
                    if (selectedRegion && region !== selectedRegion) {{
                        isVisible = false;
                    }}

                    // Фильтр по источнику
                    if (selectedSource && source !== selectedSource) {{
                        isVisible = false;
                    }}

                    // Фильтр по цене
                    if (price < minPrice || price > maxPrice) {{
                        isVisible = false;
                    }}

                    // Фильтр по источнику
                    if (selectedFilter && filter !== selectedFilter) {{
                        isVisible = false;
                    }}

                    if (isVisible) {{
                        card.classList.remove('hidden');
                        visibleCards.push(card);
                        visibleCount++;
                    }} else {{
                        card.classList.add('hidden');
                    }}
                }});

                // Применяем сортировку к видимым карточкам
                applySorting(visibleCards);

                // Обновляем счетчик
                resultsCount.textContent = `Найдено: ${{visibleCount}}`;
            }}

            // Функция сортировки
            function applySorting(cards) {{
                const sortBy = sortBySelect.value;

                cards.sort((a, b) => {{
                    if (sortBy === 'date-desc') {{
                        return (b.dataset.date || '').localeCompare(a.dataset.date || '');
                    }} else if (sortBy === 'date-asc') {{
                        return (a.dataset.date || '').localeCompare(b.dataset.date || '');
                    }} else if (sortBy === 'price-desc') {{
                        return parseFloat(b.dataset.price || 0) - parseFloat(a.dataset.price || 0);
                    }} else if (sortBy === 'price-asc') {{
                        return parseFloat(a.dataset.price || 0) - parseFloat(b.dataset.price || 0);
                    }}
                    return 0;
                }});

                // Перестраиваем DOM
                cards.forEach(card => {{
                    card.parentNode.appendChild(card);
                }});
            }}

            // Сброс фильтров
            function resetFilters() {{
                searchInput.value = '';
                regionFilter.value = '';
                sourceFilter.value = '';
                minPriceInput.value = '';
                maxPriceInput.value = '';
                sortBySelect.value = 'date-desc';
                filterSourceSelect.value = '';
                applyFilters();
            }}

            // Обработчики событий
            searchInput.addEventListener('input', applyFilters);
            regionFilter.addEventListener('change', applyFilters);
            sourceFilter.addEventListener('change', applyFilters);
            minPriceInput.addEventListener('input', applyFilters);
            maxPriceInput.addEventListener('input', applyFilters);
            sortBySelect.addEventListener('change', applyFilters);
            filterSourceSelect.addEventListener('change', applyFilters);
            resetButton.addEventListener('click', resetFilters);

            // Инициализация при загрузке
            applyFilters();
        }});
    </script>
</body>
</html>
"""

    return html_content


async def generate_all_tenders_html(
    user_id: int,
    username: str = "Пользователь",
    limit: int = 100
) -> Path:
    """
    Генерация HTML файла со всеми тендерами пользователя.

    Args:
        user_id: ID пользователя
        username: Имя пользователя
        limit: Максимальное количество тендеров

    Returns:
        Путь к созданному HTML файлу
    """
    from tender_sniper.database import get_sniper_db

    # Получаем тендеры из БД
    db = await get_sniper_db()
    tenders = await db.get_user_tenders(user_id, limit=limit)

    # Генерируем HTML
    html_content = generate_html_report(tenders, username)

    # Сохраняем в файл
    output_dir = Path(__file__).parent.parent / 'temp_reports'
    output_dir.mkdir(exist_ok=True)

    filename = f"all_tenders_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    output_path = output_dir / filename

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    return output_path
