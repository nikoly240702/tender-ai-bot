import pytest
from datetime import datetime, timedelta

from tender_sniper.jobs.mos_portal_poll import (
    compute_poll_window,
    MAX_PAGES_PER_CYCLE,
    MIN_SCORE_FOR_NOTIFICATION,
)
from tender_sniper.matching import SmartMatcher

@pytest.mark.unit
class TestComputePollWindow:
    def test_overlaps_last_poll_by_30_minutes(self):
        last_poll = datetime(2026, 9, 13, 10, 0, 0)
        now = datetime(2026, 9, 13, 10, 10, 0)
        window_from, window_to = compute_poll_window(last_poll, now)
        assert window_from == last_poll - timedelta(minutes=30)
        assert window_to == now

    def test_first_run_with_no_prior_poll_uses_default_lookback(self):
        now = datetime(2026, 9, 13, 10, 0, 0)
        window_from, window_to = compute_poll_window(None, now)
        assert window_to == now
        assert window_from < now

    def test_max_pages_per_cycle_is_bounded(self):
        assert MAX_PAGES_PER_CYCLE <= 20  # см. спеку разд. 5 — потолок пагинации за цикл


@pytest.mark.unit
class TestMinScoreGate:
    """Finding #2 (round 1 fix): SmartMatcher.match_tender() returns a
    truthy dict even for its "negative pattern" branch (score=5), so the
    poll loop must gate on MIN_SCORE_FOR_NOTIFICATION after the `if not
    match` check, exactly like tender_sniper/service.py does. These tests
    exercise the real matcher (no DB/Telegram needed) to prove the gate
    threshold actually rejects that branch's output.
    """

    def test_min_score_constant_matches_service_py(self):
        # tender_sniper/service.py:347 — единый порог для composite score.
        assert MIN_SCORE_FOR_NOTIFICATION == 35

    def test_negative_pattern_match_is_truthy_but_below_gate(self):
        matcher = SmartMatcher()
        tender = {
            'number': '0000000000000001',
            'name': 'Оказание услуг по транспортировке медицинских отходов',
            'description': '',
        }
        filter_config = {
            'id': 1,
            'name': 'Тестовый фильтр',
            'user_id': 1,
            'keywords': '["транспортировка"]',
            'exclude_keywords': '[]',
            'regions': '[]',
            'customer_types': '[]',
            'tender_types': '[]',
        }

        match = matcher.match_tender(tender, filter_config)

        # Регрессия для finding #2: раньше `if not match: continue` пропускал
        # это дальше, потому что match — truthy dict (score=5), не None.
        assert match is not None
        assert match['score'] == 5
        assert match['score'] < MIN_SCORE_FOR_NOTIFICATION
