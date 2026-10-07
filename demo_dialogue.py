#!/usr/bin/env python3
"""Make a spoken dialogue from a plain script: each line spoken by Kokoro, laid end to end,
music ducked under it, plus a JSON timeline of every word for captions.

    set -a && source .env && set +a      # OMEMO_ELEVENLABS_API_KEY / OMEMO_ELEVENLABS_MODEL
    python demo_dialogue.py script.txt out/dialogue.wav [music.mp3]

Script format, one line per utterance (lines starting with # are skipped):

    speaker | voice | what they say

With no script file (or `-`) it speaks the built-in sample.
The voice column is the speech vendor's voice id (ElevenLabs: Copy voice ID). Needs ffmpeg on PATH.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from omemo_content_factory.adapters.speech_synthesizer import SpeechRequest
from omemo_content_factory.composition import build_speech_synthesizer
from omemo_content_factory.infrastructure.dialogue_mixer import DialogueLine, DialogueMixer

SAMPLE = """\
# a banker who laughs, a farmer who does not give up (ElevenLabs premade voices)
banker | pqHfZKP75CvOlQylNhV4 | [laughs] Yo, nice dirt you got there. Nothing's gonna grow in that.
farmer | FGY2WhTYpPnrIDTdsKH5 | Maybe not today. But I'll water it every morning anyway.
banker | pqHfZKP75CvOlQylNhV4 | Ha! Every morning? For a few sad sprouts?
farmer | FGY2WhTYpPnrIDTdsKH5 | My grandpa planted a tree here. I wanna finish what he started.
"""


def parse(script: str) -> list[tuple[str, str, str]]:
    rows = []
    for raw in script.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        speaker, voice, text = (part.strip() for part in line.split("|", 2))
        rows.append((speaker, voice, text))
    return rows


def main(argv: list[str]) -> int:
    script = Path(argv[1]).read_text() if len(argv) > 1 and argv[1] not in ("", "-") else SAMPLE
    destination = Path(argv[2] if len(argv) > 2 else "generation-tests/dialogue/demo.wav")
    music = argv[3] if len(argv) > 3 else None
    synthesizer = build_speech_synthesizer(os.environ)
    mixer = DialogueMixer()
    lines = []
    for index, (speaker, voice, text) in enumerate(parse(script), start=1):
        line_path = destination.parent / "lines" / f"{index:02d}-{speaker}.wav"
        speech = synthesizer.synthesize(
            SpeechRequest(text=text, voice=voice, destination=str(line_path))
        )
        print(f"{index:>2}. {speaker:<7} {speech.duration_ms / 1000:5.1f}s  {text}")
        lines.append(DialogueLine(speaker, speech))
    track = mixer.mix(lines, str(destination), music=music, music_db=-20.0)
    timeline = destination.with_suffix(".words.json")
    timeline.write_text(
        json.dumps(
            [
                {"speaker": w.speaker, "text": w.text, "start_ms": w.start_ms, "end_ms": w.end_ms}
                for w in track.words
            ],
            ensure_ascii=False,
            indent=1,
        )
    )
    print(
        f"\n{track.path}  {track.duration_ms / 1000:.1f}s  ({len(track.words)} words -> {timeline})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
