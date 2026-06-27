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
- Состояние: оба окружения (testing и prod) подняты — функции `watchdog`, таймер-триггеры и секреты
  заведены. Подробности по облаку, id и доступам — в разделе «Yandex Cloud» ниже.

## Yandex Cloud (инфраструктура и доступы)

**Два независимых окружения — это два разных облака/каталога.** В каждом своя функция, свой триггер и
свой сервисный аккаунт.

| | testing | prod |
|---|---|---|
| cloud-id | `b1g3ldebakcmu5fbee1n` | `b1g0ts9j4l30udlvif13` (folder `science-rag`) |
| folder-id | `b1g45gcej7fc0v27s3fn` | `b1g0gm5epaeepgfov6ni` |
| функция `watchdog` | `d4e64tvs1m5rvsr74gkt` | `d4ekb547cfbh1kpk4m0j` |
| триггер `watchdog-timer` | `a1s61m64pueq33h1a4ff` | `a1s55vaqltddlpn4nvqf` |
| SA `petergof-robot` | `ajeccs8j8b7igcjacd0r` | `ajerbgv6jbhvacbev1id` |

Соседние функции в testing-folder: `ocr-processing` (`d4e3u59k6ku61rb6l0dt`), `upload-file-to-rag-base`
(RAG) — на `python314`; watchdog на `python312`.

### Подводные камни (важно)

- В обоих облаках сервисный аккаунт называется **одинаково — `petergof-robot`**, но это **разные SA** с
  разными id. У каждого `editor` только в своём облаке. Testing-робот в prod-folder имеет лишь **чтение**
  (создавать функции/триггеры там не может — будет `PermissionDenied`).
- `editor` достаточно и для деплоя функции, и чтобы триггер вызывал функцию от имени робота — отдельные
  роли (`functions.invoker`, `iam.serviceAccounts.user`) выдавать не нужно.
- Триггер ссылается на робота по **id** (`--invoke-function-service-account-id`), потому что по имени SA
  резолвится только внутри своего folder.
- Раздавать роли (`add-access-binding`) сам робот не может — нужен владелец/`resource-manager.admin`.

### yc CLI профили

- `petergof-robot` — testing (активен по умолчанию).
- `wd-prod` — prod, создан из ключа prod-робота:
  ```bash
  yc config profile create wd-prod
  yc config set service-account-key ~/petergof-robot-key-prod.json
  yc config set folder-id b1g0gm5epaeepgfov6ni
  ```
- Команды для прода — с `--profile wd-prod`. После работы возвращай активным `petergof-robot`
  (`yc config profile activate petergof-robot`).

### Полезные команды

```bash
yc serverless function list --folder-id <folder> --profile <prof>
yc serverless function version list --function-name watchdog --folder-id <folder> --profile <prof>

# Обновить cron триггера — ТОЛЬКО по --id, флаг --new-cron-expression
# (имя как позиционный аргумент команда не принимает):
yc serverless trigger update timer --id <trigger-id> \
  --new-cron-expression '*/5 * * * ? *' --profile <prof>
```

- ⚠️ После `trigger update timer` триггер может уйти в `PAUSED` — проверь `yc serverless trigger get
  --id <trigger-id>` и при необходимости возобнови (`yc serverless trigger resume`).
- cron в YC — 6 полей (Quartz): `мин час день месяц ? год`. Раз в 5 минут: `*/5 * * * ? *`.

### GitHub-секреты репозитория Watchdog

- Секреты: `YC_SA_JSON_CREDENTIALS_TEST`/`_PROD` (авторизованные JSON-ключи соответствующих роботов),
  `YC_FOLDER_ID_TEST`/`_PROD`.
- Переменные (`vars.`): `API_BASE_URL_TEST`/`_PROD`.
- `CLOUD_FUNCTION_API_KEY` — **org-секрет** организации. Его visibility должен включать этот репозиторий,
  иначе в деплой он приедет пустым и YC упадёт с `Illegal value of environment variable`.

## Чего не делать

- Не превращай в долгоживущий процесс — это короткая функция по cron.
- Не добавляй ретраи/воскрешение мёртвых файлов без отдельной задачи (вне текущих рамок).
- Не хардкодь лимиты в `handler.py` — только через `STATUS_TIMEOUTS`.
