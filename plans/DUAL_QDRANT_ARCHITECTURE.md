# Dual Qdrant Architecture: Orders (6334) + Codebase (6333)

## 📐 Архитектура с двумя отдельными Qdrant контейнерами

```
┌─────────────────────────────────────────────────────────────┐
│                    DUAL QDRANT SETUP                        │
└─────────────────────────────────────────────────────────────┘

QDRANT #1 (Port 6333) - Кодовая база (существует)
   ├─ Container: qdrant-codebase
   ├─ Collections: bsl_modules, python_services, docs
   └─ Использование: Code indexing, documentation search

QDRANT #2 (Port 6334) - Индексирование заказов (новый)
   ├─ Container: qdrant-orders
   ├─ Collection: orders_phase1
   │   ├─ Vectors: phase1_text_dense (1536 dim, COSINE)
   │   ├─ Sparse: phase1_text_bm25 (10000 dim, DOT)
   │   └─ Payload: параметры заказов из 1С
   └─ Использование:
      ├─ Batch Indexer (services/batch_indexer) → 6334
      ├─ Hybrid Search (services/hybrid_search) → 6334
      └─ RAG LLM Pipeline (services/rag_llm) → 6334 через Hybrid Search
```

---

## ✅ Конфигурация сервисов (уже обновлено)

### services/batch_indexer/config.py
```python
QDRANT_HOST: str = "localhost"
QDRANT_PORT: int = 6334  # ← Изменено на 6334
QDRANT_COLLECTION_NAME: str = "orders_phase1"
```

### services/hybrid_search/app.py
```python
qdrant_client = QdrantClient(
    host="localhost",
    port=6334,  # ← Изменено на 6334
    timeout=30
)
```

### services/rag_llm/app.py
```python
QDRANT_HOST = "localhost"
QDRANT_PORT = 6334  # ← Добавлено для возможного прямого подключения
```

---

## 🚀 Запуск Qdrant #2 (для заказов на 6334)

### Docker command:
```bash
docker run -d \
  --name qdrant-orders \
  -p 6334:6334 \
  -e QDRANT_REST_API_URI="0.0.0.0:6334" \
  -v qdrant-orders-data:/qdrant/storage \
  qdrant/qdrant:latest
```

### Проверка:
```bash
curl http://localhost:6334/health
# {"ok":true}
```

---

## 📋 Инициализация collection (orders_phase1 на 6334)

```bash
python services/qdrant_setup.py \
  --host localhost \
  --port 6334 \
  --collection-name orders_phase1 \
  --recreate
```

**Ожидаемый результат:**
```
✅ Collection 'orders_phase1' создана успешно
   Named vectors:
   - phase1_text_dense (COSINE, 1536 dim) - semantic embeddings
   - phase1_text_bm25 (DOT, 10000 dim) - BM25 keyword search
```

---

## 🔄 Архитектура потока данных

```
ФАЗА 1: СТАДИЯ 1 (Индексирование заказов)
═══════════════════════════════════════════

1С (100K+ заказов)
        ↓
[Batch Indexer] → localhost:6334/collections/orders_phase1
        ↓
Docling (Phase 1: парсинг текста)
        ↓
BM25 векторизация + Dense embedding
        ↓
QDRANT #2 (orders_phase1 collection)


ФАЗА 1: СТАДИЯ 2 (Анализ новой макеты)
════════════════════════════════════════

[AIАссистент в 1С]
        ↓
Phase 0 (Vision) + Phase 1 (Docling)
        ↓
[Hybrid Search] (localhost:8002)
        ├─ Подключается к: localhost:6334
        ├─ Ищет в: orders_phase1
        └─ Возвращает: TOP-3 похожих заказа
        ↓
[RAG LLM Pipeline] (localhost:8003)
        ├─ Получает контекст из Hybrid Search
        ├─ Объединяет Phase0 + Phase1 + RAG
        └─ Отправляет на LLM (Claude/GPT-4o)
        ↓
JSON параметров с confidence scores
        ↓
Создание заказа в 1С
```

---

## 🎯 Резюме конфигурации

| Сервис | Читает из | Пишет в | Комментарий |
|--------|----------|---------|-----------|
| **Batch Indexer** | 1С (HTTP API) | qdrant-orders:6334 | Индексирует заказы |
| **Hybrid Search** | qdrant-orders:6334 | - | Ищет похожие заказы |
| **RAG LLM** | Hybrid Search:8002 | - | Генерирует параметры |
| **qdrant-codebase** | - | - | Код на 6333 (отдельно) |
| **qdrant-orders** | - | - | Заказы на 6334 (новый) |

---

## 📊 Проверка обеих Qdrant инстанций

```bash
# Qdrant #1 (Codebase на 6333)
curl http://localhost:6333/health
# {"ok":true}

# Qdrant #2 (Orders на 6334)
curl http://localhost:6334/health
# {"ok":true}

# Список collections на 6334
curl http://localhost:6334/collections | jq .
# {
#   "collections": [
#     {
#       "name": "orders_phase1",
#       "vectors_count": 1000
#     }
#   ]
# }
```

---

## 🔌 Docker Compose (обновленный)

```yaml
version: '3.8'

services:
  
  # Qdrant для кодовой базы
  qdrant-codebase:
    image: qdrant/qdrant:latest
    container_name: qdrant-codebase
    ports:
      - "6333:6333"
    volumes:
      - qdrant-codebase-data:/qdrant/storage
    environment:
      - QDRANT_REST_API_URI=0.0.0.0:6333
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/health"]
      interval: 10s
      timeout: 5s
      retries: 3

  # Qdrant для заказов (НОВЫЙ)
  qdrant-orders:
    image: qdrant/qdrant:latest
    container_name: qdrant-orders
    ports:
      - "6334:6334"
    volumes:
      - qdrant-orders-data:/qdrant/storage
    environment:
      - QDRANT_REST_API_URI=0.0.0.0:6334
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6334/health"]
      interval: 10s
      timeout: 5s
      retries: 3

  # Docling FastAPI (парсинг PDF/JPG)
  docling:
    build: ./services/docling
    container_name: docling
    ports:
      - "8001:8001"

  # Batch Indexer (индексирует заказы в qdrant-orders:6334)
  batch-indexer:
    build: ./services/batch_indexer
    container_name: batch-indexer
    depends_on:
      qdrant-orders:
        condition: service_healthy
    environment:
      - QDRANT_HOST=qdrant-orders
      - QDRANT_PORT=6334
      - QDRANT_COLLECTION_NAME=orders_phase1
      - INDEXER_MODE=once
      - MAX_ORDERS_TO_INDEX=100

  # Hybrid Search (ищет в qdrant-orders:6334)
  hybrid-search:
    build: ./services/hybrid_search
    container_name: hybrid-search
    ports:
      - "8002:8002"
    depends_on:
      qdrant-orders:
        condition: service_healthy
    environment:
      - QDRANT_HOST=qdrant-orders
      - QDRANT_PORT=6334

  # RAG LLM Pipeline (использует Hybrid Search)
  rag-llm:
    build: ./services/rag_llm
    container_name: rag-llm
    ports:
      - "8003:8003"
    depends_on:
      - hybrid-search
    environment:
      - HYBRID_SEARCH_URL=http://hybrid-search:8002
      - LLM_API_URL=aichat-okil-sato.kartochka.tech

volumes:
  qdrant-codebase-data:
  qdrant-orders-data:
```

**Запуск:**
```bash
docker-compose up -d
docker-compose ps
docker-compose logs -f
```

---

## ✨ ИТОГОВАЯ КОНФИГУРАЦИЯ

✅ **QDRANT #1 (6333)** - Кодовая база (существует)
✅ **QDRANT #2 (6334)** - Индексирование заказов (НОВЫЙ)
✅ **Batch Indexer** → qdrant-orders:6334
✅ **Hybrid Search** → qdrant-orders:6334
✅ **RAG LLM Pipeline** → использует Hybrid Search

ФАЗА 1 полностью настроена на работу с вторым Qdrant (6334) для индексирования и поиска заказов.
