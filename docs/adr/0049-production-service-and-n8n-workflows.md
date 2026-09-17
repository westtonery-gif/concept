# ADR-0049: An HTTP production service for n8n, and the n8n workflows that call it

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** Lead Architect / Domain Architect (transport and sweep chosen by the maintainer)
- **Realizes:** ROADMAP Этап 11 (`CLAUDE.md` queue 15.3)

## Context

ROADMAP Stage 11: "creating a brief in Notion automatically starts a `Run` through n8n; business
logic stays in the Content Director, n8n does only transport and triggers". ARCHITECTURE §3.3 gives
the Python Agent Service "entry points for starting runs and for receiving Human Approval
decisions"; today the core is reachable only as a CLI run by hand.

The maintainer chose (2026-09-17):

1. **transport** — the core exposes an HTTP service that n8n's *HTTP Request* node calls, rather than
   an *Execute Command* node running the CLI (which would bind n8n to the core's host, and is disabled
   by default in n8n 2.x);
2. **decisions** — a Google Doc has no "decided" event, so n8n calls the core **on a schedule** to
   sweep the Runs waiting for a human; n8n needs no Google credentials.

What a trigger costs is the Content Director's work: minutes of model calls. An n8n node waiting on
it would time out, and two triggers of the same brief running at once would call the models twice
for one Run (the store has no concurrent-writer protection, ADR-0024). And n8n's Notion Trigger
(`n8n-nodes-base.notionTrigger` v1, event `pagedUpdatedInDatabase`) **polls** the database by
`last_edited_time` once a minute: every status the core writes onto a brief is itself a page edit
that triggers the core again. ADR-0047 §4 and ADR-0048 make a trigger with nothing new write nothing;
this ADR must make it cheap and harmless to *receive*.

`PROJECT.md` §5 admits a web framework only on proven necessity. Two routes and a token do not prove
it.

## Decision

### 1. `infrastructure/production_service.py` — stdlib only

`ProductionService(settings, *, produce, waiting)` serves HTTP with `http.server.ThreadingHTTPServer`
(no new dependency). `produce: Callable[[str], object]` handles one brief; `waiting: Callable[[],
Sequence[str]]` names the briefs to sweep. It knows nothing else of the core — the Composition Root
passes `BriefProduction.invoke` and `BriefProduction.waiting_briefs` (ADR-0048), so no production
rule lives in the transport either.

| Route | Auth | Answer |
|---|---|---|
| `GET /v1/health` | none | `200 {"status": "ok"}` — reveals nothing |
| `POST /v1/briefs` with `{"brief_ref": "<ref>"}` | bearer | `202 {"brief_ref": ref, "queued": bool}` |
| `POST /v1/reviews/sweep` (empty body or `{}`) | bearer | `202 {"waiting": [...], "queued": [...]}`; `waiting` raising → `503`, nothing queued |

- **Accepted, not done.** Both POSTs answer `202` at once and hand the work to a **single background
  worker**, which invokes `produce` for one brief at a time, in arrival order. One worker is the
  simplest way to guarantee no two invocations of a Run overlap and to keep one SQLite writer; a
  factory running briefs in parallel is a later decision.
- **Coalescing.** A brief already **waiting in the queue** is not queued again (`"queued": false`).
  A brief whose invocation is **running** is queued once more, so an edit that lands during a
  production is not lost; the extra invocation finds nothing new and writes nothing (ADR-0048).
- **A failing job never stops the worker.** Any exception from `produce` is logged with its
  traceback at `ERROR` and the worker takes the next brief.
- **Auth.** `Authorization: Bearer <token>`, compared in constant time; otherwise `401` with
  `WWW-Authenticate: Bearer`, and the body is not parsed. The token is never logged or echoed.
- **Input.** A body must come with `Content-Length` (a chunked body is `411`) and at most 16 KiB
  (`413`); the body must be a JSON object; `brief_ref` a non-blank string of at most 512 characters
  without control characters (`400` otherwise). The core treats it as opaque, as `BriefBoard` does.
- Unknown path → `404`; a known path with another method → `405` with `Allow`. Every answer is JSON
  and closes the connection (HTTP/1.0). Access lines go to the module logger, never stderr.
- `stop()` stops accepting requests, lets the running invocation finish and drops what is still
  queued — every trigger is repeatable, and the next poll or sweep brings it back.

### 2. Settings from the environment

`service_settings_from_env(environ)`: **`OMEMO_SERVICE_TOKEN`** is required and at least 32
characters (it guards paid model calls); `OMEMO_SERVICE_HOST` defaults to `127.0.0.1`,
`OMEMO_SERVICE_PORT` to `8765` (`0` picks a free port). A missing or short token or a bad port raises
`ServiceConfigurationError` naming the variable, never its value; the token is not in the settings'
`repr`. Binding to a non-loopback host is the operator's explicit choice (n8n in Docker reaches the
host as `host.docker.internal`, or both run in one Docker network).

`composition.build_production_service(environ, production)` builds the service over a
`BriefProduction`.

### 3. `factory_service.py`

The entrypoint builds `BriefProduction` exactly as `demo_notion.py` does (its
`build_brief_production`), logs one line per invocation (brief, run, status, and any
`decision_error` / `qa_error` / `publish_error` at `WARNING`), serves until interrupted and stops
cleanly.

### 4. The n8n workflows are committed and checked

`n8n/brief-ready.workflow.json` — *Notion Trigger* (`pagedUpdatedInDatabase`, every minute) →
*HTTP Request* `POST /v1/briefs` with body parameter `brief_ref = {{ $json.id }}` (the page id).
`n8n/review-sweep.workflow.json` — *Schedule Trigger* (every 5 minutes) → *HTTP Request*
`POST /v1/reviews/sweep`. Both authenticate with an n8n *Header Auth* credential
(`Authorization: Bearer <token>`), so no secret is in the JSON; both are exported inactive; database
id and service URL are placeholders the operator sets (`n8n/README.md`).

Every page edit is forwarded, ready or not: readiness is the board's rule
(`OMEMO_NOTION_READY_*`, ADR-0040), and an IF node in n8n would be a second copy of it — business
logic in the transport. `tests/test_n8n_workflows.py` pins that each workflow is exactly one trigger
of an allowed type connected to one HTTP Request that calls one of the service's routes with the
header credential, with no code, branching or data nodes and no token-like literal.

## Consequences

### Positive

- A brief edited in Notion starts or advances its Run within about a minute with no one running a
  command; a decision typed into the Doc is picked up by the next sweep.
- n8n holds only a Notion read credential and the service token; production rules stay in the core.
- No new dependency.

### Negative / Trade-offs

- One worker: briefs are produced one after another. A long queue delays later briefs.
- The queue is in memory: a restart drops accepted-but-unstarted triggers until the next poll/sweep.
- Every page edit of the database costs a request and two Notion reads in the core.
- A live n8n round trip needs real Notion and n8n instances — the operator's check.

## Alternatives considered

- **Execute Command → CLI** — rejected by the maintainer (§Context).
- **Synchronous `200` after production** — rejected: minutes-long requests, n8n timeouts, overlapping
  invocations of one Run.
- **A web framework (FastAPI, Flask)** — rejected by `PROJECT.md` §5 for two routes.
- **Filtering "ready" pages in n8n** — rejected (§4).
- **A thread per request doing the production** — rejected: overlapping writers to one Run.

## References

- `ROADMAP.md`: Этап 11; `ARCHITECTURE.md` §3.2, §3.3, §12; `PROJECT.md` §5
- ADR-0024, ADR-0040, ADR-0047, ADR-0048
- `PRODUCTION_SERVICE_SPEC.md`, `PRODUCTION_SERVICE_ACCEPTANCE.md`; `n8n/README.md`
