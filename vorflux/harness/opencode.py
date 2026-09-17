"""opencode adapter.

Shapes verified live against opencode 1.17.13 on a real edit task.

Three quirks shape this file:

1. **No terminal result event** — like codex, the `Result` is synthesized when the stream ends.
2. **No structured-output flag.** There is no `--json-schema` equivalent, so when a schema is
   requested the adapter appends a JSON-only instruction to the prompt and recovers the object
   from the final text. This is *prompt compliance*, not API-level enforcement: claude and codex
   are guaranteed to return conforming JSON, opencode is merely likely to. Observed failing
   roughly 1 run in 5 by answering in prose. The adapter therefore fails the Result explicitly
   instead of returning `structured=None` and letting the caller guess why.
3. **`tokens.total` is a trap.** Every token field is per-step, including `total` — which is
   that step's `input + output + reasoning + cache.read`. It climbs across steps only because
   the context grows, which makes it look cumulative. Summing it counts the cached prefix once
   per step: 58k phantom tokens in the 4-step fixture. Use the components, ignore `total`.
   `input` already excludes the cached prefix, which arrives separately as `cache.read`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from vorflux.events import (
    AssistantText,
    HarnessEvent,
    Reasoning,
    Result,
    SessionStarted,
    ToolCall,
    ToolResult,
    TurnCompleted,
    Usage,
)
from vorflux.harness.base import HarnessError, InvokeSpec, stream_jsonl_process
from vorflux.harness.util import JSON_ONLY_INSTRUCTION, extract_json_object


class OpenCodeHarness:
    name = "opencode"
    binary = "opencode"

    def prompt_for(self, spec: InvokeSpec) -> str:
        if spec.output_schema is None:
            return spec.prompt
        return spec.prompt + JSON_ONLY_INSTRUCTION.format(
            schema=json.dumps(spec.output_schema, indent=2)
        )

    def build_argv(self, spec: InvokeSpec) -> list[str]:
        argv = [self.binary, "run", "--format", "json", "--dir", str(spec.cwd)]
        if spec.model:
            argv += ["-m", spec.model]
        if spec.session_id and spec.resume:
            argv += ["--session", spec.session_id]
        if spec.mode == "write":
            # opencode exposes no read-only sandbox, so --auto is only ever granted for stages
            # that are meant to write. Read-only stages simply get no approval grant.
            argv.append("--auto")
        argv.append(self.prompt_for(spec))
        return argv

    def parse(self, p: dict[str, Any]) -> list[HarnessEvent]:
        t = p.get("type")
        part = p.get("part", {}) or {}
        out: list[HarnessEvent] = []

        if session_id := p.get("sessionID"):
            # opencode announces no session-start event; the id rides on every event instead.
            # The engine dedupes — only the first is meaningful.
            if t == "step_start":
                out.append(SessionStarted(session_id=session_id))

        if t == "text":
            out.append(AssistantText(text=part.get("text", "")))
        elif t == "reasoning":
            out.append(Reasoning(text=part.get("text", "")))
        elif t == "tool_use":
            state = part.get("state", {}) or {}
            out.append(
                ToolCall(
                    tool_id=part.get("callID"),
                    name=part.get("tool", "?"),
                    input=state.get("input", {}) or {},
                )
            )
            if state.get("status") == "completed":
                out.append(
                    ToolResult(
                        tool_id=part.get("callID"),
                        output=str(state.get("output", ""))[:20000],
                        is_error=False,
                    )
                )
            elif state.get("status") == "error":
                out.append(
                    ToolResult(
                        tool_id=part.get("callID"),
                        output=str(state.get("error", ""))[:20000],
                        is_error=True,
                    )
                )
        elif t == "step_finish":
            tokens = part.get("tokens", {}) or {}
            cache = tokens.get("cache", {}) or {}
            out.append(
                Usage(
                    # Deliberately NOT tokens["total"] — see the module docstring.
                    input_tokens=tokens.get("input", 0) or 0,
                    output_tokens=tokens.get("output", 0) or 0,
                    cache_read_tokens=cache.get("read", 0) or 0,
                    cache_write_tokens=cache.get("write", 0) or 0,
                    reasoning_tokens=tokens.get("reasoning", 0) or 0,
                )
            )
            if part.get("reason") == "stop":
                out.append(TurnCompleted(stop_reason="stop"))

        return out

    async def invoke(self, spec: InvokeSpec) -> AsyncIterator[HarnessEvent]:
        argv = self.build_argv(spec)

        session_id = spec.session_id
        seen_session = False
        totals = {"in": 0, "out": 0, "cr": 0, "cw": 0}
        reported_cost = 0.0
        texts: list[str] = []

        failed: str | None = None
        try:
            async for event in stream_jsonl_process(
                argv, spec.cwd, self.parse, raw_log=spec.raw_log, env=spec.env
            ):
                if isinstance(event, SessionStarted):
                    if seen_session:
                        continue  # the id rides on every event; announce it once
                    seen_session = True
                    session_id = event.session_id
                elif isinstance(event, AssistantText):
                    texts.append(event.text)
                elif isinstance(event, Usage):
                    totals["in"] += event.input_tokens
                    totals["out"] += event.output_tokens
                    totals["cr"] += event.cache_read_tokens
                    totals["cw"] += event.cache_write_tokens
                yield event
        except HarnessError as exc:
            failed = str(exc)

        final_text = "\n".join(texts).strip()
        structured = None
        if spec.output_schema is not None:
            structured = extract_json_object(final_text)
            if structured is None and failed is None:
                # Asked for an object, got prose. Say so — a stage can then retry deliberately
                # rather than treating a missing object as an unexplained empty result.
                failed = (
                    "opencode returned no JSON object despite a schema request "
                    f"(first 200 chars: {final_text[:200]!r})"
                )

        yield Result(
            ok=failed is None,
            final_text=final_text,
            structured=structured,
            session_id=session_id,
            tokens_in=totals["in"],
            tokens_out=totals["out"],
            cache_read_tokens=totals["cr"],
            # opencode reports cost: 0 on subscription auth, where the marginal cost really is
            # zero. Nothing to normalize; leave it unset rather than asserting a zero.
            cost_usd=reported_cost or None,
            error=failed,
        )
