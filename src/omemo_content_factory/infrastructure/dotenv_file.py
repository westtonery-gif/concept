"""Reads a ``.env`` file into the environment of an entrypoint (ADR-0093).

Only entrypoints call this, from their ``__main__`` block; the Composition Root still takes the
``environ`` it is handed (ADR-0012). A variable already present is **never overridden**, so a shell
export or a deployment's environment always wins over the file.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

__all__ = ["load_dotenv", "load_project_env", "replace_block"]


def load_dotenv(
    path: str | Path = ".env", environ: MutableMapping[str, str] | None = None
) -> list[str]:
    """Set the variables of ``path`` that are not already set; return the names it set.

    A missing or unreadable file is not an error (a deployment may provide the environment some
    other way). A malformed line is skipped, the rest still load.
    """
    target = os.environ if environ is None else environ
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    loaded: list[str] = []
    for raw in text.splitlines():
        parsed = _parse(raw)
        if parsed is None:
            continue
        name, value = parsed
        if name in target:
            continue
        target[name] = value
        loaded.append(name)
    return loaded


def load_project_env(entrypoint_file: str) -> list[str]:
    """Load the ``.env`` that sits beside an entrypoint script, whatever the working directory."""
    return load_dotenv(Path(entrypoint_file).resolve().parent / ".env")


def replace_block(path: str | Path, name: str, lines: list[str]) -> None:
    """Put ``lines`` between ``# >>> name`` / ``# <<< name`` markers, replacing an earlier block.

    Idempotent and local: every line outside the markers is left exactly as it was, so a tool can
    own one section of a hand-edited ``.env`` without touching the rest. Written atomically.
    """
    target = Path(path)
    begin, end = f"# >>> {name}", f"# <<< {name}"
    block = [begin, *lines, end]
    try:
        existing = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        existing = []
    if begin in existing and end in existing and existing.index(begin) < existing.index(end):
        first, last = existing.index(begin), existing.index(end)
        updated = [*existing[:first], *block, *existing[last + 1 :]]
    else:
        spacer = [""] if existing and existing[-1].strip() else []
        updated = [*existing, *spacer, *block]
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text("\n".join(updated) + "\n", encoding="utf-8")
    temporary.replace(target)


def _parse(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if text.startswith("export "):
        text = text[len("export ") :].lstrip()
    name, sep, value = text.partition("=")
    name = name.strip()
    if not sep or not name or not (name[0].isalpha() or name[0] == "_"):
        return None
    if not all(char.isalnum() or char == "_" for char in name):
        return None
    return name, _value(value.strip())


def _value(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    # an unquoted value ends at a ` #` comment
    cut = raw.find(" #")
    return raw if cut == -1 else raw[:cut].rstrip()
