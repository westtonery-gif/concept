# ADR-0020: Analytics Record entity — append-only per-call metrics owned by Run

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

> **Amended by ADR-0029 (2026-09-15).** ROADMAP Stage 7's explicit metrics Definition of Done
> brings the deferred port change, explicit pricing and `finish_task` capture forward from Stage
> 14. The domain entity and its invariants in this ADR are unchanged.

## Context

`Analytics Record` is the immutable record of the metrics of **one agent call** — model, input and
output tokens, cost, execution time, retries (`PROJECT.md` §16; `DOMAIN_MODEL.md` §2.15). It is the
factual base of the improvement loop: the Analytics Agent aggregates it and proposes optimisations
(`ARCHITECTURE.md` §3.10, §14). It is the last child entity of the `Run` aggregate root
(`DOMAIN_MODEL.md` §9.1) that is still missing — ADR-0003 §9 deferred it together with the others,
and `RUN_ACCEPTANCE.md` AGG-05 / INV-09 already name it.

The domain rules to realise:
- the record is **immutable / append-only** and bound to a concrete **Task / Run / Agent**
  (`DOMAIN_MODEL.md` §6);
- **Task → Analytics Record is 1:N** — several attempts or calls give several records (§5);
- its values are the Value Objects **Token Usage**, **Cost** and **Time Range** (§11);
- the event is **`AnalyticsRecordCaptured`** (§10);
- "an agent call without a metrics record counts as not done" (`PROJECT.md` §16, `DOMAIN_MODEL.md`
  §6).

`ROADMAP.md` Stage 14 is where metrics are collected "by default and unavoidably". That needs a
change to the LLM port, which today returns only the generated fields — no usage, no latency. This
ADR fixes the **domain shape** first, so Stage 14 becomes a port change plus wiring, not a domain
design under time pressure. `ARCHITECTURE_FREEZE.md` §3 "No analytics pipeline" stays in force: a
record is a fact inside the Run, not aggregation, storage or an agent.

## Decision

### 1. Placement
Analytics symbols live in `omemo_content_factory.domain.analytics`: `AnalyticsRecordId`, the Value
Objects `TokenUsage` / `Cost` / `TimeRange`, the entity `AnalyticsRecord`, events and errors. The
module depends only on stdlib and `domain.errors`; its references (`run_id`, `task_id`,
`agent_ref`) are opaque `str` (ADR-0003 §3). Records are created **only via Run**.

### 2. Value Objects — validated on construction
- `TokenUsage(input_tokens, output_tokens)` — non-negative `int` counts (a `bool` is refused);
  derived `total_tokens`.
- `Cost(amount, currency)` — `amount` is a finite, non-negative `Decimal`; a `float` is refused
  (thousands of records must add up exactly). `currency` is an opaque non-blank code (`"USD"`).
  There is no default currency, so no unit is hardcoded.
- `TimeRange(started_at, finished_at)` — timezone-aware `datetime`s with `finished_at >=
  started_at`; derived `duration`. Aware times keep records comparable across hosts and over time
  (`PROJECT.md` §16 "comparability").

An implausible value never reaches the Run: construction raises `InvalidAnalyticsRecordError`.
Measuring is done **outside** the domain. The domain never reads the clock, so it stays
deterministic, and never computes a price, which keeps it provider-agnostic: rates live in
configuration (`PROJECT.md` §5).

### 3. The entity
`AnalyticsRecord` is a frozen, slotted dataclass. It is immutable by construction, so Run exposes
it directly, as it does `Output` (ADR-0005), with no view class.

| Attribute | Source |
|---|---|
| `record_id` | Run: `<run_id>-analytics-<n>` |
| `run_id`, `task_id`, `agent_ref` | **derived by Run** from the owned Task (§4) |
| `provider`, `model` | caller: the provider and model actually used (non-blank `str`) |
| `token_usage`, `cost`, `time_range` | caller: the Value Objects of §2 |
| `retries` | **derived by Run**: the Task's `attempt_count - 1` at capture (§4) |
| `prompt_ref` | caller, optional: the Prompt version used (non-blank when given) |

- **State.** The only state, `Recorded`, is implicit: the record exists or it does not. A status
  field with one value would carry no information.
- **Timestamp.** The `TimeRange` *is* the record's time mark (start and finish of the call). There
  is no separate `recorded_at`, which would be a second time source that could disagree.

### 4. Attribution is derived, not asserted
The record states which Run, Task and role made the call, and how many retries preceded it. Run
takes all of that from the Task it owns instead of accepting it from the caller, so a record can
never contradict the Task it belongs to ("bound to a concrete Task/Run/Agent", `DOMAIN_MODEL.md`
§6). `retries` follows Task's existing retry model: a retry is a re-entry into `RUNNING` that
increments `attempt_count` (ADR-0004 §2). A call made during attempt *n* therefore records
*n − 1* retries, and the history of earlier attempts stays as it was captured.

### 5. Run's additive public API
- `record_analytics(task_id, *, provider, model, token_usage, cost, time_range, by,
  prompt_ref=None) -> AnalyticsRecordId`:
  - **Content Director only.** "The system during the Task's trace" (`DOMAIN_MODEL.md` §10) is not
    a domain `Actor`. The Content Director is the orchestrator that performs the call and the only
    actor that writes into a Run. An `AGENT` cannot self-report its own cost, and neither can a
    `HUMAN_REVIEWER`.
  - **The Task must be owned** (else `KeyError`, as for the other children) **and started**
    (`attempt_count >= 1`, else `InvalidAnalyticsRecordError`). A Task that never entered
    `RUNNING` made no call.
  - **No other state restriction.** A failed call, or one recorded after its Task or its Run
    reached a terminal state, is still a true fact that cost money. Refusing it would break "a call
    without a record counts as not done". Recording is an observation, not a state change, so the
    Run and Task state machines are untouched.
  - **Append-only.** There is no update or delete operation. A Task may have any number of
    records, because one attempt may make several model calls.
  - Every check runs **before** any state change. A refused call records nothing and consumes no
    id.
  - Emits `AnalyticsRecordCaptured`.
- Read-only: `analytics_records` (in capture order) and `analytics_record(record_id)`.

### 6. Events
`AnalyticsRecordCaptured(run_id, record_id, task_id, agent_ref)` goes into the Run's single event
log. The union is extended additively with `AnalyticsEvent`. The event names the fact; the metrics
are read from the record.

### 7. Errors
Both errors are co-located in `domain.analytics`: the base `AnalyticsDomainError(DomainError)`
(ADR-0017) and `InvalidAnalyticsRecordError`, which covers implausible values and an unstarted
Task. Immutability comes from the frozen dataclass (as for `Output`), so there is no dedicated
"immutable attribute" error. Authorisation reuses Run's `UnauthorizedActorError`.

### 8. What does not change
The following are unchanged, and no application or infrastructure code changes:
- Run's state machine, its existing operations and signatures, and the Artifact gates;
- `execute_task` and `ContentDirector`, which do not record analytics yet (Deferred).

This is consistent with ADR-0015, which lists the Analytics Record outside the restorable
execution-state truth.

## Deferred
- **Capturing records on every call, which leaves the §16 invariant unenforced for now.**
  `LLMClient.complete` returns only the generated fields, with no token usage or latency, and there
  is no pricing source. Enforcing "no call without a record" needs:
  - a port change that returns usage;
  - rates in configuration;
  - wiring in `execute_task`.

  All three are ROADMAP Stage 14, with its own ADR. Recording **fabricated** zero metrics now to
  satisfy the invariant was rejected (see Alternatives).
- Provider-side (HTTP/SDK) retries inside one call, as opposed to Task retries.
- Aggregation (per agent / content type / Run), the Analytics Adapter and storage, and the
  Analytics Agent (Stages 13/14; the freeze boundary still applies).
- Optional attributes: the call outcome and the model class. Retention and archiving policy.
- Run-level metrics (outcome, rework iterations, QA verdicts, human decisions) are already
  derivable from the Run's log and views. No separate record is introduced for them.

## Consequences

### Positive
- Every child entity of `DOMAIN_MODEL.md` §9.1 now exists and is reachable only through the Run
  root. The long-skipped aggregate-integrity check (INV-07) can finally be asserted.
- Stage 14 only has to measure and call one method. The shape, the validation and the attribution
  are settled, and records cannot contradict their Task.
- Money is exact (`Decimal`), times are comparable (aware), and the domain stays deterministic and
  provider-agnostic.

### Negative / Trade-offs
- The headline invariant, "no call without a record", is **documented but not yet enforced**. The
  entity exists before any code produces it.
- Run's public API and event union grow, additively.
- `retries` counts Task attempts only. Provider-side retries are invisible until Stage 14.

## Alternatives considered
- **The caller supplies `retries` / `agent_ref`.** Rejected: a record could then contradict its
  Task. Derivation makes that impossible.
- **Records outside Run** (in the observability log, or as their own aggregate). Rejected: this
  contradicts `DOMAIN_MODEL.md` §9.1 and AGG-05. The log-only observability layer is
  best-effort (`ARCHITECTURE_FREEZE.md` §2 p.9), while metrics must be reliable.
- **`float` cost, or latency as integer milliseconds.** Rejected: money must be exact, and §11
  models time as a Time Range.
- **Run stamps `recorded_at` from the system clock.** Rejected: it makes the domain
  nondeterministic, and the call's own time range is the meaningful mark.
- **Wire `execute_task` now with zero or unknown metrics.** Rejected: fabricated statistics are
  worse than missing ones, because §16 asks for *reliable* data.
- **Refuse records for terminal Tasks or Runs.** Rejected: a call that happened is a fact, and
  refusing it loses cost data exactly where failures happen.
- **A `status` enum with a single `RECORDED` value.** Rejected: it carries no information.

## References
- `DOMAIN_MODEL.md`: §2.15 (Analytics Record), §5 (Task → Analytics Record 1:N), §6 (invariants),
  §9.1, §10 (`AnalyticsRecordCaptured`), §11 (Token Usage, Cost, Time Range)
- `PROJECT.md`: §4 п.5, п.8, §5, §16, §17
- `ARCHITECTURE.md`: §3.10, §14; `ARCHITECTURE_FREEZE.md` §3
- `ROADMAP.md`: Stage 14
- ADR-0003 §9, ADR-0004 §2 (attempts), ADR-0005 (frozen child exposed directly), ADR-0015, ADR-0017
- `ANALYTICS_RECORD_SPEC.md`, `ANALYTICS_RECORD_ACCEPTANCE.md`
