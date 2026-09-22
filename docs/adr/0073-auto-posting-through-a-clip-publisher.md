# ADR-0073: Auto-posting an approved clip through a `ClipPublisher` — upload-post behind it

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer (auto-posting wanted, upload-post chosen, YouTube connected,
  2026-09-22); Lead Architect
- **Serves:** `CLAUDE.md` queue task 31 (and 21.7, "automatic publishing — additive Adapters")

## Context

An approved clip (QA `passed` + human Approve, ADR-0018) now carries burnt captions (ADR-0071) and
a post text the reviewer has seen and may have edited (ADR-0072). The maintainer wants it posted
automatically, chose **upload-post** (one API for YouTube, TikTok, Instagram; they own the
platform apps and audits), created the profile `concept` and connected YouTube.

upload-post's `POST /api/upload` (multipart) takes `user`, `platform[]`, `video`, `title`,
`description`, platform overrides and `async_upload`. It accepts a **client-provided `request_id`**,
honours an **`Idempotency-Key`** header (a repeated request returns the existing job) and reports
progress at `GET /api/uploadposts/status?request_id=…` (`pending`/`queued`/`processing`/
`in_progress`/`completed`/`failed`/`not_found`, with per-platform results).

Two constraints from the domain shape the orchestration:

- `Run.restore` refuses a `COMPLETED` Run that owns a non-terminal Task, and `COMPLETED` has no
  outgoing edge. A publication that is still in flight cannot live on a finished Run.
- `APPROVED → PUBLISHED` exists (ADR-0007) and is gated on approval; nothing has used it yet.

## Decision

### 1. A role-named port, `ClipPublisher`

`adapters/clip_publisher.py`:

- `PublishRequest(request_id, video_path, post: PostDraft, platforms)` — `request_id` is ours;
- `submit(request) -> None` — hand the file over; **idempotent per `request_id`**;
- `status(request_id) -> PublishStatus(state, results)` — `state ∈ {PENDING, COMPLETED, FAILED,
  NOT_FOUND}`, `results` one `PlatformResult(platform, success, url?, message?)` per platform;
- `ClipPublisherError` for anything technical. The vendor is not in the contract.

### 2. upload-post behind it, over stdlib `urllib`

`infrastructure/upload_post_publisher.py`, no new dependency (multipart built by hand, as every
vendor here is reached through `urllib`). `submit` sends `async_upload=true`, our `request_id`, and
the same value as `Idempotency-Key`. Text: `title` from the post; `youtube_description` and
`description` from the post; for TikTok and Instagram, whose caption is the title field, title and
description joined. YouTube: `privacyStatus` from configuration, `selfDeclaredMadeForKids=false`.
Configuration: `OMEMO_UPLOAD_POST_API_KEY`, `OMEMO_UPLOAD_POST_PROFILE`,
`OMEMO_UPLOAD_POST_PLATFORMS` (comma list, default `youtube`) and
`OMEMO_UPLOAD_POST_YOUTUBE_PRIVACY` (`public` / `unlisted` / `private`, **required** — whether the
channel sees a post is the maintainer's call, not a default). The key never appears in a message.

### 3. Publishing happens before the Run completes, while it waits at `WAITING_HUMAN`

When a publisher is configured, `ClipProduction` publishes every `APPROVED` clip, and the Run
completes only once each of them has a **terminal** `publish-clip` Task. Without a publisher the
department behaves exactly as before (approved is the end). No Run edge is added.

Per approved clip, one Task at step `publish-clip`, agent ref `clip_publisher@v1`, with a
deterministic `request_id` = `<artifact_id>-publish`:

1. **No post text** (`latest_post` is `None`, ADR-0072) → the Task fails `NO_POST_TEXT`; nothing is
   sent. A clip is never posted without a text a human has seen.
2. Otherwise the Task is opened `RUNNING` and **committed before any outside call** (ADR-0026 §2).
3. Each invocation asks `status(request_id)` first; **only `NOT_FOUND` leads to `submit`**. So a
   crash anywhere — before the submit, after it, before the save — resumes by looking, never by
   blindly re-posting; and the `Idempotency-Key` covers the one window where the vendor has taken
   the job but not yet made it visible.
4. `COMPLETED` with every platform successful → the Task succeeds with an Output (the request id and
   each platform's URL, `clip-publication@v1`) and the Artifact goes `APPROVED → PUBLISHED`.
   `FAILED`, or any platform unsuccessful → the Task fails `PUBLISH_FAILED: <platform>: <message>`
   and the Artifact stays `APPROVED`. **No automatic retry:** a partial post is already public on
   some platform, and posting again is the maintainer's decision.
5. `PENDING` → the Task stays `RUNNING`; the next invocation looks again. An invocation does not
   sleep-poll: the operator (or a schedule) runs it again.

### 4. What this does not do

Scheduling (`scheduled_date`, queue), per-platform first comments, thumbnails, reframing to 9:16,
deleting a post, and reading platform analytics are all out of scope.

## Consequences

### Positive

- An approved clip reaches the platform with no manual step, and the Run records where it went.
- A post is at most once by construction (look before submit + our `request_id` + the vendor's
  idempotency), not by hoping a crash never happens at the wrong moment.

### Negative / Trade-offs

- A Run now waits at `WAITING_HUMAN` while its approved clips upload — the name fits "waiting on
  the outside world" less than ideally; a new status would change `Run`, which the Conventions
  forbid for this.
- A clip approved in a Run that already completed (the first episode's clip 1) cannot be posted
  by this path; it was also rendered before captions and post texts existed.
- Collecting a finished upload needs another invocation.

## Alternatives considered

- **Synchronous upload.** upload-post switches to async after 59 s anyway, and a blocking call
  cannot be committed around.
- **A separate publication Run per clip.** Keeps the clip Run untouched and could post clips from
  finished Runs, but leaves `PUBLISHED` unused and adds a second orchestrator; revisit if posting
  to more platforms later needs it.
- **Official platform APIs.** Free, but an app and an audit per platform; the maintainer chose
  upload-post.

## References

- ADR-0007 (`APPROVED → PUBLISHED`), ADR-0018, ADR-0026 §2, ADR-0059, ADR-0066 (never resubmit
  blindly), ADR-0071, ADR-0072; upload-post API: `POST /api/upload`, `GET /api/uploadposts/status`;
  `CLAUDE.md` queue tasks 21.7, 31
