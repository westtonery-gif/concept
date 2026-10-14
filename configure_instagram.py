"""Connect an Instagram professional account through its Facebook Page; never posts anything.

Takes a short-lived user token from the Graph API Explorer (typed hidden, never echoed),
exchanges it for a long-lived one and stores the token of the Page whose Instagram username
matches --handle. See SOCIAL_SETUP.md.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import urllib.parse
from pathlib import Path

from omemo_content_factory.adapters.clip_publisher import ClipPublisherError
from omemo_content_factory.infrastructure._social import profile_path, request_json, write_receipt
from omemo_content_factory.infrastructure.dotenv_file import load_project_env, replace_block
from omemo_content_factory.infrastructure.instagram_publisher import (
    DEFAULT_GRAPH_URL,
    DEFAULT_GRAPH_VERSION,
)

ROOT = Path(__file__).resolve().parent


def _graph(path: str, query: dict[str, str], *, version: str) -> dict[str, object]:
    url = f"{DEFAULT_GRAPH_URL}/{version}/{path}?" + urllib.parse.urlencode(query)
    return request_json("GET", url, vendor="Instagram")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-id", required=True, help="ID приложения Meta")
    parser.add_argument("--handle", required=True, help="имя аккаунта Instagram, например @name")
    parser.add_argument("--profile", help="отдельный профиль аккаунта")
    parser.add_argument("--graph-version", default=DEFAULT_GRAPH_VERSION)
    args = parser.parse_args(argv)
    handle = args.handle.lstrip("@").casefold()
    try:
        env_path = profile_path(ROOT, "instagram", args.profile) if args.profile else ROOT / ".env"
        secret = os.environ.get("OMEMO_META_APP_SECRET") or getpass.getpass(
            "App secret приложения Meta (ввод скрыт): "
        )
        short = getpass.getpass("Токен пользователя из Graph API Explorer (ввод скрыт): ")
        if not secret.strip() or not short.strip():
            print("Нужны App secret и токен. Подключение не сохранено.")
            return 1
        long_lived = _graph(
            "oauth/access_token",
            {
                "grant_type": "fb_exchange_token",
                "client_id": args.app_id,
                "client_secret": secret.strip(),
                "fb_exchange_token": short.strip(),
            },
            version=args.graph_version,
        ).get("access_token")
        if not isinstance(long_lived, str):
            print("Meta не выдала долгоживущий токен. Подключение не сохранено.")
            return 1
        pages = _graph(
            "me/accounts",
            {
                "fields": "name,access_token,instagram_business_account{id,username}",
                "access_token": long_lived,
            },
            version=args.graph_version,
        ).get("data")
        found = []
        for page in pages if isinstance(pages, list) else []:
            account = page.get("instagram_business_account") or {}
            name = str(account.get("username", ""))
            print(f"Страница {page.get('name', '?')}: Instagram @{name or '—'}")
            if name.casefold() == handle:
                found.append((page, account))
        if len(found) != 1:
            print(
                f"Не найден ровно один профессиональный Instagram @{handle}, привязанный к "
                "странице. Проверьте привязку и разрешения токена. Подключение не сохранено."
            )
            return 1
        page, account = found[0]
        suffix = f"-{args.profile}" if args.profile else ""
        token = ROOT / ".omemo" / f"instagram{suffix}-token.json"
        write_receipt(token, {"access_token": page["access_token"], "page_id": page.get("id")})
        replace_block(
            env_path,
            "instagram-direct",
            [
                f'OMEMO_INSTAGRAM_TOKEN_PATH="{token}"',
                f"OMEMO_INSTAGRAM_USER_ID={account['id']}",
                f"OMEMO_INSTAGRAM_HANDLE=@{account['username']}",
                f"OMEMO_INSTAGRAM_GRAPH_VERSION={args.graph_version}",
                f'OMEMO_INSTAGRAM_RECEIPTS="{ROOT / ".omemo" / f"instagram{suffix}-uploads"}"',
            ],
        )
        print(f"Подключён Instagram @{account['username']}. Ничего не опубликовано.")
        return 0
    except (ClipPublisherError, ValueError, KeyError) as error:
        print(f"Подключение не завершено: {error}")
        return 1


if __name__ == "__main__":
    load_project_env(__file__)
    sys.exit(main(sys.argv[1:]))
