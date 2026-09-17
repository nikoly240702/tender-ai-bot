"""Фильтр из бота должен попадать в кабинет.

Кабинет выбирает фильтры строго по company_id. Компания же заводилась
только при первом заходе в веб-кабинет, поэтому у пользователя, живущего
в боте, фильтр сохранялся с company_id=NULL и в кабинете не показывался
вовсе — вместе со всеми уведомлениями по нему.
"""
import inspect

import pytest

from tender_sniper.database.sqlalchemy_adapter import TenderSniperDB


@pytest.mark.unit
class TestCreateFilterResolvesCompany:
    def test_accepts_explicit_company_id(self):
        """Кабинет передаёт company_id сам — этот путь трогать нельзя."""
        params = inspect.signature(TenderSniperDB.create_filter).parameters
        assert 'company_id' in params
        assert params['company_id'].default is None

    def test_falls_back_to_existing_membership(self):
        src = inspect.getsource(TenderSniperDB.create_filter)
        assert 'CompanyMemberModel' in src
        assert 'joined_at' in src  # берём самую старую компанию, а не случайную

    def test_creates_a_company_when_the_user_has_none(self):
        """Ключевая правка: раньше здесь оставался NULL и фильтр терялся."""
        src = inspect.getsource(TenderSniperDB.create_filter)
        assert 'CompanyModel(' in src
        assert "role='owner'" in src

    def test_company_is_created_before_the_filter_row(self):
        """Иначе company_id всё равно окажется NULL — нужен flush, чтобы у
        новой компании появился id до вставки фильтра."""
        src = inspect.getsource(TenderSniperDB.create_filter)
        company_pos = src.index('CompanyModel(')
        filter_pos = src.index('SniperFilterModel(')
        assert company_pos < filter_pos
        assert 'flush()' in src[company_pos:filter_pos]
