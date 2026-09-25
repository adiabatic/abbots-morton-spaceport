// Pure helpers for loading units without holding the corpus: the incremental NDJSON line split, the sidecar header check, the byte-span addressing that fetches a unit's record from its shard, the coalescing of spans into Range requests, the locator's block table and the binary search a deep link runs over it, the bounded record cache, and the plan for the show-machine folds. The app boots from the app index (one row per unit awaiting a verdict) instead of the class shards, so the tab's memory grows with the queue, not with the corpus; rebuild/review/app_index.py describes the files. These functions live here because app.js awaits its manifest fetch at top level, so node --test cannot import it.

import { machineFoldChannel, machineFoldTotal, needsNoVerdict } from './render.js';

export const APP_INDEX_NAME = 'app-units.ndjson.gz';
export const APP_INDEX_FORMAT = 'ams-review-app-index/1';
export const LOCATOR_NAME = 'app-locator.ndjson.gz';
export const LOCATOR_FORMAT = 'ams-review-app-locator/2';
export const LOCATOR_ROWS_NAME = 'app-locator-rows.ndjson.gz';
export const RECORD_CACHE_CAP = 64;
// Decoded blocks of the locator's rows file kept at once. A fold reads the same block for several windows in a row before moving to the next, and a deep link reads each candidate block once, so a few are enough.
export const BLOCK_CACHE_CAP = 8;
// Rows a show-machine fold draws per window. A window costs one Range request per run of neighboring spans plus one card per row; the reader asks for each further window.
export const MACHINE_FOLD_WINDOW = 64;
// Spans in the same shard part at most this many bytes apart are fetched with one Range request, and the bytes between them are discarded. The gap is usually a human fragment or two between a window's machine rows, which costs less than another round trip; past this gap a separate request costs less than the skipped bytes.
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

// Whether bytes start with the gzip magic number. A server that declares Content-Encoding: gzip has already decoded the sidecar, so the bytes are NDJSON. A plain static file server sends the file as stored, and the app decompresses it, so an archived surface can be read without rebuild.review.serve.
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

// A slim fragment is the shard record of a unit that takes no verdict, written without `explain`, `drafts` and `highlight` (`SLIM_OMITTED_KEYS` in rebuild/review/audit.py lists them, and `check_unit` in rebuild/review/build.py checks the shape). The keys are absent, not null, which distinguishes a slim fragment from a full record with a blank field. An app-index row is not a slim fragment: it takes a verdict, and its explain material is fetched from its shard when the panel opens.
export function isSlimFragment(unit) {
  return Boolean(unit) && needsNoVerdict(unit) && !('explain' in unit) && !('drafts' in unit);
}

// Whether the explain panel can be filled from this object. An app-index row has none of `explain`, `provenance` and `drafts`, so its panel is filled from the shard record when opened. A shard record has what the build wrote, and for a slim fragment the panel says what was left out.
export function hasExplainSource(unit) {
  return Boolean(unit) && (isSlimFragment(unit) || 'explain' in unit || 'provenance' in unit || 'drafts' in unit);
}

// Whether a unit has what its sample cells draw. An app-index row does not: the card fetches `text_entities`, `highlight` and `after.cells` from the shard record. A shard record always does, including a slim fragment, which of these omits only `highlight`.
export function carriesSamples(unit) {
  return Boolean(unit) && typeof unit.text_entities === 'string';
}

// A unit id is `u-` followed by eleven base58 symbols (the alphabet without 0, O, I and l), made from the unit's content key by `unit_cache.unit_id_for`. Ids sort by string order, which is the order the build writes a class's fragments and locator rows in.
const UNIT_ID = /^u-[1-9A-HJ-NP-Za-km-z]{11}$/;
export function isUnitId(unitId) {
  return typeof unitId === 'string' && UNIT_ID.test(unitId);
}

// The locator's block table grouped by class. Each class's blocks keep file order, which is ascending by id; the folds and the deep-link search rely on that.
export function indexLocatorBlocks(blocks) {
  const byClass = new Map();
  for (const block of blocks) {
    if (!byClass.has(block.class)) byClass.set(block.class, []);
    byClass.get(block.class).push(block);
  }
  return byClass;
}

// The blocks that can hold a unit id: at most one per class, found by binary search on string-compared ids. Within a class the blocks are disjoint and ascending, but the id ranges of different classes overlap, so every class is searched. A deep link fetches only these blocks.
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

// Groups shard spans into Range requests: rows sorted by part and offset, with neighbors within `gap` bytes in the same part sharing one request. Each run gives the bytes to request and the rows to slice out of them.
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

// One row's fragment from the text a run's Range request returned. The shard is ASCII (`build._write_shard` writes it so), so a byte offset is a character offset.
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

// Byte ranges are inclusive at both ends (`bytes=A-B`), so this requests exactly byte_length bytes.
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

// The show-machine folds for the current view, each with its size and badge from the manifest. Classes are selected as in unitsForView (the class filter, else the classes in the current batch), and the batchless classes, which hold only units that take no verdict, are added to batch 0. A worklist carries its machine records itself and gets no plan.
//
// The manifest can apply only the class filter. Family, group, and config are per unit, so while any of them is set a fold's total is an upper bound, and `provisional` tells the view to say so.
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
