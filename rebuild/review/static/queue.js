// The in-app review queue as pure functions over the in-memory store. They follow rebuild/tools/review_queue.py's clustering, so the view agrees with a snapshot of the same verdicts. A blank unit is unverdicted or skipped. Clusters group blank human units by the build's `cluster` signature, which is the duplicate-group key without the judged pair, so every duplicate group falls inside one cluster. Evidence comes from judged units with the same signature.

export const TOP_CLUSTER_COUNT = 25;
export const SINGLETON_CHUNK = 40;
export const RULED_STATUSES = ['intended', 'reviewed-approved', 'reviewed-rejected'];
export const SINGLETON_DECISION = '#singletons';
export const SHOWN_STORAGE_KEY = 'ams-review-queue-shown';

export function isBlank(record) {
  return !record || record.verdict === 'skip';
}

// A human row's position in the manifest's triage index, the order the app pages in. A row without one sorts last. Human rows have distinct positions; byTriageOrder breaks any other tie on the id.
function triageOrder(unit) {
  return typeof unit?.order === 'number' ? unit.order : Number.POSITIVE_INFINITY;
}

function byTriageOrder(a, b) {
  return triageOrder(a) - triageOrder(b) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
}

export function buildClusters(units, recordOf) {
  // Sort in triage order, as review_queue.py does, so each exemplar, representative and evidence sample is the first unit in the order the app pages in.
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
      const duplicateGroup = unit.duplicate_group || unit.id;
      if (!groups.has(duplicateGroup)) groups.set(duplicateGroup, []);
      groups.get(duplicateGroup).push(unit);
    }
    const duplicateGroups = [...groups.entries()]
      .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
      .map(([duplicateGroup, group]) => ({ duplicateGroup, unitIds: group.map((unit) => unit.id) }));
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
      duplicateGroups,
      representatives: duplicateGroups.map((group) => group.unitIds[0]),
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
    top: multi.slice(0, TOP_CLUSTER_COUNT),
    later: multi.slice(TOP_CLUSTER_COUNT),
    singletons: unruled.filter((cluster) => cluster.size === 1),
    ruledBlankUnits,
  };
}

const ACCEPTING_MIX = ['approve', 'identical'];

// Whether the recorded verdicts on one duplicate group agree: they are all the same, or they mix only approve and identical, which both accept the new rendering. Mirrors verdicts_agree in rebuild/tools/review_queue.py.
export function verdictsAgree(verdicts) {
  if (verdicts.size <= 1) return true;
  return verdicts.size === ACCEPTING_MIX.length && ACCEPTING_MIX.every((verdict) => verdicts.has(verdict));
}

export function duplicateConflicts(duplicateIndex, unitsById, recordOf) {
  const conflicts = [];
  for (const duplicateGroup of [...duplicateIndex.keys()].sort()) {
    const unitIds = duplicateIndex
      .get(duplicateGroup)
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
      conflicts.push({ duplicateGroup, class: unitsById.get(unitIds[0])?.class ?? '', unitIds, records });
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

export function readShownDecisions(raw, manifestStamp) {
  let parsed = null;
  try {
    parsed = JSON.parse(raw ?? '');
  } catch {
    return new Set();
  }
  if (!parsed || parsed.stamp !== manifestStamp || !Array.isArray(parsed.keys)) return new Set();
  return new Set(parsed.keys.filter((key) => typeof key === 'string'));
}

export function writeShownDecisions(shown, manifestStamp) {
  return JSON.stringify({ stamp: manifestStamp, keys: [...shown] });
}

// `stacked` is the key the worklist was stacked under, from the hash's `decision`. The units alone can name the wrong decision: a lone singleton's worklist has one cluster signature but was stacked as the singleton run.
export function decisionKey(units, stacked = null) {
  if (typeof stacked === 'string' && stacked !== '') return stacked;
  const signatures = new Set();
  for (const unit of units) if (typeof unit?.cluster === 'string') signatures.add(unit.cluster);
  return signatures.size === 1 ? [...signatures][0] : SINGLETON_DECISION;
}

// The next decision to show the reviewer. Open decisions are in queue order: the largest cluster first, with one representative per duplicate group that has no record, then the singletons. A record on a blank member can only be a skip, so any recorded member defers its whole duplicate group. `shown` holds the decision keys stacked for this corpus, least recently stacked first (the app keeps it in localStorage). The first open decision not in `shown` is returned, so leaving a cluster postpones it. When every open decision has been shown, the one shown longest ago is returned with `revisit` set. The returned decision carries its `key`, which the worklist stacked from it records in `shown`. Returns null when every blank unit is in a deferred group.
export function nextQueueDecision(units, recordOf, ruledIds, shown = new Set()) {
  const clusters = buildClusters(units, recordOf);
  const { top, later, singletons } = partitionClusters(clusters, ruledIds);
  const open = [];
  for (const cluster of [...top, ...later]) {
    const representatives = [];
    for (const group of cluster.duplicateGroups) {
      if (group.unitIds.some((id) => recordOf(id))) continue;
      representatives.push(group.unitIds[0]);
    }
    if (representatives.length > 0) open.push({ key: cluster.id, decision: { kind: 'cluster', cluster, unitIds: representatives } });
  }
  const openSingles = singletons.filter((cluster) => !recordOf(cluster.exemplar.id));
  if (openSingles.length > 0) {
    const [chunk] = singletonChunks(openSingles);
    open.push({
      key: SINGLETON_DECISION,
      decision: { kind: 'singletons', unitIds: chunk.unitIds, remaining: openSingles.length },
    });
  }
  if (open.length === 0) return null;
  const fresh = open.find((candidate) => !shown.has(candidate.key));
  if (fresh) return { ...fresh.decision, key: fresh.key, revisit: false };
  const rotation = [...shown];
  let oldest = open[0];
  for (const candidate of open) {
    if (rotation.indexOf(candidate.key) < rotation.indexOf(oldest.key)) oldest = candidate;
  }
  return { ...oldest.decision, key: oldest.key, revisit: true };
}

// What to do with a review queue worklist from the URL hash. A worklist names units by id, and a rebuild gives a changed unit a new id, so a worklist stamped for another corpus (or with no stamp) is meaningless: 'restack' stacks the next decision from the live queue. A current worklist whose units all have a verdict other than skip was finished, so it gets 'advance', as finishing it live would. A skip is a record but not a verdict, and clicking a skipped-through cluster's card should show its deferred representatives again, so a worklist with any skip or blank renders (null).
export function queueResumeAction({ stamp, manifestStamp, unitIds, recordOf }) {
  if (stamp !== manifestStamp) return 'restack';
  const judged = (id) => {
    const record = recordOf(id);
    return Boolean(record) && record.verdict !== 'skip';
  };
  if (unitIds.length === 0 || unitIds.every(judged)) return 'advance';
  return null;
}

export function queueTotals(clusters) {
  let blankUnits = 0;
  let duplicateGroups = 0;
  let multiClusters = 0;
  for (const cluster of clusters) {
    blankUnits += cluster.size;
    duplicateGroups += cluster.duplicateGroups.length;
    if (cluster.size > 1) multiClusters += 1;
  }
  return {
    blankUnits,
    duplicateGroups,
    clusters: clusters.length,
    multiClusters,
    singletonClusters: clusters.length - multiClusters,
  };
}
