"""
Улучшенный парсер zakupki.gov.ru с детальным извлечением данных.
"""

from typing import List, Dict, Any, Optional
import re
from bs4 import BeautifulSoup
import requests
import warnings
import os

try:
    from .zakupki_rss_parser import ZakupkiRSSParser
    from .smart_search_expander import TenderDataExtractor
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent.parent))
    from parsers.zakupki_rss_parser import ZakupkiRSSParser
    from parsers.smart_search_expander import TenderDataExtractor

# Отключаем предупреждения SSL
warnings.filterwarnings('ignore')
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except:
    pass


class ZakupkiEnhancedParser:
    """
    Улучшенный парсер с детальным извлечением данных о тендерах.
    """

    def __init__(self, llm_adapter=None):
        """
        Инициализация парсера.

        Args:
            llm_adapter: Адаптер LLM для интеллектуального извлечения данных
        """
        self.rss_parser = ZakupkiRSSParser()
        self.llm_adapter = llm_adapter
        self.data_extractor = TenderDataExtractor(llm_adapter) if llm_adapter else None

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })

        # Настройка прокси если указан в переменных окружения
        proxy_url = os.getenv('PROXY_URL', '').strip()
        if proxy_url:
            self.session.proxies = {
                'http': proxy_url,
                'https': proxy_url
            }
            print(f"🔐 Enhanced парсер использует прокси: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")

    def search_with_details(
        self,
        keywords: Optional[str] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
        max_results: int = 10,
        regions: Optional[List[str]] = None,
        extract_details: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Поиск тендеров с извлечением детальной информации.

        Args:
            keywords: Ключевые слова
            price_min: Минимальная цена
            price_max: Максимальная цена
            max_results: Максимум результатов
            regions: Список регионов для фильтрации
            extract_details: Извлекать ли детальную информацию через LLM

        Returns:
            Список тендеров с детальной информацией
        """
        print(f"\n🔍 Поиск тендеров с детальным анализом...")

        # Если указаны регионы, используем пост-фильтрацию
        # НО: если регионов слишком много (>10), отключаем фильтрацию для лучших результатов
        need_post_filtering = regions and len(regions) > 0 and len(regions) <= 10

        # Итеративный поиск для набора нужного количества тендеров
        enhanced_tenders = []
        seen_numbers = set()  # Для дедупликации

        # Начальный множитель для запросов (увеличиваем при фильтрации)
        multiplier = 10 if need_post_filtering else 1
        max_attempts = 5  # Максимум попыток (увеличили для надежности)
        attempt = 0

        while len(enhanced_tenders) < max_results and attempt < max_attempts:
            attempt += 1

            # Вычисляем, сколько еще тендеров нужно
            remaining = max_results - len(enhanced_tenders)
            rss_max_results = remaining * multiplier

            if attempt > 1:
                print(f"\n🔄 Попытка {attempt}/{max_attempts}: нужно еще {remaining} тендеров...")

            # Получаем базовые данные через RSS
            tenders = self.rss_parser.search_tenders_rss(
                keywords=keywords,
                price_min=price_min,
                price_max=price_max,
                max_results=rss_max_results,
                regions=regions
            )

            if not tenders:
                print(f"   ⚠️  RSS вернул 0 тендеров")
                break

            print(f"\n📊 Обработка {len(tenders)} тендеров...")

            # Обрабатываем каждый тендер
            batch_count = 0
            for i, tender in enumerate(tenders, 1):
                # Пропускаем дубликаты
                tender_number = tender.get('number', '')
                if tender_number and tender_number in seen_numbers:
                    continue

                if tender_number:
                    seen_numbers.add(tender_number)

                # Извлекаем данные из summary
                enhanced = self._extract_basic_info(tender)

                # Если есть LLM и нужна детальная информация
                if extract_details and self.data_extractor and tender.get('summary'):
                    llm_data = self.data_extractor.extract_tender_details(tender['summary'])
                    enhanced.update(llm_data)

                # Фильтруем по регионам если нужно
                if need_post_filtering:
                    tender_region = enhanced.get('region', '')
                    if not tender_region:
                        print(f"      ⚠️ Тендер {tender_number}: регион не извлечён, пропускаем")
                        continue

                    # Проверяем соответствие региону
                    region_match = False
                    for needed_region in regions:
                        if needed_region.lower() in tender_region.lower():
                            region_match = True
                            break

                    if not region_match:
                        print(f"      ⚠️ Тендер {tender_number}: регион '{tender_region}' не в списке нужных регионов")
                        continue
                    else:
                        print(f"      ✓ Тендер {tender_number}: регион '{tender_region}' подходит")

                # Добавляем тендер
                enhanced_tenders.append(enhanced)
                batch_count += 1

                # Достигли нужного количества - выходим
                if len(enhanced_tenders) >= max_results:
                    break

            print(f"   ✅ Добавлено {batch_count} тендеров (всего: {len(enhanced_tenders)}/{max_results})")

            # Если нашли достаточно - выходим
            if len(enhanced_tenders) >= max_results:
                break

            # Если в этой попытке ничего не добавилось - увеличиваем множитель
            if batch_count == 0:
                multiplier *= 2
                print(f"   📈 Увеличиваем множитель до {multiplier}")

        # Обрезаем до точного количества
        enhanced_tenders = enhanced_tenders[:max_results]

        if need_post_filtering:
            print(f"\n✅ Найдено {len(enhanced_tenders)} тендеров после фильтрации по {len(regions)} регионам")
        elif regions and len(regions) > 10:
            print(f"\n✅ Найдено {len(enhanced_tenders)} тендеров (фильтрация по {len(regions)} регионам отключена для лучших результатов)")
        else:
            print(f"\n✅ Найдено {len(enhanced_tenders)} тендеров")

        return enhanced_tenders

    def _extract_basic_info(self, tender: Dict[str, Any]) -> Dict[str, Any]:
        """
        Извлекает базовую информацию из тендера регулярными выражениями.

        Args:
            tender: Базовая информация о тендере

        Returns:
            Обогащенная информация
        """
        enhanced = tender.copy()
        summary = tender.get('summary', '')

        # Извлекаем цену (улучшенные паттерны)
        price_match = re.search(
            r'Начальная.*?цена.*?контракта:\s*</strong>\s*([0-9,.]+)',
            summary,
            re.IGNORECASE | re.DOTALL
        )
        if not price_match:
            # Альтернативный паттерн
            price_match = re.search(
                r'Начальная.*?цена.*?контракта.*?</strong>\s*([0-9\s,.]+)',
                summary,
                re.IGNORECASE | re.DOTALL
            )
        if price_match:
            price_text = price_match.group(1).strip()
            try:
                price = float(re.sub(r'[^\d.]', '', price_text.replace(',', '.')))
                enhanced['price'] = price
                enhanced['price_formatted'] = f"{price:,.2f} ₽"
            except:
                pass

        # Извлекаем заказчика
        customer_match = re.search(
            r'Наименование Заказчика:\s*</strong>([^<]+)',
            summary,
            re.IGNORECASE
        )
        if customer_match:
            enhanced['customer'] = customer_match.group(1).strip()

        # Извлекаем тип закупки
        type_match = re.search(
            r'<strong>(Электронный аукцион|Запрос котировок|Конкурс|Открытый конкурс)',
            summary,
            re.IGNORECASE
        )
        if type_match:
            enhanced['procedure_type'] = type_match.group(1)

        # Извлекаем закон (44-ФЗ или 223-ФЗ)
        law_match = re.search(
            r'(44-ФЗ|223-ФЗ)',
            summary
        )
        if law_match:
            enhanced['law'] = law_match.group(1)

        # Извлекаем этап
        stage_match = re.search(
            r'Этап.*?размещения:\s*</strong>([^<]+)',
            summary,
            re.IGNORECASE | re.DOTALL
        )
        if stage_match:
            enhanced['stage'] = stage_match.group(1).strip()

        # Извлекаем ИКЗ
        ikz_match = re.search(
            r'ИКЗ.*?</strong>\s*([0-9]+)',
            summary,
            re.IGNORECASE | re.DOTALL
        )
        if ikz_match:
            enhanced['ikz'] = ikz_match.group(1).strip()

        # Извлекаем дату размещения
        placement_match = re.search(
            r'Размещено:\s*</strong>\s*([0-9.]+)',
            summary
        )
        if placement_match:
            enhanced['placement_date'] = placement_match.group(1).strip()

        # Извлекаем дату обновления
        update_match = re.search(
            r'Обновлено:\s*</strong>\s*([0-9.]+)',
            summary
        )
        if update_match:
            enhanced['update_date'] = update_match.group(1).strip()

        # Извлекаем срок подачи заявок
        submission_deadline_match = re.search(
            r'(?:Окончание подачи заявок|Дата окончания подачи заявок|Срок подачи заявок).*?</strong>\\s*([0-9.]+ [0-9:]+)',
            summary,
            re.IGNORECASE | re.DOTALL
        )
        if submission_deadline_match:
            enhanced['submission_deadline'] = submission_deadline_match.group(1).strip()
        else:
            # Альтернативный паттерн
            submission_deadline_match = re.search(
                r'до\s+([0-9.]+\s+[0-9:]+).*?(?:МСК|UTC)',
                summary
            )
            if submission_deadline_match:
                enhanced['submission_deadline'] = submission_deadline_match.group(1).strip()

        # Извлекаем срок определения победителя
        winner_deadline_match = re.search(
            r'(?:Дата подведения итогов|Дата окончания|Подведение итогов).*?</strong>\\s*([0-9.]+ [0-9:]+)',
            summary,
            re.IGNORECASE | re.DOTALL
        )
        if winner_deadline_match:
            enhanced['winner_determination_date'] = winner_deadline_match.group(1).strip()

        # Извлекаем код ОКПД2
        okpd_matches = re.findall(
            r'ОКПД2?[:\s]+([0-9.]+)',
            summary,
            re.IGNORECASE
        )
        if okpd_matches:
            enhanced['okpd_codes'] = list(set(okpd_matches))

        # Определяем регион из названия заказчика
        customer_name = enhanced.get('customer', '')
        extracted_region = self._extract_region(customer_name)
        enhanced['region'] = extracted_region

        # Определяем тип заказчика
        enhanced['customer_type'] = self._determine_customer_type(
            enhanced.get('customer', '')
        )

        return enhanced

    def _extract_region(self, text: str) -> Optional[str]:
        """Извлекает регион из текста с учетом падежных окончаний."""
        # Полный список всех регионов России (все федеральные округа)
        regions = [
            # ЦФО
            'Москва', 'Московская область', 'Белгородская область',
            'Брянская область', 'Владимирская область', 'Воронежская область',
            'Ивановская область', 'Калужская область', 'Костромская область',
            'Курская область', 'Липецкая область', 'Орловская область',
            'Рязанская область', 'Смоленская область', 'Тамбовская область',
            'Тверская область', 'Тульская область', 'Ярославская область',
            # СЗФО
            'Санкт-Петербург', 'Ленинградская область', 'Республика Карелия',
            'Республика Коми', 'Архангельская область', 'Вологодская область',
            'Калининградская область', 'Мурманская область', 'Новгородская область',
            'Псковская область', 'Ненецкий автономный округ',
            # ЮФО
            'Республика Адыгея', 'Республика Калмыкия', 'Республика Крым',
            'Краснодарский край', 'Астраханская область', 'Волгоградская область',
            'Ростовская область', 'Севастополь',
            # СКФО
            'Республика Дагестан', 'Республика Ингушетия', 'Кабардино-Балкарская Республика',
            'Карачаево-Черкесская Республика', 'Республика Северная Осетия-Алания',
            'Чеченская Республика', 'Ставропольский край',
            # ПФО
            'Республика Башкортостан', 'Республика Марий Эл', 'Республика Мордовия',
            'Республика Татарстан', 'Удмуртская Республика', 'Чувашская Республика',
            'Пермский край', 'Кировская область', 'Нижегородская область',
            'Оренбургская область', 'Пензенская область', 'Самарская область',
            'Саратовская область', 'Ульяновская область',
            # УФО
            'Курганская область', 'Свердловская область', 'Тюменская область',
            'Челябинская область', 'Ханты-Мансийский автономный округ',
            'Ямало-Ненецкий автономный округ',
            # СФО
            'Республика Алтай', 'Республика Тыва', 'Республика Хакасия',
            'Алтайский край', 'Красноярский край', 'Иркутская область',
            'Кемеровская область', 'Новосибирская область', 'Омская область',
            'Томская область',
            # ДФО
            'Республика Бурятия', 'Республика Саха (Якутия)', 'Забайкальский край',
            'Камчатский край', 'Приморский край', 'Хабаровский край',
            'Амурская область', 'Магаданская область', 'Сахалинская область',
            'Еврейская автономная область', 'Чукотский автономный округ'
        ]

        text_lower = text.lower()

        # Проверяем города федерального значения с учетом падежных окончаний
        moscow_patterns = [
            r'\bмоскв[а-яеиюы]\b',  # Москва, Москве, Москвы, Москву
            r'\bгород[а-яеиюы]?\s+москв[а-яеиюы]\b',
            r'\bг\.\s*москв[а-яеиюы]\b'
        ]
        for pattern in moscow_patterns:
            if re.search(pattern, text_lower):
                return 'Москва'

        spb_patterns = [
            r'\bсанкт[\-\s]петербург[а-яеиюы]?\b',
            r'\bспб\b',
            r'\bгород[а-яеиюы]?\s+санкт[\-\s]петербург[а-яеиюы]?\b',
            r'\bг\.\s*санкт[\-\s]петербург[а-яеиюы]?\b'
        ]
        for pattern in spb_patterns:
            if re.search(pattern, text_lower):
                return 'Санкт-Петербург'

        # Проверяем каждый регион с учетом падежных окончаний
        for region in regions:
            # Для составных названий вроде "Московская область"
            if ' ' in region:
                # Извлекаем ключевое слово (первое)
                key_word = region.split()[0].lower()
                # Убираем последние 2-3 буквы для получения основы
                if len(key_word) > 4:
                    stem = key_word[:-2]  # Москов, Белгород, Брянск и т.д.
                    # Ищем основу + любое окончание
                    pattern = r'\b' + re.escape(stem) + r'[а-яеиюы]{1,3}\s+област[ьияюе]{1,2}\b'
                    if re.search(pattern, text_lower):
                        return region

            # Для простых названий (края, республики и т.д.)
            region_lower = region.lower()
            if region_lower in text_lower:
                return region

            # Для названий с падежными окончаниями (без "область"/"край")
            if len(region.split()) == 1 and len(region) > 4:
                stem = region[:-1].lower()  # Убираем последнюю букву
                pattern = r'\b' + re.escape(stem) + r'[а-яеиюы]\b'
                if re.search(pattern, text_lower):
                    return region

        # Ищем паттерн с полным названием
        region_match = re.search(
            r'([А-Яа-я\-]+\s+(?:област[ьияюе]{1,2}|кра[йяюе]{1,2})|Республика\s+[А-Яа-я\-]+|г\.\s+[А-Яа-я\-]+)',
            text,
            re.IGNORECASE
        )
        if region_match:
            extracted = region_match.group(1).strip()
            # Нормализуем извлеченный регион к списку
            extracted_lower = extracted.lower()
            for region in regions:
                if region.lower() == extracted_lower:
                    return region
            # Если не нашли точное совпадение, возвращаем как есть с capitalize
            return extracted.title()

        return None

    def _determine_customer_type(self, customer_name: str) -> Optional[str]:
        """Определяет тип заказчика по названию."""
        customer_lower = customer_name.lower()

        federal_keywords = [
            'федеральн', 'министерство', 'служба', 'агентств',
            'роспотребнадзор', 'росздравнадзор', 'фсб', 'мвд'
        ]

        regional_keywords = [
            'департамент', 'комитет', 'управление', 'администрация',
            'правительство области', 'правительство края'
        ]

        municipal_keywords = [
            'муниципальн', 'мо ', 'городской округ', 'муниципальное образование'
        ]

        for keyword in federal_keywords:
            if keyword in customer_lower:
                return 'Федеральный'

        for keyword in regional_keywords:
            if keyword in customer_lower:
                return 'Региональный'

        for keyword in municipal_keywords:
            if keyword in customer_lower:
                return 'Муниципальный'

        return 'Неопределен'

    def enrich_with_full_card(self, tender: Dict[str, Any]) -> Dict[str, Any]:
        """
        Обогащает данные тендера информацией из полной карточки.
        Извлекает сроки подачи заявок и определения победителя.

        Args:
            tender: Базовая информация о тендере из RSS

        Returns:
            Обогащенная информация
        """
        if not tender.get('url'):
            return tender

        full_url = f"https://zakupki.gov.ru{tender['url']}"

        try:
            print(f"   📡 Загрузка полной карточки...")
            response = self.session.get(full_url, timeout=10, verify=False)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Ищем блоки с информацией о сроках
            # Паттерн 1: Ищем все строки таблиц с датами
            all_text = soup.get_text()

            # Извлекаем срок подачи заявок
            submission_patterns = [
                r'Дата и время окончания срока подачи заявок.*?(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})',
                r'Окончание подачи заявок.*?(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})',
                r'Прием заявок до.*?(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})',
            ]

            for pattern in submission_patterns:
                match = re.search(pattern, all_text, re.DOTALL)
                if match:
                    tender['submission_deadline'] = match.group(1).strip()
                    print(f"   ⏰ Срок подачи: {tender['submission_deadline']}")
                    break

            # Извлекаем срок определения победителя
            winner_patterns = [
                r'Дата подведения итогов.*?(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})',
                r'Дата окончания.*?(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})',
                r'Подведение итогов.*?(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})',
            ]

            for pattern in winner_patterns:
                match = re.search(pattern, all_text, re.DOTALL)
                if match:
                    tender['winner_determination_date'] = match.group(1).strip()
                    print(f"   🏆 Определение победителя: {tender['winner_determination_date']}")
                    break

            # Дополнительно ищем информацию об оплате
            payment_patterns = [
                r'аванс.*?(\d+%)',
                r'предоплата.*?(\d+%)',
                r'условия оплаты[:\s]+(.*?)(?:\n|<)',
            ]

            for pattern in payment_patterns:
                match = re.search(pattern, all_text, re.IGNORECASE | re.DOTALL)
                if match:
                    tender['payment_terms'] = match.group(0).strip()[:200]
                    break

        except Exception as e:
            print(f"   ⚠️  Не удалось загрузить полную карточку: {e}")

        return tender


def main():
    """Пример использования улучшенного парсера."""
    print("\n" + "="*70)
    print("  ТЕСТ УЛУЧШЕННОГО ПАРСЕРА")
    print("="*70 + "\n")

    # Создаем парсер без LLM для быстрого теста
    parser = ZakupkiEnhancedParser()

    # Поиск с извлечением базовой информации
    tenders = parser.search_with_details(
        keywords="компьютерное оборудование",
        price_min=500000,
        price_max=5000000,
        max_results=3,
        extract_details=False  # Без LLM для скорости
    )

    print(f"\n📊 РЕЗУЛЬТАТЫ:\n")

    for i, tender in enumerate(tenders, 1):
        print(f"{'='*70}")
        print(f"ТЕНДЕР #{i}")
        print(f"{'='*70}")
        print(f"📋 Номер:          {tender.get('number', 'N/A')}")
        print(f"📝 Название:       {tender.get('name', 'N/A')[:60]}...")
        print(f"💰 Цена:           {tender.get('price_formatted', 'N/A')}")
        print(f"🏢 Заказчик:       {tender.get('customer', 'N/A')[:60]}")
        print(f"🏛️  Тип заказчика:  {tender.get('customer_type', 'N/A')}")
        print(f"📍 Регион:         {tender.get('region', 'N/A')}")
        print(f"📜 Закон:          {tender.get('law', 'N/A')}")
        print(f"🔖 Тип процедуры:  {tender.get('procedure_type', 'N/A')}")
        print(f"⏱️  Этап:           {tender.get('stage', 'N/A')}")
        print(f"📅 Размещено:      {tender.get('placement_date', 'N/A')}")

        if tender.get('okpd_codes'):
            print(f"🏷️  ОКПД2:          {', '.join(tender['okpd_codes'])}")

        print()

    print("="*70 + "\n")


if __name__ == "__main__":
    main()
