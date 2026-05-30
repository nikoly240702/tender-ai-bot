"""
Сидер batch 3: промышленное оборудование (3 новых фильтра) + расширение 8 существующих.

Запуск:
  cd ~/Desktop/tender-ai-bot-fresh
  python -m scripts.seed_new_filters_2026_05_batch3          # боевой
  python -m scripts.seed_new_filters_2026_05_batch3 --dry    # только план
"""
import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from database import DatabaseSession, SniperFilter, SniperUser
from tender_sniper.regions import FEDERAL_DISTRICTS, get_regions_by_district

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("seed_2026_05_b3")

TELEGRAM_ID = 298437198
DEFAULT_LAW_TYPE = None


DEFAULT_NARROW_REGIONS = (
    get_regions_by_district("Центральный")
    + get_regions_by_district("Северо-Западный")
)


def all_regions() -> list[str]:
    out: list[str] = []
    for d in FEDERAL_DISTRICTS.values():
        out.extend(d["regions"])
    return out


# ---------------------------------------------------------------------------
# Расширение существующих фильтров — добавляем ключевики
# ---------------------------------------------------------------------------

EDITS = [
    {
        "label": "expand: Электроинструмент — добавить пропущенные позиции",
        "name_like": "%электроинструмент%",
        "add_keywords": [
            "штроборез", "шлифмашина угловая", "шлифмашина ленточная",
            "шлифмашина эксцентриковая", "фрезер ручной", "рубанок электрический",
            "пистолет монтажный", "гвоздезабивной пистолет",
            "термовоздуходувка", "паяльная станция",
            "электроножницы по металлу", "бензопила", "электропила цепная",
            "торцовочная пила", "реноватор", "многофункциональный инструмент",
            "шуруповёрт аккумуляторный", "гайковёрт ударный",
            "отбойный молоток электрический", "бетономешалка",
            "виброплита", "нивелир лазерный", "дальномер лазерный",
        ],
        "add_exclude": [],
    },
    {
        "label": "expand: Дом и сад — добавить сезонное оборудование",
        "name_like": "%дом и сад%",
        "add_keywords": [
            "снегоуборщик", "снегоуборочная машина",
            "аэратор для газона", "скарификатор",
            "измельчитель садовый", "садовый пылесос",
            "бензокоса", "мотокультиватор",
            "насос погружной", "насос дренажный", "насос садовый",
            "опрыскиватель аккумуляторный", "разбрасыватель удобрений",
            "тачка садовая", "компостер садовый",
        ],
        "add_exclude": [],
    },
    {
        "label": "expand: Красота и здоровье — дополнить косметологию",
        "name_like": "%красот%здоров%",
        "add_keywords": [
            "стерилизатор маникюрный", "УФ-стерилизатор",
            "лампа для маникюра", "лампа UV/LED",
            "парикмахерское кресло", "зеркало косметическое",
            "весы напольные электронные", "ирригатор",
            "электрическая зубная щётка", "триммер для бороды",
            "эпилятор", "массажное кресло", "массажная подушка",
        ],
        "add_exclude": [],
    },
    {
        "label": "expand: Техника для дома — добавить климат и уборку",
        "name_like": "%техник%для дом%",
        "add_keywords": [
            "робот-мойщик окон", "пароочиститель",
            "сушилка для белья электрическая",
            "вентилятор напольный", "вентилятор потолочный",
            "тепловентилятор", "конвектор электрический",
            "масляный радиатор", "инфракрасный обогреватель",
            "осушитель воздуха", "ионизатор воздуха",
            "мойка воздуха", "рециркулятор бактерицидный",
            "водонагреватель электрический", "бойлер",
        ],
        "add_exclude": [],
    },
    {
        "label": "expand: Электроника — добавить аудио/видео и аксессуары",
        "name_like": "%электроник%",
        "add_keywords": [
            "видеорегистратор", "экшн-камера",
            "фотоаппарат цифровой", "объектив для камеры",
            "штатив для камеры", "стабилизатор для камеры",
            "портативная колонка", "умная колонка",
            "электронная книга", "графический планшет",
            "док-станция", "сетевое хранилище NAS",
            "роутер Wi-Fi", "коммутатор сетевой",
            "IP-камера видеонаблюдения", "видеодомофон",
        ],
        "add_exclude": [],
    },
    {
        "label": "expand: Кухонная техника — мелкая кухонная техника",
        "name_like": "%кухон%техник%",
        "add_keywords": [
            "хлебопечка", "йогуртница", "мороженица",
            "яйцеварка", "сэндвичница", "вафельница",
            "электрогриль", "аэрогриль", "сушилка для овощей и фруктов",
            "соковыжималка", "кухонный комбайн",
            "кофемолка", "капучинатор", "термопот",
            "электрический чайник", "диспенсер для воды",
        ],
        "add_exclude": [],
    },
    {
        "label": "expand: Крупная бытовая техника — встраиваемая + сушка",
        "name_like": "%крупн%бытов%техник%",
        "add_keywords": [
            "холодильник встраиваемый", "морозильник встраиваемый",
            "посудомоечная машина встраиваемая",
            "стиральная машина встраиваемая",
            "винный шкаф", "льдогенератор",
            "водоочиститель", "фильтр для воды обратный осмос",
            "кулер для воды", "пурифайер",
        ],
        "add_exclude": [],
    },
    {
        "label": "expand: Бумага — добавить спецбумагу",
        "name_like": "%бумаг%",
        "add_keywords": [
            "бумага для плоттера", "бумага широкоформатная",
            "фотобумага для принтера", "бумага термическая",
            "бумага для факса", "ролик чековый", "чековая лента",
            "конверт почтовый", "конверт С4", "конверт С5",
            "пакет почтовый", "бумага крафт",
        ],
        "add_exclude": [],
    },
]


# ---------------------------------------------------------------------------
# 3 новых фильтра — промышленное оборудование
# ---------------------------------------------------------------------------

def build_new_filters(default_regions: list[str]) -> list[dict]:
    default_narrow = DEFAULT_NARROW_REGIONS

    return [
        {
            "name": "Двигатели и приводная техника",
            "keywords": [
                "электродвигатель", "электродвигатель асинхронный",
                "электродвигатель АИР", "электродвигатель 5АИ",
                "мотор-редуктор", "мотор-редуктор червячный",
                "мотор-редуктор цилиндрический", "мотор-редуктор планетарный",
                "редуктор червячный", "редуктор цилиндрический",
                "редуктор конический", "редуктор планетарный",
                "частотный преобразователь", "преобразователь частоты",
                "частотник",
                "привод электрический", "электропривод",
                "сервопривод", "сервомотор",
                "вариатор", "вариатор промышленный",
                "муфта соединительная", "муфта упругая", "муфта зубчатая",
                "подшипник промышленный", "подшипник шариковый",
                "подшипник роликовый", "подшипник игольчатый",
                "ремень приводной", "ремень клиновой", "ремень зубчатый",
                "цепь приводная", "цепь роликовая",
                "звёздочка приводная", "шкив клиноремённый",
                "плавный пуск", "устройство плавного пуска",
            ],
            "exclude_keywords": [
                "услуги", "ремонт электродвигателей", "перемотка двигателей",
                "монтажные работы", "пусконаладочные работы",
                "техническое обслуживание", "диагностика двигателей",
                "автомобильный", "авиационный", "судовой",
            ],
            "price_min": 200_000, "price_max": 6_000_000,
            "regions": default_regions,
        },
        {
            "name": "Промышленное оборудование и станки",
            "keywords": [
                "компрессор промышленный", "компрессор винтовой",
                "компрессор поршневой",
                "насос промышленный", "насос центробежный",
                "насос шестерёнчатый", "насос мембранный",
                "станок токарный", "станок фрезерный",
                "станок сверлильный", "станок шлифовальный",
                "станок ленточнопильный", "ленточная пила по металлу",
                "гильотина для металла", "листогиб",
                "пресс гидравлический", "пресс механический",
                "сварочный аппарат промышленный", "сварочный полуавтомат",
                "аппарат аргонодуговой сварки",
                "генератор дизельный", "дизель-генератор",
                "генератор бензиновый", "электростанция передвижная",
                "трансформатор силовой", "трансформатор сварочный",
                "конвейер ленточный", "транспортёр", "рольганг",
                "тельфер", "таль электрическая", "таль ручная",
                "лебёдка электрическая", "кран-балка",
                "домкрат гидравлический", "домкрат реечный",
                "вибростол", "вибратор глубинный",
                "промышленный пылесос", "аспирация",
            ],
            "exclude_keywords": [
                "услуги", "ремонт оборудования", "монтажные работы",
                "пусконаладочные работы", "техническое обслуживание",
                "аренда оборудования", "прокат", "лизинг",
                "проектирование",
            ],
            "price_min": 300_000, "price_max": 6_000_000,
            "regions": default_narrow,
        },
        {
            "name": "Запчасти и комплектующие для промоборудования",
            "keywords": [
                "вал приводной", "вал карданный",
                "шестерня", "шестерня цилиндрическая", "шестерня коническая",
                "звёздочка цепная", "шпонка",
                "манжета уплотнительная", "сальник", "сальник армированный",
                "прокладка уплотнительная", "о-ринг", "кольцо уплотнительное",
                "фильтр масляный промышленный", "фильтр гидравлический",
                "фильтр воздушный для компрессора",
                "фильтроэлемент",
                "ремкомплект", "ремкомплект насоса", "ремкомплект гидроцилиндра",
                "гидроцилиндр", "гидрораспределитель", "гидроклапан",
                "пневмоцилиндр", "пневмораспределитель",
                "электромагнитный клапан", "соленоидный клапан",
                "запорная арматура промышленная",
                "задвижка", "затвор дисковый", "кран шаровой промышленный",
                "фланец", "отвод стальной", "тройник стальной",
                "труба стальная бесшовная",
                "метизы промышленные", "болт высокопрочный",
                "шпилька резьбовая", "гайка высокопрочная",
                "анкер", "анкерный болт",
            ],
            "exclude_keywords": [
                "услуги", "ремонт", "монтаж", "сварочные работы",
                "техническое обслуживание", "диагностика",
                "автомобильные запчасти", "авто", "для автомобиля",
            ],
            "price_min": 100_000, "price_max": 3_000_000,
            "regions": default_regions,
        },
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _name_matches_like(name: str | None, pattern: str) -> bool:
    if not name:
        return False
    parts = [p for p in pattern.lower().split("%") if p]
    pos = 0
    low = name.lower()
    for part in parts:
        idx = low.find(part, pos)
        if idx == -1:
            return False
        pos = idx + len(part)
    return True


def _resolve_default_regions(filters_of_user: list[SniperFilter]) -> list[str]:
    for f in filters_of_user:
        if f.name and "бумаг" in f.name.lower():
            regs = f.regions or []
            if regs:
                log.info(
                    "default_regions: использую regions фильтра '%s' (%d шт.)",
                    f.name, len(regs),
                )
                return list(regs)
    fallback = (
        get_regions_by_district("Центральный")
        + get_regions_by_district("Северо-Западный")
        + get_regions_by_district("Южный")
        + get_regions_by_district("Приволжский")
    )
    log.warning(
        "default_regions: fallback (ЦФО+СЗФО+ЮФО+ПФО, %d шт.)", len(fallback),
    )
    return fallback


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(dry: bool = False) -> None:
    async with DatabaseSession() as session:
        user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == TELEGRAM_ID)
        )
        if not user:
            log.error("user telegram_id=%s не найден", TELEGRAM_ID)
            return

        log.info("user: id=%s username=%s tier=%s", user.id, user.username, user.subscription_tier)

        all_filters = list(
            (
                await session.scalars(
                    select(SniperFilter).where(
                        SniperFilter.user_id == user.id,
                        SniperFilter.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        log.info("активных фильтров: %d", len(all_filters))

        # ----- Шаг 1: расширение существующих фильтров -----
        edits_applied = 0
        for rule in EDITS:
            label = rule["label"]
            if "name_equals" in rule:
                matched = [f for f in all_filters if f.name == rule["name_equals"]]
            else:
                matched = [
                    f for f in all_filters
                    if _name_matches_like(f.name, rule["name_like"])
                ]
            if not matched:
                log.warning("[skip] %s — фильтры не найдены", label)
                continue

            for f in matched:
                changed = False

                if rule.get("add_keywords"):
                    before = list(f.keywords or [])
                    after = _dedupe_preserve_order(before + rule["add_keywords"])
                    if after != before:
                        f.keywords = after
                        flag_modified(f, "keywords")
                        changed = True
                        added = [x for x in after if x not in set(before)]
                        log.info("  [%s] keywords: +%d (%s...)", f.name, len(added), added[:3])

                if rule.get("add_exclude"):
                    before = list(f.exclude_keywords or [])
                    after = _dedupe_preserve_order(before + rule["add_exclude"])
                    if after != before:
                        f.exclude_keywords = after
                        flag_modified(f, "exclude_keywords")
                        changed = True
                        log.info("  [%s] exclude_keywords: +%d", f.name, len(after) - len(before))

                if changed:
                    edits_applied += 1
                    log.info("  ✓ %s — обновлён", f.name)
                else:
                    log.info("  · %s — уже актуален", f.name)

        log.info("правок применено: %d", edits_applied)

        # ----- Шаг 2: промышленные фильтры -----
        default_regions = _resolve_default_regions(all_filters)
        new_filters = build_new_filters(default_regions)
        existing_names = {f.name for f in all_filters}

        created = 0
        skipped = 0
        for spec in new_filters:
            if spec["name"] in existing_names:
                log.info("[skip] '%s' — уже существует", spec["name"])
                skipped += 1
                continue

            if not dry:
                obj = SniperFilter(
                    user_id=user.id,
                    name=spec["name"],
                    keywords=spec["keywords"],
                    exclude_keywords=spec["exclude_keywords"],
                    price_min=spec["price_min"],
                    price_max=spec["price_max"],
                    regions=spec["regions"],
                    law_type=DEFAULT_LAW_TYPE,
                    is_active=True,
                )
                session.add(obj)
            log.info(
                "  + '%s' (kw=%d, excl=%d, регионов=%d, цена=%s..%s)",
                spec["name"], len(spec["keywords"]), len(spec["exclude_keywords"]),
                len(spec["regions"]), spec["price_min"], spec["price_max"],
            )
            created += 1

        if dry:
            log.info("DRY: created=%d, skipped=%d", created, skipped)
            await session.rollback()
            return

        await session.commit()
        log.info("OK: edits=%d, created=%d, skipped=%d", edits_applied, created, skipped)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true", help="dry-run без записи в БД")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(dry=args.dry))
