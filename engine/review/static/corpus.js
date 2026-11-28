// The corpus as the page holds it. app.js fetches the manifest and then every class's shard whole at boot, and `indexCorpus` turns them into the in-memory corpus that every view reads: each unit's record by id, each class's units in shard order (id order), and the human units in the manifest's triage order. A human unit carries the two values no fragment does, `order`, its position in the manifest's `human_unit_ids`, and `batch`, the slice of `batch_size` that position falls in; a unit that takes no verdict carries neither. These functions are pure, so node --test can import them: app.js awaits its manifest fetch at top level and cannot be imported.

import { classesInBatch, familiesOfGroup, needsNoVerdict, unitMatchesFilters, unitWorklist } from './render.js';

export function emptyCorpus() {
  return { byId: new Map(), byClass: new Map(), human: [], families: [], problems: [] };
}

// `shards` maps each class id to its shard's parsed units. `problems` names what shows that the shards are not the ones the manifest describes, as when a land swapped the corpus while the page was fetching it: a class whose shard is not a list, a class holding another number of units than the manifest counts, and a human unit id no shard holds. `families` lists the letter families the human units' groups name, sorted, for the family filter.
export function indexCorpus(manifest, shards) {
  const corpus = emptyCorpus();
  const humanIds = Array.isArray(manifest?.human_unit_ids) ? manifest.human_unit_ids : [];
  const positions = new Map(humanIds.map((id, position) => [id, position]));
  const batchSize = Number.isInteger(manifest?.batch_size) && manifest.batch_size > 0 ? manifest.batch_size : 1;
  for (const cls of manifest?.classes ?? []) {
    const records = shards.get(cls.id);
    if (!Array.isArray(records)) {
      corpus.problems.push(`the ${cls.id} shard is not a list of units`);
      continue;
    }
    if (records.length !== cls.unit_count) {
      corpus.problems.push(`the ${cls.id} shard holds ${records.length} units where the manifest counts ${cls.unit_count}`);
    }
    const units = [];
    for (const record of records) {
      const order = positions.get(record.id);
      const unit = order === undefined ? record : { ...record, order, batch: Math.floor(order / batchSize) };
      corpus.byId.set(unit.id, unit);
      units.push(unit);
    }
    corpus.byClass.set(cls.id, units);
  }
  const families = new Set();
  for (const id of humanIds) {
    const unit = corpus.byId.get(id);
    if (!unit) {
      corpus.problems.push(`no shard holds ${id}, which the manifest's triage index lists`);
      continue;
    }
    corpus.human.push(unit);
    for (const family of familiesOfGroup(unit.group)) families.add(family);
  }
  corpus.families = [...families].sort();
  return corpus;
}

// The units a batch view draws from, before its filters apply: the human units of `batch` in the classes in view, in triage order, so the group folds and the default cursor follow the queue; then, while `showMachine` is on, the units that take no verdict of those classes, class by class in manifest order, each class's in id order. The classes in view are the class filter's alone, or else those `classesInBatch` names, which adds a batchless class, one holding only units that take no verdict, to batch 0.
export function unitsInView(corpus, manifest, { batch, classFilter = null, showMachine = false }) {
  const inView = classFilter ? new Set([classFilter]) : classesInBatch(manifest, batch, showMachine);
  const human = [];
  const machine = [];
  for (const cls of manifest?.classes ?? []) {
    if (!inView.has(cls.id)) continue;
    for (const unit of corpus.byClass.get(cls.id) ?? []) {
      if (unit.batch !== undefined) {
        if (unit.batch === batch) human.push(unit);
      } else if (showMachine && needsNoVerdict(unit)) {
        machine.push(unit);
      }
    }
  }
  human.sort((a, b) => a.order - b.order);
  return [...human, ...machine];
}

// A `#units=` worklist's units: each listed id once, in the order listed, leaving out the ids the corpus does not hold.
export function worklistUnits(corpus, key) {
  const units = [];
  const seen = new Set();
  for (const id of unitWorklist(key)) {
    if (seen.has(id)) continue;
    seen.add(id);
    const unit = corpus.byId.get(id);
    if (unit) units.push(unit);
  }
  return units;
}

// The show-machine folds for the units that take no verdict a view shows: one fold per class, in the order the classes first appear, holding its units in list order and naming the machine check its badge shows: `ink_identical` when every unit in it is ink-identical, `picture_identical` when every unit is one or the other, and `no_verdict` otherwise.
export function machineFolds(units) {
  const byClass = new Map();
  for (const unit of units) {
    if (!byClass.has(unit.class)) byClass.set(unit.class, []);
    byClass.get(unit.class).push(unit);
  }
  const folds = [];
  for (const [classId, members] of byClass) {
    const check = members.every((unit) => unit.ink_identical)
      ? 'ink_identical'
      : members.every((unit) => unit.ink_identical || unit.picture_identical)
        ? 'picture_identical'
        : 'no_verdict';
    folds.push({ classId, units: members, check });
  }
  return folds;
}

// Where a `#unit=` link to a unit outside the current view takes the page. A unit that takes no verdict is shown on its own (`reveal`). A human unit in another batch that the view's filters admit moves the view to that batch. Anything else, an id the corpus does not hold among it, gives null, and the cursor falls back to the view's first unit. `record` is the unit's verdict, which the status filter reads.
export function deepLinkMove(unit, state, record) {
  if (!unit) return null;
  if (needsNoVerdict(unit)) return { reveal: true };
  if (unit.batch !== state.batch && unitMatchesFilters(unit, state, record)) return { batch: unit.batch };
  return null;
}
