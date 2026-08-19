"""Checkpointing, HITL Task, and Failure Ticket Database Helpers.

This module provides persistent state-management infrastructure for checkpoints,
human-in-the-loop tasks, and failure tickets in the SQLite database.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field

# Target db path
DB_PATH = Path(__file__).resolve().parent.parent / "db" / "copperleaf.db"


@contextmanager
def get_connection():
    """Yield a SQLite connection with foreign keys enforced and Row access."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_write_connection():
    """Yield a SQLite connection wrapped in an explicit transaction."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────────────────────

class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str
    run_id: str
    graph_id: str
    state_name: str
    state_data: Dict[str, Any]
    completed_steps: List[str]
    pending_action: Optional[str] = None
    checkpoint_version: int
    status: str  # 'active', 'paused_hitl', 'failed', 'completed'
    created_at: str
    updated_at: str


class HITLTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hitl_task_id: str
    run_id: str
    graph_id: str
    checkpoint_id: str
    state_name: str
    reason: str
    context: Dict[str, Any]
    status: str  # 'pending', 'approved', 'rejected', 'resolved'
    decision: Optional[str] = None
    decision_data: Optional[Dict[str, Any]] = None
    created_at: str
    resolved_at: Optional[str] = None
    updated_at: str


class FailureTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_ticket_id: str
    run_id: str
    graph_id: str
    checkpoint_id: str
    failed_node: str
    error_type: str
    error_message: str
    error_details: Optional[str] = None
    state_snapshot: Dict[str, Any]
    status: str  # 'open', 'investigating', 'resolved'
    resolution: Optional[str] = None
    created_at: str
    resolved_at: Optional[str] = None
    updated_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Checkpoint Database Operations
# ─────────────────────────────────────────────────────────────────────────────

def create_checkpoint(
    run_id: str,
    graph_id: str,
    state_name: str,
    state_data: Dict[str, Any],
    completed_steps: List[str],
    pending_action: Optional[str] = None,
    status: str = "active",
) -> Checkpoint:
    """Create a new durable checkpoint for a run, automatically incrementing version."""
    checkpoint_id = str(uuid.uuid4())
    now_str = datetime.now(timezone.utc).isoformat()

    with get_write_connection() as conn:
        # Determine the next checkpoint version for this run_id
        row = conn.execute(
            "SELECT MAX(checkpoint_version) as max_v FROM checkpoints WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        next_version = (row["max_v"] + 1) if (row and row["max_v"] is not None) else 1

        conn.execute(
            """
            INSERT INTO checkpoints (
                checkpoint_id, run_id, graph_id, state_name, state_data_json,
                completed_steps_json, pending_action, checkpoint_version, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint_id,
                run_id,
                graph_id,
                state_name,
                json.dumps(state_data),
                json.dumps(completed_steps),
                pending_action,
                next_version,
                status,
                now_str,
                now_str,
            ),
        )

    return Checkpoint(
        checkpoint_id=checkpoint_id,
        run_id=run_id,
        graph_id=graph_id,
        state_name=state_name,
        state_data=state_data,
        completed_steps=completed_steps,
        pending_action=pending_action,
        checkpoint_version=next_version,
        status=status,
        created_at=now_str,
        updated_at=now_str,
    )


def get_checkpoint(checkpoint_id: str) -> Optional[Checkpoint]:
    """Retrieve a specific checkpoint by ID."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        ).fetchone()
        if not row:
            return None

        return Checkpoint(
            checkpoint_id=row["checkpoint_id"],
            run_id=row["run_id"],
            graph_id=row["graph_id"],
            state_name=row["state_name"],
            state_data=json.loads(row["state_data_json"]),
            completed_steps=json.loads(row["completed_steps_json"]),
            pending_action=row["pending_action"],
            checkpoint_version=row["checkpoint_version"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def get_latest_checkpoint(run_id: str) -> Optional[Checkpoint]:
    """Retrieve the latest checkpoint for a run based on version."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM checkpoints WHERE run_id = ? ORDER BY checkpoint_version DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if not row:
            return None

        return Checkpoint(
            checkpoint_id=row["checkpoint_id"],
            run_id=row["run_id"],
            graph_id=row["graph_id"],
            state_name=row["state_name"],
            state_data=json.loads(row["state_data_json"]),
            completed_steps=json.loads(row["completed_steps_json"]),
            pending_action=row["pending_action"],
            checkpoint_version=row["checkpoint_version"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def get_run_checkpoints(run_id: str) -> List[Checkpoint]:
    """Retrieve all checkpoints for a run ordered by version."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM checkpoints WHERE run_id = ? ORDER BY checkpoint_version ASC",
            (run_id,),
        ).fetchall()
        return [
            Checkpoint(
                checkpoint_id=r["checkpoint_id"],
                run_id=r["run_id"],
                graph_id=r["graph_id"],
                state_name=r["state_name"],
                state_data=json.loads(r["state_data_json"]),
                completed_steps=json.loads(r["completed_steps_json"]),
                pending_action=r["pending_action"],
                checkpoint_version=r["checkpoint_version"],
                status=r["status"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]


# ─────────────────────────────────────────────────────────────────────────────
# HITL Task Database Operations
# ─────────────────────────────────────────────────────────────────────────────

def create_hitl_task(
    run_id: str,
    graph_id: str,
    checkpoint_id: str,
    state_name: str,
    reason: str,
    context: Dict[str, Any],
) -> HITLTask:
    """Create a new human-in-the-loop task associated with a checkpoint."""
    hitl_task_id = str(uuid.uuid4())
    now_str = datetime.now(timezone.utc).isoformat()

    with get_write_connection() as conn:
        # Update checkpoint status to paused_hitl
        conn.execute(
            "UPDATE checkpoints SET status = 'paused_hitl', updated_at = ? WHERE checkpoint_id = ?",
            (now_str, checkpoint_id),
        )

        conn.execute(
            """
            INSERT INTO hitl_tasks (
                hitl_task_id, run_id, graph_id, checkpoint_id, state_name,
                reason, context_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                hitl_task_id,
                run_id,
                graph_id,
                checkpoint_id,
                state_name,
                reason,
                json.dumps(context),
                now_str,
                now_str,
            ),
        )

    return HITLTask(
        hitl_task_id=hitl_task_id,
        run_id=run_id,
        graph_id=graph_id,
        checkpoint_id=checkpoint_id,
        state_name=state_name,
        reason=reason,
        context=context,
        status="pending",
        created_at=now_str,
        updated_at=now_str,
    )


def submit_hitl_decision(
    task_id: str,
    decision: str,
    decision_data: Optional[Dict[str, Any]] = None,
) -> HITLTask:
    """Submit a decision for a pending HITL task and resolve it."""
    if decision not in ("approved", "rejected", "resolved"):
        raise ValueError(f"Invalid decision: {decision!r}. Must be 'approved', 'rejected', or 'resolved'.")

    now_str = datetime.now(timezone.utc).isoformat()
    decision_data = decision_data or {}

    with get_write_connection() as conn:
        row = conn.execute(
            "SELECT * FROM hitl_tasks WHERE hitl_task_id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"HITL Task not found: {task_id!r}")
        if row["status"] != "pending":
            raise ValueError(f"HITL Task is already resolved: {row['status']!r}")

        conn.execute(
            """
            UPDATE hitl_tasks
            SET status = ?, decision = ?, decision_data_json = ?, resolved_at = ?, updated_at = ?
            WHERE hitl_task_id = ?
            """,
            (
                decision,
                decision,
                json.dumps(decision_data),
                now_str,
                now_str,
                task_id,
            ),
        )

    return get_hitl_task(task_id)


def get_hitl_task(task_id: str) -> Optional[HITLTask]:
    """Retrieve a specific HITL task by ID."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM hitl_tasks WHERE hitl_task_id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            return None

        return HITLTask(
            hitl_task_id=row["hitl_task_id"],
            run_id=row["run_id"],
            graph_id=row["graph_id"],
            checkpoint_id=row["checkpoint_id"],
            state_name=row["state_name"],
            reason=row["reason"],
            context=json.loads(row["context_json"]),
            status=row["status"],
            decision=row["decision"],
            decision_data=json.loads(row["decision_data_json"]) if row["decision_data_json"] else None,
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
            updated_at=row["updated_at"],
        )


def get_hitl_tasks(status: Optional[str] = None) -> List[HITLTask]:
    """Retrieve all HITL tasks, optionally filtered by status."""
    query = "SELECT * FROM hitl_tasks"
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [
            HITLTask(
                hitl_task_id=r["hitl_task_id"],
                run_id=r["run_id"],
                graph_id=r["graph_id"],
                checkpoint_id=r["checkpoint_id"],
                state_name=r["state_name"],
                reason=r["reason"],
                context=json.loads(r["context_json"]),
                status=r["status"],
                decision=r["decision"],
                decision_data=json.loads(r["decision_data_json"]) if r["decision_data_json"] else None,
                created_at=r["created_at"],
                resolved_at=r["resolved_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Failure Ticket Database Operations
# ─────────────────────────────────────────────────────────────────────────────

def create_failure_ticket(
    run_id: str,
    graph_id: str,
    checkpoint_id: str,
    failed_node: str,
    error_type: str,
    error_message: str,
    error_details: Optional[str],
    state_snapshot: Dict[str, Any],
) -> FailureTicket:
    """Create a new failure ticket associated with a checkpoint and mark checkpoint status as failed."""
    failure_ticket_id = str(uuid.uuid4())
    now_str = datetime.now(timezone.utc).isoformat()

    with get_write_connection() as conn:
        # Update checkpoint status to failed
        conn.execute(
            "UPDATE checkpoints SET status = 'failed', updated_at = ? WHERE checkpoint_id = ?",
            (now_str, checkpoint_id),
        )

        conn.execute(
            """
            INSERT INTO failure_tickets (
                failure_ticket_id, run_id, graph_id, checkpoint_id, failed_node,
                error_type, error_message, error_details, state_snapshot_json, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                failure_ticket_id,
                run_id,
                graph_id,
                checkpoint_id,
                failed_node,
                error_type,
                error_message,
                error_details,
                json.dumps(state_snapshot),
                now_str,
                now_str,
            ),
        )

    return FailureTicket(
        failure_ticket_id=failure_ticket_id,
        run_id=run_id,
        graph_id=graph_id,
        checkpoint_id=checkpoint_id,
        failed_node=failed_node,
        error_type=error_type,
        error_message=error_message,
        error_details=error_details,
        state_snapshot=state_snapshot,
        status="open",
        created_at=now_str,
        updated_at=now_str,
    )


def resolve_failure_ticket(ticket_id: str, resolution: str) -> FailureTicket:
    """Resolve an open failure ticket."""
    now_str = datetime.now(timezone.utc).isoformat()

    with get_write_connection() as conn:
        row = conn.execute(
            "SELECT * FROM failure_tickets WHERE failure_ticket_id = ?",
            (ticket_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Failure Ticket not found: {ticket_id!r}")
        if row["status"] == "resolved":
            raise ValueError(f"Failure Ticket is already resolved.")

        conn.execute(
            """
            UPDATE failure_tickets
            SET status = 'resolved', resolution = ?, resolved_at = ?, updated_at = ?
            WHERE failure_ticket_id = ?
            """,
            (
                resolution,
                now_str,
                now_str,
                ticket_id,
            ),
        )

    return get_failure_ticket(ticket_id)


def get_failure_ticket(ticket_id: str) -> Optional[FailureTicket]:
    """Retrieve a specific failure ticket by ID."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM failure_tickets WHERE failure_ticket_id = ?",
            (ticket_id,),
        ).fetchone()
        if not row:
            return None

        return FailureTicket(
            failure_ticket_id=row["failure_ticket_id"],
            run_id=row["run_id"],
            graph_id=row["graph_id"],
            checkpoint_id=row["checkpoint_id"],
            failed_node=row["failed_node"],
            error_type=row["error_type"],
            error_message=row["error_message"],
            error_details=row["error_details"],
            state_snapshot=json.loads(row["state_snapshot_json"]),
            status=row["status"],
            resolution=row["resolution"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
            updated_at=row["updated_at"],
        )


def get_failure_tickets(status: Optional[str] = None) -> List[FailureTicket]:
    """Retrieve all failure tickets, optionally filtered by status."""
    query = "SELECT * FROM failure_tickets"
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [
            FailureTicket(
                failure_ticket_id=r["failure_ticket_id"],
                run_id=r["run_id"],
                graph_id=r["graph_id"],
                checkpoint_id=r["checkpoint_id"],
                failed_node=r["failed_node"],
                error_type=r["error_type"],
                error_message=r["error_message"],
                error_details=r["error_details"],
                state_snapshot=json.loads(r["state_snapshot_json"]),
                status=r["status"],
                resolution=r["resolution"],
                created_at=r["created_at"],
                resolved_at=r["resolved_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]
