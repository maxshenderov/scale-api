FROM python:3.11-slim

WORKDIR /app

# Установка curl для healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код сервиса
COPY . .

EXPOSE 8011

CMD ["python", "app.py"]
