/**
 * دالة إرسال الرسالة إلى الـ Agent
 * جاهزة للربط مع الـ Backend لاحقاً
 */
export const sendMessageToAgent = async (agentId, message) => {
  return new Promise((resolve) => {
    setTimeout(() => {
      const lower = message.toLowerCase();
      if (lower.includes('buy') || lower.includes('approve')) {
        resolve({
          status: 'WAITING_FOR_APPROVAL',
          reply: `⚠️ Purchase request requires Admin Approval (HITL Triggered).`,
        });
      } else if (lower.includes('fail') || lower.includes('error')) {
        resolve({
          status: 'FAILED',
          reply: `🚨 Tool Execution Failed! Failure Ticket created.`,
        });
      } else {
        resolve({
          status: 'IDLE',
          reply: `[Mock Response from ${agentId}]: Order/Task processed successfully!`,
        });
      }
    }, 1200);
  });
};