// The kerning view's model (`#view=kern`, rebuild/KERN-PLAN.md section 6) as pure functions over the build's inventory (`kern-inventory.json`, rebuild/pipeline/kern_inventory.py) and the server's values (`GET /kern`, rebuild/review/kern_routes.py). Nothing here shapes text or touches the DOM: a value is a whole-pixel shift of every glyph after its junction, applied to the inventory's pen positions.

export const INVENTORY_FORMAT = 'ams-m1-kern-inventory/1';
export const VALUES_FORMAT = 'ams-kern-values/1';
export const WORD = 'word';
export const ACROSS_ZWNJ = 'zwnj';
export const OVERLAY = 'ss10';
const INVENTORY_LISTS = ['configs', 'runes', 'glyphs', 'sides', 'pairs', 'joined', 'texts', 'rune_pairs'];
const REACH_PHRASES = { [WORD]: 'mid-word', [ACROSS_ZWNJ]: 'across a ZWNJ', [OVERLAY]: 'under ss10' };
const HEIGHT_PHRASES = { baseline: 'at the baseline', 'x-height': 'at the x-height', y6: 'at y=6', top: 'at the top' };
const CURSOR_STEPS = {
  'kern-up': [-1, 0],
  'kern-down': [1, 0],
  'kern-left': [0, -1],
  'kern-right': [0, 1],
};

// Why the app cannot use `doc` as an inventory, or null when it can.
export function inventoryRefusal(doc) {
  if (!doc || typeof doc !== 'object') return 'The kern inventory is not a JSON object.';
  if (doc.format !== INVENTORY_FORMAT) {
    return `The kern inventory is format ${doc.format ?? 'unknown'}, and this page reads ${INVENTORY_FORMAT}; reload the page after the next build.`;
  }
  for (const key of INVENTORY_LISTS) {
    if (!Array.isArray(doc[key])) return `The kern inventory has no ${key} list.`;
  }
  return null;
}

export function runePairKey(lead, follow) {
  return `${lead}:${follow}`;
}

// The inventory with the lookups the view makes: each rune's index by name, each rune pair's entry by its two rune indexes, and each facing pair's key, its two sides' names, by which the server's values name it.
export function indexInventory(doc) {
  const runeIndex = new Map();
  for (const [index, rune] of doc.runes.entries()) runeIndex.set(rune.name, index);
  const runePairs = new Map();
  for (const entry of doc.rune_pairs) runePairs.set(runePairKey(entry.lead, entry.follow), entry);
  const pairKeys = [];
  const pairByKey = new Map();
  for (const [index, pair] of doc.pairs.entries()) {
    const key = `${doc.sides[pair.lead].name}|${doc.sides[pair.follow].name}`;
    pairKeys.push(key);
    pairByKey.set(key, index);
  }
  return { doc, runeIndex, runePairs, pairKeys, pairByKey, size: doc.runes.length };
}

export function runePairAt(model, lead, follow) {
  return model.runePairs.get(runePairKey(lead, follow)) ?? null;
}

export function hasFacingPairs(entry) {
  return Boolean(entry && entry.pairs.length > 0);
}

// The server's values by facing pair key: the live ones the rune files on disk resolve to, with their deciders, and the built font's. While the live ones do not resolve (`liveError`), the view shows the built ones.
export function indexValues(payload) {
  const live = new Map();
  const built = new Map();
  if (payload?.format !== VALUES_FORMAT) {
    return { live, built, liveError: null, builtPresent: false, payload: null };
  }
  for (const value of payload.live?.values ?? []) live.set(`${value.lead}|${value.follow}`, { by: value.by, deciders: value.deciders ?? [] });
  for (const [lead, follow, by] of payload.built?.values ?? []) built.set(`${lead}|${follow}`, by);
  return {
    live,
    built,
    liveError: payload.live?.error ?? null,
    builtPresent: Boolean(payload.built?.present),
    payload,
  };
}

// The values after a fetch of `GET kern` (`{ok, status, body, error}`): the new ones on an answer the view can read, and otherwise the ones it had, with why the fetch failed, so a failed poll never redraws every value as 0.
export function valuesAfterFetch(previous, fetched) {
  if (fetched?.ok && fetched.body?.format === VALUES_FORMAT) return { values: indexValues(fetched.body), error: null };
  let error;
  if (fetched?.error) error = fetched.error.message ?? String(fetched.error);
  else if (fetched?.ok) error = 'GET kern answered with no kern values';
  else error = `GET kern answered ${fetched?.status ?? 'nothing'}`;
  return { values: previous ?? indexValues(null), error };
}

export function builtValue(model, values, pairId) {
  return values?.built.get(model.pairKeys[pairId]) ?? 0;
}

export function liveValue(model, values, pairId) {
  if (!values) return 0;
  if (values.liveError) return builtValue(model, values, pairId);
  return values.live.get(model.pairKeys[pairId])?.by ?? 0;
}

// Whether the value the view shows for a facing pair is one the built font does not have yet.
export function notInFont(model, values, pairId) {
  if (!values?.payload || values.liveError) return false;
  return liveValue(model, values, pairId) !== builtValue(model, values, pairId);
}

export function decidersOf(model, values, pairId) {
  if (!values || values.liveError) return [];
  return values.live.get(model.pairKeys[pairId])?.deciders ?? [];
}

function fileName(path) {
  return String(path ?? '').split('/').pop();
}

// The deciding records as the view names them, `qsNo.yaml kern[0]`, or null when no record decides the value.
export function deciderText(deciders) {
  if (!deciders || deciders.length === 0) return null;
  return deciders.map((decider) => `${fileName(decider.file)} ${decider.path}${decider.inherited ? ' (inherited)' : ''}`).join(', ');
}

export function reachesOf(model, entry) {
  const reaches = new Set();
  for (const id of entry?.pairs ?? []) for (const reach of model.doc.pairs[id].reach) reaches.add(reach);
  return reaches;
}

// The chip a rune pair shows when none of its facing pairs is reached mid-word: the two letters meet only across a ZWNJ, only under ss10, or only in those two ways. Null for a pair a break mid-word reaches, and for one with no facing pair.
export function reachChip(model, entry) {
  const reaches = reachesOf(model, entry);
  if (reaches.size === 0 || reaches.has(WORD)) return null;
  if (reaches.has(ACROSS_ZWNJ) && reaches.has(OVERLAY)) return 'only across a ZWNJ or under ss10';
  return reaches.has(ACROSS_ZWNJ) ? 'only across a ZWNJ' : 'only under ss10';
}

export function reachText(reach) {
  return reach.map((one) => REACH_PHRASES[one] ?? one).join(', ');
}

export function heightPhrase(height) {
  return HEIGHT_PHRASES[height] ?? `at ${height}`;
}

export function configNames(model, configs) {
  return configs.map((index) => model.doc.configs[index]).join(', ');
}

export function signed(value) {
  if (value > 0) return `+${value}`;
  if (value < 0) return `−${-value}`;
  return '0';
}

function clamp(value, low, high) {
  return Math.min(Math.max(value, low), high);
}

// The grid cursor after an arrow key, held inside the grid.
export function moveGridCursor(cursor, action, size) {
  const step = CURSOR_STEPS[action];
  if (!step || size === 0) return cursor;
  return { row: clamp(cursor.row + step[0], 0, size - 1), col: clamp(cursor.col + step[1], 0, size - 1) };
}

export function moveFacing(index, delta, count) {
  if (count === 0) return 0;
  return clamp(index + delta, 0, count - 1);
}

function normalName(name) {
  return name.toLowerCase().replace(/[·.’'\-\s]/gu, '');
}

// The rune pair a query names, `·no·tea` or `no tea` or `·day+utter·tea`, as two rune indexes, or null when the query does not name exactly two runes the inventory has.
export function pairFromQuery(query, model) {
  const byName = new Map();
  for (const [index, rune] of model.doc.runes.entries()) {
    byName.set(normalName(rune.label), index);
    byName.set(normalName(rune.name.replace(/^qs/u, '').replaceAll('_qs', '+')), index);
  }
  const tokens = String(query ?? '')
    .split(/[·.\s]+/u)
    .filter((token) => token !== '');
  if (tokens.length !== 2) return null;
  const found = tokens.map((token) => byName.get(normalName(token)));
  if (found.some((index) => index === undefined)) return null;
  return { lead: found[0], follow: found[1] };
}

// The view's selection from the URL hash's `pair` (`qsNo:qsTea`) and `facing` (a facing pair's key), with the grid cursor at the top left and the first facing pair when the hash names none the inventory has.
export function selectionFromState(model, pair, facing) {
  let cursor = { row: 0, col: 0 };
  const [lead, follow] = String(pair ?? '').split(':');
  if (model.runeIndex.has(lead) && model.runeIndex.has(follow)) {
    cursor = { row: model.runeIndex.get(lead), col: model.runeIndex.get(follow) };
  }
  const entry = runePairAt(model, cursor.row, cursor.col);
  const facingIndex = facing && entry ? entry.pairs.findIndex((id) => model.pairKeys[id] === facing) : -1;
  return { cursor, facing: Math.max(facingIndex, 0) };
}

export function stateFromSelection(model, cursor, facingIndex) {
  const lead = model.doc.runes[cursor.row]?.name;
  const follow = model.doc.runes[cursor.col]?.name;
  if (!lead || !follow) return { pair: null, facing: null };
  const entry = runePairAt(model, cursor.row, cursor.col);
  const id = entry?.pairs[facingIndex];
  return { pair: `${lead}:${follow}`, facing: id === undefined ? null : model.pairKeys[id] };
}

// Each glyph's pen position in a text once every break before it is moved by its facing pair's value. A join's gap is -1 and never moves.
export function shiftedPositions(text, valueOfPair) {
  const positions = [];
  let shift = 0;
  for (const [index, x] of text.x.entries()) {
    if (index > 0) {
      const gap = text.gaps[index - 1];
      if (gap >= 0) shift += valueOfPair(gap);
    }
    positions.push(x + shift);
  }
  return positions;
}

// The closest ink across a junction, from each side's squares ({x, y}): the blank pixels between the left side's rightmost ink and the right side's leftmost ink on the row where they are closest, or, with no row inked on both sides, between their boxes, with `overlap` set when the two sides ink one pixel, the kern stage's own measure of a collision (`defects._check_contact`). A negative gap with no shared pixel means strokes that pass each other. Null when a side has no ink.
export function closestInk(left, right) {
  const rightmost = new Map();
  const leftmost = new Map();
  const inked = new Set();
  for (const square of left) {
    rightmost.set(square.y, Math.max(rightmost.get(square.y) ?? -Infinity, square.x));
    inked.add(`${square.x},${square.y}`);
  }
  let overlap = false;
  for (const square of right) {
    leftmost.set(square.y, Math.min(leftmost.get(square.y) ?? Infinity, square.x));
    if (inked.has(`${square.x},${square.y}`)) overlap = true;
  }
  if (rightmost.size === 0 || leftmost.size === 0) return null;
  let closest = null;
  for (const [y, x] of rightmost) {
    if (!leftmost.has(y)) continue;
    const gap = leftmost.get(y) - x - 1;
    if (closest === null || gap < closest.gap) closest = { gap, y, shared: true, overlap };
  }
  if (closest) return closest;
  return { gap: Math.min(...leftmost.values()) - Math.max(...rightmost.values()) - 1, y: null, shared: false, overlap };
}

// The worst of several junctions' closest ink: a shared pixel first, then the smallest gap. Null when none has ink.
export function worstInk(closests) {
  let worst = null;
  for (const closest of closests) {
    if (!closest) continue;
    if (worst === null || (closest.overlap && !worst.overlap) || (closest.overlap === worst.overlap && closest.gap < worst.gap)) worst = closest;
  }
  return worst;
}

export function inkGapText(closest) {
  if (!closest) return { level: '', text: '' };
  if (closest.overlap) return { level: 'over', text: 'ink overlaps' };
  if (!closest.shared) {
    return closest.gap < 0
      ? { level: '', text: `no row inked on both sides; the boxes overlap by ${-closest.gap} px` }
      : { level: '', text: `no row inked on both sides; boxes ${closest.gap} px apart` };
  }
  if (closest.gap < 0) return { level: 'touch', text: `strokes pass each other at y=${closest.y}, sharing no pixel` };
  if (closest.gap === 0) return { level: 'touch', text: `ink touches at y=${closest.y}` };
  return { level: '', text: `closest ink ${closest.gap} px, at y=${closest.y}` };
}

// Where a text holds two runes side by side, for the Manual words the detail panel lists: the first break between them (`joined` false), or, in a word that holds them only joined, the first join and its joined group. Null when the two runes never meet in the text.
export function textFocus(model, text, lead, follow) {
  let joined = null;
  for (const [gap, pairId] of text.gaps.entries()) {
    const left = model.doc.glyphs[text.glyphs[gap]].rune;
    const right = model.doc.glyphs[text.glyphs[gap + 1]].rune;
    if (left !== lead || right !== follow) continue;
    if (pairId >= 0) return { gap, joined: false, group: null };
    joined ??= { gap, joined: true, group: text.joins?.[gap] ?? null };
  }
  return joined;
}

// The Manual texts a rune pair lists, the ones that hold the two runes at a break first and those that hold them only joined after, each with its focus (`textFocus`), in the inventory's order within each.
export function manualTexts(model, entry) {
  const breaks = [];
  const joins = [];
  for (const id of entry?.manual ?? []) {
    const focus = textFocus(model, model.doc.texts[id], entry.lead, entry.follow);
    if (!focus) continue;
    (focus.joined ? joins : breaks).push({ id, focus });
  }
  return [...breaks, ...joins];
}

// Which pane the kerning view's keys act in, from the focused element: `detail` inside the detail panel, `grid` inside the grid, and `page` anywhere else, a header button among them, where Enter keeps its own meaning.
export function paneFor(active, grid, detail) {
  if (active && detail?.contains(active)) return 'detail';
  if (active && grid?.contains(active)) return 'grid';
  return 'page';
}

function sameStamp(one, other) {
  return Boolean(one && other && one.tables === other.tables && one.manual === other.manual);
}

// Whether the server holds another inventory than the one the view loaded, so the view should fetch it again.
export function inventoryMoved(model, values) {
  const stamp = values?.payload?.inventory?.stamp;
  return Boolean(model && stamp && !sameStamp(model.doc.stamp, stamp));
}

// What the view should tell the reader about the data it draws, most pressing first: each line's `level` (`error`, `stale` or `warn`) and text. `refreshError` says why the last refresh of the values failed, while the view keeps the last values it had.
export function stalenessLines(model, values, refreshError = null) {
  const lines = [];
  const payload = values?.payload;
  if (!payload) {
    lines.push({ level: 'error', text: `The server gave no kern values${refreshError ? ` (${refreshError})` : ''}, so every value reads 0.` });
    return lines;
  }
  if (refreshError) lines.push({ level: 'warn', text: `The kern values could not be refreshed (${refreshError}); these are the last ones the server gave.` });
  if (payload.pass?.pid) lines.push({ level: 'warn', text: `A cycle pass (pid ${payload.pass.pid}) is running; the inventory and the built values may move when it ends.` });
  if (payload.tables?.current === false) {
    lines.push({ level: 'stale', text: 'The runes or the build code changed since the last build, so these are the last build’s shapes and pairs; make artifact-cycle builds them again.' });
  }
  if (payload.inventory?.present && payload.inventory.tables_current === false) {
    lines.push({ level: 'stale', text: 'The kern inventory was built over other tables than the ones on disk; the next build writes it again.' });
  }
  if (payload.inventory?.manual_current === false) {
    lines.push({ level: 'warn', text: 'The Manual pages changed since the inventory was built, so its Manual words are the last build’s.' });
  }
  if (values.liveError) {
    lines.push({ level: 'error', text: `${values.liveError.message}; the values shown are the built font’s.`, issues: values.liveError.issues ?? [] });
  } else if (!values.builtPresent) {
    lines.push({ level: 'warn', text: 'No kern-classes.json beside the built font, so no value can be checked against it.' });
  }
  if (model && inventoryMoved(model, values)) lines.push({ level: 'warn', text: 'A newer kern inventory is on the server; loading it.' });
  return lines;
}
