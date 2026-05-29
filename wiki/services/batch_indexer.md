# Batch Indexer Service

> Фоновая индексация 100K+ заказов из 1С в Qdrant. Связующее звено между ERP и векторной БД.

## Назначение
Пайплайн пакетной индексации:
1. Получает список заказов из 1С API (`OIL1CClient`)
2. Скачивает PDF-макеты заказов
3. Парсит через Docling (Phase 1) → извлекает текст
4. Создаёт BM25 sparse vectors (`rank-bm25`)
5. Создаёт Dense embeddings (`sentence-transformers`, 384d)
6. Загружает в Qdrant коллекцию `orders_phase1`

## Зависимости
- `httpx` — клиент к 1С API и Docling сервису
- `qdrant-client` — загрузка векторов в Qdrant (:6334)
- `sentence-transformers` — dense embeddings
- `rank-bm25` — BM25 sparse vectors
- `tenacity` — retry-логика для API вызовов
- `tqdm` — прогресс-бар

## Конфигурация
- `config.py` — настройки подключения к 1С и Qdrant
- `settings.LOG_LEVEL`, `settings.LOG_FORMAT` — логирование

## Запуск
```bash
cd services/batch_indexer
pip install -r ../requirements.txt
python app.py
```

## Связи [[docling]], [[hybrid_search]], [[rag_llm]], [[Liko_Rest]], [[Лико_QdrantИндексация]]
