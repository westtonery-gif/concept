# ADR-0016: Provider / Model Selection Ownership

- **Status:** Accepted (lightweight additive realization; no new entity, no new contract, no new principle)
- **Date:** 2026-07-12
- **Deciders:** Lead Architect / Domain Architect

> **Amended by ADR-0029 (2026-09-15).** A ready real-provider client now also requires explicit
> per-role token pricing. Provider/model selection ownership remains unchanged.
- **Type:** Light Capability — first realization of an already-mandated principle. No architecture change.

> Governance basis: this decision realizes a capability that `config.py` and the ROADMAP
> **deliberately deferred** ("no provider/adapter configuration; those belong to later ROADMAP
> stages"). `ARCHITECTURE_FREEZE.md` §0 requires any not-yet-realized capability be introduced
> through `ADR → SPEC/ACCEPTANCE → реализация`. This ADR records **only the architectural decision**;
> the mechanism, configuration shape and wiring are left to SPEC/implementation and are out of scope
> here.

## Context

The provider seam already exists in Content Factory:

- the provider-agnostic port `LLMClient` (`infrastructure/llm.py`; `ADR-0014` §2);
- the default adapter `AnthropicLLMClient`, which takes the model as a construction parameter;
- the Composition Root, which **receives** a ready `client` as a parameter and never selects or
  constructs it (`ADR-0012`: a dumb, decision-free build-time compiler).

What does **not** exist is the mechanism that *owns* the choice: there is no `role → model` binding
and no construction of an `LLMClient` from configuration. `config.py` today loads infrastructure-only
settings and **explicitly excludes** provider/model configuration. Consequently the **caller** (the
outer system that drives Content Factory) currently reads the model from its own environment,
constructs the concrete `LLMClient`, and injects it into the Composition Root. The selection of the
production model therefore lives **above** the factory boundary, even though the factory owns the
provider seam.

## Why existing principles are insufficient

The **principle** is already fixed and is not in question:

- `PROJECT.md` §5 — the model is chosen **through configuration**; the `role → model` binding lives
  in configuration; any model is swappable without touching business logic; the provider sits behind
  an adapter. This is stated as an **invariant**, not a preference.
- `PROJECT.md` §4 (п.7 Provider Abstraction, п.8 cost-per-role, п.11 extension by module) and
  `ARCHITECTURE.md` §16 ("new model/provider → change the `role → model` configuration and/or the LLM
  Adapter; do **not** change the core").

These establish *that* provider/model selection is a configuration concern owned inside the factory.
They do **not** authorize **realizing** the deferred mechanism in `src`: `config.py`/ROADMAP defer it,
and `ARCHITECTURE_FREEZE.md` §0 requires an ADR to cross from "deferred" to "implemented". Existing
ADRs give the principle; none authorizes its first realization. Hence a (lightweight) ADR is the
minimal governance step.

## Decision

**Ownership of provider/model selection (`role → model`) becomes a Content Factory responsibility**,
realizing `PROJECT.md` §5 and `ARCHITECTURE.md` §16 for the first time. The production model is no
longer selected above the factory boundary. This ADR fixes **ownership of selection only** — it
introduces no new principle and prescribes no mechanism, wiring, configuration format, or code.

## Why this is a Light Capability, not an architecture change

- It **implements** an existing invariant (`PROJECT.md` §5); it introduces **no new principle**.
- Provider/model is **infrastructure/configuration**, not a domain concept — it adds **no new domain
  entity** and touches none (Run / Task / Output / Artifact / Schema / Human Review / Workflow /
  Agent / Prompt are untouched).
- The existing seams are reused unchanged: the `LLMClient` **port** and the Composition Root's
  `client` **parameter** stay exactly as they are — **no new contract**.
- It only moves the *location* of an already-existing responsibility — **selecting** the provider/
  model — from above the boundary **into** the factory, where the principle says it belongs.

## What principally does NOT change

- **Domain model** — unchanged. Provider/model lives in infrastructure/config; no domain aggregate,
  field, or state is added or modified.
- **Contracts** — unchanged. `LLMClient.complete(...)`, the Composition Root signature that receives a
  `client`, and the Schema/Output contracts stay as-is.
- **Composition Root** — unchanged in role. It remains the dumb, decision-free build-time compiler
  (`ADR-0012`) and gains **no** selection logic.
- **Runtime / execution topology** — unchanged (`ADR-0013`). No change to how Tasks run or Output is
  validated and recorded.
- **Workflow** — unchanged (`ADR-0009`). Declaration and ordering are untouched.
- **Main Core ↔ Content Factory boundary** — unchanged in **direction** (caller → factory) and in the
  **plain-data** it carries (`brief` + context ↓, `FactoryResult` ↑). The data contract is untouched.

## Invariants preserved

- Provider independence: any model/provider remains swappable behind the `LLMClient` port without
  touching agents, prompts, schemas, orchestration, or the domain (`PROJECT.md` §5, §4 п.7).
- Configuration-driven selection: model choice stays a configuration concern, never hardcoded in
  agent/executor logic (`PROJECT.md` §5).
- Composition Root stays a pure build-time compiler with no runtime/policy decisions (`ADR-0012`).
- Schema remains the sole authority over the execution contract (`ADR-0008`); Output finalization
  path is unchanged (`ADR-0013` §8).
- Dependency direction and boundary contract (plain data, no CF domain types crossing) are unchanged.

## Out of scope (this ADR opens no door to scope creep)

- Any configuration format, schema, or key naming for the `role → model` binding.
- Any client factory design, mechanism, or code.
- Adding, changing, or retiring any LLM provider/adapter.
- Cost/latency metrics or model-cost accounting (`PROJECT.md` §16 — a separate concern).
- Any change to agents, prompts, schemas, workflow, runtime, or the boundary.
- Migration of any further agent, QA/Evaluation, Tools/Adapters, or media production.

## Self-check (classification consistency with Discovery and Governance)

- Did any architectural principle change? **No** — this realizes `PROJECT.md` §5.
- Did a new domain object appear? **No** — provider/model is infrastructure/config.
- Did a new contract appear? **No** — the `LLMClient` port and existing composition contracts are unchanged.
- Did a new lifecycle appear? **No** — a config/client factory has no aggregate lifecycle.
- Did the Slice's blast radius change? **No** — it stays small and localized to provider/model
  selection (configuration), as assessed in Discovery.

All five answers are **No** → the **Light Capability** classification holds, and this ADR is
consistent with the Discovery and Governance conclusions.

## Consequences

- **Positive:** `PROJECT.md` §5 becomes reality; provider/model selection is now owned inside the
  factory, end-to-end. Future agents reuse this without re-introducing selection above the boundary.
- **Cost/debt:** a deliberately-deferred capability now enters `src` and must follow through
  `SPEC/ACCEPTANCE → реализация`; until then the caller-side selection remains as a marked transitional
  shim.
- **Nothing else changes:** no domain, no contract, no Composition Root role, no runtime, no workflow,
  no boundary — by construction (see "What principally does NOT change").

> Source of truth for production architecture remains the Content Factory documents
> (`PROJECT.md` §17). This ADR is subordinate to them and only records the ownership decision.
