#!/bin/bash
# Запуск админ-панели Tender Sniper

cd "$(dirname "$0")"

# Загружаем переменные из .env
export $(grep -v '^#' .env | xargs)

echo "╔══════════════════════════════════════════╗"
echo "║     Tender Sniper Admin Dashboard        ║"
echo "╠══════════════════════════════════════════╣"
echo "║  URL: http://localhost:8080              ║"
echo "║  Login: admin / tender2024               ║"
echo "╚══════════════════════════════════════════╝"
echo ""

python3 scripts/run_admin.py
