"""Demonstration: the two migrated production roles (Rin, Leo) running together for real.

Runs the ``research-to-script@v1`` Workflow — Rin (``content_researcher@v1``) then Leo
(``script_writer@v1``) — through the real Composition Root (:func:`compile_runtime`, `ADR-0012`/
`ADR-0013`) and the real Run/Task/Output/Artifact domain, backed by a live Anthropic model. Unlike
``demo.py`` (which hand-wrote its own one-off Research/Writer/Editor prompts before either role was
migrated), this demo exercises the **actual catalogued Agent + Prompt + Schema assets** that
``tests/test_content_researcher_agent.py``, ``tests/test_script_writer_agent.py`` and
``tests/test_research_to_script_workflow.py`` already prove correct in isolation and in a keyless
run — the same wiring here simply uses a real model instead of :class:`FakeLLMClient`.

Run with: ``python demo_factory.py``. A real model call needs ``ANTHROPIC_API_KEY`` in the
environment (optionally ``OMEMO_LLM_MODEL`` to choose the model); without a key the demo explains
how to set it and exits cleanly. No QA, Human Review, publication or external integrations are
involved.
"""

from __future__ import annotations

import os

from demo import DEFAULT_MODEL, print_final_article, safe_print, show_run

from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.composition import compile_runtime
from omemo_content_factory.domain.run import Run
from omemo_content_factory.domain.workflow import Workflow, WorkflowStep
from omemo_content_factory.infrastructure.llm import AnthropicLLMClient

BRIEF = (
    "Тема: почему магний важен для сна и восстановления, для взрослой аудитории. "
    "Цель: короткий вертикальный видео-сценарий, спокойный и доказательный тон, без алармизма."
)

WORKFLOW = Workflow.create(
    workflow_id="research-to-script@v1",
    name="Research -> Script",
    steps=[
        WorkflowStep(
            step_id="research",
            task_type="research",
            agent_ref=rin.AGENT_REF,
            schema_ref=rin.SCHEMA_REF,
        ),
        WorkflowStep(
            step_id="write_script",
            task_type="write_script",
            agent_ref=leo.AGENT_REF,
            schema_ref=leo.SCHEMA_REF,
        ),
    ],
)


def main() -> None:
    """Run the Rin -> Leo Workflow on a real model and print the result."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        safe_print("ANTHROPIC_API_KEY is not set, so no real model can be called.")
        safe_print("Set it and re-run, e.g.:")
        safe_print('  PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."')
        safe_print("  bash:        export ANTHROPIC_API_KEY=sk-ant-...")
        safe_print(f"Optionally set OMEMO_LLM_MODEL (default: {DEFAULT_MODEL}).")
        return

    model = os.environ.get("OMEMO_LLM_MODEL", DEFAULT_MODEL)
    client = AnthropicLLMClient(model=model)
    agents = (*rin.AGENTS, *leo.AGENTS)
    prompts = {**rin.PROMPTS, **leo.PROMPTS}
    schemas = {**rin.SCHEMAS, **leo.SCHEMAS}
    director = compile_runtime(agents, prompts, client, WORKFLOW, schemas)

    run = Run.create(
        run_id="run-magnesium-sleep-001",
        content_brief_ref="brief-magnesium-sleep",
        workflow_version_ref=WORKFLOW.workflow_id,
    )

    safe_print("=" * 78)
    safe_print(f"Rin -> Leo, the real catalogued roles, on model {model}")
    safe_print("=" * 78)
    director.execute_workflow(run, WORKFLOW, brief=BRIEF)
    show_run(run)
    print_final_article(run)


if __name__ == "__main__":
    main()
