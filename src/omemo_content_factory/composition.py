"""Composition Root — a pure build-time graph compiler (the outermost wiring layer).

Per `ADR-0012` / `ADR-0013`: this is the **only** place that resolves `agent_ref → Agent →
prompt_ref → Prompt` and assembles the runtime graph. It is a **dumb, deterministic, build-time**
compiler:

- **constructs** the `agent_ref → TaskExecutor` mapping,
- **injects** a Prompt into its executor **at construction time** (`Prompt.system → system_prompt`,
  `Prompt.user_template → user_template`, `Prompt.schema_ref → schema_ref`),
- **wraps** that executor with the role's statically configured input Skill invocations when
  ``Agent.skill_refs`` declares them (`ADR-0027`),
- **builds** one scoped ``Toolbox`` from ``Agent.tool_refs`` and Tool instances whose outside
  dependencies are injected here (`ADR-0028`),
- **checks structural existence** (build-time only): ``prompt_ref`` and ``Workflow.agent_ref``
  resolve to existing entries — a pure key-presence check, **not** workflow-semantics or policy.

It contains **no execution semantics**: it never runs a Task, never simulates runtime, makes no
runtime decisions, and never interprets Workflow/execution state. It is **not a policy layer**:
workflow-semantics, business-level correctness and domain consistency stay at the domain/
application boundary, never here. All failures are **build-time** (`CompositionError`). Being the
outermost layer it may import domain/application/infrastructure; nothing imports it (dependencies
point inward, `PROJECT.md` §7).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import TypeAlias

from omemo_content_factory.adapters.run_store import RunStore
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.skill_execution import (
    SkillPreprocessingTaskExecutor,
    TaskInputSkillInvocation,
)
from omemo_content_factory.application.task_execution import TaskExecutor
from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.prompt import Prompt, PromptId
from omemo_content_factory.domain.schema import Schema
from omemo_content_factory.domain.workflow import Workflow
from omemo_content_factory.infrastructure.llm import LLMClient, LLMTaskExecutor
from omemo_content_factory.infrastructure.sqlite_run_store import SqliteRunStore
from omemo_content_factory.tools.contract import Tool
from omemo_content_factory.tools.current_date import CurrentDate
from omemo_content_factory.tools.text_metrics import TextMetrics
from omemo_content_factory.tools.toolbox import Toolbox

RUN_STORE_PATH_VAR = "OMEMO_RUN_STORE_PATH"
"""Environment variable naming the SQLite file every Run is saved to (ADR-0026 §5)."""

DEFAULT_RUN_STORE_PATH = Path(".omemo") / "runs.sqlite3"
"""The Run store when ``OMEMO_RUN_STORE_PATH`` is unset or blank, relative to the working dir."""

AgentSkillInvocations: TypeAlias = Mapping[str, Sequence[TaskInputSkillInvocation]]
"""Static ``agent_ref -> ordered Skill invocations`` supplied by role catalogues (ADR-0027)."""


class CompositionError(Exception):
    """A build-time composition-invariant violation (never raised at runtime)."""


def _aware_local_now() -> datetime:
    """Read the host's aware local time; injected into ``current_date@v1`` by the Root."""
    return datetime.now().astimezone()


def build_available_tools(*, clock: Callable[[], datetime] = _aware_local_now) -> tuple[Tool, ...]:
    """Build the Tool library with outside dependencies injected (ADR-0028 §1)."""
    return (CurrentDate(clock), TextMetrics())


def run_store_path(environ: Mapping[str, str]) -> Path:
    """Where the Run store lives: ``OMEMO_RUN_STORE_PATH``, or the default when unset or blank."""
    raw = environ.get(RUN_STORE_PATH_VAR, "").strip()
    return Path(raw) if raw else DEFAULT_RUN_STORE_PATH


def build_run_store(environ: Mapping[str, str]) -> RunStore:
    """Build the ``RunStore`` a real run commits to (ADR-0026 §5): SQLite at :func:`run_store_path`.

    Creates the file's parent directory, since ``SqliteRunStore`` needs it to exist (ADR-0024 §3);
    the database file itself is created on first use.
    """
    path = run_store_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteRunStore(path)


def build_executor_map(
    agents: Iterable[Agent],
    prompts: Mapping[PromptId, Prompt],
    client: LLMClient,
    schemas: Mapping[str, Schema],
    *,
    skill_invocations: AgentSkillInvocations | None = None,
    available_tools: Iterable[Tool] | None = None,
) -> dict[str, TaskExecutor]:
    """Compile the `agent_ref → TaskExecutor` mapping from the static catalogues (build-time).

    Deterministic, uniform construction: each Agent's `prompt_ref` is resolved to a Prompt, and the
    Prompt's `system` / `user_template` plus the **generation shape** projected from the Schema
    (`agent_ref → Prompt → schema_ref → Schema → required_fields`, `ADR-0014` §3 — a dumb structural
    projection, not validation) are injected into a freshly constructed executor, keyed by the
    Agent's `agent_id`. No Task is run and the model is not called here. ``schemas`` is
    **required**: after `ADR-0014` a structured executor cannot exist without a generation shape
    (corollary of the structured-only port; see `ADR-0014` §4). Build-time consistency is enforced
    — a duplicate
    `agent_ref`, an unknown `prompt_ref`, or an unknown `schema_ref` raises ``CompositionError``
    (structural existence checks, `ADR-0012`). Declared ``Agent.skill_refs`` must exactly match the
    role's supplied invocation refs, including order, or composition also fails before execution
    (`ADR-0027`). An empty shape is rejected by the executor's own
    construction invariant (`ADR-0014` §3, Locus 2); the Composition Root passes parameters and does
    not judge configuration correctness. ``available_tools`` defaults to the complete built-in
    library, with an aware local clock injected into ``current_date@v1``. Each executor gets a
    Toolbox scoped by its Agent's ``tool_refs``; an impossible grant fails before execution
    (`ADR-0028`).
    """
    tools = tuple(build_available_tools() if available_tools is None else available_tools)
    executors: dict[str, TaskExecutor] = {}
    for agent in agents:
        if agent.agent_id in executors:
            raise CompositionError(f"duplicate agent_ref '{agent.agent_id}'")
        prompt = prompts.get(agent.prompt_ref)
        if prompt is None:
            raise CompositionError(
                f"agent '{agent.agent_id}' references unknown prompt '{agent.prompt_ref}'"
            )
        schema = schemas.get(prompt.schema_ref)
        if schema is None:
            raise CompositionError(
                f"prompt '{prompt.prompt_id}' references unknown schema '{prompt.schema_ref}'"
            )
        base_executor = LLMTaskExecutor(
            client=client,
            system_prompt=prompt.system,
            user_template=prompt.user_template,
            schema_ref=prompt.schema_ref,
            output_fields=schema.view.required_fields,
            toolbox=Toolbox(grants=agent.tool_refs, available=tools),
        )
        configured = tuple(
            () if skill_invocations is None else skill_invocations.get(agent.agent_id, ())
        )
        configured_refs = tuple(invocation.skill_ref for invocation in configured)
        if configured_refs != agent.skill_refs:
            raise CompositionError(
                f"agent '{agent.agent_id}' declares skill_refs {agent.skill_refs!r}, "
                f"but its invocations are {configured_refs!r}"
            )
        executors[agent.agent_id] = (
            SkillPreprocessingTaskExecutor(base_executor, configured)
            if configured
            else base_executor
        )
    return executors


def build_schema_map(
    agents: Iterable[Agent],
    prompts: Mapping[PromptId, Prompt],
    schemas: Mapping[str, Schema],
) -> dict[str, Schema]:
    """Compile the `agent_ref → Schema` map from the catalogues (build-time, structural only).

    Resolves `agent.prompt_ref → Prompt → prompt.schema_ref → Schema` as pure key-presence lookups
    (duplicate `agent_ref` / unknown `prompt_ref` / unknown `schema_ref` → ``CompositionError``).
    It does **not** validate anything, run a Task, or interpret Schema/Workflow semantics — and this
    map is **not** yet used by execution (wiring is a later sub-slice). Composition-Root scope only
    (`ADR-0012`): structural existence check, no policy.
    """
    result: dict[str, Schema] = {}
    for agent in agents:
        if agent.agent_id in result:
            raise CompositionError(f"duplicate agent_ref '{agent.agent_id}'")
        prompt = prompts.get(agent.prompt_ref)
        if prompt is None:
            raise CompositionError(
                f"agent '{agent.agent_id}' references unknown prompt '{agent.prompt_ref}'"
            )
        schema = schemas.get(prompt.schema_ref)
        if schema is None:
            raise CompositionError(
                f"prompt '{prompt.prompt_id}' references unknown schema '{prompt.schema_ref}'"
            )
        result[agent.agent_id] = schema
    return result


def build_content_director(
    agents: Iterable[Agent],
    prompts: Mapping[PromptId, Prompt],
    client: LLMClient,
    schemas: Mapping[str, Schema],
    *,
    skill_invocations: AgentSkillInvocations | None = None,
    available_tools: Iterable[Tool] | None = None,
    store: RunStore | None = None,
) -> ContentDirector:
    """Compile the executor and schema maps and hand them to a ``ContentDirector``.

    The Content Director only *selects* from the assembled maps at runtime (`ADR-0013` §8); it
    never builds them. ``schemas`` is **required**: it supplies both the generation shape injected
    into each executor (`ADR-0014` §3) and the `agent_ref → Schema` map through which execution
    finalizes Output via the validated path (`ADR-0013` §8, Variant A). ``skill_invocations`` is
    the static role configuration used to wrap agents that declare ``skill_refs`` (`ADR-0027`).
    ``available_tools`` supplies dependency-injected Tool instances; when omitted the Root builds
    the standard library and scopes it independently for every Agent (`ADR-0028`).
    ``store``, when given, is the ``RunStore`` the Director commits every step to (`ADR-0026`).
    """
    executors = build_executor_map(
        agents,
        prompts,
        client,
        schemas,
        skill_invocations=skill_invocations,
        available_tools=available_tools,
    )
    return ContentDirector(executors, build_schema_map(agents, prompts, schemas), store=store)


def validate_workflow_executors(workflow: Workflow, executors: Mapping[str, TaskExecutor]) -> None:
    """Build-time **structural existence check**: every ``Workflow.agent_ref`` is a key in the map.

    A pure key-presence check — it does **not** interpret Workflow semantics (ordering,
    ``depends_on``, step meaning) and is **not** a policy/rules check. It only ensures selection
    will not raise a runtime ``KeyError``: an unknown ``agent_ref`` raises ``CompositionError``
    here, **before any Run executes**. Composition-Root scope only (`ADR-0012`); CD/runtime
    unchanged. Semantic / business-level checks belong to the domain/application boundary, not here.
    """
    missing = sorted({s.agent_ref for s in workflow.steps if s.agent_ref not in executors})
    if missing:
        raise CompositionError(
            f"workflow '{workflow.workflow_id}' references unknown agent_ref(s): {missing}"
        )


def compile_runtime(
    agents: Iterable[Agent],
    prompts: Mapping[PromptId, Prompt],
    client: LLMClient,
    workflow: Workflow,
    schemas: Mapping[str, Schema],
    *,
    skill_invocations: AgentSkillInvocations | None = None,
    available_tools: Iterable[Tool] | None = None,
    store: RunStore | None = None,
) -> ContentDirector:
    """Build executor + schema maps, structurally check vs ``workflow``, wire the CD.

    Build-time graph construction for a given Workflow: a structural existence check (no
    workflow-semantics, no policy) catches an unknown ``agent_ref`` as a ``CompositionError`` here,
    so selection never raises at runtime. ``schemas`` is **required** — it supplies the generation
    shape per executor (`ADR-0014` §3) and the `agent_ref → Schema` map for the validated Output
    path (`ADR-0013` §8, Variant A). ``skill_invocations`` is the static role configuration checked
    and wired according to ``Agent.skill_refs`` (`ADR-0027`). ``available_tools`` is the
    dependency-injected library scoped by each ``Agent.tool_refs`` (`ADR-0028`). ``store``, when
    given, is the ``RunStore`` the Director commits every step to (`ADR-0026`).
    """
    executors = build_executor_map(
        agents,
        prompts,
        client,
        schemas,
        skill_invocations=skill_invocations,
        available_tools=available_tools,
    )
    validate_workflow_executors(workflow, executors)
    return ContentDirector(executors, build_schema_map(agents, prompts, schemas), store=store)
