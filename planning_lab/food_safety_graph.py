"""Food Safety State Graph Module (Issue #5).

Implements a real stateful food safety workflow using the shared StateGraph and
StateGraphRunner foundation from planning_lab.

State Flow:
  START -> receive_inspection_request
            ↓
       gather_information (RAG Policy Lookup LLM Technique)
            ↓
        inspect_facility (Observation Logging)
            ↓
        record_findings
         ├── NO VIOLATION  → complete_inspection → done
         └── YES VIOLATION → determine_corrective_action (Constrained ReAct LLM Technique)
                                     ↓
                               admin_review (HITL Approval)
                                     ↓
                         wait_for_corrective_action (External Wait State)
                                     ↓
                                 reinspect
                                    ├── PASSED → complete_inspection → done
                                    └── FAILED → determine_corrective_action (GRAPH CYCLE)
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple

from planning_lab.checkpointing import (
    Checkpoint,
    create_checkpoint,
    get_latest_checkpoint,
)
from planning_lab.state_graph import (
    HITLRequestException,
    StateGraph,
    StateGraphRunner,
)

# Constrained ReAct Allowed Toolset
ALLOWED_CORRECTIVE_TOOLS: Set[str] = {
    "log_waste_report",
    "schedule_reinspection",
    "flag_hazard",
    "issue_sanitation_warning",
}


class ToolNotAllowedError(ValueError):
    """Raised when Constrained ReAct engine attempts to call a tool outside the allowed toolset."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Constrained ReAct LLM Technique Engine (Food Safety)
# ─────────────────────────────────────────────────────────────────────────────

def run_constrained_react_engine(
    violation_details: Dict[str, Any],
    proposed_tools: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Constrained ReAct reasoning engine.

    Formulates a Thought-Action-Observation loop while enforcing an explicit,
    strictly restricted set of allowed tools.
    """
    react_steps = []
    executed_actions = []

    for step_num, tool_call in enumerate(proposed_tools, 1):
        tool_name = tool_call.get("tool_name", "")
        tool_args = tool_call.get("args", {})

        # Constrained validation check
        if tool_name not in ALLOWED_CORRECTIVE_TOOLS:
            raise ToolNotAllowedError(
                f"Constrained ReAct Violation: Tool {tool_name!r} is not in the allowed toolset {sorted(ALLOWED_CORRECTIVE_TOOLS)}"
            )

        # Thought step
        thought = f"Step {step_num}: Violation observed in {violation_details.get('hazard_type', 'food safety')}. Selecting tool {tool_name}."
        react_steps.append({"type": "thought", "content": thought})

        # Action execution (simulation / tool dispatch)
        action_result = {
            "status": "success",
            "tool_name": tool_name,
            "args": tool_args,
            "observation": f"Executed {tool_name} successfully for branch {violation_details.get('branch_id', 1)}.",
        }
        react_steps.append({"type": "action", "tool": tool_name, "args": tool_args})
        react_steps.append({"type": "observation", "content": action_result["observation"]})
        executed_actions.append(action_result)

    return {
        "react_steps": react_steps,
        "executed_actions": executed_actions,
        "action_plan_summary": f"Formulated {len(executed_actions)} corrective actions under Constrained ReAct.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# RAG Food Safety Regulations Lookup
# ─────────────────────────────────────────────────────────────────────────────

def retrieve_food_safety_regulations(query: str, fast_mode: bool = True) -> Dict[str, Any]:
    """RAG lookup helper using existing AgenticRAGOrchestrator or hybrid search.

    Retrieves HACCP regulations, temperature thresholds, and compliance protocols.
    """
    policy_context = (
        "Food Safety Standards (FS-201): Cold storage must maintain temperature <= 4.0°C (40°F). "
        "Sanitation rating must be >= 80/100. Any temperature > 7.0°C requires immediate waste write-off and manager review."
    )

    if not fast_mode:
        try:
            from rag.hybrid_search import hybrid_search
            results = hybrid_search(query, top_k=2)
            if results:
                policy_context = "\n".join([r.get("text", "") for r in results if isinstance(r, dict)])
        except Exception:
            pass

    return {
        "query": query,
        "policy_context": policy_context,
        "max_cold_storage_temp_c": 4.0,
        "critical_temp_threshold_c": 7.0,
        "min_sanitation_score": 80,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Food Safety Graph State Node Functions
# ─────────────────────────────────────────────────────────────────────────────

def node_receive_inspection_request(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Entry state: initializes food safety inspection request."""
    branch_id = state.get("branch_id", 1)
    inspector_id = state.get("inspector_id", "INS-8821")
    state_out = {
        **state,
        "branch_id": branch_id,
        "inspector_id": inspector_id,
        "status": "INSPECTION_STARTED",
    }
    return "gather_information", state_out


def node_gather_information(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: RAG lookup for food safety regulations and standards."""
    reg_info = retrieve_food_safety_regulations("Cold storage temperature and sanitation compliance rules")
    state_out = {
        **state,
        "regulations_context": reg_info["policy_context"],
        "max_cold_temp": reg_info["max_cold_storage_temp_c"],
        "critical_temp": reg_info["critical_temp_threshold_c"],
        "min_sanitation_score": reg_info["min_sanitation_score"],
    }
    return "inspect_facility", state_out


def node_inspect_facility(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Records facility inspection measurements."""
    # Use provided values or default inspection measurements
    fridge_temp = state.get("fridge_temp_celsius", 3.5)
    sanitation_score = state.get("sanitation_score", 92)

    observations = {
        "fridge_temp_celsius": fridge_temp,
        "sanitation_score": sanitation_score,
        "expired_items_found": state.get("expired_items_found", False),
    }

    state_out = {
        **state,
        "observations": observations,
    }
    return "record_findings", state_out


def node_record_findings(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Evaluates observations against safety regulations."""
    obs = state.get("observations", {})
    max_temp = state.get("max_cold_temp", 4.0)
    min_sanitation = state.get("min_sanitation_score", 80)

    violations = []
    if obs.get("fridge_temp_celsius", 0.0) > max_temp:
        violations.append({
            "hazard_type": "COLD_STORAGE_TEMPERATURE_EXCEEDED",
            "measured_value": obs["fridge_temp_celsius"],
            "threshold": max_temp,
            "severity": "CRITICAL" if obs["fridge_temp_celsius"] > state.get("critical_temp", 7.0) else "HIGH",
        })

    if obs.get("sanitation_score", 100) < min_sanitation:
        violations.append({
            "hazard_type": "SANITATION_SCORE_BELOW_MINIMUM",
            "measured_value": obs["sanitation_score"],
            "threshold": min_sanitation,
            "severity": "MEDIUM",
        })

    if obs.get("expired_items_found"):
        violations.append({
            "hazard_type": "EXPIRED_INGREDIENTS_PRESENT",
            "severity": "HIGH",
        })

    if not violations:
        return "complete_inspection", {**state, "violations_found": [], "compliance_result": "PASSED"}

    state_out = {
        **state,
        "violations_found": violations,
        "compliance_result": "ACTION_REQUIRED",
    }
    return "determine_corrective_action", state_out


def node_determine_corrective_action(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Uses Constrained ReAct engine to select allowed corrective tools."""
    violations = state.get("violations_found", [])
    proposed_tools = state.get("proposed_tools")

    if not proposed_tools:
        # Default valid tool choices under Constrained ReAct
        proposed_tools = [
            {"tool_name": "log_waste_report", "args": {"reason": "Spoiled ingredients from temp violation"}},
            {"tool_name": "schedule_reinspection", "args": {"days_hence": 1}},
        ]

    # Execute Constrained ReAct reasoning engine (validates allowed toolset)
    v_info = violations[0] if violations else {"hazard_type": "general_safety", "branch_id": state.get("branch_id", 1)}
    react_output = run_constrained_react_engine(v_info, proposed_tools)

    state_out = {
        **state,
        "react_output": react_output,
        "corrective_action_plan": react_output["action_plan_summary"],
    }
    return "admin_review", state_out


def node_admin_review(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Evaluates severity and triggers HITL review if high risk."""
    hitl_decision = state.get("_hitl_decision")
    if hitl_decision and hitl_decision.get("task_id"):
        decision_val = hitl_decision.get("decision")
        if decision_val in ("approved", "APPROVED"):
            return "wait_for_corrective_action", {**state, "admin_review_status": "APPROVED", "_hitl_decision": None}
        else:
            return "done", {**state, "admin_review_status": "REJECTED", "status": "REJECTED", "_hitl_decision": None}

    violations = state.get("violations_found", [])
    has_high_severity = any(v.get("severity") in ("HIGH", "CRITICAL") for v in violations)

    if has_high_severity:
        raise HITLRequestException(
            reason="High-risk food safety violation corrective action requires manager review",
            context={
                "branch_id": state.get("branch_id"),
                "violations": violations,
                "action_plan": state.get("corrective_action_plan"),
            },
        )

    return "wait_for_corrective_action", {**state, "admin_review_status": "AUTO_APPROVED"}


def node_wait_for_corrective_action(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Genuine external wait state for staff to complete corrective actions."""
    staff_action = state.get("_staff_action_complete")
    hitl_decision = state.get("_hitl_decision")

    if not staff_action and hitl_decision and hitl_decision.get("decision_data"):
        d_data = hitl_decision["decision_data"]
        if isinstance(d_data, dict) and "_staff_action_complete" in d_data:
            staff_action = d_data["_staff_action_complete"]

    if not staff_action:
        raise HITLRequestException(
            reason="WAITING_FOR_STAFF_CORRECTIVE_ACTION",
            context={
                "branch_id": state.get("branch_id"),
                "action_plan": state.get("corrective_action_plan"),
            },
        )

    state_out = {
        **state,
        "_staff_action_complete": staff_action,
        "_hitl_decision": None,
        "status": "STAFF_CORRECTIVE_ACTION_FINISHED",
    }
    return "reinspect", state_out


def node_reinspect(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Re-evaluates facility compliance after corrective actions.

    Branches to complete_inspection if PASSED, or CYCLES back to determine_corrective_action if FAILED.
    """
    staff_action = state.get("_staff_action_complete", {})
    reinspection_result = staff_action.get("reinspection_result", "PASSED").upper()

    if reinspection_result == "PASSED":
        return "complete_inspection", {**state, "reinspection_status": "PASSED"}
    elif reinspection_result == "FAILED":
        cycled_nodes = ["determine_corrective_action", "admin_review", "wait_for_corrective_action", "reinspect"]
        state_out = {
            **state,
            "reinspection_status": "FAILED",
            "_staff_action_complete": None,  # Reset external wait state
            "_hitl_decision": None,
            "_clear_completed_steps": cycled_nodes,
            "proposed_tools": [
                {"tool_name": "flag_hazard", "args": {"severity": "CRITICAL"}},
                {"tool_name": "issue_sanitation_warning", "args": {"level": 2}},
            ],
        }
        return "determine_corrective_action", state_out
    else:
        raise ValueError(f"Unparseable reinspection result: {reinspection_result!r}")


def node_complete_inspection(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Terminal Node: Completes food safety inspection."""
    state_out = {
        **state,
        "status": "COMPLETED",
        "inspection_completed": True,
    }
    return "done", state_out


# ─────────────────────────────────────────────────────────────────────────────
# Graph Construction Helper
# ─────────────────────────────────────────────────────────────────────────────

def create_food_safety_graph(graph_id: str = "food-safety-graph") -> StateGraph:
    """Build and return the complete Food Safety StateGraph instance."""
    graph = StateGraph(graph_id=graph_id)
    graph.add_node("receive_inspection_request", node_receive_inspection_request)
    graph.add_node("gather_information", node_gather_information)
    graph.add_node("inspect_facility", node_inspect_facility)
    graph.add_node("record_findings", node_record_findings)
    graph.add_node("determine_corrective_action", node_determine_corrective_action)
    graph.add_node("admin_review", node_admin_review)
    graph.add_node("wait_for_corrective_action", node_wait_for_corrective_action)
    graph.add_node("reinspect", node_reinspect)
    graph.add_node("complete_inspection", node_complete_inspection)
    return graph
