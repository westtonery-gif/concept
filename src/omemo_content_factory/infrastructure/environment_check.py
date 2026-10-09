"""What a machine still needs before a department can run (ADR-0093).

``check_environment`` answers "is this machine ready to cut clips / post / generate?" from the
process environment, the programs on ``PATH`` and the file system — all injected through
:class:`Probe`, so it is testable without any of them installed. It reports **presence**, never a
variable's value, so its output is safe to paste into a chat or an issue.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from omemo_content_factory.infrastructure.provider_model import PROVIDER_PRESETS

__all__ = [
    "DEPARTMENTS",
    "Check",
    "Probe",
    "check_environment",
    "department_ready",
]

DEPARTMENTS = ("core", "clipping", "posting", "generation")

_MIN_PYTHON = (3, 11)


def _ffmpeg_filters(binary: str) -> str:
    done = subprocess.run(
        [binary, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30
    )
    return done.stdout


@dataclass(frozen=True, slots=True)
class Probe:
    """Everything the check reads from the machine, replaceable in a test."""

    which: Callable[[str], str | None] = shutil.which
    ffmpeg_filters: Callable[[str], str] = _ffmpeg_filters
    is_file: Callable[[str], bool] = lambda path: Path(path).is_file()
    is_dir: Callable[[str], bool] = lambda path: Path(path).is_dir()
    platform: str = sys.platform
    python_version: tuple[int, int] = field(default_factory=lambda: sys.version_info[:2])


@dataclass(frozen=True, slots=True)
class Check:
    """One requirement: where it belongs, whether it is met, and what to do when it is not."""

    department: str
    name: str
    ok: bool
    required: bool
    detail: str = ""
    hint: str = ""


def check_environment(
    environ: Mapping[str, str],
    departments: Sequence[str] | None = None,
    *,
    probe: Probe | None = None,
    env_file: str = ".env",
) -> list[Check]:
    """Every check of the requested departments (all of them by default), in a stable order."""
    chosen = tuple(departments) if departments else DEPARTMENTS
    unknown = [name for name in chosen if name not in DEPARTMENTS]
    if unknown:
        raise ValueError(f"unknown department(s): {', '.join(unknown)}")
    machine = probe or Probe()
    builders = {
        "core": _core,
        "clipping": _clipping,
        "posting": _posting,
        "generation": _generation,
    }
    checks: list[Check] = []
    for name in DEPARTMENTS:
        if name in chosen:
            checks.extend(builders[name](environ, machine, env_file))
    return checks


def department_ready(checks: Sequence[Check], department: str) -> bool:
    """True when every **required** check of ``department`` passed."""
    return all(c.ok for c in checks if c.department == department and c.required)


# --- departments ---------------------------------------------------------------------------


def _core(environ: Mapping[str, str], probe: Probe, env_file: str) -> list[Check]:
    wanted = ".".join(str(part) for part in _MIN_PYTHON)
    return [
        Check(
            "core",
            f"Python >= {wanted}",
            probe.python_version >= _MIN_PYTHON,
            True,
            detail=".".join(str(part) for part in probe.python_version),
            hint="install Python 3.11 or newer (python.org, brew, apt)",
        ),
        Check(
            "core",
            f"{env_file} file",
            probe.is_file(env_file),
            False,
            hint="`cp .env.example .env`, then fill it in (scripts/setup.sh does this)",
        ),
        *_model_checks(environ),
    ]


def _model_checks(environ: Mapping[str, str]) -> list[Check]:
    """The text roles' model: which provider(s) the configuration selects, and their keys.

    Reads the same variables selection reads (``OMEMO_PROVIDER__*``), so it never demands a key for
    a provider nothing selects — a Grok-only machine is not asked for an Anthropic key (ADR-0094).
    """
    selected = sorted(
        {
            value.strip().lower()
            for name, value in environ.items()
            if name.startswith("OMEMO_PROVIDER__") and value.strip()
        }
    )
    if not selected:
        return [
            Check(
                "core",
                "a model provider is selected (OMEMO_PROVIDER__DEFAULT)",
                False,
                True,
                detail="not set",
                hint="run `python configure_llm.py <provider> --model <name> ...` "
                "(docs/SETUP.md, 'Choosing a model')",
            )
        ]
    custom_key_envs = sorted(
        {
            value.strip()
            for name, value in environ.items()
            if name.startswith("OMEMO_API_KEY_ENV__") and value.strip()
        }
    )
    checks: list[Check] = []
    for provider in selected:
        if provider == "fake":
            continue
        if provider == "anthropic":
            key_envs: list[str | None] = ["ANTHROPIC_API_KEY"]
        elif provider == "openai-compatible":
            key_envs = list(custom_key_envs) or [None]
        elif provider in PROVIDER_PRESETS:
            key_envs = [PROVIDER_PRESETS[provider].key_env]
        else:
            checks.append(
                Check(
                    "core",
                    f"provider '{provider}' is known",
                    False,
                    True,
                    hint="anthropic, openai-compatible, or a preset: "
                    + ", ".join(sorted(PROVIDER_PRESETS)),
                )
            )
            continue
        for key_env in key_envs:
            if key_env is None:
                continue
            checks.append(
                _variable(
                    environ,
                    "core",
                    key_env,
                    True,
                    f"the API key for provider '{provider}' (put it in .env or export it)",
                )
            )
    return checks


def _clipping(environ: Mapping[str, str], probe: Probe, env_file: str) -> list[Check]:
    mac = probe.platform == "darwin"
    checks = [
        _binary(
            probe,
            "ffmpeg",
            "cuts and encodes every clip",
            "install the build from the next line (it also has libass, which captions need)"
            if mac
            else "`sudo apt install ffmpeg`",
        ),
        _binary(probe, "ffprobe", "measures every rendered clip", "it ships with ffmpeg"),
    ]
    has_subtitles = False
    if probe.which("ffmpeg"):
        try:
            has_subtitles = " subtitles " in probe.ffmpeg_filters("ffmpeg")
        except (OSError, subprocess.SubprocessError):
            has_subtitles = False
    checks.append(
        Check(
            "clipping",
            "ffmpeg `subtitles` filter (libass)",
            has_subtitles,
            True,
            detail="captions are burnt in with it" if has_subtitles else "this ffmpeg cannot",
            hint=(
                "`brew uninstall ffmpeg && brew tap homebrew-ffmpeg/ffmpeg && "
                "brew install homebrew-ffmpeg/ffmpeg/ffmpeg` (the stock formula has no libass)"
                if mac
                else "use a distribution ffmpeg (Debian/Ubuntu builds include libass)"
            ),
        )
    )
    checks.append(
        _binary(
            probe,
            "whisper-cli",
            "transcribes the speech",
            "`brew install whisper-cpp`"
            if mac
            else "build whisper.cpp (github.com/ggml-org/whisper.cpp) and put whisper-cli on PATH",
        )
    )
    model = environ.get("OMEMO_WHISPER_MODEL", "").strip()
    checks.append(
        Check(
            "clipping",
            "OMEMO_WHISPER_MODEL points at a model file",
            bool(model) and probe.is_file(model),
            True,
            detail="set" if model else "not set",
            hint="download a GGML model (e.g. ggml-large-v3-turbo.bin from the whisper.cpp "
            "Hugging Face repo) and set OMEMO_WHISPER_MODEL to its path",
        )
    )
    root = environ.get("OMEMO_EPISODE_ROOT", "").strip()
    checks.append(
        Check(
            "clipping",
            "OMEMO_EPISODE_ROOT is a folder",
            bool(root) and probe.is_dir(root),
            False,
            detail="set" if root else "not set",
            hint="the folder your episode files live in (only the Notion-driven path needs it)",
        )
    )
    checks.append(
        _binary(
            probe,
            "fpcalc",
            "finds opening titles and credits by sound",
            "`brew install chromaprint`" if mac else "`sudo apt install libchromaprint-tools`",
            required=False,
        )
    )
    return checks


def _posting(environ: Mapping[str, str], probe: Probe, env_file: str) -> list[Check]:
    del probe
    hint = "see docs/SETUP.md, section Posting"
    return [
        _variable(environ, "posting", "OMEMO_UPLOAD_POST_API_KEY", True, hint),
        _variable(environ, "posting", "OMEMO_UPLOAD_POST_PROFILE", True, hint),
        _variable(
            environ,
            "posting",
            "OMEMO_UPLOAD_POST_YOUTUBE_PRIVACY",
            True,
            "public | unlisted | private (unlisted videos are not shown in feeds or search)",
        ),
        _variable(environ, "posting", "OMEMO_UPLOAD_POST_PLATFORMS", False, hint),
    ]


def _generation(environ: Mapping[str, str], probe: Probe, env_file: str) -> list[Check]:
    del probe
    hint = "see docs/SETUP.md, section Generation"
    return [
        _variable(environ, "generation", "OMEMO_GEMINI_API_KEY", True, hint),
        _variable(environ, "generation", "OMEMO_HIGGSFIELD_API_KEY_ID", False, hint),
        _variable(environ, "generation", "OMEMO_HIGGSFIELD_API_KEY_SECRET", False, hint),
        _variable(environ, "generation", "OMEMO_ELEVENLABS_API_KEY", False, hint),
    ]


# --- helpers ---------------------------------------------------------------------------------


def _binary(probe: Probe, name: str, purpose: str, hint: str, *, required: bool = True) -> Check:
    found = probe.which(name)
    return Check(
        "clipping",
        f"`{name}` on PATH",
        found is not None,
        required,
        detail=purpose,
        hint=hint,
    )


def _variable(
    environ: Mapping[str, str], department: str, name: str, required: bool, hint: str
) -> Check:
    present = bool(environ.get(name, "").strip())
    return Check(
        department,
        name,
        present,
        required,
        detail="set" if present else "not set",  # never the value
        hint=hint,
    )
