# OMEMO Content Factory

Industrial multi-agent content production system for **OMEMO Health** — a
managed, reproducible, human-in-the-loop pipeline for producing content.

> **Status:** ROADMAP **Stage 2 — domain models & contracts**.
> The domain core is implemented: the **Run** aggregate and its child entities
> **Task, Output, Artifact**, **Human Review** (ADR-0003…0007), the fail-closed QA
> **Evaluation** (ADR-0018), **Artifact versioning** for rework (ADR-0019) and the append-only
> per-call **Analytics Record** (ADR-0020), with a minimal
> `ContentDirector` and an LLM-backed task executor. Full agents, skills, tools,
> adapters and workflows arrive in later stages.

## Documentation (source of truth)

The architecture is governed by three charters, in strict order of authority:

| Document | Answers |
|----------|---------|
| [PROJECT.md](PROJECT.md) | **What** we are building and by which principles |
| [ARCHITECTURE.md](ARCHITECTURE.md) | **How** the system is structured |
| [ROADMAP.md](ROADMAP.md) | In **which order** we implement it |
| [docs/adr/](docs/adr/README.md) | **Why** each architectural decision was made |

Code must never contradict the documentation; on conflict, the documentation
wins (PROJECT.md, section 17).

## Requirements

- Python **3.11+**
- git

## Quickstart

Create a virtual environment and install the project with its dev tooling:

```bash
# bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

```powershell
# PowerShell (Windows)
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Configure the environment (optional — sensible defaults are used otherwise):

```bash
cp .env.example .env        # bash
Copy-Item .env.example .env # PowerShell
```

> At Stage 1 variables are read directly from the **process environment**
> (`OMEMO_APP_ENV`, `OMEMO_LOG_LEVEL`). A `.env` auto-loader is intentionally not
> part of Stage 1. Export the variables in your shell, or set them inline.

## Running the checks

The local quality gate mirrors CI exactly:

```bash
ruff format --check .   # formatting
ruff check .            # linting
mypy                    # static typing (strict)
pytest                  # tests
```

Use `ruff format .` to apply formatting, and `pytest --cov` for coverage.

## Using pre-commit

`pre-commit` is the **first level of quality control** (ADR-0002 Revision 1). It
runs the approved checks automatically each time you create a commit, so obvious
problems are caught **before** they enter the repository. It does **not** replace
CI — CI remains the authoritative gate (see [CONTRIBUTING.md](CONTRIBUTING.md)).

**1. Install the dependencies** (the dev extra includes `pre-commit` and the
tools the hooks use):

```bash
pip install -e ".[dev]"
```

**2. Install the git hooks** (one-time, per clone):

```bash
pre-commit install
```

**What happens on commit.** When you run `git commit`, pre-commit runs the three
approved checks on the project:

- **Ruff Format** — applies formatting;
- **Ruff Check** — lints the code;
- **mypy** — static type checking (strict).

If everything passes, the commit is created. If a check fails (or Ruff Format
reformats files), **the commit is aborted**.

**If a check fails:**

1. Read the hook output to see what failed.
2. If **Ruff Format** changed files, or you fix lint/type errors, **re-stage**
   the changed files (`git add ...`) and commit again.
3. Re-run the hooks manually at any time without committing:

   ```bash
   pre-commit run --all-files
   ```

> Bypassing the hooks (`git commit --no-verify`) is discouraged: CI runs the same
> checks and will fail the Pull Request anyway.

## Running the smoke entrypoint

A Stage 1 entrypoint that proves the package installs, imports and reads
configuration (no business logic, no external calls):

```bash
python -m omemo_content_factory
```

It logs the version, the active environment and the log level.

## Running the demo

`demo.py` runs one workflow — Research → Writer → Editor — through the real
`ContentDirector` and the Run/Task/Output/Artifact domain, with each role backed
by a live Anthropic model behind the application's `TaskExecutor` port:

```bash
python demo.py
```

A real model call needs `ANTHROPIC_API_KEY` in the environment (optionally
`OMEMO_LLM_MODEL` to choose the model); without a key the demo explains how to
set it and exits cleanly. No QA, Human Review, publication or external
integrations are involved.

## Running the factory-role demo

`demo_factory.py` runs the same kind of end-to-end Workflow, but through the **actual catalogued
production roles** migrated from Main Core (ADR-0016) — Rin (`content_researcher@v1`) then Leo
(`script_writer@v1`) — assembled by the real Composition Root instead of `demo.py`'s hand-written
one-off prompts. Unlike `demo.py`, each role resolves **its own** provider/model via
`client_for_role` (ADR-0016) — there is no shared/default model:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OMEMO_PROVIDER__CONTENT_RESEARCHER_V1=anthropic
export OMEMO_MODEL__CONTENT_RESEARCHER_V1=claude-sonnet-4-6
export OMEMO_PROVIDER__SCRIPT_WRITER_V1=anthropic
export OMEMO_MODEL__SCRIPT_WRITER_V1=claude-sonnet-4-6
python demo_factory.py
```

A role with no `OMEMO_PROVIDER__<ROLE>` binding fails closed
(`ProviderModelSelectionError`) — the demo prints the exact exports each role still needs and
exits cleanly, same as it does when `ANTHROPIC_API_KEY` is missing.

## Project layout

```
omemo-content-factory/
├── PROJECT.md                  # Charter: goals & principles (source of truth)
├── ARCHITECTURE.md             # Charter: system design
├── ROADMAP.md                  # Charter: implementation order
├── README.md                   # This file
├── CONTRIBUTING.md             # Workflow, branching, quality gate
├── LICENSE                     # MIT
├── pyproject.toml              # Project metadata + tool config (ruff/mypy/pytest)
├── .pre-commit-config.yaml     # Pre-commit hooks: Ruff Format, Ruff Check, mypy
├── .env.example                # Documented environment variables
├── .gitignore
├── .github/
│   └── workflows/
│       └── ci.yml              # CI: format → lint → types → tests
├── docs/
│   └── adr/                    # Architecture Decision Records
├── src/
│   └── omemo_content_factory/  # The import package (src layout)
│       ├── __about__.py        # Version (single source of truth)
│       ├── __init__.py
│       ├── __main__.py         # `python -m omemo_content_factory`
│       ├── config.py           # Env-based infrastructure configuration
│       ├── log.py              # Logging configuration helper
│       ├── py.typed            # PEP 561 typed marker
│       ├── domain/             # Domain core: Run, Task, Output, Artifact, Human Review, Evaluation
│       ├── skills/             # Skills library: Skill contract + deterministic one-task Skills
│       ├── tools/              # Tool Layer: Tool contract + per-agent Toolbox + model-invoked Tools
│       ├── application/        # ContentDirector + task execution + QA evaluation
│       └── infrastructure/     # LLM-backed task executor
├── demo.py                     # End-to-end demo of the domain via ContentDirector
├── demo_factory.py             # Same, but through the real catalogued Rin -> Leo roles
└── tests/                      # pytest suite
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every change follows
**Build → Test → Commit → Review**, and architectural changes require an
[ADR](docs/adr/README.md).

## License

[MIT](LICENSE) © 2026 OMEMO Health
