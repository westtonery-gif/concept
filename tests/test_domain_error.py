"""Tests for the shared ``DomainError`` base (ADR-0017).

Pins the hierarchy invariant: every error defined in the domain layer descends from
``DomainError``; technical failures (infrastructure / composition root) do not. Also checks the
extraction is additive — a real domain-rule violation is still caught by its own aggregate base.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

import omemo_content_factory.domain as domain_pkg
from omemo_content_factory.composition import CompositionError
from omemo_content_factory.domain.artifact import ArtifactDomainError
from omemo_content_factory.domain.errors import DomainError
from omemo_content_factory.domain.human_review import HumanReviewDomainError
from omemo_content_factory.domain.output import OutputDomainError
from omemo_content_factory.domain.run import (
    Actor,
    InvalidTransitionError,
    Run,
    RunDomainError,
    RunStatus,
)
from omemo_content_factory.domain.schema import SchemaDomainError
from omemo_content_factory.domain.task import TaskDomainError
from omemo_content_factory.domain.workflow import (
    EmptyWorkflowError,
    Workflow,
    WorkflowDomainError,
)
from omemo_content_factory.infrastructure.llm import LLMError
from omemo_content_factory.infrastructure.provider_model import ProviderModelSelectionError


def _domain_exception_classes() -> list[type[BaseException]]:
    """Every exception class *defined* (not merely imported) in a ``domain`` module."""
    found: list[type[BaseException]] = []
    for info in pkgutil.iter_modules(domain_pkg.__path__):
        module = importlib.import_module(f"{domain_pkg.__name__}.{info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, BaseException) and obj.__module__ == module.__name__:
                found.append(obj)
    return found


def test_domain_error_is_a_plain_exception() -> None:
    assert issubclass(DomainError, Exception)
    assert DomainError.__bases__ == (Exception,)


@pytest.mark.parametrize(
    "base",
    [
        RunDomainError,
        TaskDomainError,
        OutputDomainError,
        ArtifactDomainError,
        HumanReviewDomainError,
        SchemaDomainError,
        WorkflowDomainError,
    ],
)
def test_each_aggregate_base_is_rooted_at_domain_error(base: type[Exception]) -> None:
    assert base.__bases__ == (DomainError,)


def test_every_error_defined_in_the_domain_layer_is_a_domain_error() -> None:
    classes = _domain_exception_classes()
    # Sanity: the scan actually sees the hierarchies (7 bases + their concrete errors + root).
    assert len(classes) > 7
    offenders = [c.__qualname__ for c in classes if not issubclass(c, DomainError)]
    assert offenders == []


@pytest.mark.parametrize("technical", [LLMError, CompositionError, ProviderModelSelectionError])
def test_technical_failures_are_not_domain_errors(technical: type[Exception]) -> None:
    assert not issubclass(technical, DomainError)


def test_run_violation_is_caught_by_both_its_own_base_and_domain_error() -> None:
    run = Run.create(run_id="r-1", content_brief_ref="brief", workflow_version_ref="wf@v1")

    with pytest.raises(RunDomainError):
        run.transition(RunStatus.COMPLETED, by=Actor.CONTENT_DIRECTOR)
    with pytest.raises(DomainError) as caught:
        run.transition(RunStatus.COMPLETED, by=Actor.CONTENT_DIRECTOR)
    assert isinstance(caught.value, InvalidTransitionError)


def test_workflow_violation_is_caught_as_domain_error() -> None:
    with pytest.raises(DomainError) as caught:
        Workflow.create(workflow_id="wf", name="empty", steps=[])
    assert isinstance(caught.value, EmptyWorkflowError)
