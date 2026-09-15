"""Fake LLM client — a keyless, deterministic provider behind the ``LLMClient`` port.

Provider independence (PROJECT.md §5, §7) means model access is one **swappable** implementation
behind the small :class:`~omemo_content_factory.infrastructure.llm.LLMClient` port. This is another
such implementation — the one used when **no real model** is available (keyless / offline /
deterministic runs): it fabricates structured output instead of calling a provider. It is
infrastructure, not a test double — the application and domain are unchanged and unaware of which
client is injected — and it introduces **no new execution branch**: the same ``LLMTaskExecutor``
runs it exactly like the real client.

Per `ADR-0014` §2 (Variant B) the port is uniformly structured and ``fields`` is an **opaque** list
of names to which the client ascribes no meaning. This fake honours that literally, which is what
keeps it universal:

- it knows **nothing** about any specific agent (no Leo, no QA, no research);
- it does **not** branch on field names (``title``, ``hook``, ``verdict``, …) — every field is
  treated identically;
- the generated fields read only the ``fields`` list handed to :meth:`complete`; an injected clock
  is read solely for truthful local latency metrics;
- for each requested field it returns a **deterministic**, non-empty placeholder value.

Because generated content is a uniform function of the requested fields alone, the same client
serves **any** current or future agent without modification. It never decides *what* content is
correct; ``Schema.validate`` remains the sole judge of structural validity.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from omemo_content_factory.infrastructure.llm import LLMCallMetrics, LLMCompletion
from omemo_content_factory.tools.toolbox import Toolbox

_FAKE_PREFIX = "fake"
_FAKE_MODEL = "deterministic-placeholder@v1"


def _utc_now() -> datetime:
    """Read an aware UTC time; injectable for deterministic metric tests."""
    return datetime.now(tz=UTC)


class FakeLLMClient:
    """An :class:`LLMClient` returning a deterministic placeholder for each requested field.

    Stateless and provider-free: it fulfils the structured-completion contract (``name -> value``
    for the requested ``fields``) without contacting any model and without ascribing meaning to the
    field names (`ADR-0014` §2, Variant B).
    """

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock

    def complete(
        self, *, system: str, user: str, fields: Sequence[str], toolbox: Toolbox | None = None
    ) -> LLMCompletion:
        """Return a deterministic value for each requested field.

        ``system``, ``user`` and ``toolbox`` are ignored for generation: the sole content source is
        the ``fields`` list. Each field is mapped **uniformly** to a non-empty placeholder. The
        injected clock measures the call; zero tokens/cost truthfully report that no model ran.
        """
        started_at = self._clock()
        result = {name: f"{_FAKE_PREFIX}::{name}" for name in fields}
        finished_at = self._clock()
        metrics = LLMCallMetrics(
            provider="fake",
            model=_FAKE_MODEL,
            input_tokens=0,
            output_tokens=0,
            cost_amount=Decimal(0),
            cost_currency="USD",
            started_at=started_at,
            finished_at=finished_at,
        )
        return LLMCompletion(fields=result, metrics=(metrics,))
