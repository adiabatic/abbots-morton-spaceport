import test from 'node:test';
import assert from 'node:assert/strict';
import {
  OUTBOX_KEY,
  REPLAY_MAX_AGE_MS,
  readOutbox,
  writeOutbox,
  ackOutbox,
  dropOutbox,
  planOutboxReplay,
  currentEntries,
  markTabAlive,
  markTabClosed,
  liveTabs,
  TAB_FRESH_MS,
  scopeOutbox,
  scopedOutboxKey,
  saveConflicts,
  loadConflicts,
  CONFLICTS_KEY,
} from '../static/outbox.js';

function memoryStorage() {
  const items = new Map();
  return {
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => items.set(key, String(value)),
    removeItem: (key) => items.delete(key),
  };
}

function entry(record, { tab = 'A', seq = 1, stamp = 's1', base_at = null, written_at = '2026-09-30T00:00:00.000Z' } = {}) {
  return { record, base_at, stamp, tab, seq, written_at };
}

const approve = { unit: 'u-1', verdict: 'approve', note: '', at: 't1' };

test('an entry mirrors the latest write for its unit, an undo included', () => {
  const storage = memoryStorage();
  writeOutbox(storage, [['u-1', entry(approve, { seq: 1 })]]);
  writeOutbox(storage, [['u-1', entry(null, { seq: 2 })]]);
  assert.deepEqual(readOutbox(storage), { 'u-1': entry(null, { seq: 2 }) });
});

test('an acknowledgement removes only the entry with its tab and seq', () => {
  const storage = memoryStorage();
  writeOutbox(storage, [
    ['u-1', entry(approve, { tab: 'A', seq: 2 })],
    ['u-2', entry(approve, { tab: 'B', seq: 1 })],
  ]);
  ackOutbox(storage, [
    { unit: 'u-1', tab: 'A', seq: 1 },
    { unit: 'u-2', tab: 'A', seq: 1 },
  ]);
  assert.deepEqual(Object.keys(readOutbox(storage)).sort(), ['u-1', 'u-2']);
  ackOutbox(storage, [{ unit: 'u-1', tab: 'A', seq: 2 }]);
  assert.deepEqual(Object.keys(readOutbox(storage)), ['u-2']);
  ackOutbox(storage, [{ unit: 'u-2', tab: 'B', seq: 1 }]);
  assert.equal(storage.getItem(OUTBOX_KEY), null);
});

test('dropOutbox with a tab leaves the entries another tab wrote', () => {
  const storage = memoryStorage();
  writeOutbox(storage, [
    ['u-1', entry(approve, { tab: 'A' })],
    ['u-2', entry(approve, { tab: 'B' })],
  ]);
  dropOutbox(storage, ['u-1', 'u-2'], 'A');
  assert.deepEqual(Object.keys(readOutbox(storage)), ['u-2']);
});

test('set, undo inside the debounce, flush, then reload replays nothing', () => {
  const storage = memoryStorage();
  writeOutbox(storage, [['u-1', entry(approve, { seq: 1 })]]);
  writeOutbox(storage, [['u-1', entry(null, { seq: 2 })]]);
  ackOutbox(storage, [{ unit: 'u-1', tab: 'A', seq: 2 }]);
  const plan = planOutboxReplay(readOutbox(storage), new Map());
  assert.deepEqual(plan, { drop: [], replay: new Map(), stale: [] });
});

test('a reload drops what the server holds, replays recent entries by stamp, and leaves old ones for download', () => {
  const now = Date.parse('2026-09-30T12:00:00.000Z');
  const recent = '2026-09-30T11:00:00.000Z';
  const old = new Date(now - REPLAY_MAX_AGE_MS - 1000).toISOString();
  const outbox = {
    'u-saved': entry({ unit: 'u-saved', verdict: 'approve', note: '', at: 't1' }, { written_at: recent }),
    'u-cleared': entry(null, { written_at: recent }),
    'u-new': entry({ unit: 'u-new', verdict: 'reject', note: '', at: 't2' }, { written_at: recent, stamp: 's0' }),
    'u-clear': entry(null, { written_at: recent, base_at: 't0' }),
    'u-old': entry({ unit: 'u-old', verdict: 'reject', note: '', at: 't3' }, { written_at: old }),
  };
  const server = new Map([
    ['u-saved', { unit: 'u-saved', verdict: 'approve', note: '', at: 't1' }],
    ['u-clear', { unit: 'u-clear', verdict: 'approve', note: '', at: 't0' }],
  ]);
  const plan = planOutboxReplay(outbox, server, { now });
  assert.deepEqual(plan.drop.sort(), ['u-cleared', 'u-saved']);
  assert.deepEqual([...plan.replay.keys()].sort(), ['s0', 's1']);
  assert.deepEqual(plan.replay.get('s0').map((item) => item.unit), ['u-new']);
  assert.deepEqual(plan.replay.get('s1').map((item) => item.unit), ['u-clear']);
  assert.deepEqual(plan.stale.map((item) => item.unit), ['u-old']);
});

test('a storage that throws is read as an empty outbox and reports a failed write', () => {
  const broken = {
    getItem: () => '{not json',
    setItem: () => {
      throw new Error('quota');
    },
    removeItem: () => {},
  };
  assert.deepEqual(readOutbox(broken), {});
  assert.equal(writeOutbox(broken, [['u-1', entry(approve)]]), false);
});

test('a reload leaves the entries of a tab still open, and replays those of a tab that is gone', () => {
  const now = Date.parse('2026-09-30T12:00:00.000Z');
  const storage = memoryStorage();
  markTabAlive(storage, 'A', now - TAB_FRESH_MS - 1);
  markTabAlive(storage, 'B', now - 1000);
  markTabAlive(storage, 'C', now - 1000);
  markTabClosed(storage, 'C');
  const live = liveTabs(storage, now);
  assert.deepEqual([...live].sort(), ['B']);
  const recent = '2026-09-30T11:59:00.000Z';
  const outbox = {
    'u-1': entry({ unit: 'u-1', verdict: 'approve', note: '', at: 't1' }, { tab: 'A', written_at: recent }),
    'u-2': entry({ unit: 'u-2', verdict: 'approve', note: '', at: 't1' }, { tab: 'B', written_at: recent }),
    'u-3': entry(null, { tab: 'C', written_at: recent }),
  };
  const plan = planOutboxReplay(outbox, new Map([['u-3', approve]]), { now, live });
  assert.deepEqual(plan.drop, []);
  assert.deepEqual(plan.replay.get('s1').map((item) => item.unit).sort(), ['u-1', 'u-3']);
  markTabAlive(storage, 'D', now);
  assert.deepEqual([...liveTabs(storage, now)].sort(), ['B', 'D']);
});

test('a record the carry moved with its provenance counts as saved', () => {
  const outbox = { 'u-1': entry({ unit: 'u-1', verdict: 'approve', note: 'looks fine', at: 't1' }) };
  const carried = { unit: 'u-1', verdict: 'approve', note: '[carried u-0@old, verdicted 2026-09-30] looks fine', at: 't1' };
  const now = Date.parse('2026-09-30T00:00:01.000Z');
  assert.deepEqual(planOutboxReplay(outbox, new Map([['u-1', carried]]), { now }).drop, ['u-1']);
  const edited = { ...carried, note: '[carried u-0@old, verdicted 2026-09-30] other' };
  assert.deepEqual(planOutboxReplay(outbox, new Map([['u-1', edited]]), { now }).drop, []);
});

test('entries read at boot are sent only while their tab and seq are still in the outbox', () => {
  const storage = memoryStorage();
  writeOutbox(storage, [
    ['u-1', entry(approve, { seq: 1 })],
    ['u-2', entry(approve, { seq: 1 })],
  ]);
  const read = Object.entries(readOutbox(storage)).map(([unit, value]) => ({ unit, ...value }));
  writeOutbox(storage, [['u-1', entry(null, { seq: 2 })]]);
  ackOutbox(storage, [{ unit: 'u-1', tab: 'A', seq: 2 }]);
  assert.deepEqual(currentEntries(storage, read).map((item) => item.unit), ['u-2']);
});

test('each checkout keeps its own outbox, and an unscoped one moves into the first checkout that opens', () => {
  const storage = memoryStorage();
  writeOutbox(storage, [['u-legacy', entry(approve)]]);
  const main = scopeOutbox(storage, '/repo/main');
  assert.equal(storage.getItem(OUTBOX_KEY), null);
  writeOutbox(main, [['u-1', entry(approve)]]);
  const worktree = scopeOutbox(storage, '/repo/worktree');
  writeOutbox(worktree, [['u-2', entry(approve)]]);
  assert.deepEqual(Object.keys(readOutbox(main)).sort(), ['u-1', 'u-legacy']);
  assert.deepEqual(Object.keys(readOutbox(worktree)), ['u-2']);
  assert.deepEqual(Object.keys(JSON.parse(storage.getItem(scopedOutboxKey('/repo/main')))).sort(), ['u-1', 'u-legacy']);
  assert.equal(scopeOutbox(storage, null), storage);
});

test('conflicts saved to the tab survive a reload and an empty set removes the key', () => {
  const storage = memoryStorage();
  const conflicts = new Map([
    ['u-1', approve],
    ['u-2', null],
  ]);
  saveConflicts(storage, conflicts);
  assert.deepEqual(loadConflicts(storage), conflicts);
  saveConflicts(storage, new Map());
  assert.equal(storage.getItem(CONFLICTS_KEY), null);
  assert.deepEqual(loadConflicts(storage), new Map());
});
