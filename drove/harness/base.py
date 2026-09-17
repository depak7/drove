"""The harness abstraction: a coding CLI reduced to an async stream of normalized events."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from drove.events import HarnessEvent

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


# Read in chunks rather than by line. StreamReader.readline() raises
# "Separator is found, but chunk is longer than limit" once a line exceeds its 64 KiB buffer, and
# harness output routinely blows past that — a codex command_execution carrying a long
# aggregated_output, or a tool result holding a whole file. That killed real runs mid-execute.
_CHUNK = 256 * 1024


async def _lines(stream: asyncio.StreamReader) -> AsyncIterator[str]:
    """Yield newline-delimited text with no limit on how long a line may be."""
    buffer = bytearray()
    while True:
        chunk = await stream.read(_CHUNK)
        if not chunk:
            break
        buffer.extend(chunk)
        while True:
            index = buffer.find(b"\n")
            if index < 0:
                break
            line = bytes(buffer[:index])
            del buffer[: index + 1]
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                yield text
    # A process that dies mid-line still leaves something worth recording.
    if tail := bytes(buffer).decode("utf-8", errors="replace").strip():
        yield tail


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
        async for line in _lines(proc.stdout):
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
