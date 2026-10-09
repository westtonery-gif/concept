# ADR-0093: Setup tooling that does not need an AI assistant

- **Status:** Accepted
- **Date:** 2026-10-09
- **Deciders:** Maintainer ("репо фабрики не должно быть заточено только под клод — на других
  устройствах я работаю с фабрикой без клода, и настраивать окружение неудобно", 2026-10-09);
  Lead Architect
- **Amends:** the runbook's and `.env.example`'s statement that nothing loads `.env`
  (ADR-0026 era), for **entrypoints only**
- **Builds on:** ADR-0012 (the Composition Root takes an `environ` mapping)

## Context

Everything the factory needs to run is written down, but mostly in `CLAUDE.md` (≈1900 lines of
session notes for an AI assistant) and in per-department runbooks. A person on a second machine
had to know four things nobody said in one place:

1. **No entrypoint reads `.env`** — every variable had to be exported by hand
   (`set -a && source .env && set +a`), and a forgotten one surfaces as a failed build naming one
   variable at a time.
2. **The external tools are not checked up front.** A clip run needs `ffmpeg` built with `libass`
   (the stock Homebrew formula has none), `whisper-cli` and a GGML model, and optionally `fpcalc`;
   today each is discovered when a render or an index fails.
3. **There is no single setup path.** The README's quickstart describes Stage 1 and tells the
   reader a `.env` loader is "intentionally not part" of the project.
4. **Nothing says which variables belong to which department**, so "is this machine ready to cut
   clips / post / generate?" has no answer short of running it.

None of this is Claude-specific in the code — the core is provider-agnostic and every entrypoint is a
plain Python script — the coupling is in **where the knowledge lives**.

## Decision

1. **Entrypoints load `.env`; the core still does not.** `infrastructure/dotenv_file.py`
   `load_dotenv(path, environ)` parses `KEY=VALUE` (optional `export`, comments, matching quotes),
   **never overrides** a variable already in the environment (a shell export wins, as in every
   twelve-factor tool), and returns the names it set. Each `demo_*.py` / `factory_service.py` calls
   it from its `__main__` block only — never at import time, so tests that import an entrypoint
   see the environment they build, not the developer's `.env`. The Composition Root is unchanged:
   it still takes the `environ` it is given (ADR-0012).
2. **`python doctor.py [department ...]` answers "is this machine ready?"** A pure
   `check_environment(environ, ...)` in `infrastructure/environment_check.py` (binaries and file
   system are injected, so it is testable) returns checks grouped by department — `core`,
   `clipping`, `posting`, `generation` — each marked required or optional and carrying a
   **per-OS install hint** (Homebrew / apt). It reports presence, never a value, so its output is
   safe to paste. Exit code 1 only when a **required** check of a requested department fails.
3. **`scripts/setup.sh` is the one setup path:** finds Python ≥ 3.11, creates `.venv`, installs the
   package (`--no-dev` to skip tooling), creates `.env` from `.env.example` if missing, runs the
   doctor. POSIX `sh`, no assistant, no network beyond `pip`.
4. **`docs/SETUP.md` is the single human page** — requirements by department, the variables each
   one needs, and how to install each external tool on macOS and Linux; the README quickstart
   points to it; `AGENTS.md` is a short, tool-neutral pointer (any assistant or none), and
   `CLAUDE.md` is stated to be session notes, **not** required reading to run the factory.

## Acceptance

| Row | Proves |
|---|---|
| ENV-01 | `load_dotenv` reads plain, `export`ed, quoted and commented lines |
| ENV-02 | an already-set variable is never overridden; the names set are returned |
| ENV-03 | a missing file is no error and sets nothing |
| ENV-04 | a malformed line is skipped, the rest load |
| DOC-01 | a department with all required tools and variables is `ready`; one missing item makes it not |
| DOC-02 | a missing optional item does not fail a department |
| DOC-03 | a value is never part of the report; only names and presence |
| DOC-04 | the `subtitles` filter missing from ffmpeg is a named, explained failure for `clipping` |
| DOC-05 | install hints follow the platform (`brew` vs `apt`) |

## Consequences

- **Good:** a fresh machine goes `scripts/setup.sh` → edit `.env` → `python doctor.py clipping` →
  `python demo_cut.py …` with no assistant and no `source`. The README stops contradicting the code.
- **Not done, deliberately:** a different **LLM provider**. The text roles still call Anthropic
  (the only real `LLMClient`; the port is provider-neutral, ADR-0014/0016). A second provider is
  an adapter plus an ADR of its own, and the maintainer has not asked for one yet; this ADR only
  removes the *environment* friction. Windows is documented through WSL, not scripted natively.
