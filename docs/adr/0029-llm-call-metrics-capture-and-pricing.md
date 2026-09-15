# ADR-0029: LLM call metrics capture and explicit token pricing

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect
- **Amends:** ADR-0014 / ADR-0028 (`LLMClient.complete` returns fields plus measurements)
- **Realises:** ADR-0020 §Deferred (capture and pricing), ROADMAP Stage 7 metrics slice

## Context

`AnalyticsRecord` already stores the provider, actual model, input/output tokens, exact cost,
time range, Task retry count and Prompt version for one model call (ADR-0020). Nothing produces
those records. `LLMClient.complete` returns only generated fields, `LLMTaskExecutor` consequently
drops provider usage, and `finish_task` has no measurements to give `Run.record_analytics`.

ROADMAP Stage 7 requires the first real Agent to record its Prompt version and all six metrics.
That earlier and more specific Definition of Done overrides ADR-0020's estimate that capture would
arrive only in Stage 14. Stage 14 still owns aggregation, reporting, provider-retry policy and
operational hardening; the factual per-call records have a consumer now and belong in Stage 7.

ADR-0028 also means one logical completion can contain several provider turns. Collapsing those
turns into one total would lose each turn's actual model and latency, while recording only the
final turn would undercount tokens and cost. The port therefore needs to return every completed
provider turn, not a single aggregate guessed by the application.

## Decision

### 1. The structured port returns generated fields and per-turn measurements

The provider-neutral result becomes conceptually:

```python
LLMCompletion(
    fields: Mapping[str, str],
    metrics: tuple[LLMCallMetrics, ...],
)
```

Each `LLMCallMetrics` is the observation of one **completed provider turn**:

- provider and the model reported by that response;
- provider-reported input and output token counts;
- an exact `Decimal` cost and its currency;
- aware start and finish times measured around that provider request.

The port still receives opaque field names and returns their values without interpreting Schema.
The new DTOs are provider-neutral infrastructure data, not domain entities and not an analytics
store. A bounded Tool loop returns one measurement per provider response, in request order. A
single-turn completion returns exactly one.

A request that raises before a provider response contains no reliable usage or actual model and
therefore creates no measurement. If a later turn fails, measurements from earlier completed turns
travel on `LLMError` and are still recorded. Logical adapter failures after a response (bad Tool
mix, missing Tool call, exhausted budget) likewise retain every completed turn. We do not invent
zero usage or cost for an unobserved response.

### 2. Pricing is explicit, exact and owned by provider/model configuration

`TokenPricing` carries non-negative `Decimal` input/output rates per one million tokens and a
non-blank currency. The Anthropic adapter receives it at construction and computes each response's
cost from the response's usage:

```text
(input_tokens × input_rate + output_tokens × output_rate) / 1_000_000
```

No model tariff is hardcoded. `client_for_role` reads the two rates and currency beside the
existing role-to-provider/model binding. An Anthropic role missing any pricing value fails closed
before a client is returned; malformed, negative or non-decimal rates do the same. The fake
provider truthfully reports its own implementation id, zero tokens and zero cost in USD because it
performs no model inference; its injected clock still measures the local call.

Prompt caching is not enabled by this adapter. If provider-specific cached-token pricing is added,
it requires an additive pricing contract before enabling the feature; silently pricing cache tokens
as ordinary input is not allowed.

### 3. Measurements cross the executor boundary as application data

`ExecutionResult` gains an ordered tuple of `AnalyticsMeasurement` values. `LLMTaskExecutor`
converts each provider-neutral metric into the existing domain Value Objects (`TokenUsage`, `Cost`,
`TimeRange`) and attaches the exact Prompt version injected by the Composition Root. Generated
fields remain structure-transparent exactly as ADR-0014 requires.

The Prompt version reference is `<prompt_id>@v<version>`. It is assembled once by the Composition
Root from the resolved immutable `Prompt`, then injected into the executor. Content Director still
does not resolve or read Agent/Prompt definitions.

On a managed LLM failure, any measurements carried by `LLMError` remain on the failed
`ExecutionResult`; failure routing must not discard facts about calls already made.

### 4. Task finalization records every reported call through Run

`finish_task` records every `ExecutionResult.analytics` item via `Run.record_analytics` **before**
transitioning the Task to `SUCCEEDED` or `FAILED`. This ordering records the current attempt number
and works for successful calls, structurally invalid outputs and managed failures. Non-LLM
`TaskExecutor`s may return the default empty tuple because they made no model call.

The Content Director's existing orchestration checkpoint remains the durability boundary: call
measurements, Task outcome, Output and Artifact are committed together after the executor step.
As ADR-0026 already states, a process crash after an external call but before that commit can cause
at-least-once re-execution. Eliminating that external-call/commit gap would require provider-side
idempotency and is not introduced here.

`AnalyticsSink` export, aggregation and reports remain separate concerns. The Run and its
`RunStore` snapshot are authoritative for this slice.

## Consequences

### Positive

- Every completed real model turn produces reliable, attributable Run analytics.
- Tool-use loops retain the cost and latency of all turns instead of hiding intermediate work.
- Prompt version, provider-reported model and Task retries are bound to the same immutable record.
- Exact pricing is configuration, so model/provider swaps require no application or domain change.
- Metrics survive managed failures and existing SQLite restoration without a new storage format.

### Negative / trade-offs

- `LLMClient.complete` and direct LLM test fakes have a breaking return-type change.
- Anthropic configuration now needs explicit pricing as well as provider/model values.
- A transport failure with no response has latency but no trustworthy usage/cost/model and cannot
  fit the current `AnalyticsRecord`; it remains represented by the Task failure, not a fabricated
  analytics record.
- SDK-internal HTTP retries remain invisible. `AnalyticsRecord.retries` continues to mean Task
  retries (`attempt_count - 1`) as ADR-0020 defines.

## Deferred

- Aggregates, dashboards, export through `AnalyticsSink` and Analytics Agent consumption.
- Provider-side retry counts and policy, failure outcome on `AnalyticsRecord`, cached-token and
  other provider-specific billing categories.
- Closing the external-call/RunStore commit gap with provider idempotency.
- Persisting Tool-call arguments/results as a trace; this slice measures provider turns, not Tool
  payloads.

## Alternatives considered

- **One aggregate measurement per `complete`.** Rejected: a Tool loop would hide per-turn latency,
  actual-model identity and cost boundaries.
- **Let `finish_task` time and price the call.** Rejected: the application does not see provider
  usage or individual turns and must not know provider billing rules.
- **Read metrics from mutable `client.last_usage`.** Rejected: it is a race-prone side channel and
  lets a later call overwrite the evidence before recording.
- **Hardcode known Anthropic prices.** Rejected: prices and model ids change independently of code;
  PROJECT §5 requires configuration-driven selection.
- **Record zero metrics for requests without a response.** Rejected: ADR-0020 explicitly rejects
  fabricated statistics; unknown is not zero.
- **Export directly from the LLM adapter.** Rejected: it would bypass the Run aggregate and split
  authoritative truth across two writes.

## References

- `PROJECT.md` §5, §6, §16, §17
- `ARCHITECTURE.md` §9, §14
- `ROADMAP.md` Stage 7 / Milestone M2 and Stage 14
- ADR-0014, ADR-0016, ADR-0020, ADR-0026, ADR-0028
- `ANALYTICS_RECORD_SPEC.md`, `ANALYTICS_RECORD_ACCEPTANCE.md`
