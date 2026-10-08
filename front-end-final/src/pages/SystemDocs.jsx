import React, { useEffect, useMemo, useState } from 'react';
import { BookOpen, FileText, RefreshCw, FileSearch } from 'lucide-react';
import { systemService } from '../services/api';
import Button from '../components/common/Button';
import Spinner from '../components/common/Spinner';
import { cn } from '../components/common/cn';
import MarkdownDocument from '../components/common/MarkdownDocument';

const tabs = [
  { key: 'api', label: 'API Endpoints', icon: BookOpen },
  { key: 'requirements', label: 'Requirements', icon: FileText },
];

const SystemDocs = () => {
  const [activeTab, setActiveTab] = useState('api');
  const [docs, setDocs] = useState({ api: null, requirements: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const activeDoc = useMemo(() => docs[activeTab], [activeTab, docs]);
  const activeContent = String(activeDoc?.content || '').trim();

  const loadDocs = async () => {
    setLoading(true);
    setError('');
    try {
      const [apiEndpoints, projectRequirements] = await Promise.all([
        systemService.apiEndpoints(),
        systemService.projectRequirements(),
      ]);
      setDocs({ api: apiEndpoints, requirements: projectRequirements });
    } catch (err) {
      setError(err?.message || 'Unable to load documentation.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDocs();
  }, []);

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-ink">Backend Docs</h1>
          <p className="text-sm text-ink-muted mt-1">
            Current backend endpoints and runtime requirements generated from the API.
          </p>
        </div>
        <Button onClick={loadDocs} variant="secondary" disabled={loading}>
          <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      <div className="border-b border-line">
        <div className="flex gap-1 overflow-x-auto">
          {tabs.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              type="button"
              onClick={() => setActiveTab(key)}
              className={cn(
                'inline-flex items-center gap-2 px-3 h-10 text-sm font-medium border-b-2 transition-colors',
                activeTab === key
                  ? 'border-primary-500 text-primary-700 dark:text-primary-300'
                  : 'border-transparent text-ink-muted hover:text-ink'
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="py-16 flex justify-center">
          <Spinner label="Loading docs..." />
        </div>
      ) : error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300">
          {error}
        </div>
      ) : (
        <section className="space-y-3">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="text-lg font-semibold text-ink">{activeDoc?.name}</h2>
            <span className="text-xs text-ink-subtle">
              {activeDoc?.updated_at
                ? `Updated ${new Date(activeDoc.updated_at).toLocaleString()}`
                : activeDoc?.path}
            </span>
          </div>
          {activeContent ? (
            <MarkdownDocument content={activeContent} className="min-h-[32rem]" />
          ) : (
            <div className="flex min-h-[24rem] flex-col items-center justify-center rounded-xl border border-line bg-surface-raised p-6 text-center shadow-card">
              <span className="inline-flex h-11 w-11 items-center justify-center rounded-xl border border-line bg-surface-muted text-ink-muted">
                <FileSearch className="h-5 w-5" />
              </span>
              <h3 className="mt-3 text-base font-semibold text-ink">No documentation content yet</h3>
              <p className="mt-1 max-w-md text-sm leading-6 text-ink-muted">
                The backend returned an empty document for this tab. Refresh after generating or uploading project files.
              </p>
              <Button onClick={loadDocs} variant="secondary" className="mt-4" disabled={loading}>
                <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
                Refresh docs
              </Button>
            </div>
          )}
        </section>
      )}
    </div>
  );
};

export default SystemDocs;
