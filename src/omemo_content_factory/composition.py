"""Composition Root — a pure build-time graph compiler (the outermost wiring layer).

Per `ADR-0012` / `ADR-0013`: this is the **only** place that resolves `agent_ref → Agent →
prompt_ref → Prompt` and assembles the runtime graph. It is a **dumb, deterministic, build-time**
compiler:

- **constructs** the `agent_ref → TaskExecutor` mapping,
- **loads** the bundled versioned Prompt store when no explicit Prompt mapping is supplied,
- **injects** a Prompt into its executor **at construction time** (`Prompt.system → system_prompt`,
  `Prompt.user_template → user_template`, `Prompt.schema_ref → schema_ref`, and the exact
  `<prompt_id>@v<version>` used for analytics),
- **wraps** that executor with the role's statically configured input Skill invocations when
  ``Agent.skill_refs`` declares them (`ADR-0027`),
- **builds** one scoped ``Toolbox`` from ``Agent.tool_refs`` and Tool instances whose outside
  dependencies are injected here (`ADR-0028`),
- **builds** the QA role's ``ArtifactEvaluator`` from its catalogue entry the same way
  (`ADR-0038`),
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

import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import TypeAlias

from omemo_content_factory.adapters.brief_board import BriefBoard
from omemo_content_factory.adapters.review_desk import ReviewDesk
from omemo_content_factory.adapters.run_store import RunIndex, RunStore
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.qa_evaluation import ArtifactEvaluator
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.skill_execution import (
    SkillPreprocessingTaskExecutor,
    TaskInputSkillInvocation,
)
from omemo_content_factory.application.task_execution import TaskExecutor
from omemo_content_factory.domain.agent import Agent
from omemo_content_factory.domain.prompt import Prompt, PromptId, PromptVersion
from omemo_content_factory.domain.schema import Schema
from omemo_content_factory.domain.workflow import Workflow
from omemo_content_factory.infrastructure.google_docs_review_desk import (
    GoogleDocsReviewDesk,
    google_docs_settings_from_env,
)
from omemo_content_factory.infrastructure.llm import (
    LLMArtifactEvaluator,
    LLMClient,
    LLMTaskExecutor,
)
from omemo_content_factory.infrastructure.notion_brief_board import (
    NotionBriefBoard,
    notion_settings_from_env,
)
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

PromptCatalogueInput: TypeAlias = Mapping[PromptId, Prompt] | None
"""Explicit Prompt data, or ``None`` to read the bundled versioned store (ADR-0030)."""

PROMPT_CATALOGUE_PACKAGE = "omemo_content_factory.prompts"
PROMPT_CATALOGUE_RESOURCE = "catalogue.toml"
_PROMPT_FIELDS = frozenset({"prompt_id", "version", "schema_ref", "system", "user_template"})


class CompositionError(Exception):
    """A build-time composition-invariant violation (never raised at runtime)."""


def _required_text(record: Mapping[object, object], field: str, index: int) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CompositionError(
            f"prompt catalogue record {index} field '{field}' must be a non-blank string"
        )
    return value


def _prompt_from_record(raw: object, index: int) -> Prompt:
    if not isinstance(raw, dict):
        raise CompositionError(f"prompt catalogue record {index} must be a table")
    record: Mapping[object, object] = raw
    fields = set(record)
    if fields != _PROMPT_FIELDS:
        missing = sorted(_PROMPT_FIELDS - fields)
        extra = sorted(str(field) for field in fields - _PROMPT_FIELDS)
        raise CompositionError(
            f"prompt catalogue record {index} has invalid fields; missing={missing}, extra={extra}"
        )
    version = record.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise CompositionError(
            f"prompt catalogue record {index} field 'version' must be an int >= 1"
        )
    return Prompt(
        prompt_id=_required_text(record, "prompt_id", index),
        version=PromptVersion(version),
        schema_ref=_required_text(record, "schema_ref", index),
        system=_required_text(record, "system", index),
        user_template=_required_text(record, "user_template", index),
    )


def load_prompt_catalogue(path: Path | None = None) -> dict[PromptId, Prompt]:
    """Materialize the versioned Prompt store as immutable domain descriptors (ADR-0030).

    ``path`` is an explicit filesystem source for embedding/tests. When omitted, the bundled
    package resource is read through :mod:`importlib.resources`, so installed wheels work without
    assuming an unpacked source tree. Parsing is strict and atomic: malformed data raises
    :class:`CompositionError` before any partial mapping is returned.
    """
    try:
        text = (
            resources.files(PROMPT_CATALOGUE_PACKAGE)
            .joinpath(PROMPT_CATALOGUE_RESOURCE)
            .read_text(encoding="utf-8")
            if path is None
            else path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ModuleNotFoundError) as exc:
        raise CompositionError("cannot read prompt catalogue") from exc
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CompositionError("prompt catalogue is not valid TOML") from exc
    if set(document) != {"prompts"}:
        raise CompositionError("prompt catalogue root must contain only 'prompts'")
    raw_prompts = document.get("prompts")
    if not isinstance(raw_prompts, list) or not raw_prompts:
        raise CompositionError("prompt catalogue 'prompts' must be a non-empty array of tables")

    catalogue: dict[PromptId, Prompt] = {}
    for index, raw in enumerate(raw_prompts, start=1):
        prompt = _prompt_from_record(raw, index)
        if prompt.prompt_id in catalogue:
            raise CompositionError(f"duplicate prompt_id '{prompt.prompt_id}' in prompt catalogue")
        catalogue[prompt.prompt_id] = prompt
    return catalogue


def _resolve_prompts(prompts: PromptCatalogueInput) -> Mapping[PromptId, Prompt]:
    return load_prompt_catalogue() if prompts is None else prompts


def _prompt_and_schema(
    agent: Agent, prompts: Mapping[PromptId, Prompt], schemas: Mapping[str, Schema]
) -> tuple[Prompt, Schema]:
    """Resolve ``agent.prompt_ref → Prompt → schema_ref → Schema`` by key presence only."""
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
    return prompt, schema


def _prompt_version_ref(prompt: Prompt) -> str:
    return f"{prompt.prompt_id}@v{prompt.version.value}"


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


def build_run_index(environ: Mapping[str, str]) -> RunIndex:
    """Build the ``RunIndex`` over the same file :func:`build_run_store` uses (ADR-0048 §2)."""
    path = run_store_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteRunStore(path)


def build_brief_board(environ: Mapping[str, str]) -> BriefBoard:
    """Build the ``BriefBoard`` a real entrypoint reads briefs from (ROADMAP Stage 9, ADR-0040).

    Reads the seven ``OMEMO_NOTION_*`` variables (:func:`notion_settings_from_env`); a missing or
    blank one fails closed with ``BriefBoardError`` before any request is made, same shape as
    :func:`client_for_role`'s fail-closed binding.
    """
    return NotionBriefBoard(notion_settings_from_env(environ))


def build_review_desk(environ: Mapping[str, str]) -> ReviewDesk:
    """Build the ``ReviewDesk`` a real entrypoint publishes reviews to (ROADMAP Stage 10, ADR-0044).

    Reads ``OMEMO_GOOGLE_SERVICE_ACCOUNT_FILE`` and ``OMEMO_GOOGLE_REVIEW_FOLDER_ID``
    (:func:`google_docs_settings_from_env`, ADR-0043): a missing variable or an unusable key file
    fails closed with ``ReviewDeskError`` before any request is made.
    """
    return GoogleDocsReviewDesk(google_docs_settings_from_env(environ))


def build_executor_map(
    agents: Iterable[Agent],
    prompts: PromptCatalogueInput,
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
    (`ADR-0028`). ``prompts=None`` reads the bundled, versioned store exactly once for this build;
    an explicit mapping is used as-is (`ADR-0030`).
    """
    resolved_prompts = _resolve_prompts(prompts)
    tools = tuple(build_available_tools() if available_tools is None else available_tools)
    executors: dict[str, TaskExecutor] = {}
    for agent in agents:
        if agent.agent_id in executors:
            raise CompositionError(f"duplicate agent_ref '{agent.agent_id}'")
        prompt, schema = _prompt_and_schema(agent, resolved_prompts, schemas)
        base_executor = LLMTaskExecutor(
            client=client,
            system_prompt=prompt.system,
            user_template=prompt.user_template,
            schema_ref=prompt.schema_ref,
            output_fields=schema.view.required_fields,
            prompt_ref=_prompt_version_ref(prompt),
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


def build_qa_evaluator(
    agent: Agent,
    prompts: PromptCatalogueInput,
    client: LLMClient,
    schemas: Mapping[str, Schema],
    *,
    available_tools: Iterable[Tool] | None = None,
) -> LLMArtifactEvaluator:
    """Compile one verdict-answering role into its ``ArtifactEvaluator`` (build-time, `ADR-0038`).

    The QA counterpart of :func:`build_executor_map`: ``agent.prompt_ref → Prompt → schema_ref →
    Schema`` by the same structural lookups, then the Prompt's text, the Schema's
    ``required_fields`` as the generation shape, ``<prompt_id>@v<version>``, the Agent's id as
    ``evaluator_ref`` and a Toolbox scoped by ``agent.tool_refs`` are injected. An Agent declaring
    ``skill_refs`` is refused: the QA path has no Skill invocation seam (`ADR-0035` §4). A shape
    without the verdict fields is rejected by the evaluator's own invariant. The model is not
    called. ``prompts=None`` reads the bundled store (`ADR-0030`).
    """
    if agent.skill_refs:
        raise CompositionError(
            f"agent '{agent.agent_id}' declares skill_refs {agent.skill_refs!r}, "
            "but a QA evaluator has no Skill invocation seam"
        )
    prompt, schema = _prompt_and_schema(agent, _resolve_prompts(prompts), schemas)
    tools = tuple(build_available_tools() if available_tools is None else available_tools)
    return LLMArtifactEvaluator(
        client=client,
        system_prompt=prompt.system,
        user_template=prompt.user_template,
        output_fields=schema.view.required_fields,
        prompt_ref=_prompt_version_ref(prompt),
        evaluator_ref=agent.agent_id,
        toolbox=Toolbox(grants=agent.tool_refs, available=tools),
    )


def build_schema_map(
    agents: Iterable[Agent],
    prompts: PromptCatalogueInput,
    schemas: Mapping[str, Schema],
) -> dict[str, SchemaBinding]:
    """Compile the `agent_ref → SchemaBinding` map (build-time, structural only).

    Resolves `agent.prompt_ref → Prompt → prompt.schema_ref → Schema` as pure key-presence lookups
    (duplicate `agent_ref` / unknown `prompt_ref` / unknown `schema_ref` → ``CompositionError``).
    The resulting immutable binding preserves the exact opaque catalogue key beside the resolved
    authority (ADR-0031). It does **not** validate anything, run a Task, or interpret
    Schema/Workflow semantics. Composition-Root scope only (`ADR-0012`): structural existence
    check, no policy.
    ``prompts=None`` reads the bundled Prompt store (`ADR-0030`).
    """
    resolved_prompts = _resolve_prompts(prompts)
    result: dict[str, SchemaBinding] = {}
    for agent in agents:
        if agent.agent_id in result:
            raise CompositionError(f"duplicate agent_ref '{agent.agent_id}'")
        prompt, schema = _prompt_and_schema(agent, resolved_prompts, schemas)
        result[agent.agent_id] = SchemaBinding(prompt.schema_ref, schema)
    return result


def build_content_director(
    agents: Iterable[Agent],
    prompts: PromptCatalogueInput,
    client: LLMClient,
    schemas: Mapping[str, Schema],
    *,
    skill_invocations: AgentSkillInvocations | None = None,
    available_tools: Iterable[Tool] | None = None,
    store: RunStore | None = None,
    qa: ArtifactEvaluator | None = None,
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
    ``qa``, when given, is the evaluator of the ``WAITING_QA`` gate (`ADR-0018`, `ADR-0038`).
    ``prompts=None`` reads one bundled Prompt catalogue snapshot and shares it across both maps
    (`ADR-0030`).
    """
    resolved_prompts = _resolve_prompts(prompts)
    executors = build_executor_map(
        agents,
        resolved_prompts,
        client,
        schemas,
        skill_invocations=skill_invocations,
        available_tools=available_tools,
    )
    return ContentDirector(
        executors, build_schema_map(agents, resolved_prompts, schemas), qa=qa, store=store
    )


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


def validate_qa_evaluator(workflow: Workflow, qa: ArtifactEvaluator) -> None:
    """Build-time check that the QA role is not also a Workflow step (`ADR-0035` §4, `ADR-0038`).

    A verdict-answering role yields no Output, so a step bound to ``qa.evaluator_ref`` raises
    ``CompositionError`` before any Run executes. Structural only, like
    :func:`validate_workflow_executors`.
    """
    steps = sorted({s.step_id for s in workflow.steps if s.agent_ref == qa.evaluator_ref})
    if steps:
        raise CompositionError(
            f"workflow '{workflow.workflow_id}' uses the QA evaluator '{qa.evaluator_ref}' "
            f"as step(s) {steps}"
        )


def compile_runtime(
    agents: Iterable[Agent],
    prompts: PromptCatalogueInput,
    client: LLMClient,
    workflow: Workflow,
    schemas: Mapping[str, Schema],
    *,
    skill_invocations: AgentSkillInvocations | None = None,
    available_tools: Iterable[Tool] | None = None,
    store: RunStore | None = None,
    qa: ArtifactEvaluator | None = None,
) -> ContentDirector:
    """Build executor + schema maps, structurally check vs ``workflow``, wire the CD.

    Build-time graph construction for a given Workflow: a structural existence check (no
    workflow-semantics, no policy) catches an unknown ``agent_ref`` as a ``CompositionError`` here,
    so selection never raises at runtime. ``schemas`` is **required** — it supplies the generation
    shape per executor (`ADR-0014` §3) and the `agent_ref → Schema` map for the validated Output
    path (`ADR-0013` §8, Variant A). ``skill_invocations`` is the static role configuration checked
    and wired according to ``Agent.skill_refs`` (`ADR-0027`). ``available_tools`` is the
    dependency-injected library scoped by each ``Agent.tool_refs`` (`ADR-0028`). ``store``, when
    given, is the ``RunStore`` the Director commits every step to (`ADR-0026`). ``qa``, when given,
    is the ``WAITING_QA`` gate's evaluator; its role must not be a step (`ADR-0038`).
    ``prompts=None`` reads one bundled Prompt catalogue snapshot for the whole graph (`ADR-0030`).
    """
    resolved_prompts = _resolve_prompts(prompts)
    executors = build_executor_map(
        agents,
        resolved_prompts,
        client,
        schemas,
        skill_invocations=skill_invocations,
        available_tools=available_tools,
    )
    validate_workflow_executors(workflow, executors)
    if qa is not None:
        validate_qa_evaluator(workflow, qa)
    return ContentDirector(
        executors, build_schema_map(agents, resolved_prompts, schemas), qa=qa, store=store
    )
