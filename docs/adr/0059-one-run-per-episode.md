# ADR-0059: One Run per episode — the aggregate already carries it, the orchestrator does not

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** Lead Architect / Domain Architect
- **Closes:** the Run-granularity question left open by ADR-0055 §6 and ADR-0056 "Deferred"
- **Serves:** `CLAUDE.md` queue task 21 (the last decision before code)

## Context

An episode yields roughly thirteen clips (ADR-0058), and ADR-0053 §5 requires each to carry its own
QA verdict and its own human Approve. Two shapes were open: one Run per episode holding many clip
Artifacts, or one Run per clip. `ContentDirector` evaluates **the final step's** Artifact
(ADR-0018), which fits neither obviously, and changing it is not permitted without a decision
(`CLAUDE.md` Conventions: *"Do NOT change Run's existing behaviour or signatures"*, extend by adding
modules).

The maintainer chose **one Run per episode** (2026-09-20).

Before recording it, the aggregate was checked rather than assumed, because a shape that the domain
refuses is worth discovering now and not in the first implementation session.

## Decision

### 1. One Run per episode. `Run` does not change — it already carries this

Checked in `domain/run.py`, not inferred:

- `open_evaluation(artifact_id, *, kind, by, evaluator_ref)` opens an Evaluation **of a named
  Artifact**; evaluations are kept in opening order and `latest_qa(run, artifact_id)` already reads
  them per Artifact.
- `open_human_review(artifact_id, *, by)` opens a `PENDING` review **on a named Artifact** and
  carries **no "only one pending review" rule** — it requires only that the Artifact is `CANDIDATE`.
- `transition_artifact(artifact_id, to, by)` gates `CANDIDATE -> APPROVED` on an approving Human
  Review **for that Artifact** and on that Artifact's latest QA Evaluation being `PASSED`. The gates
  are keyed on the Artifact, so thirteen of them are thirteen independent gates by construction.

So the fail-closed discipline (ADR-0018, `PROJECT.md` §12) holds per clip with **no domain change at
all**. That is the load-bearing finding of this ADR: the Run aggregate was designed per-Artifact,
and one Run per episode asks nothing new of it.

### 2. The Run's own status is the batch's status

One status machine covers the episode: `RUNNING` while clips are produced, `WAITING_QA` until every
clip has a recorded verdict, `WAITING_HUMAN` until every clip has a terminal decision, `COMPLETED`
when they all do. The existing transition table allows exactly this path and needs no new edge.

### 3. A clip is not the Run's fate

A clip whose QA failed cannot be approved, and a clip a human rejects does not ship. **Neither fails
the episode's Run.** The Run completes when every clip has reached a terminal decision, whatever
those decisions are; only a failure that stops production itself — the episode cannot be fetched,
the render step fails — fails the Run.

Thirteen clips will not all be good, and an episode that fails because one cut landed badly would
make the department useless.

### 4. The department orchestrates with its own application service, beside `ContentDirector`

`ContentDirector` is not used for the fan-out and is not modified. Its contract is a **declared**
Workflow whose Tasks are matched **by position** (ADR-0026) ending in one final candidate; a
per-clip fan-out of unknown width is a different shape, and bending the Director into it would
touch the core the Conventions protect.

Instead the department gets an application module beside it, the way `BriefProduction` (ADR-0048),
`brief_intake`, `review_publication` and `review_decision` already sit beside the Director rather
than inside it. **Appending Tasks outside a declared plan is an established mechanism, not a new
one:** ADR-0032's rework already appends one traced Task per iteration. One Task per clip render
gives each clip its own Output, its own Artifact, its own trace and its own retry.

The cost is named plainly: that service must keep ADR-0026's commit discipline itself — save before
an executor call, save the answer with its Output and Artifact, save the Evaluation's opening before
the evaluator, save the verdict, save each transition — and must resume from stored state without
duplicating a committed Task or a recorded verdict. Reimplementing that faithfully is the main risk
in task 21.6, and it is the reason the acceptance for this department has to exercise a crash at
every commit point, as `SWR` does for the Director.

### 5. Found while checking: the existing review path is single-review by construction

`application/review_publication.py`'s `pending_review(run)` returns `pending[-1]` — **the latest**
pending review. `pending_review_package`, `publish_pending_review` and `take_review_decision`
(ADR-0045) all inherit that assumption, and their docstrings state it: *"Both publication and
reading the decision back address this one review."*

With thirteen pending reviews on one Run, those functions would address the last one and **silently
leave twelve unpublished** — no error, no flag, just clips that never reach a human.

Therefore: the clipping department gets **per-Artifact siblings** of those functions, and the
existing ones are **not changed**. The content factory depends on their current behaviour, and
"fixing" them in place would alter a path Stage 10–12's acceptance pins. `latest_qa(run,
artifact_id)` is already per-Artifact and is reused unchanged.

### 6. Rework does not apply to a clip, and that is a simplification

ADR-0032 routes `CHANGES_REQUESTED` into re-running the candidate's **producer** to make a new
version. With no planner agent (ADR-0058 §4), a clip has no producer whose reasoning could be
re-run: its boundaries came from a scene detector and arithmetic. Asking for changes on a cut means
wanting a *different* cut, which is a different clip, not a new version of this one.

So in v1 a rejected clip is simply **not shipped**, no `SUPERSEDED` chain and no rework loop. If
per-clip rework is ever wanted, it arrives with whatever component could act on the feedback.

## Deferred

- **The per-clip review desk.** Publishing thirteen reviews needs a desk that exists in the
  maintainer's environment — the same blocker as queue task 19 and Milestone M3, and it will bite
  this department exactly as it bit the factory.
- **Commit points and resumption rules** for the new service, written out as ADR-0026 §2's table
  is, with the acceptance that exercises them (task 21.5 / 21.6).
- **Batch approval.** Still undecided pending the maintainer's test; until then thirteen Approves
  per episode (ADR-0056).
- **Whether one Task per clip is right at thirty episodes a month** — ~390 Tasks and ~390
  Evaluations a month in one store. Not a problem at pilot scale; worth measuring before it is one.

## Consequences

### Positive

- The maintainer's choice costs no core change: the domain was already per-Artifact, and this was
  verified in the source rather than hoped for.
- An episode is one traceable unit — one row, one journal, every clip's Output, Artifact, verdict,
  decision and cost inside it.
- A silent twelve-clips-never-published defect was found before it was written.

### Negative / Trade-offs

- The department reimplements the Director's commit-and-resume discipline. That is duplication of
  the most delicate logic in the repo, and it is accepted because the alternative is changing the
  core.
- The Run's status is coarse: an episode sits at `WAITING_HUMAN` whether one clip or thirteen are
  undecided. Progress within the batch is visible only in the Artifacts.
- One long-lived Run per episode means a crash late in the batch resumes a large object. The
  snapshot carries every clip.

## Alternatives considered

- **One Run per clip.** Rejected by the maintainer. It would also have scattered an episode across
  thirteen rows with nothing in the domain tying them together, and made "is this episode done?" a
  query rather than a status.
- **Teach `ContentDirector` to evaluate many Artifacts.** Rejected: it changes the core, contradicts
  the Conventions, and would put a department's shape into the orchestrator every other role shares.
- **Reuse `publish_pending_review` as it is** and publish one review per invocation, letting repeated
  sweeps drain the queue. Rejected: it depends on `pending[-1]` order and would publish in reverse,
  with no guarantee every clip is reached.
- **Create all thirteen Artifacts from one Task.** Rejected: Output→Artifact is 1:1 (ADR-0006), and
  one Task would leave thirteen clips with one shared trace and one shared retry.

## References

- `PROJECT.md` §4 п.11, §12; `CLAUDE.md` Conventions
- `domain/run.py` — `open_evaluation`, `open_human_review`, `transition_artifact`,
  `_has_approved_review`, the transition table
- `application/review_publication.py` — `pending_review`, `latest_qa`
- ADR-0006 (Output→Artifact 1:1), ADR-0007 (review on an Artifact), ADR-0018 (the fail-closed gate),
  ADR-0026 (commit points, positional Task matching, resumption), ADR-0032 (rework appends a Task),
  ADR-0044/0045 (publication and decision, single-review), ADR-0048 (application code beside the
  Director), ADR-0053, ADR-0055 §6, ADR-0056, ADR-0058 §4
