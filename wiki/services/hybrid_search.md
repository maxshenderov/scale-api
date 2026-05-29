# Hybrid Search Service

> Гибридный поиск в Qdrant: BM25 (sparse) + Dense embeddings (semantic) для поиска похожих заказов.

## Назначение
Поиск похожих заказов по текстовому запросу через комбинацию:
- BM25 sparse vectors (keyword search) — вес 0.3
- Dense embeddings (semantic search, sentence-transformers) — вес 0.7

## Endpoints
- `POST /api/search` — поиск похожих заказов по тексту, возвращает TOP-K результатов с комбинированным score
- `GET /health` — проверка здоровья сервиса

## Зависимости
- `qdrant-client` — клиент Qdrant (коллекция `orders_phase1`)
- `sentence-transformers` — dense embeddings (384d)
- `rank-bm25` — BM25 sparse vectors

## Запуск
```bash
cd services/hybrid_search
pip install -r ../requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8002
```

**Внимание:** порт 8002 конфликтует с parser — не запускать одновременно.

## Связи [[rag_llm]], [[batch_indexer]], [[parser]]
