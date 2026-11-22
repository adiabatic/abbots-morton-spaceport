import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { LETTER_CODEPOINTS, normalizeName, parsePreview, stylisticSetsOf } from '../static/preview.js';

test('the letter map covers all 44 letters at their doc/glyph-names.md codepoints', () => {
  assert.equal(LETTER_CODEPOINTS.size, 44);
  assert.equal(LETTER_CODEPOINTS.get('pea'), 0xe650);
  assert.equal(LETTER_CODEPOINTS.get('exam'), 0xe66c);
  assert.equal(LETTER_CODEPOINTS.get('it'), 0xe670);
  assert.equal(LETTER_CODEPOINTS.get('ooze'), 0xe67e);
});

test('normalizeName strips apostrophes and hyphens and lowercases', () => {
  assert.equal(normalizeName("J'ai"), 'jai');
  assert.equal(normalizeName('J’ai'), 'jai');
  assert.equal(normalizeName('-ing'), 'ing');
  assert.equal(normalizeName('Utter'), 'utter');
});

test('middot-joined names concatenate into one word', () => {
  assert.deepEqual(parsePreview('·day·utter'), { text: '\ue653\ue67a', unknown: [] });
});

test('a leading name without its middot still parses', () => {
  assert.deepEqual(parsePreview('day·utter·low'), { text: '\ue653\ue67a\ue667', unknown: [] });
});

test('whitespace separates words with a literal space', () => {
  assert.deepEqual(parsePreview('·day·utter ·low'), { text: '\ue653\ue67a \ue667', unknown: [] });
  assert.deepEqual(parsePreview('  day   utter  '), { text: '\ue653 \ue67a', unknown: [] });
});

test('plain dots separate names when every piece is recognized', () => {
  assert.deepEqual(parsePreview('.day.utter'), { text: '\ue653\ue67a', unknown: [] });
});

test('dots stay literal when the pieces are not all recognized tokens', () => {
  assert.deepEqual(parsePreview('3.14'), { text: '3.14', unknown: [] });
});

test('boundary tokens from the notation line work', () => {
  assert.deepEqual(parsePreview('·day·utter ␣ ·low'), { text: '\ue653\ue67a   \ue667', unknown: [] });
  assert.deepEqual(parsePreview('·it ◊ZWNJ ·eat'), { text: '\ue670 \u200c \ue671', unknown: [] });
  assert.deepEqual(parsePreview('·day·zwnj·utter'), { text: '\ue653\u200c\ue67a', unknown: [] });
  assert.deepEqual(parsePreview('·'), { text: '·', unknown: [] });
});

test('an adjacent diamond starts a boundary token without inserting spaces', () => {
  assert.deepEqual(parsePreview('·They·It◊ZWNJ·Day'), { text: '\ue657\ue670\u200c\ue653', unknown: [] });
  assert.deepEqual(parsePreview('.They.It◊ZWNJ.Day'), { text: '\ue657\ue670\u200c\ue653', unknown: [] });
  assert.deepEqual(parsePreview('◊zwnj·It◊ZWNJ◊ZWNJ'), { text: '\u200c\ue670\u200c\u200c', unknown: [] });
  assert.deepEqual(parsePreview('·It◊unknown·Day'), { text: '\ue670\ue653', unknown: ['◊unknown'] });
});

test('raw codepoint tokens resolve, with bare hex confined to the PUA', () => {
  assert.deepEqual(parsePreview('uniE653'), { text: '\ue653', unknown: [] });
  assert.deepEqual(parsePreview('U+E653·utter'), { text: '\ue653\ue67a', unknown: [] });
  assert.deepEqual(parsePreview('e653'), { text: '\ue653', unknown: [] });
  assert.deepEqual(parsePreview('deed'), { text: '', unknown: ['deed'] });
});

test('names win over hex readings', () => {
  assert.deepEqual(parsePreview('·fee'), { text: '\ue658', unknown: [] });
});

test('pasted Quikscript characters pass through verbatim', () => {
  assert.deepEqual(parsePreview('\ue653\ue67a\ue667!'), { text: '\ue653\ue67a\ue667!', unknown: [] });
});

test('unrecognized names are reported and contribute nothing', () => {
  assert.deepEqual(parsePreview('·day·dayx'), { text: '\ue653', unknown: ['dayx'] });
});

test('empty and blank input parse to nothing', () => {
  assert.deepEqual(parsePreview(''), { text: '', unknown: [] });
  assert.deepEqual(parsePreview('   '), { text: '', unknown: [] });
});

// A font file holding only the table directory and, for each of `tables`, a header whose feature list names `features`. stylisticSetsOf reads nothing else.
function fontWithFeatures(tables) {
  const entries = Object.entries(tables);
  const directoryEnd = 12 + entries.length * 16;
  const bodies = entries.map(([, features]) => 10 + features.length * 6);
  const bytes = new Uint8Array(directoryEnd + bodies.reduce((sum, length) => sum + length, 0));
  const view = new DataView(bytes.buffer);
  const writeTag = (offset, tag) => bytes.set([...tag].map((char) => char.charCodeAt(0)), offset);
  view.setUint32(0, 0x4f54544f);
  view.setUint16(4, entries.length);
  let tableStart = directoryEnd;
  for (const [index, [tag, features]] of entries.entries()) {
    const record = 12 + index * 16;
    writeTag(record, tag);
    view.setUint32(record + 8, tableStart);
    view.setUint32(record + 12, bodies[index]);
    view.setUint16(tableStart, 1);
    view.setUint16(tableStart + 6, 8);
    view.setUint16(tableStart + 8, features.length);
    for (const [position, feature] of features.entries()) writeTag(tableStart + 10 + position * 6, feature);
    tableStart += bodies[index];
  }
  return bytes;
}

test('stylisticSetsOf lists the GSUB and GPOS stylistic sets once each, sorted, and skips other features and tables', () => {
  const font = fontWithFeatures({
    'CFF ': ['ss01'],
    GPOS: ['curs', 'ss04'],
    GSUB: ['calt', 'ss10', 'ss03', 'ss03', 'ss04', 'ss21', 'ss00'],
  });
  assert.deepEqual(stylisticSetsOf(font), ['ss03', 'ss04', 'ss10']);
  assert.deepEqual(stylisticSetsOf(font.buffer), ['ss03', 'ss04', 'ss10']);
});

test('stylisticSetsOf finds no sets in a font without them', () => {
  assert.deepEqual(stylisticSetsOf(fontWithFeatures({ GSUB: ['calt'], GPOS: ['curs'] })), []);
  assert.deepEqual(stylisticSetsOf(fontWithFeatures({})), []);
});

test('stylisticSetsOf reads a real rebuild font, including through a view that starts partway into its buffer', async () => {
  const font = await readFile(new URL('../fixtures/mini/M1.otf', import.meta.url));
  const sets = stylisticSetsOf(font);
  const padded = new Uint8Array(font.length + 7);
  padded.set(font, 7);
  assert.deepEqual(stylisticSetsOf(padded.subarray(7)), sets);
  assert.ok(sets.includes('ss10'), `the mini M1 font's sets ${sets.join(', ')} should include ss10`);
  for (const set of sets) assert.match(set, /^ss\d{2}$/u);
  assert.deepEqual(sets, [...sets].sort());
});
