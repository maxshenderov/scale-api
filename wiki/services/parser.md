# Parser Service

> PyMuPDF (PDF) + EasyOCR (JPG/PNG) — локальный парсер документов для типографии Лико.

## Назначение
Парсит PDF и изображения, извлекая текст без внешних API. Бесплатная альтернатива Docling для простых случаев.

## Endpoints
- `POST /api/parse-base64` — парсит файл (base64) и возвращает извлечённый текст
- `GET /health` — проверка здоровья сервиса

## Зависимости
- `PyMuPDF` (fitz) — парсинг PDF
- `EasyOCR` — OCR для изображений (JPG/PNG)
- `Pillow` — обработка изображений

## Запуск
```bash
cd services/parser
pip install -r ../requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8002
```

**Внимание:** порт 8002 конфликтует с hybrid_search — не запускать одновременно.

## Связи [[docling]], [[hybrid_search]], [[AIАссистент]]
