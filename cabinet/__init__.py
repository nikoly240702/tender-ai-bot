"""
Web Cabinet — веб-кабинет для управления профилем, тендерами и документами.

Монтируется на /cabinet/* в aiohttp-сервере health_check.py.
"""

from .routes import setup_cabinet_routes

__all__ = ['setup_cabinet_routes']
