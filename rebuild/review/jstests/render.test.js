import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {
  featureSettingsValue,
  highlightRect,
  markOffset,
  familiesOfGroup,
  unitMatchesFilters,
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

test('copyPreamble names the unit, codepoints, notation, class, and configs', () => {
  const text = copyPreamble(shardB[0]);
  assert.match(text, /rebuild\/out\/review\/ unit u-0005/);
  assert.match(text, /E668:E665:E657/);
  assert.match(text, /·Roe·May·They/);
  assert.match(text, /dangling-anchor-dropped/);
  assert.match(text, /default, ss05/);
});

test('fixture units satisfy the contract fields the frontend relies on', () => {
  for (const unit of [...shardA, ...shardB]) {
    assert.match(unit.id, /^u-\d{4}$/);
    assert.equal(typeof unit.batch, 'number');
    assert.equal(typeof unit.text_entities, 'string');
    assert.doesNotMatch(unit.text_entities, /[\u200C\uE650-\uE67E]/);
    assert.ok(Array.isArray(unit.configs) && unit.configs.length >= 1);
    if (unit.pair !== null) {
      assert.ok(unit.highlight.before.x_max > unit.highlight.before.x_min);
      assert.ok(unit.highlight.after.x_max > unit.highlight.after.x_min);
    }
    for (const mark of unit.boundary_marks) {
      assert.equal(typeof mark.x, 'number');
      assert.ok(['zwnj', 'space'].includes(mark.kind));
    }
  }
});
