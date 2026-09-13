"""Клиент интеграционного API Портала поставщиков (zakupki.mos.ru).

api.zakupki.mos.ru режет не-российские IP так же, как zakupki.gov.ru —
нужен тот же прокси-пул (PROXY_URL, PROXY_URL_2..5). Прокси-ротация тут
намеренно СВОЯ, небольшая копия того, что уже есть в
src/parsers/zakupki_rss_parser.py — не рефакторим тот файл (см. Global
Constraints плана).
"""
import base64
import json
import logging
import os
import re
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.zakupki.mos.ru/api/v2/auction/public/Search"
PROXY_ENV_VARS = ["PROXY_URL", "PROXY_URL_2", "PROXY_URL_3", "PROXY_URL_4", "PROXY_URL_5"]

# Маскирует user:pass@ в любом URL внутри текста — некоторые ошибки requests
# (например ProxyError) включают в текст полный URL прокси с credentials.
_CREDENTIALS_RE = re.compile(r"://[^/@\s]+@")


def _mask_proxy_host(proxy: str) -> str:
    """Хост прокси без credentials — как в src/parsers/zakupki_rss_parser.py
    (`host = p.split('@')[-1]`). Используем только это в логах, никогда сырой
    PROXY_URL целиком."""
    return proxy.split("@")[-1] if "@" in proxy else proxy


def _sanitize_error(e: Exception) -> str:
    """Текст исключения с замаскированными credentials — на случай если сам
    requests подставил в сообщение об ошибке полный URL прокси."""
    return _CREDENTIALS_RE.sub("://***@", str(e))


def decode_jwt_exp(token: str) -> Optional[int]:
    """Читает claim exp из JWT без проверки подписи — это наш собственный
    токен, а не непроверенный ввод пользователя; нужен только чтобы
    залогировать предупреждение о скором истечении."""
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("exp")
    except Exception:
        return None


class MosPortalClient:
    def __init__(self):
        self.token = (os.environ.get("PP_TOKEN") or "").strip()
        if not self.token:
            raise RuntimeError("PP_TOKEN не задан")
        self._proxies = [os.environ.get(v, "").strip() for v in PROXY_ENV_VARS]
        self._proxies = [p for p in self._proxies if p]

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def search_auctions_sync(self, publish_date_from: str, publish_date_to: str,
                             skip: int = 0, take: int = 200) -> Dict[str, Any]:
        query = {
            "filter": {"publishDate": {"start": publish_date_from, "end": publish_date_to}},
            "skip": skip,
            "take": take,
        }
        params = {"query": json.dumps(query, ensure_ascii=False)}
        sessions = self._proxies or [None]
        last_error = None
        for idx, proxy in enumerate(sessions):
            proxies = {"http": proxy, "https": proxy} if proxy else None
            proxy_label = f"#{idx + 1} ({_mask_proxy_host(proxy)})" if proxy else "#1 (напрямую)"
            try:
                r = requests.get(BASE_URL, headers=self._headers(), params=params,
                                 proxies=proxies, timeout=15)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_error = _sanitize_error(e)
                logger.warning(f"Портал поставщиков: прокси {proxy_label} — {last_error}")
                continue
        raise RuntimeError(f"Все прокси недоступны для Портала поставщиков: {last_error}")

    async def search_auctions(self, publish_date_from: str, publish_date_to: str,
                              skip: int = 0, take: int = 200) -> Dict[str, Any]:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.search_auctions_sync, publish_date_from, publish_date_to, skip, take,
        )
