"""
Бэкфилл company_id для фильтров и уведомлений, созданных в ботах.

Компания раньше заводилась только при первом заходе в веб-кабинет.
Пользователь, живущий в боте, компании не имел, поэтому его фильтры
сохранялись с company_id=NULL, а кабинет выбирает строго по компании —
и такие фильтры вместе со всеми уведомлениями по ним в кабинете
не показывались вовсе.

Само появление новых «осиротевших» строк закрыто в
sqlalchemy_adapter.create_filter (создаёт компанию, если её нет).
Этот скрипт разбирает то, что накопилось раньше.

Идемпотентный: повторный прогон ничего не меняет.

Запуск:
  python -m scripts.backfill_company_id_2026_09 --dry   # только план
  python -m scripts.backfill_company_id_2026_09         # боевой прогон
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

from sqlalchemy import select, update

from database import (
    DatabaseSession, SniperUser, SniperFilter, SniperNotification,
    Company, CompanyMember,
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("backfill_company_id")


async def main(dry: bool = False) -> None:
    created_companies = 0
    fixed_filters = 0
    fixed_notifications = 0

    async with DatabaseSession() as session:
        # Пользователи, у которых есть осиротевшие фильтры или уведомления.
        # Группы (is_group) пропускаем: у них своя маршрутизация, компания
        # им не нужна.
        user_ids = set((await session.scalars(
            select(SniperFilter.user_id).where(SniperFilter.company_id.is_(None))
        )).all())
        user_ids |= set((await session.scalars(
            select(SniperNotification.user_id).where(SniperNotification.company_id.is_(None))
        )).all())

        log.info("пользователей с осиротевшими данными: %d", len(user_ids))

        for user_id in sorted(user_ids):
            user = await session.get(SniperUser, user_id)
            if not user or user.is_group:
                continue

            membership = await session.scalar(
                select(CompanyMember)
                .where(CompanyMember.user_id == user_id)
                .order_by(CompanyMember.joined_at)
                .limit(1)
            )
            company_id = membership.company_id if membership else None

            if company_id is None:
                name_base = user.first_name or f'User {user_id}'
                log.info("  [компания] user %s (%s) -> создать «Команда %s»",
                         user_id, user.username or '—', name_base)
                created_companies += 1
                if not dry:
                    company = Company(name=f'Команда {name_base}', owner_user_id=user_id)
                    session.add(company)
                    await session.flush()
                    session.add(CompanyMember(company_id=company.id,
                                              user_id=user_id, role='owner'))
                    company_id = company.id

            # В dry-run компания не создана, но считать её данные всё равно
            # надо — иначе план занижает объём работ и вводит в заблуждение.
            company_label = company_id if company_id is not None else '(новая)'

            filters_to_fix = (await session.scalars(
                select(SniperFilter.id).where(
                    SniperFilter.user_id == user_id,
                    SniperFilter.company_id.is_(None),
                )
            )).all()
            if filters_to_fix:
                log.info("  [фильтры] user %s: %d шт -> company %s",
                         user_id, len(filters_to_fix), company_label)
                fixed_filters += len(filters_to_fix)
                if not dry:
                    await session.execute(
                        update(SniperFilter)
                        .where(SniperFilter.id.in_(filters_to_fix))
                        .values(company_id=company_id)
                    )

            orphan_notifs = (await session.execute(
                select(SniperNotification.id, SniperNotification.tender_number).where(
                    SniperNotification.user_id == user_id,
                    SniperNotification.company_id.is_(None),
                )
            )).all()

            # Часть осиротевших уведомлений — исторические дубликаты: тот же
            # тендер уже есть с проставленной компанией. Раньше дедуп их не
            # ловил как раз потому, что company_id был пустой, а уникальность
            # идёт по (user_id, company_id, tender_number). Проставить им
            # компанию нельзя — упрёмся в это же ограничение. Удалять боевые
            # строки ради бэкфилла не будем, просто пропустим.
            notifs_to_fix, skipped = [], 0
            for notif_id, tender_number in orphan_notifs:
                if company_id is not None:
                    clash = await session.scalar(
                        select(SniperNotification.id).where(
                            SniperNotification.user_id == user_id,
                            SniperNotification.company_id == company_id,
                            SniperNotification.tender_number == tender_number,
                        ).limit(1)
                    )
                    if clash:
                        skipped += 1
                        continue
                notifs_to_fix.append(notif_id)

            if skipped:
                log.info("  [уведомления] user %s: пропущено %d — такой тендер "
                         "уже есть у компании %s", user_id, skipped, company_label)
            if notifs_to_fix:
                log.info("  [уведомления] user %s: %d шт -> company %s",
                         user_id, len(notifs_to_fix), company_label)
                fixed_notifications += len(notifs_to_fix)
                if not dry:
                    await session.execute(
                        update(SniperNotification)
                        .where(SniperNotification.id.in_(notifs_to_fix))
                        .values(company_id=company_id)
                    )

        if not dry:
            await session.commit()

    log.info("%s компаний создано %d, фильтров исправлено %d, уведомлений %d",
             "DRY, не записано —" if dry else "записано:",
             created_companies, fixed_filters, fixed_notifications)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true", help="только показать план")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(dry=parse_args().dry))
