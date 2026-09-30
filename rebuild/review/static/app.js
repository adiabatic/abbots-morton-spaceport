import { parseHash, writeHash, shedWorklist } from './state.js';
import { actionForKey, isEditableTarget } from './keyboard.js';
import { parsePreview } from './preview.js';
import { bannerModel } from './status.js';
import {
  createStore,
  recordVerdict,
  recordVerdictWithDuplicates,
  updateNote,
  groupApprove,
  undo,
  assembleExport,
  assembleDelta,
  replayDelta,
  acknowledgeDelta,
  noteServerRecords,
  adoptServerRecord,
  DELTA_FORMAT,
  EXPORT_FORMAT,
  markExported,
  importVerdicts,
  verdictCounts,
  recentNotes,
} from './verdicts.js';
import {
  OUTBOX_KEY,
  TAB_HEARTBEAT_MS,
  readOutbox,
  writeOutbox,
  ackOutbox,
  dropOutbox,
  planOutboxReplay,
  sameRecord,
  currentEntries,
  markTabAlive,
  markTabClosed,
  liveTabs,
} from './outbox.js';
import {
  configGateChips,
  configFilterOptions,
  pinStylisticSetScope,
  explainRuns,
  renderGroupsOf,
  highlightRect,
  pairBand,
  markOffset,
  secondaryJunctionsOf,
  junctionChip,
  onlyHereJunctionSpans,
  tokenMarkRuns,
  duplicateChip,
  duplicateFillTargets,
  needsNoVerdict,
  familiesOfGroup,
  unitMatchesFilters,
  unitWorklist,
  orderWorklist,
  triageOrder,
  partitionUnits,
  humanClassCount,
  humanTotal,
  noVerdictTotal,
  NO_VERDICT_BADGE,
  formatCount,
  corpusChipLabel,
  corpusAlphabetLabel,
  corpusStampLine,
  corpusDetailRows,
  classCountsLine,
  nextUnverdictedIndex,
  stepIndex,
  availableBatches,
  classesInBatch,
  copyPreamble,
  tokenSeparators,
  searchUnits,
  duplicateGroupOfQuery,
} from './render.js';
import {
  APP_INDEX_FORMAT,
  APP_INDEX_NAME,
  BLOCK_CACHE_CAP,
  LOCATOR_FORMAT,
  LOCATOR_NAME,
  LOCATOR_ROWS_NAME,
  MACHINE_FOLD_WINDOW,
  candidateBlocks,
  carriesSamples,
  checkIndexHeader,
  coalesceSpans,
  createLineSplitter,
  createRecordCache,
  finishLines,
  hasExplainSource,
  indexLocatorBlocks,
  isSlimFragment,
  looksGzipped,
  machineFoldPlan,
  rangeHeader,
  shardPartPath,
  sliceRecordText,
  splitLines,
} from './slim.js';
import {
  SHOWN_STORAGE_KEY,
  SINGLETON_CHUNK,
  SINGLETON_DECISION,
  buildClusters,
  decisionKey,
  duplicateConflicts,
  nextQueueDecision,
  partitionClusters,
  queueCounts,
  queueResumeAction,
  queueTotals,
  readShownDecisions,
  ruledClassIds,
  singletonChunks,
  writeShownDecisions,
} from './queue.js';

const FONT_SIZE = 88;
const VERDICT_LABELS = [
  ['skip', 'Skip', 'a', 'Skip — record no verdict and advance'],
  ['reject', 'Reject', 's', 'Reject — want the old behavior back (opens a follow-up choice)'],
  ['identical', 'Identical', 'e', 'The highlighted portion looks identical'],
  ['approve', 'Approve', 'f', 'Approve — the new behavior is right'],
  ['either', 'Either', 'd', 'Fine either way (any-of check)'],
  ['neither', 'Neither', 'c', 'Neither — both behaviors look wrong; flag for follow-up'],
];
const REJECT_MENU_CHOICES = [
  { action: 'reject-no-comment', key: 's', label: 'no comment', note: null },
  { action: 'reject-old-way', key: 'a', label: 'the old way seems nicer to write out by hand', note: 'the old way seems nicer to write out by hand' },
  { action: 'reject-new-broken', key: 'f', label: 'the new way is broken', note: 'the new way is broken' },
  { action: 'reject-worse-extension', key: 'z', label: 'new way has a worse-looking extension/contraction', note: 'new way has a worse-looking extension/contraction' },
  { action: 'reject-comment', key: 'x', label: 'write a comment', note: null },
];
const NEITHER_MENU_CHOICES = [
  { action: 'neither-no-comment', key: 'c', label: 'no comment', note: null },
  { action: 'neither-ss10', key: 'd', label: 'Under ss10 these must be fully isolated; old font joins them, new font ligates them — both wrong', note: 'Under ss10 these must be fully isolated; old font joins them, new font ligates them — both wrong' },
  { action: 'neither-comment', key: 'x', label: 'write a comment', note: null },
];

const manifest = await (await fetch('manifest.json')).json();
const store = createStore();
// The only memory in the tab that grows with the queue: one app-index row per unit awaiting a verdict, holding what the queue view, search, filters and progress read. A card's sample text, pair band and settled cells, and its explain table when the panel opens, are Range-fetched from the shard record into fullRecords, which is bounded. Units that take no verdict are never loaded a class at a time. A show-machine fold reads its class's locator rows a block at a time and draws a window at a time, keeping the records of the rows on screen (foldRecords, cleared on each re-render). A worklist keeps its own machine records while it is the view (worklist.records), and a deep link keeps the one unit it revealed (transientMachineUnit). On the machine side only the locator's block table stays loaded, with one entry per block of machine units.
const humanRows = new Map();
const humanList = [];
const rowsByClass = new Map();
const duplicateIndex = new Map();
const fullRecords = createRecordCache();
const foldRecords = new Map();
const locatorBlocks = createRecordCache(BLOCK_CACHE_CAP);
let locatorReady = null;
let familyOptions = [];
let worklist = null;
const queueShown = loadQueueShown();
let lastQueueShownKey = null;
let indexReady = null;
let indexLoaded = false;
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

function loadQueueShown() {
  try {
    return readShownDecisions(localStorage.getItem(SHOWN_STORAGE_KEY), manifest.generated_at);
  } catch {
    return new Set();
  }
}

function saveQueueShown() {
  try {
    localStorage.setItem(SHOWN_STORAGE_KEY, writeShownDecisions(queueShown, manifest.generated_at));
  } catch {}
}

const MACHINE_BADGE = 'ink-identical — machine approved';
const MACHINE_TITLE =
  'Both fonts render this unit identically under every config in its set; no human input is meaningful.';
const PICTURE_BADGE = 'picture-identical — machine approved';
const PICTURE_TITLE =
  "Both fonts fill exactly the same cells of the pixel grid under every config in this unit's set — only which glyph owns which pixel differs — so no human input is meaningful.";
const JUNIOR_BADGE = 'junior-equivalent — machine approved';
const JUNIOR_TITLE =
  "Divergent only under ss10, whose ratified meaning is fully isolated letters, and the rebuild's ss10 rendering is pixel-identical to the Junior font's isolated rendering of the same string (minus Junior's one-pixel letter tracking) — so the new behavior is the spec by construction.";
const NO_VERDICT_TITLE =
  "This unit's class is adjudicated wholesale at the ledger level (hover its sidebar entry for the rationale); no unit in it ever needs an individual verdict.";
const SLIM_FRAGMENT_NOTE =
  'Machine-approved or in a no-verdict class: the build wrote no candidate table, no drafts and no pair band for this unit, since nothing here is for a reviewer to act on — the settled cells and junctions above are the whole of what it carries.';

function machineCheckOf(unit) {
  if (unit.ink_identical) return { badge: MACHINE_BADGE, title: MACHINE_TITLE };
  if (unit.picture_identical) return { badge: PICTURE_BADGE, title: PICTURE_TITLE };
  if (unit.junior_equivalent) return { badge: JUNIOR_BADGE, title: JUNIOR_TITLE };
  return null;
}

function appendConfigGate(target, unit, { detail = true } = {}) {
  for (const chip of configGateChips(unit, manifest.feature_descriptions)) {
    const badge = el('span', 'config-note');
    if (chip.feature) {
      badge.dataset.ss = chip.feature;
      badge.dataset.state = chip.state;
    }
    badge.append(el('span', 'config-note-gate', chip.text));
    if (detail && chip.detail) badge.append(el('span', 'config-note-detail', ` — ${chip.detail}`));
    badge.title = detail || !chip.detail ? unit.configs.join(', ') : `${chip.detail}\n${unit.configs.join(', ')}`;
    target.append(badge);
  }
}

let state = withDefaults(parseHash(location.hash));
let visibleUnits = [];
let machineUnits = [];
let transientMachineUnitId = null;
let transientMachineUnit = null;
let renderedKey = null;
let renderToken = 0;
const machineFoldBuilders = new Map();

const MACHINE_CHECK_BADGES = {
  ink_identical: MACHINE_BADGE,
  picture_identical: PICTURE_BADGE,
  junior_equivalent: JUNIOR_BADGE,
  no_verdict: NO_VERDICT_BADGE,
};

let searchActive = -1;
let blurTimer = null;
const SEARCH_LIMIT = 50;

function withDefaults(parsed) {
  const next = { ...parsed };
  if (next.batch === null) {
    const batches = availableBatches(manifest, next.class);
    next.batch = batches.length > 0 ? batches[0] : 0;
  }
  return next;
}

function setState(patch) {
  state = { ...state, ...shedWorklist(patch) };
  const serialized = writeHash(state);
  if (location.hash.replace(/^#/, '') !== serialized) location.hash = serialized;
  else applyHashState();
}

function setStateReplace(patch) {
  state = { ...state, ...shedWorklist(patch) };
  history.replaceState(null, '', `#${writeHash(state)}`);
  applyHashState();
}

function restream(source, first) {
  return new ReadableStream({
    start(controller) {
      if (first !== undefined) controller.enqueue(first);
    },
    async pull(controller) {
      const { value, done } = await source.read();
      if (done) controller.close();
      else controller.enqueue(value);
    },
    cancel(reason) {
      return source.cancel(reason);
    },
  });
}

async function* streamNdjson(name) {
  const response = await fetch(name);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const source = response.body.getReader();
  const { value: first } = await source.read();
  let body = restream(source, first);
  // rebuild.review.serve declares Content-Encoding: gzip, so the browser has already decoded the sidecar. A plain static file server serving an archived corpus sends the gzip bytes as stored. The magic number tells the two cases apart.
  if (looksGzipped(first) && typeof DecompressionStream === 'function') {
    body = body.pipeThrough(new DecompressionStream('gzip'));
  }
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  const splitter = createLineSplitter();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const line of splitLines(splitter, value)) if (line) yield line;
    }
    for (const line of finishLines(splitter)) if (line) yield line;
  } finally {
    reader.cancel().catch(() => {});
  }
}

function parseHeaderLine(line) {
  try {
    return JSON.parse(line);
  } catch {
    throw new Error('its first line is not JSON, so the server handed it over without decompressing it');
  }
}

// Loads the app index in one streaming pass, parsing a line at a time so the tab never holds the source text and the rows at once. The queue view, search, worklists, progress, filters and duplicate chips read only these rows.
async function loadHumanIndex() {
  const families = new Set();
  try {
    let header = null;
    for await (const line of streamNdjson(APP_INDEX_NAME)) {
      if (header === null) {
        header = parseHeaderLine(line);
        const check = checkIndexHeader(header, manifest, APP_INDEX_FORMAT);
        if (!check.ok) throw new Error(check.reason);
        continue;
      }
      const row = JSON.parse(line);
      humanRows.set(row.id, row);
      humanList.push(row);
      if (!rowsByClass.has(row.class)) rowsByClass.set(row.class, []);
      rowsByClass.get(row.class).push(row);
      if (row.duplicate_group) {
        if (!duplicateIndex.has(row.duplicate_group)) duplicateIndex.set(row.duplicate_group, []);
        duplicateIndex.get(row.duplicate_group).push(row.id);
      }
      for (const family of familiesOfGroup(row.group)) families.add(family);
    }
    // A truncated index has no lines, so the header check never runs. Failing here keeps an interrupted build from loading as an empty, fully verdicted queue.
    if (header === null) throw new Error('it carries no lines at all, so the build that wrote it did not finish');
    indexLoaded = true;
  } catch (error) {
    console.warn('review index load failed', error);
    toast(`Could not load the review index (${APP_INDEX_NAME}): ${error.message}.`);
  }
  familyOptions = [...families].sort();
  populateFilterOptions();
  updateClassCounts();
}

function unitFor(unitId) {
  return (
    humanRows.get(unitId) ??
    worklist?.records.get(unitId) ??
    foldRecords.get(unitId) ??
    (transientMachineUnit?.id === unitId ? transientMachineUnit : null) ??
    fullRecords.get(unitId) ??
    null
  );
}

// Loads the locator's block table once, on first use: which gzip member of the rows file holds which class's rows and which ids. A fold reads its class's blocks in order, a deep link binary-searches every class's blocks for the one that can hold its id, and neither reads rows outside the blocks it fetches.
function loadLocator() {
  locatorReady ??= (async () => {
    const blocks = [];
    let header = null;
    for await (const line of streamNdjson(LOCATOR_NAME)) {
      if (header === null) {
        header = parseHeaderLine(line);
        const check = checkIndexHeader(header, manifest, LOCATOR_FORMAT);
        if (!check.ok) throw new Error(check.reason);
        continue;
      }
      blocks.push(JSON.parse(line));
    }
    if (header === null) throw new Error('it carries no lines at all, so the build that wrote it did not finish');
    return indexLocatorBlocks(blocks);
  })().catch((error) => {
    console.warn('machine locator load failed', error);
    toast(`Could not load the machine locator (${LOCATOR_NAME}): ${error.message}.`);
    locatorReady = null;
    return new Map();
  });
  return locatorReady;
}

// A Range response's body as text. The rows file is served identity-encoded, because Chrome rejects a partial response that declares a content encoding, so a block arrives as its gzip member's bytes and is decompressed here. The magic-number check covers a server that decoded it anyway.
async function readMaybeGzipped(response) {
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (!looksGzipped(bytes) || typeof DecompressionStream !== 'function') return new TextDecoder().decode(bytes);
  return new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
}

// One block of locator rows, fetched by the span the table gives and cached so that a fold's next window or a later deep link reads it without another request.
async function fetchLocatorBlock(block) {
  const key = `${block.class}\u0000${block.byte_start}`;
  const cached = locatorBlocks.get(key);
  if (cached) return cached;
  let text = null;
  try {
    const response = await fetch(LOCATOR_ROWS_NAME, { headers: { Range: rangeHeader(block) } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    if (response.status !== 206 && Number(response.headers.get('Content-Length')) > block.byte_length) {
      throw new Error('the server ignored the byte range');
    }
    text = await readMaybeGzipped(response);
  } catch (error) {
    console.warn('locator block fetch failed', error);
    toast(`Could not read the ${block.class} locator rows at ${block.byte_start}: ${error.message}`);
    return null;
  }
  const rows = [];
  try {
    for (const line of text.split('\n')) if (line) rows.push(JSON.parse(line));
  } catch {
    rows.length = 0;
  }
  // The table and the rows file are written together, so a block that does not start where the table says belongs to a different build.
  if (rows.length !== block.units || rows[0]?.id !== block.first) {
    toast(`The ${block.class} locator rows are not where this page was told they would be — the corpus was rebuilt; reload.`);
    return null;
  }
  locatorBlocks.set(key, rows);
  return rows;
}

// Looks up the locator rows of units that take no verdict, for a deep link or a worklist. Each id has at most one candidate block per class; each block is fetched once, and only the requested rows are kept.
async function resolveMachineIds(unitIds) {
  const wanted = new Set(unitIds);
  const found = new Map();
  if (wanted.size === 0) return found;
  const byClass = await loadLocator();
  const byBlock = new Map();
  for (const unitId of wanted) {
    for (const block of candidateBlocks(byClass, unitId)) {
      const key = `${block.class}\u0000${block.byte_start}`;
      if (!byBlock.has(key)) byBlock.set(key, { block, ids: new Set() });
      byBlock.get(key).ids.add(unitId);
    }
  }
  await Promise.all(
    [...byBlock.values()].map(async ({ block, ids }) => {
      const rows = await fetchLocatorBlock(block);
      if (!rows) return;
      for (const row of rows) if (ids.has(row.id)) found.set(row.id, row);
    }),
  );
  return found;
}

// Fetches the shard records for a set of addresses with as few Range requests as the spans allow: neighbors in a part share one request, and each record is sliced from the response at its own span. Every record read goes into fullRecords, so a card's fetch also serves its explain panel.
async function fetchRecordsBySpans(rows) {
  const found = new Map();
  const wanted = [];
  for (const row of rows) {
    const cached = fullRecords.get(row.id);
    if (cached) found.set(row.id, cached);
    else wanted.push(row);
  }
  await Promise.all(
    coalesceSpans(wanted).map(async (run) => {
      const path = shardPartPath(manifest, run);
      if (!path) return;
      let text = null;
      try {
        const response = await fetch(path, { headers: { Range: rangeHeader(run) } });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        if (response.status !== 206 && Number(response.headers.get('Content-Length')) > run.byte_length) {
          throw new Error('the server ignored the byte range');
        }
        text = await response.text();
      } catch (error) {
        console.warn('record fetch failed', error);
        toast(`Could not read ${run.rows[0].id} out of ${path}: ${error.message}`);
        return;
      }
      for (const row of run.rows) {
        let record = null;
        try {
          record = JSON.parse(sliceRecordText(text, run, row));
        } catch {
          record = null;
        }
        // A rebuild rewrites the shards, so a stale span can land on a neighboring record instead of failing. Comparing the id detects that.
        if (!record || record.id !== row.id) {
          toast(`${row.id} is not where this page was told it would be — the corpus was rebuilt; reload.`);
          continue;
        }
        fullRecords.set(record.id, record);
        found.set(record.id, record);
      }
    }),
  );
  return found;
}

async function fetchFullRecord(locator) {
  return (await fetchRecordsBySpans([locator])).get(locator.id) ?? null;
}

async function resolveWorklist(key, records) {
  const ids = [];
  const seen = new Set();
  for (const id of unitWorklist(key)) {
    if (seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  const missing = [];
  for (const id of ids) {
    if (humanRows.has(id)) continue;
    const known = unitFor(id);
    if (known) records.set(id, known);
    else missing.push(id);
  }
  if (missing.length > 0) {
    const located = await resolveMachineIds(missing);
    const fetched = await Promise.all([...located.values()].map((row) => fetchFullRecord(row)));
    for (const record of fetched) if (record) records.set(record.id, record);
  }
  const units = [];
  for (const id of ids) {
    const unit = humanRows.get(id) ?? records.get(id) ?? null;
    if (unit) units.push(unit);
  }
  return units;
}

// A worklist is resolved once and kept while it is the view. Resolving a machine id costs a locator block per candidate class and a shard fetch per unit, and applyHashState runs on every cursor move and verdict, so resolving per call would put those requests behind each of them. Keeping the records also means a worklist longer than the fullRecords cap does not evict its own earlier units, which the app could then neither move the cursor to nor copy.
function worklistFor(key) {
  if (!worklist || worklist.key !== key) {
    const records = new Map();
    const slot = { key, records, promise: null };
    slot.promise = resolveWorklist(key, records);
    worklist = slot;
  }
  return worklist.promise;
}

async function unitsForView(batch, classFilter) {
  await indexReady;
  if (state.units) return orderWorklist(await worklistFor(state.units), state.order);
  worklist = null;
  // The batch's rows in triage order (each row carries its `order`), so the group folds and the default cursor follow the queue, not the shards' id order. Selecting classes first keeps a walk over the batches from scanning every class's rows.
  const units = [];
  for (const cls of manifest.classes) {
    if (classFilter) {
      if (cls.id !== classFilter) continue;
    } else if (!cls.batches.includes(batch)) continue;
    for (const row of rowsByClass.get(cls.id) ?? []) if (row.batch === batch) units.push(row);
  }
  units.sort((a, b) => triageOrder(a) - triageOrder(b));
  return units;
}

async function findUnitAnywhere(unitId) {
  const known = unitFor(unitId);
  if (known) return known;
  const located = await resolveMachineIds([unitId]);
  const row = located.get(unitId);
  if (!row) return null;
  return fetchFullRecord(row);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// One side's sample cell. An app-index row has no text yet, so the cell starts empty and hydrateSamples rebuilds it from the record; the cell stores its side and feature settings for that.
function buildSample(unit, side, featureSettings) {
  const cell = el('div', `qs ${side}`);
  cell.style.fontFeatureSettings = featureSettings;
  cell.dataset.side = side;
  cell.dataset.features = featureSettings;
  const run = el('span', 'run');
  if (carriesSamples(unit)) run.innerHTML = unit.text_entities;
  cell.append(run);
  const upem = manifest.fonts[side].upem;
  const rect = pairBand(unit, side, FONT_SIZE, upem);
  if (rect) {
    const band = el('span', 'pair-band');
    band.style.left = `${rect.left}px`;
    band.style.width = `${rect.width}px`;
    cell.append(band);
  }
  for (const junction of secondaryJunctionsOf(unit)) {
    if (!junction[side]) continue;
    const rect = highlightRect(junction[side], FONT_SIZE, upem);
    const band = el('span', 'secondary-band');
    band.style.left = `${rect.left}px`;
    band.style.width = `${rect.width}px`;
    const chip = junctionChip(junction);
    const node = el(chip.primaryUnit ? 'a' : 'span', 'junction-chip', chip.label);
    if (chip.primaryUnit) {
      node.href = `#unit=${chip.primaryUnit}`;
      node.dataset.primaryUnit = chip.primaryUnit;
    }
    node.title = chip.title;
    node.tabIndex = -1;
    band.append(node);
    cell.append(band);
  }
  for (const mark of unit.boundary_marks ?? []) {
    const tick = el('span', 'boundary-mark');
    tick.style.left = `${markOffset(mark.x, FONT_SIZE, upem)}px`;
    tick.title = mark.kind;
    cell.append(tick);
  }
  return cell;
}

const JUNCTION_MARK_TITLE =
  'This dashed underline is the secondary divergent junction judged only in this unit — the text-line twin of the sample band’s “only here” chip.';

function appendMarkedTokens(node, tokens, separators, unit) {
  for (const run of tokenMarkRuns(tokens, separators, unit.pair_codepoints, onlyHereJunctionSpans(unit))) {
    if (!run.pair && !run.junction) {
      node.append(document.createTextNode(run.text));
      continue;
    }
    let mark = null;
    if (run.junction) {
      mark = el('span', 'junction-mark', run.text);
      mark.title = JUNCTION_MARK_TITLE;
    }
    if (run.pair) {
      const pair = el('span', 'pair-mark');
      if (mark) pair.append(mark);
      else pair.textContent = run.text;
      mark = pair;
    }
    node.append(mark);
  }
}

function buildNotationLine(unit) {
  const line = el('div', 'notation');
  const tokens = unit.notation_tokens;
  if (!Array.isArray(tokens) || tokens.length === 0 || !unit.pair_codepoints) {
    line.textContent = unit.notation;
    return line;
  }
  appendMarkedTokens(line, tokens, tokenSeparators(tokens), unit);
  return line;
}

function buildCodepointsCode(unit) {
  const code = el('code');
  if (typeof unit.codepoints !== 'string' || !unit.pair_codepoints) {
    code.textContent = unit.codepoints ?? '';
    return code;
  }
  const tokens = unit.codepoints.split(':');
  const separators = tokens.map((_token, index) => (index === 0 ? '' : ':'));
  appendMarkedTokens(code, tokens, separators, unit);
  return code;
}

// A card built from an app-index row draws its label at once and its samples when the record arrives, from one Range request against the class shard (the same one the explain panel uses). The marked runs on the text lines are rebuilt from the record, because the junction underline reads the settled cells.
async function hydrateSamples(container, unit) {
  const record = await fetchFullRecord(unit);
  if (!record || !container.isConnected) return;
  const notation = container.querySelector(':scope > .label > .notation');
  if (notation) notation.replaceWith(buildNotationLine(record));
  const code = container.querySelector(':scope > .label > .codepoints > code');
  if (code) code.replaceWith(buildCodepointsCode(record));
  for (const cell of container.querySelectorAll('.qs[data-side]')) {
    cell.replaceWith(buildSample(record, cell.dataset.side, cell.dataset.features));
  }
}

function buildRow(unit) {
  const exempt = needsNoVerdict(unit);
  const check = machineCheckOf(unit);
  const exemptTitle = check?.title ?? NO_VERDICT_TITLE;
  const row = el('article', exempt ? 'row machine' : 'row');
  row.id = `unit-${unit.id}`;
  row.dataset.unit = unit.id;
  row.dataset.group = unit.group;

  const label = el('div', 'label');
  label.append(buildNotationLine(unit));

  const codepoints = el('div', 'codepoints');
  const code = buildCodepointsCode(unit);
  const copy = el('button', 'copy-unit', 'Copy');
  copy.type = 'button';
  copy.title = 'Copy a prompt preamble for this unit';
  codepoints.append(code, copy);
  label.append(codepoints);

  const meta = el('div', 'meta-chips');
  meta.append(el('span', 'unit-id', unit.id));
  if (unit.exemplar) meta.append(el('span', 'exemplar', 'exemplar'));
  if (check) {
    const badge = el('span', 'machine-badge', check.badge);
    badge.title = check.title;
    meta.append(badge);
  } else if (unit.no_verdict) {
    const badge = el('span', 'machine-badge', NO_VERDICT_BADGE);
    badge.title = NO_VERDICT_TITLE;
    meta.append(badge);
  }
  appendConfigGate(meta, unit);
  if (unit.config_class_note) {
    const badge = el('span', 'config-class-note', unit.config_class_note);
    meta.append(badge);
  }
  const duplicate = duplicateChip(unit, duplicateIndex.get(unit.duplicate_group) ?? []);
  if (duplicate) {
    const chip = el('a', 'duplicate-chip', duplicate.label);
    chip.href = duplicate.href;
    chip.title = duplicate.title;
    chip.dataset.duplicateGroup = unit.duplicate_group;
    meta.append(chip);
  }
  label.append(meta);

  const buttons = el('div', 'verdict-buttons');
  for (const [verdict, text, key, title] of VERDICT_LABELS) {
    const button = el('button', 'verdict-btn');
    button.type = 'button';
    button.dataset.verdict = verdict;
    button.title = exempt ? exemptTitle : title;
    button.disabled = exempt;
    if (verdict === 'reject' || verdict === 'neither') button.setAttribute('aria-haspopup', 'menu');
    button.setAttribute('aria-pressed', 'false');
    button.append(document.createTextNode(`${text} `));
    const kbd = el('kbd', null, key);
    button.append(kbd);
    buttons.append(button);
  }
  if (!exempt) {
    const clear = el('button', 'clear-verdict');
    clear.type = 'button';
    clear.title = "Clear this unit's verdict (Backspace or Delete; pressing its active verdict key again also clears)";
    clear.tabIndex = -1;
    clear.append(document.createTextNode('Clear '));
    clear.append(el('kbd', null, '⌫'));
    buttons.append(clear);
    const repeat = el('button', 'repeat-verdict');
    repeat.type = 'button';
    repeat.title = 'Repeat the previous verdict and its note on this unit';
    repeat.tabIndex = -1;
    repeat.append(document.createTextNode('Repeat '));
    repeat.append(el('kbd', null, 'r'));
    buttons.append(repeat);
  }
  label.append(buttons);

  const note = el('input', 'note');
  note.type = 'text';
  note.placeholder = 'note (n)';
  note.disabled = exempt;
  note.setAttribute('aria-label', `Note for ${unit.id}`);

  const groups = renderGroupsOf(unit);
  row.append(label, buildSample(unit, 'before', groups[0].featureSettings), buildSample(unit, 'after', groups[0].featureSettings));
  for (const group of groups) {
    if (group.primary) continue;
    const extra = el('div', 'render-group');
    extra.append(el('div', 'render-group-label', `also under ${group.label}`));
    extra.append(buildSample(unit, 'before', group.featureSettings), buildSample(unit, 'after', group.featureSettings));
    row.append(extra);
  }
  row.append(note);

  const summary = el('div', 'summary');
  summary.append(el('p', 'summary-text', unit.summary ?? ''));
  const why = el('button', 'explain-toggle');
  why.type = 'button';
  why.title = 'Open the full explain panel for this unit';
  why.setAttribute('aria-expanded', 'false');
  why.append(document.createTextNode('Why? '));
  why.append(el('kbd', null, 'x'));
  summary.append(why);
  row.append(summary);

  row.append(buildExplainPanel(unit));

  syncRowVerdict(unit.id, row);
  if (!carriesSamples(unit)) hydrateSamples(row, unit);
  return row;
}

function buildExplainPanel(unit) {
  const panel = el('div', 'explain-panel');
  panel.hidden = true;
  panel.append(
    el(
      'p',
      'explain-intro',
      'This panel shows the full candidate table the settlement function considered at each divergent position, ' +
        'with each elimination attributed to the YAML record that caused it. "->" marks the winning candidate; ' +
        '"decided by" names the stage that separated it from the runner-up.',
    ),
  );
  if (hasExplainSource(unit)) fillExplainPanel(panel, unit);
  return panel;
}

function fillExplainPanel(panel, unit) {
  panel.dataset.filled = '1';
  for (const pending of panel.querySelectorAll('.explain-pending')) pending.remove();
  if (isSlimFragment(unit)) panel.append(el('p', 'explain-intro', SLIM_FRAGMENT_NOTE));
  if (unit.explain) {
    panel.append(el('h4', null, 'Explain'));
    const dump = el('pre');
    for (const run of explainRuns(unit.explain)) {
      if (run.set === null) {
        dump.append(document.createTextNode(run.text));
        continue;
      }
      const mark = el('span', 'explain-ss', run.text);
      mark.dataset.ss = run.set;
      const detail = manifest.feature_descriptions?.[run.set];
      if (detail) mark.title = `${run.set} — ${detail}`;
      dump.append(mark);
    }
    panel.append(dump);
  }
  if ((unit.provenance ?? []).length > 0) {
    panel.append(el('h4', null, 'Provenance'));
    const list = el('ul');
    for (const entry of unit.provenance) {
      const item = el('li');
      item.append(el('code', null, entry));
      list.append(item);
    }
    panel.append(list);
  }
  if (unit.drafts) {
    panel.append(el('h4', null, 'Drafts'));
    const list = el('ul');
    if (unit.drafts.pin) {
      const item = el('li', null, 'pin: ');
      item.append(el('code', null, unit.drafts.pin.expect));
      const scope = pinStylisticSetScope(unit.drafts.pin.stylistic_set, manifest.feature_descriptions);
      if (scope) {
        const marker = el('span', 'pin-scope', ` scoped to ${scope.label}, as `);
        marker.append(el('code', null, scope.attribute));
        marker.title = scope.title;
        item.append(marker);
      }
      const status = unit.drafts.pin.duplicate_of
        ? ` (duplicate of ${unit.drafts.pin.duplicate_of})`
        : ` (${unit.drafts.pin.attribute}, syntax ${unit.drafts.pin.syntax}, semantics ${unit.drafts.pin.semantics_after_font})`;
      item.append(document.createTextNode(status));
      list.append(item);
    }
    if (unit.drafts.policy) {
      const item = el('li', null, `policy: ${unit.drafts.policy.file} ${unit.drafts.policy.keypath} `);
      item.append(el('code', null, unit.drafts.policy.suggested_record));
      list.append(item);
    }
    if (unit.drafts.any_of) {
      const item = el('li', null, 'any-of: ');
      let first = true;
      for (const candidate of unit.drafts.any_of.candidates) {
        if (!first) item.append(document.createTextNode(' / '));
        item.append(el('code', null, candidate));
        first = false;
      }
      list.append(item);
    }
    panel.append(list);
  }
}

function buildQueueContext(units) {
  const strip = el('aside', 'queue-context');
  const back = el('a', 'open-app', 'Queue ↩');
  back.href = '#view=queue';
  back.title = 'Back to the full review queue; finishing this worklist advances to the next decision by itself.';
  const clusterIds = new Set();
  for (const unit of units) if (typeof unit.cluster === 'string') clusterIds.add(unit.cluster);
  if (clusterIds.size === 1) {
    const clusterId = [...clusterIds][0];
    const cluster = buildClusters(humanList, (id) => store.records.get(id)).find((entry) => entry.id === clusterId);
    if (cluster) {
      const line = el('p', 'queue-context-line');
      line.append(el('strong', null, 'Queue decision'));
      line.append(
        document.createTextNode(
          ` — one verdict per duplicate group covers all ${formatCount(cluster.size)} lookalike units of `,
        ),
      );
      line.append(el('span', 'chip', cluster.class));
      line.append(document.createTextNode(' '));
      line.append(el('span', 'configs', cluster.id));
      line.append(document.createTextNode(' '));
      line.append(back);
      strip.append(line);
      strip.append(buildEvidenceLine(cluster.evidence));
      return strip;
    }
  }
  const line = el('p', 'queue-context-line');
  const singles = clusterIds.size > 1;
  line.append(el('strong', null, singles ? 'Queue singletons' : 'Queue worklist'));
  line.append(
    document.createTextNode(` — ${units.length}${singles ? ' one-off' : ''} unit${units.length === 1 ? '' : 's'} stacked. `),
  );
  line.append(back);
  strip.append(line);
  return strip;
}

function renderBatch(units, machine, plan) {
  closeRejectMenu();
  closeNeitherMenu();
  const container = document.getElementById('batch');
  container.textContent = '';
  machineFoldBuilders.clear();
  foldRecords.clear();
  if (units.length === 0 && machine.length === 0 && plan.length === 0) {
    container.append(el('p', 'empty', 'No units match the current batch and filters.'));
    return;
  }
  // A provisional plan's fold totals are upper bounds, so the folds may hold no unit that matches the filter. Say that no unit awaiting a verdict matches, so a filter that matched nothing does not look like a full view.
  if (units.length === 0 && machine.length === 0 && plan.some((fold) => fold.provisional)) {
    container.append(el('p', 'empty', 'No units awaiting a verdict match the current batch and filters.'));
  }
  if (state.units && state.queue) container.append(buildQueueContext(units));
  let currentGroup = null;
  let groupNode = null;
  for (const unit of units) {
    if (unit.group !== currentGroup) {
      currentGroup = unit.group;
      groupNode = el('details', 'group');
      groupNode.open = true;
      groupNode.dataset.group = unit.group;
      const summary = el('summary');
      summary.append(el('span', 'group-name', unit.group));
      summary.append(el('span', 'group-counts'));
      const approveAll = el('button', 'group-approve');
      approveAll.type = 'button';
      approveAll.append(document.createTextNode('Approve rest '));
      approveAll.append(el('kbd', null, 'g'));
      summary.append(approveAll);
      groupNode.append(summary);
      container.append(groupNode);
    }
    groupNode.append(buildRow(unit));
  }
  renderMachineSection(container, machine, plan);
  updateGroupCounts();
}

function renderMachineSection(container, machine, plan) {
  const planned = new Set(plan.map((fold) => fold.classId));
  const loose = new Map();
  for (const unit of machine) {
    if (planned.has(unit.class)) continue;
    if (!loose.has(unit.class)) loose.set(unit.class, []);
    loose.get(unit.class).push(unit);
  }
  if (plan.length === 0 && loose.size === 0) return;
  let total = 0;
  for (const fold of plan) total += fold.total;
  for (const classUnits of loose.values()) total += classUnits.length;
  const provisional = plan.some((fold) => fold.provisional);
  const heading =
    state.machine === '1'
      ? provisional
        ? `No verdict needed in this view: up to ${total} units (machine-approved or in a no-verdict class) — open a fold for the count under this filter`
        : `No verdict needed in this view: ${total} units (machine-approved or in a no-verdict class)`
      : state.units
        ? `No verdict needed in your worklist: ${machine.length} unit${machine.length === 1 ? '' : 's'} shown below`
        : 'This deep-linked unit needs no verdict — it stays out of your queue and disappears when you move on.';
  container.append(el('h2', 'machine-heading', heading));
  // The filters an opened fold applies, captured at render time. The status filter is dropped, as partitionUnits drops it for units that take no verdict.
  const foldFilters = { ...state, status: null };
  for (const fold of plan) {
    const pinned = machine.filter((unit) => unit.class === fold.classId);
    container.append(
      buildMachineFold(fold.classId, fold.total, MACHINE_CHECK_BADGES[fold.check], null, pinned, foldFilters, {
        provisional: fold.provisional,
      }),
    );
  }
  for (const [classId, classUnits] of loose) {
    const badge = classUnits.every((unit) => unit.ink_identical)
      ? MACHINE_BADGE
      : classUnits.every((unit) => unit.ink_identical || unit.picture_identical)
        ? PICTURE_BADGE
        : classUnits.every((unit) => unit.ink_identical || unit.picture_identical || unit.junior_equivalent)
          ? JUNIOR_BADGE
          : NO_VERDICT_BADGE;
    container.append(buildMachineFold(classId, classUnits.length, badge, classUnits, [], foldFilters));
  }
}

function buildMachineFold(classId, total, badge, records, pinned, foldFilters, { provisional = false } = {}) {
  const fold = el('details', 'group machine-group');
  fold.dataset.machineClass = classId;
  const summary = el('summary');
  summary.append(el('span', 'group-name', classId));
  const counts = el('span', 'group-counts', provisional ? `up to ${total} units — ${badge}` : `${total} units — ${badge}`);
  summary.append(counts);
  fold.append(summary);
  const rendered = new Set();
  const render = (units) => {
    for (const unit of units) {
      if (rendered.has(unit.id)) continue;
      rendered.add(unit.id);
      foldRecords.set(unit.id, unit);
      fold.append(buildRow(unit));
    }
  };
  if (records !== null) {
    let building = null;
    const build = () => {
      if (!building) {
        render(records);
        if (records.length !== total) setText(counts, `${records.length} of ${total} units — ${badge}`);
        building = Promise.resolve();
      }
      return building;
    };
    machineFoldBuilders.set(fold, build);
    fold.addEventListener('toggle', () => {
      if (fold.open) build();
    });
    if (state.units) {
      fold.open = true;
      build();
    }
    return fold;
  }
  // Rows come from the locator a block at a time and records from the shard a window at a time, so opening a fold costs one window whatever the class size, and the reader requests each further window. Filters apply per record, so under a filter a window shows only the matching rows it read, and the count line says how many rows have been read.
  const pinnedById = new Map(pinned.map((unit) => [unit.id, unit]));
  const more = el('button', 'fold-more');
  more.type = 'button';
  let blocks = null;
  let classRows = 0;
  let cursor = { block: 0, row: 0 };
  let read = 0;
  let shown = 0;
  let loading = null;
  // The count line covers the windows only. A pinned row is the deep-linked unit, drawn before the windows whether or not its window has been read, and counted as shown once its window has been read and only if it matches the filters.
  const describe = () => {
    const unread = classRows - read;
    if (unread <= 0) {
      setText(counts, provisional || shown !== total ? `${shown} of ${total} units — ${badge}` : `${total} units — ${badge}`);
      more.remove();
      return;
    }
    setText(counts, `${shown} shown of the first ${read} of ${total} units — ${badge}`);
    setText(more, `Show ${Math.min(MACHINE_FOLD_WINDOW, unread)} more (${formatCount(unread)} not yet read)`);
    more.disabled = false;
    fold.append(more);
  };
  const nextWindow = async () => {
    if (blocks === null) {
      blocks = (await loadLocator()).get(classId) ?? [];
      for (const block of blocks) classRows += block.units;
    }
    const rows = [];
    while (rows.length < MACHINE_FOLD_WINDOW && cursor.block < blocks.length) {
      const block = await fetchLocatorBlock(blocks[cursor.block]);
      if (!block) {
        classRows = read;
        break;
      }
      const take = Math.min(MACHINE_FOLD_WINDOW - rows.length, block.length - cursor.row);
      for (const row of block.slice(cursor.row, cursor.row + take)) rows.push(row);
      cursor = cursor.row + take >= block.length ? { block: cursor.block + 1, row: 0 } : { block: cursor.block, row: cursor.row + take };
    }
    const fetched = await fetchRecordsBySpans(rows.filter((row) => !pinnedById.has(row.id)));
    const units = [];
    for (const row of rows) {
      const unit = pinnedById.get(row.id) ?? fetched.get(row.id);
      if (unit && unitMatchesFilters(unit, foldFilters, undefined)) units.push(unit);
    }
    read += rows.length;
    shown += units.length;
    render(units);
    describe();
  };
  const advance = () => {
    if (loading) return loading;
    more.disabled = true;
    loading = nextWindow().finally(() => {
      loading = null;
    });
    return loading;
  };
  let building = null;
  const build = () => {
    if (!building) {
      render(pinned);
      building = advance();
    }
    return building;
  };
  machineFoldBuilders.set(fold, build);
  fold.addEventListener('toggle', () => {
    if (fold.open) build();
  });
  more.addEventListener('click', (event) => {
    event.preventDefault();
    advance();
  });
  return fold;
}

async function revealMachineUnit(unitId) {
  const unit = unitFor(unitId);
  if (!unit || !needsNoVerdict(unit)) return false;
  const fold = document.querySelector(`details.machine-group[data-machine-class="${unit.class}"]`);
  if (!fold) return false;
  const build = machineFoldBuilders.get(fold);
  if (build) await build();
  fold.open = true;
  const row = rowFor(unitId);
  if (!row) return false;
  for (const cursor of document.querySelectorAll('.row.cursor')) cursor.classList.remove('cursor');
  row.classList.add('cursor');
  row.scrollIntoView({ block: 'start', behavior: reducedMotion.matches ? 'auto' : 'smooth' });
  return true;
}

function rowFor(unitId) {
  return document.getElementById(`unit-${unitId}`);
}

function syncRowVerdict(unitId, row = rowFor(unitId)) {
  if (!row) return;
  const record = store.records.get(unitId);
  if (record) row.dataset.verdict = record.verdict;
  else delete row.dataset.verdict;
  const clear = row.querySelector('.clear-verdict');
  if (clear) clear.disabled = !record;

  for (const button of row.querySelectorAll('.verdict-btn')) {
    button.setAttribute('aria-pressed', String(Boolean(record) && record.verdict === button.dataset.verdict));
  }
  const note = row.querySelector('.note');
  if (record && record.note && note.value !== record.note && document.activeElement !== note) note.value = record.note;
}

function cursorUnitId() {
  if (state.unit) {
    if (visibleUnits.some((unit) => unit.id === state.unit)) return state.unit;
    if (document.querySelector(`#batch .row:not(.machine)[data-unit="${state.unit}"]`)) return state.unit;
    // A unit that takes no verdict can be the URL cursor for a deep link but never the verdict cursor: keys and auto-advance act only on human units.
    if (machineUnits.some((unit) => unit.id === state.unit)) return null;
  }
  return visibleUnits.length > 0 ? visibleUnits[0].id : null;
}

async function ensureCursor() {
  const inView = (unitId) =>
    visibleUnits.some((unit) => unit.id === unitId) ||
    machineUnits.some((unit) => unit.id === unitId) ||
    Boolean(document.querySelector(`#batch .row[data-unit="${unitId}"]`));
  if (state.unit && !inView(state.unit)) {
    const unit = await findUnitAnywhere(state.unit);
    if (unit && needsNoVerdict(unit)) {
      // A deep link to a unit that takes no verdict shows just that unit; the show-machine toggle stays off, and navigating away hides it. The record is kept here because the cards' own fetches would evict it from fullRecords.
      transientMachineUnitId = unit.id;
      transientMachineUnit = unit;
      setStateReplace({});
      return false;
    }
    if (unit && unit.batch !== state.batch && unitMatchesFilters(unit, state, store.records.get(unit.id))) {
      setStateReplace({ batch: unit.batch, class: state.class && unit.class !== state.class ? null : state.class });
      return false;
    }
    setStateReplace({ unit: visibleUnits.length > 0 ? visibleUnits[0].id : null });
    return false;
  }
  if (!state.unit && visibleUnits.length > 0) {
    setStateReplace({ unit: visibleUnits[0].id });
    return false;
  }
  return true;
}

function updateCursorDom(scroll = true) {
  for (const row of document.querySelectorAll('.row.cursor')) row.classList.remove('cursor');
  const unitId = cursorUnitId();
  if (!unitId) return;
  const row = rowFor(unitId);
  if (!row) return;
  row.classList.add('cursor');
  const fold = row.closest('details.group');
  if (fold && !fold.open) fold.open = true;
  if (scroll) row.scrollIntoView({ block: 'start', behavior: reducedMotion.matches ? 'auto' : 'smooth' });
}

// Writes only when the text differs. Most calls pass unchanged counts, and assigning textContent, even the same text, replaces the node's children, which dirties layout.
function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

function updateGroupCounts() {
  for (const fold of document.querySelectorAll('details.group:not(.machine-group)')) {
    const rows = fold.querySelectorAll('.row');
    let verdicted = 0;
    for (const row of rows) if (row.dataset.verdict) verdicted += 1;
    setText(fold.querySelector('.group-counts'), `${verdicted}/${rows.length} verdicted`);
    const approve = fold.querySelector('.group-approve');
    const done = verdicted === rows.length;
    if (approve.hidden !== done) approve.hidden = done;
  }
}

function updateUnexportedNudge() {
  const nudge = document.getElementById('unexported-nudge');
  nudge.hidden = store.unexported.size === 0;
  setText(nudge, `${store.unexported.size} unexported${autosaveHealthy() ? ' (autosaved)' : ''}`);
}

// One pass over the queue for both the sidebar tallies and the selected-class line. A pass per class button would make every store mutation cost the number of classes times the queue size.
function verdictedByClass() {
  const counts = new Map();
  for (const row of humanList) {
    if (store.records.has(row.id)) counts.set(row.class, (counts.get(row.class) ?? 0) + 1);
  }
  return counts;
}

function updateProgress() {
  const counts = verdictCounts(store);
  setText(
    document.getElementById('overall-progress'),
    `Overall: ${formatCount(store.records.size)}/${formatCount(humanTotal(manifest))} ` +
      `(→${formatCount(counts.skip)} ✗${formatCount(counts.reject)} ≡${formatCount(counts.identical)} ` +
      `≈${formatCount(counts.either)} ∅${formatCount(counts.neither)} ✓${formatCount(counts.approve)})`,
  );
  const byClass = verdictedByClass();
  updateClassProgress(byClass);
  if (state.view === 'queue') {
    // In the queue view renderQueue writes the batch-progress line, so a store mutation (undo, import, a sync from another session) only re-derives the queue.
    scheduleQueueRefresh();
  } else {
    let batchVerdicted = 0;
    for (const unit of visibleUnits) if (store.records.has(unit.id)) batchVerdicted += 1;
    let line;
    if (state.units && state.queue) {
      const queue = queueCounts(humanList, (id) => store.records.get(id), ruledClassIds(manifest.classes));
      line =
        `Queue decision: ${batchVerdicted}/${visibleUnits.length} · ` +
        `queue: ${formatCount(queue.blankUnits)} blank in ${formatCount(queue.clusters)} clusters`;
    } else if (state.units) {
      line = `Worklist: ${batchVerdicted}/${visibleUnits.length}`;
    } else {
      line = `Batch ${state.batch}: ${batchVerdicted}/${visibleUnits.length}`;
    }
    setText(document.getElementById('batch-progress'), line);
  }
  updateUnexportedNudge();
  renderOutboxStatus();
  updateGroupCounts();
  updateClassCounts(byClass);
}

function updateClassProgress(byClass = verdictedByClass()) {
  const line = document.getElementById('class-progress');
  const cls = state.class ? manifest.classes.find((entry) => entry.id === state.class) : null;
  const human = cls ? humanClassCount(cls) : 0;
  if (!cls || human === 0 || !indexLoaded) {
    line.hidden = true;
    return;
  }
  setText(line, `Class ${cls.id}: ${formatCount(byClass.get(cls.id) ?? 0)}/${formatCount(human)}`);
  line.title = cls.why ?? '';
  line.hidden = false;
}

function updateTitle() {
  if (state.view === 'queue') {
    document.title = 'Review queue — AMS review';
    return;
  }
  const unitId = cursorUnitId();
  if (state.units) {
    document.title = `${unitId ?? '—'} · ${state.queue ? 'queue' : 'worklist'} — AMS review`;
    return;
  }
  document.title = `${unitId ?? '—'} · batch ${state.batch} — AMS review`;
}

function renderSidebar() {
  const list = document.getElementById('class-list');
  list.textContent = '';
  for (const cls of manifest.classes) {
    const item = el('li');
    const button = el('button', 'class-button');
    button.type = 'button';
    button.dataset.class = cls.id;
    button.title = cls.why ?? '';
    button.setAttribute('aria-pressed', String(state.class === cls.id));
    const idLine = el('span', 'class-id', cls.id);
    const status = el('span', 'class-status', cls.status ?? 'diff');
    status.dataset.status = cls.status ?? '';
    const counts = el('span', 'class-counts');
    counts.dataset.units = String(cls.unit_count);
    button.append(idLine, status, counts);
    item.append(button);
    list.append(item);
  }
  updateClassCounts();
}

function updateClassCounts(byClass = verdictedByClass()) {
  for (const button of document.querySelectorAll('.class-button')) {
    const cls = manifest.classes.find((entry) => entry.id === button.dataset.class);
    const verdicted = cls.no_verdict || !indexLoaded ? null : (byClass.get(cls.id) ?? 0);
    setText(button.querySelector('.class-counts'), classCountsLine(cls, verdicted));
    button.setAttribute('aria-pressed', String(state.class === cls.id));
  }
}

function updateSidebarHighlights() {
  const cursorId = cursorUnitId() ?? state.unit;
  const currentClass = cursorId ? (unitFor(cursorId)?.class ?? null) : null;
  let batchClasses;
  if (state.units) {
    batchClasses = new Set();
    for (const unit of visibleUnits) batchClasses.add(unit.class);
    for (const unit of machineUnits) batchClasses.add(unit.class);
  } else {
    batchClasses = classesInBatch(manifest, state.batch, state.machine === '1');
  }
  for (const button of document.querySelectorAll('.class-button')) {
    button.classList.toggle('has-cursor', button.dataset.class === currentClass);
    button.classList.toggle('in-batch', batchClasses.has(button.dataset.class));
  }
}

function updateBatchNav() {
  const batches = availableBatches(manifest, state.class);
  const position = batches.indexOf(state.batch);
  document.getElementById('batch-label').textContent = `Batch ${state.batch} (${position + 1}/${batches.length})`;
  document.getElementById('prev-batch').disabled = position <= 0;
  document.getElementById('next-batch').disabled = position < 0 || position >= batches.length - 1;
}

let queueRefreshTimer = null;

function scheduleQueueRefresh() {
  clearTimeout(queueRefreshTimer);
  queueRefreshTimer = setTimeout(() => {
    queueRefreshTimer = null;
    if (state.view === 'queue') renderQueue({ anchor: captureQueueAnchor() });
  }, 150);
}

// A live refresh removes newly judged clusters, so restoring the old scroll offset would shift the page by the height of the removed cards. Anchoring on the first card still in view keeps that card in place.
function captureQueueAnchor() {
  const queue = document.getElementById('queue');
  if (queue.hidden) return null;
  for (const card of queue.querySelectorAll('article.cluster')) {
    const rect = card.getBoundingClientRect();
    if (rect.bottom > 0) return { cluster: card.dataset.cluster, delta: rect.top };
  }
  return null;
}

function appButton(href, label) {
  const link = el('a', 'open-app', `${label} ↗`);
  link.href = href;
  return link;
}

function unitLinkEl(unitId) {
  const link = el('a', 'unit-link', unitId);
  link.href = `#unit=${unitId}`;
  return link;
}

function verdictChipEl(verdict) {
  return el('span', `verdict-chip ${verdict}`, verdict);
}

function worklistHref(unitIds) {
  return `#units=${unitIds.join(',')}`;
}

// Queue worklists carry `queue=1`, which makes finishing the worklist advance to the next queue decision; `decision`, the key the worklist records as shown (see decisionKey); and the corpus stamp, which lets a resumed tab detect that its ids came from an earlier build (see queueResumeAction). Conflict stacks use plain worklists, because resolving a conflict changes existing verdicts instead of filling blanks.
function queueWorklistHref(unitIds, decision) {
  return `${worklistHref(unitIds)}&queue=1&decision=${encodeURIComponent(decision)}&stamp=${encodeURIComponent(manifest.generated_at)}`;
}

function buildEvidenceLine(evidence) {
  const line = el('p', 'evidence');
  if (evidence.counts.length === 0) {
    line.textContent = 'No verdicted unit shares this delta — a fresh question.';
    return line;
  }
  const tallies = evidence.counts.map((entry) => `${entry.verdict} ×${entry.count}`).join(', ');
  line.append(document.createTextNode(`Same delta already judged elsewhere: ${tallies}. `));
  for (const [index, sample] of evidence.samples.entries()) {
    if (index > 0) line.append(document.createTextNode('; '));
    line.append(unitLinkEl(sample.unit));
    line.append(document.createTextNode(` ${sample.verdict}`));
    if (sample.note) line.append(el('span', 'note', ` ${sample.note.slice(0, 90)}`));
  }
  return line;
}

function buildClusterCard(cluster, position) {
  const card = el('article', 'cluster');
  card.dataset.cluster = cluster.id;
  const header = el('header');
  header.append(el('span', 'size', `${position}. ${formatCount(cluster.size)} unit${cluster.size === 1 ? '' : 's'}`));
  header.append(el('span', null, `in ${cluster.duplicateGroups.length} duplicate group${cluster.duplicateGroups.length === 1 ? '' : 's'}`));
  header.append(el('span', 'chip', cluster.class));
  appendConfigGate(header, cluster.exemplar, { detail: false });
  header.append(el('span', 'configs', cluster.id));
  card.append(header);
  if (cluster.exemplar.summary) card.append(el('p', 'summary', cluster.exemplar.summary));
  for (const group of renderGroupsOf(cluster.exemplar)) {
    const pair = el('div', 'render-pair');
    pair.append(el('div', 'config-label', group.label));
    pair.append(buildSample(cluster.exemplar, 'before', group.featureSettings));
    pair.append(buildSample(cluster.exemplar, 'after', group.featureSettings));
    card.append(pair);
  }
  if (!carriesSamples(cluster.exemplar)) hydrateSamples(card, cluster.exemplar);
  const representatives = el('p', 'representatives');
  representatives.append(
    appButton(queueWorklistHref(cluster.representatives, cluster.id), `Judge ${cluster.representatives.length} representative${cluster.representatives.length === 1 ? '' : 's'}`),
  );
  representatives.append(
    el('span', 'note', ` — one per duplicate group; each verdict duplicate-fills its group, covering all ${cluster.size} units.`),
  );
  card.append(representatives);
  card.append(buildEvidenceLine(cluster.evidence));
  const members = el('details');
  members.append(el('summary', null, `All ${cluster.size} members`));
  for (const id of cluster.memberIds) {
    members.append(unitLinkEl(id));
    members.append(document.createTextNode(' '));
  }
  card.append(members);
  return card;
}

function buildLaterSection(later) {
  const section = el('section', 'queue-later');
  let laterUnits = 0;
  for (const cluster of later) laterUnits += cluster.size;
  section.append(el('h2', null, `Smaller clusters — ${later.length} clusters, ${formatCount(laterUnits)} units`));
  const details = el('details');
  details.append(el('summary', null, 'Compact list — clusters promote into the top clusters as those clear'));
  const table = el('table', 'workorder');
  const head = el('thead');
  const headRow = el('tr');
  for (const label of ['Units', 'Class', 'Exemplar', '']) headRow.append(el('th', null, label));
  head.append(headRow);
  table.append(head);
  const body = el('tbody');
  for (const cluster of later) {
    const row = el('tr');
    row.append(el('td', null, String(cluster.size)));
    const classCell = el('td');
    classCell.append(el('span', 'chip', cluster.class));
    row.append(classCell);
    row.append(el('td', null, cluster.exemplar.notation));
    const judge = el('td');
    judge.append(appButton(queueWorklistHref(cluster.representatives, cluster.id), `Judge ${cluster.representatives.length}`));
    row.append(judge);
    body.append(row);
  }
  table.append(body);
  details.append(table);
  section.append(details);
  return section;
}

function buildSingletonSection(singletons) {
  const section = el('section', 'queue-singletons');
  section.append(el('h2', null, `Singletons — ${singletons.length} one-off units`));
  const links = el('p', 'chunk-links', `Work them as app worklists, ${SINGLETON_CHUNK} at a time: `);
  for (const chunk of singletonChunks(singletons)) {
    links.append(appButton(queueWorklistHref(chunk.unitIds, SINGLETON_DECISION), `Judge ${chunk.start}–${chunk.end}`));
    links.append(document.createTextNode(' '));
  }
  section.append(links);
  const details = el('details');
  details.append(el('summary', null, `All ${singletons.length} singletons by name`));
  const table = el('table', 'workorder');
  const body = el('tbody');
  for (const cluster of singletons) {
    const row = el('tr');
    const link = el('td');
    link.append(unitLinkEl(cluster.exemplar.id));
    row.append(link);
    row.append(el('td', null, cluster.exemplar.notation));
    const classCell = el('td');
    classCell.append(el('span', 'chip', cluster.class));
    row.append(classCell);
    body.append(row);
  }
  table.append(body);
  details.append(table);
  section.append(details);
  return section;
}

function buildConflictSection(conflicts) {
  const section = el('section', 'queue-conflicts');
  section.append(el('h2', null, `Duplicate groups with disagreeing verdicts (${conflicts.length})`));
  section.append(
    el('p', 'queue-note', 'The same visual change judged differently across contexts — worth a re-check when convenient.'),
  );
  for (const conflict of conflicts) {
    const card = el('article', 'conflict');
    const header = el('header');
    header.append(el('span', 'chip', conflict.duplicateGroup));
    header.append(el('span', 'chip', conflict.class));
    header.append(appButton(worklistHref(conflict.unitIds), 'View stacked'));
    card.append(header);
    const table = el('table', 'conflict');
    const body = el('tbody');
    for (const id of conflict.unitIds) {
      const row = el('tr');
      const link = el('td');
      link.append(unitLinkEl(id));
      row.append(link);
      row.append(el('td', null, humanRows.get(id)?.notation ?? ''));
      const verdictCell = el('td');
      const record = conflict.records.get(id);
      if (record) verdictCell.append(verdictChipEl(record.verdict));
      else verdictCell.append(el('span', 'note', '(blank)'));
      row.append(verdictCell);
      const noteCell = el('td');
      if (record && record.note) noteCell.append(el('span', 'note', record.note.slice(0, 90)));
      row.append(noteCell);
      body.append(row);
    }
    table.append(body);
    card.append(table);
    section.append(card);
  }
  return section;
}

function renderQueue({ anchor = null } = {}) {
  const container = document.getElementById('queue');
  const scrollY = window.scrollY;
  container.textContent = '';
  let clustered = false;
  for (const row of humanList) {
    if (row.batch !== null && typeof row.cluster === 'string') {
      clustered = true;
      break;
    }
  }
  if (!clustered) {
    container.append(
      el(
        'p',
        'queue-note',
        `This corpus predates cluster signatures — rebuild it with ${manifest.build_command ?? 'uv run python -m rebuild.review.build'} to use the review queue.`,
      ),
    );
    return;
  }
  const recordOf = (id) => store.records.get(id);
  const clusters = buildClusters(humanList, recordOf);
  const ruledIds = ruledClassIds(manifest.classes);
  const { top, later, singletons, ruledBlankUnits } = partitionClusters(clusters, ruledIds);
  const conflicts = duplicateConflicts(duplicateIndex, humanRows, recordOf);
  // The headline counts leave out ledger-ruled classes; the note below reports their blank units.
  const totals = queueTotals(clusters.filter((cluster) => !ruledIds.has(cluster.class)));

  const header = el('header', 'queue-header');
  header.append(el('h2', null, 'Review queue'));
  header.append(
    el(
      'p',
      'queue-provenance',
      `${formatCount(totals.blankUnits)} blank units in ${formatCount(totals.duplicateGroups)} duplicate groups → ` +
        `${formatCount(totals.clusters)} clusters (${formatCount(totals.multiClusters)} multi-unit, ` +
        `${formatCount(totals.singletonClusters)} singleton), live against the current verdicts.`,
    ),
  );
  if (ruledBlankUnits > 0) {
    header.append(
      el(
        'p',
        'queue-note',
        `${formatCount(ruledBlankUnits)} more blank units sit in ledger-ruled classes and are excluded here — ` +
          'one class-level decision (or a bulk-proposal import) covers each; reach them from the sidebar with status “unverdicted”.',
      ),
    );
  }
  header.append(
    el(
      'p',
      'queue-note',
      'Every button stacks a decision as a worklist — judge there with the keyboard flow; duplicate-fill multiplies each verdict, and this queue recomputes as verdicts land.',
    ),
  );
  container.append(header);
  renderQueueReadiness();

  if (totals.clusters === 0) container.append(el('p', 'queue-note', 'No blank units — the queue is clear.'));

  if (top.length > 0) {
    const section = el('section', 'queue-top');
    let topUnits = 0;
    for (const cluster of top) topUnits += cluster.size;
    section.append(
      el('h2', null, `Top ${top.length} cluster decisions — ${formatCount(topUnits)} units`),
    );
    for (const [index, cluster] of top.entries()) section.append(buildClusterCard(cluster, index + 1));
    container.append(section);
  }
  if (later.length > 0) container.append(buildLaterSection(later));
  if (singletons.length > 0) container.append(buildSingletonSection(singletons));
  if (conflicts.length > 0) container.append(buildConflictSection(conflicts));

  document.getElementById('batch-progress').textContent =
    `Review queue: ${formatCount(totals.blankUnits)} blank in ${formatCount(totals.clusters)} clusters`;
  const anchorCard = anchor ? container.querySelector(`article.cluster[data-cluster="${anchor.cluster}"]`) : null;
  if (anchorCard) window.scrollTo(0, anchorCard.getBoundingClientRect().top + window.scrollY - anchor.delta);
  else window.scrollTo(0, scrollY);
}

function populateFilterOptions() {
  const familySelect = document.getElementById('filter-family');
  const existing = new Set();
  for (const option of familySelect.options) existing.add(option.value);
  for (const family of familyOptions) {
    if (existing.has(family)) continue;
    const option = el('option', null, family);
    option.value = family;
    familySelect.append(option);
  }

  const configSelect = document.getElementById('filter-config');
  const existingConfigs = new Set();
  for (const option of configSelect.options) existingConfigs.add(option.value);
  for (const entry of configFilterOptions(manifest)) {
    if (existingConfigs.has(entry.value)) continue;
    const option = el('option', null, entry.label);
    option.value = entry.value;
    if (entry.title) option.title = entry.title;
    configSelect.append(option);
  }
}

function syncFilterControls() {
  document.getElementById('filter-family').value = state.family ?? '';
  const configSelect = document.getElementById('filter-config');
  configSelect.value = state.config ?? '';
  // The closed control also gets the selected option's title, so hovering it shows the selected config's gloss without opening the popup.
  configSelect.title = configSelect.selectedOptions[0]?.title ?? '';
  document.getElementById('filter-status').value = state.status ?? '';
  document.getElementById('show-machine').checked = state.machine === '1';
}

async function applyHashState(resume = false) {
  const token = (renderToken += 1);
  const queueView = state.view === 'queue';
  document.body.classList.toggle('queue-view', queueView);
  document.getElementById('queue').hidden = !queueView;
  if (queueView) {
    await indexReady;
    if (token !== renderToken) return;
    closeRejectMenu();
    closeNeitherMenu();
    visibleUnits = [];
    machineUnits = [];
    foldRecords.clear();
    renderedKey = null;
    renderQueue();
    updateProgress();
    updateTitle();
    updateSidebarHighlights();
    return;
  }
  // On boot and hashchange (`resume`), a queue worklist that is stamped for another corpus or already finished is not rendered; advanceQueue stacks the next decision from the live queue instead, because a rebuild gives every changed unit a new id, so an old hash's worklist was stacked from a queue that no longer exists. The check runs only on resume, so a cursor move, Shift+Enter, or an undo never replaces a worklist the reviewer is looking at.
  if (resume && state.units && state.queue) {
    const action = queueResumeAction({
      stamp: state.stamp,
      manifestStamp: manifest.generated_at,
      unitIds: unitWorklist(state.units),
      recordOf: (id) => store.records.get(id),
    });
    if (action) {
      // The boot index load can be slow. If another navigation starts meanwhile, renderToken changes and this restack is dropped.
      await indexReady;
      if (token !== renderToken) return;
      await advanceQueue({ stale: action === 'restack' });
      return;
    }
  }
  const units = await unitsForView(state.batch, state.class);
  if (token !== renderToken) return;
  const queueKey = state.units && state.queue ? decisionKey(units, state.decision) : null;
  if (queueKey !== lastQueueShownKey) {
    lastQueueShownKey = queueKey;
    if (queueKey !== null) {
      queueShown.delete(queueKey);
      queueShown.add(queueKey);
      saveQueueShown();
    }
  }
  const { human, machine } = partitionUnits(units, state, (unitId) => store.records.get(unitId));
  const plan = machineFoldPlan(manifest, state);
  if (transientMachineUnitId && state.unit !== transientMachineUnitId) {
    transientMachineUnitId = null;
    transientMachineUnit = null;
  }
  if (transientMachineUnitId && !machine.some((unit) => unit.id === transientMachineUnitId)) {
    const transient = unitFor(transientMachineUnitId);
    if (transient) machine.push(transient);
  }
  visibleUnits = human;
  machineUnits = machine;
  const key = JSON.stringify([
    state.class,
    state.batch,
    state.group,
    state.config,
    state.family,
    state.status,
    state.machine,
    state.units,
    state.queue,
    transientMachineUnitId,
  ]);
  if (key !== renderedKey) {
    renderBatch(human, machine, plan);
    renderedKey = key;
    if (state.units) {
      const listed = new Set(unitWorklist(state.units)).size;
      const shown = human.length + machine.length;
      if (shown < listed) toast(`${listed - shown} of ${listed} listed units aren't in this build — showing the ${shown} that are.`);
    }
  }
  syncFilterControls();
  if (!(await ensureCursor())) return;
  updateCursorDom();
  if (state.unit) {
    await revealMachineUnit(state.unit);
    if (token !== renderToken) return;
  }
  updateProgress();
  updateTitle();
  updateBatchNav();
  updateSidebarHighlights();
}

let toastTimer = null;
// Scales with reading time. Each digit adds half a word, because numerals such as "3,193" are read digit by digit.
function toastDuration(message) {
  let units = 0;
  for (const token of message.split(/\s+/)) {
    if (!token) continue;
    const digits = (token.match(/\d/g) ?? []).length;
    units += 1 + digits / 2;
  }
  return Math.min(2600 + units * 320, 10000);
}
function toast(message) {
  const node = document.getElementById('toast');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, toastDuration(message));
}

function applyVerdict(unitId, verdict, { toggle = true, note = null } = {}) {
  const row = rowFor(unitId);
  const noteValue = note ?? (row ? row.querySelector('.note').value : '');
  const existing = store.records.get(unitId);
  const wasUnverdicted = !existing;
  if (toggle && existing && existing.verdict === verdict) {
    recordVerdict(store, unitId, null);
  } else {
    // A verdict also goes to the unverdicted members of the unit's duplicate group, which show the same change on the same judged pair. Skip is a per-unit deferral and is never copied, and members that already have a record, a skip included, are not overwritten.
    const unit = unitFor(unitId);
    const duplicates =
      verdict === 'skip' || !unit
        ? []
        : duplicateFillTargets(unit, duplicateIndex.get(unit.duplicate_group) ?? [], (id) => store.records.has(id));
    const applied = recordVerdictWithDuplicates(store, unitId, verdict, duplicates, { note: noteValue });
    for (const id of applied) syncRowVerdict(id);
    if (applied.length > 1) {
      toast(`Copied to ${applied.length - 1} duplicate window${applied.length === 2 ? '' : 's'} (u to undo all)`);
    }
    lastVerdictedUnitId = unitId;
  }
  syncRowVerdict(unitId);
  updateProgress();
  scheduleAutosave();
  if (!autosaveHealthy() && store.unexported.size > 0 && store.unexported.size % 50 === 0) {
    toast(`${store.unexported.size} verdicts not yet exported — consider downloading verdicts.json`);
  }
  return wasUnverdicted;
}

async function advanceFrom(unitId) {
  const ids = [];
  for (const unit of visibleUnits) ids.push(unit.id);
  const fromIndex = ids.indexOf(unitId);
  const next = nextUnverdictedIndex(ids, fromIndex, (id) => store.records.has(id));
  if (next !== -1) {
    setStateReplace({ unit: ids[next] });
    return;
  }
  if (state.units) {
    if (state.queue) {
      await advanceQueue();
      return;
    }
    toast('Everything in this worklist is verdicted');
    updateTitle();
    return;
  }
  const batches = availableBatches(manifest, state.class);
  for (const batch of batches) {
    if (batch <= state.batch) continue;
    const units = await unitsForView(batch, state.class);
    const { human } = partitionUnits(units, { ...state, batch }, (id) => store.records.get(id));
    const open = human.find((unit) => !store.records.has(unit.id));
    if (open) {
      toast(`Batch ${state.batch} done — continuing in batch ${batch}`);
      setState({ batch, unit: open.id });
      return;
    }
  }
  toast('Everything in this view is verdicted — press ] for the next class');
  updateTitle();
}

// When a queue worklist is fully judged, stack the next decision at once, as advanceFrom moves on to the next batch. The queue is recomputed from the live store, so the next decision is the top one not yet opened (queueShown); postponed ones come back only after those run out. History is replaced, not pushed, so Back returns to the review queue in one step.
async function advanceQueue({ stale = false } = {}) {
  await indexReady;
  const recordOf = (id) => store.records.get(id);
  const decision = nextQueueDecision(humanList, recordOf, ruledClassIds(manifest.classes), queueShown);
  const lead = stale ? 'That worklist was stacked for an earlier corpus' : 'Decision done';
  if (!decision) {
    toast(`${stale ? `${lead}, and the` : 'The'} review queue is clear`);
    setState({ units: null, order: null, queue: null, decision: null, stamp: null, unit: null, view: 'queue' });
    return;
  }
  const queue = queueCounts(humanList, recordOf, ruledClassIds(manifest.classes));
  const what =
    decision.kind === 'cluster'
      ? `${formatCount(decision.cluster.size)} lookalike unit${decision.cluster.size === 1 ? '' : 's'} in ${decision.cluster.class}`
      : `${decision.unitIds.length} singletons`;
  toast(`${lead} — ${decision.revisit ? 'back round to' : 'next'}: ${what} (${formatCount(queue.blankUnits)} blank left)`);
  setStateReplace({
    units: decision.unitIds.join(','),
    queue: '1',
    decision: decision.key,
    stamp: manifest.generated_at,
    unit: null,
    view: null,
  });
}

function verdictCursor(verdict) {
  const unitId = cursorUnitId();
  if (!unitId) return;
  if (applyVerdict(unitId, verdict)) advanceFrom(unitId);
}

let lastVerdictedUnitId = null;

// Repeats the source unit's current record, so a note typed after the verdict is repeated too. It never toggles a verdict off, so pressing r across a run of identical cases is safe.
function repeatLast(unitId = cursorUnitId()) {
  const source = lastVerdictedUnitId === null ? null : store.records.get(lastVerdictedUnitId);
  if (!source) {
    toast('Nothing to repeat');
    return;
  }
  if (!unitId || unitId === lastVerdictedUnitId) return;
  if (applyVerdict(unitId, source.verdict, { toggle: false, note: source.note || null })) advanceFrom(unitId);
}

async function jumpToFirstUnverdicted() {
  if (state.units) {
    const open = visibleUnits.find((unit) => !store.records.has(unit.id));
    if (!open) {
      if (state.queue) {
        await advanceQueue();
        return;
      }
      toast('Everything in this worklist is verdicted');
      return;
    }
    setStateReplace({ unit: open.id, view: null });
    return;
  }
  const fromQueue = state.view === 'queue';
  for (const batch of availableBatches(manifest, null)) {
    const units = await unitsForView(batch, null);
    const { human } = partitionUnits(units, { ...state, class: null, batch }, (id) => store.records.get(id));
    const open = human.find((unit) => !store.records.has(unit.id));
    if (!open) continue;
    const keepClass = state.class && open.class === state.class ? state.class : null;
    if (state.class && !keepClass) toast(`First unverdicted is in ${open.class} — class filter cleared`);
    if (!fromQueue && batch === state.batch && keepClass === state.class) {
      setStateReplace({ unit: open.id, view: null });
    } else {
      setState({ batch, class: keepClass, unit: open.id, view: null });
    }
    return;
  }
  toast('Every unit in every class and batch is verdicted');
}

let rejectMenuUnitId = null;
let rejectMenuNode = null;
let rejectMenuRecents = [];

// Keys 1–9 then 0 pick from the ten most recent distinct notes in the store with the menu's verdict kind, leaving out the menu's preset notes, so a repeated objection is typed once.
function recentKeyLabel(index) {
  return index === 9 ? '0' : String(index + 1);
}

function recentIndexForAction(action, prefix) {
  if (!action.startsWith(prefix)) return null;
  const digit = action.slice(prefix.length);
  return digit === '0' ? 9 : Number.parseInt(digit, 10) - 1;
}

function appendRecentOptions(menu, optionClass, actionPrefix, recents) {
  if (recents.length === 0) return;
  menu.append(el('div', 'menu-sep'));
  for (const [index, note] of recents.entries()) {
    const option = el('button', `${optionClass} recent`);
    option.type = 'button';
    option.dataset.action = `${actionPrefix}${recentKeyLabel(index)}`;
    option.setAttribute('role', 'menuitem');
    option.append(el('kbd', null, recentKeyLabel(index)));
    option.append(document.createTextNode(` ${note}`));
    menu.append(option);
  }
}

function openRejectMenu(unitId) {
  closeRejectMenu();
  const row = rowFor(unitId);
  if (!row) return;
  const menu = el('div', 'reject-menu');
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', `Reject ${unitId} — choose a note`);
  for (const choice of REJECT_MENU_CHOICES) {
    const option = el('button', 'reject-option');
    option.type = 'button';
    option.dataset.action = choice.action;
    option.setAttribute('role', 'menuitem');
    option.append(el('kbd', null, choice.key));
    option.append(document.createTextNode(` ${choice.label}`));
    menu.append(option);
  }
  rejectMenuRecents = recentNotes(store, 'reject', {
    exclude: REJECT_MENU_CHOICES.map((choice) => choice.note).filter(Boolean),
  });
  appendRecentOptions(menu, 'reject-option', 'reject-recent-', rejectMenuRecents);
  menu.addEventListener('click', (event) => {
    const option = event.target.closest('.reject-option');
    if (!option) return;
    event.stopPropagation();
    if (option.dataset.action === 'reject-comment') {
      rejectWithComment();
      return;
    }
    const recentIndex = recentIndexForAction(option.dataset.action, 'reject-recent-');
    if (recentIndex !== null) {
      chooseRejectOption(rejectMenuRecents[recentIndex] ?? null);
      return;
    }
    const choice = REJECT_MENU_CHOICES.find((entry) => entry.action === option.dataset.action);
    chooseRejectOption(choice.note);
  });
  row.querySelector('.verdict-buttons').append(menu);
  rejectMenuUnitId = unitId;
  rejectMenuNode = menu;
  menu.querySelector('.reject-option').focus();
}

function closeRejectMenu() {
  if (rejectMenuNode) rejectMenuNode.remove();
  rejectMenuUnitId = null;
  rejectMenuNode = null;
  rejectMenuRecents = [];
}

function chooseRejectOption(cannedNote) {
  const unitId = rejectMenuUnitId;
  closeRejectMenu();
  if (!unitId) return;
  const row = rowFor(unitId);
  if (cannedNote !== null && row) {
    row.querySelector('.note').value = cannedNote;
    updateNote(store, unitId, cannedNote);
  }
  if (applyVerdict(unitId, 'reject')) advanceFrom(unitId);
}

function rejectWithComment() {
  const unitId = rejectMenuUnitId;
  closeRejectMenu();
  if (!unitId) return;
  applyVerdict(unitId, 'reject');
  const row = rowFor(unitId);
  if (row) row.querySelector('.note').focus();
}

let neitherMenuUnitId = null;
let neitherMenuNode = null;
let neitherMenuRecents = [];

function openNeitherMenu(unitId) {
  closeNeitherMenu();
  const row = rowFor(unitId);
  if (!row) return;
  const menu = el('div', 'neither-menu');
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', `Neither ${unitId} — choose a note`);
  for (const choice of NEITHER_MENU_CHOICES) {
    const option = el('button', 'neither-option');
    option.type = 'button';
    option.dataset.action = choice.action;
    option.setAttribute('role', 'menuitem');
    option.append(el('kbd', null, choice.key));
    option.append(document.createTextNode(` ${choice.label}`));
    menu.append(option);
  }
  neitherMenuRecents = recentNotes(store, 'neither', {
    exclude: NEITHER_MENU_CHOICES.map((choice) => choice.note).filter(Boolean),
  });
  appendRecentOptions(menu, 'neither-option', 'neither-recent-', neitherMenuRecents);
  menu.addEventListener('click', (event) => {
    const option = event.target.closest('.neither-option');
    if (!option) return;
    event.stopPropagation();
    if (option.dataset.action === 'neither-comment') {
      neitherWithComment();
      return;
    }
    const recentIndex = recentIndexForAction(option.dataset.action, 'neither-recent-');
    if (recentIndex !== null) {
      chooseNeitherOption(neitherMenuRecents[recentIndex] ?? null);
      return;
    }
    const choice = NEITHER_MENU_CHOICES.find((entry) => entry.action === option.dataset.action);
    chooseNeitherOption(choice.note);
  });
  row.querySelector('.verdict-buttons').append(menu);
  neitherMenuUnitId = unitId;
  neitherMenuNode = menu;
  menu.querySelector('.neither-option').focus();
}

function closeNeitherMenu() {
  if (neitherMenuNode) neitherMenuNode.remove();
  neitherMenuUnitId = null;
  neitherMenuNode = null;
  neitherMenuRecents = [];
}

function chooseNeitherOption(cannedNote) {
  const unitId = neitherMenuUnitId;
  closeNeitherMenu();
  if (!unitId) return;
  const row = rowFor(unitId);
  if (cannedNote !== null && row) {
    row.querySelector('.note').value = cannedNote;
    updateNote(store, unitId, cannedNote);
  }
  if (applyVerdict(unitId, 'neither')) advanceFrom(unitId);
}

function neitherWithComment() {
  const unitId = neitherMenuUnitId;
  closeNeitherMenu();
  if (!unitId) return;
  applyVerdict(unitId, 'neither');
  const row = rowFor(unitId);
  if (row) row.querySelector('.note').focus();
}

function renderedCursorIds() {
  const ids = [];
  for (const row of document.querySelectorAll('#batch .row:not(.machine)')) ids.push(row.dataset.unit);
  return ids;
}

function moveCursor(delta) {
  const ids = renderedCursorIds();
  const index = stepIndex(ids.length, ids.indexOf(cursorUnitId()), delta);
  if (index === -1) return;
  setStateReplace({ unit: ids[index] });
}

function shiftBatch(delta) {
  const batches = availableBatches(manifest, state.class);
  const index = stepIndex(batches.length, batches.indexOf(state.batch), delta);
  if (index === -1 || batches[index] === state.batch) return;
  setState({ batch: batches[index], unit: null });
}

function shiftClass(delta) {
  const ids = [];
  for (const cls of manifest.classes) if (humanClassCount(cls) > 0) ids.push(cls.id);
  if (ids.length === 0) return;
  const current = ids.indexOf(state.class);
  const next = current === -1 ? (delta > 0 ? 0 : ids.length - 1) : (current + delta + ids.length) % ids.length;
  const classId = ids[next];
  const batches = availableBatches(manifest, classId);
  setState({ class: classId, batch: batches.length > 0 ? batches[0] : 0, unit: null, group: null });
}

function approveGroupOf(unitId) {
  const unit = unitFor(unitId);
  if (!unit) return;
  const ids = [];
  for (const candidate of visibleUnits) {
    if (candidate.group === unit.group && !store.records.has(candidate.id)) ids.push(candidate.id);
  }
  // Each approval is also copied to the rest of its duplicate group, including members in other batches. groupApprove skips units that already have a record, so duplicate ids in the list are harmless.
  const expanded = [...ids];
  for (const id of ids) {
    const member = humanRows.get(id);
    if (member?.duplicate_group) expanded.push(...(duplicateIndex.get(member.duplicate_group) ?? []));
  }
  const applied = groupApprove(store, expanded);
  for (const id of applied) syncRowVerdict(id);
  if (applied.length > 0) lastVerdictedUnitId = applied[0];
  updateProgress();
  scheduleAutosave();
  const inGroup = applied.filter((id) => ids.includes(id)).length;
  const copied = applied.length - inGroup;
  toast(`Approved ${inGroup} remaining in ${unit.group}${copied > 0 ? ` + ${copied} duplicates elsewhere` : ''}`);
  advanceFrom(unitId);
}

function undoLast() {
  const result = undo(store);
  if (!result) {
    toast('Nothing to undo');
    return;
  }
  for (const unitId of result.units) syncRowVerdict(unitId);
  updateProgress();
  scheduleAutosave();
  setStateReplace({ unit: result.cursor });
  toast(`Undid ${result.units.length === 1 ? result.cursor : `${result.units.length} verdicts`}`);
}

// An app-index row has no explain material, so opening its panel fetches the shard record: one Range request, or none if the card's fetch left the record in fullRecords. Rows that take no verdict are built from their shard record and open with the panel already filled.
async function toggleExplain(unitId) {
  const row = rowFor(unitId);
  if (!row) return;
  const panel = row.querySelector('.explain-panel');
  panel.hidden = !panel.hidden;
  row.querySelector('.explain-toggle').setAttribute('aria-expanded', String(!panel.hidden));
  if (panel.hidden || panel.dataset.filled === '1' || panel.dataset.loading === '1') return;
  const locator = humanRows.get(unitId);
  if (!locator) return;
  panel.dataset.loading = '1';
  // Remove the message a failed read left, so a retry replaces it instead of adding another.
  for (const stale of panel.querySelectorAll('.explain-pending')) stale.remove();
  const pending = el('p', 'explain-pending', 'Reading this unit’s explain table out of its shard…');
  panel.append(pending);
  const record = await fetchFullRecord(locator);
  delete panel.dataset.loading;
  if (!panel.isConnected) return;
  if (!record) {
    pending.textContent = 'Could not read this unit’s explain table.';
    return;
  }
  fillExplainPanel(panel, record);
}

function copyToClipboard(text, button) {
  const flash = () => {
    if (!button) return;
    button.classList.add('copied');
    setTimeout(() => button.classList.remove('copied'), 1200);
  };
  try {
    const result = navigator.clipboard && navigator.clipboard.writeText(text);
    if (result && typeof result.then === 'function') {
      result.then(flash).catch((error) => console.warn('clipboard write failed', error));
    } else {
      flash();
    }
  } catch (error) {
    console.warn('clipboard write failed', error);
  }
}

function exportPayload() {
  return JSON.stringify(assembleExport(store, manifest.generated_at), null, 2);
}

// The autosave sends changes, not the store. A flush POSTs a set or a clear for each unit in store.dirty, so its size follows what the reader just did, not the store, which holds every carried and filled verdict on the corpus. The server keeps the store in memory, applies the delta, and returns a sync token. syncVerdictsFromServer sends the token back and receives only the changes since, so the focus re-merge and the queue poll get an empty delta while nothing changes. Flushes run one at a time, because two deltas in flight could arrive out of order and a clear could lose to the set it undid.
// Every change is also written to the outbox (outbox.js) the moment it is scheduled, and stays there until the server's reply acknowledges that write, so a save that a 503, a network error, a server restart, or a closed tab interrupted is not lost: a flush retries on a timer, and the next page load sends what an earlier one left. A reply can hand back conflicts (the server kept a newer record) and orphans (units the served corpus does not have); the page adopts the server's record for a conflict and offers to reapply its own.
const AUTOSAVE_DEBOUNCE_MS = 800;
const AUTOSAVE_CHUNK = 20000;
const AUTOSAVE_RETRY_FIRST_MS = 1000;
const AUTOSAVE_RETRY_MAX_MS = 10000;
const KEEPALIVE_LIMIT_BYTES = 60 * 1024;
let autosaveTimer = null;
let autosaveWorks = false;
let autosaveFailed = false;
let autosaveInFlight = false;
let autosaveToken = null;
let autosaveRetryMs = AUTOSAVE_RETRY_FIRST_MS;
let autosaveRetrying = false;

const outboxStorage = (() => {
  try {
    const storage = window.localStorage;
    storage.getItem(OUTBOX_KEY);
    return storage;
  } catch {
    return null;
  }
})();
const TAB_ID = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
let outboxSeq = 0;
let outboxWorks = outboxStorage !== null;
const outboxMirror = new Map();
let replayQueue = [];
let staleOutbox = [];
let staleDownloaded = false;
const pendingConflicts = new Map();

class RetryableSaveError extends Error {}

function mirrorDirtyToOutbox() {
  if (!outboxStorage) return;
  const entries = [];
  const writtenAt = new Date().toISOString();
  for (const unit of store.dirty) {
    const record = store.records.get(unit) ?? null;
    const known = outboxMirror.get(unit);
    if (known && sameRecord(known.record, record)) continue;
    outboxSeq += 1;
    const snapshot = record ? { unit, verdict: record.verdict, note: record.note, at: record.at } : null;
    outboxMirror.set(unit, { seq: outboxSeq, record: snapshot });
    entries.push([
      unit,
      {
        record: snapshot,
        base_at: store.serverAt.get(unit) ?? null,
        stamp: manifest.generated_at,
        tab: TAB_ID,
        seq: outboxSeq,
        written_at: writtenAt,
      },
    ]);
  }
  if (entries.length > 0) outboxWorks = writeOutbox(outboxStorage, entries);
}

function keepTabAlive() {
  if (outboxStorage) markTabAlive(outboxStorage, TAB_ID);
}

function acknowledgeOutbox(units, sentSeqs) {
  const acks = [];
  for (const unit of units) {
    const seq = sentSeqs.get(unit);
    if (seq === undefined) continue;
    acks.push({ unit, tab: TAB_ID, seq });
    if (outboxMirror.get(unit)?.seq === seq) outboxMirror.delete(unit);
  }
  if (outboxStorage && acks.length > 0) ackOutbox(outboxStorage, acks);
}

function scheduleAutosave() {
  mirrorDirtyToOutbox();
  clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(flushAutosave, AUTOSAVE_DEBOUNCE_MS);
}

function scheduleAutosaveRetry() {
  autosaveRetrying = true;
  clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(flushAutosave, autosaveRetryMs);
  autosaveRetryMs = Math.min(autosaveRetryMs * 2, AUTOSAVE_RETRY_MAX_MS);
}

function takeDirty() {
  const ids = [...store.dirty];
  store.dirty.clear();
  return ids;
}

function restoreDirty(ids) {
  for (const id of ids) store.dirty.add(id);
}

function deltaPayload(ids) {
  return JSON.stringify(assembleDelta(store, manifest.generated_at, ids));
}

async function postAutosave(payload) {
  const body = JSON.stringify(payload);
  let response;
  try {
    response = await fetch('autosave', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: new TextEncoder().encode(body).length < KEEPALIVE_LIMIT_BYTES,
    });
  } catch (error) {
    throw new RetryableSaveError(error.message);
  }
  if (response.status === 503) throw new RetryableSaveError('HTTP 503');
  return response;
}

function handleSaveOutcome(reply, sentRecords, changedSince) {
  const orphaned = Array.isArray(reply.orphaned) ? reply.orphaned : [];
  const conflicts = Array.isArray(reply.conflicts) ? reply.conflicts : [];
  let adopted = 0;
  for (const conflict of conflicts) {
    const unit = conflict?.unit;
    if (typeof unit !== 'string') continue;
    if (changedSince(unit)) {
      store.serverAt.set(unit, conflict.server ? (conflict.server.at ?? '') : null);
      continue;
    }
    pendingConflicts.set(unit, sentRecords.get(unit) ?? null);
    adoptServerRecord(store, unit, conflict.server ?? null);
    syncRowVerdict(unit);
    adopted += 1;
  }
  const lines = [];
  if (adopted > 0) lines.push(`${adopted} verdict${adopted === 1 ? '' : 's'} conflicted with newer ones; kept in the conflicts list`);
  if (orphaned.length > 0) {
    lines.push(
      `${orphaned.length} verdict${orphaned.length === 1 ? ' is' : 's are'} on units this corpus no longer has; kept in var/verdict-orphans/`,
    );
  }
  if (lines.length > 0) toast(lines.join(' · '));
}

function sentRecordsOf(payload) {
  const sent = new Map();
  for (const record of payload.sets) sent.set(record.unit, { verdict: record.verdict, note: record.note, at: record.at });
  for (const clear of payload.clears) sent.set(clear.unit, null);
  return sent;
}

async function sendReplays() {
  while (replayQueue.length > 0) {
    const group = replayQueue[0];
    const entries = outboxStorage ? currentEntries(outboxStorage, group.entries) : group.entries;
    if (entries.length === 0) {
      replayQueue.shift();
      continue;
    }
    const payload = replayDelta(group.stamp, entries);
    const response = await postAutosave(payload);
    replayQueue.shift();
    if (!response.ok) {
      console.warn('outbox replay refused', response.status);
      staleOutbox.push(...entries);
      staleDownloaded = false;
      toast(
        `The server refused ${entries.length} verdict${entries.length === 1 ? '' : 's'} this browser kept from an earlier session (HTTP ${response.status}); download them from the status bar`,
      );
      continue;
    }
    const reply = await response.json();
    if (typeof reply.token === 'string' && group.stamp === manifest.generated_at) autosaveToken = reply.token;
    const refused = new Set(Array.isArray(reply.orphaned) ? reply.orphaned : []);
    for (const conflict of Array.isArray(reply.conflicts) ? reply.conflicts : []) refused.add(conflict?.unit);
    let saved = 0;
    for (const entry of entries) {
      if (refused.has(entry.unit) || store.dirty.has(entry.unit)) continue;
      if (entry.record) store.records.set(entry.unit, { ...entry.record, unit: entry.unit });
      else store.records.delete(entry.unit);
      store.serverAt.set(entry.unit, entry.record ? entry.record.at : null);
      syncRowVerdict(entry.unit);
      saved += 1;
    }
    handleSaveOutcome(reply, sentRecordsOf(payload), (unit) => store.dirty.has(unit));
    if (outboxStorage) ackOutbox(outboxStorage, entries);
    if (saved > 0) toast(`Saved ${saved} verdict${saved === 1 ? '' : 's'} this browser kept from an earlier session`);
  }
}

async function flushAutosave() {
  clearTimeout(autosaveTimer);
  autosaveTimer = null;
  if (autosaveInFlight) {
    scheduleAutosave();
    return;
  }
  mirrorDirtyToOutbox();
  if (store.dirty.size === 0 && replayQueue.length === 0) return;
  autosaveInFlight = true;
  let ids = [];
  try {
    await sendReplays();
    ids = takeDirty();
    const sentSeqs = new Map();
    for (const id of ids) {
      const seq = outboxMirror.get(id)?.seq;
      if (seq !== undefined) sentSeqs.set(id, seq);
    }
    // A normal flush is one decision and its duplicate fills, but an Import can dirty every record in a file, so the ids go out in chunks of AUTOSAVE_CHUNK and no body reaches the server's request-size limit.
    for (let start = 0; start < ids.length; start += AUTOSAVE_CHUNK) {
      const chunk = ids.slice(start, start + AUTOSAVE_CHUNK);
      const payload = assembleDelta(store, manifest.generated_at, chunk);
      let response;
      try {
        response = await postAutosave(payload);
      } catch (error) {
        restoreDirty(ids.slice(start));
        throw error;
      }
      if (!response.ok) {
        restoreDirty(ids.slice(start));
        throw new Error(`HTTP ${response.status}`);
      }
      const reply = await response.json();
      if (typeof reply.token === 'string') autosaveToken = reply.token;
      acknowledgeDelta(store, payload);
      const sent = sentRecordsOf(payload);
      handleSaveOutcome(reply, sent, (unit) => !sameRecord(store.records.get(unit) ?? null, sent.get(unit) ?? null));
      acknowledgeOutbox(chunk, sentSeqs);
    }
    autosaveWorks = true;
    autosaveFailed = false;
    autosaveRetrying = false;
    autosaveRetryMs = AUTOSAVE_RETRY_FIRST_MS;
    if (store.dirty.size > 0) scheduleAutosave();
  } catch (error) {
    console.warn('autosave failed', error);
    if (error instanceof RetryableSaveError) {
      scheduleAutosaveRetry();
    } else if (!autosaveFailed) {
      toast('Autosave failed — download verdicts.json to be safe');
    }
    autosaveFailed = true;
  } finally {
    autosaveInFlight = false;
  }
  updateProgress();
}

function autosaveHealthy() {
  return autosaveWorks && !autosaveFailed;
}

function reapplyConflicts() {
  const count = pendingConflicts.size;
  for (const [unit, record] of pendingConflicts) {
    if (record) recordVerdict(store, unit, record.verdict, { note: record.note });
    else recordVerdict(store, unit, null);
    syncRowVerdict(unit);
  }
  pendingConflicts.clear();
  updateProgress();
  scheduleAutosave();
  toast(`Reapplied ${count} verdict${count === 1 ? '' : 's'}`);
}

// Downloads the outbox entries this page could not send, one file per corpus they were made on, with the clears as a `cleared` list of { unit, at }. The entries stay in the outbox until the reader says the files are saved (forgetStaleOutbox), so a blocked or cancelled download loses nothing.
function downloadStaleOutbox() {
  const byStamp = new Map();
  for (const entry of staleOutbox) {
    if (!byStamp.has(entry.stamp)) byStamp.set(entry.stamp, []);
    byStamp.get(entry.stamp).push(entry);
  }
  const exportedAt = new Date().toISOString();
  for (const [stamp, entries] of byStamp) {
    const verdicts = [];
    const cleared = [];
    for (const entry of entries) {
      if (entry.record) verdicts.push({ ...entry.record, unit: entry.unit });
      else cleared.push({ unit: entry.unit, at: entry.written_at });
    }
    const payload = { format: EXPORT_FORMAT, manifest_generated_at: stamp, exported_at: exportedAt, verdicts };
    if (cleared.length > 0) payload.cleared = cleared;
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `verdicts-unsaved-${stamp.replace(/[^0-9A-Za-z.-]/gu, '.')}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }
  staleDownloaded = true;
  updateProgress();
}

function forgetStaleOutbox() {
  if (outboxStorage) ackOutbox(outboxStorage, staleOutbox);
  staleOutbox = [];
  staleDownloaded = false;
  updateProgress();
}

// The status bar's outbox line: saves waiting for a retry, conflicts to reapply, and outbox entries too old to replay.
function renderOutboxStatus() {
  const node = document.getElementById('outbox-status');
  if (!node) return;
  const parts = [];
  if (autosaveRetrying) {
    const waiting = store.dirty.size + replayQueue.reduce((sum, group) => sum + group.entries.length, 0);
    const where = outboxWorks ? 'kept in this browser' : 'not yet saved';
    parts.push(el('span', 'outbox-waiting', `${formatCount(waiting)} verdict${waiting === 1 ? '' : 's'} ${where}, saving…`));
  }
  if (pendingConflicts.size > 0) {
    const button = el('button', 'outbox-reapply', `Reapply ${pendingConflicts.size} conflicted`);
    button.type = 'button';
    button.title = 'The server kept newer verdicts on these units; click to record yours again over them';
    button.addEventListener('click', reapplyConflicts);
    parts.push(button);
  }
  if (staleOutbox.length > 0) {
    const button = el('button', 'outbox-download', `Download ${staleOutbox.length} unsaved`);
    button.type = 'button';
    button.title =
      'This browser kept these changes from a session that closed before they were saved, and they are too old to send on their own or the server refused them';
    button.addEventListener('click', downloadStaleOutbox);
    parts.push(button);
    if (staleDownloaded) {
      const forget = el('button', 'outbox-forget', 'Saved them — forget');
      forget.type = 'button';
      forget.title = 'Remove these changes from this browser once the downloaded files are saved';
      forget.addEventListener('click', forgetStaleOutbox);
      parts.push(forget);
    }
  }
  node.replaceChildren(...parts);
  node.hidden = parts.length === 0;
}

// Sorts what earlier page loads left in the outbox against the server's records: the entries of tabs still open are left to them, saved entries are dropped, recent ones are queued for the first flush after boot, and old ones are offered as a download.
function planReplayFromOutbox(serverRecords) {
  if (!outboxStorage) return;
  const live = liveTabs(outboxStorage);
  live.delete(TAB_ID);
  const plan = planOutboxReplay(readOutbox(outboxStorage), serverRecords, { live });
  if (plan.drop.length > 0) dropOutbox(outboxStorage, plan.drop);
  replayQueue = [...plan.replay].map(([stamp, entries]) => ({ stamp, entries }));
  staleOutbox = plan.stale;
}

async function restoreAutosave() {
  let data = null;
  try {
    const response = await fetch('autosave');
    if (response.status === 404) {
      autosaveWorks = true;
      planReplayFromOutbox(new Map());
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch (error) {
    console.warn('autosave restore failed', error);
    return;
  }
  autosaveWorks = true;
  const serverRecords = new Map();
  for (const record of Array.isArray(data.verdicts) ? data.verdicts : []) {
    if (record && typeof record.unit === 'string') serverRecords.set(record.unit, record);
  }
  const result = importVerdicts(store, data, manifest.generated_at);
  if (!result.ok) {
    if (result.mismatch) {
      toast(
        `Found an autosave from a different corpus build (${data.verdicts.length} verdicts) — not restored; it'll be stashed aside on your next verdict`,
      );
    }
    planReplayFromOutbox(serverRecords);
    return;
  }
  if (typeof data.token === 'string') autosaveToken = data.token;
  noteServerRecords(store, serverRecords.values());
  markExported(store);
  for (const id of result.units) store.dirty.delete(id);
  if (result.added > 0) toast(`Restored ${result.added} autosaved verdicts`);
  planReplayFromOutbox(serverRecords);
}

// The store lives in this page and is restored from the server only at boot, so a copy open in another tab goes stale as verdicts are recorded elsewhere. When the page regains focus it merges the server's changes, the newer `at` winning as in an import. With a token the server returns only the records changed since; without one, or when the server does not recognize it (after a restart or an external rewrite of the file onto another stamp), it returns the whole store and a new token. A clear made in another session is not applied: clears are rare and visible, and a copy that keeps the record sends it back only when the reader changes it again. Each record the server sends becomes the unit's `serverAt`, and each clear sets it to null, so this copy's next change to the unit is sent over what the server holds.
let verdictSyncInFlight = false;
let verdictSyncLastAt = 0;
let bootRestoreDone = false;

async function syncVerdictsFromServer() {
  if (!bootRestoreDone || verdictSyncInFlight || Date.now() - verdictSyncLastAt < 2000) return;
  verdictSyncInFlight = true;
  try {
    const query = autosaveToken ? `?since=${encodeURIComponent(autosaveToken)}` : '';
    const response = await fetch(`autosave${query}`);
    if (!response.ok) return;
    const data = await response.json();
    const incoming = data.format === DELTA_FORMAT ? { ...data, format: EXPORT_FORMAT, verdicts: data.sets } : data;
    const result = importVerdicts(store, incoming, manifest.generated_at);
    if (!result.ok) return;
    if (typeof data.token === 'string') autosaveToken = data.token;
    noteServerRecords(store, incoming.verdicts);
    if (data.format === DELTA_FORMAT) {
      for (const unit of Array.isArray(data.clears) ? data.clears : []) store.serverAt.set(unit, null);
    }
    if (result.units.length === 0) return;
    for (const id of result.units) {
      store.unexported.delete(id);
      store.dirty.delete(id);
      outboxMirror.delete(id);
      syncRowVerdict(id);
    }
    if (outboxStorage) dropOutbox(outboxStorage, result.units, TAB_ID);
    updateProgress();
    toast(`Picked up ${result.units.length} verdict${result.units.length === 1 ? '' : 's'} from another session`);
  } catch (error) {
    console.warn('verdict sync failed', error);
  } finally {
    verdictSyncInFlight = false;
    verdictSyncLastAt = Date.now();
  }
}

const PAGE_LOADED_AT = new Date().toISOString();
let lastStatusModel = null;
let statusRefreshInFlight = false;
let statusRefreshLastAt = 0;

function readinessLine(model) {
  return model.remedy && model.level !== 'ready' ? `${model.text} — ${model.remedy}` : model.text;
}

function renderReadinessBanner() {
  const node = document.getElementById('readiness');
  if (!node || !lastStatusModel) return;
  node.textContent = readinessLine(lastStatusModel);
  node.className = `readiness readiness-${lastStatusModel.level}`;
  node.title = lastStatusModel.remedy ?? '';
  if (lastStatusModel.command) {
    const command = lastStatusModel.command;
    const button = el('button', 'readiness-copy', 'Copy');
    button.type = 'button';
    button.title = `Copy ${command} to the clipboard`;
    button.addEventListener('click', () => copyToClipboard(command, button));
    node.append(button);
  }
  node.hidden = false;
}

function renderQueueReadiness() {
  const header = document.querySelector('#queue .queue-header');
  if (!header) return;
  const existing = header.querySelector('.queue-readiness');
  if (existing) existing.remove();
  if (!lastStatusModel || lastStatusModel.level === 'ready') return;
  header.append(el('p', `queue-readiness readiness-${lastStatusModel.level}`, readinessLine(lastStatusModel)));
}

async function refreshStatus() {
  if (statusRefreshInFlight || Date.now() - statusRefreshLastAt < 2000) return;
  statusRefreshInFlight = true;
  try {
    let payload = null;
    try {
      const response = await fetch('status');
      payload = await response.json();
    } catch {
      payload = null;
    }
    lastStatusModel = bannerModel(payload, manifest.generated_at, PAGE_LOADED_AT);
    renderReadinessBanner();
    if (state.view === 'queue') renderQueueReadiness();
  } finally {
    statusRefreshInFlight = false;
    statusRefreshLastAt = Date.now();
  }
}

function verdictsFilename(now = new Date()) {
  const time = now
    .toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true })
    .replaceAll(':', '.')
    .replace(/\s+/gu, '');
  return `verdicts-${time}.json`;
}

function downloadVerdicts() {
  const blob = new Blob([exportPayload()], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = verdictsFilename();
  anchor.click();
  URL.revokeObjectURL(url);
  markExported(store);
  updateProgress();
  toast(`Exported ${store.records.size} verdicts`);
}

function runImport(text) {
  let data = null;
  try {
    data = JSON.parse(text);
  } catch {
    toast('Import failed: not valid JSON');
    return;
  }
  let result = importVerdicts(store, data, manifest.generated_at);
  if (!result.ok && result.mismatch) {
    const proceed = window.confirm(
      'This verdicts file was exported against a different manifest generation. Merge anyway?',
    );
    if (!proceed) return;
    result = importVerdicts(store, data, manifest.generated_at, { force: true });
  }
  if (!result.ok) {
    toast(`Import failed: ${result.error}`);
    return;
  }
  for (const unitId of result.units) syncRowVerdict(unitId);
  updateProgress();
  scheduleAutosave();
  toast(`Imported: ${result.added} added, ${result.replaced} replaced, ${result.keptNewer} kept newer`);
  document.getElementById('import').close();
}

function cancelBlurClose() {
  if (blurTimer !== null) {
    clearTimeout(blurTimer);
    blurTimer = null;
  }
}

function presentational(node) {
  node.setAttribute('role', 'presentation');
  return node;
}

function closeSearch() {
  cancelBlurClose();
  const results = document.getElementById('search-results');
  results.hidden = true;
  results.textContent = '';
  searchActive = -1;
  const input = document.getElementById('unit-search');
  input.setAttribute('aria-expanded', 'false');
  input.removeAttribute('aria-activedescendant');
}

function selectSearchResult(unitId) {
  if (!unitId) return;
  closeSearch();
  document.getElementById('unit-search').blur();
  // The hash names only the unit, the same deep-link form as a junction chip, so applyHashState finds it in any batch or class and shows it even when it takes no verdict.
  const next = `unit=${unitId}`;
  // Selecting the unit already in the hash leaves the hash unchanged and fires no hashchange, so call applyHashState directly to move the cursor and scroll.
  if (location.hash.replace(/^#/, '') === next) applyHashState();
  else location.hash = next;
}

function activeSearchRow() {
  const rows = document.querySelectorAll('#search-results .search-result');
  if (searchActive < 0 || searchActive >= rows.length) return null;
  return rows[searchActive];
}

function selectSearchRow(row) {
  if (!row) return;
  if (row.dataset.units) {
    closeSearch();
    document.getElementById('unit-search').blur();
    // The hash lists the duplicate group's unit ids as a worklist, the same form as the duplicate chip, so the group renders stacked.
    location.hash = `units=${row.dataset.units}`;
    return;
  }
  selectSearchResult(row.dataset.unit);
}

function setSearchActive(index) {
  const rows = document.querySelectorAll('#search-results .search-result');
  if (rows.length === 0) return;
  searchActive = (index + rows.length) % rows.length;
  const input = document.getElementById('unit-search');
  for (const [position, row] of rows.entries()) {
    const current = position === searchActive;
    row.setAttribute('aria-selected', String(current));
    if (current) {
      input.setAttribute('aria-activedescendant', row.id);
      row.scrollIntoView({ block: 'nearest' });
    }
  }
}

function renderSearchResults(query) {
  const results = document.getElementById('search-results');
  const { matches, total } = searchUnits(humanList, query, SEARCH_LIMIT);
  results.textContent = '';
  results.hidden = false;
  const input = document.getElementById('unit-search');
  input.setAttribute('aria-expanded', 'true');
  input.removeAttribute('aria-activedescendant');
  searchActive = -1;
  const group = duplicateGroupOfQuery(query, duplicateIndex);
  if (group) {
    const { id: groupId, members } = group;
    const row = el('button', 'search-result search-group');
    row.type = 'button';
    row.id = `search-opt-${groupId}`;
    row.dataset.units = members.join(',');
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', 'false');
    row.append(el('span', 'search-id', groupId));
    row.append(el('span', 'search-notation', `duplicate group — stack all ${members.length} members as a worklist`));
    results.append(row);
  }
  if (matches.length === 0 && !results.querySelector('.search-result')) {
    results.append(presentational(el('p', 'search-empty', 'No units match.')));
    return;
  }
  for (const unit of matches) {
    const row = el('button', 'search-result');
    row.type = 'button';
    row.id = `search-opt-${unit.id}`;
    row.dataset.unit = unit.id;
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', 'false');
    row.append(el('span', 'search-id', unit.id));
    row.append(el('span', 'search-notation', unit.notation));
    row.append(el('span', 'search-class', unit.class));
    const where = machineCheckOf(unit)
      ? 'machine'
      : unit.no_verdict
        ? 'no verdict'
        : `batch ${unit.batch}`;
    row.append(el('span', 'search-where', where));
    results.append(row);
  }
  const rows = results.querySelectorAll('.search-result');
  for (const [position, row] of rows.entries()) {
    row.setAttribute('aria-setsize', String(rows.length));
    row.setAttribute('aria-posinset', String(position + 1));
  }
  if (total > matches.length) {
    results.append(
      presentational(el('p', 'search-more', `Showing ${matches.length} of ${total} matches — refine to narrow.`)),
    );
  }
}

function updateTypePreview() {
  const input = document.getElementById('type-preview-input');
  const panel = document.getElementById('type-preview-panel');
  const sample = document.getElementById('type-preview-render');
  const hint = document.getElementById('type-preview-hint');
  if (!input.value.trim()) {
    panel.hidden = true;
    sample.textContent = '';
    hint.hidden = true;
    return;
  }
  const { text, unknown } = parsePreview(input.value);
  sample.textContent = text;
  hint.textContent = unknown.length > 0 ? `Unrecognized: ${unknown.join(', ')}` : '';
  hint.hidden = unknown.length === 0;
  panel.hidden = false;
}

async function runSearch() {
  const input = document.getElementById('unit-search');
  const query = input.value;
  if (!query.trim()) {
    closeSearch();
    return;
  }
  if (!indexLoaded) {
    const results = document.getElementById('search-results');
    results.textContent = '';
    results.hidden = false;
    results.append(presentational(el('p', 'search-empty', 'Loading the review index…')));
    await indexReady;
    if (input.value !== query || document.activeElement !== input) return;
  }
  renderSearchResults(query);
}

function wireEvents() {
  const helpDialog = document.getElementById('help');
  const importDialog = document.getElementById('import');

  document.addEventListener('keydown', (event) => {
    const overlayOpen = helpDialog.open || importDialog.open;
    const action = actionForKey(event.key, {
      inInput: isEditableTarget(event.target),
      overlayOpen,
      modified: event.ctrlKey || event.metaKey || event.altKey,
      rejectMenuOpen: rejectMenuUnitId !== null,
      neitherMenuOpen: neitherMenuUnitId !== null,
      noteInput: Boolean(event.target.closest?.('.note')),
      shift: event.shiftKey,
    });
    if (!action) return;
    if (action === 'escape') {
      if (isEditableTarget(event.target)) event.target.blur();
      return;
    }
    if (action === 'note-advance') {
      event.preventDefault();
      const row = event.target.closest('.row');
      event.target.blur();
      if (row) advanceFrom(row.dataset.unit);
      return;
    }
    if (action === 'note-stay') {
      event.preventDefault();
      const row = event.target.closest('.row');
      event.target.blur();
      if (row && row.dataset.unit !== state.unit) setStateReplace({ unit: row.dataset.unit });
      return;
    }
    if (action === 'reject-cancel') {
      event.preventDefault();
      closeRejectMenu();
      return;
    }
    if (action === 'reject-comment') {
      event.preventDefault();
      rejectWithComment();
      return;
    }
    const menuChoice = REJECT_MENU_CHOICES.find((entry) => entry.action === action);
    if (menuChoice) {
      event.preventDefault();
      chooseRejectOption(menuChoice.note);
      return;
    }
    const rejectRecentIndex = recentIndexForAction(action, 'reject-recent-');
    if (rejectRecentIndex !== null) {
      event.preventDefault();
      const note = rejectMenuRecents[rejectRecentIndex];
      if (note !== undefined) chooseRejectOption(note);
      return;
    }
    if (action === 'neither-cancel') {
      event.preventDefault();
      closeNeitherMenu();
      return;
    }
    if (action === 'neither-comment') {
      event.preventDefault();
      neitherWithComment();
      return;
    }
    const neitherChoice = NEITHER_MENU_CHOICES.find((entry) => entry.action === action);
    if (neitherChoice) {
      event.preventDefault();
      chooseNeitherOption(neitherChoice.note);
      return;
    }
    const neitherRecentIndex = recentIndexForAction(action, 'neither-recent-');
    if (neitherRecentIndex !== null) {
      event.preventDefault();
      const note = neitherMenuRecents[neitherRecentIndex];
      if (note !== undefined) chooseNeitherOption(note);
      return;
    }
    event.preventDefault();
    if (action === 'approve' || action === 'either' || action === 'identical' || action === 'skip') {
      verdictCursor(action);
    } else if (action === 'reject') {
      const unitId = cursorUnitId();
      if (unitId) openRejectMenu(unitId);
    } else if (action === 'neither') {
      const unitId = cursorUnitId();
      if (unitId) openNeitherMenu(unitId);
    } else if (action === 'clear-verdict') {
      const unitId = cursorUnitId();
      if (unitId && store.records.has(unitId)) {
        recordVerdict(store, unitId, null);
        syncRowVerdict(unitId);
        updateProgress();
        scheduleAutosave();
        toast(`Cleared ${unitId}`);
      }
    } else if (action === 'undo') {
      undoLast();
    } else if (action === 'repeat') {
      repeatLast();
    } else if (action === 'note') {
      const row = rowFor(cursorUnitId());
      if (row) row.querySelector('.note').focus();
    } else if (action === 'group-approve') {
      const unitId = cursorUnitId();
      if (unitId) approveGroupOf(unitId);
    } else if (action === 'explain') {
      const unitId = cursorUnitId();
      if (unitId) toggleExplain(unitId);
    } else if (action === 'next') {
      moveCursor(1);
    } else if (action === 'prev') {
      moveCursor(-1);
    } else if (action === 'prev-class') {
      shiftClass(-1);
    } else if (action === 'next-class') {
      shiftClass(1);
    } else if (action === 'help') {
      if (helpDialog.open) helpDialog.close();
      else helpDialog.showModal();
    } else if (action === 'search') {
      const input = document.getElementById('unit-search');
      input.focus();
      input.select();
    }
  });

  document.addEventListener(
    'click',
    (event) => {
      if (rejectMenuUnitId === null && neitherMenuUnitId === null) return;
      if (event.target.closest('.reject-menu') || event.target.closest('.neither-menu')) return;
      event.preventDefault();
      event.stopPropagation();
      closeRejectMenu();
      closeNeitherMenu();
    },
    true,
  );

  document.getElementById('batch').addEventListener('click', (event) => {
    const chip = event.target.closest('.junction-chip');
    if (chip) {
      event.preventDefault();
      // The hash names only the primary unit, the same form as a pasted deep link, so applyHashState finds it in any batch or class and shows it even when it takes no verdict.
      if (chip.dataset.primaryUnit) location.hash = `unit=${chip.dataset.primaryUnit}`;
      return;
    }
    const duplicateLink = event.target.closest('.duplicate-chip');
    if (duplicateLink) {
      event.preventDefault();
      // The hash lists the group's unit ids as a worklist, which renders those units stacked and grouped.
      location.hash = duplicateLink.getAttribute('href').replace(/^#/, '');
      return;
    }
    const row = event.target.closest('.row');
    const verdictButton = event.target.closest('.verdict-btn');
    if (verdictButton && row) {
      if (verdictButton.dataset.verdict === 'reject') {
        openRejectMenu(row.dataset.unit);
        return;
      }
      if (verdictButton.dataset.verdict === 'neither') {
        openNeitherMenu(row.dataset.unit);
        return;
      }
      if (applyVerdict(row.dataset.unit, verdictButton.dataset.verdict)) advanceFrom(row.dataset.unit);
      return;
    }
    const clear = event.target.closest('.clear-verdict');
    if (clear && row) {
      recordVerdict(store, row.dataset.unit, null);
      syncRowVerdict(row.dataset.unit);
      updateProgress();
      scheduleAutosave();
      return;
    }
    const repeat = event.target.closest('.repeat-verdict');
    if (repeat && row) {
      repeatLast(row.dataset.unit);
      return;
    }
    const copy = event.target.closest('.copy-unit');
    if (copy && row) {
      const unit = unitFor(row.dataset.unit);
      if (unit) copyToClipboard(copyPreamble(unit), copy);
      else toast(`${row.dataset.unit} is no longer loaded — re-open its fold and copy again.`);
      return;
    }
    const explain = event.target.closest('.explain-toggle');
    if (explain && row) {
      toggleExplain(row.dataset.unit);
      return;
    }
    const approveAll = event.target.closest('.group-approve');
    if (approveAll) {
      event.preventDefault();
      const fold = approveAll.closest('details.group');
      const firstRow = fold.querySelector('.row');
      if (firstRow) approveGroupOf(firstRow.dataset.unit);
      return;
    }
    if (row && !isEditableTarget(event.target)) setStateReplace({ unit: row.dataset.unit });
  });

  document.getElementById('batch').addEventListener('input', (event) => {
    const note = event.target.closest('.note');
    if (!note) return;
    const row = note.closest('.row');
    if (updateNote(store, row.dataset.unit, note.value)) {
      // A note changes no verdict and no count, so only the unexported tally needs updating; a full updateProgress per keystroke would redo work for an unchanged display.
      updateUnexportedNudge();
      scheduleAutosave();
    }
  });

  document.getElementById('class-list').addEventListener('click', (event) => {
    const button = event.target.closest('.class-button');
    if (!button) return;
    const classId = state.class === button.dataset.class ? null : button.dataset.class;
    const batches = availableBatches(manifest, classId);
    setState({ class: classId, batch: batches.length > 0 ? batches[0] : 0, unit: null, group: null });
  });

  for (const [id, key] of [
    ['filter-family', 'family'],
    ['filter-config', 'config'],
    ['filter-status', 'status'],
  ]) {
    document.getElementById(id).addEventListener('change', (event) => {
      setState({ [key]: event.target.value || null, unit: null });
    });
  }
  document.getElementById('clear-filters').addEventListener('click', () => {
    setState({ family: null, config: null, status: null, group: null });
  });
  document.getElementById('show-machine').addEventListener('change', (event) => {
    setState({ machine: event.target.checked ? '1' : null });
  });

  const searchInput = document.getElementById('unit-search');
  const searchResults = document.getElementById('search-results');
  searchInput.addEventListener('focus', () => {
    cancelBlurClose();
    if (searchInput.value.trim()) runSearch();
  });
  searchInput.addEventListener('input', runSearch);
  searchInput.addEventListener('keydown', (event) => {
    // Intercept the arrow keys and Enter only when result rows exist. While the index loads only a placeholder is shown, and the input keeps its native caret movement.
    if (searchResults.hidden || !searchResults.querySelector('.search-result')) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setSearchActive(searchActive + 1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setSearchActive(searchActive - 1);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      selectSearchRow(activeSearchRow() ?? searchResults.querySelector('.search-result'));
    }
  });
  // Close after a short delay on blur so that a mousedown on a result still selects it. Focusing the box again cancels the timer, so a quick reopen is not cleared.
  searchInput.addEventListener('blur', () => {
    blurTimer = setTimeout(closeSearch, 150);
  });
  searchResults.addEventListener('mousedown', (event) => {
    const row = event.target.closest('.search-result');
    if (!row) return;
    event.preventDefault();
    selectSearchRow(row);
  });

  document.getElementById('type-preview-input').addEventListener('input', updateTypePreview);
  document.getElementById('open-queue').addEventListener('click', () => {
    syncVerdictsFromServer();
    const next = 'view=queue';
    // Clicking Queue while in the queue view leaves the hash unchanged and fires no hashchange, so call applyHashState directly, which also refreshes the view.
    if (location.hash.replace(/^#/, '') === next) applyHashState();
    else location.hash = next;
  });
  document.getElementById('jump-unverdicted').addEventListener('click', jumpToFirstUnverdicted);
  document.getElementById('prev-batch').addEventListener('click', () => shiftBatch(-1));
  document.getElementById('next-batch').addEventListener('click', () => shiftBatch(1));
  document.getElementById('open-help').addEventListener('click', () => helpDialog.showModal());
  document.getElementById('open-import').addEventListener('click', () => importDialog.showModal());
  document.getElementById('download-verdicts').addEventListener('click', downloadVerdicts);

  document.getElementById('do-import').addEventListener('click', async () => {
    const fileInput = document.getElementById('import-file');
    if (fileInput.files.length > 0) runImport(await fileInput.files[0].text());
    else toast('Choose a file first');
  });

  window.addEventListener('hashchange', () => {
    state = withDefaults(parseHash(location.hash));
    applyHashState(true);
    refreshStatus();
  });

  window.addEventListener('focus', () => {
    syncVerdictsFromServer();
    refreshStatus();
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      syncVerdictsFromServer();
      refreshStatus();
    }
  });

  // A queue view open beside the judging tab never regains focus between decisions, so the focus re-merge does not update it. While the queue is visible, poll the server store instead. syncVerdictsFromServer limits the rate, and each pickup re-derives the queue, so judged decisions leave the page without a tab switch.
  setInterval(() => {
    if (state.view === 'queue' && !document.hidden) syncVerdictsFromServer();
  }, 3000);

  window.addEventListener('beforeunload', (event) => {
    if (store.unexported.size === 0) return;
    if (autosaveHealthy() || outboxWorks) return;
    event.preventDefault();
    event.returnValue = '';
  });

  setInterval(keepTabAlive, TAB_HEARTBEAT_MS);
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) keepTabAlive();
  });

  window.addEventListener('pagehide', () => {
    clearTimeout(autosaveTimer);
    autosaveTimer = null;
    mirrorDirtyToOutbox();
    if (outboxStorage) markTabClosed(outboxStorage, TAB_ID);
    if (store.dirty.size === 0) return;
    navigator.sendBeacon('autosave', new Blob([deltaPayload(takeDirty())], { type: 'application/json' }));
  });
}

function renderChrome() {
  document.getElementById('build-command').textContent = manifest.build_command ?? '';
  document.getElementById('serve-command').textContent = manifest.serve_command ?? '';
  const machine = manifest.machine_approved;
  const exempt = noVerdictTotal(manifest);
  document.getElementById('corpus-total').textContent = corpusChipLabel(manifest);
  document.getElementById('manifest-meta').textContent =
    `Mode ${manifest.mode}, generated ${manifest.generated_at} at ${manifest.repo_head}; ` +
    `${formatCount(manifest.totals.units)} units on the corpus — ` +
    `${formatCount(machine?.units ?? 0)} machine-approved` +
    `${exempt ? `, ${formatCount(exempt)} in no-verdict classes` : ''}, and ` +
    `${formatCount(humanTotal(manifest))} human units in ${manifest.totals.batches} batches — ` +
    `covering ${formatCount(manifest.totals.rows)} rows.`;
  const stamp = document.getElementById('corpus-stamp');
  const stampText = corpusStampLine(manifest);
  stamp.textContent = stampText ?? '';
  stamp.hidden = stampText === null;
  const alphabetLabel = corpusAlphabetLabel(manifest);
  const alphabet = document.createElement('td');
  if (alphabetLabel !== null) {
    alphabet.textContent = alphabetLabel;
    alphabet.title = 'Letters migrated to the rebuild engine, against the whole Quikscript alphabet — the corpus is built over the migrated ones.';
  }
  const unitsHeading = document.createElement('th');
  unitsHeading.scope = 'col';
  unitsHeading.textContent = 'units';
  const headRow = document.createElement('tr');
  headRow.append(alphabet, unitsHeading);
  const head = document.createElement('thead');
  head.append(headRow);
  const body = document.createElement('tbody');
  for (const row of corpusDetailRows(manifest)) {
    const line = document.createElement('tr');
    const term = document.createElement('th');
    const value = document.createElement('td');
    term.scope = 'row';
    term.textContent = row.label;
    value.textContent = row.value;
    if (row.sub) line.className = 'sub';
    if (row.title) line.title = row.title;
    line.append(term, value);
    body.append(line);
  }
  document.getElementById('corpus-detail-rows').replaceChildren(head, body);
}

renderChrome();
renderSidebar();
keepTabAlive();
wireEvents();
indexReady = loadHumanIndex();
await restoreAutosave();
bootRestoreDone = true;
await indexReady;
applyHashState(true);
if (replayQueue.length > 0) flushAutosave();
refreshStatus();
