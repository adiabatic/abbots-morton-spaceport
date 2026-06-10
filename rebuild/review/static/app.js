import { parseHash, writeHash } from './state.js';
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
  featureSettingsValue,
  highlightRect,
  markOffset,
  familiesOfGroup,
  unitMatchesFilters,
  nextUnverdictedIndex,
  stepIndex,
  availableBatches,
  copyPreamble,
} from './render.js';

const FONT_SIZE = 88;
const VERDICT_LABELS = [
  ['approve', 'Approve', 'j'],
  ['reject', 'Reject', 'f'],
  ['either', 'Either', 'd'],
  ['skip', 'Skip', 'k'],
];

const manifest = await (await fetch('manifest.json')).json();
const store = createStore();
const shardCache = new Map();
const unitsById = new Map();
const activeConfigByUnit = new Map();
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

let state = withDefaults(parseHash(location.hash));
let visibleUnits = [];
let renderedKey = null;
let renderToken = 0;

function withDefaults(parsed) {
  const next = { ...parsed };
  if (next.batch === null) {
    const batches = availableBatches(manifest, next.class);
    next.batch = batches.length > 0 ? batches[0] : 0;
  }
  return next;
}

function setState(patch) {
  state = { ...state, ...patch };
  const serialized = writeHash(state);
  if (location.hash.replace(/^#/, '') !== serialized) location.hash = serialized;
  else applyHashState();
}

function setStateReplace(patch) {
  state = { ...state, ...patch };
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

async function unitsForBatch(batch, classFilter) {
  const classes = [];
  for (const cls of manifest.classes) {
    if (classFilter && cls.id !== classFilter) continue;
    if (cls.batches.includes(batch)) classes.push(cls);
  }
  const lists = await Promise.all(classes.map((cls) => shardUnits(cls.id)));
  const units = [];
  for (const list of lists) {
    for (const unit of list) if (unit.batch === batch) units.push(unit);
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

function buildSample(unit, side) {
  const cell = el('div', `qs ${side}`);
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
  for (const mark of unit.boundary_marks ?? []) {
    const tick = el('span', 'boundary-mark');
    tick.style.left = `${markOffset(mark.x, FONT_SIZE, upem)}px`;
    tick.title = mark.kind;
    cell.append(tick);
  }
  return cell;
}

function applyConfigToRow(row, unit, configToken) {
  const value = featureSettingsValue(configToken);
  for (const cell of row.querySelectorAll('.qs')) cell.style.fontFeatureSettings = value;
  for (const chip of row.querySelectorAll('.config-chips .chip')) {
    chip.setAttribute('aria-pressed', String(chip.dataset.config === (unit.scopedConfig ?? null)));
  }
}

function buildRow(unit) {
  const row = el('article', 'row');
  row.id = `unit-${unit.id}`;
  row.dataset.unit = unit.id;
  row.dataset.group = unit.group;

  const label = el('div', 'label');
  label.append(el('div', 'notation', unit.notation));

  const codepoints = el('div', 'codepoints');
  const code = el('code', null, unit.codepoints);
  const copy = el('button', 'copy-unit', 'Copy');
  copy.type = 'button';
  copy.title = 'Copy a prompt preamble for this unit';
  codepoints.append(code, copy);
  label.append(codepoints);

  const meta = el('div', 'meta-chips');
  meta.append(el('span', 'unit-id', unit.id));
  for (const kind of unit.kinds ?? []) meta.append(el('span', 'kind', kind));
  if (unit.exemplar) meta.append(el('span', 'exemplar', 'exemplar'));
  label.append(meta);

  const chips = el('div', 'config-chips');
  for (const config of unit.configs) {
    const chip = el('button', 'chip', config);
    chip.type = 'button';
    chip.dataset.config = config;
    chip.setAttribute('aria-pressed', 'false');
    chip.title = 'View this config and scope the next verdict to it';
    chips.append(chip);
  }
  label.append(chips);

  const buttons = el('div', 'verdict-buttons');
  for (const [verdict, text, key] of VERDICT_LABELS) {
    const button = el('button', 'verdict-btn');
    button.type = 'button';
    button.dataset.verdict = verdict;
    button.setAttribute('aria-pressed', 'false');
    button.append(document.createTextNode(`${text} `));
    const kbd = el('kbd', null, key);
    button.append(kbd);
    buttons.append(button);
  }
  label.append(buttons);

  const note = el('input', 'note');
  note.type = 'text';
  note.placeholder = 'note (n)';
  note.setAttribute('aria-label', `Note for ${unit.id}`);
  label.append(note);

  const explainToggle = el('button', 'explain-toggle', 'Explain (x)');
  explainToggle.type = 'button';
  explainToggle.setAttribute('aria-expanded', 'false');
  label.append(explainToggle);

  row.append(label, buildSample(unit, 'before'), buildSample(unit, 'after'));

  const panel = el('div', 'explain-panel');
  panel.hidden = true;
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

  applyConfigToRow(row, unit, activeConfigByUnit.get(unit.id) ?? unit.configs[0]);
  syncRowVerdict(unit.id, row);
  return row;
}

function renderBatch(units) {
  const container = document.getElementById('batch');
  container.textContent = '';
  if (units.length === 0) {
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
  updateGroupCounts();
}

function rowFor(unitId) {
  return document.getElementById(`unit-${unitId}`);
}

function syncRowVerdict(unitId, row = rowFor(unitId)) {
  if (!row) return;
  const record = store.records.get(unitId);
  if (record) row.dataset.verdict = record.verdict;
  else delete row.dataset.verdict;
  for (const button of row.querySelectorAll('.verdict-btn')) {
    button.setAttribute('aria-pressed', String(Boolean(record) && record.verdict === button.dataset.verdict));
  }
  const note = row.querySelector('.note');
  if (record && record.note && note.value !== record.note && document.activeElement !== note) note.value = record.note;
}

function cursorUnitId() {
  if (state.unit && visibleUnits.some((unit) => unit.id === state.unit)) return state.unit;
  return visibleUnits.length > 0 ? visibleUnits[0].id : null;
}

async function ensureCursor() {
  if (state.unit && !visibleUnits.some((unit) => unit.id === state.unit)) {
    const unit = await findUnitAnywhere(state.unit);
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
  if (scroll) row.scrollIntoView({ block: 'nearest', behavior: reducedMotion.matches ? 'auto' : 'smooth' });
}

function updateGroupCounts() {
  for (const fold of document.querySelectorAll('details.group')) {
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
    `Overall: ${store.records.size}/${manifest.totals.units} ` +
    `(✓${counts.approve} ✗${counts.reject} ≈${counts.either} →${counts.skip})`;
  const nudge = document.getElementById('unexported-nudge');
  nudge.hidden = store.unexported.size === 0;
  nudge.textContent = `${store.unexported.size} unexported`;
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
    let verdicted = 0;
    let known = false;
    if (shardCache.has(cls.id)) {
      known = true;
      for (const [unitId, unit] of unitsById) {
        if (unit.class === cls.id && store.records.has(unitId)) verdicted += 1;
      }
    }
    const progress = known ? `${verdicted}/${cls.unit_count}` : `${cls.unit_count} units`;
    button.querySelector('.class-counts').textContent = `${progress} · ${cls.row_count} rows`;
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
}

async function applyHashState() {
  const token = (renderToken += 1);
  const units = await unitsForBatch(state.batch, state.class);
  if (token !== renderToken) return;
  const filtered = [];
  for (const unit of units) {
    if (unitMatchesFilters(unit, state, store.records.get(unit.id))) filtered.push(unit);
  }
  visibleUnits = filtered;
  const key = JSON.stringify([state.class, state.batch, state.group, state.config, state.family, state.status]);
  if (key !== renderedKey) {
    renderBatch(filtered);
    renderedKey = key;
  }
  populateFilterOptions();
  syncFilterControls();
  if (!(await ensureCursor())) return;
  updateCursorDom();
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
  const unit = unitsById.get(unitId);
  const scoped = unit && unit.scopedConfig ? [unit.scopedConfig] : null;
  const existing = store.records.get(unitId);
  if (existing && existing.verdict === verdict && !scoped) {
    recordVerdict(store, unitId, null);
  } else {
    recordVerdict(store, unitId, verdict, { configs: scoped, note });
  }
  if (unit && unit.scopedConfig) {
    delete unit.scopedConfig;
    const row2 = rowFor(unitId);
    if (row2) applyConfigToRow(row2, unit, activeConfigByUnit.get(unitId) ?? unit.configs[0]);
  }
  syncRowVerdict(unitId);
  updateProgress();
  if (store.unexported.size > 0 && store.unexported.size % 50 === 0) {
    toast(`${store.unexported.size} verdicts not yet exported — consider downloading verdicts.json`);
  }
}

function advanceFrom(unitId) {
  const ids = [];
  for (const unit of visibleUnits) ids.push(unit.id);
  const fromIndex = ids.indexOf(unitId);
  const next = nextUnverdictedIndex(ids, fromIndex, (id) => store.records.has(id));
  if (next === -1) {
    toast('Batch fully verdicted — press ] for the next batch');
    updateTitle();
    return;
  }
  setStateReplace({ unit: ids[next] });
}

function verdictCursor(verdict) {
  const unitId = cursorUnitId();
  if (!unitId) return;
  applyVerdict(unitId, verdict);
  advanceFrom(unitId);
}

function moveCursor(delta) {
  const ids = [];
  for (const unit of visibleUnits) ids.push(unit.id);
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

function downloadVerdicts() {
  const blob = new Blob([exportPayload()], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'verdicts.json';
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
  toast(`Imported: ${result.added} added, ${result.replaced} replaced, ${result.keptNewer} kept newer`);
  document.getElementById('import').close();
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
    });
    if (!action) return;
    if (action === 'escape') {
      if (isEditableTarget(event.target)) event.target.blur();
      return;
    }
    event.preventDefault();
    if (action === 'approve' || action === 'reject' || action === 'either' || action === 'skip') {
      verdictCursor(action);
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
    } else if (action === 'prev-batch') {
      shiftBatch(-1);
    } else if (action === 'next-batch') {
      shiftBatch(1);
    } else if (action === 'help') {
      if (helpDialog.open) helpDialog.close();
      else helpDialog.showModal();
    }
  });

  document.getElementById('batch').addEventListener('click', (event) => {
    const row = event.target.closest('.row');
    const verdictButton = event.target.closest('.verdict-btn');
    if (verdictButton && row) {
      applyVerdict(row.dataset.unit, verdictButton.dataset.verdict);
      advanceFrom(row.dataset.unit);
      return;
    }
    const chip = event.target.closest('.config-chips .chip');
    if (chip && row) {
      const unit = unitsById.get(row.dataset.unit);
      const wasActive = chip.getAttribute('aria-pressed') === 'true';
      const config = wasActive ? unit.configs[0] : chip.dataset.config;
      activeConfigByUnit.set(unit.id, config);
      if (wasActive) delete unit.scopedConfig;
      else unit.scopedConfig = chip.dataset.config;
      applyConfigToRow(row, unit, config);
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
    if (updateNote(store, row.dataset.unit, note.value)) updateProgress();
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
    event.preventDefault();
    event.returnValue = '';
  });
}

function renderChrome() {
  document.getElementById('build-command').textContent = manifest.build_command ?? '';
  document.getElementById('serve-command').textContent = manifest.serve_command ?? '';
  document.getElementById('manifest-meta').textContent =
    `Mode ${manifest.mode}, generated ${manifest.generated_at} at ${manifest.repo_head}; ` +
    `${manifest.totals.units} units / ${manifest.totals.rows} rows in ${manifest.totals.batches} batches.`;
}

renderChrome();
renderSidebar();
wireEvents();
applyHashState();
