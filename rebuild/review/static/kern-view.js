// The kerning view's DOM (`#view=kern`, rebuild/KERN-PLAN.md section 6): the grid of leading rune by following rune, the detail panel of the focused pair of runes with its editing controls, and the status lines. It is the app's second DOM module beside app.js (rebuild/REVIEW-PLAN.md §5.2), and nothing touches the DOM until `createKernView` is called. Its model and drawing are the pure modules kern.js and kern-draw.js; it fetches the inventory (`GET kern/inventory`) once and the values (`GET kern`) on every refresh, and draws every pixel from them, shaping nothing.
//
// Editing: a change applies to the drawings at once, as an entry of `view.edits` that the drawings read before the server's values. Presses on one scope within `COALESCE_MS` of each other become one `POST kern`, and the view sends one request at a time, each against the block the last reply named. A reply that took the change gives the server's values, which replace the edit; a conflict whose reply carries the block as it stands is sent once more against that block; a refusal or a second conflict drops the edit, so the drawing goes back, and shows the server's message beside the pair; a reply that says a cycle pass holds the runes keeps the edit, marked as waiting, until `GET kern` reports no pending change. Each change the server wrote or queued goes on the tab's undo history with its journal seq (`historyAfterReply`). `u` drops the newest edit not sent yet, and otherwise asks the server to undo the history's newest change by its seq (`POST kern/undo`), which cancels it while it still waits for a pass.
//
// Carried-over marks: a facing pair the importer set (`GET kern`'s `carried`) is marked on its grid cell and its detail row while it is unreviewed and keeps the imported value (`isCarried`); `f` posts a mark for the selected scope (`POST kern/reviewed`) and moves to the next carried-over facing pair (`nextAfterMark`), and `]` and `[` move the cursor to the next and the previous pair of letters that still has one (`nextCarried`).

import { formatCount } from './render.js';
import {
  BY_LIMIT,
  SCOPE_FACING,
  SCOPE_LETTERS,
  VALUES_FORMAT,
  blockOf,
  builtValue,
  carriedCount,
  carriedTrouble,
  entryCarried,
  isCarried,
  marksOf,
  nextAfterMark,
  nextCarried,
  configNames,
  decidersOf,
  deciderText,
  deltaSet,
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
  journalLines,
  lettersScope,
  lettersScopeOfPair,
  lettersValue,
  manualTexts,
  moveFacing,
  moveGridCursor,
  notInFont,
  pairFromQuery,
  paneFor,
  reachChip,
  reachText,
  refusalText,
  resendAfter,
  runePairAt,
  scopeRecord,
  selectionFromState,
  shownValue,
  signed,
  staleAnswer,
  stalenessLines,
  stateFromSelection,
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
const CELL_TOP = 12;
const CELL_BOTTOM = 10;
const CELL_SLACK = 4;
const STAGE_MARGIN = 1;
const COALESCE_MS = 330;
const COLOR_ROLES = [...ROLES, 'guide', 'text', 'muted', 'mark', 'carried'];
const GRID_CHIPS = { 'only across a ZWNJ': 'ZWNJ', 'only under ss10': 'ss10', 'only across a ZWNJ or under ss10': 'ZWNJ ss10' };
const EDIT_STATES = { local: 'not sent yet', sending: 'being written', waiting: 'waiting for the cycle pass to end' };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

// A button of the detail panel, with its key shown after its label the way the verdict buttons show theirs.
function button(text, action, title, key = null) {
  const node = el('button', null, text);
  node.type = 'button';
  node.dataset.action = action;
  if (title) node.title = title;
  if (key) node.append(' ', el('kbd', null, key));
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
  };

  function valueOf(id) {
    return shownValue(view.model, view.values, view.edits, id);
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
    toggle.setAttribute('aria-label', 'What a change sets (s)');
    const facingButton = button('This shape pair', 'kern-scope-facing', 'Set the selected facing pair’s own record (s switches)');
    const lettersButton = button('All unjoined', 'kern-scope-letters', 'Set the record that names only the two letters, which every facing pair of the two without a record of its own takes (s switches)');
    const switchKey = el('kbd', null, 's');
    switchKey.title = 's switches between the two scopes';
    toggle.append(facingButton, lettersButton, switchKey);
    const record = el('p', 'kern-record');
    const actions = el('div', 'kern-actions');
    const amount = el('span', 'kern-scope-value');
    actions.append(
      button('tighten', 'kern-tighten', 'Pull the follower a pixel closer', '←'),
      amount,
      button('loosen', 'kern-loosen', 'Push the follower a pixel away', '→'),
      button('drop own value', 'kern-drop', 'Remove this scope’s own record, so it takes what it inherits', '0'),
      button('note', 'kern-note', 'Write the why: of this scope’s record', 'n'),
      button('reviewed', 'kern-review', 'Mark the carried-over facing pairs of this scope reviewed and move to the next one, or take the mark off', 'f'),
      button('undo', 'kern-undo', 'Undo the last change', 'u'),
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
    root.replaceChildren(bar, status, panes, live, swatches);
    view.dom = { summary, input, status, grid, detail, live, swatches, rows: [], cursor: null, head: null, scope: buildScopeBox() };

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
    view.dom.summary.textContent =
      `${formatCount(doc.pairs.length)} facing pairs over ${formatCount(withPairs)} pairs of letters; ${formatCount(kerned)} kerned` +
      (unbuilt ? `, ${formatCount(unbuilt)} not in the built font yet` : '') +
      (unsent ? `; ${formatCount(unsent)} change${unsent === 1 ? '' : 's'} being written` : '') +
      (carried ? `; ${formatCount(carried)} carried over from the old font and not reviewed ([ and ] find them)` : '') +
      (trouble.stale ? `; ${formatCount(trouble.stale)} carried-over or reviewed pair${trouble.stale === 1 ? ' names' : 's name'} a side this build no longer has` : '') +
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
    if (isCarried(model, values, id)) what.append(el('span', 'kern-carried', 'carried over from the old font and not reviewed yet (f marks it)'));
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
    placeCursor();
    renderDetail();
    announce();
    writeState();
  }

  function setFacing(index) {
    const entry = currentEntry();
    if (!hasFacingPairs(entry)) return;
    view.facing = moveFacing(index, 0, entry.pairs.length);
    for (const option of view.dom.detail.querySelectorAll('[data-facing]')) {
      const selected = Number(option.dataset.facing) === view.facing;
      option.setAttribute('aria-selected', String(selected));
      if (selected) option.scrollIntoView({ block: 'nearest' });
    }
    if (view.scopeMode === SCOPE_FACING) renderScope(entry);
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
    view.drawn = JSON.stringify([reply.values.live ?? null, reply.values.built ?? null, reply.values.journal ?? null]);
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
  function change(scope, by, { why = undefined, now = false } = {}) {
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
    redraw();
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
    const after = valuesAfterFetch(view.values, values);
    view.values = after.values;
    view.refreshError = after.error;
    view.seenSeq ??= values.body?.journal?.seq ?? null;
    view.drawn = JSON.stringify([values.body?.live ?? null, values.body?.built ?? null, values.body?.journal ?? null]);
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
      const drawn = JSON.stringify([body?.live ?? null, body?.built ?? null, body?.journal ?? null]);
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
      setFacing(moveFacing(view.facing, action === 'kern-next-pair' ? 1 : -1, entry?.pairs.length ?? 0));
    } else if (action === 'kern-jump') {
      view.dom.input.focus();
      view.dom.input.select();
    } else if (action === 'kern-tighten' || action === 'kern-loosen' || action === 'kern-drop' || action === 'kern-note') {
      editScope(action);
    } else if (action === 'kern-letters-tighten' || action === 'kern-letters-loosen') {
      editLetters(action);
    } else if (action === 'kern-scope') {
      setScope(view.scopeMode === SCOPE_LETTERS ? SCOPE_FACING : SCOPE_LETTERS);
    } else if (action === 'kern-undo') {
      undo();
    } else if (action === 'kern-review') {
      review();
    } else if (action === 'kern-next-carried' || action === 'kern-prev-carried') {
      const found = nextCarried(view.model, view.values, view.cursor, action === 'kern-next-carried' ? 1 : -1);
      if (found) setCursor(found);
      else toast('No pair of letters has a carried-over value left to review');
    }
  }

  // Mark the selected scope's carried-over facing pairs reviewed and move to the next carried-over facing pair (`nextAfterMark`), or take the mark off, staying put, when every one has it.
  function review() {
    const entry = currentEntry();
    const scope = selectedScope();
    if (!scope || !entry) return;
    const marks = marksOf(view.model, view.values, scope, entry);
    if (marks.imported.length === 0) {
      toast('Nothing in this scope was carried over from the old font');
      return;
    }
    enqueue({ kind: 'review', body: { scope: scope.scope, lead: scope.lead, follow: scope.follow, reviewed: !marks.reviewed } });
    if (marks.reviewed) return;
    const next = nextAfterMark(view.model, view.values, view.cursor, view.facing, marks.imported);
    if (!next) {
      toast('That was the last carried-over value left to review');
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
