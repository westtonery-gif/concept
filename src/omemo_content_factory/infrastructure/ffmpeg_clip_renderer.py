"""A real ``ClipRenderer`` over the ffmpeg command line (ADR-0063).

``ffmpeg`` cuts and re-encodes one interval; ``ffprobe`` measures what came out. The measurements
are read back from the produced file rather than assumed from the request, which is the whole point
of ``RenderedClip`` carrying them: the deterministic format check (ADR-0056 §1) then compares real
numbers, and a renderer that quietly produced something else is caught instead of trusted.

**This renderer does not burn captions** (ADR-0063 §1). The ffmpeg available here was built without
``libass`` and without ``libfreetype``, so it has no ``subtitles``, ``ass`` or ``drawtext`` filter
and cannot draw text on a picture at all. ``ClipRenderRequest.captions`` is therefore **ignored
here and only here** — it is still produced, still carried and still stored in the clip's Artifact,
so burning it later is a change inside this module and nothing else.

Only ``infrastructure/`` may reach outside the process (``tests/test_adapter_contract.py``), which
is why the subprocess calls live here and the rest of the department never learns that ffmpeg
exists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from omemo_content_factory.adapters.clip_renderer import (
    ClipRendererError,
    ClipRenderRequest,
    RenderedClip,
)

__all__ = ["DEFAULT_FFMPEG", "DEFAULT_FFPROBE", "FfmpegClipRenderer"]

DEFAULT_FFMPEG = "ffmpeg"
DEFAULT_FFPROBE = "ffprobe"

_TIMEOUT_SECONDS = 600.0
"""Cutting a two-minute clip out of a 25-minute file is seconds; ten minutes is a hung process."""


class FfmpegClipRenderer:
    """Cuts one clip with ffmpeg and measures it with ffprobe."""

    def __init__(
        self,
        *,
        ffmpeg: str = DEFAULT_FFMPEG,
        ffprobe: str = DEFAULT_FFPROBE,
        timeout: float = _TIMEOUT_SECONDS,
        video_codec: str = "libx264",
        audio_codec: str = "aac",
    ) -> None:
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._timeout = timeout
        self._video_codec = video_codec
        self._audio_codec = audio_codec

    def render(self, request: ClipRenderRequest, /) -> RenderedClip:
        """Write the clip and return what ffprobe says it actually is.

        ``request.captions`` is ignored — see this module's docstring and ADR-0063.
        """
        source = Path(request.located.path)
        if not source.is_file():
            raise ClipRendererError(f"the episode file is missing: {request.located.path}")
        destination = Path(request.destination)
        if destination.parent != Path():
            destination.parent.mkdir(parents=True, exist_ok=True)

        self._run(
            [
                self._ffmpeg,
                "-nostdin",
                "-y",
                "-ss",
                _seconds(request.start_ms),
                "-i",
                str(source),
                "-t",
                _seconds(request.end_ms - request.start_ms),
                "-c:v",
                self._video_codec,
                "-c:a",
                self._audio_codec,
                str(destination),
            ],
            what="cut the clip",
        )
        if not destination.is_file():
            raise ClipRendererError(f"ffmpeg reported success but wrote no file: {destination}")
        return self._measure(destination)

    # --- measuring ----------------------------------------------------------------------

    def _measure(self, destination: Path) -> RenderedClip:
        probed = self._run(
            [
                self._ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(destination),
            ],
            what="measure the clip",
        )
        try:
            report = json.loads(probed)
        except ValueError as error:
            raise ClipRendererError("ffprobe returned output that is not JSON") from error
        if not isinstance(report, dict):
            raise ClipRendererError("ffprobe returned output that is not a JSON object")

        video = _first_video_stream(report)
        return RenderedClip(
            path=str(destination),
            duration_ms=_duration_ms(report),
            width=_positive(video, "width"),
            height=_positive(video, "height"),
            container=_container(report, destination),
        )

    # --- running ------------------------------------------------------------------------

    def _run(self, command: list[str], *, what: str) -> str:
        """Run one tool. Anything but a clean exit is a ``ClipRendererError``, never a guess."""
        if shutil.which(command[0]) is None:
            raise ClipRendererError(f"{command[0]} is not installed; cannot {what}")
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ClipRendererError(f"{command[0]} timed out trying to {what}") from error
        except OSError as error:
            raise ClipRendererError(f"{command[0]} could not be run: {error}") from error
        if completed.returncode != 0:
            raise ClipRendererError(
                f"{command[0]} could not {what} (exit {completed.returncode}): "
                f"{_last_line(completed.stderr)}"
            )
        return completed.stdout


def _seconds(milliseconds: int) -> str:
    """ffmpeg takes seconds; milliseconds are kept exactly, never rounded to a frame."""
    return f"{milliseconds / 1000:.3f}"


def _first_video_stream(report: dict[str, object]) -> dict[str, object]:
    streams = report.get("streams")
    if not isinstance(streams, list):
        raise ClipRendererError("ffprobe reported no streams")
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == "video":
            return stream
    raise ClipRendererError("the rendered clip has no video stream")


def _duration_ms(report: dict[str, object]) -> int:
    container = report.get("format")
    raw = container.get("duration") if isinstance(container, dict) else None
    try:
        milliseconds = round(float(str(raw)) * 1000)
    except (TypeError, ValueError) as error:
        raise ClipRendererError("ffprobe reported no usable duration") from error
    if milliseconds <= 0:
        raise ClipRendererError("ffprobe reported a clip of no length")
    return milliseconds


def _positive(stream: dict[str, object], field: str) -> int:
    value = stream.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ClipRendererError(f"ffprobe reported no usable {field}")
    return value


def _container(report: dict[str, object], destination: Path) -> str:
    """The container as ffprobe names it, preferring the spelling the file's suffix uses.

    ffprobe answers a family (``mov,mp4,m4a,3gp,3g2,mj2``) rather than one name, so the suffix
    picks which member of that family this file is — and if the suffix is not in the family at all,
    the family's own first name is used, because ffprobe is the measurement and the suffix is not.
    """
    container = report.get("format")
    raw = container.get("format_name") if isinstance(container, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raise ClipRendererError("ffprobe reported no container")
    names = [name.strip() for name in raw.split(",") if name.strip()]
    suffix = destination.suffix.lstrip(".").casefold()
    if suffix and suffix in names:
        return suffix
    return names[0]


def _last_line(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[-1] if lines else "no output"
