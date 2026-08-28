FROM python:3.11-slim

# Устанавливаем PostgreSQL клиент и системные либы для WeasyPrint (KP generator)
RUN apt-get update && apt-get install -y \
    postgresql-client \
    curl \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY . .

# Копируем и делаем entrypoint исполняемым
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8080

ENTRYPOINT ["/docker-entrypoint.sh"]
