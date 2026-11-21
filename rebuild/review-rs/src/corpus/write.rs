//! The write's runs, as `build._write_corpus` writes them up to the manifest: each run of classes ([`Routed`]'s runs, contiguous stretches of shard order) written on a thread of its own with a shard writer, sidecar lines, a checker and a reader over the previous corpus of its own ([`write_run`], `build._write_run` and `_write_chunk`), every unit emitted ([`Emitter::emission`]) and put into its class's parts, kept where its bytes equal its prior's on a class appended to; then the runs' results folded into the unit store and one checker in shard order (`keep`), so the store's columns, the folded facts and every file come out as the serial write leaves them at any width; the staged parts renamed into place once every run has written (the commit); and the fonts copied in ([`copy_font`]). A failed run stops the runs after it in shard order and deletes what every run staged ([`discard_runs`]), so nothing is committed. [`copy_static`] copies the static app files, which the build does once the manifest is written.
//!
//! [`stage_write`] is the `write` stage of the stage fixtures.

use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::os::unix::fs::FileExt;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use sha2::{Digest, Sha256};

use super::audit::{integer, stage_line};
use super::corpus_check::{CheckSettings, CorpusCheck, Facts};
use super::drive::{Arena, UnitsRun, through_units};
use super::emit::{Emitted, Emitter};
use super::input::{BuildInput, FontCopy, FontRecord};
use super::passes::{Passes, Triage, passes, triage};
use super::prior::PriorFragmentReader;
use super::route::{ClassToWrite, Route, RouteNames, Routed, class_meta, route_classes, write_fit};
use super::scan::stage_lines;
use super::shards::{ShardError, ShardWriter, Span, discard_runs};
use super::sidecars::{PriorSidecars, SidecarLines, SidecarNames, SidecarRun, Spilled};
use super::sink::Sink;
use super::store::UnitStore;
use super::table::{ColumnDigest, UNIT_PREFIX, UnitTable};
use super::{BuildError, failed};
use crate::check::CheckError;
use crate::fragment::fragment_bytes;
use crate::pyfmt::PyValue;
use crate::wire::{Result, WireError, error};

/// The names the write reads from the build input's vocabulary: the shard part cap (`unit_index.SHARD_PART_BYTES`), the routing's names, among them the bytes that close a part, and the sidecars'.
#[derive(Clone, Debug)]
pub struct WriteNames {
    pub cap: i64,
    pub route: RouteNames,
    pub sidecars: SidecarNames,
}

impl WriteNames {
    /// The names as the build input's vocabulary gives them.
    pub fn of(input: &BuildInput) -> Result<Self> {
        let name = "rebuild.review.unit_index.SHARD_PART_BYTES";
        let cap = integer(
            input.constant(name).ok_or_else(|| {
                WireError(format!("the build input's vocabulary holds no {name}"))
            })?,
            "SHARD_PART_BYTES",
        )?;
        Ok(Self {
            cap,
            route: RouteNames::of(input)?,
            sidecars: SidecarNames::of(input)?,
        })
    }
}

/// What the write counted, as `build._WrittenCorpus` holds it: the units written without parsing, those kept at their prior address by compare, the bytes appended to overflow parts and the plain bytes those inflate to, the fragments written deflated, the dead bytes the appended classes' parts hold once written, the classes folded and those written directly, by the share rule or as a first base, and the sidecar rows copied forward.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Counters {
    pub byte_copied: i64,
    pub kept: i64,
    pub appended: i64,
    pub appended_inflated: i64,
    pub deflated: i64,
    pub dead: i64,
    pub folded: i64,
    pub direct: i64,
    pub copied_forward: i64,
}

impl Counters {
    /// The counters by name, in the order the `write` stage records them.
    pub fn to_py(&self) -> PyValue {
        PyValue::Dict(
            [
                ("byte_copied", self.byte_copied),
                ("kept", self.kept),
                ("appended", self.appended),
                ("appended_inflated", self.appended_inflated),
                ("deflated", self.deflated),
                ("dead", self.dead),
                ("folded", self.folded),
                ("direct", self.direct),
                ("copied_forward", self.copied_forward),
            ]
            .into_iter()
            .map(|(name, count)| (PyValue::str(name), PyValue::Int(count)))
            .collect(),
        )
    }
}

/// What writing one class leaves for the fold (`build._ChunkWritten` over the whole class): per unit in the class's order its span and its config note and policy file; the class's parts with their total bytes; its counts; and the facts its units gave the run's checker.
struct ClassWritten {
    place: usize,
    spans: Vec<Span>,
    notes: Vec<Option<String>>,
    policy_files: Vec<Option<String>>,
    shards: Vec<String>,
    shard_bytes: i64,
    byte_copied: i64,
    kept: i64,
    appended: i64,
    appended_inflated: i64,
    deflated: i64,
    facts: Facts,
}

/// What writing one run leaves (`build._RunWritten`): its classes' results in shard order, the renames of its staged parts, and its sidecar lines.
struct RunWritten {
    classes: Vec<ClassWritten>,
    pending: Vec<(PathBuf, PathBuf)>,
    sidecars: SidecarRun,
}

/// What every run of the write reads (`build._WriteSettings` and the columns it shares): the corpus directory, the directory its spill files go in, the table and the store, the classes in shard order with their manifest records, the units phase's arena, the emitter, the checkers' settings, the ids some unit names as its primary unit, whose identities the run checkers keep, the names, and whether the previous sidecars are copied forward.
struct Shared<'s> {
    out_dir: &'s Path,
    scratch: &'s Path,
    table: &'s UnitTable,
    store: &'s UnitStore,
    classes: &'s [ClassToWrite],
    metas: &'s [PyValue],
    arena: &'s Arena,
    emitter: &'s Emitter,
    settings: &'s CheckSettings,
    primary_units: &'s BTreeSet<String>,
    names: &'s WriteNames,
    copy_forward: bool,
}

fn shard_failure(failure: ShardError) -> BuildError {
    failed(format!("{}: {failure}", failure.class()))
}

fn check_failure(failure: CheckError) -> BuildError {
    failed(failure)
}

fn os_failure(failure: io::Error) -> BuildError {
    failed(format!("OSError: {failure}"))
}

fn assertion(what: impl std::fmt::Display) -> BuildError {
    failed(format!("AssertionError: {what}"))
}

/// A config note or a policy file as the store keeps it: a str, or None.
fn optional_text(
    value: &PyValue,
    what: &str,
    unit_id: &str,
) -> std::result::Result<Option<String>, BuildError> {
    match value {
        PyValue::None => Ok(None),
        PyValue::Str(text) => Ok(Some(text.clone())),
        other => Err(failed(format!(
            "unit {unit_id}: its {what} is {}, not a str",
            other.kind()
        ))),
    }
}

/// Write one unit of the open class (`build._write_chunk`'s loop): its emission read from the arena or the previous corpus, put into the class's parts through `writer` (kept at its prior address where the class is appended to and its bytes equal its prior's, a stored fragment's where its part is the class's), checked at `PATCHED` unless the cache supplied it, and projected or copied forward onto the run's sidecar lines. Its span, config note and policy file go onto `class`, which counts it when it was written without parsing or kept its prior address by compare.
#[allow(clippy::too_many_arguments)]
fn write_unit(
    shared: &Shared<'_>,
    ordinal: usize,
    writer: &mut ShardWriter,
    prior: &mut PriorFragmentReader,
    check: &mut CorpusCheck<'_>,
    sidecars: &mut SidecarLines<'_>,
    class: &mut ClassWritten,
) -> std::result::Result<(), BuildError> {
    let (table, store) = (shared.table, shared.store);
    let source = store.source(ordinal).ok_or_else(|| assertion(ordinal))?;
    let emission = shared
        .emitter
        .emission(ordinal, &source, table, store, shared.arena, prior)?;
    if emission.unit_id != source.unit_id {
        return Err(assertion(format!("({:?}, {ordinal})", emission.unit_id)));
    }
    let order = table.order(ordinal).map(i64::from);
    let batch = table.batch(ordinal).map(i64::from);
    let (span, note) = match &emission.emitted {
        Emitted::Fragment(fragment) => {
            let body = fragment_bytes(fragment).map_err(failed)?;
            let former = if writer.appending() {
                store.prior_fragment(ordinal)
            } else {
                None
            };
            let kept = match &former {
                Some(former) if writer.holds(&former.part) => prior
                    .same_bytes(former, body.as_bytes())
                    .map_err(|failure| failed(format!("OSError: {failure}")))?,
                _ => false,
            };
            let span = match former {
                Some(former) if kept => {
                    class.kept += 1;
                    writer
                        .keep(&former.part, former.start, former.length, former.inflated)
                        .map_err(shard_failure)?
                }
                _ => writer
                    .add_bytes(body.as_bytes(), 0)
                    .map_err(shard_failure)?,
            };
            drop(body);
            check
                .unit(fragment, store.flags(ordinal).cached)
                .map_err(check_failure)?;
            sidecars.unit(fragment, span, order, batch)?;
            (
                span,
                shared.emitter.emitted_note(&emission, table, ordinal)?,
            )
        }
        Emitted::Bytes {
            body,
            source,
            identity,
        } => {
            let span = if writer.holds(&source.part) {
                writer
                    .keep(&source.part, source.start, source.length, source.inflated)
                    .map_err(shard_failure)?
            } else {
                let inflated = u32::try_from(source.inflated).map_err(|_| {
                    failed(format!(
                        "unit {}: an inflated length of {}",
                        source.unit_id, source.inflated
                    ))
                })?;
                writer.add_bytes(body, inflated).map_err(shard_failure)?
            };
            check.unit(identity, true).map_err(check_failure)?;
            sidecars.cached(
                &emission.unit_id,
                body,
                source.inflated,
                identity,
                span,
                order,
                batch,
            )?;
            class.byte_copied += 1;
            (span, shared.emitter.config_note(table.configs(ordinal)))
        }
    };
    if emission.content_key != store.content_key_hex(ordinal) {
        return Err(assertion(&emission.unit_id));
    }
    class.spans.push(span);
    class
        .notes
        .push(optional_text(&note, "config note", &emission.unit_id)?);
    class.policy_files.push(emission.policy_file);
    Ok(())
}

/// Write the run's classes, in shard order, through `writer`, with sidecar lines, a reader over the previous corpus and a checker of the run's own; None when `stopped` says a run before this one failed.
fn write_classes(
    shared: &Shared<'_>,
    places: &[usize],
    writer: &mut ShardWriter,
    stopped: &dyn Fn() -> bool,
) -> std::result::Result<Option<(Vec<ClassWritten>, SidecarRun)>, BuildError> {
    let names = &shared.names.sidecars;
    let prior_sidecars = PriorSidecars::open(shared.out_dir, shared.copy_forward, names)?;
    let mut sidecars = SidecarLines::new(&prior_sidecars, shared.scratch, names)?;
    let mut prior = PriorFragmentReader::new(shared.out_dir);
    let mut check = CorpusCheck::new(shared.settings, Some(shared.primary_units));
    let mut classes = Vec::with_capacity(places.len());
    for &place in places {
        let entry = &shared.classes[place];
        writer
            .open(
                &entry.id,
                &entry.prior_parts,
                entry.route == Route::Append,
                entry.route != Route::First,
            )
            .map_err(shard_failure)?;
        check
            .class_start(&shared.metas[place])
            .map_err(check_failure)?;
        let mut class = ClassWritten {
            place,
            spans: Vec::with_capacity(entry.ordinals.len()),
            notes: Vec::with_capacity(entry.ordinals.len()),
            policy_files: Vec::with_capacity(entry.ordinals.len()),
            shards: Vec::new(),
            shard_bytes: 0,
            byte_copied: 0,
            kept: 0,
            appended: 0,
            appended_inflated: 0,
            deflated: 0,
            facts: Facts::default(),
        };
        let since = (writer.appended, writer.appended_inflated, writer.deflated);
        for &ordinal in &entry.ordinals {
            if stopped() {
                return Ok(None);
            }
            write_unit(
                shared,
                ordinal as usize,
                writer,
                &mut prior,
                &mut check,
                &mut sidecars,
                &mut class,
            )?;
        }
        class.appended = writer.appended - since.0;
        class.appended_inflated = writer.appended_inflated - since.1;
        class.deflated = writer.deflated - since.2;
        (class.shards, class.shard_bytes) = writer.close().map_err(shard_failure)?;
        check.class_end().map_err(check_failure)?;
        class.facts = check.take_facts();
        classes.push(class);
    }
    Ok(Some((classes, sidecars.close()?)))
}

/// A run's outcome: what it wrote, None when a run before it in shard order failed and stopped it, or why it failed.
type RunOutcome = std::result::Result<Option<RunWritten>, BuildError>;

/// `build._write_run`: write one run of classes with a shard writer of its own, its staged parts left for the commit; a run that fails or is stopped deletes what it staged.
fn write_run(shared: &Shared<'_>, places: &[usize], stopped: &dyn Fn() -> bool) -> RunOutcome {
    let mut writer = ShardWriter::new(
        shared.out_dir,
        shared.names.cap,
        shared.names.route.part_closing.clone(),
    )
    .map_err(os_failure)?;
    match write_classes(shared, places, &mut writer, stopped) {
        Ok(Some((classes, sidecars))) => Ok(Some(RunWritten {
            classes,
            pending: writer.take_pending(),
            sidecars,
        })),
        outcome => {
            writer.abort().map_err(os_failure)?;
            outcome.map(|_| None)
        }
    }
}

/// The runs written on `jobs` threads, each taking the largest run left, by index in shard order: each run's outcome, or None for a run not written because a run before it failed. At one thread the runs are written on this one.
fn write_runs(shared: &Shared<'_>, runs: &[Vec<usize>], jobs: usize) -> Vec<Option<RunOutcome>> {
    let mut queue: Vec<usize> = (0..runs.len()).collect();
    queue.sort_by_key(|&index| {
        std::cmp::Reverse(
            runs[index]
                .iter()
                .map(|&place| shared.classes[place].ordinals.len())
                .sum::<usize>(),
        )
    });
    let next = AtomicUsize::new(0);
    let failed_at = AtomicUsize::new(usize::MAX);
    let outcomes: Vec<Mutex<Option<RunOutcome>>> = runs.iter().map(|_| Mutex::new(None)).collect();
    let work = || {
        loop {
            let taken = next.fetch_add(1, Ordering::Relaxed);
            let Some(&index) = queue.get(taken) else {
                return;
            };
            if failed_at.load(Ordering::Relaxed) < index {
                continue;
            }
            let stopped = || failed_at.load(Ordering::Relaxed) < index;
            let outcome = write_run(shared, &runs[index], &stopped);
            if outcome.is_err() {
                failed_at.fetch_min(index, Ordering::Relaxed);
            }
            if let Ok(mut held) = outcomes[index].lock() {
                *held = Some(outcome);
            }
        }
    };
    let mut panicked: Option<String> = None;
    if jobs <= 1 {
        work();
    } else {
        std::thread::scope(|scope| {
            let handles: Vec<_> = (0..jobs).map(|_| scope.spawn(work)).collect();
            for handle in handles {
                if let Err(payload) = handle.join() {
                    let message = payload
                        .downcast_ref::<&str>()
                        .map(|text| (*text).to_owned())
                        .or_else(|| payload.downcast_ref::<String>().cloned())
                        .unwrap_or_else(|| "a panic with no message".to_owned());
                    panicked.get_or_insert(message);
                }
            }
        });
    }
    let mut outcomes: Vec<_> = outcomes
        .into_iter()
        .map(|held| held.into_inner().ok().flatten())
        .collect();
    if let Some(message) = panicked {
        let first = outcomes.iter().position(Option::is_none).unwrap_or(0);
        outcomes[first] = Some(Err(failed(format!(
            "a thread of the write panicked: {message}"
        ))));
    }
    outcomes
}

/// What the write leaves once its parts are committed and the fonts copied: each class's manifest record in shard order, its `shards` filled; the fonts' records, before then after; the checker every class's facts were folded into, in shard order; each run's sidecar lines, in shard order; the width the runs were written at; and the counters.
pub struct Written<'a> {
    pub metas: Vec<PyValue>,
    pub fonts: Vec<(String, PyValue)>,
    pub check: CorpusCheck<'a>,
    pub runs: Vec<SidecarRun>,
    pub jobs: usize,
    pub counters: Counters,
}

/// Set a manifest record's `shards`.
fn set_shards(meta: &mut PyValue, shards: &[String]) {
    if let PyValue::Dict(entries) = meta
        && let Some(entry) = entries
            .iter_mut()
            .find(|(key, _)| matches!(key, PyValue::Str(key) if key == "shards"))
    {
        entry.1 = PyValue::List(shards.iter().map(PyValue::str).collect());
    }
}

/// `build._write_corpus` up to the manifest, over the classes `routed` holds in shard order with their routes and runs: each class's manifest record (`_class_meta`); the runs written on as many threads as there are runs, at most the width they were cut at ([`write_runs`]); their results folded in shard order into `store` (each unit's span, config note, policy file and, once its class names its parts, its part) and into one checker, with the counters; the staged parts renamed into place; and the fonts copied into `fonts/` ([`copy_font`]). A run that fails, or a rename, deletes what every run staged ([`discard_runs`]) and fails the write with the first failure in shard order; a font that fails comes after the commit.
#[allow(clippy::too_many_arguments)]
pub fn write_corpus<'a>(
    input: &BuildInput,
    out_dir: &Path,
    scratch: &Path,
    table: &UnitTable,
    store: &mut UnitStore,
    routed: &Routed,
    arena: &Arena,
    emitter: &Emitter,
    settings: &'a CheckSettings,
    names: &WriteNames,
    sink: &Sink,
) -> std::result::Result<Written<'a>, BuildError> {
    let mut metas = routed
        .classes
        .iter()
        .map(|entry| class_meta(entry, table, store, &names.route.machine_checks))
        .collect::<std::result::Result<Vec<_>, _>>()?;
    let primary_units = store.primary_unit_ids();
    let jobs = routed.width.min(routed.runs.len());
    let discard = || {
        discard_runs(
            out_dir,
            routed
                .runs
                .iter()
                .flatten()
                .map(|&place| routed.classes[place].id.as_str()),
        )
        .map_err(os_failure)
    };
    let outcomes = {
        let shared = Shared {
            out_dir,
            scratch,
            table,
            store,
            classes: &routed.classes,
            metas: &metas,
            arena,
            emitter,
            settings,
            primary_units: &primary_units,
            names,
            copy_forward: routed.copy_forward,
        };
        write_runs(&shared, &routed.runs, jobs)
    };
    let mut written = Vec::with_capacity(outcomes.len());
    for outcome in outcomes {
        match outcome {
            Some(Ok(Some(run))) => written.push(run),
            Some(Err(failure)) => {
                discard()?;
                return Err(failure);
            }
            _ => {
                discard()?;
                return Err(failed("a run of the write was neither written nor failed"));
            }
        }
    }
    let closing = names.route.part_closing.len() as i64;
    let mut check = CorpusCheck::new(settings, None);
    let mut counters = Counters::default();
    let mut runs = Vec::with_capacity(written.len());
    let mut pending = Vec::new();
    for run in written {
        for class in run.classes {
            let entry = &routed.classes[class.place];
            for (index, &ordinal) in entry.ordinals.iter().enumerate() {
                let ordinal = ordinal as usize;
                let span = class.spans[index];
                store
                    .set_written_span(ordinal, span.start, span.length, span.inflated)
                    .map_err(failed)?;
                store.set_config_note(ordinal, class.notes[index].as_deref());
                store.set_policy_file(ordinal, class.policy_files[index].as_deref());
            }
            check.absorb(class.facts).map_err(check_failure)?;
            counters.byte_copied += class.byte_copied;
            counters.kept += class.kept;
            counters.appended += class.appended;
            counters.appended_inflated += class.appended_inflated;
            counters.deflated += class.deflated;
            set_shards(&mut metas[class.place], &class.shards);
            let live: i64 = class.spans.iter().map(|span| span.length + 1).sum();
            if entry.route == Route::Append {
                counters.dead += class.shard_bytes - closing * class.shards.len() as i64 - live;
            }
            for (index, &ordinal) in entry.ordinals.iter().enumerate() {
                let part = class.spans[index].part;
                let name = class
                    .shards
                    .get(part)
                    .ok_or_else(|| failed(format!("IndexError: part {part} of {}", entry.id)))?;
                store.set_written_part(ordinal as usize, name);
            }
        }
        pending.extend(run.pending);
        counters.copied_forward += run.sidecars.copied_forward;
        runs.push(run.sidecars);
    }
    for (staging, target) in &pending {
        if let Err(failure) = fs::rename(staging, target) {
            discard()?;
            return Err(os_failure(failure));
        }
    }
    counters.folded = routed
        .classes
        .iter()
        .filter(|entry| entry.route == Route::Fold)
        .count() as i64;
    counters.direct = routed
        .classes
        .iter()
        .filter(|entry| matches!(entry.route, Route::Direct | Route::First))
        .count() as i64;
    let mut fonts = Vec::with_capacity(input.fonts.len());
    for ((side, copy), (_, record)) in input.fonts.iter().zip(&input.manifest.fonts) {
        copy_font(sink, out_dir, copy)?;
        fonts.push((side.clone(), font_record(record)));
    }
    Ok(Written {
        metas,
        fonts,
        check,
        runs,
        jobs,
        counters,
    })
}

/// A font's manifest record, as `_copy_font` returns it.
pub fn font_record(record: &FontRecord) -> PyValue {
    PyValue::Dict(vec![
        (PyValue::str("file"), PyValue::str(&record.file)),
        (PyValue::str("family"), PyValue::str(&record.family)),
        (PyValue::str("source"), PyValue::str(&record.source)),
        (PyValue::str("sha256"), PyValue::str(&record.sha256)),
        (PyValue::str("upem"), PyValue::Int(record.upem)),
    ])
}

/// The sha256 of a file's bytes, as hex, read in chunks.
fn file_sha256(path: &Path) -> io::Result<String> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut chunk = vec![0; 1 << 20];
    loop {
        let read = file.read(&mut chunk)?;
        if read == 0 {
            break;
        }
        digest.update(&chunk[..read]);
    }
    Ok(digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

/// What a font's copy raises: an `OSError`, or the refusal of a font that changed since the load.
enum CopyFailure {
    Os(io::Error),
    Changed(String),
}

impl From<io::Error> for CopyFailure {
    fn from(failure: io::Error) -> Self {
        Self::Os(failure)
    }
}

/// `build._copy_font`: copy one font into the corpus as `fonts/<name>`, renamed into place through its staging file, and checked against the digest the build took when it loaded the font; a copy already there with that digest is left in place. Return whether it was written. A font whose copy has another digest stops the build with Python's `SystemExit` text.
pub fn copy_font(
    sink: &Sink,
    out_dir: &Path,
    copy: &FontCopy,
) -> std::result::Result<bool, BuildError> {
    let target = out_dir.join("fonts").join(&copy.name);
    sink.replace_output_with(&target, Some(&copy.sha256), |staging| {
        io::copy(&mut File::open(&copy.source)?, &mut File::create(staging)?)?;
        let digest = file_sha256(staging)?;
        if digest != copy.sha256 {
            return Err(CopyFailure::Changed(format!(
                "{} changed between this build's load ({}) and its copy ({digest}), so the units would describe a font other than the one shipped beside them; rebuild the corpus",
                copy.source, copy.sha256
            )));
        }
        Ok(())
    })
    .map_err(|failure| match failure {
        CopyFailure::Os(failure) => os_failure(failure),
        CopyFailure::Changed(message) => BuildError::Exit(message),
    })
}

/// `build.copy_static`: copy each static app file into `out_dir` at its relative path, and the fallback `index.html` when they hold none, each renamed into place and left where it already holds those bytes; return their relative paths.
pub fn copy_static(
    sink: &Sink,
    out_dir: &Path,
    files: &[(String, String)],
    fallback: &str,
) -> io::Result<Vec<String>> {
    let mut copied = Vec::with_capacity(files.len() + 1);
    for (relative, source) in files {
        sink.replace_output_from(&out_dir.join(relative), Path::new(source), true)?;
        copied.push(relative.clone());
    }
    if !copied.iter().any(|relative| relative == "index.html") {
        sink.replace_output(&out_dir.join("index.html"), fallback.as_bytes(), true)?;
        copied.push("index.html".to_owned());
    }
    Ok(copied)
}

// --- the stage ----------------------------------------------------------------------------------

/// The keys of the `write` stage's lines beside its units (`units_stage_fixtures.WRITE_*`).
const CLASS_PREFIX: &str = "class:";
const UNITS_FILES_KEY: &str = "units-files";
const FONTS_KEY: &str = "fonts";
const FONTS_FILES_KEY: &str = "fonts-files";
const COUNTERS_KEY: &str = "counters";
const SIDECARS_KEY: &str = "sidecars";
const FACTS_KEY: &str = "facts";
const STATIC_GENERATED: &str = "generated:static:";
const FONT_GENERATED: &str = "generated:font:";

/// Each regular file under `directory`, by its path relative to it in code-point order, with its size and sha256; none for a directory that is not there.
pub(crate) fn files_held(directory: &Path) -> Result<PyValue> {
    let fail =
        |failure: io::Error| WireError(format!("cannot list {}: {failure}", directory.display()));
    let mut found: Vec<(String, PathBuf)> = Vec::new();
    let mut pending = vec![directory.to_path_buf()];
    if !directory.is_dir() {
        return Ok(PyValue::List(Vec::new()));
    }
    while let Some(at) = pending.pop() {
        for entry in fs::read_dir(&at).map_err(fail)? {
            let path = entry.map_err(fail)?.path();
            let kind = fs::symlink_metadata(&path).map_err(fail)?;
            if kind.is_dir() {
                pending.push(path);
            } else if path.is_file() {
                let relative = path
                    .strip_prefix(directory)
                    .map_err(|why| WireError(why.to_string()))?
                    .to_string_lossy()
                    .into_owned();
                found.push((relative, path));
            }
        }
    }
    found.sort();
    found
        .into_iter()
        .map(|(name, path)| {
            let size = fs::metadata(&path).map_err(fail)?.len();
            Ok(PyValue::Tuple(vec![
                PyValue::Str(name),
                PyValue::Int(size as i64),
                PyValue::Str(file_sha256(&path).map_err(fail)?),
            ]))
        })
        .collect::<Result<Vec<_>>>()
        .map(PyValue::List)
}

/// A digest of spill files taken one after another, as the stage records one: their length together and the sha256 of their bytes, read in chunks.
#[derive(Default)]
struct SpillDigest {
    length: i64,
    digest: Sha256,
}

impl SpillDigest {
    fn add(&mut self, spilled: &Spilled) -> io::Result<()> {
        let mut chunk = vec![0; 1 << 20];
        let mut at = 0;
        while at < spilled.length {
            let take = usize::try_from(spilled.length - at)
                .unwrap_or(usize::MAX)
                .min(chunk.len());
            spilled.file.read_exact_at(&mut chunk[..take], at as u64)?;
            self.digest.update(&chunk[..take]);
            at += take as i64;
        }
        self.length += spilled.length;
        Ok(())
    }

    fn value(self) -> PyValue {
        PyValue::Tuple(vec![
            PyValue::Int(self.length),
            PyValue::Str(
                self.digest
                    .finalize()
                    .iter()
                    .map(|byte| format!("{byte:02x}"))
                    .collect(),
            ),
        ])
    }
}

/// The runs' sidecar lines taken together, as the `write` stage records them: the app-index and locator row counts, the rows copied forward, the index lines', the app-index lines' and the locator rows' spill files run after run as their digests ([`SpillDigest`]), and the locator blocks' table entries, each `byte_start` moved by the rows of the runs before it.
fn sidecars_value(runs: &[SidecarRun]) -> Result<PyValue> {
    let read = |failure: io::Error| WireError(format!("cannot read a spill file: {failure}"));
    let (mut index, mut app, mut rows) = (
        SpillDigest::default(),
        SpillDigest::default(),
        SpillDigest::default(),
    );
    let mut blocks = Vec::new();
    let (mut human, mut machine, mut copied) = (0, 0, 0);
    for run in runs {
        index.add(&run.index).map_err(read)?;
        app.add(&run.app).map_err(read)?;
        let base = rows.length;
        rows.add(&run.rows).map_err(read)?;
        blocks.extend(run.blocks.iter().map(|block| block.value(base)));
        human += run.human;
        machine += run.machine;
        copied += run.copied_forward;
    }
    Ok(PyValue::Tuple(vec![
        PyValue::Int(human),
        PyValue::Int(machine),
        PyValue::Int(copied),
        index.value(),
        app.value(),
        rows.value(),
        PyValue::List(blocks),
    ]))
}

/// Write the `write` stage's run lines, as the Python stage's `write_boundary` lays them out: a `unit:<ordinal>` line for each ordinal in `units`, the unit's written address, inflated length, config note and policy file, and the column over every unit; a `class:<place>` line a class in shard order, its id and manifest record; the files under `units/` and `fonts/`; the fonts' records; the counters; the runs' sidecar lines taken together; and the folded checker's facts.
fn write_lines(
    corpus: &Path,
    store: &UnitStore,
    routed: &Routed,
    written: &Written<'_>,
    units: &std::collections::HashSet<usize>,
    out: &mut impl Write,
) -> Result<()> {
    let mut digest = ColumnDigest::new();
    for ordinal in 0..store.len() {
        let address = match store.written_address(ordinal) {
            Some((part, start, length)) => PyValue::Tuple(vec![
                PyValue::str(part),
                PyValue::Int(start as i64),
                PyValue::Int(i64::from(length)),
            ]),
            None => PyValue::None,
        };
        let value = PyValue::Tuple(vec![
            address,
            PyValue::Int(i64::from(store.written_inflated(ordinal))),
            PyValue::optional_str(store.config_note(ordinal)),
            PyValue::optional_str(store.policy_file(ordinal)),
        ]);
        if units.contains(&ordinal) {
            stage_line(out, &format!("{UNIT_PREFIX}{ordinal}"), &value)?;
        }
        digest.add(&value);
    }
    digest.write(out, "value")?;
    for (place, (entry, meta)) in routed.classes.iter().zip(&written.metas).enumerate() {
        stage_line(
            out,
            &format!("{CLASS_PREFIX}{place}"),
            &PyValue::Tuple(vec![PyValue::str(&entry.id), meta.clone()]),
        )?;
    }
    stage_line(out, UNITS_FILES_KEY, &files_held(&corpus.join("units"))?)?;
    stage_line(
        out,
        FONTS_KEY,
        &PyValue::Dict(
            written
                .fonts
                .iter()
                .map(|(side, record)| (PyValue::str(side), record.clone()))
                .collect(),
        ),
    )?;
    stage_line(out, FONTS_FILES_KEY, &files_held(&corpus.join("fonts"))?)?;
    stage_line(out, COUNTERS_KEY, &written.counters.to_py())?;
    stage_line(out, SIDECARS_KEY, &sidecars_value(&written.runs)?)?;
    stage_line(out, FACTS_KEY, &written.check.facts().to_py())
}

/// A directory the stage makes beside its file and removes when it is dropped.
pub(crate) struct Scratch(pub(crate) PathBuf);

impl Scratch {
    pub(crate) fn new(stage_file: &Path, what: &str) -> Result<Self> {
        let directory = stage_file.with_file_name(format!(".write-{what}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&directory);
        fs::create_dir_all(&directory)
            .map_err(|why| WireError(format!("cannot make {}: {why}", directory.display())))?;
        Ok(Self(directory))
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// A generated case's input: the files its directory holds first, by relative path, and the arguments of its call.
pub(crate) type CaseInput<'v> = (&'v [(PyValue, PyValue)], &'v [PyValue]);

/// A generated case's input read ([`CaseInput`]).
pub(crate) fn case_input<'v>(key: &str, input: &'v PyValue) -> Result<CaseInput<'v>> {
    let PyValue::Tuple(items) = input else {
        return error(format!("{key}: the input is not a tuple"));
    };
    let [PyValue::Dict(held), rest @ ..] = items.as_slice() else {
        return error(format!(
            "{key}: the input does not start with the files held"
        ));
    };
    Ok((held, rest))
}

/// Write the files a generated case's directory holds first.
pub(crate) fn hold(directory: &Path, held: &[(PyValue, PyValue)], key: &str) -> Result<()> {
    for (name, data) in held {
        let (PyValue::Str(name), PyValue::Bytes(data)) = (name, data) else {
            return error(format!("{key}: a file held is not a str named bytes"));
        };
        let path = directory.join(name);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|why| WireError(format!("{key}: {why}")))?;
        }
        fs::write(&path, data).map_err(|why| WireError(format!("{key}: {why}")))?;
    }
    Ok(())
}

/// The counts lines a generated case's calls appended.
fn counts_of(counts: &Path, key: &str) -> Result<PyValue> {
    fs::read(counts)
        .map(PyValue::Bytes)
        .map_err(|why| WireError(format!("{key}: cannot read the counts: {why}")))
}

/// Answer one generated line of the `write` stage: `copy_static` from the input's static files into a directory holding the case's files, called as often as the case says, valued what its last call returned, the counts lines its calls appended and the files the directory holds then; or `copy_font` of a font against a digest into a corpus holding the case's files, as often as the case says until it refuses, valued the refusal's message or None, the counts lines and the files `fonts/` holds then.
fn answer_generated(
    input: &BuildInput,
    stage_file: &Path,
    key: &str,
    line_input: &PyValue,
) -> Result<PyValue> {
    let scratch = Scratch::new(stage_file, "case")?;
    let corpus = scratch.0.join("out");
    let counts = scratch.0.join("counts");
    fs::create_dir_all(&corpus).map_err(|why| WireError(format!("{key}: {why}")))?;
    fs::write(&counts, b"").map_err(|why| WireError(format!("{key}: {why}")))?;
    let sink = Sink::new(Some(counts.clone()));
    let (held, rest) = case_input(key, line_input)?;
    hold(&corpus, held, key)?;
    if key.starts_with(STATIC_GENERATED) {
        let [PyValue::Int(times)] = rest else {
            return error(format!(
                "{key}: the input does not end with how often to copy"
            ));
        };
        let mut copied = Vec::new();
        for _ in 0..*times {
            copied = copy_static(&sink, &corpus, &input.static_files, &input.fallback_index)
                .map_err(|why| WireError(format!("{key}: {why}")))?;
        }
        return Ok(PyValue::Tuple(vec![
            PyValue::List(copied.into_iter().map(PyValue::Str).collect()),
            counts_of(&counts, key)?,
            files_held(&corpus)?,
        ]));
    }
    if key.starts_with(FONT_GENERATED) {
        let [
            PyValue::Str(source),
            PyValue::Str(name),
            PyValue::Str(family),
            PyValue::Str(sha256),
            PyValue::Int(times),
        ] = rest
        else {
            return error(format!(
                "{key}: the input does not end with a font's source, name, family and digest and how often to copy it"
            ));
        };
        let copy = FontCopy {
            source: source.clone(),
            name: name.clone(),
            family: family.clone(),
            sha256: sha256.clone(),
        };
        let mut refusal = PyValue::None;
        for _ in 0..*times {
            match copy_font(&sink, &corpus, &copy) {
                Ok(_) => {}
                Err(BuildError::Exit(message)) => {
                    refusal = PyValue::Str(message);
                    break;
                }
                Err(failure) => return error(format!("{key}: {failure}")),
            }
        }
        return Ok(PyValue::Tuple(vec![
            refusal,
            counts_of(&counts, key)?,
            files_held(&corpus.join("fonts"))?,
        ]));
    }
    error(format!("{key}: not a line the write stage answers"))
}

/// The `write` stage: when the stage file holds lines from the build's run, run the build through the write up to the manifest ([`through_write`]) at the write width `write_threads` gives or else the input's, and answer with the lines `write_lines` writes; then answer each generated line ([`answer_generated`]).
pub fn stage_write(
    input_path: &Path,
    write_threads: Option<i64>,
    stage_file: &Path,
    out: &mut impl Write,
) -> Result<()> {
    let mut input = BuildInput::read(input_path)?;
    if write_threads.is_some() {
        input.widths.write_threads = write_threads;
    }
    let lines = stage_lines(stage_file)?;
    if lines.ran {
        through_write(&input, stage_file, "write", |held| {
            write_lines(
                &held.run.corpus,
                &held.run.planned.store,
                held.routed,
                held.written,
                &lines.units,
                out,
            )
        })?;
    }
    for (key, key_input) in &lines.generated {
        let value = answer_generated(&input, stage_file, key, key_input)?;
        stage_line(out, key, &value)?;
    }
    Ok(())
}

/// What a stage holds once the build has run through the write's commit and the fonts' copies ([`through_write`]): the build through phase 1, the triage and the passes after it, the classes as routed, and what the write left.
pub(crate) struct WriteHeld<'h, 'a> {
    pub(crate) run: &'h UnitsRun,
    pub(crate) triage: &'h Triage,
    pub(crate) passes: &'h Passes,
    pub(crate) routed: &'h Routed,
    pub(crate) written: &'h Written<'a>,
}

/// Run the build through phase 1 ([`through_units`]), then [`triage`], [`passes`] and [`route_classes`] over the corpus directory the build ran in, at the input's write width ([`write_fit`]), then the write up to the manifest ([`write_corpus`]), its spill files beside that directory, and hand what the build holds then to `then`.
pub(crate) fn through_write<T>(
    input: &BuildInput,
    stage_file: &Path,
    stage: &str,
    then: impl FnOnce(WriteHeld<'_, '_>) -> Result<T>,
) -> Result<T> {
    let names = WriteNames::of(input)?;
    through_units(input, stage_file, stage, |run| {
        let failure = |failure: BuildError| WireError(format!("the write failed: {failure}"));
        let emitter = Emitter::open(input).map_err(failure)?;
        let settings = CheckSettings::of(input).map_err(|why| WireError(why.to_string()))?;
        let triage = triage(
            input,
            &run.classes_present,
            &mut run.table,
            &mut run.planned.store,
        )
        .map_err(failure)?;
        let passes = passes(
            &mut run.table,
            &mut run.planned.store,
            &triage,
            &run.drive,
            &run.planned.verify_units,
        )
        .map_err(failure)?;
        let routed = route_classes(
            &run.corpus,
            &run.table,
            &run.planned.store,
            &triage.classes,
            &triage.sections,
            &passes.by_class,
            run.planned.prior_shards.as_ref(),
            run.planned.cached,
            write_fit(&input.widths),
            &names.route,
        )
        .map_err(failure)?;
        let scratch = run
            .corpus
            .parent()
            .map_or_else(|| PathBuf::from("."), Path::to_path_buf);
        let written = write_corpus(
            input,
            &run.corpus,
            &scratch,
            &run.table,
            &mut run.planned.store,
            &routed,
            run.drive.arena(),
            &emitter,
            &settings,
            &names,
            &Sink::new(None),
        )
        .map_err(failure)?;
        then(WriteHeld {
            run,
            triage: &triage,
            passes: &passes,
            routed: &routed,
            written: &written,
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let directory =
            std::env::temp_dir().join(format!("ams-review-write-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&directory);
        fs::create_dir_all(&directory).expect("scratch");
        directory
    }

    #[test]
    fn the_static_copy_adds_the_fallback_page_and_keeps_what_already_holds_its_bytes() {
        let directory = scratch("static");
        let source = directory.join("app.js");
        fs::write(&source, b"app\n").expect("source");
        let out = directory.join("out");
        let counts = directory.join("counts");
        fs::write(&counts, b"").expect("counts");
        let sink = Sink::new(Some(counts.clone()));
        let files = [("app.js".to_owned(), source.to_string_lossy().into_owned())];
        for _ in 0..2 {
            assert_eq!(
                copy_static(&sink, &out, &files, "<p>fallback</p>\n").expect("copied"),
                vec!["app.js".to_owned(), "index.html".to_owned()]
            );
        }
        assert_eq!(
            fs::read_to_string(&counts).expect("counts"),
            "changed\t4\nchanged\t16\nkept\t4\nkept\t16\n"
        );
        assert_eq!(
            fs::read(out.join("index.html")).expect("page"),
            b"<p>fallback</p>\n"
        );
        let _ = fs::remove_dir_all(&directory);
    }

    #[test]
    fn a_font_that_changed_since_the_load_is_refused_and_leaves_nothing_behind() {
        let directory = scratch("font");
        let source = directory.join("font.otf");
        fs::write(&source, b"font").expect("source");
        let copy = FontCopy {
            source: source.to_string_lossy().into_owned(),
            name: "before.otf".to_owned(),
            family: "B".to_owned(),
            sha256: "0".repeat(64),
        };
        let out = directory.join("out");
        let Err(BuildError::Exit(message)) = copy_font(&Sink::new(None), &out, &copy) else {
            panic!("the copy is refused");
        };
        assert!(message.starts_with(&format!(
            "{} changed between this build's load ({}) and its copy (",
            copy.source, copy.sha256
        )));
        assert!(!out.join("fonts").join("before.otf").exists());
        assert!(!out.join("fonts").join("before.otf.partial").exists());
        let _ = fs::remove_dir_all(&directory);
    }
}
