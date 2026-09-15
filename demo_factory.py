"""Demonstration: the two migrated production roles (Rin, Leo) running together for real.

Runs the ``research-to-script@v1`` Workflow — Rin (``content_researcher@v1``) then Leo
(``script_writer@v1``) — through the real Composition Root (`ADR-0012`/`ADR-0013`) and the real
Run/Task/Output/Artifact domain, backed by a live Anthropic model. Unlike ``demo.py`` (which
hand-wrote its own one-off Research/Writer/Editor prompts before either role was migrated), this
demo exercises the **actual catalogued Agent + Prompt + Schema assets** that
``tests/test_content_researcher_agent.py``, ``tests/test_script_writer_agent.py`` and
``tests/test_research_to_script_workflow.py`` already prove correct in isolation and in a keyless
run.

Each role gets **its own** configured provider/model via :func:`client_for_role` (`ADR-0016`,
`infrastructure/provider_model.py`) — the Composition Root's ``build_executor_map`` is called once
per agent with that role's own client and the results merged, since the Root itself still takes one
shared client per call (`ADR-0012`, unchanged) and per-role selection is a caller concern (SPEC §4).
There is no shared/default model here: a role with no ``OMEMO_PROVIDER__<ROLE>`` binding fails
closed (`ProviderModelSelectionError`), matching this repo's earlier ad-hoc demos never having a
silent hardcoded fallback either.

Run with: ``python demo_factory.py``. Needs ``ANTHROPIC_API_KEY`` plus a per-role
``OMEMO_PROVIDER__<ROLE>`` / ``OMEMO_MODEL__<ROLE>`` binding for each of Rin and Leo (see the
printed instructions, or README.md); missing either, the demo explains what to set and exits
cleanly. No QA, Human Review, publication or external integrations are involved.

The Run is saved after every step to the Run store (`ADR-0026`; ``OMEMO_RUN_STORE_PATH``, default
``.omemo/runs.sqlite3``). Running the demo again resumes the stored Run instead of starting over:
an interrupted run continues where it stopped, a finished one is only shown — no model is called
for work already committed.
"""

from __future__ import annotations

import os

from demo import print_final_article, safe_print, show_run

from omemo_content_factory.agents import content_researcher as rin
from omemo_content_factory.agents import script_writer as leo
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.task_execution import TaskExecutor
from omemo_content_factory.composition import (
    build_executor_map,
    build_run_store,
    build_schema_map,
    run_store_path,
    validate_workflow_executors,
)
from omemo_content_factory.domain.run import Run
from omemo_content_factory.domain.workflow import Workflow, WorkflowStep
from omemo_content_factory.infrastructure.provider_model import (
    ProviderModelSelectionError,
    client_for_role,
)

RUN_ID = "run-magnesium-sleep-001"

BRIEF = (
    "Тема: почему магний важен для сна и восстановления, для взрослой аудитории. "
    "Цель: короткий вертикальный видео-сценарий, спокойный и доказательный тон, без алармизма."
)

AGENTS = (*rin.AGENTS, *leo.AGENTS)
PROMPTS = {**rin.PROMPTS, **leo.PROMPTS}
SCHEMAS = {**rin.SCHEMAS, **leo.SCHEMAS}

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


def _env_token(agent_ref: str) -> str:
    """Mirror ``infrastructure/provider_model.py``'s role -> env-token normalization (SPEC §1.2)."""
    return "".join(ch if ch.isalnum() else "_" for ch in agent_ref).upper()


def _print_binding_instructions() -> None:
    """Print the exact ``OMEMO_PROVIDER__*`` / ``OMEMO_MODEL__*`` exports each role still needs."""
    for agent in AGENTS:
        token = _env_token(agent.agent_id)
        safe_print(f"  export OMEMO_PROVIDER__{token}=anthropic")
        safe_print(f"  export OMEMO_MODEL__{token}=claude-sonnet-4-6")


def _build_executors() -> dict[str, TaskExecutor]:
    """Resolve each role's own provider/model via `client_for_role` (ADR-0016) — no shared client.

    `build_executor_map` takes one client per call, so each agent is compiled on its own (a
    single-element agents list) with its own resolved client, and the resulting one-entry maps are
    merged — the Composition Root itself is unchanged (ADR-0012); this is caller-side per-role
    selection (PROVIDER_MODEL_SPEC §4).
    """
    executors: dict[str, TaskExecutor] = {}
    for agent in AGENTS:
        role_client = client_for_role(agent.agent_id, os.environ)
        executors.update(build_executor_map([agent], PROMPTS, role_client, SCHEMAS))
    return executors


def main() -> None:
    """Run the Rin -> Leo Workflow, each role on its own configured provider/model."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        safe_print("ANTHROPIC_API_KEY is not set, so no real model can be called.")
        safe_print("Set it and re-run, e.g.:")
        safe_print('  PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."')
        safe_print("  bash:        export ANTHROPIC_API_KEY=sk-ant-...")
        return

    try:
        executors = _build_executors()
    except ProviderModelSelectionError as exc:
        safe_print(f"Provider/model selection failed: {exc}")
        safe_print("Each role needs its own binding (ADR-0016) — no shared/default model. Set:")
        _print_binding_instructions()
        return

    validate_workflow_executors(WORKFLOW, executors)
    store = build_run_store(os.environ)
    director = ContentDirector(executors, build_schema_map(AGENTS, PROMPTS, SCHEMAS), store=store)

    safe_print("=" * 78)
    safe_print("Rin -> Leo, the real catalogued roles, each on its own configured provider/model")
    safe_print("=" * 78)
    run = store.load(RUN_ID)
    if run is None:
        run = Run.create(
            run_id=RUN_ID,
            content_brief_ref="brief-magnesium-sleep",
            workflow_version_ref=WORKFLOW.workflow_id,
        )
        director.execute_workflow(run, WORKFLOW, brief=BRIEF)
    else:
        location = run_store_path(os.environ)
        safe_print(f"Resuming {RUN_ID} from {location} at '{run.status.value}';")
        safe_print("committed steps are not run again. Delete that file to start over.")
        director.resume_workflow(run, WORKFLOW, brief=BRIEF)
    show_run(run)
    print_final_article(run)


if __name__ == "__main__":
    main()
