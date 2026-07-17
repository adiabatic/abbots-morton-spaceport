export const LETTER_CODEPOINTS = new Map([
  ['pea', 0xe650],
  ['bay', 0xe651],
  ['tea', 0xe652],
  ['day', 0xe653],
  ['key', 0xe654],
  ['gay', 0xe655],
  ['thaw', 0xe656],
  ['they', 0xe657],
  ['fee', 0xe658],
  ['vie', 0xe659],
  ['see', 0xe65a],
  ['zoo', 0xe65b],
  ['she', 0xe65c],
  ['jai', 0xe65d],
  ['cheer', 0xe65e],
  ['jay', 0xe65f],
  ['ye', 0xe660],
  ['way', 0xe661],
  ['he', 0xe662],
  ['why', 0xe663],
  ['ing', 0xe664],
  ['may', 0xe665],
  ['no', 0xe666],
  ['low', 0xe667],
  ['roe', 0xe668],
  ['loch', 0xe669],
  ['llan', 0xe66a],
  ['excite', 0xe66b],
  ['exam', 0xe66c],
  ['it', 0xe670],
  ['eat', 0xe671],
  ['et', 0xe672],
  ['eight', 0xe673],
  ['at', 0xe674],
  ['i', 0xe675],
  ['ah', 0xe676],
  ['awe', 0xe677],
  ['ox', 0xe678],
  ['oy', 0xe679],
  ['utter', 0xe67a],
  ['out', 0xe67b],
  ['owe', 0xe67c],
  ['foot', 0xe67d],
  ['ooze', 0xe67e],
]);

const SPECIAL_TOKENS = new Map([
  ['zwnj', '\u200c'],
  ['◊zwnj', '\u200c'],
  ['␣', ' '],
]);

export function normalizeName(piece) {
  return piece.toLowerCase().replace(/['’-]/gu, '');
}

function hexChar(piece) {
  const explicit = piece.match(/^(?:u\+|uni)([0-9a-f]{4,6})$/i);
  const bare = explicit ? null : piece.match(/^[0-9a-f]{4}$/i);
  if (!explicit && !bare) return null;
  const code = parseInt(explicit ? explicit[1] : piece, 16);
  // Bare hex is only honored inside the BMP private use area, so an ordinary word that happens to be hex ("deed") stays an unknown-name hint instead of a surrogate or a random character.
  if (!explicit && !(code >= 0xe000 && code <= 0xf8ff)) return null;
  if (code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return null;
  return String.fromCodePoint(code);
}

function resolveToken(piece) {
  const special = SPECIAL_TOKENS.get(piece.toLowerCase());
  if (special !== undefined) return special;
  const codepoint = LETTER_CODEPOINTS.get(normalizeName(piece));
  if (codepoint !== undefined) return String.fromCodePoint(codepoint);
  return hexChar(piece);
}

function piecesOf(word) {
  // Plain dots work as separators too (.day.utter), but only when every piece is a recognized token — otherwise the dot is literal text (3.14, actual punctuation in pasted Quikscript).
  if (word.includes('.')) {
    const dotSplit = word.split(/[·.]/u);
    if (dotSplit.every((piece) => piece === '' || resolveToken(piece) !== null)) return dotSplit;
  }
  return word.split('·');
}

export function parsePreview(input) {
  const unknown = [];
  const words = [];
  for (const word of input.trim().split(/\s+/u)) {
    if (word === '') continue;
    if (word === '·') {
      words.push('·');
      continue;
    }
    let text = '';
    for (const piece of piecesOf(word)) {
      if (piece === '') continue;
      const resolved = resolveToken(piece);
      if (resolved !== null) text += resolved;
      else if (!/[a-z]/iu.test(piece)) text += piece;
      else unknown.push(piece);
    }
    words.push(text);
  }
  return { text: words.join(' '), unknown };
}
