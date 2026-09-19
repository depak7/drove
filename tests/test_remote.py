"""Publishing a branch, and building a link to it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from drove import db, workspace as ws_mod
from drove.vcs import git, remote, tree as trees_mod

from .conftest import make_repo


# --- reading a remote ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "git@github.com:me/thing.git",
            "https://github.com/me/thing/compare/main...dv/abc?expand=1",
        ),
        (
            "https://github.com/me/thing.git",
            "https://github.com/me/thing/compare/main...dv/abc?expand=1",
        ),
        ("https://github.com/me/thing", "https://github.com/me/thing/compare/main...dv/abc?expand=1"),
        ("ssh://git@github.com/me/thing.git", "https://github.com/me/thing/compare/main...dv/abc?expand=1"),
        ("git@gitlab.com:group/sub/thing.git", "https://gitlab.com/group/sub/thing/-/compare/main...dv/abc"),
        # A host we cannot build a compare page for still pushes; it just gets no link.
        ("git@git.internal.example:me/thing.git", None),
        ("/srv/mirrors/thing.git", None),
    ],
)
def test_compare_links_are_built_only_for_hosts_we_know(url, expected):
    assert remote.web_url(url, "main", "dv/abc") == expected


def test_an_ssh_alias_resolves_to_the_real_host(tmp_path, monkeypatch):
    """`git@github-personal:me/x.git` is a second key, not a second website.

    Taking the alias literally would build a link to a host that does not exist — the exact shape
    anyone with a work and a personal GitHub account uses.
    """
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "config").write_text(
        "Host github-personal\n  HostName github.com\n  IdentityFile ~/.ssh/personal\n"
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    assert remote.web_url("git@github-personal:me/thing.git", "main", "dv/abc") == (
        "https://github.com/me/thing/compare/main...dv/abc?expand=1"
    )


def test_an_unknown_alias_is_left_alone(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "config").write_text("Host other\n  HostName gitlab.com\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    assert remote.web_url("git@mystery:me/thing.git", "main", "dv/abc") is None


# --- pushing ------------------------------------------------------------------------------------

@pytest.fixture
def feature(state):
    """A one-repo workspace with a feature branch carrying a commit."""
    repo = make_repo(state / "projects", "api")
    with db.connect() as conn:
        space = ws_mod.create(conn, "solo", [repo])
    trees = trees_mod.create(space, "abc123", "dv/abc123")
    tree = trees.trees[0]
    (tree.path / "new.py").write_text("y = 2\n")
    git.git(tree.path, "add", "-A")
    git.git(tree.path, "commit", "-qm", "work")
    return tree


def test_a_repo_with_no_remote_is_skipped_not_failed(feature):
    """Most local projects have no remote. That is not an error, and must not read as one."""
    result = remote.push(feature)

    assert result.pushed is False
    assert result.error is None
    assert "no 'origin' remote" in result.skipped


def test_pushing_publishes_the_branch_and_sets_upstream(feature, tmp_path):
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", str(upstream)], check=True)
    git.git(feature.path, "remote", "add", "origin", str(upstream))

    result = remote.push(feature)

    assert result.pushed is True
    assert result.error is None
    # The branch is really there, with the commit on it.
    landed = subprocess.run(
        ["git", "-C", str(upstream), "rev-parse", "dv/abc123"],
        capture_output=True, text=True,
    )
    assert landed.returncode == 0
    assert git.git(feature.path, "rev-parse", "dv/abc123") == landed.stdout.strip()
    # A bare path is not a website, so there is no link to offer — and none is invented.
    assert result.url is None


def test_a_failed_push_reports_why_and_does_not_raise(feature, tmp_path):
    """A run that passed review and tests must not be recorded as failed because a push did."""
    git.git(feature.path, "remote", "add", "origin", str(tmp_path / "nowhere.git"))

    result = remote.push(feature)

    assert result.pushed is False
    assert result.error
    assert result.skipped is None


def test_pushing_refuses_to_clobber_someone_elses_work_on_the_branch(feature, tmp_path):
    """--force-with-lease: rewriting our own fix rounds is fine, overwriting a person is not."""
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", str(upstream)], check=True)
    git.git(feature.path, "remote", "add", "origin", str(upstream))
    assert remote.push(feature).pushed

    # Someone else pushes to the branch, and we rewrite ours without seeing theirs.
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(upstream), str(other)], check=True)
    for args in (["config", "user.email", "o@b.c"], ["config", "user.name", "o"],
                 ["checkout", "-q", "dv/abc123"]):
        subprocess.run(["git", "-C", str(other), *args], check=True)
    (other / "theirs.py").write_text("z = 3\n")
    git.git(other, "add", "-A")
    git.git(other, "commit", "-qm", "theirs")
    git.git(other, "push", "-q", "origin", "dv/abc123")

    git.git(feature.path, "commit", "-q", "--amend", "-m", "reworded")
    result = remote.push(feature)

    assert result.pushed is False
    assert result.error


def test_the_reported_reason_is_the_cause_not_the_advice_after_it():
    """git follows its complaint with boilerplate; the last line of a push failure is useless."""
    proc = subprocess.CompletedProcess(
        args=[], returncode=128, stdout="",
        stderr=(
            "git@github.com: Permission denied (publickey).\n"
            "fatal: Could not read from remote repository.\n"
            "\n"
            "Please make sure you have the correct access rights\n"
            "and the repository exists.\n"
        ),
    )
    assert remote._why(proc) == "git@github.com: Permission denied (publickey)."
