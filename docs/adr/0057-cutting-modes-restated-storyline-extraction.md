# ADR-0057: The two cutting modes restated — the episode's structure decides, and storyline mode runs on the transcript

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** Lead Architect / Domain Architect
- **Amends:** ADR-0053 §5 (what the two modes are), ADR-0055 §2 (`ClipMode`'s vocabulary),
  ADR-0056 §2 (the clip payload and QA criterion 2). Those ADRs stay Accepted; this one supersedes
  the specific passages named below.
- **Serves:** `CLAUDE.md` queue task 21

## Context

ADR-0053 §5 recorded two cutting modes and described the first one as *moment-finding*: the service
indexes the episode, the planner asks it for moments, ranks them and justifies each candidate. That
came straight from `CLIPPING_DEPARTMENT_THOUGHTS.md`, which frames the whole department around
"find the clip-worthy moments" — highlight detection.

The maintainer corrected this on 2026-09-20, and the correction is about the **source material**,
not about the algorithm:

- Some series follow essentially one thread — the main characters are on screen throughout. An
  episode like that is continuous, so **cutting it into consecutive two-minute pieces works**, and
  nothing needs to be understood to do it.
- Other series carry **many main characters, each with their own scenes, and those scenes are
  interleaved through the episode**. Cutting that by length gives you two minutes of three unrelated
  threads. What is wanted there is to **follow one thread and gather its scattered scenes into one
  clip**.

That second thing is not highlight ranking. It is **de-interleaving** — separating storylines an
editor deliberately braided together. The distinction matters because it changes what the planner
outputs, what a clip physically is, and what QA must forbid.

It also resolves an arithmetic flag raised in ADR-0053's Deferred list: 15 clips of two minutes out
of a 25-minute episode looked impossible. It is not — 25 ÷ 2 ≈ 12–13, so those were **chunk-mode
numbers all along**, describing an episode covered end to end. Storyline mode yields a different,
smaller count, and its clips are as long as the thread needs.

## Decision

### 1. The modes are named for what they are: `CHUNK` and `STORYLINE`

`ClipMode`'s vocabulary becomes `CHUNK` and `STORYLINE`, replacing ADR-0055 §2's
`SEMANTIC`/`CHUNK`/`BOTH`. "Semantic" described a method and said nothing about the result;
"storyline" names the thing produced. `BOTH` is dropped: an episode is one structure or the other,
and an editor who genuinely wants both passes over one episode can mark it twice. A vocabulary that
offers a combination nobody has asked for invites a Workflow branch nobody has designed.

Everything else in ADR-0055 §2 stands: the mode is an **editorial** property on the board, because
which kind of series this is, is something the editor knows and an agent would only guess at.

### 2. A storyline clip is several segments, in episode order

This is the substantive change. A chunk clip is one interval. A **storyline clip is a list of
non-contiguous segments spliced together**, and splicing is the *point* of the mode, not an
accident of it.

So the clip Artifact's canonical JSON (ADR-0056 §2) carries **`segments`** — an ordered list of
start/end pairs with the transcript of each — rather than a single boundary pair. A chunk clip is
the degenerate case with one segment, which keeps one payload shape for both modes and one QA input
for both.

**Segments stay in the episode's own chronological order.** Reordering them would let the department
assemble a sequence of events that never happened in that sequence, which is precisely the harm
criterion 2 exists to prevent.

### 3. QA criterion 2 is restated, because splicing is now intended

ADR-0056 §2's criterion 2 reads *"no splice that invents an exchange that never happened"*, written
when a clip was assumed to be one continuous cut. Under storyline mode that wording would forbid
the mode itself. Restated, keeping the harm and dropping the accidental prohibition:

> **No invented continuity.** Segments may be joined only in episode order and only from one
> thread. A join must not make two moments read as continuous when they are not — two characters
> appearing to address each other across a cut they never shared, a reaction attached to a line it
> did not follow, a consequence placed before its cause.

The other three criteria are unchanged, and criterion 1 (self-contained) gets heavier in this mode
rather than lighter: a thread pulled out of its episode has to stand alone without the scenes that
were interleaved around it.

### 4. Storyline mode is designed to run on the transcript, not on face recognition

The obvious implementation — identify which recurring character is in which scene — needs person
re-identification across a 25-minute episode. That capability is **not evidenced** in the chosen
vendor.

Checked on 2026-09-20 against Vyra AI's own product page: it states *"Every clip is analyzed:
scenes detected, speech transcribed, subjects tagged"*, with the tags shown being generic
categories (`person`, `indoors`, `speech`) rather than identities, and the product is presented
around short creator footage — a handful of clips, a four-minute vlog — not a broadcast episode
with recurring cast. Scene detection, transcription, captions and export to TikTok/Reels/Shorts are
all clearly covered; *"this person in scene 3 is the same person as in scene 11"* is not claimed
anywhere on that page.

So the department does not depend on it. **Storyline mode follows threads through the
transcript**: Vyra supplies scene boundaries and timed speech, and the planner agent — whose medium
is text — reads who speaks, to whom, and about what, and groups the scenes that belong to one
thread. This is work a language model is actually good at, it needs nothing from the vendor beyond
what is advertised, and it degrades honestly: a thread carried by silent action rather than
dialogue will be followed poorly, and that is a limit to measure on the first episode rather than
to hide.

If re-identification turns out to exist and to be good, it becomes an additional signal behind the
same port, not a redesign. ADR-0053's escape hatch — revisit the in-house approach only if the
service does not deliver — applies to this capability specifically, and this is the first concrete
thing to put to the vendor.

### 5. What does not change

`CHUNK` mode still needs the transcript, for the reason ADR-0053 §5 already gave: a boundary is
nudged to the nearest speech pause so a cut does not land mid-word. It still runs with no agent, no
vendor reasoning call and no ranking. The gate, the verdict grammar, the board contract and the
Tool boundary are all untouched.

## Deferred

- **Putting re-identification to the vendor** as a concrete question, with the first real episode
  as the test (§4).
- **How a thread is identified and bounded** — per character, per subplot, how much of a scene
  counts as belonging to a thread — is the planner's Prompt and Schema, task 21.6.
- **A maximum length for a storyline clip.** A thread might add up to eight minutes; whether that
  is one clip, several, or a reason to reject is a product call with no evidence yet.
- The Run-granularity knot (ADR-0055 §6, ADR-0056 Deferred) is untouched by this ADR and still open.

## Consequences

### Positive

- The department is now designed against the maintainer's actual material instead of the working
  note's assumption, and the two modes have a clear rule for choosing between them: the episode's
  structure, which an editor recognises at a glance.
- The vendor dependency shrinks to what the vendor actually advertises, and the hard part lands on
  the component best suited to it.
- One payload shape and one QA input serve both modes.

### Negative / Trade-offs

- Three Accepted ADRs now have passages superseded by a fourth. The index says so and each amended
  section is named, but a reader of ADR-0053 alone will get the old framing.
- Transcript-driven thread-following will be weakest exactly where television is often strongest —
  a storyline carried visually, with little dialogue.
- Dropping `BOTH` means an editor who wants both passes marks the episode twice. That is deliberate;
  it can come back as a mode if the double-marking turns out to be the common case.

## Alternatives considered

- **Keep "semantic / highlight" framing and treat storylines as one kind of moment.** Rejected: a
  highlight is a point, a storyline is a set of intervals scattered across the episode. Conflating
  them would have left the planner's output shape wrong.
- **Depend on the vendor for character re-identification.** Rejected as an unverified dependency at
  the centre of the design; §4's transcript route needs only advertised capabilities.
- **Let the planner reorder segments for a better clip.** Rejected outright: it is the one edit that
  manufactures events, and no ranking benefit justifies it.
- **Fix storyline clips at two minutes too**, for uniformity. Rejected: the length of a thread is a
  property of the episode, and truncating it to a number would reintroduce the orphaned-punchline
  problem QA exists to catch.

## References

- ADR-0053 §5 (amended), ADR-0055 §2 (amended), ADR-0056 §2 (amended), ADR-0018 (the gate),
  ADR-0034 (the verdict grammar)
- `CLIPPING_DEPARTMENT_THOUGHTS.md` (non-normative; its moment-finding framing is superseded here)
- Vyra AI product page, read 2026-09-20 — "Footage Understanding: scenes detected, speech
  transcribed, subjects tagged"
- `CLAUDE.md` queue task 21
