#!/usr/bin/env python3
"""
Умный поиск тендеров с расширением запросов и детальным анализом.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from parsers.zakupki_enhanced_parser import ZakupkiEnhancedParser
from parsers.smart_search_expander import SmartSearchExpander
from analyzers.tender_analyzer import TenderAnalyzer
from utils.config_loader import ConfigLoader
from datetime import datetime
import webbrowser
import json

def create_analysis_html(analysis):
    """Генерирует HTML для секции AI-анализа с современным дизайном."""
    if not analysis:
        return ''

    html = '<div class="analysis-section">'

    # Header with badge
    html += '<div class="analysis-header">'
    html += '<div class="analysis-title">🤖 AI Анализ тендера</div>'

    # Suitable/Not suitable badge
    if analysis.get('suitable') is not None:
        badge_class = 'suitable' if analysis['suitable'] else 'not-suitable'
        badge_text = '✓ ПОДХОДИТ' if analysis['suitable'] else '✗ НЕ ПОДХОДИТ'
        html += f'<span class="analysis-badge {badge_class}">{badge_text}</span>'

    html += '</div>'

    # Products/Services
    tender_info = analysis.get('tender_info', {})
    products = tender_info.get('products_or_services', [])
    if products:
        html += '<div class="analysis-content">'
        html += '<h4>📦 Товары и услуги</h4>'
        for product in products[:3]:
            html += '<div class="analysis-item">'
            html += f'<div class="analysis-item-title">{product.get("name", "N/A")}</div>'

            details = []
            if product.get('quantity') and product.get('unit'):
                details.append(f'Количество: {product["quantity"]} {product["unit"]}')

            specs = product.get('specifications', {})
            if specs:
                for key, value in list(specs.items())[:2]:
                    details.append(f'{key}: {value}')

            if details:
                html += f'<div class="analysis-item-text">{" • ".join(details)}</div>'

            html += '</div>'
        html += '</div>'

    # Requirements
    requirements = analysis.get('requirements', {})
    if requirements and any(requirements.values()):
        html += '<div class="requirements-grid">'
        req_labels = {
            'technical': '🔧 Технические',
            'qualification': '🎓 Квалификационные',
            'financial': '💰 Финансовые',
            'documentation': '📄 Документация'
        }
        for req_type, req_list in requirements.items():
            if req_list:
                html += '<div class="requirement-box">'
                html += f'<h5>{req_labels.get(req_type, req_type)}</h5>'
                html += '<ul>'
                for req in req_list[:3]:
                    html += f'<li>{req}</li>'
                html += '</ul>'
                html += '</div>'
        html += '</div>'

    # Gaps
    gaps = analysis.get('gaps', [])
    if gaps:
        critical_gaps = [g for g in gaps if g.get('criticality') == 'CRITICAL']
        high_gaps = [g for g in gaps if g.get('criticality') == 'HIGH']
        medium_gaps = [g for g in gaps if g.get('criticality') == 'MEDIUM']

        if critical_gaps or high_gaps or medium_gaps:
            html += '<div class="analysis-content">'
            html += f'<h4>⚠️ Обнаружено рисков: {len(gaps)}</h4>'

            # Critical gaps
            for gap in critical_gaps[:2]:
                html += '<div class="gap-item critical">'
                html += '<div class="gap-criticality">🔴 Критический</div>'
                html += f'<div class="gap-issue">{gap.get("issue", "N/A")}</div>'
                if gap.get('impact'):
                    html += f'<div class="gap-impact">Влияние: {gap["impact"]}</div>'
                html += '</div>'

            # High gaps
            for gap in high_gaps[:2]:
                html += '<div class="gap-item high">'
                html += '<div class="gap-criticality">🟡 Высокий</div>'
                html += f'<div class="gap-issue">{gap.get("issue", "N/A")}</div>'
                if gap.get('impact'):
                    html += f'<div class="gap-impact">Влияние: {gap["impact"]}</div>'
                html += '</div>'

            # Medium gaps
            for gap in medium_gaps[:1]:
                html += '<div class="gap-item medium">'
                html += '<div class="gap-criticality">🟠 Средний</div>'
                html += f'<div class="gap-issue">{gap.get("issue", "N/A")}</div>'
                if gap.get('impact'):
                    html += f'<div class="gap-impact">Влияние: {gap["impact"]}</div>'
                html += '</div>'

            html += '</div>'

    # Questions for customer
    questions = analysis.get('questions', {})
    critical_q = questions.get('critical', [])
    important_q = questions.get('important', [])

    if critical_q or important_q:
        html += '<div class="analysis-content">'
        html += '<h4>❓ Вопросы для уточнения</h4>'

        if critical_q:
            for q in critical_q[:2]:
                html += '<div class="analysis-item">'
                html += f'<div class="analysis-item-title">🔴 Критический вопрос</div>'
                html += f'<div class="analysis-item-text">{q}</div>'
                html += '</div>'

        if important_q:
            for q in important_q[:2]:
                html += '<div class="analysis-item">'
                html += f'<div class="analysis-item-title">🟡 Важный вопрос</div>'
                html += f'<div class="analysis-item-text">{q}</div>'
                html += '</div>'

        html += '</div>'

    html += '</div>'
    return html


def create_enhanced_html_report(all_tenders, search_params):
    """Создает улучшенный HTML отчет с современным дизайном."""

    # Вычисляем дополнительные метрики
    total_price = sum(tender.get('price', 0) for tender in all_tenders if tender.get('price'))
    avg_price = total_price / len(all_tenders) if all_tenders else 0
    law_223_count = sum(1 for tender in all_tenders if tender.get('law') == '223-ФЗ')
    law_223_percent = (law_223_count / len(all_tenders) * 100) if all_tenders else 0

    html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Поиск тендеров - {search_params['original_query']}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        /* CSS Custom Properties - Design System */
        :root {{
            /* Primary Colors */
            --color-primary: #5B8CDE;
            --color-primary-dark: #2D3E5F;
            --color-primary-light: #B8D4F1;

            /* Background Colors */
            --color-bg-main: #F7F9FC;
            --color-bg-card: #FFFFFF;
            --color-bg-alt: #EEF3F9;

            /* Data Colors */
            --color-money: #10B981;
            --color-warning: #F59E0B;
            --color-error: #EF4444;
            --color-neutral: #6B7280;

            /* Text Colors */
            --color-text-heading: #1F2937;
            --color-text-primary: #374151;
            --color-text-secondary: #6B7280;
            --color-text-meta: #9CA3AF;

            /* Shadows */
            --shadow-card: 0 1px 3px rgba(0, 0, 0, 0.08), 0 1px 2px rgba(0, 0, 0, 0.06);
            --shadow-card-hover: 0 4px 12px rgba(0, 0, 0, 0.1), 0 2px 6px rgba(0, 0, 0, 0.06);
            --shadow-ai: 0 2px 8px rgba(59, 130, 246, 0.12);

            /* Spacing */
            --spacing-xs: 8px;
            --spacing-sm: 12px;
            --spacing-md: 16px;
            --spacing-lg: 24px;
            --spacing-xl: 32px;

            /* Border Radius */
            --radius-sm: 6px;
            --radius-md: 8px;
            --radius-lg: 12px;
            --radius-full: 16px;
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--color-bg-main);
            color: var(--color-text-primary);
            padding: var(--spacing-lg);
            min-height: 100vh;
            line-height: 1.6;
        }}

        .container {{
            max-width: 1280px;
            margin: 0 auto;
        }}

        /* Header Section */
        .header {{
            background: var(--color-bg-card);
            border-radius: var(--radius-lg);
            padding: var(--spacing-xl);
            margin-bottom: var(--spacing-lg);
            box-shadow: var(--shadow-card);
        }}

        .header-top {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: var(--spacing-lg);
            flex-wrap: wrap;
            gap: var(--spacing-md);
        }}

        .header h1 {{
            color: var(--color-text-heading);
            font-size: 32px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: var(--spacing-md);
            margin: 0;
        }}

        .badge-ai {{
            display: inline-block;
            padding: 6px 16px;
            background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-dark) 100%);
            color: white;
            border-radius: var(--radius-full);
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .search-params {{
            background: var(--color-bg-main);
            padding: var(--spacing-lg);
            border-radius: var(--radius-md);
            margin-top: var(--spacing-md);
        }}

        .search-params h3 {{
            color: var(--color-text-heading);
            font-size: 16px;
            font-weight: 600;
            margin-bottom: var(--spacing-md);
        }}

        .search-params .query {{
            color: var(--color-primary);
            font-weight: 600;
        }}

        .expanded-queries-box {{
            background: var(--color-bg-alt);
            padding: var(--spacing-md);
            border-radius: var(--radius-md);
            margin-top: var(--spacing-md);
            border-left: 3px solid var(--color-primary);
        }}

        .expanded-queries-box h4 {{
            color: var(--color-text-secondary);
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: var(--spacing-sm);
            display: flex;
            align-items: center;
            gap: var(--spacing-xs);
        }}

        .query-tags {{
            display: flex;
            flex-wrap: wrap;
            gap: var(--spacing-xs);
        }}

        .query-tag {{
            display: inline-block;
            padding: 6px 14px;
            background: white;
            border: 2px solid var(--color-primary);
            color: var(--color-primary);
            border-radius: var(--radius-full);
            font-size: 13px;
            font-weight: 500;
        }}

        .params-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: var(--spacing-md);
            margin-top: var(--spacing-md);
        }}

        .param-item {{
            display: flex;
            align-items: center;
            gap: var(--spacing-xs);
            font-size: 14px;
        }}

        .param-label {{
            color: var(--color-text-secondary);
            font-weight: 500;
        }}

        .param-value {{
            color: var(--color-text-heading);
            font-weight: 600;
        }}

        /* Stats Bar */
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: var(--spacing-md);
            margin-bottom: var(--spacing-lg);
        }}

        .stat-card {{
            background: var(--color-bg-card);
            padding: var(--spacing-lg);
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow-card);
            text-align: center;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }}

        .stat-card:hover {{
            box-shadow: var(--shadow-card-hover);
            transform: translateY(-2px);
        }}

        .stat-icon {{
            font-size: 24px;
            margin-bottom: var(--spacing-sm);
        }}

        .stat-value {{
            font-size: 40px;
            font-weight: 700;
            color: var(--color-primary);
            margin-bottom: var(--spacing-xs);
            line-height: 1;
        }}

        .stat-label {{
            color: var(--color-text-secondary);
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 500;
        }}

        /* Tender Cards */
        .tender-card {{
            background: var(--color-bg-card);
            border-radius: var(--radius-lg);
            padding: var(--spacing-lg);
            margin-bottom: var(--spacing-lg);
            box-shadow: var(--shadow-card);
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }}

        .tender-card:hover {{
            box-shadow: var(--shadow-card-hover);
        }}

        .tender-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: var(--spacing-md);
            border-bottom: 2px solid var(--color-bg-alt);
            margin-bottom: var(--spacing-md);
            flex-wrap: wrap;
            gap: var(--spacing-sm);
        }}

        .tender-number {{
            font-size: 14px;
            color: var(--color-primary);
            font-weight: 600;
            background: var(--color-bg-alt);
            padding: 8px 16px;
            border-radius: var(--radius-sm);
            font-family: 'Courier New', monospace;
        }}

        .tender-date {{
            font-size: 13px;
            color: var(--color-text-meta);
            font-weight: 500;
        }}

        .tender-title {{
            font-size: 18px;
            font-weight: 600;
            color: var(--color-text-heading);
            line-height: 1.6;
            margin-bottom: var(--spacing-md);
        }}

        /* Tags */
        .tags {{
            display: flex;
            flex-wrap: wrap;
            gap: var(--spacing-xs);
            margin-bottom: var(--spacing-md);
        }}

        .tag {{
            padding: 6px 12px;
            border-radius: var(--radius-full);
            font-size: 12px;
            font-weight: 600;
        }}

        .tag.law {{
            background: #DBEAFE;
            color: #1E40AF;
        }}

        .tag.type {{
            background: #D1FAE5;
            color: #065F46;
        }}

        .tag.stage {{
            background: #FEE2E2;
            color: #991B1B;
        }}

        .tag.region {{
            background: #FED7AA;
            color: #9A3412;
        }}

        .tag.customer {{
            background: #E9D5FF;
            color: #6B21A8;
        }}

        /* Meta Grid (2x3) */
        .tender-meta {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: var(--spacing-md);
            margin-bottom: var(--spacing-md);
        }}

        .meta-item {{
            background: var(--color-bg-main);
            padding: var(--spacing-md);
            border-radius: var(--radius-md);
        }}

        .meta-header {{
            display: flex;
            align-items: center;
            gap: var(--spacing-xs);
            margin-bottom: var(--spacing-xs);
        }}

        .meta-icon {{
            font-size: 18px;
        }}

        .meta-label {{
            font-size: 11px;
            color: var(--color-text-meta);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-weight: 600;
        }}

        .meta-value {{
            color: var(--color-text-heading);
            font-weight: 600;
            font-size: 14px;
            word-wrap: break-word;
        }}

        .meta-value.price {{
            color: var(--color-money);
            font-size: 20px;
            font-weight: 700;
        }}

        /* AI Analysis Block */
        .analysis-section {{
            background: #EEF6FF;
            border-left: 4px solid #3B82F6;
            border-radius: var(--radius-lg);
            padding: var(--spacing-lg);
            margin-top: var(--spacing-md);
            box-shadow: var(--shadow-ai);
        }}

        .analysis-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: var(--spacing-md);
            flex-wrap: wrap;
            gap: var(--spacing-sm);
        }}

        .analysis-title {{
            display: flex;
            align-items: center;
            gap: var(--spacing-sm);
            font-size: 16px;
            font-weight: 700;
            color: #1E40AF;
        }}

        .analysis-badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: var(--radius-full);
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .analysis-badge.suitable {{
            background: #D1FAE5;
            color: #065F46;
        }}

        .analysis-badge.not-suitable {{
            background: #FEE2E2;
            color: #991B1B;
        }}

        .analysis-content {{
            background: white;
            padding: var(--spacing-md);
            border-radius: var(--radius-md);
            margin: var(--spacing-sm) 0;
        }}

        .analysis-content h4 {{
            color: var(--color-text-heading);
            font-size: 14px;
            font-weight: 600;
            margin-bottom: var(--spacing-sm);
        }}

        .analysis-item {{
            padding: var(--spacing-sm);
            background: #F9FAFB;
            border-left: 3px solid #3B82F6;
            margin: var(--spacing-xs) 0;
            border-radius: var(--radius-sm);
        }}

        .analysis-item-title {{
            font-weight: 600;
            color: var(--color-text-heading);
            margin-bottom: 4px;
            font-size: 14px;
        }}

        .analysis-item-text {{
            font-size: 13px;
            color: var(--color-text-secondary);
        }}

        .gap-item {{
            padding: var(--spacing-sm);
            margin: var(--spacing-xs) 0;
            border-radius: var(--radius-sm);
            border-left: 4px solid;
        }}

        .gap-item.critical {{
            background: #FEF2F2;
            border-color: #DC2626;
        }}

        .gap-item.high {{
            background: #FFFBEB;
            border-color: #F59E0B;
        }}

        .gap-item.medium {{
            background: #FEF3C7;
            border-color: #FBBF24;
        }}

        .gap-criticality {{
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            margin-bottom: 4px;
        }}

        .gap-item.critical .gap-criticality {{
            color: #DC2626;
        }}

        .gap-item.high .gap-criticality {{
            color: #F59E0B;
        }}

        .gap-issue {{
            font-size: 14px;
            color: var(--color-text-heading);
            font-weight: 600;
            margin-bottom: 4px;
        }}

        .gap-impact {{
            font-size: 13px;
            color: var(--color-text-secondary);
        }}

        .requirements-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: var(--spacing-sm);
            margin: var(--spacing-sm) 0;
        }}

        .requirement-box {{
            background: white;
            padding: var(--spacing-md);
            border-radius: var(--radius-md);
            border: 1px solid #E5E7EB;
        }}

        .requirement-box h5 {{
            font-size: 11px;
            font-weight: 600;
            color: #3B82F6;
            text-transform: uppercase;
            margin-bottom: var(--spacing-xs);
            letter-spacing: 0.5px;
        }}

        .requirement-box ul {{
            list-style: none;
            padding: 0;
            margin: 0;
        }}

        .requirement-box li {{
            font-size: 13px;
            color: var(--color-text-secondary);
            padding: 4px 0;
            padding-left: 16px;
            position: relative;
        }}

        .requirement-box li:before {{
            content: "▸";
            position: absolute;
            left: 0;
            color: #3B82F6;
            font-weight: bold;
        }}

        /* Button */
        .tender-link {{
            display: inline-flex;
            align-items: center;
            gap: var(--spacing-xs);
            margin-top: var(--spacing-md);
            padding: 12px 24px;
            background: var(--color-primary);
            color: white;
            text-decoration: none;
            border-radius: var(--radius-md);
            font-weight: 600;
            font-size: 14px;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }}

        .tender-link:hover {{
            background: var(--color-primary-dark);
            transform: translateX(2px);
        }}

        /* Additional Info */
        .additional-info {{
            background: #FFFBEB;
            border-left: 4px solid #F59E0B;
            padding: var(--spacing-md);
            margin-top: var(--spacing-md);
            border-radius: var(--radius-md);
        }}

        .additional-info h4 {{
            color: #D97706;
            font-size: 14px;
            font-weight: 600;
            margin-bottom: var(--spacing-xs);
        }}

        .additional-info p {{
            color: #92400E;
            font-size: 13px;
            line-height: 1.6;
            margin: 4px 0;
        }}

        /* Footer */
        .footer {{
            background: var(--color-bg-card);
            border-radius: var(--radius-lg);
            padding: var(--spacing-lg);
            margin-top: var(--spacing-xl);
            text-align: center;
            color: var(--color-text-secondary);
            box-shadow: var(--shadow-card);
        }}

        .footer p {{
            margin: 8px 0;
            font-size: 14px;
        }}

        /* Responsive Design */
        @media (max-width: 1023px) {{
            .tender-meta {{
                grid-template-columns: repeat(2, 1fr);
            }}
        }}

        @media (max-width: 767px) {{
            body {{
                padding: var(--spacing-md);
            }}

            .header h1 {{
                font-size: 24px;
            }}

            .stat-value {{
                font-size: 32px;
            }}

            .tender-meta {{
                grid-template-columns: 1fr;
            }}

            .stats {{
                grid-template-columns: repeat(2, 1fr);
            }}

            .requirements-grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <div class="header">
            <div class="header-top">
                <h1>
                    <span>🔍</span>
                    Поиск тендеров
                </h1>
                <span class="badge-ai">AI-Powered</span>
            </div>

            <div class="search-params">
                <h3>📋 Параметры поиска</h3>
                <div class="param-item" style="margin-bottom: 12px;">
                    <span class="param-label">Основной запрос:</span>
                    <span class="param-value query">{search_params['original_query']}</span>
                </div>

                {f'''<div class="expanded-queries-box">
                    <h4>🧠 Расширенные запросы AI</h4>
                    <div class="query-tags">
                        {''.join([f'<span class="query-tag">{q}</span>' for q in search_params.get('expanded_queries', [])])}
                    </div>
                </div>''' if search_params.get('expanded_queries') else ''}

                <div class="params-grid">
                    <div class="param-item">
                        <span class="param-label">💰 Цена:</span>
                        <span class="param-value">{search_params['price_min']:,} - {search_params['price_max']:,} ₽</span>
                    </div>
                    {f'''<div class="param-item">
                        <span class="param-label">📍 Регионы:</span>
                        <span class="param-value">{', '.join(search_params['regions'])}</span>
                    </div>''' if search_params.get('regions') else ''}
                    <div class="param-item">
                        <span class="param-label">📅 Дата:</span>
                        <span class="param-value">{datetime.now().strftime('%d.%m.%Y %H:%M')}</span>
                    </div>
                    <div class="param-item">
                        <span class="param-label">⏱️ Время поиска:</span>
                        <span class="param-value">{search_params.get('time', 0):.1f}s</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- Stats Bar -->
        <div class="stats">
            <div class="stat-card">
                <div class="stat-icon">📊</div>
                <div class="stat-value">{len(all_tenders)}</div>
                <div class="stat-label">Тендеров</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">💰</div>
                <div class="stat-value">{total_price / 1_000_000:.1f}M</div>
                <div class="stat-label">Сумма млн ₽</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">📈</div>
                <div class="stat-value">{avg_price / 1_000_000:.1f}M</div>
                <div class="stat-label">Средняя млн ₽</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">⚖️</div>
                <div class="stat-value">{law_223_percent:.0f}%</div>
                <div class="stat-label">223-ФЗ</div>
            </div>
        </div>
"""

    # Tender Cards
    for i, tender in enumerate(all_tenders, 1):
        # Tags
        tags_html = '<div class="tags">'
        if tender.get('law'):
            tags_html += f'<span class="tag law">{tender["law"]}</span>'
        if tender.get('procedure_type'):
            tags_html += f'<span class="tag type">{tender["procedure_type"]}</span>'
        if tender.get('stage'):
            tags_html += f'<span class="tag stage">{tender["stage"]}</span>'
        if tender.get('region'):
            tags_html += f'<span class="tag region">📍 {tender["region"]}</span>'
        if tender.get('customer_type'):
            tags_html += f'<span class="tag customer">{tender["customer_type"]}</span>'
        tags_html += '</div>'

        # Additional Info
        additional_info = ''
        if tender.get('payment_terms') or tender.get('quantity_info'):
            additional_info = '<div class="additional-info"><h4>ℹ️ Дополнительная информация</h4>'
            if tender.get('payment_terms'):
                additional_info += f'<p><strong>💳 Условия оплаты:</strong> {tender["payment_terms"]}</p>'
            if tender.get('quantity_info'):
                additional_info += f'<p><strong>📦 Количество:</strong> {tender["quantity_info"]}</p>'
            additional_info += '</div>'

        html += f"""
        <div class="tender-card">
            <div class="tender-header">
                <div class="tender-number">№{tender.get('number', 'N/A')}</div>
                <div class="tender-date">📅 {tender.get('published', tender.get('placement_date', 'N/A'))}</div>
            </div>

            <div class="tender-title">
                {i}. {tender.get('name', 'Без названия')}
            </div>

            {tags_html}

            <div class="tender-meta">
                {f'''<div class="meta-item">
                    <div class="meta-header">
                        <div class="meta-icon">💰</div>
                        <div class="meta-label">Цена контракта</div>
                    </div>
                    <div class="meta-value price">{tender["price_formatted"]}</div>
                </div>''' if tender.get('price_formatted') else ''}

                {f'''<div class="meta-item">
                    <div class="meta-header">
                        <div class="meta-icon">🏢</div>
                        <div class="meta-label">Заказчик</div>
                    </div>
                    <div class="meta-value">{tender["customer"][:60]}{'...' if len(tender["customer"]) > 60 else ''}</div>
                </div>''' if tender.get('customer') else ''}

                {f'''<div class="meta-item">
                    <div class="meta-header">
                        <div class="meta-icon">📍</div>
                        <div class="meta-label">Регион</div>
                    </div>
                    <div class="meta-value">{tender["region"]}</div>
                </div>''' if tender.get('region') else ''}

                {f'''<div class="meta-item">
                    <div class="meta-header">
                        <div class="meta-icon">⏰</div>
                        <div class="meta-label">Срок подачи</div>
                    </div>
                    <div class="meta-value">{tender["submission_deadline"]}</div>
                </div>''' if tender.get('submission_deadline') else ''}

                {f'''<div class="meta-item">
                    <div class="meta-header">
                        <div class="meta-icon">🔖</div>
                        <div class="meta-label">ИКЗ</div>
                    </div>
                    <div class="meta-value" style="font-size: 12px;">{tender["ikz"]}</div>
                </div>''' if tender.get('ikz') else ''}

                {f'''<div class="meta-item">
                    <div class="meta-header">
                        <div class="meta-icon">🏛️</div>
                        <div class="meta-label">Тип закупки</div>
                    </div>
                    <div class="meta-value">{tender["customer_type"] if tender.get("customer_type") else "N/A"}</div>
                </div>''' if True else ''}
            </div>

            {additional_info}

            <a href="https://zakupki.gov.ru{tender.get('url', '')}" target="_blank" class="tender-link">
                Открыть на zakupki.gov.ru →
            </a>

            {create_analysis_html(tender.get('analysis')) if tender.get('analysis') else ''}
        </div>
"""

    html += f"""
        <!-- Footer -->
        <div class="footer">
            <p><strong>🧠 Умный поиск с AI расширением запросов</strong></p>
            <p>📡 Данные получены через RSS-фид zakupki.gov.ru</p>
            <p style="font-size: 12px; color: var(--color-text-meta); margin-top: 12px;">
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
    print("  УМНЫЙ ПОИСК ТЕНДЕРОВ С AI")
    print("="*70 + "\n")

    # Загружаем конфигурацию
    config = ConfigLoader()
    llm_config = config.get_llm_config()

    # Создаем TenderAnalyzer (он содержит LLM адаптер)
    print("🤖 Инициализация AI...")
    tender_analyzer = TenderAnalyzer(
        api_key=llm_config['api_key'],
        provider=llm_config['provider'],
        model=llm_config.get('model'),
        model_fast=llm_config.get('model_fast'),
        model_premium=llm_config.get('model_premium')
    )

    # Создаем компоненты
    search_expander = SmartSearchExpander(tender_analyzer.llm)
    enhanced_parser = ZakupkiEnhancedParser(tender_analyzer.llm)

    # Параметры поиска
    original_query = "компьютерное оборудование"
    price_min = 500000
    price_max = 5000000

    print(f"\n🔍 Оригинальный запрос: '{original_query}'")
    print(f"💰 Цена: {price_min:,} - {price_max:,} руб\n")

    # Расширяем поисковый запрос через AI
    print("🧠 Расширение запроса через AI...")
    expanded_queries = search_expander.expand_search_query(original_query, max_variants=4)

    start_time = datetime.now()

    # Ищем по всем расширенным запросам
    all_tenders = []
    seen_numbers = set()

    for query in expanded_queries:
        print(f"\n🔎 Поиск по запросу: '{query}'")

        tenders = enhanced_parser.search_with_details(
            keywords=query,
            price_min=price_min,
            price_max=price_max,
            max_results=5,
            extract_details=False  # Отключаем LLM для скорости
        )

        # Дедупликация
        for tender in tenders:
            number = tender.get('number')
            if number and number not in seen_numbers:
                seen_numbers.add(number)
                all_tenders.append(tender)

    elapsed = (datetime.now() - start_time).total_seconds()

    print(f"\n✅ Всего найдено уникальных тендеров: {len(all_tenders)}")
    print(f"⏱️  Общее время поиска: {elapsed:.2f} секунд\n")

    if not all_tenders:
        print("❌ Тендеры не найдены")
        return

    # Создаем HTML отчет
    print("📄 Создание HTML отчета...")

    search_params = {
        'original_query': original_query,
        'expanded_queries': expanded_queries,
        'price_min': price_min,
        'price_max': price_max,
        'time': elapsed
    }

    html_content = create_enhanced_html_report(all_tenders, search_params)

    # Сохраняем
    output_dir = Path(__file__).parent / 'output' / 'reports'
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    html_file = output_dir / f'smart_search_{timestamp}.html'

    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"✅ HTML отчет сохранен: {html_file}")

    # Открываем в браузере
    print("\n🌐 Открываем в браузере...")
    webbrowser.open(f'file://{html_file.absolute()}')

    print("\n" + "="*70)
    print("✅ Готово! Умный поиск завершен.")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
