"""vorflux command line."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import typer

from vorflux import __version__, db, evidence, ui
from vorflux.config import HOME, REPO_CONFIG, TEMPLATE, RepoConfig
from vorflux.harness import registry
from vorflux.pipeline.engine import RunOutcome, run_cycle
from vorflux.pipeline.schemas import PlanDoc
from vorflux.pipeline.stages.execute import StageError as ExecuteError
from vorflux.pipeline.stages.execute import run_execute
from vorflux.pipeline.stages.plan import StageError, run_plan
from vorflux.pipeline.stages.review import StageError as ReviewError
from vorflux.vcs import git, worktree

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


def _open_repo(repo: Path) -> tuple[Path, RepoConfig]:
    repo = repo.resolve()
    if not git.is_repo(repo):
        _err(f"{repo} is not a git repository")
        raise typer.Exit(1)
    return repo, RepoConfig.load(repo)


def _plan_loop(
    task: str,
    cfg: RepoConfig,
    wt,
    run_id: str,
    feature_id: str,
    yes: bool,
) -> bool:
    """Plan, then approve / decline / revise. Returns True once a plan is approved.

    A revision resumes the planner's session: it already spent real tokens reading the codebase,
    and "use PKCE instead" should adjust that understanding rather than rebuild it.
    """
    session: str | None = None
    feedback: str | None = None

    while True:
        try:
            outcome = asyncio.run(
                run_plan(
                    task,
                    cfg,
                    run_id=run_id,
                    on_event=ui.stream_line,
                    cwd=wt.path,
                    resume_session=session,
                    feedback=feedback,
                )
            )
        except (StageError, KeyError) as exc:
            _err(str(exc))
            raise typer.Exit(1) from exc

        session = outcome.session_id
        ui.render_plan(outcome.plan)

        with db.connect() as conn:
            db.set_run_plan(conn, run_id, outcome.plan.model_dump())
            db.record_session(
                conn, run_id, "plan", cfg.harness["plan"], session, wt.path,
                tokens_in=outcome.tokens_in, tokens_out=outcome.tokens_out,
                cost_usd=outcome.cost_usd,
            )

        if yes:
            return True

        answer = typer.prompt("[a]pprove  [d]ecline  or type feedback", default="a").strip()
        if answer.lower() in ("a", "approve", "y", "yes"):
            return True
        if answer.lower() in ("d", "decline", "n", "no", "q"):
            return False
        feedback = answer


@app.command(name="plan")
def plan_cmd(
    task: str = typer.Argument(..., help="What you want built"),
    repo: Path = typer.Option(Path("."), "--repo", "-C", help="Repository root"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve the first plan without asking"),
) -> None:
    """Plan a new feature, iterate on it, then approve it to run."""
    repo, cfg = _open_repo(repo)
    worktree.prune(repo)

    feature_id = db.new_id()
    wt = worktree.create(repo, feature_id, cfg.base_branch)
    with db.connect() as conn:
        db.create_feature(
            conn, repo, task, wt.branch, wt.path, cfg.base_branch, feature_id=feature_id
        )
        run_id, _ = db.create_run(conn, feature_id, task)

    typer.secho(f"feature {feature_id}  branch {wt.branch}", fg=ui.DIM)
    typer.secho(f"worktree {wt.path}", fg=ui.DIM)

    if not _plan_loop(task, cfg, wt, run_id, feature_id, yes):
        result = worktree.teardown(wt)
        with db.connect() as conn:
            db.finish_run(conn, run_id, "declined")
            if result.removed:
                db.delete_feature(conn, feature_id)
            else:
                db.set_feature_status(conn, feature_id, "abandoned")
        typer.secho(
            "declined — worktree removed" if result.removed
            else f"declined — worktree kept: {result.reason}",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(0)

    with db.connect() as conn:
        db.set_feature_status(conn, feature_id, "approved")
    typer.secho(f"approved — run it with:  vorflux execute {feature_id}", fg=typer.colors.GREEN)


@app.command(name="pivot")
def pivot_cmd(
    feature: str = typer.Argument(..., help="Feature id or branch"),
    intent: str = typer.Argument(..., help="What to change about it"),
    repo: Path = typer.Option(Path("."), "--repo", "-C", help="Repository root"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve the first plan without asking"),
) -> None:
    """Change direction on an existing feature.

    Delivered is a resting state, not a terminal one. A pivot plans against the feature's own
    worktree — so the planner sees what was already built, not just the base branch — and appends
    another run to the same branch. The executor session carries over, so the agent still knows
    why it built things the way it did.
    """
    repo, cfg = _open_repo(repo)

    with db.connect() as conn:
        row = db.find_feature(conn, feature)
        if row is None:
            _err(f"no feature matching {feature!r}")
            raise typer.Exit(1)
        feature_id = row["id"]

    wt = worktree.create(repo, feature_id, row["base_branch"])
    _check_drift(wt, yes)

    with db.connect() as conn:
        run_id, iteration = db.create_run(conn, feature_id, intent)
        db.set_feature_status(conn, feature_id, "planning")

    typer.secho(f"feature {feature_id}  iteration {iteration}  branch {wt.branch}", fg=ui.DIM)

    if not _plan_loop(intent, cfg, wt, run_id, feature_id, yes):
        # Never tear down on a declined pivot: earlier iterations' work is on this branch.
        with db.connect() as conn:
            db.finish_run(conn, run_id, "declined")
            db.set_feature_status(conn, feature_id, "executed")
        typer.secho("declined — earlier work left untouched", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    with db.connect() as conn:
        db.set_feature_status(conn, feature_id, "approved")
    typer.secho(f"approved — run it with:  vorflux execute {feature_id}", fg=typer.colors.GREEN)


def _check_drift(wt, assume_ignore: bool = False) -> None:
    """Surface base drift and let the user decide. Never rebase silently.

    An unattended rebase that hits conflicts mid-automation is a bad failure to discover later.
    """
    behind = worktree.drift(wt)
    if not behind:
        return
    typer.secho(
        f"  ! {wt.base} has advanced {behind} commit(s) since this feature branched",
        fg=typer.colors.YELLOW,
    )
    if assume_ignore:
        return
    choice = typer.prompt(
        "  [r]ebase onto it  [m]erge it in  [i]gnore", default="i"
    ).strip().lower()
    if choice.startswith("r"):
        git.git(wt.path, "rebase", wt.base)
        typer.secho(f"  rebased onto {wt.base}", fg=typer.colors.GREEN)
    elif choice.startswith("m"):
        git.git(wt.path, "merge", "--no-edit", wt.base)
        typer.secho(f"  merged {wt.base}", fg=typer.colors.GREEN)


@app.command(name="execute")
def execute_cmd(
    feature: str = typer.Argument(..., help="Feature id or branch"),
    repo: Path = typer.Option(Path("."), "--repo", "-C", help="Repository root"),
    no_review: bool = typer.Option(False, "--no-review", help="Implement only; skip the cycle"),
) -> None:
    """Run the full cycle on an approved plan: implement, review, fix, verify, deliver."""
    repo, cfg = _open_repo(repo)

    with db.connect() as conn:
        row = db.find_feature(conn, feature)
        if row is None:
            _err(f"no feature matching {feature!r}")
            raise typer.Exit(1)
        runs = db.list_runs(conn, row["id"])
        prior = db.last_session(conn, row["id"], "execute")

    latest = next((r for r in reversed(runs) if r["plan_json"]), None)
    if latest is None:
        _err("that feature has no approved plan yet — run `vorflux plan` first")
        raise typer.Exit(1)

    plan = PlanDoc.model_validate(json.loads(latest["plan_json"]))
    wt = worktree.create(repo, row["id"], row["base_branch"])
    _check_drift(wt, assume_ignore=True)

    if not no_review and cfg.harness["review"] == cfg.harness["execute"]:
        typer.secho(
            f"  ! review and execute are both {cfg.harness['execute']} — a harness reviewing its"
            " own work is not an independent review",
            fg=typer.colors.YELLOW,
        )

    resume = prior["session_id"] if prior else None
    if resume:
        typer.secho(f"  resuming executor session {resume[:8]}", fg=ui.DIM)

    with db.connect() as conn:
        db.set_feature_status(conn, row["id"], "executing")

    def report(stage: str, message: str) -> None:
        typer.secho(f"\n▸ {stage}: {message}", fg=typer.colors.CYAN, err=True)

    try:
        if no_review:
            executed = asyncio.run(
                run_execute(
                    plan, wt, cfg, latest["id"], latest["intent"],
                    resume_session=resume, on_event=ui.stream_line,
                )
            )
            outcome = RunOutcome(
                status="delivered" if executed.committed else "no_changes",
                execute=executed,
                files_changed=list(executed.files_changed),
                sessions={"execute": executed.session_id},
                cost_usd=executed.cost_usd,
                tokens_in=executed.tokens_in,
                tokens_out=executed.tokens_out,
            )
        else:
            outcome = asyncio.run(
                run_cycle(
                    plan, wt, cfg, latest["id"], latest["intent"],
                    resume_session=resume, on_event=ui.stream_line, report=report,
                )
            )
    except (StageError, ExecuteError, ReviewError, KeyError) as exc:
        with db.connect() as conn:
            db.finish_run(conn, latest["id"], "failed")
            db.set_feature_status(conn, row["id"], "failed")
        _err(str(exc))
        raise typer.Exit(1) from exc

    head_sha = outcome.execute.head_sha if outcome.execute else None

    pack = evidence.write(
        evidence.Evidence(
            run_id=latest["id"],
            feature_id=row["id"],
            iteration=latest["iteration"],
            intent=latest["intent"],
            branch=wt.branch,
            base=wt.base,
            plan=plan,
            head_sha=head_sha,
            files_changed=outcome.files_changed,
            reviews=outcome.reviews,
            verify=outcome.verify,
            cost_usd=outcome.cost_usd,
            tokens_in=outcome.tokens_in,
            tokens_out=outcome.tokens_out,
            sessions=outcome.sessions,
            status=outcome.status,
        )
    )

    with db.connect() as conn:
        for stage, session_id in outcome.sessions.items():
            base_stage, _, attempt = stage.partition("-")
            db.record_session(
                conn, latest["id"], base_stage,
                cfg.harness.get(base_stage, cfg.harness["execute"]),
                session_id, wt.path, attempt=int(attempt or 1),
            )
        db.finish_run(conn, latest["id"], outcome.status, head_sha=head_sha)
        db.set_feature_status(conn, row["id"], outcome.status)

    ui.render_outcome(outcome, wt, repo, pack)


@app.command(name="features")
def features_cmd(
    repo: Path = typer.Option(Path("."), "--repo", "-C", help="Repository root"),
    all_repos: bool = typer.Option(False, "--all", help="Every repo, not just this one"),
) -> None:
    """List features and their state."""
    target = None if all_repos else Path(repo).resolve()
    with db.connect() as conn:
        rows = db.list_features(conn, target)
        if not rows:
            typer.secho("no features yet — start with `vorflux plan \"...\"`", fg=ui.DIM)
            return
        for row in rows:
            runs = db.list_runs(conn, row["id"])
            status = row["status"]
            colour = {
                "executed": typer.colors.GREEN,
                "failed": typer.colors.RED,
                "abandoned": typer.colors.YELLOW,
            }.get(status, typer.colors.WHITE)
            typer.secho(f"  {row['id']}  {status:<10}", fg=colour, nl=False)
            typer.echo(f"{row['title'][:56]}")
            typer.secho(
                f"        {row['branch']}  ·  {len(runs)} run(s)", fg=ui.DIM
            )


@app.command()
def serve() -> None:
    """Start the daemon and web UI (M4)."""
    _err("serve lands in M4; use `vorflux run` for now")
    raise typer.Exit(2)


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(app())
