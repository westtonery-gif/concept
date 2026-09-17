"""The Google Docs Adapter — a real ``ReviewDesk`` over the Google Drive API (ADR-0043).

A review is a Google Doc in one configured Drive folder, created from plain text by Drive's
conversion on upload and found again by the hashes of its ``review_id`` and package kept in the
file's ``appProperties``. The reviewer decides by typing a word after ``РЕШЕНИЕ:`` in the block at
the top of the Doc; ``fetch_decision`` exports the text and reads that block only. The factory signs
in as a service account (a JWT signed with RS256, exchanged for an access token). The API is reached
through ``urllib``; no Google shape leaves this module (ADAPTER_SPEC §3).

Where the contract is silent (ADR-0043 §5): a decision not yet made — or not recognisable — is
``None``, while a technical or configuration fault, or a damaged decision block, is
``ReviewDeskError`` — never a guess.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from omemo_content_factory.adapters.review_desk import (
    ReviewDecision,
    ReviewDeskError,
    ReviewPackage,
)
from omemo_content_factory.domain.human_review import ReviewId, ReviewStatus

__all__ = [
    "DECISION_MARKER",
    "DEFAULT_API_URL",
    "DRIVE_SCOPE",
    "REASON_MARKER",
    "SEPARATOR",
    "GoogleDocsDeskSettings",
    "GoogleDocsReviewDesk",
    "google_docs_settings_from_env",
    "read_decision",
    "render_review",
]

DEFAULT_API_URL = "https://www.googleapis.com"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

SERVICE_ACCOUNT_FILE_VAR = "OMEMO_GOOGLE_SERVICE_ACCOUNT_FILE"
REVIEW_FOLDER_ID_VAR = "OMEMO_GOOGLE_REVIEW_FOLDER_ID"

DECISION_MARKER = "РЕШЕНИЕ:"
REASON_MARKER = "ПРИЧИНА:"
SEPARATOR = "======== МАТЕРИАЛЫ РЕВЬЮ ========"
_INSTRUCTIONS = (
    "Как принять решение: после «РЕШЕНИЕ:» впишите одно слово — одобрено, отклонено или "
    "доработать. После «ПРИЧИНА:» — причину отказа или что доработать (для одобрения можно не "
    "писать). Строки до разделителя не удаляйте."
)
_DECISIONS = {
    "одобрено": ReviewStatus.APPROVED,
    "approved": ReviewStatus.APPROVED,
    "отклонено": ReviewStatus.REJECTED,
    "rejected": ReviewStatus.REJECTED,
    "доработать": ReviewStatus.CHANGES_REQUESTED,
    "changes_requested": ReviewStatus.CHANGES_REQUESTED,
}

_GOOGLE_DOC = "application/vnd.google-apps.document"
_REVIEW_PROPERTY = "omemo_review"
_PACKAGE_PROPERTY = "omemo_package"
_TOKEN_LIFETIME = 3600
_TOKEN_MARGIN = 60
_FILE_ID = re.compile(r"[A-Za-z0-9_-]+")
_DOC_LOCATION = "https://docs.google.com/document/d/{file_id}/edit"

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GoogleDocsDeskSettings:
    """Who the factory signs in as and which Drive folder holds the review Docs."""

    client_email: str
    private_key: str = field(repr=False)
    token_uri: str
    folder_id: str

    def __post_init__(self) -> None:
        for name in ("client_email", "private_key", "token_uri", "folder_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Google Docs desk settings need a non-blank {name}")


def google_docs_settings_from_env(environ: Mapping[str, str]) -> GoogleDocsDeskSettings:
    """Read both required variables and the key file; anything missing or malformed fails closed.

    Messages name the variables and the key file's path, never key material.
    """
    names = (SERVICE_ACCOUNT_FILE_VAR, REVIEW_FOLDER_ID_VAR)
    values = {name: environ.get(name, "").strip() for name in names}
    missing = [name for name in names if not values[name]]
    if missing:
        raise ReviewDeskError("the Google Docs desk is not configured; set " + ", ".join(missing))
    path = Path(values[SERVICE_ACCOUNT_FILE_VAR])
    try:
        key = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ReviewDeskError(
            f"the service account key {str(path)!r} is not a readable JSON file"
        ) from error
    if not isinstance(key, dict) or key.get("type") != "service_account":
        raise ReviewDeskError(f"{str(path)!r} is not a service account key")
    fields = {name: key.get(name) for name in ("client_email", "private_key", "token_uri")}
    blank = [
        name for name, value in fields.items() if not isinstance(value, str) or not value.strip()
    ]
    if blank:
        raise ReviewDeskError(
            f"the service account key {str(path)!r} has no " + ", ".join(sorted(blank))
        )
    settings = GoogleDocsDeskSettings(
        client_email=str(fields["client_email"]),
        private_key=str(fields["private_key"]),
        token_uri=str(fields["token_uri"]),
        folder_id=values[REVIEW_FOLDER_ID_VAR],
    )
    _rsa_key(settings.private_key)
    return settings


class GoogleDocsReviewDesk:
    """A ``ReviewDesk`` whose reviews are Google Docs in one Drive folder (ADAPTER_SPEC §6)."""

    def __init__(
        self,
        settings: GoogleDocsDeskSettings,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 10.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._key = _rsa_key(settings.private_key)
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout
        self._clock = clock
        self._token: tuple[str, float] | None = None

    def publish(self, package: ReviewPackage, /) -> str:
        """Create the review Doc, or return the existing one for the same package.

        Another package under an already-published ``review_id`` raises ``ReviewDeskError`` and
        the first Doc stays.
        """
        package_hash = _package_hash(package)
        existing = self._find(package.review_id)
        if existing is not None:
            file_id, properties = existing
            if properties.get(_PACKAGE_PROPERTY) != package_hash:
                raise ReviewDeskError(
                    f"review {package.review_id} is already published with another package"
                )
            return _DOC_LOCATION.format(file_id=file_id)
        metadata = {
            "name": f"Ревью {package.review_id} "
            f"({package.candidate.kind}, v{package.candidate.version})",
            "mimeType": _GOOGLE_DOC,
            "parents": [self._settings.folder_id],
            "appProperties": {
                _REVIEW_PROPERTY: _sha256(package.review_id),
                _PACKAGE_PROPERTY: package_hash,
            },
        }
        created = self._create(metadata, render_review(package))
        return _DOC_LOCATION.format(file_id=_file_id(created))

    def fetch_decision(self, review_id: ReviewId, /) -> ReviewDecision | None:
        """The decision typed into the Doc's decision block, or ``None`` while there is none.

        A ``review_id`` with no Doc, or a Doc whose decision block is damaged, raises
        ``ReviewDeskError``.
        """
        existing = self._find(review_id)
        if existing is None:
            raise ReviewDeskError(f"review {review_id} was never published")
        file_id, _ = existing
        raw = self._call("GET", f"/drive/v3/files/{file_id}/export?mimeType=text%2Fplain")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ReviewDeskError("Google Drive exported a Doc that is not UTF-8") from error
        return read_decision(text, review_id=review_id)

    # --- Drive ----------------------------------------------------------------------------

    def _find(self, review_id: ReviewId) -> tuple[str, dict[str, Any]] | None:
        folder = self._settings.folder_id.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            f"'{folder}' in parents and trashed = false and appProperties has "
            f"{{ key='{_REVIEW_PROPERTY}' and value='{_sha256(review_id)}' }}"
        )
        params = urllib.parse.urlencode(
            {
                "q": query,
                "fields": "files(id,appProperties)",
                "corpora": "allDrives",
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
                "pageSize": "10",
            }
        )
        listing = _json_object(self._call("GET", f"/drive/v3/files?{params}"))
        files = listing.get("files")
        if not isinstance(files, list) or not all(isinstance(f, dict) for f in files):
            raise ReviewDeskError("Google Drive returned a file listing of an unexpected shape")
        if not files:
            return None
        if len(files) > 1:
            raise ReviewDeskError(f"review {review_id} is published more than once")
        properties = files[0].get("appProperties", {})
        if not isinstance(properties, dict):
            raise ReviewDeskError("Google Drive returned file properties of an unexpected shape")
        return _file_id(files[0]), properties

    def _create(self, metadata: dict[str, Any], text: str) -> dict[str, Any]:
        boundary = f"omemo-{uuid.uuid4().hex}"
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata, ensure_ascii=False)}\r\n"
            f"--{boundary}\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n"
            f"{text}\r\n--{boundary}--\r\n"
        ).encode()
        path = "/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id"
        raw = self._call(
            "POST", path, body=body, content_type=f"multipart/related; boundary={boundary}"
        )
        return _json_object(raw)

    # --- transport ------------------------------------------------------------------------

    def _call(
        self, method: str, path: str, *, body: bytes | None = None, content_type: str | None = None
    ) -> bytes:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        if content_type is not None:
            headers["Content-Type"] = content_type
        name = f"{method} {path.split('?')[0]}"
        try:
            return self._send(self._api_url + path, body, headers, method, name)
        except _HTTPStatusError as error:
            if error.code == 401:
                self._token = None
            raise ReviewDeskError(f"Google Drive refused {name}: HTTP {error.code}") from error

    def _access_token(self) -> str:
        now = self._clock()
        if self._token is not None and now < self._token[1]:
            return self._token[0]
        settings = self._settings
        issued = int(now)
        claims = {
            "iss": settings.client_email,
            "scope": DRIVE_SCOPE,
            "aud": settings.token_uri,
            "iat": issued,
            "exp": issued + _TOKEN_LIFETIME,
        }
        form = urllib.parse.urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": _signed_jwt(self._key, claims),
            }
        ).encode("ascii")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        try:
            raw = self._send(settings.token_uri, form, headers, "POST", "the token request")
        except _HTTPStatusError as error:
            raise ReviewDeskError(
                f"Google refused the service account sign-in: HTTP {error.code}"
            ) from error
        grant = _json_object(raw)
        token, expires_in = grant.get("access_token"), grant.get("expires_in")
        if not isinstance(token, str) or not token or not isinstance(expires_in, int):
            raise ReviewDeskError("Google returned an access token of an unexpected shape")
        self._token = (token, now + expires_in - _TOKEN_MARGIN)
        return token

    def _send(
        self, url: str, body: bytes | None, headers: dict[str, str], method: str, name: str
    ) -> bytes:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw: bytes = response.read()
                return raw
        except urllib.error.HTTPError as error:
            error.close()
            raise _HTTPStatusError(error.code) from error
        except (urllib.error.URLError, OSError) as error:
            raise ReviewDeskError(f"Google could not be reached for {name}: {error}") from error


class _HTTPStatusError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


# --- The Doc's text ---------------------------------------------------------------------


def render_review(package: ReviewPackage) -> str:
    """The Doc's plain text: the decision block, the separator, then the review material."""
    candidate = package.candidate
    lines = [
        _INSTRUCTIONS,
        DECISION_MARKER,
        REASON_MARKER,
        SEPARATOR,
        f"Run: {package.run_id}",
        f"Ревью: {package.review_id}",
        f"Артефакт: {candidate.artifact_id} — {candidate.kind}, версия {candidate.version}",
    ]
    if candidate.supersedes_ref is not None:
        lines.append(f"Заменяет версию: {candidate.supersedes_ref}")
    lines += ["", "БРИФ", package.brief, "", "ЗАМЕЧАНИЯ QA"]
    lines += [f"- {flag}" for flag in package.qa_flags] or ["— нет"]
    lines += ["", "КАНДИДАТ", candidate.content]
    return "\n".join(lines)


def read_decision(text: str, *, review_id: ReviewId) -> ReviewDecision | None:
    """Read the decision block above the first separator line (ADR-0043 §4)."""
    lines = text.lstrip("\ufeff").replace("\x0b", "\n").splitlines()
    stripped = [line.strip() for line in lines]
    if SEPARATOR not in stripped:
        raise ReviewDeskError(f"the Doc of review {review_id} has lost its separator line")
    block = stripped[: stripped.index(SEPARATOR)]
    decision_at = _marker_line(block, DECISION_MARKER, review_id)
    reason_at = _marker_line(block, REASON_MARKER, review_id)
    if reason_at < decision_at:
        raise ReviewDeskError(f"the Doc of review {review_id} has its decision lines out of order")
    value = block[decision_at][len(DECISION_MARKER) :].strip().rstrip(".!").strip()
    if not value:
        return None
    decision = _DECISIONS.get(value.casefold())
    if decision is None:
        _LOG.warning("review %s: unrecognised decision %r, still pending", review_id, value)
        return None
    reason_lines = [block[reason_at][len(REASON_MARKER) :], *block[reason_at + 1 :]]
    reason = "\n".join(reason_lines).strip()
    return ReviewDecision(decision, reason or None)


def _marker_line(block: list[str], marker: str, review_id: ReviewId) -> int:
    found = [index for index, line in enumerate(block) if line.upper().startswith(marker)]
    if len(found) != 1:
        state = "has no" if not found else "repeats its"
        raise ReviewDeskError(f"the Doc of review {review_id} {state} {marker!r} line")
    return found[0]


# --- helpers ----------------------------------------------------------------------------


def _rsa_key(pem: str) -> rsa.RSAPrivateKey:
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise ReviewDeskError("the service account private key is not a PEM key") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ReviewDeskError("the service account private key is not an RSA key")
    return key


def _signed_jwt(key: rsa.RSAPrivateKey, claims: dict[str, Any]) -> str:
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("utf-8"))
    payload = _b64url(json.dumps(claims).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _package_hash(package: ReviewPackage) -> str:
    candidate = package.candidate
    canonical = {
        "run_id": package.run_id,
        "review_id": package.review_id,
        "brief": package.brief,
        "qa_flags": list(package.qa_flags),
        "candidate": {
            "artifact_id": candidate.artifact_id,
            "run_id": candidate.run_id,
            "output_ref": candidate.output_ref,
            "kind": candidate.kind,
            "content": candidate.content,
            "version": candidate.version,
            "status": candidate.status.value,
            "supersedes_ref": candidate.supersedes_ref,
        },
    }
    return _sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True))


def _file_id(file: dict[str, Any]) -> str:
    file_id = file.get("id")
    if not isinstance(file_id, str) or not _FILE_ID.fullmatch(file_id):
        raise ReviewDeskError("Google Drive returned a file id of an unexpected shape")
    return file_id


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(raw)
    except ValueError as error:
        raise ReviewDeskError("Google returned a response that is not JSON") from error
    if not isinstance(decoded, dict):
        raise ReviewDeskError("Google returned a response that is not a JSON object")
    return decoded
