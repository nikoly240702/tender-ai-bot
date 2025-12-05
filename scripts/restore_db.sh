#!/bin/bash
#
# PostgreSQL Database Restore Script
#
# Usage:
#   ./scripts/restore_db.sh <backup_file>
#
# Environment variables required:
#   DATABASE_URL - PostgreSQL connection string
#
# Example:
#   ./scripts/restore_db.sh backups/backup_2024-11-24_12-00-00.sql.gz
#

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Функция логирования
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Проверка аргументов
if [ $# -eq 0 ]; then
    log_error "Укажите файл бэкапа"
    echo "Usage: $0 <backup_file>"
    echo ""
    echo "Доступные бэкапы:"
    ls -lh backups/backup_*.sql.gz 2>/dev/null || echo "  (нет бэкапов)"
    exit 1
fi

BACKUP_FILE="$1"

# Проверка существования файла
if [ ! -f "$BACKUP_FILE" ]; then
    log_error "Файл не найден: $BACKUP_FILE"
    exit 1
fi

# Проверка DATABASE_URL
if [ -z "$DATABASE_URL" ]; then
    log_error "DATABASE_URL не установлен"
    exit 1
fi

log_warn "⚠️  ВНИМАНИЕ: Восстановление БД удалит все существующие данные!"
log_warn "Файл: $BACKUP_FILE"
echo ""
read -p "Продолжить? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    log_info "Отменено пользователем"
    exit 0
fi

# Парсим DATABASE_URL
if [[ $DATABASE_URL =~ postgres://([^:]+):([^@]+)@([^:]+):([^/]+)/(.+) ]]; then
    PGUSER="${BASH_REMATCH[1]}"
    PGPASSWORD="${BASH_REMATCH[2]}"
    PGHOST="${BASH_REMATCH[3]}"
    PGPORT="${BASH_REMATCH[4]}"
    PGDATABASE="${BASH_REMATCH[5]}"
elif [[ $DATABASE_URL =~ postgresql://([^:]+):([^@]+)@([^:]+):([^/]+)/(.+) ]]; then
    PGUSER="${BASH_REMATCH[1]}"
    PGPASSWORD="${BASH_REMATCH[2]}"
    PGHOST="${BASH_REMATCH[3]}"
    PGPORT="${BASH_REMATCH[4]}"
    PGDATABASE="${BASH_REMATCH[5]}"
else
    log_error "Неверный формат DATABASE_URL"
    exit 1
fi

log_info "Начинаем восстановление БД..."

# Распаковываем если .gz
if [[ $BACKUP_FILE == *.gz ]]; then
    log_info "Распаковка архива..."
    TEMP_FILE="${BACKUP_FILE%.gz}"
    gunzip -c "$BACKUP_FILE" > "$TEMP_FILE"
    RESTORE_FILE="$TEMP_FILE"
else
    RESTORE_FILE="$BACKUP_FILE"
fi

# Восстанавливаем БД
PGPASSWORD="$PGPASSWORD" psql \
    -h "$PGHOST" \
    -p "$PGPORT" \
    -U "$PGUSER" \
    -d "$PGDATABASE" \
    -f "$RESTORE_FILE"

if [ $? -eq 0 ]; then
    log_info "✅ База данных восстановлена успешно!"

    # Удаляем временный файл
    if [[ $BACKUP_FILE == *.gz ]]; then
        rm -f "$TEMP_FILE"
    fi
else
    log_error "❌ Ошибка при восстановлении БД"
    if [[ $BACKUP_FILE == *.gz ]]; then
        rm -f "$TEMP_FILE"
    fi
    exit 1
fi
