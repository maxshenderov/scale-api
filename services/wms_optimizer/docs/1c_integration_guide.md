# Интеграция WMS оптимизатора с 1С

**Дата:** 2026-07-24  
**Статус:** 📖 Документация

---

## Обзор

WMS оптимизатор (Python FastAPI) интегрирован с 1С через HTTP REST API.  
Основной модуль в 1С: `Лико_WMS_Сервер` (`1s/ERP/extensions/liko/CommonModules/Лико_WMS_Сервер/Ext/Module.bsl`)

---

## Архитектура взаимодействия

```
1С (Лико_WMS_Сервер)  ←→  Python FastAPI (wms_optimizer)
                HTTP REST API
```

### Основные endpoints в 1С

| Endpoint | Назначение |
|----------|-----------|
| `WMS_CheckConnection` | Health-check, проверка доступности |
| `WMS_GetOccupancy` | Получить текущее состояние склада (occupancy) |
| `WMS_GetFloorPallets` | Получить паллеты с пола для размещения |
| `WMS_PlacePallets` | Выполнить план размещения от оптимизатора |
| `WMS_ExportSnapshot` | Экспорт состояния склада |

---

## Сценарий 1: Размещение паллет с пола (холодный старт)

### Шаг 1: Получить occupancy из 1С

**Python → 1С:**
```python
import httpx

# Получить текущее состояние секций
response = httpx.post(
    "http://1c-server/liko/hs/wms/GetOccupancy",
    json={"warehouse": "warehouse-guid-here"}
)
occupancy = response.json()["sections"]
```

**1С возвращает:**
```json
{
  "ok": true,
  "sections": [
    {
      "section_id": "guid",
      "section_code": "Р701-М(01-02-03)-Э01",
      "rack_id": "guid",
      "width": 2600.0,
      "height": 4200.0,
      "depth": 1200.0,
      "max_pallets": 3,
      "max_weight": 3000.0,
      "narrow_aisle": false,
      "address1": "Р701М1Э1",  // КОД ячейки, не GUID
      "address2": "Р701М2Э1",
      "address3": "Р701М3Э1",
      "pallets": [
        {
          "id": "pallet-guid",
          "width": 1200,
          "height": 1400,
          "depth": 800,
          "weight": 500,
          "level": 1  // 1, 2, или 3
        }
      ],
      "version": 42  // Для optimistic locking
    }
  ]
}
```

### Шаг 2: Получить паллеты с пола

**Python → 1С:**
```python
response = httpx.post(
    "http://1c-server/liko/hs/wms/GetFloorPallets",
    json={"warehouse": "warehouse-guid-here"}
)
floor_pallets = response.json()["pallets"]
```

### Шаг 3: ЭТАП 1 — Размещение без реслота

**Python (оптимизатор):**
```python
from api.schemas import OptimizationRequest, OptimizationSettingsSchema
from optimizer.global_optimizer import run_optimization

req = OptimizationRequest(
    optimizationId="COLD-START-001",
    mode="place",
    occupancy=occupancy,  # Из шага 1
    newPallets=floor_pallets,  # Из шага 2
    settings=OptimizationSettingsSchema(
        allowReslot=False,  # НЕТ реслота на ЭТАПЕ 1
        maxOperations=5000,
        timeLimitSeconds=180,
        strictNarrowAislePlacement=True,  # ⚠️ ВАЖНО для разделения
    ),
)

resp_stage1 = run_optimization(req)

print(f"ЭТАП 1: Размещено {resp_stage1.metrics.placedPallets}/{len(floor_pallets)}")
print(f"Время: {resp_stage1.executionTimeSeconds}s")
print(f"Не размещено: {resp_stage1.metrics.notPlacedPallets}")
```

**Результат ЭТАПА 1:**
- Размещено: ~3240/3406 (95%)
- Не размещено: ~165
- Время: ~180-250s

### Шаг 4: ЭТАП 2 — Реслот для улучшения

**Построить occupancy после ЭТАПА 1:**
```python
# Построить новую occupancy из операций ЭТАПА 1
occupancy_after_stage1 = []

for section in occupancy:
    section_dict = section.model_dump()
    section_dict["pallets"] = []  # Очистим — заполним из operations
    occupancy_after_stage1.append(OccupancySectionSchema(**section_dict))

# Заполнить секции из operations ЭТАПА 1
section_pallets_map = {}
for op in resp_stage1.operations:
    if op.operation == "PUT" and op.newAddress:
        # Извлечь section_id из адреса (формат: "Р701М1Э1" → "Р701-М(01-02-03)")
        section_id = op.newAddress[:op.newAddress.rfind('Э')]
        
        if section_id not in section_pallets_map:
            section_pallets_map[section_id] = []
        
        # Найти паллету в floor_pallets
        pallet = next((p for p in floor_pallets if p.id == op.pallet), None)
        if pallet:
            section_pallets_map[section_id].append({
                "id": pallet.id,
                "width": pallet.width,
                "height": pallet.height,
                "depth": pallet.depth,
                "weight": pallet.weight,
            })

# Обновить occupancy
for section in occupancy_after_stage1:
    if section.section_id in section_pallets_map:
        section.pallets = section_pallets_map[section.section_id]
```

**Запустить ЭТАП 2 с реслотом:**
```python
# Паллеты которые не разместились на ЭТАПЕ 1
not_placed_ids = {np.pallet for np in resp_stage1.notPlaced}
not_placed_pallets = [p for p in floor_pallets if p.id in not_placed_ids]

req_stage2 = OptimizationRequest(
    optimizationId="COLD-START-002-RESLOT",
    mode="place",
    occupancy=occupancy_after_stage1,  # ⚠️ Состояние после ЭТАПА 1
    newPallets=not_placed_pallets,  # ⚠️ Только не размещённые
    settings=OptimizationSettingsSchema(
        allowReslot=True,  # ✅ ВКЛЮЧАЕМ реслот
        maxReslotPercent=10,  # До 10% существующих можно двигать
        maxOperations=5000,
        timeLimitSeconds=120,
        strictNarrowAislePlacement=True,
    ),
)

resp_stage2 = run_optimization(req_stage2)

print(f"ЭТАП 2: Дополнительно размещено {resp_stage2.metrics.placedPallets}/{len(not_placed_pallets)}")
print(f"Время: {resp_stage2.executionTimeSeconds}s")
print(f"Передвинуто: {resp_stage2.metrics.movedPallets}")
```

**Результат ЭТАПА 2:**
- Дополнительно размещено: +91/165
- Время: ~4-10s (OPTIMAL!)
- Передвинуто: 0 (нашёл свободные места)

**ИТОГО:**
- Размещено: 3241 + 91 = **3332/3406 (97.8%)**
- Общее время: 248s + 4s = **252s**

### Шаг 5: Отправить план в 1С для выполнения

**Объединить операции ЭТАПА 1 и ЭТАПА 2:**
```python
all_operations = resp_stage1.operations + resp_stage2.operations

# Разделить на placements и rearrangements
placements = []
rearrangements = []

for op in all_operations:
    if op.operation == "PUT":
        placements.append({
            "pallet": op.pallet,
            "address": op.newAddress,  # ⚠️ КОД ячейки, не GUID
            "section": extract_section_guid(op.newAddress),  # Опционально
        })
    elif op.operation == "MOVE":
        rearrangements.append({
            "pallet": op.pallet,
            "fromAddress": op.oldAddress,
            "toAddress": op.newAddress,
        })

# Собрать версии секций из occupancy
versions = []
for section in occupancy:
    versions.append({
        "section": section.section_id,
        "version": section.version,
    })
```

**Python → 1С (выполнить план):**
```python
response = httpx.post(
    "http://1c-server/liko/hs/wms/PlacePallets",
    json={
        "warehouse": "warehouse-guid",
        "placements": placements,
        "rearrangements": rearrangements,
        "versions": versions,  # ⚠️ Optimistic locking
    },
    timeout=300,  # 5 минут на выполнение
)

result = response.json()
if result["ok"]:
    print(f"Выполнено: {result['executed']} операций")
    if result.get("warnings"):
        print(f"Предупреждения: {result['warnings']}")
else:
    error = result["error"]
    if error["code"] == "PLAN_INVALID":
        print("План устарел! Нужно пересчитать:")
        print(f"Конфликтов версий: {len(error['conflicts'])}")
        # Повторить с шага 1
    else:
        print(f"Ошибка: {error['message']}")
```

---

## Сценарий 2: Параллельное размещение (узкопроходные + широкие)

**⚠️ ЭКСПЕРИМЕНТАЛЬНО — см. [[parallel_narrow_wide_solution]]**

Когда `strictNarrowAislePlacement=True`, можно разделить на две независимые задачи:

### Вариант A: Последовательное решение

```python
# 1. Разделить паллеты
narrow_pallets = [p for p in floor_pallets 
                 if p.width <= 1200 and p.depth <= 1200]
wide_pallets = [p for p in floor_pallets 
               if p.width > 1200 or p.depth > 1200]

# 2. Разделить секции
narrow_sections = [s for s in occupancy if s.narrow_aisle]
wide_sections = [s for s in occupancy if not s.narrow_aisle]

# 3. ЗАДАЧА 1: Узкопроходные
req_narrow = OptimizationRequest(
    optimizationId="NARROW-001",
    mode="place",
    occupancy=narrow_sections,
    newPallets=narrow_pallets,
    settings=OptimizationSettingsSchema(
        allowReslot=False,
        timeLimitSeconds=120,
        strictNarrowAislePlacement=True,
    ),
)
resp_narrow = run_optimization(req_narrow)

# 4. ЗАДАЧА 2: Широкие
req_wide = OptimizationRequest(
    optimizationId="WIDE-001",
    mode="place",
    occupancy=wide_sections,
    newPallets=wide_pallets,
    settings=OptimizationSettingsSchema(
        allowReslot=False,
        timeLimitSeconds=90,
        strictNarrowAislePlacement=True,
    ),
)
resp_wide = run_optimization(req_wide)

# 5. Объединить occupancy
combined_occupancy = narrow_sections_after + wide_sections_after

# 6. РЕСЛОТ для не размещённых
not_placed_narrow = [...]
not_placed_wide = [...]
all_not_placed = not_placed_narrow + not_placed_wide

req_reslot = OptimizationRequest(
    optimizationId="RESLOT-001",
    mode="place",
    occupancy=combined_occupancy,
    newPallets=all_not_placed,
    settings=OptimizationSettingsSchema(
        allowReslot=True,
        maxReslotPercent=10,
        timeLimitSeconds=120,
    ),
)
resp_reslot = run_optimization(req_reslot)
```

**Ожидаемый выигрыш:**
- Время: 120s + 90s + 10s = **220s** vs 252s текущий
- Качество: **~3300-3350/3406** (гипотеза)

---

## Важные детали

### Адреса ячеек: GUID vs КОД

⚠️ **occupancy отдаёт address1/2/3 как КОД ячейки** (`"Р701М1Э1"`), **НЕ GUID!**

1С функция `ЯчейкаПоСтрокеАдреса()` принимает оба варианта:
1. Сначала пробует как GUID
2. При неудаче — ищет по коду ячейки

**Оптимизатор должен возвращать:**
```python
{
    "operation": "PUT",
    "pallet": "pallet-guid",
    "newAddress": "Р701М1Э1",  # КОД, как в occupancy.address1/2/3
}
```

### Optimistic Locking (версии секций)

1С проверяет версии ПЕРЕД выполнением плана (§15 ТЗ):

```python
# Версии из occupancy (шаг 1)
versions = [
    {"section": "section-guid-1", "version": 42},
    {"section": "section-guid-2", "version": 17},
]

# Если хотя бы одна версия изменилась → PLAN_INVALID
response = httpx.post(".../PlacePallets", json={
    "warehouse": "...",
    "placements": [...],
    "rearrangements": [...],
    "versions": versions,  # ⚠️ ОБЯЗАТЕЛЬНО
})

if response.json()["error"]["code"] == "PLAN_INVALID":
    # План устарел — повторить с шага 1 (GetOccupancy)
    conflicts = response.json()["error"]["conflicts"]
    print(f"Изменилось секций: {len(conflicts)}")
```

### strictNarrowAislePlacement

⚠️ **Ключевой параметр для параллельного подхода:**

- `True` — узкопроходные паллеты ТОЛЬКО в узкопроходные секции
- `False` — узкопроходные могут идти в любые секции

**Для параллельного решения ОБЯЗАТЕЛЬНО `True`!**

---

## См. также

- [[two_stage_reslot_approach]] — успешный двухэтапный подход
- [[parallel_narrow_wide_solution]] — экспериментальный параллельный подход
- [[phase_c_final_report]] — итоги Фазы C
- `Лико_WMS_Сервер` — BSL-модуль в 1С (`1s/ERP/extensions/liko/CommonModules/`)
