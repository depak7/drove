"""Global state root and per-repo configuration."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path(os.environ.get("DROVE_HOME", Path.home() / ".drove"))
REPO_CONFIG = ".drove.toml"
LEGACY_REPO_CONFIG = ".vorflux.toml"


def migrate_home() -> str | None:
    """Move state from the product's former name, once.

    Called at startup. Only ever moves when the new location does not exist, so it cannot clobber
    a real state directory, and it is a rename rather than a copy so nothing is duplicated.
    """
    if HOME.exists():
        return None
    legacy = Path(os.environ.get("VORFLUX_HOME", Path.home() / ".vorflux"))
    if not legacy.exists() or legacy == HOME:
        return None
    HOME.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(HOME)
    return str(legacy)

DEFAULT_HARNESSES = {
    "plan": "claude",
    "execute": "claude",
    "review": "codex",
    "arbiter": "opencode",
}


@dataclass
class RepoConfig:
    root: Path
    base_branch: str = "main"
    verify: dict[str, str] = field(default_factory=dict)
    harness: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HARNESSES))

    @classmethod
    def load(cls, root: Path) -> RepoConfig:
        path = root / REPO_CONFIG
        if not path.exists() and (root / LEGACY_REPO_CONFIG).exists():
            path = root / LEGACY_REPO_CONFIG
        data: dict = {}
        if path.exists():
            data = tomllib.loads(path.read_text())
        return cls(
            root=root,
            base_branch=data.get("base_branch", "main"),
            verify=data.get("verify", {}) or {},
            harness={**DEFAULT_HARNESSES, **(data.get("harness", {}) or {})},
        )


def runs_dir(run_id: str) -> Path:
    return HOME / "runs" / run_id


def worktrees_dir(repo: Path) -> Path:
    return HOME / "worktrees" / slug(repo.name)


def slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-").lower() or "repo"


TEMPLATE = """# drove project config

base_branch = "{base}"

[verify]
# Commands run in the worktree after review passes. Omit what does not apply.
# build = "npm run build"
# test  = "npm test"
# lint  = "npm run lint"

[harness]
# Cross-harness by stage: whoever writes the code must not be the one who reviews it.
plan = "claude"
execute = "claude"
review = "codex"
arbiter = "opencode"
"""
