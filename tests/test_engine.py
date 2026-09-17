"""Engine state machine, exercised with stub stages so no tokens are spent."""

from __future__ import annotations

import subprocess

import pytest

from vorflux import config
from vorflux.config import RepoConfig
from vorflux.pipeline import engine
from vorflux.pipeline.schemas import BlockingIssue, PlanDoc, ReviewVerdict
from vorflux.pipeline.stages import review as review_stage
from vorflux.pipeline.stages.execute import ExecuteOutcome
from vorflux.pipeline.stages.verify import Check, VerifyOutcome
from vorflux.vcs import git, worktree

PLAN = PlanDoc(summary="add a thing", acceptance_criteria=["it works"])
PASS = ReviewVerdict(verdict="pass", summary="looks right")


def blocked(why="off-by-one in the retry count"):
    return ReviewVerdict(
        verdict="changes_requested",
        summary="not yet",
        blocking=[BlockingIssue(file="a.py", line=3, severity="major", why=why)],
    )


@pytest.fixture
def wt(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path / "state")
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "a@b.c"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "a.py").write_text("x = 1\n")
    git.git(repo, "add", "-A")
    git.git(repo, "commit", "-qm", "init")
    return worktree.create(repo, "task-1", "main")


@pytest.fixture
def cfg(wt):
    return RepoConfig(root=wt.repo, base_branch="main", verify={})


def stub_stages(monkeypatch, verdicts, committed=True, executes=None):
    """Replace execute/review with recorders so the state machine is what is under test."""
    calls = {"execute": [], "review": []}

    async def fake_execute(plan, wt_, cfg_, run_id, title, **kw):
        calls["execute"].append({"title": title, **kw})
        return ExecuteOutcome(
            session_id=kw.get("resume_session") or "sess-exec",
            head_sha="abc123",
            committed=committed,
            files_changed=["a.py"],
            tokens_in=10,
            tokens_out=5,
        )

    async def fake_review(plan, wt_, cfg_, run_id, attempt=1, **kw):
        calls["review"].append(attempt)
        return review_stage.ReviewOutcome(
            verdict=verdicts[attempt - 1],
            session_id=f"sess-review-{attempt}",
            harness="codex",
            tokens_in=3,
            tokens_out=1,
        )

    monkeypatch.setattr(engine, "run_execute", executes or fake_execute)
    monkeypatch.setattr(engine.review_stage, "run_review", fake_review)
    return calls


async def test_clean_review_delivers_without_a_fix_round(wt, cfg, monkeypatch):
    calls = stub_stages(monkeypatch, [PASS])
    outcome = await engine.run_cycle(PLAN, wt, cfg, "run-1", "add a thing")

    assert outcome.status == "delivered"
    assert calls["review"] == [1]
    assert len(calls["execute"]) == 1, "no fix round when nothing is blocking"


async def test_blocked_then_passing_runs_exactly_one_fix(wt, cfg, monkeypatch):
    calls = stub_stages(monkeypatch, [blocked(), PASS])
    outcome = await engine.run_cycle(PLAN, wt, cfg, "run-1", "add a thing")

    assert outcome.status == "delivered"
    assert calls["review"] == [1, 2]
    assert len(calls["execute"]) == 2
    assert calls["execute"][1]["prompt_override"], "the fix round must carry the review's issues"


async def test_persistent_disagreement_stops_for_a_human(wt, cfg, monkeypatch):
    """Two rounds, then stop. A cross-model deadlock usually means an ambiguous intent."""
    calls = stub_stages(monkeypatch, [blocked(), blocked()])
    outcome = await engine.run_cycle(PLAN, wt, cfg, "run-1", "add a thing")

    assert outcome.status == "needs_human"
    assert calls["review"] == [1, 2]
    assert len(outcome.reviews) == 2
    assert "outstanding" in outcome.note


async def test_the_implementer_keeps_its_session_across_a_fix(wt, cfg, monkeypatch):
    calls = stub_stages(monkeypatch, [blocked(), PASS])
    await engine.run_cycle(PLAN, wt, cfg, "run-1", "add a thing", resume_session="sess-0")

    first, fix = calls["execute"][0], calls["execute"][1]
    assert first["resume_session"] == "sess-0"
    assert fix["resume_session"] == first["resume_session"], (
        "the fix round must continue the implementer's conversation, not start a new one — "
        "it already knows what it wrote and what it tried"
    )


async def test_every_review_round_gets_its_own_session(wt, cfg, monkeypatch):
    """The reviewer is stateless by architecture: a resumed reviewer ratifies its own verdict."""
    stub_stages(monkeypatch, [blocked(), PASS])
    outcome = await engine.run_cycle(PLAN, wt, cfg, "run-1", "add a thing")

    reviews = {k: v for k, v in outcome.sessions.items() if k.startswith("review")}
    assert reviews == {"review-1": "sess-review-1", "review-2": "sess-review-2"}
    assert len(set(reviews.values())) == 2


async def test_no_changes_short_circuits_before_review(wt, cfg, monkeypatch):
    calls = stub_stages(monkeypatch, [PASS], committed=False)
    outcome = await engine.run_cycle(PLAN, wt, cfg, "run-1", "add a thing")

    assert outcome.status == "no_changes"
    assert calls["review"] == [], "nothing to review means nothing was spent reviewing"


async def test_failing_project_checks_block_delivery(wt, cfg, monkeypatch):
    stub_stages(monkeypatch, [PASS])
    cfg.verify = {"test": "exit 1"}
    monkeypatch.setattr(
        engine.verify_stage,
        "run_verify",
        lambda cwd, cmds: VerifyOutcome(
            checks=[Check("test", "exit 1", 1, "boom", 0.1)]
        ),
    )
    outcome = await engine.run_cycle(PLAN, wt, cfg, "run-1", "add a thing")

    assert outcome.status == "verify_failed"
    assert "test" in outcome.note


async def test_costs_and_tokens_accumulate_across_every_stage(wt, cfg, monkeypatch):
    stub_stages(monkeypatch, [blocked(), PASS])
    outcome = await engine.run_cycle(PLAN, wt, cfg, "run-1", "add a thing")

    # two executes (10 in each) + two reviews (3 in each)
    assert outcome.tokens_in == 26
    assert outcome.tokens_out == 12


async def test_changed_files_come_from_git_not_the_event_stream(wt, cfg, monkeypatch):
    """claude emits no FileChanged events; codex does. git is the one source both agree with."""
    stub_stages(monkeypatch, [PASS])
    (wt.path / "b.py").write_text("y = 2\n")
    git.git(wt.path, "add", "-A")
    git.git(wt.path, "commit", "-qm", "work")

    outcome = await engine.run_cycle(PLAN, wt, cfg, "run-1", "add a thing")
    assert outcome.files_changed == ["b.py"]
