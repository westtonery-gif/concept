# EVALUATION_ACCEPTANCE.md — Критерии приёмки Evaluation (QA) и гейта fail closed

> Приёмка реализации `EVALUATION_SPEC.md` (по `ADR-0018`). Каждый критерий фальсифицируем: при
> неверной реализации соответствующий тест падает. Идентификаторы используются в докстрингах
> тестов (`tests/test_evaluation.py`, `tests/test_qa_evaluation.py`, `tests/test_qa_agent.py`,
> `tests/test_qa_call_metrics.py`, `tests/test_qa_wiring.py`).
>
> **Статус:** Accepted. **Дата:** 2026-09-15. Дополнено `ADR-0036` (2026-09-16): EFL-07, §4.3.
> Дополнено `ADR-0038` (2026-09-17): §4.4.
> Дополнено `CLAUDE.md` задачей 12 (2026-09-17): `qa-agent` v2 — QAR-02, §4.4 (QWR-01).

---

## 0. Соглашения

- Всё — только через корень агрегата Run и снимки `*View`; прогоны детерминированы, без моков,
  сети и LLM. Оценщик QA — детерминированный фейк за портом `ArtifactEvaluator`.
- «Кандидат» — артефакт в `CANDIDATE`; «одобрение человеком» — `APPROVED` Human Review.

## 1. Счастливый путь (EHP)

| ID | Сценарий | Ожидание |
|---|---|---|
| EHP-01 | Открыть оценку кандидата | `PENDING`, верные `run_id`/`artifact_ref`/`kind`, `flags == ()`; журнал событий не растёт |
| EHP-02 | Записать `PASSED` | снимок `PASSED`; последнее событие `EvaluationCompleted` с `verdict`, `artifact_ref`, `evaluation_id` |
| EHP-03 | Записать `FLAGGED` / `FAILED` с флагами | вердикт и флаги сохранены в снимке и в событии |

## 2. Нарушения (EFL)

| ID | Сценарий | Ожидание |
|---|---|---|
| EFL-01 | Открыть оценку не Content Director | `UnauthorizedActorError` |
| EFL-02 | Записать вердикт `AGENT` / `HUMAN_REVIEWER` | `UnauthorizedActorError`; оценка остаётся `PENDING` |
| EFL-03 | Открыть оценку `DRAFT`-артефакта | `InvalidTransitionError` |
| EFL-04 | Второй вердикт | `InvalidEvaluationTransitionError`; первый вердикт не изменён |
| EFL-05 | `PENDING` как вердикт | `InvalidEvaluationTransitionError` |
| EFL-06 | Запись в неизменяемый атрибут | `ImmutableEvaluationAttributeError` |
| EFL-07 | Открыть оценку с пустым `evaluator_ref` | `InvalidEvaluationError`; оценка не открыта |

## 3. Гейт fail closed (EGT)

| ID | Сценарий | Ожидание |
|---|---|---|
| EGT-01 | Approve человека, оценок нет → одобрить | `ArtifactQaNotPassedError`; артефакт `CANDIDATE` |
| EGT-02 | Approve человека, оценка `PENDING` | `ArtifactQaNotPassedError` |
| EGT-03 | Approve человека, вердикт `FLAGGED` / `FAILED` | `ArtifactQaNotPassedError` |
| EGT-04 | Approve человека + `PASSED` | `APPROVED`, затем `PUBLISHED` |
| EGT-05 | Последняя оценка решает: `PASSED`→`FLAGGED` / `FLAGGED`→`PASSED` | запрещено / разрешено |
| EGT-06 | `PASSED` у другого артефакта | для данного — `ArtifactQaNotPassedError` |
| EGT-07 | `PASSED` без Approve человека | `ArtifactNotApprovedError` (гейт ADR-0007 не ослаблен) |
| EGT-08 | `CANDIDATE → REJECTED` без ревью и оценки | разрешено |

## 4. Прикладной слой (EAP)

| ID | Сценарий | Ожидание |
|---|---|---|
| EAP-01 | `evaluate_artifact` | оценщик получает содержимое артефакта; вердикт и флаги записаны |
| EAP-02 | Оценщик бросает исключение | исключение пробрасывается; оценка `PENDING`; одобрение запрещено |

### 4.1 Декодер вердикта (QVD, ADR-0034, `EVALUATION_SPEC.md` §8.1)

| ID | Сценарий | Ожидание |
|---|---|---|
| QVD-01 | `verdict` = `passed` / `flagged` / `failed`, в т.ч. с пробелами по краям и в верхнем регистре | соответствующий `EvaluationStatus` |
| QVD-02 | `flags` — JSON-массив строк | кортеж тех же строк как есть, в том же порядке; `[]` → `()` |
| QVD-03 | `passed` с замечаниями | принято, замечания сохранены |
| QVD-04 | Лишние ключи в ответе | игнорируются |
| QVD-05 | `verdict` пусто / `pending` / синоним / перевод / другой текст | `QaVerdictError` |
| QVD-06 | Нет поля `verdict` или `flags` | `QaVerdictError` |
| QVD-07 | `flags` — пустая строка, невалидный JSON, не массив, не строка или пустая строка внутри | `QaVerdictError` |
| QVD-08 | `flagged` / `failed` с `[]` | `QaVerdictError` |
| QVD-09 | Оценщик с неверным ответом за `evaluate_artifact` | `QaVerdictError` пробрасывается; оценка `PENDING`; одобрение запрещено |
| QVD-10 | `QA_VERDICT_FIELDS` | ровно `("verdict", "flags")` |

### 4.2 Определение QA-роли (QAR, ADR-0035, `EVALUATION_SPEC.md` §8.2)

| ID | Сценарий | Ожидание |
|---|---|---|
| QAR-01 | Schema роли | `ACTIVE`; `required_fields == QA_VERDICT_FIELDS`; единственная запись каталога `qa-verdict@v1` |
| QAR-02 | Agent → Prompt → Schema | `qa_agent@v1` → `qa-agent` v2 → `qa-verdict@v1` → та же Schema; нет `skill_refs` и `tool_refs` |
| QAR-03 | Текст Prompt | System называет оба поля, три токена вердикта, `JSON` и `[]`; User template содержит `{input}` ровно один раз и оба поля |
| QAR-04 | `build_schema_map` для роли | `SchemaBinding("qa-verdict@v1", Schema роли)`; модель не вызывается |
| QAR-05 | Ответ `passed` с `[]` | проходит проверку наличия Schema и декодируется в `PASSED` |

### 4.3 LLM-оценщик и метрики QA-вызова (LAE, ADR-0036, `EVALUATION_SPEC.md` §8.3)

Модель — детерминированный фейк порта `LLMClient` (без SDK и сети).

| ID | Сценарий | Ожидание |
|---|---|---|
| LAE-01 | `LLMArtifactEvaluator.evaluate` | System и User из Prompt (`{input}` = содержимое), поля = форма; вердикт и флаги из `decode_verdict`; одно измерение на provider-turn с `prompt_ref` |
| LAE-02 | `evaluate_artifact` с ним | Evaluation открыта с `evaluator_ref` оценщика; вызов записан на неё до вердикта (`AnalyticsRecordCaptured` раньше `EvaluationCompleted`) |
| LAE-03 | Неверный ответ модели | `QaVerdictError` с измерениями; вызов записан; оценка `PENDING`; одобрение запрещено |
| LAE-04 | `LLMError` после завершённых turn / без ответа провайдера | `QaCallError`, причина — та же `LLMError`; завершённые turn записаны, без ответа — записей нет; оценка `PENDING`; одобрение запрещено |
| LAE-05 | Конструирование | форма без `verdict`/`flags`, пустые `prompt_ref`/`evaluator_ref` → `ValueError` |
| LAE-06 | ContentDirector с хранилищем, QA падает после вызова | ошибка пробрасывается; сохранённый Run в `WAITING_QA`, оценка `PENDING` с `evaluator_ref`, запись вызова сохранена |
| LAE-07 | ContentDirector с `LLMArtifactEvaluator`: `passed` / `flagged` | `WAITING_HUMAN` с Review в обоих случаях; в обоих случаях ровно одна запись вызова QA на Evaluation с ролью и `prompt_ref` |

### 4.4 Подключение оценщика и исход ошибки QA (QWR, ADR-0038, `EVALUATION_SPEC.md` §8.4)

Модель — детерминированный фейк порта `LLMClient`; Prompt и Schema — реальные ассеты `qa_agent@v1`
(Prompt — v2, CLAUDE.md задача 12).

| ID | Сценарий | Ожидание |
|---|---|---|
| QWR-01 | `build_qa_evaluator(QA_AGENT, None, …)` | `evaluator_ref = qa_agent@v1`, `prompt_ref = qa-agent@v2`, `output_fields = QA_VERDICT_FIELDS`; System и User — из встроенного каталога; модель не вызвана |
| QWR-02 | Неизвестный Prompt / неизвестная Schema | `CompositionError` |
| QWR-03 | Agent с `skill_refs` | `CompositionError` |
| QWR-04 | Agent с `tool_refs` | модель видит ровно выданные Tools; недоступный Tool → `ToolGrantError` при сборке |
| QWR-05 | `compile_runtime(..., qa=)` | Run проходит QA-гейт на вердикте собранного оценщика и ждёт человека с Review (ADR-0044); оценка открыта с его `evaluator_ref`; QA-роль как шаг Workflow → `CompositionError` |
| QWR-06 | QA падает (неверный ответ / сбой вызова), затем `resume` с исправной моделью | после ошибки: сохранённый Run `WAITING_QA`, не `FAILED`, оценка `PENDING`; после `resume`: та же и единственная оценка решена, записи обоих вызовов на ней, маршрут по вердикту (при `passed` — `WAITING_HUMAN` с Review) |
| QWR-07 | Модель ставит `flagged`, человек `CHANGES_REQUESTED`, `resume` | producer получает флаги модели в `qa_flags`; новая версия оценена заново; при `passed` Run `WAITING_HUMAN` со свежим Review новой версии |

### 4.5 Approval Gate (APG, ADR-0044, `EVALUATION_SPEC.md` §9) — `tests/test_review_publication.py`

| ID | Сценарий | Ожидание |
|---|---|---|
| APG-01 | QA `PASSED` | Run `WAITING_HUMAN`; ровно один `PENDING` Review на кандидате (`CANDIDATE`); в store ровно один снимок `WAITING_HUMAN`, и в нём уже есть Review |
| APG-02 | затем `APPROVED` и `resume` | артефакт `APPROVED`, Run `COMPLETED` — ровно один новый коммит; исполнитель и оценщик не вызваны |
| APG-03 | `resume` при: `PASSED` + Review `PENDING`; `PASSED` + `REJECTED`; `FLAGGED`/`FAILED` + `APPROVED`; `FLAGGED` + `REJECTED` | снимок Run не изменён, коммитов нет, моделей нет |
| APG-04 | QA не подключён | Run `COMPLETED` без Review (прежний маршрут) |
| APG-05 | `FLAGGED` → `CHANGES_REQUESTED` → версия 2 `PASSED` → `APPROVED` → `resume` | версия 2 ждёт человека со свежим Review; после одобрения — `APPROVED`, Run `COMPLETED`, версия 1 `SUPERSEDED` |

## 5. Маршрутизация ContentDirector (ECD)

| ID | Сценарий | Ожидание |
|---|---|---|
| ECD-01 | QA `PASSED` | Run `WAITING_HUMAN` с `PENDING` Review кандидата; финальный артефакт `CANDIDATE` с одной `PASSED` оценкой; промежуточные `DRAFT` и не оценены; `APPROVED` + `resume` → артефакт `APPROVED`, Run `COMPLETED` без повторного вызова оценщика |
| ECD-02 | QA `FLAGGED` | Run `WAITING_HUMAN` (не `COMPLETED`); открыт `PENDING` Human Review кандидата; даже Approve человека не даёт одобрить артефакт |
| ECD-03 | QA `FAILED` | как ECD-02 |
| ECD-04 | QA подключён, артефакта нет | Run `FAILED` с причиной `QA gate: no candidate artifact to evaluate`; оценщик не вызван |
| ECD-05 | QA не подключён | поведение прежнее (существующие тесты ContentDirector зелёные) |

## 6. Иерархия ошибок (EDE)

| ID | Сценарий | Ожидание |
|---|---|---|
| EDE-01 | `EvaluationDomainError` | прямой наследник `DomainError` (`tests/test_domain_error.py`) |

## 7. Что приёмка НЕ проверяет

Качество реального QA-вердикта и содержание правил комплаенса (Этап 8; QAR проверяет только
согласованность Prompt и Schema, а не формулировки критериев); метку времени; оценку Output.
Доработку после риска с новой версией артефакта проверяет отдельный контракт
`REWORK_ROUTING_ACCEPTANCE.md` (ADR-0032).
