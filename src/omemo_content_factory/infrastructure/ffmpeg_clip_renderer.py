"""A real ``ClipRenderer`` over the ffmpeg command line (ADR-0063).

``ffmpeg`` cuts and re-encodes one interval; ``ffprobe`` measures what came out. The measurements
are read back from the produced file rather than assumed from the request, which is the whole point
of ``RenderedClip`` carrying them: the deterministic format check (ADR-0056 §1) then compares real
numbers, and a renderer that quietly produced something else is caught instead of trusted.

**Captions are burnt in** (ADR-0071, superseding ADR-0063 §1): they are written to a temporary ASS
file this module owns and drawn with ffmpeg's ``subtitles`` filter, which needs an ffmpeg built with
``libass`` (``homebrew-ffmpeg/ffmpeg``). The reviewer therefore approves the very file that ships.
An ffmpeg without the filter fails the render loudly rather than producing a caption-less clip.

Only ``infrastructure/`` may reach outside the process (``tests/test_adapter_contract.py``), which
is why the subprocess calls live here and the rest of the department never learns that ffmpeg
exists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from omemo_content_factory.adapters.clip_renderer import (
    Caption,
    ClipRendererError,
    ClipRenderRequest,
    FinishRequest,
    RenderedClip,
)

__all__ = ["DEFAULT_FFMPEG", "DEFAULT_FFPROBE", "FfmpegClipRenderer", "caption_colour"]

DEFAULT_FFMPEG = "ffmpeg"
DEFAULT_FFPROBE = "ffprobe"

_TIMEOUT_SECONDS = 600.0
"""Cutting a two-minute clip out of a 25-minute file is seconds; ten minutes is a hung process."""

DEFAULT_CAPTION_FONT = "Arial"
"""Present on macOS with Cyrillic; ``libass`` finds it through the system font lookup."""

DEFAULT_CAPTION_SIZE = 72
"""Pixels on a 1920×1080 canvas, scaled to the real frame: readable when a 16:9 clip is
letterboxed into a 9:16 feed, where the picture is about a third of the screen (ADR-0071 §2)."""

_NO_SUBTITLES_FILTER = "No such filter: 'subtitles'"

_NAMED_COLOURS = {"white": "ffffff", "yellow": "ffff00"}
"""The caption colours that have a name; anything else is spelt ``#RRGGBB``."""


def caption_colour(value: str) -> str:
    """A caption colour as ASS spells it (``&H00BBGGRR``) from ``white``/``yellow``/``#RRGGBB``."""
    text = value.strip().casefold()
    rgb = _NAMED_COLOURS.get(text, text.removeprefix("#"))
    if len(rgb) != 6 or any(char not in "0123456789abcdef" for char in rgb):
        raise ValueError("a caption colour is white, yellow or #RRGGBB")
    red, green, blue = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H00{blue}{green}{red}".upper()


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
        caption_font: str = DEFAULT_CAPTION_FONT,
        caption_size: int = DEFAULT_CAPTION_SIZE,
        caption_color: str = "white",
        canvas: tuple[int, int] | None = None,
        fill: str = "black",
        picture_height: float | None = None,
        music: str | None = None,
        music_volume: float = 0.2,
        crf: int = 18,
        preset: str = "medium",
    ) -> None:
        if not caption_font.strip() or "," in caption_font:
            raise ValueError("a caption font needs a non-blank name without commas")
        if caption_size <= 0:
            raise ValueError("a caption size must be positive")
        if canvas is not None and (
            len(canvas) != 2 or any(side <= 0 or side % 2 for side in canvas)
        ):
            raise ValueError("a canvas is two positive, even sides")
        if fill not in ("black", "blur"):
            raise ValueError("a canvas fill is 'black' or 'blur'")
        if picture_height is not None and not 0.3 <= picture_height <= 1:
            raise ValueError("a picture height is a fraction of the canvas, 0.3 to 1")
        if music is not None and not music.strip():
            raise ValueError("a music file needs a non-blank path")
        if not 0 < music_volume <= 1:
            raise ValueError("music volume is a fraction above 0 and up to 1")
        self._music = music.strip() if music else None
        self._music_volume = music_volume
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe
        self._timeout = timeout
        self._video_codec = video_codec
        self._audio_codec = audio_codec
        self._caption_font = caption_font.strip()
        self._caption_size = caption_size
        self._caption_colour = caption_colour(caption_color)
        self._canvas = canvas
        self._fill = fill
        self._picture_height = picture_height
        self._crf = crf
        self._preset = preset

    @property
    def _words_on_frame(self) -> bool:
        """Zoomed pictures are cropped at the sides, so captions go on the finished frame, one
        word at a time in the middle of the screen."""
        return (
            self._canvas is not None and self._fill == "blur" and self._picture_height is not None
        )

    @property
    def has_canvas(self) -> bool:
        """Whether clips get bars — the only place a headline and footer can go (ADR-0078).

        A zoomed picture leaves only narrow blurred strips, so there is nowhere to put them."""
        return self._canvas is not None and self._picture_height is None

    def _on_canvas(self, filters: list[str], after: list[str]) -> tuple[list[str], str | None]:
        """Put the picture on the canvas: padded black, or on a blurred copy (a graph)."""
        if self._canvas is None:
            return filters, None
        width, height = self._canvas
        if self._fill == "blur":
            return [], _blur_graph(filters, width, height, self._picture_height, after)
        # The same picture, uncropped, centred on our own bars (ADR-0075 §1).
        return [
            *filters,
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
        ], None

    def render(self, request: ClipRenderRequest, /) -> RenderedClip:
        """Write the clip with its captions burnt in; return what ffprobe says it actually is."""
        source = Path(request.located.path)
        if not source.is_file():
            raise ClipRendererError(f"the episode file is missing: {request.located.path}")
        destination = Path(request.destination)
        if destination.parent != Path():
            destination.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as workspace:
            filters: list[str] = []
            after: list[str] = []
            if request.captions:
                subtitles = Path(workspace) / "captions.ass"
                if self._words_on_frame:
                    assert self._canvas is not None
                    script = _word_ass(
                        request.captions,
                        width=self._canvas[0],
                        height=self._canvas[1],
                        font=self._caption_font,
                        colour=self._caption_colour,
                    )
                    target = after
                else:
                    script = _ass(
                        request.captions,
                        font=self._caption_font,
                        size=self._caption_size,
                        colour=self._caption_colour,
                    )
                    target = filters
                subtitles.write_text(script, encoding="utf-8")
                target.append(f"subtitles=filename={_filter_path(subtitles)}")
            filters, blur_graph = self._on_canvas(filters, after)
            burn = ["-vf", ",".join(filters)] if filters else []
            duration_s = (request.end_ms - request.start_ms) / 1000
            music_inputs: list[str] = []
            if self._music is not None:
                if not Path(self._music).is_file():
                    raise ClipRendererError(f"the music file is missing: {self._music}")
                # The track is looped under the clip, faded at both ends and kept well below the
                # original sound; the clip's own audio decides how long the result is.
                music_inputs = ["-stream_loop", "-1", "-i", self._music]
                audio_graph = (
                    f"[1:a]volume={self._music_volume},afade=t=in:st=0:d=1,"
                    f"afade=t=out:st={max(duration_s - 1.5, 0):.3f}:d=1.5[m];"
                    "[0:a][m]amix=inputs=2:duration=first:normalize=0[a]"
                )
                graph = f"{blur_graph};{audio_graph}" if blur_graph else audio_graph
                video_map = "[v]" if blur_graph else "0:v"
                burn = ["-filter_complex", graph, "-map", video_map, "-map", "[a]"]
                if not blur_graph and filters:
                    burn = ["-vf", ",".join(filters), *burn]
            elif blur_graph is not None:
                burn = ["-filter_complex", blur_graph, "-map", "[v]", "-map", "0:a?"]
            self._run(
                [
                    self._ffmpeg,
                    "-nostdin",
                    "-y",
                    "-ss",
                    _seconds(request.start_ms),
                    "-i",
                    str(source),
                    *music_inputs,
                    "-t",
                    _seconds(request.end_ms - request.start_ms),
                    *burn,
                    "-c:v",
                    self._video_codec,
                    "-crf",
                    str(self._crf),
                    "-preset",
                    self._preset,
                    "-c:a",
                    self._audio_codec,
                    str(destination),
                ],
                what="cut the clip",
            )
        if not destination.is_file():
            raise ClipRendererError(f"ffmpeg reported success but wrote no file: {destination}")
        return self._measure(destination)

    def finish(self, request: FinishRequest, /) -> RenderedClip:
        """Burn the headline into the top bar and the footer into the bottom one (ADR-0078)."""
        if self._canvas is None:
            raise ClipRendererError("a clip without a canvas has no bars to frame")
        source = Path(request.source_path)
        if not source.is_file():
            raise ClipRendererError(f"the clip to frame is missing: {request.source_path}")
        destination = Path(request.destination)
        width, height = self._canvas
        with tempfile.TemporaryDirectory() as workspace:
            script = Path(workspace) / "frame.ass"
            script.write_text(
                _frame_ass(
                    request.headline,
                    request.footer,
                    width=width,
                    height=height,
                    font=self._caption_font,
                ),
                encoding="utf-8",
            )
            self._run(
                [
                    self._ffmpeg,
                    "-nostdin",
                    "-y",
                    "-i",
                    str(source),
                    "-vf",
                    f"subtitles=filename={_filter_path(script)}",
                    "-c:v",
                    self._video_codec,
                    "-crf",
                    str(self._crf),
                    "-preset",
                    self._preset,
                    "-c:a",
                    "copy",
                    str(destination),
                ],
                what="frame the clip",
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
        if completed.returncode != 0 and _NO_SUBTITLES_FILTER in completed.stderr:
            raise ClipRendererError(
                f"{command[0]} cannot burn captions: it was built without libass "
                "(install homebrew-ffmpeg/ffmpeg, ADR-0071)"
            )
        if completed.returncode != 0:
            raise ClipRendererError(
                f"{command[0]} could not {what} (exit {completed.returncode}): "
                f"{_last_line(completed.stderr)}"
            )
        return completed.stdout


def _ass(captions: Sequence[Caption], *, font: str, size: int, colour: str) -> str:
    """The captions as an ASS script: one style, one dialogue line per caption (ADR-0071 §2)."""
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},{colour},{colour},&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,4,1,2,80,80,60,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [
        f"Dialogue: 0,{_ass_time(caption.start_ms)},{_ass_time(caption.end_ms)},Default,,0,0,0,,"
        f"{_ass_text(caption.text)}\n"
        for caption in captions
    ]
    return header + "".join(lines)


def _word_ass(
    captions: Sequence[Caption], *, width: int, height: int, font: str, colour: str
) -> str:
    """One word at a time, big, in the middle of the frame, on the canvas's own pixels.

    The line's time is shared among its words in proportion to their length — an estimate, since
    the index keeps line times, not word times.
    """
    size = width * 9 // 100
    outline = max(width // 120, 2)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Word,{font},{size},{colour},{colour},&H00000000,&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},0,5,60,60,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines: list[str] = []
    for caption in captions:
        words = caption.text.split()
        if not words:
            continue
        weights = [len(word) + 1 for word in words]
        span = caption.end_ms - caption.start_ms
        start = float(caption.start_ms)
        for word, weight in zip(words, weights, strict=True):
            end = start + span * weight / sum(weights)
            lines.append(
                f"Dialogue: 0,{_ass_time(round(start))},{_ass_time(round(end))},Word,,0,0,0,,"
                f"{_ass_text(word)}\n"
            )
            start = end
    return header + "".join(lines)


def _frame_ass(headline: str, footer: str | None, *, width: int, height: int, font: str) -> str:
    """One ASS script on the canvas: headline and footer in the bottom bar (ADR-0079).

    The top bar is left empty — it is kept for banner advertising.

    The bars are what the 16:9 picture leaves of the canvas; sizes are fractions of the canvas
    width so the layout holds at 1080 and at 2160 wide.
    """
    bar = (height - width * 9 // 16) // 2
    head_size = width * 7 // 100
    foot_size = width * 4 // 100
    margin = width // 20
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Head,{font},{head_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,0,0,5,{margin},{margin},0,1\n"
        f"Style: Foot,{font},{foot_size},&H00B0B0B0,&H00B0B0B0,&H00000000,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,0,0,5,{margin},{margin},0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    whole = "0:00:00.00,9:59:59.99"
    lines = [
        f"Dialogue: 0,{whole},Head,,0,0,0,,{{\\pos({width // 2},{height - bar + bar * 2 // 5})}}"
        f"{_ass_text(_drawable(headline))}\n"
    ]
    if footer is not None:
        lines.append(
            f"Dialogue: 0,{whole},Foot,,0,0,0,,{{\\pos({width // 2},{height - bar // 6})}}"
            f"{_ass_text(_drawable(footer))}\n"
        )
    return header + "".join(lines)


def _drawable(text: str) -> str:
    """Text a plain font can draw: emoji, pictographs and their modifiers dropped (ADR-0078 §3)."""
    kept = "".join(
        char
        for char in text
        if (ord(char) <= 0xFFFF and unicodedata.category(char) not in ("So", "Cs", "Mn", "Cf"))
        or char.isalnum()
    )
    return " ".join(kept.split())


def _ass_time(milliseconds: int) -> str:
    """``H:MM:SS.cc`` — ASS counts centiseconds; a caption's edge moves by under 10 ms."""
    centiseconds = milliseconds // 10
    hours, rest = divmod(centiseconds, 360_000)
    minutes, rest = divmod(rest, 6_000)
    seconds, hundredths = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{hundredths:02d}"


def _ass_text(text: str) -> str:
    """Caption text that cannot turn into ASS markup: no override blocks, escapes or breaks."""
    return " ".join(text.split()).replace("\\", "/").replace("{", "(").replace("}", ")")


def _blur_graph(
    filters: list[str],
    width: int,
    height: int,
    picture_height: float | None = None,
    after: Sequence[str] = (),
) -> str:
    """No bars: the picture sits on a blurred, darkened copy of itself that fills the frame.

    The copy is blurred small and scaled up, which is cheap; the captions are burnt first so they
    stay on the sharp picture.
    """
    chain = ",".join(filters)
    head = f"[0:v]{chain}," if chain else "[0:v]"
    small = f"{width // 8}:{height // 8}"
    if picture_height is None:
        picture = (
            f"[fg0]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
        )
    else:
        # Zoomed: the picture fills the canvas width and this share of its height, cropped at the
        # sides, with only a narrow blurred strip left above and below.
        tall = round(height * picture_height / 2) * 2
        picture = (
            f"[fg0]scale={width}:{tall}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{tall}[fg];"
        )
    return (
        f"{head}split=2[fg0][bg0];"
        f"[bg0]scale={small}:force_original_aspect_ratio=increase,crop={small},"
        f"boxblur=4:2,eq=brightness=-0.08,scale={width}:{height}:flags=bicubic[bg];"
        f"{picture}"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2{''.join(',' + step for step in after)},setsar=1[v]"
    )


def _filter_path(path: Path) -> str:
    """A path quoted for an ffmpeg filter argument: ``\\``, ``:`` and ``'`` are escaped."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


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
