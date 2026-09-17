"""Execute-stage mechanics that need no live harness."""

from __future__ import annotations

from vorflux.pipeline.schemas import PlanDoc, PlanStep
from vorflux.pipeline.stages import execute
from vorflux.vcs import git
from vorflux.vcs import tree as trees_mod

PLAN = PlanDoc(
    summary="`test_calc.py` imports subtract, but calc.py does not define it, so collection fails.",
    steps=[PlanStep(title="Add subtract()", files=["calc.py"], detail="mirror add()")],
    acceptance_criteria=["pytest passes"],
    test_plan="python -m pytest -q",
)


def test_plan_file_lands_outside_every_repo(solo):
    """It must be unable to reach a commit, so it lives at the feature root, not in a worktree."""
    trees = trees_mod.create(solo, "task-1")
    path = execute.write_plan_file(trees.root, PLAN)

    assert path.exists() and "Add subtract()" in path.read_text()
    for t in trees:
        assert not path.is_relative_to(t.path)
        assert not git.is_dirty(t.path)


def test_commit_uses_the_users_words_not_the_plan_summary(solo):
    """plan.summary is explanatory prose; its first line is a terrible commit subject."""
    trees = trees_mod.create(solo, "task-1")
    (trees.by_name("api").path / "api.py").write_text("x = 2\n")

    shas = execute.commit(trees, "Implement the missing subtract function")
    assert set(shas) == {"api"}

    subject = git.git(trees.by_name("api").path, "log", "-1", "--format=%s")
    assert subject == "Implement the missing subtract function"
    assert "test_calc.py" not in subject


def test_commit_subject_is_truncated_and_single_line(solo):
    trees = trees_mod.create(solo, "task-1")
    (trees.by_name("api").path / "api.py").write_text("x = 2\n")
    execute.commit(trees, "a\nb   c" + " very long tail" * 20)

    subject = git.git(trees.by_name("api").path, "log", "-1", "--format=%s")
    assert len(subject) <= 72 and "\n" not in subject


def test_commit_is_a_no_op_when_nothing_changed(solo):
    trees = trees_mod.create(solo, "task-1")
    assert execute.commit(trees, "no changes") == {}


def test_each_iteration_commits_under_its_own_intent(solo):
    """A pivot's commit must describe the pivot, not the feature it belongs to."""
    trees = trees_mod.create(solo, "task-1")
    path = trees.by_name("api").path

    (path / "api.py").write_text("def divide(a, b):\n    return a // b\n")
    execute.commit(trees, "Add a divide(a, b) function")
    (path / "api.py").write_text("def divide(a, b):\n    return a / b\n")
    execute.commit(trees, "Switch divide back to true division")

    assert git.git(path, "log", "-2", "--format=%s").splitlines() == [
        "Switch divide back to true division",
        "Add a divide(a, b) function",
    ]


def test_a_cross_repo_change_commits_in_each_repo_under_one_subject(duo):
    """The shared subject is how a reviewer recognises the branches as one change."""
    trees = trees_mod.create(duo, "task-1")
    (trees.by_name("api").path / "api.py").write_text("def v2(): ...\n")
    (trees.by_name("web").path / "web.py").write_text("from api import v2\n")

    shas = execute.commit(trees, "Move callers to the v2 endpoint")
    assert set(shas) == {"api", "web"}
    assert len(set(shas.values())) == 2, "separate repos, separate commits"

    for t in trees:
        assert git.git(t.path, "log", "-1", "--format=%s") == "Move callers to the v2 endpoint"


def test_untouched_repos_are_not_committed(duo):
    trees = trees_mod.create(duo, "task-1")
    (trees.by_name("api").path / "api.py").write_text("y = 2\n")

    assert set(execute.commit(trees, "only the api")) == {"api"}


def test_the_agent_is_granted_every_worktree(duo):
    """cwd is the feature root so repos are siblings; each worktree is granted explicitly."""
    trees = trees_mod.create(duo, "task-1")
    prompt = execute.render_prompt(PLAN, trees)

    assert "api/" in prompt and "web/" in prompt
    assert str(trees.root) in prompt


def test_context_threshold_decides_resume_versus_handoff():
    window = 200_000
    assert execute.should_resume(10_000, 20_000, window)
    assert not execute.should_resume(60_000, 80_000, window)
    # Cache reads occupy the window even though they are cheap to send.
    assert not execute.should_resume(1_000, 130_000, window)
