# ADR-0040: Notion Adapter — a real `BriefBoard` over the Notion REST API

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 9 asks for "a full implementation of the Notion Adapter" so that a brief filed in
Notion starts a Run and the Run's statuses land back on it. The session queue calls this task 13.1.
The following are already in place:

- `ADR-0023` §6 fixes the contract: `BriefBoard.fetch_brief(brief_ref) -> IncomingBrief | None` and
  `report_status(brief_ref, *, run_id, status)`. The shared adapter rules (`ADAPTER_SPEC.md` §3) say
  an implementation:
  - lives in `infrastructure/`;
  - raises its own technical error;
  - writes idempotently;
  - never changes a Run;
  - reads its credentials from the environment.
- `ADR-0025` built `InMemoryBriefBoard`, the reference for the cases the contract leaves open. A
  status reported on a brief the board does not have is refused with `BriefBoardError`.
- `ADR-0024` is the realization pattern to follow: build the real implementation first, and wire it
  in a later step (`ADR-0026`).

The queue leaves one choice to this task: use the official `notion-client` library or raw HTTP.
Today `pyproject.toml` depends only on `anthropic`.

## Decision

### 1. Raw HTTP through the stdlib, no new dependency
`NotionBriefBoard` (`infrastructure/notion_brief_board.py`) calls the Notion REST API through
`urllib.request`. The alternative, `notion-client`, was rejected for three reasons:

- **Three calls are all it needs:**
  - `GET /v1/pages/{id}` to read a page;
  - `GET /v1/blocks/{id}/children` to read the page body, paginated;
  - `PATCH /v1/pages/{id}` to write properties.
  A library would add a dependency (and its `httpx` transitive tree) to wrap three requests.
- **Precedent:** `ADR-0024` chose stdlib `sqlite3` over a DB layer, and `CONTRIBUTING.md` "Scope
  discipline" asks for no dependency ahead of need.
- **Testability:** the tests run the adapter's real HTTP code against a local HTTP server that
  plays Notion. No transport seam is needed, and nothing is mocked below the adapter.

The API version is pinned with the `Notion-Version: 2022-06-28` header. Under that version a
database page names its database as `parent.database_id`. Moving to a later version (data sources)
is a change to this one module.

### 2. How a Notion page maps to the contract
| Contract | Notion |
|---|---|
| `brief_ref` | the page id, opaque. It is percent-encoded into the path in full, so a ref cannot address another endpoint |
| "on the board" | a non-archived, non-trashed page whose `parent.database_id` is the configured database (dashes and case ignored) |
| "ready for production" | the configured readiness property, of type `status` or `select`, holds exactly the configured ready option name |
| `body` | the page's top-level blocks that carry `rich_text`, as plain text, one line per block, in order, across all pages of results |
| `report_status` | writes `status.value` and `run_id` into two configured `rich_text` properties of the page |

### 3. Decisions where the contract is silent
The rule of `ADR-0025` §3 applies: a technical or configuration fault is loud (`BriefBoardError`),
and a brief that simply is not producible is `None`.

**`fetch_brief` returns `None`** (no Run is started) when:
- the ref is blank;
- Notion answers `404`, which covers both an unknown page and a page not shared with the integration;
- the page is archived or in the trash;
- the page belongs to another database;
- the readiness option is empty or has another name;
- the page body has no text. A ready brief with nothing to produce from is not ready.

**`fetch_brief` raises `BriefBoardError`** when:
- the readiness property is missing, or is not `status`/`select`. That is a configuration fault, so
  it must not read as "not ready";
- Notion answers any other non-2xx status (`400`, `401`, `403`, `429`, `5xx`);
- the connection fails or times out;
- the response is not a JSON object, or its shape is not what the pinned version returns.

**`report_status` raises `BriefBoardError`, and sends no `PATCH`,** when:
- the ref is blank;
- the page is unknown (`404`), archived, in the trash, or belongs to another database;
- either status property is missing or is not `rich_text`.

This matches the stub, where the board refuses a status for a brief it does not have. A failed
`PATCH` is also `BriefBoardError`.

**Idempotency:** a report sets two property values, so repeating it rewrites the same values.
Nothing is appended, which satisfies `ADAPTER_SPEC.md` §3.5.

A report is always `GET` then `PATCH`. The extra read is the cost of refusing a foreign or
misconfigured page before writing.

### 4. Configuration from the environment, fail closed
`notion_settings_from_env(environ) -> NotionBoardSettings` reads six variables. All are required,
and a missing or blank one raises `BriefBoardError` naming every missing variable (names only,
never values):

| Variable | Meaning |
|---|---|
| `OMEMO_NOTION_TOKEN` | the integration's secret. Kept out of `repr`, and never part of a message |
| `OMEMO_NOTION_DATABASE_ID` | the briefs database |
| `OMEMO_NOTION_READY_PROPERTY` | the `status`/`select` property that marks a brief ready |
| `OMEMO_NOTION_READY_VALUE` | the option name that means ready |
| `OMEMO_NOTION_RUN_STATUS_PROPERTY` | the `rich_text` property that shows the Run status |
| `OMEMO_NOTION_RUN_ID_PROPERTY` | the `rich_text` property that shows the Run id |

There are no default property names. A database's schema belongs to the editorial team, and a
guessed name would fail later and less clearly (`PROJECT.md` "Fail closed при сомнении"). The
constructor also takes `api_url` (default `https://api.notion.com`, overridden only by tests) and
`timeout` (default 10 s).

### 5. Not wired yet
No entrypoint builds a `NotionBriefBoard`, and the Composition Root has no `build_brief_board`.
Wiring the Brief → Run entrypoint is task 13.2. Deciding which transitions to report is task 13.3.

## Deferred
- **Nested blocks:** children of toggles, columns and synced blocks are not read (`has_children`).
  Revisit when a real brief needs them.
- **Structured brief metadata:** content type, topic, requirements (`ARCHITECTURE.md` §3.1) become
  part of the Content Brief entity, which does not exist yet. Today `IncomingBrief` is a ref and
  text only.
- **Retry/back-off on `429`/`5xx`:** a failure is surfaced to the caller as `BriefBoardError`. No
  retry is attempted.
- **Discovering ready briefs** (a database query): the contract fetches by ref, and the trigger is
  manual at this stage (n8n is Stage 11).
- **Notion API versions after `2022-06-28`.**

## Consequences

### Positive
- The Stage 9 adapter exists with no new dependency, and nothing Notion-specific leaves
  `infrastructure/`.
- The tests exercise the real request, encoding and error paths over a real socket.
- A misconfigured database schema is refused loudly, before any Run starts or any page is written.

### Negative / Trade-offs
- It is a hand-maintained HTTP client. A change in Notion's JSON shape is caught only by the shape
  checks, not by a vendor library update.
- Six variables must be set before the board works.
- Every report makes two calls.

## Alternatives considered
- **`notion-client`:** it adds a dependency and `httpx` to wrap three requests, and its typed layer
  is still dicts.
- **Body from a `rich_text` property:** Notion caps each text chunk, and editors write briefs in
  the page, not in a cell.
- **One combined `"run_id: status"` property:** it is less legible and harder to filter on in
  Notion.
- **Writing the status into a `select`:** a write creates options silently, and filtering works
  just as well on text.
- **Treating a misconfigured readiness property as "not ready" (`None`):** it would hide a broken
  setup behind silence.

## References
- `ROADMAP.md`: Stage 9
- `ARCHITECTURE.md`: §3.1, §9
- `ADR-0023`, `ADR-0024`, `ADR-0025`
- `ADAPTER_SPEC.md` §3, §5; `ADAPTER_ACCEPTANCE.md` §8
