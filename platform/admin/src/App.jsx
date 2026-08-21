import React, { useEffect, useMemo, useState } from 'react';
import {
  Bot,
  Wrench,
  Database,
  UserCheck,
  AlertTriangle,
  RefreshCw,
  Plus,
  Trash2,
  Check,
  X,
  Upload,
  FileText,
  Loader2,
} from 'lucide-react';

import {
  fetchAgents,
  fetchTools,
  fetchAssignments,
  assignTool,
  removeTool,
  fetchRagDocuments,
  uploadRagDocument,
  deleteRagDocument,
  fetchHitlTasks,
  submitHitlDecision,
  fetchFailureTickets,
  fetchTicketDetails,
  investigateTicket,
  resolveTicket,
} from './services/adminService';


const TABS = [
  { id: 'agents', label: 'Agents & Tools', icon: Bot },
  { id: 'rag', label: 'RAG Documents', icon: Database },
  { id: 'hitl', label: 'HITL Tasks', icon: UserCheck },
  { id: 'tickets', label: 'Failure Tickets', icon: AlertTriangle },
];


function getAgentId(agent) {
  if (typeof agent === 'string') return agent;
  return agent.id || agent.agent_id || '';
}


function getAgentName(agent) {
  if (typeof agent === 'string') return agent;

  return (
    agent.name ||
    agent.display_name ||
    agent.agent_id ||
    agent.id ||
    'Agent'
  );
}


function getToolName(tool) {
  if (typeof tool === 'string') return tool;

  return (
    tool.tool_name ||
    tool.name ||
    tool.id ||
    ''
  );
}


function buildAssignmentSet(assignments) {
  const result = new Set();

  if (Array.isArray(assignments)) {
    assignments.forEach((item) => {
      if (typeof item === 'string') return;

      const agentId =
        item.agent_id ||
        item.agent ||
        item.id;

      const toolName =
        item.tool_name ||
        item.tool;

      if (agentId && toolName) {
        result.add(`${agentId}::${toolName}`);
      }
    });

    return result;
  }

  if (assignments && typeof assignments === 'object') {
    Object.entries(assignments).forEach(
      ([agentId, agentTools]) => {
        if (!Array.isArray(agentTools)) return;

        agentTools.forEach((tool) => {
          const toolName = getToolName(tool);

          if (toolName) {
            result.add(
              `${agentId}::${toolName}`
            );
          }
        });
      }
    );
  }

  return result;
}


function Badge({ children, type = 'gray' }) {
  const classes = {
    gray: 'bg-gray-100 text-gray-700 border-gray-200',
    green: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    amber: 'bg-amber-50 text-amber-700 border-amber-200',
    red: 'bg-red-50 text-red-700 border-red-200',
    blue: 'bg-blue-50 text-blue-700 border-blue-200',
  };

  return (
    <span
      className={`inline-flex px-2 py-1 rounded-full border text-xs font-medium ${
        classes[type] || classes.gray
      }`}
    >
      {children}
    </span>
  );
}


function EmptyState({ text }) {
  return (
    <div className="border border-dashed border-gray-300 rounded-xl p-8 text-center text-gray-500">
      {text}
    </div>
  );
}


function App() {
  const [activeTab, setActiveTab] = useState('agents');

  const [agents, setAgents] = useState([]);
  const [tools, setTools] = useState([]);
  const [assignments, setAssignments] = useState([]);

  const [documents, setDocuments] = useState([]);
  const [hitlTasks, setHitlTasks] = useState([]);
  const [tickets, setTickets] = useState([]);

  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState('');
  const [error, setError] = useState('');

  const assignmentSet = useMemo(
    () => buildAssignmentSet(assignments),
    [assignments]
  );


  const loadAgents = async () => {
    const [agentData, toolData, assignmentData] =
      await Promise.all([
        fetchAgents(),
        fetchTools(),
        fetchAssignments(),
      ]);

    setAgents(
      Array.isArray(agentData)
        ? agentData
        : agentData?.agents || []
    );

    setTools(
      Array.isArray(toolData)
        ? toolData
        : toolData?.tools || []
    );

    setAssignments(
      assignmentData?.assignments ??
      assignmentData ??
      []
    );
  };


  const loadRag = async () => {
    const data = await fetchRagDocuments();

    setDocuments(
      Array.isArray(data)
        ? data
        : data?.documents || []
    );
  };


  const loadHitl = async () => {
    const data = await fetchHitlTasks();

    setHitlTasks(
      Array.isArray(data)
        ? data
        : data?.tasks || []
    );
  };


  const loadTickets = async () => {
    const data = await fetchFailureTickets();

    setTickets(
      Array.isArray(data)
        ? data
        : data?.tickets || []
    );
  };


  const refreshCurrentTab = async () => {
    setLoading(true);
    setError('');

    try {
      if (activeTab === 'agents') {
        await loadAgents();
      }

      if (activeTab === 'rag') {
        await loadRag();
      }

      if (activeTab === 'hitl') {
        await loadHitl();
      }

      if (activeTab === 'tickets') {
        await loadTickets();
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };


  useEffect(() => {
    refreshCurrentTab();
  }, [activeTab]);


  const handleToolToggle = async (
    agentId,
    toolName,
    assigned
  ) => {
    const actionKey = `${agentId}-${toolName}`;

    setActionLoading(actionKey);
    setError('');

    try {
      if (assigned) {
        await removeTool(agentId, toolName);
      } else {
        await assignTool(agentId, toolName);
      }

      await loadAgents();
    } catch (err) {
      setError(err.message);
    } finally {
      setActionLoading('');
    }
  };


  const handleUpload = async (event) => {
    const file = event.target.files?.[0];

    if (!file) return;

    setActionLoading('rag-upload');
    setError('');

    try {
      await uploadRagDocument(file);
      await loadRag();
    } catch (err) {
      setError(err.message);
    } finally {
      event.target.value = '';
      setActionLoading('');
    }
  };


  const handleDeleteDocument = async (name) => {
    if (
      !window.confirm(
        `Delete ${name} from the RAG knowledge base?`
      )
    ) {
      return;
    }

    setActionLoading(`delete-${name}`);
    setError('');

    try {
      await deleteRagDocument(name);
      await loadRag();
    } catch (err) {
      setError(err.message);
    } finally {
      setActionLoading('');
    }
  };


  const handleHitl = async (taskId, decision) => {
    setActionLoading(`hitl-${taskId}-${decision}`);
    setError('');
    
    try {
        await submitHitlDecision(
            taskId,
            decision,
            {
                note: `${decision} from admin platform`,
                decided_by: 'admin-platform',
            }
        );
        
        await loadHitl();
    } catch (err) {
        setError(err.message);
    } finally {
        setActionLoading('');
    }
};


  const handleInvestigate = async (
    ticketId
  ) => {
    setActionLoading(
      `investigate-${ticketId}`
    );
    setError('');

    try {
      await investigateTicket(ticketId);
      await loadTickets();
    } catch (err) {
      setError(err.message);
    } finally {
      setActionLoading('');
    }
  };


  const handleInspect = async (
    ticketId
  ) => {
    setActionLoading(
      `inspect-${ticketId}`
    );

    try {
      const details =
        await fetchTicketDetails(ticketId);

      window.alert(
        JSON.stringify(
          details,
          null,
          2
        )
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setActionLoading('');
    }
  };


  const handleResolve = async (ticketId) => {
    setActionLoading(`resolve-${ticketId}`);
    setError('');
    
    try {
        await resolveTicket(
            ticketId,
            'Failure investigated and resolved by admin platform'
        );
        
        await loadTickets();
    } catch (err) {
        setError(err.message);
    } finally {
        setActionLoading('');
    }
};


  return (
    <div className="min-h-screen bg-gray-100 text-gray-900">
      <header className="bg-slate-950 text-white px-6 py-4 flex items-center justify-between shadow">
        <div className="flex items-center gap-3">
          <div className="bg-blue-600 p-2 rounded-lg">
            <Wrench className="w-5 h-5" />
          </div>

          <div>
            <h1 className="font-bold text-lg">
              Copperleaf Kitchens
            </h1>

            <p className="text-xs text-slate-400">
              Admin Platform
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Badge type="green">
            Live MCP
          </Badge>

          <button
            onClick={refreshCurrentTab}
            disabled={loading}
            className="flex items-center gap-2 text-sm bg-slate-800 hover:bg-slate-700 px-3 py-2 rounded-lg disabled:opacity-50"
          >
            <RefreshCw
              className={`w-4 h-4 ${
                loading
                  ? 'animate-spin'
                  : ''
              }`}
            />

            Refresh
          </button>
        </div>
      </header>


      <div className="flex min-h-[calc(100vh-72px)]">
        <aside className="w-64 bg-white border-r border-gray-200 p-4">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Administration
          </p>

          <div className="space-y-2">
            {TABS.map((tab) => {
              const Icon = tab.icon;
              const selected =
                activeTab === tab.id;

              return (
                <button
                  key={tab.id}
                  onClick={() =>
                    setActiveTab(tab.id)
                  }
                  className={`w-full flex items-center gap-3 px-3 py-3 rounded-xl text-sm font-medium transition ${
                    selected
                      ? 'bg-blue-50 text-blue-700 border border-blue-200'
                      : 'text-gray-600 hover:bg-gray-50 border border-transparent'
                  }`}
                >
                  <Icon className="w-4 h-4" />

                  {tab.label}
                </button>
              );
            })}
          </div>
        </aside>


        <main className="flex-1 p-6 overflow-y-auto">
          {error && (
            <div className="mb-5 bg-red-50 border border-red-200 text-red-700 rounded-xl px-4 py-3">
              <strong>Error:</strong> {error}
            </div>
          )}


          {loading ? (
            <div className="flex justify-center items-center py-20">
              <Loader2 className="w-7 h-7 animate-spin text-blue-600" />
            </div>
          ) : (
            <>
              {/* AGENTS + TOOLS */}
              {activeTab === 'agents' && (
                <section>
                  <div className="mb-6">
                    <h2 className="text-2xl font-bold">
                      Agents & MCP Tools
                    </h2>

                    <p className="text-sm text-gray-500 mt-1">
                      Add or remove tools from live agents.
                      Changes are applied to the running MCP server.
                    </p>
                  </div>

                  <div className="space-y-5">
                    {agents.length === 0 && (
                      <EmptyState text="No agents returned by the backend." />
                    )}

                    {agents.map((agent) => {
                      const agentId =
                        getAgentId(agent);

                      return (
                        <div
                          key={agentId}
                          className="bg-white border border-gray-200 rounded-2xl shadow-sm"
                        >
                          <div className="p-5 border-b border-gray-100 flex items-center justify-between">
                            <div className="flex items-center gap-3">
                              <div className="w-10 h-10 bg-slate-900 rounded-xl flex items-center justify-center">
                                <Bot className="w-5 h-5 text-blue-400" />
                              </div>

                              <div>
                                <h3 className="font-bold">
                                  {getAgentName(
                                    agent
                                  )}
                                </h3>

                                <p className="text-xs text-gray-500">
                                  {agentId}
                                </p>
                              </div>
                            </div>

                            <Badge type="green">
                              Active
                            </Badge>
                          </div>

                          <div className="p-5">
                            <p className="text-xs font-semibold text-gray-400 uppercase mb-3">
                              Tool Assignments
                            </p>

                            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                              {tools.map((tool) => {
                                const toolName =
                                  getToolName(tool);

                                const assigned =
                                  assignmentSet.has(
                                    `${agentId}::${toolName}`
                                  );

                                const actionKey =
                                  `${agentId}-${toolName}`;

                                return (
                                  <button
                                    key={toolName}
                                    disabled={
                                      actionLoading ===
                                      actionKey
                                    }
                                    onClick={() =>
                                      handleToolToggle(
                                        agentId,
                                        toolName,
                                        assigned
                                      )
                                    }
                                    className={`text-left flex items-center justify-between gap-3 px-3 py-3 rounded-xl border transition ${
                                      assigned
                                        ? 'bg-emerald-50 border-emerald-200'
                                        : 'bg-gray-50 border-gray-200 hover:border-blue-300'
                                    }`}
                                  >
                                    <span className="text-sm font-medium break-all">
                                      {toolName}
                                    </span>

                                    {actionLoading ===
                                    actionKey ? (
                                      <Loader2 className="w-4 h-4 animate-spin" />
                                    ) : assigned ? (
                                      <Check className="w-4 h-4 text-emerald-600 shrink-0" />
                                    ) : (
                                      <Plus className="w-4 h-4 text-gray-400 shrink-0" />
                                    )}
                                  </button>
                                );
                              })}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>
              )}


              {/* RAG */}
              {activeTab === 'rag' && (
                <section>
                  <div className="flex items-center justify-between mb-6">
                    <div>
                      <h2 className="text-2xl font-bold">
                        RAG Documents
                      </h2>

                      <p className="text-sm text-gray-500 mt-1">
                        Upload and remove documents from the live RAG index.
                      </p>
                    </div>

                    <label className="cursor-pointer bg-blue-600 hover:bg-blue-700 text-white px-4 py-2.5 rounded-xl text-sm font-medium flex items-center gap-2">
                      {actionLoading ===
                      'rag-upload' ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Upload className="w-4 h-4" />
                      )}

                      Upload PDF

                      <input
                        type="file"
                        accept=".pdf,application/pdf"
                        onChange={handleUpload}
                        className="hidden"
                        disabled={
                          actionLoading ===
                          'rag-upload'
                        }
                      />
                    </label>
                  </div>

                  <div className="bg-white border border-gray-200 rounded-2xl overflow-hidden shadow-sm">
                    {documents.length === 0 ? (
                      <div className="p-5">
                        <EmptyState text="No RAG documents found." />
                      </div>
                    ) : (
                      documents.map(
                        (document) => (
                          <div
                            key={
                              document.name
                            }
                            className="p-4 border-b last:border-b-0 border-gray-100 flex items-center justify-between"
                          >
                            <div className="flex items-center gap-3">
                              <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center">
                                <FileText className="w-5 h-5 text-blue-600" />
                              </div>

                              <div>
                                <p className="font-medium">
                                  {
                                    document.name
                                  }
                                </p>

                                <p className="text-xs text-gray-500">
                                  Indexed chunks:{' '}
                                  {
                                    document.indexed_chunks
                                  }
                                </p>
                              </div>
                            </div>

                            <button
                              onClick={() =>
                                handleDeleteDocument(
                                  document.name
                                )
                              }
                              className="text-red-600 hover:bg-red-50 p-2 rounded-lg"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        )
                      )
                    )}
                  </div>
                </section>
              )}


              {/* HITL */}
              {activeTab === 'hitl' && (
                <section>
                  <div className="mb-6">
                    <h2 className="text-2xl font-bold">
                      Human-in-the-Loop Tasks
                    </h2>

                    <p className="text-sm text-gray-500 mt-1">
                      Review paused graph actions and approve or reject them.
                    </p>
                  </div>

                  <div className="space-y-4">
                    {hitlTasks.length === 0 && (
                      <EmptyState text="No HITL tasks found." />
                    )}

                    {hitlTasks.map(
                      (task, index) => {
                        const taskId =
                          task.hitl_task_id ||
                          task.task_id ||
                          task.id;

                        return (
                          <div
                            key={
                              taskId ||
                              index
                            }
                            className="bg-white border border-amber-200 rounded-2xl p-5 shadow-sm"
                          >
                            <div className="flex items-start justify-between gap-4">
                              <div>
                                <div className="flex items-center gap-2 mb-2">
                                  <UserCheck className="w-5 h-5 text-amber-600" />

                                  <h3 className="font-bold">
                                    HITL Task
                                  </h3>

                                  <Badge type="amber">
                                    {task.status ||
                                      'pending'}
                                  </Badge>
                                </div>

                                <p className="text-sm text-gray-600">
                                  Run:{' '}
                                  {task.run_id ||
                                    '—'}
                                </p>

                                <p className="text-sm text-gray-600">
                                  State:{' '}
                                  {task.state_name ||
                                    task.current_state ||
                                    '—'}
                                </p>

                                <p className="text-sm text-gray-600 mt-2">
                                  {task.reason ||
                                    task.requested_action ||
                                    task.description ||
                                    'Administrative decision required.'}
                                </p>
                              </div>

                              {(!task.status ||
                                task.status ===
                                  'pending' ||
                                task.status ===
                                  'open') && (
                                <div className="flex gap-2 shrink-0">
                                  <button
                                    onClick={() =>
                                      handleHitl(
                                        taskId,
                                        'approved'
                                      )
                                    }
                                    className="bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-2 rounded-lg text-sm flex items-center gap-1"
                                  >
                                    <Check className="w-4 h-4" />
                                    Approve
                                  </button>

                                  <button
                                    onClick={() =>
                                      handleHitl(
                                        taskId,
                                        'rejected'
                                      )
                                    }
                                    className="bg-red-600 hover:bg-red-700 text-white px-3 py-2 rounded-lg text-sm flex items-center gap-1"
                                  >
                                    <X className="w-4 h-4" />
                                    Reject
                                  </button>
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      }
                    )}
                  </div>
                </section>
              )}


              {/* FAILURE TICKETS */}
              {activeTab === 'tickets' && (
                <section>
                  <div className="mb-6">
                    <h2 className="text-2xl font-bold">
                      Failure Tickets
                    </h2>

                    <p className="text-sm text-gray-500 mt-1">
                      Inspect failed runs, investigate the checkpoint and resume after resolution.
                    </p>
                  </div>

                  <div className="space-y-4">
                    {tickets.length === 0 && (
                      <EmptyState text="No failure tickets found." />
                    )}

                    {tickets.map(
                      (ticket, index) => {
                        const ticketId =
                          ticket.failure_ticket_id ||
                          ticket.ticket_id ||
                          ticket.id;

                        const status =
                          ticket.status ||
                          'open';

                        return (
                          <div
                            key={
                              ticketId ||
                              index
                            }
                            className="bg-white border border-red-200 rounded-2xl p-5 shadow-sm"
                          >
                            <div className="flex items-start justify-between gap-4">
                              <div>
                                <div className="flex gap-2 items-center mb-2">
                                  <AlertTriangle className="w-5 h-5 text-red-600" />

                                  <h3 className="font-bold">
                                    Failure Ticket
                                  </h3>

                                  <Badge
                                    type={
                                      status ===
                                      'resolved'
                                        ? 'green'
                                        : status ===
                                          'investigating'
                                        ? 'amber'
                                        : 'red'
                                    }
                                  >
                                    {status}
                                  </Badge>
                                </div>

                                <p className="text-sm text-gray-600">
                                  Ticket:{' '}
                                  {ticketId}
                                </p>

                                <p className="text-sm text-gray-600">
                                  Run:{' '}
                                  {ticket.run_id ||
                                    '—'}
                                </p>

                                <p className="text-sm text-gray-600 mt-2">
                                  {ticket.error_message ||
                                    ticket.reason ||
                                    ticket.description ||
                                    'Graph execution failure.'}
                                </p>
                              </div>

                              <div className="flex gap-2 flex-wrap justify-end">
                                <button
                                  onClick={() =>
                                    handleInspect(
                                      ticketId
                                    )
                                  }
                                  className="bg-gray-100 hover:bg-gray-200 px-3 py-2 rounded-lg text-sm"
                                >
                                  Inspect
                                </button>

                                {status ===
                                  'open' && (
                                  <button
                                    onClick={() =>
                                      handleInvestigate(
                                        ticketId
                                      )
                                    }
                                    className="bg-amber-100 hover:bg-amber-200 text-amber-800 px-3 py-2 rounded-lg text-sm"
                                  >
                                    Investigate
                                  </button>
                                )}

                                {status !==
                                  'resolved' && (
                                  <button
                                    onClick={() =>
                                      handleResolve(
                                        ticketId
                                      )
                                    }
                                    className="bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-2 rounded-lg text-sm"
                                  >
                                    Resolve & Resume
                                  </button>
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      }
                    )}
                  </div>
                </section>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  );
}

export default App;