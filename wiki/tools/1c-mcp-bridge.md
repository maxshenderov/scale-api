# 1c-mcp-bridge

> Внешний Python MCP-сервер. Соединяет Claude Code с 1С:Предприятие через [[MCP_Расширение]]. Даёт три инструмента для чтения метаданных и выполнения BSL-кода.

## Как это работает

```
Claude Code
  → 1c-mcp-bridge (Python MCP server)
    → HTTP → [[MCP_Расширение]] (JSON-RPC 2.0 внутри 1С)
      → mcp_APIBackend (HTTPService)
        → mcp_Выполнение / mcp_Метаданные
```

Бридж транслирует MCP-вызовы Claude в JSON-RPC запросы к 1С. Включён в [.claude/settings.json](../../.claude/settings.json) как `enabledMcpjsonServers`.

## Инструменты

### 1. `list_metadata_objects` — список объектов метаданных

Получение списка объектов конфигурации с фильтрацией по типу и имени.

| Параметр | Тип | Описание |
|---|---|---|
| `metaType` | enum | Тип объекта метаданных (см. ниже) |
| `nameMask` | string | Маска имени — проверяется на вхождение подстроки в имя или синоним |
| `maxItems` | number | Максимальное количество результатов (по умолчанию 100) |

**Все доступные типы метаданных (metaType):**

```
Catalogs              — Справочники
Documents             — Документы
InformationRegisters  — Регистры сведений
AccumulationRegisters — Регистры накопления
AccountingRegisters   — Регистры бухгалтерии
CalculationRegisters  — Регистры расчёта
ChartsOfCharacteristicTypes — Планы видов характеристик
ChartsOfAccounts      — Планы счетов
ChartsOfCalculationTypes    — Планы видов расчёта
BusinessProcesses     — Бизнес-процессы
Tasks                 — Задачи
ExchangePlans         — Планы обмена
FilterCriteria        — Критерии отбора
Reports               — Отчёты
DataProcessors        — Обработки
Enums                 — Перечисления
CommonModules         — Общие модули
SessionParameters     — Параметры сеанса
CommonTemplates       — Общие макеты
CommonPictures        — Общие картинки
XDTOPackages          — XDTO-пакеты
WebServices           — Web-сервисы
HTTPServices          — HTTP-сервисы
WSReferences          — WS-ссылки
Styles                — Стили
Languages             — Языки
FunctionalOptions     — Функциональные опции
FunctionalOptionsParameters — Параметры функциональных опций
DefinedTypes          — Определяемые типы
CommonAttributes      — Общие реквизиты
CommonCommands        — Общие команды
CommandGroups         — Группы команд
Constants             — Константы
CommonForms           — Общие формы
Roles                 — Роли
Subsystems            — Подсистемы
EventSubscriptions    — Подписки на события
ScheduledJobs         — Регламентные задания
SettingsStorages      — Хранилища настроек
Sequences             — Последовательности
DocumentJournals      — Журналы документов
ExternalDataSources   — Внешние источники данных
Interfaces            — Интерфейсы
```

### 2. `get_metadata_structure` — структура объекта

Получение реквизитов, табличных частей, измерений и ресурсов конкретного объекта.

| Параметр | Тип | Описание |
|---|---|---|
| `metaType` | enum | Тип объекта (Catalogs, Documents, InformationRegisters, AccumulationRegisters, AccountingRegisters, CalculationRegisters, ChartsOfCharacteristicTypes, ChartsOfAccounts, ChartsOfCalculationTypes, BusinessProcesses, Tasks, ExchangePlans, Reports, DataProcessors) |
| `name` | string | Точное имя объекта (без учёта регистра) |

**Возвращает:**
- Синоним объекта
- Стандартные реквизиты (Ссылка, Код, Наименование, ПометкаУдаления и др.)
- Пользовательские реквизиты (имя — тип — синоним)
- Табличные части (для документов и справочников)
- Измерения и ресурсы (для регистров)

### 3. `execute_bsl` — выполнение BSL-кода

Выполнение произвольного BSL-кода на стороне 1С. **Основной инструмент для получения данных.**

| Параметр | Тип | По умолчанию | Описание |
|---|---|---|
| `code` | string | — | BSL-код для выполнения |
| `params` | object | — | Произвольные параметры (JSON), доступны как `MCP_Params` (Структура) |
| `transaction` | boolean | true | Выполнять в транзакции |
| `commit` | boolean | false | Фиксировать изменения (только при `transaction=true`) |
| `resultExpression` | string | — | BSL-выражение для результата (если `MCP_Result` не задан) |
| `returnJson` | boolean | true | Вернуть JSON `{ok, committed, result}` или строку |

**Контекст выполнения:**
- `MCP_Params` — Структура с переданными параметрами
- `MCP_Result` — присвой любое значение, чтобы вернуть результат

## Паттерны использования

### Найти все Лико_-объекты определённого типа

```
list_metadata_objects(metaType="Catalogs", nameMask="Лико", maxItems=50)
list_metadata_objects(metaType="Documents", nameMask="Лико", maxItems=50)
list_metadata_objects(metaType="CommonModules", nameMask="Лико_", maxItems=50)
```

### Получить структуру объекта перед чтением кода

```
get_metadata_structure(metaType="Documents", name="ЗаказКлиента")
```

### Прочитать данные из 1С

```bsl
// execute_bsl — безопасное чтение (transaction=true, commit=false по умолчанию)
Запрос = Новый Запрос;
Запрос.Текст = "ВЫБРАТЬ ПЕРВЫЕ 10 Ссылка, Номер, Дата ИЗ Документ.ЗаказКлиента";
MCP_Result = Запрос.Выполнить().Выгрузить();
```

### Запрос с параметрами

```bsl
// params: {"НомерЗаказа": "000000123"}
Заказ = Документы.ЗаказКлиента.НайтиПоНомеру(MCP_Params.НомерЗаказа);
MCP_Result = Новый Структура("Номер,Дата,Контрагент", 
    Заказ.Номер, Заказ.Дата, Строка(Заказ.Контрагент));
```

## Безопасность

- По умолчанию **всегда транзакция с `commit=false`** — изменения откатываются
- Для записи данных нужно явно указать `commit=true` и `transaction=true`
- Чтение данных через `execute_bsl` безопасно даже без транзакции (`transaction=false`)

## Связи

- [[MCP_Расширение]] — серверная часть внутри 1С
- [[Лико_ОбщегоНазначенияСервер]] — главный модуль, часто нужен в BSL-коде
- [[РасширениеЛико]] — все Лико_-объекты доступны через бридж
