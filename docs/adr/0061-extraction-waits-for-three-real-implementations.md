# ADR-0061: The Notion plumbing extraction waits for three *written* implementations

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** Lead Architect / Domain Architect
- **Amends:** ADR-0060 §5 (the ordering only; everything else in ADR-0060 stands)

## Context

ADR-0060 §5 decided that the shared Notion HTTP plumbing would be extracted **before** the Notion
`ReviewDesk` was built, reasoning that the desk is the third consumer and that ADR-0055 §3 had
named the third as the moment to extract.

That count was wrong. It counted **decisions**, not **modules**. As of today the repository holds
exactly one module that speaks Notion — `infrastructure/notion_brief_board.py`.
`NotionEpisodeBoard` (ADR-0055) and `NotionReviewDesk` (ADR-0060) are both decided and **unwritten**.

Extracting a shared client from a single implementation means inventing the seams two future
callers will need, which is exactly what `CLAUDE.md` Conventions forbid: *"Do NOT extract shared
base classes prematurely (rule of three)."* ADR-0055 §3's own wording is "when the third
**arrives**" — and foreseeable is not arrived. The refactor would also have no proof: its whole
argument is "behaviour did not change", and there is no second behaviour to hold it against.

## Decision

**The extraction happens after three Notion implementations exist in `src/`, not before.** The
order is:

1. `NotionReviewDesk` (ADR-0060) — the second implementation. It duplicates the request helper,
   the auth header, the page fetch and the property reader **deliberately**, exactly as ADR-0055 §3
   authorised at two.
2. `NotionEpisodeBoard` (ADR-0055) — the third, in queue task 21.6.
3. **Then** the extraction, as its own behaviour-neutral refactor with its own ADR, touching
   `infrastructure/` only, with the full quality gate over three real consumers as its proof.

ADR-0060 §5's reason for extracting *before* a feature — that a refactor's proof is worthless in a
diff that also adds behaviour — is correct and is kept. It applies to step 3: the extraction still
lands alone, never inside the commit that revealed the need.

## Consequences

### Positive

- The rule of three is applied to code rather than to intentions, which is what it is for.
- The desk, which unblocks Milestone M3 and the clipping department's gate, stops waiting on a
  refactor that cannot yet be justified.
- When the extraction comes, three written callers will show what actually varies — the settings
  shape, the property types, the error mapping — instead of one caller and two guesses.

### Negative / Trade-offs

- The plumbing is written a second time by hand, and the second copy will drift slightly from the
  first before they are reconciled.
- Two ADRs now govern one ordering. ADR-0060 §5 must be read with this amendment beside it.

## Alternatives considered

- **Extract from one implementation, shaping it by the two ADRs.** Rejected: the ADRs fix
  contracts, not call shapes, and a client designed against unwritten callers is a guess that later
  code has to work around.
- **Build all three, then extract.** Effectively what this decides; naming the desk as the next step
  keeps M3's blocker moving rather than holding it behind task 21.6.
- **Leave ADR-0060 §5 as written and extract now anyway.** Rejected: it would put a premature
  abstraction into `infrastructure/` and contradict the Conventions the rest of the repo keeps.

## References

- `CLAUDE.md` Conventions (rule of three); `PROJECT.md` §4 п.11
- ADR-0055 §3 (duplication deliberate at two, extract when the third arrives), ADR-0060 §5 (amended)
