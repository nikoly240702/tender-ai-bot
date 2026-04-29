"""
Сидер: 11 новых фильтров + 4 правки существующих для Николая (TG=298437198).

Источник спецификации:
  /Users/nikolaichizhik/Library/Application Support/Claude/local-agent-mode-sessions/
  ac897e4b-.../local_936a7ac9-.../outputs/tender_sniper_new_filters.md
  (стратегическая сессия 2026-04-27).

Идемпотентность:
  - Существующие фильтры ищутся по имени (LIKE) — изменения применяются всегда:
    дубликация ключевиков убирается выставлением новых списков.
  - Новые фильтры создаются только если по точному имени их ещё нет
    (соответственно, повторный запуск не плодит дубли).

Запуск:
  cd ~/Desktop/tender-ai-bot-fresh
  python -m scripts.seed_new_filters_2026_04         # «боевой» режим
  python -m scripts.seed_new_filters_2026_04 --dry   # только показать план
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

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO
)
log = logging.getLogger("seed_2026_04")

TELEGRAM_ID = 298437198
DEFAULT_LAW_TYPE = None  # оба ФЗ

# ---------------------------------------------------------------------------
# Region presets
# ---------------------------------------------------------------------------

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
# Правки к существующим фильтрам
# ---------------------------------------------------------------------------

# Каждое правило ищет фильтры по `name_like` и:
#  - удаляет все ключевики из remove_kw / remove_kw_substr;
#  - добавляет add_exclude к exclude_keywords (без дублей);
#  - если задан replace_keywords — полностью заменяет keywords;
#  - если задан replace_excludes — полностью заменяет exclude_keywords.
EDITS = [
    {
        "label": "edit#1: бумажный фильтр — убрать общие 'канцелярский / канцтовары'",
        "name_like": "%бумаг%",
        "remove_kw_substr": [
            "канцелярский",
            "канцтовары",
            "канцелярские принадлежности",
        ],
        "add_exclude": [],
    },
    {
        "label": "edit#2: оргтехника — добавить минус-слова про расходники",
        "name_equals": "Орг. техника 02.02.2026",
        "remove_kw_substr": [],
        "add_exclude": [
            "картридж",
            "тонер",
            "расходные материалы",
            "тонер-картридж",
            "фотобарабан",
        ],
    },
    {
        "label": "edit#3a: 'Электроника' — оставить 'монитор' тут, не дублировать в IT-периферии",
        "name_like": "%электроника%",
        "remove_kw_substr": [],
        "add_exclude": [],  # ничего не делаем; монитор остаётся
    },
    {
        "label": "edit#3b: 'Дом и сад' — газонокосилки/триммеры/культиваторы остаются здесь",
        "name_like": "%дом и сад%",
        "remove_kw_substr": [],
        "add_exclude": [],  # ничего не делаем; они остаются
    },
    {
        "label": "edit#4: СИЗ — переписать ключевики на узкозащитные",
        "name_like": "%СИЗ%",
        "replace_keywords": [
            "каска защитная",
            "каска строительная",
            "очки защитные",
            "респиратор",
            "противогаз",
            "защитные перчатки",
            "перчатки нитриловые",
            "перчатки спилковые",
            "сигнальный жилет",
            "спецобувь защитная",
            "страховочная привязь",
            "диэлектрические боты",
            "защитный костюм",
            "противошумные наушники",
            "костюм Л-1",
            "фильтр для респиратора",
            "средства индивидуальной защиты",
        ],
        "replace_excludes": [
            "форменная",
            "медицинская",
            "поварская",
            "бытовая",
            "латексная",
            "виниловая",
            "услуги",
        ],
    },
]


# ---------------------------------------------------------------------------
# 11 новых фильтров
# ---------------------------------------------------------------------------

def build_new_filters(default_regions: list[str]) -> list[dict]:
    """Возвращает список фильтров с подставленными regions per preset."""

    wide = all_regions()
    default_narrow = DEFAULT_NARROW_REGIONS

    return [
        {
            "name": "Канцтовары не-бумажные",
            "keywords": [
                "ручка шариковая", "ручка гелевая", "ручка автоматическая",
                "маркер", "текстовыделитель",
                "папка-регистратор", "папка-скоросшиватель", "файл-вкладыш",
                "скрепка", "степлер", "скобы для степлера", "антистеплер",
                "скотч канцелярский", "клей канцелярский", "клей-карандаш",
                "корректор", "штрих-корректор",
                "ластик", "карандаш чернографитный", "точилка",
                "ножницы канцелярские", "дырокол",
                "лоток для бумаг", "блокнот", "тетрадь общая", "ежедневник",
                "кнопки канцелярские", "разделитель листов",
            ],
            "exclude_keywords": [
                "бумага", "услуги", "монтаж", "установка", "ремонт", "заправка",
            ],
            "price_min": 100_000, "price_max": 2_000_000,
            "regions": default_regions,
        },
        {
            "name": "Расходники для оргтехники",
            "keywords": [
                "картридж лазерный", "картридж струйный", "тонер-картридж",
                "тонер", "фотобарабан", "фотовал",
                "термоплёнка", "чернила для принтера", "чернила для МФУ",
                "риббон", "лента красящая", "чип картриджа",
                "девелопер", "фьюзер", "ракель", "печатающая головка",
                "ролик подачи бумаги",
                "расходные материалы для печати", "расходники для оргтехники",
                "картриджи к МФУ",
            ],
            "exclude_keywords": [
                "услуги", "заправка картриджей", "восстановление картриджей",
                "ремонт", "техническое обслуживание", "монтаж",
            ],
            "price_min": 100_000, "price_max": 3_000_000,
            "regions": default_regions,
        },
        {
            "name": "Хозтовары и моющие средства",
            "keywords": [
                "моющее средство", "чистящее средство",
                "средство для уборки", "средство для пола",
                "средство для стёкол", "средство для сантехники",
                "мыло жидкое", "мыло туалетное",
                "средство дезинфицирующее", "антисептик для рук",
                "перчатки бытовые", "перчатки латексные", "перчатки виниловые",
                "ведро пластиковое", "швабра", "моп", "тряпка для уборки",
                "салфетка для уборки", "губка хозяйственная",
                "бумажное полотенце", "туалетная бумага в рулонах",
                "мешки для мусора", "освежитель воздуха",
                "дозатор для мыла", "диспенсер для бумажных полотенец",
                "инвентарь уборочный", "хозяйственный инвентарь",
            ],
            "exclude_keywords": [
                "услуги клининга", "уборка помещений", "дезинфекция помещений",
                "стирка", "химчистка", "ремонт", "монтаж",
            ],
            "price_min": 100_000, "price_max": 1_500_000,
            "regions": default_regions,
        },
        {
            "name": "Офисная мебель эконом-сегмента",
            "keywords": [
                "стол письменный", "стол офисный", "стол компьютерный",
                "стол рабочий", "стол ученический",
                "стул офисный", "стул для посетителей", "стул ученический",
                "кресло офисное", "кресло руководителя", "кресло компьютерное",
                "шкаф для документов", "шкаф для одежды", "шкаф архивный",
                "тумба офисная", "тумба приставная", "тумба под оргтехнику",
                "стеллаж металлический", "стеллаж офисный",
                "мебель для кабинета", "мебель для офиса",
                "мебель ученическая", "парта школьная", "доска школьная",
                "мебель для учебного класса",
            ],
            "exclude_keywords": [
                "мягкая мебель", "диван", "кровать",
                "кухонная мебель", "бытовая мебель",
                "услуги сборки", "монтаж мебели", "реставрация", "ремонт мебели",
            ],
            "price_min": 300_000, "price_max": 5_000_000,
            "regions": default_narrow,  # доставка габарита
        },
        {
            "name": "Спецодежда и рабочая обувь (форменная)",
            "keywords": [
                "спецодежда", "рабочая одежда", "форменная одежда",
                "форма корпоративная",
                "костюм рабочий", "костюм летний рабочий", "костюм зимний рабочий",
                "куртка рабочая", "брюки рабочие",
                "халат медицинский", "халат рабочий", "халат лабораторный",
                "медицинская одежда",
                "поварская форма", "китель повара", "куртка повара", "фартук",
                "обувь рабочая", "ботинки рабочие", "ботинки кожаные",
                "сапоги резиновые",
                "головной убор форменный", "униформа", "рубашка форменная",
            ],
            "exclude_keywords": [
                "услуги пошива", "индивидуальный пошив",
                "чистка одежды", "стирка", "ремонт одежды",
            ],
            "price_min": 200_000, "price_max": 3_000_000,
            "regions": default_regions,
        },
        {
            "name": "Электротовары и светотехника",
            "keywords": [
                "светильник светодиодный", "светильник потолочный",
                "светильник встраиваемый", "светильник уличный",
                "лампа светодиодная", "LED-лампа",
                "лампа люминесцентная", "лампа накаливания",
                "прожектор светодиодный", "светодиодная панель",
                "кабель силовой", "кабель ВВГ", "кабель NYM",
                "провод электрический", "удлинитель электрический",
                "розетка", "выключатель",
                "автоматический выключатель", "УЗО", "дифференциальный автомат",
                "щит электрический", "стабилизатор напряжения",
                "источник бесперебойного питания промышленный",
                "контактор", "реле",
            ],
            "exclude_keywords": [
                "услуги электромонтажа", "электромонтажные работы",
                "проектирование", "ремонт", "замена освещения",
                "техобслуживание электросетей", "прокладка кабельных линий",
            ],
            "price_min": 200_000, "price_max": 3_000_000,
            "regions": default_regions,
        },
        {
            "name": "Медицинские расходники бытового сегмента",
            "keywords": [
                "тонометр автоматический", "тонометр механический",
                "термометр медицинский", "термометр электронный",
                "термометр инфракрасный бесконтактный",
                "дозатор для антисептика", "диспенсер для дезинфектанта",
                "аптечка первой помощи", "аптечка офисная", "аптечка автомобильная",
                "перчатки медицинские нитриловые",
                "маска медицинская одноразовая",
                "бинт стерильный", "бинт нестерильный",
                "лейкопластырь", "антисептик кожный",
                "бахилы медицинские", "шапочка медицинская одноразовая",
                "пульсоксиметр", "ингалятор небулайзер", "грелка медицинская",
            ],
            "exclude_keywords": [
                "услуги", "техническое обслуживание", "поверка", "калибровка",
                "ремонт медицинского оборудования", "монтаж",
            ],
            "price_min": 100_000, "price_max": 2_000_000,
            "regions": default_regions,
        },
        {
            "name": "Сувенирная и наградная продукция",
            "keywords": [
                "кубок наградной", "кубок спортивный",
                "медаль наградная", "медаль памятная",
                "грамота", "благодарность", "диплом",
                "значок наградной", "плакетка наградная",
                "подарок корпоративный", "подарочный набор",
                "подарок к юбилею", "подарок ветерану",
                "подарок учителю", "подарок выпускнику",
                "новогодний подарок детский",
                "сувенир", "сувенирная продукция",
                "нанесение логотипа",
                "флешка с логотипом", "ежедневник с логотипом",
                "ручка с логотипом", "кружка с нанесением",
                "рамка для грамот",
                "изготовление наградной продукции", "изготовление кубков",
            ],
            # ВНИМАНИЕ: 'услуги' НЕ исключаем (см. notes в спеке).
            "exclude_keywords": [
                "ремонт", "чистка наград", "реставрация",
            ],
            "price_min": 100_000, "price_max": 800_000,
            "regions": wide,
        },
        {
            "name": "Книги, учебная литература, наглядные пособия",
            "keywords": [
                "учебник", "учебное пособие", "рабочая тетрадь",
                "методическое пособие", "школьный учебник",
                "учебная литература", "художественная литература",
                "литература для библиотеки", "книги для библиотеки",
                "фонд библиотеки",
                "наглядное пособие", "плакат учебный",
                "демонстрационный материал", "раздаточный материал",
                "дидактический материал",
                "географическая карта", "глобус", "таблица учебная",
                "стенд информационный",
                "микроскоп учебный", "набор для опытов",
                "лабораторный набор школьный", "химический набор учебный",
            ],
            "exclude_keywords": [
                "услуги издания", "типографские услуги",
                "разработка учебного материала",
                "подписка на электронные ресурсы", "электронная библиотека",
            ],
            "price_min": 200_000, "price_max": 3_000_000,
            "regions": wide,
        },
        {
            "name": "Семена, удобрения, садовый инвентарь",
            "keywords": [
                "семена цветов", "семена газонной травы",
                "газонная травосмесь",
                "рассада однолетних", "рассада многолетних",
                "удобрение минеральное", "удобрение комплексное",
                "удобрение азотное",
                "грунт растительный", "торф фрезерный",
                "песок строительный для озеленения",
                "инвентарь садовый",
                "лопата штыковая", "лопата совковая", "грабли садовые",
                "секатор", "садовые ножницы",
                "шланг поливочный", "лейка", "опрыскиватель садовый",
                # Газонокосилки/триммеры/культиваторы — оставлены в 'Дом и сад'
                # (см. правка #3b). Тут НЕ дублируем.
            ],
            "exclude_keywords": [
                "услуги озеленения", "благоустройство территории",
                "посадка зелёных насаждений", "стрижка газона",
                "уход за насаждениями", "обрезка деревьев",
            ],
            "price_min": 100_000, "price_max": 1_500_000,
            "regions": default_regions,
        },
        {
            "name": "IT-периферия и мелкие комплектующие",
            "keywords": [
                "мышь компьютерная", "клавиатура компьютерная",
                "комплект клавиатура и мышь",
                "веб-камера", "гарнитура компьютерная", "наушники с микрофоном",
                "кабель HDMI", "кабель Ethernet", "кабель UTP", "патч-корд",
                "USB-флешка", "флеш-накопитель",
                "внешний жёсткий диск", "внешний SSD",
                "источник бесперебойного питания для ПК", "ИБП до 1500 ВА",
                "коврик для мыши", "USB-хаб", "картридер",
                "удлинитель USB", "переходник USB",
                "кабель питания компьютера",
                "акустическая система компьютерная",
                # Мониторы — оставлены в 'Электроника' (см. правка #3a).
            ],
            "exclude_keywords": [
                "услуги", "ремонт компьютерной техники",
                "техническое обслуживание ПК", "монтаж сетей",
                "прокладка структурированной кабельной системы",
                "настройка рабочих мест", "сопровождение",
            ],
            "price_min": 100_000, "price_max": 2_000_000,
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


def _kw_matches_substr(kw: str, substrs: list[str]) -> bool:
    low = kw.lower()
    return any(s.lower() in low for s in substrs)


def _name_matches_like(name: str | None, pattern: str) -> bool:
    """Простая SQL LIKE-подобная проверка: '%a%b%' → name содержит 'a', потом 'b'."""
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
    """Берём regions у первого подходящего «бумажного» фильтра, иначе fallback."""
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
        "default_regions: 'бумажный' фильтр не найден, использую fallback (ЦФО+СЗФО+ЮФО+ПФО, %d шт.)",
        len(fallback),
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
            log.error("user telegram_id=%s не найден — прекращаю", TELEGRAM_ID)
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
        log.info("у пользователя сейчас %d активных фильтров (без soft-deleted)", len(all_filters))

        # ----- Шаг 1: правки существующих фильтров -----
        edits_applied = 0
        for rule in EDITS:
            label = rule["label"]
            if "name_equals" in rule:
                target = rule["name_equals"]
                matched = [f for f in all_filters if f.name == target]
                pattern_descr = f"={target!r}"
            else:
                matched = [
                    f for f in all_filters
                    if _name_matches_like(f.name, rule["name_like"])
                ]
                pattern_descr = rule["name_like"]
            if not matched:
                log.warning("[skip] %s — фильтры по '%s' не найдены", label, pattern_descr)
                continue

            for f in matched:
                changed = False
                # remove keywords by substring
                if rule.get("remove_kw_substr"):
                    before = list(f.keywords or [])
                    after = [
                        kw for kw in before
                        if not _kw_matches_substr(kw, rule["remove_kw_substr"])
                    ]
                    if after != before:
                        f.keywords = after
                        flag_modified(f, "keywords")
                        changed = True
                        log.info(
                            "  [%s] keywords: %d → %d (убрано: %s)",
                            f.name, len(before), len(after),
                            [kw for kw in before if kw not in after],
                        )

                # add to excludes
                if rule.get("add_exclude"):
                    before = list(f.exclude_keywords or [])
                    after = _dedupe_preserve_order(before + rule["add_exclude"])
                    if after != before:
                        f.exclude_keywords = after
                        flag_modified(f, "exclude_keywords")
                        changed = True
                        log.info(
                            "  [%s] exclude_keywords: +%s",
                            f.name,
                            [x for x in after if x not in before],
                        )

                # full replace keywords
                if "replace_keywords" in rule:
                    before = list(f.keywords or [])
                    after = list(rule["replace_keywords"])
                    if after != before:
                        f.keywords = after
                        flag_modified(f, "keywords")
                        changed = True
                        log.info(
                            "  [%s] keywords переписаны: %d → %d",
                            f.name, len(before), len(after),
                        )

                # full replace excludes
                if "replace_excludes" in rule:
                    before = list(f.exclude_keywords or [])
                    after = list(rule["replace_excludes"])
                    if after != before:
                        f.exclude_keywords = after
                        flag_modified(f, "exclude_keywords")
                        changed = True
                        log.info(
                            "  [%s] exclude_keywords переписаны: %d → %d",
                            f.name, len(before), len(after),
                        )

                if changed:
                    edits_applied += 1
                    log.info("  ✓ %s — обновлён", f.name)
                else:
                    log.info("  · %s — уже актуален, ничего не меняем", f.name)

        log.info("правок применено: %d", edits_applied)

        # ----- Шаг 2: создание новых фильтров -----
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
                    # notify_chat_ids НЕ задаём — null = только в личку владельца
                )
                session.add(obj)
            log.info(
                "  + '%s' (kw=%d, excl=%d, регионов=%d, цена=%s..%s)",
                spec["name"],
                len(spec["keywords"]),
                len(spec["exclude_keywords"]),
                len(spec["regions"]),
                spec["price_min"], spec["price_max"],
            )
            created += 1

        if dry:
            log.info("DRY: ничего не сохранено. Будет создано: %d, пропущено: %d", created, skipped)
            await session.rollback()
            return

        await session.commit()
        log.info(
            "OK: edits=%d, created=%d, skipped=%d, всего фильтров было=%d",
            edits_applied, created, skipped, len(all_filters),
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true", help="dry-run без записи в БД")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(dry=args.dry))
