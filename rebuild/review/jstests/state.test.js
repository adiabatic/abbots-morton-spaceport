import test from 'node:test';
import assert from 'node:assert/strict';
import { parseHash, writeHash, STATE_KEYS } from '../static/state.js';

test('parseHash returns nulls for an empty hash', () => {
  const state = parseHash('');
  for (const key of STATE_KEYS) assert.equal(state[key], null);
});

test('parseHash reads every key and coerces batch to an integer', () => {
  const state = parseHash('#class=dangling-anchor-dropped&batch=3&unit=u-0005&group=qsRoe:qsMay&config=ss05&family=qsMay&status=unverdicted');
  assert.equal(state.class, 'dangling-anchor-dropped');
  assert.equal(state.batch, 3);
  assert.equal(state.unit, 'u-0005');
  assert.equal(state.group, 'qsRoe:qsMay');
  assert.equal(state.config, 'ss05');
  assert.equal(state.family, 'qsMay');
  assert.equal(state.status, 'unverdicted');
});

test('parseHash rejects malformed and negative batch values', () => {
  assert.equal(parseHash('#batch=nope').batch, null);
  assert.equal(parseHash('#batch=-2').batch, null);
});

test('writeHash omits null and empty values', () => {
  const serialized = writeHash({ class: null, batch: 0, unit: '', group: null, config: null, family: null, status: null });
  assert.equal(serialized, 'batch=0');
});

test('hash state round-trips', () => {
  const state = {
    class: 'marker-staging-ligature-formation',
    batch: 6,
    unit: 'u-0412',
    group: 'qsTea:qsOy',
    config: 'ss02+ss03',
    family: null,
    status: 'reject',
  };
  const reparsed = parseHash(`#${writeHash(state)}`);
  assert.deepEqual(reparsed, { ...state, family: null });
});

test('round-trip preserves characters needing escaping', () => {
  const state = parseHash(`#${writeHash({ batch: 1, config: 'ss02+ss03+ss05', group: 'qsTea:qsOy' })}`);
  assert.equal(state.config, 'ss02+ss03+ss05');
  assert.equal(state.group, 'qsTea:qsOy');
});
