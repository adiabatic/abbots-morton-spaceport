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

// The kerning view's keys (`#view=kern`), by the pane that has focus. The grid's arrows move its cursor and Enter moves to the detail panel; the detail panel's up and down arrows choose a facing pair and Escape returns to the grid. Anywhere else on the page (`page`, a header button among them) the arrows still move the grid's cursor, but Enter keeps its own meaning. `KERN_RESERVED_KEYS` stay unbound in the detail panel, for the editing keys of rebuild/KERN-PLAN.md's fourth step.
export const KERN_GRID_MAP = new Map([
  ['ArrowUp', 'kern-up'],
  ['ArrowDown', 'kern-down'],
  ['ArrowLeft', 'kern-left'],
  ['ArrowRight', 'kern-right'],
  ['Enter', 'kern-detail'],
  ['/', 'kern-jump'],
  ['?', 'help'],
]);

export const KERN_DETAIL_MAP = new Map([
  ['ArrowUp', 'kern-prev-pair'],
  ['ArrowDown', 'kern-next-pair'],
  ['/', 'kern-jump'],
  ['?', 'help'],
]);

export const KERN_RESERVED_KEYS = ['ArrowLeft', 'ArrowRight', '0'];

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
