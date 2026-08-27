import sys
import asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from aiohttp import streams
from aiohttp.base_protocol import BaseProtocol
from aiohttp.test_utils import make_mocked_request

import bot.health_check as health_check


def _form_request(path: str, body: bytes):
    """make_mocked_request не принимает голые bytes как payload для
    await request.post() — нужен настоящий StreamReader с фидом данных."""
    loop = asyncio.get_event_loop()
    protocol = BaseProtocol(loop=loop)
    stream = streams.StreamReader(protocol, limit=2**16, loop=loop)
    stream.feed_data(body)
    stream.feed_eof()
    return make_mocked_request(
        'POST', path,
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Content-Length': str(len(body))},
        payload=stream,
    )


@pytest.mark.asyncio
async def test_events_handler_rejects_missing_secret(monkeypatch):
    async def fake_verify(company_id, secret):
        return False
    monkeypatch.setattr('cabinet.bitrix_sync.verify_inbound_secret', fake_verify)

    request = _form_request('/webhook/bitrix24/events?c=1&t=wrong', b'')
    resp = await health_check.bitrix24_events_handler(request)
    assert resp.status == 401


@pytest.mark.asyncio
async def test_events_handler_missing_company_param_is_400():
    request = _form_request('/webhook/bitrix24/events?t=abc', b'')
    resp = await health_check.bitrix24_events_handler(request)
    assert resp.status == 400


@pytest.mark.asyncio
async def test_events_handler_dispatches_background_task(monkeypatch):
    async def fake_verify(company_id, secret):
        return True
    monkeypatch.setattr('cabinet.bitrix_sync.verify_inbound_secret', fake_verify)

    dispatched = []
    async def fake_handle_deal_event(company_id, event, deal_id):
        dispatched.append((company_id, event, deal_id))
    monkeypatch.setattr('cabinet.bitrix_sync.handle_deal_event', fake_handle_deal_event)

    body = b'event=ONCRMDEALUPDATE&data%5BFIELDS%5D%5BID%5D=42&auth%5Bdomain%5D=x.bitrix24.ru'
    request = _form_request('/webhook/bitrix24/events?c=1&t=good', body)
    resp = await health_check.bitrix24_events_handler(request)
    assert resp.status == 200
    # фоновая задача — даём event loop'у шанс её выполнить
    await asyncio.sleep(0)
    assert dispatched == [(1, 'ONCRMDEALUPDATE', '42')]
