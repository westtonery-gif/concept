# CLAUDE.md — Concept Content Factory

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

**Running things** (not normative, but read before touching either department in anger):
`CLIPPING_RUNBOOK.md` — how to cut an episode end to end, including the two things that bite a
first run (entrypoint scripts now load `.env`; Homebrew still needs to be on `PATH`);
`n8n/README.md` — the content factory's operator setup and the verification log for both.

`CONTENT_FACTORY_THOUGHTS.md` sits **outside** this hierarchy — explicitly non-normative
(says so in its own header), a product/architecture exploration of the full video-factory vision
reconciled against the repo as of commit `063cfde`. Read it for context and the proposed sequence
(§16), but it does not override anything above 6 and it does not itself authorize starting
new work by itself — see task 10 in [development notes](docs/history/development-notes.md).

## Current state (2026-10-09)

- The brief path is assembled: Notion → researcher → script writer → QA → human review.
  `factory_service.py` exposes it to the two n8n workflows.
- The clipping path indexes and renders local episodes, reviews each clip, and can publish
  approved clips through upload-post. Start with `CLIPPING_RUNBOOK.md`.
- Generated stories use separate CLI scripts for writing, dialogue, storyboards, frames and
  animation. `demo_animate.py join` produces a **silent** video; audio is mixed separately.
- Runs are stored in SQLite. Models, pricing and request budgets are configured per role.
  Python entrypoints load their sibling `.env`; shell exports win.
  `configure_llm.py` offers direct GPT/Claude setup with a shared default binding.
- Notion adapters share HTTP functions in `infrastructure/_notion.py` (ADR-0098).
- A fresh clone needs Python 3.11+ and its own environment. Old notes about an installed
  `.venv`, local media, keys, boards or active services do not establish that they exist here.

The old status reports and numbered backlog are in
[development notes](docs/history/development-notes.md). References to “task N” in existing
specs and comments refer to that archive. Check the current code before treating an old
open item as unfinished. Follow the user's current task rather than executing the archive.

## Session workflow (push straight to main — no branch/PR ceremony needed)

`main` is not protected (CONTRIBUTING.md "Branching", updated 2026-09-15 — PRs turned out to be
pure overhead for a solo maintainer + AI assistants, so the earlier "no direct pushes" rule was
dropped). For tasks from the archived queue:

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

## Quality gate

Set up and activate `.venv` as described in `README.md`; then run:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m pytest -q
```
