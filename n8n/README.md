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

- **2026-09-20 — the maintainer asked for a rework; ADR-0032 and ADR-0052 both ran on live models
  for the first time, and QA flagged the new version too.** The maintainer decided the review (a
  session may not): changes requested on `…-review-1`, with instructions covering the three flags —
  drop the unsourced "paralysis" claim, drop the nonexistent checklist link and any promised
  outcome (no client profile exists, so the material is general, not promo), drop the "the brain
  gives up" psychology. Before the run, `.env` was moved off the pilot's `claude-haiku-4-5` onto
  **`claude-sonnet-5`** for all three roles, with the two variables ADR-0052 now requires per
  anthropic role — `OMEMO_MAX_TOKENS__<ROLE>=16000` and `OMEMO_THINKING__<ROLE>=adaptive`. Without
  them the factory refused to build at all (`ProviderModelSelectionError: role
  'content_researcher@v1' selects provider 'anthropic' without max tokens, thinking`), which is the
  fail-closed behaviour that note predicted. The already-running `factory_service.py` was stopped
  first, so the CLI and a re-triggered service could not touch the same stored Run at once.

  What the factory did: `…-review-1` recorded `changes_requested`, `resume` re-entered `RUNNING`,
  **only Leo** was re-executed (`…-task-3`, `script_writer@v1`), `…-artifact-2` became `SUPERSEDED`
  and `…-artifact-3` was created as `CANDIDATE` **v2** with `supersedes_ref` pointing at its
  predecessor; `rework_count` is 1 of 3; a fresh `…-review-2` is `PENDING`. Rin was not called
  again. Sonnet 5 accepted `thinking: {"type": "adaptive"}` on every turn — ADR-0052's grammar is
  now proven against a model that rejects `budget_tokens`. Cost of this invocation: **$0.020702**
  (`…-task-3` $0.012282, `…-evaluation-2` $0.008420), about twice the whole haiku pilot, as
  expected from Sonnet 5's $2/$10 per MTok.

  **QA answered `flagged` again**, with four flags: no client/product data or editorial rules; "они
  решают проблему фиксации, а не приоритизации" stated as fact; "это просто честный учёт
  собственной пропускной способности" reading as an implied promise of results; and the script
  looking like a stock problem-agitate-solve pattern in the productivity niche. Two of those Leo
  could not have fixed, which is the finding below.

  **Finding — `passed` is unreachable on this path, and it is structural, not a wording problem.**
  `ArtifactEvaluator.evaluate(content: str)` (ADR-0018/0036) hands the QA model **only the
  candidate's own content** — never the brief, never a client profile. But `qa-agent` v2's criteria
  ask for exactly that: criterion 4 judges "редакционным правилам клиента, присланным в контексте",
  and criterion 1 asks whether the material repeats the client's own or a competitor's output.
  Neither is decidable from the text alone, and the prompt closes with "Если сомневаешься — не
  ставь passed" — so a correct QA agent flags the missing context every time. The gate is doing
  what it was told; the contract is what is short. No further rework was spent on it: another
  iteration would buy a differently-worded flag, not a verdict. Options for the maintainer are
  queued in `CLAUDE.md` task 22.

- **2026-09-19 — the whole loop ran from a live Notion trigger; the pilot is paused at the human
  gate.** Steps 2 and 5 finished (credential renamed to `Concept Notion (read)`, the leftover
  `ConceptImportChk` deleted, `brief-ready` activated; `review-sweep` deliberately left inactive —
  with no desk it has nothing to pick up). `factory_service.py` was started on `0.0.0.0:8765` with
  `claude-haiku-4-5` bound to all three roles (at the time the only usable choice — `max_tokens` was
  hardcoded at 2048; ADR-0052 has since made it and the thinking mode per-role configuration, so a
  next pilot can bind Opus 5 or Sonnet 5 by setting `OMEMO_MAX_TOKENS__<ROLE>` and
  `OMEMO_THINKING__<ROLE>=adaptive`). Verified
  before spending anything: `/v1/health` answers from the host **and from inside the n8n container**
  over `host.docker.internal`; no token → `401`; a brief still at `Stage = Draft` → `202` and
  "nothing to produce", no model call. Then `Stage` was flipped to `Ready for production` and **n8n
  itself** called the service:

  ```
  06:11:08  POST /v1/briefs → 202          (sent by n8n, not by hand)
  06:11:15  Rin   → Output VALID, schema content-research-report
  06:11:22  Leo   → Output VALID, schema script-draft@v1
  06:11:28  QA    → verdict
  06:11:29  Run → waiting_human
  ```

  Result: two `SUCCEEDED` Tasks, two `VALID` Outputs, a `DRAFT` research artifact and a `CANDIDATE`
  script, one `PENDING` review, and three Analytics Records totalling **$0.010045** — real tokens,
  real prices. In Notion the page shows `Run status = waiting_human` and the Run id; `Review` stays
  empty because there is no desk.

  **QA answered `flagged`, with three substantive flags** — an unsourced claim, no client context,
  and psychological assertions needing grounding. The first one is a genuine catch: the brief asked
  for no unverifiable research references and Leo used one anyway. So the gate is shut: an Approve
  now would raise `ArtifactQaNotPassedError` (ADR-0018), and the honest route is a rework. **No
  decision was taken** — the approval is the operator's and nobody else's, so the Run is left
  `waiting_human` exactly as the factory left it.

  Two operational findings worth keeping:

  - **A rejected trigger is lost, not retried.** The first n8n call got `401` (see below) and the
    Notion Trigger never repeated it — it forwards a page once, when the page changes. The page had
    to be touched again to re-fire. Anything that makes the service refuse or be unreachable
    silently drops that brief until someone edits the page (`CLAUDE.md` task 18).
  - **The polling loop settled by itself.** The core's own four status writes edited the page during
    the run, yet no further `POST /v1/briefs` arrived in the following minutes. Notion reports
    `last_edited_time` only to the minute, so writes inside the same minute as the poll look
    unchanged. Observation over a few minutes, not a guarantee — the design does not rely on it
    (ADR-0047/0048 make a repeat call harmless anyway).

- **2026-09-20 — the Notion review desk is live and verified end to end (ADR-0060).** The
  integration's token (`.env`'s `OMEMO_NOTION_TOKEN`) was checked first and still authenticates
  against `Immiray's Space`, so nothing had to be reissued — and it turned out the integration has
  **workspace-level** access there, not per-page grants: a page created in the UI was visible to
  `/v1/search` immediately, so **no sharing step was needed** at all. Setup: a top-level page
  `Concept — ревью` created in the UI (the API cannot create a database whose parent is the
  workspace, and the brief database sits at workspace level, so a parent page had to exist), then
  the database **`Ревью Concept`** (`3e164b74-a905-817e-82fb-e45d15659ba6`) created **through the
  API** with exactly `Name` (title), `Review id` (rich_text), `Решение` (**select**, options
  `Одобрено`/`Отклонено`/`Доработать`), `Причина` (rich_text), `Fingerprint` (rich_text). The API
  route was chosen deliberately over clicking: it guarantees the names and, above all, the **type**
  — the UI's obvious choice would be a `status` property, which the API cannot create and which the
  2026-09-18 entry already recorded as a trap. The seven `OMEMO_REVIEW_NOTION_*` variables are in
  `.env`, the token reusing the existing integration's value.
  **Verified against real Notion, through `composition.build_review_desk`:** it selects
  `NotionReviewDesk`; `publish` created the page and returned its url; republishing the same
  package returned **the same page**; a **different** package under that `review_id` was refused;
  an undecided review read `None`; a never-published `review_id` was refused; and all three options
  mapped correctly — `Доработать → changes_requested` (with the reason), `Одобрено → approved`
  (reason `None`), `Отклонено → rejected` (with the reason). The decision was then reset to unset,
  so the test row claims no verdict. **The test row `run-live-check-review-1` is left in the
  database as evidence and can be deleted.** Nothing was decided on any real review: a session must
  not act as the reviewer (`CLAUDE.md` 16.3).
- **2026-09-19 — the n8n credential had `__n8n_BLANK_VALUE_<uuid>` in front of the token.** The
  first trigger was refused `401`. The Header Auth value was 104 characters instead of 50: n8n shows
  a saved secret as a "leave unchanged" placeholder, and `Bearer <token>` had been pasted **after**
  it. The token itself was right. Fixed by rewriting the credential through
  `n8n import:credentials` (value assembled from `.env`, never printed; the temp files inside the
  container had to be removed as root, since `docker cp` lands them owned by root). Worth knowing
  when a credential "looks correct" in the UI but every request comes back `401`.
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
