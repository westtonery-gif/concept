# ADR-0022: Tool Layer — the `Tool` contract, the per-agent `Toolbox` and the first two Tools

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 5 lays down the `Tools` layer: capabilities an agent calls *during its reasoning*,
kept clearly apart from Skills. Stage 5's DoD asks for:
- a Tool that is called in the context of an agent's reasoning and returns a result, covered by
  tests;
- a Tool/Skill boundary that is kept and written down;
- Tools that reach the outside world do so through adapters (stubs until Stage 6).

The Stage also asks for a mechanism where the set of Tools an agent may use comes from the agent's
configuration, where the agent cannot go past what it was granted, and where a Tool serves one
reasoning step and never steers the pipeline.

The docs fix the constraints:
- a Tool is a capability the **model** calls during reasoning, to act or to get data. A Skill is a
  deterministic module called by code. Skill ≠ Tool, and Adapter ≠ Tool: an Adapter serves the
  system, a Tool serves the agent's reasoning (`PROJECT.md` §18);
- a Tool is a controlled boundary. The agent does not reach the outside world directly, only
  through a declared Tool, which in turn goes through an Adapter when it has to leave the process
  (`ARCHITECTURE.md` §8);
- "the set of Tools used" is an optional attribute of the Agent (`DOMAIN_MODEL.md` §2.5).
  Agent—Tool is N:M, and a Tool is *referenced* by an Agent, not owned by it (§6, §9.3);
- in the layering, Tools sit next to Agents and Skills, above Adapters and the domain
  (`ARCHITECTURE.md` §15). Skills may depend on Tools (§3.6), so Tools must not depend on Skills,
  or the two layers would form a cycle.

No agent calls a Tool yet. The LLM port is a single structured completion that uses forced tool
use only to shape the output (ADR-0014). There is no reasoning loop in which a model could pick a
Tool. So this ADR builds the provider-agnostic seam that such a loop will drive. The loop itself
arrives with the first agent that is granted a Tool (Stage 7).

## Decision

### 1. Three places, mirroring Skills
- **`domain/tool.py`** holds the passive catalogue descriptor and the Tool domain errors, as
  `domain/skill.py` does for Skills.
- **`tools/`** is a new layer package (flat, one module per Tool). It holds the executable contract
  (`tools/contract.py`), the scoping mechanism (`tools/toolbox.py`), the Tools themselves and
  `tools/catalogue.py`.
- **`Agent.tool_refs`** is the grant. It is one additive field on the Agent descriptor.

### 2. The descriptor — identity plus the declared parameters
`ToolDescriptor(tool_id, version: ToolVersion, description, parameters)` is a frozen dataclass,
validated at construction (`InvalidToolDescriptorError`):
- `tool_id` is snake_case, `[a-z][a-z0-9_]{0,63}`. It is also the name the model calls the Tool
  by, so it has to be a valid function name for every mainstream provider;
- `description` is non-blank. The model reads it to decide when to call the Tool;
- `parameters` is a tuple of `ToolParameter(name, kind, description, required=True)` with unique
  names. It may be empty. `kind` is a `ToolParameterType`: `string`, `integer` or `boolean`;
- `ToolVersion.value` is an `int >= 1` (a `bool` is refused).

The derived `ref` is `<tool_id>@v<n>`, the same shape as a Skill ref.

This deliberately departs from ADR-0021 §2, which kept the input contract off the Skill
descriptor. A Skill's typed `apply` signature is read by the type checker, which is its only
reader. A Tool's parameters have two readers at runtime: the model, which forms a call from them,
and the `Toolbox`, which checks every call against them. Neither can read a Python signature. The
parameter list is provider-neutral data. Translating it into a provider's tool definition is the
LLM adapter's job.

There is no separate `name` field. The model-facing name is `tool_id`, and a second display name
would have no reader.

### 3. The executable contract — `Tool`
`Tool` is a structural `typing.Protocol` with two members:
- `descriptor -> ToolDescriptor`;
- `invoke(arguments: ToolArguments, /) -> Mapping[str, ToolValue]`, where `ToolValue` is
  `str | int | bool` and `ToolArguments` is `Mapping[str, ToolValue]`.

The rules for every Tool, which the tests check:
- **Blind to its caller.** `invoke` takes only the checked arguments: no `Run`, `Task`, agent,
  actor or context. A Tool serves one reasoning step and has nothing it could use to steer the
  pipeline.
- **No state between calls, no ambient world.** A Tool may need something outside the process: a
  clock today, an adapter after Stage 6. That dependency is **injected at construction**, never
  imported or reached for. The layer's modules import only an allowlist of pure stdlib,
  `domain.tool` and each other, so no SDK, network, filesystem, randomness, `time`, Skill, agent,
  application or infrastructure module. They also never call `now()`/`today()`/`utcnow()`. This is
  how the third DoD bullet holds until Stage 6: the outside world can enter a Tool only as an
  injected port, which Stage 6 will supply as an Adapter.
- **Failure is reported, not raised through.** A Tool that cannot produce a result raises
  `ToolExecutionError`. It is a technical failure, like `LLMError`, and deliberately not a
  `DomainError`. It lives in `tools/contract.py`.

How a Tool differs from a Skill, at a glance:

| | Skill (ADR-0021) | Tool (this ADR) |
|---|---|---|
| Who decides to call it | agent/system code | the agent's model, mid-reasoning |
| How it is reached | imported and called directly | only through the agent's `Toolbox` |
| Input | a typed frozen dataclass, trusted | untrusted model JSON, checked against the declared parameters |
| Determinism | required (pure) | not required; any world access is injected |
| Failure | `InvalidSkillInputError` raised to the caller | `ToolExecutionError`, returned to the model as `FAILED` |
| Verb | `apply` | `invoke` |

The distinct verbs mean that neither can be passed where the other is expected (mypy). The same
computation could be either one. What makes something a Tool is that the model chooses when to
call it.

### 4. Scoping — `Agent.tool_refs` and the `Toolbox`
- **The grant is agent configuration.** `Agent.tool_refs: tuple[ToolRef, ...] = ()`. Empty means
  the role has no Tools, which fails closed and is the default for Rin and Leo. Like `prompt_ref`,
  the grant is plain data. It is read only where the Toolbox is built, in the Composition Root
  (ADR-0012). The Agent itself calls nothing (ADR-0010).
- **`Toolbox(grants=…, available=…)`** is built once per agent. `grants` is the agent's refs and
  `available` is the Tool instances with their dependencies injected. The Toolbox takes refs, not
  an `Agent`, so the tools layer stays blind to agents. A grant that cannot be honoured fails at
  construction, before anything runs, with `ToolGrantError`: a ref that is not available, a ref
  granted twice, two versions of the same `tool_id` (the model-facing name would be ambiguous),
  two available Tools sharing a ref, or a bare `str` passed instead of a collection of refs.
- **`Toolbox.descriptors`** exposes only the granted Tools, in grant order. This is what the model
  is shown.
- **`Toolbox.invoke(ToolCall) -> ToolResult`** is the only way a model's call reaches a Tool. A
  `ToolCall(name, arguments)` is raw model output and is not validated at construction. The answer
  is always a `ToolResult(status, data, error)`:

  | Call | Status | Tool runs? |
  |---|---|---|
  | `name` is not a granted Tool (unknown, or known but not granted) | `REFUSED` (the error lists the granted names) | no |
  | `arguments` is not a mapping; has an undeclared key; lacks a required one; has a value of the wrong kind (`True` is not an `integer`) | `REFUSED` | no |
  | the Tool raises `ToolExecutionError` | `FAILED` | yes |
  | otherwise | `OK` with the Tool's data | yes |

  The Tool receives a read-only mapping of only the declared arguments. `ToolResult.data` is
  read-only as well.

Refusals are results, not exceptions, because a call comes from untrusted model output. The model
has to receive the refusal as an answer inside its reasoning step, so it can correct the call.
Refusing is still fail closed: the Tool never runs. Any exception other than `ToolExecutionError`
is a defect in our code, not something the model caused, so it propagates.

### 5. The first two Tools (no external service)
| Ref | Parameters | Returns | Why a Tool |
|---|---|---|---|
| `current_date@v1` | none | `date` (ISO), `weekday` (English, locale-independent), `datetime` (ISO, seconds, with offset) | a model does not know today's date. This is the canonical "eyes" capability. The clock is injected, must be timezone-aware (a naive clock gives `FAILED`), and is read on every call. The date is the clock's local date. |
| `text_metrics@v1` | `text: string`, `max_chars: integer` (optional, `>= 1`) | `characters`, `words`, `sentences`, `paragraphs`; with a limit also `max_chars`, `within_limit`, `over_by` | models count characters badly, while carousel slides and captions have hard limits. Lets a writer role check a draft mid-reasoning instead of guessing. |

`current_date` also sets the pattern for the third DoD bullet: an outside dependency is injected
as a port (a `Callable[[], datetime]`), never read from the system. The Composition Root decides
which clock and which timezone to pass (Stage 7).

### 6. Errors
`ToolDomainError(DomainError)` (ADR-0017) with `InvalidToolDescriptorError` and `ToolGrantError`,
co-located in `domain/tool.py`. `ToolExecutionError(Exception)` is technical and lives in the tools
layer. Immutability comes from frozen dataclasses, so there is no dedicated error for it.

### 7. Catalogue
`tools/catalogue.py` lists the descriptors (`TOOL_DESCRIPTORS`, `TOOLS_BY_REF`), with refs unique.
It is passive data. It holds descriptors, not instances, because a Tool instance carries injected
dependencies that only the Composition Root can supply.

### 8. What does not change
`Run`, `Task`, `Workflow`, `ContentDirector`, the application layer, the Composition Root, the LLM
port and infrastructure, and the Skills library are all untouched. `Agent` gains one defaulted
field, and every existing construction of an Agent still works unchanged.

## Deferred
- **The reasoning loop** (ROADMAP Stage 7, with the first agent that is granted a Tool). This means
  a tool-use loop in the LLM adapter: translating `Toolbox.descriptors` into provider tool
  definitions, turning the model's tool-use blocks into `ToolCall`s, feeding `ToolResult`s back,
  and a budget on calls per step. It changes the ADR-0014 port, so it gets its own ADR.
- **Building Toolboxes in the Composition Root** (Stage 7). This includes choosing the clock and
  timezone for `current_date`.
- **Tools on top of Adapters** (after Stage 6), such as a Notion or reference lookup. The import
  allowlist is widened explicitly, by ADR, and the adapter is injected like the clock.
- **Recording tool calls** in the Run trace and in Analytics (`PROJECT.md` §16, Stage 14).
- **Tool lifecycle** (`Active`/`Deprecated`), as with Skills.
- **Richer parameter kinds** (number, enum, arrays): each is added when a Tool needs it.

## Consequences

### Positive
- Stage 5's DoD holds and is checked by tests: a Tool is reached only through a per-agent grant,
  answers every call with a result, and cannot reach a `Run`, an agent, a Skill or the outside
  world except through an injected port.
- The Skill/Tool boundary is enforced in both directions. Skills cannot import `tools` (SKB-01),
  Tools cannot import `skills` (TLB-01), and the verbs differ.
- Stage 7 gets a ready seam. The loop only has to translate between provider blocks and
  `ToolCall`/`ToolResult`. Adding a Tool is one module plus one catalogue line.

### Negative / Trade-offs
- The contract is designed before its first consumer, and Stage 7 may extend it additively, by
  ADR.
- Parameter kinds are scalars only, and there is no per-parameter range (`max_chars >= 1` is
  checked by the Tool and reported as `FAILED`, not `REFUSED`).
- `ToolVersion` is a third copy of the int-version value object (after `PromptVersion` and
  `SkillVersion`). Extracting a shared one is a separate, behaviour-neutral refactor, deliberately
  not mixed into this change.
- `text_metrics` splits sentences on punctuation followed by whitespace or the end of the text. An
  abbreviation such as "т. е." counts as a sentence end.

## Alternatives considered
- **An abstract base class `Tool`.** Rejected, for the same reason as for Skills: a shared base
  with no shared behaviour. A Protocol gives the static check without inheritance.
- **A global registry resolved by name at call time.** Rejected: any agent could reach any Tool.
  Scoping has to be a per-agent object that simply does not contain ungranted Tools.
- **Raise on a refused call.** Rejected: model output would crash the reasoning step, and the
  model would never learn that its call was wrong. The Tool still never runs, so returning a
  result is just as fail closed.
- **Keep the grant in a Composition Root mapping instead of on the Agent.** Rejected:
  `DOMAIN_MODEL.md` §2.5 names it an Agent attribute, and ROADMAP Stage 5 says the set of Tools
  "задаётся конфигурацией агента".
- **A raw JSON-Schema dict as `parameters`.** Rejected: it is provider-shaped, unvalidated, and
  would leak one provider's format into the domain. The typed `ToolParameter` list is translated at
  the adapter.
- **Build the tool-use loop now.** Rejected on scope: it changes the ADR-0014 port with no agent to
  exercise it, and Stage 7 is where the first real agent runs through the full orchestrator.
- **Tools that wrap Skills** (e.g. exposing `check_required_elements` to the model). Rejected for
  now: Skills may depend on Tools (`ARCHITECTURE.md` §3.6), so the reverse direction would open a
  cycle.

## References
- `PROJECT.md`: §4 п.7, §16, §18
- `ARCHITECTURE.md`: §3.5–§3.8, §8, §15
- `DOMAIN_MODEL.md`: §2.5, §6, §9.3
- `ROADMAP.md`: Stage 5 (and Stages 6, 7, 14 for the deferred items)
- ADR-0010 / ADR-0012 (the Agent calls nothing; resolution happens in the Composition Root),
  ADR-0014 (the LLM port), ADR-0017 (`DomainError`), ADR-0021 (Skills)
- `TOOL_SPEC.md`, `TOOL_ACCEPTANCE.md`
