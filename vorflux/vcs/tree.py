"""A feature's worktrees — one per repo in its workspace, managed as a set.

Single-repo is N=1, not a separate code path. Every operation below is a loop, so nothing has to
be kept in sync between a "simple" and a "multi-repo" implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vorflux.config import slug
from vorflux.vcs import git
from vorflux.vcs.worktree import Teardown, WorktreeError
from vorflux.workspace import Repo, Workspace


@dataclass(frozen=True)
class Tree:
    """One repo's worktree inside a feature."""

    repo: Repo
    path: Path
    branch: str

    @property
    def base(self) -> str:
        return self.repo.base_branch


@dataclass
class FeatureTrees:
    root: Path
    trees: list[Tree]

    def __iter__(self):
        return iter(self.trees)

    def __len__(self) -> int:
        return len(self.trees)

    @property
    def branch(self) -> str:
        return self.trees[0].branch if self.trees else ""

    def by_name(self, name: str) -> Tree | None:
        return next((t for t in self.trees if t.repo.name == name), None)

    @property
    def dirty_or_pending(self) -> list[Tree]:
        return [t for t in self.trees if git.is_dirty(t.path) or unintegrated(t)]


def branch_for(feature_id: str) -> str:
    return f"vf/{slug(feature_id)}"


def create(workspace: Workspace, feature_id: str) -> FeatureTrees:
    """Check out every repo in the workspace side by side under one feature root."""
    if not workspace.repos:
        raise WorktreeError(f"workspace {workspace.name!r} has no repositories")

    root = workspace.feature_root(feature_id)
    branch = branch_for(feature_id)
    trees: list[Tree] = []

    for repo in workspace.repos:
        path = workspace.worktree_path(feature_id, repo)
        if path.is_relative_to(repo.path):
            raise WorktreeError(
                f"worktree {path} would live inside the repository {repo.path}. "
                "Set VORFLUX_HOME to a directory outside your projects."
            )
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            if git.branch_exists(repo.path, branch):
                git.git(repo.path, "worktree", "add", str(path), branch)
            else:
                git.git(repo.path, "worktree", "add", "-b", branch, str(path), repo.base_branch)
        trees.append(Tree(repo=repo, path=path, branch=branch))

    return FeatureTrees(root=root, trees=trees)


def unintegrated(tree: Tree) -> list[str]:
    return git.commits_ahead(tree.repo.path, tree.base, tree.branch)


def drift(tree: Tree) -> int:
    return git.commits_behind(tree.repo.path, tree.base, tree.branch)


def touched(trees: FeatureTrees) -> list[Tree]:
    """Only the repos this feature actually changed.

    A workspace may hold repos a given feature never touches; those should not appear in the diff,
    the review, or the set of branches a reviewer is told must land together.
    """
    return [t for t in trees if unintegrated(t) or git.is_dirty(t.path)]


def combined_diff(trees: FeatureTrees) -> str:
    """One diff across every changed repo, each section labelled with its repo."""
    parts = []
    for tree in touched(trees):
        body = git.git(tree.path, "diff", f"{tree.base}...HEAD", check=False)
        if body.strip():
            header = f"===== repo: {tree.repo.name} ({tree.base}...{tree.branch}) ====="
            parts.append(f"{header}\n{body}")
    return "\n\n".join(parts)


def changed_files(trees: FeatureTrees) -> list[str]:
    files: list[str] = []
    for tree in touched(trees):
        for path in git.changed_files(tree.path, tree.base):
            files.append(f"{tree.repo.name}/{path}" if len(trees) > 1 else path)
    return files


def participating(trees: FeatureTrees) -> list[Tree]:
    """Repos where this feature's branch exists at all.

    Distinct from `touched`, which is about *current* divergence. Once a branch is merged it no
    longer diverges, so divergence cannot tell "never had commits" apart from "had commits, now
    merged" — which is exactly the question `has_landed` asks.
    """
    return [t for t in trees if git.branch_exists(t.repo.path, t.branch)]


def has_landed(trees: FeatureTrees) -> bool:
    """True once EVERY repo carrying this feature has its branch in base.

    A half-merged feature is not delivered. Reporting it as done is how the other half gets
    forgotten — which, for a change spanning an API and its caller, means a broken deploy.

    Ask this only of a feature that actually produced commits; a planned-but-never-executed
    feature has empty branches, which are trivially "integrated".
    """
    branches = participating(trees)
    return bool(branches) and not any(unintegrated(t) for t in branches)


def teardown(trees: FeatureTrees, force: bool = False) -> dict[str, Teardown]:
    """Remove every worktree, refusing any repo that still holds work.

    Per repo, and independently: one repo's work being merged is no reason to delete another's.
    """
    results: dict[str, Teardown] = {}
    for tree in trees.trees:
        results[tree.repo.name] = _teardown_one(tree, force)
    return results


def _teardown_one(tree: Tree, force: bool) -> Teardown:
    if not tree.path.exists():
        return Teardown(removed=False, reason="worktree already gone")

    if not force:
        pending = unintegrated(tree)
        if pending:
            return Teardown(
                removed=False,
                reason=f"{len(pending)} commit(s) not in {tree.base}",
                recovery=f"git -C {tree.repo.path} worktree remove {tree.path}",
            )
        if git.is_dirty(tree.path):
            return Teardown(
                removed=False,
                reason="uncommitted or untracked changes in the worktree",
                recovery=f"cd {tree.path}",
            )

    git.git(tree.repo.path, "worktree", "remove", str(tree.path))
    # -d, never -D: git refusing to delete a branch with unmerged commits is a safety net.
    git.git(tree.repo.path, "branch", "-d", tree.branch, check=False)
    return Teardown(removed=True, reason="work is integrated into base")


def prune(workspace: Workspace) -> None:
    for repo in workspace.repos:
        git.git(repo.path, "worktree", "prune", check=False)
