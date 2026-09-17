"""Replay a recorded Claude Code run through the adapter.

The fixture is a real `claude -p --output-format stream-json` session, trimmed. Replaying it keeps
adapter regressions cheap to catch: no network, no tokens, no CLI required.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vorflux.events import (
    AssistantText,
    RateLimit,
    Result,
    SessionStarted,
    ToolCall,
    ToolResult,
    Usage,
)
from vorflux.harness.base import InvokeSpec
from vorflux.harness.claude_code import ClaudeCodeHarness
from vorflux.pipeline.schemas import PlanDoc, json_schema

FIXTURE = Path(__file__).parent / "fixtures" / "claude_plan.jsonl"


def replay() -> list:
    harness = ClaudeCodeHarness()
    events = []
    for line in FIXTURE.read_text().splitlines():
        events.extend(harness.parse(json.loads(line)))
    return events


def test_session_start_is_first_and_carries_identity():
    events = replay()
    assert isinstance(events[0], SessionStarted)
    assert events[0].session_id
    assert events[0].model
    assert events[0].cwd


def test_every_event_kind_is_covered():
    kinds = {type(e) for e in replay()}
    for expected in (SessionStarted, RateLimit, ToolCall, ToolResult, AssistantText, Usage, Result):
        assert expected in kinds, f"{expected.__name__} never produced"


def test_terminal_result_is_last_and_structured():
    events = replay()
    result = events[-1]
    assert isinstance(result, Result)
    assert result.ok
    assert result.cost_usd is not None and result.cost_usd > 0, "claude reports real cost"
    assert result.tokens_in > 0 and result.cache_read_tokens > 0
    assert result.structured is not None, "--json-schema output must parse into an object"
    PlanDoc.model_validate(result.structured)


def test_rate_limit_utilisation_is_extracted():
    rl = next(e for e in replay() if isinstance(e, RateLimit))
    assert rl.status
    assert rl.five_hour_utilization is not None


def test_usage_snapshots_are_non_negative():
    for u in (e for e in replay() if isinstance(e, Usage)):
        assert u.input_tokens >= 0 and u.output_tokens >= 0


# --- argv construction -------------------------------------------------------------------

def spec(**kw) -> InvokeSpec:
    base = {"prompt": "do the thing", "cwd": Path("/tmp")}
    return InvokeSpec(**{**base, **kw})


def test_readonly_mode_strips_write_tools():
    argv = ClaudeCodeHarness().build_argv(spec(mode="readonly"))
    assert "--disallowed-tools" in argv
    for tool in ("Edit", "Write", "NotebookEdit"):
        assert tool in argv
    assert argv[-1] == "do the thing", "prompt must be the trailing positional"


def test_write_mode_keeps_edit_tools():
    argv = ClaudeCodeHarness().build_argv(spec(mode="write"))
    assert "--disallowed-tools" not in argv


def test_schema_is_passed_inline_not_as_a_path():
    schema = json_schema(PlanDoc)
    argv = ClaudeCodeHarness().build_argv(spec(output_schema=schema))
    value = argv[argv.index("--json-schema") + 1]
    # A path here fails at runtime with "--json-schema is not valid JSON".
    assert json.loads(value)["properties"]["summary"]


@pytest.mark.parametrize("requested", [1, 2])
def test_structured_output_never_runs_with_one_turn(requested):
    """--max-turns 1 with a schema exits error_max_turns before emitting the object."""
    argv = ClaudeCodeHarness().build_argv(
        spec(output_schema=json_schema(PlanDoc), max_turns=requested)
    )
    assert int(argv[argv.index("--max-turns") + 1]) >= 2


def test_session_id_is_assigned_not_resumed_by_default():
    argv = ClaudeCodeHarness().build_argv(spec(session_id="abc"))
    assert "--session-id" in argv and "--resume" not in argv

    resumed = ClaudeCodeHarness().build_argv(spec(session_id="abc", resume=True))
    assert "--resume" in resumed and "--session-id" not in resumed
