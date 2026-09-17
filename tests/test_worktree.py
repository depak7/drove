"""Worktree isolation and the teardown gate.

These tests exist because deleted work is the one failure in this system with no undo. An
autonomous tool that eats your afternoon once is a tool you never leave running unattended again.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vorflux import config
from vorflux.vcs import git, worktree


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(config, "HOME", tmp_path / "state")
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "a@b.c"],
        ["config", "user.name", "test"],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (root / "app.txt").write_text("v1\n")
    git.git(root, "add", "-A")
    git.git(root, "commit", "-qm", "init")
    return root


def commit_in(path: Path, name: str, text: str) -> None:
    (path / name).write_text(text)
    git.git(path, "add", "-A")
    git.git(path, "commit", "-qm", f"add {name}")


# --- path safety ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "hostile",
    ["../../../.ssh/authorized_keys", "../../escape", "/etc/passwd", "..", "a/../../../../x"],
)
def test_path_never_escapes_the_worktrees_root(repo, hostile):
    """Feature ids can come from model output; a title like 'Fix ../legacy/ parser' is enough."""
    path = worktree.path_for(repo, hostile)
    assert path.is_relative_to((config.HOME / "worktrees").resolve())


# --- isolation -----------------------------------------------------------------------------

def test_worktrees_are_isolated_but_share_history(repo):
    a = worktree.create(repo, "task-1", "main")
    b = worktree.create(repo, "task-2", "main")
    assert a.path != b.path

    commit_in(a.path, "feature_a.txt", "a")
    commit_in(b.path, "feature_b.txt", "b")

    assert (a.path / "feature_a.txt").exists()
    assert not (a.path / "feature_b.txt").exists(), "worktrees must not see each other's files"
    assert not (repo / "feature_a.txt").exists(), "the user's own checkout stays untouched"

    # …but both commits live in the one shared object store.
    assert git.commits_ahead(repo, "main", a.branch)
    assert git.commits_ahead(repo, "main", b.branch)


def test_create_is_idempotent_so_a_feature_can_be_pivoted(repo):
    first = worktree.create(repo, "task-1", "main")
    commit_in(first.path, "x.txt", "x")
    again = worktree.create(repo, "task-1", "main")
    assert again.path == first.path
    assert (again.path / "x.txt").exists(), "pivot must find the earlier work still there"


# --- the teardown gate ---------------------------------------------------------------------

def test_refuses_teardown_with_uncommitted_changes(repo):
    wt = worktree.create(repo, "task-1", "main")
    (wt.path / "scratch.txt").write_text("half-written agent edit")

    result = worktree.teardown(wt)
    assert not result.removed
    assert "uncommitted" in result.reason
    assert wt.path.exists()


def test_refuses_teardown_with_unmerged_commits(repo):
    wt = worktree.create(repo, "task-1", "main")
    commit_in(wt.path, "feature.txt", "work")

    result = worktree.teardown(wt)
    assert not result.removed
    assert "not in main" in result.reason
    assert result.recovery and "worktree remove" in result.recovery
    assert wt.path.exists(), "an afternoon of agent work must survive cleanup"
    assert git.branch_exists(repo, wt.branch)


def test_removes_only_once_work_is_integrated(repo):
    wt = worktree.create(repo, "task-1", "main")
    commit_in(wt.path, "feature.txt", "work")
    git.git(repo, "merge", "--no-edit", "-q", wt.branch)

    result = worktree.teardown(wt)
    assert result.removed, result.reason
    assert not wt.path.exists()
    assert not git.branch_exists(repo, wt.branch), "branch -d is safe once merged"


def test_teardown_of_an_empty_worktree_is_allowed(repo):
    """A declined plan leaves nothing behind, so cleanup must not need a human."""
    wt = worktree.create(repo, "task-1", "main")
    assert worktree.teardown(wt).removed


# --- landed detection & drift ----------------------------------------------------------------

def test_has_landed_flips_when_the_branch_merges(repo):
    wt = worktree.create(repo, "task-1", "main")
    commit_in(wt.path, "feature.txt", "work")
    assert not worktree.has_landed(wt)

    git.git(repo, "merge", "--no-edit", "-q", wt.branch)
    assert worktree.has_landed(wt), "the teardown check doubles as the merge signal"


def test_drift_counts_commits_base_gained_since_branching(repo):
    wt = worktree.create(repo, "task-1", "main")
    commit_in(wt.path, "feature.txt", "work")
    assert worktree.drift(wt) == 0

    commit_in(repo, "hotfix.txt", "urgent")
    commit_in(repo, "hotfix2.txt", "urgent")
    assert worktree.drift(wt) == 2, "surfaced to the user; never rebased automatically"


def test_prune_clears_stale_registrations(repo):
    wt = worktree.create(repo, "task-1", "main")
    subprocess.run(["rm", "-rf", str(wt.path)], check=True)
    worktree.prune(repo)
    assert str(wt.path) not in git.git(repo, "worktree", "list")
