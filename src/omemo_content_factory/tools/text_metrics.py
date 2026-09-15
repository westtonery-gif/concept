"""``text_metrics@v1`` — measure a draft and check it against a character limit (ADR-0022 §5).

Models count characters badly, while carousel slides and captions have hard limits. This Tool lets
a writer role measure its draft mid-reasoning instead of guessing. The same computation called from
code would be a Skill; it is a Tool because the *model* decides when to call it (`PROJECT.md` §18).

Counting rules (TOOL_SPEC §6): ``characters`` is the length of the text as given; words are split
on whitespace; a sentence ends at ``.``/``!``/``?``/``…`` followed by whitespace or the end of the
text (so ``3.5`` does not end one) and counts only if it contains a letter or digit; paragraphs are
separated by a blank line.
"""

from __future__ import annotations

import re
from typing import ClassVar

from omemo_content_factory.domain.tool import (
    ToolDescriptor,
    ToolParameter,
    ToolParameterType,
    ToolVersion,
)
from omemo_content_factory.tools.contract import ToolArguments, ToolExecutionError, ToolValue

DESCRIPTOR = ToolDescriptor(
    tool_id="text_metrics",
    version=ToolVersion(1),
    description=(
        "Count the characters, words, sentences and paragraphs of a text and, if max_chars is "
        "given, check the text against that character limit. Use it instead of estimating the "
        "length of a draft yourself."
    ),
    parameters=(
        ToolParameter(
            name="text",
            kind=ToolParameterType.STRING,
            description="The text to measure, exactly as it will be published.",
        ),
        ToolParameter(
            name="max_chars",
            kind=ToolParameterType.INTEGER,
            description="Optional character limit (>= 1) to check the text against.",
            required=False,
        ),
    ),
)

_SENTENCE_END = re.compile(r"[.!?…]+(?=\s|$)")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_WORD_CHAR = re.compile(r"\w")


class TextMetrics:
    """Stateless text measurement. A ``Tool``."""

    __slots__ = ()
    descriptor: ClassVar[ToolDescriptor] = DESCRIPTOR

    def invoke(self, arguments: ToolArguments, /) -> dict[str, ToolValue]:
        text = arguments.get("text")
        limit = arguments.get("max_chars")
        if not isinstance(text, str):
            raise ToolExecutionError("text must be a string")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise ToolExecutionError(f"max_chars must be an integer >= 1, got {limit!r}")
        metrics: dict[str, ToolValue] = {
            "characters": len(text),
            "words": len(text.split()),
            "sentences": sum(1 for part in _SENTENCE_END.split(text) if _WORD_CHAR.search(part)),
            "paragraphs": sum(1 for part in _PARAGRAPH_BREAK.split(text) if part.strip()),
        }
        if limit is not None:
            metrics["max_chars"] = limit
            metrics["within_limit"] = len(text) <= limit
            metrics["over_by"] = max(0, len(text) - limit)
        return metrics
