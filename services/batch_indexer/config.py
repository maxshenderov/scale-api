"""
Конфигурация для Batch Indexer сервиса
"""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Параметры конфигурации"""

    # ─────────────────────────────────────────────────────────────────────────
    # 1C КОНФИГУРАЦИЯ
    # ─────────────────────────────────────────────────────────────────────────

    # HTTP API 1С для получения заказов
    OIL_API_URL: str = os.getenv("OIL_API_URL", "http://localhost:8080")
    OIL_API_USERNAME: str = os.getenv("OIL_API_USERNAME", "")
    OIL_API_PASSWORD: str = os.getenv("OIL_API_PASSWORD", "")

    # Путь к API методам 1С
    OIL_API_ORDERS_ENDPOINT: str = "/api/orders"
    OIL_API_ORDER_DETAILS_ENDPOINT: str = "/api/orders/{order_id}"

    # ─────────────────────────────────────────────────────────────────────────
    # DOCLING СЕРВИС (ФАЗА 1)
    # ─────────────────────────────────────────────────────────────────────────

    DOCLING_API_URL: str = os.getenv("DOCLING_API_URL", "http://docling:8001")
    DOCLING_API_TIMEOUT: int = 30  # секунды

    # ─────────────────────────────────────────────────────────────────────────
    # QDRANT КОНФИГУРАЦИЯ
    # ─────────────────────────────────────────────────────────────────────────

    QDRANT_HOST: str = os.getenv("QDRANT_HOST", "localhost")
    QDRANT_PORT: int = int(os.getenv("QDRANT_PORT", "6334"))  # Второй Qdrant (заказы)
    QDRANT_API_KEY: str = os.getenv("QDRANT_API_KEY", "")

    # Имя коллекции для индексирования
    QDRANT_COLLECTION_NAME: str = "orders_phase1"

    # Размеры векторов
    QDRANT_DENSE_VECTOR_SIZE: int = 1536  # для OpenAI embeddings
    QDRANT_SPARSE_VECTOR_SIZE: int = 10000  # для BM25

    # ─────────────────────────────────────────────────────────────────────────
    # EMBEDDINGS (SEMANTIC VECTORS)
    # ─────────────────────────────────────────────────────────────────────────

    # Используем sentence-transformers локально (free, no API key)
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

    # ─────────────────────────────────────────────────────────────────────────
    # BATCH INDEXER ПАРАМЕТРЫ
    # ─────────────────────────────────────────────────────────────────────────

    # Количество заказов для обработки в одном батче
    BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "50"))

    # Максимальное количество заказов для переиндексации
    # (0 = все доступные)
    MAX_ORDERS_TO_INDEX: int = int(os.getenv("MAX_ORDERS_TO_INDEX", "100000"))

    # Интервал переиндексации (секунды)
    REINDEX_INTERVAL_SECONDS: int = int(os.getenv("REINDEX_INTERVAL_SECONDS", "86400"))  # 24 часа

    # Режим работы: "once" (один раз), "daemon" (фоновый worker)
    INDEXER_MODE: str = os.getenv("INDEXER_MODE", "once")

    # ─────────────────────────────────────────────────────────────────────────
    # ЛОГИРОВАНИЕ
    # ─────────────────────────────────────────────────────────────────────────

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # ─────────────────────────────────────────────────────────────────────────
    # RETRY ПАРАМЕТРЫ
    # ─────────────────────────────────────────────────────────────────────────

    MAX_RETRIES: int = 3
    RETRY_DELAY: int = 5  # секунды

    # ─────────────────────────────────────────────────────────────────────────
    # CACHE ПАРАМЕТРЫ (для промежуточных результатов)
    # ─────────────────────────────────────────────────────────────────────────

    CACHE_DIR: str = os.getenv("CACHE_DIR", "/tmp/batch_indexer_cache")
    CACHE_ENABLED: bool = True

    class Config:
        env_file = ".env"
        case_sensitive = True


# Инстанция глобальной конфигурации
settings = Settings()
