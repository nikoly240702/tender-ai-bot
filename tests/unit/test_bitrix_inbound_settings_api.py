import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from aiohttp.test_utils import make_mocked_request

import cabinet.api as api


@pytest.mark.asyncio
async def test_get_bitrix_inbound_settings_returns_url_with_secret(monkeypatch):
    async def fake_secret(company_id):
        return 'abc123'
    monkeypatch.setattr('cabinet.bitrix_sync.get_or_create_inbound_secret', fake_secret)
    monkeypatch.setattr('bot.config.BotConfig.WEBAPP_BASE_URL', 'https://example.app')

    request = make_mocked_request('GET', '/cabinet/api/settings/bitrix-inbound')
    request['company'] = {'id': 5, 'owner_user_id': 1}

    resp = await api._get_bitrix_inbound_settings_impl(request)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_rotate_bitrix_inbound_secret_returns_new_url(monkeypatch):
    async def fake_rotate(company_id):
        return 'new-secret'
    monkeypatch.setattr('cabinet.bitrix_sync.rotate_inbound_secret', fake_rotate)
    monkeypatch.setattr('bot.config.BotConfig.WEBAPP_BASE_URL', 'https://example.app')

    request = make_mocked_request('POST', '/cabinet/api/settings/bitrix-inbound/rotate')
    request['company'] = {'id': 5, 'owner_user_id': 1}

    resp = await api._rotate_bitrix_inbound_secret_impl(request)
    assert resp.status == 200
