"""Режим «только чтение» в кабинете при истёкшей подписке.

Смотреть разрешено всё, менять — ничего, кроме оплаты, профиля,
настроек и выключения собственных фильтров. Правило проверяется одним
middleware на входе, поэтому здесь тестируются чистые функции решения,
а не каждая ручка по отдельности.
"""
from datetime import datetime, timedelta

import pytest

from cabinet.auth import (
    filter_toggle_allowed,
    subscription_is_active,
    write_allowed,
)


NOW = datetime(2026, 9, 28, 12, 0, 0)


@pytest.mark.unit
class TestSubscriptionIsActive:
    def test_future_date_is_active(self):
        assert subscription_is_active(NOW + timedelta(days=1), now=NOW) is True

    def test_past_date_is_not_active(self):
        assert subscription_is_active(NOW - timedelta(seconds=1), now=NOW) is False

    def test_missing_date_is_not_active(self):
        """Пустая дата — это «подписки не было», а не «бессрочная»."""
        assert subscription_is_active(None, now=NOW) is False

    def test_iso_string_is_understood(self):
        """Дата приезжает из JSON-полей, где она уже строка."""
        assert subscription_is_active("2026-10-01T00:00:00", now=NOW) is True
        assert subscription_is_active("2026-09-01T00:00:00", now=NOW) is False

    def test_garbage_is_not_active(self):
        assert subscription_is_active("позавчера", now=NOW) is False


@pytest.mark.unit
class TestWriteAllowed:
    def test_reading_is_never_blocked(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            assert write_allowed(method, "/cabinet/api/filters",
                                 subscription_active=False) is True

    def test_write_blocked_without_subscription(self):
        assert write_allowed("POST", "/cabinet/api/filters/create",
                             subscription_active=False) is False
        assert write_allowed("DELETE", "/cabinet/api/filters/7",
                             subscription_active=False) is False

    def test_write_allowed_with_subscription(self):
        assert write_allowed("POST", "/cabinet/api/filters/create",
                             subscription_active=True) is True

    def test_payment_stays_open(self):
        """Иначе блокировка запирает и выход из неё самой."""
        assert write_allowed("POST", "/cabinet/api/subscription/pay",
                             subscription_active=False) is True

    def test_profile_and_settings_stay_open(self):
        assert write_allowed("POST", "/cabinet/api/profile",
                             subscription_active=False) is True
        assert write_allowed("POST", "/cabinet/api/settings",
                             subscription_active=False) is True

    def test_filter_toggle_reaches_handler(self):
        """Выключать фильтры можно, поэтому middleware пропускает toggle —
        включение отсекает уже сам обработчик, см. filter_toggle_allowed."""
        assert write_allowed("POST", "/cabinet/api/filters/7/toggle",
                             subscription_active=False) is True

    def test_admin_passes_anywhere(self):
        assert write_allowed("POST", "/cabinet/api/filters/create",
                             subscription_active=False, is_admin=True) is True

    def test_other_routes_are_not_our_business(self):
        """Middleware висит на всём приложении, а правило — только про кабинет."""
        assert write_allowed("POST", "/payment/webhook",
                             subscription_active=False) is True
        assert write_allowed("POST", "/webhook/bitrix24/analyze",
                             subscription_active=False) is True


@pytest.mark.unit
class TestFilterToggle:
    def test_disabling_allowed_without_subscription(self):
        assert filter_toggle_allowed(will_enable=False,
                                     subscription_active=False) is True

    def test_enabling_blocked_without_subscription(self):
        """Иначе выключателем обходится вся блокировка: выключил и включил."""
        assert filter_toggle_allowed(will_enable=True,
                                     subscription_active=False) is False

    def test_enabling_allowed_with_subscription(self):
        assert filter_toggle_allowed(will_enable=True,
                                     subscription_active=True) is True

    def test_admin_can_enable(self):
        assert filter_toggle_allowed(will_enable=True, subscription_active=False,
                                     is_admin=True) is True
