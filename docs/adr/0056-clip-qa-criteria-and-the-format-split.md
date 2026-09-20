# ADR-0056: Clip QA — what the model judges, what arithmetic judges, and why they are two different gates

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** Lead Architect / Domain Architect
- **Serves:** `CLAUDE.md` queue task 21.2 (the clipping department, ADR-0053)

## Context

ADR-0053 put a fail-closed QA verdict and a human Approve on every clip, the same discipline the
content factory already enforces (ADR-0018). Two questions were flagged back to the maintainer
rather than guessed, and both are answered (2026-09-20, recorded in queue task 21.2):

- **Accuracy to the source**, for scripted fiction, is not "do not state a falsehood". A clip must
  be **self-contained and must create no meaning the scene does not contain**: no reply cut
  mid-word, no splice that invents an exchange that never happened, no punchline without its setup,
  no spoiler of a later beat.
- **The frame:** v1 does not reframe. The maintainer's screenshot was misread on first look — the
  black bars are TikTok's, added because a 16:9 clip was uploaded as-is. The render keeps the source
  aspect and the platform letterboxes it.

A third thing is settled and shapes everything here: `ArtifactEvaluator.evaluate(content: str)`
(ADR-0018 §6) takes **text**. `LLMArtifactEvaluator` renders that text into the role's Prompt
template (ADR-0036). The QA port cannot see pixels, and nothing in this department's v1 justifies
changing a port the whole factory depends on.

## Decision

### 1. Format compliance is arithmetic, and arithmetic never goes to a model

A clip's technical fitness — duration inside the platform's cap, the resolution and container the
render was told to produce — is **checked deterministically and is not a QA criterion**. A model
asked whether 118 seconds is within a 120-second limit will usually be right, which is the problem:
usually is not a gate.

More than that, a clip that is out of spec is **not a content risk a human should read about** — it
is the render step producing something other than what it was told. That is a defect in our
parameters or our code, so it ends as a **`FAILED` Task with a stable reason**, the way an `INVALID`
Output does (ADR-0033), not as a `flagged` verdict queued for a person. The measurements come from
the render step, which produced the file and knows them; checking measured numbers against
configured limits is pure and belongs in the Skills library, whose precedent is already
`check_required_elements@v1`.

This keeps the two failure modes apart, which matters at ~450 clips a month: a human's queue holds
only clips where judgement is actually required.

### 2. The QA role judges the clip's meaning, and it reads the plan, not the pixels

`clip_qa_agent@v1`, built exactly on `qa_agent`'s template (ADR-0035): a new `Agent`, a new Prompt
`clip-qa-agent` in the bundled versioned store (ADR-0030), **no Skills and no Tools**, and it
answers with a verdict rather than an Output — it sits behind `ArtifactEvaluator`, never as a
Workflow step.

It reuses the **`qa-verdict@v1` Schema and the ADR-0034 verdict grammar unchanged**:
`verdict ∈ {passed, flagged, failed}`, `flags` a JSON array of non-blank strings, a risk verdict
needs at least one flag, and `decode_verdict` stays the sole judge. Nothing about a clip justifies a
second verdict vocabulary, and a second one would mean a second decoder to keep honest.

Its four criteria are the maintainer's answer, stated as things a reader can check:

1. **Self-contained** — the clip is comprehensible without the surrounding episode.
2. **No invented exchange** — no cut or splice makes two moments read as one that never happened.
3. **No orphaned punchline** — a joke or payoff carries the setup it depends on.
4. **No spoiler** — the clip does not give away a beat the episode reveals later.

The content it reads is the clip Artifact's own text: canonical JSON (the shape ADR-0032 already
uses for rework input) carrying the episode reference, the cutting mode, the boundaries, the
transcript of exactly what the cut contains, the draft caption, and where the rendered file is. The
human reviewer needs the video itself, and that is what the location is for — QA needs the meaning,
which the transcript carries.

### 3. A spoiler criterion needs the whole episode, and that costs money

Criterion 4 cannot be judged from the clip alone: nothing in a two-minute excerpt says whether it
reveals something the episode discloses later. So the QA input carries the **full episode
transcript** alongside the clip's own, and without it criterion 4 would be theatre — a rule in a
Prompt that the model has no evidence to apply.

The cost is real and is accepted with open eyes: every clip's QA call re-sends the same episode
transcript, and at ~15 clips per episode that is the transcript fifteen times. It is bounded (one
episode is minutes of dialogue, not a corpus), it is per-role configurable (ADR-0052), and the
obvious optimisation — caching the per-episode prefix across the calls of one episode — is
infrastructure, deferred and measured rather than assumed.

### 4. Chunk-mode clips are judged too, for now

Chunk mode cannot splice, so criterion 2 is unreachable by construction, and its boundaries are
already nudged to speech pauses (ADR-0053 §5), which weakens criterion 1's worst case. It would be
easy to argue chunk mode needs no model at all.

It still gets the verdict in v1. Criteria 3 and 4 remain fully live — a mechanical two-minute cut
lands on an orphaned punchline or a spoiler as readily as a chosen one, arguably more so. Narrowing
or dropping QA for chunk mode is an optimisation to make **with numbers from the first episode**,
not an assumption to build in, and it is the same discipline the batch-approval question is already
waiting on.

### 5. What this ADR does not change

- **The gate.** `PROJECT.md` §12 and ADR-0018 are untouched: an Artifact reaches `APPROVED` only
  with an approving Human Review **and** a `PASSED` latest Evaluation. One Artifact, one verdict,
  one Approve per clip stays the v1 reading until the maintainer's test says otherwise.
- **The QA port, the verdict grammar, `decode_verdict`, or `LLMArtifactEvaluator`.** This role is
  an additive catalogue entry, nothing more.
- **`qa_agent@v1`.** The content factory's role is untouched; a Prompt version is immutable
  (ADR-0030/0035) and this is a different role, not a new version of that one.

## Deferred

- **The Prompt text and the role module** land with the implementation (task 21.6), in the shape
  ADR-0035 used: criteria in the System Prompt, the ADR-0034 grammar reused verbatim, and
  `tests/test_clip_qa_agent.py` pinning Prompt/Schema consistency rather than wording.
- **Which evaluator the Director is given** when a Run carries many clip Artifacts.
  `ContentDirector(qa=…)` evaluates the final step's Artifact (ADR-0018), and a per-clip verdict
  needs more than that. This is the same knot ADR-0055 §6 flagged about Run granularity, and it is
  the planner/Workflow ADR's to untie — this ADR fixes *what* a clip verdict says, not *when the
  Director asks for it*.
- **Caching the per-episode transcript prefix** across a run's QA calls (§3).
- **Judging the rendered pixels** — burnt-in captions overlapping a face, a hard cut on a black
  frame. It needs a multimodal call or a vendor check and a port that carries more than `str`. Not
  v1, and not worth widening the factory's QA port for one department.
- **Platform limits as configuration** — the actual duration caps per platform, which belong in
  config with the render parameters, not in this decision.

## Consequences

### Positive

- The model is never asked to do arithmetic it cannot be trusted with, and a human is never asked
  to adjudicate a render bug.
- The verdict contract, decoder, gate and metrics attribution are reused untouched, so a clip's QA
  is observable through the same Evaluation/AnalyticsRecord machinery as the factory's.
- Criterion 4 is honest: it comes with the evidence it needs, or it would not be worth stating.

### Negative / Trade-offs

- Sending the episode transcript per clip is knowingly wasteful until the caching work is done.
- Holding the strict per-clip verdict for chunk mode costs model calls that may prove unnecessary.
  That is the deliberate direction: measure, then relax.
- QA judges the plan, so a defect introduced **between** the plan and the pixels — a render that
  cuts at the wrong timestamp — is invisible to it. §1's deterministic check covers duration and
  container, not "did the render cut where the plan said". That gap is real, and closing it is part
  of the render step's own acceptance, not of QA.

## Alternatives considered

- **One QA role for both departments, with criteria switched by input.** Rejected: `qa-agent`'s
  criteria are the client's editorial rules and uniqueness (ADR-0037, Prompt v2); a clip's are
  about meaning preserved under cutting. One Prompt carrying both would serve neither, and Prompt
  versions are immutable anyway.
- **Let the model check duration and format too, as extra flags.** Rejected per §1: a probabilistic
  answer to an exact question, and it would put render defects into a human's review queue.
- **Skip the LLM verdict for chunk mode.** Rejected for v1 per §4 — plausible, unproven, and
  cheaper to test than to assume.
- **Extend `ArtifactEvaluator` to carry a media reference so QA can see the file.** Rejected: it
  changes a port every existing role depends on, for a capability v1 does not have a way to use.

## References

- `PROJECT.md` §1 (quality and uniqueness), §12 (nothing ships without Approve), §18
- `ARCHITECTURE.md` §3.9 (fail closed when in doubt), §13
- ADR-0018 (the fail-closed gate, the `ArtifactEvaluator` port), ADR-0030 (the versioned Prompt
  store), ADR-0032 (canonical JSON as an agent-facing payload), ADR-0033 (a contract failure is a
  `FAILED` Task with a stable reason), ADR-0034 (the verdict field contract), ADR-0035 (the QA role
  template), ADR-0036 (`LLMArtifactEvaluator`, metrics attributed to the Evaluation), ADR-0052
  (per-role `max_tokens`), ADR-0053 §5 (two cutting modes), ADR-0055 §6 (the Run-granularity knot)
- `EVALUATION_SPEC.md` §8, `CLAUDE.md` queue task 21
