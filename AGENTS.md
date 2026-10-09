# For anyone — or any tool — working in this repository

You do **not** need Claude, or any particular AI assistant, to run or develop this project. It
works from a plain terminal, and equally from Cursor, Copilot, Aider, Codex or a human with an
editor. The factory's own text roles can use **any** model provider (ADR-0094).

- **Run it:** [docs/SETUP.md](docs/SETUP.md) — `scripts/setup.sh`, then
  `python configure_llm.py <provider> --model <name> ...`, then `python check_llm.py`.
- **Understand it:** [PROJECT.md](PROJECT.md) → [ARCHITECTURE.md](ARCHITECTURE.md) →
  [ROADMAP.md](ROADMAP.md), and [docs/adr/](docs/adr/README.md) for why each decision was made.
  The documents win over the code (PROJECT.md §17).
- **Check a change:** `ruff check . && ruff format --check . && mypy && pytest` (the local gate).
- **Conventions:** spec/ADR before code; extend the core additively, never change `Run`'s existing
  behaviour (see [CONTRIBUTING.md](CONTRIBUTING.md)); no rate, model or budget is ever hardcoded.
- **Never** put an API key in a file that is committed; keys live in `.env` (git-ignored) or the
  shell, and nothing prints them.

[CLAUDE.md](CLAUDE.md) is the running session log kept for Claude Code. It is useful history
(what was built, what was found, what is queued) but nothing in it is required to use the factory.
Cursor users: `.cursor/rules/` carries the same rules in Cursor's format.
