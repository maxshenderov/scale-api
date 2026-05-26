"""
ФАЗА 1: Hybrid Search сервис
Поиск похожих заказов в Qdrant используя комбинацию:
- BM25 sparse vectors (keyword search) - вес 0.3
- Dense embeddings (semantic search) - вес 0.7

Endpoint:
  POST /api/search - поиск похожих заказов по текстовому запросу
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, PointStruct
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi
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

class SearchRequest(BaseModel):
    """Запрос поиска похожих заказов"""
    text: str  # Текст для поиска (из Phase 1)
    top_k: int = 3  # Количество результатов
    min_score: float = 0.0  # Минимальный score для результата
    sparse_weight: float = 0.3  # Вес BM25 sparse search
    dense_weight: float = 0.7  # Вес dense semantic search


class OrderSearchResult(BaseModel):
    """Результат поиска одного заказа"""
    order_id: str
    order_number: str
    score: float  # Комбинированный score
    sparse_score: float  # BM25 score
    dense_score: float  # Semantic score
    work_center: Optional[str] = None
    print_technology: Optional[str] = None
    material_type: Optional[str] = None
    colors: List[str] = []
    additional_works: List[str] = []
    created_at: Optional[str] = None
    label_width: Optional[float] = None
    label_height: Optional[float] = None


class SearchResponse(BaseModel):
    """Ответ с результатами поиска"""
    success: bool
    query_text: str
    results: List[OrderSearchResult] = []
    error: Optional[str] = None
    stats: Dict[str, Any] = {}


class HealthResponse(BaseModel):
    """Ответ health check"""
    status: str
    qdrant_connected: bool
    embedding_model_loaded: bool


# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI ПРИЛОЖЕНИЕ
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Hybrid Search for Phase 1",
    description="Поиск похожих заказов используя BM25 + Dense embeddings",
    version="1.0"
)

# Глобальные переменные
qdrant_client = None
embeddings_model = None
bm25_vectorizer = None
QDRANT_COLLECTION_NAME = "orders_phase1"

@app.on_event("startup")
async def startup_event():
    """Инициализация при старте сервиса"""
    global qdrant_client, embeddings_model, bm25_vectorizer
    
    logger.info("🚀 Инициализация Hybrid Search сервиса...")
    
    # Подключение к Qdrant
    try:
        qdrant_client = QdrantClient(
            host="localhost",
            port=6334,  # Второй Qdrant для заказов
            timeout=30
        )
        logger.info("✅ Подключение к Qdrant успешно")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к Qdrant: {e}")
        qdrant_client = None
    
    # Загрузка модели embeddings
    try:
        embeddings_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        logger.info("✅ Модель embeddings загружена")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки модели embeddings: {e}")
        embeddings_model = None
    
    # BM25 инициализируется при первом поиске
    logger.info("✅ Hybrid Search сервис инициализирован")


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Проверка здоровья сервиса"""
    return HealthResponse(
        status="healthy",
        qdrant_connected=qdrant_client is not None,
        embedding_model_loaded=embeddings_model is not None
    )


@app.post("/api/search", response_model=SearchResponse)
async def hybrid_search(request: SearchRequest) -> SearchResponse:
    """
    Гибридный поиск по текстовому запросу
    
    Комбинирует:
    1. BM25 sparse vectors (keyword search)
    2. Dense embeddings (semantic search)
    
    Результаты мержатся с весами:
    - sparse_weight: 0.3 (по умолчанию)
    - dense_weight: 0.7 (по умолчанию)
    """
    
    logger.info(f"🔍 Поиск: '{request.text[:50]}...' (top_k={request.top_k})")
    
    if not qdrant_client:
        logger.error("❌ Qdrant не подключен")
        raise HTTPException(status_code=503, detail="Qdrant service unavailable")
    
    if not embeddings_model:
        logger.error("❌ Модель embeddings не загружена")
        raise HTTPException(status_code=503, detail="Embedding model unavailable")
    
    if not request.text or len(request.text.strip()) == 0:
        raise HTTPException(status_code=400, detail="Query text cannot be empty")
    
    try:
        # ─────────────────────────────────────────────────────────────────────
        # ШАГИ ПОИСКА
        # ─────────────────────────────────────────────────────────────────────
        
        # 1. Dense vector search (semantic)
        logger.info("🔄 Выполняем Dense search (semantic)...")
        dense_vector = embeddings_model.encode(request.text)
        
        # Поиск через Qdrant
        dense_results = qdrant_client.search(
            collection_name=QDRANT_COLLECTION_NAME,
            query_vector=("phase1_text_dense", dense_vector),
            limit=request.top_k * 2,  # Берем больше для дальнейшей фильтрации
            score_threshold=0.0
        )
        
        # Конвертируем в словарь order_id -> dense_score
        dense_scores = {}
        for scored_point in dense_results:
            dense_scores[scored_point.id] = scored_point.score
        
        logger.info(f"✅ Dense search: найдено {len(dense_scores)} результатов")
        
        # 2. BM25 sparse vector search (keyword)
        logger.info("🔄 Выполняем BM25 search (keyword)...")
        
        # Tokenization
        query_tokens = request.text.lower().split()
        
        # Поиск через Qdrant
        # Примечание: Qdrant поддерживает поиск по sparse vectors
        # но нам нужно передать query_vector_name и данные
        try:
            bm25_results = qdrant_client.search(
                collection_name=QDRANT_COLLECTION_NAME,
                query_vector=("phase1_text_bm25", query_tokens),
                limit=request.top_k * 2,
                score_threshold=0.0
            )
            
            # Конвертируем в словарь order_id -> bm25_score
            bm25_scores = {}
            for scored_point in bm25_results:
                bm25_scores[scored_point.id] = scored_point.score
            
            logger.info(f"✅ BM25 search: найдено {len(bm25_scores)} результатов")
            
        except Exception as e:
            logger.warning(f"⚠️ BM25 поиск не поддерживается или произошла ошибка: {e}")
            logger.info("ℹ️ Используем только Dense search")
            bm25_scores = {}
        
        # ─────────────────────────────────────────────────────────────────────
        # МЕРЖИМ РЕЗУЛЬТАТЫ
        # ─────────────────────────────────────────────────────────────────────
        
        # Собираем все уникальные point IDs
        all_point_ids = set(dense_scores.keys()) | set(bm25_scores.keys())
        
        # Нормализуем скоры (0-1 диапазон) и комбинируем
        combined_scores = {}
        
        # Получаем максимальные скоры для нормализации
        max_dense = max(dense_scores.values()) if dense_scores else 1.0
        max_bm25 = max(bm25_scores.values()) if bm25_scores else 1.0
        
        for point_id in all_point_ids:
            # Нормализованные скоры
            dense_norm = (dense_scores.get(point_id, 0) / max_dense) if max_dense > 0 else 0
            bm25_norm = (bm25_scores.get(point_id, 0) / max_bm25) if max_bm25 > 0 else 0
            
            # Комбинированный скор
            combined_score = (
                request.dense_weight * dense_norm +
                request.sparse_weight * bm25_norm
            )
            
            combined_scores[point_id] = {
                "combined": combined_score,
                "dense": dense_norm,
                "bm25": bm25_norm
            }
        
        # Сортируем по комбинированному скору
        sorted_results = sorted(
            combined_scores.items(),
            key=lambda x: x[1]["combined"],
            reverse=True
        )
        
        logger.info(f"📊 Merged: {len(combined_scores)} результатов")
        
        # ─────────────────────────────────────────────────────────────────────
        # ПОЛУЧАЕМ PAYLOAD И ФОРМИРУЕМ РЕЗУЛЬТАТ
        # ─────────────────────────────────────────────────────────────────────
        
        results = []
        for i, (point_id, scores) in enumerate(sorted_results[:request.top_k]):
            if scores["combined"] < request.min_score:
                break
            
            try:
                # Получаем точку из Qdrant для payload
                point = qdrant_client.retrieve(
                    collection_name=QDRANT_COLLECTION_NAME,
                    ids=[point_id]
                )
                
                if not point:
                    logger.warning(f"⚠️ Point {point_id} не найден")
                    continue
                
                point = point[0]
                payload = point.payload or {}
                
                result = OrderSearchResult(
                    order_id=payload.get("order_id", f"point_{point_id}"),
                    order_number=payload.get("order_number", ""),
                    score=scores["combined"],
                    sparse_score=scores["bm25"],
                    dense_score=scores["dense"],
                    work_center=payload.get("work_center"),
                    print_technology=payload.get("print_technology"),
                    material_type=payload.get("material_type"),
                    colors=payload.get("colors", []),
                    additional_works=payload.get("additional_works", []),
                    created_at=payload.get("created_at"),
                    label_width=payload.get("label_width"),
                    label_height=payload.get("label_height"),
                )
                
                results.append(result)
                
            except Exception as e:
                logger.error(f"❌ Ошибка получения payload для point {point_id}: {e}")
                continue
        
        logger.info(f"✅ Поиск завершен: {len(results)} результатов")
        
        return SearchResponse(
            success=True,
            query_text=request.text,
            results=results,
            stats={
                "total_candidates": len(combined_scores),
                "dense_results": len(dense_scores),
                "bm25_results": len(bm25_scores),
                "final_results": len(results)
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Критическая ошибка поиска: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Search error: {str(e)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ГЛАВНАЯ ТОЧКА ВХОДА
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("🚀 Запуск Hybrid Search сервиса на http://localhost:8002")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8002,
        log_level="info"
    )
