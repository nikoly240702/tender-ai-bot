import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

import cabinet.bitrix_sync as bitrix_sync
from database import Base, SniperUser, Company, PipelineCard


class FakeDatabaseSession:
    factory = None

    async def __aenter__(self):
        self.session = FakeDatabaseSession.factory()
        return self.session

    async def __aexit__(self, et, ev, tb):
        if et is not None:
            await self.session.rollback()
        else:
            await self.session.commit()
        await self.session.close()


@pytest_asyncio.fixture
async def setup(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/char_test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    FakeDatabaseSession.factory = factory
    monkeypatch.setattr(bitrix_sync, "DatabaseSession", FakeDatabaseSession)

    async with factory() as s:
        owner = SniperUser(telegram_id=222, data={'bitrix24_webhook_url': 'https://x.bitrix24.ru/rest/1/tok/', 'bitrix24_enabled': True})
        s.add(owner); await s.flush()
        company = Company(name="Team", owner_user_id=owner.id)
        s.add(company); await s.flush()
        await s.commit()
        company_id, owner_id = company.id, owner.id

    yield company_id, owner_id, factory
    await engine.dispose()


def _deal(deal_id, stage='NEW', title='Поставка бумаги', number='0123456789012345678'):
    return {
        'ID': deal_id, 'STAGE_ID': stage, 'TITLE': title,
        'OPPORTUNITY': '150000', 'CLOSEDATE': '2026-09-01T00:00:00+03:00',
        'UF_CRM_TENDER_CUSTOMER': 'ООО Ромашка', 'UF_CRM_TENDER_REGION': 'Москва',
        'COMMENTS': f'Тендер {number}',
    }


@pytest.mark.asyncio
async def test_import_creates_card_with_expected_stage_and_source(setup, monkeypatch):
    company_id, owner_id, factory = setup

    async def fake_fetch_all_deals(webhook):
        return [_deal('501', stage='NEW')]
    monkeypatch.setattr(bitrix_sync, "_fetch_all_deals", fake_fetch_all_deals)

    result = await bitrix_sync.import_deals_to_pipeline(company_id)
    assert result['imported'] == 1

    async with factory() as s:
        card = (await s.execute(select(PipelineCard).where(PipelineCard.company_id == company_id))).scalar_one()
        assert card.stage == 'FOUND'
        assert card.source == 'bitrix_import'
        assert card.data['bitrix_deal_id'] == '501'
        assert card.data['name'] == 'Поставка бумаги'


@pytest.mark.asyncio
async def test_import_skips_existing_card_but_backfills_deal_id(setup, monkeypatch):
    company_id, owner_id, factory = setup
    async with factory() as s:
        s.add(PipelineCard(
            company_id=company_id, tender_number='0123456789012345678',
            stage='IN_WORK', assignee_user_id=owner_id, created_by=owner_id,
            data={'name': 'уже в pipeline'},
        ))
        await s.commit()

    async def fake_fetch_all_deals(webhook):
        return [_deal('501')]
    monkeypatch.setattr(bitrix_sync, "_fetch_all_deals", fake_fetch_all_deals)

    result = await bitrix_sync.import_deals_to_pipeline(company_id)
    assert result['imported'] == 0
    assert result['skipped'] == 1

    async with factory() as s:
        card = (await s.execute(select(PipelineCard).where(PipelineCard.company_id == company_id))).scalar_one()
        assert card.stage == 'IN_WORK'  # не тронуто
        assert card.data['bitrix_deal_id'] == '501'  # но подставлено


@pytest.mark.asyncio
async def test_pull_updates_stage_for_mapped_stage_and_ignores_unmapped(setup, monkeypatch):
    company_id, owner_id, factory = setup
    async with factory() as s:
        card = PipelineCard(
            company_id=company_id, tender_number='t1', stage='FOUND',
            assignee_user_id=owner_id, created_by=owner_id,
            data={'bitrix_deal_id': '900'},
        )
        s.add(card)
        await s.commit()

    async def fake_fetch_modified_deals(webhook, since):
        return [{'ID': '900', 'STAGE_ID': 'WON'}]
    monkeypatch.setattr(bitrix_sync, "_fetch_modified_deals", fake_fetch_modified_deals)

    result = await bitrix_sync.pull_changes_from_bitrix(company_id)
    # Task 5 fixed the raw-SQL `data::text LIKE :pattern` lookup (Postgres-only
    # cast, non-portable to SQLite and latently buggy on substring matches) by
    # replacing it with the portable `_find_card_by_bitrix_deal_id` helper.
    # The lookup now succeeds and the mapped stage (WON -> RESULT/won) applies.
    assert result['checked'] == 1
    assert result['updated'] == 1
    assert result['errors'] == 0

    async with factory() as s:
        card = (await s.execute(select(PipelineCard).where(PipelineCard.company_id == company_id))).scalar_one()
        assert card.stage == 'RESULT'
        assert card.result == 'won'


@pytest.mark.asyncio
async def test_pull_does_not_rollback_further_along_card(setup, monkeypatch):
    company_id, owner_id, factory = setup
    async with factory() as s:
        card = PipelineCard(
            company_id=company_id, tender_number='t2', stage='SUBMITTED',
            assignee_user_id=owner_id, created_by=owner_id,
            data={'bitrix_deal_id': '901'},
        )
        s.add(card)
        await s.commit()

    async def fake_fetch_modified_deals(webhook, since):
        return [{'ID': '901', 'STAGE_ID': 'NEW'}]  # NEW -> FOUND, это "назад"
    monkeypatch.setattr(bitrix_sync, "_fetch_modified_deals", fake_fetch_modified_deals)

    result = await bitrix_sync.pull_changes_from_bitrix(company_id)
    # The card lookup now succeeds (Task 5's portable `_find_card_by_bitrix_deal_id`),
    # so this genuinely exercises the `_STAGE_ORDER` anti-rollback comparison in
    # `_pull_stage_update`: NEW -> FOUND would move the card backward from
    # SUBMITTED, so it must be declined and `updated` stays 0.
    assert result['updated'] == 0

    async with factory() as s:
        card = (await s.execute(select(PipelineCard).where(PipelineCard.company_id == company_id))).scalar_one()
        assert card.stage == 'SUBMITTED'  # не откатили
