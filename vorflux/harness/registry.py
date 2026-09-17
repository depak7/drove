"""Which harnesses exist, how to detect them, and how a user installs a missing one.

Following munder-difflin's most durable idea: harness differences are *data*, not branching code.
Install commands are shown, never executed -- we don't silently install someone's coding agent.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from vorflux.harness.base import Harness
from vorflux.harness.claude_code import ClaudeCodeHarness


@dataclass(frozen=True)
class Preset:
    name: str
    binary: str
    install: str
    factory: type


PRESETS: dict[str, Preset] = {
    "claude": Preset(
        name="claude",
        binary="claude",
        install="curl -fsSL https://claude.ai/install.sh | bash",
        factory=ClaudeCodeHarness,
    ),
}

# Declared here so `vorflux doctor` can report them as "planned" rather than pretending they
# don't exist. Adapters land in M1.
PLANNED = {
    "codex": "npm i -g @openai/codex",
    "opencode": "curl -fsSL https://opencode.ai/install | bash",
    "cursor-agent": "curl https://cursor.com/install -fsS | bash",
}


def get(name: str) -> Harness:
    if name not in PRESETS:
        planned = " (adapter not implemented yet)" if name in PLANNED else ""
        raise KeyError(f"unknown harness {name!r}{planned}")
    return PRESETS[name].factory()


def which(binary: str) -> str | None:
    return shutil.which(binary)


def available() -> dict[str, str | None]:
    found = {p.name: which(p.binary) for p in PRESETS.values()}
    found.update({name: which(name) for name in PLANNED})
    return found
