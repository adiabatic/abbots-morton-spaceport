import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {
  featureSettingsValue,
  configGateChips,
  configFilterOptions,
  pinStylisticSetScope,
  explainRuns,
  renderGroupsOf,
  highlightRect,
  pairBand,
  markOffset,
  secondaryJunctionsOf,
  junctionChip,
  cellCodepointSpans,
  onlyHereJunctionSpans,
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
  STATUS_LABELS,
  SECTION_ORDER,
  SECTION_LABELS,
  PREDATES_APP,
  manifestRefusal,
  classInfo,
  pillStatus,
  pillText,
  classChip,
  codeRuns,
  appendCodeRuns,
  rationaleParagraphs,
  sidebarSections,
  sectionTotalsLine,
  classSectionSentence,
  classProgressLine,
  configSetPhrase,
  configClassBadge,
  UNMATCHED_CLASS,
  nextUnverdictedIndex,
  stepIndex,
  availableBatches,
  classesInBatch,
  copyPreamble,
  isLetterToken,
  tokenSeparators,
  searchHaystack,
  searchUnits,
  duplicateGroupOfQuery,
  duplicateChip,
  duplicateFillTargets,
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
  assert.deepEqual(byValue.get('ss02+ss03+ss05').title.split('\n'), [
    `ss02 — ${manifest.feature_descriptions.ss02}`,
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

test('pinStylisticSetScope names the sets in ssNN notation and echoes the attribute the pin will be written as', () => {
  const scope = pinStylisticSetScope('03', manifest.feature_descriptions);
  assert.deepEqual(scope.sets, ['ss03']);
  assert.equal(scope.label, 'ss03');
  assert.equal(scope.attribute, 'data-stylistic-set="03"');
  assert.ok(scope.title.includes(`ss03 — ${manifest.feature_descriptions.ss03}`));
});

test('pinStylisticSetScope keeps a multi-set value in the attribute order the corpus cell needs', () => {
  const scope = pinStylisticSetScope('02 10', manifest.feature_descriptions);
  assert.deepEqual(scope.sets, ['ss02', 'ss10']);
  assert.equal(scope.label, 'ss02+ss10');
  assert.equal(scope.attribute, 'data-stylistic-set="02 10"');
  assert.deepEqual(scope.title.split('\n').slice(1), [
    `ss02 — ${manifest.feature_descriptions.ss02}`,
    `ss10 — ${manifest.feature_descriptions.ss10}`,
  ]);
});

test('pinStylisticSetScope yields nothing for an unscoped pin, so its draft line is untouched', () => {
  assert.equal(pinStylisticSetScope(null, manifest.feature_descriptions), null);
  assert.equal(pinStylisticSetScope(undefined, manifest.feature_descriptions), null);
  assert.equal(pinStylisticSetScope('', manifest.feature_descriptions), null);
  assert.equal(pinStylisticSetScope('   ', manifest.feature_descriptions), null);
});

test('pinStylisticSetScope zero-pads a bare set number and falls back to the bare name with no gloss', () => {
  const scope = pinStylisticSetScope('2 99', { ss02: 'a gloss' });
  assert.deepEqual(scope.sets, ['ss02', 'ss99']);
  assert.equal(scope.attribute, 'data-stylistic-set="02 99"');
  assert.deepEqual(scope.title.split('\n').slice(1), ['ss02 — a gloss', 'ss99']);
});

test('every fixture pin resolves its scope to sets the unit actually diverges under', () => {
  let scoped = 0;
  let unscoped = 0;
  for (const unit of allUnits) {
    const pin = unit.drafts?.pin;
    if (!pin) continue;
    const scope = pinStylisticSetScope(pin.stylistic_set, manifest.feature_descriptions);
    if (!scope) {
      assert.ok(unit.configs.includes('default'), `${unit.id} pins nothing but does not hold by default`);
      unscoped += 1;
      continue;
    }
    assert.equal(scope.attribute, `data-stylistic-set="${pin.stylistic_set}"`);
    const named = new Set(unit.configs.flatMap((config) => config.split('+')));
    for (const set of scope.sets) {
      assert.ok(named.has(set), `${unit.id} pins ${set}, which no config of the unit turns on`);
      assert.ok(manifest.feature_descriptions[set], `${unit.id} pins ${set} with no description`);
    }
    scoped += 1;
  }
  assert.ok(scoped > 0, 'the fixtures must exercise a set-scoped pin');
  assert.ok(unscoped > 0, 'the fixtures must exercise an unscoped pin');
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

test('explainRuns is lossless over real build explain strings and finds their every stylistic set', () => {
  const seen = new Set();
  let mentionless = 0;
  for (const sample of explainSamples) {
    const runs = assertLosslessRuns(sample.explain, `${sample.id} (${sample.shard})`);
    const sets = setRuns(runs);
    if (sets.length === 0) mentionless += 1;
    for (const run of sets) seen.add(run.set);
    assert.deepEqual(sets.map((run) => run.set), sample.explain.match(/ss\d\d/gu) ?? [], `${sample.id} marked a different set list than it spells`);
  }
  assert.ok(mentionless > 0, 'the samples must include a dump with no stylistic set at all');
  assert.ok(seen.size > 1, 'the samples must exercise more than one stylistic set');
  for (const set of seen) assert.ok(manifest.feature_descriptions[set], `${set} is marked but has no gloss to hover`);
});

test('the real samples cover both places the engine names a set: the header config and an unlock note', () => {
  const headers = explainSamples.filter((sample) => /^sequence .* config ss\d\d/mu.test(sample.explain));
  const notes = explainSamples.filter((sample) => /^ {2}note: unlocked by ss\d\d$/mu.test(sample.explain));
  assert.ok(headers.length > 0, 'the samples must exercise a set named on the header line');
  assert.ok(notes.length > 0, 'the samples must exercise an "unlocked by" note');
  for (const sample of [...headers, ...notes]) {
    assert.ok(setRuns(explainRuns(sample.explain)).length > 0, `${sample.id} names a set that went unmarked`);
  }
});

test('every stylistic set the explain panel can mark has exactly one color, shared with the config chips', async () => {
  const css = await readFile(new URL('../static/app.css', import.meta.url), 'utf8');
  const colored = new Set([...css.matchAll(/\[data-ss="(ss\d{2})"\]/gu)].map((match) => match[1]));
  assert.deepEqual([...colored].sort(), Object.keys(manifest.feature_descriptions).sort());
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

test('duplicateChip appears only for multi-member duplicate groups and deep-links the worklist', () => {
  const unit = { id: 'u-YAQVKvoqzCv', duplicate_group: 'e-0000', class: 'join-dropped' };
  assert.equal(duplicateChip(unit, ['u-YAQVKvoqzCv']), null);
  assert.equal(duplicateChip({ ...unit, duplicate_group: null }, ['u-YAQVKvoqzCv', 'u-DdkFp23muye']), null);
  const chip = duplicateChip(unit, ['u-YAQVKvoqzCv', 'u-DdkFp23muye', 'u-0007'], 'Join dropped');
  assert.equal(chip.label, 'duplicate ×3');
  assert.equal(chip.href, '#units=u-YAQVKvoqzCv,u-DdkFp23muye,u-0007');
  assert.ok(chip.title.includes('3 windows of “Join dropped”'), 'the tooltip names the kind of change by its title');
  assert.ok(duplicateChip(unit, ['u-YAQVKvoqzCv', 'u-DdkFp23muye']).title.includes('“join-dropped”'), 'and by its id without one');
});

test('duplicateFillTargets excludes the unit itself and anything already verdicted', () => {
  const unit = { id: 'u-YAQVKvoqzCv', duplicate_group: 'e-0000' };
  const verdicted = new Set(['u-0007']);
  assert.deepEqual(
    duplicateFillTargets(unit, ['u-YAQVKvoqzCv', 'u-DdkFp23muye', 'u-0007'], (id) => verdicted.has(id)),
    ['u-DdkFp23muye'],
  );
  assert.deepEqual(duplicateFillTargets({ id: 'u-YAQVKvoqzCv', duplicate_group: null }, ['u-YAQVKvoqzCv', 'u-DdkFp23muye'], () => false), []);
  assert.deepEqual(duplicateFillTargets(undefined, ['u-YAQVKvoqzCv'], () => false), []);
});

test('fixture duplicate-group ids group only within a shard and singletons carry their own id', () => {
  const members = new Map();
  for (const unit of allUnits) {
    if (unit.duplicate_group === null) {
      assert.equal(needsNoVerdict(unit), true, unit.id);
      continue;
    }
    if (!members.has(unit.duplicate_group)) members.set(unit.duplicate_group, []);
    members.get(unit.duplicate_group).push(unit);
  }
  const grouped = [...members.values()].find((units) => units.length > 1);
  assert.ok(grouped, 'the fixtures must exercise a multi-member duplicate group');
  assert.equal(new Set(grouped.map((unit) => unit.class)).size, 1);
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

test('pairBand draws the judged pair from a whole record and nothing from a slim fragment', () => {
  const human = allUnits.find((unit) => !needsNoVerdict(unit) && unit.pair !== null);
  const upem = manifest.fonts.after.upem;
  assert.deepEqual(pairBand(human, 'after', 88, upem), highlightRect(human.highlight.after, 88, upem));
  assert.deepEqual(pairBand(human, 'before', 88, upem), highlightRect(human.highlight.before, 88, upem));
  assert.equal(pairBand({ ...human, pair: null }, 'after', 88, upem), null, 'no primary pair, no band');
  const slim = allUnits.find((unit) => needsNoVerdict(unit));
  assert.equal('highlight' in slim, false, 'the fixture machine fragment is slim');
  assert.equal(pairBand(slim, 'after', 88, upem), null);
  assert.equal(pairBand(slim, 'before', 88, upem), null);
});

test('a slim fragment reaching the fold renderer yields a row from its cells and junctions alone', () => {
  // Runs the pure helpers buildRow uses over the fixture's machine fragment, as the app does for a fold's rows. None may throw on the missing explain, drafts, and highlight.
  const slim = allUnits.find((unit) => needsNoVerdict(unit));
  for (const key of ['explain', 'drafts', 'highlight']) assert.equal(key in slim, false, key);
  assert.equal(needsNoVerdict(slim), true);
  const groups = renderGroupsOf(slim);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].configs, slim.configs);
  assert.deepEqual(secondaryJunctionsOf(slim), []);
  assert.deepEqual(onlyHereJunctionSpans(slim), []);
  assert.equal(pairBand(slim, 'after', 88, manifest.fonts.after.upem), null);
  const separators = tokenSeparators(slim.notation_tokens);
  assert.equal(separators.map((sep, index) => sep + slim.notation_tokens[index]).join(''), slim.notation);
  const runs = tokenMarkRuns(slim.notation_tokens, separators, slim.pair_codepoints, []);
  assert.equal(runs.map((run) => run.text).join(''), slim.notation);
  assert.ok(runs.some((run) => run.pair), 'the judged pair still underlines on the text lines');
  assert.deepEqual(configGateChips(slim, manifest.feature_descriptions).map((chip) => chip.text), [slim.config_note]);
  assert.ok(searchHaystack(slim).includes(slim.id.toLowerCase()), 'the haystack is lowercase; the search lowercases the query to match');
  assert.ok(typeof slim.summary === 'string' && slim.summary.startsWith('New: '));
  assert.ok(slim.after.cells.length > 0 && slim.after.junctions.length > 0);
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
  const unit = fixtureUnit('u-BLyK6Z13RtN');
  const empty = { class: null, group: null, family: null, config: null, status: null };
  assert.equal(unitMatchesFilters(unit, empty, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, class: 'fixture-intended' }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, class: 'join-dropped' }, undefined), false);
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
  const unit = fixtureUnit('u-5NP1CPJxyNT');
  const empty = { class: null, group: null, family: null, config: null, status: null, units: null };
  assert.equal(unitMatchesFilters(unit, { ...empty, units: `other,${unit.id}` }, undefined), true);
  assert.equal(unitMatchesFilters(unit, { ...empty, units: 'u-9990,u-9991' }, undefined), false);
  assert.equal(unitMatchesFilters(unit, { ...empty, units: unit.id, class: 'join-dropped' }, undefined), false);
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
    class: 'fixture-mismatch',
    config: 'ss03',
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

test('needsNoVerdict reads the machine checks and the exemption, never a batch', () => {
  for (const flag of ['ink_identical', 'picture_identical', 'junior_equivalent', 'no_verdict']) {
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

// App index rows leave out the three machine-check flags and `no_verdict`, because only human units have a row (app_index.app_row). These tests check that the readers treat a row without the flags the same as a shard record with them false.
const slimRow = (unit) => {
  const row = { ...unit };
  for (const field of ['ink_identical', 'picture_identical', 'junior_equivalent', 'no_verdict', 'explain', 'drafts', 'provenance']) {
    delete row[field];
  }
  return row;
};

test('a slim row missing the machine flags reads as human wherever a whole record does', () => {
  const human = allUnits.filter((unit) => !needsNoVerdict(unit));
  assert.ok(human.length >= 2);
  for (const unit of human) {
    const row = slimRow(unit);
    assert.equal(needsNoVerdict(row), needsNoVerdict(unit), unit.id);
    assert.deepEqual(secondaryJunctionsOf(row), secondaryJunctionsOf(unit), unit.id);
    assert.deepEqual(onlyHereJunctionSpans(row), onlyHereJunctionSpans(unit), unit.id);
    assert.equal(unitMatchesFilters(row, emptyFilters, undefined), unitMatchesFilters(unit, emptyFilters, undefined), unit.id);
    assert.equal(searchHaystack(row), searchHaystack(unit), unit.id);
    assert.equal(copyPreamble(row), copyPreamble(unit), unit.id);
  }
});

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
  const wrongClass = partitionUnits(allUnits, { ...filters, class: 'join-dropped' }, noRecords);
  assert.deepEqual(wrongClass.machine, []);
});

test('human and machine counts come from the manifest class metadata', () => {
  const marker = manifest.classes.find((cls) => cls.id === 'fixture-intended');
  const dangling = manifest.classes.find((cls) => cls.id === 'join-dropped');
  assert.equal(humanClassCount(marker), marker.unit_count - 1);
  assert.equal(humanClassCount(dangling), dangling.unit_count);
  assert.equal(humanTotal(manifest), manifest.totals.units - manifest.machine_approved.units);
  assert.equal(noVerdictTotal(manifest), 0);
});

test('machineFoldTotal counts what a class fold will hold before the class is fetched', () => {
  const marker = manifest.classes.find((cls) => cls.id === 'fixture-intended');
  const dangling = manifest.classes.find((cls) => cls.id === 'join-dropped');
  assert.equal(machineFoldTotal(marker), 1);
  assert.equal(machineFoldTotal(dangling), 0, 'a class with nothing machine-approved folds nothing');
  assert.equal(machineFoldTotal({ no_verdict: true, unit_count: 6344, machine_approved_count: 4465 }), 6344);
  assert.equal(machineFoldTotal({ no_verdict: false, unit_count: 9 }), 0, 'a countless class folds nothing');
});

test('machineFoldCheck picks the narrowest check that accounts for every machine unit', () => {
  const cls = (over) => ({ no_verdict: false, unit_count: 10, machine_approved_count: 10, ...over });
  assert.equal(
    machineFoldCheck(cls({ machine_checks: { ink_identical: 10, picture_identical: 0, junior_equivalent: 0 } })),
    'ink_identical',
  );
  assert.equal(
    machineFoldCheck(cls({ machine_checks: { ink_identical: 6, picture_identical: 4, junior_equivalent: 0 } })),
    'picture_identical',
  );
  assert.equal(
    machineFoldCheck(cls({ machine_checks: { ink_identical: 6, picture_identical: 1, junior_equivalent: 3 } })),
    'junior_equivalent',
  );
  assert.equal(
    machineFoldCheck({
      no_verdict: true,
      unit_count: 10,
      machine_approved_count: 4,
      machine_checks: { ink_identical: 4, picture_identical: 0, junior_equivalent: 0 },
    }),
    'no_verdict',
    'a no-verdict class folds its exempt units too, so no machine check accounts for all of them',
  );
});

test('machineFoldCheck falls back to the no-verdict badge for a class carrying no check split', () => {
  assert.equal(machineFoldCheck({ no_verdict: false, unit_count: 10, machine_approved_count: 10 }), 'no_verdict');
  assert.equal(machineFoldCheck({ no_verdict: false, unit_count: 10, machine_approved_count: 0 }), 'no_verdict');
  const marker = manifest.classes.find((cls) => cls.id === 'fixture-intended');
  assert.equal(machineFoldCheck(marker), 'ink_identical');
});

test('a no-verdict class contributes nothing to the human units and everything non-identical to the exempt total', () => {
  const exemptClass = { id: 'split-by-space-or-zwnj', no_verdict: true, unit_count: 6344, machine_approved_count: 4465 };
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
  assert.deepEqual(machineChecks(manifest), { units: 1, inkIdentical: 1, pictureIdentical: 0, juniorEquivalent: 0 });
  assert.deepEqual(machineChecks({ machine_approved: { units: 1 } }), { units: 1, inkIdentical: 1, pictureIdentical: 0, juniorEquivalent: 0 });
  const splitManifest = {
    machine_approved: {
      units: 11926,
      checks: { ink_identical: { units: 8350 }, junior_equivalent: { units: 3576 } },
    },
  };
  assert.deepEqual(machineChecks(splitManifest), { units: 11926, inkIdentical: 8350, pictureIdentical: 0, juniorEquivalent: 3576 });
  const threeWay = {
    machine_approved: {
      units: 13126,
      checks: { ink_identical: { units: 8350 }, picture_identical: { units: 1200 }, junior_equivalent: { units: 3576 } },
    },
  };
  assert.deepEqual(machineChecks(threeWay), { units: 13126, inkIdentical: 8350, pictureIdentical: 1200, juniorEquivalent: 3576 });
  assert.deepEqual(machineChecks({}), { units: 0, inkIdentical: 0, pictureIdentical: 0, juniorEquivalent: 0 });
});

test('the collapsed chip carries the corpus total and the popover breaks it down to the human units', () => {
  assert.equal(corpusChipLabel(manifest), '11 units');
  assert.deepEqual(
    corpusDetailRows(manifest).map((row) => [row.label, row.value]),
    [
      ['Corpus', '11'],
      ['ink-identical machine-approved', '1'],
      ['for human review', '10'],
    ],
  );
  const splitManifest = {
    totals: { units: 15960 },
    machine_approved: {
      units: 11926,
      method: 'Shaped in both fonts and compared.',
      checks: { ink_identical: { units: 8350 }, junior_equivalent: { units: 3576 } },
    },
    classes: [
      { id: 'split-by-space-or-zwnj', no_verdict: true, unit_count: 6256, machine_approved_count: 4940 },
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
      ['junior-equivalent', '3,576', true],
      ['covered by a ruling', '1,316', false],
      ['for human review', '2,718', false],
    ],
  );
  const rows = corpusDetailRows({ totals: { units: 4 }, machine_approved: { units: 0 }, classes: [] });
  assert.deepEqual(
    rows.map((row) => row.label),
    ['Corpus', 'for human review'],
  );
});

test('a third machine check takes its own sub row, and a lone check merges back into the total row', () => {
  const corpus = {
    totals: { units: 15960 },
    classes: [
      { id: 'split-by-space-or-zwnj', no_verdict: true, unit_count: 6256, machine_approved_count: 4940 },
      { id: 'x', no_verdict: false, unit_count: 9704, machine_approved_count: 6986 },
    ],
  };
  const threeWay = {
    ...corpus,
    machine_approved: {
      units: 13126,
      method: 'Shaped in both fonts and compared.',
      checks: { ink_identical: { units: 8350 }, picture_identical: { units: 1200 }, junior_equivalent: { units: 3576 } },
    },
  };
  assert.deepEqual(
    corpusDetailRows(threeWay).map((row) => [row.label, row.value, row.sub ?? false]),
    [
      ['Corpus', '15,960', false],
      ['machine-approved', '13,126', false],
      ['ink-identical', '8,350', true],
      ['picture-identical', '1,200', true],
      ['junior-equivalent', '3,576', true],
      ['covered by a ruling', '1,316', false],
      ['for human review', '2,718', false],
    ],
  );
  const pictureOnly = {
    ...corpus,
    machine_approved: {
      units: 3,
      method: 'Rasterized in both fonts and compared cell by cell.',
      checks: { ink_identical: { units: 0 }, picture_identical: { units: 3 }, junior_equivalent: { units: 0 } },
    },
  };
  assert.deepEqual(
    corpusDetailRows(pictureOnly).map((row) => [row.label, row.value, row.sub ?? false]),
    [
      ['Corpus', '15,960', false],
      ['picture-identical machine-approved', '3', false],
      ['covered by a ruling', '1,316', false],
      ['for human review', '2,718', false],
    ],
  );
  const juniorOnly = {
    ...corpus,
    machine_approved: {
      units: 3576,
      method: 'Compared against the Junior font.',
      checks: { ink_identical: { units: 0 }, picture_identical: { units: 0 }, junior_equivalent: { units: 3576 } },
    },
  };
  assert.deepEqual(
    corpusDetailRows(juniorOnly).map((row) => [row.label, row.value, row.sub ?? false]),
    [
      ['Corpus', '15,960', false],
      ['junior-equivalent machine-approved', '3,576', false],
      ['covered by a ruling', '1,316', false],
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
      checks: { ink_identical: { units: 8350 }, junior_equivalent: { units: 3576 } },
    },
  };
  assert.equal(
    machineTitle(splitManifest),
    '8,350 ink-identical + 3,576 junior-equivalent. Shaped in both fonts and compared.',
  );
  const methodless = { machine_approved: { units: 11926, checks: splitManifest.machine_approved.checks } };
  assert.equal(machineTitle(methodless), '8,350 ink-identical + 3,576 junior-equivalent.');
  const threeWay = {
    machine_approved: {
      units: 13126,
      method: 'Shaped in both fonts and compared.',
      checks: { ink_identical: { units: 8350 }, picture_identical: { units: 1200 }, junior_equivalent: { units: 3576 } },
    },
  };
  assert.equal(
    machineTitle(threeWay),
    '8,350 ink-identical + 1,200 picture-identical + 3,576 junior-equivalent. Shaped in both fonts and compared.',
  );
  const pictureOnly = {
    machine_approved: {
      units: 3,
      method: 'Rasterized in both fonts and compared cell by cell.',
      checks: { ink_identical: { units: 0 }, picture_identical: { units: 3 }, junior_equivalent: { units: 0 } },
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

test('classCountsLine counts a Spot-check entry to confirm and names the rejects a spot-check looks for', () => {
  const spot = { section: 'spot-check', no_verdict: false, unit_count: 460, machine_approved_count: 10 };
  assert.equal(classCountsLine(spot, { done: 0, skipped: 0, rejected: 0 }), '450 to confirm');
  assert.equal(classCountsLine(spot, { done: 50, skipped: 0, rejected: 0 }), '400 to confirm · 50 done');
  assert.equal(classCountsLine(spot, { done: 50, skipped: 2, rejected: 3 }), '400 to confirm (2 skipped) · 50 done (3 rejected)');
  assert.equal(classCountsLine(spot, { done: 450, skipped: 0, rejected: 3 }), 'all 450 done (3 rejected)');
});

test('classCountsLine reads "all N done" once nothing is left, and says why a class with no human units needs none', () => {
  const finished = { section: 'open-questions', no_verdict: false, unit_count: 5555, machine_approved_count: 4528 };
  assert.equal(classCountsLine(finished, { done: 1027, skipped: 0, rejected: 0 }), 'all 1,027 done');
  assert.equal(classIsDone(finished, { done: 1027, skipped: 0, rejected: 0 }), true);
  assert.equal(classIsDone(finished, null), false, 'nothing is done before the index loads');
  const exempt = { section: 'no-review-needed', no_verdict: true, unit_count: 6256, machine_approved_count: 4940 };
  assert.equal(classCountsLine(exempt, null), '6,256 units · covered by a ruling');
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
  assert.deepEqual(availableBatches(manifest, 'join-dropped'), [0]);
  assert.deepEqual(availableBatches(manifest, 'fixture-intended'), [0, 1]);
  assert.deepEqual(availableBatches(manifest, 'nonexistent'), []);
});

test('classesInBatch names the classes with units in a batch, batchless classes included with batch 0', () => {
  assert.deepEqual(
    [...classesInBatch(manifest, 0)].sort(),
    ['fixture-intended', 'join-dropped', 'same-joins-same-width'],
  );
  assert.deepEqual([...classesInBatch(manifest, 1)], ['fixture-intended', 'fixture-mismatch']);
  const withExempt = { classes: [...manifest.classes, { id: 'split-by-space-or-zwnj', batches: [], no_verdict: true }] };
  assert.deepEqual(
    [...classesInBatch(withExempt, 0)].sort(),
    ['fixture-intended', 'join-dropped', 'same-joins-same-width', 'split-by-space-or-zwnj'],
  );
  assert.deepEqual([...classesInBatch(withExempt, 1)], ['fixture-intended', 'fixture-mismatch']);
  assert.deepEqual(
    [...classesInBatch(withExempt, 0, false)].sort(),
    ['fixture-intended', 'join-dropped', 'same-joins-same-width'],
    'with machine rows hidden a batchless class contributes nothing to batch 0',
  );
});

test('copyPreamble names only the unit, codepoints, and notation — the rest is looked up from the shards', () => {
  const text = copyPreamble(fixtureUnit('u-WT2Pjqw8CKB'));
  assert.match(text, /rebuild\/out\/review\/ unit u-WT2Pjqw8CKB/);
  assert.match(text, /E670:E653/);
  assert.match(text, /·It·Day/);
  assert.doesNotMatch(text, /join-dropped/);
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
  for (const unit of allUnits) {
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

test('secondaryJunctionsOf returns junctions for human units and nothing for machine-approved or legacy units', () => {
  const withPrimary = fixtureUnit('u-Xp5KzYAPYK7');
  assert.equal(secondaryJunctionsOf(withPrimary).length, 1);
  assert.equal(secondaryJunctionsOf(withPrimary)[0].primary_unit, 'u-6VFDHaMNgCP');
  const legacy = { ink_identical: false };
  assert.deepEqual(secondaryJunctionsOf(legacy), []);
  const nulled = { ink_identical: false, secondary_junctions: null };
  assert.deepEqual(secondaryJunctionsOf(nulled), []);
  const machine = { ink_identical: true, secondary_junctions: [{ primary_unit: 'u-LK4WFNGn5Kk' }] };
  assert.deepEqual(secondaryJunctionsOf(machine), [], 'machine-approved renderings never show junction markers');
  const picture = { picture_identical: true, secondary_junctions: [{ primary_unit: 'u-LK4WFNGn5Kk' }] };
  assert.deepEqual(secondaryJunctionsOf(picture), [], 'picture identity is a whole-window property, so it hides them too');
});

test('junctionChip labels a junction that has a primary unit with its id and one that has none with "only here"', () => {
  const withPrimary = junctionChip({ primary_unit: 'u-0312' });
  assert.equal(withPrimary.primaryUnit, 'u-0312');
  assert.equal(withPrimary.label, 'u-0312');
  assert.match(withPrimary.title, /u-0312/);
  const withoutPrimary = junctionChip({ primary_unit: null });
  assert.equal(withoutPrimary.primaryUnit, null);
  assert.equal(withoutPrimary.label, 'only here');
  assert.match(withoutPrimary.title, /no shorter primary unit/);
});

test('cellCodepointSpans gives each cell one codepoint position and a formed ligature two', () => {
  assert.deepEqual(
    cellCodepointSpans(['uni200C', 'qsTea_qsOy/sole/None/None/+locked', 'qsUtter/mono/None/None/']),
    [[0, 0], [1, 2], [3, 3]],
  );
});

const onlyHereUnit = {
  ink_identical: false,
  pair: { left: 0, right: 1 },
  pair_codepoints: [0, 1],
  after: {
    cells: ['qsNo/loop/None/x-height/', 'qsIt/sole/x-height/baseline/', 'qsMay/loop/baseline/None/', 'qsTea/full/None/None/'],
  },
  secondary_junctions: [
    { pair: { left: 1, right: 2 }, primary_unit: null },
    { pair: { left: 2, right: 3 }, primary_unit: 'u-Yt4bRkgFmpk' },
  ],
};

test('onlyHereJunctionSpans maps junctions with no primary unit to codepoint spans and skips the rest', () => {
  assert.deepEqual(onlyHereJunctionSpans(onlyHereUnit), [[1, 2]]);
});

test('onlyHereJunctionSpans shifts spans across a formed ligature', () => {
  const unit = {
    ...onlyHereUnit,
    pair_codepoints: [0, 2],
    after: { cells: ['qsTea_qsOy/full/None/None/', 'qsIt/sole/x-height/baseline/', 'qsMay/loop/baseline/None/'] },
    secondary_junctions: [{ pair: { left: 1, right: 2 }, primary_unit: null }],
  };
  assert.deepEqual(onlyHereJunctionSpans(unit), [[2, 3]]);
});

test('onlyHereJunctionSpans yields nothing for machine-approved units, missing cells, no pair, or a derivation that disagrees with the build', () => {
  assert.deepEqual(onlyHereJunctionSpans({ ...onlyHereUnit, ink_identical: true }), []);
  assert.deepEqual(onlyHereJunctionSpans({ ...onlyHereUnit, picture_identical: true }), []);
  assert.deepEqual(onlyHereJunctionSpans({ ...onlyHereUnit, after: {} }), []);
  assert.deepEqual(onlyHereJunctionSpans({ ...onlyHereUnit, pair: null }), []);
  assert.deepEqual(onlyHereJunctionSpans({ ...onlyHereUnit, pair_codepoints: [0, 2] }), []);
});

// App index rows carry no `after` (app_index.app_row). When every secondary junction has a primary unit, onlyHereJunctionSpans skips them all, so the result is [] with or without the cells.
test('onlyHereJunctionSpans reads a row whose junctions all have primary units the same with cells present and with after nulled', () => {
  const withPrimaryOnly = {
    ...onlyHereUnit,
    secondary_junctions: [{ pair: { left: 1, right: 2 }, primary_unit: 'u-Yt4bRkgFmpk' }],
  };
  assert.deepEqual(onlyHereJunctionSpans(withPrimaryOnly), []);
  assert.deepEqual(onlyHereJunctionSpans({ ...withPrimaryOnly, after: null }), []);
  const withoutJunctions = { ...onlyHereUnit, secondary_junctions: null };
  assert.deepEqual(onlyHereJunctionSpans(withoutJunctions), []);
  assert.deepEqual(onlyHereJunctionSpans({ ...withoutJunctions, after: null }), []);
  assert.deepEqual(onlyHereJunctionSpans({ ...onlyHereUnit, after: null }), [], 'a junction with no primary unit needs its cells to underline');
});

test('the only-here fixture unit underlines its junction tokens', () => {
  const unit = fixtureUnit('u-LhDRYppbBuN');
  assert.deepEqual(onlyHereJunctionSpans(unit), [[1, 2]]);
});

test('tokenMarkRuns marks pair and junction stretches, sharing a separator only between two tokens under the same mark', () => {
  const runs = tokenMarkRuns(['E666', 'E670', 'E665', 'E652'], ['', ':', ':', ':'], [0, 1], [[1, 2]]);
  assert.deepEqual(runs, [
    { text: 'E666:', pair: true, junction: false },
    { text: 'E670', pair: true, junction: true },
    { text: ':E665', pair: false, junction: true },
    { text: ':E652', pair: false, junction: false },
  ]);
  assert.equal(runs.map((run) => run.text).join(''), 'E666:E670:E665:E652');
});

test('tokenMarkRuns without junction spans reproduces the single pair-mark split', () => {
  assert.deepEqual(tokenMarkRuns(['·No', '·It', '·May', '·Tea'], ['', '', '', ''], [0, 1], []), [
    { text: '·No·It', pair: true, junction: false },
    { text: '·May·Tea', pair: false, junction: false },
  ]);
  assert.deepEqual(tokenMarkRuns(['◊ZWNJ', '·Tea', '·Oy'], ['', ' ', ''], [1, 2], []), [
    { text: '◊ZWNJ ', pair: false, junction: false },
    { text: '·Tea·Oy', pair: true, junction: false },
  ]);
});

test('fixture units satisfy the contract fields the frontend relies on', () => {
  for (const unit of allUnits) {
    assert.match(unit.id, /^u-[1-9A-HJ-NP-Za-km-z]{11}$/);
    assert.equal(typeof unit.ink_identical, 'boolean');
    assert.equal(typeof unit.picture_identical, 'boolean');
    assert.equal(typeof unit.junior_equivalent, 'boolean');
    assert.equal(typeof unit.no_verdict, 'boolean');
    const machineApproved = unit.ink_identical || unit.picture_identical || unit.junior_equivalent;
    assert.equal('batch' in unit, false, 'a fragment carries no batch; the manifest index does');
    assert.equal(typeof unit.text_entities, 'string');
    assert.doesNotMatch(unit.text_entities, /[\u200C\uE650-\uE67E]/);
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
    // A unit that takes no verdict is written slim: the keys in audit.SLIM_OMITTED_KEYS are absent, not null. A human unit has them all.
    if (needsNoVerdict(unit)) {
      for (const key of ['explain', 'drafts', 'highlight']) assert.equal(key in unit, false, `${unit.id} carries ${key}`);
    } else {
      assert.equal(typeof unit.explain, 'string');
      assert.ok(unit.drafts && typeof unit.drafts === 'object');
      if (unit.pair !== null) {
        assert.ok(unit.highlight.before.x_max > unit.highlight.before.x_min);
        assert.ok(unit.highlight.after.x_max > unit.highlight.after.x_min);
      }
    }
    for (const mark of unit.boundary_marks) {
      assert.equal(typeof mark.x, 'number');
      assert.ok(['zwnj', 'space', 'namer-dot'].includes(mark.kind));
    }
    if (unit.secondary_junctions != null) {
      assert.ok(Array.isArray(unit.secondary_junctions) && unit.secondary_junctions.length >= 1);
      assert.equal(unit.ink_identical, false);
      assert.notEqual(unit.picture_identical, true);
      for (const junction of unit.secondary_junctions) {
        assert.ok(Number.isInteger(junction.pair.left) && Number.isInteger(junction.pair.right));
        assert.ok(junction.pair.left < junction.pair.right);
        assert.notDeepEqual(junction.pair, unit.pair, `${unit.id}: a secondary junction must not duplicate the primary pair`);
        for (const side of ['before', 'after']) {
          assert.ok(Number.isInteger(junction[side].x_min) && Number.isInteger(junction[side].x_max));
          assert.ok(junction[side].x_min <= junction[side].x_max);
          assert.ok(Number.isInteger(junction[side].advance_total));
        }
        assert.ok(junction.primary_unit === null || /^u-[1-9A-HJ-NP-Za-km-z]{11}$/.test(junction.primary_unit));
      }
    }
  }
  assert.ok(
    allUnits.some((unit) => (unit.secondary_junctions ?? []).some((junction) => junction.primary_unit)),
    'the fixtures must exercise a secondary junction with a primary unit',
  );
  assert.ok(
    allUnits.some((unit) => (unit.secondary_junctions ?? []).some((junction) => junction.primary_unit === null)),
    'the fixtures must exercise a secondary junction with no primary unit',
  );
});

test('searchHaystack folds id, notation, codepoints, class, group, duplicate group, cluster, and kinds into one lowercase string', () => {
  const haystack = searchHaystack(fixtureUnit('u-BLyK6Z13RtN'));
  assert.ok(haystack.includes('u-blyk6z13rtn'), 'the id, folded to lowercase like everything else');
  assert.ok(haystack.includes('·tea·oy'));
  assert.ok(haystack.includes('teaoy'), 'notation with the namer dots stripped is searchable');
  assert.ok(haystack.includes('200c:e652:e679'));
  assert.ok(haystack.includes('200ce652e679'), 'codepoints with the colons stripped are searchable');
  assert.ok(haystack.includes('fixture-intended'));
  assert.ok(haystack.includes('qstea:qsoy'));
  assert.ok(haystack.includes('ligation'));
  assert.ok(haystack.includes('e-0001'), 'the duplicate group id is searchable');
  assert.ok(haystack.includes('c-f1a70001'), 'the cluster signature id is searchable');
});

test('searchHaystack is memoized per unit, so a keystroke folds each row at most once', () => {
  // Rows are immutable once parsed; mutating one here is only a probe for whether the second call refolded it.
  const unit = { ...fixtureUnit('u-5NP1CPJxyNT') };
  const first = searchHaystack(unit);
  unit.notation = '·Nope';
  assert.equal(searchHaystack(unit), first, 'the second call is answered from the cache');
  const twin = { ...fixtureUnit('u-5NP1CPJxyNT'), notation: '·Nope' };
  assert.notEqual(searchHaystack(twin), first, 'a row the cache has not seen folds on its own');
  assert.ok(searchHaystack(twin).includes('·nope'));
});

test('searchUnits finds units by duplicate group id and by cluster id', () => {
  assert.deepEqual(
    searchUnits(allUnits, 'e-0005').matches.map((unit) => unit.id).sort(),
    ['u-HLeS39nnjSM', 'u-WT2Pjqw8CKB'],
  );
  assert.deepEqual(
    searchUnits(allUnits, 'c-f1a70012').matches.map((unit) => unit.id).sort(),
    ['u-5NP1CPJxyNT', 'u-Nc3wVLJaPHB'],
  );
});

test('duplicateGroupOfQuery offers the duplicate group for a content-addressed id, keeping its case', () => {
  const duplicateIndex = new Map([['e-3mJ7kPq2Xw9', ['u-DdkFp23muye', 'u-YAQVKvoqzCv']]]);
  assert.deepEqual(duplicateGroupOfQuery('  e-3mJ7kPq2Xw9 ', duplicateIndex), {
    id: 'e-3mJ7kPq2Xw9',
    members: ['u-DdkFp23muye', 'u-YAQVKvoqzCv'],
  });
  assert.equal(duplicateGroupOfQuery('e-3mj7kpq2xw9', duplicateIndex), null, 'base58 ids are case-significant');
  assert.equal(duplicateGroupOfQuery('e-3mJ7', duplicateIndex), null);
});

test('searchUnits finds a unit by its exact id across every shard', () => {
  const { matches, total } = searchUnits(allUnits, 'u-SupLKT1ijWn');
  assert.equal(total, 1);
  assert.equal(matches[0].id, 'u-SupLKT1ijWn');
});

test('searchUnits matches notation with and without the namer dots, case-insensitively', () => {
  assert.deepEqual(
    searchUnits(allUnits, '·Pea·May').matches.map((unit) => unit.id),
    ['u-DBug8cSj15L'],
  );
  assert.deepEqual(
    searchUnits(allUnits, 'peamay').matches.map((unit) => unit.id),
    ['u-DBug8cSj15L'],
  );
});

test('searchUnits matches codepoints with and without the colons', () => {
  assert.deepEqual(searchUnits(allUnits, 'E667').matches.map((unit) => unit.id), ['u-LhDRYppbBuN']);
  assert.deepEqual(searchUnits(allUnits, 'e653e667').matches.map((unit) => unit.id), ['u-LhDRYppbBuN']);
});

test('searchUnits matches class, group, and kind, and includes machine-approved units', () => {
  const byClass = searchUnits(allUnits, 'join-dropped');
  assert.deepEqual(byClass.matches.map((unit) => unit.id).sort(), ['u-HLeS39nnjSM', 'u-LhDRYppbBuN', 'u-WT2Pjqw8CKB']);
  const junction = searchUnits(allUnits, 'junction');
  assert.deepEqual(junction.matches.map((unit) => unit.id).sort(), ['u-DBug8cSj15L', 'u-HLeS39nnjSM', 'u-LhDRYppbBuN', 'u-WT2Pjqw8CKB']);
  assert.equal(junction.matches.find((unit) => unit.id === 'u-DBug8cSj15L').ink_identical, true, 'a machine-approved unit is still findable');
});

test('searchUnits matches a kind of change by its title, a re-routed unit by the class its glyph names gave it, and the raw config badge', () => {
  const info = classInfo(manifest);
  const titleOf = (id) => info.get(id)?.title ?? id;
  const fresh = allUnits.map((unit) => ({ ...unit }));
  assert.deepEqual(
    searchUnits(fresh, 'whole', 50, titleOf).matches.map((unit) => unit.id).sort(),
    ['u-5NP1CPJxyNT', 'u-BLyK6Z13RtN', 'u-DBug8cSj15L', 'u-Nc3wVLJaPHB'],
  );
  const untitled = allUnits.map((unit) => ({ ...unit }));
  assert.equal(searchUnits(untitled, 'whole').total, 0, 'only the title says "whole"');
  assert.deepEqual(searchUnits(fresh, 'fixture-identical', 50, titleOf).matches.map((unit) => unit.id), ['u-D6rjFJxbdV6']);
  const noted = { ...allUnits[0], id: 'u-noted', config_class_note: 'blessed as ss03-adds-joins-around-tea under ss03' };
  assert.deepEqual(searchUnits([noted], 'blessed').matches.map((unit) => unit.id), ['u-noted']);
});

test('searchUnits requires every whitespace-separated token to match (AND)', () => {
  assert.deepEqual(searchUnits(allUnits, 'teaoy').matches.map((unit) => unit.id).sort(), ['u-6VFDHaMNgCP', 'u-BLyK6Z13RtN', 'u-SupLKT1ijWn']);
  assert.deepEqual(searchUnits(allUnits, 'teaoy 200c').matches.map((unit) => unit.id), ['u-BLyK6Z13RtN']);
  assert.equal(searchUnits(allUnits, 'tea low').total, 0);
});

test('searchUnits ranks an exact id hit ahead of incidental substring hits', () => {
  // Only one fixture unit contains this id. searchScore ranks an exact id match first, so it would stay first even if another unit's haystack contained the id.
  const { matches } = searchUnits(allUnits, 'u-WT2Pjqw8CKB');
  assert.equal(matches[0].id, 'u-WT2Pjqw8CKB');
});

test('searchUnits caps the matches at the limit but reports the true total', () => {
  const { matches, total } = searchUnits(allUnits, 'u-', 2);
  assert.equal(total, allUnits.length, 'every fixture unit id starts with u-');
  assert.equal(matches.length, 2);
});

test('searchUnits returns nothing for a blank query', () => {
  assert.deepEqual(searchUnits(allUnits, '   '), { matches: [], total: 0 });
});

test('the fixture manifest lists its classes in section order, each with a title, a summary, and a ledger status', () => {
  const ranks = manifest.classes.map((cls) => SECTION_ORDER.indexOf(cls.section));
  assert.ok(ranks.every((rank) => rank >= 0));
  assert.deepEqual(ranks, [...ranks].sort((a, b) => a - b));
  for (const cls of manifest.classes) {
    assert.ok(cls.title && cls.summary, cls.id);
    assert.ok(['by-design', 'approved', 'per-unit'].includes(cls.status), cls.id);
  }
  const info = classInfo(manifest);
  for (const unit of allUnits) {
    for (const classId of [unit.class, unit.ledger_class, ...Object.values(unit.config_classes ?? {})]) {
      if (classId && classId !== UNMATCHED_CLASS) assert.ok(info.has(classId), `${unit.id} names ${classId}, which the manifest cannot title`);
    }
  }
  assert.ok(allUnits.some((unit) => unit.ledger_class), 'the fixtures must exercise a re-routed unit');
});

test('STATUS_LABELS has a pill and a legend line for every fixture status, the derived machine-approved pill, and a table-diff bucket', () => {
  const statuses = new Set(manifest.classes.map((cls) => cls.status));
  for (const entry of Object.values(manifest.ledger_classes)) statuses.add(entry.status);
  assert.deepEqual([...statuses].sort(), ['approved', 'by-design', 'per-unit'], 'the fixtures exercise every ledger status');
  for (const status of [...statuses, 'machine-approved', 'diff']) {
    assert.ok(STATUS_LABELS[status]?.pill, status);
    assert.ok(STATUS_LABELS[status]?.legend, status);
  }
  assert.deepEqual(
    ['per-unit', 'approved', 'by-design', 'machine-approved', 'diff'].map(pillText),
    ['you decide', 'you approved', 'by design', 'machine-approved', 'diff'],
  );
});

test('pillStatus shows machine-approved only on a No review needed class the machine clears, and diff on a table-diff bucket', () => {
  const cls = (over) => ({ no_verdict: false, unit_count: 10, machine_approved_count: 0, ...over });
  assert.equal(pillStatus(cls({ section: 'no-review-needed', status: 'by-design', machine_approved_count: 10 })), 'machine-approved');
  assert.equal(pillStatus(cls({ section: 'no-review-needed', status: 'approved', no_verdict: true })), 'approved', 'a ruling, not the machine, cleared a no_verdict class');
  assert.equal(pillStatus(cls({ section: 'spot-check', status: 'approved', machine_approved_count: 4 })), 'approved');
  assert.equal(pillStatus(cls({ section: 'open-questions', status: null })), 'diff');
  assert.equal(pillStatus({ status: 'by-design', title: 'x', summary: 'y' }), 'by-design', 'a ledger entry that reaches no unit shows its status');
  assert.equal(pillStatus(null), null);
});

test('manifestRefusal refuses a manifest without sections, which would otherwise leave the Queue empty', () => {
  assert.equal(manifestRefusal(manifest), null);
  const unsectioned = { ...manifest, classes: manifest.classes.map(({ section, ...rest }) => rest) };
  assert.equal(manifestRefusal(unsectioned), PREDATES_APP);
  assert.equal(manifestRefusal({ classes: [{ ...manifest.classes[0], section: 'ruled' }] }), PREDATES_APP);
  assert.equal(manifestRefusal({ classes: [] }), null);
  assert.equal(PREDATES_APP, 'This corpus predates the app; run the review cycle.');
});

test('classChip names a class by its title, with its id and plain-text summary to hover and its pill status to color it', () => {
  const info = classInfo(manifest);
  assert.deepEqual(classChip('fixture-intended', info), {
    text: 'Fixture class A, approved as a whole',
    title: 'fixture-intended — Hand-written units for the multi-config, ZWNJ, ligation and duplicate-pin branches, such as ◊ZWNJ | ·Tea+Oy.',
    status: 'approved',
    pill: 'you approved',
  });
  assert.equal(classChip('join-dropped', info).status, 'per-unit');
  const unlisted = classChip('fixture-identical', info);
  assert.equal(unlisted.text, 'Fixture class C: looks identical');
  assert.equal(unlisted.status, 'by-design');
  assert.equal(info.get('fixture-identical').listed, false, 'a ledger class whose every unit is re-routed is named but not listed');
  assert.equal(info.get('fixture-intended').listed, true);
  assert.deepEqual(classChip('no-such-class', info), { text: 'no-such-class', title: 'no-such-class', status: null, pill: null });
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
  assert.equal(rationaleParagraphs(manifest.ledger_classes['fixture-identical'].why).length, 2);
  assert.deepEqual(rationaleParagraphs(''), []);
  assert.deepEqual(rationaleParagraphs(undefined), []);
});

test('sidebarSections sorts the classes into the three sections, with the change groups after the ledger classes', () => {
  const sections = sidebarSections(manifest);
  assert.deepEqual(sections.map((section) => section.id), SECTION_ORDER);
  assert.deepEqual(sections.map((section) => section.title), ['Open questions', 'Spot-check (already ruled)', 'No review needed']);
  assert.deepEqual(sections.map((section) => section.open), [true, true, false]);
  assert.deepEqual(sections[0].entries, []);
  assert.deepEqual(sections[0].changeGroups.map((cls) => cls.id), ['join-dropped', 'same-joins-same-width'], 'a class the ledger lookup does not name is a change group');
  assert.deepEqual(sections[1].entries.map((cls) => cls.id), ['fixture-intended', 'fixture-mismatch']);
  assert.deepEqual(sections[2].entries, []);
  const tableDiff = { classes: [{ id: 'bucket', section: 'open-questions', status: null }], ledger_classes: {} };
  assert.deepEqual(sidebarSections(tableDiff)[0].entries.map((cls) => cls.id), ['bucket'], 'a table-diff bucket gets no change-group subheading');
  for (const id of SECTION_ORDER) assert.ok(SECTION_LABELS[id].legend, id);
});

test('sectionTotalsLine counts a section\'s entries and what is left in it', () => {
  const open = [
    { id: 'a', section: 'open-questions', no_verdict: false, unit_count: 10, machine_approved_count: 2 },
    { id: 'b', section: 'open-questions', no_verdict: false, unit_count: 5, machine_approved_count: 0 },
  ];
  assert.equal(sectionTotalsLine('open-questions', open, null), '2 · 13 to review');
  assert.equal(sectionTotalsLine('open-questions', open, new Map([['a', { done: 8, skipped: 1, rejected: 0 }]])), '2 · 5 to review');
  const spot = [{ id: 'c', section: 'spot-check', no_verdict: false, unit_count: 3000, machine_approved_count: 61 }];
  assert.equal(sectionTotalsLine('spot-check', spot, new Map()), '1 · 2,939 to confirm');
  const none = [
    { id: 'd', section: 'no-review-needed', no_verdict: true, unit_count: 233079, machine_approved_count: 199141 },
    { id: 'e', section: 'no-review-needed', no_verdict: false, unit_count: 3149, machine_approved_count: 3149 },
  ];
  assert.equal(sectionTotalsLine('no-review-needed', none, null), '2 · 236,228 units');
});

test('the class header says what each section asks, and the progress strip counts the class by its title', () => {
  const open = manifest.classes.find((cls) => cls.section === 'open-questions');
  const spot = manifest.classes.find((cls) => cls.section === 'spot-check');
  assert.equal(
    classSectionSentence(open),
    'No ruling covers this whole class. Judge each unit; units you have already ruled count as done. The Queue draws from here.',
  );
  assert.equal(
    classSectionSentence(spot),
    'You ruled this kind of change. Approve a unit that shows what you ruled; reject one that doesn’t. The Queue skips these.',
  );
  assert.equal(
    classSectionSentence({ section: 'no-review-needed', unit_count: 3904, no_verdict: true }),
    'Nothing to judge: a ruling or the machine covers all 3,904 units.',
  );
  assert.equal(classProgressLine(open, { done: 1, skipped: 0, rejected: 0 }), '“Join dropped”: 2 to review of 3');
  assert.equal(classProgressLine(spot, null), '“Fixture class A, approved as a whole”: 3 to confirm of 3');
});

test('configSetPhrase names a config set by its one stylistic set only when the set is exactly the configs that turn it on, and a set another config also turns on as that set alone', () => {
  const configs = ['default', 'ss03', 'ss04', 'ss05', 'ss03+ss05'];
  assert.equal(configSetPhrase(['ss03', 'ss03+ss05'], configs), 'with ss03 on');
  assert.equal(configSetPhrase(['ss05', 'ss03+ss05'], configs), 'with ss05 on');
  assert.equal(configSetPhrase(['ss03'], configs), 'with ss03 alone on');
  assert.equal(configSetPhrase(['ss04'], configs), 'with ss04 on');
  assert.equal(configSetPhrase(['ss03+ss05'], configs), 'with ss03+ss05 on');
  assert.equal(configSetPhrase(['default', 'ss04', 'ss05'], configs), 'with no set, ss04 or ss05 alone on');
  assert.equal(configSetPhrase(['ss03', 'ss04'], configs), 'with ss03 alone or ss04 on');
});

test('configClassBadge says which configs a ruling covers, by its title and pill, and which ones you are judging', () => {
  const info = classInfo({
    ...manifest,
    ledger_classes: {
      ...manifest.ledger_classes,
      'ss03-adds-joins-around-tea': { title: 'ss03 adds joins around ·Tea', summary: 'With ss03 on, the full ·Tea joins the next letter.', status: 'approved', why: '' },
    },
  });
  const unit = {
    configs: ['default', 'ss03', 'ss04', 'ss05', 'ss03+ss05'],
    config_classes: {
      default: 'UNMATCHED',
      ss03: 'ss03-adds-joins-around-tea',
      ss04: 'UNMATCHED',
      ss05: 'UNMATCHED',
      'ss03+ss05': 'ss03-adds-joins-around-tea',
    },
    config_class_note: 'blessed as ss03-adds-joins-around-tea under ss03, ss03+ss05; novel under default, ss04, ss05',
  };
  assert.equal(
    configClassBadge(unit, info),
    'With ss03 on: covered by “ss03 adds joins around ·Tea” (you approved). With no set, ss04 or ss05 alone on, no ruling covers it; that is what you’re judging.',
  );
});

test('configClassBadge shows nothing for a unit with one class under every config, and the raw note without a class map', () => {
  const info = classInfo(manifest);
  const rerouted = fixtureUnit('u-D6rjFJxbdV6');
  assert.ok(Object.keys(rerouted.config_classes).length > 1);
  assert.equal(configClassBadge(rerouted, info), null, 'a re-routed unit is one identical class under every config');
  assert.equal(configClassBadge({ config_class_note: 'novel under default' }, info), 'novel under default');
  assert.equal(configClassBadge({ config_classes: null, config_class_note: null }, info), null);
});
