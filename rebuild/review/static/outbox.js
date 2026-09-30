// The outbox keeps every verdict change this browser has not yet seen the server accept, in localStorage under OUTBOX_KEY, suffixed with the checkout (`scopeOutbox`), so a save that a closed tab, a crash, or a server restart interrupted is sent again the next time the app opens. It maps a unit id to the tab's latest state for that unit: `{ record, base_at, stamp, tab, seq, written_at }`, where `record` is null for a clear, `base_at` is the `at` the tab last saw the server hold, `stamp` is the corpus the change was made on, and `tab` and `seq` name the write. A write replaces the unit's entry, so the entry always mirrors the tab's current state, undo and note edits included; an acknowledgement removes an entry only when its tab and seq still match, so a later write made while a save was in flight survives the save's reply. Every function takes the storage object, so the tests pass a plain one.
// The outbox is kept per checkout: every checkout's review server listens on the same port, so the browser gives them one origin and one localStorage, and a change made on one checkout's corpus must not be sent to another's store. `scopeOutbox` keys the outbox by the repo root the server names on /capabilities.
// Each open tab also records when it was last seen under TABS_KEY, every TAB_HEARTBEAT_MS, and removes itself on pagehide, so a page that boots replays only the entries of tabs that are gone: an entry a tab still open has pending (in its debounce or its retry backoff) is that tab's to send. A tab not seen for TAB_FRESH_MS counts as gone, which is longer than a browser's throttling of a background tab's timers.

export const OUTBOX_KEY = 'ams-review-outbox';
export const TABS_KEY = 'ams-review-tabs';
export const CONFLICTS_KEY = 'ams-review-conflicts';
export const REPLAY_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
export const TAB_HEARTBEAT_MS = 20 * 1000;
export const TAB_FRESH_MS = 3 * 60 * 1000;

function readKey(storage, key) {
  let parsed = null;
  try {
    parsed = JSON.parse(storage.getItem(key) ?? '{}');
  } catch {
    parsed = null;
  }
  return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
}

function saveKey(storage, key, value) {
  try {
    if (Object.keys(value).length === 0) storage.removeItem(key);
    else storage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

export function scopedOutboxKey(scope) {
  return typeof scope === 'string' && scope !== '' ? `${OUTBOX_KEY}:${scope}` : OUTBOX_KEY;
}

// A storage object whose outbox is the one for `scope` (the server's repo root), and whose other keys are the storage's own. An outbox kept under the unscoped key, which a page wrote before outboxes were kept per checkout, is moved into this scope, keeping any entry the scope already has for the same unit. With no scope the storage is returned as it is.
export function scopeOutbox(storage, scope) {
  const key = scopedOutboxKey(scope);
  if (key === OUTBOX_KEY) return storage;
  const scoped = {
    getItem: (name) => storage.getItem(name === OUTBOX_KEY ? key : name),
    setItem: (name, value) => storage.setItem(name === OUTBOX_KEY ? key : name, value),
    removeItem: (name) => storage.removeItem(name === OUTBOX_KEY ? key : name),
  };
  const legacy = readKey(storage, OUTBOX_KEY);
  if (Object.keys(legacy).length > 0 && saveKey(storage, key, { ...legacy, ...readKey(storage, key) })) {
    storage.removeItem(OUTBOX_KEY);
  }
  return scoped;
}

export function readOutbox(storage) {
  return readKey(storage, OUTBOX_KEY);
}

function save(storage, outbox) {
  return saveKey(storage, OUTBOX_KEY, outbox);
}

// Records that `tab` is open at `now`, and forgets the tabs not seen within TAB_FRESH_MS.
export function markTabAlive(storage, tab, now = Date.now()) {
  const tabs = readKey(storage, TABS_KEY);
  for (const [id, seen] of Object.entries(tabs)) {
    if (!(now - seen <= TAB_FRESH_MS)) delete tabs[id];
  }
  tabs[tab] = now;
  return saveKey(storage, TABS_KEY, tabs);
}

export function markTabClosed(storage, tab) {
  const tabs = readKey(storage, TABS_KEY);
  if (!(tab in tabs)) return true;
  delete tabs[tab];
  return saveKey(storage, TABS_KEY, tabs);
}

// The ids of the tabs seen within TAB_FRESH_MS.
export function liveTabs(storage, now = Date.now()) {
  const live = new Set();
  for (const [id, seen] of Object.entries(readKey(storage, TABS_KEY))) {
    if (now - seen <= TAB_FRESH_MS) live.add(id);
  }
  return live;
}

// `entries` is an iterable of [unit, entry] pairs. Returns false when the storage refused the write (a quota overflow or storage that is switched off).
export function writeOutbox(storage, entries) {
  const outbox = readOutbox(storage);
  for (const [unit, entry] of entries) outbox[unit] = entry;
  return save(storage, outbox);
}

// `acks` is an iterable of { unit, tab, seq }.
export function ackOutbox(storage, acks) {
  const outbox = readOutbox(storage);
  let changed = false;
  for (const { unit, tab, seq } of acks) {
    const entry = outbox[unit];
    if (entry && entry.tab === tab && entry.seq === seq) {
      delete outbox[unit];
      changed = true;
    }
  }
  return changed ? save(storage, outbox) : true;
}

// Removes the entries for `units` whatever wrote them, or only those `tab` wrote when it is given.
export function dropOutbox(storage, units, tab = null) {
  const outbox = readOutbox(storage);
  let changed = false;
  for (const unit of units) {
    const entry = outbox[unit];
    if (entry && (tab === null || entry.tab === tab)) {
      delete outbox[unit];
      changed = true;
    }
  }
  return changed ? save(storage, outbox) : true;
}

export function sameRecord(a, b) {
  if (!a || !b) return !a && !b;
  return a.verdict === b.verdict && (a.note ?? '') === (b.note ?? '') && (a.at ?? '') === (b.at ?? '');
}

const CARRIED_PREFIX = /^(?:\[carried [^\]]*\]\s*)+/u;

// Whether the server's record is the entry's, allowing for the `[carried …]` provenance the carry puts before the note of each record it moves onto a rebuilt corpus.
export function heldByServer(record, serverRecord) {
  if (sameRecord(record, serverRecord)) return true;
  if (!record || !serverRecord) return false;
  const bare = (note) => (note ?? '').replace(CARRIED_PREFIX, '');
  return (
    record.verdict === serverRecord.verdict &&
    (record.at ?? '') === (serverRecord.at ?? '') &&
    bare(record.note) === bare(serverRecord.note)
  );
}

// The entries among `entries` (each with its `unit`) whose outbox entry is still the one with their tab and seq, so a page about to send what it read at boot skips an entry its tab has since replaced or saved.
export function currentEntries(storage, entries) {
  const outbox = readOutbox(storage);
  return entries.filter((entry) => outbox[entry.unit]?.tab === entry.tab && outbox[entry.unit]?.seq === entry.seq);
}

// Sorts the outbox against the server's records at boot. The entries of the tabs in `live` are left alone, because those tabs are still open to send them. An entry whose record the server already holds (a clear the server has no record for included, and a record the carry moved with its provenance) was saved, so it is dropped. The rest are replayed when written within `maxAgeMs`, grouped by the stamp they were made on, and the older ones are left for the reader to download.
export function planOutboxReplay(
  outbox,
  serverRecords,
  { now = Date.now(), maxAgeMs = REPLAY_MAX_AGE_MS, live = new Set() } = {},
) {
  const drop = [];
  const replay = new Map();
  const stale = [];
  for (const [unit, entry] of Object.entries(outbox)) {
    if (!entry || typeof entry.stamp !== 'string') {
      drop.push(unit);
      continue;
    }
    if (live.has(entry.tab)) continue;
    if (heldByServer(entry.record, serverRecords.get(unit) ?? null)) {
      drop.push(unit);
      continue;
    }
    const writtenAt = Date.parse(entry.written_at ?? '');
    if (!(now - writtenAt <= maxAgeMs)) {
      stale.push({ unit, ...entry });
      continue;
    }
    if (!replay.has(entry.stamp)) replay.set(entry.stamp, []);
    replay.get(entry.stamp).push({ unit, ...entry });
  }
  return { drop, replay, stale };
}

// The conflicts a tab has not reapplied, kept in the tab's sessionStorage under CONFLICTS_KEY so they outlive a reload, the one that moves the tab onto a rebuilt corpus included. `conflicts` maps a unit to the record the tab sent (null for a clear).
export function saveConflicts(storage, conflicts) {
  const kept = {};
  for (const [unit, record] of conflicts) kept[unit] = record;
  return saveKey(storage, CONFLICTS_KEY, kept);
}

export function loadConflicts(storage) {
  const conflicts = new Map();
  for (const [unit, record] of Object.entries(readKey(storage, CONFLICTS_KEY))) {
    if (record === null || (record && typeof record.verdict === 'string')) conflicts.set(unit, record);
  }
  return conflicts;
}
