"""
Новый HTML шаблон для AI-анализа тендеров с современным дизайном.
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Анализ тендера</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --color-primary: #5B8CDE;
            --color-primary-dark: #2D3E5F;
            --color-bg-main: #F7F9FC;
            --color-bg-card: #FFFFFF;
            --color-bg-alt: #EEF3F9;
            --color-text-heading: #1F2937;
            --color-text-primary: #374151;
            --color-text-secondary: #6B7280;
            --color-text-meta: #9CA3AF;
            --shadow-card: 0 1px 3px rgba(0, 0, 0, 0.08), 0 1px 2px rgba(0, 0, 0, 0.06);
            --spacing-sm: 12px;
            --spacing-md: 16px;
            --spacing-lg: 24px;
            --spacing-xl: 32px;
            --radius-md: 8px;
            --radius-lg: 12px;
            --radius-full: 16px;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--color-bg-main);
            color: var(--color-text-primary);
            padding: var(--spacing-lg);
            min-height: 100vh;
            line-height: 1.6;
        }
        .container { max-width: 1280px; margin: 0 auto; }
        .header {
            background: var(--color-bg-card);
            border-radius: var(--radius-lg);
            padding: var(--spacing-xl);
            margin-bottom: var(--spacing-lg);
            box-shadow: var(--shadow-card);
        }
        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: var(--spacing-lg);
            flex-wrap: wrap;
            gap: var(--spacing-md);
        }
        .header h1 {
            color: var(--color-text-heading);
            font-size: 32px;
            font-weight: 700;
            margin: 0;
        }
        .badge-ai {
            display: inline-block;
            padding: 6px 16px;
            background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-dark) 100%);
            color: white;
            border-radius: var(--radius-full);
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .tender-info-box {
            background: var(--color-bg-alt);
            padding: var(--spacing-lg);
            border-radius: var(--radius-md);
            margin-top: var(--spacing-md);
        }
        .tender-name {
            font-size: 20px;
            font-weight: 600;
            color: var(--color-text-heading);
            margin-bottom: var(--spacing-sm);
        }
        .tender-customer {
            font-size: 16px;
            color: var(--color-text-secondary);
            margin-bottom: var(--spacing-sm);
        }
        .date { font-size: 13px; color: var(--color-text-meta); }
        .section {
            background: var(--color-bg-card);
            border-radius: var(--radius-lg);
            padding: var(--spacing-xl);
            margin-bottom: var(--spacing-lg);
            box-shadow: var(--shadow-card);
        }
        .section-title {
            font-size: 24px;
            font-weight: 600;
            color: var(--color-text-heading);
            margin-bottom: var(--spacing-lg);
            display: flex;
            align-items: center;
            gap: var(--spacing-sm);
        }
        .gap-item {
            background: var(--color-bg-main);
            padding: var(--spacing-md);
            border-radius: var(--radius-md);
            margin-bottom: var(--spacing-md);
            border-left: 4px solid #6B7280;
        }
        .gap-item.CRITICAL { border-color: #DC2626; background: #FEF2F2; }
        .gap-item.HIGH { border-color: #F59E0B; background: #FFFBEB; }
        .gap-item.MEDIUM { border-color: #FBBF24; background: #FEF3C7; }
        .gap-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: var(--radius-full);
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            margin-bottom: 8px;
            color: white;
        }
        .gap-badge.CRITICAL { background: #DC2626; }
        .gap-badge.HIGH { background: #F59E0B; }
        .gap-badge.MEDIUM { background: #FBBF24; }
        .gap-badge.LOW { background: #6B7280; }
        .gap-category {
            font-size: 16px;
            font-weight: 600;
            color: var(--color-text-heading);
            margin-bottom: 8px;
        }
        .gap-issue {
            font-size: 14px;
            color: var(--color-text-primary);
            margin-bottom: 8px;
        }
        .gap-impact {
            font-size: 13px;
            color: var(--color-text-secondary);
            font-style: italic;
        }
        .question-type {
            font-size: 14px;
            font-weight: 600;
            color: var(--color-text-secondary);
            text-transform: uppercase;
            margin-top: var(--spacing-lg);
            margin-bottom: var(--spacing-md);
        }
        .question-item {
            background: #EEF6FF;
            padding: var(--spacing-md);
            border-radius: var(--radius-md);
            margin-bottom: 8px;
            border-left: 3px solid #3B82F6;
        }
        .question-item.critical { background: #FEF2F2; border-color: #DC2626; }
        .question-item.important { background: #FFFBEB; border-color: #F59E0B; }
        .contact-info {
            background: #D1FAE5;
            padding: var(--spacing-lg);
            border-radius: var(--radius-md);
            border-left: 4px solid #10B981;
        }
        .contact-item {
            margin: var(--spacing-sm) 0;
            display: flex;
            gap: var(--spacing-sm);
        }
        .contact-label {
            font-weight: 600;
            color: var(--color-text-heading);
            min-width: 100px;
        }
        .no-contacts {
            background: #FEE2E2;
            padding: var(--spacing-lg);
            border-radius: var(--radius-md);
            color: #991B1B;
            border-left: 4px solid #EF4444;
        }
        .footer {
            background: var(--color-bg-card);
            border-radius: var(--radius-lg);
            padding: var(--spacing-lg);
            margin-top: var(--spacing-xl);
            text-align: center;
            color: var(--color-text-secondary);
            box-shadow: var(--shadow-card);
        }
        @media (max-width: 767px) {
            body { padding: var(--spacing-md); }
            .header h1 { font-size: 24px; }
            .section-title { font-size: 20px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-top">
                <h1>🤖 AI Анализ тендера</h1>
                <span class="badge-ai">AI-Powered</span>
            </div>
            <div class="tender-info-box">
                <div style="font-size: 14px; font-weight: 600; color: var(--color-text-secondary); margin-bottom: 8px; text-transform: uppercase;">📦 Объект закупки</div>
                <div class="tender-name">{{ tender_info.name or 'Анализируемый тендер' }}</div>
                {% if tender_info.customer and tender_info.customer != 'N/A' %}
                <div class="tender-customer">🏢 {{ tender_info.customer }}</div>
                {% endif %}
                {% if tender_info.customer_type %}
                <div style="font-size: 14px; color: var(--color-text-secondary); margin-top: 4px;">
                    Тип заказчика: {{ tender_info.customer_type }}
                </div>
                {% endif %}
                {% if tender_info.customer_location %}
                <div style="font-size: 13px; color: var(--color-text-meta); margin-top: 4px;">
                    📍 {{ tender_info.customer_location }}
                </div>
                {% endif %}
                {% if tender_info.customer_email or tender_info.customer_phone %}
                <div style="font-size: 13px; color: var(--color-text-secondary); margin-top: 8px;">
                    {% if tender_info.customer_email %}📧 {{ tender_info.customer_email }}{% endif %}
                    {% if tender_info.customer_phone %} • 📱 {{ tender_info.customer_phone }}{% endif %}
                </div>
                {% endif %}
                {% if tender_info.nmck %}
                <div style="font-size: 16px; font-weight: 600; color: var(--color-primary); margin-top: 12px;">
                    💰 Начальная цена: {{ "{:,.0f}".format(tender_info.nmck) }} ₽
                </div>
                {% endif %}
                {% if tender_info.deadline_submission %}
                <div style="font-size: 14px; color: var(--color-text-primary); margin-top: 8px;">
                    ⏰ Окончание подачи заявок: {{ tender_info.deadline_submission }}
                    {% if tender_info.days_until_deadline is defined and tender_info.days_until_deadline is not none %}
                        {% if tender_info.days_until_deadline == 0 %}
                        <span style="color: #DC2626; font-weight: 600;">(сегодня!)</span>
                        {% elif tender_info.days_until_deadline > 0 %}
                        <span style="color: {% if tender_info.days_until_deadline <= 3 %}#DC2626{% elif tender_info.days_until_deadline <= 7 %}#F59E0B{% else %}#10B981{% endif %}; font-weight: 600;">({{ tender_info.days_until_deadline }} дней)</span>
                        {% else %}
                        <span style="color: #6B7280;">(истек)</span>
                        {% endif %}
                    {% endif %}
                </div>
                {% endif %}
                {% if tender_info.contract_guarantee %}
                <div style="font-size: 13px; color: var(--color-text-secondary); margin-top: 6px;">
                    🔒 Обеспечение исполнения контракта: {{ tender_info.contract_guarantee }}
                </div>
                {% endif %}
                {% if tender_info.delivery_address %}
                <div style="font-size: 13px; color: var(--color-text-secondary); margin-top: 6px;">
                    🚚 Адрес поставки:
                    {% if tender_info.delivery_address is string %}
                        {{ tender_info.delivery_address }}
                    {% elif tender_info.delivery_address is iterable %}
                        {% for addr in tender_info.delivery_address %}
                            <br>• {{ addr }}
                        {% endfor %}
                    {% endif %}
                </div>
                {% endif %}
                {% if tender_info.document_count %}
                <div style="font-size: 13px; color: var(--color-text-meta); margin-top: 6px;">
                    📄 Кол-во документации: {{ tender_info.document_count }} шт.
                </div>
                {% endif %}
                {% if tender_info.arbitration %}
                <div style="font-size: 13px; color: #F59E0B; margin-top: 8px; padding: 8px; background: #FFFBEB; border-radius: 6px;">
                    ⚠️ Арбитражные дела: {{ tender_info.arbitration.arbitration_count or 'проверка не выполнена' }}
                    {% if tender_info.arbitration.total_amount %}
                    на сумму {{ tender_info.arbitration.total_amount }}
                    {% endif %}
                    {% if tender_info.arbitration.note %}
                    <br><small>{{ tender_info.arbitration.note }}</small>
                    {% endif %}
                </div>
                {% endif %}
                <div class="date">📅 {{ current_date }}</div>
            </div>
        </div>

        {% if gaps and gaps|length > 0 %}
        <div class="section">
            <div class="section-title"><span>⚠️</span><span>Выявленные пробелы ({{ gaps|length }})</span></div>
            {% for gap in gaps %}
            <div class="gap-item {{ gap.criticality or 'MEDIUM' }}">
                <span class="gap-badge {{ gap.criticality or 'MEDIUM' }}">{{ gap.criticality or 'MEDIUM' }}</span>
                <div class="gap-category">{{ gap.category or 'Общее' }}</div>
                <div class="gap-issue">{{ gap.issue or gap.description or 'Пробел в документации' }}</div>
                {% if gap.impact %}<div class="gap-impact">💡 Влияние: {{ gap.impact }}</div>{% endif %}
            </div>
            {% endfor %}
        </div>
        {% endif %}

        {% if questions %}
        <div class="section">
            <div class="section-title"><span>❓</span><span>Вопросы для заказчика</span></div>
            {% if questions.critical and questions.critical|length > 0 %}
            <div class="question-type">🔴 Критичные вопросы</div>
            {% for question in questions.critical %}
            <div class="question-item critical">{{ loop.index }}. {{ question }}</div>
            {% endfor %}
            {% endif %}
            {% if questions.important and questions.important|length > 0 %}
            <div class="question-type">🟡 Важные вопросы</div>
            {% for question in questions.important %}
            <div class="question-item important">{{ loop.index }}. {{ question }}</div>
            {% endfor %}
            {% endif %}
            {% if questions.optional and questions.optional|length > 0 %}
            <div class="question-type">🟢 Дополнительные вопросы</div>
            {% for question in questions.optional %}
            <div class="question-item">{{ loop.index }}. {{ question }}</div>
            {% endfor %}
            {% endif %}
        </div>
        {% endif %}

        <div class="section">
            <div class="section-title"><span>📞</span><span>Контакты заказчика</span></div>
            {% if contacts.has_contacts %}
            <div class="contact-info">
                {% if contacts.emails and contacts.emails|length > 0 %}
                <div class="contact-item">
                    <span class="contact-label">📧 Email:</span>
                    <span>{{ contacts.emails|join(', ') }}</span>
                </div>
                {% endif %}
                {% if contacts.phones and contacts.phones|length > 0 %}
                <div class="contact-item">
                    <span class="contact-label">📱 Телефон:</span>
                    <span>{{ contacts.phones|join(', ') }}</span>
                </div>
                {% endif %}
            </div>
            {% else %}
            <div class="no-contacts">⚠️ Контактная информация не найдена в документации тендера</div>
            {% endif %}
        </div>

        <div class="footer">
            <p><strong>🧠 Отчет сгенерирован ИИ-агентом для анализа тендеров</strong></p>
            <p style="margin-top: 8px; font-size: 13px;">Данные получены из тендерной документации и проанализированы с помощью Claude AI</p>
        </div>
    </div>
</body>
</html>
"""
