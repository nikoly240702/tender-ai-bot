release: alembic upgrade head && ([ -z "$RUN_MIGRATION_BITRIX24" ] || python3 scripts/migrate_to_bitrix24.py)
web: python -m bot.main
