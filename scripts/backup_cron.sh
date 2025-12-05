#!/bin/bash
#
# Автоматический бэкап БД (для cron)
#
# Установка в crontab:
#   # Бэкап каждый день в 3:00 AM
#   0 3 * * * /path/to/scripts/backup_cron.sh
#
# Для Railway/Heroku используйте scheduled jobs или external cron service
#

# Загружаем переменные окружения из .env (если локально)
if [ -f "$(dirname "$0")/../.env" ]; then
    set -a
    source "$(dirname "$0")/../.env"
    set +a
fi

# Переходим в директорию проекта
cd "$(dirname "$0")/.."

# Запускаем бэкап
./scripts/backup_db.sh

# Опционально: загружаем в S3/Google Cloud Storage
# Раскомментируйте и настройте при необходимости
#
# BACKUP_FILE=$(ls -t backups/backup_*.sql.gz | head -1)
# aws s3 cp "$BACKUP_FILE" "s3://your-bucket/backups/"
# или
# gsutil cp "$BACKUP_FILE" "gs://your-bucket/backups/"

# Отправляем уведомление в Slack/Telegram
# Раскомментируйте и настройте при необходимости
#
# if [ $? -eq 0 ]; then
#     curl -X POST "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
#         -d chat_id="$ADMIN_CHAT_ID" \
#         -d text="✅ Backup completed: $BACKUP_FILE"
# fi
