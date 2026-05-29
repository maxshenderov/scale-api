# Docling Service

> IBM Docling структурный парсинг PDF/DOCX + Vision LLM (Gemini) для OCR изображений.

## Назначение
Глубокий структурный парсинг документов: извлекает текст, таблицы и изображения из PDF, DOCX, JPG, PNG. Использует IBM Docling как основной движок, с fallback на Tesseract OCR и Vision LLM (Gemini) для сложных изображений.

## Endpoints
- `POST /api/parse` — парсит файл (multipart upload или base64) и возвращает JSON с текстом, таблицами, изображениями
- `GET /health` — проверка здоровья сервиса

## Зависимости
- `docling` — IBM Docling DocumentConverter
- `pytesseract` + `Pillow` — Tesseract OCR fallback
- `httpx` — вызов Vision LLM (routerai.ru, Gemini)
- `python-docx`, `python-pptx` — офисные форматы

## Запуск
```bash
cd services/docling
pip install -r ../requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8001
```

## Связи [[parser]], [[rag_llm]], [[batch_indexer]], [[AIАссистент]]
