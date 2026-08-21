"""Tests for Issue #4 — Admin runtime MCP tool management."""

from __future__ import annotations

import sqlite3

import pytest
from mcp.server.fastmcp import FastMCP

import mcp_server.db as db
from mcp_server.admin_registry import (
    AGENTS,
    RuntimeToolRegistry,
)


# ---------------------------------------------------------------------
# Test tools
# ---------------------------------------------------------------------

def get_inventory(branch_id: int) -> dict:
    """Test inventory lookup."""
    return {"branch_id": branch_id}


def get_run_status(run_id: str) -> dict:
    """Test run status lookup."""
    return {"run_id": run_id}


# ---------------------------------------------------------------------
# Temporary database
# ---------------------------------------------------------------------

@pytest.fixture()
def admin_db(tmp_path, monkeypatch):
    """Use a temporary DB instead of db/copperleaf.db."""

    db_path = tmp_path / "admin_test.db"

    conn = sqlite3.connect(db_path)

    conn.executescript(
        """
        CREATE TABLE agent_tool_assignments (
            agent_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (agent_id, tool_name)
        );

        CREATE INDEX idx_agent_tool_assignments_agent
        ON agent_tool_assignments(agent_id);
        """
    )

    conn.commit()
    conn.close()

    monkeypatch.setattr(
        db,
        "DB_PATH",
        db_path,
    )

    return db_path


@pytest.fixture()
def registry(admin_db):
    """Create a real FastMCP instance for runtime tool tests."""

    mcp = FastMCP("admin-registry-test")

    tool_library = {
        "get_inventory": get_inventory,
        "get_run_status": get_run_status,
    }

    runtime_registry = RuntimeToolRegistry(
        mcp=mcp,
        tool_library=tool_library,
    )

    return runtime_registry, mcp


# ---------------------------------------------------------------------
# Test 1 — Agents visible to admin
# ---------------------------------------------------------------------

def test_admin_can_list_agents(registry):
    runtime_registry, _ = registry

    agents = runtime_registry.list_agents()

    agent_ids = {
        agent["agent_id"]
        for agent in agents
    }

    assert agent_ids == set(AGENTS.keys())

    assert "procurement" in agent_ids
    assert "food_safety" in agent_ids
    assert "maintenance" in agent_ids
    assert "memory_rag" in agent_ids
    assert "planning" in agent_ids


# ---------------------------------------------------------------------
# Test 2 — Tools visible to admin
# ---------------------------------------------------------------------

def test_admin_can_list_available_tools(registry):
    runtime_registry, _ = registry

    tools = runtime_registry.list_available_tools()

    tool_names = {
        tool["tool_name"]
        for tool in tools
    }

    assert tool_names == {
        "get_inventory",
        "get_run_status",
    }


# ---------------------------------------------------------------------
# Test 3 — Add tool persists AND reaches live MCP
# ---------------------------------------------------------------------

def test_assign_tool_updates_database_and_live_mcp(
    registry,
    admin_db,
):
    runtime_registry, mcp = registry

    result = runtime_registry.assign_tool(
        agent_id="maintenance",
        tool_name="get_inventory",
    )

    assert result["assigned"] is True
    assert result["live"] is True

    alias = "maintenance__get_inventory"

    live_tool = mcp._tool_manager.get_tool(alias)

    assert live_tool is not None
    assert live_tool.name == alias

    conn = sqlite3.connect(admin_db)

    row = conn.execute(
        """
        SELECT agent_id, tool_name
        FROM agent_tool_assignments
        WHERE agent_id = ?
        AND tool_name = ?
        """,
        (
            "maintenance",
            "get_inventory",
        ),
    ).fetchone()

    conn.close()

    assert row is not None
    assert row[0] == "maintenance"
    assert row[1] == "get_inventory"


# ---------------------------------------------------------------------
# Test 4 — Remove tool updates DB AND live MCP
# ---------------------------------------------------------------------

def test_remove_tool_updates_database_and_live_mcp(
    registry,
    admin_db,
):
    runtime_registry, mcp = registry

    runtime_registry.assign_tool(
        agent_id="maintenance",
        tool_name="get_run_status",
    )

    alias = "maintenance__get_run_status"

    assert (
        mcp._tool_manager.get_tool(alias)
        is not None
    )

    result = runtime_registry.remove_tool(
        agent_id="maintenance",
        tool_name="get_run_status",
    )

    assert result["assigned"] is False
    assert result["live"] is False

    assert (
        mcp._tool_manager.get_tool(alias)
        is None
    )

    conn = sqlite3.connect(admin_db)

    row = conn.execute(
        """
        SELECT 1
        FROM agent_tool_assignments
        WHERE agent_id = ?
        AND tool_name = ?
        """,
        (
            "maintenance",
            "get_run_status",
        ),
    ).fetchone()

    conn.close()

    assert row is None


# ---------------------------------------------------------------------
# Test 5 — Invalid agent/tool rejected
# ---------------------------------------------------------------------

def test_invalid_assignment_is_rejected(registry):
    runtime_registry, _ = registry

    with pytest.raises(
        ValueError,
        match="Unknown agent",
    ):
        runtime_registry.assign_tool(
            "does-not-exist",
            "get_inventory",
        )

    with pytest.raises(
        ValueError,
        match="Unknown manageable MCP tool",
    ):
        runtime_registry.assign_tool(
            "maintenance",
            "fake_tool",
        )