"""VERIFY stage: run the repo's own commands and keep the output as evidence.

No model is involved. These are the project's real build/test/lint commands from `.drove.toml`,
run in the worktree. A reviewer's opinion that the code is correct and a test suite that actually
passes are different kinds of claim, and the second is the one worth keeping.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

MAX_OUTPUT = 20_000
TIMEOUT_SECONDS = 900


@dataclass
class Check:
    name: str
    command: str
    exit_code: int
    output: str
    duration_s: float
    repo: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class VerifyOutcome:
    checks: list[Check] = field(default_factory=list)

    @property
    def ran(self) -> bool:
        return bool(self.checks)

    @property
    def passed(self) -> bool:
        # Vacuously true when nothing is configured. `ran` distinguishes that from real success,
        # so a repo with no verify commands is never reported as "tests passed".
        return all(c.ok for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


def _terminate(proc: subprocess.Popen[str]) -> None:
    """Terminate a verify command and every process it spawned."""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            proc.kill()
    else:
        # A shell can exit on TERM while a child ignores it, so KILL the original group even if
        # the leader reaps promptly.
        time.sleep(0.2)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=0.5)


def run_verify(
    cwd: Path,
    commands: dict[str, str],
    repo: str = "",
    register: Callable[[subprocess.Popen[str]], None] | None = None,
) -> VerifyOutcome:
    outcome = VerifyOutcome()
    for name, command in commands.items():
        if not command or not command.strip():
            continue
        started = time.monotonic()
        try:
            # shell=True is deliberate: these are the user's own commands, written in their
            # own repo config, with shell syntax they expect to work ("pytest -q && ruff .").
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            if register is not None:
                register(proc)
            stdout, stderr = proc.communicate(timeout=TIMEOUT_SECONDS)
            output, code = stdout + stderr, proc.returncode
        except subprocess.TimeoutExpired:
            _terminate(proc)
            output, code = (f"timed out after {TIMEOUT_SECONDS}s", 124)
        except OSError as exc:
            output, code = (str(exc), 127)

        outcome.checks.append(
            Check(
                name=name,
                command=command,
                repo=repo,
                exit_code=code,
                output=output[-MAX_OUTPUT:],
                duration_s=round(time.monotonic() - started, 2),
            )
        )
    return outcome


def run_all(
    trees,
    workspace,
    register: Callable[[subprocess.Popen[str]], None] | None = None,
) -> VerifyOutcome:
    """Each repo's own verify commands, run inside that repo's worktree.

    A workspace's repos are separate projects with separate toolchains; running one repo's test
    command from another's directory is meaningless. Only repos the feature actually touched are
    checked — re-running an untouched repo's suite proves nothing about this change.
    """
    from drove.vcs import tree as trees_mod

    combined = VerifyOutcome()
    for t in trees_mod.touched(trees):
        commands = t.repo.config.verify
        if not commands:
            continue
        result = run_verify(t.path, commands, repo=t.repo.name, register=register)
        combined.checks.extend(result.checks)
    return combined


async def run_all_async(trees, workspace) -> VerifyOutcome:
    """Run verification off-loop and kill its process groups if the pipeline is cancelled."""
    processes: list[subprocess.Popen[str]] = []
    try:
        return await asyncio.to_thread(run_all, trees, workspace, processes.append)
    except asyncio.CancelledError:
        for proc in processes:
            if proc.poll() is None:
                _terminate(proc)
        raise
