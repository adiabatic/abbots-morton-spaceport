//! A finished trace memo detached from the engine that filled it, and the rule under which another engine may read it. An entry records what one engine settled for one window (a collapsed left, an input and four raw slots), with the pointers that evaluation fired and the runes and classes it read. By the window-locality theorem (`doc/rebuild-design.md` §10), that result depends only on the crate, the script registry and the rune files the key names. So one enumeration's memo can answer another enumeration's windows wherever the two agree on every rune a key names. By the configuration corollary, a configuration can differ from `default` only on the windows naming a rune with an unlock, a `feature:`-conditioned record, or an unlock gate under that configuration.
//!
//! This is how the per-configuration delta enumeration works. `default` enumerates first and returns its memo as a [`MemoSnapshot`]. Every other configuration's engine takes that snapshot as a [`MemoBase`] whose [`Exclusion`] names the configuration's unlocking runes ([`unlocking_runes`]), runs the same worklist from the same seeds in the same order, and reads from the base every window whose key names no unlocking rune and whose evaluation read none. The worklist is not seeded: each traversal re-derives reachability, because the memo records what a window settles to, never whether the window exists. So a cell that another configuration reaches first, or reaches only there, is still found. The fired journal carries over as it does for a hit on the engine's own memo: a base entry stores the delta its evaluation journaled and a hit replays it, so a delta configuration's `cited_provenance` is the union over the windows it visited, as in a from-scratch enumeration.
//!
//! The same rule carries a memo across builds. A table build writes each configuration's finished memo beside its tables as `memo-<config>.tsv` ([`write_memo`]), and the next build reads it back as a base whose exclusion names every rune edited in between ([`read_memo`]). A window naming no edited rune settles as it did last time, and only the rest are traced. The file holds every window the memo holds, not only the rows' windows: it includes the probe windows the liveness and fiber derivations trace, with their virtual lefts and unknown coordinates. So those derivations, which quantify over the whole alphabet, run every build and are read from the base wherever a probe names no edited rune, and a verdict that aggregates over every letter stays exact without being stored. `run_m1` decides which runes count as edited and whether the file may be read at all. The head carries the configuration, the world and an opaque stamp the writer chose. The crate fails on a file whose configuration or world is not the one asked for; the stamp, with the structure it names and the rune digests it records, is read only on the Python side.
//!
//! Which windows a base may answer is decided by what each entry's evaluation read. While a capture is open, the index journals every rune whose resolved content and every predicate class whose membership its accessors return ([`crate::index`]), and the entry stores that set beside its fired delta. An [`Exclusion`] naming runes and classes therefore rejects the entries that read one of them. A window whose deep slots name a rune its evaluation never consulted can still be read from the base when that rune changes, and a class whose membership changed invalidates only the windows that consulted it. The six runes a key names are also rejected, as a redundant check on the journal: over-invalidation costs a trace, and under-invalidation costs a wrong table.
//!
//! The snapshot is shared behind an [`Arc`] instead of copied per configuration, because the memo is the largest structure the enumeration holds, and a copy per delta configuration would again limit by memory how many delta configurations run at once. So it holds no `Rc`, no reference into any engine, and no ladder (the fixpoint never records one), and a base is read-only once built.

use std::fmt::Write as _;
use std::hash::{Hash, Hasher};
use std::io::{BufRead, Seek as _, Write as _};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use crate::engine::{
    DeltaSeat, Pointer, ReadsSeat, TraceEntry, TraceKey, TraceNotesSeat, TraceSettledSeat,
};
use crate::hash::{FastHasher, HashMap, HashSet};
use crate::index::{Read, SpecIndex};
use crate::model::{PolicyRecord, Provenance, Sym, When};
use crate::types::{
    AdjustmentToken, CellId, DecidedStage, LeftOrdinals, PackedKinds, Settled, TokenKind,
    TransitionTrace, adjustment_from_text, adjustment_text, boundary_settled,
};

/// Immutable full-key records partitioned into buckets by a hash prefix, with keys sorted inside each bucket. The bucket count is the record count over sixteen, rounded up to a power of two, which gives eight to sixteen records per bucket on average once there is more than one bucket. The index costs four bytes per bucket plus a final offset. A lookup hashes once, then binary-searches complete keys within its bucket; the hash never stands in for equality.
#[derive(Debug, Default)]
pub(crate) struct SnapshotEntries {
    records: Box<[(TraceKey, TraceEntry)]>,
    offsets: Box<[u32]>,
    prefix_bits: u32,
}

impl SnapshotEntries {
    fn bucket(key: &TraceKey, prefix_bits: u32) -> usize {
        if prefix_bits == 0 {
            return 0;
        }
        let mut hash = FastHasher::default();
        key.hash(&mut hash);
        (hash.finish() >> (64 - prefix_bits)) as usize
    }

    /// Construction holds a four-byte source position per record beside the records. Counting and in-place bucket partitioning hash each key a bounded number of times. Each bucket is sorted on key and source position, so a duplicate key keeps its last input even though partitioning reorders equal keys. Buckets of up to sixty-four records are insertion-sorted; larger ones use a temporary decorated sort, which bounds the sorting time when many keys collide. The positions and partition cursors are freed before the final array is boxed. If removing duplicates lowers the bucket count needed, the index is rebuilt over the surviving records.
    fn from_records(mut records: Vec<(TraceKey, TraceEntry)>) -> Result<Self, String> {
        let count = u32::try_from(records.len())
            .map_err(|_| "a memo holds fewer than 2^32 windows".to_owned())?;
        if records.is_empty() {
            return Ok(Self::default());
        }
        let buckets = records.len().div_ceil(16).next_power_of_two();
        let prefix_bits = buckets.trailing_zeros();
        let mut offsets = vec![0u32; buckets + 1];
        for (key, _) in &records {
            offsets[Self::bucket(key, prefix_bits) + 1] += 1;
        }
        for bucket in 0..buckets {
            offsets[bucket + 1] += offsets[bucket];
        }
        let mut positions: Vec<u32> = (0..count).collect();
        let mut next = offsets[..buckets].to_vec();
        for bucket in 0..buckets {
            while next[bucket] < offsets[bucket + 1] {
                let at = next[bucket] as usize;
                let destination = Self::bucket(&records[at].0, prefix_bits);
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
        if written.div_ceil(16).next_power_of_two() < buckets {
            drop(offsets);
            return Self::from_records(records);
        }
        Ok(Self {
            records: records.into_boxed_slice(),
            offsets: offsets.into_boxed_slice(),
            prefix_bits,
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
        let bucket = Self::bucket(key, self.prefix_bits);
        let records =
            &self.records[self.offsets[bucket] as usize..self.offsets[bucket + 1] as usize];
        records
            .binary_search_by_key(key, |(key, _)| *key)
            .ok()
            .map(|at| &records[at].1)
    }

    pub(crate) fn iter(&self) -> impl Iterator<Item = (&TraceKey, &TraceEntry)> {
        self.records.iter().map(|(key, entry)| (key, entry))
    }

    #[cfg(test)]
    fn keys(&self) -> impl Iterator<Item = &TraceKey> {
        self.records.iter().map(|(key, _)| key)
    }
}

impl From<HashMap<TraceKey, TraceEntry>> for SnapshotEntries {
    fn from(entries: HashMap<TraceKey, TraceEntry>) -> Self {
        let records = entries.into_iter().collect();
        Self::from_records(records).expect("a live memo holds fewer than 2^32 windows")
    }
}

impl<'a> IntoIterator for &'a SnapshotEntries {
    type Item = (&'a TraceKey, &'a TraceEntry);
    type IntoIter = std::iter::Map<
        std::slice::Iter<'a, (TraceKey, TraceEntry)>,
        fn(&'a (TraceKey, TraceEntry)) -> (&'a TraceKey, &'a TraceEntry),
    >;

    fn into_iter(self) -> Self::IntoIter {
        self.records.iter().map(|(key, entry)| (key, entry))
    }
}

#[cfg(test)]
impl std::ops::Index<&TraceKey> for SnapshotEntries {
    type Output = TraceEntry;

    fn index(&self, key: &TraceKey) -> &Self::Output {
        self.get(key).expect("the snapshot holds the window")
    }
}

/// One engine's finished trace memo: compact immutable entries and the four tables their seats index. The tables are the engine memo's own pools flattened, so an entry read through the snapshot resolves as it did in the engine that recorded it. The live engine's memo is a hash map; the snapshot has no hash-table slack or control bytes.
#[derive(Debug, Default)]
pub struct MemoSnapshot {
    pub(crate) entries: SnapshotEntries,
    pub(crate) settled: Vec<Settled>,
    pub(crate) notes: Vec<Vec<String>>,
    pub(crate) deltas: Vec<Box<[Pointer]>>,
    pub(crate) reads: Vec<Box<[Read]>>,
}

impl MemoSnapshot {
    /// How many windows this snapshot holds.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The trace one entry stands for, rebuilt from the tables as the recording engine's miss returned it, without the ladder, which the fixpoint never records.
    pub(crate) fn trace(&self, entry: TraceEntry) -> TransitionTrace {
        TransitionTrace {
            settled: self.settled[entry.settled.index()].clone(),
            joint_floor: entry.joint_floor(),
            prospect: i64::from(entry.prospect()),
            decided_stage: entry.decided_stage(),
            notes: self.notes[entry.notes.index()].to_vec(),
            ladder: None,
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

/// What a base may not answer for. An entry whose evaluation read any of these runes' content or any of these classes' membership is a miss on that base. So is an entry whose key names one of the runes: the left cell's, the input's, or a raw slot's ([`TraceKey::runes_named`]). The read journal makes that second test redundant; the module doc says why it is kept.
#[derive(Clone, Debug, Default)]
pub struct Exclusion {
    runes: HashSet<Sym>,
    classes: HashSet<Sym>,
    /// The runes as the keys name them: one flag per rune-field [`crate::index::Ordinal`], indexed by ordinal up to the highest one named, so that [`Exclusion::admits`] tests a key's ordinals against a slice on every base probe without resolving or hashing. A name the registry knows no family by has no ordinal and no flag, and no key can name it.
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

    /// An exclusion naming nothing, under which a base supplies every key it holds.
    pub fn none() -> Self {
        Self::default()
    }

    /// Whether `key` names any of this exclusion's runes, on its left, as its input, or in any slot.
    pub(crate) fn names(&self, key: &TraceKey) -> bool {
        key.runes_named()
            .any(|ordinal| self.named.get(usize::from(ordinal.get())).copied() == Some(true))
    }

    /// Whether this base may answer for `key` given what its entry read: no named rune among the key's, and no excluded rune or class among the reads.
    pub(crate) fn admits(&self, key: &TraceKey, reads: &[Read]) -> bool {
        if self.runes.is_empty() && self.classes.is_empty() {
            return true;
        }
        !self.names(key)
            && !reads.iter().any(|read| match read {
                Read::Rune(rune) => self.runes.contains(rune),
                Read::Class(class) => self.classes.contains(class),
            })
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
pub struct MemoBase {
    pub memo: Arc<MemoSnapshot>,
    pub excluded: Exclusion,
}

/// Every rune whose settlement can differ between `default` and the configuration enabling `features`: a rune with an unlock for one of them, or with any record whose `when:` names one (an unlock's own gate, a refusal, a prefer, an extension, a contraction or a resolution). The scan reads every `when:` a rune can carry rather than the record kinds `rebuild/test_spec_load.py` pins feature conditions to, so a kind that gains a feature gate widens this set without an edit here.
///
/// A rune outside this set has no record whose outcome depends on these features, so every window naming only such runes settles identically under both configurations. That is the configuration corollary of the window-locality theorem, and the reason the delta enumeration excludes this set from its base.
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

/// The marker on a memo file's head line. A file with any other marker is a different format.
pub const MEMO_FORMAT: &str = "ams-m1-memo/1";

/// What a memo file's head records: the configuration it was traced under, the world it was traced in (the enumeration's semantics tokens, [`crate::fixpoint::EnumerationModes::world_token`]), and the stamp its writer chose. The crate checks the first two. The stamp is opaque here; `run_m1` reads it to decide which runes changed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MemoHead {
    pub config: String,
    pub world: String,
    pub stamp: String,
}

/// The separator between the members of a list inside one field: a notes list, a fired delta, or a read set. It is the ASCII unit separator because a note is prose (`unlocked by ss03`) and may contain a space, while nothing the engine writes contains this byte. The writer returns an error for a note that contains it instead of writing a list that would read back split.
const LIST_SEPARATOR: char = '\u{1f}';

/// Where one configuration's memo file sits under a directory.
pub fn memo_path(dir: &Path, token: &str) -> PathBuf {
    dir.join(format!("memo-{token}.tsv"))
}

/// The file's `Y` table while it is written. A symbol gets its seat the first time a window, a record or a pointer names it, so every rune, stance, height and pointer half is written once as text and every later mention is an integer.
struct Symbols {
    seats: HashMap<Sym, u32>,
    lines: Vec<String>,
}

impl Symbols {
    fn new() -> Self {
        Self {
            seats: HashMap::default(),
            lines: Vec::new(),
        }
    }

    fn seat(&mut self, index: &SpecIndex, symbol: Sym) -> u32 {
        if let Some(&seat) = self.seats.get(&symbol) {
            return seat;
        }
        let seat =
            u32::try_from(self.lines.len()).expect("a memo file names fewer than 2^32 symbols");
        self.seats.insert(symbol, seat);
        self.lines.push(format!("Y\t{}", index.resolve(symbol)));
        seat
    }

    fn optional(&mut self, index: &SpecIndex, symbol: Option<Sym>) -> String {
        symbol.map_or_else(
            || "-".to_owned(),
            |symbol| self.seat(index, symbol).to_string(),
        )
    }

    /// One slot as the file writes it: `#` and the kind's first letter for a non-letter, the rune's symbol seat for a letter.
    fn slot(&mut self, index: &SpecIndex, kind: TokenKind, rune: Option<Sym>) -> String {
        match rune {
            Some(rune) => self.seat(index, rune).to_string(),
            None => format!("#{}", kind_letter(kind)),
        }
    }
}

/// The letter the file writes for each kind: the first letter of its name, which differs across the six kinds.
fn kind_letter(kind: TokenKind) -> char {
    kind.as_str()
        .chars()
        .next()
        .expect("every kind has a spelling")
}

fn kind_of_letter(letter: &str) -> Option<TokenKind> {
    [
        TokenKind::Edge,
        TokenKind::Space,
        TokenKind::Zwnj,
        TokenKind::NamerDot,
        TokenKind::Letter,
        TokenKind::Unknown,
    ]
    .into_iter()
    .find(|kind| kind_letter(*kind).to_string() == letter)
}

/// The file's interning of one kind of record while it is written: each distinct value once, at the seat of its `S`, `N`, `D` or `R` line.
struct FileTable<T> {
    seats: HashMap<T, u32>,
    lines: Vec<String>,
}

impl<T: std::hash::Hash + Eq + Clone> FileTable<T> {
    fn new() -> Self {
        Self {
            seats: HashMap::default(),
            lines: Vec::new(),
        }
    }

    fn seat(
        &mut self,
        value: &T,
        spell: impl FnOnce(&T) -> Result<String, String>,
    ) -> Result<u32, String> {
        if let Some(&seat) = self.seats.get(value) {
            return Ok(seat);
        }
        let seat =
            u32::try_from(self.lines.len()).expect("a memo file seats fewer than 2^32 records");
        let line = spell(value)?;
        self.seats.insert(value.clone(), seat);
        self.lines.push(line);
        Ok(seat)
    }
}

fn settled_line(index: &SpecIndex, symbols: &mut Symbols, settled: &Settled) -> String {
    let cell = &settled.cell;
    let adjustments = if cell.adjustments.is_empty() {
        "-".to_owned()
    } else {
        cell.adjustments
            .iter()
            .map(|token| adjustment_text(index, *token))
            .collect::<Vec<String>>()
            .join(" ")
    };
    format!(
        "S\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
        symbols.seat(index, cell.rune),
        symbols.seat(index, cell.stance),
        symbols.optional(index, cell.entry),
        symbols.optional(index, cell.exit),
        adjustments,
        symbols.optional(index, settled.seam),
        settled.extension
    )
}

fn delta_line(index: &SpecIndex, symbols: &mut Symbols, pointers: &[Pointer]) -> String {
    let spelled: Vec<String> = pointers
        .iter()
        .map(|pointer| {
            format!(
                "{}:{}",
                symbols.seat(index, pointer.file),
                symbols.seat(index, pointer.path)
            )
        })
        .collect();
    format!("D\t{}", spelled.join(&LIST_SEPARATOR.to_string()))
}

/// One read set as its `R` line: `r` and a rune's symbol seat, or `c` and a class's.
fn reads_line(index: &SpecIndex, symbols: &mut Symbols, reads: &[Read]) -> String {
    let spelled: Vec<String> = reads
        .iter()
        .map(|read| match read {
            Read::Rune(rune) => format!("r{}", symbols.seat(index, *rune)),
            Read::Class(class) => format!("c{}", symbols.seat(index, *class)),
        })
        .collect();
    format!("R\t{}", spelled.join(&LIST_SEPARATOR.to_string()))
}

/// One notes list as its `N` line, or an error for a note the format cannot hold.
fn notes_line(list: &[String]) -> Result<String, String> {
    for note in list {
        if note.contains([LIST_SEPARATOR, '\t', '\n']) {
            return Err(format!("a note the memo format cannot carry: {note:?}"));
        }
    }
    Ok(format!("N\t{}", list.join(&LIST_SEPARATOR.to_string())))
}

/// One window of the file while it is written: the key by reference; four seats, which hold the source's pool seats until the first pass overwrites them with the file's seats; the three bytes of the entry that the `E` line writes; and the index of the source that holds the window. A row is thirty-two bytes at align eight, and the `const` below checks that size so an added field cannot widen it unnoticed. One row per window of the union lives from the collection loop until the last byte is written, in a vector reserved once at the summed source entry counts, which the union cannot exceed, so no push reallocates. Issue #264 explains why the four seats are not packed into one `u64` and why the entry's three bytes are copied here instead of fetched again at write time.
struct Row<'a> {
    key: &'a TraceKey,
    seats: [u32; 4],
    prospect: i8,
    joint_floor: bool,
    decided_stage: DecidedStage,
    source: u16,
}

const _: () = assert!(std::mem::size_of::<Row<'static>>() == 32);

/// A pool seat as a [`Row`] holds it. The conversion is checked, as every seat mint is, so an index past `u32` panics instead of writing a wrong file.
fn pool_seat(index: usize) -> u32 {
    u32::try_from(index).expect("a memo pool seats fewer than 2^32 records")
}

/// The memo file one configuration's build writes: its path, its head, and the bases whose admitted windows are written with its own, so the file is the union a later build reads. [`crate::fixpoint::enumerate_for_tables`] writes it at the enumeration's release point, from the finished snapshot the engine returns after freeing its other memos and before that snapshot is dropped, so a configuration that does not keep its memo for other enumerations (`keep_memo`) holds none past its enumeration.
pub struct MemoFile {
    pub path: PathBuf,
    pub head: MemoHead,
    pub carried: Vec<MemoBase>,
}

/// Writes one configuration's memo as `memo-<config>.tsv`: the head line, then five tables, then one `E` line per window. The tables are `Y` for every symbol the file names, `S` for the settled records, `N` for the notes lists, `D` for the fired deltas and `R` for the read sets, each seated in file order, with list members separated by [`LIST_SEPARATOR`]. An `E` line names its key by symbol seats, then its four record seats, its prospect, its joint flag and its stage.
///
/// The windows are `own`'s, then every window of each carried base that the base's exclusion admits and that no earlier source both holds and admits, so the file is the union a later build may read, with each window written once. They are written in key order, so two builds of the same memo write the same file. Every name is written as text once, in the `Y` table, because a symbol's integer is this spec's interning order and the next spec's may differ. The windows are streamed to the file, and per window only one [`Row`] is held until the last byte is written, because a configuration's memo holds millions of windows.
///
/// Neither the stamp nor the configuration may contain a tab or a newline, since the head is one tab-separated line; the writer returns an error for either.
pub fn write_memo(
    index: &SpecIndex,
    path: &Path,
    head: &MemoHead,
    own: &MemoSnapshot,
    carried: &[MemoBase],
) -> Result<(), String> {
    if head.stamp.contains(['\t', '\n']) || head.config.contains(['\t', '\n']) {
        return Err("a memo stamp is one line with no tabs".to_owned());
    }
    let none = Exclusion::none();
    let sources: Vec<(&MemoSnapshot, &Exclusion)> = std::iter::once((own, &none))
        .chain(carried.iter().map(|base| (&*base.memo, &base.excluded)))
        .collect();
    let held_earlier = |seat: usize, key: &TraceKey| {
        sources[..seat].iter().any(|(memo, excluded)| {
            memo.entries
                .get(key)
                .is_some_and(|entry| excluded.admits(key, memo.reads(*entry)))
        })
    };
    let mut rows: Vec<Row<'_>> =
        Vec::with_capacity(sources.iter().map(|(memo, _)| memo.len()).sum());
    for (seat, (memo, excluded)) in sources.iter().enumerate() {
        let source = u16::try_from(seat).expect("a memo is written from fewer than 2^16 sources");
        for (key, entry) in memo.entries.iter() {
            if !excluded.admits(key, memo.reads(*entry)) || held_earlier(seat, key) {
                continue;
            }
            rows.push(Row {
                key,
                seats: [
                    pool_seat(entry.settled.index()),
                    pool_seat(entry.notes.index()),
                    pool_seat(entry.delta.index()),
                    pool_seat(entry.reads.index()),
                ],
                prospect: entry.prospect(),
                joint_floor: entry.joint_floor(),
                decided_stage: entry.decided_stage(),
                source,
            });
        }
    }
    rows.sort_unstable_by_key(|row| *row.key);
    let mut symbols = Symbols::new();
    let mut settled: FileTable<Settled> = FileTable::new();
    let mut notes: FileTable<Vec<String>> = FileTable::new();
    let mut deltas: FileTable<Box<[Pointer]>> = FileTable::new();
    let mut reads: FileTable<Box<[Read]>> = FileTable::new();
    // Two passes over the same order: the first seats every symbol and record so the tables can go out ahead of the windows that name them, the second streams the windows.
    for row in &mut rows {
        let (memo, _) = sources[usize::from(row.source)];
        let key = row.key;
        for ordinal in key.runes_named() {
            symbols.seat(index, index.rune_at_ordinal(ordinal));
        }
        if let Some(stance) = key.left_stance {
            symbols.seat(index, index.stance_at_ordinal(stance));
        }
        if let Some(seam) = key.left_seam {
            symbols.seat(index, index.seam_at_ordinal(seam));
        }
        let settled_seat = settled.seat(&memo.settled[row.seats[0] as usize], |record| {
            Ok(settled_line(index, &mut symbols, record))
        })?;
        if TraceSettledSeat::try_at(settled_seat as usize).is_none() {
            return Err(format!(
                "{}: a memo seats fewer than 65,536 settled records",
                path.display()
            ));
        }
        let notes_seat = notes.seat(&memo.notes[row.seats[1] as usize], |list| notes_line(list))?;
        if TraceNotesSeat::try_at(notes_seat as usize).is_none() {
            return Err(format!(
                "{}: a memo seats fewer than 65,536 notes lists",
                path.display()
            ));
        }
        let delta_seat = deltas.seat(&memo.deltas[row.seats[2] as usize], |delta| {
            Ok(delta_line(index, &mut symbols, delta))
        })?;
        let reads_seat = reads.seat(&memo.reads[row.seats[3] as usize], |list| {
            Ok(reads_line(index, &mut symbols, list))
        })?;
        row.seats = [settled_seat, notes_seat, delta_seat, reads_seat];
    }
    let file =
        std::fs::File::create(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let mut out = std::io::BufWriter::with_capacity(1 << 20, file);
    let complain = |error: std::io::Error| format!("{}: {error}", path.display());
    writeln!(
        out,
        "# {MEMO_FORMAT}\t{}\t{}\t{}",
        head.config, head.world, head.stamp
    )
    .map_err(complain)?;
    for table in [
        &symbols.lines,
        &settled.lines,
        &notes.lines,
        &deltas.lines,
        &reads.lines,
    ] {
        for line in table {
            writeln!(out, "{line}").map_err(complain)?;
        }
    }
    let mut line = String::new();
    for row in &rows {
        let key = row.key;
        line.clear();
        let _ = write!(
            line,
            "E\t{}\t{}\t{}\t{}\t{}\t{}",
            kind_letter(key.left_kind()),
            symbols.optional(index, key.left_rune.map(|rune| index.rune_at_ordinal(rune))),
            symbols.optional(
                index,
                key.left_stance
                    .map(|stance| index.stance_at_ordinal(stance))
            ),
            symbols.optional(index, key.left_seam.map(|seam| index.seam_at_ordinal(seam))),
            key.left_extension,
            symbols.seat(index, index.rune_at_ordinal(key.token))
        );
        for slot in 0..4 {
            let _ = write!(
                line,
                "\t{}",
                symbols.slot(
                    index,
                    key.slot_kind(slot),
                    key.runes[slot].map(|rune| index.rune_at_ordinal(rune))
                )
            );
        }
        let [settled_seat, notes_seat, delta_seat, reads_seat] = row.seats;
        let _ = write!(
            line,
            "\t{settled_seat}\t{notes_seat}\t{delta_seat}\t{reads_seat}\t{}\t{}\t{}",
            row.prospect,
            u8::from(row.joint_floor),
            row.decided_stage.as_str()
        );
        writeln!(out, "{line}").map_err(complain)?;
    }
    out.flush().map_err(complain)
}

/// The head of a memo file, read without the rest of it.
pub fn read_memo_head(path: &Path) -> Result<MemoHead, String> {
    let file = std::fs::File::open(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let mut first = String::new();
    std::io::BufReader::new(file)
        .read_line(&mut first)
        .map_err(|error| format!("{}: {error}", path.display()))?;
    parse_head(first.trim_end_matches('\n')).ok_or_else(|| {
        format!(
            "{}: not an {MEMO_FORMAT} memo (head line {first:?})",
            path.display()
        )
    })
}

fn parse_head(line: &str) -> Option<MemoHead> {
    let rest = line.strip_prefix(&format!("# {MEMO_FORMAT}\t"))?;
    let mut fields = rest.splitn(3, '\t');
    let config = fields.next()?.to_owned();
    let world = fields.next()?.to_owned();
    let stamp = fields.next()?.to_owned();
    Some(MemoHead {
        config,
        world,
        stamp,
    })
}

/// A seat in the file's symbol table resolved to this spec's symbol: `None` for an absent field (`-`), and `Err` for a malformed seat, a seat the table never assigned, or a name this spec never interned.
fn symbol_at(table: &[Option<Sym>], text: &str) -> Result<Option<Sym>, ()> {
    if text == "-" {
        return Ok(None);
    }
    let seat: usize = text.parse().map_err(|_| ())?;
    table.get(seat).copied().flatten().map(Some).ok_or(())
}

fn seat_at(text: &str) -> Option<usize> {
    text.parse().ok()
}

/// One memo file read back as a snapshot over this spec, keeping only the windows `keep` admits. A first byte scan counts the window lines so the record vector is allocated once instead of growing by doubling during a large load; capacity left unused by rejected windows is released when the records are boxed. The head's configuration and world must match `expected`'s; the stamp is the caller's concern and is not read.
///
/// Some windows are dropped instead of failing the read, because each names something that changed and the caller's exclusion would reject it anyway:
///
/// - a window naming a symbol this spec never interned: a rune, stance or height that left the spec, or a pointer whose record did;
/// - a window naming a rune the registry knows no family by, a stance name no rune declares, or a height no seam field holds, because the key stores each as its field's [`crate::index::Ordinal`] and this spec's index has none for it;
/// - a window seated on a settled record whose cell no left of this spec keys ([`LeftOrdinals::of`]), so that a stale record cannot reach a left, or whose adjustment tokens this spec cannot parse.
///
/// A line the format does not define fails the read with an error naming the line.
pub(crate) fn read_memo(
    index: &SpecIndex,
    path: &Path,
    expected: &MemoHead,
    keep: impl Fn(&TraceKey) -> bool,
) -> Result<MemoSnapshot, String> {
    let file = std::fs::File::open(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let complain = |number: usize, what: &str| format!("{}: line {number}: {what}", path.display());
    let mut reader = std::io::BufReader::with_capacity(1 << 20, file);
    let mut window_lines = 0usize;
    loop {
        let first = reader
            .fill_buf()
            .map_err(|error| format!("{}: {error}", path.display()))?
            .first()
            .copied();
        let Some(first) = first else { break };
        if first == b'E' {
            window_lines = window_lines
                .checked_add(1)
                .filter(|count| u32::try_from(*count).is_ok())
                .ok_or_else(|| {
                    format!("{}: a memo holds fewer than 2^32 windows", path.display())
                })?;
        }
        reader
            .skip_until(b'\n')
            .map_err(|error| format!("{}: {error}", path.display()))?;
    }
    reader
        .rewind()
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let mut lines = reader.lines().enumerate();
    let (_, head) = lines
        .next()
        .ok_or_else(|| complain(1, "an empty file is not a memo"))?;
    let head = head.map_err(|error| format!("{}: {error}", path.display()))?;
    let head = parse_head(&head).ok_or_else(|| complain(1, "not a memo head line"))?;
    if head.config != expected.config || head.world != expected.world {
        return Err(format!(
            "{}: a memo for configuration {} in world {}, not {} in {}",
            path.display(),
            head.config,
            head.world,
            expected.config,
            expected.world
        ));
    }
    let placeholder = boundary_settled(index.vocab(), TokenKind::Edge);
    let mut memo = MemoSnapshot::default();
    let mut records = Vec::with_capacity(window_lines);
    let mut symbols: Vec<Option<Sym>> = Vec::new();
    let mut settled_usable: Vec<bool> = Vec::new();
    let mut delta_usable: Vec<bool> = Vec::new();
    let mut reads_usable: Vec<bool> = Vec::new();
    for (offset, line) in lines {
        let number = offset + 1;
        let line = line.map_err(|error| format!("{}: {error}", path.display()))?;
        let mut fields = line.split('\t');
        match fields.next() {
            Some("Y") => {
                let text = fields.next().unwrap_or_default();
                if fields.next().is_some() {
                    return Err(complain(number, "a symbol is one field"));
                }
                symbols.push(index.sym_of(text));
            }
            Some("S") => {
                if memo.settled.len() == TraceSettledSeat::CAPACITY {
                    return Err(complain(
                        number,
                        "a memo seats fewer than 65,536 settled records",
                    ));
                }
                let fields: Vec<&str> = fields.collect();
                let [rune, stance, entry, exit, adjustments, seam, extension] = fields.as_slice()
                else {
                    return Err(complain(number, "a settled record has seven fields"));
                };
                let extension: i64 = extension
                    .parse()
                    .map_err(|_| complain(number, "an extension is a count"))?;
                let parsed = (|| {
                    let adjustments: Vec<AdjustmentToken> = if *adjustments == "-" {
                        Vec::new()
                    } else {
                        adjustments
                            .split(' ')
                            .map(|token| adjustment_from_text(index, token))
                            .collect::<Option<Vec<_>>>()?
                    };
                    Some(Settled {
                        cell: CellId {
                            rune: symbol_at(&symbols, rune).ok()??,
                            stance: symbol_at(&symbols, stance).ok()??,
                            entry: symbol_at(&symbols, entry).ok()?,
                            exit: symbol_at(&symbols, exit).ok()?,
                            adjustments,
                        },
                        seam: symbol_at(&symbols, seam).ok()?,
                        extension,
                    })
                })()
                .filter(|settled| LeftOrdinals::of(index, settled).is_some());
                settled_usable.push(parsed.is_some());
                memo.settled
                    .push(parsed.unwrap_or_else(|| placeholder.clone()));
            }
            Some("N") => {
                if memo.notes.len() == TraceNotesSeat::CAPACITY {
                    return Err(complain(
                        number,
                        "a memo seats fewer than 65,536 notes lists",
                    ));
                }
                let text = fields.next().unwrap_or_default();
                if fields.next().is_some() {
                    return Err(complain(number, "a notes list is one field"));
                }
                memo.notes.push(if text.is_empty() {
                    Vec::new()
                } else {
                    text.split(LIST_SEPARATOR).map(str::to_owned).collect()
                });
            }
            Some("D") => {
                let text = fields.next().unwrap_or_default();
                if fields.next().is_some() {
                    return Err(complain(number, "a delta is one field"));
                }
                let parsed: Option<Vec<Pointer>> = if text.is_empty() {
                    Some(Vec::new())
                } else {
                    text.split(LIST_SEPARATOR)
                        .map(|pointer| {
                            let (file, path) = pointer.split_once(':')?;
                            Some(Pointer::of(&Provenance {
                                file: symbol_at(&symbols, file).ok()??,
                                path: symbol_at(&symbols, path).ok()??,
                            }))
                        })
                        .collect()
                };
                delta_usable.push(parsed.is_some());
                memo.deltas
                    .push(parsed.unwrap_or_default().into_boxed_slice());
            }
            Some("R") => {
                let text = fields.next().unwrap_or_default();
                if fields.next().is_some() {
                    return Err(complain(number, "a read set is one field"));
                }
                let parsed: Option<Vec<Read>> = if text.is_empty() {
                    Some(Vec::new())
                } else {
                    text.split(LIST_SEPARATOR)
                        .map(|read| {
                            let symbol = symbol_at(&symbols, &read[1..]).ok()??;
                            match &read[..1] {
                                "r" => Some(Read::Rune(symbol)),
                                "c" => Some(Read::Class(symbol)),
                                _ => None,
                            }
                        })
                        .collect()
                };
                reads_usable.push(parsed.is_some());
                memo.reads
                    .push(parsed.unwrap_or_default().into_boxed_slice());
            }
            Some("E") => {
                let fields: Vec<&str> = fields.collect();
                let [
                    left_kind,
                    left_rune,
                    left_stance,
                    left_seam,
                    left_extension,
                    token,
                    slot1,
                    slot2,
                    slot3,
                    slot4,
                    settled_seat,
                    notes_seat,
                    delta_seat,
                    reads_seat,
                    prospect,
                    joint,
                    stage,
                ] = fields.as_slice()
                else {
                    return Err(complain(number, "a window has seventeen fields"));
                };
                let left_kind = kind_of_letter(left_kind)
                    .ok_or_else(|| complain(number, "a left kind is one of the six"))?;
                let left_extension: i16 = left_extension
                    .parse()
                    .map_err(|_| complain(number, "a left extension is a count"))?;
                let (Some(settled_seat), Some(notes_seat), Some(delta_seat), Some(reads_seat)) = (
                    seat_at(settled_seat),
                    seat_at(notes_seat),
                    seat_at(delta_seat),
                    seat_at(reads_seat),
                ) else {
                    return Err(complain(number, "a seat is a count"));
                };
                let prospect: i64 = prospect
                    .parse()
                    .ok()
                    .filter(|term| (0..=1).contains(term))
                    .ok_or_else(|| complain(number, "a prospect is a seam count, zero or one"))?;
                let joint_floor = match *joint {
                    "0" => false,
                    "1" => true,
                    _ => return Err(complain(number, "a joint flag is 0 or 1")),
                };
                let decided_stage = DecidedStage::from_text(stage)
                    .ok_or_else(|| complain(number, "a stage is one of the seven"))?;
                if settled_seat >= memo.settled.len()
                    || notes_seat >= memo.notes.len()
                    || delta_seat >= memo.deltas.len()
                    || reads_seat >= memo.reads.len()
                {
                    return Err(complain(
                        number,
                        "a seat names a record the file seated first",
                    ));
                }
                if !settled_usable[settled_seat]
                    || !delta_usable[delta_seat]
                    || !reads_usable[reads_seat]
                {
                    continue;
                }
                let key = (|| {
                    let mut kinds = [TokenKind::Edge; 4];
                    let mut runes = [None; 4];
                    for (slot, text) in [slot1, slot2, slot3, slot4].into_iter().enumerate() {
                        match text.strip_prefix('#') {
                            Some(letter) => {
                                kinds[slot] = kind_of_letter(letter)?;
                            }
                            None => {
                                kinds[slot] = TokenKind::Letter;
                                runes[slot] =
                                    Some(index.rune_ordinal(symbol_at(&symbols, text).ok()??)?);
                            }
                        }
                    }
                    let left_rune = match symbol_at(&symbols, left_rune).ok()? {
                        Some(rune) => Some(index.rune_ordinal(rune)?),
                        None => None,
                    };
                    let left_stance = match symbol_at(&symbols, left_stance).ok()? {
                        Some(stance) => Some(index.stance_ordinal(stance)?),
                        None => None,
                    };
                    let left_seam = match symbol_at(&symbols, left_seam).ok()? {
                        Some(seam) => Some(index.seam_ordinal(seam)?),
                        None => None,
                    };
                    Some(TraceKey {
                        left_rune,
                        left_stance,
                        left_seam,
                        left_extension,
                        token: index.rune_ordinal(symbol_at(&symbols, token).ok()??)?,
                        runes,
                        kinds: PackedKinds::of(&[
                            left_kind, kinds[0], kinds[1], kinds[2], kinds[3],
                        ]),
                    })
                })();
                let Some(key) = key else {
                    continue;
                };
                if !keep(&key) {
                    continue;
                }
                if records.len() == u32::MAX as usize {
                    return Err(complain(number, "a memo holds fewer than 2^32 windows"));
                }
                records.push((
                    key,
                    TraceEntry::new(
                        TraceSettledSeat::at(settled_seat),
                        TraceNotesSeat::at(notes_seat),
                        DeltaSeat::at(delta_seat),
                        ReadsSeat::at(reads_seat),
                        prospect,
                        joint_floor,
                        decided_stage,
                    ),
                ));
            }
            Some(other) => {
                return Err(complain(
                    number,
                    &format!("{other:?} is not a line the memo format spells"),
                ));
            }
            None => return Err(complain(number, "an empty line")),
        }
    }
    memo.entries = SnapshotEntries::from_records(records)
        .map_err(|error| format!("{}: {error}", path.display()))?;
    Ok(memo)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixpoint::{EnumerationModes, Seed, enumerate_for_tables};
    use crate::index::fixtures;
    use crate::stream::emit_transitions;

    /// A scratch directory of this module's own under `target/`, cleared first.
    fn scratch(name: &str) -> PathBuf {
        let directory = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target/test-scratch")
            .join(name);
        let _ = std::fs::remove_dir_all(&directory);
        std::fs::create_dir_all(&directory).expect("the scratch directory is makeable");
        directory
    }

    /// The mini dump with `qsTea`'s refusal removed, the edit the seeding tests exclude `qsTea` for.
    fn mini_dump_without_the_tea_refusal() -> String {
        let refusal = format!(
            "\"refuse\":{}",
            fixtures::seq(&[&fixtures::record(&[
                ("kind", "\"refuse\""),
                (
                    "provenance",
                    &fixtures::names(&["qsTea.yaml", "policy.refuse[0]"])
                ),
            ])])
        );
        let dump = fixtures::mini_dump();
        assert!(
            dump.contains(&refusal),
            "the fixture spells the refusal this test strikes"
        );
        dump.replacen(&refusal, "\"refuse\":[]", 1)
    }

    fn head(config: &str) -> MemoHead {
        MemoHead {
            config: config.to_owned(),
            world: EnumerationModes::default().world_token(),
            stamp: "stamp-under-test".to_owned(),
        }
    }

    fn entry_with_notes(seat: usize) -> TraceEntry {
        TraceEntry::new(
            TraceSettledSeat::at(0),
            TraceNotesSeat::at(seat),
            DeltaSeat::at(0),
            ReadsSeat::at(0),
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
        for seat in 0..128 {
            let entry = entry_with_notes(seat);
            records.push((template, entry));
            expected.insert(template, entry);
        }
        let entries = SnapshotEntries::from_records(records).expect("the records fit");
        assert_eq!(entries.len(), expected.len());
        assert_eq!(std::mem::size_of::<(TraceKey, TraceEntry)>(), 36);
        assert!(entries.offsets.windows(2).any(|pair| pair[1] - pair[0] > 1));
        for (key, expected) in expected {
            assert_eq!(entries.get(&key).expect("present").notes, expected.notes);
        }
        for extension in [-1, 512, i16::MAX] {
            let missing = TraceKey {
                left_extension: extension,
                ..template
            };
            assert!(entries.get(&missing).is_none());
        }
        assert!(SnapshotEntries::default().get(&template).is_none());
        let empty = SnapshotEntries::from_records(Vec::new()).expect("empty fits");
        assert!(empty.is_empty());
        assert_eq!(empty.iter().count(), 0);
        assert!(empty.offsets.is_empty());
    }

    #[test]
    fn duplicate_file_windows_keep_the_last_entry() {
        let index = fixtures::mini();
        let (_, memo) = enumerate_keeping(&index, &[], Vec::new());
        let path = scratch("memo-duplicate-window").join("memo-default.tsv");
        write_memo(&index, &path, &head("default"), &memo, &[]).expect("writes");
        let mut text = std::fs::read_to_string(&path).expect("the file");
        let mut duplicate: Vec<_> = text
            .lines()
            .find(|line| line.starts_with("E\t"))
            .expect("a window")
            .split('\t')
            .map(str::to_owned)
            .collect();
        duplicate[15] = if duplicate[15] == "0" { "1" } else { "0" }.to_owned();
        text.push_str(&duplicate.join("\t"));
        text.push('\n');
        std::fs::write(&path, text).expect("the duplicate appends");
        let back = read_memo(&index, &path, &head("default"), |_| true).expect("reads");
        assert_eq!(back.len(), memo.len());
        let changed = memo
            .entries
            .iter()
            .filter(|(key, entry)| {
                back.entries.get(key).expect("present").prospect() != entry.prospect()
            })
            .count();
        assert_eq!(changed, 1, "the final duplicate replaces its first record");
    }

    #[test]
    fn file_windows_are_indexed_after_symbol_and_ordinal_remapping() {
        let before = fixtures::mini();
        let (_, memo) = enumerate_keeping(&before, &[], Vec::new());
        let path = scratch("memo-remapped-windows").join("memo-default.tsv");
        write_memo(&before, &path, &head("default"), &memo, &[]).expect("writes");
        let mut spec =
            crate::parse::parse_spec(&fixtures::mini_dump()).expect("the fixture parses");
        let mut reversed = crate::model::Table::new();
        for (name, rune) in spec.root.runes.iter().rev() {
            reversed.push(*name, rune.clone());
        }
        spec.root.runes = reversed;
        let after = fixtures::index_of(&crate::emit::emit_spec(&spec));
        let pea_before = fixtures::sym(&before, "qsPea");
        let pea_after = fixtures::sym(&after, "qsPea");
        assert_ne!(pea_before, pea_after, "the symbol interning order changes");
        assert_ne!(
            before.rune_ordinal(pea_before),
            after.rune_ordinal(pea_after),
            "the key's field-local ordinal changes too"
        );
        let back =
            read_memo(&after, &path, &head("default"), |_| true).expect("reads remapped keys");
        assert_eq!(back.len(), memo.len());
        let (expected, fresh) = enumerate_keeping(&after, &[], Vec::new());
        for (key, entry) in fresh.entries.iter() {
            let loaded = *back.entries.get(key).expect("the remapped key is found");
            assert_eq!(back.trace(loaded), fresh.trace(*entry));
            assert_eq!(
                back.delta(loaded).iter().collect::<HashSet<_>>(),
                fresh.delta(*entry).iter().collect::<HashSet<_>>()
            );
            assert_eq!(
                back.reads(loaded).iter().collect::<HashSet<_>>(),
                fresh.reads(*entry).iter().collect::<HashSet<_>>()
            );
        }
        let (seeded, own) = enumerate_keeping(
            &after,
            &[],
            vec![MemoBase {
                memo: Arc::new(back),
                excluded: Exclusion::none(),
            }],
        );
        assert!(own.is_empty());
        assert_eq!(
            emit_transitions(&after, &seeded),
            emit_transitions(&after, &expected)
        );
    }

    /// One configuration enumerated with its memo kept, over whatever bases it is handed.
    fn enumerate_keeping(
        index: &SpecIndex,
        features: &[Sym],
        bases: Vec<MemoBase>,
    ) -> (crate::stream::FixpointProduct, MemoSnapshot) {
        let enumeration = enumerate_for_tables(
            index,
            features,
            EnumerationModes::default(),
            None,
            Seed {
                bases,
                keep_memo: true,
            },
            None,
        )
        .expect("the fixture closes");
        (enumeration.product, enumeration.memo.expect("kept"))
    }

    /// Written and read back over the same spec, the file holds every window with its record, notes, delta, read set and stage. An enumeration using it as a base reads every window from it and reaches the same product. A union of two bases that both hold every window writes the same file as the memo alone, with each window written once, from the first base.
    #[test]
    fn a_memo_file_reads_back_as_the_memo_that_wrote_it() {
        let index = fixtures::mini();
        let (product, memo) = enumerate_keeping(&index, &[], Vec::new());
        let dir = scratch("memo-round-trip");
        let path = dir.join("memo-default.tsv");
        write_memo(&index, &path, &head("default"), &memo, &[]).expect("the file writes");
        assert_eq!(
            read_memo_head(&path).expect("the head reads"),
            head("default")
        );
        let back = read_memo(&index, &path, &head("default"), |_| true).expect("the file reads");
        assert_eq!(back.len(), memo.len());
        for (key, entry) in &memo.entries {
            let again = back.entries[key];
            assert_eq!(back.settled(again), memo.settled(*entry));
            assert_eq!(
                back.notes[again.notes.index()],
                memo.notes[entry.notes.index()]
            );
            assert_eq!(back.delta(again), memo.delta(*entry));
            assert_eq!(back.reads(again), memo.reads(*entry));
            assert_eq!(
                (again.prospect(), again.joint_floor(), again.decided_stage()),
                (entry.prospect(), entry.joint_floor(), entry.decided_stage())
            );
        }
        let back = Arc::new(back);
        let base = MemoBase {
            memo: Arc::clone(&back),
            excluded: Exclusion::none(),
        };
        let (seeded, own) = enumerate_keeping(&index, &[], vec![base]);
        assert_eq!(
            emit_transitions(&index, &product),
            emit_transitions(&index, &seeded)
        );
        assert!(own.is_empty(), "every window was answered out of the file");
        let twice = dir.join("memo-default-twice.tsv");
        let both = [
            MemoBase {
                memo: Arc::clone(&back),
                excluded: Exclusion::none(),
            },
            MemoBase {
                memo: Arc::clone(&back),
                excluded: Exclusion::none(),
            },
        ];
        write_memo(
            &index,
            &twice,
            &head("default"),
            &MemoSnapshot::default(),
            &both,
        )
        .expect("the union writes");
        assert_eq!(
            std::fs::read(&twice).expect("the union file"),
            std::fs::read(&path).expect("the first file"),
            "a window two bases hold is written once, out of the first"
        );
    }

    /// Seeding across builds: the edited spec enumerated over the previous spec's memo, with an exclusion naming the edited rune, reaches the edited spec's from-scratch product. The two specs' products differ, so the edit changes the output.
    #[test]
    fn an_edited_spec_seeded_from_the_previous_memo_reaches_its_from_scratch_product() {
        let before = fixtures::mini();
        let after = fixtures::index_of(&mini_dump_without_the_tea_refusal());
        let (product_before, memo_before) = enumerate_keeping(&before, &[], Vec::new());
        let path = scratch("memo-edited").join("memo-default.tsv");
        write_memo(&before, &path, &head("default"), &memo_before, &[]).expect("the file writes");
        let (product_after, _) = enumerate_keeping(&after, &[], Vec::new());
        assert_ne!(
            emit_transitions(&before, &product_before),
            emit_transitions(&after, &product_after),
            "striking the refusal moves the product"
        );
        let previous = read_memo(&after, &path, &head("default"), |_| true)
            .expect("reads over the edited spec");
        let tea = fixtures::sym(&after, "qsTea");
        let tea_ordinal = after.rune_ordinal(tea).expect("qsTea is modeled");
        let (seeded, own) = enumerate_keeping(
            &after,
            &[],
            vec![MemoBase {
                memo: Arc::new(previous),
                excluded: Exclusion::of(&after, [tea]),
            }],
        );
        assert_eq!(
            emit_transitions(&after, &product_after),
            emit_transitions(&after, &seeded)
        );
        assert!(
            !own.is_empty(),
            "the windows that read qsTea were traced afresh"
        );
        assert!(
            own.entries.iter().all(|(key, entry)| key
                .runes_named()
                .any(|rune| rune == tea_ordinal)
                || own.reads(*entry).contains(&Read::Rune(tea))),
            "and nothing else was"
        );
    }

    /// Dropping keys that name edited runes while reading keeps fewer entries without changing the product, the base hits, or the union bytes written for the next build. Lookup still checks each remaining entry's reads.
    #[test]
    fn filtering_edited_keys_preserves_products_hits_and_memo_bytes() {
        let before = fixtures::mini();
        let after = fixtures::index_of(&mini_dump_without_the_tea_refusal());
        let edited = Exclusion::of(&after, [fixtures::sym(&after, "qsTea")]);
        let root = scratch("memo-filter-edited");
        for token in ["default", "ss03"] {
            let features = |index: &SpecIndex| {
                if token == "default" {
                    Vec::new()
                } else {
                    vec![fixtures::sym(index, token)]
                }
            };
            let (_, previous) = enumerate_keeping(&before, &features(&before), Vec::new());
            let path = root.join(format!("previous-{token}.tsv"));
            write_memo(&before, &path, &head(token), &previous, &[]).expect("previous writes");
            let unfiltered = read_memo(&after, &path, &head(token), |_| true)
                .expect("the whole previous memo reads");
            let filtered = read_memo(&after, &path, &head(token), |key| !edited.names(key))
                .expect("the filtered previous memo reads");
            assert!(
                !filtered.is_empty(),
                "unaffected windows remain for {token}"
            );
            assert!(
                filtered.len() < unfiltered.len(),
                "edited windows are omitted for {token}"
            );
            assert!(filtered.entries.keys().all(|key| !edited.names(key)));
            let mut expected = None;
            for (arm, previous) in [("unfiltered", unfiltered), ("filtered", filtered)] {
                let bases = vec![MemoBase {
                    memo: Arc::new(previous),
                    excluded: edited.clone(),
                }];
                let mut census = Vec::new();
                let enumeration = enumerate_for_tables(
                    &after,
                    &features(&after),
                    EnumerationModes::default(),
                    Some(&mut census),
                    Seed {
                        bases: bases.clone(),
                        keep_memo: true,
                    },
                    None,
                )
                .expect("the edited spec closes over either base");
                let hits = census
                    .iter()
                    .find(|line| line.contains(" memo_base_hits count="))
                    .expect("the census reports hits")
                    .clone();
                let own = enumeration.memo.expect("the fresh memo is kept");
                let output = root.join(format!("{arm}-{token}.tsv"));
                write_memo(&after, &output, &head(token), &own, &bases).expect("the union writes");
                let actual = (
                    enumeration.product,
                    hits,
                    own.len(),
                    std::fs::read(output).expect("the union reads"),
                );
                if let Some(expected) = &expected {
                    assert_eq!(&actual, expected, "filtering changes no answer for {token}");
                } else {
                    expected = Some(actual);
                }
            }
        }
    }

    /// A window held by two sources is written from the first source that admits it, and `own` is the first source. With the previous spec's memo and the edited spec's, the file equals the edited memo's file in either carry order as long as the previous memo excludes the edited rune, since every window it holds beyond the edited memo names or reads that rune. Carried first with no exclusion, the previous memo supplies every window it holds, so the windows that name or read the rune come from it, and at least one of them differs from the edited memo's. `own` beside one base writes the same file as two bases in that order, so the shape every seeded build writes (a fresh `own` beside the previous build's memo) is covered too.
    #[test]
    fn a_window_two_bases_hold_is_written_from_the_first_source_that_admits_it() {
        let before = fixtures::mini();
        let after = fixtures::index_of(&mini_dump_without_the_tea_refusal());
        let (_, memo_before) = enumerate_keeping(&before, &[], Vec::new());
        let dir = scratch("memo-attribution");
        let path_before = dir.join("memo-before.tsv");
        write_memo(&before, &path_before, &head("default"), &memo_before, &[])
            .expect("the file writes");
        let previous = Arc::new(
            read_memo(&after, &path_before, &head("default"), |_| true)
                .expect("reads over the edited spec"),
        );
        let (_, memo_after) = enumerate_keeping(&after, &[], Vec::new());
        let memo_after = Arc::new(memo_after);
        let tea = fixtures::sym(&after, "qsTea");
        let tea_ordinal = after.rune_ordinal(tea).expect("qsTea is modeled");
        let base = |memo: &Arc<MemoSnapshot>, excluded: Exclusion| MemoBase {
            memo: Arc::clone(memo),
            excluded,
        };
        let alone = dir.join("memo-alone.tsv");
        write_memo(&after, &alone, &head("default"), &memo_after, &[]).expect("writes");
        let alone_bytes = std::fs::read(&alone).expect("the file");
        let union = |name: &str, carried: Vec<MemoBase>| {
            let path = dir.join(name);
            write_memo(
                &after,
                &path,
                &head("default"),
                &MemoSnapshot::default(),
                &carried,
            )
            .expect("the union writes");
            path
        };
        let edited_first = union(
            "memo-edited-first.tsv",
            vec![
                base(&memo_after, Exclusion::none()),
                base(&previous, Exclusion::of(&after, [tea])),
            ],
        );
        assert_eq!(
            std::fs::read(&edited_first).expect("the file"),
            alone_bytes,
            "the previous memo behind the edited rune adds nothing"
        );
        let previous_behind_tea = union(
            "memo-previous-behind-tea.tsv",
            vec![
                base(&previous, Exclusion::of(&after, [tea])),
                base(&memo_after, Exclusion::none()),
            ],
        );
        assert_eq!(
            std::fs::read(&previous_behind_tea).expect("the file"),
            alone_bytes,
            "a window the first source refuses is taken from the second"
        );
        let previous_first = union(
            "memo-previous-first.tsv",
            vec![
                base(&previous, Exclusion::none()),
                base(&memo_after, Exclusion::none()),
            ],
        );
        let previous_first_bytes = std::fs::read(&previous_first).expect("the file");
        assert_ne!(previous_first_bytes, alone_bytes);
        let own_first = dir.join("memo-own-first.tsv");
        write_memo(
            &after,
            &own_first,
            &head("default"),
            &previous,
            &[base(&memo_after, Exclusion::none())],
        )
        .expect("writes");
        assert_eq!(
            std::fs::read(&own_first).expect("the file"),
            previous_first_bytes,
            "own is the first source, and a base beside it is written as a second base is"
        );
        let own_behind_a_base = dir.join("memo-own-behind-a-base.tsv");
        write_memo(
            &after,
            &own_behind_a_base,
            &head("default"),
            &memo_after,
            &[base(&previous, Exclusion::of(&after, [tea]))],
        )
        .expect("writes");
        assert_eq!(
            std::fs::read(&own_behind_a_base).expect("the file"),
            alone_bytes,
            "a seeded build's shape: own beside the previous memo behind the edited rune"
        );
        let back =
            read_memo(&after, &previous_first, &head("default"), |_| true).expect("reads back");
        let union_keys: HashSet<&TraceKey> = previous
            .entries
            .keys()
            .chain(memo_after.entries.keys())
            .collect();
        assert_eq!(back.len(), union_keys.len());
        let record = |memo: &MemoSnapshot, entry: TraceEntry| {
            (
                memo.settled(entry).clone(),
                memo.notes[entry.notes.index()].clone(),
                memo.delta(entry).to_vec(),
                memo.reads(entry).to_vec(),
                entry.prospect(),
                entry.joint_floor(),
                entry.decided_stage(),
            )
        };
        let mut moved = 0;
        for (key, again) in &back.entries {
            let held_before = previous.entries.get(key).copied();
            let held_after = memo_after.entries.get(key).copied();
            let names_tea = key.runes_named().any(|rune| rune == tea_ordinal)
                || held_before
                    .is_some_and(|entry| previous.reads(entry).contains(&Read::Rune(tea)));
            if let Some(entry) = held_before.filter(|_| names_tea) {
                assert_eq!(
                    record(&back, *again),
                    record(&previous, entry),
                    "a window naming the edited rune is written from the earlier source"
                );
                if held_after
                    .is_none_or(|entry| record(&memo_after, entry) != record(&back, *again))
                {
                    moved += 1;
                }
            } else {
                let entry = held_after.expect("every other window is the edited memo's");
                assert_eq!(record(&back, *again), record(&memo_after, entry));
            }
        }
        assert!(moved > 0, "striking the refusal moves some qsTea window");
    }

    /// Reading another configuration's file fails with an error naming both configurations, and a window naming a stance the spec no longer has is dropped while the other windows read.
    #[test]
    fn a_memo_for_another_configuration_is_refused_and_a_stale_window_is_dropped() {
        let index = fixtures::mini();
        let (_, memo) = enumerate_keeping(&index, &[], Vec::new());
        let path = scratch("memo-refusals").join("memo-default.tsv");
        write_memo(&index, &path, &head("default"), &memo, &[]).expect("the file writes");
        let complaint = read_memo(&index, &path, &head("ss03"), |_| true).expect_err("refused");
        assert!(complaint.contains("configuration default"), "{complaint}");
        let text = std::fs::read_to_string(&path).expect("the file is text");
        let stale = text.replace("\nY\talt\n", "\nY\tgone\n");
        assert_ne!(
            stale, text,
            "the memo names the alt stance in its symbol table"
        );
        std::fs::write(&path, stale).expect("rewritten");
        let back = read_memo(&index, &path, &head("default"), |_| true).expect("still reads");
        assert!(back.len() < memo.len());
        assert!(!back.is_empty());
    }

    /// A file with more settled records or notes lists than a trace entry's two-byte seat can index fails at the first line past the range, naming that line, instead of wrapping the seat. A file with exactly the range reads.
    #[test]
    fn a_memo_seating_more_than_a_trace_seat_names_is_refused() {
        let index = fixtures::mini();
        let path = scratch("memo-seat-range").join("memo-default.tsv");
        let head_line = format!(
            "# {MEMO_FORMAT}\tdefault\t{}\t{}\n",
            head("default").world,
            head("default").stamp
        );
        for (line, capacity, complaint) in [
            (
                "S\t0\t0\t-\t-\t-\t-\t0\n",
                TraceSettledSeat::CAPACITY,
                "line 65537: a memo seats fewer than 65,536 settled records",
            ),
            (
                "N\t\n",
                TraceNotesSeat::CAPACITY,
                "line 65537: a memo seats fewer than 65,536 notes lists",
            ),
        ] {
            let mut text = head_line.clone();
            for _ in 0..capacity {
                text.push_str(line);
            }
            std::fs::write(&path, &text).expect("written");
            read_memo(&index, &path, &head("default"), |_| true)
                .expect("a table at the range reads");
            text.push_str(line);
            std::fs::write(&path, &text).expect("rewritten");
            let refusal =
                read_memo(&index, &path, &head("default"), |_| true).expect_err("refused");
            assert!(refusal.contains(complaint), "{refusal}");
        }
    }

    /// A window whose prospect is not zero or one fails at its line, because the entry stores the prospect in one bit.
    #[test]
    fn a_window_with_a_prospect_past_one_is_refused() {
        let index = fixtures::mini();
        let (_, memo) = enumerate_keeping(&index, &[], Vec::new());
        let path = scratch("memo-prospect-range").join("memo-default.tsv");
        write_memo(&index, &path, &head("default"), &memo, &[]).expect("the file writes");
        let text = std::fs::read_to_string(&path).expect("the file is text");
        let first_window = text
            .lines()
            .position(|line| line.starts_with("E\t"))
            .expect("the memo holds a window");
        for bad in ["2", "-1"] {
            let edited: Vec<String> = text
                .lines()
                .enumerate()
                .map(|(number, line)| {
                    if number != first_window {
                        return line.to_owned();
                    }
                    let mut fields: Vec<&str> = line.split('\t').collect();
                    assert!(matches!(fields[15], "0" | "1"), "the field is the prospect");
                    fields[15] = bad;
                    fields.join("\t")
                })
                .collect();
            std::fs::write(&path, edited.join("\n") + "\n").expect("rewritten");
            let refusal =
                read_memo(&index, &path, &head("default"), |_| true).expect_err("refused");
            assert!(
                refusal.contains(&format!(
                    "line {}: a prospect is a seam count, zero or one",
                    first_window + 1
                )),
                "{refusal}"
            );
        }
    }

    /// The same range at the writer: a union with more distinct settled records or notes lists than a trace entry's two-byte seat can index fails at the write, naming the file, instead of producing a file the next build would reject. A union at exactly the range writes and reads back whole. Each source here is within the range on its own, so only the union exceeds it.
    #[test]
    fn a_union_seating_more_than_a_trace_seat_names_is_refused_at_the_write() {
        let index = fixtures::mini();
        let (_, memo) = enumerate_keeping(&index, &[], Vec::new());
        let (key, entry) = memo
            .entries
            .iter()
            .next()
            .map(|(key, entry)| (*key, *entry))
            .expect("the memo holds a window");
        let record = memo.settled(entry).clone();
        let list = memo.notes[entry.notes.index()].clone();
        let delta = memo.delta(entry).to_vec().into_boxed_slice();
        let reads = memo.reads(entry).to_vec().into_boxed_slice();
        let path = scratch("memo-union-range").join("memo-default.tsv");
        for (distinct_records, complaint) in [
            (true, "a memo seats fewer than 65,536 settled records"),
            (false, "a memo seats fewer than 65,536 notes lists"),
        ] {
            let build = |extensions: std::ops::RangeInclusive<i16>| {
                let mut built = MemoSnapshot::default();
                let mut records = Vec::new();
                built.deltas.push(delta.clone());
                built.reads.push(reads.clone());
                if distinct_records {
                    built.notes.push(list.clone());
                } else {
                    built.settled.push(record.clone());
                }
                for (seat, extension) in extensions.enumerate() {
                    if distinct_records {
                        built.settled.push(Settled {
                            extension: i64::from(extension),
                            ..record.clone()
                        });
                    } else {
                        built.notes.push(vec![extension.to_string()]);
                    }
                    let mut window = key;
                    window.left_extension = extension;
                    let (settled_seat, notes_seat) = if distinct_records {
                        (seat, 0)
                    } else {
                        (0, seat)
                    };
                    records.push((
                        window,
                        TraceEntry::new(
                            TraceSettledSeat::at(settled_seat),
                            TraceNotesSeat::at(notes_seat),
                            DeltaSeat::at(0),
                            ReadsSeat::at(0),
                            0,
                            false,
                            DecidedStage::Order,
                        ),
                    ));
                }
                built.entries = SnapshotEntries::from_records(records).expect("the windows fit");
                built
            };
            let own = build(i16::MIN..=-1);
            let carried = |last: i16| MemoBase {
                memo: Arc::new(build(0..=last)),
                excluded: Exclusion::none(),
            };
            write_memo(
                &index,
                &path,
                &head("default"),
                &own,
                &[carried(i16::MAX - 1)],
            )
            .expect("a union at the range writes");
            let back = read_memo(&index, &path, &head("default"), |_| true)
                .expect("a file at the range reads");
            assert_eq!(back.len(), TraceSettledSeat::CAPACITY);
            let refusal = write_memo(&index, &path, &head("default"), &own, &[carried(i16::MAX)])
                .expect_err("a union past the range is refused");
            assert!(refusal.contains(complaint), "{refusal}");
        }
    }

    /// A settled record naming a cell no left of this spec keys drops the windows seated on it while the rest read. Here the record's stance seat points at a rune's name, which the spec interns but which has no stance ordinal.
    #[test]
    fn a_window_seated_on_a_record_no_left_keys_is_dropped() {
        let index = fixtures::mini();
        let (_, memo) = enumerate_keeping(&index, &[], Vec::new());
        let path = scratch("memo-stale-record").join("memo-default.tsv");
        write_memo(&index, &path, &head("default"), &memo, &[]).expect("the file writes");
        let text = std::fs::read_to_string(&path).expect("the file is text");
        let seat_of = |name: &str| {
            text.lines()
                .filter_map(|line| line.strip_prefix("Y\t"))
                .position(|symbol| symbol == name)
                .expect("the memo names it")
                .to_string()
        };
        let (half, pea) = (seat_of("half"), seat_of("qsPea"));
        let mut moved = 0;
        let stale: Vec<String> = text
            .lines()
            .map(|line| {
                let mut fields: Vec<&str> = line.split('\t').collect();
                if fields[0] == "S" && fields[2] == half {
                    fields[2] = &pea;
                    moved += 1;
                }
                fields.join("\t")
            })
            .collect();
        assert!(moved > 0, "the memo seats a record in the half stance");
        std::fs::write(&path, stale.join("\n") + "\n").expect("rewritten");
        let back = read_memo(&index, &path, &head("default"), |_| true).expect("still reads");
        assert!(back.len() < memo.len());
        assert!(!back.is_empty());
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

    /// The exclusion tests the read journal: an entry whose evaluation read an excluded rune or class is rejected even though its key names neither, and one that read neither is admitted.
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
    }
}
