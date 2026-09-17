"""vorflux command line."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import typer

from vorflux import __version__
from vorflux.config import HOME, REPO_CONFIG, TEMPLATE, RepoConfig
from vorflux.events import AssistantText, HarnessEvent, RateLimit, Result, ToolCall
from vorflux.harness import registry
from vorflux.pipeline.stages.plan import StageError, run_plan

app = typer.Typer(
    add_completion=False,
    help="Local autonomous engineering pipeline driving the coding CLIs you already have.",
)


def _err(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    _version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version"
    ),
) -> None:
    """Local autonomous engineering pipeline."""


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def init(
    repo: Path = typer.Argument(Path("."), help="Repository root"),
    base: str = typer.Option("main", help="Base branch to plan and review against"),
) -> None:
    """Write a .vorflux.toml into a repository."""
    repo = repo.resolve()
    target = repo / REPO_CONFIG
    if target.exists():
        typer.echo(f"{target} already exists — leaving it alone")
        raise typer.Exit(0)
    if not (repo / ".git").exists():
        _err(f"{repo} is not a git repository")
        raise typer.Exit(1)
    target.write_text(TEMPLATE.format(base=base))
    typer.secho(f"wrote {target}", fg=typer.colors.GREEN)


@app.command()
def doctor() -> None:
    """Report which agent CLIs are installed, authenticated and adapter-backed."""
    typer.echo(f"vorflux {__version__}   state: {HOME}")
    typer.echo("")

    usable: list[str] = []
    for name, path in registry.available().items():
        preset = registry.PRESETS.get(name)

        if path is None:
            install = preset.install if preset else registry.PLANNED.get(name, "")
            typer.secho(f"  [miss] {name:<14} not installed — {install}", fg=typer.colors.RED)
            continue

        if preset is None:
            typer.secho(
                f"  [wait] {name:<14} installed, no adapter yet", fg=typer.colors.YELLOW
            )
            continue

        auth = registry.auth_state(name)
        notes = []
        if not preset.supports_schema:
            notes.append("schema via prompt")
        suffix = f"  ({', '.join(notes)})" if notes else ""

        if auth == "logged_out":
            typer.secho(
                f"  [auth] {name:<14} installed but logged out — run `{name} login`",
                fg=typer.colors.YELLOW,
            )
            continue

        label = "ok  " if auth == "ok" else "ok? "
        colour = typer.colors.GREEN if auth == "ok" else typer.colors.BRIGHT_BLACK
        typer.secho(f"  [{label}] {name:<14} {path}{suffix}", fg=colour)
        usable.append(name)

    typer.echo("")
    if not usable:
        _err("no usable harness found; vorflux cannot run a stage")
        raise typer.Exit(1)

    if len(usable) < 2:
        typer.secho(
            "  only one harness usable — cross-model review needs a second one",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho(f"  cross-model review available: {', '.join(usable)}", fg=typer.colors.GREEN)


@app.command(name="run")
def run_cmd(
    task: str = typer.Argument(..., help="What you want built"),
    repo: Path = typer.Option(Path("."), "--repo", "-C", help="Repository root"),
    stage: str = typer.Option("plan", "--stage", help="Stage to run (plan)"),
    as_json: bool = typer.Option(False, "--json", help="Emit the raw stage object"),
) -> None:
    """Run a single pipeline stage. M0 implements `plan`."""
    if stage != "plan":
        _err(f"stage {stage!r} not implemented yet (M0 ships `plan`)")
        raise typer.Exit(2)

    repo = repo.resolve()
    if not (repo / ".git").exists():
        _err(f"{repo} is not a git repository")
        raise typer.Exit(1)

    cfg = RepoConfig.load(repo)

    def show(event: HarnessEvent) -> None:
        if as_json:
            return
        if isinstance(event, ToolCall):
            hint = event.input.get("file_path") or event.input.get("pattern") or ""
            typer.secho(
                f"  · {event.name} {str(hint)[:70]}",
                fg=typer.colors.BRIGHT_BLACK,
                err=True,
            )
        elif isinstance(event, AssistantText):
            typer.secho(f"  {event.text.strip()[:200]}", fg=typer.colors.BRIGHT_BLACK, err=True)
        elif isinstance(event, RateLimit) and (event.five_hour_utilization or 0) > 0.8:
            typer.secho(
                f"  ! 5h window {event.five_hour_utilization:.0%} used",
                fg=typer.colors.YELLOW,
                err=True,
            )
        elif isinstance(event, Result) and event.cost_usd:
            typer.secho(f"  ${event.cost_usd:.4f}", fg=typer.colors.BRIGHT_BLACK, err=True)

    try:
        outcome = asyncio.run(run_plan(task, cfg, on_event=show))
    except (StageError, KeyError) as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc

    if as_json:
        typer.echo(json.dumps(outcome.plan.model_dump(), indent=2))
        return

    p = outcome.plan
    typer.echo("")
    typer.secho(p.summary, bold=True)
    if p.steps:
        typer.echo("\nSteps")
        for i, step in enumerate(p.steps, 1):
            typer.echo(f"  {i}. {step.title}")
            for f in step.files:
                typer.secho(f"       {f}", fg=typer.colors.BRIGHT_BLACK)
    if p.acceptance_criteria:
        typer.echo("\nDone when")
        for c in p.acceptance_criteria:
            typer.echo(f"  - {c}")
    if p.risks:
        typer.echo("\nRisks")
        for r in p.risks:
            typer.secho(f"  ! {r}", fg=typer.colors.YELLOW)
    if p.test_plan:
        typer.echo(f"\nVerify\n  {p.test_plan}")
    typer.secho(f"\nlog: {outcome.raw_log}", fg=typer.colors.BRIGHT_BLACK)


@app.command()
def serve() -> None:
    """Start the daemon and web UI (M4)."""
    _err("serve lands in M4; use `vorflux run` for now")
    raise typer.Exit(2)


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(app())
