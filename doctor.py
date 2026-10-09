"""Is this machine ready to run the factory? No assistant needed (ADR-0093).

    python doctor.py                      # every department
    python doctor.py clipping posting     # only these (core | clipping | posting | generation)

Loads ``.env`` first (a variable already exported wins), then reports what is present, what is
missing and how to install it. It never prints a variable's value. Exit code 1 only when a
**required** check of a requested department fails; optional ones are advice.
"""

from __future__ import annotations

import os
import sys

from omemo_content_factory.infrastructure.dotenv_file import load_project_env
from omemo_content_factory.infrastructure.environment_check import (
    DEPARTMENTS,
    check_environment,
    department_ready,
)


def main(argv: list[str]) -> int:
    chosen = [name for name in argv if not name.startswith("-")]
    if "-h" in argv or "--help" in argv:
        sys.stdout.write(__doc__ or "")
        return 0
    load_project_env(__file__)
    try:
        checks = check_environment(os.environ, chosen or None)
    except ValueError as error:
        sys.stderr.write(f"{error}\n")
        return 2
    shown = chosen or list(DEPARTMENTS)
    failed = False
    for department in shown:
        ready = department_ready(checks, department)
        failed = failed or not ready
        sys.stdout.write(f"\n== {department}: {'ready' if ready else 'NOT READY'}\n")
        for check in (c for c in checks if c.department == department):
            mark = "ok " if check.ok else ("MISSING" if check.required else "optional")
            line = f"  [{mark}] {check.name}"
            if check.detail:
                line += f" — {check.detail}"
            sys.stdout.write(line + "\n")
            if not check.ok and check.hint:
                sys.stdout.write(f"          -> {check.hint}\n")
    sys.stdout.write("\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
