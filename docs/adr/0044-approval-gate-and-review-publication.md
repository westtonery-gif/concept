# ADR-0044: The Approval Gate holds every QA-passed candidate, and a pending review is published

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect; the gate change (§1) was taken by the maintainer
  when asked
- **Amends:** ADR-0018 §7 (the `PASSED` route); ADR-0026 §2 (the gate's commit points)

## Context

ROADMAP Stage 10 says: "the transition of a `Run` to `Waiting Human` is accompanied by publishing
the candidate to Google Docs". The session queue calls this task 14.2. `GoogleDocsReviewDesk`
exists (ADR-0043); nothing calls `publish`.

Reading the Director for the wiring turned up a conflict with the charter:

- `PROJECT.md` §12 makes Human Approval an architectural **invariant** — "the final yes is always a
  human's", at least one gate "before an artifact is recognised as ready". `ARCHITECTURE.md` §13
  draws it: QA "passed" → the Content Director publishes the candidate to Google Docs → `Waiting
  Human`. `RUN_SPEC.md` §4 and `DOMAIN_MODEL.md` define `Completed` as "an artifact approved by a
  human".
- The code did not do that. ADR-0018 §7 kept "the existing route" for `PASSED`: the Run passed
  through `WAITING_HUMAN` straight to `COMPLETED` in two commits, with no Human Review and the
  candidate still `CANDIDATE`. Only a QA risk (ADR-0018) and a rework successor (ADR-0032) opened a
  review. So the most common outcome — QA is happy — had nothing to publish and no human in it.
- Nothing applied an **approved** decision either: after `APPROVED`, `resume` left the Run at
  `WAITING_HUMAN` forever; tests approved the Artifact by hand.

The maintainer was asked and chose to hold the gate on `PASSED` as well (§1), rather than publishing
only the reviews that already existed.

## Decision

### 1. With QA wired, a candidate always stops at the Approval Gate
When a QA evaluator is wired (or the Run is in rework, ADR-0032), reaching `WAITING_HUMAN` **always**
opens a `PENDING` Human Review on the final candidate — a `PASSED` verdict included — in the **same
commit** as the transition. The Director then stops. A risk verdict was already committed this way;
the rework successor's review, previously a separate commit, joins it. A stored Run at
`WAITING_HUMAN` therefore always has its review; one stored without it (by older code) gets it on
`resume`.

On `resume`, the gate is routed from the candidate's **latest** review:

| Latest review of the candidate | Latest QA of the candidate | Director |
|---|---|---|
| `PENDING` | any | leaves the Run alone, commits nothing |
| `APPROVED` | `PASSED` | Artifact → `APPROVED`, Run → `COMPLETED`, **one** commit |
| `APPROVED` | `FLAGGED` / `FAILED` | leaves the Run alone (the domain would refuse the approval, ADR-0018 §5) |
| `CHANGES_REQUESTED` | any | the ADR-0032 rework, unchanged |
| `REJECTED` | any | leaves the Run alone (§2) |

No model is called on any of these rows. Publication of the approved Artifact stays a Run
transition, not part of this route (ADAPTER_SPEC §6).

**Without** a QA evaluator (and outside rework) the legacy route is unchanged: `WAITING_HUMAN` →
`COMPLETED` with no review. No Artifact can be approved there anyway (no `PASSED` Evaluation), and
the no-QA configuration is a test/demo configuration, not a production one; closing it is not this
decision's job.

### 2. Deferred on purpose
- **`REJECTED`** stays deferred as in ADR-0032: whether a rejected Run fails, and whether a rejected
  version may be superseded, is a domain decision with no reader yet.
- **An approved escalation** (human `APPROVED` on a flagged candidate) stays waiting: the fail-closed
  gate refuses it, and choosing between "treat as reject" and "ask again" is the same open question.

Both leave a Run at `WAITING_HUMAN`; the operator sees the decided review in the store.

### 3. The package is built from the Run alone
`application/review_publication.py` adds:

- `pending_review_package(run) -> ReviewPackage | None` — `None` unless the Run is at
  `WAITING_HUMAN` with a `PENDING` review; otherwise the latest pending review, its Artifact as
  `candidate`, `brief` = the first Task's stored `task_input` (exactly what the Run was produced
  from — the Run holds no other copy, ADR-0042 §2), `qa_flags` = the flags of the candidate's latest
  QA Evaluation (`()` when it passed or none exists).
- `publish_pending_review(run, desk) -> PublishedReview | None` — publishes that package and returns
  `PublishedReview(review_id, location)`; `None` when there is nothing to publish.

It reads the Run and never changes it. It knows only the `ReviewDesk` Protocol.

### 4. Published by the entrypoint after the Director returns, not inside a commit
The Director is not given a desk, and no `RunStore` decorator publishes (unlike ADR-0041):

- `WAITING_HUMAN` is a **resting** state — the Director returns right after committing it — so
  publishing after `execute`/`resume` returns loses no time a reviewer could use.
- `publish` is idempotent per `review_id` (ADAPTER_SPEC §6), so "publish whatever is pending" after
  every invocation is both the first attempt and the retry, with no remembered state: a crash
  between the commit and the publication is repaired by the next invocation, and a Run published
  earlier gets the same location back.
- A `ReviewDeskError` **propagates** to the entrypoint. The Run is already stored and untouched; the
  entrypoint reports the refusal and the next invocation tries again. Nothing is logged-and-kept as
  in ADR-0041, because the caller is not in the middle of a production run.

The Run does not record the location: the desk finds a review by `review_id` (ADR-0043 §3), and a
location in the snapshot would need a format bump for no reader.

### 5. Wiring
- `composition.build_review_desk(environ) -> ReviewDesk` builds `GoogleDocsReviewDesk` from
  `google_docs_settings_from_env` — missing variables or an unusable key fail closed with
  `ReviewDeskError` before any request.
- `demo_notion.py` builds the desk when **either** `OMEMO_GOOGLE_*` variable is set (a half setup is
  explained and the demo exits before any model call); with neither, reviews stay in the store and
  that is said. After every invocation it calls `publish_pending_review` and prints the Doc link, or
  the refusal.
- `demo_factory.py` and `demo_notion.py` gain `--approve` next to `--request-changes`, so the new
  gate can be passed locally until the decision is read from the desk (queue 14.3).

## Consequences

### Positive
- The code now matches `PROJECT.md` §12, `ARCHITECTURE.md` §13 and `RUN_SPEC.md` §4: no QA-wired
  Run completes without a human `APPROVED`, and `COMPLETED` means an approved Artifact.
- Stage 10's first DoD line is met for every candidate, not only escalations.
- A desk outage costs a later publication, never a changed or failed Run.

### Negative / Trade-offs
- A behaviour change: Stage 8/9 acceptances, SWR, BSR, QWR, LAE and ECD expectations of
  `passed → completed` became `passed → waiting_human (+ review)`, then `APPROVED → completed`.
  Existing stored Runs at `COMPLETED` are unaffected.
- Every QA-passed Run now needs a human action before it completes — intended, but slower.
- `REJECTED` and an approved escalation leave a Run waiting with no automatic exit (§2).
- Until 14.3, an operator must apply the Doc's decision with `--approve` / `--request-changes`.

## Alternatives considered
- **Publish only the reviews that already existed** (risk escalations, rework successors), keep
  `PASSED → COMPLETED`: smaller, but leaves the charter's invariant broken for the common path. The
  maintainer rejected it.
- **`ContentDirector(..., desk=)` publishing inside the gate commit:** couples the orchestrator to a
  second adapter, and a desk outage mid-`execute` would have to be swallowed or would abort a run
  whose state is already safe.
- **A `RunStore` decorator like ADR-0041:** needs remembered "already published" state and a `sync`,
  for a state in which the Director has already returned.
- **Storing the location on the Run:** a snapshot format bump with no reader.

## References
- `PROJECT.md` §4.11, §10, §12
- `ARCHITECTURE.md` §3.11, §13
- `ROADMAP.md` Stage 10
- `RUN_SPEC.md` §4; `DOMAIN_MODEL.md` §2 (Run), §10 (`RunCompleted`)
- `ADR-0007`, `ADR-0018`, `ADR-0023`, `ADR-0026`, `ADR-0032`, `ADR-0041`, `ADR-0042`, `ADR-0043`
- `EVALUATION_SPEC.md` §9; `EVALUATION_ACCEPTANCE.md` §4.5; `ADAPTER_SPEC.md` §6;
  `ADAPTER_ACCEPTANCE.md` §11
