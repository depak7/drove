"""Multi-repo feature trees: isolation, the teardown gate, and landed detection across repos."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from drove import config, db, workspace
from drove.vcs import git, tree


def make_repo(root: Path, name: str, default_branch: str = "main") -> Path:
    path = root / name
    path.mkdir(parents=True)
    for args in (
        ["init", "-q", "-b", default_branch],
        ["config", "user.email", "a@b.c"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    (path / f"{name}.py").write_text("x = 1\n")
    git.git(path, "add", "-A")
    git.git(path, "commit", "-qm", "init")
    return path


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path / "state")
    api = make_repo(tmp_path / "projects", "api")
    web = make_repo(tmp_path / "projects", "web")
    with db.connect() as conn:
        created = workspace.create(conn, "product", [api, web])
    return created


def commit_in(path: Path, name: str, text: str = "y = 2\n") -> None:
    (path / name).write_text(text)
    git.git(path, "add", "-A")
    git.git(path, "commit", "-qm", f"add {name}")


def test_every_repo_gets_a_worktree_under_one_feature_root(ws):
    trees = tree.create(ws, "task-1")

    assert len(trees) == 2
    assert {t.repo.name for t in trees} == {"api", "web"}
    # Siblings under one root, so the agent can change both in a single change.
    assert {t.path.parent for t in trees} == {trees.root}
    assert all(t.branch == "dv/task-1" for t in trees)


def test_repos_stay_isolated_from_each_other(ws):
    trees = tree.create(ws, "task-1")
    api, web = trees.by_name("api"), trees.by_name("web")

    commit_in(api.path, "feature.py")
    assert not (web.path / "feature.py").exists()
    assert not (api.repo.path / "feature.py").exists(), "the user's checkout is untouched"


def test_only_touched_repos_appear_in_the_diff(ws):
    trees = tree.create(ws, "task-1")
    commit_in(trees.by_name("api").path, "feature.py")

    assert [t.repo.name for t in tree.touched(trees)] == ["api"]
    diff = tree.combined_diff(trees)
    assert "repo: api" in diff
    assert "repo: web" not in diff, "an untouched repo must not clutter the review"


def test_changed_files_are_repo_qualified_when_several_repos_exist(ws):
    trees = tree.create(ws, "task-1")
    commit_in(trees.by_name("api").path, "feature.py")
    commit_in(trees.by_name("web").path, "page.py")

    assert sorted(tree.changed_files(trees)) == ["api/feature.py", "web/page.py"]


def test_teardown_refuses_per_repo_independently(ws):
    """One repo's work being merged is no reason to delete another's."""
    trees = tree.create(ws, "task-1")
    api, web = trees.by_name("api"), trees.by_name("web")
    commit_in(api.path, "feature.py")
    commit_in(web.path, "page.py")
    git.git(api.repo.path, "merge", "--no-edit", "-q", api.branch)

    results = tree.teardown(trees)
    assert results["api"].removed, results["api"].reason
    assert not results["web"].removed
    assert "not in main" in results["web"].reason
    assert web.path.exists()
    assert trees.root.exists()


def test_teardown_refuses_uncommitted_work_per_repo(ws):
    trees = tree.create(ws, "task-1")
    api = trees.by_name("api")
    (api.path / "scratch.txt").write_text("half-written agent edit")

    result = tree.teardown(trees)["api"]

    assert not result.removed
    assert "uncommitted" in result.reason
    assert api.path.exists()


def test_landed_requires_every_changed_repo_to_be_merged(ws):
    """Half-merged is not delivered: calling it done is how the other half gets forgotten."""
    trees = tree.create(ws, "task-1")
    api, web = trees.by_name("api"), trees.by_name("web")
    commit_in(api.path, "feature.py")
    commit_in(web.path, "page.py")
    assert not tree.has_landed(trees)

    git.git(api.repo.path, "merge", "--no-edit", "-q", api.branch)
    assert not tree.has_landed(trees), "half-merged is not landed"

    git.git(web.repo.path, "merge", "--no-edit", "-q", web.branch)
    assert tree.has_landed(trees)


def test_a_repo_with_a_different_default_branch_is_detected(tmp_path, monkeypatch):
    """Cutting branches from a `main` that does not exist fails in a confusing way."""
    monkeypatch.setattr(config, "HOME", tmp_path / "state")
    legacy = make_repo(tmp_path / "projects", "legacy", default_branch="master")
    with db.connect() as conn:
        ws = workspace.create(conn, "mixed", [legacy])

    assert ws.repos[0].base_branch == "master"
    trees = tree.create(ws, "task-1")
    assert trees.trees[0].base == "master"


def test_create_is_idempotent_so_a_feature_can_be_pivoted(ws):
    first = tree.create(ws, "task-1")
    commit_in(first.by_name("api").path, "feature.py")

    again = tree.create(ws, "task-1")
    assert (again.by_name("api").path / "feature.py").exists()


def test_attach_describes_absent_worktrees_without_creating_them(ws):
    created = tree.create(ws, "task-1")
    expected = [(t.repo.name, t.path, t.branch) for t in created]
    tree.teardown(created)

    attached = tree.attach(ws, "task-1")

    assert [(t.repo.name, t.path, t.branch) for t in attached] == expected
    assert not attached.root.exists()


def test_landed_can_be_checked_after_worktrees_are_removed(ws):
    trees = tree.create(ws, "task-1")
    for name, filename in (("api", "feature.py"), ("web", "page.py")):
        worktree = trees.by_name(name)
        commit_in(worktree.path, filename)
        git.git(worktree.repo.path, "merge", "--no-edit", "-q", worktree.branch)
        git.git(worktree.repo.path, "worktree", "remove", str(worktree.path))
    trees.root.rmdir()

    attached = tree.attach(ws, "task-1")

    assert tree.has_landed(attached)
    assert not attached.root.exists(), "landed detection must not recreate reclaimed worktrees"


def test_teardown_removes_the_empty_feature_root(ws):
    trees = tree.create(ws, "task-1")
    for name, filename in (("api", "feature.py"), ("web", "page.py")):
        worktree = trees.by_name(name)
        commit_in(worktree.path, filename)
        git.git(worktree.repo.path, "merge", "--no-edit", "-q", worktree.branch)

    results = tree.teardown(trees)

    assert all(result.removed for result in results.values())
    assert not trees.root.exists()


def test_paths_never_escape_the_state_root(ws):
    """slug() already neutralises `..`; the assertion is the second layer, not the only one."""
    root = (config.HOME / "worktrees").resolve()
    for hostile in ("../../../.ssh/authorized_keys", "/etc/passwd", "..", "a/../../x"):
        assert ws.feature_root(hostile).is_relative_to(root)


def test_refuses_a_worktree_nested_inside_a_repository(ws, monkeypatch):
    api = ws.repos[0]
    monkeypatch.setattr(config, "HOME", api.path / ".state")

    with pytest.raises(tree.WorktreeError, match="inside the repository"):
        tree.create(ws, "task-1")


def test_an_explicit_branch_overrides_the_current_prefix(ws):
    """Features created before the product was renamed keep the branch their commits are on.

    The prefix is a constant, so changing it would otherwise strand every existing feature: the
    worktree would be cut on a new branch while its work sat on the old one.
    """
    trees = tree.create(ws, "task-1", "vf/task-1")
    assert all(t.branch == "vf/task-1" for t in trees)

    commit_in(trees.by_name("api").path, "legacy.py")
    assert git.branch_exists(trees.by_name("api").repo.path, "vf/task-1")

    # Reopening with the same recorded branch finds the same work.
    again = tree.create(ws, "task-1", "vf/task-1")
    assert (again.by_name("api").path / "legacy.py").exists()
