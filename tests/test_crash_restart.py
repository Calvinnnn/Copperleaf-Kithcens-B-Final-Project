"""tests/test_crash_restart.py

Integration test proving process crash recovery and restart from durable SQLite checkpoints.

Simulates Python process crashes (e.g. power loss, OS SIGKILL) during state graph execution,
re-instantiates state graph runners from sqlite db state, and verifies execution resumes
seamlessly from saved checkpoints without repeating previously completed state nodes.
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

import planning_lab.checkpointing as chk
from planning_lab.food_safety_graph import create_food_safety_graph
from planning_lab.procurement_graph import create_procurement_graph
from planning_lab.state_graph import RunPausedException, StateGraphRunner

MIGRATE_SQL_PATH = (
    Path(__file__).resolve().parent.parent / "db" / "migrate_checkpoint.sql"
)


@pytest.fixture()
def crash_test_db(tmp_path, monkeypatch):
    """Fixture: patches DB_PATH to a temporary DB for crash test isolation."""
    db_path = tmp_path / "crash_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(MIGRATE_SQL_PATH.read_text())
    conn.commit()
    conn.close()

    monkeypatch.setattr(chk, "DB_PATH", db_path)
    return db_path


def test_procurement_graph_crash_and_restart_recovery(crash_test_db):
    """Simulates a process crash during Procurement workflow external supplier wait state.

    1. Process A executes graph up to waiting_for_supplier pause, saving checkpoint v4 to SQLite.
    2. Process A CRASHES (object garbage collected, execution stops).
    3. Process B spawns, instantiates fresh runner with existing run_id, loads state from SQLite.
    4. Process B resumes execution successfully to terminal state without re-running completed steps.
    """
    run_id = str(uuid.uuid4())

    # --- PROCESS A EXECUTION (Pre-crash) ---
    graph_a = create_procurement_graph()
    runner_a = StateGraphRunner(graph_a, run_id)

    with pytest.raises(RunPausedException):
        runner_a.run(
            initial_state="receive_request",
            initial_data={"raw_request": "Order 10kg tomatoes and 5kg onions"},
        )

    hitl_task = next(t for t in chk.get_hitl_tasks(status="pending") if t.run_id == run_id)
    assert hitl_task.reason == "WAITING_FOR_SUPPLIER"

    # Simulate Process A crash (del runner_a)
    del runner_a
    del graph_a

    # --- SIMULATE EXTERNAL PAYLOAD ARRIVAL IN PERSISTENT DB ---
    chk.submit_hitl_decision(
        hitl_task.hitl_task_id,
        "approved",
        {"decision": "ACCEPTED", "order_ref": "ORD-9981"},
    )

    # --- PROCESS B EXECUTION (Post-crash restart) ---
    graph_b = create_procurement_graph()
    runner_b = StateGraphRunner(graph_b, run_id)

    # Process B resumes without passing initial_data or initial_state
    final_result = runner_b.run()

    assert final_result["status"] == "COMPLETED"
    assert final_result["supplier_response_status"] == "ACCEPTED"
    cps = chk.get_run_checkpoints(run_id)
    assert "receive_request" in cps[-1].completed_steps
    assert "decompose_request" in cps[-1].completed_steps
    assert "receive_order" in cps[-1].completed_steps


def test_food_safety_graph_crash_and_restart_recovery(crash_test_db):
    """Simulates a process crash during Food Safety workflow admin review pause.

    1. Process A executes food safety graph up to admin_review HITL pause.
    2. Process A CRASHES.
    3. Admin approves HITL task in DB.
    4. Process B spawns, instantiates runner, resumes execution from checkpoint.
    """
    run_id = str(uuid.uuid4())

    # --- PROCESS A EXECUTION ---
    graph_a = create_food_safety_graph()
    runner_a = StateGraphRunner(graph_a, run_id)

    with pytest.raises(RunPausedException):
        runner_a.run(
            initial_state="receive_inspection_request",
            initial_data={"branch_id": 1, "fridge_temp_celsius": 9.5},
        )

    task_a = next(t for t in chk.get_hitl_tasks(status="pending") if t.run_id == run_id)
    assert "High-risk food safety" in task_a.reason

    # Process A crashes
    del runner_a
    del graph_a

    # Admin approves
    chk.submit_hitl_decision(task_a.hitl_task_id, "approved")

    # --- PROCESS B EXECUTION ---
    graph_b = create_food_safety_graph()
    runner_b = StateGraphRunner(graph_b, run_id)

    # Resumes post-crash, hits staff wait state
    with pytest.raises(RunPausedException):
        runner_b.run()

    wait_task = next(t for t in chk.get_hitl_tasks(status="pending") if t.run_id == run_id)
    chk.submit_hitl_decision(
        wait_task.hitl_task_id,
        "approved",
        {"_staff_action_complete": {"reinspection_result": "PASSED"}},
    )

    # Process B completes graph
    final_result = runner_b.run()
    assert final_result["status"] == "COMPLETED"
    assert final_result["inspection_completed"] is True
