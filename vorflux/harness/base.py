"""The harness abstraction: a coding CLI reduced to an async stream of normalized events."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from vorflux.events import HarnessEvent

Mode = Literal["readonly", "write"]


@dataclass
class InvokeSpec:
    """What to ask a harness to do. Harness-agnostic by construction."""

    prompt: str
    cwd: Path
    mode: Mode = "readonly"
    output_schema: dict[str, Any] | None = None
    session_id: str | None = None
    resume: bool = False
    model: str | None = None
    extra_dirs: list[Path] = field(default_factory=list)
    max_turns: int | None = None
    raw_log: Path | None = None
    env: dict[str, str] = field(default_factory=dict)


class HarnessError(RuntimeError):
    pass


@runtime_checkable
class Harness(Protocol):
    name: str
    binary: str

    def build_argv(self, spec: InvokeSpec) -> list[str]: ...

    def parse(self, payload: dict[str, Any]) -> list[HarnessEvent]: ...

    def invoke(self, spec: InvokeSpec) -> AsyncIterator[HarnessEvent]: ...


async def stream_jsonl_process(
    argv: list[str],
    cwd: Path,
    on_payload: Callable[[dict[str, Any]], list[HarnessEvent]],
    raw_log: Path | None = None,
    env: dict[str, str] | None = None,
) -> AsyncIterator[HarnessEvent]:
    """Spawn a CLI that emits JSON Lines on stdout and yield normalized events.

    Two non-obvious rules, both learned by watching real CLIs misbehave:

    * ``stdin`` is always ``DEVNULL``. Every harness tested (claude, codex) blocks waiting on
      stdin when it is not a TTY — codex hangs outright, claude stalls 3s then warns. Redirecting
      is not optional.
    * stdout is read line-by-line and tee'd verbatim to ``raw_log`` *before* parsing, so a parse
      failure never costs us the evidence of what actually happened.
    """
    proc_env = {**os.environ, **(env or {})}
    log = None
    if raw_log is not None:
        raw_log.parent.mkdir(parents=True, exist_ok=True)
        log = raw_log.open("a", encoding="utf-8")

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=proc_env,
    )
    assert proc.stdout is not None

    try:
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if log is not None:
                log.write(line + "\n")
                log.flush()
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue  # progress chatter on stdout; the raw log keeps it
            if not isinstance(payload, dict):
                continue
            for event in on_payload(payload):
                yield event

        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace") if proc.stderr else ""
        code = await proc.wait()
        if code != 0:
            raise HarnessError(f"{argv[0]} exited {code}: {stderr.strip()[:2000]}")
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if log is not None:
            log.close()
