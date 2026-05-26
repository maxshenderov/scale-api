FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    libpoppler-cpp-dev \
    libtesseract-dev \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY app.py .
COPY config.py .

# Запуск batch indexer (фоновый worker)
CMD ["python", "app.py"]
