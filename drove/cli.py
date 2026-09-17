"""drove command line."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import typer

from drove import __version__, config, db, evidence, ui
from drove.config import HOME, REPO_CONFIG, TEMPLATE, provisional_title
from drove.harness import registry
from drove.pipeline.engine import RunOutcome, run_cycle
from drove.pipeline.schemas import PlanDoc
from drove.pipeline.stages.execute import StageError as ExecuteError
from drove.pipeline.stages.execute import run_execute
from drove.pipeline.stages.plan import StageError, run_plan
from drove.pipeline.stages.review import StageError as ReviewError
from drove import workspace
from drove.vcs import git, tree

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
    """Write a .drove.toml into a repository."""
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
    typer.echo(f"drove {__version__}   state: {HOME}")
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
        _err("no usable harness found; drove cannot run a stage")
        raise typer.Exit(1)

    if len(usable) < 2:
        typer.secho(
            "  only one harness usable — cross-model review needs a second one",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho(f"  cross-model review available: {', '.join(usable)}", fg=typer.colors.GREEN)



def _resolve_workspace(ref: str | None) -> workspace.Workspace:
    """Find the workspace by reference, or infer it when there is only one."""
    with db.connect() as conn:
        if ref:
            ws = workspace.get(conn, ref)
            if ws is None:
                _err(f"no workspace matching {ref!r}")
                raise typer.Exit(1)
            return ws
        found = workspace.load_all(conn)
    if len(found) == 1:
        return found[0]
    if not found:
        _err("no workspaces yet — create one with `drove workspace new <name> <repo>`")
        raise typer.Exit(1)
    _err("several workspaces exist; pass --workspace")
    for ws in found:
        typer.secho(f"  {ws.id}  {ws.name}", fg=ui.DIM, err=True)
    raise typer.Exit(1)


ws_app = typer.Typer(help="Manage workspaces — the set of repos a feature may change.")
app.add_typer(ws_app, name="workspace")


@ws_app.command("new")
def workspace_new(
    name: str = typer.Argument(..., help="Workspace name"),
    repos: list[Path] = typer.Argument(None, help="Repositories to include"),
) -> None:
    """Create a workspace, optionally with its repos."""
    with db.connect() as conn:
        try:
            ws = workspace.create(conn, name, list(repos or []))
        except workspace.WorkspaceError as exc:
            _err(str(exc))
            raise typer.Exit(1) from exc
    typer.secho(f"{ws.id}  {ws.name}", fg=typer.colors.GREEN)
    for repo in ws.repos:
        typer.secho(f"  {repo.name:<16} {repo.path}  ({repo.base_branch})", fg=ui.DIM)


@ws_app.command("add")
def workspace_add(
    repo: Path = typer.Argument(..., help="Repository to add"),
    ws_ref: str = typer.Option(None, "--workspace", "-w", help="Workspace id or name"),
) -> None:
    """Add a repository to a workspace."""
    ws = _resolve_workspace(ws_ref)
    with db.connect() as conn:
        try:
            added = workspace.attach(conn, ws.id, repo)
        except workspace.WorkspaceError as exc:
            _err(str(exc))
            raise typer.Exit(1) from exc
    typer.secho(f"added {added.name} ({added.base_branch}) to {ws.name}", fg=typer.colors.GREEN)


@ws_app.command("list")
def workspace_list() -> None:
    """List workspaces and their repositories."""
    with db.connect() as conn:
        found = workspace.load_all(conn)
        if not found:
            typer.secho("no workspaces yet", fg=ui.DIM)
            return
        for ws in found:
            count = len(db.features_in(conn, ws.id))
            typer.secho(f"{ws.id}  {ws.name}", bold=True, nl=False)
            typer.secho(f"   {count} feature(s)", fg=ui.DIM)
            for repo in ws.repos:
                mark = "" if repo.path.exists() else "  (missing)"
                typer.secho(
                    f"  {repo.name:<16} {repo.path} ({repo.base_branch}){mark}", fg=ui.DIM
                )


def _plan_loop(task: str, ws, trees, run_id: str, feature_id: str, yes: bool) -> bool:
    """Plan, then approve / decline / revise. True once a plan is approved.

    A revision resumes the planner's session: it already paid to read this codebase, so feedback
    should adjust that understanding rather than rebuild it.
    """
    session: str | None = None
    feedback: str | None = None

    while True:
        try:
            outcome = asyncio.run(
                run_plan(
                    task, ws, trees, run_id=run_id, on_event=ui.stream_line,
                    resume_session=session, feedback=feedback,
                )
            )
        except (StageError, KeyError) as exc:
            _err(str(exc))
            raise typer.Exit(1) from exc

        session = outcome.session_id
        ui.render_plan(outcome.plan)

        with db.connect() as conn:
            db.set_run_plan(conn, run_id, outcome.plan.model_dump())
            if title := outcome.plan.title.strip():
                db.set_feature_title(conn, feature_id, title[:80])
            db.record_session(
                conn, run_id, "plan", ws.harness["plan"], session, trees.root,
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
    ws_ref: str = typer.Option(None, "--workspace", "-w", help="Workspace id or name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve the first plan without asking"),
) -> None:
    """Plan a new feature, iterate on it, then approve it to run."""
    ws = _resolve_workspace(ws_ref)
    if not ws.repos:
        _err(f"workspace {ws.name!r} has no repositories — add one with `drove workspace add`")
        raise typer.Exit(1)
    tree.prune(ws)

    feature_id = db.new_id()
    trees = tree.create(ws, feature_id)
    primary = trees.trees[0]
    with db.connect() as conn:
        db.create_feature(
            conn, primary.repo.path, provisional_title(task), trees.branch, trees.root,
            primary.base, feature_id=feature_id, workspace_id=ws.id,
        )
        run_id, _ = db.create_run(conn, feature_id, task)

    typer.secho(f"feature {feature_id}  branch {trees.branch}", fg=ui.DIM)
    for t in trees:
        typer.secho(f"  {t.repo.name:<16} {t.path}", fg=ui.DIM)

    if not _plan_loop(task, ws, trees, run_id, feature_id, yes):
        results = tree.teardown(trees)
        kept = {n: r for n, r in results.items() if not r.removed}
        with db.connect() as conn:
            db.finish_run(conn, run_id, "declined")
            if kept:
                db.set_feature_status(conn, feature_id, "abandoned")
            else:
                db.delete_feature(conn, feature_id)
        if kept:
            for name, result in kept.items():
                typer.secho(f"declined — {name} kept: {result.reason}", fg=typer.colors.YELLOW)
        else:
            typer.secho("declined — worktrees removed", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    with db.connect() as conn:
        db.set_feature_status(conn, feature_id, "approved")
    typer.secho(f"approved — run it with:  drove execute {feature_id}", fg=typer.colors.GREEN)


@app.command(name="pivot")
def pivot_cmd(
    feature: str = typer.Argument(..., help="Feature id or branch"),
    intent: str = typer.Argument(..., help="What to change about it"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve the first plan without asking"),
) -> None:
    """Change direction on an existing feature.

    Delivered is a resting state, not a terminal one. A pivot plans against the feature's own
    worktrees — so the planner sees what was already built — and appends a run to the same
    branches. The executor session carries over, so the agent still knows why it built things
    the way it did.
    """
    row, ws, trees = _load_feature(feature)
    _check_drift(trees, assume_ignore=yes)

    with db.connect() as conn:
        run_id, iteration = db.create_run(conn, row["id"], intent)
        db.set_feature_status(conn, row["id"], "planning")

    typer.secho(f"feature {row['id']}  iteration {iteration}  branch {trees.branch}", fg=ui.DIM)

    if not _plan_loop(intent, ws, trees, run_id, row["id"], yes):
        # Never tear down on a declined pivot: earlier iterations' work is on these branches.
        with db.connect() as conn:
            db.finish_run(conn, run_id, "declined")
            db.set_feature_status(conn, row["id"], "executed")
        typer.secho("declined — earlier work left untouched", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    with db.connect() as conn:
        db.set_feature_status(conn, row["id"], "approved")
    typer.secho(f"approved — run it with:  drove execute {row['id']}", fg=typer.colors.GREEN)


def _load_feature(ref: str):
    with db.connect() as conn:
        row = db.find_feature(conn, ref)
        if row is None:
            _err(f"no feature matching {ref!r}")
            raise typer.Exit(1)
        ws = workspace.get(conn, row["workspace_id"] or "")
    if ws is None:
        _err(f"feature {row['id']} has no workspace")
        raise typer.Exit(1)
    return row, ws, tree.create(ws, row["id"], row["branch"])


def _check_drift(trees, assume_ignore: bool = False) -> None:
    """Surface base drift per repo and let the user decide. Never rebase silently.

    An unattended rebase that hits conflicts mid-automation is a bad thing to discover later.
    """
    drifted = [(t, tree.drift(t)) for t in trees]
    drifted = [(t, n) for t, n in drifted if n]
    if not drifted:
        return
    for t, behind in drifted:
        typer.secho(
            f"  ! {t.repo.name}: {t.base} has advanced {behind} commit(s) since this branched",
            fg=typer.colors.YELLOW,
        )
    if assume_ignore:
        return
    choice = typer.prompt(
        "  [r]ebase onto it  [m]erge it in  [i]gnore", default="i"
    ).strip().lower()
    for t, _ in drifted:
        if choice.startswith("r"):
            git.git(t.path, "rebase", t.base)
        elif choice.startswith("m"):
            git.git(t.path, "merge", "--no-edit", t.base)
    if choice[:1] in ("r", "m"):
        typer.secho("  done", fg=typer.colors.GREEN)


@app.command(name="execute")
def execute_cmd(
    feature: str = typer.Argument(..., help="Feature id or branch"),
    no_review: bool = typer.Option(False, "--no-review", help="Implement only; skip the cycle"),
) -> None:
    """Run the full cycle on an approved plan: implement, review, fix, verify, deliver."""
    row, ws, trees = _load_feature(feature)

    with db.connect() as conn:
        runs = db.list_runs(conn, row["id"])
        prior = db.last_session(conn, row["id"], "execute")

    latest = next((r for r in reversed(runs) if r["plan_json"]), None)
    if latest is None:
        _err("that feature has no approved plan yet — run `drove plan` first")
        raise typer.Exit(1)

    plan = PlanDoc.model_validate(json.loads(latest["plan_json"]))
    _check_drift(trees, assume_ignore=True)

    if not no_review and ws.harness["review"] == ws.harness["execute"]:
        typer.secho(
            f"  ! review and execute are both {ws.harness['execute']} — a harness reviewing its"
            " own work is not an independent review",
            fg=typer.colors.YELLOW,
        )

    resume = prior["session_id"] if prior else None
    if resume:
        typer.secho(f"  resuming executor session {resume[:8]}", fg=ui.DIM)

    with db.connect() as conn:
        db.set_feature_status(conn, row["id"], "executing")

    def report(stage: str, message: str) -> None:
        typer.secho(f"\n\u25b8 {stage}: {message}", fg=typer.colors.CYAN, err=True)

    try:
        if no_review:
            executed = asyncio.run(
                run_execute(
                    plan, trees, ws, latest["id"], latest["intent"],
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
                    plan, trees, ws, latest["id"], latest["intent"],
                    resume_session=resume, on_event=ui.stream_line, report=report,
                )
            )
    except (StageError, ExecuteError, ReviewError, KeyError) as exc:
        with db.connect() as conn:
            db.finish_run(conn, latest["id"], "failed")
            db.set_feature_status(conn, row["id"], "failed")
        _err(str(exc))
        raise typer.Exit(1) from exc

    head_shas = outcome.execute.head_shas if outcome.execute else {}
    pack = evidence.write(
        evidence.Evidence(
            run_id=latest["id"],
            feature_id=row["id"],
            iteration=latest["iteration"],
            intent=latest["intent"],
            branch=trees.branch,
            base=", ".join(sorted({t.base for t in trees})),
            workspace=ws.name,
            repos=outcome.repos_touched,
            head_shas=head_shas,
            head_sha=outcome.execute.head_sha if outcome.execute else None,
            plan=plan,
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
                ws.harness.get(base_stage, ws.harness["execute"]),
                session_id, trees.root, attempt=int(attempt or 1),
            )
        db.finish_run(
            conn, latest["id"], outcome.status,
            head_sha=outcome.execute.head_sha if outcome.execute else None,
        )
        db.set_feature_status(conn, row["id"], outcome.status)

    ui.render_outcome(outcome, trees, pack)


@app.command(name="features")
def features_cmd(
    ws_ref: str = typer.Option(None, "--workspace", "-w", help="Workspace id or name"),
    all_workspaces: bool = typer.Option(False, "--all", help="Every workspace"),
) -> None:
    """List features and their state."""
    with db.connect() as conn:
        rows = (
            db.list_features(conn)
            if all_workspaces
            else db.features_in(conn, _resolve_workspace(ws_ref).id)
        )
        if not rows:
            typer.secho('no features yet — start with `drove plan "..."`', fg=ui.DIM)
            return
        for row in rows:
            runs = db.list_runs(conn, row["id"])
            colour = {
                "delivered": typer.colors.GREEN,
                "failed": typer.colors.RED,
                "verify_failed": typer.colors.RED,
                "needs_human": typer.colors.YELLOW,
                "abandoned": typer.colors.YELLOW,
            }.get(row["status"], typer.colors.WHITE)
            typer.secho(f"  {row['id']}  {row['status']:<16}", fg=colour, nl=False)
            typer.echo(f"{row['title'][:56]}")
            typer.secho(f"        {row['branch']}  \u00b7  {len(runs)} run(s)", fg=ui.DIM)


@app.command()
def serve(
    port: int = typer.Option(8787, help="Port to listen on"),
    host: str = typer.Option("127.0.0.1", help="Interface to bind"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the UI on start"),
) -> None:
    """Start the local daemon and web UI.

    Not scoped to a repository: workspaces are created and given repos from the app itself.
    """
    import uvicorn

    from drove.api import server

    with db.connect() as conn:
        for ws in workspace.load_all(conn):
            tree.prune(ws)

    url = f"http://{host}:{port}"
    typer.secho(f"drove {__version__}", bold=True)
    typer.secho(f"  {url}", fg=typer.colors.GREEN)

    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(server.build_app(), host=host, port=port, log_level="warning")


def main() -> None:
    if moved := config.migrate_home():
        typer.secho(f"moved state from {moved} to {config.HOME}", fg=ui.DIM, err=True)
    app()


if __name__ == "__main__":
    sys.exit(app())
