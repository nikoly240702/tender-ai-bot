# Multi-stage build для оптимизации размера образа
FROM python:3.11-slim as builder

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    postgresql-client \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    file \
    && rm -rf /var/lib/apt/lists/*

# Создаем виртуальное окружение
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Копируем requirements
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.11-slim

# Метаданные
LABEL maintainer="tender-ai-bot"
LABEL description="Tender AI Bot - Intelligent tender analysis and monitoring"

# Устанавливаем только runtime зависимости
RUN apt-get update && apt-get install -y \
    postgresql-client \
    curl \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    file \
    && rm -rf /var/lib/apt/lists/*

# Копируем виртуальное окружение из builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Создаем пользователя для запуска
RUN useradd -m -u 1000 botuser && \
    mkdir -p /app/bot_logs /app/backups && \
    chown -R botuser:botuser /app

# Рабочая директория
WORKDIR /app

# Копируем код приложения
COPY --chown=botuser:botuser . .

# Устанавливаем переменные окружения
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Переключаемся на непривилегированного пользователя
USER botuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${HEALTH_CHECK_PORT:-8080}/health || exit 1

# Порт для health check
EXPOSE 8080

# Запуск бота
CMD ["python", "-m", "bot.main"]
