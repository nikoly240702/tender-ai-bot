"""Tests for broadcast click tracking."""
from bot.handlers.broadcast_tracking import parse_bcast_callback


def test_parse_basic():
    result = parse_bcast_callback("bcast:42:1234:pay_starter")
    assert result == {'broadcast_id': 42, 'recipient_id': 1234, 'action': 'pay_starter'}


def test_parse_action_with_underscore():
    result = parse_bcast_callback("bcast:1:5:tiers")
    assert result == {'broadcast_id': 1, 'recipient_id': 5, 'action': 'tiers'}


def test_parse_invalid_returns_none():
    assert parse_bcast_callback("invalid") is None
    assert parse_bcast_callback("bcast:not_a_number:1:action") is None
    assert parse_bcast_callback("bcast:1:2") is None  # missing action


def test_callback_data_within_telegram_limit():
    """Telegram callback_data limit is 64 bytes."""
    sample = "bcast:99999:99999:pay_starter"
    assert len(sample.encode('utf-8')) <= 64
