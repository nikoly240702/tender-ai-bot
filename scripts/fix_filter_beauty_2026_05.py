"""Точечный фикс фильтра «Красота и здоровье» (id=101):

Проблема: одиночный ключевик «фен» матчится в названиях медицинских
тендеров (где «фен» — сокращение действующего вещества или часть
списка препаратов через запятую). Word-boundary матч это не спасает.

Решение:
- Заменить «фен» → набор более конкретных фраз («фен для волос»,
  «фен бытовой», «фен электрический», «фен профессиональный»).
- Добавить в excludes медицинский black-list, чтобы перестраховаться
  если «фен для…» каким-то образом окажется в тендере про лекарства.

Идемпотентно: повторный запуск не задублирует ключи.
"""
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

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("fix_beauty")

TELEGRAM_ID = 298437198

NEW_FEN_KEYWORDS = [
    "фен для волос",
    "фен бытовой",
    "фен электрический",
    "фен профессиональный",
    "фен-щётка",
]

ADDITIONAL_EXCLUDES = [
    "препарат", "лекарственный", "лекарственные средства",
    "медикамент", "таблетк", "капсул", "ампул",
    "раствор для инъекций", "субстанция", "действующее вещество",
    "натрия", "калия", "кальция",  # часто в названиях фарм-тендеров
    "фенобарбитал", "фенацетин", "фенил",
]


def _dedupe(items):
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


async def main():
    async with DatabaseSession() as s:
        u = await s.scalar(select(SniperUser).where(SniperUser.telegram_id == TELEGRAM_ID))
        if not u:
            log.error("user not found"); return
        f = await s.scalar(
            select(SniperFilter).where(
                SniperFilter.user_id == u.id,
                SniperFilter.name == "Красота и здоровье",
                SniperFilter.deleted_at.is_(None),
            )
        )
        if not f:
            log.error("фильтр 'Красота и здоровье' не найден")
            return

        log.info("до: kw=%s", f.keywords)
        log.info("до: excl=%s", f.exclude_keywords)

        kws = list(f.keywords or [])
        if "фен" in kws:
            kws.remove("фен")
            log.info("удалил одиночное 'фен'")
        kws = _dedupe(kws + NEW_FEN_KEYWORDS)

        excludes = list(f.exclude_keywords or [])
        excludes = _dedupe(excludes + ADDITIONAL_EXCLUDES)

        if kws != f.keywords:
            f.keywords = kws
            flag_modified(f, "keywords")
        if excludes != f.exclude_keywords:
            f.exclude_keywords = excludes
            flag_modified(f, "exclude_keywords")

        await s.commit()
        log.info("после: kw=%s", f.keywords)
        log.info("после: excl=%s", f.exclude_keywords)
        log.info("✓ применено")


if __name__ == "__main__":
    asyncio.run(main())
