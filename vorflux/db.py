"""SQLite state: features, runs, sessions.

Schema changes are append-only migrations keyed on ``PRAGMA user_version`` — never edits to an
existing migration, because someone's database is already at that version.

What lives here is *state*. Harness output does not: raw JSONL stays on disk under
~/.vorflux/runs/<run-id>/, where it is simultaneously the audit log, the replay source and a test
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

from vorflux import config

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
]


def path() -> Path:
    return config.HOME / "vorflux.db"


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
        conn.execute(f"PRAGMA user_version = {index}")
    conn.commit()


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
) -> str:
    # The caller usually mints the id first, because the worktree path is derived from it and
    # must exist before there is anything worth recording.
    feature_id = feature_id or new_id()
    conn.execute(
        "INSERT INTO features (id, repo, title, branch, worktree_path, base_branch, status,"
        " created_at) VALUES (?,?,?,?,?,?,?,?)",
        (feature_id, str(repo), title, branch, str(worktree), base, "planning", time.time()),
    )
    return feature_id


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
    conn: sqlite3.Connection, run_id: str, status: str, head_sha: str | None = None
) -> None:
    conn.execute(
        "UPDATE runs SET status = ?, head_sha = ?, ended_at = ? WHERE id = ?",
        (status, head_sha, time.time(), run_id),
    )


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
