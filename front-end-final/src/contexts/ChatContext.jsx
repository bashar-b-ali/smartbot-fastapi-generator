import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';
import { chatService } from '../services/api';

const ChatContext = createContext();

export const useChat = () => {
  const context = useContext(ChatContext);
  if (!context) {
    throw new Error('useChat must be used within a ChatProvider');
  }
  return context;
};

const SELECTED_MODEL_KEY = 'chat-selected-model';
const PENDING_CHAT_KEY = 'chat-pending-requests-v1';
const MIN_ASSISTANT_RESPONSE_MS = 1200;
const PENDING_TTL_MS = 2 * 60 * 60 * 1000;

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const normalizeOptionalId = (value) => {
  if (value == null) return null;
  const text = String(value).trim();
  if (!text || ['null', 'none', 'undefined', 'default', 'server-default'].includes(text.toLowerCase())) {
    return null;
  }
  return text;
};

const normalizeMessageText = (value) => String(value || '').trim().replace(/\s+/g, ' ');
const sameMessageText = (a, b) => normalizeMessageText(a) === normalizeMessageText(b);

const messageSortTime = (message) => {
  const value = new Date(message?.created_at || 0).getTime();
  return Number.isNaN(value) ? 0 : value;
};

const isLocalOnlyMessage = (message) => message?._optimistic || String(message?.id || '').startsWith('local-');

const dedupeMessages = (items) => {
  const list = Array.isArray(items) ? items.filter(Boolean) : [];
  const byId = new Map();
  list.forEach((message) => {
    if (!message?.id) return;
    const existing = byId.get(String(message.id));
    if (!existing || (isLocalOnlyMessage(existing) && !isLocalOnlyMessage(message))) {
      byId.set(String(message.id), message);
    }
  });

  const sorted = Array.from(byId.values()).sort((a, b) => messageSortTime(a) - messageSortTime(b));
  const result = [];
  sorted.forEach((message) => {
    if (message.message_type === 'user') {
      const duplicateIndex = result.findIndex(
        (existing) =>
          existing.message_type === 'user' &&
          sameMessageText(existing.content, message.content) &&
          (String(existing.session_id || '') === String(message.session_id || '') || isLocalOnlyMessage(existing) || isLocalOnlyMessage(message))
      );
      if (duplicateIndex >= 0) {
        if (isLocalOnlyMessage(result[duplicateIndex]) && !isLocalOnlyMessage(message)) {
          result[duplicateIndex] = message;
        }
        return;
      }
    }
    result.push(message);
  });
  return result;
};

const readPendingRequests = () => {
  try {
    const parsed = JSON.parse(localStorage.getItem(PENDING_CHAT_KEY) || '[]');
    if (!Array.isArray(parsed)) return [];
    const cutoff = Date.now() - PENDING_TTL_MS;
    return parsed.filter((item) => new Date(item.created_at).getTime() >= cutoff);
  } catch {
    return [];
  }
};

const writePendingRequests = (items) => {
  const next = Array.isArray(items) ? items : [];
  if (next.length) localStorage.setItem(PENDING_CHAT_KEY, JSON.stringify(next));
  else localStorage.removeItem(PENDING_CHAT_KEY);
};

const addPendingRequest = (pending) => {
  const list = readPendingRequests().filter((item) => item.id !== pending.id);
  writePendingRequests([...list, pending]);
};

const removePendingRequest = (pendingId) => {
  writePendingRequests(readPendingRequests().filter((item) => item.id !== pendingId));
};

const pendingForProject = (projectId) =>
  readPendingRequests().filter((item) => String(item.project_id) === String(projectId));

const isInterruptedRequest = (error) => {
  const status = error?.status ?? error?.original?.response?.status;
  if (status) return false;
  const code = error?.code || error?.original?.code || '';
  const name = error?.name || error?.original?.name || '';
  const message = `${error?.message || ''} ${error?.original?.message || ''}`.toLowerCase();
  return (
    code === 'ERR_CANCELED' ||
    name === 'CanceledError' ||
    message.includes('canceled') ||
    message.includes('cancelled') ||
    message.includes('abort') ||
    message.includes('network error') ||
    message.includes('failed to fetch')
  );
};

const pendingToMessage = (pending, sessionId) => ({
  id: pending.local_message_id,
  session_id: sessionId || pending.session_id || `pending-${pending.project_id}`,
  message_type: 'user',
  content: pending.message,
  tokens_used: 0,
  model_used: '',
  attachments: pending.attachments || [],
  created_at: pending.created_at,
  _optimistic: true,
  _pendingRequest: true,
});

const mergePendingMessages = (messagesData, { projectId, sessionId }) => {
  const list = dedupeMessages(Array.isArray(messagesData) ? messagesData : []);
  const pending = pendingForProject(projectId);

  if (!pending.length) return { messages: list, hasPending: false };

  const next = [...list];
  const remainingPending = [];

  pending.forEach((item) => {
    const userIndex = next.findIndex(
      (message) =>
        message.message_type === 'user' &&
        sameMessageText(message.content, item.message) &&
        (!item.session_id || !message.session_id || String(message.session_id) === String(item.session_id))
    );
    const userMessage = userIndex >= 0 ? next[userIndex] : null;
    const terminalAssistant = item.assistant_message_id
      ? next.find(
          (message) =>
            String(message.id) === String(item.assistant_message_id) &&
            message.message_type === 'assistant' &&
            message.model_used !== 'pipeline-running'
        )
      : userIndex >= 0
        ? next.slice(userIndex + 1).find(
            (message) => message.message_type === 'assistant' && message.model_used !== 'pipeline-running'
          )
        : null;

    if (userMessage && terminalAssistant) {
      removePendingRequest(item.id);
      return;
    }

    if (!userMessage) next.push(pendingToMessage(item, sessionId));
    remainingPending.push(item);
  });

  return { messages: dedupeMessages(next), hasPending: remainingPending.length > 0 };
};

export const ChatProvider = ({ children }) => {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [activeSession, setActiveSession] = useState(null);
  const [pendingRequests, setPendingRequests] = useState(() => readPendingRequests());
  const initializingByProjectRef = useRef(new Map());

  // Multi-model support
  const [models, setModels] = useState([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [selectedModelId, setSelectedModelId] = useState(
    () => normalizeOptionalId(localStorage.getItem(SELECTED_MODEL_KEY))
  );

  useEffect(() => {
    if (selectedModelId) {
      localStorage.setItem(SELECTED_MODEL_KEY, selectedModelId);
    } else {
      localStorage.removeItem(SELECTED_MODEL_KEY);
    }
  }, [selectedModelId]);

  const syncPendingRequests = useCallback(() => {
    const next = readPendingRequests();
    setPendingRequests(next);
    return next;
  }, []);

  /** Load the list of usable LLM models for the picker. */
  const loadModels = useCallback(async () => {
    setModelsLoading(true);
    try {
      const list = await chatService.listModels();
      setModels(Array.isArray(list) ? list : []);
      if (selectedModelId && Array.isArray(list) && !list.some((m) => m.id === selectedModelId)) {
        setSelectedModelId(null);
        return;
      }
      // If no model selected yet, default to the backend's is_default one.
      if (!selectedModelId && Array.isArray(list) && list.length) {
        const def = list.find((m) => m.is_default) || list[0];
        if (def) setSelectedModelId(def.id);
      }
    } catch (error) {
      console.error('Failed to load models:', error);
      setModels([]);
    } finally {
      setModelsLoading(false);
    }
  }, [selectedModelId]);

  const createSession = useCallback(async (projectId, title) => {
    try {
      const session = await chatService.createSession(projectId, { title });
      setSessions((prev) => {
        const list = Array.isArray(prev) ? prev : [];
        return [session, ...list];
      });
      setActiveSession(session);
      setMessages([]);
      return session;
    } catch (error) {
      console.error('Failed to create session:', error);
      throw error;
    }
  }, []);

  const deleteSession = useCallback(async (sessionId) => {
    try {
      await chatService.deleteSession(sessionId);
      setSessions((prev) => {
        const list = Array.isArray(prev) ? prev : [];
        return list.filter((s) => s.id !== sessionId);
      });
      if (activeSession?.id === sessionId) {
        setActiveSession(null);
        setMessages([]);
      }
    } catch (error) {
      console.error('Failed to delete session:', error);
      throw error;
    }
  }, [activeSession]);

  const loadSessions = useCallback(async (projectId) => {
    try {
      const sessionsData = await chatService.listSessions(projectId);
      setSessions(Array.isArray(sessionsData) ? sessionsData : []);
    } catch (error) {
      console.error('Failed to load sessions:', error);
      setSessions([]);
    }
  }, []);

  const loadMessages = useCallback(async (sessionId, projectId = null) => {
    try {
      if (!sessionId) {
        setMessages([]);
        setLoading(false);
        return;
      }
      const messagesData = await chatService.getMessages(sessionId);
      const merged = projectId
        ? mergePendingMessages(messagesData, { projectId, sessionId })
        : { messages: dedupeMessages(Array.isArray(messagesData) ? messagesData : []), hasPending: false };
      setMessages(merged.messages);
      setLoading(merged.hasPending);
      syncPendingRequests();
    } catch (error) {
      console.error('Failed to load messages:', error);
      const pending = projectId ? pendingForProject(projectId) : [];
      setMessages(pending.map((item) => pendingToMessage(item, sessionId)));
      setLoading(pending.length > 0);
      syncPendingRequests();
    }
  }, [syncPendingRequests]);

  const sendMessage = useCallback(async (
    projectId,
    message,
    sessionId = null,
    options = {}
  ) => {
    const existingPending = pendingForProject(projectId).find((item) => sameMessageText(item.message, message));
    if (existingPending) {
      setLoading(true);
      syncPendingRequests();
      return { success: false, pending: true, error: "This request is already running." };
    }

    const startedAt = Date.now();
    const { attachments = [], modelId } = options;
    const pendingId = `pending-chat-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const optimisticId = `local-user-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const optimisticSessionId = sessionId || activeSession?.id || `pending-${projectId}`;
    const createdAt = new Date().toISOString();
    const optimisticUserMessage = {
      id: optimisticId,
      session_id: optimisticSessionId,
      message_type: 'user',
      content: message,
      tokens_used: 0,
      model_used: '',
      attachments,
      created_at: createdAt,
      _optimistic: true,
    };

    addPendingRequest({
      id: pendingId,
      local_message_id: optimisticId,
      project_id: projectId,
      session_id: normalizeOptionalId(sessionId || activeSession?.id),
      message,
      attachments,
      model_id: normalizeOptionalId(modelId || selectedModelId),
      created_at: createdAt,
    });
    syncPendingRequests();
    setMessages((prev) => [...(Array.isArray(prev) ? prev : []), optimisticUserMessage]);
    setLoading(true);
    let keepPendingAfterCatch = false;
    try {
      const response = await chatService.sendMessage(projectId, {
        message,
        session_id: normalizeOptionalId(sessionId),
        model_id: normalizeOptionalId(modelId || selectedModelId),
        attachments,
      });

      const effectiveSessionId =
        response?.session_id || sessionId || null;

      const remainingDelay = MIN_ASSISTANT_RESPONSE_MS - (Date.now() - startedAt);
      if (remainingDelay > 0) {
        await wait(remainingDelay);
      }

      if (effectiveSessionId && !sessionId) {
        setActiveSession((curr) =>
          curr?.id === effectiveSessionId
            ? curr
            : { id: effectiveSessionId, project_id: projectId, title: 'New Chat' }
        );
      }

      setMessages((prev) => {
        const list = Array.isArray(prev) ? prev : [];
        const withoutOptimistic = list.filter(
          (m) => m.id !== optimisticId && !(m._optimistic && m.message_type === 'user' && sameMessageText(m.content, message))
        );
        const responseMessages = [];
        if (response?.user_message) responseMessages.push(response.user_message);
        else responseMessages.push({ ...optimisticUserMessage, session_id: effectiveSessionId || optimisticSessionId });
        if (response?.assistant_message) responseMessages.push(response.assistant_message);
        return dedupeMessages([...withoutOptimistic, ...responseMessages]);
      });
      if (response?.run_id) {
        removePendingRequest(pendingId);
        setLoading(false);
      } else if (response?.assistant_message?.model_used === 'pipeline-running') {
        addPendingRequest({
          id: pendingId,
          local_message_id: optimisticId,
          project_id: projectId,
          session_id: normalizeOptionalId(effectiveSessionId || sessionId || activeSession?.id),
          assistant_message_id: response.assistant_message.id,
          message,
          attachments,
          model_id: normalizeOptionalId(modelId || selectedModelId),
          created_at: response?.user_message?.created_at || createdAt,
        });
        setLoading(true);
      } else {
        removePendingRequest(pendingId);
      }
      syncPendingRequests();

      return { success: true, data: response };
    } catch (error) {
      console.error('Failed to send message:', error);
      if (isInterruptedRequest(error)) {
        keepPendingAfterCatch = true;
        setMessages((prev) => {
          const list = Array.isArray(prev) ? prev : [];
          if (list.some((m) => m.id === optimisticId)) return list;
          return [
            ...list,
            {
              ...optimisticUserMessage,
              session_id: optimisticSessionId,
              _pendingRequest: true,
            },
          ];
        });
        setLoading(true);
        syncPendingRequests();
        return {
          success: false,
          pending: true,
          error: 'The page navigation interrupted the response, but this request is still tracked.',
        };
      }
      setMessages((prev) => {
        const list = Array.isArray(prev) ? prev : [];
        const withoutOptimistic = list.filter((m) => m.id !== optimisticId);
        return [
          ...withoutOptimistic,
          {
            ...optimisticUserMessage,
            session_id: optimisticSessionId,
          },
          {
            id: `local-error-${Date.now()}-${Math.random().toString(16).slice(2)}`,
            session_id: optimisticSessionId,
            message_type: 'assistant',
            content: `The request failed before a normal assistant reply could be saved.\n\n${error.message || 'Request failed.'}`,
            tokens_used: 0,
            model_used: '',
            attachments: [],
            created_at: new Date().toISOString(),
            _localError: true,
          },
        ];
      });
      removePendingRequest(pendingId);
      syncPendingRequests();
      return { success: false, error: error.message || 'Failed to send message' };
    } finally {
      if (!keepPendingAfterCatch) setLoading(readPendingRequests().length > 0);
    }
  }, [activeSession?.id, selectedModelId, syncPendingRequests]);

  const resetProjectContext = useCallback((projectId) => {
    setActiveSession(null);
    setMessages([]);
    setSessions([]);
    setLoading(false);
    const remaining = readPendingRequests().filter((item) => String(item.project_id) !== String(projectId));
    writePendingRequests(remaining);
    setPendingRequests(remaining);
  }, []);
  const initializeChat = useCallback(async (projectId) => {
    if (!projectId) return null;
    const inFlight = initializingByProjectRef.current.get(projectId);
    if (inFlight) return inFlight;

    const request = (async () => {
      try {
        const resp = await chatService.initializeChat(projectId);
        if (resp && resp.session) {
          setActiveSession(resp.session);
          if (Array.isArray(resp.session.messages)) {
            const merged = mergePendingMessages(resp.session.messages, {
              projectId,
              sessionId: resp.session.id,
            });
            setMessages(merged.messages);
            setLoading(merged.hasPending);
            syncPendingRequests();
          }
        }
        return resp;
      } catch (error) {
        console.error('Failed to initialize chat:', error);
        const pending = pendingForProject(projectId);
        if (pending.length) {
          setMessages(pending.map((item) => pendingToMessage(item, null)));
          setLoading(true);
          syncPendingRequests();
        }
        throw error;
      } finally {
        initializingByProjectRef.current.delete(projectId);
      }
    })();

    initializingByProjectRef.current.set(projectId, request);
    return request;
  }, [syncPendingRequests]);

  const analyze = useCallback(async (projectId, data) => {
    try {
      return await chatService.analyze(projectId, data);
    } catch (error) {
      console.error('Analyze failed:', error);
      throw error;
    }
  }, []);

  const generateCode = useCallback(async (projectId, data) => {
    try {
      return await chatService.generateCode(projectId, data);
    } catch (error) {
      console.error('Generate code failed:', error);
      throw error;
    }
  }, []);

  const previewPlan = useCallback(async (projectId, data) => {
    try {
      return await chatService.previewPlan(projectId, data);
    } catch (error) {
      console.error('Preview plan failed:', error);
      throw error;
    }
  }, []);

  const generateFromPrompt = useCallback(async (projectId, data) => {
    try {
      return await chatService.generateFromPrompt(projectId, data);
    } catch (error) {
      console.error('Generate from prompt failed:', error);
      throw error;
    }
  }, []);

  /** Upload one image/file as a chat attachment. Returns the descriptor. */
  const uploadAttachment = useCallback(async (projectId, file) => {
    try {
      const att = await chatService.uploadAttachment(projectId, file);
      return att;
    } catch (error) {
      console.error('Attachment upload failed:', error);
      throw error;
    }
  }, []);

  const value = {
    messages,
    loading,
    pendingRequests,
    sessions,
    activeSession,
    setActiveSession,
    sendMessage,
    createSession,
    deleteSession,
    loadSessions,
    loadMessages,
    initializeChat,
        resetProjectContext,
    analyze,
    generateCode,
    previewPlan,
    generateFromPrompt,
    // Models
    models,
    modelsLoading,
    selectedModelId,
    setSelectedModelId,
    loadModels,
    // Attachments
    uploadAttachment,
  };

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
};
