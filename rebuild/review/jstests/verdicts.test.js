import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createStore,
  recordVerdict,
  updateNote,
  groupApprove,
  undo,
  assembleExport,
  markExported,
  importVerdicts,
  verdictCounts,
  EXPORT_FORMAT,
} from '../static/verdicts.js';

test('recordVerdict stores the whole-unit record shape', () => {
  const store = createStore();
  recordVerdict(store, 'u-0413', 'reject', { note: 'seam looks reached-for', at: '2026-06-10T18:21:40Z' });
  assert.deepEqual(store.records.get('u-0413'), {
    unit: 'u-0413',
    verdict: 'reject',
    note: 'seam looks reached-for',
    at: '2026-06-10T18:21:40Z',
  });
  assert.ok(store.unexported.has('u-0413'));
});

test('recordVerdict rejects unknown verdict kinds', () => {
  const store = createStore();
  assert.throws(() => recordVerdict(store, 'u-0001', 'maybe'));
});

test('a null verdict retracts and is undoable', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'approve', { at: 't1' });
  recordVerdict(store, 'u-0001', null);
  assert.equal(store.records.size, 0);
  const result = undo(store);
  assert.deepEqual(result, { units: ['u-0001'], cursor: 'u-0001' });
  assert.equal(store.records.get('u-0001').verdict, 'approve');
});

test('retracting an absent record is a no-op that pushes no undo action', () => {
  const store = createStore();
  assert.equal(recordVerdict(store, 'u-0001', null), null);
  assert.equal(store.undoStack.length, 0);
  assert.equal(undo(store), null);
});

test('undo restores the previous record, not just deletes', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'approve', { at: 't1' });
  recordVerdict(store, 'u-0001', 'reject', { at: 't2' });
  const result = undo(store);
  assert.equal(result.cursor, 'u-0001');
  assert.equal(store.records.get('u-0001').verdict, 'approve');
  undo(store);
  assert.equal(store.records.has('u-0001'), false);
});

test('groupApprove only touches unverdicted units and undoes as one action', () => {
  const store = createStore();
  recordVerdict(store, 'u-0002', 'reject', { at: 't1' });
  const applied = groupApprove(store, ['u-0001', 'u-0002', 'u-0003'], { at: 't2' });
  assert.deepEqual(applied, ['u-0001', 'u-0003']);
  assert.equal(store.records.get('u-0002').verdict, 'reject');
  const result = undo(store);
  assert.deepEqual(result.units, ['u-0001', 'u-0003']);
  assert.equal(result.cursor, 'u-0001');
  assert.equal(store.records.size, 1);
});

test('groupApprove with nothing to do pushes no undo action', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'approve');
  assert.deepEqual(groupApprove(store, ['u-0001']), []);
  assert.equal(store.undoStack.length, 1);
});

test('updateNote edits the live record and marks it unexported', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'approve', { at: 't1' });
  markExported(store);
  assert.equal(updateNote(store, 'u-0001', 'hmm'), true);
  assert.equal(store.records.get('u-0001').note, 'hmm');
  assert.ok(store.unexported.has('u-0001'));
  assert.equal(updateNote(store, 'u-0404', 'orphan note'), false);
});

test('assembleExport emits the export document sorted by unit id', () => {
  const store = createStore();
  recordVerdict(store, 'u-0413', 'reject', { note: 'seam looks reached-for', at: '2026-06-10T18:21:40Z' });
  recordVerdict(store, 'u-0412', 'approve', { at: '2026-06-10T18:21:09Z' });
  const doc = assembleExport(store, '2026-06-10T17:02:11Z', '2026-06-10T18:40:02Z');
  assert.deepEqual(doc, {
    format: EXPORT_FORMAT,
    manifest_generated_at: '2026-06-10T17:02:11Z',
    exported_at: '2026-06-10T18:40:02Z',
    verdicts: [
      { unit: 'u-0412', verdict: 'approve', note: '', at: '2026-06-10T18:21:09Z' },
      { unit: 'u-0413', verdict: 'reject', note: 'seam looks reached-for', at: '2026-06-10T18:21:40Z' },
    ],
  });
});

test('export and import round-trip', () => {
  const store = createStore();
  recordVerdict(store, 'u-0412', 'approve', { at: 't1' });
  recordVerdict(store, 'u-0413', 'either', { at: 't2', note: 'fine both ways' });
  const doc = assembleExport(store, 'gen-1');
  const fresh = createStore();
  const result = importVerdicts(fresh, doc, 'gen-1');
  assert.equal(result.ok, true);
  assert.equal(result.added, 2);
  assert.deepEqual(fresh.records.get('u-0413'), store.records.get('u-0413'));
});

test('import refuses a manifest mismatch unless forced', () => {
  const store = createStore();
  const doc = { format: EXPORT_FORMAT, manifest_generated_at: 'gen-old', verdicts: [{ unit: 'u-0001', verdict: 'approve', note: '', at: 't1' }] };
  const refused = importVerdicts(store, doc, 'gen-new');
  assert.deepEqual(refused, { ok: false, mismatch: true });
  assert.equal(store.records.size, 0);
  const forced = importVerdicts(store, doc, 'gen-new', { force: true });
  assert.equal(forced.ok, true);
  assert.equal(forced.mismatch, true);
  assert.equal(store.records.size, 1);
});

test('import merges by unit id and the newer record wins', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'approve', { at: '2026-06-10T10:00:00Z' });
  recordVerdict(store, 'u-0002', 'approve', { at: '2026-06-10T10:00:00Z' });
  const doc = {
    format: EXPORT_FORMAT,
    manifest_generated_at: 'gen-1',
    verdicts: [
      { unit: 'u-0001', verdict: 'reject', note: '', at: '2026-06-10T09:00:00Z' },
      { unit: 'u-0002', verdict: 'reject', note: '', at: '2026-06-10T11:00:00Z' },
      { unit: 'u-0003', verdict: 'skip', note: '', at: '2026-06-10T11:00:00Z' },
      { unit: 42, verdict: 'approve' },
      { unit: 'u-0004', verdict: 'banana' },
    ],
  };
  const result = importVerdicts(store, doc, 'gen-1');
  assert.equal(result.keptNewer, 1);
  assert.equal(result.replaced, 1);
  assert.equal(result.added, 1);
  assert.equal(result.invalid, 2);
  assert.equal(store.records.get('u-0001').verdict, 'approve');
  assert.equal(store.records.get('u-0002').verdict, 'reject');
  assert.equal(store.records.get('u-0003').verdict, 'skip');
});

test('import tolerates legacy records carrying a configs field by ignoring it', () => {
  const store = createStore();
  const doc = {
    format: EXPORT_FORMAT,
    manifest_generated_at: 'gen-1',
    verdicts: [{ unit: 'u-0001', verdict: 'reject', configs: ['ss03'], note: '', at: 't1' }],
  };
  const result = importVerdicts(store, doc, 'gen-1');
  assert.equal(result.added, 1);
  assert.deepEqual(store.records.get('u-0001'), { unit: 'u-0001', verdict: 'reject', note: '', at: 't1' });
});

test('import rejects non-export documents', () => {
  const store = createStore();
  assert.equal(importVerdicts(store, { format: 'something-else' }, 'gen-1').ok, false);
  assert.equal(importVerdicts(store, null, 'gen-1').ok, false);
});

test('markExported clears the dirty set and counts stay accurate', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'approve');
  recordVerdict(store, 'u-0002', 'skip');
  recordVerdict(store, 'u-0003', 'either');
  markExported(store);
  assert.equal(store.unexported.size, 0);
  assert.deepEqual(verdictCounts(store), { approve: 1, reject: 0, either: 1, skip: 1 });
});
