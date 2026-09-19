"""Global state root and per-repo configuration."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path(os.environ.get("DROVE_HOME", Path.home() / ".drove"))
REPO_CONFIG = ".drove.toml"


DEFAULT_HARNESSES = {
    "plan": "claude",
    "execute": "claude",
    "review": "codex",
}


@dataclass
class RepoConfig:
    root: Path
    base_branch: str = "main"
    verify: dict[str, str] = field(default_factory=dict)
    # Opt-in browser smoke checks; see pipeline/stages/browser.py. A separate table from [verify]
    # because that one holds shell commands and this holds structure.
    browser: dict = field(default_factory=dict)
    harness: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HARNESSES))
    # Publish the branch once a run passes. On by default: a branch that exists only on this
    # laptop cannot be opened, shared or built by CI, so delivery is not really delivery. A repo
    # whose remote you would rather Drove left alone sets `push = false`.
    push: bool = True
    remote: str = "origin"

    @classmethod
    def load(cls, root: Path) -> RepoConfig:
        path = root / REPO_CONFIG
        data: dict = {}
        if path.exists():
            data = tomllib.loads(path.read_text())
        return cls(
            root=root,
            base_branch=data.get("base_branch", "main"),
            verify={k: v for k, v in (data.get("verify") or {}).items() if isinstance(v, str)},
            browser=data.get("browser", {}) or {},
            harness={**DEFAULT_HARNESSES, **(data.get("harness", {}) or {})},
            push=bool((data.get("deliver") or {}).get("push", True)),
            remote=str((data.get("deliver") or {}).get("remote", "origin")),
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

# [browser]
# Opt-in smoke checks: start the app, open these pages, and record console errors, failed
# requests and a screenshot of each. Advisory — it never fails a run on its own.
# start = "npm run dev"
# dir   = "web"
# url   = "http://localhost:5173"
# paths = ["/"]

[deliver]
# Push the feature branch to the remote once review and your checks have passed, and record a
# compare link with the evidence. Nothing is pushed for a run that failed. Set false to keep this
# repo's branches local.
push = true
remote = "origin"

[harness]
# Cross-harness by stage: whoever writes the code must not be the one who reviews it.
plan = "claude"
execute = "claude"
review = "codex"
"""


def provisional_title(task: str, limit: int = 62) -> str:
    """A readable name for a feature before the planner has named it properly.

    A pasted paragraph makes a terrible title, and the feature list is unusable when every row is
    the same wall of text. The full request is never lost — it is kept verbatim as the run's
    intent, and the planner replaces this with a real title as soon as it produces one.
    """
    text = " ".join(task.split())
    if not text:
        return "Untitled"
    # A first sentence or clause is usually the ask; everything after it is qualification.
    for stop in (". ", "; ", " — ", " - ", ", and ", ": "):
        head, sep, _ = text.partition(stop)
        if sep and 12 <= len(head) <= limit:
            text = head
            break
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return f"{cut or text[:limit]}…"
