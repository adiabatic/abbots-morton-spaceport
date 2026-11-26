import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { indexInventory, runePairAt } from '../static/kern.js';
import {
  STAGE_ROWS,
  STAGE_TOP,
  cellDrawList,
  drawingsOf,
  glyphSquares,
  gridCellPixels,
  paintGuides,
  paintSquares,
  pairDrawList,
  textDrawList,
} from '../static/kern-draw.js';

const fixtureDir = new URL('../fixtures/', import.meta.url);
const inventory = JSON.parse(await readFile(new URL('kern-inventory.json', fixtureDir), 'utf8'));
const model = indexInventory(inventory);
const rune = (name) => model.runeIndex.get(name);

function recorder() {
  const calls = [];
  return {
    calls,
    set fillStyle(value) {
      calls.push(['fillStyle', value]);
    },
    fillRect(...args) {
      calls.push(['fillRect', ...args]);
    },
  };
}

test('a glyph’s squares sit right of its pen position by the drawing’s left bearing, its bottom row at its y', () => {
  const glyph = { rows: ['# ', ' #'], y: -1, adv: 3 };
  assert.deepEqual(glyphSquares(glyph, 5, 1), [
    { x: 6, y: 0 },
    { x: 7, y: -1 },
  ]);
});

test('a text is drawn at the inventory’s positions with each break shifted, the junction’s glyphs marked', () => {
  const noTea = runePairAt(model, rune('qsNo'), rune('qsTea'));
  const id = noTea.pairs.find((pairId) => inventory.pairs[pairId].witness !== null);
  const pair = inventory.pairs[id];
  const text = inventory.texts[pair.witness];
  const still = textDrawList(model, text, () => 0, pair.at);
  const moved = textDrawList(model, text, (gap) => (gap === id ? -1 : 0), pair.at);
  const following = (draw) => draw.squares.filter((square) => square.role === 'follow' || square.role === 'hit');
  assert.ok(still.squares.some((square) => square.role === 'lead'));
  assert.ok(following(still).length > 0);
  const stillFollow = following(still).map((square) => square.x).sort((a, b) => a - b);
  const movedFollow = following(moved).map((square) => square.x).sort((a, b) => a - b);
  assert.deepEqual(movedFollow, stillFollow.map((x) => x - 1));
  assert.equal(moved.closest.gap, still.closest.gap - 1);
});

test('ink two glyphs share is drawn as a hit in both', () => {
  const glyphs = [
    { name: 'a', rune: 0, rows: ['##'], y: 0, adv: 2 },
    { name: 'b', rune: 0, rows: ['#'], y: 0, adv: 2 },
  ];
  const tiny = indexInventory({ ...inventory, glyphs, pairs: [], rune_pairs: [], texts: [] });
  const draw = pairDrawList(tiny, [0, 1], -1);
  assert.deepEqual(
    draw.squares.map((square) => [square.x, square.role]),
    [
      [1, 'lead'],
      [2, 'hit'],
      [2, 'hit'],
    ],
  );
  assert.equal(draw.closest.gap, -1);
});

test('a grid cell draws its representative facing pair at its value, with the count of facing pairs, and an empty rune pair draws nothing', () => {
  const noTea = runePairAt(model, rune('qsNo'), rune('qsTea'));
  const cell = cellDrawList(model, noTea, (id) => (id === noTea.rep ? -2 : 0));
  assert.equal(cell.rep, noTea.rep);
  assert.equal(cell.value, -2);
  assert.equal(cell.count, noTea.pairs.length);
  assert.equal(cellDrawList(model, null, () => 0), null);
  assert.equal(cellDrawList(model, { ...noTea, pairs: [] }, () => 0), null);
  assert.ok(gridCellPixels(model) >= cell.maxX - cell.minX);
});

test('the painters fill a square per pixel in its role’s color, and the baseline and x-height as hairlines', () => {
  const context = recorder();
  paintSquares(context, [{ x: 2, y: 0, role: 'lead' }, { x: 3, y: 8, role: 'unknown' }], {
    scale: 4,
    originX: 10,
    originY: 20,
    colors: { lead: 'teal', other: 'gray' },
  });
  assert.deepEqual(context.calls, [
    ['fillStyle', 'teal'],
    ['fillRect', 18, 20 + STAGE_TOP * 4, 4, 4],
    ['fillStyle', 'gray'],
    ['fillRect', 22, 20, 4, 4],
  ]);
  const guides = recorder();
  paintGuides(guides, { width: 50, scale: 2, originX: 0, originY: 0, color: 'line' });
  assert.deepEqual(guides.calls, [
    ['fillStyle', 'line'],
    ['fillRect', 0, (STAGE_TOP + 1) * 2, 50, 1],
    ['fillRect', 0, (STAGE_TOP - 5) * 2, 50, 1],
  ]);
  assert.equal(STAGE_ROWS, 12);
});

test('a grid cell draws the shapes the two letters settle to alone, its rune pair’s rep_member, not the member its witness shows', () => {
  const noTea = runePairAt(model, rune('qsNo'), rune('qsTea'));
  const pair = inventory.pairs[noTea.rep];
  const other = inventory.glyphs.findIndex((glyph, index) => glyph.rune === rune('qsNo') && index !== noTea.rep_member[0]);
  assert.ok(other >= 0);
  const entry = { ...noTea, rep_member: [other, noTea.rep_member[1]] };
  const drawn = cellDrawList(model, entry, () => 0);
  const expected = pairDrawList(model, entry.rep_member, 0);
  assert.deepEqual(drawn.squares, expected.squares);
  assert.deepEqual(cellDrawList(model, noTea, () => 0).squares, pairDrawList(model, noTea.rep_member, 0).squares);
  assert.ok(pair.member);
});

test('a facing pair draws each of its drawings only when a mixed side gives it more than one', () => {
  assert.deepEqual(drawingsOf({ member: [0, 1] }), []);
  assert.deepEqual(drawingsOf({ member: [0, 1], drawings: [[0, 1]] }), []);
  assert.deepEqual(drawingsOf({ member: [0, 1], drawings: [[0, 1], [2, 1]] }), [[0, 1], [2, 1]]);
});

test('a clip leaves out every square that would not lie wholly inside it, so a wide pair stays in its grid cell', () => {
  const context = recorder();
  paintSquares(context, [{ x: 0, y: 0, role: 'lead' }, { x: 3, y: 0, role: 'follow' }, { x: 5, y: 0, role: 'follow' }], {
    scale: 2,
    originX: 10,
    originY: 0,
    colors: { lead: 'a', follow: 'b', other: 'c' },
    clip: { left: 10, right: 18 },
  });
  assert.deepEqual(
    context.calls.filter((call) => call[0] === 'fillRect').map((call) => call[1]),
    [10, 16],
  );
});
