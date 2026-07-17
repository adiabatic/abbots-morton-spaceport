import test from 'node:test';
import assert from 'node:assert/strict';
import { LETTER_CODEPOINTS, normalizeName, parsePreview } from '../static/preview.js';

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
