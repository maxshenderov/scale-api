"""
ФАЗА 1: RAG LLM Pipeline сервис
Генерирует параметры заказа используя:
1. Phase 0 (Vision LLM параметры)
2. Phase 1 (Docling текст + доп файлы)
3. RAG (TOP-3 похожих заказов из Qdrant)
4. LLM (Claude/GPT генерирует JSON параметров)

Также выполняет Confidence Scoring для каждого параметра

Endpoint:
  POST /api/generate-order-parameters - генерирует параметры заказа
"""

import logging
import json
import httpx
from typing import List, Dict, Optional, Any
from datetime import datetime
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
import uvicorn

# ─────────────────────────────────────────────────────────────────────────────
# ЛОГИРОВАНИЕ
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# МОДЕЛИ ДАННЫХ
# ─────────────────────────────────────────────────────────────────────────────

class Phase0Parameters(BaseModel):
    """Параметры из Phase 0 (Vision LLM)"""
    label_width: Optional[float] = None
    label_height: Optional[float] = None
    orientation: Optional[str] = None  # portrait, landscape
    print_type: Optional[str] = None
    estimated_colors: Optional[int] = None
    confidence: float = 0.5


class Phase1Data(BaseModel):
    """Данные из Phase 1 (Docling)"""
    text: str  # Извлеченный текст из макета
    confidence: float = 0.85
    additional_files: List[Dict[str, str]] = []  # доп файлы с их текстом
    manager_notes: Optional[str] = None  # комментарии менеджера


class GenerateParametersRequest(BaseModel):
    """Запрос для генерации параметров заказа"""
    phase0: Phase0Parameters  # Визуальные параметры
    phase1: Phase1Data  # Текстовые данные + доп файлы
    customer_name: Optional[str] = None
    customer_id: Optional[str] = None


class ParameterConfidence(BaseModel):
    """Параметр с confidence score"""
    value: Any  # значение параметра
    confidence: float  # уверенность (0-1)
    source: str  # откуда взялся: "phase0", "phase1", "rag", "llm"
    reasoning: Optional[str] = None  # обоснование


class OrderParameters(BaseModel):
    """Сгенерированные параметры заказа"""
    work_center: ParameterConfidence
    print_technology: ParameterConfidence
    material_type: ParameterConfidence
    material_grammar: Optional[ParameterConfidence] = None
    label_width: Optional[ParameterConfidence] = None
    label_height: Optional[ParameterConfidence] = None
    colors: Optional[ParameterConfidence] = None
    additional_works: Optional[ParameterConfidence] = None
    label_type: Optional[ParameterConfidence] = None
    order_type: Optional[ParameterConfidence] = None


class UncertainParameter(BaseModel):
    """Параметр, требующий уточнения"""
    name: str
    reason: str
    current_confidence: float
    threshold: float
    suggestions: Optional[List[str]] = None


class GenerateParametersResponse(BaseModel):
    """Ответ с сгенерированными параметрами"""
    success: bool
    parameters: Optional[OrderParameters] = None
    uncertain_parameters: List[UncertainParameter] = []  # параметры, требующие уточнения
    rag_context: Optional[List[Dict[str, Any]]] = None  # TOP-3 похожих заказов
    llm_reasoning: Optional[str] = None  # обоснование от LLM
    error: Optional[str] = None


class HealthResponse(BaseModel):
    """Ответ health check"""
    status: str
    hybrid_search_available: bool
    llm_available: bool


# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI ПРИЛОЖЕНИЕ
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="RAG LLM Pipeline",
    description="Генерирует параметры заказа используя Phase0+Phase1+RAG+LLM",
    version="1.0"
)

# Конфигурация
HYBRID_SEARCH_URL = "http://localhost:8002"
LLM_API_URL = "aichat-okil-sato.kartochka.tech"
LLM_API_KEY = "sk-5ad444531ffa461a92ea0ddcd4f92a02"

# Qdrant для заказов на порту 6334
QDRANT_HOST = "localhost"
QDRANT_PORT = 6334

# Пороги confidence для критичных параметров
CONFIDENCE_THRESHOLDS = {
    "default": 0.70,
    "work_center": 0.85,
    "print_technology": 0.80,
    "material_type": 0.75,
}

# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Проверка здоровья сервиса"""
    
    # Проверяем доступность Hybrid Search
    hybrid_search_available = False
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{HYBRID_SEARCH_URL}/health", timeout=5)
            hybrid_search_available = response.status_code == 200
    except:
        pass
    
    # Проверяем доступность LLM
    llm_available = False
    try:
        async with httpx.AsyncClient() as client:
            # Пропускаем полную проверку, просто предполагаем доступность
            llm_available = True
    except:
        pass
    
    return HealthResponse(
        status="healthy",
        hybrid_search_available=hybrid_search_available,
        llm_available=llm_available
    )


@app.post("/api/generate-order-parameters", response_model=GenerateParametersResponse)
async def generate_order_parameters(request: GenerateParametersRequest) -> GenerateParametersResponse:
    """
    Генерирует параметры заказа используя RAG + LLM
    
    Поток:
    1. Получает TOP-3 похожих заказов через Hybrid Search
    2. Формирует prompt с Phase0 + Phase1 + RAG контекстом
    3. Отправляет на LLM для анализа
    4. Вычисляет confidence scores
    5. Возвращает параметры + список параметров требующих уточнения
    """
    
    logger.info("🚀 Генерирование параметров заказа (RAG LLM pipeline)...")
    
    try:
        
        # ─────────────────────────────────────────────────────────────────────
        # ШАГ 1: ПОЛУЧИТЬ RAG КОНТЕКСТ (TOP-3 похожих заказов)
        # ─────────────────────────────────────────────────────────────────────
        
        logger.info("🔍 Шаг 1: Поиск похожих заказов (Hybrid Search)...")
        
        rag_context = []
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{HYBRID_SEARCH_URL}/api/search",
                    json={
                        "text": request.phase1.text,
                        "top_k": 3,
                        "min_score": 0.0
                    },
                    timeout=30
                )
                response.raise_for_status()
                
                search_result = response.json()
                
                if search_result.get("success"):
                    rag_context = search_result.get("results", [])
                    logger.info(f"✅ Найдено {len(rag_context)} похожих заказов")
                else:
                    logger.warning(f"⚠️ Hybrid Search ошибка: {search_result.get('error')}")
                    
        except Exception as e:
            logger.error(f"❌ Ошибка Hybrid Search: {e}")
            # Продолжаем без RAG контекста
        
        # ─────────────────────────────────────────────────────────────────────
        # ШАГ 2: ПОСТРОИТЬ PROMPT
        # ─────────────────────────────────────────────────────────────────────
        
        logger.info("📝 Шаг 2: Построение prompt для LLM...")
        
        # Форматируем RAG контекст
        rag_context_text = ""
        if rag_context:
            rag_context_text = "ПОХОЖИЕ ЗАКАЗЫ ИЗ ИСТОРИИ (TOP-3):\n"
            for i, order in enumerate(rag_context, 1):
                rag_context_text += f"""
{i}. Заказ #{order.get('order_number', 'N/A')}:
   - Рабочий центр: {order.get('work_center', 'N/A')}
   - Технология: {order.get('print_technology', 'N/A')}
   - Материал: {order.get('material_type', 'N/A')} ({order.get('label_width', 'N/A')}×{order.get('label_height', 'N/A')} мм)
   - Цвета: {', '.join(order.get('colors', []))}
   - Доп работы: {', '.join(order.get('additional_works', []))}
   - Score: {order.get('score', 0):.2f}
"""
        else:
            rag_context_text = "(Похожих заказов не найдено)"
        
        # Форматируем доп файлы
        additional_files_text = ""
        if request.phase1.additional_files:
            additional_files_text = "ДОПОЛНИТЕЛЬНЫЕ ФАЙЛЫ:\n"
            for file_data in request.phase1.additional_files:
                additional_files_text += f"- {file_data.get('filename', 'unknown')}: {file_data.get('text', '')[:200]}\n"
        
        # Форматируем комментарии менеджера
        manager_notes_text = ""
        if request.phase1.manager_notes:
            manager_notes_text = f"\nКОММЕНТАРИ МЕНЕДЖЕРА: {request.phase1.manager_notes}"
        
        # Главный prompt
        prompt = f"""Ты — эксперт по определению параметров заказа этикеток для типографии.

НОВЫЙ ЗАКАЗ (из загруженного макета):
═════════════════════════════════════════

ФАЗА 0 (Vision LLM анализ изображения):
- Ширина этикетки: {request.phase0.label_width} мм
- Высота этикетки: {request.phase0.label_height} мм
- Ориентация: {request.phase0.orientation}
- Предполагаемый тип печати: {request.phase0.print_type}
- Примерное количество цветов: {request.phase0.estimated_colors}
- Уверенность Phase0: {request.phase0.confidence:.0%}

ФАЗА 1 (Docling парсинг текста из макета):
{request.phase1.text[:1000]}
... (продолжение текста)
- Уверенность Phase1 парсинга: {request.phase1.confidence:.0%}

{additional_files_text}

{rag_context_text}

{manager_notes_text}

═════════════════════════════════════════

На основе всей информации выше, определи:

1. **Рабочий центр** (печать, флексо, глубокая, цифра, комбо):
   Выбери наиболее вероятный рабочий центр с обоснованием

2. **Технология печати** (офсет, флексо, цифра, глубокая, и т.д.):
   Какая технология печати соответствует заказу?

3. **Тип материала** (плёнка, бумага, картон, и т.д.):
   Из какого материала должна быть этикетка?

4. **Грамматура материала** (в г/м²):
   Какая грамматура материала?

5. **Типы цветов** (CMYK, pantone, специальные):
   Какие цвета/краски используются?

6. **Дополнительные работы** (конвертация, вырубка, ламинирование, тиснение, и т.д.):
   Нужны ли доп работы?

7. **Тип этикетки** (ЭТ, КЭ, КДР, комплект, пакет, ФЛ, ПП):
   Какой тип этикетки?

ВАЖНО: Для каждого параметра вычисли confidence (0-1):
- 0.95-1.0: уверен почти на 100%
- 0.80-0.95: уверен (но возможны варианты)
- 0.70-0.80: приемлемая уверенность
- 0.60-0.70: нужно уточнение
- < 0.60: ТРЕБУЕТСЯ УТОЧНЕНИЕ у менеджера

ОТВЕТ В JSON:
{{
  "work_center": {{
    "value": "...",
    "confidence": 0.85,
    "source": "phase0/phase1/rag/llm",
    "reasoning": "обоснование"
  }},
  "print_technology": {{
    "value": "...",
    "confidence": 0.80,
    "source": "phase1/rag",
    "reasoning": "обоснование"
  }},
  "material_type": {{
    "value": "...",
    "confidence": 0.75,
    "source": "rag",
    "reasoning": "обоснование"
  }},
  "material_grammar": {{
    "value": 150,
    "confidence": 0.70,
    "source": "llm",
    "reasoning": "обоснование"
  }},
  "label_width": {{
    "value": {request.phase0.label_width},
    "confidence": {request.phase0.confidence},
    "source": "phase0",
    "reasoning": "из Vision анализа"
  }},
  "label_height": {{
    "value": {request.phase0.label_height},
    "confidence": {request.phase0.confidence},
    "source": "phase0",
    "reasoning": "из Vision анализа"
  }},
  "colors": {{
    "value": ["Cyan", "Magenta", "Yellow", "Black"],
    "confidence": 0.85,
    "source": "phase0/phase1",
    "reasoning": "обоснование"
  }},
  "additional_works": {{
    "value": ["конвертация", "вырубка"],
    "confidence": 0.70,
    "source": "phase1/rag",
    "reasoning": "обоснование"
  }},
  "label_type": {{
    "value": "ЭТ",
    "confidence": 0.80,
    "source": "phase1",
    "reasoning": "обоснование"
  }},
  "order_type": {{
    "value": "СамоклеящаясяЭтикетка",
    "confidence": 0.90,
    "source": "phase1",
    "reasoning": "обоснование"
  }}
}}

ОТВЕТ: (только JSON, без markdown блоков)
"""
        
        logger.info("📤 Отправка prompt на LLM...")
        
        # ─────────────────────────────────────────────────────────────────────
        # ШАГ 3: ОТПРАВИТЬ НА LLM
        # ─────────────────────────────────────────────────────────────────────
        
        llm_response_text = ""
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LLM_API_KEY}"
            }
            
            body = {
                "model": "anthropic/claude-haiku-4.5",
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты — эксперт по определению параметров заказа этикеток. Отвечай только валидным JSON без дополнительного текста."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 2000
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://{LLM_API_URL}/api/v1/chat/completions",
                    json=body,
                    headers=headers,
                    timeout=60
                )
                response.raise_for_status()
                
                result = response.json()
                llm_response_text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                logger.info("✅ LLM ответ получен")
                
        except Exception as e:
            logger.error(f"❌ Ошибка LLM запроса: {e}")
            return GenerateParametersResponse(
                success=False,
                error=f"LLM error: {str(e)}"
            )
        
        # ─────────────────────────────────────────────────────────────────────
        # ШАГ 4: ПАРСИМ JSON ОТВЕТ
        # ─────────────────────────────────────────────────────────────────────
        
        logger.info("📊 Шаг 3: Парсинг LLM ответа...")
        
        try:
            # Очищаем ответ от markdown обозначений
            json_str = llm_response_text
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0]
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0]
            
            llm_json = json.loads(json_str)
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Ошибка парсинга JSON: {e}")
            logger.error(f"LLM ответ: {llm_response_text[:500]}")
            return GenerateParametersResponse(
                success=False,
                error=f"JSON parse error: {str(e)}"
            )
        
        # ─────────────────────────────────────────────────────────────────────
        # ШАГ 5: ПОСТРОИТЬ OrderParameters И ОПРЕДЕЛИТЬ UNCERTAIN
        # ─────────────────────────────────────────────────────────────────────
        
        logger.info("🎯 Шаг 4: Вычисление confidence scores и определение неопределённых параметров...")
        
        # Функция для извлечения ParameterConfidence
        def extract_param(param_name: str) -> Optional[ParameterConfidence]:
            if param_name not in llm_json:
                return None
            
            p = llm_json[param_name]
            return ParameterConfidence(
                value=p.get("value"),
                confidence=p.get("confidence", 0.5),
                source=p.get("source", "llm"),
                reasoning=p.get("reasoning")
            )
        
        # Строим параметры заказа
        parameters = OrderParameters(
            work_center=extract_param("work_center"),
            print_technology=extract_param("print_technology"),
            material_type=extract_param("material_type"),
            material_grammar=extract_param("material_grammar"),
            label_width=extract_param("label_width"),
            label_height=extract_param("label_height"),
            colors=extract_param("colors"),
            additional_works=extract_param("additional_works"),
            label_type=extract_param("label_type"),
            order_type=extract_param("order_type"),
        )
        
        # Определяем параметры требующие уточнения
        uncertain_parameters = []
        critical_params = {
            "work_center": CONFIDENCE_THRESHOLDS["work_center"],
            "print_technology": CONFIDENCE_THRESHOLDS["print_technology"],
            "material_type": CONFIDENCE_THRESHOLDS["material_type"],
        }
        
        for param_name, threshold in critical_params.items():
            param = getattr(parameters, param_name)
            if param and param.confidence < threshold:
                uncertain_parameters.append(
                    UncertainParameter(
                        name=param_name,
                        reason=f"Confidence {param.confidence:.0%} ниже порога {threshold:.0%}",
                        current_confidence=param.confidence,
                        threshold=threshold,
                        suggestions=None  # Можно добавить предложения
                    )
                )
        
        logger.info(f"✅ Генерирование завершено ({len(uncertain_parameters)} параметров требуют уточнения)")
        
        return GenerateParametersResponse(
            success=True,
            parameters=parameters,
            uncertain_parameters=uncertain_parameters,
            rag_context=[order.model_dump() for order in rag_context],
            llm_reasoning=llm_response_text
        )
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# ГЛАВНАЯ ТОЧКА ВХОДА
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("🚀 Запуск RAG LLM Pipeline сервиса на http://localhost:8003")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8003,
        log_level="info"
    )
