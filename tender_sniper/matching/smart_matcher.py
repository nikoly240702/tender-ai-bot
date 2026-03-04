"""
Smart Matching Engine для сопоставления тендеров с пользовательскими фильтрами.

Использует scoring алгоритм для ранжирования тендеров по релевантности.
"""

import re
import json
import functools
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1024)
def _compile_pattern(pattern: str, flags: int = 0) -> re.Pattern:
    """Кэширует скомпилированные regex паттерны."""
    return re.compile(pattern, flags)


def detect_red_flags(tender: Dict[str, Any]) -> List[str]:
    """
    Детектирует потенциальные проблемы (красные флаги) в тендере.

    Args:
        tender: Данные тендера

    Returns:
        Список строк с описанием обнаруженных проблем
    """
    flags = []

    # 1. Проверка на короткий срок подачи заявки
    deadline = tender.get('submission_deadline') or tender.get('deadline')
    if deadline:
        try:
            if isinstance(deadline, str):
                # Пробуем разные форматы даты
                for fmt in ['%Y-%m-%d', '%d.%m.%Y', '%Y-%m-%dT%H:%M:%S', '%d.%m.%Y %H:%M']:
                    try:
                        deadline_dt = datetime.strptime(deadline.split('+')[0].split('Z')[0], fmt)
                        break
                    except ValueError:
                        continue
                else:
                    deadline_dt = None
            else:
                deadline_dt = deadline

            if deadline_dt:
                days_left = (deadline_dt - datetime.now()).days
                if days_left < 0:
                    flags.append("⛔ Срок подачи истёк")
                elif days_left <= 3:
                    flags.append("🔴 Срок подачи менее 3 дней")
                elif days_left <= 5:
                    flags.append("⚠️ Срок подачи менее 5 дней")
        except Exception:
            pass

    # 2. Проверка на специальные лицензии в тексте
    text = (tender.get('name', '') + ' ' + (tender.get('description', '') or '')).lower()

    # Лицензии ФСБ/ФСТЭК (гостайна, криптография)
    fsb_patterns = ['лицензия фсб', 'лицензии фсб', 'фсб россии', 'гостайна', 'государственная тайна',
                    'секретно', 'совершенно секретно', 'особой важности']
    for pattern in fsb_patterns:
        if pattern in text:
            flags.append("🔒 Требуется лицензия ФСБ (гостайна)")
            break

    fstec_patterns = ['лицензия фстэк', 'лицензии фстэк', 'фстэк россии', 'защита информации',
                      'средства защиты информации', 'сзи']
    for pattern in fstec_patterns:
        if pattern in text:
            flags.append("🔒 Требуется лицензия ФСТЭК")
            break

    # 3. Проверка на подозрительно низкую цену
    price = tender.get('price')
    if price and price > 0:
        # Если цена меньше 100 000 рублей для тендера - подозрительно низкая
        if price < 100000:
            flags.append("💰 Очень низкая начальная цена")

    # 4. Проверка на обеспечение заявки/контракта
    if 'обеспечение заявки' in text or 'обеспечение исполнения' in text:
        # Ищем большие проценты обеспечения (>10%)
        percent_matches = re.findall(r'(\d+)\s*%\s*(?:от\s+)?(?:нмцк|цены|контракта|обеспечен)', text)
        for match in percent_matches:
            if int(match) > 10:
                flags.append(f"💳 Высокое обеспечение ({match}%)")
                break

    # 5. Проверка на единственного поставщика
    if 'единственн' in text and ('поставщик' in text or 'исполнител' in text or 'подрядчик' in text):
        flags.append("👤 Закупка у единственного поставщика")

    # 6. Проверка на срочную закупку
    urgent_patterns = ['срочная закупка', 'срочный заказ', 'экстренн', 'безотлагательн', 'неотложн']
    for pattern in urgent_patterns:
        if pattern in text:
            flags.append("⏰ Срочная/экстренная закупка")
            break

    # 7. Проверка на специфические требования
    specific_patterns = [
        ('членство в сро', '📜 Требуется членство в СРО'),
        ('опыт не менее 3 лет', '📋 Требуется опыт от 3 лет'),
        ('опыт не менее 5 лет', '📋 Требуется опыт от 5 лет'),
        ('квалифицированный персонал', '👥 Требования к квалификации персонала'),
        ('аккредитация', '📜 Требуется аккредитация'),
    ]
    for pattern, flag_text in specific_patterns:
        if pattern in text:
            flags.append(flag_text)

    # 8. Требование конкретной марки/бренда (ограничение конкуренции)
    brand_patterns = [
        r'(?:товарный знак|торговая марка|конкретн\w+ производител)',
        r'эквивалент\w* не допускается',
    ]
    for bp in brand_patterns:
        if re.search(bp, text):
            flags.append("🏷️ Требование конкретной марки/бренда")
            break

    # 9. Аукцион с ограниченным участием
    if 'ограниченн' in text and ('участи' in text or 'аукцион' in text):
        flags.append("🚫 Ограниченное участие")

    # 10. Запрет субподряда
    if ('субподряд' in text or 'субподрядчик' in text) and ('запрещ' in text or 'не допускается' in text):
        flags.append("🔗 Субподряд запрещён")

    # 11. Очень маленький срок поставки (если указан в тексте)
    delivery_match = re.search(r'(\d+)\s*(?:рабочих|календарных)?\s*дней?\s*(?:с момента|после|от даты)', text)
    if delivery_match:
        delivery_days = int(delivery_match.group(1))
        if delivery_days <= 5:
            flags.append(f"⚡ Очень короткий срок поставки ({delivery_days} дн.)")

    return flags


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

    # Короткие ключевые слова (< 3 символов), которые НЕ должны отбрасываться
    # Матчатся ТОЛЬКО по точному совпадению (word boundary) для избежания ложных срабатываний
    SHORT_KEYWORDS_WHITELIST = {
        'по', 'it', 'ит', 'ибп', 'ас', 'бд', 'ос', 'пк', 'схд', 'мфу', 'эвм', 'си',
        '1с', 'ук', 'тп', 'км', 'уз', 'кт', 'мр', 'мрт', 'ктп', 'рф', 'суг',
    }

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
    # Эти паттерны указывают на нерелевантность
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
        # Медицинская тематика (путается с "системой" и "транспортировкой")
        'медицинская помощь': True,
        'скорая помощь': True,
        'лечебное учреждение': True,
        'медицинских отходов': True,
        'биологических образцов': True,
        'лекарственных препаратов': True,
        # Строительная тематика
        'капитальный ремонт': True,
        'строительство здания': True,
        'реконструкция здания': True,
        # Продовольственная тематика
        'продукты питания': True,
        'пищевые продукты': True,
        'столовая': True,
        # Рекламная/маркетинговая тематика (ложные срабатывания для IT)
        'поставка баннеров': True,
        'рекламные материалы': True,
        'рекламная продукция': True,
        'наружная реклама': True,
        'полиграфическая продукция': True,
        'печатная продукция': True,
        # Телеметрия/мониторинг не-IT (путается с IT-мониторингом)
        'телеметрическая система': True,
        'телеметрия транспорта': True,
        'экспертный совет': True,
        # Канцелярия и бытовые товары
        'канцелярские товары': True,
        'хозяйственные товары': True,
        'уборка помещений': True,
        'клининг': True,
        # Животные и ветеринария (путается с "транспортировкой")
        'отлов собак': True,
        'отлов животных': True,
        'безнадзорных животных': True,
        'бездомных животных': True,
        'ветеринарн': True,
        # Мероприятия и event (путается с "логистикой" в названии заказчика)
        'визуальному сопровождению мероприятий': True,
        'организации и проведению мероприятия': True,
        'проведение мероприятия': True,
        'event-услуги': True,
        # Охранные услуги
        'охранных услуг': True,
        'охранные услуги': True,
        'пультовая охрана': True,
        # Жидкие бытовые отходы
        'жидких бытовых отходов': True,
        'жбо': True,
    }

    # Минимальные требования для качественного матчинга
    MIN_KEYWORDS_FOR_STRICT_MODE = 8  # При 8+ keywords включаем строгий режим
    MIN_MATCH_RATIO_STRICT = 0.10  # Минимум 10% совпадений в строгом режиме
    MIN_MATCHES_ABSOLUTE = 2  # Минимум 2 совпадения для строгого режима

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

        # Аббревиатура "ПО" — программное обеспечение
        # (используется в пользовательских фразах типа "разработка ПО")
        'по': ['программное обеспечение', 'программного обеспечения', 'программный продукт'],
    }

    # Федеральные округа для раскрытия
    FEDERAL_DISTRICTS = {
        "центральный": [
            "москва", "московская область", "белгородская область", "брянская область",
            "владимирская область", "воронежская область", "ивановская область",
            "калужская область", "костромская область", "курская область",
            "липецкая область", "орловская область", "рязанская область",
            "смоленская область", "тамбовская область", "тверская область",
            "тульская область", "ярославская область"
        ],
        "северо-западный": [
            "санкт-петербург", "ленинградская область", "архангельская область",
            "вологодская область", "калининградская область", "республика карелия",
            "республика коми", "мурманская область", "ненецкий автономный округ",
            "новгородская область", "псковская область"
        ],
        "южный": [
            "ростовская область", "астраханская область", "волгоградская область",
            "республика адыгея", "республика калмыкия", "краснодарский край",
            "республика крым", "крым", "севастополь"
        ],
        "северо-кавказский": [
            "ставропольский край", "республика дагестан", "республика ингушетия",
            "кабардино-балкарская республика", "карачаево-черкесская республика",
            "республика северная осетия", "чеченская республика"
        ],
        "приволжский": [
            "нижегородская область", "кировская область", "самарская область",
            "оренбургская область", "пензенская область", "пермский край",
            "саратовская область", "ульяновская область", "республика башкортостан",
            "республика марий эл", "республика мордовия", "республика татарстан",
            "удмуртская республика", "чувашская республика"
        ],
        "уральский": [
            "свердловская область", "челябинская область", "курганская область",
            "тюменская область", "ханты-мансийский", "югра", "ямало-ненецкий"
        ],
        "сибирский": [
            "новосибирская область", "алтайский край", "республика алтай",
            "иркутская область", "кемеровская область", "красноярский край",
            "омская область", "томская область", "республика тыва", "республика хакасия"
        ],
        "дальневосточный": [
            "хабаровский край", "приморский край", "амурская область",
            "камчатский край", "магаданская область", "сахалинская область",
            "республика саха", "якутия", "еврейская автономная область",
            "чукотский автономный округ", "республика бурятия", "забайкальский край"
        ]
    }

    # Алиасы регионов (сокращения и альтернативные названия)
    REGION_ALIASES = {
        # Города федерального значения
        "спб": ["санкт-петербург", "питер", "ленинград"],
        "санкт-петербург": ["спб", "питер", "ленинград", "с.-петербург", "с-петербург"],
        "мск": ["москва"],
        "москва": ["мск"],
        # Автономные округа (сокращения)
        "янао": ["ямало-ненецкий", "ямал", "ямало-ненецкий автономный округ"],
        "ямало-ненецкий": ["янао", "ямал"],
        "хмао": ["ханты-мансийский", "югра", "ханты-мансийский автономный округ"],
        "ханты-мансийский": ["хмао", "югра"],
        "югра": ["хмао", "ханты-мансийский"],
        "нао": ["ненецкий", "ненецкий автономный округ"],
        "ненецкий": ["нао"],
        "чао": ["чукотский", "чукотка", "чукотский автономный округ"],
        "чукотский": ["чао", "чукотка"],
        # Республики
        "татарстан": ["республика татарстан", "рт"],
        "башкортостан": ["республика башкортостан", "башкирия"],
        "дагестан": ["республика дагестан"],
        "якутия": ["республика саха", "саха"],
        "саха": ["якутия", "республика саха"],
        "крым": ["республика крым"],
        # Края
        "красноярск": ["красноярский край"],
        "красноярский": ["красноярск", "красноярский край"],
        "краснодар": ["краснодарский край", "кубань"],
        "краснодарский": ["краснодар", "кубань"],
        # Области (города)
        "екатеринбург": ["свердловская область", "свердловская"],
        "свердловская": ["екатеринбург"],
        "новосибирск": ["новосибирская область", "новосибирская"],
        "новосибирская": ["новосибирск"],
        "нижний новгород": ["нижегородская область", "нижегородская"],
        "нижегородская": ["нижний новгород"],
        "тюмень": ["тюменская область", "тюменская"],
        "тюменская": ["тюмень"],
    }

    def __init__(self):
        """Инициализация matching engine."""
        self.stats = {
            'total_matches': 0,
            'high_score_matches': 0,  # score >= 70
            'medium_score_matches': 0,  # 50 <= score < 70
            'low_score_matches': 0,  # score < 50
        }

    def _expand_federal_districts(self, regions: List[str]) -> List[str]:
        """
        Раскрывает федеральные округа в список отдельных регионов.

        Например: "Центральный федеральный округ" → ["Москва", "Московская область", ...]
        """
        expanded = []
        for region in regions:
            region_lower = region.lower().strip()

            # Проверяем, является ли это федеральным округом
            found_district = False
            for district_key, district_regions in self.FEDERAL_DISTRICTS.items():
                # Проверяем разные варианты написания ФО
                if (district_key in region_lower or
                    f"{district_key} федеральный" in region_lower or
                    f"{district_key} фо" in region_lower):
                    expanded.extend(district_regions)
                    found_district = True
                    logger.debug(f"   📍 Раскрыт ФО '{region}' → {len(district_regions)} регионов")
                    break

            if not found_district:
                # Это обычный регион, добавляем как есть
                expanded.append(region_lower)

        return expanded

    def _normalize_region(self, region: str) -> str:
        """Нормализует название региона, убирая лишние слова."""
        region = region.lower().strip()
        # Убираем типичные суффиксы/префиксы
        remove_patterns = [
            "область", "край", "республика", "автономный округ", "автономная область",
            "город", "г.", "обл.", "респ.", "а.о.", "ао", "федеральный округ", "фо"
        ]
        for pattern in remove_patterns:
            region = region.replace(pattern, "")
        return region.strip()

    def _check_region_match(self, filter_regions: List[str], tender_region: str) -> bool:
        """
        Проверяет соответствие региона тендера фильтру.

        Использует fuzzy matching с учётом:
        - Вариаций написания (СПб = Санкт-Петербург)
        - Сокращений (ЯНАО = Ямало-Ненецкий АО)
        - Частичного совпадения

        Args:
            filter_regions: Список регионов из фильтра
            tender_region: Регион тендера

        Returns:
            True если регион подходит, False иначе
        """
        if not filter_regions:
            return True  # Нет ограничений по региону

        if not tender_region or tender_region.strip() == "" or tender_region.strip() == "Не указан":
            # Регион тендера неизвестен - отклоняем если фильтр требует регион
            logger.info(f"   ⛔ Регион тендера пустой, но фильтр требует: {filter_regions[:3]}...")
            return False

        # Нормализуем регион тендера через единый справочник
        try:
            from tender_sniper.regions import normalize_region as _norm_region
            canonical_tender = _norm_region(tender_region)
            if canonical_tender:
                tender_region = canonical_tender
        except ImportError:
            pass

        tender_region_lower = tender_region.lower().strip()
        tender_region_normalized = self._normalize_region(tender_region_lower)

        logger.debug(f"   🗺️ Проверка региона: tender='{tender_region_lower}' (norm: '{tender_region_normalized}')")
        logger.debug(f"   🗺️ Фильтр регионов: {filter_regions[:5]}{'...' if len(filter_regions) > 5 else ''}")

        for filter_region in filter_regions:
            filter_region_lower = filter_region.lower().strip()
            filter_region_normalized = self._normalize_region(filter_region_lower)

            # 1. Прямое совпадение
            if filter_region_lower == tender_region_lower:
                logger.debug(f"   ✅ Точное совпадение региона: {filter_region_lower}")
                return True

            # 2. Нормализованное совпадение
            if filter_region_normalized == tender_region_normalized:
                logger.debug(f"   ✅ Нормализованное совпадение: {filter_region_normalized}")
                return True

            # 3. Вхождение (с учётом порядка)
            if filter_region_lower in tender_region_lower:
                logger.debug(f"   ✅ Фильтр содержится в регионе тендера: '{filter_region_lower}' in '{tender_region_lower}'")
                return True

            if tender_region_lower in filter_region_lower:
                logger.debug(f"   ✅ Регион тендера содержится в фильтре: '{tender_region_lower}' in '{filter_region_lower}'")
                return True

            # 4. Проверка нормализованных версий на вхождение
            if len(filter_region_normalized) >= 4 and filter_region_normalized in tender_region_lower:
                logger.debug(f"   ✅ Нормализованный фильтр в регионе: '{filter_region_normalized}' in '{tender_region_lower}'")
                return True

            if len(tender_region_normalized) >= 4 and tender_region_normalized in filter_region_lower:
                logger.debug(f"   ✅ Нормализованный регион в фильтре: '{tender_region_normalized}' in '{filter_region_lower}'")
                return True

            # 5. Проверка алиасов (точное совпадение — без substring, чтобы не было
            #    ложных срабатываний типа "ленинград" matching "ленинградская область")
            aliases = self.REGION_ALIASES.get(filter_region_normalized, [])
            for alias in aliases:
                alias_lower = alias.lower()
                if alias_lower == tender_region_lower or tender_region_lower == alias_lower:
                    logger.debug(f"   ✅ Совпадение по алиасу: {filter_region_normalized} -> {alias_lower}")
                    return True

            # Также проверяем алиасы для региона тендера
            tender_aliases = self.REGION_ALIASES.get(tender_region_normalized, [])
            for alias in tender_aliases:
                alias_lower = alias.lower()
                if alias_lower == filter_region_lower or filter_region_lower == alias_lower:
                    logger.debug(f"   ✅ Совпадение по алиасу тендера: {tender_region_normalized} -> {alias_lower}")
                    return True

        # Ничего не совпало
        logger.info(f"   ⛔ Регион не совпадает: tender='{tender_region_lower}', filter={filter_regions[:3]}...")
        return False

    def _is_short_keyword_whitelisted(self, word: str) -> bool:
        """Проверяет, является ли короткое слово разрешённым ключевым словом."""
        return word.lower().strip() in self.SHORT_KEYWORDS_WHITELIST

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
            meaningful_words = [w for w in words if not self._is_stop_word(w) and (len(w) >= 3 or self._is_short_keyword_whitelisted(w))]
            if meaningful_words:
                # Добавляем как отдельные слова
                keywords.extend(meaningful_words)

        return keywords

    def _word_boundary_match(self, keyword: str, text: str) -> bool:
        """
        Проверяет совпадение слова с учетом границ слов.
        Избегает ложных срабатываний типа 'служб' в 'службы военной'.
        Использует кэшированные regex паттерны для производительности.
        """
        keyword_lower = keyword.lower().strip()

        # Для коротких слов (< 4 символов) требуем точное совпадение с границами
        if len(keyword_lower) < 4:
            pattern = r'\b' + re.escape(keyword_lower) + r'\b'
        else:
            # Для более длинных слов - ищем начало слова
            # Это позволяет найти "linux" в "linux-система" или "линукс"
            pattern = r'\b' + re.escape(keyword_lower)

        return bool(_compile_pattern(pattern, re.IGNORECASE).search(text))

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

    def _check_phrase_word(self, word: str, text: str) -> bool:
        """
        Проверяет совпадение одного слова фразы в тексте.
        Учитывает синонимы, аббревиатуры и расшифровки.
        """
        # Прямое слово
        if self._word_boundary_match(word, text):
            return True

        # Проверяем расшифровки аббревиатур (каждое слово расшифровки ищем через prefix)
        for expansion in self.ABBREVIATIONS.get(word, []):
            exp_words = expansion.split()
            if all(self._word_boundary_match(ew, text) for ew in exp_words):
                return True

        # Проверяем синонимы
        for synonym in self.SYNONYMS.get(word, []):
            syn_words = synonym.split()
            if all(self._word_boundary_match(sw, text) for sw in syn_words):
                return True

        return False

    def _match_compound_phrase(self, phrase: str, text: str) -> bool:
        """
        Проверяет совпадение составной фразы в тексте.
        Фраза должна встречаться целиком или через синонимы.
        Для пользовательских фраз (не в COMPOUND_PHRASES) — требует,
        чтобы все слова фразы встречались в тексте (AND-логика с аббревиатурами).
        """
        phrase_lower = phrase.lower().strip()
        text_lower = text.lower()

        # Прямое совпадение фразы целиком
        if phrase_lower in text_lower:
            return True

        # Проверяем синонимы из COMPOUND_PHRASES (только для словарных фраз)
        synonyms = self.COMPOUND_PHRASES.get(phrase_lower, [])
        for synonym in synonyms:
            if synonym.lower() in text_lower:
                return True

        # Для пользовательских многословных ключевых слов (не в COMPOUND_PHRASES):
        # требуем, чтобы ВСЕ значимые слова фразы встречались в тексте
        # (с учётом синонимов и расшифровок аббревиатур)
        if ' ' in phrase_lower and phrase_lower not in self.COMPOUND_PHRASES:
            phrase_words = [
                w for w in phrase_lower.split()
                if not self._is_stop_word(w) and (len(w) >= 2 or self._is_short_keyword_whitelisted(w))
            ]
            if phrase_words and all(self._check_phrase_word(w, text_lower) for w in phrase_words):
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

            # Проверяем, является ли это словарной составной фразой
            if keyword_lower in self.COMPOUND_PHRASES:
                compound_found.append(keyword)
            elif ' ' in keyword_lower:
                # Многословный keyword от пользователя (не в словаре):
                # проверяем, содержит ли он известную составную фразу
                found_compound = False
                for phrase in self.COMPOUND_PHRASES:
                    if phrase in keyword_lower:
                        compound_found.append(phrase)
                        found_compound = True
                        # Оставшиеся значимые слова — в individual
                        remaining_text = keyword_lower.replace(phrase, '').strip()
                        if remaining_text:
                            for word in remaining_text.split():
                                if not self._is_stop_word(word) and (len(word) >= 3 or self._is_short_keyword_whitelisted(word)):
                                    remaining.append(word)
                        break
                if not found_compound:
                    # Пользовательская фраза: матчить как фразу (AND по всем словам)
                    # Не разбиваем на отдельные слова, чтобы избежать false positives
                    compound_found.append(keyword)
            else:
                remaining.append(keyword)

        return compound_found, remaining

    def apply_feedback_penalty(
        self,
        score: int,
        tender: Dict[str, Any],
        user_negative_keywords: List[str]
    ) -> int:
        """
        Применяет штраф к score на основе feedback learning.

        Если в названии/описании тендера найдены слова, которые пользователь
        часто пропускал ранее, снижаем score.

        Args:
            score: Текущий score
            tender: Данные тендера
            user_negative_keywords: Персональные negative keywords из feedback

        Returns:
            Скорректированный score
        """
        if not user_negative_keywords:
            return score

        searchable_text = (
            tender.get('name', '') + ' ' +
            (tender.get('description', '') or '')
        ).lower()

        penalties = 0
        matched_negative = []

        for keyword in user_negative_keywords:
            if self._word_boundary_match(keyword, searchable_text):
                penalties += 1
                matched_negative.append(keyword)

        if penalties > 0:
            # Штраф: 5% за каждое совпадение, максимум 30%
            penalty_percent = min(0.30, penalties * 0.05)
            penalty_points = int(score * penalty_percent)
            new_score = max(0, score - penalty_points)

            logger.debug(
                f"   📉 Feedback penalty: -{penalty_points} points "
                f"(matched: {matched_negative[:3]})"
            )
            return new_score

        return score

    def match_tender(
        self,
        tender: Dict[str, Any],
        filter_config: Dict[str, Any],
        user_negative_keywords: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Проверка, соответствует ли тендер фильтру.

        Args:
            tender: Данные тендера
            filter_config: Конфигурация фильтра пользователя
            user_negative_keywords: Персональные negative keywords (feedback learning)

        Returns:
            Результат матчинга со score или None если не подходит
        """
        try:
            return self._match_tender_internal(tender, filter_config, user_negative_keywords)
        except Exception as e:
            logger.error(
                f"Error scoring tender {tender.get('number', '?')}: {e}",
                exc_info=True
            )
            return None  # Безопасный fallback — не крашим pipeline

    def _match_tender_internal(
        self,
        tender: Dict[str, Any],
        filter_config: Dict[str, Any],
        user_negative_keywords: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Внутренняя логика match_tender, обёрнутая в try/except."""
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

        # ВАЖНО: НЕ включаем customer_name в основной поиск!
        # Иначе "УПРАВЛЕНИЕ ЛОГИСТИКИ" матчит любые тендеры этого заказчика
        # Поиск идёт ТОЛЬКО по названию и описанию тендера
        searchable_text = f"{tender_name} {tender_description}"

        # Название заказчика храним отдельно для специальных фильтров (customer_keywords)
        customer_text = customer_name

        # ============================================
        # 1. ПРОВЕРКА ИСКЛЮЧАЮЩИХ ФИЛЬТРОВ
        # ============================================

        # 1.1 Проверка негативных паттернов (низкий score, но не исключаем)
        negative_match = self._check_negative_patterns(searchable_text)
        if negative_match:
            logger.debug(f"   ⚠️ Негативный паттерн (low score): {negative_match}")
            return {
                'filter_id': filter_config.get('id'),
                'filter_name': filter_config.get('name'),
                'score': 5,
                'matched_keywords': [],
                'reasons': [f'Негативный паттерн: {negative_match}'],
                'matched_at': datetime.now().isoformat(),
                'tender_number': tender.get('number'),
                'tender_name': tender.get('name'),
                'tender_price': tender_price,
                'tender_url': tender.get('url'),
                'red_flags': detect_red_flags(tender)
            }

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

        # Проверка региона (СТРОГАЯ - если указан регион в фильтре, тендер ДОЛЖЕН соответствовать)
        if regions:
            # Раскрываем федеральные округа в отдельные регионы
            expanded_regions = self._expand_federal_districts(regions)

            # Используем умный метод сравнения с поддержкой алиасов
            if not self._check_region_match(expanded_regions, tender_region):
                return None  # СТРОГАЯ ФИЛЬТРАЦИЯ: отклоняем тендеры из других регионов!

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
                logger.debug(f"   ⚠️ Нет совпадений по SmartMatcher, но найден по RSS")
                return {
                    'filter_id': filter_config.get('id'),
                    'filter_name': filter_config.get('name'),
                    'score': 10,
                    'matched_keywords': [],
                    'reasons': ['Найден по поисковому запросу RSS'],
                    'matched_at': datetime.now().isoformat(),
                    'tender_number': tender.get('number'),
                    'tender_name': tender.get('name'),
                    'tender_price': tender_price,
                    'tender_url': tender.get('url'),
                    'red_flags': detect_red_flags(tender)
                }

            # ШАГ 7: СТРОГИЙ РЕЖИМ для фильтров с множеством ключевых слов
            # Если пользователь указал 5+ слов, требуем качественного матчинга
            match_ratio = len(matched_keywords) / total_criteria

            if total_criteria >= self.MIN_KEYWORDS_FOR_STRICT_MODE:
                # Строгий режим: штраф за мало совпадений (но не отсекаем!)
                if len(matched_keywords) < self.MIN_MATCHES_ABSOLUTE or match_ratio < self.MIN_MATCH_RATIO_STRICT:
                    penalty = int(score * 0.40)
                    score -= penalty
                    logger.debug(f"   ⚠️ Строгий режим: штраф -{penalty} ({len(matched_keywords)} совпадений, {match_ratio:.0%})")
                else:
                    logger.debug(f"   ✅ Строгий режим пройден: {len(matched_keywords)} совпадений ({match_ratio:.0%})")

            # ШАГ 8: Бонус/штраф за процент совпадений
            # Умеренные штрафы - не слишком агрессивные, чтобы не отсекать релевантные тендеры
            if match_ratio < 0.15 and total_criteria >= 5:
                # Штраф только при очень низком % совпадений И большом числе слов
                penalty = int(score * 0.25)
                score -= penalty
                logger.debug(f"   ⚠️ Штраф за очень низкий % совпадений ({match_ratio:.0%}): -{penalty}")
            elif match_ratio < 0.25 and total_criteria >= 5:
                # Небольшой штраф за низкий процент совпадений
                penalty = int(score * 0.15)
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
            except (ValueError, TypeError, AttributeError):
                pass

        # ============================================
        # 5. FEEDBACK LEARNING (Premium)
        # ============================================

        # Применяем персональные штрафы на основе истории пропусков
        if user_negative_keywords:
            score = self.apply_feedback_penalty(score, tender, user_negative_keywords)

        # ============================================
        # 6. НОРМАЛИЗАЦИЯ SCORE (0-100)
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

        # Детектируем красные флаги
        red_flags = detect_red_flags(tender)
        if red_flags:
            logger.info(f"   🚩 Обнаружены красные флаги: {red_flags}")

        return {
            'filter_id': filter_config.get('id'),
            'filter_name': filter_config.get('name'),
            'score': score,
            'matched_keywords': matched_keywords,
            'reasons': [f'Совпадение: {kw}' for kw in matched_keywords],
            'matched_at': datetime.now().isoformat(),
            'tender_number': tender.get('number'),
            'tender_name': tender.get('name'),
            'tender_price': tender_price,
            'tender_url': tender.get('url'),
            'red_flags': red_flags
        }

    def match_against_filters(
        self,
        tender: Dict[str, Any],
        filters: List[Dict[str, Any]],
        min_score: int = 75,
        user_negative_keywords: Optional[List[str]] = None
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
            match_result = self.match_tender(tender, filter_config, user_negative_keywords)

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
        min_score: int = 75
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
            except (json.JSONDecodeError, ValueError, TypeError):
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
