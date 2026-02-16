"""
Instant Search - мгновенный поиск тендеров по критериям фильтра.

Выполняет поиск, ранжирование и генерацию HTML отчета.
"""

import sys
import re
import asyncio
import functools
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parsers.zakupki_rss_parser import ZakupkiRSSParser
from tender_sniper.matching import SmartMatcher
from tender_sniper.matching.smart_matcher import detect_red_flags
from src.utils.transliterator import Transliterator
from tender_sniper.ai_relevance_checker import get_relevance_checker, check_tender_relevance

logger = logging.getLogger(__name__)


class InstantSearch:
    """Мгновенный поиск тендеров по фильтру."""

    # Кэш обогащённых тендеров (по номеру тендера)
    # Сохраняет данные на время сессии, экономит HTTP запросы
    _enrichment_cache: Dict[str, Dict[str, Any]] = {}
    _cache_max_size = 500  # Максимум кэшированных тендеров

    # Минимальный pre-score для обогащения (без обогащения - пропускаем)
    MIN_PRESCORE_FOR_ENRICHMENT = 1

    def __init__(self):
        """Инициализация компонентов поиска."""
        self.parser = ZakupkiRSSParser()
        self.matcher = SmartMatcher()

    @classmethod
    def clear_cache(cls):
        """Очищает кэш обогащённых тендеров."""
        cache_size = len(cls._enrichment_cache)
        cls._enrichment_cache.clear()
        logger.info(f"🗑️ Кэш обогащения очищен ({cache_size} записей)")

    @classmethod
    def get_cache_stats(cls) -> Dict[str, int]:
        """Возвращает статистику кэша."""
        return {
            'size': len(cls._enrichment_cache),
            'max_size': cls._cache_max_size
        }

    async def search_by_filter(
        self,
        filter_data: Dict[str, Any],
        max_tenders: int = 25,
        expanded_keywords: List[str] = None,
        use_ai_check: bool = True,
        user_id: int = None,
        subscription_tier: str = 'trial'
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
        publication_days = filter_data.get('publication_days')  # 🧪 БЕТА: фильтр по дате публикации

        # Формируем список поисковых запросов
        # Каждое оригинальное ключевое слово - отдельный запрос (OR логика)
        # + добавляем топ-3 расширенных термина
        search_queries = original_keywords.copy()

        # Добавляем расширенные термины (если есть)
        if expanded_keywords:
            extra_keywords = [kw for kw in expanded_keywords if kw not in original_keywords][:3]
            search_queries.extend(extra_keywords)

        logger.debug(f"   🔑 Запросы ({len(search_queries)}): {', '.join(search_queries)}")
        logger.debug(f"   💰 Цена: {price_min} - {price_max}, 📍 Регионы: {regions if regions else 'Все'}")

        # По умолчанию ищем только АКТИВНЫЕ тендеры (идёт приём заявок)
        effective_purchase_stage = purchase_stage if purchase_stage else "submission"

        try:
            # Выполняем ОТДЕЛЬНЫЙ поиск для каждого ключевого слова
            # Это OR логика - тендер найдётся если содержит ЛЮБОЕ из слов
            all_results = []
            seen_numbers = set()

            results_per_query = max(10, max_tenders // len(search_queries) + 5)

            for query in search_queries:
                # 🧪 БЕТА: Генерация вариантов транслитерации (латиница → кириллица)
                query_variants = Transliterator.generate_variants(query)

                for variant in query_variants:
                    logger.debug(f"   🔎 Поиск: '{variant}'" + (" (транслит)" if variant != query else ""))

                    # Определяем тип закупки для RSS
                    # Если выбраны все типы (3) или ничего не выбрано - не фильтруем
                    # Если выбран 1 тип - фильтруем по нему
                    # Если выбрано 2 типа - не фильтруем на RSS уровне (фильтрация на клиенте)
                    all_types = {'товары', 'услуги', 'работы'}
                    selected_types_set = set(tender_types) if tender_types else set()

                    if len(selected_types_set) == 1:
                        # Только 1 тип выбран - фильтруем по нему
                        tender_type_for_rss = tender_types[0]
                    elif len(selected_types_set) >= 3 or len(selected_types_set) == 0:
                        # Все типы или ничего - без фильтрации
                        tender_type_for_rss = None
                    else:
                        # 2 типа - без фильтрации на RSS уровне
                        tender_type_for_rss = None

                    # Запускаем синхронный HTTP запрос в thread executor,
                    # чтобы не блокировать event loop бота
                    loop = asyncio.get_event_loop()
                    results = await loop.run_in_executor(
                        None,
                        functools.partial(
                            self.parser.search_tenders_rss,
                            keywords=variant,
                            price_min=price_min,
                            price_max=price_max,
                            regions=regions,
                            max_results=results_per_query,
                            tender_type=tender_type_for_rss,
                            law_type=law_type,
                            purchase_stage=effective_purchase_stage,
                            purchase_method=purchase_method,
                        )
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

                            # === ФИЛЬТРАЦИЯ ПО КЛЮЧЕВЫМ СЛОВАМ ===
                            # RSS API может возвращать нерелевантные результаты
                            # Проверяем, что тендер содержит хотя бы одно ключевое слово
                            keyword_found = False
                            for keyword in original_keywords:
                                kw_lower = keyword.lower()
                                # Проверяем точное вхождение слова (с границами для коротких слов)
                                if len(kw_lower) <= 4:
                                    pattern = r'\b' + re.escape(kw_lower) + r'\b'
                                else:
                                    # Для длинных слов - минимум 7 символов корня (было 5)
                                    # "разработка" → "разрабо" (не "разра" → ловит "разгрузка")
                                    min_chars = min(len(kw_lower), max(7, len(kw_lower) - 3))
                                    pattern = r'\b' + re.escape(kw_lower[:min_chars])

                                if re.search(pattern, tender_text, re.IGNORECASE):
                                    keyword_found = True
                                    break

                            if not keyword_found:
                                logger.debug(f"      ⛔ Не содержит ключевых слов: {tender.get('name', '')[:60]}")
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

                            # === ОБЯЗАТЕЛЬНАЯ ПРОВЕРКА: дедлайн не просрочен ===
                            # Отсекаем тендеры с просроченным дедлайном (баг zakupki.gov.ru)
                            deadline = tender.get('submission_deadline') or tender.get('deadline') or tender.get('end_date')
                            if deadline:
                                try:
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

                                        # Просроченный тендер - пропускаем
                                        if days_left < 0:
                                            logger.debug(f"      ⛔ Просрочен ({days_left} дн.): {tender.get('name', '')[:50]}")
                                            continue

                                        # Проверяем минимум дней до дедлайна (если указано)
                                        if min_deadline_days and days_left < min_deadline_days:
                                            logger.debug(f"      ⛔ Мало дней до дедлайна ({days_left}): {tender.get('name', '')[:50]}")
                                            continue
                                except Exception as e:
                                    logger.debug(f"      ⚠️ Не удалось проверить дедлайн: {e}")

                            seen_numbers.add(number)
                            all_results.append(tender)

                    logger.debug(f"      Найдено: {len(results)}, уникальных всего: {len(all_results)}")

                    # Достаточно результатов - выходим из обоих циклов
                    if len(all_results) >= max_tenders:
                        break

                # Проверка после внутреннего цикла
                if len(all_results) >= max_tenders:
                    break

            search_results = all_results[:max_tenders]
            logger.info(f"   ✅ Итого найдено тендеров: {len(search_results)}")

            # === ОПТИМИЗАЦИЯ: Pre-scoring + обогащение только нужных тендеров ===
            # Вместо обогащения ВСЕХ тендеров (медленно), сначала делаем быстрый pre-scoring
            # и обогащаем только те, которые потенциально релевантны

            if search_results:
                # 1. Создаём временные фильтры для pre-scoring и финального скоринга
                temp_filter = {
                    'id': filter_data['id'],
                    'name': filter_data['name'],
                    'keywords': original_keywords,
                    'price_min': price_min,
                    'price_max': price_max,
                    'regions': regions
                }

                # Pre-scoring фильтр БЕЗ регионов — RSS данные часто не содержат регион,
                # он появляется только после обогащения. Регион проверяется на финальном этапе.
                pre_score_filter = {
                    'id': filter_data['id'],
                    'name': filter_data['name'],
                    'keywords': original_keywords,
                    'price_min': price_min,
                    'price_max': price_max,
                    'regions': []  # Не проверяем регион до обогащения
                }

                # 2. Quick pre-scoring (без обогащения, на основе RSS данных)
                logger.debug(f"   ⚡ Быстрый pre-scoring ({len(search_results)} тендеров)...")
                tenders_to_enrich = []
                tenders_skipped = 0

                for tender in search_results:
                    tender_number = tender.get('number', '')

                    # Проверяем кэш обогащённых тендеров
                    if tender_number and tender_number in self._enrichment_cache:
                        # Используем кэшированные данные
                        cached = self._enrichment_cache[tender_number]
                        tender.update(cached)

                        # Кэшированные тендеры уже обогащены → полная проверка с регионом
                        pre_match = self.matcher.match_tender(tender, temp_filter)
                        if pre_match is None:
                            tenders_skipped += 1
                            logger.debug(f"      ⏭️ Кэш: отклонён SmartMatcher: {tender.get('name', '')[:50]}")
                            continue

                        tenders_to_enrich.append(tender)
                        logger.debug(f"      💾 Из кэша: {tender_number}")
                        continue

                    # Pre-scoring на основе RSS данных (без HTTP запросов)
                    # Используем фильтр БЕЗ регионов — регион проверяется после обогащения
                    pre_match = self.matcher.match_tender(tender, pre_score_filter)
                    pre_score = pre_match.get('score', 0) if pre_match else 0

                    # Если pre-score слишком низкий - пропускаем обогащение
                    if pre_score < self.MIN_PRESCORE_FOR_ENRICHMENT:
                        tenders_skipped += 1
                        logger.debug(f"      ⏭️ Pre-score {pre_score} < {self.MIN_PRESCORE_FOR_ENRICHMENT}, пропускаем обогащение: {tender.get('name', '')[:50]}")
                        continue

                    tenders_to_enrich.append(tender)

                if tenders_skipped > 0:
                    logger.debug(f"   ⏭️ Пропущено по pre-score: {tenders_skipped}")

                # 3. Обогащаем только отобранные тендеры
                if tenders_to_enrich:
                    logger.debug(f"   📥 Загрузка данных для {len(tenders_to_enrich)} тендеров (из {len(search_results)})...")
                    enriched_results = []

                    for i, tender in enumerate(tenders_to_enrich):
                        tender_number = tender.get('number', '')

                        # Уже обогащён из кэша - пропускаем
                        if tender_number in self._enrichment_cache:
                            enriched_results.append(tender)
                            continue

                        try:
                            # Синхронный HTTP в thread executor
                            loop = asyncio.get_event_loop()
                            enriched = await loop.run_in_executor(
                                None, self.parser.enrich_tender_from_page, tender
                            )
                            enriched_results.append(enriched)

                            # Сохраняем в кэш (ограничиваем размер)
                            if tender_number and len(self._enrichment_cache) < self._cache_max_size:
                                # Кэшируем только обогащённые поля
                                self._enrichment_cache[tender_number] = {
                                    'price': enriched.get('price'),
                                    'price_formatted': enriched.get('price_formatted'),
                                    'submission_deadline': enriched.get('submission_deadline'),
                                    'customer_region': enriched.get('customer_region'),
                                    'customer_city': enriched.get('customer_city'),
                                    'customer': enriched.get('customer'),
                                    'customer_address': enriched.get('customer_address'),
                                }
                        except Exception as e:
                            logger.error(f"      ⚠️ Ошибка обогащения {tender_number}: {e}")
                            enriched_results.append(tender)

                    search_results = enriched_results
                    logger.debug(f"   ✅ Данные обогащены")
                else:
                    search_results = []
                    logger.debug(f"   ℹ️ Нет тендеров для обогащения")

            # === CLIENT-SIDE ФИЛЬТРАЦИЯ ПО СТАТУСУ ЗАКУПКИ ===
            # Режим "archive" - ищем ТОЛЬКО архивные тендеры (с прошедшим дедлайном)
            # Режим "submission" - исключаем архивные тендеры
            archive_mode = purchase_stage == "archive"

            if (purchase_stage == "submission" or archive_mode) and search_results:
                from datetime import datetime as dt
                active_results = []
                archived_count = 0

                for tender in search_results:
                    deadline_str = tender.get('submission_deadline', '')
                    if deadline_str:
                        try:
                            # Парсим дату дедлайна (форматы: "DD.MM.YYYY HH:MM" или "DD.MM.YYYY")
                            deadline_date = None
                            deadline_str_clean = deadline_str.strip()

                            # Пробуем разные форматы
                            if len(deadline_str_clean) >= 16:  # "DD.MM.YYYY HH:MM"
                                try:
                                    deadline_date = dt.strptime(deadline_str_clean[:16], '%d.%m.%Y %H:%M')
                                except ValueError:
                                    pass

                            if not deadline_date and len(deadline_str_clean) >= 10:  # "DD.MM.YYYY"
                                try:
                                    deadline_date = dt.strptime(deadline_str_clean[:10], '%d.%m.%Y')
                                except ValueError:
                                    try:
                                        deadline_date = dt.strptime(deadline_str_clean[:10], '%Y-%m-%d')
                                    except ValueError:
                                        pass

                            if deadline_date:
                                is_archived = deadline_date < dt.now()

                                if archive_mode:
                                    # Режим архива: ОСТАВЛЯЕМ только архивные
                                    if not is_archived:
                                        logger.debug(f"      ⛔ Не архивный (дедлайн {deadline_str}): {tender.get('name', '')[:50]}")
                                        continue
                                    archived_count += 1
                                else:
                                    # Режим подачи заявок: ИСКЛЮЧАЕМ архивные
                                    if is_archived:
                                        archived_count += 1
                                        logger.debug(f"      ⛔ Архивный (дедлайн {deadline_str}): {tender.get('name', '')[:50]}")
                                        continue
                        except Exception as e:
                            logger.debug(f"      ⚠️ Не удалось проверить дедлайн: {e}")

                    active_results.append(tender)

                if archive_mode:
                    logger.info(f"   📦 Найдено архивных тендеров: {archived_count}")
                elif archived_count > 0:
                    logger.info(f"   📦 Исключено архивных тендеров: {archived_count}")
                search_results = active_results
                logger.info(f"   ✅ Итого после фильтрации: {len(search_results)}")

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

            # ============================================
            # СТРОГАЯ ФИЛЬТРАЦИЯ ПО ОРИГИНАЛЬНЫМ КЛЮЧЕВЫМ СЛОВАМ
            # ============================================
            # Применяется ТОЛЬКО для расширенного поиска (когда есть expanded_keywords)
            # Для точного поиска RSS уже ищет по точным ключевым словам

            # ============================================
            # ОБЯЗАТЕЛЬНАЯ ФИЛЬТРАЦИЯ ПО КЛЮЧЕВЫМ СЛОВАМ
            # ============================================
            # RSS API zakupki.gov.ru может возвращать нерелевантные результаты,
            # поэтому ВСЕГДА проверяем что тендер содержит хотя бы одно ключевое слово

            def check_keyword_match(tender_text: str, keywords_list: List[str]) -> Optional[str]:
                """Проверяет, содержит ли тендер хотя бы одно ключевое слово."""
                tender_lower = tender_text.lower()

                for kw in keywords_list:
                    kw_lower = kw.lower().strip()

                    # Пропускаем стоп-слова и очень короткие слова
                    if len(kw_lower) < 2 or kw_lower in {'закупка', 'закупки', 'услуга', 'услуги',
                                                          'поставка', 'поставки', 'работа', 'работы',
                                                          'для', 'нужд', 'оказание', 'выполнение'}:
                        continue

                    # Прямое вхождение (регистронезависимо)
                    if kw_lower in tender_lower:
                        return kw

                    # Для латинских брендов также проверяем варианты транслитерации
                    if kw.isascii():
                        # Генерируем кириллические варианты
                        cyrillic_variants = Transliterator.generate_variants(kw)
                        for variant in cyrillic_variants:
                            if variant.lower() != kw_lower and variant.lower() in tender_lower:
                                return kw

                    # Проверяем корень слова (для русской морфологии)
                    if len(kw_lower) >= 5 and not kw.isascii():
                        root = kw_lower[:max(5, len(kw_lower) - 2)]
                        if root in tender_lower:
                            return kw

                    # Проверяем известные синонимы
                    synonyms_map = {
                        'linux': ['линукс', 'ubuntu', 'убунту', 'debian', 'centos', 'redhat', 'astra', 'астра', 'альт'],
                        'линукс': ['linux', 'ubuntu', 'убунту', 'debian', 'centos', 'redhat', 'astra', 'астра'],
                        'lenovo': ['леново', 'thinkpad', 'синкпад'],
                        'dell': ['делл'],
                        'hp': ['hewlett', 'packard', 'хьюлетт', 'паккард'],
                        'cisco': ['циско'],
                        'аутентификация': ['авторизация', '2fa', 'mfa', 'двухфакторн', 'многофакторн'],
                        'сервер': ['серверн', 'blade'],
                        'антивирус': ['касперский', 'dr.web', 'eset', 'антивирусн'],
                    }
                    for synonym in synonyms_map.get(kw_lower, []):
                        if synonym in tender_lower:
                            return kw

                return None

            # ВСЕГДА фильтруем по ключевым словам (и для точного, и для расширенного поиска)
            filtered_results = []
            keywords_to_check = original_keywords if original_keywords else search_queries

            for tender in search_results:
                tender_text = f"{tender.get('name', '')} {tender.get('summary', '')} {tender.get('description', '')}"
                matched_kw = check_keyword_match(tender_text, keywords_to_check)
                if matched_kw:
                    tender['_matched_original_keyword'] = matched_kw
                    filtered_results.append(tender)
                else:
                    logger.debug(f"   ⛔ Исключен (нет ключевых слов): {tender.get('name', '')[:60]}")

            logger.info(f"   🎯 После фильтрации по ключевым словам: {len(filtered_results)}/{len(search_results)}")
            search_results = filtered_results

            # Ранжируем результаты через SmartMatcher
            # temp_filter уже создан ранее для pre-scoring

            matches = []
            for tender in search_results:
                # ФИЛЬТР 1: Исключаем старые тендеры (старше 2 лет или старше publication_days)
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

                        from datetime import datetime, timedelta

                        # 🧪 БЕТА: Фильтр по дате публикации (если указано)
                        if publication_days:
                            cutoff_date = datetime.now() - timedelta(days=publication_days)
                            if published_dt < cutoff_date:
                                logger.debug(f"      ⛔ Исключен (старше {publication_days} дней): {tender.get('name', '')[:60]}")
                                continue
                        else:
                            # По умолчанию не старше 2 лет
                            two_years_ago = datetime.now() - timedelta(days=730)
                            if published_dt < two_years_ago:
                                logger.debug(f"      ⛔ Исключен (старый, {published_dt.year}): {tender.get('name', '')[:60]}")
                                continue
                    except:
                        pass  # Если не удалось распарсить - пропускаем проверку

                # ФИЛЬТР 2: ДВОЙНАЯ ПРОВЕРКА ТИПА - дополнительная защита от услуг в товарах
                if tender_types and len(tender_types) > 0:
                    tender_name = tender.get('name', '').lower()

                    # Если выбраны только товары - исключаем явные услуги
                    if tender_types == ['товары']:
                        # ШАГ 1: Название НАЧИНАЕТСЯ с сервисного слова → точно услуга
                        service_start = ['услуга ', 'услуги ', 'ремонт ', 'обслуживание ',
                                        'выполнение ', 'оказание ', 'работы по ',
                                        'техническое обслуживание', 'сервисное обслуживание',
                                        'монтаж ', 'демонтаж ', 'проектирование ',
                                        'разработка проект', 'консультирование ',
                                        'заправка ', 'восстановление ', 'диагностика ',
                                        'расчет ', 'расчёт ', 'создание ']
                        if any(tender_name.startswith(s) for s in service_start):
                            logger.debug(f"      ⛔ Исключен (услуга по началу): {tender.get('name', '')[:60]}")
                            continue

                        # ШАГ 2: Содержит индикаторы услуг ВЕЗДЕ в названии
                        service_indicators = ['оказание услуг', 'выполнение работ', 'проведение работ',
                                             'медицинские услуги', 'услуги по', 'услуга по',
                                             'работы по ', 'ремонт и обслуживание',
                                             'техническое обслуживание', 'сервисное обслуживание',
                                             'текущий ремонт', 'капитальный ремонт',
                                             'заправка картридж', 'восстановление картридж',
                                             'заправка и восстановление', 'диагностика и ремонт']
                        if any(ind in tender_name for ind in service_indicators):
                            logger.debug(f"      ⛔ Исключен (индикатор услуги): {tender.get('name', '')[:60]}")
                            continue

                match_result = self.matcher.match_tender(tender, temp_filter)

                # match_tender возвращает None = жёсткое отклонение (регион/цена/исключения)
                if match_result is None:
                    logger.debug(f"      ⛔ Отклонён SmartMatcher (регион/цена/исключение): {tender.get('name', '')[:60]}")
                    continue

                tender_with_score = tender.copy()

                if match_result.get('score', 0) > 0:
                    # Есть совпадение - используем score от matcher
                    tender_with_score['match_score'] = match_result['score']
                    tender_with_score['match_reasons'] = match_result.get('reasons', [])
                    tender_with_score['matched_keywords'] = match_result.get('matched_keywords', [])
                else:
                    # Нет совпадения по SmartMatcher, но тендер найден RSS по ключевым словам
                    # Даём базовый score 20 чтобы показать пользователю
                    tender_with_score['match_score'] = 20
                    tender_with_score['match_reasons'] = ['Найден по поисковому запросу RSS']
                    tender_with_score['matched_keywords'] = []

                # Detect red flags for each tender
                tender_with_score['red_flags'] = detect_red_flags(tender_with_score)

                matches.append(tender_with_score)

            # ============================================
            # AI СЕМАНТИЧЕСКАЯ ПРОВЕРКА
            # ============================================
            ai_intent = filter_data.get('ai_intent')
            ai_filtered_matches = []
            ai_rejected_count = 0

            # Если ai_intent отсутствует, генерируем его на лету из названия фильтра и ключевых слов
            if use_ai_check and not ai_intent and original_keywords:
                filter_name = filter_data.get('name', '')
                ai_intent = f"Ищу тендеры по запросу '{filter_name}'. Ключевые слова: {', '.join(original_keywords)}. Меня интересуют ТОЛЬКО тендеры, которые напрямую связаны с этими ключевыми словами."
                logger.debug(f"   ⚠️ ai_intent отсутствует, сгенерирован fallback")

            if use_ai_check and ai_intent and matches:

                for tender in matches:
                    tender_score = tender.get('match_score', 0)

                    # Высокий score (>=85) — пропускаем без AI проверки
                    if tender_score >= 85:
                        tender['ai_verified'] = False
                        tender['ai_skipped'] = True
                        ai_filtered_matches.append(tender)
                        continue

                    # Проверяем через AI
                    try:
                        ai_result = await check_tender_relevance(
                            tender_name=tender.get('name', ''),
                            filter_intent=ai_intent,
                            filter_keywords=original_keywords,
                            tender_description=tender.get('description', '') or tender.get('summary', ''),
                            user_id=user_id,
                            subscription_tier=subscription_tier,
                            tender_types=tender_types
                        )

                        if ai_result.get('is_relevant', True):
                            # AI подтвердил релевантность — сохраняем SmartMatcher score
                            tender['ai_verified'] = True
                            tender['ai_confidence'] = ai_result.get('confidence', 0)
                            tender['ai_reason'] = ai_result.get('reason', '')
                            ai_filtered_matches.append(tender)
                        else:
                            ai_rejected_count += 1

                        # Проверяем квоту
                        if ai_result.get('source') == 'quota_exceeded':
                            logger.warning(f"   ⚠️ Квота AI исчерпана, остальные без проверки")
                            # Добавляем оставшиеся без AI проверки
                            remaining_idx = matches.index(tender) + 1
                            for remaining in matches[remaining_idx:]:
                                remaining['ai_verified'] = False
                                remaining['ai_skipped'] = True
                                ai_filtered_matches.append(remaining)
                            break

                    except Exception as e:
                        logger.warning(f"      ⚠️ Ошибка AI: {e}")
                        # При ошибке — пропускаем тендер (лучше показать)
                        tender['ai_verified'] = False
                        tender['ai_error'] = str(e)
                        ai_filtered_matches.append(tender)

                matches = ai_filtered_matches
                logger.info(f"   🤖 AI результат: {len(ai_filtered_matches)} одобрено, {ai_rejected_count} отклонено")

            # Сортируем по скору
            matches.sort(key=lambda x: x['match_score'], reverse=True)

            high_score = len([m for m in matches if m['match_score'] >= 35])
            logger.info(f"   🎯 Всего тендеров: {len(matches)} (высокий score ≥35: {high_score})")

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
                    'medium_score_count': len([m for m in matches if 40 <= m['match_score'] < 70]),
                    # AI статистика
                    'ai_enabled': bool(use_ai_check and ai_intent) if 'ai_intent' in dir() else False,
                    'ai_verified_count': len([m for m in matches if m.get('ai_verified')]),
                    'ai_rejected_count': ai_rejected_count if 'ai_rejected_count' in locals() else 0
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
                total_count=len(tenders_for_report)
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
