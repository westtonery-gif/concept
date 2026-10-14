"""Connect a YouTube channel through Desktop OAuth; never uploads videos."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from omemo_content_factory.infrastructure.dotenv_file import replace_block
from omemo_content_factory.infrastructure.youtube_publisher import (
    SCOPES,
    write_private_json,
    youtube_profile_path,
)

ROOT = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_json", type=Path, help="JSON OAuth client типа Desktop app")
    parser.add_argument("--handle", default="@tsxmoon-n7s")
    parser.add_argument("--profile", help="отдельный профиль канала, например whtprod")
    args = parser.parse_args(argv)
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    try:
        env_path = youtube_profile_path(ROOT, args.profile) if args.profile else ROOT / ".env"
        flow = InstalledAppFlow.from_client_secrets_file(
            str(args.client_json), scopes=SCOPES, autogenerate_code_verifier=True
        )
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            open_browser=True,
            timeout_seconds=300,
            authorization_prompt_message="Разрешите доступ к YouTube в открывшемся браузере.",
            success_message="Авторизация получена. Можно закрыть вкладку.",
            prompt="consent select_account",
            access_type="offline",
        )
        service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        mine = service.channels().list(part="id,snippet", mine=True).execute().get("items", [])
        target = (
            service.channels().list(part="id", forHandle=args.handle).execute().get("items", [])
        )
        if len(mine) != 1 or len(target) != 1 or mine[0]["id"] != target[0]["id"]:
            print(f"Не удалось подтвердить канал {args.handle}. Подключение не сохранено.")
            for channel in mine:
                snippet = channel.get("snippet", {})
                print(
                    "Google предоставил канал: "
                    f"{snippet.get('title', '')} {snippet.get('customUrl', '')} "
                    f"(ID: {channel['id']})"
                )
            if not mine:
                print("Google не вернул ни одного канала для выбранного аккаунта.")
            if not target:
                print(f"Канал по имени {args.handle} не найден. Проверьте ссылку на канал.")
            return 1
        if not credentials.refresh_token:
            print("Google не выдал refresh token. Повторите авторизацию с согласием.")
            return 1
        token_name = f"youtube-{args.profile}-token.json" if args.profile else "youtube-token.json"
        token = ROOT / ".omemo" / token_name
        write_private_json(token, json.loads(credentials.to_json()))
        replace_block(
            env_path,
            "youtube-direct",
            [
                "OMEMO_POSTING_PROVIDER=youtube",
                f'OMEMO_YOUTUBE_TOKEN_PATH="{token}"',
                f"OMEMO_YOUTUBE_CHANNEL_ID={mine[0]['id']}",
                f"OMEMO_YOUTUBE_HANDLE={args.handle}",
                "OMEMO_YOUTUBE_PRIVACY=private",
                f'OMEMO_YOUTUBE_RECEIPTS="{ROOT / ".omemo/youtube-uploads"}"',
            ],
        )
        print(f"Подключён {args.handle}: {mine[0]['snippet']['title']}. Ничего не загружено.")
        return 0
    except Exception:
        print(
            "Подключение не завершено. Проверьте Desktop OAuth JSON, доступ к API и выбор канала."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
