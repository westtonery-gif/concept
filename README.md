# Concept Content Factory

Industrial multi-agent content production system for **Concept** — a
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

A real model call needs `ANTHROPIC_API_KEY` plus explicit
`OMEMO_LLM_INPUT_PRICE_PER_MILLION`, `OMEMO_LLM_OUTPUT_PRICE_PER_MILLION` and
`OMEMO_LLM_PRICE_CURRENCY` values in the environment (optionally `OMEMO_LLM_MODEL` to choose the
model); missing configuration is explained and the demo exits cleanly. No QA, Human Review,
publication or external integrations are involved.

## Running the factory-role demo

`demo_factory.py` runs the same kind of end-to-end Workflow, but through the **actual catalogued
production roles** migrated from Main Core (ADR-0016) — Rin (`content_researcher@v1`) then Leo
(`script_writer@v1`), with Leo's script judged at the fail-closed QA gate by the QA role
(`qa_agent@v1`, ADR-0038) — assembled by the real Composition Root instead of `demo.py`'s
hand-written one-off prompts. Unlike `demo.py`, each role resolves **its own** provider/model via
`client_for_role` (ADR-0016) — there is no shared/default model:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OMEMO_PROVIDER__CONTENT_RESEARCHER_V1=anthropic
export OMEMO_MODEL__CONTENT_RESEARCHER_V1=claude-sonnet-4-6
export OMEMO_INPUT_PRICE_PER_MILLION__CONTENT_RESEARCHER_V1=REPLACE_WITH_CURRENT_DECIMAL_RATE
export OMEMO_OUTPUT_PRICE_PER_MILLION__CONTENT_RESEARCHER_V1=REPLACE_WITH_CURRENT_DECIMAL_RATE
export OMEMO_PRICE_CURRENCY__CONTENT_RESEARCHER_V1=USD
export OMEMO_PROVIDER__SCRIPT_WRITER_V1=anthropic
export OMEMO_MODEL__SCRIPT_WRITER_V1=claude-sonnet-4-6
export OMEMO_INPUT_PRICE_PER_MILLION__SCRIPT_WRITER_V1=REPLACE_WITH_CURRENT_DECIMAL_RATE
export OMEMO_OUTPUT_PRICE_PER_MILLION__SCRIPT_WRITER_V1=REPLACE_WITH_CURRENT_DECIMAL_RATE
export OMEMO_PRICE_CURRENCY__SCRIPT_WRITER_V1=USD
export OMEMO_PROVIDER__QA_AGENT_V1=anthropic
export OMEMO_MODEL__QA_AGENT_V1=claude-sonnet-4-6
export OMEMO_INPUT_PRICE_PER_MILLION__QA_AGENT_V1=REPLACE_WITH_CURRENT_DECIMAL_RATE
export OMEMO_OUTPUT_PRICE_PER_MILLION__QA_AGENT_V1=REPLACE_WITH_CURRENT_DECIMAL_RATE
export OMEMO_PRICE_CURRENCY__QA_AGENT_V1=USD
python demo_factory.py
```

A `passed` QA verdict completes the Run. A `flagged` / `failed` verdict stops it at `WAITING_HUMAN`
with a Review open; play the reviewer to drive a rework from the model's flags (ADR-0032):

```bash
python demo_factory.py --request-changes "add a source for the 70% figure"
```

If the QA call itself fails (provider error, or an answer that breaks the verdict grammar), the Run
stays at `WAITING_QA` and running the demo again asks QA again (ADR-0038).

Replace the rate placeholders with the provider's current per-million-token prices. Pricing is
explicit and per role; an Anthropic binding without both rates and a currency fails closed rather
than recording a guessed cost (ADR-0029).

The Run is saved after every step to `OMEMO_RUN_STORE_PATH` (default `.omemo/runs.sqlite3`,
git-ignored; ADR-0026). Running the demo again **resumes** that Run instead of starting over: an
interrupted run continues where it stopped, a finished one is only shown, and no model is called
for work already committed. Delete the file to start from scratch.

A role with no `OMEMO_PROVIDER__<ROLE>` binding fails closed
(`ProviderModelSelectionError`) — the demo prints the exact exports each role still needs and
exits cleanly, same as it does when `ANTHROPIC_API_KEY` is missing.

## Running the Notion demo

`demo_notion.py` runs the same real Rin → Leo → QA Workflow, but the brief comes from a real
Notion database instead of a hardcoded string — the first real "core reads its input from the
outside" path (ROADMAP Stage 9, ADR-0040). Everything else (roles, QA gate, rework, Run store) is
exactly `demo_factory.py`, reused rather than duplicated.

```bash
# same ANTHROPIC_API_KEY / OMEMO_PROVIDER__* / pricing exports as demo_factory.py, plus:
export OMEMO_NOTION_TOKEN=secret_...
export OMEMO_NOTION_DATABASE_ID=...
export OMEMO_NOTION_READY_PROPERTY=Stage
export OMEMO_NOTION_READY_VALUE="Ready for production"
export OMEMO_NOTION_RUN_STATUS_PROPERTY="Run status"
export OMEMO_NOTION_RUN_ID_PROPERTY="Run id"
python demo_notion.py <notion-page-id>
```

A brief that is not on the board, not marked ready, or has no text all print the same message and
exit cleanly (`BriefBoard.fetch_brief` returns `None` for each, deliberately indistinguishable —
ADR-0040 §3). `--request-changes "<instructions>"` works the same as in `demo_factory.py`.

**Not done here:** writing the Run's status back onto the Notion page (`BriefBoard.report_status`
exists and is tested, ADR-0040, but nothing calls it yet — which transitions to report is its own
design decision, CLAUDE.md queue subtask 13.3).

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
│       ├── adapters/           # Adapter contracts: RunStore, BriefBoard, ReviewDesk, AnalyticsSink
│       ├── application/        # ContentDirector + task execution + QA evaluation
│       └── infrastructure/     # LLM executor + SQLite RunStore + in-memory adapter stubs
├── demo.py                     # End-to-end demo of the domain via ContentDirector
├── demo_factory.py             # Same, but through the real catalogued Rin -> Leo roles
├── demo_notion.py              # Same, but the brief is fetched from a real Notion database
└── tests/                      # pytest suite
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Every change follows
**Build → Test → Commit → Review**, and architectural changes require an
[ADR](docs/adr/README.md).

## License

[MIT](LICENSE) © 2026 OMEMO Health
