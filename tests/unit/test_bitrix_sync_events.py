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


@pytest.mark.asyncio
async def test_sync_assignee_maps_known_bitrix_user(db, card_factory):
    company_id = db
    async with FakeDatabaseSession.factory() as s:
        from database import CompanyMember
        member = SniperUser(telegram_id=333, data={'bitrix_user_id': 42})
        s.add(member); await s.flush()
        s.add(CompanyMember(company_id=company_id, user_id=member.id, role='member'))
        await s.commit()
        member_id = member.id

    card_id = await card_factory(data={'bitrix_snapshot': {'ASSIGNED_BY_ID': 1}})
    new_deal = {'ID': '700', 'ASSIGNED_BY_ID': 42}

    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        await bitrix_sync._sync_assignee_from_deal(s, company_id, card, new_deal, owner_user_id=1)
        await s.commit()

    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        assert card.assignee_user_id == member_id
        rows = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        assert any(h.action == 'bitrix_field_changed' and h.payload['field'] == 'ASSIGNED_BY_ID' for h in rows)


@pytest.mark.asyncio
async def test_sync_assignee_matches_despite_str_int_mismatch(db, card_factory):
    """Fix 4: bitrix_user_id stored as int, ASSIGNED_BY_ID arriving as str (or
    vice versa) must still match — both sides are str()-normalized."""
    company_id = db
    async with FakeDatabaseSession.factory() as s:
        from database import CompanyMember
        member = SniperUser(telegram_id=334, data={'bitrix_user_id': 42})  # int
        s.add(member); await s.flush()
        s.add(CompanyMember(company_id=company_id, user_id=member.id, role='member'))
        await s.commit()
        member_id = member.id

    card_id = await card_factory(data={'bitrix_snapshot': {'ASSIGNED_BY_ID': 1}})
    new_deal = {'ID': '700', 'ASSIGNED_BY_ID': '42'}  # str from Bitrix API

    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        await bitrix_sync._sync_assignee_from_deal(s, company_id, card, new_deal, owner_user_id=1)
        await s.commit()

    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        assert card.assignee_user_id == member_id


@pytest.mark.asyncio
async def test_sync_assignee_unknown_bitrix_user_keeps_assignee_but_stores_name(db, card_factory):
    company_id = db
    card_id = await card_factory(data={'bitrix_snapshot': {'ASSIGNED_BY_ID': 1}})
    new_deal = {'ID': '700', 'ASSIGNED_BY_ID': 999, 'ASSIGNED_BY_NAME': 'Партнёр'}

    async with FakeDatabaseSession.factory() as s:
        card_before = await s.get(PipelineCard, card_id)
        original_assignee = card_before.assignee_user_id
        await bitrix_sync._sync_assignee_from_deal(s, company_id, card_before, new_deal, owner_user_id=1)
        await s.commit()

    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        assert card.assignee_user_id == original_assignee  # не тронуто
        assert card.data.get('bitrix_assigned_name') == 'Партнёр'


@pytest.mark.asyncio
async def test_sync_assignee_no_change_is_noop(db, card_factory):
    card_id = await card_factory(data={'bitrix_snapshot': {'ASSIGNED_BY_ID': 1}})
    new_deal = {'ID': '700', 'ASSIGNED_BY_ID': 1}
    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        await bitrix_sync._sync_assignee_from_deal(s, 1, card, new_deal, owner_user_id=1)
        await s.commit()
    async with FakeDatabaseSession.factory() as s:
        rows = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        assert [h for h in rows if h.action == 'bitrix_field_changed' and h.payload.get('field') == 'ASSIGNED_BY_ID'] == []


@pytest.mark.asyncio
async def test_sync_assignee_missing_key_in_deal_is_noop(db, card_factory):
    """Malformed/partial deal dict without ASSIGNED_BY_ID at all must not be
    treated as "assignee cleared to None" — no history entry, no mutation."""
    card_id = await card_factory(data={'bitrix_snapshot': {'ASSIGNED_BY_ID': 1}})
    new_deal = {'ID': '700'}  # no ASSIGNED_BY_ID key at all

    async with FakeDatabaseSession.factory() as s:
        card_before = await s.get(PipelineCard, card_id)
        original_assignee = card_before.assignee_user_id
        await bitrix_sync._sync_assignee_from_deal(s, 1, card_before, new_deal, owner_user_id=1)
        await s.commit()

    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        assert card.assignee_user_id == original_assignee
        assert 'bitrix_assigned_name' not in (card.data or {})
        rows = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        assert [h for h in rows if h.action == 'bitrix_field_changed' and h.payload.get('field') == 'ASSIGNED_BY_ID'] == []


@pytest.mark.asyncio
async def test_handle_deal_event_add_creates_card(db, monkeypatch):
    company_id = db
    async with FakeDatabaseSession.factory() as s:
        company = await s.get(Company, company_id)
        owner = await s.get(SniperUser, company.owner_user_id)
        data = dict(owner.data or {})
        data['bitrix24_webhook_url'] = 'https://x.bitrix24.ru/rest/1/tok/'
        data['bitrix24_enabled'] = True
        owner.data = data
        flag_modified(owner, 'data')
        await s.commit()

    async def fake_get_deal(webhook, deal_id):
        return {'ID': deal_id, 'STAGE_ID': 'NEW', 'TITLE': 'Новая сделка', 'COMMENTS': 'Тендер 9999999999999999999'}
    monkeypatch.setattr('bot.handlers.bitrix24.get_bitrix24_deal', fake_get_deal)

    await bitrix_sync.handle_deal_event(company_id, 'ONCRMDEALADD', '111')

    async with FakeDatabaseSession.factory() as s:
        card = (await s.execute(
            select(PipelineCard).where(PipelineCard.company_id == company_id)
        )).scalar_one()
        assert card.data['bitrix_deal_id'] == '111'
        history = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card.id)
        )).scalars().all()
        assert any(h.action == 'card_created_from_bitrix' for h in history)


@pytest.mark.asyncio
async def test_handle_deal_event_update_diffs_existing_card(db, card_factory, monkeypatch):
    company_id = db
    async with FakeDatabaseSession.factory() as s:
        company = await s.get(Company, company_id)
        owner = await s.get(SniperUser, company.owner_user_id)
        data = dict(owner.data or {})
        data['bitrix24_webhook_url'] = 'https://x.bitrix24.ru/rest/1/tok/'
        data['bitrix24_enabled'] = True
        owner.data = data
        flag_modified(owner, 'data')
        await s.commit()

    card_id = await card_factory(bitrix_deal_id='222')

    async def fake_get_deal(webhook, deal_id):
        return {
            'ID': '222', 'STAGE_ID': 'NEW', 'TITLE': 'Обновлённое имя',
            'OPPORTUNITY': '999999', 'UF_CRM_TENDER_CUSTOMER': 'Заказчик А',
            'UF_CRM_TENDER_REGION': 'Москва', 'CLOSEDATE': '2026-09-01', 'ASSIGNED_BY_ID': 1,
        }
    monkeypatch.setattr('bot.handlers.bitrix24.get_bitrix24_deal', fake_get_deal)

    await bitrix_sync.handle_deal_event(company_id, 'ONCRMDEALUPDATE', '222')

    async with FakeDatabaseSession.factory() as s:
        history = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        changed = {h.payload['field'] for h in history if h.action == 'bitrix_field_changed'}
        assert 'TITLE' in changed and 'OPPORTUNITY' in changed


@pytest.mark.asyncio
async def test_handle_deal_event_delete_archives_card(db, card_factory, monkeypatch):
    company_id = db
    async with FakeDatabaseSession.factory() as s:
        company = await s.get(Company, company_id)
        owner = await s.get(SniperUser, company.owner_user_id)
        data = dict(owner.data or {})
        data['bitrix24_webhook_url'] = 'https://x.bitrix24.ru/rest/1/tok/'
        data['bitrix24_enabled'] = True
        owner.data = data
        flag_modified(owner, 'data')
        await s.commit()

    card_id = await card_factory(bitrix_deal_id='333')

    await bitrix_sync.handle_deal_event(company_id, 'ONCRMDEALDELETE', '333')

    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        assert card.archived_at is not None
        history = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        assert any(h.action == 'bitrix_deal_deleted' for h in history)


@pytest.mark.asyncio
async def test_handle_deal_event_no_webhook_configured_is_noop(db):
    company_id = db  # владелец без bitrix24_webhook_url
    await bitrix_sync.handle_deal_event(company_id, 'ONCRMDEALUPDATE', '444')
    async with FakeDatabaseSession.factory() as s:
        count = (await s.execute(select(PipelineCard))).scalars().all()
        assert count == []


@pytest.mark.asyncio
async def test_handle_deal_event_unknown_event_is_noop(db, monkeypatch):
    company_id = db
    called = {'n': 0}
    async def fake_get_deal(webhook, deal_id):
        called['n'] += 1
        return {}
    monkeypatch.setattr('bot.handlers.bitrix24.get_bitrix24_deal', fake_get_deal)
    await bitrix_sync.handle_deal_event(company_id, 'ONSOMETHINGELSE', '555')
    assert called['n'] == 0


@pytest.mark.asyncio
async def test_handle_deal_event_backfills_bitrix_deal_id_on_existing_card_without_link(
    db, card_factory, monkeypatch
):
    """Fix 1: a PipelineCard exists for the tender number (created via some other
    path, e.g. the tender-monitoring feed) but has no bitrix_deal_id yet. An
    incoming Bitrix event for a deal matching that tender number must backfill
    bitrix_deal_id onto the EXISTING card, not attempt a doomed duplicate create."""
    company_id = db
    async with FakeDatabaseSession.factory() as s:
        company = await s.get(Company, company_id)
        owner = await s.get(SniperUser, company.owner_user_id)
        data = dict(owner.data or {})
        data['bitrix24_webhook_url'] = 'https://x.bitrix24.ru/rest/1/tok/'
        data['bitrix24_enabled'] = True
        owner.data = data
        flag_modified(owner, 'data')
        await s.commit()

    tender_number = '9999999999999999999'
    card_id = await card_factory(
        bitrix_deal_id=None, tender_number=tender_number,
        data={'bitrix_snapshot': {}},
    )

    async def fake_get_deal(webhook, deal_id):
        return {
            'ID': deal_id, 'STAGE_ID': 'NEW', 'TITLE': 'Сделка из Bitrix',
            'COMMENTS': f'Тендер {tender_number}',
        }
    monkeypatch.setattr('bot.handlers.bitrix24.get_bitrix24_deal', fake_get_deal)

    await bitrix_sync.handle_deal_event(company_id, 'ONCRMDEALADD', '888')

    async with FakeDatabaseSession.factory() as s:
        cards = (await s.execute(
            select(PipelineCard).where(PipelineCard.company_id == company_id)
        )).scalars().all()
        assert len(cards) == 1  # no duplicate card created
        card = cards[0]
        assert card.id == card_id
        assert card.data['bitrix_deal_id'] == '888'
        assert card.data.get('bitrix_snapshot', {}).get('STAGE_ID') == 'NEW'


@pytest.mark.asyncio
async def test_handle_deal_event_logs_unmapped_stage_move_without_changing_pipeline_stage(
    db, card_factory, monkeypatch
):
    """Fix 2: a Bitrix stage move to something outside _PULL_STAGE_MAP (e.g. an
    intermediate stage like EXECUTING) must be logged as a bitrix_field_changed
    history entry for visibility, but must NOT touch card.stage itself."""
    company_id = db
    async with FakeDatabaseSession.factory() as s:
        company = await s.get(Company, company_id)
        owner = await s.get(SniperUser, company.owner_user_id)
        data = dict(owner.data or {})
        data['bitrix24_webhook_url'] = 'https://x.bitrix24.ru/rest/1/tok/'
        data['bitrix24_enabled'] = True
        owner.data = data
        flag_modified(owner, 'data')
        await s.commit()

    card_id = await card_factory(
        bitrix_deal_id='321', stage='IN_WORK',
        data={'bitrix_snapshot': {'STAGE_ID': 'NEW'}},
    )

    async def fake_get_deal(webhook, deal_id):
        return {'ID': '321', 'STAGE_ID': 'EXECUTING'}
    monkeypatch.setattr('bot.handlers.bitrix24.get_bitrix24_deal', fake_get_deal)

    await bitrix_sync.handle_deal_event(company_id, 'ONCRMDEALUPDATE', '321')

    async with FakeDatabaseSession.factory() as s:
        card = await s.get(PipelineCard, card_id)
        assert card.stage == 'IN_WORK'  # Pipeline's own stage untouched
        history = (await s.execute(
            select(PipelineCardHistory).where(PipelineCardHistory.card_id == card_id)
        )).scalars().all()
        matches = [
            h for h in history
            if h.action == 'bitrix_field_changed' and h.payload.get('field') == 'STAGE_ID'
        ]
        assert len(matches) == 1
        assert matches[0].payload['old'] == 'NEW'
        assert matches[0].payload['new'] == 'EXECUTING'
