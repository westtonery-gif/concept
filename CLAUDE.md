# CLAUDE.md — OMEMO Content Factory

Industrial multi-agent content production system (carousels, AI-video, articles).
Built bottom-up with a strict **spec-before-code** process. The architecture documents are
the source of truth — code must never contradict them; on conflict, the docs win
(PROJECT.md §17).

## Documentation hierarchy (source of truth, in order)
1. `PROJECT.md` — goals & principles (highest authority)
2. `ARCHITECTURE.md` — how the system is structured
3. `ROADMAP.md` — implementation order (stages + milestones)
4. `DOMAIN_MODEL.md` — entities, aggregates, events, invariants
5. Per-aggregate specs: `<NAME>_SPEC.md`, `<NAME>_ACCEPTANCE.md` (e.g. `RUN_SPEC.md`)
6. `docs/adr/` — Architecture Decision Records (technical decisions)
7. Code in `src/`, tests in `tests/`

## Current state (2026-09-15)
- **All 20 ADRs (0001–0020) are Accepted.** Run/Task/Output/Artifact/Human Review (0003–0007),
  Schema + Output validation (0008), Workflow (0009), Agent boundary + Prompt binding
  (0010/0011), Composition Root (0012), execution topology (0013), structured output (0014),
  Run restoration (0015), provider/model selection ownership (0016), shared `DomainError` base
  (0017), Evaluation/QA + fail-closed gate (0018), Artifact versioning (0019), Analytics Record
  (0020) are all implemented and tested. All gates green: ruff, ruff format, mypy --strict,
  pytest (357 passed, 0 skipped).
- **Analytics Record exists as a domain entity only** (ADR-0020, `domain/analytics.py`,
  `ANALYTICS_RECORD_SPEC.md`): `Run.record_analytics(task_id, …)` appends an immutable per-call
  record (provider/model, `TokenUsage`, `Cost` as `Decimal`, timezone-aware `TimeRange`);
  `run_id`/`task_id`/`agent_ref`/`retries` are derived from the Task, never passed in. **Nothing
  produces records yet** — the LLM port reports no usage/latency, so "no call without a record"
  (PROJECT §16) is documented but not enforced until ROADMAP Stage 14 (port change + pricing
  config + `execute_task` wiring, ADR-0020 "Deferred").
- **QA gate is fail closed** (ADR-0018, `domain/evaluation.py`, `EVALUATION_SPEC.md`): an
  Artifact reaches `APPROVED` only with an approving Human Review **and** a `PASSED` *latest*
  Evaluation — no/pending/`FLAGGED`/`FAILED` QA blocks it even after a human Approve.
  `ContentDirector(..., qa=evaluator)` evaluates the final step's Artifact at `WAITING_QA`;
  a risk verdict stops the Run at `WAITING_HUMAN` with an escalation review. No entrypoint
  wires a real QA evaluator yet — that is the QA Agent (ROADMAP Stage 8).
- **Domain errors share one root**: every per-aggregate base (`RunDomainError`,
  `TaskDomainError`, …) subclasses `DomainError` (`domain/errors.py`, ADR-0017). A new
  aggregate must root its own error base there; `tests/test_domain_error.py` enforces it.
- **Two real production roles are migrated and chained**: `content_researcher@v1` (Rin) and
  `script_writer@v1` (Leo) — `src/omemo_content_factory/agents/`. Each is proven alone
  (`tests/test_content_researcher_agent.py`, `tests/test_script_writer_agent.py`) and together
  as a real two-step Workflow (`tests/test_research_to_script_workflow.py`,
  `demo_factory.py`).
- **The Artifact lifecycle is fully wired** (ADR-0006/0007/0018/0019): `DRAFT→CANDIDATE→APPROVED→
  PUBLISHED`, `CANDIDATE→REJECTED` (approval gated by Human Review + QA) and, from every *working*
  state (`DRAFT`/`CANDIDATE`/`APPROVED`), `→SUPERSEDED`. A fixed version is immutable: rework goes
  through `Run.create_artifact_version(previous, output, by=…)`, which supersedes the predecessor
  and creates the successor (`version` + 1, `supersedes_ref`, its own Output) in one operation.
  `SUPERSEDED` is deliberately **not** reachable via `transition_artifact`
  (`ArtifactSupersessionError`). A new version inherits neither the predecessor's approval nor its
  QA verdict — the gates key on the artifact id, so rework restarts the lifecycle by construction.
  Orchestrating rework (ContentDirector routing a risk verdict into a re-run) is still open —
  ADR-0019 "Deferred", ROADMAP Stage 7/8.
- **`client_for_role` is wired into `demo_factory.py`** (ADR-0016 realization,
  `infrastructure/provider_model.py`): each role resolves its own provider/model via
  `OMEMO_PROVIDER__<ROLE>` / `OMEMO_MODEL__<ROLE>`, no shared/default client — a role with no
  binding fails closed (`ProviderModelSelectionError`), and the demo prints the exact exports
  still needed. `demo.py` predates the Rin/Leo migration and is untouched (out of scope, no
  catalogued Agent/Prompt/Schema to key a binding on).

## Session workflow (push straight to main — no branch/PR ceremony needed)

`main` is not protected (CONTRIBUTING.md "Branching", updated 2026-09-15 — PRs turned out to be
pure overhead for a solo maintainer + AI assistants, so the earlier "no direct pushes" rule was
dropped). For every task in the queue below:

1. Build → Test locally (the quality gate below) → Commit (Conventional Commits) directly on
   `main` (`git pull --ff-only` first if it's been a while since you last synced).
2. **Push straight to `origin main` — this is pre-authorized, do not stop to ask.** CI
   (`.github/workflows/ci.yml`) runs on the push as a post-hoc check; if it goes red, the next
   session's first job is fixing it forward (`git revert` only if a fix-forward isn't quick).
3. A short-lived branch + PR is still fine when *you* want CI green before landing (a large or
   risky change, e.g. one touching `Run`'s public contract), or when two sessions are working
   concurrently and a PR avoids interleaving unfinished work — but it's your call, not the
   default, and never something to ask permission for either way.

(History: PRs #1-#4 in this repo predate this rule and went through branch+PR; that was the
process at the time, not a pattern to keep copying.)

## Next tasks (ordered queue — one task per session; each ends Build → Test → Commit → Push/PR)
1. ~~Actualize this file~~ — done.
2. ~~Extract the shared `DomainError` base~~ — done (ADR-0017, `domain/errors.py`, merged via
   [PR #1](https://github.com/westtonery-gif/omemo-content-factory/pull/1)).
3. ~~Evaluation / QA entity~~ — done (ADR-0018, `domain/evaluation.py`,
   `application/qa_evaluation.py`; wired in `ContentDirector`, where `WAITING_QA` happens).
4. ~~Artifact versioning (`SUPERSEDED`)~~ — done (ADR-0019, `Run.create_artifact_version` +
   `domain/artifact.py`, `tests/test_artifact_versioning.py`; `artifact.py`'s stale module
   docstring fixed in the same change). The domain rework path exists now; **routing** a QA risk
   verdict / `CHANGES_REQUESTED` into a re-run that produces the new version is still open
   (ADR-0019 "Deferred", ROADMAP Stage 7/8).
5. ~~Analytics Record entity~~ — done (ADR-0020, `domain/analytics.py`, `Run.record_analytics`,
   `tests/test_analytics_record.py`; the long-skipped INV-07 integrity test in `tests/test_run.py`
   now runs). Scope check (the Stage-14 / scope-discipline concern raised here): ROADMAP Stage 2
   doesn't list it, but RUN_SPEC / RUN_ACCEPTANCE (AGG-05, INV-09) already make it part of the Run
   aggregate contract and ARCHITECTURE_FREEZE §3 asks for exactly ADR → SPEC → tests; it was kept
   domain-only (no capture, aggregation, adapter or agent) and the maintainer confirmed landing
   it. Capturing a record on every real call stays Stage 14 (ADR-0020 "Deferred").
6. ~~Wire `client_for_role` into a real entrypoint~~ — done (`demo_factory.py`: each role
   resolves its own provider/model via `build_executor_map` called once per agent + merged;
   `demo.py` untouched, out of scope).
7. ROADMAP Stage 4–6 (Skills library / Tool Layer / Adapter Layer) — broken down below into
   session-sized subtasks. Prerequisite for Stage 7 (first Agent through the full orchestrator)
   and the Stage 12 MVP.
   1. **Skills library** (Stage 4) — contract `Skill` (typed input/output, agent-agnostic, no
      `Run` access) + 2-3 first Skills (e.g. thesis extraction from a brief, terminology
      normalization, text segmentation — ROADMAP's own examples). ADR + code + tests.
   2. **Tool Layer** (Stage 5) — contract `Tool` (≠ Skill: invoked by an agent mid-reasoning,
      never manages the pipeline), a mechanism that scopes an agent to only its configured
      Tools, a couple of Tools needing no external service. ADR + code + tests.
   3. **Adapter contracts** (Stage 6a) — record that the **LLM Adapter is already done**
      (`LLMClient`/`AnthropicLLMClient`/`client_for_role`, ADR-0014/0016 — it already satisfies
      Stage 6's LLM Adapter DoD, just not labeled as such) and design the internal contracts for
      the remaining four: Storage, Notion, Google Docs, Analytics adapters. ADR + contracts only,
      no implementation yet.
   4. **Storage Adapter** (Stage 6b) — real persistence for `Run` and its children (everything
      is in-memory today). Likely the heaviest subtask here. ADR + code + tests.
   5. **Notion / Google Docs / Analytics adapter stubs** (Stage 6c) — contract + a
      fake/stub implementation only; full integration is explicitly Stages 9-11, not here
      (ROADMAP Stage 6: "полноценная интеграция — Этапы 9–11; здесь — контракт и базовая
      реализация/заглушка"). ADR + minimal code.

   After 7.5, Stage 7 (first real Agent through the full orchestrator, Milestone M2) becomes
   possible — that is the next queue item after this breakdown, not part of it.

See `DOMAIN_MODEL.md` (entities) and §9 (aggregate roots) for the domain shape of tasks 3–5.

## Conventions
- No code before its spec/acceptance/ADR exist. Significant decisions → an ADR.
- **Do NOT change Run's existing behaviour or signatures** (reference impl); extend it only
  *additively* and via an ADR, as ADR-0004…0007 did for its children. **Do NOT extract shared
  base classes prematurely** (rule of three). Extend by *adding* modules, not by modifying the
  core (PROJECT.md §4.11).
- Provider-agnostic: never hardcode a model; selection lives in config.
- An aggregate's public API is small: factory `create`, read-only properties, one guarded
  mutation method, domain events, domain errors (rooted at `DomainError`). Immutable input via
  `__slots__` + guarded `__setattr__`. Single guarded `transition` + declarative
  allowed-transitions table.

## Quality gate (all green before commit; a working `.venv` with Python exists)
Windows: `.venv/Scripts/python.exe`; macOS/Linux: `.venv/bin/python`.
```
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe -m pytest -q
```
