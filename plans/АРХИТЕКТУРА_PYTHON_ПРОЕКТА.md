# Архитектура Python-проекта AI-ассистента

## 📋 Общее описание

Python-проект — это **FastAPI backend**, который служит мостом между **1С:Предприятие** и **Claude LLM API**. Проект анализирует заказы типографии и возвращает HTML-оценки для технолога и экономиста.

---

## 🔄 Поток данных (Этап 0)

```
┌─────────────────────────────────────────────────────────────────┐
│ 1С:Предприятие (форма ЗаказКлиента)                            │
│ ┌───────────────────────────────────────────────────────────┐   │
│ │ [Кнопка] AI-Ассистент технолога                          │   │
│ │ [Кнопка] AI-Ассистент экономиста                         │   │
│ └───────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                    ↓ HTTP POST
        JSON заказа + PDF (base64)
                    ↓
┌─────────────────────────────────────────────────────────────────┐
│ FastAPI Backend (Python)                                        │
│ ┌───────────────────────────────────────────────────────────┐   │
│ │ POST /api/v1/analyze                                      │   │
│ │ ├─ Получить JSON заказа + PDF                            │   │
│ │ ├─ Нормализовать JSON (order_normalizer.py)              │   │
│ │ ├─ Парсить PDF и анализировать Vision (pdf_processor.py)│   │
│ │ ├─ Отправить в Claude API (llm_client.py)               │   │
│ │ ├─ Сгенерировать HTML (html_generator.py)               │   │
│ │ └─ Вернуть HTML-форму                                    │   │
│ └───────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                    ↓ HTTP Response
                  HTML-форма
                    ↓
┌─────────────────────────────────────────────────────────────────┐
│ 1С:Предприятие (модальное окно)                                 │
│ ┌───────────────────────────────────────────────────────────┐   │
│ │ ┌─────────────────────────────────────────────────────┐   │   │
│ │ │ Техническая оценка                                  │   │   │
│ │ │ - Параметры этикетки                                │   │   │
│ │ │ - Технология печати                                 │   │   │
│ │ │ - Рекомендации                                      │   │   │
│ │ ├─────────────────────────────────────────────────────┤   │   │
│ │ │ Экономическая оценка                                │   │   │
│ │ │ - Себестоимость                                     │   │   │
│ │ │ - Рентабельность                                    │   │   │
│ │ │ - Риски                                             │   │   │
│ │ ├─────────────────────────────────────────────────────┤   │   │
│ │ │ Анализ макета                                       │   │   │
│ │ │ - Что видно на PDF                                  │   │   │
│ │ │ - Качество макета                                   │   │   │
│ │ ├─────────────────────────────────────────────────────┤   │   │
│ │ │ Чек-лист готовности                                 │   │   │
│ │ │ - Что заполнено                                     │   │   │
│ │ │ - Что нужно уточнить                                │   │   │
│ │ └─────────────────────────────────────────────────────┘   │   │
│ └───────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📦 Структура проекта

```
services/
├── api/                          # FastAPI приложение
│   ├── __init__.py
│   ├── main.py                   # Инициализация FastAPI, конфигурация
│   ├── routes.py                 # Эндпоинты (POST /api/v1/analyze)
│   └── models.py                 # Pydantic модели для валидации
│
├── document-processor/           # Обработка документов
│   ├── __init__.py
│   ├── order_normalizer.py       # Нормализация JSON заказа
│   └── pdf_processor.py          # Парсинг PDF и Vision-анализ
│
├── llm/                          # Интеграция с LLM
│   ├── __init__.py
│   ├── llm_client.py             # Клиент Claude API
│   └── prompts.py                # Production-промпты
│
├── html/                         # Генерация HTML
│   ├── __init__.py
│   └── html_generator.py         # Генерация HTML-форм
│
├── tests/                        # Unit-тесты
│   ├── __init__.py
│   ├── test_order_normalizer.py
│   ├── test_pdf_processor.py
│   ├── test_llm_client.py
│   └── test_html_generator.py
│
├── requirements.txt              # Python зависимости
├── .env.example                  # Шаблон переменных окружения
├── Dockerfile                    # Docker контейнер
├── docker-compose.yml            # Docker Compose для локального запуска
├── README.md                     # Документация
├── INSTALL.md                    # Инструкция по установке
└── USAGE.md                      # Инструкция по использованию
```

---

## 🔧 Компоненты и их функции

### 1. **api/main.py** — Инициализация FastAPI
**Что делает:**
- Создаёт FastAPI приложение
- Настраивает CORS для 1С
- Подключает логирование
- Регистрирует маршруты
- Добавляет health-check эндпоинт

**Пример:**
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Liko AI Assistant", version="0.1.0")

# CORS для 1С
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1570"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    return {"status": "ok"}
```

---

### 2. **api/routes.py** — Эндпоинты
**Что делает:**
- Определяет `POST /api/v1/analyze` эндпоинт
- Получает JSON заказа и PDF (base64)
- Координирует вызовы других модулей
- Возвращает HTML-форму

**Пример:**
```python
@router.post("/api/v1/analyze")
async def analyze_order(request: AnalyzeRequest):
    # 1. Нормализовать JSON
    normalized = normalize_order(request.order_json)
    
    # 2. Парсить PDF
    pdf_analysis = analyze_pdf_with_vision(request.pdf_base64)
    
    # 3. Отправить в Claude
    llm_analysis = await claude_client.analyze_order(
        normalized, 
        pdf_analysis,
        request.analysis_type  # "technologist" или "economist"
    )
    
    # 4. Сгенерировать HTML
    html = generate_assessment_html(llm_analysis)
    
    return {"html": html}
```

---

### 3. **document-processor/order_normalizer.py** — Нормализация JSON
**Что делает:**
- Преобразует сырой JSON из 1С в стандартный формат
- Извлекает и структурирует данные:
  - Шапка заказа (реквизиты)
  - Табличные части (Цвета, ВидыРабот, Товары)
  - Производственные параметры
  - Экономические данные

**Пример:**
```python
def normalize_order(raw_json: dict) -> dict:
    return {
        "header": {
            "number": raw_json.get("Номер"),
            "date": raw_json.get("Дата"),
            "customer": raw_json.get("Контрагент"),
            "label_type": raw_json.get("Лико_ТипЗаказа"),
            "label_width": raw_json.get("ШиринаЭтикетки"),
            "label_height": raw_json.get("ДлинаЭтикетки"),
        },
        "colors": extract_colors(raw_json.get("Цвета", [])),
        "works": extract_works(raw_json.get("ВидыРабот", [])),
        "items": extract_items(raw_json.get("Товары", [])),
        "production": extract_production(raw_json.get("Производство", [])),
        "economics": extract_economics(raw_json),
    }
```

---

### 4. **document-processor/pdf_processor.py** — Парсинг PDF
**Что делает:**
- Декодирует PDF из base64
- Извлекает текст из PDF
- Анализирует макет с помощью OpenAI Vision API
- Возвращает структурированный анализ

**Пример:**
```python
async def analyze_pdf_with_vision(pdf_base64: str) -> dict:
    # 1. Декодировать base64
    pdf_bytes = base64.b64decode(pdf_base64)
    
    # 2. Извлечь текст
    text = extract_pdf_text(pdf_bytes)
    
    # 3. Конвертировать в изображение
    images = pdf_to_images(pdf_bytes)
    
    # 4. Отправить в OpenAI Vision
    vision_analysis = await openai_client.analyze_images(
        images,
        prompt="Проанализируй макет этикетки..."
    )
    
    return {
        "text": text,
        "vision_analysis": vision_analysis,
        "page_count": len(images),
    }
```

---

### 5. **llm/llm_client.py** — Интеграция с Claude API
**Что делает:**
- Создаёт запрос к Claude API
- Отправляет нормализованные данные + анализ PDF
- Получает анализ от LLM
- Парсит ответ в структурированный формат

**Пример:**
```python
class ClaudeClient:
    async def analyze_order(
        self, 
        normalized_order: dict, 
        pdf_analysis: dict,
        analysis_type: str  # "technologist" или "economist"
    ) -> dict:
        # 1. Выбрать промпт в зависимости от типа анализа
        if analysis_type == "technologist":
            prompt = TECHNOLOGIST_PROMPT
        else:
            prompt = ECONOMIST_PROMPT
        
        # 2. Подготовить сообщение
        message = f"""
        {prompt}
        
        Данные заказа:
        {json.dumps(normalized_order, ensure_ascii=False, indent=2)}
        
        Анализ макета:
        {json.dumps(pdf_analysis, ensure_ascii=False, indent=2)}
        """
        
        # 3. Отправить в Claude
        response = await self.client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=2000,
            messages=[{"role": "user", "content": message}]
        )
        
        # 4. Парсить ответ
        return parse_llm_response(response.content[0].text)
```

---

### 6. **llm/prompts.py** — Production-промпты
**Что делает:**
- Содержит промпты для технолога и экономиста
- Определяет структуру ответа LLM
- Задаёт контекст и инструкции

**Пример:**
```python
TECHNOLOGIST_PROMPT = """
Ты опытный технолог типографии. Проанализируй заказ этикетки и дай оценку:

1. **Параметры этикетки**: размеры, раппорт, раскладка
2. **Технология печати**: рекомендуемая технология (флексо/офсет/цифра)
3. **Краски и материалы**: анализ цветов, материалов
4. **Возможные проблемы**: что может пойти не так
5. **Рекомендации**: как улучшить заказ

Ответ в JSON формате:
{
    "label_parameters": {...},
    "recommended_technology": "...",
    "colors_analysis": {...},
    "potential_issues": [...],
    "recommendations": [...]
}
"""

ECONOMIST_PROMPT = """
Ты экономист типографии. Проанализируй заказ и дай оценку:

1. **Себестоимость**: расчётная себестоимость
2. **Рентабельность**: прибыль и маржа
3. **Риски**: финансовые риски
4. **Сравнение**: альтернативные технологии
5. **Рекомендации**: стоит ли брать заказ

Ответ в JSON формате:
{
    "cost_estimate": {...},
    "profitability": {...},
    "risks": [...],
    "recommendations": [...]
}
"""
```

---

### 7. **html/html_generator.py** — Генерация HTML
**Что делает:**
- Преобразует анализ LLM в красивую HTML-форму
- Генерирует 4 блока оценки:
  1. Техническая оценка
  2. Экономическая оценка
  3. Анализ макета
  4. Чек-лист готовности
- Добавляет стили и интерактивность

**Пример:**
```python
def generate_assessment_html(analysis: dict) -> str:
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .block {{ border: 1px solid #ddd; padding: 15px; margin: 10px 0; }}
            .technical {{ background: #e3f2fd; }}
            .economic {{ background: #f3e5f5; }}
            .checklist {{ background: #e8f5e9; }}
            h2 {{ color: #333; }}
            .warning {{ color: #d32f2f; }}
            .success {{ color: #388e3c; }}
        </style>
    </head>
    <body>
        <h1>AI-Ассистент анализа заказа</h1>
        
        {generate_technical_block(analysis.get("technical", {}))}
        {generate_economic_block(analysis.get("economic", {}))}
        {generate_pdf_analysis_block(analysis.get("pdf_analysis", {}))}
        {generate_checklist_block(analysis.get("checklist", {}))}
        
    </body>
    </html>
    """
    return html
```

---

## 🚀 Жизненный цикл запроса

1. **Пользователь в 1С** нажимает кнопку "AI-Ассистент технолога"
2. **1С модуль** собирает JSON заказа и PDF макета
3. **1С отправляет** HTTP POST на `http://localhost:8000/api/v1/analyze`
4. **FastAPI получает** запрос в `routes.py`
5. **order_normalizer** преобразует JSON в стандартный формат
6. **pdf_processor** парсит PDF и анализирует макет
7. **llm_client** отправляет данные в Claude API
8. **Claude** анализирует и возвращает структурированный ответ
9. **html_generator** преобразует ответ в HTML
10. **FastAPI возвращает** HTML в 1С
11. **1С отображает** HTML в модальном окне

---

## 🔐 Безопасность

- **API ключи** хранятся в `.env` файле (не в коде)
- **CORS** настроен только для 1С
- **Валидация** всех входных данных через Pydantic
- **Логирование** всех запросов и ошибок
- **Timeout** для защиты от зависаний

---

## 📊 Зависимости

| Библиотека | Версия | Назначение |
|-----------|--------|-----------|
| `fastapi` | 0.104.1 | Web-фреймворк |
| `uvicorn` | 0.24.0 | ASGI-сервер |
| `pydantic` | 2.5.0 | Валидация данных |
| `anthropic` | 0.7.1 | Claude API |
| `openai` | 1.3.5 | OpenAI Vision API |
| `PyPDF2` | 3.0.1 | Парсинг PDF |
| `pdf2image` | 1.16.3 | Конвертация PDF в изображения |
| `pillow` | 10.1.0 | Обработка изображений |
| `pytest` | 7.4.3 | Unit-тестирование |

---

## 🧪 Тестирование

Каждый модуль имеет unit-тесты:
- `test_order_normalizer.py` — тесты нормализации JSON
- `test_pdf_processor.py` — тесты парсинга PDF
- `test_llm_client.py` — тесты интеграции с Claude
- `test_html_generator.py` — тесты генерации HTML

Запуск тестов:
```bash
pytest tests/ -v
```

---

## 📝 Логирование

Все операции логируются:
- Входящие запросы
- Ошибки обработки
- Вызовы API
- Время выполнения

Логи сохраняются в `logs/app.log`

---

## 🐳 Docker

Проект контейнеризирован для простого развёртывания:

```bash
# Локальный запуск
docker-compose up

# Production запуск
docker-compose -f docker-compose.yml up -d
```

API будет доступен на `http://localhost:8000`
