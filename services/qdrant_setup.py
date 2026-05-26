"""
Скрипт для инициализации Qdrant collection orders_phase1 с named vectors (dense + sparse BM25)

Использование:
  python qdrant_setup.py --host localhost --port 6333 --api-key "" --recreate

Параметры:
  --host: хост Qdrant (default: localhost)
  --port: порт Qdrant (default: 6333)
  --api-key: API ключ (если требуется)
  --recreate: пересоздать collection если существует
"""

import argparse
import logging
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams,
    Distance,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def setup_qdrant_collection(
    host: str = "localhost",
    port: int = 6333,
    api_key: Optional[str] = None,
    recreate: bool = False,
    collection_name: str = "orders_phase1"
):
    """
    Инициализирует Qdrant collection с named vectors
    
    Параметры:
    - host: хост Qdrant
    - port: порт Qdrant
    - api_key: API ключ для Qdrant
    - recreate: пересоздать collection если существует
    - collection_name: имя collection
    """
    
    logger.info("="*80)
    logger.info("🚀 Qdrant Collection Setup")
    logger.info(f"Host: {host}:{port}")
    logger.info(f"Collection: {collection_name}")
    logger.info(f"Recreate: {recreate}")
    logger.info("="*80)
    
    # Подключаемся к Qdrant
    logger.info(f"🔌 Подключение к Qdrant ({host}:{port})...")
    try:
        client = QdrantClient(
            host=host,
            port=port,
            api_key=api_key if api_key else None,
            timeout=30
        )
        logger.info("✅ Подключение успешно")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения: {e}")
        return False
    
    # Проверяем существование collection
    collection_exists = False
    try:
        collection_info = client.get_collection(collection_name)
        collection_exists = True
        logger.info(f"✅ Collection '{collection_name}' уже существует")
        logger.info(f"   Vectors: {len(collection_info.vectors_count) if hasattr(collection_info, 'vectors_count') else 'N/A'}")
    except Exception:
        logger.info(f"ℹ️ Collection '{collection_name}' не найдена")
    
    # Пересоздаем если требуется
    if collection_exists and recreate:
        logger.info(f"🗑️ Удаление существующей collection '{collection_name}'...")
        try:
            client.delete_collection(collection_name)
            logger.info("✅ Collection удалена")
            collection_exists = False
        except Exception as e:
            logger.error(f"❌ Ошибка удаления: {e}")
            return False
    
    # Создаем новую collection если не существует
    if not collection_exists:
        logger.info(f"🆕 Создание collection '{collection_name}'...")
        
        try:
            # Collection с двумя типами векторов:
            # 1. phase1_text_dense - dense semantic embeddings (1536 dim для OpenAI/sentence-transformers)
            # 2. phase1_text_bm25 - sparse BM25 (keyword search)
            
            client.create_collection(
                collection_name=collection_name,
                vectors_config={
                    "phase1_text_dense": VectorParams(
                        size=1536,  # all-MiniLM-L6-v2: 384, OpenAI text-embedding-3-small: 1536
                        distance=Distance.COSINE,
                        on_disk=True
                    )
                },
                sparse_vectors_config={
                    "phase1_text_bm25": VectorParams(
                        size=10000,  # размер vocab для BM25
                        distance=Distance.DOT,
                        on_disk=True
                    )
                },
                # Оптимизации
                shard_number=1,  # для single-node setup
                replication_factor=1,
                write_consistency_factor=1,
            )
            
            logger.info(f"✅ Collection '{collection_name}' создана успешно")
            logger.info("   Named vectors:")
            logger.info("     - phase1_text_dense (COSINE, 1536 dim) - semantic embeddings")
            logger.info("     - phase1_text_bm25 (DOT, 10000 dim) - BM25 keyword search")
            logger.info("   Payload fields для каждого point:")
            logger.info("     - order_id: UUID заказа")
            logger.info("     - order_number: номер заказа в 1С")
            logger.info("     - work_center: рабочий центр (печать, флексо, и т.д.)")
            logger.info("     - print_technology: технология печати")
            logger.info("     - material_type: тип материала")
            logger.info("     - label_width, label_height: размеры этикетки")
            logger.info("     - colors: массив цветов")
            logger.info("     - additional_works: массив доп работ")
            logger.info("     - created_at: дата создания")
            
        except Exception as e:
            logger.error(f"❌ Ошибка создания collection: {e}")
            return False
    
    # Проверяем статус collection
    try:
        logger.info(f"📊 Информация о collection '{collection_name}':")
        
        collection_info = client.get_collection(collection_name)
        
        logger.info(f"   Точек (points): {collection_info.points_count}")
        logger.info(f"   Статус: {collection_info.status}")
        
        if hasattr(collection_info, 'config'):
            config = collection_info.config
            if hasattr(config, 'vectors') and config.vectors:
                logger.info("   Dense vectors:")
                for name, params in config.vectors.items():
                    logger.info(f"     - {name}: size={params.size}, distance={params.distance}")
            
            if hasattr(config, 'sparse_vectors') and config.sparse_vectors:
                logger.info("   Sparse vectors:")
                for name, params in config.sparse_vectors.items():
                    logger.info(f"     - {name}: size={params.size}, distance={params.distance}")
        
    except Exception as e:
        logger.warning(f"⚠️ Не удалось получить информацию о collection: {e}")
    
    logger.info("="*80)
    logger.info("✅ Setup завершен успешно")
    logger.info("="*80)
    
    return True


def main():
    """Главная функция"""
    
    parser = argparse.ArgumentParser(
        description="Инициализация Qdrant collection для Phase 1 indexing"
    )
    
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="Хост Qdrant (default: localhost)"
    )
    
    parser.add_argument(
        "--port",
        type=int,
        default=6333,
        help="Порт Qdrant (default: 6333)"
    )
    
    parser.add_argument(
        "--api-key",
        type=str,
        default="",
        help="API ключ для Qdrant"
    )
    
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Пересоздать collection если существует"
    )
    
    parser.add_argument(
        "--collection-name",
        type=str,
        default="orders_phase1",
        help="Имя collection (default: orders_phase1)"
    )
    
    args = parser.parse_args()
    
    success = setup_qdrant_collection(
        host=args.host,
        port=args.port,
        api_key=args.api_key if args.api_key else None,
        recreate=args.recreate,
        collection_name=args.collection_name
    )
    
    return 0 if success else 1


if __name__ == "__main__":
    exit(main())
