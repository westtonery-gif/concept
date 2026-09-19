# ADR-0052: `max_tokens` and extended thinking belong to the role's binding

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** Lead Architect / Maintainer (both configuration decisions chosen by the maintainer)
- **Realizes:** `CLAUDE.md` queue task 17 (a defect found while setting up the M3 pilot)
- **Amends:** ADR-0016 (what a role's binding contains), ADR-0014/0028 (the adapter's request shape)

## Context

`infrastructure/llm.py` held `_DEFAULT_MAX_TOKENS = 2048` as a constructor default, and
`provider_model.py` built `AnthropicLLMClient(model=…, pricing=…)` without it. No environment
variable could change it, unlike provider, model and the three pricing values — so the one request
parameter that decides whether an answer fits at all was the only part of the selection that lived
in code (ADR-0016 says selection lives in configuration).

This was harmless while every model had thinking off by default. It is not any more, and the failure
is silent by design:

- On **Claude Opus 5** and **Claude Sonnet 5** thinking is on when the request omits `thinking`, and
  thinking spends the same output budget as the answer.
- A role that thinks its way through 2048 tokens never emits the forced `emit_fields` call,
  `_extract_fields` returns `{}`, `Schema.validate` says `INVALID`, and the Run ends `FAILED` with
  `INVALID_OUTPUT_REASON` (ADR-0033). The fail-closed path works exactly as designed — for a
  configuration reason rather than a content one, which is the worst kind of correct behaviour.

The live M3 pilot therefore ran `claude-haiku-4-5` (thinking off by default, so 2048 is the whole
answer). That was a workaround for this defect, not a preference.

Two further facts about the current API were checked, not recalled, and they shape the decision:

1. **`budget_tokens` is removed on the current models.** `thinking: {"type": "enabled",
   "budget_tokens": N}` returns **400** on Opus 5, Opus 4.8/4.7, Sonnet 5 and the Fable family; it is
   deprecated on Opus 4.6 / Sonnet 4.6 and remains the only form on Haiku 4.5 and older models. The
   current on-mode is `thinking: {"type": "adaptive"}`.
2. **A model's default is not stable across models.** Omitting `thinking` means adaptive on Opus 5 /
   Sonnet 5 and no thinking on Opus 4.8/4.7 and Haiku 4.5. Inheriting that default means the
   factory's behaviour changes under it whenever a role's model changes.

Two decisions were **asked, not guessed** — the maintainer chose (2026-09-19):

1. `max_tokens` becomes part of the role's binding and is **required**, exactly like the pricing
   values: no value → a managed refusal, no default in code.
2. The adapter stops inheriting whatever the model's default happens to be: thinking is **sent
   explicitly** and is **configurable per role**.

The maintainer's answer named `budget_tokens` as the knob. Fact (1) above makes that spelling
unusable on every current model, so the grammar below expresses the same intent — one explicit
per-role thinking choice — in the form the API actually accepts. That correction is the only
deviation from the answer as given.

## Decision

### 1. `OMEMO_MAX_TOKENS__<ROLE>` — required, no default anywhere

A role's binding gains `max_tokens`, read from `OMEMO_MAX_TOKENS__<token>` with the same role-token
normalization as the existing variables. For an `anthropic` binding it is **required**: absent,
blank, non-integer, `0`, negative or a float spelling → `ProviderModelSelectionError` naming it,
before any client is returned. A `fake` binding ignores it, as it ignores pricing.

`_DEFAULT_MAX_TOKENS` is **deleted** and `AnthropicLLMClient(max_tokens=…)` becomes a **required**
keyword argument. This is the fix, not a nicety: as long as the adapter can be constructed without a
decision, some caller will construct it that way.

No default is chosen in code — not 2048, not a larger number. A default here is a guess about the
role's model, and ADR-0016 gives that guess no home. `.env.example` and `README.md` carry the
operator's guidance instead (≥ 16000 for a current model; 2048 is too small for one that thinks).

### 2. `OMEMO_THINKING__<ROLE>` — required, and the request says exactly what it holds

A role's binding gains a thinking choice, read from `OMEMO_THINKING__<token>`, **required** for an
`anthropic` binding in the same way. Its grammar is closed (case-insensitive, surrounding whitespace
ignored); anything else is a managed refusal:

| Value | What the request carries |
|---|---|
| `adaptive` | `thinking: {"type": "adaptive"}` — the current models' on-mode |
| `disabled` | `thinking: {"type": "disabled"}` |
| `budget:<N>` | `thinking: {"type": "enabled", "budget_tokens": N}` — Haiku 4.5 and older models |
| `inherit` | the request **omits** `thinking` |

`inherit` exists because on some models neither `adaptive` nor `disabled` is accepted, and a
configuration language that cannot express "leave it to the model" would strand them. It is not the
same as today's behaviour: today inheritance is what happens when nobody decided, and after this ADR
it is a decision someone wrote down and can grep for.

`budget:<N>` is validated against the API's own rules where the factory can see them: `N` must be an
integer ≥ 1024 and **< `max_tokens`**. Both checks run at construction — that is, at selection time,
before a single token is bought. A non-numeric or out-of-range `N` is the same managed refusal.

The adapter itself gains one immutable value object (`ThinkingSetting`) and sends what it holds on
**every** provider turn, the single-call path and each turn of the ADR-0028 Tool loop alike. Nothing
else in the request shape changes; thinking blocks that come back are replayed unchanged inside the
loop (already the case — the loop appends `message.content` verbatim), and `_extract_fields` keeps
reading only the `emit_fields` `tool_use` block, so a `thinking` block in the answer is ignored as
any other block is.

### 3. Selection stays the only owner

`client_for_role` remains the single public entry, the Composition Root gains no selection logic
(ADR-0012), the `LLMClient` port is untouched, and `FakeLLMClient` needs neither variable. Both new
values are per-role, like everything else in the binding: a QA role may think while the producers do
not.

### 4. What this ADR deliberately does not decide

- **`output_config.effort`** (`low`…`max`) — the other half of the current thinking surface, and the
  lever the API's own guidance prefers over disabling thinking on Opus 5. It is a second knob with
  its own per-model validity table; nothing in the factory needs it to stop failing, so it stays out
  until a role does.
- **`thinking.display`** — the adapter never reads reasoning text; it reads one tool call.
- **Streaming for large `max_tokens`.** Values near 128K require streaming to avoid HTTP timeouts.
  The adapter is non-streaming and stays so; the operator guidance says to keep `max_tokens` inside
  what a non-streaming request can return.
- **Forced tool use is unavailable on Claude Fable 5.1 / Mythos 5.1**, which reject
  `tool_choice: {"type": "any"}` and `{"type": "tool"}` with a 400. ADR-0014's structured output
  *is* a forced tool call, so those models cannot back a role at all, whatever these two variables
  say. Recorded here as a known limitation: lifting it means changing the ADR-0014 mechanism
  (`output_config.format` or `auto` + `strict`), which is its own decision.
- **A per-model validity table in the factory.** The factory does not know which thinking mode a
  model accepts and must not guess: a wrong pair fails at the provider with a 400, which
  `AnthropicLLMClient` already turns into a managed `LLMError` (`FAILED` Task, PROJECT §10). Encoding
  the table would hardcode model knowledge, which is exactly what PROJECT §5 forbids.

## Consequences

### Positive

- The parameter that decides whether an answer fits is configuration, per role, like the model and
  the prices — and the pilot's `claude-haiku-4-5` workaround can be retired by editing `.env`.
- The factory's behaviour no longer changes under it when a provider changes a model's default: the
  request states the choice.
- Both new values fail closed with the value named, so a misconfiguration is a startup refusal, not
  an `INVALID_OUTPUT_REASON` Run failure after paid calls.
- A misconfigured `budget:<N>` (below 1024, or ≥ `max_tokens`) is refused before the call the
  provider would have rejected.

### Negative / Trade-offs

- **Two new required variables per `anthropic` role.** An existing `.env` fails closed at startup
  until both are added (the error names the missing one). Accepted, and deliberately symmetric with
  ADR-0029's pricing: an optional `max_tokens` is the defect this ADR exists to remove, and an
  optional thinking value would silently inherit the model default again.
- `AnthropicLLMClient`'s constructor is no longer backward compatible. Its callers are this
  repository's own (`provider_model.py`, `demo.py`, the tests that model the production path); each
  now states both values.
- `demo.py`, the older non-catalogued entrypoint with global bindings, gains `OMEMO_LLM_MAX_TOKENS`
  and `OMEMO_LLM_THINKING` alongside its global pricing variables.

## Alternatives considered

- **Optional `OMEMO_MAX_TOKENS__<ROLE>` with a larger default (8192).** Rejected by the maintainer:
  it keeps a model-shaped guess in code, and the silent failure returns for anyone who does not set
  it on a thinking model.
- **Always send `thinking: {"type": "disabled"}`.** Rejected by the maintainer: it is the cheapest
  and most deterministic option, but it cannot be turned on without a code change, and on Opus 5
  disabled thinking is documented to sometimes write a tool call into visible text instead of a
  `tool_use` block — which is precisely the block ADR-0014 depends on.
- **Leave thinking alone in this task.** Rejected: the inherited default is what made a
  configuration error look like a content failure, and it varies per model.
- **`budget_tokens` as the only knob** (the answer as literally given). Rejected on fact: a 400 on
  every current model. Kept as the `budget:<N>` spelling for the models that still take it.
- **A `max_tokens` upper bound in the factory.** Rejected: the cap is per model (200K context and
  128K output differ by model), so a bound here would be another hardcoded model fact.

## References

- `PROJECT.md` §5 (selection in configuration, never hardcoded), §10 (a Run is always in an explicit
  state), §16
- ADR-0014 (structured output through a forced tool call), ADR-0016 (selection ownership), ADR-0028
  (the bounded Tool loop), ADR-0029 (explicit per-role pricing, fail-closed), ADR-0033
  (`INVALID_OUTPUT_REASON`)
- `PROVIDER_MODEL_SPEC.md` 1.2 §3, §4.2; `PROVIDER_MODEL_ACCEPTANCE.md` 1.2 §4, §7
- `n8n/README.md` → Operator verification log (the pilot that surfaced this), `CLAUDE.md` queue
  task 17
