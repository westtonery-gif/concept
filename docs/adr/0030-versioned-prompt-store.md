# ADR-0030: Versioned Prompt Store at the Composition Boundary

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 7 / M2 requires Prompt to be stored separately from code. Rin and Leo currently own
their System/User text as Python literals in `agents/*.py`, although `ADR-0011` defines Prompt as a
versioned immutable artifact and `ADR-0012` assigns delivery of its text to the Composition Root.
The domain object and execution wiring already work; the missing slice is a separate source of
truth for production text.

The Root must remain a deterministic build-time compiler. `ContentDirector`, executors and Agent
descriptors must not gain file lookup or activation policy.

## Decision

1. Production Prompt definitions move to the checked-in package resource
   `omemo_content_factory/prompts/catalogue.toml`. Each record carries all existing `Prompt` data,
   including its explicit positive integer version.
2. `composition.load_prompt_catalogue()` reads and strictly validates that resource, then creates a
   fresh `prompt_id -> Prompt` mapping. Invalid or unreadable input fails before runtime with
   `CompositionError`; no partial catalogue escapes.
3. Composition build functions accept their existing explicit mapping or `None`. `None` means
   “read the built-in store”; explicit mappings remain available for isolated tests and embedding.
   A top-level build resolves the catalogue once and shares that immutable snapshot across its
   executor and schema projections.
4. The store has one active record per logical `prompt_id`. Runtime history/activation, choosing a
   latest version, remote storage and hot reload stay deferred. Version changes remain explicit.
5. Role modules retain Agent, Schema and Skill/Tool declarations only. They neither construct
   `Prompt` nor read the store.

This is build-time configuration parsing, not orchestration or a policy decision. Validation is
uniform structural fail-closed checking, analogous to the Root's existing reference checks.

## Consequences

- Prompt copy can change independently of Python role code and is reviewable as versioned data.
- The exact prompt version used for analytics still comes from the materialized `Prompt`.
- The bundled catalogue is available from an installed wheel through `importlib.resources`.
- A malformed shipped catalogue prevents runtime construction instead of failing during a model
  call.
- This slice intentionally does not introduce a Prompt Adapter or mutable Prompt service.

## Alternatives considered

- **Python registry.** Rejected: it moves literals between modules but does not store Prompt text
  separately from code.
- **Read files inside Agent modules.** Rejected: Agent would acquire configuration behaviour and
  violate `ADR-0011`.
- **Read files inside the executor.** Rejected: failure would move to runtime and the executor would
  gain lookup responsibility.
- **Choose the highest discovered version.** Rejected: implicit activation is policy and makes a
  version switch accidental.
- **Remote Prompt CMS now.** Rejected: no current consumer needs network mutation or hot reload;
  it would introduce an Adapter and operational surface beyond Stage 7.5.

## References

- `PROJECT.md` §15, §17
- `ARCHITECTURE.md` §6, §10
- `ROADMAP.md` Stage 7 / Milestone M2
- `AGENT_SPEC.md` §4–§5
- `PROMPT_STORE_SPEC.md`; `PROMPT_STORE_ACCEPTANCE.md`
- `ADR-0011`, `ADR-0012`, `ADR-0014`, `ADR-0029`
