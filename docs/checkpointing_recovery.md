# Checkpointing, HITL, and Failure Recovery Architecture

## Overview

This document describes the durable state-management infrastructure added to the Copperleaf Kitchens project as part of Issue #2. It provides a generic, reusable engine for any future state-graph agents to execute safely with persistent recovery after crashes, human approval gates, and unexpected failures.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         StateGraphRunner                            │
│                                                                     │
│  run() ──▶  load_latest_checkpoint()                                │
│             │                                                       │
│             ├─ No checkpoint ──▶ create_initial_checkpoint()        │
│             ├─ status=paused_hitl ──▶ check pending HITL tasks      │
│             ├─ status=failed ──▶ check open failure tickets         │
│             └─ status=active/completed ──▶ resume from state        │
│                                                                     │
│  Execution Loop:                                                    │
│    for each state:                                                  │
│      if state in completed_steps → SKIP (idempotency)              │
│      node_fn(state_data) → (next_state, updated_data)              │
│      ├─ Success → create_checkpoint(status=active/completed)        │
│      ├─ HITLRequestException → create_checkpoint(paused_hitl)       │
│      │                       → create_hitl_task()                  │
│      │                       → raise RunPausedException             │
│      └─ Any other Exception → create_checkpoint(status=failed)      │
│                             → create_failure_ticket()               │
│                             → raise RunFailedException              │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │   SQLite DB     │
                    │                 │
                    │  checkpoints    │
                    │  hitl_tasks     │
                    │  failure_tickets│
                    └─────────────────┘
```

---

## Database Schema

Tables are created by [`db/migrate_checkpoint.sql`](../db/migrate_checkpoint.sql) and applied automatically by [`mcp_server/init_db.py`](../mcp_server/init_db.py).

### `checkpoints`

Stores a durable, versioned record of a run's state after each node executes.

| Column | Type | Description |
|--------|------|-------------|
| `checkpoint_id` | TEXT PK | UUID for this checkpoint |
| `run_id` | TEXT | Unique identifier for the workflow run |
| `graph_id` | TEXT | Identifier of the StateGraph definition |
| `state_name` | TEXT | Current node/state name |
| `state_data_json` | TEXT | JSON-serialised state dictionary |
| `completed_steps_json` | TEXT | JSON list of node names already executed |
| `pending_action` | TEXT | Optional: reason for HITL pause |
| `checkpoint_version` | INTEGER | Auto-incrementing per `run_id` |
| `status` | TEXT | `active`, `paused_hitl`, `failed`, `completed` |
| `created_at` | TEXT | ISO-8601 UTC timestamp |
| `updated_at` | TEXT | ISO-8601 UTC timestamp |

### `hitl_tasks`

Tracks human-in-the-loop review requests. A task is created when a node raises `HITLRequestException`.

| Column | Type | Description |
|--------|------|-------------|
| `hitl_task_id` | TEXT PK | UUID |
| `run_id` | TEXT | Parent run |
| `graph_id` | TEXT | Parent graph |
| `checkpoint_id` | TEXT FK → checkpoints | The checkpoint captured at pause time |
| `state_name` | TEXT | Node that requested human approval |
| `reason` | TEXT | Human-readable explanation |
| `context_json` | TEXT | JSON payload for the reviewer |
| `status` | TEXT | `pending`, `approved`, `rejected`, `resolved` |
| `decision` | TEXT | Decision submitted by the reviewer |
| `decision_data_json` | TEXT | Optional JSON context from reviewer |
| `created_at` / `resolved_at` / `updated_at` | TEXT | Timestamps |

### `failure_tickets`

Tracks unexpected runtime errors. A ticket is created when a node raises any exception that is not `HITLRequestException`.

| Column | Type | Description |
|--------|------|-------------|
| `failure_ticket_id` | TEXT PK | UUID |
| `run_id` | TEXT | Parent run |
| `graph_id` | TEXT | Parent graph |
| `checkpoint_id` | TEXT FK → checkpoints | The checkpoint captured at failure time |
| `failed_node` | TEXT | The node/state that raised |
| `error_type` | TEXT | Python exception class name |
| `error_message` | TEXT | `str(exception)` |
| `error_details` | TEXT | Full traceback |
| `state_snapshot_json` | TEXT | JSON copy of state at time of failure |
| `status` | TEXT | `open`, `investigating`, `resolved` |
| `resolution` | TEXT | Admin resolution note |
| `created_at` / `resolved_at` / `updated_at` | TEXT | Timestamps |

---

## Module Reference

### `planning_lab/checkpointing.py`

The data access layer. Contains Pydantic models and all SQLite CRUD operations.

**Pydantic models**: `Checkpoint`, `HITLTask`, `FailureTicket`

**Checkpoint operations**:
- `create_checkpoint(...)` → `Checkpoint` — Persists state after a node completes; auto-increments version per `run_id`.
- `get_checkpoint(checkpoint_id)` → `Optional[Checkpoint]`
- `get_latest_checkpoint(run_id)` → `Optional[Checkpoint]` — Returns the highest-version checkpoint.
- `get_run_checkpoints(run_id)` → `List[Checkpoint]` — All checkpoints in version order.

**HITL operations**:
- `create_hitl_task(...)` → `HITLTask` — Also marks associated checkpoint as `paused_hitl`.
- `submit_hitl_decision(task_id, decision, decision_data)` → `HITLTask` — Validates and stores the decision (`approved`, `rejected`, `resolved`).
- `get_hitl_task(task_id)` → `Optional[HITLTask]`
- `get_hitl_tasks(status=None)` → `List[HITLTask]`

**Failure ticket operations**:
- `create_failure_ticket(...)` → `FailureTicket` — Also marks associated checkpoint as `failed`.
- `resolve_failure_ticket(ticket_id, resolution)` → `FailureTicket`
- `get_failure_ticket(ticket_id)` → `Optional[FailureTicket]`
- `get_failure_tickets(status=None)` → `List[FailureTicket]`

---

### `planning_lab/state_graph.py`

The execution engine.

#### Exceptions

| Exception | Raised by | Caught by |
|-----------|-----------|-----------|
| `HITLRequestException(reason, context)` | Node function | `StateGraphRunner.run()` |
| `RunPausedException` | `StateGraphRunner` | Caller / MCP client |
| `RunFailedException` | `StateGraphRunner` | Caller / MCP client |

#### `StateGraph`

```python
g = StateGraph(graph_id="my-workflow")
g.add_node("step_one", my_step_one_fn)   # fn: (state_dict) -> (next_state_name, new_state_dict)
g.add_node("step_two", my_step_two_fn)
```

Nodes return `(next_state_name, updated_state_dict)`. The reserved terminal state is `"done"`.

#### `StateGraphRunner`

```python
runner = StateGraphRunner(graph=g, run_id="run-uuid-123")
result = runner.run(initial_state="step_one", initial_data={"order_id": 99})
```

- **New run**: Pass `initial_state` and optionally `initial_data`. An initial checkpoint is created immediately.
- **Resume**: Call `runner.run()` with no arguments. The runner loads `get_latest_checkpoint(run_id)` and resumes from where it left off.
- **Completed runs**: If the latest checkpoint has `status=completed`, the final state data is returned immediately without re-execution.

---

## Key Behaviours

### Durable Checkpointing

A checkpoint is written:
1. **At the start of every new run** (after loading `initial_state`).
2. **After every successful node execution** with `status=active`.
3. **On reaching the `done` terminal state** with `status=completed`.
4. **On HITL pause** with `status=paused_hitl`.
5. **On unexpected failure** with `status=failed`.

Checkpoints are versioned (integer sequence per `run_id`), making it easy to audit the history of a run.

### Idempotency

The runner maintains a `completed_steps` list in each checkpoint. When resuming, any state whose name is already in `completed_steps` is skipped. The `_transitions` key in `state_data` records which state each completed node transitioned to, allowing the runner to correctly advance without re-executing.

### HITL Pause & Resume

1. A node raises `HITLRequestException(reason="Manager sign-off required", context={...})`.
2. The runner saves a `paused_hitl` checkpoint and creates a `hitl_tasks` row with `status=pending`.
3. `RunPausedException` propagates to the caller.
4. An admin/manager calls `submit_hitl_decision(task_id, "approved")` via MCP tool or directly.
5. The caller re-invokes `runner.run()`. The runner detects the checkpoint is `paused_hitl`, finds the resolved task, injects `_hitl_decision` into `state_data`, and re-executes the same node. The node can now inspect `state["_hitl_decision"]` to continue on the approved path.

### Failure Recovery

1. A node raises any Python exception (other than `HITLRequestException`).
2. The runner saves a `failed` checkpoint and creates a `failure_tickets` row with `status=open`.
3. `RunFailedException` propagates to the caller.
4. Calling `runner.run()` while an open ticket exists raises `RunFailedException` again (blocks re-execution).
5. An admin calls `resolve_failure_ticket(ticket_id, resolution)` via MCP tool or directly.
6. The caller re-invokes `runner.run()`. The runner resumes from the `failed` checkpoint's state and re-executes the failed node.

### Process Crash Recovery

Because all state is persisted to SQLite after **every** successful node transition, a process crash (SIGKILL, OOM, deployment restart) loses at most the in-progress execution of the current node. On restart, a new `StateGraphRunner` for the same `run_id` will automatically load the last persisted checkpoint and continue from the next un-executed node.

---

## MCP Tool Reference

Six new MCP tools are exposed via `mcp_server/server.py`. Four are read-only (any authenticated staff) and two are manager-gated writes.

| Tool | Role Required | Description |
|------|---------------|-------------|
| `get_run_status(run_id)` | Any staff | Latest checkpoint status and current state |
| `list_run_checkpoints(run_id)` | Any staff | All checkpoints for a run in order |
| `list_hitl_tasks(status?)` | Any staff | HITL tasks, optionally filtered by status |
| `approve_hitl_task(task_id, decision_data?)` | Manager | Approve a pending HITL task |
| `reject_hitl_task(task_id, decision_data?)` | Manager | Reject a pending HITL task |
| `list_failure_tickets(status?)` | Any staff | Failure tickets, optionally filtered |
| `resolve_failure(ticket_id, resolution)` | Manager | Mark a failure ticket as resolved |

---

## Testing

The test suite is in [`tests/test_checkpoint_hitl_recovery.py`](../tests/test_checkpoint_hitl_recovery.py).

Each test class uses a `test_db` fixture that:
1. Creates a fresh temporary SQLite database.
2. Applies `db/migrate_checkpoint.sql` to it.
3. Monkeypatches `planning_lab.checkpointing.DB_PATH` to point to the temp DB.

This ensures all tests are fully isolated, repeatable, and do not touch the real `copperleaf.db`.

| Test Class | Coverage |
|------------|----------|
| `TestCheckpointCRUD` | create, read, versioning, JSON round-trip, multi-run isolation |
| `TestHITLTaskLifecycle` | create, approve, reject, invalid decision, double-resolve guard |
| `TestFailureTicketLifecycle` | create, resolve, double-resolve guard, list with filter |
| `TestStateGraphHappyPath` | end-to-end run, checkpoint creation, completed status, early return |
| `TestStateGraphIdempotency` | completed steps skipped on resume |
| `TestHITLRecovery` | pause, HITL task created, checkpoint set, block on pending, resume after approval |
| `TestFailureRecovery` | failure ticket created, checkpoint set, block on open ticket, resume after resolution |
| `TestCrashRecovery` | restart with injected checkpoint, completed steps skipped, run continues |

Run the tests with:

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_checkpoint_hitl_recovery.py -v
```

To run the entire project test suite:

```bash
PYTHONPATH=. .venv/bin/pytest tests/ -v
```

---

## File Map

```
planning_lab/
  checkpointing.py       ← Pydantic models + SQLite CRUD layer
  state_graph.py         ← StateGraph, StateGraphRunner, exceptions
  __init__.py            ← Exports all public classes

mcp_server/
  tools.py               ← Checkpoint management helper functions (appended)
  server.py              ← 7 new @mcp.tool() registrations (appended)
  init_db.py             ← Runs migrate_checkpoint.sql on DB build

db/
  migrate_checkpoint.sql ← CREATE TABLE statements for checkpoints, hitl_tasks, failure_tickets

tests/
  test_checkpoint_hitl_recovery.py ← Full test suite (44 tests)

docs/
  checkpointing_recovery.md        ← This document
```
