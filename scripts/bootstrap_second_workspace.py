"""
Bootstrap второго (изолированного) воркспейса: новая Company + owner
membership + копия активных фильтров текущей компании с новым
notify_chat_ids.

Ничего чувствительного (название компании, chat_id, кто владелец) не
хардкодится — всё аргументами командной строки.

Запуск:
  cd ~/Desktop/tender-ai-bot-fresh
  python -m scripts.bootstrap_second_workspace \
      --owner-telegram-id <telegram_id> \
      --company-name "<name>" \
      --notify-chat-id <chat_id> \
      --dry                                    # план без записи

  # затем без --dry для боевого запуска
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

from database import DatabaseSession, SniperUser, SniperFilter, Company, CompanyMember

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("bootstrap_second_workspace")


async def main(owner_telegram_id: int, company_name: str, notify_chat_id: int, dry: bool = False) -> None:
    async with DatabaseSession() as session:
        owner = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == owner_telegram_id)
        )
        if not owner:
            log.error("owner telegram_id=%s не найден", owner_telegram_id)
            return

        existing = await session.scalar(select(Company).where(Company.name == company_name))
        if existing:
            log.info("[skip] компания '%s' уже существует (id=%s)", company_name, existing.id)
            return

        source_membership = await session.scalar(
            select(CompanyMember)
            .where(CompanyMember.user_id == owner.id)
            .order_by(CompanyMember.joined_at)
            .limit(1)
        )
        if not source_membership:
            log.error("у owner нет ни одной компании — сначала откройте /cabinet")
            return
        source_company_id = source_membership.company_id

        source_filters = (
            await session.scalars(
                select(SniperFilter).where(
                    SniperFilter.company_id == source_company_id,
                    SniperFilter.is_active == True,
                    SniperFilter.deleted_at.is_(None),
                )
            )
        ).all()

        log.info(
            "новая компания '%s', источник company_id=%s, фильтров к копированию=%d",
            company_name, source_company_id, len(source_filters),
        )
        for f in source_filters:
            log.info("  + '%s'", f.name)

        if dry:
            log.info("DRY: компания и %d фильтр(ов) не записаны", len(source_filters))
            return

        new_company = Company(name=company_name, owner_user_id=owner.id)
        session.add(new_company)
        await session.flush()

        session.add(CompanyMember(company_id=new_company.id, user_id=owner.id, role='owner'))

        for f in source_filters:
            session.add(SniperFilter(
                user_id=owner.id,
                company_id=new_company.id,
                name=f.name,
                keywords=f.keywords,
                exclude_keywords=f.exclude_keywords,
                price_min=f.price_min,
                price_max=f.price_max,
                regions=f.regions,
                customer_types=f.customer_types,
                tender_types=f.tender_types,
                law_type=f.law_type,
                purchase_stage=f.purchase_stage,
                purchase_method=f.purchase_method,
                okpd2_codes=f.okpd2_codes,
                min_deadline_days=f.min_deadline_days,
                customer_keywords=f.customer_keywords,
                exact_match=f.exact_match,
                purchase_number=f.purchase_number,
                customer_inn=f.customer_inn,
                excluded_customer_inns=f.excluded_customer_inns,
                excluded_customer_keywords=f.excluded_customer_keywords,
                execution_regions=f.execution_regions,
                publication_days=f.publication_days,
                primary_keywords=f.primary_keywords,
                secondary_keywords=f.secondary_keywords,
                search_in=f.search_in,
                ai_intent=f.ai_intent,
                expanded_keywords=f.expanded_keywords,
                notify_chat_ids=[notify_chat_id],
                is_active=True,
            ))

        await session.commit()
        log.info(
            "OK: компания id=%s создана, скопировано %d фильтр(ов). "
            "Пригласить людей: /cabinet/team в кабинете, переключившись на этот воркспейс.",
            new_company.id, len(source_filters),
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--owner-telegram-id", type=int, required=True)
    p.add_argument("--company-name", type=str, required=True)
    p.add_argument("--notify-chat-id", type=int, required=True)
    p.add_argument("--dry", action="store_true", help="dry-run без записи в БД")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(
        owner_telegram_id=args.owner_telegram_id,
        company_name=args.company_name,
        notify_chat_id=args.notify_chat_id,
        dry=args.dry,
    ))
