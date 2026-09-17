"""Изоляция данных между компаниями.

Аудит 17.09.2026 нашёл места, где принадлежность к компании не
проверялась. Здесь закрепляются именно они — все дыры такого рода
выглядят одинаково: объект берётся по числовому id, а связь с компанией
не проверяется, потому что у самого объекта company_id нет.
"""
import pytest

from cabinet import holodilnik_service


@pytest.mark.unit
class TestTaskStatusIsolation:
    """GET /api/pipeline/cards/{id}/holodilnik-status отдавал статус по
    одному task_id, без всякой проверки прав. Токен угадать трудно, но
    отсутствие проверки — это не защита."""

    def setup_method(self):
        holodilnik_service._TASKS.clear()

    def teardown_method(self):
        holodilnik_service._TASKS.clear()

    def test_owner_sees_the_task(self):
        holodilnik_service._TASKS['tok'] = {'company_id': 57, 'status': 'running'}
        got = holodilnik_service.get_status('tok', 57)
        assert got is not None
        assert got['status'] == 'running'

    def test_another_company_gets_nothing(self):
        holodilnik_service._TASKS['tok'] = {'company_id': 57, 'status': 'running'}
        assert holodilnik_service.get_status('tok', 999) is None

    def test_foreign_task_is_indistinguishable_from_missing(self):
        """Разный ответ на «чужая» и «не существует» сам по себе
        подсказывает, какие токены валидны."""
        holodilnik_service._TASKS['tok'] = {'company_id': 57, 'status': 'running'}
        assert holodilnik_service.get_status('tok', 999) is None
        assert holodilnik_service.get_status('нет-такого', 999) is None

    def test_company_id_is_not_leaked_to_the_client(self):
        holodilnik_service._TASKS['tok'] = {'company_id': 57, 'status': 'done'}
        got = holodilnik_service.get_status('tok', 57)
        assert 'company_id' not in got


@pytest.mark.unit
class TestChecklistGuardExists:
    """Пункты чек-листа не имеют своего company_id — принадлежность идёт
    через карточку. Маршруты приходят с id ПУНКТА, поэтому обычная
    проверка get_card(card_id, company_id) в обработчике невозможна, и её
    забыли: чужой пункт можно было отметить и удалить, подобрав число.

    Полноценная проверка требует БД; здесь фиксируем контракт сигнатур,
    чтобы company_id нельзя было снова потерять при рефакторинге.
    """

    def test_toggle_requires_company_id(self):
        import inspect
        from cabinet.pipeline_service import toggle_checklist
        params = inspect.signature(toggle_checklist).parameters
        assert 'company_id' in params
        # Обязательный, без значения по умолчанию — иначе вызывающий код
        # снова сможет «забыть» его передать, и проверка тихо отключится.
        assert params['company_id'].default is inspect.Parameter.empty

    def test_delete_requires_company_id(self):
        import inspect
        from cabinet.pipeline_service import delete_checklist
        params = inspect.signature(delete_checklist).parameters
        assert 'company_id' in params
        assert params['company_id'].default is inspect.Parameter.empty
