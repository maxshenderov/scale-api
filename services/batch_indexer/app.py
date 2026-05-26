"""
ФАЗА 1: Batch Indexer для 1С
Фоновый сервис для переиндексации 100K+ заказов из 1С в Qdrant

Поток:
1. Получает список заказов из 1С
2. Для каждого заказа скачивает макет
3. Парсит макет через Docling (Phase 1) → извлекает текст
4. Создает BM25 sparse vectors из текста
5. Создает dense vectors через sentence-transformers
6. Загружает в Qdrant collection orders_phase1
"""

import logging
import asyncio
import json
import base64
import time
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from pathlib import Path

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    PointStruct,
    VectorParams,
    Distance,
    NamedVector,
    NamedSparseVector,
    SparseVector,
    VectorParamsMultiset,
)
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer
from tqdm import tqdm
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings

# ─────────────────────────────────────────────────────────────────────────────
# ЛОГИРОВАНИЕ
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format=settings.LOG_FORMAT
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ КЛАССЫ
# ─────────────────────────────────────────────────────────────────────────────

class OIL1CClient:
    """Клиент для работы с 1С API (получение заказов)"""

    def __init__(self, base_url: str, username: str = "", password: str = ""):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=30,
            auth=(username, password) if username else None
        )

    @retry(stop=stop_after_attempt(settings.MAX_RETRIES), wait=wait_exponential())
    async def get_orders(self, limit: int = 1000, offset: int = 0) -> List[Dict]:
        """Получает список заказов из 1С"""
        logger.info(f"📥 Получение заказов из 1С (limit={limit}, offset={offset})...")
        
        try:
            response = self.client.get(
                settings.OIL_API_ORDERS_ENDPOINT,
                params={"limit": limit, "offset": offset}
            )
            response.raise_for_status()
            
            data = response.json()
            orders = data.get("orders", [])
            logger.info(f"✅ Получено {len(orders)} заказов")
            
            return orders
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения заказов: {e}")
            raise

    @retry(stop=stop_after_attempt(settings.MAX_RETRIES), wait=wait_exponential())
    async def get_order_details(self, order_id: str) -> Dict:
        """Получает детали заказа (включая данные макета)"""
        try:
            response = self.client.get(
                settings.OIL_API_ORDER_DETAILS_ENDPOINT.format(order_id=order_id)
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"❌ Ошибка получения деталей заказа {order_id}: {e}")
            raise

    def close(self):
        """Закрывает HTTP соединение"""
        self.client.close()


class DoclingClient:
    """Клиент для работы с Docling сервисом (Phase 1 парсинг)"""

    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip("/")
        self.client = httpx.Client(
            base_url=self.api_url,
            timeout=settings.DOCLING_API_TIMEOUT
        )

    @retry(stop=stop_after_attempt(settings.MAX_RETRIES), wait=wait_exponential())
    async def parse_file_base64(self, file_base64: str, file_type: str, filename: str = "") -> Dict:
        """Парсит файл в виде base64 через Docling"""
        logger.debug(f"📄 Парсинг файла {filename} ({file_type}) через Docling...")
        
        try:
            response = self.client.post(
                "/api/parse-base64",
                json={
                    "file_base64": file_base64,
                    "file_type": file_type,
                    "filename": filename
                }
            )
            response.raise_for_status()
            
            data = response.json()
            if data.get("success"):
                logger.debug(f"✅ Docling: извлечено {len(data.get('text', ''))} символов")
            else:
                logger.warning(f"⚠️ Docling ошибка: {data.get('error')}")
            
            return data
            
        except Exception as e:
            logger.error(f"❌ Ошибка Docling парсинга: {e}")
            raise

    def close(self):
        """Закрывает HTTP соединение"""
        self.client.close()


class BM25Vectorizer:
    """BM25 векторизатор для sparse vectors"""

    def __init__(self, vocab_size: int = 10000):
        self.vocab_size = vocab_size
        self.corpus = []
        self.vectorizer = None

    def fit(self, texts: List[str]):
        """Обучает BM25 на корпусе текстов"""
        logger.info(f"🔤 Обучение BM25 на {len(texts)} текстах...")
        
        # Простая tokenization
        self.corpus = [text.lower().split() for text in texts]
        self.vectorizer = BM25Okapi(self.corpus)
        
        logger.info("✅ BM25 обучен")

    def transform(self, text: str) -> Dict[int, float]:
        """Преобразует текст в BM25 sparse vector"""
        if not self.vectorizer:
            logger.warning("⚠️ BM25 не обучен, используется пустой вектор")
            return {}
        
        tokens = text.lower().split()
        scores = self.vectorizer.get_scores(tokens)
        
        # Преобразуем в sparse формат (индекс -> оценка)
        sparse_dict = {}
        for idx, score in enumerate(scores):
            if score > 0 and idx < self.vocab_size:
                sparse_dict[idx] = float(score)
        
        return sparse_dict


class EmbeddingsModel:
    """Модель для создания dense embeddings (semantic vectors)"""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        logger.info(f"🤖 Загрузка модели embeddings: {model_name}...")
        self.model = SentenceTransformer(model_name)
        logger.info("✅ Модель embeddings загружена")

    def embed(self, text: str) -> List[float]:
        """Создает embedding для текста"""
        if not text:
            return [0.0] * self.model.get_sentence_embedding_dimension()
        
        embedding = self.model.encode(text, convert_to_tensor=False)
        return embedding.tolist()


class QdrantIndexer:
    """Класс для работы с Qdrant (indexing и search)"""

    def __init__(self, host: str, port: int, api_key: str = ""):
        logger.info(f"🔌 Подключение к Qdrant ({host}:{port})...")
        
        self.client = QdrantClient(
            host=host,
            port=port,
            api_key=api_key if api_key else None,
            timeout=30
        )
        
        logger.info("✅ Подключение к Qdrant успешно")

    def create_collection_if_not_exists(self, collection_name: str):
        """Создает collection с named vectors (dense + sparse BM25)"""
        
        try:
            # Проверяем существование collection
            self.client.get_collection(collection_name)
            logger.info(f"✅ Collection '{collection_name}' уже существует")
            return
        except:
            pass

        logger.info(f"🆕 Создание collection '{collection_name}'...")
        
        # Создаем collection с двумя типами векторов:
        # 1. phase1_text_dense - dense semantic embeddings (1536 dim)
        # 2. phase1_text_bm25 - sparse BM25 (keyword search)
        
        self.client.create_collection(
            collection_name=collection_name,
            vectors_config={
                "phase1_text_dense": VectorParams(
                    size=settings.QDRANT_DENSE_VECTOR_SIZE,
                    distance=Distance.COSINE
                )
            },
            sparse_vectors_config={
                "phase1_text_bm25": VectorParams(
                    size=settings.QDRANT_SPARSE_VECTOR_SIZE,
                    distance=Distance.DOT
                )
            }
        )
        
        logger.info(f"✅ Collection '{collection_name}' создана")

    def upsert_points(self, collection_name: str, points: List[PointStruct]):
        """Загружает points в collection (create or update)"""
        logger.info(f"⬆️ Загрузка {len(points)} points в Qdrant...")
        
        try:
            self.client.upsert(
                collection_name=collection_name,
                points=points
            )
            logger.info(f"✅ {len(points)} points успешно загружено")
        except Exception as e:
            logger.error(f"❌ Ошибка upsert: {e}")
            raise

    def close(self):
        """Закрывает соединение с Qdrant"""
        self.client.close()


# ─────────────────────────────────────────────────────────────────────────────
# ОСНОВНАЯ ЛОГИКА BATCH INDEXING
# ─────────────────────────────────────────────────────────────────────────────

class BatchIndexer:
    """Главный класс для batch индексирования заказов"""

    def __init__(self):
        self.oil_client = OIL1CClient(
            settings.OIL_API_URL,
            settings.OIL_API_USERNAME,
            settings.OIL_API_PASSWORD
        )
        self.docling_client = DoclingClient(settings.DOCLING_API_URL)
        self.embeddings_model = EmbeddingsModel(settings.EMBEDDING_MODEL)
        self.qdrant_client = QdrantIndexer(
            settings.QDRANT_HOST,
            settings.QDRANT_PORT,
            settings.QDRANT_API_KEY
        )
        
        # Инициализируем BM25 (потом обучим на корпусе текстов)
        self.bm25_vectorizer = BM25Vectorizer(settings.QDRANT_SPARSE_VECTOR_SIZE)
        
        # Создаем collection если не существует
        self.qdrant_client.create_collection_if_not_exists(
            settings.QDRANT_COLLECTION_NAME
        )

    async def index_orders(self, max_orders: Optional[int] = None) -> Dict:
        """Главный метод: индексирует заказы из 1С"""
        
        max_orders = max_orders or settings.MAX_ORDERS_TO_INDEX
        logger.info(f"🚀 Начало индексирования заказов (max={max_orders})...")
        
        start_time = time.time()
        total_indexed = 0
        total_errors = 0
        
        try:
            # Получаем заказы батчами
            offset = 0
            all_orders = []
            
            while len(all_orders) < max_orders:
                batch_size = min(settings.BATCH_SIZE, max_orders - len(all_orders))
                
                try:
                    orders = await self.oil_client.get_orders(
                        limit=batch_size,
                        offset=offset
                    )
                    
                    if not orders:
                        break
                    
                    all_orders.extend(orders)
                    offset += len(orders)
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка получения батча заказов: {e}")
                    total_errors += 1
                    continue
            
            logger.info(f"📊 Получено всего {len(all_orders)} заказов для индексирования")
            
            # Обучаем BM25 на текстах (если есть кеш)
            # Пока пропускаем - обучение будет done incrementally
            
            # Индексируем заказы
            points_to_upsert = []
            
            for i, order in enumerate(tqdm(all_orders, desc="Индексирование заказов")):
                try:
                    point = await self._index_single_order(order)
                    if point:
                        points_to_upsert.append(point)
                        total_indexed += 1
                    
                    # Загружаем батч в Qdrant
                    if len(points_to_upsert) >= settings.BATCH_SIZE:
                        self.qdrant_client.upsert_points(
                            settings.QDRANT_COLLECTION_NAME,
                            points_to_upsert
                        )
                        points_to_upsert = []
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка индексирования заказа {order.get('id')}: {e}")
                    total_errors += 1
                    continue
            
            # Загружаем оставшиеся points
            if points_to_upsert:
                self.qdrant_client.upsert_points(
                    settings.QDRANT_COLLECTION_NAME,
                    points_to_upsert
                )
            
            elapsed_time = time.time() - start_time
            
            result = {
                "success": True,
                "total_indexed": total_indexed,
                "total_errors": total_errors,
                "elapsed_seconds": elapsed_time,
                "rate_per_second": total_indexed / elapsed_time if elapsed_time > 0 else 0,
                "timestamp": datetime.now().isoformat()
            }
            
            logger.info(f"✅ Индексирование завершено: {total_indexed} успешно, {total_errors} ошибок за {elapsed_time:.1f}s")
            return result
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка индексирования: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "total_indexed": total_indexed,
                "total_errors": total_errors
            }

    async def _index_single_order(self, order: Dict) -> Optional[PointStruct]:
        """Индексирует один заказ"""
        
        order_id = order.get("id") or order.get("uuid")
        if not order_id:
            logger.warning("⚠️ Заказ без ID, пропускаем")
            return None
        
        try:
            # Получаем детали заказа
            order_details = await self.oil_client.get_order_details(order_id)
            
            # Извлекаем текст макета (Phase 1)
            combined_text = await self._extract_text_phase1(order_details)
            
            if not combined_text:
                logger.debug(f"⚠️ Заказ {order_id}: нет текста для индексирования")
                combined_text = f"Заказ {order_id}"
            
            # Создаем embeddings (dense vector)
            dense_vector = self.embeddings_model.embed(combined_text)
            
            # Создаем BM25 sparse vector
            bm25_vector = self.bm25_vectorizer.transform(combined_text)
            
            # Подготавливаем payload (1С данные)
            payload = self._prepare_payload(order, order_details)
            
            # Создаем point для Qdrant
            point = PointStruct(
                id=hash(order_id) % (2**31),  # Используем hash как numeric ID
                vector={
                    "phase1_text_dense": dense_vector,
                    "phase1_text_bm25": SparseVector(
                        indices=list(bm25_vector.keys()),
                        values=list(bm25_vector.values())
                    )
                },
                payload=payload
            )
            
            return point
            
        except Exception as e:
            logger.error(f"❌ Ошибка индексирования заказа {order_id}: {e}")
            return None

    async def _extract_text_phase1(self, order_details: Dict) -> str:
        """Извлекает текст макета через Phase 1 (Docling)"""
        
        # Пытаемся получить файл макета
        macket_file = order_details.get("macket_file") or order_details.get("layout_file")
        
        if not macket_file:
            logger.debug("⚠️ Макет не найден, используем текст из 1С")
            return order_details.get("description", "")
        
        try:
            # Проверяем формат файла
            file_type = macket_file.get("type") or "pdf"
            file_base64 = macket_file.get("base64")
            filename = macket_file.get("filename", "macket")
            
            if not file_base64:
                logger.debug(f"⚠️ Файл макета нет base64 данных")
                return order_details.get("description", "")
            
            # Парсим через Docling
            result = await self.docling_client.parse_file_base64(
                file_base64,
                file_type,
                filename
            )
            
            if result.get("success"):
                return result.get("text", "")
            else:
                logger.warning(f"⚠️ Docling ошибка: {result.get('error')}")
                return order_details.get("description", "")
                
        except Exception as e:
            logger.error(f"❌ Ошибка извлечения текста Phase 1: {e}")
            return order_details.get("description", "")

    def _prepare_payload(self, order: Dict, order_details: Dict) -> Dict:
        """Подготавливает payload с 1С данными"""
        
        return {
            "order_id": order.get("id") or order.get("uuid"),
            "order_number": order.get("number") or order.get("external_number"),
            "created_at": order.get("created_at"),
            "work_center": order.get("work_center") or order_details.get("work_center"),
            "print_technology": order_details.get("print_technology"),
            "material_type": order_details.get("material_type"),
            "material_grammar": order_details.get("material_grammar"),
            "label_width": order_details.get("label_width"),
            "label_height": order_details.get("label_height"),
            "label_type": order_details.get("label_type"),
            "colors": order_details.get("colors", []),
            "additional_works": order_details.get("additional_works", []),
            "order_type": order.get("type") or order_details.get("order_type"),
            "circulation_thousands": order_details.get("circulation_thousands", 0),
        }

    def cleanup(self):
        """Закрывает все соединения"""
        logger.info("🧹 Закрытие соединений...")
        try:
            self.oil_client.close()
            self.docling_client.close()
            self.qdrant_client.close()
            logger.info("✅ Соединения закрыты")
        except Exception as e:
            logger.error(f"❌ Ошибка закрытия: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# ГЛАВНАЯ ТОЧКА ВХОДА
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    """Главная функция"""
    
    logger.info("="*80)
    logger.info("🚀 ФАЗА 1: Batch Indexer для 1С")
    logger.info(f"Режим: {settings.INDEXER_MODE}")
    logger.info(f"Docling API: {settings.DOCLING_API_URL}")
    logger.info(f"Qdrant: {settings.QDRANT_HOST}:{settings.QDRANT_PORT}")
    logger.info("="*80)
    
    indexer = BatchIndexer()
    
    try:
        if settings.INDEXER_MODE == "once":
            # Один проход индексирования
            result = await indexer.index_orders(settings.MAX_ORDERS_TO_INDEX)
            logger.info(f"📊 Результат: {json.dumps(result, indent=2)}")
            
        elif settings.INDEXER_MODE == "daemon":
            # Фоновый worker - периодическая переиндексация
            logger.info(f"⏱️ Фоновый режим: переиндексация каждые {settings.REINDEX_INTERVAL_SECONDS} секунд")
            
            while True:
                result = await indexer.index_orders(settings.MAX_ORDERS_TO_INDEX)
                logger.info(f"📊 Результат: {json.dumps(result, indent=2)}")
                
                logger.info(f"⏳ Ожидание {settings.REINDEX_INTERVAL_SECONDS}s перед следующей переиндексацией...")
                await asyncio.sleep(settings.REINDEX_INTERVAL_SECONDS)
        else:
            logger.error(f"❌ Неизвестный режим: {settings.INDEXER_MODE}")
            
    except KeyboardInterrupt:
        logger.info("⏸️ Остановка по Ctrl+C")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
    finally:
        indexer.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
