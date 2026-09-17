"""One contract, every harness.

The offline half asserts structural promises each adapter must keep. The `live` half actually
spends tokens and is deselected by default; run it with `pytest -m live` when a CLI is upgraded,
since that is when these contracts break.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vorflux.events import Result, SessionStarted
from vorflux.harness import registry
from vorflux.harness.base import InvokeSpec
from vorflux.pipeline.schemas import ReviewVerdict, json_schema

NAMES = sorted(registry.PRESETS)


@pytest.mark.parametrize("name", NAMES)
def test_preset_is_well_formed(name):
    preset = registry.PRESETS[name]
    assert preset.name == name
    assert preset.binary and preset.install
    harness = registry.get(name)
    assert harness.name == name


@pytest.mark.parametrize("name", NAMES)
def test_prompt_is_always_the_final_argument(name):
    """Every CLI takes the prompt as a trailing positional; a flag after it would be swallowed."""
    harness = registry.get(name)
    spec = InvokeSpec(prompt="THE-PROMPT", cwd=Path("/repo"))
    argv = harness.build_argv(spec)
    assert argv[-1].startswith("THE-PROMPT"), argv


@pytest.mark.parametrize("name", NAMES)
def test_binary_leads_the_argv(name):
    harness = registry.get(name)
    argv = harness.build_argv(InvokeSpec(prompt="x", cwd=Path("/repo")))
    assert argv[0] == harness.binary


@pytest.mark.parametrize("name", NAMES)
def test_unknown_payloads_are_ignored_not_fatal(name):
    """CLIs add event types between releases; an unknown one must never crash a run."""
    harness = registry.get(name)
    for payload in ({}, {"type": "brand_new_event_type"}, {"type": "item.completed"}):
        assert isinstance(harness.parse(payload), list)


def test_registry_rejects_unknown_names():
    with pytest.raises(KeyError):
        registry.get("no-such-harness")


# --- live ---------------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.parametrize("name", NAMES)
def test_live_structured_output_round_trip(name, tmp_path):
    """Every harness must return a schema-valid object, whether natively or via prompt."""
    if registry.which(registry.PRESETS[name].binary) is None:
        pytest.skip(f"{name} not installed")

    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")

    async def run():
        harness = registry.get(name)
        spec = InvokeSpec(
            prompt="Review calc.py. Reply with verdict 'pass' and a one-sentence summary.",
            cwd=tmp_path,
            mode="readonly",
            output_schema=json_schema(ReviewVerdict),
        )
        events = [e async for e in harness.invoke(spec)]
        return events

    events = asyncio.run(run())

    assert any(isinstance(e, SessionStarted) and e.session_id for e in events)

    result = events[-1]
    assert isinstance(result, Result), "the last event must always be a terminal Result"
    assert result.ok, result.error
    assert result.session_id
    assert result.structured is not None
    ReviewVerdict.model_validate(result.structured)
