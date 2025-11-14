# Управление Telegram ботом

## Скрипт управления

Используйте скрипт `manage_bot.sh` для управления ботом:

```bash
cd /Users/nikolaichizhik/tender-ai-agent/bot

# Запустить бота
./manage_bot.sh start

# Остановить бота
./manage_bot.sh stop

# Перезапустить бота
./manage_bot.sh restart

# Проверить статус
./manage_bot.sh status

# Посмотреть логи в реальном времени
./manage_bot.sh logs

# Полная очистка всех процессов (при проблемах)
./manage_bot.sh clean
```

## Решение проблем

### Проблема: "Conflict: terminated by other getUpdates request"

Это означает, что Telegram API еще держит соединение от предыдущего экземпляра бота.

**Решение:**
1. Выполните полную очистку:
   ```bash
   ./manage_bot.sh clean
   ```

2. Подождите 2-3 минуты, чтобы Telegram освободил соединения

3. Запустите бота снова:
   ```bash
   ./manage_bot.sh start
   ```

### Проблема: Бот не реагирует или реагирует со 2-го раза

**Причины:**
- Работает несколько экземпляров бота одновременно
- Telegram держит старые соединения

**Решение:**
1. Проверьте статус:
   ```bash
   ./manage_bot.sh status
   ```

2. Если видите конфликты, сделайте полную очистку:
   ```bash
   ./manage_bot.sh clean
   ```

3. Подождите 2-3 минуты

4. Запустите снова:
   ```bash
   ./manage_bot.sh start
   ```

### Проблема: Множественные процессы python3 main.py

**Решение:**
```bash
# Убить все процессы вручную
pkill -9 -f "python3 main.py"

# Подождать 10 секунд
sleep 10

# Запустить через скрипт
./manage_bot.sh start
```

## Логи

Логи бота сохраняются в `/tmp/tender_bot.log`

Просмотр последних строк:
```bash
tail -50 /tmp/tender_bot.log
```

Просмотр в реальном времени:
```bash
./manage_bot.sh logs
# Или
tail -f /tmp/tender_bot.log
```

## PID файл

Скрипт использует PID-файл для отслеживания процесса: `/Users/nikolaichizhik/tender-ai-agent/bot/bot.pid`

Если бот не запускается, проверьте и удалите устаревший PID-файл:
```bash
rm -f /Users/nikolaichizhik/tender-ai-agent/bot/bot.pid
```

## Рекомендации

1. **Всегда используйте manage_bot.sh** вместо прямого запуска `python3 main.py`
2. **Не запускайте бота вручную** через `python3 main.py &` - это приводит к множественным процессам
3. **После изменений в коде** используйте `./manage_bot.sh restart`
4. **При проблемах** всегда начинайте с `./manage_bot.sh clean`

## Как работает скрипт

1. **start**:
   - Проверяет наличие работающего процесса
   - Убивает старые процессы
   - Запускает новый экземпляр
   - Сохраняет PID в файл

2. **stop**:
   - Читает PID из файла
   - Останавливает процесс (SIGTERM, затем SIGKILL)
   - Убивает все остальные процессы python3 main.py
   - Удаляет PID-файл

3. **restart**:
   - Вызывает stop
   - Ждет 5 секунд
   - Вызывает start

4. **clean**:
   - Убивает ВСЕ процессы python3 main.py
   - Удаляет PID-файл
   - Ждет 10 секунд для освобождения Telegram соединений
