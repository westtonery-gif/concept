# ADR-0058: Cutting at scene boundaries — and what falls away when v1 has no planner agent

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** Lead Architect / Domain Architect
- **Supersedes:** ADR-0057 in full. Further amends ADR-0053 §5 and ADR-0056 §2.
- **Serves:** `CLAUDE.md` queue task 21

## Context

ADR-0057, written earlier today, recorded the maintainer's description of the problem: in a series
with many main characters, the scenes of different threads are interleaved, so cutting by length
yields two minutes of three unrelated threads. Its answer was **storyline extraction** — follow one
thread through the transcript and gather its scattered scenes into one clip.

The maintainer then simplified the requirement (2026-09-20): cut **where one scene ends and the
next begins**, and if even that proves heavy, fall back to plain two-minute pieces.

It is not heavy, and it is worth saying why before deciding anything: **this solves the stated
problem directly**. The complaint was never "I want one character's whole arc in one clip" — it was
that a length-based cut lands in the middle of things and mixes threads that do not belong
together. A scene boundary never falls mid-scene, and a scene belongs to exactly one thread. So
cutting at scene boundaries removes the interleaving problem at its source.

And it is cheap, because the vendor already reports the boundaries. Vyra's own product page states
*"scenes detected, speech transcribed"* (checked 2026-09-20, the reading that survives from
ADR-0057 §4). Scene detection is the advertised, mainstream half of what it does — unlike
recurring-character re-identification across a 25-minute episode, which ADR-0057 found to be claimed
nowhere and which storyline mode would have depended on.

So the simplification trades away something real and buys something larger. This ADR states both.

## Decision

### 1. Both v1 modes produce one contiguous interval

`ClipMode` becomes **`CHUNK`** and **`SCENE`**, replacing ADR-0057 §1's `CHUNK`/`STORYLINE`:

- **`CHUNK`** — consecutive pieces of a configured length, each boundary nudged to the nearest
  speech pause so a cut does not land mid-word. Unchanged from ADR-0053 §5.
- **`SCENE`** — boundaries are the scene boundaries the vendor reports. Consecutive scenes are
  merged while the result stays under the configured maximum, and a scene longer than that maximum
  is cut at a speech pause, falling back to `CHUNK`'s rule for that one clip rather than emitting
  something out of spec.

It stays an **editorial property on the board** (ADR-0055 §2 holds): which kind of series this is,
the editor knows at a glance.

### 2. `segments` is withdrawn — a clip is one start and one end

ADR-0057 §2 gave the clip payload an ordered `segments` list, because a storyline clip was several
non-contiguous pieces spliced together. **Nothing in v1 splices.** Both modes emit one contiguous
interval, so the payload carries a single `start`/`end` pair and the list is removed before it ever
exists.

This is the repo's own discipline about fields with no reader (ADR-0022 §2 refused a second display
name on the same grounds). If splicing returns, the list returns with it, in its own ADR and with
the QA criterion below restored in the same change.

### 3. QA criterion 2 is withdrawn, not merely restated

ADR-0056 §2's criterion 2 forbade a splice that invents an exchange; ADR-0057 §3 restated it as "no
invented continuity" so that deliberate splicing could exist. With §2 above, **neither applies**:
merging two consecutive scenes joins what the episode itself shows consecutively, which invents
nothing.

v1's QA criteria are therefore three, and every one of them still has work to do:

1. **Self-contained** — the clip is comprehensible without the surrounding episode.
2. **No orphaned punchline** — a joke or payoff carries the setup it depends on. A scene boundary is
   not a comic boundary; a setup two scenes earlier is exactly the case this catches.
3. **No spoiler** — the clip does not give away a beat the episode reveals later. Still needs the
   full episode transcript in the QA input (ADR-0056 §3), and still the reason that cost is
   accepted.

"No reply cut mid-word" leaves the criteria list entirely: §1 makes it a property of how boundaries
are chosen, checked deterministically, not something to ask a model about — the same split ADR-0056
§1 made for duration.

### 4. v1 has no planner agent, and that is the real consequence

Both modes are now **deterministic `Workflow` steps**: ask the vendor for scene boundaries and the
transcript, compute intervals, render, caption. Nothing in either mode requires a model to decide
anything. The clip-planner Agent that ADR-0053 §5 and the working note assumed — the role that
ranks moments and justifies candidates — **does not exist in v1**.

What the department keeps is the part that actually needs judgement: the **QA role** (ADR-0056 §2,
`clip_qa_agent@v1`), the fail-closed gate and the human Approve. That is one model call per clip,
not two, and the expensive reasoning loop is gone.

Consequences that follow, stated rather than left to be discovered:

- **ADR-0054 has no consumer in v1.** The side-effecting Tool boundary was settled for a planner
  agent that would query the vendor mid-reasoning. With no such agent, no Tool is needed: the vendor
  is reached from a Workflow step through an ordinary Adapter, which is the plainer path and the one
  ADR-0054 §2 pointed at anyway. **ADR-0054 is not wrong and is not superseded** — it is the
  standing answer for the first Tool that does need it, and its at-least-once finding remains true
  of every Tool call. It is simply unexercised, and queue task 21.1 is done as a decision with no
  code to write.
- **The department is thin on purpose.** A deterministic pipeline plus a QA role plus a human gate
  is a legitimate shape here: `TaskExecutor` is a Protocol and `SkillPreprocessingTaskExecutor`
  (ADR-0027) is already a non-LLM implementation of it.
- **The first working version arrives much sooner**, and the vendor evaluation narrows to one
  question — are its scene boundaries good on this material.

### 5. What is given up

Naming it so the choice stays visible: the department will **not** assemble one character's arc into
a single clip. A viewer gets that character's scenes as separate clips, each coherent on its own,
in the order the episode showed them.

If that turns out to be what the material needs, storyline extraction comes back as an additive
mode — ADR-0057 is superseded, not deleted, and its transcript-driven design (§4 there) is the
starting point, along with its finding that the vendor does not advertise the re-identification such
a mode would otherwise want.

## Deferred

- **The maximum clip length and the configured chunk length** — real numbers, with the render
  parameters, in configuration. §1's merge-and-split rule is written in terms of them.
- **Whether scene boundaries from this vendor are good on this material.** The one open vendor
  question now, answered by the first real episode.
- **The Run-granularity knot** (ADR-0055 §6, ADR-0056 Deferred) — untouched by this ADR and still
  the last decision before code: one Run per episode with many clip Artifacts, or one Run per clip.
  Simplifying the cutting did not simplify this.
- **The gate's granularity** — ~13 clips per episode, each with its own Approve until the
  maintainer's test says a batch may share one.

## Consequences

### Positive

- The stated problem — a length-based cut mixing unrelated threads — is solved at its source, by a
  vendor capability that is mainstream rather than speculative.
- One model call per clip instead of a planning loop plus a verdict: materially cheaper at ~13 clips
  an episode, and far less to build before the first episode runs end to end.
- Two payload fields and one QA criterion were removed before they were ever written, rather than
  carried as dead weight.

### Negative / Trade-offs

- Per-character arcs are out of reach in v1 (§5).
- A scene is whatever the detector says it is. A series that cuts fast will produce many short
  scenes and lean on the merge rule; one with long static scenes will lean on the split rule. Both
  are configuration-shaped problems, but they are real and the first episode will expose them.
- Three ADRs written today now have passages superseded, one of them in full. The chain is
  recorded, but the working note and ADR-0053 read as if a planner agent is coming.

## Alternatives considered

- **Keep storyline extraction as v1.** Rejected on the maintainer's simplification, and it was the
  riskier half of the design: it depended on thread-following quality that could only be judged
  after building it.
- **Drop to `CHUNK` only**, the maintainer's own fallback. Not needed: `SCENE` costs little, because
  the boundaries are already reported, and it is the mode that answers the original complaint.
- **Keep `segments` and the continuity criterion "for later".** Rejected: a field no producer fills
  and a criterion no clip can violate are noise that a later reader has to disprove.
- **Keep a planner agent to choose which scenes are worth clipping.** Rejected for v1: that is
  highlight ranking under another name, the thing this department was explicitly not asked for. If
  clip *selection* becomes a need, it arrives as its own decision with its own evidence.

## References

- ADR-0053 §5 (amended), ADR-0054 (unexercised in v1, not superseded), ADR-0055 §2 (vocabulary),
  ADR-0056 §1/§2/§3 (criteria amended), ADR-0057 (superseded; its Vyra finding survives here),
  ADR-0027 (a non-LLM `TaskExecutor` already exists), ADR-0022 §2 (no field without a reader)
- Vyra AI product page, read 2026-09-20 — "scenes detected, speech transcribed"
- `CLAUDE.md` queue task 21
