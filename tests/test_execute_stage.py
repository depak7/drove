"""Execute-stage mechanics that do not need a live harness."""

from __future__ import annotations

import subprocess

import pytest

from vorflux import config
from vorflux.pipeline.schemas import PlanDoc, PlanStep
from vorflux.pipeline.stages import execute
from vorflux.vcs import git, worktree

PLAN = PlanDoc(
    summary="`test_calc.py` imports subtract, but calc.py does not define it, so collection fails.",
    steps=[PlanStep(title="Add subtract()", files=["calc.py"], detail="mirror add()")],
    acceptance_criteria=["pytest passes"],
    test_plan="python -m pytest -q",
)


@pytest.fixture
def wt(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path / "state")
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "a@b.c"],
        ["config", "user.name", "test"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git.git(repo, "add", "-A")
    git.git(repo, "commit", "-qm", "init")
    return worktree.create(repo, "task-1", "main")


def test_plan_file_lands_in_the_worktree_and_is_gitignored(wt):
    """The agent can re-read its instructions after a compaction drops them from context."""
    path = execute.write_plan_file(wt, PLAN)
    assert path.exists()
    assert "Add subtract()" in path.read_text()
    assert not git.is_dirty(wt.path), ".vorflux must never show up as a change to commit"


def test_commit_uses_the_users_words_not_the_plan_summary(wt):
    """plan.summary is an explanatory paragraph; its first line is a terrible commit subject."""
    (wt.path / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n"
    )
    sha, committed = execute.commit(wt, "Implement the missing subtract function")

    assert committed and sha
    subject = git.git(wt.path, "log", "-1", "--format=%s")
    assert subject == "Implement the missing subtract function"
    assert "test_calc.py" not in subject


def test_commit_subject_is_truncated_and_single_line(wt):
    (wt.path / "calc.py").write_text("x = 1\n")
    execute.commit(wt, "a\nb   c" + " very long tail" * 20)
    subject = git.git(wt.path, "log", "-1", "--format=%s")
    assert len(subject) <= 72
    assert "\n" not in subject


def test_commit_is_a_no_op_when_the_agent_changed_nothing(wt):
    before = git.head_sha(wt.path)
    sha, committed = execute.commit(wt, "no changes")
    assert not committed
    assert sha == before


def test_context_threshold_decides_resume_versus_handoff():
    """Below the line resume and keep the agent's memory; above it, hand off deliberately."""
    window = 200_000
    assert execute.should_resume(10_000, 20_000, window)
    assert not execute.should_resume(60_000, 80_000, window)
    # cache reads count: they occupy the window even though they are cheap to send.
    assert not execute.should_resume(1_000, 130_000, window)


def test_each_iteration_commits_under_its_own_intent(wt):
    """A pivot's commit must describe the pivot, not the feature it belongs to."""
    (wt.path / "calc.py").write_text("def divide(a, b):\n    return a // b\n")
    execute.commit(wt, "Add a divide(a, b) function")

    (wt.path / "calc.py").write_text("def divide(a, b):\n    return a / b\n")
    execute.commit(wt, "Switch divide back to true division")

    subjects = git.git(wt.path, "log", "-2", "--format=%s").splitlines()
    assert subjects == ["Switch divide back to true division", "Add a divide(a, b) function"]
