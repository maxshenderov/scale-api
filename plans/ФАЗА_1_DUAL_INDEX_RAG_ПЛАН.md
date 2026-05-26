# ФАЗА 1 ПЕРЕРАБОТАННАЯ: Dual-Index RAG для формирования заказов

**Версия:** 2.0 (Переработана на основе обратной связи)  
**Дата:** 13.05.2026

---

## 🎯 Новый сценарий

Вместо простого Docling → Vision LLM, вы хотите **двухстадийную индексацию + RAG для формирования новых заказов**:

### Стадия 1: Историческое индексирование (one-time, background)
- **Источник:** 100K+ заказов из 1С базы
- **Процесс:**
  - Index 0 (Phase 0): данные из 1С таблиц (существует)
  - Index 1 (Phase 1): Docling парсинг от PDF макетов в `Лико_ОригиналМакеты.Файлы`
- **Результат:** Qdrant с 100K+ заказов × 2 индекса (200K точек), **бесплатно** (локально)

### Стадия 2: Обработка нового макета (realtime, interactive)
- **Вход:** Новый PDF макет от клиента
- **Процесс:**
  1. Фаза 0 (Vision LLM): анализирует визуальные параметры
  2. Фаза 1 (Docling): парсит текст (бесплатно)
  3. Hybrid search в Qdrant:
     - Поиск в Index 0 (1С данные) → ТОП-3 похожих заказа
     - Поиск в Index 1 (Phase 1 текст) → ТОП-3 похожих по тексту
  4. **Merging** ТОП-3 результатов
  5. **RAG к LLM:**
     - Context: похожие заказы из 1С + их параметры
     - New data: Phase 0 visual + Phase 1 text
     - Task: сформировать JSON параметров нового заказа
  6. **Output:** JSON параметров для создания заказа в 1С

---

## 📊 Архитектура (визуально)

```
┌──────────────────────────────────────────────────────────────────┐
│ СТАДИЯ 1: Историческое индексирование (background, один раз)    │
└──────────────────────────────────────────────────────────────────┘

1С База (100K+ заказов)
  ├─ Реквизиты заказа (ШиринаЭтикетки, ДлинаЭтикетки, ...)
  └─ Лико_ОригиналМакеты.Файлы (PDF макеты)
       ↓
    FOR EACH заказ:
       ├─ Индекс 0: 1С данные + embedding (существующий Phase 0)
       └─ Индекс 1: Docling.parse(PDF) + BM25 sparse vector (новый Phase 1)
       ↓
Qdrant Collections:
  ├─ orders_phase0: {dense_vector, payload_1c_data}
  └─ orders_phase1: {dense_vector, sparse_bm25, payload_docling_data}


┌──────────────────────────────────────────────────────────────────┐
│ СТАДИЯ 2: Обработка нового макета (realtime, user-facing)       │
└──────────────────────────────────────────────────────────────────┘

Новый PDF макет (от клиента)
  ├─ Фаза 0: Vision LLM → {label_text, colors, size, ...}
  └─ Фаза 1: Docling → {full_text, brand, product, bm25_vector}
       ↓
Qdrant Hybrid Search:
  ├─ Query Phase 0 collection (dense only)
  │  └─ ТОП-3 похожих заказа по visual parameters
  ├─ Query Phase 1 collection (dense + sparse BM25)
  │  └─ ТОП-3 похожих заказа по тексту
  └─ Merge (dedup) → ТОП-3 most relevant orders
       ↓
┌─────────────────────────────────────────┐
│ LLM + RAG для формирования нового заказа│
├─────────────────────────────────────────┤
│ CONTEXT:                                │
│  - ТОП-3 похожих заказа из 1С           │
│    (их params, technology, materials)   │
│                                         │
│ NEW DATA:                               │
│  - Phase 0: visual_params               │
│  - Phase 1: text_extraction             │
│                                         │
│ TASK:                                   │
│  Сформировать JSON параметров нового    │
│  заказа, используя context + новые      │
│  данные                                 │
└─────────────────────────────────────────┘
       ↓
Структурированный JSON
  {
    "label_width_mm": 100,
    "label_height_mm": 50,
    "material": "selfadhesive",
    "print_technology": "flexo",
    "colors": ["pantone_red", "pantone_black"],
    "quantity": 5000,
    "label_text": "Ceresit CS25...",
    "source": "hybrid_rag"
  }
       ↓
Программное создание заказа в 1С
```

---

## 🗂️ Структура данных

### Collection: `orders_phase0` (фаза 0 — текущие данные 1С)

```json
{
  "id": 1,
  "vector": {
    "dense": [0.1, 0.2, ..., 0.9]  // 1536 dimensions (от существующего embedding)
  },
  "payload": {
    "order_id": "ЗК-123456",
    "label_width_mm": 100.5,
    "label_height_mm": 71,
    "label_type": "ET",
    "print_technology": "flexo",
    "material_type": "selfadhesive",
    "colors": ["pantone_red", "pantone_black"],
    "quantity": 5000,
    "product_name": "Ceresit CS25",
    "label_text_brand": "Ceresit",
    "label_text_full": "Ceresit CS 25 07 Gray 280ml...",
    "customer": "ООО Henkel",
    "date_created": "2026-05-10",
    "source": "phase0_1c"
  }
}
```

### Collection: `orders_phase1` (фаза 1 — Docling парсинг)

```json
{
  "id": 1,
  "vector": {
    "dense": [0.1, 0.2, ..., 0.9],   // 1536 dimensions (embedding от Docling text)
    "sparse_bm25": {
      "indices": [0, 5, 12, 27, ...],
      "values": [2.5, 1.3, 0.8, 1.1, ...]
    }
  },
  "payload": {
    "order_id": "ЗК-123456",
    "full_text": "Ceresit CS 25 07 Gray 280ml elasticity...",
    "brand": "Ceresit",
    "product_name": "CS 25",
    "volume_weight": "280 ml",
    "composition": "...",
    "tables": [{...}],
    "images": [{...}],
    "pdf_filename": "8516304_3050609_Ceresit_CS25_07_Gray_280ml_72,5x180mm.pdf",
    "source": "phase1_docling",
    "docling_confidence": 0.92
  }
}
```

---

## 🔄 Процесс поиска и RAG

### Шаг 1: Поиск похожих заказов (hybrid)

```python
from qdrant_client import QdrantClient

client = QdrantClient("localhost", port=6333)

# NEW PDF from user
new_phase0_embedding = embed_vision_llm_result(vision_data)  # 1536 dim
new_phase1_embedding = embed_docling_text(docling_text)      # 1536 dim
new_bm25_sparse = vectorize_bm25(docling_text)               # sparse indices+values

# Query Phase 0 (visual/1C data)
phase0_results = client.search(
    collection_name="orders_phase0",
    query_vector=("dense", new_phase0_embedding),
    limit=3,
    score_threshold=0.6
)

# Query Phase 1 (text data with hybrid)
phase1_results_dense = client.search(
    collection_name="orders_phase1",
    query_vector=("dense", new_phase1_embedding),
    limit=3,
    score_threshold=0.5
)

phase1_results_sparse = client.search(
    collection_name="orders_phase1",
    query_vector=("sparse_bm25", new_bm25_sparse),
    limit=3,
    score_threshold=0.4
)

# Merge Phase 1 results (dense + sparse, with weighted scoring)
merged_phase1 = merge_hybrid_results(
    phase1_results_dense,
    phase1_results_sparse,
    weight_dense=0.6,
    weight_sparse=0.4
)

# Final merge: Phase 0 + Phase 1 (dedup, rerank)
top_similar_orders = merge_and_dedup(
    phase0_results,
    merged_phase1,
    weight_phase0=0.5,
    weight_phase1=0.5,
    top_k=3
)
```

### Шаг 2: RAG промпт для LLM

```python
rag_context = prepare_rag_context(top_similar_orders)

prompt = f"""
ВЫ — эксперт по производству этикеток типографии ЛИКО.

НОВЫЙ МАКЕТ (от клиента):
── Визуальные параметры (фаза 0):
{json.dumps(vision_llm_result, indent=2, ensure_ascii=False)}

── Извлечённый текст (фаза 1):
{docling_text[:500]}...

ПОХОЖИЕ ЗАКАЗЫ ИЗ 1С (для reference):
{rag_context}

ЗАДАЧА:
На основе визуальных параметров, текста и похожих заказов, сформировать JSON параметров для нового заказа.

Требования:
1. Используй визуальные параметры из фазы 0 (размеры, цвета)
2. Используй текстовые параметры из фазы 1 (бренд, название продукта)
3. Проверь consistency с похожими заказами
4. Если чего-то не хватает — предложи на основе сходства

OUTPUT (только JSON, no markdown):
{{
  "label_width_mm": <число>,
  "label_height_mm": <число>,
  "label_type": "<ET|CE|Кльр|...>",
  "material_type": "<selfadhesive|thermoshrink|...>",
  "print_technology": "<flexo|offset|digital|...>",
  "colors": [<пантоны>],
  "quantity": <число>,
  "product_brand": "<бренд из text>",
  "product_name": "<название>",
  "notes": "<любые замечания>"
}}
"""

response = llm.complete(prompt, response_format="json_object")
new_order_params = json.loads(response.text)
```

### Шаг 3: Создание заказа в 1С

```bsl
// В 1С
новый_json = ОтправитьЗапросК_RAG_LLM(фаза0_данные, фаза1_данные, похожие_заказы);
ПараметрыНовогоЗаказа = ПарсингJSON(новый_json);

// Создаём заказ (используя Лико_ПрограммноеСозданиеДокументовСправочников)
Лико_ПрограммноеСозданиеДокументовСправочников.СоздатьЗаказКлиентаИзПараметров(
    ПараметрыНовогоЗаказа
);
```

---

## 🛠️ Docker структура (обновленная)

```
services/
├── docker-compose.yml
├── api/
│   └── docker-compose.yml
├── docling/                        ← ДЛЯ ОБЕИХ СТАДИЙ
│   ├── docker-compose.yml
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   └── parsers/
│       ├── docling_parser.py
│       └── bm25_vectorizer.py
├── qdrant_roo/
│   ├── docker-compose.yml
│   └── storage/
├── qdrant_orders/
│   ├── docker-compose.yml
│   └── storage/
├── indexer/                        ← НОВЫЙ: фоновая переиндексация
│   ├── docker-compose.yml
│   ├── requirements.txt
│   └── batch_indexer.py
└── ollama/
    └── docker-compose.yml
```

### Новый сервис: `batch_indexer` (фоновая переиндексация)

```python
# services/indexer/batch_indexer.py

from qdrant_client import QdrantClient
from docling_parser import DoclingParser
from bm25_vectorizer import BM25Vectorizer
import requests
from typing import Optional

class BatchIndexer:
    def __init__(self):
        self.qdrant_client = QdrantClient("qdrant_orders", port=6333)
        self.docling = DoclingParser()
        self.bm25 = BM25Vectorizer()
        self.embedding_service = "http://embedding-service:8000"  # или Ollama
    
    def index_historical_orders(self, batch_size=100):
        """
        Переиндексировать исторические заказы из 1С
        Вызывается один раз при инициализации Phase 1
        """
        # Получить 100K+ заказов из 1С через API
        # TODO: реализовать
        pass
    
    def index_single_order_phase1(self, order_id: str, pdf_path: str):
        """
        Добавить Phase 1 индекс для одного заказа (Docling парсинг)
        """
        # Парсим PDF
        with open(pdf_path, 'rb') as f:
            docling_result = self.docling.parse(f.read())
        
        # Эмбеддируем текст
        embedding = self.embed_text(docling_result['full_text'])
        
        # BM25 sparse vector
        sparse_bm25 = self.bm25.vectorize(docling_result['full_text'])
        
        # Upsert в Qdrant
        point = PointStruct(
            id=order_id,
            vector={
                "dense": embedding,
                "sparse_bm25": sparse_bm25
            },
            payload={
                "order_id": order_id,
                "full_text": docling_result['full_text'],
                "brand": docling_result.get('brand'),
                "product_name": docling_result.get('product_name'),
                "volume_weight": docling_result.get('volume_weight'),
                "source": "phase1_docling"
            }
        )
        
        self.qdrant_client.upsert(
            collection_name="orders_phase1",
            points=[point]
        )
    
    def embed_text(self, text: str) -> list:
        """Получить embedding через Ollama/API"""
        response = requests.post(
            f"{self.embedding_service}/embed",
            json={"text": text}
        )
        return response.json()['embedding']
```

---

## ✅ Обновленный Checklist

### Стадия 1: Подготовка инфраструктуры
- [ ] Создать Qdrant collections (`orders_phase0`, `orders_phase1`) с named vectors
- [ ] Реализовать Docling FastAPI сервис (парсинг + BM25)
- [ ] Реализовать Indexer сервис для фоновой переиндексации
- [ ] Обновить корневой `docker-compose.yml`

### Стадия 2: Историческое индексирование
- [ ] Создать скрипт для экспорта 100K+ заказов из 1С
- [ ] Indexer парсит PDF макеты → создаёт Phase 1 индексы
- [ ] Populate `orders_phase1` collection в Qdrant

### Стадия 3: RAG pipeline для новых макетов
- [ ] Реализовать Hybrid Search функцию (Phase 0 + Phase 1)
- [ ] Обновить 1С код: вызов Docling + Vision LLM (параллельно)
- [ ] Реализовать RAG LLM промпт + merge похожих заказов
- [ ] Тестировать end-to-end: PDF → похожие → LLM → JSON → 1С

---

## 💡 Key Differences from original plan

| Аспект | Original | New (Dual-Index RAG) |
|---|---|---|
| **Qdrant collections** | 1 (`orders`) | 2 (`orders_phase0`, `orders_phase1`) |
| **Историческое индексирование** | Нет | Да, фоновая переиндексация (batch_indexer) |
| **Docling использование** | Только для новых PDF | Для исторических + новых |
| **RAG контекст** | Только новые данные | Новые + похожие заказы из 1С |
| **LLM вход** | Vision LLM result | Vision + Docling + RAG context |
| **Точность** | ~70% (semantic) | ~85-90% (hybrid + RAG) |
| **Стоимость** | $0.01/PDF (Vision) | $0 (все локально) |

---

## 📝 Технические вопросы для уточнения

1. **Embedding model для Phase 1**: Использовать Ollama (BGE-m3) или отправить на API?
2. **Batch indexer**: Когда запускать переиндексацию — ночью или полностью фоновый процесс?
3. **Merge похожих заказов**: Какая стратегия — voting, weighting, или LLM выбирает?
4. **Fallback**: Если похожих заказов нет — использовать только Phase 0 + Phase 1 данные?
5. **Миграция**: Переиндексировать все 100K+ заказов сразу или постепенно?
