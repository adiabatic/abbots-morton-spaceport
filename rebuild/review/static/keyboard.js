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
  } = {},
) {
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
