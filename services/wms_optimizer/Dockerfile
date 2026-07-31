FROM python:3.11-slim

WORKDIR /app

# Установка curl для healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --default-timeout=120 --retries 5 --no-cache-dir \
    --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org \
    -r requirements.txt

# Копируем весь код сервиса
COPY . .

EXPOSE 8010

# Запуск через main.py (uvicorn внутри)
CMD ["python", "main.py"]
