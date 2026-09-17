"""Feature / run / session persistence."""

from __future__ import annotations

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
        conn, Path("/repo"), title, f"vf/{fid}", Path(f"/wt/{fid}"), "main", feature_id=fid
    )
    return fid


def test_migrations_are_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path)
    with db.connect() as c:
        first = c.execute("PRAGMA user_version").fetchone()[0]
    with db.connect() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == first == len(db.MIGRATIONS)


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
    assert db.find_feature(conn, f"vf/{fid}")["id"] == fid
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
