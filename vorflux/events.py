"""Canonical harness event vocabulary.

Every harness adapter maps its native stream onto this union, so the pipeline, the API and the
UI never branch on which CLI produced an event. Adding a harness means adding a mapping, not a
new code path downstream.

Raw payloads are deliberately *not* carried here — adapters tee them to
``~/.vorflux/runs/<run-id>/<stage>.jsonl``, which is simultaneously the audit log, the replay
source and the test fixture.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class _Event(BaseModel):
    kind: str


class SessionStarted(_Event):
    kind: Literal["session_started"] = "session_started"
    session_id: str
    model: str | None = None
    cwd: str | None = None
    tools: list[str] = Field(default_factory=list)


class AssistantText(_Event):
    kind: Literal["assistant_text"] = "assistant_text"
    text: str
    partial: bool = False


class Reasoning(_Event):
    kind: Literal["reasoning"] = "reasoning"
    text: str


class ToolCall(_Event):
    kind: Literal["tool_call"] = "tool_call"
    tool_id: str | None = None
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResult(_Event):
    kind: Literal["tool_result"] = "tool_result"
    tool_id: str | None = None
    output: str = ""
    is_error: bool = False


class FileChanged(_Event):
    kind: Literal["file_changed"] = "file_changed"
    path: str
    change: Literal["add", "modify", "delete"] = "modify"


class Usage(_Event):
    """A cumulative snapshot, never a delta.

    Harnesses report running totals for a session. Velocity is the difference between consecutive
    snapshots; summing them double-counts. munder-difflin learned this the hard way — naive
    last-value reads hid 59% of real spend because counters reset on restart.
    """

    kind: Literal["usage"] = "usage"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    model: str | None = None


class RateLimit(_Event):
    """Claude Code only. Drives back-pressure: hold new runs when the window is nearly spent."""

    kind: Literal["rate_limit"] = "rate_limit"
    status: str
    five_hour_utilization: float | None = None
    seven_day_utilization: float | None = None
    resets_at: int | None = None


class TurnCompleted(_Event):
    kind: Literal["turn_completed"] = "turn_completed"
    stop_reason: str | None = None


class Result(_Event):
    """Terminal event. Exactly one per invocation, success or failure."""

    kind: Literal["result"] = "result"
    ok: bool = True
    final_text: str = ""
    structured: dict[str, Any] | None = None
    session_id: str | None = None
    cost_usd: float | None = None
    cost_is_estimate: bool = False
    duration_ms: int | None = None
    error: str | None = None


HarnessEvent = Annotated[
    SessionStarted
    | AssistantText
    | Reasoning
    | ToolCall
    | ToolResult
    | FileChanged
    | Usage
    | RateLimit
    | TurnCompleted
    | Result,
    Field(discriminator="kind"),
]
