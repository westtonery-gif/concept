# ADR-0042: The ROADMAP Stage 9 (Notion) acceptance, and the brief intake it required

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect
- **Closes:** ROADMAP Этап 9 (`CLAUDE.md` queue 13.4)

## Context

Stage 9 was built in three subtasks — the real `NotionBriefBoard` (ADR-0040), the brief → Run
entrypoint `demo_notion.py` (queue 13.2, no ADR), and the status write-back `BriefStatusReporter`
(ADR-0041). The Stage's Definition of Done (ROADMAP.md) has two lines:

1. a brief from Notion produces a valid `Run`; statuses return to Notion;
2. the core knows nothing Notion-specific outside the adapter; the contract is covered by tests
   against a fake.

Line 2 already holds: `tests/test_adapter_contract.py` forbids any module outside `infrastructure/`
from importing network/third-party code, `NBB` runs the real adapter against a local fake Notion,
and `STB` / `BSR` cover the in-memory board and the reporter. Line 1 has no test at all: the only
code that turns a brief into a Run is the body of `demo_notion.py`'s `main`, which is not importable
without an API key and a Notion token.

Writing the acceptance against that flow found a gap. On a re-run `demo_notion.py` resumes the
stored Run with `resume_workflow(run, WORKFLOW, brief="")`. That is harmless once the first Task
exists — a started Task keeps its own stored input (ADR-0026 §3). But the Director commits `queued`
and `running` **before** it starts the first Task (ADR-0026 §2, rows 1–2). A process that dies
between those commits and the first Task's start leaves a stored Run with no Task; the next run
starts the first step on `""`. The first role would be called — and paid — on an empty brief, and
its Output would flow on as if it answered the brief. `Task` does not refuse a blank input (later
steps legitimately carry `""` in their requests), so nothing stops it.

## Decision

### 1. The brief intake is an application module, not an entrypoint body

`application/brief_intake.py` adds

```python
produce_brief(director, store, board, workflow, *, brief_ref, run_id) -> Run | None
```

where `store` is the `BriefStatusReporter` the `director` commits through and `board` the board it
reports to. It is the flow `demo_notion.py` had, made importable and testable:

- **No stored Run under `run_id`:** `board.fetch_brief(brief_ref)`; `None` → return `None` — no Run,
  no model call, no report. Otherwise `Run.create(run_id, content_brief_ref=brief.brief_ref,
  workflow_version_ref=workflow.workflow_id)` and `execute_workflow(..., brief=brief.body)`.
- **A stored Run:** its `content_brief_ref` must equal `brief_ref`, otherwise `BriefIntakeError`
  before anything changes (a `run_id` reused for another brief is a defect of the caller, not
  something to guess about). It is resumed with `resume_workflow` on the brief of §2.
- **At the end** of a normal return, and when the Director raised a `MeasuredEvaluatorError`
  (the Run is parked and stored at `waiting_qa`, ADR-0038), `store.sync(run)` — then the exception,
  if any, propagates unchanged. Any other exception propagates **without** a sync: the in-memory
  Run may be ahead of the store (e.g. a failed save), and the board must never show a status the
  store does not hold (ADR-0041 §1).

The module imports only `adapters`, `application` and `domain` — no Notion. The run-id scheme
stays the entrypoint's (`run-notion-<brief_ref>` in `demo_notion.py`), because naming a Run after a
vendor is exactly the specificity the core must not have.

### 2. The brief a resume hands to the first step

- **The Run already has a Task:** the first Task's stored `task_input` — the brief exactly as the
  first step received it. The Director does not read it for an existing Task, so this changes no
  behaviour; it only removes the `""` placeholder.
- **The Run has no Task yet** (the crash window above): `board.fetch_brief(run.content_brief_ref)`
  again. Nothing committed has used the brief text yet, so the board's current text *is* the brief.
  `None` (no longer on the board, no longer ready, no text) → `BriefIntakeError`; the Run stays as
  stored, no model is called and nothing is reported. A `BriefBoardError` propagates the same way.

Storing the brief body in the Run instead was rejected for this Stage: it changes `Run`'s snapshot
(`FORMAT_VERSION` bump, stored Runs refused) to cover a window of two commits, while the board
already is the brief's system of record (`ARCHITECTURE.md` §3.1). The Content Brief entity, when it
exists, may take this over.

### 3. `demo_notion.py` calls the intake

The entrypoint keeps its configuration, argument parsing, `--request-changes` and printing, and
delegates fetch → create → execute / resume → sync to `produce_brief`. A `BriefIntakeError` is
printed and the demo exits cleanly.

### 4. Stage 9 is accepted by one production-asset pass against an in-memory board

`tests/test_stage9_acceptance.py` (`STAGE9_ACCEPTANCE.md`, prefix `S9A`) mirrors S8A (ADR-0039):
the bundled Prompt store for all three roles, Rin's Skill and Tool, a real `AnthropicLLMClient` for
the producers and another for QA (only the network below the SDK is scripted), the real
`SqliteRunStore`, every restart a new Composition Root over the same file. The board is
`InMemoryBriefBoard` (ADR-0025) — the fake the DoD names — wrapped in `BriefStatusReporter` and driven
only through `produce_brief`. It covers: a filed brief → a valid Run and every status on the board;
an unproducible brief → nothing; a risk verdict → human rework across a restart, shown; the crash
window of §2 in both outcomes; a QA error parked and shown; a board outage that never stops the Run
and is caught up by the next invocation; a foreign `run_id`.

A live Notion round-trip stays the operator's check through `demo_notion.py` (a real integration
token and database are not available to CI), as a live model call is for `demo_factory.py`.

## Consequences

### Positive

- Both DoD lines of Stage 9 are covered on the production path; the brief → Run flow is code with
  tests, not an untested entrypoint body.
- A crash before the first Task can no longer run the first role on an empty brief.

### Negative / Trade-offs

- A Run that crashed before its first Task and whose brief was edited in the meantime is produced
  from the edited text. This is the text the editor last filed and nothing was produced from the
  old one, so it is accepted.
- `produce_brief` trusts that `director` commits through `store`; it cannot check it.

## Alternatives considered

- **Accept by testing `demo_notion.main` directly.** Rejected: it reads the environment, needs an
  API key to get past its first check, and builds its own clients — the acceptance would have to
  patch globals, and the gap would still sit in untestable code.
- **Refuse to resume a Run without a Task** (fail with "start over"). Rejected: the operator would
  have to delete the whole store file (the demo keeps every Run in one), and the brief is still on
  the board.
- **Make `Task` refuse a blank input.** Rejected: changes a domain contract every later step relies
  on (`""` placeholders in `expand`), and would turn the crash into a Run that cannot resume at all.

## References

- `ROADMAP.md`: Этап 9
- ADR-0023, ADR-0025, ADR-0026 §2–§3, ADR-0038, ADR-0039, ADR-0040, ADR-0041
- `STAGE9_ACCEPTANCE.md`; `tests/test_stage9_acceptance.py`; `ADAPTER_SPEC.md` §5
