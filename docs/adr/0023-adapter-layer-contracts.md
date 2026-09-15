# ADR-0023: Adapter Layer contracts — the LLM Adapter recognised, four new ports

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 6 builds the Adapter Layer, which isolates the core from outside providers. It has
five adapters: **LLM, Notion, Google Docs, Storage, Analytics**. Its DoD asks for:
- the contracts of all five adapters defined, and no direct calls from the core to external APIs;
- an LLM Adapter that swaps the model or provider through configuration, without touching agents
  or the orchestrator;
- a Storage Adapter that really persists `Run`, with the contracts covered by tests against fakes.

The session queue splits the Stage into 6a (this ADR: contracts only), 6b (the Storage Adapter)
and 6c (Notion / Google Docs / Analytics stubs). Full Notion and Google Docs integration is
Stages 9–10 ("здесь — контракт и базовая реализация/заглушка").

The docs fix the constraints:
- access to the LLM, storage and external services goes only through abstractions/adapters, and
  switching a provider must not touch pipeline logic (`PROJECT.md` §4 п.7, §5). Dependencies are
  injected, never read from global state (§6). Secrets come only from the environment or a secret
  store (§5);
- an adapter implements an **internal contract of the system on top of someone else's API**, and
  any change to an external API touches only its own adapter (`ARCHITECTURE.md` §9). Adapters are
  the only point of contact with the outside world, and dependencies point inward (§15);
- Notion, Google Docs and the database are not domain entities. The domain owns the Content Brief,
  the Human Review and the Run, not the tools where they live (`DOMAIN_MODEL.md` §8);
- the Content Director is the only owner of Run transitions (`ADR-0013`, `ARCHITECTURE.md` §10), and
  nothing is published without a human Approve and a passed QA verdict (`ADR-0007`, `ADR-0018`);
- `ADR-0015` admits a restored Run, and `RUN_RESTORE_SPEC.md` §6 already names the store at its
  boundary: `RunStore.save(run)` / `RunStore.load(run_id)`.

The LLM Adapter already exists, although nothing calls it that. It is the `LLMClient` port
(`ADR-0014`), the default `AnthropicLLMClient`, the keyless `FakeLLMClient` and per-role selection
`client_for_role` (`ADR-0016`). The other four exist only in the docs.

## Decision

### 1. The LLM Adapter is done and is recognised as such
The existing code satisfies the Stage 6 LLM Adapter DoD. Nothing about it changes.

| Stage 6 asks for | Where it already is |
|---|---|
| a single internal access point to models | `LLMClient.complete(system, user, fields)` (`ADR-0014`), `infrastructure/llm.py` |
| the default provider behind it, swappable | `AnthropicLLMClient`. SDK errors are wrapped as `LLMError`, and the provider mechanism (forced tool use) stays hidden |
| role → model binding held outside the code | `client_for_role(agent_ref, environ)` with `OMEMO_PROVIDER__<ROLE>` / `OMEMO_MODEL__<ROLE>`. A missing binding fails closed (`ADR-0016`) |
| tests without the live provider | `FakeLLMClient` (provider `fake`) |

The port stays in `infrastructure/llm.py`. Its only consumer is `LLMTaskExecutor`, which lives in
infrastructure too, and the application sees the `TaskExecutor` port, not `LLMClient`. Moving it
would be churn with no new reader. Its announced extensions keep their own ADRs: the tool-use loop
(Stage 7, `ADR-0022`) and usage reporting (Stage 14, `ADR-0020`).

### 2. Where the four new contracts live: the `adapters` package
- **`omemo_content_factory/adapters/`** holds contracts only. Each module is one adapter: a
  `typing.Protocol`, the values it exchanges, and its technical error. It imports only a pure
  stdlib allowlist and `domain.*`: no application, infrastructure, Composition Root, agents,
  Skills, Tools, SDK or I/O.
- **Implementations live in `infrastructure/`**, next to `llm.py`. That is where vendor SDKs and
  credentials are allowed (Stages 6b/6c and later).
- **Consumers** (the application, entrypoints and, later, Tools on top of adapters) import
  `adapters`, never `infrastructure`. Only the Composition Root chooses and injects an
  implementation (`ADR-0012`).

| Layer | May import an adapter contract | May import an implementation |
|---|---|---|
| domain | no (it depends on nothing) | no |
| adapters (contracts) | its own package | no |
| application, agents, Skills, Tools | yes | no |
| infrastructure | yes (it implements them) | yes |
| Composition Root | yes | yes (it picks one) |

The contracts sit below the application on purpose. Tools on top of an adapter (`ADR-0022`
"Deferred") must reach the contract, and the Tool layer may not import the application.

### 3. Contracts are named by role, not by vendor
| Adapter (`ARCHITECTURE.md` §9) | Contract | Module | Behind it (MVP) |
|---|---|---|---|
| LLM Adapter | `LLMClient` | `infrastructure/llm.py` | Anthropic |
| Storage Adapter | `RunStore` | `adapters/run_store.py` | a simple store (`PROJECT.md` §5), Stage 6b |
| Notion Adapter | `BriefBoard` | `adapters/brief_board.py` | Notion |
| Google Docs Adapter | `ReviewDesk` | `adapters/review_desk.py` | Google Docs |
| Analytics Adapter | `AnalyticsSink` | `adapters/analytics_sink.py` | an analytics store |

A contract never names or shapes a vendor. There is no "page id" or "document id", only opaque
`str` references. Replacing Notion with another board, or Google Docs with another editor, is a new
implementation of the same Protocol (`PROJECT.md` §4 п.11).

### 4. Rules shared by all four contracts
1. **A structural `Protocol`, no base class.** A test double or an implementation needs no
   inheritance, and mypy checks conformance.
2. **Domain types and opaque references only.** Inputs and outputs are domain values
   (`Run`, `RunStatus`, `ArtifactView`, `ReviewStatus`, `AnalyticsRecord`), small frozen values
   defined by the contract, or `str`.
3. **Adapters never change a Run.** They return data. The core applies it through the Run root
   under the unchanged transition contract (`ADR-0013`). The only adapter that receives a `Run` is
   `RunStore`, and it only persists and restores it.
4. **One technical error per contract.** An external failure (network, API, disk) is raised as the
   contract's own `<Contract>Error(Exception)`, like `LLMError`, and deliberately not a
   `DomainError`. A domain error raised by the core while the adapter works (for example, a Run that
   refuses stored truth) passes through unmasked.
5. **Writes are idempotent.** The core may repeat a write after a failure or a restart, so repeating
   it must not duplicate its effect (`ADR-0015` I3).
6. **No secrets cross a contract.** Credentials are read by the implementation, in infrastructure,
   from the environment or a secret store (`PROJECT.md` §5, §6). Nothing that crosses a contract is a
   secret.
7. **Calls are synchronous**, like `LLMClient`. Async and batching are future extensions.

### 5. `RunStore` — the Storage Adapter
- `save(run, /) -> None` stores the Run's **whole** current truth: state, children, analytics
  records and the event journal. It is atomic and replaces what was stored under the same `run_id`.
  Saving an unchanged Run again changes nothing, and a failed save leaves the previous truth intact
  (`RunStoreError`).
- `load(run_id, /) -> Run | None` returns the same Run (`ADR-0015` I7), or `None` if nothing is
  stored under that id. If the Run refuses the stored truth (`RunRestorationError`), that domain
  error propagates. It is never turned into `None` or a `RunStoreError`.
- The contract speaks `Run`, not a snapshot, as `RUN_RESTORE_SPEC.md` §6 fixes it. How the truth is
  encoded (snapshot, format, transactions) is the implementation's business, so a caller cannot get
  a snapshot wrong.
- **One store per aggregate.** Artifacts, reviews, evaluations, analytics records and the trace are
  Run children (`DOMAIN_MODEL.md` §9.1), so they are persisted with their Run, not by separate
  stores. The definitions (Workflow, Schema, Agent, Prompt) are not stored here: they are
  dependencies, not the Run's truth (`ADR-0015` §2).
- **Prerequisite for 6b.** `Run.restore` / `RunSnapshot` (`RUN_RESTORE_SPEC.md`) are not implemented
  yet. The spec's snapshot composition (§3) predates `ADR-0018`/`ADR-0020`, so it lacks the
  evaluations, the analytics records and their id counters. 6b amends the spec first and then
  implements restoration, before the store.

### 6. `BriefBoard` — the Notion Adapter
- `fetch_brief(brief_ref, /) -> IncomingBrief | None` returns the brief if it is ready for
  production. It returns `None` both for an unknown brief and for one not yet ready. The core then
  starts no Run (fail closed).
- `IncomingBrief(brief_ref, body)` holds two non-blank strings. `brief_ref` becomes the Run's
  `content_brief_ref`, and `body` is what the Content Director hands to the first step
  (`execute_workflow(..., brief=...)`). It is deliberately **not** the Content Brief entity, which
  is not implemented yet (`ARCHITECTURE_FREEZE.md` §3). The entity and its metadata (topic, content
  type, workflow choice) are Stage 9, with their own ADR.
- `report_status(brief_ref, /, *, run_id, status: RunStatus) -> None` shows the Run's status on the
  brief. The board is a display (`ARCHITECTURE.md` §12). Reporting the same status twice is
  harmless, and a failed report never changes the Run.

### 7. `ReviewDesk` — the Google Docs Adapter
- `publish(package, /) -> str` puts the candidate in front of the reviewer and returns a non-blank
  location where it can be found. Publishing the same `review_id` again returns the same place
  instead of a second copy.
- `ReviewPackage(run_id, review_id, candidate: ArtifactView, brief, qa_flags=())` holds the
  candidate plus the context of the decision (`ARCHITECTURE.md` §13). The candidate must belong to
  the same Run and be `CANDIDATE`. `qa_flags` is a tuple of non-blank strings. An ill-formed package
  is refused at construction (`ValueError`), so it never reaches a reviewer.
- `fetch_decision(review_id, /) -> ReviewDecision | None` returns `None` while the review is
  pending. A `review_id` that was never published raises `ReviewDeskError`.
- `ReviewDecision(decision: ReviewStatus, reason=None)` holds only a terminal outcome
  (`APPROVED` / `REJECTED` / `CHANGES_REQUESTED`) and a reason that is `None` or non-blank. It maps
  one-to-one onto the existing `Run.submit_review(review_id, decision, by=HUMAN_REVIEWER, reason=…)`.
- **The desk carries the decision; it never makes it.** The core applies it through the Run as the
  Human Reviewer, and approval still needs a `PASSED` latest Evaluation (`ADR-0018`). A desk cannot
  publish anything outward: publishing an Artifact is a Run transition, not an adapter call.

### 8. `AnalyticsSink` — the Analytics Adapter
- `export(records: Sequence[AnalyticsRecord], /) -> None` delivers copies of a Run's records to the
  analytics store (`ARCHITECTURE.md` §14).
- The Run owns the authoritative records (`ADR-0020`), and `RunStore` persists them. The sink is a
  **downstream copy**: delivering the same `record_id` again does not count it twice, and a failed
  export (`AnalyticsSinkError`) is retried later, never blocking or changing the Run.

### 9. What does not change
Nothing in `src` changes. That covers `Run` and its children, `ContentDirector`, `execute_task`, the
Composition Root, the LLM port and its implementations, Skills and Tools. The change adds one new
package, `adapters`, and tests. No adapter is implemented or wired yet.

### 10. Stage 6 DoD after this ADR
| DoD item | Status |
|---|---|
| Contracts of the five adapters defined | done: `LLMClient` + the four Protocols above |
| The core calls no external API directly | enforced by test: outside `infrastructure/`, no module imports a third-party package or a network/storage stdlib module, and only the Composition Root imports `infrastructure` |
| LLM swapped through configuration | done (`ADR-0016`) |
| Storage really persists `Run`; contracts tested against fakes | 6b (Storage), 6c (stubs for the other three) |

## Deferred
- **Implementations.** `RunStore` together with `Run.restore` / `RunSnapshot`, including the
  snapshot amendment in §5 (6b). Fake/stub `BriefBoard`, `ReviewDesk` and `AnalyticsSink` (6c).
  Real Notion and Google Docs integration (Stages 9–10), real analytics export (Stages 13–14).
- **Wiring.** When the Content Director saves a Run (after each step, `ARCHITECTURE.md` §10), when
  it publishes to the desk and polls for a decision, and which entrypoint turns a brief into a Run.
  n8n triggers are Stage 11.
- **The reviewer's identity.** `Run.submit_review` records the actor role (`human_reviewer`), not
  the person. Carrying an opaque reviewer id through `ReviewDecision` into the Run needs an additive
  Run change and a PII rule (`ARCHITECTURE.md` §10). Deferred to Stage 10. Until then the field
  would have no reader.
- **Reading the reviewer's edits** back from the desk, beyond the reason/instructions (Stage 10).
- **Listing or querying stored Runs** (for example, "all unfinished Runs" after a restart) and
  concurrent continuation, which `ADR-0015` excluded.
- **A shared `AdapterError` base.** No consumer handles the four errors uniformly yet (rule of
  three). `LLMError` stays as it is.
- **Async calls, batching and retry/backoff policy.**

## Consequences

### Positive
- The Stage 6 contract DoD holds. The boundary "the core calls no external API directly" is now a
  test, not a convention.
- Stages 6b/6c, 9, 10 and 14 each implement a fixed Protocol instead of designing an interface
  under deadline. Swapping a vendor is one new infrastructure module.
- Human Approval and the QA gate survive the external review site by construction: the desk
  returns a value, and the Run still decides.

### Negative / Trade-offs
- Three contracts (`BriefBoard`, `ReviewDesk`, `AnalyticsSink`) are designed before any
  implementation. Stages 9–10 may extend them additively, by ADR.
- `IncomingBrief` is a thin stand-in for the Content Brief entity. When the entity arrives, the
  board's return type will change, by ADR.
- The LLM port lives in `infrastructure/`, the four new ports in `adapters/`. The split has a
  reason (§1), but a reader has to learn it.
- The idempotency rules (§4 п.5, §6, §7, §8) are contract text. They can be checked only against
  an implementation, from 6b/6c on.

## Alternatives considered
- **Contracts in `application/ports`.** Rejected: Tools on top of adapters could not import them,
  since the Tool layer may not depend on the application (`ADR-0022`). The architecture also draws
  adapters as their own layer, below agents/Skills/Tools (`ARCHITECTURE.md` §15).
- **Contracts in `infrastructure/`, next to the implementations (like `LLMClient`).** Rejected: the
  application would have to import infrastructure to reach them, against the inward dependency
  rule.
- **Vendor-named contracts (`NotionAdapter`, `GoogleDocsAdapter`).** Rejected: the vendor is not a
  domain concept (`DOMAIN_MODEL.md` §8), and the name would lie after a replacement.
- **Contracts as prose only, with no code.** Rejected: mypy could not check conformance, and the
  boundary could not be tested. The code is a few Protocols and values.
- **`RunStore` speaking `RunSnapshot`.** Rejected: `RUN_RESTORE_SPEC.md` §6 already fixes
  `save(run)` / `load(run_id)`. Exposing the snapshot would make every caller responsible for
  taking it correctly.
- **Separate stores for Artifacts / trace.** Rejected: they are Run children, and the aggregate is
  saved atomically as a whole (`ARCHITECTURE.md` §10).
- **Two Notion contracts (brief intake, status reporting).** Rejected for now: one Notion Adapter
  in the docs, one implementation in sight. Splitting later is additive.
- **Moving `LLMClient` into `adapters/`.** Rejected now: it would rewrite `ADR-0014` code with no new
  consumer. Revisit if something outside infrastructure needs the LLM port.

## References
- `PROJECT.md`: §4 п.7 and п.11, §5, §6, §16
- `ARCHITECTURE.md`: §9, §10, §12, §13, §14, §15
- `DOMAIN_MODEL.md`: §2.1, §8, §9.1
- `ROADMAP.md`: Stage 6 (and Stages 9, 10, 11, 14 for the deferred items)
- `ADR-0007`, `ADR-0012`, `ADR-0013`, `ADR-0014`, `ADR-0015`, `ADR-0016`, `ADR-0018`, `ADR-0020`,
  `ADR-0022`
- `RUN_RESTORE_SPEC.md` §3, §6
- `ADAPTER_SPEC.md`, `ADAPTER_ACCEPTANCE.md`
