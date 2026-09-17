"""SQLite state: features, runs, sessions.

Schema changes are append-only migrations keyed on ``PRAGMA user_version`` — never edits to an
existing migration, because someone's database is already at that version.

What lives here is *state*. Harness output does not: raw JSONL stays on disk under
~/.drove/runs/<run-id>/, where it is simultaneously the audit log, the replay source and a test
fixture. Putting megabytes of transcript in SQLite would buy nothing.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from drove import config

MIGRATIONS: list[str] = [
    # 1
    """
    CREATE TABLE features (
        id            TEXT PRIMARY KEY,
        repo          TEXT NOT NULL,
        title         TEXT NOT NULL,
        branch        TEXT NOT NULL,
        worktree_path TEXT NOT NULL,
        base_branch   TEXT NOT NULL,
        status        TEXT NOT NULL,
        created_at    REAL NOT NULL
    );
    CREATE TABLE runs (
        id         TEXT PRIMARY KEY,
        feature_id TEXT NOT NULL REFERENCES features(id) ON DELETE CASCADE,
        iteration  INTEGER NOT NULL,
        intent     TEXT NOT NULL,
        plan_json  TEXT,
        head_sha   TEXT,
        status     TEXT NOT NULL,
        started_at REAL NOT NULL,
        ended_at   REAL
    );
    CREATE TABLE sessions (
        run_id     TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        stage      TEXT NOT NULL,
        attempt    INTEGER NOT NULL DEFAULT 1,
        harness    TEXT NOT NULL,
        session_id TEXT NOT NULL,
        cwd        TEXT NOT NULL,
        tokens_in  INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        cost_usd   REAL,
        PRIMARY KEY (run_id, stage, attempt)
    );
    CREATE INDEX runs_by_feature ON runs(feature_id, iteration);
    """,
    # 2 — workspaces. A workspace is a named set of repos worked on together, so a feature can
    # change an API in one repo and its caller in another. Schema only; existing rows are
    # backfilled by _backfill_workspaces, because deriving a repo's name from its path is a
    # basename operation and SQLite has no clean way to express one.
    """
    CREATE TABLE workspaces (
        id         TEXT PRIMARY KEY,
        name       TEXT NOT NULL,
        harness    TEXT,
        created_at REAL NOT NULL
    );
    CREATE TABLE workspace_repos (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        path         TEXT NOT NULL,
        name         TEXT NOT NULL,
        base_branch  TEXT NOT NULL,
        added_at     REAL NOT NULL,
        PRIMARY KEY (workspace_id, path)
    );
    ALTER TABLE features ADD COLUMN workspace_id TEXT REFERENCES workspaces(id);
    CREATE INDEX features_by_workspace ON features(workspace_id);
    """,
    # 3 — which model each stage runs. Null means "let the CLI decide", which stays the default:
    # pinning a model id in config is how a workspace silently breaks when a provider retires one.
    """
    ALTER TABLE workspaces ADD COLUMN models TEXT;
    """,
    # 4 — why a run failed. It was emitted over SSE and nowhere else, so refreshing the page lost
    # the only account of what went wrong.
    """
    ALTER TABLE runs ADD COLUMN error TEXT;
    """,
]

# Data migrations that need real code. Keyed by the schema version they run after.
AFTER: dict[int, str] = {2: "_backfill_workspaces"}


LEGACY_DB = "vorflux.db"


def _is_empty(db_file: Path) -> bool:
    """True for a database with no workspaces and no features — nothing worth keeping."""
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        rows = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN"
            " ('workspaces','features')"
        ).fetchone()[0]
        if rows < 2:
            return True
        counts = conn.execute(
            "SELECT (SELECT COUNT(*) FROM workspaces) + (SELECT COUNT(*) FROM features)"
        ).fetchone()[0]
        return counts == 0
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def path() -> Path:
    """The database file, adopting one left by the product's former name.

    The rename changed the filename as well as the directory, so without this an existing install
    opens a fresh empty database and every workspace appears to have vanished while the old file
    sits beside it.

    The empty check matters: anyone who launched once after upgrading already has a blank
    drove.db, and a plain "does it exist" test would decide the migration was done and strand
    their real data forever. An empty database has nothing to lose, so it is set aside.
    """
    current = config.HOME / "drove.db"
    legacy = config.HOME / LEGACY_DB

    if not legacy.exists() or _is_empty(legacy):
        return current

    if current.exists():
        if not _is_empty(current):
            return current  # real data on both sides — never merge, never clobber
        current.rename(current.with_suffix(".db.superseded"))

    legacy.rename(current)
    for suffix in ("-wal", "-shm"):
        sidecar = config.HOME / f"{LEGACY_DB}{suffix}"
        if sidecar.exists():
            sidecar.rename(config.HOME / f"drove.db{suffix}")
    return current


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    db_path = path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets readers proceed while a write is in flight. The CLI, the daemon and concurrent runs
    # all share this one file, and under the default rollback journal a single write blocks every
    # reader — which shows up as the UI hanging while a run records a session. WAL is persisted in
    # the database header, so this is a one-time change that later connections inherit.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for index, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(sql)
        if step := AFTER.get(index):
            globals()[step](conn)
        conn.execute(f"PRAGMA user_version = {index}")
    conn.commit()


def _backfill_workspaces(conn: sqlite3.Connection) -> None:
    """Give every pre-workspace feature the one-repo workspace it always implicitly had."""
    rows = conn.execute("SELECT DISTINCT repo, base_branch FROM features").fetchall()
    for row in rows:
        repo = Path(row["repo"])
        workspace_id = new_id()
        now = time.time()
        conn.execute(
            "INSERT INTO workspaces (id, name, harness, created_at) VALUES (?,?,?,?)",
            (workspace_id, repo.name or str(repo), None, now),
        )
        conn.execute(
            "INSERT INTO workspace_repos (workspace_id, path, name, base_branch, added_at)"
            " VALUES (?,?,?,?,?)",
            (workspace_id, str(repo), repo.name or "repo", row["base_branch"], now),
        )
        conn.execute(
            "UPDATE features SET workspace_id = ? WHERE repo = ?", (workspace_id, str(repo))
        )


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# --- features --------------------------------------------------------------------------------

def create_feature(
    conn: sqlite3.Connection,
    repo: Path,
    title: str,
    branch: str,
    worktree: Path,
    base: str,
    feature_id: str | None = None,
    workspace_id: str | None = None,
) -> str:
    # The caller usually mints the id first, because the worktree path is derived from it and
    # must exist before there is anything worth recording.
    feature_id = feature_id or new_id()
    conn.execute(
        "INSERT INTO features (id, repo, title, branch, worktree_path, base_branch, status,"
        " created_at, workspace_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            feature_id, str(repo), title, branch, str(worktree), base, "planning", time.time(),
            workspace_id,
        ),
    )
    return feature_id


def set_feature_title(conn: sqlite3.Connection, feature_id: str, title: str) -> None:
    conn.execute("UPDATE features SET title = ? WHERE id = ?", (title, feature_id))


def set_feature_status(conn: sqlite3.Connection, feature_id: str, status: str) -> None:
    conn.execute("UPDATE features SET status = ? WHERE id = ?", (status, feature_id))


def get_feature(conn: sqlite3.Connection, feature_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM features WHERE id = ?", (feature_id,)).fetchone()


def find_feature(conn: sqlite3.Connection, ref: str) -> sqlite3.Row | None:
    """Look a feature up by id, id prefix, or branch — whatever the user typed."""
    row = get_feature(conn, ref)
    if row:
        return row
    return conn.execute(
        "SELECT * FROM features WHERE id LIKE ? OR branch = ? OR branch = ?"
        " ORDER BY created_at DESC LIMIT 1",
        (f"{ref}%", ref, f"vf/{ref}"),
    ).fetchone()


def list_features(conn: sqlite3.Connection, repo: Path | None = None) -> list[sqlite3.Row]:
    if repo is None:
        return conn.execute("SELECT * FROM features ORDER BY created_at DESC").fetchall()
    return conn.execute(
        "SELECT * FROM features WHERE repo = ? ORDER BY created_at DESC", (str(repo),)
    ).fetchall()


def delete_feature(conn: sqlite3.Connection, feature_id: str) -> None:
    conn.execute("DELETE FROM features WHERE id = ?", (feature_id,))


# --- runs ------------------------------------------------------------------------------------

def create_run(conn: sqlite3.Connection, feature_id: str, intent: str) -> tuple[str, int]:
    iteration = (
        conn.execute(
            "SELECT COALESCE(MAX(iteration), 0) + 1 FROM runs WHERE feature_id = ?", (feature_id,)
        ).fetchone()[0]
        or 1
    )
    run_id = new_id()
    conn.execute(
        "INSERT INTO runs (id, feature_id, iteration, intent, status, started_at)"
        " VALUES (?,?,?,?,?,?)",
        (run_id, feature_id, iteration, intent, "planning", time.time()),
    )
    return run_id, iteration


def set_run_plan(conn: sqlite3.Connection, run_id: str, plan: dict[str, Any]) -> None:
    conn.execute("UPDATE runs SET plan_json = ? WHERE id = ?", (json.dumps(plan), run_id))


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    head_sha: str | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE runs SET status = ?, head_sha = ?, ended_at = ?, error = ? WHERE id = ?",
        (status, head_sha, time.time(), error, run_id),
    )


def latest_run(conn: sqlite3.Connection, feature_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runs WHERE feature_id = ? ORDER BY iteration DESC LIMIT 1", (feature_id,)
    ).fetchone()


def list_runs(conn: sqlite3.Connection, feature_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM runs WHERE feature_id = ? ORDER BY iteration", (feature_id,)
    ).fetchall()


# --- sessions ---------------------------------------------------------------------------------

def record_session(
    conn: sqlite3.Connection,
    run_id: str,
    stage: str,
    harness: str,
    session_id: str,
    cwd: Path,
    attempt: int = 1,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float | None = None,
) -> None:
    """Keep every agent conversation addressable.

    `cd <cwd> && claude --resume <session-id>` drops you into the real thing. cwd is stored
    because resume only works from the directory the session started in.
    """
    conn.execute(
        "INSERT OR REPLACE INTO sessions (run_id, stage, attempt, harness, session_id, cwd,"
        " tokens_in, tokens_out, cost_usd) VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, stage, attempt, harness, session_id, str(cwd), tokens_in, tokens_out, cost_usd),
    )


def last_session(conn: sqlite3.Connection, feature_id: str, stage: str) -> sqlite3.Row | None:
    """The most recent session for a stage across all of a feature's runs.

    This is what makes a pivot resume the executor that already knows the codebase.
    """
    return conn.execute(
        "SELECT s.* FROM sessions s JOIN runs r ON r.id = s.run_id"
        " WHERE r.feature_id = ? AND s.stage = ?"
        " ORDER BY r.iteration DESC, s.attempt DESC LIMIT 1",
        (feature_id, stage),
    ).fetchone()


# --- workspaces -------------------------------------------------------------------------------

def set_workspace_config(
    conn: sqlite3.Connection, workspace_id: str, harness: dict, models: dict
) -> None:
    conn.execute(
        "UPDATE workspaces SET harness = ?, models = ? WHERE id = ?",
        (json.dumps(harness), json.dumps(models), workspace_id),
    )


def create_workspace(conn: sqlite3.Connection, name: str, harness: dict | None = None) -> str:
    workspace_id = new_id()
    conn.execute(
        "INSERT INTO workspaces (id, name, harness, created_at) VALUES (?,?,?,?)",
        (workspace_id, name, json.dumps(harness) if harness else None, time.time()),
    )
    return workspace_id


def add_repo(
    conn: sqlite3.Connection, workspace_id: str, path: Path, name: str, base: str
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO workspace_repos (workspace_id, path, name, base_branch, added_at)"
        " VALUES (?,?,?,?,?)",
        (workspace_id, str(path), name, base, time.time()),
    )


def remove_repo(conn: sqlite3.Connection, workspace_id: str, path: Path) -> None:
    conn.execute(
        "DELETE FROM workspace_repos WHERE workspace_id = ? AND path = ?",
        (workspace_id, str(path)),
    )


def get_workspace(conn: sqlite3.Connection, workspace_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()


def find_workspace(conn: sqlite3.Connection, ref: str) -> sqlite3.Row | None:
    row = get_workspace(conn, ref)
    if row:
        return row
    return conn.execute(
        "SELECT * FROM workspaces WHERE id LIKE ? OR name = ? ORDER BY created_at LIMIT 1",
        (f"{ref}%", ref),
    ).fetchone()


def list_workspaces(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM workspaces ORDER BY created_at").fetchall()


def workspace_repos(conn: sqlite3.Connection, workspace_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM workspace_repos WHERE workspace_id = ? ORDER BY added_at",
        (workspace_id,),
    ).fetchall()


def delete_workspace(conn: sqlite3.Connection, workspace_id: str) -> None:
    conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))


def features_in(conn: sqlite3.Connection, workspace_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM features WHERE workspace_id = ? ORDER BY created_at DESC",
        (workspace_id,),
    ).fetchall()


# --- read models for the app's screens ---------------------------------------------------------

def runs_in(conn: sqlite3.Connection, workspace_id: str, limit: int = 100) -> list[sqlite3.Row]:
    """Every run in a workspace, newest first, with its feature and rolled-up cost."""
    return conn.execute(
        """
        SELECT r.*, f.title, f.branch, f.status AS feature_status,
               (SELECT COALESCE(SUM(s.cost_usd), 0) FROM sessions s WHERE s.run_id = r.id) AS cost,
               (SELECT COALESCE(SUM(s.tokens_in), 0) FROM sessions s WHERE s.run_id = r.id) AS tin,
               (SELECT COALESCE(SUM(s.tokens_out), 0) FROM sessions s WHERE s.run_id = r.id) AS tout
        FROM runs r JOIN features f ON f.id = r.feature_id
        WHERE f.workspace_id = ?
        ORDER BY r.started_at DESC LIMIT ?
        """,
        (workspace_id, limit),
    ).fetchall()


def sessions_in(conn: sqlite3.Connection, workspace_id: str, limit: int = 200) -> list[sqlite3.Row]:
    """Every agent session in a workspace — who ran what, on which conversation."""
    return conn.execute(
        """
        SELECT s.*, r.intent, r.iteration, r.status AS run_status, r.started_at, r.ended_at,
               f.id AS feature_id, f.title, f.status AS feature_status
        FROM sessions s
        JOIN runs r ON r.id = s.run_id
        JOIN features f ON f.id = r.feature_id
        WHERE f.workspace_id = ?
        ORDER BY r.started_at DESC, s.attempt DESC LIMIT ?
        """,
        (workspace_id, limit),
    ).fetchall()
