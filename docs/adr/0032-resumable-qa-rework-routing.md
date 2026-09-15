# ADR-0032: Resumable QA rework routing through a new Artifact version

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

The domain already describes the complete rework shape, but the application stops halfway:

- a QA `FLAGGED` / `FAILED` verdict is fail closed and opens a Human Review for escalation
  (ADR-0018);
- the reviewer can record `CHANGES_REQUESTED` with instructions (ADR-0007);
- `Run` admits `WAITING_HUMAN -> RUNNING`, counts and bounds that transition, and can create a
  successor through `create_artifact_version` (ADR-0019);
- the stored Run can already preserve the resulting Tasks, Outputs, version chain, Evaluations and
  Reviews (ADR-0024/0026).

`ContentDirector.resume`, however, deliberately leaves every Run with a Human Review untouched.
The accepted decision therefore cannot cause work: the producer is not called again, the reviewed
Artifact remains the live candidate, and no fresh QA lifecycle can begin. This is the next ordered
Stage 7 task in `CLAUDE.md` and the application-side gap deferred by ADR-0007, ADR-0018 and
ADR-0019.

The route must preserve the higher-level constraints: risk still stops and reaches a human by
default (`PROJECT.md` §4.9, §10, §12); the orchestrator, not an Agent, chooses the route (§4.2);
successful earlier Workflow steps are not repeated (§4.5–§4.6); a rework is a real Task with a
real Output and metrics; and every boundary around an external call remains recoverable.

## Decision

### 1. `CHANGES_REQUESTED` is the deterministic application trigger

ADR-0018's risk route is unchanged: a terminal risk verdict moves the Run to `WAITING_HUMAN` and
opens a review. On `resume`, the Content Director inspects the **latest** review of the current
candidate:

- `PENDING`, `APPROVED` and `REJECTED` do not start rework in this slice;
- `CHANGES_REQUESTED` moves `WAITING_HUMAN -> RUNNING` and starts one rework iteration.

This makes a QA risk actionable only after the required human escalation has supplied an explicit
decision. It also works for any future ordinary Approval Gate that records `CHANGES_REQUESTED`.
Routing `REJECTED` remains separate because ADR-0019 deliberately makes `REJECTED` Artifact
versions terminal and excludes superseding them.

If `Run` refuses the re-entry because `ReworkPolicy` is exhausted, the Director routes the still
non-terminal Run to `FAILED` with the stable reason `REWORK_LIMIT_REASON`. No executor is called.

### 2. Rework only the producer of the current candidate

One rework iteration appends exactly one Task. It reuses the `workflow_step_ref` and `agent_ref` of
the Task whose Output produced the reviewed candidate. Earlier Workflow steps and their committed
Outputs are retained and never called again. The role is resolved through the existing executor
and authoritative Schema maps, so normal structured-output validation, Skill/Tool behaviour and
per-call analytics apply unchanged.

The Task input is deterministic UTF-8 JSON with this versioned shape:

```json
{
  "artifact": {"content": "...", "ref": "...", "version": 1},
  "human_instructions": "...",
  "qa_flags": ["..."],
  "rework_iteration": 1
}
```

The content and identity come from the reviewed Artifact, QA flags come from its latest `qa`
Evaluation, and instructions come from the triggering Review. JSON preserves field boundaries and
exact feedback without inventing another domain entity or changing the current string Task-input
port. The fixed input is stored on the Task; a restart always reuses that stored value rather than
reconstructing it.

### 3. A successful rework Output creates the successor atomically

After the executor answers, the Director records analytics, Task outcome and validated Output
through the existing `finish_task` path. If the Task succeeded with an Output, it calls
`Run.create_artifact_version(previous_artifact_id, output_id, by=CONTENT_DIRECTOR)`. The
predecessor becomes `SUPERSEDED` and the successor starts as `DRAFT`; they are committed together
with the terminal Task outcome as one orchestration step.

The successor inherits the predecessor's `kind` through the existing default. It then follows the
ordinary gate: Run moves to `WAITING_QA`, the successor becomes `CANDIDATE`, gets a new Evaluation,
and either passes or is escalated again. Reviews and Evaluations of the predecessor never carry
over because they target its id.

A failed rework Task routes Run to `FAILED` by the existing Task-failure rule and leaves the
predecessor live. A successful Task without a validated Output cannot create a version, so Run
fails with the stable `REWORK_NO_OUTPUT_REASON`; the predecessor is not superseded.

### 4. Rework is recoverable from Run state alone

The storage boundary gains these commit points, all through the existing optional `RunStore`:

1. the human decision and `WAITING_HUMAN -> RUNNING` re-entry;
2. the rework Task `RUNNING`, before its executor is called;
3. terminal Task + Output + predecessor `SUPERSEDED` + successor `DRAFT`;
4. `RUNNING -> WAITING_QA`;
5. the existing QA checkpoints and verdict routing.

`rework_count` identifies that a `RUNNING` Run is in the rework path. The original plan occupies
the first `len(requests)` Task positions; each rework iteration may append one Task matching the
plan's final producer. Resumption accepts only that shape. A mismatch raises `RunResumptionError`
before a new mutation, and a committed terminal rework Task / successor is reused rather than
duplicated. An uncommitted executor result remains at-least-once, exactly as ADR-0026 specifies.

No snapshot field, codec version, Run signature, Task signature or Composition Root signature
changes.

## Deferred

- Direct automatic rework without a human decision; configurable choice between rework and
  escalation for particular QA flags.
- `REJECTED` routing and the domain decision about whether a rejected version may be superseded.
- A dedicated structured Task-input type. The current execution port accepts `str`; the JSON
  envelope is an application convention pinned by this ADR.
- Selecting an earlier targeted Workflow step, or replaying a downstream suffix. The current
  final-candidate gate reworks only that candidate's producer.
- The real QA Agent and production Human Review adapter round-trip (ROADMAP Stages 8 and 10).

## Consequences

### Positive

- QA feedback and human instructions now cause traceable work and a genuinely new Artifact
  version without weakening fail closed or Human Approval.
- Earlier successful calls are not repeated; the rework call uses all existing validation,
  Tool/Skill and analytics wiring.
- A crash at every new boundary resumes from the Run's own truth without forking the Artifact
  chain or duplicating committed work.
- The aggregate and persisted snapshot contracts remain unchanged.

### Negative / Trade-offs

- Rework Tasks extend the positional Task list beyond the original Workflow plan. The Director
  must validate the explicit `base plan + one final-producer Task per rework iteration` shape.
- The executor receives a versioned JSON envelope through a string port. A future typed input port
  will need an explicit migration.
- `APPROVED` and `REJECTED` human decisions remain waiting states in the current minimal Director;
  this slice closes only the ordered `CHANGES_REQUESTED` task.

## Alternatives considered

- **Automatically rework every QA risk before human escalation.** Rejected: `PROJECT.md` makes
  human escalation the fail-closed default, and ADR-0018 already fixes that observable route.
- **Repeat the whole Workflow.** Rejected: it duplicates committed work and cost even though the
  reviewed candidate identifies the producer that needs correction.
- **Mutate the original Task or Artifact.** Rejected: both are immutable historical facts; a real
  re-execution must create a Task, Output and Artifact version.
- **Let a rework Agent choose the route.** Rejected: routing is deterministic Content Director
  responsibility, never prompt logic.
- **Keep rework progress in a side record.** Rejected: Run is the single state and audit unit;
  side progress can disagree with it after a crash.

## References

- `PROJECT.md`: §4.2, §4.5–§4.6, §4.9, §10, §12, §17
- `ARCHITECTURE.md`: §4, §5, §10, §13
- `ROADMAP.md`: Stage 7 / Milestone M2; Stage 8
- `DOMAIN_MODEL.md`: §6, §7, §9.1, §10
- `RUN_SPEC.md`: §4, §7–§8; `RUN_ACCEPTANCE.md`: RW-01, RW-03, RW-04
- ADR-0007, ADR-0018, ADR-0019, ADR-0026, ADR-0031
