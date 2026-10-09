# ADR-0092: An unattended post queue for clips made outside a Run

- **Status:** Accepted
- **Date:** 2026-10-09
- **Deciders:** Maintainer ("смысл фабрики — её автоматизированность, надо чинить",
  2026-10-09); Lead Architect
- **Builds on:** ADR-0073 (the `ClipPublisher` port and upload-post), ADR-0077 (a clip posted by
  hand), ADR-0078 (post text)

## Context

Posting a clip went through a person: an agent session ran a one-off script, and the session's own
permission layer refused it three times ("real-world transaction") until the maintainer allowed it
by hand. The factory itself was never the obstacle — `scripts/run_clips.sh` already posts under
launchd with no model in the loop (ADR-0073, task 34) — but that path only serves clips that
belong to a **Run**: an episode card on the Notion board, a QA verdict, a review. Clips cut from a
file by `demo_cut.py` (a scene picked by a model or a person) belong to no Run, so the only way to
post them was the manual one. A factory that needs someone to say "yes" per upload is not one.

## Decision

1. **A post queue is a folder.** Each job is `NAME.json` with `video` (a path, absolute or relative
   to the folder), `title` and `description` — exactly what `PostDraft` is (title ≤ 100). Any
   producer can drop one: `demo_cut.py`, a person, a future scene-finder agent.
2. **One run of the queue is one pass, and a schedule makes the runs.** In file-name order, per job:
   - `NAME.done.json` or `NAME.failed.json` exists → nothing to do, and **no publisher call**;
   - ask the publisher for the status of `request_id = NAME-publish` **first** (ADR-0073 §3);
     `COMPLETED` → write `NAME.done.json` with the per-platform results; `FAILED` → write
     `NAME.failed.json`, **never retried by the queue**; `PENDING` → the job is in flight;
   - `NOT_FOUND` → submit, but only if fewer than `max_new_per_run` jobs were submitted in this
     pass **and none is in flight** (pacing: one clip at a time, the same rule as ADR-0073's
     `max_new_per_invocation`).
   The queue keeps **no other state**: the publisher's own record under the `request_id` is the
   truth, the two marker files only stop it asking forever. A crash between submit and marker is
   harmless — the next pass sees `PENDING`/`COMPLETED` and submits nothing.
3. **A fault stops the pass, a bad job does not.** A `ClipPublisherError` (outage, bad key)
   propagates and **nothing is marked**; the next pass starts again. A malformed job file, a
   missing video or a title over the limit is reported as `invalid` and skipped — the rest go on.
4. **Where it lives.** `infrastructure/file_post_queue.py` (a folder is a storage detail, so it is
   an adapter-layer module over the `ClipPublisher` port), entrypoint `demo_post_queue.py <folder>`,
   `scripts/run_post_queue.sh` + `scripts/com.concept.post-queue.plist` (launchd, every 2 hours).
   `OMEMO_UPLOAD_POST_MAX_NEW_PER_RUN` is reused. **No change to `ClipPublisher`, `Run`,
   `ClipProduction`.**

## Acceptance

| Row | Proves |
|---|---|
| PQU-01 | with two jobs and `max_new_per_run=1`, one pass submits exactly the first |
| PQU-02 | a pass while the first is `PENDING` submits nothing (one in flight at a time) |
| PQU-03 | once it completes, the next pass writes `done.json` and submits the second |
| PQU-04 | a pass over a finished queue makes **no publisher call at all** |
| PQU-05 | a crash after submit before any marker → the next pass does not submit again |
| PQU-06 | an outage raises, marks nothing, and a later pass recovers |
| PQU-07 | a failed job gets `failed.json` and is never resubmitted |
| PQU-08 | a malformed job (bad JSON, missing video, long title) is `invalid`, others still post |

## Consequences

- **Good:** posting a clip needs no model and no confirmation; the maintainer's one-time act is
  installing the agent. Any clip source feeds it. The publisher stays the single source of truth.
- **Cost / not done:** the *choice* of which scene becomes a clip is still a model-or-person act
  (queue task 25's planner); this ADR only removes the human from the **posting**. The queue does
  not review — a clip in the folder is one someone decided to post; the Run path (QA + Human
  Review) is still the gated one. `unlisted` stays the default privacy (ADR-0073).
