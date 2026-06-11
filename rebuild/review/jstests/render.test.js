import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {
  featureSettingsValue,
  renderGroupsOf,
  highlightRect,
  markOffset,
  secondarySeamsOf,
  seamChip,
  familiesOfGroup,
  unitMatchesFilters,
  partitionUnits,
  humanClassCount,
  humanTotal,
  nextUnverdictedIndex,
  stepIndex,
  availableBatches,
  copyPreamble,
} from '../static/render.js';

const fixtureDir = new URL('./fixtures/', import.meta.url);
const manifest = JSON.parse(await readFile(new URL('manifest.json', fixtureDir), 'utf8'));
const shardA = JSON.parse(await readFile(new URL('units/marker-staging-ligature-formation.json', fixtureDir), 'utf8'));
const shardB = JSON.parse(await readFile(new URL('units/dangling-anchor-dropped.json', fixtureDir), 'utf8'));

test('featureSettingsValue maps config tokens per the plan', () => {
  assert.equal(featureSettingsValue('default'), 'normal');
  assert.equal(featureSettingsValue(null), 'normal');
  assert.equal(featureSettingsValue('ss03'), '"ss03" 1');
  assert.equal(featureSettingsValue('ss02+ss03'), '"ss02" 1, "ss03" 1');
  assert.equal(featureSettingsValue('ss02+ss03+ss05'), '"ss02" 1, "ss03" 1, "ss05" 1');
  assert.equal(featureSettingsValue('ss10'), '"ss10" 1');
});

test('every fixture config token produces a parseable settings value', () => {
  for (const config of manifest.configs) {
    const value = featureSettingsValue(config);
    assert.ok(value === 'normal' || /^("ss\d{2}" 1)(, "ss\d{2}" 1)*$/.test(value), config);
  }
});

const twoGroupUnit = {
  id: 'u-9999',
  configs: ['default', 'ss03', 'ss02+ss03'],
  render_groups: [{ configs: ['default'] }, { configs: ['ss03', 'ss02+ss03'] }],
};

test('renderGroupsOf stacks a synthetic two-group unit with per-group feature settings', () => {
  const groups = renderGroupsOf(twoGroupUnit);
  assert.equal(groups.length, 2);
  assert.deepEqual(groups[0], { configs: ['default'], label: 'default', featureSettings: 'normal', primary: true });
  assert.deepEqual(groups[1], {
    configs: ['ss03', 'ss02+ss03'],
    label: 'ss03, ss02+ss03',
    featureSettings: '"ss03" 1',
    primary: false,
  });
});

test('renderGroupsOf collapses a single-group unit and tolerates missing render_groups', () => {
  const single = { configs: ['ss03', 'ss02+ss03'], render_groups: [{ configs: ['ss03', 'ss02+ss03'] }] };
  assert.equal(renderGroupsOf(single).length, 1);
  assert.equal(renderGroupsOf(single)[0].featureSettings, '"ss03" 1');
  const legacy = { configs: ['ss05'] };
  assert.deepEqual(renderGroupsOf(legacy), [
    { configs: ['ss05'], label: 'ss05', featureSettings: '"ss05" 1', primary: true },
  ]);
});

test('every fixture unit carries exactly one render group covering its configs', () => {
  for (const unit of [...shardA, ...shardB]) {
    const groups = renderGroupsOf(unit);
    assert.equal(groups.length, 1, unit.id);
    assert.deepEqual(groups[0].configs, unit.configs, unit.id);
  }
});

test('highlightRect converts font units at font-size / upem', () => {
  const rect = highlightRect({ x_min: 0, x_max: 1100, advance_total: 1650 }, 88, 550);
  assert.equal(rect.left, 0);
  assert.equal(rect.width, 176);
  const inset = highlightRect({ x_min: 275, x_max: 1375 }, 88, 550);
  assert.equal(inset.left, 44);
  assert.equal(inset.width, 176);
});

test('markOffset converts a boundary mark x position', () => {
  assert.equal(markOffset(0, 88, 550), 0);
  assert.equal(markOffset(137, 88, 550), 21.92);
});

test('familiesOfGroup splits the lead pair', () => {
  assert.deepEqual(familiesOfGroup('qsTea:qsOy'), ['qsTea', 'qsOy']);
  assert.deepEqual(familiesOfGroup(null), []);
});

test('unitMatchesFilters covers class, group, family, config, and status', () => {
  const unit = shardA[0];
  const empty = { class: null, group: null, family: null, config: null, status: null };
  assert.equal(unitMatchesFilters(unit, empty, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, class: 'marker-staging-ligature-formation' }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, class: 'dangling-anchor-dropped' }, undefined), false);
  assert.equal(unitMatchesFilters(unit, { ...empty, group: 'qsTea:qsOy' }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, family: 'qsOy' }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, family: 'qsPea' }, undefined), false);
  assert.equal(unitMatchesFilters(unit, { ...empty, config: 'ss02+ss03' }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, config: 'ss04' }, undefined), false);
  assert.equal(unitMatchesFilters(unit, { ...empty, status: 'unverdicted' }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, status: 'unverdicted' }, { verdict: 'approve' }), false);
  assert.equal(unitMatchesFilters(unit, { ...empty, status: 'verdicted' }, { verdict: 'approve' }), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, status: 'approve' }, { verdict: 'approve' }), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, status: 'reject' }, { verdict: 'approve' }), false);
});

const emptyFilters = {
  class: null,
  group: null,
  family: null,
  config: null,
  status: null,
  machine: null,
};
const allUnits = [...shardA, ...shardB];
const noRecords = () => undefined;

test('ink-identical units are hidden unless the machine toggle is on', () => {
  const off = partitionUnits(allUnits, emptyFilters, noRecords);
  assert.deepEqual(
    off.human.map((unit) => unit.id),
    allUnits.filter((unit) => !unit.ink_identical).map((unit) => unit.id),
  );
  assert.deepEqual(off.machine, []);
  const on = partitionUnits(allUnits, { ...emptyFilters, machine: '1' }, noRecords);
  assert.deepEqual(on.human.map((unit) => unit.id), off.human.map((unit) => unit.id));
  assert.deepEqual(
    on.machine.map((unit) => unit.id),
    allUnits.filter((unit) => unit.ink_identical).map((unit) => unit.id),
  );
  assert.ok(on.machine.length >= 1);
});

test('class and family filters apply to machine units; the status filter does not', () => {
  const machineUnit = allUnits.find((unit) => unit.ink_identical);
  const filters = { ...emptyFilters, machine: '1', status: 'unverdicted' };
  const partitioned = partitionUnits(allUnits, filters, noRecords);
  assert.ok(partitioned.machine.some((unit) => unit.id === machineUnit.id));
  const wrongClass = partitionUnits(allUnits, { ...filters, class: 'dangling-anchor-dropped' }, noRecords);
  assert.deepEqual(wrongClass.machine, []);
});

test('human and machine counts come from the manifest class metadata', () => {
  const marker = manifest.classes.find((cls) => cls.id === 'marker-staging-ligature-formation');
  const dangling = manifest.classes.find((cls) => cls.id === 'dangling-anchor-dropped');
  assert.equal(humanClassCount(marker), marker.unit_count - 1);
  assert.equal(humanClassCount(dangling), dangling.unit_count);
  assert.equal(humanTotal(manifest), manifest.totals.units - manifest.machine_approved.units);
});

test('nextUnverdictedIndex advances, wraps, and reports exhaustion', () => {
  const ids = ['a', 'b', 'c', 'd'];
  const verdicted = new Set(['a', 'c']);
  const has = (id) => verdicted.has(id);
  assert.equal(nextUnverdictedIndex(ids, 0, has), 1);
  assert.equal(nextUnverdictedIndex(ids, 1, has), 3);
  assert.equal(nextUnverdictedIndex(ids, 3, has), 1);
  assert.equal(nextUnverdictedIndex(ids, 2, has), 3);
  assert.equal(nextUnverdictedIndex(ids, 0, () => true), -1);
  assert.equal(nextUnverdictedIndex([], 0, has), -1);
});

test('stepIndex clamps at the ends', () => {
  assert.equal(stepIndex(4, 0, -1), 0);
  assert.equal(stepIndex(4, 3, 1), 3);
  assert.equal(stepIndex(4, 1, 1), 2);
  assert.equal(stepIndex(0, 0, 1), -1);
});

test('availableBatches respects a class filter', () => {
  assert.deepEqual(availableBatches(manifest, null), [0, 1]);
  assert.deepEqual(availableBatches(manifest, 'dangling-anchor-dropped'), [0, 1]);
  assert.deepEqual(availableBatches(manifest, 'marker-staging-ligature-formation'), [0]);
  assert.deepEqual(availableBatches(manifest, 'nonexistent'), []);
});

test('copyPreamble names only the unit, codepoints, and notation — the rest is looked up from the shards', () => {
  const text = copyPreamble(shardB[0]);
  assert.match(text, /rebuild\/out\/review\/ unit u-0005/);
  assert.match(text, /E668:E665:E657/);
  assert.match(text, /·Roe·May·They/);
  assert.doesNotMatch(text, /dangling-anchor-dropped/);
  assert.doesNotMatch(text, /ss05/);
});

test('secondarySeamsOf returns seams for human units and nothing for machine-approved or legacy units', () => {
  const homed = shardB.find((unit) => unit.id === 'u-0005');
  assert.equal(secondarySeamsOf(homed).length, 1);
  assert.equal(secondarySeamsOf(homed)[0].home, 'u-0003');
  const legacy = { ink_identical: false };
  assert.deepEqual(secondarySeamsOf(legacy), []);
  const nulled = { ink_identical: false, secondary_seams: null };
  assert.deepEqual(secondarySeamsOf(nulled), []);
  const machine = { ink_identical: true, secondary_seams: [{ home: 'u-0003' }] };
  assert.deepEqual(secondarySeamsOf(machine), [], 'machine-approved renderings never show seam markers');
});

test('seamChip labels a homed seam with the home unit id and a home-less seam with "only here"', () => {
  const homed = seamChip({ home: 'u-0312' });
  assert.equal(homed.home, 'u-0312');
  assert.equal(homed.label, 'u-0312');
  assert.match(homed.title, /u-0312/);
  const homeless = seamChip({ home: null });
  assert.equal(homeless.home, null);
  assert.equal(homeless.label, 'only here');
  assert.match(homeless.title, /no shorter home/);
});

test('fixture units satisfy the contract fields the frontend relies on', () => {
  for (const unit of [...shardA, ...shardB]) {
    assert.match(unit.id, /^u-\d{4}$/);
    assert.equal(typeof unit.ink_identical, 'boolean');
    if (unit.ink_identical) assert.equal(unit.batch, null);
    else assert.equal(typeof unit.batch, 'number');
    assert.equal(typeof unit.text_entities, 'string');
    assert.doesNotMatch(unit.text_entities, /[\u200C\uE650-\uE67E]/);
    assert.ok(Array.isArray(unit.configs) && unit.configs.length >= 1);
    assert.ok(unit.config_note === null || (typeof unit.config_note === 'string' && unit.config_note.length > 0));
    assert.ok(Array.isArray(unit.render_groups) && unit.render_groups.length >= 1);
    assert.ok(typeof unit.summary === 'string' && unit.summary.length > 0);
    if (unit.pair !== null) {
      assert.ok(unit.highlight.before.x_max > unit.highlight.before.x_min);
      assert.ok(unit.highlight.after.x_max > unit.highlight.after.x_min);
    }
    for (const mark of unit.boundary_marks) {
      assert.equal(typeof mark.x, 'number');
      assert.ok(['zwnj', 'space'].includes(mark.kind));
    }
    if (unit.secondary_seams != null) {
      assert.ok(Array.isArray(unit.secondary_seams) && unit.secondary_seams.length >= 1);
      assert.equal(unit.ink_identical, false);
      for (const seam of unit.secondary_seams) {
        assert.ok(Number.isInteger(seam.pair.left) && Number.isInteger(seam.pair.right));
        assert.ok(seam.pair.left < seam.pair.right);
        assert.notDeepEqual(seam.pair, unit.pair, `${unit.id}: a secondary seam must not duplicate the primary pair`);
        for (const side of ['before', 'after']) {
          assert.ok(Number.isInteger(seam[side].x_min) && Number.isInteger(seam[side].x_max));
          assert.ok(seam[side].x_min <= seam[side].x_max);
          assert.ok(Number.isInteger(seam[side].advance_total));
        }
        assert.ok(seam.home === null || /^u-\d{4}$/.test(seam.home));
      }
    }
  }
  assert.ok(
    [...shardA, ...shardB].some((unit) => (unit.secondary_seams ?? []).some((seam) => seam.home)),
    'the fixtures must exercise a homed secondary seam',
  );
  assert.ok(
    [...shardA, ...shardB].some((unit) => (unit.secondary_seams ?? []).some((seam) => seam.home === null)),
    'the fixtures must exercise a home-less secondary seam',
  );
});
