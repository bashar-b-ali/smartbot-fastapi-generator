import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { projectRunService, webSocketService } from '../services/api';

const ProjectRunContext = createContext(null);
const ACTIVE_STATUSES = new Set([
  'queued',
  'running',
  'waiting_for_model',
  'needs_input',
  'needs_attention',
]);

const byNewest = (a, b) =>
  new Date(b?.created_at || 0).getTime() - new Date(a?.created_at || 0).getTime();

const normalizeRun = (run) => ({
  contract_version: 'project-run.v1',
  checkpoints: [],
  warnings: [],
  result: {},
  error: {},
  ...run,
  run_id: String(run?.run_id || ''),
  project_id: String(run?.project_id || ''),
});

export const ProjectRunProvider = ({ children }) => {
  const [runsByProject, setRunsByProject] = useState({});
  const socketRef = useRef(null);
  const reconnectRef = useRef(null);

  const upsertRun = useCallback((run) => {
    if (!run?.project_id || !run?.run_id) return;
    const normalized = normalizeRun(run);
    setRunsByProject((current) => {
      const projectId = normalized.project_id;
      const existing = current[projectId] || [];
      const next = [
        normalized,
        ...existing.filter((item) => String(item.run_id) !== normalized.run_id),
      ].sort(byNewest);
      return { ...current, [projectId]: next };
    });
  }, []);

  const loadRuns = useCallback(async (projectId, options = {}) => {
    if (!projectId) return [];
    const data = await projectRunService.list(projectId, options);
    const list = (Array.isArray(data) ? data : []).map(normalizeRun).sort(byNewest);
    setRunsByProject((current) => ({ ...current, [String(projectId)]: list }));
    return list;
  }, []);

  const refreshRun = useCallback(async (projectId, runId) => {
    if (!projectId || !runId) return null;
    const run = normalizeRun(await projectRunService.get(projectId, runId));
    upsertRun(run);
    return run;
  }, [upsertRun]);

  const createRun = useCallback(async (projectId, payload) => {
    const run = normalizeRun(await projectRunService.create(projectId, payload));
    upsertRun(run);
    return run;
  }, [upsertRun]);

  const retryRun = useCallback(async (projectId, runId, payload = {}) => {
    const run = normalizeRun(await projectRunService.retry(projectId, runId, payload));
    upsertRun(run);
    return run;
  }, [upsertRun]);

  const resumeRun = useCallback(async (projectId, runId, payload = {}) => {
    const run = normalizeRun(await projectRunService.resume(projectId, runId, payload));
    upsertRun(run);
    return run;
  }, [upsertRun]);

  const cancelRun = useCallback(async (projectId, runId) => {
    const run = normalizeRun(await projectRunService.cancel(projectId, runId));
    upsertRun(run);
    return run;
  }, [upsertRun]);

  useEffect(() => {
    let disposed = false;
    const connect = async () => {
      if (disposed || socketRef.current) return;
      const url = await webSocketService.projectsUrl();
      if (disposed || !url) return;
      const socket = new WebSocket(url);
      socketRef.current = socket;
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (!message?.type?.startsWith('project_run.') || !message.project_id || !message.run_id) {
            return;
          }
          refreshRun(message.project_id, message.run_id).catch(() => {});
        } catch {}
      };
      socket.onclose = (event) => {
        socketRef.current = null;
        // Clean closes happen during refresh/unmount; reconnect only after an unexpected failure.
        if (!disposed && event?.code !== 1000) reconnectRef.current = window.setTimeout(() => {
          connect().catch(() => {});
        }, 2500);
      };
      socket.onerror = () => socket.close();
    };
    connect().catch(() => {});
    return () => {
      disposed = true;
      if (reconnectRef.current) window.clearTimeout(reconnectRef.current);
      if (socketRef.current) socketRef.current.close();
      socketRef.current = null;
    };
  }, [refreshRun]);

  const value = useMemo(() => ({
    runsByProject,
    loadRuns,
    refreshRun,
    createRun,
    retryRun,
    resumeRun,
    cancelRun,
  }), [runsByProject, loadRuns, refreshRun, createRun, retryRun, resumeRun, cancelRun]);

  return <ProjectRunContext.Provider value={value}>{children}</ProjectRunContext.Provider>;
};

export const useProjectRuns = (projectId) => {
  const context = useContext(ProjectRunContext);
  if (!context) throw new Error('useProjectRuns must be used within ProjectRunProvider');
  const { loadRuns } = context;
  const key = String(projectId || '');
  const runs = context.runsByProject[key] || [];
  const activeRuns = runs.filter((run) => ACTIVE_STATUSES.has(run.status));
  const isBusy = activeRuns.some((run) =>
    ['queued', 'running', 'waiting_for_model'].includes(run.status)
  );

  useEffect(() => {
    if (!projectId) return undefined;
    loadRuns(projectId).catch(() => {});
    const interval = window.setInterval(() => {
      if (isBusy) loadRuns(projectId).catch(() => {});
    }, 3000);
    return () => window.clearInterval(interval);
  }, [projectId, loadRuns, isBusy]);

  return {
    runs,
    activeRuns,
    latestRun: runs[0] || null,
    isBusy,
    ...context,
  };
};
