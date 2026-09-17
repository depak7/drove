"""CLI surface tests.

`--version` as a flag and `version` as a subcommand must both work: adding an `@app.callback()`
to a Typer app that had none changes how the root group is built, so subcommand dispatch is
asserted here rather than assumed.
"""

from __future__ import annotations

from typer.testing import CliRunner

from vorflux import __version__
from vorflux.cli import app

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
