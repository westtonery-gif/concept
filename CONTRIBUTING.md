# Contributing

This project follows the engineering discipline defined in `PROJECT.md`
(sections 6 and 11) and `ROADMAP.md`. Please read those before contributing.

## Source of truth

`PROJECT.md` → `ARCHITECTURE.md` → `ROADMAP.md` → `ADR` → code. Code must not
contradict any level of the documentation. On conflict, the documentation wins
(PROJECT.md, section 17). Architectural changes require an
[ADR](docs/adr/README.md).

## Per-change workflow

Every change follows **Build → Test → Commit → Push**:

1. **Build** — implement the smallest coherent change for the current ROADMAP
   stage. Do not implement work that belongs to a later stage.
2. **Test** — add/keep tests; run the full local quality gate (below) green.
3. **Commit** — small, atomic [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:` …).
4. **Push** — push straight to `main` once the local gate is green. CI
   (`.github/workflows/ci.yml`) also runs on every push to `main` as a
   post-hoc safety net.

## Branching

- `main` is not protected — a green local quality gate is the gate. Direct
  pushes to `main` are the normal path; a solo maintainer working with AI
  assistants doesn't need a PR as a review checkpoint for most changes (a
  reviewed-only-in-hindsight trunk, verified by CI on every push, not before
  it).
- A short-lived feature branch + PR is still **available**, not required —
  use one when a change is large, risky, or you specifically want CI green
  *before* it lands on `main` rather than after (e.g. it changes `Run`'s
  public contract, or several sessions are touching the repo at once and a
  PR avoids interleaving unfinished work).

## Local quality gate

Run all of these before opening a PR (CI runs the same checks):

```bash
ruff format --check .   # formatting
ruff check .            # linting
mypy                    # static typing (strict)
pytest                  # tests
```

`ruff format .` (without `--check`) applies formatting locally.

## Pre-commit hooks

`pre-commit` is the **first level** of quality control (ADR-0002 Revision 1): it
runs the approved checks automatically before each commit so obvious errors never
enter the repository. It does **not** replace CI — CI remains the authoritative
gate.

Set it up once per clone (the `pre-commit` package ships in the `dev` extra):

```bash
pip install -e ".[dev]"   # installs pre-commit and the hook tools
pre-commit install        # registers the git hook
```

On `git commit` the hooks run **Ruff Format**, **Ruff Check** and **mypy**. If a
hook fails or reformats files, the commit is aborted — fix or re-stage the
changes (`git add ...`) and commit again. Run them on demand with:

```bash
pre-commit run --all-files
```

Avoid `git commit --no-verify`: CI runs the same checks and will fail the PR.

## Scope discipline

Do not add agents, skills, tools, adapters, workflows, domain models,
integrations, APIs, databases or LLM code ahead of their ROADMAP stage. If a
change feels like "for the future", it belongs to a later stage.
