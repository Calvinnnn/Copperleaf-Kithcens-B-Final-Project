/**
 * chatService.js - User Platform Communication Layer
 * Connects React UI to the real Copperleaf backend (port 8000).
 */

const API_BASE_URL = 'http://localhost:8000';

/**
 * إرسال رسالة إلى الـ Agent (LLM + RAG + Memory، أو الـ State Graph الحقيقي
 * حسب الوكيل ونوع الطلب).
 */
export const sendMessageToAgent = async (agentId, message, threadId = null) => {
  const activeThreadId = threadId || `thread_${agentId}_${Date.now()}`;

  const response = await fetch(`${API_BASE_URL}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      agent_id: agentId,
      message: message,
      thread_id: activeThreadId,
    }),
  });

  let data = null;
  try {
    data = await response.json();
  } catch (e) {
    data = null;
  }

  if (!response.ok) {
    // Surface the real backend error instead of pretending it succeeded.
    throw new Error(data?.error || `Server returned status: ${response.status}`);
  }

  return {
    status: data.status || 'IDLE', // IDLE | IN_PROGRESS | WAITING_FOR_APPROVAL | FAILED
    reply: data.reply || 'No response text returned.',
    runId: data.run_id || null,
    taskId: data.task_id || null,
    threadId: activeThreadId,
  };
};

/**
 * جلب حالة الـ Run الحالية (لـ Polling الـ HITL والـ Tickets).
 * لازم نبعت agent_id مع thread_id عشان الباك إند يقدر يبني run_id الصحيح
 * (run_{agent_id}_{thread_id}) ويرجع الحالة الحقيقية من قاعدة البيانات.
 */
export const fetchRunStatus = async (agentId, threadId) => {
  try {
    const response = await fetch(`${API_BASE_URL}/tools/get_run_status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId, thread_id: threadId }),
    });
    if (!response.ok) return null;
    return await response.json();
  } catch (err) {
    return null;
  }
};
