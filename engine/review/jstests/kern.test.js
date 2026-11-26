import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {
  ACROSS_ZWNJ,
  INVENTORY_FORMAT,
  OVERLAY,
  BY_LIMIT,
  SCOPE_FACING,
  SCOPE_LETTERS,
  WORD,
  blockOf,
  builtValue,
  carriedCount,
  carriedTrouble,
  classMembers,
  clearanceOf,
  comparablesOf,
  edgeLikeness,
  entryCarried,
  isCarried,
  marksOf,
  nextAfterMark,
  reviewStep,
  nextCarried,
  closestInk,
  decidersOf,
  deciderText,
  deltaSet,
  facingOrder,
  facingScope,
  followsLetters,
  historyAfterReply,
  indexInventory,
  indexValues,
  inkGapText,
  inventoryMoved,
  inventoryRefusal,
  isApart,
  journalLines,
  junctionOf,
  lettersScope,
  lettersValue,
  likenessText,
  liveValue,
  manualTexts,
  moveFacing,
  stepFacing,
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
  relativesIndex,
  relativesOf,
  relativesToGive,
  relativesValue,
  resendAfter,
  runePairAt,
  scopeRecord,
  selectionFromState,
  selectionOfPair,
  shiftedPositions,
  shownValue,
  signed,
  splitStanding,
  staleAnswer,
  stalenessLines,
  stepInOrder,
  stateFromSelection,
  textFocus,
  valueForClearance,
  valuesAfterFetch,
  worstInk,
} from '../static/kern.js';

// The fixture is a slice of a build's inventory (engine/pipeline/kern_inventory.py) to ·No, ·Tea and ·It and what their witnesses need, which engine/test_kern_inventory.py checks against the inventory's shape; the values fixture is `GET /kern`'s shape (engine/review/kern_routes.py), set ·No.flipped before half ·Tea by −1 in the rune files, by that facing pair's own record, and ·No.loop before full ·Tea by −1 in the built font.
const fixtureDir = new URL('../fixtures/', import.meta.url);
const inventory = JSON.parse(await readFile(new URL('kern-inventory.json', fixtureDir), 'utf8'));
const payload = JSON.parse(await readFile(new URL('kern-values.json', fixtureDir), 'utf8'));
const model = indexInventory(inventory);
const values = indexValues(payload);
const rune = (name) => model.runeIndex.get(name);
const noTea = runePairAt(model, rune('qsNo'), rune('qsTea'));
const pairNamed = (lead, follow) => model.pairByKey.get(`${lead}|${follow}`);
const flippedHalf = pairNamed('qsNo[flipped]', 'qsTea[half exit=x-height]');
const loopFull = pairNamed('qsNo[loop]', 'qsTea[full]');

test('the fixture is an inventory the view accepts, and anything else is refused with a reason', () => {
  assert.equal(inventoryRefusal(inventory), null);
  assert.match(inventoryRefusal({ ...inventory, format: 'ams-kern-inventory/0' }), new RegExp(INVENTORY_FORMAT));
  assert.match(inventoryRefusal({ ...inventory, texts: null }), /texts/);
  assert.ok(inventoryRefusal(null));
});

test('a rune pair is found by its two runes, and lists facing pairs that face those runes', () => {
  assert.ok(noTea);
  assert.equal(noTea.pairs.length, 4);
  for (const id of noTea.pairs) {
    const pair = inventory.pairs[id];
    assert.equal(inventory.sides[pair.lead].rune, rune('qsNo'));
    assert.equal(inventory.sides[pair.follow].rune, rune('qsTea'));
  }
  assert.ok(noTea.pairs.includes(noTea.rep));
  assert.equal(runePairAt(model, rune('qsNo'), 999), null);
});

test('the live values come with their deciders, and a facing pair no record decides reads 0', () => {
  assert.equal(liveValue(model, values, flippedHalf), -1);
  assert.equal(deciderText(decidersOf(model, values, flippedHalf)), 'qsNo.yaml kern[0]');
  assert.equal(liveValue(model, values, loopFull), 0);
  assert.equal(deciderText(decidersOf(model, values, loopFull)), null);
  assert.equal(deciderText([{ file: 'glyph_data/runes/qsUtter.yaml', path: 'kern[2]', inherited: true }]), 'qsUtter.yaml kern[2] (inherited)');
});

test('a value the built font lacks, or one it has that the rune files no longer set, is marked', () => {
  assert.equal(builtValue(model, values, loopFull), -1);
  assert.equal(notInFont(model, values, flippedHalf), true);
  assert.equal(notInFont(model, values, loopFull), true);
  for (const id of noTea.pairs) {
    if (id !== flippedHalf && id !== loopFull) assert.equal(notInFont(model, values, id), false);
  }
});

test('while the rune files do not resolve, the view shows the built values and marks nothing', () => {
  const broken = indexValues({ ...payload, live: { values: [], error: { message: 'the kern blocks on disk do not resolve', issues: [['E-KERN-UNMATCHED', 'qsNo.yaml:kern[0] matches no break']] } } });
  assert.equal(liveValue(model, broken, loopFull), -1);
  assert.equal(liveValue(model, broken, flippedHalf), 0);
  assert.equal(notInFont(model, broken, loopFull), false);
  assert.deepEqual(decidersOf(model, broken, flippedHalf), []);
  const lines = stalenessLines(model, broken);
  assert.equal(lines[0].level, 'error');
  assert.deepEqual(lines[0].issues, [['E-KERN-UNMATCHED', 'qsNo.yaml:kern[0] matches no break']]);
});

test('no values at all read as 0 everywhere, and the status says so', () => {
  const none = indexValues(null);
  assert.equal(liveValue(model, none, flippedHalf), 0);
  assert.equal(notInFont(model, none, flippedHalf), false);
  assert.equal(stalenessLines(model, none)[0].level, 'error');
});

test('the staleness lines name a running pass, stale tables, an inventory over other tables, and a moved Manual', () => {
  assert.deepEqual(stalenessLines(model, values), []);
  const stale = indexValues({
    ...payload,
    pass: { pid: '4242' },
    tables: { inputs: 'x', current: false },
    inventory: { ...payload.inventory, tables_current: false, manual_current: false },
  });
  const texts = stalenessLines(model, stale).map((line) => line.text);
  assert.ok(texts.some((text) => text.includes('4242')));
  assert.ok(texts.some((text) => text.includes('make artifact-cycle')));
  assert.ok(texts.some((text) => text.includes('other tables')));
  assert.ok(texts.some((text) => text.includes('Manual')));
});

test('the view loads the inventory again only when the server holds one with another stamp', () => {
  assert.equal(inventoryMoved(model, values), false);
  const moved = indexValues({ ...payload, inventory: { ...payload.inventory, stamp: { ...inventory.stamp, tables: 'other' } } });
  assert.equal(inventoryMoved(model, moved), true);
  assert.equal(inventoryMoved(model, indexValues({ ...payload, inventory: { present: false, stamp: null } })), false);
});

test('a rune pair reached mid-word has no chip; one reached only across a ZWNJ or under ss10 says which', () => {
  assert.equal(reachChip(model, noTea), null);
  const fake = (reaches) => {
    const doc = { ...inventory, pairs: reaches.map((reach) => ({ ...inventory.pairs[0], reach })) };
    return [indexInventory(doc), { pairs: reaches.map((_reach, index) => index) }];
  };
  assert.equal(reachChip(...fake([[ACROSS_ZWNJ], [ACROSS_ZWNJ]])), 'only across a ZWNJ');
  assert.equal(reachChip(...fake([[OVERLAY]])), 'only under ss10');
  assert.equal(reachChip(...fake([[ACROSS_ZWNJ, OVERLAY], [ACROSS_ZWNJ]])), 'only across a ZWNJ or under ss10');
  assert.equal(reachChip(...fake([[ACROSS_ZWNJ], [WORD, ACROSS_ZWNJ]])), null);
  assert.equal(reachText([WORD, ACROSS_ZWNJ, OVERLAY]), 'mid-word, across a ZWNJ, under ss10');
});

test('the grid cursor moves one cell an arrow and stays inside the grid', () => {
  assert.deepEqual(moveGridCursor({ row: 0, col: 0 }, 'kern-up', 5), { row: 0, col: 0 });
  assert.deepEqual(moveGridCursor({ row: 0, col: 0 }, 'kern-down', 5), { row: 1, col: 0 });
  assert.deepEqual(moveGridCursor({ row: 4, col: 4 }, 'kern-right', 5), { row: 4, col: 4 });
  assert.deepEqual(moveGridCursor({ row: 2, col: 2 }, 'kern-left', 5), { row: 2, col: 1 });
  assert.deepEqual(moveGridCursor({ row: 2, col: 2 }, 'kern-jump', 5), { row: 2, col: 2 });
  assert.equal(moveFacing(0, -1, 4), 0);
  assert.equal(moveFacing(3, 1, 4), 3);
  assert.equal(moveFacing(1, 1, 4), 2);
});

test('up and down step through the facing pairs and report the ends, where they move on to another pair of letters', () => {
  assert.equal(stepFacing(1, 1, 4), 2);
  assert.equal(stepFacing(1, -1, 4), 0);
  assert.equal(stepFacing(3, 1, 4), null);
  assert.equal(stepFacing(0, -1, 4), null);
  assert.equal(stepFacing(0, 1, 0), null);
});

test('a query names a pair of runes by their letters, ligatures with a plus', () => {
  assert.deepEqual(pairFromQuery('·no·tea', model), { lead: rune('qsNo'), follow: rune('qsTea') });
  assert.deepEqual(pairFromQuery('No Tea', model), { lead: rune('qsNo'), follow: rune('qsTea') });
  assert.deepEqual(pairFromQuery('.it.no', model), { lead: rune('qsIt'), follow: rune('qsNo') });
  assert.equal(pairFromQuery('·no', model), null);
  assert.equal(pairFromQuery('·no·nothing', model), null);
  const ligature = inventory.runes.find((one) => one.name.includes('_'));
  if (ligature) {
    const typed = ligature.label.replace('·', '');
    assert.deepEqual(pairFromQuery(`${typed} ·no`, model), { lead: rune(ligature.name), follow: rune('qsNo') });
  }
});

test('the selection round-trips through the URL hash, and a hash the inventory cannot read selects the first cell', () => {
  const cursor = { row: rune('qsNo'), col: rune('qsTea') };
  const facing = noTea.pairs.indexOf(flippedHalf);
  const state = stateFromSelection(model, cursor, facing);
  assert.deepEqual(state, { pair: 'qsNo:qsTea', facing: 'qsNo[flipped]|qsTea[half exit=x-height]' });
  assert.deepEqual(selectionFromState(model, state.pair, state.facing), { cursor, facing });
  assert.deepEqual(selectionFromState(model, 'qsNope:qsTea', null), { cursor: { row: 0, col: 0 }, facing: 0 });
  assert.deepEqual(selectionFromState(model, 'qsNo:qsTea', 'no such pair'), { cursor, facing: 0 });
});

test('a value shifts every glyph after its break, and a join never moves', () => {
  const text = { x: [0, 5, 11, 12], gaps: [-1, 7, -1] };
  assert.deepEqual(shiftedPositions(text, () => -1), [0, 5, 10, 11]);
  assert.deepEqual(shiftedPositions({ x: [0, 4, 9], gaps: [3, 4] }, (id) => (id === 3 ? -1 : 2)), [0, 3, 10]);
  assert.deepEqual(shiftedPositions(text, () => 0), text.x);
});

test('the closest ink is measured on the closest shared row, or between boxes with none', () => {
  const left = [{ x: 3, y: 0 }, { x: 4, y: 1 }];
  assert.deepEqual(closestInk(left, [{ x: 6, y: 0 }, { x: 6, y: 1 }]), { gap: 1, y: 1, shared: true, overlap: false });
  assert.deepEqual(closestInk(left, [{ x: 5, y: 1 }]), { gap: 0, y: 1, shared: true, overlap: false });
  assert.deepEqual(closestInk(left, [{ x: 7, y: 5 }]), { gap: 2, y: null, shared: false, overlap: false });
  assert.equal(closestInk([], left), null);
  assert.equal(inkGapText({ gap: 0, y: 1, shared: true, overlap: false }).level, 'touch');
  assert.equal(inkGapText({ gap: 2, y: 5, shared: true, overlap: false }).text, 'closest ink 2 px, at y=5');
  assert.equal(signed(-2), '−2');
  assert.equal(signed(1), '+1');
  assert.equal(signed(0), '0');
});

test('every witness text in the fixture holds its facing pair at its gap', () => {
  for (const [id, pair] of inventory.pairs.entries()) {
    if (pair.witness === null) continue;
    assert.equal(inventory.texts[pair.witness].gaps[pair.at], id);
  }
});

test('only a pixel both sides ink is an overlap; strokes that pass each other without one are told apart', () => {
  const left = [{ x: 3, y: 0 }, { x: 4, y: 1 }];
  const shared = closestInk(left, [{ x: 4, y: 1 }]);
  assert.equal(shared.overlap, true);
  assert.deepEqual(inkGapText(shared), { level: 'over', text: 'ink overlaps' });
  const passing = closestInk(left, [{ x: 3, y: 1 }, { x: 5, y: 0 }]);
  assert.equal(passing.overlap, false);
  assert.equal(passing.gap, -2);
  assert.equal(inkGapText(passing).level, 'touch');
  assert.match(inkGapText(passing).text, /pass each other at y=1, sharing no pixel/);
  assert.doesNotMatch(inkGapText(passing).text, /overlap/);
});

test('the worst of several junctions is a shared pixel first, then the smallest gap', () => {
  const clear = { gap: 2, y: 1, shared: true, overlap: false };
  const passing = { gap: -1, y: 3, shared: true, overlap: false };
  const shared = { gap: 0, y: 4, shared: true, overlap: true };
  assert.equal(worstInk([clear, null, passing]), passing);
  assert.equal(worstInk([passing, shared, clear]), shared);
  assert.equal(worstInk([null]), null);
});

test('the Manual words list the ones that hold the pair at a break first, each focused there, and those that hold it only joined after', () => {
  const listed = manualTexts(model, noTea);
  assert.ok(listed.length > 0 && listed.length <= noTea.manual.length);
  const firstJoined = listed.findIndex((found) => found.focus.joined);
  assert.ok(firstJoined > 0, 'the fixture holds both kinds');
  assert.ok(listed.slice(firstJoined).every((found) => found.focus.joined));
  for (const { id, focus } of listed) {
    const text = inventory.texts[id];
    assert.equal(inventory.glyphs[text.glyphs[focus.gap]].rune, rune('qsNo'));
    assert.equal(inventory.glyphs[text.glyphs[focus.gap + 1]].rune, rune('qsTea'));
    if (focus.joined) {
      assert.equal(text.gaps[focus.gap], -1);
      assert.ok(inventory.joined[focus.group].height);
      assert.ok(text.gaps.every((pairId, gap) => pairId === -1 || inventory.glyphs[text.glyphs[gap]].rune !== rune('qsNo') || inventory.glyphs[text.glyphs[gap + 1]].rune !== rune('qsTea')));
    } else {
      assert.ok(text.gaps[focus.gap] >= 0);
    }
  }
});

test('a text whose only meeting of the two runes is a join is focused on that join, and one where they never meet is not focused', () => {
  const text = { glyphs: [0, 1, 2], gaps: [-1, 3], joins: [5, null] };
  const tiny = indexInventory({
    ...inventory,
    glyphs: [
      { name: 'a', rune: 1, rows: ['#'], y: 0, adv: 2 },
      { name: 'b', rune: 2, rows: ['#'], y: 0, adv: 2 },
      { name: 'c', rune: 3, rows: ['#'], y: 0, adv: 2 },
    ],
  });
  assert.deepEqual(textFocus(tiny, text, 1, 2), { gap: 0, joined: true, group: 5 });
  assert.deepEqual(textFocus(tiny, text, 2, 3), { gap: 1, joined: false, group: null });
  assert.equal(textFocus(tiny, text, 1, 3), null);
});

test('the keys act in the pane the focused element is in, and anywhere else on the page', () => {
  const inside = (members) => ({ contains: (node) => members.includes(node) });
  const cell = {};
  const option = {};
  const button = {};
  const grid = inside([cell]);
  const detail = inside([option]);
  assert.equal(paneFor(cell, grid, detail), 'grid');
  assert.equal(paneFor(option, grid, detail), 'detail');
  assert.equal(paneFor(button, grid, detail), 'page');
  assert.equal(paneFor(null, grid, detail), 'page');
});

test('a failed fetch of the values keeps the last ones and says why; a good one replaces them', () => {
  const kept = valuesAfterFetch(values, { ok: false, status: 503, body: { error: 'restarting' } });
  assert.equal(kept.values, values);
  assert.equal(kept.error, 'GET kern answered 503');
  assert.equal(liveValue(model, kept.values, flippedHalf), -1);
  assert.equal(valuesAfterFetch(values, { ok: false, status: 0, body: null, error: new Error('Failed to fetch') }).error, 'Failed to fetch');
  assert.match(valuesAfterFetch(values, { ok: true, status: 200, body: { error: 'x' } }).error, /no kern values/);
  const fresh = valuesAfterFetch(values, { ok: true, status: 200, body: payload });
  assert.equal(fresh.error, null);
  assert.notEqual(fresh.values, values);
  assert.equal(liveValue(model, fresh.values, flippedHalf), -1);
  assert.equal(valuesAfterFetch(null, { ok: false, status: 404 }).values.payload, null);
  const lines = stalenessLines(model, values, 'GET kern answered 503');
  assert.equal(lines[0].level, 'warn');
  assert.match(lines[0].text, /could not be refreshed \(GET kern answered 503\); these are the last ones/);
});

test('a scope names the record an edit sets, and a facing pair of two whole runes is its letters scope', () => {
  const letters = lettersScope(model, noTea);
  assert.deepEqual(letters, { scope: SCOPE_LETTERS, key: 'letters:qsNo|qsTea', rune: 'qsNo', followRune: 'qsTea', lead: 'qsNo', follow: 'qsTea' });
  const facing = facingScope(model, flippedHalf);
  assert.equal(facing.scope, SCOPE_FACING);
  assert.equal(facing.key, 'facing:qsNo[flipped]|qsTea[half exit=x-height]');
  assert.equal(facing.followRune, 'qsTea');
  const whole = inventory.pairs.findIndex((pair) => !inventory.sides[pair.lead].name.includes('[') && !inventory.sides[pair.follow].name.includes('['));
  assert.ok(whole >= 0);
  assert.equal(facingScope(model, whole).scope, SCOPE_LETTERS);
  assert.equal(scopeRecord(values, facing).path, 'kern[0]');
  assert.equal(scopeRecord(values, letters), null);
  assert.equal(blockOf(values, 'qsNo').file, 'glyph_data/runes/qsNo.yaml');
});

test('a change to the letters moves every facing pair of the two but one with its own record, and a facing pair’s own change moves it alone', () => {
  assert.equal(followsLetters(model, values, flippedHalf), false);
  assert.equal(followsLetters(model, values, loopFull), true);
  assert.equal(lettersValue(model, values, new Map(), noTea), 0);
  const letters = lettersScope(model, noTea);
  const lettersEdit = new Map([[letters.key, { scope: letters, by: -2, state: 'local' }]]);
  assert.equal(shownValue(model, values, lettersEdit, loopFull), -2);
  assert.equal(shownValue(model, values, lettersEdit, flippedHalf), -1);
  assert.equal(lettersValue(model, values, lettersEdit, noTea), -2);
  const facing = facingScope(model, flippedHalf);
  const both = new Map([...lettersEdit, [facing.key, { scope: facing, by: -3, state: 'sending' }]]);
  assert.equal(shownValue(model, values, both, flippedHalf), -3);
  assert.equal(shownValue(model, values, both, loopFull), -2);
  const dropped = new Map([...lettersEdit, [facing.key, { scope: facing, by: null, state: 'local' }]]);
  assert.equal(shownValue(model, values, dropped, flippedHalf), -2);
  assert.equal(shownValue(model, values, new Map(), flippedHalf), liveValue(model, values, flippedHalf));
});

test('a set carries the block it was made against, and a note only when one is given', () => {
  const facing = facingScope(model, flippedHalf);
  assert.deepEqual(deltaSet(values, facing, -2), {
    scope: 'facing',
    lead: 'qsNo[flipped]',
    follow: 'qsTea[half exit=x-height]',
    by: -2,
    base: payload.runes.qsNo.sha256,
  });
  assert.equal(deltaSet(values, lettersScope(model, noTea), -1, '').why, '');
});

test('a refusal shows the server’s message and the build’s first issues', () => {
  const reply = { message: 'the defect gate refuses the result', issues: [['E-CONTACT', 'a'], ['E-CONTACT', 'b'], ['E-CONTACT', 'c'], ['E-CONTACT', 'd']] };
  assert.equal(refusalText(reply, 422), 'the defect gate refuses the result\nE-CONTACT: a\nE-CONTACT: b\nE-CONTACT: c\nand 1 more');
  assert.match(refusalText(null, 500), /answered 500/);
});

test('the journal’s pending changes and the ones dropped since the view loaded are reported', () => {
  assert.deepEqual(journalLines(values, 1), []);
  const busy = indexValues({ ...payload, journal: { seq: 5, pending: 2, dropped: [{ seq: 5, reason: 'stale' }, { seq: 1, reason: 'old' }] } });
  const lines = journalLines(busy, 3);
  assert.equal(lines.length, 2);
  assert.match(lines[0].text, /2 changes wait for the cycle pass to end/);
  assert.match(lines[1].text, /dropped when it ended: stale/);
});

test('a dropped record carries no note, so a note saved just before a drop cannot turn the drop into a refused request', () => {
  const facing = facingScope(model, flippedHalf);
  assert.equal('why' in deltaSet(values, facing, null, 'a note'), false);
  assert.equal(deltaSet(values, facing, -1, 'a note').why, 'a note');
});

test('only a change the server wrote or queued goes on the undo history, with its seq', () => {
  let history = historyAfterReply([], 200, { status: 'unchanged' }, 'k');
  assert.deepEqual(history, []);
  history = historyAfterReply(history, 422, { status: 'refused' }, 'k');
  history = historyAfterReply(history, 409, { status: 'conflict', block: {} }, 'k');
  assert.deepEqual(history, []);
  history = historyAfterReply(history, 200, { status: 'applied', seq: 4 }, 'a');
  history = historyAfterReply(history, 202, { status: 'pending', seq: 5 }, 'b');
  assert.deepEqual(history, [
    { seq: 4, key: 'a', waiting: false },
    { seq: 5, key: 'b', waiting: true },
  ]);
});

test('a conflict that carries the block is sent once more, and only once', () => {
  assert.equal(resendAfter(409, { block: { sha256: 'x' } }, false), true);
  assert.equal(resendAfter(409, { block: { sha256: 'x' } }, true), false);
  assert.equal(resendAfter(409, { message: 'stale' }, false), false);
  assert.equal(resendAfter(422, { block: {} }, false), false);
});

test('a poll older than a write the view saw is ignored, but a restarted journal’s answers are taken', () => {
  assert.equal(staleAnswer({ id: 'a', seq: 9 }, { id: 'a', seq: 8 }), true);
  assert.equal(staleAnswer({ id: 'a', seq: 9 }, { id: 'a', seq: 9 }), false);
  assert.equal(staleAnswer({ id: 'a', seq: 9 }, { id: 'b', seq: 1 }), false);
  assert.equal(staleAnswer({ id: 'a', seq: 9 }, { id: null, seq: 0 }), false);
  assert.equal(staleAnswer(null, { id: 'a', seq: 1 }), false);
  assert.equal(BY_LIMIT, 64);
});

test('a facing pair carried over from the old font counts until it is marked or its value moves', () => {
  assert.equal(isCarried(model, values, flippedHalf), true);
  assert.equal(isCarried(model, values, loopFull), false);
  assert.equal(entryCarried(model, values, noTea), true);
  assert.equal(carriedCount(model, values), 1);
  const marked = indexValues({ ...payload, carried: { ...payload.carried, reviewed: ['qsNo[flipped]|qsTea[half exit=x-height]'] } });
  assert.equal(isCarried(model, marked, flippedHalf), false);
  assert.equal(carriedCount(model, marked), 0);
  const moved = indexValues({ ...payload, live: { ...payload.live, values: payload.live.values.map((value) => ({ ...value, by: -2 })) } });
  assert.equal(isCarried(model, moved, flippedHalf), false);
  assert.equal(carriedCount(model, indexValues({ ...payload, carried: undefined })), 0);
});

test('the jump keys find the next and the previous pair of letters with a carried-over pair left, wrapping round', () => {
  const at = { row: rune('qsNo'), col: rune('qsTea') };
  assert.deepEqual(nextCarried(model, values, { row: 0, col: 0 }, 1), at);
  assert.deepEqual(nextCarried(model, values, { row: 0, col: 0 }, -1), at);
  assert.deepEqual(nextCarried(model, values, at, 1), at);
  const none = indexValues({ ...payload, carried: { pairs: [], reviewed: [] } });
  assert.equal(nextCarried(model, none, at, 1), null);
});

test('the mark key moves to the next carried-over facing pair, reading the pairs it marked as reviewed', () => {
  const at = { row: rune('qsNo'), col: rune('qsTea') };
  const index = noTea.pairs.indexOf(flippedHalf);
  const flippedKey = 'qsNo[flipped]|qsTea[half exit=x-height]';
  assert.deepEqual(nextAfterMark(model, values, { row: 0, col: 0 }, 0, ['qsNo[loop]|qsTea[full]']), { cursor: at, facing: index });
  assert.deepEqual(nextAfterMark(model, values, at, index, []), { cursor: at, facing: index });
  assert.ok(index > 0);
  assert.deepEqual(nextAfterMark(model, values, at, index - 1, []), { cursor: at, facing: index });
  assert.equal(nextAfterMark(model, values, at, index, [flippedKey]), null);
  assert.equal(nextAfterMark(model, values, { row: 0, col: 0 }, 0, [flippedKey]), null);
});

test('the mark key marks and moves on but never takes a mark off, and the reviewed button takes it off and stays', () => {
  const none = { imported: [], reviewed: false };
  const open = { imported: ['a|b'], reviewed: false };
  const done = { imported: ['a|b'], reviewed: true };
  assert.deepEqual(reviewStep(none, false), { post: null, move: true });
  assert.deepEqual(reviewStep(open, false), { post: true, move: true });
  assert.deepEqual(reviewStep(done, false), { post: null, move: true });
  assert.deepEqual(reviewStep(none, true), { post: null, move: true });
  assert.deepEqual(reviewStep(open, true), { post: true, move: true });
  assert.deepEqual(reviewStep(done, true), { post: false, move: false });
});

test('a mark reaches the carried-over facing pairs of its scope, and takes the mark off when every one has it', () => {
  const facing = facingScope(model, flippedHalf);
  assert.deepEqual(marksOf(model, values, facing, noTea), { imported: ['qsNo[flipped]|qsTea[half exit=x-height]'], reviewed: false });
  const letters = lettersScope(model, noTea);
  assert.deepEqual(marksOf(model, values, letters, noTea).imported.sort(), ['qsNo[flipped]|qsTea[half exit=x-height]', 'qsNo[loop]|qsTea[full]']);
  const marked = indexValues({ ...payload, carried: { ...payload.carried, reviewed: ['qsNo[flipped]|qsTea[half exit=x-height]', 'qsNo[loop]|qsTea[full]'] } });
  assert.equal(marksOf(model, marked, letters, noTea).reviewed, true);
  assert.deepEqual(marksOf(model, values, facingScope(model, noTea.pairs.find((id) => id !== flippedHalf && id !== loopFull)), noTea).imported, []);
});

test('the marks that name lost sides and marks that cannot be read are reported', () => {
  assert.deepEqual(carriedTrouble(values), { stale: 0, error: null });
  const lost = indexValues({ ...payload, carried: { ...payload.carried, stale: ['qsNo|qsTea'], error: 'var/kern/reviewed.json is not JSON' } });
  assert.deepEqual(carriedTrouble(lost), { stale: 1, error: 'var/kern/reviewed.json is not JSON' });
  assert.deepEqual(carriedTrouble(indexValues(null)), { stale: 0, error: null });
});

// A hand-made inventory for the relatives: ·A is a diagonal rising to the right and ·B its mirror image, which is also ·A upside down; ·S is a stem; ·C is ·A with one pixel of its second row from the top a column to the left; ·D is ·A under a Tall stem set far back from its right edge. Each rune has one leading and one following side, and the facing pairs are listed in `RELATIVE_PAIRS`' order.
const DIAGONAL = ['     #', '    # ', '   #  ', '  #   ', ' #    ', '#     '];
const RELATIVE_GLYPHS = {
  qsA: { rows: DIAGONAL, adv: 8 },
  qsB: { rows: DIAGONAL.map((row) => [...row].reverse().join('')), adv: 8 },
  qsS: { rows: ['#', '#', '#', '#', '#', '#'], adv: 3 },
  qsC: { rows: ['     #', '   #  ', '   #  ', '  #   ', ' #    ', '#     '], adv: 8 },
  qsD: { rows: ['#     ', '#     ', '#     ', ...DIAGONAL], adv: 8 },
};
const RELATIVE_PAIRS = [
  ['qsA', 'qsS'],
  ['qsS', 'qsB'],
  ['qsB', 'qsS'],
  ['qsS', 'qsA'],
  ['qsS', 'qsS'],
  ['qsC', 'qsS'],
  ['qsD', 'qsS'],
];

function relativesModel() {
  const names = Object.keys(RELATIVE_GLYPHS);
  const count = names.length;
  const doc = {
    format: INVENTORY_FORMAT,
    ink_x: 1,
    configs: ['default'],
    runes: names.map((name) => ({ name, label: `·${name.slice(2)}`, letters: [] })),
    glyphs: names.map((name, index) => ({ name: `${name}.sole`, rune: index, y: 0, ...RELATIVE_GLYPHS[name] })),
    sides: [
      ...names.map((name, index) => ({ facing: 'leading', name, rune: index, cells: [index] })),
      ...names.map((name, index) => ({ facing: 'following', name, rune: index, cells: [index] })),
    ],
    pairs: RELATIVE_PAIRS.map(([lead, follow]) => ({
      lead: names.indexOf(lead),
      follow: count + names.indexOf(follow),
      member: [names.indexOf(lead), names.indexOf(follow)],
      reach: [WORD],
      configs: [0],
      members: 1,
      witness: null,
      at: null,
    })),
    joined: [],
    texts: [],
    rune_pairs: RELATIVE_PAIRS.map(([lead, follow], id) => ({ lead: names.indexOf(lead), follow: names.indexOf(follow), pairs: [id], rep: id, joined: [], manual: [] })),
  };
  return indexInventory(doc);
}

const relModel = relativesModel();
const [AS, SB, BS, SA, SS, CS, DS] = RELATIVE_PAIRS.keys();
const valuesWith = (by = {}, apart = []) => ({ values: { apart: new Set(apart.map((id) => relModel.pairKeys[id])) }, valueOf: (id) => by[id] ?? 0 });

test('a junction is every gap between the two sides’ ink on one row or on neighboring rows, with the side bearings summed', () => {
  const gaps = junctionOf(relModel, AS);
  // ·A's blank right of its ink is 1 on its top row and 6 on its bottom row, and ·S's blank left of its ink is 1 on every row.
  assert.deepEqual([...gaps.values()].sort((one, other) => one - other).slice(0, 2), [2, 2]);
  assert.equal(Math.min(...gaps.values()), 2);
  assert.equal(Math.max(...gaps.values()), 7);
  assert.equal(gaps.size, 16);
  assert.equal(junctionOf(relModel, SS).size, 16);
});

test('a facing pair’s relatives are the pairs with its junction as it is, mirrored, upside down or turned half round', () => {
  assert.deepEqual(relativesOf(relModel, AS), [
    { id: SB, relation: 'mirror', exact: true },
    { id: BS, relation: 'flip', exact: true },
    { id: SA, relation: 'turn', exact: true },
    { id: DS, relation: 'same', exact: false },
  ]);
  assert.deepEqual(
    relativesOf(relModel, SB).map((relative) => [relative.id, relative.relation]),
    [
      [AS, 'mirror'],
      [BS, 'turn'],
      [SA, 'flip'],
      [DS, 'mirror'],
    ],
  );
  assert.deepEqual(relativesOf(relModel, SS), []);
  assert.deepEqual([...classMembers(relModel, AS)], [AS, SB, BS, SA, DS]);
  assert.deepEqual([...classMembers(relModel, SS)], [SS]);
  assert.equal(relativesIndex(relModel), relativesIndex(relModel));
});

test('ink set well back from the junction makes a relative that is not pixel for pixel one, and the view says which', () => {
  assert.equal(relationText({ relation: 'mirror', exact: true }), 'its mirror image, pixel for pixel');
  assert.equal(relationText({ relation: 'same', exact: false }), 'the same two edges, as far as 3 px past the closest ink');
  assert.equal(relationText({ relation: 'flip', distance: 2 }), 'nearly the same junction upside down: 2 px of difference in all');
});

test('a near relative’s junction differs by a few pixels in all, and a pair further off is none', () => {
  assert.deepEqual(nearRelativesOf(relModel, AS), [{ id: CS, relation: 'same', distance: 3 }]);
  assert.deepEqual(
    nearRelativesOf(relModel, CS).map((relative) => relative.id),
    [AS, SB, BS, SA, DS],
  );
  assert.deepEqual(nearRelativesOf(relModel, SS), []);
});

test('relatives that take more than one value are kerned inconsistently, until the odd one is marked apart', () => {
  const even = valuesWith();
  assert.deepEqual(splitStanding(relModel, even.values, even.valueOf), { pairs: new Set(), classes: 0 });
  const odd = valuesWith({ [AS]: -1 });
  const standing = splitStanding(relModel, odd.values, odd.valueOf);
  assert.equal(standing.classes, 1);
  assert.deepEqual([...standing.pairs], [AS, SB, BS, SA, DS]);
  const marked = valuesWith({ [AS]: -1 }, [AS]);
  assert.equal(isApart(relModel, marked.values, AS), true);
  assert.equal(isApart(relModel, marked.values, SB), false);
  assert.equal(splitStanding(relModel, marked.values, marked.valueOf).classes, 0);
  // A near relative is in no count.
  const near = valuesWith({ [CS]: -2 });
  assert.equal(splitStanding(relModel, near.values, near.valueOf).classes, 0);
});

test('the value to take is the one most relatives have, and the relatives to give a value to are those that differ', () => {
  const odd = valuesWith({ [AS]: -1 });
  assert.deepEqual(relativesValue(relModel, odd.values, odd.valueOf, AS), { value: 0, tied: [0] });
  assert.deepEqual(relativesToGive(relModel, odd.values, odd.valueOf, AS, -1), [SB, BS, SA, DS]);
  const tied = valuesWith({ [SB]: -1, [BS]: -1 });
  assert.deepEqual(relativesValue(relModel, tied.values, tied.valueOf, AS), { value: null, tied: [-1, 0] });
  assert.deepEqual(relativesValue(relModel, tied.values, tied.valueOf, SS), { value: null, tied: [] });
  const apart = valuesWith({ [SB]: -1, [BS]: -1 }, [SB]);
  assert.deepEqual(relativesValue(relModel, apart.values, apart.valueOf, AS), { value: 0, tied: [0] });
  assert.deepEqual(relativesToGive(relModel, apart.values, apart.valueOf, AS, 0), [BS]);
});

test('the tour of inconsistent pairs moves in reading order, wraps round, and leaves the selected pair’s own class behind', () => {
  const split = new Set([AS, SB, SS]);
  const at = (id) => selectionOfPair(relModel, id);
  assert.deepEqual(at(SB), { cursor: { row: 2, col: 1 }, facing: 0 });
  assert.deepEqual(nextSplit(relModel, split, at(AS).cursor, 0, 1), at(SB));
  assert.deepEqual(nextSplit(relModel, split, at(AS).cursor, 0, -1), at(SS));
  assert.deepEqual(nextSplit(relModel, split, at(AS).cursor, 0, 1, classMembers(relModel, AS)), at(SS));
  assert.deepEqual(nextSplit(relModel, split, at(SS).cursor, 0, 1), at(AS));
  assert.equal(nextSplit(relModel, new Set([AS]), at(AS).cursor, 0, 1), null);
  assert.equal(nextSplit(relModel, new Set(), at(AS).cursor, 0, 1), null);
});

test('marking a pair apart moves on to the next class still kerned inconsistently, reading the mark as made', () => {
  const odd = valuesWith({ [AS]: -1 });
  const from = selectionOfPair(relModel, AS);
  assert.equal(nextAfterApart(relModel, odd.values, odd.valueOf, from.cursor, from.facing, [relModel.pairKeys[AS]]), null);
});

test('the server’s apart marks are read by facing pair key, and a side is named by its letter', () => {
  assert.deepEqual([...values.apart], []);
  const marked = indexValues({ ...payload, apart: { pairs: ['qsNo[loop]|qsTea[full]'], stale: [], error: null } });
  assert.equal(isApart(model, marked, loopFull), true);
  assert.equal(isApart(model, marked, flippedHalf), false);
  assert.deepEqual([...indexValues(null).apart], []);
  assert.equal(pairLabel(model, flippedHalf), '·No[flipped] | ·Tea[half exit=x-height]');
  assert.equal(pairLabel(relModel, AS), '·A | ·S');
});

test('the first shapes to compare before one second shape are those with a like edge, most alike first, and the rest of the column after', () => {
  // ·C is ·A but for a pixel on one row; the stem, the Tall ·D and the mirrored ·B are further off than a pixel a row.
  assert.deepEqual(comparablesOf(relModel, AS, 'lead'), {
    budget: 6,
    like: [{ id: CS, distance: 1, offset: 0 }],
    rest: [
      { id: SS, distance: 9, offset: -3 },
      { id: DS, distance: 12, offset: 0 },
      { id: BS, distance: 18, offset: -1 },
    ],
  });
  // After ·S, ·A's diagonal and ·B's are as far from a stem as each other, and a pair is never its own comparable.
  const after = comparablesOf(relModel, SB, 'follow');
  assert.deepEqual(after.like, []);
  assert.deepEqual(
    after.rest.map((one) => one.id),
    [SS, SA],
  );
});

test('two edges are alike once the distance each is set from the junction is taken out, and a row only one inks counts against them', () => {
  const edge = (blanks) => new Map(blanks.map((blank, y) => [y, blank]));
  assert.deepEqual(edgeLikeness(edge([1, 2, 3]), edge([3, 4, 5])), { distance: 0, offset: 2 });
  assert.deepEqual(edgeLikeness(edge([1, 2, 3]), edge([1, 2, 5])), { distance: 2, offset: 0 });
  assert.deepEqual(edgeLikeness(edge([1, 2, 3]), edge([1, 2])), { distance: 4, offset: 0 });
  assert.equal(edgeLikeness(edge([1]), new Map([[7, 1]])), null);
  assert.equal(likenessText({ distance: 0, offset: 0 }), 'the same edge');
  assert.equal(likenessText({ distance: 3, offset: 2 }), '3 px from the same edge in all, set 2 px further from the junction');
  assert.equal(likenessText({ distance: 3, offset: -1 }), '3 px from the same edge in all, set 1 px nearer to the junction');
  assert.equal(likenessText({ distance: Infinity, offset: 0 }), 'inks none of the same rows');
});

test('a pair’s clearance is its closest gap moved by its value, and the value for a clearance undoes that', () => {
  assert.equal(clearanceOf(relModel, AS, 0), 2);
  assert.equal(clearanceOf(relModel, AS, -1), 1);
  assert.equal(valueForClearance(relModel, CS, 1), -1);
  assert.equal(valueForClearance(relModel, SS, 1), -1);
  assert.equal(valueForClearance(relModel, SS, null), null);
});

test('the comparison key steps through the relatives, the like first shapes and the like second shapes, and round again', () => {
  assert.equal(nextComparison('junction'), 'lead');
  assert.equal(nextComparison('lead'), 'follow');
  assert.equal(nextComparison('follow'), 'junction');
});

test('a pair of letters’ facing pairs are listed by second shape, so the pairs that share one stand together', () => {
  const order = facingOrder(model, noTea);
  assert.deepEqual([...order].sort((one, other) => one - other), [...noTea.pairs.keys()]);
  const follows = order.map((index) => model.doc.pairs[noTea.pairs[index]].follow);
  assert.deepEqual(follows, [...follows].sort((one, other) => one - other));
  const leadsWithin = order.filter((index) => model.doc.pairs[noTea.pairs[index]].follow === follows[0]).map((index) => model.doc.pairs[noTea.pairs[index]].lead);
  assert.deepEqual(leadsWithin, [...leadsWithin].sort((one, other) => one - other));
  assert.deepEqual(facingOrder(model, null), []);
  // A made-up entry whose pairs alternate between two second shapes comes out with each shape's pairs together.
  const made = { doc: { pairs: [{ lead: 0, follow: 5 }, { lead: 0, follow: 6 }, { lead: 1, follow: 5 }, { lead: 1, follow: 6 }] } };
  assert.deepEqual(facingOrder(made, { pairs: [0, 1, 2, 3] }), [0, 2, 1, 3]);
});

test('up and down step through that order, and past either end give way to another pair of letters', () => {
  const order = [0, 2, 1, 3];
  assert.equal(stepInOrder(order, 0, 1), 2);
  assert.equal(stepInOrder(order, 2, 1), 1);
  assert.equal(stepInOrder(order, 1, -1), 2);
  assert.equal(stepInOrder(order, 3, 1), null);
  assert.equal(stepInOrder(order, 0, -1), null);
  assert.equal(stepInOrder([], 0, 1), null);
});

test('a tile names a relative’s relation in a word or two', () => {
  assert.equal(relationWord({ relation: 'mirror', exact: true }), 'mirror');
  assert.equal(relationWord({ relation: 'flip', exact: false }), '≈ upside down');
  assert.equal(relationWord({ relation: 'turn', distance: 2 }), 'near half turn, 2 px');
});
