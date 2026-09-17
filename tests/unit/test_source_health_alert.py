"""Детектор «источник молча отдаёт пустоту».

Регрессия, ради которой это написано: в сентябре 2026 все прокси к
zakupki.gov.ru отвалились, парсер начал возвращать пустой список, и для
системы это выглядело как «подходящих тендеров нет». Ошибок в логах не
было, счётчик ошибок фильтра не рос, алерт не уходил — простой в несколько
часов заметили вручную.
"""
import pytest
from unittest.mock import AsyncMock, patch

from tender_sniper.service import TenderSniperService


def _service() -> TenderSniperService:
    """Сервис без побочных эффектов — нужен только счётчик и метод проверки."""
    svc = TenderSniperService.__new__(TenderSniperService)
    svc._empty_cycles = 0
    return svc


@pytest.mark.unit
@pytest.mark.asyncio
class TestSourceHealth:
    async def test_no_alert_while_tenders_are_downloaded(self):
        svc = _service()
        with patch('tender_sniper.monitoring.send_ops_alert', new=AsyncMock()) as alert:
            await svc._check_source_health(cycle_total_found=120, searched_filters=39,
                                           search_error_count=0)
            alert.assert_not_awaited()
        assert svc._empty_cycles == 0

    async def test_zero_matches_is_not_a_failure(self):
        """Ноль СОВПАДЕНИЙ — норма. Тревога только на ноль СКАЧАННЫХ."""
        svc = _service()
        with patch('tender_sniper.monitoring.send_ops_alert', new=AsyncMock()) as alert:
            # тендеры скачались, просто ни один не подошёл под фильтры
            await svc._check_source_health(cycle_total_found=500, searched_filters=39,
                                           search_error_count=0)
            alert.assert_not_awaited()

    async def test_single_empty_cycle_stays_quiet(self):
        """Один пустой цикл — возможный кратковременный сбой, не алертим."""
        svc = _service()
        with patch('tender_sniper.monitoring.send_ops_alert', new=AsyncMock()) as alert:
            await svc._check_source_health(0, searched_filters=39, search_error_count=0)
            alert.assert_not_awaited()
        assert svc._empty_cycles == 1

    async def test_alerts_on_second_consecutive_empty_cycle(self):
        svc = _service()
        with patch('tender_sniper.monitoring.send_ops_alert', new=AsyncMock()) as alert:
            await svc._check_source_health(0, 39, 0)
            await svc._check_source_health(0, 39, 0)
            assert alert.await_count == 1
            title = alert.await_args[0][0]
            assert 'не скачиваются' in title.lower()

    async def test_does_not_spam_every_cycle(self):
        """После порога молчим до каждого десятого — иначе при долгом
        простое админ получит сотню сообщений и перестанет их читать."""
        svc = _service()
        with patch('tender_sniper.monitoring.send_ops_alert', new=AsyncMock()) as alert:
            for _ in range(9):
                await svc._check_source_health(0, 39, 0)
            assert svc._empty_cycles == 9
            assert alert.await_count == 1  # только на пороге (2-й цикл)
            await svc._check_source_health(0, 39, 0)  # 10-й
            assert alert.await_count == 2

    async def test_reports_recovery(self):
        svc = _service()
        with patch('tender_sniper.monitoring.send_ops_alert', new=AsyncMock()) as alert:
            await svc._check_source_health(0, 39, 0)
            await svc._check_source_health(0, 39, 0)
            alert.reset_mock()
            await svc._check_source_health(300, 39, 0)
            assert alert.await_count == 1
            assert 'восстанов' in alert.await_args[0][0].lower()
        assert svc._empty_cycles == 0

    async def test_nothing_to_search_is_not_an_outage(self):
        """Ни одного фильтра не опрашивали (все на паузе/истёк тариф) —
        это не отказ источника."""
        svc = _service()
        with patch('tender_sniper.monitoring.send_ops_alert', new=AsyncMock()) as alert:
            await svc._check_source_health(0, searched_filters=0, search_error_count=0)
            alert.assert_not_awaited()
        assert svc._empty_cycles == 0
