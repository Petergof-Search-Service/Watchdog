# CLAUDE.md — Watchdog

Заметки для будущих сессий Claude по этому сервису.

## Что это

Облачная функция **Yandex Cloud**, запускаемая раз в минуту по таймер-триггеру. Помечает «застрявшие»
файлы как `dead`, если они находятся в своём статусе дольше лимита. Подробности и контекст — в
[`README.md`](README.md).

## Главные факты

- **Точка входа:** `handler.handler` (`src/handler.py`). Сигнатура `handler(event, context)`; событие
  таймера не используется — функция просто опрашивает API.
- **Без состояния.** Своей БД нет. Всё состояние — в API. Не добавляй сюда работу с БД/S3.
- **Авторизация только по сервисному ключу** (`x-service-key`), никогда не JWT. Watchdog —
  системный актор без организации.
- **Источник истины по «терминальности»** — словарь `STATUS_TIMEOUTS` в `src/config.py`. Статус
  отслеживается ⇔ он есть в этом словаре. Терминальные (`indexed`, `dead`, `failed`) там отсутствуют.
- **Вся фильтрующая логика — в чистой функции** `find_stuck_files(files, now, timeouts)` в
  `src/handler.py`. Держи её без I/O — на ней строятся юнит-тесты.

## Зависимость от API (важно)

Сервис работает в паре с двумя эндпоинтами в репозитории `../API`
(`app/api/v1/endpoints/files.py`), оба под `require_service_key`:

- `GET /files/service?status=...` — список файлов по всем организациям, опциональный фильтр по статусам.
- `PATCH /files/by-key/status` — смена статуса по `system_key` (схема `ServiceStatusUpdate`).

Если меняешь формат запроса/ответа в watchdog — синхронизируй обе стороны. Эндпоинт `/files/service`
был добавлен специально для watchdog; обычный `GET /files` требует JWT и видит только одну организацию.

## Тесты

```bash
pip install -r requirements-dev.txt
ruff check
pytest
```

- `pythonpath=src` задан в `pyproject.toml`, поэтому `import handler` / `import config` работают.
- Тесты офлайн: HTTP мокается через `requests-mock`, текущее время фиксируется патчем `_now_utc`.
- `tests/conftest.py` выставляет env (`API_BASE_URL`, `CLOUD_FUNCTION_API_KEY`) до импорта `config`,
  т.к. `Settings()` читает их на этапе импорта.

## Деплой

- CI: `.github/workflows/ci.yml`. Test и prod — **разные облака/каталоги** YC; функция в обоих зовётся
  `watchdog`. PR → деплой в testing-каталог; мерж в `main` → деплой в prod-каталог.
- Различаются только секреты: `YC_SA_JSON_CREDENTIALS_{TEST,PROD}` + `YC_FOLDER_ID_{TEST,PROD}` +
  `vars.API_BASE_URL_{TEST,PROD}`. `CLOUD_FUNCTION_API_KEY` — общий.
- Экшен `yc-actions/yc-sls-function@v3`, `source-root: ./src`, рантайм `python312`.
- CI публикует только версии. Сами функции и таймер-триггеры создаются один раз вручную (см. README).
- Состояние: testing-облако уже поднято (функция `watchdog` + триггер раз/мин, секреты заведены).
  Prod ещё настраивается — пока prod-секретов нет, джоба `Deploy (prod)` на пуше в `main` падает с
  `No credentials`; это ожидаемо и не ломает `test`/`Deploy (test)`.

## Чего не делать

- Не превращай в долгоживущий процесс — это короткая функция по cron.
- Не добавляй ретраи/воскрешение мёртвых файлов без отдельной задачи (вне текущих рамок).
- Не хардкодь лимиты в `handler.py` — только через `STATUS_TIMEOUTS`.
