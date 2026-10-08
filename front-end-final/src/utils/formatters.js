const API_DATE_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/;

export const parseApiDate = (value) => {
  if (!value) return null;
  if (value instanceof Date) return value;
  const text = String(value);
  const normalized = API_DATE_RE.test(text) ? `${text}Z` : text;
  const d = new Date(normalized);
  return Number.isNaN(d.getTime()) ? null : d;
};

export const formatBytes = (bytes) => {
  if (bytes == null) return '\u2014';
  const n = typeof bytes === 'number' ? bytes : Number(bytes);
  if (Number.isNaN(n)) return '\u2014';
  if (n === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(k)), sizes.length - 1);
  const value = n / Math.pow(k, i);
  return `${parseFloat(value.toFixed(value < 10 && i > 0 ? 2 : 1))} ${sizes[i]}`;
};

export const formatDate = (value) => {
  const d = parseApiDate(value);
  if (!d) return '\u2014';
  return d.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
};

export const formatTime = (value) => {
  const d = parseApiDate(value);
  if (!d) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

export const formatRelativeOrDate = (value) => {
  const d = parseApiDate(value);
  if (!d) return '\u2014';
  const now = new Date();
  const msPerDay = 1000 * 60 * 60 * 24;
  const diffMs = now.setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0);
  const diffDays = Math.floor(diffMs / msPerDay);
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays > 1 && diffDays <= 6) return `${diffDays} days ago`;
  return formatDate(d);
};

export const initialsOf = (user) => {
  if (!user) return '?';
  const f = (user.first_name || '').trim()[0] || '';
  const l = (user.last_name || '').trim()[0] || '';
  if (f || l) return `${f}${l}`.toUpperCase();
  const e = (user.email || '').trim();
  return e ? e[0].toUpperCase() : '?';
};
