"""Daemon HTTP surface, with jobs stubbed so nothing spends tokens."""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from vorflux import config
from vorflux.api import jobs, server
from vorflux.api.bus import Bus
from vorflux.vcs import git


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path / "state")
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "a@b.c"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "a.py").write_text("x = 1\n")
    (root / ".vorflux.toml").write_text('base_branch = "main"\n')
    git.git(root, "add", "-A")
    git.git(root, "commit", "-qm", "init")
    monkeypatch.setattr(server, "REPO", root)
    return root


@pytest.fixture
def client(repo, monkeypatch):
    started: list[tuple[str, tuple]] = []
    monkeypatch.setattr(jobs, "start_plan", lambda *a, **k: started.append(("plan", a)))
    monkeypatch.setattr(jobs, "start_cycle", lambda *a, **k: started.append(("cycle", a)))
    with TestClient(server.build_app()) as c:
        c.started = started
        yield c


def test_health_reports_whether_review_is_independent(client):
    body = client.get("/api/health").json()
    assert body["repo"]
    assert body["stages"]["execute"] == "claude"
    assert body["independent_review"] is (body["stages"]["review"] != body["stages"]["execute"])


def test_creating_a_feature_makes_a_worktree_and_starts_planning(client):
    body = client.post("/api/features", json={"task": "add a thing"}).json()

    assert body["status"] == "planning" or body["status"] == "awaiting_approval"
    assert body["branch"].startswith("vf/")
    assert client.started[0][0] == "plan"

    listed = client.get("/api/features").json()
    assert [f["id"] for f in listed] == [body["id"]]


def test_approve_starts_the_cycle_and_records_the_decision(client):
    feature = client.post("/api/features", json={"task": "add a thing"}).json()
    client.started.clear()

    body = client.post(f"/api/features/{feature['id']}/approve").json()
    assert body["status"] in ("approved", "executing")
    assert client.started[0][0] == "cycle"


def test_revise_replans_rather_than_executing(client):
    feature = client.post("/api/features", json={"task": "add a thing"}).json()
    client.started.clear()

    client.post(f"/api/features/{feature['id']}/revise", json={"feedback": "use a token bucket"})
    assert client.started[0][0] == "plan", "feedback must never start a run"


def test_pivot_appends_a_run_to_the_same_feature(client):
    feature = client.post("/api/features", json={"task": "add a thing"}).json()
    client.post(f"/api/features/{feature['id']}/pivot", json={"intent": "use PKCE"})

    body = client.get(f"/api/features/{feature['id']}").json()
    assert body["iterations"] == 2
    assert [r["iteration"] for r in body["runs"]] == [1, 2]
    assert body["runs"][1]["intent"] == "use PKCE"


def test_decline_removes_an_untouched_feature(client):
    feature = client.post("/api/features", json={"task": "add a thing"}).json()
    body = client.post(f"/api/features/{feature['id']}/decline").json()

    assert body["removed"] is True
    assert client.get("/api/features").json() == []


def test_decline_preserves_a_feature_that_has_commits(client, repo):
    feature = client.post("/api/features", json={"task": "add a thing"}).json()
    worktree_path = config.HOME / "worktrees" / "repo" / feature["id"]
    (worktree_path / "new.py").write_text("y = 2\n")
    git.git(worktree_path, "add", "-A")
    git.git(worktree_path, "commit", "-qm", "agent work")

    body = client.post(f"/api/features/{feature['id']}/decline").json()
    assert body["removed"] is False
    assert "not in main" in body["reason"]
    assert client.get(f"/api/features/{feature['id']}").json()["status"] == "abandoned"


def test_unknown_feature_is_a_404_not_a_500(client):
    assert client.get("/api/features/nope").status_code == 404


def test_worktree_errors_become_actionable_400s(client, monkeypatch):
    """A WorktreeError carries a diagnosis and a fix; a 500 stack trace throws both away."""
    from vorflux.vcs.worktree import WorktreeError

    def boom(*a, **k):
        raise WorktreeError("worktree would live inside the repository; set VORFLUX_HOME elsewhere")

    monkeypatch.setattr(server.worktree, "create", boom)
    response = client.post("/api/features", json={"task": "x"})
    assert response.status_code == 400
    assert "VORFLUX_HOME" in response.json()["detail"]


def test_diff_endpoint_reports_the_branch_change(client):
    feature = client.post("/api/features", json={"task": "add a thing"}).json()
    worktree_path = config.HOME / "worktrees" / "repo" / feature["id"]
    (worktree_path / "a.py").write_text("x = 2\n")
    git.git(worktree_path, "add", "-A")
    git.git(worktree_path, "commit", "-qm", "change")

    body = client.get(f"/api/features/{feature['id']}/diff").json()
    assert "-x = 1" in body["diff"] and "+x = 2" in body["diff"]


# --- the bus ------------------------------------------------------------------------------

async def test_subscribers_receive_published_events():
    bus = Bus()
    with bus.subscribe() as queue:
        bus.publish({"kind": "status", "status": "planning"})
        assert (await queue.get())["status"] == "planning"


async def test_a_stalled_subscriber_never_blocks_the_engine():
    """A backgrounded tab must not apply back-pressure to a run; drop its oldest events instead."""
    from vorflux.api import bus as bus_module

    bus = Bus()
    with bus.subscribe() as queue:
        for i in range(bus_module.QUEUE_LIMIT + 50):
            bus.publish({"kind": "tool", "n": i})

        assert queue.qsize() == bus_module.QUEUE_LIMIT
        # The oldest were dropped, so what remains ends at the newest.
        drained = [queue.get_nowait()["n"] for _ in range(queue.qsize())]
        assert drained[-1] == bus_module.QUEUE_LIMIT + 49


async def test_unsubscribed_queues_stop_receiving():
    bus = Bus()
    with bus.subscribe() as queue:
        pass
    bus.publish({"kind": "x"})
    assert queue.empty()
