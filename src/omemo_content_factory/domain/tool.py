"""Tool domain model — the immutable catalogue descriptor of a model-invoked capability.

A Tool is an operational capability that an agent's **model** calls during its reasoning, to act or
to get data (`PROJECT.md` §18; `ARCHITECTURE.md` §8; ADR-0022). This module holds only its
**passive descriptor** — identity, version, the model-facing description and the declared
parameters — exactly as ``domain/skill.py`` holds the Skill descriptor. The executable contract, the
scoping ``Toolbox`` and the concrete Tools live in the ``tools`` layer, never here.

Unlike a Skill, a Tool carries its parameter contract as data: the model reads it to form a call,
and the ``Toolbox`` checks every call against it before the Tool runs (ADR-0022 §2). Nothing here
knows Run, Task, Agent or Workflow. Only stdlib and ``domain.errors`` are used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from omemo_content_factory.domain.errors import DomainError

ToolId: TypeAlias = str
"""Stable identifier of the logical Tool — also the name the model calls it by (ADR-0022 §2)."""

ToolRef: TypeAlias = str
"""Opaque handle of one Tool version, ``<tool_id>@v<n>`` — what an agent's grant references."""

_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
"""snake_case, at most 64 chars: a valid function name for every mainstream model provider."""


# --- Domain errors -----------------------------------------------------------------------
# Tool contract violations (ADR-0022 §6). Rooted at the shared ``DomainError`` (ADR-0017).


class ToolDomainError(DomainError):
    """Base class for all Tool contract violations."""


class InvalidToolDescriptorError(ToolDomainError):
    """A Tool descriptor or parameter was built with an ill-formed id, text, kind or version."""


class ToolGrantError(ToolDomainError):
    """An agent's Tool grant cannot be honoured: an unknown, repeated or ambiguous ref."""


# --- Value Objects + descriptor ----------------------------------------------------------


class ToolParameterType(Enum):
    """The kind of value a Tool parameter accepts (JSON scalar kinds; ADR-0022 §2)."""

    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"


@dataclass(frozen=True, slots=True)
class ToolParameter:
    """One named argument a Tool accepts; ``description`` tells the model what to pass."""

    name: str
    kind: ToolParameterType
    description: str
    required: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise InvalidToolDescriptorError(
                f"a Tool parameter name must be snake_case (<= 64 chars), got {self.name!r}"
            )
        if not isinstance(self.kind, ToolParameterType):
            raise InvalidToolDescriptorError(
                f"parameter '{self.name}' needs a ToolParameterType kind, got {self.kind!r}"
            )
        if not self.description.strip():
            raise InvalidToolDescriptorError(
                f"parameter '{self.name}' needs a non-blank description"
            )
        if not isinstance(self.required, bool):
            raise InvalidToolDescriptorError(f"parameter '{self.name}': required must be a bool")


@dataclass(frozen=True, slots=True)
class ToolVersion:
    """A version of a Tool — a Value Object compared by value (`DOMAIN_MODEL.md` §11)."""

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 1:
            raise InvalidToolDescriptorError(
                f"a Tool version must be an int >= 1, got {self.value!r}"
            )


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """The immutable catalogue entry of one Tool version (ADR-0022 §2; TOOL_SPEC §2).

    ``tool_id`` doubles as the name the model calls the Tool by; ``description`` is what the model
    reads to decide *when* to call it; ``parameters`` declares the arguments it may pass.
    """

    tool_id: ToolId
    version: ToolVersion
    description: str
    parameters: tuple[ToolParameter, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.tool_id, str) or not _NAME.fullmatch(self.tool_id):
            raise InvalidToolDescriptorError(
                f"tool_id must be snake_case (<= 64 chars), got {self.tool_id!r}"
            )
        if not self.description.strip():
            raise InvalidToolDescriptorError(f"Tool '{self.tool_id}' needs a non-blank description")
        if not isinstance(self.parameters, tuple) or not all(
            isinstance(parameter, ToolParameter) for parameter in self.parameters
        ):
            raise InvalidToolDescriptorError(
                f"Tool '{self.tool_id}': parameters must be a tuple of ToolParameter"
            )
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise InvalidToolDescriptorError(
                f"Tool '{self.tool_id}' declares a parameter name more than once"
            )

    @property
    def ref(self) -> ToolRef:
        """The version handle ``<tool_id>@v<n>`` (same shape as a Skill ref)."""
        return f"{self.tool_id}@v{self.version.value}"
