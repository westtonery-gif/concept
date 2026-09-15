# ADR-0014: Structured Output Port and Generation Shape

- **Status:** Accepted (formalises the agreed slice contract; no new entity, no new responsibility)
- **Date:** 2026-06-29
- **Deciders:** Lead Architect / Domain Architect

> **Amended by ADR-0028 (2026-09-15).** The port still returns the same opaque structured mapping,
> but one `complete` call may now contain a bounded multi-turn Tool loop and receives a scoped
> `Toolbox`. The Schema/field invariants in this ADR are unchanged.

## Context

After Slice 3 (evaluation-ownership **Variant A**, `ADR-0013` §8) the validated finalization path
is the **only** way an Output is recorded: the Application invokes `Schema.validate(payload_fields)`
(the Schema authority decides VALID/INVALID) and `Run.record_output` persists the verdict as a pure
sink. `Schema.validate` consumes a `Mapping[str, str]` of named fields and checks that the required
fields are present and non-empty.

The single real `TaskExecutor`, `LLMTaskExecutor`, currently calls a **text-only** LLM port
(`LLMClient.complete(*, system, user) -> str`) and returns an `ExecutionResult` carrying `output`
(free text) and `schema_ref` but **no `payload_fields`**. Because the validated path requires
`payload_fields`, the real LLM branch reaches `COMPLETED` without ever recording an Output or
Artifact — the acknowledged gap (ARCHITECTURE_FREEZE §3, "LLM structured output"). After the move to
validated-only Output, a text-only port is structurally insufficient: there is no provider-agnostic
way for a free-text completion to yield the named-field surface the Schema authority must validate.

A second, smaller gap is recorded by `ADR-0011` §5 and `ADR-0012`: `Prompt.user_template` exists as
immutable data but is **not** delivered into execution (the Composition Root injects only
`Prompt.system` and `Prompt.schema_ref`; the executor uses the raw Task input as the user message).

Constraints already fixed by accepted ADRs, which this ADR must not breach:

- **`ADR-0005`** — `Output` is an immutable, terminal record; `Run.record_output` records `payload`
  as a string and is (post-Slice-3) a pure sink taking a verdict. This ADR changes neither.
- **`ADR-0008`** — Schema is the **sole authority** over the execution contract; validation is
  deterministic and admissible only against an `ACTIVE` version; the field/contract definition is
  owned by Schema, nowhere else.
- **`ADR-0011`** — Agent is a passive descriptor and Prompt an immutable artifact carrying `system`,
  `user_template` and an opaque `schema_ref` that only *references* the authoritative Schema; the
  Prompt defines no contract.
- **`ADR-0012`** — the Composition Root is a strict **dumb** build-time wiring layer: deterministic,
  uniform, decision-free construction of the runtime graph from the static contracts; it resolves
  `agent_ref → Prompt`, assembles the execution context (executors injected with resolved data), and
  interprets nothing.
- **`ADR-0013`** — single runtime entry through the Content Director; Run owns its state machine;
  evaluation-ownership is **Variant A** (Schema decides → Application invokes → Run persists).

This ADR **formalises the already-agreed contract** that closes both gaps. It introduces no new
entity and no new responsibility, and it alters no existing ADR.

## Decision

### 1. `LLMClient` becomes a structured port (single method)

The provider-agnostic LLM port is **uniformly structured**. Conceptually:

```
complete(*, system: str, user: str, fields: Sequence[str]) -> Mapping[str, str]
```

It accepts the role's system and user text plus a list of field names, and promises a mapping
`name → value`. The previous text-only `complete(*, system, user) -> str` is **replaced** — there is
no legacy or secondary method alongside it. Free text is the degenerate case of a single requested
field. The provider mechanism by which structure is elicited (tool/function schema, JSON response
format, grammar, …) is hidden inside the adapter and is **not** named by the port. This is a richer
parameterisation of the port's one existing responsibility (a single model completion), not a new
responsibility.

### 2. `fields` is form transport, not knowledge of Schema (Variant B)

`fields` is an **opaque** `Sequence[str]` handed to the port. The port does not know the names'
origin, does not know whether they are required, knows no validation rule, and knows nothing of
Schema or the domain. Its promise is purely mechanical: produce a `name → value` mapping for the
given names. It guarantees neither completeness nor exclusivity of keys; a partial or empty result
is **not** a port error — it is input for the Schema verdict downstream. The denied knowledge is
also **unnecessary** for the port's function, so Variant B is a precise boundary, not a limitation.

This is enforced at the type level: the port signature mentions **no domain type** (`Sequence[str]`
in, `Mapping[str, str]` out), and `infrastructure` imports no `domain`.

### 3. The generation shape is projected from Schema in the Composition Root

The field list given to an executor is the **generation shape**, projected at build time from the
already-resolved Schema: `agent_ref → Prompt → schema_ref → Schema → required_fields`. This falls
within the Composition Root's existing "assemble the execution context" responsibility (`ADR-0012`
§1) and is a **dumb structural projection**: it is deterministic, uniform across all agents (no
per-`agent_ref` branching), decision-free, and reads a public read-only attribute (`required_fields`,
already exposed via `SchemaView`). It is the **same kind** of operation `ADR-0012` already sanctions
for `Prompt.system` / `Prompt.schema_ref` injection — reading an attribute of a resolved static
contract and injecting it as executor construction input. The Composition Root **transports the
shape; it never applies the rule** — `Schema.validate` remains the sole evaluator. The generation
shape is therefore a **mandatory part of a structured executor's configuration**: with no resolvable
Schema there is no shape, and — because the port is structured-only (§4) — there is no valid
executor to construct. The shape is resolved at build time in the Composition Root (above), so its
absence surfaces as a build-time failure, not a runtime one. *(Errata: an earlier draft stated a
"no schemas ⇒ no Output" symmetry; that described the pre-`ADR-0014` text port and is inconsistent
with the structured-only port. Corrected here without changing any decision.)*

The same wiring delivers `Prompt.user_template` into the executor, closing the `ADR-0011` §5 /
`ADR-0012` deferral. This is delivery of existing data, not a new decision.

### 4. The executor only forwards the shape and the result

`LLMTaskExecutor` renders the user message from `Prompt.user_template` and the Task input (per the
rendering contract, §7), calls the structured port with the injected generation shape, and assembles
the `ExecutionResult`: the returned mapping becomes `payload_fields` **verbatim**, and `payload` is a
deterministic serialization of those same fields (§7). The executor makes no contract decision and
ascribes no meaning to the names — it is transport plus assembly, within the executor's existing
execution responsibility, not a new one.

### 5. `payload_fields` is the structured result surface

`ExecutionResult.payload_fields` (already present, previously data-only) becomes the canonical
structured representation of the result and the input to `Schema.validate`. The `ExecutionResult` DTO shape is
**unchanged**; only its population changes.

### 6. `payload` stays a string as a slice-level compatibility adapter

`Output.payload` remains `str` and `Run.record_output` keeps its `payload`-as-string signature. This
is a **deliberate decision to preserve the existing domain contract** (`ADR-0005`; the "Run
unchanged" invariant of `ADR-0013`) — **not** an assertion that a flat string is the preferred
storage model for structured content. `payload_fields` are **not** persisted; they are transient
validation input. The serialization is therefore a **slice-level compatibility adapter** between the
structured result surface and the unchanged Output record. If a future consumer needs persisted
field-level structure, a first-class structured representation is introduced by its **own** ADR,
under a real consumer — explicitly deferred here, never endorsed.

### 7. Template rendering and serialization are separate, mechanism-free contracts

**Template rendering** — `render(user_template, task_input) -> user_text` — is a pure deterministic
function with a single dynamic input (the Task input); it is content-opaque (the input is placed,
not parsed or transformed), input-optional, bounded, and either total or surfaced as a managed
`FAILED` `ExecutionResult` (never an exception escaping the port, never a silent or partial result).
The substitution syntax is an executor/Prompt convention and is **not** frozen by this ADR.

**Serialization** — `payload_fields -> payload` — is deterministic, faithful (it represents exactly
the validated fields), and unambiguous. The concrete format is **not** frozen by this ADR.

`payload_fields` is the canonical structured representation of a result; `payload` is a derived
representation produced solely for compatibility and storage. The mapping is strictly one-directional
(`payload_fields → payload`): no logic reconstructs `payload_fields` from `payload`, and no runtime
behaviour depends on parsing `payload`. Consequently, the serialization algorithm may evolve without
changing the semantics of the result or affecting `Schema.validate`, which operates exclusively on
`payload_fields`.

Naming these as contracts (not mechanisms) lets the implementation evolve without reopening this
decision.

### 8. Invariants carried by this slice

These restate, for this slice, rules already in force; no new rule is introduced.

- **I1 — Schema remains the authority.** The generation shape is a build-time derivation of Schema;
  it is a generation hint, never a parallel contract. The single source of truth for the contract is
  Schema, and the VALID/INVALID verdict comes only from `Schema.validate`. Even if the model returns
  wrong or missing fields, Schema decides.
- **I2 — The executor is structure-transparent.** Any `TaskExecutor` forwards `payload_fields`
  verbatim and deterministically serializes them into `payload`; it must not inspect, add, drop,
  rename, reorder, coerce, normalize, default, repair, branch on, re-request, or pre-validate fields.
  `ExecutionResult.succeeded` reflects call success, **not** structural validity (distinct axes); a
  successful call with missing fields is forwarded and ruled INVALID by Schema.
- **I3 — The port is structure-opaque (Variant B).** `LLMClient` takes opaque names and promises a
  `name → value` mapping; it knows no origin, requiredness, rule, or Schema, and guarantees neither
  completeness nor exclusivity. Type-level guarantee: the port signature carries no domain type, and
  `infrastructure` imports no `domain`.

Canonical chain: **Schema defines → Composition Root projects the shape → Executor forwards →
LLMClient produces values → `Schema.validate` is the sole judge of correctness.**

## Consequences

### Positive
- Closes the LLM-path gap: the real model branch now yields `payload_fields`, so the validated path
  records an Output (and an Artifact) end to end.
- Preserves **Variant A** unchanged: Schema decides, the Application invokes, Run persists
  (`Run.record_output` stays a pure sink; no Run / Task / Output / Schema signature changes).
- Keeps infrastructure independent of the domain: the structured port carries only `Sequence[str]` /
  `Mapping[str, str]`; `infrastructure` imports no `domain` type.
- Preserves provider independence: the structuring mechanism is hidden behind the port; swapping
  providers touches neither the Application, the Composition Root, nor the domain.
- Delivers `Prompt.user_template` into execution without adding orchestration: rendering is an
  infra-local string transformation; the Content Director and `execute_task` are unchanged and
  unaware of fields or templates.

### Negative / Trade-offs
- Replacing the port method is a breaking change to `LLMClient` (one production implementation, one
  caller, the test fakes); accepted because it avoids a permanent bimodal/legacy port.
- `payload` carries structured content as a serialized string (a slice-level adapter), so field-level
  access to a persisted Output is unavailable until a future ADR introduces structured persistence.
- The generation shape restates Schema's required-field names at build time; bounded because the
  shape is *derived from* Schema (single source of truth), not authored separately, and is never the
  validation authority.
- The Composition Root reads one further attribute (`required_fields`); mitigated by §3 — a dumb
  structural projection of the same kind already performed for Prompt attributes, within its existing
  responsibility.

## Alternatives considered
- **Keep a text-only port; obtain structure via prompt engineering + parsing in the executor.**
  Rejected: it relocates the provider's native structured-output capability into hand-rolled prose
  and fragile text parsing, makes correctness depend on the model obeying prose, leaks per-provider
  encoding into the executor, and re-creates a structural seam with parsing risk.
- **Keep `fields` only inside the executor (text port unchanged).** Rejected: the executor would own
  and encode a *contract* concept whose authority is Schema, and would have to parse and decide field
  values — colliding with the structure-transparent invariant (I2) and turning the executor into a
  shadow validator.
- **Store the structure directly in `Output`.** Rejected: it would change `Output` (`ADR-0005`) and
  the `Run.record_output` signature, violating the "Run unchanged" invariant (`ADR-0013`), and would
  make the Output record Schema-shape-aware — leaking the authority's concern into the domain record.
  Deferred to a future ADR under a real consumer.
- **Carry the generation shape in `Prompt`.** Rejected: it would create a second source of field
  names beside Schema, risking drift, and would push contract definition into Prompt, which
  `ADR-0011` keeps a pure reference to Schema, not a contract definition.

## References
- `ADR-0005` — Output entity (immutable record; `payload` string).
- `ADR-0008` — Schema entity (sole contract authority; deterministic validation).
- `ADR-0011` — Agent + Prompt (passive descriptor / immutable artifact; deferred `user_template`
  delivery).
- `ADR-0012` — Composition Root (dumb build-time wiring; assemble-execution-context responsibility).
- `ADR-0013` — Execution Topology + Authority (single entry; Variant A).
