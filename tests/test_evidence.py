"""The evidence pack is the artifact that justifies merging an agent's change.

Its claims have to be true, and the one that matters most is independence: "a different model
reviewed this" is the whole argument, so the document must not assert it when it did not happen.
"""

from __future__ import annotations

import json

from drove import evidence
from drove.pipeline.schemas import BlockingIssue, PlanDoc, ReviewVerdict
from drove.pipeline.stages.verify import Check, VerifyOutcome

PLAN = PlanDoc(
    title="Add remove_item",
    summary="Mirror add_item's shape.",
    acceptance_criteria=["pytest exits 0"],
    risks=["stock could go negative"],
)

CROSS_LAB = {
    "execute": {"harness": "claude", "model": "claude-opus-5", "lab": "Anthropic"},
    "review": {"harness": "codex", "model": "gpt-5.6-terra", "lab": "OpenAI"},
}


def pack(**kw) -> evidence.Evidence:
    base = {
        "run_id": "r1", "feature_id": "f1", "iteration": 1, "intent": "add a thing",
        "branch": "dv/f1", "base": "main", "plan": PLAN, "stages": CROSS_LAB,
    }
    return evidence.Evidence(**{**base, **kw})


def test_it_names_who_built_and_who_reviewed_including_the_lab():
    """"A different model checked it" is weaker than "a model from a different lab checked it"."""
    md = pack(reviews=[ReviewVerdict(verdict="pass", summary="fine")]).to_markdown()

    assert "claude (Anthropic, claude-opus-5)" in md
    assert "codex (OpenAI, gpt-5.6-terra)" in md
    assert "different lab" in md


def test_it_refuses_to_claim_independence_when_one_harness_did_both():
    """Someone with a single CLI installed can still run; the pack must not oversell the result."""
    same = {
        "execute": {"harness": "claude", "model": "", "lab": "Anthropic"},
        "review": {"harness": "claude", "model": "", "lab": "Anthropic"},
    }
    doc = pack(stages=same, reviews=[ReviewVerdict(verdict="pass", summary="fine")])

    assert doc.independent is False
    md = doc.to_markdown()
    assert "both wrote and reviewed" in md
    assert "different lab" not in md
    assert doc.to_json()["independent_review"] is False


def test_blocking_issues_survive_into_the_document():
    md = pack(reviews=[
        ReviewVerdict(
            verdict="changes_requested", summary="not yet",
            blocking=[BlockingIssue(file="a.py", line=12, severity="major", why="off by one")],
        )
    ]).to_markdown()

    assert "a.py:12" in md
    assert "off by one" in md
    assert "changes requested" in md


def test_a_fix_round_is_narrated_not_just_listed():
    md = pack(reviews=[
        ReviewVerdict(verdict="changes_requested", summary="no", blocking=[BlockingIssue(why="x")]),
        ReviewVerdict(verdict="pass", summary="yes"),
    ]).to_markdown()

    assert "Round 1" in md and "Round 2" in md
    assert "addressed and a fresh reviewer passed" in md


def test_failing_output_gets_far_more_room_than_passing_output():
    """Recording failures is the point; a truncated traceback is useless."""
    verify = VerifyOutcome(checks=[
        Check("test", "pytest", 1, "E" * 20_000, 1.0),
        Check("lint", "ruff", 0, "P" * 20_000, 1.0),
    ])
    md = pack(verify=verify).to_markdown()

    assert md.count("E") > evidence.MAX_PASS_OUTPUT
    assert md.count("P") <= evidence.MAX_PASS_OUTPUT + 50


def test_no_verify_commands_is_stated_plainly_not_passed_over():
    """A run delivered with no checks rests on a reviewer's opinion alone; say so."""
    md = pack(verify=VerifyOutcome()).to_markdown()

    assert "No verify commands are configured" in md
    assert "reviewer's opinion alone" in md


def test_an_enormous_diff_is_truncated_with_a_way_to_read_the_rest():
    md = pack(diff="+x\n" * 80_000).to_markdown()

    assert len(md) < evidence.MAX_DIFF + 8_000
    assert "git diff main...dv/f1" in md


def test_a_cross_repo_change_says_the_branches_must_land_together():
    md = pack(head_shas={"api": "a" * 12, "web": "b" * 12}).to_markdown()

    assert "2 repositories" in md
    assert "must be merged in all of them" in md


def test_an_unknown_cost_is_explained_rather_than_shown_as_zero():
    md = pack(cost_usd=None, tokens_in=1000).to_markdown()

    assert "$0.00" not in md
    assert "subscription" in md


def test_the_json_carries_what_tooling_needs():
    doc = pack(
        reviews=[ReviewVerdict(verdict="pass", summary="fine")],
        verify=VerifyOutcome(checks=[Check("test", "pytest", 0, "ok", 0.1)]),
        head_shas={"api": "abc123abc123"},
    ).to_json()

    assert doc["independent_review"] is True
    assert doc["stages"]["review"]["lab"] == "OpenAI"
    assert doc["reviews"][0]["verdict"] == "pass"
    assert doc["verify"][0]["name"] == "test"


def test_the_review_endpoint_serves_what_the_pack_says(tmp_path, monkeypatch):
    """The app reads the run's own evidence rather than recomputing, so the two cannot drift."""
    from drove import config

    monkeypatch.setattr(config, "HOME", tmp_path)
    doc = pack(
        reviews=[
            ReviewVerdict(verdict="changes_requested", summary="no",
                          blocking=[BlockingIssue(file="a.py", line=3, severity="major", why="x")]),
            ReviewVerdict(verdict="pass", summary="yes"),
        ]
    )
    evidence.write(doc)

    written = json.loads((evidence.runs_dir(doc.run_id) / "evidence.json").read_text())
    assert written["independent_review"] is True
    assert [r["verdict"] for r in written["reviews"]] == ["changes_requested", "pass"]
    assert written["reviews"][0]["blocking"][0]["file"] == "a.py"
    assert written["stages"]["execute"]["lab"] == "Anthropic"
