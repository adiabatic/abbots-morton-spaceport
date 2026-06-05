export const LETTERS = [
  { code: 0xE650, name: 'Pea', family: 'qsPea' },
  { code: 0xE651, name: 'Bay', family: 'qsBay' },
  { code: 0xE652, name: 'Tea', family: 'qsTea' },
  { code: 0xE653, name: 'Day', family: 'qsDay' },
  { code: 0xE654, name: 'Key', family: 'qsKey' },
  { code: 0xE655, name: 'Gay', family: 'qsGay' },
  { code: 0xE656, name: 'Thaw', family: 'qsThaw' },
  { code: 0xE657, name: 'They', family: 'qsThey' },
  { code: 0xE658, name: 'Fee', family: 'qsFee' },
  { code: 0xE659, name: 'Vie', family: 'qsVie' },
  { code: 0xE65A, name: 'See', family: 'qsSee' },
  { code: 0xE65B, name: 'Zoo', family: 'qsZoo' },
  { code: 0xE65C, name: 'She', family: 'qsShe' },
  { code: 0xE65D, name: "J'ai", family: 'qsJai' },
  { code: 0xE65E, name: 'Cheer', family: 'qsCheer' },
  { code: 0xE65F, name: 'Jay', family: 'qsJay' },
  { code: 0xE660, name: 'Ye', family: 'qsYe' },
  { code: 0xE661, name: 'Way', family: 'qsWay' },
  { code: 0xE662, name: 'He', family: 'qsHe' },
  { code: 0xE663, name: 'Why', family: 'qsWhy' },
  { code: 0xE664, name: '-ing', family: 'qsIng' },
  { code: 0xE665, name: 'May', family: 'qsMay' },
  { code: 0xE666, name: 'No', family: 'qsNo' },
  { code: 0xE667, name: 'Low', family: 'qsLow' },
  { code: 0xE668, name: 'Roe', family: 'qsRoe' },
  { code: 0xE669, name: 'Loch', family: 'qsLoch' },
  { code: 0xE66A, name: 'Llan', family: 'qsLlan' },
  { code: 0xE66B, name: 'Excite', family: 'qsExcite' },
  { code: 0xE66C, name: 'Exam', family: 'qsExam' },
  { code: 0xE670, name: 'It', family: 'qsIt' },
  { code: 0xE671, name: 'Eat', family: 'qsEat' },
  { code: 0xE672, name: 'Et', family: 'qsEt' },
  { code: 0xE673, name: 'Eight', family: 'qsEight' },
  { code: 0xE674, name: 'At', family: 'qsAt' },
  { code: 0xE675, name: 'I', family: 'qsI' },
  { code: 0xE676, name: 'Ah', family: 'qsAh' },
  { code: 0xE677, name: 'Awe', family: 'qsAwe' },
  { code: 0xE678, name: 'Ox', family: 'qsOx' },
  { code: 0xE679, name: 'Oy', family: 'qsOy' },
  { code: 0xE67A, name: 'Utter', family: 'qsUtter' },
  { code: 0xE67B, name: 'Out', family: 'qsOut' },
  { code: 0xE67C, name: 'Owe', family: 'qsOwe' },
  { code: 0xE67D, name: 'Foot', family: 'qsFoot' },
  { code: 0xE67E, name: 'Ooze', family: 'qsOoze' },
];

export const NAME_TO_CP = Object.fromEntries(
  LETTERS.map(l => [l.name, l.code])
);

for (const l of LETTERS) {
  const normalized = l.name.replace(/[\u2018\u2019]/g, "'");
  if (normalized !== l.name) NAME_TO_CP[normalized] = l.code;
}

NAME_TO_CP["Jai"] = NAME_TO_CP["J'ai"];

const PIXEL_FONT_RE = /Abbots Morton Spaceport|Departure Mono/;

// Smoothing must travel with the font: any code that sets --font-stack must set the --font-smoothing* trio alongside it. The pixel fonts want smoothing off; everything else wants the platform default.
function applyFontSmoothing(el, fontStack) {
  const pixel = PIXEL_FONT_RE.test(fontStack.split(',')[0]);
  el.style.setProperty('--font-smoothing', pixel ? 'none' : 'subpixel-antialiased');
  el.style.setProperty('--font-smoothing-osx', pixel ? 'grayscale' : 'auto');
  el.style.setProperty('--font-smooth', pixel ? 'never' : 'auto');
}

export function initToggles(opts = {}) {
  const fontOrderToggle = opts.fontOrderToggle && document.getElementById(opts.fontOrderToggle);
  const fontToggle = opts.fontToggle && document.getElementById(opts.fontToggle);
  const levelToggle = opts.levelToggle && document.getElementById(opts.levelToggle);
  const weightToggle = opts.weightToggle && document.getElementById(opts.weightToggle);
  const titleEl = opts.titleEl && document.querySelector(opts.titleEl);

  const sizeContainer = opts.sizeToggle && document.getElementById(opts.sizeToggle);
  const sizeDown = sizeContainer && sizeContainer.querySelector('.size-down');
  const sizeDisplay = sizeContainer && sizeContainer.querySelector('.size-display');
  const sizeUp = sizeContainer && sizeContainer.querySelector('.size-up');

  let isSans = true;
  let isSenior = true;
  let dmFirst = false;
  const weights = [400, 700];
  const weightLabels = ['Regular', 'Bold'];
  let weightIndex = 0;
  const sizes = [11, 22, 33, 44, 55, 66, 77, 88];
  let sizeIndex = 1;

  function applyState() {
    let fontFamily, fontTitle;
    if (isSans) {
      const level = isSenior ? 'Senior' : 'Junior';
      fontFamily = `'Abbots Morton Spaceport Sans ${level}'`;
      fontTitle = `Abbots Morton Spaceport Sans ${level}`;
      if (levelToggle) {
        levelToggle.textContent = level;
        levelToggle.hidden = false;
      }
      if (weightToggle) weightToggle.hidden = false;
    } else {
      fontFamily = "'Abbots Morton Spaceport Mono'";
      fontTitle = 'Abbots Morton Spaceport Mono';
      if (levelToggle) levelToggle.hidden = true;
      if (weightToggle) weightToggle.hidden = true;
    }

    const fontStack = dmFirst
      ? `'Departure Mono', ${fontFamily}, monospace`
      : `${fontFamily}, 'Departure Mono', monospace`;

    document.documentElement.style.setProperty('--font-stack', fontStack);
    applyFontSmoothing(document.documentElement, fontStack);
    const fontWeight = isSans ? weights[weightIndex] : 400;
    document.documentElement.style.setProperty('--font-weight', String(fontWeight));
    if (titleEl) titleEl.textContent = fontTitle;
    if (fontToggle) fontToggle.textContent = isSans ? 'Sans' : 'Mono';
    if (weightToggle) weightToggle.textContent = weightLabels[weightIndex];

    if (sizeDisplay) {
      document.documentElement.style.setProperty('--font-size', sizes[sizeIndex] + 'px');
      sizeDisplay.textContent = sizes[sizeIndex] + 'px';
    }

    if (opts.onApply) opts.onApply({ isSans, isSenior, dmFirst, fontTitle, fontStack });
  }

  if (fontToggle) {
    fontToggle.addEventListener('click', () => {
      isSans = !isSans;
      applyState();
    });
  }

  if (levelToggle) {
    levelToggle.addEventListener('click', () => {
      isSenior = !isSenior;
      applyState();
    });
  }

  if (weightToggle) {
    weightToggle.addEventListener('click', () => {
      weightIndex = (weightIndex + 1) % weights.length;
      applyState();
    });
  }

  if (sizeDown) {
    sizeDown.addEventListener('click', () => {
      if (sizeIndex > 0) sizeIndex--;
      applyState();
    });
  }

  if (sizeUp) {
    sizeUp.addEventListener('click', () => {
      if (sizeIndex < sizes.length - 1) sizeIndex++;
      applyState();
    });
  }

  if (fontOrderToggle) {
    fontOrderToggle.addEventListener('click', () => {
      dmFirst = !dmFirst;
      fontOrderToggle.textContent = dmFirst ? 'DM first' : 'AMS first';
      applyState();
    });
  }

  applyState();

  return { applyState, getState: () => ({ isSans, isSenior, dmFirst, weightIndex }) };
}

export function initHighlights(opts = {}) {
  const storageKey = opts.storageKey || 'ams-highlights';
  const clearButton = opts.clearButtonId && document.getElementById(opts.clearButtonId);
  const tables = document.querySelectorAll(opts.tableSelector || '.pairings');
  const wordLists = opts.wordListSelector ? document.querySelectorAll(opts.wordListSelector) : [];
  const passages = opts.passageSelector ? document.querySelectorAll(opts.passageSelector) : [];
  const cellPathFn = opts.cellPathFn || null;

  function loadHighlights() {
    try {
      return new Set(JSON.parse(localStorage.getItem(storageKey)) || []);
    } catch {
      return new Set();
    }
  }

  function saveHighlights(set) {
    localStorage.setItem(storageKey, JSON.stringify([...set]));
  }

  const highlighted = loadHighlights();

  function defaultCellPath(td) {
    const table = td.closest(opts.tableSelector || '.pairings');
    const tableIndex = [...tables].indexOf(table);
    const row = td.parentElement;
    const rowIndex = [...table.rows].indexOf(row);
    const cellIndex = [...row.cells].indexOf(td);
    return `${tableIndex}-${rowIndex}-${cellIndex}`;
  }

  const getCellPath = cellPathFn || defaultCellPath;

  for (const path of highlighted) {
    if (path.startsWith('dt-')) continue;
    if (cellPathFn) continue;
    const [ti, ri, ci] = path.split('-').map(Number);
    const td = tables[ti]?.rows[ri]?.cells[ci];
    if (td) td.classList.add('highlighted');
  }

  for (const table of tables) {
    table.addEventListener('click', (e) => {
      const td = e.target.closest('td');
      if (!td || td.isContentEditable) return;
      td.classList.toggle('highlighted');
      const path = getCellPath(td);
      if (td.classList.contains('highlighted')) {
        highlighted.add(path);
      } else {
        highlighted.delete(path);
      }
      saveHighlights(highlighted);
    });
  }

  if (wordLists.length > 0) {
    function dtPath(dt) {
      const dl = dt.closest(opts.wordListSelector);
      const dlIndex = [...wordLists].indexOf(dl);
      const dtIndex = [...dl.querySelectorAll('dt')].indexOf(dt);
      return `dt-${dlIndex}-${dtIndex}`;
    }

    for (const path of highlighted) {
      if (!path.startsWith('dt-')) continue;
      const [, di, dti] = path.split('-').map(Number);
      const dt = wordLists[di]?.querySelectorAll('dt')[dti];
      if (dt) dt.classList.add('highlighted');
    }

    for (const dl of wordLists) {
      dl.addEventListener('click', (e) => {
        const dt = e.target.closest('dt');
        if (!dt) return;
        dt.classList.toggle('highlighted');
        const path = dtPath(dt);
        if (dt.classList.contains('highlighted')) {
          highlighted.add(path);
        } else {
          highlighted.delete(path);
        }
        saveHighlights(highlighted);
      });
    }
  }

  if (passages.length > 0) {
    function spanPath(span) {
      const passage = span.closest(opts.passageSelector);
      const pi = [...passages].indexOf(passage);
      const si = [...passage.querySelectorAll('span[data-expect], span[data-expect-noncanonically]')].indexOf(span);
      return `span-${pi}-${si}`;
    }

    for (const path of highlighted) {
      if (!path.startsWith('span-')) continue;
      const [, pi, si] = path.split('-').map(Number);
      const span = passages[pi]?.querySelectorAll('span[data-expect], span[data-expect-noncanonically]')[si];
      if (span) span.classList.add('highlighted');
    }

    for (const passage of passages) {
      passage.addEventListener('click', (e) => {
        const span = e.target.closest('span[data-expect], span[data-expect-noncanonically]');
        if (!span) return;
        span.classList.toggle('highlighted');
        const path = spanPath(span);
        if (span.classList.contains('highlighted')) {
          highlighted.add(path);
        } else {
          highlighted.delete(path);
        }
        saveHighlights(highlighted);
      });
    }
  }

  function clearAllHighlights() {
    for (const el of document.querySelectorAll('.highlighted')) {
      el.classList.remove('highlighted');
    }
    highlighted.clear();
    saveHighlights(highlighted);
  }

  if (clearButton) {
    clearButton.addEventListener('click', clearAllHighlights);
  }

  return { clearAllHighlights, highlighted, loadHighlights, saveHighlights };
}

export function initEscapeToClear(clearFn) {
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') clearFn();
  });
}
