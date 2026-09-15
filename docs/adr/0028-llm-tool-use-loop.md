# ADR-0028: Provider-neutral LLM tool-use loop

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect
- **Amends:** ADR-0014 (`LLMClient` is no longer a single provider round-trip)
- **Realises:** ADR-0022 §Deferred (reasoning loop and Composition Root Toolbox wiring)

> **Amended by ADR-0029 (2026-09-15).** `complete` now returns structured fields together with one
> measurement for every completed provider turn. The Tool authorization, loop and budget decisions
> in this ADR are unchanged.

## Context

ADR-0014 made `LLMClient.complete(system, user, fields)` a uniformly structured, single-call port.
The Anthropic adapter used one forced, private `emit_fields` tool to obtain the final mapping. That
closed the structured-output gap, but left no turn in which a model could call one of the Tools
defined by ADR-0022. `Agent.tool_refs`, `Toolbox`, `ToolCall` and `ToolResult` therefore exist but no
production agent can exercise them.

ROADMAP Stage 7 now has a consumer: Rin (`content_researcher@v1`) needs `current_date@v1` for briefs
whose meaning depends on “today”, “this week” or recency. The LLM boundary must support a bounded
conversation without moving reasoning into the Content Director, weakening Schema authority, or
letting a model call outside its grant.

## Decision

### 1. The port receives one already-scoped Toolbox per task step

The provider-neutral port becomes conceptually:

```python
complete(*, system: str, user: str, fields: Sequence[str], toolbox: Toolbox) -> Mapping[str, str]
```

`LLMTaskExecutor` owns the Toolbox injected at construction and passes it to every completion.
The Composition Root builds one Toolbox per Agent from `agent.tool_refs` and the available Tool
instances. It injects dependencies into those instances: the default `current_date@v1` instance
receives an aware local clock from the Root, and tests/callers may inject another clock.

The port receives neither an Agent nor a Run. `Toolbox.descriptors` is the complete provider-facing
capability surface and `Toolbox.invoke` is the only execution path. An empty grant is a valid empty
Toolbox and keeps existing roles/callers fail closed.

This amends ADR-0014's “single completion” wording and narrows its I3 import claim: the LLM adapter
may now read the Tool layer's domain descriptor through `Toolbox`, because it must translate that
provider-neutral contract. `fields` is still opaque, no Schema or validation rule crosses the port,
and Schema remains the only validator.

### 2. The Anthropic adapter owns the multi-turn loop

`AnthropicLLMClient` translates each granted `ToolDescriptor` into the provider's tool schema. The
private `emit_fields` tool remains separate from the grant and remains the only way to finish with
structured output. It is an adapter mechanism, not a domain Tool and never appears in a Toolbox.

- with no granted Tools the adapter keeps the one-call forced `emit_fields` path;
- with granted Tools, each provider turn must choose either a granted Tool or `emit_fields`;
- a granted tool-use block becomes `ToolCall`, is invoked through the Toolbox, and its complete
  `ToolResult` (`status`, `data`, `error`) is returned as the provider's `tool_result` block;
- `REFUSED` and `FAILED` are returned to the model with `is_error=true`, so it may correct the next
  call; neither becomes orchestration or pipeline routing;
- a turn mixing final `emit_fields` with operational Tool calls is an `LLMError`: a final result may
  not race with effects whose results it has not observed;
- a provider/API failure remains `LLMError`; an unexpected exception from Tool code remains a code
  defect and propagates as ADR-0022 requires.

The adapter preserves the provider's assistant blocks verbatim in conversation history and appends
one user `tool_result` block for every call id, so multiple tool calls in one turn are supported.

### 3. Every task step has a hard operational-call budget

`AnthropicLLMClient.max_tool_calls` is a positive integer (default 8). The budget counts requested
operational Tool calls, including refused and failed calls; the private finalizer does not count.
Before a multi-call turn is executed, the whole batch must fit. Exceeding the budget raises
`LLMError` and no Tool in that over-budget batch runs. `LLMTaskExecutor` converts that error to the
existing managed failed `ExecutionResult`.

This bounds retries driven by untrusted model output without introducing a second orchestration
loop. Provider implementations may use different wire formats, but every implementation must keep
tool access scoped by the supplied Toolbox and bounded per `complete` call.

### 4. Rin receives `current_date@v1`

Rin's passive `Agent.tool_refs` contains `current_date@v1`. The role still calls nothing itself: the
model decides whether the brief needs the date. Leo keeps an empty grant. A deterministic adapter
test proves the sequence `provider asks for current_date → clock-backed Tool runs through Toolbox →
provider receives the result → emit_fields produces the final structured mapping`.

Tool-call payloads are not persisted in the Run trace. ADR-0029, the metrics-capture subtask that
followed this one, records each completed provider turn around the loop without storing Tool
arguments/results; this slice itself changed no Run, Task, Output or Artifact API.

## Consequences

### Positive

- A real production role can use a real Tool in the middle of model reasoning.
- The model sees and reaches only the Agent's grant; invalid/unknown calls remain fail closed.
- Structured output and Schema ownership are unchanged after the loop completes.
- The Content Director stays deterministic and unaware of provider turns and Tool calls.
- A finite call budget prevents an unbounded model/Tool cycle.

### Negative / trade-offs

- `LLMClient.complete` is a breaking port change; all implementations and fakes must accept the
  Toolbox even when it is empty.
- The current port couples the LLM adapter seam to the provider-neutral Tools layer. This is
  intentional: translating Tool descriptors and results is an LLM-adapter responsibility, while
  the Toolbox remains the authorization/execution authority.
- Tool-call traces are transient until the next Stage 7 metrics/trace slice.

## Alternatives considered

- **Put the loop in Content Director or application orchestration.** Rejected: provider blocks and
  conversational turns are adapter mechanics, and the orchestrator must not reason or call Tools.
- **Pass raw available Tools instead of a Toolbox.** Rejected: it would duplicate grant checks in
  every provider adapter and could expose ungranted capabilities.
- **Expose `emit_fields` as a domain Tool.** Rejected: it is a provider-specific structured-output
  mechanism, not a capability the Agent is granted.
- **Let the adapter retry forever.** Rejected: untrusted model output must have a hard per-step
  limit and fail in the existing managed execution path.

## References

- `PROJECT.md` §4, §9, §10, §18
- `ARCHITECTURE.md` §3.5–§3.8, §4, §6, §8, §9, §15
- `ROADMAP.md` Stage 7 / M2
- ADR-0014, ADR-0022, ADR-0023
- `TOOL_SPEC.md`, `TOOL_ACCEPTANCE.md`
