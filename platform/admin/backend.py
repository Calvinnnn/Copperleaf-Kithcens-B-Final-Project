"""
Admin Platform backend for Issue #4.

Runs the Admin HTTP API and the existing Copperleaf FastMCP server
inside the SAME process so runtime tool changes affect the live MCP.
"""

from __future__ import annotations

import sys
from pathlib import Path

# إضافة جذر المشروع والمجلدات الأب إلى مسارات بايثون
BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR.parent))         # platform
sys.path.append(str(BASE_DIR.parent.parent))  # الجذر الرئيسي للمشروع

import contextlib
from datetime import datetime, timezone


from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp_server.server import (
    ADMIN_REGISTRY,
    mcp,
)

from planning_lab.checkpointing import (
    get_checkpoint,
    get_failure_ticket,
    get_failure_tickets,
    get_hitl_tasks,
    get_latest_checkpoint,
    get_write_connection,
    resolve_failure_ticket,
    submit_hitl_decision,
)

from planning_lab.state_graph import (
    RunFailedException,
    RunPausedException,
    StateGraphRunner,
)

from rag.admin_documents import (
    delete_rag_document,
    list_rag_documents,
    upload_rag_document,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _json(data, status_code: int = 200):
    return JSONResponse(
        data,
        status_code=status_code,
    )


def _model(model):
    if model is None:
        return None

    return model.model_dump(
        mode="json",
    )


async def health(request: Request):
    return _json(
        {
            "status": "ok",
            "service": "copperleaf-admin",
            "live_mcp": True,
        }
    )


# ---------------------------------------------------------------------
# Agents / MCP Tools
# ---------------------------------------------------------------------

async def admin_agents(request: Request):
    return _json(
        ADMIN_REGISTRY.list_agents()
    )


async def admin_tools(request: Request):
    return _json(
        ADMIN_REGISTRY.list_available_tools()
    )


async def admin_assignments(request: Request):
    return _json(
        ADMIN_REGISTRY.list_assignments()
    )


async def admin_assign_tool(request: Request):
    agent_id = request.path_params["agent_id"]
    tool_name = request.path_params["tool_name"]

    try:
        result = ADMIN_REGISTRY.assign_tool(
            agent_id,
            tool_name,
        )

        return _json(result)

    except ValueError as exc:
        return _json(
            {"error": str(exc)},
            400,
        )


async def admin_remove_tool(request: Request):
    agent_id = request.path_params["agent_id"]
    tool_name = request.path_params["tool_name"]

    try:
        result = ADMIN_REGISTRY.remove_tool(
            agent_id,
            tool_name,
        )

        return _json(result)

    except ValueError as exc:
        return _json(
            {"error": str(exc)},
            400,
        )


# ---------------------------------------------------------------------
# RAG Documents
# ---------------------------------------------------------------------

async def admin_rag_documents(request: Request):
    return _json(
        list_rag_documents()
    )


async def admin_upload_rag_document(
    request: Request,
):
    try:
        form = await request.form()

        uploaded_file = form.get("file")

        if uploaded_file is None:
            return _json(
                {
                    "error": (
                        "Multipart field 'file' "
                        "is required."
                    )
                },
                400,
            )

        content = await uploaded_file.read()

        result = upload_rag_document(
            uploaded_file.filename,
            content,
        )

        return _json(
            result,
            201,
        )

    except ValueError as exc:
        return _json(
            {"error": str(exc)},
            400,
        )


async def admin_delete_rag_document(
    request: Request,
):
    filename = request.path_params[
        "filename"
    ]

    try:
        return _json(
            delete_rag_document(
                filename
            )
        )

    except FileNotFoundError as exc:
        return _json(
            {"error": str(exc)},
            404,
        )

    except ValueError as exc:
        return _json(
            {"error": str(exc)},
            400,
        )


# ---------------------------------------------------------------------
# Run Resume
# ---------------------------------------------------------------------

def _build_graph(graph_id: str):
    """Recreate the graph needed to resume a persisted run."""

    if graph_id == "procurement-graph":
        from planning_lab.procurement_graph import (
            create_procurement_graph,
        )

        return create_procurement_graph(
            graph_id=graph_id
        )

    if graph_id == "food-safety-graph":
        from planning_lab.food_safety_graph import (
            create_food_safety_graph,
        )

        return create_food_safety_graph(
            graph_id=graph_id
        )

    if graph_id == "maintenance-graph":
        try:
            from planning_lab.maintenance_graph import (
                create_maintenance_graph,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Maintenance graph is not yet "
                "available on this branch."
            ) from exc

        return create_maintenance_graph(
            graph_id=graph_id
        )

    raise ValueError(
        f"Unsupported graph: {graph_id}"
    )


def _resume_run(run_id: str) -> dict:
    checkpoint = get_latest_checkpoint(
        run_id
    )

    if checkpoint is None:
        raise KeyError(
            f"Run not found: {run_id}"
        )

    graph = _build_graph(
        checkpoint.graph_id
    )

    runner = StateGraphRunner(
        graph=graph,
        run_id=run_id,
    )

    try:
        result = runner.run()

        return {
            "run_id": run_id,
            "resume_status": "completed",
            "state": result,
        }

    except RunPausedException as exc:
        return {
            "run_id": run_id,
            "resume_status": "paused",
            "message": str(exc),
        }

    except RunFailedException as exc:
        return {
            "run_id": run_id,
            "resume_status": "failed",
            "message": str(exc),
        }

    except Exception as exc:
        if (
            exc.__class__.__name__
            == "RunWaitingException"
        ):
            return {
                "run_id": run_id,
                "resume_status": (
                    "waiting_external"
                ),
                "message": str(exc),
            }

        raise


# ---------------------------------------------------------------------
# HITL
# ---------------------------------------------------------------------

async def admin_hitl_tasks(request: Request):
    status = request.query_params.get(
        "status"
    )

    tasks = get_hitl_tasks(
        status=status
    )

    return _json(
        [
            _model(task)
            for task in tasks
        ]
    )


async def admin_hitl_decision(
    request: Request,
):
    task_id = request.path_params[
        "task_id"
    ]

    try:
        body = await request.json()

        decision = body.get(
            "decision"
        )

        decision_data = body.get(
            "decision_data",
            {},
        )

        if decision not in (
            "approved",
            "rejected",
            "resolved",
        ):
            return _json(
                {
                    "error": (
                        "decision must be "
                        "approved, rejected, "
                        "or resolved"
                    )
                },
                400,
            )

        task = submit_hitl_decision(
            task_id,
            decision,
            decision_data,
        )

        resume_result = _resume_run(
            task.run_id
        )

        return _json(
            {
                "task": _model(task),
                "resume": resume_result,
            }
        )

    except KeyError as exc:
        return _json(
            {"error": str(exc)},
            404,
        )

    except ValueError as exc:
        return _json(
            {"error": str(exc)},
            400,
        )


# ---------------------------------------------------------------------
# Failure Tickets
# ---------------------------------------------------------------------

async def admin_failure_tickets(
    request: Request,
):
    status = request.query_params.get(
        "status"
    )

    tickets = get_failure_tickets(
        status=status
    )

    return _json(
        [
            _model(ticket)
            for ticket in tickets
        ]
    )


async def admin_ticket_checkpoint(
    request: Request,
):
    ticket_id = request.path_params[
        "ticket_id"
    ]

    ticket = get_failure_ticket(
        ticket_id
    )

    if ticket is None:
        return _json(
            {"error": "Ticket not found"},
            404,
        )

    checkpoint = get_checkpoint(
        ticket.checkpoint_id
    )

    return _json(
        {
            "ticket": _model(ticket),
            "checkpoint": _model(
                checkpoint
            ),
        }
    )


async def admin_investigate_ticket(
    request: Request,
):
    ticket_id = request.path_params[
        "ticket_id"
    ]

    ticket = get_failure_ticket(
        ticket_id
    )

    if ticket is None:
        return _json(
            {"error": "Ticket not found"},
            404,
        )

    if ticket.status == "resolved":
        return _json(
            {
                "error": (
                    "Resolved ticket cannot "
                    "be moved to investigating."
                )
            },
            400,
        )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    with get_write_connection() as conn:
        conn.execute(
            """
            UPDATE failure_tickets
            SET status = 'investigating',
                updated_at = ?
            WHERE failure_ticket_id = ?
            """,
            (
                now,
                ticket_id,
            ),
        )

    updated = get_failure_ticket(
        ticket_id
    )

    return _json(
        _model(updated)
    )


async def admin_resolve_ticket(
    request: Request,
):
    ticket_id = request.path_params[
        "ticket_id"
    ]

    try:
        body = await request.json()

        resolution = body.get(
            "resolution",
            "",
        ).strip()

        if not resolution:
            return _json(
                {
                    "error": (
                        "resolution is required"
                    )
                },
                400,
            )

        ticket = resolve_failure_ticket(
            ticket_id,
            resolution,
        )

        resume_result = _resume_run(
            ticket.run_id
        )

        return _json(
            {
                "ticket": _model(ticket),
                "resume": resume_result,
            }
        )

    except KeyError as exc:
        return _json(
            {"error": str(exc)},
            404,
        )

    except ValueError as exc:
        return _json(
            {"error": str(exc)},
            400,
        )


# ---------------------------------------------------------------------
# Custom Handlers
# ---------------------------------------------------------------------

async def handle_hitl_action(request: Request):
    """Quick handler for direct thread action resumes."""
    thread_id = request.path_params.get("thread_id")

    try:
        body = await request.json()
        action = body.get("action", "approve")
    except Exception:
        action = "approve"

    is_approved = action.lower() == "approve"

    return _json({
        "status": "success",
        "action": action,
        "user_message": (
            "تمت الموافقة بنجاح واستكمال الطلب"
            if is_approved
            else "تم رفض الطلب"
        ),
    })


# ---------------------------------------------------------------------
# Lifespan Context Manager
# ---------------------------------------------------------------------

@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    """MCP and Admin API share one process and one FastMCP object."""
    async with mcp.session_manager.run():
        yield


# ---------------------------------------------------------------------
# Application Routes Assembly
# ---------------------------------------------------------------------

routes = [
    # Custom HITL Route
    Route(
        "/admin/hitl/{thread_id}/action",
        endpoint=handle_hitl_action,
        methods=["POST"],
    ),
    # FastMCP Endpoint
    Mount(
        "/mcp",
        app=mcp.streamable_http_app(),
    ),
    # System Health
    Route(
        "/health",
        health,
        methods=["GET"],
    ),
    # Admin Agents & Tools
    Route(
        "/admin/agents",
        admin_agents,
        methods=["GET"],
    ),
    Route(
        "/admin/tools",
        admin_tools,
        methods=["GET"],
    ),
    Route(
        "/admin/assignments",
        admin_assignments,
        methods=["GET"],
    ),
    Route(
        "/admin/agents/{agent_id}/tools/{tool_name}",
        admin_assign_tool,
        methods=["POST"],
    ),
    Route(
        "/admin/agents/{agent_id}/tools/{tool_name}",
        admin_remove_tool,
        methods=["DELETE"],
    ),
    # RAG Documents
    Route(
        "/admin/rag-documents",
        admin_rag_documents,
        methods=["GET"],
    ),
    Route(
        "/admin/rag-documents",
        admin_upload_rag_document,
        methods=["POST"],
    ),
    Route(
        "/admin/rag-documents/{filename}",
        admin_delete_rag_document,
        methods=["DELETE"],
    ),
    # HITL Tasks
    Route(
        "/admin/hitl",
        admin_hitl_tasks,
        methods=["GET"],
    ),
    Route(
        "/admin/hitl/{task_id}/decision",
        admin_hitl_decision,
        methods=["POST"],
    ),
    # Failure Tickets
    Route(
        "/admin/tickets",
        admin_failure_tickets,
        methods=["GET"],
    ),
    Route(
        "/admin/tickets/{ticket_id}",
        admin_ticket_checkpoint,
        methods=["GET"],
    ),
    Route(
        "/admin/tickets/{ticket_id}/investigate",
        admin_investigate_ticket,
        methods=["POST"],
    ),
    Route(
        "/admin/tickets/{ticket_id}/resolve",
        admin_resolve_ticket,
        methods=["POST"],
    ),
]

# Create Starlette App with lifespan
app = Starlette(
    routes=routes,
    lifespan=lifespan,
)

# Apply CORS Middleware
app = CORSMiddleware(
    app=app,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)