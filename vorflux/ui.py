"""Terminal rendering shared by the CLI commands."""

from __future__ import annotations

import typer

from vorflux.events import AssistantText, HarnessEvent, RateLimit, Result, ToolCall
from vorflux.pipeline.schemas import PlanDoc

DIM = typer.colors.BRIGHT_BLACK


def stream_line(event: HarnessEvent) -> None:
    """One dim line per interesting event, on stderr so stdout stays pipeable."""
    if isinstance(event, ToolCall):
        hint = event.input.get("file_path") or event.input.get("command") or ""
        typer.secho(f"  · {event.name} {str(hint)[:70]}", fg=DIM, err=True)
    elif isinstance(event, AssistantText) and event.text.strip():
        typer.secho(f"  {event.text.strip()[:160]}", fg=DIM, err=True)
    elif isinstance(event, RateLimit) and (event.five_hour_utilization or 0) > 0.8:
        typer.secho(
            f"  ! 5h window {event.five_hour_utilization:.0%} used",
            fg=typer.colors.YELLOW,
            err=True,
        )
    elif isinstance(event, Result) and event.cost_usd:
        typer.secho(f"  ${event.cost_usd:.4f}", fg=DIM, err=True)


def render_plan(plan: PlanDoc) -> None:
    typer.echo("")
    typer.secho(plan.summary, bold=True)
    if plan.steps:
        typer.echo("\nSteps")
        for i, step in enumerate(plan.steps, 1):
            typer.echo(f"  {i}. {step.title}")
            for f in step.files:
                typer.secho(f"       {f}", fg=DIM)
    if plan.acceptance_criteria:
        typer.echo("\nDone when")
        for c in plan.acceptance_criteria:
            typer.echo(f"  - {c}")
    if plan.risks:
        typer.echo("\nRisks")
        for r in plan.risks:
            typer.secho(f"  ! {r}", fg=typer.colors.YELLOW)
    if plan.test_plan:
        typer.echo(f"\nVerify\n  {plan.test_plan}")
    typer.echo("")
