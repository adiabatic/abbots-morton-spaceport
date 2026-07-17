export function featureSettingsValue(configToken) {
  if (!configToken || configToken === 'default') return 'normal';
  const settings = [];
  for (const part of configToken.split('+')) settings.push(`"${part}" 1`);
  return settings.join(', ');
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

export function markOffset(x, fontSize, upem) {
  return (x * fontSize) / upem;
}

export function secondarySeamsOf(unit) {
  if (unit.ink_identical) return [];
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

export function needsNoVerdict(unit) {
  return Boolean(unit.ink_identical || unit.junior_equivalent || unit.no_verdict);
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

export function formatCount(value) {
  return value.toLocaleString('en-US');
}

export function machineChannels(manifest) {
  const machine = manifest.machine_approved ?? {};
  const channels = machine.channels ?? {};
  return {
    units: machine.units ?? 0,
    inkIdentical: channels.ink_identical?.units ?? machine.units ?? 0,
    juniorEquivalent: channels.junior_equivalent?.units ?? 0,
  };
}

export function surfaceLine(manifest) {
  return `Surface: ${formatCount(manifest.totals.units)} units`;
}

export function machineLine(manifest) {
  const { units, inkIdentical, juniorEquivalent } = machineChannels(manifest);
  if (units === 0) return null;
  if (juniorEquivalent === 0) return `${formatCount(units)} ink-identical machine-approved`;
  return (
    `${formatCount(units)} machine-approved ` +
    `(${formatCount(inkIdentical)} ink-identical + ${formatCount(juniorEquivalent)} junior-equivalent)`
  );
}

export function noVerdictLine(manifest) {
  const exempt = noVerdictTotal(manifest);
  return exempt > 0 ? `${formatCount(exempt)} in no-verdict classes` : null;
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

export function classesInBatch(manifest, batch) {
  // Mirrors unitsForView's class selection: batchless classes (all machine-approved or no-verdict) ride along with batch 0.
  const ids = new Set();
  for (const cls of manifest.classes) {
    if (cls.batches.includes(batch) || (cls.batches.length === 0 && batch === 0)) ids.add(cls.id);
  }
  return ids;
}

export function copyPreamble(unit) {
  return `I'm looking at rebuild/out/review/ unit ${unit.id} — ${unit.codepoints} (${unit.notation}). `;
}

export function searchHaystack(unit) {
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
  return parts.join(' ').toLowerCase();
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
