# Фаза C: Тюнинг параметров — Краткая справка

## Применённые изменения (2026-07-24)

### 1. Более агрессивная агрегация секций
```python
# solver/cp_sat_aggregated.py:82
_BUCKET_CHUNK_SIZE = 5  # Было: 1
```
Секции одного типа группируются в бакеты по 5 штук вместо 1 → меньше переменных.

### 2. Более ранняя агрегация
```python
# solver/cp_sat_model.py:69
AGGREGATION_THRESHOLD = 100_000  # Было: 300_000
```
Модель переключится на агрегацию при 100k+ допустимых пар вместо 300k+.

### 3. Больше времени для solver
```python
# tests/test_s7_vs_standard.py:44
timeLimitSeconds = 180  # Было: 120
```
CP-SAT получит 180s вместо 120s для поиска оптимального решения.

---

## Базовые результаты (до оптимизации)

```
Параметры: BUCKET=1, THRESHOLD=300k, TIME=120s
Результат: 3239/3406 (95.1%), 188.3s, FEASIBLE
```

## Ожидаемые результаты (после оптимизации)

```
Параметры: BUCKET=5, THRESHOLD=100k, TIME=180s
Ожидается: ~3242-3245/3406 (95.2%+), ~200-240s, OPTIMAL/FEASIBLE
```

---

## Статус

⏳ **Тест выполняется:** `pytest tests/test_s7_vs_standard.py -v -s`  
📂 **Task ID:** bpi85g08i  
📄 **Output:** `C:\Users\MSHEND~1\AppData\Local\Temp\claude\d--project-OKIL\4f0a0857-cc48-4b5d-a095-a1f68b483e9b\tasks\bpi85g08i.output`

Ожидаемое время выполнения: ~3-4 минуты
