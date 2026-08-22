import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Bot, MessageSquare, Send, CheckCircle2, User, AlertTriangle, Loader2, AlertCircle, Trash2 } from 'lucide-react';
import { sendMessageToAgent, fetchRunStatus } from './services/chatService';

const AGENTS = [
  {
    id: 'procurement',
    name: 'Procurement Agent',
    description: 'Handles supplier orders & stock decomposition',
    status: 'Ready',
  },
  {
    id: 'food_safety',
    name: 'Food Safety Agent',
    description: 'Monitors compliance & safety policies',
    status: 'Ready',
  },
  {
    id: 'maintenance',
    name: 'Maintenance Agent',
    description: 'Diagnoses equipment & schedules repairs',
    status: 'Ready',
  },
  {
    id: 'memory_rag',
    name: 'Memory / RAG Agent',
    description: 'Retrieves historical docs & recipe specs',
    status: 'Ready',
  },
  {
    id: 'planning',
    name: 'Planning Agent',
    description: 'Decomposes complex multi-step kitchen tasks',
    status: 'Ready',
  },
];

function App() {
  const [selectedAgent, setSelectedAgent] = useState(AGENTS[0]);
  const [inputMessage, setInputMessage] = useState('');

  // Run status is tracked PER AGENT, not as one global value — otherwise
  // switching agents and coming back would forget that a run is still
  // genuinely paused waiting on the admin.
  const [runStatuses, setRunStatuses] = useState(() => {
    const initial = {};
    AGENTS.forEach((a) => { initial[a.id] = 'IDLE'; });
    return initial;
  });
  const runStatus = runStatuses[selectedAgent.id] || 'IDLE';

  const setRunStatusFor = (agentId, status) => {
    setRunStatuses((prev) => ({ ...prev, [agentId]: status }));
  };

  // 1. Thread IDs Persistence per Agent
  const [threadIds, setThreadIds] = useState(() => {
    const saved = localStorage.getItem('copperleaf_threads');
    if (saved) {
      try { return JSON.parse(saved); } catch (e) {}
    }
    return {
      procurement: 'thread_procurement_1',
      food_safety: 'thread_food_safety_1',
      maintenance: 'thread_maintenance_1',
      memory_rag: 'thread_memory_rag_1',
      planning: 'thread_planning_1',
    };
  });

  useEffect(() => {
    localStorage.setItem('copperleaf_threads', JSON.stringify(threadIds));
  }, [threadIds]);

  // 2. Chat History Persistence
  const [conversations, setConversations] = useState(() => {
    const saved = localStorage.getItem('copperleaf_chats');
    if (saved) {
      try {
        return JSON.parse(saved);
      } catch (e) {}
    }
    return {
      procurement: [{ id: 1, sender: 'agent', text: 'Hello! How can I assist you with procurement today?' }],
      food_safety: [{ id: 1, sender: 'agent', text: 'Food Safety Agent active. Ask me about food safety protocols.' }],
      maintenance: [{ id: 1, sender: 'agent', text: 'Maintenance Agent online. Do you need equipment diagnostics?' }],
      memory_rag: [{ id: 1, sender: 'agent', text: 'RAG Agent ready. I can search past documents and recipes.' }],
      planning: [{ id: 1, sender: 'agent', text: 'Planning Agent here. Let’s decompose complex kitchen operations.' }],
    };
  });

  useEffect(() => {
    localStorage.setItem('copperleaf_chats', JSON.stringify(conversations));
  }, [conversations]);

  const appendAgentMessage = useCallback((agentId, text) => {
    setConversations((prev) => ({
      ...prev,
      [agentId]: [
        ...(prev[agentId] || []),
        { id: Date.now() + Math.random(), sender: 'agent', text },
      ],
    }));
  }, []);

  // Avoid appending the same resolution message twice if a poll fires
  // again before the effect re-checks runStatus.
  const lastHandledResolution = useRef({});

  // Whenever the selected agent (or its thread) changes, re-sync its real
  // status from the backend — this is what makes a still-paused run stay
  // locked even after switching agents and coming back.
  useEffect(() => {
    let cancelled = false;
    const agentId = selectedAgent.id;
    const activeThreadId = threadIds[agentId];

    (async () => {
      const statusData = await fetchRunStatus(agentId, activeThreadId);
      if (cancelled || !statusData || !statusData.status) return;
      setRunStatusFor(agentId, statusData.status);
    })();

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAgent.id]);

  // Polling loop for the currently selected agent while its run is
  // paused/in-progress. When it resolves, push the admin's decision (or
  // failure) into the chat and unlock the input.
  useEffect(() => {
    if (runStatus !== 'WAITING_FOR_APPROVAL' && runStatus !== 'IN_PROGRESS') {
      return undefined;
    }

    const agentId = selectedAgent.id;
    const activeThreadId = threadIds[agentId];

    const intervalId = setInterval(async () => {
      const statusData = await fetchRunStatus(agentId, activeThreadId);
      if (!statusData || !statusData.status) return;

      if (statusData.status !== runStatuses[agentId]) {
        setRunStatusFor(agentId, statusData.status);

        // The run left WAITING_FOR_APPROVAL/IN_PROGRESS — tell the user
        // what happened, once.
        if (statusData.status !== 'WAITING_FOR_APPROVAL' && statusData.status !== 'IN_PROGRESS') {
          const resolutionKey = `${agentId}:${activeThreadId}:${statusData.status}`;
          if (lastHandledResolution.current[resolutionKey] !== true && statusData.reply) {
            lastHandledResolution.current[resolutionKey] = true;
            appendAgentMessage(agentId, statusData.reply);
          }
        }
      }
    }, 3000);

    return () => clearInterval(intervalId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runStatus, selectedAgent.id, threadIds]);

  const currentMessages = conversations[selectedAgent.id] || [];

  // 3. Send Message Handler
  const handleSendMessage = async (e) => {
    e.preventDefault();

    // منع الإرسال تماماً إذا كان الـ Agent شغال أو منتظر موافقة الأدمن
    if (!inputMessage.trim() || runStatus === 'IN_PROGRESS' || runStatus === 'WAITING_FOR_APPROVAL') {
      return;
    }

    const agentId = selectedAgent.id;
    const userMsg = {
      id: Date.now(),
      sender: 'user',
      text: inputMessage,
    };

    const activeThreadId = threadIds[agentId] || `thread_${agentId}_1`;

    setConversations((prev) => ({
      ...prev,
      [agentId]: [...(prev[agentId] || []), userMsg],
    }));

    const currentText = inputMessage;
    setInputMessage('');
    setRunStatusFor(agentId, 'IN_PROGRESS');

    try {
      const response = await sendMessageToAgent(agentId, currentText, activeThreadId);
      setRunStatusFor(agentId, response.status);
      appendAgentMessage(agentId, response.reply);
    } catch (error) {
      setRunStatusFor(agentId, 'FAILED');
      appendAgentMessage(agentId, `⚠️ Couldn't reach the backend: ${error.message}`);
    }
  };

  // 4. Clear History & Start New Thread
  const handleClearHistory = () => {
    if (window.confirm(`Are you sure you want to clear chat history for ${selectedAgent.name}?`)) {
      const agentId = selectedAgent.id;
      const newThreadId = `thread_${agentId}_${Date.now()}`;

      setThreadIds((prev) => ({
        ...prev,
        [agentId]: newThreadId,
      }));

      setConversations((prev) => ({
        ...prev,
        [agentId]: [
          { id: Date.now(), sender: 'agent', text: `Chat cleared. New session started for ${selectedAgent.name}.` },
        ],
      }));
      setRunStatusFor(agentId, 'IDLE');
    }
  };

  return (
    <div className="flex h-screen bg-gray-100 font-sans">
      {/* 1. Sidebar - Agents List */}
      <aside className="w-80 bg-white border-r border-gray-200 flex flex-col">
        <div className="p-4 border-b border-gray-200 flex items-center gap-2 bg-slate-900 text-white">
          <Bot className="w-6 h-6 text-blue-400" />
          <h1 className="font-bold text-lg">Copperleaf Kitchens</h1>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-2">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider px-2 my-2">
            Select Active Agent
          </p>

          {AGENTS.map((agent) => {
            const isSelected = selectedAgent.id === agent.id;
            const agentStatus = runStatuses[agent.id] || 'IDLE';
            return (
              <div
                key={agent.id}
                onClick={() => setSelectedAgent(agent)}
                className={`p-3 rounded-xl cursor-pointer transition-all border ${
                  isSelected
                    ? 'bg-blue-50 border-blue-500 shadow-sm'
                    : 'bg-white border-gray-100 hover:border-gray-300 hover:bg-gray-50'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <MessageSquare className={`w-4 h-4 ${isSelected ? 'text-blue-600' : 'text-gray-400'}`} />
                    <p className={`text-sm font-semibold ${isSelected ? 'text-blue-900' : 'text-gray-700'}`}>
                      {agent.name}
                    </p>
                  </div>
                  {agentStatus === 'WAITING_FOR_APPROVAL' ? (
                    <span className="flex items-center gap-1 text-[10px] font-medium text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full border border-amber-200">
                      <AlertTriangle className="w-3 h-3" />
                      Waiting
                    </span>
                  ) : agentStatus === 'FAILED' ? (
                    <span className="flex items-center gap-1 text-[10px] font-medium text-red-700 bg-red-50 px-2 py-0.5 rounded-full border border-red-200">
                      <AlertCircle className="w-3 h-3" />
                      Failed
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-[10px] font-medium text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full border border-emerald-200">
                      <CheckCircle2 className="w-3 h-3" />
                      {agent.status}
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-500 line-clamp-1">{agent.description}</p>
              </div>
            );
          })}
        </div>
      </aside>

      {/* 2. Main Chat Area */}
      <main className="flex-1 flex flex-col bg-gray-50">
        {/* Header */}
        <header className="h-16 bg-white border-b border-gray-200 px-6 flex items-center justify-between shadow-sm">
          <button
            onClick={handleClearHistory}
            title="Clear Chat History"
            className="flex items-center gap-1 bg-red-50 hover:bg-red-100 text-red-600 px-3 py-1 rounded-full border border-red-200 text-xs font-medium transition-colors cursor-pointer"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>Clear Chat</span>
          </button>
          <div>
            <h2 className="font-bold text-gray-800 text-base">{selectedAgent.name}</h2>
            <p className="text-xs text-gray-500">{selectedAgent.description}</p>
          </div>

          <div className="w-24" />
        </header>

        {/* 3. Status Banner */}
        {runStatus === 'IN_PROGRESS' && (
          <div className="bg-blue-50 border-b border-blue-200 px-6 py-2.5 flex items-center gap-2 text-xs font-medium text-blue-700">
            <Loader2 className="w-4 h-4 animate-spin text-blue-600" />
            <span>State Graph Executing... Agent is reasoning and processing tools.</span>
          </div>
        )}

        {runStatus === 'WAITING_FOR_APPROVAL' && (
          <div className="bg-amber-50 border-b border-amber-200 px-6 py-2.5 flex items-center justify-between text-xs font-medium text-amber-800">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-amber-600" />
              <span><strong>Graph Paused:</strong> High-risk operation requires Admin approval (Human-in-the-loop).</span>
            </div>
            <span className="bg-amber-100 text-amber-900 px-2 py-0.5 rounded border border-amber-300 text-[10px]">Action Required on Admin Platform</span>
          </div>
        )}

        {runStatus === 'FAILED' && (
          <div className="bg-red-50 border-b border-red-200 px-6 py-2.5 flex items-center justify-between text-xs font-medium text-red-800">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-600" />
              <span><strong>Execution Error:</strong> Graph crashed. A Failure Ticket has been opened for admin review.</span>
            </div>
            <span className="bg-red-100 text-red-900 px-2 py-0.5 rounded border border-red-300 text-[10px]">Ticket Created</span>
          </div>
        )}

        {/* 4. Messages Feed */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {currentMessages.map((msg) => {
            const isUser = msg.sender === 'user';
            return (
              <div
                key={msg.id}
                className={`flex gap-3 max-w-2xl ${isUser ? 'ml-auto flex-row-reverse' : ''}`}
              >
                <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${
                  isUser ? 'bg-blue-600 text-white' : 'bg-slate-800 text-blue-400'
                }`}>
                  {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
                </div>

                <div className={`p-4 rounded-2xl text-sm shadow-sm border ${
                  isUser
                    ? 'bg-blue-600 text-white border-blue-600 rounded-tr-none'
                    : 'bg-white text-gray-800 border-gray-200 rounded-tl-none'
                }`}>
                  <p className="whitespace-pre-wrap">{msg.text}</p>
                </div>
              </div>
            );
          })}
        </div>

        {/* 5. Input Form */}
        <form onSubmit={handleSendMessage} className="p-4 bg-white border-t border-gray-200">
          <div className="flex gap-2 max-w-4xl mx-auto">
            <input
              type="text"
              value={inputMessage}
              disabled={runStatus === 'IN_PROGRESS' || runStatus === 'WAITING_FOR_APPROVAL'}
              onChange={(e) => setInputMessage(e.target.value)}
              placeholder={
                runStatus === 'WAITING_FOR_APPROVAL'
                  ? "🔒 Chat paused: Waiting for Admin approval..."
                  : runStatus === 'IN_PROGRESS'
                  ? "Processing message..."
                  : `Message ${selectedAgent.name}...`
              }
              className="flex-1 border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100 disabled:text-gray-400 disabled:cursor-not-allowed"
            />
            <button
              type="submit"
              disabled={runStatus === 'IN_PROGRESS' || runStatus === 'WAITING_FOR_APPROVAL'}
              className="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2.5 rounded-xl font-medium text-sm flex items-center justify-center gap-2 transition-colors disabled:bg-gray-300 disabled:cursor-not-allowed"
            >
              <Send className="w-4 h-4" />
              Send
            </button>
          </div>
        </form>
      </main>
    </div>
  );
}

export default App;
