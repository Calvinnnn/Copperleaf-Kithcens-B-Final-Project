const API_BASE_URL = 'http://127.0.0.1:8080';

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, options);

  let data = null;

  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    throw new Error(
      data?.error || `Request failed with status ${response.status}`
    );
  }

  return data;
}


// ------------------------------------------------------------------
// Agents & Tools
// ------------------------------------------------------------------

export const fetchAgents = () =>
  request('/admin/agents');


export const fetchTools = () =>
  request('/admin/tools');

export const fetchAssignments = () =>
  request('/admin/assignments');

export const assignTool = (agentId, toolName) =>
  request(
    `/admin/agents/${encodeURIComponent(agentId)}/tools/${encodeURIComponent(toolName)}`,
    {
      method: 'POST',
    }
  );


export const removeTool = (agentId, toolName) =>
  request(
    `/admin/agents/${encodeURIComponent(agentId)}/tools/${encodeURIComponent(toolName)}`,
    {
      method: 'DELETE',
    }
  );


// ------------------------------------------------------------------
// RAG Documents
// ------------------------------------------------------------------

export const fetchRagDocuments = () =>
  request('/admin/rag-documents');


export const uploadRagDocument = (file) => {
  const formData = new FormData();
  formData.append('file', file);

  return request('/admin/rag-documents', {
    method: 'POST',
    body: formData,
  });
};


export const deleteRagDocument = (filename) =>
  request(
    `/admin/rag-documents/${encodeURIComponent(filename)}`,
    {
      method: 'DELETE',
    }
  );


// ------------------------------------------------------------------
// HITL
// ------------------------------------------------------------------

export const fetchHitlTasks = (status = '') => {
  const query = status
    ? `?status=${encodeURIComponent(status)}`
    : '';

  return request(`/admin/hitl${query}`);
};


export const submitHitlDecision = (
  taskId,
  decision,
  decisionData = {}
) =>
  request(
    `/admin/hitl/${encodeURIComponent(taskId)}/decision`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        decision,
        decision_data: decisionData,
      }),
    }
  );


// ------------------------------------------------------------------
// Failure Tickets
// ------------------------------------------------------------------

export const fetchFailureTickets = (status = '') => {
  const query = status
    ? `?status=${encodeURIComponent(status)}`
    : '';

  return request(`/admin/tickets${query}`);
};


export const fetchTicketDetails = (ticketId) =>
  request(
    `/admin/tickets/${encodeURIComponent(ticketId)}`
  );


export const investigateTicket = (ticketId) =>
  request(
    `/admin/tickets/${encodeURIComponent(ticketId)}/investigate`,
    {
      method: 'POST',
    }
  );


export const resolveTicket = (ticketId, resolution) =>
  request(
    `/admin/tickets/${encodeURIComponent(ticketId)}/resolve`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        resolution,
      }),
    }
  );