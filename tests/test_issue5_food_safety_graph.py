"""tests/test_issue5_food_safety_graph.py

Comprehensive test suite for the Food Safety State Graph (Issue #5).

Tests cover:
  1. No violation path (all standards met -> completes immediately)
  2. High-severity violation path (triggers HITL admin review pause, resumes on approval)
  3. Constrained ReAct tool enforcement (unallowed tool raises ToolNotAllowedError)
  4. Graph Cycle & Re-inspection retry loop (re-inspection FAILED loops back to determine_corrective_action)
  5. Successful re-inspection (re-inspection PASSED transitions to complete_inspection)
  6. Failure Ticket & Recovery (unexpected exception creates open ticket, resumes on resolution)
  7. Checkpoint persistence and version tracking in SQLite
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

import planning_lab.checkpointing as chk
from planning_lab.food_safety_graph import (
    ALLOWED_CORRECTIVE_TOOLS,
    ToolNotAllowedError,
    create_food_safety_graph,
    run_constrained_react_engine,
)
from planning_lab.state_graph import (
    RunFailedException,
    RunPausedException,
    StateGraphRunner,
)

MIGRATE_SQL_PATH = (
    Path(__file__).resolve().parent.parent / "db" / "migrate_checkpoint.sql"
)


@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    """Fixture: patches planning_lab.checkpointing.DB_PATH to a fresh temp DB."""
    db_path = tmp_path / "food_safety_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(MIGRATE_SQL_PATH.read_text())
    conn.commit()
    conn.close()

    monkeypatch.setattr(chk, "DB_PATH", db_path)
    return db_path


class TestConstrainedReActEngine:
    def test_allowed_tools_pass(self):
        violation = {"hazard_type": "COLD_STORAGE_TEMPERATURE_EXCEEDED", "branch_id": 1}
        proposed = [
            {"tool_name": "log_waste_report", "args": {"reason": "Temp violation"}},
            {"tool_name": "schedule_reinspection", "args": {"days_hence": 1}},
        ]
        res = run_constrained_react_engine(violation, proposed)
        assert len(res["executed_actions"]) == 2
        assert res["executed_actions"][0]["tool_name"] == "log_waste_report"

    def test_disallowed_tool_raises_error(self):
        violation = {"hazard_type": "COLD_STORAGE_TEMPERATURE_EXCEEDED", "branch_id": 1}
        proposed = [
            {"tool_name": "delete_database_records", "args": {}},  # Illegal tool
        ]
        with pytest.raises(ToolNotAllowedError) as exc_info:
            run_constrained_react_engine(violation, proposed)
        assert "is not in the allowed toolset" in str(exc_info.value)


class TestFoodSafetyGraphExecution:
    def test_no_violation_clean_completion_path(self, test_db):
        """Inspection passes all criteria -> directly completes without HITL or corrective actions."""
        run_id = str(uuid.uuid4())
        graph = create_food_safety_graph()
        runner = StateGraphRunner(graph, run_id)

        result = runner.run(
            initial_state="receive_inspection_request",
            initial_data={
                "branch_id": 1,
                "fridge_temp_celsius": 3.0,  # Below 4.0 threshold
                "sanitation_score": 95,      # Above 80 threshold
                "expired_items_found": False,
            },
        )
        assert result["status"] == "COMPLETED"
        assert result["compliance_result"] == "PASSED"
        assert result["inspection_completed"] is True

    def test_high_severity_violation_triggers_hitl_admin_review(self, test_db):
        """High fridge temp (> 4°C) -> triggers HITL admin review pause, resumes on approval."""
        run_id = str(uuid.uuid4())
        graph = create_food_safety_graph()
        runner = StateGraphRunner(graph, run_id)

        # High temp (8.5°C) -> triggers HITL pause at admin_review
        with pytest.raises(RunPausedException):
            runner.run(
                initial_state="receive_inspection_request",
                initial_data={
                    "branch_id": 1,
                    "fridge_temp_celsius": 8.5,
                    "sanitation_score": 85,
                },
            )

        pending_tasks = chk.get_hitl_tasks(status="pending")
        review_task = next(t for t in pending_tasks if t.run_id == run_id)
        assert "High-risk food safety violation" in review_task.reason

        # Admin approves corrective plan
        chk.submit_hitl_decision(review_task.hitl_task_id, "approved", {"reviewed_by": "Safety Officer"})

        # Resume -> advances to wait_for_corrective_action pause
        with pytest.raises(RunPausedException):
            runner.run()

        wait_task = next(t for t in chk.get_hitl_tasks(status="pending") if t.run_id == run_id)
        assert wait_task.reason == "WAITING_FOR_STAFF_CORRECTIVE_ACTION"

        # Staff completes action & reinspection passes
        chk.submit_hitl_decision(
            wait_task.hitl_task_id,
            "approved",
            {"_staff_action_complete": {"reinspection_result": "PASSED"}},
        )

        final_result = runner.run()
        assert final_result["status"] == "COMPLETED"
        assert final_result["inspection_completed"] is True

    def test_graph_cycle_reinspection_failure_retry_loop(self, test_db):
        """Re-inspection FAILED -> graph CYCLES back to determine_corrective_action for round 2."""
        run_id = str(uuid.uuid4())
        graph = create_food_safety_graph()
        runner = StateGraphRunner(graph, run_id)

        # Initial run with temp violation
        with pytest.raises(RunPausedException):
            runner.run(
                initial_state="receive_inspection_request",
                initial_data={
                    "branch_id": 1,
                    "fridge_temp_celsius": 9.0,
                },
            )

        review_task = next(t for t in chk.get_hitl_tasks(status="pending") if t.run_id == run_id)
        chk.submit_hitl_decision(review_task.hitl_task_id, "approved")

        with pytest.raises(RunPausedException):
            runner.run()

        wait_task1 = next(t for t in chk.get_hitl_tasks(status="pending") if t.run_id == run_id)

        # First staff reinspection FAILS
        chk.submit_hitl_decision(
            wait_task1.hitl_task_id,
            "approved",
            {"_staff_action_complete": {"reinspection_result": "FAILED"}},
        )

        # Resume -> graph CYCLES back to determine_corrective_action, admin_review pause
        with pytest.raises(RunPausedException):
            runner.run()

        review_task2 = next(t for t in chk.get_hitl_tasks(status="pending") if t.run_id == run_id)
        assert review_task2.hitl_task_id != review_task.hitl_task_id

        chk.submit_hitl_decision(review_task2.hitl_task_id, "approved")

        with pytest.raises(RunPausedException):
            runner.run()

        wait_task2 = next(t for t in chk.get_hitl_tasks(status="pending") if t.run_id == run_id)

        # Second staff reinspection PASSES
        chk.submit_hitl_decision(
            wait_task2.hitl_task_id,
            "approved",
            {"_staff_action_complete": {"reinspection_result": "PASSED"}},
        )

        cycle_final = runner.run()
        assert cycle_final["status"] == "COMPLETED"
        assert cycle_final["reinspection_status"] == "PASSED"

    def test_unexpected_failure_creates_ticket_and_resumes(self, test_db):
        """Unexpected node exception creates failure ticket; resumes after resolution."""
        run_id = str(uuid.uuid4())

        def faulty_node(state):
            if not state.get("fixed"):
                raise RuntimeError("Sensor network offline")
            return "gather_information", {**state}

        graph = create_food_safety_graph()
        graph.nodes["receive_inspection_request"] = faulty_node

        runner = StateGraphRunner(graph, run_id)

        with pytest.raises(RunFailedException):
            runner.run(initial_state="receive_inspection_request", initial_data={})

        tickets = chk.get_failure_tickets(status="open")
        run_ticket = next(t for t in tickets if t.run_id == run_id)
        assert run_ticket.error_type == "RuntimeError"

        # Resolve ticket and fix node state
        chk.resolve_failure_ticket(run_ticket.failure_ticket_id, "Sensors rebooted")

        def fixed_node(state):
            return "gather_information", {**state, "fixed": True, "fridge_temp_celsius": 3.0}

        graph.nodes["receive_inspection_request"] = fixed_node

        resumed_result = runner.run()
        assert resumed_result["status"] == "COMPLETED"

    def test_sqlite_checkpoint_persistence_and_versioning(self, test_db):
        """Verify state checkpoints persist in SQLite with incremental versions."""
        run_id = str(uuid.uuid4())
        graph = create_food_safety_graph()
        runner = StateGraphRunner(graph, run_id)

        runner.run(
            initial_state="receive_inspection_request",
            initial_data={"branch_id": 1, "fridge_temp_celsius": 3.0},
        )

        checkpoints = chk.get_run_checkpoints(run_id)
        assert len(checkpoints) >= 5
        versions = [c.checkpoint_version for c in checkpoints]
        assert versions == list(range(1, len(checkpoints) + 1))
