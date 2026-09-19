-- The spine: a feature is durable, a run is one iteration against it, and a session is one
-- agent conversation inside a run. Sessions exist so every conversation stays addressable after
-- the fact — `cd <worktree> && claude --resume <session-id>` drops you into the real thing.

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
