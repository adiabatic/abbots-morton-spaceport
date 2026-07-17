import test from 'node:test';
import assert from 'node:assert/strict';
import { parseHash, writeHash, STATE_KEYS, shedWorklist } from '../static/state.js';

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
  assert.deepEqual(reparsed, { ...state, family: null, machine: null, units: null, docket: null, view: null });
});

test('a units worklist rides the hash and round-trips', () => {
  assert.equal(parseHash('#units=u-1163,u-2224').units, 'u-1163,u-2224');
  assert.equal(parseHash('#batch=0').units, null);
  const serialized = writeHash({ batch: 0, units: 'u-1163,u-2224' });
  assert.equal(parseHash(`#${serialized}`).units, 'u-1163,u-2224');
});

test('the docket cursor rides the hash beside a units worklist and round-trips', () => {
  const parsed = parseHash('#units=u-0001,u-0002&docket=1');
  assert.equal(parsed.units, 'u-0001,u-0002');
  assert.equal(parsed.docket, '1');
  assert.equal(parseHash('#batch=0').docket, null);
  const serialized = writeHash({ units: 'u-0001,u-0002', docket: '1' });
  const reparsed = parseHash(`#${serialized}`);
  assert.equal(reparsed.units, 'u-0001,u-0002');
  assert.equal(reparsed.docket, '1');
});

test('shedWorklist drops the units worklist, the docket cursor, and the docket view when a navigation patch changes class, batch, group, config, family, or status', () => {
  for (const [key, value] of [['class', 'dangling-anchor-dropped'], ['batch', 2], ['group', 'qsTea:qsOy'], ['config', 'ss04'], ['family', 'qsMay'], ['status', 'verdicted']]) {
    assert.deepEqual(shedWorklist({ [key]: value }), { units: null, docket: null, view: null, [key]: value }, `changing ${key} must shed the worklist, docket cursor, and view`);
  }
  const cleared = shedWorklist({ family: null, config: null, status: null, group: null });
  assert.equal(cleared.units, null, 'clear-filters sheds the worklist alongside the other filters');
  assert.equal(cleared.docket, null, 'clear-filters sheds the docket cursor alongside the other filters');
  assert.equal(cleared.view, null, 'clear-filters leaves the docket view too');
});

test('shedWorklist keeps a cursor move or machine toggle inside the worklist, injecting no docket key', () => {
  assert.deepEqual(shedWorklist({ unit: 'u-0001' }), { unit: 'u-0001' });
  assert.deepEqual(shedWorklist({ unit: 'u-0003' }), { unit: 'u-0003' });
  assert.deepEqual(shedWorklist({ machine: '1' }), { machine: '1' });
  assert.deepEqual(shedWorklist({}), {});
});

test('the docket view rides the hash and round-trips', () => {
  assert.equal(parseHash('#view=docket').view, 'docket');
  assert.equal(parseHash('#batch=0').view, null);
  const serialized = writeHash({ view: 'docket' });
  assert.equal(serialized, 'view=docket');
  assert.equal(parseHash(`#${serialized}`).view, 'docket');
});

test('the machine toggle rides the hash and round-trips', () => {
  assert.equal(parseHash('#machine=1').machine, '1');
  assert.equal(parseHash('#batch=0').machine, null);
  const serialized = writeHash({ batch: 0, machine: '1' });
  assert.equal(serialized, 'batch=0&machine=1');
  assert.equal(parseHash(`#${serialized}`).machine, '1');
  assert.equal(writeHash({ batch: 0, machine: null }), 'batch=0');
});

test('round-trip preserves characters needing escaping', () => {
  const state = parseHash(`#${writeHash({ batch: 1, config: 'ss02+ss03+ss05', group: 'qsTea:qsOy' })}`);
  assert.equal(state.config, 'ss02+ss03+ss05');
  assert.equal(state.group, 'qsTea:qsOy');
});
