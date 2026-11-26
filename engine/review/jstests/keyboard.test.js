import test from 'node:test';
import assert from 'node:assert/strict';
import { actionForKey, KEY_MAP, KERN_GRID_MAP, KERN_DETAIL_MAP } from '../static/keyboard.js';

const EXPECTED = [
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
];

test('every planned binding dispatches its action', () => {
  for (const [key, action] of EXPECTED) {
    assert.equal(actionForKey(key), action, `key ${key}`);
  }
});

test('the key map carries no extra bindings beyond the plan', () => {
  assert.equal(KEY_MAP.size, EXPECTED.length + 1);
  assert.equal(KEY_MAP.get('Escape'), 'escape');
});

test('verdict keys are suppressed while typing in an input', () => {
  for (const [key] of EXPECTED) {
    assert.equal(actionForKey(key, { inInput: true }), null, `key ${key}`);
  }
});

test('Escape works even inside an input', () => {
  assert.equal(actionForKey('Escape', { inInput: true }), 'escape');
});

test('modified keys never dispatch', () => {
  assert.equal(actionForKey('a', { modified: true }), null);
  assert.equal(actionForKey(']', { modified: true }), null);
});

test('with an overlay open only help and escape act', () => {
  assert.equal(actionForKey('a', { overlayOpen: true }), null);
  assert.equal(actionForKey('?', { overlayOpen: true }), 'help');
  assert.equal(actionForKey('Escape', { overlayOpen: true }), 'escape');
});

test('unbound keys return null', () => {
  assert.equal(actionForKey('z'), null);
  assert.equal(actionForKey('Enter'), null);
});

test('slash focuses search, but types literally inside an input and yields to an open overlay', () => {
  assert.equal(actionForKey('/'), 'search');
  assert.equal(actionForKey('/', { inInput: true }), null);
  assert.equal(actionForKey('/', { overlayOpen: true }), null);
});

test('the retired j verdict key stays unbound; k is now a navigation alias', () => {
  assert.equal(actionForKey('j'), null);
  assert.equal(KEY_MAP.has('j'), false);
  assert.equal(actionForKey('k'), 'next');
  assert.equal(KEY_MAP.get('k'), 'next');
});

test('i and k mirror the arrow keys', () => {
  assert.equal(actionForKey('i'), actionForKey('ArrowUp'));
  assert.equal(actionForKey('k'), actionForKey('ArrowDown'));
  assert.equal(actionForKey('i', { inInput: true }), null);
  assert.equal(actionForKey('k', { inInput: true }), null);
  assert.equal(actionForKey('i', { overlayOpen: true }), null);
  assert.equal(actionForKey('k', { overlayOpen: true }), null);
});

test('while the reject menu is open, s/a/f/x/Escape map to the menu actions', () => {
  assert.equal(actionForKey('s', { rejectMenuOpen: true }), 'reject-no-comment');
  assert.equal(actionForKey('a', { rejectMenuOpen: true }), 'reject-old-way');
  assert.equal(actionForKey('f', { rejectMenuOpen: true }), 'reject-new-broken');
  assert.equal(actionForKey('x', { rejectMenuOpen: true }), 'reject-comment');
  assert.equal(actionForKey('z', { rejectMenuOpen: true }), 'reject-worse-extension');
  assert.equal(actionForKey('Escape', { rejectMenuOpen: true }), 'reject-cancel');
});

test('while the reject menu is open, every other key is suppressed', () => {
  for (const key of ['d', 'c', 'e', 'u', 'n', 'g', 'ArrowDown', 'ArrowUp', 'i', 'k', '[', ']', '?', 'q', 'Enter']) {
    assert.equal(actionForKey(key, { rejectMenuOpen: true }), null, `key ${key}`);
  }
});

test('while the neither menu is open, c/d/x/Escape map to the menu actions', () => {
  assert.equal(actionForKey('c', { neitherMenuOpen: true }), 'neither-no-comment');
  assert.equal(actionForKey('d', { neitherMenuOpen: true }), 'neither-ss10');
  assert.equal(actionForKey('x', { neitherMenuOpen: true }), 'neither-comment');
  assert.equal(actionForKey('Escape', { neitherMenuOpen: true }), 'neither-cancel');
});

test('while the neither menu is open, every other key is suppressed', () => {
  for (const key of ['a', 's', 'f', 'e', 'z', 'u', 'n', 'g', 'ArrowDown', 'ArrowUp', 'i', 'k', '[', ']', '?', 'q', 'Enter']) {
    assert.equal(actionForKey(key, { neitherMenuOpen: true }), null, `key ${key}`);
  }
});

test('neither-menu choices respect input focus and modifiers, but Escape still cancels', () => {
  assert.equal(actionForKey('c', { neitherMenuOpen: true, inInput: true }), null);
  assert.equal(actionForKey('d', { neitherMenuOpen: true, modified: true }), null);
  assert.equal(actionForKey('Escape', { neitherMenuOpen: true, inInput: true }), 'neither-cancel');
});

test('Enter in the note input saves and advances; Enter elsewhere stays inert', () => {
  assert.equal(actionForKey('Enter', { inInput: true, noteInput: true }), 'note-advance');
  assert.equal(actionForKey('Enter', { inInput: true, noteInput: true, shift: true }), 'note-stay');
  assert.equal(actionForKey('Enter', { inInput: true }), null);
  assert.equal(actionForKey('Enter', {}), null);
  assert.equal(actionForKey('Enter', { inInput: true, noteInput: true, modified: true }), null);
  assert.equal(actionForKey('Enter', { rejectMenuOpen: true, noteInput: true }), null);
});

test('reject-menu choices respect input focus and modifiers, but Escape still cancels', () => {
  assert.equal(actionForKey('s', { rejectMenuOpen: true, inInput: true }), null);
  assert.equal(actionForKey('a', { rejectMenuOpen: true, modified: true }), null);
  assert.equal(actionForKey('Escape', { rejectMenuOpen: true, inInput: true }), 'reject-cancel');
});

test('while the reject menu is open, an unmapped digit picks a recent reject note', () => {
  assert.equal(actionForKey('1', { rejectMenuOpen: true }), 'reject-recent-1');
  assert.equal(actionForKey('7', { rejectMenuOpen: true }), 'reject-recent-7');
  assert.equal(actionForKey('9', { rejectMenuOpen: true }), 'reject-recent-9');
});

test('while the neither menu is open, an unmapped digit picks a recent neither note', () => {
  assert.equal(actionForKey('1', { neitherMenuOpen: true }), 'neither-recent-1');
  assert.equal(actionForKey('4', { neitherMenuOpen: true }), 'neither-recent-4');
  assert.equal(actionForKey('9', { neitherMenuOpen: true }), 'neither-recent-9');
});

test('the zero key selects the tenth recent note in either menu', () => {
  assert.equal(actionForKey('0', { rejectMenuOpen: true }), 'reject-recent-0');
  assert.equal(actionForKey('0', { neitherMenuOpen: true }), 'neither-recent-0');
});

test('menu digit shortcuts are suppressed under modifiers or input focus', () => {
  assert.equal(actionForKey('1', { rejectMenuOpen: true, modified: true }), null);
  assert.equal(actionForKey('1', { rejectMenuOpen: true, inInput: true }), null);
  assert.equal(actionForKey('0', { neitherMenuOpen: true, modified: true }), null);
  assert.equal(actionForKey('0', { neitherMenuOpen: true, inInput: true }), null);
});

test('digits stay unbound when no menu is open', () => {
  for (const key of ['0', '1', '5', '9']) {
    assert.equal(actionForKey(key), null, `key ${key}`);
    assert.equal(actionForKey(key, { overlayOpen: true }), null, `key ${key}`);
  }
});

const KERN_GRID = [
  ['ArrowUp', 'kern-up'],
  ['ArrowDown', 'kern-down'],
  ['e', 'kern-up'],
  ['d', 'kern-down'],
  ['ArrowLeft', 'kern-left'],
  ['ArrowRight', 'kern-right'],
  ['s', 'kern-left'],
  ['f', 'kern-right'],
  ['Enter', 'kern-detail'],
  ['-', 'kern-letters-tighten'],
  ['=', 'kern-letters-loosen'],
  ['+', 'kern-letters-loosen'],
  ['u', 'kern-undo'],
  [']', 'kern-next-carried'],
  ['[', 'kern-prev-carried'],
  ['r', 'kern-next-split'],
  ['w', 'kern-prev-split'],
  ['/', 'kern-jump'],
  ['?', 'help'],
];

const KERN_DETAIL = [
  ['ArrowUp', 'kern-prev-pair'],
  ['ArrowDown', 'kern-next-pair'],
  ['e', 'kern-prev-pair'],
  ['d', 'kern-next-pair'],
  ['ArrowLeft', 'kern-tighten'],
  ['ArrowRight', 'kern-loosen'],
  ['s', 'kern-tighten'],
  ['f', 'kern-loosen'],
  ['0', 'kern-drop'],
  ['n', 'kern-note'],
  ['g', 'kern-review'],
  ['t', 'kern-take'],
  ['v', 'kern-give'],
  ['a', 'kern-apart'],
  ['c', 'kern-compare'],
  ['u', 'kern-undo'],
  [']', 'kern-next-carried'],
  ['[', 'kern-prev-carried'],
  ['r', 'kern-next-split'],
  ['w', 'kern-prev-split'],
  ['/', 'kern-jump'],
  ['?', 'help'],
];

test('the kerning view binds exactly its planned keys in each pane', () => {
  assert.deepEqual([...KERN_GRID_MAP], KERN_GRID);
  assert.deepEqual([...KERN_DETAIL_MAP], KERN_DETAIL);
  for (const [key, action] of KERN_GRID) assert.equal(actionForKey(key, { view: 'kern', kernPane: 'grid' }), action, `grid ${key}`);
  for (const [key, action] of KERN_GRID) {
    if (key !== 'Enter') assert.equal(actionForKey(key, { view: 'kern', kernPane: 'page' }), action, `page ${key}`);
  }
  for (const [key, action] of KERN_DETAIL) assert.equal(actionForKey(key, { view: 'kern', kernPane: 'detail' }), action, `detail ${key}`);
});

test('the grid’s - and = act on the letters and leave the detail panel’s arrows to its selected scope', () => {
  assert.equal(actionForKey('-', { view: 'kern', kernPane: 'detail' }), null);
  assert.equal(actionForKey('0', { view: 'kern', kernPane: 'grid' }), null);
  assert.equal(actionForKey('n', { view: 'kern', kernPane: 'grid' }), null);
  assert.equal(actionForKey('g', { view: 'kern', kernPane: 'grid' }), null);
  for (const key of ['t', 'v', 'a', 'c']) assert.equal(actionForKey(key, { view: 'kern', kernPane: 'grid' }), null, `grid ${key}`);
  assert.equal(actionForKey('r', { view: 'kern', kernPane: 'grid' }), 'kern-next-split');
  assert.equal(actionForKey('r'), 'repeat');
  assert.equal(actionForKey(']', { view: 'kern', kernPane: 'grid' }), 'kern-next-carried');
  assert.equal(actionForKey(']'), 'next-class');
  assert.equal(actionForKey('ArrowLeft', { view: 'kern', kernPane: 'detail' }), 'kern-tighten');
  assert.equal(actionForKey('ArrowLeft', { view: 'kern', kernPane: 'grid' }), 'kern-left');
  assert.equal(actionForKey('-', { view: 'kern', kernPane: 'grid', inInput: true }), null);
});

test('no review key acts in the kerning view', () => {
  for (const [key] of EXPECTED) {
    const action = actionForKey(key, { view: 'kern', kernPane: 'grid' });
    assert.ok(action === null || action.startsWith('kern-') || action === 'help', `key ${key} gave ${action}`);
  }
  assert.equal(actionForKey('a', { view: 'kern' }), null);
  assert.equal(actionForKey('Backspace', { view: 'kern', kernPane: 'detail' }), null);
});

test('in the kerning view Escape leaves the detail panel for the grid, and blurs a field or does nothing more elsewhere', () => {
  assert.equal(actionForKey('Escape', { view: 'kern', kernPane: 'detail' }), 'kern-grid');
  assert.equal(actionForKey('Escape', { view: 'kern', kernPane: 'grid' }), 'escape');
  assert.equal(actionForKey('Escape', { view: 'kern', kernPane: 'detail', inInput: true }), 'escape');
});

test('the kerning view’s keys yield to a field, a modifier and an open overlay, but for help', () => {
  assert.equal(actionForKey('ArrowUp', { view: 'kern', inInput: true }), null);
  assert.equal(actionForKey('/', { view: 'kern', inInput: true }), null);
  assert.equal(actionForKey('ArrowDown', { view: 'kern', modified: true }), null);
  assert.equal(actionForKey('ArrowDown', { view: 'kern', overlayOpen: true }), null);
  assert.equal(actionForKey('?', { view: 'kern', overlayOpen: true }), 'help');
});

test('Enter leaves a focused button on the page to the button: only the grid itself turns it into a move to the detail panel', () => {
  assert.equal(actionForKey('Enter', { view: 'kern', kernPane: 'page' }), null);
  assert.equal(actionForKey('Enter', { view: 'kern' }), null);
  assert.equal(actionForKey('Enter', { view: 'kern', kernPane: 'grid' }), 'kern-detail');
  assert.equal(actionForKey('Enter', { view: 'kern', kernPane: 'detail' }), null);
});
