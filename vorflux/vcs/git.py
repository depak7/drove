"""Thin, synchronous git wrapper.

Every git call in vorflux goes through here so that argv construction, error surfacing and the
"never use a shell" rule live in one place.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.strip()


def is_repo(path: Path) -> bool:
    # In a worktree, .git is a FILE pointing at the real gitdir, not a directory.
    return (path / ".git").exists()


def current_branch(repo: Path) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def head_sha(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def commits_ahead(repo: Path, base: str, branch: str) -> list[str]:
    """Commits on `branch` that `base` does not have — i.e. unintegrated work.

    This is the single check that decides whether a worktree may be torn down.
    """
    out = git(repo, "log", "--oneline", f"{base}..{branch}", check=False)
    return [line for line in out.splitlines() if line.strip()]


def commits_behind(repo: Path, base: str, branch: str) -> int:
    """How far `base` has moved on since `branch` diverged — base drift."""
    out = git(repo, "rev-list", "--count", f"{branch}..{base}", check=False)
    try:
        return int(out or 0)
    except ValueError:
        return 0


def is_dirty(worktree: Path) -> bool:
    """Uncommitted or untracked changes. git refuses `worktree remove` on these anyway."""
    return bool(git(worktree, "status", "--porcelain", check=False))


def branch_exists(repo: Path, branch: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", branch],
            capture_output=True,
            stdin=subprocess.DEVNULL,
        ).returncode
        == 0
    )
