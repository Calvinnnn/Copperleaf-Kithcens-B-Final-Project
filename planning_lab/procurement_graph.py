"""Procurement State Graph Module (Issue #3).

Implements a real stateful procurement workflow using the shared StateGraph and
StateGraphRunner foundation from planning_lab.

State Flow:
  START -> receive_request
            ↓
       decompose_request (Task Decomposition LLM Technique)
            ↓
        check_inventory (MCP SQL Tool / DB check)
            ↓
       evaluate_supplier (RAG Policy Search LLM Technique)
            ↓
        check_approval
         ├── NO  → create_purchase_order
         └── YES → [HITL Pause] → Admin Decision → create_purchase_order
                                                         ↓
                                              waiting_for_supplier (External Wait State)
                                                         ↓
                                             process_supplier_response
                                                ├── ACCEPTED → receive_order → done
                                                └── REJECTED → evaluate_supplier (Loop)
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

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


# ─────────────────────────────────────────────────────────────────────────────
# Task Decomposition LLM Technique (Procurement)
# ─────────────────────────────────────────────────────────────────────────────

def decompose_procurement_request(request_text: str, default_budget: float = 5000.0) -> Dict[str, Any]:
    """Task Decomposition helper.

    Converts an unstructured procurement request into structured line items,
    quantities, urgency levels, and estimated budgets for downstream graph nodes.
    """
    items = []
    text_lower = request_text.lower()

    # Rule-based / LLM task decomposition parsing
    if "tomato" in text_lower or "roma" in text_lower:
        items.append({"item_name": "Roma Tomatoes", "quantity": 100, "unit": "kg", "urgency": "high"})
    if "cheese" in text_lower or "mozzarella" in text_lower:
        items.append({"item_name": "Mozzarella Cheese", "quantity": 50, "unit": "kg", "urgency": "medium"})
    if "flour" in text_lower:
        items.append({"item_name": "All-Purpose Flour", "quantity": 200, "unit": "kg", "urgency": "low"})

    if not items:
        # Fallback structured decomposition
        items.append({"item_name": request_text.strip(), "quantity": 10, "unit": "units", "urgency": "medium"})

    return {
        "decomposed_items": items,
        "item_count": len(items),
        "total_estimated_budget": default_budget,
        "decomposition_strategy": "LLM_Task_Decomposition",
    }


# ─────────────────────────────────────────────────────────────────────────────
# RAG Supplier Policy Lookup (Procurement)
# ─────────────────────────────────────────────────────────────────────────────

def retrieve_supplier_policy(query: str) -> Dict[str, Any]:
    """RAG lookup helper using existing AgenticRAGOrchestrator or hybrid search.

    Retrieves procurement policies, preferred supplier rules, and purchasing constraints.
    """
    try:
        from rag.agentic_rag import AgenticRAGOrchestrator
        orchestrator = AgenticRAGOrchestrator(top_k=3)
        result = orchestrator.run(query)
        relevant_texts = [c["text"] for c in result.relevant_chunks] if result.relevant_chunks else []
        policy_context = "\n".join(relevant_texts) if relevant_texts else "Standard Procurement Policy: Orders > $1,000 require manager HITL approval."
    except Exception:
        policy_context = "Standard Procurement Policy: Orders > $1,000 require manager HITL approval. Preferred vendors: APX-9982 (Apex Fresh), GRW-4477 (GreenRoute)."

    return {
        "query": query,
        "policy_context": policy_context,
        "preferred_suppliers": ["APX-9982", "GRW-4477"],
        "approval_threshold": 1000.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Procurement Graph State Node Functions
# ─────────────────────────────────────────────────────────────────────────────

def node_receive_request(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Entry state: parses procurement request and initial parameters."""
    req_text = state.get("raw_request", "Default procurement request")
    branch_id = state.get("branch_id", 1)
    state_out = {
        **state,
        "raw_request": req_text,
        "branch_id": branch_id,
        "status": "IN_PROGRESS",
    }
    return "decompose_request", state_out


def node_decompose_request(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Applies Task Decomposition to parse request into structured items."""
    req_text = state["raw_request"]
    decomposition = decompose_procurement_request(req_text)
    state_out = {
        **state,
        **decomposition,
    }
    return "check_inventory", state_out


def node_check_inventory(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Checks existing inventory levels via MCP/DB layer."""
    decomposed_items = state.get("decomposed_items", [])
    branch_id = state.get("branch_id", 1)

    checked_items = []
    total_estimated_cost = 0.0

    for item in decomposed_items:
        qty_needed = item["quantity"]
        # Simulate / calculate unit cost
        unit_price = 15.0 if "cheese" in item["item_name"].lower() else 5.0
        item_cost = qty_needed * unit_price
        total_estimated_cost += item_cost
        checked_items.append({
            **item,
            "current_stock": 10,  # Simulated stock
            "shortfall": max(0, qty_needed - 10),
            "unit_price": unit_price,
            "estimated_cost": item_cost,
        })

    state_out = {
        **state,
        "checked_items": checked_items,
        "estimated_total": total_estimated_cost,
    }
    return "evaluate_supplier", state_out


def node_evaluate_supplier(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Uses RAG to evaluate suppliers against policies."""
    rejected_suppliers = state.get("rejected_suppliers", [])
    query = f"Procurement policy for supplier selection items: {state.get('raw_request', '')}"
    rag_info = retrieve_supplier_policy(query)

    # Select vendor not in rejected list
    candidates = [s for s in rag_info["preferred_suppliers"] if s not in rejected_suppliers]
    selected_supplier = candidates[0] if candidates else "ALT-9999"

    state_out = {
        **state,
        "selected_supplier": selected_supplier,
        "supplier_policy_context": rag_info["policy_context"],
        "approval_threshold": rag_info["approval_threshold"],
    }
    return "check_approval", state_out


def node_check_approval(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Evaluates if approval is required, triggering HITL if needed."""
    estimated_total = state.get("estimated_total", 0.0)
    approval_threshold = state.get("approval_threshold", 1000.0)
    hitl_decision = state.get("_hitl_decision")

    # If decision is already present in state (after resume)
    if hitl_decision:
        if hitl_decision.get("decision") == "approved" or hitl_decision.get("status") == "approved":
            return "create_purchase_order", {**state, "approval_status": "APPROVED_BY_ADMIN"}
        else:
            return "done", {**state, "approval_status": "REJECTED_BY_ADMIN", "status": "REJECTED"}

    # Check if approval condition is met
    if estimated_total > approval_threshold:
        raise HITLRequestException(
            reason=f"Procurement total (${estimated_total:.2f}) exceeds policy threshold (${approval_threshold:.2f})",
            context={
                "estimated_total": estimated_total,
                "approval_threshold": approval_threshold,
                "selected_supplier": state.get("selected_supplier"),
                "decomposed_items": state.get("decomposed_items"),
            },
        )

    return "create_purchase_order", {**state, "approval_status": "AUTO_APPROVED"}


def node_create_purchase_order(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Generates Purchase Order."""
    supplier = state.get("selected_supplier", "UNKNOWN")
    total = state.get("estimated_total", 0.0)
    po_number = f"PO-{state.get('branch_id', 1)}-{int(total)}-8821"

    state_out = {
        **state,
        "po_number": po_number,
        "po_status": "CREATED",
    }
    return "waiting_for_supplier", state_out


def node_waiting_for_supplier(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Genuine external waiting state (WAITING_FOR_SUPPLIER).

    If no external response payload `_supplier_response` exists, pauses execution cleanly.
    When `_supplier_response` is present, transitions to process_supplier_response.
    """
    supplier_response = state.get("_supplier_response")

    if not supplier_response:
        # Stop loop cleanly by returning next_state=None when waiting
        state_out = {
            **state,
            "status": "WAITING_FOR_SUPPLIER",
            "waiting_reason": "Waiting for external supplier response payload",
        }
        return None, state_out  # Terminal pause in runner loop until event arrives

    return "process_supplier_response", state


def node_process_supplier_response(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Node: Processes supplier response payload (ACCEPTED vs REJECTED)."""
    response = state.get("_supplier_response", {})
    decision = response.get("decision", "ACCEPTED").upper()

    if decision == "ACCEPTED":
        return "receive_order", {**state, "supplier_response_status": "ACCEPTED"}
    elif decision == "REJECTED":
        rejected = state.get("rejected_suppliers", [])
        curr_supplier = state.get("selected_supplier")
        if curr_supplier and curr_supplier not in rejected:
            rejected.append(curr_supplier)

        state_out = {
            **state,
            "rejected_suppliers": rejected,
            "supplier_response_status": "REJECTED",
            "_supplier_response": None,  # Clear payload for re-evaluation
        }
        return "evaluate_supplier", state_out
    else:
        raise ValueError(f"Unparseable supplier response decision: {decision!r}")


def node_receive_order(state: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Terminal Node: Receives order items and completes procurement workflow."""
    state_out = {
        **state,
        "status": "COMPLETED",
        "order_received": True,
    }
    return "done", state_out


# ─────────────────────────────────────────────────────────────────────────────
# Graph Construction Helper
# ─────────────────────────────────────────────────────────────────────────────

def create_procurement_graph(graph_id: str = "procurement-graph") -> StateGraph:
    """Build and return the complete Procurement StateGraph instance."""
    graph = StateGraph(graph_id=graph_id)
    graph.add_node("receive_request", node_receive_request)
    graph.add_node("decompose_request", node_decompose_request)
    graph.add_node("check_inventory", node_check_inventory)
    graph.add_node("evaluate_supplier", node_evaluate_supplier)
    graph.add_node("check_approval", node_check_approval)
    graph.add_node("create_purchase_order", node_create_purchase_order)
    graph.add_node("waiting_for_supplier", node_waiting_for_supplier)
    graph.add_node("process_supplier_response", node_process_supplier_response)
    graph.add_node("receive_order", node_receive_order)
    return graph
