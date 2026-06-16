"""
Сидер: новый фильтр «Мойка и уборка авто (Москва и МО)» (1 фильтр).

Это фильтр на УСЛУГИ (не на товар), поэтому «услуги» НЕ исключаем.
Охват: мойка авто + химчистка салона/детейлинг.
Исключаем: ремонт/ТО/шиномонтаж и поставку оборудования/автохимии.
Регионы: Москва + Московская область. Без нижней границы цены.

Запуск:
  cd ~/Desktop/tender-ai-bot-fresh
  python -m scripts.seed_filter_carwash_msk_2026_06          # боевой
  python -m scripts.seed_filter_carwash_msk_2026_06 --dry    # только план
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
log = logging.getLogger("seed_carwash_2026_06")

TELEGRAM_ID = 298437198
DEFAULT_LAW_TYPE = None

REGIONS = ["Москва", "Московская область"]


def build_filter() -> dict:
    return {
        "name": "Мойка и уборка авто (Москва и МО)",
        "keywords": [
            # --- Мойка авто (услуги) ---
            "услуги мойки автомобилей", "мойка автомобилей",
            "мойка автотранспорта", "мойка служебного автотранспорта",
            "мойка служебных автомобилей", "мойка транспортных средств",
            "услуги автомойки", "мойка автомобиля",
            "мойка автопарка", "мойка автобусов",
            "мойка грузовых автомобилей", "мойка спецтехники",
            "наружная мойка автомобилей", "бесконтактная мойка",
            "комплексная мойка автомобилей", "помывка автомобилей",
            # --- Химчистка салона / детейлинг ---
            "химчистка салона автомобиля", "химчистка салона",
            "уборка салона автомобиля", "чистка салона автомобиля",
            "комплексная уборка автомобиля", "уборка автомобилей",
            "детейлинг", "полировка кузова",
            "предпродажная подготовка автомобиля",
        ],
        "exclude_keywords": [
            # ремонт / ТО / смежное обслуживание
            "ремонт автомобилей", "ремонт транспортных средств",
            "техническое обслуживание автомобилей",
            "техническое обслуживание транспортных средств",
            "шиномонтаж", "заправка", "ГСМ", "топливо",
            "автозапчасти", "запасные части", "диагностика автомобиля",
            "эвакуация", "аренда автомобилей", "аренда транспортных средств",
            # поставка товара/оборудования вместо услуги
            "поставка оборудования", "оборудование для автомойки",
            "моечное оборудование", "монтаж оборудования",
            "автохимия", "моющее средство", "поставка автохимии",
            "строительство автомойки",
            # другая «мойка/уборка» (не авто)
            "мойка окон", "мойка фасадов", "уборка помещений",
            "уборка территории", "уборка снега", "вывоз мусора",
        ],
        "price_min": 0,
        "price_max": 20_000_000,
        "regions": REGIONS,
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

        spec = build_filter()

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
