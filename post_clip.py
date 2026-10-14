"""Preview a local clip and its TXT for Instagram or TikTok; post only with the approval print.

Like post_youtube.py: without --approve nothing leaves the machine. See SOCIAL_SETUP.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from omemo_content_factory.adapters.clip_publisher import (
    ClipPublisher,
    ClipPublisherError,
    PublishRequest,
)
from omemo_content_factory.infrastructure._social import caption, profile_path
from omemo_content_factory.infrastructure.clip_text_file import read_clip_text
from omemo_content_factory.infrastructure.dotenv_file import load_dotenv, load_project_env
from omemo_content_factory.infrastructure.instagram_publisher import (
    InstagramPublisher,
    instagram_settings_from_env,
)
from omemo_content_factory.infrastructure.tiktok_publisher import (
    TikTokPublisher,
    tiktok_settings_from_env,
)

__all__ = ["PLATFORMS", "environment", "main", "prepare", "profile_path", "publisher_for"]

ROOT = Path(__file__).resolve().parent
PLATFORMS = ("instagram", "tiktok")


def publisher_for(platform: str, environ: Mapping[str, str]) -> ClipPublisher:
    if platform == "instagram":
        return InstagramPublisher(instagram_settings_from_env(environ))
    return TikTokPublisher(tiktok_settings_from_env(environ))


def prepare(
    video: Path, platform: str, environ: Mapping[str, str]
) -> tuple[PublishRequest, str, dict[str, object]]:
    video = video.resolve(strict=True)
    with video.open("rb") as stream:
        file_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    post = read_clip_text(video)
    preview: dict[str, object] = {"video": str(video), "sha256": file_hash, "platform": platform}
    if platform == "instagram":
        ig = instagram_settings_from_env(environ)
        account = ig.user_id
        preview.update(account=ig.handle, account_id=account)
    else:
        tt = tiktok_settings_from_env(environ)
        account = tt.open_id
        preview.update(account=tt.handle, account_id=account, mode=tt.mode)
        if tt.mode == "direct":
            preview["privacy"] = tt.privacy
    request = PublishRequest(
        # A changed caption must not post the same video twice.
        f"{platform}-{account}-{file_hash}",
        str(video),
        post,
        (platform,),
    )
    preview["caption"] = caption(request)
    approval = hashlib.sha256(json.dumps(preview, sort_keys=True).encode()).hexdigest()
    return request, approval, preview


def environment(platform: str, profile: str | None) -> dict[str, str]:
    env = dict(os.environ)
    if profile:
        path = profile_path(ROOT, platform, profile)
        if not path.is_file():
            raise ValueError(f"Профиль {profile} для {platform} ещё не подключён")
        load_dotenv(path, env)
    return env


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--to", choices=PLATFORMS, required=True)
    parser.add_argument("--profile", help="отдельный профиль аккаунта")
    parser.add_argument("--approve", help="отпечаток предпросмотра; только после апрува")
    args = parser.parse_args(argv)
    try:
        env = environment(args.to, args.profile)
        request, approval, preview = prepare(args.video, args.to, env)
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        print(f"Отпечаток для апрува: {approval}")
        if args.approve is None:
            print("Предпросмотр. Ничего не отправлено. Дождитесь одобрения пользователя.")
            return 0
        if args.approve != approval:
            raise ValueError("Клип, текст, аккаунт или настройки изменились. Нужен новый апрув")
        publisher = publisher_for(args.to, env)
        publisher.submit(request)
        status = publisher.status(request.request_id)
        for result in status.results:
            print(f"{result.platform}: {status.state.value} {result.url or result.message or ''}")
        if args.to == "tiktok" and preview.get("mode") == "inbox":
            print("Откройте TikTok → Входящие, вставьте подпись и опубликуйте:")
            print(preview["caption"])
        return 0
    except (OSError, ValueError, ClipPublisherError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    load_project_env(__file__)
    sys.exit(main(sys.argv[1:]))
