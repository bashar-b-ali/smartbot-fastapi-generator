import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000/api/v1';
const API_ORIGIN = API_BASE_URL.replace(/\/api\/v1\/?$/, '');
const SESSION_KEY = 'session_token';
const REFRESH_KEY = 'session_refresh_token';

const PUBLIC_ENDPOINT_FRAGMENTS = [
  '/auth/login',
  '/auth/register',
  '/auth/verify-email',
  '/auth/resend-verification',
  '/auth/request-password-reset',
  '/auth/confirm-password-reset',
  '/auth/refresh',
];

const PUBLIC_PATHS = ['/', '/intro', '/login', '/register', '/forgot-password'];
const TOKEN_REFRESH_LEEWAY_SECONDS = 60;

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
});

let refreshPromise = null;

const decodeJwtPayload = (token) => {
  try {
    const [, payload] = String(token || '').split('.');
    if (!payload) return null;
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    return JSON.parse(window.atob(padded));
  } catch {
    return null;
  }
};

const tokenExpiresSoon = (token) => {
  const payload = decodeJwtPayload(token);
  if (!payload?.exp) return false;
  return payload.exp - Math.floor(Date.now() / 1000) <= TOKEN_REFRESH_LEEWAY_SECONDS;
};

const refreshAccessToken = async (refreshToken) => {
  refreshPromise =
    refreshPromise ||
    axios
      .post(`${API_BASE_URL}/auth/refresh`, { refresh_token: refreshToken }, { timeout: 5000 })
      .finally(() => {
        refreshPromise = null;
      });
  const refreshed = await refreshPromise;
  const accessToken = refreshed.data?.access_token;
  if (!accessToken) return null;
  localStorage.setItem(SESSION_KEY, accessToken);
  if (refreshed.data?.refresh_token) {
    localStorage.setItem(REFRESH_KEY, refreshed.data.refresh_token);
  }
  return accessToken;
};

// WebSocket handshakes do not pass through Axios interceptors. Always resolve
// a current access token before constructing a WebSocket URL.
export const getValidAccessToken = async () => {
  const token = localStorage.getItem(SESSION_KEY);
  const refreshToken = localStorage.getItem(REFRESH_KEY);
  if (token && !tokenExpiresSoon(token)) return token;
  if (!refreshToken) return null;
  try {
    return await refreshAccessToken(refreshToken);
  } catch {
    clearStoredSession();
    return null;
  }
};

api.interceptors.request.use(async (config) => {
  const token = localStorage.getItem(SESSION_KEY);
  const refreshToken = localStorage.getItem(REFRESH_KEY);
  const url = config.url || '';
  let accessToken = token;

  if ((!accessToken || tokenExpiresSoon(accessToken)) && refreshToken && !isPublicEndpoint(url)) {
    try {
      accessToken = await refreshAccessToken(refreshToken);
    } catch {
      accessToken = token;
    }
  }

  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`;
  return config;
});

const isPublicEndpoint = (url = '') =>
  PUBLIC_ENDPOINT_FRAGMENTS.some((ep) => url.includes(ep));

const isPublicPage = () => PUBLIC_PATHS.includes(window.location.pathname);

const clearStoredSession = () => {
  localStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(REFRESH_KEY);
};

const redirectToLogin = () => {
  if (!isPublicPage()) window.location.assign('/login');
};

const extractErrorMessage = (data, fallback) => {
  if (!data) return fallback;
  if (typeof data.error === 'string') return data.error;
  if (data.error && typeof data.error.message === 'string') return data.error.message;
  if (typeof data.detail === 'string') return data.detail;
  if (Array.isArray(data.detail) && data.detail[0]?.msg) return data.detail[0].msg;
  if (typeof data.message === 'string') return data.message;
  return fallback;
};

const extractErrorCode = (response) => {
  const data = response?.data;
  if (data?.error && typeof data.error.code === 'string') return data.error.code;
  if (typeof data?.code === 'string') return data.code;
  const authHeader = response?.headers?.['www-authenticate'] || response?.headers?.['WWW-Authenticate'];
  const match = /error="([^"]+)"/.exec(authHeader || '');
  return match?.[1] || '';
};

const isExpiredAuthError = (response) => {
  if (extractErrorCode(response) === 'token_expired') return true;
  const message = extractErrorMessage(response?.data, '').toLowerCase();
  return message.includes('token expired') || message.includes('jwt expired');
};

api.interceptors.response.use(
  (response) => response.data,
  async (error) => {
    const status = error.response?.status;
    const url = error.config?.url || '';
    const originalRequest = error.config || {};

    if (
      status === 401 &&
      isExpiredAuthError(error.response) &&
      !originalRequest._retry &&
      !isPublicEndpoint(url)
    ) {
      const refreshToken = localStorage.getItem(REFRESH_KEY);
      if (refreshToken) {
        originalRequest._retry = true;
        try {
          const accessToken = await refreshAccessToken(refreshToken);
          if (accessToken) {
            originalRequest.headers = {
              ...(originalRequest.headers || {}),
              Authorization: `Bearer ${accessToken}`,
            };
            return api(originalRequest);
          }
        } catch {
          clearStoredSession();
          redirectToLogin();
        }
      }
    }

    if ((status === 401 || status === 403) && !isPublicEndpoint(url)) {
      clearStoredSession();
      redirectToLogin();
    }

    const normalized = {
      status,
      message: extractErrorMessage(error.response?.data, error.message || 'Something went wrong'),
      data: error.response?.data,
      original: error,
    };
    return Promise.reject(normalized);
  }
);

const unwrapList = (data, ...keys) => {
  if (Array.isArray(data)) return data;
  for (const k of keys) {
    if (data && Array.isArray(data[k])) return data[k];
  }
  return [];
};

export const authService = {
  register: (userData) => api.post('/auth/register', userData),
  verifyEmail: (data) => api.post('/auth/verify-email', data),
  resendVerification: (data) => api.post('/auth/resend-verification', data),
  login: (credentials) => api.post('/auth/login', credentials),
  // Backend uses stateless JWT — there is no server-side logout. Token is
  // cleared from localStorage by AuthContext.
  logout: () => Promise.resolve({ message: 'Signed out' }),
  getProfile: () => api.get('/users/me', { timeout: 5000 }),
  updateProfile: (data) => api.patch('/users/me', data),
  changePassword: (data) => api.post('/users/me/change-password', data),
  requestPasswordReset: (data) => api.post('/auth/request-password-reset', data),
  confirmPasswordReset: (data) => api.post('/auth/confirm-password-reset', data),
  refresh: (data) => api.post('/auth/refresh', data),
};

export const systemService = {
  health: () => api.get('/health'),
  apiEndpoints: () => api.get('/docs/api-endpoints'),
  projectRequirements: () => api.get('/docs/project-requirements'),
};

export const projectService = {
  list: () => api.get('/projects').then((data) => unwrapList(data, 'projects', 'results')),

  create: (data) => api.post('/projects', data),
  get: (id) => api.get(`/projects/${id}`),
  update: (id, data) => api.patch(`/projects/${id}`, data),
  delete: (id) => api.delete(`/projects/${id}`),
  download: (id) => api.get(`/projects/${id}/download`, { responseType: 'blob' }),
  getStats: (id) => api.get(`/projects/${id}/stats`),
  getIndex: (id) => api.get(`/projects/${id}/index`),
  getRequirements: (id) => api.get(`/projects/${id}/requirements`),
  repairRequirement: (id, requirementId, data = {}) => api.post(`/projects/${id}/requirements/${encodeURIComponent(requirementId)}/repair`, data),
  rebuildIndex: (id) => api.post(`/projects/${id}/index/rebuild`),

  getFolderContent: async (id, path = '') => {
    const res = await api.get(`/projects/${id}/folder-content`, {
      params: path ? { path } : {},
    });
    if (Array.isArray(res)) return res;
    if (Array.isArray(res?.content)) return res.content;
    if (Array.isArray(res?.results)) return res.results;
    return [];
  },

  uploadFile: (id, formData) =>
    api.post(`/projects/${id}/files`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),

  deleteFile: (projectId, path) => api.delete(`/projects/${projectId}/files`, { params: { path } }),

  getFileContent: async (projectId, path) => {
    if (!path) return '';
    try {
      const res = await api.get(`/projects/${projectId}/files/content`, { params: { path } });
      if (typeof res?.content === 'string') return res.content;
      if (typeof res === 'string') return res;
    } catch {}
    return '';
  },

  downloadFile: (projectId, path) => {
    if (!path) throw new Error('downloadFile requires a path');
    return api.get(`/projects/${projectId}/files/download`, {
      params: { path },
      responseType: 'blob',
    });
  },
};

export const projectRunService = {
  create: (projectId, data) => api.post(`/projects/${projectId}/runs`, data),
  list: (projectId, { active = false, limit = 30 } = {}) =>
    api.get(`/projects/${projectId}/runs`, { params: { active, limit } }),
  get: (projectId, runId) => api.get(`/projects/${projectId}/runs/${runId}`),
  events: (projectId, runId, after = 0) =>
    api.get(`/projects/${projectId}/runs/${runId}/events`, { params: { after } }),
  retry: (projectId, runId, data = {}) =>
    api.post(`/projects/${projectId}/runs/${runId}/retry`, data),
  resume: (projectId, runId, data = {}) =>
    api.post(`/projects/${projectId}/runs/${runId}/resume`, data),
  cancel: (projectId, runId) =>
    api.post(`/projects/${projectId}/runs/${runId}/cancel`),
};

export const chatService = {
  analyze: (projectId, data) => api.post(`/chatbot/projects/${projectId}/analyze`, data),
  getAnalysis: (projectId) => api.get(`/chatbot/projects/${projectId}/analysis`),
  listSessions: (projectId) =>
    api
      .get(`/chatbot/projects/${projectId}/sessions`)
      .then((d) => unwrapList(d, 'sessions', 'results')),

  // Backend has no dedicated initialize-chat endpoint; client-side equivalent:
  // reuse the most recent session, or create a fresh one. Wrapped as
  // `{ session }` so existing callers (ChatInterface, ChatContext) keep working.
  initializeChat: async (projectId) => {
    const sessions = await chatService.listSessions(projectId);
    const session = sessions.length > 0
      ? sessions[0]
      : await chatService.createSession(projectId, { title: 'New chat' });
    return { session };
  },

  createSession: (projectId, data) =>
    api.post(`/chatbot/projects/${projectId}/sessions`, data),
  getMessages: (sessionId) =>
    api.get(`/chatbot/sessions/${sessionId}`).then((data) => {
      if (Array.isArray(data)) return data;
      if (Array.isArray(data?.messages)) return data.messages;
      if (Array.isArray(data?.session?.messages)) return data.session.messages;
      return [];
    }),
  sendMessage: (projectId, data) => api.post(`/chatbot/projects/${projectId}/chat`, data),
  generateCode: (projectId, data) =>
    api.post(`/chatbot/projects/${projectId}/generate-code`, data),
  generateApp: (projectId, data) =>
    api.post(`/chatbot/projects/${projectId}/generate-app`, data),
  previewPlan: (projectId, data) =>
    api.post(`/chatbot/projects/${projectId}/preview-plan`, data),
  generateFromPrompt: (projectId, data) =>
    api.post(`/chatbot/projects/${projectId}/generate-from-prompt`, data),
  reviewCode: (projectId, data) =>
    api.post(`/chatbot/projects/${projectId}/review-code`, data),
  fixError: (projectId, data) =>
    api.post(`/chatbot/projects/${projectId}/fix-error`, data),
  explainCode: (projectId, data) =>
    api.post(`/chatbot/projects/${projectId}/explain-code`, data),
  deleteSession: (sessionId) => api.delete(`/chatbot/sessions/${sessionId}`),

  /** List models the chat picker can offer (id, name, provider, is_default). */
  listModels: () =>
    api.get('/chatbot/models').then((d) => unwrapList(d, 'models', 'results')),
  listCustomModels: () =>
    api.get('/chatbot/models/custom').then((d) => unwrapList(d, 'models', 'results')),
  createCustomModel: (data) => api.post('/chatbot/models/custom', data),
  updateCustomModel: (id, data) => api.patch(`/chatbot/models/custom/${id}`, data),
  deleteCustomModel: (id) => api.delete(`/chatbot/models/custom/${id}`),
  setCustomModelDefault: (id) => api.post(`/chatbot/models/custom/${id}/set-default`),
  useServerDefaultModel: () => api.post('/chatbot/models/use-server-default'),
  getModelInfo: () => api.get('/chatbot/model-info'),
  health: () => api.get('/chatbot/health'),

  /** Upload an attachment for a project chat. Returns an Attachment descriptor. */
  uploadAttachment: (projectId, file) => {
    const fd = new FormData();
    fd.append('file', file);
    return api.post(`/chatbot/projects/${projectId}/upload-attachment`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
};

/**
 * Resolve an attachment URL the backend returned. Backend paths begin with
 * `/api/v1/...`; we mount them at the same origin as the API server (not
 * relative to the React dev server's origin).
 */
export const resolveAttachmentUrl = (url) => {
  if (!url) return '';
  if (/^https?:/i.test(url)) return url;
  // The token must be appended for protected attachment routes.
  return `${API_ORIGIN}${url}`;
};

export const templateService = {
  list: () => api.get('/templates'),
  get: (id) => api.get(`/templates/${id}`),
  createProject: (data) => api.post('/templates/create-project', data),
};

export const llmModelService = {
  list: () => api.get('/llm-models'),
  create: (data) => api.post('/llm-models', data),
  get: (id) => api.get(`/llm-models/${id}`),
  update: (id, data) => api.patch(`/llm-models/${id}`, data),
  delete: (id) => api.delete(`/llm-models/${id}`),
  setDefault: (id) => api.post(`/llm-models/${id}/set-default`),
};

export const webSocketService = {
  url: async (path) => {
    const token = await getValidAccessToken();
    if (!token) return null;
    const wsOrigin = API_ORIGIN.replace(/^http/i, 'ws');
    const separator = path.includes('?') ? '&' : '?';
    return `${wsOrigin}/api/v1/ws${path}${separator}token=${encodeURIComponent(token)}`;
  },
  projectsUrl: () => webSocketService.url('/projects'),
  chatUrl: () => webSocketService.url('/chat'),
};

export default api;




