import { formatCount } from './render.js';

const FAIL_SCAN = ['corpus', 'freshness', 'gates', 'verdict_store'];
const WARN_SCAN = ['corpus', 'freshness', 'gates', 'verdict_store', 'fullest_verdicts'];

// A remedy is copyable only when the whole string is a shell command. Every command remedy status.py emits starts with `make` or `uv run`; the prose remedies ("reload the page", "Merge … into the autosave: uv run …") do not.
export function remedyCommand(remedy) {
  return remedy && /^(make|uv run) /u.test(remedy) ? remedy : null;
}

// The saves the verdict store kept aside since `pageLoadedAt` (an ISO time): conflicts and orphans the server recorded in its orphan documents, counted from each kind's `recent` list of [recorded_at, count] pairs that /status returns. `recorded_at` has whole seconds, so the page's load time is taken to the second. Null when none was kept since then.
export function keptAsideLine(status, pageLoadedAt) {
  const loaded = Date.parse(pageLoadedAt ?? '');
  if (Number.isNaN(loaded)) return null;
  const since = Math.floor(loaded / 1000) * 1000;
  const parts = [];
  let file = null;
  for (const [key, label] of [
    ['conflicts', 'conflicted'],
    ['orphans', 'orphaned'],
  ]) {
    const entry = status?.[key];
    let count = 0;
    for (const [at, n] of Array.isArray(entry?.recent) ? entry.recent : []) {
      if (Date.parse(at) >= since) count += n;
    }
    if (count === 0) continue;
    parts.push(`${formatCount(count)} ${label}`);
    file ??= entry.file ?? null;
  }
  if (parts.length === 0) return null;
  return { text: `Saves kept aside by the server: ${parts.join(', ')}`, file };
}

export function bannerModel(status, pageGeneratedAt, pageLoadedAt = null) {
  if (!status || status.error || !status.checks) {
    const text = status && status.error ? `Status unavailable — ${status.error}` : 'Status unavailable';
    return { level: 'error', text, remedy: 'restart the review server (make review-serve)', command: 'make review-serve' };
  }
  const corpusStamp = status.corpus?.generated_at;
  if (corpusStamp && pageGeneratedAt && corpusStamp !== pageGeneratedAt) {
    return { level: 'stale', text: 'Corpus rebuilt since this page loaded', remedy: 'reload the page', command: null };
  }
  const checks = status.checks;
  for (const name of FAIL_SCAN) {
    const check = checks[name];
    if (check && check.level === 'fail') {
      return { level: 'stale', text: check.detail, remedy: check.remedy, command: remedyCommand(check.remedy) };
    }
  }
  const keptAside = keptAsideLine(status, pageLoadedAt);
  if (keptAside) {
    return { level: 'warn', text: keptAside.text, remedy: keptAside.file ? `see ${keptAside.file}` : null, command: null };
  }
  for (const name of WARN_SCAN) {
    const check = checks[name];
    if (check && check.level === 'warn') {
      return { level: 'warn', text: check.detail, remedy: check.remedy, command: remedyCommand(check.remedy) };
    }
  }
  const count = checks.blanks?.count ?? null;
  const text = count === null ? 'Ready' : `Ready — ${formatCount(count)} blanks left`;
  return { level: 'ready', text, remedy: null, command: null };
}
