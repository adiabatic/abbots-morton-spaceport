import test from 'node:test';
import assert from 'node:assert/strict';
import { actionForKey, KEY_MAP } from '../static/keyboard.js';

const EXPECTED = [
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
  ['[', 'prev-batch'],
  [']', 'next-batch'],
  ['?', 'help'],
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

test('the retired j/k verdict keys are fully unbound', () => {
  assert.equal(actionForKey('j'), null);
  assert.equal(actionForKey('k'), null);
  assert.equal(KEY_MAP.has('j'), false);
  assert.equal(KEY_MAP.has('k'), false);
});
