# RAG LLM Service

> RAG Pipeline: TOP-3 похожих заказа → промпт → LLM → параметры заказа + confidence scoring.

## Назначение
Генерирует параметры заказа на основе:
1. Phase 0 — Vision LLM параметры (изображение макета)
2. Phase 1 — Docling текст + доп. файлы
3. RAG — TOP-3 похожих заказа из Qdrant (через hybrid_search)
4. LLM — генерирует JSON параметров с confidence scoring

## Endpoints
- `POST /api/generate-order-parameters` — генерирует параметры заказа
- `GET /health` — проверка здоровья сервиса

## Зависимости
- `httpx` — HTTP-клиент к LLM API и hybrid_search
- `aichat-okil-sato.kartochka.tech` — OpenAI-совместимый LLM API
- `qdrant-client` — поиск в коллекции `orders_phase1`

## Запуск
```bash
cd services/rag_llm
pip install -r ../requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8003
```

## Связи [[docling]], [[hybrid_search]], [[batch_indexer]], [[AIАссистент]]
