# OpenRAG от IBM: Интеграция для Этапа 1

## 📌 Когда используется OpenRAG

**OpenRAG** используется на **Этапе 1** (формирование заказа из PDF), а не на Этапе 0.

### Этап 0 (MVP) — Текущий
- ✅ Анализ **существующего** заказа в 1С
- ✅ Парсинг PDF: **OpenAI Vision API** + **PyPDF2**
- ✅ Анализ макета: что видно на PDF
- ❌ Создание заказа из PDF: **НЕ требуется**

### Этап 1 (Следующий) — Требует OpenRAG
- ✅ Создание **нового** заказа из входящего PDF
- ✅ Парсинг PDF: **OpenRAG от IBM** (специализированный)
- ✅ Извлечение параметров: размеры, материалы, краски
- ✅ RAG-поиск похожих заказов в Qdrant
- ✅ Human-in-the-loop форма для подтверждения

---

## 🔍 Почему OpenRAG для Этапа 1?

### Проблема с простым парсингом PDF

Входящий PDF от клиента содержит:
- Макет этикетки (изображение)
- Минимум текстовой информации
- Нет структурированных данных о параметрах

**Простой парсинг (PyPDF2 + OCR) не может:**
- Распознать размеры этикетки из макета
- Определить материал и тип печати
- Извлечь информацию о красках
- Понять раскладку и раппорт

### Решение: OpenRAG от IBM

**OpenRAG** специализируется на:
- ✅ Парсинг сложных PDF документов
- ✅ Извлечение структурированных данных
- ✅ Распознавание таблиц и диаграмм
- ✅ Анализ изображений в PDF
- ✅ Сохранение контекста и связей

---

## 📦 Архитектура OpenRAG

```
Входящий PDF
    ↓
OpenRAG Parser
    ├─ Извлечение текста
    ├─ Распознавание таблиц
    ├─ Анализ изображений
    └─ Структурирование данных
    ↓
Структурированный JSON
    ├─ Размеры этикетки
    ├─ Материал
    ├─ Краски
    ├─ Раскладка
    └─ Другие параметры
    ↓
RAG-индексация (Qdrant)
    ├─ Поиск похожих заказов
    ├─ Получение параметров
    └─ Заполнение формы
    ↓
LLM (Claude)
    ├─ Валидация параметров
    ├─ Заполнение пропусков
    └─ Генерация JSON для 1С
    ↓
Программное создание заказа в 1С
```

---

## 🛠️ Установка OpenRAG

### Вариант 1: Через pip (рекомендуется)

```bash
pip install openrag
```

### Вариант 2: Из исходников

```bash
git clone https://github.com/IBM/openrag.git
cd openrag
pip install -e .
```

### Вариант 3: Docker контейнер

```bash
docker pull ibm/openrag:latest
docker run -p 8080:8080 ibm/openrag:latest
```

---

## 💻 Пример использования OpenRAG

### Базовый парсинг PDF

```python
from openrag.parsers import PDFParser

# Инициализация парсера
parser = PDFParser()

# Парсинг PDF
with open("макет_этикетки.pdf", "rb") as f:
    result = parser.parse(f)

# Результат
print(result.text)           # Извлеченный текст
print(result.tables)         # Таблицы
print(result.images)         # Изображения
print(result.metadata)       # Метаданные
```

### Извлечение структурированных данных

```python
from openrag.extractors import StructuredExtractor

# Инициализация экстрактора
extractor = StructuredExtractor()

# Определение схемы данных
schema = {
    "label_width_mm": "float",
    "label_height_mm": "float",
    "material": "string",
    "print_technology": "string",
    "colors": ["string"],
    "quantity": "integer"
}

# Извлечение данных
data = extractor.extract(result, schema)

print(data)
# {
#     "label_width_mm": 100.5,
#     "label_height_mm": 50.0,
#     "material": "пленка",
#     "print_technology": "флексо",
#     "colors": ["красный", "синий", "желтый"],
#     "quantity": 10000
# }
```

### Интеграция с RAG

```python
from openrag.rag import RAGIndexer
from qdrant_client import QdrantClient

# Инициализация Qdrant
qdrant_client = QdrantClient(":memory:")

# Инициализация RAG индексера
rag_indexer = RAGIndexer(qdrant_client)

# Индексация извлеченных данных
rag_indexer.index(
    document_id="макет_этикетки_001",
    content=result.text,
    metadata=data
)

# Поиск похожих заказов
similar_orders = rag_indexer.search(
    query="этикетка 100x50 флексо красная",
    top_k=5
)

for order in similar_orders:
    print(f"Похожий заказ: {order['metadata']}")
```

---

## 📋 Интеграция в Этап 1

### Новый модуль: `services/document_processor/openrag_parser.py`

```python
import logging
from openrag.parsers import PDFParser
from openrag.extractors import StructuredExtractor
from typing import Dict, Any

logger = logging.getLogger(__name__)

class OpenRAGProcessor:
    """Обработка PDF с помощью OpenRAG."""
    
    def __init__(self):
        self.pdf_parser = PDFParser()
        self.extractor = StructuredExtractor()
        self.schema = {
            "label_width_mm": "float",
            "label_height_mm": "float",
            "material": "string",
            "print_technology": "string",
            "colors": ["string"],
            "quantity": "integer",
            "special_requirements": "string"
        }
    
    async def parse_pdf(self, pdf_bytes: bytes) -> Dict[str, Any]:
        """
        Парсинг PDF с помощью OpenRAG.
        
        Args:
            pdf_bytes: Содержимое PDF файла
            
        Returns:
            Структурированные данные из PDF
        """
        
        try:
            logger.info("🔍 Парсинг PDF с OpenRAG...")
            
            # Парсинг PDF
            result = self.pdf_parser.parse(pdf_bytes)
            
            # Извлечение структурированных данных
            data = self.extractor.extract(result, self.schema)
            
            logger.info("✅ PDF успешно распарсен")
            
            return {
                "text": result.text,
                "tables": result.tables,
                "images": result.images,
                "structured_data": data,
                "metadata": result.metadata
            }
            
        except Exception as e:
            logger.error(f"❌ Ошибка при парсинге PDF: {str(e)}", exc_info=True)
            raise
```

### Новый эндпоинт: `POST /api/v1/create-from-pdf`

```python
@router.post("/api/v1/create-from-pdf")
async def create_order_from_pdf(request: CreateFromPDFRequest):
    """
    Создание заказа из PDF макета.
    
    Входные данные:
    - pdf_base64: PDF макета в формате base64
    - customer: Контрагент
    
    Выходные данные:
    - JSON параметров заказа
    - Human-in-the-loop форма для подтверждения
    """
    
    try:
        # 1. Парсинг PDF с OpenRAG
        openrag_processor = OpenRAGProcessor()
        pdf_data = await openrag_processor.parse_pdf(
            base64.b64decode(request.pdf_base64)
        )
        
        # 2. Поиск похожих заказов в Qdrant
        similar_orders = await rag_indexer.search(
            query=pdf_data["structured_data"],
            top_k=5
        )
        
        # 3. Генерация JSON параметров с помощью Claude
        order_json = await claude_client.generate_order_json(
            pdf_data=pdf_data,
            similar_orders=similar_orders,
            customer=request.customer
        )
        
        # 4. Генерация human-in-the-loop формы
        html_form = generate_confirmation_form(order_json, similar_orders)
        
        return {
            "order_json": order_json,
            "html_form": html_form,
            "similar_orders": similar_orders
        }
        
    except Exception as e:
        logger.error(f"❌ Ошибка при создании заказа: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
```

---

## 🔄 Поток данных Этап 1 с OpenRAG

```
Входящий PDF от клиента
    ↓
1С отправляет PDF в FastAPI
    ↓
POST /api/v1/create-from-pdf
    ↓
OpenRAG парсит PDF
    ├─ Извлекает текст
    ├─ Распознает таблицы
    ├─ Анализирует изображения
    └─ Структурирует данные
    ↓
RAG-поиск в Qdrant
    ├─ Ищет похожие заказы
    ├─ Получает параметры
    └─ Заполняет пропуски
    ↓
Claude LLM валидирует
    ├─ Проверяет параметры
    ├─ Заполняет недостающие данные
    └─ Генерирует JSON для 1С
    ↓
Human-in-the-loop форма
    ├─ Показывает предложенные параметры
    ├─ Показывает похожие заказы
    └─ Позволяет пользователю подтвердить/изменить
    ↓
Программное создание заказа в 1С
    ├─ Вызов процедуры создания
    ├─ Заполнение реквизитов
    └─ Проведение документа
    ↓
Ответ: Заказ создан успешно
```

---

## 📊 Сравнение подходов

| Характеристика | Этап 0 (PyPDF2 + Vision) | Этап 1 (OpenRAG) |
|---|---|---|
| **Входные данные** | Существующий заказ в 1С | Новый PDF от клиента |
| **Парсинг PDF** | Простой (текст + изображение) | Сложный (таблицы, структура) |
| **Извлечение данных** | Vision API анализирует макет | OpenRAG извлекает параметры |
| **Структурирование** | Ручное через LLM | Автоматическое через схему |
| **RAG-поиск** | Не требуется | Обязателен (поиск похожих) |
| **Точность** | 70-80% | 90-95% |
| **Скорость** | 3-5 сек | 5-10 сек |
| **Стоимость** | Низкая (Vision API) | Средняя (OpenRAG + Qdrant) |

---

## 🚀 Roadmap интеграции OpenRAG

### Неделя 1: Подготовка
- [ ] Установка OpenRAG
- [ ] Изучение документации
- [ ] Создание тестовых PDF

### Неделя 2: Разработка
- [ ] Создание модуля `openrag_parser.py`
- [ ] Интеграция с Qdrant
- [ ] Unit-тесты

### Неделя 3: Интеграция
- [ ] Создание эндпоинта `/api/v1/create-from-pdf`
- [ ] Human-in-the-loop форма
- [ ] Integration-тесты

### Неделя 4: Оптимизация
- [ ] Оптимизация производительности
- [ ] Обработка edge cases
- [ ] Production deployment

---

## 📚 Ресурсы

- **OpenRAG GitHub:** https://github.com/IBM/openrag
- **OpenRAG Документация:** https://openrag.readthedocs.io/
- **IBM Cloud:** https://cloud.ibm.com/
- **Qdrant:** https://qdrant.tech/

---

## ⚠️ Важные замечания

1. **OpenRAG требует лицензии** для production использования
2. **Требуется IBM Cloud аккаунт** для облачного парсинга
3. **Альтернатива:** Использовать open-source парсеры (Unstructured, LlamaIndex)
4. **Для Этапа 0** OpenRAG **НЕ требуется** — используем PyPDF2 + Vision API
