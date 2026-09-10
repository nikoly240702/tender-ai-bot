from cabinet.team_service import _pick_active_company


def test_no_companies_returns_none():
    assert _pick_active_company([], None) is None


def test_single_company_returned_regardless_of_active_id():
    company = {'id': 1, 'name': 'Solo'}
    assert _pick_active_company([company], None) == company
    assert _pick_active_company([company], 999) == company


def test_multi_company_uses_valid_active_id():
    a = {'id': 1, 'name': 'A'}
    b = {'id': 2, 'name': 'B'}
    assert _pick_active_company([a, b], 2) == b


def test_multi_company_falls_back_to_oldest_when_active_id_invalid():
    a = {'id': 1, 'name': 'A'}
    b = {'id': 2, 'name': 'B'}
    assert _pick_active_company([a, b], 999) == a


def test_multi_company_falls_back_to_oldest_when_no_active_id():
    a = {'id': 1, 'name': 'A'}
    b = {'id': 2, 'name': 'B'}
    assert _pick_active_company([a, b], None) == a
