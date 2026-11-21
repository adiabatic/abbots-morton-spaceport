//! The review corpus build's parent, which `rebuild/CORPUS-PARENT-PORT-PLAN.md` moves from `rebuild/review/build.py` into the crate beside the units engine, with `rebuild/review/corpus_exec.py` as its Python half. [`input`] reads the build input that interface writes, [`console`] prints the build's console lines, [`sink`] writes its whole-file outputs as `output_sink` does, [`gzip`] writes and reads its zlib and gzip containers as Python's `zlib` and `gzip` do, [`random`] draws the verification sample as CPython's `random.Random` does, [`rowdelta`] reads and writes the segment format's heads, parts and segments as `row_delta` does, [`audit`] finds the held audit's sections as `audit.audit_layout` does, [`stream`] walks them a window at a time as `audit_stream.lockstep` does, [`signatures`] loads and writes the ink-signature store as `unit_cache.SignatureArrays` does and finds and shapes its misses on threads as the build's key-only read and shaping do, [`columns`] holds the string table, the pools and the indexes the build's columns are built on as `rebuild/review/columns.py` does, and [`table`] orders the unit table's rows by `audit.triage_key` as `audit`'s counting sorts do, merges a window's ink-duplicate units as `audit._merge_members` does, and holds the unit table the build's stream loads as `audit.stream_workload` loads it; [`premerge`] holds the pre-merge snapshot the review facts are derived from, rebased onto the merge, as `facts.PremergeSnapshot` holds it; [`records`] parses, slices and writes the unit store's records as `unit_cache` does; [`store`] holds the unit store's columns as `unit_store.UnitStore` holds them; and [`scan`] reads the prior store and places its records as the plan's scans and walk do.
//!
//! The build's load phase is here ([`load_phase`], [`load_corpus`]), as `build._build_m1` runs it: the ink-signature store loaded, the misses found and shaped, the audit read as a stream into the unit table and the pre-merge snapshot. The Rust build reads an audit only as a stream, so an audit that is not in sorted sections, or whose key-only read or stream breaks, is refused, naming the Python build that reads it ([`not_a_stream`]). [`stage_load`] is the `load` stage of the stage fixtures.
//!
//! The build's plan phase is here too ([`plan_phase`]), as `build._build_m1` runs it after the load: the unit store made over the load's units and input keys, the prior unit store scanned and every unit it can reuse loaded, the records it cannot reuse kept by id, a store that stops reading partway answered by a restart over an emptied store, and the verification sample drawn from the reused units ([`Planned`]). [`stage_plan`] is the `plan` stage of the stage fixtures.
//!
//! [`drive`] runs the units phase after the plan on the worker pool in process, as `build._RecomputeRunner` drives the Rust units engine, with the recomputed fragments in its arena's spill files. [`passes`] runs the whole-corpus passes after it, from the id index through the triage order and its batches, the duplicate groups and the primary units, to the verification and its stale check. [`route`] decides how the write takes each class before it writes: each class's route and prediction, its manifest record, whether the previous sidecars are copied forward, and the runs. [`shards`] writes each class's fragments into its shard parts, directly into base parts or appended to the overflow parts after the parts the previous corpus holds, each part staged until the write commits. [`prior`] reads each fragment back for the write: a cached one from the previous corpus by its address, a recomputed one from the units phase's arena, each checked to be the unit and stamp its address carries. [`emit`] gives each unit as the write receives it: a fragment read back and patched with this build's scaffold and primary units, or a cached fragment's stored bytes with the identity that stands in for it. [`corpus_check`] checks the units the write writes, per unit at `PATCHED` and class by class against their manifest records as they pass, and as a whole against the manifest once each class's facts are folded in shard order. [`sidecars`] projects each run's sidecar lines as the write passes each unit, or copies a byte-copied unit's lines forward from the previous sidecars, and spills them until the manifest is on disk. [`write`] writes the classes' runs on threads through them, folds their results into the store and one checker in shard order, commits the staged parts and copies the fonts in.

pub mod audit;
pub mod columns;
pub mod console;
pub mod corpus_check;
pub mod drive;
pub mod emit;
pub mod gzip;
pub mod input;
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

use audit::{Section, Vocabulary, audit_layout, stage_line};
use columns::{KeyIndex, KeyIndexError, RepeatedKey};
use input::{BuildInput, Member};
use premerge::PremergeSnapshot;
use random::verification_sample;
use records::{ParsedCachedUnit, id_word, prior_record, truthy};
use rowdelta::Head;
use scan::{
    OpenStore, ScanError, ScanNames, WantedFragments, adopt_single_file, locate_prior_fragments,
    manifest_stamp, open_store, rederiving_moved, scan_marked, scan_store, store_miss_note,
    trusted_parts,
};
use signatures::{
    AUDIT_IDENTITY_KEY, FAMILY_KEYS_KEY, HeldWindows, InkShaping, MissError, SignatureArrays,
    StoreNames, dump_prior, find_misses, get, is_str, key_only_count, lines_value,
    signature_miss_note,
};
use sink::Sink;
use store::{PriorFragment, UnitStore};
use stream::{Lockstep, StreamError};
use table::{
    ColumnDigest, InkDigest, MergeStats, UNIT_PREFIX, Unit, UnitTable, Workload, WorkloadError,
    count_value, int, stage_units, stream_workload, workload_unit,
};

use crate::pyfmt::PyValue;
use crate::wire::{Result, WireError};

/// The switch setting that builds what the Rust build refuses (`corpus_exec.ENGINE_VARIABLE`).
pub const PYTHON_BUILD: &str = "AMS_CORPUS_BUILD_ENGINE=python";
/// The label of the load phase's console lines.
pub const LOAD_LABEL: &str = "review.build load";
/// The label of the plan phase's console lines.
pub const PLAN_LABEL: &str = "review.build plan";
/// The load's source, as the load phase's note names it.
pub const STREAM_SOURCE: &str = "read as a stream";

/// What stops the build, by how the Python build stops: a `SystemExit` with this text, or any other failure, which the result line carries as an `error`.
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

/// The refusal of an audit the Rust build does not read, where the Python build reads it through its in-memory source instead: why the audit at `path` is not read as a stream, and the switch setting that builds it.
pub fn not_a_stream(path: &str, why: &str) -> BuildError {
    BuildError::Exit(format!(
        "the Rust build reads an audit only as a stream, and {path} is not one, since {why}; {PYTHON_BUILD} builds it"
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
    /// The key-only read's part of the load phase's note (`build._key_only_note`).
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

/// What the load gives the plan (`build._Loaded` from the stream): the workload, its table merged and in load order; the pre-merge snapshot, rebased onto the merge; each unit's input key in load order; the ink-signature store's columns, hits marked and misses shaped, and the stamps its header records; the merge's stats; what the key-only read did; the misses shaped, late ones among them, and the threads the key-only read's misses were shaped on; and the held audit's parts, those held as gzip members and its segments' rows.
#[derive(Debug)]
pub struct Loaded {
    pub workload: Workload,
    pub premerge: PremergeSnapshot,
    pub input_keys: Vec<u8>,
    pub signatures: SignatureArrays,
    pub stamps: Vec<(PyValue, PyValue)>,
    pub merge_stats: MergeStats,
    pub key_only: KeyOnlyRun,
    pub shaped: usize,
    pub width: usize,
    pub parts: usize,
    pub compressed: i64,
    pub segment_rows: i64,
}

impl Loaded {
    /// The load phase's note (`_Loaded.note`), after the `build=rust` token: the signatures the store answered and those shaped, and on how many threads, what the key-only read did, the source, the held audit's parts, and the prior store's hits, unhit entries and misses.
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

/// The audit's sections in sorted order, as `audit.audit_layout` finds them over `files`, or the refusal of an audit that is not in sorted sections ([`not_a_stream`]); an audit with no header or another header, or a member that does not inflate to what its index records, fails as Python's raises.
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

/// Load the audit for the build (`build._load_corpus`'s stream path), the prior store read from `out_dir` and the audit through `files`, the parts the build input's descriptors hold: the ink-signature store read into columns, unless the build recomputes every unit, with its miss note on `console` when none loads, said for no store and warned otherwise; the audit's sections found ([`sorted_sections`]); the misses found by the key-only read at the vocabulary's `KEY_ONLY_PROCESSES` ranges on the input's threads, unless the store's stamps hold, and shaped on its signature threads; the audit read as a stream ([`stream_workload`]), each digest the store does not hold shaped late; and the pre-merge snapshot taken over the ledger's `ink_identical` classes and rebased onto the merge. The shaping's fonts are dropped as the load returns.
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
        merge_stats: streamed.merge_stats,
        key_only,
        shaped: shaped + late_shaped,
        width: if shaped > 0 { shaped_width } else { 1 },
        parts: files.len(),
        compressed: input.audit.compressed,
        segment_rows: input.audit.segment_rows,
    })
}

/// The build's load phase (`build._build_m1` up to the plan): its `[phase]` line, the load over the audit's held parts and the prior store in the input's output directory ([`load_corpus`]), the refusal of a table with no units, and the `[t]` line that closes it with the load's note.
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

/// The keys of the `load` stage's lines beside its units and its pre-merge column (`units_stage_fixtures.LOAD_*_KEY`).
const SOURCE_KEY: &str = "source";
const KEY_ONLY_KEY: &str = "key-only";
const SIGNATURES_KEY: &str = "signatures";
const STAMPS_KEY: &str = "stamps";
const ENVIRONMENT_KEY: &str = "environment";
const MERGE_KEY: &str = "merge";
const AUDIT_KEY: &str = "audit";
const CONSOLE_KEY: &str = "console";

/// Write the `load` stage's lines over what the load gave, as the Python stage's `load_boundary` lays them out: a `unit:<ordinal>` line for each ordinal in `units`, the unit as the `workload` stage holds it, and the column over every unit; a column over the pre-merge snapshot's rows; the source, the key-only read's facts, the store's counts and new lines, the stamps, the signature stamp's value, the merge's stats, the held audit's facts, and the console lines the load printed.
fn load_lines(
    loaded: &Loaded,
    input: &BuildInput,
    printed: &[u8],
    units: &std::collections::HashSet<usize>,
    out: &mut impl Write,
) -> Result<()> {
    let table = &loaded.workload.table;
    let mut digest = ColumnDigest::new();
    for ordinal in 0..table.n {
        let value = workload_unit(table, &loaded.input_keys, ordinal);
        if units.contains(&ordinal) {
            stage_line(out, &format!("{UNIT_PREFIX}{ordinal}"), &value)?;
        }
        digest.add(&value);
    }
    digest.write(out, "value")?;
    let snapshot = &loaded.premerge;
    let (Some(survivor), Some(removed)) = (&snapshot.survivor, &snapshot.removed) else {
        return Err(WireError(
            "the load stage answers over a rebased snapshot".to_owned(),
        ));
    };
    let mut rows = ColumnDigest::new();
    for at in 0..snapshot.len() {
        rows.add(&PyValue::Tuple(vec![
            int(snapshot.class_id[at]),
            int(snapshot.no_verdict[at]),
            int(snapshot.configs[at]),
            int(snapshot.window_start[at]),
            int(snapshot.window_n[at]),
            int(survivor[at]),
            int(removed[at]),
        ]));
    }
    rows.write(out, "premerge")?;
    stage_line(out, SOURCE_KEY, &PyValue::str(STREAM_SOURCE))?;
    let key_only = match loaded.key_only {
        KeyOnlyRun::Skipped => PyValue::Tuple(vec![PyValue::str("skipped")]),
        KeyOnlyRun::Ran { ranges, .. } => {
            PyValue::Tuple(vec![PyValue::str("ran"), count_value(ranges)])
        }
    };
    stage_line(out, KEY_ONLY_KEY, &key_only)?;
    let signatures = &loaded.signatures;
    stage_line(
        out,
        SIGNATURES_KEY,
        &PyValue::Tuple(vec![
            count_value(signatures.hits()),
            count_value(loaded.shaped),
            count_value(signatures.unhit()),
            count_value(signatures.misses()),
            PyValue::Bool(signatures.unchanged()),
            lines_value(signatures)?,
        ]),
    )?;
    stage_line(out, STAMPS_KEY, &PyValue::Dict(loaded.stamps.clone()))?;
    stage_line(
        out,
        ENVIRONMENT_KEY,
        &PyValue::str(&input.signature_environment.value),
    )?;
    stage_line(out, MERGE_KEY, &loaded.merge_stats.to_py())?;
    stage_line(
        out,
        AUDIT_KEY,
        &PyValue::Tuple(vec![
            count_value(loaded.parts),
            PyValue::Int(loaded.compressed),
            PyValue::Int(loaded.segment_rows),
        ]),
    )?;
    let printed = String::from_utf8_lossy(printed);
    stage_line(
        out,
        CONSOLE_KEY,
        &PyValue::List(printed.lines().map(PyValue::str).collect()),
    )
}

/// The `load` stage: run the build's load ([`load_corpus`]) over the audit its build input names, read through the descriptors the process inherits, and the prior store of the dump's prior corpus, as its `source.json` names it, its console lines kept rather than printed, and answer with the lines `load_lines` writes, a `unit:<ordinal>` line for each one the stage file asks for. A stage file with no lines, from a build whose load did not return, is answered with none.
pub fn stage_load(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let (units, lines) = stage_units(stage_file)?;
    if lines == 0 {
        return Ok(());
    }
    let files = input.held_audit()?;
    let mut printed = Vec::new();
    let loaded = load_corpus(&input, &files, &dump_prior(stage_file)?, &mut printed)
        .map_err(|failure| WireError(format!("the load failed: {failure}")))?;
    load_lines(&loaded, &input, &printed, &units, out)
}

/// What the plan learned of the prior unit store, for the cache write's choice of a head alone, a segment or a new base (`unit_cache.StorePlan`): whether a store existed; whether it read to its end in this format; its head, or for a single file the one the plan put it under; its materialized record count and its base's row bytes; and whether a stamp line moved that says every record is re-derived.
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

    /// The plan as the `plan` stage records it, its head as its document.
    pub fn value(&self) -> PyValue {
        PyValue::Tuple(vec![
            PyValue::Bool(self.existed),
            PyValue::Bool(self.readable),
            self.head.as_ref().map_or(PyValue::None, Head::document),
            count_value(self.lines),
            PyValue::Int(self.rows_bytes),
            PyValue::Bool(self.rederived),
        ])
    }
}

/// What the plan read of the previous corpus's shard parts, for each class's fold as the write opens it (`build.PriorShards`): each part's size as the unit store records it, and the live bytes its records name in it, each live fragment with the comma or bracket before it. The plan keeps it only when the store describes the parts beside it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PriorShards {
    pub sizes: BTreeMap<String, i64>,
    pub live: BTreeMap<String, i64>,
}

impl PriorShards {
    /// The shards as the `plan` stage records them: each map's items in order.
    pub fn value(&self) -> PyValue {
        let items = |map: &BTreeMap<String, i64>| {
            PyValue::Tuple(
                map.iter()
                    .map(|(part, bytes)| {
                        PyValue::Tuple(vec![PyValue::str(part), PyValue::Int(*bytes)])
                    })
                    .collect(),
            )
        };
        PyValue::Tuple(vec![items(&self.sizes), items(&self.live)])
    }
}

/// What the plan gives the units phase: the unit store, every unit taken from the prior store loaded into it, every prior line it names and every prior record kept by id; the store plan; the prior shards; the count of units reused; the ordinals left to recompute, ascending; and the verification sample's ordinals, ascending, with the units materialized for it.
#[derive(Debug)]
pub struct Planned {
    pub store: UnitStore,
    pub store_plan: StorePlan,
    pub prior_shards: Option<PriorShards>,
    pub cached: usize,
    pub recomputed: Vec<u32>,
    pub sampled: Vec<u32>,
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

/// `build._slim_for`: whether this build would write the unit's fragment slim, from the record's machine flags and this build's exemption.
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

/// What the plan scans with: the prior corpus and its names, the input keys' index over the column it reads, whether the scan parses named lines and keeps addresses, the parts an address is trusted in, and the table the loads read.
struct PriorScan<'a> {
    out_dir: &'a Path,
    names: &'a ScanNames,
    named: &'a KeyIndex,
    column: &'a [u8],
    loadable: bool,
    addresses_hold: bool,
    trusted: &'a HashSet<String>,
    table: &'a UnitTable,
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

    /// The plan's reads of the open store `opened` as `_build_m1`'s `try` makes them: every record in the store's materialized order, each named one giving its unit its prior line, each reusable one loaded, each one not loaded kept by id while the addresses hold; then the records found without an address placed by one walk over the prior corpus and parsed again alone, each reusable one loaded at the place the walk found.
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
        for scanned in scan_store(opened, self.named, self.column, self.loadable, self.trusted) {
            let scanned = scanned?;
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
                    continue;
                };
                if self.reusable(ordinal, cached, &found) {
                    self.load(store, ordinal, cached, &found)?;
                    *cached_count += 1;
                    if self.addresses_hold {
                        *read.live.entry(found.part).or_default() += found.length + 1;
                    }
                    continue;
                }
            }
            if self.addresses_hold {
                let record = prior_record(&scanned.line)
                    .map_err(|why| failed(format!("a value the reader does not hold: {why}")))?;
                if let Some(record) = record
                    && let Some((part, _, length)) = &record.address
                {
                    *read.live.entry(part.clone()).or_default() += length + 1;
                    store.add_prior_record(&record).map_err(failed)?;
                }
            }
        }
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

/// The plan phase (`build._build_m1` from the plan's `[phase]` line to its `[t]` line): the unit store made over the table's units and their input keys, refusing two units with one key; the prior unit store under `out_dir` opened, with its notes, and scanned as [`PriorScan::read`] reads it, restarting over an emptied store with its note when it stops reading partway; once it reads to its end, the kept records sealed, a single file put under a head through `sink`, and the store plan and the prior shards it gives; the ordinals left to recompute; and the verification sample drawn from the reused units' ids, seeded with the store's environment stamp, with the units materialized for it.
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
        sampled,
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

/// The keys of the `plan` stage's lines beside its units and their column (`units_stage_fixtures.PLAN_*`).
const PRIOR_RECORDS_COLUMN: &str = "prior-records";
const RECOMPUTED_COLUMN: &str = "recomputed";
const STORE_PLAN_KEY: &str = "store-plan";
const PRIOR_SHARDS_KEY: &str = "prior-shards";
const CACHED_KEY: &str = "cached";
const SAMPLE_KEY: &str = "sample";
const NOTE_KEY: &str = "note";

/// Write the `plan` stage's lines over what the plan gave, as the Python stage's `plan_boundary` lays them out: a `unit:<ordinal>` line for each ordinal in `units`, the store's row as the `store-load` stage reads one, and the column over every row; a column over the prior records the store keeps by id and one over the recomputed ordinals; the store plan, the prior shards, the count reused, the sample with its units, the note, and the console lines the plan printed but its `[t]` line.
fn plan_lines(
    planned: &Planned,
    printed: &[u8],
    units: &HashSet<usize>,
    out: &mut impl Write,
) -> Result<()> {
    let store = &planned.store;
    let mut digest = ColumnDigest::new();
    for ordinal in 0..store.len() {
        let value = store::reading(store, ordinal);
        if units.contains(&ordinal) {
            stage_line(out, &format!("{UNIT_PREFIX}{ordinal}"), &value)?;
        }
        digest.add(&value);
    }
    digest.write(out, "value")?;
    let mut records = ColumnDigest::new();
    for record in store::sealed_records(store) {
        records.add(&record);
    }
    records.write(out, PRIOR_RECORDS_COLUMN)?;
    let mut recomputed = ColumnDigest::new();
    for &ordinal in &planned.recomputed {
        recomputed.add(&int(ordinal));
    }
    recomputed.write(out, RECOMPUTED_COLUMN)?;
    stage_line(out, STORE_PLAN_KEY, &planned.store_plan.value())?;
    stage_line(
        out,
        PRIOR_SHARDS_KEY,
        &planned
            .prior_shards
            .as_ref()
            .map_or(PyValue::None, PriorShards::value),
    )?;
    stage_line(out, CACHED_KEY, &count_value(planned.cached))?;
    stage_line(
        out,
        SAMPLE_KEY,
        &PyValue::Tuple(vec![
            PyValue::Tuple(planned.sampled.iter().copied().map(int).collect()),
            PyValue::Tuple(planned.verify_units.iter().map(Unit::to_py).collect()),
        ]),
    )?;
    stage_line(out, NOTE_KEY, &PyValue::str(planned.note()))?;
    let printed = String::from_utf8_lossy(printed);
    stage_line(
        out,
        CONSOLE_KEY,
        &PyValue::List(
            printed
                .lines()
                .filter(|line| !line.starts_with(console::TIMING))
                .map(PyValue::str)
                .collect(),
        ),
    )
}

/// Make `target` a tree of hard links to the files under `source`, so a build can write into it as into a clone while the files it reads stay where they are: every writer replaces a file by renaming a staged one over it.
fn linked_tree(source: &Path, target: &Path) -> Result<()> {
    let fail = |what: &Path, failure: std::io::Error| {
        WireError(format!("cannot link {}: {failure}", what.display()))
    };
    std::fs::create_dir_all(target).map_err(|failure| fail(target, failure))?;
    for entry in std::fs::read_dir(source).map_err(|failure| fail(source, failure))? {
        let entry = entry.map_err(|failure| fail(source, failure))?;
        let path = entry.path();
        let linked = target.join(entry.file_name());
        if entry
            .file_type()
            .map_err(|failure| fail(&path, failure))?
            .is_dir()
        {
            linked_tree(&path, &linked)?;
        } else {
            std::fs::hard_link(&path, &linked).map_err(|failure| fail(&path, failure))?;
        }
    }
    Ok(())
}

/// The `plan` stage: run the build's load ([`load_corpus`]) over the audit its build input names, read through the descriptors the process inherits, and the prior corpus of the dump, as its `source.json` names it; then the plan phase ([`plan_phase`]) over a tree of links to that prior corpus beside the stage file, which a single file's adoption writes into, its console lines kept rather than printed; and answer with the lines `plan_lines` writes, a `unit:<ordinal>` line for each one the stage file asks for. A stage file with no lines, from a build whose plan did not finish, is answered with none.
pub fn stage_plan(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let (units, lines) = stage_units(stage_file)?;
    if lines == 0 {
        return Ok(());
    }
    let files = input.held_audit()?;
    let prior = dump_prior(stage_file)?;
    let loaded = load_corpus(&input, &files, &prior, &mut Vec::new())
        .map_err(|failure| WireError(format!("the load failed: {failure}")))?;
    let directory = stage_file.parent().unwrap_or_else(|| Path::new("."));
    let scratch = directory.join(format!("plan.scratch-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&scratch);
    let mut printed = Vec::new();
    let planned = linked_tree(&prior, &scratch).and_then(|()| {
        plan_phase(
            &input,
            &scratch,
            &Sink::new(None),
            &loaded.workload.table,
            loaded.input_keys,
            &mut printed,
        )
        .map_err(|failure| WireError(format!("the plan failed: {failure}")))
    });
    let _ = std::fs::remove_dir_all(&scratch);
    plan_lines(&planned?, &printed, &units, out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_audit_out_of_section_order_is_refused_naming_why_and_the_python_build() {
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
                "the Rust build reads an audit only as a stream, and audit.tsv is not one, since the 'ss03' rows from byte {} of part 0 end in a row of 'default'; AMS_CORPUS_BUILD_ENGINE=python builds it",
                header.len()
            ))
        );
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
