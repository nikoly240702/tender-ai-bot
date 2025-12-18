"""
Instant Search - мгновенный поиск тендеров по критериям фильтра.

Выполняет поиск, ранжирование и генерацию HTML отчета.
"""

import sys
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parsers.zakupki_rss_parser import ZakupkiRSSParser
from tender_sniper.matching import SmartMatcher

logger = logging.getLogger(__name__)


class InstantSearch:
    """Мгновенный поиск тендеров по фильтру."""

    def __init__(self):
        """Инициализация компонентов поиска."""
        self.parser = ZakupkiRSSParser()
        self.matcher = SmartMatcher()

    async def search_by_filter(
        self,
        filter_data: Dict[str, Any],
        max_tenders: int = 25,
        expanded_keywords: List[str] = None
    ) -> Dict[str, Any]:
        """
        Поиск тендеров по критериям фильтра.

        Args:
            filter_data: Данные фильтра из БД
            max_tenders: Максимальное количество тендеров
            expanded_keywords: Расширенные ключевые слова (если были сгенерированы AI)

        Returns:
            Dict с результатами поиска:
            {
                'tenders': [...],
                'total_found': int,
                'matches': [...],  # Тендеры с хорошим скором
                'stats': {...}
            }
        """
        import json

        logger.info(f"🔍 Запуск мгновенного поиска по фильтру: {filter_data['name']}")

        # Вспомогательная функция для безопасного парсинга JSON (совместимость SQLite/PostgreSQL)
        def safe_json_parse(value, default=[]):
            """Парсит JSON если это строка, иначе возвращает как есть."""
            if value is None:
                return default
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except:
                    return default
            # Уже распарсено (PostgreSQL JSON/JSONB)
            return value if isinstance(value, list) else default

        # Парсим критерии (совместимость SQLite/PostgreSQL)
        original_keywords = safe_json_parse(filter_data.get('keywords'), [])
        exclude_keywords = safe_json_parse(filter_data.get('exclude_keywords'), [])

        # Комбинируем оригинальные и расширенные ключевые слова
        # Приоритет: оригинальные ключевые слова ВСЕГДА используются
        if expanded_keywords:
            # Используем оригинальные + топ расширенных (избегаем дубликатов)
            keywords_to_search = original_keywords + [
                kw for kw in expanded_keywords
                if kw not in original_keywords
            ]
        else:
            keywords_to_search = original_keywords

        price_min = filter_data.get('price_min')
        price_max = filter_data.get('price_max')
        regions = safe_json_parse(filter_data.get('regions'), [])
        tender_types = safe_json_parse(filter_data.get('tender_types'), [])
        law_type = filter_data.get('law_type')
        purchase_stage = filter_data.get('purchase_stage')
        purchase_method = filter_data.get('purchase_method')
        okpd2_codes = safe_json_parse(filter_data.get('okpd2_codes'), [])
        min_deadline_days = filter_data.get('min_deadline_days')
        customer_keywords = safe_json_parse(filter_data.get('customer_keywords'), [])

        # Формируем список поисковых запросов
        # Каждое оригинальное ключевое слово - отдельный запрос (OR логика)
        # + добавляем топ-3 расширенных термина
        search_queries = original_keywords.copy()

        # Добавляем расширенные термины (если есть)
        if expanded_keywords:
            extra_keywords = [kw for kw in expanded_keywords if kw not in original_keywords][:3]
            search_queries.extend(extra_keywords)

        logger.info(f"   🔑 Поисковые запросы ({len(search_queries)}): {', '.join(search_queries)}")
        logger.info(f"   💰 Цена: {price_min} - {price_max}")
        logger.info(f"   📍 Регионы: {regions if regions else 'Все'}")
        logger.info(f"   📜 Закон: {law_type if law_type else 'Все'}")
        logger.info(f"   📝 Этап: {purchase_stage if purchase_stage else 'Все'}")
        logger.info(f"   🔨 Способ: {purchase_method if purchase_method else 'Все'}")
        if okpd2_codes:
            logger.info(f"   📋 ОКПД2: {', '.join(okpd2_codes)}")
        if min_deadline_days:
            logger.info(f"   ⏰ Мин. дней до дедлайна: {min_deadline_days}")
        if customer_keywords:
            logger.info(f"   🏢 Заказчик содержит: {', '.join(customer_keywords)}")
        if exclude_keywords:
            logger.info(f"   ❌ Исключаем: {', '.join(exclude_keywords)}")

        try:
            # Выполняем ОТДЕЛЬНЫЙ поиск для каждого ключевого слова
            # Это OR логика - тендер найдётся если содержит ЛЮБОЕ из слов
            all_results = []
            seen_numbers = set()

            results_per_query = max(10, max_tenders // len(search_queries) + 5)

            for query in search_queries:
                logger.info(f"   🔎 Поиск: '{query}'...")

                # Определяем тип закупки для RSS
                tender_type_for_rss = tender_types[0] if tender_types else None

                results = self.parser.search_tenders_rss(
                    keywords=query,
                    price_min=price_min,
                    price_max=price_max,
                    regions=regions,
                    max_results=results_per_query,
                    tender_type=tender_type_for_rss,
                    law_type=law_type,
                    purchase_stage=purchase_stage,
                    purchase_method=purchase_method,
                )

                # Дедупликация по номеру тендера + client-side фильтрация
                for tender in results:
                    number = tender.get('number')
                    if number and number not in seen_numbers:
                        tender_text = f"{tender.get('name', '')} {tender.get('summary', '')}".lower()
                        customer_name = tender.get('customer', '') or tender.get('customer_name', '')

                        # Проверяем исключающие слова (с границами слов для точности)
                        if exclude_keywords:
                            skip = False
                            for exclude_word in exclude_keywords:
                                # Используем regex с границами слов для избежания ложных срабатываний
                                pattern = r'\b' + re.escape(exclude_word.lower()) + r'\b' if len(exclude_word) < 4 else r'\b' + re.escape(exclude_word.lower())
                                if re.search(pattern, tender_text, re.IGNORECASE):
                                    logger.debug(f"      ⛔ Исключен (содержит '{exclude_word}'): {tender.get('name', '')[:50]}")
                                    skip = True
                                    break
                            if skip:
                                continue

                        # Проверяем ключевые слова заказчика
                        if customer_keywords and customer_name:
                            customer_match = False
                            for kw in customer_keywords:
                                if kw.lower() in customer_name.lower():
                                    customer_match = True
                                    break
                            if not customer_match:
                                logger.debug(f"      ⛔ Заказчик не совпадает: {customer_name[:50]}")
                                continue

                        # Проверяем минимум дней до дедлайна
                        if min_deadline_days:
                            deadline = tender.get('deadline') or tender.get('end_date')
                            if deadline:
                                try:
                                    from datetime import datetime, timedelta
                                    # Пробуем разные форматы даты
                                    deadline_date = None
                                    for fmt in ['%d.%m.%Y', '%Y-%m-%d', '%d.%m.%Y %H:%M']:
                                        try:
                                            deadline_date = datetime.strptime(deadline[:10], fmt[:len(deadline[:10])])
                                            break
                                        except:
                                            continue

                                    if deadline_date:
                                        days_left = (deadline_date - datetime.now()).days
                                        if days_left < min_deadline_days:
                                            logger.debug(f"      ⛔ Мало дней до дедлайна ({days_left}): {tender.get('name', '')[:50]}")
                                            continue
                                except Exception as e:
                                    logger.debug(f"      ⚠️ Не удалось проверить дедлайн: {e}")

                        seen_numbers.add(number)
                        all_results.append(tender)

                logger.info(f"      Найдено: {len(results)}, уникальных всего: {len(all_results)}")

                # Достаточно результатов
                if len(all_results) >= max_tenders:
                    break

            search_results = all_results[:max_tenders]
            logger.info(f"   ✅ Итого найдено тендеров: {len(search_results)}")

            # === Обогащаем тендеры данными со страниц ===
            if search_results:
                logger.info(f"   📥 Загрузка полных данных тендеров...")
                enriched_results = []
                for i, tender in enumerate(search_results):
                    try:
                        logger.debug(f"      [{i+1}/{len(search_results)}] Обогащение: {tender.get('number', 'N/A')}")
                        enriched = self.parser.enrich_tender_from_page(tender)
                        enriched_results.append(enriched)
                    except Exception as e:
                        logger.error(f"      ⚠️ Ошибка обогащения тендера {tender.get('number', 'N/A')}: {e}", exc_info=True)
                        enriched_results.append(tender)
                search_results = enriched_results
                logger.info(f"   ✅ Данные обогащены")

            # Если RSS не вернул результатов - возвращаем пустой ответ
            if not search_results:
                logger.warning("⚠️ RSS feed не вернул результаты")
                return {
                    'tenders': [],
                    'total_found': 0,
                    'matches': [],
                    'stats': {
                        'search_queries': search_queries,
                        'search_query': ', '.join(search_queries),  # Для совместимости с HTML шаблоном
                        'expanded_keywords': expanded_keywords or [],
                        'original_keywords': original_keywords
                    }
                }

            # Ранжируем результаты через SmartMatcher
            # Создаем временный фильтр для матчинга
            temp_filter = {
                'id': filter_data['id'],
                'name': filter_data['name'],
                'keywords': original_keywords,  # Используем оригинальные для матчинга
                'price_min': price_min,
                'price_max': price_max,
                'regions': regions
            }

            matches = []
            for tender in search_results:
                # ФИЛЬТР 1: Исключаем старые тендеры (старше 2 лет)
                published_str = tender.get('published', '')
                if published_str:
                    try:
                        # Парсим дату
                        if 'GMT' in published_str:
                            from email.utils import parsedate_to_datetime
                            published_dt = parsedate_to_datetime(published_str)
                        else:
                            from datetime import datetime as dt
                            published_dt = dt.strptime(published_str[:10], '%Y-%m-%d')

                        # Проверяем что тендер не старше 2 лет
                        from datetime import datetime, timedelta
                        two_years_ago = datetime.now() - timedelta(days=730)
                        if published_dt < two_years_ago:
                            logger.debug(f"      ⛔ Исключен (старый, {published_dt.year}): {tender.get('name', '')[:60]}")
                            continue
                    except:
                        pass  # Если не удалось распарсить - пропускаем проверку

                # ФИЛЬТР 2: ДВОЙНАЯ ПРОВЕРКА ТИПА - дополнительная защита от услуг в товарах
                if tender_types and len(tender_types) > 0:
                    tender_name = tender.get('name', '').lower()
                    tender_summary = tender.get('summary', '').lower()
                    full_text = tender_name + ' ' + tender_summary

                    # Если выбраны только товары - исключаем явные услуги
                    if tender_types == ['товары']:
                        service_indicators = ['оказание услуг', 'выполнение работ', 'медицинские услуги',
                                             'ремонт', 'обслуживание', 'услуги по', 'работы по']
                        if any(ind in full_text for ind in service_indicators):
                            logger.debug(f"      ⛔ Исключен при scoring (услуга): {tender.get('name', '')[:60]}")
                            continue

                match_result = self.matcher.match_tender(tender, temp_filter)

                # Проверяем что match_result не None
                # Порог 30 - базовый уровень для показа результатов
                # Если SmartMatcher вернул результат - значит есть хоть какое-то совпадение
                if match_result and match_result.get('score', 0) >= 30:
                    tender_with_score = tender.copy()
                    tender_with_score['match_score'] = match_result['score']
                    tender_with_score['match_reasons'] = match_result.get('reasons', [])
                    matches.append(tender_with_score)

            # Сортируем по скору
            matches.sort(key=lambda x: x['match_score'], reverse=True)

            logger.info(f"   🎯 Совпадений (score ≥ 30): {len(matches)}")

            return {
                'tenders': search_results,
                'total_found': len(search_results),
                'matches': matches,
                'stats': {
                    'search_queries': search_queries,
                    'search_query': ', '.join(search_queries),  # Для совместимости с HTML шаблоном
                    'expanded_keywords': expanded_keywords or [],
                    'original_keywords': original_keywords,
                    'high_score_count': len([m for m in matches if m['match_score'] >= 70]),
                    'medium_score_count': len([m for m in matches if 40 <= m['match_score'] < 70])
                }
            }

        except Exception as e:
            logger.error(f"❌ Ошибка поиска: {e}", exc_info=True)
            return {
                'tenders': [],
                'total_found': 0,
                'matches': [],
                'stats': {
                    'error': str(e)
                },
                'error': str(e)
            }

    async def generate_html_report(
        self,
        search_results: Dict[str, Any],
        filter_data: Dict[str, Any],
        output_path: Path = None
    ) -> Path:
        """
        Генерирует HTML отчет с результатами поиска.

        Args:
            search_results: Результаты от search_by_filter()
            filter_data: Данные фильтра
            output_path: Путь для сохранения отчета

        Returns:
            Path к созданному HTML файлу
        """
        logger.info(f"📄 Генерация HTML отчета...")

        if output_path is None:
            output_dir = Path(__file__).parent.parent / 'output' / 'reports'
            output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = output_dir / f"filter_{filter_data['id']}_{timestamp}.html"

        try:
            # Формируем данные для отчета
            report_data = {
                'filter_name': filter_data['name'],
                'search_query': search_results['stats'].get('search_query', ''),
                'original_keywords': search_results['stats'].get('original_keywords', []),
                'expanded_keywords': search_results['stats'].get('expanded_keywords', []),
                'total_found': search_results['total_found'],
                'matches': search_results['matches'],
                'high_score_count': search_results['stats'].get('high_score_count', 0),
                'medium_score_count': search_results['stats'].get('medium_score_count', 0),
                'generated_at': datetime.now().isoformat()
            }

            # Используем генератор с JavaScript фильтрацией
            from tender_sniper.all_tenders_report import generate_html_report as generate_filtered_html

            # Преобразуем данные в формат all_tenders_report
            tenders_for_report = []
            for match in search_results['matches']:
                tenders_for_report.append({
                    'number': match.get('number', 'N/A'),
                    'name': match.get('name', 'Без названия'),
                    'price': match.get('price', 0),
                    'url': match.get('url', ''),
                    'customer_name': match.get('customer', 'Не указан'),
                    'region': match.get('customer_region', 'Не указан'),
                    'published_date': match.get('published', ''),
                    'submission_deadline': match.get('submission_deadline', ''),  # ВАЖНО: Срок подачи заявок
                    'sent_at': datetime.now().isoformat(),
                    'filter_name': filter_data['name']
                })

            # Генерируем HTML с JavaScript фильтрацией
            html_content = generate_filtered_html(
                tenders=tenders_for_report,
                username="Пользователь",
                total_count=search_results['total_found']
            )

            # Сохраняем
            output_path.write_text(html_content, encoding='utf-8')

            logger.info(f"   ✅ Отчет сохранен с JavaScript фильтрацией: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"❌ Ошибка генерации отчета: {e}", exc_info=True)
            raise

    def _build_html_content(self, data: Dict[str, Any]) -> str:
        """Формирует HTML контент отчета."""

        # Формируем список тендеров
        tenders_html = ""
        for i, tender in enumerate(data['matches'], 1):
            score = tender.get('match_score', 0)
            score_class = self._get_score_class(score)
            score_emoji = self._get_score_emoji(score)

            reasons_html = "<br>".join([
                f"• {reason}" for reason in tender.get('match_reasons', [])
            ])

            # Форматируем цену (НМЦК)
            price_display = tender.get('price_formatted') or tender.get('price', 'Не указана')
            if isinstance(price_display, (int, float)):
                price_display = f"{price_display:,.0f} ₽".replace(',', ' ')

            # Форматируем дату публикации
            published = tender.get('published_formatted') or tender.get('published', '') or 'Н/Д'
            # Если дата в формате GMT, пробуем конвертировать
            if 'GMT' in str(published):
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(published)
                    published = dt.strftime('%d.%m.%Y %H:%M')
                except:
                    pass

            # Дата окончания подачи заявок
            deadline = tender.get('submission_deadline', 'Н/Д')

            # Заказчик и его местонахождение
            customer = tender.get('customer', '')
            customer_city = tender.get('customer_city', '')
            customer_region = tender.get('customer_region', '')

            # Формируем строку местонахождения: "г. Прохладный, Кабардино-Балкарская Республика"
            if customer_city and customer_region:
                # Проверяем что город не дублируется в названии региона
                city_name = customer_city.replace('г. ', '')
                if city_name.lower() not in customer_region.lower():
                    location_display = f"{customer_city}, {customer_region}"
                else:
                    location_display = customer_region
            elif customer_city:
                location_display = customer_city
            elif customer_region:
                location_display = customer_region
            else:
                location_display = 'Н/Д'

            tenders_html += f"""
            <div class="tender-card">
                <div class="tender-header">
                    <span class="tender-number">{i}. №{tender.get('number', 'Н/Д')}</span>
                    <span class="score-badge {score_class}">{score_emoji} {score}/100</span>
                </div>
                <h3 class="tender-title">{tender.get('name', 'Без названия')}</h3>
                <div class="tender-details">
                    <p><strong>💰 НМЦК:</strong> {price_display}</p>
                    <p><strong>📅 Размещено:</strong> {published}</p>
                    <p><strong>⏰ Окончание подачи:</strong> {deadline}</p>
                    <p><strong>🏢 Заказчик:</strong> {customer if customer else 'Н/Д'}</p>
                    <p><strong>📍 Регион:</strong> {location_display}</p>
                </div>
                <div class="match-reasons">
                    <strong>Причины совпадения:</strong><br>
                    {reasons_html if reasons_html else '• Найдено по ключевым словам'}
                </div>
                <div class="tender-actions">
                    <a href="{tender.get('url', '#')}" target="_blank" class="btn-primary">Открыть на zakupki.gov.ru</a>
                </div>
            </div>
            """

        # Формируем расширенные ключевые слова
        expanded_keywords_html = ""
        if data.get('expanded_keywords'):
            expanded_keywords_html = f"""
            <div class="info-block">
                <h3>🤖 AI расширение запроса</h3>
                <p><strong>Исходные критерии:</strong> {', '.join(data['original_keywords'])}</p>
                <p><strong>Расширенные термины:</strong> {', '.join(data['expanded_keywords'][:15])}</p>
                <p class="hint">AI добавил {len(data['expanded_keywords'])} связанных терминов для более точного поиска</p>
            </div>
            """

        # Полный HTML
        html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Результаты поиска: {data['filter_name']}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f7fa;
            padding: 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 12px;
            box-shadow: 0 2px 20px rgba(0,0,0,0.1);
        }}
        .header {{
            border-bottom: 3px solid #4CAF50;
            padding-bottom: 20px;
            margin-bottom: 30px;
        }}
        h1 {{
            color: #2c3e50;
            font-size: 32px;
            margin-bottom: 10px;
        }}
        .summary {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin: 30px 0;
        }}
        .summary-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
        }}
        .summary-card h3 {{
            font-size: 36px;
            margin-bottom: 5px;
        }}
        .summary-card p {{
            opacity: 0.9;
            font-size: 14px;
        }}
        .info-block {{
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            margin: 20px 0;
            border-left: 4px solid #4CAF50;
        }}
        .info-block h3 {{
            color: #2c3e50;
            margin-bottom: 10px;
        }}
        .hint {{
            color: #6c757d;
            font-size: 14px;
            font-style: italic;
        }}
        .tender-card {{
            background: white;
            border: 1px solid #e1e8ed;
            border-radius: 8px;
            padding: 25px;
            margin-bottom: 20px;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .tender-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }}
        .tender-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }}
        .tender-number {{
            font-size: 14px;
            color: #6c757d;
            font-family: 'Courier New', monospace;
        }}
        .score-badge {{
            padding: 6px 12px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 14px;
        }}
        .score-high {{
            background: #d4edda;
            color: #155724;
        }}
        .score-medium {{
            background: #fff3cd;
            color: #856404;
        }}
        .score-low {{
            background: #f8d7da;
            color: #721c24;
        }}
        .tender-title {{
            color: #2c3e50;
            font-size: 20px;
            margin-bottom: 15px;
            line-height: 1.4;
        }}
        .tender-details {{
            color: #555;
            margin-bottom: 15px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 8px 20px;
        }}
        .tender-details p {{
            margin: 4px 0;
            font-size: 14px;
        }}
        .tender-details strong {{
            color: #2c3e50;
        }}
        .match-reasons {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 6px;
            margin: 15px 0;
            font-size: 14px;
        }}
        .match-reasons strong {{
            color: #2c3e50;
        }}
        .tender-actions {{
            margin-top: 15px;
        }}
        .btn-primary {{
            display: inline-block;
            background: #4CAF50;
            color: white;
            padding: 10px 20px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: 500;
            transition: background 0.3s;
        }}
        .btn-primary:hover {{
            background: #45a049;
        }}
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #e1e8ed;
            text-align: center;
            color: #6c757d;
            font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎯 {data['filter_name']}</h1>
            <p>Поисковый запрос: <strong>{data['search_query']}</strong></p>
            <p>Сгенерировано: {datetime.fromisoformat(data['generated_at']).strftime('%d.%m.%Y %H:%M')}</p>
        </div>

        <div class="summary">
            <div class="summary-card">
                <h3>{data['total_found']}</h3>
                <p>Всего найдено</p>
            </div>
            <div class="summary-card">
                <h3>{data['high_score_count']}</h3>
                <p>Отличные совпадения (≥70)</p>
            </div>
            <div class="summary-card">
                <h3>{data['medium_score_count']}</h3>
                <p>Хорошие совпадения (40-69)</p>
            </div>
        </div>

        {expanded_keywords_html}

        <h2 style="margin: 30px 0 20px; color: #2c3e50;">📋 Найденные тендеры</h2>
        {tenders_html if tenders_html else '<p class="hint">Тендеров с достаточным уровнем совпадения не найдено. Попробуйте изменить критерии поиска.</p>'}

        <div class="footer">
            <p>🤖 Сгенерировано Tender Sniper AI Bot</p>
            <p>Данные актуальны на момент генерации отчета</p>
        </div>
    </div>
</body>
</html>
        """

        return html

    def _get_score_class(self, score: int) -> str:
        """Возвращает CSS класс для скора."""
        if score >= 70:
            return "score-high"
        elif score >= 40:
            return "score-medium"
        else:
            return "score-low"

    def _get_score_emoji(self, score: int) -> str:
        """Возвращает эмодзи для скора."""
        if score >= 80:
            return "🔥"
        elif score >= 70:
            return "✨"
        elif score >= 50:
            return "📌"
        else:
            return "ℹ️"
