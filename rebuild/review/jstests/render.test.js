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
  secondarySeamsOf,
  seamChip,
  cellCodepointSpans,
  onlyHereSeamSpans,
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
  machineFoldChannel,
  machineFoldTotal,
  noVerdictTotal,
  formatCount,
  machineChannels,
  surfaceChipLabel,
  surfaceAlphabetLabel,
  surfaceStampLine,
  surfaceDetailRows,
  machineTitle,
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
const explainSamples = JSON.parse(await readFile(new URL('explain-samples.json', fixtureDir), 'utf8'));

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
  const gated = shardA.find((unit) => (unit.config_gate ?? []).length > 1);
  assert.ok(gated, 'the fixtures must exercise a multi-clause config gate');
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
  const fallback = shardA.find((unit) => unit.config_gate === null && unit.config_note);
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
  for (const unit of [...shardA, ...shardB]) {
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
  for (const unit of [...shardA, ...shardB]) {
    for (const config of unit.configs) assert.ok(offered.has(config), `${unit.id} carries unofferable config ${config}`);
  }
  let matched = 0;
  for (const option of options) {
    for (const unit of [...shardA, ...shardB]) {
      const filters = { class: null, group: null, family: null, config: option.value, status: null };
      assert.equal(unitMatchesFilters(unit, filters, undefined), unit.configs.includes(option.value), unit.id);
      if (unit.configs.includes(option.value)) matched += 1;
    }
  }
  assert.ok(matched > 0, 'the fixtures must have units the config filter selects');
});

test('pinStylisticSetScope names the sets in the ssNN idiom and echoes the attribute the pin will be written as', () => {
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
  for (const unit of [...shardA, ...shardB]) {
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
    'the per-set color map must be declared once and shared, not copied per surface',
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

test('echoChip appears only for multi-member echo groups and deep-links the worklist', () => {
  const unit = { id: 'u-JSRuJ51yvVj', echo: 'e-0000', class: 'dangling-anchor-dropped' };
  assert.equal(echoChip(unit, ['u-JSRuJ51yvVj']), null);
  assert.equal(echoChip({ ...unit, echo: null }, ['u-JSRuJ51yvVj', 'u-CKS1rpqQsLb']), null);
  const chip = echoChip(unit, ['u-JSRuJ51yvVj', 'u-CKS1rpqQsLb', 'u-0007']);
  assert.equal(chip.label, 'echo ×3');
  assert.equal(chip.href, '#units=u-JSRuJ51yvVj,u-CKS1rpqQsLb,u-0007');
  assert.ok(chip.title.includes('dangling-anchor-dropped'));
});

test('echoFillTargets excludes the unit itself and anything already verdicted', () => {
  const unit = { id: 'u-JSRuJ51yvVj', echo: 'e-0000' };
  const verdicted = new Set(['u-0007']);
  assert.deepEqual(
    echoFillTargets(unit, ['u-JSRuJ51yvVj', 'u-CKS1rpqQsLb', 'u-0007'], (id) => verdicted.has(id)),
    ['u-CKS1rpqQsLb'],
  );
  assert.deepEqual(echoFillTargets({ id: 'u-JSRuJ51yvVj', echo: null }, ['u-JSRuJ51yvVj', 'u-CKS1rpqQsLb'], () => false), []);
  assert.deepEqual(echoFillTargets(undefined, ['u-JSRuJ51yvVj'], () => false), []);
});

test('fixture echo ids group only within a shard and singletons carry their own id', () => {
  const members = new Map();
  for (const unit of [...shardA, ...shardB]) {
    if (unit.echo === null) {
      assert.equal(needsNoVerdict(unit), true, unit.id);
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

test('pairBand draws the judged pair from a whole record and nothing from a slim fragment', () => {
  const human = shardA.find((unit) => !needsNoVerdict(unit) && unit.pair !== null);
  const upem = manifest.fonts.after.upem;
  assert.deepEqual(pairBand(human, 'after', 88, upem), highlightRect(human.highlight.after, 88, upem));
  assert.deepEqual(pairBand(human, 'before', 88, upem), highlightRect(human.highlight.before, 88, upem));
  assert.equal(pairBand({ ...human, pair: null }, 'after', 88, upem), null, 'no primary pair, no band');
  const slim = shardA.find((unit) => needsNoVerdict(unit));
  assert.equal('highlight' in slim, false, 'the fixture machine fragment is slim');
  assert.equal(pairBand(slim, 'after', 88, upem), null);
  assert.equal(pairBand(slim, 'before', 88, upem), null);
});

test('a slim fragment reaching the fold renderer yields a row from its cells and seams alone', () => {
  // Every pure piece of buildRow, run over the fixture's machine fragment exactly as the app runs them over a fold's rows: none may throw on the absent explain, drafts and highlight, and what they yield is the badge, the sample cells and the summary the fold shows.
  const slim = shardA.find((unit) => needsNoVerdict(unit));
  for (const key of ['explain', 'drafts', 'highlight']) assert.equal(key in slim, false, key);
  assert.equal(needsNoVerdict(slim), true);
  const groups = renderGroupsOf(slim);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].configs, slim.configs);
  assert.deepEqual(secondarySeamsOf(slim), []);
  assert.deepEqual(onlyHereSeamSpans(slim), []);
  assert.equal(pairBand(slim, 'after', 88, manifest.fonts.after.upem), null);
  const separators = tokenSeparators(slim.notation_tokens);
  assert.equal(separators.map((sep, index) => sep + slim.notation_tokens[index]).join(''), slim.notation);
  const runs = tokenMarkRuns(slim.notation_tokens, separators, slim.pair_codepoints, []);
  assert.equal(runs.map((run) => run.text).join(''), slim.notation);
  assert.ok(runs.some((run) => run.pair), 'the judged pair still underlines on the text lines');
  assert.deepEqual(configGateChips(slim, manifest.feature_descriptions).map((chip) => chip.text), [slim.config_note]);
  assert.ok(searchHaystack(slim).includes(slim.id.toLowerCase()), 'the haystack is lowercase; the search lowercases the query to match');
  assert.ok(typeof slim.summary === 'string' && slim.summary.startsWith('New: '));
  assert.ok(slim.after.cells.length > 0 && slim.after.seams.length > 0);
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
  const unit = shardA.find((candidate) => candidate.id === 'u-5vrBNy2RYrJ');
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

test('orderWorklist sorts by family-pair group then id by default', () => {
  const units = [
    { id: 'u-3S9VGa388F8', group: 'qsTea:qsOy' },
    { id: 'u-fyt9pUaPbr6', group: 'qsMay:qsNo' },
    { id: 'u-5vrBNy2RYrJ', group: 'qsTea:qsOy' },
  ];
  assert.deepEqual(orderWorklist(units, null).map((u) => u.id), ['u-fyt9pUaPbr6', 'u-3S9VGa388F8', 'u-5vrBNy2RYrJ']);
  assert.deepEqual(units.map((u) => u.id), ['u-3S9VGa388F8', 'u-fyt9pUaPbr6', 'u-5vrBNy2RYrJ'], 'the default sort must not mutate the given list');
});

test('orderWorklist preserves the given order under order=given', () => {
  const units = [
    { id: 'u-3S9VGa388F8', group: 'qsTea:qsOy' },
    { id: 'u-fyt9pUaPbr6', group: 'qsMay:qsNo' },
    { id: 'u-5vrBNy2RYrJ', group: 'qsTea:qsOy' },
  ];
  assert.deepEqual(orderWorklist(units, 'given').map((u) => u.id), ['u-3S9VGa388F8', 'u-fyt9pUaPbr6', 'u-5vrBNy2RYrJ']);
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

test('needsNoVerdict reads the machine channels and the exemption, never a batch', () => {
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

// The app index drops the four machine-channel flags because a unit carrying a batch is provably not machine-approved and not exempt; these pin that the readers behave the same over a row that lacks them as over a whole shard record that carries them false.
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
    assert.deepEqual(secondarySeamsOf(row), secondarySeamsOf(unit), unit.id);
    assert.deepEqual(onlyHereSeamSpans(row), onlyHereSeamSpans(unit), unit.id);
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

test('machineFoldTotal counts what a class fold will hold before the class is fetched', () => {
  const marker = manifest.classes.find((cls) => cls.id === 'marker-staging-ligature-formation');
  const dangling = manifest.classes.find((cls) => cls.id === 'dangling-anchor-dropped');
  assert.equal(machineFoldTotal(marker), 1);
  assert.equal(machineFoldTotal(dangling), 0, 'a class with nothing machine-approved folds nothing');
  assert.equal(machineFoldTotal({ no_verdict: true, unit_count: 6344, machine_approved_count: 4465 }), 6344);
  assert.equal(machineFoldTotal({ no_verdict: false, unit_count: 9 }), 0, 'a countless class folds nothing');
});

test('machineFoldChannel picks the narrowest channel that accounts for every machine unit', () => {
  const cls = (over) => ({ no_verdict: false, unit_count: 10, machine_approved_count: 10, ...over });
  assert.equal(
    machineFoldChannel(cls({ machine_channels: { ink_identical: 10, picture_identical: 0, junior_equivalent: 0 } })),
    'ink_identical',
  );
  assert.equal(
    machineFoldChannel(cls({ machine_channels: { ink_identical: 6, picture_identical: 4, junior_equivalent: 0 } })),
    'picture_identical',
  );
  assert.equal(
    machineFoldChannel(cls({ machine_channels: { ink_identical: 6, picture_identical: 1, junior_equivalent: 3 } })),
    'junior_equivalent',
  );
  assert.equal(
    machineFoldChannel({
      no_verdict: true,
      unit_count: 10,
      machine_approved_count: 4,
      machine_channels: { ink_identical: 4, picture_identical: 0, junior_equivalent: 0 },
    }),
    'no_verdict',
    'a no-verdict class folds its exempt units too, so no machine channel accounts for all of them',
  );
});

test('machineFoldChannel falls back to the no-verdict badge for a class carrying no channel split', () => {
  assert.equal(machineFoldChannel({ no_verdict: false, unit_count: 10, machine_approved_count: 10 }), 'no_verdict');
  assert.equal(machineFoldChannel({ no_verdict: false, unit_count: 10, machine_approved_count: 0 }), 'no_verdict');
  const marker = manifest.classes.find((cls) => cls.id === 'marker-staging-ligature-formation');
  assert.equal(machineFoldChannel(marker), 'ink_identical');
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
  assert.deepEqual(machineChannels(manifest), { units: 1, inkIdentical: 1, pictureIdentical: 0, juniorEquivalent: 0 });
  const channelled = {
    machine_approved: {
      units: 11926,
      channels: { ink_identical: { units: 8350 }, junior_equivalent: { units: 3576 } },
    },
  };
  assert.deepEqual(machineChannels(channelled), { units: 11926, inkIdentical: 8350, pictureIdentical: 0, juniorEquivalent: 3576 });
  const threeWay = {
    machine_approved: {
      units: 13126,
      channels: { ink_identical: { units: 8350 }, picture_identical: { units: 1200 }, junior_equivalent: { units: 3576 } },
    },
  };
  assert.deepEqual(machineChannels(threeWay), { units: 13126, inkIdentical: 8350, pictureIdentical: 1200, juniorEquivalent: 3576 });
  assert.deepEqual(machineChannels({}), { units: 0, inkIdentical: 0, pictureIdentical: 0, juniorEquivalent: 0 });
});

test('the collapsed chip carries the surface total and the popover breaks it down to the human workload', () => {
  assert.equal(surfaceChipLabel(manifest), '6 units');
  assert.deepEqual(
    surfaceDetailRows(manifest).map((row) => [row.label, row.value]),
    [
      ['Surface', '6'],
      ['ink-identical machine-approved', '1'],
      ['for human review', '5'],
    ],
  );
  const channelled = {
    totals: { units: 15960 },
    machine_approved: {
      units: 11926,
      method: 'Shaped in both fonts and compared.',
      channels: { ink_identical: { units: 8350 }, junior_equivalent: { units: 3576 } },
    },
    classes: [
      { id: 'boundary-echo', no_verdict: true, unit_count: 6256, machine_approved_count: 4940 },
      { id: 'x', no_verdict: false, unit_count: 9704, machine_approved_count: 6986 },
    ],
  };
  assert.equal(surfaceChipLabel(channelled), '15,960 units');
  assert.deepEqual(
    surfaceDetailRows(channelled).map((row) => [row.label, row.value, row.sub ?? false]),
    [
      ['Surface', '15,960', false],
      ['machine-approved', '11,926', false],
      ['ink-identical', '8,350', true],
      ['junior-equivalent', '3,576', true],
      ['in no-verdict classes', '1,316', false],
      ['for human review', '2,718', false],
    ],
  );
  const rows = surfaceDetailRows({ totals: { units: 4 }, machine_approved: { units: 0 }, classes: [] });
  assert.deepEqual(
    rows.map((row) => row.label),
    ['Surface', 'for human review'],
  );
});

test('a third machine channel takes its own sub row, and a lone channel merges back into the total row', () => {
  const surface = {
    totals: { units: 15960 },
    classes: [
      { id: 'boundary-echo', no_verdict: true, unit_count: 6256, machine_approved_count: 4940 },
      { id: 'x', no_verdict: false, unit_count: 9704, machine_approved_count: 6986 },
    ],
  };
  const threeWay = {
    ...surface,
    machine_approved: {
      units: 13126,
      method: 'Shaped in both fonts and compared.',
      channels: { ink_identical: { units: 8350 }, picture_identical: { units: 1200 }, junior_equivalent: { units: 3576 } },
    },
  };
  assert.deepEqual(
    surfaceDetailRows(threeWay).map((row) => [row.label, row.value, row.sub ?? false]),
    [
      ['Surface', '15,960', false],
      ['machine-approved', '13,126', false],
      ['ink-identical', '8,350', true],
      ['picture-identical', '1,200', true],
      ['junior-equivalent', '3,576', true],
      ['in no-verdict classes', '1,316', false],
      ['for human review', '2,718', false],
    ],
  );
  const pictureOnly = {
    ...surface,
    machine_approved: {
      units: 3,
      method: 'Rasterized in both fonts and compared cell by cell.',
      channels: { ink_identical: { units: 0 }, picture_identical: { units: 3 }, junior_equivalent: { units: 0 } },
    },
  };
  assert.deepEqual(
    surfaceDetailRows(pictureOnly).map((row) => [row.label, row.value, row.sub ?? false]),
    [
      ['Surface', '15,960', false],
      ['picture-identical machine-approved', '3', false],
      ['in no-verdict classes', '1,316', false],
      ['for human review', '2,718', false],
    ],
  );
  const juniorOnly = {
    ...surface,
    machine_approved: {
      units: 3576,
      method: 'Compared against the Junior font.',
      channels: { ink_identical: { units: 0 }, picture_identical: { units: 0 }, junior_equivalent: { units: 3576 } },
    },
  };
  assert.deepEqual(
    surfaceDetailRows(juniorOnly).map((row) => [row.label, row.value, row.sub ?? false]),
    [
      ['Surface', '15,960', false],
      ['junior-equivalent machine-approved', '3,576', false],
      ['in no-verdict classes', '1,316', false],
      ['for human review', '2,718', false],
    ],
    'a channel with no units gets no row, so the one channel left names the total',
  );
});

test('the popover corner says how much of the alphabet has migrated', () => {
  assert.equal(surfaceAlphabetLabel({ alphabet: { migrated: 16, total: 44 } }), '16 of 44 letters');
  assert.equal(surfaceAlphabetLabel({}), null);
});

test('the popover stamp names the generation and the head it was generated at', () => {
  assert.equal(
    surfaceStampLine({ generated_at: '2026-06-10T17:02:11Z', repo_head: 'abc1234' }),
    'Generated 2026-06-10T17:02:11Z at abc1234',
  );
  assert.equal(surfaceStampLine({ generated_at: '2026-06-10T17:02:11Z' }), 'Generated 2026-06-10T17:02:11Z');
  assert.equal(surfaceStampLine({}), null);
});

test('the machine-approved tooltip leads with the channel split, then the verification method', () => {
  const channelled = {
    machine_approved: {
      units: 11926,
      method: 'Shaped in both fonts and compared.',
      channels: { ink_identical: { units: 8350 }, junior_equivalent: { units: 3576 } },
    },
  };
  assert.equal(
    machineTitle(channelled),
    '8,350 ink-identical + 3,576 junior-equivalent. Shaped in both fonts and compared.',
  );
  const methodless = { machine_approved: { units: 11926, channels: channelled.machine_approved.channels } };
  assert.equal(machineTitle(methodless), '8,350 ink-identical + 3,576 junior-equivalent.');
  const threeWay = {
    machine_approved: {
      units: 13126,
      method: 'Shaped in both fonts and compared.',
      channels: { ink_identical: { units: 8350 }, picture_identical: { units: 1200 }, junior_equivalent: { units: 3576 } },
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
      channels: { ink_identical: { units: 0 }, picture_identical: { units: 3 }, junior_equivalent: { units: 0 } },
    },
  };
  assert.equal(machineTitle(pictureOnly), pictureOnly.machine_approved.method, 'a lone channel needs no split line');
  assert.equal(machineTitle(manifest), manifest.machine_approved.method);
  assert.equal(machineTitle({}), '');
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
  assert.deepEqual(
    [...classesInBatch(withExempt, 0, false)].sort(),
    ['dangling-anchor-dropped', 'marker-staging-ligature-formation'],
    'with machine rows hidden a batchless class contributes nothing to batch 0',
  );
});

test('copyPreamble names only the unit, codepoints, and notation — the rest is looked up from the shards', () => {
  const text = copyPreamble(shardB.find((unit) => unit.id === 'u-JSRuJ51yvVj'));
  assert.match(text, /rebuild\/out\/review\/ unit u-JSRuJ51yvVj/);
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
  const homed = shardB.find((unit) => unit.id === 'u-JSRuJ51yvVj');
  assert.equal(secondarySeamsOf(homed).length, 1);
  assert.equal(secondarySeamsOf(homed)[0].home, 'u-3S9VGa388F8');
  const legacy = { ink_identical: false };
  assert.deepEqual(secondarySeamsOf(legacy), []);
  const nulled = { ink_identical: false, secondary_seams: null };
  assert.deepEqual(secondarySeamsOf(nulled), []);
  const machine = { ink_identical: true, secondary_seams: [{ home: 'u-3S9VGa388F8' }] };
  assert.deepEqual(secondarySeamsOf(machine), [], 'machine-approved renderings never show seam markers');
  const picture = { picture_identical: true, secondary_seams: [{ home: 'u-3S9VGa388F8' }] };
  assert.deepEqual(secondarySeamsOf(picture), [], 'picture identity is a whole-window property, so it hides them too');
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
    { pair: { left: 2, right: 3 }, home: 'u-5vrBNy2RYrJ' },
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
  assert.deepEqual(onlyHereSeamSpans({ ...onlyHereUnit, picture_identical: true }), []);
  assert.deepEqual(onlyHereSeamSpans({ ...onlyHereUnit, after: {} }), []);
  assert.deepEqual(onlyHereSeamSpans({ ...onlyHereUnit, pair: null }), []);
  assert.deepEqual(onlyHereSeamSpans({ ...onlyHereUnit, pair_codepoints: [0, 2] }), []);
});

// Why the app index may ship `after: null` on a row whose secondary seams all have a home: with the cells present the walk computes the spans and then continues past every homed seam, so the two answers are the same [] and the row need not carry the cells at all.
test('onlyHereSeamSpans reads a homed-only row the same with cells present and with after nulled', () => {
  const homedOnly = {
    ...onlyHereUnit,
    secondary_seams: [{ pair: { left: 1, right: 2 }, home: 'u-5vrBNy2RYrJ' }],
  };
  assert.deepEqual(onlyHereSeamSpans(homedOnly), []);
  assert.deepEqual(onlyHereSeamSpans({ ...homedOnly, after: null }), []);
  const seamless = { ...onlyHereUnit, secondary_seams: null };
  assert.deepEqual(onlyHereSeamSpans(seamless), []);
  assert.deepEqual(onlyHereSeamSpans({ ...seamless, after: null }), []);
  assert.deepEqual(onlyHereSeamSpans({ ...onlyHereUnit, after: null }), [], 'a home-less seam needs its cells to underline');
});

test('the only-here fixture unit underlines its seam tokens', () => {
  const unit = shardB.find((entry) => entry.id === 'u-CKS1rpqQsLb');
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
    // A unit that takes no verdict ships slim: the build omits the three fields the fold never draws, keys absent rather than null, and a human unit is whole.
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
      assert.ok(['zwnj', 'space'].includes(mark.kind));
    }
    if (unit.secondary_seams != null) {
      assert.ok(Array.isArray(unit.secondary_seams) && unit.secondary_seams.length >= 1);
      assert.equal(unit.ink_identical, false);
      assert.notEqual(unit.picture_identical, true);
      for (const seam of unit.secondary_seams) {
        assert.ok(Number.isInteger(seam.pair.left) && Number.isInteger(seam.pair.right));
        assert.ok(seam.pair.left < seam.pair.right);
        assert.notDeepEqual(seam.pair, unit.pair, `${unit.id}: a secondary seam must not duplicate the primary pair`);
        for (const side of ['before', 'after']) {
          assert.ok(Number.isInteger(seam[side].x_min) && Number.isInteger(seam[side].x_max));
          assert.ok(seam[side].x_min <= seam[side].x_max);
          assert.ok(Number.isInteger(seam[side].advance_total));
        }
        assert.ok(seam.home === null || /^u-[1-9A-HJ-NP-Za-km-z]{11}$/.test(seam.home));
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

test('searchHaystack folds id, notation, codepoints, class, group, echo, cluster, and kinds into one lowercase string', () => {
  const haystack = searchHaystack(shardA.find((unit) => unit.id === 'u-5vrBNy2RYrJ'));
  assert.ok(haystack.includes('u-5vrbny2ryrj'), 'the id, folded to lowercase like everything else');
  assert.ok(haystack.includes('·tea·oy'));
  assert.ok(haystack.includes('teaoy'), 'notation with the namer dots stripped is searchable');
  assert.ok(haystack.includes('200c:e652:e679'));
  assert.ok(haystack.includes('200ce652e679'), 'codepoints with the colons stripped are searchable');
  assert.ok(haystack.includes('marker-staging-ligature-formation'));
  assert.ok(haystack.includes('qstea:qsoy'));
  assert.ok(haystack.includes('ligation'));
  assert.ok(haystack.includes('e-0001'), 'the echo group id is searchable');
  assert.ok(haystack.includes('c-3a570001'), 'the cluster signature id is searchable');
});

test('searchHaystack is memoized per unit, so a keystroke folds each row at most once', () => {
  // Rows are immutable once parsed; mutating one here is only a probe for whether the second call refolded it.
  const unit = { ...shardA[0] };
  const first = searchHaystack(unit);
  unit.notation = '·Nope';
  assert.equal(searchHaystack(unit), first, 'the second call is answered from the cache');
  const twin = { ...shardA[0], notation: '·Nope' };
  assert.notEqual(searchHaystack(twin), first, 'a row the cache has not seen folds on its own');
  assert.ok(searchHaystack(twin).includes('·nope'));
});

test('searchUnits finds units by echo group id and by cluster id', () => {
  assert.deepEqual(
    searchUnits(allUnits, 'e-0000').matches.map((unit) => unit.id).sort(),
    ['u-CKS1rpqQsLb', 'u-JSRuJ51yvVj'],
  );
  assert.deepEqual(
    searchUnits(allUnits, 'c-0da49c11').matches.map((unit) => unit.id).sort(),
    ['u-CKS1rpqQsLb', 'u-JSRuJ51yvVj'],
  );
});

test('searchUnits finds a unit by its exact id across every shard', () => {
  const { matches, total } = searchUnits(allUnits, 'u-CKS1rpqQsLb');
  assert.equal(total, 1);
  assert.equal(matches[0].id, 'u-CKS1rpqQsLb');
});

test('searchUnits matches notation with and without the namer dots, case-insensitively', () => {
  assert.deepEqual(
    searchUnits(allUnits, '·Pea·May').matches.map((unit) => unit.id),
    ['u-3S9VGa388F8'],
  );
  assert.deepEqual(
    searchUnits(allUnits, 'peamay').matches.map((unit) => unit.id),
    ['u-3S9VGa388F8'],
  );
});

test('searchUnits matches codepoints with and without the colons', () => {
  assert.deepEqual(searchUnits(allUnits, 'E66C').matches.map((unit) => unit.id), ['u-CKS1rpqQsLb']);
  assert.deepEqual(searchUnits(allUnits, 'e670e653').matches.map((unit) => unit.id), ['u-CKS1rpqQsLb']);
});

test('searchUnits matches class, group, and kind, and includes machine-approved units', () => {
  const byClass = searchUnits(allUnits, 'dangling-anchor-dropped');
  assert.deepEqual(byClass.matches.map((unit) => unit.id).sort(), ['u-CKS1rpqQsLb', 'u-JSRuJ51yvVj']);
  const extension = searchUnits(allUnits, 'extension');
  assert.deepEqual(extension.matches.map((unit) => unit.id), ['u-RNA7DFboKfW']);
  assert.equal(extension.matches[0].ink_identical, true, 'a machine-approved unit is still findable');
});

test('searchUnits requires every whitespace-separated token to match (AND)', () => {
  assert.deepEqual(searchUnits(allUnits, 'tea oy').matches.map((unit) => unit.id).sort(), ['u-5vrBNy2RYrJ', 'u-fyt9pUaPbr6']);
  assert.equal(searchUnits(allUnits, 'tea exam').total, 0);
});

test('searchUnits ranks an exact id hit ahead of incidental substring hits', () => {
  // "u-JSRuJ51yvVj" appears verbatim only in u-JSRuJ51yvVj, but a 3-codepoint substring could in principle collide; the exact-id rank keeps it first.
  const { matches } = searchUnits(allUnits, 'u-JSRuJ51yvVj');
  assert.equal(matches[0].id, 'u-JSRuJ51yvVj');
});

test('searchUnits caps the matches at the limit but reports the true total', () => {
  const { matches, total } = searchUnits(allUnits, 'u-', 2);
  assert.equal(total, 6, 'every fixture unit id starts with u-');
  assert.equal(matches.length, 2);
});

test('searchUnits returns nothing for a blank query', () => {
  assert.deepEqual(searchUnits(allUnits, '   '), { matches: [], total: 0 });
});
