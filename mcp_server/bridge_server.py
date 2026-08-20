import os
import sys
import uvicorn
from typing import Optional, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from langchain_mistralai import ChatMistralAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.agent import MemoryEnabledAgent

app = FastAPI(title="Copperleaf FastMCP Bridge Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. إدخال الـ API Key الحقيقي لـ Mistral هنا
MISTRAL_API_KEY = "put your api key"

# تهيئة موديل Mistral
llm = ChatMistralAI(
    api_key=MISTRAL_API_KEY, 
    model="mistral-small-latest"
)

active_agents: Dict[str, MemoryEnabledAgent] = {}

def get_or_create_agent(thread_id: str, api_token: Optional[str] = "tok_mona_mgr_9f2a") -> MemoryEnabledAgent:
    if thread_id not in active_agents:
        active_agents[thread_id] = MemoryEnabledAgent(
            stm_capacity=10,
            consolidation_batch_size=5,
            enable_rag=True,
            api_token=api_token
        )
    return active_agents[thread_id]

class ChatRequest(BaseModel):
    agent_id: str
    message: str
    thread_id: str
    api_token: Optional[str] = "tok_mona_mgr_9f2a"




from rag.agentic_rag import AgenticRAGOrchestrator
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

# إنشاء كائن الـ RAG
rag_orchestrator = AgenticRAGOrchestrator()

from rag.agentic_rag import AgenticRAGOrchestrator
from langchain_core.messages import SystemMessage, HumanMessage

# تهيئة الـ Orchestrator
rag_orchestrator = AgenticRAGOrchestrator()

@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    print(f"--- [DEBUG] Incoming Request Payload: {req.dict()} ---")
    try:
        agent = get_or_create_agent(req.thread_id, req.api_token)
        
        # 1. تنفيذ جلب البيانات مباشرة
        rag_result = rag_orchestrator.run(query=req.message)
        
        # 2. طباعة نص الـ Context لتشخيص المشكلة في الـ Terminal
        print(f"\n--- [DEBUG] RETRIEVED CONTEXT LENGTH: {len(rag_result.answer_context)} ---")
        
        # 3. إعداد الـ Prompt مع الرسائل
        messages = [
            SystemMessage(content=rag_result.answer_context),
            HumanMessage(content=req.message)
        ]
        
        # 4. استدعاء النموذج
        response = llm.invoke(messages)
        reply_text = response.content
        
        # 5. حفظ الحوار في الذاكرة
        agent.receive_message(req.message, role="user")
        agent.receive_message(reply_text, role="assistant")
        
        return {
            "status": "IDLE",
            "reply": reply_text,
            "run_id": f"run_{req.thread_id}",
            "thread_id": req.thread_id
        }
    except Exception as e:
        print(f"Error in chat endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# أضف الـ Endpoints الجديدة هنا في الأسفل مباشرة
# ==========================================

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
    return {
        "status": "COMPLETED",
        "run_id": "run_default",
        "thread_id": "thread_default",
        "error": None
    }

# ==========================================    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
