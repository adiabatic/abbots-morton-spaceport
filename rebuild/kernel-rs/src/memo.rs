//! A finished trace memo detached from the engine that filled it, and the rule under which another engine may read it. An entry records what one engine settled for one window (a collapsed left, an input and four raw slots), with the pointers that evaluation fired and the runes and classes it read. By the window locality rule (`doc/rebuild-design.md` §10), that result depends only on the crate, the script registry and the rune files the key names. So one enumeration's memo can answer another enumeration's windows wherever the two agree on every rune a key names. Applied across configurations, the same rule means a configuration can differ from `default` only on the windows naming a rune with an unlock, a `feature:`-conditioned record, or an unlock gate under that configuration.
//!
//! This is how the per-configuration delta enumeration works. `default` enumerates first and returns its memo as a [`MemoSnapshot`]. Every other configuration's engine takes that snapshot as a [`SharedMemo`] whose [`Exclusion`] names the configuration's unlocking runes ([`unlocking_runes`]), runs the same worklist from the same seeds in the same order, and reads from the shared memo every window whose key names no unlocking rune and whose evaluation read none. The worklist is not seeded: each traversal re-derives reachability, because the memo records what a window settles to, never whether the window exists. So a cell that another configuration reaches first, or reaches only there, is still found. The fired journal carries over as it does for a hit on the engine's own memo: a shared memo entry stores the delta its evaluation journaled and a hit replays it, so a delta configuration's `cited_provenance` is the union over the windows it visited, as in a from-scratch enumeration.
//!
//! Which windows a shared memo may answer is decided by what each entry's evaluation read. While a capture is open, the index journals every rune whose resolved content and every predicate class whose membership its accessors return ([`crate::index`]), and the entry stores that set beside its fired delta. An [`Exclusion`] naming runes or classes therefore rejects the entries that read one of them. A delta's exclusion names its unlocking runes, so a window whose deep slots name an unlocking rune its evaluation never consulted is still read from `default`'s memo. The six runes a key names are also rejected, as a redundant check on the journal: over-invalidation costs a trace, and under-invalidation costs a wrong table.
//!
//! The snapshot is shared behind an [`Arc`] instead of copied per configuration, because the memo is the largest structure the enumeration holds, and a copy per delta configuration would again limit by memory how many delta configurations run at once. So it holds no `Rc`, no reference into any engine, and no ranking (the fixpoint never records one), and a shared memo is read-only once built.

use std::hash::{Hash, Hasher};
use std::sync::Arc;

use crate::engine::{DeltaId, Pointer, TraceEntry, TraceKey, VerdictEntry, VerdictKey};
use crate::hash::{FastHasher, HashMap, HashSet, ShardedMap};
use crate::index::{Read, SpecIndex};
use crate::model::{PolicyRecord, Sym, When};
use crate::types::{Settled, TransitionTrace};

/// Immutable full-key records grouped into hash buckets, with a [`Block`] per bucket that a lookup reads before any record. The bucket count is the record count over [`SnapshotEntries::PER_BUCKET`], rounded up, and a key's bucket is the high half of its hash scaled onto that count. Records are contiguous per bucket and sorted by key inside it. A lookup hashes once and reads its bucket's block, which holds a tag per record from the hash's low byte, then compares the full key of each record whose tag matches, usually one. So a hit reads one block and, in the common case, one record, where a binary search over the bucket would read several records' cache lines one after another. The hash never stands in for equality. A bucket holding more records than a block has tags for is binary-searched on the full key instead. The index costs thirty-two bytes per bucket, two bytes per record, beside a record's thirty-six.
#[derive(Debug, Default)]
pub(crate) struct SnapshotEntries {
    records: Box<[(TraceKey, TraceEntry)]>,
    /// One per bucket, then one more whose start is the record count, so a bucket's end is the next block's start.
    blocks: Box<[Block]>,
}

/// One bucket's part of a snapshot's index: where the bucket's records start, how many there are, and a one-byte tag per record. Thirty-two bytes, so a block never straddles a cache line in an array that starts on a line boundary, as a large allocation does. The type asks for no more than its fields' alignment: an over-aligned array is allocated through `posix_memalign`, and at a thirty-two-byte alignment a build of `default` alone holds 3.3 GB more resident just after its release point (issue #511).
#[derive(Clone, Copy, Debug, Default)]
#[repr(C)]
struct Block {
    start: u32,
    len: u8,
    tags: [u8; Block::TAGS],
}

const _: () = assert!(std::mem::size_of::<Block>() == 32);

impl Block {
    const TAGS: usize = 27;
    /// The `len` of a bucket holding more records than [`Block::TAGS`], whose tags are not filled.
    const OVERFLOW: u8 = u8::MAX;

    /// One bit per record in the bucket whose tag is `tag`, the first record at bit zero. Only for a block that is not [`Block::OVERFLOW`].
    fn matches(&self, tag: u8) -> u32 {
        let mut mask = 0u32;
        for (slot, held) in self.tags.iter().enumerate() {
            mask |= u32::from(*held == tag) << slot;
        }
        mask & ((1u32 << self.len) - 1)
    }
}

impl SnapshotEntries {
    /// The mean bucket size the index is sized for. If bucket sizes are Poisson-distributed about it, fewer than one record in a hundred sits in a bucket larger than [`Block::TAGS`].
    const PER_BUCKET: usize = 16;

    /// The key's hash for bucketing and tags: its own packed `Hash` ([`TraceKey`]'s, the one the engine's live trace memo uses) folded through [`FastHasher`].
    fn hash(key: &TraceKey) -> u64 {
        let mut hash = FastHasher::default();
        key.hash(&mut hash);
        hash.finish()
    }

    fn bucket(hash: u64, buckets: usize) -> usize {
        (((hash >> 32) * buckets as u64) >> 32) as usize
    }

    fn tag(hash: u64) -> u8 {
        hash as u8
    }

    fn from_records(records: Vec<(TraceKey, TraceEntry)>) -> Result<Self, String> {
        Self::from_records_per_bucket(records, Self::PER_BUCKET)
    }

    /// Construction holds a four-byte source position per record and a four-byte offset per bucket beside the records. Counting and in-place bucket partitioning hash each key a bounded number of times. Each bucket is sorted on key and source position, so a duplicate key keeps its last input even though partitioning reorders equal keys. Buckets of up to sixty-four records are insertion-sorted; larger ones use a temporary decorated sort, which bounds the sorting time when many keys collide. The positions and partition cursors are freed before the blocks are built, and the offsets after. Building the blocks hashes each surviving key once more. If removing duplicates lowers the bucket count needed, the index is rebuilt over the surviving records.
    fn from_records_per_bucket(
        mut records: Vec<(TraceKey, TraceEntry)>,
        per_bucket: usize,
    ) -> Result<Self, String> {
        let count = u32::try_from(records.len())
            .map_err(|_| "a memo holds fewer than 2^32 windows".to_owned())?;
        if records.is_empty() {
            return Ok(Self::default());
        }
        let buckets = records.len().div_ceil(per_bucket);
        let mut offsets = vec![0u32; buckets + 1];
        for (key, _) in &records {
            offsets[Self::bucket(Self::hash(key), buckets) + 1] += 1;
        }
        for bucket in 0..buckets {
            offsets[bucket + 1] += offsets[bucket];
        }
        let mut positions: Vec<u32> = (0..count).collect();
        let mut next = offsets[..buckets].to_vec();
        for bucket in 0..buckets {
            while next[bucket] < offsets[bucket + 1] {
                let at = next[bucket] as usize;
                let destination = Self::bucket(Self::hash(&records[at].0), buckets);
                if destination == bucket {
                    next[bucket] += 1;
                } else {
                    let target = next[destination] as usize;
                    records.swap(at, target);
                    positions.swap(at, target);
                    next[destination] += 1;
                }
            }
        }
        drop(next);
        for bucket in 0..buckets {
            let start = offsets[bucket] as usize;
            let end = offsets[bucket + 1] as usize;
            if end - start > 64 {
                let mut ordered: Vec<_> = records[start..end]
                    .iter()
                    .copied()
                    .zip(positions[start..end].iter().copied())
                    .collect();
                ordered.sort_unstable_by_key(|((key, _), position)| (*key, *position));
                for (target, (record, _)) in records[start..end].iter_mut().zip(ordered) {
                    *target = record;
                }
                continue;
            }
            for at in start + 1..end {
                let mut cursor = at;
                while cursor > start
                    && (records[cursor].0, positions[cursor])
                        < (records[cursor - 1].0, positions[cursor - 1])
                {
                    records.swap(cursor, cursor - 1);
                    positions.swap(cursor, cursor - 1);
                    cursor -= 1;
                }
            }
        }
        drop(positions);
        let mut written = 0usize;
        for bucket in 0..buckets {
            let start = offsets[bucket] as usize;
            let end = offsets[bucket + 1] as usize;
            offsets[bucket] = written as u32;
            for at in start..end {
                if at + 1 == end || records[at].0 != records[at + 1].0 {
                    records[written] = records[at];
                    written += 1;
                }
            }
        }
        offsets[buckets] = written as u32;
        records.truncate(written);
        if written.div_ceil(per_bucket) < buckets {
            drop(offsets);
            return Self::from_records_per_bucket(records, per_bucket);
        }
        let mut blocks = vec![Block::default(); buckets + 1];
        for (bucket, block) in blocks.iter_mut().enumerate() {
            block.start = offsets[bucket];
            if bucket == buckets {
                break;
            }
            let held = &records[offsets[bucket] as usize..offsets[bucket + 1] as usize];
            if held.len() > Block::TAGS {
                block.len = Block::OVERFLOW;
                continue;
            }
            block.len = held.len() as u8;
            for (tag, (key, _)) in block.tags.iter_mut().zip(held) {
                *tag = Self::tag(Self::hash(key));
            }
        }
        drop(offsets);
        Ok(Self {
            records: records.into_boxed_slice(),
            blocks: blocks.into_boxed_slice(),
        })
    }

    pub(crate) fn len(&self) -> usize {
        self.records.len()
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.records.is_empty()
    }

    pub(crate) fn get(&self, key: &TraceKey) -> Option<&TraceEntry> {
        if self.is_empty() {
            return None;
        }
        let hash = Self::hash(key);
        let bucket = Self::bucket(hash, self.blocks.len() - 1);
        let block = &self.blocks[bucket];
        let start = block.start as usize;
        if block.len == Block::OVERFLOW {
            let records = &self.records[start..self.blocks[bucket + 1].start as usize];
            return records
                .binary_search_by_key(key, |(key, _)| *key)
                .ok()
                .map(|at| &records[at].1);
        }
        let mut matches = block.matches(Self::tag(hash));
        while matches != 0 {
            let (held, entry) = &self.records[start + matches.trailing_zeros() as usize];
            if held == key {
                return Some(entry);
            }
            matches &= matches - 1;
        }
        None
    }

    #[cfg(test)]
    pub(crate) fn iter(&self) -> impl Iterator<Item = (&TraceKey, &TraceEntry)> {
        self.records.iter().map(|(key, entry)| (key, entry))
    }
}

impl From<ShardedMap<TraceKey, TraceEntry>> for SnapshotEntries {
    /// The records are drained one shard at a time, so each shard's table is freed once its entries have moved. Their order does not reach the snapshot, which sorts each bucket on the key.
    fn from(entries: ShardedMap<TraceKey, TraceEntry>) -> Self {
        let mut records = Vec::with_capacity(entries.len());
        for shard in entries.into_shards() {
            records.extend(shard);
        }
        Self::from_records(records).expect("a live memo holds fewer than 2^32 windows")
    }
}

/// The liveness verdicts one engine published ([`crate::engine::Engine::publish_verdicts`]), whose delta ids index the snapshot's delta pool as its windows' do. `groups` are the unlocking-rune sets they were published for, one per distinct set among the deltas that read them. Each record's [`VerdictEntry::slots`] is where its slots start in `slots`, one per group in group order: the fired delta of the probe's asks that name none of that group's runes, or `None` where the verdict is not published for that group ([`crate::engine::Engine::end_verdict`], [`crate::liveness`]).
#[derive(Debug, Default)]
pub(crate) struct SnapshotVerdicts {
    pub(crate) groups: Vec<HashSet<Sym>>,
    pub(crate) records: HashMap<VerdictKey, VerdictEntry>,
    pub(crate) slots: Box<[Option<DeltaId>]>,
}

/// One engine's finished trace memo: compact immutable entries and the four tables their ids index. The tables are the engine memo's own pools flattened, so an entry read through the snapshot resolves as it did in the engine that recorded it. The live engine's memo is a hash map; the snapshot has no hash-table slack, and its index costs two bytes a record. `verdicts` holds the liveness verdicts the engine published, empty for every other engine.
#[derive(Debug, Default)]
pub struct MemoSnapshot {
    pub(crate) entries: SnapshotEntries,
    pub(crate) settled: Vec<Settled>,
    pub(crate) notes: Vec<Vec<String>>,
    pub(crate) deltas: Vec<Box<[Pointer]>>,
    pub(crate) reads: Vec<Box<[Read]>>,
    pub(crate) verdicts: SnapshotVerdicts,
}

impl MemoSnapshot {
    /// How many windows this snapshot holds.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// How many liveness verdicts this snapshot carries.
    pub fn verdicts(&self) -> usize {
        self.verdicts.records.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The trace one entry stands for, rebuilt from the tables as the recording engine's miss returned it, without the ranking, which the fixpoint never records.
    pub(crate) fn trace(&self, entry: TraceEntry) -> TransitionTrace {
        TransitionTrace {
            settled: self.settled[entry.settled.index()].clone(),
            joint_tiebreak: entry.joint_tiebreak(),
            prospect: i64::from(entry.prospect()),
            decided_stage: entry.decided_stage(),
            notes: self.notes[entry.notes.index()].to_vec(),
            ranking: None,
        }
    }

    /// The settled record one entry names.
    pub(crate) fn settled(&self, entry: TraceEntry) -> &Settled {
        &self.settled[entry.settled.index()]
    }

    /// The fired delta one entry names.
    pub(crate) fn delta(&self, entry: TraceEntry) -> &[Pointer] {
        &self.deltas[entry.delta.index()]
    }

    /// The runes and classes one entry's evaluation read.
    pub(crate) fn reads(&self, entry: TraceEntry) -> &[Read] {
        &self.reads[entry.reads.index()]
    }
}

/// What a shared memo may not answer for. An entry whose evaluation read any of these runes' content or any of these classes' membership is a miss on that shared memo. So is an entry whose key names one of the runes: the left cell's, the input's, or a raw slot's ([`TraceKey::runes_named`]). The read journal makes that second test redundant; the module doc says why it is kept.
#[derive(Clone, Debug, Default)]
pub struct Exclusion {
    runes: HashSet<Sym>,
    classes: HashSet<Sym>,
    /// The runes as the keys name them: one flag per rune-field [`crate::index::Ordinal`], indexed by ordinal up to the highest one named, so that [`Exclusion::names`] tests a key's ordinals against a slice on every shared memo probe without resolving or hashing. A name the registry knows no family by has no ordinal and no flag, and no key can name it.
    named: Box<[bool]>,
}

impl Exclusion {
    /// An exclusion over these runes and no classes.
    pub fn of(index: &SpecIndex, runes: impl IntoIterator<Item = Sym>) -> Self {
        let runes: HashSet<Sym> = runes.into_iter().collect();
        let mut named: Vec<bool> = Vec::new();
        for rune in &runes {
            if let Some(ordinal) = index.rune_ordinal(*rune) {
                let at = usize::from(ordinal.get());
                if named.len() <= at {
                    named.resize(at + 1, false);
                }
                named[at] = true;
            }
        }
        Self {
            runes,
            classes: HashSet::default(),
            named: named.into_boxed_slice(),
        }
    }

    /// The same exclusion naming these predicate classes as well.
    pub fn with_classes(mut self, classes: impl IntoIterator<Item = Sym>) -> Self {
        self.classes = classes.into_iter().collect();
        self
    }

    /// An exclusion naming nothing, under which a shared memo supplies every key it holds.
    pub fn none() -> Self {
        Self::default()
    }

    /// Whether `key` names any of this exclusion's runes, on its left, as its input, or in any slot.
    pub(crate) fn names(&self, key: &TraceKey) -> bool {
        !self.runes.is_empty()
            && key
                .runes_named()
                .any(|ordinal| self.named.get(usize::from(ordinal.get())).copied() == Some(true))
    }

    /// Whether this shared memo may answer for `key` given what its entry read: no named rune among the key's, and no excluded rune or class among the reads.
    #[cfg(test)]
    pub(crate) fn admits(&self, key: &TraceKey, reads: &[Read]) -> bool {
        if self.runes.is_empty() && self.classes.is_empty() {
            return true;
        }
        !self.names(key) && self.admits_reads(reads)
    }

    /// Whether no excluded rune or class is among an entry's reads: what a lookup tests once [`Exclusion::names`] has cleared the key, which it tests first because that needs no entry.
    pub(crate) fn admits_reads(&self, reads: &[Read]) -> bool {
        (self.runes.is_empty() && self.classes.is_empty())
            || !reads.iter().any(|read| match read {
                Read::Rune(rune) => self.runes.contains(rune),
                Read::Class(class) => self.classes.contains(class),
            })
    }

    /// One flag per rune-field ordinal, true for this exclusion's runes, as [`Exclusion::names`] tests them.
    pub(crate) fn named(&self) -> &[bool] {
        &self.named
    }

    /// The runes this exclusion names.
    pub fn runes(&self) -> &HashSet<Sym> {
        &self.runes
    }

    /// The classes this exclusion names.
    pub fn classes(&self) -> &HashSet<Sym> {
        &self.classes
    }
}

/// One memo another engine may read, and the runes it may not read it for.
#[derive(Clone, Debug)]
pub struct SharedMemo {
    pub memo: Arc<MemoSnapshot>,
    pub excluded: Exclusion,
}

/// Every rune whose settlement can differ between `default` and the configuration enabling `features`: a rune with an unlock for one of them, or with any record whose `when:` names one (an unlock's own gate, a refusal, a prefer, an extension, a contraction or a resolution). The scan reads every `when:` a rune can carry rather than the record kinds `rebuild/test_spec_load.py` pins feature conditions to, so a kind that gains a feature gate widens this set without an edit here.
///
/// A rune outside this set has no record whose outcome depends on these features, so every window naming only such runes settles identically under both configurations. That is the window locality rule applied across configurations, and the reason the delta enumeration excludes this set from its shared memo.
pub fn unlocking_runes(index: &SpecIndex, features: &[Sym]) -> HashSet<Sym> {
    let enabled: HashSet<Sym> = features.iter().copied().collect();
    let gated = |when: &When| {
        when.feature
            .is_some_and(|feature| enabled.contains(&feature))
    };
    let record_gated = |records: &[PolicyRecord]| records.iter().any(|record| gated(&record.when));
    index
        .runes()
        .iter()
        .filter(|(_, rune)| {
            let policy = &rune.policy;
            rune.stances.iter().any(|(_, stance)| {
                stance.surface.unlocks.iter().any(|unlock| {
                    enabled.contains(&unlock.feature) || unlock.when.as_ref().is_some_and(gated)
                })
            }) || record_gated(&policy.refuse)
                || record_gated(&policy.prefer)
                || record_gated(&policy.extend)
                || record_gated(&policy.contract)
                || record_gated(&policy.resolve)
        })
        .map(|(name, _)| *name)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::{ReadsId, TraceNotesId, TraceSettledId};
    use crate::index::fixtures;
    use crate::types::DecidedStage;

    fn entry_with_notes(id: usize) -> TraceEntry {
        TraceEntry::new(
            TraceSettledId::at(0),
            TraceNotesId::at(id),
            DeltaId::at(0),
            ReadsId::at(0),
            0,
            false,
            DecidedStage::Order,
        )
    }

    #[test]
    fn compact_entries_find_full_keys_and_keep_the_last_duplicate() {
        let index = fixtures::mini();
        let template = TraceKey::for_test(&index, fixtures::sym(&index, "qsPea"), [None; 4]);
        let mut records = Vec::new();
        let mut expected: HashMap<TraceKey, TraceEntry> = HashMap::default();
        for pass in 0..3 {
            for extension in (0..512i16).rev() {
                let key = TraceKey {
                    left_extension: extension,
                    ..template
                };
                let entry = entry_with_notes(pass * 512 + extension as usize);
                records.push((key, entry));
                expected.insert(key, entry);
            }
        }
        for id in 0..128 {
            let entry = entry_with_notes(id);
            records.push((template, entry));
            expected.insert(template, entry);
        }
        assert_eq!(std::mem::size_of::<(TraceKey, TraceEntry)>(), 36);
        for per_bucket in [1, SnapshotEntries::PER_BUCKET, 64] {
            let entries = SnapshotEntries::from_records_per_bucket(records.clone(), per_bucket)
                .expect("the records fit");
            assert_eq!(entries.len(), expected.len());
            let blocks = &entries.blocks[..entries.blocks.len() - 1];
            if per_bucket == SnapshotEntries::PER_BUCKET {
                assert!(
                    blocks.iter().any(|block| {
                        block.len != Block::OVERFLOW
                            && block.tags[..usize::from(block.len)]
                                .iter()
                                .any(|tag| block.matches(*tag).count_ones() > 1)
                    }),
                    "two keys in one bucket share a tag, so only the full key tells them apart"
                );
            }
            if per_bucket == 64 {
                assert!(blocks.iter().all(|block| block.len == Block::OVERFLOW));
            }
            for (key, expected) in &expected {
                assert_eq!(entries.get(key).expect("present").notes, expected.notes);
            }
            for extension in [-1, 512, i16::MAX] {
                let missing = TraceKey {
                    left_extension: extension,
                    ..template
                };
                assert!(entries.get(&missing).is_none());
            }
        }
        assert!(SnapshotEntries::default().get(&template).is_none());
        let empty = SnapshotEntries::from_records(Vec::new()).expect("empty fits");
        assert!(empty.is_empty());
        assert_eq!(empty.iter().count(), 0);
        assert!(empty.blocks.is_empty());
    }

    /// The mini fixture unlocks a `qsMay` entry under `ss03` and nothing else under any feature, so `ss03` names `qsMay` alone and an empty feature set names no rune.
    #[test]
    fn the_unlocking_runes_of_a_configuration_are_the_ones_reading_its_features() {
        let index = fixtures::mini();
        let ss03 = fixtures::sym(&index, "ss03");
        let named = unlocking_runes(&index, &[ss03]);
        let names: Vec<&str> = {
            let mut names: Vec<&str> = named.iter().map(|rune| index.resolve(*rune)).collect();
            names.sort_unstable();
            names
        };
        assert_eq!(names, ["qsMay"]);
        assert!(unlocking_runes(&index, &[]).is_empty());
    }

    /// An exclusion rejects any key naming one of its runes, on the left, as the input, or in any raw slot, and an empty exclusion admits everything.
    #[test]
    fn an_exclusion_refuses_a_key_naming_any_of_its_runes_anywhere() {
        let index = fixtures::mini();
        let may = fixtures::sym(&index, "qsMay");
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may_ordinal = index.rune_ordinal(may).expect("qsMay is modeled");
        let excluded = Exclusion::of(&index, [may]);
        let mut key = TraceKey::for_test(&index, pea, [Some(tea), None, None, None]);
        assert!(excluded.admits(&key, &[]));
        assert!(!excluded.names(&key));
        assert!(Exclusion::none().admits(&key, &[Read::Rune(may)]));
        key.runes[2] = Some(may_ordinal);
        assert!(!excluded.admits(&key, &[]));
        assert!(excluded.names(&key));
        key.runes[2] = None;
        key.left_rune = Some(may_ordinal);
        assert!(!excluded.admits(&key, &[]));
        key.left_rune = None;
        key.token = may_ordinal;
        assert!(!excluded.admits(&key, &[]));
        assert!(
            Exclusion::of(&index, [fixtures::sym(&index, "half")]).admits(&key, &[]),
            "a name that is no rune flags no ordinal"
        );
    }

    /// The exclusion tests the read journal: an entry whose evaluation read an excluded rune or class is rejected even though its key names neither, and one that read neither is admitted. A lookup that clears the key with `names` and then tests the reads alone answers as `admits` does.
    #[test]
    fn an_exclusion_refuses_an_entry_by_what_it_read() {
        let index = fixtures::mini();
        let may = fixtures::sym(&index, "qsMay");
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let class = fixtures::sym(&index, "halves-that-exit-at-x-height");
        let key = TraceKey::for_test(&index, pea, [Some(tea), None, None, None]);
        let by_reads = Exclusion::of(&index, [may]).with_classes([class]);
        assert!(by_reads.admits(&key, &[Read::Rune(pea), Read::Rune(tea)]));
        assert!(!by_reads.admits(&key, &[Read::Rune(pea), Read::Rune(may)]));
        assert!(!by_reads.admits(&key, &[Read::Class(class)]));
        assert!(
            Exclusion::of(&index, [])
                .with_classes([class])
                .admits(&key, &[Read::Rune(may)])
        );
        let may_ordinal = index.rune_ordinal(may).expect("qsMay is modeled");
        let mut naming = key;
        naming.runes[1] = Some(may_ordinal);
        let exclusions = [
            Exclusion::none(),
            Exclusion::of(&index, [may]),
            Exclusion::of(&index, []).with_classes([class]),
            by_reads,
        ];
        let reads: [&[Read]; 4] = [
            &[],
            &[Read::Rune(pea)],
            &[Read::Rune(may)],
            &[Read::Class(class)],
        ];
        for exclusion in &exclusions {
            for key in [&key, &naming] {
                for read in reads {
                    assert_eq!(
                        exclusion.admits(key, read),
                        !exclusion.names(key) && exclusion.admits_reads(read)
                    );
                }
            }
        }
    }
}
