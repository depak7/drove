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


STATUS_COLOUR = {
    "delivered": typer.colors.GREEN,
    "needs_human": typer.colors.YELLOW,
    "verify_failed": typer.colors.RED,
    "no_changes": typer.colors.YELLOW,
}


def render_outcome(outcome, wt, repo, pack) -> None:
    """The end of a run: what happened, and where to look next."""
    typer.echo("")
    colour = STATUS_COLOUR.get(outcome.status, typer.colors.WHITE)
    typer.secho(outcome.status.replace("_", " ").upper(), fg=colour, bold=True)
    if outcome.note:
        typer.secho(f"  {outcome.note}", fg=colour)

    for i, review in enumerate(outcome.reviews, 1):
        mark = "pass" if review.verdict == "pass" else f"{len(review.blocking)} blocking"
        typer.echo(f"  review {i}: {mark}")
        for issue in review.blocking:
            where = issue.file + (f":{issue.line}" if issue.line else "")
            typer.secho(f"      {where} — {issue.why[:100]}", fg=DIM)

    if outcome.verify and outcome.verify.ran:
        for check in outcome.verify.checks:
            state = "pass" if check.ok else f"FAIL ({check.exit_code})"
            typer.echo(f"  {check.name}: {state}")

    dollars = f" · ${outcome.cost_usd:.4f}" if outcome.cost_usd is not None else ""
    typer.secho(
        f"  {outcome.tokens_in:,} in / {outcome.tokens_out:,} out{dollars}", fg=DIM
    )
    typer.echo("")
    typer.secho(f"  evidence: {pack}", fg=DIM)
    typer.secho(f"  diff:     git -C {repo} diff {wt.base}..{wt.branch}", fg=DIM)
    typer.secho(f"  tree:     {wt.path}", fg=DIM)
