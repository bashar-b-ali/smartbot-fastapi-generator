import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Folder,
  Download,
  ArrowLeft,
  MoreHorizontal,
  Loader2,
  Wand2,
  Eye,
  CheckCircle2,
  AlertCircle,
  FileCode2,
  Pencil,
  Trash2,
} from 'lucide-react';
import FileViewer from '../../components/files/FileViewer.jsx';
import ChatInterface from '../../components/chat/ChatInterface.jsx';
import ProjectRunPanel from '../../components/projects/ProjectRunPanel.jsx';
import Button from '../../components/common/Button';
import IconButton from '../../components/common/IconButton';
import Badge from '../../components/common/Badge';
import Modal from '../../components/common/Modal';
import Input from '../../components/common/Input';
import Textarea from '../../components/common/Textarea';
import { useToast } from '../../components/common/Toast';
import { projectService } from '../../services/api.js';
import { useChat } from '../../contexts/ChatContext.jsx';
import { useProjectRuns } from '../../contexts/ProjectRunContext.jsx';
import { formatBytes, formatRelativeOrDate } from '../../utils/formatters';

const unwrap = (obj) => {
  if (!obj) return null;
  if (obj.project) return obj.project;
  if (obj.data) return obj.data;
  if (Array.isArray(obj.results) && obj.results.length === 1) return obj.results[0];
  return obj;
};

const fetchProjectInfo = async (projectId) =>
  Promise.all([
    projectService.get(projectId).catch(() => null),
    projectService.getStats(projectId).catch(() => null),
  ]);

const unwrapStats = (raw) => raw?.statistics || raw || null;

const normalizeEntries = (items, parentPath = '') =>
  (Array.isArray(items) ? items : []).map((item) => {
    const name = item.name || item.filename || item.path?.split('/').pop() || '';
    const rawPath = item.path || item.file_path || name;
    const path = rawPath.includes('/') || !parentPath ? rawPath : `${parentPath}/${rawPath}`;
    return {
      name,
      path,
      is_directory: !!(item.is_directory ?? item.is_dir ?? item.type === 'directory'),
      size: item.size ?? item.file_size ?? null,
    };
  });

const findProjectApiDoc = async (projectId) => {
  const directCandidates = [
    'API_ENDPOINT.md',
    'API_ENDPOINTS.md',
    'api_endpoints.md',
    'docs/API_ENDPOINT.md',
    'docs/API_ENDPOINTS.md',
    'docs/api_endpoints.md',
  ];

  for (const path of directCandidates) {
    const content = await projectService.getFileContent(projectId, path);
    if (String(content || '').trim()) {
      return { name: path.split('/').pop(), path, content };
    }
  }

  const queue = [''];
  const visited = new Set();
  let fetched = 0;

  while (queue.length && fetched < 150) {
    const path = queue.shift();
    if (visited.has(path)) continue;
    visited.add(path);
    fetched += 1;

    const entries = normalizeEntries(await projectService.getFolderContent(projectId, path), path);
    for (const entry of entries) {
      if (entry.is_directory) {
        queue.push(entry.path);
        continue;
      }
      const lower = entry.name.toLowerCase();
      if (lower.includes('api') && lower.includes('endpoint') && lower.endsWith('.md')) {
        const content = await projectService.getFileContent(projectId, entry.path);
        if (String(content || '').trim()) {
          return { ...entry, content };
        }
      }
    }
  }

  return null;
};

const ProjectDetail = ({ projectId: propProjectId }) => {
  const params = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const projectId = propProjectId || params.projectId || params.id || null;

  const [selectedFile, setSelectedFile] = useState(null);
  const [isChatVisible, setIsChatVisible] = useState(true);
  const [project, setProject] = useState(null);
  const [stats, setStats] = useState(null);
  const [loadingInfo, setLoadingInfo] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [showDetailsSheet, setShowDetailsSheet] = useState(false);
  const [showGenerateDialog, setShowGenerateDialog] = useState(false);
  const [editState, setEditState] = useState({ open: false, name: '', description: '', loading: false });
  const [deleteState, setDeleteState] = useState({ open: false, loading: false });
  const [fileRefreshKey, setFileRefreshKey] = useState(0);
  const appliedCheckpointsRef = useRef('');
  const { isBusy: projectPending, runs } = useProjectRuns(projectId);

  const loadProjectInfo = useCallback(async () => {
    if (!projectId) return;
    setLoadingInfo(true);
    const [projRaw, statsRaw] = await fetchProjectInfo(projectId);
    setProject(unwrap(projRaw));
    setStats(unwrapStats(statsRaw));
    setLoadingInfo(false);
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    if (!projectId) return undefined;
    setLoadingInfo(true);
    fetchProjectInfo(projectId).then(([projRaw, statsRaw]) => {
      if (cancelled) return;
      setProject(unwrap(projRaw));
      setStats(unwrapStats(statsRaw));
      setLoadingInfo(false);
    });
    return () => { cancelled = true; };
  }, [projectId]);

  const projectSize = stats?.total_size_bytes ?? stats?.total_size ?? stats?.total_bytes ?? null;
  const filesCount = stats?.total_files ?? stats?.files_count ?? stats?.file_count ?? null;
  const projectName = useMemo(() => project?.name || 'Project Workspace', [project]);
  const appliedCheckpoints = useMemo(
    () => runs.flatMap((run) => run.checkpoints || [])
      .filter((checkpoint) => checkpoint.status === 'applied')
      .map((checkpoint) => `${checkpoint.id}:${checkpoint.applied_revision || checkpoint.updated_at}`)
      .join('|'),
    [runs]
  );

  useEffect(() => {
    if (appliedCheckpointsRef.current && appliedCheckpointsRef.current !== appliedCheckpoints) {
      setFileRefreshKey((value) => value + 1);
      loadProjectInfo();
    }
    appliedCheckpointsRef.current = appliedCheckpoints;
  }, [appliedCheckpoints, loadProjectInfo]);

  const handleDownload = async () => {
    if (!projectId) return;
    setDownloading(true);
    try {
      const blob = await projectService.download(projectId);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      const filename = `${project?.name || `project-${projectId}`}.zip`;
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast.success('Download started.');
      setShowDetailsSheet(false);
    } catch {
      toast.error('Download failed. Please try again.');
    } finally {
      setDownloading(false);
    }
  };

  const openProjectDoc = async () => {
    setShowDetailsSheet(false);
    if (!projectId) return;
    const doc = await findProjectApiDoc(projectId);
    if (!doc) {
      toast.error('No generated API endpoints doc was found for this project.');
      return;
    }
    setSelectedFile({
      name: doc.name || 'API_ENDPOINT.md',
      path: doc.path,
      is_directory: false,
      size: new Blob([doc.content]).size,
      initialContent: doc.content,
    });
  };

  const openEditDialog = () => {
    setShowDetailsSheet(false);
    setEditState({
      open: true,
      name: project?.name || '',
      description: project?.description || '',
      loading: false,
    });
  };

  const saveProject = async (e) => {
    e?.preventDefault();
    if (!projectId || !editState.name.trim()) return;
    setEditState((state) => ({ ...state, loading: true }));
    try {
      await projectService.update(projectId, {
        name: editState.name.trim(),
        description: editState.description.trim(),
      });
      toast.success('Project updated.');
      setEditState({ open: false, name: '', description: '', loading: false });
      loadProjectInfo();
    } catch (err) {
      toast.error(err?.response?.data?.detail || err?.message || 'Failed to update project.');
      setEditState((state) => ({ ...state, loading: false }));
    }
  };

  const deleteProject = async () => {
    if (!projectId) return;
    setDeleteState((state) => ({ ...state, loading: true }));
    try {
      await projectService.delete(projectId);
      toast.success('Project deleted.');
      navigate('/dashboard');
    } catch (err) {
      toast.error(err?.response?.data?.detail || err?.message || 'Failed to delete project.');
      setDeleteState({ open: true, loading: false });
    }
  };

  return (
    <div className="flex h-[calc(100dvh-3.5rem)] min-h-0 flex-col gap-2.5 p-2.5 sm:gap-3 sm:p-3">
      <header className="flex flex-shrink-0 flex-wrap items-center justify-between gap-2.5 rounded-xl border border-line bg-surface-raised px-3 py-2.5 shadow-card">
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <IconButton
            label="Back to dashboard"
            tone="ghost"
            size="sm"
            onClick={() => navigate('/dashboard')}
            className="-ml-1.5"
          >
            <ArrowLeft className="h-4 w-4" />
          </IconButton>
          <div className="inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-300">
            <Folder className="h-4 w-4" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-sm font-semibold text-ink sm:text-base">
              {loadingInfo ? <span className="inline-flex items-center gap-2"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading...</span> : projectName}
            </h1>
            <div className="mt-0.5 flex items-center gap-2 text-[11px] text-ink-subtle">
              {project?.project_code && <Badge tone="primary" className="font-mono">{project.project_code}</Badge>}
              {projectPending && (
                <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Applying
                </span>
              )}
              <span className="truncate hidden md:inline">{loadingInfo ? '...' : filesCount ?? '-'} files</span>
              <span className="hidden md:inline">/</span>
              <span className="truncate hidden md:inline">{loadingInfo ? '...' : formatBytes(projectSize)}</span>
            </div>
          </div>
        </div>

        <div className="hidden sm:flex items-center gap-1.5">
          <Button
            variant="secondary"
            size="xs"
            onClick={openEditDialog}
            leftIcon={<Pencil className="h-3.5 w-3.5" />}
          >
            Edit
          </Button>
          <Button
            variant="secondary"
            size="xs"
            onClick={() => setShowGenerateDialog(true)}
            leftIcon={<Wand2 className="h-3.5 w-3.5" />}
          >
            Generate
          </Button>
          <Button
            variant="secondary"
            size="xs"
            onClick={openProjectDoc}
            leftIcon={<FileCode2 className="h-3.5 w-3.5" />}
          >
            API Endpoints
          </Button>
          <Button
            variant="primary"
            size="xs"
            onClick={handleDownload}
            loading={downloading}
            leftIcon={!downloading && <Download className="h-3.5 w-3.5" />}
          >
            {downloading ? 'Preparing...' : 'Download'}
          </Button>
        </div>

        <IconButton
          label="More options"
          tone="subtle"
          size="sm"
          className="sm:hidden"
          onClick={() => setShowDetailsSheet(true)}
        >
          <MoreHorizontal className="h-4 w-4" />
        </IconButton>
      </header>

      <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-line bg-surface-raised shadow-card">
        <FileViewer
          projectId={projectId}
          selectedFile={selectedFile}
          onFileSelect={setSelectedFile}
          refreshKey={fileRefreshKey}
        />
      </div>

      <ChatInterface
        projectId={projectId}
        topContent={(
          <ProjectRunPanel
            projectId={projectId}
            onOpenFile={(filePath) => {
              if (!filePath) return;
              setSelectedFile({
                name: filePath.split('/').pop(),
                path: filePath,
                is_directory: false,
              });
            }}
          />
        )}
        isVisible={isChatVisible}
        onToggleVisibility={() => setIsChatVisible((v) => !v)}
        onOpenFile={(filePath) => {
          if (!filePath) return;
          setSelectedFile({
            name: filePath.split('/').pop(),
            path: filePath,
            is_directory: false,
          });
        }}
        onFilesChanged={(result) => {
          const filePath = result?.view_file_path || result?.generated_files?.[0]?.path || '';
          if (filePath) {
            setSelectedFile({
              name: filePath.split('/').pop(),
              path: filePath,
              is_directory: false,
            });
          }
          setFileRefreshKey((v) => v + 1);
          loadProjectInfo();
        }}
      />

      <Modal
        open={showDetailsSheet}
        onClose={() => setShowDetailsSheet(false)}
        title="Project details"
        size="md"
      >
        <dl className="grid grid-cols-2 gap-2.5 text-sm">
          <Detail label="Name" value={project?.name || '-'} />
          <Detail label="Code" value={project?.project_code || '-'} />
          <Detail label="Files" value={loadingInfo ? '...' : filesCount ?? '-'} />
          <Detail label="Size" value={loadingInfo ? '...' : formatBytes(projectSize)} />
          <Detail label="Created" value={project?.created_at ? formatRelativeOrDate(project.created_at) : '-'} />
          <Detail label="ID" value={projectId} />
        </dl>
        <Button
          variant="secondary"
          fullWidth
          size="md"
          className="mt-4"
          onClick={openEditDialog}
          leftIcon={<Pencil className="h-4 w-4" />}
        >
          Edit project
        </Button>
        <Button
          variant="primary"
          fullWidth
          size="md"
          className="mt-2"
          loading={downloading}
          onClick={handleDownload}
          leftIcon={!downloading && <Download className="h-4 w-4" />}
        >
          Download project as .zip
        </Button>
        <Button
          variant="secondary"
          fullWidth
          size="md"
          className="mt-2"
          onClick={openProjectDoc}
          leftIcon={<FileCode2 className="h-4 w-4" />}
        >
          Open API endpoints
        </Button>
        <Button
          variant="secondary"
          fullWidth
          size="md"
          className="mt-2"
          onClick={() => {
            setShowDetailsSheet(false);
            setShowGenerateDialog(true);
          }}
          leftIcon={<Wand2 className="h-4 w-4" />}
        >
          Generate project files
        </Button>
        <Button
          variant="danger"
          fullWidth
          size="md"
          className="mt-2"
          onClick={() => {
            setShowDetailsSheet(false);
            setDeleteState({ open: true, loading: false });
          }}
          leftIcon={<Trash2 className="h-4 w-4" />}
        >
          Delete project
        </Button>
      </Modal>

      <Modal
        open={editState.open}
        onClose={() => !editState.loading && setEditState({ open: false, name: '', description: '', loading: false })}
        title="Edit project"
        description="Keep the project list clear with a short name and description."
        size="lg"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setEditState({ open: false, name: '', description: '', loading: false })}
              disabled={editState.loading}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={saveProject}
              loading={editState.loading}
              disabled={!editState.name.trim()}
              leftIcon={!editState.loading && <Pencil className="h-4 w-4" />}
            >
              Save changes
            </Button>
          </>
        }
      >
        <form onSubmit={saveProject} className="space-y-4">
          <Input
            label="Project name"
            value={editState.name}
            onChange={(e) => setEditState((state) => ({ ...state, name: e.target.value }))}
            autoFocus
            required
            maxLength={60}
          />
          <Textarea
            label="Description"
            value={editState.description}
            onChange={(e) => setEditState((state) => ({ ...state, description: e.target.value }))}
            rows={4}
            maxLength={500}
          />
        </form>
      </Modal>

      <Modal
        open={deleteState.open}
        onClose={() => !deleteState.loading && setDeleteState({ open: false, loading: false })}
        title="Delete project"
        description="This permanently removes the project, files, docs, and chat history."
        size="md"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setDeleteState({ open: false, loading: false })}
              disabled={deleteState.loading}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={deleteProject}
              loading={deleteState.loading}
              leftIcon={!deleteState.loading && <Trash2 className="h-4 w-4" />}
            >
              Delete project
            </Button>
          </>
        }
      >
        <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-200">
          <p className="font-semibold">{projectName}</p>
          <p className="mt-1 text-xs leading-5 opacity-90">
            {filesCount ?? 0} files, {formatBytes(projectSize)}. This action cannot be undone.
          </p>
        </div>
      </Modal>

      <Modal
        open={showGenerateDialog}
        onClose={() => setShowGenerateDialog(false)}
        title="Generate project files"
        description="Preview the directive plan before writing files into this project."
        size="2xl"
        bodyClassName="pt-0"
      >
        <GenerationDialog
          projectId={projectId}
          onGenerated={(result) => {
            const filePath = result?.view_file_path || result?.files?.[0]?.path || '';
            if (filePath) {
              setSelectedFile({
                name: filePath.split('/').pop(),
                path: filePath,
                is_directory: false,
              });
            }
            setFileRefreshKey((v) => v + 1);
            loadProjectInfo();
            setShowGenerateDialog(false);
          }}
        />
      </Modal>
    </div>
  );
};

const Detail = ({ label, value }) => (
  <div className="rounded-lg border border-line bg-surface-muted/40 p-2.5">
    <dt className="text-[10px] uppercase tracking-wider font-semibold text-ink-subtle">{label}</dt>
    <dd className="mt-1 truncate font-medium text-ink">{value}</dd>
  </div>
);

const fileCountFrom = (result) =>
  result?.stats?.file_count ?? result?.stats?.total_files ?? result?.files?.length ?? result?.file_paths?.length ?? 0;

const buildGenerationPayload = ({ prompt, selectedModelId }) =>
  selectedModelId
    ? { prompt, model_id: selectedModelId }
    : { prompt, provider: 'auto', model_id: null };

const GenerationDialog = ({ projectId, onGenerated }) => {
  const toast = useToast();
  const {
    previewPlan,
    models,
    modelsLoading,
    selectedModelId,
    loadModels,
  } = useChat();
  const { createRun } = useProjectRuns(projectId);

  const [prompt, setPrompt] = useState('');
  const [preview, setPreview] = useState(null);
  const [generated, setGenerated] = useState(null);
  const [busy, setBusy] = useState(null);

  useEffect(() => {
    loadModels();
  }, [loadModels]);

  const selectedModel = useMemo(
    () => models.find((m) => m.id === selectedModelId) || null,
    [models, selectedModelId]
  );

  const trimmedPrompt = prompt.trim();
  const canPreview = trimmedPrompt.length > 0 && trimmedPrompt.length <= 4000 && !busy;
  const canGenerate = canPreview && !!preview && !busy;

  const handlePreview = async () => {
    if (!canPreview) return;
    setBusy('preview');
    try {
      const result = await previewPlan(
        projectId,
        buildGenerationPayload({
          prompt: trimmedPrompt,
          selectedModelId,
        })
      );
      setGenerated(null);
      setPreview(result);
    } catch (err) {
      toast.error(err?.message || 'Preview failed.');
    } finally {
      setBusy(null);
    }
  };

  const handleGenerate = async () => {
    if (!canGenerate) return;
    setBusy('generate');
    try {
      const result = await createRun(
        projectId,
        {
          operation: 'create',
          ...buildGenerationPayload({
          prompt: trimmedPrompt,
          selectedModelId,
          }),
          idempotency_key: `generation-dialog:${Date.now()}`,
        }
      );
      toast.success('Generation queued. Progress is saved in the project workspace.');
      setGenerated(result);
    } catch (err) {
      toast.error(err?.message || 'Generation failed.');
    } finally {
      setBusy(null);
    }
  };

  const files = preview?.file_paths || preview?.files?.map((f) => f.path) || [];
  const directives = Array.isArray(preview?.plan?.directives) ? preview.plan.directives : [];
  const provider = preview?.provider || (selectedModel ? selectedModel.provider : 'auto');
  const validation = preview?.validation;

  return (
    <div className="space-y-4">
      <Textarea
        label="Prompt"
        value={prompt}
        maxLength={4000}
        rows={5}
        placeholder="Build a task manager API with login, projects, tasks, status filters, and file uploads."
        onChange={(e) => {
          setPrompt(e.target.value);
          setPreview(null);
          setGenerated(null);
        }}
        disabled={!!busy}
      />

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface-muted/45 px-3 py-2.5">
        <span className="text-sm font-medium text-ink">
          Existing files are preserved. Changes are applied as validated checkpoints.
        </span>
        <span className="text-xs text-ink-subtle">
          {modelsLoading
            ? 'Loading model options...'
            : selectedModel
              ? `Using ${selectedModel.name}`
              : 'Using backend auto provider'}
        </span>
      </div>

      {preview && (
        <div className="rounded-xl border border-line bg-surface-raised shadow-card overflow-hidden">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-ink truncate">
                {preview.plan?.project_name || 'Generated plan'}
              </p>
              <p className="mt-0.5 text-xs text-ink-muted line-clamp-2">
                {preview.plan?.description || 'No description returned.'}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="rounded-md bg-primary-50 px-2 py-1 font-semibold text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
                {provider}
              </span>
              <span className="rounded-md border border-line bg-surface-muted px-2 py-1 text-ink-muted">
                {fileCountFrom(preview)} files
              </span>
            </div>
          </div>

          <div className="grid gap-4 p-4 md:grid-cols-[minmax(0,1fr)_minmax(220px,0.8fr)]">
            <div className="space-y-3">
              {preview.selected_template && (
                <div>
                  <p className="text-xs font-bold uppercase tracking-wider text-ink-subtle">Template</p>
                  <p className="mt-1 text-sm font-medium text-ink">{preview.selected_template.name}</p>
                  {preview.selected_template.endpoint_pattern && (
                    <p className="mt-1 text-xs text-ink-muted">{preview.selected_template.endpoint_pattern}</p>
                  )}
                </div>
              )}

              <div>
                <p className="text-xs font-bold uppercase tracking-wider text-ink-subtle">Directives</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {directives.length ? directives.slice(0, 12).map((directive, index) => (
                    <span
                      key={`${directive.action}-${directive.name || directive.pack || index}`}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-muted px-2.5 py-1 text-xs text-ink"
                    >
                      <FileCode2 className="h-3 w-3 text-primary-500" />
                      {directive.name || directive.pack || directive.action}
                    </span>
                  )) : (
                    <span className="text-sm text-ink-muted">No directives returned.</span>
                  )}
                </div>
              </div>

              {validation && (
                <div className="inline-flex items-center gap-2 text-sm">
                  {validation.passed ? (
                    <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                  ) : (
                    <AlertCircle className="h-4 w-4 text-amber-500" />
                  )}
                  <span className="text-ink-muted">
                    Validation {validation.passed ? 'passed' : 'returned warnings'}
                  </span>
                </div>
              )}
            </div>

            <div>
              <p className="text-xs font-bold uppercase tracking-wider text-ink-subtle">Files</p>
              <div className="mt-2 max-h-48 overflow-auto rounded-lg border border-line bg-surface-muted/40 p-2 text-xs font-mono text-ink">
                {files.length ? files.map((path) => (
                  <div key={path} className="truncate py-0.5">{path}</div>
                )) : (
                  <div className="font-sans text-sm text-ink-muted">No files returned.</div>
                )}
              </div>
            </div>

            {generated?.working_summary?.length > 0 && (
              <div className="md:col-span-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200">
                <p className="font-semibold">Working summary</p>
                <ul className="mt-1 list-disc pl-5">
                  {generated.working_summary.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                {generated.next_prompt && (
                  <p className="mt-2 text-xs">{generated.next_prompt}</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2">
        {generated?.view_file_path && (
          <Button
            variant="secondary"
            onClick={() => onGenerated?.(generated)}
            leftIcon={<Eye className="h-4 w-4" />}
          >
            View file
          </Button>
        )}
        <Button
          variant="secondary"
          onClick={handlePreview}
          loading={busy === 'preview'}
          disabled={!canPreview}
          leftIcon={<Eye className="h-4 w-4" />}
        >
          Preview plan
        </Button>
        <Button
          variant="primary"
          onClick={handleGenerate}
          loading={busy === 'generate'}
          disabled={!canGenerate}
          leftIcon={<Wand2 className="h-4 w-4" />}
        >
          Generate files
        </Button>
      </div>
    </div>
  );
};

export default ProjectDetail;
