export const VERDICT_KINDS = ['approve', 'reject', 'either', 'identical', 'neither', 'skip'];

export const EXPORT_FORMAT = 'ams-review-verdicts/1';
export const DELTA_FORMAT = 'ams-review-verdicts-delta/1';

// `unexported` holds the units changed since the last download; the status bar's unexported count and the beforeunload prompt read it. `dirty` holds the units changed since the last autosave the server accepted; each flush empties it, and a failed flush puts the ids back. `touch` adds a unit to both, and different events clear each one. `serverAt` maps a unit to the `at` of the record the server last acknowledged holding for it, or null when it acknowledged holding none, and each save sends it as `base_at`, so a server that carries the save onto a rebuilt corpus can tell a stale change from one made over what it holds. `clearedAt` maps a cleared unit to when it was cleared, and `changedAt` maps each unit to when this page last changed it, which a sync compares against a record another session saved while the change waits to be sent.
export function createStore() {
  return {
    records: new Map(),
    undoStack: [],
    unexported: new Set(),
    dirty: new Set(),
    serverAt: new Map(),
    clearedAt: new Map(),
    changedAt: new Map(),
  };
}

function touch(store, unitId) {
  const now = new Date().toISOString();
  store.unexported.add(unitId);
  store.dirty.add(unitId);
  store.changedAt.set(unitId, now);
  if (store.records.has(unitId)) store.clearedAt.delete(unitId);
  else store.clearedAt.set(unitId, now);
}

export function recordVerdict(store, unitId, verdict, { note = '', at = new Date().toISOString() } = {}) {
  if (verdict !== null && !VERDICT_KINDS.includes(verdict)) throw new Error(`unknown verdict: ${verdict}`);
  const prev = store.records.get(unitId) ?? null;
  if (verdict === null && !prev) return null;
  if (verdict === null) {
    store.records.delete(unitId);
  } else {
    store.records.set(unitId, { unit: unitId, verdict, note, at });
  }
  store.undoStack.push({ type: 'verdict', unit: unitId, prev });
  touch(store, unitId);
  return store.records.get(unitId) ?? null;
}

export function recordVerdictWithDuplicates(
  store,
  unitId,
  verdict,
  duplicateIds = [],
  { note = '', at = new Date().toISOString() } = {},
) {
  if (!VERDICT_KINDS.includes(verdict)) throw new Error(`unknown verdict: ${verdict}`);
  const entries = [{ unit: unitId, prev: store.records.get(unitId) ?? null }];
  store.records.set(unitId, { unit: unitId, verdict, note, at });
  touch(store, unitId);
  for (const id of duplicateIds) {
    if (id === unitId || store.records.has(id)) continue;
    entries.push({ unit: id, prev: null });
    store.records.set(id, { unit: id, verdict, note, at });
    touch(store, id);
  }
  if (entries.length === 1) store.undoStack.push({ type: 'verdict', unit: unitId, prev: entries[0].prev });
  else store.undoStack.push({ type: 'group', entries });
  const applied = [];
  for (const entry of entries) applied.push(entry.unit);
  return applied;
}

export function updateNote(store, unitId, note) {
  const record = store.records.get(unitId);
  if (!record || record.note === note) return false;
  record.note = note;
  touch(store, unitId);
  return true;
}

export function groupApprove(store, unitIds, { at = new Date().toISOString() } = {}) {
  const entries = [];
  for (const unitId of unitIds) {
    if (store.records.has(unitId)) continue;
    entries.push({ unit: unitId, prev: null });
    store.records.set(unitId, { unit: unitId, verdict: 'approve', note: '', at });
    touch(store, unitId);
  }
  if (entries.length > 0) store.undoStack.push({ type: 'group', entries });
  const applied = [];
  for (const entry of entries) applied.push(entry.unit);
  return applied;
}

export function undo(store) {
  const action = store.undoStack.pop();
  if (!action) return null;
  const restore = (unitId, prev) => {
    if (prev) store.records.set(unitId, prev);
    else store.records.delete(unitId);
    touch(store, unitId);
  };
  if (action.type === 'verdict') {
    restore(action.unit, action.prev);
    return { units: [action.unit], cursor: action.unit };
  }
  const units = [];
  for (const entry of action.entries) {
    restore(entry.unit, entry.prev);
    units.push(entry.unit);
  }
  return { units, cursor: units[0] };
}

export function assembleExport(store, manifestGeneratedAt, exportedAt = new Date().toISOString()) {
  const verdicts = [];
  for (const record of store.records.values()) {
    verdicts.push({ unit: record.unit, verdict: record.verdict, note: record.note, at: record.at });
  }
  verdicts.sort((a, b) => (a.unit < b.unit ? -1 : a.unit > b.unit ? 1 : 0));
  return { format: EXPORT_FORMAT, manifest_generated_at: manifestGeneratedAt, exported_at: exportedAt, verdicts };
}

export function markExported(store) {
  store.unexported.clear();
}

// The autosave body for a set of units: each unit's current record under `sets`, or `{ unit, base_at, at }` under `clears` when it has no record, where `at` is when it was cleared. Both carry the unit's `serverAt` as `base_at`. The caller takes the ids from the store's `dirty` set and puts them back if the save fails.
export function assembleDelta(store, manifestGeneratedAt, unitIds) {
  const sets = [];
  const clears = [];
  for (const unitId of [...unitIds].sort()) {
    const record = store.records.get(unitId);
    const baseAt = store.serverAt.get(unitId) ?? null;
    if (record) {
      sets.push({ unit: record.unit, verdict: record.verdict, note: record.note, at: record.at, base_at: baseAt });
    } else {
      clears.push({ unit: unitId, base_at: baseAt, at: store.clearedAt.get(unitId) ?? new Date().toISOString() });
    }
  }
  return { format: DELTA_FORMAT, manifest_generated_at: manifestGeneratedAt, sets, clears };
}

// The body that sends outbox entries (`outbox.js`) again: each entry's record under `sets`, or a clear dated when the entry was written, with the `base_at` the entry recorded. `replay: true` makes the server test each unit instead of taking the last write.
export function replayDelta(stamp, entries) {
  const sets = [];
  const clears = [];
  for (const entry of entries) {
    if (entry.record) sets.push({ ...entry.record, unit: entry.unit, base_at: entry.base_at ?? null });
    else clears.push({ unit: entry.unit, base_at: entry.base_at ?? null, at: entry.written_at });
  }
  return { format: DELTA_FORMAT, manifest_generated_at: stamp, replay: true, sets, clears };
}

// Records what the server now holds after it accepted `delta`: each set's `at`, and null for each clear.
export function acknowledgeDelta(store, delta) {
  for (const record of delta.sets) store.serverAt.set(record.unit, record.at);
  for (const clear of delta.clears) store.serverAt.set(clear.unit, null);
}

// Records the `at` of records the server sent (a restore or a sync).
export function noteServerRecords(store, records) {
  for (const record of records) {
    if (record && typeof record.unit === 'string') store.serverAt.set(record.unit, record.at ?? '');
  }
}

// Replaces the tab's record for `unitId` with the server's (null when the server holds none) after the server refused the tab's change as a conflict.
export function adoptServerRecord(store, unitId, record) {
  if (record) {
    store.records.set(unitId, { unit: unitId, verdict: record.verdict, note: record.note ?? '', at: record.at ?? '' });
  } else {
    store.records.delete(unitId);
  }
  store.serverAt.set(unitId, record ? (record.at ?? '') : null);
  store.dirty.delete(unitId);
  store.unexported.delete(unitId);
}

// `pendingAt(unit)` returns when this page made a change to the unit that the server has not yet acknowledged, or undefined when it has none: an incoming record replaces such a unit only when it is newer than that change, so a sync never reverts a clear or an undo waiting to be sent.
export function importVerdicts(store, data, manifestGeneratedAt, { force = false, pendingAt = null } = {}) {
  if (!data || data.format !== EXPORT_FORMAT || !Array.isArray(data.verdicts)) {
    return { ok: false, error: `not an ${EXPORT_FORMAT} document` };
  }
  const mismatch = data.manifest_generated_at !== manifestGeneratedAt;
  if (mismatch && !force) return { ok: false, mismatch };
  let added = 0;
  let replaced = 0;
  let keptNewer = 0;
  let invalid = 0;
  const units = [];
  for (const entry of data.verdicts) {
    if (!entry || typeof entry.unit !== 'string' || !VERDICT_KINDS.includes(entry.verdict)) {
      invalid += 1;
      continue;
    }
    const existing = store.records.get(entry.unit);
    const pending = pendingAt?.(entry.unit);
    const kept = [existing?.at, pending].filter((at) => typeof at === 'string');
    if (kept.length > 0 && kept.some((at) => at >= (entry.at ?? ''))) {
      keptNewer += 1;
      continue;
    }
    store.records.set(entry.unit, {
      unit: entry.unit,
      verdict: entry.verdict,
      note: entry.note ?? '',
      at: entry.at ?? '',
    });
    touch(store, entry.unit);
    units.push(entry.unit);
    if (existing) replaced += 1;
    else added += 1;
  }
  return { ok: true, mismatch, added, replaced, keptNewer, invalid, units };
}

const CARRIED_PROVENANCE_PREFIX = /^(?:\s*\[(?:carried|duplicate-fill|duplicate-harmonize|echo-fill|echo-harmonize|bulk|deferred|parked|standing)\b[^\]]*\])+\s*/;

export function stripCarriedProvenance(note) {
  return note.replace(CARRIED_PROVENANCE_PREFIX, '');
}

export function recentNotes(store, verdict = null, { limit = 10, exclude = [] } = {}) {
  const excluded = new Set(exclude);
  const newestAt = new Map();
  for (const record of store.records.values()) {
    if (verdict !== null && record.verdict !== verdict) continue;
    const note = stripCarriedProvenance(record.note);
    if (!note || excluded.has(note)) continue;
    const seen = newestAt.get(note);
    if (seen === undefined || record.at > seen) newestAt.set(note, record.at);
  }
  const notes = [...newestAt.keys()];
  notes.sort((a, b) => (newestAt.get(a) < newestAt.get(b) ? 1 : newestAt.get(a) > newestAt.get(b) ? -1 : 0));
  return notes.slice(0, limit);
}

export function verdictCounts(store) {
  const counts = { approve: 0, reject: 0, either: 0, identical: 0, neither: 0, skip: 0 };
  for (const record of store.records.values()) counts[record.verdict] += 1;
  return counts;
}
