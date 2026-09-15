# ADR-0026: Storage wiring — the Content Director commits every step and resumes a restored Run

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 7 (Milestone M2) puts the first real Agent through the full orchestrator; the session
queue breaks it into subtasks, and 8.1 is **storage wiring**: save the Run through `RunStore` after
each transition in a real run and bring it back through `Run.restore`. Already in place:

- `ADR-0015` admits a restored Run and fixes the invariants of recoverability, among them **I3 —
  idempotent continuation** ("continuation from the preserved truth neither loses nor duplicates
  completed work").
- `ADR-0024` implemented `SqliteRunStore` and deferred exactly this wiring: "saving after each
  transition, the database location, and its configuration variable (Stage 7)" (§6, "Deferred").
- `ARCHITECTURE.md` §10 fixes the cycle: an event (an agent finished · QA gave a verdict · a human
  decided · an error) → the Content Director computes the next transition → **the new state and its
  trace are committed atomically through the Storage Adapter** → the Run is observable and
  recoverable at any moment. It adds: a step without its trace counts as not done.

Nothing saves a Run today. `ContentDirector.execute` drives a freshly created Run to the end in
memory; a crash loses everything, and a Run brought back by `SqliteRunStore.load` has nothing that
could continue it — `execute` starts with `CREATED → QUEUED` and refuses any other state.

Two questions are open: **where** exactly a commit falls, and **how** a restored Run is continued.

## Decision

### 1. The Content Director owns the commits; the store is optional
`ContentDirector(..., store: RunStore | None = None)`. With a store, the Director calls
`store.save(run)` at every commit point below; without one, it behaves exactly as before. The
Director holds only the `RunStore` Protocol (`adapters/`); the Composition Root injects the
implementation. It is the Director because it is the single owner of transitions (`ADR-0013` §8,
`ARCHITECTURE.md` §10) — the one place that knows where a step begins and ends.

A failed save raises `RunStoreError` out of the Director, unmasked (PROJECT §10: no swallowed
errors). The Run in memory is then ahead of the stored truth; the stored truth stays whole
(`ADR-0024` §3) and a restart continues from it.

### 2. A commit point is one orchestration step, not one aggregate call
Committing after *every* aggregate call would store half-recorded steps: a Task `SUCCEEDED` without
its Output, an Output without its Artifact. A restart could not tell a lost result from a step that
legitimately produced none, and a `SUCCEEDED` Task cannot run again. So a commit falls after each
**step** of `ARCHITECTURE.md` §10, and every commit that precedes an external call is taken *before*
the call:

| # | Commit point | The stored Run shows |
|---|---|---|
| 1 | `CREATED → QUEUED` | `QUEUED` |
| 2 | `QUEUED → RUNNING` | `RUNNING` |
| 3 | a Task is started — **before** its executor is called | the Task `RUNNING` with its fixed input |
| 4 | the executor has answered | the Task terminal, **with** its Output and the Artifact made from it |
| 5 | `RUNNING → WAITING_QA`, or `→ FAILED` when a Task failed | that status |
| 6 | the QA gate is opened — **before** the evaluator is called | the candidate `CANDIDATE`, one `PENDING` Evaluation |
| 7 | the evaluator has answered | the verdict recorded |
| 8 | the verdict is routed | `WAITING_HUMAN` (on a risk verdict, together with the escalation review); or `FAILED` when there is no candidate |
| 9 | `WAITING_HUMAN → COMPLETED` | `COMPLETED` |

Each commit is one `save`, and `save` is atomic (`ADR-0024` §3). Hence, for every stored Run: a
`SUCCEEDED` Task carries its Output (when the validated path produced one) and that Output its
Artifact; a `RUNNING` Task marks an executor call whose answer was never committed; a `PENDING` QA
Evaluation marks an evaluator call whose verdict was never committed.

To commit *between* opening a Task and calling its executor, the application slices expose their
two halves: `start_task` / `finish_task` in `task_execution` and `record_verdict` in
`qa_evaluation`. `execute_task` and `evaluate_artifact` keep their signatures and behaviour and are
now the composition of those halves.

### 3. Resumption: `ContentDirector.resume` / `resume_workflow`
`resume(run, tasks)` continues a Run — typically one returned by `RunStore.load` — from whatever
state it is in, under the unchanged transition contract (`ADR-0015` I7). `resume_workflow(run,
workflow, *, brief)` is its Workflow form, as `execute_workflow` is for `execute`. `execute` keeps
its contract: it takes a freshly created Run and still refuses any other.

The Director derives progress from the Run itself, never from a side record:

- **The requests and the Tasks are matched by position** — the i-th Task was opened for the i-th
  request, as `execute` opens them. A Task whose `workflow_step_ref` or `agent_ref` differs from its
  request, or more Tasks than requests, raise `RunResumptionError` before anything changes: the
  stored Run is not a run of this workflow, and guessing would corrupt it.
- **A terminal Task is not run again.** A `SUCCEEDED` Task hands its Output on as the next input,
  exactly as in the original run; its Artifact is created only if it is missing. A `FAILED` Task
  stays failed.
- **A `RUNNING` Task is run again** by re-entering `RUNNING` — the retry the Task already models
  (`ADR-0004`; the attempt counter rises, bounded by its retry policy) — on its *stored* input. When
  its attempts are exhausted it is failed instead, with the reason `RESUME_LIMIT_REASON`, and the
  Run follows the ordinary failure route. A `PENDING` Task (never stored by the Director, but a legal
  state) is simply started.
- **The QA gate** re-uses a `PENDING` Evaluation of the candidate instead of opening a second one,
  and routes an already recorded verdict without asking the evaluator again.
- **`WAITING_HUMAN`** with a Human Review in the Run means the Run waits for the human: `resume`
  changes nothing (the decision round-trip is Stage 10; routing `CHANGES_REQUESTED` is subtask 8.6).
  Without one — the QA gate passed and the process died before `COMPLETED` — the Run is completed.
- **A terminal Run** (`COMPLETED`, `FAILED`) is left as it is. Resuming it calls nothing and changes
  nothing.

`RunResumptionError` is an application error (`Exception`), like `CompositionError`: the
orchestrator was handed a Run and a plan that do not belong together; no domain rule was broken.

### 4. What "not duplicated" means, precisely
Committed work is never done twice: every committed Task outcome, Artifact and verdict is reused on
resume. An external call whose answer was **not** committed is made again — an executor or evaluator
call is *at least once* across a crash. That is I3 read through `ARCHITECTURE.md` §10: work without
its committed trace counts as not done. The re-entry into `RUNNING` makes the repeat visible in the
Task's `attempt_count`, so it is traced, not hidden.

### 5. Where the database lives
`composition.build_run_store(environ)` builds a `SqliteRunStore` at `OMEMO_RUN_STORE_PATH`, or at
`.omemo/runs.sqlite3` (relative to the working directory) when the variable is unset or blank, and
creates the parent directory. `.omemo/` is git-ignored. `build_content_director` and
`compile_runtime` accept an optional `store` and hand it to the Director. `demo_factory.py` loads
its Run from the store: an unknown id is created and executed, a stored one is resumed — so a second
run of the demo after a completed one calls no model at all.

## Deferred
- **Listing unfinished Runs** after a restart ("resume everything that was running"): still
  `ADR-0024` "Deferred"; the entrypoint resumes the Run it names.
- **Bounding repeated external calls** beyond the Task's retry policy (a crash loop on the same
  step spends one attempt per restart and then fails the Task — nothing more).
- **The evaluator's attempt count.** A `PENDING` Evaluation carries none; repeated evaluator calls
  across crashes are not counted.
- **Saving on a failed save's retry.** A `RunStoreError` stops the Director; retrying the store is
  left to the entrypoint.
- **Wiring the other three adapters** (board, review desk, analytics sink): later Stage 7/9/10
  subtasks.

## Consequences

### Positive
- `ARCHITECTURE.md` §10's cycle holds in code: each step is committed atomically, and a stored Run
  is never a half-recorded step.
- A crash at any point loses at most the one external call in flight; a restart continues from the
  stored truth with no id collisions and no repeated committed work.
- Additive: `Run` is untouched; `execute`, `execute_task`, `evaluate_artifact` and the Composition
  Root keep their signatures; a Director without a store behaves as before.

### Negative / Trade-offs
- Up to nine or so saves per two-step run, each rewriting the whole document. Cheap for SQLite and
  a document of this size; revisit with an industrial store.
- An interrupted attempt consumes an attempt from the Task's retry policy even when the executor
  was never reached (the crash fell between commit 3 and the call).
- The Director's resumption logic reads Run state that `execute` never had to read (positions,
  statuses, an existing pending Evaluation). It is the second reader of that state, after the gates.

## Alternatives considered
- **Save after every aggregate call.** Rejected (§2): it stores half-recorded steps that a restart
  cannot interpret.
- **Save only at the end, or only at lifecycle transitions of the Run.** Rejected: a crash would lose
  every completed Task of the run, against I3.
- **A saving decorator around `Run`, or a hook inside `Run`.** Rejected: it changes the reference
  aggregate (CLAUDE.md conventions) and cannot see step boundaries, only calls.
- **Passing the store into `execute_task` / `evaluate_artifact`.** Rejected: the slices would learn
  about persistence, and every future slice would need the same parameter. Splitting them lets the
  one owner of transitions commit.
- **Making `execute` accept a restored Run.** Rejected: it would silently change a public contract
  that tests and callers rely on ("expects a freshly created Run"). A separate `resume` says what it
  does.
- **A separate progress record ("step k done") beside the Run.** Rejected: the Run is the single
  unit of state (`ARCHITECTURE.md` §10); a second record could disagree with it.

## References
- `PROJECT.md`: §4.11, §10
- `ARCHITECTURE.md`: §9, §10
- `ROADMAP.md`: Stage 7
- `ADR-0004`, `ADR-0013` §8, `ADR-0015` (I3, I7), `ADR-0018` §7, `ADR-0023` §5, `ADR-0024` §6
- `ADAPTER_SPEC.md` §4, `ADAPTER_ACCEPTANCE.md` §7 (SWR)
