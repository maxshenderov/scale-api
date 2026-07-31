# WMS Optimizer — Методология оптимизации

> Как работает оптимизатор размещения паллет: архитектура решения, scoring system, potential loss, настройка коэффициентов.

## Связь с проектом

Эта страница описывает **методологию** — как устроен алгоритм оптимизации. Для API endpoints и интеграции с 1С см. [[wms_optimizer]].

Базовое ТЗ: [ТЗ/WMS_Optimizer_OKIL_Adapted.md](../../ТЗ/WMS_Optimizer_OKIL_Adapted.md)

---

## Архитектура решения

### Двухуровневая оптимизация

```
┌─────────────────────────────────────────────┐
│ Global Optimizer (CP-SAT)                   │
│ ─────────────────────────────────────────── │
│ Решает: какие паллеты → в какие секции      │
│ Цель: максимизировать GlobalScore           │
│ Ограничения:                                │
│   - физические (§7 ТЗ): Fits(p, s)          │
│   - версия секции (§15 ТЗ)                  │
│   - maxOperations (§6 ТЗ): лимит реслотинга │
└─────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────┐
│ Section Optimizer                           │
│ ─────────────────────────────────────────── │
│ Решает: порядок паллет внутри секции        │
│ Цель: максимизировать AddressScore          │
│ Правила: портированы из ПодобратьЯчейку     │
└─────────────────────────────────────────────┘
```

**CP-SAT (Constraint Programming - SAT solver)** — Google OR-Tools. Решает задачу комбинаторной оптимизации с ограничениями за O(seconds-minutes) для ~2000 паллет и ~1400 секций.

---

## Scoring System

### GlobalScore (§9.1 ТЗ) — целевая функция глобального оптимизатора

**Максимизируется.** Учитывает:

| Компонента | Вес (weights.json) | Смысл |
|---|---|---|
| `placed_pallets` | `+placedPalletWeight` | Награда за размещённую паллету |
| `section_moves` | `-sectionMovePenalty` | Штраф за переброс паллеты между секциями |
| `address_moves` | `-addressMovePenalty` | Штраф за смену адреса внутри секции |
| `potential_loss` | `-potentialLossPenalty` | Штраф за потерю потенциала секции |
| `unused_space` | `-spaceLossPenalty` | Штраф за неиспользованное место |
| `used_sections` | `-sectionUsagePenalty` | Штраф за фрагментацию склада |

**Реализация:** [`optimizer/scoring.py:compute_global_score()`](../../services/wms_optimizer/optimizer/scoring.py)

**Формула:**
```python
GlobalScore = (
    placedPalletWeight * placed_pallets
    - sectionMovePenalty * section_moves
    - addressMovePenalty * address_moves
    - potentialLossPenalty * potential_loss
    - spaceLossPenalty * unused_space
    - sectionUsagePenalty * used_sections
)
```

---

### AddressScore (§9.2 ТЗ) — локальная оценка адреса

**Максимизируется.** Определяет порядок паллет внутри секции:

| Компонента | Вес | Смысл |
|---|---|---|
| `width_residual` | `-widthResidualPenalty` | Штраф за неоптимальное использование ширины секции |
| `future_potential` | `+futurePotentialReward` | Награда за сохранение потенциала для будущих паллет |
| `potential_loss` | `-potentialLossPenalty` | Штраф за потерю потенциала |

**Реализация:** [`optimizer/scoring.py:compute_address_score()`](../../services/wms_optimizer/optimizer/scoring.py)

**Формула:**
```python
AddressScore = (
    - widthResidualPenalty * width_residual
    + futurePotentialReward * future_potential
    - potentialLossPenalty * potential_loss
)
```

---

## Potential — ключевая концепция

**Потенциал секции** = количество **необработанных** паллет, которые гипотетически могут поместиться в эту секцию.

### Зачем нужен Potential?

Оптимизатор жадный: обрабатывает паллеты по одной. **Potential** предотвращает близорукие решения:

- ❌ **Без Potential:** оптимизатор размещает широкую паллету → узкая секция становится непригодной для будущих узких паллет → неоптимально.
- ✅ **С Potential:** оптимизатор видит `PotentialLoss = 5` (секция теряет возможность принять 5 узких паллет) → штрафует это решение → ищет лучший вариант.

### Три функции (§8 ТЗ)

**Модуль:** [`optimizer/potential.py`](../../services/wms_optimizer/optimizer/potential.py)

1. **`compute_potential(section, pallets_in_section, remaining_pallets)`**  
   Для каждой паллеты из `remaining_pallets` проверяет `Fits(p, section)` с учётом уже размещённых `pallets_in_section`.

2. **`compute_potential_after_placement(section, pallets, new_pallet, remaining)`**  
   Гипотетически размещает `new_pallet` → пересчитывает потенциал.

3. **`compute_potential_loss(section, pallets, new_pallet, remaining)`**  
   ```python
   PotentialLoss = PotentialBefore - PotentialAfter
   ```
   **Неотрицательно** (по определению): размещение паллеты не может **увеличить** потенциал.

### Физическая проверка Fits(p, section)

Паллета `p` помещается в секцию, если:

1. **Ширина:** `FreeWidth >= p.width + GapWidth`  
   где `FreeWidth = SectionWidth - SUM(PalletWidth) - (N+1)*GapWidth`
2. **Высота:** `p.height <= section.height`
3. **Глубина:** `p.depth <= section.depth`
4. **Вес:** `CurrentWeight + p.weight <= section.max_lift_weight`
5. **Количество:** `len(pallets_in_section) < section.max_pallets`

**Реализация:** [`potential.py:_fits()`](../../services/wms_optimizer/optimizer/potential.py#L71-L91)

---

## Настройка коэффициентов (§19 ТЗ)

### Где хранятся веса

**Файл:** [`config/weights.json`](../../services/wms_optimizer/config/weights.json)

```json
{
  "globalWeights": {
    "placedPalletWeight": 1000,
    "sectionMovePenalty": 50,
    "addressMovePenalty": 10,
    "potentialLossPenalty": 20,
    "spaceLossPenalty": 5,
    "sectionUsagePenalty": 15
  },
  "localWeights": {
    "widthResidualPenalty": 10,
    "futurePotentialReward": 15,
    "potentialLossPenalty": 20
  }
}
```

### Методика подбора

**Baseline:** веса выше — начальная конфигурация. Все тесты проходят с ними.

**Итеративная настройка:**

1. **Запустить тесты:**  
   ```bash
   cd services/wms_optimizer
   pytest tests/ -v
   ```
   Базовая корректность: 32 теста должны быть зелёными.

2. **Запустить на реальных данных:**  
   Оптимизировать snapshot с ~2000 паллет (Ликофлекс Высотный). Собрать метрики:
   - `placed_pallets` / `total_pallets` — коэффициент размещения
   - `section_moves` — количество реслотов
   - `address_moves` — внутрисекционные перемещения
   - `potential_loss_total` — суммарная потеря потенциала
   - `used_sections` — фрагментация склада

3. **Сравнить с текущей эвристикой [[Лико_ПодобратьЯчейку]]:**  
   Экспортировать текущее размещение из 1С → запустить оптимизатор → сравнить метрики.

4. **Скорректировать веса:**
   - **Слишком много реслота?** → увеличить `sectionMovePenalty`
   - **Низкий коэффициент размещения?** → увеличить `placedPalletWeight`, снизить штрафы
   - **Фрагментация склада?** → увеличить `sectionUsagePenalty`
   - **Плохое использование ширины секций?** → увеличить `widthResidualPenalty`

5. **Повторить пункты 1-4** до достижения целевых метрик.

### Целевые метрики (примерные)

| Метрика | Baseline (эвристика) | Цель оптимизатора |
|---|---|---|
| Коэффициент размещения | 85-90% | ≥95% |
| Section moves | 0 (новые паллеты) | <5% от total |
| Potential loss total | N/A | минимизировать |
| Used sections | N/A | минимизировать |

---

## Оценка качества решения (§20 ТЗ)

### Автоматические тесты

**Файлы:**
- [`tests/test_acceptance.py`](../../services/wms_optimizer/tests/test_acceptance.py) — 8 приёмочных сценариев
- [`tests/test_potential.py`](../../services/wms_optimizer/tests/test_potential.py) — проверка корректности Potential
- [`tests/test_scoring.py`](../../services/wms_optimizer/tests/test_scoring.py) — проверка scoring functions

**Критерий:** все тесты зелёные = базовая корректность гарантирована.

### Метрики результата (§14.6 ТЗ)

**Структура ответа `/optimize`:**

```json
{
  "score": 47850.5,
  "metrics": {
    "placed_pallets": 48,
    "not_placed": 2,
    "section_moves": 3,
    "address_moves": 5,
    "potential_loss_total": 7,
    "used_sections": 12,
    "solver_time_seconds": 4.2,
    "solver_status": "OPTIMAL"
  }
}
```

### Сравнение с baseline (ручное размещение)

**Шаги:**

1. **Экспорт текущего состояния из 1С:**  
   `WMS_ExportSnapshot()` → `snapshot_baseline.json`

2. **Запуск оптимизатора:**  
   ```bash
   curl -X POST http://localhost:8005/optimize \
     -H "Content-Type: application/json" \
     -d @snapshot_baseline.json
   ```

3. **Сохранить результат:**  
   `optimization_result.json`

4. **Посчитать метрики для baseline:**  
   Скрипт `scripts/compare_placement.py` (создать):
   ```python
   # Сравнивает текущее размещение (из 1С) с планом оптимизатора
   # Выводит diff по метрикам
   ```

5. **Визуализация:**  
   Таблица сравнения:

   | Метрика | Baseline (1С) | Optimizer | Δ |
   |---|---|---|---|
   | Размещено паллет | 1850/2000 (92.5%) | 1920/2000 (96%) | +3.5% |
   | Фрагментация (секций) | 145 | 128 | -11.7% |
   | Potential loss | N/A | 42 | N/A |

### A/B тест в production

**Сценарий:**

1. **Неделя 1:** новые паллеты размещаются по старой эвристике [[Лико_ПодобратьЪчейку]]
2. **Неделя 2:** новые паллеты размещаются через оптимизатор
3. **Метрики:**
   - Время работы кладовщика (от получения задания до завершения)
   - Количество ошибок размещения (нарушение §7 ТЗ)
   - Коэффициент заполнения склада (паллет/секция)
   - Количество жалоб на "нет места" при наличии физического пространства

**Критерий успеха:** оптимизатор не хуже baseline по времени работы И лучше по коэффициенту заполнения.

---

## Ограничения и компромиссы

### maxOperations (§6 ТЗ)

**Проблема:** реслот дорого́й — кладовщик переставляет паллеты физически.

**Решение:** параметр `maxOperations` ограничивает количество операций `MOVE` (реслот).

**Компромисс:**
- `maxOperations = 0` → оптимизатор = эвристика (нет реслота)
- `maxOperations = 50` → оптимизатор может переставить до 50 паллет для улучшения плана
- `maxOperations = ∞` → максимальное качество, но долгая работа кладовщика

**Настройка:** бизнес-решение. Начать с `maxOperations = 10`, измерить время работы кладовщика, скорректировать.

### timeLimitSeconds (§6 ТЗ)

**Проблема:** CP-SAT может искать решение бесконечно.

**Решение:** жёсткий лимит времени. После истечения возвращается лучшее найденное решение.

**Статусы:**
- `OPTIMAL` — найдено доказуемо лучшее решение
- `FEASIBLE` — найдено корректное решение, но может быть лучше
- `INFEASIBLE` — не существует решения (все паллеты слишком большие)

**Типичные значения:** 30-300 секунд для ~2000 паллет.

---

## Связанные страницы

- [[wms_optimizer]] — API endpoints, интеграция с 1С
- [[Лико_WMS_Сервер]] — BSL API модуль
- [[Лико_ПодобратьЯчейку]] — текущая эвристика (baseline для сравнения)
- [[Лико_СкладскиеСекции]] — структура данных секций

---

## Changelog

- **2026-07-21:** Создание страницы — методология scoring system, potential loss, настройка коэффициентов (§19/§20 ТЗ)
