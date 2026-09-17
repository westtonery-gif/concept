"""The committed n8n workflows are transport only (ADR-0049 §4).

Maps PRODUCTION_SERVICE_ACCEPTANCE.md (N8N). The workflows live in ``n8n/`` as n8n exports; these
tests read them as data and pin that each one is a single trigger connected to a single HTTP Request
that calls one of the service's routes with the header credential — no code, branching, data or
write nodes, and no secret in the file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from omemo_content_factory.infrastructure.production_service import (
    BRIEFS_ROUTE,
    SWEEP_ROUTE,
    TOKEN_VAR,
)

N8N = Path(__file__).resolve().parent.parent / "n8n"
BRIEF_READY = N8N / "brief-ready.workflow.json"
REVIEW_SWEEP = N8N / "review-sweep.workflow.json"
TRIGGERS = {"n8n-nodes-base.notionTrigger", "n8n-nodes-base.scheduleTrigger"}
HTTP = "n8n-nodes-base.httpRequest"
CREDENTIAL = "Concept factory service"


def _load(path: Path) -> dict[str, Any]:
    workflow = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _split(workflow: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    [trigger] = [node for node in workflow["nodes"] if node["type"] in TRIGGERS]
    [request] = [node for node in workflow["nodes"] if node["type"] == HTTP]
    return trigger, request


def test_n8n_01_exactly_the_two_workflows_are_committed_inactive() -> None:
    assert sorted(N8N.glob("*.workflow.json")) == [BRIEF_READY, REVIEW_SWEEP]
    for path in (BRIEF_READY, REVIEW_SWEEP):
        workflow = _load(path)
        assert {"name", "nodes", "connections", "active"} <= set(workflow)
        assert workflow["active"] is False


@pytest.mark.parametrize("path", [BRIEF_READY, REVIEW_SWEEP], ids=lambda p: p.name)
def test_n8n_02_a_workflow_is_one_trigger_connected_to_one_request(path: Path) -> None:
    workflow = _load(path)

    assert len(workflow["nodes"]) == 2
    trigger, request = _split(workflow)
    assert workflow["connections"] == {
        trigger["name"]: {"main": [[{"node": request["name"], "type": "main", "index": 0}]]}
    }


@pytest.mark.parametrize(
    ("path", "route"),
    [(BRIEF_READY, BRIEFS_ROUTE), (REVIEW_SWEEP, SWEEP_ROUTE)],
    ids=["brief", "sweep"],
)
def test_n8n_03_the_request_posts_to_a_service_route_with_the_header_credential(
    path: Path, route: str
) -> None:
    _, request = _split(_load(path))
    parameters = request["parameters"]

    assert parameters["method"] == "POST"
    assert urlsplit(parameters["url"]).path == route
    assert parameters["authentication"] == "genericCredentialType"
    assert parameters["genericAuthType"] == "httpHeaderAuth"
    assert request["credentials"] == {"httpHeaderAuth": {"name": CREDENTIAL}}
    assert "sendHeaders" not in parameters and "headerParameters" not in parameters

    text = path.read_text(encoding="utf-8")
    assert "Bearer" not in text and "Authorization" not in text
    without_node_ids = re.sub(r'"id": "[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}"', "", text)
    placeholder_free = without_node_ids.replace("REPLACE_WITH_NOTION_DATABASE_ID", "")
    assert not re.search(r"[A-Za-z0-9_\-]{32,}", placeholder_free)


def test_n8n_04_brief_ready_forwards_every_updated_page_id() -> None:
    trigger, request = _split(_load(BRIEF_READY))

    assert trigger["type"] == "n8n-nodes-base.notionTrigger"
    assert trigger["parameters"]["event"] == "pagedUpdatedInDatabase"
    assert trigger["parameters"]["pollTimes"] == {"item": [{"mode": "everyMinute"}]}
    parameters = request["parameters"]
    assert (parameters["sendBody"], parameters["specifyBody"]) == (True, "keypair")
    assert parameters["bodyParameters"] == {
        "parameters": [{"name": "brief_ref", "value": "={{ $json.id }}"}]
    }


def test_n8n_04_review_sweep_runs_every_five_minutes_without_a_body() -> None:
    trigger, request = _split(_load(REVIEW_SWEEP))

    assert trigger["type"] == "n8n-nodes-base.scheduleTrigger"
    assert trigger["parameters"]["rule"] == {
        "interval": [{"field": "minutes", "minutesInterval": 5}]
    }
    assert "sendBody" not in request["parameters"]


def test_n8n_05_the_readme_names_the_files_the_credential_and_the_token_variable() -> None:
    readme = (N8N / "README.md").read_text(encoding="utf-8")

    for needle in (BRIEF_READY.name, REVIEW_SWEEP.name, CREDENTIAL, TOKEN_VAR):
        assert needle in readme
