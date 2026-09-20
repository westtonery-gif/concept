# ADR-0054: Side-effecting Tools — a thin Tool over an injected port, and the rule that they observe rather than change

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** Lead Architect / Domain Architect
- **Realises:** ADR-0022 "Deferred" — *"Tools on top of Adapters (after Stage 6) … The import
  allowlist is widened explicitly, by ADR, and the adapter is injected like the clock."*
- **Serves:** `CLAUDE.md` queue task 21.1 (the clipping department, ADR-0053)

## Context

Today every Tool in `tools/` is pure. `tests/test_tool_contract.py` enforces it statically: the
layer may import an allowlist of stdlib modules, `omemo_content_factory.domain.tool` and its own
package, nothing else (TLB-01), and no module may read `now`/`today`/`utcnow` (TLB-02). The one
outside dependency in the library — `current_date@v1`'s clock — arrives as a `Callable[[],
datetime]` injected at construction, so the rule holds without an exception.

The clipping department (ADR-0053) needs Tools that reach a real service: a clip-planning agent has
to ask an indexed episode for moments while it reasons. That is the case ADR-0022 named and
deferred. The question this ADR answers is **not** whether such a Tool may exist — ADR-0022 §3
already says *"a Tool may need something outside the process … That dependency is injected at
construction"*, and `ARCHITECTURE.md` §8 already says a Tool reaches the world only through an
Adapter. What is missing is the boundary written down: which import becomes legal, what a failure
looks like, what it may cost, and — the part nothing in the repo had stated — **what a Tool is
allowed to do out there.**

That last one came out of reading ADR-0026 §4 against ADR-0022. ADR-0026 §4 says an external call
whose answer was not committed is made again: *"an executor or evaluator call is at least once
across a crash."* Tool calls happen **inside** an executor call, and ADR-0028 keeps their payloads
transient — nothing about a Tool call is committed. So every Tool call inherits at-least-once
semantics, and a crash mid-step replays every Tool the model had already called in that step. While
Tools were pure this was invisible: `current_date` twice is `current_date` once. The moment a Tool
touches the world it stops being invisible, and no document in this repo had noticed.

## Decision

### 1. A side-effecting Tool is a thin Tool over a port injected at construction

Three places, the same split every outside system in this repo already uses (ADR-0023):

- the **port** is a `Protocol` in `adapters/`, named by its role and not by its vendor
  (`DOMAIN_MODEL.md` §8), with its own technical `<Contract>Error` — not a `DomainError`;
- the **implementation** lives in `infrastructure/`, the only package allowed a third-party
  dependency or network I/O (`tests/test_adapter_contract.py`);
- the **Tool** in `tools/` holds the port, translates the model's checked arguments into one port
  call and the answer into its result mapping. It constructs nothing, imports no client, reads no
  environment and keeps no state between calls.

The Composition Root builds the implementation and injects it, exactly as it injects the aware
clock into `CurrentDate` (ADR-0028 §1). A Tool that reached for its dependency instead of receiving
it would be unbuildable in a test and untraceable in production; that is why the rule is injection,
not import.

### 2. Tools observe the outside world. They do not change it

**A Tool's call must be repeatable without a second irreversible consequence.** Reads, queries,
lookups and searches qualify. Anything that creates, uploads, publishes, deletes, renders to a
file, or otherwise leaves a mark that a repeat would duplicate does **not** — it belongs in a
deterministic `Workflow` step, where `ContentDirector` commits its outcome (ADR-0026 §2) and a
crash resumes instead of repeating.

This follows from at-least-once, not from taste. A model may call a Tool, the process may die
before the Task commits, and `resume` re-executes that step on its stored input — re-entering
`RUNNING` and calling the model again, which will call the Tool again. The `attempt_count` makes
the repeat visible (ADR-0026 §4), but visible is not harmless: a Tool that had uploaded a file
would now have uploaded it twice, and nothing in the Run would say which upload is the real one.

The rule is deliberately stricter than "side-effecting Tools are allowed", which is how ADR-0022's
deferral is easy to misread, and it costs the clipping department nothing: ADR-0053 §4 already puts
indexing, cutting and rendering in Workflow steps and leaves the agent only questions to ask. It is
also the reason this ADR's title says *observe rather than change* — the phrase is the contract, not
decoration.

A paid query is still a query. Repeating it spends money again but corrupts nothing, so it stays on
the legal side of this line; §4 bounds the spending.

### 3. The import allowlist widens by name, one port at a time

`_ALLOWED_PROJECT_IMPORTS` in `tests/test_tool_contract.py` gains the **exact module** of each port
a Tool is built over — `omemo_content_factory.adapters.<port>` — never the `adapters` package as a
prefix and never a pattern. Adding a port to that set is an ADR-sized decision every time, which is
the property ADR-0022 was protecting when it said the widening happens "explicitly, by ADR".

Two constraints come with it:

- **Direction.** `tools/` may import `adapters/`, never the reverse. Tools sit above Adapters
  (`ARCHITECTURE.md` §15), and `adapters/` imports only stdlib and `domain.*`, so there is no cycle.
- **A port a Tool uses carries its own frozen dataclasses**, as `IncomingBrief` does, rather than
  domain entities. `BriefBoard` imports `domain.run.RunStatus` because the core reports a Run's
  status to it; a Tool's port has no such need, and keeping it that way stops `tools/` from growing
  a transitive path to the aggregate it is forbidden to touch.

TLB-01's row in `TOOL_ACCEPTANCE.md` and the matching text in `TOOL_SPEC.md` are amended **in the
same change as the first such Tool**, together with the allowlist entry itself — so the specification,
the test and the code that needs them land together rather than a document claiming a boundary no
test yet checks.

### 4. Failure is reported to the model; cost is bounded by the existing budget

A port raises its own technical error. The Tool catches **that** error and raises
`ToolExecutionError`, which the Toolbox turns into a `FAILED` result the model receives and may
correct (ADR-0022 §4). Any other exception is a defect in our code and still propagates untouched.
The message a Tool puts in `ToolExecutionError` **reaches the model**, so it says what went wrong
and never carries a token, key, signed URL or credential — the same discipline
`notion_settings_from_env` and `google_docs_settings_from_env` already keep for logs.

Spending is bounded by what already exists: ADR-0028 §3's budget of `max_tool_calls` operational
calls per Task step (default 8), where an over-budget batch runs nothing. Two properties of that
budget matter now that a call can cost money, and both fall out of ADR-0022 §4 unchanged: a
`REFUSED` call never reaches the Tool, so a malformed or ungranted call is free; a `FAILED` one ran,
so it may have cost. The budget is therefore also the per-step spend bound.

### 5. No dependency is chosen here

Which client a concrete adapter speaks — an SDK, an MCP transport, raw `urllib` — is decided by that
adapter's own ADR with its own evaluation, the way ADR-0040 chose stdlib `urllib` over
`notion-client` and ADR-0043 accepted `cryptography` as the one new runtime dependency. This ADR
fixes the shape, not the vendor surface.

## Deferred

- **Richer `ToolValue` kinds.** `ToolValue` is `str | int | bool` and a Tool answers with a flat
  mapping, so a Tool cannot return a list of candidates. ADR-0022 deferred the wider kinds and
  ADR-0053 left the choice to the planner's own ADR: carry the list in the agent's Structured
  Output, or widen the kinds — which would touch the Toolbox's argument checking and the provider
  translation, so it is not a free change.
- **A per-role `max_tool_calls`.** It is a constructor argument on `AnthropicLLMClient` today, not
  configuration. A paid Tool is the first reason to want it per role, the way ADR-0052 made
  `max_tokens` per role. Not needed until a paid Tool exists.
- **Tracing Tool calls.** ADR-0028 keeps arguments and results transient and ADR-0029 records only
  provider turns. A Tool that spends money is an argument for recording the call — and, separately,
  for reconsidering it as an `AnalyticsRecord` subject, which today is a Task **or** an Evaluation
  (ADR-0036). Both stay deferred.
- **The clipping department's actual ports** — the episode source and the video-understanding
  service — with their names, methods and dependency choices (queue task 21).

## Consequences

### Positive

- The boundary ADR-0022 promised exists, and the property it was protecting is kept: every new port
  a Tool may import is named in a test, so widening the layer stays a decision rather than a drift.
- A latent hole is closed before it could be dug. At-least-once Tool calls were already true and
  already unstated; a department that uploads and renders would have found it in production.
- The clipping department can proceed: its agent needs to ask questions, which is exactly what §2
  permits, and its effects were already placed in Workflow steps by ADR-0053 §4.

### Negative / Trade-offs

- §2 forbids a shape some agent designs want — a model that acts, observes its own effect and
  continues. Anything like that must be decomposed into Workflow steps with the model between them,
  which is more plumbing than one Tool call.
- Two things now govern one layer: this ADR says what a Tool may do, ADR-0022 says how it is built.
  A future reader has to hold both.
- The allowlist growing one name per ADR is friction by design, and it will feel like friction the
  third time.

## Alternatives considered

- **Let a Tool declare its own narrow `Protocol` locally**, so `tools/` imports nothing new and the
  allowlist never changes (structural typing means the real adapter satisfies it). Rejected: it
  makes the boundary invisible — the layer would quietly gain outside reach with no test to notice —
  and it duplicates a contract that would then drift. The allowlist's whole value is that widening it
  is loud.
- **Widen the allowlist to the whole `adapters` package.** Rejected for the same reason: it converts
  an ADR-sized decision into an import.
- **Put the port's I/O directly in the Tool and move the Tool into `infrastructure/`.** Rejected: a
  Tool is reached only through a `Toolbox` built in the Composition Root from an Agent's grant
  (ADR-0022 §4), and `tests/test_adapter_contract.py` lets only `composition.py` import
  `infrastructure`. The Tool would become unreachable by its own grant mechanism.
- **Allow mutating Tools and make them idempotent with a caller-supplied key.** Rejected for now:
  the key would have to come from the Run to be stable across a resume, and `invoke` deliberately
  receives no `Run`, `Task` or caller identity (ADR-0022 §3). Reversing that is a bigger change than
  the clipping department needs, and §2 costs it nothing.

## References

- `PROJECT.md` §18 (Skill ≠ Tool, Adapter ≠ Tool), §4 п.11 (extend by adding)
- `ARCHITECTURE.md` §8 (a Tool reaches the world only through an Adapter), §10, §15 (layering)
- ADR-0022 §3/§4/§5 + "Deferred" (the Tool contract, the Toolbox, the reserved widening), ADR-0023
  (ports named by role), ADR-0026 §2/§4 (commit points; at-least-once), ADR-0028 §1/§3 (injection at
  the Root; the per-step budget), ADR-0029/0036 (what is recorded), ADR-0053 (the clipping
  department's shape)
- `TOOL_SPEC.md`, `TOOL_ACCEPTANCE.md` §2 (TLB-01), `tests/test_tool_contract.py`
