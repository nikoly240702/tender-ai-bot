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
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.zakupki.mos.ru/api/v2/auction/public/Search"
PROXY_ENV_VARS = ["PROXY_URL", "PROXY_URL_2", "PROXY_URL_3", "PROXY_URL_4", "PROXY_URL_5"]


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
            "filter": {"publishDate": {"from": publish_date_from, "to": publish_date_to}},
            "skip": skip,
            "take": take,
        }
        params = {"query": json.dumps(query, ensure_ascii=False)}
        sessions = self._proxies or [None]
        last_error = None
        for proxy in sessions:
            proxies = {"http": proxy, "https": proxy} if proxy else None
            try:
                r = requests.get(BASE_URL, headers=self._headers(), params=params,
                                 proxies=proxies, timeout=15)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_error = e
                logger.warning(f"Портал поставщиков: прокси {proxy or 'напрямую'} — {e}")
                continue
        raise RuntimeError(f"Все прокси недоступны для Портала поставщиков: {last_error}")

    async def search_auctions(self, publish_date_from: str, publish_date_to: str,
                              skip: int = 0, take: int = 200) -> Dict[str, Any]:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.search_auctions_sync, publish_date_from, publish_date_to, skip, take,
        )
