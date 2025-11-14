# Инструкция по деплою Telegram бота

Эта инструкция описывает различные способы развертывания Telegram бота для анализа тендеров.

## Содержание
- [Подготовка](#подготовка)
- [Деплой через Docker Compose (рекомендуется)](#деплой-через-docker-compose)
- [Деплой на VPS](#деплой-на-vps)
- [Деплой на Railway](#деплой-на-railway)
- [Деплой на Render](#деплой-на-render)
- [Локальный запуск](#локальный-запуск)

---

## Подготовка

### 1. Получение токена Telegram бота

1. Откройте Telegram и найдите [@BotFather](https://t.me/BotFather)
2. Отправьте команду `/newbot`
3. Следуйте инструкциям для создания бота
4. Скопируйте полученный токен (выглядит как `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)

### 2. Получение OpenAI API ключа

1. Зайдите на [platform.openai.com](https://platform.openai.com/)
2. Войдите или зарегистрируйтесь
3. Перейдите в раздел [API keys](https://platform.openai.com/api-keys)
4. Нажмите "Create new secret key"
5. Скопируйте ключ (начинается с `sk-proj-`)

### 3. Настройка переменных окружения

Создайте файл `.env` в корне проекта:

```env
# Telegram Bot Token
TELEGRAM_BOT_TOKEN=ваш-токен-бота

# OpenAI API Key
OPENAI_API_KEY=ваш-openai-ключ

# LLM Configuration
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
LLM_MODEL_FAST=gpt-4o-mini
LLM_MODEL_PREMIUM=gpt-4o

# Level 2 Analysis (Chain-of-Thought + Verification)
# Включите для повышения точности анализа с 70% до 85-90%
USE_LEVEL2_ANALYSIS=true
```

---

## Деплой через Docker Compose

**Рекомендуемый способ** для быстрого и надежного развертывания.

### Требования
- Docker и Docker Compose установлены
- Доступ к серверу (VPS) или локальная машина

### Шаги

1. **Клонируйте репозиторий**:
   ```bash
   git clone <url-репозитория>
   cd tender-ai-agent
   ```

2. **Создайте и настройте `.env` файл**:
   ```bash
   cp .env.example .env
   nano .env  # или используйте любой редактор
   ```

3. **Соберите и запустите контейнер**:
   ```bash
   docker-compose up -d
   ```

4. **Проверьте логи**:
   ```bash
   docker-compose logs -f tender-bot
   ```

5. **Управление ботом**:
   ```bash
   # Остановить
   docker-compose stop

   # Перезапустить
   docker-compose restart

   # Остановить и удалить контейнер
   docker-compose down

   # Обновить и перезапустить
   git pull
   docker-compose up -d --build
   ```

### Преимущества
- ✅ Изолированное окружение
- ✅ Автоматический перезапуск при падении
- ✅ Легкое обновление
- ✅ Персистентность данных (БД и файлы сохраняются)

---

## Деплой на VPS

Подходит для любого VPS провайдера (DigitalOcean, AWS EC2, Hetzner, и т.д.).

### Требования
- Ubuntu 20.04 или новее
- Минимум 1 GB RAM
- Python 3.11+

### Шаги

1. **Подключитесь к серверу**:
   ```bash
   ssh user@your-server-ip
   ```

2. **Обновите систему**:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

3. **Установите зависимости**:
   ```bash
   sudo apt install -y python3.11 python3.11-venv python3-pip git
   ```

4. **Клонируйте репозиторий**:
   ```bash
   cd /opt
   sudo git clone <url-репозитория> tender-bot
   cd tender-bot
   ```

5. **Создайте виртуальное окружение**:
   ```bash
   python3.11 -m venv venv
   source venv/bin/activate
   ```

6. **Установите зависимости**:
   ```bash
   pip install -r requirements.txt
   ```

7. **Создайте `.env` файл**:
   ```bash
   nano .env
   # Вставьте ваши токены и ключи
   ```

8. **Создайте systemd service**:
   ```bash
   sudo nano /etc/systemd/system/tender-bot.service
   ```

   Вставьте:
   ```ini
   [Unit]
   Description=Tender AI Bot
   After=network.target

   [Service]
   Type=simple
   User=root
   WorkingDirectory=/opt/tender-bot
   Environment="PATH=/opt/tender-bot/venv/bin"
   ExecStart=/opt/tender-bot/venv/bin/python3 bot/main.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

9. **Запустите сервис**:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable tender-bot
   sudo systemctl start tender-bot
   ```

10. **Проверьте статус**:
    ```bash
    sudo systemctl status tender-bot

    # Логи
    sudo journalctl -u tender-bot -f
    ```

### Управление
```bash
# Остановить
sudo systemctl stop tender-bot

# Перезапустить
sudo systemctl restart tender-bot

# Обновить код
cd /opt/tender-bot
sudo git pull
sudo systemctl restart tender-bot
```

---

## Деплой на Railway

[Railway](https://railway.app/) - простой PaaS с бесплатным tier.

### Шаги

1. **Зарегистрируйтесь на Railway**: https://railway.app/

2. **Создайте новый проект**:
   - Нажмите "New Project"
   - Выберите "Deploy from GitHub repo"
   - Подключите ваш GitHub аккаунт
   - Выберите репозиторий

3. **Добавьте переменные окружения**:
   - Перейдите в настройки проекта
   - Variables → Add Variable
   - Добавьте:
     - `TELEGRAM_BOT_TOKEN`
     - `OPENAI_API_KEY`
     - `LLM_PROVIDER=openai`
     - `LLM_MODEL=gpt-4o-mini`
     - `LLM_MODEL_FAST=gpt-4o-mini`
     - `LLM_MODEL_PREMIUM=gpt-4o`
     - `USE_LEVEL2_ANALYSIS=true`

4. **Railway автоматически**:
   - Обнаружит Dockerfile
   - Соберет образ
   - Запустит бота

5. **Проверьте логи** в веб-интерфейсе Railway

### Преимущества
- ✅ Бесплатный tier ($5 credit в месяц)
- ✅ Автоматический деплой при push в GitHub
- ✅ Встроенные логи и мониторинг
- ✅ Персистентные volumes

---

## Деплой на Render

[Render](https://render.com/) - альтернатива Railway с бесплатным tier.

### Шаги

1. **Зарегистрируйтесь на Render**: https://render.com/

2. **Создайте Web Service**:
   - Dashboard → New → Web Service
   - Подключите GitHub репозиторий

3. **Настройте сервис**:
   - Name: `tender-ai-bot`
   - Environment: `Docker`
   - Plan: `Free`

4. **Добавьте переменные окружения**:
   В разделе Environment добавьте:
   - `TELEGRAM_BOT_TOKEN`
   - `OPENAI_API_KEY`
   - `LLM_PROVIDER=openai`
   - `LLM_MODEL=gpt-4o-mini`
   - `LLM_MODEL_FAST=gpt-4o-mini`
   - `LLM_MODEL_PREMIUM=gpt-4o`
   - `USE_LEVEL2_ANALYSIS=true`

5. **Deploy**

### Преимущества
- ✅ Бесплатный tier
- ✅ Автоматический деплой
- ✅ SSL из коробки

### Ограничения бесплатного tier
- Засыпает после 15 минут бездействия
- 750 часов в месяц

---

## Локальный запуск

Для разработки и тестирования.

### Требования
- Python 3.11+
- pip

### Шаги

1. **Клонируйте репозиторий**:
   ```bash
   git clone <url-репозитория>
   cd tender-ai-agent
   ```

2. **Создайте виртуальное окружение**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # Linux/Mac
   # или
   venv\Scripts\activate  # Windows
   ```

3. **Установите зависимости**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Создайте `.env` файл**:
   ```bash
   cp .env.example .env
   # Отредактируйте .env и добавьте ваши токены
   ```

5. **Запустите бота**:
   ```bash
   python bot/main.py
   ```

---

## Мониторинг и обслуживание

### Просмотр логов

**Docker Compose**:
```bash
docker-compose logs -f tender-bot
```

**VPS (systemd)**:
```bash
sudo journalctl -u tender-bot -f
```

**Railway/Render**:
Используйте веб-интерфейс

### Резервное копирование БД

База данных SQLite хранится в `bot/database/bot.db`.

**Создать backup**:
```bash
# Docker
docker-compose exec tender-bot cp /app/bot/database/bot.db /app/output/backup.db

# VPS
cp /opt/tender-bot/bot/database/bot.db /opt/tender-bot/backup_$(date +%Y%m%d).db
```

### Обновление

**Docker Compose**:
```bash
git pull
docker-compose up -d --build
```

**VPS**:
```bash
cd /opt/tender-bot
sudo git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart tender-bot
```

---

## Решение проблем

### Бот не отвечает

1. Проверьте логи
2. Убедитесь, что переменные окружения заданы правильно
3. Проверьте интернет-соединение сервера
4. Убедитесь, что токен бота валидный

### Ошибки OpenAI API

1. Проверьте, что API ключ правильный
2. Убедитесь, что на аккаунте есть credits
3. Проверьте лимиты запросов

### База данных не сохраняется

**Docker**: Убедитесь, что volume смонтирован правильно в `docker-compose.yml`

**VPS**: Проверьте права доступа к директории `bot/database/`

---

## Поддержка

Если возникли проблемы:
1. Проверьте логи
2. Убедитесь, что все зависимости установлены
3. Проверьте переменные окружения
4. Создайте issue в репозитории

---

## Производительность

### Рекомендуемые ресурсы

- **Минимум**: 1 GB RAM, 1 CPU core, 10 GB диск
- **Рекомендуется**: 2 GB RAM, 2 CPU cores, 20 GB диск

### Стоимость хостинга

- **Railway**: ~$5-10/месяц (free tier доступен)
- **Render**: Бесплатно с ограничениями
- **VPS (Hetzner)**: от €4/месяц
- **VPS (DigitalOcean)**: от $6/месяц

### Стоимость OpenAI API

- gpt-4o-mini: ~$0.15 за 1M input tokens
- gpt-4o: ~$2.5 за 1M input tokens

Средний запрос использует ~1000-3000 tokens, так что стоимость ~$0.001-0.01 за поиск.
