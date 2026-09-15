# ADR-0034: The QA verdict field contract — a three-way verdict and flags over flat string fields

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 8 puts a real QA Agent behind the existing QA port (`CLAUDE.md` queue 11). The port
is `ArtifactEvaluator.evaluate(content) -> EvaluationResult` (ADR-0018 §6), where
`EvaluationResult` is a **three-way enum** verdict (`PASSED` / `FLAGGED` / `FAILED`;
`EvaluationStatus`) plus `flags: tuple[str, ...]`. A real role produces its answer through the
structured LLM port, which returns only a flat `Mapping[str, str]` of opaque fields (ADR-0014 §2,
Variant B). No role so far has had to produce an enum-constrained field or a multi-value one, so
nothing yet says how the model's fields become a verdict.

Constraints this decision must not breach:

- **ADR-0014 Variant B.** The port knows field *names* only; it carries no type, no allowed values,
  and guarantees neither completeness nor exclusivity of keys. Changing that changes every provider
  adapter and every executor.
- **ADR-0008 minimal Schema.** A Schema is a set of required field names, and `validate` checks only
  presence and non-emptiness. Richer typing was explicitly deferred.
- **ADR-0018 fail closed.** An evaluator failure propagates and leaves the Evaluation `PENDING`;
  there is no default verdict, and turning a technical failure into a domain verdict was rejected.
- **The QA path records no Output.** `evaluate_artifact` / `record_verdict` never open a Task, never
  record an Output and never call `Schema.validate`; the Evaluation entity is the only record. So
  the Variant A chain (Schema decides → application invokes → Run persists) does not run here —
  something else has to judge the answer.
- **ADR-0032 rework.** On `CHANGES_REQUESTED` the latest QA flags are fed verbatim into the
  producer's rework input (`qa_flags`). Flags are therefore instructions a later model call acts on,
  not just decoration.

## Decision

### 1. Two fields: `verdict` and `flags`

A QA role's structured answer has exactly two contract fields, exposed as
`QA_VERDICT_FIELDS = ("verdict", "flags")` in `application/qa_evaluation.py`. The QA role's Schema
(subtask 11.2) declares exactly these as its `required_fields`, so the Composition Root projects them
as the generation shape the usual way (ADR-0014 §3). Other keys the model may return are ignored, as
`Schema.validate` ignores them.

### 2. `verdict` — one exact token

`verdict` is one of the three terminal `EvaluationStatus` values: `passed`, `flagged`, `failed`.
Leading/trailing whitespace and letter case are ignored (`" FLAGGED\n"` is `FLAGGED`); nothing else
is normalized. `pending`, a blank value, synonyms (`pass`, `ok`, `risk`), translations or any other
text are contract violations. The allowed tokens reach the model through the Prompt text (subtask
11.2), not through the port.

### 3. `flags` — a JSON array of non-blank strings

`flags` is the JSON text of an array whose items are non-blank strings, e.g.
`["unsupported claim: cures anxiety", "missing disclaimer"]`; no flags is `[]`. Items are kept
verbatim and in order — not trimmed, deduplicated or reordered. Anything else (an empty string,
invalid JSON, a non-array, a non-string or blank item) is a contract violation.

JSON is chosen because it is the only convention that is unambiguous for multi-line flags, has an
explicit empty value that is still a non-empty field (so the Schema's presence check holds), and is
already how the rework route serializes the same flags back to a model (ADR-0032).

### 4. Consistency: a risk verdict names its reason

`flagged` and `failed` require at least one flag; `passed` may carry flags as remarks, or none. A
risk verdict without a reason would reach the human with nothing to decide on and would give the
ADR-0032 rework call no instruction to act on.

### 5. The decoder is the sole judge on the QA path

`application/qa_evaluation.py` gains a pure, deterministic function
`decode_verdict(fields: Mapping[str, str]) -> EvaluationResult` and a technical error
`QaVerdictError` (an application error, not a `DomainError` — it is about the answer's form, not a
domain rule). Any QA evaluator built on the structured port (subtask 11.3) converts its completion
through this function and nothing else. It is the narrow, local realization of the "richer typing"
ADR-0008 deferred, placed at the only boundary that needs it today.

### 6. A malformed answer fails closed, never into a verdict

`QaVerdictError` is raised and propagates exactly like any evaluator failure under ADR-0018 §6: the
Evaluation stays `PENDING` and the approval gate stays shut. A malformed answer is **never** mapped
to `FAILED` or `FLAGGED` — ADR-0018 already rejected turning a technical failure into a domain
verdict. How the Content Director surfaces the propagated error in a real run (leave the Run in
`WAITING_QA` for `resume`, or route it to `FAILED` with a stable reason, as ADR-0033 did for an
`INVALID` Output) is decided with the wiring (subtask 11.4), where there is a real caller.

### 7. What does not change

The `LLMClient` port, `Schema`, `Evaluation`, `EvaluationResult`, `ArtifactEvaluator`, `Run` and the
snapshot format are unchanged. This ADR adds one function, one error and one constant.

## Deferred

- **QA call metrics.** `Run.record_analytics` attributes a call to a started Task (ADR-0020 §4-5),
  and the QA path opens no Task, while `ArtifactEvaluator.evaluate` returns no measurements. So a
  real QA call's cost cannot be recorded yet. This must be decided before or with subtask 11.3
  (e.g. extend the port's result, or attribute QA calls to something other than a Task) — not
  silently dropped.
- **Retry of a malformed answer** (a repair prompt quoting the violation), like ADR-0033's deferred
  retry of an `INVALID` Output.
- **Allowed values in the provider tool schema** (an `enum` in `emit_fields`). It would make the
  port type-aware, reversing Variant B; revisit only if prompt-level instruction proves unreliable.
- **A typed QA report** (defect codes, severity, repair target, score —
  `CONTENT_FACTORY_THOUGHTS.md` §8) stored as its own Output/Artifact beside the Evaluation. Needs
  its own ADR under the video/media QA work.

## Consequences

### Positive

- Subtasks 11.2 (Prompt + Schema) and 11.3 (`LLMArtifactEvaluator`) have one fixed target shape.
- The fail-closed gate stays intact end to end: no wording a model can produce opens it except the
  exact `passed` token.
- No port, Schema or domain change; the decoder is testable without any model.

### Negative / Trade-offs

- The QA contract has two parts in two places: field names in the QA Schema, value grammar in the
  decoder. Bounded: the names are pinned by one constant, the grammar by one function and its tests.
- A near-miss answer (`"pass"`, `flags: ""`) stalls the gate until someone retries, instead of being
  guessed. Intended: fail closed is cheaper than a wrong pass on health content.
- The model must write JSON inside a string field; a prompt must say so explicitly.

## Alternatives considered

- **Extend `Schema` with allowed values per field.** Rejected for now: changes the Schema aggregate
  contract (ADR-0008, `SCHEMA_SPEC.md`) for one consumer that doesn't even pass through
  `Schema.validate`; rule of three (`CLAUDE.md` conventions).
- **Newline-separated flags.** Rejected: a multi-line flag is ambiguous, and "no flags" would be an
  empty field that the Schema presence check treats as missing.
- **Numbered fields (`flag_1` … `flag_n`).** Rejected: a fixed maximum, and empty slots would again
  fail the presence check.
- **Map a malformed answer to `FAILED`.** Rejected: it invents a verdict the model did not give and
  contradicts ADR-0018's rejected alternative; `PENDING` is already fail closed.
- **Accept synonyms and translations of the verdict.** Rejected: an open list is a guessing
  parser; one exact grammar is testable and cheap for a model to follow.
- **Let risk verdicts carry no flags.** Rejected: the human and the ADR-0032 rework call would have
  nothing to act on (§4).

## References

- `PROJECT.md`: §4.9, §10, §12
- `ROADMAP.md`: Stage 8
- ADR-0008, ADR-0014 §2-3, ADR-0018 §6, ADR-0020, ADR-0032, ADR-0033
- `EVALUATION_SPEC.md` §8.1, `EVALUATION_ACCEPTANCE.md` §4.1 (`QVD`)
- `CONTENT_FACTORY_THOUGHTS.md` §8 (non-normative)
