# ADR-0038: Wiring the QA evaluator into the Composition Root and an entrypoint, and how a QA failure surfaces

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect
- **Resolves:** ADR-0034 §6 (how the Director surfaces a propagated QA error); ADR-0036 "Deferred"
  (building `LLMArtifactEvaluator` from `qa_agent@v1` and wiring it)

## Context

Subtask 11.4 (`CLAUDE.md` queue) connects the pieces Stage 8 already has: the QA role's static
assets (`agents/qa_agent.py`, `qa-agent` v1, `qa-verdict@v1`; ADR-0035), the model-backed evaluator
(`LLMArtifactEvaluator`; ADR-0036) and the Director's fail-closed gate (ADR-0018) with rework routing
(ADR-0032). Two things are missing:

1. **Nothing builds the evaluator from its catalogue entry.** The Composition Root resolves
   `agent_ref → Prompt → Schema` only for Task executors (`build_executor_map`), and ADR-0035 §4
   forbids passing the QA Agent there: its answer is a verdict, not an Output.
2. **A QA failure has no decided outcome in a real run.** A malformed answer (`QaVerdictError`) or a
   failed model call (`QaCallError`) already propagates out of `execute` / `resume` after the Run is
   committed with the Evaluation `PENDING` and the calls recorded (ADR-0036 §3, `LAE-06`). ADR-0034
   §6 left open whether that is the outcome, or whether the Director should route the Run to
   `FAILED` with a stable reason, as ADR-0033 did for an `INVALID` Output.

Facts that bear on (2):

- `FAILED` is terminal: no transition leaves it and `resume` leaves a terminal Run alone. Everything
  committed before the gate — every producer Task, Output and Artifact, all paid for — can no longer
  reach a human.
- A QA failure is about the **answer**, not the candidate. Both causes are typically transient: an
  `LLMError` is a network/provider fault; a model that breaks the verdict grammar once usually does
  not on the next call. Neither says anything about the content.
- `WAITING_QA` with a `PENDING` Evaluation is already the state a crash during the QA call leaves, and
  `resume` already finishes that same Evaluation without opening a second one (ADR-0026 §3,
  `SWR-06`). The approval gate treats `PENDING` as "not passed" (ADR-0018 §5).
- An `INVALID` Output (ADR-0033) is different in kind: it is a recorded domain fact about the
  produced content, and passing it on would corrupt the plan. A QA failure leaves no fact about the
  content at all.
- Evaluations have no attempt model (ADR-0036 "Deferred"), so the Director has nothing to bound an
  automatic retry with.

## Decision

### 1. A QA failure leaves the Run resumable at the gate

When the injected evaluator raises, the Content Director does **not** change the Run's state: it
stays `WAITING_QA`, the Evaluation stays `PENDING` with its `evaluator_ref`, a
`MeasuredEvaluatorError`'s calls are recorded and committed (ADR-0036 §3), and the **same exception
propagates** to the caller (PROJECT.md §10 — no swallowed errors). There is no automatic retry and no
failure reason. `resume` asks the evaluator again on the same Evaluation; the first verdict is routed
as usual (`PASSED` → human gate, risk → escalation, ADR-0018 §7).

This makes the already-implemented behaviour the decision; the Director's code does not change. The
gate stays fail closed in every branch: no verdict, no approval.

### 2. The Composition Root builds the evaluator from the role catalogue

`composition.py` gains

```python
build_qa_evaluator(agent, prompts, client, schemas, *, available_tools=None) -> LLMArtifactEvaluator
```

the verdict-side counterpart of `build_executor_map` for **one** Agent:

- resolves `agent.prompt_ref → Prompt → schema_ref → Schema` by the same structural lookups
  (unknown Prompt / Schema → `CompositionError`); `prompts=None` reads the bundled store (ADR-0030);
- injects `system_prompt` / `user_template` from the Prompt, `output_fields` = the Schema's
  `required_fields` (ADR-0014 §3), `prompt_ref = <prompt_id>@v<version>` (ADR-0029),
  `evaluator_ref = agent.agent_id` (ADR-0036 §1) and a `Toolbox` scoped by `agent.tool_refs` over the
  injected Tool library (ADR-0028; empty for `qa_agent@v1`);
- refuses an Agent that declares `skill_refs` with `CompositionError`: the QA path has no Skill
  invocation seam (ADR-0035 §4), and silently dropping a declared Skill would misrepresent the role.

A shape without `verdict` / `flags` is rejected by the evaluator's own construction invariant
(`ValueError`), exactly as an empty executor shape is (ADR-0014 §3): the Root passes parameters and
does not judge configuration. The model is not called at build time.

The per-call client stays a caller concern, as for executors (PROVIDER_MODEL_SPEC §4): an entrypoint
passes `client_for_role(qa_agent@v1, …)`.

### 3. The compiled Director can carry it; the QA role is never a Workflow step

`build_content_director` and `compile_runtime` gain an additive keyword `qa: ArtifactEvaluator | None
= None`, handed to `ContentDirector(qa=…)`. A new structural check,

```python
validate_qa_evaluator(workflow, qa) -> None
```

raises `CompositionError` when a Workflow step's `agent_ref` equals `qa.evaluator_ref`, enforcing
ADR-0035 §4 at build time instead of by convention. `compile_runtime` calls it; a caller that
assembles the Director itself (`demo_factory.py`) calls it directly, as it already calls
`validate_workflow_executors`.

### 4. `demo_factory.py` runs the gate on a real model

- The QA role gets its own `client_for_role` binding with explicit pricing; its missing variables
  are printed with Rin's and Leo's.
- The Director is built with `qa=build_qa_evaluator(qa_agent.QA_AGENT, None, <QA client>,
  qa_agent.SCHEMAS)`, checked with `validate_qa_evaluator`.
- A QA failure (`MeasuredEvaluatorError`) is caught **at the entrypoint only**: the demo says the Run
  stays at `WAITING_QA` and that re-running asks QA again, then shows the stored Run and its model
  calls. Any other exception still propagates.
- The operator can play the human reviewer for the live rework check: `python demo_factory.py
  --request-changes "<instructions>"` submits `CHANGES_REQUESTED` (as `HUMAN_REVIEWER`) on the stored
  Run's pending Review, commits, and resumes — which drives ADR-0032's rework off the real model's
  flags. It refuses, changing nothing, when there is no stored Run or no pending Review. The demo
  prints each Evaluation (status, evaluator, flags) and each Review.

Approving or rejecting from the demo is not added: the Director does not route those decisions yet
(Stage 10), so the command would have no effect to observe.

## Consequences

### Positive

- A transient QA fault costs one repeated QA call, never the whole Run's committed work.
- A crash during the QA call and a QA error raised during it leave the same state and resume the
  same way — one recovery path.
- The QA role is compiled from its catalogue like every other role; misuse as a step or a declared
  Skill with no seam fails before any Run executes.
- The first model-produced risk verdict, and rework driven by it, can be exercised end to end from a
  real entrypoint.

### Negative / Trade-offs

- A QA evaluator that fails deterministically (e.g. a model that never follows the grammar) leaves
  the Run parked at `WAITING_QA`; each `resume` spends another call. Bounded today only by the
  operator. An attempt model on Evaluations (ADR-0036 "Deferred") is the place to add a limit and a
  terminal route.
- A caller of `execute` / `resume` has to handle `MeasuredEvaluatorError` to present it; the Director
  gives no stable status reason for it.

## Deferred

- An attempt count on Evaluations, and with it a bounded automatic retry and a terminal
  "QA unavailable" route (together with ADR-0036's `retries` for QA records).
- A repair prompt quoting the grammar violation (ADR-0034 "Deferred").
- Human decisions other than `CHANGES_REQUESTED` driven from an entrypoint (Stage 10, `ReviewDesk`).
- Stage 8 acceptance through `compile_runtime` with production assets (subtask 11.5).

## Alternatives considered

- **Route a QA failure to `FAILED` with a stable reason (the ADR-0033 shape).** Rejected: `FAILED`
  is terminal, so a transient provider fault would discard every committed producer result; and
  unlike an `INVALID` Output, the failure is no fact about the content.
- **Swallow the error in the Director and return with the Run at `WAITING_QA`.** Rejected: the
  caller could not tell a parked gate from a normal stop without inspecting Evaluations, and PROJECT.md
  §10 forbids swallowing errors. The entrypoint is the right place to present it.
- **Retry the QA call automatically inside the Director.** Rejected for now: no attempt model to bound
  it and no persisted count, so a restart would reset any in-memory limit.
- **Map a malformed answer to `FLAGGED` so a human sees it.** Rejected by ADR-0018 and ADR-0034 §6: a
  technical failure is never turned into a verdict.
- **Build the evaluator inside `build_executor_map` when the Schema is the verdict Schema.**
  Rejected: that function yields Task executors keyed for Workflow steps; mixing a verdict role in
  would contradict ADR-0035 §4 and make the map's type ambiguous.

## References

- `PROJECT.md`: §10, §12, §16
- `ROADMAP.md`: Этап 8
- ADR-0014 §3, ADR-0018 §5-§7, ADR-0026 §3, ADR-0028, ADR-0029, ADR-0030, ADR-0032, ADR-0033,
  ADR-0034 §6, ADR-0035 §4, ADR-0036
- `EVALUATION_SPEC.md` §8.4; `EVALUATION_ACCEPTANCE.md` §4.4 (`QWR`)
