"""Direct provider setup: secrets, switching, defaults and entrypoint loading."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from omemo_content_factory.infrastructure.dotenv_file import load_dotenv, replace_block, set_api_key
from omemo_content_factory.infrastructure.llm import AnthropicLLMClient
from omemo_content_factory.infrastructure.openai_compatible_llm import OpenAICompatibleLLMClient
from omemo_content_factory.infrastructure.provider_model import client_for_role

ROOT = Path(__file__).resolve().parents[1]


def _module(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "provider,key", [("gpt", "OPENAI_API_KEY"), ("claude", "ANTHROPIC_API_KEY")]
)
def test_setup_from_key_builds_all_roles_without_network(
    provider: str,
    key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup = _module("configure_llm")
    monkeypatch.delenv(key, raising=False)
    secret = "test-secret-local-only"
    monkeypatch.setattr(setup.getpass, "getpass", lambda _: secret)
    path = tmp_path / ".env"
    path.write_text("KEEP=some-value\n")
    assert setup.main([provider, "--env-file", str(path)]) == 0
    assert secret not in capsys.readouterr().out
    assert path.stat().st_mode & 0o777 == 0o600
    environ: dict[str, str] = {}
    load_dotenv(path, environ)
    assert environ[key] == secret and environ["KEEP"] == "some-value"
    for role in [
        "clip_moment_picker@v1",
        "clip_qa_agent@v1",
        "clip_post_writer@v1",
        "story_writer@v1",
    ]:
        client = client_for_role(role, environ)
        if provider == "gpt":
            assert isinstance(client, OpenAICompatibleLLMClient)
            assert client.model == "gpt-4.1-mini"
        else:
            assert isinstance(client, AnthropicLLMClient)
            assert client._client.api_key == secret
            client._client.close()


def test_switch_preserves_other_settings_and_reports_role_overrides(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup = _module("configure_llm")
    path = tmp_path / ".env"
    path.write_text("KEEP=yes\nOMEMO_PROVIDER__MY_ROLE=fake\n")
    for provider in ["gpt", "claude", "gpt"]:
        assert setup.main([provider, "--no-key", "--env-file", str(path)]) == 0
    text = path.read_text()
    assert text.count("OMEMO_PROVIDER__DEFAULT=") == 1
    assert "OMEMO_PROVIDER__DEFAULT=openai" in text and "KEEP=yes" in text
    assert "OMEMO_PROVIDER__MY_ROLE" in capsys.readouterr().out


def test_invalid_model_rates_or_cancelled_key_do_not_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _module("configure_llm")
    path = tmp_path / ".env"
    args = ["gpt", "--env-file", str(path)]
    assert setup.main([*args, "--model", "other-model"]) == 2
    assert setup.main([*args, "--input-price", "NaN"]) == 2
    assert not path.exists()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(setup.getpass, "getpass", lambda _: "")
    assert setup.main(args) == 2
    assert not path.exists()


def test_key_replacement_and_block_writes_keep_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("export OPENAI_API_KEY=old\nOPENAI_API_KEY=duplicate\nKEEP=yes\n")
    set_api_key(path, "OPENAI_API_KEY", "new-secret")
    replace_block(path, "test", ["SETTING=1"])
    environ: dict[str, str] = {}
    load_dotenv(path, environ)
    assert environ == {"OPENAI_API_KEY": "new-secret", "KEEP": "yes", "SETTING": "1"}
    assert path.read_text().count("OPENAI_API_KEY=") == 1
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        set_api_key(path, "OPENAI_API_KEY", "value\nINJECTED=1")


def test_entrypoint_loads_sibling_env_from_other_directory_and_shell_wins(tmp_path: Path) -> None:
    script = tmp_path / "check_llm.py"
    script.write_bytes((ROOT / script.name).read_bytes())
    (tmp_path / ".env").write_text("OMEMO_PROVIDER__DEFAULT=fake\n")
    environ = {k: v for k, v in os.environ.items() if not k.startswith("OMEMO_")}
    for override, expected in [(None, 0), ("invalid-provider", 2)]:
        if override:
            environ["OMEMO_PROVIDER__DEFAULT"] = override
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            env=environ,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == expected, result.stderr


def test_shared_binding_enables_clipping_optional_roles() -> None:
    # demo_clips imports demo_factory from its script directory.
    sys.path.insert(0, str(ROOT))
    try:
        clips = _module("demo_clips")
        env = {"OMEMO_PROVIDER__DEFAULT": "fake"}
        assert clips._build_moment_picker(env) is not None
        assert clips._build_post_writer(env) is not None
        assert clips._build_moment_picker({}) is None
        assert clips._build_post_writer({}) is None
    finally:
        sys.path.remove(str(ROOT))
