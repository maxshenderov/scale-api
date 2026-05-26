# Портативная структура Docker-сервисов для OKIL

**Версия:** 1.0  
**Дата:** 09.05.2026

---

## Проблема

Абсолютные пути (`d:/project/OKIL/qdrant_storage`) ломаются при:
- Клонировании на другой компьютер (диск `C:` вместо `D:`)
- Переносе проекта в другую папку
- Развёртывании на сервере (Linux-пути `/opt/okil/` вместо `d:\project\`)

## Решение: Docker Compose с относительными путями

Каждый сервис — своя папка в корне `okil/`, внутри свой `docker-compose.yml` с **относительными** путями.

```
d:/project/OKIL/
├── .gitignore
├── 1c/
├── plans/
├── services/
│   ├── qdrant_roo/              ← сервис 1
│   │   ├── docker-compose.yml
│   │   └── storage/             ← данные Qdrant (bind mount, .gitignore)
│   │       └── ...
│   ├── qdrant_orders/           ← сервис 2
│   │   ├── docker-compose.yml
│   │   └── storage/             ← данные заказов (bind mount, .gitignore)
│   │       └── ...
│   ├── ollama/                  ← сервис 3
│   │   └── docker-compose.yml   ← модели в named volume
│   └── docker-compose.yml       ← КОРНЕВОЙ: запускает все три сразу
```

### Почему папки хранения внутри папки сервиса

```
services/qdrant_orders/
├── docker-compose.yml
└── storage/          ← bind mount: ./storage:/qdrant/storage
```

- `./storage` — **относительный путь**, работает на любом диске
- Вся папка `qdrant_orders/` клонируется с GitLab → `docker compose up -d` → готово
- `storage/` в `.gitignore` — не пушим данные, только инфраструктуру

## docker-compose.yml файлы

### Корневой `services/docker-compose.yml`

```yaml
version: '3.8'

# Запускает все сервисы одной командой:
#   cd services && docker compose up -d

services:
  # Подключаем внешние compose-файлы
  # Каждый сервис определён в своей папке
  # Используем include (Docker Compose v2.20+)
```

### `services/qdrant_roo/docker-compose.yml`

```yaml
version: '3.8'

services:
  qdrant_roo:
    image: qdrant/qdrant:latest
    container_name: qdrant_roo
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      # ОТНОСИТЕЛЬНЫЙ путь — работает на любом диске
      - ./storage:/qdrant/storage
    restart: unless-stopped
```

### `services/qdrant_orders/docker-compose.yml`

```yaml
version: '3.8'

services:
  qdrant_orders:
    image: qdrant/qdrant:latest
    container_name: qdrant_orders
    ports:
      - "6335:6333"
      - "6336:6334"
    volumes:
      # ОТНОСИТЕЛЬНЫЙ путь
      - ./storage:/qdrant/storage
    restart: unless-stopped
```

### `services/ollama/docker-compose.yml`

```yaml
version: '3.8'

services:
  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    ports:
      - "11434:11434"
    volumes:
      # Named volume — не зависит от диска
      - ollama_data:/root/.ollama
    restart: unless-stopped
    environment:
      OLLAMA_KEEP_ALIVE: 24h
      OLLAMA_HOST: 0.0.0.0
    deploy:
      resources:
        limits:
          memory: 4G

volumes:
  ollama_data:
    name: liko_ollama_models
```

### Корневой `services/docker-compose.yml` (менеджер)

```yaml
version: '3.8'

# Точка входа: cd services && docker compose up -d

include:
  - path: qdrant_roo/docker-compose.yml
  - path: qdrant_orders/docker-compose.yml
  - path: ollama/docker-compose.yml
```

## .gitignore

```gitignore
# Данные сервисов (не пушить в GitLab)
services/qdrant_roo/storage/
services/qdrant_orders/storage/

# Но сами папки должны существовать (кладём .gitkeep)
```

## Как это работает при переносе

### Сценарий: клонирование на другой компьютер

```bash
# Компьютер 1 (диск D:)
cd d:\project\OKIL\services\qdrant_orders
docker compose up -d
# Данные в d:\project\OKIL\services\qdrant_orders\storage\

# Компьютер 2 (диск C:)
cd c:\users\ivan\OKIL\services\qdrant_orders
docker compose up -d
# Данные в c:\users\ivan\OKIL\services\qdrant_orders\storage\
```

Ничего менять не нужно — `./storage` сам разрешится в правильный путь.

### Сценарий: перенос индексов между компьютерами

```bash
# Экспорт с компьютера 1
xcopy d:\project\OKIL\services\qdrant_orders\storage d:\backup\qdrant_orders_storage /E /I

# Импорт на компьютер 2
xcopy d:\backup\qdrant_orders_storage c:\users\ivan\OKIL\services\qdrant_orders\storage /E /I
# После копирования: docker compose restart qdrant_orders
```

## ВАЖНО: миграция существующих данных Qdrant

Сейчас данные лежат в `d:/project/OKIL/qdrant_storage/` (в корне проекта).
После реструктуризации их нужно перенести:

```bash
# Остановить текущий Qdrant
docker stop qdrant

# Скопировать данные в новую папку
xcopy d:\project\OKIL\qdrant_storage d:\project\OKIL\services\qdrant_roo\storage /E /I

# Запустить новый контейнер
cd d:\project\OKIL\services\qdrant_roo
docker compose up -d

# Проверить
curl http://localhost:6333/collections/ws-5e70e849fd3d1c12
```

После успешной миграции старую папку `qdrant_storage/` в корне можно удалить и добавить в `.gitignore`.

## Итоговая структура

```
OKIL/
├── services/
│   ├── docker-compose.yml          ← запускает всё: docker compose up -d
│   ├── qdrant_roo/
│   │   ├── docker-compose.yml
│   │   ├── .gitkeep/storage.keep
│   │   └── storage/                ← .gitignore
│   ├── qdrant_orders/
│   │   ├── docker-compose.yml
│   │   ├── .gitkeep/storage.keep
│   │   └── storage/                ← .gitignore  
│   └── ollama/
│       └── docker-compose.yml      ← named volume, без локальной папки
├── 1c/
├── plans/
├── .gitignore
└── AGENTS.md
```

**Каждая папка сервиса — самостоятельный unit:** её можно скопировать, перенести, запустить где угодно.
