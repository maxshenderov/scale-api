# WMS Pallet Optimizer — API Documentation

> Python-сервис оптимального размещения паллет на складе.  
> OR-Tools CP-SAT solver + FFD эвристика + Section Optimizer.  
> Порт: **8010** | Base URL: `http://localhost:8010`

---

## Оглавление

- [Запуск сервиса](#запуск-сервиса)
- [Endpoints](#endpoints)
  - [GET /health](#get-health)
  - [POST /api/optimize](#post-apioptimize) — синхронный режим
  - [POST /api/optimize/async](#post-apioptimizeasync) — асинхронный запуск
  - [GET /api/optimization/{id}](#get-apioptimizationid) — статус задачи
  - [GET /api/optimization/{id}/result](#get-apioptimizationidresult) — результат
- [Схемы данных](#схемы-данных)
- [Примеры — curl](#примеры--curl)
- [Примеры — 1С BSL](#примеры--1с-bsl)
- [Коды ошибок](#коды-ошибок)

---

## Запуск сервиса

```bash
# Docker (рекомендуется)
cd d:\project\OKIL
docker-compose up -d wms-optimizer

# Локально
cd d:\project\OKIL\services\wms_optimizer
python main.py
```

Проверка: `curl http://localhost:8010/health`

Swagger UI: http://localhost:8010/docs

---

## Endpoints

### GET /health

Проверка работоспособности.

**Response 200:**
```json
{"status": "ok"}
```

---

### POST /api/optimize

**Синхронная оптимизация.** Блокирует до завершения.

Для задач: < 500 паллет, timeLimitSeconds ≤ 120.

**Request:** Content-Type: application/json

**Response 200:** OptimizationResponse

**Response 422:** Ошибка валидации

**Response 500:** Внутренняя ошибка

---
