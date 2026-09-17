"""The HTTP production service n8n calls (ROADMAP Stage 11, ADR-0049).

A small stdlib HTTP server in front of one background worker. ``POST /v1/briefs`` queues one
brief, ``POST /v1/reviews/sweep`` queues every brief whose Run waits for a human, and both answer
``202`` at once; the worker invokes ``produce`` for one brief at a time. The service is transport
only: what an invocation does is ``produce``'s business (``BriefProduction.invoke``, ADR-0048), and
which briefs a sweep takes is ``waiting``'s.

A brief already waiting in the queue is not queued twice; a brief being produced is queued once
more, so an edit during a production is not lost. Every answer is JSON and the token never appears
in an answer or a log line (PRODUCTION_SERVICE_SPEC.md).
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "BRIEFS_ROUTE",
    "HEALTH_ROUTE",
    "SWEEP_ROUTE",
    "ProductionQueue",
    "ProductionService",
    "ServiceConfigurationError",
    "ServiceSettings",
    "service_settings_from_env",
]

_LOG = logging.getLogger(__name__)

TOKEN_VAR = "OMEMO_SERVICE_TOKEN"
HOST_VAR = "OMEMO_SERVICE_HOST"
PORT_VAR = "OMEMO_SERVICE_PORT"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MIN_TOKEN_LENGTH = 32

HEALTH_ROUTE = "/v1/health"
BRIEFS_ROUTE = "/v1/briefs"
SWEEP_ROUTE = "/v1/reviews/sweep"
_METHODS = {HEALTH_ROUTE: "GET", BRIEFS_ROUTE: "POST", SWEEP_ROUTE: "POST"}

MAX_BODY_BYTES = 16 * 1024
MAX_BRIEF_REF_LENGTH = 512


class ServiceConfigurationError(Exception):
    """The service is not configured well enough to start; names the variable, never its value."""


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    """Where the service listens and the token every production request must carry."""

    token: str = field(repr=False)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT


def service_settings_from_env(environ: Mapping[str, str]) -> ServiceSettings:
    """Read the token (required, 32+ chars), host and port variables (ADR-0049 §2)."""
    token = environ.get(TOKEN_VAR, "").strip()
    if not token:
        raise ServiceConfigurationError(f"the production service needs {TOKEN_VAR}")
    if len(token) < MIN_TOKEN_LENGTH:
        raise ServiceConfigurationError(
            f"{TOKEN_VAR} must be at least {MIN_TOKEN_LENGTH} characters long"
        )
    host = environ.get(HOST_VAR, "").strip() or DEFAULT_HOST
    raw_port = environ.get(PORT_VAR, "").strip()
    port = DEFAULT_PORT
    if raw_port:
        try:
            port = int(raw_port)
        except ValueError:
            raise ServiceConfigurationError(f"{PORT_VAR} must be a whole number") from None
        if not 0 <= port <= 65535:
            raise ServiceConfigurationError(f"{PORT_VAR} must be between 0 and 65535")
    return ServiceSettings(token=token, host=host, port=port)


class ProductionQueue:
    """One worker thread producing queued briefs one at a time (ADR-0049 §1)."""

    def __init__(self, produce: Callable[[str], object]) -> None:
        self._produce = produce
        self._condition = threading.Condition()
        self._queue: deque[str] = deque()
        self._busy = False
        self._stopped = False
        self._thread = threading.Thread(target=self._work, name="production-worker", daemon=True)

    def start(self) -> None:
        """Start the worker."""
        self._thread.start()

    def submit(self, brief_ref: str) -> bool:
        """Queue ``brief_ref`` unless it is already waiting in the queue; whether it was queued."""
        with self._condition:
            if self._stopped or brief_ref in self._queue:
                return False
            self._queue.append(brief_ref)
            self._condition.notify_all()
            return True

    @property
    def closed(self) -> bool:
        """Whether :meth:`stop` was called: nothing more is accepted or started."""
        with self._condition:
            return self._stopped

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait until nothing is queued or running; ``False`` if ``timeout`` passed first."""
        with self._condition:
            return self._condition.wait_for(
                lambda: not self._queue and not self._busy, timeout=timeout
            )

    def stop(self, timeout: float | None = None) -> None:
        """Drop what is queued, let the running invocation finish, end the worker."""
        with self._condition:
            self._stopped = True
            self._queue.clear()
            self._condition.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _work(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._stopped or bool(self._queue))
                if self._stopped:
                    return
                brief_ref = self._queue.popleft()
                self._busy = True
            try:
                self._produce(brief_ref)
            except Exception:
                _LOG.exception("producing brief %s failed", brief_ref)
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()


class ProductionService:
    """The HTTP front of a ``ProductionQueue`` (PRODUCTION_SERVICE_SPEC.md §3)."""

    def __init__(
        self,
        settings: ServiceSettings,
        *,
        produce: Callable[[str], object],
        waiting: Callable[[], Sequence[str]],
    ) -> None:
        self._settings = settings
        self._waiting = waiting
        self.queue = ProductionQueue(produce)
        self._server: ThreadingHTTPServer | None = None
        self._serving: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        """The host and port the service listens on (the real port when configured as ``0``)."""
        if self._server is None:
            raise RuntimeError("the production service is not started")
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        """Bind, start the worker, and serve requests on a background thread."""
        self._server = ThreadingHTTPServer(
            (self._settings.host, self._settings.port), _handler_for(self)
        )
        self._server.daemon_threads = True
        self.queue.start()
        self._serving = threading.Thread(
            target=self._server.serve_forever, args=(0.1,), name="production-http", daemon=True
        )
        self._serving.start()
        _LOG.info("production service listening on %s:%s", *self.address)

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait until the worker has nothing queued or running."""
        return self.queue.wait_idle(timeout)

    def stop(self) -> None:
        """Drop the queue, stop accepting requests, finish the running invocation. Repeatable."""
        self.queue.stop(timeout=0)
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        self.queue.stop()

    # --- request handling (called from handler threads) -------------------------------

    def authorized(self, header: str | None) -> bool:
        expected = f"Bearer {self._settings.token}".encode()
        return header is not None and hmac.compare_digest(header.encode(), expected)

    def accept_brief(self, body: object) -> tuple[int, dict[str, Any]]:
        if not isinstance(body, dict):
            return 400, {"error": "the body must be a JSON object"}
        brief_ref = body.get("brief_ref")
        problem = _brief_ref_problem(brief_ref)
        if problem is not None:
            return 400, {"error": problem}
        assert isinstance(brief_ref, str)
        return 202, {"brief_ref": brief_ref, "queued": self.queue.submit(brief_ref)}

    def accept_sweep(self, body: object) -> tuple[int, dict[str, Any]]:
        if body is not None and not isinstance(body, dict):
            return 400, {"error": "the body must be a JSON object"}
        try:
            waiting = list(self._waiting())
        except Exception:
            _LOG.exception("listing the waiting briefs failed")
            return 503, {"error": "the waiting briefs could not be listed"}
        queued = [brief_ref for brief_ref in waiting if self.queue.submit(brief_ref)]
        return 202, {"waiting": waiting, "queued": queued}


def _brief_ref_problem(brief_ref: object) -> str | None:
    if not isinstance(brief_ref, str):
        return "brief_ref must be a string"
    if not brief_ref.strip():
        return "brief_ref must not be blank"
    if len(brief_ref) > MAX_BRIEF_REF_LENGTH:
        return f"brief_ref must be at most {MAX_BRIEF_REF_LENGTH} characters"
    if any(ord(char) < 32 or ord(char) == 127 for char in brief_ref):
        return "brief_ref must not contain control characters"
    return None


class _BodyError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


_Answer = tuple[int, dict[str, Any], dict[str, str]]


def _read_body(handler: BaseHTTPRequestHandler) -> object:
    """The request's JSON body, ``None`` when empty; ``_BodyError`` for a refused one."""
    raw_length = handler.headers.get("Content-Length")
    if raw_length is None:
        if handler.headers.get("Transfer-Encoding"):
            raise _BodyError(411, "a body needs Content-Length")
        return None
    try:
        length = int(raw_length)
    except ValueError:
        raise _BodyError(400, "Content-Length must be a number") from None
    if length < 0:
        raise _BodyError(400, "Content-Length must not be negative")
    if length > MAX_BODY_BYTES:
        raise _BodyError(413, f"the body must be at most {MAX_BODY_BYTES} bytes")
    if length == 0:
        return None
    try:
        return json.loads(handler.rfile.read(length).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _BodyError(400, "the body must be JSON") from None


def _route(service: ProductionService, handler: BaseHTTPRequestHandler, method: str) -> _Answer:
    """Answer one request: route, method, token, body — in that order (SPEC §3)."""
    route = urlsplit(handler.path).path
    allowed = _METHODS.get(route)
    if allowed is None:
        return 404, {"error": "not found"}, {}
    if method != allowed:
        return 405, {"error": "method not allowed"}, {"Allow": allowed}
    if route == HEALTH_ROUTE:
        return 200, {"status": "ok"}, {}
    if not service.authorized(handler.headers.get("Authorization")):
        return 401, {"error": "unauthorized"}, {"WWW-Authenticate": "Bearer"}
    try:
        body = _read_body(handler)
    except _BodyError as exc:
        return exc.status, {"error": str(exc)}, {}
    accept = service.accept_brief if route == BRIEFS_ROUTE else service.accept_sweep
    status, payload = accept(body)
    return status, payload, {}


def _handler_for(service: ProductionService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "concept-factory"
        sys_version = ""

        def do_GET(self) -> None:
            self._answer(*_route(service, self, "GET"))

        def do_POST(self) -> None:
            self._answer(*_route(service, self, "POST"))

        def do_PUT(self) -> None:
            self._answer(*_route(service, self, "PUT"))

        def do_DELETE(self) -> None:
            self._answer(*_route(service, self, "DELETE"))

        def _answer(self, status: int, payload: dict[str, Any], headers: dict[str, str]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            _LOG.info("%s %s", self.address_string(), format % args)

    return Handler
