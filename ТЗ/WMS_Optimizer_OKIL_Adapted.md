# WMS Pallet Optimizer — Адаптация для OKIL

> Адаптация ТЗ v4 FINAL под архитектуру проекта OKIL (Лико).
> Базовое ТЗ: `ТЗ_WMS_Pallet_Optimizer_v4_FINAL.md`
> Дата адаптации: 2026-07-21

---

## 🔗 Связь с объектами OKIL

### 1С Объекты (существующие)

| Сущность ТЗ | Объект OKIL | Путь | Примечание |
|---|---|---|---|
| **Паллета** | `Лико_Паллеты2_0` | `Справочники.Лико_Паллеты2_0` | версия 2.0 |
| **Типоразмер паллеты** | `Лико_ТипоразмерыПаллет` | `Справочники.Лико_ТипоразмерыПаллет` | width/height/depth/weight |
| **Секция** | `Лико_СкладскиеСекции` | `Справочники.Лико_СкладскиеСекции` | ⚠️ нужно поле `Версия` |
| **Типоразмер секции** | `Лико_ТипоразмерыСкладскихСекций` | `Справочники.Лико_ТипоразмерыСкладскихСекций` | ⚠️ проверить `gapWidth`, `maxLiftWeight` |
| **Стеллаж** | `Лико_Стеллажи` | `Справочники.Лико_Стеллажи` | управление топологией |
| **Адрес (ячейка)** | `СкладскиеЯчейки` | `Справочники.СкладскиеЯчейки` | типовой, 3 адреса/секцию |
| **Размещение паллет** | `Лико_ПаллетыВСекциях` | `РегистрНакопления.Лико_ПаллетыВСекциях` | текущее состояние |
| **Параметры паллеты** | `Лико_ПараметрыПаллет` | `РегистрСведений.Лико_ПараметрыПаллет` | габариты |
| **Задание на размещение** | `Лико_ЗаданиеНаОтборРазмещение` | `Документы.Лико_ЗаданиеНаОтборРазмещение` | исполнение плана |

### API Модули (существующие)

| Модуль | Назначение | ProcName |
|---|---|---|
| `Лико_WMS_Сервер` | REST API для WMS | 10 функций |
| `Liko_Rest` | HTTP-сервис | маршрутизация `/wms/*` |
| `Лико_HTTP_Сервер` | HTTP-утилиты | JSON, сериализация |

---

## 🏗️ Архитектура (адаптация §2)

```
┌─────────────────────────────────────────────────────────────────┐
│                    1С WMS (ERP OKIL)                            │
│                                                                 │
│  Модуль: Лико_WMS_Сервер                                       │
│  ├─ WMS_ExportSnapshot()      → snapshot для оптимизации       │
│  ├─ WMS_ValidatePlacement()   → проверка §7                    │
│  └─ WMS_PlacePallets()        → исполнение плана §14           │
│                                                                 │
│  HTTP-сервис: hs/LikoRest/API                                  │
│  Auth: Basic (administrator / пароль из базы)                  │
└─────────────────────────────────────────────────────────────────┘
                            ↕ HTTP POST (JSON)
┌─────────────────────────────────────────────────────────────────┐
│        Python WMS Optimizer (FastAPI, порт 8005)               │
│                                                                 │
│  POST /optimize          → синхронный расчёт §13.1             │
│  POST /optimize?async    → асинхронный §13.2                   │
│  GET  /optimization/{id} → статус                              │
│                                                                 │
│  ┌──────────────────┐         ┌─────────────────┐             │
│  │ Global Optimizer │         │ Section         │             │
│  │ OR-Tools CP-SAT  │ ──────→ │ Optimizer       │             │
│  │ (§9.1)           │         │ (§9.2 + правила │             │
│  │                  │         │  ПодобратьЯчейку)│             │
│  └──────────────────┘         └─────────────────┘             │
│           ↓                            ↓                        │
│  ┌─────────────────────────────────────────────┐               │
│  │     Optimization Plan (§14)                 │               │
│  │  - operations[]  (PUT/MOVE/KEEP)            │               │
│  │  - notPlaced[]   (с причинами §12)          │               │
│  │  - metrics       (§14.6)                    │               │
│  └─────────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────┘
                            ↓ HTTP POST
                   WMS_PlacePallets(operations)
                            ↓
              Лико_ЗаданиеНаОтборРазмещение
```

---

## 🔧 Изменения в 1С (BSL)

### A. Добавить поле `Версия` в `Лико_СкладскиеСекции`

**Через расширение или EDT:**

```bsl
// Справочник.Лико_СкладскиеСекции
// Новый реквизит: Версия (Число, 10, 0)

// Модуль объекта:
Процедура ПередЗаписью(Отказ)
    
    //+Лико m.shenderov 21.07.2026 — инкремент версии для контроля snapshot'а
    Если НЕ ЭтоНовый() Тогда
        // При любом изменении секции
        Версия = Версия + 1;
    КонецЕсли;
    
КонецПроцедуры
```

**Когда инкрементировать:**
- Изменение типоразмера секции
- Блокировка/разблокировка секции
- Изменение размещённых паллет (автоматически через документы)

### B. Расширить `WMS_ExportSnapshot()` версиями

**Модуль `Лико_WMS_Сервер`:**

```bsl
Функция WMS_ExportSnapshot(ПараметрыPOST) Экспорт
    
    СкладСсылка = СсылкаПоСтрокеGUID(ПараметрыPOST["warehouse"], "Склады");
    
    // Существующая логика сборки snapshot'а...
    Топология = СобратьТопологиюСклада(СкладСсылка);
    Занятость = СобратьЗанятостьСекций(СкладСсылка);
    
    // ⚠️ НОВОЕ: добавить версии секций
    ВерсииСекций = Новый Соответствие;
    
    Запрос = Новый Запрос;
    Запрос.Текст = "
    |ВЫБРАТЬ
    |    Ссылка.УникальныйИдентификатор КАК СекцияGUID,
    |    Ссылка.Версия КАК Версия
    |ИЗ
    |    Справочник.Лико_СкладскиеСекции КАК Секции
    |ГДЕ
    |    Секции.Владелец.Склад = &Склад
    |    И НЕ Секции.ПометкаУдаления
    |";
    Запрос.УстановитьПараметр("Склад", СкладСсылка);
    
    Выборка = Запрос.Выполнить().Выбрать();
    Пока Выборка.Следующий() Цикл
        ВерсииСекций.Вставить(Строка(Выборка.СекцияGUID), Выборка.Версия);
    КонецЦикла;
    
    Результат = Новый Структура;
    Результат.Вставить("ok", Истина);
    Результат.Вставить("data", Новый Структура(
        "warehouse", Топология,
        "occupancy", Занятость,
        "versions", ВерсииСекций  // ⚠️ НОВОЕ
    ));
    
    Возврат Лико_HTTP_Сервер.СоздатьОтветJSON(Результат);
    
КонецФункции
```

### C. Добавить проверку версий в `WMS_PlacePallets()`

**Модуль `Лико_WMS_Сервер`:**

```bsl
Функция WMS_PlacePallets(ПараметрыPOST) Экспорт
    
    //+Лико m.shenderov 21.07.2026 — пакетное размещение с проверкой snapshot'а
    
    МассивОпераций = ПараметрыPOST["operations"];         // массив {pallet, newAddress, operation}
    ЗатронутыеСекции = ПараметрыPOST["affected_sections"]; // массив GUID секций
    ВерсииSnapshot = ПараметрыPOST["snapshot_versions"];   // map[guid]->version
    
    // §15 — проверка актуальности ТОЛЬКО затронутых секций
    Если ЗначениеЗаполнено(ЗатронутыеСекции) И ЗначениеЗаполнено(ВерсииSnapshot) Тогда
        
        Для Каждого СекцияGUID Из ЗатронутыеСекции Цикл
            
            СекцияСсылка = СсылкаПоСтрокеGUID(СекцияGUID, "Лико_СкладскиеСекции");
            ТекущаяВерсия = СекцияСсылка.Версия;
            ОжидаемаяВерсия = Число(ВерсииSnapshot[СекцияGUID]);
            
            Если ТекущаяВерсия <> ОжидаемаяВерсия Тогда
                Возврат HttpВернутьОшибку_WMS(
                    "PLAN_INVALID",
                    СтрШаблон("Секция %1 изменилась (v%2→v%3). План устарел.",
                        СекцияСсылка.Код, ОжидаемаяВерсия, ТекущаяВерсия)
                );
            КонецЕсли;
            
        КонецЦикла;
        
    КонецЕсли;
    
    // Дальше выполнение операций...
    УспешныхОпераций = 0;
    ОшибокОпераций = 0;
    Результаты = Новый Массив;
    
    Для Каждого Операция Из МассивОпераций Цикл
        
        ПаллетGUID = Операция["pallet"];
        НоваяЯчейкаGUID = Операция["newAddress"];
        ТипОперации = Операция["operation"]; // PUT/MOVE/KEEP
        
        Если ТипОперации = "KEEP" Тогда
            // Паллет остаётся на месте — ничего не делаем
            Результаты.Добавить(Новый Структура("pallet", ПаллетGUID, "status", "kept"));
            Продолжить;
        КонецЕсли;
        
        Попытка
            ПаллетСсылка = СсылкаПоСтрокеGUID(ПаллетGUID, "Лико_Паллеты2_0");
            ЯчейкаСсылка = СсылкаПоСтрокеGUID(НоваяЯчейкаGUID, "СкладскиеЯчейки");
            
            // Здесь должна быть логика создания документа Лико_ЗаданиеНаОтборРазмещение
            // или прямое изменение регистра (в зависимости от бизнес-логики)
            
            УспешныхОпераций = УспешныхОпераций + 1;
            Результаты.Добавить(Новый Структура(
                "pallet", ПаллетGUID,
                "status", "success",
                "newAddress", НоваяЯчейкаGUID
            ));
            
        Исключение
            ОшибокОпераций = ОшибокОпераций + 1;
            Результаты.Добавить(Новый Структура(
                "pallet", ПаллетGUID,
                "status", "error",
                "error", ОписаниеОшибки()
            ));
        КонецПопытки;
        
    КонецЦикла;
    
    Ответ = Новый Структура;
    Ответ.Вставить("ok", Истина);
    Ответ.Вставить("data", Новый Структура(
        "total", МассивОпераций.Количество(),
        "success", УспешныхОпераций,
        "errors", ОшибокОпераций,
        "results", Результаты
    ));
    
    Возврат Лико_HTTP_Сервер.СоздатьОтветJSON(Ответ);
    
КонецФункции
```

---

## 🐍 Структура Python-проекта (расширение §16)

```
D:\project\OKIL\services\wms_optimizer\
│
├── main.py                          # FastAPI app, точка входа
├── requirements.txt                 # ortools, fastapi, pydantic, httpx
├── .env.example                     # шаблон конфигурации
├── README.md
│
├── api/
│   ├── __init__.py
│   ├── routes.py                    # POST /optimize, GET /optimization/{id}
│   ├── schemas.py                   # Pydantic models (§14)
│   └── onec_client.py               # HTTP-клиент для Лико_WMS_Сервер
│
├── models/
│   ├── __init__.py
│   ├── pallet.py                    # класс Pallet
│   ├── section.py                   # класс Section
│   ├── address.py                   # класс Address
│   └── warehouse.py                 # WarehouseSnapshot
│
├── optimizer/
│   ├── __init__.py
│   ├── global_optimizer.py          # CP-SAT (§9.1)
│   ├── section_optimizer.py         # адреса внутри секции (§9.2)
│   ├── scoring.py                   # GlobalScore + AddressScore
│   └── potential.py                 # единый расчёт потенциала (§8)
│
├── solver/
│   ├── __init__.py
│   ├── cp_sat_model.py              # построение модели OR-Tools
│   └── warm_start.py                # First Fit Decreasing эвристика
│
├── validation/
│   ├── __init__.py
│   └── validator.py                 # проверка INVALID_DATA (§17.3)
│
├── integration/
│   ├── __init__.py
│   └── podobrat_rules.py            # портирование правил из ПодобратьЯчейку
│
├── tests/
│   ├── __init__.py
│   ├── test_api.py
│   ├── test_optimizer.py
│   ├── test_potential.py
│   └── fixtures/
│       ├── snapshot_likofleks.json  # реальный snapshot Ликофлекс
│       └── mock_pallets.json
│
└── config/
    ├── weights.json                 # коэффициенты оптимизации (§16.4)
    └── settings.json                # общие настройки
```

---

## 🔌 API Integration

### Python → 1С (через `OneCClient`)

```python
# services/wms_optimizer/api/onec_client.py

import httpx
from typing import Dict, Any
import os

class OneCClient:
    """HTTP-клиент для взаимодействия с Лико_WMS_Сервер"""
    
    def __init__(self):
        self.base_url = os.getenv("ONEC_API_URL", "http://localhost/OKIL_ERP/hs/LikoRest/API")
        self.login = os.getenv("ONEC_LOGIN", "administrator")
        self.password = os.getenv("ONEC_PASSWORD")
        self.timeout = 300  # §6: timeLimitSeconds может быть до 600
    
    async def call_proc(self, proc_name: str, params: Dict[str, Any]) -> Dict:
        """Универсальный вызов ProcName в 1С"""
        
        payload = {"ProcName": proc_name, **params}
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.base_url,
                json=payload,
                auth=(self.login, self.password),
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            return response.json()
    
    async def export_snapshot(self, warehouse_guid: str):
        """
        Вызов WMS_ExportSnapshot — получение полного snapshot'а склада
        
        Возвращает:
        {
            "warehouse": {...},    # топология (стеллажи, секции)
            "occupancy": {...},    # текущее размещение паллет
            "versions": {          # версии секций для проверки §15
                "секция_guid": версия_число
            }
        }
        """
        result = await self.call_proc("WMS_ExportSnapshot", {
            "warehouse": warehouse_guid
        })
        
        if not result.get("ok"):
            raise Exception(f"1С error: {result.get('error')}")
        
        return result["data"]
    
    async def validate_placement(self, pallet_guid: str, cell_guid: str) -> bool:
        """
        Вызов WMS_ValidatePlacement — проверка физических ограничений §7
        """
        result = await self.call_proc("WMS_ValidatePlacement", {
            "pallet": pallet_guid,
            "cell": cell_guid
        })
        return result.get("ok", False)
    
    async def execute_plan(self, operations: list, affected_sections: list, versions: dict):
        """
        Вызов WMS_PlacePallets — пакетное исполнение плана §14
        
        operations: [
            {"pallet": guid, "newAddress": guid, "operation": "PUT|MOVE|KEEP"}
        ]
        affected_sections: [guid, guid, ...]
        versions: {"guid": version, ...}
        """
        result = await self.call_proc("WMS_PlacePallets", {
            "operations": operations,
            "affected_sections": affected_sections,
            "snapshot_versions": versions
        })
        
        if not result.get("ok"):
            error = result.get("error", {})
            if error.get("code") == "PLAN_INVALID":
                raise PlanInvalidError(error.get("message"))
            raise Exception(f"Execution failed: {error}")
        
        return result["data"]


class PlanInvalidError(Exception):
    """§15 — план устарел, секция изменилась"""
    pass
```

### FastAPI Routes

```python
# services/wms_optimizer/api/routes.py

from fastapi import FastAPI, HTTPException
from .schemas import OptimizationRequest, OptimizationResponse
from .onec_client import OneCClient, PlanInvalidError
from optimizer.global_optimizer import GlobalOptimizer

app = FastAPI(title="WMS Pallet Optimizer", version="1.0.0")
client = OneCClient()

@app.post("/optimize", response_model=OptimizationResponse)
async def optimize_placement(request: OptimizationRequest):
    """
    Синхронная оптимизация размещения паллет (§13.1)
    
    Шаги:
    1. Получить snapshot из 1С (WMS_ExportSnapshot)
    2. Построить модель склада
    3. Запустить CP-SAT solver (с лимитом времени)
    4. Вернуть план + метрики
    """
    
    # Шаг 1: получить snapshot
    snapshot = await client.export_snapshot(request.warehouse_guid)
    
    # Шаг 2-3: оптимизация
    optimizer = GlobalOptimizer(
        snapshot=snapshot,
        new_pallets=request.new_pallets,
        settings=request.settings
    )
    
    plan = optimizer.solve()
    
    # План содержит affected_sections и snapshot_versions для §15
    return OptimizationResponse(
        optimizationId=request.optimizationId,
        solverStatus=plan.solver_status,
        placementStatus=plan.placement_status,
        score=plan.score,
        executionTimeSeconds=plan.execution_time,
        result=plan.to_dict()
    )

@app.post("/optimize/{id}/execute")
async def execute_optimization(id: str, plan: dict):
    """
    Исполнение плана в 1С (§14)
    
    Вызывает WMS_PlacePallets с проверкой версий §15
    """
    
    try:
        result = await client.execute_plan(
            operations=plan["operations"],
            affected_sections=plan["affected_sections"],
            versions=plan["snapshot_versions"]
        )
        return {"ok": True, "result": result}
    
    except PlanInvalidError as e:
        raise HTTPException(status_code=409, detail=str(e))
```

---

## ✅ Чеклист реализации

### Фаза 1: Подготовка 1С (BSL)

- [ ] Добавить реквизит `Версия` (Число) в `Лико_СкладскиеСекции`
- [ ] Реализовать инкремент версии в `ПередЗаписью`
- [ ] Проверить наличие полей в `Лико_ТипоразмерыСкладскихСекций`:
  - [ ] `ТехнологическийЗазор` (gapWidth из ТЗ §7.1)
  - [ ] `МаксимальныйВесПодъема` (maxLiftWeight из §7.5)
- [ ] Расширить `WMS_ExportSnapshot()` — добавить `versions`
- [ ] Доработать `WMS_PlacePallets()` — проверка версий §15

### Фаза 2: Python Infrastructure

- [ ] Создать структуру `services/wms_optimizer/`
- [ ] Настроить `requirements.txt` (ortools, fastapi, pydantic)
- [ ] Реализовать `OneCClient` с методами:
  - [ ] `export_snapshot()`
  - [ ] `validate_placement()`
  - [ ] `execute_plan()`
- [ ] Настроить `.env` с параметрами подключения к 1С

### Фаза 3: Core Optimizer (§9)

- [ ] Реализовать `potential.py` (§8) — единая функция
- [ ] Реализовать `scoring.py` (§9.1, §9.2) с `config/weights.json`
- [ ] Реализовать `cp_sat_model.py` — построение модели OR-Tools
- [ ] Реализовать `warm_start.py` — First Fit Decreasing
- [ ] Реализовать `global_optimizer.py` — главный пайплайн §11
- [ ] Реализовать `section_optimizer.py` — выбор адресов §9.2

### Фаза 4: Integration & Testing

- [ ] Портировать правила из `ПодобратьЯчейку` → `podobrat_rules.py`
- [ ] Реализовать `validator.py` (§17.3) — INVALID_DATA проверки
- [ ] Создать fixture `snapshot_likofleks.json` (реальные данные)
- [ ] Написать приёмочные тесты §21 (10 сценариев)
- [ ] Интеграционный тест: 1С → Python → 1С (полный цикл)

### Фаза 5: Production Ready

- [ ] Настроить логирование (§18)
- [ ] Добавить асинхронный режим (§13.2)
- [ ] Методика настройки коэффициентов (§19)
- [ ] Методика оценки качества (§20)
- [ ] Документация API (Swagger)

---

## 📊 Тестовая конфигурация

**Склад Ликофлекс Высотный** (из топологии):
- **9 стеллажей** (R01–R09)
- **20 типоразмеров секций**
- **1403 секции** (не 1190 из примера ТЗ!)
- **4209 адресов** (1403 × 3)

⚠️ **Важно**: Код оптимизатора НЕ должен хардкодить эти цифры — всё читается из snapshot'а.

---

## 🔗 Связанные страницы вики

- [[Лико_WMS_Сервер]] — API модуль
- [[Лико_СкладскиеСекции]] — справочник секций
- [[Лико_ПодобратьЯчейку]] — текущая эвристика (портировать правила)
- [[Топология_Склада_Ликофлекс_Высотный]] — реальная топология
- [[wms_optimizer]] — страница Python-сервиса (создать после реализации)

---

## 📝 Следующий шаг

**Выбрать фазу:**

1. **Фаза 1** — подготовка 1С (добавить `Версия`, проверить поля)
2. **Фаза 2** — инфраструктура Python (структура проекта + OneCClient)
3. **MVP** — упрощённая эвристика без реслота (проверка интеграции)

**Рекомендация**: Начать с **Фазы 1** (1С) + fixture snapshot для Фазы 2.
