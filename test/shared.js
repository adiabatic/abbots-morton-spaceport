export const LETTERS = [
  { code: 0xE650, name: 'Pea' },
  { code: 0xE651, name: 'Bay' },
  { code: 0xE652, name: 'Tea' },
  { code: 0xE653, name: 'Day' },
  { code: 0xE654, name: 'Key' },
  { code: 0xE655, name: 'Gay' },
  { code: 0xE656, name: 'Thaw' },
  { code: 0xE657, name: 'They' },
  { code: 0xE658, name: 'Fee' },
  { code: 0xE659, name: 'Vie' },
  { code: 0xE65A, name: 'See' },
  { code: 0xE65B, name: 'Zoo' },
  { code: 0xE65C, name: 'She' },
  { code: 0xE65D, name: "J'ai" },
  { code: 0xE65E, name: 'Cheer' },
  { code: 0xE65F, name: 'Jay' },
  { code: 0xE660, name: 'Ye' },
  { code: 0xE661, name: 'Way' },
  { code: 0xE662, name: 'He' },
  { code: 0xE663, name: 'Why' },
  { code: 0xE664, name: '-ing' },
  { code: 0xE665, name: 'May' },
  { code: 0xE666, name: 'No' },
  { code: 0xE667, name: 'Low' },
  { code: 0xE668, name: 'Roe' },
  { code: 0xE669, name: 'Loch' },
  { code: 0xE66A, name: 'Llan' },
  { code: 0xE66B, name: 'Excite' },
  { code: 0xE66C, name: 'Exam' },
  { code: 0xE670, name: 'It' },
  { code: 0xE671, name: 'Eat' },
  { code: 0xE672, name: 'Et' },
  { code: 0xE673, name: 'Eight' },
  { code: 0xE674, name: 'At' },
  { code: 0xE675, name: 'I' },
  { code: 0xE676, name: 'Ah' },
  { code: 0xE677, name: 'Awe' },
  { code: 0xE678, name: 'Ox' },
  { code: 0xE679, name: 'Oy' },
  { code: 0xE67A, name: 'Utter' },
  { code: 0xE67B, name: 'Out' },
  { code: 0xE67C, name: 'Owe' },
  { code: 0xE67D, name: 'Foot' },
  { code: 0xE67E, name: 'Ooze' },
];

export const NAME_TO_CP = Object.fromEntries(
  LETTERS.map(l => [l.name, l.code])
);

for (const l of LETTERS) {
  const normalized = l.name.replace(/[\u2018\u2019]/g, "'");
  if (normalized !== l.name) NAME_TO_CP[normalized] = l.code;
}

NAME_TO_CP["Jai"] = NAME_TO_CP["J'ai"];

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
  const weights = [200, 400, 600, 800];
  let weightIndex = 1;
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
    const fontWeight = isSans ? weights[weightIndex] : 400;
    document.documentElement.style.setProperty('--font-weight', String(fontWeight));
    if (titleEl) titleEl.textContent = fontTitle;
    if (fontToggle) fontToggle.textContent = isSans ? 'Sans' : 'Mono';
    if (weightToggle) weightToggle.textContent = `Weight ${weights[weightIndex]}`;

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
