"""CLI surface tests.

`--version` as a flag and `version` as a subcommand must both work: adding an `@app.callback()`
to a Typer app that had none changes how the root group is built, so subcommand dispatch is
asserted here rather than assumed.
"""

from __future__ import annotations

from typer.testing import CliRunner

from drove import __version__, db
from drove.cli import app
from drove.vcs import git, tree

runner = CliRunner()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_version_subcommand_matches_flag():
    assert runner.invoke(app, ["version"]).stdout == runner.invoke(app, ["--version"]).stdout


def test_callback_did_not_break_subcommand_dispatch():
    help_text = runner.invoke(app, ["--help"]).stdout
    for command in ("init", "doctor", "run", "serve", "version"):
        assert command in help_text


def test_run_rejects_unimplemented_stage(tmp_path):
    (tmp_path / ".git").mkdir()
    result = runner.invoke(app, ["run", "x", "-C", str(tmp_path), "--stage", "execute"])
    assert result.exit_code == 2


def test_init_refuses_non_git_directory(tmp_path):
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 1


def test_reclaim_removes_a_merged_features_worktrees(duo):
    trees = tree.create(duo, "task-1", "feat/task-1")
    for worktree in trees:
        (worktree.path / "feature.py").write_text("y = 2\n")
        git.git(worktree.path, "add", "-A")
        git.git(worktree.path, "commit", "-qm", f"change {worktree.repo.name}")
        git.git(worktree.repo.path, "merge", "--no-edit", "-q", worktree.branch)
    primary = trees.trees[0]
    with db.connect() as conn:
        db.create_feature(
            conn,
            primary.repo.path,
            "merged feature",
            trees.branch,
            trees.root,
            primary.base,
            feature_id="task-1",
            workspace_id=duo.id,
        )
        db.set_feature_status(conn, "task-1", "delivered")

    preview = runner.invoke(app, ["reclaim", "--workspace", duo.id, "--dry-run"])
    assert preview.exit_code == 0
    assert "would reclaim" in preview.stdout
    assert trees.root.exists()

    result = runner.invoke(app, ["reclaim", "--workspace", duo.id])

    assert result.exit_code == 0, result.stdout
    assert "1 feature(s) reclaimed" in result.stdout
    assert not trees.root.exists()
