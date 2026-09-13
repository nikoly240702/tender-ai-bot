"""
Регрессия для бага дедупа группы (обнаружен 13.09.2026 на живом прогоне
Портала поставщиков): is_tender_sent_to_chat() раньше проверяла
"отправлено ли ЭТОМУ ПОЛЬЗОВАТЕЛЮ" (по user_id), а не "отправлено ли
ИМЕННО В ЭТОТ ЧАТ" — поэтому отправка в личку (всегда первая в
notify_chat_ids) ложно "засчитывалась" и для группы, и группа молча
пропускалась. Миграция 20260913_notified_chat добавила
notified_chat_id в sniper_notifications; функция теперь фильтрует
именно по нему.

Нет живой dev/staging БД в этом окружении (см. test_get_all_active_filters_
company_scope.py) — тот же паттерн: подменяем DatabaseSession фейковой
async-сессией, которая захватывает переданный SQLAlchemy select(...)
вместо реального обращения к БД, и проверяем скомпилированный SQL.
"""

import pytest

from tender_sniper.database import sqlalchemy_adapter as adapter_module
from tender_sniper.database.sqlalchemy_adapter import TenderSniperDB


class _FakeResult:
    def first(self):
        return None


class _FakeSession:
    def __init__(self, captured):
        self._captured = captured

    async def execute(self, stmt):
        self._captured.append(stmt)
        return _FakeResult()


class _FakeDatabaseSession:
    def __init__(self, captured):
        self._captured = captured

    async def __aenter__(self):
        return _FakeSession(self._captured)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _compiled_sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_filters_by_exact_chat_id_not_user_id(monkeypatch):
    """WHERE должен содержать notified_chat_id = <chat_id> и НЕ должен
    больше делать отдельный подзапрос/join по user_id через notify_chat_ids
    (старая, багованная логика) — только один запрос, по одному условию."""
    captured = []
    monkeypatch.setattr(
        adapter_module, "DatabaseSession", lambda: _FakeDatabaseSession(captured)
    )

    db = TenderSniperDB()
    result = await db.is_tender_sent_to_chat("MOS-123", -1004376206306)

    assert result is False
    # Ровно один execute — старая версия делала два (сначала искала user_ids
    # среди фильтров, потом отдельно проверяла sniper_notifications)
    assert len(captured) == 1
    sql = _compiled_sql(captured[0])
    assert "notified_chat_id = -1004376206306" in sql
    assert "tender_number = 'MOS-123'" in sql


@pytest.mark.asyncio
async def test_different_chat_id_is_independent(monkeypatch):
    """Явно фиксируем регрессию: chat_id личного чата и chat_id группы —
    разные условия, одно не должно подставляться вместо другого."""
    captured = []
    monkeypatch.setattr(
        adapter_module, "DatabaseSession", lambda: _FakeDatabaseSession(captured)
    )

    db = TenderSniperDB()
    await db.is_tender_sent_to_chat("MOS-123", 298437198)  # личка
    await db.is_tender_sent_to_chat("MOS-123", -1004376206306)  # группа

    assert len(captured) == 2
    sql_personal = _compiled_sql(captured[0])
    sql_group = _compiled_sql(captured[1])
    assert "notified_chat_id = 298437198" in sql_personal
    assert "notified_chat_id = -1004376206306" in sql_group
    assert "notified_chat_id = 298437198" not in sql_group
