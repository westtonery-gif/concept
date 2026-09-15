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
- **All 16 ADRs (0001–0016) are Accepted.** Run/Task/Output/Artifact/Human Review (0003–0007),
  Schema + Output validation (0008), Workflow (0009), Agent boundary + Prompt binding
  (0010/0011), Composition Root (0012), execution topology (0013), structured output (0014),
  Run restoration (0015), provider/model selection ownership (0016) are all implemented and
  tested. All gates green: ruff, ruff format, mypy --strict, pytest (261 passed, 1 skipped).
- **Two real production roles are migrated and chained**: `content_researcher@v1` (Rin) and
  `script_writer@v1` (Leo) — `src/omemo_content_factory/agents/`. Each is proven alone
  (`tests/test_content_researcher_agent.py`, `tests/test_script_writer_agent.py`) and together
  as a real two-step Workflow (`tests/test_research_to_script_workflow.py`,
  `demo_factory.py`).
- Artifact transitions actually wired: `DRAFT→CANDIDATE→APPROVED→PUBLISHED` and
  `CANDIDATE→REJECTED` (gated by Human Review, ADR-0007). Only `SUPERSEDED` (versioning) has
  no edges yet — the module docstring in `artifact.py` predates ADR-0007 and still says
  otherwise; fix it together with the versioning ADR (task 4 below), not standalone.
- `client_for_role` (ADR-0016 realization, `infrastructure/provider_model.py`) is implemented
  and tested, but **not yet used by any entrypoint** — `demo.py` and `demo_factory.py` both
  still take one shared client for every role via `OMEMO_LLM_MODEL` (task 6 below).

## Next tasks (ordered queue — one task per session; each ends Build → Test → Commit → Review)
1. ~~Actualize this file~~ — done by this edit.
2. Extract the shared `DomainError` base (rule-of-three follow-up; ADR-0005 §9) — small ADR +
   mechanical refactor across Task/Output/Artifact/Human Review/Schema/Workflow error hierarchies.
3. **Evaluation / QA entity** (`fail closed`; deferred by ADR-0005/0006/0007) — new ADR +
   SPEC/ACCEPTANCE + domain code + wiring into `application/task_execution.py` before the
   Human Review gate. Blocks ROADMAP Stage 8 (QA Agent) and an honest MVP path.
4. **Artifact versioning** (`SUPERSEDED`) — new ADR + domain code; fix `artifact.py`'s stale
   module docstring in the same change.
5. **Analytics Record** entity — new ADR + domain code. Needed for ROADMAP Stage 14
   (metrics), not before — lowest urgency of the domain gaps.
6. Wire `client_for_role` into a real entrypoint (e.g. `demo_factory.py`) so each role actually
   gets its own configured provider/model instead of one shared client. No Composition Root
   change needed — call `build_executor_map` once per agent with that agent's
   `client_for_role(agent_ref, os.environ)` client and merge the resulting dicts.
7. ROADMAP Stage 4–6 (Skills library / Tool Layer / Adapter Layer) — each needs its own ADR
   first; likely several sessions each. Prerequisite for Stage 7 (first Agent through the full
   orchestrator) and the Stage 12 MVP.

See `DOMAIN_MODEL.md` (entities) and §9 (aggregate roots) for the domain shape of tasks 2–5.

## Conventions
- No code before its spec/acceptance/ADR exist. Significant decisions → an ADR.
- **Do NOT change Run's existing behaviour or signatures** (reference impl); extend it only
  *additively* and via an ADR, as ADR-0004…0007 did for its children. **Do NOT extract shared
  base classes prematurely** (rule of three). Extend by *adding* modules, not by modifying the
  core (PROJECT.md §4.11).
- Provider-agnostic: never hardcode a model; selection lives in config.
- An aggregate's public API is small: factory `create`, read-only properties, one guarded
  mutation method, domain events, domain errors. Immutable input via `__slots__` + guarded
  `__setattr__`. Single guarded `transition` + declarative allowed-transitions table.

## Quality gate (all green before commit; a working `.venv` with Python exists)
```
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe -m pytest -q
```
