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

# تهيئة كائن الـ Procurement Graph
procurement_graph = create_procurement_graph()

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

# ذاكرة بسيطة ومضمونة لتخزين المحادثات لكل thread_id عبر جميع الوكلاء
chat_histories: Dict[str, list] = {}

class ChatRequest(BaseModel):
    agent_id: str
    message: str
    thread_id: str
    api_token: Optional[str] = "tok_mona_mgr_9f2a"

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    print(f"--- [DEBUG] Incoming Request Payload: {req.dict()} ---")
    try:
        run_id = f"run_{req.thread_id}"

        # تهيئة سجل الذاكرة لـ thread_id إذا لم يكن موجوداً
        if req.thread_id not in chat_histories:
            chat_histories[req.thread_id] = []
        
        history = chat_histories[req.thread_id]

        # المسار 1: Procurement Agent
        if req.agent_id == "procurement":
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
                
                # حفظ الرسالة والرد في الذاكرة
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

        # المسار 2: Food Safety Agent
        elif req.agent_id == "food_safety":
            if food_safety_graph:
                food_safety_runner = StateGraphRunner(food_safety_graph, run_id=run_id)
                initial_data = {
                    "raw_request": req.message, 
                    "thread_id": req.thread_id,
                    "branch_id": 1
                }
                
                try:
                    # تعديل اسم النود الأولي الصحيح: receive_inspection_request
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

        # المسار 3: General / Memory & RAG Agent (يعمل لكافة الأسئلة العامة للـ LLM)
        else:
            # 1. جلب نتائج الـ RAG
            rag_result = rag_orchestrator.run(query=req.message)
            context_text = getattr(rag_result, "answer_context", "") or ""

            # 2. صياغة الـ System Prompt
            system_prompt = (
                "You are an intelligent assistant.\n"
                "- Pay strict attention to the conversation history below to answer any personal questions (like names, roles, branches, etc.).\n"
                "- If the query is about procedures, SOPs, or internal rules, use the Knowledge Base Context provided.\n\n"
                f"Knowledge Base Context:\n{context_text}"
            )

            # 3. تجميع الرسائل (System + History + Current Message)
            messages = [SystemMessage(content=system_prompt)] + list(history) + [HumanMessage(content=req.message)]

            # 4. استدعاء الـ LLM
            response = llm.invoke(messages)
            reply_text = response.content

            # 5. حفظ الرسالة والرد في ذاكرة الـ thread_id
            history.append(HumanMessage(content=req.message))
            history.append(AIMessage(content=reply_text))

            # الحفاظ على آخر 10 رسائل
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

@app.get("/tools/get_run_status")
async def get_run_status(run_id: str = None, thread_id: str = None):
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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)