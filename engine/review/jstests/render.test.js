import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {
  featureSettingsValue,
  configGateChips,
  configFilterOptions,
  explainRuns,
  renderGroupsOf,
  highlightRect,
  pairBand,
  markOffset,
  namerDotStandIn,
  namerDotStandInEntities,
  tokenMarkRuns,
  needsNoVerdict,
  familiesOfGroup,
  unitMatchesFilters,
  unitWorklist,
  orderWorklist,
  triageOrder,
  partitionUnits,
  humanClassCount,
  humanTotal,
  machineFoldCheck,
  machineFoldTotal,
  noVerdictTotal,
  formatCount,
  verdictAtText,
  machineChecks,
  corpusChipLabel,
  corpusAlphabetLabel,
  corpusStampLine,
  corpusDetailRows,
  machineTitle,
  classCountsLine,
  classVerdictTallies,
  classLeft,
  classIsDone,
  SECTION_ORDER,
  SECTION_LABELS,
  PREDATES_APP,
  manifestRefusal,
  classInfo,
  classChip,
  codeRuns,
  appendCodeRuns,
  rationaleParagraphs,
  sidebarSections,
  sectionTotalsLine,
  classSectionSentence,
  classProgressLine,
  nextUnverdictedIndex,
  stepIndex,
  availableBatches,
  classesInBatch,
  copyPreamble,
  isLetterToken,
  tokenSeparators,
  searchHaystack,
  searchUnits,
} from '../static/render.js';

const fixtureDir = new URL('../fixtures/', import.meta.url);
const manifest = JSON.parse(await readFile(new URL('manifest.json', fixtureDir), 'utf8'));
const allUnits = [];
for (const cls of manifest.classes) {
  for (const part of cls.shards) allUnits.push(...JSON.parse(await readFile(new URL(part, fixtureDir), 'utf8')));
}
const fixtureUnit = (id) => {
  const unit = allUnits.find((candidate) => candidate.id === id);
  assert.ok(unit, `no fixture unit ${id}`);
  return unit;
};
const explainSamples = JSON.parse(await readFile(new URL('./explain-samples.json', import.meta.url), 'utf8'));

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

test('configGateChips draws one glossed chip per gate clause, on-constraints first', () => {
  const states = (unit) => new Set((unit.config_gate ?? []).map((clause) => clause.state));
  const gated = allUnits.find((unit) => states(unit).has('on') && states(unit).has('off'));
  assert.ok(gated, 'the fixtures must exercise a config gate with both an on and an off clause');
  const chips = configGateChips(gated, manifest.feature_descriptions);
  assert.equal(chips.length, gated.config_gate.length);
  assert.equal(chips[0].state, 'on');
  assert.deepEqual(
    chips.map((chip) => chip.text),
    gated.config_gate.map((clause) => clause.text),
  );
  assert.equal(chips.map((chip) => chip.text).join(' '), gated.config_note);
  for (const chip of chips) assert.equal(chip.detail, manifest.feature_descriptions[chip.feature]);
});

test('configGateChips falls back to one unattributed chip when no conjunction pins the set', () => {
  const fallback = allUnits.find((unit) => unit.config_gate === null && unit.config_note);
  assert.ok(fallback, 'the fixtures must exercise the literal config-note fallback');
  assert.deepEqual(configGateChips(fallback, manifest.feature_descriptions), [
    { feature: null, state: null, text: fallback.config_note, detail: null },
  ]);
});

test('configGateChips draws nothing for a unit whose config set carries no information', () => {
  assert.deepEqual(configGateChips({ config_gate: null, config_note: null, configs: [] }, {}), []);
  assert.deepEqual(configGateChips({ configs: [] }, undefined), []);
});

test('every fixture gate clause resolves to a feature description', () => {
  let clauses = 0;
  for (const unit of allUnits) {
    for (const chip of configGateChips(unit, manifest.feature_descriptions)) {
      if (!chip.feature) continue;
      assert.ok(['on', 'off'].includes(chip.state));
      assert.ok(chip.detail, `${unit.id} names ${chip.feature} with no description`);
      clauses += 1;
    }
  }
  assert.ok(clauses > 0, 'the fixtures must exercise at least one attributed gate clause');
});

test('configFilterOptions offers every acceptance config in the manifest order', () => {
  const options = configFilterOptions(manifest);
  assert.deepEqual(options.map((option) => option.value), manifest.configs);
  assert.deepEqual(options.map((option) => option.label), manifest.configs);
});

test('configFilterOptions glosses each option with one line per named feature', () => {
  const byValue = new Map(configFilterOptions(manifest).map((option) => [option.value, option]));
  assert.equal(byValue.get('default').title, 'no stylistic set on — the shipping default');
  assert.equal(byValue.get('ss03').title, `ss03 — ${manifest.feature_descriptions.ss03}`);
  assert.deepEqual(byValue.get('ss03+ss05').title.split('\n'), [
    `ss03 — ${manifest.feature_descriptions.ss03}`,
    `ss05 — ${manifest.feature_descriptions.ss05}`,
  ]);
  for (const option of byValue.values()) assert.ok(option.title, `${option.value} must be self-explanatory`);
});

test('configFilterOptions falls back to the bare set name when a feature has no description', () => {
  const options = configFilterOptions({ configs: ['ss02+ss99'], feature_descriptions: { ss02: 'a gloss' } });
  assert.deepEqual(options, [{ value: 'ss02+ss99', label: 'ss02+ss99', title: 'ss02 — a gloss\nss99' }]);
});

test('configFilterOptions yields nothing for a manifest with no config list', () => {
  assert.deepEqual(configFilterOptions({}), []);
  assert.deepEqual(configFilterOptions(undefined), []);
});

test('every configFilterOptions value is a config token the unit filter understands', () => {
  const options = configFilterOptions(manifest);
  const offered = new Set(options.map((option) => option.value));
  for (const unit of allUnits) {
    for (const config of unit.configs) assert.ok(offered.has(config), `${unit.id} carries unofferable config ${config}`);
  }
  let matched = 0;
  for (const option of options) {
    for (const unit of allUnits) {
      const filters = { class: null, group: null, family: null, config: option.value, status: null };
      assert.equal(unitMatchesFilters(unit, filters, undefined), unit.configs.includes(option.value), unit.id);
      if (unit.configs.includes(option.value)) matched += 1;
    }
  }
  assert.ok(matched > 0, 'the fixtures must have units the config filter selects');
});

const plainRuns = (runs) => runs.filter((run) => run.set === null);
const setRuns = (runs) => runs.filter((run) => run.set !== null);

function assertLosslessRuns(text, label) {
  const runs = explainRuns(text);
  assert.equal(runs.map((run) => run.text).join(''), text, `${label} must survive segmentation character for character`);
  for (const [index, run] of runs.entries()) {
    assert.notEqual(run.text, '', `${label} run ${index} is empty`);
    if (run.set !== null) assert.equal(run.set, run.text, `${label} run ${index} labels a set it does not spell`);
    const previous = runs[index - 1];
    if (previous) assert.ok(previous.set !== null || run.set !== null, `${label} runs ${index - 1} and ${index} are both plain`);
  }
  return runs;
}

test('explainRuns marks a stylistic set wherever it sits in the line', () => {
  assert.deepEqual(explainRuns('ss03 unlocked it'), [
    { text: 'ss03', set: 'ss03' },
    { text: ' unlocked it', set: null },
  ]);
  assert.deepEqual(explainRuns('  note: unlocked by ss03\n  settled: qsTea.full'), [
    { text: '  note: unlocked by ', set: null },
    { text: 'ss03', set: 'ss03' },
    { text: '\n  settled: qsTea.full', set: null },
  ]);
  assert.deepEqual(explainRuns('  note: unlocked by ss02'), [
    { text: '  note: unlocked by ', set: null },
    { text: 'ss02', set: 'ss02' },
  ]);
});

test('explainRuns marks every set of a multi-set block, the header config included', () => {
  const runs = explainRuns('sequence E650:E652   config ss02+ss03\n  note: unlocked by ss03');
  assert.deepEqual(setRuns(runs).map((run) => run.set), ['ss02', 'ss03', 'ss03']);
  assert.deepEqual(plainRuns(runs).map((run) => run.text), [
    'sequence E650:E652   config ',
    '+',
    '\n  note: unlocked by ',
  ]);
});

test('explainRuns leaves a mentionless dump as one plain run, so most panels are untouched', () => {
  const dump = 'position 0: qsPea\n  decided by: only-candidate\n';
  assert.deepEqual(explainRuns(dump), [{ text: dump, set: null }]);
  assert.deepEqual(explainRuns(''), []);
  assert.deepEqual(explainRuns(null), []);
  assert.deepEqual(explainRuns(undefined), []);
});

test('explainRuns only marks a bare ssNN token, never a lookalike inside a longer word', () => {
  for (const text of ['pressed02 rows', 'ss021 is not a set', 'class02 ss3 ssNN', 'glyph_data/runes/qsMay.yaml:policy.press03']) {
    assert.deepEqual(explainRuns(text), [{ text, set: null }], text);
  }
});

test('explainRuns is lossless over synthetic dumps, including back-to-back and boundary mentions', () => {
  for (const text of [
    'ss02',
    'ss02ss03',
    'ss02 ss03',
    ' ss10 ',
    'ss02+ss03+ss05',
    'no set here at all',
    '\n\n',
    'trailing ss04\n',
  ]) {
    assertLosslessRuns(text, JSON.stringify(text));
  }
});

// explain-samples.json holds the explain text of three units of a real table-diff corpus, the one engine/test_review_diff_units.py builds from two builds of the mini fixture spec, as `diff_units._explain` writes it.
test('explainRuns is lossless over real build explain strings and finds their every stylistic set', () => {
  const seen = new Set();
  for (const sample of explainSamples) {
    const runs = assertLosslessRuns(sample.explain, `${sample.id} (${sample.class})`);
    const sets = setRuns(runs);
    for (const run of sets) seen.add(run.set);
    assert.deepEqual(sets.map((run) => run.set), sample.explain.match(/ss\d\d/gu) ?? [], `${sample.id} marked a different set list than it spells`);
  }
  assert.ok(seen.size > 1, 'the samples must exercise more than one stylistic set');
  for (const set of seen) assert.ok(manifest.feature_descriptions[set], `${set} is marked but has no gloss to hover`);
});

test('the real samples cover both places the explain names a set: the configurations line and a changed-rows heading', () => {
  const headings = (text) => [...text.matchAll(/^ {2}[\w+]+(?:, [\w+]+)*:$/gmu)].map((match) => match[0]);
  const lines = explainSamples.filter((sample) => /^ {2}configurations: .*ss\d\d/mu.test(sample.explain));
  const rows = explainSamples.filter((sample) => headings(sample.explain).some((heading) => /ss\d\d/u.test(heading)));
  const combined = explainSamples.filter((sample) => /ss\d\d\+ss\d\d/u.test(sample.explain));
  assert.ok(lines.length > 0, 'the samples must exercise a set named on the configurations line');
  assert.ok(rows.length > 0, 'the samples must exercise a set named on a changed-rows heading');
  assert.ok(combined.length > 0, 'the samples must exercise a combination of sets');
  for (const sample of [...lines, ...rows, ...combined]) {
    assert.ok(setRuns(explainRuns(sample.explain)).length > 0, `${sample.id} names a set that went unmarked`);
  }
});

test('every stylistic set the explain panel can mark has exactly one color, shared with the config chips', async () => {
  const css = await readFile(new URL('../static/app.css', import.meta.url), 'utf8');
  const colored = new Set([...css.matchAll(/\[data-ss="(ss\d{2})"\]/gu)].map((match) => match[1]));
  // README's "Stylistic sets" list is the authority the build's FEATURE_DESCRIPTIONS is checked against (test_feature_descriptions_keys_match_the_readme_stylistic_set_list), so a set added there needs a color here even before the fixture manifest glosses it.
  const readme = await readFile(new URL('../../../README.md', import.meta.url), 'utf8');
  const readmeSets = [...readme.split('## Stylistic sets')[1].split('\n## ')[0].matchAll(/^- `(ss\d{2})`:/gmu)].map((match) => match[1]);
  assert.ok(readmeSets.length > 0, 'no `ssNN` bullets found under README’s Stylistic sets heading');
  assert.deepEqual([...colored].sort(), readmeSets.sort());
  for (const set of Object.keys(manifest.feature_descriptions)) assert.ok(colored.has(set), `the fixture glosses ${set}, which has no color`);
  assert.equal(
    (css.match(/--ss-color:/gu) ?? []).length,
    colored.size,
    'the per-set color map must be declared once and shared, not copied per UI element',
  );
  for (const sample of explainSamples) {
    for (const run of setRuns(explainRuns(sample.explain))) {
      assert.ok(colored.has(run.set), `${sample.id} marks ${run.set}, which the stylesheet gives no color`);
    }
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
  for (const unit of allUnits) {
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

test('pairBand draws the judged pair from its highlight and nothing for a unit with no pair', () => {
  const human = allUnits.find((unit) => !needsNoVerdict(unit) && unit.pair !== null);
  const upem = manifest.fonts.after.upem;
  assert.deepEqual(pairBand(human, 'after', 88, upem), highlightRect(human.highlight.after, 88, upem));
  assert.deepEqual(pairBand(human, 'before', 88, upem), highlightRect(human.highlight.before, 88, upem));
  assert.equal(pairBand({ ...human, pair: null }, 'after', 88, upem), null, 'no primary pair, no band');
  const textless = allUnits.find((unit) => unit.pair === null && unit.highlight === null);
  assert.ok(textless, 'the fixtures must carry a unit shown by its row alone');
  assert.equal(pairBand(textless, 'after', 88, upem), null);
  assert.equal(pairBand(textless, 'before', 88, upem), null);
});

test('markOffset converts a boundary mark x position', () => {
  assert.equal(markOffset(0, 88, 550), 0);
  assert.equal(markOffset(137, 88, 550), 21.92);
});

test('the namer dot is written as its private-use stand-in, so Chromium shapes a sample as one run', () => {
  assert.equal(namerDotStandIn('·'), '');
  assert.equal(namerDotStandIn('· ·'), ' ');
  assert.equal(namerDotStandIn(' ‌'), ' ‌');
  assert.equal(
    namerDotStandInEntities('&#xE650;&#x00B7;&#xE66B;&#xE652;'),
    '&#xE650;&#xF8FF;&#xE66B;&#xE652;',
  );
  assert.equal(namerDotStandInEntities('&#xb7;&#x00b7;&#x0000B7;'), '&#xF8FF;&#xF8FF;&#xF8FF;');
  assert.equal(namerDotStandInEntities('&#x20B7;&#xE6B7;&#x0020;'), '&#x20B7;&#xE6B7;&#x0020;');
});

test('familiesOfGroup splits the lead pair', () => {
  assert.deepEqual(familiesOfGroup('qsTea:qsOy'), ['qsTea', 'qsOy']);
  assert.deepEqual(familiesOfGroup(null), []);
});

test('unitMatchesFilters covers class, group, family, config, and status', () => {
  const unit = fixtureUnit('u-MG7BYctpZgY');
  const empty = { class: null, group: null, family: null, config: null, status: null };
  assert.equal(unitMatchesFilters(unit, empty, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, class: 'joins' }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, class: 'settlement' }, undefined), false);
  assert.equal(unitMatchesFilters(unit, { ...empty, group: 'qsTea:qsOy' }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, family: 'qsOy' }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, family: 'qsPea' }, undefined), false);
  assert.equal(unitMatchesFilters(unit, { ...empty, config: 'ss03' }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, config: 'ss04' }, undefined), false);
  assert.equal(unitMatchesFilters(unit, { ...empty, status: 'unverdicted' }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, status: 'unverdicted' }, { verdict: 'approve' }), false);
  assert.equal(unitMatchesFilters(unit, { ...empty, status: 'verdicted' }, { verdict: 'approve' }), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, status: 'approve' }, { verdict: 'approve' }), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, status: 'reject' }, { verdict: 'approve' }), false);
});

test('orderWorklist sorts by family-pair group then id by default', () => {
  const units = [
    { id: 'u-Yt4bRkgFmpk', group: 'qsTea:qsOy' },
    { id: 'u-ejrhc5MnMoF', group: 'qsMay:qsNo' },
    { id: 'u-LK4WFNGn5Kk', group: 'qsTea:qsOy' },
  ];
  assert.deepEqual(orderWorklist(units, null).map((u) => u.id), ['u-ejrhc5MnMoF', 'u-LK4WFNGn5Kk', 'u-Yt4bRkgFmpk']);
  assert.deepEqual(units.map((u) => u.id), ['u-Yt4bRkgFmpk', 'u-ejrhc5MnMoF', 'u-LK4WFNGn5Kk'], 'the default sort must not mutate the given list');
});

test('orderWorklist preserves the given order under order=given', () => {
  const units = [
    { id: 'u-LK4WFNGn5Kk', group: 'qsTea:qsOy' },
    { id: 'u-ejrhc5MnMoF', group: 'qsMay:qsNo' },
    { id: 'u-Yt4bRkgFmpk', group: 'qsTea:qsOy' },
  ];
  assert.deepEqual(orderWorklist(units, 'given').map((u) => u.id), ['u-LK4WFNGn5Kk', 'u-ejrhc5MnMoF', 'u-Yt4bRkgFmpk']);
});

test('unitWorklist splits, trims, and drops empties', () => {
  assert.deepEqual(unitWorklist('u-1163,u-2224'), ['u-1163', 'u-2224']);
  assert.deepEqual(unitWorklist(' u-1163 , u-2224 ,'), ['u-1163', 'u-2224']);
  assert.deepEqual(unitWorklist(''), []);
  assert.deepEqual(unitWorklist(null), []);
});

test('the units worklist filter keeps only the listed ids and composes with other filters', () => {
  const unit = fixtureUnit('u-BzEBRDeYbuP');
  const empty = { class: null, group: null, family: null, config: null, status: null, units: null };
  assert.equal(unitMatchesFilters(unit, { ...empty, units: `other,${unit.id}` }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, units: 'u-9990,u-9991' }, undefined), false);
  assert.equal(unitMatchesFilters(unit, { ...empty, units: unit.id, class: 'settlement' }, undefined), false);
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
const noRecords = () => undefined;

test('partitionUnits with a units worklist narrows the human queue to the listed ids', () => {
  const wanted = allUnits.filter((unit) => !needsNoVerdict(unit)).slice(0, 2).map((unit) => unit.id);
  const { human } = partitionUnits(allUnits, { ...emptyFilters, units: wanted.join(',') }, noRecords);
  assert.deepEqual(human.map((unit) => unit.id).sort(), [...wanted].sort());
});

test('a units worklist spanning classes and batches keeps every listed unit visible, including a machine-approved one', () => {
  const machineUnit = allUnits.find((unit) => unit.ink_identical);
  const humanUnits = allUnits.filter((unit) => !needsNoVerdict(unit));
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
  const humanUnits = allUnits.filter((unit) => !needsNoVerdict(unit));
  const wanted = [humanUnits[0].id, machineUnit.id, humanUnits[humanUnits.length - 1].id];
  const filters = {
    ...emptyFilters,
    units: wanted.join(','),
    class: 'kerning',
    config: 'ss03',
    status: 'verdicted',
  };
  const { human, machine } = partitionUnits(allUnits, filters, noRecords);
  const shown = new Set([...human, ...machine].map((unit) => unit.id));
  for (const id of wanted) assert.ok(shown.has(id), `${id} must render despite conflicting class/config/status filters`);
  assert.ok(machine.some((unit) => unit.id === machineUnit.id), 'a machine-approved listed id survives a conflicting class filter');
  assert.equal(shown.size, wanted.length, 'no unlisted unit leaks into the worklist view');
});

test('units that need no verdict are hidden unless the machine toggle is on', () => {
  const off = partitionUnits(allUnits, emptyFilters, noRecords);
  assert.deepEqual(
    off.human.map((unit) => unit.id),
    allUnits.filter((unit) => !needsNoVerdict(unit)).map((unit) => unit.id),
  );
  assert.deepEqual(off.machine, []);
  const on = partitionUnits(allUnits, { ...emptyFilters, machine: '1' }, noRecords);
  assert.deepEqual(on.human.map((unit) => unit.id), off.human.map((unit) => unit.id));
  assert.deepEqual(
    on.machine.map((unit) => unit.id),
    allUnits.filter((unit) => needsNoVerdict(unit)).map((unit) => unit.id),
  );
  assert.ok(on.machine.some((unit) => unit.ink_identical), 'the fixtures must carry an ink-identical unit');
  assert.ok(on.machine.some((unit) => unit.no_verdict), 'the fixtures must carry a unit of a class that takes no verdict');
});

test('needsNoVerdict reads the machine checks and the exemption, never a batch', () => {
  for (const flag of ['ink_identical', 'picture_identical', 'no_verdict']) {
    assert.equal(needsNoVerdict({ [flag]: true }), true, flag);
    assert.equal(needsNoVerdict({ [flag]: false }), false, flag);
  }
  assert.equal(needsNoVerdict({ batch: null }), false, 'a fragment carries no batch, so one says nothing');
  assert.equal(needsNoVerdict({ order: 0, batch: 0 }), false, 'an app-index row carries no flags and is human');
  assert.equal(needsNoVerdict({}), false, 'a unit without any flag stays human');
  assert.equal(needsNoVerdict(null), false);
});

test('triageOrder reads a row\'s place in the manifest index and puts a record without one last', () => {
  assert.equal(triageOrder({ order: 0 }), 0);
  assert.equal(triageOrder({ order: 41 }), 41);
  assert.equal(triageOrder({}), Number.POSITIVE_INFINITY);
  assert.equal(triageOrder(null), Number.POSITIVE_INFINITY);
  const rows = [{ id: 'b', order: 2, group: 'x' }, { id: 'a', group: 'x' }, { id: 'c', order: 0, group: 'y' }];
  assert.deepEqual(orderWorklist(rows, null).map((row) => row.id), ['c', 'b', 'a']);
  assert.deepEqual(orderWorklist(rows, 'given').map((row) => row.id), ['b', 'a', 'c']);
});

// App index rows leave out the machine-check flags and `no_verdict`, because only human units have a row (app_index.app_row). These tests check that the readers treat a row without the flags the same as a shard record with them false.
const slimRow = (unit) => {
  const row = { ...unit };
  for (const field of ['ink_identical', 'picture_identical', 'no_verdict', 'explain', 'provenance']) {
    delete row[field];
  }
  return row;
};

test('partitionUnits over slim rows queues exactly the units it queues over whole records', () => {
  const human = allUnits.filter((unit) => !needsNoVerdict(unit));
  const overRows = partitionUnits(human.map(slimRow), { ...emptyFilters, machine: '1' }, noRecords);
  const overRecords = partitionUnits(human, { ...emptyFilters, machine: '1' }, noRecords);
  assert.deepEqual(overRows.human.map((unit) => unit.id), overRecords.human.map((unit) => unit.id));
  assert.deepEqual(overRows.machine, []);
  assert.deepEqual(overRecords.machine, []);
});

test('a no-verdict unit leaves the human queue and appears with the machine toggle', () => {
  const exemptUnit = { ...allUnits.find((unit) => !unit.ink_identical), id: 'u-8888', no_verdict: true };
  const units = [...allUnits, exemptUnit];
  const off = partitionUnits(units, emptyFilters, noRecords);
  assert.ok(!off.human.some((unit) => unit.id === 'u-8888'));
  assert.deepEqual(off.machine, []);
  const on = partitionUnits(units, { ...emptyFilters, machine: '1' }, noRecords);
  assert.ok(!on.human.some((unit) => unit.id === 'u-8888'));
  assert.ok(on.machine.some((unit) => unit.id === 'u-8888'));
});

test('a picture-identical unit leaves the human queue and appears with the machine toggle', () => {
  const pictureUnit = { ...allUnits.find((unit) => !unit.ink_identical), id: 'u-7777', picture_identical: true };
  const units = [...allUnits, pictureUnit];
  const off = partitionUnits(units, emptyFilters, noRecords);
  assert.ok(!off.human.some((unit) => unit.id === 'u-7777'));
  assert.deepEqual(off.machine, []);
  const on = partitionUnits(units, { ...emptyFilters, machine: '1' }, noRecords);
  assert.ok(!on.human.some((unit) => unit.id === 'u-7777'));
  assert.ok(on.machine.some((unit) => unit.id === 'u-7777'));
});

test('class and family filters apply to machine units; the status filter does not', () => {
  const machineUnit = allUnits.find((unit) => unit.ink_identical);
  const filters = { ...emptyFilters, machine: '1', status: 'unverdicted' };
  const partitioned = partitionUnits(allUnits, filters, noRecords);
  assert.ok(partitioned.machine.some((unit) => unit.id === machineUnit.id));
  const wrongClass = partitionUnits(allUnits, { ...filters, class: 'joins' }, noRecords);
  assert.deepEqual(wrongClass.machine, []);
});

test('human and machine counts come from the manifest class metadata', () => {
  const settlement = manifest.classes.find((cls) => cls.id === 'settlement');
  const joins = manifest.classes.find((cls) => cls.id === 'joins');
  assert.equal(humanClassCount(settlement), settlement.unit_count - 1);
  assert.equal(humanClassCount(joins), joins.unit_count);
  assert.equal(humanTotal(manifest), manifest.human_unit_ids.length);
  assert.equal(noVerdictTotal(manifest), 2, 'the Kerning and Provenance only units');
});

test('machineFoldTotal counts what a class fold will hold before the class is fetched', () => {
  const byId = (id) => manifest.classes.find((cls) => cls.id === id);
  assert.equal(machineFoldTotal(byId('settlement')), 1);
  assert.equal(machineFoldTotal(byId('joins')), 0, 'a class with nothing machine-approved folds nothing');
  assert.equal(machineFoldTotal(byId('kerning')), 1, 'a class that takes no verdict folds every unit');
  assert.equal(machineFoldTotal({ no_verdict: true, unit_count: 6344, machine_approved_count: 4465 }), 6344);
  assert.equal(machineFoldTotal({ no_verdict: false, unit_count: 9 }), 0, 'a countless class folds nothing');
});

test('machineFoldCheck picks the narrowest check that accounts for every machine unit', () => {
  const cls = (over) => ({ no_verdict: false, unit_count: 10, machine_approved_count: 10, ...over });
  assert.equal(machineFoldCheck(cls({ machine_checks: { ink_identical: 10, picture_identical: 0 } })), 'ink_identical');
  assert.equal(machineFoldCheck(cls({ machine_checks: { ink_identical: 6, picture_identical: 4 } })), 'picture_identical');
  assert.equal(
    machineFoldCheck({
      no_verdict: true,
      unit_count: 10,
      machine_approved_count: 4,
      machine_checks: { ink_identical: 4, picture_identical: 0 },
    }),
    'no_verdict',
    'a no-verdict class folds its exempt units too, so no machine check accounts for all of them',
  );
});

test('machineFoldCheck falls back to the no-verdict badge for a class carrying no check split', () => {
  assert.equal(machineFoldCheck({ no_verdict: false, unit_count: 10, machine_approved_count: 10 }), 'no_verdict');
  assert.equal(machineFoldCheck({ no_verdict: false, unit_count: 10, machine_approved_count: 0 }), 'no_verdict');
  assert.equal(machineFoldCheck(manifest.classes.find((cls) => cls.id === 'settlement')), 'ink_identical');
});

test('a no-verdict class contributes nothing to the human units and everything non-identical to the exempt total', () => {
  const exemptClass = { id: 'kerning', no_verdict: true, unit_count: 6344, machine_approved_count: 4465 };
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

test('machineChecks splits the machine-approved total and treats a check-less manifest as all ink-identical', () => {
  assert.deepEqual(machineChecks(manifest), { units: 1, inkIdentical: 1, pictureIdentical: 0 });
  assert.deepEqual(machineChecks({ machine_approved: { units: 1 } }), { units: 1, inkIdentical: 1, pictureIdentical: 0 });
  const splitManifest = {
    machine_approved: {
      units: 11926,
      checks: { ink_identical: { units: 8350 }, picture_identical: { units: 3576 } },
    },
  };
  assert.deepEqual(machineChecks(splitManifest), { units: 11926, inkIdentical: 8350, pictureIdentical: 3576 });
  assert.deepEqual(machineChecks({}), { units: 0, inkIdentical: 0, pictureIdentical: 0 });
});

test('the collapsed chip carries the corpus total and the popover breaks it down to the human units', () => {
  assert.equal(corpusChipLabel(manifest), '9 units');
  assert.deepEqual(
    corpusDetailRows(manifest).map((row) => [row.label, row.value]),
    [
      ['Corpus', '9'],
      ['ink-identical machine-approved', '1'],
      ['no verdict needed', '2'],
      ['for human review', '6'],
    ],
  );
  const splitManifest = {
    totals: { units: 15960 },
    machine_approved: {
      units: 11926,
      method: 'Shaped in both fonts and compared.',
      checks: { ink_identical: { units: 8350 }, picture_identical: { units: 3576 } },
    },
    classes: [
      { id: 'kerning', no_verdict: true, unit_count: 6256, machine_approved_count: 4940 },
      { id: 'x', no_verdict: false, unit_count: 9704, machine_approved_count: 6986 },
    ],
  };
  assert.equal(corpusChipLabel(splitManifest), '15,960 units');
  assert.deepEqual(
    corpusDetailRows(splitManifest).map((row) => [row.label, row.value, row.sub ?? false]),
    [
      ['Corpus', '15,960', false],
      ['machine-approved', '11,926', false],
      ['ink-identical', '8,350', true],
      ['picture-identical', '3,576', true],
      ['no verdict needed', '1,316', false],
      ['for human review', '2,718', false],
    ],
  );
  const rows = corpusDetailRows({ totals: { units: 4 }, machine_approved: { units: 0 }, classes: [] });
  assert.deepEqual(
    rows.map((row) => row.label),
    ['Corpus', 'for human review'],
  );
});

test('a lone machine check merges back into the total row', () => {
  const pictureOnly = {
    totals: { units: 15960 },
    classes: [
      { id: 'kerning', no_verdict: true, unit_count: 6256, machine_approved_count: 4940 },
      { id: 'x', no_verdict: false, unit_count: 9704, machine_approved_count: 6986 },
    ],
    machine_approved: {
      units: 3,
      method: 'Rasterized in both fonts and compared cell by cell.',
      checks: { ink_identical: { units: 0 }, picture_identical: { units: 3 } },
    },
  };
  assert.deepEqual(
    corpusDetailRows(pictureOnly).map((row) => [row.label, row.value, row.sub ?? false]),
    [
      ['Corpus', '15,960', false],
      ['picture-identical machine-approved', '3', false],
      ['no verdict needed', '1,316', false],
      ['for human review', '2,718', false],
    ],
    'a check with no units gets no row, so the one check left names the total',
  );
});

test('the popover corner says how much of the alphabet has migrated', () => {
  assert.equal(corpusAlphabetLabel({ alphabet: { migrated: 16, total: 44 } }), '16 of 44 letters');
  assert.equal(corpusAlphabetLabel({}), null);
});

test('the popover stamp names the generation and the head it was generated at', () => {
  assert.equal(
    corpusStampLine({ generated_at: '2026-06-10T17:02:11Z', repo_head: 'abc1234' }),
    'Generated 2026-06-10T17:02:11Z at abc1234',
  );
  assert.equal(corpusStampLine({ generated_at: '2026-06-10T17:02:11Z' }), 'Generated 2026-06-10T17:02:11Z');
  assert.equal(corpusStampLine({}), null);
});

test('the machine-approved tooltip leads with the check split, then the verification method', () => {
  const splitManifest = {
    machine_approved: {
      units: 11926,
      method: 'Shaped in both fonts and compared.',
      checks: { ink_identical: { units: 8350 }, picture_identical: { units: 3576 } },
    },
  };
  assert.equal(
    machineTitle(splitManifest),
    '8,350 ink-identical + 3,576 picture-identical. Shaped in both fonts and compared.',
  );
  const methodless = { machine_approved: { units: 11926, checks: splitManifest.machine_approved.checks } };
  assert.equal(machineTitle(methodless), '8,350 ink-identical + 3,576 picture-identical.');
  const pictureOnly = {
    machine_approved: {
      units: 3,
      method: 'Rasterized in both fonts and compared cell by cell.',
      checks: { ink_identical: { units: 0 }, picture_identical: { units: 3 } },
    },
  };
  assert.equal(machineTitle(pictureOnly), pictureOnly.machine_approved.method, 'a lone check needs no split line');
  assert.equal(machineTitle(manifest), manifest.machine_approved.method);
  assert.equal(machineTitle({}), '');
});

test('classCountsLine counts an Open questions entry to review, done, and machine-approved, dropping zero parts', () => {
  const open = { section: 'open-questions', no_verdict: false, unit_count: 3361, machine_approved_count: 3344 };
  assert.equal(classCountsLine(open, null), '17 to review · 3,344 machine-approved', 'before the index loads nothing counts as done');
  assert.equal(classCountsLine(open, { done: 12, skipped: 0, rejected: 1 }), '5 to review · 12 done · 3,344 machine-approved');
  const plain = { section: 'open-questions', no_verdict: false, unit_count: 166, machine_approved_count: 0 };
  assert.equal(classCountsLine(plain, { done: 0, skipped: 0, rejected: 0 }), '166 to review');
});

test('classCountsLine keeps skipped units to review, as the Queue does, and says how many', () => {
  const open = { section: 'open-questions', no_verdict: false, unit_count: 10, machine_approved_count: 0 };
  assert.equal(classCountsLine(open, { done: 6, skipped: 3, rejected: 0 }), '4 to review (3 skipped) · 6 done');
  assert.equal(classCountsLine(open, { done: 7, skipped: 3, rejected: 0 }), '3 to review (3 skipped) · 7 done', 'a class whose last units are skipped is not done');
});

test('classCountsLine reads "all N done" once nothing is left, and says why a class with no human units needs none', () => {
  const finished = { section: 'open-questions', no_verdict: false, unit_count: 5555, machine_approved_count: 4528 };
  assert.equal(classCountsLine(finished, { done: 1027, skipped: 0, rejected: 0 }), 'all 1,027 done');
  assert.equal(classIsDone(finished, { done: 1027, skipped: 0, rejected: 0 }), true);
  assert.equal(classIsDone(finished, null), false, 'nothing is done before the index loads');
  const exempt = { section: 'no-review-needed', no_verdict: true, unit_count: 6256, machine_approved_count: 4940 };
  assert.equal(classCountsLine(exempt, null), '6,256 units · no verdict needed');
  const allMachine = { section: 'no-review-needed', no_verdict: false, unit_count: 1722, machine_approved_count: 1722 };
  assert.equal(classCountsLine(allMachine, null), '1,722 units · all machine-approved');
  assert.equal(classIsDone(allMachine, { done: 0, skipped: 0, rejected: 0 }), false, 'a class with nothing to judge is never dimmed as done');
});

test('classVerdictTallies counts done, skipped, and rejected units per class in one pass', () => {
  const rows = [
    { id: 'u-1', class: 'a' },
    { id: 'u-2', class: 'a' },
    { id: 'u-3', class: 'a' },
    { id: 'u-4', class: 'b' },
    { id: 'u-5', class: 'b' },
  ];
  const records = { 'u-1': { verdict: 'approve' }, 'u-2': { verdict: 'reject' }, 'u-3': { verdict: 'skip' }, 'u-5': { verdict: 'either' } };
  const tallies = classVerdictTallies(rows, (id) => records[id]);
  assert.deepEqual(tallies.get('a'), { done: 2, skipped: 1, rejected: 1 });
  assert.deepEqual(tallies.get('b'), { done: 1, skipped: 0, rejected: 0 });
  assert.equal(classVerdictTallies(rows, () => undefined).size, 0);
  assert.equal(classLeft({ no_verdict: false, unit_count: 3, machine_approved_count: 0 }, tallies.get('a')), 1);
});

test("a row's date slot names the day its verdict was recorded", () => {
  assert.equal(verdictAtText({ verdict: 'approve', note: '', at: '2026-11-06T09:15:02.417Z' }), 'verdicted 2026-11-06');
  assert.equal(verdictAtText(undefined), '');
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
  assert.deepEqual(availableBatches(manifest, 'joins'), [0]);
  assert.deepEqual(availableBatches(manifest, 'glyphs'), [0, 1]);
  assert.deepEqual(availableBatches(manifest, 'kerning'), []);
  assert.deepEqual(availableBatches(manifest, 'nonexistent'), []);
});

test('classesInBatch names the classes with units in a batch, batchless classes included with batch 0', () => {
  assert.deepEqual(
    [...classesInBatch(manifest, 0)].sort(),
    ['glyphs', 'joins', 'kerning', 'provenance-only', 'settlement'],
  );
  assert.deepEqual([...classesInBatch(manifest, 1)], ['glyphs']);
  assert.deepEqual(
    [...classesInBatch(manifest, 0, false)].sort(),
    ['glyphs', 'joins', 'settlement'],
    'with machine rows hidden a batchless class contributes nothing to batch 0',
  );
});

test('copyPreamble names only the unit, codepoints, and notation — the rest is looked up from the shards', () => {
  const text = copyPreamble(fixtureUnit('u-BzEBRDeYbuP'));
  assert.match(text, /engine\/out\/review\/ unit u-BzEBRDeYbuP/);
  assert.match(text, /E653:E67A:E652/);
  assert.match(text, /·Day·Utter·Tea/);
  assert.doesNotMatch(text, /joins/);
  assert.doesNotMatch(text, /ss04/);
  assert.match(copyPreamble(fixtureUnit('u-AZd95bckcbg')), /u-AZd95bckcbg — no text \(join qsPea/);
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
  for (const unit of allUnits) {
    const tokens = unit.notation_tokens;
    if (unit.codepoints === null) {
      assert.equal(tokens, null, `${unit.id} has no text, so no tokens`);
      continue;
    }
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

test('tokenMarkRuns marks the pair stretch, sharing a separator only between two marked tokens', () => {
  const runs = tokenMarkRuns(['E666', 'E670', 'E665', 'E652'], ['', ':', ':', ':'], [1, 2]);
  assert.deepEqual(runs, [
    { text: 'E666:', pair: false },
    { text: 'E670:E665', pair: true },
    { text: ':E652', pair: false },
  ]);
  assert.equal(runs.map((run) => run.text).join(''), 'E666:E670:E665:E652');
  assert.deepEqual(tokenMarkRuns(['·No', '·It', '·May', '·Tea'], ['', '', '', ''], [0, 1]), [
    { text: '·No·It', pair: true },
    { text: '·May·Tea', pair: false },
  ]);
  assert.deepEqual(tokenMarkRuns(['◊ZWNJ', '·Tea', '·Oy'], ['', ' ', ''], [1, 2]), [
    { text: '◊ZWNJ ', pair: false },
    { text: '·Tea·Oy', pair: true },
  ]);
  assert.deepEqual(tokenMarkRuns(['·Tea'], [''], null), [{ text: '·Tea', pair: false }]);
});

test('fixture units satisfy the contract fields the frontend relies on', () => {
  for (const unit of allUnits) {
    assert.match(unit.id, /^u-[1-9A-HJ-NP-Za-km-z]{11}$/);
    assert.equal(typeof unit.ink_identical, 'boolean');
    assert.equal(typeof unit.picture_identical, 'boolean');
    assert.equal(typeof unit.no_verdict, 'boolean');
    assert.equal(typeof unit.kerning, 'boolean');
    assert.equal('batch' in unit, false, 'a fragment carries no batch; the manifest index does');
    assert.ok(unit.text_entities === null || typeof unit.text_entities === 'string', unit.id);
    if (unit.text_entities !== null) assert.doesNotMatch(unit.text_entities, /[\u200C\uE650-\uE67E]/);
    assert.ok(Array.isArray(unit.configs) && unit.configs.length >= 1);
    assert.ok(unit.config_note === null || (typeof unit.config_note === 'string' && unit.config_note.length > 0));
    assert.ok(unit.config_gate === null || (Array.isArray(unit.config_gate) && unit.config_gate.length > 0));
    for (const clause of unit.config_gate ?? []) {
      assert.equal(typeof clause.feature, 'string');
      assert.ok(['on', 'off'].includes(clause.state));
      assert.ok(typeof clause.text === 'string' && clause.text.length > 0);
    }
    if (unit.config_gate) {
      assert.equal(unit.config_gate.map((clause) => clause.text).join(' '), unit.config_note);
    }
    assert.ok(Array.isArray(unit.render_groups) && unit.render_groups.length >= 1);
    assert.ok(typeof unit.summary === 'string' && unit.summary.length > 0);
    assert.equal(typeof unit.explain, 'string');
    if (unit.pair !== null) {
      assert.ok(unit.highlight.before.x_max > unit.highlight.before.x_min);
      assert.ok(unit.highlight.after.x_max > unit.highlight.after.x_min);
    }
    for (const mark of unit.boundary_marks) {
      assert.equal(typeof mark.x, 'number');
      assert.ok(['zwnj', 'space', 'namer-dot'].includes(mark.kind));
    }
  }
});

test('searchHaystack folds id, notation, codepoints, class, group, cluster, and kinds into one lowercase string', () => {
  const unit = fixtureUnit('u-MG7BYctpZgY');
  const haystack = searchHaystack(unit);
  assert.notEqual(unit.id.toLowerCase(), unit.id);
  assert.ok(haystack.includes(unit.id.toLowerCase()), 'the id, folded to lowercase like everything else');
  assert.ok(haystack.includes('·tea·oy'));
  assert.ok(haystack.includes('teaoy'), 'notation with the namer dots stripped is searchable');
  assert.ok(haystack.includes('200c:e652:e679'));
  assert.ok(haystack.includes('200ce652e679'), 'codepoints with the colons stripped are searchable');
  assert.ok(haystack.includes('joins'));
  assert.ok(haystack.includes('qstea:qsoy'));
  assert.ok(haystack.includes('added'));
  assert.ok(haystack.includes(unit.cluster), 'the cluster signature id is searchable');
});

test('searchHaystack is memoized per unit, so a keystroke folds each row at most once', () => {
  // Rows are immutable once parsed; mutating one here is only a probe for whether the second call refolded it.
  const unit = { ...fixtureUnit('u-BzEBRDeYbuP') };
  const first = searchHaystack(unit);
  unit.notation = '·Nope';
  assert.equal(searchHaystack(unit), first, 'the second call is answered from the cache');
  const twin = { ...fixtureUnit('u-BzEBRDeYbuP'), notation: '·Nope' };
  assert.notEqual(searchHaystack(twin), first, 'a row the cache has not seen folds on its own');
  assert.ok(searchHaystack(twin).includes('·nope'));
});

test('searchUnits finds a unit by its cluster id', () => {
  assert.deepEqual(searchUnits(allUnits, 'c-5e0d1a22').matches.map((unit) => unit.id), ['u-MG7BYctpZgY']);
});

test('searchUnits finds a unit by its exact id across every shard', () => {
  const { matches, total } = searchUnits(allUnits, 'u-LtadFU2dmvz');
  assert.equal(total, 1);
  assert.equal(matches[0].id, 'u-LtadFU2dmvz');
});

test('searchUnits matches notation with and without the namer dots, case-insensitively', () => {
  assert.deepEqual(
    searchUnits(allUnits, '·Pea·May').matches.map((unit) => unit.id),
    ['u-i35WcnXw8nb'],
  );
  assert.deepEqual(
    searchUnits(allUnits, 'peamay').matches.map((unit) => unit.id),
    ['u-i35WcnXw8nb'],
  );
});

test('searchUnits matches codepoints with and without the colons', () => {
  assert.deepEqual(searchUnits(allUnits, 'E679').matches.map((unit) => unit.id), ['u-MG7BYctpZgY']);
  assert.deepEqual(searchUnits(allUnits, 'e652e679').matches.map((unit) => unit.id), ['u-MG7BYctpZgY']);
});

test('searchUnits matches class, group, and kind, and includes machine-approved units', () => {
  const byClass = searchUnits(allUnits, 'settlement');
  assert.deepEqual(byClass.matches.map((unit) => unit.id).sort(), ['u-MZiMjJpq1Tt', 'u-i35WcnXw8nb']);
  assert.deepEqual(searchUnits(allUnits, 'qsTea:qsOy').matches.map((unit) => unit.id), ['u-MG7BYctpZgY']);
  const removed = searchUnits(allUnits, 'removed');
  assert.deepEqual(removed.matches.map((unit) => unit.id).sort(), ['u-AZd95bckcbg', 'u-BzEBRDeYbuP']);
  const changed = searchUnits(allUnits, 'changed');
  assert.equal(changed.matches.find((unit) => unit.id === 'u-i35WcnXw8nb').ink_identical, true, 'a machine-approved unit is still findable');
});

test('searchUnits matches a kind of change by its title', () => {
  const titleOf = (id) => (id === 'glyphs' ? 'Redrawn letters' : id);
  const fresh = allUnits.map((unit) => ({ ...unit }));
  assert.deepEqual(
    searchUnits(fresh, 'redrawn', 50, titleOf).matches.map((unit) => unit.id).sort(),
    ['u-51MZrbcA5zc', 'u-LtadFU2dmvz'],
  );
  const untitled = allUnits.map((unit) => ({ ...unit }));
  assert.equal(searchUnits(untitled, 'redrawn').total, 0, 'only the title says "redrawn"');
});

test('searchUnits requires every whitespace-separated token to match (AND)', () => {
  assert.deepEqual(searchUnits(allUnits, 'tea 200c').matches.map((unit) => unit.id).sort(), ['u-MG7BYctpZgY', 'u-Qn291AdDMtp']);
  assert.deepEqual(searchUnits(allUnits, 'tea 200c e679').matches.map((unit) => unit.id), ['u-MG7BYctpZgY']);
  assert.equal(searchUnits(allUnits, 'tea low').total, 0);
});

test('searchUnits ranks an exact id hit ahead of incidental substring hits', () => {
  // Only one fixture unit contains this id. searchScore ranks an exact id match first, so it would stay first even if another unit's haystack contained the id.
  const { matches } = searchUnits(allUnits, 'u-BzEBRDeYbuP');
  assert.equal(matches[0].id, 'u-BzEBRDeYbuP');
});

test('searchUnits caps the matches at the limit but reports the true total', () => {
  const { matches, total } = searchUnits(allUnits, 'u-', 2);
  assert.equal(total, allUnits.length, 'every fixture unit id starts with u-');
  assert.equal(matches.length, 2);
});

test('searchUnits returns nothing for a blank query', () => {
  assert.deepEqual(searchUnits(allUnits, '   '), { matches: [], total: 0 });
});

test('manifestRefusal refuses a manifest without sections, which would otherwise leave the Queue empty', () => {
  assert.equal(manifestRefusal(manifest), null);
  const unsectioned = { ...manifest, classes: manifest.classes.map(({ section, ...rest }) => rest) };
  assert.equal(manifestRefusal(unsectioned), PREDATES_APP);
  assert.equal(manifestRefusal({ classes: [{ ...manifest.classes[0], section: 'ruled' }] }), PREDATES_APP);
  assert.equal(manifestRefusal({ classes: [] }), null);
  assert.equal(PREDATES_APP, 'This corpus predates the app; run the review cycle.');
});

test('classChip names a class by its title, with its id and plain-text summary to hover', () => {
  const info = classInfo(manifest);
  assert.deepEqual(classChip('joins', info), {
    text: 'Joins',
    title: `joins — ${info.get('joins').summary}`,
  });
  assert.deepEqual(classChip('no-such-class', info), { text: 'no-such-class', title: 'no-such-class' });
});

function fakeDocument() {
  const element = (tagName) => ({
    tagName,
    textContent: '',
    children: [],
    append(...nodes) {
      this.children.push(...nodes);
    },
  });
  return {
    createElement: (tag) => element(tag.toUpperCase()),
    createTextNode: (text) => ({ nodeValue: text }),
    root: () => element('SPAN'),
  };
}

test('appendCodeRuns builds each backtick span as a code element and the rest as text, never as markup', () => {
  const doc = fakeDocument();
  const target = appendCodeRuns(doc.root(), 'Letters still join, but lower: `·Pea.half ~x~ ·No` becomes `·Pea ~b~ ·No`, <b>likewise</b>.', doc);
  assert.deepEqual(
    target.children.map((node) => (node.tagName ? [node.tagName, node.textContent] : node.nodeValue)),
    [
      'Letters still join, but lower: ',
      ['CODE', '·Pea.half ~x~ ·No'],
      ' becomes ',
      ['CODE', '·Pea ~b~ ·No'],
      ', <b>likewise</b>.',
    ],
  );
});

test('codeRuns keeps the copy whole, leaves an unpaired backtick as text, and yields nothing for no copy', () => {
  const text = 'A `·Way ~b~ ·May` join and a stray ` here';
  const runs = codeRuns(text);
  assert.deepEqual(runs.map((run) => run.code), [false, true, false]);
  assert.equal(runs[2].text, ' join and a stray ` here');
  assert.deepEqual(codeRuns('`·Zoo | ·Utter ~b~ ·Ooze`'), [{ text: '·Zoo | ·Utter ~b~ ·Ooze', code: true }]);
  assert.deepEqual(codeRuns(''), []);
  assert.deepEqual(codeRuns(null), []);
});

test('rationaleParagraphs splits a why into its non-blank lines', () => {
  assert.deepEqual(rationaleParagraphs('First paragraph.\n\n  Second, with `code`.\n'), ['First paragraph.', 'Second, with `code`.']);
  assert.deepEqual(rationaleParagraphs(''), []);
  assert.deepEqual(rationaleParagraphs(undefined), []);
});

test('sidebarSections sorts the classes into the two sections in manifest order', () => {
  const sections = sidebarSections(manifest);
  assert.deepEqual(sections.map((section) => section.id), SECTION_ORDER);
  assert.deepEqual(sections.map((section) => section.title), ['Open questions', 'No review needed']);
  assert.deepEqual(sections.map((section) => section.open), [true, false]);
  assert.deepEqual(sections[0].entries.map((cls) => cls.id), ['joins', 'settlement', 'glyphs']);
  assert.deepEqual(sections[1].entries.map((cls) => cls.id), ['kerning', 'provenance-only']);
  for (const id of SECTION_ORDER) assert.ok(SECTION_LABELS[id].legend, id);
});

test('sectionTotalsLine counts a section\'s entries and what is left in it', () => {
  const open = [
    { id: 'a', section: 'open-questions', no_verdict: false, unit_count: 10, machine_approved_count: 2 },
    { id: 'b', section: 'open-questions', no_verdict: false, unit_count: 5, machine_approved_count: 0 },
  ];
  assert.equal(sectionTotalsLine('open-questions', open, null), '2 · 13 to review');
  assert.equal(sectionTotalsLine('open-questions', open, new Map([['a', { done: 8, skipped: 1, rejected: 0 }]])), '2 · 5 to review');
  const none = [
    { id: 'd', section: 'no-review-needed', no_verdict: true, unit_count: 233079, machine_approved_count: 199141 },
    { id: 'e', section: 'no-review-needed', no_verdict: false, unit_count: 3149, machine_approved_count: 3149 },
  ];
  assert.equal(sectionTotalsLine('no-review-needed', none, null), '2 · 236,228 units');
});

test('the class header says what each section asks, and the progress strip counts the class by its title', () => {
  const open = manifest.classes.find((cls) => cls.id === 'joins');
  assert.equal(
    classSectionSentence(open),
    'Judge each unit; units you have already judged count as done. The Queue draws from here.',
  );
  assert.equal(
    classSectionSentence({ section: 'no-review-needed', unit_count: 3904, no_verdict: true }),
    'Nothing to judge: the class takes no verdict, or the machine approves all 3,904 units.',
  );
  assert.equal(
    classSectionSentence(manifest.classes.find((cls) => cls.id === 'kerning')),
    'Nothing to judge: the class takes no verdict, or the machine approves its one unit.',
  );
  assert.equal(classProgressLine(open, { done: 1, skipped: 0, rejected: 0 }), '“Joins”: 2 to review of 3');
});

