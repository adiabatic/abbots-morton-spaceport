// The kerning view's DOM (`#view=kern`, rebuild/KERN-PLAN.md section 6): the grid of leading rune by following rune, the detail panel of the focused pair of runes, and the status lines. It is the app's second DOM module beside app.js (rebuild/REVIEW-PLAN.md §5.2), and nothing touches the DOM until `createKernView` is called. Its model and drawing are the pure modules kern.js and kern-draw.js; it fetches the inventory (`GET kern/inventory`) once and the values (`GET kern`) on every refresh, and draws every pixel from them, shaping nothing. The view is read-only: rebuild/KERN-PLAN.md's fourth step adds editing.

import { formatCount } from './render.js';
import {
  configNames,
  decidersOf,
  deciderText,
  hasFacingPairs,
  heightPhrase,
  indexInventory,
  inkGapText,
  inventoryMoved,
  inventoryRefusal,
  liveValue,
  builtValue,
  manualTexts,
  moveFacing,
  moveGridCursor,
  notInFont,
  pairFromQuery,
  paneFor,
  valuesAfterFetch,
  reachChip,
  reachText,
  runePairAt,
  selectionFromState,
  signed,
  stalenessLines,
  stateFromSelection,
  worstInk,
} from './kern.js';
import {
  ROLES,
  STAGE_ROWS,
  cellDrawList,
  drawingsOf,
  gridCellPixels,
  paintGuides,
  paintSquares,
  pairDrawList,
  textDrawList,
} from './kern-draw.js';

const GRID_SCALE = 2;
const STAGE_SCALE = 6;
const CELL_TOP = 12;
const CELL_BOTTOM = 10;
const CELL_SLACK = 4;
const STAGE_MARGIN = 1;
const COLOR_ROLES = [...ROLES, 'guide', 'text', 'muted', 'mark'];
const GRID_CHIPS = { 'only across a ZWNJ': 'ZWNJ', 'only under ss10': 'ss10', 'only across a ZWNJ or under ss10': 'ZWNJ ss10' };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function sizedContext(canvas, width, height) {
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(width * ratio));
  canvas.height = Math.max(1, Math.round(height * ratio));
  // The height follows from the width and the backing store's proportions, so a stage narrowed to fit its panel keeps square pixels.
  canvas.style.width = `${width}px`;
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  return context;
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: 'no-store' });
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  return { ok: response.ok, status: response.status, body };
}

// Create the view in `root`, the app's `#kern` section. `toast` shows a short message; `writeSelection` puts the selection's `pair` and `facing` in the URL hash without moving the page.
export function createKernView({ root, toast, writeSelection }) {
  const view = {
    model: null,
    values: null,
    cursor: { row: 0, col: 0 },
    facing: 0,
    loading: null,
    error: null,
    colors: null,
    dom: null,
    cell: { width: 0, height: 0 },
    pending: null,
    drawn: null,
    refreshError: null,
    requested: { pair: null, facing: null },
  };

  function valueOf(id) {
    return liveValue(view.model, view.values, id);
  }

  function readColors() {
    const colors = {};
    for (const swatch of view.dom.swatches.children) colors[swatch.dataset.role] = getComputedStyle(swatch).color;
    return colors;
  }

  function build() {
    const title = el('h2', null, 'Kerning');
    const summary = el('p', 'kern-summary');
    const jump = el('label', 'kern-jump');
    const input = el('input');
    input.type = 'search';
    input.placeholder = '·no·tea (/)';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.setAttribute('aria-keyshortcuts', '/');
    input.title = 'Jump to a pair of letters: their names joined with middots or spaces (·no·tea, no tea), a ligature’s with a plus (·day+utter·tea).';
    jump.append('Pair ', input);
    const bar = el('div', 'kern-bar');
    bar.append(title, summary, jump);
    const status = el('div', 'kern-status');
    status.setAttribute('role', 'status');
    const grid = el('div', 'kern-grid');
    grid.tabIndex = 0;
    grid.setAttribute('role', 'grid');
    grid.setAttribute('aria-label', 'Kerning grid: the leading letter down the side, the following letter across the top; the arrows move the cursor');
    const detail = el('section', 'kern-detail');
    detail.tabIndex = 0;
    detail.setAttribute('aria-label', 'The focused pair of letters; up and down choose a facing pair');
    const panes = el('div', 'kern-panes');
    panes.append(grid, detail);
    const live = el('p', 'kern-live');
    live.setAttribute('aria-live', 'polite');
    const swatches = el('div', 'kern-swatches');
    swatches.setAttribute('aria-hidden', 'true');
    for (const role of COLOR_ROLES) {
      const swatch = el('span');
      swatch.dataset.role = role;
      swatches.append(swatch);
    }
    root.replaceChildren(bar, status, panes, live, swatches);
    view.dom = { summary, input, status, grid, detail, live, swatches, rows: [], cursor: null, head: null };

    input.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      const found = view.model ? pairFromQuery(input.value, view.model) : null;
      if (!found) {
        toast(`No pair of letters “${input.value}” in the kern inventory`);
        return;
      }
      input.blur();
      setCursor({ row: found.lead, col: found.follow });
      grid.focus();
    });
    grid.addEventListener('click', (event) => {
      const canvas = event.target.closest?.('canvas[data-row]');
      if (!canvas || view.cell.width === 0) return;
      setCursor({ row: Number(canvas.dataset.row), col: Math.floor(event.offsetX / view.cell.width) });
    });
    detail.addEventListener('click', (event) => {
      const option = event.target.closest?.('[data-facing]');
      if (option) setFacing(Number(option.dataset.facing));
    });
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      if (!view.model) return;
      view.colors = readColors();
      paintRows();
      renderDetail();
    });
  }

  function renderStatus() {
    const lines = view.error ? [{ level: 'error', text: view.error }] : stalenessLines(view.model, view.values, view.refreshError);
    const nodes = [];
    for (const line of lines) {
      const node = el('p', `kern-status-line ${line.level}`, line.text);
      for (const [code, message] of line.issues ?? []) node.append(el('span', 'kern-issue', `${code}: ${message}`));
      nodes.push(node);
    }
    view.dom.status.replaceChildren(...nodes);
    view.dom.status.hidden = nodes.length === 0;
    if (!view.model) {
      view.dom.summary.textContent = '';
      return;
    }
    const doc = view.model.doc;
    let kerned = 0;
    let unbuilt = 0;
    for (const id of doc.pairs.keys()) {
      if (valueOf(id) !== 0) kerned += 1;
      if (notInFont(view.model, view.values, id)) unbuilt += 1;
    }
    const withPairs = doc.rune_pairs.filter((entry) => entry.pairs.length > 0).length;
    view.dom.summary.textContent =
      `${formatCount(doc.pairs.length)} facing pairs over ${formatCount(withPairs)} pairs of letters; ${formatCount(kerned)} kerned` +
      (unbuilt ? `, ${formatCount(unbuilt)} not in the built font yet` : '') +
      '. Read-only.';
  }

  function renderGrid() {
    const { model } = view;
    view.cell = {
      width: (gridCellPixels(model) + CELL_SLACK) * GRID_SCALE,
      height: CELL_TOP + STAGE_ROWS * GRID_SCALE + CELL_BOTTOM,
    };
    const inner = el('div', 'kern-grid-inner');
    inner.style.setProperty('--kern-cell-width', `${view.cell.width}px`);
    inner.style.setProperty('--kern-cell-height', `${view.cell.height}px`);
    const head = el('div', 'kern-cols');
    head.append(el('span', 'kern-corner', 'first ↓ second →'));
    for (const rune of model.doc.runes) {
      const label = el('span', 'kern-col', rune.label);
      label.title = rune.name;
      head.append(label);
    }
    inner.append(head);
    const rows = [];
    for (const [index, rune] of model.doc.runes.entries()) {
      const row = el('div', 'kern-row');
      const label = el('span', 'kern-row-label', rune.label);
      label.title = rune.name;
      const canvas = el('canvas', 'kern-row-canvas');
      canvas.dataset.row = String(index);
      row.append(label, canvas);
      inner.append(row);
      rows.push(canvas);
    }
    const cursor = el('div', 'kern-cursor');
    inner.append(cursor);
    view.dom.grid.replaceChildren(inner);
    view.dom.rows = rows;
    view.dom.cursor = cursor;
    view.dom.head = head;
    paintRows();
    placeCursor(false);
  }

  function paintRows() {
    for (const row of view.dom.rows.keys()) paintRow(row);
  }

  function paintRow(row) {
    const { model, colors, cell } = view;
    const canvas = view.dom.rows[row];
    const width = cell.width * model.size;
    const context = sizedContext(canvas, width, cell.height);
    context.font = '9px system-ui, sans-serif';
    context.textBaseline = 'alphabetic';
    for (const col of model.doc.runes.keys()) {
      const x0 = col * cell.width;
      context.fillStyle = colors.guide;
      context.fillRect(x0, 0, 1, cell.height);
      const entry = runePairAt(model, row, col);
      const draw = cellDrawList(model, entry, valueOf);
      if (!draw) continue;
      paintSquares(context, draw.squares, {
        scale: GRID_SCALE,
        originX: x0 + (STAGE_MARGIN - Math.min(draw.minX, 0)) * GRID_SCALE,
        originY: CELL_TOP,
        colors,
        clip: { left: x0 + 1, right: x0 + cell.width },
      });
      context.fillStyle = colors.text;
      context.textAlign = 'left';
      if (draw.value !== 0) context.fillText(signed(draw.value), x0 + 2, CELL_TOP - 3);
      if (draw.count > 1) {
        context.fillStyle = colors.muted;
        context.textAlign = 'right';
        context.fillText(`×${draw.count}`, x0 + cell.width - 2, CELL_TOP - 3);
      }
      const chip = reachChip(model, entry);
      if (chip) {
        context.fillStyle = colors.muted;
        context.textAlign = 'left';
        context.fillText(GRID_CHIPS[chip] ?? chip, x0 + 2, cell.height - 2);
      }
      if (entry.pairs.some((id) => notInFont(model, view.values, id))) {
        context.strokeStyle = colors.mark;
        context.setLineDash([3, 2]);
        context.strokeRect(x0 + 1.5, 1.5, cell.width - 3, cell.height - 3);
        context.setLineDash([]);
      }
    }
  }

  function placeCursor(scroll = true) {
    const canvas = view.dom.rows[view.cursor.row];
    if (!canvas || !view.dom.cursor) return;
    const left = canvas.offsetLeft + view.cursor.col * view.cell.width;
    const top = canvas.offsetTop;
    const box = view.dom.cursor;
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
    if (!scroll) return;
    const grid = view.dom.grid;
    const labelWidth = canvas.offsetLeft;
    const headHeight = view.dom.head.offsetHeight;
    if (left - labelWidth < grid.scrollLeft) grid.scrollLeft = left - labelWidth;
    if (left + view.cell.width > grid.scrollLeft + grid.clientWidth) grid.scrollLeft = left + view.cell.width - grid.clientWidth;
    if (top - headHeight < grid.scrollTop) grid.scrollTop = top - headHeight;
    if (top + view.cell.height > grid.scrollTop + grid.clientHeight) grid.scrollTop = top + view.cell.height - grid.clientHeight;
  }

  function stage(draw) {
    const pixels = draw.maxX - draw.minX + 1 + 2 * STAGE_MARGIN;
    const width = pixels * STAGE_SCALE;
    const height = STAGE_ROWS * STAGE_SCALE;
    const canvas = el('canvas', 'kern-stage');
    const context = sizedContext(canvas, width, height);
    paintGuides(context, { width, scale: STAGE_SCALE, originX: 0, originY: 0, color: view.colors.guide });
    paintSquares(context, draw.squares, {
      scale: STAGE_SCALE,
      originX: (STAGE_MARGIN - draw.minX) * STAGE_SCALE,
      originY: 0,
      colors: view.colors,
    });
    return canvas;
  }

  function gapLine(closest, suffix = '') {
    const gap = inkGapText(closest);
    return el('span', `kern-gap ${gap.level}`.trim(), gap.text + suffix);
  }

  function facingRow(id, index) {
    const { model, values } = view;
    const doc = model.doc;
    const pair = doc.pairs[id];
    const value = valueOf(id);
    const text = pair.witness === null ? null : doc.texts[pair.witness];
    const draw = text ? textDrawList(model, text, valueOf, pair.at) : pairDrawList(model, pair.member, value);
    const row = el('li', 'kern-facing-row');
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', String(index === view.facing));
    row.dataset.facing = String(index);
    const what = el('div', 'kern-what');
    const notation = text ? text.notation : `${doc.glyphs[pair.member[0]].name} | ${doc.glyphs[pair.member[1]].name}`;
    what.append(el('code', null, notation));
    what.append(
      el(
        'span',
        null,
        `${doc.sides[pair.lead].name} | ${doc.sides[pair.follow].name} · ${pair.members} glyph pair${pair.members === 1 ? '' : 's'} · ${reachText(pair.reach)} · ${configNames(model, pair.configs)}`,
      ),
    );
    if (text && text.config !== 0) what.append(el('span', null, `witnessed under ${doc.configs[text.config]}`));
    if (!text) what.append(el('span', 'kern-mark', 'No witness text: the two shapes are drawn alone.'));
    const drawings = drawingsOf(pair).map((member) => pairDrawList(model, member, value));
    if (drawings.length > 0) {
      what.append(
        el('span', null, `${drawings.length} drawings: a side mixes shapes no record can tell apart, so each is drawn below and the closest ink is the worst of them`),
      );
      what.append(gapLine(worstInk([draw.closest, ...drawings.map((one) => one.closest)]), ` (worst of ${drawings.length + 1})`));
      const shelf = el('div', 'kern-drawings');
      for (const one of drawings) shelf.append(stage(one));
      what.append(shelf);
    } else {
      what.append(gapLine(draw.closest));
    }
    const source = deciderText(decidersOf(model, values, id));
    what.append(el('span', null, source ? `decided by ${source}` : 'no record: the default spacing'));
    if (notInFont(model, values, id)) what.append(el('span', 'kern-mark', `not in the built font yet, which has ${signed(builtValue(model, values, id))}`));
    const number = el('div', `kern-value${value === 0 ? ' zero' : ''}`, signed(value));
    row.append(stage(draw), what, number);
    return row;
  }

  function joinedRow(groupId) {
    const { model } = view;
    const doc = model.doc;
    const group = doc.joined[groupId];
    const row = el('li', 'kern-joined-row');
    const what = el('div', 'kern-what');
    const text = group.witness === null ? null : doc.texts[group.witness];
    what.append(el('code', null, text ? text.notation : `${doc.glyphs[group.member[0]].name} ${doc.glyphs[group.member[1]].name}`));
    what.append(
      el(
        'span',
        null,
        `${group.lead_stance} joins ${group.follow_stance} ${heightPhrase(group.height)} · ${group.members} glyph pair${group.members === 1 ? '' : 's'} · ${configNames(model, group.configs)}`,
      ),
    );
    what.append(el('span', null, 'Joined, so not kerned: extend and contract set its length.'));
    if (text) {
      const draw = textDrawList(model, text, valueOf, group.at);
      row.append(stage(draw), what);
    } else {
      what.append(el('span', 'kern-mark', 'No witness text.'));
      row.append(what);
    }
    return row;
  }

  function manualRow({ id, focus }) {
    const { model } = view;
    const text = model.doc.texts[id];
    const row = el('li', 'kern-manual-row');
    const draw = textDrawList(model, text, valueOf, focus.gap);
    const what = el('div', 'kern-what');
    what.append(el('code', null, text.notation));
    if (focus.joined) {
      const group = focus.group === null ? null : model.doc.joined[focus.group];
      what.append(el('span', null, group ? `the two letters join here ${heightPhrase(group.height)}, so no kern applies` : 'the two letters join here, so no kern applies'));
    } else {
      what.append(gapLine(draw.closest));
    }
    row.append(stage(draw), what);
    return row;
  }

  function renderDetail() {
    const { model } = view;
    if (!model) return;
    const doc = model.doc;
    const lead = doc.runes[view.cursor.row];
    const follow = doc.runes[view.cursor.col];
    const entry = runePairAt(model, view.cursor.row, view.cursor.col);
    const nodes = [];
    const heading = el('h3', null, `${lead.label} → ${follow.label}`);
    const chip = entry ? reachChip(model, entry) : null;
    if (chip) heading.append(el('span', 'kern-chip', chip));
    nodes.push(heading);
    if (!hasFacingPairs(entry)) {
      nodes.push(el('p', 'kern-empty', 'These two letters have no facing pair: they never meet at a break.'));
    } else {
      const list = el('ol', 'kern-facing');
      list.setAttribute('role', 'listbox');
      list.setAttribute('aria-label', 'Facing pairs');
      for (const [index, id] of entry.pairs.entries()) list.append(facingRow(id, index));
      nodes.push(list);
    }
    if (entry && entry.joined.length > 0) {
      nodes.push(el('h4', null, 'Joined, not kerned'));
      const list = el('ol', 'kern-joined');
      for (const id of entry.joined) list.append(joinedRow(id));
      nodes.push(list);
    }
    const manual = manualTexts(model, entry);
    if (manual.length > 0) {
      nodes.push(el('h4', null, 'In the Manual'));
      const list = el('ul', 'kern-manual');
      for (const found of manual) list.append(manualRow(found));
      nodes.push(list);
    }
    view.dom.detail.replaceChildren(...nodes);
  }

  function announce() {
    const { model } = view;
    const entry = runePairAt(model, view.cursor.row, view.cursor.col);
    const lead = model.doc.runes[view.cursor.row].label;
    const follow = model.doc.runes[view.cursor.col].label;
    view.dom.live.textContent = hasFacingPairs(entry)
      ? `${lead} before ${follow}: ${entry.pairs.length} facing pair${entry.pairs.length === 1 ? '' : 's'}, the first at ${signed(valueOf(entry.rep ?? entry.pairs[0]))}`
      : `${lead} before ${follow}: no facing pair`;
  }

  function writeState() {
    writeSelection(stateFromSelection(view.model, view.cursor, view.facing));
  }

  function setCursor(cursor) {
    if (!view.model) return;
    view.cursor = cursor;
    view.facing = 0;
    placeCursor();
    renderDetail();
    announce();
    writeState();
  }

  function setFacing(index) {
    const entry = runePairAt(view.model, view.cursor.row, view.cursor.col);
    if (!hasFacingPairs(entry)) return;
    view.facing = moveFacing(index, 0, entry.pairs.length);
    for (const option of view.dom.detail.querySelectorAll('[data-facing]')) {
      const selected = Number(option.dataset.facing) === view.facing;
      option.setAttribute('aria-selected', String(selected));
      if (selected) option.scrollIntoView({ block: 'nearest' });
    }
    writeState();
  }

  async function loadInventory() {
    const [inventory, values] = await Promise.all([
      fetchJson('kern/inventory'),
      fetchJson('kern').catch((error) => ({ ok: false, status: 0, body: null, error })),
    ]);
    if (!inventory.ok) {
      view.error = inventory.body?.error ?? `GET kern/inventory answered ${inventory.status}`;
      view.model = null;
      return;
    }
    const refusal = inventoryRefusal(inventory.body);
    if (refusal) {
      view.error = refusal;
      view.model = null;
      return;
    }
    view.error = null;
    view.model = indexInventory(inventory.body);
    const after = valuesAfterFetch(view.values, values);
    view.values = after.values;
    view.refreshError = after.error;
    view.drawn = JSON.stringify([values.body?.live ?? null, values.body?.built ?? null]);
    view.colors = readColors();
    renderGrid();
  }

  async function load() {
    if (!view.dom) build();
    view.dom.status.replaceChildren(el('p', 'kern-status-line warn', 'Loading the kern inventory…'));
    view.dom.status.hidden = false;
    try {
      await loadInventory();
    } catch (error) {
      view.error = `The kern inventory did not load: ${error.message}`;
      view.model = null;
    }
    renderStatus();
  }

  function applySelection(pair, facing) {
    if (!view.model) return;
    const chosen = selectionFromState(view.model, pair, facing);
    view.cursor = chosen.cursor;
    view.facing = chosen.facing;
    placeCursor();
    renderDetail();
  }

  // Show the view with the selection the URL hash names, loading the inventory the first time.
  async function show({ pair = null, facing = null } = {}) {
    view.requested = { pair, facing };
    if (!view.loading) view.loading = load();
    await view.loading;
    if (!view.model) return;
    const before = stateFromSelection(view.model, view.cursor, view.facing);
    if (!view.dom.detail.firstChild || before.pair !== pair || before.facing !== facing) applySelection(pair, facing);
  }

  // Load the inventory again after a load that failed (before the first build, or while the server restarted), and show the selection the hash last named.
  async function retryLoad() {
    view.loading = load();
    await view.loading;
    if (view.model) applySelection(view.requested.pair, view.requested.facing);
  }

  // Fetch the values again, and the inventory too when the server holds another one, and redraw. A failed fetch keeps the values the view has and says so; a view whose inventory never loaded tries the load again.
  async function refresh() {
    if (view.pending || !view.dom) return;
    if (!view.model) {
      view.pending = retryLoad();
      try {
        await view.pending;
      } finally {
        view.pending = null;
      }
      return;
    }
    view.pending = (async () => {
      const values = await fetchJson('kern').catch((error) => ({ ok: false, status: 0, body: null, error }));
      const after = valuesAfterFetch(view.values, values);
      view.refreshError = after.error;
      if (after.error) {
        renderStatus();
        return;
      }
      const body = values.body;
      const drawn = JSON.stringify([body?.live ?? null, body?.built ?? null]);
      const changed = drawn !== view.drawn;
      view.drawn = drawn;
      view.values = after.values;
      if (inventoryMoved(view.model, view.values)) {
        const selection = stateFromSelection(view.model, view.cursor, view.facing);
        await loadInventory();
        if (view.model) applySelection(selection.pair, selection.facing);
      } else if (changed) {
        paintRows();
        renderDetail();
      }
      renderStatus();
    })();
    try {
      await view.pending;
    } finally {
      view.pending = null;
    }
  }

  function handle(action) {
    if (!view.model) return;
    const entry = runePairAt(view.model, view.cursor.row, view.cursor.col);
    if (action === 'kern-up' || action === 'kern-down' || action === 'kern-left' || action === 'kern-right') {
      setCursor(moveGridCursor(view.cursor, action, view.model.size));
    } else if (action === 'kern-detail') {
      view.dom.detail.focus();
    } else if (action === 'kern-grid') {
      view.dom.grid.focus();
    } else if (action === 'kern-prev-pair' || action === 'kern-next-pair') {
      setFacing(moveFacing(view.facing, action === 'kern-next-pair' ? 1 : -1, entry?.pairs.length ?? 0));
    } else if (action === 'kern-jump') {
      view.dom.input.focus();
      view.dom.input.select();
    }
  }

  // The pane the view's keys act in (`paneFor`): the detail panel or the grid while it has focus, and the page anywhere else.
  function pane() {
    return paneFor(document.activeElement, view.dom?.grid, view.dom?.detail);
  }

  return { show, refresh, handle, pane };
}
