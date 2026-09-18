# n8n workflows for the Concept factory (ROADMAP Stage 11, ADR-0049)

n8n only **triggers** the factory. It does not decide whether a brief is ready, what a review
decision means or what to write back to Notion. The core does all of that behind
`factory_service.py` (`PRODUCTION_SERVICE_SPEC.md`).

| File | What it does |
|---|---|
| `brief-ready.workflow.json` | A *Notion Trigger* polls the brief database every minute (**Page Updated in Database**) and sends each updated page's id to `POST /v1/briefs`. The core produces the page only if it is ready. Otherwise nothing happens. |
| `review-sweep.workflow.json` | A *Schedule Trigger* calls `POST /v1/reviews/sweep` every 5 minutes. The core reads the decisions typed into the review Google Docs and continues those Runs. |

Every page edit is forwarded on purpose, ready or not. Readiness is the board's rule
(`OMEMO_NOTION_READY_*`), and an IF node here would be a second copy of it. The core's own status and
link writes also edit the page, so they trigger one more call. That call finds nothing new and
writes nothing (ADR-0047, ADR-0048).

## Running n8n persistently (Docker)

`docker-compose.yml` runs n8n as a `restart: unless-stopped` container instead of the ad hoc
`npx n8n` (which dies with its shell/terminal and needs no persistence). It bind-mounts an
existing `~/.n8n` (SQLite store) by absolute path, so credentials and workflows created before
switching to Docker are kept, not lost.

```bash
cp .env.example .env   # then set N8N_DATA_DIR to the absolute path of the existing ~/.n8n
docker compose up -d
docker compose logs -f n8n   # startup should end "Editor is now accessible via: http://localhost:5678"
```

`.env` is git-ignored (repo `.gitignore`); only `.env.example` is committed. Because n8n now runs
**inside** a container, `http://127.0.0.1:8765` in both workflow files' HTTP Request nodes (step 4
below) must become `http://host.docker.internal:8765` to reach a `factory_service.py` running
directly on the host — that placeholder note in step 4 is no longer a Docker-Desktop-only caveat,
it's the actual value needed here.

Moving to a domain later is the same image: point `N8N_HOST`/`N8N_PROTOCOL`/`WEBHOOK_URL` at the
real host name instead of `localhost`/`http`, put a reverse proxy with TLS in front, and use the
same `docker-compose.yml` (or its image) on the hosting provider — no n8n reconfiguration beyond
those env vars.

## Setup

1. **Start the service** where n8n can reach it, with everything `demo_notion.py` needs plus a token
   of at least 32 characters:

   ```bash
   export OMEMO_SERVICE_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
   export OMEMO_SERVICE_HOST=0.0.0.0   # only if n8n runs in Docker or on another host
   python factory_service.py
   ```

2. **Create two credentials in n8n** (the names must match; re-select them after import if n8n
   asks):
   - **Header Auth** named `Concept factory service`. Set *Name* to `Authorization` and *Value* to
     `Bearer <the value of OMEMO_SERVICE_TOKEN>`.
   - **Notion API** named `Concept Notion (read)`, with an integration that can read the brief
     database. n8n never writes to Notion.

3. **Import** both files (*Workflows → Import from File*, or
   `n8n import:workflow --input=brief-ready.workflow.json`).

4. **Edit the placeholders:**
   - in *Brief page updated*, set the database (it is `REPLACE_WITH_NOTION_DATABASE_ID`);
   - in both HTTP Request nodes, change `http://127.0.0.1:8765` to wherever the service listens.
     From n8n in Docker Desktop that is `http://host.docker.internal:8765`.

5. **Activate** both workflows.

## Checking it

- `curl http://127.0.0.1:8765/v1/health` returns `{"status": "ok"}`.
- A request without the token gets `401`. A request n8n sent gets `202` with
  `{"brief_ref": …, "queued": true|false}`. `false` means the brief was already waiting in the
  queue.
- The service log has one line per produced brief. In Notion, the brief's *Run status* moves through
  `queued` … `waiting_human`, and its review link property points at the Google Doc.

## Operator verification log

`CLAUDE.md`'s Stage 11 note ("A live Notion Trigger stays the operator's check") is tracked here as
it's actually done, step by step against the Setup list above — not all at once.

- **2026-09-17 — step 2 (Notion API credential), partially done.** Created the Notion integration
  `concept` (Internal Integration Secret) at `notion.com/my-integrations` and granted its Content
  access to the brief database `New database` (id `3de64b74a905809daaa5f749653c1f29`, workspace
  `Immiray's Space`). Created a **Notion API** credential in n8n with that token; n8n's own
  credential test reported "Connection tested successfully" and the credential was saved.
  **Deviation from the Setup steps:** the credential is currently named `Notion account`, not
  `Concept Notion (read)` — rename it (or recreate it under that name) before importing the
  workflow files, since both JSON files reference the credential **by name**. The Header Auth
  credential (`Concept factory service`), `factory_service.py`, the workflow import/placeholder
  edits, activation and an actual triggered request are **not done yet** — still open for the next
  pass at this same list.
- **2026-09-18 — n8n moved off `npx` onto Docker (see "Running n8n persistently" above).** Docker
  Desktop installed; n8n's existing `~/.n8n` data directory bind-mounted into the official
  `n8nio/n8n:1.121.0` image via `docker-compose.yml`, `restart: unless-stopped`. Verified after the
  switch: `docker logs concept-n8n` ends clean (no deprecation warnings once
  `DB_SQLITE_POOL_SIZE`/`N8N_RUNNERS_ENABLED`/`N8N_BLOCK_ENV_ACCESS_IN_NODE`/
  `N8N_GIT_NODE_DISABLE_BARE_REPOS` are set), `curl http://localhost:5678/healthz` → `{"status":
  "ok"}`, and both credentials from the entry above (`Notion account`, `Concept factory service`)
  are present and unchanged in the n8n UI after the container recreate — the bind mount round-trips
  the same SQLite file, nothing was re-entered. Still not done: everything the previous entry
  already listed as open.
- **2026-09-18 — step 2's naming deviation resolved.** Renamed the Notion API credential in the n8n
  UI from `Notion account` to `Concept Notion (read)`, matching what both workflow JSON files
  reference by name; the connection test still reports "Connection tested successfully" after the
  rename. Step 2 of the Setup list is now fully done. **Note:** a `Concept factory service` Header
  Auth credential already exists in n8n (created 17 September, correctly named) — but it has never
  been tested, since `factory_service.py` has never been started, so it's unverified whether its
  `Authorization` value actually matches a real `OMEMO_SERVICE_TOKEN`. Still open: start
  `factory_service.py` and confirm/update that credential's value against it, then workflow
  import/placeholder edits, activation and an actual triggered request.
