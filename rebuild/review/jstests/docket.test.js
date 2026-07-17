import test from 'node:test';
import assert from 'node:assert/strict';
import {
  isBlank,
  buildClusters,
  ruledClassIds,
  partitionClusters,
  echoConflicts,
  singletonChunks,
  docketTotals,
  TRANCHE_SIZE,
  SINGLETON_CHUNK,
} from '../static/docket.js';

const makeUnit = (id, over = {}) => ({
  id,
  batch: 0,
  echo: `e-${id.slice(2)}`,
  cluster: 'c-aaaaaaaa',
  class: 'cls-a',
  configs: ['default'],
  notation: '·Pea·Tea',
  summary: null,
  ...over,
});

const blank = () => undefined;

test('isBlank treats an absent record or a skip verdict as blank and every real verdict as decided', () => {
  assert.equal(isBlank(undefined), true);
  assert.equal(isBlank(null), true);
  assert.equal(isBlank({ verdict: 'skip' }), true);
  for (const verdict of ['approve', 'reject', 'either', 'identical', 'neither']) {
    assert.equal(isBlank({ verdict }), false, verdict);
  }
});

test('buildClusters groups blank human units by cluster across different echo groups, ordering members, reps, and exemplar', () => {
  const units = [
    makeUnit('u-0002', { echo: 'e-0001' }),
    makeUnit('u-0001', { echo: 'e-0001' }),
    makeUnit('u-0003', { echo: 'e-0002' }),
  ];
  const clusters = buildClusters(units, blank);
  assert.equal(clusters.length, 1);
  const [cluster] = clusters;
  assert.equal(cluster.id, 'c-aaaaaaaa');
  assert.equal(cluster.class, 'cls-a');
  assert.deepEqual(cluster.configs, ['default']);
  assert.notEqual(cluster.configs, units[0].configs);
  assert.equal(cluster.size, 3);
  assert.deepEqual(cluster.echoGroups, [
    { echo: 'e-0001', unitIds: ['u-0001', 'u-0002'] },
    { echo: 'e-0002', unitIds: ['u-0003'] },
  ]);
  assert.deepEqual(cluster.reps, ['u-0001', 'u-0003']);
  assert.equal(cluster.exemplar.id, 'u-0001');
  assert.deepEqual(cluster.memberIds, ['u-0001', 'u-0002', 'u-0003']);
  assert.deepEqual(cluster.evidence, { counts: [], judgedTotal: 0, samples: [] });
});

test('buildClusters excludes verdicted units from membership but folds them into evidence with the exemplar staying the lowest blank member', () => {
  const units = [
    makeUnit('u-0001', { echo: 'e-0000' }),
    makeUnit('u-0002', { echo: 'e-0001' }),
    makeUnit('u-0003', { echo: 'e-0002' }),
    makeUnit('u-0008', { echo: 'e-0003' }),
    makeUnit('u-0009', { echo: 'e-0004' }),
    makeUnit('u-0010', { echo: 'e-0005' }),
  ];
  const records = {
    'u-0001': { unit: 'u-0001', verdict: 'approve', note: 'first note', at: '2026-01-01' },
    'u-0008': { unit: 'u-0008', verdict: 'approve', at: '2026-01-02' },
    'u-0009': { unit: 'u-0009', verdict: 'approve', note: 'third', at: '2026-01-03' },
    'u-0010': { unit: 'u-0010', verdict: 'reject', note: 'nope', at: '2026-01-04' },
  };
  const [cluster] = buildClusters(units, (id) => records[id]);
  assert.equal(cluster.size, 2);
  assert.deepEqual(cluster.memberIds, ['u-0002', 'u-0003']);
  assert.equal(cluster.exemplar.id, 'u-0002');
  assert.deepEqual(cluster.evidence.counts, [
    { verdict: 'approve', count: 3 },
    { verdict: 'reject', count: 1 },
  ]);
  assert.equal(cluster.evidence.judgedTotal, 4);
  assert.deepEqual(cluster.evidence.samples, [
    { unit: 'u-0001', verdict: 'approve', note: 'first note' },
    { unit: 'u-0008', verdict: 'approve', note: '' },
    { unit: 'u-0009', verdict: 'approve', note: 'third' },
  ]);
  assert.equal(cluster.evidence.samples.length, 3);
});

test('buildClusters keeps skip-verdicted units as members, never as evidence', () => {
  const units = [
    makeUnit('u-0001', { echo: 'e-0001' }),
    makeUnit('u-0005', { echo: 'e-0002' }),
  ];
  const records = { 'u-0005': { unit: 'u-0005', verdict: 'skip', note: '', at: '2026-02-01' } };
  const [cluster] = buildClusters(units, (id) => records[id]);
  assert.equal(cluster.size, 2);
  assert.deepEqual(cluster.memberIds, ['u-0001', 'u-0005']);
  assert.deepEqual(cluster.reps, ['u-0001', 'u-0005']);
  assert.equal(cluster.evidence.judgedTotal, 0);
  assert.deepEqual(cluster.evidence.counts, []);
});

test('buildClusters ignores units with a null or undefined batch or a non-string cluster, and treats batch 0 as human', () => {
  const units = [
    makeUnit('u-0001', { echo: 'e-0001', batch: 0 }),
    makeUnit('u-0002', { echo: 'e-0002', batch: null }),
    makeUnit('u-0003', { echo: 'e-0003', cluster: null }),
    makeUnit('u-0004', { echo: 'e-0004', batch: undefined }),
  ];
  const clusters = buildClusters(units, blank);
  assert.equal(clusters.length, 1);
  assert.equal(clusters[0].size, 1);
  assert.deepEqual(clusters[0].memberIds, ['u-0001']);
});

test('buildClusters falls back to the unit id for an echo-null member, forming its own echo group', () => {
  const units = [
    makeUnit('u-0001', { echo: null, cluster: 'c-aaaaaaaa' }),
    makeUnit('u-0002', { echo: 'e-0001' }),
  ];
  const [cluster] = buildClusters(units, blank);
  assert.deepEqual(cluster.echoGroups, [
    { echo: 'e-0001', unitIds: ['u-0002'] },
    { echo: 'u-0001', unitIds: ['u-0001'] },
  ]);
  assert.deepEqual(cluster.reps, ['u-0002', 'u-0001']);
});

test('buildClusters sorts clusters by descending size, then class, then id', () => {
  const units = [
    makeUnit('u-0001', { cluster: 'c-aaaaaaaa', class: 'cls-b', echo: 'e-0001' }),
    makeUnit('u-0002', { cluster: 'c-bbbbbbbb', class: 'cls-a', echo: 'e-0002' }),
    makeUnit('u-0003', { cluster: 'c-cccccccc', class: 'cls-a', echo: 'e-0003' }),
    makeUnit('u-0004', { cluster: 'c-cccccccc', class: 'cls-a', echo: 'e-0004' }),
    makeUnit('u-0005', { cluster: 'c-dddddddd', class: 'cls-a', echo: 'e-0005' }),
    makeUnit('u-0006', { cluster: 'c-dddddddd', class: 'cls-a', echo: 'e-0006' }),
  ];
  const clusters = buildClusters(units, blank);
  assert.deepEqual(
    clusters.map((cluster) => cluster.id),
    ['c-cccccccc', 'c-dddddddd', 'c-bbbbbbbb', 'c-aaaaaaaa'],
  );
});

test('buildClusters is order-independent and sorts members by numeric id so u-10000 follows u-9999', () => {
  const units = [
    makeUnit('u-0001', { echo: 'e-0001' }),
    makeUnit('u-9999', { echo: 'e-0001' }),
    makeUnit('u-10000', { echo: 'e-0001' }),
    makeUnit('u-0002', { cluster: 'c-bbbbbbbb', echo: 'e-0002' }),
  ];
  const forward = buildClusters(units, blank);
  const reversed = buildClusters([...units].reverse(), blank);
  const shuffled = buildClusters([units[2], units[0], units[3], units[1]], blank);
  assert.deepEqual(reversed, forward);
  assert.deepEqual(shuffled, forward);
  assert.deepEqual(forward[0].memberIds, ['u-0001', 'u-9999', 'u-10000']);
  assert.deepEqual(forward[0].reps, ['u-0001']);
});

test('ruledClassIds picks exactly the intended and reviewed statuses and tolerates undefined input', () => {
  const ids = ruledClassIds([
    { id: 'a', status: 'intended' },
    { id: 'b', status: 'reviewed-approved' },
    { id: 'c', status: 'reviewed-rejected' },
    { id: 'd', status: 'unverdicted' },
    { id: 'e', status: 'proposed' },
  ]);
  assert.ok(ids instanceof Set);
  assert.deepEqual([...ids].sort(), ['a', 'b', 'c']);
  assert.equal(ids.has('d'), false);
  assert.equal(ruledClassIds(undefined).size, 0);
  assert.equal(ruledClassIds().size, 0);
});

test('partitionClusters splits unruled multi clusters at the tranche cap, lists singletons, and sums ruled blanks', () => {
  assert.equal(TRANCHE_SIZE, 25);
  const clusters = [];
  for (let i = 0; i < 27; i += 1) clusters.push({ id: `c-multi-${i}`, class: 'cls-a', size: 2 });
  clusters.push({ id: 'c-single-1', class: 'cls-a', size: 1 });
  clusters.push({ id: 'c-single-2', class: 'cls-a', size: 1 });
  clusters.push({ id: 'c-ruled-1', class: 'cls-ruled', size: 3 });
  clusters.push({ id: 'c-ruled-2', class: 'cls-ruled', size: 4 });
  clusters.push({ id: 'c-ruled-3', class: 'cls-ruled', size: 1 });
  const result = partitionClusters(clusters, new Set(['cls-ruled']));
  assert.equal(result.tranche.length, 25);
  assert.equal(result.later.length, 2);
  assert.deepEqual(result.later.map((cluster) => cluster.id), ['c-multi-25', 'c-multi-26']);
  assert.equal(result.singletons.length, 2);
  assert.equal(result.ruledBlankUnits, 8);
  for (const cluster of [...result.tranche, ...result.later, ...result.singletons]) {
    assert.notEqual(cluster.class, 'cls-ruled');
  }
});

test('echoConflicts flags only echo groups whose judged verdicts disagree, sorted by echo id', () => {
  const unitsById = new Map([
    ['u-0001', { id: 'u-0001', class: 'cls-a' }],
    ['u-0002', { id: 'u-0002', class: 'cls-a' }],
    ['u-0003', { id: 'u-0003', class: 'cls-b' }],
    ['u-0004', { id: 'u-0004', class: 'cls-b' }],
    ['u-0009', { id: 'u-0009', class: 'cls-b' }],
    ['u-0005', { id: 'u-0005', class: 'cls-c' }],
    ['u-0006', { id: 'u-0006', class: 'cls-c' }],
    ['u-0007', { id: 'u-0007', class: 'cls-d' }],
    ['u-0008', { id: 'u-0008', class: 'cls-d' }],
  ]);
  const echoIndex = new Map([
    ['e-0002', ['u-0004', 'u-0003', 'u-0009']],
    ['e-0001', ['u-0001', 'u-0002']],
    ['e-0003', ['u-0005', 'u-0006']],
    ['e-0004', ['u-0007', 'u-0008']],
  ]);
  const records = {
    'u-0001': { unit: 'u-0001', verdict: 'approve', note: '', at: '2026-01-01' },
    'u-0002': { unit: 'u-0002', verdict: 'approve', note: '', at: '2026-01-02' },
    'u-0003': { unit: 'u-0003', verdict: 'approve', note: 'a', at: '2026-01-03' },
    'u-0004': { unit: 'u-0004', verdict: 'reject', note: 'b', at: '2026-01-04' },
    'u-0009': { unit: 'u-0009', verdict: 'skip', note: '', at: '2026-01-05' },
    'u-0006': { unit: 'u-0006', verdict: 'approve', note: '', at: '2026-01-06' },
    'u-0007': { unit: 'u-0007', verdict: 'either', note: 'e', at: '2026-01-07' },
    'u-0008': { unit: 'u-0008', verdict: 'neither', note: 'n', at: '2026-01-08' },
  };
  const conflicts = echoConflicts(echoIndex, unitsById, (id) => records[id]);
  assert.deepEqual(conflicts.map((conflict) => conflict.echo), ['e-0002', 'e-0004']);
  const [split] = conflicts;
  assert.equal(split.echo, 'e-0002');
  assert.equal(split.class, 'cls-b');
  assert.deepEqual(split.unitIds, ['u-0003', 'u-0004', 'u-0009']);
  assert.ok(split.records instanceof Map);
  assert.deepEqual([...split.records.keys()], ['u-0003', 'u-0004']);
  assert.equal(split.records.get('u-0003').verdict, 'approve');
  assert.equal(split.records.has('u-0009'), false);
  assert.equal(conflicts[1].class, 'cls-d');
  assert.deepEqual(conflicts[1].unitIds, ['u-0007', 'u-0008']);
});

test('singletonChunks slices the exemplars into 40-plus-remainder chunks with 1-based bounds', () => {
  assert.equal(SINGLETON_CHUNK, 40);
  const singletons = [];
  for (let i = 1; i <= 41; i += 1) {
    singletons.push({ exemplar: { id: `u-${String(i).padStart(4, '0')}` } });
  }
  const chunks = singletonChunks(singletons);
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].start, 1);
  assert.equal(chunks[0].end, 40);
  assert.equal(chunks[0].unitIds.length, 40);
  assert.equal(chunks[0].unitIds[0], 'u-0001');
  assert.equal(chunks[0].unitIds[39], 'u-0040');
  assert.equal(chunks[1].start, 41);
  assert.equal(chunks[1].end, 41);
  assert.deepEqual(chunks[1].unitIds, ['u-0041']);
});

test('docketTotals sums blank units, echo groups, and the multi versus singleton cluster split', () => {
  const totals = docketTotals([
    { size: 3, echoGroups: [{}, {}] },
    { size: 1, echoGroups: [{}] },
    { size: 2, echoGroups: [{}, {}, {}] },
  ]);
  assert.deepEqual(totals, {
    blankUnits: 6,
    echoGroups: 6,
    clusters: 3,
    multiClusters: 2,
    singletonClusters: 1,
  });
});
