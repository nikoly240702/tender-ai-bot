"""
Генератор отчетов в различных форматах (HTML, JSON, Markdown).
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any
from jinja2 import Template
from reporting.html_template_simple import HTML_TEMPLATE


class ReportGenerator:
    """Класс для генерации отчетов о тендере."""

    def __init__(self, output_dir: str):
        """
        Инициализация генератора отчетов.

        Args:
            output_dir: Директория для сохранения отчетов
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_report_filename(self, tender_name: str, format: str) -> str:
        """Генерирует имя файла для отчета."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_name = "".join(c for c in tender_name[:30] if c.isalnum() or c in (' ', '_')).strip()
        safe_name = safe_name.replace(' ', '_')
        return f"tender_report_{safe_name}_{timestamp}.{format}"

    def generate_json_report(self, data: Dict[str, Any], tender_name: str) -> str:
        """
        Генерирует JSON отчет.

        Returns:
            Путь к созданному файлу
        """
        filename = self.generate_report_filename(tender_name, 'json')
        filepath = self.output_dir / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return str(filepath)

    def generate_markdown_report(self, data: Dict[str, Any], tender_name: str) -> str:
        """
        Генерирует Markdown отчет.

        Returns:
            Путь к созданному файлу
        """
        filename = self.generate_report_filename(tender_name, 'md')
        filepath = self.output_dir / filename

        md_content = self._build_markdown_content(data)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(md_content)

        return str(filepath)

    def _build_markdown_content(self, data: Dict[str, Any]) -> str:
        """Строит содержимое Markdown отчета."""
        tender_info = data.get('tender_info', {})
        score = data.get('score', {})
        financial = data.get('financial_analysis', {})
        gaps = data.get('gaps', [])
        questions = data.get('questions', {})
        contacts = data.get('contacts', {})

        md = f"""# Отчет по анализу тендера

**Дата:** {datetime.now().strftime('%d.%m.%Y %H:%M')}

## Информация о тендере

- **Название:** {tender_info.get('name', 'Н/Д')}
- **Заказчик:** {tender_info.get('customer', 'Н/Д')}
- **НМЦК:** {(tender_info.get('nmck') or 0):,.0f} руб.
- **Срок подачи заявок:** {tender_info.get('deadline_submission', 'Н/Д')}

## Финансовая информация

- **НМЦК:** {(tender_info.get('nmck') or 0):,.0f} руб.
- **Обеспечение заявки:** {(financial.get('guarantees', {}).get('application_guarantee') or 0):,.0f} руб.
- **Обеспечение контракта:** {(financial.get('guarantees', {}).get('contract_guarantee') or 0):,.0f} руб.

## Выявленные пробелы

**Всего:** {len(gaps)}

"""
        # Добавляем пробелы по критичности
        for criticality in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']:
            critical_gaps = [g for g in gaps if g.get('criticality') == criticality]
            if critical_gaps:
                md += f"\n### {criticality}\n\n"
                for i, gap in enumerate(critical_gaps, 1):
                    md += f"{i}. **{gap.get('category', 'Н/Д')}:** {gap.get('issue', 'Н/Д')}\n"
                    md += f"   - *Влияние:* {gap.get('impact', 'Н/Д')}\n\n"

        # Добавляем вопросы
        md += "\n## Вопросы для заказчика\n\n"

        if questions.get('critical'):
            md += "### Критичные\n\n"
            for i, q in enumerate(questions['critical'], 1):
                md += f"{i}. {q}\n\n"

        if questions.get('important'):
            md += "### Важные\n\n"
            for i, q in enumerate(questions['important'], 1):
                md += f"{i}. {q}\n\n"

        # Контакты
        md += "\n## Контакты заказчика\n\n"
        if contacts.get('emails'):
            md += f"**Email:** {', '.join(contacts['emails'])}\n\n"
        if contacts.get('phones'):
            md += f"**Телефон:** {', '.join(contacts['phones'])}\n\n"

        md += f"\n---\n*Отчет сгенерирован ИИ-агентом для анализа тендеров*\n"

        return md

    def generate_html_report(self, data: Dict[str, Any], tender_name: str) -> str:
        """
        Генерирует HTML отчет.

        Returns:
            Путь к созданному файлу
        """
        filename = self.generate_report_filename(tender_name, 'html')
        filepath = self.output_dir / filename

        html_content = self._build_html_content(data)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_content)

        return str(filepath)

    def _build_html_content(self, data: Dict[str, Any]) -> str:
        """Строит содержимое HTML отчета."""
        # Используем новый упрощенный шаблон
        from jinja2 import Environment
        env = Environment()

        # Добавляем custom фильтр для безопасного форматирования чисел
        def safe_format(value, format_spec=":,.0f"):
            """Безопасно форматирует число, возвращает 'Н/Д' если None."""
            if value is None:
                return "Н/Д"
            try:
                return format(value, format_spec)
            except (ValueError, TypeError):
                return str(value) if value else "Н/Д"

        env.filters['safe_format'] = safe_format
        template = env.from_string(HTML_TEMPLATE)

        return template.render(
            tender_info=data.get('tender_info', {}),
            gaps=data.get('gaps', []),
            questions=data.get('questions', {}),
            contacts=data.get('contacts', {}),
            requirements=data.get('requirements', {}),
            score=data.get('score', {}),
            financial_analysis=data.get('financial_analysis', {}),
            contract_analysis=data.get('contract_analysis', {}),
            risk_assessment=data.get('risk_assessment', {}),
            analysis_summary=data.get('analysis_summary', {}),
            files_info=data.get('files_info', []),  # Информация о документах
            current_date=datetime.now().strftime('%d.%m.%Y %H:%M')
        )

    def _build_html_content_old(self, data: Dict[str, Any]) -> str:
        """DEPRECATED: Старый метод генерации HTML."""
        template_html = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Отчет по тендеру - {{ tender_info.name }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 { color: #2c3e50; margin-bottom: 10px; }
        h2 { color: #34495e; margin-top: 30px; margin-bottom: 15px; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
        h3 { color: #7f8c8d; margin-top: 20px; margin-bottom: 10px; }
        .header { border-bottom: 3px solid #3498db; padding-bottom: 20px; margin-bottom: 30px; }
        .date { color: #7f8c8d; font-size: 14px; }
        .score-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            border-radius: 10px;
            margin: 20px 0;
        }
        .score-value {
            font-size: 48px;
            font-weight: bold;
            margin: 10px 0;
        }
        .recommendation {
            display: inline-block;
            padding: 10px 20px;
            border-radius: 5px;
            font-weight: bold;
            margin-top: 15px;
        }
        .recommendation.УЧАСТВОВАТЬ { background: #27ae60; color: white; }
        .recommendation.УТОЧНИТЬ { background: #f39c12; color: white; }
        .recommendation.ОТКАЗАТЬСЯ { background: #e74c3c; color: white; }
        .progress-bar {
            background: #ecf0f1;
            height: 25px;
            border-radius: 12px;
            overflow: hidden;
            margin: 10px 0;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #3498db, #2ecc71);
            transition: width 0.3s ease;
            display: flex;
            align-items: center;
            padding-left: 10px;
            color: white;
            font-weight: bold;
            font-size: 12px;
        }
        .info-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }
        .info-item {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 5px;
            border-left: 4px solid #3498db;
        }
        .info-label { font-weight: bold; color: #7f8c8d; font-size: 12px; text-transform: uppercase; }
        .info-value { font-size: 18px; color: #2c3e50; margin-top: 5px; }
        .gap-list { list-style: none; }
        .gap-item {
            background: #fff;
            border-left: 4px solid #ccc;
            padding: 15px;
            margin: 10px 0;
            border-radius: 5px;
        }
        .gap-item.CRITICAL { border-left-color: #e74c3c; background: #fef5f5; }
        .gap-item.HIGH { border-left-color: #f39c12; background: #fef9f5; }
        .gap-item.MEDIUM { border-left-color: #3498db; background: #f5f9fe; }
        .gap-item.LOW { border-left-color: #95a5a6; background: #f8f9fa; }
        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 11px;
            font-weight: bold;
            text-transform: uppercase;
        }
        .badge.CRITICAL { background: #e74c3c; color: white; }
        .badge.HIGH { background: #f39c12; color: white; }
        .badge.MEDIUM { background: #3498db; color: white; }
        .badge.LOW { background: #95a5a6; color: white; }
        .question-block {
            background: #f8f9fa;
            padding: 15px;
            border-radius: 5px;
            margin: 10px 0;
        }
        .contact-box {
            background: #e8f5e9;
            padding: 20px;
            border-radius: 5px;
            border-left: 4px solid #27ae60;
        }
        table { width: 100%; border-collapse: collapse; margin: 20px 0; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ecf0f1; }
        th { background: #34495e; color: white; }
        tr:hover { background: #f8f9fa; }
        .product-card {
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 20px;
            margin: 15px 0;
        }
        .product-header {
            font-size: 20px;
            font-weight: bold;
            color: #2c3e50;
            margin-bottom: 15px;
            border-bottom: 2px solid #3498db;
            padding-bottom: 10px;
        }
        .specs-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 10px;
            margin: 15px 0;
        }
        .spec-item {
            background: white;
            padding: 10px;
            border-radius: 4px;
            font-size: 14px;
        }
        .spec-label {
            font-weight: bold;
            color: #7f8c8d;
            font-size: 12px;
        }
        .spec-value {
            color: #2c3e50;
            margin-top: 3px;
        }
        .best-offer {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 8px;
            margin: 15px 0;
        }
        .offer-title {
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 10px;
        }
        .offer-price {
            font-size: 28px;
            font-weight: bold;
            margin: 10px 0;
        }
        .offer-link {
            color: white;
            text-decoration: underline;
            word-break: break-all;
        }
        .price-range-box {
            background: #e8f5e9;
            padding: 15px;
            border-radius: 5px;
            margin: 10px 0;
        }
        .confidence-badge {
            display: inline-block;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
            margin-left: 10px;
        }
        .confidence-badge.high { background: #27ae60; color: white; }
        .confidence-badge.medium { background: #f39c12; color: white; }
        .confidence-badge.low { background: #95a5a6; color: white; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Отчет по анализу тендера</h1>
            <div class="date">Дата: {{ current_date }}</div>
        </div>

        <h2>Информация о тендере</h2>
        <div class="info-grid">
            <div class="info-item">
                <div class="info-label">Название</div>
                <div class="info-value">{{ tender_info.name }}</div>
            </div>
            <div class="info-item">
                <div class="info-label">Заказчик</div>
                <div class="info-value">{{ tender_info.customer }}</div>
            </div>
            <div class="info-item">
                <div class="info-label">НМЦК</div>
                <div class="info-value">{{ "{:,.0f}".format(tender_info.nmck) }} ₽</div>
            </div>
            <div class="info-item">
                <div class="info-label">Срок подачи</div>
                <div class="info-value">{{ tender_info.deadline_submission }}</div>
            </div>
        </div>

        <h2>Финансовая информация</h2>
        <table>
            <tr>
                <th>Показатель</th>
                <th>Значение</th>
            </tr>
            {% if financial.guarantees %}
            <tr>
                <td>Обеспечение заявки</td>
                <td>{{ "{:,.0f}".format(financial.guarantees.application_guarantee) }} ₽</td>
            </tr>
            <tr>
                <td>Обеспечение контракта</td>
                <td>{{ "{:,.0f}".format(financial.guarantees.contract_guarantee) }} ₽</td>
            </tr>
            {% endif %}
        </table>

        {% if financial.product_analysis and financial.product_analysis.products_found %}
        <h2>Найденные товары и рыночные цены</h2>
        <div style="background: #e3f2fd; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
            <strong>Тип закупки:</strong> {{ financial.tender_type or 'GOODS' }}
            <span class="confidence-badge {{ financial.product_analysis.confidence }}">
                Уверенность: {{ financial.product_analysis.confidence }}
            </span>
        </div>

        {% for item in financial.product_analysis.products_with_prices %}
        <div class="product-card">
            <div class="product-header">
                📦 {{ item.product.name }}
                <span style="font-size: 14px; color: #7f8c8d;">
                    ({{ item.product.quantity }} {{ item.product.unit }})
                </span>
            </div>

            {% if item.product.specifications %}
            <h4 style="color: #7f8c8d; margin-top: 15px;">Технические характеристики:</h4>
            <div class="specs-grid">
                {% for key, value in item.product.specifications.items() %}
                <div class="spec-item">
                    <div class="spec-label">{{ key }}</div>
                    <div class="spec-value">{{ value }}</div>
                </div>
                {% endfor %}
            </div>
            {% endif %}

            {% if item.price_info %}
            <h4 style="color: #7f8c8d; margin-top: 20px;">Оценка стоимости:</h4>
            <div style="padding: 15px; background: white; border-radius: 5px;">
                <div><strong>Категория:</strong> {{ item.price_info.product_category or 'Не определена' }}</div>
                <div><strong>Сегмент рынка:</strong> {{ item.price_info.market_segment or 'Не определен' }}</div>
                {% if item.price_info.price_range %}
                <div class="price-range-box">
                    <strong>📊 Диапазон цен за единицу:</strong><br>
                    От {{ "{:,.0f}".format(item.price_info.price_range.min) }} ₽
                    до {{ "{:,.0f}".format(item.price_info.price_range.max) }} ₽<br>
                    <strong>Типичная цена:</strong> {{ "{:,.0f}".format(item.price_info.price_range.typical) }} ₽
                </div>
                {% endif %}
                <div style="font-size: 18px; margin-top: 10px;">
                    <strong>Общая стоимость:</strong>
                    <span style="color: #27ae60; font-size: 24px; font-weight: bold;">
                        {{ "{:,.0f}".format(item.price_info.total_cost_estimate) }} ₽
                    </span>
                </div>
                {% if item.price_info.reasoning %}
                <div style="margin-top: 10px; font-size: 14px; color: #7f8c8d;">
                    <em>{{ item.price_info.reasoning }}</em>
                </div>
                {% endif %}
            </div>
            {% endif %}

            {% if item.web_search and item.web_search.links_found %}
            <h4 style="color: #7f8c8d; margin-top: 20px;">🔗 Найденные предложения:</h4>
            {% if item.web_search.analysis.best_offer %}
            <div class="best-offer">
                <div class="offer-title">🏆 Лучшее предложение</div>
                <div>{{ item.web_search.analysis.best_offer.title }}</div>
                <div class="offer-price">
                    💰 {{ "{:,.0f}".format(item.web_search.analysis.best_offer.price) }} ₽
                </div>
                <div style="margin-top: 10px;">
                    🔗 <a href="{{ item.web_search.analysis.best_offer.url }}" class="offer-link" target="_blank">
                        {{ item.web_search.analysis.best_offer.url }}
                    </a>
                </div>
                {% if item.web_search.analysis.best_offer.reasoning %}
                <div style="margin-top: 15px; font-size: 14px; opacity: 0.9;">
                    <strong>Почему это лучшее:</strong> {{ item.web_search.analysis.best_offer.reasoning }}
                </div>
                {% endif %}
            </div>
            {% endif %}

            {% if item.web_search.analysis.price_range %}
            <div style="background: white; padding: 15px; border-radius: 5px; margin-top: 10px;">
                <strong>📈 Диапазон цен по найденным предложениям:</strong><br>
                Минимум: {{ "{:,.0f}".format(item.web_search.analysis.price_range.min) }} ₽ |
                Максимум: {{ "{:,.0f}".format(item.web_search.analysis.price_range.max) }} ₽ |
                Средняя: {{ "{:,.0f}".format(item.web_search.analysis.price_range.average) }} ₽
            </div>
            {% endif %}
            {% elif item.web_search %}
            <div style="background: #fff3cd; padding: 15px; border-radius: 5px; margin-top: 15px;">
                ⚠️ {{ item.web_search.message or 'Реальные предложения не найдены' }}
            </div>
            {% endif %}
        </div>
        {% endfor %}

        <div style="background: #f8f9fa; padding: 20px; border-radius: 5px; margin-top: 20px;">
            <h4>💰 Итоговая оценка закупки</h4>
            <table>
                <tr>
                    <td><strong>Стоимость товаров:</strong></td>
                    <td>{{ "{:,.0f}".format(financial.product_analysis.cost_breakdown.products_cost) }} ₽</td>
                </tr>
                <tr>
                    <td><strong>Логистика (3%):</strong></td>
                    <td>{{ "{:,.0f}".format(financial.product_analysis.cost_breakdown.logistics) }} ₽</td>
                </tr>
                <tr>
                    <td><strong>Накладные расходы (2%):</strong></td>
                    <td>{{ "{:,.0f}".format(financial.product_analysis.cost_breakdown.overhead) }} ₽</td>
                </tr>
                <tr style="background: #e8f5e9;">
                    <td><strong>Итого себестоимость:</strong></td>
                    <td><strong>{{ "{:,.0f}".format(financial.product_analysis.estimated_cost) }} ₽</strong></td>
                </tr>
                <tr>
                    <td><strong>НМЦК:</strong></td>
                    <td>{{ "{:,.0f}".format(financial.product_analysis.nmck) }} ₽</td>
                </tr>
                <tr style="background: #d4edda;">
                    <td><strong>Прибыль (маржа):</strong></td>
                    <td><strong style="color: #27ae60; font-size: 18px;">
                        {{ "{:,.0f}".format(financial.product_analysis.margin_amount) }} ₽
                        ({{ financial.product_analysis.margin_percent|round(1) }}%)
                    </strong></td>
                </tr>
            </table>
        </div>
        {% endif %}

        <h2>Выявленные пробелы ({{ gaps|length }})</h2>
        <ul class="gap-list">
        {% for gap in gaps %}
            <li class="gap-item {{ gap.criticality }}">
                <span class="badge {{ gap.criticality }}">{{ gap.criticality }}</span>
                <strong>{{ gap.category }}</strong><br>
                {{ gap.issue }}<br>
                <small><em>Влияние:</em> {{ gap.impact }}</small>
            </li>
        {% endfor %}
        </ul>

        <h2>Вопросы для заказчика</h2>

        {% if questions.critical %}
        <h3>Критичные вопросы</h3>
        {% for q in questions.critical %}
        <div class="question-block">{{ loop.index }}. {{ q }}</div>
        {% endfor %}
        {% endif %}

        {% if questions.important %}
        <h3>Важные вопросы</h3>
        {% for q in questions.important %}
        <div class="question-block">{{ loop.index }}. {{ q }}</div>
        {% endfor %}
        {% endif %}

        {% if questions.optional %}
        <h3>Дополнительные вопросы</h3>
        {% for q in questions.optional %}
        <div class="question-block">{{ loop.index }}. {{ q }}</div>
        {% endfor %}
        {% endif %}

        <h2>Контакты заказчика</h2>
        <div class="contact-box">
            {% if contacts.emails %}
            <div><strong>Email:</strong> {{ contacts.emails|join(', ') }}</div>
            {% endif %}
            {% if contacts.phones %}
            <div><strong>Телефон:</strong> {{ contacts.phones|join(', ') }}</div>
            {% endif %}
            {% if not contacts.has_contacts %}
            <div>Контактная информация не найдена в документации</div>
            {% endif %}
        </div>

        <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #ecf0f1; color: #7f8c8d; font-size: 12px;">
            Отчет сгенерирован ИИ-агентом для анализа тендеров
        </div>
    </div>
</body>
</html>
        """

        template = Template(template_html)

        return template.render(
            tender_info=data.get('tender_info', {}),
            score=data.get('score', {}),
            financial=data.get('financial_analysis', {}),
            gaps=data.get('gaps', []),
            questions=data.get('questions', {}),
            contacts=data.get('contacts', {}),
            current_date=datetime.now().strftime('%d.%m.%Y %H:%M')
        )

    def generate_all_reports(self, data: Dict[str, Any], tender_name: str) -> Dict[str, str]:
        """
        Генерирует все форматы отчетов.

        Returns:
            Словарь с путями к файлам
        """
        return {
            'json': self.generate_json_report(data, tender_name),
            'markdown': self.generate_markdown_report(data, tender_name),
            'html': self.generate_html_report(data, tender_name)
        }


if __name__ == "__main__":
    # Тестовый пример
    test_data = {
        'tender_info': {
            'name': 'Поставка компьютерного оборудования',
            'customer': 'ООО "Заказчик"',
            'nmck': 5000000,
            'deadline_submission': '2025-12-31'
        },
        'score': {
            'scores': {
                'total_score': 75,
                'technical_fit': 80,
                'financial_attractiveness': 70,
                'information_completeness': 65,
                'competence_match': 85
            },
            'readiness_percent': 70,
            'recommendation': 'УТОЧНИТЬ',
            'recommendation_details': 'Требуется уточнение у заказчика'
        },
        'financial_analysis': {
            'cost_estimate': {'total_cost': 3500000},
            'margin': {'margin_amount': 1500000, 'margin_percent': 30, 'roi': 42.8},
            'guarantees': {'application_guarantee': 100000, 'contract_guarantee': 250000}
        },
        'gaps': [
            {
                'category': 'Сроки',
                'issue': 'Не указаны промежуточные сроки',
                'impact': 'Сложность планирования',
                'criticality': 'HIGH'
            }
        ],
        'questions': {
            'critical': ['Уточните сроки поставки'],
            'important': ['Какой состав комиссии?'],
            'optional': []
        },
        'contacts': {
            'emails': ['contact@example.com'],
            'phones': ['+7 (495) 123-45-67'],
            'has_contacts': True
        }
    }

    generator = ReportGenerator('output/reports')
    paths = generator.generate_all_reports(test_data, 'Тест')

    print("Отчеты созданы:")
    for format, path in paths.items():
        print(f"{format}: {path}")
