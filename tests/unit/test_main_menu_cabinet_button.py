"""Вход в веб-кабинет из бота.

До этого точки входа не было ни одной: адрес кабинета человек мог узнать,
только если ему прислали ссылку руками.
"""
import pytest
from aiogram.types import InlineKeyboardButton

from bot.config import cabinet_login_url
from bot.handlers.menu_priority import main_menu_keyboard


def _urls(keyboard):
    return [b.url for row in keyboard.inline_keyboard for b in row if b.url]


@pytest.fixture
def monitoring_button():
    return InlineKeyboardButton(text="⏸️ Пауза", callback_data="sniper_pause_monitoring")


@pytest.mark.unit
class TestCabinetLoginUrl:
    def test_default_points_at_the_domain_registered_in_botfather(self):
        """Виджет входа Telegram работает только на домене из BotFather —
        на любом другом кнопка входа молча не авторизует."""
        assert cabinet_login_url() == "https://cabinet.tendersniper.ru/cabinet/login"

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("CABINET_URL", "https://example.test")
        assert cabinet_login_url() == "https://example.test/cabinet/login"

    def test_trailing_slash_does_not_double(self, monkeypatch):
        monkeypatch.setenv("CABINET_URL", "https://example.test/")
        assert cabinet_login_url() == "https://example.test/cabinet/login"


@pytest.mark.unit
class TestMainMenuKeyboard:
    def test_has_cabinet_button(self, monitoring_button):
        keyboard = main_menu_keyboard(monitoring_button)
        assert cabinet_login_url() in _urls(keyboard)

    def test_cabinet_button_follows_env(self, monkeypatch, monitoring_button):
        monkeypatch.setenv("CABINET_URL", "https://example.test")
        keyboard = main_menu_keyboard(monitoring_button)
        assert "https://example.test/cabinet/login" in _urls(keyboard)

    def test_keeps_existing_actions(self, monitoring_button):
        """Кнопка добавляется к меню, а не вместо чего-то."""
        keyboard = main_menu_keyboard(monitoring_button)
        callbacks = [b.callback_data
                     for row in keyboard.inline_keyboard for b in row if b.callback_data]
        for expected in ("sniper_my_filters", "sniper_new_search", "sniper_all_tenders",
                         "analyze_start", "open_settings", "sniper_stats",
                         "sniper_plans", "sniper_help"):
            assert expected in callbacks

    def test_monitoring_button_is_kept(self, monitoring_button):
        keyboard = main_menu_keyboard(monitoring_button)
        assert monitoring_button in [b for row in keyboard.inline_keyboard for b in row]

    def test_no_bitrix_left(self, monitoring_button):
        """Битрикс убран из интерфейса — в меню его быть не должно."""
        texts = " ".join(b.text.lower()
                         for row in main_menu_keyboard(monitoring_button).inline_keyboard
                         for b in row)
        assert "битрикс" not in texts and "bitrix" not in texts
