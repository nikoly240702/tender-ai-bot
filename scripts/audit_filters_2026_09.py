"""
Ревизия фильтров владельца (сентябрь 2026) по итогам разбора потока уведомлений.

Что делает (всё идемпотентно, можно гонять повторно):

1. Отключает категории, где нет шансов на победу (решение владельца):
   компьютерная техника, картриджи/тонеры, принтеры/МФУ/сканеры.
2. Убирает перчатки из фильтров, где они ловились попутно.
3. Схлопывает дубли: в паре «богатый фильтр + его обеднённая копия»
   уникальные слова копии переносятся в основной фильтр, копия гасится.
   Иначе один тендер даёт два уведомления.
4. Расширяет охват бытовой техники (запрос владельца — таких нужно больше).
5. Ставит нижнюю границу цены не ниже MIN_PRICE_FLOOR везде, где её нет
   или где она ниже порога. Уже выставленные более высокие пороги
   (например 300k у бумаги/канцтоваров) не трогает.
6. Заводит новый фильтр на студенческие билеты, зачётки и обложки
   для дипломов — эту нишу владелец назвал приоритетной.

Запуск:
  cd ~/Desktop/tender-ai-bot-fresh
  python -m scripts.audit_filters_2026_09 --dry    # только план, без записи
  python -m scripts.audit_filters_2026_09          # боевой прогон
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

from database import DatabaseSession, SniperFilter, SniperUser

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("audit_filters_2026_09")

TELEGRAM_ID = 298437198

# Нижняя граница цены: отсекает совсем мелкие закупки, где возиться незачем.
MIN_PRICE_FLOOR = 30_000

# --- 1. Категории без шансов на победу -> выключить целиком ---
DEACTIVATE = {
    "Оргтехника: принтеры, МФУ, сканеры": "принтеры/МФУ — нет шансов на победу",
    "Расходные материалы для печати": "картриджи/тонеры — нет шансов на победу",
    "Компьютерная периферия и аксессуары": "компьютерная техника — нет шансов на победу",
}

# --- 2. Перчатки: выкинуть из ключевых слов и занести в исключения ---
GLOVE_WORDS = ["перчатки", "перчатк", "перчатки нитриловые", "перчатки латексные",
               "перчатки медицинские", "перчатки хозяйственные", "перчатки рабочие"]
GLOVE_EXCLUDES = ["перчатки", "перчаток", "перчатками"]
DROP_GLOVES_FROM = [
    "Разное 16.02",
    "СИЗ и средства безопасности труда",
    "Хозтовары, уборочный инвентарь, бытовая химия",
]

# --- 3. Компьютеры выкинуть из «Электроники» (сам фильтр нужен) ---
COMPUTER_WORDS = ["компьютер", "компьютеры", "ноутбук", "ноутбуки", "моноблок",
                  "моноблоки", "системный блок", "системные блоки", "процессор",
                  "материнская плата", "видеокарта", "монитор", "мониторы"]
COMPUTER_EXCLUDES = ["компьютер", "ноутбук", "моноблок", "системный блок",
                     "картридж", "тонер", "мфу", "принтер"]
DROP_COMPUTERS_FROM = ["Электроника"]

# --- 4. Дубли: (основной, копия). Уникальное из копии переносим в основной. ---
DUPLICATE_PAIRS = [
    ("Электроника", "Электроника"),                                    # 99 <- 249
    ("Кухонная техника", "Кухонная техника"),                          # 98 <- 248
    ("Техника для дома и уход за домом", "Техника для дома"),          # 100 <- 250
    ("Крупная и встраиваемая бытовая техника", "Крупная бытовая техника"),  # 97 <- 247
    ("Электроинструмент и аккумуляторный инструмент", "Электроинструмент"),  # 103 <- 253
    ("Красота и здоровье", "Красота и здоровье"),                      # 101 <- 251
    ("Садовая техника и инвентарь", "Дом и сад"),                      # 102 <- 252
]

# --- 5. Расширение охвата бытовой техники ---
APPLIANCE_EXTRA = {
    "Крупная и встраиваемая бытовая техника": [
        "холодильник", "холодильный шкаф", "морозильная камера", "морозильный ларь",
        "стиральная машина", "сушильная машина", "посудомоечная машина",
        "варочная панель", "духовой шкаф", "электрическая плита", "газовая плита",
        "вытяжка кухонная", "встраиваемая техника", "винный шкаф",
    ],
    "Кухонная техника": [
        "микроволновая печь", "электрочайник", "чайник электрический",
        "кофемашина", "кофеварка", "мультиварка", "пароварка", "блендер",
        "миксер", "мясорубка электрическая", "тостер", "соковыжималка",
        "кухонный комбайн", "электрическая мясорубка", "термопот",
        "печь конвекционная", "фритюрница",
    ],
    "Техника для дома и уход за домом": [
        "пылесос", "пылесос моющий", "робот-пылесос", "утюг", "гладильная система",
        "парогенератор", "отпариватель", "увлажнитель воздуха", "очиститель воздуха",
        "вентилятор напольный", "обогреватель", "конвектор", "тепловентилятор",
        "швейная машина", "водонагреватель накопительный",
    ],
}

# --- 6. Новый фильтр: студенческая полиграфия ---
NEW_FILTER = {
    "name": "Студенческие билеты, зачётки, обложки для дипломов",
    "keywords": [
        "студенческий билет", "студенческие билеты", "бланк студенческого билета",
        "зачетная книжка", "зачётная книжка", "зачетные книжки", "зачётные книжки",
        "бланк зачетной книжки", "обложка для диплома", "обложки для дипломов",
        "папка для диплома", "папки для дипломов", "твердая обложка диплома",
        "диплом бланк", "бланк диплома", "бланки дипломов",
        "аттестат бланк", "бланк аттестата", "бланки аттестатов",
        "приложение к диплому", "вкладыш к диплому",
        "обложка для аттестата", "обложки для аттестатов",
        "дипломная папка", "папка адресная",
        "бланк свидетельства", "бланки свидетельств",
        "удостоверение бланк", "бланк удостоверения",
        "защищенная полиграфическая продукция",
        "полиграфическая продукция защищенная",
        "бланки строгой отчетности",
        "изготовление бланков", "печать бланков",
        "студенческий билет изготовление",
    ],
    "exclude_keywords": [
        # чтобы не ловить «дипломы» как награды и мероприятия
        "грамота", "медаль", "кубок", "награда", "сувенир",
        "диплом победителя", "диплом участника",
        # не образовательные услуги, а именно полиграфия
        "обучение", "повышение квалификации", "образовательные услуги",
        "проживание", "питание",
        # не электронные системы
        "программное обеспечение", "информационная система", "электронный документооборот",
    ],
    "price_min": MIN_PRICE_FLOOR,
    "price_max": 5_000_000,
    "regions": [],
}


def _norm(items):
    """Уникализация без потери порядка (порядок слов в фильтре читаемо важен)."""
    seen, out = set(), []
    for x in items or []:
        key = (x or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(x.strip())
    return out


def _without(items, banned_substrings):
    """Выкидывает слова, содержащие любую из подстрок (регистронезависимо)."""
    banned = [b.lower() for b in banned_substrings]
    return [x for x in (items or []) if not any(b in (x or "").lower() for b in banned)]


async def main(dry: bool = False) -> None:
    changes = []
    # В --dry режиме объекты не мутируются, поэтому «уже отключённые на этом
    # прогоне» надо помнить отдельно — иначе план показывает правки для
    # фильтров, которые сам же и гасит.
    turned_off = set()

    async with DatabaseSession() as session:
        user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == TELEGRAM_ID)
        )
        if not user:
            log.error("user telegram_id=%s не найден", TELEGRAM_ID)
            return

        filters = (await session.scalars(
            select(SniperFilter).where(
                SniperFilter.user_id == user.id,
                SniperFilter.deleted_at.is_(None),
            ).order_by(SniperFilter.id)
        )).all()
        log.info("user id=%s, активных фильтров: %d",
                 user.id, sum(1 for f in filters if f.is_active))

        by_name = {}
        for f in filters:
            by_name.setdefault(f.name, []).append(f)

        # --- 1. Отключаем ненужные категории ---
        for name, reason in DEACTIVATE.items():
            for f in by_name.get(name, []):
                if f.is_active and f.id not in turned_off:
                    changes.append(f"[выкл] #{f.id} «{f.name}» — {reason}")
                    turned_off.add(f.id)
                    if not dry:
                        f.is_active = False

        # --- 2/3. Чистим перчатки и компьютеры ---
        def live(f):
            return f.is_active and f.id not in turned_off

        for name in DROP_GLOVES_FROM:
            for f in by_name.get(name, []):
                if not live(f):
                    continue
                kw = _without(f.keywords, ["перчат"])
                ex = _norm(list(f.exclude_keywords or []) + GLOVE_EXCLUDES)
                if len(kw) != len(f.keywords or []) or len(ex) != len(f.exclude_keywords or []):
                    changes.append(
                        f"[перчатки] #{f.id} «{f.name}»: слов {len(f.keywords or [])}->{len(kw)}, "
                        f"исключений {len(f.exclude_keywords or [])}->{len(ex)}")
                    if not dry:
                        f.keywords, f.exclude_keywords = kw, ex

        for name in DROP_COMPUTERS_FROM:
            for f in by_name.get(name, []):
                if not live(f):
                    continue
                kw = _without(f.keywords, ["компьютер", "ноутбук", "моноблок",
                                           "системный блок", "монитор", "картридж",
                                           "тонер", "мфу", "принтер"])
                ex = _norm(list(f.exclude_keywords or []) + COMPUTER_EXCLUDES)
                if len(kw) != len(f.keywords or []) or len(ex) != len(f.exclude_keywords or []):
                    changes.append(
                        f"[компьютеры] #{f.id} «{f.name}»: слов {len(f.keywords or [])}->{len(kw)}, "
                        f"исключений {len(f.exclude_keywords or [])}->{len(ex)}")
                    if not dry:
                        f.keywords, f.exclude_keywords = kw, ex

        # --- 4. Схлопываем дубли ---
        for main_name, dup_name in DUPLICATE_PAIRS:
            candidates = by_name.get(main_name, []) + (
                by_name.get(dup_name, []) if dup_name != main_name else [])
            candidates = [f for f in candidates if live(f)]
            if len(candidates) < 2:
                continue
            # Основной — тот, у кого больше ключевых слов.
            candidates.sort(key=lambda f: len(f.keywords or []), reverse=True)
            main_f, dups = candidates[0], candidates[1:]
            for dup in dups:
                extra = [k for k in (dup.keywords or [])
                         if k.strip().lower() not in {
                             x.strip().lower() for x in (main_f.keywords or [])}]
                merged = _norm(list(main_f.keywords or []) + extra)
                changes.append(
                    f"[дубль] #{dup.id} «{dup.name}» -> выключен, "
                    f"{len(extra)} уник. слов перенесено в #{main_f.id} «{main_f.name}» "
                    f"({len(main_f.keywords or [])}->{len(merged)})")
                turned_off.add(dup.id)
                if not dry:
                    main_f.keywords = merged
                    dup.is_active = False

        # --- 5. Расширяем бытовую технику ---
        for name, extra_words in APPLIANCE_EXTRA.items():
            for f in by_name.get(name, []):
                if not live(f):
                    continue
                merged = _norm(list(f.keywords or []) + extra_words)
                added = len(merged) - len(f.keywords or [])
                if added:
                    changes.append(
                        f"[бытовая техника] #{f.id} «{f.name}»: +{added} слов "
                        f"({len(f.keywords or [])}->{len(merged)})")
                    if not dry:
                        f.keywords = merged

        # --- 6. Нижняя граница цены ---
        for f in filters:
            if not live(f):
                continue
            current = f.price_min or 0
            if current < MIN_PRICE_FLOOR:
                changes.append(
                    f"[цена] #{f.id} «{f.name}»: min {int(current)} -> {MIN_PRICE_FLOOR}")
                if not dry:
                    f.price_min = MIN_PRICE_FLOOR

        # --- 7. Новый фильтр ---
        if NEW_FILTER["name"] in by_name:
            log.info("[skip] «%s» — уже существует", NEW_FILTER["name"])
        else:
            changes.append(
                f"[новый] «{NEW_FILTER['name']}» "
                f"(kw={len(NEW_FILTER['keywords'])}, excl={len(NEW_FILTER['exclude_keywords'])}, "
                f"цена {NEW_FILTER['price_min']}..{NEW_FILTER['price_max']})")
            if not dry:
                session.add(SniperFilter(
                    user_id=user.id,
                    name=NEW_FILTER["name"],
                    keywords=NEW_FILTER["keywords"],
                    exclude_keywords=NEW_FILTER["exclude_keywords"],
                    price_min=NEW_FILTER["price_min"],
                    price_max=NEW_FILTER["price_max"],
                    regions=NEW_FILTER["regions"],
                    law_type=None,
                    is_active=True,
                ))

        for c in changes:
            log.info("  %s", c)
        log.info("%s изменений: %d", "DRY, не записано —" if dry else "записано", len(changes))

        if not dry:
            await session.commit()
            log.info("OK: изменения сохранены")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true", help="только показать план")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(dry=parse_args().dry))
