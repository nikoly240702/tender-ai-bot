import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm.attributes import flag_modified

import cabinet.bitrix_sync as bitrix_sync
from database import Base, SniperUser, Company, PipelineCard, PipelineCardHistory


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
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/comment_sync_test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    FakeDatabaseSession.factory = factory
    monkeypatch.setattr(bitrix_sync, "DatabaseSession", FakeDatabaseSession)

    async with factory() as s:
        owner = SniperUser(telegram_id=444, data={
            'bitrix24_webhook_url': 'https://x.bitrix24.ru/rest/1/tok/',
            'bitrix24_enabled': True,
        })
        s.add(owner); await s.flush()
        company = Company(name="Team", owner_user_id=owner.id)
        s.add(company); await s.flush()
        card = PipelineCard(
            company_id=company.id, tender_number='t-comments', stage='FOUND',
            assignee_user_id=owner.id, created_by=owner.id,
            data={'bitrix_deal_id': '55', 'bitrix_last_comment_id': 0},
        )
        s.add(card); await s.flush()
        await s.commit()
        company_id, card_id, owner_id = company.id, card.id, owner.id

    yield company_id, card_id, owner_id, factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_comments_appends_history_and_advances_cursor(setup, monkeypatch):
    company_id, card_id, owner_id, factory = setup

    async def fake_batch(webhook, deal_since):
        assert deal_since == {'55': 0}
        return {'55': [
            {'ID': '10', 'AUTHOR_ID': '7', 'COMMENT': 'Заказчик перезвонил', 'CREATED': '2026-08-27T10:00:00'},
            {'ID': '11', 'AUTHOR_ID': '7', 'COMMENT': 'Отправили КП', 'CREATED': '2026-08-27T11:00:00'},
        ]}
    monkeypatch.setattr('bot.handlers.bitrix24.batch_list_deal_comments', fake_batch)

    result = await bitrix_sync.sync_comments_for_company(company_id)
    assert result == {'checked': 1, 'added': 2}

    async with factory() as s:
        card = await s.get(PipelineCard, card_id)
        assert card.data['bitrix_last_comment_id'] == 11
        history = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        comment_rows = [h for h in history if h.action == 'bitrix_comment']
        assert len(comment_rows) == 2
        assert comment_rows[0].payload['text'] == 'Заказчик перезвонил'


@pytest.mark.asyncio
async def test_sync_comments_no_new_comments_no_history(setup, monkeypatch):
    company_id, card_id, owner_id, factory = setup

    async def fake_batch(webhook, deal_since):
        return {'55': []}
    monkeypatch.setattr('bot.handlers.bitrix24.batch_list_deal_comments', fake_batch)

    result = await bitrix_sync.sync_comments_for_company(company_id)
    assert result == {'checked': 1, 'added': 0}


@pytest.mark.asyncio
async def test_sync_comments_skips_archived_cards(setup, monkeypatch):
    company_id, card_id, owner_id, factory = setup
    async with factory() as s:
        card = await s.get(PipelineCard, card_id)
        from datetime import datetime
        card.archived_at = datetime.utcnow()
        await s.commit()

    called = {'n': 0}
    async def fake_batch(webhook, deal_since):
        called['n'] += 1
        return {}
    monkeypatch.setattr('bot.handlers.bitrix24.batch_list_deal_comments', fake_batch)

    result = await bitrix_sync.sync_comments_for_company(company_id)
    assert result == {'checked': 0, 'added': 0}
    assert called['n'] == 0
