"""
Верификация Task 3 (mos-portal-integration): опциональный company_id в
get_all_active_filters (tender_sniper/database/sqlalchemy_adapter.py).

Нет живой dev/staging БД в этом окружении, поэтому вместо ручного скрипта
из брифа (Step 2/4) — тест без реального подключения к БД:

  - подменяем DatabaseSession на фейковую async context-manager-сессию,
    чья execute() не бьёт по БД, а просто запоминает переданный
    SQLAlchemy select(...) и возвращает пустой результат;
  - вызываем реальный метод get_all_active_filters() (без company_id) и
    get_all_active_filters(company_id=57);
  - компилируем оба захваченных statement'а через
    stmt.compile(compile_kwargs={"literal_binds": True}) и проверяем
    итоговый SQL-текст.

Это напрямую упражняет код метода (а не переизобретённую копию запроса) и
доказывает: (1) вызов без company_id рендерит WHERE без единого упоминания
company_id — поведение существующих вызывающих строго не изменилось;
(2) company_id=57 добавляет условие `company_id = 57` в WHERE.
"""

import pytest

from tender_sniper.database import sqlalchemy_adapter as adapter_module
from tender_sniper.database.sqlalchemy_adapter import TenderSniperDB


class _FakeResult:
    """Достаточно похож на sqlalchemy Result, чтобы .all() не падал."""

    def all(self):
        return []


class _FakeSession:
    """Захватывает переданный statement вместо реального execute() к БД."""

    def __init__(self, captured):
        self._captured = captured

    async def execute(self, stmt):
        self._captured.append(stmt)
        return _FakeResult()


class _FakeDatabaseSession:
    """Подмена tender_sniper.database.sqlalchemy_adapter.DatabaseSession —
    async context manager без какого-либо реального подключения к БД."""

    def __init__(self, captured):
        self._captured = captured

    async def __aenter__(self):
        return _FakeSession(self._captured)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _compiled_sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def _where_clause(sql: str) -> str:
    """SELECT list всегда содержит колонку company_id (она есть в модели) —
    сравнивать нужно только WHERE, иначе тест ложно \"находит\" company_id
    в списке выбираемых столбцов, а не в условии фильтрации."""
    flat = " ".join(sql.split())  # схлопываем переводы строк/отступы
    lowered = flat.lower()
    idx = lowered.index(" where ")
    return flat[idx:]


@pytest.mark.asyncio
async def test_no_company_id_where_clause_unchanged(monkeypatch):
    """company_id по умолчанию (None) — WHERE не должен упоминать company_id вовсе."""
    captured = []
    monkeypatch.setattr(
        adapter_module, "DatabaseSession", lambda: _FakeDatabaseSession(captured)
    )

    db = TenderSniperDB()
    result = await db.get_all_active_filters()

    assert result == []
    assert len(captured) == 1
    sql = _compiled_sql(captured[0])
    where = _where_clause(sql)
    assert "company_id" not in where.lower()
    # существующие условия по-прежнему на месте
    assert "is_active" in where.lower()
    assert "deleted_at" in where.lower()
    assert "notifications_enabled" in where.lower()
    assert "subscription_tier" in where.lower()


@pytest.mark.asyncio
async def test_company_id_adds_condition(monkeypatch):
    """company_id=57 — в WHERE должно появиться company_id = 57."""
    captured = []
    monkeypatch.setattr(
        adapter_module, "DatabaseSession", lambda: _FakeDatabaseSession(captured)
    )

    db = TenderSniperDB()
    result = await db.get_all_active_filters(company_id=57)

    assert result == []
    assert len(captured) == 1
    sql = _compiled_sql(captured[0])
    where = _where_clause(sql)
    assert "company_id = 57" in where
