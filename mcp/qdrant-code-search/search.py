"""Поиск по кодовой базе в Qdrant (port 6333) через эмбеддинги RouterAI/Perplexity."""
import sys
import os
import ssl
from pathlib import Path

# Исправить кодировку stdout на Windows (для кириллицы в BSL/XML)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import httpx
from dotenv import load_dotenv

# Конфигурация — из .env в корне проекта
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

EMBEDDING_URL = os.getenv("EMBEDDING_API_URL", "https://routerai.ru/api/v1")
EMBEDDING_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "perplexity/pplx-embed-v1-0.6b")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "ws-5e70e849fd3d1c12")


def _legacy_ssl_context() -> ssl.SSLContext:
    """SSL-контекст с поддержкой legacy renegotiation (корпоративный сертификат)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT  # разрешить слабые ключи сертификата
    return ctx


def get_embedding(client: httpx.Client, text: str) -> list[float]:
    """Получить эмбеддинг для текста через OpenAI-совместимый API."""
    response = client.post(
        url=f"{EMBEDDING_URL}/embeddings",
        headers={
            "Authorization": f"Bearer {EMBEDDING_KEY}",
            "Content-Type": "application/json",
        },
        json={"model": EMBEDDING_MODEL, "input": text},
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def search_qdrant(client: httpx.Client, vector: list[float], limit: int) -> list[dict]:
    """Поиск ближайших точек в Qdrant."""
    response = client.post(
        url=f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/search",
        json={"vector": vector, "limit": limit, "with_payload": True, "with_vector": False},
    )
    response.raise_for_status()
    return response.json()["result"]


def format_results(results: list[dict]) -> str:
    """Форматировать результаты для вывода."""
    lines = [f"Найдено результатов: {len(results)}\n"]
    for i, r in enumerate(results):
        p = r.get("payload", {})
        score = r.get("score", 0)
        path = p.get("filePath") or "/".join(
            v for k, v in sorted((p.get("pathSegments") or {}).items())
        ) or "(путь не указан)"
        chunk = (p.get("codeChunk") or "(нет фрагмента)")[:500]
        start_line = p.get("startLine")
        end_line = p.get("endLine")
        line_info = f" (строки {start_line}-{end_line})" if start_line else ""

        lines.append(f"#{i + 1} [score: {score:.4f}] {path}{line_info}")
        lines.append(f"```")
        lines.append(chunk)
        lines.append(f"```")
        lines.append("")
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("Использование: python search.py <поисковый запрос> [лимит=5]")
        sys.exit(1)

    query = sys.argv[1]
    limit = min(int(sys.argv[2]) if len(sys.argv) > 2 else 5, 20)

    embed_client = httpx.Client(timeout=30, verify=_legacy_ssl_context())
    qdrant_client = httpx.Client(timeout=30)  # HTTP, SSL не нужен

    try:
        print(f"[Embedding] Получение эмбеддинга для: «{query[:80]}»...", file=sys.stderr)
        vector = get_embedding(embed_client, query)

        print(f"[Qdrant] Поиск (limit={limit})...", file=sys.stderr)
        results = search_qdrant(qdrant_client, vector, limit)

        print(format_results(results))
    finally:
        embed_client.close()
        qdrant_client.close()


if __name__ == "__main__":
    main()
