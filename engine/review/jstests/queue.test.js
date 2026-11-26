import test from 'node:test';
import assert from 'node:assert/strict';
import {
  isBlank,
  buildClusters,
  ruledClassIds,
  partitionClusters,
  singletonChunks,
  queueResumeAction,
  queueEmptyNote,
  queueTotals,
  queueCounts,
  nextQueueDecision,
  decisionKey,
  readShownDecisions,
  writeShownDecisions,
  pruneShownDecisions,
  SINGLETON_DECISION,
  TOP_CLUSTER_COUNT,
  SINGLETON_CHUNK,
} from '../static/queue.js';

// The ids are synthetic. The sort key is `order`, the row's place in the manifest's triage index, which makeUnit takes from the id's digits.
const makeUnit = (id, over = {}) => ({
  id,
  order: Number.parseInt(id.slice(2), 10),
  batch: 0,
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

test('buildClusters groups blank human units by cluster, ordering members and exemplar', () => {
  const units = [
    makeUnit('u-0002'),
    makeUnit('u-0001'),
    makeUnit('u-0003'),
  ];
  const clusters = buildClusters(units, blank);
  assert.equal(clusters.length, 1);
  const [cluster] = clusters;
  assert.equal(cluster.id, 'c-aaaaaaaa');
  assert.equal(cluster.class, 'cls-a');
  assert.deepEqual(cluster.configs, ['default']);
  assert.notEqual(cluster.configs, units[0].configs);
  assert.equal(cluster.size, 3);
  assert.equal(cluster.exemplar.id, 'u-0001');
  assert.deepEqual(cluster.memberIds, ['u-0001', 'u-0002', 'u-0003']);
  assert.deepEqual(cluster.evidence, { counts: [], judgedTotal: 0, samples: [] });
});

test('buildClusters excludes verdicted units from membership but folds them into evidence with the exemplar staying the lowest blank member', () => {
  const units = [
    makeUnit('u-0001'),
    makeUnit('u-0002'),
    makeUnit('u-0003'),
    makeUnit('u-0008'),
    makeUnit('u-0009'),
    makeUnit('u-0010'),
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
    makeUnit('u-0001'),
    makeUnit('u-0005'),
  ];
  const records = { 'u-0005': { unit: 'u-0005', verdict: 'skip', note: '', at: '2026-02-01' } };
  const [cluster] = buildClusters(units, (id) => records[id]);
  assert.equal(cluster.size, 2);
  assert.deepEqual(cluster.memberIds, ['u-0001', 'u-0005']);
  assert.equal(cluster.evidence.judgedTotal, 0);
  assert.deepEqual(cluster.evidence.counts, []);
});

test('buildClusters ignores units with a null or undefined batch or a non-string cluster, and treats batch 0 as human', () => {
  const units = [
    makeUnit('u-0001', { batch: 0 }),
    makeUnit('u-0002', { batch: null }),
    makeUnit('u-0003', { cluster: null }),
    makeUnit('u-0004', { batch: undefined }),
  ];
  const clusters = buildClusters(units, blank);
  assert.equal(clusters.length, 1);
  assert.equal(clusters[0].size, 1);
  assert.deepEqual(clusters[0].memberIds, ['u-0001']);
});

test('buildClusters sorts clusters by descending size, then class, then id', () => {
  const units = [
    makeUnit('u-0001', { cluster: 'c-aaaaaaaa', class: 'cls-b' }),
    makeUnit('u-0002', { cluster: 'c-bbbbbbbb', class: 'cls-a' }),
    makeUnit('u-0003', { cluster: 'c-cccccccc', class: 'cls-a' }),
    makeUnit('u-0004', { cluster: 'c-cccccccc', class: 'cls-a' }),
    makeUnit('u-0005', { cluster: 'c-dddddddd', class: 'cls-a' }),
    makeUnit('u-0006', { cluster: 'c-dddddddd', class: 'cls-a' }),
  ];
  const clusters = buildClusters(units, blank);
  assert.deepEqual(
    clusters.map((cluster) => cluster.id),
    ['c-cccccccc', 'c-dddddddd', 'c-bbbbbbbb', 'c-aaaaaaaa'],
  );
});

test('buildClusters is order-independent and sorts members by their triage position so u-10000 follows u-9999', () => {
  const units = [
    makeUnit('u-0001'),
    makeUnit('u-9999'),
    makeUnit('u-10000'),
    makeUnit('u-0002', { cluster: 'c-bbbbbbbb' }),
  ];
  const forward = buildClusters(units, blank);
  const reversed = buildClusters([...units].reverse(), blank);
  const shuffled = buildClusters([units[2], units[0], units[3], units[1]], blank);
  assert.deepEqual(reversed, forward);
  assert.deepEqual(shuffled, forward);
  assert.deepEqual(forward[0].memberIds, ['u-0001', 'u-9999', 'u-10000']);
});

// Real ids carry no order. A string sort of these synthetic ids would put u-10 before u-9 and change the cluster's exemplar and evidence sample.
test('buildClusters orders members by triage position, not by id text', () => {
  const ids = ['u-9', 'u-10', 'u-100', 'u-2', 'u-1078641', 'u-21'];
  const units = ids.map((id) => makeUnit(id, {}));
  const [cluster] = buildClusters([...units].reverse(), blank);
  assert.deepEqual(cluster.memberIds, ['u-2', 'u-9', 'u-10', 'u-21', 'u-100', 'u-1078641']);
  assert.equal(cluster.exemplar.id, 'u-2');
});

test('ruledClassIds leaves out every class outside Open questions, by the section the build gave it, and tolerates undefined input', () => {
  const ids = ruledClassIds([
    { id: 'kerning', no_verdict: true, section: 'no-review-needed' },
    { id: 'glyphs', no_verdict: false, section: 'no-review-needed' },
    { id: 'joins', no_verdict: false, section: 'open-questions' },
  ]);
  assert.ok(ids instanceof Set);
  assert.deepEqual([...ids].sort(), ['glyphs', 'kerning']);
  assert.equal(ids.has('joins'), false);
  assert.equal(ruledClassIds(undefined).size, 0);
  assert.equal(ruledClassIds().size, 0);
});

test('partitionClusters splits unruled multi clusters at the top-cluster count and lists singletons', () => {
  assert.equal(TOP_CLUSTER_COUNT, 25);
  const clusters = [];
  for (let i = 0; i < 27; i += 1) clusters.push({ id: `c-multi-${i}`, class: 'cls-a', size: 2 });
  clusters.push({ id: 'c-single-1', class: 'cls-a', size: 1 });
  clusters.push({ id: 'c-single-2', class: 'cls-a', size: 1 });
  clusters.push({ id: 'c-ruled-1', class: 'cls-ruled', size: 3 });
  clusters.push({ id: 'c-ruled-2', class: 'cls-ruled', size: 4 });
  clusters.push({ id: 'c-ruled-3', class: 'cls-ruled', size: 1 });
  const result = partitionClusters(clusters, new Set(['cls-ruled']));
  assert.equal(result.top.length, 25);
  assert.equal(result.later.length, 2);
  assert.deepEqual(result.later.map((cluster) => cluster.id), ['c-multi-25', 'c-multi-26']);
  assert.equal(result.singletons.length, 2);
  for (const cluster of [...result.top, ...result.later, ...result.singletons]) {
    assert.notEqual(cluster.class, 'cls-ruled');
  }
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

test('queueTotals sums blank units and the multi versus singleton cluster split', () => {
  const totals = queueTotals([{ size: 3 }, { size: 1 }, { size: 2 }]);
  assert.deepEqual(totals, {
    blankUnits: 6,
    clusters: 3,
    multiClusters: 2,
    singletonClusters: 1,
  });
});

test('queueCounts tallies blank human units and the distinct clusters holding them, counting skips as blank but real verdicts as decided', () => {
  const units = [
    makeUnit('u-0001', { cluster: 'c-aaaaaaaa' }),
    makeUnit('u-0002', { cluster: 'c-aaaaaaaa' }),
    makeUnit('u-0003', { cluster: 'c-bbbbbbbb' }),
    makeUnit('u-0004', { cluster: 'c-cccccccc' }),
    makeUnit('u-0005', { cluster: 'c-cccccccc' }),
  ];
  const records = {
    'u-0002': { unit: 'u-0002', verdict: 'skip', note: '', at: '2026-01-01' },
    'u-0004': { unit: 'u-0004', verdict: 'approve', note: '', at: '2026-01-02' },
    'u-0005': { unit: 'u-0005', verdict: 'reject', note: '', at: '2026-01-03' },
  };
  assert.deepEqual(queueCounts(units, (id) => records[id]), { blankUnits: 3, clusters: 2 });
});

test('queueCounts drops ruled classes named in the third argument and defaults to no ruled classes when it is omitted', () => {
  const units = [
    makeUnit('u-0001', { cluster: 'c-aaaaaaaa', class: 'cls-a' }),
    makeUnit('u-0002', { cluster: 'c-bbbbbbbb', class: 'cls-ruled' }),
    makeUnit('u-0003', { cluster: 'c-cccccccc', class: 'cls-ruled' }),
  ];
  assert.deepEqual(queueCounts(units, blank, new Set(['cls-ruled'])), { blankUnits: 1, clusters: 1 });
  assert.deepEqual(queueCounts(units, blank), { blankUnits: 3, clusters: 3 });
});

test('queueCounts ignores units with a null or undefined batch or a non-string cluster', () => {
  const units = [
    makeUnit('u-0001', { cluster: 'c-aaaaaaaa', batch: 0 }),
    makeUnit('u-0002', { cluster: 'c-bbbbbbbb', batch: null }),
    makeUnit('u-0003', { cluster: 'c-cccccccc', batch: undefined }),
    makeUnit('u-0004', { cluster: null }),
  ];
  assert.deepEqual(queueCounts(units, blank), { blankUnits: 1, clusters: 1 });
});

test('nextQueueDecision offers the largest cluster first, with every member that has no record', () => {
  const units = [
    makeUnit('u-0001', { cluster: 'c-big' }),
    makeUnit('u-0002', { cluster: 'c-big' }),
    makeUnit('u-0003', { cluster: 'c-big' }),
    makeUnit('u-0010', { cluster: 'c-small' }),
    makeUnit('u-0011', { cluster: 'c-small' }),
  ];
  const decision = nextQueueDecision(units, blank, new Set());
  assert.equal(decision.kind, 'cluster');
  assert.equal(decision.cluster.id, 'c-big');
  assert.deepEqual(decision.unitIds, ['u-0001', 'u-0002', 'u-0003']);
});

test('nextQueueDecision defers a skipped member, keeping the cluster for the sake of its still-open members', () => {
  const units = [
    makeUnit('u-0001', { cluster: 'c-big' }),
    makeUnit('u-0002', { cluster: 'c-big' }),
    makeUnit('u-0003', { cluster: 'c-big' }),
    makeUnit('u-0004', { cluster: 'c-big' }),
  ];
  const records = { 'u-0001': { unit: 'u-0001', verdict: 'skip', note: '', at: '2026-01-01' } };
  const decision = nextQueueDecision(units, (id) => records[id], new Set());
  assert.equal(decision.kind, 'cluster');
  assert.equal(decision.cluster.id, 'c-big');
  assert.deepEqual(decision.unitIds, ['u-0002', 'u-0003', 'u-0004']);
});

test('nextQueueDecision passes over a wholly deferred cluster in favor of the next cluster with an open member', () => {
  const units = [
    makeUnit('u-0001', { cluster: 'c-big' }),
    makeUnit('u-0002', { cluster: 'c-big' }),
    makeUnit('u-0003', { cluster: 'c-big' }),
    makeUnit('u-0010', { cluster: 'c-small' }),
    makeUnit('u-0011', { cluster: 'c-small' }),
  ];
  const records = {
    'u-0001': { unit: 'u-0001', verdict: 'skip', note: '', at: '2026-01-01' },
    'u-0002': { unit: 'u-0002', verdict: 'skip', note: '', at: '2026-01-01' },
    'u-0003': { unit: 'u-0003', verdict: 'skip', note: '', at: '2026-01-02' },
  };
  const decision = nextQueueDecision(units, (id) => records[id], new Set());
  assert.equal(decision.kind, 'cluster');
  assert.equal(decision.cluster.id, 'c-small');
  assert.deepEqual(decision.unitIds, ['u-0010', 'u-0011']);
});

test('nextQueueDecision falls through to the first singleton chunk once every cluster is worked, excluding skip-recorded exemplars', () => {
  const units = [];
  for (let i = 1; i <= 42; i += 1) {
    const stamp = String(i).padStart(4, '0');
    units.push(makeUnit(`u-${stamp}`, { cluster: `c-${stamp}` }));
  }
  const records = { 'u-0007': { unit: 'u-0007', verdict: 'skip', note: '', at: '2026-01-01' } };
  const decision = nextQueueDecision(units, (id) => records[id], new Set());
  assert.equal(decision.kind, 'singletons');
  assert.equal(decision.remaining, 41);
  assert.equal(decision.unitIds.length, 40);
  assert.equal(decision.unitIds[0], 'u-0001');
  assert.equal(decision.unitIds.includes('u-0007'), false);
});

test('nextQueueDecision returns null when every blank unit is deferred', () => {
  const units = [
    makeUnit('u-0001', { cluster: 'c-big' }),
    makeUnit('u-0002', { cluster: 'c-big' }),
    makeUnit('u-0003', { cluster: 'c-single' }),
  ];
  const records = {
    'u-0001': { unit: 'u-0001', verdict: 'skip', note: '', at: '2026-01-01' },
    'u-0002': { unit: 'u-0002', verdict: 'skip', note: '', at: '2026-01-01' },
    'u-0003': { unit: 'u-0003', verdict: 'skip', note: '', at: '2026-01-02' },
  };
  assert.equal(nextQueueDecision(units, (id) => records[id], new Set()), null);
});

const threeClusters = () => [
  makeUnit('u-0001', { cluster: 'c-big' }),
  makeUnit('u-0002', { cluster: 'c-big' }),
  makeUnit('u-0003', { cluster: 'c-big' }),
  makeUnit('u-0010', { cluster: 'c-mid' }),
  makeUnit('u-0011', { cluster: 'c-mid' }),
  makeUnit('u-0020', { cluster: 'c-small' }),
  makeUnit('u-0021', { cluster: 'c-small' }),
];

test('nextQueueDecision steps past a cluster the session has already shown, however large, to one it never opened', () => {
  const units = threeClusters();
  const first = nextQueueDecision(units, blank, new Set(), new Set(['c-big']));
  assert.equal(first.cluster.id, 'c-mid');
  assert.equal(first.revisit, false);
  const second = nextQueueDecision(units, blank, new Set(), new Set(['c-big', 'c-mid']));
  assert.equal(second.cluster.id, 'c-small');
  assert.equal(second.revisit, false);
});

test('nextQueueDecision comes back round to the decision shown longest ago once every open one has been shown', () => {
  const units = threeClusters();
  const rotated = nextQueueDecision(units, blank, new Set(), new Set(['c-big', 'c-mid', 'c-small']));
  assert.equal(rotated.cluster.id, 'c-big');
  assert.equal(rotated.revisit, true);
  const next = nextQueueDecision(units, blank, new Set(), new Set(['c-mid', 'c-small', 'c-big']));
  assert.equal(next.cluster.id, 'c-mid');
  assert.equal(next.revisit, true);
});

test('nextQueueDecision reaches the singleton run as a fresh decision while every cluster is already shown', () => {
  const units = [
    ...threeClusters(),
    makeUnit('u-0030', { cluster: 'c-one' }),
    makeUnit('u-0031', { cluster: 'c-two' }),
  ];
  const shown = new Set(['c-big', 'c-mid', 'c-small']);
  const decision = nextQueueDecision(units, blank, new Set(), shown);
  assert.equal(decision.kind, 'singletons');
  assert.equal(decision.revisit, false);
  assert.deepEqual(decision.unitIds, ['u-0030', 'u-0031']);
  shown.add(SINGLETON_DECISION);
  assert.equal(nextQueueDecision(units, blank, new Set(), shown).cluster.id, 'c-big');
});

test('the last open singleton, once shown, waits behind clusters postponed before it', () => {
  const units = [...threeClusters(), makeUnit('u-0030', { cluster: 'c-one' })];
  const shown = new Set(['c-big', 'c-mid', 'c-small']);
  const decision = nextQueueDecision(units, blank, new Set(), shown);
  assert.equal(decision.kind, 'singletons');
  assert.deepEqual(decision.unitIds, ['u-0030']);
  const key = decisionKey([units.at(-1)], decision.key);
  shown.delete(key);
  shown.add(key);
  const next = nextQueueDecision(units, blank, new Set(), shown);
  assert.equal(next.kind, 'cluster');
  assert.equal(next.cluster.id, 'c-big');
  assert.equal(next.revisit, true);
});

test('decisionKey names the cluster behind the worklist of a cluster and the singleton run behind anything else', () => {
  assert.equal(decisionKey([makeUnit('u-0001', { cluster: 'c-big' }), makeUnit('u-0003', { cluster: 'c-big' })]), 'c-big');
  assert.equal(decisionKey([makeUnit('u-0030', { cluster: 'c-one' }), makeUnit('u-0031', { cluster: 'c-two' })]), SINGLETON_DECISION);
  assert.equal(decisionKey([]), SINGLETON_DECISION);
});

test('writeShownDecisions and readShownDecisions round-trip the rotation in the order it was stacked', () => {
  const shown = new Set(['c-bbe6acfa', 'c-cdde37a0']);
  const restored = readShownDecisions(writeShownDecisions(shown));
  assert.deepEqual([...restored], ['c-bbe6acfa', 'c-cdde37a0']);
});

test('readShownDecisions keeps the rotation across a rebuilt corpus, so a postponed decision stays postponed', () => {
  const stored = JSON.stringify({ stamp: '2026-09-01T00:00:00Z', keys: ['c-bbe6acfa', 'c-cdde37a0'] });
  const shown = readShownDecisions(stored);
  assert.deepEqual([...shown], ['c-bbe6acfa', 'c-cdde37a0']);
  const units = [
    makeUnit('u-0001', { cluster: 'c-bbe6acfa' }),
    makeUnit('u-0002', { cluster: 'c-bbe6acfa' }),
    makeUnit('u-0003', { cluster: 'c-cdde37a0' }),
    makeUnit('u-0004', { cluster: 'c-cdde37a0' }),
    makeUnit('u-0005', { cluster: 'c-fresh' }),
    makeUnit('u-0006', { cluster: 'c-fresh' }),
  ];
  assert.equal(nextQueueDecision(units, blank, new Set(), shown).key, 'c-fresh');
});

test('pruneShownDecisions drops the signatures no unit of the corpus carries and keeps the singleton run', () => {
  const shown = new Set(['c-gone', SINGLETON_DECISION, 'c-bbe6acfa']);
  pruneShownDecisions(shown, [makeUnit('u-0001', { cluster: 'c-bbe6acfa' })]);
  assert.deepEqual([...shown], [SINGLETON_DECISION, 'c-bbe6acfa']);
  pruneShownDecisions(shown, []);
  assert.deepEqual([...shown], [SINGLETON_DECISION, 'c-bbe6acfa']);
});

test('readShownDecisions starts a fresh rotation on unreadable storage or nothing stored at all', () => {
  assert.deepEqual([...readShownDecisions('not json')], []);
  assert.deepEqual([...readShownDecisions(null)], []);
  assert.deepEqual([...readShownDecisions(JSON.stringify({ keys: 'c-bbe6acfa' }))], []);
  assert.deepEqual([...readShownDecisions(JSON.stringify({ keys: ['c-bbe6acfa', 7, null] }))], ['c-bbe6acfa']);
});

test('queueResumeAction keeps the surviving ids of a worklist stamped for another corpus, stampless hashes included', () => {
  const records = { 'u-0001': { unit: 'u-0001', verdict: 'approve', note: '', at: '2026-01-01' } };
  const recordOf = (id) => records[id];
  const live = new Set(['u-0001', 'u-0003']);
  const exists = (id) => live.has(id);
  const unitIds = ['u-0001', 'u-0002', 'u-0003'];
  for (const stamp of ['2026-01-01T00:00:00Z', null]) {
    assert.deepEqual(queueResumeAction({ stamp, manifestStamp: '2026-01-02T00:00:00Z', unitIds, recordOf, exists }), {
      action: 'restamp',
      unitIds: ['u-0001', 'u-0003'],
    });
  }
});

test('queueResumeAction restacks a worklist from another corpus only when none of its units survive', () => {
  const recordOf = () => undefined;
  const exists = () => false;
  assert.deepEqual(
    queueResumeAction({ stamp: '2026-01-01T00:00:00Z', manifestStamp: '2026-01-02T00:00:00Z', unitIds: ['u-0001'], recordOf, exists }),
    { action: 'restack', unitIds: [] },
  );
});

test('queueResumeAction advances a worklist from another corpus whose surviving units all carry a real verdict', () => {
  const records = { 'u-0001': { unit: 'u-0001', verdict: 'reject', note: '', at: '2026-01-01' } };
  const recordOf = (id) => records[id];
  const exists = (id) => id === 'u-0001';
  assert.deepEqual(
    queueResumeAction({ stamp: '2026-01-01T00:00:00Z', manifestStamp: '2026-01-02T00:00:00Z', unitIds: ['u-0001', 'u-0002'], recordOf, exists }),
    { action: 'advance', unitIds: ['u-0001'] },
  );
});

test('queueResumeAction advances a current-corpus worklist whose every listed unit carries a real verdict', () => {
  const records = {
    'u-0001': { unit: 'u-0001', verdict: 'approve', note: '', at: '2026-01-01' },
    'u-0002': { unit: 'u-0002', verdict: 'reject', note: 'worse', at: '2026-01-01' },
  };
  const recordOf = (id) => records[id];
  const exists = () => true;
  const stamp = '2026-01-02T00:00:00Z';
  assert.equal(queueResumeAction({ stamp, manifestStamp: stamp, unitIds: ['u-0001', 'u-0002'], recordOf, exists }).action, 'advance');
  assert.equal(queueResumeAction({ stamp, manifestStamp: stamp, unitIds: [], recordOf, exists }).action, 'advance');
});

test('queueResumeAction renders a current-corpus worklist holding a blank or a skip — clicking a skipped-through cluster card must re-show the deferred members, never teleport', () => {
  const records = {
    'u-0001': { unit: 'u-0001', verdict: 'approve', note: '', at: '2026-01-01' },
    'u-0003': { unit: 'u-0003', verdict: 'skip', note: '', at: '2026-01-01' },
  };
  const recordOf = (id) => records[id];
  const exists = () => true;
  const stamp = '2026-01-02T00:00:00Z';
  assert.equal(queueResumeAction({ stamp, manifestStamp: stamp, unitIds: ['u-0001', 'u-0002'], recordOf, exists }).action, null);
  assert.equal(queueResumeAction({ stamp, manifestStamp: stamp, unitIds: ['u-0001', 'u-0003'], recordOf, exists }).action, null);
});

test('nextQueueDecision never offers a ruled class, even the largest one', () => {
  const units = [
    makeUnit('u-0001', { cluster: 'c-ruled', class: 'cls-ruled' }),
    makeUnit('u-0002', { cluster: 'c-ruled', class: 'cls-ruled' }),
    makeUnit('u-0003', { cluster: 'c-ruled', class: 'cls-ruled' }),
    makeUnit('u-0010', { cluster: 'c-open', class: 'cls-a' }),
    makeUnit('u-0011', { cluster: 'c-open', class: 'cls-a' }),
  ];
  const decision = nextQueueDecision(units, blank, new Set(['cls-ruled']));
  assert.equal(decision.kind, 'cluster');
  assert.equal(decision.cluster.id, 'c-open');
  assert.deepEqual(decision.unitIds, ['u-0010', 'u-0011']);
});

test('buildClusters puts units that show the same pixels on one card, whatever their kinds and configurations', () => {
  const rows = [
    makeUnit('u-1', { class: 'settlement', cluster: 'c-11111111', configs: ['default', 'ss03'] }),
    makeUnit('u-2', { class: 'joins', cluster: 'c-11111111', configs: ['default'] }),
    makeUnit('u-3', { class: 'joins', cluster: 'c-22222222', configs: ['ss04'] }),
  ];
  const clusters = buildClusters(rows, () => undefined);
  assert.deepEqual(
    clusters.map((cluster) => [cluster.id, cluster.memberIds]),
    [
      ['c-11111111', ['u-1', 'u-2']],
      ['c-22222222', ['u-3']],
    ],
    'a join unit and a settlement unit that show the same pixels share one card, whatever their configurations',
  );
  const decision = nextQueueDecision(rows, () => undefined, new Set());
  assert.equal(decision.key, 'c-11111111');
  assert.deepEqual(decision.unitIds, ['u-1', 'u-2'], 'every unit of a card is stacked');
});

test('queueEmptyNote says what an unclustered queue means', () => {
  const commit = 'bb61f6e003b0a83211f09264519387a52344f18d';
  const tableDiff = { mode: 'table-diff', baseline: { present: true, commit, taken_at: '2026-11-26T09:08:33Z', digest: 'd0' }, totals: { units: 0 } };
  assert.equal(queueEmptyNote(tableDiff), 'No unaccepted changes since bb61f6e003b0.');
  assert.equal(
    queueEmptyNote({ ...tableDiff, totals: { units: 1234 } }),
    'No unaccepted change since bb61f6e003b0 needs a verdict: the 1,234 changes are machine-approved or take no verdict.',
  );
  assert.match(queueEmptyNote({ ...tableDiff, totals: { units: 1 } }), /the one change is machine-approved/u);
  assert.match(
    queueEmptyNote({ mode: 'table-diff', baseline: { present: false, commit: null, taken_at: null, digest: null }, totals: { units: 0 } }),
    /No snapshot of an accepted build is on this machine/u,
  );
});
