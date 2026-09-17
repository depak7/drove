"""Claude Code adapter.

Event shapes below were captured from `claude` 2.1.273 running live, not read from docs.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from vorflux.events import (
    AssistantText,
    HarnessEvent,
    RateLimit,
    Reasoning,
    Result,
    SessionStarted,
    ToolCall,
    ToolResult,
    TurnCompleted,
    Usage,
)
from vorflux.harness.base import InvokeSpec, stream_jsonl_process

# Tools that mutate the tree. Stripped for read-only stages (planning, reviewing) so a stage
# that is supposed to only look at the repo cannot quietly rewrite it.
_WRITE_TOOLS = ["Edit", "Write", "NotebookEdit"]


class ClaudeCodeHarness:
    name = "claude"
    binary = "claude"

    def build_argv(self, spec: InvokeSpec) -> list[str]:
        argv = [self.binary, "-p", "--output-format", "stream-json", "--verbose"]

        if spec.model:
            argv += ["--model", spec.model]

        if spec.resume and spec.session_id:
            argv += ["--resume", spec.session_id]
        elif spec.session_id:
            # We mint the UUID ourselves, so run <-> session mapping is known before spawn.
            argv += ["--session-id", spec.session_id]

        if spec.mode == "readonly":
            argv += ["--permission-mode", "acceptEdits", "--disallowed-tools", *_WRITE_TOOLS]
        else:
            argv += ["--permission-mode", "acceptEdits"]

        for d in spec.extra_dirs:
            argv += ["--add-dir", str(d)]

        if spec.output_schema is not None:
            # Inline JSON, NOT a file path -- unlike codex's --output-schema. Passing a path
            # fails with "--json-schema is not valid JSON".
            argv += ["--json-schema", json.dumps(spec.output_schema)]

        if spec.max_turns is not None:
            # Structured output needs a turn of its own to emit the object; --max-turns 1
            # terminates with subtype=error_max_turns before anything is produced.
            floor = 2 if spec.output_schema is not None else 1
            argv += ["--max-turns", str(max(spec.max_turns, floor))]

        argv.append(spec.prompt)
        return argv

    def parse(self, p: dict[str, Any]) -> list[HarnessEvent]:
        t = p.get("type")
        out: list[HarnessEvent] = []

        if t == "system" and p.get("subtype") == "init":
            out.append(
                SessionStarted(
                    session_id=p.get("session_id", ""),
                    model=p.get("model"),
                    cwd=p.get("cwd"),
                    tools=p.get("tools", []),
                )
            )

        elif t == "rate_limit_event":
            info = p.get("rate_limit_info", {}) or {}
            windows = info.get("unifiedWindows", {}) or {}
            out.append(
                RateLimit(
                    status=info.get("status", "unknown"),
                    five_hour_utilization=(windows.get("five_hour") or {}).get("utilization"),
                    seven_day_utilization=(windows.get("seven_day") or {}).get("utilization"),
                    resets_at=info.get("resetsAt"),
                )
            )

        elif t == "assistant":
            msg = p.get("message", {}) or {}
            for block in msg.get("content", []) or []:
                bt = block.get("type")
                if bt == "text" and block.get("text"):
                    out.append(AssistantText(text=block["text"]))
                elif bt == "thinking" and block.get("thinking"):
                    out.append(Reasoning(text=block["thinking"]))
                elif bt == "tool_use":
                    out.append(
                        ToolCall(
                            tool_id=block.get("id"),
                            name=block.get("name", "?"),
                            input=block.get("input", {}) or {},
                        )
                    )
            if usage := msg.get("usage"):
                out.append(_usage(usage, msg.get("model")))

        elif t == "user":
            msg = p.get("message", {}) or {}
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_result":
                        body = block.get("content", "")
                        if isinstance(body, list):
                            body = "".join(
                                b.get("text", "") for b in body if isinstance(b, dict)
                            )
                        out.append(
                            ToolResult(
                                tool_id=block.get("tool_use_id"),
                                output=str(body)[:20000],
                                is_error=bool(block.get("is_error")),
                            )
                        )

        elif t == "result" or "total_cost_usd" in p:
            if reason := p.get("stop_reason"):
                out.append(TurnCompleted(stop_reason=reason))
            out.append(_result(p))

        return out

    async def invoke(self, spec: InvokeSpec) -> AsyncIterator[HarnessEvent]:
        argv = self.build_argv(spec)
        async for event in stream_jsonl_process(
            argv, spec.cwd, self.parse, raw_log=spec.raw_log, env=spec.env
        ):
            yield event


def _usage(u: dict[str, Any], model: str | None) -> Usage:
    return Usage(
        input_tokens=u.get("input_tokens", 0) or 0,
        output_tokens=u.get("output_tokens", 0) or 0,
        cache_read_tokens=u.get("cache_read_input_tokens", 0) or 0,
        cache_write_tokens=u.get("cache_creation_input_tokens", 0) or 0,
        reasoning_tokens=(u.get("output_tokens_details") or {}).get("thinking_tokens", 0) or 0,
        model=model,
    )


def _result(p: dict[str, Any]) -> Result:
    text = p.get("result", "") or ""
    structured: dict[str, Any] | None = None
    # With --json-schema the final text IS the object, delivered as a JSON string.
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                structured = parsed
        except json.JSONDecodeError:
            pass

    errors = p.get("errors") or []
    return Result(
        ok=not p.get("is_error", False),
        final_text=text,
        structured=structured,
        session_id=p.get("session_id"),
        cost_usd=p.get("total_cost_usd"),
        cost_is_estimate=False,
        duration_ms=p.get("duration_ms"),
        error="; ".join(str(e) for e in errors) if errors else None,
    )
