"""Tests for the moment picker and the MOMENTS mode (ADR-0097).

Maps CLIPPING_ACCEPTANCE.md §10 (MPK): the pure decoder and plan, the role's consistency, and the
production path with a scripted picker — recorded once, repaired once, failing loudly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omemo_content_factory.adapters.episode_board import ClipMode, IncomingEpisode
from omemo_content_factory.adapters.footage_index import IndexedFootage, SkipZone, SpeechSpan
from omemo_content_factory.agents import clip_moment_picker
from omemo_content_factory.application.clip_format import ClipFormatLimits
from omemo_content_factory.application.clip_moments import (
    KINDS,
    MomentLimits,
    MomentsError,
    decode_moments,
    moments_task_input,
    plan_from_moments,
)
from omemo_content_factory.application.clip_plan import plan_clips
from omemo_content_factory.application.clip_production import (
    MOMENT_PLAN_FAILED_REASON,
    PICK_STEP_REF,
    ClipProduction,
    ClipProductionError,
    ClipSettings,
    MomentPicking,
)
from omemo_content_factory.application.qa_evaluation import EvaluationResult
from omemo_content_factory.application.schema_validation import SchemaBinding
from omemo_content_factory.application.task_execution import ExecutionResult
from omemo_content_factory.composition import load_prompt_catalogue
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.run import RunStatus
from omemo_content_factory.domain.task import TaskStatus
from omemo_content_factory.infrastructure.in_memory_adapters import InMemoryEpisodeBoard
from omemo_content_factory.infrastructure.in_memory_clipping import (
    InMemoryClipRenderer,
    InMemoryEpisodeSource,
    InMemoryFootageIndex,
    InMemoryVideoProcessor,
)
from omemo_content_factory.infrastructure.sqlite_run_store import SqliteRunStore

SEC = 1_000
LIMITS = MomentLimits(min_ms=60 * SEC, max_ms=120 * SEC, count=3)
# Twelve contiguous 30-second lines: line n covers [(n-1)*30 s, n*30 s - 1 s].
SPEECH = tuple(
    SpeechSpan(start_ms=(n - 1) * 30 * SEC, end_ms=n * 30 * SEC - SEC, text=f"line {n}")
    for n in range(1, 13)
)
FOOTAGE = IndexedFootage(duration_ms=360 * SEC, scenes=(), speech=SPEECH)


def _faults(answer: str, footage: IndexedFootage = FOOTAGE) -> tuple[str, ...]:
    with pytest.raises(MomentsError) as caught:
        decode_moments(answer, footage, LIMITS)
    return caught.value.faults


# --- MPK-01: the decoder --------------------------------------------------------------


def test_mpk_01_a_good_answer_names_moments_by_line_and_cuts_at_line_edges() -> None:
    moments = decode_moments(
        "4-6 | laugh | punchline\n1-3 | Quote | a famous line", FOOTAGE, LIMITS
    )
    assert [(m.first_line, m.last_line) for m in moments] == [(4, 6), (1, 3)]
    assert (moments[0].start_ms, moments[0].end_ms) == (90 * SEC, 179 * SEC)
    assert moments[1].kind == "quote"


def test_mpk_01_every_fault_is_reported_together_and_nothing_is_repaired() -> None:
    faults = _faults("1-1 | laugh | too short\n2-3 | joy | bad kind\nnonsense\n4-5 | tension |")
    text = "\n".join(faults)
    assert "moment 1" in text and "lasts 29 s" in text
    assert "moment 2" in text and "kind 'joy'" in text
    assert "moment 3" in text
    assert "moment 4" in text and "reason is blank" in text


def test_mpk_01_an_empty_answer_is_a_fault() -> None:
    assert _faults("  \n") == ("the answer names no moments",)


def test_mpk_01_overlapping_moments_are_refused() -> None:
    assert any("overlap" in fault for fault in _faults("1-3 | laugh | a\n3-5 | laugh | b"))


def test_mpk_01_too_many_moments_are_refused() -> None:
    answer = "\n".join(f"{n}-{n + 1} | laugh | x" for n in (1, 3, 5, 7))
    assert any("at most 3" in fault for fault in _faults(answer))


def test_mpk_01_a_line_that_is_not_listed_is_refused() -> None:
    assert any("line 13 is not a listed line" in fault for fault in _faults("11-13 | laugh | x"))


def test_mpk_01_a_moment_never_runs_through_the_titles() -> None:
    footage = IndexedFootage(
        duration_ms=360 * SEC,
        scenes=(),
        speech=SPEECH,
        skips=(SkipZone(start_ms=100 * SEC, end_ms=130 * SEC),),
    )
    # lines 4 and 5 sit inside the zone, so they are not listed; 3-6 spans it
    assert "line 4" not in "\n".join(
        json.loads(moments_task_input(footage, LIMITS, episode_file="e"))["transcript"]
    )
    faults = _faults("3-6 | laugh | x", footage)
    assert any("not a listed line" in f or "titles" in f for f in faults)


def test_mpk_01_the_vocabulary_is_closed() -> None:
    assert KINDS == ("laugh", "surprise", "tension", "emotion", "quote")


# --- MPK-02: the plan -----------------------------------------------------------------


def test_mpk_02_the_plan_is_in_episode_order_with_captions_and_transcript() -> None:
    moments = decode_moments("7-9 | laugh | b\n1-3 | quote | a", FOOTAGE, LIMITS)
    plan = plan_from_moments(FOOTAGE, moments, episode_ref="ep")
    assert plan.mode is ClipMode.MOMENTS
    assert [clip.index for clip in plan.clips] == [1, 2]
    assert [clip.start_ms for clip in plan.clips] == [0, 180 * SEC]
    assert plan.clips[0].transcript == "line 1 line 2 line 3"
    assert plan.clips[0].captions[0].start_ms == 0


def test_mpk_02_plan_clips_refuses_moments_mode() -> None:
    with pytest.raises(ValueError, match="MOMENTS"):
        plan_clips(
            FOOTAGE,
            episode_ref="ep",
            mode=ClipMode.MOMENTS,
            chunk_ms=1,
            max_ms=1,
            pause_tolerance_ms=0,
        )


def test_mpk_02_the_input_numbers_lines_and_carries_the_limits_and_faults() -> None:
    payload = json.loads(moments_task_input(FOOTAGE, LIMITS, episode_file="s01e01.mp4"))
    assert payload["transcript"][0] == "1 | 0.0 | 29.0 | line 1"
    assert (payload["min_clip_s"], payload["max_clip_s"], payload["moments_wanted"]) == (60, 120, 3)
    assert "previous_answer_faults" not in payload
    repair = json.loads(moments_task_input(FOOTAGE, LIMITS, episode_file="x", faults=("bad",)))
    assert repair["previous_answer_faults"] == ["bad"]


# --- MPK-03: the role -----------------------------------------------------------------


def test_mpk_03_the_prompt_schema_and_agent_agree() -> None:
    prompt = load_prompt_catalogue()[clip_moment_picker.PROMPT_REF]
    assert prompt.schema_ref == clip_moment_picker.SCHEMA_REF
    assert clip_moment_picker.CLIP_MOMENTS_SCHEMA.view.required_fields == ("moments",)
    assert clip_moment_picker.CLIP_MOMENT_PICKER_AGENT.prompt_ref == prompt.prompt_id
    for kind in KINDS:  # the Prompt teaches exactly the decoder's vocabulary
        assert kind in prompt.system


# --- MPK-04..: the production path ----------------------------------------------------

EPISODE = "episode-1"
SOURCE = "s01e01.mp4"
PATH = "/media/s01e01.mp4"
SETTINGS = ClipSettings(
    chunk_ms=120 * SEC,
    max_ms=120 * SEC,
    pause_tolerance_ms=2_000,
    limits=ClipFormatLimits(max_duration_ms=180 * SEC, containers=("mp4",)),
)


class _Picker:
    """A scripted picker executor: answers in order, repeats the last; counts its calls."""

    def __init__(self, *answers: str, fails: bool = False) -> None:
        self._answers = answers
        self.inputs: list[str] = []
        self.fails = fails

    def execute(self, task_input: str) -> ExecutionResult:
        self.inputs.append(task_input)
        if self.fails:
            return ExecutionResult(succeeded=False, failure_reason="LLM execution failed: down")
        answer = self._answers[min(len(self.inputs), len(self._answers)) - 1]
        fields = {"moments": answer}
        return ExecutionResult(
            succeeded=True,
            output=json.dumps(fields, ensure_ascii=False, sort_keys=True),
            schema_ref=clip_moment_picker.SCHEMA_REF,
            payload_fields=fields,
        )


class _Pass:
    evaluator_ref = "clip_qa_agent@v1"

    def evaluate(self, content: str) -> EvaluationResult:
        raise AssertionError("always in context")

    def evaluate_in_context(self, content: str, context: str) -> EvaluationResult:
        return EvaluationResult(verdict=EvaluationStatus.PASSED, flags=())


def _production(
    tmp_path: Path, picker: _Picker | None, *, mode: ClipMode = ClipMode.MOMENTS
) -> tuple[ClipProduction, InMemoryClipRenderer]:
    board = InMemoryEpisodeBoard()
    board.put(IncomingEpisode(episode_ref=EPISODE, source_ref=SOURCE, mode=mode))
    source = InMemoryEpisodeSource()
    source.put(SOURCE, PATH)
    index = InMemoryFootageIndex()
    index.put(PATH, FOOTAGE)
    renderer = InMemoryClipRenderer()
    picking = (
        None
        if picker is None
        else MomentPicking(
            executor=picker,
            schema_binding=SchemaBinding(
                schema_ref=clip_moment_picker.SCHEMA_REF,
                schema=clip_moment_picker.CLIP_MOMENTS_SCHEMA,
            ),
            limits=LIMITS,
        )
    )
    production = ClipProduction(
        SqliteRunStore(tmp_path / "runs.sqlite3"),
        board,
        source,
        index,
        renderer,
        _Pass(),
        settings=SETTINGS,
        processor=InMemoryVideoProcessor(renderer),
        moment_picker=picking,
    )
    return production, renderer


def _run(tmp_path: Path):  # type: ignore[no-untyped-def]
    run = SqliteRunStore(tmp_path / "runs.sqlite3").load(f"run-episode-{EPISODE}")
    assert run is not None
    return run


def test_mpk_04_the_picked_moments_become_clips_in_episode_order(tmp_path: Path) -> None:
    picker = _Picker("7-9 | laugh | the payoff\n1-3 | quote | a famous line")
    production, renderer = _production(tmp_path, picker)
    invocation = production.invoke(EPISODE)
    assert invocation.clips == 2
    assert [(r.start_ms, r.end_ms) for r in renderer.requests] == [
        (0, 89 * SEC),
        (180 * SEC, 269 * SEC),
    ]
    tasks = [t for t in _run(tmp_path).tasks if t.workflow_step_ref == PICK_STEP_REF]
    assert len(tasks) == 1 and tasks[0].status is TaskStatus.SUCCEEDED


def test_mpk_05_a_second_invocation_never_asks_the_model_again(tmp_path: Path) -> None:
    picker = _Picker("1-3 | quote | a")
    production, _ = _production(tmp_path, picker)
    production.invoke(EPISODE)
    production, _ = _production(tmp_path, picker)
    production.invoke(EPISODE)
    assert len(picker.inputs) == 1


def test_mpk_06_a_refused_answer_gets_one_repair_round_with_its_faults(tmp_path: Path) -> None:
    picker = _Picker("1-1 | laugh | too short", "1-3 | quote | fine")
    production, _ = _production(tmp_path, picker)
    assert production.invoke(EPISODE).clips == 1
    assert len(picker.inputs) == 2
    assert "lasts 29 s" in json.loads(picker.inputs[1])["previous_answer_faults"][0]


def test_mpk_07_two_refused_answers_fail_the_run_and_cut_nothing(tmp_path: Path) -> None:
    picker = _Picker("1-1 | laugh | too short")
    production, renderer = _production(tmp_path, picker)
    invocation = production.invoke(EPISODE)
    assert invocation.status is RunStatus.FAILED
    assert invocation.failure_reason == MOMENT_PLAN_FAILED_REASON
    assert renderer.requests == [] and len(picker.inputs) == 2


def test_mpk_07_a_model_outage_fails_the_run_with_the_same_reason(tmp_path: Path) -> None:
    production, renderer = _production(tmp_path, _Picker(fails=True))
    invocation = production.invoke(EPISODE)
    assert invocation.failure_reason == MOMENT_PLAN_FAILED_REASON
    assert renderer.requests == []


def test_mpk_08_moments_mode_without_a_picker_is_a_configuration_error(tmp_path: Path) -> None:
    production, _ = _production(tmp_path, None)
    with pytest.raises(ClipProductionError, match="moment picker"):
        production.invoke(EPISODE)


def test_mpk_09_the_other_modes_never_call_the_picker(tmp_path: Path) -> None:
    picker = _Picker("1-3 | quote | a")
    production, _ = _production(tmp_path, picker, mode=ClipMode.CHUNK)
    production.invoke(EPISODE)
    assert picker.inputs == []
