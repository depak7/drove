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
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vorflux import db, evidence
from vorflux.config import RepoConfig
from vorflux.events import AssistantText, HarnessEvent, RateLimit, Result, ToolCall
from vorflux.api.bus import bus
from vorflux.pipeline.engine import run_cycle
from vorflux.pipeline.schemas import PlanDoc
from vorflux.pipeline.stages.plan import run_plan
from vorflux.vcs import worktree

MAX_CONCURRENT_RUNS = 2
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
_active: dict[str, Future] = {}
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
    task = _active.get(feature_id)
    return task is not None and not task.done()


def active_features() -> list[str]:
    return [fid for fid, task in _active.items() if not task.done()]


def _spawn(feature_id: str, coro) -> None:
    if is_busy(feature_id):
        raise JobError(f"feature {feature_id} already has a job running")

    async def guarded() -> None:
        async with _semaphore:
            try:
                await coro
            except Exception as exc:  # surfaced to the UI, never swallowed
                emit(feature_id, "error", message=str(exc))
                with db.connect() as conn:
                    db.set_feature_status(conn, feature_id, "failed")
                emit(feature_id, "status", status="failed")

    loop = _loop
    if loop is None:
        raise JobError("daemon loop is not bound; call jobs.bind_loop() at startup")
    _active[feature_id] = asyncio.run_coroutine_threadsafe(guarded(), loop)


# --- planning ----------------------------------------------------------------------------------

async def _plan(feature_id: str, task: str, feedback: str | None) -> None:
    with db.connect() as conn:
        feature = db.get_feature(conn, feature_id)
        if feature is None:
            raise JobError(f"unknown feature {feature_id}")
        repo = Path(feature["repo"])
        runs = db.list_runs(conn, feature_id)
        prior = db.last_session(conn, feature_id, "plan")

    cfg = RepoConfig.load(repo)
    wt = worktree.create(repo, feature_id, feature["base_branch"])
    run_id = runs[-1]["id"]

    emit(feature_id, "status", status="planning")
    with db.connect() as conn:
        db.set_feature_status(conn, feature_id, "planning")

    outcome = await run_plan(
        task,
        cfg,
        run_id=run_id,
        on_event=_event_reporter(feature_id),
        cwd=wt.path,
        # A revision continues the planner's conversation: it already paid to read this codebase.
        resume_session=prior["session_id"] if (prior and feedback) else None,
        feedback=feedback,
    )

    with db.connect() as conn:
        db.set_run_plan(conn, run_id, outcome.plan.model_dump())
        db.record_session(
            conn, run_id, "plan", cfg.harness["plan"], outcome.session_id, wt.path,
            tokens_in=outcome.tokens_in, tokens_out=outcome.tokens_out, cost_usd=outcome.cost_usd,
        )
        db.set_feature_status(conn, feature_id, "awaiting_approval")

    emit(feature_id, "plan", plan=outcome.plan.model_dump())
    emit(feature_id, "status", status="awaiting_approval")


def start_plan(feature_id: str, task: str, feedback: str | None = None) -> None:
    _spawn(feature_id, _plan(feature_id, task, feedback))


# --- the run cycle -----------------------------------------------------------------------------

async def _cycle(feature_id: str) -> None:
    with db.connect() as conn:
        feature = db.get_feature(conn, feature_id)
        if feature is None:
            raise JobError(f"unknown feature {feature_id}")
        repo = Path(feature["repo"])
        runs = db.list_runs(conn, feature_id)
        prior = db.last_session(conn, feature_id, "execute")

    latest = next((r for r in reversed(runs) if r["plan_json"]), None)
    if latest is None:
        raise JobError("no approved plan")

    cfg = RepoConfig.load(repo)
    plan = PlanDoc.model_validate(json.loads(latest["plan_json"]))
    wt = worktree.create(repo, feature_id, feature["base_branch"])

    drift = worktree.drift(wt)
    if drift:
        emit(feature_id, "drift", base=wt.base, commits=drift)

    def report(stage: str, message: str) -> None:
        emit(feature_id, "stage", stage=stage, message=message)

    with db.connect() as conn:
        db.set_feature_status(conn, feature_id, "executing")
    emit(feature_id, "status", status="executing")

    outcome = await run_cycle(
        plan, wt, cfg, latest["id"], latest["intent"],
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
        db.set_feature_status(conn, feature_id, outcome.status)

    emit(
        feature_id, "done",
        status=outcome.status, note=outcome.note, evidence=str(pack),
        cost_usd=outcome.cost_usd, files_changed=outcome.files_changed,
        reviews=[r.model_dump() for r in outcome.reviews],
    )
    emit(feature_id, "status", status=outcome.status)


def start_cycle(feature_id: str) -> None:
    _spawn(feature_id, _cycle(feature_id))
