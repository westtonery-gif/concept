# PRODUCTION_SERVICE_ACCEPTANCE.md — Acceptance Criteria

**Версия:** 1.0
**Статус:** Принят
**Дата:** 2026-09-17
**Префиксы тестов:** `SVC` (`tests/test_production_service.py`), `N8N` (`tests/test_n8n_workflows.py`)
**Решение:** `docs/adr/0049-production-service-and-n8n-workflows.md`

Сервис проверяется через настоящий HTTP на `127.0.0.1` (порт `0`) клиентом `urllib`; `produce` и
`waiting` — записывающие двойники, которые можно задержать событием или заставить бросить. Сеть за
пределами `127.0.0.1` не используется.

## Сервис (SVC)

| ID | Критерий |
|---|---|
| SVC-01 | `GET /v1/health` без авторизации → `200 {"status": "ok"}`; `produce`/`waiting` не вызваны. |
| SVC-02 | `POST /v1/briefs` с токеном → `202 {"brief_ref", "queued": true}`; исполнитель вызывает `produce(ref)` ровно раз. |
| SVC-03 | Нет заголовка / другой токен / схема не `Bearer` / токен с лишним символом — для `/v1/briefs` и `/v1/reviews/sweep` → `401` с `WWW-Authenticate: Bearer`; ничего не вызвано; присланный токен не попал ни в ответ, ни в лог. |
| SVC-04 | Тело: не JSON; не объект; нет `brief_ref`; не строка; пустой/пробельный; длиннее 512; с управляющим символом → `400`; больше 16 KiB → `413`; chunked-тело без `Content-Length` → `411`; ничего не поставлено. |
| SVC-05 | Неизвестный путь → `404`; `GET /v1/briefs`, `POST /v1/health` → `405` с `Allow`; строка запроса не мешает маршруту. |
| SVC-06 | Пока `produce(A)` выполняется: A → `queued: true`, A ещё раз → `false`, B → `true`; после освобождения вызовы ровно `A, A, B`. |
| SVC-07 | `produce` бросает исключение → `ERROR` с трассировкой; следующий бриф выполняется. |
| SVC-08 | Sweep: `waiting` → `[A, B]`, A уже ждёт в очереди → `202 {"waiting": [A, B], "queued": [B]}`; `waiting` бросает → `503`, ничего не поставлено; пустое тело и `{}` принимаются. |
| SVC-09 | Настройки: всё задано; только токен (умолчания хоста/порта); токен отсутствует / короче 32; порт не число / вне диапазона → `ServiceConfigurationError` с именем переменной и без значения; токена нет в `repr`. |
| SVC-10 | `stop()`: запросы больше не принимаются, текущий вызов доводится до конца, ожидающие отброшены; повторный `stop()` безвреден. |
| SVC-11 | `build_production_service(environ, production)` — сервис над `BriefProduction.invoke` / `waiting_briefs`; без токена → `ServiceConfigurationError`. |

## Воркфлоу n8n (N8N)

| ID | Критерий |
|---|---|
| N8N-01 | В `n8n/` ровно два файла `*.workflow.json`; каждый — JSON-объект с `id` (разный у двух; без него `n8n import:workflow` падает на `NOT NULL constraint failed: workflow_entity.id`), `name`, `nodes`, `connections`, `active: false`. |
| N8N-02 | Каждый воркфлоу — ровно два узла: триггер разрешённого типа (`notionTrigger` / `scheduleTrigger`) и `httpRequest`; соединение ровно триггер → запрос. |
| N8N-03 | Запрос: `POST` на URL, чей путь — маршрут сервиса (`BRIEFS_ROUTE` / `SWEEP_ROUTE` из кода), авторизация — учётка Header Auth `Concept factory service`; в файле нет `Bearer`, `Authorization` и строк, похожих на токен. |
| N8N-04 | `brief-ready`: Notion Trigger `pagedUpdatedInDatabase`, опрос `everyMinute`; тело — ровно параметр `brief_ref = ={{ $json.id }}`. `review-sweep`: Schedule Trigger по минутам с интервалом 5; тела нет. |
| N8N-05 | `n8n/README.md` называет оба файла, учётку `Concept factory service` и переменную `OMEMO_SERVICE_TOKEN`. |

## Вне критериев

Живой n8n и живой Notion — проверка оператора (`n8n/README.md`); сквозной путь с производственными
активами — `STAGE11_ACCEPTANCE.md` (`S11A`).
