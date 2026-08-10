# scale_api

> HTTP-сервис для чтения показаний весов СКУ I2121 (СКИ-12/Yaohua) через M2M WiFi-UART модуль. Порт 8011.

## Назначение

Принимает GET-запросы от 1С, подключается к M2M WiFi-модулю весов по TCP, читает одно показание в формате Yaohua и возвращает JSON.

## Endpoints

| Endpoint | Метод | Назначение |
|---|---|---|
| `/health` | GET | Проверка работоспособности |
| `/api/weight` | GET | Прочитать текущее показание весов |

### GET /api/weight

Ответ:
```json
{
  "ok": true,
  "value": 5.0,
  "unit": "kg",
  "stable": true,
  "mode": "n",
  "raw": "wn00005.0kg"
}
```

Поля:
- `value` — число, вес
- `unit` — `"kg"`
- `stable` — `true` если вес стабилизировался
- `mode` — `"n"` (нетто) или `"g"` (брутто)

## Запуск

```bash
cd services/scale_api
docker compose up -d
```

Переменные окружения: `SCALE_HOST` (192.168.12.147), `SCALE_PORT` (8899), `SERVER_PORT` (8011).

## 1С — как забирать данные

```bsl
Соединение = Новый HTTPСоединение("<хост>", 8011);
Запрос = Новый HTTPЗапрос("/api/weight");
Ответ = Соединение.Получить(Запрос);
Чтение = Новый ЧтениеJSON;
Чтение.УстановитьПоток(Ответ.ПолучитьТелоКакПоток());
Данные = ПрочитатьJSON(Чтение);
// Данные.value, Данные.unit, Данные.stable
```

## Настройки M2M модуля

| Параметр | Значение |
|---|---|
| IP | 192.168.12.147 |
| Data Transfer Mode | Transparent (0) |
| 485 mode | ON |
| Baudrate | 9600, 8N1 |
| Network | Server, TCP, Port 8899 |

## Связи

- [[wms_optimizer]] — стиль сервиса (FastAPI + Docker)
- GitHub: https://github.com/maxshenderov/scale-api
