# CLAUDE.md — Concept Content Factory

Industrial multi-agent content production system (carousels, AI-video, articles).
Built bottom-up with a strict **spec-before-code** process. The architecture documents are
the source of truth — code must never contradict them; on conflict, the docs win
(PROJECT.md §17).

## Documentation hierarchy (source of truth, in order)
1. `PROJECT.md` — goals & principles (highest authority)
2. `ARCHITECTURE.md` — how the system is structured
3. `ROADMAP.md` — implementation order (stages + milestones)
4. `DOMAIN_MODEL.md` — entities, aggregates, events, invariants
5. Per-aggregate specs: `<NAME>_SPEC.md`, `<NAME>_ACCEPTANCE.md` (e.g. `RUN_SPEC.md`)
6. `docs/adr/` — Architecture Decision Records (technical decisions)
7. Code in `src/`, tests in `tests/`

**Running things** (not normative, but read before touching either department in anger):
`CLIPPING_RUNBOOK.md` — how to cut an episode end to end, including the two things that bite a
first run (**nothing loads `.env`**, and Homebrew is only on an interactive shell's `PATH`);
`n8n/README.md` — the content factory's operator setup and the verification log for both.

`CONTENT_FACTORY_THOUGHTS.md` sits **outside** this hierarchy — explicitly non-normative
(says so in its own header), a product/architecture exploration of the full video-factory vision
reconciled against the repo as of commit `063cfde`. Read it for context and the proposed sequence
(§16), but it does not override anything above 6 and it does not itself authorize starting
anything ahead of the queue below — see task 10.

## Current state (2026-09-21)
- **`CLIPPING_RUNBOOK.md` exists, and writing it found two gaps that would have broken the first
  real run.** (1) **Nothing reads `.env`** — every entrypoint takes `os.environ`, so the file is a
  record, not a loader, and the operator must `set -a && source .env && set +a`. (2) **The clip QA
  role had no provider binding**: `client_for_role("clip_qa_agent@v1", …)` would have raised
  `ProviderModelSelectionError` at build time, because `.env` carried bindings only for the three
  original roles. Both are now written down, and the binding was added mirroring `qa_agent@v1`'s
  (`claude-sonnet-5`, `adaptive` thinking) — a deliberate copy, not a default, and the runbook says
  where to change it. The runbook also covers what "normal" looks like, so a correct refusal is not
  read as a fault: a `flagged` clip is never approved, a rejected clip is simply not shipped, one
  bad clip does not fail the episode, an empty plan in `scene` mode is not an error, and a re-run is
  safe because committed work is reused. `CLAUDE.md`'s hierarchy section now points at it.
- **The clipping department is assembled and runnable (queue task 21.6).**
  `composition.build_clip_production(environ, evaluator=, desk=)` is the one place every outside
  system enters: the Notion episode board, the filesystem episode source, the ffmpeg+whisper.cpp
  footage index, the ffmpeg renderer, the Run store and — optionally — a review desk. Settings come
  from `OMEMO_CLIP_*` with **documented defaults** (2-minute pieces, a 2-minute ceiling, a 2-second
  pause nudge, a 3-minute platform cap, `mp4`). That is a **deliberate departure from ADR-0040's
  no-defaults rule**, and `CMP-02` pins the reason: a property name is someone else's column and
  guessing it silently binds the core to the wrong one, while a clip length is a product choice
  that is safe to start somewhere and tune from the first episode — the model path, being neither,
  still has no default. `demo_clips.py <episode page id>` runs one invocation, the same code a
  service would. Tests: `tests/test_clip_composition.py` (`CMP`), build-time only — no model call,
  no network, no ffmpeg — proving that one missing variable stops the **whole** build, named, so
  nothing half-built reaches a Run. Suite: 1400 passed.
  **The operator setup is done on the Notion side:** the database **`Эпизоды к нарезке`**
  (`3e264b74-a905-81cb-befb-ecee0e94c6b4`) was created through the API with `Name` (title), `Stage`
  (**select**: `Ready to clip` / `Draft`), `Source` (rich_text), `Mode` (**select**: `scene` /
  `chunk`), `Run status` and `Run id` (rich_text) — the API route again chosen so the **types** are
  right, since the UI's obvious choice would be a `status` property the API cannot create. It lives
  beside the review database on the page now named `Concept — нарезка`, and the integration has
  workspace-level access, so no sharing step was needed. All eleven variables are in `.env`, and
  `~/episodes/` exists. **What remains for a first real clip: an episode file in that folder and a
  card on the board.**
- **The clipping department has no vendor: `FootageIndex` runs on ffmpeg and whisper.cpp
  (ADR-0064, queue task 21.6).** `infrastructure/local_footage_index.py` answers all three things
  the port owes from tools on the operator's own machine: `ffprobe` for the duration, ffmpeg's
  **`scdet`** filter for scene-change timestamps, and **`whisper-cli`** (whisper.cpp, MIT, one brew
  formula) for speech with millisecond offsets. Offline end to end — for licensed television, the
  footage never leaving the machine is not a small point. **Vyra is dropped.** Capability was never
  the problem: its MCP drives an editor **open in a browser tab** ("No browser connected" is a
  documented failure mode), while `index()` is called from a Workflow step in `factory_service.py`
  on a schedule with nobody present. ADR-0053 §3 assumed "MCP" meant a server-side API and never
  checked — that was the error. Five alternatives were read: **Shorty** is genuinely headless but
  offers neither transcription nor scene detection; **SynthCut** is open source and has exactly the
  three tools, but is a Node/Electron app (Windows-only packaged build, a persistent core process,
  an MCP client dependency, a frame-based API against our millisecond contract) **wrapping
  whisper.cpp and ffmpeg as external processes** — which we now call directly; the two Claude Code
  plugins are tools for an agent session, not for a service. **The reason to buy had also gone
  away**: "buy, don't build" was about moment-finding, and ADR-0058 removed the planner. **The port
  did not change** — swapping a cloud editor for two local binaries touched one module in
  `infrastructure/`, which is the layering earning its keep. `silencedetect` is deliberately unused:
  Whisper's word timings already say where the pauses are, and two derivations of "a pause" would
  be one too many. Tests: `tests/test_local_footage_index.py` (`FIX`) measure and cut a **real**
  generated video with a known hard cut, drive transcription through a stub `whisper-cli` (a real
  model is multi-gigabyte operator setup, not a fixture), and **clean Whisper's output rather than
  trusting it** — blank, zero-length, overlapping and past-the-end segments are each dropped or
  clamped, because a transcript is a machine listening to speech, not a contract. One live test
  runs the real binary when `OMEMO_WHISPER_MODEL` is set and skips honestly when it is not. Suite:
  1380 passed, 1 skipped. **Still operator setup:** the GGML model file, and then the first real
  episode — which is also what decides `scdet`'s threshold.
- **The clipping department renders real files (queue task 21.6; ADR-0063).**
  `infrastructure/ffmpeg_clip_renderer.py` cuts one interval with `ffmpeg` and measures the result
  with `ffprobe` — **the measurements are read back from the produced file, never echoed from the
  request**, which is the point of `RenderedClip` carrying them: ADR-0056 §1's format check then
  compares real numbers, and `FCR-01` proves it by asking for a clip past the end of the episode
  and getting a shorter one. `infrastructure/file_system_episode_source.py` resolves a
  `source_ref` to a file inside one configured root, treating the ref as a **file name, never a
  path**: `..`, `/etc/passwd` and `sub/dir.mp4` are `None`, so a board someone else can edit cannot
  reach out of its folder. Tests cut a real video ffmpeg generates and read it back with ffprobe;
  where ffmpeg is absent they **skip**, which for a machine that cannot run them is the honest
  answer (12 passed, 7 skipped without it).
  **Captions are not burnt in, and this is recorded rather than hidden (ADR-0063).** The Homebrew
  ffmpeg 9.0.2 installed here was built without `libass` and without `libfreetype`: checked
  directly, `ffmpeg -h filter=subtitles` and `-h filter=drawtext` both answer `Unknown filter`, and
  its `configuration:` line and `brew deps ffmpeg` list neither. It cuts, encodes and measures
  perfectly; it simply cannot draw text on a picture. The maintainer chose to go without rather
  than spend an hour building ffmpeg from a third-party tap, because v1 publishes nothing by hand
  and the platforms caption automatically. **The captions are kept** — on `PlannedClip` and in the
  clip Artifact — so restoring burn-in is a change inside one adapter and nothing else, which is
  exactly what ADR-0062 bought. `OMEMO_EPISODE_ROOT` is the one new variable. **Homebrew is on
  `PATH` through `~/.zprofile`, so an interactive shell finds ffmpeg; a non-interactive one does
  not, and those tests skip there.**
- **The clipping department's production path is assembled and proven (queue task 21.6, `CRN`).**
  `application/clip_production.py` `ClipProduction.invoke(episode_ref)` takes one episode from the
  board to a human decision on every clip, as **application code beside `ContentDirector`** — never
  through it, because the Director's contract is a *declared* Workflow matched by position ending
  in one candidate (ADR-0026), and a fan-out of unknown width is a different shape. **Two of the
  Director's rules are deliberately not copied and both are written down in the module:** ADR-0031's
  fail-fast across steps (clips are independent, so one that cannot be rendered fails **its own**
  Task and the episode goes on) and its single candidate (every clip gets its own Artifact,
  Evaluation and Human Review — thirteen independent fail-closed gates, which `Run` already supports
  unchanged). What **is** copied is ADR-0026 §2's commit discipline. **Resumption is real, not
  claimed:** the plan is a pure function of the footage, so a crashed invocation is resumed by
  re-planning and reusing every committed Task — a terminal one is skipped, one left `RUNNING` is
  finished on its stored input rather than opened twice. `CRN-08` kills the process after **each of
  the fourteen commit points of a full invocation** and proves the episode still completes with
  three artifacts, three evaluations and no duplicate. Other `CRN` rows: a flagged clip blocks only
  itself and never fails the Run; a rejected clip is simply not shipped (no `SUPERSEDED`, no
  rework); the Run completes when every clip is decided whatever the decisions were; a QA outage
  parks at `WAITING_QA` and the next invocation asks again; a desk outage never changes the Run;
  and `RND-05` — a clip out of spec **fails its Task and never becomes an Artifact, an Evaluation or
  a review**, because a render defect is not a content risk. `application/clip_review.py` holds the
  per-Artifact siblings ADR-0059 §5 demanded; `review_publication`'s `pending[-1]` behaviour is
  **unchanged**, and `CRN-07` pins that. **Found while writing it:** the domain refuses an Output on
  a Task that has not succeeded (ADR-0005 §5), so the Task succeeds first and all four writes land
  in one commit. `infrastructure/in_memory_clipping.py` supplies the stubs for the two ports blocked
  on the environment (no ffmpeg, no Vyra), so the whole path is exercisable today. Suite: 1340
  passed. **What is left for a real clip to exist:** an ffmpeg-backed `ClipRenderer`, a Vyra-backed
  `FootageIndex`, a filesystem `EpisodeSource`, a Composition Root builder and the n8n trigger.
- **The clipping department's QA role exists (queue task 21.6, third slice; ADR-0056).**
  `agents/clip_qa_agent.py` — `clip_qa_agent@v1` → Prompt `clip-qa-agent` v1 (bundled store) →
  **the same** `qa-verdict@v1` Schema, whose object is *reused* rather than rebuilt: one verdict
  contract, one decoder, and a second vocabulary would be a second thing to keep honest. No Skills,
  no Tools; it answers with a verdict, so `validate_workflow_executors` refuses it as a Workflow
  step exactly as it refuses `qa_agent`. The Prompt carries the **three** criteria
  (self-contained, no orphaned punchline, no spoiler) and two instructions that matter as much as
  they do: it is given **the whole episode's transcript beside the clip's**, without which the
  spoiler criterion has no evidence to apply (ADR-0056 §3), and it is told **not** to judge
  duration, container, resolution or aspect ratio — that is arithmetic (ADR-0056 §1), and a model
  spending flags on it would fill a human queue with render defects. Tests:
  `tests/test_clip_qa_agent.py` (`CQA`), pinning consistency and those two instructions, not
  wording. **Two pinned sets were widened explicitly, and both caught the change on the first run:**
  `PST-01`'s bundled-catalogue set now names the fourth prompt, and the adapter-layer scan was
  widened in the previous slice. Suite: 1309 passed.
- **Both episode boards exist (queue task 21.6, second slice).** `InMemoryEpisodeBoard`
  (`infrastructure/in_memory_adapters.py`) keeps ADR-0025's shape: a control side **outside** the
  Protocol (`put` = the editor, `reports` = what the outside sees), and it fails **loudly** where
  the contract is silent — a status on an episode the board never had is `EpisodeBoardError`, not
  an invented row. `NotionEpisodeBoard` (`infrastructure/notion_episode_board.py`) is the real one:
  readiness from a `status`/`select` property, `source_ref` from a `rich_text` one, the cutting mode
  from a `select` speaking **exactly `ClipMode`'s vocabulary** (`chunk`/`scene`, case- and
  space-insensitive). **The mode is the one place this adapter departs from ADR-0040's "not
  producible is `None`" rule**: a mode column holding something that is not a `ClipMode` — or
  nothing at all — is `EpisodeBoardError`, because reading a broken board as "not ready" would hide
  the breakage behind a legitimate-looking `None`. Everything genuinely unclippable (unknown,
  archived, other database, not ready, no source) stays indistinguishably `None`. Eight required
  `OMEMO_EPISODE_NOTION_*` variables, no default property names; the token never appears in `repr`
  or a message; refs percent-encoded, dots included. Tests: `tests/test_notion_episode_board.py`
  (`NEB`, 35 cases against a local `ThreadingHTTPServer` playing Notion) and `STE` in
  `tests/test_in_memory_adapters.py`. **This is the third Notion implementation, so ADR-0061's
  condition is now met and the shared-client extraction is due** — as its own behaviour-neutral
  refactor, never inside a feature commit. Suite: 1300 passed.
- **The intermittent suite failure has a name:** `tests/test_production_service.py::
  test_svc_04_a_chunked_body_without_content_length_is_refused`. It failed once in a full run
  (`1299 passed, 1 failed`), then five full runs and eight isolated runs all passed — a timing race
  in the test's own HTTP client against the service closing the connection after refusing, not a
  defect in `ProductionService`. Queued as its own task; **not** fixed inside the clipping work.
- **The clipping department's contracts and its two pure functions exist (queue task 21.6, first
  slice; `CLIPPING_SPEC.md` / `CLIPPING_ACCEPTANCE.md`).** Four new role-named ports in `adapters/`:
  `EpisodeBoard` (+ `IncomingEpisode`, `ClipMode`, `EpisodeBoardError`), `EpisodeSource`
  (`locate -> LocatedEpisode`, a **locally readable** path, so a future remote implementation
  materialises a copy and the renderer never learns about the network), `FootageIndex`
  (`index -> IndexedFootage(duration_ms, scenes, speech)`, validated: breaks strictly increasing,
  speech ordered and non-overlapping, both inside the episode) and `ClipRenderer`
  (`render -> RenderedClip` carrying **its own** measurements — it made the file, which is what lets
  the format check be arithmetic). Two pure functions in `application/`: `plan_clips` (both modes;
  `CHUNK` cuts at a configured length with each boundary nudged to the nearer edge of a spoken line
  when one is within tolerance, `SCENE` merges consecutive scenes under a maximum and splits a
  longer one by the `CHUNK` rule; **no detected scene plans nothing and is not an error** — guessing
  "the whole episode is one scene" would be a second copy of a decision that is not ours) and
  `check_clip_format` (duration and container only — **aspect ratio is deliberately not a
  violation**, ADR-0058). **The adapter-layer boundary was widened explicitly, not quietly:**
  `tests/test_adapter_contract.py` gained `enum` to `_ALLOWED_STDLIB` for `ClipMode`, with the
  reason written beside it, and its module list now names all eight contracts — both tests caught
  the new modules on the first run, which is what they are for. Tests:
  `tests/test_episode_board.py` (`EPB`), `tests/test_clip_plan.py` (`CLP`, including a purity scan
  in the shape of `TLB-01`), `tests/test_clip_format.py` (`RND-01…04`). Suite: 1259 passed.
  **Still blocked on the environment, and said so in the acceptance's own state table:** ffmpeg is
  not installed (so the real `ClipRenderer` waits) and Vyra is not configured (so `FootageIndex`'s
  real adapter waits). **Next:** the production path (`CRN`).
- **A real Notion `ReviewDesk` exists (ADR-0060; queue task 19) — not wired yet.**
  `infrastructure/notion_review_desk.py` `NotionReviewDesk` is the **second** implementation of the
  ADR-0023 port, beside `GoogleDocsReviewDesk`; no core change, exactly as `NotionBriefBoard` was
  additive. A review is a page of its **own** database: `publish` writes the package into the page's
  blocks (instruction, what is reviewed, the brief, QA flags, the material) plus three properties —
  title, `review_id` and a SHA-256 fingerprint of the canonical package — and returns the page's
  `url`; republishing the same `review_id` with the same package returns that same page (so it is
  also the retry), while a **different** package under it is `ReviewDeskError`. `fetch_decision`
  reads **typed properties**, not a marker line: a `select` (`Одобрено`/`Отклонено`/`Доработать`,
  plus the `ReviewStatus` spellings, case- and space-insensitive) and a `rich_text` reason (blank →
  `None`). **Unset → `None` is the only ambiguity left; an option outside the three is
  `ReviewDeskError`, never silence.** Lookup is a database query filtered on the `review_id`
  property — a `POST` body, so unlike ADR-0043's Drive lookup nothing is hashed and no id enters a
  URL. Notion's limits are handled: text is chunked at 2000 characters and blocks past the first
  100 are appended in further requests. Seven required `OMEMO_REVIEW_NOTION_*` variables, no default
  property names (ADR-0040's rule); the token never appears in `repr` or a message. The request
  helper, auth header and property reader are **duplicated from `notion_brief_board` on purpose** —
  rule of three, and ADR-0061 records that the shared client is extracted only once three
  implementations exist in `src/` (this is the second). Spec: `ADAPTER_SPEC.md` §6 "Реализация на
  Notion"; acceptance: `ADAPTER_ACCEPTANCE.md` §14 `NRD`; tests `tests/test_notion_review_desk.py`
  run the real HTTP code against a local `ThreadingHTTPServer` playing Notion. **Wired:** `composition.build_review_desk(environ)` now
  **chooses**, with presence as the opt-in and **no silent fallback** — any `OMEMO_REVIEW_NOTION_*`
  variable selects the Notion desk, otherwise any `OMEMO_GOOGLE_*` selects Google Docs, and a
  **partly** configured desk fails closed naming its own missing variables rather than being
  quietly replaced by the other one (an operator who set half of Notion's variables meant Notion).
  With neither set, the error names both sets. `demo_notion.py`'s `_DESK_VARS` is the union, so "no
  desk at all" stays a legitimate state (ADR-0044 §5) and reviews stay in the Run store. Tests:
  `RPB-08`. **Still the operator's:** create the review database in Notion with the five properties
  and **grant the `concept` integration access to it** — a database the integration cannot see is
  indistinguishable from an empty one (ADR-0055 §5). Suite: 1214 passed.
- **`max_tokens` and extended thinking are per-role configuration; no request parameter is hardcoded
  any more (ADR-0052; queue task 17).** `OMEMO_MAX_TOKENS__<ROLE>` and `OMEMO_THINKING__<ROLE>`
  (`adaptive` | `disabled` | `budget:<N>` | `inherit`) are **required** for an anthropic binding, like
  the three pricing values; `_DEFAULT_MAX_TOKENS = 2048` is deleted and `AnthropicLLMClient` cannot be
  built without both, so the defect cannot come back through a caller. `ThinkingSetting` (immutable,
  owns the grammar via `parse`) is sent on **every** provider turn, including each turn of the
  ADR-0028 Tool loop; `inherit` is the only mode that omits the field, and the SDK strips it from the
  body. `N >= 1024` and `N < max_tokens` are checked at selection time, before a token is bought.
  What a given model accepts is deliberately not modelled — a wrong pair is the provider's `400`,
  i.e. an already-managed failed Task. **An existing `.env` must add both variables per anthropic
  role** (it fails closed, naming what is missing). `PROVIDER_MODEL_SPEC.md` /
  `PROVIDER_MODEL_ACCEPTANCE.md` are 1.2 (§7 new); tests `tests/test_provider_model.py` + `LTL-09`.
  Suite: 1182 passed.
- **The M3 pilot ran on live systems and is PAUSED AT THE HUMAN GATE — the milestone is not
  closed.** On 2026-09-19 the whole loop ran end to end against real Notion, real n8n (Docker,
  1.121.0) and real Anthropic: a page flipped to `Ready for production` → **n8n itself** called
  `factory_service.py` → Rin → Leo → QA → `waiting_human`, in 21 seconds, for **$0.010045** of real
  tokens (three Analytics Records). Two `SUCCEEDED` Tasks, two `VALID` Outputs against the catalogue
  Schemas, a `CANDIDATE` script, a `PENDING` review, and `Run status = waiting_human` written back
  onto the Notion page. Full log: `n8n/README.md` → Operator verification log.
  **Where it stopped and why:** QA answered **`flagged`** with three substantive flags (an unsourced
  claim the brief had explicitly forbidden, no client context, ungrounded psychological assertions).
  That shuts the gate — an Approve raises `ArtifactQaNotPassedError` (ADR-0018) — so the real route
  was a rework.
  **On 2026-09-20 the maintainer asked for that rework, and it ran (see task 22 for what it
  found).** Instructions covered the three flags; the factory did exactly what ADR-0032 specifies —
  `review-1` `changes_requested`, re-entry into `RUNNING`, **only Leo** re-executed (`task-3`),
  `artifact-2` `SUPERSEDED`, `artifact-3` `CANDIDATE` **v2** with `supersedes_ref`, `rework_count`
  1/3, a fresh `PENDING` `review-2`. Roles were moved to **`claude-sonnet-5`** with
  `OMEMO_MAX_TOKENS__<ROLE>=16000` + `OMEMO_THINKING__<ROLE>=adaptive`, so **ADR-0052 is now proven
  on a live model that rejects `budget_tokens`**; cost $0.020702 for the invocation. **QA answered
  `flagged` again** — and two of its four flags are ones no rewrite can clear, because
  `ArtifactEvaluator.evaluate(content)` shows QA only the artifact while `qa-agent` v2 judges
  client rules and uniqueness. **`passed` is therefore unreachable on this path; that is now the
  thing blocking M3, not the wording of the script (task 22).** The Run sits stored at
  `waiting_human` with v2 pending. **Every review decision is the maintainer's: a session must not
  approve, request changes or reject on their behalf.**
  **What the pilot does NOT cover:** the `→ Google Docs →` leg. The maintainer cannot create a
  Google service account, so `OMEMO_GOOGLE_*` are unset, `has_desk` is `False` and the approval
  would be given through the CLI. **Milestone M3 therefore stays open** on two counts: no
  human-approved artifact yet, and the review-desk leg unproven (see task 16.3 and task 19).
  The pilot itself ran on `claude-haiku-4-5` (the task 17 workaround); the 2026-09-20 rework runs on
  `claude-sonnet-5`, which is what an `.env` carrying the two ADR-0052 variables buys. **Without
  them an existing `.env` does not fail late, it fails to build at all** — verified on 2026-09-20:
  `ProviderModelSelectionError: role 'content_researcher@v1' selects provider 'anthropic' without
  max tokens, thinking`. `.env` (git-ignored) holds the live configuration; `factory_service.py` was
  run manually and is not a service unit — **stop it before driving the same brief from the CLI**,
  or a re-triggered service and the CLI write the same stored Run at once.
- **ROADMAP Stage 12 (MVP) is closed as code; Milestone M3 is still open (ADR-0051).**
  `tests/test_stage12_acceptance.py` (`S12A`, `STAGE12_ACCEPTANCE.md`) runs **S11A's production path
  unchanged** — the real `ProductionService` over `BriefProduction` + `build_run_index`, every
  request rendered from the committed n8n workflows — and asserts each of Stage 12's five DoD lines
  **as the DoD words it, on the assembled loop entered through the trigger**: a brief goes from the
  Notion trigger to an artifact a human `APPROVED`; the finished Run is reproducible from one stored
  row (brief as the first Task's input, two `VALID` Outputs with catalogue `schema_ref`s, an Artifact
  per Output, the Evaluation with its `evaluator_ref`, the Review with `decided_by`, one Analytics
  Record per completed provider turn, and the journal's events); replaying the triggers produces
  nothing again; nothing is `APPROVED` without an explicit Approve that QA passed; every inter-agent
  message carries exactly its Schema's fields; one that fails its Schema stops the pipeline
  (`FAILED` + `INVALID_OUTPUT_REASON`, no Artifact, Leo and QA never called); a crash mid-intake
  leaves a managed `QUEUED` Run the next trigger produces **from the brief on the board**; a sweep
  the service cannot dispatch answers `503` and changes nothing. No production code changed; S11A's
  `Factory` gained two optional keyword arguments (`dies_after_saves`, `index`), as `_Process` did
  for S9A. **Milestone M3 is deliberately NOT claimed (ADR-0051 §3): it needs the live pilot — one
  real brief through real Notion, real Google Docs, real Anthropic and a real n8n trigger — which
  needs the maintainer's four external accounts and cannot be done in this environment. A scripted
  test may not stand in for it.** Suite: 1154 passed.
- **ROADMAP Stage 11 (n8n) is closed (ADR-0050).** `tests/test_stage11_acceptance.py` (`S11A`,
  `STAGE11_ACCEPTANCE.md`) runs the real `ProductionService` over S10A's production path assembled as
  `BriefProduction` + `build_run_index`, and **renders every request from the committed n8n workflow
  files** (HTTP Request node's method/route/body; `={{ $json.id }}` from a Notion-Trigger-shaped
  item; an unknown expression fails instead of being guessed). Covered: a ready page → Run +
  statuses + review link; an unready/unknown page → nothing; repeated polls after the core's own
  writes → no model call, no board write; an approval on the desk → sweep completes; changes
  requested → sweep reworks and links v2; a wrong credential → 401; a failing job does not stop the
  worker; a restarted service sweeps a Run left waiting. No production code changed. **Checked once
  against a real n8n 2.39.7** (local install, not CI): both files import and export back unchanged
  (this found that `n8n import:workflow` needs a top-level workflow `id` — added, pinned by N8N-01),
  and the activated sweep workflow called a live `factory_service.py` → `202` with the credential
  resolved by name. A live Notion Trigger stays the operator's check (`n8n/README.md`).
- **The core is an HTTP service n8n calls, and the n8n workflows are committed (ROADMAP Stage 11,
  ADR-0049; queue task 15.3).** `infrastructure/production_service.py` (stdlib `http.server`, no new
  dependency): `POST /v1/briefs {"brief_ref"}` and `POST /v1/reviews/sweep` answer `202` and queue
  work for **one** background worker (a brief waiting in the queue is not queued twice; a running one
  is queued once more); `GET /v1/health`; bearer `OMEMO_SERVICE_TOKEN` (required, ≥ 32 chars,
  constant-time compare, never logged), `OMEMO_SERVICE_HOST`/`_PORT` default `127.0.0.1:8765`; a
  failing job is logged and the worker goes on; `stop()` closes the queue first. Built by
  `composition.build_production_service(environ, production)` over `BriefProduction.invoke` /
  `waiting_briefs`; entrypoint `factory_service.py` (logs one line per invocation). `n8n/`:
  `brief-ready.workflow.json` (Notion Trigger `pagedUpdatedInDatabase` every minute → POST
  `brief_ref = {{ $json.id }}`), `review-sweep.workflow.json` (Schedule every 5 min → POST sweep),
  Header Auth credential `Concept factory service`, placeholders + setup in `n8n/README.md`. n8n
  forwards every page edit — readiness stays the board's rule. Spec/acceptance:
  `PRODUCTION_SERVICE_SPEC.md` / `PRODUCTION_SERVICE_ACCEPTANCE.md`; tests
  `tests/test_production_service.py` (`SVC`), `tests/test_n8n_workflows.py` (`N8N`). Suite: 1137
  passed.
- **One brief invocation is application code, and stored Runs can be listed by status (ROADMAP
  Stage 11, ADR-0048; queue task 15.2).** `application/brief_production.py` `BriefProduction(director,
  store, board, workflow, *, desk=None, index=None)`: `invoke(brief_ref) -> BriefInvocation` runs
  `take_review_decision` → `produce_brief` → `publish_pending_review` → `show_review_location`,
  turning desk refusals / QA-without-verdict into `decision_error` / `publish_error` / `qa_error`
  fields (everything else propagates); `waiting_briefs()` = briefs of intake Runs (id is
  `run_id_for_brief(ref)` = `run-notion-<ref>`) stored at `waiting_human`. New additive contract
  `adapters/run_store.py` `RunIndex.run_ids(*, status)` — `RunStore` unchanged; `SqliteRunStore`
  implements it by decoding every row (no column, no migration); `composition.build_run_index`.
  `demo_notion.py` now delegates to `invoke` and exposes `build_brief_production(environ)` for the
  service. Tests: `tests/test_brief_production.py` (`BPR`, `ADAPTER_ACCEPTANCE.md` §13), STO-12.
  Suite: 1090 passed.
- **The review Doc's link is shown on the Notion brief (ROADMAP Stage 11, ADR-0047; queue task
  15.1).** Additive `BriefBoard.report_review_location(brief_ref, /, *, run_id, location)`;
  `NotionBriefBoard` writes it into a `url` property named by the **new required seventh variable
  `OMEMO_NOTION_REVIEW_LINK_PROPERTY`** (an existing `.env` must add it); `InMemoryBriefBoard`
  records it (`review_locations`). `BriefStatusReporter.show_review_location(run, location)` reports
  only a location it has not yet shown successfully — a page write re-triggers n8n's polling Notion
  Trigger, so a call with nothing new writes nothing; a refusal → `WARNING` +
  `FailedLocationReport`, retried by the next call. `demo_notion.py` shows it after publishing.
  Tests: NBB-09/10, STB-08, BSR-09. Suite: 1073 passed.
- **ROADMAP Stage 10 (Google Docs) is closed (ADR-0046).** `tests/test_stage10_acceptance.py`
  (`S10A`, `STAGE10_ACCEPTANCE.md`) reuses S9A's production path and adds a review desk
  (`InMemoryReviewDesk` subclassed to play a Doc: the decision line can be retyped, the desk can go
  down). Every invocation does what `demo_notion.py` does: `take_review_decision` → `produce_brief` →
  `publish_pending_review`. Covered: publication with brief/flags/version; undecided; approve →
  `COMPLETED`; changes/reject → rework, v2 published with `supersedes_ref`; an approval QA did not
  pass, then retyped; lost publication; desk outage while reading; crash after the decision is saved;
  rejections past the rework bound → `FAILED`. **Extracted:** the entrypoint's "publish → apply →
  save only when applied" step is now `application/review_decision.py`
  `take_review_decision(store, desk, run_id)` (`RDF-08`); `demo_notion.py` delegates and no longer
  says "not decided yet" after a crash left a recorded decision unresumed. **Noted, kept:** the
  in-memory stub's "first decision is final" (ADR-0025 §3) cannot model ADR-0045 §3's retyped Doc.
  A live Google Docs round-trip is the operator's check. Suite: 1055 passed.
- **The reviewer's decision is read back from the desk, and a Reject is reworked (ROADMAP Stage 10,
  ADR-0045; queue task 14.3).** Two domain calls **asked, not guessed** — the maintainer chose:
  (1) `REJECTED` routes like `CHANGES_REQUESTED` (ARCHITECTURE §13 / DOMAIN_MODEL §2.14): rework of
  the candidate's producer, the rejected version `SUPERSEDED` (never `ArtifactStatus.REJECTED`),
  `ReworkPolicy` bound → `FAILED`; the rework input gained `human_decision`
  (`changes_requested`/`rejected`); (2) an approved escalation stays deferred. Decided here: such an
  approval is **not recorded** (`applied=False`), so the review stays `PENDING` and the reviewer can
  still change the Doc — recording it would strand the Run with no pending review.
  `application/review_decision.py` `apply_review_decision(run, desk) -> FetchedDecision | None`
  (no pending review → no desk call; undecided → `None`; `ReviewDeskError` propagates; Run changed in
  memory only — caller saves + resumes). `review_publication.py` now exposes `pending_review` /
  `latest_qa`. `demo_notion.py` for a stored Run with a desk and no flag: publish (idempotent) →
  apply → save → resume. Both demos gained `--reject "<reason>"`; manual `--approve` on a non-passed
  candidate is refused too. APG-03/RWR-05 `REJECTED` rows became APG-06/RWR-07. Tests:
  `tests/test_review_decision.py` (`RDF`, `ADAPTER_ACCEPTANCE.md` §12). Suite: 1040 passed.
- **The Approval Gate now holds every QA-passed candidate, and a pending review is published
  (ROADMAP Stage 10, ADR-0044; queue task 14.2).** Found while wiring: `PASSED` used to go
  `WAITING_HUMAN → COMPLETED` with no Human Review, contradicting `PROJECT.md` §12 /
  `ARCHITECTURE.md` §13 / `RUN_SPEC.md` §4 — **the maintainer chose to hold the gate.** With QA wired
  (or in rework) reaching `WAITING_HUMAN` opens the candidate's `PENDING` review **in the same
  commit**; on `resume`, latest review `APPROVED` + latest QA `PASSED` → Artifact `APPROVED` + Run
  `COMPLETED` in one commit; `PENDING`, `REJECTED` and an approved escalation are left alone
  (`REJECTED` routing still deferred, ADR-0044 §2). Without QA the legacy no-review completion
  stays. `application/review_publication.py` `publish_pending_review(run, desk)` builds the
  `ReviewPackage` from the Run only (candidate, first Task's input as the brief, latest QA flags),
  never mutates it, and lets `ReviewDeskError` propagate; the **entrypoint** calls it after the
  Director returns (idempotent per `review_id`, so it is also the retry). `composition.
  build_review_desk(environ)`; `demo_notion.py` publishes when `OMEMO_GOOGLE_*` is set and prints
  the Doc link; both demos gained `--approve`. Stage 8/9, SWR, BSR, QWR, LAE, ECD expectations moved
  from `passed → completed` to `passed → waiting_human (+review) → APPROVED → completed`. Tests:
  `tests/test_review_publication.py` (`APG`, `EVALUATION_ACCEPTANCE.md` §4.5; `RPB`,
  `ADAPTER_ACCEPTANCE.md` §11). Suite: 1029 passed.
- **A real Google Docs `ReviewDesk` exists (ROADMAP Stage 10, ADR-0043; queue task 14.1) — not
  wired yet.** `infrastructure/google_docs_review_desk.py` `GoogleDocsReviewDesk` speaks the **Google
  Drive API v3** only, through stdlib `urllib`: find by `appProperties` (SHA-256 of `review_id` /
  of the canonical package JSON — no raw id ever enters a query), create a Doc by multipart upload
  of `text/plain` with conversion, read by `export?mimeType=text/plain`. The two flagged decisions
  were **asked, not guessed** — the maintainer chose: (1) **service account** auth (JWT RS256 →
  token at the key's `token_uri`, cached to expiry − 60 s, dropped on `401`); the one new runtime
  dependency is **`cryptography`** (stdlib cannot sign RS256; `google-auth` /
  `google-api-python-client` rejected); (2) a **marker line**: the Doc starts with an instruction,
  `РЕШЕНИЕ:`, `ПРИЧИНА:` and a `======== МАТЕРИАЛЫ РЕВЬЮ ========` separator; only the block above
  the first separator is read. `одобрено`/`отклонено`/`доработать` (or the `ReviewStatus` values,
  case-insensitive, trailing `.`/`!` ignored) decide; reason = rest of the `ПРИЧИНА:` line + following
  block lines. Empty → `None`; unrecognised word → `None` + `WARNING` (a typo is not a fault);
  damaged block (no separator, marker missing/repeated/out of order) → `ReviewDeskError`, as are
  unpublished/trashed, two Docs per review, another package under a published review, non-2xx,
  network, bad shapes. `google_docs_settings_from_env` needs `OMEMO_GOOGLE_SERVICE_ACCOUNT_FILE` +
  `OMEMO_GOOGLE_REVIEW_FOLDER_ID` and validates the key file up front (never echoes key material).
  Operator setup (shared drive folder shared with the service account) is in README / `.env.example`.
  Tests: `tests/test_google_docs_review_desk.py` (`GDR`, `ADAPTER_ACCEPTANCE.md` §10) against a local
  fake Google that verifies the JWT signature. No Composition Root builder yet — 14.2. **A new
  dependency means a fresh checkout's venv needs `pip install -e ".[dev]"` (or
  `uv pip install cryptography`) again.** Suite: 1007 passed.
- **ROADMAP Stage 9 (Notion) is closed (ADR-0042).** `tests/test_stage9_acceptance.py` (`S9A`,
  `STAGE9_ACCEPTANCE.md`) files a brief on `InMemoryBriefBoard` and produces it only through the new
  `application/brief_intake.py` `produce_brief(director, store, board, workflow, *, brief_ref,
  run_id)` on Stage 8's production path (bundled Prompts, real `AnthropicLLMClient`s with scripted
  transport, real `SqliteRunStore` under `BriefStatusReporter`, restarts): valid Run, every status on
  the board, unproducible brief → nothing, rework across a restart, QA error parked + shown, board
  outage never stops the Run and is caught up next invocation, foreign `run_id` refused. **Gap found
  and fixed:** `demo_notion.py` used to resume with `brief=""`, so a crash between the `queued`
  commit and the first Task's start would run Rin on an empty brief. `produce_brief` now resumes on
  the first Task's stored input, or — no Task yet — fetches the brief from the board again
  (unproducible → `BriefIntakeError`, Run untouched). `demo_notion.py` delegates to it. A live
  Notion round-trip stays the operator's check. Suite: 958 passed.
- **Run statuses are written back to the Notion brief (ROADMAP Stage 9, ADR-0041; queue task
  13.3).** `application/brief_status.py` `BriefStatusReporter(store, board)` is itself a
  `RunStore`: `save` saves through the wrapped store, **then** reports `run.status` on
  `run.content_brief_ref` — once per status change (every one the Director commits: `queued`,
  `running`, `waiting_qa`, `waiting_human`, `completed`, `failed`; never `created`, never before the
  save). `ContentDirector` is **unchanged** — it already saves at every status change (ADR-0026 §2).
  A `BriefBoardError` never stops the Run: `WARNING` log + `failed_reports`, not retried by later
  saves in the same status (a Notion outage would otherwise cost a timeout per commit), retried by
  `sync(run)` or superseded by the next status; any other board exception propagates.
  `demo_notion.py` wraps its store and calls `sync` at the end of every invocation, printing refused
  reports. Verified end to end against a local fake Notion (PATCH 503 then 200) with fake providers.
  Tests: `tests/test_brief_status.py` (`BSR`, `ADAPTER_ACCEPTANCE.md` §9). Suite: 948 passed.
- **A real Notion `BriefBoard` exists (ROADMAP Stage 9, ADR-0040; queue task 13.1) — not wired
  yet.** `infrastructure/notion_brief_board.py` `NotionBriefBoard` speaks the Notion REST API
  through stdlib `urllib` (**no new dependency** — `notion-client` was rejected: three endpoints,
  and it would drag in `httpx`), `Notion-Version: 2022-06-28` pinned. A brief is a page of the
  configured database; ready = a `status`/`select` property holds the configured option; `body` =
  text of top-level `rich_text` blocks (all listing pages; nested children deferred);
  `report_status` overwrites two `rich_text` properties (status value + run id), so a repeat is
  harmless. Not producible (blank ref, `404`, archived/trashed, other database, not ready, no text)
  → `None`; misconfigured property, other non-2xx, network/timeout, bad JSON shape →
  `BriefBoardError`; a report on a page not on the board / with bad properties is refused **before**
  any `PATCH`. Refs are percent-encoded incl. dots (no path injection). `notion_settings_from_env`
  needs all six `OMEMO_NOTION_*` variables (token, database id, ready property + value, run status +
  run id properties) — no default property names; missing ones are named, the token never appears in
  `repr`/messages. Tests: `tests/test_notion_brief_board.py` (`NBB`, `ADAPTER_ACCEPTANCE.md` §8)
  run the real HTTP code against a local `ThreadingHTTPServer` playing Notion. Suite: 936 passed.
- **The three production Prompts are retargeted to the business-agnostic domain (follow-up of
  ADR-0037; CLAUDE.md queue task 12 — content, no new ADR).** `prompts/catalogue.toml` now holds version 2 of
  `content-researcher`, `script-writer` and `qa-agent`, replacing each v1 record (the store holds
  exactly one active record per `prompt_id`, `PROMPT_STORE_SPEC.md` §1 — v1's text stays recoverable
  from git history, never edited in place). `qa-agent` v2 drops the health/medical-claim criteria for
  the four already-charter-decided ones: uniqueness (not templated, not a repeat of the client's own
  or a competitor's material), factual correctness, no unsubstantiated claims, the client's editorial
  rules, regulated-niche rules only when a client profile supplies them. The ADR-0034 verdict grammar
  is untouched. Because `Agent.prompt_ref` is the bare unversioned `prompt_id`, no role module needed
  a code change to "point at" v2. Tests: version/`prompt_ref` assertions bumped in
  `tests/test_prompt_store.py`, `tests/test_qa_agent.py`, `tests/test_qa_wiring.py`,
  `tests/test_metrics_capture.py` (no new behavior, so the suite count is unchanged at 892).
- **ROADMAP Stage 8 (QA Agent) is closed (ADR-0039).** `tests/test_stage8_acceptance.py` (`S8A`,
  `STAGE8_ACCEPTANCE.md`) runs Rin → Leo with the `qa_agent@v1` gate through `compile_runtime` on
  production assets only — bundled Prompts for all three roles, two real `AnthropicLLMClient`s
  (producers / QA, priced separately; transport scripted below the SDK), real `SqliteRunStore`,
  every restart a fresh Root over the same file: `passed` → `COMPLETED`; `flagged`/`failed` →
  escalation a human Approve cannot open; model flag → `CHANGES_REQUESTED` → rework of Leo only →
  v2 judged again → approvable; malformed verdict → parked at `WAITING_QA` → restart asks again.
  No gap found, no production code changed. **Noted, deferred (ADR-0039):** `AnthropicLLMClient`
  stringifies field values with `str()`, so a model that sends `flags` as a native array (not the
  declared string) yields `['…']` → `QaVerdictError` → the gate parks instead of escalating.
- **The domain pivoted: no health content, no OMEMO (ADR-0037; charter `PROJECT.md` is now 1.3).**
  The factory is **business-agnostic** — domain, brand, audience and rules arrive as a client
  profile, not baked into the core. Value #1 is now **uniqueness** + quality (not templated, not a
  repeat of the client's own or a competitor's material), and "Fail closed для домена здоровья"
  became "Fail closed при сомнении". `PROJECT.md` / `ARCHITECTURE.md` / `ROADMAP.md` were reworded
  and renamed to *Concept Content Factory*; `PROJECT.md` §11 now matches the direct-push policy it
  had contradicted since 2026-09-15. **Deliberately not touched:** the three v1 Prompts
  (`qa-agent`, `content-researcher`, `script-writer`) still say OMEMO/health — a Prompt version is
  immutable (ADR-0030/0035), so the new criteria land as **v2** (task 12). The Python package keeps
  the name `omemo_content_factory`; renaming it is a separate mechanical change, if ever.
- **The QA gate runs on a real model from a real entrypoint (ROADMAP Stage 8, ADR-0038).**
  `composition.build_qa_evaluator(agent, prompts, client, schemas)` compiles `qa_agent@v1` from its
  catalogue entry into an `LLMArtifactEvaluator` (evaluator_ref = agent id, `qa-agent@v<n>`,
  Schema fields as shape, scoped Toolbox; declared `skill_refs` → `CompositionError`);
  `build_content_director` / `compile_runtime` take `qa=`, and `validate_qa_evaluator` refuses the
  QA role as a Workflow step. **ADR-0034 §6 decided: a QA failure does not fail the Run** — it
  stays `WAITING_QA`, Evaluation `PENDING`, calls committed, the same error propagates, and
  `resume` asks again (no attempt bound yet — deferred with Evaluation attempts). `FAILED` was
  rejected: it is terminal and would discard the paid producer work. `demo_factory.py` wires the
  QA role (own `client_for_role` binding), reports a QA failure, prints Evaluations/Reviews and has
  `--request-changes "<text>"` to play the reviewer and drive a real rework. Tests:
  `tests/test_qa_wiring.py` (`QWR`, `EVALUATION_ACCEPTANCE.md` §4.4).
- **All 61 ADRs (0001–0061) are recorded; 0057 is Superseded by 0058, the rest Accepted.** Run/Task/Output/Artifact/Human Review (0003–0007),
  Schema + Output validation (0008), Workflow (0009), Agent boundary + Prompt binding
  (0010/0011), Composition Root (0012), execution topology (0013), structured output (0014),
  Run restoration (0015), provider/model selection ownership (0016), shared `DomainError` base
  (0017), Evaluation/QA + fail-closed gate (0018), Artifact versioning (0019), Analytics Record
  (0020), Skills library (0021), Tool Layer (0022), Adapter Layer contracts (0023), Storage
  Adapter (0024), in-memory adapter stubs (0025), storage wiring (0026), the first Skill consumer
  (0027), the bounded LLM Tool-use loop (0028), per-call metrics capture + explicit pricing
  (0029), the external versioned Prompt store (0030), fail-fast Task sequencing with authoritative
  Schema bindings (0031), resumable QA/human rework routing (0032), invalid-Output contract
  errors + the Milestone M2 acceptance (0033), the QA verdict field contract (0034), the QA
  Agent role definition (0035) and QA call metrics attributed to the Evaluation +
  `LLMArtifactEvaluator` (0036), the domain pivot (0037), the QA evaluator wiring (0038), the Stage 8 acceptance (0039), the Notion `BriefBoard` (0040), the status write-back (0041), the Stage 9 acceptance + brief intake (0042), the Google Docs desk
  (0043), the held Approval Gate + publication (0044), the decision fetch + Reject routing (0045),
  the Stage 10 acceptance (0046), the review link on the brief (0047), the brief invocation + Run
  index (0048), the HTTP production service + n8n workflows (0049), the Stage 11 acceptance (0050)
  the Stage 12 acceptance + the M3 pilot boundary (0051) and the per-role `max_tokens` + explicit
  thinking (0052) are all implemented and tested. **ADR-0053** (the clipping department is the next
  work, and its shape), **ADR-0054** (side-effecting Tools over injected ports; Tools observe the
  world rather than change it) **ADR-0055** (the `EpisodeBoard` port and its Notion
  implementation) **ADR-0056** (clip QA: arithmetic is checked deterministically, the model
  judges meaning) **ADR-0057** (superseded) and **ADR-0058** (cut at scene
  boundaries; `CHUNK` / `SCENE`; **v1 has no planner agent**, so ADR-0054 has no consumer yet) and
  **ADR-0059** (one Run per episode; the aggregate already carries it, the orchestrator does not)
  are decisions with **no code yet** — queue task 21. **All the department's decisions are now
  made; 21.5 (specs) and 21.6 (implementation) are what remain.** **ADR-0060** (a Notion
  `ReviewDesk`: typed properties, its own database — queue task 19) is likewise decided and
  unbuilt. **ADR-0061** corrects ADR-0060 §5's ordering: only one Notion module exists, so the
  plumbing extraction waits until three are **written** — desk, then episode board, then extract. All gates green:
  ruff, ruff format, mypy --strict, pytest.
- **A real model can answer the QA gate, and every QA call is recorded (ROADMAP Stage 8,
  ADR-0036); wired by ADR-0038 (above).** `infrastructure/llm.py`
  `LLMArtifactEvaluator` renders the Artifact content into the `qa-agent` template, calls
  `LLMClient.complete` and decodes only through `decode_verdict`; an `LLMError` becomes
  `QaCallError`, a malformed answer a `QaVerdictError` — both `MeasuredEvaluatorError`s carrying
  the completed turns' measurements, never a verdict. **Metrics attribution (the maintainer's
  choice, 2026-09-16):** an `Evaluation` names its `evaluator_ref` (`open_evaluation(...,
  evaluator_ref=)`, taken from the port's new `ArtifactEvaluator.evaluator_ref`); an
  `AnalyticsRecord` has **exactly one subject** — `task_id` *or* `evaluation_id` — and `retries` is
  `None` for an Evaluation record (no attempt model; unknown is not zero). The additive
  `Run.record_evaluation_analytics` derives `agent_ref` from the Evaluation. `record_verdict` records
  every measurement before the verdict, and on a `MeasuredEvaluatorError` records its measurements
  then re-raises the same exception (Evaluation stays `PENDING`); the Director commits before
  re-raising. `DOMAIN_MODEL.md` §2.13/§2.15/§5/§6 were amended. **Snapshot `FORMAT_VERSION` is 2**:
  a Run stored under format 1 (local `.omemo/runs.sqlite3`) is refused — start it again. Tests:
  `tests/test_qa_call_metrics.py` (`AEV`, `EFL-07`, `LAE`).
- **The QA Agent role is defined (ROADMAP Stage 8, ADR-0035); its evaluator exists (ADR-0036).**
  `agents/qa_agent.py`: `qa_agent@v1` → Prompt `qa-agent` v1 (bundled store) → Schema
  `qa-verdict@v1` whose `required_fields` *are* `QA_VERDICT_FIELDS`; no Skills, no Tools. It answers
  with a verdict, not an Output — it goes behind `ArtifactEvaluator`, never into a Workflow step.
  **The v1 System Prompt uses only the documented criteria** (PROJECT.md §1: factual correctness,
  no unsubstantiated medical claims, editorial standards) plus the ADR-0034 grammar — the
  maintainer chose this baseline on 2026-09-16 and **still owes a review**; concrete clinical rules /
  disclaimer wording land as `qa-agent` v2, never as an edit of v1. Tests: `tests/test_qa_agent.py`
  (`QAR`, `EVALUATION_ACCEPTANCE.md` §4.2) pin Prompt/Schema consistency, not wording.
- **ROADMAP Stage 7 / Milestone M2 is closed (ADR-0033).** `tests/test_m2_acceptance.py` (`M2A`,
  `M2_ACCEPTANCE.md`) runs `research-to-script@v1` (Rin → Leo) once through `compile_runtime` with
  only production assets — the bundled Prompt store, Rin's Skill and `current_date` Tool, the real
  `AnthropicLLMClient` (only the transport below the SDK is scripted) and the real
  `SqliteRunStore` — and checks every Stage 7 DoD line in that one pass. Found and fixed on the
  way: an `INVALID` Output used to become an Artifact, feed the next step and let the Run complete.
  Now it stays recorded for audit but stops the plan, never becomes an Artifact or version, and
  fails the Run with `INVALID_OUTPUT_REASON` (in rework: `REWORK_NO_OUTPUT_REASON`). Retry on
  `INVALID` is deferred (needs a Task/Run decision). A live-provider run is the operator's check
  via `demo_factory.py`, which now prints each call's model/tokens/cost/latency/retries/prompt.
- **QA rework routing is real (ROADMAP Stage 7, ADR-0032).** A QA risk still fails closed into
  `WAITING_HUMAN` with an escalation review. When the current candidate's latest decision is
  `CHANGES_REQUESTED`, `ContentDirector.resume` re-enters `RUNNING`, re-executes only that
  candidate's producer on canonical JSON containing the immutable content, latest QA flags and
  human instructions, and turns its validated Output into the successor through
  `Run.create_artifact_version`. Earlier Workflow steps are reused; each iteration appends one
  traced Task and starts fresh QA/Human gates on the new id. The Run's rework bound fails
  observably before a call; a failed/outputless rework never supersedes the candidate. Every new
  storage boundary resumes without duplicating a committed Task or forking the version chain.
  Spec/acceptance: `REWORK_ROUTING_SPEC.md` / `REWORK_ROUTING_ACCEPTANCE.md`; tests:
  `tests/test_rework_routing.py` (`RWR`/`RWF`/`RWS`/`RWG`).
- **The Stage 7 correctness review is closed (ADR-0031).** `ContentDirector` stops a sequential
  plan at its first non-successful Task, so no downstream Task is opened or executor called after
  a failure (including on resume). The Composition Root now preserves each Prompt's exact opaque
  `schema_ref` together with the resolved Schema in an immutable `SchemaBinding`; validation uses
  that Schema and Output recording uses that binding's reference, never the executor's untrusted
  self-report. Existing reference spellings and all Run/Schema domain contracts remain unchanged.
- **Production Prompts are stored outside Python code (ROADMAP Stage 7.5, ADR-0030).** Rin and
  Leo's exact v1 System/User text lives in the bundled `prompts/catalogue.toml`; their role modules
  own only Agent/Schema/Skill/Tool declarations. `composition.load_prompt_catalogue()` strictly,
  atomically materializes `Prompt` descriptors through `importlib.resources`; malformed TOML,
  wrong/blank fields, invalid versions and duplicate ids fail at build-time with
  `CompositionError`. Passing `prompts=None` to the existing Root build functions selects the
  bundled store, while an explicit mapping remains available for tests/embedding. A top-level
  build reads one snapshot and shares it across executor/schema wiring. The wheel includes the
  TOML resource. Spec/acceptance: `PROMPT_STORE_SPEC.md` / `PROMPT_STORE_ACCEPTANCE.md`; tests:
  `tests/test_prompt_store.py` (`PST`).
- **Storage is wired (ROADMAP Stage 7.1, ADR-0026).** `ContentDirector(..., store=RunStore)` saves
  the Run after every **orchestration step**, not every aggregate call: a Task start is committed
  *before* its executor is called, the executor's answer together with its Output + Artifact, the
  QA gate's opening *before* the evaluator, the verdict, and each Run transition (table: ADR-0026
  §2) — so a stored Run never holds a half-recorded step. `ContentDirector.resume` /
  `resume_workflow` continue a loaded Run from its own state: Tasks match requests by position
  (mismatch → `RunResumptionError`, nothing changed), terminal Tasks are not re-run, a `RUNNING`
  one is retried on its stored input (re-entry into `RUNNING`; attempts exhausted → `FAILED` with
  `RESUME_LIMIT_REASON`), a `PENDING` QA Evaluation is finished rather than duplicated, a Run
  waiting for a human is left alone unless its current review is `CHANGES_REQUESTED` (then the
  ADR-0032 rework route runs), and a terminal is left alone. `execute` still requires a `CREATED`
  Run. To commit
  between the halves, `execute_task` = `start_task` + `finish_task` and `evaluate_artifact` =
  `open_evaluation` + `record_verdict` (signatures of the old functions unchanged).
  `composition.build_run_store(environ)` → `SqliteRunStore` at `OMEMO_RUN_STORE_PATH` (default
  `.omemo/runs.sqlite3`, git-ignored); `build_content_director` / `compile_runtime` take `store=`.
  `demo_factory.py` loads-or-creates its Run, so a re-run resumes instead of repeating model calls.
  Tests: `tests/test_storage_wiring.py` (SWR, `ADAPTER_ACCEPTANCE.md` §7) — incl. a crash after
  every one of the 11 commit points, each resuming with exactly one call per step.
- **ROADMAP Stage 6 is complete.** In-memory stubs of the other three contracts exist (Stage 6c,
  ADR-0025, `infrastructure/in_memory_adapters.py`): `InMemoryBriefBoard`, `InMemoryReviewDesk`,
  `InMemoryAnalyticsSink` — infrastructure like `FakeLLMClient`, not test doubles. Each has a
  **control side outside its Protocol** playing the party beyond the wall (`put` = the editor,
  `decide` = the human reviewer; `reports` / `published` / `records` read back what the outside
  sees) — the core only ever holds the Protocol type. Where the contract is silent they **fail
  loudly**: a status on an unknown brief, a different package under a published `review_id`, a
  second different decision, a different record under a delivered `record_id` → the contract's own
  error (a refused export delivers nothing). Tests: `tests/test_in_memory_adapters.py` (STB,
  `ADAPTER_ACCEPTANCE.md` §6). **Not wired** — Stage 7.
- **Run persistence exists (ROADMAP Stage 6b, ADR-0024)**, wired by ADR-0026 (above). Domain: `Run.snapshot` (read-only `RunSnapshot`: everything incl. policies,
  the five id counters and the journal) and `Run.restore(snapshot)` (second factory, no transition,
  no event; verify/reject → `RunRestorationError`), per `RUN_RESTORE_SPEC.md` **1.1** (amended:
  evaluations/analytics + their counters, events' `run_id`, id uniqueness, 1:1, policy bounds,
  counter ≥ every used id number). Children got internal `restore` classmethods only.
  Infrastructure: `SqliteRunStore` (`infrastructure/sqlite_run_store.py`, stdlib `sqlite3`, one row
  per Run = one JSON document) + the type-hint-driven codec `infrastructure/run_snapshot_codec.py`
  (`FORMAT_VERSION = 1`, `EVENT_TYPES` registry pinned by a test to every journal event class — a
  **new event class must be registered there**, and a new snapshot field makes old stored documents
  unreadable → bump `FORMAT_VERSION`; migrations deferred). Malformed storage → `RunStoreError`;
  domain refusals pass through unmasked. Tests: `tests/test_run_restore.py` (RST),
  `tests/test_sqlite_run_store.py` (STO), shared builders in `tests/restorable_runs.py`.
- **Adapter Layer contracts exist (ROADMAP Stage 6a, ADR-0023, `ADAPTER_SPEC.md`)**: the LLM
  Adapter is recognised as already done (`LLMClient`/`AnthropicLLMClient`/`FakeLLMClient`/
  `client_for_role`, still in `infrastructure/`). The other four are `Protocol`s in
  the new contracts-only package **`adapters/`** (imports: pure stdlib + `domain.*`), named by role,
  not vendor: `RunStore` (Storage: `save(run)`/`load(run_id) -> Run | None`), `BriefBoard` (Notion:
  `fetch_brief -> IncomingBrief | None`, `report_status`), `ReviewDesk` (Google Docs:
  `publish(ReviewPackage) -> location`, `fetch_decision -> ReviewDecision | None`), `AnalyticsSink`
  (`export(records)`, a downstream copy — the Run's records stay authoritative). Each has its own
  technical `<Contract>Error` (not a `DomainError`); adapters never mutate a Run.
  `tests/test_adapter_contract.py` enforces the boundary: outside `infrastructure/` no module
  imports a third-party package or network/storage stdlib, and only `composition.py` imports
  `infrastructure`. `RunStore` is implemented (`SqliteRunStore`, ADR-0024); the other three have
  in-memory stubs (ADR-0025). Only `RunStore` is wired (ADR-0026); the other three are not yet.
- **Tool Layer exists (ROADMAP Stage 5, ADR-0022, `TOOL_SPEC.md`)**: passive `ToolDescriptor`
  in `domain/tool.py` (unlike a Skill it carries its declared `ToolParameter`s — the model and the
  Toolbox both read them); executable `Tool` Protocol in `tools/contract.py` (`descriptor` +
  `invoke(arguments, /)`), `ToolCall`/`ToolResult` (`OK`/`REFUSED`/`FAILED`); **`tools/toolbox.py`**
  scopes one agent to its grant — `Toolbox(grants=agent.tool_refs, available=…)`: an ungranted
  name or ill-formed arguments → `REFUSED` and the Tool never runs, `ToolExecutionError` →
  `FAILED`, a bad grant → `ToolGrantError` at construction. Two Tools: `current_date@v1` (clock
  **injected**, never read from the system) and `text_metrics@v1`. `Agent.tool_refs` (default `()`
  = no Tools) is the grant. `tests/test_tool_contract.py` scans `tools/` imports: pure stdlib +
  `domain.tool` only (no `skills` — Skills may depend on Tools — no SDK/clock/agents/Run).
  **Rin now has `current_date@v1` and the Tool-use loop is wired** (ROADMAP Stage 7.3, ADR-0028):
  `LLMClient.complete(..., toolbox=)` runs a bounded provider conversation; Anthropic translates
  only the scoped descriptors, sends every model call through `Toolbox`, feeds the complete
  `ToolResult` back, and finishes through its private structured-output Tool. The Root builds one
  Toolbox per Agent and injects the date Tool's aware local clock. The default limit is 8
  operational calls per Task step; an over-budget batch runs nothing and becomes a managed LLM
  failure. ADR-0029 now records every completed provider turn around the loop; Tool arguments and
  results themselves remain transient.
- **Skills library exists and has its first consumer (ROADMAP Stage 4 + Stage 7.2, ADR-0021/0027,
  `SKILL_SPEC.md`)**: passive
  `SkillDescriptor` in `domain/skill.py` (like `Agent`), executable `Skill[In, Out]` Protocol in
  `skills/contract.py` (`descriptor` + pure `apply(input, /)`), three deterministic Skills —
  `segment_text@v1`, `normalize_terminology@v1`, `check_required_elements@v1` — and
  `skills/catalogue.py`. `tests/test_skill_contract.py` scans `skills/` imports: only pure stdlib,
  `domain.skill` and `skills.*` — a Skill importing an agent/Run/application/clock fails the
  gate. `Agent.skill_refs` is now passive ordered configuration (default `()`); Rin declares and
  invokes `normalize_terminology@v1` on its input before the LLM call through
  `SkillPreprocessingTaskExecutor`. The Task retains the original input, retry re-applies the pure
  Skill, and the Composition Root requires invocation refs to exactly match the Agent declaration
  before execution. The `Deprecated` status and a persisted per-invocation trace remain deferred.
- **LLM metrics capture is wired** (ROADMAP Stage 7.4, ADR-0029; amends ADR-0020's deferral).
  `LLMClient.complete` returns opaque fields plus one provider-neutral measurement per completed
  provider turn; Anthropic uses response-reported actual model/tokens, an injected aware clock and
  explicit per-role `Decimal` rates, while Fake truthfully reports zero inference tokens/cost.
  Tool loops retain every turn in order and managed failures retain already completed turns.
  `LLMTaskExecutor` attaches `<prompt_id>@v<version>` and `finish_task` records each measurement via
  `Run.record_analytics` before Task finalization, so Run derives `run_id`/`task_id`/`agent_ref` and
  retries. An Anthropic binding without valid input/output prices + currency fails closed; no tariff
  is hardcoded or guessed. Aggregation/export, provider-side retries, failed requests without a
  provider response, and Tool payload tracing remain deferred.
- **QA gate is fail closed** (ADR-0018, `domain/evaluation.py`, `EVALUATION_SPEC.md`): an
  Artifact reaches `APPROVED` only with an approving Human Review **and** a `PASSED` *latest*
  Evaluation — no/pending/`FLAGGED`/`FAILED` QA blocks it even after a human Approve.
  `ContentDirector(..., qa=evaluator)` evaluates the final step's Artifact at `WAITING_QA`;
  a risk verdict stops the Run at `WAITING_HUMAN` with an escalation review. No entrypoint
  wires a real QA evaluator yet — that is the QA Agent (ROADMAP Stage 8).
- **Domain errors share one root**: every per-aggregate base (`RunDomainError`,
  `TaskDomainError`, …) subclasses `DomainError` (`domain/errors.py`, ADR-0017). A new
  aggregate must root its own error base there; `tests/test_domain_error.py` enforces it.
- **Two real production roles are migrated and chained**: `content_researcher@v1` (Rin) and
  `script_writer@v1` (Leo) — `src/omemo_content_factory/agents/`. Each is proven alone
  (`tests/test_content_researcher_agent.py`, `tests/test_script_writer_agent.py`) and together
  as a real two-step Workflow (`tests/test_research_to_script_workflow.py`,
  `demo_factory.py`).
- **The Artifact lifecycle is fully wired** (ADR-0006/0007/0018/0019): `DRAFT→CANDIDATE→APPROVED→
  PUBLISHED`, `CANDIDATE→REJECTED` (approval gated by Human Review + QA) and, from every *working*
  state (`DRAFT`/`CANDIDATE`/`APPROVED`), `→SUPERSEDED`. A fixed version is immutable: rework goes
  through `Run.create_artifact_version(previous, output, by=…)`, which supersedes the predecessor
  and creates the successor (`version` + 1, `supersedes_ref`, its own Output) in one operation.
  `SUPERSEDED` is deliberately **not** reachable via `transition_artifact`
  (`ArtifactSupersessionError`). A new version inherits neither the predecessor's approval nor its
  QA verdict — the gates key on the artifact id, so rework restarts the lifecycle by construction.
  Orchestrating rework (ContentDirector routing a reviewed risk verdict into a re-run) is done —
  ADR-0032.
- **`client_for_role` is wired into `demo_factory.py`** (ADR-0016 realization,
  `infrastructure/provider_model.py`): each role resolves its own provider/model plus explicit token
  prices/currency, with no shared/default client — an incomplete binding fails closed
  (`ProviderModelSelectionError`), and the demo prints the exact variables still needed.
  `demo.py` remains the older non-catalogued entrypoint but now also requires explicit global
  pricing so it cannot record a guessed cost.

## Session workflow (push straight to main — no branch/PR ceremony needed)

`main` is not protected (CONTRIBUTING.md "Branching", updated 2026-09-15 — PRs turned out to be
pure overhead for a solo maintainer + AI assistants, so the earlier "no direct pushes" rule was
dropped). For every task in the queue below:

1. Build → Test locally (the quality gate below) → Commit (Conventional Commits) directly on
   `main` (`git pull --ff-only` first if it's been a while since you last synced).
2. **Push straight to `origin main` — this is pre-authorized, do not stop to ask.** CI
   (`.github/workflows/ci.yml`) runs on the push as a post-hoc check; if it goes red, the next
   session's first job is fixing it forward (`git revert` only if a fix-forward isn't quick).
3. A short-lived branch + PR is still fine when *you* want CI green before landing (a large or
   risky change, e.g. one touching `Run`'s public contract), or when two sessions are working
   concurrently and a PR avoids interleaving unfinished work — but it's your call, not the
   default, and never something to ask permission for either way.

(History: PRs #1-#4 in this repo predate this rule and went through branch+PR; that was the
process at the time, not a pattern to keep copying.)

## Next tasks (ordered queue — one task per session; each ends Build → Test → Commit → Push/PR)
1. ~~Actualize this file~~ — done.
2. ~~Extract the shared `DomainError` base~~ — done (ADR-0017, `domain/errors.py`, merged via
   [PR #1](https://github.com/westtonery-gif/omemo-content-factory/pull/1)).
3. ~~Evaluation / QA entity~~ — done (ADR-0018, `domain/evaluation.py`,
   `application/qa_evaluation.py`; wired in `ContentDirector`, where `WAITING_QA` happens).
4. ~~Artifact versioning (`SUPERSEDED`)~~ — done (ADR-0019, `Run.create_artifact_version` +
   `domain/artifact.py`, `tests/test_artifact_versioning.py`; `artifact.py`'s stale module
   docstring fixed in the same change). The domain rework path exists now; **routing** a QA risk
   verdict / `CHANGES_REQUESTED` into a re-run that produces the new version is still open
   (ADR-0019 "Deferred", ROADMAP Stage 7/8).
5. ~~Analytics Record entity~~ — done (ADR-0020, `domain/analytics.py`, `Run.record_analytics`,
   `tests/test_analytics_record.py`; the long-skipped INV-07 integrity test in `tests/test_run.py`
   now runs). Scope check (the Stage-14 / scope-discipline concern raised here): ROADMAP Stage 2
   doesn't list it, but RUN_SPEC / RUN_ACCEPTANCE (AGG-05, INV-09) already make it part of the Run
   aggregate contract and ARCHITECTURE_FREEZE §3 asks for exactly ADR → SPEC → tests; it was kept
   domain-only (no capture, aggregation, adapter or agent) and the maintainer confirmed landing
   it. Capturing a record on every real call stays Stage 14 (ADR-0020 "Deferred").
6. ~~Wire `client_for_role` into a real entrypoint~~ — done (`demo_factory.py`: each role
   resolves its own provider/model via `build_executor_map` called once per agent + merged;
   `demo.py` untouched, out of scope).
7. ~~ROADMAP Stage 4–6 (Skills library / Tool Layer / Adapter Layer)~~ — done, broken down below
   into session-sized subtasks. Prerequisite for Stage 7 (first Agent through the full
   orchestrator) and the Stage 12 MVP.
   1. ~~**Skills library** (Stage 4)~~ — done (ADR-0021, `domain/skill.py` + `skills/`,
      `SKILL_SPEC.md` / `SKILL_ACCEPTANCE.md`, `tests/test_skill_*.py`). Thesis extraction was
      deliberately not taken: done well it is LLM work (a role), not a deterministic Skill.
   2. ~~**Tool Layer** (Stage 5)~~ — done (ADR-0022, `domain/tool.py` + `tools/` incl.
      `toolbox.py`, `Agent.tool_refs`, `TOOL_SPEC.md` / `TOOL_ACCEPTANCE.md`,
      `tests/test_tool*.py` + `tests/test_toolbox.py`). The tool-use loop in the LLM adapter was
      deliberately not built: it changes the ADR-0014 port and has no consumer before Stage 7.
   3. ~~**Adapter contracts** (Stage 6a)~~ — done (ADR-0023, `adapters/` — `RunStore`,
      `BriefBoard`, `ReviewDesk`, `AnalyticsSink`; `ADAPTER_SPEC.md` / `ADAPTER_ACCEPTANCE.md`,
      `tests/test_adapter_contract.py`). The LLM Adapter was recognised as done, not moved. The
      reviewer's identity was deliberately left off `ReviewDecision`: `Run.submit_review` records
      only the actor role, so the field would have no reader (ADR-0023 "Deferred", Stage 10).
   4. ~~**Storage Adapter** (Stage 6b)~~ — done (ADR-0024: `RUN_RESTORE_SPEC.md` /
      `RUN_RESTORE_ACCEPTANCE.md` amended to 1.1 first, then `Run.snapshot` / `Run.restore`, then
      `SqliteRunStore` + codec; `ADAPTER_ACCEPTANCE.md` §5 STO). Found while amending: spec 1.0's
      counter check (`seq ≥ |children|`) let a lone `…-task-3` with `task_seq = 2` through (next
      `open_task` would overwrite it), and RST-03 expected a Run-level Approve guard that doesn't
      exist (the gate is on the Artifact, ADR-0007 §6) — both corrected in 1.1. Saving after each
      transition (the wiring) was deliberately left to Stage 7 (ADR-0024 "Deferred").
   5. ~~**Notion / Google Docs / Analytics adapter stubs** (Stage 6c)~~ — done (ADR-0025,
      `infrastructure/in_memory_adapters.py`, `ADAPTER_SPEC.md` §5–§7 "Реализация (6c)",
      `ADAPTER_ACCEPTANCE.md` §6 STB, `tests/test_in_memory_adapters.py`). Kept in-memory and in
      one module; the cases the contract leaves open were decided as fail-loud (ADR-0025 §3).
      Failure injection (a stub raising on demand, to test that a failed report/export never
      blocks the Run) was deliberately left to Stage 7 wiring — no reader before then.
8. ~~**ROADMAP Stage 7 — first real Agent through the full orchestrator (Milestone M2)**~~ — done
   (closed by subtask 8 below, ADR-0033).
   Checked line-by-line against Stage 7's own DoD (ROADMAP.md): the two migrated roles (Rin/Leo)
   already satisfy "input → reasoning → Structured Output → recorded in Run" and "invalid output
   -> contract error", but nothing here uses a Skill or a Tool, no adapter is wired into a real
   run, no call produces an Analytics Record, and a Prompt is a Python string literal, not
   something "stored separately from code". Broken into subtasks:
   1. ~~**Storage wiring**~~ — done (ADR-0026, `ContentDirector(store=…)` + `resume` /
      `resume_workflow`, `composition.build_run_store`, `demo_factory.py` load-or-create,
      `ADAPTER_SPEC.md` §4 "Проводка", `ADAPTER_ACCEPTANCE.md` §7 SWR,
      `tests/test_storage_wiring.py`). Commit granularity was decided as one orchestration step,
      not one aggregate call (a stored `SUCCEEDED` Task always has its Output + Artifact); an
      uncommitted executor/evaluator call is at-least-once across a crash (ADR-0026 §4). Deliberately
      left: listing unfinished Runs for a "resume everything" entrypoint, and the human-decision
      round-trip at `WAITING_HUMAN` (Stage 10 / subtask 6).
   2. ~~**First Skill consumer**~~ — done (ADR-0027: `Agent.skill_refs` +
      `TaskInputSkillInvocation` / `SkillPreprocessingTaskExecutor`; Rin applies
      `normalize_terminology@v1` to the brief before its LLM call, while the Task keeps the original
      input; Composition Root fails at build time when declaration and invocation bindings differ;
      `SKILL_SPEC.md` / `SKILL_ACCEPTANCE.md` 1.1, tests `SCI`).
   3. ~~**Tool-use loop**~~ — done (ADR-0028 amends ADR-0014 and realizes ADR-0022's deferred
      loop: `LLMClient.complete(..., toolbox=)`, bounded Anthropic multi-turn translation,
      `ToolCall → Toolbox → ToolResult` round-trip, private `emit_fields` finalization, Root-built
      per-Agent Toolboxes; Rin is granted `current_date@v1`, proven mid-reasoning by `LTL`).
   4. ~~**Metrics capture — now, not Stage 14**~~ — done (ADR-0029 amends ADR-0020/0014/0028:
      `LLMCompletion` carries one measured record per completed provider turn; Tool loops and
      managed failures retain all observed turns; `TokenPricing` uses explicit exact per-role rates;
      the Root injects `<prompt_id>@v<version>`; `finish_task` records through Run before Task
      finalization. `ANALYTICS_RECORD_SPEC` / acceptance and `PROVIDER_MODEL_SPEC` / acceptance are
      1.1; tests `MTC`).
   5. ~~**Prompt stored separately from code**~~ — done (ADR-0030,
      `PROMPT_STORE_SPEC.md` / `PROMPT_STORE_ACCEPTANCE.md`: Rin/Leo's exact v1 System/User text
      moved from `agents/*.py` to the bundled, versioned `prompts/catalogue.toml`; the Composition
      Root loads and strictly validates it fail-closed when `prompts=None`, once per top-level build;
      explicit Prompt mappings remain supported and the built wheel contains the TOML resource;
      tests `PST`).
   6. ~~**Two correctness bugs found in code review**~~ — done (ADR-0031). `_run_steps` now
      stops at the first non-successful Task and leaves later requests unopened; resumption obeys
      the same fail-fast boundary. `SchemaBinding` keeps the trusted opaque catalogue reference
      beside the exact Schema authority; `validate_and_record_output` validates with that object
      and records that reference, normalizing away any different executor self-report. Tests cover
      both regressions.
   7. ~~**QA rework routing**~~ — done (ADR-0032: QA risk retains the ADR-0018 fail-closed human
      escalation; `CHANGES_REQUESTED` makes `resume` append and execute one Task for the current
      candidate's producer, then create the successor with `Run.create_artifact_version`; canonical
      JSON feedback input, bounded failure and crash-safe resumption are specified in
      `REWORK_ROUTING_SPEC.md` / `REWORK_ROUTING_ACCEPTANCE.md`, tests `RWR`/`RWF`/`RWS`/`RWG`).
   8. ~~**Bring it together (Milestone M2 acceptance)**~~ — done (ADR-0033, `M2_ACCEPTANCE.md`,
      `tests/test_m2_acceptance.py` `M2A`): one pass through `compile_runtime` exercising Storage +
      a Skill + a Tool + captured metrics + the externally-stored prompt. The DoD's "invalid output
      -> contract error" line turned out to be unmet (an `INVALID` Output was chained and turned
      into an Artifact) and is now an error state; the retry half is deferred (ADR-0033 §3).
9. ~~**(Not yet — Stage 13, after the Stage 12 MVP.)**~~ — **started 2026-09-21 (ADR-0065)**, as
   task 23's narrow v1 slice (image + video generation to one approved file). Original scope
   (TTS, automated splicing/overlay across formats, the rest of Stage 13's agents) is still not
   authorized — only what ADR-0065 states. See task 23 for the subtask breakdown.
10. **(Not yet — read `CONTENT_FACTORY_THOUGHTS.md` in full before touching this.)** Once task 8
    (Stage 7, M2) is closed, the maintainer has a detailed exploratory design for the eventual
    video vertical slice — a full product vision (multi-tenant faceless-reel factory: idea
    generation/ranking, script/storyboard/production-plan roles, image/video/TTS generation,
    deterministic assembly, a multi-stage QA cascade with targeted repair routing, cost control,
    multi-tenancy) reconciled against this exact codebase. It is explicitly a working note, not
    an accepted plan (see the doc's own header) — §16 proposes: close Stage 7 → Stage 8 (real QA
    Agent + rework routing) → then explicitly decide, via ADR, whether to keep Stage 9-12 (text
    MVP) before Stage 13, or carve out an earlier narrow video slice. §19 has the doc's own
    suggested opening question for whichever session picks this up. Do not start any of §16's
    "video vertical slice" work, or reorder Stage 8-13, without that explicit ADR decision first.
11. ~~**ROADMAP Stage 8 — QA Agent.**~~ — done (closed by subtask 5 below, ADR-0039). (ROADMAP order; also `CONTENT_FACTORY_THOUGHTS.md` §16's
    first step after Stage 7). The fail-closed gate (ADR-0018) and rework routing (ADR-0032)
    already exist; `application/qa_evaluation.py`'s `ArtifactEvaluator` Protocol
    (`evaluate(content: str) -> EvaluationResult`) is the seam — its own docstring already says
    "the QA Agent (ROADMAP Stage 8) implements the same contract." What's missing is a real role
    behind it, on the same template as Rin/Leo (Stage 7): Agent + Prompt (bundled store, ADR-0030)
    + Schema + optionally Skills/Tools. Broken into subtasks:
    1. ~~**Verdict shape — needs a small ADR.**~~ — done (ADR-0034, `EVALUATION_SPEC.md` §8.1,
       `EVALUATION_ACCEPTANCE.md` §4.1 `QVD`, tests in `tests/test_qa_evaluation.py`). Two fields,
       `QA_VERDICT_FIELDS = ("verdict", "flags")`: `verdict` is exactly `passed`/`flagged`/`failed`
       (case and surrounding whitespace ignored, nothing else), `flags` is a JSON array of non-blank
       strings (`[]` = none), and a risk verdict needs at least one flag. The pure
       `decode_verdict(fields) -> EvaluationResult` in `application/qa_evaluation.py` is the sole
       judge; any violation raises `QaVerdictError`, which fails closed exactly like any evaluator
       failure (Evaluation stays `PENDING`) and is never guessed into a verdict. The allowed-value
       check was deliberately **not** put into `Schema` (the QA path never calls
       `Schema.validate` — it records no Output) nor into the port's tool schema (Variant B).
    2. ~~**QA Agent role: Prompt + Schema + catalog entry**~~ — done (ADR-0035,
       `agents/qa_agent.py`, `qa-agent` v1 in `prompts/catalogue.toml`, `EVALUATION_SPEC.md` §8.2,
       `EVALUATION_ACCEPTANCE.md` §4.2 `QAR`, `PROMPT_STORE_ACCEPTANCE.md` 1.1). Asked the
       maintainer first: v1 criteria are the documented principles only, **pending their review**
       (→ a v2 Prompt). `check_required_elements@v1` was deliberately **not** granted: the QA path
       has no Skill-invocation seam (ADR-0027 wraps a `TaskExecutor`), and the disclaimer wording
       is domain content too — revisit with 11.3. **The pending review resolved itself (ADR-0037):**
       there is no health domain any more, so v1's medical criteria are simply off-target and the
       v2 Prompt carries uniqueness + client rules instead — see task 12.
    3. ~~**`LLMArtifactEvaluator`**~~ — done (ADR-0036, `infrastructure/llm.py`,
       `EVALUATION_SPEC.md` §8.3, `EVALUATION_ACCEPTANCE.md` §4.3 `LAE`,
       `ANALYTICS_RECORD_SPEC.md` / `ANALYTICS_RECORD_ACCEPTANCE.md` 1.2 `AEV`,
       `RUN_RESTORE_SPEC.md` 1.2, `tests/test_qa_call_metrics.py`). The metrics question was put to
       the maintainer, who chose **attribution to the Evaluation** over a QA Task (would have
       broken the Director's positional Task matching and ADR-0035 §4) and over "return but don't
       record" (knowingly breaks PROJECT.md §16). An `LLMError` is not swallowed: it is re-raised
       as `QaCallError` chained `from` it, because the application layer may not import
       infrastructure and still has to receive the failed call's measurements.
    4. ~~**Wire it into a real entrypoint**~~ — done (ADR-0038, `composition.build_qa_evaluator` /
       `validate_qa_evaluator`, `qa=` on `build_content_director` / `compile_runtime`,
       `demo_factory.py` incl. `--request-changes`, `EVALUATION_SPEC.md` §8.4,
       `EVALUATION_ACCEPTANCE.md` §4.4 `QWR`, `tests/test_qa_wiring.py`). Decided without asking
       (a technical call, reversible): a QA failure keeps the Run at `WAITING_QA` for `resume`
       rather than routing to `FAILED`. Verified offline with scripted models through the demo
       (QA error → re-run → `flagged` → `--request-changes` → rework → `passed`); a live-provider
       run is still the operator's check. Original brief: extend Composition Root helpers (or add a small
       analogous one) to build the QA evaluator from its catalog entry the same way
       `build_executor_map` + `client_for_role` do for Rin/Leo, then pass `qa=` into
       `ContentDirector` in `demo_factory.py` (or a new demo). This is the **first real exercise**
       of a risk verdict actually produced by a model, not a test fake — watch specifically that
       ADR-0032's rework routing fires correctly off a real `FLAGGED`/`FAILED`. Also decide here
       (ADR-0034 §6) how the Director surfaces a propagated `QaVerdictError`/`LLMError`: leave the
       Run in `WAITING_QA` with a `PENDING` Evaluation for `resume`, or route it to `FAILED` with a
       stable reason as ADR-0033 did for an `INVALID` Output. Build the evaluator with
       `evaluator_ref=qa_agent@v1`, `prompt_ref=qa-agent@v<version>`, the Schema's
       `required_fields` as `output_fields`, and a `client_for_role` binding for the QA role
       (explicit pricing required). Already in place from 11.3: the Director opens the Evaluation
       with `evaluator_ref` and commits recorded QA calls before re-raising a
       `MeasuredEvaluatorError` (today it propagates out of `execute`/`resume`); a non-measured
       exception still propagates without that extra commit.
    5. ~~**Stage 8 acceptance**~~ — done (ADR-0039, `STAGE8_ACCEPTANCE.md`,
       `tests/test_stage8_acceptance.py` `S8A`; no behaviour change needed). Original brief: a test in the shape of `test_m2_acceptance.py` proving both DoD
       lines end to end: a `PASSED` verdict lets a run complete, a risk verdict fail-closes to
       `WAITING_HUMAN` with escalation and, on `CHANGES_REQUESTED`, drives a real rework loop
       (ADR-0032) — covered with a realistic evaluator, not necessarily a live API call.

12. ~~**Retarget the Prompts to the new domain (follow-up of ADR-0037) — corrected mechanism.**~~ —
    done, no new ADR (this is content, reviewed like code per `PROJECT.md` §15, not an
    architectural decision — matches this section's own correction below). `prompts/catalogue.toml`
    §1/§3 allows **exactly one active record per `prompt_id`**, and `agents/*.py` reference a
    Prompt by bare id — so bumping the version was editing that one TOML record's `version` +
    `system` (+, unchanged, `user_template`) fields in place; no `agents/*.py` change. A past Run's
    trace stays honest because it already recorded `<prompt_id>@v<n>` at the time it ran, not
    because old text is still loadable — that history lives in git, not the live catalogue.
    Subtasks:
    1. ~~**`qa-agent` → v2.**~~ — done. Criteria per `PROJECT.md` 1.3 §1/§4 п.9 and
       `ARCHITECTURE.md` §3.9: uniqueness (not templated, not a repeat of the client's own or a
       competitor's material), factual correctness, no unsubstantiated claims (promised outcomes,
       guarantees, unverifiable claims — generalized from v1's medical-claim list, same shape),
       the client's editorial rules from the input context; regulated-niche rules only when a
       client profile supplies them (none exists yet, so none was invented). The ADR-0034 verdict
       grammar is unchanged; `tests/test_qa_agent.py` (`QAR`) needed only its `PromptVersion(1)` →
       `(2)` assertion bumped, not a rewrite (it pins consistency, not wording, as expected).
    2. ~~**`content-researcher` + `script-writer` → v2.**~~ — done. Dropped "видео-фабрики OMEMO"
       and the health-adjacent framing; kept the shape (audience/angle; title/hook/script) and
       added one line that the result must not repeat the client's own or a competitor's material
       (`PROJECT.md` §1 value #1). `user_template` for all three roles was already domain-neutral
       and is unchanged.
    3. ~~**Verify end to end.**~~ — done. `tests/test_prompt_store.py` (PST-01/03),
       `tests/test_qa_wiring.py` (QWR-01) and `tests/test_metrics_capture.py` (MTC) assert the real
       bundled catalogue now resolves to `content-researcher@v2` / `script-writer@v2` /
       `qa-agent@v2` through `build_executor_map`/`build_qa_evaluator`/`ContentDirector`, not just
       the TOML file in isolation. Full quality gate green (892 passed, unchanged count — no new
       behavior). Two small drive-by corrections, same file/section, plain non-versioned metadata
       (not Prompt text): `qa_agent.py`'s stale "health-compliance"/"health content"
       docstring/`Agent.description`, and `EVALUATION_SPEC.md` §1/§8.2's stale "домена
       здоровья"/"медицинских утверждений" prose. `PROMPT_STORE_ACCEPTANCE.md` bumped to 1.2;
       `EVALUATION_SPEC.md` / `EVALUATION_ACCEPTANCE.md` amended (§8.2, QAR-02, QWR-01). Left for
       later, out of scope: the `omemo_content_factory` package name, `DOMAIN_MODEL.md`'s own
       health mention, and `content_researcher.py`'s "омемо"→"OMEMO" terminology-glossary
       invocation (a Skill invocation, not Prompt text).

13. ~~**ROADMAP Stage 9 — Notion integration.**~~ — done (closed by subtask 4 below, ADR-0042). (ROADMAP order.) Dependencies: Stages 6, 7 —
    both done. The `BriefBoard` contract already exists (`adapters/brief_board.py`, ADR-0023:
    `fetch_brief(brief_ref) -> IncomingBrief | None`, `report_status(brief_ref, *, run_id,
    status)`) and has an in-memory stub (`InMemoryBriefBoard`, ADR-0025) — what's missing is a
    **real** implementation and its wiring, the same split Storage went through (ADR-0024 real
    impl, then ADR-0026 wiring). No n8n at this stage (Stage 11) — a manual/direct trigger is
    fine. Subtasks:
    1. ~~**Real `NotionBriefBoard`.**~~ — done (ADR-0040, `infrastructure/notion_brief_board.py`,
       `ADAPTER_SPEC.md` §5 "Реализация (Этап 9)", `ADAPTER_ACCEPTANCE.md` §8 `NBB`,
       `tests/test_notion_brief_board.py`). Decided: **stdlib `urllib`, no new dependency**; body from
       page blocks, readiness from a `status`/`select` property, statuses into two `rich_text`
       properties; six required `OMEMO_NOTION_*` variables, no defaults. No Composition Root builder
       yet — `build_brief_board(environ)` belongs to 13.2. Original brief: First non-Anthropic third-party dependency (`pyproject.toml`
       `dependencies` currently has only `anthropic`) — decide the Notion client library (official
       `notion-client` vs. raw HTTP) as part of this subtask, not before. Maps a Notion
       database entry to `IncomingBrief` (`brief_ref`, `body`) and writes `RunStatus` back to a
       status property. Lives in `infrastructure/` (only place allowed to import a third-party
       package or do network I/O, per `tests/test_adapter_contract.py`'s boundary). Needs its own
       ADR + spec/acceptance, mirroring ADR-0024's realization pattern; auth via env vars
       following the `client_for_role`/`OMEMO_PROVIDER__` convention, not hardcoded.
    2. ~~**Brief → Run entrypoint**~~ — done. `composition.build_brief_board(environ)` (mirrors
       `build_run_store`) plus a dedicated `demo_notion.py` (not a `demo_factory.py` edit — kept
       the hardcoded-brief demo intact, imported its executor/QA-building/reviewer-request
       helpers instead of duplicating them): `board.fetch_brief(brief_ref)` → `Run.create` →
       `execute_workflow`/`resume_workflow`, exactly `demo_factory.py`'s shape with the brief
       sourced from Notion (a missing/not-ready/textless brief is the same `None`, printed and a
       clean exit). `run_id = f"run-notion-{brief_ref}"` keyed by brief so a re-run resumes the
       same Run. Verified end to end against a local fake-Notion HTTP server with
       `OMEMO_PROVIDER__*=fake` for all three roles (throwaway script, not committed): brief
       fetched over real HTTP → Rin/Leo executed → QA correctly rejected `FakeLLMClient`'s
       non-grammar verdict and parked at `WAITING_QA` (fail-closed, ADR-0038) → second run resumed
       without re-executing Rin/Leo, asked QA again. README documents the new entrypoint.
       Status write-back is explicitly out of scope here — that's 13.3, next.
    3. ~~**Status write-back — needs a design decision.**~~ — done (ADR-0041,
       `application/brief_status.py` `BriefStatusReporter`, `ADAPTER_SPEC.md` §5 "Обратная запись
       статусов", `ADAPTER_ACCEPTANCE.md` §9 `BSR`, `tests/test_brief_status.py`, `demo_notion.py`).
       Decided without asking (technical, reversible): **every** committed status change, reported
       after the save, through a `RunStore` decorator rather than a `board=` on the Director (core
       untouched). Filtered/resting-only reporting was rejected: `running` is the longest-lived
       state an editor wants to see. A refused report is logged and kept, never fails the Run; it is
       retried by `sync`, not by every later commit. Original brief: decide which `Run` transitions
       get reported (every one, or only `QUEUED`/`WAITING_HUMAN`/`COMPLETED`/`FAILED`) and wire
       `report_status` into the entrypoint or `ContentDirector`.
    4. ~~**Stage 9 acceptance**~~ — done (ADR-0042, `STAGE9_ACCEPTANCE.md`,
       `tests/test_stage9_acceptance.py` `S9A`, `application/brief_intake.py`). Unlike Stage 8 it
       found a gap: the brief → Run flow lived untested in `demo_notion.py`'s `main` and resumed on
       `brief=""`, so a crash before the first Task ran Rin on an empty brief. Extracted into
       `produce_brief` (resume uses the first Task's input, or re-fetches the brief when no Task
       exists). Storing the brief body in the Run was rejected (snapshot format bump for a
       two-commit window). Original brief: a test in the `test_stage8_acceptance.py` shape proving the DoD
       (a filed brief produces a valid Run; statuses land back on the board), run against
       `InMemoryBriefBoard` for CI like Stage 8 used scripted transport under a real
       `AnthropicLLMClient`. A live Notion round-trip is the operator's manual check afterward
       (needs a real integration token + database — not available in this environment), the same
       role `demo_factory.py` plays for live LLM calls.

14. ~~**ROADMAP Stage 10 — Google Docs integration.**~~ — done (closed by subtask 4 below, ADR-0046). (ROADMAP order.) Dependencies: Stages 6, 7
    — both done. Same shape as Stage 9 (task 13): the `ReviewDesk` contract
    (`adapters/review_desk.py`, ADR-0023: `publish(ReviewPackage) -> str`,
    `fetch_decision(review_id) -> ReviewDecision | None`) and its in-memory stub
    (`InMemoryReviewDesk`, ADR-0025) exist; missing are a real implementation and its wiring. Two
    real design decisions Notion's adapter didn't have to make — flag them, don't guess:
    - **Auth is heavier.** Notion needed one bearer token; Google Docs/Drive needs OAuth2 or a
      service account (credentials JSON, scopes, token refresh). Decide the mechanism as part of
      14.1, following the `client_for_role`/`OMEMO_NOTION_*` convention of explicit env-sourced
      config, never hardcoded.
    - **There is no "Approve" button in a Google Doc.** `ReviewPackage`/`ReviewDecision` assume
      *some* way to read a terminal decision back from the desk, but neither `ARCHITECTURE.md`
      §3.11/§12/§13 nor `ADAPTER_SPEC.md` specify the mechanism (a comment? a marker line the
      reviewer types/selects? a suggestion?). This needs its own small ADR before 14.1 writes code,
      not an improvised choice buried in the implementation.
    Subtasks:
    1. ~~**Real `GoogleDocsReviewDesk`.**~~ — done (ADR-0043,
       `infrastructure/google_docs_review_desk.py`, `ADAPTER_SPEC.md` §6 "Реализация (Этап 10)",
       `ADAPTER_ACCEPTANCE.md` §10 `GDR`, `tests/test_google_docs_review_desk.py`). Decided by the
       maintainer when asked: service account auth; a `РЕШЕНИЕ:`/`ПРИЧИНА:` marker block above a
       separator line. Decided here: Drive API v3 only over `urllib`, `cryptography` for RS256 (the
       one new dependency). No `build_review_desk` yet — belongs to 14.2. Original brief: ADR first for the two decisions above, then the
       implementation: `publish` creates/updates a Doc with the candidate content + brief + QA
       flags (`ReviewPackage`'s existing fields) and returns its location; `fetch_decision` reads
       whatever mechanism the ADR chose. Second non-Anthropic third-party surface after Notion —
       decide the library (official `google-api-python-client` vs. raw REST) in this subtask.
    2. ~~**Publish wiring.**~~ — done (ADR-0044, `application/review_publication.py`,
       `composition.build_review_desk`, `demo_notion.py`, `EVALUATION_SPEC.md` §9,
       `ADAPTER_SPEC.md` §6, `EVALUATION_ACCEPTANCE.md` §4.5 `APG`, `ADAPTER_ACCEPTANCE.md` §11
       `RPB`, `tests/test_review_publication.py`). Grew a gate change the maintainer chose when
       asked: a `PASSED` candidate no longer completes on its own — it waits at `WAITING_HUMAN` with
       a review, and `APPROVED` + `PASSED` completes on `resume`. Decided without asking
       (technical, reversible): publish from the entrypoint after the Director returns, not from
       the Director or a `RunStore` decorator. Original brief: `WAITING_HUMAN` gets a real desk the
       way `WAITING_QA` got a real evaluator (ADR-0038): build the `ReviewPackage` from the Run's
       candidate Artifact, brief and the QA Evaluation's flags, and call `desk.publish`.
    3. ~~**Decision-fetch wiring.**~~ — done (ADR-0045, `application/review_decision.py`,
       `REWORK_ROUTING_SPEC.md` §1–§3, `EVALUATION_SPEC.md` §9, `ADAPTER_SPEC.md` §6,
       `ADAPTER_ACCEPTANCE.md` §12 `RDF`, `tests/test_review_decision.py`). The maintainer chose
       Reject = rework with the reason, and kept the approved escalation deferred. Original brief: `desk.fetch_decision(review_id)` → `Run.submit_review(...,
       by=Actor.HUMAN_REVIEWER, reason=...)` for the pending review, then `resume` — the Director
       already routes `APPROVED` (ADR-0044 §1) and `CHANGES_REQUESTED` (ADR-0032), so this is the
       application/entrypoint side only, probably next to `publish_pending_review`. Replaces/
       complements the demos' manual `--approve` / `--request-changes` (they stay for keyless/local
       testing). A pending decision (`None`) leaves the Run at `WAITING_HUMAN` for the next
       invocation to check again. Decide there what a fetched `REJECTED` does to the Run — still
       deferred (ADR-0032, ADR-0044 §2) and a domain decision: ask, don't guess.
    4. ~~**Stage 10 acceptance**~~ — done (ADR-0046, `STAGE10_ACCEPTANCE.md`,
       `tests/test_stage10_acceptance.py` `S10A`, `take_review_decision`). Found: the entrypoint's
       decision step was untested demo code (extracted), and a wrong "not decided yet" message after
       a crash. Original brief: a test in the `test_stage9_acceptance.py` shape (`S10A`,
       `STAGE10_ACCEPTANCE.md`) against `InMemoryReviewDesk` for CI; a live Google Docs round-trip
       is the operator's manual check (needs real credentials, not available in this environment) —
       likely a `demo_notion.py`-style entrypoint, or an extension of it.

15. ~~**ROADMAP Stage 11 — n8n integration.**~~ — done (closed by subtask 4 below, ADR-0050). (ROADMAP order.) Dependencies: Stages 9, 10 — both
    done. (Supersedes the first breakdown of e31faa9, whose open trigger question is now answered;
    its gap check — the Doc link never reaches Notion — is confirmed and became 15.1.) DoD: creating
    a brief in Notion starts a `Run` through n8n automatically; business logic stays in the Content
    Director, n8n is transport/triggers only (ARCHITECTURE §3.2, §12). Today
    the core is reachable only from a CLI (`demo_notion.py`). Three decisions were **asked, not
    guessed** — the maintainer chose (2026-09-17): (1) **the core exposes an HTTP service** (stdlib,
    no new dependency, bearer token) that n8n calls, not an n8n *Execute Command* running the CLI;
    (2) **n8n polls on a schedule** and asks the core to sweep Runs waiting for a human — a Google
    Doc has no "decided" event, and n8n then needs no Google credentials; (3) **the core writes the
    review Doc's link onto the Notion brief through the adapter**, next to the statuses it already
    writes (ADR-0041) — one writer, no n8n write access to Notion. Found while designing: n8n's
    Notion Trigger polls `last_edited_time`, so every core write to the page re-triggers it — a
    trigger for a brief with nothing to do must write nothing, or the loop never stops. Subtasks:
    1. ~~**Review link on the brief.**~~ — done (ADR-0047). Additive `BriefBoard.report_review_location(brief_ref, /, *,
       run_id, location)`; `NotionBriefBoard` writes a `url` property named by a new required
       `OMEMO_NOTION_REVIEW_LINK_PROPERTY`; `InMemoryBriefBoard` records it;
       `BriefStatusReporter.show_review_location(run, location)` shows it once per location (a
       refusal logged + kept, never fails the Run); `demo_notion.py` shows it after publishing. ADR.
    2. ~~**One brief invocation + the waiting-Run listing.**~~ — done (ADR-0048). Extract `demo_notion.py`'s whole
       per-brief flow (decision → produce → publish → link) into an application function returning
       an outcome instead of printing, so the CLI and the service run the same code; an additive
       `RunIndex` contract (Run ids by status) implemented by `SqliteRunStore`, and the sweep's
       "which briefs wait for a human". ADR.
    3. ~~**HTTP production service + n8n workflows.**~~ — done (ADR-0049). `infrastructure/` service: `POST /v1/briefs`
       (`{"brief_ref"}`) and `POST /v1/reviews/sweep` answer `202` and queue work for **one**
       background worker (a brief already queued is not queued twice), `GET /v1/health`; bearer
       token `OMEMO_SERVICE_TOKEN`, host/port from env; entrypoint `factory_service.py`. Exported
       n8n workflows in `n8n/` (Notion Trigger → HTTP Request; Schedule Trigger → HTTP Request) and
       a static test that they hold only trigger + HTTP nodes calling the service's routes with a
       credential, no inline token and no logic nodes. ADR.
    4. ~~**Stage 11 acceptance**~~ — done (ADR-0050). (`S11A`, `STAGE11_ACCEPTANCE.md`): the real service over HTTP on
       S10A's production path, driven by requests rendered from the committed n8n workflows — a
       ready brief is produced and its link shown; an unready one does nothing; repeated triggers
       cause no second model call and no extra board write; a desk decision is picked up by the
       sweep; a wrong token is refused; a failing job does not stop the worker. A live n8n round
       trip stays the operator's check. ADR.
16. **ROADMAP Stage 12 — first working MVP (Milestone M3, "Ключевая веха проекта").** **Closed as
    code by 16.1 + 16.2 (ADR-0051); 16.3 — the live pilot — is still open and is the maintainer's,
    not a session's.** The trigger mechanism 16.1 waited for is the HTTP
    `factory_service.py` + the `n8n/` workflows, and S11A already drives it with production assets.
    **Audit finding (checked against the actual test files, not assumed):** `tests/test_
    stage10_acceptance.py` already runs the **entire** chain in one process per invocation — Notion
    brief intake → Rin → Leo → real QA → Google Docs publish → decision → completion/rework,
    crash/outage-safe (`STAGE9_ACCEPTANCE.md` reused, `STAGE10_ACCEPTANCE.md`). Four of Stage 12's
    five DoD lines (one real brief reaches a human-approved artifact; every run is reproducible
    from stored state; nothing ships without Approve; every inter-agent message is schema-
    validated) are **already demonstrated at the CI/component level** by the S8A→S9A→S10A chain,
    not still to build. So this stage is much smaller than its own "Высокая/L" estimate suggests —
    don't reinvent what's already proven. Subtasks:
    1. ~~**Extend the acceptance chain with the trigger.**~~ — done (ADR-0051,
       `STAGE12_ACCEPTANCE.md`, `tests/test_stage12_acceptance.py` `S12A`). The audit held up: no
       component needed assembling, so `S12A` reuses S11A's path untouched and instead asserts each
       DoD line **as the DoD words it**, entered through the n8n trigger — which is what none of
       S8A–S11A did. Four gaps were real and are now covered: the candidate's `APPROVED` status
       through the trigger (S11A-04 only checked the Run), reproducibility stated as one property of
       the stored row (brief, both Outputs, Artifacts, Evaluation, Review, per-turn Analytics, the
       journal), schema validation of inter-agent messages on the full loop (M2A proves it below the
       board/desk/trigger), and the two crashes of 16.2. No production code changed.
    2. ~~**Re-check the fifth DoD line for real.**~~ — done in the same `S12A` (S12A-07/08).
       Checked, not assumed: behind the service a dying intake is a worker job raising, so the
       service must answer the next request while the stored Run stays a managed `QUEUED` with no
       Task and the next trigger produces it from the brief **on the board** (ADR-0042's resume
       rule); and a scheduled trigger the service cannot even dispatch (listing the waiting Runs
       fails) answers `503`, queues nothing, calls no model and leaves every stored Run untouched.
       S11A's `Factory` gained `dies_after_saves` / `index` keyword arguments to drive both.
    3. **The actual milestone action is a live pilot, not more code. — PARTLY DONE 2026-09-19 and
       2026-09-20, STILL OPEN.** The pilot ran (see "Current state" above and `n8n/README.md`): a
       real brief in real Notion, triggered by real n8n, produced by real Anthropic, through to
       `waiting_human` with statuses written back — for $0.010045. The maintainer then asked for a
       rework, which ran on `claude-sonnet-5` for $0.020702 and exercised **ADR-0032 and ADR-0052
       live for the first time**: only Leo re-executed, `artifact-2` `SUPERSEDED`, `artifact-3`
       `CANDIDATE` v2, `rework_count` 1/3. **Two things keep M3 open.** (1) Still no human-approved
       artifact — and the reason changed: QA flagged v2 as well, and **task 22 shows `passed` is
       structurally unreachable** while QA sees only the artifact's content but is asked about client
       rules and uniqueness. That is now the blocker, and it is a contract decision, not another
       rework. (2) No Google Docs leg: no service account, so the desk is absent and approval would
       go through the CLI — the maintainer chose the Notion desk of task 19 for this. Closing M3
       needs task 22 first, then task 19's wiring, then a decision on the candidate.
       Original brief: One real brief, real Notion, real Google Docs, real
       Anthropic, real n8n trigger, through to a human-approved artifact. This needs the
       maintainer's real accounts across four external services — not available in this
       environment, not something a session can do unattended. Flag it, don't fake it with a
       scripted "acceptance" test standing in for the milestone. **ADR-0051 §3 says the same in the
       record: Stage 12 is complete as code, Milestone M3 is not claimed until this runs.** Setup
       the operator needs is already written down: `n8n/README.md` (live Notion Trigger + the
       service's token/host), ADR-0043 + `.env.example` (the shared-drive folder shared with the
       Google service account), the seven `OMEMO_NOTION_*` variables, and `demo_factory.py` for a
       live provider call. `demo_notion.py` is the CLI for one brief; `factory_service.py` is the
       service n8n calls.

17. ~~**`max_tokens` is hardcoded at 2048 and cannot be configured.**~~ — done (ADR-0052,
    `PROVIDER_MODEL_SPEC.md` / `_ACCEPTANCE.md` 1.2). Both decisions the task asked for were **asked,
    not guessed** — the maintainer chose (2026-09-19): `OMEMO_MAX_TOKENS__<ROLE>` is **required** for
    an anthropic binding, exactly like the pricing values (so **no** default lives in code — the
    `_DEFAULT_MAX_TOKENS` constant is gone and `AnthropicLLMClient(max_tokens=…, thinking=…)` cannot
    be constructed without both), and thinking is **sent explicitly and per role** rather than
    inherited. **Corrected while implementing:** the maintainer's answer named `budget_tokens`, which
    the current models reject with a `400` (it lives on only for Haiku 4.5 and older) — so
    `OMEMO_THINKING__<ROLE>` has a closed grammar instead: `adaptive` | `disabled` | `budget:<N>` |
    `inherit`, the last being the one spelling that omits the field (a written-down decision, not a
    silent fallback). `budget:<N>` is checked against `N >= 1024` and `N < max_tokens` **at selection
    time**, so a pair the provider would `400` costs zero tokens. Tests: `tests/test_provider_model.py`
    (§4 variant + §7 A/C/D), `LTL-09` (§7 B: both values on every turn of the Tool loop). Suite: 1182
    passed. **Deliberately left out (ADR-0052 §4):** `output_config.effort`, `thinking.display`,
    streaming for large budgets, a per-model validity table in the factory (that would hardcode model
    knowledge — a wrong pair surfaces as the provider's `400`, i.e. a managed failed Task), and the
    finding that **Claude Fable 5.1 / Mythos 5.1 cannot back any role at all**: they reject forced
    `tool_choice`, which *is* ADR-0014's structured-output mechanism. Note for the next live pilot:
    an existing `.env` must add both variables per anthropic role or the factory fails closed at
    startup, and Opus 5 / Sonnet 5 are now usable (`adaptive` + a real budget).

18. **A refused or undelivered trigger is silently lost — found in the pilot (2026-09-19), needs a
    decision, probably an ADR.** n8n's Notion Trigger forwards a page **once**, when it changes. In
    the pilot the first call came back `401` (a credential typo) and the trigger never retried; the
    brief sat ready and unproduced until the page was edited again by hand. The same hole is open
    whenever `factory_service.py` is down, restarting, or refusing — every brief edited in that
    window is dropped, with nothing in the core to notice. `review-sweep` covers Runs already at
    `waiting_human`, and nothing covers briefs that never became Runs. Options to weigh, not to
    assume: a second scheduled route (`POST /v1/briefs/sweep`) that asks the board for ready briefs
    with no stored Run — symmetrical with the review sweep, one more `BriefBoard` query, no n8n
    logic; n8n's own retry/error-workflow settings — transport-level, keeps the core simpler, but
    puts behaviour in n8n, which ARCHITECTURE §3.2 wants kept to transport; or accepting it and
    documenting the manual re-touch. Note this is not the polling-loop question of ADR-0049 §3
    (that one settled — see the log), it is its mirror image.

19. **A `ReviewDesk` on Notion — chosen by the maintainer 2026-09-20; contract settled by
    ADR-0060, implementation open.** This closes M3's last structural gap without a Google service
    account **and** gives the clipping department its human gate (~390 reviews/month, ADR-0059).
    The port is unchanged and vendor-neutral; a second implementation is additive, like
    `NotionBriefBoard`. Decisions:
    - **Its own database**, not a page in the brief board — a review outlives the brief, belongs to
      a Run and an Artifact, and ~390 clip reviews a month would bury a board holding tens of rows.
      Per ADR-0055 §5 the prerequisite is **content access**: the `concept` integration must be
      granted that database explicitly, or every lookup is indistinguishable from "not decided yet".
    - **Typed properties, not ADR-0043's marker line — a deliberate departure from what this task
      originally suggested.** The `РЕШЕНИЕ:`/`ПРИЧИНА:` grammar exists because *a Google Doc has no
      properties*; it is a workaround for a constraint Notion does not have, and copying it would
      import its whole failure surface (prose parsing, damaged/missing/repeated marker, an
      unrecognised word that must be treated as silence). Instead: **`Решение`** is a `select` with
      exactly `Одобрено`/`Отклонено`/`Доработать` → `APPROVED`/`REJECTED`/`CHANGES_REQUESTED`,
      unset → `None` (the only ambiguity left), an option outside the three → `ReviewDeskError`
      (misconfigured database, not silence); **`Причина`** is `rich_text`, blank → `None`. A
      dropdown cannot be typo'd. The API **can** create `select` properties (not `status` — the
      operator log recorded that), so setup is scriptable. ADR-0043 is **not** superseded: its
      grammar stays right for a Doc.
    - **`review_id` is a queryable `rich_text` property**, found by a database query with a filter.
      No hashing: ADR-0043 hashed because a Drive lookup puts its query in the URL, while a Notion
      query is a `POST` body, so no id enters a path. Publishing the same `review_id` twice returns
      the same page (the port's idempotence; publish is also the retry, ADR-0044 §4).
    - **A clip is reviewed by its local file path** — `ArtifactView.content` is text and Notion
      cannot play a file on the maintainer's machine, so the page carries the plan + path and the
      reviewer opens it in a player before choosing. Honest about what v1 is; hosting clips for a
      remote reviewer is an upload adapter and its own decision.
    - **Ordering, corrected by ADR-0061:** ADR-0060 §5 said to extract the shared Notion plumbing
      *before* building this desk, counting it as the third consumer. That counted **decisions, not
      modules** — `src/` holds exactly one Notion module (`notion_brief_board.py`);
      `NotionEpisodeBoard` and `NotionReviewDesk` are decided and unwritten. Extracting from one
      implementation means inventing the seams two unwritten callers will need, which the
      Conventions forbid, and the refactor would have no proof: its argument is "behaviour did not
      change" and there is no second behaviour to hold it against. **So: desk (2nd, duplicates the
      plumbing deliberately) → `NotionEpisodeBoard` (3rd, task 21.6) → then the extraction**, alone,
      with its own ADR and the gate over three real consumers. ADR-0060 §5's *reason* stands and
      applies to that step: the refactor never lands inside the commit that revealed the need.

20. ~~**What comes after Stage 12 — decide before coding, don't drift into it.**~~ — done
    (ADR-0053, 2026-09-20). Asked, not guessed: the maintainer brought a **third** candidate — a
    clipping department — and chose it as the next work. Task 9 (Stage 13) and task 10 (the §16
    video slice) stay unauthorized; Milestone M3 stays open and is not blocked, because its two
    remaining items (a decision on the `flagged` candidate, and a review desk) wait on the
    maintainer, not on engineering time. The department itself is task 21. Original brief: Stage 12 is closed as
    code, so the two things that were waiting on it are now unblocked *as candidates*, not as a
    default: task 9 (ROADMAP Stage 13 — real media production) and task 10 (the `CONTENT_FACTORY_
    THOUGHTS.md` §16 question: keep Stage 13 as ROADMAP has it, or carve out an earlier narrow video
    slice). **Both still require the explicit ADR decision task 10 describes — closing Stage 12 did
    not authorize either one**, and the maintainer's own note (§19) has the opening question for
    whichever session picks it up. Note too that Milestone M3 (16.3) is still open: a green CI is
    not the pilot. A reasonable next session is the ADR for that ordering decision — asked, not
    guessed — while the live pilot waits on the maintainer's accounts.

21. **The clipping department (started 2026-09-20, authorized by ADR-0053).** A second department
    alongside the content factory: an existing TV series episode (rights cleared) → short vertical
    clips for TikTok / Reels / Shorts, through the same fail-closed QA + Human Review discipline
    (ADR-0018). Everything is **additive** — no core change, or it is a defect signal with its own
    ADR (ROADMAP Stage 13 DoD, `PROJECT.md` §4 п.11). The working note
    `CLIPPING_DEPARTMENT_THOUGHTS.md` is non-normative and authorizes nothing; ADR-0053 records what
    the maintainer settled on 2026-09-20: Vyra AI as the vendor (behind a role-named port, per
    ADR-0023), a **separate** board database, a local episode file in test mode, v1 ends at an
    approved clip file and publishes nothing, and nothing slow ever runs inside a reasoning step
    (ADR-0028 §3's budget is for fast calls; minutes-long work uses the ADR-0049 queue, and
    cutting/rendering is a deterministic `Workflow` step). Subtasks, each ADR-before-code:
    1. ~~**Side-effecting Tools — the boundary ADR.**~~ — done (ADR-0054). The shape was as
       ADR-0022 "Deferred" reserved it: a thin Tool in `tools/` over a port injected at
       construction, the real I/O in `infrastructure/`, and `_ALLOWED_PROJECT_IMPORTS` in
       `tests/test_tool_contract.py` widened **by exact module name**, one port per ADR — never the
       `adapters` package as a prefix, because the allowlist's whole value is that widening it is
       loud. **Found while writing it, and not in any document before:** ADR-0026 §4 makes an
       uncommitted executor call at-least-once across a crash, and Tool calls live inside that call
       with their payloads transient (ADR-0028) — so **every Tool call is at-least-once**, and a
       crash mid-step replays every Tool the model already called. Invisible while Tools were pure
       (`current_date` twice is `current_date` once); not invisible the moment one touches the
       world. Hence ADR-0054 §2: **a Tool observes the outside world and does not change it** — its
       call must be repeatable without a second irreversible consequence, and anything that creates,
       uploads, publishes, deletes or renders goes in a deterministic `Workflow` step, which is
       committed. A paid query stays legal (repeating spends again but corrupts nothing); ADR-0028
       §3's budget is now also the per-step spend bound, where a `REFUSED` call is free and a
       `FAILED` one already ran. This costs the clipping department nothing — ADR-0053 §4 had
       already put indexing, cutting and rendering in Workflow steps. `TOOL_SPEC.md` /
       `TOOL_ACCEPTANCE.md` TLB-01 are amended **with the first such Tool**, in the same change as
       the allowlist entry, so no document claims a boundary no test checks yet. Still deferred
       (ADR-0054): richer `ToolValue` kinds — it is `str | int | bool` and a Tool answers with a flat
       mapping, so a list of clip candidates belongs in the planner's Structured Output unless the
       kinds are widened by ADR; a per-role `max_tool_calls`; and tracing Tool calls.
    2. ~~**The clip QA criteria + the platform format contract.**~~ — done (ADR-0056); the Prompt
       and the role module land with 21.6. The two flagged questions were answered by the maintainer
       (2026-09-20): **accuracy to the source**, for scripted fiction, is *a clip must be
       self-contained and create no meaning the scene does not contain* — no reply cut mid-word, no
       splice that invents an exchange, no punchline without its setup, no spoiler; and **v1 does
       not reframe** — the screenshot was misread on first look, the black bars are TikTok's, added
       because a 16:9 clip was uploaded as-is, so the render keeps the source aspect and the
       platform letterboxes it. (That supersedes the second bullet of ADR-0053's "clip QA criteria"
       Deferred item; the ADR is Accepted and immutable, so the correction lives here.) ADR-0056
       then splits the gate in two:
       - **Format compliance is arithmetic and never goes to a model.** Duration, resolution and
         container are checked deterministically from the render step's own measurements against
         configured limits (pure, Skills-library shaped — `check_required_elements@v1` is the
         precedent). A clip out of spec is **not** a content risk for a human to read: it is the
         render producing something other than it was told, so it is a **`FAILED` Task with a stable
         reason** (ADR-0033's shape), not a `flagged` verdict. At ~450 clips/month this is what keeps
         the human queue to clips that actually need judgement.
       - **The model judges meaning, and reads the plan, not the pixels.** `ArtifactEvaluator.
         evaluate(content: str)` takes text, and nothing in v1 justifies changing a port the whole
         factory depends on. `clip_qa_agent@v1` on `qa_agent`'s template (ADR-0035) — new Agent, new
         Prompt `clip-qa-agent`, **no Skills, no Tools** — reusing **`qa-verdict@v1` and the
         ADR-0034 grammar unchanged**. It reads the clip Artifact's canonical JSON: episode ref,
         mode, boundaries, the transcript of exactly what the cut contains, the draft caption, the
         rendered file's location.
       **Found while writing it:** criterion 4 (no spoiler) **cannot be judged from the clip alone**
       — nothing in a two-minute excerpt says whether it gives away a later beat. So the QA input
       carries the **full episode transcript** too, or the criterion is theatre. Cost accepted with
       open eyes: ~15 clips per episode means that transcript re-sent fifteen times; caching the
       per-episode prefix is the obvious optimisation and is deferred to be measured, not assumed.
       **Chunk-mode clips still get the verdict in v1** — criterion 2 is unreachable there by
       construction, but a mechanical cut lands on an orphaned punchline or a spoiler as readily as
       a chosen one; narrowing it is an optimisation to make with numbers from the first episode.
       **Still open** (unchanged): the gate's granularity — ~450 clips/month means ~450 Approves
       (~15/day), and whether one Approve may cover a batch of chunk-mode clips from one episode is
       **not decided, the maintainer wants a test first**. Until then v1 keeps the strict reading:
       one Artifact, one verdict, one Approve per clip (fail closed, `PROJECT.md` §12 / ADR-0018).
       The first pilot should therefore be one episode, not thirty. Also deferred by ADR-0056:
       judging the rendered pixels (needs a multimodal port), platform caps as configuration, and
       **which evaluator the Director is given when a Run carries many clip Artifacts** — the same
       knot ADR-0055 §6 flagged, for 21.6 to untie.
    3. **The two cutting modes — simplified by the maintainer 2026-09-20 (ADR-0058, which
       supersedes ADR-0057 in full and further amends ADR-0053 §5 and ADR-0056 §2).** Cut **where
       one scene ends and the next begins**. `ClipMode` is `CHUNK` / `SCENE`:
       - **`CHUNK`** — consecutive pieces of a configured length, each boundary nudged to the
         nearest speech pause. Unchanged.
       - **`SCENE`** — boundaries are the vendor's detected scene boundaries; consecutive scenes are
         merged while under the configured maximum, and a scene longer than that is split at a
         speech pause rather than emitted out of spec.
       **Why this is the right simplification, not a retreat:** the complaint was never "give me one
       character's whole arc" — it was that a length-based cut lands mid-scene and mixes threads. A
       scene boundary never falls mid-scene and a scene belongs to one thread, so this solves it at
       the source. It is also cheap: scene detection is the vendor's advertised, mainstream
       capability, unlike the recurring-character re-identification storyline mode would have needed
       (ADR-0057 §4 found it claimed nowhere — that reading survives).
       **Three things were withdrawn before they were ever built:** the `segments` list (nothing in
       v1 splices, so a clip is one `start`/`end` — ADR-0022 §2's "no field without a reader");
       QA criterion 2 in both its forms (merging consecutive scenes joins what the episode itself
       shows consecutively, so it invents nothing); and "no reply cut mid-word" as a *criterion* —
       it is a property of how boundaries are chosen, checked deterministically, the same split
       ADR-0056 §1 made for duration. **v1's QA criteria are three:** self-contained, no orphaned
       punchline (a scene boundary is not a comic boundary), no spoiler (still needs the full
       episode transcript, ADR-0056 §3).
       **The real consequence: v1 has no planner agent.** Both modes are deterministic `Workflow`
       steps — ask the vendor for boundaries + transcript, compute intervals, render, caption. The
       clip-planner Agent the working note and ADR-0053 §5 assumed does not exist in v1. What
       remains is the part that needs judgement: `clip_qa_agent@v1`, the fail-closed gate and the
       human Approve — one model call per clip, no reasoning loop. **So ADR-0054 has no consumer in
       v1** (no agent queries the vendor mid-reasoning; a Workflow step reaches it through an
       ordinary Adapter). It is **not wrong and not superseded** — it is the standing answer for the
       first Tool that needs it, and its at-least-once finding stays true of every Tool call; 21.1
       is simply a decision with no code to write. The shape is legitimate: `TaskExecutor` is a
       Protocol and `SkillPreprocessingTaskExecutor` (ADR-0027) is already a non-LLM one.
       **Given up, deliberately (ADR-0058 §5):** no per-character arc in one clip — a character's
       scenes arrive as separate coherent clips in episode order. If the material needs arcs,
       storyline extraction returns as an additive mode from superseded ADR-0057's design.
       **Still open:** the configured chunk/maximum lengths; whether this vendor's scene boundaries
       are good on this material (the one vendor question now, answered by the first episode); and
       the gate's granularity — ~13 clips per episode, each with its own Approve until the
       maintainer's test says a batch may share one (`PROJECT.md` §12, ADR-0018). First pilot: one
       episode.
    4. ~~**The board Adapter — the contract ADR.**~~ — done (ADR-0055); the implementation is
       21.6. A second port `EpisodeBoard` beside `BriefBoard` (not an extension of it):
       `fetch_episode -> IncomingEpisode | None`, `report_status(episode_ref, *, run_id, status)`,
       its own `EpisodeBoardError`. `IncomingEpisode(episode_ref, source_ref, mode)` carries
       **editorial intent, not the video** — `source_ref` is an opaque handle the episode-source
       port resolves (ADR-0053 §7), so the board survives the move off a local file; `mode` is a
       closed `ClipMode` (`SEMANTIC`/`CHUNK`/`BOTH`) because which way an episode is cut is an
       editorial call, not an inference. Clip length/count/platform deliberately stay out —
       configuration, not a column every editor fills. `NotionEpisodeBoard` duplicates
       `NotionBriefBoard`'s HTTP plumbing **on purpose** (rule of three); **the foreseeable third is
       task 19's Notion `ReviewDesk`, and that is when to extract it — as its own behaviour-neutral
       refactor, never mixed into the feature commit.** Eight required `OMEMO_EPISODE_NOTION_*`
       variables, no default property names (ADR-0040). **The "read-only token" was a misreading,
       resolved from the operator log:** `Concept Notion (read)` names *n8n's* credential, not the
       integration's capability — the same token created four properties via the API (2026-09-18)
       and wrote `Run status` back in the live pilot (2026-09-19). **The real prerequisite is
       access:** Notion grants content access per database, so the `concept` integration must be
       granted access to the new episode database, or `fetch_episode` sees an empty board and says
       `None` silently. **This answers the identical doubt in task 19.** Flagged for 21.6, not
       decided here: `report_status` presumes **one Run per episode** (clips as its Artifacts),
       which sits awkwardly with ADR-0053 §5's per-clip gate given `ContentDirector` evaluates the
       final step's Artifact — if it becomes one Run per clip, the write-back needs revisiting.
       Also deferred: the `POST /v1/episodes` route + a second Notion Trigger (ADR-0049's two
       findings apply unchanged — a core write re-triggers the poll, and task 18's lost-trigger hole
       is open on this board too), clip locations reported back, and `InMemoryEpisodeBoard`.
    4a. **Run granularity — decided (ADR-0059): one Run per episode**, holding ~13 clip Artifacts.
       **`Run` needs no change, verified in `domain/run.py` rather than assumed:**
       `open_evaluation(artifact_id, …)` and `open_human_review(artifact_id, …)` are both
       per-Artifact, the latter carries **no "one pending review" rule**, and
       `transition_artifact`'s `CANDIDATE -> APPROVED` gate keys on *that* Artifact's approving
       review and *that* Artifact's latest `PASSED` verdict — so 13 clips are 13 independent
       fail-closed gates by construction. Run status covers the batch: `RUNNING` → `WAITING_QA`
       (every clip has a verdict) → `WAITING_HUMAN` (every clip decided) → `COMPLETED`; no new edge.
       **A clip is not the Run's fate** — a clip that fails QA or is rejected just does not ship;
       only a failure that stops production fails the Run. **The department does not use
       `ContentDirector`** and does not modify it: its contract is a *declared* Workflow with
       positional Task matching ending in one candidate, and a fan-out of unknown width is a
       different shape. It gets an application module beside it, like `BriefProduction` (ADR-0048);
       **one Task per clip render**, which is ADR-0032's existing "append a traced Task" mechanism,
       giving each clip its own Output, Artifact, trace and retry. **Cost named:** that service must
       keep ADR-0026's commit discipline itself and resume without duplicating a committed Task or
       verdict — the main risk in 21.6, so the acceptance must crash at every commit point the way
       `SWR` does. **Found while checking, would have failed silently:**
       `review_publication.pending_review(run)` returns `pending[-1]` — *the latest* pending review,
       and `pending_review_package` / `publish_pending_review` / `take_review_decision` all inherit
       it. With 13 pending reviews they would address one and **leave twelve unpublished with no
       error**. So the department gets **per-Artifact siblings**, and the existing functions are
       **not** changed — Stage 10–12's acceptance pins their current behaviour.
       `latest_qa(run, artifact_id)` is already per-Artifact and is reused as is. **Rework does not
       apply to a clip** (ADR-0059 §6): with no planner agent there is no producer whose reasoning
       could be re-run — asking for changes on a cut means wanting a *different* cut, i.e. a
       different clip. A rejected clip is simply not shipped; no `SUPERSEDED` chain, no rework loop.
       **Deferred:** the per-clip review desk (same blocker as task 19 / M3 — it will bite here too),
       and whether ~390 Tasks + ~390 Evaluations a month in one store stays comfortable.
    5. ~~**`CLIPPING_SPEC.md` / `CLIPPING_ACCEPTANCE.md`**~~ — done. Ten sections and seven
       criterion families (`EPB`, `STE`, `NEB`, `CLP`, `RND`, `CQA`, `CRN`). The acceptance opens
       with an honest implementation-state table, because two layers are blocked on things this
       environment does not have: **ffmpeg is not installed** (so `ClipRenderer` waits, though the
       pure format check does not) and **Vyra is not configured** (so `FootageIndex`'s real adapter
       waits, though the port and a stub do not). **Found while writing the spec:** with the planner
       agent gone (ADR-0058 §4) **nothing generates the draft caption** ADR-0056 §2 put in the clip
       payload — and v1 publishes nothing, so it has no reader either. It is dropped
       (ADR-0022 §2's rule); burnt-in subtitles come from the transcript deterministically and are
       not a caption. Ports named: `EpisodeBoard`, `EpisodeSource` (`locate -> LocatedEpisode`, a
       locally readable path; a remote implementation materialises a local copy so the render step
       never learns about the network), `FootageIndex`
       (`index -> IndexedFootage(duration_ms, scenes, speech)`), `ClipRenderer`
       (`render -> RenderedClip` carrying its **own measurements** — it made the file and knows
       them, which is what lets ADR-0056 §1's check be arithmetic).
    6. **Only then implement:** the episode-source port (local file first), the indexing/transcription
       step on the ADR-0049 queue, the clip-planner Agent (Prompt + Schema + Tool grants), the QA role,
       the deterministic render step, and one Artifact + one gate per clip candidate.
    7. **Automatic publishing** — wanted by the maintainer, deliberately out of scope for v1. Additive
       Adapters per platform when it is asked for.

22. **QA is asked questions its input cannot answer, so `passed` is unreachable — found in the live
    2026-09-20 rework, needs a decision, probably an ADR. This now blocks M3 (16.3), and it will
    bite the clipping department's own QA role (21.2/21.6) the same way.**
    `ArtifactEvaluator.evaluate(content: str)` (ADR-0018, realized by `LLMArtifactEvaluator`,
    ADR-0036) passes the QA model **only the candidate Artifact's own content** — not the brief, not
    a client profile, not the previous version, not the reviewer's instructions. But `qa-agent` v2
    (task 12.1, from `PROJECT.md` 1.3 §1) judges on criteria that need exactly that: criterion 1
    asks whether the material repeats the client's own or a competitor's output, criterion 4 asks
    for "редакционным правилам клиента, присланным в контексте". Neither is decidable from a
    15-second script in isolation, and the prompt ends with "Если сомневаешься — не ставь passed"
    (fail closed, `PROJECT.md` §1). So a **correct** QA agent flags the missing context on every
    iteration: v1 was flagged for it, v2 was flagged for it again in different words, and a third
    rework would buy a third wording. The gate is behaving as told; the port is what is short.
    Options to weigh, not to assume:
    - **Widen the port** so an evaluation receives its context (brief, client profile, human
      instructions, the superseded version) instead of a bare string. Fixes the cause, but changes
      an ADR-0018/0036 contract and every implementer — an ADR, and additive per PROJECT.md §4.11
      (a second method, not a changed signature).
    - **`qa-agent` v3** restricted to what one artifact's text can support (factual correctness,
      unsubstantiated claims, internal consistency), with client rules and uniqueness dropped until
      something can supply them. Cheapest and has a precedent (task 12 was prompt content, reviewed
      like code, no ADR) — but it narrows the charter's value #1, so say so out loud.
    - **Supply a client profile** as part of the brief. Does not help on its own: whatever the brief
      carries, `evaluate(content)` still cannot show it to QA. Only useful together with option 1.
    Do not "fix" this by rewording the script again, and do not approve around it — the fail-closed
    gate is correct, and ADR-0018 exists precisely so that a flagged candidate cannot be waved
    through.

23. **The generation department — ordering decided (ADR-0065, 2026-09-21), broken into subtasks
    here so a session can pick one up without re-deriving the plan.** A third additive department,
    the same shape clipping (task 21) proved out: photo + a human-written prompt in, image + video
    generation via role-named ports (Google Gemini/"Nano Banana" for images, Higgsfield/Kling for
    video), QA, Human Approval, one approved video file out — no auto-publish, no prompt-crafting
    agent in v1 (both explicitly deferred, ADR-0065 §5/Deferred). Subtasks, roughly the order
    clipping's own history took:
    1. **Ports ADR — check real vendor capability first, do not guess.** Design the two role-named
       ports (`adapters/`, working names `ImageGenerator`/`VideoGenerator`) and resolve ADR-0065's
       open question against the actual APIs (`docs.higgsfield.ai`, Gemini's image-generation
       docs): does Higgsfield/Kling need a Nano Banana-produced image as input, or can it generate
       video straight from a photo + text prompt? This decides whether the pipeline has one vendor
       call or two. Async Higgsfield job handling mirrors `ProductionService`'s queued shape
       (ADR-0049); check whether Gemini's call is fast enough to be synchronous instead of assumed.
    2. **Vendor implementations in `infrastructure/`** — `infrastructure/gemini_image_generator.py`
       / `infrastructure/higgsfield_video_generator.py` (working names), auth via
       `OMEMO_GEMINI_*` / `OMEMO_HIGGSFIELD_*` env vars, fail-closed with no defaults (ADR-0040's
       rule), no SDK/vendor shape leaking past `infrastructure/` (`tests/test_adapter_contract.py`
       boundary). One new third-party dependency each, most likely — decide the library
       (official Google/Higgsfield SDKs vs. raw REST over `urllib`, the choice already made twice
       for Notion and ffmpeg/whisper.cpp) inside this subtask, not before.
    3. **QA criteria for generated video — ask the maintainer, don't invent.** A different
       judgement than clip QA (ADR-0056): coherence with the prompt, visual/temporal artifacts,
       platform-safety, nothing scripted-fiction-specific applies. Its own small ADR, reusing
       `qa-verdict@v1`'s Schema/decoder the way `clip_qa_agent@v1` did (one verdict contract, not a
       second one) unless the maintainer's criteria genuinely need a different shape.
    4. **The board — a Notion database** (mirroring `EPISODE_BOARD`, ADR-0055): title, readiness
       property, reference-photo location, the manual generation prompt field, `Run status` /
       `Run id`. Create it through the API the way the episode database was (so property *types*
       are right, not just labels) and confirm the integration has access — task 21.4 already found
       this "read-only token" worry was a misreading once checked, so check here rather than
       assume the same blocker exists.
    5. **Run granularity — confirm, don't assume "one Run per episode" applies here.** One
       generation request probably does not fan out into many artifacts the way one episode fans
       out into ~13 clips (ADR-0059), so this is likely one Run per request — state it in its own
       small ADR or fold into the ports ADR if it turns out trivial.
    6. **`GENERATION_SPEC.md` / `GENERATION_ACCEPTANCE.md`**, in the shape of `CLIPPING_SPEC.md`,
       once the ADRs above land — open with an honest implementation-state table the way
       `CLIPPING_ACCEPTANCE.md` did (real credentials/quota are operator setup, not something a
       session has).
    7. **Assemble the department and give it an entrypoint** — `composition.build_generation_...`
       (mirroring `build_clip_production`) + `demo_generation.py` (mirroring `demo_clips.py`), one
       application module beside `ContentDirector` (mirroring `ClipProduction`, ADR-0059 §1's
       reasoning: `ContentDirector`'s declared-Workflow contract doesn't fit a two-vendor pipeline
       with its own gate any better than clipping's fan-out did).
    8. **Real environment + a first real generation — the maintainer's action, not a session's.**
       Gemini API key, Higgsfield API key pair (`HF_API_KEY_ID`/`HF_API_KEY_SECRET`), the Notion
       database shared with the integration, a reference photo and a hand-written prompt (from the
       GPT Store tool or otherwise). Mirrors `CLIPPING_RUNBOOK.md` — write the equivalent runbook
       in the same subtask once there is something to run.
    9. **(Later, not v1 — ADR-0065 §5.)** The prompt-crafting Agent: photo (+ maybe a short brief)
       in, a generation prompt out, built on this repo's own Claude integration
       (`client_for_role`, the Prompt store) — never an OpenAI integration, since the GPT Store
       tool that inspired it has no callable API (ADR-0065 Context). Do not start before the
       vendor-calling skeleton (subtasks 1–8) is proven on a real generation.

See `DOMAIN_MODEL.md` (entities) and §9 (aggregate roots) for the domain shape of tasks 3–5.

## Conventions
- No code before its spec/acceptance/ADR exist. Significant decisions → an ADR.
- **Do NOT change Run's existing behaviour or signatures** (reference impl); extend it only
  *additively* and via an ADR, as ADR-0004…0007 did for its children. **Do NOT extract shared
  base classes prematurely** (rule of three). Extend by *adding* modules, not by modifying the
  core (PROJECT.md §4.11).
- Provider-agnostic: never hardcode a model; selection lives in config.
- An aggregate's public API is small: factory `create`, read-only properties, one guarded
  mutation method, domain events, domain errors (rooted at `DomainError`). Immutable input via
  `__slots__` + guarded `__setattr__`. Single guarded `transition` + declarative
  allowed-transitions table.

## Quality gate (all green before commit; a working `.venv` with Python exists)
Windows: `.venv/Scripts/python.exe`; macOS/Linux: `.venv/bin/python`.
```
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe -m pytest -q
```
