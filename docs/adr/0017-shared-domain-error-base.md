# ADR-0017: Shared `DomainError` base for the per-aggregate error hierarchies

- **Status:** Accepted
- **Date:** 2026-09-15
- **Deciders:** Lead Architect / Domain Architect
- **Type:** Technical-debt follow-up (rule of three). No new entity, contract, lifecycle or principle.

## Context

Every domain aggregate carries its own error hierarchy, each rooted directly at `Exception`:
`RunDomainError` (ADR-0003 §8), `TaskDomainError` (ADR-0004 §8), `OutputDomainError`
(ADR-0005 §8), `ArtifactDomainError` (ADR-0006 §9), `HumanReviewDomainError` (ADR-0007),
`SchemaDomainError` (ADR-0008 §11) and `WorkflowDomainError` (ADR-0009 §9).

ADR-0004 §8 deferred a shared base until a third hierarchy existed ("rule of three"). ADR-0005 §9
recorded that the third had arrived but kept the extraction out of the Output change and **flagged
it as a separate follow-up with its own ADR**; ADR-0006/0007/0008/0009/0011 repeated the deferral.
There are now **seven** structurally identical hierarchies, far past the threshold.

The consequence of not extracting it: a caller that must distinguish "a domain rule was violated"
from a technical failure (`LLMError`, `CompositionError`, `ProviderModelSelectionError`) has to
enumerate seven unrelated base classes, and every new aggregate (Evaluation/QA, Analytics Record —
next in the queue) would add an eighth, ninth… root that such callers silently miss.

## Decision

We introduce one common base, `DomainError(Exception)`, in a new module
`omemo_content_factory.domain.errors`, and re-parent each existing per-aggregate base onto it:

```
Exception
└── DomainError                       (domain.errors — new, ADR-0017)
    ├── RunDomainError                (domain.run)
    ├── TaskDomainError               (domain.task)
    ├── OutputDomainError             (domain.output)
    ├── ArtifactDomainError           (domain.artifact)
    ├── HumanReviewDomainError        (domain.human_review)
    ├── SchemaDomainError             (domain.schema)
    └── WorkflowDomainError           (domain.workflow)
```

1. **Marker only.** `DomainError` has no attributes, no methods, no constructor override — it adds
   classification, never behaviour.
2. **Additive only.** Every per-aggregate base and every concrete error keeps its name, module,
   docstring, message and raise sites. Each remains an `Exception`, so every existing
   `except XDomainError` / `pytest.raises(...)` keeps working unchanged. Run's public contract
   (ADR-0003) is extended, not changed: `RunDomainError` gains an ancestor, nothing else.
3. **No cycle, inward dependencies.** `domain.errors` imports nothing; domain modules import it.
   The domain layer still depends on nothing outside itself (ARCHITECTURE_FREEZE.md §2 п.10).
4. **Technical failures stay outside.** `LLMError` (infrastructure), `CompositionError`
   (composition root) and `ProviderModelSelectionError` (infrastructure) are **not** domain errors
   and are **not** re-parented. The split "domain rule vs technical failure" (ADR-0003 §8) is
   exactly what the new base makes checkable.
5. **Every future aggregate** roots its own base at `DomainError` (e.g. the upcoming
   Evaluation/QA entity).

## Consequences

### Positive

- One `except DomainError` covers every domain-rule violation, current and future.
- The rule-of-three follow-up flagged in ADR-0004 §8 / ADR-0005 §9 is closed; the stale
  "not extracted yet" comments in the domain modules are corrected.
- A test pins the invariant "every domain error is a `DomainError`, no technical error is".

### Negative / Trade-offs

- Seven domain modules gain one import each (mechanical, no behaviour change).
- Shared base classes for **events** and **aggregates** are *not* extracted (see below); the
  hierarchy is unified for errors only.

## Alternatives considered

- **Collapse the per-aggregate bases into `DomainError`** — rejected: breaks existing
  `except XDomainError` callers and tests and loses per-aggregate classification; it would change
  Run's contract rather than extend it.
- **Put `DomainError` in `domain/run.py`** — rejected: every other domain module would then have
  to import `run`, which already imports them (import cycle), and it would make Run the owner of a
  cross-aggregate concept.
- **Put it in `domain/__init__.py`** — rejected: turns the package initialiser into a dependency of
  every submodule; a dedicated leaf module is simpler and explicit.
- **Also extract a shared `DomainEvent` / aggregate base now** — rejected: out of scope. Events
  are `frozen` dataclasses with differing identity fields; unifying them is a separate decision
  with its own trade-offs.
- **Keep deferring** — rejected: seven hierarchies, and the next entities in the queue would widen
  the gap.

## Out of scope

- A shared `DomainEvent` base or aggregate base class.
- Any change to error names, messages, raise sites or which error a rule raises.
- Re-parenting technical errors (`LLMError`, `CompositionError`, `ProviderModelSelectionError`).
- Any new catch site: no application code is changed to catch `DomainError`.

## References

- `PROJECT.md` §4 п.11 (extension by adding modules), §17 (docs are the source of truth).
- `ARCHITECTURE_FREEZE.md` §2 п.10 (dependencies point inward).
- ADR-0003 §8, ADR-0004 §8, ADR-0005 §8–9, ADR-0006 §9, ADR-0007, ADR-0008 §11, ADR-0009 §9.
- `ROADMAP.md` Stage 2 (domain contracts).
