"""Choose the model the factory's text roles use — any provider, one command (ADR-0094).

    python configure_llm.py xai --model grok-4 --input-price 3 --output-price 15
    python configure_llm.py ollama --model llama3.1 --free
    python configure_llm.py openai-compatible --model my-model --base-url https://llm.example/v1 \
        --key-env MY_GATEWAY_KEY --input-price 1 --output-price 2

Writes one managed block of ``OMEMO_*__DEFAULT`` lines into ``.env`` (every other line is left as
it is), after running it through the same selection the factory uses. **Prices are yours to give**
— per one million tokens, from your provider's price page; the factory never guesses a rate.
The API key is never written: put it in ``.env`` or export it under the name this prints.

Providers: anthropic, openai-compatible, or a preset — see ``--list``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from omemo_content_factory.infrastructure.dotenv_file import replace_block
from omemo_content_factory.infrastructure.llm_setup import (
    BLOCK_NAME,
    LLMChoice,
    env_block,
    key_variable,
    problem_with,
)
from omemo_content_factory.infrastructure.provider_model import PROVIDER_PRESETS


def main(argv: list[str]) -> int:
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
