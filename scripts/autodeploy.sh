#!/bin/bash
# Автодеплой: если в origin/main появился новый коммит — пересобрать и перезапустить.
# Опрос вместо webhook: не нужен ни открытый порт, ни ключ в GitHub Secrets.
set -euo pipefail
cd /opt/tender/app
git fetch origin main --quiet
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" = "$REMOTE" ]; then exit 0; fi
echo "[$(date '+%F %T')] новый коммит $REMOTE — деплой"
git reset --hard origin/main --quiet

cd /opt/tender

# Базовый образ тянем через зеркало Google: анонимные загрузки с Docker
# Hub лимитированы по IP, и 21.09.2026 выкатка встала на 429. Держать
# свою копию в кэше недостаточно — её вычищает prune в конце скрипта.
if docker pull -q mirror.gcr.io/library/python:3.11-slim >/dev/null 2>&1; then
    docker tag mirror.gcr.io/library/python:3.11-slim python:3.11-slim
fi

# Сборка не должна уходить в пайп «как есть»: в тот же раз падение
# скрылось за `| tail`, деплой отрапортовал «готово», а контейнеры
# остались на старом образе — заметили только по времени их жизни.
# Поэтому сначала собираем, проверяем код возврата, и только потом
# показываем хвост лога.
build_log=$(mktemp)
if ! DOCKER_BUILDKIT=0 docker compose build >"$build_log" 2>&1; then
    tail -8 "$build_log"
    rm -f "$build_log"
    echo "[$(date '+%F %T')] СБОРКА УПАЛА — образ не обновлён, контейнеры не тронуты"
    exit 1
fi
tail -3 "$build_log"
rm -f "$build_log"

docker compose up -d 2>&1 | tail -3
# Зеркальный тег держим: без него следующая сборка снова пойдёт в Hub.
docker image prune -f >/dev/null 2>&1 || true
echo "[$(date '+%F %T')] готово: $(cd /opt/tender/app && git log --oneline -1)"
