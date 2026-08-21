"""Maintenance State Graph Module (Issue #7).

Implements the third genuinely stateful Copperleaf Kitchens workflow.

State Flow:

Maintenance Request
        ↓
     Diagnose
    (RAG + LATS)
        ↓
 Approval Check
        ↓
   Parts Needed?
     /       \
   NO         YES
   ↓           ↓
Schedule    Order Parts
 Work           ↓
   │       Wait for Parts
   │           ↓
   │       Parts Received
   │           ↓
   └────────→ Schedule Work
                  ↓
          Perform Maintenance
                  ↓
              Inspection
                  ↓
           Issue Resolved?
             /       \
           YES        NO
            ↓          ↓
         Resolved   Re-diagnose
            ↓          │
         Complete      └────→ Diagnose
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from langchain_core.language_models.chat_models import BaseChatModel

from planning_lab.algorithms.environment import Environment
from planning_lab.algorithms.lats import flatten_lats_tree, lats
from planning_lab.state_graph import (
    ExternalWaitException,
    HITLRequestException,
    StateGraph,
)

from rag.agentic_rag import AgenticRAGOrchestrator


# ---------------------------------------------------------------------
# Maintenance configuration
# ---------------------------------------------------------------------

PARTS_APPROVAL_THRESHOLD = 500.0


# ---------------------------------------------------------------------
# LLM setup
# ---------------------------------------------------------------------

def _build_default_llm() -> Optional[BaseChatModel]:
    """Create the real LLM used by the Maintenance LATS node.

    Tests may run the graph in fast_mode without an external model.
    Production/demo runs should provide MISTRAL_API_KEY.
    """

    api_key = os.getenv("MISTRAL_API_KEY")

    if not api_key:
        return None

    from langchain_mistralai import ChatMistralAI

    return ChatMistralAI(
        api_key=api_key,
        model=os.getenv("MISTRAL_MODEL", "mistral-small-latest"),
        temperature=0,
    )


# ---------------------------------------------------------------------
# RAG — Maintenance manuals / procedures
# ---------------------------------------------------------------------

def retrieve_maintenance_knowledge(query: str) -> Dict[str, Any]:
    """Retrieve maintenance manuals and procedures through the existing RAG system."""

    orchestrator = AgenticRAGOrchestrator(
        top_k=5,
        max_retry_attempts=1,
        use_hybrid_search=True,
    )

    result = orchestrator.run(query)

    retrieved = result.relevant_chunks or result.retrieved_chunks

    return {
        "rag_query": query,
        "rag_context": result.answer_context,
        "rag_retrieved_count": len(retrieved),
        "rag_trace": result.retrieval_trace,
        "rag_was_rewritten": result.was_rewritten,
    }


# ---------------------------------------------------------------------
# LATS — Diagnostic path search
# ---------------------------------------------------------------------

def run_maintenance_lats(
    issue_description: str,
    rag_context: str,
    llm: Optional[BaseChatModel],
    fast_mode: bool = False,
) -> Dict[str, Any]:
    """Evaluate alternative maintenance/diagnostic paths using LATS."""

    if fast_mode:
        # Deterministic path used by automated tests only.
        # The real demo path uses planning_lab.algorithms.lats with an LLM.
        return {
            "lats_success": True,
            "lats_output": (
                "Inspect the affected equipment, isolate the likely failed "
                "component, verify power and safety conditions, replace or "
                "repair the damaged component if required, then perform a "
                "post-repair inspection."
            ),
            "lats_best_score": 1.0,
            "lats_iterations": 0,
            "lats_tree": [],
            "lats_mode": "TEST_FAST_MODE",
        }

    active_llm = llm or _build_default_llm()

    if active_llm is None:
        raise RuntimeError(
            "Maintenance LATS requires an LLM. "
            "Set MISTRAL_API_KEY or pass an LLM to create_maintenance_graph()."
        )

    task = (
        "Diagnose this Copperleaf Kitchens equipment maintenance problem and "
        "choose the safest, most practical repair path. Consider whether "
        "replacement parts are required and what inspection should confirm "
        f"the repair.\n\nReported problem: {issue_description}"
    )

    result = lats(
        task=task,
        llm=active_llm,
        environment=Environment(),
        iterations=2,
        n_actions=2,
        context=rag_context,
    )

    return {
        "lats_success": result.success,
        "lats_output": result.output,
        "lats_best_score": result.best_score,
        "lats_iterations": result.iterations,
        "lats_tree": flatten_lats_tree(result.root),
        "lats_mode": "REAL_LATS",
    }


# ---------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------

def _infer_parts_needed(issue_description: str, diagnosis: str) -> bool:
    """Infer whether repair parts are likely required."""

    text = f"{issue_description} {diagnosis}".lower()

    part_keywords = (
        "replace",
        "replacement",
        "broken",
        "damaged component",
        "failed component",
        "motor",
        "compressor",
        "belt",
        "sensor",
        "valve",
        "fan",
        "pump",
        "thermostat",
    )

    return any(keyword in text for keyword in part_keywords)


def _route_after_approval(state: Dict[str, Any]) -> str:
    """Choose parts ordering or direct scheduling."""

    if state.get("parts_required", False):
        return "order_parts"

    return "schedule_work"


# ---------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------

def node_maintenance_request(
    state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """REQUESTED — receive and normalize a maintenance request."""

    issue_description = state.get(
        "issue_description",
        "Unspecified kitchen equipment maintenance problem",
    )

    equipment = state.get("equipment", "Unknown equipment")
    branch_id = state.get("branch_id", 1)

    state_out = {
        **state,
        "issue_description": issue_description,
        "equipment": equipment,
        "branch_id": branch_id,
        "status": "REQUESTED",
        "diagnosis_attempt": state.get("diagnosis_attempt", 1),
    }

    return "diagnose", state_out


def node_diagnose(
    state: Dict[str, Any],
    llm: Optional[BaseChatModel] = None,
    fast_mode: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """DIAGNOSING — use RAG + LATS to evaluate repair paths."""

    issue_description = state["issue_description"]
    equipment = state.get("equipment", "Unknown equipment")

    rag_query = (
        f"Maintenance manual troubleshooting repair procedure for "
        f"{equipment}: {issue_description}"
    )

    # RAG technique
    rag_data = retrieve_maintenance_knowledge(rag_query)

    # LATS technique using the RAG output as grounded context
    lats_data = run_maintenance_lats(
        issue_description=issue_description,
        rag_context=rag_data["rag_context"],
        llm=llm,
        fast_mode=fast_mode,
    )

    # Explicit input can override automatic inference for real external data.
    if "parts_needed" in state:
        parts_required = bool(state["parts_needed"])
    elif state.get("parts_installed"):
        parts_required = bool(state.get("new_parts_needed", False))
    else:
        parts_required = _infer_parts_needed(
            issue_description,
            lats_data["lats_output"],
        )

    estimated_parts_cost = float(
        state.get(
            "estimated_parts_cost",
            0.0 if not parts_required else 250.0,
        )
    )

    safety_critical = bool(state.get("safety_critical", False))

    approval_required = (
        safety_critical
        or estimated_parts_cost > PARTS_APPROVAL_THRESHOLD
    )

    state_out = {
        **state,
        **rag_data,
        **lats_data,
        "status": "DIAGNOSING",
        "parts_required": parts_required,
        "estimated_parts_cost": estimated_parts_cost,
        "safety_critical": safety_critical,
        "approval_required": approval_required,
    }

    return "approval_check", state_out


def node_approval_check(
    state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """HITL gate for expensive or safety-critical maintenance."""

    if not state.get("approval_required", False):
        return _route_after_approval(state), {
            **state,
            "approval_status": "NOT_REQUIRED",
        }

    hitl_decision = state.get("_hitl_decision")

    if hitl_decision and hitl_decision.get("task_id"):
        decision = str(hitl_decision.get("decision", "")).lower()

        if decision == "approved":
            next_state = _route_after_approval(state)

            return next_state, {
                **state,
                "approval_status": "APPROVED_BY_ADMIN",
                "_hitl_decision": None,
            }

        if decision == "rejected":
            return "complete", {
                **state,
                "approval_status": "REJECTED_BY_ADMIN",
                "maintenance_outcome": "NOT_AUTHORIZED",
                "_hitl_decision": None,
            }

    raise HITLRequestException(
        reason="MAINTENANCE_ADMIN_APPROVAL_REQUIRED",
        context={
            "equipment": state.get("equipment"),
            "issue_description": state.get("issue_description"),
            "estimated_parts_cost": state.get("estimated_parts_cost"),
            "safety_critical": state.get("safety_critical"),
            "diagnosis": state.get("lats_output"),
        },
    )


def node_order_parts(
    state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """Create the parts-order stage before the genuine external wait."""

    attempt = int(state.get("diagnosis_attempt", 1))
    branch_id = int(state.get("branch_id", 1))

    parts_order_id = state.get(
        "parts_order_id",
        f"MP-{branch_id}-{attempt:03d}",
    )

    state_out = {
        **state,
        "parts_order_id": parts_order_id,
        "parts_ordered": True,
        "status": "WAITING_FOR_PARTS",
    }

    return "wait_for_parts", state_out


def node_wait_for_parts(
    state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """WAITING_FOR_PARTS — genuine pause for an external delivery event."""

    parts_received = bool(state.get("_parts_received", False))

    if not parts_received:
        raise ExternalWaitException(
            reason="WAITING_FOR_PARTS",
            context={
                "parts_order_id": state.get("parts_order_id"),
                "equipment": state.get("equipment"),
                "branch_id": state.get("branch_id"),
            },
        )

    return "parts_received", {
        **state,
        "status": "PARTS_RECEIVED",
    }


def node_parts_received(
    state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """PARTS_RECEIVED — record arrival and continue the workflow."""

    state_out = {
        **state,
        "status": "PARTS_RECEIVED",
        "parts_received": True,
        "parts_installed": True,
        "_parts_received": None,
    }

    return "schedule_work", state_out


def node_schedule_work(
    state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """SCHEDULED — prepare the maintenance work."""

    state_out = {
        **state,
        "status": "SCHEDULED",
        "scheduled_slot": state.get(
            "scheduled_slot",
            "NEXT_AVAILABLE_MAINTENANCE_SLOT",
        ),
    }

    return "perform_maintenance", state_out


def node_perform_maintenance(
    state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """IN_PROGRESS — execute the selected diagnostic/repair path."""

    state_out = {
        **state,
        "status": "IN_PROGRESS",
        "repair_performed": True,
        "repair_action": state.get(
            "lats_output",
            "Maintenance performed according to diagnostic plan.",
        ),
    }

    return "inspection", state_out


def node_inspection(
    state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """INSPECTION — verify whether the maintenance solved the issue."""

    inspection_sequence = list(
        state.get("inspection_sequence", [])
    )

    if inspection_sequence:
        inspection_passed = bool(inspection_sequence.pop(0))
    else:
        inspection_passed = bool(
            state.get("inspection_passed", True)
        )

    state_out = {
        **state,
        "status": "INSPECTION",
        "inspection_passed": inspection_passed,
        "inspection_sequence": inspection_sequence,
    }

    if inspection_passed:
        return "resolved", state_out

    return "rediagnose", state_out


def node_rediagnose(
    state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """REQUIRES_REDIAGNOSIS — genuine graph cycle back to diagnosis."""

    diagnosis_attempt = int(
        state.get("diagnosis_attempt", 1)
    ) + 1

    # These nodes must be allowed to execute again during the cycle.
    cycle_nodes = [
        "diagnose",
        "approval_check",
        "order_parts",
        "wait_for_parts",
        "parts_received",
        "schedule_work",
        "perform_maintenance",
        "inspection",
        "rediagnose",
    ]

    state_out = {
        **state,
        "status": "REQUIRES_REDIAGNOSIS",
        "diagnosis_attempt": diagnosis_attempt,
        "_hitl_decision": None,
        "_clear_completed_steps": cycle_nodes,
    }

    return "diagnose", state_out


def node_resolved(
    state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """RESOLVED — successful post-maintenance inspection."""

    return "complete", {
        **state,
        "status": "RESOLVED",
        "maintenance_outcome": "RESOLVED",
    }


def node_complete(
    state: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """COMPLETED — terminal maintenance state."""

    state_out = {
        **state,
        "status": "COMPLETED",
        "maintenance_completed": True,
    }

    return "done", state_out


# ---------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------

def create_maintenance_graph(
    graph_id: str = "maintenance-graph",
    llm: Optional[BaseChatModel] = None,
    fast_mode: bool = False,
) -> StateGraph:
    """Build the complete Maintenance StateGraph."""

    graph = StateGraph(graph_id=graph_id)

    graph.add_node(
        "maintenance_request",
        node_maintenance_request,
    )

    graph.add_node(
        "diagnose",
        lambda state: node_diagnose(
            state,
            llm=llm,
            fast_mode=fast_mode,
        ),
    )

    graph.add_node(
        "approval_check",
        node_approval_check,
    )

    graph.add_node(
        "order_parts",
        node_order_parts,
    )

    graph.add_node(
        "wait_for_parts",
        node_wait_for_parts,
    )

    graph.add_node(
        "parts_received",
        node_parts_received,
    )

    graph.add_node(
        "schedule_work",
        node_schedule_work,
    )

    graph.add_node(
        "perform_maintenance",
        node_perform_maintenance,
    )

    graph.add_node(
        "inspection",
        node_inspection,
    )

    graph.add_node(
        "rediagnose",
        node_rediagnose,
    )

    graph.add_node(
        "resolved",
        node_resolved,
    )

    graph.add_node(
        "complete",
        node_complete,
    )

    return graph