"""Private phrase-matching helper shared by the text Skills (not a Skill itself).

A phrase matches as a whole-word sequence, case-insensitively, tolerant to any whitespace between
its words (a line break inside a disclaimer still counts). ``\\w`` is Unicode-aware, so Cyrillic
text is matched the same way as Latin.
"""

from __future__ import annotations

import re


def phrase_pattern(phrase: str) -> str:
    """Regex source matching ``phrase`` as a whole-word sequence with flexible whitespace."""
    words = (re.escape(word) for word in phrase.split())
    return r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)"


def phrase_key(phrase: str) -> str:
    """Whitespace-collapsed, case-folded key: two phrases with equal keys match the same text."""
    return " ".join(phrase.split()).casefold()
