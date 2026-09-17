"""Which harnesses exist, how to detect them, whether they are usable.

munder-difflin's most durable idea: harness differences are *data*, not branching code. Install
commands are shown, never executed — we do not silently install someone's coding agent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from dataclasses import dataclass
from typing import Any
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
    # A command that lists the models this CLI can run, when it has one.
    list_models_cmd: list[str] | None = None
    # Models we know work, for CLIs that cannot enumerate. Never invented — these are verified.
    known_models: tuple[str, ...] = ()


PRESETS: dict[str, Preset] = {
    "claude": Preset(
        name="claude",
        binary="claude",
        install="curl -fsSL https://claude.ai/install.sh | bash",
        factory=ClaudeCodeHarness,
        # No cheap offline login probe; presence on PATH is all we can assert for free.
        auth_probe=None,
        # claude has no list command. These three aliases are verified working; anything else can
        # be typed in, because a hardcoded list of model ids goes stale the week it ships.
        known_models=("opus", "sonnet", "haiku"),
    ),
    "codex": Preset(
        name="codex",
        binary="codex",
        install="npm i -g @openai/codex",
        factory=CodexHarness,
        auth_probe=(["codex", "login", "status"], "Logged in"),
        # codex has no list command either; its configured default is read from config.toml.
    ),
    "opencode": Preset(
        name="opencode",
        binary="opencode",
        install="curl -fsSL https://opencode.ai/install | bash",
        factory=OpenCodeHarness,
        # Each configured credential renders as a "●" bullet; no bullets means nothing is
        # authenticated. Cosmetic-format dependent, but a wrong answer only downgrades the
        # doctor line to a warning, it never blocks a run.
        list_models_cmd=["opencode", "models"],
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
    harness = PRESETS[name].factory()
    # Spawn by absolute path. Resolving here means a GUI-launched daemon runs the same binary the
    # UI told the user it found, instead of failing with ENOENT deep inside a run.
    if resolved := which(PRESETS[name].binary):
        harness.binary = resolved
    return harness


# Where coding CLIs actually install themselves. A daemon launched from a GUI — the desktop app,
# a Dock icon, launchd — does not inherit your shell profile: its PATH is roughly
# /usr/local/bin:/bin:/usr/bin, so every one of these is invisible and the app reports that you
# have no harnesses installed while they sit right there.
EXTRA_BIN_DIRS = (
    Path.home() / ".local" / "bin",
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
    Path.home() / ".opencode" / "bin",
    Path.home() / ".cargo" / "bin",
    Path.home() / ".bun" / "bin",
)


def which(binary: str) -> str | None:
    """Locate a CLI by PATH, then by the places these tools actually live."""
    if found := shutil.which(binary):
        return found
    for directory in EXTRA_BIN_DIRS:
        candidate = directory / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


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


def configured_model(name: str) -> str | None:
    """Whatever the CLI is already set to use, so the UI can show a real default."""
    if name != "codex":
        return None
    config = Path.home() / ".codex" / "config.toml"
    if not config.exists():
        return None
    try:
        data = tomllib.loads(config.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    model = data.get("model")
    return str(model) if isinstance(model, str) else None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _claude_catalog() -> list[dict[str, str]]:
    """Claude Code caches the model catalogue it was served.

    Read from disk rather than shelling out: enumerating models should never launch anything or
    cost a round trip, and this is the same list the CLI itself offers.
    """
    folder = Path.home() / ".claude" / "cache" / "model-catalog"
    if not folder.is_dir():
        return []
    for file in sorted(folder.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        data = _read_json(file)
        models = (((data or {}).get("catalog") or {}).get("config") or {}).get("models")
        if not isinstance(models, list):
            continue
        out = []
        for m in models:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            out.append({
                "id": str(m["id"]),
                "label": str(m.get("short_name") or m.get("name") or m["id"]),
                "note": str(m.get("description") or ""),
            })
        if out:
            return out
    return []


def _codex_catalog() -> list[dict[str, str]]:
    """Codex caches its catalogue too, with a visibility flag.

    Entries marked `hide` are internal (an auto-review model, a reserve pool) — the CLI does not
    offer them, so neither do we.
    """
    data = _read_json(Path.home() / ".codex" / "models_cache.json")
    models = (data or {}).get("models")
    if not isinstance(models, list):
        return []
    return [
        {
            "id": str(m["slug"]),
            "label": str(m.get("display_name") or m["slug"]),
            "note": str(m.get("description") or ""),
        }
        for m in models
        if isinstance(m, dict) and m.get("slug") and m.get("visibility") != "hide"
    ]


def list_models(name: str) -> list[dict[str, str]]:
    """Every model this harness can run, discovered rather than hardcoded.

    Claude and Codex both cache their catalogue on disk, so this costs a file read and has no side
    effects. That matters: an earlier version shelled out to `lms ls` to offer locally served
    models, and `lms` *starts LM Studio* — merely opening the settings screen launched an
    application. Those models were unusable anyway, because driving one through codex needs
    `--oss --local-provider`, which this adapter does not pass; selecting one would have failed at
    spawn. They are left out until that is actually supported.
    """
    if name == "claude":
        if catalog := _claude_catalog():
            return catalog
        # The CLI has never cached a catalogue. These aliases are verified working.
        return [{"id": m, "label": m, "note": ""} for m in ("opus", "sonnet", "haiku")]

    if name == "codex":
        catalog = _codex_catalog()
        current = configured_model(name)
        if current and not any(m["id"] == current for m in catalog):
            catalog.insert(0, {"id": current, "label": current, "note": "from config.toml"})
        return catalog

    preset = PRESETS.get(name)
    if preset is None:
        return []
    if preset.list_models_cmd and (exe := which(preset.list_models_cmd[0])):
        try:
            proc = subprocess.run(
                [exe, *preset.list_models_cmd[1:]],
                capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
            )
            if proc.returncode == 0:
                return [
                    {"id": line.strip(), "label": line.strip(), "note": ""}
                    for line in proc.stdout.splitlines()
                    if line.strip()
                ]
        except (OSError, subprocess.SubprocessError):
            pass
    return [{"id": m, "label": m, "note": ""} for m in preset.known_models]
