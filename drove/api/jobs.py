"""Background work for the daemon.

The CLI drives the pipeline synchronously; the daemon cannot, because the browser needs the plan
gate to be a *state*, not a blocking prompt. So planning and the run cycle become jobs: they
publish progress to the bus, park the feature in `awaiting_approval`, and let the UI decide.

Concurrency is bounded. Several features may run at once — each in its own worktree — but not
unboundedly: every concurrent run is a CLI process holding a slice of the same rate limit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from drove import db, evidence, workspace as ws_mod
from drove.api.bus import bus
from drove.events import AssistantText, HarnessEvent, RateLimit, Result, ToolCall
from drove.pipeline.engine import run_cycle
from drove.pipeline.schemas import PlanDoc
from drove.pipeline.stages.plan import run_plan
from drove.vcs import tree as trees_mod

MAX_CONCURRENT_RUNS = 2
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)


@dataclass
class Job:
    future: Future[None] | None = None
    task: asyncio.Task[None] | None = None

    def done(self) -> bool:
        return self.future is not None and self.future.done()


_active: dict[str, Job] = {}
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Give the job runner the daemon's event loop.

    Routes are sync `def` — deliberately, because they do blocking sqlite and git work that would
    stall the SSE stream if run on the loop. FastAPI therefore executes them in a threadpool,
    where `asyncio.create_task` raises "no running event loop". Jobs are scheduled onto the loop
    explicitly instead.
    """
    global _loop
    _loop = loop
    bus.bind(loop)


@dataclass
class JobError(Exception):
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


def emit(feature_id: str, kind: str, **payload: Any) -> None:
    bus.publish({"feature_id": feature_id, "kind": kind, **payload})


def _event_reporter(feature_id: str):
    def report(event: HarnessEvent) -> None:
        if isinstance(event, ToolCall):
            hint = event.input.get("file_path") or event.input.get("command") or ""
            emit(feature_id, "tool", name=event.name, hint=str(hint)[:120])
        elif isinstance(event, AssistantText) and event.text.strip():
            emit(feature_id, "text", text=event.text.strip()[:500])
        elif isinstance(event, RateLimit):
            emit(feature_id, "rate_limit", utilization=event.five_hour_utilization)
        elif isinstance(event, Result) and event.cost_usd:
            emit(feature_id, "cost", cost_usd=event.cost_usd)

    return report


def is_busy(feature_id: str) -> bool:
    job = _active.get(feature_id)
    return job is not None and not job.done()


def active_features() -> list[str]:
    return [fid for fid, job in _active.items() if not job.done()]


def cancel(feature_id: str) -> bool:
    """Stop this feature's run, whichever process is driving it.

    Job state is memory-local, so a run started by `drove execute` in a terminal is invisible
    here. That run gets a cancel request written to its row instead, which the owning process
    picks up and acts on itself.
    """
    job = _active.get(feature_id)
    loop = _loop
    if job is None or job.done() or loop is None:
        return _request_remote_cancel(feature_id)

    def stop() -> None:
        if job.task is not None:
            job.task.cancel()
        else:
            # run_coroutine_threadsafe may have created its Future before the loop has run the
            # coroutine's first line. Retry on-loop so cancellation still reaches guarded() and
            # records the outcome, including for jobs queued behind the semaphore.
            loop.call_soon(stop)

    loop.call_soon_threadsafe(stop)
    return True


def _record_cancellation(feature_id: str) -> None:
    """Write down that a person stopped this, and tell anyone watching."""
    with db.connect() as conn:
        reason = db.cancel_run(conn, feature_id)
    emit(feature_id, "error", message=reason)
    emit(feature_id, "status", status="cancelled")


def _request_remote_cancel(feature_id: str) -> bool:
    """Ask the process that owns this run to stop itself.

    Signals were the obvious route and do not work: a `drove execute` streaming a harness ignores
    SIGINT — sent to its pid and to its process group alike — and runs to completion regardless.
    So the request is recorded, and the owner acts on it at its next check. Slower by a second or
    two, and it actually stops the run.
    """
    with db.connect() as conn:
        run = db.latest_run(conn, feature_id)
        pid = run["owner_pid"] if run else None
        if not run or not pid or pid == os.getpid() or not db.process_alive(pid):
            return False
        db.request_cancel(conn, run["id"])
    return True


def _spawn(feature_id: str, coro) -> None:
    if is_busy(feature_id):
        raise JobError(f"feature {feature_id} already has a job running")

    job = Job()

    async def guarded() -> None:
        started = False
        task = asyncio.current_task()
        assert task is not None
        job.task = task
        try:
            async with _semaphore:
                # Stamp ownership before the first await that can block: a run interrupted from
                # here on is recoverable at the next startup, one interrupted before it is not.
                with db.connect() as conn:
                    if run := db.latest_run(conn, feature_id):
                        db.claim_run(conn, run["id"])
                started = True
                await coro
        except asyncio.CancelledError:
            if not started and hasattr(coro, "close"):
                coro.close()
            _record_cancellation(feature_id)
            raise
        except Exception as exc:
            # Record why, and on the run — not only on the event stream, which is gone the
            # moment the page reloads and takes the only account of the failure with it.
            reason = f"{type(exc).__name__}: {exc}".strip()
            logging.getLogger("drove").exception("run failed for feature %s", feature_id)
            with db.connect() as conn:
                if run := db.latest_run(conn, feature_id):
                    db.finish_run(conn, run["id"], "failed", error=reason[:4000])
                db.set_feature_status(conn, feature_id, "failed")
            emit(feature_id, "error", message=reason)
            emit(feature_id, "status", status="failed")

    loop = _loop
    if loop is None:
        raise JobError("daemon loop is not bound; call jobs.bind_loop() at startup")
    _active[feature_id] = job
    job.future = asyncio.run_coroutine_threadsafe(guarded(), loop)


# --- planning ----------------------------------------------------------------------------------

def _context(feature_id: str):
    """Everything a job needs: the feature row, its workspace, and its worktrees."""
    with db.connect() as conn:
        feature = db.get_feature(conn, feature_id)
        if feature is None:
            raise JobError(f"unknown feature {feature_id}")
        workspace = ws_mod.get(conn, feature["workspace_id"] or "")
        if workspace is None:
            raise JobError(f"feature {feature_id} has no workspace")
        runs = db.list_runs(conn, feature_id)
    trees = trees_mod.create(workspace, feature_id, feature["branch"])
    return feature, workspace, trees, runs


async def _plan(feature_id: str, task: str, feedback: str | None) -> None:
    _, workspace, trees, runs = _context(feature_id)
    with db.connect() as conn:
        prior = db.last_session(conn, feature_id, "plan")
    run_id = runs[-1]["id"]

    emit(feature_id, "status", status="planning")
    with db.connect() as conn:
        db.set_feature_status(conn, feature_id, "planning")

    outcome = await run_plan(
        task,
        workspace,
        trees,
        run_id=run_id,
        on_event=_event_reporter(feature_id),
        # A revision continues the planner's conversation: it already paid to read this codebase.
        resume_session=prior["session_id"] if (prior and feedback) else None,
        feedback=feedback,
    )

    with db.connect() as conn:
        db.set_run_plan(conn, run_id, outcome.plan.model_dump())
        # The planner read the code; its title beats anything derived from the raw request.
        if title := outcome.plan.title.strip():
            db.set_feature_title(conn, feature_id, title[:80])
        db.record_session(
            conn, run_id, "plan", workspace.harness["plan"], outcome.session_id, trees.root,
            tokens_in=outcome.tokens_in, tokens_out=outcome.tokens_out, cost_usd=outcome.cost_usd,
        )
        db.set_feature_status(conn, feature_id, "awaiting_approval")

    emit(feature_id, "plan", plan=outcome.plan.model_dump())
    emit(feature_id, "status", status="awaiting_approval")


def start_plan(feature_id: str, task: str, feedback: str | None = None) -> None:
    _spawn(feature_id, _plan(feature_id, task, feedback))


# --- the run cycle -----------------------------------------------------------------------------

async def _cycle(feature_id: str) -> None:
    _, workspace, trees, runs = _context(feature_id)
    with db.connect() as conn:
        prior = db.last_session(conn, feature_id, "execute")

    latest = next((r for r in reversed(runs) if r["plan_json"]), None)
    if latest is None:
        raise JobError("no approved plan")

    plan = PlanDoc.model_validate(json.loads(latest["plan_json"]))

    for t in trees:
        behind = trees_mod.drift(t)
        if behind:
            emit(feature_id, "drift", repo=t.repo.name, base=t.base, commits=behind)

    # The engine names each stage as it starts; persist that as the feature's status so the UI is
    # truthful on a refresh, not only for whoever happened to be watching the event stream.
    STAGE_STATUS = {
        "execute": "executing",
        "fix": "fixing",
        "review": "reviewing",
        "verify": "verifying",
    }

    def report(stage: str, message: str) -> None:
        emit(feature_id, "stage", stage=stage, message=message)
        if status := STAGE_STATUS.get(stage):
            with db.connect() as conn:
                db.set_feature_status(conn, feature_id, status)
            emit(feature_id, "status", status=status)

    with db.connect() as conn:
        db.set_feature_status(conn, feature_id, "executing")
    emit(feature_id, "status", status="executing")

    outcome = await run_cycle(
        plan, trees, workspace, latest["id"], latest["intent"],
        resume_session=prior["session_id"] if prior else None,
        on_event=_event_reporter(feature_id),
        report=report,
    )

    head_sha = outcome.execute.head_sha if outcome.execute else None
    pack = evidence.write(
        evidence.Evidence(
            run_id=latest["id"],
            feature_id=feature_id,
            iteration=latest["iteration"],
            intent=latest["intent"],
            branch=trees.branch,
            base=", ".join(sorted({t.base for t in trees})),
            workspace=workspace.name,
            repos=outcome.repos_touched,
            stages=evidence.stage_attribution(workspace),
            diff=trees_mod.combined_diff(trees),
            diff_stat=trees_mod.combined_stat(trees),
            head_shas=outcome.execute.head_shas if outcome.execute else {},
            plan=plan,
            head_sha=head_sha,
            files_changed=outcome.files_changed,
            reviews=outcome.reviews,
            verify=outcome.verify,
            browser=outcome.browser,
            pushes=outcome.pushes,
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
                workspace.harness.get(base_stage, workspace.harness["execute"]),
                session_id, trees.root, attempt=int(attempt or 1),
            )
        db.finish_run(conn, latest["id"], outcome.status, head_sha=head_sha)
        db.set_feature_status(conn, feature_id, outcome.status)

    emit(
        feature_id, "done",
        status=outcome.status, note=outcome.note, evidence=str(pack),
        cost_usd=outcome.cost_usd, files_changed=outcome.files_changed,
        repos=outcome.repos_touched,
        reviews=[r.model_dump() for r in outcome.reviews],
    )
    emit(feature_id, "status", status=outcome.status)


def start_cycle(feature_id: str) -> None:
    _spawn(feature_id, _cycle(feature_id))
