"""tests/test_issue3_procurement_graph.py

Comprehensive test suite for the Procurement State Graph (Issue #3).

Tests cover:
  1. Normal successful path (small order, auto-approved, accepted by supplier)
  2. HITL Approval path (large order > $1,000 threshold, pauses for review, resumes on approval)
  3. HITL Rejection path (large order rejected by admin)
  4. External Waiting State (WAITING_FOR_SUPPLIER pauses cleanly, resumes on event payload)
  5. Supplier Rejection Branch (supplier rejects, graph loops back to evaluate alternative supplier)
  6. Failure Ticket & Recovery (unexpected node exception generates open ticket, resumes on resolution)
  7. Checkpoint persistence and version tracking in SQLite
"""
from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

import planning_lab.checkpointing as chk
from planning_lab.procurement_graph import (
    create_procurement_graph,
    decompose_procurement_request,
    retrieve_supplier_policy,
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
    db_path = tmp_path / "procurement_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(MIGRATE_SQL_PATH.read_text())
    conn.commit()
    conn.close()

    monkeypatch.setattr(chk, "DB_PATH", db_path)
    return db_path


class TestProcurementHelpers:
    def test_task_decomposition_parsing(self):
        result = decompose_procurement_request("Need 100 kg Roma Tomatoes and 50 kg Mozzarella Cheese for Branch 1")
        assert result["item_count"] == 2
        items = result["decomposed_items"]
        assert any(i["item_name"] == "Roma Tomatoes" for i in items)
        assert any(i["item_name"] == "Mozzarella Cheese" for i in items)

    def test_rag_supplier_policy_retrieval(self):
        info = retrieve_supplier_policy("Preferred vendor list for produce")
        assert "preferred_suppliers" in info
        assert info["approval_threshold"] == 1000.0


class TestProcurementGraphExecution:
    def test_normal_small_order_successful_path(self, test_db):
        """Small order (< $1000): Auto-approved, pauses at supplier wait, supplier accepts -> completed."""
        run_id = str(uuid.uuid4())
        graph = create_procurement_graph()
        runner = StateGraphRunner(graph, run_id)

        # Initial run: advances up to external wait state WAITING_FOR_SUPPLIER
        with pytest.raises(RunPausedException):
            runner.run(
                initial_state="receive_request",
                initial_data={"raw_request": "Need 10 kg flour", "branch_id": 1},
            )

        # Verify HITL / wait task created
        pending_tasks = chk.get_hitl_tasks(status="pending")
        wait_task = next(t for t in pending_tasks if t.run_id == run_id)
        assert wait_task.reason == "WAITING_FOR_SUPPLIER"

        # Submit external supplier response: ACCEPTED
        chk.submit_hitl_decision(
            wait_task.hitl_task_id,
            "approved",
            {"_supplier_response": {"decision": "ACCEPTED"}},
        )

        # Resume execution to completion
        final_result = runner.run()
        assert final_result["status"] == "COMPLETED"
        assert final_result["order_received"] is True
        assert final_result["approval_status"] == "AUTO_APPROVED"
        assert "po_number" in final_result

    def test_hitl_approval_flow_large_order(self, test_db):
        """Large order (> $1000): Triggers HITL pause, resumes after admin approval, then pauses at supplier wait."""
        run_id = str(uuid.uuid4())
        graph = create_procurement_graph()
        runner = StateGraphRunner(graph, run_id)

        # Step 1: Large order -> raises RunPausedException for budget threshold
        with pytest.raises(RunPausedException):
            runner.run(
                initial_state="receive_request",
                initial_data={"raw_request": "Need 500 kg Mozzarella Cheese", "branch_id": 1},
            )

        # Verify manager approval HITL task created
        pending_tasks = chk.get_hitl_tasks(status="pending")
        run_task = next((t for t in pending_tasks if t.run_id == run_id), None)
        assert run_task is not None
        assert "exceeds policy threshold" in run_task.reason

        # Step 2: Approve task by manager
        chk.submit_hitl_decision(run_task.hitl_task_id, "approved", {"approved_by": "Mona Manager"})

        # Step 3: Resume -> advances to WAITING_FOR_SUPPLIER pause
        with pytest.raises(RunPausedException):
            runner.run()

        # Get the new supplier wait task
        pending_tasks_2 = chk.get_hitl_tasks(status="pending")
        wait_task = next(t for t in pending_tasks_2 if t.run_id == run_id)
        assert wait_task.reason == "WAITING_FOR_SUPPLIER"

        # Step 4: Submit supplier response ACCEPTED
        chk.submit_hitl_decision(
            wait_task.hitl_task_id,
            "approved",
            {"_supplier_response": {"decision": "ACCEPTED"}},
        )

        final_result = runner.run()
        assert final_result["approval_status"] == "APPROVED_BY_ADMIN"
        assert final_result["status"] == "COMPLETED"

    def test_hitl_rejection_flow_large_order(self, test_db):
        """Large order rejected by admin -> status set to REJECTED."""
        run_id = str(uuid.uuid4())
        graph = create_procurement_graph()
        runner = StateGraphRunner(graph, run_id)

        with pytest.raises(RunPausedException):
            runner.run(
                initial_state="receive_request",
                initial_data={"raw_request": "Need 1000 kg Mozzarella Cheese", "branch_id": 1},
            )

        pending_tasks = chk.get_hitl_tasks(status="pending")
        run_task = next(t for t in pending_tasks if t.run_id == run_id)
        chk.submit_hitl_decision(run_task.hitl_task_id, "rejected", {"reason": "Over budget"})

        final_result = runner.run()
        assert final_result["status"] == "REJECTED"
        assert final_result["approval_status"] == "REJECTED_BY_ADMIN"

    def test_supplier_rejection_branch_loop(self, test_db):
        """Supplier rejects order -> graph loops back to evaluate alternative supplier."""
        run_id = str(uuid.uuid4())
        graph = create_procurement_graph()
        runner = StateGraphRunner(graph, run_id)

        # Initial run to supplier wait state
        with pytest.raises(RunPausedException):
            runner.run(
                initial_state="receive_request",
                initial_data={"raw_request": "Need 10 kg flour", "branch_id": 1},
            )

        wait_task1 = next(t for t in chk.get_hitl_tasks(status="pending") if t.run_id == run_id)
        first_supplier = wait_task1.context.get("selected_supplier") if isinstance(wait_task1.context, dict) else "APX-9982"

        # Submit supplier response: REJECTED
        chk.submit_hitl_decision(
            wait_task1.hitl_task_id,
            "approved",
            {"_supplier_response": {"decision": "REJECTED"}},
        )

        # Resume -> loops back to evaluate_supplier and pauses at next supplier wait state
        with pytest.raises(RunPausedException):
            runner.run()

        wait_task2 = next(t for t in chk.get_hitl_tasks(status="pending") if t.run_id == run_id)
        new_supplier = wait_task2.context.get("selected_supplier") if isinstance(wait_task2.context, dict) else "GRW-4477"
        assert new_supplier != first_supplier

    def test_unexpected_failure_creates_ticket_and_resumes(self, test_db):
        """Unexpected exception in node creates failure ticket; resumes after resolution."""
        run_id = str(uuid.uuid4())

        def faulty_node(state):
            if not state.get("fixed"):
                raise ValueError("Database connection timeout")
            return "check_inventory", {**state, "raw_request": "Need flour"}

        graph = create_procurement_graph()
        graph.nodes["receive_request"] = faulty_node  # Inject failure

        runner = StateGraphRunner(graph, run_id)

        # Should fail and create open failure ticket
        with pytest.raises(RunFailedException):
            runner.run(initial_state="receive_request", initial_data={})

        tickets = chk.get_failure_tickets(status="open")
        run_ticket = next(t for t in tickets if t.run_id == run_id)
        assert run_ticket.error_type == "ValueError"
        assert "connection timeout" in run_ticket.error_message

        # Resolve ticket and fix node state
        chk.resolve_failure_ticket(run_ticket.failure_ticket_id, "DB connectivity restored")

        # Update node function to succeed
        def fixed_node(state):
            return "check_inventory", {**state, "raw_request": "Need 10 kg flour", "fixed": True}

        graph.nodes["receive_request"] = fixed_node

        # Resume -> continues cleanly up to supplier wait pause
        with pytest.raises(RunPausedException):
            runner.run()

        wait_task = next(t for t in chk.get_hitl_tasks(status="pending") if t.run_id == run_id)
        assert wait_task.reason == "WAITING_FOR_SUPPLIER"

    def test_sqlite_checkpoint_persistence_and_versioning(self, test_db):
        """Verify state checkpoints persist in SQLite with incremental versions."""
        run_id = str(uuid.uuid4())
        graph = create_procurement_graph()
        runner = StateGraphRunner(graph, run_id)

        with pytest.raises(RunPausedException):
            runner.run(
                initial_state="receive_request",
                initial_data={"raw_request": "Need flour", "branch_id": 1},
            )

        checkpoints = chk.get_run_checkpoints(run_id)
        assert len(checkpoints) >= 4
        versions = [c.checkpoint_version for c in checkpoints]
        assert versions == list(range(1, len(checkpoints) + 1))
