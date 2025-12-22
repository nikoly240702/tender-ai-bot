"""
Redis Caching Layer.

Кэширование результатов поиска и данных тендеров.
Fallback на in-memory cache если Redis недоступен.

Feature flag: redis_cache (config/features.yaml)
"""

import os
import json
import logging
import hashlib
from typing import Any, Optional
from datetime import timedelta
from functools import lru_cache

logger = logging.getLogger(__name__)

# Try to import Redis
try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("⚠️ redis package not installed. Using in-memory cache fallback.")


# In-memory cache fallback
_memory_cache = {}


class CacheConfig:
    """Cache configuration."""

    # Default TTL values (seconds)
    TENDER_TTL = 3600        # 1 hour
    SEARCH_TTL = 300         # 5 minutes
    USER_TTL = 1800          # 30 minutes
    FILTER_TTL = 600         # 10 minutes

    # Redis connection
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    REDIS_MAX_CONNECTIONS = 10

    # Key prefixes
    PREFIX_TENDER = 'tender:'
    PREFIX_SEARCH = 'search:'
    PREFIX_USER = 'user:'
    PREFIX_FILTER = 'filter:'


class TenderCache:
    """
    Unified cache interface.

    Uses Redis when available, falls back to in-memory cache.
    """

    def __init__(self):
        """Initialize cache."""
        self._redis: Optional[aioredis.Redis] = None
        self._connected = False

    async def connect(self) -> bool:
        """Connect to Redis."""
        if not REDIS_AVAILABLE:
            logger.info("ℹ️ Redis not available, using in-memory cache")
            return False

        try:
            self._redis = aioredis.from_url(
                CacheConfig.REDIS_URL,
                max_connections=CacheConfig.REDIS_MAX_CONNECTIONS,
                decode_responses=True
            )
            # Test connection
            await self._redis.ping()
            self._connected = True
            logger.info("✅ Redis cache connected")
            return True
        except Exception as e:
            logger.warning(f"⚠️ Redis connection failed: {e}. Using in-memory cache.")
            self._connected = False
            return False

    async def disconnect(self):
        """Disconnect from Redis."""
        if self._redis:
            await self._redis.close()
            self._connected = False
            logger.info("Redis cache disconnected")

    @property
    def is_connected(self) -> bool:
        """Check if Redis is connected."""
        return self._connected

    # ============================================
    # Core Cache Operations
    # ============================================

    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None
        """
        if self._connected:
            try:
                value = await self._redis.get(key)
                if value:
                    return json.loads(value)
            except Exception as e:
                logger.warning(f"Redis get error: {e}")

        # Fallback to memory cache
        return _memory_cache.get(key)

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int = CacheConfig.TENDER_TTL
    ) -> bool:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl: Time to live in seconds

        Returns:
            True if successful
        """
        serialized = json.dumps(value, ensure_ascii=False, default=str)

        if self._connected:
            try:
                await self._redis.setex(key, ttl, serialized)
                return True
            except Exception as e:
                logger.warning(f"Redis set error: {e}")

        # Fallback to memory cache (no TTL in simple implementation)
        _memory_cache[key] = value
        return True

    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if self._connected:
            try:
                await self._redis.delete(key)
            except Exception as e:
                logger.warning(f"Redis delete error: {e}")

        _memory_cache.pop(key, None)
        return True

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        if self._connected:
            try:
                return await self._redis.exists(key)
            except Exception as e:
                logger.warning(f"Redis exists error: {e}")

        return key in _memory_cache

    async def clear_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern.

        Args:
            pattern: Key pattern (e.g., 'tender:*')

        Returns:
            Number of deleted keys
        """
        if self._connected:
            try:
                keys = await self._redis.keys(pattern)
                if keys:
                    return await self._redis.delete(*keys)
            except Exception as e:
                logger.warning(f"Redis clear_pattern error: {e}")

        # Memory cache fallback
        count = 0
        to_delete = [k for k in _memory_cache if k.startswith(pattern.replace('*', ''))]
        for k in to_delete:
            del _memory_cache[k]
            count += 1
        return count

    # ============================================
    # Tender Cache Operations
    # ============================================

    async def cache_tender(self, tender_number: str, tender_data: dict) -> bool:
        """Cache tender data."""
        key = f"{CacheConfig.PREFIX_TENDER}{tender_number}"
        return await self.set(key, tender_data, CacheConfig.TENDER_TTL)

    async def get_tender(self, tender_number: str) -> Optional[dict]:
        """Get cached tender data."""
        key = f"{CacheConfig.PREFIX_TENDER}{tender_number}"
        return await self.get(key)

    # ============================================
    # Search Cache Operations
    # ============================================

    def _make_search_key(self, keywords: list, filters: dict = None) -> str:
        """Generate cache key for search query."""
        data = {
            'keywords': sorted(keywords),
            'filters': filters or {}
        }
        hash_input = json.dumps(data, sort_keys=True, ensure_ascii=False)
        hash_value = hashlib.md5(hash_input.encode()).hexdigest()[:16]
        return f"{CacheConfig.PREFIX_SEARCH}{hash_value}"

    async def cache_search_results(
        self,
        keywords: list,
        results: list,
        filters: dict = None
    ) -> bool:
        """Cache search results."""
        key = self._make_search_key(keywords, filters)
        return await self.set(key, results, CacheConfig.SEARCH_TTL)

    async def get_search_results(
        self,
        keywords: list,
        filters: dict = None
    ) -> Optional[list]:
        """Get cached search results."""
        key = self._make_search_key(keywords, filters)
        return await self.get(key)

    # ============================================
    # User Cache Operations
    # ============================================

    async def cache_user_data(self, user_id: int, data: dict) -> bool:
        """Cache user data."""
        key = f"{CacheConfig.PREFIX_USER}{user_id}"
        return await self.set(key, data, CacheConfig.USER_TTL)

    async def get_user_data(self, user_id: int) -> Optional[dict]:
        """Get cached user data."""
        key = f"{CacheConfig.PREFIX_USER}{user_id}"
        return await self.get(key)

    async def invalidate_user_cache(self, user_id: int) -> bool:
        """Invalidate user cache."""
        key = f"{CacheConfig.PREFIX_USER}{user_id}"
        return await self.delete(key)

    # ============================================
    # Filter Cache Operations
    # ============================================

    async def cache_filter_data(self, filter_id: int, data: dict) -> bool:
        """Cache filter data."""
        key = f"{CacheConfig.PREFIX_FILTER}{filter_id}"
        return await self.set(key, data, CacheConfig.FILTER_TTL)

    async def get_filter_data(self, filter_id: int) -> Optional[dict]:
        """Get cached filter data."""
        key = f"{CacheConfig.PREFIX_FILTER}{filter_id}"
        return await self.get(key)

    async def invalidate_filter_cache(self, filter_id: int) -> bool:
        """Invalidate filter cache."""
        key = f"{CacheConfig.PREFIX_FILTER}{filter_id}"
        return await self.delete(key)

    # ============================================
    # Statistics
    # ============================================

    async def get_stats(self) -> dict:
        """Get cache statistics."""
        stats = {
            'backend': 'redis' if self._connected else 'memory',
            'connected': self._connected,
        }

        if self._connected:
            try:
                info = await self._redis.info('memory')
                stats['memory_used'] = info.get('used_memory_human', 'N/A')
                stats['keys_count'] = await self._redis.dbsize()
            except Exception as e:
                logger.warning(f"Redis stats error: {e}")
                stats['error'] = str(e)
        else:
            stats['keys_count'] = len(_memory_cache)
            stats['memory_used'] = 'N/A (in-memory)'

        return stats

    def clear_memory_cache(self):
        """Clear in-memory cache."""
        global _memory_cache
        _memory_cache.clear()
        logger.info("In-memory cache cleared")


# Singleton instance
cache = TenderCache()


# Convenience functions
async def init_cache() -> bool:
    """Initialize cache connection."""
    return await cache.connect()


async def close_cache():
    """Close cache connection."""
    await cache.disconnect()


async def get_cache() -> TenderCache:
    """Get cache instance."""
    return cache
