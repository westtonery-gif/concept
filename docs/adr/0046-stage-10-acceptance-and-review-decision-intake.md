# ADR-0046: The ROADMAP Stage 10 (Google Docs) acceptance, and taking a review decision into the store

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect
- **Closes:** ROADMAP Этап 10 (`CLAUDE.md` queue 14.4)

## Context

Stage 10 was built in three subtasks: the real `GoogleDocsReviewDesk` (ADR-0043), publication of
every pending review behind a held Approval Gate (ADR-0044), and reading the reviewer's decision
back with Reject routed to rework (ADR-0045). The Stage's Definition of Done (ROADMAP.md) has two
lines:

1. the candidate is published to Google Docs with its context (brief, QA flags, versions);
2. the human's decision (Approve / Reject / Request changes) is read and delivered to the
   orchestrator.

`GDR`, `RPB` and `RDF` cover each piece on its own, with deterministic executors. Nothing covers the
Stage end to end on the production path, and — as in Stage 9 (ADR-0042) — part of the flow existed
only in `demo_notion.py`: for a stored Run, publish the pending review, apply the desk's decision,
save **only when it was recorded**, then resume. That order is ADR-0045 §4's (publish first, so a
review a crash left unpublished can be read), and it was code nobody could import.

Writing the acceptance against that flow turned up two smaller things:

- After a crash between "decision saved" and "resume", the next invocation found no pending review
  and the demo printed *"the reviewer has not decided yet"* — then completed the Run. Wrong text,
  right behaviour.
- `InMemoryReviewDesk.decide` keeps the first decision for good (ADR-0025 §3), but ADR-0045 §3
  relies on a reviewer **retyping** the Doc's decision after an approval QA did not pass — which the
  real Doc allows. The stub cannot play that scenario.

## Decision

### 1. `take_review_decision` is an application function

`application/review_decision.py` adds

```python
take_review_decision(store, desk, run_id) -> FetchedDecision | None
```

- no stored Run under `run_id` → `None`; the desk is not called;
- otherwise `publish_pending_review(run, desk)` (idempotent; nothing pending → no desk call), then
  `apply_review_decision(run, desk)`;
- the Run is saved through `store` **only** when the result is `applied`; an undecided review or an
  approval the gate refuses saves nothing;
- a `ReviewDeskError` propagates; nothing is saved.

It does not resume: the caller does, exactly as ADR-0045 §2 keeps it. `demo_notion.py` delegates to
it; its "not decided yet" message now appears only when the Run still has a pending review.

### 2. The stub stays; the acceptance plays a Doc

`InMemoryReviewDesk` is left as ADR-0025 decided. The acceptance subclasses it with a desk whose
decision line can be overwritten (and which can be taken down), because that is what a Google Doc
is. Changing the stub's rule would reopen ADR-0025 for no production reader.

### 3. Stage 10 is accepted by one production-asset pass

`tests/test_stage10_acceptance.py` (`STAGE10_ACCEPTANCE.md`, prefix `S10A`) reuses S9A's process —
bundled Prompts for all three roles, Rin's Skill and Tool, real `AnthropicLLMClient`s with scripted
transport, the real `SqliteRunStore` under `BriefStatusReporter` — and adds the desk. Every
invocation is a new process doing what `demo_notion.py` does: `take_review_decision` →
`produce_brief` → `publish_pending_review`. It covers: publication with brief, flags and version; an
undecided review; approval → `COMPLETED`; changes requested / rejected → rework whose v2 is
published with `supersedes_ref`; an approval QA did not pass, then retyped; a lost publication; a
desk outage while reading; a crash after the decision is saved; rejections past the rework bound.

A live Google Docs round-trip stays the operator's check through `demo_notion.py` (a service account
and a shared folder are not available to CI).

## Consequences

### Positive

- Both DoD lines of Stage 10 are covered on the production path; the entrypoint's decision step is
  tested code.
- The misleading message after a crash is gone.

### Negative / Trade-offs

- The in-memory stub still disagrees with the real desk on a retyped decision; a test that uses
  `decide` cannot model ADR-0045 §3.
- The order "take decision → produce → publish" is still composed by the entrypoint; the acceptance
  repeats that three-call composition rather than importing it. A single function would have to
  report two independent desk outcomes plus the intake's, for one caller.

## Alternatives considered

- **Test by composing `apply_review_decision` + a save in the test.** Rejected: the save-only-when-
  applied rule would live in the test and the demo separately.
- **Let `InMemoryReviewDesk.decide` overwrite.** Rejected here (§2).
- **One `review_brief(...)` doing the whole invocation.** Rejected (see trade-offs).

## References

- `ROADMAP.md`: Этап 10
- ADR-0025 §3, ADR-0042, ADR-0043, ADR-0044, ADR-0045
- `STAGE10_ACCEPTANCE.md`; `tests/test_stage10_acceptance.py`; `ADAPTER_SPEC.md` §6;
  `ADAPTER_ACCEPTANCE.md` §12
