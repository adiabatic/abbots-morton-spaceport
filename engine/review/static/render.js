export function featureSettingsValue(configToken) {
  if (!configToken || configToken === 'default') return 'normal';
  const settings = [];
  for (const part of configToken.split('+')) settings.push(`"${part}" 1`);
  return settings.join(', ');
}

export function configGateChips(unit, featureDescriptions) {
  const descriptions = featureDescriptions ?? {};
  const gate = Array.isArray(unit.config_gate) ? unit.config_gate : [];
  if (gate.length === 0) {
    return unit.config_note ? [{ feature: null, state: null, text: unit.config_note, detail: null }] : [];
  }
  return gate.map((clause) => ({
    feature: clause.feature,
    state: clause.state,
    text: clause.text,
    detail: descriptions[clause.feature] ?? null,
  }));
}

const DEFAULT_CONFIG = 'default';
const DEFAULT_CONFIG_GLOSS = 'no stylistic set on — the shipping default';

export function configFilterOptions(manifest) {
  const descriptions = manifest?.feature_descriptions ?? {};
  const configs = Array.isArray(manifest?.configs) ? manifest.configs : [];
  const options = [];
  for (const config of configs) {
    if (config === DEFAULT_CONFIG) {
      options.push({ value: config, label: config, title: DEFAULT_CONFIG_GLOSS });
      continue;
    }
    const glosses = [];
    for (const feature of config.split('+')) {
      const detail = descriptions[feature];
      glosses.push(detail ? `${feature} — ${detail}` : feature);
    }
    options.push({ value: config, label: config, title: glosses.join('\n') });
  }
  return options;
}

const STYLISTIC_SET_MENTION = /\bss\d{2}\b/gu;

export function explainRuns(text) {
  // The explain text is the engine's output, so the runs partition it without changing it: their concatenated text equals the input.
  if (typeof text !== 'string' || text === '') return [];
  const runs = [];
  let plainFrom = 0;
  for (const match of text.matchAll(STYLISTIC_SET_MENTION)) {
    if (match.index > plainFrom) runs.push({ text: text.slice(plainFrom, match.index), set: null });
    runs.push({ text: match[0], set: match[0] });
    plainFrom = match.index + match[0].length;
  }
  if (plainFrom < text.length) runs.push({ text: text.slice(plainFrom), set: null });
  return runs;
}

export function renderGroupsOf(unit) {
  const groups = [];
  for (const group of unit.render_groups) {
    groups.push({
      configs: [...group.configs],
      label: group.configs.join(', '),
      featureSettings: featureSettingsValue(group.configs[0]),
      primary: groups.length === 0,
    });
  }
  return groups;
}

export function highlightRect(highlight, fontSize, upem) {
  const scale = fontSize / upem;
  return { left: highlight.x_min * scale, width: (highlight.x_max - highlight.x_min) * scale };
}

// The amber band over the judged pair on one side, or null when the unit has no judged pair or no highlight for that side.
export function pairBand(unit, side, fontSize, upem) {
  const highlight = unit.highlight?.[side];
  if (unit.pair === null || !highlight) return null;
  return highlightRect(highlight, fontSize, upem);
}

export function markOffset(x, fontSize, upem) {
  return (x * fontSize) / upem;
}

// Chromium ends a shaping run at the namer dot (U+00B7), whose Script_Extensions leave out the letters' Unknown script, so the dot is shaped without seeing the letter after it, and the rule that lowers it before a Short letter (`emit_gsub.namer_dot_lookup`) never fires. The server's /fonts/browser/ copies map U+F8FF to the dot's glyph as well (`namer_dot_stand_in_font` in engine/review/serve.py), and the samples write U+F8FF in the dot's place, which keeps each sample in one run.
export const NAMER_DOT_STAND_IN = '';

export function namerDotStandIn(text) {
  return text.replaceAll('·', NAMER_DOT_STAND_IN);
}

export function namerDotStandInEntities(entities) {
  return entities.replace(/&#x0*b7;/giu, '&#xF8FF;');
}

const MACHINE_CHECKS = ['ink_identical', 'picture_identical'];

export function needsNoVerdict(unit) {
  // Matches audit.needs_no_verdict in the build: a unit approved by any machine check, or in a class that takes no verdict, takes no verdict. It reads the flags, not the batch, because only app-index rows carry a batch. Those rows are all human units and omit the flags, so they read as human here.
  return Boolean(unit) && (unit.no_verdict === true || MACHINE_CHECKS.some((check) => unit[check] === true));
}

// A human row's position in the manifest's triage index, the order the app pages in, or Infinity for a record without one.
export function triageOrder(unit) {
  return typeof unit?.order === 'number' ? unit.order : Number.POSITIVE_INFINITY;
}

export function familiesOfGroup(group) {
  return group ? group.split(':') : [];
}

export function unitWorklist(value) {
  return value ? value.split(',').map((id) => id.trim()).filter(Boolean) : [];
}

export function orderWorklist(units, order) {
  if (order === 'given') return units;
  // Sort by triage order where rows have it. Records without it (a worklist's machine records) sort after them by group, then id.
  return [...units].sort(
    (a, b) => triageOrder(a) - triageOrder(b) || a.group.localeCompare(b.group) || a.id.localeCompare(b.id),
  );
}

export function unitMatchesFilters(unit, filters, record) {
  if (filters.class && unit.class !== filters.class) return false;
  if (filters.group && unit.group !== filters.group) return false;
  if (filters.family && !familiesOfGroup(unit.group).includes(filters.family)) return false;
  if (filters.units && !unitWorklist(filters.units).includes(unit.id)) return false;
  if (filters.config && !unit.configs.includes(filters.config)) return false;
  if (filters.status && filters.unit !== unit.id) {
    if (filters.status === 'unverdicted') return !record;
    if (filters.status === 'verdicted') return Boolean(record);
    return Boolean(record) && record.verdict === filters.status;
  }
  return true;
}

export function partitionUnits(units, filters, recordOf) {
  const worklist = Boolean(filters.units);
  const effective = worklist ? { units: filters.units } : filters;
  const showMachine = filters.machine === '1' || worklist;
  const human = [];
  const machine = [];
  for (const unit of units) {
    if (needsNoVerdict(unit)) {
      if (showMachine && unitMatchesFilters(unit, { ...effective, status: null }, undefined)) {
        machine.push(unit);
      }
    } else if (unitMatchesFilters(unit, effective, recordOf(unit.id))) {
      human.push(unit);
    }
  }
  return { human, machine };
}

export function humanClassCount(cls) {
  if (cls.no_verdict) return 0;
  return cls.unit_count - (cls.machine_approved_count ?? 0);
}

// A class's units that take no verdict: all of a no-verdict class, else its machine-approved units. It comes from the manifest so a show-machine fold can show its size without fetching the class.
export function machineFoldTotal(cls) {
  return cls.no_verdict ? cls.unit_count : (cls.machine_approved_count ?? 0);
}

// The badge for that fold, from the manifest's per-class machine-check counts. Checks are tried from narrowest to widest (ink-identical, then picture-identical), and the first whose count plus the narrower checks' counts equals the fold total is the badge; otherwise the fold gets the no-verdict badge.
export function machineFoldCheck(cls) {
  const total = machineFoldTotal(cls);
  const checks = cls.machine_checks ?? {};
  const ink = checks.ink_identical ?? 0;
  const picture = checks.picture_identical ?? 0;
  if (total === 0) return 'no_verdict';
  if (ink === total) return 'ink_identical';
  if (ink + picture === total) return 'picture_identical';
  return 'no_verdict';
}

export function humanTotal(manifest) {
  let total = 0;
  for (const cls of manifest.classes) total += humanClassCount(cls);
  return total;
}

export function noVerdictTotal(manifest) {
  let total = 0;
  for (const cls of manifest.classes) {
    if (cls.no_verdict) total += cls.unit_count - (cls.machine_approved_count ?? 0);
  }
  return total;
}

export const NO_VERDICT_BADGE = 'no verdict needed';

const COUNT_FORMAT = new Intl.NumberFormat('en-US');

export function formatCount(value) {
  return COUNT_FORMAT.format(value);
}

export function unitCount(value) {
  return `${formatCount(value)} ${value === 1 ? 'unit' : 'units'}`;
}

// A row's date slot: the day its verdict was recorded.
export function verdictAtText(record) {
  if (!record?.at) return '';
  return `verdicted ${record.at.slice(0, 10)}`;
}

export function machineChecks(manifest) {
  const machine = manifest.machine_approved ?? {};
  const checks = machine.checks ?? {};
  return {
    units: machine.units ?? 0,
    inkIdentical: checks.ink_identical?.units ?? machine.units ?? 0,
    pictureIdentical: checks.picture_identical?.units ?? 0,
  };
}

const MACHINE_CHECK_ROWS = [
  ['ink_identical', 'inkIdentical', 'ink-identical'],
  ['picture_identical', 'pictureIdentical', 'picture-identical'],
];

function machineCheckSplit(manifest) {
  const counts = machineChecks(manifest);
  const checks = manifest.machine_approved?.checks ?? {};
  const split = [];
  for (const [key, countKey, label] of MACHINE_CHECK_ROWS) {
    const units = counts[countKey];
    if (units > 0) split.push({ label, units, method: checks[key]?.method ?? '' });
  }
  return split;
}

export function corpusChipLabel(manifest) {
  return `${formatCount(manifest.totals.units)} units`;
}

export function corpusAlphabetLabel(manifest) {
  const alphabet = manifest.alphabet;
  if (!alphabet) return null;
  return `${formatCount(alphabet.migrated)} of ${formatCount(alphabet.total)} letters`;
}

export function corpusStampLine(manifest) {
  if (!manifest.generated_at) return null;
  return manifest.repo_head
    ? `Generated ${manifest.generated_at} at ${manifest.repo_head}`
    : `Generated ${manifest.generated_at}`;
}

const NO_VERDICT_DETAIL_TITLE =
  'Units of a class that takes no verdict never need individual verdicts; select their class in the sidebar to read why.';

export function corpusDetailRows(manifest) {
  const rows = [{ label: 'Corpus', value: formatCount(manifest.totals.units) }];
  const { units } = machineChecks(manifest);
  if (units > 0) {
    const split = machineCheckSplit(manifest);
    const label = split.length === 1 ? `${split[0].label} machine-approved` : 'machine-approved';
    rows.push({ label, value: formatCount(units), title: machineTitle(manifest) });
    if (split.length > 1) {
      for (const check of split) {
        rows.push({ label: check.label, value: formatCount(check.units), sub: true, title: check.method });
      }
    }
  }
  const exempt = noVerdictTotal(manifest);
  if (exempt > 0) rows.push({ label: NO_VERDICT_BADGE, value: formatCount(exempt), title: NO_VERDICT_DETAIL_TITLE });
  rows.push({ label: 'for human review', value: formatCount(humanTotal(manifest)), title: 'The Overall denominator: everything the corpus leaves to a person.' });
  return rows;
}

export function machineTitle(manifest) {
  const { units } = machineChecks(manifest);
  const method = manifest.machine_approved?.method ?? '';
  const split = machineCheckSplit(manifest);
  if (units === 0 || split.length < 2) return method;
  const parts = `${split.map((check) => `${formatCount(check.units)} ${check.label}`).join(' + ')}.`;
  return method ? `${parts} ${method}` : parts;
}

// A class's verdict tallies from the human rows in one pass: `done` counts real verdicts, `skipped` the skips (which leave a unit to review, as the Queue counts it), and `rejected` the rejects among the done.
export function classVerdictTallies(rows, recordOf) {
  const tallies = new Map();
  for (const row of rows) {
    const record = recordOf(row.id);
    if (!record) continue;
    let tally = tallies.get(row.class);
    if (!tally) {
      tally = { done: 0, skipped: 0, rejected: 0 };
      tallies.set(row.class, tally);
    }
    if (record.verdict === 'skip') tally.skipped += 1;
    else {
      tally.done += 1;
      if (record.verdict === 'reject') tally.rejected += 1;
    }
  }
  return tallies;
}

export function classLeft(cls, tally = null) {
  return Math.max(humanClassCount(cls) - (tally?.done ?? 0), 0);
}

export function classIsDone(cls, tally = null) {
  return tally !== null && humanClassCount(cls) > 0 && classLeft(cls, tally) === 0;
}

export function classCountsLine(cls, tally = null) {
  if (cls.no_verdict) return `${unitCount(cls.unit_count)} · ${NO_VERDICT_BADGE}`;
  const human = humanClassCount(cls);
  if (human === 0) return `${unitCount(cls.unit_count)} · all machine-approved`;
  const done = tally?.done ?? 0;
  const skipped = tally?.skipped ?? 0;
  const left = classLeft(cls, tally);
  if (classIsDone(cls, tally)) return `all ${formatCount(done)} done`;
  const skips = skipped > 0 ? ` (${formatCount(skipped)} skipped)` : '';
  const parts = [`${formatCount(left)} to review${skips}`];
  if (done > 0) parts.push(`${formatCount(done)} done`);
  if (cls.machine_approved_count) parts.push(`${formatCount(cls.machine_approved_count)} machine-approved`);
  return parts.join(' · ');
}

export function nextUnverdictedIndex(unitIds, fromIndex, hasVerdict) {
  const total = unitIds.length;
  for (let step = 1; step <= total; step += 1) {
    const index = (fromIndex + step) % total;
    if (!hasVerdict(unitIds[index])) return index;
  }
  return -1;
}

export function stepIndex(length, fromIndex, delta) {
  if (length === 0) return -1;
  const next = fromIndex + delta;
  if (next < 0) return 0;
  if (next >= length) return length - 1;
  return next;
}

export function availableBatches(manifest, classId) {
  if (classId) {
    const cls = manifest.classes.find((entry) => entry.id === classId);
    return cls ? [...cls.batches] : [];
  }
  const batches = [];
  for (let index = 0; index < manifest.totals.batches; index += 1) batches.push(index);
  return batches;
}

export function classesInBatch(manifest, batch, showMachine = true) {
  // Selects classes as machineFoldPlan does: the classes in the batch, plus the batchless classes in batch 0 when machine units are shown, since those classes hold only units that take no verdict.
  const ids = new Set();
  for (const cls of manifest.classes) {
    if (cls.batches.includes(batch)) ids.add(cls.id);
    else if (cls.batches.length === 0 && batch === 0 && showMachine) ids.add(cls.id);
  }
  return ids;
}

export function copyPreamble(unit) {
  return `I'm looking at engine/out/review/ unit ${unit.id} — ${unit.codepoints ?? 'no text'} (${unit.notation}). `;
}

// Keyed on the unit object, which is not modified after parsing, so an entry cannot go stale and is collected with its row. Without the cache every keystroke would rebuild and lowercase one string per unit in the queue.
const haystacks = new WeakMap();

// `titleOf` maps a class id to its title, so a class is found by its title as well as its id. It is the same function for the life of the page, so the cache can ignore it.
export function searchHaystack(unit, titleOf = null) {
  const cached = haystacks.get(unit);
  if (cached !== undefined) return cached;
  const codepoints = unit.codepoints ?? '';
  const parts = [
    unit.id,
    unit.notation,
    (unit.notation ?? '').replaceAll('·', ''),
    codepoints,
    codepoints.replaceAll(':', ''),
    unit.class,
    titleOf?.(unit.class) ?? '',
    unit.group,
    unit.cluster ?? '',
    ...(unit.kinds ?? []),
  ];
  const haystack = parts.join(' ').toLowerCase();
  haystacks.set(unit, haystack);
  return haystack;
}

function searchScore(unit, tokens, query) {
  const id = unit.id.toLowerCase();
  if (id === query) return 0;
  if (id.startsWith(query) || tokens.some((token) => id.startsWith(token))) return 1;
  if ((unit.notation ?? '').toLowerCase().includes(query)) return 2;
  return 3;
}

export function searchUnits(units, query, limit = 50, titleOf = null) {
  const trimmed = (query ?? '').trim().toLowerCase();
  if (!trimmed) return { matches: [], total: 0 };
  const tokens = trimmed.split(/\s+/u);
  const ranked = [];
  for (const unit of units) {
    const haystack = searchHaystack(unit, titleOf);
    if (tokens.every((token) => haystack.includes(token))) {
      ranked.push({ unit, score: searchScore(unit, tokens, trimmed) });
    }
  }
  ranked.sort((a, b) => a.score - b.score || a.unit.id.localeCompare(b.unit.id));
  return { matches: ranked.slice(0, limit).map((entry) => entry.unit), total: ranked.length };
}

export function isLetterToken(token) {
  return typeof token === 'string' && token.length > 1 && token.startsWith('·');
}

export function tokenSeparators(tokens) {
  // Follows the build's notation() spacing (engine/review/enrich.py): letters concatenate and boundary tokens (◊ZWNJ, ␣, the bare namer dot ·) are separated by spaces, so joining separators[i] + tokens[i] reproduces unit.notation.
  const separators = [];
  let previousWasLetter = false;
  for (const token of tokens) {
    const letter = isLetterToken(token);
    separators.push(separators.length === 0 ? '' : letter && previousWasLetter ? '' : ' ');
    previousWasLetter = letter;
  }
  return separators;
}

export function tokenMarkRuns(tokens, separators, pairSpan) {
  // A separator is marked only when both its neighbors are, so the separator before the first marked token stays outside the mark. Adjacent pieces with the same mark merge into one run.
  const markedAt = (index) => Boolean(pairSpan) && index >= pairSpan[0] && index <= pairSpan[1];
  const runs = [];
  const push = (text, pair) => {
    if (!text) return;
    const last = runs.at(-1);
    if (last && last.pair === pair) last.text += text;
    else runs.push({ text, pair });
  };
  for (const [index, token] of tokens.entries()) {
    const pair = markedAt(index);
    if (index > 0) push(separators[index], pair && markedAt(index - 1));
    push(token, pair);
  }
  return runs;
}

export const SECTION_ORDER = ['open-questions', 'no-review-needed'];

export const SECTION_LABELS = {
  'open-questions': {
    title: 'Open questions',
    open: true,
    legend: 'Your verdicts decide these changes, and an accept makes them the accepted build. The Queue draws only from here.',
  },
  'no-review-needed': {
    title: 'No review needed',
    open: false,
    legend: 'The class takes no verdict, or the machine approves every unit because it draws exactly as in the accepted build. Their units stay hidden unless “Show units that need no review” is on.',
  },
};

export function classInfo(manifest) {
  const info = new Map();
  for (const cls of manifest?.classes ?? []) info.set(cls.id, cls);
  return info;
}

export function classTitle(cls, id = cls?.id) {
  return cls?.title || id || '';
}

export function classChip(id, info) {
  const cls = info?.get(id) ?? null;
  return {
    text: classTitle(cls, id),
    title: cls?.summary ? `${id} — ${plainText(cls.summary)}` : id,
  };
}

// Splits copy on backtick spans, which class prose uses for data-expect notation, so the app builds each span as a <code> element instead of parsing markup. An unpaired backtick stays as text.
export function codeRuns(text) {
  if (typeof text !== 'string' || text === '') return [];
  const runs = [];
  let from = 0;
  for (const match of text.matchAll(/`([^`]+)`/gu)) {
    if (match.index > from) runs.push({ text: text.slice(from, match.index), code: false });
    runs.push({ text: match[1], code: true });
    from = match.index + match[0].length;
  }
  if (from < text.length) runs.push({ text: text.slice(from), code: false });
  return runs;
}

export function plainText(text) {
  return codeRuns(text)
    .map((run) => run.text)
    .join('');
}

export function appendCodeRuns(target, text, doc = target.ownerDocument) {
  for (const run of codeRuns(text)) {
    if (run.code) {
      const code = doc.createElement('code');
      code.textContent = run.text;
      target.append(code);
    } else {
      target.append(doc.createTextNode(run.text));
    }
  }
  return target;
}

export function rationaleParagraphs(why) {
  if (typeof why !== 'string') return [];
  return why
    .split(/\n+/u)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function sidebarSections(manifest) {
  const sections = SECTION_ORDER.map((id) => ({ id, ...SECTION_LABELS[id], entries: [] }));
  const byId = new Map(sections.map((section) => [section.id, section]));
  for (const cls of manifest?.classes ?? []) byId.get(cls.section)?.entries.push(cls);
  return sections;
}

export function sectionTotalsLine(sectionId, classes, tallies = null) {
  const count = formatCount(classes.length);
  if (sectionId === 'no-review-needed') {
    let units = 0;
    for (const cls of classes) units += cls.unit_count;
    return `${count} · ${unitCount(units)}`;
  }
  let left = 0;
  for (const cls of classes) left += classLeft(cls, tallies?.get(cls.id) ?? null);
  return `${count} · ${formatCount(left)} to review`;
}

export function classSectionSentence(cls) {
  if (cls.section === 'no-review-needed') {
    return `Nothing to judge: the class takes no verdict, or the machine approves ${cls.unit_count === 1 ? 'its one unit' : `all ${unitCount(cls.unit_count)}`}.`;
  }
  return 'Judge each unit; units you have already judged count as done. The Queue draws from here.';
}

export function classProgressLine(cls, tally = null) {
  return `“${classTitle(cls)}”: ${formatCount(classLeft(cls, tally))} to review of ${formatCount(humanClassCount(cls))}`;
}

