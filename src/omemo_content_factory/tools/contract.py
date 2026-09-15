"""The executable Tool contract and the call/result shapes of one reasoning step (ADR-0022 §3).

``Tool`` is a structural ``Protocol`` — a ``descriptor`` plus ``invoke(arguments) -> data`` — with
no base class to inherit. A Tool is what the agent's *model* decides to call mid-reasoning
(`PROJECT.md` §18), so agent code never calls one directly: every call goes through the agent's
``Toolbox``, which checks it against the grant and the declared parameters first.

- ``invoke`` receives only the checked arguments — no ``Run``, ``Task``, agent or caller identity;
  a Tool serves one reasoning step and cannot steer the pipeline (`ARCHITECTURE.md` §8).
- A Tool holds no state between calls. Anything outside the process it needs (a clock today, an
  adapter after Stage 6) is injected at construction, never imported.
- A Tool that cannot produce a result raises ``ToolExecutionError``; the Toolbox turns it into a
  ``FAILED`` result for the model. It is a technical failure, deliberately not a ``DomainError``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Protocol, TypeAlias

from omemo_content_factory.domain.tool import ToolDescriptor

ToolValue: TypeAlias = str | int | bool
"""A scalar a Tool accepts or returns (JSON-compatible; ADR-0022 §2)."""

ToolArguments: TypeAlias = Mapping[str, ToolValue]
"""A call's arguments after the Toolbox checked them against the declared parameters."""


class ToolExecutionError(Exception):
    """A Tool ran but could not produce a result (technical failure, not a ``DomainError``)."""


class Tool(Protocol):
    """A capability the agent's model may call during reasoning (ADR-0022 §3)."""

    @property
    def descriptor(self) -> ToolDescriptor:
        """The Tool's catalogue entry: model-facing name, description and parameters."""
        ...

    def invoke(self, arguments: ToolArguments, /) -> Mapping[str, ToolValue]:
        """Serve one reasoning step; raise ``ToolExecutionError`` if no result can be produced."""
        ...


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One call the model asked for: a Tool name and its raw arguments.

    Model output is untrusted data, so nothing is validated here — the ``Toolbox`` decides whether
    the call is granted and well-formed, and answers with a ``ToolResult`` either way.
    """

    name: str
    arguments: Mapping[str, object] = field(default_factory=dict)


class ToolResultStatus(Enum):
    """How a call ended (TOOL_SPEC §3)."""

    OK = "ok"
    """The Tool ran and returned data."""
    REFUSED = "refused"
    """The call was not granted or not well-formed; the Tool never ran."""
    FAILED = "failed"
    """The Tool ran and reported ``ToolExecutionError``."""


def _no_data() -> Mapping[str, ToolValue]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The answer to one ``ToolCall``, handed back into the model's reasoning.

    ``OK`` carries the Tool's data and no error; ``REFUSED``/``FAILED`` carry a reason and no data.
    ``data`` is always a read-only copy.
    """

    status: ToolResultStatus
    data: Mapping[str, ToolValue] = field(default_factory=_no_data)
    error: str = ""

    def __post_init__(self) -> None:
        if self.status is ToolResultStatus.OK:
            if self.error:
                raise ValueError("an OK ToolResult carries no error")
        elif not self.error.strip() or self.data:
            raise ValueError(f"a {self.status.value} ToolResult needs a reason and no data")
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))

    @classmethod
    def ok(cls, data: Mapping[str, ToolValue]) -> ToolResult:
        return cls(ToolResultStatus.OK, data)

    @classmethod
    def refused(cls, reason: str) -> ToolResult:
        return cls(ToolResultStatus.REFUSED, error=reason)

    @classmethod
    def failed(cls, reason: str) -> ToolResult:
        return cls(ToolResultStatus.FAILED, error=reason)
