"""Daemon HTTP surface, with jobs stubbed so nothing spends tokens."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vorflux import config
from vorflux.api import jobs, server
from vorflux.api.bus import Bus
from vorflux.vcs import git

from .conftest import make_repo


@pytest.fixture
def client(state, monkeypatch):
    started: list[tuple[str, tuple]] = []
    monkeypatch.setattr(jobs, "start_plan", lambda *a, **k: started.append(("plan", a)))
    monkeypatch.setattr(jobs, "start_cycle", lambda *a, **k: started.append(("cycle", a)))
    with TestClient(server.build_app()) as c:
        c.started = started
        c.projects = state / "projects"
        yield c


def new_workspace(client, name="product", repos=("api",)):
    paths = [str(make_repo(client.projects, r)) for r in repos]
    return client.post("/api/workspaces", json={"name": name, "repos": paths}).json()


def new_feature(client, ws, task="add a thing"):
    return client.post("/api/features", json={"task": task, "workspace_id": ws["id"]}).json()


# --- workspaces --------------------------------------------------------------------------

def test_a_workspace_is_created_with_its_repos(client):
    ws = new_workspace(client, repos=("api", "web"))

    assert ws["name"] == "product"
    assert [r["name"] for r in ws["repos"]] == ["api", "web"]
    assert all(r["base_branch"] == "main" for r in ws["repos"])
    assert ws["independent_review"] is True


def test_repos_are_added_and_removed_from_the_app(client):
    """The whole point: you add a repo from the UI, not by restarting a daemon."""
    ws = client.post("/api/workspaces", json={"name": "product"}).json()
    assert ws["repos"] == []

    path = str(make_repo(client.projects, "api"))
    ws = client.post(f"/api/workspaces/{ws['id']}/repos", json={"path": path}).json()
    assert [r["name"] for r in ws["repos"]] == ["api"]

    ws = client.request(
        "DELETE", f"/api/workspaces/{ws['id']}/repos", params={"path": path}
    ).json()
    assert ws["repos"] == []


def test_a_repo_with_a_different_default_branch_is_detected(client):
    ws = client.post("/api/workspaces", json={"name": "legacy"}).json()
    path = str(make_repo(client.projects, "old", default_branch="master"))
    ws = client.post(f"/api/workspaces/{ws['id']}/repos", json={"path": path}).json()

    assert ws["repos"][0]["base_branch"] == "master"


def test_adding_a_non_repository_is_a_clear_400(client):
    ws = client.post("/api/workspaces", json={"name": "product"}).json()
    plain = client.projects / "not-a-repo"
    plain.mkdir(parents=True)

    response = client.post(f"/api/workspaces/{ws['id']}/repos", json={"path": str(plain)})
    assert response.status_code == 400
    assert "not a git repository" in response.json()["detail"]


def test_a_workspace_with_features_is_not_deleted(client):
    """Deleting it would orphan branches holding real work."""
    ws = new_workspace(client)
    new_feature(client, ws)

    response = client.delete(f"/api/workspaces/{ws['id']}")
    assert response.status_code == 400
    assert "orphaned" in response.json()["detail"]


def test_planning_needs_a_repo_in_the_workspace(client):
    ws = client.post("/api/workspaces", json={"name": "empty"}).json()
    response = client.post("/api/features", json={"task": "x", "workspace_id": ws["id"]})

    assert response.status_code == 400
    assert "no repositories" in response.json()["detail"]


# --- features ----------------------------------------------------------------------------

def test_creating_a_feature_makes_a_worktree_per_repo_and_starts_planning(client):
    ws = new_workspace(client, repos=("api", "web"))
    feature = new_feature(client, ws)

    assert feature["branch"].startswith("vf/")
    assert feature["workspace_id"] == ws["id"]
    assert client.started[0][0] == "plan"

    root = config.HOME / "worktrees" / "product" / feature["id"]
    assert sorted(p.name for p in root.iterdir() if p.is_dir()) == ["api", "web"]


def test_features_are_listed_per_workspace(client):
    a = new_workspace(client, name="alpha", repos=("api",))
    b = new_workspace(client, name="beta", repos=("web",))
    new_feature(client, a, "in alpha")
    new_feature(client, b, "in beta")

    assert len(client.get("/api/features").json()) == 2
    scoped = client.get("/api/features", params={"workspace_id": a["id"]}).json()
    assert [f["title"] for f in scoped] == ["in alpha"]


def test_approve_starts_the_cycle(client):
    feature = new_feature(client, new_workspace(client))
    client.started.clear()

    body = client.post(f"/api/features/{feature['id']}/approve").json()
    assert body["status"] in ("approved", "executing")
    assert client.started[0][0] == "cycle"


def test_revise_replans_rather_than_executing(client):
    feature = new_feature(client, new_workspace(client))
    client.started.clear()

    client.post(f"/api/features/{feature['id']}/revise", json={"feedback": "use a token bucket"})
    assert client.started[0][0] == "plan", "feedback must never start a run"


def test_pivot_appends_a_run_to_the_same_feature(client):
    feature = new_feature(client, new_workspace(client))
    client.post(f"/api/features/{feature['id']}/pivot", json={"intent": "use PKCE"})

    body = client.get(f"/api/features/{feature['id']}").json()
    assert body["iterations"] == 2
    assert body["runs"][1]["intent"] == "use PKCE"


def test_decline_removes_an_untouched_feature(client):
    feature = new_feature(client, new_workspace(client))
    body = client.post(f"/api/features/{feature['id']}/decline").json()

    assert body["removed"] is True
    assert client.get("/api/features").json() == []


def test_decline_keeps_repos_that_hold_work_and_says_which(client):
    ws = new_workspace(client, repos=("api", "web"))
    feature = new_feature(client, ws)
    api = config.HOME / "worktrees" / "product" / feature["id"] / "api"
    (api / "new.py").write_text("y = 2\n")
    git.git(api, "add", "-A")
    git.git(api, "commit", "-qm", "agent work")

    body = client.post(f"/api/features/{feature['id']}/decline").json()
    assert body["removed"] is False
    assert set(body["kept"]) == {"api"}, "the untouched repo is cleaned up regardless"
    assert "not in main" in body["kept"]["api"]["reason"]
    assert client.get(f"/api/features/{feature['id']}").json()["status"] == "abandoned"


def test_diff_reports_each_changed_repo(client):
    ws = new_workspace(client, repos=("api", "web"))
    feature = new_feature(client, ws)
    api = config.HOME / "worktrees" / "product" / feature["id"] / "api"
    (api / "api.py").write_text("x = 2\n")
    git.git(api, "add", "-A")
    git.git(api, "commit", "-qm", "change")

    body = client.get(f"/api/features/{feature['id']}/diff").json()
    assert [r["name"] for r in body["repos"]] == ["api"], "untouched repos stay out of review"
    assert "-x = 1" in body["diff"] and "+x = 2" in body["diff"]
    assert "repo: api" in body["diff"]


def test_unknown_feature_is_a_404_not_a_500(client):
    assert client.get("/api/features/nope").status_code == 404


def test_health_reports_harness_availability(client):
    body = client.get("/api/health").json()
    assert "claude" in body["harnesses"]
    assert body["active"] == []


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
        drained = [queue.get_nowait()["n"] for _ in range(queue.qsize())]
        assert drained[-1] == bus_module.QUEUE_LIMIT + 49


async def test_unsubscribed_queues_stop_receiving():
    bus = Bus()
    with bus.subscribe() as queue:
        pass
    bus.publish({"kind": "x"})
    assert queue.empty()
