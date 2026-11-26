// The kerning view's DOM (`#view=kern`, rebuild/KERN-PLAN.md section 6): the grid of leading rune by following rune, the detail panel of the focused pair of runes with its editing controls, and the status lines. It is the app's second DOM module beside app.js (rebuild/REVIEW-PLAN.md §5.2), and nothing touches the DOM until `createKernView` is called. Its model and drawing are the pure modules kern.js and kern-draw.js; it fetches the inventory (`GET kern/inventory`) once and the values (`GET kern`) on every refresh, and draws every pixel from them, shaping nothing.
//
// Editing: a change applies to the drawings at once, as an entry of `view.edits` that the drawings read before the server's values. Presses on one scope within `COALESCE_MS` of each other become one `POST kern`, and the view sends one request at a time, each against the block the last reply named. A reply that took the change gives the server's values, which replace the edit; a conflict whose reply carries the block as it stands is sent once more against that block; a refusal or a second conflict drops the edit, so the drawing goes back, and shows the server's message beside the pair; a reply that says a cycle pass holds the runes keeps the edit, marked as waiting, until `GET kern` reports no pending change. Each change the server wrote or queued goes on the tab's undo history with its journal seq (`historyAfterReply`). `u` drops the newest edit not sent yet, and otherwise asks the server to undo the history's newest change by its seq (`POST kern/undo`), which cancels it while it still waits for a pass.
//
// Carried-over marks: a facing pair the importer set (`GET kern`'s `carried`) is marked on its grid cell and its detail row while it is unreviewed and keeps the imported value (`isCarried`); `g` posts a mark for the selected scope (`POST kern/reviewed`) and moves to the next carried-over facing pair (`nextAfterMark`), moving on too from a scope with nothing left to mark (the reviewed button takes a mark off instead, `reviewStep`), and `]` and `[` move the cursor to the next and the previous pair of letters that still has one (`nextCarried`).
//
// Relatives: under the facing pairs, the detail panel lists the selected facing pair's relatives (`relativesOf`, the facing pairs whose junction is the same one, mirrored, upside down or turned half round), grouped by the value each takes, and its near relatives (`nearRelativesOf`). A class of relatives that takes more than one value is kerned inconsistently (`splitStanding`): its members are marked in the grid and in their detail rows, `r` and `w` move to the next and the previous such class, `t` sets the selected facing pair to the value most of its relatives take, `v` gives its value to every relative that differs, and `a` marks it as apart on purpose (`POST kern/apart`), which leaves it out of the comparison, and moves on.
//
// Layout: the facing pairs are listed by second shape (`facingOrder`), under a line naming it when the two letters have several, and only those with the selected pair's second shape are listed in full, the others as one line each; the relatives and comparables are tiles, a small drawing with a name, a line and its buttons, the rest in the tile's tooltip.
//
// Comparables: the section's three buttons, and `c`, choose what it lists (`view.compare`): the relatives, or the facing pairs with the same second shape whose first shape is like the selected pair's (`comparablesOf` with `COMPARE_LEAD`: every letter shaped like ·Ye before half ·Way), or those with the same first shape and a like second shape. Each row gives how alike the two shapes are, how far apart the pair's ink is where it is closest (`clearanceOf`) against the selected pair's, and buttons that tighten or loosen that pair in place or set it to the selected pair's clearance.

import { formatCount } from './render.js';
import {
  BY_LIMIT,
  NEAR_BUDGET,
  COMPARE_FOLLOW,
  COMPARE_JUNCTION,
  COMPARE_LEAD,
  SCOPE_FACING,
  SCOPE_LETTERS,
  VALUES_FORMAT,
  blockOf,
  builtValue,
  carriedCount,
  carriedTrouble,
  classMembers,
  clearanceOf,
  comparablesOf,
  entryCarried,
  isCarried,
  marksOf,
  nextAfterMark,
  reviewStep,
  nextCarried,
  configNames,
  decidersOf,
  deciderText,
  deltaSet,
  facingOrder,
  facingScope,
  followsLetters,
  hasFacingPairs,
  heightPhrase,
  historyAfterReply,
  indexInventory,
  indexValues,
  inkGapText,
  inventoryMoved,
  inventoryRefusal,
  isApart,
  journalLines,
  lettersScope,
  lettersScopeOfPair,
  lettersValue,
  likenessText,
  manualTexts,
  moveFacing,
  moveGridCursor,
  nearRelativesOf,
  nextAfterApart,
  nextComparison,
  nextSplit,
  notInFont,
  pairFromQuery,
  pairLabel,
  paneFor,
  reachChip,
  reachText,
  refusalText,
  relationText,
  relationWord,
  relativesOf,
  relativesToGive,
  relativesValue,
  resendAfter,
  runePairAt,
  scopeRecord,
  selectionFromState,
  selectionOfPair,
  shownValue,
  sideLabel,
  signed,
  splitStanding,
  staleAnswer,
  stalenessLines,
  stepInOrder,
  stateFromSelection,
  valueForClearance,
  valuesAfterFetch,
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
const TILE_SCALE = 4;
const CELL_TOP = 12;
const CELL_BOTTOM = 10;
const CELL_SLACK = 4;
const STAGE_MARGIN = 1;
const COALESCE_MS = 330;
const COLOR_ROLES = [...ROLES, 'guide', 'text', 'muted', 'mark', 'carried', 'differs'];
// How many relatives a group of the detail panel lists before its "show all" button, and how many near relatives the panel lists.
const GROUP_SHOWN = 8;
const NEAR_SHOWN = 8;
const GRID_CHIPS = { 'only across a ZWNJ': 'ZWNJ', 'only under ss10': 'ss10', 'only across a ZWNJ or under ss10': 'ZWNJ ss10' };
const EDIT_STATES = { local: 'not sent yet', sending: 'being written', waiting: 'waiting for the cycle pass to end' };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

// A button of the detail panel, with its keys shown after its label the way the verdict buttons show theirs.
function button(text, action, title, keys = []) {
  const node = el('button', null, text);
  node.type = 'button';
  node.dataset.action = action;
  if (title) node.title = title;
  for (const key of keys) node.append(' ', el('kbd', null, key));
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

async function postJson(url, body) {
  try {
    const response = await fetch(url, {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    let reply = null;
    try {
      reply = await response.json();
    } catch {
      reply = null;
    }
    return { status: response.status, reply };
  } catch (error) {
    return { status: 0, reply: { message: `The server did not answer (${error.message}); the change was not written.` } };
  }
}

// Create the view in `root`, the app's `#kern` section. `toast` shows a short message; `writeSelection` puts the selection's `pair` and `facing` in the URL hash without moving the page.
export function createKernView({ root, toast, writeSelection }) {
  const view = {
    model: null,
    values: null,
    cursor: { row: 0, col: 0 },
    facing: 0,
    scopeMode: SCOPE_FACING,
    loading: null,
    error: null,
    colors: null,
    dom: null,
    cell: { width: 0, height: 0 },
    pending: null,
    drawn: null,
    refreshError: null,
    requested: { pair: null, facing: null },
    edits: new Map(),
    timers: new Map(),
    queue: [],
    sending: false,
    editCount: 0,
    history: [],
    messages: new Map(),
    seenSeq: null,
    note: null,
    split: { pairs: new Set(), classes: 0 },
    opened: new Set(),
    near: { id: null, found: [] },
    compare: COMPARE_JUNCTION,
  };

  function valueOf(id) {
    return shownValue(view.model, view.values, view.edits, id);
  }

  // The facing pairs kerned differently from a relative, with the view's changes applied.
  function stand() {
    view.split = splitStanding(view.model, view.values, valueOf);
    return view.split;
  }

  // What a change of the values redraws when it differs from the last one drawn.
  function drawnOf(body) {
    return JSON.stringify([body?.live ?? null, body?.built ?? null, body?.journal ?? null, body?.apart ?? null]);
  }

  function readColors() {
    const colors = {};
    for (const swatch of view.dom.swatches.children) colors[swatch.dataset.role] = getComputedStyle(swatch).color;
    return colors;
  }

  function buildScopeBox() {
    const box = el('div', 'kern-scope');
    const toggle = el('div', 'kern-scope-toggle');
    toggle.setAttribute('role', 'group');
    toggle.setAttribute('aria-label', 'What a change sets');
    const facingButton = button('This shape pair', 'kern-scope-facing', 'Set the selected facing pair’s own record');
    const lettersButton = button('All unjoined', 'kern-scope-letters', 'Set the record that names only the two letters, which every facing pair of the two without a record of its own takes');
    toggle.append(facingButton, lettersButton);
    const record = el('p', 'kern-record');
    const actions = el('div', 'kern-actions');
    const amount = el('span', 'kern-scope-value');
    actions.append(
      button('tighten', 'kern-tighten', 'Pull the follower a pixel closer', ['s', '←']),
      amount,
      button('loosen', 'kern-loosen', 'Push the follower a pixel away', ['f', '→']),
      button('drop own value', 'kern-drop', 'Remove this scope’s own record, so it takes what it inherits', ['0']),
      button('note', 'kern-note', 'Write the why: of this scope’s record', ['n']),
      button('reviewed', 'kern-review-toggle', 'Mark the carried-over facing pairs of this scope reviewed and move to the next one, or take the mark off when they all have it (g marks and moves on, and never takes a mark off)', ['g']),
      button('undo', 'kern-undo', 'Undo the last change', ['u']),
    );
    const why = el('p', 'kern-why');
    const noteForm = el('form', 'kern-note-form');
    noteForm.hidden = true;
    const noteInput = el('input');
    noteInput.type = 'text';
    noteInput.spellcheck = true;
    noteInput.placeholder = 'Why this pair is kerned this way (Enter saves, an empty note removes it, Escape cancels)';
    noteForm.append(noteInput);
    const message = el('p', 'kern-message');
    message.setAttribute('role', 'status');
    box.append(toggle, record, actions, why, noteForm, message);
    box.addEventListener('click', (event) => {
      const action = event.target.closest?.('button[data-action]')?.dataset.action;
      if (!action) return;
      if (action === 'kern-scope-facing' || action === 'kern-scope-letters') {
        setScope(action === 'kern-scope-letters' ? SCOPE_LETTERS : SCOPE_FACING);
      } else {
        handle(action);
      }
      if (!view.note) view.dom.detail.focus();
    });
    noteForm.addEventListener('submit', (event) => {
      event.preventDefault();
      saveNote();
    });
    noteInput.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      closeNote();
    });
    noteInput.addEventListener('input', () => {
      if (view.note) view.note.draft = noteInput.value;
    });
    return { box, facingButton, lettersButton, record, amount, why, noteForm, noteInput, message, actions };
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
    grid.setAttribute('aria-label', 'Kerning grid: the leading letter down the side, the following letter across the top; the arrows move the cursor, - and = tighten and loosen the two letters');
    const detail = el('section', 'kern-detail');
    detail.tabIndex = 0;
    detail.setAttribute('aria-label', 'The focused pair of letters; up and down choose a facing pair, left and right tighten and loosen');
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
    const relatives = el('section', 'kern-relatives');
    relatives.setAttribute('aria-label', 'Relatives of the selected facing pair');
    relatives.addEventListener('click', (event) => {
      const pressed = event.target.closest?.('button[data-action]');
      if (pressed) {
        relativesAction(pressed.dataset);
        view.dom.detail.focus();
        return;
      }
      const row = event.target.closest?.('[data-goto]');
      if (row) {
        goTo(selectionOfPair(view.model, Number(row.dataset.goto)));
        view.dom.detail.focus();
      }
    });
    root.replaceChildren(bar, status, panes, live, swatches);
    view.dom = { summary, input, status, grid, detail, live, swatches, relatives, rows: [], cursor: null, head: null, scope: buildScopeBox() };

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

  function waitingCount() {
    let count = 0;
    for (const edit of view.edits.values()) if (edit.state === 'waiting') count += 1;
    return count;
  }

  function renderStatus() {
    const lines = view.error
      ? [{ level: 'error', text: view.error }]
      : [
          ...stalenessLines(view.model, view.values, view.refreshError),
          ...journalLines(view.values, view.seenSeq),
          ...(carriedTrouble(view.values).error ? [{ level: 'error', text: `The reviewed marks cannot be read: ${carriedTrouble(view.values).error}` }] : []),
          ...(view.values?.payload?.apart?.error ? [{ level: 'error', text: `The apart marks cannot be read: ${view.values.payload.apart.error}` }] : []),
        ];
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
    const unsent = view.edits.size - waitingCount();
    const carried = carriedCount(view.model, view.values);
    const trouble = carriedTrouble(view.values);
    const split = stand().classes;
    view.dom.summary.textContent =
      `${formatCount(doc.pairs.length)} facing pairs over ${formatCount(withPairs)} pairs of letters; ${formatCount(kerned)} kerned` +
      (unbuilt ? `, ${formatCount(unbuilt)} not in the built font yet` : '') +
      (unsent ? `; ${formatCount(unsent)} change${unsent === 1 ? '' : 's'} being written` : '') +
      (carried ? `; ${formatCount(carried)} carried over from the old font and not reviewed ([ and ] find them)` : '') +
      (trouble.stale ? `; ${formatCount(trouble.stale)} carried-over or reviewed pair${trouble.stale === 1 ? ' names' : 's name'} a side this build no longer has` : '') +
      (split ? `; ${formatCount(split)} group${split === 1 ? '' : 's'} of matching shape pairs ${split === 1 ? 'is' : 'are'} kerned differently (w and r find them)` : '') +
      '.';
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
    stand();
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
      if (entryCarried(model, view.values, entry)) {
        context.fillStyle = colors.carried;
        context.textAlign = 'right';
        context.fillText('old', x0 + cell.width - 2, cell.height - 2);
      }
      if (entry.pairs.some((id) => view.split.pairs.has(id))) {
        context.fillStyle = colors.differs;
        context.textAlign = 'center';
        context.font = 'bold 11px system-ui, sans-serif';
        context.fillText('≠', x0 + cell.width / 2, CELL_TOP - 2);
        context.font = '9px system-ui, sans-serif';
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

  function stage(draw, scale = STAGE_SCALE) {
    const pixels = draw.maxX - draw.minX + 1 + 2 * STAGE_MARGIN;
    const width = pixels * scale;
    const height = STAGE_ROWS * scale;
    const canvas = el('canvas', 'kern-stage');
    const context = sizedContext(canvas, width, height);
    paintGuides(context, { width, scale, originX: 0, originY: 0, color: view.colors.guide });
    paintSquares(context, draw.squares, {
      scale,
      originX: (STAGE_MARGIN - draw.minX) * scale,
      originY: 0,
      colors: view.colors,
    });
    return canvas;
  }

  function gapLine(closest, suffix = '') {
    const gap = inkGapText(closest);
    return el('span', `kern-gap ${gap.level}`.trim(), gap.text + suffix);
  }

  // The view's change the drawing of a facing pair shows, and its state, or null.
  function editOf(id) {
    const own = view.edits.get(facingScope(view.model, id).key);
    if (own) return own;
    const letters = view.edits.get(lettersScopeOfPair(view.model, id).key);
    return letters && followsLetters(view.model, view.values, id) ? letters : null;
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
    const follows = followsLetters(model, values, id);
    if (view.scopeMode === SCOPE_LETTERS && follows) row.classList.add('in-scope');
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
    if (view.scopeMode === SCOPE_LETTERS && !follows) {
      what.append(el('span', 'kern-keeps', `keeps its own value${source ? `, from ${source}` : ''}: a change to the two letters does not move it`));
    }
    const edit = editOf(id);
    if (edit) what.append(el('span', `kern-pending ${edit.state}`, `changed here, ${EDIT_STATES[edit.state]}`));
    if (isCarried(model, values, id)) what.append(el('span', 'kern-carried', 'carried over from the old font and not reviewed yet (g marks it)'));
    if (view.split.pairs.has(id)) what.append(el('span', 'kern-differs', 'kerned differently from a relative: see Relatives below'));
    if (isApart(model, values, id)) what.append(el('span', 'kern-apart', 'kerned apart from its relatives on purpose (a takes the mark off)'));
    if (notInFont(model, values, id)) what.append(el('span', 'kern-mark', `not in the built font yet, which has ${signed(builtValue(model, values, id))}`));
    const number = el('div', `kern-value${value === 0 ? ' zero' : ''}`, signed(value));
    row.append(stage(draw), what, number);
    return row;
  }

  // A facing pair whose second shape is not the selected pair's, as one line: its two sides, its marks and its value. Selecting it opens its group in full.
  function compactFacingRow(id, index) {
    const { model, values } = view;
    const row = el('li', 'kern-facing-row compact');
    row.setAttribute('role', 'option');
    row.setAttribute('aria-selected', 'false');
    row.dataset.facing = String(index);
    const what = el('div', 'kern-what');
    what.append(el('code', null, pairLabel(model, id)));
    if (view.split.pairs.has(id)) what.append(el('span', 'kern-differs', '≠'));
    if (isCarried(model, values, id)) what.append(el('span', 'kern-carried', 'old'));
    if (notInFont(model, values, id)) what.append(el('span', 'kern-mark', 'not built'));
    const value = valueOf(id);
    row.append(what, el('div', `kern-value${value === 0 ? ' zero' : ''}`, signed(value)));
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

  function currentEntry() {
    return view.model ? runePairAt(view.model, view.cursor.row, view.cursor.col) : null;
  }

  function pairKeyOf(entry) {
    return `${view.model.doc.runes[entry.lead].name}:${view.model.doc.runes[entry.follow].name}`;
  }

  // The scope the detail panel's keys set: the selected facing pair's, or the two letters'.
  function selectedScope() {
    const entry = currentEntry();
    if (!hasFacingPairs(entry)) return null;
    if (view.scopeMode === SCOPE_LETTERS) return lettersScope(view.model, entry);
    return facingScope(view.model, entry.pairs[view.facing] ?? entry.pairs[0]);
  }

  // The value the selected scope stands at: the two letters' value, or the selected facing pair's.
  function scopeValue(entry) {
    if (view.scopeMode === SCOPE_LETTERS) return lettersValue(view.model, view.values, view.edits, entry);
    return valueOf(entry.pairs[view.facing] ?? entry.pairs[0]);
  }

  function renderScope(entry) {
    const parts = view.dom.scope;
    const doc = view.model.doc;
    const lead = doc.runes[entry.lead];
    const follow = doc.runes[entry.follow];
    parts.lettersButton.textContent = `All unjoined ${lead.label}${follow.label}`;
    parts.facingButton.setAttribute('aria-pressed', String(view.scopeMode === SCOPE_FACING));
    parts.lettersButton.setAttribute('aria-pressed', String(view.scopeMode === SCOPE_LETTERS));
    const scope = selectedScope();
    const block = blockOf(view.values, scope.rune);
    const record = scopeRecord(view.values, scope);
    const edit = view.edits.get(scope.key);
    const file = block?.file ?? `${scope.rune}.yaml`;
    const recordNodes = [el('span', 'kern-file', file)];
    if (record) recordNodes.push(el('code', null, record.line ?? `- ${record.text}`));
    else if (scope.scope === SCOPE_LETTERS) recordNodes.push(el('span', null, ` holds no record that names only ${lead.label} and ${follow.label}`));
    else recordNodes.push(el('span', null, ` holds no record of ${scope.lead} | ${scope.follow} of its own`));
    if (edit) recordNodes.push(el('span', `kern-pending ${edit.state}`, ` — ${edit.by === null ? 'dropping it' : `setting ${signed(edit.by)}`}, ${EDIT_STATES[edit.state]}`));
    parts.record.replaceChildren(...recordNodes);
    if (block?.refusal) parts.record.append(el('span', 'kern-refusal', `The editor cannot write this file’s kern block: ${block.refusal}`));
    parts.amount.textContent = signed(scopeValue(entry));
    parts.why.textContent = record?.why ? `why: ${record.why}` : '';
    parts.why.hidden = !record?.why;
    const message = view.messages.get(pairKeyOf(entry));
    parts.message.textContent = message?.text ?? '';
    parts.message.className = `kern-message ${message?.level ?? ''}`.trim();
    parts.message.hidden = !message;
    const noteOpen = Boolean(view.note && view.note.key === scope.key);
    parts.noteForm.hidden = !noteOpen;
    if (noteOpen && parts.noteInput.value !== view.note.draft) parts.noteInput.value = view.note.draft;
  }

  function renderDetail() {
    const { model } = view;
    if (!model) return;
    const typing = document.activeElement === view.dom.scope.noteInput;
    stand();
    const doc = model.doc;
    const lead = doc.runes[view.cursor.row];
    const follow = doc.runes[view.cursor.col];
    const entry = currentEntry();
    const nodes = [];
    const heading = el('h3', null, `${lead.label} → ${follow.label}`);
    const chip = entry ? reachChip(model, entry) : null;
    if (chip) heading.append(el('span', 'kern-chip', chip));
    nodes.push(heading);
    if (!hasFacingPairs(entry)) {
      nodes.push(el('p', 'kern-empty', 'These two letters have no facing pair: they never meet at a break.'));
    } else {
      renderScope(entry);
      nodes.push(view.dom.scope.box);
      const list = el('ol', 'kern-facing');
      list.setAttribute('role', 'listbox');
      list.setAttribute('aria-label', 'Facing pairs');
      const selected = model.doc.pairs[entry.pairs[view.facing] ?? entry.pairs[0]].follow;
      const several = new Set(entry.pairs.map((id) => model.doc.pairs[id].follow)).size > 1;
      let shown = null;
      for (const index of facingOrder(model, entry)) {
        const id = entry.pairs[index];
        const follow = model.doc.pairs[id].follow;
        if (several && follow !== shown) {
          const head = el('li', 'kern-facing-head', `before ${sideLabel(model, follow)}`);
          head.setAttribute('role', 'presentation');
          list.append(head);
          shown = follow;
        }
        list.append(follow === selected ? facingRow(id, index) : compactFacingRow(id, index));
      }
      nodes.push(list);
      renderRelatives();
      nodes.push(view.dom.relatives);
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
    if (typing && view.note) view.dom.scope.noteInput.focus();
  }

  function announce() {
    const { model } = view;
    const entry = currentEntry();
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
    view.note = null;
    view.opened.clear();
    placeCursor();
    renderDetail();
    announce();
    writeState();
  }

  function setFacing(index) {
    const entry = currentEntry();
    if (!hasFacingPairs(entry)) return;
    const before = view.model.doc.pairs[entry.pairs[view.facing] ?? entry.pairs[0]].follow;
    view.facing = moveFacing(index, 0, entry.pairs.length);
    view.opened.clear();
    if (view.model.doc.pairs[entry.pairs[view.facing]].follow !== before) {
      // Another second shape: its group opens in full and the one left collapses.
      renderDetail();
      view.dom.detail.querySelector('[data-facing][aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
    } else {
      for (const option of view.dom.detail.querySelectorAll('[data-facing]')) {
        const selected = Number(option.dataset.facing) === view.facing;
        option.setAttribute('aria-selected', String(selected));
        if (selected) option.scrollIntoView({ block: 'nearest' });
      }
      if (view.scopeMode === SCOPE_FACING) renderScope(entry);
      renderRelatives();
    }
    writeState();
  }

  function setScope(mode) {
    view.scopeMode = mode;
    view.note = null;
    renderDetail();
  }

  // Redraw what a change to the values or the edits moves: the grid's cells, the detail panel and the status lines.
  function redraw() {
    paintRows();
    renderDetail();
    renderStatus();
  }

  function takeValues(reply) {
    if (reply?.values?.format !== VALUES_FORMAT) return;
    noteJournal(reply.values.journal);
    view.values = indexValues(reply.values);
    view.refreshError = null;
    view.drawn = drawnOf(reply.values);
  }

  // Forget the undo history, and report every drop, when the server's journal is another file than the one the view's seqs came from.
  function noteJournal(journal) {
    const held = view.values?.payload?.journal;
    if (held && journal && held.id !== journal.id) {
      view.history = [];
      view.seenSeq = 0;
    }
  }

  function setMessage(scope, message) {
    const key = `${scope.rune}:${scope.followRune}`;
    if (message) view.messages.set(key, message);
    else view.messages.delete(key);
  }

  // Set `scope` to `by` (null drops its record) and, when given, its note, in the drawings at once, and send it once no press has come for `COALESCE_MS`, or at once with `now`.
  function change(scope, by, { why = undefined, now = false, quiet = false } = {}) {
    const previous = view.edits.get(scope.key);
    let keptWhy = why !== undefined ? why : previous?.state === 'local' ? previous.why : undefined;
    if (by === null) keptWhy = undefined;
    view.editCount += 1;
    view.edits.set(scope.key, { scope, by, why: keptWhy, state: 'local', order: view.editCount });
    clearTimeout(view.timers.get(scope.key));
    const send = () => {
      view.timers.delete(scope.key);
      enqueue({ kind: 'set', key: scope.key });
    };
    if (now) send();
    else view.timers.set(scope.key, setTimeout(send, COALESCE_MS));
    view.dom.live.textContent = `${scope.lead} | ${scope.follow}: ${by === null ? 'its own value dropped' : signed(by)}`;
    if (!quiet) redraw();
  }

  function enqueue(item) {
    view.queue.push(item);
    pump();
  }

  async function pump() {
    if (view.sending) return;
    const item = view.queue.shift();
    if (!item) return;
    view.sending = true;
    try {
      if (item.kind === 'undo') await sendUndo(item.entry);
      else if (item.kind === 'review') await sendReview(item.body);
      else if (item.kind === 'apart') await sendApart(item.body);
      else await sendSet(item.key);
    } finally {
      view.sending = false;
      pump();
    }
  }

  // Send a scope's change. A conflict whose reply carries the block as it stands (a drain moved it since the view's last poll) is sent once more against that block; only a second conflict is shown.
  async function sendSet(key, retried = false) {
    const edit = view.edits.get(key);
    if (!edit || edit.state !== 'local') return;
    const sent = { by: edit.by, why: edit.why };
    edit.state = 'sending';
    const { status, reply } = await postJson('kern', { sets: [deltaSet(view.values, edit.scope, edit.by, edit.why)] });
    takeValues(reply);
    const current = view.edits.get(key);
    const same = current === edit && current.by === sent.by && current.why === sent.why;
    view.history = historyAfterReply(view.history, status, reply, key);
    if (resendAfter(status, reply, retried) && same) {
      current.state = 'local';
      await sendSet(key, true);
      return;
    }
    if (status === 200) {
      if (same) view.edits.delete(key);
      const removed = (reply.changes ?? []).some((one) => one.after === null) && sent.by !== null;
      setMessage(edit.scope, removed ? { level: 'info', text: 'That value is what the scope takes without a record of its own, so the file holds none.' } : null);
    } else if (status === 202) {
      if (same) current.state = 'waiting';
      setMessage(edit.scope, { level: 'warn', text: reply?.message ?? 'A cycle pass holds the runes; the change is written when it ends.' });
    } else {
      if (same) view.edits.delete(key);
      else if (current?.state === 'sending') current.state = 'local';
      const text = refusalText(reply, status);
      setMessage(edit.scope, { level: 'error', text });
      toast(text.split('\n')[0]);
    }
    redraw();
  }

  // Ask the server to undo the change `entry` names by its journal seq: a change still waiting for a pass is cancelled, and a written one is reverted while it is the newest.
  async function sendUndo(entry) {
    const { status, reply } = await postJson('kern/undo', { undoes: entry.seq });
    takeValues(reply);
    if (status === 200 && reply?.status === 'cancelled') {
      const edit = view.edits.get(entry.key);
      if (edit) setMessage(edit.scope, null);
      if (edit?.state === 'waiting') view.edits.delete(entry.key);
      toast('The change that was waiting for the pass is cancelled');
    } else if (status === 200 && reply?.status === 'undone') {
      toast(`Undid the last kern change (${reply.file})`);
    } else if (status === 200) {
      toast(reply?.message ?? 'Nothing to undo');
    } else if (status === 202) {
      toast('The undo waits for the cycle pass to end');
    } else {
      toast(refusalText(reply, status).split('\n')[0]);
      const current = currentEntry();
      if (current) view.messages.set(pairKeyOf(current), { level: 'error', text: refusalText(reply, status) });
    }
    redraw();
  }

  // Undo the last change this tab made: the newest one not sent yet, in the drawings alone, or else the newest the server wrote or queued for it, named by its journal seq.
  function undo() {
    let newest = null;
    for (const [key, edit] of view.edits) {
      if (edit.state === 'local' && (!newest || edit.order > newest[1].order)) newest = [key, edit];
    }
    if (newest) {
      clearTimeout(view.timers.get(newest[0]));
      view.timers.delete(newest[0]);
      view.edits.delete(newest[0]);
      view.dom.live.textContent = 'The change was dropped before it was written.';
      redraw();
      return;
    }
    const entry = view.history.at(-1);
    if (!entry) {
      toast('Nothing this tab changed is left to undo');
      return;
    }
    view.history = view.history.slice(0, -1);
    enqueue({ kind: 'undo', entry });
  }

  function step(scope, value, delta) {
    const by = value + delta;
    if (Math.abs(by) > BY_LIMIT) {
      toast(`A kern value stays within ${BY_LIMIT} pixels either way`);
      return;
    }
    change(scope, by);
  }

  function editScope(action) {
    const entry = currentEntry();
    const scope = selectedScope();
    if (!scope) {
      toast('These two letters never meet at a break, so there is nothing to kern');
      return;
    }
    if (action === 'kern-tighten' || action === 'kern-loosen') {
      step(scope, scopeValue(entry), action === 'kern-tighten' ? -1 : 1);
    } else if (action === 'kern-drop') {
      if (!scopeRecord(view.values, scope) && !view.edits.has(scope.key)) {
        toast('This scope has no record of its own to drop');
        return;
      }
      change(scope, null);
    } else if (action === 'kern-note') {
      openNote(scope);
    }
  }

  function openNote(scope) {
    const record = scopeRecord(view.values, scope);
    const edit = view.edits.get(scope.key);
    if (!record && (!edit || edit.by === null)) {
      toast('This scope has no record of its own to hold a note; give it a value first');
      return;
    }
    view.note = { key: scope.key, draft: edit?.why ?? record?.why ?? '' };
    renderScope(currentEntry());
    view.dom.scope.noteInput.focus();
    view.dom.scope.noteInput.select();
  }

  function closeNote() {
    view.note = null;
    renderScope(currentEntry());
    view.dom.detail.focus();
  }

  function saveNote() {
    const scope = selectedScope();
    if (!scope || !view.note || view.note.key !== scope.key) return;
    const edit = view.edits.get(scope.key);
    const record = scopeRecord(view.values, scope);
    const by = edit && edit.by !== null ? edit.by : record?.by;
    const why = view.dom.scope.noteInput.value;
    view.note = null;
    if (by === undefined || by === null) return;
    change(scope, by, { why, now: true });
    view.dom.detail.focus();
  }

  function editLetters(action) {
    const entry = currentEntry();
    if (!hasFacingPairs(entry)) {
      toast('These two letters never meet at a break, so there is nothing to kern');
      return;
    }
    const scope = lettersScope(view.model, entry);
    step(scope, lettersValue(view.model, view.values, view.edits, entry), action === 'kern-letters-tighten' ? -1 : 1);
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
    view.near = { id: null, found: [] };
    const after = valuesAfterFetch(view.values, values);
    view.values = after.values;
    view.refreshError = after.error;
    view.seenSeq ??= values.body?.journal?.seq ?? null;
    view.drawn = drawnOf(values.body);
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
    focusFirst();
  }

  // Give the keys a pane when nothing on the page has focus, as when the view has just opened or reloaded: the detail panel when the selected pair of letters has a facing pair, so its keys act on the selected one at once, and the grid otherwise.
  function focusFirst() {
    const active = document.activeElement;
    if (active && active !== document.body) return;
    const target = hasFacingPairs(currentEntry()) ? view.dom.detail : view.dom.grid;
    target.focus({ preventScroll: true });
  }

  // Load the inventory again after a load that failed (before the first build, or while the server restarted), and show the selection the hash last named.
  async function retryLoad() {
    view.loading = load();
    await view.loading;
    if (view.model) applySelection(view.requested.pair, view.requested.facing);
  }

  // Fetch the values again, and the inventory too when the server holds another one, and redraw. A failed fetch keeps the values the view has and says so; a view whose inventory never loaded tries the load again. Changes waiting for a pass are dropped from the drawings once the server holds none pending, since it has written or dropped them.
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
      // A poll the server answered before a write the view has the reply of would put the old values back.
      if (view.sending || staleAnswer(view.values?.payload?.journal, body?.journal)) {
        renderStatus();
        return;
      }
      const drawn = drawnOf(body);
      let changed = drawn !== view.drawn;
      noteJournal(body?.journal);
      view.drawn = drawn;
      view.values = after.values;
      view.seenSeq ??= body?.journal?.seq ?? null;
      if (waitingCount() > 0 && body?.journal?.pending === 0) {
        for (const [key, edit] of view.edits) if (edit.state === 'waiting') view.edits.delete(key);
        changed = true;
      }
      if (inventoryMoved(view.model, view.values)) {
        const selection = stateFromSelection(view.model, view.cursor, view.facing);
        await loadInventory();
        if (view.model) applySelection(selection.pair, selection.facing);
      } else if (changed && !view.note) {
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
    const entry = currentEntry();
    if (action === 'kern-up' || action === 'kern-down' || action === 'kern-left' || action === 'kern-right') {
      setCursor(moveGridCursor(view.cursor, action, view.model.size));
    } else if (action === 'kern-detail') {
      view.dom.detail.focus();
    } else if (action === 'kern-grid') {
      view.dom.grid.focus();
    } else if (action === 'kern-prev-pair' || action === 'kern-next-pair') {
      const step = action === 'kern-next-pair' ? 1 : -1;
      const at = entry ? stepInOrder(facingOrder(view.model, entry), view.facing, step) : null;
      if (at === null) jumpCarried(step);
      else setFacing(at);
    } else if (action === 'kern-jump') {
      view.dom.input.focus();
      view.dom.input.select();
    } else if (action === 'kern-tighten' || action === 'kern-loosen' || action === 'kern-drop' || action === 'kern-note') {
      editScope(action);
    } else if (action === 'kern-letters-tighten' || action === 'kern-letters-loosen') {
      editLetters(action);
    } else if (action === 'kern-undo') {
      undo();
    } else if (action === 'kern-review' || action === 'kern-review-toggle') {
      review({ toggle: action === 'kern-review-toggle' });
    } else if (action === 'kern-next-carried' || action === 'kern-prev-carried') {
      jumpCarried(action === 'kern-next-carried' ? 1 : -1);
    } else if (action === 'kern-next-split' || action === 'kern-prev-split') {
      jumpSplit(action === 'kern-next-split' ? 1 : -1);
    } else if (action === 'kern-compare') {
      setCompare(nextComparison(view.compare));
    } else if (action === 'kern-take' || action === 'kern-give' || action === 'kern-apart') {
      if (view.compare !== COMPARE_JUNCTION) toast('t, v and a act on the same-junction relatives; c shows them');
      else if (action === 'kern-take') takeRelatives();
      else if (action === 'kern-give') giveRelatives();
      else markApart();
    }
  }

  // Choose what the relatives section lists: the same-junction relatives, or the pairs with a like first or second shape.
  function setCompare(compare) {
    view.compare = compare;
    view.opened.clear();
    renderRelatives();
  }

  // The facing pair the detail panel has selected, or null for two letters with none.
  function selectedPair() {
    const entry = currentEntry();
    return hasFacingPairs(entry) ? (entry.pairs[view.facing] ?? entry.pairs[0]) : null;
  }

  function goTo({ cursor, facing }) {
    if (cursor.row !== view.cursor.row || cursor.col !== view.cursor.col) setCursor(cursor);
    setFacing(facing);
  }

  // Move to the next (`step` 1) or the previous (`step` -1) facing pair kerned differently from a relative, past the selected pair's own class.
  function jumpSplit(step) {
    const id = selectedPair();
    const found = nextSplit(view.model, stand().pairs, view.cursor, view.facing, step, id === null ? null : classMembers(view.model, id));
    if (found) goTo(found);
    else toast(view.split.classes > 0 ? 'This is the only group of matching shape pairs kerned differently' : 'No matching shape pairs are kerned differently');
  }

  // Set the selected facing pair's own record to `value`.
  function takeValue(id, value) {
    if (value === valueOf(id)) {
      toast('This shape pair already has that value');
      return;
    }
    change(facingScope(view.model, id), value);
  }

  // Set the selected facing pair to the value most of its relatives take.
  function takeRelatives() {
    const id = selectedPair();
    if (id === null) return;
    const found = relativesValue(view.model, view.values, valueOf, id);
    if (found.value !== null) takeValue(id, found.value);
    else if (found.tied.length === 0) toast('This shape pair has no relative to take a value from');
    else toast(`Its relatives are split between ${found.tied.map(signed).join(' and ')}; choose one in the list`);
  }

  // Give the selected facing pair's value to every relative that has another and is not marked apart, each as its own record's change.
  function giveRelatives() {
    const id = selectedPair();
    if (id === null) return;
    const value = valueOf(id);
    const targets = relativesToGive(view.model, view.values, valueOf, id, value);
    if (targets.length === 0) {
      toast('Every relative not marked apart already has this value');
      return;
    }
    for (const target of targets) change(facingScope(view.model, target), value, { now: true, quiet: true });
    view.dom.live.textContent = `${signed(value)} given to ${targets.length} relative${targets.length === 1 ? '' : 's'}`;
    redraw();
  }

  function apartBody(ids, apart) {
    return { pairs: ids.map((id) => ({ lead: view.model.doc.sides[view.model.doc.pairs[id].lead].name, follow: view.model.doc.sides[view.model.doc.pairs[id].follow].name })), apart };
  }

  // Mark the selected facing pair as kerned apart from its relatives on purpose and move to the next class kerned differently, or take the mark off and stay.
  function markApart() {
    const id = selectedPair();
    if (id === null) return;
    if (isApart(view.model, view.values, id)) {
      enqueue({ kind: 'apart', body: apartBody([id], false) });
      return;
    }
    if (relativesOf(view.model, id).length === 0) {
      toast('This shape pair has no relative to be kerned apart from');
      return;
    }
    enqueue({ kind: 'apart', body: apartBody([id], true) });
    const next = nextAfterApart(view.model, view.values, valueOf, view.cursor, view.facing, [view.model.pairKeys[id]]);
    if (next) goTo(next);
    else toast('No other matching shape pairs are kerned differently');
  }

  async function sendApart(body) {
    const { status, reply } = await postJson('kern/apart', body);
    takeValues(reply);
    if (status === 200) {
      const count = reply.pairs?.length ?? 0;
      view.dom.live.textContent = `${count} facing pair${count === 1 ? '' : 's'} ${body.apart ? 'marked apart' : 'no longer marked apart'}`;
    } else {
      toast(refusalText(reply, status).split('\n')[0]);
    }
    redraw();
  }

  // A button of the relatives section: take a group's value, mark a group apart or take its marks off, or list a group in full.
  function relativesAction(data) {
    const id = selectedPair();
    if (id === null) return;
    if (data.action === 'kern-take-value') {
      takeValue(id, Number(data.value));
    } else if (data.action === 'kern-apart-group' || data.action === 'kern-together-group') {
      const apart = data.action === 'kern-apart-group';
      const ids = relativesOf(view.model, id)
        .map((relative) => relative.id)
        .filter((one) => isApart(view.model, view.values, one) !== apart && (!apart || valueOf(one) === Number(data.value)));
      if (ids.length > 0) enqueue({ kind: 'apart', body: apartBody(ids, apart) });
    } else if (data.action === 'kern-show-group') {
      view.opened.add(data.group);
      renderRelatives();
    } else if (data.action === 'kern-compare-set') {
      setCompare(data.compare);
    } else if (data.action === 'kern-row-tighten' || data.action === 'kern-row-loosen') {
      const other = Number(data.pair);
      step(facingScope(view.model, other), valueOf(other), data.action === 'kern-row-tighten' ? -1 : 1);
    } else if (data.action === 'kern-row-match') {
      const other = Number(data.pair);
      const value = valueForClearance(view.model, other, clearanceOf(view.model, id, valueOf(id)));
      if (value === null) toast('These two shapes come near each other on no row, so there is no clearance to match');
      else if (value === valueOf(other)) toast('That pair’s ink is already as far apart as this one’s');
      else if (Math.abs(value) > BY_LIMIT) toast(`A kern value stays within ${BY_LIMIT} pixels either way`);
      else change(facingScope(view.model, other), value);
    } else {
      handle(data.action);
    }
  }

  // A row button that acts on the row's own facing pair, without leaving the selected one.
  function rowButton(text, action, title, id) {
    const node = button(text, action, title);
    node.dataset.pair = String(id);
    return node;
  }

  function clearanceText(clearance) {
    return clearance === null ? 'no ink on the same or neighboring rows' : `closest ink ${clearance} px, rows one apart counted`;
  }

  // A tile of the relatives section: a facing pair drawn small as its two shapes alone at its value, its name (`label`), a line under it, and the buttons that tighten and loosen it in place. A relative's line is how its junction relates to the selected pair's; with `ownClearance` given the tile is a comparable, its line is its clearance, in color when it is not the selected pair's, and it has the button that sets it to that clearance. The rest is in the tile's tooltip, and a click on the tile goes to the pair.
  function relativeTile(relative, own, label, ownClearance = undefined) {
    const { model, values } = view;
    const pair = model.doc.pairs[relative.id];
    const value = valueOf(relative.id);
    const comparable = ownClearance !== undefined;
    const clearance = clearanceOf(model, relative.id, value);
    const tile = el('li', 'kern-tile');
    tile.dataset.goto = String(relative.id);
    const source = deciderText(decidersOf(model, values, relative.id));
    tile.title = [
      pairLabel(model, relative.id),
      comparable ? likenessText(relative) : relationText(relative),
      clearanceText(clearance),
      reachText(pair.reach),
      source ? `decided by ${source}` : 'no record: the default spacing',
      'Click to go to this shape pair',
    ].join('\n');
    const edit = editOf(relative.id);
    const line = comparable
      ? el('span', `kern-clearance${clearance === ownClearance ? '' : ' differs'}`, clearance === null ? 'no near ink' : `${clearance} px apart`)
      : el('span', 'kern-relation', relationWord(relative));
    const number = el('span', `kern-value${value === 0 ? ' zero' : ''}${comparable || value === own ? '' : ' differs'}${edit ? ' pending' : ''}`, signed(value));
    const adjust = el('div', 'kern-row-actions');
    adjust.append(
      rowButton('−', 'kern-row-tighten', 'Tighten this pair by a pixel, as its own record', relative.id),
      number,
      rowButton('+', 'kern-row-loosen', 'Loosen this pair by a pixel, as its own record', relative.id),
    );
    if (comparable) adjust.append(rowButton('=', 'kern-row-match', 'Set this pair so its ink is as far apart, where it is closest, as the selected pair’s', relative.id));
    tile.append(stage(pairDrawList(model, pair.member, value), TILE_SCALE), el('code', null, label), line, adjust);
    return tile;
  }

  // The selected facing pair as the first tile of a list, for comparison with the tiles after it.
  function ownTile(id, own, line) {
    const tile = el('li', 'kern-tile own');
    tile.title = `${pairLabel(view.model, id)}\nthe selected shape pair, drawn alone`;
    const adjust = el('div', 'kern-row-actions');
    adjust.append(el('span', `kern-value${own === 0 ? ' zero' : ''}`, signed(own)));
    tile.append(stage(pairDrawList(view.model, view.model.doc.pairs[id].member, own), TILE_SCALE), el('code', null, 'this pair'), el('span', 'kern-clearance', line), adjust);
    return tile;
  }

  function tileList(tiles) {
    const list = el('ol', 'kern-tiles');
    list.append(...tiles);
    return list;
  }

  // The buttons that choose what the relatives section lists, named after the selected pair's two shapes.
  function compareToggle(id) {
    const pair = view.model.doc.pairs[id];
    const lead = sideLabel(view.model, pair.lead);
    const follow = sideLabel(view.model, pair.follow);
    const toggle = el('div', 'kern-scope-toggle');
    toggle.setAttribute('role', 'group');
    toggle.setAttribute('aria-label', 'What to compare this shape pair with');
    const choices = [
      [COMPARE_JUNCTION, 'Same junction', 'The shape pairs whose junction is this one, mirrored, upside down or turned half round'],
      [COMPARE_LEAD, `Like ${lead} before ${follow}`, `The first shapes shaped like ${lead} that meet ${follow} at a break`],
      [COMPARE_FOLLOW, `Like ${follow} after ${lead}`, `The second shapes shaped like ${follow} that ${lead} meets at a break`],
    ];
    for (const [compare, text, title] of choices) {
      const node = button(text, 'kern-compare-set', title);
      node.dataset.compare = compare;
      node.setAttribute('aria-pressed', String(view.compare === compare));
      toggle.append(node);
    }
    toggle.append(el('kbd', null, 'c'));
    return toggle;
  }

  // The relatives section while it compares across one side: the selected pair drawn alone, the pairs that share its other side and whose shape on this side is like its own, most alike first, and behind a button the rest of that row or column of the grid.
  function comparableNodes(id) {
    const { model } = view;
    const pair = model.doc.pairs[id];
    const lead = sideLabel(model, pair.lead);
    const follow = sideLabel(model, pair.follow);
    const leading = view.compare === COMPARE_LEAD;
    const own = valueOf(id);
    const ownClearance = clearanceOf(model, id, own);
    const found = comparablesOf(model, id, view.compare);
    const nodes = [el('h4', null, leading ? `Before ${follow}: first shapes like ${lead}` : `After ${lead}: second shapes like ${follow}`)];
    const shape = leading ? lead : follow;
    nodes.push(
      el(
        'p',
        'kern-relatives-note',
        found.like.length === 0
          ? `No other ${leading ? 'first' : 'second'} shape ${leading ? `before ${follow}` : `after ${lead}`} is shaped like ${shape}.`
          : `${found.like.length} shaped like ${shape}, most alike first; a clearance in color is not this pair’s. − and + kern a pair here, = gives it this pair’s clearance.`,
      ),
    );
    const label = (comparable) => sideLabel(model, leading ? model.doc.pairs[comparable.id].lead : model.doc.pairs[comparable.id].follow);
    const ownLine = ownClearance === null ? 'no near ink' : `${ownClearance} px apart`;
    nodes.push(tileList([ownTile(id, own, ownLine), ...found.like.map((comparable) => relativeTile(comparable, own, label(comparable), ownClearance))]));
    if (found.rest.length > 0) {
      if (view.opened.has('rest')) {
        nodes.push(el('h4', null, leading ? `The other first shapes before ${follow}` : `The other second shapes after ${lead}`));
        nodes.push(tileList(found.rest.map((comparable) => relativeTile(comparable, own, label(comparable), ownClearance))));
      } else {
        const more = button(`show the other ${found.rest.length} ${leading ? `before ${follow}` : `after ${lead}`}`, 'kern-show-group', 'List every other shape pair that shares this side, most alike first');
        more.dataset.group = 'rest';
        nodes.push(more);
      }
    }
    return nodes;
  }

  function relativeGroup(name, title, relatives, own, buttons) {
    const group = el('div', 'kern-relative-group');
    const head = el('div', 'kern-relative-head');
    head.append(el('span', null, title), ...buttons);
    const open = view.opened.has(name) || relatives.length <= GROUP_SHOWN + 1;
    const list = tileList((open ? relatives : relatives.slice(0, GROUP_SHOWN)).map((relative) => relativeTile(relative, own, pairLabel(view.model, relative.id))));
    group.append(head, list);
    if (!open) {
      const more = button(`show all ${relatives.length}`, 'kern-show-group', 'List every relative of this group');
      more.dataset.group = name;
      group.append(more);
    }
    return group;
  }

  // The relatives section of the detail panel, for the selected facing pair: what its relatives are and whether they agree, the keys' buttons, the pair drawn alone for comparison, its relatives grouped by the value each takes (those that differ first, the largest group first), those marked apart, and its nearest near relatives.
  function renderRelatives() {
    const { model, values } = view;
    const box = view.dom.relatives;
    const id = selectedPair();
    if (id === null) {
      box.replaceChildren();
      return;
    }
    if (view.compare !== COMPARE_JUNCTION) {
      box.replaceChildren(compareToggle(id), ...comparableNodes(id));
      return;
    }
    const own = valueOf(id);
    const apart = isApart(model, values, id);
    const relatives = relativesOf(model, id);
    const counted = relatives.filter((relative) => !isApart(model, values, relative.id));
    const marked = relatives.filter((relative) => isApart(model, values, relative.id));
    const groups = new Map();
    for (const relative of counted) {
      const value = valueOf(relative.id);
      if (!groups.has(value)) groups.set(value, []);
      groups.get(value).push(relative);
    }
    const differing = counted.length - (groups.get(own)?.length ?? 0);
    const nodes = [compareToggle(id), el('h4', null, `Relatives of ${pairLabel(model, id)}`)];
    let note;
    if (relatives.length === 0) note = 'No other shape pair has this junction.';
    else if (apart) note = 'Marked as kerned apart from its relatives on purpose, so it is compared with none of them.';
    else if (differing === 0) note = `${counted.length === 1 ? 'The one shape pair' : `All ${counted.length} shape pairs`} with this junction ${counted.length === 1 ? 'has' : 'have'} this value.`;
    else note = `${differing} of the ${counted.length} shape pair${counted.length === 1 ? '' : 's'} with this junction ${differing === 1 ? 'has' : 'have'} another value.`;
    nodes.push(el('p', `kern-relatives-note${differing > 0 && !apart ? ' differs' : ''}`, note));
    const actions = el('div', 'kern-actions');
    actions.append(
      button('take their value', 'kern-take', 'Set this shape pair to the value most of its relatives take', ['t']),
      button('give them this value', 'kern-give', 'Set every relative that has another value, and is not marked apart, to this shape pair’s value', ['v']),
      button(apart ? 'not apart' : 'apart on purpose', 'kern-apart', 'Mark this shape pair as kerned apart from its relatives on purpose, so it is compared with none of them, and move to the next group kerned differently; on a marked pair, take the mark off', ['a']),
      button('previous', 'kern-prev-split', 'The previous group of matching shape pairs kerned differently', ['w']),
      button('next', 'kern-next-split', 'The next group of matching shape pairs kerned differently', ['r']),
    );
    nodes.push(actions);
    if (relatives.length > 0) {
      const ownClearance = clearanceOf(model, id, own);
      nodes.push(tileList([ownTile(id, own, ownClearance === null ? 'no near ink' : `${ownClearance} px apart`)]));
    }
    const ordered = [...groups].sort((one, other) => Number(one[0] === own) - Number(other[0] === own) || other[1].length - one[1].length || one[0] - other[0]);
    for (const [value, members] of ordered) {
      const count = `${members.length} relative${members.length === 1 ? '' : 's'}`;
      const buttons = [];
      if (value !== own) {
        const take = button(`take ${signed(value)}`, 'kern-take-value', `Set this shape pair to ${signed(value)}`);
        take.dataset.value = String(value);
        const part = button(members.length === 1 ? 'mark it apart' : `mark these ${members.length} apart`, 'kern-apart-group', 'Mark every relative at this value as kerned apart from the others on purpose');
        part.dataset.value = String(value);
        buttons.push(take, part);
      }
      nodes.push(relativeGroup(`value:${value}`, value === own ? `${count} at ${signed(value)}, as this one` : `${count} at ${signed(value)}`, members, own, buttons));
    }
    if (marked.length > 0) {
      nodes.push(
        relativeGroup('apart', `${marked.length} marked apart on purpose, and compared with none`, marked, own, [
          button(marked.length === 1 ? 'take the mark off' : 'take the marks off', 'kern-together-group', 'Compare these with their relatives again'),
        ]),
      );
    }
    if (view.near.id !== id) view.near = { id, found: nearRelativesOf(model, id) };
    const near = [...view.near.found].sort((one, other) => one.distance - other.distance || Number(valueOf(one.id) === own) - Number(valueOf(other.id) === own) || one.id - other.id);
    if (near.length > 0) {
      const open = view.opened.has('near') || near.length <= NEAR_SHOWN + 1;
      nodes.push(el('h4', null, 'Near relatives'));
      nodes.push(el('p', 'kern-relatives-note', `${near.length} with a junction within ${NEAR_BUDGET} px of this one, nearest first; a value may rightly differ, and they are in no count.`));
      nodes.push(tileList((open ? near : near.slice(0, NEAR_SHOWN)).map((relative) => relativeTile(relative, own, pairLabel(model, relative.id)))));
      if (!open) {
        const more = button(`show all ${near.length}`, 'kern-show-group', 'List every near relative');
        more.dataset.group = 'near';
        nodes.push(more);
      }
    }
    box.replaceChildren(...nodes);
  }

  // Move the cursor to the next (`step` 1) or the previous (`step` -1) pair of letters that still has a carried-over facing pair not reviewed.
  function jumpCarried(step) {
    const found = nextCarried(view.model, view.values, view.cursor, step);
    if (found) setCursor(found);
    else toast('No pair of letters has a carried-over value left to review');
  }

  // Mark the selected scope's carried-over facing pairs reviewed and move to the next carried-over facing pair (`nextAfterMark`), or take the mark off, staying put, when every one has it. A scope with nothing carried over is left as it is, and the selection moves on all the same.
  function review({ toggle = false } = {}) {
    const entry = currentEntry();
    const scope = selectedScope();
    const marks = scope && entry ? marksOf(view.model, view.values, scope, entry) : { imported: [], reviewed: false };
    const { post, move } = reviewStep(marks, toggle);
    if (post !== null) enqueue({ kind: 'review', body: { scope: scope.scope, lead: scope.lead, follow: scope.follow, reviewed: post } });
    if (!move) return;
    const next = nextAfterMark(view.model, view.values, view.cursor, view.facing, marks.imported);
    if (!next) {
      toast(post ? 'That was the last carried-over value left to review' : 'No carried-over value is left to review');
      return;
    }
    if (next.cursor.row !== view.cursor.row || next.cursor.col !== view.cursor.col) setCursor(next.cursor);
    setFacing(next.facing);
  }

  async function sendReview(body) {
    const { status, reply } = await postJson('kern/reviewed', body);
    takeValues(reply);
    if (status === 200) {
      const count = reply.pairs?.length ?? 0;
      view.dom.live.textContent = `${body.reviewed ? 'Marked' : 'Unmarked'} ${count} facing pair${count === 1 ? '' : 's'} reviewed`;
    } else {
      toast(refusalText(reply, status).split('\n')[0]);
    }
    redraw();
  }

  // The pane the view's keys act in (`paneFor`): the detail panel or the grid while it has focus, and the page anywhere else.
  function pane() {
    return paneFor(document.activeElement, view.dom?.grid, view.dom?.detail);
  }

  // Whether a change has not reached the server yet, so leaving the page would lose it.
  function unsent() {
    for (const edit of view.edits.values()) if (edit.state !== 'waiting') return true;
    return view.queue.length > 0;
  }

  return { show, refresh, handle, pane, unsent };
}
