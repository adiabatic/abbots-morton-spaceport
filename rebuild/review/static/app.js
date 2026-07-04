import { parseHash, writeHash, shedWorklist } from './state.js';
import { actionForKey, isEditableTarget } from './keyboard.js';
import {
  createStore,
  recordVerdict,
  updateNote,
  groupApprove,
  undo,
  assembleExport,
  markExported,
  importVerdicts,
  verdictCounts,
} from './verdicts.js';
import {
  renderGroupsOf,
  highlightRect,
  markOffset,
  secondarySeamsOf,
  seamChip,
  needsNoVerdict,
  familiesOfGroup,
  unitMatchesFilters,
  unitWorklist,
  partitionUnits,
  humanClassCount,
  humanTotal,
  noVerdictTotal,
  nextUnverdictedIndex,
  stepIndex,
  availableBatches,
  copyPreamble,
  tokenSeparators,
  searchUnits,
} from './render.js';

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
const shardCache = new Map();
const unitsById = new Map();
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

const MACHINE_BADGE = 'ink-identical — machine approved';
const MACHINE_TITLE =
  'Both fonts render this unit identically under every config in its set; no human input is meaningful.';
const NO_VERDICT_BADGE = 'no verdict needed';
const NO_VERDICT_TITLE =
  "This unit's class is adjudicated wholesale at the ledger level (hover its sidebar entry for the rationale); no unit in it ever needs an individual verdict.";

function configNoteDetail(note) {
  const descriptions = manifest.feature_descriptions;
  if (!descriptions) return null;
  const match = note.match(/^only when (ss\d+) is (?:on|off)$/) ?? note.match(/^only under (ss\d+)$/);
  return match ? (descriptions[match[1]] ?? null) : null;
}

let state = withDefaults(parseHash(location.hash));
let visibleUnits = [];
let machineUnits = [];
let transientMachineUnitId = null;
let renderedKey = null;
let renderToken = 0;
const machineFoldBuilders = new Map();

let allShardsPromise = null;
let allShardsLoaded = false;
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

async function shardUnits(classId) {
  if (!shardCache.has(classId)) {
    const cls = manifest.classes.find((entry) => entry.id === classId);
    const promise = fetch(cls.shard)
      .then((response) => response.json())
      .then((units) => {
        for (const unit of units) unitsById.set(unit.id, unit);
        return units;
      });
    shardCache.set(classId, promise);
  }
  return shardCache.get(classId);
}

function ensureAllShards() {
  if (!allShardsPromise) {
    allShardsPromise = Promise.all(manifest.classes.map((cls) => shardUnits(cls.id))).then(() => {
      allShardsLoaded = true;
      populateFilterOptions();
      updateClassCounts();
    });
  }
  return allShardsPromise;
}

async function unitsForView(batch, classFilter) {
  if (state.units) {
    await ensureAllShards();
    const seen = new Set();
    const units = [];
    for (const id of unitWorklist(state.units)) {
      const unit = unitsById.get(id);
      if (unit && !seen.has(id)) {
        seen.add(id);
        units.push(unit);
      }
    }
    units.sort((a, b) => a.group.localeCompare(b.group) || a.id.localeCompare(b.id));
    return units;
  }
  const classes = [];
  for (const cls of manifest.classes) {
    if (classFilter) {
      if (cls.id === classFilter) classes.push(cls);
      continue;
    }
    if (cls.batches.includes(batch)) classes.push(cls);
    else if (cls.batches.length === 0 && batch === 0) classes.push(cls);
  }
  const lists = await Promise.all(classes.map((cls) => shardUnits(cls.id)));
  const units = [];
  for (const list of lists) {
    for (const unit of list) {
      if (needsNoVerdict(unit) || unit.batch === batch) units.push(unit);
    }
  }
  return units;
}

async function findUnitAnywhere(unitId) {
  if (unitsById.has(unitId)) return unitsById.get(unitId);
  for (const cls of manifest.classes) {
    if (state.class && cls.id !== state.class) continue;
    await shardUnits(cls.id);
    if (unitsById.has(unitId)) return unitsById.get(unitId);
  }
  return null;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function buildSample(unit, side, featureSettings) {
  const cell = el('div', `qs ${side}`);
  cell.style.fontFeatureSettings = featureSettings;
  const run = el('span', 'run');
  run.innerHTML = unit.text_entities;
  cell.append(run);
  const upem = manifest.fonts[side].upem;
  if (unit.pair !== null && unit.highlight && unit.highlight[side]) {
    const rect = highlightRect(unit.highlight[side], FONT_SIZE, upem);
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

function appendMarkedTokens(node, tokens, separators, span) {
  // The pair-mark span covers the pair's tokens and the separators between them; the separator before the first marked token stays outside.
  const pieces = { before: '', inside: '', after: '' };
  for (const [index, token] of tokens.entries()) {
    if (index < span[0]) pieces.before += separators[index] + token;
    else if (index === span[0]) {
      pieces.before += separators[index];
      pieces.inside += token;
    } else if (index <= span[1]) pieces.inside += separators[index] + token;
    else pieces.after += separators[index] + token;
  }
  if (pieces.before) node.append(document.createTextNode(pieces.before));
  node.append(el('span', 'pair-mark', pieces.inside));
  if (pieces.after) node.append(document.createTextNode(pieces.after));
}

function buildNotationLine(unit) {
  const line = el('div', 'notation');
  const tokens = unit.notation_tokens;
  if (!Array.isArray(tokens) || tokens.length === 0 || !unit.pair_codepoints) {
    line.textContent = unit.notation;
    return line;
  }
  appendMarkedTokens(line, tokens, tokenSeparators(tokens), unit.pair_codepoints);
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
  appendMarkedTokens(code, tokens, separators, unit.pair_codepoints);
  return code;
}

function buildRow(unit) {
  const exempt = needsNoVerdict(unit);
  const exemptTitle = unit.ink_identical ? MACHINE_TITLE : NO_VERDICT_TITLE;
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
  if (unit.ink_identical) {
    const badge = el('span', 'machine-badge', MACHINE_BADGE);
    badge.title = MACHINE_TITLE;
    meta.append(badge);
  } else if (unit.no_verdict) {
    const badge = el('span', 'machine-badge', NO_VERDICT_BADGE);
    badge.title = NO_VERDICT_TITLE;
    meta.append(badge);
  }
  if (unit.config_note) {
    const badge = el('span', 'config-note');
    const ssMatch =
      unit.config_note.match(/^only when (ss\d+) is (on|off)$/) ?? unit.config_note.match(/^only under (ss\d+)$/);
    if (ssMatch) {
      badge.dataset.ss = ssMatch[1];
      badge.dataset.state = ssMatch[2] === 'off' ? 'off' : 'on';
    }
    badge.append(el('span', 'config-note-gate', unit.config_note));
    const detail = configNoteDetail(unit.config_note);
    if (detail) badge.append(el('span', 'config-note-detail', ` — ${detail}`));
    badge.title = unit.configs.join(', ');
    meta.append(badge);
  }
  if (unit.config_class_note) {
    const badge = el('span', 'config-class-note', unit.config_class_note);
    meta.append(badge);
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
  if (unit.explain) {
    panel.append(el('h4', null, 'Explain'));
    panel.append(el('pre', null, unit.explain));
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
  row.append(panel);

  syncRowVerdict(unit.id, row);
  return row;
}

function renderBatch(units, machine) {
  closeRejectMenu();
  closeNeitherMenu();
  const container = document.getElementById('batch');
  container.textContent = '';
  machineFoldBuilders.clear();
  if (units.length === 0 && machine.length === 0) {
    container.append(el('p', 'empty', 'No units match the current batch and filters.'));
    return;
  }
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
  renderMachineSection(container, machine);
  updateGroupCounts();
}

function renderMachineSection(container, machine) {
  if (machine.length === 0) return;
  const heading =
    state.machine === '1'
      ? `No verdict needed in this view: ${machine.length} units (ink-identical or in a no-verdict class)`
      : state.units
        ? `No verdict needed in your worklist: ${machine.length} unit${machine.length === 1 ? '' : 's'} shown below`
        : 'This deep-linked unit needs no verdict — it stays out of your queue and disappears when you move on.';
  container.append(el('h2', 'machine-heading', heading));
  const byClass = new Map();
  for (const unit of machine) {
    if (!byClass.has(unit.class)) byClass.set(unit.class, []);
    byClass.get(unit.class).push(unit);
  }
  for (const [classId, classUnits] of byClass) {
    const fold = el('details', 'group machine-group');
    fold.dataset.machineClass = classId;
    const badge = classUnits.every((unit) => unit.ink_identical) ? MACHINE_BADGE : NO_VERDICT_BADGE;
    const summary = el('summary');
    summary.append(el('span', 'group-name', classId));
    summary.append(el('span', 'group-counts', `${classUnits.length} units — ${badge}`));
    fold.append(summary);
    const build = () => {
      if (!machineFoldBuilders.has(fold)) return;
      machineFoldBuilders.delete(fold);
      for (const unit of classUnits) fold.append(buildRow(unit));
    };
    machineFoldBuilders.set(fold, build);
    fold.addEventListener('toggle', () => {
      if (fold.open) build();
    });
    if (state.units) {
      fold.open = true;
      build();
    }
    container.append(fold);
  }
}

function revealMachineUnit(unitId) {
  const unit = unitsById.get(unitId);
  if (!unit || !needsNoVerdict(unit)) return false;
  const fold = document.querySelector(`details.machine-group[data-machine-class="${unit.class}"]`);
  if (!fold) return false;
  const build = machineFoldBuilders.get(fold);
  if (build) build();
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
      // Deep-linking to a machine-approved or no-verdict unit reveals just that unit transiently; the persistent toggle stays off and any navigation away hides it again.
      transientMachineUnitId = unit.id;
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

function updateGroupCounts() {
  for (const fold of document.querySelectorAll('details.group:not(.machine-group)')) {
    const rows = fold.querySelectorAll('.row');
    let verdicted = 0;
    for (const row of rows) if (row.dataset.verdict) verdicted += 1;
    fold.querySelector('.group-counts').textContent = `${verdicted}/${rows.length} verdicted`;
    fold.querySelector('.group-approve').hidden = verdicted === rows.length;
  }
}

function updateProgress() {
  let batchVerdicted = 0;
  for (const unit of visibleUnits) if (store.records.has(unit.id)) batchVerdicted += 1;
  document.getElementById('batch-progress').textContent =
    `Batch ${state.batch}: ${batchVerdicted}/${visibleUnits.length}`;
  const counts = verdictCounts(store);
  document.getElementById('overall-progress').textContent =
    `Overall: ${store.records.size}/${humanTotal(manifest)} ` +
    `(✓${counts.approve} ✗${counts.reject} ≈${counts.either} ≡${counts.identical} ∅${counts.neither} →${counts.skip})`;
  const nudge = document.getElementById('unexported-nudge');
  nudge.hidden = store.unexported.size === 0;
  nudge.textContent = `${store.unexported.size} unexported${autosaveHealthy() ? ' (autosaved)' : ''}`;
  updateGroupCounts();
  updateClassCounts();
}

function updateTitle() {
  const unitId = cursorUnitId();
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

function updateClassCounts() {
  for (const button of document.querySelectorAll('.class-button')) {
    const cls = manifest.classes.find((entry) => entry.id === button.dataset.class);
    if (cls.no_verdict) {
      button.querySelector('.class-counts').textContent =
        `${cls.unit_count} units — ${NO_VERDICT_BADGE} · ${cls.row_count} rows`;
      button.setAttribute('aria-pressed', String(state.class === cls.id));
      continue;
    }
    let verdicted = 0;
    let known = false;
    if (shardCache.has(cls.id)) {
      known = true;
      for (const [unitId, unit] of unitsById) {
        if (unit.class === cls.id && store.records.has(unitId)) verdicted += 1;
      }
    }
    const human = humanClassCount(cls);
    const progress = known ? `${verdicted}/${human}` : `${human} units`;
    const machine = cls.machine_approved_count ? ` · ${cls.machine_approved_count} machine` : '';
    button.querySelector('.class-counts').textContent = `${progress} · ${cls.row_count} rows${machine}`;
    button.setAttribute('aria-pressed', String(state.class === cls.id));
  }
}

function updateBatchNav() {
  const batches = availableBatches(manifest, state.class);
  const position = batches.indexOf(state.batch);
  document.getElementById('batch-label').textContent = `Batch ${state.batch} (${position + 1}/${batches.length})`;
  document.getElementById('prev-batch').disabled = position <= 0;
  document.getElementById('next-batch').disabled = position < 0 || position >= batches.length - 1;
}

function populateFilterOptions() {
  const familySelect = document.getElementById('filter-family');
  const families = new Set();
  for (const unit of unitsById.values()) {
    for (const family of familiesOfGroup(unit.group)) families.add(family);
  }
  const existing = new Set();
  for (const option of familySelect.options) existing.add(option.value);
  for (const family of [...families].sort()) {
    if (existing.has(family)) continue;
    const option = el('option', null, family);
    option.value = family;
    familySelect.append(option);
  }
}

function syncFilterControls() {
  document.getElementById('filter-family').value = state.family ?? '';
  document.getElementById('filter-config').value = state.config ?? '';
  document.getElementById('filter-status').value = state.status ?? '';
  document.getElementById('show-machine').checked = state.machine === '1';
}

async function applyHashState() {
  const token = (renderToken += 1);
  const units = await unitsForView(state.batch, state.class);
  if (token !== renderToken) return;
  const { human, machine } = partitionUnits(units, state, (unitId) => store.records.get(unitId));
  if (transientMachineUnitId && state.unit !== transientMachineUnitId) transientMachineUnitId = null;
  if (transientMachineUnitId && !machine.some((unit) => unit.id === transientMachineUnitId)) {
    const transient = unitsById.get(transientMachineUnitId);
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
    transientMachineUnitId,
  ]);
  if (key !== renderedKey) {
    renderBatch(human, machine);
    renderedKey = key;
    if (state.units) {
      const listed = new Set(unitWorklist(state.units)).size;
      const shown = human.length + machine.length;
      if (shown < listed) toast(`${listed - shown} of ${listed} listed units aren't in this build — showing the ${shown} that are.`);
    }
  }
  populateFilterOptions();
  syncFilterControls();
  if (!(await ensureCursor())) return;
  updateCursorDom();
  if (state.unit) revealMachineUnit(state.unit);
  updateProgress();
  updateTitle();
  updateBatchNav();
  updateClassCounts();
}

let toastTimer = null;
function toast(message) {
  const node = document.getElementById('toast');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, 2600);
}

function applyVerdict(unitId, verdict) {
  const row = rowFor(unitId);
  const note = row ? row.querySelector('.note').value : '';
  const existing = store.records.get(unitId);
  const wasUnverdicted = !existing;
  if (existing && existing.verdict === verdict) {
    recordVerdict(store, unitId, null);
  } else {
    recordVerdict(store, unitId, verdict, { note });
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

function verdictCursor(verdict) {
  const unitId = cursorUnitId();
  if (!unitId) return;
  if (applyVerdict(unitId, verdict)) advanceFrom(unitId);
}

function jumpToFirstUnverdicted() {
  const open = visibleUnits.find((unit) => !store.records.has(unit.id));
  if (!open) {
    toast('Everything in this view is verdicted');
    return;
  }
  setStateReplace({ unit: open.id });
}

let rejectMenuUnitId = null;
let rejectMenuNode = null;

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
  menu.addEventListener('click', (event) => {
    const option = event.target.closest('.reject-option');
    if (!option) return;
    event.stopPropagation();
    if (option.dataset.action === 'reject-comment') {
      rejectWithComment();
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
  menu.addEventListener('click', (event) => {
    const option = event.target.closest('.neither-option');
    if (!option) return;
    event.stopPropagation();
    if (option.dataset.action === 'neither-comment') {
      neitherWithComment();
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
  const unit = unitsById.get(unitId);
  if (!unit) return;
  const ids = [];
  for (const candidate of visibleUnits) {
    if (candidate.group === unit.group && !store.records.has(candidate.id)) ids.push(candidate.id);
  }
  const applied = groupApprove(store, ids);
  for (const id of applied) syncRowVerdict(id);
  updateProgress();
  scheduleAutosave();
  toast(`Approved ${applied.length} remaining in ${unit.group}`);
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

function toggleExplain(unitId) {
  const row = rowFor(unitId);
  if (!row) return;
  const panel = row.querySelector('.explain-panel');
  panel.hidden = !panel.hidden;
  row.querySelector('.explain-toggle').setAttribute('aria-expanded', String(!panel.hidden));
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

function activeSearchUnitId() {
  const rows = document.querySelectorAll('#search-results .search-result');
  if (searchActive < 0 || searchActive >= rows.length) return null;
  return rows[searchActive].dataset.unit;
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
  const { matches, total } = searchUnits([...unitsById.values()], query, SEARCH_LIMIT);
  results.textContent = '';
  results.hidden = false;
  const input = document.getElementById('unit-search');
  input.setAttribute('aria-expanded', 'true');
  input.removeAttribute('aria-activedescendant');
  searchActive = -1;
  if (matches.length === 0) {
    results.append(presentational(el('p', 'search-empty', 'No units match.')));
    return;
  }
  for (const [position, unit] of matches.entries()) {
    const row = el('button', 'search-result');
    row.type = 'button';
    row.id = `search-opt-${unit.id}`;
    row.dataset.unit = unit.id;
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', 'false');
    row.setAttribute('aria-setsize', String(matches.length));
    row.setAttribute('aria-posinset', String(position + 1));
    row.append(el('span', 'search-id', unit.id));
    row.append(el('span', 'search-notation', unit.notation));
    row.append(el('span', 'search-class', unit.class));
    const where = unit.ink_identical ? 'machine' : unit.no_verdict ? 'no verdict' : `batch ${unit.batch}`;
    row.append(el('span', 'search-where', where));
    results.append(row);
  }
  if (total > matches.length) {
    results.append(
      presentational(el('p', 'search-more', `Showing ${matches.length} of ${total} matches — refine to narrow.`)),
    );
  }
}

async function runSearch() {
  const input = document.getElementById('unit-search');
  const query = input.value;
  if (!query.trim()) {
    closeSearch();
    return;
  }
  if (!allShardsLoaded) {
    const results = document.getElementById('search-results');
    results.textContent = '';
    results.hidden = false;
    results.append(presentational(el('p', 'search-empty', 'Loading every class…')));
    await ensureAllShards();
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
    const copy = event.target.closest('.copy-unit');
    if (copy && row) {
      copyToClipboard(copyPreamble(unitsById.get(row.dataset.unit)), copy);
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
      updateProgress();
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
    ensureAllShards();
    if (searchInput.value.trim()) runSearch();
  });
  searchInput.addEventListener('input', runSearch);
  searchInput.addEventListener('keydown', (event) => {
    // Only intercept navigation when real result rows exist — during the all-shards load only the placeholder is shown, so let the browser keep native caret movement.
    if (searchResults.hidden || !searchResults.querySelector('.search-result')) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setSearchActive(searchActive + 1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setSearchActive(searchActive - 1);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const firstId = searchResults.querySelector('.search-result')?.dataset.unit;
      selectSearchResult(activeSearchUnitId() ?? firstId);
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
    selectSearchResult(row.dataset.unit);
  });

  document.getElementById('jump-unverdicted').addEventListener('click', jumpToFirstUnverdicted);
  document.getElementById('prev-batch').addEventListener('click', () => shiftBatch(-1));
  document.getElementById('next-batch').addEventListener('click', () => shiftBatch(1));
  document.getElementById('open-help').addEventListener('click', () => helpDialog.showModal());
  document.getElementById('open-import').addEventListener('click', () => importDialog.showModal());
  document.getElementById('download-verdicts').addEventListener('click', downloadVerdicts);
  document.getElementById('copy-verdicts').addEventListener('click', (event) => {
    copyToClipboard(exportPayload(), event.target.closest('button'));
    markExported(store);
    updateProgress();
    toast(`Copied ${store.records.size} verdicts to the clipboard`);
  });

  document.getElementById('do-import').addEventListener('click', async () => {
    const fileInput = document.getElementById('import-file');
    const paste = document.getElementById('import-paste');
    if (fileInput.files.length > 0) {
      runImport(await fileInput.files[0].text());
    } else if (paste.value.trim()) {
      runImport(paste.value);
    } else {
      toast('Choose a file or paste an export first');
    }
  });

  window.addEventListener('hashchange', () => {
    state = withDefaults(parseHash(location.hash));
    applyHashState();
  });

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
  document.getElementById('manifest-meta').textContent =
    `Mode ${manifest.mode}, generated ${manifest.generated_at} at ${manifest.repo_head}; ` +
    `${humanTotal(manifest)} human-workload units in ${manifest.totals.batches} batches, plus ` +
    `${machine?.units ?? 0} machine-approved${exempt ? ` and ${exempt} in no-verdict classes` : ''}, ` +
    `covering ${manifest.totals.rows} rows.`;
  const line = document.getElementById('machine-approved-line');
  if (machine && machine.units > 0) {
    line.textContent = `${machine.units} ink-identical units machine-approved`;
    line.title = machine.method ?? '';
    line.hidden = false;
  }
}

renderChrome();
renderSidebar();
wireEvents();
await restoreAutosave();
applyHashState();
