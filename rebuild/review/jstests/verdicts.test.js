import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createStore,
  recordVerdict,
  recordVerdictWithEchoes,
  updateNote,
  groupApprove,
  undo,
  assembleExport,
  markExported,
  importVerdicts,
  recentNotes,
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

test('recordVerdictWithEchoes fills unverdicted echo siblings as one undo step', () => {
  const store = createStore();
  recordVerdict(store, 'u-0003', 'reject', { at: 't0' });
  const applied = recordVerdictWithEchoes(store, 'u-0001', 'approve', ['u-0002', 'u-0003', 'u-0001'], {
    note: 'same change',
    at: 't1',
  });
  assert.deepEqual(applied, ['u-0001', 'u-0002']);
  assert.deepEqual(store.records.get('u-0002'), { unit: 'u-0002', verdict: 'approve', note: 'same change', at: 't1' });
  assert.equal(store.records.get('u-0003').verdict, 'reject');
  const result = undo(store);
  assert.deepEqual(result.units, ['u-0001', 'u-0002']);
  assert.equal(result.cursor, 'u-0001');
  assert.equal(store.records.has('u-0001'), false);
  assert.equal(store.records.has('u-0002'), false);
  assert.equal(store.records.get('u-0003').verdict, 'reject');
});

test('recordVerdictWithEchoes without echoes behaves exactly like recordVerdict', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'approve', { at: 't1' });
  const applied = recordVerdictWithEchoes(store, 'u-0001', 'reject', [], { at: 't2' });
  assert.deepEqual(applied, ['u-0001']);
  const result = undo(store);
  assert.deepEqual(result, { units: ['u-0001'], cursor: 'u-0001' });
  assert.equal(store.records.get('u-0001').verdict, 'approve');
  assert.throws(() => recordVerdictWithEchoes(store, 'u-0001', 'maybe', []));
});

test('an echo-filled record is individually overridable and clearable without touching its siblings', () => {
  const store = createStore();
  recordVerdictWithEchoes(store, 'u-0001', 'approve', ['u-0002'], { at: 't1' });
  recordVerdict(store, 'u-0002', 'reject', { at: 't2' });
  assert.equal(store.records.get('u-0001').verdict, 'approve');
  assert.equal(store.records.get('u-0002').verdict, 'reject');
  recordVerdict(store, 'u-0002', null);
  assert.equal(store.records.has('u-0002'), false);
  assert.equal(store.records.get('u-0001').verdict, 'approve');
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
  recordVerdict(store, 'u-0414', 'neither', { at: 't3', note: 'both joins look wrong' });
  recordVerdict(store, 'u-0415', 'identical', { at: 't4', note: 'cannot see the flagged difference' });
  const doc = assembleExport(store, 'gen-1');
  const fresh = createStore();
  const result = importVerdicts(fresh, doc, 'gen-1');
  assert.equal(result.ok, true);
  assert.equal(result.added, 4);
  assert.deepEqual(fresh.records.get('u-0413'), store.records.get('u-0413'));
  assert.deepEqual(fresh.records.get('u-0414'), store.records.get('u-0414'));
  assert.equal(fresh.records.get('u-0414').verdict, 'neither');
  assert.deepEqual(fresh.records.get('u-0415'), store.records.get('u-0415'));
  assert.equal(fresh.records.get('u-0415').verdict, 'identical');
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
  recordVerdict(store, 'u-0004', 'neither');
  recordVerdict(store, 'u-0005', 'identical');
  markExported(store);
  assert.equal(store.unexported.size, 0);
  assert.deepEqual(verdictCounts(store), { approve: 1, reject: 0, either: 1, identical: 1, neither: 1, skip: 1 });
});

test('neither is a first-class verdict kind: recordable, countable, undoable', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'neither', { note: 'needs follow-up authoring', at: 't1' });
  assert.deepEqual(store.records.get('u-0001'), {
    unit: 'u-0001',
    verdict: 'neither',
    note: 'needs follow-up authoring',
    at: 't1',
  });
  assert.equal(verdictCounts(store).neither, 1);
  undo(store);
  assert.equal(store.records.has('u-0001'), false);
});

test('identical is a first-class verdict kind: recordable, countable, undoable', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'identical', { note: 'the highlighted portion looks the same', at: 't1' });
  assert.deepEqual(store.records.get('u-0001'), {
    unit: 'u-0001',
    verdict: 'identical',
    note: 'the highlighted portion looks the same',
    at: 't1',
  });
  assert.equal(verdictCounts(store).identical, 1);
  undo(store);
  assert.equal(store.records.has('u-0001'), false);
});

test('recentNotes returns distinct notes newest-first by their at stamp', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', { note: 'oldest note', at: '2026-06-10T09:00:00Z' });
  recordVerdict(store, 'u-0002', 'reject', { note: 'middle note', at: '2026-06-10T10:00:00Z' });
  recordVerdict(store, 'u-0003', 'reject', { note: 'newest note', at: '2026-06-10T11:00:00Z' });
  assert.deepEqual(recentNotes(store), ['newest note', 'middle note', 'oldest note']);
});

test('recentNotes dedupes a note across units and ranks it by its newest stamp', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', { note: 'reached-for seam', at: '2026-06-10T09:00:00Z' });
  recordVerdict(store, 'u-0002', 'reject', { note: 'clean baseline join', at: '2026-06-10T10:00:00Z' });
  recordVerdict(store, 'u-0003', 'reject', { note: 'reached-for seam', at: '2026-06-10T11:00:00Z' });
  assert.deepEqual(recentNotes(store), ['reached-for seam', 'clean baseline join']);
});

test('recentNotes filters by verdict kind, and null gathers every kind', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', { note: 'reject note', at: '2026-06-10T09:00:00Z' });
  recordVerdict(store, 'u-0002', 'neither', { note: 'neither note', at: '2026-06-10T10:00:00Z' });
  recordVerdict(store, 'u-0003', 'reject', { note: 'later reject note', at: '2026-06-10T11:00:00Z' });
  assert.deepEqual(recentNotes(store, 'reject'), ['later reject note', 'reject note']);
  assert.deepEqual(recentNotes(store, 'neither'), ['neither note']);
  assert.deepEqual(recentNotes(store, null), ['later reject note', 'neither note', 'reject note']);
  assert.deepEqual(recentNotes(store), ['later reject note', 'neither note', 'reject note']);
});

test('recentNotes drops notes matching an exclude entry', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', { note: 'keep me', at: '2026-06-10T09:00:00Z' });
  recordVerdict(store, 'u-0002', 'reject', { note: 'drop me', at: '2026-06-10T10:00:00Z' });
  assert.deepEqual(recentNotes(store, 'reject', { exclude: ['drop me'] }), ['keep me']);
});

test('recentNotes caps the result at the requested limit', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', { note: 'note one', at: '2026-06-10T09:00:00Z' });
  recordVerdict(store, 'u-0002', 'reject', { note: 'note two', at: '2026-06-10T10:00:00Z' });
  recordVerdict(store, 'u-0003', 'reject', { note: 'note three', at: '2026-06-10T11:00:00Z' });
  assert.deepEqual(recentNotes(store, 'reject', { limit: 2 }), ['note three', 'note two']);
});

test('recentNotes never returns an empty note', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', { at: '2026-06-10T09:00:00Z' });
  recordVerdict(store, 'u-0002', 'reject', { note: 'real note', at: '2026-06-10T10:00:00Z' });
  assert.deepEqual(recentNotes(store), ['real note']);
});

test('recentNotes strips stacked carried-forward provenance markers from a note', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', {
    note: '[carried u-23958@review-pre-85f66b0, verdicted 2026-07-17] [carried u-23697@review-pre-0f5155b, verdicted 2026-07-17] the new way fails to join for no obvious reason',
    at: '2026-06-10T09:00:00Z',
  });
  assert.deepEqual(recentNotes(store), ['the new way fails to join for no obvious reason']);
});

test('recentNotes merges a carried copy with its hand-typed twin under the newest stamp', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', { note: 'seam overshoots', at: '2026-06-10T09:00:00Z' });
  recordVerdict(store, 'u-0002', 'reject', {
    note: '[carried u-0100@review-pre-abc1234, verdicted 2026-07-01] seam overshoots',
    at: '2026-06-10T10:00:00Z',
  });
  recordVerdict(store, 'u-0003', 'reject', { note: 'later note', at: '2026-06-10T11:00:00Z' });
  assert.deepEqual(recentNotes(store), ['later note', 'seam overshoots']);
});

test('recentNotes drops a carried note whose stripped text matches an exclude entry', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', {
    note: '[carried u-0100@review-pre-abc1234, verdicted 2026-07-01] the old way seems nicer to write out by hand',
    at: '2026-06-10T09:00:00Z',
  });
  const excluded = recentNotes(store, 'reject', { exclude: ['the old way seems nicer to write out by hand'] });
  assert.deepEqual(excluded, []);
});

test('recentNotes drops a note that is nothing but provenance markers', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', {
    note: '[carried u-0100@review-pre-abc1234, verdicted 2026-07-01]',
    at: '2026-06-10T09:00:00Z',
  });
  recordVerdict(store, 'u-0002', 'reject', { note: 'real note', at: '2026-06-10T10:00:00Z' });
  assert.deepEqual(recentNotes(store), ['real note']);
});

test('recentNotes leaves a carried mention after real text untouched', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', {
    note: 'compare with [carried u-0100@review-pre-abc1234, verdicted 2026-07-01] handling',
    at: '2026-06-10T09:00:00Z',
  });
  assert.deepEqual(recentNotes(store), [
    'compare with [carried u-0100@review-pre-abc1234, verdicted 2026-07-01] handling',
  ]);
});

test('recentNotes strips leading echo-fill and echo-harmonize markers ahead of the note', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', {
    note: '[echo-fill from u-0282] [carried u-0100@review-pre-abc1234, verdicted 2026-07-01] the seam overshoots',
    at: '2026-06-10T09:00:00Z',
  });
  recordVerdict(store, 'u-0002', 'reject', {
    note: '[echo-harmonize e-1007 — docket 2026-07-18T00:00:00Z] harmonize to approve',
    at: '2026-06-10T10:00:00Z',
  });
  assert.deepEqual(recentNotes(store), ['harmonize to approve', 'the seam overshoots']);
});

test('recentNotes drops a note that is nothing but echo-fill provenance', () => {
  const store = createStore();
  recordVerdict(store, 'u-0001', 'reject', {
    note: '[echo-fill from u-0282] [carried u-0100@review-pre-abc1234, verdicted 2026-07-01]',
    at: '2026-06-10T09:00:00Z',
  });
  recordVerdict(store, 'u-0002', 'reject', { note: 'real note', at: '2026-06-10T10:00:00Z' });
  assert.deepEqual(recentNotes(store), ['real note']);
});
