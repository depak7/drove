"""Codex CLI adapter.

Shapes verified live against codex-cli 0.153.2 on a real edit task.

Two structural differences from Claude Code drive this file:

1. **Codex emits no terminal result event.** The stream just ends. The adapter tracks state and
   synthesizes a `Result` once the process exits.
2. **`codex exec resume` takes a different, smaller option set than `codex exec`** — no `-s`,
   no `-C`, no `--add-dir`. Those are inherited from the original session. Passing `-s` to resume
   fails with `error: unexpected argument '-s' found`, and option order matters: flags must come
   before the SESSION_ID positional.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from drove.events import (
    AssistantText,
    FileChanged,
    HarnessEvent,
    Reasoning,
    Result,
    SessionStarted,
    ToolCall,
    ToolResult,
    TurnCompleted,
    Usage,
)
from drove.harness.base import HarnessError, InvokeSpec, stream_jsonl_process

_KIND = {"add": "add", "update": "modify", "delete": "delete"}


class CodexHarness:
    name = "codex"
    binary = "codex"

    def build_argv(
        self,
        spec: InvokeSpec,
        schema_file: Path | None = None,
        last_message_file: Path | None = None,
    ) -> list[str]:
        resuming = spec.resume and spec.session_id

        # Codex refuses to start outside a git repo: "Not inside a trusted directory and
        # --skip-git-repo-check was not specified." This is the normal case, not an edge case —
        # a feature's working directory is the root that HOLDS its repo worktrees, so it is not
        # itself a repo. Needed on both paths: `exec resume` enforces the same rule and accepts
        # the same flag, and omitting it there failed every retry that resumed a codex session.
        # (`.git` is a FILE inside a worktree, not a directory, so test for existence.)
        #
        # Placed here, early, rather than appended: it takes no value, and a bare flag left
        # adjacent to the trailing prompt is the shape that let a variadic option swallow claude's.
        trust = [] if (spec.cwd / ".git").exists() else ["--skip-git-repo-check"]

        if resuming:
            # Flags first: `codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]`.
            argv = [self.binary, "exec", "resume", "--json", *trust]
        else:
            sandbox = "read-only" if spec.mode == "readonly" else "workspace-write"
            argv = [self.binary, "exec", "--json", *trust, "-s", sandbox, "-C", str(spec.cwd)]
            for d in spec.extra_dirs:
                argv += ["--add-dir", str(d)]

        if spec.model:
            argv += ["-m", spec.model]
        if schema_file is not None:
            # A FILE path here — the opposite of claude's --json-schema, which wants inline JSON.
            argv += ["--output-schema", str(schema_file)]
        if last_message_file is not None:
            argv += ["-o", str(last_message_file)]

        if resuming:
            argv.append(str(spec.session_id))
        argv.append(spec.prompt)
        return argv

    def parse(self, p: dict[str, Any]) -> list[HarnessEvent]:
        t = p.get("type")
        out: list[HarnessEvent] = []

        if t == "thread.started":
            out.append(SessionStarted(session_id=p.get("thread_id", "")))
            return out

        if t in ("item.started", "item.completed"):
            item = p.get("item", {}) or {}
            it = item.get("type")
            done = t == "item.completed"

            if it == "agent_message" and done:
                out.append(AssistantText(text=item.get("text", "")))
            elif it == "reasoning" and done:
                out.append(Reasoning(text=item.get("text", "")))
            elif it == "command_execution":
                if done:
                    out.append(
                        ToolResult(
                            tool_id=item.get("id"),
                            output=str(item.get("aggregated_output", ""))[:20000],
                            is_error=bool(item.get("exit_code")),
                        )
                    )
                else:
                    out.append(
                        ToolCall(
                            tool_id=item.get("id"),
                            name="bash",
                            input={"command": item.get("command", "")},
                        )
                    )
            elif it == "file_change" and done:
                for change in item.get("changes", []) or []:
                    out.append(
                        FileChanged(
                            path=change.get("path", ""),
                            change=_KIND.get(change.get("kind", ""), "modify"),
                        )
                    )
            elif it and done:
                # mcp_tool_call, web_search, todo_list … keep them visible rather than dropping.
                out.append(ToolCall(tool_id=item.get("id"), name=it, input={}))
            return out

        if t == "turn.completed":
            u = p.get("usage", {}) or {}
            out.append(
                Usage(
                    input_tokens=u.get("input_tokens", 0) or 0,
                    output_tokens=u.get("output_tokens", 0) or 0,
                    cache_read_tokens=u.get("cached_input_tokens", 0) or 0,
                    cache_write_tokens=u.get("cache_write_input_tokens", 0) or 0,
                    reasoning_tokens=u.get("reasoning_output_tokens", 0) or 0,
                )
            )
            out.append(TurnCompleted(stop_reason="end_turn"))
        elif t in ("turn.failed", "error"):
            err = p.get("error", {}) if t == "turn.failed" else p
            message = (err or {}).get("message", "unknown")
            out.append(TurnCompleted(stop_reason=f"failed: {message}"))

        return out

    async def invoke(self, spec: InvokeSpec) -> AsyncIterator[HarnessEvent]:
        with tempfile.TemporaryDirectory(prefix="drove-codex-") as tmp:
            tmpdir = Path(tmp)
            schema_file = None
            if spec.output_schema is not None:
                schema_file = tmpdir / "schema.json"
                schema_file.write_text(json.dumps(spec.output_schema))
            last_message = tmpdir / "last.txt"

            argv = self.build_argv(spec, schema_file, last_message)

            session_id = spec.session_id
            totals = {"in": 0, "out": 0, "cr": 0, "cw": 0}
            last_text = ""
            failed: str | None = None

            # codex reports the real cause as an `error`/`turn.failed` event and *then* exits
            # non-zero, so the exception text is only noise ("Reading additional input from
            # stdin..."). Keep the parsed reason and always emit a terminal Result — a stage that
            # never sees one cannot explain why it failed.
            try:
                async for event in stream_jsonl_process(
                    argv, spec.cwd, self.parse, raw_log=spec.raw_log, env=spec.env
                ):
                    if isinstance(event, SessionStarted):
                        session_id = event.session_id
                    elif isinstance(event, AssistantText):
                        last_text = event.text
                    elif isinstance(event, Usage):
                        totals["in"] += event.input_tokens
                        totals["out"] += event.output_tokens
                        totals["cr"] += event.cache_read_tokens
                        totals["cw"] += event.cache_write_tokens
                    elif isinstance(event, TurnCompleted) and (
                        event.stop_reason or ""
                    ).startswith("failed"):
                        failed = event.stop_reason
                    yield event
            except HarnessError as exc:
                failed = failed or str(exc)

            # codex emits several schema-conforming messages during a turn; the first is a
            # placeholder. -o writes only the final one, so trust the file over the stream.
            final_text = last_text
            if last_message.exists():
                written = last_message.read_text().strip()
                if written:
                    final_text = written

            structured: dict[str, Any] | None = None
            if final_text.startswith("{"):
                try:
                    parsed = json.loads(final_text)
                    if isinstance(parsed, dict):
                        structured = parsed
                except json.JSONDecodeError:
                    pass

            yield Result(
                ok=failed is None,
                final_text=final_text,
                structured=structured,
                session_id=session_id,
                tokens_in=totals["in"],
                tokens_out=totals["out"],
                cache_read_tokens=totals["cr"],
                error=failed,
            )
