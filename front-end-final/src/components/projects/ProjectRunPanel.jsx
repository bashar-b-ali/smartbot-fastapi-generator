import React, { useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock3,
  FileCode2,
  Loader2,
  PauseCircle,
  RefreshCw,
  Square,
} from 'lucide-react';
import { useProjectRuns } from '../../contexts/ProjectRunContext';
import Button from '../common/Button';
import { cn } from '../common/cn';

const ACTIVE = new Set(['queued', 'running', 'waiting_for_model']);
const ATTENTION = new Set(['needs_attention', 'needs_input']);
const isCompleted = (status) => status === 'applied' || String(status || '').startsWith('completed');

const toneFor = (status) => {
  if (isCompleted(status)) return 'text-emerald-700 bg-emerald-50 border-emerald-200 dark:text-emerald-200 dark:bg-emerald-500/10 dark:border-emerald-500/30';
  if (ATTENTION.has(status) || status === 'blocked') return 'text-amber-800 bg-amber-50 border-amber-200 dark:text-amber-100 dark:bg-amber-500/10 dark:border-amber-500/30';
  if (status === 'failed' || status === 'cancelled') return 'text-red-700 bg-red-50 border-red-200 dark:text-red-200 dark:bg-red-500/10 dark:border-red-500/30';
  return 'text-primary-700 bg-primary-50 border-primary-200 dark:text-primary-200 dark:bg-primary-500/10 dark:border-primary-500/30';
};

const StatusIcon = ({ status }) => {
  if (isCompleted(status)) return <CheckCircle2 className="h-4 w-4" />;
  if (ATTENTION.has(status) || status === 'blocked') return <AlertTriangle className="h-4 w-4" />;
  if (status === 'waiting_for_model') return <PauseCircle className="h-4 w-4" />;
  if (ACTIVE.has(status) || status === 'retrying') return <Loader2 className="h-4 w-4 animate-spin" />;
  return <Clock3 className="h-4 w-4" />;
};

const readableIssue = (item) => {
  if (typeof item === 'string') return item;
  if (item?.message) return String(item.message);
  if (item?.error) return String(item.error);
  try { return JSON.stringify(item); } catch { return String(item); }
};

const validationSummary = (run) => {
  const result = run?.result || {};
  const validation = result.validation || {};
  const staticValidation = validation.static_validation || {};
  const artifactValidation = validation.artifact_validation || result.artifact_validation || {};
  const runtimeValidation = validation.runtime_validation || result.runtime_validation || {};
  const hasValidation = [validation, staticValidation, artifactValidation, runtimeValidation]
    .some((value) => value && Object.keys(value).length > 0);

  const gates = [
    ['Code', staticValidation.passed ?? validation.static_safe],
    ['Requirements', artifactValidation.passed],
    ['Runtime', runtimeValidation.passed],
    ['Accepted', validation.accepted ?? result.accepted],
  ].filter(([, passed]) => typeof passed === 'boolean');

  const issues = [
    ...(result.missing_artifacts || []),
    ...(validation.missing_artifacts || []),
    ...(artifactValidation.missing_artifacts || []),
    ...(result.regressions || []),
    ...(validation.regressions || []),
    ...(artifactValidation.regressions || []),
    ...(runtimeValidation.errors || []),
    ...(runtimeValidation.skipped_paths || []).map((path) => `Runtime validation unavailable: ${path}`),
  ].map(readableIssue).filter(Boolean)
    .filter((item, index, list) => list.indexOf(item) === index);

  return { hasValidation, gates, issues };
};

const GateBadge = ({ label, passed }) => (
  <span className={cn(
    'inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[10px] font-bold uppercase tracking-wide',
    passed
      ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-100'
      : 'border-red-300 bg-red-50 text-red-800 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-100',
  )}>
    {passed ? <CheckCircle2 className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
    {label}
  </span>
);

const filePaths = (run) => {
  const checkpointFiles = (run?.checkpoints || []).flatMap((item) => item.changed_files || []);
  const resultFiles = run?.result?.changed_files || run?.result?.files || [];
  return [...checkpointFiles, ...resultFiles]
    .map((item) => typeof item === 'string' ? item : item?.path)
    .filter(Boolean)
    .filter((path, index, list) => list.indexOf(path) === index);
};

const ProjectRunPanel = ({ projectId, onOpenFile }) => {
  const { runs, retryRun, resumeRun, cancelRun } = useProjectRuns(projectId);
  const run = runs[0] || null;
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState('');
  const files = useMemo(() => filePaths(run), [run]);
  const validation = useMemo(() => validationSummary(run), [run]);
  if (!run) return null;

  const active = ACTIVE.has(run.status);
  const attention = ATTENTION.has(run.status);
  const action = async (name, callback) => {
    setBusy(name);
    try { await callback(); } finally { setBusy(''); }
  };

  return (
    <section className={cn('flex-shrink-0 overflow-hidden rounded-b-xl rounded-t-none border shadow-card', toneFor(run.status))}>
      <button type="button" className="flex w-full items-center gap-3 px-2.5 py-1.5 text-left transition-colors hover:bg-white/20 dark:hover:bg-black/10" onClick={() => setExpanded((value) => !value)}>
        <StatusIcon status={run.status} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold capitalize">{run.operation} project</span>
            <span className="rounded-full border border-current/20 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide">
              {String(run.status).replaceAll('_', ' ')}
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs opacity-80">
            {run.stage?.replaceAll('_', ' ')} - {run.prompt}
          </p>
        </div>
        {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
      </button>

      <div className={cn('grid overflow-hidden transition-[grid-template-rows,opacity] duration-300 ease-out', expanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0')}>
        <div className="min-h-0 max-h-[calc(100dvh-12rem)] overflow-y-auto sm:max-h-96 border-t border-current/15 bg-white/35 px-2.5 py-2 dark:bg-black/10">
          <div className="space-y-2">
            {(run.checkpoints || []).map((checkpoint) => (
              <div key={checkpoint.id || checkpoint.sequence} className="flex items-start gap-2 text-xs">
                <StatusIcon status={checkpoint.status} />
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">{checkpoint.sequence}. {checkpoint.title}</p>
                  <p className="opacity-75">
                    {checkpoint.stage?.replaceAll('_', ' ')}
                    {checkpoint.attempts > 1 ? ` - attempt ${checkpoint.attempts}` : ''}
                  </p>
                  {checkpoint.error?.message && <p className="mt-1 break-words">{String(checkpoint.error.message)}</p>}
                </div>
              </div>
            ))}
          </div>

          {files.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {files.map((path) => (
                <button type="button" key={path} onClick={() => onOpenFile?.(path)} className="inline-flex max-w-full items-center gap-1 rounded-md border border-current/20 bg-white/60 px-2 py-1 text-[11px] hover:bg-white dark:bg-black/10">
                  <FileCode2 className="h-3 w-3 flex-shrink-0" />
                  <span className="truncate">{path}</span>
                </button>
              ))}
            </div>
          )}

          {validation.hasValidation && (
            <div className="mt-3 rounded-lg border border-current/15 bg-white/45 p-2.5 dark:bg-black/10">
              <p className="text-xs font-semibold">Acceptance checks</p>
              {validation.gates.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {validation.gates.map(([label, passed]) => <GateBadge key={label} label={label} passed={passed} />)}
                </div>
              )}
              {validation.issues.length > 0 && (
                <ul className="mt-2 list-disc space-y-1 pl-4 text-xs">
                  {validation.issues.slice(0, 6).map((issue) => <li key={issue} className="break-words">{issue}</li>)}
                  {validation.issues.length > 6 && <li>{validation.issues.length - 6} more issues</li>}
                </ul>
              )}
            </div>
          )}

          {run.warnings?.length > 0 && <p className="mt-3 text-xs">{run.warnings.length} advisory warnings recorded.</p>}

          <div className="mt-3 flex flex-wrap justify-end gap-2">
            {attention && (
              <Button size="xs" variant="secondary" loading={busy === 'resume'} leftIcon={busy !== 'resume' && <RefreshCw className="h-3.5 w-3.5" />} onClick={() => action('resume', () => resumeRun(projectId, run.run_id, {}))}>
                Resume
              </Button>
            )}
            {run.status === 'waiting_for_model' && (
              <Button size="xs" variant="secondary" loading={busy === 'retry'} leftIcon={busy !== 'retry' && <RefreshCw className="h-3.5 w-3.5" />} onClick={() => action('retry', () => retryRun(projectId, run.run_id, {}))}>
                Retry now
              </Button>
            )}
            {(active || attention) && (
              <Button size="xs" variant="secondary" loading={busy === 'cancel'} leftIcon={busy !== 'cancel' && <Square className="h-3.5 w-3.5" />} onClick={() => action('cancel', () => cancelRun(projectId, run.run_id))}>
                Stop future steps
              </Button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
};

export default ProjectRunPanel;
