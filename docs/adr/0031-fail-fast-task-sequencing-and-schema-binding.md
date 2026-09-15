# ADR-0031: Fail-fast Task sequencing and authoritative Schema binding

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 7 drives real, potentially paid Agent calls through `ContentDirector`. Two defects in
that execution path violate the existing deterministic-control and boundary-contract principles:

1. `_run_steps` continues after a Task reaches `FAILED`, so independent-looking later requests are
   opened and executed without the failed step's Output.
2. Output finalization validates fields with the Schema selected for the Agent, but records the
   executor-reported `schema_ref`. The resulting Output can therefore claim a different contract
   from the one that actually produced its verdict.

Schema references are deliberately opaque (`ADR-0008`), and one historical production reference
does not use the otherwise common `@vN` spelling. Deriving or parsing a reference from `Schema`
would silently introduce a new naming contract.

## Decision

### 1. Sequential workflows stop at the first non-successful Task

`ContentDirector._run_steps` stops immediately after a Task is observed in any terminal state other
than `SUCCEEDED`. No later request is opened and no later executor is called. `_drive` then follows
the existing Run failure route and commits `FAILED`.

This rule also applies during resumption. If an old snapshot already contains later Tasks, they are
left untouched; the first earlier failed Task still stops new or resumed downstream work. We do not
invent `SKIPPED` Tasks: an unopened request is plan data, not Run state.

### 2. Composition binds an opaque reference to its Schema authority

The application uses an immutable `SchemaBinding(schema_ref, schema)`. The Composition Root creates
the binding from the Prompt's exact `schema_ref` and the Schema object resolved under that key, then
maps it to the Agent. Direct application callers must make the same association explicitly.

Output finalization validates with `binding.schema` and always records `binding.schema_ref`. An
executor's `ExecutionResult.schema_ref` remains part of the current structured-result completeness
contract, but it is not an authority and cannot select or label the validated Schema. A differing
self-report is normalized to the trusted binding rather than raised: the Output retains the truthful
contract while the already completed Task does not become a half-finalized exceptional state.

The `Run` and `Schema` domain contracts are unchanged.

## Consequences

### Positive

- A failed step cannot trigger paid or semantically invalid downstream work.
- Every recorded Output identifies exactly the Schema binding whose object issued its verdict.
- Existing opaque Schema reference spellings remain valid; no parser or naming migration is added.
- The decision is additive outside the domain aggregates and preserves storage snapshots.

### Negative / Trade-offs

- Downstream requests after a failure have no Task record. A future scheduler that needs explicit
  skip observability must introduce that policy and event vocabulary separately.
- The executor-reported `schema_ref` is retained for compatibility but is no longer authoritative;
  a later port revision may remove it.
- Application callers now pass a `SchemaBinding` instead of a bare `Schema` when validation is
  wired.

## Alternatives considered

- **Continue every step.** Rejected: a sequential workflow has no valid chained input after a
  failure and later calls may spend money pointlessly.
- **Create `SKIPPED` Tasks.** Rejected: `SKIPPED` orchestration semantics and events are not yet
  specified, and unopened work is sufficient for the current positional plan.
- **Parse or derive the reference from `(SchemaId, SchemaVersion)`.** Rejected: `schema_ref` is an
  opaque handle and current accepted handles do not all share one spelling.
- **Raise on an executor mismatch after success.** Rejected for this slice: it can leave an in-memory
  Task terminal without its Output. The trusted binding can label the Output correctly without
  accepting the self-report.

## References

- `PROJECT.md`: §4.2, §4.3, §9, §10
- `ARCHITECTURE.md`: §4, §10
- `ROADMAP.md`: Stage 7 / Milestone M2
- `ADR-0008` §7–§8, `ADR-0012`, `ADR-0013` §8, `ADR-0026`
- `CONTENT_FACTORY_THOUGHTS.md` §15.4 (non-normative defect report)
