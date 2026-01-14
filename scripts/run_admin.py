#!/usr/bin/env python3
"""
Скрипт запуска админ-панели Tender Sniper.

Запуск: python scripts/run_admin.py
Или:    uvicorn tender_sniper.admin.app:app --host 0.0.0.0 --port 8080 --reload
"""

import os
import sys
from pathlib import Path

# Добавляем корень проекта
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Загружаем .env файл ПЕРЕД импортом других модулей
from dotenv import load_dotenv
load_dotenv(project_root / '.env')

import uvicorn


def main():
    """Запуск админ-панели."""
    host = os.getenv("ADMIN_HOST", "0.0.0.0")
    port = int(os.getenv("ADMIN_PORT", "8080"))
    reload_mode = os.getenv("ADMIN_RELOAD", "false").lower() == "true"

    print(f"""
╔══════════════════════════════════════════╗
║     Tender Sniper Admin Dashboard        ║
╠══════════════════════════════════════════╣
║  URL: http://{host}:{port}
║  Login: admin / tender2024
╚══════════════════════════════════════════╝
    """)

    uvicorn.run(
        "tender_sniper.admin.app:app",
        host=host,
        port=port,
        reload=reload_mode,
        log_level="info"
    )


if __name__ == "__main__":
    main()
