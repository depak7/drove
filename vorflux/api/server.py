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
from vorflux import workspace as ws_mod
from vorflux.api import jobs
from vorflux.api.bus import bus
from vorflux.config import runs_dir
from vorflux.harness import registry
from vorflux.vcs import git
from vorflux.vcs import tree as trees_mod
from vorflux.vcs.git import GitError
from vorflux.vcs.tree import WorktreeError
from vorflux.workspace import WorkspaceError

api = APIRouter(prefix="/api")


def _browse_root() -> Path:
    """The local repository picker never exposes paths outside the user's home directory."""
    return Path.home().resolve()


def _browse_path(path: str | None) -> Path:
    root = _browse_root()
    candidate = (Path(path).expanduser() if path else root).resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(403, "repository picker is limited to your home directory")
    if not candidate.is_dir():
        raise HTTPException(404, "folder not found")
    return candidate


class NewWorkspace(BaseModel):
    name: str
    repos: list[str] = []


class NewRepo(BaseModel):
    path: str
    name: str | None = None


class NewFeature(BaseModel):
    task: str
    workspace_id: str


class Feedback(BaseModel):
    feedback: str


class Pivot(BaseModel):
    intent: str


def _workspace_json(conn, ws: ws_mod.Workspace) -> dict[str, Any]:
    return {
        "id": ws.id,
        "name": ws.name,
        "harness": ws.harness,
        "independent_review": ws.harness["review"] != ws.harness["execute"],
        "repos": [
            {
                "path": str(r.path),
                "name": r.name,
                "base_branch": r.base_branch,
                "verify": list(r.config.verify),
                "exists": r.path.exists(),
            }
            for r in ws.repos
        ],
        "features": len(db.features_in(conn, ws.id)),
    }


def _feature_json(conn, row) -> dict[str, Any]:
    runs = db.list_runs(conn, row["id"])
    latest_plan = next((r for r in reversed(runs) if r["plan_json"]), None)
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
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
    return {
        "version": __version__,
        "harnesses": {
            name: {"path": registry.which(p.binary), "auth": registry.auth_state(name)}
            for name, p in registry.PRESETS.items()
        },
        "active": jobs.active_features(),
    }


# --- workspaces --------------------------------------------------------------------------------


@api.get("/repos/browse")
def browse_repositories(path: str | None = None) -> dict[str, Any]:
    """List local folders for the browser-based repository picker.

    This is intentionally read-only and constrained to the current user's home directory. The
    endpoint does not inspect files beyond whether a folder is a Git worktree.
    """
    folder = _browse_path(path)
    root = _browse_root()
    try:
        children = sorted(
            (
                child
                for child in folder.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            ),
            key=lambda child: child.name.lower(),
        )[:200]
    except OSError as exc:
        raise HTTPException(400, f"cannot read folder: {exc}") from exc
    return {
        "path": str(folder),
        "parent": str(folder.parent) if folder != root else None,
        "root": str(root),
        "entries": [
            {"name": child.name, "path": str(child), "is_repo": (child / ".git").exists()}
            for child in children
        ],
    }

@api.get("/workspaces")
def list_workspaces() -> list[dict[str, Any]]:
    with db.connect() as conn:
        return [_workspace_json(conn, ws) for ws in ws_mod.load_all(conn)]


@api.post("/workspaces")
def create_workspace(body: NewWorkspace) -> dict[str, Any]:
    with db.connect() as conn:
        ws = ws_mod.create(conn, body.name, [Path(p) for p in body.repos])
        return _workspace_json(conn, ws)


def _workspace(conn, workspace_id: str) -> ws_mod.Workspace:
    ws = ws_mod.get(conn, workspace_id)
    if ws is None:
        raise HTTPException(404, f"no workspace {workspace_id}")
    return ws


@api.post("/workspaces/{workspace_id}/repos")
def add_repo(workspace_id: str, body: NewRepo) -> dict[str, Any]:
    with db.connect() as conn:
        ws = _workspace(conn, workspace_id)
        ws_mod.attach(conn, ws.id, Path(body.path), body.name)
        return _workspace_json(conn, _workspace(conn, workspace_id))


@api.delete("/workspaces/{workspace_id}/repos")
def remove_repo(workspace_id: str, path: str) -> dict[str, Any]:
    with db.connect() as conn:
        ws = _workspace(conn, workspace_id)
        ws_mod.detach(conn, ws.id, Path(path))
        return _workspace_json(conn, _workspace(conn, workspace_id))


@api.delete("/workspaces/{workspace_id}")
def delete_workspace(workspace_id: str) -> dict[str, bool]:
    with db.connect() as conn:
        ws = _workspace(conn, workspace_id)
        if db.features_in(conn, ws.id):
            raise HTTPException(
                400, "this workspace still has features; their branches would be orphaned"
            )
        db.delete_workspace(conn, ws.id)
    return {"deleted": True}


# --- features ----------------------------------------------------------------------------------

@api.get("/features")
def list_features(workspace_id: str | None = None) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = (
            db.features_in(conn, workspace_id)
            if workspace_id
            else db.list_features(conn)
        )
        return [_feature_json(conn, row) for row in rows]


@api.post("/features")
def create_feature(body: NewFeature) -> dict[str, Any]:
    feature_id = db.new_id()
    with db.connect() as conn:
        ws = _workspace(conn, body.workspace_id)
        if not ws.repos:
            raise HTTPException(400, f"workspace {ws.name!r} has no repositories yet")
        trees = trees_mod.create(ws, feature_id)
        primary = trees.trees[0]
        db.create_feature(
            conn, primary.repo.path, body.task, trees.branch, trees.root,
            primary.base, feature_id=feature_id, workspace_id=ws.id,
        )
        db.create_run(conn, feature_id, body.task)
        payload = _feature_json(conn, db.get_feature(conn, feature_id))

    jobs.start_plan(feature_id, body.task)
    return payload


def _trees_for(row):
    with db.connect() as conn:
        ws = _workspace(conn, row["workspace_id"] or "")
    return trees_mod.create(ws, row["id"])


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
    trees = _trees_for(row)
    results = trees_mod.teardown(trees)

    kept = {name: r for name, r in results.items() if not r.removed}
    with db.connect() as conn:
        if kept:
            db.set_feature_status(conn, row["id"], "abandoned")
        else:
            db.delete_feature(conn, row["id"])
    jobs.emit(row["id"], "status", status="abandoned" if kept else "declined")
    return {
        "removed": not kept,
        # Per repo: one repo's work being merged is no reason to delete another's.
        "kept": {
            name: {"reason": r.reason, "recovery": r.recovery} for name, r in kept.items()
        },
    }


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
    if not Path(row["worktree_path"]).exists():
        return {"diff": "", "repos": [], "note": "worktrees no longer on disk"}

    trees = _trees_for(row)
    return {
        "diff": trees_mod.combined_diff(trees),
        "repos": [
            {
                "name": t.repo.name,
                "branch": t.branch,
                "base": t.base,
                "stat": git.git(t.path, "diff", "--stat", f"{t.base}...HEAD", check=False),
                "drift": trees_mod.drift(t),
            }
            for t in trees_mod.touched(trees)
        ],
    }


@api.get("/features/{feature_id}/evidence")
def read_evidence(feature_id: str) -> dict[str, Any]:
    _, payload = _load(feature_id)
    for run in reversed(payload["runs"]):
        path = runs_dir(run["id"]) / "evidence.md"
        if path.exists():
            json_path = path.with_suffix(".json")
            return {
                "run_id": run["id"],
                "markdown": path.read_text(),
                "summary": json.loads(json_path.read_text()) if json_path.exists() else None,
            }
    return {"run_id": None, "markdown": "", "summary": None}


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

    @app.exception_handler(WorkspaceError)
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
