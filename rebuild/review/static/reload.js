// How the page moves onto a changed corpus or a refreshed set of app files. The server never reloads a tab on its own: the cycle sends `ams:corpus/<generated_at>` when the served corpus changes and `ams:assets/<static hash>` after an assets refresh, through livereload's /forcereload, and `reload-plugin.js` hands those paths to the app, which flushes its saves and reloads when the reader is not typing. The page's identity is the corpus stamp and the app-files hash it loaded with. Before reloading, the app records where it moved from (MOVED_FROM_KEY) and what the view held beyond its URL hash (VIEW_EXTRAS_KEY), both in sessionStorage, so the reloaded page can restore the view and never loops on a move that did not happen.

export const RELOAD_EVENT = 'ams-reload';
export const MOVED_FROM_KEY = 'ams-review-moved-from';
export const VIEW_EXTRAS_KEY = 'ams-review-view-extras';
export const VIEW_EXTRAS_MAX_AGE_MS = 60_000;
const KINDS = new Set(['corpus', 'assets']);

export function parseReloadPath(path) {
  if (typeof path !== 'string') return null;
  const match = /^ams:([^/]+)\/(.+)$/u.exec(path);
  return match && KINDS.has(match[1]) ? { kind: match[1], value: match[2] } : null;
}

export function pageIdentity(manifest) {
  return {
    corpus: typeof manifest?.generated_at === 'string' ? manifest.generated_at : null,
    assets: typeof manifest?.inputs_fingerprint?.static === 'string' ? manifest.inputs_fingerprint.static : null,
  };
}

export function targetKey(target) {
  return `${target.kind}/${target.value}`;
}

export function movesPage(target, identity) {
  if (!KINDS.has(target?.kind)) return false;
  return identity[target.kind] !== target.value;
}

export function writeMoveGuard(identity, target) {
  return JSON.stringify({ from: identity, target });
}

// The target a reloaded page must not move to again: the one the page before it reloaded for, when this page loaded with the same identity for that kind, so the reload did not reach it. Null otherwise.
export function readMoveGuard(raw, identity) {
  let parsed = null;
  try {
    parsed = JSON.parse(raw ?? '');
  } catch {
    return null;
  }
  const target = parsed?.target;
  if (!KINDS.has(target?.kind) || typeof target.value !== 'string') return null;
  if (parsed.from?.[target.kind] !== identity[target.kind]) return null;
  return { kind: target.kind, value: target.value };
}

// `notes` holds [unit, text] pairs for note fields whose text differs from the unit's saved note, which includes a note typed on a unit with no verdict yet. `keptAsideSince` is the time (ISO) from which the page counts the saves the server kept aside as conflicts or orphans for its banner, so the reloaded page still shows those its last saves before the move got.
export function writeViewExtras({ hash, scrollY, queueAnchor, notes, keptAsideSince = null }, now = Date.now()) {
  return JSON.stringify({ hash, scrollY, queueAnchor, notes, keptAsideSince, at: now });
}

// The extras saved before a reload, when they were saved for this URL hash within VIEW_EXTRAS_MAX_AGE_MS; null otherwise.
export function readViewExtras(raw, hash, now = Date.now()) {
  let parsed = null;
  try {
    parsed = JSON.parse(raw ?? '');
  } catch {
    return null;
  }
  if (!parsed || parsed.hash !== hash || typeof parsed.at !== 'number') return null;
  if (now < parsed.at || now - parsed.at > VIEW_EXTRAS_MAX_AGE_MS) return null;
  const anchor = parsed.queueAnchor;
  const notes = [];
  for (const pair of Array.isArray(parsed.notes) ? parsed.notes : []) {
    if (Array.isArray(pair) && typeof pair[0] === 'string' && typeof pair[1] === 'string') notes.push([pair[0], pair[1]]);
  }
  const since = parsed.keptAsideSince;
  return {
    scrollY: Number.isFinite(parsed.scrollY) ? parsed.scrollY : 0,
    queueAnchor: anchor && typeof anchor.cluster === 'string' && Number.isFinite(anchor.delta) ? anchor : null,
    notes,
    keptAsideSince: typeof since === 'string' && !Number.isNaN(Date.parse(since)) ? since : null,
  };
}
