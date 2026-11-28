import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { deepLinkMove, emptyCorpus, indexCorpus, machineFolds, unitsInView, worklistUnits } from '../static/corpus.js';
import { needsNoVerdict, partitionUnits } from '../static/render.js';

// The fixture corpus read as the app reads it at boot: the manifest, then every class's shard whole.
const fixtureDir = new URL('../fixtures/', import.meta.url);
const manifest = JSON.parse(await readFile(new URL('manifest.json', fixtureDir), 'utf8'));
const shards = new Map();
for (const cls of manifest.classes) {
  const units = [];
  for (const path of cls.shards) units.push(...JSON.parse(await readFile(new URL(path, fixtureDir), 'utf8')));
  shards.set(cls.id, units);
}
const corpus = indexCorpus(manifest, shards);
const allIds = [...shards.values()].flat().map((unit) => unit.id);

const viewState = (over = {}) => ({
  class: null,
  batch: 0,
  unit: null,
  group: null,
  config: null,
  family: null,
  status: null,
  machine: null,
  units: null,
  order: null,
  queue: null,
  stamp: null,
  view: null,
  ...over,
});
const ids = (units) => units.map((unit) => unit.id);
const noRecords = () => undefined;

test('the fixture corpus indexes without a problem and holds every unit by id', () => {
  assert.deepEqual(corpus.problems, []);
  assert.deepEqual([...corpus.byId.keys()].sort(), [...allIds].sort());
  assert.equal(corpus.byId.size, manifest.totals.units);
  for (const cls of manifest.classes) {
    assert.deepEqual(ids(corpus.byClass.get(cls.id)), ids(shards.get(cls.id)), `${cls.id} keeps its shard order`);
  }
});

test('the human units are the manifest triage index, in its order, each with its position and batch', () => {
  assert.deepEqual(ids(corpus.human), manifest.human_unit_ids);
  for (const [position, unit] of corpus.human.entries()) {
    assert.equal(unit.order, position, unit.id);
    assert.equal(unit.batch, Math.floor(position / manifest.batch_size), unit.id);
    assert.equal(needsNoVerdict(unit), false, unit.id);
  }
  assert.ok(new Set(corpus.human.map((unit) => unit.batch)).size > 1, 'the fixtures must span more than one batch');
});

test("a unit's place in the queue is read from the manifest, whatever order the shards hold it in", () => {
  const reversed = { ...manifest, human_unit_ids: [...manifest.human_unit_ids].reverse(), batch_size: 2 };
  const reindexed = indexCorpus(reversed, shards);
  assert.deepEqual(ids(reindexed.human), reversed.human_unit_ids);
  assert.deepEqual(
    reindexed.human.map((unit) => [unit.order, unit.batch]),
    reversed.human_unit_ids.map((_id, position) => [position, Math.floor(position / 2)]),
  );
});

test('a unit that takes no verdict carries no place in the queue, and indexing leaves the shard records as parsed', () => {
  const machine = [...corpus.byId.values()].filter((unit) => needsNoVerdict(unit));
  assert.ok(machine.length > 0, 'the fixtures must carry units that take no verdict');
  for (const unit of machine) {
    assert.equal(unit.order, undefined, unit.id);
    assert.equal(unit.batch, undefined, unit.id);
  }
  for (const record of shards.values().next().value) assert.equal('order' in record, false, record.id);
});

test('the family filter lists the families the human units name, sorted', () => {
  assert.deepEqual(corpus.families, [...corpus.families].sort());
  assert.ok(corpus.families.includes('qsTea'));
  for (const unit of corpus.human) {
    for (const family of unit.group.split(':')) assert.ok(corpus.families.includes(family), family);
  }
});

test('shards that do not match the manifest are reported, as when a land swaps the corpus while the page fetches it', () => {
  const [first, ...rest] = manifest.classes;
  const shortened = new Map(shards);
  shortened.set(first.id, shards.get(first.id).slice(1));
  const short = indexCorpus(manifest, shortened);
  assert.ok(short.problems.some((problem) => problem.includes(`the ${first.id} shard holds`)), short.problems.join('; '));
  assert.ok(short.problems.some((problem) => problem.includes(shards.get(first.id)[0].id) || problem.includes('triage index')));

  const notAList = new Map(shards);
  notAList.set(rest[0].id, { id: 'u-AZd95bckcbg' });
  assert.ok(indexCorpus(manifest, notAList).problems.some((problem) => problem.includes(`the ${rest[0].id} shard is not a list`)));

  assert.ok(indexCorpus(manifest, new Map()).problems.length >= manifest.classes.length);
  assert.deepEqual(indexCorpus({ classes: [], human_unit_ids: [] }, new Map()).problems, []);
});

test('the empty corpus holds nothing, which is what the page shows until its corpus loads', () => {
  const empty = emptyCorpus();
  assert.equal(empty.byId.size, 0);
  assert.deepEqual(empty.human, []);
  assert.deepEqual(empty.families, []);
  assert.deepEqual(unitsInView(empty, manifest, { batch: 0, showMachine: true }), []);
});

test('a batch view holds the batch human units in triage order', () => {
  for (const batch of [0, 1]) {
    const view = unitsInView(corpus, manifest, { batch });
    assert.deepEqual(ids(view), ids(corpus.human.filter((unit) => unit.batch === batch)), `batch ${batch}`);
  }
  const glyphs = unitsInView(corpus, manifest, { batch: 1, classFilter: 'glyphs' });
  assert.deepEqual(ids(glyphs), ids(corpus.human.filter((unit) => unit.batch === 1 && unit.class === 'glyphs')));
  assert.deepEqual(unitsInView(corpus, manifest, { batch: 1, classFilter: 'joins' }), [], 'a class outside the batch adds nothing');
});

test('with the show-machine toggle on, a batch view adds the units that take no verdict of each class in view, batchless classes in batch 0', () => {
  const view = unitsInView(corpus, manifest, { batch: 0, showMachine: true });
  const machine = view.filter((unit) => needsNoVerdict(unit));
  assert.deepEqual(ids(view.slice(0, view.length - machine.length)), ids(corpus.human.filter((unit) => unit.batch === 0)));
  assert.deepEqual(
    machineFolds(machine).map((fold) => [fold.classId, fold.units.length, fold.check]),
    [
      ['settlement', 1, 'ink_identical'],
      ['kerning', 1, 'no_verdict'],
      ['provenance-only', 1, 'no_verdict'],
    ],
  );
  assert.deepEqual(
    unitsInView(corpus, manifest, { batch: 1, showMachine: true }).filter((unit) => needsNoVerdict(unit)),
    [],
    'batch 1 holds only glyphs units, and a batchless class folds into batch 0 alone',
  );
  assert.deepEqual(
    ids(unitsInView(corpus, manifest, { batch: 1, classFilter: 'kerning', showMachine: true })),
    ids(corpus.byClass.get('kerning')),
    'the class filter shows its class whatever the batch',
  );
});

test('the filters apply to the units that take no verdict a view holds, so a fold counts exactly what it shows', () => {
  const units = unitsInView(corpus, manifest, { batch: 0, showMachine: true });
  const state = viewState({ machine: '1', family: 'qsNo' });
  const { human, machine } = partitionUnits(units, state, noRecords);
  assert.deepEqual(ids(machine), ['u-4ZwsVeZJXT8'], 'only the kern unit names ·No');
  assert.ok(human.every((unit) => unit.group.split(':').includes('qsNo')));
  const { machine: hidden } = partitionUnits(units, viewState(), noRecords);
  assert.deepEqual(hidden, [], 'with the toggle off no unit that takes no verdict is drawn');
});

test('a fold is badged by the narrowest machine check that covers every unit in it', () => {
  const unit = (id, cls, flags = {}) => ({ id, class: cls, ink_identical: false, picture_identical: false, no_verdict: false, ...flags });
  const folds = machineFolds([
    unit('u-1', 'a', { ink_identical: true }),
    unit('u-2', 'b', { ink_identical: true }),
    unit('u-3', 'a', { ink_identical: true }),
    unit('u-4', 'b', { picture_identical: true }),
    unit('u-5', 'c', { no_verdict: true }),
    unit('u-6', 'c', { ink_identical: true }),
  ]);
  assert.deepEqual(
    folds.map((fold) => [fold.classId, ids(fold.units), fold.check]),
    [
      ['a', ['u-1', 'u-3'], 'ink_identical'],
      ['b', ['u-2', 'u-4'], 'picture_identical'],
      ['c', ['u-5', 'u-6'], 'no_verdict'],
    ],
  );
  assert.deepEqual(machineFolds([]), []);
});

test('a worklist holds each listed unit once, in the order listed, human or not, and leaves out ids the corpus lacks', () => {
  const machineId = 'u-i35WcnXw8nb';
  const key = ['u-LtadFU2dmvz', machineId, 'u-AZd95bckcbg', 'u-LtadFU2dmvz', 'u-zzzzzzzzzzz'].join(',');
  assert.deepEqual(ids(worklistUnits(corpus, key)), ['u-LtadFU2dmvz', machineId, 'u-AZd95bckcbg']);
  assert.equal(worklistUnits(corpus, key)[1], corpus.byId.get(machineId), 'a worklist reads the records in memory');
  assert.deepEqual(worklistUnits(corpus, ''), []);
});

test('a deep link to a unit that takes no verdict reveals it, whatever the batch', () => {
  for (const id of ['u-i35WcnXw8nb', 'u-4ZwsVeZJXT8', 'u-RbQ5ouLr5qu']) {
    assert.deepEqual(deepLinkMove(corpus.byId.get(id), viewState({ batch: 1 }), undefined), { reveal: true }, id);
  }
});

test('a deep link to a human unit in another batch moves the view to its batch when the filters admit it', () => {
  const later = corpus.byId.get('u-LtadFU2dmvz');
  assert.equal(later.batch, 1);
  assert.deepEqual(deepLinkMove(later, viewState({ batch: 0 }), undefined), { batch: 1 });
  assert.deepEqual(deepLinkMove(later, viewState({ batch: 0, class: 'glyphs' }), undefined), { batch: 1 });
  assert.equal(deepLinkMove(later, viewState({ batch: 0, class: 'joins' }), undefined), null, 'outside the class filter');
  assert.equal(deepLinkMove(later, viewState({ batch: 0, status: 'unverdicted' }), { verdict: 'approve' }), null, 'outside the status filter');
  assert.equal(deepLinkMove(later, viewState({ batch: 1 }), undefined), null, 'already in its batch');
  assert.equal(deepLinkMove(corpus.byId.get('u-no-such-unit'), viewState(), undefined), null, 'an id the corpus does not hold');
});
