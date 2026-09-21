# ADR-0068: An evaluation can be given its context — and the episode's index is recorded in the Run to be that context

- **Status:** Accepted
- **Date:** 2026-09-21
- **Deciders:** Lead Architect / Domain Architect (option chosen by the maintainer, 2026-09-21)
- **Amends:** ADR-0056 §3 (how the episode transcript reaches clip QA — decided there, never
  realised). Extends ADR-0018 §6 additively.
- **Serves:** `CLAUDE.md` queue task 24.1; lays the port that task 22 needs

## Context

ADR-0056 §3 decided that clip QA reads **the full episode transcript** next to the clip's own,
because criterion "no spoiler" cannot be judged from an excerpt. `clip-qa-agent` v1's System Prompt
says so to the model. No code path delivered it: `ArtifactEvaluator.evaluate(content: str)` gets
only the Artifact's content, and the clip Artifact carries only its own transcript. The first real
run (2026-09-21, task 24) showed the consequence: every clip was flagged or failed, and several
verdicts said, correctly, that the episode transcript was missing and criterion 3 could not be
checked. By construction, no clip could pass.

The acceptance criterion meant to catch this, CQA-04, tested the **Prompt text** ("the prompt asks
for the whole episode"), not **what the evaluator receives**. It was green while the input was
absent.

This is task 22's defect in another department: the content factory's `qa-agent` v2 judges against
the client's rules and past output, which `evaluate(content)` cannot show it either. The maintainer
chose to answer both with one mechanism rather than twice.

One further constraint decides where the transcript lives. `ClipProduction` indexes the episode
only while producing; an invocation that resumes at `WAITING_QA` (after a QA outage, CRN-02) goes
straight to judging and never indexes. The context must therefore be **in the Run**, not in memory
of the invocation that happened to compute it.

## Decision

### 1. The port gains a second, additive method

```python
class ContextualArtifactEvaluator(ArtifactEvaluator, Protocol):
    def evaluate_in_context(self, content: str, context: str) -> EvaluationResult: ...
```

`evaluate(content)` is **unchanged** in signature and behaviour; every existing implementer stays
valid (PROJECT.md §4.11, additive). Alongside `evaluate_artifact` / `record_verdict`, the
application layer gets `evaluate_artifact_in_context` / `record_verdict_in_context`, which differ
only in handing the evaluator the context. Everything else — opening the Evaluation, recording
calls before the verdict, fail closed on any exception, `decode_verdict` as the sole judge — is the
same code path, not a copy.

The context is **not stored on the Evaluation**. It is derived from the Run (§2), so storing it
again would be a second copy of a fact the Run already holds, and it would change the Evaluation
entity for nothing.

### 2. The episode's index is a recorded step of the Run

Indexing becomes a Task of its own: `index-footage`, agent ref `footage_index@v1`, opened and
committed **before** the outside call (ADR-0026 §2), succeeding with an Output whose payload is
the canonical JSON of the `IndexedFootage` (`duration_ms`, `scenes`, `speech` with each span's
text and times) under the opaque reference `footage-index@v1`. It gets no Artifact: it is a trace,
not a deliverable. If indexing fails, the Task fails with `FOOTAGE_INDEX_FAILED` and the Run fails
as before.

Two things follow, and both are wanted:

- **The QA context is read from the Run**, so a resumed invocation judges with exactly the
  transcript the clips were cut from.
- **A resumed invocation no longer re-runs whisper.** It re-plans from the recorded index. That is
  minutes saved, and it makes CLP-09's claim ("re-planning gives exactly the clips a crashed
  invocation was producing") true even though a second transcription is not guaranteed to be
  bit-identical to the first.

Clip Tasks are recognised by their step (`render-clip`), not by position, so the tally (task 24.4)
and resumption ignore the index Task.

### 3. What the context says

`episode_transcript(footage) -> str` is a pure function: one line per speech span,
`[start_ms–end_ms] text`, in episode order. Milliseconds, not `mm:ss`, because the clip's own
`start_ms` / `end_ms` are milliseconds: locating the clip in the episode is then a comparison, not
arithmetic the model has to do. The context is **the same string for every clip of an episode**, and
it goes **before** the clip in the user message — so the per-episode prefix is identical across the
calls, which is exactly what the caching deferred by ADR-0056 §3 will need.

The clip Artifact's content is unchanged.

### 4. A Prompt must say where the context goes, and the evaluator refuses a mismatch

`LLMArtifactEvaluator` renders `{context}` alongside `{input}` in one pass (a transcript containing
the text `{input}` is not re-substituted). It refuses both mismatches, loudly and before any call:

- `evaluate_in_context` on a template **without** `{context}` — the context would be dropped
  silently, which is this ADR's bug;
- `evaluate` on a template **with** `{context}` — the Prompt would promise the model something it
  does not get, which is the same bug seen from the other side.

The refusal is a `ValueError`: the Evaluation stays `PENDING` and the gate stays shut.

`clip-qa-agent` becomes **v2**: the same three criteria, a System Prompt that describes the
timestamped episode transcript, and a user template with `{context}` before `{input}`. Prompt
versions are immutable (ADR-0030); v1 is superseded in the store, not edited.

### 5. What this ADR does not decide

- **Task 22's context.** The factory's `qa-agent` can now be given one; *what* it is (the brief as
  the first Task's input, a client profile, the superseded version) and the matching `qa-agent` v3
  remain task 22's decision.
- **Caching** the per-episode prefix (ADR-0056 §3 Deferred) — made possible by §3, not done here.
- **Old Runs.** A Run produced before this ADR has no recorded index. At `WAITING_QA` it cannot be
  judged: the invocation reports a QA error naming the missing index and the gate stays shut. The
  only such Run in existence is `COMPLETED`.

## Consequences

### Positive

- Criterion 3 finally has its evidence; a clip **can** pass.
- The factory's QA port can carry context without a changed signature, which task 22 needs.
- The recorded index removes a slow, non-deterministic step from resumption.
- CQA-04 is restated to test **the evaluator's actual input**, so this class of gap — a Prompt
  promising what the code never sends — is now a failing test rather than a live surprise.

### Negative / Trade-offs

- The Run snapshot grows by one episode index (~40 KB of JSON for 22 minutes, ~550 spans). It is
  written with every save of that Run; bounded, and far below the per-clip copying it replaces.
- The episode transcript is sent once per clip (ADR-0056 §3 accepted this cost); ~12 k input tokens
  per call on this episode, until caching lands.
- `ArtifactEvaluator` now has a sibling protocol. That is the price of additivity.

## Alternatives considered

- **Put the episode transcript into every clip's payload.** No port change, but ~15 copies per
  episode in the store, an Artifact carrying data that is not the clip, and nothing for task 22.
  Offered to the maintainer, not chosen.
- **Build the evaluator per episode with the transcript bound in.** No port change, but the
  transcript would exist only in the invocation that indexed — a resumed invocation (§Context)
  would have none, or would re-run whisper to rebuild it.
- **Change `evaluate`'s signature to take a context.** Breaks every implementer and the frozen
  ADR-0018/0036 contract; the Conventions require a second method instead.
- **Store the context on the Evaluation.** Duplicates what the Run holds and changes a domain entity
  without need (§1).

## References

- ADR-0018 §6 (the QA port), ADR-0026 §2 (commit before an outside call), ADR-0030 (immutable
  Prompt versions), ADR-0036 (`LLMArtifactEvaluator`), ADR-0056 §3 (the full transcript),
  ADR-0059 (one Run per episode), ADR-0064 (the local footage index)
- `CLAUDE.md` queue tasks 22 and 24; `CLIPPING_SPEC.md` §8; `EVALUATION_SPEC.md` §8
