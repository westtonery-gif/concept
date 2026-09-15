"""Workflow domain model — a declarative execution plan (v1 static model).

A standalone root of the definitions catalogue that **owns** its Steps (DOMAIN_MODEL.md §2.3,
§2.4, §9.2; ADR-0009). It is **pure data**: it does not execute anything, holds no runtime state,
no lifecycle, no versioning, no scheduling and no execution semantics. It does not know Run, Task
or Output; cross-entity references (``agent_ref``, ``schema_ref``) are opaque ``str`` (ADR-0003 §3).

Both ``Workflow`` and ``WorkflowStep`` are **immutable** (frozen): a different plan is a new value,
never a mutation. Validity is checked **at construction** (data integrity only): a non-empty plan,
unique step ids, and referential ``depends_on``. No ordering, scheduling or cycle analysis is
performed — ``depends_on`` is inert metadata (ADR-0009 §5, §6). Only stdlib is used.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TypeAlias

from omemo_content_factory.domain.errors import DomainError

WorkflowId: TypeAlias = str
"""Opaque, stable identifier of a Workflow (ADR-0009 §4)."""

StepId: TypeAlias = str
"""Opaque identifier of a Workflow Step, unique within its Workflow (ADR-0009 §4)."""


# --- Domain errors -----------------------------------------------------------------------
# Workflow domain-rule violations (ADR-0009 §9). Rooted at the shared ``DomainError`` (ADR-0017).


class WorkflowDomainError(DomainError):
    """Base class for all Workflow domain-rule violations."""


class EmptyWorkflowError(WorkflowDomainError):
    """A Workflow was created with no steps (DOMAIN_MODEL.md §6)."""


class DuplicateStepIdError(WorkflowDomainError):
    """Two steps in a Workflow share the same ``step_id`` (ADR-0009 §5)."""


class UnknownDependencyError(WorkflowDomainError):
    """A step's ``depends_on`` refers to a ``step_id`` absent from the Workflow (ADR-0009 §5)."""


# --- Workflow Step (child data of a Workflow) --------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """One declared step of a Workflow — immutable data, no execution state (ADR-0009 §3, §4).

    ``depends_on`` is **descriptive metadata only**: it never affects execution order, scheduling
    or control flow (ADR-0009 §6).
    """

    step_id: StepId
    task_type: str
    agent_ref: str
    schema_ref: str
    depends_on: tuple[StepId, ...] = ()


# --- Workflow (aggregate root — immutable composition of steps) --------------------------


@dataclass(frozen=True, slots=True)
class Workflow:
    """A declarative execution plan: an ordered, immutable set of steps (ADR-0009 §2, §4).

    Execution order is **strictly the list order of ``steps``**; ``depends_on`` is inert. Data
    integrity is validated at construction (non-empty, unique step ids, referential ``depends_on``).
    """

    workflow_id: WorkflowId
    name: str
    steps: tuple[WorkflowStep, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.steps:
            raise EmptyWorkflowError("a Workflow must have at least one step")
        seen: set[StepId] = set()
        for step in self.steps:
            if step.step_id in seen:
                raise DuplicateStepIdError(f"duplicate step_id '{step.step_id}'")
            seen.add(step.step_id)
        for step in self.steps:
            for dependency in step.depends_on:
                if dependency not in seen:
                    raise UnknownDependencyError(
                        f"step '{step.step_id}' depends on unknown step '{dependency}'"
                    )

    @classmethod
    def create(
        cls, *, workflow_id: WorkflowId, name: str, steps: Iterable[WorkflowStep]
    ) -> Workflow:
        """Create a Workflow from an ordered iterable of steps (ADR-0009 §4)."""
        return cls(workflow_id=workflow_id, name=name, steps=tuple(steps))
