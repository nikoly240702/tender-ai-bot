#!/bin/bash
#
# PostgreSQL Database Backup Script
#
# Usage:
#   ./scripts/backup_db.sh
#
# Environment variables required:
#   DATABASE_URL - PostgreSQL connection string
#
# Output:
#   backups/backup_YYYY-MM-DD_HH-MM-SS.sql
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

# Проверка DATABASE_URL
if [ -z "$DATABASE_URL" ]; then
    log_error "DATABASE_URL не установлен"
    exit 1
fi

# Создаем директорию для бэкапов
BACKUP_DIR="backups"
mkdir -p "$BACKUP_DIR"

# Генерируем имя файла
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
BACKUP_FILE="$BACKUP_DIR/backup_$TIMESTAMP.sql"

log_info "Начинаем бэкап базы данных..."
log_info "Файл: $BACKUP_FILE"

# Парсим DATABASE_URL для pg_dump
# Формат: postgresql://user:password@host:port/dbname
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

# Выполняем бэкап
PGPASSWORD="$PGPASSWORD" pg_dump \
    -h "$PGHOST" \
    -p "$PGPORT" \
    -U "$PGUSER" \
    -d "$PGDATABASE" \
    --no-owner \
    --no-acl \
    --clean \
    --if-exists \
    -f "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    # Сжимаем бэкап
    log_info "Сжимаем бэкап..."
    gzip "$BACKUP_FILE"
    BACKUP_FILE="${BACKUP_FILE}.gz"

    # Получаем размер файла
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)

    log_info "✅ Бэкап завершен успешно!"
    log_info "Файл: $BACKUP_FILE"
    log_info "Размер: $BACKUP_SIZE"

    # Удаляем старые бэкапы (старше 7 дней)
    log_info "Удаление старых бэкапов (>7 дней)..."
    find "$BACKUP_DIR" -name "backup_*.sql.gz" -mtime +7 -delete

    # Показываем список бэкапов
    log_info "Список бэкапов:"
    ls -lh "$BACKUP_DIR"/backup_*.sql.gz | tail -5
else
    log_error "❌ Ошибка при создании бэкапа"
    exit 1
fi
