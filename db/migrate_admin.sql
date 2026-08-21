-- Admin Platform runtime agent/tool assignments

CREATE TABLE IF NOT EXISTS agent_tool_assignments (
    agent_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (agent_id, tool_name)
);

CREATE INDEX IF NOT EXISTS idx_agent_tool_assignments_agent
ON agent_tool_assignments(agent_id);