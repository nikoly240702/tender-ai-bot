import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tender_sniper.jobs.mos_portal_poll import (
    compute_poll_window,
    MAX_PAGES_PER_CYCLE,
    MOS_MIN_SCORE_FOR_NOTIFICATION,
    _map_page_to_tenders,
    _to_api_timestamp,
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

    def test_accepts_timezone_aware_datetimes_unchanged(self):
        # Fix 2 (final review): вызывающий код теперь строит now/last_poll как
        # Europe/Moscow-aware datetime, а не naive UTC. compute_poll_window
        # делает только timedelta-арифметику, поэтому должен одинаково
        # работать что с naive, что с aware datetime — без изменения сигнатуры.
        tz = ZoneInfo("Europe/Moscow")
        last_poll = datetime(2026, 9, 13, 10, 0, 0, tzinfo=tz)
        now = datetime(2026, 9, 13, 10, 10, 0, tzinfo=tz)
        window_from, window_to = compute_poll_window(last_poll, now)
        assert window_from == last_poll - timedelta(minutes=30)
        assert window_to == now
        assert window_from.tzinfo is not None


@pytest.mark.unit
class TestToApiTimestamp:
    """Live smoke-test found the API's .NET query parser mangles a literal
    '+' (from a '+03:00' offset) into a space after decoding — 400 Bad
    Request ("Could not convert string to DateTime: ...23:09:26 03:00").
    A UTC 'Z'-suffixed timestamp has no '+' character and sidesteps this
    entirely; confirmed against the real API after this fix."""

    def test_converts_moscow_time_to_utc_z_suffix(self):
        msk = ZoneInfo("Europe/Moscow")
        dt = datetime(2026, 9, 13, 23, 9, 26, tzinfo=msk)  # MSK = UTC+3
        assert _to_api_timestamp(dt) == "2026-09-13T20:09:26Z"

    def test_never_contains_plus_character(self):
        msk = ZoneInfo("Europe/Moscow")
        dt = datetime(2026, 9, 13, 23, 9, 26, tzinfo=msk)
        assert "+" not in _to_api_timestamp(dt)


@pytest.mark.unit
class TestMinScoreGate:
    """Finding #2 (round 1 fix): SmartMatcher.match_tender() returns a
    truthy dict even for its "negative pattern" branch (score=5), so the
    poll loop must gate on MOS_MIN_SCORE_FOR_NOTIFICATION after the `if not
    match` check, exactly like tender_sniper/service.py does. These tests
    exercise the real matcher (no DB/Telegram needed) to prove the gate
    threshold actually rejects that branch's output.

    Finding (final review): this job's threshold was a straight copy of
    service.py's MIN_SCORE_FOR_NOTIFICATION=35, which gates a COMPOSITE
    score (raw SmartMatcher + AI-relevance boost, name+description). This
    job has no AI-boost step and description is always empty, so it was
    gating a strictly smaller raw score against a threshold calibrated for
    a larger composite one — real matches scoring 9-16 were silently
    dropped. Renamed + lowered to MOS_MIN_SCORE_FOR_NOTIFICATION=20 (a
    reasoned starting point, not a final tuned number).
    """

    def test_mos_min_score_constant_is_lower_than_service_py(self):
        # tender_sniper/service.py:347 — MIN_SCORE_FOR_NOTIFICATION=35 гейтит
        # composite score; здесь — сырой name-only score, порог ниже намеренно.
        assert MOS_MIN_SCORE_FOR_NOTIFICATION == 20
        assert MOS_MIN_SCORE_FOR_NOTIFICATION < 35

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
        assert match['score'] < MOS_MIN_SCORE_FOR_NOTIFICATION


@pytest.mark.unit
class TestMapPageToTenders:
    """Fix 5 (final review): a single DTO missing "id" used to raise a
    KeyError that propagated to the cycle-level except, aborting the whole
    cycle (including good tenders in the same page) and preventing
    last_poll from advancing. _map_page_to_tenders isolates one bad DTO."""

    def test_skips_malformed_dto_keeps_good_ones(self):
        page = [
            {"id": 1, "name": "Тендер 1"},
            {"name": "Тендер без id — должен быть пропущен"},
            {"id": 3, "name": "Тендер 3"},
        ]
        tenders = _map_page_to_tenders(page)
        assert len(tenders) == 2
        assert [t['number'] for t in tenders] == ['MOS-1', 'MOS-3']

    def test_all_malformed_returns_empty_list(self):
        page = [{"name": "нет id"}, {"name": "тоже нет id"}]
        assert _map_page_to_tenders(page) == []
