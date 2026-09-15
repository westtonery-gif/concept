# CLAUDE.md — concept Content Factory

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

## Current state (2026-09-16)
- **All 36 ADRs (0001–0036) are Accepted.** Run/Task/Output/Artifact/Human Review (0003–0007),
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
  `LLMArtifactEvaluator` (0036) are all implemented and tested. All gates green: ruff, ruff format,
  mypy --strict, pytest (875 passed, 0 skipped).
- **A real model can answer the QA gate, and every QA call is recorded (ROADMAP Stage 8,
  ADR-0036) — but no entrypoint wires it yet (11.4).** `infrastructure/llm.py`
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
11. **ROADMAP Stage 8 — QA Agent.** Next (ROADMAP order; also `CONTENT_FACTORY_THOUGHTS.md` §16's
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
       is domain content too — revisit with 11.3.
    3. ~~**`LLMArtifactEvaluator`**~~ — done (ADR-0036, `infrastructure/llm.py`,
       `EVALUATION_SPEC.md` §8.3, `EVALUATION_ACCEPTANCE.md` §4.3 `LAE`,
       `ANALYTICS_RECORD_SPEC.md` / `ANALYTICS_RECORD_ACCEPTANCE.md` 1.2 `AEV`,
       `RUN_RESTORE_SPEC.md` 1.2, `tests/test_qa_call_metrics.py`). The metrics question was put to
       the maintainer, who chose **attribution to the Evaluation** over a QA Task (would have
       broken the Director's positional Task matching and ADR-0035 §4) and over "return but don't
       record" (knowingly breaks PROJECT.md §16). An `LLMError` is not swallowed: it is re-raised
       as `QaCallError` chained `from` it, because the application layer may not import
       infrastructure and still has to receive the failed call's measurements.
    4. **Wire it into a real entrypoint** — extend Composition Root helpers (or add a small
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
    5. **Stage 8 acceptance** — a test in the shape of `test_m2_acceptance.py` proving both DoD
       lines end to end: a `PASSED` verdict lets a run complete, a risk verdict fail-closes to
       `WAITING_HUMAN` with escalation and, on `CHANGES_REQUESTED`, drives a real rework loop
       (ADR-0032) — covered with a realistic evaluator, not necessarily a live API call.

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
