"""A feature's worktrees — one per repo in its workspace, managed as a set.

Single-repo is N=1, not a separate code path. Every operation below is a loop, so nothing has to
be kept in sync between a "simple" and a "multi-repo" implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from drove.config import provisional_title, slug
from drove.vcs import git
from drove.workspace import Repo, Workspace


class WorktreeError(RuntimeError):
    """A worktree operation would be unsafe or cannot be completed."""


@dataclass(frozen=True)
class Teardown:
    removed: bool
    reason: str
    recovery: str | None = None


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


# What kind of change this is, inferred from the words people actually use. Ordered: the first
# match wins, so specific kinds come before the catch-all. Matched on word boundaries, because
# "prefix" is not a fix and "contest" is not a test.
BRANCH_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fix", ("fix", "bug", "broken", "crash", "regress", "fails", "failing", "error", "wrong")),
    ("docs", ("document", "documentation", "docs", "readme", "changelog")),
    ("test", ("test", "tests", "coverage", "flaky")),
    ("refactor", ("refactor", "restructure", "extract", "simplify", "clean up", "tidy")),
    ("chore", ("bump", "upgrade", "pin", "rename", "delete", "drop")),
)
DEFAULT_KIND = "feat"

# Long enough to be a sentence you recognise, short enough to read in a terminal prompt and a
# `git branch` listing without wrapping.
MAX_BRANCH_SLUG = 48


def _kind(task: str) -> str:
    """feat / fix / docs / ... from the request itself."""
    text = " ".join(task.lower().split())[:160]
    for kind, words in BRANCH_KINDS:
        # Both boundaries, plus the ordinary inflections. Leading-only matched "fixtures" as a
        # fix and would have matched "contested" as a test.
        if any(re.search(rf"\b{re.escape(word)}(s|es|ed|ing)?\b", text) for word in words):
            return kind
    return DEFAULT_KIND


def _slugify(text: str, limit: int = MAX_BRANCH_SLUG) -> str:
    """Lowercase words joined by hyphens, cut at a word boundary.

    Deliberately narrower than `config.slug`: git accepts dots and underscores in a branch name,
    but `..`, a trailing `.lock` and a leading `.` are all refused, and the rules are fiddly
    enough that restricting to [a-z0-9-] is easier to be sure about than encoding them.
    """
    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(out) > limit:
        out = out[:limit].rsplit("-", 1)[0] or out[:limit]
    return out.strip("-") or "change"


# Left stranded once the leading verb is dropped: "fix the login bug" should read `fix/login-bug`,
# and "tests for the reconciliation" should read `test/reconciliation`.
_FILLER = ("the", "a", "an", "this", "that", "my", "our", "for", "of", "to", "in", "on")


def _undouble(kind: str, title: str) -> str:
    """Drop a leading verb the prefix already says, so `fix/fix-the-x` reads `fix/x`."""
    words = title.split()
    triggers = dict(BRANCH_KINDS).get(kind, ())
    if not words or words[0].lower().rstrip(":,") not in triggers:
        return title
    words = words[1:]
    # At most two: enough for "for the", not enough to eat the subject of a terse request.
    for _ in range(2):
        if words and words[0].lower() in _FILLER:
            words = words[1:]
    return " ".join(words) or title


def branch_for(task: str) -> str:
    """A branch name a person can read: `feat/add-cancel-for-an-in-flight-run`.

    Named from the request rather than the feature id, because the branch is the thing that ends
    up in `git branch`, in a pull request title and in someone else's review queue — and
    `dv/f31c59bc0e85` tells that reader nothing at all.

    Minted once, at creation, and never renamed. The planner produces a better title a minute
    later, but by then the branch may already exist in three worktrees and on a remote, and a
    branch that changes name under you is worse than one that is merely approximate.
    """
    kind = _kind(task)
    return f"{kind}/{_slugify(_undouble(kind, provisional_title(task)))}"


def unique_branch(workspace: Workspace, task: str, feature_id: str) -> str:
    """`branch_for`, guaranteed free in every repo of the workspace.

    Two features can legitimately be asked for in the same words. The feature id disambiguates
    them, which keeps the suffix stable and meaningful rather than a counter that depends on the
    order things happened to be created in.
    """
    name = branch_for(task)
    if any(git.branch_exists(repo.path, name) for repo in workspace.repos):
        return f"{name}-{slug(feature_id)[:6]}"
    return name


def create(workspace: Workspace, feature_id: str, branch: str) -> FeatureTrees:
    """Check out every repo in the workspace side by side under one feature root.

    `branch` is always given, never derived here: a new feature's name comes from its request via
    `unique_branch`, and an existing one's comes from the database — because a branch may already
    carry commits, exist in three worktrees and be pushed, and must never be recomputed.
    """
    if not workspace.repos:
        raise WorktreeError(f"workspace {workspace.name!r} has no repositories")

    root = workspace.feature_root(feature_id)
    trees: list[Tree] = []

    for repo in workspace.repos:
        path = workspace.worktree_path(feature_id, repo)
        if path.is_relative_to(repo.path):
            raise WorktreeError(
                f"worktree {path} would live inside the repository {repo.path}. "
                "Set DROVE_HOME to a directory outside your projects."
            )
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            if git.branch_exists(repo.path, branch):
                git.git(repo.path, "worktree", "add", str(path), branch)
            else:
                git.git(repo.path, "worktree", "add", "-b", branch, str(path), repo.base_branch)
        trees.append(Tree(repo=repo, path=path, branch=branch))

    return FeatureTrees(root=root, trees=trees)


def attach(workspace: Workspace, feature_id: str, branch: str | None = None) -> FeatureTrees:
    """Describe a feature's trees without creating or changing anything on disk.

    Landed detection only asks git about branches in each repository, not about the worktree, so
    reclaimed worktrees do not need to be recreated merely to inspect their branches.
    """
    if not workspace.repos:
        raise WorktreeError(f"workspace {workspace.name!r} has no repositories")

    root = workspace.feature_root(feature_id)
    if not branch:
        raise WorktreeError(f"feature {feature_id} has no branch name")
    return FeatureTrees(
        root=root,
        trees=[
            Tree(
                repo=repo,
                path=workspace.worktree_path(feature_id, repo),
                branch=branch,
            )
            for repo in workspace.repos
        ],
    )


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


def combined_stat(trees: FeatureTrees) -> str:
    """`git diff --stat` across every changed repo, labelled when there is more than one."""
    multi = len(touched(trees)) > 1
    parts = []
    for tree in touched(trees):
        stat = git.git(tree.path, "diff", "--stat", f"{tree.base}...HEAD", check=False)
        if stat.strip():
            parts.append(f"{tree.repo.name}:\n{stat}" if multi else stat)
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
    all_gone = all(
        result.removed or result.reason == "worktree already gone"
        for result in results.values()
    )
    if all_gone:
        try:
            trees.root.rmdir()
        except OSError:
            pass
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
