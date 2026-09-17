"""Replay a recorded `opencode run --format json` run through the adapter."""

from __future__ import annotations

import json
from pathlib import Path

from vorflux.events import AssistantText, SessionStarted, ToolCall, ToolResult, Usage
from vorflux.harness.base import InvokeSpec
from vorflux.harness.opencode import OpenCodeHarness
from vorflux.harness.util import extract_json_object

FIXTURE = Path(__file__).parent / "fixtures" / "opencode_edit.jsonl"


def rows() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text().splitlines()]


def replay() -> list:
    h = OpenCodeHarness()
    out = []
    for row in rows():
        out.extend(h.parse(row))
    return out


def spec(**kw) -> InvokeSpec:
    return InvokeSpec(**{"prompt": "do it", "cwd": Path("/repo"), **kw})


def test_session_id_is_recovered_from_events():
    starts = [e for e in replay() if isinstance(e, SessionStarted)]
    assert starts and starts[0].session_id.startswith("ses_")


def test_tool_use_yields_call_and_result_together():
    events = replay()
    call = next(e for e in events if isinstance(e, ToolCall))
    result = next(e for e in events if isinstance(e, ToolResult))
    assert call.name and call.tool_id
    assert result.tool_id == call.tool_id
    assert not result.is_error


def test_text_events_become_assistant_text():
    assert any(isinstance(e, AssistantText) and e.text for e in replay())


def test_usage_ignores_the_misleading_total_field():
    """`tokens.total` looks cumulative but is not — it is a per-step sum including cache reads.

    total == input + output + reasoning + cache.read for that same step. It rises across steps
    only because the context grows, so summing it counts the cached prefix once per step.
    """
    steps = [r["part"]["tokens"] for r in rows() if r.get("type") == "step_finish"]
    assert len(steps) > 1

    for tok in steps:
        components = tok["input"] + tok["output"] + tok["reasoning"] + tok["cache"]["read"]
        assert tok["total"] == components, "total is a per-step sum, not a running total"

    usages = [e for e in replay() if isinstance(e, Usage)]
    assert [u.input_tokens for u in usages] == [t["input"] for t in steps]
    assert [u.cache_read_tokens for u in usages] == [t["cache"]["read"] for t in steps]

    phantom = sum(t["total"] for t in steps) - sum(
        u.input_tokens + u.output_tokens + u.reasoning_tokens + u.cache_read_tokens
        for u in usages
    )
    assert phantom == 0, "our accounting must not invent or lose tokens"


def test_write_mode_grants_auto_readonly_does_not():
    assert "--auto" in OpenCodeHarness().build_argv(spec(mode="write"))
    assert "--auto" not in OpenCodeHarness().build_argv(spec(mode="readonly"))


def test_resume_passes_session_flag():
    argv = OpenCodeHarness().build_argv(spec(session_id="ses_1", resume=True))
    assert argv[argv.index("--session") + 1] == "ses_1"


def test_schema_request_is_appended_to_the_prompt():
    """opencode has no --json-schema equivalent, so the ask rides in the prompt."""
    h = OpenCodeHarness()
    prompt = h.prompt_for(spec(output_schema={"type": "object", "properties": {"verdict": {}}}))
    assert "do it" in prompt
    assert "verdict" in prompt
    assert "JSON" in prompt


# --- recovering an object from prose ------------------------------------------------------

def test_extracts_plain_object():
    assert extract_json_object('{"verdict": "pass"}') == {"verdict": "pass"}


def test_extracts_from_fenced_block():
    assert extract_json_object('```json\n{"verdict": "pass"}\n```') == {"verdict": "pass"}


def test_extracts_when_prefixed_with_prose():
    text = 'Here is my answer:\n{"verdict": "changes_requested"}'
    assert extract_json_object(text) == {"verdict": "changes_requested"}


def test_returns_none_when_there_is_no_object():
    assert extract_json_object("no json here") is None
    assert extract_json_object("") is None
