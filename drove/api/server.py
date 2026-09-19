"""FastAPI daemon: REST for state, SSE for what is happening right now.

The browser is a client of the same database and the same pipeline the CLI uses; nothing here
reimplements the engine. The one thing the daemon adds is that the plan gate becomes a *state*
(`awaiting_approval`) rather than a blocking terminal prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from drove import __version__, db, landed
from drove import workspace as ws_mod
from drove.api import jobs, terminal
from drove.api.bus import bus
from drove.config import provisional_title, runs_dir
from drove.harness import registry
from drove.vcs import git
from drove.vcs import remote as remote_mod
from drove.vcs import tree as trees_mod
from drove.vcs.git import GitError
from drove.vcs.tree import WorktreeError
from drove.workspace import WorkspaceError

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


class Settings(BaseModel):
    """Which harness runs each stage, and on which model."""

    harness: dict[str, str] = {}
    models: dict[str, str | None] = {}


class Feedback(BaseModel):
    feedback: str


class Pivot(BaseModel):
    intent: str


def _workspace_json(conn, ws: ws_mod.Workspace) -> dict[str, Any]:
    return {
        "id": ws.id,
        "name": ws.name,
        "harness": ws.harness,
        "models": ws.models,
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
        # Why the most recent run stopped, so a failure survives a page reload.
        "error": runs[-1]["error"] if runs else None,
        "latest_run_id": runs[-1]["id"] if runs else None,
        # What there is to look at. A tab that opens onto "nothing here yet" is a tab that should
        # not have been offered.
        "has": _available(row, runs),
    }


def _available(row, runs) -> dict[str, bool]:
    latest = runs[-1] if runs else None
    run_dir = runs_dir(latest["id"]) if latest else None
    packs = [_pack(r["id"]) for r in runs]
    # A run whose work was already committed by a crashed earlier attempt records no head_sha —
    # it had nothing left to commit — yet the branch is full of changes. Keying purely on the sha
    # hid the diff and the source of exactly those runs, which are the ones worth looking at.
    has_work = any(r["head_sha"] for r in runs) or any(p.get("files_changed") for p in packs)
    return {
        "worktree": Path(row["worktree_path"]).exists(),
        "plan": any(r["plan_json"] for r in runs),
        "log": bool(run_dir and run_dir.is_dir() and any(run_dir.glob("*.jsonl"))),
        # Something was committed, so there is a diff to read.
        "diff": has_work,
        # Keyed on the structured pack, which is what the app renders; the markdown beside it is
        # for sending to someone outside the app.
        "evidence": any((runs_dir(r["id"]) / "evidence.json").exists() for r in runs),
        "browser": any((runs_dir(r["id"]) / "screens").is_dir() for r in runs),
        "review": any((runs_dir(r["id"]) / "evidence.json").exists() for r in runs),
        # Anything committed can be published, so the tab appears as soon as there is a branch
        # worth pointing at — not only after a push has been recorded. Keying it on the push
        # meant a branch that was never pushed had no screen from which to push it.
        "source": has_work,
    }


def _pack(run_id: str) -> dict:
    """A run's evidence, or an empty pack when it never wrote one."""
    path = runs_dir(run_id) / "evidence.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


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

    Read-only and confined to the user's home directory; the only thing inspected about a folder
    is whether it is a Git worktree.

    Every failure here is reported with its cause. This is the first screen someone touches, and
    an opaque "Internal Server Error" in the one control that adds a repository leaves them with
    nothing to do and nothing to report.
    """
    folder = _browse_path(path)

    entries: list[dict[str, Any]] = []
    try:
        children = sorted(folder.iterdir(), key=lambda child: child.name.lower())
    except OSError as exc:
        raise HTTPException(400, f"cannot read {folder}: {exc.strerror or exc}") from exc

    for child in children:
        if child.name.startswith("."):
            continue
        try:
            if not child.is_dir():
                continue
            is_repo = (child / ".git").exists()
        except OSError:
            # A single unreadable entry — a dead symlink, a folder behind macOS privacy controls —
            # must not take down the listing around it.
            continue
        entries.append({"name": child.name, "path": str(child), "is_repo": is_repo})
        if len(entries) >= 200:
            break

    root = _browse_root()
    return {
        "path": str(folder),
        "parent": str(folder.parent) if folder != root else None,
        "root": str(root),
        "entries": entries,
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

# Capabilities cost about a second to gather — opencode enumerates its models and checks auth by
# spawning processes — and they change about as often as you install a CLI. Cache them so opening
# the settings screen is instant rather than a blank second.
_HARNESS_TTL = 60.0
_harness_cache: tuple[float, list[dict[str, Any]]] | None = None


@api.get("/harnesses")
def harnesses(refresh: bool = False) -> list[dict[str, Any]]:
    """What can run a stage, and what each one can run on."""
    global _harness_cache
    if not refresh and _harness_cache and time.monotonic() - _harness_cache[0] < _HARNESS_TTL:
        return _harness_cache[1]

    out = []
    for name, preset in registry.PRESETS.items():
        path = registry.which(preset.binary)
        out.append({
            "name": name,
            "installed": bool(path),
            "auth": registry.auth_state(name) if path else "logged_out",
            "supports_schema": preset.supports_schema,
            "install": preset.install,
            # Empty is honest: two of three CLIs cannot enumerate, so the UI offers free text.
            "models": registry.list_models(name) if path else [],
            "default_model": registry.configured_model(name),
        })
    _harness_cache = (time.monotonic(), out)
    return out


@api.put("/workspaces/{workspace_id}/settings")
def update_settings(workspace_id: str, body: Settings) -> dict[str, Any]:
    with db.connect() as conn:
        ws = _workspace(conn, workspace_id)
        harness = {**ws.harness, **{k: v for k, v in body.harness.items() if v}}

        unknown = [v for v in harness.values() if v not in registry.PRESETS]
        if unknown:
            raise HTTPException(400, f"no adapter for {', '.join(sorted(set(unknown)))}")

        # An empty string from a <select> means "let the CLI decide", which is not the same as
        # never having chosen — so store the key with None rather than dropping it.
        models = {**ws.models, **{k: (v or None) for k, v in body.models.items()}}
        db.set_workspace_config(conn, ws.id, harness, models)
        return _workspace_json(conn, _workspace(conn, workspace_id))


@api.get("/workspaces/{workspace_id}/runs")
def list_runs(workspace_id: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        ws = _workspace(conn, workspace_id)
        return [
            {
                "id": r["id"],
                "feature_id": r["feature_id"],
                "title": r["title"],
                "branch": r["branch"],
                "iteration": r["iteration"],
                "intent": r["intent"],
                "status": r["status"],
                "error": r["error"],
                "head_sha": r["head_sha"],
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
                "duration_s": (
                    round(r["ended_at"] - r["started_at"]) if r["ended_at"] else None
                ),
                "cost_usd": r["cost"] or None,
                "tokens_in": r["tin"],
                "tokens_out": r["tout"],
            }
            for r in db.runs_in(conn, ws.id)
        ]


# Which stage a session served maps to the role a person would name it.
ROLE = {
    "plan": "Planner",
    "execute": "Builder",
    "review": "Reviewer",
    "arbiter": "Arbiter",
}


@api.get("/workspaces/{workspace_id}/agents")
def list_agents(workspace_id: str) -> list[dict[str, Any]]:
    """Agent sessions, newest first.

    Each row is a real conversation that can be reopened: `cd <cwd> && <harness> --resume <id>`
    drops you into what the agent actually did. That only works because the session id is minted
    before the process starts, so it is recorded even for a run that died on its first turn.
    """
    active = set(jobs.active_features())
    with db.connect() as conn:
        ws = _workspace(conn, workspace_id)
        return [
            {
                "session_id": s["session_id"],
                "harness": s["harness"],
                "stage": s["stage"],
                "role": ROLE.get(s["stage"], s["stage"].title()),
                "attempt": s["attempt"],
                "cwd": s["cwd"],
                "feature_id": s["feature_id"],
                "title": s["title"],
                "intent": s["intent"],
                "tokens_in": s["tokens_in"],
                "tokens_out": s["tokens_out"],
                "cost_usd": s["cost_usd"],
                "running": s["feature_id"] in active,
                "started_at": s["started_at"],
                # The exact command to reopen this conversation.
                "resume": _resume_command(s["harness"], s["session_id"], s["cwd"]),
            }
            for s in db.sessions_in(conn, ws.id)
        ]


def _resume_command(harness: str, session_id: str, cwd: str) -> str | None:
    """How to reattach to a session, per harness. None when that CLI cannot resume."""
    if harness == "claude":
        return f"cd {shlex.quote(cwd)} && claude --resume {session_id}"
    if harness == "codex":
        return f"cd {shlex.quote(cwd)} && codex exec resume --json {session_id}"
    if harness == "opencode":
        return f"cd {shlex.quote(cwd)} && opencode --session {session_id}"
    return None


@api.get("/features")
def list_features(workspace_id: str | None = None) -> list[dict[str, Any]]:
    with db.connect() as conn:
        newly_landed = landed.reconcile(conn)
        rows = (
            db.features_in(conn, workspace_id)
            if workspace_id
            else db.list_features(conn)
        )
        payload = [_feature_json(conn, row) for row in rows]
    for row in newly_landed:
        jobs.emit(row["id"], "status", status="landed")
    return payload


@api.post("/features")
def create_feature(body: NewFeature) -> dict[str, Any]:
    feature_id = db.new_id()
    with db.connect() as conn:
        ws = _workspace(conn, body.workspace_id)
        if not ws.repos:
            raise HTTPException(400, f"workspace {ws.name!r} has no repositories yet")
        trees = trees_mod.create(ws, feature_id, trees_mod.unique_branch(ws, body.task, feature_id))
        primary = trees.trees[0]
        db.create_feature(
            conn, primary.repo.path, provisional_title(body.task), trees.branch, trees.root,
            primary.base, feature_id=feature_id, workspace_id=ws.id,
        )
        # The run keeps the request verbatim; only the feature's display name is shortened.
        db.create_run(conn, feature_id, body.task)
        payload = _feature_json(conn, db.get_feature(conn, feature_id))

    jobs.start_plan(feature_id, body.task)
    return payload


def _trees_for(row):
    with db.connect() as conn:
        ws = _workspace(conn, row["workspace_id"] or "")
    return trees_mod.create(ws, row["id"], row["branch"])


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


@api.post("/features/{feature_id}/reclaim")
def reclaim(feature_id: str) -> dict[str, Any]:
    row, _ = _load(feature_id)
    if row["status"] != "landed":
        raise HTTPException(400, "only landed features can be reclaimed")
    if jobs.is_busy(row["id"]):
        raise HTTPException(409, "this feature is already running")

    with db.connect() as conn:
        results = landed.reclaim(conn, row)
    kept = {name: result for name, result in results.items() if not result.removed}
    return {
        "removed": not kept,
        "kept": {
            name: {"reason": result.reason, "recovery": result.recovery}
            for name, result in kept.items()
        },
    }


@api.post("/features/{feature_id}/retry")
def retry(feature_id: str) -> dict[str, Any]:
    """Run the approved plan again, unchanged.

    A run can fail for reasons that have nothing to do with the plan — a rate limit, a harness
    crash, a network blip. Making someone re-plan to recover from that wastes a planning call and
    loses the executor session that already knows the codebase.
    """
    row, payload = _load(feature_id)
    if jobs.is_busy(row["id"]):
        raise HTTPException(409, "this feature is already running")

    if not payload["plan"]:
        # A run interrupted before it produced a plan has nothing to re-run — but the request that
        # started it is still recorded, so resuming means planning it again rather than asking
        # someone to retype what they already asked for.
        with db.connect() as conn:
            prior = db.latest_run(conn, row["id"])
            if row["status"] not in ("interrupted", "cancelled") or prior is None:
                raise HTTPException(400, "this feature has no approved plan to retry")
            db.create_run(conn, row["id"], prior["intent"])
        jobs.start_plan(row["id"], prior["intent"])
        return _load(feature_id)[1]

    with db.connect() as conn:
        db.set_feature_status(conn, row["id"], "approved")
    jobs.emit(row["id"], "status", status="approved")
    jobs.start_cycle(row["id"])
    return _load(feature_id)[1]


@api.post("/features/{feature_id}/cancel")
def cancel(feature_id: str) -> dict[str, Any]:
    """Stop this feature's run while keeping its branch and worktree.

    Works whether the run belongs to this daemon or to a `drove execute` in a terminal: job state
    is memory-local, but runs record their owning pid, and that process is asked to stop the same
    way Ctrl-C would ask it.
    """
    row, _ = _load(feature_id)
    if not jobs.cancel(row["id"]):
        raise HTTPException(409, "nothing is running for this feature")
    return _load(feature_id)[1]


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


MAX_BLOB_BYTES = 800_000


def _tree_named(trees, repo: str | None):
    """The repo the request means — the only one, when a workspace has one."""
    found = trees.by_name(repo) if repo else (trees.trees[0] if len(trees) else None)
    if found is None:
        raise HTTPException(404, f"no repository {repo!r} in this feature")
    return found


def _inside(root: Path, relative: str) -> Path:
    """Resolve a path the browser asked for, refusing anything outside the worktree.

    The path arrives in a query string, so `../../.ssh/id_rsa` is a thing someone can type. A
    localhost daemon is still a server, and this is the only place it reads an arbitrary path.
    """
    base = root.resolve()
    target = (base / relative).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(400, "path is outside the worktree")
    return target


@api.get("/features/{feature_id}/changes")
def changes(feature_id: str) -> dict[str, Any]:
    """Every changed file with its line counts — the list a reviewer scans before reading."""
    row, _ = _load(feature_id)
    if not Path(row["worktree_path"]).exists():
        return {"files": [], "note": "worktrees no longer on disk"}
    trees = _trees_for(row)
    return {
        "files": [asdict(change) for change in trees_mod.changes(trees)],
        "repos": [t.repo.name for t in trees_mod.touched(trees)],
    }


@api.get("/features/{feature_id}/blob")
def blob(feature_id: str, path: str, repo: str | None = None) -> dict[str, Any]:
    """One file: its current text, and its diff when the feature changed it."""
    row, _ = _load(feature_id)
    if not Path(row["worktree_path"]).exists():
        raise HTTPException(404, "worktrees no longer on disk")
    tree = _tree_named(_trees_for(row), repo)
    target = _inside(tree.path, path)

    text, note = "", None
    if target.is_file():
        if target.stat().st_size > MAX_BLOB_BYTES:
            note = f"file is larger than {MAX_BLOB_BYTES // 1000} KB and is not shown"
        else:
            try:
                text = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                note = "not a text file"
    elif not (diff := trees_mod.file_diff(tree, path)) or not diff.strip():
        raise HTTPException(404, f"no such file: {path}")

    return {
        "repo": tree.repo.name,
        "path": path,
        "text": text,
        "note": note,
        "diff": trees_mod.file_diff(tree, path),
    }


@api.get("/features/{feature_id}/tree")
def browse(feature_id: str, path: str = "", repo: str | None = None) -> dict[str, Any]:
    """One directory of the worktree, so the code can be read without leaving the app."""
    row, _ = _load(feature_id)
    if not Path(row["worktree_path"]).exists():
        raise HTTPException(404, "worktrees no longer on disk")
    tree = _tree_named(_trees_for(row), repo)
    target = _inside(tree.path, path)
    if not target.is_dir():
        raise HTTPException(404, f"no such directory: {path}")

    entries = []
    for child in sorted(target.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
        if child.name in (".git", "__pycache__", "node_modules", ".drove"):
            continue
        entries.append({
            "name": child.name,
            "path": str(Path(path) / child.name) if path else child.name,
            "dir": child.is_dir(),
        })
    return {"repo": tree.repo.name, "path": path, "entries": entries}


@api.websocket("/features/{feature_id}/terminal")
async def terminal_socket(socket: WebSocket, feature_id: str) -> None:
    """A shell in the feature's worktree, for the things a person wants to do by hand."""
    with db.connect() as conn:
        row = db.get_feature(conn, feature_id)
    worktree = Path(row["worktree_path"]) if row else None
    if row is None or worktree is None or not worktree.is_dir():
        await socket.close(code=1008, reason="no worktree for this feature")
        return
    # One repo's checkout rather than the feature root, when there is only one: that is where
    # `git status` and the project's own commands mean something.
    children = [child for child in worktree.iterdir() if (child / ".git").exists()]
    await terminal.serve(socket, children[0] if len(children) == 1 else worktree)


@api.get("/features/{feature_id}/log")
def read_log(feature_id: str, run_id: str | None = None) -> dict[str, Any]:
    """Replay a run's recorded harness output.

    The live stream only exists while a tab is open, so a failed run had nothing to show after a
    reload. The raw JSONL is already on disk for exactly this — it is replayed through the same
    adapter the live view used, so what you read afterwards is what you would have watched.
    """
    row, payload = _load(feature_id)
    target = run_id or payload["latest_run_id"]
    if not target:
        return {"run_id": None, "stages": []}

    workspace_harness = {}
    with db.connect() as conn:
        if ws := ws_mod.get(conn, row["workspace_id"] or ""):
            workspace_harness = ws.harness

    stages: list[dict[str, Any]] = []
    directory = runs_dir(target)
    if not directory.is_dir():
        return {"run_id": target, "stages": []}

    for file in sorted(directory.glob("*.jsonl")):
        stage = file.stem.split("-")[0]
        name = workspace_harness.get(stage if stage != "fix" else "execute", "claude")
        try:
            harness = registry.get(name)
        except KeyError:
            continue

        lines: list[dict[str, Any]] = []
        for raw in file.read_text(errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                payload_json = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload_json, dict):
                continue
            for event in harness.parse(payload_json):
                rendered = _render_event(event)
                if rendered:
                    lines.append(rendered)
        stages.append({"stage": file.stem, "harness": name, "lines": lines[-400:]})

    return {"run_id": target, "stages": stages}


def _render_event(event: Any) -> dict[str, str] | None:
    """One log line per event, matching what the live stream shows."""
    kind = getattr(event, "kind", "")
    if kind == "tool_call":
        hint = event.input.get("file_path") or event.input.get("command") or ""
        return {"tone": "dim", "text": f"{event.name} {str(hint)[:120]}".strip()}
    if kind == "assistant_text" and event.text.strip():
        return {"tone": "text", "text": event.text.strip()[:500]}
    if kind == "result" and not event.ok:
        return {"tone": "error", "text": event.error or "failed"}
    return None


def _evidence_json(payload) -> tuple[str | None, dict[str, Any]]:
    """The most recent run that produced a machine-readable pack."""
    for run in reversed(payload["runs"]):
        path = runs_dir(run["id"]) / "evidence.json"
        if not path.exists():
            continue
        try:
            return run["id"], json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return None, {}


@api.get("/features/{feature_id}/review")
def review_summary(feature_id: str) -> dict[str, Any]:
    """Who judged the change, and what they said.

    Served from the run's own evidence rather than recomputed, so what the app shows and what the
    pack says can never drift apart.
    """
    _, payload = _load(feature_id)
    run_id, data = _evidence_json(payload)
    return {
        "run_id": run_id,
        "reviews": data.get("reviews", []),
        "stages": data.get("stages", {}),
        "independent": data.get("independent_review", False),
    }


@api.get("/features/{feature_id}/browser")
def browser_results(feature_id: str) -> dict[str, Any]:
    """What the app looked like when it was opened, from the run's evidence."""
    _, payload = _load(feature_id)
    run_id, data = _evidence_json(payload)
    return {"run_id": run_id, "checks": data.get("browser", [])}


@api.get("/features/{feature_id}/source")
def source(feature_id: str) -> dict[str, Any]:
    """Where the branch was published, per repo — the link you actually open."""
    row, payload = _load(feature_id)
    for run in reversed(payload["runs"]):
        if pushes := _pack(run["id"]).get("source"):
            return {"run_id": run["id"], "branch": row["branch"], "repos": pushes}
    return {"run_id": None, "branch": row["branch"], "repos": []}


@api.post("/features/{feature_id}/push")
def publish(feature_id: str) -> dict[str, Any]:
    """Push the feature's branches now, and record where they went.

    Runs push themselves when they pass, so this is for the cases they cannot cover: a repo with
    `push = false` you have changed your mind about, a push that failed on a flaky network, and
    branches built before publishing existed at all.
    """
    row, payload = _load(feature_id)
    if jobs.is_busy(row["id"]):
        raise HTTPException(409, "this feature is already running")

    trees = _trees_for(row)
    pushed = [remote_mod.push(t, t.repo.config.remote) for t in trees_mod.touched(trees)]
    if not pushed:
        raise HTTPException(400, "nothing has been committed on this branch yet")

    # Record it on the latest run that has a pack, so the Source tab shows what just happened
    # rather than staying empty until the next run writes one.
    for run in reversed(payload["runs"]):
        pack = runs_dir(run["id"]) / "evidence.json"
        if not pack.exists():
            continue
        try:
            data = json.loads(pack.read_text())
        except ValueError:
            break
        data["source"] = [asdict(p) for p in pushed]
        pack.write_text(json.dumps(data, indent=2))
        break

    return {"repos": [asdict(p) for p in pushed]}


@api.get("/features/{feature_id}/screens/{name}")
def screenshot(feature_id: str, name: str) -> FileResponse:
    _, payload = _load(feature_id)
    # The name comes from a URL, so it is never trusted as a path: take the basename and require
    # the resolved file to sit inside the run's own screens directory.
    safe = Path(name).name
    for run in reversed(payload["runs"]):
        folder = (runs_dir(run["id"]) / "screens").resolve()
        candidate = (folder / safe).resolve()
        if candidate.is_file() and candidate.is_relative_to(folder):
            return FileResponse(candidate, media_type="image/png")
    raise HTTPException(404, "no such screenshot")


@api.get("/features/{feature_id}/evidence")
def read_evidence(feature_id: str) -> dict[str, Any]:
    """The evidence pack: structured for the app, markdown for sending elsewhere.

    The app renders `pack`; the markdown is what you hand to someone outside it. Keyed on the JSON
    because that is what the UI actually draws — keying on the markdown meant a run that had a
    pack still showed "no evidence yet".
    """
    _, payload = _load(feature_id)
    run_id, data = _evidence_json(payload)
    markdown = ""
    if run_id:
        path = runs_dir(run_id) / "evidence.md"
        if path.exists():
            markdown = path.read_text()
    return {"run_id": run_id, "markdown": markdown, "pack": data}


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
    # Work in flight when the last daemon died is stranded: its job state lived in memory. Retire
    # it before the UI can read the database, so nobody is ever shown a run that cannot progress.
    with db.connect() as conn:
        stranded = db.reconcile_interrupted(conn)
        newly_landed = landed.reconcile(conn, force=True)
    if stranded:
        # warning, not info: nothing configures logging, so info would never reach the terminal —
        # and a previous run dying is exactly the thing you want said out loud on the next start.
        logging.getLogger("drove").warning(
            "%d feature(s) were interrupted when Drove last stopped and can be resumed: %s",
            len(stranded), ", ".join(f["title"] for f in stranded),
        )
    if newly_landed:
        logging.getLogger("drove").warning(
            "%d delivered feature(s) have landed: %s",
            len(newly_landed), ", ".join(f["title"] for f in newly_landed),
        )
    yield


def build_app() -> FastAPI:
    app = FastAPI(title="drove", version=__version__, lifespan=lifespan)

    @app.exception_handler(Exception)
    def _unexpected(request, exc):
        # Log it properly so a recurrence is diagnosable, and hand the UI something it can show.
        logging.getLogger("drove").exception("unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

    @app.exception_handler(jobs.JobError)
    def _busy(request, exc):
        # "already running" is a conflict, not a server fault. The status flips to failed a moment
        # before the job's future settles, so a fast retry can legitimately land in that window.
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(WorkspaceError)
    @app.exception_handler(WorktreeError)
    @app.exception_handler(GitError)
    def _operational_error(request, exc):
        # These carry a diagnosis and usually a fix ("Set DROVE_HOME to a directory outside the
        # project"). Letting them surface as a 500 with a stack trace throws that away.
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.include_router(api)

    static = Path(str(resources.files("drove").joinpath("web")))
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
