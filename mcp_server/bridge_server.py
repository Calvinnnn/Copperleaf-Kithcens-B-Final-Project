import os
import sys
import uvicorn
import json
from typing import Optional, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from langchain_mistralai import ChatMistralAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

# 1. إعداد الـ Path للوصول للمجلدات المجاورة
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 2. تحميل المتغيرات البيئية
load_dotenv()

# 3. استيراد الـ RAG والـ Memory Agents
from agent.agent import MemoryEnabledAgent
from rag.agentic_rag import AgenticRAGOrchestrator

# 4. استيراد الـ State Graph والـ Runner والـ Exceptions
from planning_lab.state_graph import StateGraphRunner, HITLRequestException, RunPausedException
from planning_lab.procurement_graph import create_procurement_graph

# 5. استيراد الـ Maintenance Graph المباشر الخاص بك (Person 3)
# استخدام import نسبي أو استدعاء الملف مباشرة
from planning_lab.maintenance_graph import create_maintenance_graph

# تهيئة كائنات الـ Graphs
procurement_graph = create_procurement_graph()
maintenance_graph = create_maintenance_graph(fast_mode=True)

# تهيئة كائن الـ Food Safety Graph
try:
    from planning_lab.food_safety_graph import create_food_safety_graph
    food_safety_graph = create_food_safety_graph()
except ImportError:
    food_safety_graph = None

app = FastAPI(title="Copperleaf FastMCP Bridge Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "aenI105PF5QfztU1w6tSZZG4hkVRkQyy")

llm = ChatMistralAI(
    api_key=MISTRAL_API_KEY, 
    model="mistral-small-latest"
)

rag_orchestrator = AgenticRAGOrchestrator()

# ذاكرة لتخزين حالات المحادثة وتاسكات الـ HITL المعلقة للأدمن
chat_histories: Dict[str, list] = {}
pending_hitl_tasks: Dict[str, dict] = {}  # قاعدة بيانات مؤقتة للـ HITL Tasks

class ChatRequest(BaseModel):
    agent_id: str
    message: str
    thread_id: str
    api_token: Optional[str] = "tok_mona_mgr_9f2a"

class ApproveRequest(BaseModel):
    task_id: str
    decision: str  # "approved" or "rejected"

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    print(f"--- [DEBUG] Incoming Request Payload: {req.dict()} ---")
    try:
        run_id = f"run_{req.thread_id}"

        if req.thread_id not in chat_histories:
            chat_histories[req.thread_id] = []
        
        history = chat_histories[req.thread_id]

        # ==========================================
        # المسار 1: Maintenance Agent (Person 3 HITL)
        # ==========================================
        if req.agent_id in ["maintenance", "maintenance_agent"]:
            maintenance_runner = StateGraphRunner(maintenance_graph, run_id=run_id)
            
            # بناء الـ State الخاصة بـ maintenance_graph.py
            initial_data = {
                "issue_description": req.message,
                "equipment": "Gas Oven",
                "branch_id": 1,
                "estimated_parts_cost": 1500.0,  # تفترض التكلفة > 500$ لتفعيل الـ HITL
                "safety_critical": True,
                "thread_id": req.thread_id
            }

            try:
                # تشغيل الجراف بدءاً من عقدة الاستلام
                final_state = maintenance_runner.run(
                    initial_state="maintenance_request",
                    initial_data=initial_data
                )
                
                reply_text = (
                    f"Maintenance Processed Successfully:\n"
                    f"- Status: {final_state.get('status', 'COMPLETED')}\n"
                    f"- Action: {final_state.get('repair_action', 'N/A')}"
                )
                history.append(HumanMessage(content=req.message))
                history.append(AIMessage(content=reply_text))

                return {
                    "status": "IDLE",
                    "reply": reply_text,
                    "run_id": run_id,
                    "thread_id": req.thread_id
                }

            except (HITLRequestException, RunPausedException) as hitl_err:
                # عند تجاوز 500$ أو خطورة السلامة يرمي الجراف الاستثناء ويتوقف هنا!
                task_id = f"task_{req.thread_id}"
                pending_hitl_tasks[task_id] = {
                    "task_id": task_id,
                    "thread_id": req.thread_id,
                    "agent_id": "maintenance",
                    "reason": getattr(hitl_err, "reason", "MAINTENANCE_ADMIN_APPROVAL_REQUIRED"),
                    "context": getattr(hitl_err, "context", {}),
                    "initial_data": initial_data,
                    "status": "PENDING"
                }

                reply_text = "⚠️ [HITL INTERRUPT] Maintenance request requires Admin Approval ($1500 estimate / Safety Critical)."
                history.append(HumanMessage(content=req.message))
                history.append(AIMessage(content=reply_text))

                # إرجاع حالة WAITING_FOR_APPROVAL لتجميد اليوزر
                return {
                    "status": "WAITING_FOR_APPROVAL",
                    "reply": reply_text,
                    "run_id": run_id,
                    "thread_id": req.thread_id,
                    "task_id": task_id
                }

        # ==========================================
        # المسار 2: Procurement Agent
        # ==========================================
        elif req.agent_id == "procurement":
            initial_data = {
                "raw_request": req.message,
                "thread_id": req.thread_id,
                "branch_id": 1,
                "decomposed_items": [],
                "rejected_suppliers": []
            }
            procurement_runner = StateGraphRunner(procurement_graph, run_id=run_id)
            try:
                final_state = procurement_runner.run(
                    initial_state="receive_request",
                    initial_data=initial_data
                )
                po_num = final_state.get("po_number", "N/A")
                supplier = final_state.get("selected_supplier", "N/A")
                total = final_state.get("estimated_total", 0.0)
                
                reply_text = (
                    f"Procurement Order Processed Successfully:\n"
                    f"- PO Number: {po_num}\n"
                    f"- Supplier: {supplier}\n"
                    f"- Total Estimated: ${total:.2f}\n"
                    f"- Status: {final_state.get('status', 'COMPLETED')}"
                )
                history.append(HumanMessage(content=req.message))
                history.append(AIMessage(content=reply_text))

                return {
                    "status": "IDLE",
                    "reply": reply_text,
                    "run_id": run_id,
                    "thread_id": req.thread_id
                }
            except (HITLRequestException, RunPausedException) as hitl_err:
                reply_text = "Approval required for procurement order."
                history.append(HumanMessage(content=req.message))
                history.append(AIMessage(content=reply_text))
                return {
                    "status": "WAITING_FOR_APPROVAL",
                    "reply": reply_text,
                    "run_id": run_id,
                    "thread_id": req.thread_id
                }

        # ==========================================
        # المسار 3: Food Safety Agent
        # ==========================================
        elif req.agent_id == "food_safety":
            if food_safety_graph:
                food_safety_runner = StateGraphRunner(food_safety_graph, run_id=run_id)
                initial_data = {
                    "raw_request": req.message, 
                    "thread_id": req.thread_id,
                    "branch_id": 1
                }
                try:
                    final_state = food_safety_runner.run(
                        initial_state="receive_inspection_request",
                        initial_data=initial_data
                    )
                    status = final_state.get('status', 'COMPLETED')
                    compliance = final_state.get('compliance_result', 'PASSED')
                    violations = len(final_state.get('violations_found', []))
                    
                    reply_text = (
                        f"Food Safety Inspection Completed:\n"
                        f"- Compliance Result: {compliance}\n"
                        f"- Violations Found: {violations}\n"
                        f"- Status: {status}"
                    )
                    history.append(HumanMessage(content=req.message))
                    history.append(AIMessage(content=reply_text))

                    return {
                        "status": "IDLE",
                        "reply": reply_text,
                        "run_id": run_id,
                        "thread_id": req.thread_id
                    }
                except (HITLRequestException, RunPausedException) as hitl_err:
                    reply_text = f"High-risk food safety violation detected! Manager review required: {hitl_err.reason}"
                    history.append(HumanMessage(content=req.message))
                    history.append(AIMessage(content=reply_text))
                    return {
                        "status": "WAITING_FOR_APPROVAL",
                        "reply": reply_text,
                        "run_id": run_id,
                        "thread_id": req.thread_id
                    }
            else:
                reply_text = "Food safety module initialized."
                history.append(HumanMessage(content=req.message))
                history.append(AIMessage(content=reply_text))
                return {
                    "status": "IDLE",
                    "reply": reply_text,
                    "run_id": run_id,
                    "thread_id": req.thread_id
                }

        # ==========================================
        # المسار 4: General / RAG Agent
        # ==========================================
        else:
            rag_result = rag_orchestrator.run(query=req.message)
            context_text = getattr(rag_result, "answer_context", "") or ""

            system_prompt = (
                "You are an intelligent assistant.\n"
                "- Pay strict attention to the conversation history below to answer any personal questions.\n"
                "- If the query is about procedures, SOPs, or internal rules, use the Knowledge Base Context provided.\n\n"
                f"Knowledge Base Context:\n{context_text}"
            )

            messages = [SystemMessage(content=system_prompt)] + list(history) + [HumanMessage(content=req.message)]
            response = llm.invoke(messages)
            reply_text = response.content

            history.append(HumanMessage(content=req.message))
            history.append(AIMessage(content=reply_text))

            if len(history) > 10:
                chat_histories[req.thread_id] = history[-10:]

            return {
                "status": "IDLE",
                "reply": reply_text,
                "run_id": run_id,
                "thread_id": req.thread_id
            }

    except Exception as e:
        print(f"Error in chat endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# Endpoints خاصة بـ Admin Platform (HITL Management)
# ==========================================

@app.get("/admin/tasks")
async def get_admin_tasks():
    """تستدعيها شاشة الأدمن لعرض التأسكات الموقفة تنتظر موافقة"""
    tasks = [task for task in pending_hitl_tasks.values() if task["status"] == "PENDING"]
    return {"tasks": tasks}

@app.post("/admin/approve")
async def admin_approve_task(req: ApproveRequest):
    """يستدعيها زر Approve/Reject من منصة الأدمن لاستئناف الجراف"""
    if req.task_id not in pending_hitl_tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    task = pending_hitl_tasks[req.task_id]
    task["status"] = "RESOLVED"

    # تجهيز قرار الأدمن لإعادة التمرير
    resumed_data = {
        **task["initial_data"],
        "_hitl_decision": {
            "task_id": req.task_id,
            "decision": req.decision  # "approved" or "rejected"
        }
    }

    # استئناف تشغيل الجراف بعد موافقة/رفض الأدمن
    maintenance_runner = StateGraphRunner(maintenance_graph, run_id=f"run_{task['thread_id']}")
    final_state = maintenance_runner.run(
        initial_state="approval_check",
        initial_data=resumed_data
    )

    return {
        "status": "SUCCESS",
        "decision": req.decision,
        "final_state": final_state
    }

@app.get("/tools/get_run_status")
async def get_run_status(run_id: str = None, thread_id: str = None):
    # البحث إذا كان هناك تاسك معلقة لهذا الـ thread
    task_id = f"task_{thread_id}"
    if task_id in pending_hitl_tasks and pending_hitl_tasks[task_id]["status"] == "PENDING":
        return {
            "status": "WAITING_FOR_APPROVAL",
            "run_id": run_id or "run_default",
            "thread_id": thread_id or "thread_default",
            "error": None
        }
        
    return {
        "status": "COMPLETED",
        "run_id": run_id or "run_default",
        "thread_id": thread_id or "thread_default",
        "error": None
    }

@app.post("/tools/get_run_status")
async def post_run_status(payload: dict = None):
    thread_id = payload.get("thread_id") if payload else None
    run_id = payload.get("run_id") if payload else None
    return await get_run_status(run_id=run_id, thread_id=thread_id)

# ==========================================
# Endpoints خاصة بـ Admin Platform (HITL Management)
# ==========================================

@app.get("/api/hitl/pending")
@app.get("/api/pending_approvals")
@app.get("/api/admin/tasks")
async def get_pending_tasks_all_routes():
    tasks = [task for task in pending_hitl_tasks.values() if task["status"] == "PENDING"]
    return {"tasks": tasks, "data": tasks, "approvals": tasks}

@app.post("/admin/approve")
@app.post("/api/admin/approve")
async def admin_approve_task(req: ApproveRequest):
    if req.task_id not in pending_hitl_tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    task = pending_hitl_tasks[req.task_id]
    task["status"] = "RESOLVED"

    resumed_data = {
        **task["initial_data"],
        "_hitl_decision": {
            "task_id": req.task_id,
            "decision": req.decision
        }
    }

    maintenance_runner = StateGraphRunner(maintenance_graph, run_id=f"run_{task['thread_id']}")
    final_state = maintenance_runner.run(
        initial_state="approval_check",
        initial_data=resumed_data
    )

    return {
        "status": "SUCCESS",
        "decision": req.decision,
        "final_state": final_state
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)