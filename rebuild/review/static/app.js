import { parseHash, writeHash, shedWorklist } from './state.js';
import { actionForKey, isEditableTarget } from './keyboard.js';
import { parsePreview } from './preview.js';
import { bannerModel } from './status.js';
import {
  createStore,
  recordVerdict,
  recordVerdictWithEchoes,
  updateNote,
  groupApprove,
  undo,
  assembleExport,
  markExported,
  importVerdicts,
  verdictCounts,
  recentNotes,
} from './verdicts.js';
import {
  configGateChips,
  configFilterOptions,
  pinStylisticSetScope,
  explainRuns,
  renderGroupsOf,
  highlightRect,
  pairBand,
  markOffset,
  secondarySeamsOf,
  seamChip,
  onlyHereSeamSpans,
  tokenMarkRuns,
  echoChip,
  echoFillTargets,
  needsNoVerdict,
  familiesOfGroup,
  unitMatchesFilters,
  unitWorklist,
  orderWorklist,
  partitionUnits,
  humanClassCount,
  humanTotal,
  noVerdictTotal,
  NO_VERDICT_BADGE,
  formatCount,
  surfaceChipLabel,
  surfaceAlphabetLabel,
  surfaceStampLine,
  surfaceDetailRows,
  classCountsLine,
  nextUnverdictedIndex,
  stepIndex,
  availableBatches,
  classesInBatch,
  copyPreamble,
  tokenSeparators,
  searchUnits,
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
  SINGLETON_CHUNK,
  buildClusters,
  docketResumeAction,
  docketTotals,
  echoConflicts,
  nextDocketDecision,
  partitionClusters,
  queueCounts,
  ruledClassIds,
  singletonChunks,
} from './docket.js';

const FONT_SIZE = 88;
const VERDICT_LABELS = [
  ['skip', 'Skip', 'a', 'Skip — record no verdict and advance'],
  ['reject', 'Reject', 's', 'Reject — want the old behavior back (opens a follow-up choice)'],
  ['identical', 'Identical', 'e', 'The highlighted portion looks identical'],
  ['approve', 'Approve', 'f', 'Approve — the new behavior is right'],
  ['either', 'Either', 'd', 'Fine either way (any-of channel)'],
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
// The only queue-scaled retention in the tab: one slim row per unit awaiting a verdict, holding what the docket, search, filters and progress read across the whole queue. What a card draws — the sample text, the pair band, the settled cells — is Range-fetched from the shard record as the card renders, and the explain table when its panel opens, through fullRecords, which is bounded. Machine-approved and no-verdict units are never resident as a class: a show-machine fold reads its class's locator rows one block at a time and draws them a window at a time, keeping the records of the rows on screen (foldRecords, dropped with the view), a worklist keeps its own machine records while it is the view (worklist.records), and a deep link keeps the one unit it revealed (transientMachineUnit). The locator's block table is the one thing retained on the machine side, and it is the machine workload divided by the block size.
const humanRows = new Map();
const humanList = [];
const rowsByClass = new Map();
const echoIndex = new Map();
const fullRecords = createRecordCache();
const foldRecords = new Map();
const locatorBlocks = createRecordCache(BLOCK_CACHE_CAP);
let locatorReady = null;
let familyOptions = [];
let worklist = null;
let indexReady = null;
let indexLoaded = false;
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

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
  'Machine-approved or in a no-verdict class: the build wrote no candidate table, no drafts and no pair band for this unit, since nothing here is for a reviewer to act on — the settled cells and seams above are the whole of what it carries.';

function machineChannelOf(unit) {
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

const MACHINE_CHANNEL_BADGES = {
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
  // Through rebuild.review.serve the sidecar arrives already decoded, because that handler declares Content-Encoding: gzip; through anything else — an archived surface under a plain static file server — it arrives as the gzip bytes sitting on disk. The magic number is what says which happened, so the surface reads the same either way.
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

// The whole boot load: one streaming pass over the slim index, parsed a line at a time so the tab never holds the source text and the rows at once. Everything downstream — docket, search, worklists, progress, filters, echo chips — reads these rows and nothing else.
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
      if (row.echo) {
        if (!echoIndex.has(row.echo)) echoIndex.set(row.echo, []);
        echoIndex.get(row.echo).push(row.id);
      }
      for (const family of familiesOfGroup(row.group)) families.add(family);
    }
    // A truncated index yields no lines at all, so the header check above never runs. Refusing it here is what keeps an interrupted build from booting as a clean, fully-verdicted-looking corpus.
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

// The locator's block table: which gzip member of the rows file holds which class's rows and which unit numbers, loaded once on first need. A fold reads its class's blocks in order, a deep link binary-searches every class's blocks for the one that can hold its id, and neither reads a row outside the block it asked for.
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

// A Range response's body as text. A block of the rows file arrives as its own gzip member's bytes — the file goes out identity-encoded, since Chrome refuses a partial response that declares a content encoding — and is decompressed here; the magic number is what says so, in case a server decoded it anyway.
async function readMaybeGzipped(response) {
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (!looksGzipped(bytes) || typeof DecompressionStream !== 'function') return new TextDecoder().decode(bytes);
  return new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
}

// One block of locator rows, fetched by the span the table names and held in a small cache so a fold's next window and a deep link's neighbors read it back without another request.
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
  // The table and the rows file land together, so a block that does not start where the table says is a rows file this table was not written for.
  if (rows.length !== block.units || rows[0]?.id !== block.first) {
    toast(`The ${block.class} locator rows are not where this page was told they would be — the surface was rebuilt; reload.`);
    return null;
  }
  locatorBlocks.set(key, rows);
  return rows;
}

// The addresses of machine-approved or no-verdict units, for a deep link or a worklist: each id names at most one candidate block per class, those blocks are fetched once each, and only the rows asked for are kept.
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

// The shard records behind a set of addresses, as few Range requests as their spans allow: neighbors in a part share one request, and each record is sliced out of the returned text at its own span. Every record read is cached, so a card's fetch also serves the explain panel that opens on it.
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
        // A surface rebuilt under this tab renumbers its units, so a stale span can land on a neighboring record rather than on nothing; the id is what says which happened.
        if (!record || record.id !== row.id) {
          toast(`${row.id} is not where this page was told it would be — the surface was rebuilt; reload.`);
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

// A worklist resolves once and stays resolved for as long as it is the view. Resolving a machine id is a locator block per candidate class and a shard fetch per unit, so re-deriving the list per call would put those requests behind every cursor move and every verdict — applyHashState runs on all of them. Pinning the records it found does the second job too: a worklist longer than the record cache's cap would otherwise evict its own earlier units, and a machine unit the app cannot look up is one it can neither cursor to nor copy.
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
  // Manifest-class order, not index order: the batch's rows arrive grouped exactly as they did when each class's shard was concatenated in turn, so the group folds and the default cursor land where they always did. The class selection is the same one too, which is what keeps a walk over the batches from touching every class's rows on every step.
  const units = [];
  for (const cls of manifest.classes) {
    if (classFilter) {
      if (cls.id !== classFilter) continue;
    } else if (!cls.batches.includes(batch)) continue;
    for (const row of rowsByClass.get(cls.id) ?? []) if (row.batch === batch) units.push(row);
  }
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

// One side's sample cell. A row out of the app index has no text to draw yet — the cell is built empty and hydrateSamples rebuilds it from the record, which is why the cell remembers its side and its feature settings.
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
  for (const seam of secondarySeamsOf(unit)) {
    if (!seam[side]) continue;
    const rect = highlightRect(seam[side], FONT_SIZE, upem);
    const band = el('span', 'secondary-band');
    band.style.left = `${rect.left}px`;
    band.style.width = `${rect.width}px`;
    const chip = seamChip(seam);
    const node = el(chip.home ? 'a' : 'span', 'seam-chip', chip.label);
    if (chip.home) {
      node.href = `#unit=${chip.home}`;
      node.dataset.home = chip.home;
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

const SEAM_MARK_TITLE =
  'This dashed underline is the secondary divergent seam judged only in this unit — the text-line twin of the sample band’s “only here” chip.';

function appendMarkedTokens(node, tokens, separators, unit) {
  for (const run of tokenMarkRuns(tokens, separators, unit.pair_codepoints, onlyHereSeamSpans(unit))) {
    if (!run.pair && !run.seam) {
      node.append(document.createTextNode(run.text));
      continue;
    }
    let mark = null;
    if (run.seam) {
      mark = el('span', 'seam-mark', run.text);
      mark.title = SEAM_MARK_TITLE;
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

// A card built from a slim row draws its label at once and its samples once the unit's record arrives — one Range request against the class shard, the same one the explain panel makes. The marked runs on the text lines come back with the record too, since the seam underline reads the settled cells.
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
  const channel = machineChannelOf(unit);
  const exemptTitle = channel?.title ?? NO_VERDICT_TITLE;
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
  if (channel) {
    const badge = el('span', 'machine-badge', channel.badge);
    badge.title = channel.title;
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
  const echo = echoChip(unit, echoIndex.get(unit.echo) ?? []);
  if (echo) {
    const chip = el('a', 'echo-chip', echo.label);
    chip.href = echo.href;
    chip.title = echo.title;
    chip.dataset.echo = unit.echo;
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

function buildDocketContext(units) {
  const strip = el('aside', 'docket-context');
  const back = el('a', 'open-app', 'Docket ↩');
  back.href = '#view=docket';
  back.title = 'Back to the full docket view; finishing this worklist advances to the next decision by itself.';
  const clusterIds = new Set();
  for (const unit of units) if (typeof unit.cluster === 'string') clusterIds.add(unit.cluster);
  if (clusterIds.size === 1) {
    const clusterId = [...clusterIds][0];
    const cluster = buildClusters(humanList, (id) => store.records.get(id)).find((entry) => entry.id === clusterId);
    if (cluster) {
      const line = el('p', 'docket-context-line');
      line.append(el('strong', null, 'Docket decision'));
      line.append(
        document.createTextNode(
          ` — one verdict per echo group covers all ${formatCount(cluster.size)} lookalike units of `,
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
  const line = el('p', 'docket-context-line');
  const singles = clusterIds.size > 1;
  line.append(el('strong', null, singles ? 'Docket singletons' : 'Docket worklist'));
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
  // A provisional plan cannot say whether its folds hold anything under this filter, so the queue answers for itself rather than leaving a filter that matched nothing looking like a view full of units.
  if (units.length === 0 && machine.length === 0 && plan.some((fold) => fold.provisional)) {
    container.append(el('p', 'empty', 'No units awaiting a verdict match the current batch and filters.'));
  }
  if (state.units && state.docket) container.append(buildDocketContext(units));
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
  // The filters a fold applies once it is opened, frozen at render time the way partitionUnits froze them; the status filter never reaches a unit that takes no verdict.
  const foldFilters = { ...state, status: null };
  for (const fold of plan) {
    const pinned = machine.filter((unit) => unit.class === fold.classId);
    container.append(
      buildMachineFold(fold.classId, fold.total, MACHINE_CHANNEL_BADGES[fold.channel], null, pinned, foldFilters, {
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
  // The class's rows come off the locator a block at a time and its records off the shard a window at a time, so opening the fold costs one window whatever the class holds, and every further window is asked for. The filters a fold applies are per record, so under one a window shows the rows it read that match and says how many it has read.
  const pinnedIds = new Set(pinned.map((unit) => unit.id));
  const more = el('button', 'fold-more');
  more.type = 'button';
  let blocks = null;
  let classRows = 0;
  let cursor = { block: 0, row: 0 };
  let read = 0;
  let shown = 0;
  let loading = null;
  // The count line speaks of the windows alone: a pinned row is the unit the reader deep-linked to, drawn ahead of the windows whether or not its window has been read yet.
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
    const fetched = await fetchRecordsBySpans(rows.filter((row) => !pinnedIds.has(row.id)));
    const units = [];
    for (const row of rows) {
      const unit = fetched.get(row.id);
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
    // A machine-approved unit can hold the URL cursor for deep links, but it is never the verdict cursor: keys and auto-advance operate over the human workload only.
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
      // Deep-linking to a machine-approved or no-verdict unit reveals just that unit transiently; the persistent toggle stays off and any navigation away hides it again. The record is held here rather than left to the record cache, which the cards' own fetches churn through.
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

// Every write here is guarded by a read: the counts are unchanged for most of the calls that reach this function, and an unconditional textContent assignment would dirty layout across the whole batch grid anyway.
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

// One walk of the queue, not one per class button: the sidebar tally and the selected-class line ask the same question of the same rows, and answering it separately for each of the two dozen classes made every store mutation quadratic in the queue.
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
  if (state.view === 'docket') {
    // renderDocket owns the batch-progress line in the docket view; a store mutation here (undo, import, autosave restore) just re-derives the queue.
    scheduleDocketRefresh();
  } else {
    let batchVerdicted = 0;
    for (const unit of visibleUnits) if (store.records.has(unit.id)) batchVerdicted += 1;
    let line;
    if (state.units && state.docket) {
      const queue = queueCounts(humanList, (id) => store.records.get(id), ruledClassIds(manifest.classes));
      line =
        `Docket decision: ${batchVerdicted}/${visibleUnits.length} · ` +
        `queue: ${formatCount(queue.blankUnits)} blank in ${formatCount(queue.clusters)} clusters`;
    } else if (state.units) {
      line = `Worklist: ${batchVerdicted}/${visibleUnits.length}`;
    } else {
      line = `Batch ${state.batch}: ${batchVerdicted}/${visibleUnits.length}`;
    }
    setText(document.getElementById('batch-progress'), line);
  }
  updateUnexportedNudge();
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
  if (state.view === 'docket') {
    document.title = 'Docket — AMS review';
    return;
  }
  const unitId = cursorUnitId();
  if (state.units) {
    document.title = `${unitId ?? '—'} · ${state.docket ? 'docket' : 'worklist'} — AMS review`;
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

let docketRefreshTimer = null;

function scheduleDocketRefresh() {
  clearTimeout(docketRefreshTimer);
  docketRefreshTimer = setTimeout(() => {
    docketRefreshTimer = null;
    if (state.view === 'docket') renderDocket({ anchor: captureDocketAnchor() });
  }, 150);
}

// A live refresh drops every newly judged cluster, so restoring the old pixel offset would slide the page under the reader by the height of whatever vanished above them; anchoring to the first card still in view keeps the card being read in place while the judged ones melt away around it.
function captureDocketAnchor() {
  const docket = document.getElementById('docket');
  if (docket.hidden) return null;
  for (const card of docket.querySelectorAll('article.cluster')) {
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

// Docket-launched worklists carry the flag that turns worklist exhaustion into an auto-advance to the next docket decision, plus the surface stamp that lets a resumed tab notice the ids in its hash were minted for an earlier build (see docketResumeAction); conflict stacks stay plain because settling one means changing existing verdicts, not filling blanks.
function docketWorklistHref(unitIds) {
  return `${worklistHref(unitIds)}&docket=1&stamp=${encodeURIComponent(manifest.generated_at)}`;
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
  header.append(el('span', null, `in ${cluster.echoGroups.length} echo group${cluster.echoGroups.length === 1 ? '' : 's'}`));
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
  const reps = el('p', 'reps');
  reps.append(
    appButton(docketWorklistHref(cluster.reps), `Judge ${cluster.reps.length} rep${cluster.reps.length === 1 ? '' : 's'}`),
  );
  reps.append(
    el('span', 'note', ` — one per echo group; each verdict echo-fills its group, covering all ${cluster.size} units.`),
  );
  card.append(reps);
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
  const section = el('section', 'docket-later');
  let laterUnits = 0;
  for (const cluster of later) laterUnits += cluster.size;
  section.append(el('h2', null, `Later tranches — ${later.length} smaller clusters, ${formatCount(laterUnits)} units`));
  const details = el('details');
  details.append(el('summary', null, 'Compact list — clusters promote into the tranche above as it clears'));
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
    judge.append(appButton(docketWorklistHref(cluster.reps), `Judge ${cluster.reps.length}`));
    row.append(judge);
    body.append(row);
  }
  table.append(body);
  details.append(table);
  section.append(details);
  return section;
}

function buildSingletonSection(singletons) {
  const section = el('section', 'docket-singletons');
  section.append(el('h2', null, `Singletons — ${singletons.length} one-off units`));
  const links = el('p', 'chunk-links', `Work them as app worklists, ${SINGLETON_CHUNK} at a time: `);
  for (const chunk of singletonChunks(singletons)) {
    links.append(appButton(docketWorklistHref(chunk.unitIds), `Judge ${chunk.start}–${chunk.end}`));
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
  const section = el('section', 'docket-conflicts');
  section.append(el('h2', null, `Echo groups with disagreeing verdicts (${conflicts.length})`));
  section.append(
    el('p', 'docket-note', 'The same visual change judged differently across contexts — worth a re-check when convenient.'),
  );
  for (const conflict of conflicts) {
    const card = el('article', 'conflict');
    const header = el('header');
    header.append(el('span', 'chip', conflict.echo));
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

function renderDocket({ anchor = null } = {}) {
  const container = document.getElementById('docket');
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
        'docket-note',
        `This surface predates cluster signatures — rebuild it with ${manifest.build_command ?? 'uv run python -m rebuild.review.build'} to use the docket view.`,
      ),
    );
    return;
  }
  const recordOf = (id) => store.records.get(id);
  const clusters = buildClusters(humanList, recordOf);
  const ruledIds = ruledClassIds(manifest.classes);
  const { tranche, later, singletons, ruledBlankUnits } = partitionClusters(clusters, ruledIds);
  const conflicts = echoConflicts(echoIndex, humanRows, recordOf);
  // The headline counts cover the workable queue — the note below already presents the ledger-ruled blanks as excluded from it.
  const totals = docketTotals(clusters.filter((cluster) => !ruledIds.has(cluster.class)));

  const header = el('header', 'docket-header');
  header.append(el('h2', null, 'Docket'));
  header.append(
    el(
      'p',
      'docket-provenance',
      `${formatCount(totals.blankUnits)} blank units in ${formatCount(totals.echoGroups)} echo groups → ` +
        `${formatCount(totals.clusters)} clusters (${formatCount(totals.multiClusters)} multi-unit, ` +
        `${formatCount(totals.singletonClusters)} singleton), live against the current verdicts.`,
    ),
  );
  if (ruledBlankUnits > 0) {
    header.append(
      el(
        'p',
        'docket-note',
        `${formatCount(ruledBlankUnits)} more blank units sit in ledger-ruled classes and are excluded here — ` +
          'one class-level decision (or a bulk-proposal import) covers each; reach them from the sidebar with status “unverdicted”.',
      ),
    );
  }
  header.append(
    el(
      'p',
      'docket-note',
      'Every button stacks a decision as a worklist — judge there with the keyboard flow; echo-fill multiplies each verdict, and this queue recomputes as verdicts land.',
    ),
  );
  container.append(header);
  renderDocketReadiness();

  if (totals.clusters === 0) container.append(el('p', 'docket-note', 'No blank units — the queue is clear.'));

  if (tranche.length > 0) {
    const section = el('section', 'docket-tranche');
    let trancheUnits = 0;
    for (const cluster of tranche) trancheUnits += cluster.size;
    section.append(
      el('h2', null, `This tranche — top ${tranche.length} cluster decisions, ${formatCount(trancheUnits)} units`),
    );
    for (const [index, cluster] of tranche.entries()) section.append(buildClusterCard(cluster, index + 1));
    container.append(section);
  }
  if (later.length > 0) container.append(buildLaterSection(later));
  if (singletons.length > 0) container.append(buildSingletonSection(singletons));
  if (conflicts.length > 0) container.append(buildConflictSection(conflicts));

  document.getElementById('batch-progress').textContent =
    `Docket: ${formatCount(totals.blankUnits)} blank in ${formatCount(totals.clusters)} clusters`;
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
  // Safari's native select popup doesn't surface per-option tooltips, so the closed control carries the selected set's gloss too.
  configSelect.title = configSelect.selectedOptions[0]?.title ?? '';
  document.getElementById('filter-status').value = state.status ?? '';
  document.getElementById('show-machine').checked = state.machine === '1';
}

async function applyHashState(resume = false) {
  const token = (renderToken += 1);
  const docketView = state.view === 'docket';
  document.body.classList.toggle('docket-view', docketView);
  document.getElementById('docket').hidden = !docketView;
  if (docketView) {
    await indexReady;
    if (token !== renderToken) return;
    closeRejectMenu();
    closeNeitherMenu();
    visibleUnits = [];
    machineUnits = [];
    foldRecords.clear();
    renderedKey = null;
    renderDocket();
    updateProgress();
    updateTitle();
    updateSidebarHighlights();
    return;
  }
  // A docket worklist that arrives finished or stamped for another surface never renders — it restacks from the live queue instead, so a tab resumed after a rebuild (whose hash names ids the new surface reassigned to unrelated units) or reopened on an already-judged screenful lands on the next docket decision, not a dead or misdirected list. Gated on resume (boot and hashchange) so an in-view adjustment — a cursor move, Shift+Enter's deliberate stay, an undo — can never yank a worklist the reviewer is still looking at.
  if (resume && state.units && state.docket) {
    const action = docketResumeAction({
      stamp: state.stamp,
      manifestStamp: manifest.generated_at,
      unitIds: unitWorklist(state.units),
      recordOf: (id) => store.records.get(id),
    });
    if (action) {
      // The cold-boot index load can take a while; a navigation that lands meanwhile owns the view, and the restack yields to it instead of clobbering it.
      await indexReady;
      if (token !== renderToken) return;
      await advanceDocket({ stale: action === 'restack' });
      return;
    }
  }
  const units = await unitsForView(state.batch, state.class);
  if (token !== renderToken) return;
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
    state.docket,
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
// Reading time, not a flat timeout: each digit adds half a word because numerals ("3,193", "e-0061") are read digit-by-digit, slower than a word of the same length.
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
    // A verdict fills the unverdicted rest of the unit's echo group (same change, same judged pair — one question); skip is a per-unit deferral and never echoes, and already-verdicted members are never overwritten.
    const unit = unitFor(unitId);
    const echoes =
      verdict === 'skip' || !unit
        ? []
        : echoFillTargets(unit, echoIndex.get(unit.echo) ?? [], (id) => store.records.has(id));
    const applied = recordVerdictWithEchoes(store, unitId, verdict, echoes, { note: noteValue });
    for (const id of applied) syncRowVerdict(id);
    if (applied.length > 1) {
      toast(`Echoed to ${applied.length - 1} matching window${applied.length === 2 ? '' : 's'} (u to undo all)`);
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
    if (state.docket) {
      await advanceDocket();
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

// The docket flow's analog of the cross-batch advance: when a docket worklist is fully judged, stack the next decision straight away — the queue recomputes from the live store, so "next" is always the docket view's own top card. Replaces (not pushes) history so Back still returns to the docket in one step.
async function advanceDocket({ stale = false } = {}) {
  await indexReady;
  const recordOf = (id) => store.records.get(id);
  const decision = nextDocketDecision(humanList, recordOf, ruledClassIds(manifest.classes));
  const lead = stale ? 'That worklist was stacked for an earlier surface' : 'Decision done';
  if (!decision) {
    toast(`${stale ? `${lead}, and the` : 'The'} docket queue is clear`);
    setState({ units: null, order: null, docket: null, stamp: null, unit: null, view: 'docket' });
    return;
  }
  const queue = queueCounts(humanList, recordOf, ruledClassIds(manifest.classes));
  const what =
    decision.kind === 'cluster'
      ? `${formatCount(decision.cluster.size)} lookalike unit${decision.cluster.size === 1 ? '' : 's'} in ${decision.cluster.class}`
      : `${decision.unitIds.length} singletons`;
  toast(`${lead} — next: ${what} (${formatCount(queue.blankUnits)} blank left)`);
  setStateReplace({
    units: decision.unitIds.join(','),
    docket: '1',
    stamp: manifest.generated_at,
    unit: decision.unitIds[0],
    view: null,
  });
}

function verdictCursor(verdict) {
  const unitId = cursorUnitId();
  if (!unitId) return;
  if (applyVerdict(unitId, verdict)) advanceFrom(unitId);
}

let lastVerdictedUnitId = null;

// Repeat reads the source unit's live record rather than a snapshot, so a note typed or edited after the verdict landed is what gets repeated; it never toggle-clears, so hammering r across a run of identical screwups is safe.
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
      if (state.docket) {
        await advanceDocket();
        return;
      }
      toast('Everything in this worklist is verdicted');
      return;
    }
    setStateReplace({ unit: open.id, view: null });
    return;
  }
  const fromDocket = state.view === 'docket';
  for (const batch of availableBatches(manifest, null)) {
    const units = await unitsForView(batch, null);
    const { human } = partitionUnits(units, { ...state, class: null, batch }, (id) => store.records.get(id));
    const open = human.find((unit) => !store.records.has(unit.id));
    if (!open) continue;
    const keepClass = state.class && open.class === state.class ? state.class : null;
    if (state.class && !keepClass) toast(`First unverdicted is in ${open.class} — class filter cleared`);
    if (!fromDocket && batch === state.batch && keepClass === state.class) {
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

// The keys 1–9 then 0 pick from this sitting's ten most recent distinct comments of the menu's own verdict kind, so a repeated objection is typed once and reused until it ages off the list.
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
  // Each approval echoes to the rest of its echo group, wherever those windows live; groupApprove skips anything already verdicted, so duplicates in the list are harmless.
  const expanded = [...ids];
  for (const id of ids) {
    const member = humanRows.get(id);
    if (member?.echo) expanded.push(...(echoIndex.get(member.echo) ?? []));
  }
  const applied = groupApprove(store, expanded);
  for (const id of applied) syncRowVerdict(id);
  if (applied.length > 0) lastVerdictedUnitId = applied[0];
  updateProgress();
  scheduleAutosave();
  const inGroup = applied.filter((id) => ids.includes(id)).length;
  const echoed = applied.length - inGroup;
  toast(`Approved ${inGroup} remaining in ${unit.group}${echoed > 0 ? ` + ${echoed} echoes elsewhere` : ''}`);
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

// A slim row carries no explain material, so opening the panel is where its shard record gets read back — one Range request against the class shard, or none when the card's own fetch of the same record is still in the cache. Machine rows are built from their shard record — a slim fragment, whose panel fills with the note saying what the build left out — and open with the panel already filled, as they always did.
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
  // A read that failed left its line behind for the reader to see; this open replaces it rather than stacking another one under it.
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

const AUTOSAVE_DEBOUNCE_MS = 800;
let autosaveTimer = null;
let autosaveWorks = false;
let autosaveFailed = false;

function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(flushAutosave, AUTOSAVE_DEBOUNCE_MS);
}

async function flushAutosave() {
  clearTimeout(autosaveTimer);
  autosaveTimer = null;
  try {
    const response = await fetch('autosave', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: exportPayload(),
    });
    if (response.status === 409) {
      if (!autosaveFailed) toast('Autosave refused: this tab is from an older surface — reload to continue');
      autosaveFailed = true;
      updateProgress();
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    autosaveWorks = true;
    autosaveFailed = false;
  } catch (error) {
    console.warn('autosave failed', error);
    if (!autosaveFailed) toast('Autosave failed — download verdicts.json to be safe');
    autosaveFailed = true;
  }
  updateProgress();
}

function autosaveHealthy() {
  return autosaveWorks && !autosaveFailed;
}

async function restoreAutosave() {
  let data = null;
  try {
    const response = await fetch('autosave');
    if (response.status === 404) {
      autosaveWorks = true;
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    data = await response.json();
  } catch (error) {
    console.warn('autosave restore failed', error);
    return;
  }
  autosaveWorks = true;
  const result = importVerdicts(store, data, manifest.generated_at);
  if (!result.ok) {
    if (result.mismatch) {
      toast(
        `Found an autosave from a different surface build (${data.verdicts.length} verdicts) — not restored; it'll be stashed aside on your next verdict`,
      );
    }
    return;
  }
  markExported(store);
  if (result.added > 0) toast(`Restored ${result.added} autosaved verdicts`);
}

// The store is this page's memory alone, restored from the server only at boot — so a docket left open in another tab or window goes stale as verdicts land elsewhere, and its next autosave POST would clobber them. Re-merging the server file whenever the page regains focus keeps every open copy of the app fresh (newer `at` wins, exactly the import rule) and folds the other session's verdicts into this page's next POST. Cleared verdicts are absent from the file rather than tombstoned, so a clear never propagates across tabs — another still-open copy can resurrect it; clearing is rare and visible, so that trade is fine.
let verdictSyncInFlight = false;
let verdictSyncLastAt = 0;
let bootRestoreDone = false;

async function syncVerdictsFromServer() {
  if (!bootRestoreDone || verdictSyncInFlight || Date.now() - verdictSyncLastAt < 2000) return;
  verdictSyncInFlight = true;
  try {
    const response = await fetch('autosave');
    if (!response.ok) return;
    const data = await response.json();
    const result = importVerdicts(store, data, manifest.generated_at);
    if (!result.ok || result.units.length === 0) return;
    for (const id of result.units) {
      store.unexported.delete(id);
      syncRowVerdict(id);
    }
    updateProgress();
    toast(`Picked up ${result.units.length} verdict${result.units.length === 1 ? '' : 's'} from another session`);
  } catch (error) {
    console.warn('verdict sync failed', error);
  } finally {
    verdictSyncInFlight = false;
    verdictSyncLastAt = Date.now();
  }
}

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

function renderDocketReadiness() {
  const header = document.querySelector('#docket .docket-header');
  if (!header) return;
  const existing = header.querySelector('.docket-readiness');
  if (existing) existing.remove();
  if (!lastStatusModel || lastStatusModel.level === 'ready') return;
  header.append(el('p', `docket-readiness readiness-${lastStatusModel.level}`, readinessLine(lastStatusModel)));
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
    lastStatusModel = bannerModel(payload, manifest.generated_at);
    renderReadinessBanner();
    if (state.view === 'docket') renderDocketReadiness();
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
  // The hash carries only the unit id — the same deep-link form as a seam chip — so the existing machinery relocates across batches and classes and transiently reveals a machine-approved home.
  const next = `unit=${unitId}`;
  // Re-selecting the unit you're already deep-linked to leaves the hash byte-identical, so the browser fires no hashchange; re-resolve directly so the row still re-cursors and re-scrolls.
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
    // The hash carries the group's unit ids as a worklist — the same form as the echo chip — so the group renders stacked.
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
  const groupId = query.trim().toLowerCase();
  if (/^e-\d{4}$/.test(groupId) && echoIndex.has(groupId)) {
    const members = echoIndex.get(groupId);
    const row = el('button', 'search-result search-group');
    row.type = 'button';
    row.id = `search-opt-${groupId}`;
    row.dataset.units = members.join(',');
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', 'false');
    row.append(el('span', 'search-id', groupId));
    row.append(el('span', 'search-notation', `echo group — stack all ${members.length} members as a worklist`));
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
    const where = machineChannelOf(unit)
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
    const chip = event.target.closest('.seam-chip');
    if (chip) {
      event.preventDefault();
      // The hash carries only the home unit id — the same deep-link form as a pasted URL — so the existing machinery relocates across batches and classes, or transiently reveals a machine-approved home.
      if (chip.dataset.home) location.hash = `unit=${chip.dataset.home}`;
      return;
    }
    const echoLink = event.target.closest('.echo-chip');
    if (echoLink) {
      event.preventDefault();
      // The hash carries the group's unit ids as a worklist — the existing #units= machinery renders exactly those units, stacked and grouped.
      location.hash = echoLink.getAttribute('href').replace(/^#/, '');
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
      // A fold's records stay resident while its rows are on screen, so a row without one behind it is one from a view that has since re-rendered; re-opening its fold brings it back.
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
      // Note text moves no verdict and no count, so the only progress readout it can change is the unexported tally — the full sweep would be per-keystroke work for an unchanged display.
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
    // Only intercept navigation when real result rows exist — during the index load only the placeholder is shown, so let the browser keep native caret movement.
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
  // Hide on blur after a beat so a result's mousedown still registers as a selection; the timer is cancelled if the box is re-focused first (so a fast reopen isn't blanked).
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
  document.getElementById('open-docket').addEventListener('click', () => {
    syncVerdictsFromServer();
    const next = 'view=docket';
    // Re-clicking while already in the docket leaves the hash byte-identical (no hashchange), so re-resolve directly — a free manual refresh.
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

  // A docket kept open beside the judging tab never regains focus between decisions, so the focus re-merge above can't keep it fresh; while the docket is on screen, poll the server store instead. syncVerdictsFromServer's own guard bounds the rate, and each pickup re-derives the queue, so judged cluster decisions leave the page by themselves instead of waiting for a tab switch.
  setInterval(() => {
    if (state.view === 'docket' && !document.hidden) syncVerdictsFromServer();
  }, 3000);

  window.addEventListener('beforeunload', (event) => {
    if (store.unexported.size === 0) return;
    if (autosaveHealthy()) return;
    event.preventDefault();
    event.returnValue = '';
  });

  window.addEventListener('pagehide', () => {
    if (autosaveTimer === null) return;
    clearTimeout(autosaveTimer);
    autosaveTimer = null;
    navigator.sendBeacon('autosave', new Blob([exportPayload()], { type: 'application/json' }));
  });
}

function renderChrome() {
  document.getElementById('build-command').textContent = manifest.build_command ?? '';
  document.getElementById('serve-command').textContent = manifest.serve_command ?? '';
  const machine = manifest.machine_approved;
  const exempt = noVerdictTotal(manifest);
  document.getElementById('surface-total').textContent = surfaceChipLabel(manifest);
  document.getElementById('manifest-meta').textContent =
    `Mode ${manifest.mode}, generated ${manifest.generated_at} at ${manifest.repo_head}; ` +
    `${formatCount(manifest.totals.units)} units on the surface — ` +
    `${formatCount(machine?.units ?? 0)} machine-approved` +
    `${exempt ? `, ${formatCount(exempt)} in no-verdict classes` : ''}, and ` +
    `${formatCount(humanTotal(manifest))} human-workload in ${manifest.totals.batches} batches — ` +
    `covering ${formatCount(manifest.totals.rows)} rows.`;
  const stamp = document.getElementById('surface-stamp');
  const stampText = surfaceStampLine(manifest);
  stamp.textContent = stampText ?? '';
  stamp.hidden = stampText === null;
  const alphabetLabel = surfaceAlphabetLabel(manifest);
  const alphabet = document.createElement('td');
  if (alphabetLabel !== null) {
    alphabet.textContent = alphabetLabel;
    alphabet.title = 'Letters migrated to the rebuild engine, against the whole Quikscript alphabet — the surface is built over the migrated ones.';
  }
  const unitsHeading = document.createElement('th');
  unitsHeading.scope = 'col';
  unitsHeading.textContent = 'units';
  const headRow = document.createElement('tr');
  headRow.append(alphabet, unitsHeading);
  const head = document.createElement('thead');
  head.append(headRow);
  const body = document.createElement('tbody');
  for (const row of surfaceDetailRows(manifest)) {
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
  document.getElementById('surface-detail-rows').replaceChildren(head, body);
}

renderChrome();
renderSidebar();
wireEvents();
indexReady = loadHumanIndex();
await restoreAutosave();
bootRestoreDone = true;
await indexReady;
applyHashState(true);
refreshStatus();
