# ANALYTICS_RECORD_SPEC.md — Спецификация сущности Analytics Record

> Технический контракт по `ADR-0020`. Подчинён `DOMAIN_MODEL.md` §2.15/§6/§11 и `PROJECT.md` §16
> (PROJECT §17: при конфликте побеждают документы). Приёмка — `ANALYTICS_RECORD_ACCEPTANCE.md`.
>
> **Статус:** Accepted. **Дата:** 2026-09-15.

---

## 1. Назначение и границы

- **Что это.** Analytics Record — неизменяемая (append-only) запись метрик **одного вызова**
  агента: поставщик и модель, входные/выходные токены, стоимость, время выполнения, число повторов
  шага.
- **Зачем.** Достоверная статистика по агентам/шагам/прогонам — фактологическая база цикла
  улучшения (PROJECT §16) и будущего Analytics Agent.
- **В scope.** Доменная сущность и её Value Objects, создание через Run, событие, ошибки.
- **Вне scope.** Сбор метрик в прикладном слое (порт LLM пока не отдаёт usage — ROADMAP Этап 14);
  агрегаты и отчёты; Analytics Adapter/хранилище; Analytics Agent; исход вызова; политика
  хранения. Инвариант «вызов без записи считается несделанным» **задокументирован, но пока не
  исполняется** — его обеспечит Этап 14.

## 2. Value Objects

| VO | Поля | Правила (иначе `InvalidAnalyticsRecordError`) |
|---|---|---|
| `TokenUsage` | `input_tokens: int`, `output_tokens: int` | целые ≥ 0; `bool` не принимается; производное `total_tokens` |
| `Cost` | `amount: Decimal`, `currency: str` | `Decimal`, конечный, ≥ 0 (`float` не принимается); `currency` непустой; валюты по умолчанию нет |
| `TimeRange` | `started_at: datetime`, `finished_at: datetime` | оба с часовым поясом; `finished_at ≥ started_at`; производное `duration` |

Замер делается **вне** домена: домен не читает часы и не вычисляет цену (тарифы — в конфигурации).

## 3. Сущность

| Атрибут | Тип | Откуда |
|---|---|---|
| `record_id` | `AnalyticsRecordId` (`str`: `<run_id>-analytics-<n>`) | Run |
| `run_id` | `str` | Run (владелец) |
| `task_id` | `str` | Run (переданная задача) |
| `agent_ref` | `str` | Run, **из Task** |
| `provider`, `model` | `str` (непустые) | вызывающий |
| `token_usage` | `TokenUsage` | вызывающий |
| `cost` | `Cost` | вызывающий |
| `time_range` | `TimeRange` | вызывающий |
| `retries` | `int` | Run, **из Task**: `attempt_count − 1` на момент записи |
| `prompt_ref` | `str \| None` (непустой, если задан) | вызывающий, необязателен |

- Неизменяема целиком (frozen dataclass) и выдаётся наружу напрямую, как `Output`.
- Состояние одно — `Recorded` — и неявно: запись либо есть, либо нет. Метка времени — это
  `time_range`; отдельного `recorded_at` нет.

## 4. Контракт через Run (единственный путь)

| Операция | Кто | Предусловия | Эффект |
|---|---|---|---|
| `record_analytics(task_id, *, provider, model, token_usage, cost, time_range, by, prompt_ref=None)` | Content Director | Task принадлежит Run (иначе `KeyError`) и запускалась (`attempt_count ≥ 1`) | новая запись; `AnalyticsRecordCaptured`; возвращается id |
| `analytics_records` / `analytics_record(id)` | чтение | — | записи в порядке фиксации |

- `AGENT` и `HUMAN_REVIEWER` записывать метрики не могут (`UnauthorizedActorError`).
- **Иных ограничений по состоянию нет:** можно записать неуспешный вызов, а также вызов после
  того, как Task или Run стали терминальными, — состоявшийся вызов есть факт.
- **Task → записи 1:N** без предела (одна попытка может делать несколько вызовов модели).
- Все проверки выполняются **до** изменения состояния: отказ ничего не записывает и не расходует
  id.
- Обновления и удаления нет (append-only). Машины состояний Run и Task не меняются.

## 5. События

`AnalyticsRecordCaptured(run_id, record_id, task_id, agent_ref)` — в единый журнал Run. Сами
метрики читаются из записи.

## 6. Ошибки

| Ошибка | База | Когда |
|---|---|---|
| `InvalidAnalyticsRecordError` | `AnalyticsDomainError` | неправдоподобное значение VO; пустые `provider`/`model`/`prompt_ref`; Task не запускалась |
| `UnauthorizedActorError` (Run) | `RunDomainError` | не Content Director |
| `KeyError` | — | Task не принадлежит Run |
| `dataclasses.FrozenInstanceError` | — | попытка изменить запись или VO |

`AnalyticsDomainError` наследует `DomainError` (ADR-0017).
