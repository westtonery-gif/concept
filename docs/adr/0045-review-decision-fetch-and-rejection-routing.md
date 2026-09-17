# ADR-0045: The reviewer's decision is read from the desk, and a rejection is reworked with its reason

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect; the `REJECTED` route (§1) and keeping the approved
  escalation deferred (§3) were taken by the maintainer when asked
- **Amends:** ADR-0032 §1 (`REJECTED` no longer waits); ADR-0044 §1–§2 (the `REJECTED` row)

## Context

ROADMAP Stage 10's second DoD line: "the human's decision (Approve/Reject/Request changes) is read
and delivered to the orchestrator". Queue task 14.3. ADR-0044 publishes every pending review; the
Doc's `РЕШЕНИЕ:` line is parsed by `GoogleDocsReviewDesk.fetch_decision` (ADR-0043); nothing calls
it — an operator re-types the decision with `--approve` / `--request-changes`.

Two routes were still open (ADR-0032, ADR-0044 §2) and a fetched decision would hit both:

- **`REJECTED`.** `ARCHITECTURE.md` §13 and `DOMAIN_MODEL.md` §2.14/§10 say a Reject sends the Run
  back to rework (`Running`) with the recorded reason, and only an exhausted iteration limit ends it
  in `Failed`. The code left a rejected Run waiting, because ADR-0019 makes an Artifact in
  `REJECTED` terminal and not supersedable. The maintainer was asked: rework with the reason
  (chosen), a terminal `FAILED`, or keep it deferred.
- **An approved escalation** — `APPROVED` on a candidate whose latest QA is `FLAGGED`/`FAILED`. The
  maintainer chose to keep it deferred (no automatic route).

## Decision

### 1. A rejection is a rework iteration carrying its reason
A candidate whose latest review is `REJECTED` is routed exactly like `CHANGES_REQUESTED`
(ADR-0032): `WAITING_HUMAN → RUNNING`, one appended Task for the candidate's producer, the successor
through `Run.create_artifact_version`, fresh QA and review gates; an exhausted `ReworkPolicy` →
`FAILED` with `REWORK_LIMIT_REASON`.

The rejected **Artifact** is not moved to `ArtifactStatus.REJECTED`: it stays `CANDIDATE` until its
successor supersedes it. The decision itself is recorded on the Human Review (`REJECTED`, reason,
`HumanReviewRejected` event), which is where `PROJECT.md` §12 wants it for audit. ADR-0019 is
unchanged — `ArtifactStatus.REJECTED` keeps no route in the Director.

The rework Task's canonical input gains one field, **`human_decision`** — the triggering review's
status value (`changes_requested` / `rejected`) — so the producer knows whether it is refining or
replacing the candidate. `human_instructions` stays the review's reason (`null` when none). Stored
rework Tasks keep their stored input; nothing is migrated.

A Run already stored at `WAITING_HUMAN` with a `REJECTED` latest review is reworked on its next
`resume`.

### 2. `application/review_decision.py` reads a decision into the Run
- `apply_review_decision(run, desk) -> FetchedDecision | None`:
  - no pending review (Run not at `WAITING_HUMAN`, or every review decided) → `None`, the desk is
    **not** called;
  - `desk.fetch_decision(review_id)` of the latest pending review — the one
    `publish_pending_review` publishes; `None` (still undecided) → `None`, the Run is unchanged;
  - otherwise `Run.submit_review(review_id, decision, by=HUMAN_REVIEWER, reason=reason)` and
    `FetchedDecision(review_id, decision, applied=True)`;
  - **except** an `APPROVED` decision on a candidate whose latest QA Evaluation is not `PASSED`:
    nothing is submitted, `applied=False` (§3).
- A `ReviewDeskError` propagates and the Run is unchanged, as in ADR-0044 §4.
- It changes the Run in memory only. The **caller** saves it and then calls `resume`, which routes
  the decision (ADR-0044 §1 table + §1 here). Keeping the save in the caller keeps the function free
  of a `RunStore` and matches `publish_pending_review`.

### 3. An approval the gate would refuse is not recorded
The Director cannot complete such a Run (ADR-0018 §5), and the maintainer kept that route deferred.
If the approval were submitted, the Run would hold no `PENDING` review any more: neither a later
Doc edit nor `--request-changes` could reach it, and the Run would be stuck at `WAITING_HUMAN` for
good. Leaving the review `PENDING` keeps the exit open — the reviewer changes `РЕШЕНИЕ:` to
`доработать` or `отклонено` — and fails closed. The entrypoint says so; the approval word stays
visible in the Doc.

### 4. Entrypoint order
`demo_notion.py`, with a desk configured and no manual decision flag given, for a stored Run:
publish the pending review (idempotent, so a review that a crash left unpublished is published
before it is read — `fetch_decision` refuses an unpublished review), apply the fetched decision,
save, then `produce_brief` resumes as before and the end-of-invocation publication (ADR-0044)
publishes the successor's review. A desk refusal at this point is printed and the invocation goes
on; the Run is simply still waiting. Manual flags win over the desk and stay for keyless/local
testing; `--reject "<reason>"` joins `--approve` / `--request-changes` in both demos.

## Consequences

### Positive
- ROADMAP Stage 10's second DoD line is met: all three decisions travel Doc → Run → Director.
- The code now matches `ARCHITECTURE.md` §13 and `DOMAIN_MODEL.md` §2.14 on Reject.
- No new commit point in the Director, no snapshot format bump, no domain change.

### Negative / Trade-offs
- A rejection costs a model call (the rework) instead of ending the Run; the only way to stop
  iterating is the Run's `ReworkPolicy`.
- `ArtifactStatus.REJECTED` remains unreachable from the Director.
- APG-03's `REJECTED` rows and RWR-05's `REJECTED` case changed from "left alone" to "reworked".
- A reviewer's approval of a risky candidate is visible only in the Doc, not in the Run's audit,
  until the approved-escalation route is decided.

## Alternatives considered
- **Reject → Artifact `REJECTED` + Run `FAILED`:** contradicts `ARCHITECTURE.md` §13 /
  `DOMAIN_MODEL.md`; the maintainer rejected it.
- **Keep `REJECTED` deferred:** leaves a fetched rejection with no exit; the maintainer rejected it.
- **Submit an approval the gate refuses:** strands the Run without a pending review (§3).
- **Fetch inside the Director or a `RunStore` decorator:** the same reasons ADR-0044 §4 gives for
  publication.

## References
- `PROJECT.md` §12; `ARCHITECTURE.md` §13; `DOMAIN_MODEL.md` §2.14, §10; `ROADMAP.md` Stage 10
- `ADR-0018`, `ADR-0019`, `ADR-0032`, `ADR-0043`, `ADR-0044`
- `REWORK_ROUTING_SPEC.md` §1–§3; `EVALUATION_SPEC.md` §9; `ADAPTER_SPEC.md` §6;
  `EVALUATION_ACCEPTANCE.md` §4.5; `REWORK_ROUTING_ACCEPTANCE.md` §1; `ADAPTER_ACCEPTANCE.md` §12
