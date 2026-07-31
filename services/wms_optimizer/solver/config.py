"""Общие константы и настройки CP-SAT солверов (Фаза A/C)."""
from __future__ import annotations

import os

# Порог допустимых пар (pallet, section) — выше него глобальный оптимизатор
# переключается на агрегированную модель (solver/cp_sat_aggregated.py).
# Переключение возможно только когда нет решений о реслоте (см. global_optimizer.py).
FEASIBLE_PAIRS_THRESHOLD = 300_000


def num_search_workers() -> int:
    """Число параллельных потоков поиска для CP-SAT.

    Раньше было захардкожено 4 — не зависело от реальных ядер хоста (Фаза A).
    -2 — запас для остальных контейнеров (postgres/backend/nginx) на том же хосте.
    """
    cpu = os.cpu_count() or 4
    return max(1, min(28, cpu - 2))
