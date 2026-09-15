# ADR-0033: Invalid Output as an orchestration contract error, and the Milestone M2 acceptance

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 7 (Milestone M2) is closed by one real pass through `ContentDirector`, not by any of
its subtasks alone (`CLAUDE.md` queue 8.8). Its Definition of Done has four lines:

1. the Agent goes the whole way: input → reasoning → Structured Output → recorded in `Run`;
2. an invalid output is handled as a **contract error** (retries / an error state);
3. the prompt version and call metrics (model, tokens, cost, latency, retries) are recorded;
4. the prompt is stored separately from code and versioned.

Lines 1, 3 and 4 are each proven by a subtask (ADR-0026 … ADR-0032), but never together in one
run. Checking line 2 against the code found a gap. Under Variant A (ADR-0014 §8 I1/I2, ADR-0031)
`finish_task` records a successful call's Output with the verdict of `Schema.validate`, so an
`INVALID` Output is correctly **recorded**. What happens next was never decided, and the
`ContentDirector` treats it exactly like a valid one:

- it creates an Artifact from the `INVALID` Output (`Run.create_artifact` does not look at
  validity — ADR-0006 left that to the caller);
- it chains the `INVALID` payload into the next step's input, spending a further model call on it;
- the Run can reach `WAITING_QA` / `COMPLETED` with no human or QA ever told the contract broke;
- in the rework route an `INVALID` Output supersedes the reviewed candidate, although ADR-0032 §3
  says a version comes only from a *validated* Output (`REWORK_NO_OUTPUT_REASON` says so too).

No test covered any of this. That breaks fail-closed (`PROJECT.md` §4.9, §10, §12): a structural
contract violation passes silently as content.

## Decision

### 1. An `INVALID` Output stops the plan and fails the Run

The application-level rule, applied by the Content Director and nowhere else:

- The `INVALID` Output **stays recorded** on its `SUCCEEDED` Task. Variant A is unchanged: the call
  succeeded (`ExecutionResult.succeeded` is about the call, ADR-0014 I2), Schema gave its verdict,
  and Run keeps the fact for audit and analytics.
- It **never becomes an Artifact** — neither a first version (`create_artifact`) nor a successor
  (`create_artifact_version`).
- It is **never chained**: the sequential plan stops at that step exactly as it stops at a failed
  Task (ADR-0031); no downstream Task is opened and no downstream executor is called.
- The Run moves to `FAILED` with the stable reason `INVALID_OUTPUT_REASON`. In the rework route the
  existing `REWORK_NO_OUTPUT_REASON` applies unchanged and the reviewed candidate stays live.

No domain type changes. `Run.create_artifact` stays permissive; validity is an orchestration
decision, not an Artifact invariant, so existing domain contracts and snapshots are untouched.

### 2. Recoverable from Run state alone

No new commit point is added. The Task outcome with its `INVALID` Output is committed as one step
(ADR-0026 §2); the `FAILED` transition is the next commit. A Run restored between the two is seen
through the same rule on `resume`: the terminal Task is not called again, no Artifact is created,
and the Run is routed to `FAILED`.

### 3. Retry on `INVALID` is deferred, and the error state satisfies the DoD

The DoD accepts "retries / an error state". A retry is not possible without a domain change: the
Task is already `SUCCEEDED` (terminal) when Schema rules on its Output, because `Run.record_output`
requires a `SUCCEEDED` Task, and the executor must not re-request on its own (ADR-0014 I2). A
Director-level re-attempt needs a new Task/Run decision and belongs to a later ADR.

### 4. What closes M2

M2 is accepted by `tests/test_m2_acceptance.py` (`M2_ACCEPTANCE.md`, prefix `M2A`): one pass of the
`research-to-script@v1` Workflow (Rin → Leo) through `compile_runtime` with **only production
assets**:

- the bundled versioned Prompt store (`prompts=None`, ADR-0030);
- Rin's `normalize_terminology@v1` Skill invocation (ADR-0027);
- Rin's `current_date@v1` Tool, called mid-reasoning (ADR-0028);
- the real `AnthropicLLMClient`, whose per-turn metrics and pricing are recorded as Analytics
  Records (ADR-0029);
- the real `SqliteRunStore` at `OMEMO_RUN_STORE_PATH` (ADR-0024/0026).

The only substitute is the network transport below the Anthropic SDK: a scripted `messages.create`
endpoint returns real SDK `Message` objects. Everything from the Composition Root to the stored
database row is the production code path. The same pass with an `INVALID` model answer proves §1.

A live provider run is **not** a quality gate: CI has no key and a real call costs money. It is
the operator's check through `demo_factory.py`, which now also prints every captured call's
model, tokens, cost, latency, retries and prompt version.

## Deferred

- Re-attempting a step whose Output is `INVALID` (§3), including a repair prompt with the Schema
  verdict as feedback.
- Routing an `INVALID` Output to a human instead of `FAILED`.
- Recording *which* fields failed. Schema's verdict is logged through the validation observer
  today; it is not part of the Run.

## Consequences

### Positive

- A structural contract violation can no longer become content, feed the next role or complete a
  Run; the refusal is observable and stable (`INVALID_OUTPUT_REASON`).
- ADR-0032's "validated Output" wording is now what the code does.
- Stage 7 has one acceptance pass covering every DoD line at once.

### Negative / Trade-offs

- An `INVALID` answer fails the whole Run instead of being retried; until the deferred retry lands,
  one bad model answer costs a full re-run.
- The acceptance pass scripts the transport, so it cannot catch a provider-side change in the
  Messages API; only the operator's live run can.

## Alternatives considered

- **Refuse `INVALID` Outputs in `Run.create_artifact`.** Rejected: changes Run's existing behaviour
  (`CLAUDE.md` conventions) for what is a routing decision; the Director is where routing lives.
- **Mark the Task `FAILED` instead.** Rejected: impossible without a domain change (Output is
  recorded only on a `SUCCEEDED` Task) and it would conflate call failure with contract failure,
  the two axes ADR-0014 I2 keeps apart.
- **Stop recording `INVALID` Outputs.** Rejected: loses the audit fact and reverses Variant A.
- **Accept M2 on a live call inside the test suite.** Rejected: needs a secret and money in CI and
  makes the gate non-deterministic.

## References

- `PROJECT.md`: §4.9, §10, §12, §17
- `ROADMAP.md`: Stage 7 / Milestone M2
- ADR-0006, ADR-0014 §8, ADR-0024, ADR-0026, ADR-0027, ADR-0028, ADR-0029, ADR-0030, ADR-0031,
  ADR-0032
- `M2_ACCEPTANCE.md`; `tests/test_m2_acceptance.py`
