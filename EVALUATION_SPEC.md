# EVALUATION_SPEC.md — Спецификация сущности Evaluation (QA) и гейта fail closed

> Технический контракт по `ADR-0018`. Подчинён `DOMAIN_MODEL.md` §2.13/§6 и `PROJECT.md` §10/§12
> (PROJECT §17: при конфликте побеждают документы). Приёмка — `EVALUATION_ACCEPTANCE.md`.
>
> **Статус:** Accepted. **Дата:** 2026-09-15.
> Дополнено `ADR-0036` (2026-09-16): `evaluator_ref`, запись метрик вызовов оценщика,
> `LLMArtifactEvaluator` (§2, §4, §7, §8, §8.3).
> Дополнено `ADR-0038` (2026-09-17): подключение оценщика и исход ошибки QA (§8.4).
> Дополнено `CLAUDE.md` задачей 12 (2026-09-17): `qa-agent` v2, домен-нейтральная формулировка (§1, §8.2).

---

## 1. Назначение и границы

- **Что это.** Evaluation — структурированное суждение «пройдено / риск / отклонить» о
  артефакте-кандидате с перечнем замечаний/флагов. Частный случай — QA-вердикт по критериям
  уникальности, качества и правил клиента, работающий по принципу `fail closed`.
- **Зачем.** Сделать инвариант «при риске контент дальше не идёт» (DOMAIN_MODEL §6) исполняемым:
  без вердикта `PASSED` артефакт нельзя одобрить, а значит — опубликовать.
- **В scope.** Доменная сущность, её жизненный цикл через Run, гейт одобрения артефакта, порт
  QA-оценщика в прикладном слое, маршрутизация по вердикту в `ContentDirector`.
- **Вне scope этого документа.** Исполнение реального QA Agent и конкретные правила комплаенса
  (ROADMAP Этап 8; контракт полей его вердикта — §8.1, ADR-0034; определение роли — §8.2,
  ADR-0035); оценка Output; оценка уверенности; ссылка на критерии; метка времени.
  Доработка после эскалации риска и `CHANGES_REQUESTED` реализуется отдельным прикладным
  контрактом `REWORK_ROUTING_SPEC.md` / ADR-0032.

## 2. Сущность

| Атрибут | Тип | Изменяемость |
|---|---|---|
| `evaluation_id` | `EvaluationId` (`str`, генерирует Run: `<run_id>-evaluation-<n>`) | неизменяем |
| `run_id` | `str` (владеющий Run) | неизменяем |
| `artifact_ref` | `str` (id артефакта-кандидата) | неизменяем |
| `kind` | `str` (вид оценки, напр. `"qa"`) | неизменяем |
| `evaluator_ref` | `str \| None` (`agent_ref` отвечающей роли; ADR-0036) | неизменяем |
| `status` | `EvaluationStatus` (вердикт) | один раз: `PENDING` → терминальный |
| `flags` | `tuple[str, ...]` (замечания / флаги риска) | один раз, вместе с вердиктом |

Наружу выходит только неизменяемый снимок `EvaluationView` с теми же полями.

## 3. Жизненный цикл

```
PENDING ──► PASSED    (пройдено)
        ├─► FLAGGED   (риск)
        └─► FAILED    (отклонить)
```

- Создаётся `PENDING`; вердикт выносится **ровно один раз**; все три вердикта терминальны.
- `PENDING` не является вердиктом. Повторная оценка — **новая** Evaluation того же артефакта.

## 4. Контракт через Run (единственный путь)

| Операция | Кто | Предусловия | Эффект |
|---|---|---|---|
| `open_evaluation(artifact_id, *, kind, by, evaluator_ref=None)` | Content Director | артефакт `CANDIDATE`; `evaluator_ref`, если задан, непустой (иначе `InvalidEvaluationError`) | новая `PENDING` Evaluation; событие не порождается |
| `record_evaluation(evaluation_id, verdict, *, by, flags=())` | Content Director | Evaluation `PENDING`; `verdict` ∈ {PASSED, FLAGGED, FAILED} | вердикт и флаги фиксируются; `EvaluationCompleted` |
| `evaluations` / `evaluation(id)` | чтение | — | снимки `EvaluationView` |

Вердикт выносит оценщик вне домена; Run его только **сохраняет** (Variant A, ADR-0013 §8).
`AGENT` и `HUMAN_REVIEWER` не могут ни открыть, ни записать оценку.

Метрики вызовов оценщика записываются на Evaluation через `record_evaluation_analytics`
(`ANALYTICS_RECORD_SPEC.md` §4, ADR-0036); роль в записи выводится из `evaluator_ref`, поэтому
записать вызов можно только для Evaluation, у которой он задан.

## 5. Гейт fail closed

Переход артефакта `CANDIDATE → APPROVED` разрешён, только если **оба** условия выполнены:

1. есть `APPROVED` Human Review этого артефакта (ADR-0007; проверяется первым →
   `ArtifactNotApprovedError`);
2. **последняя** открытая Evaluation этого артефакта имеет вердикт `PASSED`
   (→ иначе `ArtifactQaNotPassedError`).

| Состояние QA артефакта | Одобрение |
|---|---|
| оценок нет | запрещено |
| последняя `PENDING` | запрещено |
| последняя `FLAGGED` / `FAILED` | запрещено (даже при Approve человека) |
| последняя `PASSED` | разрешено |

- Оценки других артефактов не учитываются.
- `CANDIDATE → REJECTED` не требует ни ревью, ни оценки.
- Машина состояний Run (`Run.transition`) не меняется.

## 6. События

`EvaluationCompleted(run_id, evaluation_id, artifact_ref, verdict, flags)` — в единый журнал Run.

## 7. Ошибки

| Ошибка | База | Когда |
|---|---|---|
| `InvalidEvaluationTransitionError` | `EvaluationDomainError` | повторный вердикт; `PENDING` как вердикт |
| `InvalidEvaluationError` | `EvaluationDomainError` | пустой `evaluator_ref` при открытии (ADR-0036) |
| `ImmutableEvaluationAttributeError` | `EvaluationDomainError` | запись в неизменяемый атрибут |
| `ArtifactQaNotPassedError` | `ArtifactDomainError` | одобрение без последнего `PASSED` |
| `InvalidTransitionError` (Run) | `RunDomainError` | оценка не-`CANDIDATE` артефакта |
| `UnauthorizedActorError` (Run) | `RunDomainError` | не Content Director |

`EvaluationDomainError` наследует `DomainError` (ADR-0017).

## 8. Прикладной слой — порт QA

`application/qa_evaluation.py`:

- `EvaluationResult(verdict: EvaluationStatus, flags: tuple[str, ...] = (), analytics=())` —
  `analytics`: по одному `AnalyticsMeasurement` на завершённый вызов модели, по порядку (ADR-0036);
- `ArtifactEvaluator` — протокол: свойство `evaluator_ref: str` (роль, которая отвечает) и
  `evaluate(content: str) -> EvaluationResult`;
- `evaluate_artifact(run, evaluator, artifact_id, *, kind="qa") -> EvaluationId` — открывает
  оценку с `evaluator_ref` оценщика, передаёт ему содержимое артефакта, записывает его вызовы и
  вердикт;
- `ContextualArtifactEvaluator` — **аддитивный** протокол-наследник (`ADR-0068` §1): тот же
  `ArtifactEvaluator` плюс `evaluate_in_context(content: str, context: str) -> EvaluationResult`;
  `evaluate_artifact_in_context(run, evaluator, artifact_id, context, *, kind="qa")` и
  `record_verdict_in_context` — те же пути, что без контекста, отличаются только тем, что оценщик
  получает контекст. Контекст **не** хранится в Evaluation: он выводится из Run.
  `LLMArtifactEvaluator` подставляет `{context}` рядом с `{input}` за один проход и отказывает
  (`ValueError`, до вызова модели) в двух несовпадениях: контекст для шаблона без `{context}` и
  вызов без контекста для шаблона с `{context}`.

Исключения оценщика **не перехватываются** (PROJECT §10): Evaluation остаётся `PENDING`, гейт
закрыт. Вердикта по умолчанию нет. Единственный перехват — `MeasuredEvaluatorError`
(`QaVerdictError`, `QaCallError`): сначала записываются измерения уже сделанных вызовов, затем
пробрасывается **то же** исключение (ADR-0036 §3).

### 8.1 Контракт полей вердикта (ADR-0034)

Оценщик на структурном LLM-порте (плоские строковые поля, ADR-0014) переводит ответ модели в
`EvaluationResult` только через `decode_verdict(fields) -> EvaluationResult` из
`application/qa_evaluation.py`:

| Поле | Грамматика | Нарушение |
|---|---|---|
| `verdict` | ровно `passed` / `flagged` / `failed`; пробелы по краям и регистр игнорируются | пусто, `pending`, синонимы, переводы, любой другой текст |
| `flags` | JSON-массив непустых (не из одних пробелов) строк; «нет флагов» = `[]`; элементы сохраняются как есть и в исходном порядке | пустая строка, невалидный JSON, не массив, не строка или пустая строка внутри |

- `QA_VERDICT_FIELDS = ("verdict", "flags")` — ровно `required_fields` Schema QA-роли; прочие
  ключи игнорируются.
- `flagged` / `failed` требуют хотя бы одного флага; `passed` может нести замечания или ничего.
- Любое нарушение → `QaVerdictError` (прикладная ошибка, не `DomainError`). Она пробрасывается как
  любая ошибка оценщика: Evaluation остаётся `PENDING`, гейт закрыт. Неверный ответ **никогда** не
  превращается в вердикт (ни в `FAILED`, ни в `FLAGGED`).
- Порт `LLMClient`, `Schema`, `Evaluation`, `ArtifactEvaluator` и `Run` не меняются (позднее
  ADR-0036 аддитивно дополнил `Evaluation`, `ArtifactEvaluator` и `Run` — §8.3).

### 8.2 Определение QA-роли (ADR-0035; критерии — v2, CLAUDE.md задача 12)

`agents/qa_agent.py` — статические данные роли по шаблону Rin/Leo:

| Элемент | Значение |
|---|---|
| Agent | `qa_agent@v1` → `prompt_ref = "qa-agent"`; `skill_refs = ()`, `tool_refs = ()` |
| Schema | `qa-verdict`, версия 1, `ACTIVE`; `required_fields = QA_VERDICT_FIELDS`; ссылка `qa-verdict@v1` |
| Prompt | `qa-agent` v2 во встроенном каталоге (ADR-0030), `schema_ref = "qa-verdict@v1"` |

- System Prompt содержит четыре критерия, взятых из уже принятого устава (`PROJECT.md` §1,
  `ARCHITECTURE.md` §3.9), без домена: уникальность (не шаблонный контент, не повтор материалов
  клиента или конкурентов); фактическая корректность; отсутствие необоснованных утверждений
  (обещания результата, гарантии, непроверяемые заявления); редакционные правила клиента из
  присланного контекста, а правила регулируемых ниш — только если их сообщает профиль клиента. Он
  явно учит грамматике §8.1 (три токена `verdict`, JSON-массив `flags`, `[]` без замечаний, флаг
  обязателен при риске) и склоняет к fail closed («сомневаешься — не `passed`»).
- v1 — health-домена и медицинских критериев — не редактировался: он остаётся в истории Git и в
  атрибуции уже записанных `AnalyticsRecord`; v2 — полная замена активной записи в каталоге
  (`PROMPT_STORE_SPEC.md` §1: ровно одна активная запись на `prompt_id`), не дополнение.
- Роль отвечает вердиктом, а не Output: она предназначена для порта `ArtifactEvaluator`, а не для
  шага Workflow. Оценщик на её основе — §8.3; подключение к точке входа — отдельная подзадача
  Этапа 8.

### 8.3 `LLMArtifactEvaluator` и метрики QA-вызова (ADR-0036)

`infrastructure/llm.py` — `LLMArtifactEvaluator(client, system_prompt, user_template,
output_fields, prompt_ref, evaluator_ref, toolbox=пустой)`:

- конструирование: `output_fields` содержит `QA_VERDICT_FIELDS`; `prompt_ref`
  (`<prompt_id>@v<version>`) и `evaluator_ref` непустые — иначе `ValueError`;
- `evaluate(content)`: `{input}` шаблона заменяется содержимым артефакта; один вызов
  `LLMClient.complete` с формой `output_fields`; поля превращаются в вердикт **только** через
  `decode_verdict`; результат несёт по одному измерению на завершённый provider-turn с `prompt_ref`;
- сбой никогда не становится вердиктом: `LLMError` → `QaCallError` (цепочка `from`), неверный
  ответ → `QaVerdictError`; оба несут измерения уже сделанных вызовов.

Запись (`record_verdict`): все измерения результата записываются через
`Run.record_evaluation_analytics` **до** вердикта; при `MeasuredEvaluatorError` записываются
измерения ошибки и пробрасывается то же исключение; Evaluation остаётся `PENDING`.
`ContentDirector` открывает оценку с `evaluator_ref=qa.evaluator_ref` и при
`MeasuredEvaluatorError` сохраняет Run в хранилище перед пробросом. Что Director делает с
ошибкой QA дальше, определяет §8.4.

### 8.4 Подключение оценщика и исход ошибки QA (ADR-0038)

**Ошибка QA.** Если оценщик бросает исключение, Director **не меняет** состояние Run: Run остаётся
`WAITING_QA`, Evaluation — `PENDING` со своим `evaluator_ref`. Вызовы из `MeasuredEvaluatorError`
записаны и сохранены, **то же** исключение пробрасывается вызывающему. Автоматического повтора и
причины отказа нет; Run не переводится в `FAILED`. `resume` снова вызывает оценщик для той же
Evaluation, без второй; первый полученный вердикт маршрутизируется по §9.

**Composition Root** (`composition.py`):

- `build_qa_evaluator(agent, prompts, client, schemas, *, available_tools=None) ->
  LLMArtifactEvaluator` — `agent.prompt_ref → Prompt → schema_ref → Schema` (неизвестные →
  `CompositionError`; `prompts=None` — встроенный каталог). Внедряются `system_prompt` /
  `user_template`, `output_fields = required_fields` Schema, `prompt_ref =
  <prompt_id>@v<version>`, `evaluator_ref = agent.agent_id`, `Toolbox` по `agent.tool_refs`.
  Непустые `skill_refs` → `CompositionError` (у пути QA нет места вызова Skill). Модель при сборке
  не вызывается;
- `validate_qa_evaluator(workflow, qa)` — `CompositionError`, если `agent_ref` какого-либо шага
  равен `qa.evaluator_ref` (QA-роль не шаг Workflow, ADR-0035 §4);
- `build_content_director(..., qa=None)` и `compile_runtime(..., qa=None)` передают оценщик в
  `ContentDirector`; `compile_runtime` вызывает `validate_qa_evaluator`.

**Точка входа** `demo_factory.py`: QA-роль получает свою привязку `client_for_role` с явными
ценами; `MeasuredEvaluatorError` перехватывается только здесь — демо сообщает, что Run остался в
`WAITING_QA`, и показывает сохранённый Run. `--request-changes "<указания>"` от имени
`HUMAN_REVIEWER` отправляет `CHANGES_REQUESTED` на ожидающий Review сохранённого Run и возобновляет
его (доработка ADR-0032 по флагам реальной модели); без сохранённого Run или ожидающего Review
ничего не меняет. `--approve` отправляет `APPROVED` на ожидающий Review и возобновляет Run (ADR-0044).

## 9. Маршрутизация в ContentDirector

`ContentDirector(executor, schemas=None, qa=None)`. Без `qa` поведение прежнее. С `qa`, после
успеха всех задач и перехода Run в `WAITING_QA`:

| Ситуация | Маршрут |
|---|---|
| финальный шаг не дал артефакта | Run → `FAILED`, причина `QA gate: no candidate artifact to evaluate` |
| вердикт `PASSED` | артефакт финального шага `CANDIDATE`; Run → `WAITING_HUMAN`, открыт `PENDING` Human Review кандидата **в том же коммите** (Approval Gate, ADR-0044 §1); Run **не** завершается без человека |
| вердикт `FLAGGED` / `FAILED` | Run → `WAITING_HUMAN`, открыт Human Review кандидата; Run **не** завершается |

Оценивается только артефакт финального шага; промежуточные остаются `DRAFT`.
Если человек отвечает `CHANGES_REQUESTED` или `REJECTED` (ADR-0045), дальнейший маршрут определён
`REWORK_ROUTING_SPEC.md`: повторно исполняется producer текущего кандидата, а его Output создаёт
новую версию Artifact. Сам QA-риск до решения человека по-прежнему только эскалируется.

**Approval Gate (ADR-0044 §1).** С `qa` (или в доработке) `resume` Run в `WAITING_HUMAN` смотрит
последний Review кандидата и последнюю QA-оценку кандидата:

| Последний Review | Последняя QA | Маршрут |
|---|---|---|
| нет (Run сохранён старым кодом) | любая | открыть `PENDING` Review, один коммит |
| `PENDING` | любая | ничего, коммита нет |
| `APPROVED` | `PASSED` | артефакт → `APPROVED`, Run → `COMPLETED`, **один** коммит |
| `APPROVED` | `FLAGGED` / `FAILED` | ничего (отложено, ADR-0044 §2) |
| `CHANGES_REQUESTED` | любая | доработка (`REWORK_ROUTING_SPEC.md`) |
| `REJECTED` | любая | доработка с причиной отказа (`REWORK_ROUTING_SPEC.md`, ADR-0045 §1) |

Модель ни в одной строке не вызывается. Без `qa` и вне доработки маршрут прежний:
`WAITING_HUMAN` → `COMPLETED` без Review.
