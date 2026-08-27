import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from aioresponses import aioresponses

from bot.handlers.bitrix24 import (
    get_bitrix24_deal, list_deal_timeline_comments, batch_list_deal_comments,
)

WEBHOOK = 'https://portal.bitrix24.ru/rest/1/token/'


@pytest.mark.asyncio
async def test_get_bitrix24_deal_returns_result():
    with aioresponses() as m:
        m.post(WEBHOOK + 'crm.deal.get.json', payload={'result': {'ID': '5', 'TITLE': 'Deal'}})
        deal = await get_bitrix24_deal(WEBHOOK, '5')
    assert deal == {'ID': '5', 'TITLE': 'Deal'}


@pytest.mark.asyncio
async def test_get_bitrix24_deal_returns_none_on_http_error():
    with aioresponses() as m:
        m.post(WEBHOOK + 'crm.deal.get.json', status=500)
        deal = await get_bitrix24_deal(WEBHOOK, '5')
    assert deal is None


@pytest.mark.asyncio
async def test_get_bitrix24_deal_returns_none_on_exception():
    with aioresponses() as m:
        m.post(WEBHOOK + 'crm.deal.get.json', exception=ConnectionError('boom'))
        deal = await get_bitrix24_deal(WEBHOOK, '5')
    assert deal is None


@pytest.mark.asyncio
async def test_list_deal_timeline_comments_no_since():
    with aioresponses() as m:
        m.get(
            WEBHOOK + 'crm.timeline.comment.list.json?filter%5BENTITY_ID%5D=5&filter%5BENTITY_TYPE%5D=deal',
            payload={'result': [{'ID': '1', 'COMMENT': 'hi'}]},
        )
        comments = await list_deal_timeline_comments(WEBHOOK, '5')
    assert comments == [{'ID': '1', 'COMMENT': 'hi'}]


@pytest.mark.asyncio
async def test_list_deal_timeline_comments_with_since():
    with aioresponses() as m:
        m.get(
            WEBHOOK + 'crm.timeline.comment.list.json'
            '?filter%5BENTITY_ID%5D=5&filter%5BENTITY_TYPE%5D=deal&filter%5B%3EID%5D=10',
            payload={'result': []},
        )
        comments = await list_deal_timeline_comments(WEBHOOK, '5', since_id=10)
    assert comments == []


@pytest.mark.asyncio
async def test_batch_list_deal_comments_empty_input_returns_empty():
    result = await batch_list_deal_comments(WEBHOOK, {})
    assert result == {}


@pytest.mark.asyncio
async def test_batch_list_deal_comments_maps_by_deal_id():
    with aioresponses() as m:
        m.post(WEBHOOK + 'batch.json', payload={
            'result': {'result': {'5': [{'ID': '1'}], '7': []}},
        })
        result = await batch_list_deal_comments(WEBHOOK, {'5': 0, '7': 3})
    assert result == {'5': [{'ID': '1'}], '7': []}


@pytest.mark.asyncio
async def test_batch_list_deal_comments_chunks_over_50():
    deal_since = {str(i): 0 for i in range(75)}
    calls = []

    def _payload_for_chunk(chunk_size):
        return {'result': {'result': {str(i): [] for i in range(chunk_size)}}}

    with aioresponses() as m:
        # первый батч — 50 сделок, второй — оставшиеся 25
        m.post(WEBHOOK + 'batch.json', payload=_payload_for_chunk(50))
        m.post(WEBHOOK + 'batch.json', payload=_payload_for_chunk(25))
        result = await batch_list_deal_comments(WEBHOOK, deal_since)
    assert len(result) >= 50  # обе пачки учтены (ключи из двух ответов объединяются в результат ниже)
