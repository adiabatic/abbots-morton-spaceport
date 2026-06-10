export const KEY_MAP = new Map([
  ['j', 'approve'],
  ['f', 'reject'],
  ['d', 'either'],
  ['k', 'skip'],
  ['u', 'undo'],
  ['n', 'note'],
  ['g', 'group-approve'],
  ['x', 'explain'],
  ['ArrowDown', 'next'],
  ['ArrowUp', 'prev'],
  ['[', 'prev-batch'],
  [']', 'next-batch'],
  ['?', 'help'],
  ['Escape', 'escape'],
]);

export function actionForKey(key, { inInput = false, overlayOpen = false, modified = false } = {}) {
  if (key === 'Escape') return 'escape';
  if (modified || inInput) return null;
  if (overlayOpen) return key === '?' ? 'help' : null;
  return KEY_MAP.get(key) ?? null;
}

export function isEditableTarget(target) {
  return Boolean(target && typeof target.closest === 'function' && target.closest('input, textarea, select'));
}
