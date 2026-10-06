"""Tender-GPT временно скрыт от обычных пользователей (06.10.2026) — он
только что был мёртв на каждом сообщении из-за проксирования, и прежде
чем снова открывать его всем, владелец хочет сам проверить качество.

Доступ остаётся только у администратора (ADMIN_USER_ID); для всех
остальных — явное сообщение, без похода в сессию/квоту/LLM.
"""
import pytest

from tender_sniper.tender_gpt.service import TenderGPTService


class _ExplodingSessionManager:
    """Если выполнение дойдёт сюда — гейт сработал не в начале chat()."""
    async def get_or_create_session(self, *a, **kw):
        raise AssertionError("session_manager не должен вызываться для не-админа")


class _ExplodingQuotaManager:
    async def check_quota(self, *a, **kw):
        raise AssertionError("quota_manager не должен вызываться для не-админа")


@pytest.fixture
def service():
    svc = TenderGPTService()
    svc.session_manager = _ExplodingSessionManager()
    svc.quota_manager = _ExplodingQuotaManager()
    return svc


@pytest.mark.unit
class TestTenderGptAdminGate:
    @pytest.mark.asyncio
    async def test_non_admin_gets_disabled_message_with_no_side_effects(self, service, monkeypatch):
        monkeypatch.setenv("ADMIN_USER_ID", "111")
        result = await service.chat(telegram_id=222, user_id=1, user_message="привет")
        assert result["session_id"] is None
        assert result["tool_calls"] == 0
        assert "недоступ" in result["response"].lower() or "отключ" in result["response"].lower()

    @pytest.mark.asyncio
    async def test_admin_is_not_blocked_by_the_gate(self, service, monkeypatch):
        """Админ должен пройти гейт — дальше он упадёт на проверке квоты
        (следующий шаг в chat()), и это доказывает, что для него код идёт
        дальше, а не возвращается рано."""
        monkeypatch.setenv("ADMIN_USER_ID", "111")
        with pytest.raises(AssertionError, match="quota_manager"):
            await service.chat(telegram_id=111, user_id=1, user_message="привет")

    @pytest.mark.asyncio
    async def test_no_admin_configured_blocks_everyone(self, service, monkeypatch):
        monkeypatch.delenv("ADMIN_USER_ID", raising=False)
        monkeypatch.delenv("ADMIN_TELEGRAM_ID", raising=False)
        result = await service.chat(telegram_id=222, user_id=1, user_message="привет")
        assert result["session_id"] is None
