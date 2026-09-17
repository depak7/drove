"""VERIFY stage: run the repo's own commands and keep the output as evidence.

No model is involved. These are the project's real build/test/lint commands from `.vorflux.toml`,
run in the worktree. A reviewer's opinion that the code is correct and a test suite that actually
passes are different kinds of claim, and the second is the one worth keeping.
"""

from __future__ import annotations

import subprocess
import time
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


def run_verify(cwd: Path, commands: dict[str, str]) -> VerifyOutcome:
    outcome = VerifyOutcome()
    for name, command in commands.items():
        if not command or not command.strip():
            continue
        started = time.monotonic()
        try:
            # shell=True is deliberate: these are the user's own commands, written in their
            # own repo config, with shell syntax they expect to work ("pytest -q && ruff .").
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
            )
            output, code = (proc.stdout + proc.stderr), proc.returncode
        except subprocess.TimeoutExpired:
            output, code = (f"timed out after {TIMEOUT_SECONDS}s", 124)
        except OSError as exc:
            output, code = (str(exc), 127)

        outcome.checks.append(
            Check(
                name=name,
                command=command,
                exit_code=code,
                output=output[-MAX_OUTPUT:],
                duration_s=round(time.monotonic() - started, 2),
            )
        )
    return outcome
