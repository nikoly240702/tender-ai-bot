"""
Tender Sniper - Real-time Tender Monitoring System

Status: ✅ IMPLEMENTED (Phase 2 - Week 1)

Enable via config/features.yaml:
    tender_sniper:
      enabled: true
      components:
        realtime_parser: true
        smart_matching: true
        instant_notifications: true

This package contains the real-time tender monitoring and notification system.

Components:
- ✅ database/      - SQLite schema, subscription plans, filters
- ✅ parser/        - Real-time RSS parser with callback system
- ✅ matching/      - Smart matching engine with scoring (0-100)
- ✅ notifications/ - Telegram notifier with quota management
- ✅ service.py     - Main coordinator service
- 🚧 payments/      - Payment processing (YooKassa) - TODO
- 🚧 bot/           - Enhanced Telegram bot handlers - TODO
- 📋 admin/         - Admin dashboard - Planned
- 📋 api/           - REST API - Planned

Quick Start:
    from tender_sniper.service import TenderSniperService
    import asyncio

    async def main():
        service = TenderSniperService(bot_token="YOUR_TOKEN")
        await service.initialize()
        await service.start()

    asyncio.run(main())

See tender_sniper/README.md for full documentation.
"""

from pathlib import Path
import yaml

def is_enabled() -> bool:
    """Check if Tender Sniper is enabled in features config."""
    config_path = Path(__file__).parent.parent / 'config' / 'features.yaml'

    if not config_path.exists():
        return False

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            return config.get('tender_sniper', {}).get('enabled', False)
    except:
        return False

# Version info
__version__ = '0.1.0-mvp'
__author__ = 'Tender AI Bot Team'

__all__ = ['is_enabled', '__version__']
