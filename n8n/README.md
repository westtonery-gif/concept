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
