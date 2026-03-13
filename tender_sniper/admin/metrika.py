"""
Yandex Metrika API integration for admin panel analytics.

Provides traffic data, ad spend, and conversion metrics.
"""

import os
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

YANDEX_METRIKA_TOKEN = os.getenv("YANDEX_METRIKA_TOKEN", "")
YANDEX_METRIKA_COUNTER_ID = os.getenv("YANDEX_METRIKA_COUNTER_ID", "107009876")
METRIKA_API_BASE = "https://api-metrika.yandex.net/stat/v1/data"

# In-memory cache with TTL
_cache: dict = {}
_CACHE_TTL = 900  # 15 minutes


def _cache_key(method: str, params: dict) -> str:
    return f"{method}:{sorted(params.items())}"


def _get_cached(key: str):
    if key in _cache:
        value, ts = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return value
        del _cache[key]
    return None


def _set_cached(key: str, value):
    _cache[key] = (value, time.time())


class MetrikaService:
    """Wrapper around Yandex Metrika API."""

    def __init__(self):
        self.token = YANDEX_METRIKA_TOKEN
        self.counter_id = YANDEX_METRIKA_COUNTER_ID

    @property
    def is_configured(self) -> bool:
        return bool(self.token)

    async def _request(self, params: dict) -> Optional[dict]:
        if not self.is_configured:
            return None

        cache_key = _cache_key("metrika", params)
        cached = _get_cached(cache_key)
        if cached is not None:
            return cached

        headers = {"Authorization": f"OAuth {self.token}"}
        params["ids"] = self.counter_id

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    METRIKA_API_BASE, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _set_cached(cache_key, data)
                        return data
                    else:
                        text = await resp.text()
                        logger.error(f"Metrika API error {resp.status}: {text}")
                        return None
        except Exception as e:
            logger.error(f"Metrika API request failed: {e}")
            return None

    async def get_traffic_summary(self, date_from: str, date_to: str) -> dict:
        """Get visits, users, bounce rate, avg visit duration."""
        data = await self._request({
            "date1": date_from,
            "date2": date_to,
            "metrics": "ym:s:visits,ym:s:users,ym:s:bounceRate,ym:s:avgVisitDurationSeconds",
        })
        if not data or not data.get("totals"):
            return {"visits": 0, "users": 0, "bounce_rate": 0, "avg_duration": 0}

        totals = data["totals"]
        return {
            "visits": int(totals[0]) if len(totals) > 0 else 0,
            "users": int(totals[1]) if len(totals) > 1 else 0,
            "bounce_rate": round(totals[2], 1) if len(totals) > 2 else 0,
            "avg_duration": int(totals[3]) if len(totals) > 3 else 0,
        }

    async def get_traffic_sources(self, date_from: str, date_to: str) -> list:
        """Get traffic breakdown by source type."""
        data = await self._request({
            "date1": date_from,
            "date2": date_to,
            "metrics": "ym:s:visits",
            "dimensions": "ym:s:lastTrafficSource",
        })
        if not data or not data.get("data"):
            return []

        sources = []
        source_names = {
            "ad": "Реклама (Директ)",
            "organic": "Органика",
            "social": "Соцсети",
            "referral": "Рефералы",
            "direct": "Прямые заходы",
            "internal": "Внутренние",
            "recommendation": "Рекомендации",
            "messenger": "Мессенджеры",
            "email": "Email",
        }
        for row in data["data"]:
            key = row["dimensions"][0]["id"] if row.get("dimensions") else "unknown"
            sources.append({
                "name": source_names.get(key, key),
                "key": key,
                "visits": int(row["metrics"][0]) if row.get("metrics") else 0,
            })
        return sorted(sources, key=lambda x: -x["visits"])

    async def get_goal_conversions(self, date_from: str, date_to: str) -> dict:
        """Get cta_click goal conversions."""
        data = await self._request({
            "date1": date_from,
            "date2": date_to,
            "metrics": "ym:s:visits,ym:s:goalReaches",
        })
        if not data or not data.get("totals"):
            return {"visits": 0, "goal_reaches": 0, "conversion_rate": 0}

        totals = data["totals"]
        visits = int(totals[0]) if len(totals) > 0 else 0
        goals = int(totals[1]) if len(totals) > 1 else 0
        rate = round((goals / visits * 100), 2) if visits > 0 else 0
        return {"visits": visits, "goal_reaches": goals, "conversion_rate": rate}

    async def get_ad_spend(self, date_from: str, date_to: str) -> float:
        """Get ad spend from Yandex Direct (linked to Metrika)."""
        data = await self._request({
            "date1": date_from,
            "date2": date_to,
            "metrics": "ym:s:expenses<currency>RUB",
            "dimensions": "ym:s:lastTrafficSource",
            "filters": "ym:s:lastTrafficSource=='ad'",
        })
        if not data or not data.get("totals"):
            return 0.0
        return round(data["totals"][0], 2) if data["totals"] else 0.0

    async def get_daily_visitors(self, date_from: str, date_to: str) -> list:
        """Get daily visitors for chart."""
        data = await self._request({
            "date1": date_from,
            "date2": date_to,
            "metrics": "ym:s:visits,ym:s:users",
            "dimensions": "ym:s:date",
            "sort": "ym:s:date",
        })
        if not data or not data.get("data"):
            return []

        result = []
        for row in data["data"]:
            date_str = row["dimensions"][0]["name"] if row.get("dimensions") else ""
            result.append({
                "date": date_str,
                "visits": int(row["metrics"][0]) if row.get("metrics") else 0,
                "users": int(row["metrics"][1]) if len(row.get("metrics", [])) > 1 else 0,
            })
        return result
