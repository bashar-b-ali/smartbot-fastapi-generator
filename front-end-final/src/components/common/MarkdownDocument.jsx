import React from 'react';
import { cn } from './cn';

const METHOD_TONE = {
  GET: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300',
  POST: 'border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-300',
  PUT: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300',
  DELETE: 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300',
  WEBSOCKET: 'border-purple-200 bg-purple-50 text-purple-700 dark:border-purple-500/30 dark:bg-purple-500/10 dark:text-purple-300',
};

const renderInline = (text) => {
  const parts = String(text || '').split(/(`[^`]+`)/g);
  return parts.map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code
          key={index}
          className="rounded-md border border-line bg-surface-muted px-1.5 py-0.5 font-mono text-[0.92em] text-primary-700 dark:text-primary-300"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return <React.Fragment key={index}>{part}</React.Fragment>;
  });
};

const parseApiEndpointDoc = (content) => {
  const lines = String(content || '').split('\n');
  const overview = [];
  const methodSummary = [];
  const groups = [];
  const dbLines = [];
  let mode = '';
  let group = null;
  let endpoint = null;

  const commitEndpoint = () => {
    if (endpoint && group) group.endpoints.push(endpoint);
    endpoint = null;
  };
  const commitGroup = () => {
    commitEndpoint();
    if (group) groups.push(group);
    group = null;
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line === '# API Endpoints') continue;

    if (line === '## Overview') { mode = 'overview'; continue; }
    if (line === '## Method Summary') { mode = 'methods'; continue; }
    if (line === '## Endpoints') { mode = 'endpoints'; continue; }
    if (line === '## Database Tables') {
      commitGroup();
      mode = 'database';
      continue;
    }

    if (mode === 'overview' && line.startsWith('- ')) {
      const match = line.match(/^-\s+([^:]+):\s*(.+)$/);
      if (match) overview.push({ label: match[1], value: match[2] });
      continue;
    }

    if (mode === 'methods' && line.startsWith('- ')) {
      const match = line.match(/^-\s+`?([A-Z]+|WEBSOCKET)`?:\s*(.+)$/);
      if (match) methodSummary.push({ method: match[1], count: match[2] });
      continue;
    }

    if (mode === 'endpoints') {
      const groupMatch = line.match(/^###\s+(.+)$/);
      if (groupMatch) {
        commitGroup();
        group = { name: groupMatch[1], endpoints: [] };
        continue;
      }

      const endpointMatch = line.match(/^####\s+`([A-Z]+|WEBSOCKET)\s+(.+)`$/);
      if (endpointMatch) {
        commitEndpoint();
        endpoint = {
          method: endpointMatch[1],
          path: endpointMatch[2],
          purpose: '',
          handler: '',
          source: '',
          params: [],
        };
        continue;
      }

      if (endpoint) {
        if (line.startsWith('- Purpose:')) endpoint.purpose = line.replace('- Purpose:', '').trim();
        else if (line.startsWith('- Handler:')) endpoint.handler = line.replace('- Handler:', '').trim().replace(/`/g, '');
        else if (line.startsWith('- Source:')) endpoint.source = line.replace('- Source:', '').trim().replace(/`/g, '');
        else if (line.startsWith('- `')) endpoint.params.push(line.replace(/^- /, ''));
      }
      continue;
    }

    if (mode === 'database') dbLines.push(raw);
  }
  commitGroup();
  return { overview, methodSummary, groups, database: dbLines.join('\n').trim() };
};

const MethodBadge = ({ method }) => (
  <span className={cn(
    'inline-flex min-w-[4.5rem] justify-center rounded-md border px-2 py-1 font-mono text-[11px] font-bold',
    METHOD_TONE[method] || 'border-line bg-surface-muted text-ink-muted'
  )}>
    {method}
  </span>
);

const parseParam = (line) => {
  const text = String(line || '').replace(/`/g, '');
  const match = text.match(/^([^:]+):\s+(.+?)\s+in\s+([a-z_]+)\s+\(([^)]+)\)$/i);
  if (!match) return { name: text, type: '', location: '', required: '' };
  return {
    name: match[1],
    type: match[2],
    location: match[3],
    required: match[4],
  };
};

const parseDatabaseTables = (databaseText) => {
  const lines = String(databaseText || '').split('\n');
  const tables = [];
  let table = null;
  let index = 0;

  const commit = () => {
    if (table) tables.push(table);
    table = null;
  };

  while (index < lines.length) {
    const line = lines[index].trim();
    const tableMatch = line.match(/^###\s+(.+)$/);
    if (tableMatch) {
      commit();
      table = { name: tableMatch[1], fields: [] };
      index += 1;
      continue;
    }

    if (table && /^\|.*\|$/.test(line)) {
      const rows = [];
      while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) {
        rows.push(lines[index]);
        index += 1;
      }
      const cellsOf = (row) =>
        row
          .trim()
          .replace(/^\|/, '')
          .replace(/\|$/, '')
          .split('|')
          .map((cell) => cell.trim().replace(/`/g, ''));
      table.fields = rows.slice(2).map((row) => {
        const [field, type, notes] = cellsOf(row);
        return { field, type, notes };
      });
      continue;
    }

    index += 1;
  }

  commit();
  return tables;
};

const DatabaseTables = ({ content }) => {
  const tables = parseDatabaseTables(content);
  if (!tables.length) {
    return <MarkdownDocument content={content} className="border-0 bg-transparent p-0 shadow-none" />;
  }

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {tables.map((table) => (
        <section key={table.name} className="overflow-hidden rounded-xl border border-line bg-surface-raised">
          <div className="flex items-center justify-between gap-3 border-b border-line bg-surface-muted/45 px-3 py-2.5">
            <div className="min-w-0">
              <h3 className="truncate text-sm font-semibold text-ink">{table.name}</h3>
              <p className="text-[11px] text-ink-subtle">
                {table.fields.length} {table.fields.length === 1 ? 'field' : 'fields'}
              </p>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-xs">
              <thead className="bg-surface-muted/70 text-ink-subtle">
                <tr>
                  <th className="px-3 py-2 font-semibold uppercase tracking-[0.12em]">Field</th>
                  <th className="px-3 py-2 font-semibold uppercase tracking-[0.12em]">Type</th>
                  <th className="px-3 py-2 font-semibold uppercase tracking-[0.12em]">Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {table.fields.map((field) => {
                  const note = field.notes || '-';
                  return (
                    <tr key={`${table.name}-${field.field}`} className="transition-colors duration-200 hover:bg-surface-muted/40">
                      <td className="px-3 py-2">
                        <code className="font-mono font-semibold text-ink">{field.field}</code>
                      </td>
                      <td className="px-3 py-2">
                        <code className="font-mono text-ink-muted">{field.type || '-'}</code>
                      </td>
                      <td className="px-3 py-2">
                        <span className={cn(
                          'inline-flex rounded-md border px-2 py-0.5 text-[11px] font-medium',
                          /primary key/i.test(note)
                            ? 'border-primary-200 bg-primary-50 text-primary-700 dark:border-primary-500/30 dark:bg-primary-500/10 dark:text-primary-300'
                            : /indexed/i.test(note)
                              ? 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300'
                              : 'border-line bg-surface-muted text-ink-muted'
                        )}>
                          {note}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
};

const ApiEndpointDocument = ({ content, className }) => {
  const doc = parseApiEndpointDoc(content);
  return (
    <article className={cn('space-y-5 rounded-xl border border-line bg-surface-raised p-4 shadow-card sm:p-5', className)}>
      <header className="border-b border-line pb-4">
        <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink-subtle">Project documentation</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-ink">API Endpoints</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-ink-muted">
          Generated FastAPI route map with handlers, sources, parameters, and database tables.
        </p>
      </header>

      {doc.overview.length > 0 && (
        <section className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {doc.overview.map((item) => (
            <div key={item.label} className="rounded-lg border border-line bg-surface-muted/55 px-3 py-2.5">
              <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-subtle">{item.label}</p>
              <p className="mt-1 font-mono text-lg font-bold text-ink">{item.value}</p>
            </div>
          ))}
        </section>
      )}

      {doc.methodSummary.length > 0 && (
        <section className="flex flex-wrap gap-2">
          {doc.methodSummary.map((item) => (
            <div key={item.method} className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface-muted/45 px-2.5 py-2">
              <MethodBadge method={item.method} />
              <span className="text-sm font-semibold tabular-nums text-ink">{item.count}</span>
            </div>
          ))}
        </section>
      )}

      <section className="space-y-4">
        {doc.groups.map((group) => (
          <div key={group.name} className="rounded-xl border border-line bg-surface-muted/30">
            <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
              <h2 className="text-base font-semibold text-ink">{group.name}</h2>
              <span className="rounded-full bg-surface-raised px-2 py-1 text-xs font-semibold text-ink-subtle">
                {group.endpoints.length} routes
              </span>
            </div>
            <div className="divide-y divide-line">
              {group.endpoints.map((endpoint) => (
                <section key={`${endpoint.method}-${endpoint.path}`} className="p-4">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <MethodBadge method={endpoint.method} />
                    <code className="min-w-0 break-all rounded-lg border border-line bg-surface-raised px-2.5 py-1.5 font-mono text-sm text-ink">
                      {endpoint.path}
                    </code>
                  </div>
                  {endpoint.purpose && <p className="mt-3 text-sm leading-6 text-ink-muted">{endpoint.purpose}</p>}
                  <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                    {endpoint.handler && (
                      <div className="rounded-lg border border-line bg-surface-raised px-3 py-2">
                        <span className="font-semibold text-ink-subtle">Handler</span>
                        <code className="ml-2 font-mono text-ink">{endpoint.handler}</code>
                      </div>
                    )}
                    {endpoint.source && (
                      <div className="rounded-lg border border-line bg-surface-raised px-3 py-2">
                        <span className="font-semibold text-ink-subtle">Source</span>
                        <code className="ml-2 font-mono text-ink">{endpoint.source}</code>
                      </div>
                    )}
                  </div>
                  <div className="mt-3">
                    <p className="mb-2 text-xs font-semibold uppercase tracking-[0.12em] text-ink-subtle">Parameters</p>
                    {endpoint.params.length ? (
                      <div className="overflow-hidden rounded-lg border border-line bg-surface-raised">
                        <table className="min-w-full text-left text-xs">
                          <thead className="bg-surface-muted/70 text-ink-subtle">
                            <tr>
                              <th className="px-3 py-2 font-semibold uppercase tracking-[0.12em]">Name</th>
                              <th className="px-3 py-2 font-semibold uppercase tracking-[0.12em]">Type</th>
                              <th className="px-3 py-2 font-semibold uppercase tracking-[0.12em]">In</th>
                              <th className="px-3 py-2 font-semibold uppercase tracking-[0.12em]">Required</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-line">
                            {endpoint.params.map((param) => {
                              const parsed = parseParam(param);
                              return (
                                <tr key={param} className="transition-colors duration-200 hover:bg-surface-muted/40">
                                  <td className="px-3 py-2 font-mono font-semibold text-ink">{parsed.name}</td>
                                  <td className="px-3 py-2 font-mono text-ink-muted">{parsed.type || '-'}</td>
                                  <td className="px-3 py-2 text-ink-muted">{parsed.location || '-'}</td>
                                  <td className="px-3 py-2 text-ink-muted">{parsed.required || '-'}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-sm text-ink-subtle">None detected.</p>
                    )}
                  </div>
                </section>
              ))}
            </div>
          </div>
        ))}
      </section>

      {doc.database && (
        <section className="space-y-3 border-t border-line pt-5">
          <div>
            <h2 className="text-lg font-semibold text-ink">Database Tables</h2>
            <p className="mt-1 text-sm text-ink-muted">Detected SQLModel tables, fields, and persistence hints.</p>
          </div>
          <DatabaseTables content={doc.database} />
        </section>
      )}
    </article>
  );
};

const parseTable = (lines, startIndex) => {
  const rows = [];
  let index = startIndex;
  while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) {
    rows.push(lines[index]);
    index += 1;
  }
  if (rows.length < 2) return null;

  const cellsOf = (row) =>
    row
      .trim()
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split('|')
      .map((cell) => cell.trim());

  const header = cellsOf(rows[0]);
  const body = rows.slice(2).map(cellsOf);
  return { nextIndex: index, header, body };
};

const MarkdownDocument = ({ content, className }) => {
  if (/^#\s+API Endpoints/m.test(String(content || ''))) {
    return <ApiEndpointDocument content={content} className={className} />;
  }

  const lines = String(content || '').split('\n');
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    const table = parseTable(lines, index);
    if (table) {
      blocks.push(
        <div key={`table-${index}`} className="overflow-x-auto rounded-xl border border-line bg-surface-raised shadow-card">
          <table className="min-w-full divide-y divide-line text-sm">
            <thead className="bg-surface-muted/80">
              <tr>
                {table.header.map((cell) => (
                  <th key={cell} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.12em] text-ink-subtle">
                    {renderInline(cell)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line bg-surface-raised">
              {table.body.map((row, rowIndex) => (
                <tr key={rowIndex} className="transition-colors duration-200 hover:bg-surface-muted/45">
                  {row.map((cell, cellIndex) => (
                    <td key={`${rowIndex}-${cellIndex}`} className="px-4 py-3 align-top text-ink-muted">
                      {renderInline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      index = table.nextIndex;
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const text = heading[2];
      const Tag = level === 1 ? 'h1' : level === 2 ? 'h2' : level === 3 ? 'h3' : 'h4';
      blocks.push(
        <Tag
          key={`h-${index}`}
          className={cn(
            'tracking-tight text-ink',
            level === 1 && 'mt-0 text-2xl font-bold',
            level === 2 && 'mt-8 border-t border-line pt-5 text-xl font-semibold',
            level === 3 && 'mt-6 text-base font-semibold',
            level === 4 && 'mt-4 text-sm font-semibold'
          )}
        >
          {renderInline(text)}
        </Tag>
      );
      index += 1;
      continue;
    }

    if (/^-\s+/.test(trimmed)) {
      const items = [];
      while (index < lines.length && /^-\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^-\s+/, ''));
        index += 1;
      }
      blocks.push(
        <ul key={`ul-${index}`} className="space-y-1.5 pl-5 text-sm leading-6 text-ink-muted">
          {items.map((item, itemIndex) => (
            <li key={itemIndex} className="list-disc">
              {renderInline(item)}
            </li>
          ))}
        </ul>
      );
      continue;
    }

    blocks.push(
      <p key={`p-${index}`} className="text-sm leading-6 text-ink-muted">
        {renderInline(trimmed)}
      </p>
    );
    index += 1;
  }

  return (
    <article className={cn('space-y-3 rounded-xl border border-line bg-surface-raised p-5 shadow-card', className)}>
      {blocks.length ? blocks : <p className="text-sm text-ink-subtle">No documentation available.</p>}
    </article>
  );
};

export default MarkdownDocument;
