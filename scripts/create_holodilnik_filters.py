"""
One-shot: создание фильтров по категориям holodilnik.ru для пользователя Николая.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from database import DatabaseSession, SniperUser, SniperFilter
from tender_sniper.regions import get_regions_by_district

TELEGRAM_ID = 298437198

# Регионы: ЦФО + СЗФО + ЮФО + ПФО
REGIONS = (
    get_regions_by_district("Центральный")
    + get_regions_by_district("Северо-Западный")
    + get_regions_by_district("Южный")
    + get_regions_by_district("Приволжский")
)

PRICE_MIN = 200_000
PRICE_MAX = 4_000_000

# Общие слова-исключения (отсекают услуги/ремонт)
BASE_EXCLUDE = [
    "ремонт", "техническое обслуживание", "сервисное обслуживание",
    "диагностика", "утилизация",
]

FILTERS = [
    {
        "name": "Крупная бытовая техника",
        "keywords": [
            "холодильник", "морозильник", "морозильная камера",
            "стиральная машина", "сушильная машина", "стирально-сушильная машина",
            "кондиционер", "сплит-система",
        ],
        "exclude_extra": [
            "заправка кондиционеров", "монтаж кондиционеров", "чистка кондиционеров",
        ],
    },
    {
        "name": "Кухонная техника",
        "keywords": [
            "электрическая плита", "газовая плита", "духовой шкаф",
            "варочная панель", "посудомоечная машина", "микроволновая печь",
            "кухонная вытяжка", "мультиварка", "кофемашина",
            "блендер", "миксер", "мясорубка", "тостер", "чайник электрический",
        ],
        "exclude_extra": ["монтаж вытяжки", "установка плиты"],
    },
    {
        "name": "Электроника",
        "keywords": [
            "телевизор", "ноутбук", "смартфон", "планшет", "монитор",
            "акустическая система", "наушники", "медиаплеер", "саундбар", "проектор",
        ],
        "exclude_extra": ["ремонт ноутбуков", "ремонт телевизоров"],
    },
    {
        "name": "Техника для дома",
        "keywords": [
            "пылесос", "робот-пылесос", "утюг", "отпариватель",
            "увлажнитель воздуха", "очиститель воздуха",
            "обогреватель", "вентилятор напольный",
            "весы напольные", "швейная машина",
        ],
        "exclude_extra": [],
    },
    {
        "name": "Красота и здоровье",
        "keywords": [
            "фен", "электробритва", "машинка для стрижки",
            "эпилятор", "массажёр", "ирригатор", "электрическая зубная щётка",
        ],
        "exclude_extra": [],
    },
    {
        "name": "Дом и сад",
        "keywords": [
            "газонокосилка", "триммер садовый", "мойка высокого давления",
            "бензопила", "электропила", "культиватор",
            "мотоблок", "снегоуборщик", "воздуходувка",
        ],
        "exclude_extra": ["ремонт газонокосилок"],
    },
    {
        "name": "Электроинструмент",
        "keywords": [
            "перфоратор", "шуруповёрт", "дрель", "болгарка",
            "лобзик", "сварочный аппарат", "циркулярная пила",
            "строительный фен", "шлифовальная машина",
        ],
        "exclude_extra": [],
    },
]


async def main():
    async with DatabaseSession() as session:
        user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == TELEGRAM_ID)
        )
        if not user:
            print(f"ERROR: user telegram_id={TELEGRAM_ID} не найден")
            return

        print(f"User: {user.username} (id={user.id})")

        # Проверяем нет ли уже таких фильтров
        existing = await session.scalar(
            select(SniperFilter).where(
                SniperFilter.user_id == user.id,
                SniperFilter.name == FILTERS[0]["name"],
            )
        )
        if existing:
            print("Фильтры уже созданы, пропускаю.")
            return

        created = 0
        for f in FILTERS:
            exclude = BASE_EXCLUDE + f.get("exclude_extra", [])

            new_filter = SniperFilter(
                user_id=user.id,
                name=f["name"],
                keywords=f["keywords"],
                exclude_keywords=exclude,
                price_min=PRICE_MIN,
                price_max=PRICE_MAX,
                regions=REGIONS,
                law_type=None,  # оба ФЗ
            )
            session.add(new_filter)
            created += 1
            print(f"  + {f['name']} ({len(f['keywords'])} keywords, {len(exclude)} excludes)")

        await session.commit()
        print(f"\nOK: создано {created} фильтров для {user.username}")


if __name__ == "__main__":
    asyncio.run(main())
