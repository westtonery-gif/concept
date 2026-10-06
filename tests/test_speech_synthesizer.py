"""Tests for the speech port's value types (GENERATION_ACCEPTANCE §7, SPP; ADR-0085)."""

from __future__ import annotations

from typing import Any

import pytest

from omemo_content_factory.adapters.speech_synthesizer import (
    SpeechRequest,
    SpokenWord,
    SynthesizedSpeech,
)

WORDS = (SpokenWord("Hello,", 100, 400), SpokenWord("world.", 500, 900))


@pytest.mark.parametrize("field", ["text", "voice", "destination"])
def test_spp_01_a_request_refuses_a_blank_field(field: str) -> None:
    fields: dict[str, Any] = {"text": "Hi", "voice": "am_adam", "destination": "/a.wav"}
    fields[field] = "  "
    with pytest.raises(ValueError):
        SpeechRequest(**fields)


@pytest.mark.parametrize("speed", [0, -1.0, True, float("nan")])
def test_spp_01_a_request_needs_a_positive_speed(speed: object) -> None:
    with pytest.raises(ValueError):
        SpeechRequest(text="Hi", voice="v", destination="/a.wav", speed=speed)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [(" ", 0, 10), ("a", -1, 10), ("a", 10, 10), ("a", 10, 5), ("a", True, 10)],
)
def test_spp_02_a_spoken_word_needs_text_and_a_forward_interval(
    text: str, start: int, end: int
) -> None:
    with pytest.raises(ValueError):
        SpokenWord(text, start, end)


def test_spp_03_synthesized_speech_accepts_ordered_words_inside_the_file() -> None:
    speech = SynthesizedSpeech(path="/a.wav", duration_ms=1000, sample_rate=24000, words=WORDS)
    assert speech.words == WORDS


@pytest.mark.parametrize(
    "overrides",
    [
        {"duration_ms": 0},
        {"duration_ms": True},
        {"sample_rate": -1},
        {"path": " "},
        {"words": ()},
        {"words": (SpokenWord("b", 500, 600), SpokenWord("a", 100, 200))},
        {"words": (SpokenWord("a", 100, 600), SpokenWord("b", 500, 700))},
        {"words": (SpokenWord("a", 100, 1500),)},
    ],
)
def test_spp_03_synthesized_speech_refuses_what_cannot_be_true(
    overrides: dict[str, object],
) -> None:
    fields: dict[str, object] = {
        "path": "/a.wav",
        "duration_ms": 1000,
        "sample_rate": 24000,
        "words": WORDS,
    }
    fields.update(overrides)
    with pytest.raises(ValueError):
        SynthesizedSpeech(**fields)  # type: ignore[arg-type]
