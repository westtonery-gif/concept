# ADR-0041: Status write-back — every committed Run status is shown on its brief

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 9's DoD has two lines: "a brief from Notion produces a valid `Run`" and "statuses
return to Notion". The first is done: `NotionBriefBoard` (`ADR-0040`) reads a brief, and
`demo_notion.py` (queue subtask 13.2) turns it into a Run. The second is not: `BriefBoard.
report_status(brief_ref, *, run_id, status)` exists and is tested, but nothing calls it. The queue
names this subtask 13.3 and leaves two questions open — **which** transitions to report, and
**where** the call lives.

Already in place:

- `ADR-0023` §6 / `ADAPTER_SPEC.md` §5: reporting the same status again is harmless, and **a failed
  report never changes the Run**.
- `ADR-0026`: the Content Director commits the Run through `RunStore` after every orchestration
  step. A status change is always one of those commits (§2, rows 1, 2, 5, 8, 9 — plus the rework
  entry of `ADR-0032`). The stored Run is the truth; the board is a showcase of it
  (`ARCHITECTURE.md` §3.1: Notion is where a human "sees the result").
- `PROJECT.md` §10: no silent failure; retries only for transient errors, bounded. §4.11: extend
  by adding modules, not by modifying the core.
- `ADR-0040` §3: a board outage surfaces as `BriefBoardError`; retry/back-off is deferred.

## Decision

### 1. Report every status change, never more than the store holds
Every change of `Run.status` that the Director commits is reported: `queued`, `running`,
`waiting_qa`, `waiting_human`, `completed`, `failed` (a rework shows `running` again). `created` is
never reported — the Director's first commit is already `queued`.

Why every one, not a filtered subset:

- A step is a model call that can take minutes; `running` is exactly what an editor wants to see
  while it lasts. A board that jumps from nothing to `completed` hides a live Run.
- A filter would be a second list of "interesting" states with no reader to decide it. The Run's
  seven states are already the human-facing vocabulary (`RUN_SPEC.md` §4).
- The cost is bounded: at most one report per transition — five on a straight run, three more per
  rework iteration — and each is two Notion requests (`ADR-0040` §3).

A report is sent **after** the commit that stored the status, never before: the board never shows
a status the store does not hold. A failed `save` (`RunStoreError`) propagates as before and
reports nothing.

### 2. A `RunStore` decorator, not a change to the Director
`application/brief_status.py` adds `BriefStatusReporter(store, board)`, itself a `RunStore`:

- `save(run)` saves through the wrapped store, then reports `run.status` on
  `run.content_brief_ref` with `run.run_id` — once per status change: only when this reporter's
  last attempt for that Run was a different status.
- `load(run_id)` delegates unchanged.
- `sync(run)` reports the Run's current status unless it was already shown successfully, without
  saving — the retry path of §3.

The Director is not touched: it already calls `save` at exactly the points where a status changes,
so it keeps knowing only `RunStore` (`ADR-0026` §1). The reporter sits in `application/` because it
composes two adapter Protocols and imports no implementation.

The wrapped Run's `content_brief_ref` must be a reference **on that board** — it is when the Run
was created from `board.fetch_brief(...)`, which is the only way the entrypoint creates one. The
reporter does not guess otherwise; a foreign reference is refused by the board itself (§3).

The last attempted and the last shown status are remembered per `run_id` in memory. A new process
starts empty, so its first save or sync reports again — harmless by the contract.

### 3. A failed report is logged and retried, never allowed to stop production
When `report_status` raises `BriefBoardError`:

- the Run and the stored truth are unchanged (the save already happened);
- the reporter logs a `WARNING` on the `omemo_content_factory.application.brief_status` logger
  naming the Run, the status and the error, and keeps it in `failed_reports`;
- the status is **not** remembered as shown, so `sync` tries it again;
- the orchestration continues.

Later saves in the **same** status do not retry: the Director commits several times inside one
status (every Task start and finish is a `RUNNING` commit), and during an outage each retry would
wait for the board's timeout. A refused status is either superseded by the next status change —
which overwrites the same two properties anyway — or retried by the entrypoint's `sync`.

This is not a silent failure (`PROJECT.md` §10): the Run stays in an explicit, stored state, and
the missed report is logged and exposed for the entrypoint to print. Stopping a paid production
run because the showcase is unreachable would invert which side is the truth. No retry loop or
back-off is added (`ADR-0040` "Deferred"); the retry is the next status change or `sync`.

Any other exception from the board is a defect, not an outage, and propagates unmasked.

### 4. The entrypoint syncs once at the end
`demo_notion.py` wraps its `SqliteRunStore` in a `BriefStatusReporter` over the board it fetched
the brief from, hands that to the Director, and calls `sync(run)` after every invocation — also
when the Director raised a `MeasuredEvaluatorError`. That covers the cases where no status changed
in this process (resuming a Run waiting for a human, or a QA error leaving it at `waiting_qa`) and
retries a report that failed mid-run. A `failed_reports` entry is printed.

A brief that `fetch_brief` does not hand over starts no Run and gets no report.

## Consequences

### Positive
- Stage 9's second DoD line is met without changing `ContentDirector`, `Run` or any adapter
  contract.
- The board shows a Run live, in the Run's own vocabulary, and never ahead of the stored truth.
- A Notion outage costs a stale board, never a stopped or corrupted Run.

### Negative / Trade-offs
- Reporting is synchronous inside a commit: a slow Notion slows the Run by up to its timeout per
  transition (10 s by default, `ADR-0040` §4).
- A refused report is not retried until the next status change or the entrypoint's `sync`; a
  process that dies first leaves the board stale until the next invocation.
- The reporter relies on `save` being called on status changes — a contract of `ADR-0026`, not of
  the `RunStore` Protocol. A future orchestrator that commits differently must keep that.

## Alternatives considered
- **Report only resting states from the entrypoint** (`waiting_human`, `completed`, `failed`, after
  `execute` returns): simpler, but an editor sees nothing for the whole production and a crash
  leaves the brief showing no Run at all.
- **A filtered list** (`queued` / `waiting_human` / `completed` / `failed`): an extra rule with no
  owner; `running` and `waiting_qa` are the states that last longest.
- **`ContentDirector(..., board=)`**: couples the orchestrator to a second adapter and duplicates
  the commit points it already has; it would modify the core instead of adding a module.
- **Raise `BriefBoardError` out of `save`:** the Run is safe (already stored) but one unreachable
  showcase would halt every production run until Notion is back.
- **Report before the save:** the board could show a status the store never received.

## References
- `PROJECT.md` §4.11, §10
- `ARCHITECTURE.md` §3.1, §10, §12
- `ROADMAP.md` Stage 9
- `ADR-0023`, `ADR-0026`, `ADR-0032`, `ADR-0040`
- `ADAPTER_SPEC.md` §5; `ADAPTER_ACCEPTANCE.md` §9
