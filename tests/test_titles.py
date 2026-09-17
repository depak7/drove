"""Feature titles: readable immediately, replaced by the planner's own."""

from __future__ import annotations

from drove.config import provisional_title
from drove.pipeline.schemas import PlanDoc

PASTED = """Surface the "landed" state for features whose branches have been merged.

drove/vcs/tree.py already has a tested has_landed() function and nothing calls it, so a feature
that I have merged sits in the UI as "delivered" forever. Wire it up so the API reports it.
"""


def test_a_pasted_paragraph_becomes_a_readable_title():
    """Every row showing the same wall of text makes the feature list unusable."""
    title = provisional_title(PASTED)

    assert len(title) <= 63
    assert "\n" not in title
    assert title.startswith("Surface the")


def test_a_short_request_is_left_alone():
    assert provisional_title("fix the login bug") == "fix the login bug"


def test_the_first_clause_wins_when_there_is_one():
    text = "Add rate limiting to the API, and update the dashboard, and write tests for both"
    assert provisional_title(text) == "Add rate limiting to the API"


def test_whitespace_is_collapsed():
    assert provisional_title("  add   \n  caching  ") == "add caching"


def test_empty_input_still_yields_something_displayable():
    assert provisional_title("") == "Untitled"
    assert provisional_title("   ") == "Untitled"


def test_a_long_unbroken_request_is_cut_on_a_word():
    title = provisional_title("Refactor " + "the authentication subsystem " * 8)
    assert len(title) <= 63
    assert title.endswith("…")
    assert not title[:-1].endswith(" ")


def test_the_planner_can_name_the_work_itself():
    """Its title beats anything derived from the raw request, because it read the code."""
    plan = PlanDoc.model_validate({"title": "Surface landed features", "summary": "…"})
    assert plan.title == "Surface landed features"


def test_a_plan_without_a_title_is_still_valid():
    """An older plan, or a model that skipped the field, must not fail the stage."""
    assert PlanDoc.model_validate({"summary": "…"}).title == ""
