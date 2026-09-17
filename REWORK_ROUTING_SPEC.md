# REWORK_ROUTING_SPEC.md — QA / Human rework routing

> Application contract for ADR-0032. It is subordinate to `PROJECT.md`, `ARCHITECTURE.md`,
> `ROADMAP.md`, `DOMAIN_MODEL.md` and the Run/Artifact/Evaluation/Human Review contracts.
> Acceptance: `REWORK_ROUTING_ACCEPTANCE.md`.
>
> **Status:** Accepted. **Date:** 2026-09-15.

## 1. Scope

This contract closes the application route:

`QA risk -> WAITING_HUMAN -> CHANGES_REQUESTED | REJECTED -> RUNNING -> rework Task -> Artifact vNext -> WAITING_QA`.

`REJECTED` joined the route in ADR-0045 §1.

It changes no domain aggregate API. The Content Director composes existing operations and the
existing executor, Schema, QA and RunStore ports.

Out of scope: automatic pre-human rework, a terminal rejection (`ArtifactStatus.REJECTED`), a real QA Agent, external ReviewDesk wiring,
replaying more than the current candidate's producer, and a typed replacement for string Task
input.

## 2. Trigger and routing

While a Run is `WAITING_HUMAN`, `ContentDirector.resume(run, tasks)` reads the latest Human Review
of the current candidate.

| Review status | Director action |
|---|---|
| `PENDING` | stop and wait |
| `APPROVED` | no rework; existing approval routing remains outside this slice |
| `CHANGES_REQUESTED` / `REJECTED` | validate the plan, then request `WAITING_HUMAN -> RUNNING` (ADR-0045 §1) |

A rejected candidate stays `CANDIDATE` until its successor supersedes it; it never becomes
`ArtifactStatus.REJECTED` on this route.

The triggering review must target the current live candidate. Older decided reviews never start a
new iteration.

If the Run's rework bound refuses the transition, the Director makes no executor call and moves the
Run to `FAILED` with:

`Rework requested but the configured iteration limit is exhausted` (`REWORK_LIMIT_REASON`).

## 3. Rework Task

One accepted rework transition owns exactly one appended Task:

- `workflow_step_ref` and `agent_ref` equal those of the Task whose Output produced the candidate;
- the normal per-agent executor and authoritative Schema binding are used;
- earlier Tasks are not reopened or retried;
- `finish_task` records every reported analytics measurement before the Task outcome, unchanged.

The stored Task input is canonical JSON produced with sorted keys and compact separators:

| Field | Value |
|---|---|
| `artifact.ref` | reviewed Artifact id |
| `artifact.version` | reviewed Artifact version |
| `artifact.content` | reviewed immutable content |
| `qa_flags` | flags of that Artifact's latest `qa` Evaluation, or `[]` |
| `human_decision` | triggering Review's status value: `changes_requested` or `rejected` (ADR-0045 §1) |
| `human_instructions` | triggering Review's reason/instructions, including `null` when absent |
| `rework_iteration` | Run's rework count after re-entry |

This exact string is reused for an interrupted `RUNNING` Task.

## 4. Outcome

### 4.1 Success with Output

The Director calls `Run.create_artifact_version(candidate, output, by=CONTENT_DIRECTOR)`. The
predecessor and successor are committed in the same step as the Task outcome:

- predecessor: `SUPERSEDED`, unchanged content/version/provenance;
- successor: `DRAFT`, version + 1, `supersedes_ref` = predecessor id, content/provenance from the
  rework Output, predecessor kind inherited.

Run then moves to `WAITING_QA`. With QA wired, only the successor becomes `CANDIDATE` and receives a
new Evaluation. A risk verdict repeats the existing human escalation; `CHANGES_REQUESTED` can begin
another bounded iteration. If a restored rework Run is resumed without a QA evaluator, it remains
at `WAITING_QA`; unlike the legacy non-QA first-pass demo route, a successor can never bypass its
fresh gate.

### 4.2 Failed Task

Run moves to `FAILED` with the existing `one or more tasks failed` reason. The predecessor remains
the live `CANDIDATE`; no new Artifact exists.

### 4.3 Success without Output

Run moves to `FAILED` with:

`Rework task succeeded without a validated Output` (`REWORK_NO_OUTPUT_REASON`).

The predecessor remains the live `CANDIDATE`; no new Artifact exists.

## 5. Plan integrity and resumption

For a running rework, the Task list must have this shape:

`original tasks (one per request) + zero or one final-producer Task per counted rework iteration`.

Every appended Task must match the final request's `workflow_step_ref` and `agent_ref`. There may
not be more appended Tasks than `rework_count`. A violation raises `RunResumptionError` before the
Director mutates the Run.

Commit boundaries:

1. `CHANGES_REQUESTED` plus Run `RUNNING`;
2. rework Task `RUNNING`, before the external call;
3. terminal rework Task + Output + complete version replacement;
4. Run `WAITING_QA`;
5. existing QA open/verdict/route boundaries.

A committed Task outcome/version is reused. A `RUNNING` rework Task is retried on its stored input,
bounded by `TaskRetryPolicy`. A committed successor is never recreated.

## 6. Compatibility

- `Run`, Task, Artifact, Evaluation and Human Review public signatures are unchanged.
- Run snapshot and SQLite codec format stay unchanged.
- `ContentDirector` constructor and public method signatures stay unchanged.
- Existing QA risk behaviour before a human decision stays unchanged.
- Runs with no rework behave exactly as before.
