# ADR-0071: Captions are burnt in at render time, so the reviewer approves what ships

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Maintainer (wants burnt-in subtitles, installed an ffmpeg with `libass`,
  2026-09-22); Lead Architect (when in the pipeline)
- **Supersedes:** ADR-0063 §1 (the renderer ignores captions). ADR-0063 §2 is what makes this a
  one-module change, and stands.
- **Serves:** `CLAUDE.md` queue task 29

## Context

ADR-0063 shipped clips without captions because the installed ffmpeg had neither `libass` nor
`freetype`. The maintainer has since installed `homebrew-ffmpeg/ffmpeg` (9.0.2), whose `subtitles`
filter renders through `libass` (checked with `ffmpeg -h filter=subtitles`). Every clip already
carries its captions, timed from the clip's own start (ADR-0062), so the capability is a change
inside `FfmpegClipRenderer`, as ADR-0063 §2 promised.

The maintainer asked for subtitles "после одобрения". Read literally that is a second render after
the human Approve. Two things argue for burning at the first render instead:

- **The reviewer should approve the file that ships.** Burnt captions carry whisper's mistakes
  (task 24.2 found some); a reviewer who approved a clean cut would never see them, and the gate
  exists precisely so that nothing reaches the audience unseen (`PROJECT.md` §12, ADR-0018).
- **The Run is `COMPLETED` once every clip is decided** (ADR-0059 §3); a post-approval render would
  need new Tasks on a finished Run, a question better left to publishing (task 31), which has to
  answer it anyway.

The order the maintainer cares about — approved first, then posted with subtitles — is unchanged.

## Decision

### 1. `FfmpegClipRenderer` burns `request.captions`

It writes the captions to a temporary ASS file it owns and adds ffmpeg's `subtitles` filter to the
cut. Empty captions mean no filter: a clip with no speech is a normal clip. The subtitle format
stays out of the port (ADR-0062 §3). A caption's text cannot inject ASS markup: override braces
become parentheses, backslashes become slashes, line breaks become spaces.

### 2. The look: large, bottom-centre, readable letterboxed

White bold sans-serif (Arial, present on macOS with Cyrillic) with a black outline, bottom-centre,
sized for a 1080-line frame (72 px on a 1920×1080 canvas, scaled by `libass` to the real
resolution). The clips stay 16:9 (ADR-0058) and are letterboxed in a 9:16 feed, where the picture
occupies about a third of the screen — small captions would be unreadable there. The font and size
are constructor parameters with these defaults; they become configuration when someone asks.

### 3. An ffmpeg without `libass` fails loudly

If the filter is missing, the render fails as a `ClipRendererError` that says so, and the clip's
Task fails with `CLIP_RENDER_FAILED` (ADR-0059 §3) — never a silently caption-less clip.

## Consequences

### Positive

- What the reviewer approves is byte-for-byte what task 31 will post.
- No new step, Task or Artifact shape; the captions stored since ADR-0062 are simply drawn.

### Negative / Trade-offs

- Every clip is burnt, including the ones that will be rejected — CPU only, seconds per clip.
- A caption typo cannot be fixed without re-rendering; editing the text is not a v1 feature.
- An operator machine with a stock Homebrew ffmpeg now fails every clip until it installs the tap
  build — the runbook says so.

## Alternatives considered

- **Burn after approval, as a second render.** The reviewer would approve a file that is not the
  one posted, and the step needs Tasks on a `COMPLETED` Run. Rejected for the two reasons above.
- **Sidecar `.srt` only.** TikTok and Reels do not accept one; the maintainer chose burn-in.
- **Fall back to no captions when `libass` is missing.** A silent quality drop; rejected per §3.

## References

- ADR-0018, ADR-0058, ADR-0059 §3, ADR-0062, ADR-0063; `CLAUDE.md` queue tasks 24, 29, 31
