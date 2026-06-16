"""
Сидер: новый фильтр «МРТ/КТ и лучевая диагностика» (1 фильтр).

Охват: томографы МРТ/КТ + расходники/комплектующие + смежная лучевая
диагностика (рентген, ангиографы, маммографы, С-дуги, ПЭТ/КТ).
Сервис/монтаж/аренда — в исключениях (нужна поставка техники, не услуги).
Регионы: вся Россия. Цена: 100к..300млн ₽ (расходники → томографы).

Запуск:
  cd ~/Desktop/tender-ai-bot-fresh
  python -m scripts.seed_filter_mri_ct_2026_06          # боевой
  python -m scripts.seed_filter_mri_ct_2026_06 --dry    # только план
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
from tender_sniper.regions import FEDERAL_DISTRICTS

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("seed_mri_ct_2026_06")

TELEGRAM_ID = 298437198
DEFAULT_LAW_TYPE = None


def all_regions() -> list[str]:
    out: list[str] = []
    for d in FEDERAL_DISTRICTS.values():
        out.extend(d["regions"])
    return out


def build_filter(regions: list[str]) -> dict:
    return {
        "name": "МРТ/КТ и лучевая диагностика",
        "keywords": [
            # --- Томографы МРТ ---
            "магнитно-резонансный томограф", "томограф магнитно-резонансный",
            "аппарат МРТ", "МРТ-аппарат", "система МРТ",
            "магнитно-резонансная томография",
            # --- Томографы КТ ---
            "компьютерный томограф", "томограф компьютерный",
            "аппарат КТ", "КТ-аппарат", "система КТ",
            "мультиспиральный компьютерный томограф", "МСКТ",
            "спиральный компьютерный томограф",
            "компьютерная томография",
            # --- ПЭТ/КТ ---
            "ПЭТ/КТ", "ПЭТ-КТ", "позитронно-эмиссионный томограф",
            "однофотонный эмиссионный томограф", "ОФЭКТ",
            # --- Смежная лучевая диагностика ---
            "рентгеновский аппарат", "рентген-аппарат",
            "рентгенодиагностический комплекс", "рентгенодиагностическая система",
            "рентгеновский комплекс", "флюорограф", "маммограф",
            "ангиограф", "ангиографическая система", "ангиографический комплекс",
            "рентгеновская С-дуга", "С-дуга хирургическая",
            "дентальный томограф", "конусно-лучевой томограф", "КЛКТ",
            "рентгеновский денситометр", "костный денситометр",
            # --- Расходники и комплектующие ---
            "радиочастотная катушка", "катушка для МРТ", "РЧ-катушка",
            "инжектор контрастного вещества", "автоматический инжектор",
            "шприц-инжектор", "контрастное вещество для МРТ",
            "контрастное вещество для КТ", "гадолинийсодержащий контраст",
            "йодсодержащее контрастное вещество",
            "гелий жидкий", "криоген для МРТ",
            "рентгеновская трубка", "рентгеновский излучатель",
            "плоскопанельный детектор", "рентгеновский детектор",
            "высоковольтный генератор рентгеновский", "рентгеновский генератор",
            "коллиматор рентгеновский",
            "ЗИП для томографа", "запчасти для томографа",
        ],
        "exclude_keywords": [
            "услуги", "техническое обслуживание", "сервисное обслуживание",
            "ремонт оборудования", "монтаж", "пусконаладочные работы",
            "демонтаж", "аренда", "прокат", "лизинг",
            "проектирование", "поверка", "метрологическая аттестация",
            "дозиметрический контроль", "обучение персонала",
            "расходные материалы для печати",
        ],
        "price_min": 100_000,
        "price_max": 300_000_000,
        "regions": regions,
    }


async def main(dry: bool = False) -> None:
    async with DatabaseSession() as session:
        user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == TELEGRAM_ID)
        )
        if not user:
            log.error("user telegram_id=%s не найден", TELEGRAM_ID)
            return

        log.info("user: id=%s username=%s tier=%s", user.id, user.username, user.subscription_tier)

        existing_names = {
            f.name
            for f in (
                await session.scalars(
                    select(SniperFilter).where(
                        SniperFilter.user_id == user.id,
                        SniperFilter.deleted_at.is_(None),
                    )
                )
            ).all()
        }

        regions = all_regions()
        spec = build_filter(regions)

        if spec["name"] in existing_names:
            log.info("[skip] '%s' — уже существует", spec["name"])
            return

        log.info(
            "  + '%s' (kw=%d, excl=%d, регионов=%d, цена=%s..%s)",
            spec["name"], len(spec["keywords"]), len(spec["exclude_keywords"]),
            len(spec["regions"]), spec["price_min"], spec["price_max"],
        )

        if dry:
            log.info("DRY: created=1")
            return

        session.add(
            SniperFilter(
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
        )
        await session.commit()
        log.info("OK: создан фильтр '%s'", spec["name"])


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true", help="dry-run без записи в БД")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(dry=args.dry))
