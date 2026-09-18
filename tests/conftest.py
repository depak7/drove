"""Shared workspace fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from drove import config, db, landed, workspace
from drove.vcs import git


@pytest.fixture(autouse=True)
def clear_landed_checks():
    landed._checked.clear()
    yield
    landed._checked.clear()


def make_repo(root: Path, name: str, default_branch: str = "main") -> Path:
    path = root / name
    path.mkdir(parents=True)
    for args in (
        ["init", "-q", "-b", default_branch],
        ["config", "user.email", "a@b.c"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    (path / f"{name}.py").write_text("x = 1\n")
    git.git(path, "add", "-A")
    git.git(path, "commit", "-qm", "init")
    return path


@pytest.fixture
def state(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(config, "HOME", tmp_path / "state")
    return tmp_path


@pytest.fixture
def solo(state):
    """A one-repo workspace — the ordinary case."""
    repo = make_repo(state / "projects", "api")
    with db.connect() as conn:
        return workspace.create(conn, "solo", [repo])


@pytest.fixture
def duo(state):
    """A two-repo workspace, where a feature can span an API and its caller."""
    api = make_repo(state / "projects", "api")
    web = make_repo(state / "projects", "web")
    with db.connect() as conn:
        return workspace.create(conn, "product", [api, web])
