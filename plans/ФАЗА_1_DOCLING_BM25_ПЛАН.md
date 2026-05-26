# ФАЗА 1: IBM Docling + BM25 Sparse Vectors — План реализации в Docker

**Версия:** 1.0  
**Дата:** 13.05.2026  
**Статус:** Архитектурное планирование

---

## 🎯 Цель Фазы 1

**Заменить Vision LLM парсинг PDF на специализированный Docling** и добавить **гибридный поиск** (dense + sparse vectors) в Qdrant.

### Проблема Фазы 0
- Vision LLM стоит дорого (~$0.01 за PDF)
- Неэффективно использовать Vision для **текста** — это работа для OCR/PDF парсера
- Нет keyword search в Qdrant — только semantic (dense vectors)

### Решение Фазы 1
1. **Docling** → дешево парсит PDF текст (OCR + PDF структура)
2. **BM25** → sparse vectors для keyword search
3. **Hybrid search** в Qdrant → `(dense_vector × 0.7) + (sparse_bm25 × 0.3)`

---

## 📊 Архитектура Фазы 1

```
Входящий PDF
    ↓
┌─────────────────────────┐
│ Docling FastAPI Service │  ← Новый сервис в Docker
├─────────────────────────┤
│ POST /parse-pdf         │
│ - Парсинг PDF (OCR)     │
│ - Извлечение текста     │
│ - Структурирование JSON │
└─────────────────────────┘
    ↓
  JSON:
  {
    "full_text": "Ceresit CS25...",
    "brand": "Ceresit",
    "product_name": "CS 25",
    "volume_weight": "280 ml",
    "tables": [{...}],
    "images": [{...}]
  }
    ↓
┌──────────────────────────────┐
│ Vision LLM (GPT-4o)          │  ← Сохраняется для параметров
│ POST /analyze-layout         │
├──────────────────────────────┤
│ INPUT: full_text + image_url │
│ OUTPUT: JSON структура       │
│ - label_text (из Docling)    │
│ - visual_params (цвета, ...)  │
└──────────────────────────────┘
    ↓
  Структурированный JSON для 1С
    ↓
┌──────────────────────────────┐
│ BM25 Vectorizer (Python)     │  ← Часть Docling сервиса
├──────────────────────────────┤
│ - Токенизация текста         │
│ - BM25 sparse vector         │
│ - Хранение в Qdrant (named)  │
└──────────────────────────────┘
    ↓
Qdrant (hybrid search)
  - Dense vector (semantic)
  - Sparse vector (BM25, keyword)
  - Named vectors поддержка
```

---

## 🗂️ Структура Docker сервисов

```
services/
├── docker-compose.yml              ← Корневой (все сервисы)
├── api/                             ← Существующий основной API
│   ├── docker-compose.yml
│   └── requirements.txt
├── docling/                         ← НОВЫЙ: Docling парсинг
│   ├── docker-compose.yml
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                     ← FastAPI приложение
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── docling_parser.py       ← Обёртка для Docling
│   │   └── bm25_vectorizer.py      ← BM25 sparse vectors
│   └── models/
│       ├── __init__.py
│       └── schemas.py              ← Pydantic schemas
├── qdrant_roo/                      ← Существующий Qdrant (Roo)
│   └── docker-compose.yml
├── qdrant_orders/                   ← Существующий Qdrant (заказы)
│   └── docker-compose.yml
└── ollama/                          ← Существующий Ollama
    └── docker-compose.yml
```

---

## 🚀 Компоненты Фазы 1

### 1. Docling FastAPI Service (`services/docling/`)

**Задача:** Парсить PDF и извлекать структурированный текст дешево.

#### 1.1 `main.py`

```python
from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel
from parsers.docling_parser import DoclingParser
from parsers.bm25_vectorizer import BM25Vectorizer
import json

app = FastAPI(title="Docling Service", version="1.0")

docling = DoclingParser()
bm25 = BM25Vectorizer()

@app.post("/parse-pdf")
async def parse_pdf(file: UploadFile = File(...)):
    """
    Парсить PDF и вернуть структурированный текст + BM25 vector
    """
    content = await file.read()
    
    # Парсим PDF через Docling
    result = docling.parse(content)
    
    # Генерируем BM25 sparse vector
    sparse_vector = bm25.vectorize(result["full_text"])
    
    return {
        "full_text": result["full_text"],
        "brand": result.get("brand"),
        "product_name": result.get("product_name"),
        "volume_weight": result.get("volume_weight"),
        "tables": result.get("tables", []),
        "images": result.get("images", []),
        "bm25_vector": sparse_vector,
        "bm25_indices": sparse_vector["indices"],
        "bm25_values": sparse_vector["values"]
    }

@app.get("/health")
def health():
    return {"status": "ok"}
```

#### 1.2 `parsers/docling_parser.py`

```python
from docling.document_converter import DocumentConverter
from docling_core.types import Document
import io
import json

class DoclingParser:
    def __init__(self):
        self.converter = DocumentConverter()
    
    def parse(self, pdf_bytes: bytes) -> dict:
        """
        Парсить PDF через Docling
        Возвращает: {full_text, brand, product_name, volume_weight, tables, images}
        """
        # Конвертируем bytes в документ
        doc_stream = io.BytesIO(pdf_bytes)
        result = self.converter.convert_single(doc_stream)
        
        # Извлекаем текст
        full_text = result.document.export_to_markdown()
        
        # Парсим таблицы
        tables = self._extract_tables(result.document)
        
        # Парсим изображения
        images = self._extract_images(result.document)
        
        return {
            "full_text": full_text,
            "brand": self._extract_brand(full_text),
            "product_name": self._extract_product_name(full_text),
            "volume_weight": self._extract_volume_weight(full_text),
            "tables": tables,
            "images": images
        }
    
    def _extract_brand(self, text: str) -> str:
        """Извлечь бренд из текста (эвристика)"""
        lines = text.split('\n')[:5]  # Первые 5 строк часто содержат бренд
        return lines[0] if lines else ""
    
    def _extract_product_name(self, text: str) -> str:
        """Извлечь название продукта"""
        # Простая эвристика — вторая строка
        lines = text.split('\n')
        return lines[1] if len(lines) > 1 else ""
    
    def _extract_volume_weight(self, text: str) -> str:
        """Извлечь объём/вес (искать паттерны типа '280 ml', '1 кг')"""
        import re
        match = re.search(r'(\d+(?:\.\d+)?)\s*(ml|г|кг|л|l|kg|g)', text, re.IGNORECASE)
        return f"{match.group(1)} {match.group(2)}" if match else ""
    
    def _extract_tables(self, doc: Document) -> list:
        """Извлечь таблицы из документа"""
        # Docling поддерживает таблицы через API
        tables = []
        for table in doc.tables:
            tables.append({
                "type": "table",
                "content": str(table)
            })
        return tables
    
    def _extract_images(self, doc: Document) -> list:
        """Извлечь изображения (базовая информация)"""
        images = []
        for figure in doc.figures:
            images.append({
                "type": "image",
                "description": figure.label or "no description"
            })
        return images
```

#### 1.3 `parsers/bm25_vectorizer.py`

```python
from rank_bm25 import BM25Okapi
from nltk.tokenize import word_tokenize
from nltk.corpus import stopwords
import nltk

nltk.download('punkt')
nltk.download('stopwords')

class BM25Vectorizer:
    def __init__(self, language='russian'):
        self.language = language
        self.tokenizer = self._get_tokenizer()
        self.stop_words = set(stopwords.words(language))
    
    def _get_tokenizer(self):
        def tokenize(text):
            tokens = word_tokenize(text.lower(), language=self.language)
            # Убираем стоп-слова и пунктуацию
            filtered = [t for t in tokens if t.isalnum() and t not in self.stop_words]
            return filtered
        return tokenize
    
    def vectorize(self, text: str) -> dict:
        """
        Создать BM25 sparse vector для текста
        
        Возвращает:
        {
            "indices": [0, 5, 12, ...],      # Индексы токенов в словаре
            "values": [2.5, 1.3, 0.8, ...]   # BM25 scores
        }
        """
        # Токенизируем текст
        tokens = self.tokenizer(text)
        
        # Создаём "corpus" из одного документа для BM25
        bm25 = BM25Okapi([tokens])
        
        # Получаем BM25 scores для каждого токена
        scores = bm25.get_scores([tokens])[0]  # Это даст массив scores
        
        # Преобразуем в sparse vector format (индекс → score)
        indices = []
        values = []
        
        for idx, score in enumerate(scores):
            if score > 0:  # Только ненулевые
                indices.append(idx)
                values.append(float(score))
        
        return {
            "indices": indices,
            "values": values,
            "vocab_size": len(tokens)
        }
```

#### 1.4 `requirements.txt`

```
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
docling>=1.16.0
docling-core>=2.3.0
rank-bm25>=0.2.2
nltk>=3.8.1
python-dotenv>=1.0.0
httpx>=0.25.0
```

#### 1.5 `docker-compose.yml`

```yaml
version: '3.8'

services:
  docling:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: docling_service
    ports:
      - "8001:8000"
    environment:
      - PYTHONUNBUFFERED=1
    volumes:
      - ./logs:/app/logs
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

#### 1.6 `Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download NLTK data
RUN python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords')"

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

### 2. Qdrant Named Vectors (BM25 sparse)

**Задача:** Хранить sparse BM25 vectors как "named vectors" в Qdrant.

#### 2.1 Создание collection с named vectors

```bash
curl -X PUT http://localhost:6333/collections/orders \
  -H "Content-Type: application/json" \
  -d '{
    "vectors": {
      "dense": {
        "size": 1536,
        "distance": "Cosine"
      },
      "sparse_bm25": {
        "datatype": "uint32",
        "index": {
          "type": "mmap"
        }
      }
    }
  }'
```

#### 2.2 Загрузка в Qdrant с named vectors

```python
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, SparseVector

client = QdrantClient("localhost", port=6333)

# Точка с двумя векторами
point = PointStruct(
    id=1,
    vector={
        "dense": [0.1, 0.2, ..., 0.9],          # 1536 dimensions
        "sparse_bm25": SparseVector(
            indices=[0, 5, 12, ...],             # Индексы токенов
            values=[2.5, 1.3, 0.8, ...]          # BM25 scores
        )
    },
    payload={
        "order_id": "123",
        "label_text_full": "Ceresit CS25...",
        ...
    }
)

client.upsert(
    collection_name="orders",
    points=[point]
)
```

#### 2.3 Hybrid Search (dense + sparse)

```python
from qdrant_client.models import Filter, FieldCondition, HasIdCondition

# Поиск по dense vector
dense_results = client.search(
    collection_name="orders",
    query_vector=("dense", embedding_vector),
    limit=10,
    score_threshold=0.7
)

# Поиск по sparse BM25 vector
sparse_results = client.search(
    collection_name="orders",
    query_vector=("sparse_bm25", SparseVector(
        indices=[0, 5, 12],
        values=[2.0, 1.5, 1.0]
    )),
    limit=10,
    score_threshold=0.5
)

# Merging (можно также использовать RRF - Reciprocal Rank Fusion)
hybrid_results = merge_results(dense_results, sparse_results, weight=0.6)
```

---

### 3. Обновление 1С кода

**Задача:** Вызвать Docling перед Vision LLM.

#### 3.1 Новая функция в `Module.bsl`

```bsl
// Вызов Docling для парсинга PDF перед Vision LLM
&НаСервере
Функция ПолучитьТекстИзPDFЧерезDocling(ДвоичныеДанные, НазваниеФайла = "") Экспорт
    
    Попытка
        // 1. Сохранить двоичные данные во временный файл
        ПутьВремФайла = ПолучитьИмяВременногоФайла("pdf");
        ДвоичныеДанные.Записать(ПутьВремФайла);
        
        // 2. Отправить на Docling service
        URLДокЛинга = Лико_ПредопределенныеЗначения.Лико_URLDoclingService;
        // = "http://docling_service:8000/parse-pdf"
        
        ТипКонтента = "multipart/form-data";
        Заголовки = Новый Соответствие();
        
        // Отправляем PDF файл
        HTTPСоединение = Новый HTTPСоединение(
            "docling_service",
            8000,
            Ложь,
            ,
            30
        );
        
        ФайловДляЗагрузки = Новый Массив();
        ФайловДляЗагрузки.Добавить(
            Новый ПередаваемыйФайл(ПутьВремФайла, "file")
        );
        
        Запрос = Новый HTTPЗапрос("/parse-pdf");
        Запрос.УстановитьТело(ФайловДляЗагрузки);
        
        Ответ = HTTPСоединение.ОтправитьДляЗагрузки(Запрос);
        ТекстОтвета = Ответ.ПолучитьТелоКакСтроку("UTF-8");
        
        HTTPСоединение.Закрыть();
        
        // 3. Парсим JSON ответ
        СтруктураОтвета = ПарсингJSON(ТекстОтвета);
        
        Возврат СтруктураОтвета;
        
    Исключение
        СообщитьДляКлиента("Ошибка при вызове Docling: " + ОписаниеОшибки());
        Возврат Неопределено;
    КонецПопытки;
    
КонецФункции

// Обновлённая функция ИзвлечьПараметрыМакета с Docling
&НаСервере
Процедура ИзвлечьПараметрыМакета_Фаза1(ДвоичныеДанные, НазваниеФайла = "")
    
    // Шаг 1: Парсим через Docling (дешево)
    ДанныеДокЛинга = ПолучитьТекстИзPDFЧерезDocling(ДвоичныеДанные, НазваниеФайла);
    
    Если ДанныеДокЛинга = Неопределено Тогда
        СообщитьДляКлиента("Docling парсинг не удался");
        Возврат;
    КонецЕсли;
    
    // Шаг 2: Берём extracted_text из Docling
    ПолныйТекст = ДанныеДокЛинга.full_text;
    
    // Шаг 3: Отправляем на Vision LLM (уже есть label_text от Docling)
    ДополнительныйПромпт = "
    |DOCLING EXTRACTED TEXT:
    |" + ПолныйТекст + "
    |
    |ФАЙЛ: " + НазваниеФайла;
    
    // ... остальная логика ИзвлечьПараметрыМакета ...
    
КонецПроцедуры
```

---

## 📈 Сравнение Фаза 0 vs Фаза 1

| Аспект | Фаза 0 (Vision LLM) | Фаза 1 (Docling + BM25) |
|---|---|---|
| **Парсинг PDF** | $0.01 за PDF (Vision) | $0 (Docling local) |
| **Текст из PDF** | Зависит от Vision | Надёжный OCR + структура |
| **Keyword search** | Нет (только semantic) | Да (BM25 sparse) |
| **Гибридный поиск** | Нет | Да (dense + sparse) |
| **Точность поиска** | ~70% (semantic) | ~85% (hybrid) |
| **Скорость** | Медленная (API) | Быстрая (local) |
| **Стоимость** | ~$100/мес (1000 PDF) | $0 (+Docker) |

---

## 🔄 Миграция данных (если нужна)

Для существующих заказов нужно пересчитать BM25 vectors:

```python
# Script для пересчёта sparse vectors в Qdrant
from qdrant_client import QdrantClient
from parsers.bm25_vectorizer import BM25Vectorizer

client = QdrantClient("localhost", port=6333)
bm25 = BM25Vectorizer()

# Получить все точки
points = client.scroll(collection_name="orders", limit=100)

for point in points[0]:
    # Получить label_text_full из payload
    label_text = point.payload.get("label_text_full", "")
    
    if label_text:
        # Пересчитать BM25
        sparse_vector = bm25.vectorize(label_text)
        
        # Обновить в Qdrant
        client.upsert(
            collection_name="orders",
            points=[PointStruct(
                id=point.id,
                vector={
                    "dense": point.vector["dense"],
                    "sparse_bm25": sparse_vector
                },
                payload=point.payload
            )]
        )
```

---

## ✅ Checklist реализации

- [ ] Создать `services/docling/` структуру
- [ ] Реализовать `main.py` с FastAPI
- [ ] Реализовать `parsers/docling_parser.py`
- [ ] Реализовать `parsers/bm25_vectorizer.py`
- [ ] Создать `Dockerfile` и `docker-compose.yml`
- [ ] Обновить корневой `services/docker-compose.yml` с Docling сервисом
- [ ] Обновить 1С код для вызова Docling
- [ ] Обновить Qdrant collection с named vectors
- [ ] Тестировать hybrid search
- [ ] Документировать API и примеры

---

## 🚀 Запуск в Docker

```bash
# Запустить все сервисы
cd services
docker-compose up -d

# Проверить Docling
curl http://localhost:8001/health

# Загрузить файл для тестирования
curl -X POST http://localhost:8001/parse-pdf \
  -F "file=@/path/to/test.pdf"
```

---

## 📝 Вопросы для уточнения

1. **Ollama**: нужна ли ещё Ollama для embedding генерации, или будем использовать API?
2. **Docling версия**: использовать последнюю (1.16+) или стабильную?
3. **BM25 параметры**: есть ли предпочтения для k1, b параметров?
4. **Sparse vector размер**: сколько максимум ненулевых элементов допустимо?
5. **Миграция данных**: нужно ли пересчитывать BM25 для существующих заказов сразу?
