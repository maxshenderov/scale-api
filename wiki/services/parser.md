# Parser Service

> PyMuPDF (PDF) + EasyOCR (JPG/PNG) — локальный парсер документов для типографии Лико. Порт 8002.

## Назначение

Парсит PDF и изображения, извлекая текст без внешних API. Бесплатная альтернатива Docling для простых случаев.

**Актуальный статус (2026-06):** Базовый парсер. Основной парсинг заказов идёт через [[docling]] (IBM Docling + Vision LLM, порт 8001). Parser сохранён для быстрых локальных тестов PDF через PyMuPDF.

## Endpoints

| Endpoint | Метод | Назначение |
|---|---|---|
| `/api/parse-base64` | POST | Парсит файл (base64) и возвращает извлечённый текст |
| `/health` | GET | Проверка здоровья сервиса |

### POST /api/parse-base64

```json
// Запрос
{"file": "<base64-encoded-file>", "filename": "order.pdf"}

// Ответ
{"text": "извлечённый текст...", "pages": 3}
```

## Зависимости

- `PyMuPDF` (fitz) — парсинг PDF (текст, метаданные, страницы)
- `EasyOCR` — OCR для изображений (JPG/PNG), русский + английский
- `Pillow` — обработка изображений перед OCR

## Запуск

```bash
cd services/parser
pip install -r ../requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8002
```

> **⚠️ Порт 8002** конфликтует с [[hybrid_search]] — не запускать одновременно.

## Тестирование

```bash
cd services/parser
python test_parser.py
```

## Связи

[[docling]] — основной парсер (IBM Docling + Vision LLM) | [[hybrid_search]] — конфликт порта 8002 | [[AIАссистент]] — используется в «PyMuPDF тест»
