"""Live generation against the real vendors (GENERATION_ACCEPTANCE §6, LIV) — opt-in, costs money.

These tests call Google Gemini and Higgsfield for real, with the operator's keys, on a real photo.
They run only when ``OMEMO_LIVE_GENERATION=1`` and every variable they need is set; otherwise they
**skip and say why**, which on a machine without keys is the honest answer, not a pass. The files
they produce are kept under ``OMEMO_LIVE_OUTPUT_DIR`` (default: pytest's temporary directory) so a
human can look at the frame and the video afterwards.

Run them deliberately, one vendor at a time if you like::

    set -a && source .env && set +a
    OMEMO_LIVE_GENERATION=1 .venv/bin/python -m pytest -q -s tests/test_live_generation.py
"""

from __future__ import annotations

import os
import random
import time
from pathlib import Path

import pytest

from omemo_content_factory.adapters.image_generator import ImageGenerationRequest
from omemo_content_factory.adapters.video_generator import (
    VideoGenerationRequest,
    VideoJobState,
)
from omemo_content_factory.infrastructure.gemini_image_generator import (
    ASPECT_RATIOS,
    GeminiImageGenerator,
    gemini_image_settings_from_env,
    nearest_aspect_ratio,
)
from omemo_content_factory.infrastructure.higgsfield_video_generator import (
    HiggsfieldVideoGenerator,
    higgsfield_video_settings_from_env,
)
from omemo_content_factory.infrastructure.media_measure import measure_image

IMAGE_PROMPT = (
    "The same place in the same photo, seen from the same camera position, after construction is "
    "complete: a finished, camouflaged bunker. Keep the landscape, light and framing identical."
)
VIDEO_PROMPT = (
    "A construction timelapse from the first frame to the last: the bunker is built step by step, "
    "static camera, consistent lighting."
)
LIVE_TIMEOUT_S = 15 * 60


def _require(*names: str) -> dict[str, str]:
    if os.environ.get("OMEMO_LIVE_GENERATION") != "1":
        pytest.skip("live generation spends money; set OMEMO_LIVE_GENERATION=1 to run it")
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip("live generation needs " + ", ".join(missing))
    return dict(os.environ)


def _out(tmp_path: Path) -> Path:
    chosen = os.environ.get("OMEMO_LIVE_OUTPUT_DIR", "").strip()
    out = Path(chosen) if chosen else tmp_path
    out.mkdir(parents=True, exist_ok=True)
    return out


def _frame(environ: dict[str, str], out: Path) -> Path:
    generator = GeminiImageGenerator(gemini_image_settings_from_env(environ))
    photo = environ["OMEMO_LIVE_REFERENCE_PHOTO"]
    image = generator.generate(
        ImageGenerationRequest(
            reference=photo, prompt=IMAGE_PROMPT, destination=str(out / "ending-frame.png")
        )
    )
    source = measure_image(Path(photo).read_bytes())
    wanted = ASPECT_RATIOS[nearest_aspect_ratio(source.width, source.height)]
    assert abs(image.width / image.height - wanted) / wanted <= 0.03
    print(f"\nending frame: {image.path} {image.width}x{image.height} {image.media_type}")
    return Path(image.path)


def test_liv_01_gemini_makes_the_ending_frame(tmp_path: Path) -> None:
    environ = _require(
        "OMEMO_GEMINI_API_KEY",
        "OMEMO_GEMINI_IMAGE_MODEL",
        "OMEMO_GEMINI_IMAGE_SIZE",
        "OMEMO_LIVE_REFERENCE_PHOTO",
    )
    _frame(environ, _out(tmp_path))


def test_liv_02_the_whole_two_call_pipeline_makes_a_video(tmp_path: Path) -> None:
    environ = _require(
        "OMEMO_GEMINI_API_KEY",
        "OMEMO_GEMINI_IMAGE_MODEL",
        "OMEMO_GEMINI_IMAGE_SIZE",
        "OMEMO_HIGGSFIELD_API_KEY_ID",
        "OMEMO_HIGGSFIELD_API_KEY_SECRET",
        "OMEMO_HIGGSFIELD_VIDEO_MODEL",
        "OMEMO_HIGGSFIELD_SOUND",
        "OMEMO_LIVE_REFERENCE_PHOTO",
    )
    out = _out(tmp_path)
    frame = _frame(environ, out)
    generator = HiggsfieldVideoGenerator(higgsfield_video_settings_from_env(environ))

    job = generator.submit(
        VideoGenerationRequest(
            first_frame=environ["OMEMO_LIVE_REFERENCE_PHOTO"],
            last_frame=str(frame),
            prompt=VIDEO_PROMPT,
            duration_s=5,
        )
    )
    print(f"submitted: {job.job_id}")  # the id, never a URL

    destination = str(out / "video.mp4")
    deadline = time.monotonic() + LIVE_TIMEOUT_S
    delay = 2.0
    while True:
        result = generator.collect(job, destination)
        if result.state is not VideoJobState.PENDING:
            break
        assert time.monotonic() < deadline, f"job {job.job_id} still pending after the deadline"
        time.sleep(delay + random.uniform(0, 0.5))
        delay = min(delay * 1.5, 10.0)

    assert result.state is VideoJobState.COMPLETED, f"job {job.job_id} ended {result.state.value}"
    assert result.video is not None
    assert 4000 <= result.video.duration_ms <= 6500
    print(
        f"video: {result.video.path} {result.video.width}x{result.video.height} "
        f"{result.video.duration_ms} ms {result.video.container}"
    )
