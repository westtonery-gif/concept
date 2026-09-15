"""Apply declared Skills at the Task-executor boundary (ADR-0027).

The application layer sees only a tiny invocation protocol and an executor decorator. A concrete
invocation lives with agent/system code, where it adapts the Task executor's string input to a
Skill's typed input and output. Neither the Content Director nor ``task_execution`` knows that a
Skill ran, and the Skill itself remains blind to Agent, Task and Run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from omemo_content_factory.application.task_execution import ExecutionResult, TaskExecutor
from omemo_content_factory.domain.skill import SkillRef


class TaskInputSkillInvocation(Protocol):
    """One declared Skill adapted to deterministic ``str -> str`` Task-input preprocessing."""

    @property
    def skill_ref(self) -> SkillRef:
        """The exact Skill version this invocation executes."""
        ...

    def apply_to(self, task_input: str, /) -> str:
        """Invoke the Skill and return the input handed to the next invocation or executor."""
        ...


@dataclass(frozen=True, slots=True)
class SkillPreprocessingTaskExecutor:
    """Decorate a ``TaskExecutor`` with ordered, deterministic input Skill invocations.

    The original input stays owned by the Task. This decorator transforms only the value handed to
    ``delegate`` and returns its ``ExecutionResult`` unchanged (`ADR-0027` §3).
    """

    delegate: TaskExecutor
    invocations: tuple[TaskInputSkillInvocation, ...]

    def execute(self, task_input: str) -> ExecutionResult:
        prepared = task_input
        for invocation in self.invocations:
            prepared = invocation.apply_to(prepared)
        return self.delegate.execute(prepared)
