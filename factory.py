"""Local video → moments → captions → QA → TXT → human-approved YouTube Shorts.

Run in a terminal: python factory.py /path/episode.mp4 --profile my-channel
The first run asks for missing local settings. Never asks for a Google password.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import configure_llm
import configure_youtube
import post_youtube
from demo_clips import _build_evaluator, _build_moment_picker, _build_post_writer, _report

from omemo_content_factory.adapters.clip_publisher import PublishState
from omemo_content_factory.adapters.episode_board import ClipMode, IncomingEpisode
from omemo_content_factory.application.clip_review import latest_post
from omemo_content_factory.application.review_publication import latest_qa
from omemo_content_factory.composition import build_clip_production, build_run_store
from omemo_content_factory.domain.evaluation import EvaluationStatus
from omemo_content_factory.domain.run import Run
from omemo_content_factory.infrastructure.dotenv_file import load_dotenv, replace_block
from omemo_content_factory.infrastructure.local_episode_board import LocalEpisodeBoard
from omemo_content_factory.infrastructure.youtube_publisher import (
    YouTubePublisher,
    YouTubeSettings,
    youtube_profile_path,
    youtube_settings_from_env,
)

ROOT = Path(__file__).resolve().parent


def preflight() -> None:
    missing = [
        name
        for name in ("ffmpeg", "ffprobe", "whisper-cli", "fpcalc")
        if shutil.which(name) is None
    ]
    if missing:
        raise ValueError("Установите инструменты и добавьте в PATH: " + ", ".join(missing))
    filters = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, check=True
    ).stdout
    if "subtitles" not in filters:
        raise ValueError("Нужен FFmpeg с фильтром subtitles (libass); см. LOCAL_FACTORY.md")


def setup_model(model: Path | None) -> None:
    if model is not None or not os.environ.get("OMEMO_WHISPER_MODEL"):
        model = model or Path(input("Путь к скачанной модели Whisper (.bin): ").strip())
        model = model.expanduser().resolve(strict=True)
        if not model.is_file() or any(c in str(model) for c in '\n\r"'):
            raise ValueError("Укажите обычный файл модели Whisper")
        replace_block(ROOT / ".env", "local-whisper", [f'OMEMO_WHISPER_MODEL="{model}"'])
        os.environ["OMEMO_WHISPER_MODEL"] = str(model)
    if not Path(os.environ["OMEMO_WHISPER_MODEL"]).is_file():
        raise ValueError("Модель Whisper не найдена; задайте --whisper-model /path/model.bin")
    if not os.environ.get("OMEMO_PROVIDER__DEFAULT", "").strip():
        if configure_llm.main(["--env-file", str(ROOT / ".env")]):
            raise ValueError("Настройка GPT/Claude не завершена")
        load_dotenv(ROOT / ".env")


def setup_channel(args: argparse.Namespace) -> YouTubeSettings:
    path = youtube_profile_path(ROOT, args.profile)
    if args.client_json or not path.is_file():
        client = args.client_json or Path(input("Путь к Desktop OAuth JSON: ").strip())
        handle = args.channel or input("Канал YouTube (@имя): ").strip()
        if not handle.startswith("@"):
            raise ValueError("Укажите канал в формате @имя")
        if configure_youtube.main(
            [
                str(client.expanduser().resolve(strict=True)),
                "--handle",
                handle,
                "--profile",
                args.profile,
            ]
        ):
            raise ValueError("Канал не подключён")
    env: dict[str, str] = {}
    load_dotenv(path, env)
    if args.channel and env.get("OMEMO_YOUTUBE_HANDLE") != args.channel:
        raise ValueError("Профиль относится к другому каналу; подключите его с --client-json")
    settings = youtube_settings_from_env(env)
    if not settings.token_path.is_file():
        raise ValueError("Токен отсутствует; повторите подключение с --client-json")
    return replace(settings, privacy="public")


def local_environment(video: Path, args: argparse.Namespace) -> tuple[dict[str, str], str, Path]:
    env = dict(os.environ)
    env.update(
        {
            "OMEMO_EPISODE_ROOT": str(video.parent),
            "OMEMO_CLIP_MOMENT_COUNT": str(args.count),
            "OMEMO_CLIP_MOMENT_MIN_MS": "25000",
            "OMEMO_CLIP_MAX_MS": "60000",
        }
    )
    for key, value in {
        "OMEMO_CLIP_CANVAS": "720x1280",
        "OMEMO_CLIP_FILL": "blur",
        "OMEMO_CLIP_PICTURE_HEIGHT": "0.32",
        "OMEMO_CLIP_CAPTION_COLOR": "yellow",
        "OMEMO_WHISPER_LANGUAGE": "ru",
        "OMEMO_VIDEO_PRESET": "fast",
    }.items():
        env.setdefault(key, value)
    with video.open("rb") as stream:
        source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    # New source or changed production settings must not resume an unrelated cached Run.
    config = {
        k: v
        for k, v in env.items()
        if k.startswith(
            (
                "OMEMO_CLIP_",
                "OMEMO_VIDEO_",
                "OMEMO_WHISPER_",
                "OMEMO_MODEL__",
                "OMEMO_PROVIDER__",
            )
        )
    }
    output_root = (args.output or video.parent / "outputs").expanduser().resolve()
    identity = json.dumps([str(video), source_hash, str(output_root), config], sort_keys=True)
    episode_ref = "local-" + hashlib.sha256(identity.encode()).hexdigest()[:20]
    output = output_root / f"{video.stem}-{episode_ref[6:]}"
    # Escape literal braces: the existing destination is a format template.
    destination = str(output).replace("{", "{{").replace("}", "}}")
    env["OMEMO_CLIP_DESTINATION"] = destination + "/{index:02d}.mp4"
    env["OMEMO_RUN_STORE_PATH"] = str(ROOT / ".omemo" / "local-factory.sqlite3")
    return env, episode_ref, output


def ready_clips(run: Run) -> list[Path]:
    clips = []
    for artifact in run.artifacts:
        qa = latest_qa(run, artifact.artifact_id)
        if (
            qa is not None
            and qa.status is EvaluationStatus.PASSED
            and latest_post(run, artifact.artifact_id) is not None
        ):
            path = Path(json.loads(artifact.content)["path"])
            if path.is_file() and path.with_suffix(".txt").is_file():
                clips.append(path)
    return clips


def review_and_post(clips: list[Path], profile: str, settings: YouTubeSettings) -> int:
    for clip in clips:
        request, _, _ = post_youtube.prepare(clip, settings)
        previous = YouTubePublisher(settings).status(request.request_id)
        if previous.state is PublishState.COMPLETED:
            print(f"Уже загружено: {previous.results[0].url}")
            continue
        print(f"\nПосмотрите видео: {clip}\nТекст можно изменить здесь: {clip.with_suffix('.txt')}")
        audience = input("Ролик предназначен для детей? yes / no; Enter — пропустить: ").strip()
        if audience not in {"yes", "no"}:
            continue
        selected = replace(settings, made_for_kids=audience == "yes")
        _, fingerprint, preview = post_youtube.prepare(clip, selected)
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        answer = input("Опубликовать именно этот ролик с этим текстом? Введите publish: ").strip()
        if answer != "publish":
            print("Пропущено, файл сохранён.")
            continue
        # The existing CLI rechecks the fingerprint after human input and verifies the channel.
        code = post_youtube.main(
            [
                str(clip),
                "--profile",
                profile,
                "--privacy",
                "public",
                "--made-for-kids",
                audience,
                "--approve",
                fingerprint,
            ]
        )
        if code:
            return code
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--profile", default="local", help="сохранённый профиль YouTube")
    parser.add_argument("--client-json", type=Path, help="Desktop OAuth JSON для подключения")
    parser.add_argument("--channel", help="YouTube @имя")
    parser.add_argument("--whisper-model", type=Path)
    parser.add_argument(
        "--output", type=Path, help="папка результатов; по умолчанию videos/outputs"
    )
    parser.add_argument("--count", type=int, default=3, help="максимальное число моментов")
    parser.add_argument(
        "--draft-only", action="store_true", help="подготовить без подключения канала"
    )
    args = parser.parse_args(argv)
    try:
        load_dotenv(ROOT / ".env")
        video = args.video.expanduser().resolve(strict=True)
        if not video.is_file() or args.count < 1:
            raise ValueError("Нужен файл видео и --count >= 1")
        youtube_profile_path(ROOT, args.profile)  # Reject invalid names before any setup.
        preflight()
        setup_model(args.whisper_model)
        settings = None if args.draft_only else setup_channel(args)
        env, episode_ref, output = local_environment(video, args)
        board = LocalEpisodeBoard(IncomingEpisode(episode_ref, video.name, ClipMode.MOMENTS))
        production = build_clip_production(
            env,
            board=board,
            evaluator=_build_evaluator(),
            post_writer=_build_post_writer(env),
            moment_picker=_build_moment_picker(env),
        )
        print(
            "Расшифровка → выбор моментов → монтаж → QA → тексты. LLM-запросы платные.", flush=True
        )
        result = production.invoke(episode_ref)
        _report(result)
        run = build_run_store(env).load(result.run_id) if result.run_id else None
        clips = ready_clips(run) if run is not None else []
        print(f"Результаты: {output}\nГотово к просмотру: {len(clips)}", flush=True)
        if not clips:
            print("Нет клипов, прошедших QA и получивших TXT. Публикация не запускалась.")
            return 1
        if settings is None:
            return 0
        return review_and_post(clips, args.profile, settings)
    except (EOFError, KeyboardInterrupt):
        print("Остановлено. Сохранённые результаты останутся для повторного запуска.")
        return 2
    except Exception as error:
        # SDK failures can contain secrets; display only a type and safe local guidance.
        if isinstance(error, (ValueError, FileNotFoundError)):
            print(f"Ошибка: {error}", file=sys.stderr)
        else:
            print(
                f"Ошибка {type(error).__name__}. Проверьте настройки и доступ к сервисам.",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
