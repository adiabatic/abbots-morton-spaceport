export const VERDICT_KINDS = ['approve', 'reject', 'either', 'identical', 'neither', 'skip'];

export const EXPORT_FORMAT = 'ams-review-verdicts/1';
export const DELTA_FORMAT = 'ams-review-verdicts-delta/1';

// `unexported` is every unit changed since the last download, the count the status bar and the beforeunload nag read; `dirty` is every unit changed since the last autosave the server accepted, drained by each flush and refilled when one fails. The two sets move together on a mutation and apart on the events that clear them.
export function createStore() {
  return { records: new Map(), undoStack: [], unexported: new Set(), dirty: new Set() };
}

function touch(store, unitId) {
  store.unexported.add(unitId);
  store.dirty.add(unitId);
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

export function recordVerdictWithEchoes(
  store,
  unitId,
  verdict,
  echoIds = [],
  { note = '', at = new Date().toISOString() } = {},
) {
  if (!VERDICT_KINDS.includes(verdict)) throw new Error(`unknown verdict: ${verdict}`);
  const entries = [{ unit: unitId, prev: store.records.get(unitId) ?? null }];
  store.records.set(unitId, { unit: unitId, verdict, note, at });
  touch(store, unitId);
  for (const id of echoIds) {
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

// The autosave body for a set of changed units: each one's current record under `sets`, or its id under `clears` when it holds no record any more. The units are the store's `dirty` set at flush time; the caller empties it and, on a failed save, puts the same ids back.
export function assembleDelta(store, manifestGeneratedAt, unitIds) {
  const sets = [];
  const clears = [];
  for (const unitId of [...unitIds].sort()) {
    const record = store.records.get(unitId);
    if (record) sets.push({ unit: record.unit, verdict: record.verdict, note: record.note, at: record.at });
    else clears.push(unitId);
  }
  return { format: DELTA_FORMAT, manifest_generated_at: manifestGeneratedAt, sets, clears };
}

export function importVerdicts(store, data, manifestGeneratedAt, { force = false } = {}) {
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
    if (existing && existing.at >= (entry.at ?? '')) {
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

const CARRIED_PROVENANCE_PREFIX = /^(?:\s*\[(?:carried|echo-fill|echo-harmonize|bulk|parked|standing)\b[^\]]*\])+\s*/;

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
