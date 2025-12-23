"""
Tender Sniper Admin Dashboard.

FastAPI-based admin panel for managing users, filters, and notifications.

Usage:
    uvicorn tender_sniper.admin.app:app --host 0.0.0.0 --port 8080

Or run via script:
    python scripts/run_admin.py

Pages:
- / - Dashboard with statistics
- /users - User management
- /filters - Filter management
- /notifications - Notification history

API Endpoints:
- /api/stats/hourly - Hourly notification stats
- /api/stats/daily - Daily notification stats
- /health - Health check
"""

from .app import app

__all__ = ['app']
