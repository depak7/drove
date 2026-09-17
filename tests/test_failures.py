"""A failed run must be diagnosable and recoverable.

A run can fail for reasons that have nothing to do with the plan — a rate limit, a harness crash,
a network blip. Before this, the reason went to the event stream and nowhere else, so reloading
the page lost the only account of what happened, and the only way forward was to re-plan.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from drove import db, workspace
from drove.api import jobs, server

from .conftest import make_repo

PLAN = {
    "title": "Add a thing",
    "summary": "does the thing",
    "steps": [],
    "files_to_touch": [],
    "risks": [],
    "acceptance_criteria": [],
    "test_plan": "",
}


@pytest.fixture
def client(state, monkeypatch):
    # Planning is stubbed; this is about what happens when the CYCLE fails.
    monkeypatch.setattr(jobs, "start_plan", lambda *a, **k: None)
    with TestClient(server.build_app()) as c:
        c.projects = state / "projects"
        yield c


def approved_feature(client):
    path = make_repo(client.projects, "api")
    with db.connect() as conn:
        ws = workspace.create(conn, "product", [path])
    feature = client.post(
        "/api/features", json={"task": "add a thing", "workspace_id": ws.id}
    ).json()
    with db.connect() as conn:
        run = db.list_runs(conn, feature["id"])[-1]
        db.set_run_plan(conn, run["id"], PLAN)
    return feature


def settle(client, feature_id, want, timeout=10.0):
    deadline = time.time() + timeout
    body = client.get(f"/api/features/{feature_id}").json()
    while time.time() < deadline and body["status"] != want:
        time.sleep(0.05)
        body = client.get(f"/api/features/{feature_id}").json()
    while time.time() < deadline and jobs.is_busy(feature_id):
        time.sleep(0.05)
    return body


def test_a_crashed_run_records_why_and_stays_readable(client, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("claude exited 1: rate limit exceeded")

    monkeypatch.setattr(jobs, "run_cycle", boom)
    feature = approved_feature(client)

    client.post(f"/api/features/{feature['id']}/approve")
    body = settle(client, feature["id"], "failed")

    assert body["status"] == "failed"
    assert "rate limit exceeded" in body["error"], "the reason must survive the event stream"

    # And on the run itself, so the Runs screen can show it too.
    run = client.get(f"/api/workspaces/{body['workspace_id']}/runs").json()[0]
    assert run["status"] == "failed"
    assert "rate limit" in run["error"]


def test_a_failed_run_can_be_retried_without_replanning(client, monkeypatch):
    """Re-planning to recover from a rate limit wastes a planning call and the executor session."""
    async def boom(*a, **k):
        raise RuntimeError("transient")

    monkeypatch.setattr(jobs, "run_cycle", boom)
    feature = approved_feature(client)
    client.post(f"/api/features/{feature['id']}/approve")
    settle(client, feature["id"], "failed")

    started: list[str] = []
    monkeypatch.setattr(jobs, "start_cycle", lambda fid: started.append(fid))
    monkeypatch.setattr(jobs, "start_plan", lambda *a, **k: pytest.fail("must not re-plan"))

    assert client.post(f"/api/features/{feature['id']}/retry").status_code == 200
    assert started == [feature["id"]]


def test_retry_needs_a_plan(client):
    path = make_repo(client.projects, "api")
    with db.connect() as conn:
        ws = workspace.create(conn, "product", [path])
    feature = client.post("/api/features", json={"task": "x", "workspace_id": ws.id}).json()

    response = client.post(f"/api/features/{feature['id']}/retry")
    assert response.status_code == 400
    assert "no approved plan" in response.json()["detail"]


def test_a_busy_feature_reports_a_conflict_not_a_server_error(client, monkeypatch):
    """The status flips before the job's future settles, so a fast retry lands in that window."""
    monkeypatch.setattr(jobs, "is_busy", lambda _: True)
    feature = approved_feature(client)

    assert client.post(f"/api/features/{feature['id']}/retry").status_code == 409


def test_the_recorded_log_is_replayable_after_the_stream_is_gone(client):
    """The live view exists only while a tab is open; the run's JSONL is written for this."""
    from drove.config import runs_dir

    feature = approved_feature(client)
    run_id = client.get(f"/api/features/{feature['id']}").json()["latest_run_id"]

    directory = runs_dir(run_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "execute.jsonl").write_text(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"reading the code"}]}}\n'
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit",'
        '"input":{"file_path":"a.py"}}]}}\n'
    )

    body = client.get(f"/api/features/{feature['id']}/log").json()
    assert body["run_id"] == run_id
    lines = [line["text"] for stage in body["stages"] for line in stage["lines"]]
    assert any("reading the code" in line for line in lines)
    assert any("Edit" in line for line in lines)


def test_a_run_with_no_recording_says_so_rather_than_erroring(client):
    feature = approved_feature(client)
    body = client.get(f"/api/features/{feature['id']}/log").json()
    assert body["stages"] == []
