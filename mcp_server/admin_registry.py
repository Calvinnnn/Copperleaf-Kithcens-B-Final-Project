"""Runtime agent/tool management for the Admin Platform.

Issue #4 requires admin changes to affect the LIVE MCP server rather than
only changing frontend state.

Each agent receives agent-scoped MCP tool aliases such as:

    maintenance__get_inventory
    procurement__get_supplier_orders

Adding/removing an assignment therefore adds/removes an actual tool from
the running FastMCP server.
"""

from __future__ import annotations

from typing import Any, Callable

from mcp_server.db import get_connection, get_write_connection


AGENTS = {
    "procurement": "Procurement",
    "food_safety": "Food Safety",
    "maintenance": "Maintenance",
    "memory_rag": "Memory / RAG",
    "planning": "Planning",
}


DEFAULT_ASSIGNMENTS = {
    "procurement": {
        "get_inventory",
        "get_low_stock_items",
        "get_supplier_orders",
    },
    "food_safety": {
        "get_inventory",
        "get_transaction_history",
        "get_run_status",
    },
    "maintenance": {
        "get_inventory",
        "get_supplier_orders",
        "get_run_status",
    },
    "memory_rag": {
        "get_inventory",
        "get_low_stock_items",
        "get_supplier_orders",
        "get_transaction_history",
    },
    "planning": {
        "get_inventory",
        "get_low_stock_items",
        "get_supplier_orders",
        "get_transaction_history",
        "write_off_inventory",
        "generate_waste_report",
    },
}


class RuntimeToolRegistry:
    """Manage agent-specific tools against the live FastMCP instance."""

    def __init__(
        self,
        mcp: Any,
        tool_library: dict[str, Callable[..., Any]],
    ) -> None:
        self.mcp = mcp
        self.tool_library = tool_library

        self._seed_defaults_if_empty()
        self.restore_live_assignments()

    @staticmethod
    def alias_name(agent_id: str, tool_name: str) -> str:
        return f"{agent_id}__{tool_name}"

    def _validate_agent(self, agent_id: str) -> None:
        if agent_id not in AGENTS:
            raise ValueError(f"Unknown agent: {agent_id}")

    def _validate_tool(self, tool_name: str) -> None:
        if tool_name not in self.tool_library:
            raise ValueError(f"Unknown manageable MCP tool: {tool_name}")

    def _seed_defaults_if_empty(self) -> None:
        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM agent_tool_assignments"
            ).fetchone()["count"]

        if count:
            return

        with get_write_connection() as conn:
            for agent_id, tool_names in DEFAULT_ASSIGNMENTS.items():
                for tool_name in tool_names:
                    if tool_name not in self.tool_library:
                        continue

                    conn.execute(
                        """
                        INSERT OR IGNORE INTO agent_tool_assignments
                        (agent_id, tool_name)
                        VALUES (?, ?)
                        """,
                        (agent_id, tool_name),
                    )

    def list_agents(self) -> list[dict]:
        assignments = self.list_assignments()

        by_agent: dict[str, list[str]] = {
            agent_id: [] for agent_id in AGENTS
        }

        for item in assignments:
            by_agent[item["agent_id"]].append(item["tool_name"])

        return [
            {
                "agent_id": agent_id,
                "name": display_name,
                "tools": sorted(by_agent[agent_id]),
            }
            for agent_id, display_name in AGENTS.items()
        ]

    def list_available_tools(self) -> list[dict]:
        return [
            {
                "tool_name": name,
                "description": (
                    (fn.__doc__ or "").strip().splitlines()[0]
                    if fn.__doc__
                    else ""
                ),
            }
            for name, fn in sorted(self.tool_library.items())
        ]

    def list_assignments(self) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT agent_id, tool_name, created_at
                FROM agent_tool_assignments
                ORDER BY agent_id, tool_name
                """
            ).fetchall()

        return [dict(row) for row in rows]

    def _is_live(self, alias: str) -> bool:
        manager = getattr(self.mcp, "_tool_manager", None)

        if manager is None:
            raise RuntimeError(
                "FastMCP tool manager is unavailable."
            )

        return manager.get_tool(alias) is not None

    def _register_live_alias(
        self,
        agent_id: str,
        tool_name: str,
    ) -> str:
        alias = self.alias_name(agent_id, tool_name)

        if not self._is_live(alias):
            source_fn = self.tool_library[tool_name]

            self.mcp.add_tool(
                source_fn,
                name=alias,
                description=(
                    f"Agent-scoped runtime tool for "
                    f"{AGENTS[agent_id]}: {tool_name}"
                ),
            )

        return alias

    def restore_live_assignments(self) -> None:
        for assignment in self.list_assignments():
            agent_id = assignment["agent_id"]
            tool_name = assignment["tool_name"]

            if (
                agent_id in AGENTS
                and tool_name in self.tool_library
            ):
                self._register_live_alias(
                    agent_id,
                    tool_name,
                )

    def assign_tool(
        self,
        agent_id: str,
        tool_name: str,
    ) -> dict:
        self._validate_agent(agent_id)
        self._validate_tool(tool_name)

        with get_write_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO agent_tool_assignments
                (agent_id, tool_name)
                VALUES (?, ?)
                """,
                (agent_id, tool_name),
            )

        alias = self._register_live_alias(
            agent_id,
            tool_name,
        )

        return {
            "agent_id": agent_id,
            "tool_name": tool_name,
            "live_tool_name": alias,
            "assigned": True,
            "live": self._is_live(alias),
        }

    def remove_tool(
        self,
        agent_id: str,
        tool_name: str,
    ) -> dict:
        self._validate_agent(agent_id)
        self._validate_tool(tool_name)

        alias = self.alias_name(
            agent_id,
            tool_name,
        )

        with get_write_connection() as conn:
            conn.execute(
                """
                DELETE FROM agent_tool_assignments
                WHERE agent_id = ? AND tool_name = ?
                """,
                (agent_id, tool_name),
            )

        if self._is_live(alias):
            self.mcp.remove_tool(alias)

        return {
            "agent_id": agent_id,
            "tool_name": tool_name,
            "live_tool_name": alias,
            "assigned": False,
            "live": self._is_live(alias),
        }

    def agent_can_use(
        self,
        agent_id: str,
        tool_name: str,
    ) -> bool:
        self._validate_agent(agent_id)
        self._validate_tool(tool_name)

        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM agent_tool_assignments
                WHERE agent_id = ? AND tool_name = ?
                """,
                (agent_id, tool_name),
            ).fetchone()

        return row is not None