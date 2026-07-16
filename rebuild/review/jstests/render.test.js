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
  cellCodepointSpans,
  onlyHereSeamSpans,
  tokenMarkRuns,
  needsNoVerdict,
  familiesOfGroup,
  unitMatchesFilters,
  unitWorklist,
  partitionUnits,
  humanClassCount,
  humanTotal,
  noVerdictTotal,
  formatCount,
  machineChannels,
  surfaceLine,
  machineLine,
  noVerdictLine,
  classCountsLine,
  nextUnverdictedIndex,
  stepIndex,
  availableBatches,
  classesInBatch,
  copyPreamble,
  isLetterToken,
  tokenSeparators,
  searchHaystack,
  searchUnits,
  echoChip,
  echoFillTargets,
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

test('echoChip appears only for multi-member echo groups and deep-links the worklist', () => {
  const unit = { id: 'u-0005', echo: 'e-0000', class: 'dangling-anchor-dropped' };
  assert.equal(echoChip(unit, ['u-0005']), null);
  assert.equal(echoChip({ ...unit, echo: null }, ['u-0005', 'u-0006']), null);
  const chip = echoChip(unit, ['u-0005', 'u-0006', 'u-0007']);
  assert.equal(chip.label, 'echo ×3');
  assert.equal(chip.href, '#units=u-0005,u-0006,u-0007');
  assert.ok(chip.title.includes('dangling-anchor-dropped'));
});

test('echoFillTargets excludes the unit itself and anything already verdicted', () => {
  const unit = { id: 'u-0005', echo: 'e-0000' };
  const verdicted = new Set(['u-0007']);
  assert.deepEqual(
    echoFillTargets(unit, ['u-0005', 'u-0006', 'u-0007'], (id) => verdicted.has(id)),
    ['u-0006'],
  );
  assert.deepEqual(echoFillTargets({ id: 'u-0005', echo: null }, ['u-0005', 'u-0006'], () => false), []);
  assert.deepEqual(echoFillTargets(undefined, ['u-0005'], () => false), []);
});

test('fixture echo ids group only within a shard and singletons carry their own id', () => {
  const members = new Map();
  for (const unit of [...shardA, ...shardB]) {
    if (unit.echo === null) {
      assert.equal(unit.batch, null, unit.id);
      continue;
    }
    if (!members.has(unit.echo)) members.set(unit.echo, []);
    members.get(unit.echo).push(unit);
  }
  const grouped = [...members.values()].find((units) => units.length > 1);
  assert.ok(grouped, 'fixtures must exercise a multi-member echo group');
  assert.equal(new Set(grouped.map((unit) => unit.class)).size, 1);
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

test('unitWorklist splits, trims, and drops empties', () => {
  assert.deepEqual(unitWorklist('u-1163,u-2224'), ['u-1163', 'u-2224']);
  assert.deepEqual(unitWorklist(' u-1163 , u-2224 ,'), ['u-1163', 'u-2224']);
  assert.deepEqual(unitWorklist(''), []);
  assert.deepEqual(unitWorklist(null), []);
});

test('the units worklist filter keeps only the listed ids and composes with other filters', () => {
  const unit = shardA[0];
  const empty = { class: null, group: null, family: null, config: null, status: null, units: null };
  assert.equal(unitMatchesFilters(unit, { ...empty, units: `other,${unit.id}` }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, units: 'u-9990,u-9991' }, undefined), false);
  assert.equal(unitMatchesFilters(unit, { ...empty, units: unit.id, class: 'dangling-anchor-dropped' }, undefined), false);
});

const emptyFilters = {
  class: null,
  group: null,
  family: null,
  config: null,
  status: null,
  machine: null,
  units: null,
};
const allUnits = [...shardA, ...shardB];
const noRecords = () => undefined;

test('partitionUnits with a units worklist narrows the human queue to the listed ids', () => {
  const wanted = allUnits.filter((unit) => !unit.ink_identical).slice(0, 2).map((unit) => unit.id);
  const { human } = partitionUnits(allUnits, { ...emptyFilters, units: wanted.join(',') }, noRecords);
  assert.deepEqual(human.map((unit) => unit.id).sort(), [...wanted].sort());
});

test('a units worklist spanning classes and batches keeps every listed unit visible, including a machine-approved one', () => {
  const machineUnit = allUnits.find((unit) => unit.ink_identical);
  const humanUnits = allUnits.filter((unit) => !unit.ink_identical);
  const wanted = [humanUnits[0].id, machineUnit.id, humanUnits[humanUnits.length - 1].id];
  const { human, machine } = partitionUnits(allUnits, { ...emptyFilters, units: wanted.join(',') }, noRecords);
  const shown = new Set([...human, ...machine].map((unit) => unit.id));
  for (const id of wanted) assert.ok(shown.has(id), `${id} must render in the worklist view`);
  assert.ok(machine.some((unit) => unit.id === machineUnit.id), 'a machine-approved unit named in the worklist stays visible without the machine toggle');
  assert.ok(human.some((unit) => unit.id === humanUnits[0].id), 'the human units in the worklist stay in the verdict queue');
  assert.ok(human.some((unit) => unit.id === humanUnits[humanUnits.length - 1].id), 'a worklist unit from a different class and batch still renders');
});

test('a units worklist is exclusive: class/config/status filters never drop a listed id', () => {
  const machineUnit = allUnits.find((unit) => unit.ink_identical);
  const humanUnits = allUnits.filter((unit) => !unit.ink_identical);
  const wanted = [humanUnits[0].id, machineUnit.id, humanUnits[humanUnits.length - 1].id];
  const filters = {
    ...emptyFilters,
    units: wanted.join(','),
    class: 'dangling-anchor-dropped',
    config: 'ss04',
    status: 'verdicted',
  };
  const { human, machine } = partitionUnits(allUnits, filters, noRecords);
  const shown = new Set([...human, ...machine].map((unit) => unit.id));
  for (const id of wanted) assert.ok(shown.has(id), `${id} must render despite conflicting class/config/status filters`);
  assert.ok(machine.some((unit) => unit.id === machineUnit.id), 'a machine-approved listed id survives a conflicting class filter');
  assert.equal(shown.size, wanted.length, 'no unlisted unit leaks into the worklist view');
});

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

test('needsNoVerdict covers all three exemption kinds', () => {
  assert.equal(needsNoVerdict({ ink_identical: true, no_verdict: false }), true);
  assert.equal(needsNoVerdict({ ink_identical: false, junior_equivalent: true, no_verdict: false }), true);
  assert.equal(needsNoVerdict({ ink_identical: false, no_verdict: true }), true);
  assert.equal(needsNoVerdict({ ink_identical: false, no_verdict: false }), false);
  assert.equal(needsNoVerdict({ ink_identical: false }), false, 'a legacy unit without the field stays human');
});

test('a no-verdict unit leaves the human queue and appears with the machine toggle', () => {
  const exemptUnit = { ...allUnits.find((unit) => !unit.ink_identical), id: 'u-8888', no_verdict: true, batch: null };
  const units = [...allUnits, exemptUnit];
  const off = partitionUnits(units, emptyFilters, noRecords);
  assert.ok(!off.human.some((unit) => unit.id === 'u-8888'));
  assert.deepEqual(off.machine, []);
  const on = partitionUnits(units, { ...emptyFilters, machine: '1' }, noRecords);
  assert.ok(!on.human.some((unit) => unit.id === 'u-8888'));
  assert.ok(on.machine.some((unit) => unit.id === 'u-8888'));
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
  assert.equal(noVerdictTotal(manifest), 0);
});

test('a no-verdict class contributes nothing to the human workload and everything non-identical to the exempt total', () => {
  const exemptClass = { id: 'boundary-echo', no_verdict: true, unit_count: 6344, machine_approved_count: 4465 };
  assert.equal(humanClassCount(exemptClass), 0);
  const synthetic = { totals: { units: 6350 }, classes: [exemptClass, { id: 'x', no_verdict: false, unit_count: 6, machine_approved_count: 1 }] };
  assert.equal(humanTotal(synthetic), 5);
  assert.equal(noVerdictTotal(synthetic), 1879);
});

test('formatCount groups thousands', () => {
  assert.equal(formatCount(0), '0');
  assert.equal(formatCount(300), '300');
  assert.equal(formatCount(2562), '2,562');
  assert.equal(formatCount(15960), '15,960');
});

test('machineChannels splits the machine-approved total and treats a channel-less manifest as all ink-identical', () => {
  assert.deepEqual(machineChannels(manifest), { units: 1, inkIdentical: 1, juniorEquivalent: 0 });
  const channelled = {
    machine_approved: {
      units: 11926,
      channels: { ink_identical: { units: 8350 }, junior_equivalent: { units: 3576 } },
    },
  };
  assert.deepEqual(machineChannels(channelled), { units: 11926, inkIdentical: 8350, juniorEquivalent: 3576 });
  assert.deepEqual(machineChannels({}), { units: 0, inkIdentical: 0, juniorEquivalent: 0 });
});

test('the header strip lines run big to small: surface total, machine channels, no-verdict exemptions', () => {
  assert.equal(surfaceLine(manifest), 'Surface: 6 units');
  assert.equal(machineLine(manifest), '1 ink-identical machine-approved');
  assert.equal(noVerdictLine(manifest), null);
  const channelled = {
    totals: { units: 15960 },
    machine_approved: {
      units: 11926,
      channels: { ink_identical: { units: 8350 }, junior_equivalent: { units: 3576 } },
    },
    classes: [{ id: 'boundary-echo', no_verdict: true, unit_count: 6256, machine_approved_count: 4940 }],
  };
  assert.equal(surfaceLine(channelled), 'Surface: 15,960 units');
  assert.equal(
    machineLine(channelled),
    '11,926 machine-approved (8,350 ink-identical + 3,576 junior-equivalent)',
  );
  assert.equal(noVerdictLine(channelled), '1,316 in no-verdict classes');
  assert.equal(machineLine({ machine_approved: { units: 0 } }), null);
});

test('classCountsLine orders each sidebar entry big to small: units, machine, human progress, rows', () => {
  const mixed = { no_verdict: false, unit_count: 3361, machine_approved_count: 3344, row_count: 22075 };
  assert.equal(classCountsLine(mixed, null), '3,361 units · 3,344 machine · 17 to review · 22,075 rows');
  assert.equal(classCountsLine(mixed, 12), '3,361 units · 3,344 machine · 12/17 · 22,075 rows');
  const plain = { no_verdict: false, unit_count: 166, machine_approved_count: 0, row_count: 1048 };
  assert.equal(classCountsLine(plain, null), '166 units · 166 to review · 1,048 rows');
  assert.equal(classCountsLine(plain, 0), '166 units · 0/166 · 1,048 rows');
  const allMachine = { no_verdict: false, unit_count: 1722, machine_approved_count: 1722, row_count: 1722 };
  assert.equal(classCountsLine(allMachine, null), '1,722 units · 1,722 machine · 1,722 rows');
  const exempt = { no_verdict: true, unit_count: 6256, machine_approved_count: 4940, row_count: 34477 };
  assert.equal(classCountsLine(exempt, null), '6,256 units — no verdict needed · 34,477 rows');
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

test('classesInBatch names the classes with units in a batch, batchless classes riding with batch 0', () => {
  assert.deepEqual(
    [...classesInBatch(manifest, 0)].sort(),
    ['dangling-anchor-dropped', 'marker-staging-ligature-formation'],
  );
  assert.deepEqual([...classesInBatch(manifest, 1)], ['dangling-anchor-dropped']);
  const withExempt = { classes: [...manifest.classes, { id: 'boundary-echo', batches: [], no_verdict: true }] };
  assert.deepEqual(
    [...classesInBatch(withExempt, 0)].sort(),
    ['boundary-echo', 'dangling-anchor-dropped', 'marker-staging-ligature-formation'],
  );
  assert.deepEqual([...classesInBatch(withExempt, 1)], ['dangling-anchor-dropped']);
});

test('copyPreamble names only the unit, codepoints, and notation — the rest is looked up from the shards', () => {
  const text = copyPreamble(shardB[0]);
  assert.match(text, /rebuild\/out\/review\/ unit u-0005/);
  assert.match(text, /E668:E665:E657/);
  assert.match(text, /·Roe·May·They/);
  assert.doesNotMatch(text, /dangling-anchor-dropped/);
  assert.doesNotMatch(text, /ss05/);
});

test('isLetterToken accepts letter names and rejects boundary tokens', () => {
  assert.equal(isLetterToken('·May'), true);
  assert.equal(isLetterToken('·-ing'), true);
  assert.equal(isLetterToken('·J’ai'), true);
  assert.equal(isLetterToken('·'), false, 'the bare namer dot is a boundary token');
  assert.equal(isLetterToken('◊ZWNJ'), false);
  assert.equal(isLetterToken('␣'), false);
  assert.equal(isLetterToken('U+E6FF'), false);
});

test('tokenSeparators reproduces the notation spacing rule', () => {
  const join = (tokens) => tokenSeparators(tokens).map((sep, index) => sep + tokens[index]).join('');
  assert.equal(join(['◊ZWNJ', '·Tea', '·Oy']), '◊ZWNJ ·Tea·Oy');
  assert.equal(join(['·Pea', '·May']), '·Pea·May');
  assert.equal(join(['·', '·Oy']), '· ·Oy');
  assert.equal(join(['·Pea', '␣', '·Pea']), '·Pea ␣ ·Pea');
});

test('every fixture unit joins its notation tokens back into its notation string', () => {
  for (const unit of [...shardA, ...shardB]) {
    const tokens = unit.notation_tokens;
    assert.equal(tokens.length, unit.codepoints.split(':').length, unit.id);
    const joined = tokenSeparators(tokens).map((sep, index) => sep + tokens[index]).join('');
    assert.equal(joined, unit.notation, unit.id);
    if (unit.pair_codepoints !== null) {
      const [start, end] = unit.pair_codepoints;
      assert.ok(Number.isInteger(start) && Number.isInteger(end) && 0 <= start && start <= end, unit.id);
      assert.ok(end < tokens.length, unit.id);
    }
  }
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

test('cellCodepointSpans gives each cell one codepoint position and a formed ligature two', () => {
  assert.deepEqual(
    cellCodepointSpans(['uni200C', 'qsTea_qsOy/hapax/None/None/+locked', 'qsUtter/mono/None/None/']),
    [[0, 0], [1, 2], [3, 3]],
  );
});

const onlyHereUnit = {
  ink_identical: false,
  pair: { left: 0, right: 1 },
  pair_codepoints: [0, 1],
  after: {
    cells: ['qsNo/loop/None/x-height/', 'qsIt/hapax/x-height/baseline/', 'qsMay/loop/baseline/None/', 'qsTea/full/None/None/'],
  },
  secondary_seams: [
    { pair: { left: 1, right: 2 }, home: null },
    { pair: { left: 2, right: 3 }, home: 'u-0001' },
  ],
};

test('onlyHereSeamSpans maps home-less seams to codepoint spans and skips homed ones', () => {
  assert.deepEqual(onlyHereSeamSpans(onlyHereUnit), [[1, 2]]);
});

test('onlyHereSeamSpans shifts spans across a formed ligature', () => {
  const unit = {
    ...onlyHereUnit,
    pair_codepoints: [0, 2],
    after: { cells: ['qsTea_qsOy/full/None/None/', 'qsIt/hapax/x-height/baseline/', 'qsMay/loop/baseline/None/'] },
    secondary_seams: [{ pair: { left: 1, right: 2 }, home: null }],
  };
  assert.deepEqual(onlyHereSeamSpans(unit), [[2, 3]]);
});

test('onlyHereSeamSpans yields nothing for machine-approved units, missing cells, no pair, or a derivation that disagrees with the build', () => {
  assert.deepEqual(onlyHereSeamSpans({ ...onlyHereUnit, ink_identical: true }), []);
  assert.deepEqual(onlyHereSeamSpans({ ...onlyHereUnit, after: {} }), []);
  assert.deepEqual(onlyHereSeamSpans({ ...onlyHereUnit, pair: null }), []);
  assert.deepEqual(onlyHereSeamSpans({ ...onlyHereUnit, pair_codepoints: [0, 2] }), []);
});

test('the only-here fixture unit underlines its seam tokens', () => {
  const unit = shardB.find((entry) => entry.id === 'u-0006');
  assert.deepEqual(onlyHereSeamSpans(unit), [[1, 2]]);
});

test('tokenMarkRuns marks pair and seam stretches, sharing a separator only between two tokens under the same mark', () => {
  const runs = tokenMarkRuns(['E666', 'E670', 'E665', 'E652'], ['', ':', ':', ':'], [0, 1], [[1, 2]]);
  assert.deepEqual(runs, [
    { text: 'E666:', pair: true, seam: false },
    { text: 'E670', pair: true, seam: true },
    { text: ':E665', pair: false, seam: true },
    { text: ':E652', pair: false, seam: false },
  ]);
  assert.equal(runs.map((run) => run.text).join(''), 'E666:E670:E665:E652');
});

test('tokenMarkRuns without seam spans reproduces the single pair-mark split', () => {
  assert.deepEqual(tokenMarkRuns(['·No', '·It', '·May', '·Tea'], ['', '', '', ''], [0, 1], []), [
    { text: '·No·It', pair: true, seam: false },
    { text: '·May·Tea', pair: false, seam: false },
  ]);
  assert.deepEqual(tokenMarkRuns(['◊ZWNJ', '·Tea', '·Oy'], ['', ' ', ''], [1, 2], []), [
    { text: '◊ZWNJ ', pair: false, seam: false },
    { text: '·Tea·Oy', pair: true, seam: false },
  ]);
});

test('fixture units satisfy the contract fields the frontend relies on', () => {
  for (const unit of [...shardA, ...shardB]) {
    assert.match(unit.id, /^u-\d{4}$/);
    assert.equal(typeof unit.ink_identical, 'boolean');
    assert.equal(typeof unit.no_verdict, 'boolean');
    if (unit.ink_identical || unit.no_verdict) assert.equal(unit.batch, null);
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

test('searchHaystack folds id, notation, codepoints, class, group, and kinds into one lowercase string', () => {
  const haystack = searchHaystack(shardA[0]);
  assert.ok(haystack.includes('u-0001'));
  assert.ok(haystack.includes('·tea·oy'));
  assert.ok(haystack.includes('teaoy'), 'notation with the namer dots stripped is searchable');
  assert.ok(haystack.includes('200c:e652:e679'));
  assert.ok(haystack.includes('200ce652e679'), 'codepoints with the colons stripped are searchable');
  assert.ok(haystack.includes('marker-staging-ligature-formation'));
  assert.ok(haystack.includes('qstea:qsoy'));
  assert.ok(haystack.includes('ligation'));
});

test('searchUnits finds a unit by its exact id across every shard', () => {
  const { matches, total } = searchUnits(allUnits, 'u-0006');
  assert.equal(total, 1);
  assert.equal(matches[0].id, 'u-0006');
});

test('searchUnits matches notation with and without the namer dots, case-insensitively', () => {
  assert.deepEqual(
    searchUnits(allUnits, '·Pea·May').matches.map((unit) => unit.id),
    ['u-0003'],
  );
  assert.deepEqual(
    searchUnits(allUnits, 'peamay').matches.map((unit) => unit.id),
    ['u-0003'],
  );
});

test('searchUnits matches codepoints with and without the colons', () => {
  assert.deepEqual(searchUnits(allUnits, 'E66C').matches.map((unit) => unit.id), ['u-0006']);
  assert.deepEqual(searchUnits(allUnits, 'e670e653').matches.map((unit) => unit.id), ['u-0006']);
});

test('searchUnits matches class, group, and kind, and includes machine-approved units', () => {
  const byClass = searchUnits(allUnits, 'dangling-anchor-dropped');
  assert.deepEqual(byClass.matches.map((unit) => unit.id).sort(), ['u-0005', 'u-0006']);
  const extension = searchUnits(allUnits, 'extension');
  assert.deepEqual(extension.matches.map((unit) => unit.id), ['u-0004']);
  assert.equal(extension.matches[0].ink_identical, true, 'a machine-approved unit is still findable');
});

test('searchUnits requires every whitespace-separated token to match (AND)', () => {
  assert.deepEqual(searchUnits(allUnits, 'tea oy').matches.map((unit) => unit.id).sort(), ['u-0001', 'u-0002']);
  assert.equal(searchUnits(allUnits, 'tea exam').total, 0);
});

test('searchUnits ranks an exact id hit ahead of incidental substring hits', () => {
  // "u-0005" appears verbatim only in u-0005, but a 3-codepoint substring could in principle collide; the exact-id rank keeps it first.
  const { matches } = searchUnits(allUnits, 'u-0005');
  assert.equal(matches[0].id, 'u-0005');
});

test('searchUnits caps the matches at the limit but reports the true total', () => {
  const { matches, total } = searchUnits(allUnits, 'u-', 2);
  assert.equal(total, 6, 'every fixture unit id starts with u-');
  assert.equal(matches.length, 2);
});

test('searchUnits returns nothing for a blank query', () => {
  assert.deepEqual(searchUnits(allUnits, '   '), { matches: [], total: 0 });
});
