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

    # Стоп-слова - слишком общие термины, которые встречаются почти везде
    # Эти слова игнорируются при матчинге
    STOP_WORDS = {
        'закупка', 'закупки', 'закупок',
        'услуга', 'услуги', 'услуг',
        'поставка', 'поставки', 'поставок',
        'работа', 'работы', 'работ',
        'оказание', 'выполнение', 'обеспечение',
        'приобретение', 'покупка',
        'товар', 'товары', 'товаров',
        'для', 'нужд', 'целей',
        'служба', 'службы', 'служб',
        'система', 'системы', 'систем',
        'обслуживание', 'сопровождение',
    }

    # Словарь синонимов (можно расширять)
    # ВАЖНО: добавлены обратные синонимы для морфологических вариантов
    SYNONYMS = {
        'компьютер': ['ноутбук', 'пк', 'pc', 'ноутбуков', 'компьютеры', 'компьютерное', 'компьютерный'],
        'компьютеры': ['компьютер', 'ноутбук', 'пк', 'pc', 'компьютерное', 'компьютерный'],
        'ноутбук': ['компьютер', 'пк', 'pc', 'ноутбуки', 'ноутбуков', 'лэптоп'],
        'ноутбуки': ['ноутбук', 'компьютер', 'пк', 'лэптоп', 'ноутбуков'],
        'медицина': ['медицинские', 'здравоохранение', 'больница', 'поликлиника'],
        'канцелярия': ['канцтовары', 'офис', 'письменные принадлежности'],
        'мебель': ['столы', 'стулья', 'шкафы', 'офисная мебель'],
        'linux': ['линукс', 'убунту', 'ubuntu', 'debian', 'centos', 'redhat', 'astra linux', 'астра', 'альт линукс'],
        'аутентификация': ['авторизация', '2fa', 'mfa', 'двухфакторная', 'многофакторная', 'токен', 'смарт-карт'],
        'каталог': ['ldap', 'active directory', 'ad', 'домен', 'directory'],
        'сервер': ['серверное оборудование', 'серверная платформа', 'blade', 'серверы'],
        'серверы': ['сервер', 'серверное оборудование', 'серверная платформа'],
        'сеть': ['сетевое оборудование', 'коммутатор', 'маршрутизатор', 'switch', 'router'],
        'программное обеспечение': ['по', 'софт', 'software', 'лицензия', 'лицензии'],
        'оборудование': ['техника', 'устройства', 'аппаратура'],
    }

    # Составные фразы - технические термины из нескольких слов
    # Эти фразы матчатся как единое целое, а не по отдельным словам
    COMPOUND_PHRASES = {
        # IT термины
        'служба каталогов': ['directory service', 'ldap', 'active directory', 'ad ds'],
        'двухфакторная аутентификация': ['2fa', 'two-factor', 'мультифакторная'],
        'операционная система': ['ос', 'os', 'windows', 'linux'],
        'программное обеспечение': ['по', 'софт', 'software'],
        'антивирусная защита': ['антивирус', 'касперский', 'dr.web', 'eset'],
        'информационная безопасность': ['ибп', 'cybersecurity', 'защита информации'],
        'виртуализация серверов': ['vmware', 'hyper-v', 'proxmox', 'виртуальные машины'],
        'резервное копирование': ['бэкап', 'backup', 'архивирование'],
        'электронная подпись': ['эцп', 'эп', 'криптопро', 'цифровая подпись'],
        # Другие области
        'медицинское оборудование': ['медтехника', 'мед. оборудование'],
        'офисная мебель': ['рабочие места', 'столы офисные'],
    }

    # Негативные паттерны - если они найдены, тендер исключается
    # Эти паттерны указывают на нерелевантность для IT-тематики
    NEGATIVE_PATTERNS = {
        # Военная/силовая тематика (часто путается со "службой")
        'военная служба': True,
        'воинская служба': True,
        'контрактная служба': True,
        'служба по контракту': True,
        'призыв на службу': True,
        'привлечение граждан': True,
        'агитационные материалы': True,
        'мобилизация': True,
        'военкомат': True,
        # Медицинская тематика (путается с "системой")
        'медицинская помощь': True,
        'скорая помощь': True,
        'лечебное учреждение': True,
        # Строительная тематика
        'капитальный ремонт': True,
        'строительство здания': True,
        'реконструкция здания': True,
        # Продовольственная тематика
        'продукты питания': True,
        'пищевые продукты': True,
        'столовая': True,
    }

    # 🧪 БЕТА: Синонимы брендов (латиница ↔ кириллица)
    # Используются для матчинга тендеров с разными написаниями брендов
    BRAND_SYNONYMS = {
        # Компрессоры и пневматика
        'atlas copco': ['атлас копко', 'атлас-копко', 'atlascopco'],
        'атлас копко': ['atlas copco', 'atlascopco'],
        'ingersoll rand': ['ингерсолл рэнд', 'ingersoll'],
        'kaeser': ['кайзер'],

        # IT оборудование
        'cisco': ['циско', 'сиско'],
        'циско': ['cisco', 'сиско'],
        'hewlett packard': ['хьюлетт паккард', 'hp', 'хп'],
        'hp': ['hewlett packard', 'хьюлетт паккард', 'хп'],
        'dell': ['делл'],
        'lenovo': ['леново'],
        'ibm': ['ибм', 'айбиэм'],
        'apple': ['эпл', 'эппл'],
        'intel': ['интел'],
        'amd': ['амд'],

        # Промышленное оборудование
        'komatsu': ['комацу'],
        'комацу': ['komatsu'],
        'caterpillar': ['катерпиллер', 'катерпиллар', 'cat', 'кат'],
        'cat': ['caterpillar', 'катерпиллер'],
        'hitachi': ['хитачи'],
        'volvo': ['вольво'],

        # Электроинструмент
        'bosch': ['бош'],
        'бош': ['bosch'],
        'makita': ['макита'],
        'макита': ['makita'],
        'hilti': ['хилти'],
        'хилти': ['hilti'],
        'dewalt': ['деволт', 'девольт'],
        'metabo': ['метабо'],

        # Электротехника
        'siemens': ['сименс'],
        'сименс': ['siemens'],
        'schneider electric': ['шнейдер электрик', 'schneider'],
        'abb': ['абб'],
        'legrand': ['легранд'],

        # ПО и IT-компании
        'microsoft': ['майкрософт', 'ms'],
        'майкрософт': ['microsoft', 'ms'],
        'kaspersky': ['касперский', 'kaspersky lab'],
        'касперский': ['kaspersky'],
        'oracle': ['оракл'],
        'sap': ['сап'],
        'vmware': ['вмваре', 'vmvare'],
        '1c': ['1с', 'один эс'],
        '1с': ['1c', 'один эс'],

        # Насосы и климат
        'grundfos': ['грундфос'],
        'wilo': ['вило'],
        'danfoss': ['данфосс'],
        'daikin': ['дайкин'],

        # Медицинское оборудование
        'philips': ['филипс'],
        'ge healthcare': ['джи хелскеа', 'ge'],
        'mindray': ['миндрей'],

        # Автомобили и техника
        'mercedes': ['мерседес', 'mercedes-benz'],
        'volkswagen': ['фольксваген', 'vw'],
        'toyota': ['тойота'],
        'scania': ['скания'],
        'man': ['ман'],
    }

    # 🧪 БЕТА: Аббревиатуры (техническая терминология)
    # Используются для матчинга сокращений с полными названиями
    ABBREVIATIONS = {
        # IT системы
        'scada': ['скада', 'scada-система', 'ску'],
        'скада': ['scada', 'scada-система'],
        'erp': ['ерп', 'erp-система', 'система планирования ресурсов'],
        'crm': ['црм', 'crm-система', 'система управления клиентами'],
        'mes': ['мес', 'система управления производством'],

        # Сети и безопасность
        'vpn': ['впн', 'виртуальная частная сеть'],
        'впн': ['vpn'],
        'utm': ['ютм', 'unified threat management'],
        'ngfw': ['межсетевой экран нового поколения'],
        'ids': ['система обнаружения вторжений'],
        'ips': ['система предотвращения вторжений'],

        # Оборудование
        'ups': ['ибп', 'источник бесперебойного питания'],
        'ибп': ['ups', 'источник бесперебойного питания'],
        'pdu': ['пду', 'распределитель питания', 'блок розеток'],
        'kvm': ['квм', 'переключатель консоли'],
        'nas': ['нас', 'сетевое хранилище'],
        'san': ['сан', 'сеть хранения данных'],

        # Компьютерные компоненты
        'ssd': ['ссд', 'твердотельный накопитель', 'solid state'],
        'hdd': ['хдд', 'жёсткий диск', 'жесткий диск'],
        'cpu': ['цпу', 'процессор', 'центральный процессор'],
        'gpu': ['гпу', 'видеокарта', 'графический процессор'],
        'ram': ['озу', 'оперативная память', 'оперативка'],
        'озу': ['ram', 'оперативная память'],

        # Автоматизация
        'plc': ['плк', 'программируемый логический контроллер', 'plc-контроллер'],
        'плк': ['plc', 'программируемый логический контроллер'],
        'hmi': ['чми', 'человеко-машинный интерфейс', 'панель оператора'],
        'dcs': ['рсу', 'распределённая система управления'],

        # Связь
        'voip': ['воип', 'ip-телефония', 'интернет-телефония'],
        'pbx': ['атс', 'автоматическая телефонная станция'],
        'атс': ['pbx', 'телефонная станция'],

        # Прочее
        'cad': ['сапр', 'система автоматизированного проектирования'],
        'сапр': ['cad', 'autocad'],
        'bim': ['бим', 'информационная модель здания'],
        'gis': ['гис', 'геоинформационная система'],
        'гис': ['gis', 'геоинформационная'],
    }

    def __init__(self):
        """Инициализация matching engine."""
        self.stats = {
            'total_matches': 0,
            'high_score_matches': 0,  # score >= 70
            'medium_score_matches': 0,  # 50 <= score < 70
            'low_score_matches': 0,  # score < 50
        }

    def _is_stop_word(self, word: str) -> bool:
        """Проверяет, является ли слово стоп-словом."""
        return word.lower().strip() in self.STOP_WORDS

    def _extract_meaningful_keywords(self, text: str) -> List[str]:
        """
        Извлекает значимые ключевые слова из текста запроса.
        Удаляет стоп-слова и разбивает по запятым.
        """
        # Разбиваем по запятым
        parts = text.split(',')
        keywords = []

        for part in parts:
            # Разбиваем каждую часть на слова
            words = part.strip().split()
            meaningful_words = [w for w in words if not self._is_stop_word(w) and len(w) >= 3]
            if meaningful_words:
                # Добавляем как отдельные слова
                keywords.extend(meaningful_words)

        return keywords

    def _word_boundary_match(self, keyword: str, text: str) -> bool:
        """
        Проверяет совпадение слова с учетом границ слов.
        Избегает ложных срабатываний типа 'служб' в 'службы военной'.
        """
        keyword_lower = keyword.lower().strip()

        # Для коротких слов (< 4 символов) требуем точное совпадение с границами
        if len(keyword_lower) < 4:
            pattern = r'\b' + re.escape(keyword_lower) + r'\b'
            return bool(re.search(pattern, text, re.IGNORECASE))

        # Для более длинных слов - ищем начало слова
        # Это позволяет найти "linux" в "linux-система" или "линукс"
        pattern = r'\b' + re.escape(keyword_lower)
        return bool(re.search(pattern, text, re.IGNORECASE))

    def _check_negative_patterns(self, text: str) -> Optional[str]:
        """
        Проверяет текст на наличие негативных паттернов.

        Returns:
            Найденный паттерн или None если не найдено
        """
        text_lower = text.lower()
        for pattern in self.NEGATIVE_PATTERNS:
            if pattern in text_lower:
                return pattern
        return None

    def _match_compound_phrase(self, phrase: str, text: str) -> bool:
        """
        Проверяет совпадение составной фразы в тексте.
        Фраза должна встречаться целиком или через синонимы.
        """
        phrase_lower = phrase.lower().strip()
        text_lower = text.lower()

        # Прямое совпадение фразы целиком
        if phrase_lower in text_lower:
            return True

        # Проверяем синонимы составной фразы
        synonyms = self.COMPOUND_PHRASES.get(phrase_lower, [])
        for synonym in synonyms:
            if synonym.lower() in text_lower:
                return True

        return False

    def _extract_compound_phrases(self, keywords: List[str]) -> tuple:
        """
        Извлекает составные фразы из списка ключевых слов.

        Returns:
            (compound_phrases, remaining_keywords) - составные фразы и оставшиеся слова
        """
        compound_found = []
        remaining = []

        for keyword in keywords:
            keyword_lower = keyword.lower().strip()

            # Проверяем, является ли это составной фразой
            if keyword_lower in self.COMPOUND_PHRASES:
                compound_found.append(keyword)
            else:
                # Проверяем, содержит ли keyword составную фразу
                found_compound = False
                for phrase in self.COMPOUND_PHRASES:
                    if phrase in keyword_lower:
                        compound_found.append(phrase)
                        found_compound = True
                        # Извлекаем оставшиеся значимые слова
                        remaining_text = keyword_lower.replace(phrase, '').strip()
                        if remaining_text:
                            for word in remaining_text.split():
                                if len(word) >= 3 and not self._is_stop_word(word):
                                    remaining.append(word)
                        break

                if not found_compound:
                    remaining.append(keyword)

        return compound_found, remaining

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
        # Регион может быть в разных полях
        tender_region = (tender.get('region', '') or tender.get('customer_region', '') or '').lower()
        tender_type = tender.get('purchase_type', '') or tender.get('tender_type', '')
        tender_type = tender_type.lower()
        customer_name = tender.get('customer_name', '') or tender.get('customer', '')
        customer_name = customer_name.lower()

        # Объединяем текст для поиска (все доступные поля)
        searchable_text = f"{tender_name} {tender_description} {customer_name}"

        # ============================================
        # 1. ПРОВЕРКА ИСКЛЮЧАЮЩИХ ФИЛЬТРОВ
        # ============================================

        # 1.1 Проверка негативных паттернов (автоматическое исключение)
        negative_match = self._check_negative_patterns(searchable_text)
        if negative_match:
            logger.debug(f"   ⛔ Исключено по негативному паттерну: {negative_match}")
            return None

        # 1.2 Проверка пользовательских исключающих слов (с границами слов)
        if exclude_keywords:
            for keyword in exclude_keywords:
                # Используем проверку с границами слов для точности
                if self._word_boundary_match(keyword, searchable_text):
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

        # Проверка региона (не строгая - не отклоняем если регион не указан в тендере)
        # RSS уже фильтрует по региону, здесь только дополнительная проверка
        if regions and tender_region:
            region_match = False
            for region in regions:
                if region.lower() in tender_region:
                    region_match = True
                    break

            if not region_match:
                logger.debug(f"   ⛔ Регион не подходит: {tender_region}")
                # Не отклоняем полностью, т.к. RSS уже отфильтровал
                # return None

        # Проверка типа тендера (не строгая - не отклоняем если тип не указан)
        # RSS/клиентская фильтрация уже проверили тип
        if tender_types and tender_type:
            type_match = False
            for t_type in tender_types:
                if t_type.lower() in tender_type:
                    type_match = True
                    break

            if not type_match:
                logger.debug(f"   ⛔ Тип тендера не подходит: {tender_type}")
                # Не отклоняем полностью
                # return None

        # ============================================
        # 3. SCORING ПО КЛЮЧЕВЫМ СЛОВАМ
        # ============================================

        score = 0
        matched_keywords = []

        if keywords:
            # ШАГ 1: Извлекаем составные фразы и отдельные ключевые слова
            compound_phrases, remaining_keywords = self._extract_compound_phrases(keywords)

            # ШАГ 2: Фильтруем стоп-слова из оставшихся ключевых слов
            meaningful_keywords = []
            for keyword in remaining_keywords:
                keyword_lower = keyword.lower().strip()
                if not keyword_lower:
                    continue
                # Пропускаем стоп-слова
                if self._is_stop_word(keyword_lower):
                    logger.debug(f"   ⏭️ Пропускаем стоп-слово: {keyword}")
                    continue
                meaningful_keywords.append(keyword)

            # ШАГ 3: Если после фильтрации не осталось значимых слов - пробуем извлечь из фраз
            if not meaningful_keywords and not compound_phrases:
                for keyword in keywords:
                    extracted = self._extract_meaningful_keywords(keyword)
                    meaningful_keywords.extend(extracted)

            # Общее количество критериев для процентного скоринга
            total_criteria = len(compound_phrases) + len(meaningful_keywords)
            if total_criteria == 0:
                logger.debug(f"   ⛔ Нет значимых критериев после фильтрации")
                return None

            logger.debug(f"   📝 Составные фразы: {compound_phrases}")
            logger.debug(f"   📝 Значимые слова: {meaningful_keywords}")

            # ШАГ 4: Матчинг составных фраз (высший приоритет)
            for phrase in compound_phrases:
                if self._match_compound_phrase(phrase, searchable_text):
                    score += 35  # Высокий бонус за составную фразу
                    matched_keywords.append(f"📌 {phrase}")
                    logger.debug(f"   ✅ Найдена составная фраза: {phrase}")

            # ШАГ 5: Матчинг отдельных ключевых слов
            for keyword in meaningful_keywords:
                keyword_lower = keyword.lower().strip()

                # Пропускаем пустые и стоп-слова
                if not keyword_lower or self._is_stop_word(keyword_lower):
                    continue

                # Прямое вхождение с учетом границ слов
                if self._word_boundary_match(keyword_lower, searchable_text):
                    score += 25  # Бонус за точное совпадение
                    matched_keywords.append(keyword)
                    logger.debug(f"   ✅ Найдено ключевое слово: {keyword}")
                    continue

                # Частичное совпадение (корень слова, минимум 5 символов для точности)
                if len(keyword_lower) >= 5:
                    root = keyword_lower[:max(5, len(keyword_lower) - 2)]
                    if self._word_boundary_match(root, searchable_text):
                        score += 18
                        matched_keywords.append(f"{keyword} (частичное)")
                        logger.debug(f"   ✅ Частичное совпадение: {root}* → {keyword}")
                        continue

                # Поиск синонимов
                synonyms = self.SYNONYMS.get(keyword_lower, [])
                synonym_found = False
                for synonym in synonyms:
                    if self._word_boundary_match(synonym.lower(), searchable_text):
                        score += 20
                        matched_keywords.append(f"{keyword} (синоним: {synonym})")
                        logger.debug(f"   ✅ Найден синоним: {synonym} → {keyword}")
                        synonym_found = True
                        break

                if synonym_found:
                    continue

                # 🧪 БЕТА: Поиск по брендам (латиница ↔ кириллица)
                brand_synonyms = self.BRAND_SYNONYMS.get(keyword_lower, [])
                for brand_syn in brand_synonyms:
                    if self._word_boundary_match(brand_syn.lower(), searchable_text):
                        score += 22  # Чуть выше чем обычные синонимы - бренды важны
                        matched_keywords.append(f"{keyword} (бренд: {brand_syn})")
                        logger.debug(f"   ✅ 🧪 Найден бренд: {brand_syn} → {keyword}")
                        synonym_found = True
                        break

                if synonym_found:
                    continue

                # 🧪 БЕТА: Поиск по аббревиатурам (техническая терминология)
                abbrev_synonyms = self.ABBREVIATIONS.get(keyword_lower, [])
                for abbrev_syn in abbrev_synonyms:
                    if self._word_boundary_match(abbrev_syn.lower(), searchable_text):
                        score += 22  # Аббревиатуры тоже важны
                        matched_keywords.append(f"{keyword} (аббр: {abbrev_syn})")
                        logger.debug(f"   ✅ 🧪 Найдена аббревиатура: {abbrev_syn} → {keyword}")
                        break

            # ШАГ 6: Проверка на минимум совпадений
            if not matched_keywords:
                logger.debug(f"   ⛔ Нет совпадений по значимым критериям")
                return None

            # ШАГ 7: Бонус/штраф за процент совпадений
            # Если совпало меньше 30% критериев - снижаем скор
            match_ratio = len(matched_keywords) / total_criteria
            if match_ratio < 0.3 and total_criteria >= 3:
                # Штраф за низкий процент совпадений
                penalty = int(score * 0.3)
                score -= penalty
                logger.debug(f"   ⚠️ Штраф за низкий % совпадений ({match_ratio:.0%}): -{penalty}")
            elif match_ratio >= 0.7:
                # Бонус за высокий процент совпадений
                bonus = int(score * 0.2)
                score += bonus
                logger.debug(f"   ✨ Бонус за высокий % совпадений ({match_ratio:.0%}): +{bonus}")

        else:
            # Если фильтр без ключевых слов - возвращаем None (фильтр некорректный)
            logger.debug(f"   ⛔ Фильтр без ключевых слов")
            return None

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
        elif score >= 50:
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
        min_score: int = 50
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
        min_score: int = 50
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
