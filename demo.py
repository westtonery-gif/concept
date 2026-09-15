"""Demonstration: the first real AI run on the existing architecture.

Runs one Workflow — Research -> Writer -> Editor — through the real ``ContentDirector`` and the
real Run/Task/Output/Artifact domain, with each role backed by a live Anthropic model behind the
application's ``TaskExecutor`` port. The domain knows nothing of the model; the LLM lives only in
the infrastructure layer.

It shows, for the run: the Run status, each Task's status, each Task's Output, the Artifacts (with
provenance ``Task -> Output -> Artifact``), the domain-event journal, and the final article.

Run with: ``python demo.py``. A real model call needs ``ANTHROPIC_API_KEY`` plus explicit input /
output token prices and currency in the environment (`ADR-0029`; optionally ``OMEMO_LLM_MODEL``
to choose the model). Missing configuration is explained and the demo exits cleanly. No QA,
Human Review, publication or external integrations are involved.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from omemo_content_factory.application.content_director import ContentDirector, TaskRequest
from omemo_content_factory.application.task_execution import TaskExecutor
from omemo_content_factory.domain.artifact import ArtifactCreated, ArtifactEvent
from omemo_content_factory.domain.output import OutputEvent
from omemo_content_factory.domain.run import Run, RunEvent, RunFailed
from omemo_content_factory.domain.schema import Schema, SchemaStatus, SchemaVersion
from omemo_content_factory.domain.task import TaskEvent, TaskFailed
from omemo_content_factory.infrastructure.llm import (
    AnthropicLLMClient,
    LLMTaskExecutor,
    TokenPricing,
)

DEFAULT_MODEL = "claude-sonnet-4-6"

BRIEF = (
    "Topic: the health benefits of magnesium for a general adult audience. "
    "Goal: a short, trustworthy, evidence-based article (~400-500 words) with a clear "
    "structure and a calm, non-alarmist tone."
)

RESEARCH_PROMPT = (
    "You are a meticulous health-content researcher for OMEMO Health. From the content brief, "
    "produce concise, evidence-based research notes: key facts, a suggested article structure, "
    "and any necessary medical caveats. Be accurate and non-alarmist. Do not fabricate studies "
    "or statistics."
)
WRITER_PROMPT = (
    "You are a health-content writer for OMEMO Health. From the research notes, write a clear, "
    "engaging article draft for a general audience, following the suggested structure. Keep every "
    "claim evidence-based and non-alarmist. Include a brief, general medical disclaimer."
)
EDITOR_PROMPT = (
    "You are a health-content editor for OMEMO Health. From the article draft, produce the final "
    "polished article: improve clarity, flow and accuracy, keep a calm non-alarmist tone, and "
    "keep a brief medical disclaimer. Return only the final article text."
)


def _active_schema(schema_ref: str, field_name: str) -> Schema:
    """An ACTIVE Schema requiring the role's single output field (`ADR-0014` generation shape)."""
    schema_id, _, version = schema_ref.partition("@v")
    schema = Schema.create(
        schema_id=schema_id,
        version=SchemaVersion(int(version)),
        description=f"{schema_id} output contract",
        required_fields=[field_name],
    )
    schema.transition(SchemaStatus.ACTIVE)
    return schema


def safe_print(text: str = "") -> None:
    """Print, replacing characters the console cannot encode (robust on any locale)."""
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.write(text.encode(encoding, errors="replace").decode(encoding) + "\n")


def short(text: str, limit: int = 100) -> str:
    """One-line, length-bounded preview of a (possibly long) model output."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "..."


def describe_event(event: RunEvent | TaskEvent | OutputEvent | ArtifactEvent) -> str:
    """Render one domain event as a short human-readable line."""
    name = type(event).__name__
    if isinstance(event, ArtifactCreated):
        return f"{name}(artifact={event.artifact_id}, from_output={event.output_ref})"
    if isinstance(event, OutputEvent):
        return f"{name}(task={event.task_id}, output={event.output_id})"
    if isinstance(event, TaskFailed):
        return f"{name}(task={event.task_id}, reason={event.reason})"
    if isinstance(event, TaskEvent):
        return f"{name}(task={event.task_id})"
    if isinstance(event, RunFailed):
        return f"{name}(reason={event.reason})"
    return name


def show_run(run: Run) -> None:
    """Print the Task results, Artifacts (with provenance), event journal and final status."""
    safe_print(f"Run: {run.run_id}")
    safe_print(f"  brief={run.content_brief_ref}, workflow={run.workflow_version_ref}")

    safe_print("  Tasks:")
    for view in run.tasks:
        detail = f"attempts={view.attempt_count}"
        if view.failure_reason is not None:
            detail += f", reason={view.failure_reason}"
        columns = f"{view.workflow_step_ref:<10} {view.agent_ref:<14} {view.status.value:<10}"
        safe_print(f"    - {columns} ({detail})")
        if view.output is not None:
            safe_print(f"        -> Output {view.output.output_id} [{view.output.status.value}]")
            safe_print(f"           preview: {short(view.output.payload)}")

    if run.artifacts:
        origin = {
            view.output.output_id: view.workflow_step_ref
            for view in run.tasks
            if view.output is not None
        }
        safe_print("  Artifacts (provenance Task -> Output -> Artifact):")
        for artifact in run.artifacts:
            step = origin.get(artifact.output_ref, "?")
            safe_print(
                f"    - {artifact.artifact_id} [{artifact.status.value}] kind={artifact.kind}"
            )
            safe_print(f"        from: task {step} -> output {artifact.output_ref}")

    safe_print("  Event journal:")
    for index, event in enumerate(run.events, start=1):
        safe_print(f"    {index:>2}. {describe_event(event)}")

    safe_print(f"  Final Run status: {run.status.value.upper()}")


def print_final_article(run: Run) -> None:
    """Print the final article — the last Task's Output."""
    final = run.tasks[-1].output if run.tasks else None
    safe_print("")
    safe_print("=" * 78)
    safe_print("FINAL ARTICLE")
    safe_print("=" * 78)
    if final is None:
        safe_print("(no final article — the run did not produce a final Output)")
        return
    # Output.payload is the deterministic serialization of the structured fields (ADR-0014 §6);
    # render the field values for readability, falling back to the raw payload.
    try:
        fields = json.loads(final.payload)
    except ValueError:
        safe_print(final.payload)
        return
    for value in fields.values():
        safe_print(str(value))


def main() -> None:
    """Run the Research -> Writer -> Editor workflow on a real model and print the result."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        safe_print("ANTHROPIC_API_KEY is not set, so no real model can be called.")
        safe_print("Set it and re-run, e.g.:")
        safe_print('  PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."')
        safe_print("  bash:        export ANTHROPIC_API_KEY=sk-ant-...")
        safe_print(f"Optionally set OMEMO_LLM_MODEL (default: {DEFAULT_MODEL}).")
        return

    model = os.environ.get("OMEMO_LLM_MODEL", DEFAULT_MODEL)
    try:
        pricing = TokenPricing(
            Decimal(os.environ["OMEMO_LLM_INPUT_PRICE_PER_MILLION"]),
            Decimal(os.environ["OMEMO_LLM_OUTPUT_PRICE_PER_MILLION"]),
            os.environ["OMEMO_LLM_PRICE_CURRENCY"],
        )
    except (KeyError, InvalidOperation, ValueError) as exc:
        safe_print(f"Explicit token pricing is missing or invalid: {exc}")
        safe_print("Set OMEMO_LLM_INPUT_PRICE_PER_MILLION, ")
        safe_print("OMEMO_LLM_OUTPUT_PRICE_PER_MILLION and OMEMO_LLM_PRICE_CURRENCY.")
        return
    client = AnthropicLLMClient(model=model, pricing=pricing)
    executors: Mapping[str, TaskExecutor] = {
        "researcher@v1": LLMTaskExecutor(
            client=client,
            system_prompt=RESEARCH_PROMPT,
            user_template="{input}",
            schema_ref="research-notes@v1",
            output_fields=("notes",),
            prompt_ref="demo-research@v1",
        ),
        "writer@v1": LLMTaskExecutor(
            client=client,
            system_prompt=WRITER_PROMPT,
            user_template="{input}",
            schema_ref="article-draft@v1",
            output_fields=("draft",),
            prompt_ref="demo-writer@v1",
        ),
        "editor@v1": LLMTaskExecutor(
            client=client,
            system_prompt=EDITOR_PROMPT,
            user_template="{input}",
            schema_ref="final-article@v1",
            output_fields=("article",),
            prompt_ref="demo-editor@v1",
        ),
    }
    # Per-role ACTIVE Schemas (the generation shape + the validated-Output authority, ADR-0014).
    schemas: Mapping[str, Schema] = {
        "researcher@v1": _active_schema("research-notes@v1", "notes"),
        "writer@v1": _active_schema("article-draft@v1", "draft"),
        "editor@v1": _active_schema("final-article@v1", "article"),
    }
    director = ContentDirector(executors, schemas)

    run = Run.create(
        run_id="run-magnesium-001",
        content_brief_ref="brief-magnesium",
        workflow_version_ref="health-article@v1",
    )
    tasks = [
        TaskRequest("research", "researcher@v1", BRIEF, artifact_kind="research-notes"),
        TaskRequest("write", "writer@v1", "", artifact_kind="article-draft"),
        TaskRequest("edit", "editor@v1", "", artifact_kind="final-article"),
    ]

    safe_print("=" * 78)
    safe_print(f"First real AI run - Research -> Writer -> Editor on model {model}")
    safe_print("=" * 78)
    director.execute(run, tasks)
    show_run(run)
    print_final_article(run)


if __name__ == "__main__":
    main()
