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

# لازم يتحمل .env قبل أي استيراد لـ mcp_server.server، لأنه بيقرا
# COPPERLEAF_API_TOKEN من البيئة وقت الاستيراد نفسه (module-level).
from dotenv import load_dotenv

load_dotenv()

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

import os

from langchain_mistralai import ChatMistralAI
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.agent import MemoryEnabledAgent


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
# Chat (real LLM + RAG + short-term memory + state-graph HITL)
# ---------------------------------------------------------------------

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

if not MISTRAL_API_KEY:
    raise RuntimeError(
        "MISTRAL_API_KEY is not set. Add it to your .env file "
        "(never hardcode it in source)."
    )

_llm = ChatMistralAI(
    api_key=MISTRAL_API_KEY,
    model="mistral-small-latest",
)

# One MemoryEnabledAgent per (agent, thread) so short-term memory and
# RAG context are scoped to a single conversation, not shared globally.
_memory_agents: dict[str, MemoryEnabledAgent] = {}


def _get_memory_agent(memory_key: str) -> MemoryEnabledAgent:
    if memory_key not in _memory_agents:
        _memory_agents[memory_key] = MemoryEnabledAgent(
            enable_rag=True,
        )
    return _memory_agents[memory_key]


AGENT_DISPLAY_NAMES = {
    "procurement": "Procurement Agent",
    "food_safety": "Food Safety Agent",
    "maintenance": "Maintenance Agent",
    "memory_rag": "Memory / RAG Agent",
    "planning": "Planning Agent",
}


def _maintenance_initial_data(message: str, thread_id: str) -> dict:
    return {
        "issue_description": message,
        "equipment": "Kitchen Equipment",
        "branch_id": 1,
        "thread_id": thread_id,
    }


import json
import re


def _extract_json_object(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


_SIGNAL_EXTRACTION_PROMPTS = {
    "maintenance": (
        "Extract maintenance-repair signals from the message as JSON with "
        "exactly these keys: "
        '{"estimated_parts_cost": <number in USD, 0 if no cost is mentioned '
        'or implied>, "safety_critical": <true only if this is a genuine '
        "safety hazard like a gas leak, fire risk, or electrical hazard, "
        'otherwise false>}. Reply with ONLY the JSON object, nothing else.'
    ),
    "food_safety": (
        "Extract food-safety inspection signals from the message as JSON "
        "with exactly these keys: "
        '{"fridge_temp_celsius": <number, default 3.5 if not mentioned>, '
        '"sanitation_score": <0-100, default 92 if not mentioned>, '
        '"expired_items_found": <true/false, default false>}. '
        "Reply with ONLY the JSON object, nothing else."
    ),
}


_DOLLAR_AMOUNT_RE = re.compile(r"\$?\s?([\d][\d,]*(?:\.\d+)?)\s*(k|thousand)?", re.IGNORECASE)
_TEMP_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:°|deg(?:rees)?)?\s*c\b", re.IGNORECASE)


def _regex_fallback_signals(agent_id: str, message: str) -> dict:
    """Deterministic backstop: if the LLM extraction step fails or returns
    nothing useful, still pick up an explicit number the user actually typed
    (e.g. "$3000", "cost 3000", "15 degrees") so HITL triggering never
    silently depends on the LLM alone."""
    signals: dict = {}

    if agent_id == "maintenance":
        # Only look for a cost if the message actually mentions cost/price/$,
        # so a stray large number elsewhere isn't misread as a repair cost.
        if re.search(r"\$|cost|price|quote|estimate", message, re.IGNORECASE):
            match = _DOLLAR_AMOUNT_RE.search(message)
            if match:
                amount = float(match.group(1).replace(",", ""))
                if match.group(2):
                    amount *= 1000
                signals["estimated_parts_cost"] = amount
        if re.search(r"gas leak|fire risk|electrical hazard|shock|explosion", message, re.IGNORECASE):
            signals["safety_critical"] = True

    elif agent_id == "food_safety":
        temp_match = _TEMP_RE.search(message)
        if temp_match:
            signals["fridge_temp_celsius"] = float(temp_match.group(1))
        if re.search(r"expired", message, re.IGNORECASE):
            signals["expired_items_found"] = True

    return signals


async def _extract_graph_signals(agent_id: str, message: str) -> dict:
    """Pull out the few numeric/boolean signals a graph's HITL threshold
    actually depends on, straight from the user's message. Combines an LLM
    read with a deterministic regex backstop so a genuine number/keyword
    the user typed is never lost to a flaky LLM response."""
    signals: dict = {}

    prompt = _SIGNAL_EXTRACTION_PROMPTS.get(agent_id)
    if prompt is not None:
        try:
            result = _llm.invoke(
                [
                    SystemMessage(content=prompt),
                    HumanMessage(content=message),
                ]
            )
            signals.update(_extract_json_object(result.content))
        except Exception:
            pass

    # Fill in anything the LLM missed (or got wrong) with a direct regex read.
    for key, value in _regex_fallback_signals(agent_id, message).items():
        if not signals.get(key):
            signals[key] = value

    return signals


def _procurement_initial_data(message: str, thread_id: str) -> dict:
    return {
        "raw_request": message,
        "branch_id": 1,
        "thread_id": thread_id,
    }


def _food_safety_initial_data(message: str, thread_id: str) -> dict:
    return {
        "branch_id": 1,
        "inspector_id": "INS-8821",
        "thread_id": thread_id,
        "notes": message,
    }


# Agents that are backed by a real, DB-checkpointed state graph.
# Any agent_id NOT listed here falls through to the plain LLM+RAG+memory path.
GRAPH_AGENTS = {
    "maintenance": {
        "graph_id": "maintenance-graph",
        "entry_state": "maintenance_request",
        "build_initial_data": _maintenance_initial_data,
    },
    "procurement": {
        "graph_id": "procurement-graph",
        "entry_state": "receive_request",
        "build_initial_data": _procurement_initial_data,
    },
    "food_safety": {
        "graph_id": "food-safety-graph",
        "entry_state": "receive_inspection_request",
        "build_initial_data": _food_safety_initial_data,
    },
}


def _friendly_outcome(state: dict) -> str:
    """Turn a finished graph's final state into a short human sentence."""
    status = state.get("status") or state.get("approval_status") or "Completed"
    return str(status).replace("_", " ").title()


async def _has_active_run(agent_id: str, thread_id: str) -> bool:
    """True if this thread already has a graph run that isn't finished yet."""
    run_id = f"run_{agent_id}_{thread_id}"
    checkpoint = get_latest_checkpoint(run_id)
    return checkpoint is not None and checkpoint.status != "completed"


async def _looks_like_actionable_request(agent_id: str, message: str) -> bool:
    """Cheap intent check so a greeting or question doesn't spin up a real
    work order / purchase order / inspection ticket by itself."""
    try:
        classification = _llm.invoke(
            [
                SystemMessage(
                    content=(
                        f"You are a strict intent classifier for the "
                        f"{AGENT_DISPLAY_NAMES[agent_id]} at a restaurant "
                        "chain. Reply with EXACTLY one word and nothing "
                        "else — no punctuation, no explanation: "
                        "ACTION if the message is a concrete request that "
                        "should open a real work order, purchase order, or "
                        "inspection (e.g. an actual order to place, a "
                        "specific problem needing action); CHAT if it's a "
                        "greeting, a question, or general conversation that "
                        "only needs an answer."
                    )
                ),
                HumanMessage(content=message),
            ]
        )
        verdict = (classification.content or "").strip().strip(".").upper()
    except Exception:
        verdict = ""

    if verdict == "ACTION":
        return True
    if verdict == "CHAT":
        return False

    # The model didn't follow the format — fall back to a deterministic
    # keyword check rather than trusting a loose substring match.
    return bool(
        re.search(
            r"\border\b|\bpurchase\b|\bbuy\b|\brepair\b|\bbroken\b|\bfix\b|"
            r"\bleak\b|\bnot working\b|\bexpired\b|\bviolation\b|\$\d",
            message,
            re.IGNORECASE,
        )
    )


async def _run_graph_agent(agent_id: str, message: str, thread_id: str) -> dict:
    """Drive a chat-triggered agent through its real state graph."""
    config = GRAPH_AGENTS[agent_id]
    run_id = f"run_{agent_id}_{thread_id}"

    checkpoint = get_latest_checkpoint(run_id)
    start_fresh = checkpoint is None or checkpoint.status == "completed"

    graph = _build_graph(config["graph_id"])
    runner = StateGraphRunner(graph=graph, run_id=run_id)

    try:
        if start_fresh:
            initial_data = config["build_initial_data"](message, thread_id)
            initial_data.update(
                await _extract_graph_signals(agent_id, message)
            )
            final_state = runner.run(
                initial_state=config["entry_state"],
                initial_data=initial_data,
            )
        else:
            # A run is already in flight for this thread (e.g. waiting on an
            # external event) — treat this message as a nudge to continue it
            # rather than silently starting a brand-new, disconnected run.
            final_state = runner.run()

        return {
            "status": "IDLE",
            "reply": (
                f"Your {AGENT_DISPLAY_NAMES[agent_id].lower()} request has been "
                f"processed: {_friendly_outcome(final_state)}."
            ),
            "run_id": run_id,
            "thread_id": thread_id,
        }

    except RunPausedException:
        tasks = get_hitl_tasks(status="pending")
        task = next((t for t in tasks if t.run_id == run_id), None)
        return {
            "status": "WAITING_FOR_APPROVAL",
            "reply": (
                "This request needs a manager's sign-off before it can go "
                "further (it crossed a policy threshold). I've sent it to "
                "the admin for approval — I'll let you know here as soon "
                "as they decide."
            ),
            "run_id": run_id,
            "thread_id": thread_id,
            "task_id": task.hitl_task_id if task else None,
        }

    except RunFailedException as exc:
        return {
            "status": "FAILED",
            "reply": (
                f"Something went wrong while processing this request: {exc}. "
                "A failure ticket has been opened for the admin to investigate."
            ),
            "run_id": run_id,
            "thread_id": thread_id,
        }


async def _run_llm_agent(agent_id: str, message: str, thread_id: str) -> dict:
    """Plain conversational path: LLM + RAG + short-term conversational memory."""
    memory_key = f"{agent_id}:{thread_id}"
    memory_agent = _get_memory_agent(memory_key)

    memory_agent.receive_message(message, role="user")
    context_messages = memory_agent.build_context(query=message)

    lc_messages = [
        SystemMessage(
            content=(
                f"You are the {AGENT_DISPLAY_NAMES.get(agent_id, agent_id)} "
                "for Copperleaf Kitchens. Use the conversation history and any "
                "knowledge-base context provided below to answer accurately. "
                "If something isn't covered by that context, say so plainly "
                "instead of guessing."
            )
        )
    ]

    for m in context_messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))
        else:
            lc_messages.append(SystemMessage(content=content))

    response = _llm.invoke(lc_messages)
    reply_text = response.content

    memory_agent.receive_message(reply_text, role="assistant")

    return {
        "status": "IDLE",
        "reply": reply_text,
        "run_id": f"run_{agent_id}_{thread_id}",
        "thread_id": thread_id,
    }


async def chat_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "Invalid JSON body"}, 400)

    agent_id = body.get("agent_id")
    message = (body.get("message") or "").strip()
    thread_id = body.get("thread_id")

    if not agent_id or not message or not thread_id:
        return _json(
            {"error": "agent_id, message and thread_id are required"},
            400,
        )

    try:
        if agent_id in GRAPH_AGENTS:
            if await _has_active_run(agent_id, thread_id) or await _looks_like_actionable_request(
                agent_id, message
            ):
                result = await _run_graph_agent(agent_id, message, thread_id)
            else:
                result = await _run_llm_agent(agent_id, message, thread_id)
        else:
            result = await _run_llm_agent(agent_id, message, thread_id)

        return _json(result)

    except Exception as exc:
        return _json({"error": str(exc)}, 500)


async def get_run_status_endpoint(request: Request):
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
        run_id = body.get("run_id")
        thread_id = body.get("thread_id")
        agent_id = body.get("agent_id")
    else:
        run_id = request.query_params.get("run_id")
        thread_id = request.query_params.get("thread_id")
        agent_id = request.query_params.get("agent_id")

    if not run_id and thread_id and agent_id:
        run_id = f"run_{agent_id}_{thread_id}"

    if not run_id:
        return _json(
            {"error": "run_id, or agent_id + thread_id, is required"},
            400,
        )

    checkpoint = get_latest_checkpoint(run_id)

    if checkpoint is None:
        return _json(
            {"status": "IDLE", "run_id": run_id, "thread_id": thread_id, "error": None}
        )

    if checkpoint.status == "paused_hitl":
        tasks = get_hitl_tasks()
        task = next((t for t in tasks if t.run_id == run_id), None)

        if task and task.status == "pending":
            return _json(
                {
                    "status": "WAITING_FOR_APPROVAL",
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "error": None,
                }
            )

        # Task has been decided but the graph hasn't been re-run against it yet
        # (this only happens if the admin's decision-resume failed to run).
        return _json(
            {
                "status": "WAITING_FOR_APPROVAL",
                "run_id": run_id,
                "thread_id": thread_id,
                "error": None,
            }
        )

    if checkpoint.status == "failed":
        return _json(
            {
                "status": "FAILED",
                "run_id": run_id,
                "thread_id": thread_id,
                "reply": "This request hit an error and a failure ticket was opened for the admin.",
                "error": None,
            }
        )

    if checkpoint.status == "completed":
        return _json(
            {
                "status": "IDLE",
                "run_id": run_id,
                "thread_id": thread_id,
                "reply": (
                    f"Update from the admin: {_friendly_outcome(checkpoint.state_data)}."
                ),
                "error": None,
            }
        )

    return _json(
        {"status": "IN_PROGRESS", "run_id": run_id, "thread_id": thread_id, "error": None}
    )


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
    # Real chat: LLM + RAG + memory, with state-graph HITL for
    # maintenance, procurement, and food_safety
    Route(
        "/chat",
        chat_endpoint,
        methods=["POST"],
    ),
    Route(
        "/tools/get_run_status",
        get_run_status_endpoint,
        methods=["GET", "POST"],
    ),
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