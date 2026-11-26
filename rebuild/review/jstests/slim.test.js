import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {
  APP_INDEX_FORMAT,
  LOCATOR_FORMAT,
  MACHINE_FOLD_WINDOW,
  RECORD_CACHE_CAP,
  SPAN_GAP_BYTES,
  candidateBlocks,
  carriesSamples,
  checkIndexHeader,
  drawnWithKerning,
  coalesceSpans,
  createLineSplitter,
  createRecordCache,
  finishLines,
  hasExplainSource,
  indexLocatorBlocks,
  looksGzipped,
  machineFoldPlan,
  rangeHeader,
  recordText,
  shardPartPath,
  sliceRecordBytes,
  splitLines,
  isUnitId,
} from '../static/slim.js';
import { needsNoVerdict } from '../static/render.js';

const fixtureDir = new URL('../fixtures/', import.meta.url);
const manifest = JSON.parse(await readFile(new URL('manifest.json', fixtureDir), 'utf8'));
const allUnits = [];
for (const cls of manifest.classes) {
  for (const part of cls.shards) allUnits.push(...JSON.parse(await readFile(new URL(part, fixtureDir), 'utf8')));
}
const human = allUnits.find((unit) => !needsNoVerdict(unit) && unit.text_entities !== null);
const machineUnit = allUnits.find((unit) => needsNoVerdict(unit) && unit.text_entities !== null);

const drain = (state, chunks) => {
  const lines = [];
  for (const chunk of chunks) lines.push(...splitLines(state, chunk));
  lines.push(...finishLines(state));
  return lines;
};

const viewState = (over = {}) => ({
  class: null,
  batch: 0,
  unit: null,
  group: null,
  config: null,
  family: null,
  status: null,
  machine: '1',
  units: null,
  order: null,
  queue: null,
  stamp: null,
  view: null,
  ...over,
});

test('splitLines yields whole lines and carries a record split across a chunk boundary', () => {
  const state = createLineSplitter();
  assert.deepEqual(splitLines(state, '{"a":1}\n{"b":'), ['{"a":1}']);
  assert.equal(state.tail, '{"b":');
  assert.deepEqual(splitLines(state, '2}\n'), ['{"b":2}']);
  assert.equal(state.tail, '');
  assert.deepEqual(finishLines(state), []);
});

test('splitLines is indifferent to where the chunks fall', () => {
  const body = '{"format":"x"}\n{"id":"u-0001"}\n{"id":"u-0002"}\n';
  const whole = drain(createLineSplitter(), [body]);
  assert.deepEqual(whole, ['{"format":"x"}', '{"id":"u-0001"}', '{"id":"u-0002"}']);
  for (let cut = 1; cut < body.length; cut += 1) {
    assert.deepEqual(drain(createLineSplitter(), [body.slice(0, cut), body.slice(cut)]), whole, `cut at ${cut}`);
  }
});

test('splitLines keeps a chunk that ends exactly on a newline from stranding an empty tail', () => {
  const state = createLineSplitter();
  assert.deepEqual(splitLines(state, '{"a":1}\n'), ['{"a":1}']);
  assert.equal(state.tail, '');
  assert.deepEqual(splitLines(state, '{"b":2}\n'), ['{"b":2}']);
  assert.deepEqual(finishLines(state), []);
});

test('finishLines releases a last line written without a trailing newline, and only once', () => {
  const state = createLineSplitter();
  assert.deepEqual(splitLines(state, '{"a":1}\n{"b":2}'), ['{"a":1}']);
  assert.deepEqual(finishLines(state), ['{"b":2}']);
  assert.deepEqual(finishLines(state), []);
});

test('checkIndexHeader accepts a header stamped for the corpus beside it', () => {
  const header = { format: APP_INDEX_FORMAT, generated_at: manifest.generated_at, units: 5 };
  assert.deepEqual(checkIndexHeader(header, manifest, APP_INDEX_FORMAT), { ok: true, reason: null });
});

test('checkIndexHeader refuses another format, so the locator can never be read as the index', () => {
  const header = { format: LOCATOR_FORMAT, generated_at: manifest.generated_at };
  const check = checkIndexHeader(header, manifest, APP_INDEX_FORMAT);
  assert.equal(check.ok, false);
  assert.match(check.reason, /ams-review-app-locator\/2/);
});

test('checkIndexHeader refuses an index stamped for another build, whose ids name other units', () => {
  const header = { format: APP_INDEX_FORMAT, generated_at: '2026-06-11T00:00:00Z' };
  const check = checkIndexHeader(header, manifest, APP_INDEX_FORMAT);
  assert.equal(check.ok, false);
  assert.match(check.reason, /2026-06-11T00:00:00Z/);
  assert.match(check.reason, new RegExp(manifest.generated_at));
});

test('checkIndexHeader refuses an unreadable or stampless header rather than trusting it', () => {
  assert.equal(checkIndexHeader(null, manifest, APP_INDEX_FORMAT).ok, false);
  assert.equal(checkIndexHeader('{}', manifest, APP_INDEX_FORMAT).ok, false);
  assert.equal(checkIndexHeader({ format: APP_INDEX_FORMAT }, manifest, APP_INDEX_FORMAT).ok, false);
  assert.equal(checkIndexHeader({ generated_at: manifest.generated_at }, manifest, APP_INDEX_FORMAT).ok, false);
});

test('hasExplainSource separates an app-index row from a whole shard record', () => {
  assert.equal(hasExplainSource(human), true);
  const row = { ...human };
  delete row.explain;
  delete row.provenance;
  assert.equal(hasExplainSource(row), false);
  assert.equal(hasExplainSource({ explain: null }), true, 'a record whose explain is empty is still a whole record');
  assert.equal(hasExplainSource(machineUnit), true, 'a machine-approved record fills its own panel');
  assert.equal(hasExplainSource(null), false);
});

test('carriesSamples tells a shard record, a machine-approved one included, from an app-index row', () => {
  assert.equal(carriesSamples(human), true);
  assert.equal(carriesSamples(machineUnit), true, 'a machine-approved record carries its text and cells');
  const row = { ...human };
  delete row.text_entities;
  delete row.highlight;
  delete row.after;
  assert.equal(carriesSamples(row), false);
  assert.equal(carriesSamples({ text_entities: null }), false);
  assert.equal(carriesSamples(null), false);
});

test('drawnWithKerning is true only on a unit that says it shows a kern value', () => {
  assert.equal(drawnWithKerning({ ...human, kerning: true }), true);
  assert.equal(drawnWithKerning({ ...human, kerning: false }), false);
  assert.equal(drawnWithKerning({ ...human, kerning: undefined }), false, 'a record without the field is drawn with kerning off');
  assert.equal(drawnWithKerning(null), false);
});

test('isUnitId accepts exactly the content-addressed shape: u- and eleven base58 symbols', () => {
  assert.equal(isUnitId('u-3mJ7kPq2Xw9'), true);
  assert.equal(isUnitId('u-11111111111'), true, 'the zero-padded value');
  for (const bad of ['u-0000', 'u-0O0O0O0O0O0', 'u-3mJ7kPq2Xw', 'u-3mJ7kPq2Xw9Z', 'e-3mJ7kPq2Xw9', 'u-', '3mJ7kPq2Xw9', 'c-aaaaaaaa', null, undefined, 7]) {
    assert.equal(isUnitId(bad), false, String(bad));
  }
});

// A table in the shape the build writes: in shard order, with each class's blocks contiguous and ascending by id, and with id ranges that overlap across classes. Ids compare as strings.
const locatorTable = [
  { class: 'alpha', byte_start: 0, byte_length: 100, first: 'u-1111111111A', last: 'u-1111111111K', units: 4 },
  { class: 'alpha', byte_start: 100, byte_length: 100, first: 'u-1111111111P', last: 'u-1111111111Z', units: 4 },
  { class: 'beta', byte_start: 200, byte_length: 100, first: 'u-1111111111F', last: 'u-1111111111a', units: 4 },
  { class: 'beta', byte_start: 300, byte_length: 50, first: 'u-1111111111b', last: 'u-1111111111b', units: 1 },
];

test('indexLocatorBlocks groups the table by class in file order', () => {
  const byClass = indexLocatorBlocks(locatorTable);
  assert.deepEqual([...byClass.keys()], ['alpha', 'beta']);
  assert.deepEqual(
    byClass.get('alpha').map((block) => [block.first, block.last, block.byte_start]),
    [
      ['u-1111111111A', 'u-1111111111K', 0],
      ['u-1111111111P', 'u-1111111111Z', 100],
    ],
  );
  assert.equal(byClass.get('beta').length, 2);
});

test('candidateBlocks names at most one block per class, by binary search over that class alone', () => {
  const byClass = indexLocatorBlocks(locatorTable);
  const found = (id) => candidateBlocks(byClass, id).map((block) => `${block.class}@${block.byte_start}`);
  assert.deepEqual(found('u-1111111111H'), ['alpha@0', 'beta@200'], 'an id inside two overlapping class ranges is a candidate in both');
  assert.deepEqual(found('u-1111111111P'), ['alpha@100', 'beta@200']);
  assert.deepEqual(found('u-1111111111b'), ['beta@300']);
  assert.deepEqual(found('u-1111111111L'), ['beta@200'], "an id between two of a class's blocks is in neither");
  assert.deepEqual(found('u-1111111111N'), ['beta@200']);
  assert.deepEqual(found('u-1111111111z'), [], 'past every block');
  assert.deepEqual(found('u-0007'), [], 'not a unit id');
  assert.deepEqual(found('e-1111111111H'), [], 'not a unit id');
  assert.deepEqual(candidateBlocks(new Map(), 'u-1111111111A'), []);
});

test('coalesceSpans shares one Range request among neighbors in a part and splits at a gap or a part boundary', () => {
  const rows = [
    { id: 'c', shard_part: 0, byte_start: 100, byte_length: 10 },
    { id: 'a', shard_part: 0, byte_start: 0, byte_length: 10 },
    { id: 'far', shard_part: 0, byte_start: 110 + SPAN_GAP_BYTES + 1, byte_length: 5 },
    { id: 'other-part', shard_part: 1, byte_start: 0, byte_length: 3 },
  ];
  const runs = coalesceSpans(rows);
  assert.deepEqual(
    runs.map((run) => [run.shard_part, run.byte_start, run.byte_length, run.rows.map((row) => row.id)]),
    [
      [0, 0, 110, ['a', 'c']],
      [0, 110 + SPAN_GAP_BYTES + 1, 5, ['far']],
      [1, 0, 3, ['other-part']],
    ],
  );
  assert.equal(rangeHeader(runs[0]), 'bytes=0-109');
  assert.deepEqual(coalesceSpans([]), []);
});

test('coalesceSpans joins two spans exactly a gap apart and keeps the class the part path needs', () => {
  const rows = [
    { class: 'k', shard_part: 2, byte_start: 0, byte_length: 10 },
    { class: 'k', shard_part: 2, byte_start: 10 + SPAN_GAP_BYTES, byte_length: 10 },
  ];
  const runs = coalesceSpans(rows);
  assert.equal(runs.length, 1);
  assert.equal(runs[0].class, 'k');
  assert.equal(runs[0].byte_length, 20 + SPAN_GAP_BYTES);
  assert.equal(coalesceSpans(rows, 0).length, 2, 'a gap of zero shares nothing but abutting spans');
});

test('a run of rows slices each record back out of its bytes', async () => {
  const records = [{ id: 'a' }, { id: 'bb', text: 'x'.repeat(300) }, { id: 'ccc' }];
  const stored = records.map((record) => Buffer.from(JSON.stringify(record), 'ascii'));
  const rows = [];
  let start = 1000;
  for (const [index, bytes] of stored.entries()) {
    rows.push({ id: records[index].id, shard_part: 0, byte_start: start, byte_length: bytes.length });
    start += bytes.length + 1;
  }
  const joined = stored.flatMap((bytes, index) => (index ? [Buffer.from(','), bytes] : [bytes]));
  const runBytes = new Uint8Array(Buffer.concat(joined));
  const [run] = coalesceSpans(rows);
  assert.equal(run.byte_length, runBytes.length);
  assert.deepEqual([...sliceRecordBytes(runBytes, run, rows[1])], [...stored[1]]);
  for (const [index, row] of rows.entries()) {
    assert.deepEqual(JSON.parse(await recordText(runBytes, run, row)), records[index]);
  }
});

test('a fold window is a positive count', () => {
  assert.ok(Number.isInteger(MACHINE_FOLD_WINDOW) && MACHINE_FOLD_WINDOW > 0);
});

test('shardPartPath resolves a row to the part its bytes are in, for both spellings _write_shard produces', () => {
  const bare = { class: 'joins', shard_part: 0 };
  assert.equal(shardPartPath(manifest, bare), 'units/joins.json');
  const numbered = {
    classes: [{ id: 'wide', shards: ['units/wide.000.json', 'units/wide.001.json', 'units/wide.002.json'] }],
  };
  assert.equal(shardPartPath(numbered, { class: 'wide', shard_part: 2 }), 'units/wide.002.json');
});

test('shardPartPath yields nothing for an unknown class or a part the manifest does not list', () => {
  assert.equal(shardPartPath(manifest, { class: 'no-such-class', shard_part: 0 }), null);
  assert.equal(shardPartPath(manifest, { class: 'joins', shard_part: 7 }), null);
  assert.equal(shardPartPath(undefined, { class: 'x', shard_part: 0 }), null);
});

test('rangeHeader asks for exactly byte_length bytes, both ends inclusive', () => {
  assert.equal(rangeHeader({ byte_start: 5, byte_length: 3 }), 'bytes=5-7');
  assert.equal(rangeHeader({ byte_start: 0, byte_length: 1 }), 'bytes=0-0');
  assert.equal(rangeHeader({ byte_start: 1_048_576, byte_length: 2048 }), 'bytes=1048576-1050623');
});

test('createRecordCache evicts the oldest entry once it is over its cap', () => {
  const cache = createRecordCache(3);
  for (const id of ['a', 'b', 'c']) cache.set(id, { id });
  assert.deepEqual(cache.keys(), ['a', 'b', 'c']);
  cache.set('d', { id: 'd' });
  assert.equal(cache.size, 3);
  assert.deepEqual(cache.keys(), ['b', 'c', 'd']);
  assert.equal(cache.has('a'), false);
  assert.equal(cache.get('a'), undefined);
  assert.equal(cache.get('d').id, 'd');
});

test('createRecordCache freshens an entry on a hit, so a re-read is not the next thing dropped', () => {
  const cache = createRecordCache(3);
  for (const id of ['a', 'b', 'c']) cache.set(id, { id });
  assert.equal(cache.get('a').id, 'a');
  cache.set('d', { id: 'd' });
  assert.deepEqual(cache.keys(), ['c', 'a', 'd']);
  assert.equal(cache.has('b'), false);
});

test('createRecordCache re-setting a key keeps one entry and moves it to the end', () => {
  const cache = createRecordCache(3);
  for (const id of ['a', 'b']) cache.set(id, { id });
  cache.set('a', { id: 'a', again: true });
  assert.equal(cache.size, 2);
  assert.deepEqual(cache.keys(), ['b', 'a']);
  assert.equal(cache.get('a').again, true);
  cache.clear();
  assert.equal(cache.size, 0);
});

test('createRecordCache defaults to the cap the app declares', () => {
  const cache = createRecordCache();
  for (let index = 0; index < RECORD_CACHE_CAP + 5; index += 1) cache.set(`u-${index}`, { index });
  assert.equal(cache.size, RECORD_CACHE_CAP);
  assert.equal(cache.has('u-0'), false);
  assert.equal(cache.has(`u-${RECORD_CACHE_CAP + 4}`), true);
});

test('looksGzipped reads the magic number off a body a server handed over undecoded', () => {
  assert.equal(looksGzipped(new Uint8Array([0x1f, 0x8b, 0x08, 0x00])), true);
  assert.equal(looksGzipped(new TextEncoder().encode('{"format":"x"}\n')), false);
  assert.equal(looksGzipped(new Uint8Array([0x1f])), false);
  assert.equal(looksGzipped(new Uint8Array([])), false);
  assert.equal(looksGzipped(undefined), false);
});

test('machineFoldPlan names one fold per class in the batch that holds units needing no verdict', () => {
  const plan = machineFoldPlan(manifest, viewState());
  assert.deepEqual(plan, [
    { classId: 'settlement', total: 1, check: 'ink_identical', provisional: false },
    { classId: 'kerning', total: 1, check: 'no_verdict', provisional: false },
    { classId: 'provenance-only', total: 1, check: 'no_verdict', provisional: false },
  ]);
});

test('machineFoldPlan marks its totals provisional under a filter the manifest cannot answer', () => {
  for (const filter of [{ family: 'qsTea' }, { group: 'qsTea:qsOy' }, { config: 'ss03' }]) {
    const plan = machineFoldPlan(manifest, viewState(filter));
    assert.deepEqual(
      plan.map((fold) => fold.provisional),
      [true, true, true],
      JSON.stringify(filter),
    );
  }
  // The plan applies the class filter itself, so the totals stay exact.
  assert.equal(
    machineFoldPlan(manifest, viewState({ class: 'settlement' }))[0].provisional,
    false,
  );
});

test('machineFoldPlan plans nothing while the show-machine toggle is off, or inside a worklist', () => {
  assert.deepEqual(machineFoldPlan(manifest, viewState({ machine: null })), []);
  assert.deepEqual(machineFoldPlan(manifest, viewState({ units: 'u-0004' })), []);
});

test('machineFoldPlan honors the class filter and skips classes with nothing to fold', () => {
  assert.deepEqual(
    machineFoldPlan(manifest, viewState({ class: 'settlement' })).map((fold) => fold.classId),
    ['settlement'],
  );
  assert.deepEqual(machineFoldPlan(manifest, viewState({ class: 'joins' })), []);
});

test('machineFoldPlan drops a class out of the view when the batch moves past it', () => {
  assert.deepEqual(
    machineFoldPlan(manifest, viewState({ batch: 1 })),
    [],
    'batch 1 holds only glyphs units, and a batchless class folds into batch 0 alone',
  );
});

test('machineFoldPlan includes a batchless class with batch 0, the way unitsForView did', () => {
  const batchless = {
    classes: [
      { id: 'all-machine', batches: [], unit_count: 9, machine_approved_count: 9, no_verdict: false, machine_checks: { ink_identical: 9, picture_identical: 0 } },
    ],
  };
  assert.deepEqual(machineFoldPlan(batchless, viewState()), [
    { classId: 'all-machine', total: 9, check: 'ink_identical', provisional: false },
  ]);
  assert.deepEqual(machineFoldPlan(batchless, viewState({ batch: 1 })), []);
});
