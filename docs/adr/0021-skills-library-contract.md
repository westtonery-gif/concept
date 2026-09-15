# ADR-0021: Skills library — the `Skill` contract and the first three Skills

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect

## Context

ROADMAP Stage 4 lays down the `Skills` layer: a library of reusable one-task modules that agents
compose (`PROJECT.md` §14, `ARCHITECTURE.md` §7). Stage 4's DoD asks for:
- isolated Skills with an explicit, typed input/output contract, unit-tested without agents or the
  orchestrator;
- no Skill that references an agent or changes `Run` state.

The docs fix the constraints:
- a Skill is a **deterministic internal module** called from agent/system code, while a Tool is
  called by the *model* during reasoning. Skill ≠ Tool, and Skill ≠ Agent (`PROJECT.md` §18);
- a Skill knows no agent, doesn't know its caller and doesn't steer the pipeline (`PROJECT.md` §14);
- a Skill is a standalone definition root that owns its versions. Its mandatory attributes are id,
  name, purpose, input contract and output contract, and its states are `Active`/`Deprecated`
  (`DOMAIN_MODEL.md` §2.6, §9.5);
- in the layering, Skills sit next to Agents, above Adapters and the domain (`ARCHITECTURE.md` §15).

No agent consumes a Skill yet. The two migrated roles (Rin, Leo) are passive Agent + Prompt +
Schema data, and the LLM does their work. So the contract is designed ahead of its first consumer
(Stage 7). It is kept to what the DoD needs.

## Decision

### 1. Two places, mirroring Agent
- **`domain/skill.py`** holds the passive catalogue descriptor and the Skill errors, the way
  `domain/agent.py` holds the Agent descriptor.
- **`skills/`** is a new library package (flat, one module per Skill, like `agents/`). It holds the
  executable contract (`skills/contract.py`), the Skills themselves and `skills/catalogue.py`.

### 2. The descriptor — identity, not behaviour
`SkillDescriptor(skill_id, version: SkillVersion, name, purpose)` is a frozen dataclass, validated
at construction (`InvalidSkillDescriptorError`):
- `skill_id` is non-blank, with no whitespace and no `@`;
- `name` and `purpose` are non-blank;
- `SkillVersion.value` is an `int >= 1` (a `bool` is refused).

The derived `ref` is `<skill_id>@v<n>`, the same shape as an `agent_ref`, and is what a consumer
will reference. The input/output contract is the Skill's typed `apply` signature, not a descriptor
field: a string description of a type would be a second, unchecked copy of it.

### 3. The executable contract — `Skill[InT, OutT]`
`Skill` is a structural `typing.Protocol` with two members:
- `descriptor -> SkillDescriptor`;
- `apply(skill_input: InT, /) -> OutT`.

A Protocol means there is no base class to inherit, so no shared base class is extracted
prematurely. Every Skill obeys these rules, and the tests check them:
- **Typed and immutable I/O.** The input is a frozen dataclass validated at construction: a
  violation raises `InvalidSkillInputError` before `apply` runs. The output is a frozen dataclass.
- **Pure.** The same input gives an equal output. There is no clock, randomness, I/O, network,
  model call, or state carried between calls, and the Skill classes are stateless
  (`__slots__ = ()`).
- **Blind to its caller.** `apply` takes only its input: no `Run`, `Task`, agent, actor or context.
  A Skill cannot know who called it, and it has nothing it could use to change the pipeline.
- **Import boundary.** Library modules import only pure stdlib, `domain.skill` and each other. A
  source scan in `tests/test_skill_contract.py` enforces this. It is the falsifiable form of "no
  Skill references agents or changes `Run`" (Stage 4 DoD).

### 4. The first three Skills (all from ROADMAP Stage 4's own examples)
| Ref | One task | Notes |
|---|---|---|
| `segment_text@v1` | split a text into segments of at most `max_chars` | paragraphs are never merged; whole sentences are packed greedily; an over-long sentence is packed by words, an over-long word is cut. Every non-whitespace character is kept in order. |
| `normalize_terminology@v1` | replace glossary variants with canonical terms | the caller supplies the glossary, so no vocabulary is hardcoded. Matching is whole-word, case- and whitespace-insensitive, in one pass with the longest variant first. Returns an audit of the replacements. |
| `check_required_elements@v1` | report which required elements a text contains | an element is satisfied by any of its phrases, matched the same way. Only **reports** present/missing elements. An empty requirement set is refused, so "nothing checked" never reads as "complete". |

Thesis extraction from a brief, the fourth ROADMAP example, was not taken: done deterministically it
would be a weak heuristic, and done well it is LLM work, i.e. a role and not a Skill. The two phrase
Skills share a private helper (`skills/_phrase.py`), a function, not a base class.

### 5. Errors
`SkillDomainError(DomainError)` (ADR-0017) with `InvalidSkillDescriptorError` and
`InvalidSkillInputError`, co-located in `domain/skill.py`. Immutability comes from frozen
dataclasses, so there is no dedicated error for it.

### 6. Catalogue
`skills/catalogue.py` lists the descriptors (`SKILL_DESCRIPTORS`, `SKILLS_BY_REF`), with refs
unique. It is passive data. Nothing resolves a Skill by ref at runtime: an agent uses a Skill by
importing it, because a Skill is code called by code, not a capability handed to a model.

### 7. What does not change
`Run`, `Task`, `Agent`, `Prompt`, `Workflow`, the application layer, the Composition Root and the
infrastructure are all untouched.

## Deferred
- **Agent → Skill references.** The Agent's `skill_refs` (`DOMAIN_MODEL.md` §2.5 "набор
  используемых Skills") arrive with the first agent that actually calls a Skill (ROADMAP Stage 7),
  as an additive change to the Agent descriptor.
- **Skill lifecycle** (`Active`/`Deprecated`). As with Agent, there is no status until something
  consumes it. A deprecated version would stay in the catalogue with a status field.
- **Skills that depend on Tools or Adapters** (`ARCHITECTURE.md` §3.6 allows it). None of the first
  Skills needs one. This comes after Stage 5 and 6, and the import boundary will then be widened
  explicitly, by ADR.
- Thesis extraction, structuring by template, readability and tone, citation normalisation: the
  other examples in `ARCHITECTURE.md` §7. Each one is a new module when a consumer needs it.

## Consequences

### Positive
- Stage 4's DoD holds and is checked by tests: typed contracts, isolation, determinism, and no path
  from a Skill to an agent or a `Run`.
- Stage 7 agents get three ready building blocks. Adding a fourth Skill is one module plus one
  catalogue line.
- `check_required_elements` gives QA (Stage 8) a deterministic, fail-closed-friendly primitive for
  mandatory health disclaimers.

### Negative / Trade-offs
- The contract is designed before its first consumer. Stage 7 may add to it, additively, by ADR.
- The descriptor carries no status yet, and the catalogue is a hand-maintained tuple.
- Sentence splitting is punctuation-based (`.`, `!`, `?`, `…`). Abbreviations such as "т. е." can
  cut a sentence early. The limit and the character preservation still hold.

## Alternatives considered
- **An abstract base class `Skill`.** Rejected: it would be a shared base with no shared behaviour.
  A Protocol gives the same static check without inheritance.
- **Put the executable contract in `domain/`.** Rejected: Skills are a layer next to Agents
  (`ARCHITECTURE.md` §15). The domain keeps only the passive descriptor, as it does for Agent.
- **A runtime registry that resolves Skills by ref.** Rejected: that is Tool-shaped machinery
  (Stage 5). Skills are imported directly, and the catalogue stays data.
- **LLM-backed Skills (e.g. thesis extraction now).** Rejected: this contradicts "Skill is a
  deterministic internal module" (`PROJECT.md` §18). Model work belongs to agents.
- **An `input_schema` / `output_schema` field on the descriptor.** Rejected: it duplicates the
  typed signature and nothing reads it yet.

## References
- `PROJECT.md`: §4 п.11, §14, §18
- `ARCHITECTURE.md`: §3.6, §7, §15
- `DOMAIN_MODEL.md`: §2.5, §2.6, §6, §9.5, §11
- `ROADMAP.md`: Stage 4 (and Stages 5, 7, 8 for the deferred items)
- ADR-0010 / ADR-0011 (passive descriptor precedent), ADR-0017 (`DomainError`)
- `SKILL_SPEC.md`, `SKILL_ACCEPTANCE.md`
