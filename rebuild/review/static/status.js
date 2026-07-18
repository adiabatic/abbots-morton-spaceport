const FAIL_SCAN = ['surface', 'freshness', 'gates', 'verdict_store'];
const WARN_SCAN = ['surface', 'freshness', 'gates', 'verdict_store', 'frontier'];

// A remedy is copyable when it is itself a shell command rather than prose ("reload the page", "Import … in the app."); every command remedy status.py emits starts with make or uv run.
export function remedyCommand(remedy) {
  return remedy && /^(make|uv run) /u.test(remedy) ? remedy : null;
}

export function bannerModel(status, pageGeneratedAt) {
  if (!status || status.error || !status.checks) {
    const text = status && status.error ? `Status unavailable — ${status.error}` : 'Status unavailable';
    return { level: 'error', text, remedy: 'restart the review server (make review-serve)', command: 'make review-serve' };
  }
  const surfaceStamp = status.surface?.generated_at;
  if (surfaceStamp && pageGeneratedAt && surfaceStamp !== pageGeneratedAt) {
    return { level: 'stale', text: 'Surface rebuilt since this page loaded', remedy: 'reload the page', command: null };
  }
  const checks = status.checks;
  for (const name of FAIL_SCAN) {
    const check = checks[name];
    if (check && check.level === 'fail') {
      return { level: 'stale', text: check.detail, remedy: check.remedy, command: remedyCommand(check.remedy) };
    }
  }
  for (const name of WARN_SCAN) {
    const check = checks[name];
    if (check && check.level === 'warn') {
      return { level: 'warn', text: check.detail, remedy: check.remedy, command: remedyCommand(check.remedy) };
    }
  }
  const count = checks.blanks?.count ?? null;
  const text = count === null ? 'Ready' : `Ready — ${count} blanks left`;
  return { level: 'ready', text, remedy: null, command: null };
}
