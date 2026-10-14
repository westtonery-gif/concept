"""Preview a local clip and its TXT; upload only with an explicit approval fingerprint."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from omemo_content_factory.adapters.clip_publisher import ClipPublisherError, PublishRequest
from omemo_content_factory.adapters.review_desk import PostDraft
from omemo_content_factory.infrastructure.dotenv_file import load_dotenv, load_project_env
from omemo_content_factory.infrastructure.youtube_publisher import (
    YouTubePublisher,
    YouTubeSettings,
    youtube_profile_path,
    youtube_settings_from_env,
)


def prepare(
    video: Path, settings: YouTubeSettings
) -> tuple[PublishRequest, str, dict[str, object]]:
    video = video.resolve(strict=True)
    with video.open("rb") as stream:
        file_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    text = video.with_suffix(".txt").read_text(encoding="utf-8")
    _, title_marker, rest = text.partition("Название:\n")
    title, description_marker, description = rest.partition("\nОписание:\n")
    if not title_marker or not description_marker or len(description.strip()) > 5000:
        raise ValueError("TXT должен содержать Название: и Описание: (до 5000 символов)")
    post = PostDraft(title=title.strip(), description=description.strip())
    preview: dict[str, object] = {
        "video": str(video),
        "sha256": file_hash,
        "channel_id": settings.channel_id,
        "title": post.title,
        "description": post.description,
        "privacy": settings.privacy,
        "made_for_kids": settings.made_for_kids,
    }
    approval = hashlib.sha256(json.dumps(preview, sort_keys=True).encode()).hexdigest()
    # Changing a caption or visibility must not cause a second upload of the same video.
    request_id = f"youtube-{settings.channel_id}-{file_hash}"
    return PublishRequest(request_id, str(video), post, ("youtube",)), approval, preview


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--profile", help="отдельный профиль канала, например whtprod")
    parser.add_argument("--privacy", choices=["private", "unlisted", "public"])
    parser.add_argument("--made-for-kids", choices=["yes", "no"], default="no")
    parser.add_argument(
        "--approve", help="отпечаток предпросмотра; только после апрува пользователя"
    )
    args = parser.parse_args(argv)
    try:
        if args.profile:
            profile_path = youtube_profile_path(Path(__file__).resolve().parent, args.profile)
            if not profile_path.is_file():
                raise ValueError(f"Профиль {args.profile} ещё не подключён")
            profile_env: dict[str, str] = {}
            load_dotenv(profile_path, profile_env)
            settings = youtube_settings_from_env(profile_env)
        else:
            settings = youtube_settings_from_env(os.environ)
        if args.privacy:
            settings = replace(settings, privacy=args.privacy)
        settings = replace(settings, made_for_kids=args.made_for_kids == "yes")
        request, approval, preview = prepare(args.video, settings)
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        print(f"Отпечаток для апрува: {approval}")
        if args.approve is None:
            print("Предпросмотр. Ничего не отправлено. Дождитесь одобрения пользователя.")
            return 0
        if args.approve != approval:
            raise ValueError("Клип, текст, канал или настройки изменились. Нужен новый апрув")
        publisher = YouTubePublisher(settings)
        publisher.submit(request)
        status = publisher.status(request.request_id)
        for result in status.results:
            print(f"YouTube принял загрузку: {result.url}")
        print("Доступность и обработку видео можно проверить в YouTube Studio.")
        return 0
    except (OSError, ValueError, ClipPublisherError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    load_project_env(__file__)
    sys.exit(main(sys.argv[1:]))
