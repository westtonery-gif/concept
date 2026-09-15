# ANALYTICS_RECORD_ACCEPTANCE.md — Критерии приёмки Analytics Record

> Приёмка реализации `ANALYTICS_RECORD_SPEC.md` (по `ADR-0020`). Каждый критерий фальсифицируем:
> при неверной реализации соответствующий тест падает. Идентификаторы используются в докстрингах
> тестов (`tests/test_analytics_record.py`).
>
> **Версия:** 1.1. **Статус:** Accepted. **Дата:** 2026-09-15.
> Версия 1.1 добавляет приёмку сбора по `ADR-0029`.

---

## 0. Соглашения

- Всё — только через корень агрегата Run; без моков, сети, LLM и системных часов (время задаётся
  явно, с часовым поясом).
- «Запущенная задача» — Task, хотя бы раз вошедшая в `RUNNING` (`attempt_count ≥ 1`).

## 1. Счастливый путь (AHP)

| ID | Сценарий | Ожидание |
|---|---|---|
| AHP-01 | Записать метрики вызова запущенной задачи | запись с верными `run_id`/`task_id`, `agent_ref` из Task, переданными метриками, `retries == 0`, `prompt_ref is None`; последнее событие `AnalyticsRecordCaptured` с `record_id`/`task_id`/`agent_ref` |
| AHP-02 | Вызов после повтора задачи | `retries == 1`; ранее записанная запись сохранила `retries == 0` |
| AHP-03 | Несколько вызовов одной задачи | несколько записей с разными id в порядке фиксации |
| AHP-04 | Вызов упавшей задачи в упавшем Run | запись принимается |
| AHP-05 | Задан `prompt_ref` | сохранён в записи |
| AHP-06 | Производные значения | `token_usage.total_tokens`, `time_range.duration` верны |

## 2. Нарушения (AFL)

| ID | Сценарий | Ожидание |
|---|---|---|
| AFL-01 | Записать метрики `AGENT` / `HUMAN_REVIEWER` | `UnauthorizedActorError`; записей нет, журнал не вырос |
| AFL-02 | Задача не принадлежит Run | `KeyError` |
| AFL-03 | Задача не запускалась (`PENDING` / `SKIPPED`) | `InvalidAnalyticsRecordError`; записей нет |
| AFL-04 | Неправдоподобные значения VO: отрицательные/`bool` токены; отрицательная, `float`, `NaN`, бесконечная стоимость; пустая валюта; время без пояса; конец раньше начала | `InvalidAnalyticsRecordError` при создании VO |
| AFL-05 | Пустые `provider` / `model` / `prompt_ref` | `InvalidAnalyticsRecordError`; записей нет; следующий id не пропущен |
| AFL-06 | Изменить запись или VO | `FrozenInstanceError` |

## 3. Границы агрегата (AAG)

| ID | Сценарий | Ожидание |
|---|---|---|
| AAG-01 | Запись в одном Run | в другом Run не видна; её `run_id` и id принадлежат своему Run |
| AAG-02 | INV-07 (`tests/test_run.py`) | все дочерние сущности (Task, Output, Artifact, Human Review, Evaluation, Analytics Record) принадлежат ровно одному Run |

## 4. Иерархия ошибок (ADE)

| ID | Сценарий | Ожидание |
|---|---|---|
| ADE-01 | `AnalyticsDomainError` | прямой наследник `DomainError` (`tests/test_domain_error.py`) |

## 5. Сбор метрик (MTC, версия 1.1)

| ID | Сценарий | Ожидание |
|---|---|---|
| MTC-01 | Одно успешное обращение к Anthropic | результат содержит поля и одно измерение с `provider=anthropic`, фактической `Message.model`, usage ответа, exact cost и aware time range |
| MTC-02 | Tool-use loop из нескольких provider-turn | usage не схлопывается: одно измерение на каждый ответ, в исходном порядке |
| MTC-03 | Явный тариф | стоимость каждого turn равна формуле из двух `Decimal`-ставок за миллион; отрицательный/float/пустая валюта отвергаются |
| MTC-04 | Provider/model binding Anthropic без полного тарифа | `ProviderModelSelectionError` до возврата клиента; хардкода/нулевого fallback нет |
| MTC-05 | Composition Root собирает executor | инъецирует `prompt_ref=<prompt_id>@v<version>` без lookup в runtime |
| MTC-06 | Успешный `finish_task` | каждое измерение записано через Run до `SUCCEEDED`; Output/Artifact-путь не меняется |
| MTC-07 | Управляемая ошибка после завершённого provider-turn | измерения завершённых turn записаны, Task становится `FAILED`; turn без provider-response не фабрикуется |
| MTC-08 | Повтор Task | запись нового вызова получает `retries == attempt_count - 1`; предыдущие записи неизменны |
| MTC-09 | Fake provider | один результат с собственным provider/model, 0/0 tokens, exact zero cost и измеренным aware time range |

## 6. Что приёмка НЕ проверяет

Агрегаты/отчёты, экспорт в Analytics Sink и Analytics Agent; повторы на стороне поставщика;
исход вызова; метрика запроса без provider-response; провайдерские категории биллинга кэша.
