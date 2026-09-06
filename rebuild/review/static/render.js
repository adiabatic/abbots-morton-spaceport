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
  // The dump is the engine's own output, so the emphasis partitions it instead of rewriting it: concatenating every run's text gives back the input character for character.
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

// The amber band over the judged pair on one side, or null when there is none to draw: a unit with no primary pair has no band, and neither has a slim fragment, which carries no highlight at all rather than an empty one — so a show-machine fold draws its rows from the cells and seams alone and never reaches for geometry the build left out.
export function pairBand(unit, side, fontSize, upem) {
  const highlight = unit.highlight?.[side];
  if (unit.pair === null || !highlight) return null;
  return highlightRect(highlight, fontSize, upem);
}

export function markOffset(x, fontSize, upem) {
  return (x * fontSize) / upem;
}

export function secondarySeamsOf(unit) {
  if (unit.ink_identical || unit.picture_identical) return [];
  return Array.isArray(unit.secondary_seams) ? unit.secondary_seams : [];
}

export function seamChip(seam) {
  if (seam.home) {
    return {
      home: seam.home,
      label: seam.home,
      title: `This dim band is a secondary divergent seam; its behavior is judged at its home unit ${seam.home}. Click to jump there.`,
    };
  }
  return {
    home: null,
    label: 'only here',
    title:
      'This secondary divergent seam has no shorter home unit where the same behavior is the primary judgment, so judge it in this unit.',
  };
}

export function cellCodepointSpans(cells) {
  // Mirrors the build's after-span walk behind pair_codepoints: a settled cell covers one codepoint position, except a formed ligature (the qsX_qsY underscore in its rune segment), which covers two.
  const spans = [];
  let position = 0;
  for (const cell of cells) {
    const width = cell.split('/')[0].includes('_') ? 2 : 1;
    spans.push([position, position + width - 1]);
    position += width;
  }
  return spans;
}

export function onlyHereSeamSpans(unit) {
  // Cross-checked against the build's pair_codepoints: on any disagreement the text lines degrade to unmarked rather than underlining the wrong letters.
  const cells = unit.after?.cells;
  if (!Array.isArray(cells) || !unit.pair || !unit.pair_codepoints) return [];
  const spans = cellCodepointSpans(cells);
  const derived = [spans[unit.pair.left]?.[0], spans[unit.pair.right]?.[1]];
  if (derived[0] !== unit.pair_codepoints[0] || derived[1] !== unit.pair_codepoints[1]) return [];
  const result = [];
  for (const seam of secondarySeamsOf(unit)) {
    if (seam.home !== null) continue;
    const left = spans[seam.pair.left];
    const right = spans[seam.pair.right];
    if (left && right) result.push([left[0], right[1]]);
  }
  return result;
}

const MACHINE_CHANNELS = ['ink_identical', 'picture_identical', 'junior_equivalent'];

export function needsNoVerdict(unit) {
  // The disjunction audit.slim_fragment reads on the build side: a unit any machine channel approved, or one of a no-verdict ledger class, takes no verdict. It is read off the flags rather than off a batch because a fragment carries no batch — a unit's place in the queue is the manifest's triage index, and only the app index's rows carry it — and those rows, which are human by construction, carry none of the flags either, so they read as human here.
  return Boolean(unit) && (unit.no_verdict === true || MACHINE_CHANNELS.some((channel) => unit[channel] === true));
}

// A human row's place in the manifest's triage index — the order the app pages in — or Infinity for a record that carries none.
export function triageOrder(unit) {
  return typeof unit?.order === 'number' ? unit.order : Number.POSITIVE_INFINITY;
}

export function echoChip(unit, memberIds) {
  if (!unit.echo || !Array.isArray(memberIds) || memberIds.length < 2) return null;
  return {
    label: `echo ×${memberIds.length}`,
    href: `#units=${memberIds.join(',')}`,
    title:
      `The before→after change here is pixel-identical in ${memberIds.length} ${unit.class} windows with the same judged pair and configs — ` +
      'the surrounding letters differ but the change is the same picture. A verdict on any of them fills the unverdicted rest ' +
      '(each can still be overridden or cleared individually). Click to view the whole echo group stacked.',
  };
}

export function echoFillTargets(unit, memberIds, hasVerdict) {
  if (!unit || !unit.echo || !Array.isArray(memberIds)) return [];
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
  // Triage order where the rows carry it; the group and the id order the records that do not (a worklist's machine records).
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

// A class's units that need no verdict: everything in a no-verdict class, the machine-approved ones anywhere else. The app reads it from the manifest so a show-machine fold can state its size without fetching the class.
export function machineFoldTotal(cls) {
  return cls.no_verdict ? cls.unit_count : (cls.machine_approved_count ?? 0);
}

// The badge that fold wears, from the manifest's per-class channel counts rather than from the class's loaded units: the narrowest channel that accounts for every one of them, and the no-verdict badge when none does.
export function machineFoldChannel(cls) {
  const total = machineFoldTotal(cls);
  const channels = cls.machine_channels ?? {};
  const ink = channels.ink_identical ?? 0;
  const picture = channels.picture_identical ?? 0;
  const junior = channels.junior_equivalent ?? 0;
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

export function machineChannels(manifest) {
  const machine = manifest.machine_approved ?? {};
  const channels = machine.channels ?? {};
  return {
    units: machine.units ?? 0,
    inkIdentical: channels.ink_identical?.units ?? machine.units ?? 0,
    pictureIdentical: channels.picture_identical?.units ?? 0,
    juniorEquivalent: channels.junior_equivalent?.units ?? 0,
  };
}

const MACHINE_CHANNEL_ROWS = [
  ['ink_identical', 'inkIdentical', 'ink-identical'],
  ['picture_identical', 'pictureIdentical', 'picture-identical'],
  ['junior_equivalent', 'juniorEquivalent', 'junior-equivalent'],
];

function machineChannelSplit(manifest) {
  const counts = machineChannels(manifest);
  const channels = manifest.machine_approved?.channels ?? {};
  const split = [];
  for (const [key, countKey, label] of MACHINE_CHANNEL_ROWS) {
    const units = counts[countKey];
    if (units > 0) split.push({ label, units, method: channels[key]?.method ?? '' });
  }
  return split;
}

export function surfaceChipLabel(manifest) {
  return `${formatCount(manifest.totals.units)} units`;
}

export function surfaceAlphabetLabel(manifest) {
  const alphabet = manifest.alphabet;
  if (!alphabet) return null;
  return `${formatCount(alphabet.migrated)} of ${formatCount(alphabet.total)} letters`;
}

export function surfaceStampLine(manifest) {
  if (!manifest.generated_at) return null;
  return manifest.repo_head
    ? `Generated ${manifest.generated_at} at ${manifest.repo_head}`
    : `Generated ${manifest.generated_at}`;
}

const NO_VERDICT_DETAIL_TITLE =
  'Units in a no-verdict ledger class are adjudicated wholesale by a ratified rule and never need individual verdicts; hover the class in the sidebar for its rationale.';

export function surfaceDetailRows(manifest) {
  const rows = [{ label: 'Surface', value: formatCount(manifest.totals.units) }];
  const { units } = machineChannels(manifest);
  if (units > 0) {
    const split = machineChannelSplit(manifest);
    const label = split.length === 1 ? `${split[0].label} machine-approved` : 'machine-approved';
    rows.push({ label, value: formatCount(units), title: machineTitle(manifest) });
    if (split.length > 1) {
      for (const channel of split) {
        rows.push({ label: channel.label, value: formatCount(channel.units), sub: true, title: channel.method });
      }
    }
  }
  const exempt = noVerdictTotal(manifest);
  if (exempt > 0) rows.push({ label: 'in no-verdict classes', value: formatCount(exempt), title: NO_VERDICT_DETAIL_TITLE });
  rows.push({ label: 'for human review', value: formatCount(humanTotal(manifest)), title: 'The Overall denominator: everything the surface leaves to a person.' });
  return rows;
}

export function machineTitle(manifest) {
  const { units } = machineChannels(manifest);
  const method = manifest.machine_approved?.method ?? '';
  const split = machineChannelSplit(manifest);
  if (units === 0 || split.length < 2) return method;
  const parts = `${split.map((channel) => `${formatCount(channel.units)} ${channel.label}`).join(' + ')}.`;
  return method ? `${parts} ${method}` : parts;
}

export function classCountsLine(cls, verdicted = null) {
  const parts = [`${formatCount(cls.unit_count)} units${cls.no_verdict ? ` — ${NO_VERDICT_BADGE}` : ''}`];
  if (!cls.no_verdict) {
    if (cls.machine_approved_count) parts.push(`${formatCount(cls.machine_approved_count)} machine`);
    const human = humanClassCount(cls);
    if (human > 0) {
      parts.push(
        verdicted === null
          ? `${formatCount(human)} to review`
          : `${formatCount(verdicted)}/${formatCount(human)}`,
      );
    }
  }
  parts.push(`${formatCount(cls.row_count)} rows`);
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
  // Mirrors unitsForView's class selection: batchless classes hold nothing but machine-approved and no-verdict units, so they ride along with batch 0 only when those are on screen to be seen.
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

// Keyed on the unit object, which is immutable once parsed, so the cache can never go stale and collects with the rows it describes; without it a keystroke rebuilds and lowercases one array per unit in the whole queue.
const haystacks = new WeakMap();

export function searchHaystack(unit) {
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
    unit.group,
    unit.echo ?? '',
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

export function searchUnits(units, query, limit = 50) {
  const trimmed = (query ?? '').trim().toLowerCase();
  if (!trimmed) return { matches: [], total: 0 };
  const tokens = trimmed.split(/\s+/u);
  const ranked = [];
  for (const unit of units) {
    const haystack = searchHaystack(unit);
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
  // Mirrors the build's notation() spacing rule: letters concatenate, boundary tokens (◊ZWNJ, ␣, the bare namer dot ·) are space-separated, so joining separators[i] + tokens[i] reproduces unit.notation.
  const separators = [];
  let previousWasLetter = false;
  for (const token of tokens) {
    const letter = isLetterToken(token);
    separators.push(separators.length === 0 ? '' : letter && previousWasLetter ? '' : ' ');
    previousWasLetter = letter;
  }
  return separators;
}

export function tokenMarkRuns(tokens, separators, pairSpan, seamSpans) {
  // A separator carries a mark only when both its neighbors do, so the separator before the first marked token stays outside the mark; adjacent pieces under the same marks merge into one run.
  const marksAt = (index) => ({
    pair: Boolean(pairSpan) && index >= pairSpan[0] && index <= pairSpan[1],
    seam: seamSpans.some((span) => index >= span[0] && index <= span[1]),
  });
  const runs = [];
  const push = (text, pair, seam) => {
    if (!text) return;
    const last = runs.at(-1);
    if (last && last.pair === pair && last.seam === seam) last.text += text;
    else runs.push({ text, pair, seam });
  };
  for (const [index, token] of tokens.entries()) {
    const marks = marksAt(index);
    if (index > 0) {
      const previous = marksAt(index - 1);
      push(separators[index], marks.pair && previous.pair, marks.seam && previous.seam);
    }
    push(token, marks.pair, marks.seam);
  }
  return runs;
}
