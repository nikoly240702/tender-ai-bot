#!/bin/bash

# Управление ботом с использованием PID-файла
BOT_DIR="/Users/nikolaichizhik/tender-ai-agent/bot"
PID_FILE="$BOT_DIR/bot.pid"
LOG_FILE="/tmp/tender_bot.log"

case "$1" in
    start)
        # Проверяем, не запущен ли уже бот
        if [ -f "$PID_FILE" ]; then
            OLD_PID=$(cat "$PID_FILE")
            if ps -p "$OLD_PID" > /dev/null 2>&1; then
                echo "❌ Бот уже запущен (PID: $OLD_PID)"
                exit 1
            else
                echo "⚠️  Найден устаревший PID-файл, удаляем..."
                rm -f "$PID_FILE"
            fi
        fi

        # Убиваем все старые процессы python3 main.py и Python main.py
        echo "🧹 Очистка старых процессов..."
        pkill -9 -f "python3 main.py" 2>/dev/null
        pkill -9 -f "Python main.py" 2>/dev/null
        sleep 2

        # Запускаем бота
        echo "🚀 Запуск бота..."
        cd "$BOT_DIR" && nohup python3 main.py > "$LOG_FILE" 2>&1 &
        NEW_PID=$!
        echo $NEW_PID > "$PID_FILE"

        # Ждем 3 секунды и проверяем, что процесс жив
        sleep 3
        if ps -p "$NEW_PID" > /dev/null 2>&1; then
            echo "✅ Бот успешно запущен (PID: $NEW_PID)"
            echo "📝 Логи: $LOG_FILE"
        else
            echo "❌ Не удалось запустить бота, проверьте логи: $LOG_FILE"
            rm -f "$PID_FILE"
            exit 1
        fi
        ;;

    stop)
        if [ ! -f "$PID_FILE" ]; then
            echo "⚠️  PID-файл не найден"
            echo "🧹 Убиваем все процессы python3 main.py..."
            pkill -9 -f "python3 main.py" 2>/dev/null
            exit 0
        fi

        PID=$(cat "$PID_FILE")
        echo "🛑 Остановка бота (PID: $PID)..."
        kill -15 "$PID" 2>/dev/null
        sleep 2

        # Если процесс все еще жив, убиваем жестко
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "⚠️  Процесс не остановился, используем SIGKILL..."
            kill -9 "$PID" 2>/dev/null
        fi

        # Убиваем все остальные процессы python3 main.py и Python main.py
        pkill -9 -f "python3 main.py" 2>/dev/null
        pkill -9 -f "Python main.py" 2>/dev/null

        rm -f "$PID_FILE"
        echo "✅ Бот остановлен"
        ;;

    restart)
        $0 stop
        echo "⏳ Ожидание 5 секунд для освобождения Telegram соединений..."
        sleep 5
        $0 start
        ;;

    status)
        if [ ! -f "$PID_FILE" ]; then
            echo "❌ Бот не запущен (PID-файл не найден)"

            # Проверяем, есть ли процессы python3 main.py
            RUNNING=$(ps aux | grep "python3 main.py" | grep -v grep | wc -l | tr -d ' ')
            if [ "$RUNNING" -gt 0 ]; then
                echo "⚠️  Обнаружено $RUNNING процессов python3 main.py без PID-файла!"
                ps aux | grep "python3 main.py" | grep -v grep
            fi
            exit 1
        fi

        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "✅ Бот работает (PID: $PID)"
            echo "📝 Логи: $LOG_FILE"
            echo "---"
            tail -5 "$LOG_FILE"
        else
            echo "❌ Бот не работает (PID $PID не найден)"
            rm -f "$PID_FILE"
            exit 1
        fi
        ;;

    logs)
        if [ -f "$LOG_FILE" ]; then
            tail -f "$LOG_FILE"
        else
            echo "❌ Лог-файл не найден: $LOG_FILE"
            exit 1
        fi
        ;;

    clean)
        echo "🧹 Полная очистка..."
        pkill -9 -f "python3 main.py" 2>/dev/null
        pkill -9 -f "Python main.py" 2>/dev/null
        rm -f "$PID_FILE"
        echo "⏳ Ожидание 10 секунд для освобождения Telegram соединений..."
        sleep 10
        echo "✅ Очистка завершена"
        ;;

    *)
        echo "Использование: $0 {start|stop|restart|status|logs|clean}"
        echo ""
        echo "Команды:"
        echo "  start    - Запустить бота"
        echo "  stop     - Остановить бота"
        echo "  restart  - Перезапустить бота"
        echo "  status   - Проверить статус бота"
        echo "  logs     - Показать логи (Ctrl+C для выхода)"
        echo "  clean    - Полная очистка всех процессов"
        exit 1
        ;;
esac

exit 0
