"""One tiny real call to the model a role uses: proves the key, the model name and the price work.

    python check_llm.py [role]          # default role: qa_agent@v1

Costs a fraction of a cent. Prints the provider, the answer, the tokens, the exact cost from your
configured rates and the latency; on failure it prints the provider's own refusal.
"""

from __future__ import annotations

import os
import sys

from omemo_content_factory.infrastructure.dotenv_file import load_project_env
from omemo_content_factory.infrastructure.llm import LLMError
from omemo_content_factory.infrastructure.provider_model import (
    ProviderModelSelectionError,
    client_for_role,
)
from omemo_content_factory.tools.toolbox import Toolbox


def main(argv: list[str]) -> int:
    role = argv[0] if argv else "qa_agent@v1"
    try:
        client = client_for_role(role, os.environ)
    except ProviderModelSelectionError as error:
        sys.stderr.write(f"not configured: {error}\n")
        return 2
    try:
        completion = client.complete(
            system="You are a connectivity check.",
            user="Reply with the single word: ok",
            fields=("answer",),
            toolbox=Toolbox(grants=(), available=()),
        )
    except LLMError as error:
        sys.stderr.write(f"the model call failed: {error}\n")
        return 1
    answer = completion.fields.get("answer", "")
    sys.stdout.write(f"role {role}: answer {answer!r}\n")
    for metric in completion.metrics:
        seconds = (metric.finished_at - metric.started_at).total_seconds()
        sys.stdout.write(
            f"  {metric.provider} / {metric.model}: {metric.input_tokens} in, "
            f"{metric.output_tokens} out, {metric.cost_amount} {metric.cost_currency}, "
            f"{seconds:.1f}s\n"
        )
    if not answer:
        sys.stderr.write(
            "the model answered but not in the expected shape — try OMEMO_STRUCTURED__DEFAULT=json "
            "(or tool-required) in .env, see docs/SETUP.md\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    load_project_env(__file__)
    raise SystemExit(main(sys.argv[1:]))
