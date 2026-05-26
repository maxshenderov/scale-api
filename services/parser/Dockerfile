FROM python:3.11-slim

# Системные зависимости для OpenCV, PyTorch, EasyOCR
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# SSL bypass для корпоративной сети
ENV PYTHONHTTPSVERIFY=0

WORKDIR /app

# Копируем и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org --trusted-host *.pypi.org --trusted-host *.pythonhosted.org --trusted-host pypi-files.s3.amazonaws.com -r requirements.txt

# Копируем приложение
COPY app.py .

# EasyOCR скачивает модели при первом запуске
ENV EASYOCR_MODEL_DIR=/app/easyocr_models
RUN mkdir -p /app/easyocr_models

EXPOSE 8002

CMD ["python", "app.py"]
