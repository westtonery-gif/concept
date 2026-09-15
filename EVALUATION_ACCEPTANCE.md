# EVALUATION_ACCEPTANCE.md — Критерии приёмки Evaluation (QA) и гейта fail closed

> Приёмка реализации `EVALUATION_SPEC.md` (по `ADR-0018`). Каждый критерий фальсифицируем: при
> неверной реализации соответствующий тест падает. Идентификаторы используются в докстрингах
> тестов (`tests/test_evaluation.py`, `tests/test_qa_evaluation.py`, `tests/test_qa_agent.py`).
>
> **Статус:** Accepted. **Дата:** 2026-09-15.

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
| QAR-02 | Agent → Prompt → Schema | `qa_agent@v1` → `qa-agent` v1 → `qa-verdict@v1` → та же Schema; нет `skill_refs` и `tool_refs` |
| QAR-03 | Текст Prompt | System называет оба поля, три токена вердикта, `JSON` и `[]`; User template содержит `{input}` ровно один раз и оба поля |
| QAR-04 | `build_schema_map` для роли | `SchemaBinding("qa-verdict@v1", Schema роли)`; модель не вызывается |
| QAR-05 | Ответ `passed` с `[]` | проходит проверку наличия Schema и декодируется в `PASSED` |

## 5. Маршрутизация ContentDirector (ECD)

| ID | Сценарий | Ожидание |
|---|---|---|
| ECD-01 | QA `PASSED` | Run `COMPLETED`; финальный артефакт `CANDIDATE` с одной `PASSED` оценкой; промежуточные `DRAFT` и не оценены |
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
