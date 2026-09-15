# ADR-0037: Domain pivot — a business-agnostic content factory, uniqueness as value #1

- **Status:** Accepted
- **Date:** 2026-09-16
- **Deciders:** maintainer (product owner); Lead Architect
- **Type:** Charter revision. `PROJECT.md` 1.2 → 1.3 (Revision 3). No code contract changes.

## Context

`PROJECT.md` 1.2 declared the system to be "промышленная мультиагентная система производства
контента **для OMEMO Health**", with a customer field (`Заказчик: OMEMO Health`) and a health
domain woven into the principles themselves:

- value #1 was "Качество и достоверность" justified by "Контент в сфере здоровья имеет
  повышенную цену ошибки… отсутствие необоснованных **медицинских** утверждений";
- architectural principle §4 п.9 was literally "**Fail closed для домена здоровья**";
- §8 п.9, §13, §15 and the Stage-5 knowledge base all referenced health/medical guidelines;
- `ARCHITECTURE.md` §3.9 made QA responsible for "медицинский комплаенс домена здоровья", and
  `ROADMAP.md` Stage 8 inherited the same wording.

Two things forced a decision:

1. **The QA Agent could not be finished.** ADR-0035 had to ship `qa-agent` v1 on nothing but
   PROJECT.md §1's abstract principles, explicitly recording that it "still owes a review"
   because no document states concrete clinical rules — and no one could supply them, since
   inventing clinical criteria for a real health business is a product/legal act, not an
   architectural one.
2. **The premise was withdrawn.** On 2026-09-16 the maintainer stated that the factory will not
   be used for OMEMO Health and will not produce health content at all; it is a general-purpose
   content factory, and the quality property that actually matters is **uniqueness** — content
   that is not templated, not generic, and not a repeat of what the client or its competitors
   already published. The GitHub repository had already been renamed `concept`.

A charter whose stated purpose is a customer the project no longer serves is not a small
inaccuracy: `PROJECT.md` is the top of the documentation hierarchy (§17), every other document
and ADR is required not to contradict it, and every future session reads it as the source of
truth.

## Decision

### 1. The domain becomes the client's, not the core's

`PROJECT.md` 1.3 states the system is **not bound to an industry**: domain, brand, audience and
rules arrive as an input profile per client, and the core stays agnostic. The `Заказчик: OMEMO
Health` field is removed; the product is named **Concept Content Factory** in all three charters.

This is the charter catching up with what the architecture already did — ADR-0016's per-role
provider selection, ADR-0030's external Prompt store and the `BusinessContentProfile` sketch in
`CONTENT_FACTORY_THOUGHTS.md` §5 all assume per-client configuration, not a fixed vertical.

### 2. Value #1 becomes uniqueness and quality

"Качество и достоверность (домен здоровья)" is replaced by **"Уникальность и качество"**:
originality of angle, factual correctness, no unsubstantiated claims, conformance to the
client's editorial standards. Factual correctness is kept — it was never health-specific.

### 3. Fail closed keeps its force, loses its domain

§4 п.9 becomes **"Fail closed при сомнении"**: a risk signal — an unsupported claim, a breach of
the client's rules, insufficient uniqueness — stops the Run and escalates to a human. Regulated
niches (medicine, finance, law) are handled by the *client profile* carrying those rules, never
by the core assuming them. `ARCHITECTURE.md` §3.9 and `ROADMAP.md` Stage 8 are reworded to match.

### 4. §11 is aligned with the branching policy already in force

`PROJECT.md` §11 still demanded "Прямые пуши в неё запрещены. Работа через… Pull Request", which
`CONTRIBUTING.md` stopped requiring on 2026-09-15 (commit `420c50b`). The charter now records the
current rule: trunk-based with a green local gate as the condition for pushing; branch + PR
remains available for large or risky changes, not mandatory. This closes a live contradiction
between the top of the hierarchy and the practice below it.

### 5. What this ADR does **not** change

- **No code, contract, schema or domain-model change.** Nothing in `src/` depends on the domain
  wording; the Run/Task/Output/Artifact/Evaluation contracts are untouched.
- **`qa-agent` v1 is not edited.** A Prompt version is immutable (ADR-0030/0035): the health-specific
  v1 System Prompt stays in the catalogue and a **`qa-agent` v2** carries the new criteria
  (uniqueness, client rules) — queued as a follow-up, not done here.
- **Rin's and Leo's v1 Prompts are not edited** either, though their System text says "видео-фабрики
  OMEMO"; same v2 rule, same follow-up.
- **The Python package keeps the name `omemo_content_factory`.** Renaming it is a mechanical,
  repo-wide change with its own risk (imports, wheel, stored snapshots) and belongs in its own
  change, if it is worth doing at all.

## Consequences

### Positive

- The charter matches reality again; a fresh session reading `PROJECT.md` is no longer told it is
  building health content for a customer that is not there.
- Subtask 11.2's blocker dissolves: the QA Agent no longer owes clinical rules it could never get,
  and its v2 criteria (uniqueness, client rules) are things the maintainer can actually decide.
- Uniqueness becomes a first-class, stated requirement instead of an unwritten expectation — future
  QA/eval work has a charter line to trace to.

### Negative / Trade-offs

- `PROJECT.md` 1.2 called itself final ("дальнейшие изменения — только через ADR"); this is exactly
  that procedure, but it is still a charter rewrite rather than a local decision.
- Three Prompt versions (`qa-agent`, `content-researcher`, `script-writer`) now describe a domain
  the charter no longer claims — an accepted, tracked inconsistency until the v2 prompts land.
- Any future regulated-niche client needs its rules supplied through the profile; the core will
  not carry them, so that path is unproven until such a profile exists.

## Alternatives considered

- **Leave `PROJECT.md` alone and just ignore the health parts** — rejected: §17 makes documentation
  the source of truth over code, so a knowingly wrong charter poisons every later decision, and the
  QA Agent's criteria would stay permanently unresolvable.
- **Edit `qa-agent` v1 in place to drop the medical criteria** — rejected: Prompt versions are
  immutable and traceable (ADR-0030); the run that used v1 must stay reproducible.
- **Rename the Python package in the same change** — rejected as a big-bang mix of a charter
  revision with a mechanical refactor; separable, so separated.
- **Keep health as one supported vertical among several** — rejected: the maintainer stated the
  factory will not be used for health content, so carrying those rules would be dead weight in the
  core, contrary to §4 п.11.

## References

- `PROJECT.md` 1.3: §1 (purpose, values), §4 п.9 (fail closed), §8 п.9, §11 (branching), §12, §13,
  §15, §17 (hierarchy).
- `ARCHITECTURE.md`: §1, §2 (layer table), §3.9 (QA), §12 (agent invariants), §13 (Skills examples).
- `ROADMAP.md`: Stage 8 (QA Agent).
- ADR-0030 (immutable versioned Prompt store), ADR-0034/0035/0036 (QA verdict contract, role, evaluator).
- `CONTRIBUTING.md` "Branching" (2026-09-15); `CONTENT_FACTORY_THOUGHTS.md` §5 (client profile).
