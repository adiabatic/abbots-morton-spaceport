export const KEY_MAP = new Map([
  ['a', 'skip'],
  ['s', 'reject'],
  ['d', 'either'],
  ['f', 'approve'],
  ['c', 'neither'],
  ['u', 'undo'],
  ['n', 'note'],
  ['g', 'group-approve'],
  ['x', 'explain'],
  ['ArrowDown', 'next'],
  ['ArrowUp', 'prev'],
  ['k', 'next'],
  ['i', 'prev'],
  ['[', 'prev-class'],
  [']', 'next-class'],
  ['?', 'help'],
  ['Escape', 'escape'],
]);

export const REJECT_MENU_MAP = new Map([
  ['s', 'reject-no-comment'],
  ['a', 'reject-old-way'],
  ['f', 'reject-new-broken'],
  ['x', 'reject-comment'],
  ['Escape', 'reject-cancel'],
]);

export function actionForKey(
  key,
  { inInput = false, overlayOpen = false, modified = false, rejectMenuOpen = false, noteInput = false } = {},
) {
  if (rejectMenuOpen) {
    if (key === 'Escape') return 'reject-cancel';
    if (modified || inInput) return null;
    return REJECT_MENU_MAP.get(key) ?? null;
  }
  if (key === 'Escape') return 'escape';
  if (key === 'Enter' && noteInput && !modified) return 'note-advance';
  if (modified || inInput) return null;
  if (overlayOpen) return key === '?' ? 'help' : null;
  return KEY_MAP.get(key) ?? null;
}

export function isEditableTarget(target) {
  return Boolean(target && typeof target.closest === 'function' && target.closest('input, textarea, select'));
}
