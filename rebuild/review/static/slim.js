// The app boots from the build's slim human-units index rather than from the class shards, so the tab's resident set is the queue awaiting a verdict rather than the whole corpus. This module holds the pure parts of that: the incremental NDJSON split, the header stamp check, the byte-span addressing that fetches a unit's explain material back out of its shard, the bounded record cache, and the manifest-derived plan for the show-machine folds. They live here rather than in app.js because app.js top-level-awaits its manifest fetch and so can never be imported by node --test.

import { machineFoldChannel, machineFoldTotal, needsNoVerdict } from './render.js';

export const APP_INDEX_NAME = 'app-units.ndjson.gz';
export const APP_INDEX_FORMAT = 'ams-review-app-index/1';
export const LOCATOR_NAME = 'app-locator.ndjson.gz';
export const LOCATOR_FORMAT = 'ams-review-app-locator/1';
export const RECORD_CACHE_CAP = 64;

export function createLineSplitter() {
  return { tail: '' };
}

export function splitLines(state, text) {
  const parts = (state.tail + text).split('\n');
  state.tail = parts.pop();
  return parts;
}

export function finishLines(state) {
  const { tail } = state;
  state.tail = '';
  return tail ? [tail] : [];
}

// The gzip magic number, read off the first bytes of a sidecar's body. A server that declares Content-Encoding: gzip has already decoded it and these bytes are the NDJSON; one that hands the file over as-is has not, and the app decompresses it itself rather than requiring a particular server to read an archived surface.
export function looksGzipped(bytes) {
  return Boolean(bytes) && bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b;
}

export function checkIndexHeader(header, manifest, format) {
  if (!header || typeof header !== 'object') return { ok: false, reason: 'it carries no readable header line' };
  if (header.format !== format) {
    return { ok: false, reason: `its format is ${header.format ?? 'missing'}, not ${format}` };
  }
  if (header.generated_at !== manifest?.generated_at) {
    return {
      ok: false,
      reason: `it is stamped ${header.generated_at ?? 'nothing'} but this surface was generated ${manifest?.generated_at}`,
    };
  }
  return { ok: true, reason: null };
}

// The build writes a machine-approved or no-verdict unit's shard fragment slim: `explain`, `drafts` and `highlight` are absent — never null — because the fold that draws it renders the cells, the seams and the summary, not the explain table, the drafts or the pair band (`SLIM_OMITTED_KEYS` in rebuild/review/audit.py is the authority on the list, `check_unit` there holds the shape exact). Absent rather than empty is what tells a slim fragment from a whole record with a blank field, and a human row out of the app index is neither: it takes a verdict, so its explain material is in its shard and fetched on open.
export function isSlimFragment(unit) {
  return Boolean(unit) && needsNoVerdict(unit) && !('explain' in unit) && !('drafts' in unit);
}

// A slim row carries none of the three fields the explain panel renders, so their absence is what says the panel has to be filled from the unit's shard record; a machine unit built from that record already has everything the build wrote for it — a slim fragment included, whose panel says what was left out — and opens as it always did.
export function hasExplainSource(unit) {
  return Boolean(unit) && (isSlimFragment(unit) || 'explain' in unit || 'provenance' in unit || 'drafts' in unit);
}

export function shardPartPath(manifest, row) {
  const cls = (manifest?.classes ?? []).find((entry) => entry.id === row?.class);
  const parts = cls?.shards;
  if (!Array.isArray(parts)) return null;
  return parts[row.shard_part] ?? null;
}

// Inclusive on both ends, which is what tornado's StaticFileHandler parses `bytes=A-B` as, so this asks for exactly byte_length bytes.
export function rangeHeader(row) {
  return `bytes=${row.byte_start}-${row.byte_start + row.byte_length - 1}`;
}

export function createRecordCache(cap = RECORD_CACHE_CAP) {
  const entries = new Map();
  return {
    get size() {
      return entries.size;
    },
    has(key) {
      return entries.has(key);
    },
    get(key) {
      if (!entries.has(key)) return undefined;
      const value = entries.get(key);
      entries.delete(key);
      entries.set(key, value);
      return value;
    },
    set(key, value) {
      entries.delete(key);
      entries.set(key, value);
      while (entries.size > cap) entries.delete(entries.keys().next().value);
      return value;
    },
    keys() {
      return [...entries.keys()];
    },
    clear() {
      entries.clear();
    },
  };
}

// Which show-machine folds the current view carries, and what each one's summary line says before it is opened. Mirrors unitsForView's class selection, so a fold appears exactly where a class's machine units would sit in the batch; a worklist supplies its machine records explicitly and needs no plan.
//
// The class filter is the only one the manifest can answer. Family, group, and config are per-unit, so while any of them is set a fold's total is an upper bound rather than a count, and `provisional` is what tells the view to say so instead of reporting a whole class's units as matching a filter that may exclude every one of them.
export function machineFoldPlan(manifest, state) {
  if (state.units || state.machine !== '1') return [];
  const provisional = Boolean(state.family || state.group || state.config);
  const plan = [];
  for (const cls of manifest?.classes ?? []) {
    if (state.class) {
      if (cls.id !== state.class) continue;
    } else if (!cls.batches.includes(state.batch) && !(cls.batches.length === 0 && state.batch === 0)) {
      continue;
    }
    const total = machineFoldTotal(cls);
    if (total === 0) continue;
    plan.push({ classId: cls.id, total, channel: machineFoldChannel(cls), provisional });
  }
  return plan;
}
