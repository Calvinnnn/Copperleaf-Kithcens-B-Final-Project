-- Checkpointing and HITL recovery migration
-- Adds checkpoints, hitl_tasks, and failure_tickets tables

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------
-- CHECKPOINTS
-- Stores checkpoints to recover execution state of runs.
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id          TEXT PRIMARY KEY,
    run_id                 TEXT NOT NULL,
    graph_id               TEXT NOT NULL,
    state_name             TEXT NOT NULL,
    state_data_json        TEXT NOT NULL,
    completed_steps_json   TEXT NOT NULL,
    pending_action         TEXT,
    checkpoint_version     INTEGER NOT NULL,
    status                 TEXT NOT NULL CHECK(status IN ('active', 'paused_hitl', 'failed', 'completed')),
    created_at             TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    updated_at             TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    UNIQUE(run_id, checkpoint_version)
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_run    ON checkpoints(run_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_status ON checkpoints(status);

-- ----------------------------------------------------------
-- HITL_TASKS
-- Stores human-in-the-loop task definitions and decisions.
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS hitl_tasks (
    hitl_task_id          TEXT PRIMARY KEY,
    run_id                 TEXT NOT NULL,
    graph_id               TEXT NOT NULL,
    checkpoint_id          TEXT NOT NULL,
    state_name             TEXT NOT NULL,
    reason                 TEXT NOT NULL,
    context_json           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected', 'resolved')),
    decision               TEXT,
    decision_data_json     TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    resolved_at            TEXT,
    updated_at             TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY(checkpoint_id) REFERENCES checkpoints(checkpoint_id)
);

CREATE INDEX IF NOT EXISTS idx_hitl_run    ON hitl_tasks(run_id);
CREATE INDEX IF NOT EXISTS idx_hitl_status ON hitl_tasks(status);

-- ----------------------------------------------------------
-- FAILURE_TICKETS
-- Stores failure tickets generated upon unexpected execution errors.
-- ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS failure_tickets (
    failure_ticket_id     TEXT PRIMARY KEY,
    run_id                 TEXT NOT NULL,
    graph_id               TEXT NOT NULL,
    checkpoint_id          TEXT NOT NULL,
    failed_node            TEXT NOT NULL,
    error_type             TEXT NOT NULL,
    error_message          TEXT NOT NULL,
    error_details          TEXT,
    state_snapshot_json    TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'investigating', 'resolved')),
    resolution             TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    resolved_at            TEXT,
    updated_at             TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
    FOREIGN KEY(checkpoint_id) REFERENCES checkpoints(checkpoint_id)
);

CREATE INDEX IF NOT EXISTS idx_failures_run    ON failure_tickets(run_id);
CREATE INDEX IF NOT EXISTS idx_failures_status ON failure_tickets(status);
