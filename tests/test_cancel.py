"""Cancelling stops live work, records why, and leaves the feature recoverable."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from drove import db, workspace
from drove.api import jobs, server
from drove.harness.base import stream_jsonl_process
from drove.pipeline.stages import verify

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
    monkeypatch.setattr(jobs, "start_plan", lambda *a, **k: None)
    with TestClient(server.build_app()) as test_client:
        test_client.projects = state / "projects"
        yield test_client


def approved_feature(client):
    path = make_repo(client.projects, "api")
    with db.connect() as conn:
        ws = workspace.create(conn, "product", [path])
    feature = client.post(
        "/api/features", json={"task": "add a thing", "workspace_id": ws.id}
    ).json()
    with db.connect() as conn:
        run = db.latest_run(conn, feature["id"])
        db.set_run_plan(conn, run["id"], PLAN)
    return feature


def settle(client, feature_id, want, timeout=10.0):
    deadline = time.monotonic() + timeout
    body = client.get(f"/api/features/{feature_id}").json()
    while time.monotonic() < deadline and (body["status"] != want or jobs.is_busy(feature_id)):
        time.sleep(0.05)
        body = client.get(f"/api/features/{feature_id}").json()
    return body


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_cancel_records_the_stopped_stage_on_feature_and_run(client, monkeypatch):
    started = threading.Event()

    async def wait_forever(*args, **kwargs):
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(jobs, "run_cycle", wait_forever)
    feature = approved_feature(client)

    assert client.post(f"/api/features/{feature['id']}/approve").status_code == 200
    assert started.wait(2)
    assert jobs.is_busy(feature["id"])
    assert client.post(f"/api/features/{feature['id']}/cancel").status_code == 200

    body = settle(client, feature["id"], "cancelled")
    assert body["status"] == "cancelled"
    assert "while it was implementing" in body["error"]
    assert not jobs.is_busy(feature["id"])

    run = client.get(f"/api/workspaces/{body['workspace_id']}/runs").json()[0]
    assert run["status"] == "cancelled"
    assert run["ended_at"] is not None
    assert run["duration_s"] is not None
    assert "while it was implementing" in run["error"]


def test_cancel_records_a_job_queued_behind_the_concurrency_limit(client, monkeypatch):
    monkeypatch.setattr(jobs, "_semaphore", asyncio.Semaphore(0))

    async def must_not_start(*args, **kwargs):
        pytest.fail("a queued cycle must not start")

    monkeypatch.setattr(jobs, "run_cycle", must_not_start)
    feature = approved_feature(client)

    assert client.post(f"/api/features/{feature['id']}/approve").status_code == 200
    assert jobs.is_busy(feature["id"])
    assert client.post(f"/api/features/{feature['id']}/cancel").status_code == 200

    body = settle(client, feature["id"], "cancelled")
    assert body["status"] == "cancelled"
    assert "before it started work" in body["error"]


def test_cancel_when_nothing_is_running_is_a_conflict(client):
    feature = approved_feature(client)

    response = client.post(f"/api/features/{feature['id']}/cancel")

    assert response.status_code == 409
    assert "nothing is running" in response.json()["detail"]


def test_a_cancelled_feature_with_a_plan_retries_the_cycle(client, monkeypatch):
    feature = approved_feature(client)
    with db.connect() as conn:
        db.set_feature_status(conn, feature["id"], "cancelled")
    started: list[str] = []
    monkeypatch.setattr(jobs, "start_cycle", started.append)

    assert client.post(f"/api/features/{feature['id']}/retry").status_code == 200
    assert started == [feature["id"]]


def test_a_cancelled_feature_without_a_plan_replans_its_original_intent(client, monkeypatch):
    path = make_repo(client.projects, "api")
    with db.connect() as conn:
        ws = workspace.create(conn, "product", [path])
    feature = client.post(
        "/api/features", json={"task": "add oauth login", "workspace_id": ws.id}
    ).json()
    with db.connect() as conn:
        db.set_feature_status(conn, feature["id"], "cancelled")
    planned: list[tuple[str, str]] = []
    monkeypatch.setattr(jobs, "start_plan", lambda fid, intent: planned.append((fid, intent)))

    assert client.post(f"/api/features/{feature['id']}/retry").status_code == 200
    assert planned == [(feature["id"], "add oauth login")]


async def test_cancelling_harness_stream_kills_its_process_group(tmp_path):
    pid_file = tmp_path / "child.pid"
    script = f'trap "" TERM; sleep 30 & echo $! > "{pid_file}"; wait'

    async def consume():
        async for _ in stream_jsonl_process(["sh", "-c", script], tmp_path, lambda _: []):
            pass

    task = asyncio.create_task(consume())
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_file.exists()
    child_pid = int(pid_file.read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1.5)

    for _ in range(50):
        if not process_exists(child_pid):
            break
        await asyncio.sleep(0.01)
    assert not process_exists(child_pid)


async def test_cancelling_async_verify_kills_the_check_process(tmp_path, monkeypatch):
    pid_file = tmp_path / "verify.pid"
    later_command = tmp_path / "later-command"
    later_repo = tmp_path / "later-repo"
    command = f'sh -c \'trap "" TERM; echo $$ > "{pid_file}"; sleep 30\''
    repo = SimpleNamespace(
        name="api",
        config=SimpleNamespace(
            verify={"test": command, "lint": f'touch "{later_command}"'}
        ),
    )
    tree = SimpleNamespace(path=tmp_path, repo=repo)
    other_repo = SimpleNamespace(
        name="web",
        config=SimpleNamespace(verify={"test": f'touch "{later_repo}"'}),
    )
    other_tree = SimpleNamespace(path=tmp_path, repo=other_repo)
    monkeypatch.setattr(verify, "TIMEOUT_SECONDS", 30)
    monkeypatch.setattr("drove.vcs.tree.touched", lambda trees: trees)

    task = asyncio.create_task(verify.run_all_async([tree, other_tree], SimpleNamespace()))
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_file.exists()
    child_pid = int(pid_file.read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1.5)

    for _ in range(50):
        if not process_exists(child_pid):
            break
        await asyncio.sleep(0.01)
    assert not process_exists(child_pid)
    await asyncio.sleep(0.1)  # let the worker observe the stop event after communicate() returns
    assert not later_command.exists()
    assert not later_repo.exists()


# --- reaching a run in another process -----------------------------------------------------

def test_a_run_owned_by_another_process_is_asked_to_stop(client, monkeypatch):
    """`drove execute` in a terminal is invisible to the daemon's job table but still reachable.

    Not by signalling it: a CLI streaming a harness ignores SIGINT to its pid and to its process
    group alike, and runs to completion. The request is recorded and the owner acts on it.
    """
    feature = approved_feature(client)
    with db.connect() as conn:
        run = db.latest_run(conn, feature["id"])
        conn.execute("UPDATE runs SET owner_pid = ? WHERE id = ?", (999_001, run["id"]))
    monkeypatch.setattr(db, "process_alive", lambda pid: pid == 999_001)

    assert client.post(f"/api/features/{feature['id']}/cancel").status_code == 200
    assert db.cancel_requested(run["id"]) is True


def test_a_run_whose_owner_is_gone_is_not_cancellable(client, monkeypatch):
    """Nothing is running, so there is nothing to stop — the startup sweep retires it instead."""
    feature = approved_feature(client)
    with db.connect() as conn:
        run = db.latest_run(conn, feature["id"])
        conn.execute("UPDATE runs SET owner_pid = ? WHERE id = ?", (999_002, run["id"]))
    monkeypatch.setattr(db, "process_alive", lambda pid: False)

    assert client.post(f"/api/features/{feature['id']}/cancel").status_code == 409
    assert db.cancel_requested(run["id"]) is False


def test_starting_a_run_clears_a_request_meant_for_an_earlier_attempt(client):
    """Otherwise a retry stops the instant it first looks at the flag."""
    feature = approved_feature(client)
    with db.connect() as conn:
        run = db.latest_run(conn, feature["id"])
        db.request_cancel(conn, run["id"])
    assert db.cancel_requested(run["id"]) is True

    with db.connect() as conn:
        db.claim_run(conn, run["id"])

    assert db.cancel_requested(run["id"]) is False
