export const KEY_MAP = new Map([
  ['a', 'skip'],
  ['s', 'reject'],
  ['d', 'either'],
  ['f', 'approve'],
  ['c', 'neither'],
  ['e', 'identical'],
  ['u', 'undo'],
  ['r', 'repeat'],
  ['n', 'note'],
  ['g', 'group-approve'],
  ['x', 'explain'],
  ['ArrowDown', 'next'],
  ['ArrowUp', 'prev'],
  ['k', 'next'],
  ['i', 'prev'],
  ['[', 'prev-class'],
  [']', 'next-class'],
  ['/', 'search'],
  ['?', 'help'],
  ['Backspace', 'clear-verdict'],
  ['Delete', 'clear-verdict'],
  ['Escape', 'escape'],
]);

export const REJECT_MENU_MAP = new Map([
  ['s', 'reject-no-comment'],
  ['a', 'reject-old-way'],
  ['f', 'reject-new-broken'],
  ['x', 'reject-comment'],
  ['z', 'reject-worse-extension'],
  ['Escape', 'reject-cancel'],
]);

export const NEITHER_MENU_MAP = new Map([
  ['c', 'neither-no-comment'],
  ['d', 'neither-ss10'],
  ['x', 'neither-comment'],
  ['Escape', 'neither-cancel'],
]);

// The kerning view's keys (`#view=kern`), by the pane that has focus. The grid's arrows move its cursor, Enter moves to the detail panel, and `-` and `=` (or `+`) tighten and loosen the value set on the focused pair of letters, for every facing pair of the two that has no record of its own. In the detail panel up and down choose a facing pair, left and right tighten and loosen the selected scope, `0` drops the scope's own record, `n` opens the note of the scope's record, `s` switches the scope between the selected facing pair and every unjoined pair of the two letters, and Escape returns to the grid. In the detail panel `f` marks the selected scope's carried-over facing pairs reviewed and moves to the next carried-over facing pair, in this pair of letters or the next one that has one, or takes the mark off, staying put, when they all have it. `u` undoes the last change in either pane, and `]` and `[` move to the next and the previous pair of letters that still has a carried-over facing pair not reviewed. Anywhere else on the page (`page`, a header button among them) the grid's keys still act, but Enter keeps its own meaning.
export const KERN_GRID_MAP = new Map([
  ['ArrowUp', 'kern-up'],
  ['ArrowDown', 'kern-down'],
  ['ArrowLeft', 'kern-left'],
  ['ArrowRight', 'kern-right'],
  ['Enter', 'kern-detail'],
  ['-', 'kern-letters-tighten'],
  ['=', 'kern-letters-loosen'],
  ['+', 'kern-letters-loosen'],
  ['u', 'kern-undo'],
  [']', 'kern-next-carried'],
  ['[', 'kern-prev-carried'],
  ['/', 'kern-jump'],
  ['?', 'help'],
]);

export const KERN_DETAIL_MAP = new Map([
  ['ArrowUp', 'kern-prev-pair'],
  ['ArrowDown', 'kern-next-pair'],
  ['ArrowLeft', 'kern-tighten'],
  ['ArrowRight', 'kern-loosen'],
  ['0', 'kern-drop'],
  ['n', 'kern-note'],
  ['s', 'kern-scope'],
  ['f', 'kern-review'],
  ['u', 'kern-undo'],
  [']', 'kern-next-carried'],
  ['[', 'kern-prev-carried'],
  ['/', 'kern-jump'],
  ['?', 'help'],
]);

function kernActionForKey(key, { inInput, overlayOpen, modified, kernPane }) {
  if (key === 'Escape') return inInput || kernPane !== 'detail' ? 'escape' : 'kern-grid';
  if (modified || inInput) return null;
  if (overlayOpen) return key === '?' ? 'help' : null;
  if (kernPane === 'detail') return KERN_DETAIL_MAP.get(key) ?? null;
  if (key === 'Enter' && kernPane !== 'grid') return null;
  return KERN_GRID_MAP.get(key) ?? null;
}

export function actionForKey(
  key,
  {
    inInput = false,
    overlayOpen = false,
    modified = false,
    rejectMenuOpen = false,
    neitherMenuOpen = false,
    noteInput = false,
    shift = false,
    view = null,
    kernPane = 'page',
  } = {},
) {
  if (view === 'kern') return kernActionForKey(key, { inInput, overlayOpen, modified, kernPane });
  if (rejectMenuOpen) {
    if (key === 'Escape') return 'reject-cancel';
    if (modified || inInput) return null;
    return REJECT_MENU_MAP.get(key) ?? (/^[0-9]$/.test(key) ? `reject-recent-${key}` : null);
  }
  if (neitherMenuOpen) {
    if (key === 'Escape') return 'neither-cancel';
    if (modified || inInput) return null;
    return NEITHER_MENU_MAP.get(key) ?? (/^[0-9]$/.test(key) ? `neither-recent-${key}` : null);
  }
  if (key === 'Escape') return 'escape';
  if (key === 'Enter' && noteInput && !modified) return shift ? 'note-stay' : 'note-advance';
  if (modified || inInput) return null;
  if (overlayOpen) return key === '?' ? 'help' : null;
  return KEY_MAP.get(key) ?? null;
}

export function isEditableTarget(target) {
  return Boolean(target && typeof target.closest === 'function' && target.closest('input, textarea, select'));
}
