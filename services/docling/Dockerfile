FROM python:3.11-slim

# Установка системных зависимостей для работы с PDF/изображениями
RUN apt-get update && apt-get install -y \
    libpoppler-cpp-dev \
    libtesseract-dev \
    tesseract-ocr \
    libmagic1 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Отключение проверки SSL для корпоративных сетей (MITM прокси)
# ВАЖНО: НЕ очищаем SSL_CERT_FILE — Rust Xet клиент HuggingFace нуждается в системных сертификатах
ENV PYTHONHTTPSVERIFY=0
ENV HF_HUB_DISABLE_TELEMETRY=1
ENV HF_HUB_DISABLE_XET=1

# Trust пайтоновский SSL для корпоративных сетей
RUN pip install --no-cache-dir certifi && \
    python -c "import certifi; print(certifi.where())"

WORKDIR /app

# Кэш моделей HuggingFace (монтируется как volume)
ENV HF_HOME=/app/hf_cache
ENV TRANSFORMERS_CACHE=/app/hf_cache
ENV DOCLING_MODELS_DIR=/app/models

# Копируем requirements.txt и устанавливаем зависимости (trusted-host для корпоративных сетей)
COPY requirements.txt .
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt

# Копируем исходный код
COPY app.py .

# Создаём директорию для кэша моделей
RUN mkdir -p /app/hf_cache /app/models

# Expose порт для FastAPI
EXPOSE 8001

# Запуск приложения
CMD ["python", "app.py"]
