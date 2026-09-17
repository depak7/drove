"""Workspaces: a named set of repositories worked on together.

A feature belongs to a workspace and gets a worktree in *every* repo, all under one feature root,
so the agent sees them as sibling directories and can change an API in one repo and its caller in
another within a single change.

    ~/.drove/worktrees/<workspace>/<feature-id>/
        api/     ← worktree of repo A on branch vf/<feature-id>
        web/     ← worktree of repo B on the same branch

One repo is the ordinary case and is simply N=1; every operation here is a loop over repos so
there is no separate single-repo path to keep in sync.

**Cross-repo changes cannot merge atomically.** If a feature changes an API in one repo and its
caller in another, merging one branch without the other breaks things. Nothing here can fix that —
the evidence pack and UI present the branches as a set that has to land together.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from drove import config, db
from drove.config import DEFAULT_HARNESSES, RepoConfig, slug
from drove.vcs import git


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Repo:
    path: Path
    name: str
    base_branch: str

    @property
    def config(self) -> RepoConfig:
        return RepoConfig.load(self.path)


@dataclass
class Workspace:
    id: str
    name: str
    repos: list[Repo] = field(default_factory=list)
    harness: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HARNESSES))
    # stage -> model id, or absent/None meaning "whatever that CLI defaults to"
    models: dict[str, str | None] = field(default_factory=dict)

    def model_for(self, stage: str) -> str | None:
        return self.models.get(stage) or None

    @property
    def root(self) -> Path:
        return config.HOME / "worktrees" / slug(self.name)

    def feature_root(self, feature_id: str) -> Path:
        """Where every repo's worktree for one feature lives, side by side."""
        root = (config.HOME / "worktrees").resolve()
        target = (self.root / slug(feature_id)).resolve()
        # Feature ids and workspace names can originate from model or user input; a name like
        # "../.ssh" must not escape the state directory.
        if not target.is_relative_to(root):
            raise WorkspaceError(f"feature path escapes {root}: {target}")
        return target

    def worktree_path(self, feature_id: str, repo: Repo) -> Path:
        return self.feature_root(feature_id) / slug(repo.name)

    @property
    def verify(self) -> dict[str, dict[str, str]]:
        """Each repo's own verify commands, keyed by repo name."""
        return {repo.name: repo.config.verify for repo in self.repos if repo.config.verify}


def _row_to_workspace(conn, row) -> Workspace:
    repos = [
        Repo(path=Path(r["path"]), name=r["name"], base_branch=r["base_branch"])
        for r in db.workspace_repos(conn, row["id"])
    ]
    harness = dict(DEFAULT_HARNESSES)
    if row["harness"]:
        harness.update(json.loads(row["harness"]))
    models = json.loads(row["models"]) if row["models"] else {}
    return Workspace(
        id=row["id"], name=row["name"], repos=repos, harness=harness, models=models
    )


def get(conn, ref: str) -> Workspace | None:
    row = db.find_workspace(conn, ref)
    return _row_to_workspace(conn, row) if row else None


def load_all(conn) -> list[Workspace]:
    return [_row_to_workspace(conn, row) for row in db.list_workspaces(conn)]


def create(conn, name: str, repos: list[Path] | None = None) -> Workspace:
    if not name.strip():
        raise WorkspaceError("a workspace needs a name")
    workspace_id = db.create_workspace(conn, name.strip())
    for path in repos or []:
        attach(conn, workspace_id, path)
    row = db.get_workspace(conn, workspace_id)
    return _row_to_workspace(conn, row)


def attach(conn, workspace_id: str, path: Path, name: str | None = None) -> Repo:
    """Add a repository to a workspace.

    The base branch is detected rather than assumed: a repo whose default is `master`, `develop`
    or anything else should not silently get branches cut from a `main` that does not exist.
    """
    path = path.expanduser().resolve()
    if not path.exists():
        raise WorkspaceError(f"{path} does not exist")
    if not git.is_repo(path):
        raise WorkspaceError(f"{path} is not a git repository")

    base = RepoConfig.load(path).base_branch
    if not git.branch_exists(path, base):
        detected = git.current_branch(path)
        if detected and detected != "HEAD":
            base = detected
        else:
            raise WorkspaceError(f"cannot determine a base branch for {path}")

    repo = Repo(path=path, name=name or path.name, base_branch=base)
    db.add_repo(conn, workspace_id, repo.path, repo.name, repo.base_branch)
    return repo


def detach(conn, workspace_id: str, path: Path) -> None:
    db.remove_repo(conn, workspace_id, path.expanduser().resolve())
