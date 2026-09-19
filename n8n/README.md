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
- **2026-09-18 — steps 1, 3 and 4 done; the Notion side is live.** The brief database
  (`3de64b74a905809daaa5f749653c1f29`) had only its `Name` title property, so the four the core
  needs were created through the Notion API: `Stage` (**select**, options `Ready for production` /
  `Draft`), `Run status` (rich_text), `Run id` (rich_text), `Review` (url). **Note the type:** the
  Notion API cannot create `status` properties at all — only `select`, which the adapter accepts
  just the same (ADR-0040). All seven `OMEMO_NOTION_*` are in the repo's `.env`, and
  `build_brief_board(os.environ).fetch_brief(<page>)` returned the page's paragraphs over the live
  API, so the board adapter is verified end to end against real Notion. A brief page was filled in
  and left at `Stage = Draft`, so flipping it to `Ready for production` is the deliberate start of
  the pilot. Both workflows are imported (step 3) with the database id substituted and the HTTP
  Request URLs pointed at `http://host.docker.internal:8765` (step 4). **The pilot runs without the
  Google Docs desk** — the operator cannot create a Google service account, so `OMEMO_GOOGLE_*` are
  unset, `BriefProduction.has_desk` is `False`, the Run stops at `waiting_human` and the approval is
  given with `demo_notion.py --approve`. The "→ Google Docs →" leg of Stage 12's DoD is therefore
  **not** covered by this pilot; a Notion-based `ReviewDesk` behind the existing port is the
  candidate fix (`CLAUDE.md` 16.3). Still open: renaming the `Notion account` credential to
  `Concept Notion (read)`, putting the generated `OMEMO_SERVICE_TOKEN` into the Header Auth
  credential, deleting the leftover `ConceptImportChk` workflow (CLI has no `delete:workflow`),
  activating, and the run itself.
- **2026-09-18 — `versionId` was missing from both committed workflow files** (fixed in 4499e2b).
  `n8n import:workflow` failed with `SQLITE_CONSTRAINT: NOT NULL constraint failed:
  workflow_entity.versionId` and created nothing — the same class of defect as the missing top-level
  `id`. Both files now carry a fixed `versionId`, pinned by `N8N-01`; the committed file was then
  imported into this n8n unchanged (only its `id` altered, so the check could not touch the
  configured workflow).
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
- **2026-09-19 — credential/workflow cleanup for the pilot (phase A).** Confirmed
  `Concept Notion (read)` is still the credential's name after the Docker move (the entry above's
  rename survived the bind-mounted SQLite round-trip). Updated `Concept factory service`'s
  `Authorization` value to `Bearer <the repo .env's OMEMO_SERVICE_TOKEN>` — its previous value
  predated that token and did not match it; still unverified against a *running*
  `factory_service.py` (n8n's Header Auth credential type has no connection test). Deleted the
  leftover `ConceptImportChk` workflow: the n8n UI has no direct Delete on an active workflow —
  **Archive first** (workflow list row's `⋮` menu), then enable "Show archived workflows" in the
  filter panel and Delete from there. Activated only `Concept factory — brief page updated →
  production`; `Concept factory — sweep reviews waiting for a human` stays Inactive on purpose —
  phase A has no review desk (no `OMEMO_GOOGLE_*`, per the entry above), so there is nothing for a
  sweep to pick up yet. Still open: start `factory_service.py` and flip the brief page's `Stage` to
  `Ready for production` — the actual pilot trigger.
