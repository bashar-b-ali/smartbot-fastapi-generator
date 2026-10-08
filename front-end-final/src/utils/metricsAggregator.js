// Recursively aggregate files count and total size for a project by
// walking folder-content endpoints. Designed to be used from the
// frontend with the `getFolderContent(projectId, path)` function
// provided by `ProjectContext`.

async function sleep(ms) {
  return new Promise(res => setTimeout(res, ms));
}

function safeNumber(v) {
  if (v == null) return 0;
  if (typeof v === 'number') return v;
  if (typeof v === 'string') {
    const n = Number(v.replace(/[^0-9.-]+/g, ''));
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

export async function aggregateProjectMetrics(projectId, getFolderContent, opts = {}) {
  const concurrency = opts.concurrency || 4;
  const maxFetches = opts.maxFetches || 1000;
  const delayBetween = opts.delayBetween || 0; // ms

  if (!projectId) return { files: 0, size: 0 };

  const seen = new Set();
  const cache = new Map();
  const queue = ['']; // start at project root path ''
  let active = 0;
  let fetched = 0;
  let files = 0;
  let size = 0;

  const fetchFolder = async (path) => {
    const cacheKey = `${projectId}::${path}`;
    if (cache.has(cacheKey)) return cache.get(cacheKey);
    const resp = await getFolderContent(projectId, path);
    cache.set(cacheKey, resp);
    return resp;
  };

  return new Promise((resolve, reject) => {
    const next = async () => {
      if (fetched >= maxFetches) return resolve({ files, size });
      if (queue.length === 0 && active === 0) return resolve({ files, size });
      while (active < concurrency && queue.length) {
        const path = queue.shift();
        if (seen.has(path)) continue;
        seen.add(path);
        active += 1;
        fetched += 1;
        (async () => {
          try {
            const resp = await fetchFolder(path);
            // If response is an object with aggregate stats, use them
            if (resp && typeof resp === 'object' && !Array.isArray(resp)) {
              // prefer explicit stats keys
              const filesCandidate = resp.files_count ?? resp.file_count ?? resp.total_files ?? null;
              const sizeCandidate = resp.total_size ?? resp.size ?? resp.totalSize ?? resp.total_bytes ?? null;
              const fc = safeNumber(filesCandidate);
              const sc = safeNumber(sizeCandidate);
              if (fc > 0 || sc > 0) {
                files += fc;
                size += sc;
                // if entries present, also descend into them
                if (Array.isArray(resp.entries) && resp.entries.length) {
                  for (const e of resp.entries) {
                    if (!e) continue;
                    const isDir = e.is_dir || e.type === 'dir' || e.isdir || e.directory;
                    if (isDir) {
                      const childPath = e.path ?? e.full_path ?? e.name ?? null;
                      if (childPath) queue.push(childPath);
                    } else {
                      files += 0; // already accounted in filesCandidate
                    }
                  }
                }
                active -= 1;
                if (delayBetween) await sleep(delayBetween);
                return next();
              }
            }

            // If resp is array of entries
            if (Array.isArray(resp)) {
              for (const entry of resp) {
                if (!entry) continue;
                const isDir = entry.is_dir || entry.type === 'dir' || entry.isdir || entry.directory;
                if (isDir) {
                  const childPath = entry.path ?? entry.full_path ?? entry.name ?? null;
                  if (childPath) queue.push(childPath);
                } else {
                  files += 1;
                  const fileSize = safeNumber(entry.size ?? entry.file_size ?? entry.size_bytes ?? entry.total_size ?? 0);
                  size += fileSize;
                }
              }
            } else if (resp && resp.file && typeof resp.file === 'object') {
              // single-file response
              files += 1;
              size += safeNumber(resp.file.size ?? resp.file.file_size ?? resp.file.size_bytes ?? 0);
            }
          } catch (err) {
            // ignore errors for individual folders, continue
            console.warn('aggregateProjectMetrics: failed to fetch', path, err?.message || err);
          } finally {
            active -= 1;
            if (delayBetween) await sleep(delayBetween);
            // continue processing
            next();
          }
        })();
      }
    };
    next();
  });
}

export default aggregateProjectMetrics;
