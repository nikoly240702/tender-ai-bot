"""
Tender Sniper - Real-time Tender Monitoring System

Status: PLACEHOLDER - Implementation pending
Phase: 2 (Target: Week 1-2 of development)

This package will contain the real-time tender monitoring and notification system.

Enable via: config/features.yaml → tender_sniper.enabled: true

Modules:
- bot/           - Enhanced Telegram bot with subscription logic
- parser/        - Real-time parser for zakupki.gov.ru
- matching/      - Smart matching engine for tender criteria
- notifications/ - Instant notification system
- payments/      - Payment processing and subscription management
- database/      - Database models and migrations
- admin/         - Admin dashboard (web interface)
- api/           - REST API for external integrations

Copyright (c) 2024
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
__version__ = '0.1.0-placeholder'
__author__ = 'Tender AI Bot Team'