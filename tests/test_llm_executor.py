"""Offline tests for the LLM-backed TaskExecutor (``infrastructure.llm.LLMTaskExecutor``).

These bind only to the public contract (``execute`` -> ``ExecutionResult``) and inject a
deterministic fake ``LLMClient`` — no SDK, no network, no API key. The real ``AnthropicLLMClient``
is a thin adapter exercised by the demo, not unit-tested with mocks. Under the structured port
(ADR-0014) the executor carries a mandatory generation shape (``output_fields``), renders its
user message from ``user_template``, and emits structured ``payload_fields`` plus a deterministic
serialized ``payload``; end to end (with ACTIVE Schemas wired) the LLM path now records a validated
Output and an Artifact.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from omemo_content_factory.application.content_director import ContentDirector, TaskRequest
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.domain.run import Run, RunStatus
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.domain.task import TaskStatus
from omemo_content_factory.infrastructure.llm import (
    LLMCallMetrics,
    LLMCompletion,
    LLMError,
    LLMTaskExecutor,
)
from omemo_content_factory.tools.toolbox import Toolbox

_NOW = datetime(2026, 9, 15, tzinfo=UTC)


def _completion(fields: dict[str, str]) -> LLMCompletion:
    return LLMCompletion(
        fields,
        (
            LLMCallMetrics(
                provider="test",
                model="test-model",
                input_tokens=1,
                output_tokens=2,
                cost_amount=Decimal("0.000003"),
                cost_currency="USD",
                started_at=_NOW,
                finished_at=_NOW,
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class EchoLLMClient:
    """Deterministic fake: fills every requested field with the input tagged by the role."""

    tag: str

    def complete(
        self, *, system: str, user: str, fields: Sequence[str], toolbox: Toolbox
    ) -> LLMCompletion:
        return _completion({name: f"{self.tag}:{user}" for name in fields})


@dataclass
class RecordingLLMClient:
    """Fake that records each (system, user, fields) call and fills fields with a fixed value."""

    reply: str
    calls: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)

    def complete(
        self, *, system: str, user: str, fields: Sequence[str], toolbox: Toolbox
    ) -> LLMCompletion:
        self.calls.append((system, user, tuple(fields)))
        return _completion({name: self.reply for name in fields})


@dataclass(frozen=True, slots=True)
class FailingLLMClient:
    """Deterministic fake that always fails with an ``LLMError``."""

    def complete(
        self, *, system: str, user: str, fields: Sequence[str], toolbox: Toolbox
    ) -> LLMCompletion:
        raise LLMError("rate limited")


def _active_schema(schema_id: str) -> Schema:
    """An ACTIVE Schema requiring a single ``text`` field (matches the fakes' output)."""
    schema = Schema.create(
        schema_id=schema_id, version=SchemaVersion(1), description="d", required_fields=["text"]
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


def test_successful_completion_becomes_a_structured_execution_result() -> None:
    """A structured completion is wrapped as a success: payload_fields + serialized payload."""
    executor = LLMTaskExecutor(
        client=EchoLLMClient("writer"),
        system_prompt="system prompt",
        user_template="{input}",
        schema_ref="draft@v1",
        output_fields=("facts",),
        prompt_ref="writer@v1",
    )
    result = executor.execute("the brief")
    assert result.succeeded is True
    assert result.payload_fields == {"facts": "writer:the brief"}
    assert result.schema_ref == "draft@v1"
    # payload is a deterministic serialization of the same fields (format not asserted directly).
    assert result.output is not None
    assert json.loads(result.output) == {"facts": "writer:the brief"}


def test_executor_renders_template_and_forwards_system_and_shape() -> None:
    """The system prompt, the rendered user message, and the generation shape reach the client."""
    client = RecordingLLMClient(reply="done")
    executor = LLMTaskExecutor(
        client=client,
        system_prompt="you are a writer",
        user_template="brief: {input}",
        schema_ref="draft@v1",
        output_fields=("facts", "sources"),
        prompt_ref="writer@v1",
    )
    result = executor.execute("research notes")
    assert client.calls == [("you are a writer", "brief: research notes", ("facts", "sources"))]
    assert result.payload_fields == {"facts": "done", "sources": "done"}


def test_model_failure_becomes_a_managed_failed_result() -> None:
    """An LLMError is converted to a FAILED ExecutionResult, not propagated (PROJECT.md §10)."""
    executor = LLMTaskExecutor(
        client=FailingLLMClient(),
        system_prompt="system prompt",
        user_template="{input}",
        schema_ref="draft@v1",
        output_fields=("facts",),
        prompt_ref="writer@v1",
    )
    result = executor.execute("the brief")
    assert result.succeeded is False
    assert result.output is None
    assert result.payload_fields is None
    assert result.failure_reason is not None
    assert "rate limited" in result.failure_reason


def test_executor_requires_a_non_empty_generation_shape() -> None:
    """Locus 2 (ADR-0014 §3): a structured executor cannot be constructed without a shape."""
    with pytest.raises(ValueError, match="output_fields"):
        LLMTaskExecutor(
            client=EchoLLMClient("writer"),
            system_prompt="s",
            user_template="{input}",
            schema_ref="draft@v1",
            output_fields=(),
            prompt_ref="writer@v1",
        )


def test_three_role_pipeline_records_validated_output_through_the_root() -> None:
    """Research -> Writer -> Editor with per-role LLM executors yields a COMPLETED Run.

    Under the structured port (ADR-0014) each executor emits structured ``payload_fields``; with
    per-role ACTIVE Schemas wired into the Content Director, the validated path (Variant A) records
    a VALID Output and an Artifact for every step — the LLM-path gap is closed.
    """
    executors = {
        "researcher@v1": LLMTaskExecutor(
            client=EchoLLMClient("research"),
            system_prompt="research",
            user_template="{input}",
            schema_ref="notes@v1",
            output_fields=("text",),
            prompt_ref="research@v1",
        ),
        "writer@v1": LLMTaskExecutor(
            client=EchoLLMClient("write"),
            system_prompt="write",
            user_template="{input}",
            schema_ref="draft@v1",
            output_fields=("text",),
            prompt_ref="writer@v1",
        ),
        "editor@v1": LLMTaskExecutor(
            client=EchoLLMClient("edit"),
            system_prompt="edit",
            user_template="{input}",
            schema_ref="final@v1",
            output_fields=("text",),
            prompt_ref="editor@v1",
        ),
    }
    schemas = {
        "researcher@v1": SchemaBinding("notes@v1", _active_schema("notes")),
        "writer@v1": SchemaBinding("draft@v1", _active_schema("draft")),
        "editor@v1": SchemaBinding("final@v1", _active_schema("final")),
    }
    director = ContentDirector(executors, schemas)
    run = Run.create(
        run_id="run-llm", content_brief_ref="brief", workflow_version_ref="health-article@v1"
    )
    director.execute(
        run,
        [
            TaskRequest("step-research", "researcher@v1", "BRIEF", artifact_kind="notes"),
            TaskRequest("step-write", "writer@v1", "", artifact_kind="draft"),
            TaskRequest("step-edit", "editor@v1", "", artifact_kind="final"),
        ],
    )
    assert run.status is RunStatus.COMPLETED
    assert [view.status for view in run.tasks] == [TaskStatus.SUCCEEDED] * 3
    assert all(view.output is not None for view in run.tasks)
    assert len(run.artifacts) == 3
