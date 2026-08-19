"""tests/test_checkpoint_hitl_recovery.py

Comprehensive test suite for the checkpointing, HITL, failure ticket, and
StateGraph runner infrastructure (Issue #2).

Tests are organised into five areas:
  1. Checkpoint CRUD — create, read, versioning
  2. HITL Task lifecycle — create, submit decisions, list
  3. Failure Ticket lifecycle — create, resolve, list
  4. StateGraph Runner — happy-path execution, idempotency, crash resume
  5. HITL and failure recovery — pause/resume via state runner
"""
from __future__ import annotations

import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple
from unittest.mock import patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Test database setup — each test gets a fresh in-memory DB via monkeypatching
# ─────────────────────────────────────────────────────────────────────────────

MIGRATE_SQL_PATH = (
    Path(__file__).resolve().parent.parent / "db" / "migrate_checkpoint.sql"
)


def _make_test_db() -> Path:
    """Create a temporary SQLite database with the checkpoint schema applied."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(MIGRATE_SQL_PATH.read_text())
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    """Fixture: patches planning_lab.checkpointing.DB_PATH to a temp DB."""
    import planning_lab.checkpointing as chk_module

    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(MIGRATE_SQL_PATH.read_text())
    conn.commit()
    conn.close()

    monkeypatch.setattr(chk_module, "DB_PATH", db_path)
    return db_path


# Shorthand import for checkpointing functions (after monkeypatch)
import planning_lab.checkpointing as chk


# ─────────────────────────────────────────────────────────────────────────────
# 1. Checkpoint CRUD Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckpointCRUD:
    def test_create_checkpoint_basic(self, test_db):
        run_id = str(uuid.uuid4())
        cp = chk.create_checkpoint(
            run_id=run_id,
            graph_id="graph-a",
            state_name="step_one",
            state_data={"foo": "bar"},
            completed_steps=[],
        )
        assert cp.run_id == run_id
        assert cp.graph_id == "graph-a"
        assert cp.state_name == "step_one"
        assert cp.state_data == {"foo": "bar"}
        assert cp.checkpoint_version == 1
        assert cp.status == "active"

    def test_create_checkpoint_increments_version(self, test_db):
        run_id = str(uuid.uuid4())
        cp1 = chk.create_checkpoint(
            run_id=run_id,
            graph_id="g",
            state_name="s1",
            state_data={},
            completed_steps=[],
        )
        cp2 = chk.create_checkpoint(
            run_id=run_id,
            graph_id="g",
            state_name="s2",
            state_data={},
            completed_steps=["s1"],
        )
        assert cp1.checkpoint_version == 1
        assert cp2.checkpoint_version == 2

    def test_get_checkpoint_by_id(self, test_db):
        run_id = str(uuid.uuid4())
        created = chk.create_checkpoint(
            run_id=run_id,
            graph_id="g",
            state_name="s1",
            state_data={"key": 42},
            completed_steps=["prev"],
        )
        fetched = chk.get_checkpoint(created.checkpoint_id)
        assert fetched is not None
        assert fetched.checkpoint_id == created.checkpoint_id
        assert fetched.state_data == {"key": 42}
        assert fetched.completed_steps == ["prev"]

    def test_get_latest_checkpoint(self, test_db):
        run_id = str(uuid.uuid4())
        chk.create_checkpoint(run_id=run_id, graph_id="g", state_name="s1", state_data={}, completed_steps=[])
        chk.create_checkpoint(run_id=run_id, graph_id="g", state_name="s2", state_data={}, completed_steps=["s1"])
        latest = chk.get_latest_checkpoint(run_id)
        assert latest is not None
        assert latest.state_name == "s2"
        assert latest.checkpoint_version == 2

    def test_get_checkpoint_nonexistent(self, test_db):
        result = chk.get_checkpoint(str(uuid.uuid4()))
        assert result is None

    def test_get_latest_checkpoint_no_run(self, test_db):
        result = chk.get_latest_checkpoint("nonexistent-run")
        assert result is None

    def test_get_run_checkpoints_ordered(self, test_db):
        run_id = str(uuid.uuid4())
        for step in ["a", "b", "c"]:
            chk.create_checkpoint(run_id=run_id, graph_id="g", state_name=step, state_data={}, completed_steps=[])
        all_chks = chk.get_run_checkpoints(run_id)
        versions = [c.checkpoint_version for c in all_chks]
        assert versions == sorted(versions)

    def test_checkpoint_state_data_round_trips_json(self, test_db):
        run_id = str(uuid.uuid4())
        complex_data = {"list": [1, 2, 3], "nested": {"a": True}}
        cp = chk.create_checkpoint(
            run_id=run_id, graph_id="g", state_name="s", state_data=complex_data, completed_steps=[]
        )
        fetched = chk.get_checkpoint(cp.checkpoint_id)
        assert fetched.state_data == complex_data

    def test_checkpoint_status_field(self, test_db):
        run_id = str(uuid.uuid4())
        cp = chk.create_checkpoint(
            run_id=run_id, graph_id="g", state_name="s", state_data={}, completed_steps=[], status="completed"
        )
        fetched = chk.get_checkpoint(cp.checkpoint_id)
        assert fetched.status == "completed"

    def test_different_runs_have_independent_versions(self, test_db):
        run_a = str(uuid.uuid4())
        run_b = str(uuid.uuid4())
        chk.create_checkpoint(run_id=run_a, graph_id="g", state_name="s1", state_data={}, completed_steps=[])
        chk.create_checkpoint(run_id=run_a, graph_id="g", state_name="s2", state_data={}, completed_steps=[])
        chk.create_checkpoint(run_id=run_b, graph_id="g", state_name="s1", state_data={}, completed_steps=[])

        a_latest = chk.get_latest_checkpoint(run_a)
        b_latest = chk.get_latest_checkpoint(run_b)
        assert a_latest.checkpoint_version == 2
        assert b_latest.checkpoint_version == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. HITL Task Lifecycle Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestHITLTaskLifecycle:
    def _make_checkpoint(self, run_id: str) -> chk.Checkpoint:
        return chk.create_checkpoint(
            run_id=run_id, graph_id="g", state_name="review", state_data={}, completed_steps=[]
        )

    def test_create_hitl_task(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        task = chk.create_hitl_task(
            run_id=run_id,
            graph_id="g",
            checkpoint_id=cp.checkpoint_id,
            state_name="review",
            reason="Need approval",
            context={"amount": 1000},
        )
        assert task.hitl_task_id is not None
        assert task.status == "pending"
        assert task.run_id == run_id
        assert task.context == {"amount": 1000}

    def test_create_hitl_task_updates_checkpoint_status(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        chk.create_hitl_task(
            run_id=run_id, graph_id="g", checkpoint_id=cp.checkpoint_id,
            state_name="review", reason="sign-off", context={}
        )
        updated_cp = chk.get_checkpoint(cp.checkpoint_id)
        assert updated_cp.status == "paused_hitl"

    def test_submit_hitl_decision_approved(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        task = chk.create_hitl_task(
            run_id=run_id, graph_id="g", checkpoint_id=cp.checkpoint_id,
            state_name="review", reason="r", context={}
        )
        resolved = chk.submit_hitl_decision(task.hitl_task_id, "approved", {"note": "looks good"})
        assert resolved.status == "approved"
        assert resolved.decision == "approved"
        assert resolved.decision_data == {"note": "looks good"}
        assert resolved.resolved_at is not None

    def test_submit_hitl_decision_rejected(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        task = chk.create_hitl_task(
            run_id=run_id, graph_id="g", checkpoint_id=cp.checkpoint_id,
            state_name="review", reason="r", context={}
        )
        resolved = chk.submit_hitl_decision(task.hitl_task_id, "rejected")
        assert resolved.status == "rejected"

    def test_submit_hitl_decision_invalid(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        task = chk.create_hitl_task(
            run_id=run_id, graph_id="g", checkpoint_id=cp.checkpoint_id,
            state_name="review", reason="r", context={}
        )
        with pytest.raises(ValueError, match="Invalid decision"):
            chk.submit_hitl_decision(task.hitl_task_id, "maybe")

    def test_submit_hitl_decision_on_resolved_task_raises(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        task = chk.create_hitl_task(
            run_id=run_id, graph_id="g", checkpoint_id=cp.checkpoint_id,
            state_name="review", reason="r", context={}
        )
        chk.submit_hitl_decision(task.hitl_task_id, "approved")
        with pytest.raises(ValueError, match="already resolved"):
            chk.submit_hitl_decision(task.hitl_task_id, "rejected")

    def test_get_hitl_task_by_id(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        task = chk.create_hitl_task(
            run_id=run_id, graph_id="g", checkpoint_id=cp.checkpoint_id,
            state_name="review", reason="r", context={"x": 1}
        )
        fetched = chk.get_hitl_task(task.hitl_task_id)
        assert fetched is not None
        assert fetched.context == {"x": 1}

    def test_get_hitl_task_nonexistent(self, test_db):
        result = chk.get_hitl_task(str(uuid.uuid4()))
        assert result is None

    def test_list_hitl_tasks_filter_by_status(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        task1 = chk.create_hitl_task(
            run_id=run_id, graph_id="g", checkpoint_id=cp.checkpoint_id,
            state_name="s", reason="r", context={}
        )
        cp2 = chk.create_checkpoint(run_id=run_id, graph_id="g", state_name="s2", state_data={}, completed_steps=[])
        task2 = chk.create_hitl_task(
            run_id=run_id, graph_id="g", checkpoint_id=cp2.checkpoint_id,
            state_name="s2", reason="r", context={}
        )
        chk.submit_hitl_decision(task1.hitl_task_id, "approved")

        pending = chk.get_hitl_tasks(status="pending")
        approved = chk.get_hitl_tasks(status="approved")
        assert all(t.status == "pending" for t in pending)
        assert all(t.status == "approved" for t in approved)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Failure Ticket Lifecycle Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestFailureTicketLifecycle:
    def _make_checkpoint(self, run_id: str) -> chk.Checkpoint:
        return chk.create_checkpoint(
            run_id=run_id, graph_id="g", state_name="processing", state_data={}, completed_steps=[]
        )

    def test_create_failure_ticket(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        ticket = chk.create_failure_ticket(
            run_id=run_id,
            graph_id="g",
            checkpoint_id=cp.checkpoint_id,
            failed_node="processing",
            error_type="ValueError",
            error_message="something broke",
            error_details="Traceback...",
            state_snapshot={"data": 1},
        )
        assert ticket.failure_ticket_id is not None
        assert ticket.status == "open"
        assert ticket.error_type == "ValueError"
        assert ticket.state_snapshot == {"data": 1}

    def test_create_failure_ticket_updates_checkpoint_status(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        chk.create_failure_ticket(
            run_id=run_id, graph_id="g", checkpoint_id=cp.checkpoint_id,
            failed_node="processing", error_type="E", error_message="msg",
            error_details=None, state_snapshot={}
        )
        updated_cp = chk.get_checkpoint(cp.checkpoint_id)
        assert updated_cp.status == "failed"

    def test_resolve_failure_ticket(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        ticket = chk.create_failure_ticket(
            run_id=run_id, graph_id="g", checkpoint_id=cp.checkpoint_id,
            failed_node="processing", error_type="E", error_message="msg",
            error_details=None, state_snapshot={}
        )
        resolved = chk.resolve_failure_ticket(ticket.failure_ticket_id, "Fixed by admin")
        assert resolved.status == "resolved"
        assert resolved.resolution == "Fixed by admin"
        assert resolved.resolved_at is not None

    def test_resolve_already_resolved_ticket_raises(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        ticket = chk.create_failure_ticket(
            run_id=run_id, graph_id="g", checkpoint_id=cp.checkpoint_id,
            failed_node="n", error_type="E", error_message="m",
            error_details=None, state_snapshot={}
        )
        chk.resolve_failure_ticket(ticket.failure_ticket_id, "ok")
        with pytest.raises(ValueError, match="already resolved"):
            chk.resolve_failure_ticket(ticket.failure_ticket_id, "again")

    def test_resolve_nonexistent_ticket_raises(self, test_db):
        with pytest.raises(KeyError):
            chk.resolve_failure_ticket(str(uuid.uuid4()), "fix")

    def test_get_failure_ticket_by_id(self, test_db):
        run_id = str(uuid.uuid4())
        cp = self._make_checkpoint(run_id)
        ticket = chk.create_failure_ticket(
            run_id=run_id, graph_id="g", checkpoint_id=cp.checkpoint_id,
            failed_node="n", error_type="E", error_message="m",
            error_details=None, state_snapshot={"k": "v"}
        )
        fetched = chk.get_failure_ticket(ticket.failure_ticket_id)
        assert fetched is not None
        assert fetched.state_snapshot == {"k": "v"}

    def test_get_failure_ticket_nonexistent(self, test_db):
        result = chk.get_failure_ticket(str(uuid.uuid4()))
        assert result is None

    def test_list_failure_tickets_filter_by_status(self, test_db):
        run_id = str(uuid.uuid4())
        cp1 = chk.create_checkpoint(run_id=run_id, graph_id="g", state_name="n1", state_data={}, completed_steps=[])
        cp2 = chk.create_checkpoint(run_id=run_id, graph_id="g", state_name="n2", state_data={}, completed_steps=[])
        t1 = chk.create_failure_ticket(
            run_id=run_id, graph_id="g", checkpoint_id=cp1.checkpoint_id,
            failed_node="n1", error_type="E", error_message="m", error_details=None, state_snapshot={}
        )
        chk.create_failure_ticket(
            run_id=run_id, graph_id="g", checkpoint_id=cp2.checkpoint_id,
            failed_node="n2", error_type="E", error_message="m", error_details=None, state_snapshot={}
        )
        chk.resolve_failure_ticket(t1.failure_ticket_id, "fixed")

        open_tickets = chk.get_failure_tickets(status="open")
        resolved_tickets = chk.get_failure_tickets(status="resolved")
        assert all(t.status == "open" for t in open_tickets)
        assert all(t.status == "resolved" for t in resolved_tickets)


# ─────────────────────────────────────────────────────────────────────────────
# 4. StateGraph Runner Tests — happy path, idempotency, crash recovery
# ─────────────────────────────────────────────────────────────────────────────


from planning_lab.state_graph import (
    StateGraph,
    StateGraphRunner,
    HITLRequestException,
    RunPausedException,
    RunFailedException,
)


def _build_simple_graph() -> StateGraph:
    """Build a simple 3-node graph: step_a -> step_b -> done."""
    g = StateGraph("simple-graph")

    def step_a(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        state = {**state, "visited_a": True}
        return "step_b", state

    def step_b(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        state = {**state, "visited_b": True}
        return "done", state

    g.add_node("step_a", step_a)
    g.add_node("step_b", step_b)
    return g


class TestStateGraphHappyPath:
    def test_complete_run_returns_final_state(self, test_db):
        run_id = str(uuid.uuid4())
        graph = _build_simple_graph()
        runner = StateGraphRunner(graph, run_id)
        result = runner.run(initial_state="step_a", initial_data={"input": 1})
        assert result["visited_a"] is True
        assert result["visited_b"] is True

    def test_complete_run_creates_checkpoints(self, test_db):
        run_id = str(uuid.uuid4())
        graph = _build_simple_graph()
        runner = StateGraphRunner(graph, run_id)
        runner.run(initial_state="step_a", initial_data={})
        checkpoints = chk.get_run_checkpoints(run_id)
        # At minimum: initial + after step_a + after step_b (done)
        assert len(checkpoints) >= 3

    def test_final_checkpoint_status_is_completed(self, test_db):
        run_id = str(uuid.uuid4())
        graph = _build_simple_graph()
        runner = StateGraphRunner(graph, run_id)
        runner.run(initial_state="step_a", initial_data={})
        latest = chk.get_latest_checkpoint(run_id)
        assert latest.status == "completed"

    def test_completed_run_returns_immediately_on_resume(self, test_db):
        run_id = str(uuid.uuid4())
        graph = _build_simple_graph()
        runner = StateGraphRunner(graph, run_id)
        result1 = runner.run(initial_state="step_a", initial_data={"x": 5})
        # Second call — should read 'completed' checkpoint and return early
        result2 = runner.run()
        assert result2 == result1

    def test_no_initial_state_no_checkpoint_raises(self, test_db):
        run_id = str(uuid.uuid4())
        graph = _build_simple_graph()
        runner = StateGraphRunner(graph, run_id)
        with pytest.raises(ValueError, match="no initial_state"):
            runner.run()

    def test_unknown_node_raises_value_error(self, test_db):
        run_id = str(uuid.uuid4())
        graph = StateGraph("g")
        # node returns unknown next state
        graph.add_node("start", lambda s: ("nonexistent_node", s))
        runner = StateGraphRunner(graph, run_id)
        with pytest.raises(RunFailedException):
            runner.run(initial_state="start", initial_data={})


class TestStateGraphIdempotency:
    def test_completed_steps_are_skipped_on_resume(self, test_db):
        """Inject a checkpoint where step_a is already completed, verify it isn't re-executed."""
        run_id = str(uuid.uuid4())
        call_count = {"step_a": 0, "step_b": 0}

        g = StateGraph("idempotency-graph")

        def step_a(state):
            call_count["step_a"] += 1
            state = {**state, "_transitions": {"step_a": "step_b"}, "from_a": True}
            return "step_b", state

        def step_b(state):
            call_count["step_b"] += 1
            state = {**state, "from_b": True}
            return "done", state

        g.add_node("step_a", step_a)
        g.add_node("step_b", step_b)

        # Simulate a checkpoint where step_a was already completed
        chk.create_checkpoint(
            run_id=run_id,
            graph_id="idempotency-graph",
            state_name="step_b",
            state_data={"_transitions": {"step_a": "step_b"}, "from_a": True},
            completed_steps=["step_a"],
            status="active",
        )

        runner = StateGraphRunner(g, run_id)
        result = runner.run()  # Resume from checkpoint, no initial_state needed
        # step_a should NOT have been called (it's in completed_steps)
        assert call_count["step_a"] == 0
        # step_b should have been called
        assert call_count["step_b"] == 1
        assert result.get("from_b") is True


# ─────────────────────────────────────────────────────────────────────────────
# 5. HITL and Failure Recovery via StateGraphRunner
# ─────────────────────────────────────────────────────────────────────────────


class TestHITLRecovery:
    def test_hitl_request_exception_pauses_run(self, test_db):
        run_id = str(uuid.uuid4())
        g = StateGraph("hitl-graph")

        def step_requires_review(state):
            raise HITLRequestException(
                reason="Manager approval required for large order",
                context={"amount": 5000},
            )

        g.add_node("review", step_requires_review)
        runner = StateGraphRunner(g, run_id)

        with pytest.raises(RunPausedException):
            runner.run(initial_state="review", initial_data={})

    def test_hitl_pause_creates_hitl_task(self, test_db):
        run_id = str(uuid.uuid4())
        g = StateGraph("hitl-graph")
        g.add_node("review", lambda s: (_ for _ in ()).throw(
            HITLRequestException("Need approval", {"x": 1})
        ))

        runner = StateGraphRunner(g, run_id)
        with pytest.raises(RunPausedException):
            runner.run(initial_state="review", initial_data={})

        tasks = chk.get_hitl_tasks(status="pending")
        assert any(t.run_id == run_id for t in tasks)

    def test_hitl_pause_sets_checkpoint_status_paused_hitl(self, test_db):
        run_id = str(uuid.uuid4())
        g = StateGraph("hitl-graph")
        g.add_node("review", lambda s: (_ for _ in ()).throw(
            HITLRequestException("Need approval", {})
        ))
        runner = StateGraphRunner(g, run_id)
        with pytest.raises(RunPausedException):
            runner.run(initial_state="review", initial_data={})

        latest = chk.get_latest_checkpoint(run_id)
        assert latest.status == "paused_hitl"

    def test_resume_while_pending_hitl_raises(self, test_db):
        run_id = str(uuid.uuid4())
        g = StateGraph("hitl-graph")
        g.add_node("review", lambda s: (_ for _ in ()).throw(
            HITLRequestException("Need approval", {})
        ))
        runner = StateGraphRunner(g, run_id)
        with pytest.raises(RunPausedException):
            runner.run(initial_state="review", initial_data={})

        # Try to resume before approval — should raise again
        with pytest.raises(RunPausedException):
            runner.run()

    def test_resume_after_approved_hitl(self, test_db):
        """After HITL approval, resuming the runner should continue execution."""
        run_id = str(uuid.uuid4())
        hitl_fired = {"count": 0}

        g = StateGraph("hitl-resume-graph")

        def gated_step(state):
            # On first call, raise HITL; on resume the _hitl_decision will be present
            if "_hitl_decision" not in state:
                hitl_fired["count"] += 1
                raise HITLRequestException("Approval needed", {"order_id": 99})
            # If decision is approved, proceed
            if state["_hitl_decision"]["status"] == "approved":
                return "done", {**state, "approved": True}
            return "done", {**state, "approved": False}

        g.add_node("gated_step", gated_step)
        runner = StateGraphRunner(g, run_id)

        # First attempt pauses
        with pytest.raises(RunPausedException):
            runner.run(initial_state="gated_step", initial_data={})

        # Approve the HITL task
        tasks = chk.get_hitl_tasks(status="pending")
        run_tasks = [t for t in tasks if t.run_id == run_id]
        assert len(run_tasks) == 1
        chk.submit_hitl_decision(run_tasks[0].hitl_task_id, "approved")

        # Resume
        result = runner.run()
        assert result.get("approved") is True


class TestFailureRecovery:
    def test_unexpected_exception_creates_failure_ticket(self, test_db):
        run_id = str(uuid.uuid4())
        g = StateGraph("failure-graph")

        def broken_step(state):
            raise RuntimeError("Disk full!")

        g.add_node("broken_step", broken_step)
        runner = StateGraphRunner(g, run_id)

        with pytest.raises(RunFailedException):
            runner.run(initial_state="broken_step", initial_data={})

        tickets = chk.get_failure_tickets(status="open")
        assert any(t.run_id == run_id for t in tickets)
        run_ticket = next(t for t in tickets if t.run_id == run_id)
        assert run_ticket.error_type == "RuntimeError"
        assert "Disk full!" in run_ticket.error_message

    def test_unexpected_exception_sets_checkpoint_status_failed(self, test_db):
        run_id = str(uuid.uuid4())
        g = StateGraph("failure-graph")
        g.add_node("broken_step", lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
        runner = StateGraphRunner(g, run_id)

        with pytest.raises(RunFailedException):
            runner.run(initial_state="broken_step", initial_data={})

        latest = chk.get_latest_checkpoint(run_id)
        assert latest.status == "failed"

    def test_resume_with_open_failure_ticket_raises(self, test_db):
        run_id = str(uuid.uuid4())
        g = StateGraph("failure-graph")
        g.add_node("broken_step", lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
        runner = StateGraphRunner(g, run_id)

        with pytest.raises(RunFailedException):
            runner.run(initial_state="broken_step", initial_data={})

        with pytest.raises(RunFailedException):
            runner.run()  # Should raise — open ticket present

    def test_resume_after_resolved_failure_ticket(self, test_db):
        """After resolving the failure ticket, the runner should resume from the failed checkpoint."""
        run_id = str(uuid.uuid4())
        call_count = {"n": 0}

        g = StateGraph("recovery-graph")

        def flaky_step(state):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Transient error")
            return "done", {**state, "recovered": True}

        g.add_node("flaky_step", flaky_step)
        runner = StateGraphRunner(g, run_id)

        # First run fails
        with pytest.raises(RunFailedException):
            runner.run(initial_state="flaky_step", initial_data={})

        # Resolve the failure ticket
        tickets = chk.get_failure_tickets(status="open")
        run_ticket = next(t for t in tickets if t.run_id == run_id)
        chk.resolve_failure_ticket(run_ticket.failure_ticket_id, "Retrying after transient error")

        # Resume — flaky_step is retried from the failed checkpoint
        result = runner.run()
        assert result.get("recovered") is True


class TestCrashRecovery:
    def test_run_resumes_from_persisted_checkpoint_after_simulated_crash(self, test_db):
        """
        Simulate a process crash by running a graph that persists a checkpoint mid-way,
        then creating a brand new runner instance (simulating restart) and verifying
        it picks up from where it left off.
        """
        run_id = str(uuid.uuid4())
        execution_log = []

        g = StateGraph("crash-recovery-graph")

        def step_one(state):
            execution_log.append("step_one")
            return "step_two", {**state, "s1": True}

        def step_two(state):
            execution_log.append("step_two")
            return "done", {**state, "s2": True}

        g.add_node("step_one", step_one)
        g.add_node("step_two", step_two)

        # Run step_one and create a checkpoint, then stop (simulated crash before step_two)
        chk.create_checkpoint(
            run_id=run_id,
            graph_id="crash-recovery-graph",
            state_name="step_two",
            state_data={"s1": True, "_transitions": {"step_one": "step_two"}},
            completed_steps=["step_one"],
            status="active",
        )

        # Simulate restart: new runner instance, no initial_state provided
        new_runner = StateGraphRunner(g, run_id)
        result = new_runner.run()

        # step_one should NOT have run again (it's in completed_steps)
        assert "step_one" not in execution_log
        # step_two should have run
        assert "step_two" in execution_log
        assert result.get("s2") is True
