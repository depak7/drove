"""FastAPI daemon: REST for state, SSE for what is happening right now.

The browser is a client of the same database and the same pipeline the CLI uses; nothing here
reimplements the engine. The one thing the daemon adds is that the plan gate becomes a *state*
(`awaiting_approval`) rather than a blocking terminal prompt.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vorflux import __version__, db
from vorflux.api import jobs
from vorflux.api.bus import bus
from vorflux.config import RepoConfig, runs_dir
from vorflux.harness import registry
from vorflux.vcs import git, worktree
from vorflux.vcs.git import GitError
from vorflux.vcs.worktree import WorktreeError

api = APIRouter(prefix="/api")

# Set by serve(); the daemon is scoped to one repository, matching the CLI's model.
REPO: Path = Path()


class NewFeature(BaseModel):
    task: str


class Feedback(BaseModel):
    feedback: str


class Pivot(BaseModel):
    intent: str


def _feature_json(conn, row) -> dict[str, Any]:
    runs = db.list_runs(conn, row["id"])
    latest_plan = next((r for r in reversed(runs) if r["plan_json"]), None)
    return {
        "id": row["id"],
        "title": row["title"],
        "branch": row["branch"],
        "base": row["base_branch"],
        "status": row["status"],
        "worktree": row["worktree_path"],
        "created_at": row["created_at"],
        "busy": jobs.is_busy(row["id"]),
        "iterations": len(runs),
        "runs": [
            {
                "id": r["id"],
                "iteration": r["iteration"],
                "intent": r["intent"],
                "status": r["status"],
                "head_sha": r["head_sha"],
            }
            for r in runs
        ],
        "plan": json.loads(latest_plan["plan_json"]) if latest_plan else None,
    }


@api.get("/health")
def health() -> dict[str, Any]:
    harnesses = {
        name: {"path": registry.which(p.binary), "auth": registry.auth_state(name)}
        for name, p in registry.PRESETS.items()
    }
    cfg = RepoConfig.load(REPO)
    return {
        "version": __version__,
        "repo": str(REPO),
        "base_branch": cfg.base_branch,
        "harnesses": harnesses,
        "stages": cfg.harness,
        "verify": cfg.verify,
        "independent_review": cfg.harness["review"] != cfg.harness["execute"],
        "active": jobs.active_features(),
    }


@api.get("/features")
def list_features() -> list[dict[str, Any]]:
    with db.connect() as conn:
        return [_feature_json(conn, row) for row in db.list_features(conn, REPO)]


@api.post("/features")
def create_feature(body: NewFeature) -> dict[str, Any]:
    cfg = RepoConfig.load(REPO)
    feature_id = db.new_id()
    wt = worktree.create(REPO, feature_id, cfg.base_branch)
    with db.connect() as conn:
        db.create_feature(
            conn, REPO, body.task, wt.branch, wt.path, cfg.base_branch, feature_id=feature_id
        )
        db.create_run(conn, feature_id, body.task)
        row = db.get_feature(conn, feature_id)
        payload = _feature_json(conn, row)

    jobs.start_plan(feature_id, body.task)
    return payload


def _load(feature_id: str):
    with db.connect() as conn:
        row = db.find_feature(conn, feature_id)
        if row is None:
            raise HTTPException(404, f"no feature {feature_id}")
        return row, _feature_json(conn, row)


@api.get("/features/{feature_id}")
def get_feature(feature_id: str) -> dict[str, Any]:
    return _load(feature_id)[1]


@api.post("/features/{feature_id}/revise")
def revise(feature_id: str, body: Feedback) -> dict[str, Any]:
    row, payload = _load(feature_id)
    jobs.start_plan(row["id"], row["title"], feedback=body.feedback)
    return payload


@api.post("/features/{feature_id}/approve")
def approve(feature_id: str) -> dict[str, Any]:
    row, _ = _load(feature_id)
    with db.connect() as conn:
        db.set_feature_status(conn, row["id"], "approved")
    jobs.emit(row["id"], "status", status="approved")
    jobs.start_cycle(row["id"])
    return _load(feature_id)[1]


@api.post("/features/{feature_id}/decline")
def decline(feature_id: str) -> dict[str, Any]:
    row, _ = _load(feature_id)
    wt = worktree.create(REPO, row["id"], row["base_branch"])
    result = worktree.teardown(wt)
    with db.connect() as conn:
        if result.removed:
            db.delete_feature(conn, row["id"])
        else:
            db.set_feature_status(conn, row["id"], "abandoned")
    jobs.emit(row["id"], "status", status="declined" if result.removed else "abandoned")
    return {"removed": result.removed, "reason": result.reason, "recovery": result.recovery}


@api.post("/features/{feature_id}/pivot")
def pivot(feature_id: str, body: Pivot) -> dict[str, Any]:
    row, _ = _load(feature_id)
    with db.connect() as conn:
        db.create_run(conn, row["id"], body.intent)
    jobs.start_plan(row["id"], body.intent)
    return _load(feature_id)[1]


@api.get("/features/{feature_id}/diff")
def diff(feature_id: str) -> dict[str, Any]:
    row, _ = _load(feature_id)
    path = Path(row["worktree_path"])
    if not path.exists():
        return {"diff": "", "note": "worktree no longer on disk"}
    return {
        "diff": git.git(path, "diff", f"{row['base_branch']}...HEAD", check=False),
        "stat": git.git(path, "diff", "--stat", f"{row['base_branch']}...HEAD", check=False),
        "drift": git.commits_behind(path, row["base_branch"], "HEAD"),
    }


@api.get("/features/{feature_id}/evidence")
def read_evidence(feature_id: str) -> dict[str, Any]:
    _, payload = _load(feature_id)
    for run in reversed(payload["runs"]):
        path = runs_dir(run["id"]) / "evidence.md"
        if path.exists():
            return {"run_id": run["id"], "markdown": path.read_text()}
    return {"run_id": None, "markdown": ""}


@api.get("/events")
async def events() -> StreamingResponse:
    async def generate():
        # Tell the client immediately so it can render "connected" rather than waiting for the
        # first real event, which may be minutes away.
        yield 'event: ready\ndata: {"ok":true}\n\n'
        with bus.subscribe() as queue:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except TimeoutError:
                    # Comment frame: keeps proxies and browsers from closing an idle connection.
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Routes are sync `def` and therefore run in a threadpool; the job runner needs an explicit
    # handle on this loop to schedule work onto it from there.
    jobs.bind_loop(asyncio.get_running_loop())
    yield


def build_app() -> FastAPI:
    app = FastAPI(title="vorflux", version=__version__, lifespan=lifespan)

    @app.exception_handler(WorktreeError)
    @app.exception_handler(GitError)
    def _operational_error(request, exc):
        # These carry a diagnosis and usually a fix ("Set VORFLUX_HOME to a directory outside the
        # project"). Letting them surface as a 500 with a stack trace throws that away.
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.include_router(api)

    static = Path(str(resources.files("vorflux").joinpath("web")))
    index = static / "index.html"
    if index.exists():
        app.mount("/assets", StaticFiles(directory=str(static / "assets")), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str) -> FileResponse:
            # Single-page app: every unmatched path returns the shell and the client routes it.
            return FileResponse(index)
    else:

        @app.get("/")
        def missing() -> dict[str, str]:
            return {
                "error": "the web UI was not built into this install",
                "fix": "cd web && npm install && npm run build",
            }

    return app
