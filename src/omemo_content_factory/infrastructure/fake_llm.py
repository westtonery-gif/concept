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
- the **only** input it reads is the ``fields`` list handed to :meth:`complete`;
- for each requested field it returns a **deterministic**, non-empty placeholder value.

Because the behaviour is a uniform function of the requested fields alone, the same client serves
**any** current or future agent without modification. It never decides *what* content is correct;
``Schema.validate`` remains the sole judge of structural validity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from omemo_content_factory.tools.toolbox import Toolbox

_FAKE_PREFIX = "fake"


class FakeLLMClient:
    """An :class:`LLMClient` returning a deterministic placeholder for each requested field.

    Stateless and provider-free: it fulfils the structured-completion contract (``name -> value``
    for the requested ``fields``) without contacting any model and without ascribing meaning to the
    field names (`ADR-0014` §2, Variant B).
    """

    def complete(
        self, *, system: str, user: str, fields: Sequence[str], toolbox: Toolbox | None = None
    ) -> Mapping[str, str]:
        """Return a deterministic value for each requested field.

        ``system``, ``user`` and ``toolbox`` are ignored by this offline provider: the sole source
        of the result is the ``fields`` list. Each field is mapped **uniformly** to a non-empty
        placeholder — no field is special-cased — which keeps the client agent-agnostic.
        """
        return {name: f"{_FAKE_PREFIX}::{name}" for name in fields}
