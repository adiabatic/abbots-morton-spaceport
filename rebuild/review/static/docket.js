// The live in-app docket: pure data transforms mirroring rebuild/tools/review_docket.py's clustering semantics, so the view over the in-memory store always matches what a bake of the same verdicts would say. Blank = unverdicted or skip; clusters group blank human units by the build-emitted `cluster` signature (the echo key minus the judged pair, so every echo group nests inside exactly one cluster); evidence comes from judged units sharing the signature.

export const TRANCHE_SIZE = 25;
export const SINGLETON_CHUNK = 40;
export const RULED_STATUSES = ['intended', 'reviewed-approved', 'reviewed-rejected'];

export function isBlank(record) {
  return !record || record.verdict === 'skip';
}

function unitNumber(unitId) {
  return Number.parseInt(unitId.slice(2), 10);
}

export function buildClusters(units, recordOf) {
  const human = [];
  for (const unit of units) {
    if (unit.batch !== null && unit.batch !== undefined && typeof unit.cluster === 'string') human.push(unit);
  }
  // Triage order: within a class (and every cluster is single-class) the docket tool's shard order is ascending unit number, and exemplars, representatives, and evidence samples are all "first in that order".
  human.sort((a, b) => unitNumber(a.id) - unitNumber(b.id));

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

export function echoConflicts(echoIndex, unitsById, recordOf) {
  const conflicts = [];
  for (const echo of [...echoIndex.keys()].sort()) {
    const unitIds = [...echoIndex.get(echo)].sort((a, b) => unitNumber(a) - unitNumber(b));
    const records = new Map();
    for (const id of unitIds) {
      const record = recordOf(id);
      if (record && record.verdict !== 'skip') records.set(id, record);
    }
    const verdicts = new Set();
    for (const record of records.values()) verdicts.add(record.verdict);
    if (verdicts.size > 1) {
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
