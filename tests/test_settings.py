"""Per-stage harness and model selection, end to end from config to argv."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from drove import db, workspace
from drove.api import jobs, server
from drove.harness import registry
from drove.harness.base import InvokeSpec

from .conftest import make_repo


@pytest.fixture
def client(state, monkeypatch):
    monkeypatch.setattr(jobs, "start_plan", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "start_cycle", lambda *a, **k: None)
    with TestClient(server.build_app()) as c:
        c.projects = state / "projects"
        yield c


def a_workspace(client):
    path = str(make_repo(client.projects, "api"))
    return client.post("/api/workspaces", json={"name": "product", "repos": [path]}).json()


# --- config round trip ------------------------------------------------------------------

def test_stage_config_is_saved_and_returned(client):
    ws = a_workspace(client)
    body = client.put(
        f"/api/workspaces/{ws['id']}/settings",
        json={"harness": {"review": "opencode"}, "models": {"plan": "opus"}},
    ).json()

    assert body["harness"]["review"] == "opencode"
    assert body["models"]["plan"] == "opus"
    # Unspecified stages keep their previous value rather than resetting to the default.
    assert body["harness"]["execute"] == "claude"


def test_clearing_a_model_means_let_the_cli_decide(client):
    """An empty string from a <select> is a choice: 'no pin', not 'never configured'."""
    ws = a_workspace(client)
    client.put(f"/api/workspaces/{ws['id']}/settings", json={"models": {"plan": "opus"}})
    body = client.put(f"/api/workspaces/{ws['id']}/settings", json={"models": {"plan": ""}}).json()

    assert body["models"]["plan"] is None


def test_a_harness_with_no_adapter_is_rejected(client):
    ws = a_workspace(client)
    response = client.put(
        f"/api/workspaces/{ws['id']}/settings", json={"harness": {"review": "gemini"}}
    )
    assert response.status_code == 400
    assert "gemini" in response.json()["detail"]


def test_review_sharing_the_execute_harness_is_flagged_not_blocked(client):
    """Someone with one CLI installed must still be able to run; they just get told."""
    ws = a_workspace(client)
    body = client.put(
        f"/api/workspaces/{ws['id']}/settings", json={"harness": {"review": "claude"}}
    ).json()

    assert body["harness"]["review"] == "claude"
    assert body["independent_review"] is False


# --- the part that actually matters: does the choice reach the CLI? ---------------------

def test_the_chosen_model_reaches_the_command_line(state):
    """Config that never becomes an argument is config that does nothing."""
    with db.connect() as conn:
        ws = workspace.create(conn, "product", [make_repo(state / "projects", "api")])
        db.set_workspace_config(
            conn, ws.id, dict(ws.harness), {"plan": "opus", "review": "gpt-5.6-terra"}
        )
        ws = workspace.get(conn, ws.id)

    assert ws.model_for("plan") == "opus"
    assert ws.model_for("execute") is None  # unset means the CLI decides

    claude = registry.get("claude")
    argv = claude.build_argv(InvokeSpec(prompt="x", cwd=Path("/repo"), model=ws.model_for("plan")))
    assert argv[argv.index("--model") + 1] == "opus"

    codex = registry.get("codex")
    argv = codex.build_argv(InvokeSpec(prompt="x", cwd=Path("/repo"), model=ws.model_for("review")))
    assert argv[argv.index("-m") + 1] == "gpt-5.6-terra"


def test_no_model_flag_is_sent_when_none_is_chosen(state):
    """Pinning a model by default is how a workspace breaks when a provider retires an id."""
    with db.connect() as conn:
        ws = workspace.create(conn, "product", [make_repo(state / "projects", "api")])

    for name, flag in (("claude", "--model"), ("codex", "-m"), ("opencode", "-m")):
        argv = registry.get(name).build_argv(
            InvokeSpec(prompt="x", cwd=Path("/repo"), model=ws.model_for("execute"))
        )
        assert flag not in argv, f"{name} should not pin a model by default"


# --- what the picker can honestly offer -------------------------------------------------

def test_model_lists_are_enumerated_or_known_never_invented():
    """opencode can list; the others cannot, so the UI falls back to free text."""
    assert registry.list_models("claude") == ["opus", "sonnet", "haiku"]
    assert registry.list_models("nope") == []


def test_harness_endpoint_reports_what_each_one_can_do(client):
    rows = {h["name"]: h for h in client.get("/api/harnesses").json()}

    assert set(rows) == set(registry.PRESETS)
    assert rows["opencode"]["supports_schema"] is False, "schema rides in the prompt there"
    assert rows["claude"]["supports_schema"] is True
