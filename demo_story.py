#!/usr/bin/env python3
"""Write a voiced story script from an idea (story_writer@v1, ADR-0087) and save it.

    set -a && source .env && set +a
    python demo_story.py "a peach girl whose smile makes perfume; her aunt sells it"

Prints the script, checks it with the same decoder the department uses, and writes it to
generation-tests/stories/<slug>.txt in the `speaker | voice | text` form `demo_dialogue.py` speaks;
the voice column holds `voice:<key>` placeholders to replace with the speech vendor's voice ids.
Up to two model calls (one repair round if the decoder refuses); the cost is printed.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from demo import safe_print

from omemo_content_factory.agents import story_writer
from omemo_content_factory.application.story_script import StoryScriptError, decode_story_script
from omemo_content_factory.composition import build_story_writing
from omemo_content_factory.infrastructure.provider_model import client_for_role


def main(argv: list[str]) -> int:
    if len(argv) < 2 or not argv[1].strip():
        safe_print('usage: python demo_story.py "<idea>"')
        return 2
    idea = " ".join(argv[1:]).strip()
    writing = build_story_writing(client_for_role(story_writer.AGENT_REF, os.environ))
    cost = 0.0
    task_input = idea
    for attempt in (1, 2):
        result = writing.executor.execute(task_input)
        cost += sum(float(m.cost.amount) for m in result.analytics)
        if not result.succeeded or not result.payload_fields:
            safe_print(f"The writer failed: {result.failure_reason}")
            return 1
        fields = result.payload_fields
        try:
            script = decode_story_script(fields)
            break
        except StoryScriptError as error:
            safe_print(f"Attempt {attempt}: the script does not pass the decoder: {error}")
            if attempt == 2:
                safe_print("\n".join(f"[{name}]\n{value}\n" for name, value in fields.items()))
                return 1
            # one repair round: the decoder's own words go back to the writer, nothing else
            task_input = (
                f"{idea}\n\nТвой предыдущий сценарий отклонён программой: {error}. "
                "Исправь это и верни сценарий заново."
            )
    safe_print(f"{script.title}\n{script.premise}\n")
    for character in script.characters:
        safe_print(f"  {character.key}: {character.look}  ({character.voice})")
    safe_print("")
    for line in script.lines:
        safe_print(f"  {line.speaker:<8} {line.text}")
    safe_print(
        f"\n{len(script.lines)} lines, {script.word_count} words, "
        f"~{script.estimated_seconds:.0f}s of speech; next part: {script.next_part}"
    )
    safe_print(f"cost: ${cost:.4f}")
    slug = re.sub(r"[^a-z0-9]+", "-", script.title.lower()).strip("-")[:48] or "story"
    out = Path("generation-tests/stories") / f"{slug}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Voice ids belong to the speech vendor (ADR-0086); the placeholder is replaced by hand for now.
    header = [f"# {script.title}", f"# {script.premise}", f"# next part: {script.next_part}"]
    header += [f"# {c.key}: {c.look} ({c.voice})" for c in script.characters]
    body = [f"{line.speaker} | voice:{line.speaker} | {line.text}" for line in script.lines]
    out.write_text("\n".join(header + body) + "\n")
    safe_print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
