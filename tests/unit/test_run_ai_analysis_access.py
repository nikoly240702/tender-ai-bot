"""Анализ документации тендера временно скрыт от обычных пользователей
(06.10.2026) — тот же движок (TenderDocumentExtractor), что и в карточке
Pipeline, и качество на единственном реальном прогоне было спорным.

`_run_ai_analysis` — общая точка для ВСЕХ путей, которые его запускают:
кнопка в карточке тендера, аналог в MAX, автодайджест по сделкам
Битрикс24 и его собственный вебхук. Гейт стоит здесь, одним местом, а не
в каждом из шести мест по отдельности — до любой сетевой операции
(скачивание документов, OpenAI), чтобы не тратить ничьё время и деньги.
"""
import pytest

import bot.handlers.webapp as webapp


@pytest.mark.unit
class TestRunAiAnalysisAdminGate:
    @pytest.mark.asyncio
    async def test_non_admin_is_refused_before_any_network_call(self, monkeypatch):
        monkeypatch.setenv("ADMIN_USER_ID", "111")

        def _boom(*a, **kw):
            raise AssertionError("скачивание документов не должно начинаться для не-админа")
        monkeypatch.setattr(
            "src.parsers.zakupki_document_downloader.ZakupkiDocumentDownloader.download_documents",
            _boom, raising=False)

        with pytest.raises(PermissionError):
            await webapp._run_ai_analysis("0123456789012345678901", "premium", telegram_id=222)

    @pytest.mark.asyncio
    async def test_no_admin_configured_blocks_everyone(self, monkeypatch):
        monkeypatch.delenv("ADMIN_USER_ID", raising=False)
        monkeypatch.delenv("ADMIN_TELEGRAM_ID", raising=False)
        with pytest.raises(PermissionError):
            await webapp._run_ai_analysis("0123456789012345678901", "premium", telegram_id=222)
