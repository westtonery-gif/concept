# ADR-0036: QA call metrics attributed to the Evaluation, and the `LLMArtifactEvaluator`

- **Status:** Accepted
- **Date:** 2026-09-16
- **Deciders:** Lead Architect / Domain Architect; maintainer (attribution choice, 2026-09-16)
- **Amends:** ADR-0020 (an Analytics Record's subject), ADR-0018 §6 (the QA port), ADR-0034
  (resolves its first "Deferred" item)

## Context

Subtask 11.3 puts a real model behind the QA port: an `ArtifactEvaluator` that calls the structured
`LLMClient` with the `qa-agent` Prompt and turns the answer into a verdict through
`decode_verdict` (ADR-0034/0035). Every model call must leave an Analytics Record: "an agent call
without a metrics record counts as not done" (`PROJECT.md` §16, `DOMAIN_MODEL.md` §6), and
ADR-0029 already records every completed provider turn of a Task.

The QA call has nowhere to go (ADR-0034 "Deferred"):

- `Run.record_analytics(task_id, …)` attributes a call to a **started Task** and derives `agent_ref`
  and `retries` from it (ADR-0020 §4-5). The QA path opens no Task: its answer is a verdict, not an
  Output, and it is never a Workflow step (ADR-0035 §4).
- `ArtifactEvaluator.evaluate(content) -> EvaluationResult` returns no measurements.
- `DOMAIN_MODEL.md` binds a record "to a concrete Task/Run/Agent" and owns it "through Task".

Three shapes were weighed with the maintainer (Alternatives). The maintainer chose to attribute a QA
call to the **Evaluation** it was made for.

## Decision

### 1. An Evaluation names the role that answers it

`Evaluation` gains an optional, write-once `evaluator_ref: str | None` — the `agent_ref` of the
evaluating role (`DOMAIN_MODEL.md` §2.13: "created by the QA Agent or another evaluating Agent").
`Run.open_evaluation(artifact_id, *, kind, by, evaluator_ref=None)` stores it; a blank value is
refused with the new `InvalidEvaluationError` (an `EvaluationDomainError`). `EvaluationView` carries
it. Existing callers that pass nothing get `None`, unchanged.

### 2. An Analytics Record has exactly one subject: a Task or an Evaluation

`AnalyticsRecord.task_id` becomes `str | None` and a new `evaluation_id: str | None = None` is
added; **exactly one** is set (else `InvalidAnalyticsRecordError`). `retries` becomes `int | None`:
an integer for a Task record, as before, and `None` for an Evaluation record. An Evaluation has no
attempt model: a restart re-calls a `PENDING` one (ADR-0026 §3) but the domain does not count it, and
"unknown is not zero" (ADR-0029 §1). `AnalyticsRecordCaptured` likewise carries `task_id: str | None`
and a new `evaluation_id: str | None = None`.

Run gains an **additive** operation, `record_analytics` is untouched:

```python
record_evaluation_analytics(evaluation_id, *, provider, model, token_usage, cost, time_range, by,
                            prompt_ref=None) -> AnalyticsRecordId
```

- Content Director only; the Evaluation must be owned (else `KeyError`).
- The Evaluation must name its `evaluator_ref` (else `InvalidAnalyticsRecordError`): attribution is
  still **derived, never asserted** (ADR-0020 §4) — `agent_ref` comes from the Evaluation.
- No state restriction, as for Task records: a call recorded on a decided Evaluation, or in a
  terminal Run, is still a call that happened.
- Validated before anything changes; a refusal records nothing and consumes no id. Emits
  `AnalyticsRecordCaptured` with `task_id=None` and the `evaluation_id`.

Restoration (RUN_RESTORE_SPEC 1.2 §4.3 p.3) checks the subject the same way: a Task record names an
owned Task and its `agent_ref` (unchanged); an Evaluation record names an owned Evaluation and its
`evaluator_ref`.

### 3. The QA port reports its calls, on success and on failure

In `application/qa_evaluation.py`:

- `ArtifactEvaluator` gains a read-only `evaluator_ref: str`, so the Content Director (and
  `evaluate_artifact`) opens each Evaluation with the role that will answer it.
- `EvaluationResult` gains `analytics: tuple[AnalyticsMeasurement, ...] = ()` — the same
  application type `ExecutionResult` uses (ADR-0029 §3), one per completed provider turn, in order.
- `MeasuredEvaluatorError(Exception)` carries `analytics` for a failure that happened **after** model
  calls were made. `QaVerdictError` becomes its subclass (constructed exactly as before by
  `decode_verdict`); the new `QaCallError` is the model call itself failing.
- `record_verdict` records every measurement through `Run.record_evaluation_analytics` **before**
  the verdict. On a `MeasuredEvaluatorError` it records the error's measurements and re-raises the
  **same** exception; any other exception propagates untouched. The Evaluation stays `PENDING` in
  both failure cases: fail closed is unchanged (ADR-0018 §6), the only catch is to keep a fact.
- The Content Director commits the Run before re-raising a `MeasuredEvaluatorError`, so the
  recorded calls are durable. How the Director otherwise surfaces a QA failure (stay in
  `WAITING_QA`, or route to `FAILED`) remains subtask 11.4's decision (ADR-0034 §6).

### 4. `LLMArtifactEvaluator`

`infrastructure/llm.py` gains `LLMArtifactEvaluator`, the QA analogue of `LLMTaskExecutor`:
configured with the `LLMClient`, the Prompt's `system_prompt` / `user_template`, the generation
shape `output_fields`, the exact `prompt_ref` (`<prompt_id>@v<version>`), the `evaluator_ref`, and an
optionally scoped `Toolbox` (empty for `qa_agent@v1`). Construction requires a shape that contains
`QA_VERDICT_FIELDS`, and a non-blank `prompt_ref` / `evaluator_ref`.

`evaluate(content)` renders `{input}` with the Artifact's content, calls `complete`, and decodes the
fields **only** through `decode_verdict`. Unlike `LLMTaskExecutor` it never turns a failure into a
result: an `LLMError` becomes `QaCallError` (chained `from` it) carrying the completed turns'
measurements; a `QaVerdictError` from the decoder is re-raised as a `QaVerdictError` with the call's
measurements. The application cannot import `LLMError` (infrastructure), so the translation is what
lets the failure's facts cross the port. Building it from the catalogue and wiring it into an
entrypoint is subtask 11.4.

### 5. Snapshot format 2

`AnalyticsRecord`, `AnalyticsRecordCaptured` and `EvaluationView` change shape, so the stored
document's `FORMAT_VERSION` becomes **2**. A format-1 document is refused with
`SnapshotFormatError`, as any unknown format already is; migrations stay deferred (ADR-0024). The
only store today is the git-ignored local `.omemo/runs.sqlite3`; a Run stored under format 1 has to
be started again.

### 6. What does not change

`LLMClient` and `LLMTaskExecutor`; `Run.record_analytics`, `open_task`, the state machines and the
approval gate; the verdict grammar and the `qa-agent` Prompt; the Director's routing of verdicts.

## Consequences

### Positive

- Every real QA call is recorded with its actual model, tokens, exact cost, latency and Prompt
  version — including a call whose answer was malformed or whose later turn failed.
- Attribution stays derived: a QA record cannot name a role other than the one its Evaluation names.
- The QA path keeps its shape: no Task, no Output, no Workflow step, no change to Director routing.

### Negative / Trade-offs

- `AnalyticsRecord` now has two subjects; a reader grouping by `task_id` must handle `None`.
- `retries` is `None` for QA records until Evaluations get an attempt model.
- Every `ArtifactEvaluator` must name an `evaluator_ref`, including test doubles.
- Format 2 makes existing local stored Runs unreadable.

## Deferred

- An attempt count on Evaluations (and so `retries` for QA records).
- Building `LLMArtifactEvaluator` from `qa_agent@v1` in the Composition Root and wiring it (11.4).
- Aggregation by role or by subject, and `AnalyticsSink` export (unchanged deferral).

## Alternatives considered

- **Run the QA call as its own Task.** Rejected by the maintainer: the domain and format would not
  change, but the Content Director matches Tasks to plan steps by position, takes the final
  candidate from the last Task and reworks by Task index. All of them would have to skip QA Tasks,
  and a QA Task contradicts ADR-0035 §4 (a verdict, not a Workflow step).
- **Return the measurements but do not record them yet.** Rejected by the maintainer: it leaves the
  `PROJECT.md` §16 invariant knowingly broken for every QA call.
- **Let the caller assert `agent_ref` on an Evaluation record.** Rejected: ADR-0020 §4 rejected
  asserted attribution; a record could then contradict the Evaluation it names.
- **Record `retries = 0` for QA calls.** Rejected: a resumed `PENDING` Evaluation is re-called, so
  zero would be a fabricated value.
- **Let `record_verdict` read measurements from `LLMError`.** Rejected: the application layer may
  not import infrastructure (`tests/test_adapter_contract.py`).

## References

- `PROJECT.md`: §10, §16, §17
- `DOMAIN_MODEL.md`: §2.13, §2.15, §5, §6 (amended)
- `ROADMAP.md`: Stage 8
- ADR-0018 §6, ADR-0020 §4-5, ADR-0024, ADR-0026 §3, ADR-0029, ADR-0034, ADR-0035
- `EVALUATION_SPEC.md` §2, §4, §8.3; `EVALUATION_ACCEPTANCE.md` §4.3 (`LAE`)
- `ANALYTICS_RECORD_SPEC.md` 1.2; `ANALYTICS_RECORD_ACCEPTANCE.md` 1.2 (`AEV`)
- `RUN_RESTORE_SPEC.md` 1.2 §4.3
