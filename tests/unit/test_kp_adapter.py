# tests/unit/test_kp_adapter.py
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
import tender_sniper.database.sqlalchemy_adapter as adapter_mod
from tender_sniper.database.sqlalchemy_adapter import TenderSniperDB
from database import Base, SniperUser, CompanyProfile


@pytest_asyncio.fixture
async def kp_adapter_with_profile(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/kp_test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    class FakeDatabaseSession:
        async def __aenter__(self):
            self.session = factory()
            return self.session
        async def __aexit__(self, et, ev, tb):
            if et is not None:
                await self.session.rollback()
            else:
                await self.session.commit()
            await self.session.close()

    monkeypatch.setattr(adapter_mod, "DatabaseSession", FakeDatabaseSession)

    async with factory() as s:
        user = SniperUser(telegram_id=298437198)
        s.add(user); await s.flush()
        s.add(CompanyProfile(user_id=user.id, kp_number_prefix="ИПХИС", kp_counter=26063))
        await s.commit()
        user_id = user.id

    yield TenderSniperDB(), user_id
    await engine.dispose()


async def test_next_kp_number_increments(kp_adapter_with_profile):
    adapter, user_id = kp_adapter_with_profile
    assert await adapter.next_kp_number(user_id) == "ИПХИС-26064"
    assert await adapter.next_kp_number(user_id) == "ИПХИС-26065"


async def test_save_commercial_proposal(kp_adapter_with_profile):
    adapter, user_id = kp_adapter_with_profile
    pid = await adapter.save_commercial_proposal(user_id, {
        "number": "ИПХИС-26064", "vat_mode": "none",
        "delivery_time": "15 к.д.", "items": [], "total": 0,
    })
    assert isinstance(pid, int) and pid > 0
