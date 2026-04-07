"""Tests for tier priority utility."""
from datetime import datetime, timedelta

import pytest

from bot.utils.tier_priority import tier_priority, should_upgrade


def test_tier_priority_order():
    assert tier_priority('expired') < tier_priority('trial')
    assert tier_priority('trial') < tier_priority('starter')
    assert tier_priority('starter') < tier_priority('pro')
    assert tier_priority('pro') < tier_priority('premium')


def test_tier_priority_unknown_returns_lowest():
    assert tier_priority('something_weird') == 0


def test_should_upgrade_higher_tier():
    """starter → pro: upgrade allowed"""
    now = datetime.utcnow()
    assert should_upgrade(
        current_tier='starter',
        current_expires=now + timedelta(days=10),
        new_tier='pro',
        new_expires=now + timedelta(days=30)
    ) is True


def test_should_upgrade_lower_tier_blocked():
    """pro → starter: NOT downgrade"""
    now = datetime.utcnow()
    assert should_upgrade(
        current_tier='pro',
        current_expires=now + timedelta(days=10),
        new_tier='starter',
        new_expires=now + timedelta(days=30)
    ) is False


def test_should_upgrade_same_tier_extends_expiry():
    """starter + new date > old → продление"""
    now = datetime.utcnow()
    assert should_upgrade(
        current_tier='starter',
        current_expires=now + timedelta(days=5),
        new_tier='starter',
        new_expires=now + timedelta(days=30)
    ) is True


def test_should_upgrade_same_tier_earlier_expiry_blocked():
    """starter + new date earlier → пропускаем"""
    now = datetime.utcnow()
    assert should_upgrade(
        current_tier='starter',
        current_expires=now + timedelta(days=30),
        new_tier='starter',
        new_expires=now + timedelta(days=5)
    ) is False


def test_should_upgrade_from_expired():
    """expired → starter: upgrade"""
    now = datetime.utcnow()
    assert should_upgrade(
        current_tier='expired',
        current_expires=None,
        new_tier='starter',
        new_expires=now + timedelta(days=30)
    ) is True
