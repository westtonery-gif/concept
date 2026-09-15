# ADR-0018: Evaluation (QA) entity + the fail-closed QA gate on Artifact approval

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

`Evaluation` is the structured judgement "passed / risk / reject" on produced content
(`DOMAIN_MODEL.md` §2.13); its QA case is the quality/compliance gate of the health domain that
works **fail closed** (`PROJECT.md` §4 п.9, §10, §12; `ARCHITECTURE.md` §3.9, §13). It is a child
entity of the `Run` aggregate root (`DOMAIN_MODEL.md` §9.1). It was deferred by ADR-0005/0006/0007;
ADR-0007 already made "no Publish without Approve" real, but nothing yet makes "no Approve-able
content without a passing QA verdict" real — the `WAITING_QA` state is traversed without any
evaluation, so the health-domain gate is **fail open**.

The domain invariants to realise (`DOMAIN_MODEL.md` §6 "Evaluation"):
- an Evaluation belongs to **exactly one** Run and has **exactly one** verdict;
- a `Failed`/`Flagged` (risk) verdict obliges the system **not to pass the content further**
  (`fail closed`) and to escalate;
- the verdict is immutable once given; a re-evaluation is a **new** Evaluation (§2.13).

This blocks ROADMAP Stage 8 (QA Agent) and an honest MVP path. It mirrors ADR-0004…0007; the only
change to existing behaviour is the **intended, additive tightening** of the Artifact approval
gate (§5), declared here as ADR-0007 declared its own lifecycle extension. No infrastructure.

## Decision

### 1. Placement
Evaluation symbols live in `omemo_content_factory.domain.evaluation`: `EvaluationId`,
`EvaluationStatus`, `Evaluation`, `EvaluationView`, events and errors. The module depends only on
stdlib and `domain.errors`; its references (`run_id`, `artifact_ref`) are opaque `str`
(ADR-0003 §3). Created and decided **only via Run**.

### 2. Subject, attributes, states
- **Subject:** one **Artifact**, which must be `CANDIDATE` when the evaluation is opened (QA's
  input is "the artifact-candidate", `ARCHITECTURE.md` §3.9). Evaluating an Output is deferred.
- **Attributes** (`DOMAIN_MODEL.md` §2.13 mandatory set): id, `run_id`, `artifact_ref`, `kind`
  (opaque `str`, e.g. `"qa"`, cf. `Artifact.kind`), `status` (the verdict), `flags`
  (`tuple[str, ...]` — remarks / risk flags). Optional confidence score and criteria reference are
  deferred.
- **States — `EvaluationStatus`:** `PENDING, PASSED, FLAGGED, FAILED`. Created `PENDING`; decided
  exactly once to one terminal verdict. `PASSED` = "passed"; `FLAGGED` = "risk"; `FAILED` =
  "reject". Identity, `run_id`, `artifact_ref` and `kind` are write-once (slots + guarded
  `__setattr__`); only `status` + `flags` are set, once. Exposed only as `EvaluationView`.

### 3. Actor and ownership (Variant A)
No new actor. The verdict is produced **outside** the domain (a QA Agent behind an application
port, §6) and **persisted** by Run — the same evaluation-ownership model as Schema validation
(ADR-0013 §8, ADR-0014): *evaluator decides → application invokes → Run persists*. Per
`DOMAIN_MODEL.md` §10 the QA verdict is recorded "QA Agent (через Content Director)", so both
opening and recording require `CONTENT_DIRECTOR`; `AGENT` and `HUMAN_REVIEWER` are refused.

### 4. Run's additive public API
- `open_evaluation(artifact_id, *, kind, by) -> EvaluationId` — Content Director only; the target
  Artifact must be `CANDIDATE` (else `InvalidTransitionError`, as `open_human_review`); creates a
  `PENDING` evaluation. No event (`DOMAIN_MODEL.md` §10 names only the completion).
- `record_evaluation(evaluation_id, verdict, *, by, flags=()) -> None` — Content Director only;
  the evaluation must be `PENDING` and `verdict` terminal (else
  `InvalidEvaluationTransitionError`); emits `EvaluationCompleted`.
- read-only: `evaluations` (sequence of `EvaluationView`) and `evaluation(evaluation_id)`.

### 5. The fail-closed QA gate (the only behaviour change)
`transition_artifact(artifact, CANDIDATE → APPROVED)` keeps its ADR-0007 precondition (an
`APPROVED` Human Review targeting that Artifact → else `ArtifactNotApprovedError`, checked first)
and **additionally** requires that the **latest** Evaluation opened on that Artifact is `PASSED`,
else `ArtifactQaNotPassedError` (in `domain.artifact`, beside `ArtifactNotApprovedError`).

- *No evaluation*, a *`PENDING`* one, or a *`FLAGGED`/`FAILED`* one all keep the gate shut — absence
  of a verdict is never a pass (fail closed).
- *Latest wins:* a later risk verdict overrides an earlier pass; a later pass after rework
  re-opens the gate (re-evaluation = new Evaluation).
- A human `Approve` **cannot** override a risk verdict: escalation brings the human in to decide
  `Reject` / `Request changes` with the flags as context, but risky health content is not
  approvable until a new evaluation passes. "No content further on risk" (§6) holds by
  construction, and, because `PUBLISHED` is reachable only from `APPROVED`, so does "nothing
  published without a passed QA verdict".
- `CANDIDATE → REJECTED` needs neither gate (rejecting is always safe). The Run state machine
  (`Run.transition`, ADR-0003) is **not** changed.

### 6. Application layer — the QA port
`application/qa_evaluation.py`: `EvaluationResult(verdict, flags)`, the `ArtifactEvaluator`
protocol (`evaluate(content) -> EvaluationResult`) and
`evaluate_artifact(run, evaluator, artifact_id, *, kind="qa") -> EvaluationId`, which opens the
evaluation, invokes the evaluator on the Artifact's content and records the verdict. It **never
catches**: an evaluator failure propagates (`PROJECT.md` §10 — no swallowed errors) and leaves the
evaluation `PENDING`, i.e. the gate shut. There is no default verdict.

### 7. Orchestration — `ContentDirector` routes by the verdict
`ContentDirector` gains an optional `qa: ArtifactEvaluator`. Without it, behaviour is unchanged.
With it, after all Tasks succeed and the Run enters `WAITING_QA`, the **final step's** Artifact is
moved `DRAFT → CANDIDATE` and evaluated (intermediate Artifacts stay `DRAFT`):
- `PASSED` → the existing route continues (`WAITING_HUMAN` → `COMPLETED`);
- `FLAGGED` / `FAILED` → **forced escalation** (`PROJECT.md` §12): the Run moves to
  `WAITING_HUMAN`, a Human Review is opened on the candidate, and the director stops — the Run is
  never `COMPLETED` on a risk verdict;
- no candidate (the final step produced no Output) → `FAILED` with reason
  `"QA gate: no candidate artifact to evaluate"` — nothing to evaluate is not a pass.

The wiring lives in `ContentDirector`, where the `WAITING_QA` step is requested — not in
`execute_task`, which runs a single Task and has no gate (`ROADMAP.md` Stage 8: "QA Agent как шаг
`Waiting QA`").

### 8. Events
`EvaluationCompleted(run_id, evaluation_id, artifact_ref, verdict, flags)` is recorded in the Run's
single event log (union extended additively with `EvaluationEvent`).

### 9. Errors
Co-located in `domain.evaluation`: base `EvaluationDomainError(DomainError)` (ADR-0017) +
`InvalidEvaluationTransitionError` and `ImmutableEvaluationAttributeError`. The gate raises
`ArtifactQaNotPassedError(ArtifactDomainError)`. Authorisation reuses Run's
`UnauthorizedActorError`.

## Deferred
- The real QA Agent (prompt, schema of the verdict, health-compliance rules) — ROADMAP Stage 8.
- Rework routing on a risk verdict (`WAITING_QA → RUNNING` with a new Artifact version) — needs
  Artifact versioning (`SUPERSEDED`, next task).
- Gating `open_human_review` on a decided evaluation; showing flags to the reviewer through the
  review itself (they are readable via `Run.evaluations`).
- Evaluating an Output; confidence score; criteria/prompt-version reference; audit timestamp.
- The existing `ContentDirector` shortcut `WAITING_HUMAN → COMPLETED` without a Human Review on the
  `PASSED` route predates this ADR and is left for the Stage 7/12 orchestrator.

## Consequences

### Positive
- The health-domain `fail closed` invariant becomes real and enforced by the aggregate root: no
  Artifact is approvable — hence publishable — without a passing QA verdict.
- `WAITING_QA` becomes meaningful; the QA Agent (Stage 8) only has to implement one port.
- Everything stays through the root, immutable input, no infrastructure; the error hierarchy stays
  rooted at `DomainError`.

### Negative / Trade-offs
- Approving an Artifact now needs a QA evaluation: the one existing test that approves an Artifact
  gains a `PASSED` evaluation (intended change, as ADR-0007 changed the Artifact tests).
- Run's public API and event union grow (additively); the approval gate becomes a two-entity check.

## Alternatives considered
- **Gate `Run.transition(WAITING_QA → WAITING_HUMAN)` on a passed evaluation** — rejected: changes
  Run's reference state machine (forbidden by the conventions) and would block the escalation to
  the human that `PROJECT.md` §12 requires on risk.
- **Gate only `open_human_review`** — rejected as the primary gate: a risk verdict must still reach
  the human (escalation), and it would not stop an approval recorded before QA ran.
- **Let a human `Approve` override a risk verdict** — rejected: contradicts "risk → not further"
  (`DOMAIN_MODEL.md` §6) for health content; the human's path is Reject / Request changes → rework
  → a new, passing evaluation.
- **Catch evaluator failures and record `FAILED`** — rejected: swallows a technical failure into a
  domain verdict (`PROJECT.md` §10); leaving it `PENDING` is already fail closed.
- **Evaluation as its own Aggregate Root** — rejected: contradicts `DOMAIN_MODEL.md` §9.1.

## References
- `DOMAIN_MODEL.md`: §2.13 (Evaluation), §6 (invariants), §7, §9.1, §10 (`EvaluationCompleted`),
  §11 (Verdict, Risk Flag)
- `PROJECT.md`: §4 п.9, §10, §12, §4.11, §17
- `ARCHITECTURE.md`: §3.9 (QA), §4 п.3 (routing by verdicts), §13
- `ROADMAP.md`: Stage 8
- ADR-0003/0004/0005/0006/0007 (templates this mirrors), ADR-0013 §8 (Variant A), ADR-0017
- `EVALUATION_SPEC.md`, `EVALUATION_ACCEPTANCE.md`
