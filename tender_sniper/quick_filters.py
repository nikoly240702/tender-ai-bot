"""
Quick Filters Library.

Готовые шаблоны фильтров для быстрого создания.
Пользователь выбирает шаблон → получает готовый фильтр.

Feature flag: quick_filters (config/features.yaml)
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class QuickFilterTemplate:
    """Template for quick filter creation."""
    id: str
    name: str
    icon: str
    description: str
    industry: str
    keywords: List[str]
    exclude_keywords: List[str] = field(default_factory=list)
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    suggested_regions: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)


# ============================================
# IT & TELECOM
# ============================================

IT_FILTERS = [
    QuickFilterTemplate(
        id="it_servers",
        name="Серверы и СХД",
        icon="🖥",
        description="Серверное оборудование, системы хранения данных",
        industry="IT",
        keywords=["сервер", "серверное оборудование", "СХД", "система хранения данных", "серверная платформа"],
        exclude_keywords=["медицин", "военн", "продукты питания"],
        price_min=100000,
        price_max=50000000,
        tags=["hardware", "datacenter"]
    ),
    QuickFilterTemplate(
        id="it_computers",
        name="Компьютеры и ноутбуки",
        icon="💻",
        description="ПК, ноутбуки, рабочие станции",
        industry="IT",
        keywords=["компьютер", "ноутбук", "рабочая станция", "ПК", "персональный компьютер", "моноблок"],
        exclude_keywords=["медицин", "военн"],
        price_min=50000,
        price_max=10000000,
        tags=["hardware", "desktop"]
    ),
    QuickFilterTemplate(
        id="it_network",
        name="Сетевое оборудование",
        icon="🌐",
        description="Коммутаторы, маршрутизаторы, Wi-Fi",
        industry="IT",
        keywords=["коммутатор", "маршрутизатор", "сетевое оборудование", "wi-fi", "точка доступа", "роутер"],
        exclude_keywords=["медицин"],
        price_min=50000,
        price_max=20000000,
        tags=["hardware", "network"]
    ),
    QuickFilterTemplate(
        id="it_software_ms",
        name="Лицензии Microsoft",
        icon="Ⓜ️",
        description="Windows, Office, серверные лицензии",
        industry="IT",
        keywords=["Microsoft", "Windows", "Office", "лицензия Microsoft", "MS Office", "Windows Server"],
        exclude_keywords=[],
        price_min=50000,
        price_max=50000000,
        tags=["software", "microsoft"]
    ),
    QuickFilterTemplate(
        id="it_software_1c",
        name="1С и интеграции",
        icon="🔢",
        description="Лицензии 1С, внедрение, сопровождение",
        industry="IT",
        keywords=["1С", "1C", "лицензия 1С", "внедрение 1С", "сопровождение 1С", "1С:Предприятие"],
        exclude_keywords=[],
        price_min=50000,
        price_max=30000000,
        tags=["software", "1c", "erp"]
    ),
    QuickFilterTemplate(
        id="it_security",
        name="Информационная безопасность",
        icon="🔐",
        description="Антивирусы, защита, криптография",
        industry="IT",
        keywords=["антивирус", "информационная безопасность", "защита информации", "криптография", "СКЗИ"],
        exclude_keywords=["физическая охрана"],
        price_min=100000,
        price_max=50000000,
        tags=["software", "security"]
    ),
    QuickFilterTemplate(
        id="it_printers",
        name="Принтеры и МФУ",
        icon="🖨",
        description="Печатающая техника, расходники",
        industry="IT",
        keywords=["принтер", "МФУ", "печатающее устройство", "картридж", "тонер"],
        exclude_keywords=["3D принтер"],
        price_min=30000,
        price_max=5000000,
        tags=["hardware", "printing"]
    ),
]

# ============================================
# CONSTRUCTION
# ============================================

CONSTRUCTION_FILTERS = [
    QuickFilterTemplate(
        id="const_smr",
        name="Строительно-монтажные работы",
        icon="🏗",
        description="СМР, капитальный ремонт, реконструкция",
        industry="Строительство",
        keywords=["СМР", "строительно-монтажные работы", "капитальный ремонт", "реконструкция", "строительство"],
        exclude_keywords=["дорожный ремонт"],
        price_min=1000000,
        price_max=500000000,
        tags=["works", "construction"]
    ),
    QuickFilterTemplate(
        id="const_materials",
        name="Стройматериалы",
        icon="🧱",
        description="Материалы для строительства",
        industry="Строительство",
        keywords=["стройматериалы", "строительные материалы", "цемент", "бетон", "кирпич", "арматура"],
        exclude_keywords=[],
        price_min=100000,
        price_max=50000000,
        tags=["materials", "construction"]
    ),
    QuickFilterTemplate(
        id="const_design",
        name="Проектирование",
        icon="📐",
        description="Проектные работы, изыскания",
        industry="Строительство",
        keywords=["проектирование", "проектные работы", "проектная документация", "изыскания", "ПСД"],
        exclude_keywords=[],
        price_min=500000,
        price_max=100000000,
        tags=["services", "design"]
    ),
]

# ============================================
# MEDICINE
# ============================================

MEDICINE_FILTERS = [
    QuickFilterTemplate(
        id="med_equipment",
        name="Медицинское оборудование",
        icon="🏥",
        description="Медтехника, диагностика",
        industry="Медицина",
        keywords=["медицинское оборудование", "медтехника", "диагностическое оборудование", "медицинская техника"],
        exclude_keywords=["ветеринар"],
        price_min=100000,
        price_max=100000000,
        tags=["medical", "equipment"]
    ),
    QuickFilterTemplate(
        id="med_consumables",
        name="Расходные материалы",
        icon="💉",
        description="Расходники, реагенты",
        industry="Медицина",
        keywords=["расходные материалы", "медицинские расходники", "реагенты", "перчатки медицинские", "шприцы"],
        exclude_keywords=[],
        price_min=50000,
        price_max=10000000,
        tags=["medical", "consumables"]
    ),
]

# ============================================
# INDUSTRY
# ============================================

INDUSTRY_FILTERS = [
    QuickFilterTemplate(
        id="ind_equipment",
        name="Промышленное оборудование",
        icon="🏭",
        description="Станки, производственные линии",
        industry="Промышленность",
        keywords=["промышленное оборудование", "станок", "производственная линия", "оборудование для производства"],
        exclude_keywords=["медицин"],
        price_min=500000,
        price_max=100000000,
        tags=["industry", "equipment"]
    ),
    QuickFilterTemplate(
        id="ind_compressors",
        name="Компрессоры Atlas Copco",
        icon="💨",
        description="Компрессорное оборудование Atlas Copco",
        industry="Промышленность",
        keywords=["Atlas Copco", "Атлас Копко", "компрессор", "компрессорное оборудование", "винтовой компрессор"],
        exclude_keywords=[],
        price_min=500000,
        price_max=50000000,
        tags=["industry", "compressors", "atlascopco"]
    ),
    QuickFilterTemplate(
        id="ind_spare_parts",
        name="Запчасти и комплектующие",
        icon="⚙️",
        description="Запасные части для оборудования",
        industry="Промышленность",
        keywords=["запчасти", "запасные части", "комплектующие", "расходные материалы для оборудования"],
        exclude_keywords=["автомобиль"],
        price_min=50000,
        price_max=10000000,
        tags=["industry", "spareparts"]
    ),
]

# ============================================
# TRANSPORT
# ============================================

TRANSPORT_FILTERS = [
    QuickFilterTemplate(
        id="trans_vehicles",
        name="Автомобили",
        icon="🚗",
        description="Легковые и грузовые автомобили",
        industry="Транспорт",
        keywords=["автомобиль", "легковой автомобиль", "грузовой автомобиль", "автотранспорт"],
        exclude_keywords=["велосипед"],
        price_min=500000,
        price_max=50000000,
        tags=["transport", "vehicles"]
    ),
    QuickFilterTemplate(
        id="trans_special",
        name="Спецтехника",
        icon="🚜",
        description="Спецтехника, тракторы, экскаваторы",
        industry="Транспорт",
        keywords=["спецтехника", "трактор", "экскаватор", "погрузчик", "бульдозер", "Komatsu", "Caterpillar"],
        exclude_keywords=[],
        price_min=1000000,
        price_max=100000000,
        tags=["transport", "special"]
    ),
    QuickFilterTemplate(
        id="trans_fuel",
        name="ГСМ",
        icon="⛽",
        description="Топливо, масла, смазки",
        industry="Транспорт",
        keywords=["ГСМ", "топливо", "бензин", "дизельное топливо", "моторное масло", "смазочные материалы"],
        exclude_keywords=[],
        price_min=100000,
        price_max=50000000,
        tags=["transport", "fuel"]
    ),
]

# ============================================
# SERVICES
# ============================================

SERVICES_FILTERS = [
    QuickFilterTemplate(
        id="svc_security",
        name="Охрана и безопасность",
        icon="🛡",
        description="Охранные услуги, видеонаблюдение",
        industry="Услуги",
        keywords=["охрана", "охранные услуги", "видеонаблюдение", "СКУД", "пожарная сигнализация"],
        exclude_keywords=["информационная безопасность"],
        price_min=100000,
        price_max=20000000,
        tags=["services", "security"]
    ),
    QuickFilterTemplate(
        id="svc_cleaning",
        name="Клининг",
        icon="🧹",
        description="Уборка, клининговые услуги",
        industry="Услуги",
        keywords=["клининг", "уборка", "уборка помещений", "санитарная уборка", "клининговые услуги"],
        exclude_keywords=[],
        price_min=100000,
        price_max=10000000,
        tags=["services", "cleaning"]
    ),
    QuickFilterTemplate(
        id="svc_catering",
        name="Питание",
        icon="🍽",
        description="Организация питания, кейтеринг",
        industry="Услуги",
        keywords=["питание", "организация питания", "продукты питания", "кейтеринг", "столовая"],
        exclude_keywords=[],
        price_min=100000,
        price_max=50000000,
        tags=["services", "catering"]
    ),
]

# ============================================
# ALL TEMPLATES
# ============================================

ALL_TEMPLATES: List[QuickFilterTemplate] = (
    IT_FILTERS +
    CONSTRUCTION_FILTERS +
    MEDICINE_FILTERS +
    INDUSTRY_FILTERS +
    TRANSPORT_FILTERS +
    SERVICES_FILTERS
)

# Index by ID
TEMPLATES_BY_ID: Dict[str, QuickFilterTemplate] = {t.id: t for t in ALL_TEMPLATES}

# Index by industry
TEMPLATES_BY_INDUSTRY: Dict[str, List[QuickFilterTemplate]] = {}
for template in ALL_TEMPLATES:
    if template.industry not in TEMPLATES_BY_INDUSTRY:
        TEMPLATES_BY_INDUSTRY[template.industry] = []
    TEMPLATES_BY_INDUSTRY[template.industry].append(template)


# ============================================
# API Functions
# ============================================

def get_all_templates() -> List[QuickFilterTemplate]:
    """Get all available quick filter templates."""
    return ALL_TEMPLATES


def get_template_by_id(template_id: str) -> Optional[QuickFilterTemplate]:
    """Get template by ID."""
    return TEMPLATES_BY_ID.get(template_id)


def get_templates_by_industry(industry: str) -> List[QuickFilterTemplate]:
    """Get templates for specific industry."""
    return TEMPLATES_BY_INDUSTRY.get(industry, [])


def get_industries() -> List[str]:
    """Get list of all industries."""
    return list(TEMPLATES_BY_INDUSTRY.keys())


def search_templates(query: str) -> List[QuickFilterTemplate]:
    """Search templates by name, description, or keywords."""
    query_lower = query.lower()
    results = []

    for template in ALL_TEMPLATES:
        # Check name
        if query_lower in template.name.lower():
            results.append(template)
            continue

        # Check description
        if query_lower in template.description.lower():
            results.append(template)
            continue

        # Check keywords
        if any(query_lower in kw.lower() for kw in template.keywords):
            results.append(template)
            continue

        # Check tags
        if any(query_lower in tag for tag in template.tags):
            results.append(template)
            continue

    return results


def get_template_as_filter_data(template: QuickFilterTemplate, user_id: int) -> Dict:
    """
    Convert template to filter data dictionary.

    This can be passed directly to db.create_filter().
    """
    return {
        'user_id': user_id,
        'name': f"{template.icon} {template.name}",
        'keywords': template.keywords,
        'exclude_keywords': template.exclude_keywords,
        'price_min': template.price_min,
        'price_max': template.price_max,
        'regions': template.suggested_regions if template.suggested_regions else None,
        'is_active': True,
    }
