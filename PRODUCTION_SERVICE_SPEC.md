# PRODUCTION_SERVICE_SPEC.md — HTTP-сервис производства и воркфлоу n8n

> **Место в иерархии документации (PROJECT.md, раздел 17).**
> `PROJECT.md` → `ARCHITECTURE.md` (§3.2, §3.3, §12) → `ROADMAP.md` (Этап 11) →
> `ADAPTER_SPEC.md` → **`PRODUCTION_SERVICE_SPEC.md`** → `ADR-0049` → Implementation.

**Версия документа:** 1.0
**Статус:** Принят
**Дата:** 2026-09-17

## 1. Назначение и граница

Точка входа Python Agent Service (ARCHITECTURE §3.3), через которую n8n запускает производство брифа
и опрос решений ревьюеров. Сервис — **транспорт**: он принимает запрос, ставит бриф в очередь и
вызывает переданные ему функции; правила производства (готовность брифа, решения, маршрутизация,
статусы) остаются в `BriefProduction` (ADR-0048) и Content Director.

Код — `infrastructure/production_service.py` (stdlib: `http.server`, `threading`, `hmac`, `json`);
сборка — `composition.build_production_service(environ, production)`; запуск — `factory_service.py`.

## 2. Настройки (`service_settings_from_env`)

| Переменная | Обязательна | По умолчанию | Правило |
|---|---|---|---|
| `OMEMO_SERVICE_TOKEN` | да | — | не короче 32 символов после обрезки пробелов |
| `OMEMO_SERVICE_HOST` | нет | `127.0.0.1` | непустая строка |
| `OMEMO_SERVICE_PORT` | нет | `8765` | целое `0…65535` (`0` — свободный порт) |

Нарушение → `ServiceConfigurationError` с именем переменной, без значения. Токена нет в `repr`
настроек, в логах и в ответах.

## 3. Маршруты

| Маршрут | Авторизация | Тело | Ответ |
|---|---|---|---|
| `GET /v1/health` | нет | — | `200 {"status": "ok"}` |
| `POST /v1/briefs` | Bearer | `{"brief_ref": "<ref>"}` | `202 {"brief_ref": ref, "queued": bool}` |
| `POST /v1/reviews/sweep` | Bearer | пусто или JSON-объект (игнорируется) | `202 {"waiting": [refs], "queued": [refs]}` |

- **Авторизация:** заголовок ровно `Authorization: Bearer <token>`, сравнение за постоянное время.
  Иначе `401 {"error": …}` с `WWW-Authenticate: Bearer`; тело не разбирается, в очередь ничего не
  попадает.
- **Тело POST:** тело без `Content-Length` (chunked) → `411`; нет тела — пустое; больше 16 KiB →
  `413`; не JSON или не объект → `400`. Для `/v1/briefs`: `brief_ref` отсутствует, не строка, пустой/пробельный, длиннее
  512 символов или с управляющими символами → `400`. Прочие поля игнорируются.
- **Sweep:** `waiting()` бросает исключение → `503 {"error": "the waiting briefs could not be
  listed"}`, в очередь ничего; иначе каждый бриф из `waiting` подаётся в очередь, `queued` — те,
  что действительно поставлены.
- Неизвестный путь → `404`; известный путь другим методом → `405` с `Allow`. Строка запроса
  (`?…`) не меняет маршрут. Все ответы — JSON (`application/json; charset=utf-8`), соединение
  закрывается. Журнал доступа — логгер модуля (`INFO`), не stderr.

## 4. Очередь и исполнитель

- Один фоновый поток-исполнитель вызывает `produce(brief_ref)` по одному брифу, в порядке подачи.
- `submit(brief_ref) -> bool`: бриф, **ожидающий** в очереди, повторно не ставится (`False`); бриф,
  чей вызов **уже выполняется**, ставится ещё раз (`True`).
- Исключение из `produce` → `ERROR` с трассировкой в логгер модуля; исполнитель продолжает.
- `wait_idle(timeout) -> bool` — очередь пуста и ничего не выполняется (для тестов и остановки).
- `stop()` — сначала закрыть очередь (ожидающие отброшены, новое не принимается), затем перестать
  принимать запросы и дождаться текущего вызова; повтор безвреден. `closed` — очередь закрыта.

## 5. Воркфлоу n8n (`n8n/`)

| Файл | Триггер | Запрос |
|---|---|---|
| `brief-ready.workflow.json` | `n8n-nodes-base.notionTrigger` v1, `event = pagedUpdatedInDatabase`, опрос каждую минуту | `POST …/v1/briefs`, тело `brief_ref = {{ $json.id }}` |
| `review-sweep.workflow.json` | `n8n-nodes-base.scheduleTrigger`, каждые 5 минут | `POST …/v1/reviews/sweep`, без тела |

Каждый воркфлоу — ровно триггер → один `n8n-nodes-base.httpRequest` (`method = POST`,
`authentication = genericCredentialType`, `genericAuthType = httpHeaderAuth`, учётка
`Concept factory service`). Никаких узлов кода, ветвления, преобразования данных или записи в
Notion/Google; ни токена, ни `Bearer` в JSON; экспорт неактивен (`active: false`). Готовность брифа
n8n **не** проверяет — это правило доски (ADR-0040). Настройка оператором — `n8n/README.md`.

## 6. Что вне спецификации

Параллельное производство нескольких брифов, устойчивая очередь, TLS (за reverse proxy оператора),
живой обмен n8n ↔ Notion — проверка оператора.
