/**
 * chatService.js - User Platform Communication Layer
 * Connects React UI to Python FastMCP Backend (Port 8000)
 */

const API_BASE_URL = 'http://localhost:8000';

/**
 * إرسال رسالة إلى الـ Agent
 */
export const sendMessageToAgent = async (agentId, message, threadId = null) => {
  const activeThreadId = threadId || `thread_${agentId}_${Date.now()}`;

  try {
    // محاولة الاتصال بالـ SSE / HTTP Server الخاص بـ FastMCP
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

    if (!response.ok) {
      throw new Error(`Server returned status: ${response.status}`);
    }

    const data = await response.json();
    return {
      status: data.status || 'IDLE', // IDLE | IN_PROGRESS | WAITING_FOR_APPROVAL | FAILED
      reply: data.reply || data.output || 'No response text returned.',
      runId: data.run_id || null,
      threadId: activeThreadId,
    };
  } catch (error) {
    console.warn('[chatService] Backend offline or bridge missing. Using Local Controlled Fallback:', error.message);

    // Controlled Fallback لتجربة واجهة المستخدم بدون ما السيرفر يكرش
    return new Promise((resolve) => {
      setTimeout(() => {
        const lower = message.toLowerCase();
        if (lower.includes('buy') || lower.includes('approve') || lower.includes('write_off')) {
          resolve({
            status: 'WAITING_FOR_APPROVAL',
            reply: `⚠️ [${agentId}] High-risk operation detected (requires HITL sign-off).`,
            runId: `run_${Date.now()}`,
            threadId: activeThreadId,
          });
        } else if (lower.includes('fail') || lower.includes('error')) {
          resolve({
            status: 'FAILED',
            reply: `🚨 [${agentId}] Execution Error encountered! Failure ticket generated.`,
            runId: `run_${Date.now()}`,
            threadId: activeThreadId,
          });
        } else {
          resolve({
            status: 'IDLE',
            reply: `[${agentId} Response]: Received: "${message}". Processed via Thread ${activeThreadId}.`,
            runId: `run_${Date.now()}`,
            threadId: activeThreadId,
          });
        }
      }, 1000);
    });
  }
};

/**
 * جلب حالة الـ Run الحالية (لـ Polling الـ HITL والـ Tickets)
 */
export const fetchRunStatus = async (runId) => {
  try {
    const response = await fetch(`${API_BASE_URL}/tools/get_run_status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: runId }),
    });
    if (!response.ok) return null;
    return await response.json();
  } catch (err) {
    return null;
  }
};