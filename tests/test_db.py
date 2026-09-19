"""Feature / run / session persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from drove import config, db


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    with db.connect() as c:
        yield c


def make_feature(conn, title="add oauth", fid=None):
    fid = fid or db.new_id()
    db.create_feature(
        conn, Path("/repo"), title, f"feat/{fid}", Path(f"/wt/{fid}"), "main", feature_id=fid
    )
    return fid


def test_migrations_are_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    with db.connect() as c:
        first = c.execute("PRAGMA user_version").fetchone()[0]
    with db.connect() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == first == len(db.migrations())


def test_runs_iterate_within_a_feature(conn):
    """A feature is durable; runs are its iterations. Pivots append, never replace."""
    fid = make_feature(conn)
    _, first = db.create_run(conn, fid, "build it")
    _, second = db.create_run(conn, fid, "pivot: use PKCE")
    assert (first, second) == (1, 2)
    assert len(db.list_runs(conn, fid)) == 2


def test_iterations_are_per_feature_not_global(conn):
    a, b = make_feature(conn, "a"), make_feature(conn, "b")
    db.create_run(conn, a, "x")
    _, b_first = db.create_run(conn, b, "y")
    assert b_first == 1


def test_find_feature_accepts_id_prefix_or_branch(conn):
    fid = make_feature(conn)
    assert db.find_feature(conn, fid)["id"] == fid
    assert db.find_feature(conn, fid[:6])["id"] == fid
    assert db.find_feature(conn, f"feat/{fid}")["id"] == fid
    assert db.find_feature(conn, "nope") is None


def test_last_session_spans_runs_so_a_pivot_can_resume(conn):
    """The executor session must outlive the run that created it."""
    fid = make_feature(conn)
    run1, _ = db.create_run(conn, fid, "build")
    db.record_session(conn, run1, "execute", "claude", "sess-1", Path("/wt"))
    run2, _ = db.create_run(conn, fid, "pivot")
    db.record_session(conn, run2, "execute", "claude", "sess-2", Path("/wt"))

    assert db.last_session(conn, fid, "execute")["session_id"] == "sess-2"
    assert db.last_session(conn, fid, "review") is None


def test_session_records_cwd_because_resume_depends_on_it(conn):
    fid = make_feature(conn)
    run, _ = db.create_run(conn, fid, "build")
    db.record_session(conn, run, "execute", "claude", "s", Path("/wt/feature"))
    assert db.last_session(conn, fid, "execute")["cwd"] == "/wt/feature"


def test_deleting_a_feature_cascades(conn):
    fid = make_feature(conn)
    run, _ = db.create_run(conn, fid, "build")
    db.record_session(conn, run, "plan", "claude", "s", Path("/wt"))
    db.delete_feature(conn, fid)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_wal_lets_a_reader_work_during_a_write(tmp_path, monkeypatch):
    """The CLI, the daemon and concurrent runs share one database file.

    Under the default rollback journal an open write transaction blocks every reader, which
    surfaces as the UI hanging while a run records a session.
    """
    monkeypatch.setattr(config, "HOME", tmp_path)
    with db.connect() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        fid = make_feature(c, "held open")

    import sqlite3

    writer = sqlite3.connect(db.path())
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE features SET status = 'executing' WHERE id = ?", (fid,))
    try:
        with db.connect() as reader:  # must not block or raise
            assert reader.execute("SELECT COUNT(*) FROM features").fetchone()[0] == 1
    finally:
        writer.rollback()
        writer.close()


# --- interrupted work ---------------------------------------------------------------------------

def test_a_run_whose_owner_died_is_marked_interrupted(conn):
    """The failure this exists for: the daemon stops mid-execute and the row claims to run forever.

    Job state lives in memory, so after a restart nothing remembers the work — but the database
    still says `executing`, and the UI believes it.
    """
    fid = make_feature(conn)
    run_id, _ = db.create_run(conn, fid, "build it")
    db.set_feature_status(conn, fid, "executing")
    # A pid that is not running. 2**22 is above every Linux/macOS pid_max.
    conn.execute("UPDATE runs SET owner_pid = ? WHERE id = ?", (4_194_303, run_id))

    stranded = db.reconcile_interrupted(conn)

    assert [row["id"] for row in stranded] == [fid]
    assert db.get_feature(conn, fid)["status"] == "interrupted"
    run = db.latest_run(conn, fid)
    assert run["status"] == "interrupted"
    assert run["ended_at"] is not None
    assert "while it was implementing" in run["error"]


def test_a_run_owned_by_a_live_process_is_left_alone(conn):
    """`drove execute` in a terminal is not orphaned just because the daemon restarted."""
    fid = make_feature(conn)
    run_id, _ = db.create_run(conn, fid, "build it")
    db.set_feature_status(conn, fid, "executing")
    db.claim_run(conn, run_id)  # claims for this very process, which is by definition alive

    assert db.reconcile_interrupted(conn) == []
    assert db.get_feature(conn, fid)["status"] == "executing"


def test_a_plan_waiting_on_you_survives_a_restart(conn):
    """A gate is not work in flight. Sweeping it would throw away a plan you were about to read."""
    fid = make_feature(conn)
    db.create_run(conn, fid, "build it")
    db.set_feature_status(conn, fid, "awaiting_approval")

    assert db.reconcile_interrupted(conn) == []
    assert db.get_feature(conn, fid)["status"] == "awaiting_approval"


def test_reconciling_keeps_the_commit_a_run_reached(conn):
    """The recorded head is how you find the work afterwards; retiring a run must not erase it."""
    fid = make_feature(conn)
    run_id, _ = db.create_run(conn, fid, "build it")
    db.finish_run(conn, run_id, "delivered", head_sha="abc1234")
    conn.execute("UPDATE runs SET status = 'executing', ended_at = NULL WHERE id = ?", (run_id,))
    db.set_feature_status(conn, fid, "executing")

    db.reconcile_interrupted(conn)

    assert db.latest_run(conn, fid)["head_sha"] == "abc1234"


def test_every_migration_file_is_numbered_in_order():
    """Filename order is application order, so the numbering has to be lexical, not arithmetic.

    `10_x.sql` sorting before `2_x.sql` would silently apply a later migration first and record
    the wrong version — the kind of break that only shows up on someone else's database.
    """
    names = [name for name, _ in db.migrations()]
    assert names == sorted(names)
    prefixes = [name.split("_")[0] for name in names]
    assert prefixes == [f"{i:03d}" for i in range(1, len(names) + 1)]


def test_a_data_migration_is_registered_as_a_callable_not_a_name():
    """A name resolved at runtime fails on the next real upgrade; an import fails immediately."""
    for version, step in db.AFTER.items():
        assert callable(step), f"migration {version} data step is not callable"
        assert isinstance(version, int)


def test_every_migration_is_valid_sql(tmp_path):
    """Each file runs standalone, in order, against an empty database."""
    conn = sqlite3.connect(tmp_path / "probe.db")
    for name, sql in db.migrations():
        try:
            conn.executescript(sql)
        except sqlite3.Error as exc:  # pragma: no cover - only on a broken migration
            raise AssertionError(f"{name} is not valid SQL: {exc}") from exc
    conn.close()
