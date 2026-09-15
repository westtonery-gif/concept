# CLAUDE.md — OMEMO Content Factory

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

## Current state (2026-09-15)
- **All 30 ADRs (0001–0030) are Accepted.** Run/Task/Output/Artifact/Human Review (0003–0007),
  Schema + Output validation (0008), Workflow (0009), Agent boundary + Prompt binding
  (0010/0011), Composition Root (0012), execution topology (0013), structured output (0014),
  Run restoration (0015), provider/model selection ownership (0016), shared `DomainError` base
  (0017), Evaluation/QA + fail-closed gate (0018), Artifact versioning (0019), Analytics Record
  (0020), Skills library (0021), Tool Layer (0022), Adapter Layer contracts (0023), Storage
  Adapter (0024), in-memory adapter stubs (0025), storage wiring (0026), the first Skill consumer
  (0027), the bounded LLM Tool-use loop (0028), per-call metrics capture + explicit pricing
  (0029), and the external versioned Prompt store (0030) are all implemented and tested. All gates
  green: ruff, ruff format, mypy --strict, pytest (785 passed, 0 skipped).
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
  waiting for a human or terminal is left alone. `execute` still requires a `CREATED` Run. To commit
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
  Orchestrating rework (ContentDirector routing a risk verdict into a re-run) is still open —
  ADR-0019 "Deferred", ROADMAP Stage 7/8.
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
8. **ROADMAP Stage 7 — first real Agent through the full orchestrator (Milestone M2).** Next.
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
   6. **Two correctness bugs found in code review** (`CONTENT_FACTORY_THOUGHTS.md` §15.4,
      confirmed against current code 2026-09-15) — fix before building more on top:
      - `ContentDirector._run_steps` has no fail-fast: after a Task fails, the loop still runs
        every later step (with whatever `task_input` it started with, since `chained_input`
        never advances past a failure). Harmless for cheap text calls; will trigger pointless
        paid generations once a step is expensive (media). Decide and implement the policy —
        stop the remaining steps, or an explicit configurable skip — as its own small change.
      - `finish_task` → `validate_and_record_output` persists `schema_ref=result.schema_ref`
        (whatever the executor self-reports) without checking it against the `schema` object
        actually used for `schema.validate(...)`. An Output can end up validated by one Schema
        but tagged as conforming to a different one. Add the cross-check (raise or normalize to
        the schema actually used) plus a regression test.
   7. **QA rework routing** — route a QA risk verdict / Human `CHANGES_REQUESTED` into an actual
      re-run that calls `Run.create_artifact_version` for the new version (ADR-0019 "Deferred").
   8. **Bring it together (Milestone M2 acceptance)** — one real run through `ContentDirector`
      exercising Storage + a Skill + a Tool + captured metrics + an externally-stored prompt in
      one pass; this is what actually closes Stage 7, not any subtask alone.
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
