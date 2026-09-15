# ADR-0035: The QA Agent role definition — `qa_agent@v1` with a baseline, review-pending Prompt

- **Status:** Accepted
- **Date:** 2026-09-16
- **Deciders:** Lead Architect / Domain Architect; maintainer (compliance criteria scope)

## Context

ROADMAP Stage 8 needs a real QA role behind the existing QA port (`CLAUDE.md` queue 11.2). The
port (`ArtifactEvaluator`, ADR-0018 §6) and the verdict field contract (`decode_verdict`,
`QA_VERDICT_FIELDS = ("verdict", "flags")`, ADR-0034) exist; what is missing is the role's static
assets — the same triple Rin and Leo have: an `Agent` descriptor, an output `Schema` and a versioned
`Prompt` in the bundled store (ADR-0010/0011/0008/0030).

Constraints:

- **Domain content is not the architect's to invent.** The System Prompt must carry the health
  domain's quality and compliance criteria (PROJECT.md §15, ARCHITECTURE.md §3.9), but no document
  states concrete clinical rules. PROJECT.md §1 states only the principles: factual correctness, no
  unsubstantiated medical claims, conformance to editorial standards.
- **No evaluator exists yet.** The QA path does not run through a `TaskExecutor` (ADR-0034 Context),
  so there is nothing that would execute this role before subtask 11.3 (`LLMArtifactEvaluator`).
- **Skill invocation has no QA seam.** ADR-0027 wraps a `TaskExecutor`'s input; an evaluator has no
  equivalent boundary, and the exact disclaimer wording `check_required_elements@v1` would look for
  is domain content too.

## Decision

### 1. One new role module of existing types

`agents/qa_agent.py` defines, in the shape of `script_writer.py`:

- `AGENT_REF = "qa_agent@v1"`, `PROMPT_REF = "qa-agent"`, `SCHEMA_REF = "qa-verdict@v1"`;
- `QA_VERDICT_SCHEMA` — `schema_id="qa-verdict"`, version 1, `ACTIVE`, with `required_fields`
  taken from `QA_VERDICT_FIELDS` itself, so the ADR-0034 field names have one source;
- `QA_AGENT` — the descriptor, with **no** `skill_refs` and **no** `tool_refs`;
- the `AGENTS` / `SCHEMAS` catalogues keyed as the Composition Root expects.

The module owns no Prompt text (PST-02). Adding it changes no core module (PROJECT.md §4.11).

### 2. The v1 Prompt lives in the bundled store

`prompts/catalogue.toml` gains `qa-agent` version 1 with `schema_ref = "qa-verdict@v1"`. The System
Prompt:

- names the role and its object (health material before a human sees it);
- states **only the documented criteria** — factual correctness; no unsubstantiated medical claims
  (promises of cure, guaranteed results, diagnoses or treatment prescriptions, as the plain reading
  of PROJECT.md §1); editorial standards (clear, to the point, no fear-mongering);
- teaches the ADR-0034 grammar explicitly: `verdict` is exactly one of `passed` / `flagged` /
  `failed`; `flags` is a JSON array of strings, one remark per item with a quote and a fix, `[]` for
  none; a risk verdict needs a flag;
- biases to fail closed: when in doubt, not `passed` (PROJECT.md §10).

The User template is `Материал на проверку:\n{input}\n\nВерни: verdict, flags.` — the Artifact
content will stand for `{input}` once an evaluator renders it (11.3).

### 3. The criteria are a baseline pending the maintainer's review

The maintainer chose (2026-09-16) to ship v1 on documented principles rather than invented clinical
rules, and to review it. Concrete rules — forbidden claim lists, mandatory disclaimer wording,
niche-specific evidence requirements (`CONTENT_FACTORY_THOUGHTS.md` §5, non-normative) — arrive as a
**new Prompt version** under PROMPT_STORE_SPEC §4, never as an unversioned edit. The acceptance pins
the Prompt/Schema consistency (the grammar tokens), not the wording, so a reviewed v2 needs no test
rewrite beyond its version.

### 4. Not wired, and no Skill or Tool

The role is resolvable by the Composition Root (`build_schema_map` binds `qa_agent@v1` to
`qa-verdict@v1`) but no entrypoint builds or calls it; the evaluator is 11.3 and the wiring 11.4.
The Agent must **not** be passed to `build_executor_map`/`compile_runtime` as a Workflow step: its
answer is a verdict, not an Output. Granting `check_required_elements@v1` is deferred until an
evaluator-side invocation seam and the disclaimer wording both exist.

## Consequences

### Positive

- 11.3 has a concrete Agent/Prompt/Schema to build `LLMArtifactEvaluator` from, through the usual
  catalogue shape.
- The field names stay single-sourced (`QA_VERDICT_FIELDS`), and the Prompt demonstrably teaches the
  exact grammar the decoder accepts.
- No domain rule was invented; the review point is recorded, not implicit.

### Negative / Trade-offs

- v1 criteria are generic. A live QA verdict before the maintainer's review is a first-pass filter,
  not a compliance sign-off — the human gate (ADR-0007) remains the authority either way.
- The role module imports one constant from the application layer. Bounded: it is static data, and
  the alternative (a duplicated literal) splits the ADR-0034 contract across two places.

## Alternatives considered

- **Draft concrete clinical rules now.** Rejected: explicitly the maintainer's call (`CLAUDE.md`
  11.2); unreviewed medical rules are exactly the domain risk Stage 8 exists to remove.
- **A placeholder prompt.** Rejected by the maintainer: a documented-principles baseline is usable in
  11.3–11.5 and changes only by version.
- **Grant `check_required_elements@v1` now.** Rejected: no invocation seam on the QA path and no
  agreed disclaimer wording — the grant would have no reader.
- **Literal `("verdict", "flags")` in the role module.** Rejected: two sources for one contract.

## References

- `PROJECT.md`: §1 (value 1), §4.11, §10, §15
- `ARCHITECTURE.md`: §3.9
- `ROADMAP.md`: Stage 8
- ADR-0008, ADR-0010, ADR-0011, ADR-0018 §6, ADR-0027, ADR-0030, ADR-0034
- `EVALUATION_SPEC.md` §8.2, `EVALUATION_ACCEPTANCE.md` §4.2 (`QAR`), `PROMPT_STORE_ACCEPTANCE.md`
  1.1 (PST-01)
