"""Per-feature git worktrees: creation, drift detection, and teardown that refuses to lose work.

A worktree is filesystem-isolated but history-shared: its own HEAD, index and checked-out files,
one shared object store. That is what lets several agents work at once without colliding on
`index.lock` or each other's half-written files, while your own checkout stays untouched.

Worktrees live under ~/.vorflux, never inside the repo — a nested worktree gets swept up by test
discovery, file watchers and `git add -A`.

A worktree belongs to a FEATURE, not a run: it outlives every run against it so a delivered
feature can be pivoted later with its branch and agent sessions intact.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vorflux import config
from vorflux.config import slug, worktrees_dir
from vorflux.vcs import git


class WorktreeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Worktree:
    repo: Path
    path: Path
    branch: str
    base: str


@dataclass(frozen=True)
class Teardown:
    removed: bool
    reason: str
    recovery: str | None = None


def branch_for(feature_id: str) -> str:
    return f"vf/{slug(feature_id)}"


def path_for(repo: Path, feature_id: str) -> Path:
    """Resolve a worktree path, refusing anything that escapes the worktrees root.

    Feature ids can originate from model output (a slugified task title), so a title like
    "Fix ../legacy/../ parser" must not become a write outside ~/.vorflux. Resolve first —
    that collapses `..` and follows symlinks; comparing unresolved strings is how this check
    is usually got wrong.
    """
    # Read through the module, not a from-import: binding HOME at import time makes the
    # state root impossible to redirect (tests, VORFLUX_HOME) after the fact.
    root = (config.HOME / "worktrees").resolve()
    target = (worktrees_dir(repo) / slug(feature_id)).resolve()
    if not target.is_relative_to(root):
        raise WorktreeError(f"worktree path escapes {root}: {target}")
    return target


def create(repo: Path, feature_id: str, base: str) -> Worktree:
    """Add a worktree on a new branch off `base`, or adopt one that already exists."""
    repo = repo.resolve()
    if not git.is_repo(repo):
        raise WorktreeError(f"{repo} is not a git repository")

    path = path_for(repo, feature_id)
    branch = branch_for(feature_id)

    if path.exists():
        # A feature's worktree is reused across runs, so this is the pivot path, not an error.
        return Worktree(repo=repo, path=path, branch=branch, base=base)

    path.parent.mkdir(parents=True, exist_ok=True)
    if git.branch_exists(repo, branch):
        git.git(repo, "worktree", "add", str(path), branch)
    else:
        git.git(repo, "worktree", "add", "-b", branch, str(path), base)
    return Worktree(repo=repo, path=path, branch=branch, base=base)


def drift(wt: Worktree) -> int:
    """Commits `base` has gained since this feature branched. Never acted on automatically."""
    return git.commits_behind(wt.repo, wt.base, wt.branch)


def unintegrated(wt: Worktree) -> list[str]:
    """Commits on the feature branch that base does not have."""
    return git.commits_ahead(wt.repo, wt.base, wt.branch)


def has_landed(wt: Worktree) -> bool:
    """True once the feature's work is reachable from base — the Landed signal.

    The same check that guards teardown detects a merge, so no webhook or PR polling is needed.
    """
    return git.branch_exists(wt.repo, wt.branch) and not unintegrated(wt)


def teardown(wt: Worktree, force: bool = False) -> Teardown:
    """Remove a worktree only when provably nothing would be lost.

    Git's own protections are partial and the gap is where work disappears:

    * uncommitted changes  -> `worktree remove` refuses. Good.
    * committed but unmerged -> `worktree remove` succeeds SILENTLY. The branch still holds the
      commits, so nothing is lost yet; but cleanup code naturally deletes the branch next, and
      `branch -D` on unmerged work makes those commits unreachable and eventually gc-able.

    So: never `--force`, never `branch -D`, and refuse entirely while work is unintegrated.
    Preservation is reversible; deletion is not.
    """
    if not wt.path.exists():
        return Teardown(removed=False, reason="worktree already gone")

    if not force:
        # Unintegrated commits are checked first: they are the durable work, and after a verify
        # stage a worktree is usually also "dirty" with build artifacts (__pycache__, dist/).
        # Leading with the dirt would report the trivial condition and hide the real one.
        pending = unintegrated(wt)
        if pending:
            return Teardown(
                removed=False,
                reason=f"{len(pending)} commit(s) not in {wt.base}",
                recovery=f"git -C {wt.repo} worktree remove {wt.path}",
            )
        if git.is_dirty(wt.path):
            return Teardown(
                removed=False,
                reason="uncommitted or untracked changes in the worktree",
                recovery=f"cd {wt.path}",
            )

    git.git(wt.repo, "worktree", "remove", str(wt.path))
    # -d, never -D: git refuses to delete a branch holding unmerged commits, and that refusal
    # is a safety net we want, not an obstacle to route around.
    git.git(wt.repo, "branch", "-d", wt.branch, check=False)
    return Teardown(removed=True, reason="work is integrated into base")


def prune(repo: Path) -> None:
    """Clear registrations whose directory is already gone (e.g. after a crash)."""
    git.git(repo, "worktree", "prune", check=False)
