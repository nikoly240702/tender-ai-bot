import pytest
from datetime import datetime, timedelta

from tender_sniper.jobs.mos_portal_poll import compute_poll_window, MAX_PAGES_PER_CYCLE

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
