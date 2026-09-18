"""Recognize delivered features whose branches have landed, and reclaim their worktrees."""

from __future__ import annotations

import sqlite3
import time

from drove import db
from drove import workspace as ws_mod
from drove.vcs import tree
from drove.vcs.git import GitError
from drove.vcs.tree import WorktreeError

CHECK_TTL = 15.0
_checked: dict[str, float] = {}


def reconcile(conn: sqlite3.Connection, *, force: bool = False) -> list[sqlite3.Row]:
    """Move merged delivered features to landed without creating missing worktrees."""
    # Lazy to avoid jobs -> pipeline -> tree forming an import cycle at module import time.
    from drove.api import jobs

    landed: list[sqlite3.Row] = []
    now = time.monotonic()
    rows = conn.execute("SELECT * FROM features WHERE status = 'delivered'").fetchall()
    for row in rows:
        feature_id = row["id"]
        if not force and now - _checked.get(feature_id, float("-inf")) < CHECK_TTL:
            continue
        if jobs.is_busy(feature_id):
            continue

        try:
            workspace = ws_mod.get(conn, row["workspace_id"] or "")
            if workspace is None:
                continue
            trees = tree.attach(workspace, feature_id, row["branch"])
            _checked[feature_id] = now
            if not tree.has_landed(trees):
                continue
        except (GitError, WorktreeError, OSError):
            continue

        current = db.get_feature(conn, feature_id)
        if current is None or current["status"] != "delivered":
            continue
        db.set_feature_status(conn, feature_id, "landed")
        landed.append(current)
    return landed


def reclaim(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, tree.Teardown]:
    """Remove a landed feature's safe worktrees while preserving its database history."""
    workspace = ws_mod.get(conn, row["workspace_id"] or "")
    if workspace is None:
        raise WorktreeError(f"feature {row['id']} has no workspace")
    return tree.teardown(tree.attach(workspace, row["id"], row["branch"]), force=False)
