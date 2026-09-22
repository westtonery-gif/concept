# ADR-0070: Clip QA blocks only what must not ship — boundary remarks become hints

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer (chose to soften the criteria, 2026-09-22); Lead Architect
- **Amends:** ADR-0056 §2 and ADR-0058 §3 (which of the clip criteria gate the clip)
- **Serves:** `CLAUDE.md` queue task 27

## Context

After ADR-0068/0069 the re-run of the first episode (task 24, 2026-09-22) gave 1 `passed`,
11 `flagged`, 3 `failed`. The maintainer watched the clips and judged **all of them acceptable**.
Almost every flag was about boundaries: a clip opens on a reply to something before it, ends before
the payoff, or crosses from one storyline to another. Those are criteria 1 and 2 (self-contained, no
orphaned punchline) read strictly, and "Если сомневаешься — не ставь passed" makes a strict reading
the only safe one for the model.

The gate (ADR-0018) is not the problem and is not touched: a clip ships only with a `passed` latest
verdict **and** a human Approve. What changes is what the QA role treats as a reason to shut it. For
this material the maintainer is a better judge of "does this cut read" than a model reading a
transcript; a model is useful for what a busy human might miss.

## Decision

### 1. Two things block a clip

`clip-qa-agent` **v3** returns `flagged` or `failed` only for:

1. **A spoiler** — the clip gives away something the episode reveals later (checked against the
   whole-episode transcript, ADR-0068). Clear → `failed`; unsure → `flagged`. Doubt still fails
   closed, but **only here**.
2. **No content** — the clip is essentially the opening titles, end credits, theme song or the
   dubbing/translation credits, with no scene in it → `failed`.

### 2. Everything else is a hint, with a `passed` verdict

A start or end that is not a scene boundary, a line that answers something before the clip, a
change of storyline inside the clip, a joke whose setup is outside it — the model says so in
`flags`, prefixed «Подсказка:», and answers **`passed`**. ADR-0034's grammar already allows a
`passed` verdict to carry remarks, and the review package already shows the latest verdict's flags
(ADR-0044), so the reviewer sees them on the Notion page next to the clip. No contract, decoder,
schema or gate changes.

### 3. What does not change

- The verdict grammar, `qa-verdict@v1`, `decode_verdict`, ADR-0018's gate, ADR-0068's context.
- Format is still arithmetic and never a QA question (ADR-0056 §1).
- `qa-agent` (the content factory's role) is untouched; `PROJECT.md`'s "fail closed при сомнении"
  still governs the spoiler criterion, and a human Approve is still required for every clip.

## Consequences

### Positive

- The model's judgement goes where it adds something (a later reveal the reviewer may not
  remember, a clip that is only credits), and boundary taste — which the maintainer has shown they
  judge differently — goes to the person who owns it.
- Remarks are not lost: they are on the review page as hints.

### Negative / Trade-offs

- A clip with a genuinely confusing cut now reaches the human instead of being stopped; the human
  Approve is the only filter for it. That is the point, and it costs one decision per clip.
- The task-25 planner loses some urgency; it remains the way to make boundaries better, not the
  way to make clips approvable.

## Alternatives considered

- **Let a human approve a `flagged` clip.** Changes ADR-0018's gate for every department and
  weakens "fail closed" in general. Rejected by the maintainer in favour of this narrower change.
- **Keep the strict criteria and fix boundaries first (task 25).** Leaves 14 of 15 clips the
  maintainer finds acceptable unapprovable until a new agent exists.

## References

- ADR-0018 (the gate), ADR-0034 (verdict grammar), ADR-0044 (review package carries QA flags),
  ADR-0056 §2, ADR-0058 §3, ADR-0068, ADR-0069; `CLAUDE.md` queue tasks 24, 25, 27
