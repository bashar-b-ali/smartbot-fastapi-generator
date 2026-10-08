import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Folder,
  File as FileIcon,
  ChevronRight,
  ChevronDown,
  Search,
  RefreshCw,
  Copy,
  Check,
  Code2,
  PanelLeftClose,
  PanelLeftOpen,
  Image as ImageIcon,
  FileText,
  Video,
  Music,
  Archive,
  Book,
  X,
} from 'lucide-react';
import { useProject } from '../../contexts/ProjectContext.jsx';
import { useToast } from '../common/Toast';
import { projectService } from '../../services/api';
import IconButton from '../common/IconButton';
import Button from '../common/Button';
import MarkdownDocument from '../common/MarkdownDocument';
import { cn } from '../common/cn';
import { formatBytes } from '../../utils/formatters';

const TEXT_EXT = new Set([
  'txt', 'md', 'rst', 'js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs',
  'py', 'rb', 'go', 'rs', 'java', 'kt', 'swift',
  'cpp', 'cc', 'cxx', 'c', 'h', 'hpp', 'cs',
  'html', 'htm', 'css', 'scss', 'sass', 'less',
  'json', 'yml', 'yaml', 'xml', 'toml', 'ini', 'env',
  'sql', 'php', 'sh', 'bash', 'zsh', 'ps1', 'bat', 'cmd',
  'vue', 'svelte', 'tex', 'r', 'lua', 'pl', 'dart',
]);

const IMAGE_EXT = new Set(['jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp', 'avif', 'ico']);

const LANG_MAP = {
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'jsx',
  ts: 'typescript', tsx: 'tsx',
  py: 'python', css: 'css', scss: 'scss', sass: 'scss', less: 'less',
  html: 'markup', htm: 'markup', xml: 'markup', vue: 'markup', svelte: 'markup',
  json: 'json', md: 'markdown',
  yml: 'yaml', yaml: 'yaml',
  sql: 'sql', java: 'java', kt: 'kotlin', swift: 'swift',
  c: 'c', h: 'c', cpp: 'cpp', hpp: 'cpp',
  cs: 'csharp', php: 'php', rb: 'ruby', go: 'go', rs: 'rust',
  sh: 'bash', bash: 'bash', zsh: 'bash', ps1: 'powershell',
  toml: 'toml', dockerfile: 'docker',
};

const extOf = (name = '') => name.split('.').pop()?.toLowerCase() || '';
const isImage = (name) => IMAGE_EXT.has(extOf(name));
const isText = (name) => TEXT_EXT.has(extOf(name));
const isMarkdown = (name) => ['md', 'markdown'].includes(extOf(name));
const langOf = (name) => LANG_MAP[extOf(name)] || 'text';

const lineCountOf = (text = '') => String(text || '').split('\n').length;

const TOKEN_RE = /(\/\/.*|#.*|"(?:\\.|[^"])*"|'(?:\\.|[^'])*'|\b(?:async|await|break|case|catch|class|const|def|else|except|export|false|finally|for|from|function|if|import|in|let|null|None|pass|raise|return|self|this|true|try|var|while|with|yield)\b|\b\d+(?:\.\d+)?\b)/g;

const renderCodeLine = (line) => {
  const parts = String(line || '').split(TOKEN_RE).filter((part) => part !== '');
  return parts.map((part, index) => {
    const className =
      /^\/\/|^#/.test(part)
        ? 'text-[#008000] dark:text-[#6A9955]'
        : /^["']/.test(part)
          ? 'text-[#A31515] dark:text-[#CE9178]'
          : /^\d/.test(part)
            ? 'text-[#098658] dark:text-[#B5CEA8]'
            : /^(async|await|break|case|catch|class|const|def|else|except|export|false|finally|for|from|function|if|import|in|let|null|None|pass|raise|return|self|this|true|try|var|while|with|yield)$/.test(part)
              ? 'text-[#0000FF] dark:text-[#569CD6]'
              : 'text-slate-900 dark:text-[#D4D4D4]';
    return (
      <span key={index} className={className}>
        {part}
      </span>
    );
  });
};

const collator = new Intl.Collator(undefined, {
  numeric: true,
  sensitivity: 'base',
});

const basenameForSort = (name = '') => {
  if (name === '..') return name;
  if (name.startsWith('.') && name.length > 1) return name.slice(1);
  return name;
};

const compareExplorerEntries = (a, b) => {
  if (a.is_directory !== b.is_directory) return a.is_directory ? -1 : 1;
  const baseCompare = collator.compare(basenameForSort(a.name), basenameForSort(b.name));
  if (baseCompare !== 0) return baseCompare;
  return collator.compare(a.name || '', b.name || '');
};

const iconForFile = (name, isDir) => {
  if (isDir) return <Folder className="h-4 w-4 text-primary-500 flex-shrink-0" />;
  const ext = extOf(name);
  const cls = 'h-4 w-4 flex-shrink-0';
  if (IMAGE_EXT.has(ext)) return <ImageIcon className={cn(cls, 'text-emerald-500')} />;
  if (['mp4', 'avi', 'mov', 'wmv', 'flv', 'webm'].includes(ext)) return <Video className={cn(cls, 'text-purple-500')} />;
  if (['mp3', 'wav', 'ogg', 'flac', 'aac'].includes(ext)) return <Music className={cn(cls, 'text-pink-500')} />;
  if (['zip', 'rar', 'tar', 'gz', '7z'].includes(ext)) return <Archive className={cn(cls, 'text-amber-500')} />;
  if (['pdf', 'doc', 'docx', 'rtf'].includes(ext)) return <FileText className={cn(cls, 'text-blue-500')} />;
  if (['js', 'jsx', 'ts', 'tsx', 'py', 'java', 'cpp', 'c', 'html', 'css', 'json', 'xml', 'go', 'rs'].includes(ext)) {
    return <Code2 className={cn(cls, 'text-amber-500')} />;
  }
  if (ext === 'md') return <Book className={cn(cls, 'text-purple-500')} />;
  return <FileIcon className={cn(cls, 'text-ink-subtle')} />;
};

const normalize = (items, parent = '') =>
  (Array.isArray(items) ? items : []).map((item) => {
    const name = item.name || item.filename || '';
    const dir = item.is_directory || item.is_dir || item.type === 'dir';
    const path = item.path || item.full_path || (parent ? `${parent}/${name}` : name);
    return {
      name,
      is_directory: !!dir,
      size: item.size ?? item.file_size ?? null,
      modified: item.modified ?? item.mtime ?? null,
      path,
    };
  }).sort(compareExplorerEntries);

const FileViewer = ({ projectId, selectedFile, onFileSelect, refreshKey = 0 }) => {
  const { getFolderContent } = useProject();
  const toast = useToast();

  const [filesByPath, setFilesByPath] = useState({ '': [] });
  const [expanded, setExpanded] = useState(() => new Set());
  const [loadingFolders, setLoadingFolders] = useState(() => new Set());
  const [refreshing, setRefreshing] = useState(false);

  const [content, setContent] = useState('');
  const [blobUrl, setBlobUrl] = useState(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileSize, setFileSize] = useState(null);
  const [copied, setCopied] = useState(false);

  const [searchTerm, setSearchTerm] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const searchTimer = useRef(null);

  const [explorerWidth, setExplorerWidth] = useState(() => {
    const saved = parseInt(localStorage.getItem('file-explorer-width'), 10);
    return Number.isFinite(saved) && saved > 0 ? saved : 300;
  });
  const [explorerOpen, setExplorerOpen] = useState(true);
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== 'undefined' && window.innerWidth < 768
  );
  const isResizing = useRef(false);

  useEffect(() => {
    const onResize = () => {
      const mobile = window.innerWidth < 768;
      setIsMobile(mobile);
      if (mobile) setExplorerOpen(false);
    };
    onResize();
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && isMobile) setExplorerOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isMobile]);

  useEffect(() => {
    if (!isMobile) localStorage.setItem('file-explorer-width', String(explorerWidth));
  }, [explorerWidth, isMobile]);

  useEffect(() => () => { if (blobUrl) URL.revokeObjectURL(blobUrl); }, [blobUrl]);

  const loadFolder = useCallback(
    async (path = '') => {
      if (!projectId) return;
      setLoadingFolders((prev) => new Set(prev).add(path));
      try {
        const items = await getFolderContent(projectId, path);
        const norm = normalize(items, path);
        setFilesByPath((prev) => ({ ...prev, [path]: norm }));
      } catch {
        setFilesByPath((prev) => ({ ...prev, [path]: [] }));
      } finally {
        setLoadingFolders((prev) => {
          const c = new Set(prev);
          c.delete(path);
          return c;
        });
      }
    },
    [projectId, getFolderContent]
  );

  useEffect(() => {
    if (!projectId) return;
    setFilesByPath({ '': [] });
    setExpanded(new Set());
    loadFolder('');
  }, [projectId, loadFolder, refreshKey]);

  const handleToggle = (entry) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(entry.path)) next.delete(entry.path);
      else next.add(entry.path);
      return next;
    });
    if (!filesByPath[entry.path]) loadFolder(entry.path);
  };

  useEffect(() => {
    if (!selectedFile?.path || !projectId) return;
    let cancelled = false;
    setFileLoading(true);
    setCopied(false);

    if (blobUrl) {
      URL.revokeObjectURL(blobUrl);
      setBlobUrl(null);
    }
    setFileSize(selectedFile.size || null);

    (async () => {
      try {
        if (isText(selectedFile.name)) {
          if (refreshKey === 0 && typeof selectedFile.initialContent === 'string') {
            if (cancelled) return;
            setContent(selectedFile.initialContent);
            if (!selectedFile.size) setFileSize(new Blob([selectedFile.initialContent]).size);
            return;
          }
          const res = await projectService.getFileContent(projectId, selectedFile.path);
          if (cancelled) return;
          setContent(typeof res === 'string' ? res : (res?.content ?? ''));
        } else {
          const res = await projectService.downloadFile(projectId, selectedFile.path);
          if (cancelled) return;
          const blob = res instanceof Blob ? res : res?.data;
          if (!blob) throw new Error('Invalid file response');
          const url = URL.createObjectURL(blob);
          setBlobUrl(url);
          setContent('');
          if (!selectedFile.size && blob.size) setFileSize(blob.size);
        }
      } catch (err) {
        if (!cancelled) setContent(`Error loading file: ${err?.message || err}`);
      } finally {
        if (!cancelled) setFileLoading(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFile?.path, selectedFile?.initialContent, selectedFile?.name, selectedFile?.size, projectId, refreshKey]);

  const searchFiles = useCallback(
    async (term) => {
      if (!projectId || !term) {
        setSearchResults([]);
        setSearching(false);
        return;
      }
      setSearching(true);
      const queue = [''];
      const visited = new Set();
      const matches = [];
      const MAX_FETCH = 200;
      let fetched = 0;
      const lowered = term.toLowerCase();

      while (queue.length && fetched < MAX_FETCH) {
        const path = queue.shift();
        if (visited.has(path)) continue;
        visited.add(path);
        let entries = filesByPath[path];
        if (!entries) {
          try {
            const resp = await getFolderContent(projectId, path);
            entries = normalize(resp, path);
            setFilesByPath((prev) => ({ ...prev, [path]: entries }));
          } catch {
            entries = [];
          }
          fetched += 1;
        }
        for (const entry of entries) {
          if (entry.is_directory) queue.push(entry.path);
          else if (entry.name.toLowerCase().includes(lowered)) matches.push(entry);
        }
      }
      setSearchResults(matches.sort(compareExplorerEntries));
      setSearching(false);
    },
    [projectId, filesByPath, getFolderContent]
  );

  useEffect(() => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (!searchTerm) {
      setSearchResults([]);
      setSearching(false);
      return;
    }
    searchTimer.current = setTimeout(() => searchFiles(searchTerm), 300);
    return () => searchTimer.current && clearTimeout(searchTimer.current);
  }, [searchTerm, searchFiles]);

  const startResize = (e) => {
    if (isMobile) return;
    e.preventDefault();
    isResizing.current = true;
    const startX = e.clientX;
    const startW = explorerWidth;
    const onMove = (ev) => {
      if (!isResizing.current) return;
      const next = Math.max(220, Math.min(560, startW + (ev.clientX - startX)));
      setExplorerWidth(next);
    };
    const onUp = () => {
      isResizing.current = false;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
  };

  const handleCopy = async () => {
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error('Copy failed');
    }
  };

  const onFileClick = (file) => {
    onFileSelect?.(file);
    if (isMobile) setExplorerOpen(false);
  };

  const renderTree = (path = '', depth = 0) => {
    const entries = filesByPath[path] || [];
    if (loadingFolders.has(path) && !entries.length) {
      return <div className="px-3 py-2 text-xs text-ink-subtle">Loading…</div>;
    }
    if (!entries.length) {
      return <div className="px-3 py-2 text-xs text-ink-subtle">Empty folder</div>;
    }
    return (
      <ul className="text-sm">
        {entries.map((entry) => {
          const open = expanded.has(entry.path);
          const isSelected = selectedFile?.path === entry.path;
          return (
            <li key={entry.path}>
              <button
                onClick={() => (entry.is_directory ? handleToggle(entry) : onFileClick(entry))}
                style={{ paddingLeft: 8 + depth * 12 }}
                className={cn(
                  'w-full flex items-center gap-1.5 py-1.5 pr-2 rounded-md text-left',
                  'hover:bg-surface-muted transition-colors',
                  isSelected && 'bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300'
                )}
              >
                {entry.is_directory ? (
                  open ? <ChevronDown className="h-3.5 w-3.5 text-ink-subtle flex-shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 text-ink-subtle flex-shrink-0" />
                ) : (
                  <span className="w-3.5 flex-shrink-0" />
                )}
                {iconForFile(entry.name, entry.is_directory)}
                <span className="truncate">{entry.name}</span>
              </button>
              {entry.is_directory && open && renderTree(entry.path, depth + 1)}
            </li>
          );
        })}
      </ul>
    );
  };

  const renderResults = () => {
    if (searching) return <div className="px-3 py-2 text-xs text-ink-subtle">Searching…</div>;
    if (!searchResults.length)
      return <div className="px-3 py-6 text-center text-sm text-ink-subtle">No matching files</div>;
    return (
      <ul className="text-sm">
        {searchResults.map((entry) => (
          <li key={entry.path}>
            <button
              onClick={() => onFileClick(entry)}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-md hover:bg-surface-muted text-left"
            >
              {iconForFile(entry.name, entry.is_directory)}
              <span className="truncate">{entry.name}</span>
              <span className="ml-auto text-[10px] text-ink-subtle truncate max-w-[40%]">
                {entry.path}
              </span>
            </button>
          </li>
        ))}
      </ul>
    );
  };

  const explorer = (
    <aside
      className={cn(
        'flex min-h-0 flex-col overflow-hidden bg-surface-muted/50 border-r border-line min-w-0 safe-bottom transition-[width,opacity,transform] duration-300 ease-out',
        isMobile ? 'h-full w-full' : 'h-full',
      )}
      style={!isMobile ? { width: explorerOpen ? explorerWidth : 0, opacity: explorerOpen ? 1 : 0 } : undefined}
    >
      <div className="border-b border-line p-3">
        <div className="flex items-center justify-between mb-3">
          <h3 className="inline-flex items-center gap-1.5 text-sm font-semibold text-ink">
            <Code2 className="h-4 w-4 text-primary-500" />
            Explorer
          </h3>
          <div className="flex items-center gap-1">
            <IconButton
              label="Refresh"
              tone="ghost"
              size="sm"
              onClick={async () => {
                setRefreshing(true);
                try { await loadFolder(''); } finally { setRefreshing(false); }
              }}
              disabled={refreshing}
            >
              <RefreshCw className={cn('h-4 w-4', refreshing && 'animate-spin')} />
            </IconButton>
            <IconButton
              label={isMobile ? 'Close explorer' : 'Hide explorer'}
              tone="ghost"
              size="sm"
              onClick={() => setExplorerOpen(false)}
            >
              {isMobile ? <X className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </IconButton>
          </div>
        </div>
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-ink-subtle" />
          <input
            type="search"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search files…"
            className="w-full rounded-lg border border-line bg-surface-raised py-2 pl-8 pr-3 text-base text-ink placeholder:text-ink-subtle outline-none transition-smooth focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 sm:text-sm"
          />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto scrollbar-fancy py-1">
        {searchTerm ? renderResults() : renderTree('')}
      </div>
    </aside>
  );

  const preview = (() => {
    if (!selectedFile) {
      return (
        <div className="flex min-h-0 flex-1 items-center justify-center p-6">
          <div className="text-center max-w-sm">
            <div className="mx-auto inline-flex h-16 w-16 items-center justify-center rounded-2xl bg-surface-muted border border-line text-ink-subtle mb-4">
              <FileIcon className="h-7 w-7" />
            </div>
            <h3 className="text-base font-semibold text-ink">No file selected</h3>
            <p className="mt-1 text-sm text-ink-muted">
              Pick a file from the explorer to preview it here.
            </p>
            {!explorerOpen && (
              <Button
                variant="primary"
                size="sm"
                className="mt-4"
                leftIcon={<PanelLeftOpen className="h-4 w-4" />}
                onClick={() => setExplorerOpen(true)}
              >
                Show explorer
              </Button>
            )}
          </div>
        </div>
      );
    }

    if (fileLoading) {
      return (
        <div className="flex min-h-0 flex-1 items-center justify-center text-sm text-ink-muted">
          <span className="inline-flex items-center gap-2">
            <span className="h-3 w-3 rounded-full border-2 border-primary-500 border-t-transparent animate-spin" />
            Loading {selectedFile.name}…
          </span>
        </div>
      );
    }

    if (isImage(selectedFile.name) && blobUrl) {
      return (
        <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-surface-muted/40 p-4">
          <img
            src={blobUrl}
            alt={selectedFile.name}
            className="max-w-full max-h-full object-contain rounded-lg shadow-card border border-line bg-surface-raised"
          />
        </div>
      );
    }

    if (!isText(selectedFile.name)) {
      return (
        <div className="flex min-h-0 flex-1 items-center justify-center p-6">
          <div className="text-center max-w-sm">
            <div className="mx-auto inline-flex h-16 w-16 items-center justify-center rounded-2xl bg-surface-muted border border-line mb-4">
              {iconForFile(selectedFile.name)}
            </div>
            <h3 className="text-base font-semibold text-ink">Binary file</h3>
            <p className="mt-1 text-sm text-ink-muted">
              This file type can&apos;t be previewed in the code reader.
            </p>
          </div>
        </div>
      );
    }

    if (isMarkdown(selectedFile.name)) {
      return (
        <div className="min-h-0 flex-1 overflow-auto bg-surface-muted/35 p-4 scrollbar-fancy">
          <div className="mx-auto max-w-5xl">
            <MarkdownDocument content={content || ''} />
          </div>
        </div>
      );
    }

    const lineCount = lineCountOf(content || '');
    return (
      <div className="min-h-0 flex-1 overflow-auto bg-white scrollbar-fancy selection:bg-[#ADD6FF] selection:text-slate-950 dark:bg-[#1E1E1E] dark:selection:bg-[#0E639C] dark:selection:text-white">
        <div className="min-w-max">
          <div className="grid grid-cols-[max-content_minmax(0,1fr)] items-start font-mono text-[12.5px] leading-5">
            <div
              aria-hidden="true"
              className="select-none border-r border-line bg-surface-muted/50 px-3 py-3 text-right text-ink-subtle dark:border-[#333333] dark:bg-[#1E1E1E] dark:text-[#858585]"
            >
              {Array.from({ length: lineCount }).map((_, index) => (
                <div key={index} className="h-5 tabular-nums">
                  {index + 1}
                </div>
              ))}
            </div>
            <pre className="m-0 min-h-full overflow-visible whitespace-pre px-4 py-3 text-slate-900 selection:bg-[#ADD6FF] selection:text-slate-950 dark:text-[#D4D4D4] dark:selection:bg-[#0E639C] dark:selection:text-white">
              <code>
                {String(content || '').split('\n').map((line, index) => (
                  <span key={index} className="block h-5">
                    {line ? renderCodeLine(line) : '\u00a0'}
                  </span>
                ))}
              </code>
            </pre>
          </div>
        </div>
      </div>
    );
  })();

  return (
    <div className="relative flex h-full min-h-0 overflow-hidden bg-surface-raised text-ink">
      {!isMobile && explorer}

      {!isMobile && explorerOpen && (
        <div
          onMouseDown={startResize}
          aria-label="Resize explorer"
          className="w-1.5 cursor-col-resize hover:bg-primary-500/30 transition-colors"
        />
      )}

      {isMobile && (
        <>
          <div
            className={cn("fixed inset-0 z-40 bg-slate-900/60 backdrop-blur-sm transition-opacity duration-300", explorerOpen ? "opacity-100" : "pointer-events-none opacity-0")}
            onClick={() => setExplorerOpen(false)}
          />
          <div className={cn("fixed bottom-0 left-0 top-0 z-50 w-[80%] max-w-sm bg-surface-raised border-r border-line shadow-pop transition-transform duration-300 ease-out", explorerOpen ? "translate-x-0" : "-translate-x-full")}>
            {explorer}
          </div>
        </>
      )}

      <div className="flex min-h-0 flex-1 flex-col min-w-0">
        {selectedFile ? (
          <header className="flex flex-shrink-0 items-center justify-between gap-3 border-b border-line bg-surface-raised px-3 py-2.5 sm:px-4">
            <div className="flex items-center gap-2 min-w-0">
              {!explorerOpen && (
                <IconButton
                  label="Show explorer"
                  tone="subtle"
                  size="sm"
                  onClick={() => setExplorerOpen(true)}
                >
                  <PanelLeftOpen className="h-4 w-4" />
                </IconButton>
              )}
              {iconForFile(selectedFile.name)}
              <div className="min-w-0">
                <p className="text-sm font-semibold text-ink truncate">{selectedFile.name}</p>
                <p className="text-[11px] text-ink-subtle truncate">
                  {selectedFile.path}
                  {' • '}
                  {isText(selectedFile.name) ? langOf(selectedFile.name) : 'binary'}
                  {fileSize ? ` • ${formatBytes(fileSize)}` : ''}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-1.5 flex-shrink-0">
              {isText(selectedFile.name) && (
                <Button
                  variant="secondary"
                  size="sm"
                  aria-label="Copy file contents"
                  onClick={handleCopy}
                  leftIcon={copied ? <Check className="h-4 w-4 text-emerald-500" /> : <Copy className="h-4 w-4" />}
                >
                  <span className="hidden sm:inline">{copied ? 'Copied' : 'Copy'}</span>
                </Button>
              )}
            </div>
          </header>
        ) : !explorerOpen ? (
          <div className="border-b border-line px-3 py-2.5">
            <IconButton
              label="Show explorer"
              tone="subtle"
              size="sm"
              onClick={() => setExplorerOpen(true)}
            >
              <PanelLeftOpen className="h-4 w-4" />
            </IconButton>
          </div>
        ) : null}

        {preview}
      </div>
    </div>
  );
};

export default FileViewer;
