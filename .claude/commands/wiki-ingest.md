Сделай массовый ingest ВСЕГО проекта.

Обойди все BSL файлы в:
- 1s/ERP/Conf/ — всё что не исключено .claudeignore
- 1s/ERP/extensions/ → wiki/extensions/
- 1s/ERP/obrab/ → wiki/ai/

Куда класть:
- Лико_* из CommonModules → wiki/liko/
- Лико_* из Documents, Catalogs, Registers → wiki/liko/
- Типовые объекты → wiki/documents/ или wiki/modules/
- Расширения → wiki/extensions/
- Обработки → wiki/ai/

Правила:
- Каждый объект = одна страница
- После каждого объекта обновляй wiki/index.md и wiki/log.md
- Проставляй [[ссылки]] на уже созданные страницы
- Если сессия прервётся — скажу "продолжи",
  ты читаешь wiki/log.md и продолжаешь с того места
