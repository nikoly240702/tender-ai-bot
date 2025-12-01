#!/bin/bash

# Скрипт для копирования документации на Desktop
# Запустите: bash copy_to_desktop.sh

set -e

echo "📋 Копирование документации на Desktop..."
echo "=========================================="
echo ""

# Определяем путь к Desktop
DESKTOP="$HOME/Desktop"

# Проверяем что Desktop существует
if [ ! -d "$DESKTOP" ]; then
    echo "❌ Desktop не найден: $DESKTOP"
    exit 1
fi

# Создаем папку для документации
DOC_FOLDER="$DESKTOP/Агент по тендерам - Документация 01.12"
mkdir -p "$DOC_FOLDER"

echo "📂 Создана папка: $DOC_FOLDER"
echo ""

# Копируем файлы
echo "📄 Копирование файлов..."

# 1. README для терминала
if [ -f "README_CLAUDE_TERMINAL.md" ]; then
    cp README_CLAUDE_TERMINAL.md "$DOC_FOLDER/"
    echo "  ✅ README_CLAUDE_TERMINAL.md"
else
    echo "  ⚠️  README_CLAUDE_TERMINAL.md не найден"
fi

# 2. Статус проекта
if [ -f "PROJECT_STATUS.md" ]; then
    cp PROJECT_STATUS.md "$DOC_FOLDER/"
    echo "  ✅ PROJECT_STATUS.md"
else
    echo "  ⚠️  PROJECT_STATUS.md не найден"
fi

# 3. Скрипт установки
if [ -f "setup_new_version.sh" ]; then
    cp setup_new_version.sh "$DOC_FOLDER/"
    chmod +x "$DOC_FOLDER/setup_new_version.sh"
    echo "  ✅ setup_new_version.sh"
else
    echo "  ⚠️  setup_new_version.sh не найден"
fi

# 4. .env пример (без секретов)
if [ -f ".env" ]; then
    # Создаем .env.example без реальных значений
    grep "^#" .env > "$DOC_FOLDER/.env.example" || true
    echo "" >> "$DOC_FOLDER/.env.example"
    grep -v "^#" .env | sed 's/=.*/=YOUR_VALUE_HERE/g' >> "$DOC_FOLDER/.env.example" || true
    echo "  ✅ .env.example (без секретов)"
fi

# 5. requirements.txt
if [ -f "requirements.txt" ]; then
    cp requirements.txt "$DOC_FOLDER/"
    echo "  ✅ requirements.txt"
fi

# Создаем README в папке
cat > "$DOC_FOLDER/README.md" << 'EOF'
# Документация Tender AI Bot - Версия 01.12

## 📁 Содержимое:

1. **README_CLAUDE_TERMINAL.md** - Главное руководство для работы через Claude Desktop
2. **PROJECT_STATUS.md** - Полный статус проекта, проблемы, архитектура
3. **setup_new_version.sh** - Автоматическая установка проекта
4. **.env.example** - Пример конфигурации (без секретов)
5. **requirements.txt** - Список зависимостей Python

## 🚀 Быстрый старт:

### Вариант 1: Автоматическая установка (рекомендуется)

```bash
cd ~/Desktop/"Агент по тендерам - Документация 01.12"
bash setup_new_version.sh
```

Скрипт создаст папку "Агент по тендерам версия 01.12" и настроит проект.

### Вариант 2: Вручную

```bash
# Создаем папку
mkdir -p ~/Desktop/"Агент по тендерам версия 01.12"
cd ~/Desktop/"Агент по тендерам версия 01.12"

# Клонируем
git clone https://github.com/nikoly240702/tender-ai-bot.git .
git checkout main
git pull origin main

# Устанавливаем
pip3 install -r requirements.txt
```

## 📖 Документация:

### Для работы через Claude Desktop (терминал):
👉 **Читайте: README_CLAUDE_TERMINAL.md**

### Для понимания статуса проекта:
👉 **Читайте: PROJECT_STATUS.md**

## 🔑 Настройка переменных окружения:

1. Скопируйте `.env.example` в `.env`
2. Заполните реальные значения:
   - OPENAI_API_KEY
   - TELEGRAM_BOT_TOKEN
   - PROXY_URL
   - ADMIN_USER_IDS

## 💡 Важно:

- **Claude Desktop** может пушить в `main` напрямую
- **Веб версия Claude** требует merge через GitHub PR
- Все последние исправления в ветке `claude/add-enrichment-logging-01LD6KduakYKUAki1umGLdKR`

## 📞 Поддержка:

GitHub: https://github.com/nikoly240702/tender-ai-bot

---

**Дата создания:** 1 декабря 2024
**Версия:** 01.12.2024
EOF

echo "  ✅ README.md (этот файл)"

echo ""
echo "=========================================="
echo "✅ Готово!"
echo ""
echo "📂 Файлы сохранены в:"
echo "   $DOC_FOLDER"
echo ""
echo "📄 Скопировано файлов:"
ls -1 "$DOC_FOLDER" | wc -l | xargs echo "  "
echo ""
echo "📖 Откройте README.md для инструкций"
echo ""
echo "🚀 Для установки проекта выполните:"
echo "   cd '$DOC_FOLDER'"
echo "   bash setup_new_version.sh"
echo "=========================================="
