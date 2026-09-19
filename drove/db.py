"""SQLite state: features, runs, sessions.

Schema changes are append-only migrations keyed on ``PRAGMA user_version`` — never edits to an
existing migration, because someone's database is already at that version.

What lives here is *state*. Harness output does not: raw JSONL stays on disk under
~/.drove/runs/<run-id>/, where it is simultaneously the audit log, the replay source and a test
fixture. Putting megabytes of transcript in SQLite would buy nothing.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from drove import config

# Migrations live one-per-file in drove/migrations/, applied in filename order, and the index is
# the schema version. They are append-only: a schema is not a desired state you declare, it is the
# sequence of transformations already applied to databases that exist in the world. Someone on
# version 3 gets to 5 by running exactly 004 and 005, in that order, forever — so editing a
# migration that has shipped is the one mistake that cannot be undone from here.
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def migrations() -> list[tuple[str, str]]:
    """(name, sql) for every migration, in version order.

    Sorted by filename, which is why they are numbered: lexical order and application order have
    to be the same thing, and `010` must not sort before `2`.
    """
    return [(path.name, path.read_text()) for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]


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


# Data migrations that need real code, keyed by the schema version they run after. The callable
# itself, not its name: a typo in a string is found by whoever next upgrades a real database,
# which is the worst possible moment and the worst possible person.
AFTER: dict[int, Callable[[sqlite3.Connection], None]] = {2: _backfill_workspaces}


def path() -> Path:
    return config.HOME / "drove.db"


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
    for index, (name, sql) in enumerate(migrations()[version:], start=version + 1):
        try:
            conn.executescript(sql)
        except sqlite3.Error as exc:
            # Name the file. A migration failure otherwise reports a line number inside a script
            # the reader cannot see, on a database they now cannot open.
            raise sqlite3.Error(f"migration {name} failed: {exc}") from exc
        if step := AFTER.get(index):
            step(conn)
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
        (f"{ref}%", ref, f"%/{ref}"),
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



# --- interrupted work ---------------------------------------------------------------------------

# Feature statuses that assert a process is working right now. Gates like `awaiting_approval` are
# deliberately absent: nothing is running there, and a plan waiting on you must survive a restart.
ACTIVE_STATUSES = ("planning", "approved", "executing", "fixing", "reviewing", "verifying")

# What the machine was doing, in words that survive being read a day later.
STOPPED_DURING = {
    "planning": "while it was planning",
    "approved": "before it started work",
    "executing": "while it was implementing",
    "fixing": "while it was fixing review issues",
    "reviewing": "while it was under review",
    "verifying": "while your checks were running",
}


def claim_run(conn: sqlite3.Connection, run_id: str) -> None:
    """Record this process as the owner, so an abandoned run can be told from a live one.

    Clears any pending cancel request: it was aimed at an earlier attempt, and leaving it set
    would stop this one the moment it first looked.
    """
    conn.execute(
        "UPDATE runs SET owner_pid = ?, cancel_requested = NULL WHERE id = ?",
        (os.getpid(), run_id),
    )


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


def process_alive(pid: int | None) -> bool:
    """Whether a recorded owner is still running."""
    return _alive(pid)


def is_drove_process(pid: int | None) -> bool:
    """Whether `pid` looks like a drove process, before we send it anything.

    Pids are reused. Signalling one purely because a row names it risks interrupting whatever
    happens to hold that number now, so the command line has to agree before we act on it.
    """
    if not pid:
        return False
    proc = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True
    )
    return proc.returncode == 0 and "drove" in proc.stdout.lower()


def request_cancel(conn: sqlite3.Connection, run_id: str) -> None:
    """Ask whoever owns this run to stop, without being able to reach into their process."""
    conn.execute("UPDATE runs SET cancel_requested = ? WHERE id = ?", (time.time(), run_id))


def cancel_requested(run_id: str) -> bool:
    """Whether someone has asked for this run to stop. Polled by the process that owns it.

    Opens its own connection: the caller is mid-run and holds no transaction, and the answer has
    to come from what another process has since written, not from a stale snapshot.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT cancel_requested FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    return bool(row and row["cancel_requested"])


def cancel_run(conn: sqlite3.Connection, feature_id: str) -> str:
    """Record that a person stopped this, in terms true of the stage it stopped in.

    Shared by the daemon and by Ctrl-C in `drove execute`, because both are the same event and
    the wording, the status and the preserved head_sha should not depend on which one it was.
    """
    feature = get_feature(conn, feature_id)
    status = feature["status"] if feature is not None else ""
    reason = f"You stopped this {STOPPED_DURING.get(status, 'mid-run')}."
    if status not in ("planning", "approved"):
        # Only true once there was something to commit. Saying it during planning sends people
        # looking for a branch that has nothing on it.
        reason += " Any commits it had already made are still on the branch."
    if run := latest_run(conn, feature_id):
        # Not finish_run: that writes head_sha unconditionally, so cancelling a retry would
        # erase the commit the previous attempt recorded.
        conn.execute(
            "UPDATE runs SET status = ?, ended_at = ?, error = ? WHERE id = ?",
            ("cancelled", time.time(), reason, run["id"]),
        )
    set_feature_status(conn, feature_id, "cancelled")
    return reason


def reconcile_interrupted(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Retire work whose owning process is gone, and return the features it belonged to.

    Called at daemon startup. Without it a feature interrupted by a shutdown, a crash or a closed
    laptop keeps its running status forever: the UI reads the database and shows a live spinner,
    the scheduler reads memory and sees nothing, and no screen offers a way out. Marking these
    `interrupted` turns a dead end into the one thing it should be — a run you can resume.
    """
    marks = ",".join("?" * len(ACTIVE_STATUSES))
    stranded: list[sqlite3.Row] = []
    for feature in conn.execute(
        f"SELECT * FROM features WHERE status IN ({marks})", ACTIVE_STATUSES
    ).fetchall():
        run = latest_run(conn, feature["id"])
        if run and _alive(run["owner_pid"]):
            continue  # a `drove execute` in a terminal is still working on it
        reason = (
            f"Drove stopped {STOPPED_DURING.get(feature['status'], 'mid-run')}. "
            "Any commits it had already made are still on the branch."
        )
        if run and run["ended_at"] is None:
            # Not finish_run: that writes head_sha, and passing None would erase a recorded one.
            conn.execute(
                "UPDATE runs SET status = ?, ended_at = ?, error = ? WHERE id = ?",
                ("interrupted", time.time(), reason, run["id"]),
            )
        set_feature_status(conn, feature["id"], "interrupted")
        stranded.append(feature)
    return stranded


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
