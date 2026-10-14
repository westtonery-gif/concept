# ADR-0099: Direct Instagram and TikTok publishers

- **Status:** Accepted
- **Date:** 2026-10-14
- **Extends:** ADR-0073 (the `ClipPublisher` port), the direct YouTube publisher (2026-10-13)

## Context

Clips reach YouTube directly through `YouTubePublisher`. The maintainer wants Instagram Reels
and TikTok the same way, without upload-post's paid plan. Both vendors' own documentation was
read on 2026-10-14 and it fixes most of the shape:

- **Instagram.** The Instagram API with *Instagram Login* accepts a reel only as a `video_url`
  on a public server. With *Facebook Login for Business* a reel can be uploaded from a local
  file: `POST /<ig-user>/media` with `upload_type=resumable` returns a container and an
  `rupload.facebook.com` URI; the bytes go there; the container's `status_code` turns
  `FINISHED`; `POST /<ig-user>/media_publish` makes it public. The account must be a
  professional one linked to a Facebook Page. Limits: 300 MB, at most 1920 px wide, 3 s–15 min,
  25 Mbps, caption 2200 characters / 30 hashtags.
- **TikTok.** The Content Posting API uploads a local file in chunks (5–64 MB, the last up to
  128 MB, sequential) to an `upload_url` returned by an init call. Two init routes: `inbox`
  (scope `video.upload`) puts the video into the creator's TikTok inbox as a draft, and the
  creator finishes the post in the app; `direct` (scope `video.publish`) posts it. **Until the
  app passes TikTok's audit every direct post is private** (`SELF_ONLY`). A desktop app logs in
  with PKCE (hex SHA-256 challenge) and a `http://127.0.0.1:*/callback/` redirect; access tokens
  live 24 h, refresh tokens 365 days. Neither init route takes an idempotency key.

The maintainer chose (2026-10-14): Instagram through a Facebook Page; TikTok direct, with the
audit applied for, and **drafts while the audit is pending**.

## Decision

1. **Two adapters behind the unchanged `ClipPublisher` port**, in `infrastructure/`, stdlib
   `urllib` only (no Meta or TikTok SDK): `InstagramPublisher` and `TikTokPublisher`. A post's
   text on both is the approved title, a blank line, then the description.
2. **A local receipt is the idempotency key, as for YouTube.** The receipt is reserved with an
   exclusive create *before the first irreversible request*, and each answer that moves the
   job forward is written into it. An outcome the receipt cannot prove blocks a repeat with an
   error that tells the operator to check the platform; nothing is resubmitted blindly.
   - Instagram: the container id is saved after creation, `uploaded` after the bytes, and
     `publishing` is written **before** `media_publish`. A job saved before `publishing` is safe
     to continue: a later `status` checks the container and publishes it once `FINISHED`. A job
     at `publishing` without a media id is uncertain and blocks.
   - TikTok: `publish_id` is saved right after init. Without it (a crash between init and the
     write) the job is uncertain and blocks. With it, `status/fetch` answers.
3. **TikTok's mode is configuration.** `OMEMO_TIKTOK_MODE=inbox` (default) or `direct`;
   `OMEMO_TIKTOK_PRIVACY` (default `SELF_ONLY`) is checked against the creator's
   `privacy_level_options` before a direct post. After the audit, only `.env` changes.
   **An inbox draft is not a publication**: `SEND_TO_USER_INBOX` reads as `PENDING` and the
   clip becomes `PUBLISHED` only when TikTok reports `PUBLISH_COMPLETE` — the Run never claims a
   post the creator has not made.
4. **Several platforms at once.** `OMEMO_POSTING_PROVIDER` may name a comma-separated list
   (`youtube,instagram,tiktok`). `FanOutPublisher` gives each platform its own request id
   (`<request_id>-<platform>`), submits only the platforms that have never seen theirs, and
   reports `PENDING` until every platform has settled. One platform keeps the publisher it had,
   so existing YouTube receipts stay valid.
5. **Setup scripts, as for YouTube.** `configure_instagram.py` exchanges a user token from the
   Graph API Explorer for a long-lived one and stores the token of the Page whose Instagram
   username matches `--handle`. `configure_tiktok.py` runs the desktop PKCE login. Tokens are
   files under `.omemo/` with mode 0600, never printed. `post_clip.py` previews a local clip and
   posts it to one platform only with the preview's approval fingerprint, like `post_youtube.py`.

## Consequences

- A clip rendered on the 2160×3840 canvas (ADR-0076) is too wide for Instagram; the adapter
  refuses files over 300 MB and Instagram reports a wrong size as a container `ERROR`. The local
  factory's 720×1280 output fits.
- Instagram allows a few minutes for processing; `submit` waits up to a configured time and then
  leaves the job for the next `status`.
- TikTok allows at most 5 pending inbox drafts per 24 h, so draft mode suits a few clips a day.
- Neither adapter has been run against the real service yet; the tests drive the real HTTP code
  against local servers playing each API.

## Deferred

Hosting files for the Instagram Login route; cover frames; TikTok's music/brand-content
disclosure toggles beyond `false`; resuming an interrupted TikTok chunk upload.
