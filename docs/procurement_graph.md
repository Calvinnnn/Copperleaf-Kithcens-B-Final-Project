# Procurement State Graph Architecture & Implementation (Issue #3)

## Overview
The **Procurement State Graph** automates inventory reordering, task decomposition of multi-item requests, supplier policy evaluation via RAG, human-in-the-loop (HITL) approval gates for large budget thresholds, and external supplier status tracking within the Copperleaf Kitchens platform.

---

## State Diagram

```
START ──▶ receive_request
               │
               ▼
       decompose_request (Task Decomposition LLM Technique)
               │
               ▼
        check_inventory (MCP SQL Tool / DB lookup)
               │
               ▼
       evaluate_supplier (RAG Supplier Policy Search)
               │
               ▼
        check_approval
         ├── estimated_total <= $1,000 ──▶ create_purchase_order
         └── estimated_total > $1,000  ──▶ [HITL Pause] ──▶ Admin Review
                                                                │
                                            ┌───────────────────┴───────────────────┐
                                            ▼                                       ▼
                                     Status: APPROVED                        Status: REJECTED
                                            │                                       │
                                            ▼                                       ▼
                                   create_purchase_order                          done
                                            │                                  (Status: REJECTED)
                                            ▼
                                  waiting_for_supplier (External Wait State)
                                            │
                                            ▼
                                process_supplier_response
                                 ├── ACCEPTED ──▶ receive_order ──▶ done (Status: COMPLETED)
                                 └── REJECTED ──▶ evaluate_supplier (Loop)
```

---

## Explicit Graph States

| State | Purpose | Next State |
|-------|---------|------------|
| `receive_request` | Parses input raw request string and branch parameters. | `decompose_request` |
| `decompose_request` | **Task Decomposition**: Converts unstructured request text into structured line items, quantities, urgency, and budget constraints. | `check_inventory` |
| `check_inventory` | Queries current inventory via MCP/DB tools to determine exact procurement shortfall. | `evaluate_supplier` |
| `evaluate_supplier` | **RAG**: Queries RAG knowledge base for supplier rules, pricing, and preferred vendor rankings (`APX-9982`, `GRW-4477`). | `check_approval` |
| `check_approval` | Checks estimated total against $1,000 threshold. If exceeded, raises `HITLRequestException` to pause execution for admin review. | `create_purchase_order` (Approved) or `done` (Rejected) |
| `create_purchase_order` | Generates a purchase order (`PO-1-XXXX-8821`). | `waiting_for_supplier` |
| `waiting_for_supplier` | **External Waiting State**: Raises `HITLRequestException` (`WAITING_FOR_SUPPLIER`) to pause execution cleanly until external supplier response payload arrives. | `process_supplier_response` |
| `process_supplier_response` | Evaluates supplier response (`ACCEPTED` vs `REJECTED`). | `receive_order` (ACCEPTED) or `evaluate_supplier` (REJECTED loop) |
| `receive_order` | Terminal node marking order items received and status `COMPLETED`. | `done` |

---

## LLM Techniques Integrated

1. **Task Decomposition**:
   - Location: `planning_lab/procurement_graph.py` inside `node_decompose_request`.
   - Functionality: Parses freeform procurement requests into structured Pydantic-compatible JSON objects containing item names, target quantities, unit types, and urgency flags.

2. **RAG Policy Search**:
   - Location: `planning_lab/procurement_graph.py` inside `node_evaluate_supplier`.
   - Functionality: Retrieves supplier rules, contract terms, and preferred vendor lists from the vector store / hybrid search engine.

---

## Checkpointing, HITL & Failure Recovery

- **Checkpoints**: Every node transition persists state data, version number, and completed step list to SQLite `db/copperleaf.db`.
- **HITL Integration**: Budget threshold (> $1,000) and supplier wait states trigger `HITLRequestException`, automatically logging tasks to `hitl_tasks` table.
- **Failure Ticket Recovery**: Unhandled exceptions generate an open ticket in `failure_tickets`. After ticket resolution via MCP tool or admin action, execution resumes seamlessly from the saved checkpoint without repeating completed steps.
