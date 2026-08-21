"""Tests for Issue #7 — Maintenance State Graph."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

import planning_lab.checkpointing as chk
import planning_lab.maintenance_graph as maintenance

from planning_lab.state_graph import (
    RunFailedException,
    RunPausedException,
    RunWaitingException,
    StateGraphRunner,
)


# ---------------------------------------------------------------------
# Temporary checkpoint database
# ---------------------------------------------------------------------

MIGRATE_SQL_PATH = (
    Path(__file__).resolve().parent.parent
    / "db"
    / "migrate_checkpoint.sql"
)


@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    """Use a temporary DB so tests never modify copperleaf.db."""

    db_path = tmp_path / "maintenance_test.db"

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(MIGRATE_SQL_PATH.read_text())
    conn.commit()
    conn.close()

    monkeypatch.setattr(chk, "DB_PATH", db_path)

    return db_path


# ---------------------------------------------------------------------
# Deterministic RAG response for automated tests
# ---------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fake_rag(monkeypatch):
    """Avoid external vector-store dependencies during unit tests."""

    def _fake_retrieve(query: str):
        return {
            "rag_query": query,
            "rag_context": (
                "Maintenance procedure: isolate equipment, inspect failed "
                "components, follow safety procedure, repair, then inspect."
            ),
            "rag_retrieved_count": 1,
            "rag_trace": ["TEST_RAG_RETRIEVAL"],
            "rag_was_rewritten": False,
        }

    monkeypatch.setattr(
        maintenance,
        "retrieve_maintenance_knowledge",
        _fake_retrieve,
    )


def _build_runner(run_id: str) -> StateGraphRunner:
    """Create Maintenance graph in deterministic test mode."""

    graph = maintenance.create_maintenance_graph(
        fast_mode=True,
    )

    return StateGraphRunner(
        graph=graph,
        run_id=run_id,
    )


# ---------------------------------------------------------------------
# Test 1 — Normal maintenance completion
# ---------------------------------------------------------------------

def test_maintenance_completes_without_parts(test_db):
    run_id = str(uuid.uuid4())
    runner = _build_runner(run_id)

    result = runner.run(
        initial_state="maintenance_request",
        initial_data={
            "equipment": "Preparation refrigerator door",
            "issue_description": "Door hinge is loose",
            "branch_id": 1,
            "parts_needed": False,
            "inspection_passed": True,
        },
    )

    assert result["status"] == "COMPLETED"
    assert result["maintenance_completed"] is True
    assert result["maintenance_outcome"] == "RESOLVED"
    assert result["diagnosis_attempt"] == 1

    latest = chk.get_latest_checkpoint(run_id)

    assert latest is not None
    assert latest.status == "completed"


# ---------------------------------------------------------------------
# Test 2 — Real external waiting + checkpoint resume
# ---------------------------------------------------------------------

def test_wait_for_parts_and_resume(test_db):
    run_id = str(uuid.uuid4())
    runner = _build_runner(run_id)

    with pytest.raises(RunWaitingException):
        runner.run(
            initial_state="maintenance_request",
            initial_data={
                "equipment": "Kitchen exhaust fan",
                "issue_description": "Fan motor has failed",
                "branch_id": 1,
                "parts_needed": True,
                "estimated_parts_cost": 250.0,
                "inspection_passed": True,
            },
        )

    waiting_checkpoint = chk.get_latest_checkpoint(run_id)

    assert waiting_checkpoint is not None
    assert waiting_checkpoint.state_name == "wait_for_parts"
    assert waiting_checkpoint.status == "active"
    assert waiting_checkpoint.pending_action == "WAITING_FOR_PARTS"

    # Simulate an external parts-delivery event.
    result = runner.run(
        initial_data={
            "_parts_received": True,
        }
    )

    assert result["parts_received"] is True
    assert result["status"] == "COMPLETED"
    assert result["maintenance_completed"] is True

    final_checkpoint = chk.get_latest_checkpoint(run_id)

    assert final_checkpoint.status == "completed"


# ---------------------------------------------------------------------
# Test 3 — Genuine re-diagnosis graph cycle
# ---------------------------------------------------------------------

def test_failed_inspection_cycles_back_to_diagnosis(test_db):
    run_id = str(uuid.uuid4())
    runner = _build_runner(run_id)

    result = runner.run(
        initial_state="maintenance_request",
        initial_data={
            "equipment": "Walk-in refrigerator",
            "issue_description": "Cooling performance is unstable",
            "parts_needed": False,

            # First inspection fails.
            # Second inspection passes after re-diagnosis.
            "inspection_sequence": [
                False,
                True,
            ],
        },
    )

    assert result["status"] == "COMPLETED"
    assert result["maintenance_completed"] is True

    # Confirms that Diagnose ran again.
    assert result["diagnosis_attempt"] == 2


# ---------------------------------------------------------------------
# Test 4 — HITL administrator approval
# ---------------------------------------------------------------------

def test_safety_critical_maintenance_requires_hitl(test_db):
    run_id = str(uuid.uuid4())
    runner = _build_runner(run_id)

    with pytest.raises(RunPausedException):
        runner.run(
            initial_state="maintenance_request",
            initial_data={
                "equipment": "Commercial gas oven",
                "issue_description": (
                    "Safety inspection required before maintenance"
                ),
                "parts_needed": False,
                "safety_critical": True,
                "inspection_passed": True,
            },
        )

    pending_tasks = chk.get_hitl_tasks(
        status="pending",
    )

    run_tasks = [
        task
        for task in pending_tasks
        if task.run_id == run_id
    ]

    assert len(run_tasks) == 1

    task = run_tasks[0]

    assert (
        task.reason
        == "MAINTENANCE_ADMIN_APPROVAL_REQUIRED"
    )

    # Simulate the admin approving through the platform/backend.
    chk.submit_hitl_decision(
        task.hitl_task_id,
        "approved",
        {
            "approved_by": "maintenance-admin",
        },
    )

    result = runner.run()

    assert result["approval_status"] == "APPROVED_BY_ADMIN"
    assert result["status"] == "COMPLETED"
    assert result["maintenance_completed"] is True


# ---------------------------------------------------------------------
# Test 5 — Unexpected failure creates a real failure ticket
# ---------------------------------------------------------------------

def test_maintenance_failure_creates_ticket(
    test_db,
    monkeypatch,
):
    run_id = str(uuid.uuid4())
    runner = _build_runner(run_id)

    def _broken_rag(query: str):
        raise RuntimeError(
            "Maintenance manual service unavailable"
        )

    monkeypatch.setattr(
        maintenance,
        "retrieve_maintenance_knowledge",
        _broken_rag,
    )

    with pytest.raises(RunFailedException):
        runner.run(
            initial_state="maintenance_request",
            initial_data={
                "equipment": "Industrial dishwasher",
                "issue_description": "Unexpected control fault",
                "parts_needed": False,
            },
        )

    tickets = chk.get_failure_tickets(
        status="open",
    )

    run_tickets = [
        ticket
        for ticket in tickets
        if ticket.run_id == run_id
    ]

    assert len(run_tickets) == 1

    ticket = run_tickets[0]

    assert ticket.failed_node == "diagnose"
    assert ticket.error_type == "RuntimeError"
    assert (
        "Maintenance manual service unavailable"
        in ticket.error_message
    )

    checkpoint = chk.get_latest_checkpoint(run_id)

    assert checkpoint is not None
    assert checkpoint.status == "failed"