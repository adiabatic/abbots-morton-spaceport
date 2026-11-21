//! The build's units phase in process, as `build._RecomputeRunner` drives the Rust units engine: the units the plan left to recompute handed to the worker [`Pool`] in configuration order a handout at a time, at most one handout a thread in flight, each answer's projections loaded into the unit store as it arrives and its drafting-time errors kept up to `CONTRACT_ERRORS_SHOWN` ([`Drive::phase1`]); and the verification sample recomputed in contiguous shares, one a thread ([`Drive::verify`]). The pool runs over one set of [`Resources`] opened from the units init as the phase starts ([`Drive::start`]); dropping the [`Drive`] after the verification closes its job queue, and the threads drop the resources as they end.
//!
//! The recomputed units' deflated fragments go to the [`Arena`], `_FragmentArena` on disk: each thread appends its handout's fragments to a spill file in the build's scratch directory, unlinked as it is made, so the fragments hold no memory and a killed build leaves no file. The unit store records each fragment's span as the unit's source, under the part name of its spill file ([`ArenaSpan::part`]), and the write reads it back from there ([`Arena::read`]).
//!
//! [`stage_units`] is the `units` stage of the stage fixtures, and [`through_units`] runs the build through phase 1 for it and for the stages after it.

use std::collections::{HashMap, HashSet};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::FileExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{Receiver, channel};
use std::sync::{Arc, Mutex};

use sha2::{Digest as _, Sha256};

use super::audit::stage_line;
use super::input::{BuildInput, Widths};
use super::signatures::{SignatureArrays, dump_prior};
use super::store::{LoadAddress, PrimaryRows, Recomputed, UnitStore};
use super::table::{
    ColumnDigest, UNIT_PREFIX, Unit, UnitStoreView, UnitTable, stage_units as stage_unit_lines,
};
use super::{
    BuildError, CONSOLE_KEY, Planned, console, failed, linked_tree, load_corpus, plan_phase,
};
use crate::enrich::{Highlight, Pair};
use crate::pool::{Pool, Report, shares};
use crate::pyfmt::PyValue;
use crate::unit::{CONTRACT_ERRORS_SHOWN_NAME, Projection, Resources, VerifyKey, Worker};
use crate::wire::{Injection, Result, UnitRecord, WireError};

/// The part name a recomputed unit's source carries, `build.ARENA_PART`, which names no file; a spill file's part adds its index after a dot ([`ArenaSpan::part`]).
pub const ARENA_PART: &str = "arena";
/// The unit of the units phase's `[progress]` lines, `build.PHASE1_UNITS`.
pub const PHASE1_UNITS: &str = "units enriched";
/// The vocabulary name of `build.PHASE1_HANDOUT_UNITS`, the most units one handout carries.
pub const PHASE1_HANDOUT_UNITS_NAME: &str = "rebuild.review.build.PHASE1_HANDOUT_UNITS";

/// Each arena's serial in this process, so two arenas over one directory never name one spill file.
static ARENAS: AtomicU64 = AtomicU64::new(0);

/// Where one recomputed unit's deflated fragment lies in the [`Arena`]: its spill file's index, and its byte start and length there.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ArenaSpan {
    pub file: usize,
    pub start: u64,
    pub length: u64,
}

impl ArenaSpan {
    /// The part name the unit store records the span under: `arena.<file>`.
    pub fn part(&self) -> String {
        format!("{ARENA_PART}.{}", self.file)
    }
}

/// One spill file while a thread appends to it: its index, the file, and how many bytes it holds.
struct Spill {
    index: usize,
    file: Arc<File>,
    length: u64,
}

/// `_FragmentArena` on disk: the recomputed units' deflated fragments, appended to spill files in `directory` that are unlinked as they are made. A thread appending a handout's fragments takes a spill file no other thread is writing, or makes one, so the arena holds at most one file a thread. The counts are the note's ([`Arena::note`]).
pub struct Arena {
    directory: PathBuf,
    serial: u64,
    free: Mutex<Vec<Spill>>,
    files: Mutex<Vec<Arc<File>>>,
    count: AtomicU64,
    packed: AtomicU64,
    raw: AtomicU64,
}

impl Arena {
    /// An empty arena whose spill files go in `directory`.
    pub fn new(directory: &Path) -> Self {
        Self {
            directory: directory.to_path_buf(),
            serial: ARENAS.fetch_add(1, Ordering::Relaxed),
            free: Mutex::new(Vec::new()),
            files: Mutex::new(Vec::new()),
            count: AtomicU64::new(0),
            packed: AtomicU64::new(0),
            raw: AtomicU64::new(0),
        }
    }

    /// The spill file's index a part name names, or None for a part that is not the arena's.
    pub fn file_of(part: &str) -> Option<usize> {
        let index = part.strip_prefix(ARENA_PART)?.strip_prefix('.')?;
        if index.is_empty() || !index.bytes().all(|byte| byte.is_ascii_digit()) {
            return None;
        }
        index.parse().ok()
    }

    /// A spill file no thread is writing: a free one, or one made now and unlinked at once.
    fn take(&self) -> std::result::Result<Spill, String> {
        if let Some(spill) = self.free.lock().map_err(|_| poisoned())?.pop() {
            return Ok(spill);
        }
        let mut files = self.files.lock().map_err(|_| poisoned())?;
        let index = files.len();
        let path = self.directory.join(format!(
            ".ams-review-arena-{}-{}-{index}",
            std::process::id(),
            self.serial
        ));
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|failure| {
                format!(
                    "cannot make the arena's spill file {}: {failure}",
                    path.display()
                )
            })?;
        std::fs::remove_file(&path).map_err(|failure| {
            format!(
                "cannot unlink the arena's spill file {}: {failure}",
                path.display()
            )
        })?;
        let file = Arc::new(file);
        files.push(Arc::clone(&file));
        Ok(Spill {
            index,
            file,
            length: 0,
        })
    }

    /// Append each deflated fragment, with the length of its shard bytes, to one spill file, and return each one's span (`_FragmentArena.add`).
    pub fn append<'a>(
        &self,
        fragments: impl IntoIterator<Item = (&'a [u8], usize)>,
    ) -> std::result::Result<Vec<ArenaSpan>, String> {
        let mut spill = self.take()?;
        let mut spans = Vec::new();
        let written = (|| {
            for (packed, raw_length) in fragments {
                spill
                    .file
                    .write_all_at(packed, spill.length)
                    .map_err(|failure| format!("cannot write the arena's spill file: {failure}"))?;
                spans.push(ArenaSpan {
                    file: spill.index,
                    start: spill.length,
                    length: packed.len() as u64,
                });
                spill.length += packed.len() as u64;
                self.count.fetch_add(1, Ordering::Relaxed);
                self.packed
                    .fetch_add(packed.len() as u64, Ordering::Relaxed);
                self.raw.fetch_add(raw_length as u64, Ordering::Relaxed);
            }
            Ok(())
        })();
        self.free.lock().map_err(|_| poisoned())?.push(spill);
        written.map(|()| spans)
    }

    /// The deflated bytes at `span`.
    pub fn read(&self, span: ArenaSpan) -> std::result::Result<Vec<u8>, String> {
        let file = self
            .files
            .lock()
            .map_err(|_| poisoned())?
            .get(span.file)
            .cloned()
            .ok_or_else(|| format!("the arena holds no spill file {}", span.file))?;
        let length = usize::try_from(span.length).map_err(|failure| failure.to_string())?;
        let mut bytes = vec![0; length];
        file.read_exact_at(&mut bytes, span.start)
            .map_err(|failure| {
                format!(
                    "cannot read the arena's bytes {}+{} from {}: {failure}",
                    span.start,
                    span.length,
                    span.part()
                )
            })?;
        Ok(bytes)
    }

    /// How many fragments the arena holds.
    pub fn count(&self) -> u64 {
        self.count.load(Ordering::Relaxed)
    }

    /// The deflated bytes it holds, `len(_FragmentArena)`.
    pub fn bytes(&self) -> u64 {
        self.packed.load(Ordering::Relaxed)
    }

    /// The fragments' shard bytes together.
    pub fn raw(&self) -> u64 {
        self.raw.load(Ordering::Relaxed)
    }

    /// The arena's part of the units phase's note (`_FragmentArena.note`): its size and the ratio of its fragments' shard bytes to it.
    pub fn note(&self) -> String {
        let bytes = self.bytes();
        if bytes == 0 {
            return "arena empty".to_owned();
        }
        format!(
            "arena {:.2} GB, deflated {:.2} to 1",
            bytes as f64 / 1e9,
            self.raw() as f64 / bytes as f64
        )
    }
}

fn poisoned() -> String {
    "a thread panicked while holding the arena".to_owned()
}

/// The units pool's width and, under a gated cycle, the gate lane's state that chose it (`build._units_pool_width`): the gates-idle width when the cycle passes one with its marker and the marker exists now, and the build's own width otherwise.
pub fn units_pool_width(widths: &Widths) -> (usize, &'static str) {
    let width = |value: i64| usize::try_from(value).unwrap_or(0).max(1);
    match (widths.gates_idle_threads, &widths.gates_idle_marker) {
        (Some(idle), Some(marker)) if Path::new(marker).exists() => (width(idle), "gate lane idle"),
        (Some(_), Some(_)) => (width(widths.threads), "gate lane busy"),
        _ => (width(widths.threads), ""),
    }
}

/// How many units one handout carries (`build._handout_width`): `ceiling`, or fewer when the recomputed units would not otherwise reach every thread twice.
pub fn handout_width(recomputed: usize, threads: usize, ceiling: usize) -> usize {
    ceiling.min(recomputed.div_ceil(2 * threads.max(1))).max(1)
}

/// The ordinals stably sorted by the configuration each unit settles under (`build._configuration_order`).
pub fn configuration_order(ordinals: &[u32], table: &UnitTable) -> Vec<u32> {
    let mut order = ordinals.to_vec();
    order.sort_by_key(|&ordinal| table.config_rank(ordinal as usize));
    order
}

/// A materialized unit as a request frame carries it (`units_exec.unit_record`), its code point values the table's, which its colon-joined hex spells.
pub fn unit_record(unit: &Unit, table: &UnitTable) -> UnitRecord {
    UnitRecord {
        ordinal: unit.ordinal as i64,
        codepoints: unit.codepoints.clone(),
        codepoint_values: table.codepoints(unit.ordinal),
        new: unit.new.clone(),
        class: unit.class_id.clone(),
        configs: unit.configs.clone(),
        kinds: unit.kinds.clone(),
        group: unit.group.clone(),
        exemplar: unit.exemplar,
        unit_id: unit.unit_id.clone(),
        input_key: unit.input_key.clone(),
        render_groups: unit.render_groups.clone(),
        no_verdict: unit.no_verdict,
        config_classes: unit.config_classes.clone(),
        duplicate_group: unit.duplicate_group.clone(),
        cluster: unit.cluster.clone(),
        ledger_class: unit.ledger_class.clone(),
    }
}

fn pair(&(left, right): &Pair) -> (i64, i64) {
    (left as i64, right as i64)
}

/// `build._highlight`'s rect as a `batch` reply's dict carries it.
fn rect(highlight: &Highlight) -> PyValue {
    PyValue::Dict(vec![
        (PyValue::str("x_min"), PyValue::Int(highlight.x_min)),
        (PyValue::str("x_max"), PyValue::Int(highlight.x_max)),
        (
            PyValue::str("advance_total"),
            PyValue::Int(highlight.advance_total),
        ),
    ])
}

/// One recomputed unit's projection as `UnitStore.load_projection` reads a `units_exec.RustProjection`: the reply's record shapes, its junction pairs lists, and its window's code point values the table's, which the record's colon-joined hex spells.
fn recomputed_of(projection: Projection, codepoint_values: Vec<i64>) -> Recomputed {
    let flags = projection.flags;
    let geometry = projection.geometry;
    let spans = |spans: &[(usize, usize)]| spans.iter().map(pair).collect();
    Recomputed {
        ordinal: projection.ordinal,
        unit_id: projection.unit_id,
        input_key: projection.input_key,
        content_key: projection.content_key,
        ink_identical: flags.ink_identical,
        picture_identical: flags.picture_identical,
        junior_equivalent: flags.junior_equivalent,
        ink_deltas: projection.ink_deltas,
        diffs_digest: projection.diffs_digest,
        cluster: projection.cluster,
        change_group: projection.change_group,
        ledger_class: projection.ledger_class,
        pair_codepoints: geometry.pair_codepoints.as_ref().map(pair),
        rows: PrimaryRows {
            codepoint_values,
            ink_identical: flags.ink_identical,
            picture_identical: flags.picture_identical,
            pair: geometry.pair.as_ref().map(pair),
            after_spans: spans(&geometry.after_spans),
            after_cells: geometry.after_cells,
            after_junctions: geometry.after_junctions,
            before_spans: spans(&geometry.before_spans),
            before_glyphs: geometry.before_glyphs,
            before_junctions: geometry.before_junctions,
            junction_pairs: geometry
                .secondary_junctions
                .iter()
                .map(|junction| pair(&junction.pair))
                .collect(),
        },
        junction_rects: geometry
            .secondary_junctions
            .iter()
            .map(|junction| {
                let (left, right) = pair(&junction.pair);
                (
                    PyValue::List(vec![PyValue::Int(left), PyValue::Int(right)]),
                    rect(&junction.highlight_before),
                    rect(&junction.highlight_after),
                )
            })
            .collect(),
        mismatches: projection.mismatches,
    }
}

/// The verification's keys as `_RecomputeRunner.verify` returns them, a dict by the id each recomputation stamped: in the order the shares answered, a repeated id keeping its first place and its last key.
pub fn keyed(keys: impl IntoIterator<Item = VerifyKey>) -> Vec<VerifyKey> {
    let mut at: HashMap<String, usize> = HashMap::new();
    let mut ordered: Vec<VerifyKey> = Vec::new();
    for key in keys {
        match at.get(&key.unit_id) {
            Some(&place) => ordered[place] = key,
            None => {
                at.insert(key.unit_id.clone(), ordered.len());
                ordered.push(key);
            }
        }
    }
    ordered
}

/// One piece of work for the pool: a handout, whose fragments go to the arena, or one share of the verification sample.
enum Job {
    Batch {
        batch: usize,
        units: Vec<UnitRecord>,
        arena: Arc<Arena>,
    },
    Verify {
        share: usize,
        items: Vec<(UnitRecord, Injection)>,
    },
}

/// A thread's answer: a handout's projections, each with its fragment's span in the arena and no fragment bytes, and its drafting-time errors; or one share's keys.
enum Answer {
    Batch {
        projections: Vec<(Projection, ArenaSpan)>,
        errors: Vec<String>,
    },
    Verify {
        share: usize,
        keys: Vec<VerifyKey>,
    },
}

/// One job through `worker`: a handout drafted and its fragments appended to the arena, or a share verified; a failure names the handout.
fn run_job(worker: &mut Worker<'_>, job: Job) -> std::result::Result<Answer, String> {
    match job {
        Job::Batch {
            batch,
            units,
            arena,
        } => {
            let answer = worker
                .run_batch(units)
                .map_err(|failure| format!("batch {batch}: {failure}"))?;
            let spans = arena
                .append(
                    answer
                        .projections
                        .iter()
                        .map(|projection| (projection.packed.as_slice(), projection.raw_length)),
                )
                .map_err(|failure| format!("batch {batch}: {failure}"))?;
            Ok(Answer::Batch {
                projections: answer
                    .projections
                    .into_iter()
                    .map(|mut projection| {
                        projection.packed = Vec::new();
                        projection
                    })
                    .zip(spans)
                    .collect(),
                errors: answer.contract_errors,
            })
        }
        Job::Verify { share, items } => worker
            .verify(items)
            .map(|keys| Answer::Verify { share, keys })
            .map_err(|failure| format!("verify: {failure}")),
    }
}

/// The build input's vocabulary count `name`, an int of at least `least`.
fn vocabulary_count(
    input: &BuildInput,
    name: &str,
    least: i64,
) -> std::result::Result<usize, BuildError> {
    match input.constant(name) {
        Some(PyValue::Int(value)) if *value >= least => usize::try_from(*value).map_err(failed),
        _ => Err(failed(format!(
            "the build input's vocabulary holds no int {name} of at least {least}"
        ))),
    }
}

/// The units phase's pool over the units init's [`Resources`], or none when the phase has nothing to recompute or verify, as `_RecomputeRunner` starts no engine then.
pub struct Drive {
    pool: Option<Pool<Job>>,
    events: Receiver<Report<Answer>>,
    handout: usize,
    errors_shown: usize,
    arena: Arc<Arena>,
    /// The drafting-time errors kept, in arrival order, up to `CONTRACT_ERRORS_SHOWN`.
    pub contract_errors: Vec<String>,
}

impl Drive {
    /// Start the pool for `recomputed` units and a verification sample of `verified` (`_RecomputeRunner.__init__` under the Rust engine): `min(jobs, workload)` threads over the resources of the input's units init, where the workload is the larger of the two, each handout `_handout_width` units wide; no pool when the workload is empty. The recomputed fragments go to `arena`.
    pub fn start(
        input: &BuildInput,
        jobs: usize,
        recomputed: usize,
        verified: usize,
        arena: Arc<Arena>,
    ) -> std::result::Result<Self, BuildError> {
        let ceiling = vocabulary_count(input, PHASE1_HANDOUT_UNITS_NAME, 1)?;
        let errors_shown = vocabulary_count(input, CONTRACT_ERRORS_SHOWN_NAME, 0)?;
        let (events_in, events) = channel();
        let workload = recomputed.max(verified);
        let threads = jobs.max(1).min(workload);
        let pool = if workload == 0 {
            None
        } else {
            let resources = Resources::open(Path::new(&input.units_init))
                .map_err(|failure| failed(format!("the units engine cannot start: {failure}")))?;
            Some(Pool::start(&Arc::new(resources), threads, run_job, &events_in).map_err(failed)?)
        };
        Ok(Self {
            pool,
            events,
            handout: handout_width(recomputed, threads, ceiling),
            errors_shown,
            arena,
            contract_errors: Vec::new(),
        })
    }

    /// The arena the recomputed fragments went to.
    pub fn arena(&self) -> &Arena {
        &self.arena
    }

    /// The pool's width, 0 when it has none.
    pub fn threads(&self) -> usize {
        self.pool.as_ref().map_or(0, Pool::threads)
    }

    /// The next answer, or the failure a thread reported, or the pool's end with no thread left to answer.
    fn answer(&self) -> std::result::Result<Answer, BuildError> {
        match self.events.recv() {
            Ok(Report::Answered(answer)) => Ok(answer),
            Ok(Report::Failed(failure)) => {
                Err(failed(format!("the units engine failed: {failure}")))
            }
            Err(_) => Err(failed("the units engine's threads ended before answering")),
        }
    }

    /// Phase 1 (`_RecomputeRunner.phase1` through `_drive_engine`): the `recomputed` ordinals handed out in configuration order, a handout a thread to start and the next as each answer arrives, each materialized from the table and the store as it is handed out; each answer's projections loaded into `store` at their ordinals with the arena's spans as their sources (`_load_recomputed`), its errors kept (`_keep_contract_errors`), and a `[progress]` line written to `console`. The table's name tuples are released once the phase ends, as the units read none after it (`UnitTable.release_names`).
    pub fn phase1(
        &mut self,
        table: &mut UnitTable,
        store: &mut UnitStore,
        recomputed: &[u32],
        console: &mut impl Write,
    ) -> std::result::Result<(), BuildError> {
        if let Some(pool) = &self.pool {
            let order = configuration_order(recomputed, table);
            let mut handouts = order.chunks(self.handout).enumerate();
            let total = recomputed.len() as u64;
            let mut in_flight = 0usize;
            for _ in 0..pool.threads() {
                in_flight += usize::from(hand(pool, &self.arena, &mut handouts, table, store)?);
            }
            let mut done = 0u64;
            while in_flight > 0 {
                let Answer::Batch {
                    projections,
                    errors,
                } = self.answer()?
                else {
                    return Err(failed(
                        "the units engine answered a batch with a verification",
                    ));
                };
                in_flight -= 1;
                done += projections.len() as u64;
                for (projection, span) in projections {
                    let ordinal = usize::try_from(projection.ordinal)
                        .ok()
                        .filter(|&ordinal| ordinal < table.n)
                        .ok_or_else(|| {
                            failed(format!(
                                "the units engine answered ordinal {}",
                                projection.ordinal
                            ))
                        })?;
                    let values = table
                        .codepoints(ordinal)
                        .into_iter()
                        .map(i64::from)
                        .collect();
                    let length = projection.raw_length as i64;
                    let address =
                        LoadAddress::Span(span.part(), span.start as i64, span.length as i64);
                    store
                        .load_projection(
                            &recomputed_of(projection, values),
                            table.no_verdict(ordinal),
                            None,
                            Some(&address),
                            length,
                        )
                        .map_err(failed)?;
                }
                let room = self.errors_shown.saturating_sub(self.contract_errors.len());
                self.contract_errors.extend(errors.into_iter().take(room));
                console::progress(console, done, Some(total), PHASE1_UNITS).map_err(failed)?;
                in_flight += usize::from(hand(pool, &self.arena, &mut handouts, table, store)?);
            }
        }
        table.release_names();
        Ok(())
    }

    /// The verification (`_RecomputeRunner.verify` under the Rust engine): the sampled units with their injections, stably sorted into configuration order and cut into one contiguous share a thread, each recomputed from nothing; their keys in the shares' order as a dict by the id each recomputation stamped ([`keyed`]).
    pub fn verify(
        &self,
        table: &UnitTable,
        mut items: Vec<(Unit, Injection)>,
    ) -> std::result::Result<Vec<VerifyKey>, BuildError> {
        if items.is_empty() {
            return Ok(Vec::new());
        }
        let Some(pool) = &self.pool else {
            return Err(failed("the units phase started no pool to verify with"));
        };
        items.sort_by_key(|(unit, _)| table.config_rank(unit.ordinal));
        let records = items
            .into_iter()
            .map(|(unit, injection)| (unit_record(&unit, table), injection))
            .collect();
        let shares = shares(records, pool.threads());
        let mut answered: Vec<Option<Vec<VerifyKey>>> = shares.iter().map(|_| None).collect();
        for (share, items) in shares.into_iter().enumerate() {
            if !pool.submit(Job::Verify { share, items }) {
                return Err(failed("the units engine's threads ended before verifying"));
            }
        }
        for _ in 0..answered.len() {
            let Answer::Verify { share, keys } = self.answer()? else {
                return Err(failed(
                    "the units engine answered a verification with a batch",
                ));
            };
            if let Some(slot) = answered.get_mut(share) {
                *slot = Some(keys);
            }
        }
        Ok(keyed(answered.into_iter().flatten().flatten()))
    }
}

/// Hand the next handout to `pool`, its units materialized from `table` and `store`; false once none is left.
fn hand<'a>(
    pool: &Pool<Job>,
    arena: &Arc<Arena>,
    handouts: &mut impl Iterator<Item = (usize, &'a [u32])>,
    table: &UnitTable,
    store: &UnitStore,
) -> std::result::Result<bool, BuildError> {
    let Some((batch, ordinals)) = handouts.next() else {
        return Ok(false);
    };
    let view: &dyn UnitStoreView = store;
    let units = ordinals
        .iter()
        .map(|&ordinal| unit_record(&table.unit(ordinal as usize, Some(view)), table))
        .collect();
    if !pool.submit(Job::Batch {
        batch,
        units,
        arena: Arc::clone(arena),
    }) {
        return Err(failed(format!(
            "batch {batch}: the units engine's threads have ended"
        )));
    }
    Ok(true)
}

/// The keys of the `units` stage's lines beside its units (`units_stage_fixtures.UNITS_*_KEY`).
const ERRORS_KEY: &str = "errors";
const PROGRESS_KEY: &str = "progress";
const ARENA_KEY: &str = "arena";

/// One row of the store as the `units` stage reads it: the row as the `store-load` stage reads one, a source in the arena as its part `arena`, its length and the sha256 of its deflated bytes.
fn units_reading(store: &UnitStore, arena: &Arena, ordinal: usize) -> Result<PyValue> {
    let PyValue::Dict(mut fields) = super::store::reading(store, ordinal) else {
        return Err(WireError("a store row does not read as a dict".to_owned()));
    };
    if let Some(source) = store.source(ordinal)
        && let Some(file) = Arena::file_of(&source.part)
    {
        let span = ArenaSpan {
            file,
            start: source.start as u64,
            length: source.length as u64,
        };
        let bytes = arena.read(span).map_err(WireError)?;
        let digest: String = Sha256::digest(&bytes)
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        for (name, value) in &mut fields {
            if matches!(name, PyValue::Str(name) if name == "source") {
                *value = PyValue::Tuple(vec![
                    PyValue::str(ARENA_PART),
                    PyValue::Int(source.length),
                    PyValue::str(&digest),
                ]);
            }
        }
    }
    Ok(PyValue::Dict(fields))
}

/// Write the `units` stage's lines over the store after phase 1, as the Python stage's `units_boundary` lays them out: a `unit:<ordinal>` line for each ordinal in `units`, the row as [`units_reading`] reads it, and the column over every row; the kept errors, sorted, since a pooled build keeps them in arrival order; how many `[progress]` lines the phase printed and the last; its other console lines; and the arena's fragment count, deflated bytes, shard bytes and note.
fn units_lines(
    store: &UnitStore,
    drive: &Drive,
    printed: &[u8],
    units: &HashSet<usize>,
    out: &mut impl Write,
) -> Result<()> {
    let mut digest = ColumnDigest::new();
    for ordinal in 0..store.len() {
        let value = units_reading(store, &drive.arena, ordinal)?;
        if units.contains(&ordinal) {
            stage_line(out, &format!("{UNIT_PREFIX}{ordinal}"), &value)?;
        }
        digest.add(&value);
    }
    digest.write(out, "value")?;
    let mut errors = drive.contract_errors.clone();
    errors.sort();
    stage_line(
        out,
        ERRORS_KEY,
        &PyValue::List(errors.into_iter().map(PyValue::Str).collect()),
    )?;
    let printed = String::from_utf8_lossy(printed);
    let (progress, other): (Vec<&str>, Vec<&str>) = printed
        .lines()
        .partition(|line| line.starts_with(console::PROGRESS));
    stage_line(
        out,
        PROGRESS_KEY,
        &PyValue::Tuple(vec![
            PyValue::Int(progress.len() as i64),
            PyValue::optional_str(progress.last().copied()),
        ]),
    )?;
    stage_line(
        out,
        CONSOLE_KEY,
        &PyValue::List(other.into_iter().map(PyValue::str).collect()),
    )?;
    let arena = &drive.arena;
    stage_line(
        out,
        ARENA_KEY,
        &PyValue::Tuple(vec![
            PyValue::Int(arena.count() as i64),
            PyValue::Int(arena.bytes() as i64),
            PyValue::Int(arena.raw() as i64),
            PyValue::str(arena.note()),
        ]),
    )
}

/// What a stage holds once the build has run through phase 1 ([`through_units`]): the table, the places in the ledger of the classes the load reached, the ink-signature store as the load left it, the plan, the units phase's pool with its arena, and the console lines phase 1 printed.
pub struct UnitsRun {
    pub table: UnitTable,
    pub classes_present: Vec<usize>,
    pub signatures: SignatureArrays,
    pub planned: Planned,
    pub drive: Drive,
    pub printed: Vec<u8>,
}

/// Run the build's load and plan as the `plan` stage runs them, over the audit the build input names, read through the descriptors the process inherits, and a tree of links to the dump's prior corpus in a scratch directory named for `stage` beside the stage file; then phase 1 ([`Drive::phase1`]) at the input's units width, its arena's spill files in that directory and its console lines kept rather than printed; and hand what the build holds then to `then`. The scratch directory is removed once `then` returns.
pub fn through_units<T>(
    input: &BuildInput,
    stage_file: &Path,
    stage: &str,
    then: impl FnOnce(&mut UnitsRun) -> Result<T>,
) -> Result<T> {
    let files = input.held_audit()?;
    let prior = dump_prior(stage_file)?;
    let loaded = load_corpus(input, &files, &prior, &mut Vec::new())
        .map_err(|failure| WireError(format!("the load failed: {failure}")))?;
    let directory = stage_file.parent().unwrap_or_else(|| Path::new("."));
    let scratch = directory.join(format!("{stage}.scratch-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&scratch);
    let answered = (|| {
        let corpus = scratch.join("out");
        linked_tree(&prior, &corpus)?;
        let mut table = loaded.workload.table;
        let mut planned = plan_phase(
            input,
            &corpus,
            &super::sink::Sink::new(None),
            &table,
            loaded.input_keys,
            &mut Vec::new(),
        )
        .map_err(|failure| WireError(format!("the plan failed: {failure}")))?;
        let (jobs, _) = units_pool_width(&input.widths);
        let arena = Arc::new(Arena::new(&scratch));
        let mut drive = Drive::start(
            input,
            jobs,
            planned.recomputed.len(),
            planned.verify_units.len(),
            arena,
        )
        .map_err(|failure| WireError(format!("the units phase failed: {failure}")))?;
        let mut printed = Vec::new();
        drive
            .phase1(
                &mut table,
                &mut planned.store,
                &planned.recomputed,
                &mut printed,
            )
            .map_err(|failure| WireError(format!("the units phase failed: {failure}")))?;
        then(&mut UnitsRun {
            table,
            classes_present: loaded.workload.classes_present,
            signatures: loaded.signatures,
            planned,
            drive,
            printed,
        })
    })();
    let _ = std::fs::remove_dir_all(&scratch);
    answered
}

/// The `units` stage: run the build through phase 1 ([`through_units`]) and answer with the lines `units_lines` writes, a `unit:<ordinal>` line for each one the stage file asks for. A stage file with no lines, from a build whose phase 1 did not finish, is answered with none.
pub fn stage_units(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let (units, lines) = stage_unit_lines(stage_file)?;
    if lines == 0 {
        return Ok(());
    }
    through_units(&input, stage_file, "units", |run| {
        units_lines(&run.planned.store, &run.drive, &run.printed, &units, out)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let directory =
            std::env::temp_dir().join(format!("ams-review-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&directory);
        std::fs::create_dir_all(&directory).expect("scratch");
        directory
    }

    #[test]
    fn the_arena_reads_back_what_each_append_wrote_from_files_it_unlinked() {
        let directory = scratch("arena");
        let arena = Arena::new(&directory);
        assert_eq!(arena.note(), "arena empty");
        let first = arena
            .append([(&b"abc"[..], 10), (&b"de"[..], 5)])
            .expect("appended");
        let second = arena.append([(&b"fghi"[..], 9)]).expect("appended");
        assert_eq!(
            first,
            vec![
                ArenaSpan {
                    file: 0,
                    start: 0,
                    length: 3
                },
                ArenaSpan {
                    file: 0,
                    start: 3,
                    length: 2
                }
            ]
        );
        assert_eq!(
            second,
            vec![ArenaSpan {
                file: 0,
                start: 5,
                length: 4
            }]
        );
        assert_eq!(arena.read(first[1]).expect("read"), b"de");
        assert_eq!(arena.read(second[0]).expect("read"), b"fghi");
        assert_eq!((arena.count(), arena.bytes(), arena.raw()), (3, 9, 24));
        assert_eq!(arena.note(), "arena 0.00 GB, deflated 2.67 to 1");
        assert_eq!(std::fs::read_dir(&directory).expect("listed").count(), 0);
        assert!(
            arena
                .read(ArenaSpan {
                    file: 1,
                    start: 0,
                    length: 1
                })
                .is_err()
        );
        assert!(
            arena
                .read(ArenaSpan {
                    file: 0,
                    start: 8,
                    length: 2
                })
                .is_err()
        );
        let _ = std::fs::remove_dir_all(&directory);
    }

    #[test]
    fn appends_in_flight_together_each_take_their_own_spill_file() {
        let directory = scratch("arena-threads");
        let arena = Arc::new(Arena::new(&directory));
        let barrier = Arc::new(std::sync::Barrier::new(4));
        let threads: Vec<_> = (0..4u8)
            .map(|index| {
                let (arena, barrier) = (Arc::clone(&arena), Arc::clone(&barrier));
                std::thread::spawn(move || {
                    let spill = arena.take().expect("a spill file");
                    barrier.wait();
                    arena.free.lock().expect("the lock").push(spill);
                    let bytes = vec![index; usize::from(index) + 1];
                    let span = arena.append([(bytes.as_slice(), 1)]).expect("appended")[0];
                    (span, bytes)
                })
            })
            .collect();
        for thread in threads {
            let (span, bytes) = thread.join().expect("joined");
            assert_eq!(arena.read(span).expect("read"), bytes);
        }
        assert_eq!(arena.files.lock().expect("the lock").len(), 4);
        let _ = std::fs::remove_dir_all(&directory);
    }

    #[test]
    fn an_arena_part_names_its_spill_file() {
        assert_eq!(
            ArenaSpan {
                file: 12,
                start: 0,
                length: 0
            }
            .part(),
            "arena.12"
        );
        assert_eq!(Arena::file_of("arena.12"), Some(12));
        assert_eq!(Arena::file_of("arena"), None);
        assert_eq!(Arena::file_of("arena."), None);
        assert_eq!(Arena::file_of("arena.+1"), None);
        assert_eq!(Arena::file_of("units/class.json"), None);
    }

    #[test]
    fn a_handout_is_the_ceiling_or_two_draws_a_thread() {
        assert_eq!(handout_width(10_000, 8, 200), 200);
        assert_eq!(handout_width(1_000, 8, 200), 63);
        assert_eq!(handout_width(5, 8, 200), 1);
        assert_eq!(handout_width(0, 4, 200), 1);
    }

    #[test]
    fn the_pool_widens_only_while_the_gate_lane_is_idle() {
        let directory = scratch("gates-idle");
        let marker = directory.join("idle");
        let widths = |idle: Option<i64>, marker: Option<&Path>| Widths {
            threads: 3,
            gates_idle_threads: idle,
            gates_idle_marker: marker.map(|path| path.display().to_string()),
            signature_threads: 1,
            write_threads: None,
        };
        assert_eq!(units_pool_width(&widths(None, None)), (3, ""));
        assert_eq!(units_pool_width(&widths(Some(8), None)), (3, ""));
        assert_eq!(
            units_pool_width(&widths(Some(8), Some(&marker))),
            (3, "gate lane busy")
        );
        std::fs::write(&marker, b"").expect("marker");
        assert_eq!(
            units_pool_width(&widths(Some(8), Some(&marker))),
            (8, "gate lane idle")
        );
        assert_eq!(
            units_pool_width(&widths(Some(0), Some(&marker))),
            (1, "gate lane idle")
        );
        let _ = std::fs::remove_dir_all(&directory);
    }

    #[test]
    fn the_verification_keys_fold_as_a_dict_by_id() {
        let key = |unit_id: &str, content_key: &str| VerifyKey {
            unit_id: unit_id.to_owned(),
            content_key: content_key.to_owned(),
            ink_deltas: Vec::new(),
        };
        assert_eq!(
            keyed([key("u-b", "1"), key("u-a", "2"), key("u-b", "3")]),
            vec![key("u-b", "3"), key("u-a", "2")]
        );
    }
}
