"""
Общие фабрики клиента OpenAI.

После переезда хостинга на российский VPS прямые запросы к api.openai.com
с российского IP стали получать 403 unsupported_country_region_territory
(гео-блокировка провайдера, не наша ошибка и не квота). Решение — исходящий
прокси с выходом не из РФ, задаётся через OPENAI_PROXY_URL. Если переменная
не задана, клиент создаётся как раньше (без прокси) — совместимо со средами,
где блокировки нет (например, если сервис снова переедет на зарубежный хостинг).
"""

import os
from typing import Optional

import httpx
from openai import AsyncOpenAI, OpenAI


def _proxy_url() -> Optional[str]:
    return os.getenv('OPENAI_PROXY_URL') or None


def make_openai_client(api_key: str) -> OpenAI:
    proxy = _proxy_url()
    http_client = httpx.Client(proxy=proxy, timeout=60.0) if proxy else None
    return OpenAI(api_key=api_key, http_client=http_client)


def make_async_openai_client(api_key: str) -> AsyncOpenAI:
    proxy = _proxy_url()
    http_client = httpx.AsyncClient(proxy=proxy, timeout=60.0) if proxy else None
    return AsyncOpenAI(api_key=api_key, http_client=http_client)
