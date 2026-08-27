import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm.attributes import flag_modified

import cabinet.bitrix_sync as bitrix_sync
from database import Base, SniperUser, Company
from database import PipelineCard, PipelineCardHistory
from sqlalchemy import select


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
async def db(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bitrix_events_test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    FakeDatabaseSession.factory = factory
    monkeypatch.setattr(bitrix_sync, "DatabaseSession", FakeDatabaseSession)

    async with factory() as s:
        owner = SniperUser(telegram_id=111)
        s.add(owner); await s.flush()
        company = Company(name="Team", owner_user_id=owner.id)
        s.add(company); await s.flush()
        await s.commit()
        company_id = company.id

    yield company_id
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_or_create_inbound_secret_is_stable(db):
    company_id = db
    secret1 = await bitrix_sync.get_or_create_inbound_secret(company_id)
    secret2 = await bitrix_sync.get_or_create_inbound_secret(company_id)
    assert secret1 == secret2
    assert len(secret1) >= 16


@pytest.mark.asyncio
async def test_rotate_inbound_secret_changes_value(db):
    company_id = db
    secret1 = await bitrix_sync.get_or_create_inbound_secret(company_id)
    secret2 = await bitrix_sync.rotate_inbound_secret(company_id)
    assert secret1 != secret2
    assert await bitrix_sync.get_or_create_inbound_secret(company_id) == secret2


@pytest.mark.asyncio
async def test_verify_inbound_secret(db):
    company_id = db
    secret = await bitrix_sync.get_or_create_inbound_secret(company_id)
    assert await bitrix_sync.verify_inbound_secret(company_id, secret) is True
    assert await bitrix_sync.verify_inbound_secret(company_id, 'wrong') is False
    assert await bitrix_sync.verify_inbound_secret(company_id, '') is False


@pytest.mark.asyncio
async def test_verify_inbound_secret_unknown_company_is_false(db):
    assert await bitrix_sync.verify_inbound_secret(999999, 'anything') is False


@pytest_asyncio.fixture
async def card_factory(db):
    company_id = db
    async def _make(bitrix_deal_id='700', **overrides):
        async with FakeDatabaseSession.factory() as s:
            company = await s.get(Company, company_id)
            data = {'bitrix_deal_id': bitrix_deal_id, 'bitrix_snapshot': {
                'TITLE': 'Старое имя', 'OPPORTUNITY': '100000',
                'UF_CRM_TENDER_CUSTOMER': 'Заказчик А', 'UF_CRM_TENDER_REGION': 'Москва',
                'CLOSEDATE': '2026-09-01', 'ASSIGNED_BY_ID': 1, 'STAGE_ID': 'NEW',
            }}
            data.update(overrides.pop('data', {}))
            card = PipelineCard(
                company_id=company_id, tender_number=overrides.pop('tender_number', 't-diff'),
                stage=overrides.pop('stage', 'FOUND'),
                assignee_user_id=company.owner_user_id, created_by=company.owner_user_id,
                data=data,
            )
            s.add(card)
            await s.commit()
            return card.id
    return _make


@pytest.mark.asyncio
async def test_find_card_by_bitrix_deal_id_matches(db, card_factory):
    company_id = db
    card_id = await card_factory(bitrix_deal_id='777')
    async with FakeDatabaseSession.factory() as s:
        found = await bitrix_sync._find_card_by_bitrix_deal_id(s, company_id, '777')
        assert found is not None
        assert found.id == card_id


@pytest.mark.asyncio
async def test_find_card_by_bitrix_deal_id_no_match_returns_none(db, card_factory):
    company_id = db
    await card_factory(bitrix_deal_id='777')
    async with FakeDatabaseSession.factory() as s:
        found = await bitrix_sync._find_card_by_bitrix_deal_id(s, company_id, '999')
        assert found is None


@pytest.mark.asyncio
async def test_diff_logs_one_history_entry_per_changed_field(db, card_factory):
    company_id = db
    card_id = await card_factory()
    new_deal = {
        'ID': '700', 'TITLE': 'Новое имя', 'OPPORTUNITY': '250000',
        'UF_CRM_TENDER_CUSTOMER': 'Заказчик А',  # не менялось
        'UF_CRM_TENDER_REGION': 'Санкт-Петербург', 'CLOSEDATE': '2026-09-01',
        'ASSIGNED_BY_ID': 1, 'STAGE_ID': 'NEW',
    }
    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        await bitrix_sync._diff_and_log_field_changes(s, card, new_deal, owner_user_id=1)
        await s.commit()

    async with FakeDatabaseSession.factory() as s:
        rows = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        changed_fields = {h.payload['field'] for h in rows if h.action == 'bitrix_field_changed'}
        assert changed_fields == {'TITLE', 'OPPORTUNITY', 'UF_CRM_TENDER_REGION'}
        card = await s.get(PipelineCard, card_id)
        assert card.data['bitrix_snapshot']['TITLE'] == 'Новое имя'


@pytest.mark.asyncio
async def test_diff_skips_field_never_seen_before(db, card_factory):
    company_id = db
    card_id = await card_factory(data={'bitrix_snapshot': {}})  # пустой снимок
    new_deal = {'ID': '700', 'TITLE': 'Имя', 'STAGE_ID': 'NEW'}
    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        await bitrix_sync._diff_and_log_field_changes(s, card, new_deal, owner_user_id=1)
        await s.commit()

    async with FakeDatabaseSession.factory() as s:
        rows = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        assert [h for h in rows if h.action == 'bitrix_field_changed'] == []
