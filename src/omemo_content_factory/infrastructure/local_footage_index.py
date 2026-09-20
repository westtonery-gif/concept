"""A real ``FootageIndex`` over ffmpeg and whisper.cpp — no vendor (ADR-0064).

Three local tools answer the three things the port owes:

- ``ffprobe`` — how long the episode is;
- ffmpeg's ``scdet`` filter — where scenes change;
- ``whisper-cli`` (whisper.cpp) — what is said and when, with millisecond offsets.

All of it runs offline on the operator's machine, which for licensed television is not a small
point: the footage never leaves it. Only ``infrastructure/`` may reach outside the process
(``tests/test_adapter_contract.py``), which is why the subprocess calls live here and the rest of
the department never learns these tools exist.

Whisper's own word timings are what the plan later uses to nudge a cut away from the middle of a
word, so ffmpeg's ``silencedetect`` is deliberately **not** consulted: two differently-derived
answers to "where is the pause" would be one answer too many (ADR-0064 §3).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from omemo_content_factory.adapters.episode_source import LocatedEpisode
from omemo_content_factory.adapters.footage_index import (
    FootageIndexError,
    IndexedFootage,
    SceneBreak,
    SpeechSpan,
)

__all__ = [
    "LANGUAGE_VAR",
    "MODEL_VAR",
    "SCENE_THRESHOLD_VAR",
    "LocalFootageIndex",
    "WhisperSettings",
    "whisper_settings_from_env",
]

MODEL_VAR = "OMEMO_WHISPER_MODEL"
LANGUAGE_VAR = "OMEMO_WHISPER_LANGUAGE"
SCENE_THRESHOLD_VAR = "OMEMO_SCENE_THRESHOLD"

_DEFAULT_SCENE_THRESHOLD = 10.0
"""``scdet``'s score above which a frame is a cut. A real-episode question (ADR-0064 Deferred)."""

_TIMEOUT_SECONDS = 3600.0
"""Transcribing 25 minutes is minutes of work; an hour means something has hung."""

_SCENE_TIME = re.compile(r"lavfi\.scd\.time=([0-9]+(?:\.[0-9]+)?)")


class WhisperSettings:
    """Where the model is and what language to expect. ``__slots__``, built from the environment."""

    __slots__ = ("language", "model", "scene_threshold")

    def __init__(self, *, model: Path, language: str, scene_threshold: float) -> None:
        if not model.is_file():
            raise FootageIndexError(f"the Whisper model is missing: {model}")
        if not language.strip():
            raise FootageIndexError("a Whisper language is required; use 'auto' to detect it")
        if scene_threshold <= 0:
            raise FootageIndexError("the scene threshold must be positive")
        self.model = model
        self.language = language.strip()
        self.scene_threshold = scene_threshold


def whisper_settings_from_env(environ: Mapping[str, str]) -> WhisperSettings:
    """Read the model path (required), the language and the scene threshold."""
    raw_model = environ.get(MODEL_VAR, "").strip()
    if not raw_model:
        raise FootageIndexError(
            f"the footage index is not configured; set {MODEL_VAR} to a GGML model file"
        )
    raw_threshold = environ.get(SCENE_THRESHOLD_VAR, "").strip()
    try:
        threshold = float(raw_threshold) if raw_threshold else _DEFAULT_SCENE_THRESHOLD
    except ValueError:
        raise FootageIndexError(f"{SCENE_THRESHOLD_VAR} must be a number") from None
    return WhisperSettings(
        model=Path(raw_model),
        language=environ.get(LANGUAGE_VAR, "").strip() or "auto",
        scene_threshold=threshold,
    )


class LocalFootageIndex:
    """Indexes an episode with ffprobe, ffmpeg's ``scdet`` and whisper.cpp."""

    def __init__(
        self,
        settings: WhisperSettings,
        *,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        whisper: str = "whisper-cli",
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._settings = settings
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._whisper = whisper
        self._timeout = timeout

    def index(self, located: LocatedEpisode, /) -> IndexedFootage:
        """Analyse the episode. Slow by nature — a Workflow step, never a reasoning-step call."""
        source = Path(located.path)
        if not source.is_file():
            raise FootageIndexError(f"the episode file is missing: {located.path}")
        duration_ms = self._duration_ms(source)
        scenes = self._scenes(source, duration_ms)
        speech = self._speech(source, duration_ms)
        return IndexedFootage(duration_ms=duration_ms, scenes=scenes, speech=speech)

    # --- duration -----------------------------------------------------------------------

    def _duration_ms(self, source: Path) -> int:
        raw = self._run(
            [
                self._ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            what="measure the episode",
        )
        try:
            milliseconds = round(float(raw.strip()) * 1000)
        except ValueError as error:
            raise FootageIndexError("ffprobe reported no usable duration") from error
        if milliseconds <= 0:
            raise FootageIndexError("the episode has no length")
        return milliseconds

    # --- scenes -------------------------------------------------------------------------

    def _scenes(self, source: Path, duration_ms: int) -> tuple[SceneBreak, ...]:
        """Scene-change timestamps, strictly increasing and inside the episode.

        ``scdet`` sets ``lavfi.scd.time`` only on the frames it judges to be cuts, so the metadata
        filter prints one line per detected change and nothing otherwise. A luminance heuristic
        misses a cut between two similar shots and invents one across a flash — which is why the
        threshold is configuration and the first real episode decides it.
        """
        printed = self._run(
            [
                self._ffmpeg,
                "-nostdin",
                "-i",
                str(source),
                "-vf",
                f"scdet=threshold={self._settings.scene_threshold:g},"
                "metadata=print:key=lavfi.scd.time:file=-",
                "-f",
                "null",
                "-",
            ],
            what="detect scenes",
            stderr_too=True,
        )
        seen: list[int] = []
        for match in _SCENE_TIME.finditer(printed):
            at_ms = round(float(match.group(1)) * 1000)
            if 0 < at_ms < duration_ms and (not seen or at_ms > seen[-1]):
                seen.append(at_ms)
        return tuple(SceneBreak(at_ms=at_ms) for at_ms in seen)

    # --- speech -------------------------------------------------------------------------

    def _speech(self, source: Path, duration_ms: int) -> tuple[SpeechSpan, ...]:
        """Transcribe through whisper.cpp and return ordered, non-overlapping spans."""
        with tempfile.TemporaryDirectory() as workspace:
            audio = Path(workspace) / "audio.wav"
            self._run(
                [
                    self._ffmpeg,
                    "-nostdin",
                    "-y",
                    "-i",
                    str(source),
                    "-vn",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(audio),
                ],
                what="extract the audio",
            )
            stem = Path(workspace) / "transcript"
            command = [
                self._whisper,
                "-m",
                str(self._settings.model),
                "-f",
                str(audio),
                "-oj",
                "-of",
                str(stem),
                "-np",
            ]
            if self._settings.language != "auto":
                command += ["-l", self._settings.language]
            self._run(command, what="transcribe the episode", stderr_too=True)
            report = stem.with_suffix(".json")
            if not report.is_file():
                raise FootageIndexError("whisper wrote no transcript")
            return _spans(_load(report), duration_ms)

    # --- running ------------------------------------------------------------------------

    def _run(self, command: list[str], *, what: str, stderr_too: bool = False) -> str:
        """Run one tool. Anything but a clean exit is a ``FootageIndexError``, never a guess."""
        if shutil.which(command[0]) is None:
            raise FootageIndexError(f"{command[0]} is not installed; cannot {what}")
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise FootageIndexError(f"{command[0]} timed out trying to {what}") from error
        except OSError as error:
            raise FootageIndexError(f"{command[0]} could not be run: {error}") from error
        if completed.returncode != 0:
            raise FootageIndexError(
                f"{command[0]} could not {what} (exit {completed.returncode}): "
                f"{_last_line(completed.stderr)}"
            )
        return completed.stdout + completed.stderr if stderr_too else completed.stdout


def _load(report: Path) -> object:
    try:
        return json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise FootageIndexError("whisper wrote a transcript that is not JSON") from error


def _spans(report: object, duration_ms: int) -> tuple[SpeechSpan, ...]:
    """Turn whisper.cpp's segments into spans the port will accept.

    Its output is a transcription of a machine listening to speech, not a contract: a segment can
    carry empty text, can end past the audio, and two can overlap by a few milliseconds. Each of
    those would be refused by ``IndexedFootage``, so they are cleaned here rather than propagated —
    and a cleaned segment is dropped, never invented.
    """
    if not isinstance(report, dict):
        raise FootageIndexError("whisper's transcript is not a JSON object")
    segments = report.get("transcription")
    if not isinstance(segments, list):
        raise FootageIndexError("whisper's transcript has no transcription")

    spans: list[SpeechSpan] = []
    end_of_previous = 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        offsets = segment.get("offsets")
        text = segment.get("text")
        if not isinstance(offsets, dict) or not isinstance(text, str) or not text.strip():
            continue
        start, end = offsets.get("from"), offsets.get("to")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        start = max(start, end_of_previous)
        end = min(end, duration_ms)
        if end <= start:
            continue
        spans.append(SpeechSpan(start_ms=start, end_ms=end, text=text.strip()))
        end_of_previous = end
    return tuple(spans)


def _last_line(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[-1] if lines else "no output"
