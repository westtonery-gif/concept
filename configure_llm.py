"""Настроить GPT (OpenAI) или Claude (Anthropic) напрямую для всех текстовых ролей.

    python configure_llm.py                 # выбор сервиса + скрытый ввод ключа
    python configure_llm.py gpt
    python configure_llm.py claude

Модель, цены и лимит подставляются из готового профиля (проверены 2026-10-13).
Другая модель: --model ID --input-price N --output-price N (USD / миллион токенов).
Настройка сохраняется в .env рядом со скриптом. Сетевых запросов здесь нет.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import warnings
from dataclasses import replace
from pathlib import Path

from omemo_content_factory.infrastructure.dotenv_file import (
    load_dotenv,
    replace_block,
    set_api_key,
)
from omemo_content_factory.infrastructure.llm_setup import (
    BLOCK_NAME,
    LLMChoice,
    env_block,
    key_variable,
    problem_with,
)
from omemo_content_factory.infrastructure.provider_model import PROVIDER_PRESETS

# Explicit setup choices, not runtime fallbacks. Sources and date: ADR-0094 amendment.
PROFILES = {
    "gpt": LLMChoice("openai", "gpt-4.1-mini", "0.40", "1.60"),
    "claude": LLMChoice("anthropic", "claude-haiku-4-5-20251001", "1", "5", thinking="disabled"),
}
ALIASES = {"1": "gpt", "openai": "gpt", "2": "claude", "anthropic": "claude"}


def _choice(args: argparse.Namespace) -> LLMChoice:
    selected = args.provider or input("Выберите модель: 1 — GPT, 2 — Claude: ").strip().lower()
    selected = ALIASES.get(selected, selected)
    if selected not in PROFILES:
        raise ValueError("Выберите gpt или claude.")
    profile = PROFILES[selected]
    model = args.model or profile.model
    if model != profile.model and (args.input_price is None or args.output_price is None):
        raise ValueError("Для другой модели укажите --input-price и --output-price.")
    return replace(
        profile,
        model=model,
        input_price=profile.input_price if args.input_price is None else args.input_price,
        output_price=profile.output_price if args.output_price is None else args.output_price,
        max_tokens=args.max_tokens,
        thinking=args.thinking or profile.thinking,
    )


def _configure(choice: LLMChoice, path: Path, *, no_key: bool) -> None:
    problem = problem_with(choice)
    if problem:
        raise ValueError(problem)
    print(f"{choice.provider}: {choice.model}")
    print(f"Цены: ${choice.input_price} вход / ${choice.output_price} выход за 1 млн токенов.")
    environ = dict(os.environ)
    load_dotenv(path, environ)
    key_name = key_variable(choice)
    if key_name and not no_key and not environ.get(key_name, "").strip():
        # Refuse getpass's echoing fallback if a terminal is unavailable.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            secret = getpass.getpass(f"Вставьте {key_name} (ввод скрыт): ").strip()
        set_api_key(path, key_name, secret)
    replace_block(path, BLOCK_NAME, env_block(choice))
    print(f"Настройка сохранена: {path}")
    overrides = sorted(
        name
        for name, value in environ.items()
        if name.startswith("OMEMO_PROVIDER__") and not name.endswith("__DEFAULT") and value.strip()
    )
    if overrides:
        print("Сохранены отдельные настройки ролей, они имеют приоритет: " + ", ".join(overrides))
    if no_key:
        print(f"Перед запуском задайте {key_name} в .env или окружении.")
    print("Проверка подключения (один платный запрос): python check_llm.py")


def main(argv: list[str]) -> int:
    if "--list" in argv or (
        argv
        and argv[0] in {*PROVIDER_PRESETS, "anthropic", "openai-compatible"}
        and (argv[0] not in {"openai", "anthropic"} or "--model" in argv)
    ):
        return _generic_main(argv)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("provider", nargs="?", choices=["gpt", "claude", "openai", "anthropic"])
    parser.add_argument("--model", help="другая модель выбранного сервиса")
    parser.add_argument("--input-price", help="USD за миллион входных токенов")
    parser.add_argument("--output-price", help="USD за миллион выходных токенов")
    parser.add_argument("--max-tokens", type=int, default=16000)
    parser.add_argument("--thinking", help="настройка thinking для выбранной модели")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parent / ".env")
    parser.add_argument("--no-key", action="store_true", help="сохранить профиль без ввода ключа")
    args = parser.parse_args(argv)
    try:
        _configure(_choice(args), args.env_file, no_key=args.no_key)
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print(
            "Ввод отменён или скрытый ввод недоступен. Запустите команду в терминале.",
            file=sys.stderr,
        )
        return 2
    except (ValueError, OSError) as error:
        print(f"Не удалось настроить: {error}", file=sys.stderr)
        return 2
    return 0


def _generic_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("provider", nargs="?", help="anthropic | openai-compatible | a preset")
    parser.add_argument("--model", help="the model name exactly as the provider spells it")
    parser.add_argument("--input-price", help="per 1M input tokens, e.g. 3")
    parser.add_argument("--output-price", help="per 1M output tokens, e.g. 15")
    parser.add_argument("--free", action="store_true", help="a local/free model: both prices 0")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--max-tokens", type=int, default=16000, help="one answer's output budget")
    parser.add_argument("--thinking", default="inherit", help="inherit | effort:low|medium|high")
    parser.add_argument("--base-url", help="override the preset's address (required for custom)")
    parser.add_argument("--key-env", help="name of the variable holding the key (custom/override)")
    parser.add_argument("--wire", choices=["chat", "responses"])
    parser.add_argument("--structured", choices=["tool", "tool-required", "json"])
    parser.add_argument("--env-file", default=str(Path(__file__).resolve().parent / ".env"))
    parser.add_argument("--list", action="store_true", help="list the presets and exit")
    args = parser.parse_args(argv)

    if args.list:
        sys.stdout.write("anthropic            ANTHROPIC_API_KEY\n")
        for name, preset in sorted(PROVIDER_PRESETS.items()):
            sys.stdout.write(f"{name:<20} {preset.key_env or '(no key)':<20} {preset.base_url}\n")
        sys.stdout.write("openai-compatible    (your --base-url / --key-env)\n")
        return 0
    if not args.provider or not args.model:
        parser.error("a provider and --model are required (see --list)")
    if args.free:
        input_price = output_price = "0"
    else:
        input_price, output_price = args.input_price, args.output_price
    if input_price is None or output_price is None:
        parser.error("give --input-price and --output-price (per 1M tokens), or --free")

    choice = LLMChoice(
        provider=args.provider.lower(),
        model=args.model,
        input_price=input_price,
        output_price=output_price,
        currency=args.currency,
        max_tokens=args.max_tokens,
        thinking=args.thinking,
        base_url=args.base_url,
        key_env=args.key_env,
        wire=args.wire,
        structured=args.structured,
    )
    problem = problem_with(choice)
    if problem:
        sys.stderr.write(f"not written — {problem}\n")
        return 2
    replace_block(args.env_file, BLOCK_NAME, env_block(choice))
    sys.stdout.write(f"wrote the model block for '{choice.provider}' to {args.env_file}\n")
    key = key_variable(choice)
    if key:
        sys.stdout.write(f"next: add your key to .env as  {key}=...  (or export it)\n")
    sys.stdout.write("then:  python check_llm.py      # one tiny real call: key, model, price\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
