//! The unit store, as `unit_cache.update_store` writes it once the review facts are on disk: this pass's records in triage order under a header stamped with the store's format, the environment, the manifest's identity and every shard part's size ([`store_header`]). With a prior store that read to its end in this format it writes only what changed, as the prior generation's next segment: the records that keep their places are the longest run whose prior lines ascend in triage order ([`kept`]), a kept record whose line changed is an upsert at its own place, every other record an upsert at a place cut between the kept records around it, and every prior line no kept record holds a tombstone ([`changed_rows`], [`SegmentRows`]). It writes a new base instead when no store read before (`first`, `unusable`), when a re-deriving stamp moved (`rederived`), when the segment would pass half the base's row bytes (`share`) or when the prior's fold is due and the pass's one fold is held or forced (`fold`), and the head alone when no record changed. What a write did and its note are [`StoreWrite`]'s. [`cache_phase`] runs it as `build._build_m1`'s cache phase does, over the unit store's records ([`unchanged`], [`line_of`]).
//!
//! [`stage_cache`] is the `cache` stage of the stage fixtures.

use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};

use super::audit::stage_line;
use super::index::{IndexNames, spilled_lines, write_index_lines};
use super::input::{BuildInput, json_map_py};
use super::manifest::{ManifestNames, held_manifest, write_manifest};
use super::rowdelta::{
    FoldClaim, Folding, Head, MERGE_PLACE, Place, ReadError, Row, TOMBSTONE, UPSERT, WriteError,
    base_name, commit, fold_due, fold_forced, head_path, places_between, read_head,
    remove_unlisted, row_length, write_part, write_segment_from, writes_base,
};
use super::scan::{
    OpenStore, ScanError, ScanNames, class_shards, manifest_stamp, open_store_checked, stage_lines,
};
use super::sink::{STAGING_SUFFIX, Sink};
use super::store::{NO_PRIOR_LINE, UnitStore};
use super::table::UnitTable;
use super::thousands;
use super::write::{Scratch, case_input, counts_of, file_sha256, files_held, hold, through_write};
use super::{BuildError, StorePlan};
use crate::corpus::records::{KEY_PREFIX, record_line};
use crate::pyfmt::{PyValue, json};
use crate::wire::{Result, WireError, error};

/// What the store's write raises, by the Python class it mirrors, with its text; `Unheld` for a value Python reads that the writer does not hold, which fails the Rust build.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CacheError {
    pub class: String,
    pub message: String,
}

impl CacheError {
    fn new(class: &str, message: impl Into<String>) -> Self {
        Self {
            class: class.to_owned(),
            message: message.into(),
        }
    }

    fn unreadable(message: impl Into<String>) -> Self {
        Self::new("StoreUnreadable", message)
    }

    fn index_out_of_range() -> Self {
        Self::new("IndexError", "bytearray index out of range")
    }
}

impl std::fmt::Display for CacheError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.class, self.message)
    }
}

impl std::error::Error for CacheError {}

impl From<ReadError> for CacheError {
    fn from(failure: ReadError) -> Self {
        Self::new(failure.class(), failure.message())
    }
}

impl From<WriteError> for CacheError {
    fn from(failure: WriteError) -> Self {
        Self::new(failure.class(), failure.message())
    }
}

impl From<io::Error> for CacheError {
    fn from(failure: io::Error) -> Self {
        match ReadError::of_io(&failure) {
            ReadError::Io(message) => Self::new("OSError", message),
            other => other.into(),
        }
    }
}

impl From<ScanError> for CacheError {
    fn from(failure: ScanError) -> Self {
        match failure {
            ScanError::Unreadable(why) => Self::unreadable(why),
            ScanError::Failed(why) => Self::new("Exception", why),
            ScanError::Unheld(why) => Self::new("Unheld", why),
        }
    }
}

impl From<CacheError> for BuildError {
    fn from(failure: CacheError) -> Self {
        Self::Failed(failure.to_string())
    }
}

type Raised<T> = std::result::Result<T, CacheError>;

/// The names and rules the store's write reads from the build input's vocabulary: the store's name and format and the manifest's asset components (through [`ScanNames`]), and the segment cap and fold overrun.
#[derive(Clone, Debug)]
pub struct CacheNames {
    pub scan: ScanNames,
    pub folding: Folding,
}

impl CacheNames {
    /// The names as the build input's vocabulary gives them.
    pub fn of(input: &BuildInput) -> Result<Self> {
        Ok(Self {
            scan: ScanNames::of(input)?,
            folding: Folding::of(input)?,
        })
    }

    fn name(&self) -> &str {
        &self.scan.store_name
    }

    /// `unit_cache._BASE_GZIP_NAME`, the name a base's gzip header carries whatever the part's name: the store's own with the staging suffix.
    fn base_gzip_name(&self) -> String {
        format!("{}{STAGING_SUFFIX}", self.name())
    }
}

/// What one write of the store did (`unit_cache.StoreWrite`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome {
    Head,
    Segment,
    Base,
}

/// What one write of the store did (`unit_cache.StoreWrite`): wrote the head alone, a segment, or a new base with its reason (`first`, `unusable`, `rederived`, `fold` or `share`), and the segment's or the base's rows and their bytes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StoreWrite {
    pub outcome: Outcome,
    pub reason: &'static str,
    pub rows: i64,
    pub rows_bytes: i64,
}

impl StoreWrite {
    /// The outcome's name, as `StoreWrite.outcome` holds it.
    pub fn outcome_name(&self) -> &'static str {
        match self.outcome {
            Outcome::Head => "head",
            Outcome::Segment => "segment",
            Outcome::Base => "base",
        }
    }

    /// `StoreWrite.note`: what the build's `cache` line says of the write.
    pub fn note(&self) -> String {
        let count = |rows: i64| thousands(usize::try_from(rows).unwrap_or_default());
        match self.outcome {
            Outcome::Head => "head only".to_owned(),
            Outcome::Segment => format!(
                "a segment of {} rows, {:.1} MB",
                count(self.rows),
                self.rows_bytes as f64 / 1e6
            ),
            Outcome::Base => format!("a new base ({}), {} records", self.reason, count(self.rows)),
        }
    }

    /// The write as the `cache` stage records it: its outcome, a base's reason, and its rows and their bytes.
    pub fn value(&self) -> PyValue {
        PyValue::Tuple(vec![
            PyValue::str(self.outcome_name()),
            PyValue::str(self.reason),
            PyValue::Int(self.rows),
            PyValue::Int(self.rows_bytes),
        ])
    }
}

/// `unit_cache._kept`: for each ordinal, whether its record keeps its place, the longest run of this pass's records, in `order`, whose prior lines ascend (a longest increasing subsequence by patience sorting, `bisect_left` over the tails), so the fewest records move. A pass whose prior lines all ascend keeps every record that has one, which is checked first in one walk.
pub fn kept(order: &[u32], prior_line: &dyn Fn(usize) -> u32) -> Vec<bool> {
    let size = order.iter().max().map_or(0, |&most| most as usize + 1);
    let mut kept = vec![false; size];
    let mut values: Vec<u32> = Vec::new();
    let mut ordinals: Vec<u32> = Vec::new();
    for &ordinal in order {
        let line = prior_line(ordinal as usize);
        if line != NO_PRIOR_LINE {
            values.push(line);
            ordinals.push(ordinal);
        }
    }
    if values.windows(2).all(|pair| pair[0] < pair[1]) {
        for &ordinal in &ordinals {
            kept[ordinal as usize] = true;
        }
        return kept;
    }
    let mut tails: Vec<u32> = Vec::new();
    let mut tail_at: Vec<usize> = Vec::new();
    let mut before: Vec<Option<usize>> = vec![None; values.len()];
    for (index, &value) in values.iter().enumerate() {
        let position = tails.partition_point(|&tail| tail < value);
        if position > 0 {
            before[index] = Some(tail_at[position - 1]);
        }
        if position == tails.len() {
            tails.push(value);
            tail_at.push(index);
        } else {
            tails[position] = value;
            tail_at[position] = index;
        }
    }
    let mut index = tail_at.last().copied();
    while let Some(at) = index {
        kept[ordinals[at] as usize] = true;
        index = before[at];
    }
    kept
}

/// A segment's rows as the write holds them until the share rule decides (`unit_cache._SegmentRows`), as columns rather than a value a row: each row's operation, its place's base row and fraction (the fractions' bytes end to end), and for an upsert the ordinal whose line it writes, for a tombstone its payload's place in one buffer of them.
#[derive(Default)]
pub struct SegmentRows {
    ops: Vec<u8>,
    anchors: Vec<i32>,
    fractions: Vec<u8>,
    fraction_ends: Vec<u32>,
    values: Vec<u32>,
    tombstones: Vec<u8>,
    tombstone_ends: Vec<u32>,
}

/// A count as an `array("I")` holds one, refused as Python's `OverflowError` past it.
fn unsigned(value: usize) -> Raised<u32> {
    u32::try_from(value).map_err(|_| {
        CacheError::new(
            "OverflowError",
            "unsigned int is greater than maximum".to_owned(),
        )
    })
}

impl SegmentRows {
    pub fn len(&self) -> usize {
        self.ops.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ops.is_empty()
    }

    fn upsert(&mut self, place: &Place, ordinal: u32) -> Raised<()> {
        self.add(UPSERT, place, ordinal)
    }

    fn tombstone(&mut self, place: &Place, payload: &[u8]) -> Raised<()> {
        let at = unsigned(self.tombstone_ends.len())?;
        self.add(TOMBSTONE, place, at)?;
        self.tombstones.extend_from_slice(payload);
        self.tombstone_ends.push(unsigned(self.tombstones.len())?);
        Ok(())
    }

    fn add(&mut self, op: u8, place: &Place, value: u32) -> Raised<()> {
        let anchor = i32::try_from(place.0).map_err(|_| {
            CacheError::new(
                "OverflowError",
                "signed integer is greater than maximum".to_owned(),
            )
        })?;
        self.ops.push(op);
        self.anchors.push(anchor);
        self.fractions.extend_from_slice(place.1.as_bytes());
        self.fraction_ends.push(unsigned(self.fractions.len())?);
        self.values.push(value);
        Ok(())
    }

    /// The rows in the order they were added, each upsert's line built by `line_of`.
    pub fn rows<'a>(
        &'a self,
        line_of: &'a dyn Fn(usize) -> Raised<Vec<u8>>,
    ) -> impl Iterator<Item = Raised<Row>> + 'a {
        let mut start = 0usize;
        self.ops.iter().enumerate().map(move |(index, &op)| {
            let end = self.fraction_ends[index] as usize;
            let fraction = String::from_utf8_lossy(&self.fractions[start..end]).into_owned();
            start = end;
            let place = (i64::from(self.anchors[index]), fraction);
            let value = self.values[index] as usize;
            let payload = if op == TOMBSTONE {
                let low = if value > 0 {
                    self.tombstone_ends[value - 1] as usize
                } else {
                    0
                };
                self.tombstones[low..self.tombstone_ends[value] as usize].to_vec()
            } else {
                line_of(value)?
            };
            Ok(Row { op, place, payload })
        })
    }
}

/// Where a walk to a prior line stops: the last place it passed, and the line it stopped at with its place, None at the store's end.
type Gap = (Option<Place>, Option<(Place, Vec<u8>)>);

/// How a walk of the store's lines stops early: on the share rule's limit, or on what the write raises.
enum Stop {
    OverHalf,
    Raised(CacheError),
}

impl From<CacheError> for Stop {
    fn from(failure: CacheError) -> Self {
        Self::Raised(failure)
    }
}

impl From<ReadError> for Stop {
    fn from(failure: ReadError) -> Self {
        Self::Raised(failure.into())
    }
}

impl From<WriteError> for Stop {
    fn from(failure: WriteError) -> Self {
        Self::Raised(failure.into())
    }
}

/// The calls the write makes on this pass's records, by ordinal: its line in the prior store's materialized order or [`NO_PRIOR_LINE`], whether it is predicted to be its prior line as it stands, and its line.
pub struct Records<'a> {
    pub prior_line: &'a dyn Fn(usize) -> u32,
    pub unchanged: &'a dyn Fn(usize) -> bool,
    pub line_of: &'a dyn Fn(usize) -> Raised<Vec<u8>>,
}

/// `unit_cache._changed_rows`: walk this pass's records in `order` beside the prior store's lines in theirs and return the segment's rows in place order. It stops with [`Stop::OverHalf`] once the rows' lines pass half the base's row bytes.
fn changed_rows(
    opened: &OpenStore,
    plan: &StorePlan,
    order: &[u32],
    records: &Records<'_>,
) -> std::result::Result<SegmentRows, Stop> {
    let kept = kept(order, records.prior_line);
    let mut held = vec![false; plan.lines];
    for &ordinal in order {
        if kept[ordinal as usize] {
            let line = (records.prior_line)(ordinal as usize) as usize;
            *held
                .get_mut(line)
                .ok_or_else(CacheError::index_out_of_range)? = true;
        }
    }
    let mut rows = SegmentRows::default();
    let mut written = 0i64;
    let mut count = |place: &Place, size: usize| -> std::result::Result<(), Stop> {
        written += row_length(place, size as i64);
        if writes_base(written, plan.rows_bytes, false) {
            return Err(Stop::OverHalf);
        }
        Ok(())
    };
    let mut prior = opened.lines().enumerate();
    let mut pending: Vec<u32> = Vec::new();
    let mut previous: Option<Place> = None;
    // `gap`: advance the prior lines to line `stop`, tombstoning every one before it that no kept record holds, and return the last place passed and line `stop` itself.
    let mut gap = |stop: Option<usize>,
                   previous: &Option<Place>,
                   rows: &mut SegmentRows,
                   count: &mut dyn FnMut(&Place, usize) -> std::result::Result<(), Stop>|
     -> std::result::Result<Gap, Stop> {
        let mut last = previous.clone();
        for (index, line) in prior.by_ref() {
            let (place, line) = line?;
            if Some(index) == stop {
                return Ok((last, Some((place, line))));
            }
            if !*held.get(index).ok_or_else(CacheError::index_out_of_range)? {
                let cut = (KEY_PREFIX.len() + 64).min(line.len());
                let mut tombstone = line[..cut].to_vec();
                tombstone.extend_from_slice(b"\"}\n");
                rows.tombstone(&place, &tombstone)?;
                count(&place, tombstone.len())?;
                last = Some(place);
            }
        }
        if let Some(stop) = stop {
            return Err(CacheError::unreadable(format!(
                "the prior store ends before its line {stop}"
            ))
            .into());
        }
        Ok((last, None))
    };
    let place_pending = |low: Option<&Place>,
                         high: Option<&Place>,
                         pending: &mut Vec<u32>,
                         rows: &mut SegmentRows,
                         count: &mut dyn FnMut(&Place, usize) -> std::result::Result<(), Stop>|
     -> std::result::Result<(), Stop> {
        let places = places_between(low, high, pending.len() as i64)?;
        for (place, &ordinal) in places.iter().zip(pending.iter()) {
            rows.upsert(place, ordinal)?;
            count(place, (records.line_of)(ordinal as usize)?.len())?;
        }
        pending.clear();
        Ok(())
    };
    for &ordinal in order {
        if !kept[ordinal as usize] {
            pending.push(ordinal);
            continue;
        }
        let stop = (records.prior_line)(ordinal as usize) as usize;
        let (low, found) = gap(Some(stop), &previous, &mut rows, &mut count)?;
        let (place, line) = found.expect("a gap to a line returns it");
        place_pending(
            low.as_ref(),
            Some(&place),
            &mut pending,
            &mut rows,
            &mut count,
        )?;
        if !(records.unchanged)(ordinal as usize) {
            let new = (records.line_of)(ordinal as usize)?;
            if new != line {
                rows.upsert(&place, ordinal)?;
                count(&place, new.len())?;
            }
        }
        previous = Some(place);
    }
    let (low, _found) = gap(None, &previous, &mut rows, &mut count)?;
    place_pending(low.as_ref(), None, &mut pending, &mut rows, &mut count)?;
    Ok(rows)
}

/// `unit_cache.store_header`: the store's header for a write, its format, the stamp's header fields (`unit_cache._stamp_header`), the identity of the manifest under `corpus` or `missing`, and the byte size of every part in `parts`, each once in code-point order, None for one not on disk.
pub fn store_header(
    corpus: &Path,
    stamp: &[(PyValue, PyValue)],
    parts: &[String],
    names: &CacheNames,
) -> Raised<PyValue> {
    let mut sorted: Vec<&String> = parts.iter().collect();
    sorted.sort();
    sorted.dedup();
    let sizes = sorted
        .into_iter()
        .map(|part| {
            let size = fs::metadata(corpus.join(part)).map_or(PyValue::None, |metadata| {
                PyValue::Int(metadata.len() as i64)
            });
            (PyValue::str(part), size)
        })
        .collect();
    let mut header = vec![(
        PyValue::str("format"),
        PyValue::str(&names.scan.store_format),
    )];
    header.extend(stamp.iter().cloned());
    header.push((
        PyValue::str("manifest_sha256"),
        PyValue::Str(manifest_stamp(corpus, &names.scan)?),
    ));
    header.push((PyValue::str("parts"), PyValue::Dict(sizes)));
    Ok(PyValue::Dict(header))
}

/// `unit_cache._readable_head`: the store's head, None when it does not read.
fn readable_head(corpus: &Path, names: &CacheNames) -> Raised<Option<Head>> {
    match read_head(corpus, names.name()) {
        Ok(head) => Ok(head),
        Err(ReadError::Head(_)) => Ok(None),
        Err(failure) => Err(failure.into()),
    }
}

/// Delete the single file a store written before the segment format leaves (`store_path(out_dir).unlink(missing_ok=True)`).
fn unlink_single(corpus: &Path, names: &CacheNames) -> Raised<()> {
    match fs::remove_file(names.scan.store_path(corpus)) {
        Err(failure) if failure.kind() != io::ErrorKind::NotFound => Err(failure.into()),
        _ => Ok(()),
    }
}

/// `unit_cache._write_base`: write `lines` under `header` as the next generation's base, one gzip stream naming the store's staging file ([`CacheNames::base_gzip_name`]), its header line in the default form then each line, commit a head that lists it alone, and delete the single file a store written before the segment format leaves. Return the base's records and their bytes.
fn write_base(
    corpus: &Path,
    prior: Option<&Head>,
    header: &PyValue,
    lines: &mut dyn Iterator<Item = Raised<Vec<u8>>>,
    content: i64,
    names: &CacheNames,
    sink: &Sink,
) -> Raised<(i64, i64)> {
    let generation = prior.map_or(0, |prior| prior.generation + 1);
    let mut header_line =
        json::dumps(header).map_err(|failure| CacheError::new("Unheld", failure.0))?;
    header_line.push('\n');
    let base = write_part(
        sink,
        corpus,
        &base_name(names.name(), generation),
        Some(&names.base_gzip_name()),
        |stream| -> Raised<(i64, i64)> {
            stream.write_all(header_line.as_bytes())?;
            let (mut count, mut total) = (0i64, 0i64);
            for line in lines {
                let line = line?;
                stream.write_all(&line)?;
                count += 1;
                total += line.len() as i64;
            }
            Ok((count, total))
        },
    )?;
    let written = (base.rows, base.rows_bytes);
    let head = Head {
        output: names.name().to_owned(),
        generation,
        content,
        stamps: header.clone(),
        base,
        segments: Vec::new(),
        merge: MERGE_PLACE.to_owned(),
        family_keys: Vec::new(),
    };
    commit(sink, corpus, &head)?;
    unlink_single(corpus, names)?;
    Ok(written)
}

/// `unit_cache.update_store`: write this pass's store under `corpus` after the manifest, its records `line_of(ordinal)` for the ordinals in `order`, this pass's triage order, under a header of `stamp`'s fields and `parts`' sizes ([`store_header`]). The parts no head lists are deleted first. With no store that read before it writes a `first` or `unusable` base, counted as rewritten whole; with a re-deriving stamp moved a `rederived` base; otherwise, over the prior store opened with every part checked against its sha256 and its head the plan's, it walks the records beside the prior's lines ([`changed_rows`]), and writes a `share` base when the segment would pass half the base's row bytes, a `fold` base when the prior's fold is due and `claim` holds the pass's fold for the store or the fold is forced, the head alone when no record changed, and the segment otherwise. A store that no longer opens as the plan read it is an `unusable` base. `sink` counts the writes.
#[allow(clippy::too_many_arguments)]
pub fn update_store(
    corpus: &Path,
    stamp: &[(PyValue, PyValue)],
    plan: &StorePlan,
    order: &[u32],
    records: &Records<'_>,
    parts: &[String],
    names: &CacheNames,
    claim: &FoldClaim,
    sink: &Sink,
) -> Raised<StoreWrite> {
    let name = names.name();
    let header = store_header(corpus, stamp, parts, names)?;
    let prior = if plan.existed && !plan.readable {
        readable_head(corpus, names)?
    } else {
        plan.head.clone()
    };
    remove_unlisted(corpus, name, prior.as_ref())?;
    let mut content = prior.as_ref().map_or(0, |prior| prior.content + 1);
    let whole = |reason: &'static str, content: i64| -> Raised<StoreWrite> {
        if reason == "unusable" {
            sink.note_rewritten(name);
        }
        let mut lines = order
            .iter()
            .map(|&ordinal| (records.line_of)(ordinal as usize));
        let (rows, rows_bytes) = write_base(
            corpus,
            prior.as_ref(),
            &header,
            &mut lines,
            content,
            names,
            sink,
        )?;
        Ok(StoreWrite {
            outcome: Outcome::Base,
            reason,
            rows,
            rows_bytes,
        })
    };
    if !plan.readable {
        return whole(if plan.existed { "unusable" } else { "first" }, content);
    }
    if plan.rederived {
        return whole("rederived", content);
    }
    let opened = match open_store_checked(corpus, &names.scan, true)? {
        Some(opened) if opened.head == plan.head => opened,
        _ => return whole("unusable", content),
    };
    let rows = match changed_rows(&opened, plan, order, records) {
        Ok(rows) => rows,
        Err(Stop::OverHalf) => {
            drop(opened);
            return whole("share", content);
        }
        Err(Stop::Raised(failure)) => return Err(failure),
    };
    drop(opened);
    let Folding { cap, overrun } = names.folding;
    if let Some(head) = &plan.head
        && fold_due(head, cap)
        && (claim.claim(sink, name)? || fold_forced(head, cap, overrun))
    {
        if rows.is_empty() {
            content -= 1;
        }
        return whole("fold", content);
    }
    let base = plan
        .head
        .as_ref()
        .expect("the plan puts a store it read under a head");
    if rows.is_empty() {
        let head = Head {
            output: name.to_owned(),
            generation: base.generation,
            content: base.content,
            stamps: header,
            base: base.base.clone(),
            segments: base.segments.clone(),
            merge: MERGE_PLACE.to_owned(),
            family_keys: Vec::new(),
        };
        commit(sink, corpus, &head)?;
        unlink_single(corpus, names)?;
        return Ok(StoreWrite {
            outcome: Outcome::Head,
            reason: "",
            rows: 0,
            rows_bytes: 0,
        });
    }
    let part = write_segment_from(
        sink,
        corpus,
        name,
        base.generation,
        base.segments.len() as i64 + 1,
        rows.rows(records.line_of),
    )?;
    let written = StoreWrite {
        outcome: Outcome::Segment,
        reason: "",
        rows: part.rows,
        rows_bytes: part.rows_bytes,
    };
    commit(sink, corpus, &base.with_segment(part, header))?;
    unlink_single(corpus, names)?;
    Ok(written)
}

/// The cache phase's `unchanged`: whether the unit's record is its prior line as it stands, a cached unit predicted unchanged before the write (`predicted`) and copied to its own address.
pub fn unchanged(store: &UnitStore, predicted: &[bool], ordinal: usize) -> bool {
    if !predicted[ordinal] || !store.flags(ordinal).cached {
        return false;
    }
    let Some(source) = store.source(ordinal) else {
        return false;
    };
    store
        .written_address(ordinal)
        .is_some_and(|(part, start, length)| {
            part == source.part
                && i128::from(start) == i128::from(source.start)
                && i64::from(length) == source.length
        })
}

/// The cache phase's `line_of`: the unit's store record as its line, from the store's columns and the table's class, duplicate group and ledger flags.
pub fn line_of(store: &UnitStore, table: &UnitTable, ordinal: usize) -> Raised<Vec<u8>> {
    record_line(&store.cached_unit(
        ordinal,
        table.class_id(ordinal),
        table.duplicate_group(ordinal),
        table.exemplar(ordinal),
        table.no_verdict(ordinal),
    ))
    .map_err(|failure| CacheError::new("Unheld", failure.0))
}

/// The shard parts the manifest's classes name, in manifest order (`unit_index.class_shards` over `manifest["classes"]`).
pub fn manifest_parts(manifest: &PyValue) -> Raised<Vec<String>> {
    let unread = || CacheError::new("Exception", "the manifest's classes do not read");
    let PyValue::Dict(entries) = manifest else {
        return Err(unread());
    };
    let Some((_, PyValue::List(classes))) = entries
        .iter()
        .find(|(key, _)| matches!(key, PyValue::Str(key) if key == "classes"))
    else {
        return Err(unread());
    };
    let mut parts = Vec::new();
    for meta in classes {
        parts.extend(class_shards(meta).map_err(|_| unread())?);
    }
    Ok(parts)
}

/// What the cache phase reads of the build: the corpus directory, the plan of the prior store, the triage order, the unit store and table, each unit's prediction from the routing, and the manifest as written.
pub struct CacheParts<'a> {
    pub corpus: &'a Path,
    pub plan: &'a StorePlan,
    pub order: &'a [u32],
    pub store: &'a UnitStore,
    pub table: &'a UnitTable,
    pub predicted: &'a [bool],
    pub manifest: &'a PyValue,
}

/// The cache phase, as `build._build_m1` writes the store once the review facts are on disk: [`update_store`] over the units in triage order, under the environment's stamp, each record's prior line the plan filled, [`unchanged`] and [`line_of`] over the unit store, and the manifest's shard parts.
pub fn cache_phase(
    input: &BuildInput,
    names: &CacheNames,
    parts: &CacheParts<'_>,
    claim: &FoldClaim,
    sink: &Sink,
) -> std::result::Result<StoreWrite, BuildError> {
    let PyValue::Dict(stamp) =
        json_map_py(&input.environment.header).map_err(|why| BuildError::Failed(why.0))?
    else {
        unreachable!("a map converts to a dict");
    };
    let shard_parts = manifest_parts(parts.manifest)?;
    let store = parts.store;
    let records = Records {
        prior_line: &|ordinal| store.prior_line(ordinal),
        unchanged: &|ordinal| unchanged(store, parts.predicted, ordinal),
        line_of: &|ordinal| line_of(store, parts.table, ordinal),
    };
    Ok(update_store(
        parts.corpus,
        &stamp,
        parts.plan,
        parts.order,
        &records,
        &shard_parts,
        names,
        claim,
        sink,
    )?)
}

// --- the stage ----------------------------------------------------------------------------------

/// The keys of the `cache` stage's lines (`units_stage_fixtures.CACHE_*`).
const HEAD_KEY: &str = "head";
const FILES_KEY: &str = "files";
const OUTCOME_KEY: &str = "write";
const NOTE_KEY: &str = "note";
const GENERATED: &str = "generated:";

/// Each regular file directly under `corpus` whose name starts with the store's, by name in code-point order, with its size and sha256.
fn store_files(corpus: &Path, name: &str) -> Result<PyValue> {
    let fail =
        |failure: io::Error| WireError(format!("cannot list {}: {failure}", corpus.display()));
    let mut found: Vec<String> = Vec::new();
    for entry in fs::read_dir(corpus).map_err(fail)? {
        let entry_name = entry
            .map_err(fail)?
            .file_name()
            .to_string_lossy()
            .into_owned();
        if entry_name.starts_with(name) {
            found.push(entry_name);
        }
    }
    found.sort();
    let mut held = Vec::new();
    for entry_name in found {
        let path = corpus.join(&entry_name);
        if path.is_file() {
            held.push(PyValue::Tuple(vec![
                PyValue::Str(entry_name),
                PyValue::Int(fs::metadata(&path).map_err(fail)?.len() as i64),
                PyValue::Str(file_sha256(&path).map_err(fail)?),
            ]));
        }
    }
    Ok(PyValue::List(held))
}

fn ints(value: &PyValue, key: &str, what: &str) -> Result<Vec<i64>> {
    match value {
        PyValue::List(items) | PyValue::Tuple(items) => items
            .iter()
            .map(|item| match item {
                PyValue::Int(value) => Ok(*value),
                other => error(format!("{key}: {what} holds {}, not an int", other.kind())),
            })
            .collect(),
        other => error(format!("{key}: {what} is {}, not a list", other.kind())),
    }
}

fn flags(value: &PyValue, key: &str, what: &str) -> Result<Vec<bool>> {
    match value {
        PyValue::List(items) | PyValue::Tuple(items) => items
            .iter()
            .map(|item| match item {
                PyValue::Bool(value) => Ok(*value),
                other => error(format!("{key}: {what} holds {}, not a bool", other.kind())),
            })
            .collect(),
        other => error(format!("{key}: {what} is {}, not a list", other.kind())),
    }
}

fn texts(value: &PyValue, key: &str, what: &str) -> Result<Vec<String>> {
    match value {
        PyValue::List(items) | PyValue::Tuple(items) => items
            .iter()
            .map(|item| match item {
                PyValue::Str(text) => Ok(text.clone()),
                other => error(format!("{key}: {what} holds {}, not a str", other.kind())),
            })
            .collect(),
        other => error(format!("{key}: {what} is {}, not a list", other.kind())),
    }
}

/// A generated case's plan: whether a store existed, whether it read, its head's document or None, its lines, its base's row bytes and whether a re-deriving stamp moved.
fn case_plan(value: &PyValue, key: &str, names: &CacheNames) -> Result<StorePlan> {
    let PyValue::Tuple(items) = value else {
        return error(format!("{key}: the plan is not a tuple"));
    };
    let [
        PyValue::Bool(existed),
        PyValue::Bool(readable),
        head,
        PyValue::Int(lines),
        PyValue::Int(rows_bytes),
        PyValue::Bool(rederived),
    ] = items.as_slice()
    else {
        return error(format!("{key}: the plan does not read"));
    };
    let head = match head {
        PyValue::None => None,
        document => Some(
            Head::from_document(document, names.name())
                .map_err(|why| WireError(format!("{key}: the plan's head: {why}")))?,
        ),
    };
    Ok(StorePlan {
        existed: *existed,
        readable: *readable,
        head,
        lines: usize::try_from(*lines)
            .map_err(|_| WireError(format!("{key}: the plan's lines")))?,
        rows_bytes: *rows_bytes,
        rederived: *rederived,
    })
}

/// Answer one generated line of the `cache` stage: [`update_store`] over a corpus holding the case's files, with the case's plan, stamp, order, prior lines, predictions, lines and parts, under a fold claim this process holds for the case's output or none, valued what the write did and its note, or the class and text of what it raised, the files the corpus holds then and the counts lines it appended.
fn answer_generated(
    names: &CacheNames,
    stage_file: &Path,
    key: &str,
    line_input: &PyValue,
) -> Result<PyValue> {
    if !key.starts_with(GENERATED) {
        return error(format!("{key}: not a line the cache stage answers"));
    }
    let scratch = Scratch::new(stage_file, "cache-case")?;
    let corpus = scratch.0.join("out");
    fs::create_dir_all(&corpus).map_err(|why| WireError(format!("{key}: {why}")))?;
    let (held, rest) = case_input(key, line_input)?;
    hold(&corpus, held, key)?;
    let [
        plan,
        PyValue::Dict(stamp),
        order,
        prior_lines,
        unchanged_flags,
        PyValue::List(lines),
        parts,
        holder,
    ] = rest
    else {
        return error(format!(
            "{key}: the input does not hold the plan, the stamp, the order, the prior lines, the predictions, the lines, the parts and the holder"
        ));
    };
    let plan = case_plan(plan, key, names)?;
    let order = ints(order, key, "the order")?
        .into_iter()
        .map(|ordinal| u32::try_from(ordinal).map_err(|_| WireError(format!("{key}: an ordinal"))))
        .collect::<Result<Vec<_>>>()?;
    let prior_lines = ints(prior_lines, key, "the prior lines")?
        .into_iter()
        .map(|line| u32::try_from(line).map_err(|_| WireError(format!("{key}: a prior line"))))
        .collect::<Result<Vec<_>>>()?;
    let unchanged_flags = flags(unchanged_flags, key, "the predictions")?;
    let lines = lines
        .iter()
        .map(|line| match line {
            PyValue::Bytes(line) => Ok(line.clone()),
            other => error(format!("{key}: a line is {}, not bytes", other.kind())),
        })
        .collect::<Result<Vec<_>>>()?;
    let parts = texts(parts, key, "the parts")?;
    let counts = scratch.0.join("counts");
    fs::write(&counts, b"").map_err(|why| WireError(format!("{key}: {why}")))?;
    let sink = Sink::new(Some(counts.clone()));
    let claim = FoldClaim::new(None, scratch.0.join("cycle").join("fold-claim.json"));
    match holder {
        PyValue::None => {}
        PyValue::Str(holder) => {
            claim
                .claim(&sink, holder)
                .map_err(|why| WireError(format!("{key}: {why}")))?;
        }
        other => return error(format!("{key}: the holder is {}", other.kind())),
    }
    let records = Records {
        prior_line: &|ordinal| prior_lines[ordinal],
        unchanged: &|ordinal| unchanged_flags[ordinal],
        line_of: &|ordinal| Ok(lines[ordinal].clone()),
    };
    let outcome = match update_store(
        &corpus, stamp, &plan, &order, &records, &parts, names, &claim, &sink,
    ) {
        Ok(written) => {
            let PyValue::Tuple(mut items) = written.value() else {
                unreachable!("a write's value is a tuple");
            };
            items.push(PyValue::Str(written.note()));
            PyValue::Tuple(items)
        }
        Err(failure) if failure.class == "Unheld" => {
            return error(format!("{key}: {}", failure.message));
        }
        Err(failure) => PyValue::Dict(vec![
            (PyValue::str("raised"), PyValue::Str(failure.class)),
            (PyValue::str("message"), PyValue::Str(failure.message)),
        ]),
    };
    Ok(PyValue::Tuple(vec![
        outcome,
        files_held(&corpus)?,
        counts_of(&counts, key)?,
    ]))
}

/// The `cache` stage: when the stage file holds lines from the build's run, run the build through the write's commit ([`through_write`]), write the manifest over what it holds ([`write_manifest`]) and the units index from the runs' spilled lines ([`write_index_lines`]) under the pass's fold claim the build input names, which the index claims before the store, then the store ([`cache_phase`]) under the same claim, and answer with the head it committed, the files whose names start with the store's, what it did and its note; then answer each generated line ([`answer_generated`]).
pub fn stage_cache(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let names = CacheNames::of(&input)?;
    let index_names = IndexNames::of(&input)?;
    let manifest_names = ManifestNames::of(&input)?;
    let lines = stage_lines(stage_file)?;
    if lines.ran {
        through_write(&input, stage_file, "cache", |held| {
            let failure =
                |failure: BuildError| WireError(format!("the store write failed: {failure}"));
            let manifest = held_manifest(&input, &manifest_names, &held).map_err(failure)?;
            let corpus = &held.run.corpus;
            write_manifest(corpus, &manifest, &mut io::sink()).map_err(failure)?;
            let claim = FoldClaim::new(
                input.cycle.run.clone(),
                PathBuf::from(&input.cycle.fold_claim),
            );
            let sink = Sink::new(None);
            write_index_lines(
                corpus,
                &mut spilled_lines(&held.written.runs),
                &index_names,
                &claim,
                &sink,
            )
            .map_err(|why| WireError(format!("the index write failed: {why}")))?;
            let parts = CacheParts {
                corpus,
                plan: &held.run.planned.store_plan,
                order: &held.triage.order,
                store: &held.run.planned.store,
                table: &held.run.table,
                predicted: &held.routed.predicted,
                manifest: &manifest,
            };
            let written = cache_phase(&input, &names, &parts, &claim, &sink).map_err(failure)?;
            let head = fs::read(head_path(corpus, names.name()))
                .map_err(|why| WireError(format!("cannot read the store's head: {why}")))?;
            stage_line(out, HEAD_KEY, &PyValue::Bytes(head))?;
            stage_line(out, FILES_KEY, &store_files(corpus, names.name())?)?;
            stage_line(out, OUTCOME_KEY, &written.value())?;
            stage_line(out, NOTE_KEY, &PyValue::Str(written.note()))
        })?;
    }
    for (key, key_input) in &lines.generated {
        let value = answer_generated(&names, stage_file, key, key_input)?;
        stage_line(out, key, &value)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn records_in_prior_order_all_keep_their_places_and_a_moved_one_does_not() {
        let lines = [0u32, 1, 2, NO_PRIOR_LINE, 3];
        let ascending = kept(&[0, 1, 2, 3, 4], &|ordinal| lines[ordinal]);
        assert_eq!(ascending, [true, true, true, false, true]);
        let moved = [7u32, 0, 2, 3, 4, 5, 6, 8, 9, 1];
        let order: Vec<u32> = (0..10).collect();
        let kept = kept(&order, &|ordinal| moved[ordinal]);
        assert_eq!(
            kept,
            [false, true, true, true, true, true, true, true, true, false]
        );
        assert!(super::kept(&[], &|_| 0).is_empty());
    }

    #[test]
    fn the_note_says_what_the_write_did() {
        let write = |outcome, reason, rows, rows_bytes| StoreWrite {
            outcome,
            reason,
            rows,
            rows_bytes,
        };
        assert_eq!(write(Outcome::Head, "", 0, 0).note(), "head only");
        assert_eq!(
            write(Outcome::Segment, "", 1_234, 2_250_000).note(),
            "a segment of 1,234 rows, 2.2 MB"
        );
        assert_eq!(
            write(Outcome::Base, "share", 12_345, 9).note(),
            "a new base (share), 12,345 records"
        );
    }
}
