# ADR-0095 — Mandatory video processing before delivery

Status: Accepted (2026-10-09)

## Context

The operator requires every clipped video to have fresh metadata, a different SHA-256,
0.98x playback speed (audio pitch preserved), and a 94% image centred on black padding.
This writes files, so ADR-0054 excludes it from model Tools. The clipping production
path and the standalone cutting command enforce it. Generated videos and the general post
queue are explicitly out of scope and remain unchanged.

## Decision

- Add the pure `VideoProcessor` port in `adapters/video_processor.py` and the local
  `FfmpegVideoProcessor` in `infrastructure/`. No Toolbox grant or tool allowlist change.
- Clipping records raw rendering first, then a required `process-video` Task. Only the
  processed result becomes the clip Artifact, and its actual duration/resolution is checked
  before QA. Commit the Task before the external call and its Output/Artifact after success.
  A processing failure fails that clip, never falls back to the raw file, and leaves other
  clips independent. A restart reuses successful processing Tasks.
- The clipping workflow version becomes `episode-to-clips@v2`. Existing runs which have
  already produced legacy artifacts are not silently rewritten or shipped: publishing a legacy
  artifact without processing provenance fails closed and requires an explicit new production
  run/migration. Already submitted publication jobs are only collected, never resubmitted.
- `demo_cut.py` also invokes the required processing adapter for its exported clips.
- A stable request ID plus source hash, settings and destination identifies a render. The
  adapter reuses a completed, hash-verified result; it does not repeatedly slow a video.
  MP4 publication is atomic, using a complete temporary file on the same filesystem. A crash
  after MP4 publication but before the JSON report is recoverable from the embedded fingerprint.
  Unknown/conflicting files are refused, never overwritten. A previously processed input may
  be reused only when its adjacent report matches its bytes and the required settings.
- Product defaults: speed 0.98, scale 0.94, H.264 CRF 18, preset slow, AAC 192k. Settings belong
  to composition (`OMEMO_VIDEO_SPEED`, `OMEMO_VIDEO_SCALE`, `OMEMO_VIDEO_CRF`,
  `OMEMO_VIDEO_PRESET`, `OMEMO_FFMPEG`, `OMEMO_FFPROBE`), not to an LLM. There is no bypass flag.
- Original files are unchanged. Output dimensions are even, rotation is materialized, SAR is
  normalized, audio is optional, and HDR PQ/HLG is refused rather than incorrectly tone-mapped.
  SHA-256 change is a byte-level check, not a platform deduplication guarantee.

## Validation

Real FFmpeg tests measure duration, geometry, padding, audio and metadata; recovery tests
exercise report loss, changed source/settings, corrupted outputs and repeated requests.
Workflow tests prove failure blocks QA/artifact creation, resume does not process twice,
publication uses processed clip paths, and the standalone cutter cannot skip the gate. The existing
Run/domain/Toolbox behaviour and provider calls remain unchanged. The full local gate applies.

## Revision 2 — reference-based encoding profile (2026-10-09)

The reference export reduces the image to approximately 94% and keeps the input
frame rate while slowing playback. Profile 2 uses scale 0.94, speed 0.98, CFR
by frame duplication/dropping (no optical interpolation), square pixels, and a
maximum GOP of 60 frames. It retains CRF 18 / slow and AAC 192k to limit additional
compression loss. For VFR inputs the average input rate is used; for CFR inputs
the nominal rational rate is retained. Invalid rates fail before encoding.

Profile version, CFR policy and GOP participate in the processing fingerprint.
Existing profile-1 files cannot satisfy a profile-2 request and cannot be processed
again as inputs. Committed workflow tasks are retained; upgrading a completed clip
requires a new run from its raw source with an unused destination. Explicit
OMEMO_VIDEO_SCALE overrides continue to take precedence over the new default.

## Revision 3 — explicit visual variants (2026-10-10)

Profile 2 used the same visual transformation for every export: different file
hashes did not mean different decoded video. Profile 3 adds `look` (`clean`,
`subtle`, `strong`; default `subtle`) and opt-in horizontal `mirror`. Subtle and
strong vary rotation, contrast, saturation, gamma and sharpening within bounded
ranges. Rotation fits the entire picture into the existing canvas, including
when scale=1. Clean keeps the preceding geometry without these extra effects.

Effects are deterministic from the processing request fingerprint, and actual
parameters are included in the identity/report. Retries and recovery preserve
the same effects; prior-profile inputs are refused to avoid cumulative processing.
Composition owns OMEMO_VIDEO_LOOK and OMEMO_VIDEO_MIRROR (true/false). No change to
Run, publication, the model Tool channel or generated-video workflows. Audio keeps
the existing pitch-preserving tempo change. Music, dubbing and noise are not added
by this profile. Existing clip-renderer music configuration remains available.

Decoded video/audio regression tests distinguish actual pixel changes from file
hash changes, cover seeded reproducibility and unchanged audio between variants.
These checks do not certify perceptual originality, platform acceptance or rights.
