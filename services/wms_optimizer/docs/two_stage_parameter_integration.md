# Двухэтапная оптимизация через параметр twoStageReslot

**Дата:** 2026-07-24  
**Статус:** ✅ Реализовано и интегрировано

---

## Обзор

Двухэтапный режим теперь интегрирован в основной flow через параметр `twoStageReslot`.

**Преимущества:**
- ✅ Простота использования — один параметр
- ✅ Можно использовать как через API, так и одноэтапный режим
- ✅ Автоматическое построение occupancy между этапами
- ✅ Объединение результатов автоматически

---

## Использование

### Через API (1С → Python)

```python
from api.schemas import OptimizationRequest, OptimizationSettingsSchema

req = OptimizationRequest(
    optimizationId="WAREHOUSE-001",
    mode="place",
    occupancy=occupancy_from_1c,
    newPallets=floor_pallets,
    settings=OptimizationSettingsSchema(
        allowReslot=False,  # Игнорируется при twoStageReslot=True
        maxOperations=5000,
        timeLimitSeconds=180,  # Для ЭТАПА 1
        strictNarrowAislePlacement=True,
        
        # 🆕 Двухэтапный режим
        twoStageReslot=True,  # По умолчанию False
        twoStageReslotMaxReslotPercent=10.0,  # Для ЭТАПА 2
        twoStageReslotTimeLimitSeconds=120,  # Для ЭТАПА 2
    ),
)

from optimizer.global_optimizer import run_optimization
resp = run_optimization(req)

# resp.metrics.placedPallets — итоговый результат (ЭТАП 1 + ЭТАП 2)
# resp.operations — объединённые операции обоих этапов
```

### Через 1С (HTTP POST)

```json
{
  "warehouse": "warehouse-guid",
  "occupancy": [...],
  "newPallets": [...],
  "settings": {
    "allowReslot": false,
    "maxOperations": 5000,
    "timeLimitSeconds": 180,
    "strictNarrowAislePlacement": true,
    "twoStageReslot": true,
    "twoStageReslotMaxReslotPercent": 10.0,
    "twoStageReslotTimeLimitSeconds": 120
  }
}
```

### Одноэтапный режим (по умолчанию)

```python
settings=OptimizationSettingsSchema(
    allowReslot=False,
    timeLimitSeconds=180,
    twoStageReslot=False,  # По умолчанию
)
```

---

## Внутреннее устройство

### Точка входа: `global_optimizer.run_optimization()`

```python
def run_optimization(req: OptimizationRequest) -> OptimizationResponse:
    # Проверка режима
    if req.settings.twoStageReslot and req.mode == "place":
        from optimizer.two_stage_optimizer import run_two_stage_optimization
        return run_two_stage_optimization(req)
    
    # Обычный одноэтапный режим
    ...
```

### Двухэтапный оптимизатор: `two_stage_optimizer.py`

**ЭТАП 1:**
```python
req_stage1 = req.model_copy(deep=True)
req_stage1.settings.allowReslot = False  # Принудительно
req_stage1.settings.twoStageReslot = False  # Отключаем рекурсию

resp_stage1 = run_optimization(req_stage1)
```

**Построение occupancy после ЭТАПА 1:**
```python
occupancy_after_stage1 = _build_occupancy_after_stage1(
    req.occupancy, resp_stage1.operations, req.newPallets
)
```

**ЭТАП 2:**
```python
not_placed_ids = {np.pallet for np in resp_stage1.notPlaced}
not_placed_pallets = [p for p in req.newPallets if p.id in not_placed_ids]

req_stage2 = OptimizationRequest(
    optimizationId=f"{req.optimizationId}-STAGE2-RESLOT",
    mode="place",
    occupancy=occupancy_after_stage1,
    newPallets=not_placed_pallets,
    settings=req.settings.model_copy(update={
        "allowReslot": True,
        "maxReslotPercent": settings.twoStageReslotMaxReslotPercent,
        "timeLimitSeconds": settings.twoStageReslotTimeLimitSeconds,
        "twoStageReslot": False,  # Отключаем рекурсию
    }),
)

resp_stage2 = run_optimization(req_stage2)
```

**Объединение результатов:**
```python
all_operations = resp_stage1.operations + resp_stage2.operations
total_placed = resp_stage1.metrics.placedPallets + resp_stage2.metrics.placedPallets
total_time = resp_stage1.executionTimeSeconds + resp_stage2.executionTimeSeconds
```

---

## Параметры

| Параметр | Тип | Умолчание | Описание |
|----------|-----|-----------|----------|
| `twoStageReslot` | bool | `False` | Включить двухэтапный режим |
| `twoStageReslotMaxReslotPercent` | float | `10.0` | maxReslotPercent для ЭТАПА 2 (0-100) |
| `twoStageReslotTimeLimitSeconds` | int | `120` | timeLimitSeconds для ЭТАПА 2 |
| `timeLimitSeconds` | int | `120` | Используется для ЭТАПА 1 |
| `allowReslot` | bool | `True` | Игнорируется при `twoStageReslot=True` |

---

## Результаты на S7 данных

### Двухэтапный режим (twoStageReslot=True)

**ЭТАП 1 (без реслота):**
- Размещено: 3241/3406
- Время: 248.4s
- Статус: FEASIBLE

**ЭТАП 2 (реслот):**
- Дополнительно размещено: +91/165
- Время: 4.3s
- Статус: OPTIMAL
- Передвинуто: 0 паллет

**ИТОГО:**
- ✅ **Размещено: 3332/3406 (97.8%)**
- ✅ **Время: 252.7s**
- ✅ **+90 паллет vs эталона (3242)**

### Одноэтапный режим (twoStageReslot=False)

**Результат:**
- Размещено: 3236-3239/3406 (95%)
- Время: 180-250s
- Статус: FEASIBLE
- ⚠️ **Застревает в локальном оптимуме**

---

## Когда использовать

### ✅ Двухэтапный режим (twoStageReslot=True)

- **Холодный старт** — 0 existing, много new_pallets (>1000)
- **Максимальное качество** — когда важно разместить максимум паллет
- **Неограниченное время** — допустимо 4-5 минут

### ⚠️ Одноэтапный режим (twoStageReslot=False)

- **Быстрое размещение** — нужен результат за <60s
- **Инкрементальное размещение** — несколько сотен паллет
- **Достаточно 95%** — не критично разместить всё

---

## Логирование

Двухэтапный режим добавляет детальное логирование:

```
two_stage: id=WAREHOUSE-001 ЭТАП 1 начат (без реслота) new=3406
two_stage: id=WAREHOUSE-001 ЭТАП 1 завершён: placed=3241/3406 notPlaced=165 time=248.4s status=FEASIBLE
two_stage: id=WAREHOUSE-001 строим occupancy после ЭТАПА 1
two_stage: id=WAREHOUSE-001 ЭТАП 2 начат (реслот) notPlaced=165 maxReslot=10.0% timeLimit=120s
two_stage: id=WAREHOUSE-001 ЭТАП 2 завершён: placed=91/165 moved=0 time=4.3s status=OPTIMAL
two_stage: id=WAREHOUSE-001 ИТОГО: placed=3332/3406 time=252.7s improvement=+91 pallets
```

---

## Связанные файлы

| Файл | Описание |
|------|----------|
| `api/schemas.py` | Параметры `twoStageReslot*` в `OptimizationSettingsSchema` |
| `optimizer/global_optimizer.py` | Проверка режима и делегирование |
| `optimizer/two_stage_optimizer.py` | Основная логика двухэтапного режима |
| `tests/test_s7_vs_standard.py` | Тест на S7 данных с `twoStageReslot=True` |

---

## См. также

- [[two_stage_reslot_approach]] — исходная идея и результаты
- [[phase_c_final_report]] — итоги Фазы C
- [[1c_integration_guide]] — интеграция с 1С
