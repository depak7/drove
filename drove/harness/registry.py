"""Which harnesses exist, how to detect them, whether they are usable.

munder-difflin's most durable idea: harness differences are *data*, not branching code. Install
commands are shown, never executed — we do not silently install someone's coding agent.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

from drove.harness.base import Harness
from drove.harness.claude_code import ClaudeCodeHarness
from drove.harness.codex import CodexHarness
from drove.harness.opencode import OpenCodeHarness

AuthState = Literal["ok", "logged_out", "unknown"]


@dataclass(frozen=True)
class Preset:
    name: str
    binary: str
    install: str
    factory: type
    # A cheap read-only command reporting login state, plus a substring proving success.
    # Checked against stdout AND stderr: codex prints "Logged in using ChatGPT" to stderr.
    auth_probe: tuple[list[str], str] | None = None
    supports_schema: bool = True


PRESETS: dict[str, Preset] = {
    "claude": Preset(
        name="claude",
        binary="claude",
        install="curl -fsSL https://claude.ai/install.sh | bash",
        factory=ClaudeCodeHarness,
        # No cheap offline login probe; presence on PATH is all we can assert for free.
        auth_probe=None,
    ),
    "codex": Preset(
        name="codex",
        binary="codex",
        install="npm i -g @openai/codex",
        factory=CodexHarness,
        auth_probe=(["codex", "login", "status"], "Logged in"),
    ),
    "opencode": Preset(
        name="opencode",
        binary="opencode",
        install="curl -fsSL https://opencode.ai/install | bash",
        factory=OpenCodeHarness,
        # Each configured credential renders as a "●" bullet; no bullets means nothing is
        # authenticated. Cosmetic-format dependent, but a wrong answer only downgrades the
        # doctor line to a warning, it never blocks a run.
        auth_probe=(["opencode", "providers", "list"], "\u25cf"),
        # No --json-schema equivalent; the adapter asks for JSON in the prompt instead.
        supports_schema=False,
    ),
}

PLANNED = {"cursor-agent": "curl https://cursor.com/install -fsS | bash"}


def get(name: str) -> Harness:
    if name not in PRESETS:
        planned = " (adapter not implemented yet)" if name in PLANNED else ""
        raise KeyError(f"unknown harness {name!r}{planned}")
    return PRESETS[name].factory()


def which(binary: str) -> str | None:
    return shutil.which(binary)


def auth_state(name: str) -> AuthState:
    """Best-effort login check. Never spends tokens, never blocks for long."""
    preset = PRESETS.get(name)
    if preset is None or preset.auth_probe is None:
        return "unknown"
    argv, expect = preset.auth_probe
    if which(argv[0]) is None:
        return "logged_out"
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if proc.returncode != 0:
        return "logged_out"
    combined = (proc.stdout or "") + (proc.stderr or "")
    return "ok" if (not expect or expect in combined) else "logged_out"


def available() -> dict[str, str | None]:
    found = {p.name: which(p.binary) for p in PRESETS.values()}
    found.update({name: which(name) for name in PLANNED})
    return found
