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
- **All 19 ADRs (0001–0019) are Accepted.** Run/Task/Output/Artifact/Human Review (0003–0007),
  Schema + Output validation (0008), Workflow (0009), Agent boundary + Prompt binding
  (0010/0011), Composition Root (0012), execution topology (0013), structured output (0014),
  Run restoration (0015), provider/model selection ownership (0016), shared `DomainError` base
  (0017), Evaluation/QA + fail-closed gate (0018), Artifact versioning (0019) are all implemented
  and tested. All gates green: ruff, ruff format, mypy --strict, pytest (328 passed, 1 skipped).
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
- `client_for_role` (ADR-0016 realization, `infrastructure/provider_model.py`) is implemented
  and tested, but **not yet used by any entrypoint** — `demo.py` and `demo_factory.py` both
  still take one shared client for every role via `OMEMO_LLM_MODEL` (task 6 below).

## Next tasks (ordered queue — one task per session; each ends Build → Test → Commit → Review)
1. ~~Actualize this file~~ — done.
2. ~~Extract the shared `DomainError` base~~ — done (ADR-0017, `domain/errors.py`).
3. ~~Evaluation / QA entity~~ — done (ADR-0018, `domain/evaluation.py`,
   `application/qa_evaluation.py`; wired in `ContentDirector`, where `WAITING_QA` happens).
4. ~~Artifact versioning (`SUPERSEDED`)~~ — done (ADR-0019, `Run.create_artifact_version` +
   `domain/artifact.py`, `tests/test_artifact_versioning.py`; `artifact.py`'s stale module
   docstring fixed in the same change). The domain rework path exists now; **routing** a QA risk
   verdict / `CHANGES_REQUESTED` into a re-run that produces the new version is still open
   (ADR-0019 "Deferred", ROADMAP Stage 7/8).
5. **Analytics Record** entity — new ADR + domain code. Needed for ROADMAP Stage 14
   (metrics), not before — lowest urgency of the domain gaps.
6. Wire `client_for_role` into a real entrypoint (e.g. `demo_factory.py`) so each role actually
   gets its own configured provider/model instead of one shared client. No Composition Root
   change needed — call `build_executor_map` once per agent with that agent's
   `client_for_role(agent_ref, os.environ)` client and merge the resulting dicts.
7. ROADMAP Stage 4–6 (Skills library / Tool Layer / Adapter Layer) — each needs its own ADR
   first; likely several sessions each. Prerequisite for Stage 7 (first Agent through the full
   orchestrator) and the Stage 12 MVP.

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
