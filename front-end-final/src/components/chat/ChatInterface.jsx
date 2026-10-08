import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import {
  Send,
  Bot,
  User as UserIcon,
  Sparkles,
  Minimize2,
  Maximize2,
  X,
  ArrowDownCircle,
  MessageCircle,
  CheckCheck,
  Cpu,
  ChevronDown,
  Check,
  FileText,
  CircleDot,
  CheckCircle2,
  AlertTriangle,
  ListChecks,
} from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { useChat } from '../../contexts/ChatContext';
import { useProjectRuns } from '../../contexts/ProjectRunContext';
import { projectService } from '../../services/api';
import { cn } from '../common/cn';
import IconButton from '../common/IconButton';
import { formatTime } from '../../utils/formatters';
import AuthImage from './AuthImage';

const PROVIDER_LABEL = {
  openai: 'OpenAI',
  anthropic: 'Claude',
  google: 'Gemini',
  ollama: 'Ollama',
  local: 'Local',
  custom: 'Custom',
};

const PROVIDER_TINT = {
  openai: 'from-emerald-500 to-teal-600',
  anthropic: 'from-orange-500 to-amber-600',
  google: 'from-sky-500 to-indigo-600',
  ollama: 'from-slate-500 to-slate-700',
  local: 'from-fuchsia-500 to-purple-600',
  custom: 'from-primary-500 to-accent-500',
};

const getAssistantState = (messages) => {
  const lastAssistant = [...messages].reverse().find((m) => m.message_type === 'assistant');
  const text = lastAssistant?.content || '';
  if (/I have enough detail/i.test(text)) {
    return { label: 'Ready to apply', tone: 'ready', icon: CheckCircle2 };
  }
  if (/Before I change files/i.test(text)) {
    return { label: 'Needs details', tone: 'needs', icon: CircleDot };
  }
  if (/Generated project files|Files:\n- (?!No files changed)/i.test(text)) {
    return { label: 'Files updated', tone: 'done', icon: CheckCircle2 };
  }
  if (/Some generated files need review|pipeline failed|request failed|Accepted:\s*no/i.test(text)) {
    return { label: 'Review needed', tone: 'warn', icon: AlertTriangle };
  }
  return { label: 'Ready', tone: 'idle', icon: Sparkles };
};

const statusToneClass = {
  idle: 'bg-surface-raised text-ink-muted border-line',
  needs: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:border-amber-500/30',
  ready: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:border-emerald-500/30',
  done: 'bg-primary-50 text-primary-700 border-primary-200 dark:bg-primary-500/10 dark:text-primary-300 dark:border-primary-500/30',
  warn: 'bg-red-50 text-red-700 border-red-200 dark:bg-red-500/10 dark:text-red-300 dark:border-red-500/30',
};

const QUICK_PROMPTS = [
  'Explain the generated API endpoints and what each route does.',
  'Review this project for missing validation, errors, and weak spots.',
  'Help me improve the selected file without changing unrelated code.',
];

const chatOpenMotion = {
  initial: { opacity: 0, x: 10, y: 8, scale: 0.985 },
  animate: { opacity: 1, x: 0, y: 0, scale: 1 },
  transition: { duration: 0.22, ease: [0.16, 1, 0.3, 1] },
  style: { transformOrigin: 'bottom right' },
};

const ChatInterface = ({
  projectId,
  isVisible = true,
  onToggleVisibility,
  onFilesChanged,
  onOpenFile,
  topContent,
}) => {
  const {
    messages,
    sendMessage,
    loading,
    loadMessages,
    initializeChat,
    activeSession,
    setActiveSession,
    models,
    selectedModelId,
    setSelectedModelId,
    loadModels,
    pendingRequests,
    resetProjectContext,
  } = useChat();
  const { isBusy: projectRunBusy, latestRun } = useProjectRuns(projectId);

  const [draft, setDraft] = useState('');
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [minimized, setMinimized] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('chat-minimized')) || false;
    } catch {
      return false;
    }
  });
  const [showScrollDown, setShowScrollDown] = useState(false);
  const [lightboxSrc, setLightboxSrc] = useState(null);
  const [requirements, setRequirements] = useState([]);
  const [repairingRequirement, setRepairingRequirement] = useState('');

  const wrapRef = useRef(null);
  const textareaRef = useRef(null);
  const lastSeenLengthRef = useRef(0);
  const stickToBottomRef = useRef(true);
  const modelMenuRef = useRef(null);
  const handledRunUpdateRef = useRef('');

  useEffect(() => {
    let cancelled = false;
    if (!projectId) return undefined;
    projectService.getRequirements(projectId)
      .then((data) => {
        if (!cancelled) setRequirements(Array.isArray(data?.requirements) ? data.requirements : []);
      })
      .catch(() => { if (!cancelled) setRequirements([]); });
    return () => { cancelled = true; };
  }, [projectId]);
  /* Persistence + chat init */
  useEffect(() => {
    localStorage.setItem('chat-minimized', JSON.stringify(minimized));
  }, [minimized]);

  useEffect(() => {
    let cancelled = false;
    if (!projectId) return;
    resetProjectContext(projectId);
    (async () => {
      try {
        const resp = await initializeChat(projectId);
        const session = resp?.session || null;
        if (!cancelled && session) {
          setActiveSession(session);
          await loadMessages(session.id, projectId);
        }
      } catch (err) {
        console.error('chat init failed', err);
      }
    })();
    return () => { cancelled = true; };
  }, [projectId, initializeChat, loadMessages, resetProjectContext, setActiveSession]);

  useEffect(() => {
    if (!loading || !activeSession?.id || !projectId) return undefined;
    const interval = window.setInterval(() => {
      loadMessages(activeSession.id, projectId);
    }, 3000);
    return () => window.clearInterval(interval);
  }, [activeSession?.id, loadMessages, loading, projectId]);

  useEffect(() => {
    if (!latestRun || !activeSession?.id) return;
    const signature = `${latestRun.run_id}:${latestRun.updated_at}:${latestRun.status}`;
    if (handledRunUpdateRef.current === signature) return;
    handledRunUpdateRef.current = signature;
    loadMessages(activeSession.id, projectId);
    const applied = (latestRun.checkpoints || []).filter((item) => item.status === 'applied');
    if (applied.length && onFilesChanged) {
      const changedFiles = applied.flatMap((item) => item.changed_files || []);
      const first = changedFiles.map((item) => typeof item === 'string' ? item : item?.path).find(Boolean);
      onFilesChanged({
        run_id: latestRun.run_id,
        view_file_path: latestRun.result?.view_file_path || first || '',
        generated_files: changedFiles,
      });
    }
  }, [activeSession?.id, latestRun, loadMessages, onFilesChanged, projectId]);

  /* Load model list once */
  useEffect(() => {
    loadModels();
  }, [loadModels]);

  /* Click-outside for model menu */
  useEffect(() => {
    const onClick = (e) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(e.target)) {
        setModelMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') {
        setModelMenuOpen(false);
        setLightboxSrc(null);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const scrollToBottom = useCallback((instant = false) => {
    const el = wrapRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: instant ? 'auto' : 'smooth' });
  }, []);

  const handleScroll = useCallback(() => {
    const el = wrapRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const atBottom = distanceFromBottom < 80;
    stickToBottomRef.current = atBottom;
    setShowScrollDown(!atBottom && messages.length > 0);
  }, [messages.length]);

  useEffect(() => {
    if (minimized) return;
    if (messages.length !== lastSeenLengthRef.current) {
      lastSeenLengthRef.current = messages.length;
      if (stickToBottomRef.current) scrollToBottom(true);
    }
  }, [messages, minimized, scrollToBottom]);

  useEffect(() => {
    if (loading && stickToBottomRef.current) scrollToBottom();
  }, [loading, scrollToBottom]);

  useEffect(() => {
    if (!minimized) {
      const t = setTimeout(() => scrollToBottom(true), 80);
      return () => clearTimeout(t);
    }
  }, [minimized, scrollToBottom]);

  const messagesArray = useMemo(
    () => (Array.isArray(messages) ? messages : []),
    [messages]
  );
  const assistantState = useMemo(() => getAssistantState(messagesArray), [messagesArray]);
  const hasVisiblePipelinePending = messagesArray.some((msg) => msg?.message_type === 'assistant' && msg?.model_used === 'pipeline-running');
  const StatusIcon = assistantState.icon;

  const autoSize = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
  }, []);

  useEffect(() => {
    if (minimized) return undefined;
    const frame = window.requestAnimationFrame(() => {
      autoSize();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [minimized, draft, autoSize]);

  /* Send */
  const submitMessage = async (textOverride) => {
    const text = (textOverride ?? draft).trim();
    if (!text) return;

    const sessionId = activeSession?.id || null;

    stickToBottomRef.current = true;

    setDraft('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';

    const result = await sendMessage(projectId, text, sessionId, {
      attachments: [],
      modelId: selectedModelId,
    });
    if (result?.success && result?.data?.generated) {
      onFilesChanged?.(result.data);
    }
  };

  const handleSubmit = async (e) => {
    e?.preventDefault?.();
    await submitMessage();
  };

  const handleKeyDown = (e) => {
    if (e.nativeEvent?.isComposing) return;
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const repairRequirement = async (requirement) => {
    if (!requirement?.requirement_id || repairingRequirement) return;
    setRepairingRequirement(requirement.requirement_id);
    try {
      await projectService.repairRequirement(projectId, requirement.requirement_id, { model_id: selectedModelId });
      const refreshed = await projectService.getRequirements(projectId);
      setRequirements(Array.isArray(refreshed?.requirements) ? refreshed.requirements : []);
    } catch (error) {
      console.error('Requirement repair failed', error);
    } finally {
      setRepairingRequirement('');
    }
  };
  const applyQuickPrompt = (text) => {
    setDraft(text);
    window.requestAnimationFrame(() => {
      textareaRef.current?.focus();
      autoSize();
    });
  };

  const selectedModel = useMemo(
    () => models.find((m) => m.id === selectedModelId) || null,
    [models, selectedModelId]
  );
  const projectPendingRequests = useMemo(
    () => (pendingRequests || [])
      .filter((item) => String(item.project_id) === String(projectId))
      .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)),
    [pendingRequests, projectId]
  );
  const latestPendingRequest = projectPendingRequests[0] || null;
  const activityLabel = projectRunBusy
    ? (latestRun?.stage || 'Working').replaceAll('_', ' ')
    : loading
      ? (latestPendingRequest ? 'Editing project' : 'Thinking')
    : assistantState.label;
  const composerPlaceholder = loading
    ? 'Type another message...'
    : 'Type a message...';

  if (!isVisible) {
    return (
      <motion.button
        onClick={onToggleVisibility}
        aria-label="Open AI assistant"
        initial={chatOpenMotion.initial}
        animate={chatOpenMotion.animate}
        transition={chatOpenMotion.transition}
        style={chatOpenMotion.style}
        className="fixed bottom-4 right-4 z-40 inline-flex h-12 w-12 items-center justify-center rounded-xl border border-line bg-surface-raised text-primary-600 shadow-pop transition-smooth hover:-translate-y-0.5 hover:border-primary-300 dark:text-primary-300"
      >
        <MessageCircle className="h-6 w-6" />
      </motion.button>
    );
  }

  const canSend = draft.trim().length > 0;

  if (minimized) {
    return (
      <motion.div
        initial={chatOpenMotion.initial}
        animate={chatOpenMotion.animate}
        transition={chatOpenMotion.transition}
        style={chatOpenMotion.style}
        className="fixed bottom-3 right-3 z-40 flex w-[calc(100%-1.5rem)] max-w-xs items-center gap-1.5 rounded-xl border border-line bg-surface-raised p-1 shadow-pop transition-all duration-300 ease-out sm:bottom-4 sm:right-4"
      >
        <button
          type="button"
          onClick={() => setMinimized(false)}
          className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-smooth duration-300 hover:bg-surface-muted"
        >
          <span className="inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-line bg-surface-muted text-primary-600 dark:text-primary-300">
            <Bot className="h-4 w-4" />
          </span>
          <span className={cn(
            'inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg border text-[10px]',
            statusToneClass[assistantState.tone]
          )}>
            <StatusIcon className="h-3 w-3" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold text-ink">Assistant</span>
            <span className="block truncate text-xs text-ink-subtle">{activityLabel}</span>
          </span>
        </button>
        <IconButton label="Expand chat" tone="ghost" size="sm" onClick={() => setMinimized(false)}>
          <Maximize2 className="h-4 w-4" />
        </IconButton>
        <IconButton label="Close chat" tone="ghost" size="sm" onClick={onToggleVisibility}>
          <X className="h-4 w-4" />
        </IconButton>
      </motion.div>
    );
  }

  return (
    <>
      <motion.div
        initial={chatOpenMotion.initial}
        animate={chatOpenMotion.animate}
        transition={chatOpenMotion.transition}
        style={chatOpenMotion.style}
        className={cn(
          'fixed z-40',
          'right-3 sm:right-4',
          'bottom-3 sm:bottom-4 h-[80dvh] max-h-[calc(100dvh-1.5rem)] w-[calc(100%-1.5rem)] sm:h-[37rem] sm:w-[27rem] lg:w-[30rem]'
        )}
      >
        <div
          className="relative flex h-full flex-col overflow-hidden rounded-xl border border-line bg-surface-raised shadow-pop transition-all duration-300 ease-out"
        >
          {/* Header */}
          <header className="relative flex items-center justify-between gap-2 border-b border-line bg-surface-raised px-2.5 py-1.5">
            <div className="flex min-w-0 items-center gap-2">
              <div className="relative inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-line bg-surface-muted text-primary-600 dark:text-primary-300">
                <Bot className="h-4 w-4" />
                <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-emerald-500 ring-2 ring-surface-raised" />
              </div>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold leading-tight text-ink">Assistant</p>
                <div className="mt-0.5 flex items-center gap-1.5 min-w-0">
                  <span className="text-[11px] text-ink-subtle truncate">
                    {selectedModel
                      ? `${PROVIDER_LABEL[selectedModel.provider] || selectedModel.provider} - ${selectedModel.model_id}`
                      : 'Server default model'}
                  </span>
                </div>
              </div>
            </div>

            <div className="flex items-center gap-1">
              {/* Model picker */}
              {(
                <div className="relative" ref={modelMenuRef}>
                  <button
                    onClick={() => setModelMenuOpen((o) => !o)}
                    aria-haspopup="menu"
                    aria-expanded={modelMenuOpen}
                    className={cn(
                      'inline-flex h-8 items-center gap-1.5 rounded-lg px-2',
                      'border border-line bg-surface-raised text-xs font-medium text-ink',
                      'hover:border-line-strong shadow-card transition-colors'
                    )}
                  >
                    <Cpu className="h-3.5 w-3.5 text-ink-subtle" />
                    <span className="hidden sm:inline truncate max-w-[7rem]">
                      {selectedModel?.name || 'Default'}
                    </span>
                    <ChevronDown className={cn('h-3 w-3 transition-transform', modelMenuOpen && 'rotate-180')} />
                  </button>
                  <AnimatePresence>
                    {modelMenuOpen && (
                      <motion.div
                        role="menu"
                        initial={{ opacity: 0, y: -6, scale: 0.97 }}
                        animate={{ opacity: 1, y: 0, scale: 1, transition: { duration: 0.16, ease: [0.16, 1, 0.3, 1] } }}
                        exit={{ opacity: 0, y: -4, scale: 0.98, transition: { duration: 0.12 } }}
                        className="absolute right-0 z-30 mt-2 max-h-72 w-64 max-w-[calc(100vw-2rem)] origin-top-right overflow-auto rounded-xl border border-line bg-surface-raised p-1.5 shadow-pop scrollbar-fancy"
                      >
                        <div className="px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-[0.16em] text-ink-subtle">
                          Choose model
                        </div>
                        {models.length === 0 ? (
                          <div className="px-3 py-3 text-xs text-ink-subtle">
                            No model options are available.
                          </div>
                        ) : (
                          models.map((m) => {
                            const active = m.id === selectedModelId;
                            const tint = PROVIDER_TINT[m.provider] || PROVIDER_TINT.custom;
                            return (
                              <button
                                key={m.id || 'server-default'}
                                role="menuitemradio"
                                aria-checked={active}
                                onClick={() => {
                                  setSelectedModelId(m.id);
                                  setModelMenuOpen(false);
                                }}
                                className={cn(
                                  'w-full flex items-center gap-2.5 px-2 py-2 rounded-lg text-left transition-colors',
                                  active ? 'bg-primary-50 dark:bg-primary-500/10' : 'hover:bg-surface-muted'
                                )}
                              >
                                <span className={cn(
                                  'inline-flex h-7 w-7 items-center justify-center rounded-lg text-white shadow-soft flex-shrink-0',
                                  'bg-gradient-to-br', tint
                                )}>
                                  <Cpu className="h-3.5 w-3.5" />
                                </span>
                                <div className="min-w-0 flex-1">
                                  <p className="text-sm font-semibold text-ink truncate flex items-center gap-1.5">
                                    {m.name}
                                    {m.is_default && (
                                      <span className="inline-flex items-center px-1.5 rounded bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 text-[9px] font-bold uppercase tracking-wide">
                                        default
                                      </span>
                                    )}
                                  </p>
                                  <p className="text-[11px] text-ink-subtle truncate">
                                    {PROVIDER_LABEL[m.provider] || m.provider} - {m.model_id}
                                  </p>
                                </div>
                                {active && <Check className="h-4 w-4 text-primary-600 dark:text-primary-300 flex-shrink-0" />}
                              </button>
                            );
                          })
                        )}
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              )}

              <IconButton
                label={minimized ? 'Expand chat' : 'Minimize chat'}
                tone="ghost"
                size="sm"
                onClick={() => setMinimized((m) => !m)}
              >
                {minimized ? <Maximize2 className="h-4 w-4" /> : <Minimize2 className="h-4 w-4" />}
              </IconButton>
              <IconButton label="Close chat" tone="ghost" size="sm" onClick={onToggleVisibility}>
                <X className="h-4 w-4" />
              </IconButton>
            </div>
          </header>

          {topContent && (
            <div className="sticky top-0 z-30 flex-shrink-0">
              {topContent}
            </div>
          )}

          {(
            <>
              {/* Messages */}
              <div
                ref={wrapRef}
                onScroll={handleScroll}
              className="flex-1 space-y-2.5 overflow-y-auto bg-surface-muted/25 px-2.5 py-2 selection:bg-primary-500/30 selection:text-ink scrollbar-fancy sm:px-3"
              >
                {messagesArray.length === 0 ? (
                  <div className="flex min-h-full items-center justify-center py-6">
                    <div className="w-full max-w-sm rounded-xl border border-line bg-surface-raised p-4 text-left shadow-card">
                      <div className="mb-3 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface-muted text-primary-600 dark:text-primary-300">
                        <Bot className="h-4 w-4" />
                      </div>
                      <h3 className="text-sm font-semibold text-ink">Work on this project</h3>
                      <p className="mt-1 text-xs leading-5 text-ink-muted">
                        Ask for docs, code review, route explanations, or focused changes. The answer stays tied to this project.
                      </p>
                      <div className="mt-3 grid gap-1.5">
                        {QUICK_PROMPTS.map((prompt) => (
                          <button
                            key={prompt}
                            type="button"
                            onClick={() => applyQuickPrompt(prompt)}
                            className="rounded-lg border border-line bg-surface-muted/60 px-2.5 py-2 text-left text-xs leading-5 text-ink-muted transition-smooth hover:border-primary-300 hover:bg-surface-raised hover:text-ink"
                          >
                            {prompt}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                ) : (
                  messagesArray.map((msg, idx) => {
                    const prev = messagesArray[idx - 1];
                    const showDateSep =
                      !prev ||
                      new Date(prev.created_at).toDateString() !==
                        new Date(msg.created_at).toDateString();
                    const sameSender = prev && prev.message_type === msg.message_type;
                    const isUser = msg.message_type === 'user';
                    return (
                      <motion.div
                        key={msg.id ?? `m-${idx}`}
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0, transition: { duration: 0.22, ease: [0.16, 1, 0.3, 1] } }}
                        exit={{ opacity: 0, y: -4, transition: { duration: 0.14 } }}
                      >
                        {showDateSep && (
                          <div className="my-3 flex items-center justify-center">
                            <span className="rounded-full border border-line bg-surface-raised px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-ink-subtle">
                              {new Date(msg.created_at).toLocaleDateString([], {
                                weekday: 'short',
                                month: 'short',
                                day: 'numeric',
                              })}
                            </span>
                          </div>
                        )}
                        <Bubble
                          msg={msg}
                          showAvatar={!sameSender}
                          isUser={isUser}
                          onZoom={(src) => setLightboxSrc(src)}
                          onOpenFile={onOpenFile}
                        />
                      </motion.div>
                    );
                  })
                )}

                {loading && !hasVisiblePipelinePending && (
                  <div className="flex animate-fade-in items-end gap-2">
                    <div className="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-line bg-surface-raised text-primary-600 dark:text-primary-300">
                      <Bot className="h-4 w-4" />
                    </div>
                    <div className="max-w-[82%] rounded-xl rounded-bl-md border border-line bg-surface-raised px-3 py-2 shadow-card">
                      <div className="flex items-center gap-2">
                        <div className="flex items-center gap-1.5">
                          <span className="h-1.5 w-1.5 rounded-full bg-primary-500 animate-bounce [animation-delay:0ms]" />
                          <span className="h-1.5 w-1.5 rounded-full bg-primary-500 animate-bounce [animation-delay:150ms]" />
                          <span className="h-1.5 w-1.5 rounded-full bg-primary-500 animate-bounce [animation-delay:300ms]" />
                        </div>
                        <span className="text-xs font-medium text-ink-muted">{activityLabel}</span>
                      </div>
                      {latestPendingRequest?.message && (
                        <p className="mt-1 line-clamp-2 break-words text-xs leading-5 text-ink-subtle">
                          {latestPendingRequest.message}
                        </p>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {showScrollDown && (
                <button
                  onClick={() => {
                    stickToBottomRef.current = true;
                    scrollToBottom();
                  }}
                  aria-label="Scroll to latest"
                  className="absolute bottom-28 right-4 inline-flex h-9 w-9 animate-fade-in items-center justify-center rounded-full bg-primary-600 text-white shadow-pop transition-colors hover:bg-primary-700"
                >
                  <ArrowDownCircle className="h-5 w-5" />
                </button>
              )}

              {requirements.some((item) => item.status === 'warning') && (
                <div className="border-t border-line bg-surface px-3 py-2">
                  <div className="mb-1 flex items-center gap-2 text-xs font-semibold text-ink">
                    <ListChecks className="h-3.5 w-3.5 text-primary-600" />
                    Requirements needing attention
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {requirements.filter((item) => item.status === 'warning').map((item) => (
                      <button
                        key={item.requirement_id}
                        type="button"
                        onClick={() => repairRequirement(item)}
                        disabled={Boolean(repairingRequirement)}
                        className="rounded-lg border border-amber-300 bg-amber-50 px-2.5 py-1.5 text-left text-[11px] text-amber-900 transition hover:border-primary-400 disabled:opacity-50"
                        title={(item.warnings || []).join(' ')}
                      >
                        <span className="block font-semibold">{item.requirement_id}: {item.description}</span>
                        <span>{repairingRequirement === item.requirement_id ? 'Repairing…' : 'Fix this requirement'}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {/* Composer */}
              <form
                onSubmit={handleSubmit}
                className="border-t border-line bg-surface-raised p-2 transition-[box-shadow,background-color] duration-300"
              >
                <div className="rounded-xl border border-line bg-surface px-3 py-2 transition-all duration-300 focus-within:border-primary-400 focus-within:bg-surface-raised focus-within:shadow-[0_10px_30px_-20px_rgba(58,99,245,0.55)]">
                  <div className="flex items-end gap-2">
                    <textarea
                      ref={textareaRef}
                      value={draft}
                      onChange={(e) => {
                        setDraft(e.target.value);
                        autoSize();
                      }}
                      onKeyDown={handleKeyDown}
                      placeholder={composerPlaceholder}
                      rows={1}
                      className="max-h-40 min-h-11 flex-1 resize-none overflow-y-auto rounded-lg border-0 bg-transparent px-0 py-1 text-sm leading-5 text-ink outline-none transition-[height,color] duration-200 placeholder:text-ink-subtle focus:ring-0 scrollbar-fancy"
                    />
                    <button
                      type="submit"
                      disabled={!canSend}
                      aria-label="Send message"
                      className={cn(
                        'mb-1 inline-flex h-9 flex-shrink-0 items-center justify-center gap-1.5 rounded-lg px-3 text-xs font-semibold text-white transition-all duration-300',
                        'bg-primary-600 shadow-card hover:-translate-y-0.5 hover:bg-primary-700 hover:shadow-pop',
                        'disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:translate-y-0 disabled:hover:bg-primary-600 disabled:hover:shadow-card'
                      )}
                    >
                      {loading ? (
                        <span className="h-4 w-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                      ) : (
                        <Send className="h-4 w-4" />
                      )}
                      <span className="hidden sm:inline">Send</span>
                    </button>
                  </div>
                </div>
              </form>
            </>
          )}

        </div>
      </motion.div>

      {/* Lightbox */}
      <AnimatePresence>
        {lightboxSrc && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setLightboxSrc(null)}
            className="fixed inset-0 z-[70] flex items-center justify-center bg-black/85 backdrop-blur-sm cursor-zoom-out p-6"
          >
            <button
              onClick={() => setLightboxSrc(null)}
              aria-label="Close preview"
              className="absolute top-4 right-4 inline-flex h-10 w-10 items-center justify-center rounded-full bg-white/15 hover:bg-white/25 text-white"
            >
              <X className="h-5 w-5" />
            </button>
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.97, opacity: 0 }}
              transition={{ type: 'spring', stiffness: 320, damping: 26 }}
              onClick={(e) => e.stopPropagation()}
              className="max-w-[90vw] max-h-[90vh]"
            >
              <AuthImage src={lightboxSrc} className="max-h-[90vh] max-w-[90vw] rounded-xl" />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
};

/* Bubble - message + attachments */
const FILE_PATH_RE = /(?:^|\s|`)([A-Za-z0-9_.@/-]+\.(?:py|md|txt|json|yaml|yml|toml|env|sql|html|css|js|jsx|ts|tsx|ini|rst))(?:`|\s|$|\))/;

const filePathFromLine = (line) => {
  const cleaned = line.replace(/^[\s*-]+/, '').trim();
  const match = cleaned.match(FILE_PATH_RE);
  return match?.[1] || '';
};

const parseDecisionBlock = (text) => {
  const raw = String(text || '');
  const match = raw.match(/\[CHAT_DECISIONS\]\s*([\s\S]*?)\s*\[\/CHAT_DECISIONS\]/);
  if (!match) return { cleanedText: raw, decisions: [] };
  return { cleanedText: raw.replace(match[0], '').trim(), decisions: [] };
};

const parseAssistantActions = (text) => {
  const { cleanedText } = parseDecisionBlock(text);
  const lines = cleanedText.split('\n');
  const visible = [];
  let inSuggestions = false;

  for (const line of lines) {
    if (/^Suggested replies:\s*$/i.test(line.trim())) {
      inSuggestions = true;
      continue;
    }
    if (inSuggestions) {
      const match = line.match(/^\s*-\s*`([^`]+)`:\s*(.+)$/);
      if (match) {
        continue;
      }
      if (line.trim() === '') {
        continue;
      }
      inSuggestions = false;
    }
    visible.push(line);
  }

  return {
    displayText: visible.join('\n').trim(),
  };
};

const MessageText = ({ text, isUser, onOpenFile }) => {
  const lines = String(text || '').split('\n');
  return (
    <div className="min-w-0 max-w-full space-y-1 overflow-hidden">
      {lines.map((line, index) => {
        const trimmed = line.trim();
        const path = !isUser ? filePathFromLine(line) : '';
        if (path && onOpenFile) {
          return (
            <button
              key={`${path}-${index}`}
              type="button"
              onClick={() => onOpenFile(path)}
              className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-line bg-surface-muted px-2 py-1 text-left font-mono text-[11px] text-primary-700 transition-colors hover:border-primary-300 hover:bg-primary-50 dark:text-primary-300 dark:hover:bg-primary-500/10"
              title={`Open ${path}`}
            >
              <FileText className="h-3 w-3 flex-shrink-0" />
              <span className="min-w-0 truncate">{line.trim()}</span>
            </button>
          );
        }
        if (/^-\s+/.test(trimmed)) {
          return (
            <div key={index} className="flex gap-2 text-[13px] leading-5">
              <span className="mt-2 h-1 w-1 flex-shrink-0 rounded-full bg-current opacity-45" />
              <span className="min-w-0 break-words [overflow-wrap:anywhere]">{trimmed.replace(/^-\s+/, '')}</span>
            </div>
          );
        }
        return (
          <p key={index} className="whitespace-pre-wrap break-words text-[13px] leading-5 [overflow-wrap:anywhere]">
            {line || '\u00a0'}
          </p>
        );
      })}
    </div>
  );
};

const SECTION_STYLE = {
  'Working summary:': { icon: ListChecks, tone: 'text-primary-600 dark:text-primary-300' },
  'Files:': { icon: FileText, tone: 'text-emerald-600 dark:text-emerald-300' },
  'Validation:': { icon: CheckCircle2, tone: 'text-emerald-600 dark:text-emerald-300' },
  'Next step:': { icon: CircleDot, tone: 'text-amber-600 dark:text-amber-300' },
  'Current understanding:': { icon: Sparkles, tone: 'text-primary-600 dark:text-primary-300' },
  'Answer only the items that affect the implementation:': { icon: ListChecks, tone: 'text-ink-muted' },
  'Implementation checkpoint:': { icon: CheckCircle2, tone: 'text-emerald-600 dark:text-emerald-300' },
  'Quest summary:': { icon: ListChecks, tone: 'text-primary-600 dark:text-primary-300' },
  'Context savings:': { icon: Cpu, tone: 'text-primary-600 dark:text-primary-300' },
  'AI semantic notes:': { icon: Sparkles, tone: 'text-primary-600 dark:text-primary-300' },
  'Requirements understood:': { icon: ListChecks, tone: 'text-primary-600 dark:text-primary-300' },
  'Plan:': { icon: ListChecks, tone: 'text-primary-600 dark:text-primary-300' },
  'Pipeline trace:': { icon: AlertTriangle, tone: 'text-amber-600 dark:text-amber-300' },
};

const splitAssistantSections = (text) => {
  const headings = Object.keys(SECTION_STYLE);
  const blocks = [];
  let current = { title: '', lines: [] };

  String(text || '').split('\n').forEach((line) => {
    if (headings.includes(line.trim())) {
      if (current.title || current.lines.some(Boolean)) blocks.push(current);
      current = { title: line.trim(), lines: [] };
      return;
    }
    current.lines.push(line);
  });
  if (current.title || current.lines.some(Boolean)) blocks.push(current);
  return blocks;
};

const AssistantMessageBody = ({ text, onOpenFile }) => {
  const blocks = splitAssistantSections(text);
  if (blocks.length <= 1 && !blocks[0]?.title) {
    return <MessageText text={text} isUser={false} onOpenFile={onOpenFile} />;
  }

  return (
    <div className="min-w-0 max-w-full space-y-1.5 overflow-hidden">
      {blocks.map((block, index) => {
        const style = SECTION_STYLE[block.title];
        const Icon = style?.icon || Sparkles;
        const body = block.lines.join('\n').trim();
        if (!block.title) {
          return <MessageText key={index} text={body} isUser={false} onOpenFile={onOpenFile} />;
        }
        return (
          <section key={`${block.title}-${index}`} className="min-w-0 max-w-full overflow-hidden rounded-lg border border-line bg-surface-muted/60 p-2">
            <div className={cn('mb-1 flex items-center gap-1.5 text-[11px] font-semibold', style?.tone)}>
              <Icon className="h-3 w-3" />
              {block.title.replace(':', '')}
            </div>
            {body ? (
              <MessageText text={body} isUser={false} onOpenFile={onOpenFile} />
            ) : (
              <p className="text-xs text-ink-subtle">No details.</p>
            )}
          </section>
        );
      })}
    </div>
  );
};

const PendingPipelineMessage = ({ text }) => {
  const action = /^\s*creating\b/i.test(text) ? 'Creating' : 'Editing';

  return (
    <span className="chat-pipeline-pending" aria-live="polite">
      <span>{action}</span>
      <span className="chat-pipeline-pending__dots" aria-hidden="true">
        <span>.</span>
        <span>.</span>
        <span>.</span>
      </span>
    </span>
  );
};
const Bubble = ({
  msg,
  showAvatar,
  isUser,
  onZoom,
  onOpenFile,
}) => {
  const text = msg.content || '';
  const { displayText } = !isUser
    ? parseAssistantActions(text)
    : { displayText: text, replies: [], decisions: [] };
  const looksLikeCode = /```|^\s*(import|class|function|def )/m.test(text);
  const isPipelinePending = !isUser && msg.model_used === 'pipeline-running';
  const attachments = Array.isArray(msg.attachments) ? msg.attachments : [];
  const images = attachments.filter((a) => a.kind === 'image');

  return (
	    <div className={cn('flex items-end gap-1.5', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser && (
        <div
          className={cn(
	            'inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg border border-line bg-surface-raised text-primary-600 dark:text-primary-300',
            showAvatar ? 'opacity-100' : 'opacity-0',
          )}
        >
	          <Bot className="h-3.5 w-3.5" />
        </div>
      )}
	      <div className={cn('flex min-w-0 max-w-[94%] flex-col gap-1 sm:max-w-[88%]', isUser ? 'items-end' : 'items-start')}>
        {/* Image grid */}
        {images.length > 0 && (
          <div
            className={cn(
	              'grid gap-1.5 overflow-hidden rounded-xl',
              images.length === 1 ? 'grid-cols-1' :
              images.length === 2 ? 'grid-cols-2' :
              'grid-cols-2'
            )}
          >
            {images.map((img, i) => (
              <AuthImage
                key={i}
                src={img.url}
                alt={img.name}
                onClick={() => onZoom(img.url)}
                className={cn(
                  'object-cover',
                  images.length === 1
	                    ? 'max-h-64'
	                    : 'h-28 w-full'
                )}
              />
            ))}
          </div>
        )}

        {/* Text bubble */}
        {displayText && (
          <div
            className={cn(
	              'max-w-full overflow-hidden px-3 py-2 text-[13px] leading-5 transition-[background-color,border-color,box-shadow,transform] duration-200',
	              isUser
	                ? 'rounded-xl rounded-br-md bg-primary-600 text-white shadow-card selection:bg-white/85 selection:text-slate-950'
	                : 'rounded-lg border border-line bg-surface-raised text-ink shadow-card selection:bg-primary-500/25 selection:text-ink hover:border-line-strong'
            )}
          >
            {isPipelinePending ? (
              <PendingPipelineMessage text={displayText} />
            ) : looksLikeCode ? (
              <pre className={cn(
	                'max-w-full overflow-hidden whitespace-pre-wrap break-words font-mono text-[11.5px] leading-5 [overflow-wrap:anywhere]',
                !isUser && 'text-ink'
              )}>{displayText}</pre>
            ) : (
              isUser
                ? <MessageText text={displayText} isUser={isUser} onOpenFile={onOpenFile} />
                : <AssistantMessageBody text={displayText} onOpenFile={onOpenFile} />
            )}
          </div>
        )}

        {/* Meta row */}
        <div className={cn(
          'flex items-center gap-1 text-[10px] text-ink-subtle',
          isUser ? 'justify-end' : 'justify-start'
        )}>
          <span>{formatTime(msg.created_at)}</span>
          {msg.model_used && !isUser && !isPipelinePending && (
            <span className="ml-1 inline-flex items-center gap-1">
              <Cpu className="h-2.5 w-2.5" />
              {msg.model_used}
            </span>
          )}
          {isUser && <CheckCheck className="h-3 w-3 text-primary-500" />}
        </div>
      </div>
      {isUser && (
        <div
          className={cn(
	            'inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-primary-600 text-white',
            showAvatar ? 'opacity-100' : 'opacity-0'
          )}
        >
	          <UserIcon className="h-3.5 w-3.5" />
        </div>
      )}
    </div>
  );
};

export default ChatInterface;
