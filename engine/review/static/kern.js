// The kerning view's model (`#view=kern`, doc/kerning-design.md section 6) as pure functions over the build's inventory (`kern-inventory.json`, engine/pipeline/kern_inventory.py) and the server's values (`GET /kern`, engine/review/kern_routes.py). Nothing here shapes text or touches the DOM: a value is a whole-pixel shift of every glyph after its junction, applied to the inventory's pen positions.

export const INVENTORY_FORMAT = 'ams-kern-inventory/1';
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
  const imported = new Map();
  if (payload?.format !== VALUES_FORMAT) {
    return { live, built, imported, reviewed: new Set(), apart: new Set(), liveError: null, builtPresent: false, payload: null };
  }
  for (const value of payload.live?.values ?? []) live.set(`${value.lead}|${value.follow}`, { by: value.by, deciders: value.deciders ?? [] });
  for (const [lead, follow, by] of payload.built?.values ?? []) built.set(`${lead}|${follow}`, by);
  for (const entry of payload.carried?.pairs ?? []) imported.set(`${entry.lead}|${entry.follow}`, entry);
  return {
    live,
    built,
    imported,
    reviewed: new Set(payload.carried?.reviewed ?? []),
    apart: new Set(payload.apart?.pairs ?? []),
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

// The view's selection from the URL hash's `pair` (`qsNo:qsTea`) and `facing` (a facing pair's key), with the grid cursor at the top left and the first facing pair when the hash names none the inventory has. In list mode (`list`, `resolvePairList`'s) a `pair` that is no listed pair the inventory knows gives way to the first listed pair it knows.
export function selectionFromState(model, pair, facing, list = null) {
  let cursor = null;
  const [lead, follow] = String(pair ?? '').split(':');
  if (model.runeIndex.has(lead) && model.runeIndex.has(follow)) {
    cursor = { row: model.runeIndex.get(lead), col: model.runeIndex.get(follow) };
  }
  const first = list?.find((one) => one.cursor)?.cursor;
  if (first && (!cursor || listedAt(list, cursor) < 0)) cursor = first;
  cursor ??= { row: 0, col: 0 };
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

// List mode (doc/kerning-design.md section 6). The URL hash's `pairs` lists pairs of letters to judge, in order, comma-separated, each `lead:follow` by rune name, a ligature named as the inventory names it (`qsDay_qsUtter:qsTea`). Each listed pair is `{name, lead, follow}`, listed once, where it first appears; a token that is not two names around one colon keeps its `name` with `lead` and `follow` null.
export function parsePairList(text) {
  const listed = [];
  const seen = new Set();
  for (const token of String(text ?? '').split(',')) {
    const name = token.trim();
    if (name === '' || seen.has(name)) continue;
    seen.add(name);
    const parts = name.split(':');
    const named = parts.length === 2 && parts[0] !== '' && parts[1] !== '';
    listed.push({ name, lead: named ? parts[0] : null, follow: named ? parts[1] : null });
  }
  return listed;
}

// The hash's `pairs` for a list, or null for an empty one, which is no list mode.
export function pairListText(listed) {
  return listed.length > 0 ? listed.map((one) => one.name).join(',') : null;
}

// Each listed pair against the inventory: its grid `cursor` and rune pair `entry` when the inventory has both runes, and otherwise a null cursor and entry with `missing`, the names it lacks (the whole token for one that is not `lead:follow`). A listed pair whose entry has no facing pair is two letters that never meet at a break.
export function resolvePairList(model, listed) {
  return listed.map((one) => {
    const missing = one.lead === null ? [one.name] : [...new Set([one.lead, one.follow])].filter((name) => !model.runeIndex.has(name));
    if (missing.length > 0) return { ...one, cursor: null, entry: null, missing };
    const cursor = { row: model.runeIndex.get(one.lead), col: model.runeIndex.get(one.follow) };
    return { ...one, cursor, entry: runePairAt(model, cursor.row, cursor.col), missing };
  });
}

// The index of the listed pair at the grid cursor `cursor`, or -1 when no listed pair is there.
export function listedAt(list, cursor) {
  return list.findIndex((one) => one.cursor !== null && one.cursor.row === cursor.row && one.cursor.col === cursor.col);
}

// The cursors of the listed pairs the inventory knows, in the list's order: the cells the mark key's walk visits in list mode.
export function listCells(list) {
  return list.filter((one) => one.cursor !== null).map((one) => one.cursor);
}

// The index of the listed pair the inventory knows `step` places from index `at` (`step` 1 the next, -1 the previous), passing over those it does not know, or null past either end.
export function stepListed(list, at, step) {
  for (let index = at + step; index >= 0 && index < list.length; index += step) {
    if (list[index].cursor !== null) return index;
  }
  return null;
}

const LIST_STEPS = { 'kern-up': -1, 'kern-left': -1, 'kern-down': 1, 'kern-right': 1 };

// The listed pair an arrow key moves to from index `at`: up and left the previous one the inventory knows, down and right the next, held at either end.
export function moveListCursor(list, at, action) {
  const step = LIST_STEPS[action];
  return step ? (stepListed(list, at, step) ?? at) : at;
}

// Why a listed pair has nothing to draw, or null when it has facing pairs: a token that is not `lead:follow`, a rune the inventory lacks, or two letters that never meet at a break. `unknown` marks the first two.
export function listedNote(item) {
  if (item.lead === null) return { unknown: true, text: 'not two letters written lead:follow' };
  if (item.missing.length > 0) return { unknown: true, text: `the kern inventory has no ${item.missing.join(' or ')}` };
  if (!hasFacingPairs(item.entry)) return { unknown: false, text: 'never meet at a break' };
  return null;
}

// The line above the list: how many pairs of letters it lists, and how many of them the inventory does not know or never meet at a break.
export function listLine(list) {
  let unknown = 0;
  let apart = 0;
  for (const item of list) {
    const note = listedNote(item);
    if (note?.unknown) unknown += 1;
    else if (note) apart += 1;
  }
  const parts = [];
  if (unknown) parts.push(`${unknown} not in the kern inventory`);
  if (apart) parts.push(`${apart} whose letters never meet at a break`);
  return `The link lists ${list.length} pair${list.length === 1 ? '' : 's'} of letters${parts.length ? `, ${parts.join(' and ')}` : ''}.`;
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

// Editing (doc/kerning-design.md section 6). A scope names the record an edit sets: the letters scope of two runes is the record naming only the two families, and a facing pair's scope is the record whose keys are its two sides' keys, which is the letters scope when both sides are whole runes. Each lives in the leading rune's file. `edits` maps a scope's key to the view's change not yet confirmed by the server, `{scope, by, why, state}`, `by` null to drop the record.
export const SCOPE_FACING = 'facing';
export const SCOPE_LETTERS = 'letters';
// The largest |by| a kern record takes: engine/schema/rune.schema.json's bound, which the server's `kern_routes.BY_LIMIT` holds too.
export const BY_LIMIT = 64;

export function lettersScope(model, entry) {
  const lead = model.doc.runes[entry.lead].name;
  const follow = model.doc.runes[entry.follow].name;
  return { scope: SCOPE_LETTERS, key: `${SCOPE_LETTERS}:${lead}|${follow}`, rune: lead, followRune: follow, lead, follow };
}

export function facingScope(model, pairId) {
  const pair = model.doc.pairs[pairId];
  const lead = model.doc.sides[pair.lead];
  const follow = model.doc.sides[pair.follow];
  const rune = model.doc.runes[lead.rune].name;
  if (lead.name === rune && follow.name === model.doc.runes[follow.rune].name) {
    return lettersScope(model, { lead: lead.rune, follow: follow.rune });
  }
  return { scope: SCOPE_FACING, key: `${SCOPE_FACING}:${lead.name}|${follow.name}`, rune, followRune: model.doc.runes[follow.rune].name, lead: lead.name, follow: follow.name };
}

export function lettersScopeOfPair(model, pairId) {
  const pair = model.doc.pairs[pairId];
  return lettersScope(model, { lead: model.doc.sides[pair.lead].rune, follow: model.doc.sides[pair.follow].rune });
}

// A rune file's kern block as `GET /kern` reports it, or null.
export function blockOf(values, rune) {
  return values?.payload?.runes?.[rune] ?? null;
}

// The record of the rune file that is the scope's own, or null.
export function scopeRecord(values, scope) {
  const block = blockOf(values, scope.rune);
  return block?.records.find((record) => record.sides && record.sides[0] === scope.lead && record.sides[1] === scope.follow) ?? null;
}

// Whether a facing pair takes the value set on its two letters: it has no record of its own, and no record of its leading rune's block that narrows below the two letters decides it. A record a ligature inherits from its last letter never stands in the way, since the ligature's own block outranks it.
export function followsLetters(model, values, pairId) {
  const facing = facingScope(model, pairId);
  if (facing.scope === SCOPE_LETTERS) return true;
  if (scopeRecord(values, facing)) return false;
  const block = blockOf(values, facing.rune);
  for (const decider of decidersOf(model, values, pairId)) {
    if (decider.inherited) continue;
    const record = block?.records.find((one) => one.path === decider.path);
    if (record?.narrow) return false;
  }
  return true;
}

function editedValue(edit, fallback) {
  return edit.by ?? fallback;
}

// The value the letters scope stands at: the view's change to it, its record, or with neither the value its facing pairs take that no narrower record decides, the representative's first, and 0 when there is none.
export function lettersValue(model, values, edits, entry) {
  const scope = lettersScope(model, entry);
  const edit = edits?.get(scope.key);
  if (edit) return editedValue(edit, 0);
  const record = scopeRecord(values, scope);
  if (record) return record.by;
  for (const id of [entry.rep ?? entry.pairs[0], ...entry.pairs]) {
    if (id !== undefined && followsLetters(model, values, id)) return liveValue(model, values, id);
  }
  return 0;
}

// The value a facing pair shows with the view's changes: its own scope's change; else its letters' change, when it follows its letters; else the server's value.
export function shownValue(model, values, edits, pairId) {
  if (edits && edits.size > 0) {
    const facing = facingScope(model, pairId);
    const own = edits.get(facing.key);
    const letters = lettersScopeOfPair(model, pairId);
    if (own && facing.scope === SCOPE_FACING) {
      const lettersEdit = edits.get(letters.key);
      return editedValue(own, lettersEdit ? editedValue(lettersEdit, 0) : (scopeRecord(values, letters)?.by ?? 0));
    }
    const lettersEdit = edits.get(letters.key);
    if (lettersEdit && followsLetters(model, values, pairId)) return editedValue(lettersEdit, 0);
  }
  return liveValue(model, values, pairId);
}

// One set of a `POST /kern` delta: the scope, the value (null drops the record), the block it was made against, and the note when one is given. A drop carries no note, since the record that would hold it goes.
export function deltaSet(values, scope, by, why = undefined) {
  const set = { scope: scope.scope, lead: scope.lead, follow: scope.follow, by, base: blockOf(values, scope.rune)?.sha256 ?? '' };
  if (why !== undefined && by !== null) set.why = why;
  return set;
}

// The view's undo history after a write's reply: a change the server wrote (`applied`) or queued behind a running pass (`pending`) is pushed with its journal seq and its scope's key, newest last; a reply that changed nothing, or refused, pushes nothing, so `u` never reaches past it.
export function historyAfterReply(history, status, reply, key) {
  if (status === 200 && reply?.status === 'applied' && Number.isInteger(reply.seq)) return [...history, { seq: reply.seq, key, waiting: false }];
  if (status === 202 && reply?.status === 'pending' && Number.isInteger(reply.seq)) return [...history, { seq: reply.seq, key, waiting: true }];
  return history;
}

// Whether a write's reply asks the view to send the press once more: a conflict whose reply carries the block as it stands (a drain or another tab moved it since the view's last poll), on the first try.
export function resendAfter(status, reply, retried) {
  return status === 409 && Boolean(reply?.block) && !retried;
}

// Whether a poll's answer is older than the values the view holds, from a write's reply: the same journal (its `id`) at an earlier seq. A journal deleted or replaced since starts its seqs over under another id, and its answers are taken.
export function staleAnswer(held, fresh) {
  if (!held || !fresh) return false;
  return held.id === fresh.id && (fresh.seq ?? 0) < (held.seq ?? 0);
}

// What the view says about a write the server did not take, from its reply: the message and the build's first issues.
export function refusalText(reply, status, shown = 3) {
  if (!reply || typeof reply !== 'object') return `The server answered ${status} with no reply it could read; the change was not written.`;
  const issues = Array.isArray(reply.issues) ? reply.issues : [];
  const lines = issues.slice(0, shown).map(([code, text]) => `${code}: ${text}`);
  if (issues.length > shown) lines.push(`and ${issues.length - shown} more`);
  return [reply.message ?? `The server answered ${status}.`, ...lines].join('\n');
}

// The journal's requests the view should report: how many wait for a pass to end, and each dropped since `seenSeq`, the journal's last seq when the view loaded.
export function journalLines(values, seenSeq) {
  const journal = values?.payload?.journal;
  if (!journal) return [];
  const lines = [];
  if (journal.pending > 0) {
    lines.push({ level: 'warn', text: `${journal.pending} change${journal.pending === 1 ? '' : 's'} wait${journal.pending === 1 ? 's' : ''} for the cycle pass to end; the server writes ${journal.pending === 1 ? 'it' : 'them'} then.` });
  }
  for (const dropped of journal.dropped ?? []) {
    if (seenSeq !== null && seenSeq !== undefined && dropped.seq <= seenSeq) continue;
    lines.push({ level: 'error', text: `A change made while a pass ran was dropped when it ended: ${dropped.reason}` });
  }
  return lines;
}

// Carried-over marks (doc/kerning-design.md section 8). A facing pair is carried over and unreviewed while the import set it (`imported.json`), the author has not marked it (`reviewed.json`), and it still has the value the import gave it.
export function isCarried(model, values, pairId) {
  const key = model.pairKeys[pairId];
  const entry = values?.imported?.get(key);
  if (!entry || values.reviewed.has(key)) return false;
  return liveValue(model, values, pairId) === entry.by;
}

export function entryCarried(model, values, entry) {
  return Boolean(entry) && entry.pairs.some((id) => isCarried(model, values, id));
}

export function carriedCount(model, values) {
  let count = 0;
  for (const id of model.doc.pairs.keys()) if (isCarried(model, values, id)) count += 1;
  return count;
}

// The grid cursors a walk from `cursor` visits, forward (`step` 1) or back (`step` -1), wrapping round to `cursor` itself last: every cell of the grid in reading order, or with `cells` (list mode's `listCells`) those cells in their order.
function* walkFrom(model, cursor, step, cells) {
  if (cells) {
    const start = cells.findIndex((one) => one.row === cursor.row && one.col === cursor.col);
    for (let offset = 1; offset <= cells.length; offset += 1) yield cells[(((start + step * offset) % cells.length) + cells.length) % cells.length];
    return;
  }
  const size = model.size;
  const total = size * size;
  const start = cursor.row * size + cursor.col;
  for (let offset = 1; offset <= total; offset += 1) {
    const index = (((start + step * offset) % total) + total) % total;
    yield { row: Math.floor(index / size), col: index % size };
  }
}

// The grid cursor of the next pair of letters, in reading order from `cursor` (`step` 1) or the one before (`step` -1), that still has a carried-over facing pair not reviewed, wrapping round; null when none has one. With `cells` the walk visits only those, in their order.
export function nextCarried(model, values, cursor, step, cells = null) {
  for (const cell of walkFrom(model, cursor, step, cells)) {
    if (entryCarried(model, values, runePairAt(model, cell.row, cell.col))) return cell;
  }
  return null;
}

// Where the mark key moves the selection once it marks the facing-pair keys `marked` reviewed: the next facing pair after `facing` in the same pair of letters that is still carried over, or else the first such facing pair of the next pair of letters that has one, in reading order (or among `cells`, in list mode) and wrapping round; null when none is left. It reads `marked` as reviewed already, since the mark's reply has not come back yet.
export function nextAfterMark(model, values, cursor, facing, marked, cells = null) {
  const after = { ...values, reviewed: new Set([...values.reviewed, ...marked]) };
  const here = runePairAt(model, cursor.row, cursor.col);
  const later = here ? here.pairs.findIndex((id, index) => index > facing && isCarried(model, after, id)) : -1;
  if (later >= 0) return { cursor, facing: later };
  const next = nextCarried(model, after, cursor, 1, cells);
  if (!next) return null;
  const entry = runePairAt(model, next.row, next.col);
  return { cursor: next, facing: entry.pairs.findIndex((id) => isCarried(model, after, id)) };
}

// What the mark key (`toggle` false) or the reviewed button (`toggle` true) does with a scope's `marks` (`marksOf`): `post` is the mark to send (true marks, false takes the mark off, null sends none), and `move` whether the selection moves on to the next carried-over facing pair. The key never takes a mark off, so on a scope already marked, as a change of value marks it, it moves on as it does from a scope with nothing carried over; the button takes the mark off and stays.
export function reviewStep(marks, toggle) {
  if (marks.imported.length === 0) return { post: null, move: true };
  if (!marks.reviewed) return { post: true, move: true };
  return toggle ? { post: false, move: false } : { post: null, move: true };
}

// The facing pairs a mark of `scope` reaches that the import set, and whether every one of them is marked reviewed already, so the mark key takes them off.
export function marksOf(model, values, scope, entry) {
  const keys = scope.scope === SCOPE_FACING ? [`${scope.lead}|${scope.follow}`] : entry.pairs.map((id) => model.pairKeys[id]);
  const imported = keys.filter((key) => values?.imported?.has(key));
  return { imported, reviewed: imported.length > 0 && imported.every((key) => values.reviewed.has(key)) };
}

// The carried-over marks' trouble the header line and the status lines report: how many imported or marked facing pairs name a side the current build no longer has (a later build split or renamed it, and their marks no longer reach it), and why the marks cannot be read, if they cannot.
export function carriedTrouble(values) {
  const carried = values?.payload?.carried;
  return { stale: carried?.stale?.length ?? 0, error: carried?.error ?? null };
}

// Relatives (doc/kerning-design.md section 6). A facing pair's junction is every horizontal gap between ink of its two sides on one row or on rows one apart, at a value of 0: the leading glyph's blank from its rightmost ink to its advance plus the following glyph's blank from its pen position to its leftmost ink, so the two letters' side bearings cancel out of it. Two facing pairs are relatives when their junctions are alike once one is left as it is (`same`), has its two sides exchanged (`mirror`, the junction seen in a mirror), is turned upside down (`flip`), or both (`turn`, a half turn). Alike means equal on every gap less than `CLOSE_REACH` pixels looser than the closest one, so ink set well back from the junction, a ligature's second letter's stem among it, counts for nothing. Relatives whose values differ are kerned inconsistently unless the author marks one as apart on purpose (`GET /kern`'s `apart`).
export const RELATIONS = ['same', 'mirror', 'flip', 'turn'];
export const CLOSE_REACH = 3;
// A near relative's junction differs from the selected one's by at most this many pixels in all, over the gaps within `CLOSE_REACH` of the closer of the two.
export const NEAR_BUDGET = 3;
const RELATION_PHRASES = {
  same: 'the same two edges',
  mirror: 'its mirror image',
  flip: 'the same junction upside down',
  turn: 'the same junction turned half round',
};
const ROW_BIAS = 32;
const ROW_SPAN = 128;
// The row a vertical flip sends row y to is FLIP_SUM − y: the baseline row and the x-height row change places, and so do a Tall letter's top row and a Deep letter's bottom row.
const FLIP_SUM = 5;
const relativesCache = new WeakMap();

function junctionKey(leadY, followY) {
  return (leadY + ROW_BIAS) * ROW_SPAN + followY + ROW_BIAS;
}

function relatedKey(key, relation) {
  const leadY = Math.floor(key / ROW_SPAN) - ROW_BIAS;
  const followY = (key % ROW_SPAN) - ROW_BIAS;
  if (relation === 1) return junctionKey(followY, leadY);
  if (relation === 2) return junctionKey(FLIP_SUM - leadY, FLIP_SUM - followY);
  if (relation === 3) return junctionKey(FLIP_SUM - followY, FLIP_SUM - leadY);
  return key;
}

// A glyph's blank pixels on each inked row, by glyph-space y: from its rightmost ink to its advance when it leads, and from its pen position to its leftmost ink when it follows.
function edgeBlanks(glyph, inkX, leading) {
  const blanks = new Map();
  const top = glyph.y + glyph.rows.length - 1;
  for (const [row, line] of glyph.rows.entries()) {
    const first = line.indexOf('#');
    if (first < 0) continue;
    blanks.set(top - row, leading ? glyph.adv - (inkX + line.lastIndexOf('#') + 1) : inkX + first);
  }
  return blanks;
}

// A facing pair's junction at a value of 0, as a map from a (leading row, following row) key to the gap between the two sides' ink there; over a pair with a mixed side, the smallest gap among its drawings.
export function junctionOf(model, pairId) {
  const doc = model.doc;
  const inkX = doc.ink_x ?? 1;
  const pair = doc.pairs[pairId];
  const members = Array.isArray(pair.drawings) && pair.drawings.length > 1 ? pair.drawings : [pair.member];
  const gaps = new Map();
  for (const [lead, follow] of members) {
    const right = edgeBlanks(doc.glyphs[lead], inkX, true);
    const left = edgeBlanks(doc.glyphs[follow], inkX, false);
    for (const [leadY, blank] of right) {
      for (const step of [-1, 0, 1]) {
        const other = left.get(leadY + step);
        if (other === undefined) continue;
        const key = junctionKey(leadY, leadY + step);
        gaps.set(key, Math.min(gaps.get(key) ?? Infinity, blank + other));
      }
    }
  }
  return gaps;
}

function junctionText(gaps, relation, below) {
  const parts = [];
  for (const [key, gap] of gaps) if (gap < below) parts.push([relatedKey(key, relation), gap]);
  parts.sort((one, other) => one[0] - other[0]);
  return parts.map(([key, gap]) => `${key}:${gap}`).join(' ');
}

// Every facing pair's junction, its closest gap, and the classes of relatives: `classOf[pair]` indexes `classes`, each a list of facing pairs in the inventory's order, and is -1 for a pair whose two sides have no ink on one row or on neighboring rows. Computed once for an inventory.
export function relativesIndex(model) {
  const held = relativesCache.get(model);
  if (held) return held;
  const junctions = [];
  const closest = [];
  const texts = [];
  const classOf = [];
  const classes = [];
  const byText = new Map();
  for (const id of model.doc.pairs.keys()) {
    const gaps = junctionOf(model, id);
    junctions.push(gaps);
    if (gaps.size === 0) {
      closest.push(null);
      texts.push(null);
      classOf.push(-1);
      continue;
    }
    const least = Math.min(...gaps.values());
    closest.push(least);
    const four = RELATIONS.map((_name, relation) => junctionText(gaps, relation, least + CLOSE_REACH));
    texts.push(four);
    const first = four.reduce((one, other) => (other < one ? other : one));
    let at = byText.get(first);
    if (at === undefined) {
      at = classes.length;
      classes.push([]);
      byText.set(first, at);
    }
    classes[at].push(id);
    classOf.push(at);
  }
  const index = { junctions, closest, texts, classOf, classes };
  relativesCache.set(model, index);
  return index;
}

// A facing pair's relatives, in the inventory's order: each one's id, how its junction relates to this pair's (the first of `RELATIONS` that holds), and whether the two junctions are equal on every gap (`exact`) or only on those within `CLOSE_REACH` of the closest.
export function relativesOf(model, pairId) {
  const index = relativesIndex(model);
  const at = index.classOf[pairId];
  if (at === undefined || at < 0) return [];
  const own = index.texts[pairId];
  const found = [];
  for (const id of index.classes[at]) {
    if (id === pairId) continue;
    const relation = own.indexOf(index.texts[id][0]);
    const exact = junctionText(index.junctions[id], 0, Infinity) === junctionText(index.junctions[pairId], relation, Infinity);
    found.push({ id, relation: RELATIONS[relation], exact });
  }
  return found;
}

// The facing pairs that are no relatives of `pairId` but nearly are: each one's id, the relation under which its junction comes closest, and the distance, the pixels by which the two junctions differ in all over the gaps within `CLOSE_REACH` of the closer of the two (a gap one junction lacks reading as one that far off), at most `NEAR_BUDGET`. Nearest first, then in the inventory's order.
export function nearRelativesOf(model, pairId) {
  const index = relativesIndex(model);
  const own = index.junctions[pairId];
  if (!own || own.size === 0) return [];
  const turned = RELATIONS.map((_name, relation) => {
    const gaps = new Map();
    for (const [key, gap] of own) gaps.set(relatedKey(key, relation), gap);
    return gaps;
  });
  const found = [];
  for (const [id, other] of index.junctions.entries()) {
    if (id === pairId || other.size === 0 || index.classOf[id] === index.classOf[pairId]) continue;
    const cap = Math.min(index.closest[id], index.closest[pairId]) + CLOSE_REACH;
    let best = null;
    for (const [relation, gaps] of turned.entries()) {
      let distance = 0;
      for (const [key, gap] of gaps) {
        distance += Math.abs(Math.min(gap, cap) - Math.min(other.get(key) ?? cap, cap));
        if (distance > NEAR_BUDGET) break;
      }
      if (distance > NEAR_BUDGET) continue;
      for (const [key, gap] of other) {
        if (!gaps.has(key)) distance += cap - Math.min(gap, cap);
        if (distance > NEAR_BUDGET) break;
      }
      if (distance <= NEAR_BUDGET && (best === null || distance < best.distance)) best = { id, relation: RELATIONS[relation], distance };
    }
    if (best) found.push(best);
  }
  return found.sort((one, other) => one.distance - other.distance || one.id - other.id);
}

export function relationText(relative) {
  const phrase = RELATION_PHRASES[relative.relation] ?? relative.relation;
  if (relative.distance !== undefined) return `nearly ${phrase}: ${relative.distance} px of difference in all`;
  return relative.exact ? `${phrase}, pixel for pixel` : `${phrase}, as far as ${CLOSE_REACH} px past the closest ink`;
}

// A side as the view names it beside a letter: the rune's label with the side's own keys, `·Ye[climbing-loop]`.
export function sideLabel(model, sideId) {
  const side = model.doc.sides[sideId];
  const rune = model.doc.runes[side.rune];
  return `${rune.label}${side.name.slice(rune.name.length)}`;
}

export function pairLabel(model, pairId) {
  const pair = model.doc.pairs[pairId];
  return `${sideLabel(model, pair.lead)} | ${sideLabel(model, pair.follow)}`;
}

// Whether the author has marked a facing pair as kerned apart from its relatives on purpose, which leaves it out of every comparison with them.
export function isApart(model, values, pairId) {
  return Boolean(values?.apart?.has(model.pairKeys[pairId]));
}

// The facing pairs kerned inconsistently: every member not marked apart of each class of relatives whose members not marked apart take more than one value (`valueOf`), and how many such classes there are.
export function splitStanding(model, values, valueOf) {
  const pairs = new Set();
  let classes = 0;
  for (const members of relativesIndex(model).classes) {
    if (members.length < 2) continue;
    const counted = members.filter((id) => !isApart(model, values, id));
    const first = counted.length > 0 ? valueOf(counted[0]) : 0;
    if (!counted.some((id) => valueOf(id) !== first)) continue;
    classes += 1;
    for (const id of counted) pairs.add(id);
  }
  return { pairs, classes };
}

// The value a facing pair's relatives hold: the one most of those not marked apart take, or null with `tied` listing the values that share the lead, or null with none when it has no relative to count.
export function relativesValue(model, values, valueOf, pairId) {
  const counts = new Map();
  for (const relative of relativesOf(model, pairId)) {
    if (isApart(model, values, relative.id)) continue;
    const value = valueOf(relative.id);
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  const most = Math.max(0, ...counts.values());
  const tied = [...counts].filter(([_value, count]) => count === most).map(([value]) => value);
  return tied.length === 1 ? { value: tied[0], tied } : { value: null, tied: tied.sort((one, other) => one - other) };
}

// The relatives a facing pair's value would move if it were given to them: those not marked apart whose value differs from `value`.
export function relativesToGive(model, values, valueOf, pairId, value) {
  return relativesOf(model, pairId)
    .map((relative) => relative.id)
    .filter((id) => !isApart(model, values, id) && valueOf(id) !== value);
}

// The grid cursor and the facing-pair index of a facing pair, for a jump to it.
export function selectionOfPair(model, pairId) {
  const pair = model.doc.pairs[pairId];
  const cursor = { row: model.doc.sides[pair.lead].rune, col: model.doc.sides[pair.follow].rune };
  const entry = runePairAt(model, cursor.row, cursor.col);
  return { cursor, facing: Math.max(entry?.pairs.indexOf(pairId) ?? 0, 0) };
}

// A facing pair with its relatives, as a set of ids: the class the tour of inconsistent pairs steps over as one.
export function classMembers(model, pairId) {
  const index = relativesIndex(model);
  const at = index.classOf[pairId];
  return new Set(at === undefined || at < 0 ? [pairId] : index.classes[at]);
}

// The next facing pair among `split` (`splitStanding`'s pairs) after the selected one (`step` 1) or the one before it (`step` -1): through the selected pair of letters' facing pairs, then through the grid in reading order, wrapping round, passing over the ids in `skip`, the selected pair's own class, so one press leaves a class behind. Null when no other facing pair is among them.
export function nextSplit(model, split, cursor, facing, step, skip = null) {
  const size = model.size;
  const total = size * size;
  const start = cursor.row * size + cursor.col;
  for (let offset = 0; offset <= total; offset += 1) {
    const cell = (((start + step * offset) % total) + total) % total;
    const row = Math.floor(cell / size);
    const col = cell % size;
    const entry = runePairAt(model, row, col);
    if (!entry) continue;
    const order = [...entry.pairs.keys()];
    if (step < 0) order.reverse();
    for (const at of order) {
      if (offset === 0 && (step > 0 ? at <= facing : at >= facing)) continue;
      if (offset === total && at === facing) continue;
      if (split.has(entry.pairs[at]) && !skip?.has(entry.pairs[at])) return { cursor: { row, col }, facing: at };
    }
  }
  return null;
}

// Where the apart key moves the selection once it marks the facing pairs `keys` apart: the next facing pair of another class that is still kerned inconsistently, reading the marks as made, since their reply has not come back yet. Null when none is left.
export function nextAfterApart(model, values, valueOf, cursor, facing, keys) {
  const after = { ...values, apart: new Set([...(values?.apart ?? []), ...keys]) };
  const entry = runePairAt(model, cursor.row, cursor.col);
  const skip = entry?.pairs[facing] === undefined ? null : classMembers(model, entry.pairs[facing]);
  return nextSplit(model, splitStanding(model, after, valueOf).pairs, cursor, facing, 1, skip);
}

// Comparables (doc/kerning-design.md section 6): the facing pairs that share one side with the selected pair and whose other side is shaped like the selected pair's. Two sides are alike when their facing edges, row by row, differ by a constant, the distance each is set back from its glyph's boundary, and by little else: the likeness distance is the pixels left once that constant (`offset`, the median difference) is taken out, plus `MISSING_ROW` for every row only one of them inks. A side counts as shaped like another within one pixel a row of the other's edge.
export const COMPARE_JUNCTION = 'junction';
export const COMPARE_LEAD = 'lead';
export const COMPARE_FOLLOW = 'follow';
export const COMPARISONS = [COMPARE_JUNCTION, COMPARE_LEAD, COMPARE_FOLLOW];
export const MISSING_ROW = 4;
const comparablesCache = new WeakMap();

// A side's facing edge, from its first cell: the blank pixels on each inked row (`edgeBlanks`).
export function sideEdge(model, sideId) {
  const side = model.doc.sides[sideId];
  return edgeBlanks(model.doc.glyphs[side.cells[0]], model.doc.ink_x ?? 1, side.facing === 'leading');
}

// How alike two edges are: `offset`, how many pixels further from its boundary `other`'s ink sits than `mine`'s (the median over the rows both ink), and `distance`, the pixels by which the two differ in all once that is taken out, with `MISSING_ROW` for each row only one inks. Null when they ink no row in common.
export function edgeLikeness(mine, other) {
  const differences = [];
  let lone = 0;
  for (const [y, blank] of mine) {
    if (other.has(y)) differences.push(other.get(y) - blank);
    else lone += 1;
  }
  for (const y of other.keys()) if (!mine.has(y)) lone += 1;
  if (differences.length === 0) return null;
  const sorted = [...differences].sort((one, two) => one - two);
  const offset = sorted[Math.floor((sorted.length - 1) / 2)];
  let distance = lone * MISSING_ROW;
  for (const difference of differences) distance += Math.abs(difference - offset);
  return { distance, offset };
}

function comparablesIndex(model) {
  const held = comparablesCache.get(model);
  if (held) return held;
  const byLead = new Map();
  const byFollow = new Map();
  for (const [id, pair] of model.doc.pairs.entries()) {
    if (!byLead.has(pair.lead)) byLead.set(pair.lead, []);
    byLead.get(pair.lead).push(id);
    if (!byFollow.has(pair.follow)) byFollow.set(pair.follow, []);
    byFollow.get(pair.follow).push(id);
  }
  const index = { byLead, byFollow, edges: new Map() };
  comparablesCache.set(model, index);
  return index;
}

// The facing pairs to compare `pairId` with across one of its sides. With `part` `COMPARE_LEAD` they are the pairs with the same following side, each with how alike its leading side is to this pair's (`edgeLikeness`); with `COMPARE_FOLLOW`, the pairs with the same leading side and their following sides. `like` holds those shaped like this pair's side, within `budget` (a pixel for each row of its edge), most alike first; `rest` the others, in the same order, a side with no row in common last.
export function comparablesOf(model, pairId, part) {
  const index = comparablesIndex(model);
  const pair = model.doc.pairs[pairId];
  const own = part === COMPARE_LEAD ? pair.lead : pair.follow;
  const edgeOf = (sideId) => {
    if (!index.edges.has(sideId)) index.edges.set(sideId, sideEdge(model, sideId));
    return index.edges.get(sideId);
  };
  const mine = edgeOf(own);
  const budget = mine.size;
  const found = [];
  for (const id of (part === COMPARE_LEAD ? index.byFollow.get(pair.follow) : index.byLead.get(pair.lead)) ?? []) {
    if (id === pairId) continue;
    const other = model.doc.pairs[id];
    const likeness = edgeLikeness(mine, edgeOf(part === COMPARE_LEAD ? other.lead : other.follow));
    found.push({ id, distance: likeness?.distance ?? Infinity, offset: likeness?.offset ?? 0 });
  }
  found.sort((one, two) => one.distance - two.distance || one.id - two.id);
  return { budget, like: found.filter((one) => one.distance <= budget), rest: found.filter((one) => one.distance > budget) };
}

// How far apart a facing pair's ink is at `value`, in pixels, where it is closest, ink on rows one apart counted: its junction's closest gap moved by the value. Null for a pair whose sides ink no row in common or neighboring.
export function clearanceOf(model, pairId, value) {
  const closest = relativesIndex(model).closest[pairId];
  return closest === null || closest === undefined ? null : closest + value;
}

// The value at which a facing pair's ink is `clearance` pixels apart where it is closest (`clearanceOf`), or null when it has no such place.
export function valueForClearance(model, pairId, clearance) {
  const closest = relativesIndex(model).closest[pairId];
  return closest === null || closest === undefined || clearance === null ? null : clearance - closest;
}

// What the view says of a comparable's side beside the selected pair's: how far its shape is from that one's, and how much further from the junction, or nearer to it, its ink sits.
export function likenessText(comparable) {
  if (!Number.isFinite(comparable.distance)) return 'inks none of the same rows';
  const shape = comparable.distance === 0 ? 'the same edge' : `${comparable.distance} px from the same edge in all`;
  if (comparable.offset === 0) return shape;
  return `${shape}, set ${Math.abs(comparable.offset)} px ${comparable.offset > 0 ? 'further from' : 'nearer to'} the junction`;
}

// The comparison after `current` in `COMPARISONS`' order, wrapping round, for the key that steps through them.
export function nextComparison(current) {
  return COMPARISONS[(COMPARISONS.indexOf(current) + 1) % COMPARISONS.length];
}

// The order the detail panel lists a pair of letters' facing pairs in, as indexes into the entry's `pairs`: by following side, then by leading side, so the pairs that share a second shape (every stance of ·Ye before half ·Way) stand together.
export function facingOrder(model, entry) {
  const pairs = model.doc.pairs;
  return [...(entry?.pairs ?? []).keys()].sort((one, other) => {
    const first = pairs[entry.pairs[one]];
    const second = pairs[entry.pairs[other]];
    return first.follow - second.follow || first.lead - second.lead || one - other;
  });
}

// The facing-pair index `step` places from `facing` in `order` (`facingOrder`), or null past either end, where the up and down keys move on to another pair of letters.
export function stepInOrder(order, facing, step) {
  const at = order.indexOf(facing) + step;
  return order.indexOf(facing) >= 0 && at >= 0 && at < order.length ? order[at] : null;
}

// A relative's relation in a word or two, for a tile: `≈` leads one whose junction is the selected pair's only as far as `CLOSE_REACH` px past the closest ink, and a near relative gives its distance.
export function relationWord(relative) {
  const word = { same: 'same', mirror: 'mirror', flip: 'upside down', turn: 'half turn' }[relative.relation] ?? relative.relation;
  if (relative.distance !== undefined) return `near ${word}, ${relative.distance} px`;
  return relative.exact ? word : `≈ ${word}`;
}
