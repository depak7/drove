"""Daemon HTTP surface, with jobs stubbed so nothing spends tokens."""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from drove import config, db
from drove.api import jobs, server
from drove.api.bus import Bus
from drove.vcs import git

from .conftest import make_repo


@pytest.fixture
def client(state, monkeypatch):
    server._reset_landed_cache()
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


def deliver(feature_id):
    with db.connect() as conn:
        db.set_feature_status(conn, feature_id, "delivered")


def commit_feature_file(path, name):
    (path / name).write_text("y = 2\n")
    git.git(path, "add", "-A")
    git.git(path, "commit", "-qm", f"add {name}")


# --- workspaces --------------------------------------------------------------------------


def test_repository_picker_lists_folders_and_marks_git_repositories(client, monkeypatch):
    monkeypatch.setattr(server, "_browse_root", lambda: client.projects.resolve())
    make_repo(client.projects, "api")
    (client.projects / "notes").mkdir(parents=True)

    body = client.get("/api/repos/browse").json()

    assert body["path"] == str(client.projects.resolve())
    assert [(entry["name"], entry["is_repo"]) for entry in body["entries"]] == [
        ("api", True),
        ("notes", False),
    ]


def test_repository_picker_refuses_paths_outside_its_root(client, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_browse_root", lambda: client.projects.resolve())

    response = client.get("/api/repos/browse", params={"path": str(tmp_path.resolve())})

    assert response.status_code == 403

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

    assert feature["branch"].startswith("dv/")
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


def test_a_merged_feature_is_reported_as_landed(client, monkeypatch):
    ws = new_workspace(client, repos=("api", "web"))
    feature = new_feature(client, ws)
    root = config.HOME / "worktrees" / "product" / feature["id"]
    for repo in ws["repos"]:
        commit_feature_file(root / repo["name"], f"{repo['name']}-feature.py")
        git.git(repo["path"], "merge", "--no-edit", "-q", feature["branch"])
    deliver(feature["id"])
    emitted = []
    monkeypatch.setattr(jobs, "emit", lambda *args, **kwargs: emitted.append((args, kwargs)))

    listed = client.get("/api/features").json()
    assert listed[0]["status"] == "landed"
    assert emitted == [((feature["id"], "status"), {"status": "landed"})]
    monkeypatch.setattr(
        server.trees_mod,
        "has_landed",
        lambda trees: pytest.fail("a persisted landed feature must not be checked again"),
    )
    assert client.get("/api/features").json()[0]["status"] == "landed"
    with db.connect() as conn:
        assert db.get_feature(conn, feature["id"])["status"] == "landed"


def test_a_half_merged_feature_is_not_landed(client):
    ws = new_workspace(client, repos=("api", "web"))
    feature = new_feature(client, ws)
    root = config.HOME / "worktrees" / "product" / feature["id"]
    for repo in ws["repos"]:
        commit_feature_file(root / repo["name"], f"{repo['name']}-feature.py")
    git.git(ws["repos"][0]["path"], "merge", "--no-edit", "-q", feature["branch"])
    deliver(feature["id"])

    assert client.get("/api/features").json()[0]["status"] == "delivered"


def test_only_delivered_features_are_checked_for_landing(client, monkeypatch):
    ws = new_workspace(client)
    features = [new_feature(client, ws, status) for status in (
        "planning", "needs human", "abandoned", "delivered"
    )]
    statuses = ("planning", "needs_human", "abandoned", "delivered")
    with db.connect() as conn:
        for feature, status in zip(features, statuses, strict=True):
            db.set_feature_status(conn, feature["id"], status)

    checked = []
    monkeypatch.setattr(
        server.trees_mod,
        "has_landed",
        lambda trees: checked.append(trees.root.name) or False,
    )

    assert client.get("/api/features").status_code == 200
    assert checked == [features[-1]["id"]]


def test_repeated_feature_lists_use_the_landed_cache(client, monkeypatch):
    feature = new_feature(client, new_workspace(client))
    deliver(feature["id"])
    checked = []
    monkeypatch.setattr(
        server.trees_mod,
        "has_landed",
        lambda trees: checked.append(trees.root.name) or False,
    )

    client.get("/api/features")
    client.get("/api/features")

    assert checked == [feature["id"]]


def test_the_landed_check_never_recreates_a_removed_worktree(client):
    feature = new_feature(client, new_workspace(client))
    root = config.HOME / "worktrees" / "product" / feature["id"]
    deliver(feature["id"])
    shutil.rmtree(root)

    assert client.get("/api/features").status_code == 200
    assert not root.exists()


def test_a_missing_repository_does_not_break_the_feature_list(client):
    ws = new_workspace(client)
    feature = new_feature(client, ws)
    deliver(feature["id"])
    repo = client.projects / "api"
    repo.rename(client.projects / "api-moved")

    response = client.get("/api/features")

    assert response.status_code == 200
    assert response.json()[0]["status"] == "delivered"


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
    from drove.api import bus as bus_module

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
