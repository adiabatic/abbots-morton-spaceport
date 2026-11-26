// The kerning view's drawing as pure functions: draw lists of pixel squares from the inventory's glyphs and pen positions, and painters that fill a 2D context they are handed. Glyph-space y runs from `STAGE_TOP` at the top row of a stage to `STAGE_BOTTOM` at its bottom row, the Tall and Deep letters' extent.

import { closestInk, shiftedPositions } from './kern.js';

export const STAGE_TOP = 8;
export const STAGE_BOTTOM = -3;
export const STAGE_ROWS = STAGE_TOP - STAGE_BOTTOM + 1;
export const BASELINE_Y = 0;
export const X_HEIGHT_Y = 5;
export const ROLES = ['lead', 'follow', 'other', 'hit'];

// One glyph's ink as squares, its drawing's left edge `inkX` pixels right of its pen position `originX`.
export function glyphSquares(glyph, originX, inkX) {
  const squares = [];
  const top = glyph.y + glyph.rows.length - 1;
  for (const [row, line] of glyph.rows.entries()) {
    for (const [column, pixel] of [...line].entries()) {
      if (pixel === '#') squares.push({ x: originX + inkX + column, y: top - row });
    }
  }
  return squares;
}

// The squares of glyphs at the given pen positions, each in the role the junction at `focusGap` gives it: `lead` left of it, `follow` right of it, `other` elsewhere, and `hit` where two glyphs ink one pixel. Returns the squares, their horizontal extent and the closest ink across the focused junction.
function placedSquares(model, glyphIds, positions, focusGap) {
  const inkX = model.doc.ink_x ?? 1;
  const squares = [];
  const owner = new Map();
  const sides = { lead: [], follow: [] };
  for (const [index, glyphId] of glyphIds.entries()) {
    let role = 'other';
    if (focusGap !== null && index === focusGap) role = 'lead';
    if (focusGap !== null && index === focusGap + 1) role = 'follow';
    for (const square of glyphSquares(model.doc.glyphs[glyphId], positions[index], inkX)) {
      const placed = { ...square, role };
      const key = `${square.x},${square.y}`;
      const earlier = owner.get(key);
      if (earlier && earlier.glyph !== index) {
        earlier.square.role = 'hit';
        placed.role = 'hit';
      }
      owner.set(key, { glyph: index, square: placed });
      squares.push(placed);
      if (role === 'lead' || role === 'follow') sides[role].push(square);
    }
  }
  let minX = Infinity;
  let maxX = -Infinity;
  for (const square of squares) {
    minX = Math.min(minX, square.x);
    maxX = Math.max(maxX, square.x);
  }
  if (squares.length === 0) {
    minX = 0;
    maxX = 0;
  }
  const closest = focusGap === null ? null : closestInk(sides.lead, sides.follow);
  return { squares, minX, maxX, closest };
}

// A text drawn with every break moved by its facing pair's value (`valueOfPair`), the glyphs around `focusGap` marked.
export function textDrawList(model, text, valueOfPair, focusGap = null) {
  return placedSquares(model, text.glyphs, shiftedPositions(text, valueOfPair), focusGap);
}

// Two glyphs side by side at a break moved by `value`: a facing pair with no witness text, and a grid cell.
export function pairDrawList(model, member, value) {
  return placedSquares(model, member, [0, model.doc.glyphs[member[0]].adv + value], 0);
}

// The two glyphs a grid cell draws: the representative's `rep_member`, the shapes the two letters settle to when written alone, or the representative's own member in an inventory without one.
function cellMember(model, entry) {
  return entry.rep_member ?? model.doc.pairs[entry.rep ?? entry.pairs[0]].member;
}

// A grid cell: the rune pair's representative facing pair drawn at its value, with that value, how many facing pairs the two runes have, and the representative's id. Null for a rune pair with no facing pair, which the grid leaves blank.
export function cellDrawList(model, entry, valueOfPair) {
  if (!entry || entry.pairs.length === 0) return null;
  const rep = entry.rep ?? entry.pairs[0];
  const value = valueOfPair(rep);
  return { ...pairDrawList(model, cellMember(model, entry), value), rep, value, count: entry.pairs.length };
}

// A facing pair's drawings: each member of a pair with a mixed side whose facing edges differ (the inventory's `drawings`), or none for a pair whose side draws one way.
export function drawingsOf(pair) {
  return Array.isArray(pair?.drawings) && pair.drawings.length > 1 ? pair.drawings : [];
}

// The widest a grid cell's two glyphs can be at a value of 0, in pixels: the leading glyph's advance and the following glyph's drawing.
export function gridCellPixels(model) {
  const inkX = model.doc.ink_x ?? 1;
  let widest = 0;
  for (const entry of model.doc.rune_pairs) {
    if (entry.pairs.length === 0) continue;
    const [lead, follow] = cellMember(model, entry);
    const rows = model.doc.glyphs[follow].rows;
    const followWidth = Math.max(0, ...rows.map((row) => row.length));
    widest = Math.max(widest, model.doc.glyphs[lead].adv + inkX + followWidth);
  }
  return widest;
}

// Fill `squares` into `context` at `scale` canvas units a pixel, the pixel x=0 at `originX` and the stage's top row at `originY`, each in its role's color. With `clip` ({left, right} in canvas units), a square that would not lie wholly between them is left out, so a grid cell's pair at a large value stays in its cell.
export function paintSquares(context, squares, { scale, originX, originY, colors, clip = null }) {
  for (const square of squares) {
    const x = originX + square.x * scale;
    if (clip && (x < clip.left || x + scale > clip.right)) continue;
    context.fillStyle = colors[square.role] ?? colors.other;
    context.fillRect(x, originY + (STAGE_TOP - square.y) * scale, scale, scale);
  }
}

// The baseline and the x-height as hairlines across a stage: the baseline under the y=0 row, the x-height over the y=5 row.
export function paintGuides(context, { width, scale, originX, originY, color }) {
  context.fillStyle = color;
  for (const y of [STAGE_TOP - BASELINE_Y + 1, STAGE_TOP - X_HEIGHT_Y]) context.fillRect(originX, originY + y * scale, width, 1);
}
