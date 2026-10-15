# ADR-0100: upload-post is removed; posting is direct only

- **Status:** Accepted
- **Date:** 2026-10-15
- **Deciders:** Maintainer ("удали сервис upload раз мы перешли на прямой автопостинг")
- **Supersedes:** ADR-0073 §2 (upload-post as the vendor). The `ClipPublisher` port, ADR-0073 §1
  and §3 (look before submitting, no automatic retry) stand unchanged.
- **Amends:** ADR-0092 (the post queue's publisher), ADR-0099 §4 (`OMEMO_POSTING_PROVIDER`)

## Context

ADR-0073 put upload-post behind the `ClipPublisher` port because it owned the platform apps and
their audits. YouTube (2026-10-13) and then Instagram and TikTok (ADR-0099) now have direct
publishers behind the same port, and the maintainer chose to drop the paid service. Keeping a
second route that nothing uses would leave a default (`OMEMO_POSTING_PROVIDER` unset meant
upload-post) pointing at an account the factory no longer means to pay for.

## Decision

1. `UploadPostPublisher`, its settings and its tests are deleted. The port and
   `InMemoryClipPublisher` stay.
2. `OMEMO_POSTING_PROVIDER` is **required** wherever posting is built and names one or more of
   `youtube`, `instagram`, `tiktok`. There is no default: posting to an unnamed place is
   refused at build time, never guessed. `composition.build_publisher(environ)` is the single
   selection, used by `build_posting` and by the post queue.
3. The pacing variable is renamed `OMEMO_POSTING_MAX_NEW_PER_RUN` (it was
   `OMEMO_UPLOAD_POST_MAX_NEW_PER_RUN`, a name that no longer says what it paces).
4. The post queue (ADR-0092) posts through the direct publishers. A job's `request_id` stays
   `NAME-publish`; jobs already marked `.done.json` or `.failed.json` are never asked about.
   A job submitted to upload-post and still in flight is unknown to the direct publishers and
   would be posted again — none exists at the time of this decision (every queued job has a
   `.done.json`).
5. `doctor.py posting` checks `OMEMO_POSTING_PROVIDER` and the variables of each platform it
   names.

## Consequences

- An `.env` without `OMEMO_POSTING_PROVIDER` can no longer publish until it names a platform.
- The upload-post account and its API key are the maintainer's to cancel; the code no longer
  reads `OMEMO_UPLOAD_POST_*`.
- Earlier ADRs and the development notes still describe upload-post; they are history.
