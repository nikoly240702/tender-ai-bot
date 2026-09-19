"""Клиент Yandex Search API v2.

Зачем Яндекс, а не зарубежный сервис поиска: задача — искать товары по
российским сайтам поставщиков, и покрытие рунета здесь решает всё. Плюс
оплата в рублях и никаких гео-блокировок (в отличие от OpenAI, ради
которого пришлось заводить отдельный прокси).

Обходить страницы сторонним сервисом не нужно: проверено 18.09.2026 —
наш сервер сам открывает сайты поставщиков напрямую (vasko.ru, komus.ru
отдают полноценные страницы). Блокирует нас только zakupki.gov.ru.
Крупные маркетплейсы (Ozon, Яндекс.Маркет) отвечают капчей «Вы не робот?»
и через этот API как источник цен недоступны.

По умолчанию работает ОТЛОЖЕННЫЙ режим (/v2/web/searchAsync): он стоит
30.5 ₽ за 1000 запросов против 488 ₽ у синхронного — в 16 раз дешевле.
В документации у него пугающая оговорка «от пяти минут до нескольких
часов», но замер на боевом 19.09.2026 дал 11 секунд: это верхняя
граница SLA, а не обычное время. Если отложенный всё же не успел,
переспрашиваем синхронно, чтобы не остаться вовсе без результата.
Режим переключается переменной YANDEX_SEARCH_MODE (async | sync).
"""
import base64
import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional

# Разбираем XML через defusedxml: в выдаче приезжают заголовки и сниппеты
# чужих страниц, то есть содержимое, на которое влияет посторонний, а
# stdlib-парсер уязвим к entity-атакам (в том числе «billion laughs»).
try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring
except ImportError:  # окружение без defusedxml — работаем, но предупреждаем
    logging.getLogger(__name__).warning(
        "defusedxml не установлен, XML выдачи разбирается небезопасным парсером")
    _xml_fromstring = ET.fromstring

logger = logging.getLogger(__name__)

SEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"
DEFAULT_TIMEOUT = 20
# 225 — Россия. Региональная привязка влияет на выдачу товарных запросов.
DEFAULT_REGION = "225"


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str

    @property
    def domain(self) -> str:
        from urllib.parse import urlparse
        try:
            return urlparse(self.url).netloc.replace('www.', '')
        except Exception:
            return ''


class YandexSearchError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(os.getenv('YANDEX_SEARCH_API_KEY') and os.getenv('YANDEX_SEARCH_FOLDER_ID'))


def _text(node: Optional[ET.Element]) -> str:
    """Весь текст узла вместе с вложенными тегами.

    В сниппетах Яндекс подсвечивает совпадения тегами <hlword>, поэтому
    node.text обрезал бы фразу на первом же выделенном слове.
    """
    if node is None:
        return ''
    return ' '.join(''.join(node.itertext()).split())


def parse_response_xml(xml_text: str) -> List[SearchResult]:
    """Разбирает XML выдачи Яндекса в список результатов.

    Структура: response/results/grouping/group/doc, внутри url, title и
    passages. Ошибку поиска Яндекс отдаёт тем же XML с тегом <error>.
    """
    root = _xml_fromstring(xml_text)

    error = root.find('.//error')
    if error is not None:
        raise YandexSearchError(_text(error) or 'ошибка поиска без описания')

    results: List[SearchResult] = []
    for doc in root.findall('.//doc'):
        url = _text(doc.find('url'))
        if not url:
            continue
        passages = doc.find('.//passages')
        snippet = _text(passages) if passages is not None else _text(doc.find('headline'))
        results.append(SearchResult(
            url=url,
            title=_text(doc.find('title')),
            snippet=snippet,
        ))
    return results


def _decode_raw(raw: str) -> str:
    """rawData приходит base64 (protobuf bytes в JSON кодируется так)."""
    try:
        return base64.b64decode(raw).decode('utf-8', errors='replace')
    except Exception:
        # Если вдруг придёт уже готовый XML — не падаем.
        return raw


def search_sync(query: str, limit: int = 10,
                timeout: int = DEFAULT_TIMEOUT) -> List[SearchResult]:
    """Синхронный поиск. Блокирующий — вызывать через run_in_executor."""
    api_key = os.getenv('YANDEX_SEARCH_API_KEY')
    folder_id = os.getenv('YANDEX_SEARCH_FOLDER_ID')
    if not api_key or not folder_id:
        raise YandexSearchError(
            'Не задан YANDEX_SEARCH_API_KEY или YANDEX_SEARCH_FOLDER_ID')

    import requests

    resp = requests.post(
        SEARCH_URL,
        json=_payload(query, limit, folder_id),
        headers={'Authorization': f'Api-Key {api_key}'},
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise YandexSearchError(f'HTTP {resp.status_code}: {resp.text[:200]}')

    raw = (resp.json() or {}).get('rawData')
    if not raw:
        raise YandexSearchError('пустой rawData в ответе')

    return parse_response_xml(_decode_raw(raw))[:limit]


SEARCH_ASYNC_URL = "https://searchapi.api.cloud.yandex.net/v2/web/searchAsync"
OPERATION_URL = "https://operation.api.cloud.yandex.net/operations/{}"

# Отложенный режим стоит 30.5 ₽ за 1000 запросов против 488 ₽ у
# синхронного — в 16 раз дешевле. Плата за это — ожидание: по
# документации «от пяти минут до нескольких часов». Подбор товара идёт
# фоновой задачей и результат складывается в карточку, поэтому минуты
# ожидания приемлемы, а вот часы — нет: дальше этого срока сдаёмся и
# (по настройке) переспрашиваем синхронно.
# Замер на боевом 19.09.2026: результат был готов за 11 секунд, то есть
# «пять минут» из документации — это верхняя граница SLA, а не норма.
# Поэтому опрашиваем часто вначале и разрежаем, если затянулось: так
# обычный запрос отдаётся почти как синхронный, но в 16 раз дешевле.
ASYNC_POLL_START = 3
ASYNC_POLL_MAX = 30
ASYNC_MAX_WAIT = 900  # 15 минут


def _payload(query: str, limit: int, folder_id: str) -> dict:
    return {
        'query': {
            'searchType': 'SEARCH_TYPE_RU',
            'queryText': query,
            'familyMode': 'FAMILY_MODE_MODERATE',
            'page': 0,
            'fixTypoMode': 'FIX_TYPO_MODE_ON',
        },
        'groupSpec': {
            'groupMode': 'GROUP_MODE_FLAT',
            'groupsOnPage': max(1, min(limit, 50)),
            'docsInGroup': 1,
        },
        'maxPassages': 3,
        'region': DEFAULT_REGION,
        'l10N': 'LOCALIZATION_RU',
        'folderId': folder_id,
        'responseFormat': 'FORMAT_XML',
    }


async def search_async(query: str, limit: int = 10,
                       max_wait: int = ASYNC_MAX_WAIT) -> List[SearchResult]:
    """Отложенный поиск: в 16 раз дешевле синхронного, но ждать дольше.

    Бросает YandexSearchError, если за max_wait результат не готов —
    вызывающий решает, ждать ли дальше или переспросить синхронно.
    """
    import asyncio

    import requests

    api_key = os.getenv('YANDEX_SEARCH_API_KEY')
    folder_id = os.getenv('YANDEX_SEARCH_FOLDER_ID')
    if not api_key or not folder_id:
        raise YandexSearchError(
            'Не задан YANDEX_SEARCH_API_KEY или YANDEX_SEARCH_FOLDER_ID')

    headers = {'Authorization': f'Api-Key {api_key}'}
    loop = asyncio.get_event_loop()

    def _start():
        r = requests.post(SEARCH_ASYNC_URL, json=_payload(query, limit, folder_id),
                          headers=headers, timeout=DEFAULT_TIMEOUT)
        if r.status_code != 200:
            raise YandexSearchError(f'HTTP {r.status_code}: {r.text[:200]}')
        return (r.json() or {}).get('id')

    operation_id = await loop.run_in_executor(None, _start)
    if not operation_id:
        raise YandexSearchError('API не вернул идентификатор операции')

    def _poll():
        r = requests.get(OPERATION_URL.format(operation_id),
                         headers=headers, timeout=DEFAULT_TIMEOUT)
        if r.status_code != 200:
            raise YandexSearchError(f'HTTP {r.status_code}: {r.text[:200]}')
        return r.json() or {}

    waited = 0
    delay = ASYNC_POLL_START
    while waited < max_wait:
        await asyncio.sleep(delay)
        waited += delay
        delay = min(delay * 2, ASYNC_POLL_MAX)
        data = await loop.run_in_executor(None, _poll)
        if data.get('error'):
            raise YandexSearchError(str(data['error'])[:200])
        if data.get('done'):
            raw = (data.get('response') or {}).get('rawData')
            if not raw:
                raise YandexSearchError('операция завершена, но rawData пуст')
            return parse_response_xml(_decode_raw(raw))[:limit]

    raise YandexSearchError(f'результат не готов за {max_wait} с')


async def search(query: str, limit: int = 10) -> List[SearchResult]:
    """Поиск в режиме из YANDEX_SEARCH_MODE.

    По умолчанию отложенный: он в 16 раз дешевле, а подбор и так идёт
    фоном. Если отложенный не успел — один раз переспрашиваем
    синхронно, чтобы пользователь не остался вовсе без результата.
    """
    import asyncio

    mode = (os.getenv('YANDEX_SEARCH_MODE') or 'async').lower()

    if mode == 'async':
        try:
            return await search_async(query, limit)
        except YandexSearchError as e:
            logger.warning(f'Отложенный поиск не удался ({e}), пробуем синхронный')

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: search_sync(query, limit))
