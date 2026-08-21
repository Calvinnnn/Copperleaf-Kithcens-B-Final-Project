"""StateGraph and StateGraphRunner Engine.

This module provides the generic reusable state-graph runner and state representation
to execute operations step-by-step with durable checkpointing, HITL pause,
failure ticket generation, and crash recovery.
"""
from __future__ import annotations

import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from planning_lab.checkpointing import (
    Checkpoint,
    create_checkpoint,
    create_failure_ticket,
    create_hitl_task,
    get_failure_tickets,
    get_hitl_tasks,
    get_latest_checkpoint,
)


class HITLRequestException(Exception):
    """Exception raised by state graph nodes to request a human-in-the-loop pause."""

    def __init__(self, reason: str, context: Dict[str, Any]) -> None:
        self.reason = reason
        self.context = context
        super().__init__(reason)

class ExternalWaitException(Exception):
    """Raised by a graph node when execution must wait for an external event."""

    def __init__(self, reason: str, context: Dict[str, Any]) -> None:
        self.reason = reason
        self.context = context
        super().__init__(reason)


class RunWaitingException(Exception):
    """Indicates that the graph is safely paused waiting for an external event."""
    pass

class RunPausedException(Exception):
    """Exception indicating the execution is paused for HITL approval."""
    pass


class RunFailedException(Exception):
    """Exception indicating the execution failed and an open ticket exists."""
    pass


class StateGraph:
    """Represents a workflow state-graph with nodes (states) and transitions."""

    def __init__(self, graph_id: str) -> None:
        self.graph_id = graph_id
        self.nodes: Dict[str, Callable[[Dict[str, Any]], Tuple[str, Dict[str, Any]]]] = {}

    def add_node(
        self,
        state_name: str,
        func: Callable[[Dict[str, Any]], Tuple[str, Dict[str, Any]]],
    ) -> None:
        """Register a node/state with the graph."""
        self.nodes[state_name] = func


class StateGraphRunner:
    """Runner that executes a StateGraph for a given run ID with durable checkpoints."""

    def __init__(self, graph: StateGraph, run_id: str) -> None:
        self.graph = graph
        self.run_id = run_id

    def run(
        self,
        initial_state: Optional[str] = None,
        initial_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute the state graph, resuming from the latest checkpoint if one exists.

        Args:
            initial_state: Name of initial state if starting a new run.
            initial_data: Dictionary of initial state data if starting a new run.

        Returns:
            The final state data after reaching the 'done' state.
        """
        checkpoint = get_latest_checkpoint(self.run_id)

        if checkpoint:
            # Check status of the latest checkpoint
            if checkpoint.status == "paused_hitl":
                # Verify if there is still a pending HITL task
                tasks = get_hitl_tasks()
                pending_task = next(
                    (
                        t for t in tasks
                        if t.checkpoint_id == checkpoint.checkpoint_id and t.status == "pending"
                    ),
                    None,
                )
                if pending_task:
                    raise RunPausedException(
                        f"Run {self.run_id} is paused for HITL task: {pending_task.hitl_task_id}"
                    )

                # Fetch resolved task to inject decision into state_data
                resolved_task = next(
                    (
                        t for t in tasks
                        if t.checkpoint_id == checkpoint.checkpoint_id
                        and t.status in ("approved", "rejected", "resolved")
                    ),
                    None,
                )

                state_data = checkpoint.state_data.copy()
                if resolved_task:
                    state_data["_hitl_decision"] = {
                        "task_id": resolved_task.hitl_task_id,
                        "status": resolved_task.status,
                        "decision": resolved_task.decision,
                        "decision_data": resolved_task.decision_data,
                    }

                current_state = checkpoint.state_name
                completed_steps = checkpoint.completed_steps.copy()

            elif checkpoint.status == "failed":
                # Verify if there is still an unresolved failure ticket
                tickets = get_failure_tickets()
                open_ticket = next(
                    (
                        t for t in tickets
                        if t.checkpoint_id == checkpoint.checkpoint_id and t.status != "resolved"
                    ),
                    None,
                )
                if open_ticket:
                    raise RunFailedException(
                        f"Run {self.run_id} is failed. Active failure ticket: {open_ticket.failure_ticket_id}"
                    )

                current_state = checkpoint.state_name
                state_data = checkpoint.state_data.copy()
                completed_steps = checkpoint.completed_steps.copy()

            elif checkpoint.status == "completed":
                return checkpoint.state_data
            else:
                # Active/Normal checkpoint resume
                current_state = checkpoint.state_name
                state_data = checkpoint.state_data.copy()

                # Allow new external data to be injected when resuming a waiting run.
                # Example: parts_received confirmation from an external system.
                if initial_data:
                    state_data.update(initial_data)

                completed_steps = checkpoint.completed_steps.copy()



        else:
            if not initial_state:
                raise ValueError("No checkpoint found and no initial_state provided.")

            current_state = initial_state
            state_data = initial_data or {}
            completed_steps = []

            # Save initial checkpoint
            create_checkpoint(
                run_id=self.run_id,
                graph_id=self.graph.graph_id,
                state_name=current_state,
                state_data=state_data,
                completed_steps=completed_steps,
                status="active",
            )

        # Main execution loop
        while current_state != "done" and current_state is not None:
            # Idempotency / Duplicate Execution Guard
            if current_state in completed_steps:
                transitions = state_data.get("_transitions", {})
                if current_state in transitions:
                    current_state = transitions[current_state]
                    continue

            try:
                node_fn = self.graph.nodes.get(current_state)
                if not node_fn:
                    raise ValueError(f"State node {current_state!r} not found in graph.")

                # Execute current state node
                next_state, updated_data = node_fn(state_data)

                # Record execution details
                completed_steps.append(current_state)
                if "_transitions" not in updated_data:
                    updated_data["_transitions"] = {}
                updated_data["_transitions"][current_state] = next_state

                # Process any requested cleared steps for cyclic graph loops
                if "_clear_completed_steps" in updated_data:
                    clear_list = updated_data.pop("_clear_completed_steps")
                    completed_steps = [s for s in completed_steps if s not in clear_list]
                    transitions = updated_data.get("_transitions", {})
                    for key in clear_list:
                        transitions.pop(key, None)
                    updated_data["_transitions"] = transitions

                state_data = updated_data
                current_state = next_state

                # Save durable checkpoint
                status = "completed" if current_state == "done" else "active"
                create_checkpoint(
                    run_id=self.run_id,
                    graph_id=self.graph.graph_id,
                    state_name=current_state,
                    state_data=state_data,
                    completed_steps=completed_steps,
                    status=status,
                )

            except ExternalWaitException as wait_exc:
                # External waiting is different from HITL.
                # Persist the exact graph state without creating an admin task.
                create_checkpoint(
                    run_id=self.run_id,
                    graph_id=self.graph.graph_id,
                    state_name=current_state,
                    state_data=state_data,
                    completed_steps=completed_steps,
                    pending_action=wait_exc.reason,
                    status="active",
                )

                raise RunWaitingException(
                    f"Run {self.run_id} is waiting in state "
                    f"{current_state!r}: {wait_exc.reason}"
                )

            except HITLRequestException as hitl_exc:
                # Create a checkpoint with status paused_hitl
                chk = create_checkpoint(
                    run_id=self.run_id,
                    graph_id=self.graph.graph_id,
                    state_name=current_state,
                    state_data=state_data,
                    completed_steps=completed_steps,
                    pending_action=hitl_exc.reason,
                    status="paused_hitl",
                )
                # Persist the HITL task
                create_hitl_task(
                    run_id=self.run_id,
                    graph_id=self.graph.graph_id,
                    checkpoint_id=chk.checkpoint_id,
                    state_name=current_state,
                    reason=hitl_exc.reason,
                    context=hitl_exc.context,
                )
                raise RunPausedException(
                    f"Execution paused for HITL in state {current_state!r}: {hitl_exc.reason}"
                )

            except Exception as err:
                # Create a checkpoint with status failed
                chk = create_checkpoint(
                    run_id=self.run_id,
                    graph_id=self.graph.graph_id,
                    state_name=current_state,
                    state_data=state_data,
                    completed_steps=completed_steps,
                    status="failed",
                )
                # Persist the failure ticket
                create_failure_ticket(
                    run_id=self.run_id,
                    graph_id=self.graph.graph_id,
                    checkpoint_id=chk.checkpoint_id,
                    failed_node=current_state,
                    error_type=type(err).__name__,
                    error_message=str(err),
                    error_details=traceback.format_exc(),
                    state_snapshot=state_data,
                )
                raise RunFailedException(
                    f"Execution failed in state {current_state!r} due to {type(err).__name__}: {err}"
                ) from err

        return state_data
