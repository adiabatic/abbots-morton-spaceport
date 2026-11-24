//! The review corpus build, which `corpus_exec.run_build` runs over the build input `corpus_exec.write_input` writes. [`input`] reads that input, [`console`] prints the build's console lines, [`sink`] writes its whole-file outputs as `output_sink` does, [`gzip`] writes and reads its zlib and gzip containers as CPython's `zlib` and `gzip` do, [`random`] draws the verification sample as CPython's `random.Random` does, [`rowdelta`] reads and writes the segment format's heads, parts and segments as `row_delta` does, [`audit`] finds the held audit's sections, [`stream`] walks them a window at a time, [`signatures`] loads and writes the ink-signature store and finds and shapes its misses on threads, [`columns`] holds the string table, the pools and the indexes the build's columns are built on, and [`table`] puts the unit table's rows in triage order with counting sorts, merges a window's ink-duplicate units, and holds the unit table the build's stream loads; [`premerge`] holds the pre-merge snapshot the review facts are derived from, rebased onto the merge; [`records`] parses, slices and writes the unit store's records; [`store`] holds the unit store's columns; and [`scan`] reads the prior store and places its records as the plan's scans and walk do.
//!
//! The build's load phase is here ([`load_phase`], [`load_corpus`]): the ink-signature store loaded, the misses found and shaped, the audit read as a stream into the unit table and the pre-merge snapshot. The build reads an audit only as a stream, so an audit that is not in sorted sections, or whose key-only read or stream breaks, is refused ([`not_a_stream`]).
//!
//! The build's plan phase is here too ([`plan_phase`]), after the load: the unit store made over the load's units and input keys, the prior unit store scanned and every unit it can reuse loaded, the records it cannot reuse kept by id, a store that stops reading partway answered by a restart over an emptied store, and the verification sample drawn from the reused units ([`Planned`]).
//!
//! [`build`] runs the whole build phase by phase, each phase opened and closed by its console lines, and [`build_result`] gives the result line `ams-review build` ends with.
//!
//! [`drive`] runs the units phase after the plan on the worker pool in process, with the recomputed fragments in its arena's spill files. [`passes`] runs the whole-corpus passes after it, from the id index through the triage order and its batches, the duplicate groups and the primary units, to the verification and its stale check. [`route`] decides how the write takes each class before it writes: each class's route and prediction, its manifest record, whether the previous sidecars are copied forward, and the runs. [`shards`] writes each class's fragments into its shard parts, directly into base parts or appended to the overflow parts after the parts the previous corpus holds, each part staged until the write commits. [`prior`] reads each fragment back for the write: a cached one from the previous corpus by its address, a recomputed one from the units phase's arena, each checked to be the unit and stamp its address carries. [`emit`] gives each unit as the write receives it: a fragment read back and patched with this build's scaffold and primary units, or a cached fragment's stored bytes with the identity that stands in for it. [`corpus_check`] checks the units the write writes, per unit at `PATCHED` and class by class against their manifest records as they pass, and as a whole against the manifest once each class's facts are folded in shard order. [`sidecars`] projects each run's sidecar lines as the write passes each unit, or copies a byte-copied unit's lines forward from the previous sidecars, and spills them until the manifest is on disk. [`write`] writes the classes' runs on threads through them, folds their results into the store and one checker in shard order, commits the staged parts and copies the fonts in. [`manifest`] writes the manifest after them and prunes the shards it no longer names. [`index`] writes the units index from the runs' spilled lines once the manifest is on disk: a first or unusable base, a fold, a segment of the records that differ from the prior's, or the head alone. Beside it, on a thread of its own, [`sidecars`] writes the app sidecars from the runs' spilled app-index lines and locator blocks, each staged until all three are written. [`facts`] writes the review facts once the manifest is on disk, from the pre-merge snapshot projected onto the units phase's products. [`cache`] writes the unit store after them: a first, unusable, re-derived, share or fold base, a segment of the records whose lines changed, or the head alone.

pub mod audit;
pub mod cache;
pub mod columns;
pub mod console;
pub mod corpus_check;
pub mod drive;
pub mod emit;
pub mod facts;
pub mod gzip;
pub mod index;
pub mod input;
pub mod manifest;
pub mod passes;
pub mod premerge;
pub mod prior;
pub mod random;
pub mod records;
pub mod route;
pub mod rowdelta;
pub mod scan;
pub mod shards;
pub mod sidecars;
pub mod signatures;
pub mod sink;
pub mod store;
pub mod stream;
pub mod table;
pub mod write;

use std::collections::{BTreeMap, HashSet};
use std::fs::File;
use std::io::Write;
use std::path::Path;
use std::time::Instant;

use audit::{Section, Vocabulary, audit_layout};
use columns::{KeyIndex, KeyIndexError, RepeatedKey};
use input::{BuildInput, Member};
use premerge::PremergeSnapshot;
use random::verification_sample;
use records::{ParsedCachedUnit, id_word, prior_record, truthy};
use rowdelta::Head;
use scan::{
    OpenStore, ScanError, ScanNames, ScannedLine, WantedFragments, adopt_single_file,
    locate_prior_fragments, manifest_stamp, open_store, rederiving_moved, scan_marked,
    scan_store_threaded, store_miss_note, trusted_parts,
};
use signatures::{
    AUDIT_IDENTITY_KEY, FAMILY_KEYS_KEY, HeldWindows, InkShaping, MissError, SignatureArrays,
    StoreNames, find_misses, get, is_str, key_only_count, signature_miss_note,
};
use sink::Sink;
use store::{PriorFragment, UnitStore};
use stream::{Lockstep, StreamError};
use table::{InkDigest, Unit, UnitTable, Workload, WorkloadError, stream_workload};

use crate::pyfmt::PyValue;
use crate::wire::WireError;

/// The label of the load phase's console lines.
pub const LOAD_LABEL: &str = "review.build load";
/// The label of the plan phase's console lines.
pub const PLAN_LABEL: &str = "review.build plan";
/// The load's source, as the load phase's note names it.
pub const STREAM_SOURCE: &str = "read as a stream";

/// What stops the build: a refusal, which the result line carries as an `exit` with this text for the launcher to raise as `SystemExit`, or any other failure, which it carries as an `error`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BuildError {
    Exit(String),
    Failed(String),
}

impl std::fmt::Display for BuildError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Exit(message) => write!(formatter, "SystemExit: {message}"),
            Self::Failed(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for BuildError {}

impl From<WireError> for BuildError {
    fn from(failure: WireError) -> Self {
        Self::Failed(failure.0)
    }
}

fn failed(failure: impl std::fmt::Display) -> BuildError {
    BuildError::Failed(failure.to_string())
}

/// The refusal of an audit the build does not read: why the audit at `path` is not read as a stream.
pub fn not_a_stream(path: &str, why: &str) -> BuildError {
    BuildError::Exit(format!(
        "the Rust build reads an audit only as a stream, and {path} is not one, since {why}"
    ))
}

/// What the key-only read did, for the load phase's note: skipped, since the prior store's stamps held, or run over its window ranges on threads, in seconds.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum KeyOnlyRun {
    Skipped,
    Ran {
        seconds: f64,
        ranges: usize,
        threads: usize,
    },
}

impl KeyOnlyRun {
    /// The key-only read's part of the load phase's note.
    pub fn note(&self) -> String {
        match self {
            Self::Skipped => "skipped, the store's stamps held".to_owned(),
            Self::Ran {
                seconds,
                ranges,
                threads,
            } => format!("ran {seconds:.2} s across {ranges} ranges on {threads} threads"),
        }
    }
}

/// What the load gives the plan: the workload, its table merged and in load order; the pre-merge snapshot, rebased onto the merge; each unit's input key in load order; the ink-signature store's columns, hits marked and misses shaped, and the stamps its header records; what the key-only read did; the misses shaped, late ones among them, and the threads the key-only read's misses were shaped on; and the held audit's parts, those held as gzip members and its segments' rows.
#[derive(Debug)]
pub struct Loaded {
    pub workload: Workload,
    pub premerge: PremergeSnapshot,
    pub input_keys: Vec<u8>,
    pub signatures: SignatureArrays,
    pub stamps: Vec<(PyValue, PyValue)>,
    pub key_only: KeyOnlyRun,
    pub shaped: usize,
    pub width: usize,
    pub parts: usize,
    pub compressed: i64,
    pub segment_rows: i64,
}

impl Loaded {
    /// The load phase's note, after the `build=rust` token: the signatures the store answered and those shaped, and on how many threads, what the key-only read did, the source, the held audit's parts, and the prior store's hits, unhit entries and misses.
    pub fn note(&self) -> String {
        let signatures = &self.signatures;
        let how = match (self.shaped, self.width) {
            (0, _) => String::new(),
            (_, 1) => " serially".to_owned(),
            (_, width) => format!(" across {width} threads"),
        };
        format!(
            "build=rust (signatures: {} cached, {} shaped{how}) (key-only read {}; {STREAM_SOURCE}; audit {} parts, {} compressed, {} segment rows; prior store {} hit, {} unhit, {} missed)",
            thousands(signatures.hits()),
            thousands(self.shaped),
            self.key_only.note(),
            self.parts,
            self.compressed,
            thousands(usize::try_from(self.segment_rows).unwrap_or(0)),
            thousands(signatures.hits()),
            thousands(signatures.unhit()),
            thousands(signatures.misses()),
        )
    }
}

/// A count with a comma every three digits, as Python's `f"{count:,}"` writes it.
pub(crate) fn thousands(count: usize) -> String {
    let digits = count.to_string();
    let mut written = String::with_capacity(digits.len() + digits.len() / 3);
    for (at, digit) in digits.chars().enumerate() {
        if at > 0 && (digits.len() - at).is_multiple_of(3) {
            written.push(',');
        }
        written.push(digit);
    }
    written
}

/// The audit's sections in sorted order, found over `files` ([`audit_layout`]), or the refusal of an audit that is not in sorted sections ([`not_a_stream`]); an audit with no header or another header, or a member that does not inflate to what its index records, fails.
pub fn sorted_sections(
    files: &[File],
    path: &str,
    members: &[Option<Vec<Member>>],
    vocabulary: &Vocabulary,
) -> std::result::Result<Vec<Section>, BuildError> {
    let layout = audit_layout(files, path, members, vocabulary).map_err(failed)?;
    layout
        .sections
        .ok_or_else(|| not_a_stream(path, &layout.why))
}

/// What the stream's load raised, by how the build stops on it: a break refuses the audit ([`not_a_stream`]), a classing `ValueError` is an `exit` with its text, and anything else fails.
fn stream_failure(path: &str, failure: WorkloadError) -> BuildError {
    match failure {
        WorkloadError::Stream(StreamError::Break(why)) => not_a_stream(path, &why),
        WorkloadError::Value(message) => BuildError::Exit(message),
        other => failed(other),
    }
}

/// Load the audit for the build, the prior store read from `out_dir` and the audit through `files`, the parts the build input's descriptors hold: the ink-signature store read into columns, unless the build recomputes every unit, with its miss note on `console` when none loads, said for no store and warned otherwise; the audit's sections found ([`sorted_sections`]); the misses found by the key-only read at the vocabulary's `KEY_ONLY_PROCESSES` ranges on the input's threads, unless the store's stamps hold, and shaped on its signature threads; the audit read as a stream ([`stream_workload`]), each digest the store does not hold shaped late; and the pre-merge snapshot taken over the ledger's `ink_identical` classes and rebased onto the merge. The shaping's fonts are dropped as the load returns.
pub fn load_corpus(
    input: &BuildInput,
    files: &[File],
    out_dir: &Path,
    console: &mut impl Write,
) -> std::result::Result<Loaded, BuildError> {
    let names = StoreNames::of(input)?;
    let environment = &input.signature_environment;
    let prior = if input.recompute_all {
        None
    } else {
        SignatureArrays::load(out_dir, &names, &environment.value).map_err(failed)?
    };
    if !input.recompute_all && prior.is_none() {
        let note = signature_miss_note(out_dir, &names, environment)
            .map_err(failed)?
            .unwrap_or_else(|| names.unreadable.clone());
        let line = format!("ink-signature store: {note}");
        if note == names.no_store {
            console::say(console, &line)
        } else {
            console::warn(console, &line)
        }
        .map_err(failed)?;
    }
    let mut signatures = prior.unwrap_or_default();
    let stamps = vec![
        (
            PyValue::str(AUDIT_IDENTITY_KEY),
            PyValue::List(
                input
                    .audit
                    .identity
                    .iter()
                    .copied()
                    .map(PyValue::Int)
                    .collect(),
            ),
        ),
        (
            PyValue::str(FAMILY_KEYS_KEY),
            PyValue::str(&input.family_keys_digest),
        ),
    ];
    let path = input.audit.path.as_str();
    let vocabulary = Vocabulary::of(input)?;
    let sections = sorted_sections(files, path, &input.audit.members, &vocabulary)?;
    let held = HeldWindows {
        files,
        sections: &sections,
        family_of: &input.family_of,
        family_keys: &input.family_keys,
    };
    let width = |value: i64| usize::try_from(value).unwrap_or(1).max(1);
    let threads = width(input.widths.threads);
    let mut found = find_misses(
        held,
        &mut signatures,
        &input.audit.identity,
        &input.family_keys_digest,
        key_only_count(input)?,
        threads,
    )
    .map_err(|failure| match failure {
        MissError::Exit(message) => BuildError::Exit(message),
        other => failed(other),
    })?;
    if let Some(why) = &found.broken {
        return Err(not_a_stream(path, why));
    }
    let key_only = if found.ran {
        KeyOnlyRun::Ran {
            seconds: found.seconds,
            ranges: found.ranges,
            threads: threads.min(found.ranges).max(1),
        }
    } else {
        KeyOnlyRun::Skipped
    };
    let shaping = InkShaping::open(Path::new(&input.units_init))?;
    let (shaped, shaped_width) = shaping.shape_found_misses(
        &mut found,
        &sections,
        &mut signatures,
        width(input.widths.signature_threads),
    )?;
    let mut late = shaping.late(&mut signatures);
    let streamed = {
        let digest: InkDigest<'_> =
            &mut |key: u64, window: &str, config: &str| late.digest(key, window, config);
        Lockstep::new(files, &sections, None, None)
            .map_err(WorkloadError::from)
            .and_then(|mut stream| {
                stream_workload(
                    || stream.next_window(),
                    path,
                    &input.ledger_path,
                    &sections,
                    &input.ledger,
                    &input.family_of,
                    &input.family_keys,
                    &vocabulary.configs,
                    Some(digest),
                )
            })
    };
    let late_shaped = late.shaped;
    drop(late);
    drop(shaping);
    let streamed = streamed.map_err(|failure| stream_failure(path, failure))?;
    let identical = input
        .ledger
        .iter()
        .filter(|entry| entry.ink_identical)
        .map(|entry| entry.id.clone());
    let mut premerge = PremergeSnapshot::from_columns(streamed.premerge, identical);
    premerge
        .rebase(streamed.compaction)
        .map_err(|uneven| BuildError::Failed(format!("ValueError: {uneven}")))?;
    Ok(Loaded {
        workload: streamed.workload,
        premerge,
        input_keys: streamed.input_keys,
        signatures,
        stamps,
        key_only,
        shaped: shaped + late_shaped,
        width: if shaped > 0 { shaped_width } else { 1 },
        parts: files.len(),
        compressed: input.audit.compressed,
        segment_rows: input.audit.segment_rows,
    })
}

/// The build's load phase, up to the plan: its `[phase]` line, the load over the audit's held parts and the prior store in the input's output directory ([`load_corpus`]), the refusal of a table with no units, and the `[t]` line that closes it with the load's note.
pub fn load_phase(
    input: &BuildInput,
    files: &[File],
    console: &mut impl Write,
) -> std::result::Result<Loaded, BuildError> {
    console::phase(console, LOAD_LABEL).map_err(failed)?;
    let started = Instant::now();
    let loaded = load_corpus(input, files, Path::new(&input.out_dir), console)?;
    if loaded.workload.table.n == 0 {
        return Err(BuildError::Exit(format!(
            "{} records no divergent rows, so there is nothing to build a review corpus over",
            input.audit.path
        )));
    }
    console::phase_timing(
        console,
        LOAD_LABEL,
        started.elapsed().as_secs_f64(),
        &loaded.note(),
    )
    .map_err(failed)?;
    Ok(loaded)
}

/// What the plan learned of the prior unit store, for the cache write's choice of a head alone, a segment or a new base: whether a store existed; whether it read to its end in this format; its head, or for a single file the one the plan put it under; its materialized record count and its base's row bytes; and whether a stamp line moved that says every record is re-derived.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct StorePlan {
    pub existed: bool,
    pub readable: bool,
    pub head: Option<Head>,
    pub lines: usize,
    pub rows_bytes: i64,
    pub rederived: bool,
}

impl StorePlan {
    /// The plan of a store that existed and did not read.
    fn existed() -> Self {
        Self {
            existed: true,
            ..Self::default()
        }
    }
}

/// What the plan read of the previous corpus's shard parts, for each class's fold as the write opens it: each part's size as the unit store records it, and the live bytes its records name in it, each live fragment with the comma or bracket before it. The plan keeps it only when the store describes the parts beside it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PriorShards {
    pub sizes: BTreeMap<String, i64>,
    pub live: BTreeMap<String, i64>,
}

/// What the plan gives the units phase: the unit store, every unit taken from the prior store loaded into it, every prior line it names and every prior record kept by id; the store plan; the prior shards; the count of units reused; the ordinals left to recompute, ascending; and the units of the verification sample, materialized in ascending ordinal order.
#[derive(Debug)]
pub struct Planned {
    pub store: UnitStore,
    pub store_plan: StorePlan,
    pub prior_shards: Option<PriorShards>,
    pub cached: usize,
    pub recomputed: Vec<u32>,
    pub verify_units: Vec<Unit>,
}

impl Planned {
    /// The plan phase's note.
    pub fn note(&self) -> String {
        format!(
            "(reused {} of {} units from the cache; kept {} prior records by id)",
            thousands(self.cached),
            thousands(self.store.len()),
            thousands(self.store.prior_record_count()),
        )
    }
}

/// Whether this build would write the unit's fragment slim, from the record's machine flags and this build's exemption.
fn slim_for(no_verdict: bool, cached: &ParsedCachedUnit) -> bool {
    cached.ink_identical || cached.picture_identical || cached.junior_equivalent || no_verdict
}

/// How the plan's read of the prior store stops: on what `unit_cache.StoreUnreadable` stands for, which restarts the plan over an emptied store, or on a failure of the build.
enum ScanStop {
    Unreadable,
    Failed(BuildError),
}

impl From<ScanError> for ScanStop {
    fn from(failure: ScanError) -> Self {
        match failure {
            ScanError::Unreadable(_) => Self::Unreadable,
            other => Self::Failed(failed(other)),
        }
    }
}

impl From<BuildError> for ScanStop {
    fn from(failure: BuildError) -> Self {
        Self::Failed(failure)
    }
}

/// What the plan's scans read of an open prior store: its materialized record count and row bytes, and the live bytes each part's records name in it.
struct PriorRead {
    lines: usize,
    rows_bytes: usize,
    live: BTreeMap<String, i64>,
}

/// What the plan scans with: the prior corpus and its names, the input keys' index over the column it reads, whether the scan parses named lines and keeps addresses, the parts an address is trusted in, the table the loads read, and the threads the scan parses on.
struct PriorScan<'a> {
    out_dir: &'a Path,
    names: &'a ScanNames,
    named: &'a KeyIndex,
    column: &'a [u8],
    loadable: bool,
    addresses_hold: bool,
    trusted: &'a HashSet<String>,
    table: &'a UnitTable,
    threads: usize,
}

impl PriorScan<'_> {
    /// Load one cached unit into the store at the fragment the plan located for it.
    fn load(
        &self,
        store: &mut UnitStore,
        ordinal: u32,
        cached: &ParsedCachedUnit,
        found: &PriorFragment,
    ) -> std::result::Result<(), BuildError> {
        let codepoints: Vec<i64> = self
            .table
            .codepoints(ordinal as usize)
            .into_iter()
            .map(i64::from)
            .collect();
        store
            .load_cached(i64::from(ordinal), cached, &codepoints, Some(found))
            .map_err(failed)?;
        Ok(())
    }

    /// Whether the fragment `found` carries the record's content key and has the shape this build writes the unit in.
    fn reusable(&self, ordinal: u32, cached: &ParsedCachedUnit, found: &PriorFragment) -> bool {
        found.content_key.as_deref() == Some(cached.content_key.as_str())
            && cached.slim == slim_for(self.table.no_verdict(ordinal as usize), cached)
    }

    /// The plan's reads of the open store `opened`: every record in the store's materialized order, read on one thread and parsed on the scan's threads ([`scan_store_threaded`]), each named one giving its unit its prior line, each reusable one loaded, each one not loaded kept by id while the addresses hold; then the records found without an address placed by one walk over the prior corpus and parsed again alone, each reusable one loaded at the place the walk found.
    fn read(
        &self,
        opened: &OpenStore,
        store: &mut UnitStore,
        cached_count: &mut usize,
    ) -> std::result::Result<PriorRead, ScanStop> {
        let mut read = PriorRead {
            lines: 0,
            rows_bytes: 0,
            live: BTreeMap::new(),
        };
        let mut addressless: Vec<(u32, u32)> = Vec::new();
        let mut wanted = WantedFragments::new();
        // The parse threads read each kept record beside the line's parse, for every line the load will not take.
        let kept_record = |scanned: &ScannedLine| {
            let loads = matches!(
                (&scanned.cached, scanned.ordinal),
                (Some(cached), Some(ordinal))
                    if cached.located().is_some_and(|found| self.reusable(ordinal, cached, &found))
            );
            (self.addresses_hold && !loads).then(|| prior_record(&scanned.line))
        };
        scan_store_threaded(
            opened,
            self.named,
            self.column,
            self.loadable,
            self.trusted,
            self.threads,
            kept_record,
            |scanned, kept| -> std::result::Result<(), ScanStop> {
                read.lines += 1;
                read.rows_bytes += scanned.line.len();
                let index = i64::try_from(scanned.index).map_err(failed)?;
                if let Some(ordinal) = scanned.ordinal {
                    store
                        .set_prior_line(ordinal as usize, index)
                        .map_err(failed)?;
                }
                if let (Some(cached), Some(ordinal)) = (&scanned.cached, scanned.ordinal) {
                    let Some(found) = cached.located() else {
                        if let Some(word) = id_word(&cached.prior_id) {
                            addressless.push((u32::try_from(index).map_err(failed)?, ordinal));
                            wanted.add(&cached.prior_class, word);
                        }
                        return Ok(());
                    };
                    if self.reusable(ordinal, cached, &found) {
                        self.load(store, ordinal, cached, &found)?;
                        *cached_count += 1;
                        if self.addresses_hold {
                            *read.live.entry(found.part).or_default() += found.length + 1;
                        }
                        return Ok(());
                    }
                }
                if self.addresses_hold {
                    let record = kept
                        .unwrap_or_else(|| prior_record(&scanned.line))
                        .map_err(|why| {
                            failed(format!("a value the reader does not hold: {why}"))
                        })?;
                    if let Some(record) = record
                        && let Some((part, _, length)) = &record.address
                    {
                        *read.live.entry(part.clone()).or_default() += length + 1;
                        store.add_prior_record(&record).map_err(failed)?;
                    }
                }
                Ok(())
            },
        )?;
        if !wanted.is_empty() {
            let located = locate_prior_fragments(self.out_dir, &wanted, self.names)?;
            let Some(again) = open_store(self.out_dir, self.names)? else {
                return Err(ScanStop::Unreadable);
            };
            let marked: Vec<u32> = addressless.iter().map(|&(line, _)| line).collect();
            let mut places = addressless.iter();
            for item in scan_marked(&again, &marked, self.trusted) {
                let (index, cached) = item?;
                let Some(&(line, ordinal)) = places.next() else {
                    return Err(ScanStop::Failed(failed(
                        "StopIteration: the second scan read past the marked lines",
                    )));
                };
                if index != line as usize {
                    return Err(ScanStop::Unreadable);
                }
                if let Some(found) = located.find(&cached.prior_class, &cached.prior_id)
                    && self.reusable(ordinal, &cached, &found)
                {
                    self.load(store, ordinal, &cached, &found)?;
                    *cached_count += 1;
                }
            }
        }
        Ok(read)
    }
}

/// The parts the store's stamps record (`dict(stamps.get("parts") or {})`), each name with its size; recorded parts that are neither a dict nor empty are not held.
fn recorded_parts(
    stamps: &[(PyValue, PyValue)],
) -> std::result::Result<&[(PyValue, PyValue)], BuildError> {
    match get(stamps, "parts") {
        Some(PyValue::Dict(entries)) => Ok(entries),
        Some(recorded) if truthy(recorded) => Err(failed(
            "a value the reader does not hold: the store's recorded parts are not a dict",
        )),
        _ => Ok(&[]),
    }
}

/// Whether the parts `recorded` names are the parts `trusted` holds (`trusted == set(recorded_sizes)`).
fn records_trusted(recorded: &[(PyValue, PyValue)], trusted: &HashSet<String>) -> bool {
    recorded.len() == trusted.len()
        && recorded
            .iter()
            .all(|(part, _)| matches!(part, PyValue::Str(part) if trusted.contains(part)))
}

/// The sizes of parts the store records and every one of which holds its recorded size on disk, so each is an int or the bool `==` holds equal to one.
fn trusted_sizes(recorded: &[(PyValue, PyValue)]) -> BTreeMap<String, i64> {
    recorded
        .iter()
        .filter_map(|(part, size)| {
            let PyValue::Str(part) = part else {
                return None;
            };
            let size = match size {
                PyValue::Int(size) => *size,
                PyValue::Bool(flag) => i64::from(*flag),
                _ => return None,
            };
            Some((part.clone(), size))
        })
        .collect()
}

/// The plan phase, from its `[phase]` line to its `[t]` line: the unit store made over the table's units and their input keys, refusing two units with one key; the prior unit store under `out_dir` opened, with its notes, and scanned as [`PriorScan::read`] reads it, restarting over an emptied store with its note when it stops reading partway; once it reads to its end, the kept records sealed, a single file put under a head through `sink`, and the store plan and the prior shards it gives; the ordinals left to recompute; and the verification sample drawn from the reused units' ids, seeded with the store's environment stamp, with the units materialized for it.
pub fn plan_phase(
    input: &BuildInput,
    out_dir: &Path,
    sink: &Sink,
    table: &UnitTable,
    input_keys: Vec<u8>,
    console: &mut impl Write,
) -> std::result::Result<Planned, BuildError> {
    console::phase(console, PLAN_LABEL).map_err(failed)?;
    let started = Instant::now();
    let names = ScanNames::of(input)?;
    let environment = &input.environment;
    let mut store =
        UnitStore::new(table.n, table.strings.clone(), Some(input_keys)).map_err(failed)?;
    let named = match store.input_key_index() {
        Ok(named) => named,
        Err(KeyIndexError::Repeated(RepeatedKey { first, second })) => {
            return Err(BuildError::Exit(format!(
                "units {} and {} spell one input key {}: every row belongs to one triple and every key line carries its window and names, so a repeat is a sha256 collision",
                table.codepoints_text(first as usize),
                table.codepoints_text(second as usize),
                store.input_key_hex(first as usize),
            )));
        }
        Err(other) => return Err(failed(other)),
    };
    // The scan reads the input keys while the loads write the store's rows, so it reads its own copy of the column.
    let column = store.input_keys().to_vec();
    let mut cached = 0usize;
    let mut store_plan = StorePlan::default();
    let mut prior_shards = None;
    let mut opened = match open_store(out_dir, &names) {
        Ok(opened) => {
            if opened.is_none() && !input.recompute_all {
                console::say(console, &format!("unit cache: {}", names.no_store))
                    .map_err(failed)?;
            }
            opened
        }
        Err(ScanError::Unreadable(_)) => {
            store_plan = StorePlan::existed();
            console::warn(console, &format!("unit cache: {}", names.unreadable)).map_err(failed)?;
            None
        }
        Err(other) => return Err(failed(other)),
    };
    if opened
        .as_ref()
        .is_some_and(|opened| !is_str(opened.stamp("format"), &names.store_format))
    {
        opened = None;
        store_plan = StorePlan::existed();
        let note = store_miss_note(out_dir, &names, environment).map_err(failed)?;
        console::warn(
            console,
            &format!("unit cache: {}", note.as_deref().unwrap_or("None")),
        )
        .map_err(failed)?;
    }
    if let Some(opened) = opened {
        let stamps = &opened.stamps;
        let manifest_holds = is_str(
            opened.stamp("manifest_sha256"),
            &manifest_stamp(out_dir, &names).map_err(failed)?,
        );
        let trusted = if manifest_holds {
            trusted_parts(out_dir, stamps).map_err(failed)?
        } else {
            HashSet::new()
        };
        let recorded = recorded_parts(stamps)?;
        let addresses_hold = manifest_holds && records_trusted(recorded, &trusted);
        let loadable = !input.recompute_all
            && manifest_holds
            && is_str(opened.stamp("environment"), &environment.value);
        if !loadable && !input.recompute_all {
            let note = store_miss_note(out_dir, &names, environment)
                .map_err(failed)?
                .filter(|note| !note.is_empty())
                .unwrap_or_else(|| names.unreadable.clone());
            console::warn(console, &format!("unit cache: {note}")).map_err(failed)?;
        }
        let scan = PriorScan {
            out_dir,
            names: &names,
            named: &named,
            column: &column,
            loadable,
            addresses_hold,
            trusted: &trusted,
            table,
            threads: usize::try_from(input.widths.threads).unwrap_or(1).max(1),
        };
        match scan.read(&opened, &mut store, &mut cached) {
            Err(ScanStop::Failed(failure)) => return Err(failure),
            Err(ScanStop::Unreadable) => {
                store = store.emptied();
                cached = 0;
                store_plan = StorePlan::existed();
                console::warn(console, &format!("unit cache: {}", names.unreadable))
                    .map_err(failed)?;
            }
            Ok(read) => {
                store.seal_prior_records();
                let head = match &opened.head {
                    Some(head) => head.clone(),
                    None => adopt_single_file(
                        out_dir,
                        &names,
                        stamps,
                        i64::try_from(read.lines).map_err(failed)?,
                        i64::try_from(read.rows_bytes).map_err(failed)?,
                        sink,
                    )
                    .map_err(failed)?,
                };
                store_plan = StorePlan {
                    existed: true,
                    readable: true,
                    rows_bytes: head.base.rows_bytes,
                    head: Some(head),
                    lines: read.lines,
                    rederived: rederiving_moved(stamps, environment, &names.rederiving),
                };
                if addresses_hold {
                    prior_shards = Some(PriorShards {
                        sizes: trusted_sizes(recorded),
                        live: read.live,
                    });
                }
            }
        }
    }
    drop((named, column));
    let recomputed: Vec<u32> = (0..store.len())
        .filter(|&ordinal| !store.loaded(ordinal))
        .map(|ordinal| ordinal as u32)
        .collect();
    let mut words: Vec<u64> = (0..store.len())
        .filter(|&ordinal| store.loaded(ordinal))
        .map(|ordinal| store.id_word(ordinal))
        .collect();
    words.sort_unstable();
    let size = match input.constant("rebuild.review.build.VERIFICATION_SAMPLE") {
        Some(PyValue::Int(size)) => usize::try_from(*size).map_err(failed)?,
        _ => {
            return Err(failed(
                "the build input's vocabulary holds no int rebuild.review.build.VERIFICATION_SAMPLE",
            ));
        }
    };
    let drawn: HashSet<u64> = verification_sample(&words, &environment.value, size)
        .into_iter()
        .collect();
    drop(words);
    let sampled: Vec<u32> = (0..store.len())
        .filter(|&ordinal| store.loaded(ordinal) && drawn.contains(&store.id_word(ordinal)))
        .map(|ordinal| ordinal as u32)
        .collect();
    let verify_units = sampled
        .iter()
        .map(|&ordinal| table.unit(ordinal as usize, Some(&store)))
        .collect();
    let planned = Planned {
        store,
        store_plan,
        prior_shards,
        cached,
        recomputed,
        verify_units,
    };
    console::phase_timing(
        console,
        PLAN_LABEL,
        started.elapsed().as_secs_f64(),
        &planned.note(),
    )
    .map_err(failed)?;
    Ok(planned)
}

/// The labels of the console lines of the phases after the plan.
pub const UNITS_LABEL: &str = "review.build units";
pub const MANIFEST_LABEL: &str = "review.build manifest+check";
pub const FACTS_LABEL: &str = "review.build review-facts";
pub const CACHE_LABEL: &str = "review.build cache";

/// The widths the build ran at, which its result line reports: the units pool's, the write's, and the signature shaping's.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Built {
    pub threads: usize,
    pub write_threads: usize,
    pub signature_threads: usize,
}

/// The manifest+check phase's note: the write's width and counters, the plain bytes of each run's sidecar spill files in MB, and what the units index's write did.
fn write_note(
    jobs: usize,
    units: usize,
    counters: &write::Counters,
    runs: &[sidecars::SidecarRun],
    index: &str,
) -> String {
    let megabytes = |bytes: i64| bytes as f64 / 1e6;
    let count = |value: i64| thousands(usize::try_from(value).unwrap_or(0));
    let by_run: Vec<String> = runs
        .iter()
        .map(|run| {
            format!(
                "{:.1}",
                megabytes(run.index.length + run.app.length + run.rows.length)
            )
        })
        .collect();
    format!(
        "jobs={jobs} (byte-copied {} of {} fragments, copied forward {} sidecar rows; sidecar spill files {} MB by run; appended {:.1} MB ({:.1} MB inflated), kept {} by compare, folded {} classes, wrote {} directly, deflated {} fragments, dead {:.1} MB) (index: {index})",
        count(counters.byte_copied),
        thousands(units),
        count(counters.copied_forward),
        by_run.join(", "),
        megabytes(counters.appended),
        megabytes(counters.appended_inflated),
        count(counters.kept),
        count(counters.folded),
        count(counters.direct),
        count(counters.deflated),
        megabytes(counters.dead),
    )
}

/// The whole build, from the load to the cache, over the audit's held parts `files` into the input's output directory, its console lines on `console`: the load and the plan, whose scan of the prior unit store parses on the input's threads; the units phase, which starts the ink-signature store's write on a thread of its own when the store changed ([`signatures::SignatureWrite`]), recomputes the plan's units on the worker pool with their fragments in the arena's spill files under the input's scratch directory, and runs the passes through the stale check, after which the pool and its resources are dropped; the manifest+check phase, which routes and writes the classes' runs, writes the manifest, prunes the shards it does not name, copies the static files and writes the units index and the app sidecars, then refuses the build when the re-settlement, the drafting-time check or the corpus check found an error; the review facts; and the unit store, after which the ink-signature store's write is joined. A build that stops early waits for that write before it returns. Every whole-file write counts itself in the input's counts file, and the units index and the unit store take the pass's fold claim as the input's cycle names it.
pub fn build(
    input: &BuildInput,
    files: &[File],
    console: &mut impl Write,
) -> std::result::Result<Built, BuildError> {
    let out_dir = Path::new(&input.out_dir);
    let scratch = Path::new(&input.scratch);
    let sink = Sink::new(input.cycle.counts.as_ref().map(std::path::PathBuf::from));
    let claim = rowdelta::FoldClaim::new(
        input.cycle.run.clone(),
        std::path::PathBuf::from(&input.cycle.fold_claim),
    );
    let write_names = write::WriteNames::of(input)?;
    let manifest_names = manifest::ManifestNames::of(input)?;
    let index_names = index::IndexNames::of(input)?;
    let app_names = sidecars::AppNames::of(input)?;
    let facts_names = facts::FactsNames::of(input)?;
    let cache_names = cache::CacheNames::of(input)?;
    let store_names = StoreNames::of(input)?;
    let errors_shown = match input.constant("rebuild.review.build.CONTRACT_ERRORS_SHOWN") {
        Some(PyValue::Int(shown)) => usize::try_from(*shown).map_err(failed)?,
        _ => {
            return Err(failed(
                "the build input's vocabulary holds no int rebuild.review.build.CONTRACT_ERRORS_SHOWN",
            ));
        }
    };
    std::fs::create_dir_all(out_dir).map_err(failed)?;
    std::fs::create_dir_all(scratch).map_err(failed)?;

    let loaded = load_phase(input, files, console)?;
    let signature_threads = loaded.width;
    let Loaded {
        workload,
        premerge,
        input_keys,
        signatures,
        stamps,
        ..
    } = loaded;
    let mut table = workload.table;
    let mut planned = plan_phase(input, out_dir, &sink, &table, input_keys, console)?;

    console::phase(console, UNITS_LABEL).map_err(failed)?;
    let started = Instant::now();
    let (jobs, lane) = drive::units_pool_width(&input.widths);
    let arena = std::sync::Arc::new(drive::Arena::new(scratch));
    let mut units = drive::Drive::start(
        input,
        jobs,
        planned.recomputed.len(),
        planned.verify_units.len(),
        std::sync::Arc::clone(&arena),
    )?;
    let signature_note = passes::signature_note(&signatures);
    let signature_write = if signatures.unchanged() {
        drop((signatures, stamps));
        None
    } else {
        let PyValue::Dict(header) = input::json_map_py(&input.signature_environment.header)? else {
            unreachable!("a map converts to a dict");
        };
        Some(
            signatures::SignatureWrite::start(
                out_dir.to_path_buf(),
                store_names,
                header,
                signatures,
                stamps,
            )
            .map_err(failed)?,
        )
    };
    units.phase1(&mut table, &mut planned.store, &planned.recomputed, console)?;
    let triage = passes::triage(
        input,
        &workload.classes_present,
        &mut table,
        &mut planned.store,
    )?;
    let passed = passes::passes(
        &mut table,
        &mut planned.store,
        &triage,
        &units,
        &planned.verify_units,
    )?;
    let contract_errors = std::mem::take(&mut units.contract_errors);
    drop(units);
    console::phase_timing(
        console,
        UNITS_LABEL,
        started.elapsed().as_secs_f64(),
        &passes::units_note(
            jobs,
            lane,
            planned.recomputed.len(),
            passed.verified.len(),
            &arena.note(),
            &signature_note,
        ),
    )
    .map_err(failed)?;

    console::phase(console, MANIFEST_LABEL).map_err(failed)?;
    let started = Instant::now();
    let emitter = emit::Emitter::open(input)?;
    let settings = corpus_check::CheckSettings::of(input).map_err(failed)?;
    let routed = route::route_classes(
        out_dir,
        &table,
        &planned.store,
        &triage.classes,
        &triage.sections,
        &passed.by_class,
        planned.prior_shards.as_ref(),
        planned.cached,
        route::write_fit(&input.widths),
        &write_names.route,
    )?;
    let written = write::write_corpus(
        input,
        out_dir,
        scratch,
        &table,
        &mut planned.store,
        &routed,
        &arena,
        &emitter,
        &settings,
        &write_names,
        &sink,
    )?;
    drop(emitter);
    let shard_ids: Vec<&str> = routed
        .classes
        .iter()
        .map(|entry| entry.id.as_str())
        .collect();
    let manifest = manifest::manifest(
        input,
        &manifest_names,
        &manifest::ManifestParts {
            table: &table,
            store: &planned.store,
            order: &triage.order,
            row_total: workload.row_count,
            batches: triage.batches,
            duplicate_groups: passed.duplicate_groups,
            junctions: passed.junction_counts.items().to_vec(),
            classes: triage
                .classes
                .iter()
                .map(|entry| entry.id.as_str())
                .collect(),
        },
        &shard_ids,
        &written,
    )?;
    drop(shard_ids);
    manifest::write_manifest(out_dir, &manifest, console)?;
    write::copy_static(&sink, out_dir, &input.static_files, &input.fallback_index)
        .map_err(failed)?;
    let index = sidecars::write_sidecars(
        out_dir,
        &written.runs,
        &index_names,
        &app_names,
        &claim,
        &sink,
    )?;
    let write::Written {
        check,
        runs,
        jobs: write_threads,
        counters,
        ..
    } = written;
    let mut errors = Vec::new();
    if let Some(first) = passed.mismatches.first() {
        errors.push(format!(
            "enricher: re-settled cells diverge from the audit in {} units (first: {first})",
            passed.mismatches.len()
        ));
    }
    errors.extend(contract_errors);
    errors.extend(check.finish(&manifest).map_err(failed)?);
    if !errors.is_empty() {
        errors.truncate(errors_shown);
        return Err(BuildError::Exit(format!(
            "contract check failed:\n{}",
            errors.join("\n")
        )));
    }
    drop(arena);
    let note = write_note(write_threads, table.n, &counters, &runs, &index.note());
    drop(runs);
    console::phase_timing(
        console,
        MANIFEST_LABEL,
        started.elapsed().as_secs_f64(),
        &note,
    )
    .map_err(failed)?;

    console::phase(console, FACTS_LABEL).map_err(failed)?;
    let started = Instant::now();
    facts::review_facts(
        out_dir,
        &sink,
        &facts_names,
        &facts::FactsParts {
            manifest: &manifest,
            table: &table,
            store: &planned.store,
            snapshot: &premerge,
            row_count: workload.row_count,
            group_order: &input.group_order,
        },
    )?;
    drop(premerge);
    console::phase_timing(console, FACTS_LABEL, started.elapsed().as_secs_f64(), "")
        .map_err(failed)?;

    console::phase(console, CACHE_LABEL).map_err(failed)?;
    let started = Instant::now();
    let stored = cache::cache_phase(
        input,
        &cache_names,
        &cache::CacheParts {
            corpus: out_dir,
            plan: &planned.store_plan,
            order: &triage.order,
            store: &planned.store,
            table: &table,
            predicted: &routed.predicted,
            manifest: &manifest,
        },
        &claim,
        &sink,
    )?;
    if let Some(write) = signature_write {
        write.join().map_err(failed)?;
    }
    console::phase_timing(
        console,
        CACHE_LABEL,
        started.elapsed().as_secs_f64(),
        &format!("(store: {})", stored.note()),
    )
    .map_err(failed)?;
    Ok(Built {
        threads: jobs,
        write_threads,
        signature_threads,
    })
}

/// Run the build the input at `input_path` describes, over the audit parts whose descriptors the process inherits, its console lines on `console` ([`build`]), and return its result line: `done` with the process's peak footprint and the widths it ran at; `exit` with the text of a refusal, which the launcher raises as `SystemExit`; or `error` with any other failure, a panic among them.
pub fn build_result(input_path: &Path, console: &mut impl Write) -> serde_json::Value {
    result_line(std::panic::catch_unwind(std::panic::AssertUnwindSafe(
        || {
            let input = BuildInput::read(input_path)?;
            let files = input.held_audit()?;
            build(&input, &files, console)
        },
    )))
}

/// The result line for how a build ended ([`build_result`]).
fn result_line(
    outcome: std::thread::Result<std::result::Result<Built, BuildError>>,
) -> serde_json::Value {
    match outcome {
        Ok(Ok(built)) => serde_json::json!({
            "op": "done",
            "peak_footprint": crate::ffi::peak_footprint(),
            "threads": built.threads,
            "write_threads": built.write_threads,
            "signature_threads": built.signature_threads,
        }),
        Ok(Err(BuildError::Exit(message))) => serde_json::json!({"op": "exit", "message": message}),
        Ok(Err(BuildError::Failed(message))) => {
            serde_json::json!({"op": "error", "message": message})
        }
        Err(panic) => serde_json::json!({
            "op": "error",
            "message": format!("the build panicked: {}", crate::pool::panic_message(&*panic)),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_audit_out_of_section_order_is_refused_naming_why() {
        let scratch =
            std::env::temp_dir().join(format!("ams-review-load-order-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&scratch);
        std::fs::create_dir_all(&scratch).expect("scratch");
        let header = "config\tcodepoints\tkinds\tmatched_entry\tbaseline\tnew\n";
        let path = scratch.join("audit.tsv");
        std::fs::write(
            &path,
            format!(
                "{header}ss03\tE650\tcell\tentry\tbase\tnew\ndefault\tE650\tcell\tentry\tbase\tnew\n"
            ),
        )
        .expect("audit");
        let vocabulary = Vocabulary {
            configs: ["default", "ss03"].map(str::to_owned).to_vec(),
            header: header.trim_end().split('\t').map(str::to_owned).collect(),
        };
        let files = [File::open(&path).expect("open")];
        let refusal = sorted_sections(&files, "audit.tsv", &[None], &vocabulary)
            .expect_err("the audit is out of section order");
        let _ = std::fs::remove_dir_all(&scratch);
        assert_eq!(
            refusal,
            BuildError::Exit(format!(
                "the Rust build reads an audit only as a stream, and audit.tsv is not one, since the 'ss03' rows from byte {} of part 0 end in a row of 'default'",
                header.len()
            ))
        );
    }

    // The vocabulary as `corpus_exec.vocabulary()` holds it.
    const PYTHON_VOCABULARY: &str = concat!(
        r#"["rebuild.pipeline.row_delta.AUDIT_SEGMENT_CAP",32],["rebuild.pipeline.row_delta.CONTENT_TIME_STAMP","content_mtime_ns"],["rebuild.pipeline.row_delta.FOLD_OVERRUN",5],["rebuild.pipeline.row_delta.SEGMENT_CAP",16],"#,
        r#"["rebuild.review.app_index.APP_INDEX_FORMAT","ams-review-app-index/1"],["rebuild.review.app_index.APP_INDEX_NAME","app-units.ndjson.gz"],["rebuild.review.app_index.LOCATOR_BLOCK_ROWS",1024],["rebuild.review.app_index.LOCATOR_FORMAT","ams-review-app-locator/2"],["rebuild.review.app_index.LOCATOR_NAME","app-locator.ndjson.gz"],["rebuild.review.app_index.LOCATOR_ROWS_NAME","app-locator-rows.ndjson.gz"],"#,
        r#"["rebuild.review.audit.ACCEPTANCE_CONFIGS",{"tuple":["default","ss03","ss04","ss05","ss03+ss05","ss07","ss10"]}],["rebuild.review.audit.AUDIT_HEADER",{"tuple":["config","codepoints","kinds","matched_entry","baseline","new"]}],["rebuild.review.audit.BATCH_SIZE",300],["rebuild.review.audit.INPUT_KEY_BYTES",32],["rebuild.review.audit.LEDGER_STATUSES",{"tuple":["by-design","approved","per-unit"]}],["rebuild.review.audit.MACHINE_CHECKS",{"tuple":["ink_identical","picture_identical","junior_equivalent"]}],["rebuild.review.audit.RULED_STATUSES",{"tuple":["by-design","approved"]}],["rebuild.review.audit.SECTIONS",{"tuple":["open-questions","spot-check","no-review-needed"]}],["rebuild.review.audit.UNMATCHED_CLASS","UNMATCHED"],"#,
        r#"["rebuild.review.build.CONTRACT_ERRORS_SHOWN",20],["rebuild.review.build.KEY_ONLY_PROCESSES",6],["rebuild.review.build.MANIFEST_FORMAT","ams-review-manifest/2"],["rebuild.review.build.PHASE1_HANDOUT_UNITS",8192],["rebuild.review.build.VERIFICATION_SAMPLE",200],"#,
        r#"["rebuild.review.facts.CLASS_UNIT_COUNT_KEYS",{"tuple":["split-by-space-or-zwnj","identical-unused-join-name","identical-variant-name"]}],["rebuild.review.facts.FACTS_FILENAME","review-facts.json"],["rebuild.review.facts.FACTS_FORMAT","ams-review-facts/8"],["rebuild.review.facts.REFERENCE_WINDOW_CODEPOINTS","E670:E653:E652:E666"],"#,
        r#"["rebuild.review.unit_cache.MANIFEST_MOVED_NOTE","the manifest it describes moved"],["rebuild.review.unit_cache.NO_STORE_NOTE","no store on disk"],["rebuild.review.unit_cache.REDERIVING_LABELS",{"tuple":["spec_structure","capability_features","alphabet"]}],["rebuild.review.unit_cache.SIGNATURE_STORE_FORMAT","ams-review-ink-signatures/2"],["rebuild.review.unit_cache.SIGNATURE_STORE_NAME","ink-signatures.tsv.gz"],["rebuild.review.unit_cache.STORE_FORMAT","ams-review-unit-cache/9"],["rebuild.review.unit_cache.STORE_NAME","unit-cache.ndjson.gz"],["rebuild.review.unit_cache.UNREADABLE_NOTE","the store will not read"],"#,
        r#"["rebuild.review.unit_index.ASSET_COMPONENTS",{"tuple":["static"]}],["rebuild.review.unit_index.INDEX_DEFLATE_THREADS",2],["rebuild.review.unit_index.INDEX_FORMAT","ams-review-unit-index/1"],["rebuild.review.unit_index.INDEX_MEMBER_BYTES",4194304],["rebuild.review.unit_index.INDEX_NAME","units-index.ndjson.gz"],["rebuild.review.unit_index.PART_CLOSING",{"bytes":"0a5d0a"}],["rebuild.review.unit_index.SHARD_PART_BYTES",268435456]"#,
    );

    #[test]
    fn a_build_over_an_audit_out_of_section_order_exits_at_its_load_naming_why_and_writes_nothing()
    {
        let scratch =
            std::env::temp_dir().join(format!("ams-review-build-order-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&scratch);
        std::fs::create_dir_all(&scratch).expect("scratch");
        let header = "config\tcodepoints\tkinds\tmatched_entry\tbaseline\tnew\n";
        let audit = scratch.join("audit.tsv");
        std::fs::write(
            &audit,
            format!(
                "{header}ss03\tE650\tcell\tentry\tbase\tnew\ndefault\tE650\tcell\tentry\tbase\tnew\n"
            ),
        )
        .expect("audit");
        let out = scratch.join("out");
        let quoted =
            |path: &Path| serde_json::to_string(path.to_str().expect("utf-8")).expect("json");
        let text = input::tests::input_text(PYTHON_VOCABULARY)
            .replacen(
                r#""out_dir":"/o","scratch":"/s""#,
                &format!(
                    r#""out_dir":{},"scratch":{}"#,
                    quoted(&out),
                    quoted(&scratch.join("spill"))
                ),
                1,
            )
            .replacen(
                r#""members":[null,[["",10,20],["default",5,9]]]"#,
                r#""members":[null]"#,
                1,
            )
            .replacen(r#""recompute_all":false"#, r#""recompute_all":true"#, 1)
            .replacen(r#""counts":"/c/counts""#, r#""counts":null"#, 1);
        let input = BuildInput::parse(&text).expect("input");
        let files = [File::open(&audit).expect("open")];
        let mut console = Vec::new();
        let result = result_line(Ok(build(&input, &files, &mut console)));
        let written: Vec<_> = std::fs::read_dir(&out)
            .expect("the output directory")
            .map(|entry| entry.expect("entry").file_name())
            .collect();
        let _ = std::fs::remove_dir_all(&scratch);
        assert_eq!(
            result,
            serde_json::json!({
                "op": "exit",
                "message": format!(
                    "the Rust build reads an audit only as a stream, and audit.tsv is not one, since the 'ss03' rows from byte {} of part 0 end in a row of 'default'",
                    header.len()
                ),
            })
        );
        assert_eq!(
            String::from_utf8(console).expect("utf-8"),
            "[phase] review.build load\n"
        );
        assert!(written.is_empty(), "{written:?}");
    }

    #[test]
    fn a_note_counts_as_python_writes_counts() {
        assert_eq!(thousands(0), "0");
        assert_eq!(thousands(999), "999");
        assert_eq!(thousands(1_000), "1,000");
        assert_eq!(thousands(1_234_567), "1,234,567");
        assert_eq!(
            KeyOnlyRun::Ran {
                seconds: 1.234,
                ranges: 6,
                threads: 4
            }
            .note(),
            "ran 1.23 s across 6 ranges on 4 threads"
        );
    }
}
