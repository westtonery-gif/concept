"""Connect a TikTok account through the desktop login (PKCE); never posts anything.

Opens TikTok in the browser and takes the answer on a local callback; the client secret is
typed hidden when it is not already in the environment. See SOCIAL_SETUP.md.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import http.server
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

from omemo_content_factory.adapters.clip_publisher import ClipPublisherError
from omemo_content_factory.infrastructure._social import profile_path, request_json, write_receipt
from omemo_content_factory.infrastructure.dotenv_file import load_project_env, replace_block
from omemo_content_factory.infrastructure.tiktok_publisher import DEFAULT_API_URL, token_request

ROOT = Path(__file__).resolve().parent
AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "user.info.basic,video.upload,video.publish"


def pkce_pair() -> tuple[str, str]:
    """A fresh verifier and TikTok's challenge for it: the **hex** SHA-256 (their docs)."""
    verifier = secrets.token_urlsafe(64)[:96]
    return verifier, hashlib.sha256(verifier.encode()).hexdigest()


def authorize_url(client_key: str, challenge: str, redirect: str, state: str) -> str:
    return (
        AUTHORIZE_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_key": client_key,
                "response_type": "code",
                "scope": SCOPES,
                "redirect_uri": redirect,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )


def wait_for_code(
    client_key: str, challenge: str, state: str, timeout: float = 300.0
) -> tuple[str, str]:
    """Open the login and serve one callback on 127.0.0.1; return ``(code, redirect_uri)``."""
    answer: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            answer.update({key: values[0] for key, values in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Готово. Можно закрыть вкладку.".encode())

        def log_message(self, *args: object) -> None:  # keep the code out of the terminal
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 5
    redirect = f"http://127.0.0.1:{server.server_port}/callback/"
    deadline = time.monotonic() + timeout
    try:
        webbrowser.open(authorize_url(client_key, challenge, redirect, state))
        print("Разрешите доступ в открывшемся браузере.")
        while not answer and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if answer.get("state") != state or "code" not in answer:
        detail = answer.get("error_description") or answer.get("error") or "нет ответа"
        raise ClipPublisherError(f"TikTok не вернул код авторизации: {detail}")
    return answer["code"], redirect


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-key", default=os.environ.get("OMEMO_TIKTOK_CLIENT_KEY", ""))
    parser.add_argument("--handle", required=True, help="имя аккаунта TikTok, например @name")
    parser.add_argument("--profile", help="отдельный профиль аккаунта")
    args = parser.parse_args(argv)
    try:
        env_path = profile_path(ROOT, "tiktok", args.profile) if args.profile else ROOT / ".env"
        client_key = args.client_key.strip()
        secret = os.environ.get("OMEMO_TIKTOK_CLIENT_SECRET") or getpass.getpass(
            "Client secret приложения TikTok (ввод скрыт): "
        )
        if not client_key or not secret.strip():
            print("Нужны client key и client secret. Подключение не сохранено.")
            return 1
        verifier, challenge = pkce_pair()
        code, redirect = wait_for_code(client_key, challenge, secrets.token_urlsafe(24))
        token = token_request(
            DEFAULT_API_URL,
            {
                "client_key": client_key,
                "client_secret": secret.strip(),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect,
                "code_verifier": verifier,
            },
            now=time.time(),
        )
        granted = set(str(token["scope"]).split(","))
        if "video.upload" not in granted:
            print("TikTok не выдал разрешение video.upload. Подключение не сохранено.")
            return 1
        user = request_json(
            "GET",
            DEFAULT_API_URL + "/v2/user/info/?fields=open_id,display_name",
            vendor="TikTok",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        ).get("data", {})
        name = user.get("user", {}).get("display_name", "") if isinstance(user, dict) else ""
        suffix = f"-{args.profile}" if args.profile else ""
        path = ROOT / ".omemo" / f"tiktok{suffix}-token.json"
        write_receipt(path, token)
        replace_block(
            env_path,
            "tiktok-direct",
            [
                f"OMEMO_TIKTOK_CLIENT_KEY={client_key}",
                f'OMEMO_TIKTOK_CLIENT_SECRET="{secret.strip()}"',
                f'OMEMO_TIKTOK_TOKEN_PATH="{path}"',
                f"OMEMO_TIKTOK_OPEN_ID={token['open_id']}",
                f"OMEMO_TIKTOK_HANDLE=@{args.handle.lstrip('@')}",
                "OMEMO_TIKTOK_MODE=inbox",
                "OMEMO_TIKTOK_PRIVACY=SELF_ONLY",
                f'OMEMO_TIKTOK_RECEIPTS="{ROOT / ".omemo" / f"tiktok{suffix}-uploads"}"',
            ],
        )
        if "video.publish" not in granted:
            print("Разрешение video.publish не выдано: доступен только режим черновиков.")
        print(f"Подключён TikTok {name or args.handle}. Режим: черновики. Ничего не отправлено.")
        return 0
    except (ClipPublisherError, ValueError, OSError) as error:
        print(f"Подключение не завершено: {error}")
        return 1


if __name__ == "__main__":
    load_project_env(__file__)
    sys.exit(main(sys.argv[1:]))
