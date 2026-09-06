// The live in-app docket: pure data transforms mirroring rebuild/tools/review_docket.py's clustering semantics, so the view over the in-memory store always matches what a bake of the same verdicts would say. Blank = unverdicted or skip; clusters group blank human units by the build-emitted `cluster` signature (the echo key minus the judged pair, so every echo group nests inside exactly one cluster); evidence comes from judged units sharing the signature.

export const TRANCHE_SIZE = 25;
export const SINGLETON_CHUNK = 40;
export const RULED_STATUSES = ['intended', 'reviewed-approved', 'reviewed-rejected'];

export function isBlank(record) {
  return !record || record.verdict === 'skip';
}

// A human row's place in the manifest's triage index, the order the app pages in; a row without one sorts last, and ties (none among human rows) break on the id.
function triageOrder(unit) {
  return typeof unit?.order === 'number' ? unit.order : Number.POSITIVE_INFINITY;
}

function byTriageOrder(a, b) {
  return triageOrder(a) - triageOrder(b) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
}

export function buildClusters(units, recordOf) {
  // Triage order: within a class (and every cluster is single-class) the docket tool orders human units by their place in the manifest's index, and exemplars, representatives, and evidence samples are all "first in that order".
  const human = [];
  for (const unit of units) {
    if (unit.batch !== null && unit.batch !== undefined && typeof unit.cluster === 'string') human.push(unit);
  }
  human.sort(byTriageOrder);

  const membersByCluster = new Map();
  const judgedByCluster = new Map();
  for (const unit of human) {
    const record = recordOf(unit.id);
    if (isBlank(record)) {
      if (!membersByCluster.has(unit.cluster)) membersByCluster.set(unit.cluster, []);
      membersByCluster.get(unit.cluster).push(unit);
    } else {
      if (!judgedByCluster.has(unit.cluster)) judgedByCluster.set(unit.cluster, []);
      judgedByCluster.get(unit.cluster).push({ unit, record });
    }
  }

  const clusters = [];
  for (const [id, members] of membersByCluster) {
    const groups = new Map();
    for (const unit of members) {
      const echo = unit.echo || unit.id;
      if (!groups.has(echo)) groups.set(echo, []);
      groups.get(echo).push(unit);
    }
    const echoGroups = [...groups.entries()]
      .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
      .map(([echo, group]) => ({ echo, unitIds: group.map((unit) => unit.id) }));
    const judged = judgedByCluster.get(id) ?? [];
    const tallies = new Map();
    for (const { record } of judged) tallies.set(record.verdict, (tallies.get(record.verdict) ?? 0) + 1);
    const counts = [...tallies.entries()]
      .map(([verdict, count]) => ({ verdict, count }))
      .sort((a, b) => b.count - a.count);
    clusters.push({
      id,
      class: members[0].class,
      configs: [...members[0].configs],
      size: members.length,
      echoGroups,
      reps: echoGroups.map((group) => group.unitIds[0]),
      exemplar: members[0],
      memberIds: members.map((unit) => unit.id),
      evidence: {
        counts,
        judgedTotal: judged.length,
        samples: judged
          .slice(0, 3)
          .map(({ unit, record }) => ({ unit: unit.id, verdict: record.verdict, note: record.note ?? '' })),
      },
    });
  }
  clusters.sort(
    (a, b) =>
      b.size - a.size ||
      (a.class < b.class ? -1 : a.class > b.class ? 1 : 0) ||
      (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
  return clusters;
}

export function ruledClassIds(manifestClasses) {
  const ids = new Set();
  for (const cls of manifestClasses ?? []) {
    if (RULED_STATUSES.includes(cls.status)) ids.add(cls.id);
  }
  return ids;
}

export function partitionClusters(clusters, ruledIds) {
  const unruled = clusters.filter((cluster) => !ruledIds.has(cluster.class));
  const multi = unruled.filter((cluster) => cluster.size > 1);
  let ruledBlankUnits = 0;
  for (const cluster of clusters) if (ruledIds.has(cluster.class)) ruledBlankUnits += cluster.size;
  return {
    tranche: multi.slice(0, TRANCHE_SIZE),
    later: multi.slice(TRANCHE_SIZE),
    singletons: unruled.filter((cluster) => cluster.size === 1),
    ruledBlankUnits,
  };
}

const ACCEPTING_MIX = ['approve', 'identical'];

// Whether a set of recorded verdicts on one echo group speaks with a single voice. Unanimity qualifies, and so does an approve/identical mix: both accept the new rendering, one reviewer merely having found the highlighted portion visually unchanged. Mirrors verdicts_agree in rebuild/tools/review_docket.py.
export function verdictsAgree(verdicts) {
  if (verdicts.size <= 1) return true;
  return verdicts.size === ACCEPTING_MIX.length && ACCEPTING_MIX.every((verdict) => verdicts.has(verdict));
}

export function echoConflicts(echoIndex, unitsById, recordOf) {
  const conflicts = [];
  for (const echo of [...echoIndex.keys()].sort()) {
    const unitIds = echoIndex
      .get(echo)
      .map((id) => ({ id, order: triageOrder(unitsById.get(id)) }))
      .sort(byTriageOrder)
      .map((entry) => entry.id);
    const records = new Map();
    for (const id of unitIds) {
      const record = recordOf(id);
      if (record && record.verdict !== 'skip') records.set(id, record);
    }
    const verdicts = new Set();
    for (const record of records.values()) verdicts.add(record.verdict);
    if (!verdictsAgree(verdicts)) {
      conflicts.push({ echo, class: unitsById.get(unitIds[0])?.class ?? '', unitIds, records });
    }
  }
  return conflicts;
}

export function singletonChunks(singletons) {
  const chunks = [];
  for (let start = 0; start < singletons.length; start += SINGLETON_CHUNK) {
    const slice = singletons.slice(start, start + SINGLETON_CHUNK);
    chunks.push({
      start: start + 1,
      end: start + slice.length,
      unitIds: slice.map((cluster) => cluster.exemplar.id),
    });
  }
  return chunks;
}

export function queueCounts(units, recordOf, ruledIds = new Set()) {
  let blankUnits = 0;
  const clusters = new Set();
  for (const unit of units) {
    if (unit.batch === null || unit.batch === undefined || typeof unit.cluster !== 'string') continue;
    if (ruledIds.has(unit.class)) continue;
    if (!isBlank(recordOf(unit.id))) continue;
    blankUnits += 1;
    clusters.add(unit.cluster);
  }
  return { blankUnits, clusters: clusters.size };
}

// The next unworked decision in queue order: the largest cluster with an echo group nobody has seen yet (a rep per such group), then the singletons the same way. A record on a still-blank member can only be a skip, so any recorded member marks its whole echo group as consciously deferred — the flow never re-stacks a deferral or its lookalike siblings. Returns null when every blank unit sits in a deferred group.
export function nextDocketDecision(units, recordOf, ruledIds) {
  const clusters = buildClusters(units, recordOf);
  const { tranche, later, singletons } = partitionClusters(clusters, ruledIds);
  for (const cluster of [...tranche, ...later]) {
    const open = [];
    for (const group of cluster.echoGroups) {
      if (group.unitIds.some((id) => recordOf(id))) continue;
      open.push(group.unitIds[0]);
    }
    if (open.length > 0) return { kind: 'cluster', cluster, unitIds: open };
  }
  const openSingles = singletons.filter((cluster) => !recordOf(cluster.exemplar.id));
  if (openSingles.length > 0) {
    const [chunk] = singletonChunks(openSingles);
    return { kind: 'singletons', unitIds: chunk.unitIds, remaining: openSingles.length };
  }
  return null;
}

// A docket-launched worklist names units by id, and a unit whose content moved under a rebuild carries a new one — so a tab resuming an old worklist hash could otherwise show a queue that no longer holds those units, or holds them under other batches, a plausible-looking screenful that has nothing to do with the docket queue. The stamp pins the worklist to the surface it was stacked for: a mismatch (including the stampless hash of an older tab) means the id list is meaningless and the flow should restack from the live queue, and a current worklist whose every listed unit already carries a real verdict is a finished screenful being resumed, which advances exactly as finishing it live would have. A skip is a record but not a verdict: a skipped-through cluster keeps its docket card, and clicking that card means "show me the deferred reps again", never "teleport to a different decision" — so a worklist holding any skip renders. A current worklist with blanks left renders as-is.
export function docketResumeAction({ stamp, manifestStamp, unitIds, recordOf }) {
  if (stamp !== manifestStamp) return 'restack';
  const judged = (id) => {
    const record = recordOf(id);
    return Boolean(record) && record.verdict !== 'skip';
  };
  if (unitIds.length === 0 || unitIds.every(judged)) return 'advance';
  return null;
}

export function docketTotals(clusters) {
  let blankUnits = 0;
  let echoGroups = 0;
  let multiClusters = 0;
  for (const cluster of clusters) {
    blankUnits += cluster.size;
    echoGroups += cluster.echoGroups.length;
    if (cluster.size > 1) multiClusters += 1;
  }
  return {
    blankUnits,
    echoGroups,
    clusters: clusters.length,
    multiClusters,
    singletonClusters: clusters.length - multiClusters,
  };
}
