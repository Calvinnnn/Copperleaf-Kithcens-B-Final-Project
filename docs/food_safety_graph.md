# Food Safety State Graph Architecture & Implementation (Issue #5)

## Overview
The **Food Safety State Graph** automates kitchen health inspection workflows, RAG-based regulatory compliance lookup, inspection observation logging, **Constrained ReAct** tool enforcement for corrective action plans, manager HITL review, external staff corrective action waiting states, and graph cycles for failed re-inspections within the Copperleaf Kitchens platform.

---

## State Diagram & Graph Cycle

```
START ──▶ receive_inspection_request
               │
               ▼
        gather_information (RAG Regulatory Search)
               │
               ▼
        inspect_facility (Observation Logging)
               │
               ▼
        record_findings
         ├── Compliance: PASSED ──────▶ complete_inspection ──▶ done (Status: COMPLETED)
         └── Compliance: ACTION_REQ  ──▶ determine_corrective_action (Constrained ReAct Engine)
                                              │
                                              ▼
                                         admin_review (HITL Manager Review)
                                              │
                                              ▼
                                 wait_for_corrective_action (External Wait State)
                                              │
                                              ▼
                                          reinspect
                                           ├── PASSED ──▶ complete_inspection ──▶ done
                                           └── FAILED ──▶ determine_corrective_action (GRAPH CYCLE)
```

---

## Explicit Graph States

| State | Purpose | Next State |
|-------|---------|------------|
| `receive_inspection_request` | Initializes inspection context (branch ID, inspector ID). | `gather_information` |
| `gather_information` | **RAG Lookup**: Retrieves HACCP rules, temperature thresholds (<= 4.0°C), and sanitation standards. | `inspect_facility` |
| `inspect_facility` | Logs physical observations (fridge temperature, sanitation score, expired ingredients). | `record_findings` |
| `record_findings` | Evaluates observations against RAG standards. If compliant, passes; if non-compliant, flags violations. | `complete_inspection` (Passed) or `determine_corrective_action` (Violations) |
| `determine_corrective_action` | **Constrained ReAct**: Executes Thought-Action-Observation loop while strictly enforcing allowed toolset (`log_waste_report`, `schedule_reinspection`, `flag_hazard`, `issue_sanitation_warning`). | `admin_review` |
| `admin_review` | **HITL Pause**: Triggers manager review if high/critical severity violations exist. | `wait_for_corrective_action` |
| `wait_for_corrective_action` | **External Wait State**: Raises `HITLRequestException` (`WAITING_FOR_STAFF_CORRECTIVE_ACTION`) until kitchen staff complete corrective measures. | `reinspect` |
| `reinspect` | Evaluates re-inspection findings. If PASSED, transitions to completion. If FAILED, **cycles back** to `determine_corrective_action`. | `complete_inspection` (PASSED) or `determine_corrective_action` (FAILED cycle) |
| `complete_inspection` | Terminal node marking inspection complete and status `COMPLETED`. | `done` |

---

## LLM Techniques Integrated

1. **RAG Policy Search**:
   - Location: `planning_lab/food_safety_graph.py` inside `node_gather_information`.
   - Functionality: Queries regulatory standards (FS-201, cold storage thresholds, sanitation requirements).

2. **Constrained ReAct Engine**:
   - Location: `planning_lab/food_safety_graph.py` inside `run_constrained_react_engine`.
   - Functionality: Restricts available tools to an explicit white-list (`ALLOWED_CORRECTIVE_TOOLS`). Unapproved tool choices raise `ToolNotAllowedError`.

---

## Graph Cycle Proof (`reinspect` ↔ `determine_corrective_action`)

- When re-inspection yields `FAILED`, `node_reinspect` injects `_clear_completed_steps = ["determine_corrective_action", "admin_review", "wait_for_corrective_action", "reinspect"]`.
- `StateGraphRunner` purges cycled nodes from `completed_steps` and `_transitions`, permitting clean re-execution of corrective action determination and secondary HITL approvals without getting trapped in idempotency guards.

---

## Checkpointing & Crash Recovery

- State checkpoints are saved to SQLite `db/copperleaf.db` at every transition.
- Process crashes (e.g. SIGKILL, power outage) automatically resume from the latest saved checkpoint upon process restart without losing progress or re-running completed steps.
