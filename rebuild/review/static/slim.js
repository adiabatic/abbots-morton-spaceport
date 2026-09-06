// The app boots from the build's slim human-units index rather than from the class shards, so the tab's resident set is the queue awaiting a verdict rather than the whole corpus. This module holds the pure parts of that: the incremental NDJSON split, the header stamp check, the byte-span addressing that fetches a unit's record back out of its shard — its samples when its card is drawn, its explain material when the panel opens — the coalescing of a window's spans into Range requests, the locator's block table and the binary search a deep link runs over it, the bounded record cache, and the manifest-derived plan for the show-machine folds. They live here rather than in app.js because app.js top-level-awaits its manifest fetch and so can never be imported by node --test.

import { machineFoldChannel, machineFoldTotal, needsNoVerdict } from './render.js';

export const APP_INDEX_NAME = 'app-units.ndjson.gz';
export const APP_INDEX_FORMAT = 'ams-review-app-index/1';
export const LOCATOR_NAME = 'app-locator.ndjson.gz';
export const LOCATOR_FORMAT = 'ams-review-app-locator/2';
export const LOCATOR_ROWS_NAME = 'app-locator-rows.ndjson.gz';
export const RECORD_CACHE_CAP = 64;
// Blocks of the locator's rows file held decoded at once: a fold walks its class's blocks in order and a deep link reads one candidate per class, so a handful covers both without any block being read twice in a row.
export const BLOCK_CACHE_CAP = 8;
// Rows a show-machine fold draws per window. A window costs one Range request per run of neighboring spans plus one card per row, and a reader who wants the next window asks for it.
export const MACHINE_FOLD_WINDOW = 64;
// Two spans closer than this in the same shard part are fetched with one Range request and the bytes between them discarded — a human fragment or two between a window's machine rows, which is cheaper than another round trip; further apart, and a request each costs less than the bytes it would skip.
export const SPAN_GAP_BYTES = 16384;

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

// Whether a unit carries what its sample cells draw. A row out of the app index does not — `text_entities`, `highlight` and `after.cells` are read out of the shard record the card fetches on render — while a shard record always does, a slim fragment included, which carries its text and cells and omits only the pair band.
export function carriesSamples(unit) {
  return Boolean(unit) && typeof unit.text_entities === 'string';
}

// Whether a string is a unit id: `u-` and eleven base58 symbols (the alphabet without 0, O, I and l), the build's content-addressed shape. Ids have no order but their own string order, which is the order the build writes a class's fragments and locator rows in.
const UNIT_ID = /^u-[1-9A-HJ-NP-Za-km-z]{11}$/;
export function isUnitId(unitId) {
  return typeof unitId === 'string' && UNIT_ID.test(unitId);
}

// The locator's block table grouped by class, each class's blocks in the order the file lists them, which is ascending by the ids they hold — the shape both the folds and the deep-link search read.
export function indexLocatorBlocks(blocks) {
  const byClass = new Map();
  for (const block of blocks) {
    if (!byClass.has(block.class)) byClass.set(block.class, []);
    byClass.get(block.class).push(block);
  }
  return byClass;
}

// The blocks that can hold a unit id: at most one per class, found by binary search over that class's blocks comparing ids as strings, since a class's blocks are disjoint and ascending while the classes' id ranges overlap one another. A deep link fetches these and no others.
export function candidateBlocks(byClass, unitId) {
  const found = [];
  if (!isUnitId(unitId)) return found;
  for (const blocks of byClass.values()) {
    let low = 0;
    let high = blocks.length - 1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      const block = blocks[mid];
      if (block.last < unitId) low = mid + 1;
      else if (block.first > unitId) high = mid - 1;
      else {
        found.push(block);
        break;
      }
    }
  }
  return found;
}

// The Range requests that read a set of shard spans: rows sorted by part and offset, neighbors within `gap` bytes in the same part sharing one request. Each run names the bytes to ask for and the rows to slice out of them.
export function coalesceSpans(rows, gap = SPAN_GAP_BYTES) {
  const sorted = [...rows].sort((a, b) => a.shard_part - b.shard_part || a.byte_start - b.byte_start);
  const runs = [];
  let run = null;
  for (const row of sorted) {
    const end = row.byte_start + row.byte_length;
    if (run && run.shard_part === row.shard_part && row.byte_start - run.end <= gap) {
      run.end = Math.max(run.end, end);
      run.rows.push(row);
      continue;
    }
    run = { class: row.class, shard_part: row.shard_part, byte_start: row.byte_start, end, rows: [row] };
    runs.push(run);
  }
  for (const entry of runs) entry.byte_length = entry.end - entry.byte_start;
  return runs;
}

// One row's fragment out of the text a run's Range request returned. The shard is ASCII, so a byte offset is a character offset and the slice is the same bytes `_write_shard` framed.
export function sliceRecordText(runText, run, row) {
  const start = row.byte_start - run.byte_start;
  return runText.slice(start, start + row.byte_length);
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
