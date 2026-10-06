"""AI-анализ карточки временно скрыт от обычных пользователей (06.10.2026):
за всё время существования кнопкой воспользовались один раз, и в том
единственном прогоне нашёлся баг (сбитая нумерация позиций). Пока качество
не проверено на реальном потоке, доступ — только у владельца.

Гейт стоит до открытия сессии БД: обычный пользователь не должен даже
списывать квоту или будить фоновую задачу.
"""
import pytest

import cabinet.pipeline_service as pipeline_service


class _ExplodingDatabaseSession:
    def __call__(self):
        raise AssertionError("DatabaseSession не должен открываться для не-админа")


@pytest.mark.unit
class TestEnrichCardAdminGate:
    @pytest.mark.asyncio
    async def test_non_admin_is_refused_without_touching_the_database(self, monkeypatch):
        monkeypatch.setenv("ADMIN_USER_ID", "111")
        monkeypatch.setattr(pipeline_service, "DatabaseSession", _ExplodingDatabaseSession())

        result = await pipeline_service.enrich_card_with_ai(
            card_id=1, by_user_id=1, telegram_id=222)

        assert result["ok"] is False
        assert result["status"] == 403

    @pytest.mark.asyncio
    async def test_admin_passes_the_gate(self, monkeypatch):
        """Админ должен дойти до открытия сессии БД — дальше падение на
        заглушке и доказывает, что гейт его не остановил."""
        monkeypatch.setenv("ADMIN_USER_ID", "111")
        monkeypatch.setattr(pipeline_service, "DatabaseSession", _ExplodingDatabaseSession())

        with pytest.raises(AssertionError, match="DatabaseSession"):
            await pipeline_service.enrich_card_with_ai(
                card_id=1, by_user_id=1, telegram_id=111)

    @pytest.mark.asyncio
    async def test_no_admin_configured_blocks_everyone(self, monkeypatch):
        monkeypatch.delenv("ADMIN_USER_ID", raising=False)
        monkeypatch.delenv("ADMIN_TELEGRAM_ID", raising=False)
        monkeypatch.setattr(pipeline_service, "DatabaseSession", _ExplodingDatabaseSession())

        result = await pipeline_service.enrich_card_with_ai(
            card_id=1, by_user_id=1, telegram_id=222)
        assert result["ok"] is False
