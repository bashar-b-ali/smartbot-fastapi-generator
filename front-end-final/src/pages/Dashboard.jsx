import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import {
  Folder,
  Plus,
  Trash2,
  ArrowUpRight,
  RefreshCw,
  FileText,
  Calendar,
  HardDrive,
  SlidersHorizontal,
  Search,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  MoreHorizontal,
  Edit3,
  Sparkles,
  X,
  CheckCircle2,
  Clock,
  Database,
  Activity,
  Star,
  BarChart3,
  PieChart,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { useProject } from '../contexts/ProjectContext.jsx';
import { useToast } from '../components/common/Toast';
import { projectService } from '../services/api.js';
import Button from '../components/common/Button';
import IconButton from '../components/common/IconButton';
import Input from '../components/common/Input';
import Textarea from '../components/common/Textarea';
import Modal from '../components/common/Modal';
import Card from '../components/common/Card';
import Badge from '../components/common/Badge';
import EmptyState from '../components/common/EmptyState';
import DeleteConfirmation from '../components/common/DeleteConfirmation';
import { SkeletonCard } from '../components/common/Skeleton';
import { cn } from '../components/common/cn';
import { formatBytes, formatRelativeOrDate } from '../utils/formatters';

/* ────────────────────────────────────────────────────────────
 * Sort + Tab config
 * ──────────────────────────────────────────────────────────── */
const SORT_OPTIONS = [
  { key: 'updated',  label: 'Recently updated' },
  { key: 'created',  label: 'Recently created' },
  { key: 'name',     label: 'Name (A → Z)' },
  { key: 'name_z',   label: 'Name (Z → A)' },
  { key: 'files',    label: 'Most files' },
  { key: 'size',     label: 'Largest size' },
];

const TABS = [
  { key: 'all',     label: 'All projects' },
  { key: 'recent',  label: 'Active this week' },
  { key: 'largest', label: 'Largest' },
];

const PAGE_SIZE = 8;

const sortProjects = (rows, key, statsByProject) => {
  const list = [...rows];
  switch (key) {
    case 'name':    return list.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
    case 'name_z':  return list.sort((a, b) => (b.name || '').localeCompare(a.name || ''));
    case 'created': return list.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
    case 'files':   return list.sort((a, b) => (statsByProject[b.id]?.files || 0) - (statsByProject[a.id]?.files || 0));
    case 'size':    return list.sort((a, b) => (statsByProject[b.id]?.size || 0) - (statsByProject[a.id]?.size || 0));
    case 'updated':
    default:
      return list.sort(
        (a, b) =>
          new Date(b.updated_at || b.created_at || 0) -
          new Date(a.updated_at || a.created_at || 0)
      );
  }
};

const filterByTab = (rows, tab, statsByProject) => {
  if (tab === 'recent') {
    const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
    return rows.filter((p) => new Date(p.updated_at || p.created_at || 0).getTime() >= sevenDaysAgo);
  }
  if (tab === 'largest') {
    return [...rows].sort((a, b) => (statsByProject[b.id]?.size || 0) - (statsByProject[a.id]?.size || 0)).slice(0, 12);
  }
  return rows;
};

const normalizeProjectStats = (raw) => {
  const stats = raw?.statistics || raw || {};
  return {
    size: stats.total_size_bytes || stats.total_size || stats.total_bytes || 0,
    files: stats.total_files || stats.files_count || stats.file_count || 0,
  };
};

/* ────────────────────────────────────────────────────────────
 * Page
 * ──────────────────────────────────────────────────────────── */
const Dashboard = () => {
  const { projects, loading, fetchProjects, deleteProject, createProject } = useProject();
  const toast = useToast();
  const navigate = useNavigate();

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createDescription, setCreateDescription] = useState('');
  const [creating, setCreating] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState(() => localStorage.getItem('dashboard-sort') || 'updated');
  const [tab, setTab] = useState('all');
  const [page, setPage] = useState(1);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [statsExpanded, setStatsExpanded] = useState(true);
  const [chartsExpanded, setChartsExpanded] = useState(true);
  const [sortMenuOpen, setSortMenuOpen] = useState(false);
  const sortMenuRef = useRef(null);
  const sortMenuPanelRef = useRef(null);
  const [sortMenuPosition, setSortMenuPosition] = useState({ top: 0, right: 0 });
  const [deleteState, setDeleteState] = useState({ open: false, id: null, name: '', loading: false });
  const [editState, setEditState] = useState({
    open: false,
    id: null,
    name: '',
    description: '',
    loading: false,
  });
  const [statsByProject, setStatsByProject] = useState({});
  const [statsLoading, setStatsLoading] = useState(false);
  const searchRef = useRef(null);

  /* ── ⌘K / Ctrl+K → focus search ─── */
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => { localStorage.setItem('dashboard-sort', sortKey); }, [sortKey]);
  useEffect(() => { setPage(1); }, [search, sortKey, tab]);

  const updateSortMenuPosition = () => {
    const rect = sortMenuRef.current?.getBoundingClientRect();
    if (!rect) return;
    setSortMenuPosition({
      top: rect.bottom + 8,
      right: Math.max(8, window.innerWidth - rect.right),
    });
  };

  useEffect(() => {
    const onClick = (e) => {
      const inTrigger = sortMenuRef.current?.contains(e.target);
      const inPanel = sortMenuPanelRef.current?.contains(e.target);
      if (!inTrigger && !inPanel) setSortMenuOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  useEffect(() => {
    if (!sortMenuOpen) return undefined;
    updateSortMenuPosition();
    window.addEventListener('resize', updateSortMenuPosition);
    window.addEventListener('scroll', updateSortMenuPosition, true);
    return () => {
      window.removeEventListener('resize', updateSortMenuPosition);
      window.removeEventListener('scroll', updateSortMenuPosition, true);
    };
  }, [sortMenuOpen]);

  useEffect(() => {
    handleRefresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRefresh = async () => {
    setRefreshing(true);
    try { await fetchProjects(); }
    finally { setRefreshing(false); }
  };

  const projectsArray = useMemo(
    () => (Array.isArray(projects) ? projects : []),
    [projects]
  );

  /* ── stats per project ─── */
  useEffect(() => {
    let mounted = true;
    if (!projectsArray.length) {
      setStatsByProject({});
      return;
    }
    setStatsLoading(true);
    Promise.all(
      projectsArray.map(async (p) => {
        try {
          const raw = await projectService.getStats(p.id).catch(() => null);
          return [p.id, normalizeProjectStats(raw)];
        } catch {
          return [p.id, { size: 0, files: 0 }];
        }
      })
    ).then((entries) => {
      if (!mounted) return;
      setStatsByProject(Object.fromEntries(entries));
      setStatsLoading(false);
    });
    return () => { mounted = false; };
  }, [projectsArray]);

  /* ── filtering + sorting ─── */
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    let rows = projectsArray;
    rows = filterByTab(rows, tab, statsByProject);
    if (q) {
      rows = rows.filter((p) =>
        [p.name, p.description, p.project_code]
          .filter(Boolean)
          .some((v) => String(v).toLowerCase().includes(q))
      );
    }
    return sortProjects(rows, sortKey, statsByProject);
  }, [projectsArray, search, sortKey, tab, statsByProject]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const pagedProjects = useMemo(
    () => filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [filtered, safePage]
  );

  useEffect(() => {
    if (page !== safePage) setPage(safePage);
  }, [page, safePage]);

  const totals = useMemo(() => {
    const totalSize = Object.values(statsByProject).reduce((a, s) => a + (s?.size || 0), 0);
    const totalFiles = Object.values(statsByProject).reduce((a, s) => a + (s?.files || 0), 0);
    return { count: projectsArray.length, totalSize, totalFiles };
  }, [statsByProject, projectsArray]);

  const featured = useMemo(() => {
    if (!projectsArray.length) return null;
    return [...projectsArray].sort(
      (a, b) =>
        new Date(b.updated_at || b.created_at || 0) -
        new Date(a.updated_at || a.created_at || 0)
    )[0];
  }, [projectsArray]);

  /* Per-tab counts so the tab bar can show signal at a glance. */
  const tabCounts = useMemo(() => {
    const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
    return {
      all: projectsArray.length,
      recent: projectsArray.filter(
        (p) => new Date(p.updated_at || p.created_at || 0).getTime() >= sevenDaysAgo
      ).length,
      largest: Math.min(projectsArray.length, 12),
    };
  }, [projectsArray]);

  const activeCount = tabCounts.recent;
  const lastActivity = featured
    ? formatRelativeOrDate(featured.updated_at || featured.created_at)
    : '—';

  /* ── handlers ─── */
  const handleCreate = async (e) => {
    e?.preventDefault();
    if (!createName.trim()) return;
    setCreating(true);
    const result = await createProject({
      name: createName.trim(),
      description: createDescription.trim(),
    });
    setCreating(false);
    if (result.success) {
      toast.success(`Project "${createName}" created.`);
      setShowCreateModal(false);
      setCreateName('');
      setCreateDescription('');
      handleRefresh();
    } else {
      toast.error(result.error || 'Failed to create project');
    }
  };

  const openDelete = (id, name) => setDeleteState({ open: true, id, name, loading: false });
  const closeDelete = () => setDeleteState({ open: false, id: null, name: '', loading: false });

  const openEdit = (project) => {
    setEditState({
      open: true,
      id: project.id,
      name: project.name || '',
      description: project.description || '',
      loading: false,
    });
  };

  const closeEdit = () => {
    setEditState({ open: false, id: null, name: '', description: '', loading: false });
  };

  const confirmEdit = async (e) => {
    e?.preventDefault();
    if (!editState.id || !editState.name.trim()) return;
    setEditState((state) => ({ ...state, loading: true }));
    try {
      await projectService.update(editState.id, {
        name: editState.name.trim(),
        description: editState.description.trim(),
      });
      toast.success('Project updated.');
      closeEdit();
      handleRefresh();
    } catch (err) {
      toast.error(err?.response?.data?.detail || err?.message || 'Failed to update project.');
      setEditState((state) => ({ ...state, loading: false }));
    }
  };

  const confirmDelete = async () => {
    if (!deleteState.id) return;
    setDeleteState((s) => ({ ...s, loading: true }));
    const result = await deleteProject(deleteState.id);
    if (result.success) {
      toast.success('Project deleted.');
      closeDelete();
      handleRefresh();
    } else {
      toast.error(result.error || 'Failed to delete project');
      setDeleteState((s) => ({ ...s, loading: false }));
    }
  };

  const deleteConsequences = deleteState.id
    ? []
    : [];

  const sortLabel = SORT_OPTIONS.find((o) => o.key === sortKey)?.label || 'Sort';
  const sidebarPanel = (
    <>
      <section className="rounded-xl border border-line bg-surface-raised p-3 shadow-card">
        <Button
          variant="primary"
          size="md"
          fullWidth
          leftIcon={<Plus className="h-4 w-4" />}
          onClick={() => setShowCreateModal(true)}
        >
          New project
        </Button>
      </section>

      <CollapsiblePanel title="Workspace stats" open={statsExpanded} onToggle={() => setStatsExpanded((open) => !open)}>
        <div className="grid grid-cols-2 gap-2">
          <MiniMetric label="Projects" value={totals.count} />
          <MiniMetric label="Active" value={activeCount} />
          <MiniMetric label="Files" value={statsLoading ? '...' : totals.totalFiles.toLocaleString()} />
          <MiniMetric label="Storage" value={statsLoading ? '...' : formatBytes(totals.totalSize)} />
        </div>
      </CollapsiblePanel>

      {projectsArray.length > 0 && (
        <section className="rounded-xl border border-line bg-surface-raised p-2 shadow-card">
          {TABS.map((item) => {
            const isActive = tab === item.key;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => {
                  setTab(item.key);
                  setSidebarOpen(false);
                }}
                className={cn(
                  'flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition-smooth duration-300',
                  isActive ? 'bg-surface-muted text-ink shadow-card' : 'text-ink-muted hover:bg-surface-muted/70 hover:text-ink'
                )}
              >
                <span className="font-medium">{item.label}</span>
                <span className={cn(
                  'inline-flex h-6 min-w-6 items-center justify-center rounded-full px-1.5 text-[11px] font-semibold tabular-nums',
                  isActive ? 'bg-primary-500/15 text-primary-700 dark:text-primary-300' : 'bg-surface-muted text-ink-subtle'
                )}>
                  {tabCounts[item.key] ?? 0}
                </span>
              </button>
            );
          })}
        </section>
      )}

      {projectsArray.length > 0 && (
        <CollapsiblePanel title="Charts" open={chartsExpanded} onToggle={() => setChartsExpanded((open) => !open)}>
          <CompactProjectCharts
            projects={projectsArray}
            statsByProject={statsByProject}
            statsLoading={statsLoading}
          />
        </CollapsiblePanel>
      )}
    </>
  );

  /* ────────────────────────── */

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
      <div className="mb-4 flex items-center justify-between gap-3 lg:hidden">
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<SlidersHorizontal className="h-4 w-4" />}
          onClick={() => setSidebarOpen(true)}
        >
          Workspace stats
        </Button>
      </div>

      <AnimatePresence>
        {sidebarOpen && (
          <>
            <motion.div
              className="fixed inset-0 z-40 bg-slate-950/50 backdrop-blur-sm lg:hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setSidebarOpen(false)}
            />
            <motion.aside
              className="fixed bottom-0 left-0 top-0 z-50 w-[86vw] max-w-sm overflow-y-auto border-r border-line bg-surface p-4 shadow-pop lg:hidden"
              initial={{ x: '-100%' }}
              animate={{ x: 0 }}
              exit={{ x: '-100%' }}
              transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
            >
              <div className="mb-3 flex items-center justify-between">
                <p className="text-sm font-semibold text-ink">Workspace</p>
                <IconButton label="Close workspace panel" tone="ghost" size="sm" onClick={() => setSidebarOpen(false)}>
                  <X className="h-4 w-4" />
                </IconButton>
              </div>
              <div className="space-y-4">{sidebarPanel}</div>
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      <div className="grid gap-5 lg:grid-cols-[18rem_minmax(0,1fr)] xl:grid-cols-[19rem_minmax(0,1fr)]">
        <aside className="hidden space-y-4 lg:sticky lg:top-20 lg:block lg:self-start">
          {sidebarPanel}
          <div className="hidden">
          <section className="rounded-xl border border-line bg-surface-raised p-4 shadow-card">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-subtle">
                  Workspace
                </p>
                <h1 className="mt-1 text-lg font-bold text-ink">Graduation project</h1>
                <p className="mt-1 text-xs leading-5 text-ink-muted">
                  Projects, files, storage, and generated backend docs.
                </p>
              </div>
              <span className="inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border border-line bg-surface-muted text-primary-600 dark:text-primary-300">
                <Activity className="h-4 w-4" />
              </span>
            </div>
            <Button
              variant="primary"
              size="sm"
              fullWidth
              className="mt-4"
              leftIcon={<Plus className="h-4 w-4" />}
              onClick={() => setShowCreateModal(true)}
            >
              New project
            </Button>
          </section>

          <section className="grid grid-cols-2 gap-2">
            <MiniMetric label="Projects" value={totals.count} />
            <MiniMetric label="Active" value={activeCount} />
            <MiniMetric label="Files" value={statsLoading ? '...' : totals.totalFiles.toLocaleString()} />
            <MiniMetric label="Storage" value={statsLoading ? '...' : formatBytes(totals.totalSize)} />
          </section>

          {projectsArray.length > 0 && (
            <section className="rounded-xl border border-line bg-surface-raised p-2 shadow-card">
              {TABS.map((item) => {
                const isActive = tab === item.key;
                return (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setTab(item.key)}
                    className={cn(
                      'flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition-smooth',
                      isActive
                        ? 'bg-surface-muted text-ink shadow-card'
                        : 'text-ink-muted hover:bg-surface-muted/70 hover:text-ink'
                    )}
                  >
                    <span className="font-medium">{item.label}</span>
                    <span
                      className={cn(
                        'inline-flex h-6 min-w-6 items-center justify-center rounded-full px-1.5 text-[11px] font-semibold tabular-nums',
                        isActive
                          ? 'bg-primary-500/15 text-primary-700 dark:text-primary-300'
                          : 'bg-surface-muted text-ink-subtle'
                      )}
                    >
                      {tabCounts[item.key] ?? 0}
                    </span>
                  </button>
                );
              })}
            </section>
          )}

          {projectsArray.length > 0 && (
            <CompactProjectCharts
              projects={projectsArray}
              statsByProject={statsByProject}
              statsLoading={statsLoading}
            />
          )}
          </div>
        </aside>

        <main className="min-w-0">
      <motion.section
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="hidden"
      >
        <div className="relative grid gap-0 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <div className="p-5 sm:p-6">
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface-muted px-2.5 py-1 text-xs font-medium text-ink-muted">
                <span className="h-2 w-2 rounded-full bg-emerald-500" />
                Online
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-muted px-2.5 py-1 text-xs text-ink-muted">
                <Clock className="h-3.5 w-3.5" />
                Last active <span className="font-semibold text-ink">{lastActivity}</span>
              </span>
            </div>

            <div className="max-w-3xl">
              <p className="text-sm font-semibold text-primary-600 dark:text-primary-300">
                Graduation project workspace
              </p>
              <h1 className="mt-2 text-2xl font-bold leading-tight tracking-tight text-ink sm:text-3xl">
                Projects, documentation, and code generation in one place.
              </h1>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-ink-muted">
                Review generated backends, inspect API docs, compare project size, and continue work without searching through folders manually.
              </p>
            </div>

            <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:items-center">
              <Button
                variant="primary"
                size="lg"
                leftIcon={<Plus className="h-4 w-4" />}
                onClick={() => setShowCreateModal(true)}
              >
                New project
              </Button>
              {featured && (
                <Button
                  variant="secondary"
                  size="lg"
                  rightIcon={<ArrowUpRight className="h-4 w-4" />}
                  onClick={() => navigate(`/projects/${featured.id}`)}
                >
                  Continue recent
                </Button>
              )}
            </div>
          </div>

          <div className="border-t border-line bg-surface-muted/45 p-5 lg:border-l lg:border-t-0">
            <div className="rounded-xl border border-line bg-surface-raised p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-subtle">
                    Workspace
                  </p>
                  <p className="mt-1 text-sm font-semibold text-ink">Current overview</p>
                </div>
                <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-line bg-surface-muted text-primary-600 dark:text-primary-300">
                  <Activity className="h-5 w-5" />
                </span>
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2">
                <MiniMetric label="Projects" value={totals.count} />
                <MiniMetric label="Active" value={activeCount} />
                <MiniMetric label="Files" value={statsLoading ? '—' : totals.totalFiles.toLocaleString()} />
              </div>
              <div className="mt-4 rounded-lg border border-line bg-surface-muted/60 p-3 text-xs leading-5 text-ink-muted">
                Search, sort, or continue your last project from here.
              </div>
            </div>
          </div>
        </div>
      </motion.section>

      <section className="hidden">
        <StatTile
          icon={Folder}
          label="Projects"
          value={totals.count}
          detail="Total workspaces"
          tint="from-primary-500 to-primary-600"
          delay={0}
        />
        <StatTile
          icon={FileText}
          label="Total files"
          value={statsLoading ? '—' : totals.totalFiles.toLocaleString()}
          detail="Indexed project files"
          tint="from-sky-500 to-primary-500"
          delay={0.04}
        />
        <StatTile
          icon={Database}
          label="Storage"
          value={statsLoading ? '—' : formatBytes(totals.totalSize)}
          detail="Across all projects"
          tint="from-slate-500 to-slate-700"
          delay={0.08}
        />
        <StatTile
          icon={Activity}
          label="Active in 7d"
          value={statsLoading ? '—' : activeCount}
          detail="Recent movement"
          tint="from-emerald-500 to-teal-600"
          delay={0.12}
        />
      </section>

      {false && projectsArray.length > 0 && (
        <ProjectCharts
          projects={projectsArray}
          statsByProject={statsByProject}
          statsLoading={statsLoading}
        />
      )}

      {/* ───────── Body — single column, full width ───────── */}
      <div>
        {/* Featured project */}
        {false && featured && projectsArray.length > 0 && (
          <FeaturedProject
            project={featured}
            stats={statsByProject[featured.id]}
            statsLoading={statsLoading}
            onOpen={() => navigate(`/projects/${featured.id}`)}
          />
        )}

        {/* Tabs (with live counts) */}
        {false && projectsArray.length > 0 && (
          <Tabs items={TABS} value={tab} onChange={setTab} counts={tabCounts} />
        )}

        {/* Toolbar */}
        {projectsArray.length > 0 && (
          <section className="relative mb-4 overflow-hidden rounded-xl border border-line bg-surface-raised p-4 shadow-card transition-all duration-300 hover:border-line-strong">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-ink-subtle">
                  Project directory
                </p>
                <div className="mt-1 flex items-center gap-2 text-xs text-ink-subtle">
                  <Badge tone="primary">{filtered.length}</Badge>
                  <span>
                    {filtered.length === projectsArray.length
                      ? 'projects available'
                      : `of ${projectsArray.length} projects shown`}
                  </span>
                </div>
              </div>
              <Button
                variant="primary"
                size="sm"
                leftIcon={<Plus className="h-4 w-4" />}
                onClick={() => setShowCreateModal(true)}
              >
                New project
              </Button>
            </div>

            <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
              <div className="min-w-0 flex-1">
                <Input
                  ref={searchRef}
                  placeholder="Search by project name, description, or code..."
                  leftIcon={<Search className="h-4 w-4" />}
                  rightSlot={
                    <kbd className="hidden sm:inline-flex items-center rounded border border-line bg-surface-muted px-1.5 py-0.5 text-[10px] font-mono text-ink-subtle">
                      Ctrl K
                    </kbd>
                  }
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>

              <div className="flex flex-wrap items-center gap-2 sm:flex-nowrap">
                {/* Sort dropdown */}
                <div className={cn('relative', sortMenuOpen && 'z-50')} ref={sortMenuRef}>
                  <button
                    onClick={(e) => {
                      if (!sortMenuOpen) {
                        const rect = e.currentTarget.getBoundingClientRect();
                        setSortMenuPosition({
                          top: rect.bottom + 8,
                          right: Math.max(8, window.innerWidth - rect.right),
                        });
                      }
                      setSortMenuOpen((o) => !o);
                    }}
                    aria-haspopup="menu"
                    aria-expanded={sortMenuOpen}
                    className={cn(
                      'inline-flex items-center gap-2 h-10 px-3 rounded-lg',
                      'border border-line bg-surface-raised text-sm font-medium text-ink',
                      'hover:border-line-strong shadow-card transition-smooth'
                    )}
                  >
                    <ArrowUpDown className="h-4 w-4 text-ink-subtle" />
                    <span className="hidden sm:inline">{sortLabel}</span>
                  </button>
                  {createPortal(
                    <AnimatePresence>
                      {sortMenuOpen && (
                        <motion.div
                          ref={sortMenuPanelRef}
                          role="menu"
                          initial={{ opacity: 0, y: -6, scale: 0.97 }}
                          animate={{ opacity: 1, y: 0, scale: 1, transition: { duration: 0.18, ease: [0.16, 1, 0.3, 1] } }}
                          exit={{ opacity: 0, y: -4, scale: 0.98, transition: { duration: 0.12 } }}
                          className="fixed z-[1000] w-56 origin-top-right rounded-xl border border-line bg-surface-raised p-1 shadow-pop"
                          style={{ top: sortMenuPosition.top, right: sortMenuPosition.right }}
                        >
                          {SORT_OPTIONS.map((opt) => (
                            <button
                              key={opt.key}
                              role="menuitemradio"
                              aria-checked={sortKey === opt.key}
                              onClick={() => { setSortKey(opt.key); setSortMenuOpen(false); }}
                              className={cn(
                                'w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-sm transition-colors',
                                sortKey === opt.key
                                  ? 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300'
                                  : 'text-ink-muted hover:bg-surface-muted hover:text-ink'
                              )}
                            >
                              <span>{opt.label}</span>
                              {sortKey === opt.key && <CheckCircle2 className="h-4 w-4" />}
                            </button>
                          ))}
                        </motion.div>
                      )}
                    </AnimatePresence>,
                    document.body
                  )}
                </div>

                <IconButton
                  label="Refresh"
                  onClick={handleRefresh}
                  disabled={refreshing}
                  tone="subtle"
                >
                  <RefreshCw className={cn('h-4 w-4', refreshing && 'animate-spin')} />
                </IconButton>
              </div>
            </div>

          </section>
        )}

        {/* Body */}
        {(loading || refreshing) && projectsArray.length === 0 ? (
          <div id="project-results" className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        ) : projectsArray.length === 0 ? (
          <EmptyState
            icon={Folder}
            title="No projects yet"
            description="Spin up your first workspace and start chatting with the assistant."
            action={
              <Button
                variant="primary"
                size="lg"
                leftIcon={<Plus className="h-4 w-4" />}
                onClick={() => setShowCreateModal(true)}
              >
                Create your first project
              </Button>
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={Search}
            title="Nothing matches"
            description={
              search
                ? `No projects matched "${search}". Try a different keyword or switch tabs.`
                : 'No projects in this view. Try a different tab.'
            }
            action={
              search ? (
                <Button variant="secondary" onClick={() => setSearch('')}>
                  Clear search
                </Button>
              ) : (
                <Button variant="secondary" onClick={() => setTab('all')}>
                  Show all projects
                </Button>
              )
            }
          />
        ) : (
          <motion.div
            key={`${tab}-${sortKey}-${safePage}-${search}`}
            id="project-results"
            initial={{ opacity: 0, y: 10, filter: 'blur(3px)' }}
            animate={{ opacity: 1, y: 0, filter: 'blur(0px)', transition: { duration: 0.36, ease: [0.16, 1, 0.3, 1] } }}
            className="min-w-0"
          >
            <ProjectTable
              rows={pagedProjects}
              statsByProject={statsByProject}
              statsLoading={statsLoading}
              sortKey={sortKey}
              onSort={setSortKey}
              onOpen={(id) => navigate(`/projects/${id}`)}
              onEdit={openEdit}
              onDelete={(id, name) => openDelete(id, name)}
            />
          </motion.div>
        )}
        {filtered.length > PAGE_SIZE && (
          <Pagination
            page={safePage}
            pageCount={pageCount}
            total={filtered.length}
            pageSize={PAGE_SIZE}
            onPageChange={(nextPage) => {
              setPage(nextPage);
              window.requestAnimationFrame(() => {
                document.getElementById('project-results')?.scrollIntoView({
                  behavior: 'smooth',
                  block: 'start',
                });
              });
            }}
          />
        )}
      </div>

      {/* ───────── Create dialog ───────── */}
        </main>
      </div>

      <Modal
        open={showCreateModal}
        onClose={() => !creating && setShowCreateModal(false)}
        title="New project"
        description="Create a clean workspace for generated files, docs, and chat."
        size="lg"
        bodyClassName="pt-0"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowCreateModal(false)} disabled={creating}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={handleCreate}
              loading={creating}
              leftIcon={!creating && <Sparkles className="h-4 w-4" />}
              disabled={!createName.trim()}
            >
              Create project
            </Button>
          </>
        }
      >
        <form onSubmit={handleCreate} className="space-y-4">
            <Input
              label="Project name"
              placeholder="Customer Orders API"
              value={createName}
              onChange={(e) => setCreateName(e.target.value)}
              autoFocus
              required
              maxLength={60}
              hint={`${createName.length}/60 characters`}
            />
            <Textarea
              label="Description"
              placeholder="Short scope, important entities, or notes for the assistant."
              value={createDescription}
              onChange={(e) => setCreateDescription(e.target.value)}
              rows={4}
              maxLength={500}
              hint="Optional, but useful when generating or reviewing project files."
            />
            <div className="rounded-xl border border-line bg-surface-muted/60 px-3 py-2.5 text-xs leading-5 text-ink-muted">
              The project starts empty. Upload files or generate code from inside the project workspace.
            </div>
        </form>
      </Modal>

      <Modal
        open={editState.open}
        onClose={() => !editState.loading && closeEdit()}
        title="Edit project"
        description="Update the name and description shown in your project list."
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={closeEdit} disabled={editState.loading}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={confirmEdit}
              loading={editState.loading}
              disabled={!editState.name.trim()}
              leftIcon={!editState.loading && <Edit3 className="h-4 w-4" />}
            >
              Save changes
            </Button>
          </>
        }
      >
        <form onSubmit={confirmEdit} className="space-y-4">
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

      <DeleteConfirmation
        isOpen={deleteState.open}
        onClose={() => !deleteState.loading && closeDelete()}
        onConfirm={confirmDelete}
        loading={deleteState.loading}
        title="Delete project"
        message="This will permanently remove the project and everything attached to it."
        itemName={deleteState.name}
        consequences={deleteConsequences}
      />
    </div>
  );
};

/* ────────────────────────────────────────────────────────────
 * Stat tile (top row) — gradient icon chip + label + value
 * ──────────────────────────────────────────────────────────── */
const MiniMetric = ({ label, value }) => (
  <div className="rounded-xl border border-line bg-surface-muted/60 px-3 py-2">
    <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-subtle">{label}</p>
    <p className="mt-1 truncate text-sm font-bold tabular-nums text-ink">{value}</p>
  </div>
);

const CollapsiblePanel = ({ title, open, onToggle, children }) => (
  <section className="rounded-xl border border-line bg-surface-raised shadow-card">
    <button
      type="button"
      onClick={onToggle}
      className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
    >
      <span className="text-sm font-semibold text-ink">{title}</span>
      <ChevronDown className={cn('h-4 w-4 text-ink-subtle transition-transform duration-300', open && 'rotate-180')} />
    </button>
    <AnimatePresence initial={false}>
      {open && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
          className="overflow-hidden"
        >
          <div className="px-4 pb-4">{children}</div>
        </motion.div>
      )}
    </AnimatePresence>
  </section>
);

const CompactProjectCharts = ({ projects, statsByProject, statsLoading }) => {
  const rows = useMemo(
    () =>
      projects
        .map((project) => ({
          id: project.id,
          name: project.name || 'Untitled',
          files: statsByProject[project.id]?.files || 0,
          size: statsByProject[project.id]?.size || 0,
        }))
        .sort((a, b) => b.files - a.files || b.size - a.size)
        .slice(0, 5),
    [projects, statsByProject]
  );

  const maxFiles = Math.max(1, ...rows.map((row) => row.files));
  const maxSize = Math.max(1, ...rows.map((row) => row.size));

  return (
    <section className="space-y-3 rounded-xl border border-line bg-surface-raised p-4 shadow-card">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-subtle">
            Stats
          </p>
          <p className="mt-0.5 text-sm font-semibold text-ink">Project signals</p>
        </div>
        <BarChart3 className="h-4 w-4 text-ink-subtle" />
      </div>

      <CompactBarGroup
        title="Files by project"
        rows={rows}
        valueKey="files"
        max={maxFiles}
        formatter={(value) => (statsLoading ? '...' : value.toLocaleString())}
      />
      <CompactBarGroup
        title="Storage by project"
        rows={rows}
        valueKey="size"
        max={maxSize}
        formatter={(value) => (statsLoading ? '...' : formatBytes(value))}
      />
    </section>
  );
};

const CompactBarGroup = ({ title, rows, valueKey, max, formatter }) => (
  <div className="rounded-lg border border-line bg-surface-muted/45 p-3">
    <p className="mb-2 text-xs font-semibold text-ink">{title}</p>
    {rows.length === 0 ? (
      <p className="text-xs text-ink-subtle">No project data yet.</p>
    ) : (
      <div className="space-y-2">
        {rows.map((row) => {
          const value = row[valueKey] || 0;
          return (
            <div key={`${valueKey}-${row.id}`} className="min-w-0">
              <div className="mb-1 flex items-center justify-between gap-2 text-[11px]">
                <span className="min-w-0 truncate text-ink-muted">{row.name}</span>
                <span className="font-semibold tabular-nums text-ink">{formatter(value)}</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-surface-raised">
                <div
                  className="h-full rounded-full bg-primary-500/70"
                  style={{ width: `${Math.max(5, Math.round((value / max) * 100))}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    )}
  </div>
);

const StatTile = ({ icon: Icon, label, value, detail, tint, delay = 0 }) => (
  <motion.div
    initial={{ opacity: 0, y: 10 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ duration: 0.5, delay, ease: [0.16, 1, 0.3, 1] }}
    className="group relative overflow-hidden rounded-xl border border-line bg-surface-raised p-4 shadow-card transition-smooth hover:border-line-strong"
  >
    <div className="relative flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-ink-subtle">{label}</p>
        <p className="mt-1 text-2xl font-bold tabular-nums leading-tight tracking-tight sm:text-3xl">
          {value}
        </p>
        {detail && <p className="mt-1 truncate text-xs text-ink-subtle">{detail}</p>}
      </div>
      <span className={cn(
        'inline-flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl text-white',
        'bg-gradient-to-br', tint
      )}>
        <Icon className="h-4 w-4" />
      </span>
    </div>
  </motion.div>
);

const ProjectCharts = ({ projects, statsByProject, statsLoading }) => {
  const rows = useMemo(
    () =>
      projects
        .map((project) => ({
          id: project.id,
          name: project.name || 'Untitled',
          files: statsByProject[project.id]?.files || 0,
          size: statsByProject[project.id]?.size || 0,
        }))
        .sort((a, b) => b.files - a.files)
        .slice(0, 6),
    [projects, statsByProject]
  );

  const maxFiles = Math.max(1, ...rows.map((row) => row.files));
  const maxSize = Math.max(1, ...rows.map((row) => row.size));

  return (
    <section className="mb-6 grid gap-4 lg:grid-cols-2">
      <ChartPanel
        icon={BarChart3}
        title="Files per project"
        description="Top projects by generated or uploaded files."
        loading={statsLoading}
        rows={rows}
        valueOf={(row) => row.files.toLocaleString()}
        widthOf={(row) => `${Math.max(4, (row.files / maxFiles) * 100)}%`}
      />
      <ChartPanel
        icon={PieChart}
        title="Storage distribution"
        description="Largest workspaces by project size."
        loading={statsLoading}
        rows={[...rows].sort((a, b) => b.size - a.size)}
        valueOf={(row) => formatBytes(row.size)}
        widthOf={(row) => `${Math.max(4, (row.size / maxSize) * 100)}%`}
      />
    </section>
  );
};

const ChartPanel = ({ icon: Icon, title, description, rows, loading, valueOf, widthOf }) => (
  <div className="rounded-xl border border-line bg-surface-raised p-4 shadow-card">
    <div className="mb-4 flex items-start justify-between gap-3">
      <div>
        <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
          <Icon className="h-4 w-4 text-primary-600 dark:text-primary-300" />
          {title}
        </h2>
        <p className="mt-1 text-xs text-ink-subtle">{description}</p>
      </div>
    </div>
    <div className="space-y-3">
      {loading ? (
        <div className="rounded-lg border border-line bg-surface-muted p-4 text-sm text-ink-subtle">
          Loading chart data...
        </div>
      ) : rows.length === 0 ? (
        <div className="rounded-lg border border-line bg-surface-muted p-4 text-sm text-ink-subtle">
          No project data yet.
        </div>
      ) : (
        rows.map((row) => (
          <div key={row.id} className="grid gap-1.5">
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="min-w-0 truncate font-medium text-ink-muted">{row.name}</span>
              <span className="flex-shrink-0 font-mono text-ink">{valueOf(row)}</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-surface-muted">
              <div
                className="h-full rounded-full bg-primary-500 transition-[width] duration-500 ease-out-soft"
                style={{ width: widthOf(row) }}
              />
            </div>
          </div>
        ))
      )}
    </div>
  </div>
);

/* ────────────────────────────────────────────────────────────
 * Featured project — most-recently-updated, highlighted
 * ──────────────────────────────────────────────────────────── */
const FeaturedProject = ({ project, stats, statsLoading, onOpen }) => (
  <motion.div
    initial={{ opacity: 0, y: 12 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
    className="relative mb-5 overflow-hidden rounded-xl border border-line bg-surface-raised shadow-card"
  >
    <div className="pointer-events-none absolute inset-y-0 left-0 w-1 bg-primary-500/70" />

    <div className="relative grid gap-5 p-5 sm:p-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
      <div className="flex min-w-0 gap-4">
        <div className="hidden h-12 w-12 flex-shrink-0 items-center justify-center rounded-2xl border border-line bg-surface-muted text-primary-600 dark:text-primary-300 sm:inline-flex">
          <Star className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-[0.14em] text-primary-600 dark:text-primary-300">
              Continue where you left off
            </span>
            {project.project_code && <Badge tone="primary" className="font-mono">{project.project_code}</Badge>}
          </div>
          <h2 className="mt-2 truncate text-xl font-bold tracking-tight text-ink sm:text-2xl">
            {project.name}
          </h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-ink-muted line-clamp-2">
            {project.description || 'No description yet. Add one so the assistant has stronger project context.'}
          </p>

          <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
          <span className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-muted px-2.5 py-1.5">
            <FileText className="h-3.5 w-3.5" />
            <span className="font-semibold text-ink tabular-nums">
              {statsLoading ? '…' : (stats?.files ?? 0).toLocaleString()}
            </span>
            files
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-muted px-2.5 py-1.5">
            <HardDrive className="h-3.5 w-3.5" />
            <span className="font-semibold text-ink">
              {statsLoading ? '…' : formatBytes(stats?.size ?? 0)}
            </span>
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-muted px-2.5 py-1.5">
            <Clock className="h-3.5 w-3.5" />
            <span className="font-semibold text-ink">
              {formatRelativeOrDate(project.updated_at || project.created_at)}
            </span>
          </span>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 lg:justify-end">
        <Button
          variant="primary"
          size="lg"
          onClick={onOpen}
          rightIcon={<ArrowUpRight className="h-4 w-4" />}
        >
          Open workspace
        </Button>
      </div>
    </div>
  </motion.div>
);

/* ────────────────────────────────────────────────────────────
 * Tabs — animated underline pill, with optional counts
 * ──────────────────────────────────────────────────────────── */
const Tabs = ({ items, value, onChange, counts = {} }) => (
  <div className="relative mb-5 rounded-xl border border-line bg-surface-raised p-1.5 shadow-card">
    <div className="flex items-center gap-1 overflow-x-auto scrollbar-fancy">
      {items.map((t) => {
        const active = value === t.key;
        const count = counts[t.key];
        return (
          <button
            key={t.key}
            onClick={() => onChange(t.key)}
            className={cn(
              'relative inline-flex h-10 items-center gap-2 rounded-lg px-3.5 text-sm font-medium transition-colors whitespace-nowrap',
              active ? 'text-ink' : 'text-ink-muted hover:text-ink'
            )}
          >
            {active && (
              <motion.span
                layoutId="dashboard-tab-pill"
                className="absolute inset-0 rounded-lg bg-surface-muted"
                transition={{ type: 'spring', stiffness: 380, damping: 30 }}
              />
            )}
            <span className="relative">{t.label}</span>
            {typeof count === 'number' && (
              <span
                className={cn(
                  'relative inline-flex h-5 min-w-[1.4rem] items-center justify-center rounded-full px-1.5 text-[11px] font-semibold tabular-nums transition-colors',
                  active
                    ? 'bg-primary-500/15 text-primary-700 dark:bg-primary-500/20 dark:text-primary-300'
                    : 'bg-surface-muted text-ink-subtle'
                )}
              >
                {count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  </div>
);

const Pagination = ({ page, pageCount, total, pageSize, onPageChange }) => {
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(total, page * pageSize);
  const pages = Array.from({ length: pageCount }, (_, index) => index + 1)
    .filter((item) => item === 1 || item === pageCount || Math.abs(item - page) <= 1);

  return (
    <div className="mt-5 rounded-xl border border-line bg-surface-raised px-4 py-3 shadow-card">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm text-ink-muted">
            Showing <span className="font-semibold text-ink">{start}-{end}</span> of{' '}
            <span className="font-semibold text-ink">{total}</span> projects
          </p>
          <p className="mt-0.5 text-xs text-ink-subtle">
            Page <span className="font-semibold text-ink">{page}</span> of {pageCount}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
        <IconButton
          label="Previous page"
          tone="subtle"
          disabled={page <= 1}
          onClick={() => onPageChange(Math.max(1, page - 1))}
        >
          <ChevronLeft className="h-4 w-4" />
        </IconButton>
        {pages.map((item, index) => {
          const previous = pages[index - 1];
          const hasGap = previous && item - previous > 1;
          return (
            <React.Fragment key={item}>
              {hasGap && <span className="px-1 text-sm text-ink-subtle">...</span>}
              <button
                type="button"
                onClick={() => onPageChange(item)}
                className={cn(
                  'inline-flex h-9 min-w-9 items-center justify-center rounded-lg border px-2 text-sm font-semibold transition-smooth duration-300',
                  page === item
                    ? 'border-primary-300 bg-primary-50 text-primary-700 dark:border-primary-500/30 dark:bg-primary-500/10 dark:text-primary-300'
                    : 'border-line bg-surface-raised text-ink-muted hover:border-line-strong hover:text-ink'
                )}
              >
                {item}
              </button>
            </React.Fragment>
          );
        })}
        <IconButton
          label="Next page"
          tone="subtle"
          disabled={page >= pageCount}
          onClick={() => onPageChange(Math.min(pageCount, page + 1))}
        >
          <ChevronRight className="h-4 w-4" />
        </IconButton>
        </div>
      </div>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-muted">
        <div
          className="h-full rounded-full bg-primary-500 transition-[width] duration-500 ease-out"
          style={{ width: `${Math.max(4, (page / pageCount) * 100)}%` }}
        />
      </div>
    </div>
  );
};

/* ────────────────────────────────────────────────────────────
 * Project card (grid view)
 * ──────────────────────────────────────────────────────────── */
// eslint-disable-next-line no-unused-vars
const ProjectCard = ({ project, stats, statsLoading, onOpen, onDelete, animationDelay = 0 }) => (
  <Card
    interactive
    className="group relative flex min-h-[14rem] flex-col overflow-hidden animate-slide-up"
    style={{ animationDelay: `${animationDelay}ms` }}
  >
    <div className="relative flex flex-1 flex-col p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border border-line bg-surface-muted text-primary-600 transition-smooth group-hover:border-primary-300 dark:text-primary-300">
            <Folder className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <button
              onClick={onOpen}
              className="max-w-full truncate text-left text-sm font-semibold text-ink transition-colors hover:text-primary-600"
            >
              {project.name}
            </button>
            <p className="mt-0.5 text-xs text-ink-subtle">
              Updated {formatRelativeOrDate(project.updated_at || project.created_at)}
            </p>
          </div>
        </div>
        <IconButton label="Delete project" tone="danger" size="sm" onClick={onDelete}>
          <Trash2 className="h-4 w-4" />
        </IconButton>
      </div>

      <p className="mt-3 min-h-[2.5rem] text-sm leading-5 text-ink-muted line-clamp-2 whitespace-pre-line">
        {project.description || 'No description provided.'}
      </p>

      <div className="mt-auto pt-4">
        <div className="flex flex-wrap items-center gap-2">
          {project.project_code && <Badge tone="primary" className="font-mono">{project.project_code}</Badge>}
          <span className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-muted px-2 py-1 text-xs text-ink-muted">
            <Activity className="h-3 w-3" />
            Ready
          </span>
        </div>
      </div>
    </div>

    <div className="relative grid grid-cols-3 divide-x divide-line border-y border-line bg-surface-muted/45 text-center">
      <Cell icon={FileText} label="Files" value={statsLoading ? '…' : stats?.files ?? 0} />
      <Cell icon={HardDrive} label="Size" value={statsLoading ? '…' : formatBytes(stats?.size ?? 0)} />
      <Cell icon={Calendar} label="Created" value={formatRelativeOrDate(project.created_at)} />
    </div>

    <div className="relative p-3">
      <Button
        variant="secondary"
        size="sm"
        fullWidth
        onClick={onOpen}
        rightIcon={<ArrowUpRight className="h-4 w-4" />}
      >
        Open project
      </Button>
    </div>
  </Card>
);

const Cell = ({ icon: Icon, label, value }) => (
  <div className="px-2 py-2.5">
    <Icon className="h-3 w-3 text-ink-subtle mx-auto mb-1" />
    <p className="text-[10px] font-medium uppercase tracking-wider text-ink-subtle">{label}</p>
    <p className="text-xs font-semibold text-ink mt-0.5 truncate">{value}</p>
  </div>
);

/* ────────────────────────────────────────────────────────────
 * Project Table (list view)
 * ──────────────────────────────────────────────────────────── */
const ProjectTable = ({
  rows,
  statsByProject,
  statsLoading,
  sortKey,
  onSort,
  onOpen,
  onEdit,
  onDelete,
}) => {
  const [openMenu, setOpenMenu] = useState(null);
  const menuRef = useRef(null);

  useEffect(() => {
    const onClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setOpenMenu(null);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  return (
    <Card className={cn('relative overflow-visible p-0', openMenu && 'z-30')}>
      <div className="hidden md:grid grid-cols-[2.4fr_1fr_0.9fr_0.9fr_1.1fr_4rem] gap-4 px-5 py-3 border-b border-line bg-surface-muted/70 text-[11px] font-bold uppercase tracking-[0.14em] text-ink-subtle">
        <SortHeader label="Project"  active={sortKey === 'name' || sortKey === 'name_z'} dir={sortKey === 'name' ? 'asc' : sortKey === 'name_z' ? 'desc' : null} onClick={() => onSort(sortKey === 'name' ? 'name_z' : 'name')} />
        <span>Code</span>
        <SortHeader label="Files" active={sortKey === 'files'} dir="desc" onClick={() => onSort('files')} className="justify-end text-right" />
        <SortHeader label="Size"  active={sortKey === 'size'}  dir="desc" onClick={() => onSort('size')}  className="justify-end text-right" />
        <SortHeader label="Updated" active={sortKey === 'updated'} dir="desc" onClick={() => onSort('updated')} />
        <span className="text-right" />
      </div>

      <ul className="divide-y divide-line overflow-visible">
        <AnimatePresence initial={false}>
          {rows.map((project, i) => {
            const stats = statsByProject[project.id];
            return (
              <motion.li
                key={project.id}
                layout
                initial={{ opacity: 0, y: 8, filter: 'blur(2px)' }}
                animate={{ opacity: 1, y: 0, filter: 'blur(0px)', transition: { duration: 0.34, delay: i * 0.025, ease: [0.16, 1, 0.3, 1] } }}
                exit={{ opacity: 0, y: -4, transition: { duration: 0.18 } }}
                className={cn(
                  'group relative transition-flow hover:bg-surface-muted/45',
                  openMenu === project.id && 'z-40'
                )}
              >
                <button
                  onClick={() => onOpen(project.id)}
                  className="w-full text-left grid grid-cols-1 md:grid-cols-[2.4fr_1fr_0.9fr_0.9fr_1.1fr_4rem] gap-4 px-4 py-3.5 transition-all duration-300 focus-visible:bg-surface-muted/60 outline-none sm:px-5"
                >
                  <div className="flex items-center gap-3.5 min-w-0">
                    <span className="inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border border-line bg-surface-raised text-primary-600 shadow-card transition-all duration-300 group-hover:-translate-y-0.5 group-hover:border-primary-300 dark:text-primary-300">
                      <Folder className="h-4 w-4" />
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-ink truncate transition-colors duration-300 group-hover:text-primary-700 dark:group-hover:text-primary-300">{project.name}</p>
                      <p className="mt-0.5 text-xs text-ink-muted truncate">
                        {project.description || 'No description'}
                      </p>
                    </div>
                  </div>

                  <div className="hidden md:flex items-center min-w-0">
                    {project.project_code && (
                      <span className="inline-flex max-w-full items-center rounded-md border border-line bg-surface-raised px-2.5 py-1 font-mono text-xs font-semibold text-ink-muted shadow-card">
                        <span className="truncate">{project.project_code}</span>
                      </span>
                    )}
                  </div>

                  <div className="hidden md:flex items-center justify-end text-sm font-semibold text-ink tabular-nums">
                    {statsLoading ? '…' : (stats?.files ?? 0).toLocaleString()}
                  </div>

                  <div className="hidden md:flex items-center justify-end text-sm font-semibold text-ink tabular-nums">
                    {statsLoading ? '…' : formatBytes(stats?.size ?? 0)}
                  </div>

                  <div className="hidden md:flex items-center text-sm text-ink-muted">
                    {formatRelativeOrDate(project.updated_at || project.created_at)}
                  </div>

                  <span className="hidden md:block" />
                </button>

                <div className="absolute right-4 top-4 z-50 flex items-center gap-1 transition-all duration-300 md:top-1/2 md:-translate-y-1/2 md:translate-x-1 md:opacity-0 md:group-hover:translate-x-0 md:group-hover:opacity-100 md:focus-within:translate-x-0 md:focus-within:opacity-100">
                  <IconButton
                    label="Open project"
                    tone="primary"
                    size="sm"
                    onClick={(e) => { e.stopPropagation(); onOpen(project.id); }}
                  >
                    <ArrowUpRight className="h-4 w-4" />
                  </IconButton>
                  <div className="relative" ref={openMenu === project.id ? menuRef : null}>
                    <IconButton
                      label="More actions"
                      tone="ghost"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        setOpenMenu(openMenu === project.id ? null : project.id);
                      }}
                    >
                      <MoreHorizontal className="h-4 w-4" />
                    </IconButton>
                    <AnimatePresence>
                      {openMenu === project.id && (
                        <motion.div
                          role="menu"
                          initial={{ opacity: 0, y: -6, scale: 0.97 }}
                          animate={{ opacity: 1, y: 0, scale: 1, transition: { duration: 0.16, ease: [0.16, 1, 0.3, 1] } }}
                          exit={{ opacity: 0, y: -4, scale: 0.98, transition: { duration: 0.12 } }}
                        className="absolute right-0 top-full z-[70] mt-2 w-48 origin-top-right rounded-xl border border-line bg-surface-raised p-1 shadow-pop"
                        onClick={(e) => e.stopPropagation()}
                      >
                          <MenuItem onClick={() => { onOpen(project.id); setOpenMenu(null); }} icon={ArrowUpRight}>
                            Open
                          </MenuItem>
                          <MenuItem
                            icon={Edit3}
                            onClick={() => {
                              onEdit(project);
                              setOpenMenu(null);
                            }}
                          >
                            Edit project
                          </MenuItem>
                          <div className="my-1 h-px bg-line" />
                          <MenuItem
                            icon={Trash2}
                            danger
                            onClick={() => {
                              onDelete(project.id, project.name);
                              setOpenMenu(null);
                            }}
                          >
                            Delete project
                          </MenuItem>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                </div>

                <div className="md:hidden flex flex-wrap items-center gap-2 px-4 pb-4 -mt-1 text-xs text-ink-muted sm:px-5">
                  {project.project_code && <Badge tone="primary">{project.project_code}</Badge>}
                  <span className="inline-flex items-center gap-1 rounded-lg border border-line bg-surface-muted px-2 py-1">
                    <FileText className="h-3 w-3" />
                    {statsLoading ? '…' : stats?.files ?? 0}
                  </span>
                  <span className="inline-flex items-center gap-1 rounded-lg border border-line bg-surface-muted px-2 py-1">
                    <HardDrive className="h-3 w-3" />
                    {statsLoading ? '…' : formatBytes(stats?.size ?? 0)}
                  </span>
                  <span className="inline-flex items-center gap-1 rounded-lg border border-line bg-surface-muted px-2 py-1">
                    <Calendar className="h-3 w-3" />
                    {formatRelativeOrDate(project.updated_at || project.created_at)}
                  </span>
                </div>
              </motion.li>
            );
          })}
        </AnimatePresence>
      </ul>
    </Card>
  );
};

const SortHeader = ({ label, active, dir, onClick, className }) => (
  <button
    onClick={onClick}
    className={cn(
      'inline-flex items-center gap-1.5 transition-colors',
      active ? 'text-ink' : 'text-ink-subtle hover:text-ink',
      className
    )}
  >
    <span>{label}</span>
    {active ? (
      dir === 'desc'
        ? <ArrowDown className="h-3 w-3" />
        : <ArrowUp className="h-3 w-3" />
    ) : (
      <ArrowUpDown className="h-3 w-3 opacity-60" />
    )}
  </button>
);

const MenuItem = ({ icon: Icon, children, danger, disabled, onClick }) => (
  <button
    role="menuitem"
    disabled={disabled}
    onClick={onClick}
    className={cn(
      'w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors',
      'disabled:opacity-50 disabled:cursor-not-allowed',
      danger
        ? 'text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10'
        : 'text-ink-muted hover:bg-surface-muted hover:text-ink'
    )}
  >
    {Icon && <Icon className="h-4 w-4" />}
    <span className="flex-1 text-left">{children}</span>
  </button>
);

export default Dashboard;






