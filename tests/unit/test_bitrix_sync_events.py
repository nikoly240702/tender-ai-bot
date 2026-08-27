import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm.attributes import flag_modified

import cabinet.bitrix_sync as bitrix_sync
from database import Base, SniperUser, Company


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
