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

const STYLISTIC_SET_ATTRIBUTE = 'data-stylistic-set';

export function pinStylisticSetScope(stylisticSet, featureDescriptions) {
  const numbers = typeof stylisticSet === 'string' ? stylisticSet.trim().split(/\s+/u).filter(Boolean) : [];
  if (numbers.length === 0) return null;
  const padded = numbers.map((number) => number.padStart(2, '0'));
  const sets = padded.map((number) => `ss${number}`);
  const descriptions = featureDescriptions ?? {};
  const glosses = sets.map((set) => (descriptions[set] ? `${set} — ${descriptions[set]}` : set));
  const attribute = `${STYLISTIC_SET_ATTRIBUTE}="${padded.join(' ')}"`;
  return {
    sets,
    label: sets.join('+'),
    attribute,
    title: [
      `This divergence only holds under ${sets.join(' + ')}, so the pin belongs on a corpus cell carrying ${attribute}.`,
      ...glosses,
    ].join('\n'),
  };
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
  const raw =
    Array.isArray(unit.render_groups) && unit.render_groups.length > 0
      ? unit.render_groups
      : [{ configs: unit.configs }];
  const groups = [];
  for (const group of raw) {
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

// The amber band over the judged pair on one side, or null. A unit with no judged pair has none, and neither does a slim fragment, which has no `highlight` at all, so a show-machine fold draws its rows from the cells and junctions alone.
export function pairBand(unit, side, fontSize, upem) {
  const highlight = unit.highlight?.[side];
  if (unit.pair === null || !highlight) return null;
  return highlightRect(highlight, fontSize, upem);
}

export function markOffset(x, fontSize, upem) {
  return (x * fontSize) / upem;
}

export function secondaryJunctionsOf(unit) {
  if (unit.ink_identical || unit.picture_identical) return [];
  return Array.isArray(unit.secondary_junctions) ? unit.secondary_junctions : [];
}

export function junctionChip(junction) {
  if (junction.primary_unit) {
    return {
      primaryUnit: junction.primary_unit,
      label: junction.primary_unit,
      title: `This dim band is a secondary divergent junction; its behavior is judged at its primary unit ${junction.primary_unit}. Click to jump there.`,
    };
  }
  return {
    primaryUnit: null,
    label: 'only here',
    title:
      'This secondary divergent junction has no shorter primary unit where the same behavior is the primary judgment, so judge it in this unit.',
  };
}

export function cellCodepointSpans(cells) {
  // Follows the build's after spans behind pair_codepoints (`formed_spans` in rebuild/review/enrich.py): a settled cell covers one codepoint position, and a formed ligature (an underscore in its rune segment, qsX_qsY) covers two.
  const spans = [];
  let position = 0;
  for (const cell of cells) {
    const width = cell.split('/')[0].includes('_') ? 2 : 1;
    spans.push([position, position + width - 1]);
    position += width;
  }
  return spans;
}

export function onlyHereJunctionSpans(unit) {
  // Checked against the build's pair_codepoints first. If the spans derived from the cells disagree with it, no only-here junction is underlined, so the text lines never underline the wrong letters. The pair mark, which tokenMarkRuns takes from pair_codepoints, is unaffected.
  const cells = unit.after?.cells;
  if (!Array.isArray(cells) || !unit.pair || !unit.pair_codepoints) return [];
  const spans = cellCodepointSpans(cells);
  const derived = [spans[unit.pair.left]?.[0], spans[unit.pair.right]?.[1]];
  if (derived[0] !== unit.pair_codepoints[0] || derived[1] !== unit.pair_codepoints[1]) return [];
  const result = [];
  for (const junction of secondaryJunctionsOf(unit)) {
    if (junction.primary_unit !== null) continue;
    const left = spans[junction.pair.left];
    const right = spans[junction.pair.right];
    if (left && right) result.push([left[0], right[1]]);
  }
  return result;
}

const MACHINE_CHECKS = ['ink_identical', 'picture_identical', 'junior_equivalent'];

export function needsNoVerdict(unit) {
  // Matches audit.slim_fragment in the build: a unit approved by any machine check, or in a no-verdict ledger class, takes no verdict. It reads the flags, not the batch, because only app-index rows carry a batch. Those rows are all human units and omit the flags, so they read as human here.
  return Boolean(unit) && (unit.no_verdict === true || MACHINE_CHECKS.some((check) => unit[check] === true));
}

// A human row's position in the manifest's triage index, the order the app pages in, or Infinity for a record without one.
export function triageOrder(unit) {
  return typeof unit?.order === 'number' ? unit.order : Number.POSITIVE_INFINITY;
}

export function duplicateChip(unit, memberIds, kindTitle = unit.class) {
  if (!unit.duplicate_group || !Array.isArray(memberIds) || memberIds.length < 2) return null;
  return {
    label: `duplicate ×${formatCount(memberIds.length)}`,
    href: `#units=${memberIds.join(',')}`,
    title:
      `The before→after change here is pixel-identical in ${formatCount(memberIds.length)} windows of “${kindTitle}” with the same judged pair and configs — ` +
      'the surrounding letters differ but the change is the same picture. A verdict on any of them fills the unverdicted rest ' +
      '(each can still be overridden or cleared individually). Click to view the whole duplicate group stacked.',
  };
}

export function duplicateFillTargets(unit, memberIds, hasVerdict) {
  if (!unit || !unit.duplicate_group || !Array.isArray(memberIds)) return [];
  return memberIds.filter((id) => id !== unit.id && !hasVerdict(id));
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

// The badge for that fold, from the manifest's per-class machine-check counts. Checks are tried from narrowest to widest (ink-identical, picture-identical, Junior-equivalent), and the first whose count plus the narrower checks' counts equals the fold total is the badge; otherwise the fold gets the no-verdict badge.
export function machineFoldCheck(cls) {
  const total = machineFoldTotal(cls);
  const checks = cls.machine_checks ?? {};
  const ink = checks.ink_identical ?? 0;
  const picture = checks.picture_identical ?? 0;
  const junior = checks.junior_equivalent ?? 0;
  if (total === 0) return 'no_verdict';
  if (ink === total) return 'ink_identical';
  if (ink + picture === total) return 'picture_identical';
  if (ink + picture + junior === total) return 'junior_equivalent';
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

export function machineChecks(manifest) {
  const machine = manifest.machine_approved ?? {};
  const checks = machine.checks ?? {};
  return {
    units: machine.units ?? 0,
    inkIdentical: checks.ink_identical?.units ?? machine.units ?? 0,
    pictureIdentical: checks.picture_identical?.units ?? 0,
    juniorEquivalent: checks.junior_equivalent?.units ?? 0,
  };
}

const MACHINE_CHECK_ROWS = [
  ['ink_identical', 'inkIdentical', 'ink-identical'],
  ['picture_identical', 'pictureIdentical', 'picture-identical'],
  ['junior_equivalent', 'juniorEquivalent', 'junior-equivalent'],
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
  'Units covered by a ruling are decided as a whole by that ruling and never need individual verdicts; select their class in the sidebar to read the ruling.';

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
  if (exempt > 0) rows.push({ label: 'covered by a ruling', value: formatCount(exempt), title: NO_VERDICT_DETAIL_TITLE });
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
  if (cls.no_verdict) return `${unitCount(cls.unit_count)} · covered by a ruling`;
  const human = humanClassCount(cls);
  if (human === 0) return `${unitCount(cls.unit_count)} · all machine-approved`;
  const done = tally?.done ?? 0;
  const skipped = tally?.skipped ?? 0;
  const rejected = tally?.rejected ?? 0;
  const spotCheck = cls.section === 'spot-check';
  const rejects = spotCheck && rejected > 0 ? ` (${formatCount(rejected)} rejected)` : '';
  const left = classLeft(cls, tally);
  if (classIsDone(cls, tally)) return `all ${formatCount(done)} done${rejects}`;
  const skips = skipped > 0 ? ` (${formatCount(skipped)} skipped)` : '';
  const parts = [`${formatCount(left)} ${spotCheck ? 'to confirm' : 'to review'}${skips}`];
  if (done > 0) parts.push(`${formatCount(done)} done${rejects}`);
  if (!spotCheck && cls.machine_approved_count) parts.push(`${formatCount(cls.machine_approved_count)} machine-approved`);
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
  return `I'm looking at rebuild/out/review/ unit ${unit.id} — ${unit.codepoints} (${unit.notation}). `;
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
    unit.ledger_class ?? '',
    unit.group,
    unit.duplicate_group ?? '',
    unit.cluster ?? '',
    unit.config_class_note ?? '',
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

export function duplicateGroupOfQuery(query, duplicateIndex) {
  // Duplicate-group ids are content-addressed base58, where case is significant, so the lookup keeps the query's case.
  const id = (query ?? '').trim();
  const members = duplicateIndex.get(id);
  return members ? { id, members } : null;
}

export function isLetterToken(token) {
  return typeof token === 'string' && token.length > 1 && token.startsWith('·');
}

export function tokenSeparators(tokens) {
  // Follows the build's notation() spacing (rebuild/review/enrich.py): letters concatenate and boundary tokens (◊ZWNJ, ␣, the bare namer dot ·) are separated by spaces, so joining separators[i] + tokens[i] reproduces unit.notation.
  const separators = [];
  let previousWasLetter = false;
  for (const token of tokens) {
    const letter = isLetterToken(token);
    separators.push(separators.length === 0 ? '' : letter && previousWasLetter ? '' : ' ');
    previousWasLetter = letter;
  }
  return separators;
}

export function tokenMarkRuns(tokens, separators, pairSpan, junctionSpans) {
  // A separator is marked only when both its neighbors are, so the separator before the first marked token stays outside the mark. Adjacent pieces with the same marks merge into one run.
  const marksAt = (index) => ({
    pair: Boolean(pairSpan) && index >= pairSpan[0] && index <= pairSpan[1],
    junction: junctionSpans.some((span) => index >= span[0] && index <= span[1]),
  });
  const runs = [];
  const push = (text, pair, junction) => {
    if (!text) return;
    const last = runs.at(-1);
    if (last && last.pair === pair && last.junction === junction) last.text += text;
    else runs.push({ text, pair, junction });
  };
  for (const [index, token] of tokens.entries()) {
    const marks = marksAt(index);
    if (index > 0) {
      const previous = marksAt(index - 1);
      push(separators[index], marks.pair && previous.pair, marks.junction && previous.junction);
    }
    push(token, marks.pair, marks.junction);
  }
  return runs;
}

export const SECTION_ORDER = ['open-questions', 'spot-check', 'no-review-needed'];

export const SECTION_LABELS = {
  'open-questions': {
    title: 'Open questions',
    open: true,
    legend: 'No ruling covers the whole class; your verdicts decide it. The Queue draws only from here.',
  },
  'spot-check': {
    title: 'Spot-check (already ruled)',
    open: true,
    legend:
      'You ruled this kind of change. Its units still take verdicts and count toward Overall progress: approve one that shows what you ruled, reject one that doesn’t. The Queue leaves them out.',
  },
  'no-review-needed': {
    title: 'No review needed',
    open: false,
    legend: 'A ruling covers every unit, or every unit draws exactly as in the old font. Their units stay hidden unless “Show units that need no review” is on.',
  },
};

export const STATUS_LABELS = {
  'per-unit': {
    pill: 'you decide',
    legend:
      'No ruling covers this kind of change as a whole; your verdict on each unit decides it, and the Queue draws only from these.',
  },
  approved: {
    pill: 'you approved',
    legend:
      'You ruled this kind of change correct, in a review round, a letter’s review session or from side-by-side renders. Its remaining units are a spot-check: approve one that shows what you ruled, reject one that doesn’t.',
  },
  'by-design': {
    pill: 'by design',
    legend:
      'A rule of the rebuild’s design decides the change: a space or ZWNJ splits the word, or the letter looks identical and only glyph names differ.',
  },
  'machine-approved': {
    pill: 'machine-approved',
    legend: 'Every unit draws exactly as in the old font, so the machine approved it without a ruling. Shown only in No review needed.',
  },
  diff: {
    pill: 'diff',
    legend: 'A table-diff bucket, which has no ledger status.',
  },
};

export const PREDATES_APP = 'This corpus predates the app; run the review cycle.';

// The banner text for a manifest the app cannot sort into sections, or null. Without `section` the Queue would read every class as ruled and show nothing, so the app refuses the corpus instead.
export function manifestRefusal(manifest) {
  const classes = Array.isArray(manifest?.classes) ? manifest.classes : [];
  return classes.some((cls) => !SECTION_ORDER.includes(cls?.section)) ? PREDATES_APP : null;
}

export function classInfo(manifest) {
  const info = new Map();
  for (const [id, entry] of Object.entries(manifest?.ledger_classes ?? {})) info.set(id, { ...entry, id, listed: false });
  for (const cls of manifest?.classes ?? []) info.set(cls.id, { ...info.get(cls.id), ...cls, listed: true });
  return info;
}

export function pillStatus(cls) {
  if (!cls) return null;
  if (cls.section === 'no-review-needed' && !cls.no_verdict && typeof cls.unit_count === 'number' && humanClassCount(cls) === 0) {
    return 'machine-approved';
  }
  return cls.status ?? 'diff';
}

export function pillText(status) {
  return STATUS_LABELS[status]?.pill ?? status ?? '';
}

export function classTitle(cls, id = cls?.id) {
  return cls?.title || id || '';
}

export function classChip(id, info) {
  const cls = info?.get(id) ?? null;
  const status = pillStatus(cls);
  return {
    text: classTitle(cls, id),
    title: cls?.summary ? `${id} — ${plainText(cls.summary)}` : id,
    status,
    pill: status ? pillText(status) : null,
  };
}

// Splits copy on backtick spans, which the ledger and change-group prose use for data-expect notation, so the app builds each span as a <code> element instead of parsing markup. An unpaired backtick stays as text.
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
  const ledger = manifest?.ledger_classes ?? {};
  const split = Object.keys(ledger).length > 0;
  const sections = SECTION_ORDER.map((id) => ({ id, ...SECTION_LABELS[id], entries: [], changeGroups: [] }));
  const byId = new Map(sections.map((section) => [section.id, section]));
  for (const cls of manifest?.classes ?? []) {
    const section = byId.get(cls.section);
    if (!section) continue;
    if (split && !Object.hasOwn(ledger, cls.id)) section.changeGroups.push(cls);
    else section.entries.push(cls);
  }
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
  return `${count} · ${formatCount(left)} ${sectionId === 'spot-check' ? 'to confirm' : 'to review'}`;
}

export function classSectionSentence(cls) {
  if (cls.section === 'spot-check') {
    return `You ruled this kind of change. Approve a unit that shows what you ruled; reject one that doesn’t. The Queue skips these.`;
  }
  if (cls.section === 'no-review-needed') {
    return `Nothing to judge: a ruling or the machine covers ${cls.unit_count === 1 ? 'its one unit' : `all ${unitCount(cls.unit_count)}`}.`;
  }
  return 'No ruling covers this whole class. Judge each unit; units you have already ruled count as done. The Queue draws from here.';
}

export const LOOKS_IDENTICAL_SENTENCE =
  'Every unit here draws exactly as in the old font, and the machine approves it. Units that look different are listed under the kind of change they show.';

export function classProgressLine(cls, tally = null) {
  const verb = cls.section === 'spot-check' ? 'to confirm' : 'to review';
  return `“${classTitle(cls)}”: ${formatCount(classLeft(cls, tally))} ${verb} of ${formatCount(humanClassCount(cls))}`;
}

export const UNMATCHED_CLASS = 'UNMATCHED';

function listPhrase(items) {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')} or ${items.at(-1)}`;
}

// "with ss03 on" only when the configs are exactly the unit's configs that turn ss03 on; otherwise a set that another of the unit's configs also turns on is named "ss03 alone", so the phrase never claims the whole set.
export function configSetPhrase(configs, unitConfigs) {
  const chosen = new Set(configs);
  for (const feature of configs[0].split('+')) {
    if (feature === 'default') continue;
    const holding = unitConfigs.filter((config) => config.split('+').includes(feature));
    if (holding.length === chosen.size && holding.every((config) => chosen.has(config))) return `with ${feature} on`;
  }
  const name = (config) => {
    if (config === 'default') return 'no set';
    const shared = unitConfigs.some((other) => !chosen.has(other) && other.split('+').includes(config));
    return shared ? `${config} alone` : config;
  };
  return `with ${listPhrase(configs.map(name))} on`;
}

function capitalize(text) {
  return text ? `${text[0].toUpperCase()}${text.slice(1)}` : text;
}

export function configClassBadge(unit, info) {
  const map = unit?.config_classes;
  if (!map || typeof map !== 'object') return unit?.config_class_note || null;
  const unitConfigs = Array.isArray(unit.configs) && unit.configs.length > 0 ? unit.configs : Object.keys(map);
  const rank = (config) => {
    const index = unitConfigs.indexOf(config);
    return index === -1 ? Number.POSITIVE_INFINITY : index;
  };
  const byClass = new Map();
  for (const config of Object.keys(map).sort((a, b) => rank(a) - rank(b))) {
    const classId = map[config];
    if (!byClass.has(classId)) byClass.set(classId, []);
    byClass.get(classId).push(config);
  }
  if (byClass.size < 2) return null;
  const sentences = [];
  for (const [classId, configs] of byClass) {
    if (classId === UNMATCHED_CLASS) continue;
    const chip = classChip(classId, info);
    const pill = chip.pill ? ` (${chip.pill})` : '';
    sentences.push(`${capitalize(configSetPhrase(configs, unitConfigs))}: covered by “${chip.text}”${pill}.`);
  }
  const open = byClass.get(UNMATCHED_CLASS);
  if (open) sentences.push(`${capitalize(configSetPhrase(open, unitConfigs))}, no ruling covers it; that is what you’re judging.`);
  return sentences.join(' ');
}
