-- Workspaces. A workspace is a named set of repos worked on together, so one feature can change
-- an API in one repo and its caller in another. Schema only: existing rows are backfilled by the
-- data step registered for this version, because deriving a repo's name from its path is a
-- basename operation and SQLite has no clean way to express one.

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
