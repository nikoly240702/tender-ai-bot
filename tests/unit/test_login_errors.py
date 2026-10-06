"""Человеческая страница ошибки входа в кабинет.

До этого было: при незаведённом пользователе кабинет отдавал голый текст
`web.Response(text=...)` без вёрстки, без кликабельной ссылки на бота, и
с несуществующим именем бота — @TenderSniperBot вместо реального
@TenderAI111_bot. Для случайного посетителя (например, рецензента по
ссылке на продукт) это выглядело как поломка сайта.
"""
import pytest

from cabinet.login_errors import BOT_USERNAME, resolve_login_error


@pytest.mark.unit
class TestResolveLoginError:
    def test_known_codes_have_title_and_message(self):
        for code in ('bot_not_configured', 'missing_params', 'invalid_auth',
                     'user_not_found'):
            ctx = resolve_login_error(code)
            assert ctx['title']
            assert ctx['message']

    def test_user_not_found_points_at_the_real_bot(self):
        """Это и была дыра: ссылка вела на бота, которого не существует."""
        ctx = resolve_login_error('user_not_found')
        assert BOT_USERNAME in ctx['message']
        assert 'TenderSniperBot' not in ctx['message']

    def test_user_not_found_offers_a_clickable_bot_link(self):
        ctx = resolve_login_error('user_not_found')
        assert ctx.get('bot_link') == f'https://t.me/{BOT_USERNAME}'

    def test_unknown_code_falls_back_to_something_sensible(self):
        """Параметр error можно подделать в URL — падать на этом нельзя."""
        ctx = resolve_login_error('что-то-незнакомое')
        assert ctx['title'] and ctx['message']

    def test_none_code_means_no_error(self):
        assert resolve_login_error(None) is None

    def test_real_bot_username_is_the_known_handle(self):
        assert BOT_USERNAME == 'TenderAI111_bot'
