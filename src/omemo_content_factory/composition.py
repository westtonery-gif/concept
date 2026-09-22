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
from omemo_content_factory.adapters.clip_renderer import ClipRenderer
from omemo_content_factory.adapters.episode_board import EpisodeBoard
from omemo_content_factory.adapters.episode_source import EpisodeSource
from omemo_content_factory.adapters.footage_index import FootageIndex
from omemo_content_factory.adapters.review_desk import ReviewDesk, ReviewDeskError
from omemo_content_factory.adapters.run_store import RunIndex, RunStore
from omemo_content_factory.agents import clip_post_writer
from omemo_content_factory.application.brief_production import BriefProduction
from omemo_content_factory.application.clip_format import ClipFormatLimits
from omemo_content_factory.application.clip_production import (
    ClipProduction,
    ClipSettings,
    Posting,
    PostWriting,
)
from omemo_content_factory.application.content_director import ContentDirector
from omemo_content_factory.application.qa_evaluation import (
    ArtifactEvaluator,
    ContextualArtifactEvaluator,
)
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
from omemo_content_factory.infrastructure.ffmpeg_clip_renderer import FfmpegClipRenderer
from omemo_content_factory.infrastructure.file_system_episode_source import (
    FileSystemEpisodeSource,
    episode_root_from_env,
)
from omemo_content_factory.infrastructure.google_docs_review_desk import (
    REVIEW_FOLDER_ID_VAR as GOOGLE_REVIEW_FOLDER_ID_VAR,
)
from omemo_content_factory.infrastructure.google_docs_review_desk import (
    SERVICE_ACCOUNT_FILE_VAR as GOOGLE_SERVICE_ACCOUNT_FILE_VAR,
)
from omemo_content_factory.infrastructure.google_docs_review_desk import (
    GoogleDocsReviewDesk,
    google_docs_settings_from_env,
)
from omemo_content_factory.infrastructure.llm import (
    LLMArtifactEvaluator,
    LLMClient,
    LLMTaskExecutor,
)
from omemo_content_factory.infrastructure.local_footage_index import (
    LocalFootageIndex,
    whisper_settings_from_env,
)
from omemo_content_factory.infrastructure.notion_brief_board import (
    NotionBriefBoard,
    notion_settings_from_env,
)
from omemo_content_factory.infrastructure.notion_episode_board import (
    NotionEpisodeBoard,
    notion_episode_settings_from_env,
)
from omemo_content_factory.infrastructure.notion_review_desk import (
    DATABASE_ID_VAR as NOTION_REVIEW_DATABASE_ID_VAR,
)
from omemo_content_factory.infrastructure.notion_review_desk import (
    DECISION_PROPERTY_VAR as NOTION_REVIEW_DECISION_PROPERTY_VAR,
)
from omemo_content_factory.infrastructure.notion_review_desk import (
    FINGERPRINT_PROPERTY_VAR as NOTION_REVIEW_FINGERPRINT_PROPERTY_VAR,
)
from omemo_content_factory.infrastructure.notion_review_desk import (
    REASON_PROPERTY_VAR as NOTION_REVIEW_REASON_PROPERTY_VAR,
)
from omemo_content_factory.infrastructure.notion_review_desk import (
    REVIEW_ID_PROPERTY_VAR as NOTION_REVIEW_ID_PROPERTY_VAR,
)
from omemo_content_factory.infrastructure.notion_review_desk import (
    TITLE_PROPERTY_VAR as NOTION_REVIEW_TITLE_PROPERTY_VAR,
)
from omemo_content_factory.infrastructure.notion_review_desk import (
    TOKEN_VAR as NOTION_REVIEW_TOKEN_VAR,
)
from omemo_content_factory.infrastructure.notion_review_desk import (
    NotionReviewDesk,
    notion_review_settings_from_env,
)
from omemo_content_factory.infrastructure.production_service import (
    ProductionService,
    service_settings_from_env,
)
from omemo_content_factory.infrastructure.sqlite_run_store import SqliteRunStore
from omemo_content_factory.infrastructure.upload_post_publisher import (
    UploadPostPublisher,
    upload_post_settings_from_env,
)
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


NOTION_REVIEW_DESK_VARS: tuple[str, ...] = (
    NOTION_REVIEW_TOKEN_VAR,
    NOTION_REVIEW_DATABASE_ID_VAR,
    NOTION_REVIEW_TITLE_PROPERTY_VAR,
    NOTION_REVIEW_ID_PROPERTY_VAR,
    NOTION_REVIEW_DECISION_PROPERTY_VAR,
    NOTION_REVIEW_REASON_PROPERTY_VAR,
    NOTION_REVIEW_FINGERPRINT_PROPERTY_VAR,
)
"""Every variable the Notion desk needs; **any** of them present selects it (ADR-0060)."""

GOOGLE_REVIEW_DESK_VARS: tuple[str, ...] = (
    GOOGLE_SERVICE_ACCOUNT_FILE_VAR,
    GOOGLE_REVIEW_FOLDER_ID_VAR,
)
"""Every variable the Google Docs desk needs (ADR-0043)."""


def build_review_desk(environ: Mapping[str, str]) -> ReviewDesk:
    """Build the ``ReviewDesk`` a real entrypoint publishes reviews to (ADR-0044, ADR-0060).

    Two implementations of the port exist, so the environment chooses. The rule is **presence as an
    opt-in**, with no silent fallback: setting *any* ``OMEMO_REVIEW_NOTION_*`` variable selects the
    Notion desk (ADR-0060), otherwise any ``OMEMO_GOOGLE_*`` variable selects the Google Docs desk
    (ADR-0043). A partly configured desk is **not** quietly replaced by the other one — it fails
    closed naming the variables its own implementation still needs, because an operator who set
    half of Notion's variables meant Notion.

    With neither configured, ``ReviewDeskError`` names both sets. Entrypoints that treat "no desk at
    all" as a legitimate state check the variables first (``demo_notion.py``, ADR-0044 §5).
    """
    if any(environ.get(name, "").strip() for name in NOTION_REVIEW_DESK_VARS):
        return NotionReviewDesk(notion_review_settings_from_env(environ))
    if any(environ.get(name, "").strip() for name in GOOGLE_REVIEW_DESK_VARS):
        return GoogleDocsReviewDesk(google_docs_settings_from_env(environ))
    raise ReviewDeskError(
        "no review desk is configured; set "
        + ", ".join(NOTION_REVIEW_DESK_VARS)
        + " for the Notion desk, or "
        + ", ".join(GOOGLE_REVIEW_DESK_VARS)
        + " for the Google Docs desk"
    )


CHUNK_MS_VAR = "OMEMO_CLIP_CHUNK_MS"
MAX_MS_VAR = "OMEMO_CLIP_MAX_MS"
PAUSE_TOLERANCE_MS_VAR = "OMEMO_CLIP_PAUSE_TOLERANCE_MS"
MAX_DURATION_MS_VAR = "OMEMO_CLIP_MAX_DURATION_MS"
CONTAINERS_VAR = "OMEMO_CLIP_CONTAINERS"
CLIP_DESTINATION_VAR = "OMEMO_CLIP_DESTINATION"

_CLIP_DEFAULTS = {
    CHUNK_MS_VAR: 120_000,
    MAX_MS_VAR: 120_000,
    PAUSE_TOLERANCE_MS_VAR: 2_000,
    MAX_DURATION_MS_VAR: 180_000,
}
"""Two-minute pieces, a two-minute ceiling, a two-second nudge and a three-minute platform cap.

Unlike the Notion property names (`ADR-0040`), these **do** have defaults: a wrong column name
silently binds the core to someone else's board, while a clip length is a product choice that is
safe to start somewhere and tune from the first real episode (`ADR-0064` Deferred).
"""


def _clip_number(environ: Mapping[str, str], name: str) -> int:
    raw = environ.get(name, "").strip()
    if not raw:
        return _CLIP_DEFAULTS[name]
    try:
        value = int(raw)
    except ValueError:
        raise CompositionError(f"{name} must be a whole number of milliseconds") from None
    if value <= 0:
        raise CompositionError(f"{name} must be positive")
    return value


def build_episode_board(environ: Mapping[str, str]) -> EpisodeBoard:
    """Build the Notion `EpisodeBoard` (ADR-0055); a missing variable fails closed, named."""
    return NotionEpisodeBoard(notion_episode_settings_from_env(environ))


def build_episode_source(environ: Mapping[str, str]) -> EpisodeSource:
    """Build the filesystem `EpisodeSource` over `OMEMO_EPISODE_ROOT` (ADR-0053 §7)."""
    return FileSystemEpisodeSource(episode_root_from_env(environ))


def build_footage_index(environ: Mapping[str, str]) -> FootageIndex:
    """Build the local ffmpeg + whisper.cpp `FootageIndex` (ADR-0064); no vendor, no account."""
    return LocalFootageIndex(whisper_settings_from_env(environ))


CLIP_CANVAS_VAR = "OMEMO_CLIP_CANVAS"
_DEFAULT_CLIP_CANVAS = "2160x3840"


def build_clip_renderer(environ: Mapping[str, str]) -> ClipRenderer:
    """Build the ffmpeg `ClipRenderer` (ADR-0063/0071/0075).

    `OMEMO_CLIP_CANVAS` is `WIDTHxHEIGHT` (default `2160x3840`, ADR-0076: the picture uncropped,
    no downscale, between black bars kept for banners) or `source` to keep the source frame.
    """
    raw = environ.get(CLIP_CANVAS_VAR, "").strip().casefold() or _DEFAULT_CLIP_CANVAS
    if raw == "source":
        return FfmpegClipRenderer()
    width, sep, height = raw.partition("x")
    try:
        canvas = (int(width), int(height))
    except ValueError:
        raise CompositionError(f"{CLIP_CANVAS_VAR} must be WIDTHxHEIGHT or 'source'") from None
    if not sep or any(side <= 0 or side % 2 for side in canvas):
        raise CompositionError(f"{CLIP_CANVAS_VAR} needs two positive, even sides")
    return FfmpegClipRenderer(canvas=canvas)


def build_clip_settings(environ: Mapping[str, str]) -> ClipSettings:
    """Read the department's production parameters, with the defaults documented above."""
    raw_containers = environ.get(CONTAINERS_VAR, "").strip() or "mp4"
    containers = tuple(name.strip() for name in raw_containers.split(",") if name.strip())
    if not containers:
        raise CompositionError(f"{CONTAINERS_VAR} must name at least one container")
    destination = (
        environ.get(CLIP_DESTINATION_VAR, "").strip() or "clips/{episode_ref}-{index:02d}.mp4"
    )
    return ClipSettings(
        chunk_ms=_clip_number(environ, CHUNK_MS_VAR),
        max_ms=_clip_number(environ, MAX_MS_VAR),
        pause_tolerance_ms=_clip_number(environ, PAUSE_TOLERANCE_MS_VAR),
        limits=ClipFormatLimits(
            max_duration_ms=_clip_number(environ, MAX_DURATION_MS_VAR), containers=containers
        ),
        destination_template=destination,
    )


def build_clip_production(
    environ: Mapping[str, str],
    *,
    evaluator: ContextualArtifactEvaluator,
    desk: ReviewDesk | None = None,
    post_writer: PostWriting | None = None,
    posting: Posting | None = None,
) -> ClipProduction:
    """Assemble the clipping department's production path (ADR-0059).

    Every outside system enters here and nowhere else: the board, the episode file, the footage
    index, the renderer, the store and — optionally — the review desk. The evaluator is passed in
    rather than built, because choosing a provider and pricing for a role is `client_for_role`'s
    job and the caller already holds that decision (`ADR-0016`).
    """
    return ClipProduction(
        build_run_store(environ),
        build_episode_board(environ),
        build_episode_source(environ),
        build_footage_index(environ),
        build_clip_renderer(environ),
        evaluator,
        settings=build_clip_settings(environ),
        desk=desk,
        post_writer=post_writer,
        posting=posting,
    )


POSTS_PER_RUN_VAR = "OMEMO_UPLOAD_POST_MAX_NEW_PER_RUN"
"""How many approved clips start posting per invocation (unset = all) — pacing a schedule."""


def build_posting(environ: Mapping[str, str]) -> Posting:
    """Build auto-posting through upload-post (ADR-0073); missing settings fail closed, named."""
    settings = upload_post_settings_from_env(environ)
    raw = environ.get(POSTS_PER_RUN_VAR, "").strip()
    try:
        limit = int(raw) if raw else None
    except ValueError:
        raise CompositionError(f"{POSTS_PER_RUN_VAR} must be a whole number") from None
    if limit is not None and limit < 1:
        raise CompositionError(f"{POSTS_PER_RUN_VAR} must be at least 1")
    return Posting(
        publisher=UploadPostPublisher(settings),
        platforms=settings.platforms,
        max_new_per_invocation=limit,
    )


def build_post_writing(client: LLMClient, *, prompts: PromptCatalogueInput = None) -> PostWriting:
    """Compile ``clip_post_writer@v1`` into its executor and Schema binding (ADR-0072 §1).

    The same catalogue lookups as every producer (``build_executor_map`` / ``build_schema_map``);
    the client comes from ``client_for_role`` in the caller, which owns the provider decision.
    """
    resolved = _resolve_prompts(prompts)
    agents = clip_post_writer.AGENTS
    executors = build_executor_map(agents, resolved, client, clip_post_writer.SCHEMAS)
    bindings = build_schema_map(agents, resolved, clip_post_writer.SCHEMAS)
    return PostWriting(
        executor=executors[clip_post_writer.AGENT_REF],
        schema_binding=bindings[clip_post_writer.AGENT_REF],
    )


def build_production_service(
    environ: Mapping[str, str], production: BriefProduction
) -> ProductionService:
    """Build the HTTP service n8n calls over ``production`` (ROADMAP Stage 11, ADR-0049).

    Reads ``OMEMO_SERVICE_TOKEN`` / ``_HOST`` / ``_PORT``; a missing or short token fails closed
    with ``ServiceConfigurationError`` before anything binds. Not started.
    """
    return ProductionService(
        service_settings_from_env(environ),
        produce=production.invoke,
        waiting=production.waiting_briefs,
    )


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
