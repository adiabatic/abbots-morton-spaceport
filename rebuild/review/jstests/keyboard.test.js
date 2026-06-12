import test from 'node:test';
import assert from 'node:assert/strict';
import { actionForKey, KEY_MAP } from '../static/keyboard.js';

const EXPECTED = [
  ['a', 'skip'],
  ['s', 'reject'],
  ['d', 'either'],
  ['f', 'approve'],
  ['c', 'neither'],
  ['e', 'identical'],
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
