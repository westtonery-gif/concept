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

`CONTENT_FACTORY_THOUGHTS.md` sits **outside** this hierarchy — explicitly non-normative
(says so in its own header), a product/architecture exploration of the full video-factory vision
reconciled against the repo as of commit `063cfde`. Read it for context and the proposed sequence
(§16), but it does not override anything above 6 and it does not itself authorize starting
anything ahead of the queue below — see task 10.

## Current state (2026-09-17)
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
- **All 46 ADRs (0001–0046) are Accepted.** Run/Task/Output/Artifact/Human Review (0003–0007),
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
  `LLMArtifactEvaluator` (0036), the domain pivot (0037), the QA evaluator wiring (0038), the Stage 8 acceptance (0039), the Notion `BriefBoard` (0040), the status write-back (0041) and the Stage 9 acceptance + brief intake (0042) are all
  implemented and tested. All gates green: ruff, ruff format, mypy --strict, pytest (958 passed,
  0 skipped).
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
9. **(Not yet — Stage 13, after the Stage 12 MVP.)** Real media production: AI image/video
   generation and TTS voiceover via paid provider subscriptions, plus automated video/photo
   editing (splicing, audio overlay) via editor APIs. Decision made 2026-09-15: deliberately
   deferred to Stage 13, not pulled forward — see ROADMAP.md Stage 13's new paragraph for the
   architectural shape (new Adapters for image-gen/video-gen/TTS/video-editing, Tools where an
   agent needs to invoke one mid-reasoning, editing/overlay likely a deterministic `Workflow`
   step rather than an agent decision). Don't start this before Stage 12 without asking first —
   it was an explicit, deliberate call, not an oversight.
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

15. **ROADMAP Stage 11 — n8n integration.** Next (ROADMAP order). Dependencies: Stages 9, 10 —
    both done. **Different shape from tasks 13/14**: n8n is an external tool with its own UI/JSON
    workflow config, entirely outside this repo — there is no "adapter contract" to implement for
    it, and per `ARCHITECTURE.md` §12 the interaction is deliberately described **without an API
    spec**. What the repo *does* owe (§3.2/§3.3): a real, externally-invokable entry point n8n can
    call — today there is none; `demo_notion.py` is a script a human runs by hand, not something
    an automation trigger reaches. Subtasks:
    1. **Trigger-mechanism decision — needs an ADR, and probably the maintainer's input, not a
       unilateral pick.** n8n can reach the core two ways: an **Execute Command** node shelling
       out to a CLI (reuses `produce_brief`'s existing entrypoint shape, e.g. `demo_notion.py
       <brief_ref>` on a host n8n can reach — no new dependency), or an **HTTP Request** node
       hitting a webhook (needs a new web-framework dependency — `PROJECT.md` §5 explicitly gates
       introducing a framework on "proven necessity", not by default). Recommendation to weigh,
       not a decision made here: CLI is the smaller step and both `produce_brief` (idempotent via
       `run_id`) and the QA/review resume paths already tolerate being invoked repeatedly/on a
       schedule — but this is also a deployment question (where does n8n run relative to this
       code?) the maintainer may have an opinion on.
    2. **Status **and link** propagation — check for a real gap before building anything.**
       ROADMAP's DoD says both statuses *and links* move automatically. `BriefStatusReporter`
       (ADR-0041) already writes `RunStatus` back to Notion, but check whether the Google Docs
       review location `ReviewDesk.publish` returns ever reaches Notion anywhere — if not (likely:
       `report_status`'s signature is status-only, no location field), that's a real hole to close
       (extend the reporting path, not necessarily the `BriefBoard` contract itself) before this
       stage's DoD can be honestly called met.
    3. **Document the n8n side (not pytest).** The actual "Notion event → n8n → trigger" wiring is
       n8n's own workflow JSON, configured in its UI — not Python, not this repo's test suite.
       What this subtask owes is documentation (a short ops doc or a README section) of exactly
       what an n8n workflow needs to call, given subtask 1's chosen mechanism, plus confirmation
       that the trigger entrypoint is safe to invoke unattended/on a schedule (idempotent, no
       duplicate side effects — should already hold, verify rather than assume).
    4. **Stage 11 "acceptance"** is therefore lighter than S9A/S10A's shape: a test proving the
       chosen trigger entrypoint does the right thing when invoked repeatedly/concurrently-ish
       against the same brief (CI-testable, no real n8n needed), not an end-to-end n8n round-trip.

16. **ROADMAP Stage 12 — first working MVP (Milestone M3, "Ключевая веха проекта").** **Blocked on
    task 15** — ROADMAP's own dependency list is Stages 1-11, and Stage 11 isn't done. Prepared
    ahead of time so there's no organizing pause once 15 lands, not to be started before it.
    **Audit finding (checked against the actual test files, not assumed):** `tests/test_
    stage10_acceptance.py` already runs the **entire** chain in one process per invocation — Notion
    brief intake → Rin → Leo → real QA → Google Docs publish → decision → completion/rework,
    crash/outage-safe (`STAGE9_ACCEPTANCE.md` reused, `STAGE10_ACCEPTANCE.md`). Four of Stage 12's
    five DoD lines (one real brief reaches a human-approved artifact; every run is reproducible
    from stored state; nothing ships without Approve; every inter-agent message is schema-
    validated) are **already demonstrated at the CI/component level** by the S8A→S9A→S10A chain,
    not still to build. So this stage is much smaller than its own "Высокая/L" estimate suggests —
    don't reinvent what's already proven. Subtasks:
    1. **Extend the acceptance chain with the trigger.** Once task 15 picks a mechanism, add it to
       an `S12A`-shaped test so the *whole* loop — including the entry point Notion's "brief ready"
       event would actually reach — is proven together at least once, not just its Notion→...→
       Google Docs tail.
    2. **Re-check the fifth DoD line for real**, don't assume it from the others: "падение одного
       шага не разрушает систему" across the *combined* chain specifically (S10A's crash tests
       cover the review/QA portion; confirm a crash during brief intake or trigger handling is
       equally clean) — a targeted addition, not a rewrite.
    3. **The actual milestone action is a live pilot, not more code.** One real brief, real Notion,
       real Google Docs, real Anthropic, real n8n trigger, through to a human-approved artifact.
       This needs the maintainer's real accounts across four external services — not available in
       this environment, not something a session can do unattended. Flag it, don't fake it with a
       scripted "acceptance" test standing in for the milestone.

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
