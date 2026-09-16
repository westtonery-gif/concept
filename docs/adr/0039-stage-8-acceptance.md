# ADR-0039: The ROADMAP Stage 8 (QA Agent) acceptance

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect
- **Closes:** ROADMAP Этап 8 (`CLAUDE.md` queue 11.5); ADR-0038 "Deferred" (Stage 8 acceptance
  through `compile_runtime` with production assets)

## Context

Stage 8 was built in four subtasks — the verdict contract (ADR-0034), the role (ADR-0035), the
model-backed evaluator with its metrics (ADR-0036) and its wiring (ADR-0038). Each is proven on its
own, mostly with fakes of the `LLMClient` port (`QVD`, `QAR`, `LAE`, `QWR`). The Stage's Definition
of Done (ROADMAP.md) has two lines:

1. QA produces a structured verdict; on a risk the system does not let the content through;
2. the orchestrator routes correctly by the QA verdict; covered by tests.

As with Milestone M2 (ADR-0033 §4), no subtask proves both lines together on the production code
path: the three catalogued roles, the real provider client for each side of the gate, the real
store, and restarts between the steps a human takes.

## Decision

### 1. Stage 8 is accepted by one production-asset pass

`tests/test_stage8_acceptance.py` (`STAGE8_ACCEPTANCE.md`, prefix `S8A`) runs
`research-to-script@v1` (Rin → Leo) with the `qa_agent@v1` gate, compiled by
`compile_runtime(..., qa=build_qa_evaluator(qa_agent.QA_AGENT, None, …))` from:

- the bundled Prompt store for all three roles (`prompts=None`, ADR-0030);
- Rin's Skill and Tool (ADR-0027/0028);
- two real `AnthropicLLMClient`s — one for the producers, one for the QA role with its own prices —
  as an entrypoint binds them per role (ADR-0016, ADR-0038 §4);
- the real `SqliteRunStore` (ADR-0024/0026).

The only substitute is the network below the SDK, exactly as in M2A. Every restart is a new
Composition Root over the same SQLite file, and the human reviewer's decision is committed to that
file before the restart, as `demo_factory.py --request-changes` does.

The pass covers: `passed` → `COMPLETED` (S8A-01..03); `flagged` / `failed` → escalation that even a
human Approve cannot open (S8A-04); a model flag → `CHANGES_REQUESTED` → rework of only the final
producer → the new version judged again → approvable (S8A-05); a malformed verdict → parked at
`WAITING_QA` → a restart asks again (S8A-06); a completed Run resumes without a call (S8A-07).

### 2. No behaviour change

Unlike ADR-0033, the acceptance found no gap against the DoD: every criterion held on the existing
code. The acceptance was checked to be falsifiable by letting the gate treat every verdict as a pass
(S8A-04 and S8A-05 fail). No production module changes.

### 3. What stays the operator's check

A live provider run, and the quality of a real verdict. `qa-agent` v1 still judges against the
pre-pivot health criteria (ADR-0037); retargeting it is `CLAUDE.md` task 12 (`qa-agent` v2) and does
not change this acceptance, which pins the contract and routing, not the wording.

## Deferred

- **A verdict whose `flags` arrive as a native JSON array.** The QA tool schema declares every field
  a string (ADR-0014 §3), but `AnthropicLLMClient` stringifies whatever the model sends with
  `str(value)`. A model that ignores the declared type and sends `["…"]` yields the Python repr
  `['…']`, which `decode_verdict` rightly refuses (ADR-0034) — so such a `flagged` answer parks the
  gate at `WAITING_QA` instead of escalating (`[]` happens to survive). This is fail-closed, not
  unsafe, but it would cost repeated QA calls on a live model. Deciding whether the provider adapter
  should JSON-encode non-string field values belongs to a later ADR amending ADR-0014/0034.
- Everything ADR-0038 already deferred: an Evaluation attempt model, a repair prompt, entrypoint
  routing of `APPROVED` / `REJECTED` (Stage 10).

## Consequences

### Positive

- Stage 8 has one acceptance covering both DoD lines on the production path, with restarts.
- The two-client binding of producers and QA, and QA metrics priced at the QA role's rate, are
  proven together for the first time.

### Negative / Trade-offs

- As with M2A, a scripted transport cannot catch a provider-side API change.

## Alternatives considered

- **Extend `tests/test_qa_wiring.py` instead of a new acceptance.** Rejected: QWR deliberately uses
  port fakes and a one-step Workflow; the acceptance's point is the production path end to end,
  mirroring M2A.
- **Accept on a live call.** Rejected for the same reasons as ADR-0033 §4 (secret and money in CI,
  non-determinism).

## References

- `ROADMAP.md`: Этап 8
- ADR-0014 §3, ADR-0016, ADR-0018, ADR-0026, ADR-0030, ADR-0032, ADR-0033, ADR-0034, ADR-0035,
  ADR-0036, ADR-0037, ADR-0038
- `STAGE8_ACCEPTANCE.md`; `tests/test_stage8_acceptance.py`
