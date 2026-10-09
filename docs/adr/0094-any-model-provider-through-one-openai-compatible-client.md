# ADR-0094: Any model provider, through one OpenAI-compatible client

- **Status:** Accepted
- **Date:** 2026-10-09
- **Deciders:** Maintainer ("не второго провайдера, а любого" — a person with only Cursor and a Grok
  key cannot start the factory because it waits for Anthropic keys, 2026-10-09); Lead Architect
- **Amends:** ADR-0016 / `PROVIDER_MODEL_SPEC` (selection gains a provider family and an explicit
  all-roles default), ADR-0052 (the thinking grammar is per family)
- **Builds on:** ADR-0014 (the structured port), ADR-0028 (the bounded Tool loop), ADR-0029
  (measured calls, explicit prices)

## Context

The `LLMClient` port (ADR-0014) is provider-neutral and the core never mentions a vendor, but the
**only real adapter is Anthropic's** and a role's binding names `anthropic` or `fake`. A machine
without an Anthropic key therefore cannot run any text role — QA, post writer, story writer,
storyboard — even though the maintainer's other devices have a Grok (xAI) key, and tomorrow's
user may have OpenAI, OpenRouter, Mistral, DeepSeek or a model on their own laptop (Ollama).

Writing "a Grok adapter" next, then "an OpenAI adapter" after it, repeats the problem: it is the
*wire format* that varies far less than the vendors. Nearly every provider other than Anthropic —
xAI, OpenAI, OpenRouter, Groq, Mistral, DeepSeek, Together, Fireworks, Ollama, LM Studio, vLLM —
speaks the **chat-completions** dialect, and the newer **responses** dialect is documented by xAI
and OpenAI alike (xAI marks chat-completions "deprecated" while keeping it live, without a removal
date; both are therefore supported and the choice is one setting).

A second, separate friction: today every role needs its own five-to-seven variables, so pointing
all roles at one model is ~50 lines of `.env`.

## Decision

1. **One adapter for the family:** `infrastructure/openai_compatible_llm.py`
   `OpenAICompatibleLLMClient` — stdlib `urllib`, **no new dependency** (the repo's rule for every
   vendor since ADR-0040). It implements the unchanged `LLMClient` port and the same behaviour as
   `AnthropicLLMClient`: structured output by a private `emit_fields` function, the ADR-0028 bounded
   Tool loop through the scoped `Toolbox`, one measured `LLMCallMetrics` per completed turn priced
   from **explicit** rates (ADR-0029), `LLMError` carrying the metrics of turns already paid for.
2. **Two wire dialects, one setting** (`OMEMO_WIRE`): `chat` (default; `/chat/completions`) and
   `responses` (`/responses`). The difference lives in one small strategy object per dialect;
   nothing above it knows which is in use.
3. **Two ways to get structure** (`OMEMO_STRUCTURED`): `tool` (default — the function is forced by
   name), `tool-required` (`tool_choice: "required"`, for servers that reject a named choice; with
   the single private function it is equivalent) and `json` (the answer is a JSON object in the
   message, `response_format: json_object`, for local models without reliable function calling).
   In every mode a model that returns no usable answer yields empty fields, which `Schema.validate`
   judges — the client never judges (ADR-0014 §2, Variant B).
4. **Selection:** `OMEMO_PROVIDER__<ROLE>` may now name any **preset** — `xai`, `openai`,
   `openrouter`, `groq`, `mistral`, `deepseek`, `together`, `ollama`, `lmstudio` — or
   `openai-compatible` (everything explicit). A preset only supplies a **base URL**, the **name of
   the environment variable holding the key** (`XAI_API_KEY`, `OPENAI_API_KEY`, …; none for local
   servers) and which token-limit field the endpoint takes; each can be overridden
   (`OMEMO_BASE_URL__`, `OMEMO_API_KEY_ENV__`). The preset table is convenience, not authority: it
   was written from vendor documentation and has not been exercised with every vendor's key (see
   Consequences).
5. **An explicit all-roles default:** every `OMEMO_<NAME>__<ROLE>` variable may fall back to
   `OMEMO_<NAME>__DEFAULT`. This is **not** a silent default — the operator writes it, and a role
   with neither still fails closed (ADR-0016's rule stands: nothing in code guesses a model, a
   price or a budget). Pointing every role at Grok becomes eight lines.
6. **Per-family thinking grammar.** `anthropic` keeps `adaptive|disabled|budget:N|inherit`
   (ADR-0052). The family accepts `inherit` (send nothing — the default for models that decide for
   themselves, which is nearly all of them) or `effort:low|medium|high` (sent as `reasoning_effort`
   / `reasoning.effort`); an Anthropic-only word is refused with a message that says so, never
   degraded into inheriting.
7. **Keys:** the family's key is read **by name from the environment** the selection is given and
   passed to the client, which never prints it (not in `repr`, not in an error). The adapter sends
   it as `Authorization: Bearer`. A local server needs none.
8. **Retries:** a transient `429` / `500` / `502` / `503` / `504` or a dropped connection is retried
   twice (1 s, 3 s). A failed attempt returns no usage and is therefore not recorded as a call
   (ADR-0029 §3, which already deferred provider-side retries as "failed requests without a
   response"). Anything else is an `LLMError` at once.
9. **`doctor.py` knows it:** `core` now reports, for each provider the configuration actually
   selects, whether its key is set — no Anthropic requirement when no role selects Anthropic.

## Acceptance

| Row | Proves |
|---|---|
| OAI-01 | one forced call returns the requested fields; the request carries model, token limit, the function, the forced choice and the bearer key |
| OAI-02 | a granted Tool is run through the `Toolbox` mid-reasoning and its result is fed back; the final answer still arrives through `emit_fields` |
| OAI-03 | `json` mode asks for a JSON object and decodes it; with a Tool grant it loops until the model answers in text |
| OAI-04 | every completed turn is measured and priced from the explicit rates; a failure on turn 2 keeps turn 1's metrics |
| OAI-05 | HTTP errors, timeouts, bad JSON and a mixed final+operational answer are `LLMError`s; the key is in no message and not in `repr` |
| OAI-06 | `429`/`503` are retried twice and then succeed or fail; a `401` is not retried |
| OAI-07 | the `responses` dialect produces the same fields and runs the same Tool loop |
| OAI-08 | the Tool-call budget bounds the loop exactly as for Anthropic |
| PRV-01 | a preset resolves to its base URL and key variable; `openai-compatible` needs both explicitly |
| PRV-02 | `__DEFAULT` fills a role's missing values and a per-role value wins; a role with neither fails closed |
| PRV-03 | pricing, max tokens and thinking stay mandatory for the family; an Anthropic-only thinking word is refused by name |
| PRV-04 | a missing key variable is a selection error naming the variable (never its value) |
| PRV-05 | a keyless local preset (`ollama`) builds without any key |

## Consequences

- **Good:** Cursor + Grok (or anything else) runs the factory with no Anthropic account; changing a
  model is a `.env` edit; the core, the Run, the executors and every acceptance suite are untouched.
- **Not verified live:** the adapter is tested against a local server speaking each dialect, the way
  every vendor adapter here is (ADR-0040/0043/0067). **No provider's real answer was seen** — a
  vendor quirk (a field it rejects, a tool-choice it ignores) surfaces on the first real run as an
  `LLMError` naming the HTTP answer, and the fixes are the settings above (`OMEMO_STRUCTURED`,
  `OMEMO_WIRE`) before any code. The maintainer's first Grok run is the check, as `LIV` is for
  generation.
- **Not done:** image input for this family (the image-aware port of ADR-0080/0083 does not exist
  in code yet, so there is nothing to implement it against), streaming, provider-side prompt
  caching, and exact per-vendor rate presets (rates are never hardcoded — ADR-0029).
