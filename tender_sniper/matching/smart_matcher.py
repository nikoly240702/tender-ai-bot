"""
Smart Matching Engine для сопоставления тендеров с пользовательскими фильтрами.

Использует scoring алгоритм для ранжирования тендеров по релевантности.
"""

import re
import json
from typing import List, Dict, Any, Optional, Set
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class SmartMatcher:
    """
    Smart Matching Engine для тендеров.

    Особенности:
    - Fuzzy matching по ключевым словам
    - Учет синонимов и морфологии
    - Scoring система (0-100)
    - Поддержка исключающих фильтров
    - Географическая фильтрация
    """

    # Словарь синонимов (можно расширять)
    SYNONYMS = {
        'компьютер': ['ноутбук', 'пк', 'pc', 'ноутбуков', 'компьютеры'],
        'медицина': ['медицинские', 'здравоохранение', 'больница', 'поликлиника'],
        'канцелярия': ['канцтовары', 'офис', 'письменные принадлежности'],
        'мебель': ['столы', 'стулья', 'шкафы', 'офисная мебель'],
    }

    def __init__(self):
        """Инициализация matching engine."""
        self.stats = {
            'total_matches': 0,
            'high_score_matches': 0,  # score >= 70
            'medium_score_matches': 0,  # 40 <= score < 70
            'low_score_matches': 0,  # score < 40
        }

    def match_tender(
        self,
        tender: Dict[str, Any],
        filter_config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Проверка, соответствует ли тендер фильтру.

        Args:
            tender: Данные тендера
            filter_config: Конфигурация фильтра пользователя

        Returns:
            Результат матчинга со score или None если не подходит
        """
        # Извлекаем параметры фильтра
        keywords = self._parse_json_field(filter_config.get('keywords', '[]'))
        exclude_keywords = self._parse_json_field(filter_config.get('exclude_keywords', '[]'))
        price_min = filter_config.get('price_min')
        price_max = filter_config.get('price_max')
        regions = self._parse_json_field(filter_config.get('regions', '[]'))
        customer_types = self._parse_json_field(filter_config.get('customer_types', '[]'))
        tender_types = self._parse_json_field(filter_config.get('tender_types', '[]'))

        # Извлекаем данные тендера
        # Поддерживаем разные источники данных (RSS и HTML парсеры)
        tender_name = tender.get('name', '').lower()
        tender_description = tender.get('description', '') or tender.get('summary', '')
        tender_description = tender_description.lower()
        tender_price = tender.get('price')
        tender_region = tender.get('region', '').lower()
        tender_type = tender.get('purchase_type', '') or tender.get('tender_type', '')
        tender_type = tender_type.lower()
        customer_name = tender.get('customer_name', '') or tender.get('customer', '')
        customer_name = customer_name.lower()

        # Объединяем текст для поиска (все доступные поля)
        searchable_text = f"{tender_name} {tender_description} {customer_name}"

        # ============================================
        # 1. ПРОВЕРКА ИСКЛЮЧАЮЩИХ ФИЛЬТРОВ
        # ============================================

        if exclude_keywords:
            for keyword in exclude_keywords:
                if keyword.lower() in searchable_text:
                    logger.debug(f"   ⛔ Исключено по ключевому слову: {keyword}")
                    return None

        # ============================================
        # 2. ПРОВЕРКА ОБЯЗАТЕЛЬНЫХ УСЛОВИЙ
        # ============================================

        # Проверка цены
        if price_min is not None and tender_price is not None:
            if tender_price < price_min:
                logger.debug(f"   ⛔ Цена слишком низкая: {tender_price} < {price_min}")
                return None

        if price_max is not None and tender_price is not None:
            if tender_price > price_max:
                logger.debug(f"   ⛔ Цена слишком высокая: {tender_price} > {price_max}")
                return None

        # Проверка региона
        if regions:
            region_match = False
            for region in regions:
                if region.lower() in tender_region:
                    region_match = True
                    break

            if not region_match:
                logger.debug(f"   ⛔ Регион не подходит: {tender_region}")
                return None

        # Проверка типа тендера
        if tender_types:
            type_match = False
            for t_type in tender_types:
                if t_type.lower() in tender_type:
                    type_match = True
                    break

            if not type_match:
                logger.debug(f"   ⛔ Тип тендера не подходит: {tender_type}")
                return None

        # ============================================
        # 3. SCORING ПО КЛЮЧЕВЫМ СЛОВАМ
        # ============================================

        score = 0
        matched_keywords = []

        if keywords:
            # Базовый поиск по ключевым словам
            for keyword in keywords:
                keyword_lower = keyword.lower().strip()

                # Пропускаем пустые ключевые слова
                if not keyword_lower:
                    continue

                # Прямое вхождение (точное)
                if keyword_lower in searchable_text:
                    score += 20
                    matched_keywords.append(keyword)
                    logger.debug(f"   ✅ Найдено ключевое слово: {keyword}")
                    continue

                # Частичное совпадение (корень слова, минимум 4 символа)
                if len(keyword_lower) >= 4:
                    # Берем корень слова (первые 4+ символов)
                    root = keyword_lower[:max(4, len(keyword_lower) - 2)]
                    if root in searchable_text:
                        score += 15
                        matched_keywords.append(f"{keyword} (частичное)")
                        logger.debug(f"   ✅ Частичное совпадение: {root}* → {keyword}")
                        continue

                # Поиск синонимов
                synonyms = self.SYNONYMS.get(keyword_lower, [])
                for synonym in synonyms:
                    if synonym.lower() in searchable_text:
                        score += 15
                        matched_keywords.append(f"{keyword} (синоним: {synonym})")
                        logger.debug(f"   ✅ Найден синоним: {synonym} → {keyword}")
                        break

            # Если ни одно ключевое слово не найдено - всё равно включаем с минимальным скором
            # т.к. тендер был найден RSS поиском по этим же ключевым словам
            if not matched_keywords:
                # Даём базовый скор, т.к. RSS уже отфильтровал по ключевым словам
                score = 30
                matched_keywords.append("Найден по поисковому запросу")
                logger.debug(f"   ℹ️ Базовый скор за совпадение с RSS поиском")

        else:
            # Если фильтр без ключевых слов - базовый score
            score = 50

        # ============================================
        # 4. БОНУСЫ ЗА ДОПОЛНИТЕЛЬНЫЕ КРИТЕРИИ
        # ============================================

        # Бонус за соответствие цене (чем ближе к середине диапазона, тем лучше)
        if price_min and price_max and tender_price:
            price_middle = (price_min + price_max) / 2
            price_deviation = abs(tender_price - price_middle) / (price_max - price_min)
            price_bonus = int((1 - price_deviation) * 20)
            score += price_bonus

        # Бонус за недавнюю публикацию
        published_date = tender.get('published_datetime')
        if published_date:
            try:
                if isinstance(published_date, str):
                    pub_dt = datetime.fromisoformat(published_date.replace('Z', '+00:00'))
                else:
                    pub_dt = published_date

                days_old = (datetime.now(pub_dt.tzinfo) - pub_dt).days
                if days_old == 0:
                    score += 10  # Опубликован сегодня
                elif days_old <= 3:
                    score += 5  # Опубликован недавно
            except:
                pass

        # ============================================
        # 5. НОРМАЛИЗАЦИЯ SCORE (0-100)
        # ============================================

        score = min(100, max(0, score))

        # Обновляем статистику
        self.stats['total_matches'] += 1
        if score >= 70:
            self.stats['high_score_matches'] += 1
        elif score >= 40:
            self.stats['medium_score_matches'] += 1
        else:
            self.stats['low_score_matches'] += 1

        logger.info(f"   ✅ MATCH! Score: {score}/100 | Фильтр: {filter_config.get('name', 'N/A')}")

        return {
            'filter_id': filter_config.get('id'),
            'filter_name': filter_config.get('name'),
            'score': score,
            'matched_keywords': matched_keywords,
            'matched_at': datetime.now().isoformat(),
            'tender_number': tender.get('number'),
            'tender_name': tender.get('name'),
            'tender_price': tender_price,
            'tender_url': tender.get('url')
        }

    def match_against_filters(
        self,
        tender: Dict[str, Any],
        filters: List[Dict[str, Any]],
        min_score: int = 40
    ) -> List[Dict[str, Any]]:
        """
        Проверка тендера против списка фильтров.

        Args:
            tender: Данные тендера
            filters: Список фильтров пользователей
            min_score: Минимальный score для включения в результаты

        Returns:
            Список совпадений (отсортирован по score)
        """
        matches = []

        tender_number = tender.get('number', 'N/A')
        logger.debug(f"\n🔍 Проверка тендера {tender_number} против {len(filters)} фильтров...")

        for filter_config in filters:
            match_result = self.match_tender(tender, filter_config)

            if match_result and match_result['score'] >= min_score:
                matches.append(match_result)

        # Сортируем по score (от большего к меньшему)
        matches.sort(key=lambda x: x['score'], reverse=True)

        if matches:
            logger.info(f"   ✅ Найдено совпадений: {len(matches)} (лучший score: {matches[0]['score']})")
        else:
            logger.debug(f"   ℹ️  Совпадений не найдено")

        return matches

    def batch_match(
        self,
        tenders: List[Dict[str, Any]],
        filters: List[Dict[str, Any]],
        min_score: int = 40
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Пакетная обработка тендеров против фильтров.

        Args:
            tenders: Список тендеров
            filters: Список фильтров
            min_score: Минимальный score

        Returns:
            Словарь {tender_number: [matches]}
        """
        logger.info(f"\n🔄 Пакетная обработка: {len(tenders)} тендеров x {len(filters)} фильтров")

        results = {}

        for tender in tenders:
            tender_number = tender.get('number')
            matches = self.match_against_filters(tender, filters, min_score)

            if matches:
                results[tender_number] = matches

        logger.info(f"✅ Обработано: {len(results)} тендеров с совпадениями из {len(tenders)}")

        return results

    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики matching."""
        return self.stats.copy()

    @staticmethod
    def _parse_json_field(field_value: Any) -> List[str]:
        """Парсинг JSON поля из базы данных."""
        if isinstance(field_value, list):
            return field_value
        if isinstance(field_value, str):
            try:
                return json.loads(field_value)
            except:
                return []
        return []


# ============================================
# ПРИМЕР ИСПОЛЬЗОВАНИЯ
# ============================================

def example_usage():
    """Пример использования Smart Matcher."""
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Создаем matcher
    matcher = SmartMatcher()

    # Пример тендера
    tender = {
        'number': '0123456789',
        'name': 'Поставка компьютерного оборудования',
        'description': 'Поставка ноутбуков и персональных компьютеров для офиса',
        'price': 2500000,
        'region': 'Москва',
        'purchase_type': 'товары',
        'customer_name': 'ООО "Тестовая компания"',
        'published_datetime': datetime.now().isoformat()
    }

    # Пример фильтра (как из базы данных)
    filter_config = {
        'id': 1,
        'name': 'IT оборудование',
        'keywords': json.dumps(['компьютер', 'ноутбук'], ensure_ascii=False),
        'exclude_keywords': json.dumps(['б/у', 'ремонт'], ensure_ascii=False),
        'price_min': 1000000,
        'price_max': 5000000,
        'regions': json.dumps(['Москва', 'Московская область'], ensure_ascii=False),
        'tender_types': json.dumps(['товары'], ensure_ascii=False)
    }

    # Проверяем совпадение
    match_result = matcher.match_tender(tender, filter_config)

    if match_result:
        print(f"\n✅ СОВПАДЕНИЕ!")
        print(f"Score: {match_result['score']}/100")
        print(f"Matched keywords: {', '.join(match_result['matched_keywords'])}")
    else:
        print(f"\n❌ Тендер не подходит под фильтр")

    # Статистика
    print(f"\nСтатистика matcher:")
    print(json.dumps(matcher.get_stats(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    example_usage()
