FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libsndfile1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONHTTPSVERIFY=0
ENV HF_HUB_DISABLE_XET=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org \
    -r requirements.txt

COPY app.py .
COPY bot.py .
COPY stt.py .
COPY ws_manager.py .
COPY llm.py .
COPY session.py .

ENV WHISPER_MODEL_DIR=/app/whisper_models
RUN mkdir -p /app/whisper_models

EXPOSE 8004

CMD ["python", "app.py"]
