# Watchdog

Небольшая **облачная функция Yandex Cloud**, запускающаяся **раз в минуту** по таймеру (cron-триггер)
и спасающая файлы, «застрявшие» в пайплайне загрузки.

## Зачем нужен

В системе Petergof RAG файл проходит несколько стадий, которыми управляют облачные функции:

```
загрузка → S3 → функция OCR → функция RAG
        uploaded → ocr_processing → ocr_done → rag_indexing → indexed
```

API хранит у каждого файла текущий `status` и `status_changed_at`, а фронтенд показывает их в реальном
времени по WebSocket. Проблема в том, что **если Yandex убивает функцию на полпути** (таймаут
выполнения, OOM, квоты, падение) — сбой никто не записывает обратно. Файл навсегда замирает, например,
на `ocr_processing`, и пользователь продолжает видеть «обрабатывается», хотя задача давно мертва.

Watchdog закрывает эту дыру. Раз в минуту он получает список всех не-терминальных файлов и помечает как
**`dead`** (с поясняющим `error_message`) те, что находятся в своём статусе дольше допустимого лимита.
Фронтенд после этого показывает корректное состояние.

## Как работает

1. `GET {API_BASE_URL}/files/service?status=...` — список всех не-терминальных файлов по всем
   организациям (аутентификация по сервисному ключу; watchdog — системный актор без JWT и контекста
   организации).
2. Для каждого файла сравнивает `now - status_changed_at` с лимитом для его статуса из
   [`src/config.py`](src/config.py) (`STATUS_TIMEOUTS`).
3. Для каждого файла сверх лимита: `PATCH {API_BASE_URL}/files/by-key/status` с телом
   `{"status": "dead", "error_message": "Watchdog: timeout in status '...' (Ns > Ms)"}`.

Сервис **без состояния** — у него нет своей БД, между запусками он ничего не хранит; всё состояние живёт
в API. Статус считается «терминальным» ровно тогда, когда его **нет** среди ключей `STATUS_TIMEOUTS`
(например `indexed`, `dead`, `failed`).

### Лимиты по статусам

| Статус            | Лимит  | Примечание                                   |
|-------------------|--------|----------------------------------------------|
| `pending_upload`  | 10 мин |                                              |
| `uploading`       | 15 мин |                                              |
| `uploaded`        | 3 мин  | Триггер OCR должен срабатывать почти мгновенно |
| `ocr_processing`  | 60 мин |                                              |
| `ocr_done`        | 3 мин  | Триггер RAG должен срабатывать почти мгновенно |
| `rag_indexing`    | 15 мин |                                              |

Меняйте `STATUS_TIMEOUTS`, чтобы подкрутить лимиты или добавить/убрать отслеживаемые статусы.

## Структура

```
src/handler.py        # точка входа handler.handler + чистая логика find_stuck_files()
src/config.py         # Settings (env) + STATUS_TIMEOUTS
src/requirements.txt  # рантайм-зависимости, которые ставит Yandex Cloud
tests/                # набор тестов pytest
.github/workflows/    # CI: тесты + деплой
```

## Конфигурация

| Переменная окружения     | Описание                                                       |
|--------------------------|----------------------------------------------------------------|
| `API_BASE_URL`           | Базовый URL API с префиксом версии, без слеша в конце           |
| `CLOUD_FUNCTION_API_KEY` | Сервисный ключ; должен совпадать с `CLOUD_FUNCTION_API_KEY` в API |

См. [`.env.example`](.env.example).

## Локальная разработка

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check
pytest
```

Тесты полностью офлайн — `find_stuck_files()` чистая, а HTTP-запросы подменяются через `requests-mock`,
поэтому ни переменные окружения, ни сеть не требуются.

## Деплой (CI/CD)

Test и prod живут в **разных облаках/каталогах** Yandex Cloud. Функция в обоих называется одинаково —
`watchdog`; различаются только креды и `folder-id`. Рантайм — `python312`
(соседние OCR/RAG-функции крутятся на `python314` — на совместимость с этим сервисом не влияет).
[`.github/workflows/ci.yml`](.github/workflows/ci.yml):

- **Любой push и PR** → джоба `test` (ruff + pytest).
- **Pull request** → деплой новой версии `watchdog` в **testing**-каталог.
- **Мерж в `main`** → деплой новой версии `watchdog` в **prod**-каталог.

Деплой выполняется экшеном [`yc-actions/yc-sls-function`](https://github.com/yc-actions/yc-sls-function).

### Окружения

| Окружение | Когда деплоится     | Креды в GitHub                                                              | Статус       |
|-----------|---------------------|----------------------------------------------------------------------------|--------------|
| testing   | при открытии PR     | `YC_SA_JSON_CREDENTIALS_TEST`, `YC_FOLDER_ID_TEST`, `vars.API_BASE_URL_TEST` | поднят       |
| prod      | при мерже в `main`  | `YC_SA_JSON_CREDENTIALS_PROD`, `YC_FOLDER_ID_PROD`, `vars.API_BASE_URL_PROD` | настраивается |

В каждом облаке функция `watchdog` и таймер-триггер `watchdog-timer` (раз в минуту) создаются один раз —
см. «Разовая настройка». Первый деплой кода в testing-функцию происходит при открытии любого PR.

### Требуемые настройки GitHub

Secrets:

| Секрет                          | Описание                                                              |
|---------------------------------|----------------------------------------------------------------------|
| `YC_SA_JSON_CREDENTIALS_TEST`   | JSON-ключ SA для testing-облака (роль `editor`/`functions.admin`)     |
| `YC_SA_JSON_CREDENTIALS_PROD`   | JSON-ключ SA для prod-облака                                          |
| `YC_FOLDER_ID_TEST`             | ID каталога testing                                                   |
| `YC_FOLDER_ID_PROD`             | ID каталога prod                                                      |
| `CLOUD_FUNCTION_API_KEY`        | Общий сервисный ключ (то же значение, что ожидает API)               |

Variables (`vars.`):

| Переменная           | Описание                          |
|----------------------|-----------------------------------|
| `API_BASE_URL_TEST`  | Базовый URL тест-API              |
| `API_BASE_URL_PROD`  | Базовый URL прод-API              |

## Разовая настройка в Yandex Cloud

CI публикует только новые **версии**. Сама функция и её таймер-триггер создаются один раз **в каждом
облаке** (testing и prod) через CLI `yc`. Используемый сервисный аккаунт должен иметь роль `editor`
(или как минимум `serverless.functions.admin`) в каталоге — этого достаточно и для деплоя, и для того,
чтобы триггер вызывал функцию от его имени.

```bash
# 1. Функция (CI-экшен также создаёт её при первом запуске, если её нет)
yc serverless function create --name watchdog --folder-id <folder-id>

# 2. Таймер-триггер — раз в минуту (cron в Yandex — 6 полей, со знаком '?').
#    invoke-...-service-account — существующий SA с правом вызывать функцию.
yc serverless trigger create timer \
  --name watchdog-timer \
  --cron-expression '* * * * ? *' \
  --invoke-function-name watchdog \
  --invoke-function-service-account-name <sa-name> \
  --folder-id <folder-id>

# 3. JSON-ключ этого SA для секрета YC_SA_JSON_CREDENTIALS_{TEST,PROD}
yc iam key create --service-account-name <sa-name> --output key.json
```

> После того как вставите содержимое `key.json` в GitHub-секрет — удалите файл (`rm key.json`): это
> приватный ключ.

## Контракт с API

Watchdog зависит от двух эндпоинтов API с авторизацией по сервисному ключу (заголовок `x-service-key`):

- `GET  /files/service?status=<s>&status=<s>...` → `{ "files": [ { system_key, status, status_changed_at, ... } ] }`
- `PATCH /files/by-key/status` → тело `{ "system_key", "status", "error_message" }`

## Границы ответственности

Watchdog только **помечает** мёртвые файлы — он не перезапускает, не воскрешает и не чистит хранилище.
Наблюдаемость — через логи функции в Yandex Cloud.
