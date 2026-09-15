# ADR-0025: In-memory stubs for the board, the review desk and the analytics sink

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 6 asks for "contract and a basic implementation/stub" of the Notion, Google Docs and
Analytics Adapters ("полноценная интеграция — Этапы 9–11; здесь — контракт и базовая
реализация/заглушка"), and its DoD asks that the contracts be "covered by tests against fakes". The
session queue calls this Stage 6c (task 7.5). Already in place:

- `ADR-0023` fixes the contracts — `BriefBoard`, `ReviewDesk`, `AnalyticsSink` — and their shared
  rules (§4): implementations live in `infrastructure/`, raise their own technical error, write
  idempotently and never change a Run. Its trade-offs note that the idempotency rules "can be
  checked only against an implementation, from 6b/6c on".
- `ADR-0024` implemented the fourth contract, `RunStore`, as `SqliteRunStore`.
- `FakeLLMClient` (`ADR-0014`) is the precedent for a keyless implementation: it is infrastructure
  behind the port, not a test double, and nothing in the core knows it is fake.

The only implementations of the three contracts today are the null conformers in
`tests/test_adapter_contract.py`, which prove shape only. Stage 7 needs something the Content
Director can be wired to and an entrypoint can run without Notion or Google credentials; Stages
9–10 need a reference for what the real adapters must do. Scope discipline (`CONTRIBUTING.md`)
rules out real integrations here.

## Decision

### 1. Three in-memory implementations in one infrastructure module
`infrastructure/in_memory_adapters.py` holds `InMemoryBriefBoard`, `InMemoryReviewDesk` and
`InMemoryAnalyticsSink`. They keep their state in the process's memory, import only the pure stdlib
allowlist of the contracts plus `domain.*` and `adapters.*`, and reach nothing outside the process.

- They are **infrastructure, not test doubles**, like `FakeLLMClient`: the Composition Root may
  inject them wherever no real board, desk or analytics store exists (keyless runs, demos, Stage 7).
- One module, not three: they share one nature (no vendor, no I/O, no credentials) and change
  together. Each real vendor implementation (Stages 9–10, 13–14) gets its own module.
- Names say "in memory", not a vendor, and each class satisfies its Protocol structurally (mypy).

### 2. The other side of the wall is explicit
A contract covers only what the core calls. A stub also has to play the outside party the core
never sees, so each stub adds a small **control side**, outside its Protocol:

| Stub | Plays | Control side |
|---|---|---|
| `InMemoryBriefBoard` | the editor who files briefs | `put(brief, *, ready=True)`; `reports(brief_ref)` reads what the board shows |
| `InMemoryReviewDesk` | the human reviewer | `decide(review_id, decision)`; `published(review_id)` reads what the reviewer sees |
| `InMemoryAnalyticsSink` | the analytics store | `records` reads what was delivered |

The core only ever holds the Protocol type, so it cannot call the control side.

### 3. Behaviour, including the cases the contract leaves open
Everything `ADAPTER_SPEC.md` §5–§7 fixes holds as written. Where the contract is silent, the stubs
**fail loudly rather than guess**, so a wiring bug in Stage 7 surfaces as an error instead of a
silently wrong board or report:

- **Board.** `fetch_brief` returns the brief as filed only when it is ready; unknown and not-ready
  give `None`. Re-filing a brief (`put`) replaces it, which is how a not-ready brief becomes ready.
  `report_status` appends `(run_id, status)` to the brief's history unless it repeats the last
  report exactly (idempotent). Reporting on a brief the board never had raises `BriefBoardError`
  and records nothing — a board cannot show a status on a page that does not exist.
- **Desk.** `publish` stores the package and returns `memory://reviews/<review_id>`. Publishing the
  same package again returns the same location and keeps one copy. **A different package under an
  already-published `review_id` raises `ReviewDeskError`** and keeps the first: silently replacing
  the candidate a person may already be reading, or silently ignoring the new one, would both let
  the decision land on something other than what the core thinks was reviewed. `fetch_decision` of
  an unpublished id raises `ReviewDeskError`; of a pending one returns `None`. `decide` needs a
  published review; the first decision is final — repeating it is a no-op, a different one raises
  `ReviewDeskError`, mirroring the Run, where a decided review stays decided.
- **Sink.** `export` keeps records by `record_id` in delivery order; a record already delivered is
  skipped. **A different record under a delivered `record_id` raises `AnalyticsSinkError`**, and
  the batch is checked before anything is stored, so a refused export delivers nothing. Records are
  immutable and their ids are unique per Run, so a conflict means a bug upstream, never a retry.

### 4. Not wired
Nothing in `src` imports the stubs yet; the Composition Root, `ContentDirector` and the entrypoints
are unchanged (as with `SqliteRunStore`, `ADR-0024` §6). When the Content Director fetches briefs,
reports statuses, publishes reviews, polls decisions and exports records is Stage 7 wiring
(`ADR-0023` "Deferred").

### 5. Stage 6 DoD after this ADR
| DoD item | Status |
|---|---|
| Contracts of the five adapters; the core calls no external API directly | done (`ADR-0023`) |
| LLM swapped through configuration | done (`ADR-0016`) |
| Storage really persists `Run` | done (`ADR-0024`) |
| Contracts covered by tests against fakes | **done**: every contract has an implementation tested against its spec — `SqliteRunStore` and the three stubs (`ADAPTER_ACCEPTANCE.md` §5–§6) |

With this, ROADMAP Stage 6 is complete; Stage 7 (the first real Agent through the full
orchestrator, Milestone M2) is next.

## Deferred
- **Wiring** the four adapters into the Content Director and an entrypoint (Stage 7).
- **Real integrations:** Notion (Stage 9), Google Docs (Stage 10), n8n triggers (Stage 11), analytics
  export (Stages 13–14) — each a new module implementing the same Protocol.
- **Failure injection** (a stub that raises its contract error on demand) to test that a failed
  report or export never blocks the Run. It has no reader before Stage 7 wiring; add it then.
- **Durability of stub state.** It lives and dies with the process. The Run's truth is in the
  `RunStore`; a restarted process re-files briefs and re-publishes pending reviews — which is what
  the idempotent `publish` exists for.
- **Thread safety.** Calls are synchronous and single-threaded (`ADR-0023` §4 п.7).

## Consequences

### Positive
- Every Stage 6 DoD item holds; the Adapter Layer is complete at the contract-plus-stub level.
- The idempotency rules of `ADR-0023` are now executable tests, and the cases the contract left open
  are decided once, in writing, before any real adapter has to guess them.
- Stage 7 can wire the whole Run lifecycle — brief in, statuses out, review round-trip, analytics
  export — keyless and deterministic.

### Negative / Trade-offs
- The control side (`put`, `decide`, `reports`, `published`, `records`) is stub API that no real
  adapter has. It stays off the Protocols, so the core cannot come to depend on it.
- The "fail loudly on conflict" choices are stricter than the contract text. A real adapter may
  satisfy the contract without matching them; if Stages 9–10 need a different rule, the contract
  gets an ADR, not the stub a silent change.
- In-memory state means a stub-backed demo loses its board and desk on restart.

## Alternatives considered
- **Keep fakes in `tests/` only.** Rejected: ROADMAP asks for a basic implementation as a system
  component, and Stage 7 needs something the Composition Root can inject outside tests —
  `FakeLLMClient` sits in `infrastructure/` for the same reason.
- **File-backed stubs (a JSON file per board/desk).** Rejected: nothing needs the state to survive a
  restart yet, and file I/O would add an error surface to stand-ins for services whose own
  persistence is theirs, not ours.
- **Last-write-wins on a conflicting republish or record.** Rejected: it hides exactly the bugs a
  stub is there to expose, and for the desk it could let an approval land on a candidate nobody saw.
- **Control side on the Protocols** (e.g. `ReviewDesk.decide`). Rejected: the core would gain a way to
  forge the human's decision, against `ADR-0007` / `ADR-0023` §7 ("the desk carries the decision; it
  never makes it").
- **Three modules, one per stub.** Rejected for now: they share one nature and change together;
  splitting later is a move, not a redesign.

## References
- `PROJECT.md`: §4 п.7 and п.11, §5
- `ARCHITECTURE.md`: §9, §12, §13, §14
- `ROADMAP.md`: Stage 6 (and Stages 7, 9–11, 13–14 for the deferred items)
- `ADR-0007`, `ADR-0014`, `ADR-0015`, `ADR-0020`, `ADR-0023`, `ADR-0024`
- `ADAPTER_SPEC.md` §5–§7, `ADAPTER_ACCEPTANCE.md` §6
