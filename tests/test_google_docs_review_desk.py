"""Tests for the Google Docs Adapter, ``GoogleDocsReviewDesk`` (ADR-0043).

Maps ADAPTER_ACCEPTANCE.md §10 (GDR). The adapter's real code — JWT signing, token exchange,
multipart upload, export — talks over a real socket to a local HTTP server that plays Google: its
token endpoint checks the JWT's RS256 signature against the test key, and its Drive keeps files in
memory, converts an upload into a Doc and exports it the way Drive does (a BOM, ``\\r\\n``). The
reviewer is played by editing a stored Doc's text. Nothing is mocked below the adapter and nothing
leaves ``127.0.0.1``.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from email.message import Message
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from omemo_content_factory.adapters.review_desk import (
    ReviewDecision,
    ReviewDesk,
    ReviewDeskError,
    ReviewPackage,
)
from omemo_content_factory.domain.artifact import ArtifactStatus, ArtifactView
from omemo_content_factory.domain.human_review import ReviewStatus
from omemo_content_factory.infrastructure.google_docs_review_desk import (
    DRIVE_SCOPE,
    SEPARATOR,
    GoogleDocsDeskSettings,
    GoogleDocsReviewDesk,
    google_docs_settings_from_env,
)

CLIENT_EMAIL = "factory@omemo-test.iam.gserviceaccount.com"
FOLDER = "0AFolderOnASharedDrive"
REVIEW = "run-1-review-1"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode("ascii")


# --- A local server playing Google ------------------------------------------------------


@dataclass
class _Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class _FakeGoogle:
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[_Recorded] = field(default_factory=list)
    grants: list[dict[str, Any]] = field(default_factory=list)
    tokens: set[str] = field(default_factory=set)
    forced: dict[str, tuple[int, bytes]] = field(default_factory=dict)
    delay: float = 0.0

    def handle(self, method: str, raw_path: str, headers: dict[str, str], body: bytes) -> Any:
        if self.delay:
            time.sleep(self.delay)
        parts = urlsplit(raw_path)
        route = _route(method, parts.path)
        if route in self.forced:
            status, payload = self.forced[route]
            return status, payload, "application/json"
        if route == "token":
            return self._token(body)
        if headers.get("Authorization", "").removeprefix("Bearer ") not in self.tokens:
            return (*_json(401, {"error": {"code": 401}}), "application/json")
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if route == "list":
            return (*self._list(query), "application/json")
        if route == "create":
            return (*self._create(query, headers, body), "application/json")
        if route == "export":
            return self._export(parts.path.split("/")[4], query)
        return (*_json(400, {"error": {"code": 400}}), "application/json")

    def edit(self, file_id: str, old: str, new: str) -> None:
        """The reviewer types into the Doc."""
        text = self.files[file_id]["text"]
        assert old in text
        self.files[file_id]["text"] = text.replace(old, new, 1)

    def only_file(self) -> tuple[str, dict[str, Any]]:
        assert len(self.files) == 1
        return next(iter(self.files.items()))

    def _token(self, body: bytes) -> Any:
        form = {k: v[0] for k, v in parse_qs(body.decode("ascii")).items()}
        assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
        header_b64, payload_b64, signature_b64 = form["assertion"].split(".")
        try:
            _KEY.public_key().verify(
                _unb64(signature_b64),
                f"{header_b64}.{payload_b64}".encode("ascii"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except InvalidSignature:
            return (*_json(400, {"error": "invalid_grant"}), "application/json")
        self.grants.append(
            {"header": json.loads(_unb64(header_b64)), "claims": json.loads(_unb64(payload_b64))}
        )
        token = f"access-token-{len(self.grants)}"
        self.tokens.add(token)
        return (*_json(200, {"access_token": token, "expires_in": 3600}), "application/json")

    def _list(self, query: dict[str, str]) -> tuple[int, bytes]:
        assert query["corpora"] == "allDrives"
        assert query["supportsAllDrives"] == query["includeItemsFromAllDrives"] == "true"
        match = re.fullmatch(
            r"'(.*)' in parents and trashed = false and appProperties has "
            r"\{ key='omemo_review' and value='([0-9a-f]{64})' \}",
            query["q"],
        )
        assert match is not None, query["q"]
        folder, review_hash = match.groups()
        found = [
            {"id": file_id, "appProperties": file["appProperties"]}
            for file_id, file in self.files.items()
            if folder in file["parents"]
            and not file["trashed"]
            and file["appProperties"].get("omemo_review") == review_hash
        ]
        return _json(200, {"files": found})

    def _create(
        self, query: dict[str, str], headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        assert query == {"uploadType": "multipart", "supportsAllDrives": "true", "fields": "id"}
        head = f"Content-Type: {headers['Content-Type']}\r\n\r\n".encode("ascii")
        message = BytesParser(policy=HTTP).parsebytes(head + body)
        metadata_part, media_part = list(message.iter_parts())
        assert metadata_part.get_content_type() == "application/json"
        assert media_part.get_content_type() == "text/plain"
        metadata = json.loads(_part_bytes(metadata_part))
        text = _part_bytes(media_part).decode("utf-8")
        file_id = f"docId_{len(self.files) + 1}"
        self.files[file_id] = {
            "name": metadata["name"],
            "mimeType": metadata["mimeType"],
            "parents": metadata["parents"],
            "appProperties": metadata["appProperties"],
            "text": text,
            "trashed": False,
        }
        return _json(200, {"id": file_id})

    def _export(self, file_id: str, query: dict[str, str]) -> Any:
        assert query == {"mimeType": "text/plain"}
        file = self.files.get(file_id)
        if file is None:
            return (*_json(404, {"error": {"code": 404}}), "application/json")
        exported = "\ufeff" + file["text"].replace("\r\n", "\n").replace("\n", "\r\n")
        return 200, exported.encode("utf-8"), "text/plain; charset=utf-8"


def _part_bytes(part: Message) -> bytes:
    payload = part.get_payload(decode=True)
    assert isinstance(payload, bytes)
    return payload


def _route(method: str, path: str) -> str:
    if method == "POST" and path == "/token":
        return "token"
    if method == "GET" and path == "/drive/v3/files":
        return "list"
    if method == "POST" and path == "/upload/drive/v3/files":
        return "create"
    if method == "GET" and re.fullmatch(r"/drive/v3/files/[^/]+/export", path):
        return "export"
    return "unknown"


def _json(status: int, payload: object) -> tuple[int, bytes]:
    return status, json.dumps(payload).encode("utf-8")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


@pytest.fixture
def google() -> Iterator[tuple[_FakeGoogle, str]]:
    fake = _FakeGoogle()

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            headers = dict(self.headers)
            fake.requests.append(_Recorded(self.command, self.path, headers, body))
            status, payload, content_type = fake.handle(self.command, self.path, headers, body)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _serve  # noqa: N815
        do_POST = _serve  # noqa: N815

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


class _Clock:
    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def __call__(self) -> float:
        return self.now


def _settings(url: str, **overrides: Any) -> GoogleDocsDeskSettings:
    fields: dict[str, Any] = {
        "client_email": CLIENT_EMAIL,
        "private_key": PRIVATE_PEM,
        "token_uri": f"{url}/token",
        "folder_id": FOLDER,
    }
    fields.update(overrides)
    return GoogleDocsDeskSettings(**fields)


def _desk(url: str, **kwargs: Any) -> GoogleDocsReviewDesk:
    return GoogleDocsReviewDesk(_settings(url), api_url=url, **kwargs)


def _candidate(**overrides: Any) -> ArtifactView:
    fields: dict[str, Any] = {
        "artifact_id": "run-1-artifact-2",
        "run_id": "run-1",
        "output_ref": "run-1-task-2-output-1",
        "kind": "script",
        "content": "Хук: почему сон важнее кофе.\nСценарий: три шага к утру без будильника.",
        "version": 2,
        "status": ArtifactStatus.CANDIDATE,
        "supersedes_ref": "run-1-artifact-1",
    }
    fields.update(overrides)
    return ArtifactView(**fields)


def _package(**overrides: Any) -> ReviewPackage:
    fields: dict[str, Any] = {
        "run_id": "run-1",
        "review_id": REVIEW,
        "candidate": _candidate(),
        "brief": "Тема: сон и восстановление.\nАудитория: офисные сотрудники.",
        "qa_flags": ("Проверить цифру про 8 часов.", "Нет ссылки на источник."),
    }
    fields.update(overrides)
    return ReviewPackage(**fields)


def _decide(fake: _FakeGoogle, decision: str, reason: str = "") -> None:
    file_id, _ = fake.only_file()
    fake.edit(file_id, "\nРЕШЕНИЕ:", f"\nРЕШЕНИЕ: {decision}")
    if reason:
        fake.edit(file_id, "\nПРИЧИНА:", f"\nПРИЧИНА: {reason}")


def _drive_requests(fake: _FakeGoogle) -> list[_Recorded]:
    return [r for r in fake.requests if r.path != "/token"]


# --- GDR-01 ------------------------------------------------------------------------------


def test_gdr_01_the_adapter_is_the_contract_and_signs_in_as_the_service_account(
    google: tuple[_FakeGoogle, str],
) -> None:
    fake, url = google
    desk: ReviewDesk = _desk(url)

    desk.publish(_package())
    assert desk.fetch_decision(REVIEW) is None

    assert len(fake.grants) == 1  # one sign-in, reused
    grant = fake.grants[0]
    assert grant["header"] == {"alg": "RS256", "typ": "JWT"}
    claims = grant["claims"]
    assert claims["iss"] == CLIENT_EMAIL
    assert claims["scope"] == DRIVE_SCOPE
    assert claims["aud"] == f"{url}/token"
    assert claims["exp"] - claims["iat"] == 3600
    drive = _drive_requests(fake)
    assert len(drive) == 4  # list, create, list, export
    assert all(r.headers["Authorization"] == "Bearer access-token-1" for r in drive)


def test_gdr_01_an_expiring_token_is_renewed_and_a_401_drops_it(
    google: tuple[_FakeGoogle, str],
) -> None:
    fake, url = google
    clock = _Clock()
    desk = _desk(url, clock=clock)
    desk.publish(_package())

    clock.now += 3600 - 59  # inside the 60 s margin
    desk.fetch_decision(REVIEW)
    assert len(fake.grants) == 2

    fake.tokens.clear()  # Google revoked every token
    with pytest.raises(ReviewDeskError, match="HTTP 401"):
        desk.fetch_decision(REVIEW)
    assert desk.fetch_decision(REVIEW) is None
    assert len(fake.grants) == 3


# --- GDR-02 ------------------------------------------------------------------------------


def test_gdr_02_publish_creates_one_doc_with_the_decision_block_and_the_context(
    google: tuple[_FakeGoogle, str],
) -> None:
    fake, url = google

    location = _desk(url).publish(_package())

    file_id, file = fake.only_file()
    assert location == f"https://docs.google.com/document/d/{file_id}/edit"
    assert file["mimeType"] == "application/vnd.google-apps.document"
    assert file["parents"] == [FOLDER]
    assert file["name"] == f"Ревью {REVIEW} (script, v2)"
    assert set(file["appProperties"]) == {"omemo_review", "omemo_package"}
    assert all(re.fullmatch("[0-9a-f]{64}", v) for v in file["appProperties"].values())
    assert REVIEW not in json.dumps(file["appProperties"])
    lines = file["text"].split("\n")
    assert lines[1:4] == ["РЕШЕНИЕ:", "ПРИЧИНА:", SEPARATOR]
    material = "\n".join(lines[4:])
    for expected in (
        "Run: run-1",
        f"Ревью: {REVIEW}",
        "Артефакт: run-1-artifact-2 — script, версия 2",
        "Заменяет версию: run-1-artifact-1",
        "БРИФ\nТема: сон и восстановление.\nАудитория: офисные сотрудники.",
        "ЗАМЕЧАНИЯ QA\n- Проверить цифру про 8 часов.\n- Нет ссылки на источник.",
        "КАНДИДАТ\nХук: почему сон важнее кофе.\nСценарий: три шага к утру без будильника.",
    ):
        assert expected in material


def test_gdr_02_a_first_version_without_qa_flags_says_so(
    google: tuple[_FakeGoogle, str],
) -> None:
    fake, url = google

    _desk(url).publish(_package(candidate=_candidate(version=1, supersedes_ref=None), qa_flags=()))

    text = fake.only_file()[1]["text"]
    assert "Заменяет версию" not in text
    assert "ЗАМЕЧАНИЯ QA\n— нет\n" in text


# --- GDR-03 ------------------------------------------------------------------------------


def test_gdr_03_publishing_the_same_package_again_returns_the_same_doc(
    google: tuple[_FakeGoogle, str],
) -> None:
    fake, url = google
    first = _desk(url).publish(_package())

    again = _desk(url).publish(_package())  # a fresh desk, e.g. after a restart

    assert again == first
    assert len(fake.files) == 1
    assert sum(r.path.startswith("/upload/") for r in fake.requests) == 1


def test_gdr_03_another_package_under_a_published_review_is_refused(
    google: tuple[_FakeGoogle, str],
) -> None:
    fake, url = google
    desk = _desk(url)
    desk.publish(_package())
    text = fake.only_file()[1]["text"]

    with pytest.raises(ReviewDeskError, match="already published with another package"):
        desk.publish(_package(brief="Другой бриф."))

    assert len(fake.files) == 1
    assert fake.only_file()[1]["text"] == text


def test_gdr_03_a_trashed_doc_is_not_published_any_more(
    google: tuple[_FakeGoogle, str],
) -> None:
    fake, url = google
    desk = _desk(url)
    desk.publish(_package())
    fake.only_file()[1]["trashed"] = True

    with pytest.raises(ReviewDeskError, match="never published"):
        desk.fetch_decision(REVIEW)
    desk.publish(_package())
    assert len(fake.files) == 2


# --- GDR-04 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("одобрено", ReviewStatus.APPROVED),
        ("Одобрено.", ReviewStatus.APPROVED),
        ("APPROVED", ReviewStatus.APPROVED),
        ("отклонено", ReviewStatus.REJECTED),
        ("rejected!", ReviewStatus.REJECTED),
        ("  доработать  ", ReviewStatus.CHANGES_REQUESTED),
        ("changes_requested", ReviewStatus.CHANGES_REQUESTED),
    ],
)
def test_gdr_04_the_typed_word_is_the_decision(
    google: tuple[_FakeGoogle, str], typed: str, expected: ReviewStatus
) -> None:
    fake, url = google
    desk = _desk(url)
    desk.publish(_package())

    _decide(fake, typed)

    assert desk.fetch_decision(REVIEW) == ReviewDecision(expected, None)


def test_gdr_04_the_reason_spans_every_line_up_to_the_separator(
    google: tuple[_FakeGoogle, str],
) -> None:
    fake, url = google
    desk = _desk(url)
    desk.publish(_package())

    _decide(fake, "доработать", "Сократить хук.\nУбрать цифру про 8 часов.\x0bДобавить источник.")

    assert desk.fetch_decision(REVIEW) == ReviewDecision(
        ReviewStatus.CHANGES_REQUESTED,
        "Сократить хук.\nУбрать цифру про 8 часов.\nДобавить источник.",
    )


def test_gdr_04_no_decision_yet_is_pending_and_a_reason_alone_decides_nothing(
    google: tuple[_FakeGoogle, str],
) -> None:
    fake, url = google
    desk = _desk(url)
    desk.publish(_package())
    assert desk.fetch_decision(REVIEW) is None

    file_id, _ = fake.only_file()
    fake.edit(file_id, "\nПРИЧИНА:", "\nПРИЧИНА: пока думаю")

    assert desk.fetch_decision(REVIEW) is None


def test_gdr_04_an_unrecognised_word_is_pending_with_a_warning(
    google: tuple[_FakeGoogle, str], caplog: pytest.LogCaptureFixture
) -> None:
    fake, url = google
    desk = _desk(url)
    desk.publish(_package())
    _decide(fake, "одобряю")

    with caplog.at_level(logging.WARNING):
        assert desk.fetch_decision(REVIEW) is None

    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert REVIEW in caplog.text and "одобряю" in caplog.text


def test_gdr_04_a_marker_in_the_candidate_is_never_read_as_the_decision(
    google: tuple[_FakeGoogle, str],
) -> None:
    _, url = google
    desk = _desk(url)
    desk.publish(_package(candidate=_candidate(content="РЕШЕНИЕ: одобрено\nПРИЧИНА: так надо")))

    assert desk.fetch_decision(REVIEW) is None


# --- GDR-05 ------------------------------------------------------------------------------


def test_gdr_05_an_unpublished_review_is_an_error(google: tuple[_FakeGoogle, str]) -> None:
    _, url = google

    with pytest.raises(ReviewDeskError, match="never published"):
        _desk(url).fetch_decision("run-9-review-1")


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        (SEPARATOR, "", "separator"),
        ("\nРЕШЕНИЕ:", "\n", "has no 'РЕШЕНИЕ:'"),
        ("\nПРИЧИНА:", "\n", "has no 'ПРИЧИНА:'"),
        ("\nПРИЧИНА:", "\nПРИЧИНА:\nРЕШЕНИЕ: отклонено", "repeats its 'РЕШЕНИЕ:'"),
        ("РЕШЕНИЕ:\nПРИЧИНА:", "ПРИЧИНА:\nРЕШЕНИЕ: одобрено", "out of order"),
    ],
)
def test_gdr_05_a_damaged_decision_block_is_an_error(
    google: tuple[_FakeGoogle, str], old: str, new: str, match: str
) -> None:
    fake, url = google
    desk = _desk(url)
    desk.publish(_package())
    fake.edit(fake.only_file()[0], old, new)

    with pytest.raises(ReviewDeskError, match=match):
        desk.fetch_decision(REVIEW)


def test_gdr_05_a_review_published_twice_is_an_error(google: tuple[_FakeGoogle, str]) -> None:
    fake, url = google
    desk = _desk(url)
    desk.publish(_package())
    fake.files["docId_copy"] = dict(fake.only_file()[1])  # a concurrent publisher

    with pytest.raises(ReviewDeskError, match="more than once"):
        desk.fetch_decision(REVIEW)
    with pytest.raises(ReviewDeskError, match="more than once"):
        desk.publish(_package())


# --- GDR-06 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "status", "payload", "match"),
    [
        ("token", 400, b'{"error": "invalid_grant"}', "sign-in: HTTP 400"),
        ("token", 200, b'{"token_type": "Bearer"}', "access token of an unexpected shape"),
        ("list", 403, b"{}", "refused GET /drive/v3/files: HTTP 403"),
        ("list", 500, b"<html>", "HTTP 500"),
        ("list", 200, b"not json", "not JSON"),
        ("list", 200, b"[]", "not a JSON object"),
        ("list", 200, b'{"files": {}}', "listing of an unexpected shape"),
        ("list", 200, b'{"files": [{"id": "../x"}]}', "file id of an unexpected shape"),
        ("create", 429, b"{}", "refused POST /upload/drive/v3/files: HTTP 429"),
        ("create", 200, b'{"name": "no id"}', "file id of an unexpected shape"),
    ],
)
def test_gdr_06_faults_on_publish_are_errors(
    google: tuple[_FakeGoogle, str], route: str, status: int, payload: bytes, match: str
) -> None:
    fake, url = google
    fake.forced[route] = (status, payload)

    with pytest.raises(ReviewDeskError, match=match):
        _desk(url).publish(_package())


@pytest.mark.parametrize(
    ("status", "payload", "match"),
    [(500, b"{}", "HTTP 500"), (200, b"\xff\xfe\x00", "not UTF-8")],
)
def test_gdr_06_faults_on_export_are_errors(
    google: tuple[_FakeGoogle, str], status: int, payload: bytes, match: str
) -> None:
    fake, url = google
    desk = _desk(url)
    desk.publish(_package())
    fake.forced["export"] = (status, payload)

    with pytest.raises(ReviewDeskError, match=match):
        desk.fetch_decision(REVIEW)


def test_gdr_06_an_unreachable_or_slow_google_is_an_error(
    google: tuple[_FakeGoogle, str],
) -> None:
    fake, url = google
    fake.delay = 0.5
    with pytest.raises(ReviewDeskError, match="could not be reached"):
        _desk(url, timeout=0.05).publish(_package())

    closed = GoogleDocsReviewDesk(
        _settings("http://127.0.0.1:9"), api_url="http://127.0.0.1:9", timeout=1
    )
    with pytest.raises(ReviewDeskError, match="could not be reached"):
        closed.fetch_decision(REVIEW)


def test_gdr_06_no_message_carries_a_token_or_key_material(
    google: tuple[_FakeGoogle, str],
) -> None:
    fake, url = google
    desk = _desk(url)
    desk.publish(_package())
    fake.forced["export"] = (403, b'{"error": "access-token-1 is not allowed"}')

    with pytest.raises(ReviewDeskError) as caught:
        desk.fetch_decision(REVIEW)

    assert "access-token" not in str(caught.value)
    assert "PRIVATE KEY" not in repr(_settings(url))


# --- GDR-07 ------------------------------------------------------------------------------


def test_gdr_07_ids_never_reach_a_query_or_path_raw(google: tuple[_FakeGoogle, str]) -> None:
    fake, url = google
    desk = _desk(url)
    odd = "run-1' or name contains 'x/../export?#"
    package = _package(review_id=odd)

    desk.publish(package)
    _decide(fake, "одобрено")

    assert desk.fetch_decision(odd) == ReviewDecision(ReviewStatus.APPROVED)
    assert all(odd not in r.path for r in fake.requests)
    with pytest.raises(ReviewDeskError, match="never published"):
        desk.fetch_decision(REVIEW)


def test_gdr_07_a_quote_in_the_folder_id_is_escaped(google: tuple[_FakeGoogle, str]) -> None:
    fake, url = google
    desk = GoogleDocsReviewDesk(_settings(url, folder_id="it's"), api_url=url)

    desk.publish(_package())

    listing = next(r for r in fake.requests if r.path.startswith("/drive/v3/files?"))
    query = parse_qs(urlsplit(listing.path).query)["q"][0]
    assert query.startswith("'it\\'s' in parents")


# --- GDR-08 ------------------------------------------------------------------------------


def _key_file(tmp_path: Path, **overrides: Any) -> Path:
    key: dict[str, Any] = {
        "type": "service_account",
        "client_email": CLIENT_EMAIL,
        "private_key": PRIVATE_PEM,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    key.update(overrides)
    path = tmp_path / "key.json"
    path.write_text(json.dumps(key), encoding="utf-8")
    return path


def _env(path: Path | str) -> dict[str, str]:
    return {
        "OMEMO_GOOGLE_SERVICE_ACCOUNT_FILE": str(path),
        "OMEMO_GOOGLE_REVIEW_FOLDER_ID": FOLDER,
    }


def test_gdr_08_settings_come_from_the_environment_and_the_key_file(tmp_path: Path) -> None:
    settings = google_docs_settings_from_env(_env(_key_file(tmp_path)))

    assert settings == GoogleDocsDeskSettings(
        client_email=CLIENT_EMAIL,
        private_key=PRIVATE_PEM,
        token_uri="https://oauth2.googleapis.com/token",
        folder_id=FOLDER,
    )
    assert "PRIVATE KEY" not in repr(settings)


def test_gdr_08_missing_variables_are_named(tmp_path: Path) -> None:
    with pytest.raises(ReviewDeskError) as caught:
        google_docs_settings_from_env({"OMEMO_GOOGLE_REVIEW_FOLDER_ID": "  "})

    message = str(caught.value)
    assert "OMEMO_GOOGLE_SERVICE_ACCOUNT_FILE" in message
    assert "OMEMO_GOOGLE_REVIEW_FOLDER_ID" in message


_EC_PEM = (
    ec.generate_private_key(ec.SECP256R1())
    .private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    .decode("ascii")
)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"type": "authorized_user"}, "not a service account key"),
        ({"client_email": ""}, "has no client_email"),
        ({"private_key": None, "token_uri": "  "}, None),
        ({"private_key": "-----BEGIN PRIVATE KEY-----\nbroken"}, "not a PEM key"),
        ({"private_key": _EC_PEM}, "not an RSA key"),
    ],
)
def test_gdr_08_a_malformed_key_file_fails_closed(
    tmp_path: Path, overrides: dict[str, Any], match: str | None
) -> None:
    path = _key_file(tmp_path, **overrides)

    with pytest.raises(ReviewDeskError, match=match) as caught:
        google_docs_settings_from_env(_env(path))

    assert "PRIVATE KEY" not in str(caught.value)
    assert "BEGIN" not in str(caught.value)


def test_gdr_08_an_unreadable_key_file_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")

    for path in (tmp_path / "missing.json", tmp_path / "bad.json", tmp_path):
        with pytest.raises(ReviewDeskError, match="not a readable JSON file"):
            google_docs_settings_from_env(_env(path))
