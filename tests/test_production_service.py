"""Tests for the HTTP production service n8n calls (ADR-0049).

Maps PRODUCTION_SERVICE_ACCEPTANCE.md (SVC). The service runs for real on ``127.0.0.1`` (port 0)
and is called over a real socket with ``urllib``; ``produce`` and ``waiting`` are recording doubles
that can be held back by an event or made to raise. Nothing leaves ``127.0.0.1``.
"""

from __future__ import annotations

import http.client
import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from omemo_content_factory.composition import build_production_service
from omemo_content_factory.infrastructure.production_service import (
    BRIEFS_ROUTE,
    HEALTH_ROUTE,
    SWEEP_ROUTE,
    ProductionService,
    ServiceConfigurationError,
    ServiceSettings,
    service_settings_from_env,
)

TOKEN = "t" * 16 + "0123456789abcdef-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LOGGER = "omemo_content_factory.infrastructure.production_service"


@dataclass
class Worker:
    """Records every brief produced; ``hold`` keeps an invocation running until released."""

    produced: list[str] = field(default_factory=list)
    started: threading.Event = field(default_factory=threading.Event)
    hold: threading.Event | None = None
    fail_on: str | None = None
    waiting_briefs: list[str] = field(default_factory=list)
    waiting_fails: bool = False

    def produce(self, brief_ref: str) -> None:
        self.produced.append(brief_ref)
        self.started.set()
        if self.hold is not None:
            self.hold.wait(5)
        if brief_ref == self.fail_on:
            raise RuntimeError(f"boom on {brief_ref}")

    def waiting(self) -> list[str]:
        if self.waiting_fails:
            raise OSError("store unreadable")
        return list(self.waiting_briefs)


@dataclass
class Running:
    service: ProductionService
    worker: Worker
    url: str

    def call(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any], http.client.HTTPMessage]:
        request = urllib.request.Request(
            self.url + path, data=body, headers=headers or {}, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read()), response.headers
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read()), error.headers  # type: ignore[return-value]

    def brief(self, ref: object, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        body = json.dumps({"brief_ref": ref}).encode()
        status, payload, _ = self.call(
            "POST", BRIEFS_ROUTE, body, AUTH if headers is None else headers
        )
        return status, payload


@pytest.fixture
def running() -> Iterator[Running]:
    worker = Worker()
    service = ProductionService(
        ServiceSettings(token=TOKEN, host="127.0.0.1", port=0),
        produce=worker.produce,
        waiting=worker.waiting,
    )
    service.start()
    host, port = service.address
    try:
        yield Running(service, worker, f"http://{host}:{port}")
    finally:
        if worker.hold is not None:
            worker.hold.set()
        service.stop()


# --- SVC-01/02 ------------------------------------------------------------------------------


def test_svc_01_health_needs_no_token_and_touches_nothing(running: Running) -> None:
    status, payload, _ = running.call("GET", HEALTH_ROUTE)

    assert (status, payload) == (200, {"status": "ok"})
    assert running.worker.produced == []


def test_svc_02_a_brief_is_accepted_at_once_and_produced_once(running: Running) -> None:
    status, payload = running.brief("page-1")

    assert (status, payload) == (202, {"brief_ref": "page-1", "queued": True})
    assert running.service.wait_idle(5)
    assert running.worker.produced == ["page-1"]


# --- SVC-03 ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token-of-a-reasonable-length-0000"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": f"bearer {TOKEN}"},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": TOKEN},
    ],
    ids=["none", "wrong", "basic", "lowercase-scheme", "extra-char", "no-scheme"],
)
@pytest.mark.parametrize("route", [BRIEFS_ROUTE, SWEEP_ROUTE])
def test_svc_03_a_request_without_the_token_is_refused_and_nothing_happens(
    running: Running, headers: dict[str, str], route: str, caplog: pytest.LogCaptureFixture
) -> None:
    running.worker.waiting_briefs = ["page-1"]
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        status, payload, response_headers = running.call(
            "POST", route, json.dumps({"brief_ref": "page-1"}).encode(), headers
        )

    assert status == 401
    assert response_headers["WWW-Authenticate"] == "Bearer"
    assert running.service.wait_idle(5)
    assert running.worker.produced == []
    sent = headers.get("Authorization", "")
    assert TOKEN not in json.dumps(payload)
    if sent:
        assert sent not in caplog.text
    assert TOKEN not in caplog.text


# --- SVC-04/05 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1, 2]",
        b"{}",
        b'{"brief_ref": 7}',
        b'{"brief_ref": "   "}',
        json.dumps({"brief_ref": "x" * 513}).encode(),
        json.dumps({"brief_ref": "page\n1"}).encode(),
        b"\xff\xfe",
    ],
    ids=["not-json", "not-object", "missing", "number", "blank", "too-long", "control", "not-utf8"],
)
def test_svc_04_a_malformed_body_is_refused(running: Running, body: bytes) -> None:
    status, payload, _ = running.call("POST", BRIEFS_ROUTE, body, AUTH)

    assert status == 400
    assert "error" in payload
    assert running.service.wait_idle(5)
    assert running.worker.produced == []


def test_svc_04_the_longest_allowed_ref_is_accepted(running: Running) -> None:
    assert running.brief("x" * 512)[0] == 202


def test_svc_04_a_body_over_the_limit_is_refused(running: Running) -> None:
    body = json.dumps({"brief_ref": "page-1", "pad": "x" * 20_000}).encode()

    status, _, _ = running.call("POST", BRIEFS_ROUTE, body, AUTH)

    assert status == 413
    assert running.worker.produced == []


def test_svc_04_a_chunked_body_without_content_length_is_refused(running: Running) -> None:
    host, port = running.service.address
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request(
            "POST", BRIEFS_ROUTE, body=iter([b'{"brief_ref": "page-1"}']), headers=AUTH
        )
        response = connection.getresponse()
        assert response.status == 411
        response.read()
    finally:
        connection.close()
    assert running.worker.produced == []


def test_svc_05_unknown_paths_and_wrong_methods(running: Running) -> None:
    assert running.call("GET", "/v1/unknown")[0] == 404
    assert running.call("POST", "/", b"{}", AUTH)[0] == 404

    status, _, headers = running.call("GET", BRIEFS_ROUTE, headers=AUTH)
    assert (status, headers["Allow"]) == (405, "POST")
    status, _, headers = running.call("POST", HEALTH_ROUTE, b"{}")
    assert (status, headers["Allow"]) == (405, "GET")

    status, payload, headers = running.call("GET", HEALTH_ROUTE + "?probe=1")
    assert (status, payload) == (200, {"status": "ok"})
    assert headers["Content-Type"] == "application/json; charset=utf-8"


# --- SVC-06/07 ------------------------------------------------------------------------------


def test_svc_06_a_waiting_brief_is_coalesced_and_a_running_one_is_queued_again(
    running: Running,
) -> None:
    worker = running.worker
    worker.hold = threading.Event()

    assert running.brief("A")[1]["queued"] is True
    assert worker.started.wait(5)
    assert running.brief("A")[1]["queued"] is True
    assert running.brief("A")[1]["queued"] is False
    assert running.brief("B")[1]["queued"] is True
    worker.hold.set()

    assert running.service.wait_idle(5)
    assert worker.produced == ["A", "A", "B"]


def test_svc_07_a_failing_invocation_is_logged_and_the_worker_goes_on(
    running: Running, caplog: pytest.LogCaptureFixture
) -> None:
    running.worker.fail_on = "A"

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        running.brief("A")
        assert running.service.wait_idle(5)
        running.brief("B")
        assert running.service.wait_idle(5)

    assert running.worker.produced == ["A", "B"]
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "A" in record.getMessage()
    assert record.exc_info is not None


# --- SVC-08 ---------------------------------------------------------------------------------


def test_svc_08_a_sweep_queues_the_waiting_briefs_not_already_queued(running: Running) -> None:
    worker = running.worker
    worker.hold = threading.Event()
    running.brief("busy")
    assert worker.started.wait(5)
    running.brief("A")
    worker.waiting_briefs = ["A", "B"]

    status, payload, _ = running.call("POST", SWEEP_ROUTE, headers=AUTH)

    assert (status, payload) == (202, {"waiting": ["A", "B"], "queued": ["B"]})
    worker.hold.set()
    assert running.service.wait_idle(5)
    assert worker.produced == ["busy", "A", "B"]
    assert running.call("POST", SWEEP_ROUTE, b"{}", AUTH)[0] == 202


def test_svc_08_a_sweep_that_cannot_list_is_503_and_queues_nothing(running: Running) -> None:
    running.worker.waiting_fails = True

    status, payload, _ = running.call("POST", SWEEP_ROUTE, headers=AUTH)

    assert status == 503
    assert payload == {"error": "the waiting briefs could not be listed"}
    assert running.worker.produced == []
    assert running.call("POST", SWEEP_ROUTE, b"[1]", AUTH)[0] == 400


# --- SVC-09 ---------------------------------------------------------------------------------


def test_svc_09_settings_come_from_the_environment() -> None:
    assert service_settings_from_env(
        {
            "OMEMO_SERVICE_TOKEN": f"  {TOKEN} ",
            "OMEMO_SERVICE_HOST": "0.0.0.0",
            "OMEMO_SERVICE_PORT": "9000",
        }
    ) == ServiceSettings(token=TOKEN, host="0.0.0.0", port=9000)
    assert service_settings_from_env({"OMEMO_SERVICE_TOKEN": TOKEN}) == ServiceSettings(
        token=TOKEN, host="127.0.0.1", port=8765
    )
    assert TOKEN not in repr(ServiceSettings(token=TOKEN))


@pytest.mark.parametrize(
    ("environ", "variable"),
    [
        ({}, "OMEMO_SERVICE_TOKEN"),
        ({"OMEMO_SERVICE_TOKEN": "  "}, "OMEMO_SERVICE_TOKEN"),
        ({"OMEMO_SERVICE_TOKEN": "short-token-31-characters-long!"}, "OMEMO_SERVICE_TOKEN"),
        ({"OMEMO_SERVICE_TOKEN": TOKEN, "OMEMO_SERVICE_PORT": "http"}, "OMEMO_SERVICE_PORT"),
        ({"OMEMO_SERVICE_TOKEN": TOKEN, "OMEMO_SERVICE_PORT": "70000"}, "OMEMO_SERVICE_PORT"),
        ({"OMEMO_SERVICE_TOKEN": TOKEN, "OMEMO_SERVICE_PORT": "-1"}, "OMEMO_SERVICE_PORT"),
    ],
    ids=["missing", "blank", "short", "not-a-number", "too-high", "negative"],
)
def test_svc_09_bad_settings_are_named_without_values(
    environ: dict[str, str], variable: str
) -> None:
    with pytest.raises(ServiceConfigurationError, match=variable) as refused:
        service_settings_from_env(environ)
    for value in environ.values():
        if value.strip():
            assert value not in str(refused.value)


# --- SVC-10/11 ------------------------------------------------------------------------------


def test_svc_10_stop_finishes_the_running_invocation_and_drops_the_queue(
    running: Running,
) -> None:
    worker = running.worker
    worker.hold = threading.Event()
    running.brief("A")
    assert worker.started.wait(5)
    running.brief("B")
    url = running.url

    stopping = threading.Thread(target=running.service.stop)
    stopping.start()
    while not running.service.queue.closed:
        threading.Event().wait(0.01)
    worker.hold.set()
    stopping.join(5)

    assert not stopping.is_alive()
    assert worker.produced == ["A"]
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(url + HEALTH_ROUTE, timeout=2)
    running.service.stop()


def test_svc_11_the_root_builds_the_service_over_a_brief_production(tmp_path: Path) -> None:
    calls: list[str] = []

    class Production:
        def invoke(self, brief_ref: str) -> None:
            calls.append(brief_ref)

        def waiting_briefs(self) -> tuple[str, ...]:
            return ("waiting",)

    production: Any = Production()
    service = build_production_service(
        {"OMEMO_SERVICE_TOKEN": TOKEN, "OMEMO_SERVICE_PORT": "0"}, production
    )
    service.start()
    host, port = service.address
    try:
        request = urllib.request.Request(
            f"http://{host}:{port}{SWEEP_ROUTE}", data=b"", headers=AUTH, method="POST"
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert json.loads(response.read()) == {"waiting": ["waiting"], "queued": ["waiting"]}
        assert service.wait_idle(5)
    finally:
        service.stop()
    assert calls == ["waiting"]

    with pytest.raises(ServiceConfigurationError):
        build_production_service({}, production)
