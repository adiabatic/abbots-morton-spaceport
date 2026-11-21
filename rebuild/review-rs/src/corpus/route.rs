//! How the write takes each class, decided before any byte is written, as `build._write_corpus` decides it once the whole-corpus passes have run: the previous corpus's parts for each class read from the manifest beside them ([`prior_parts`]), the classes in shard order with their units by id ([`ClassToWrite`]), each class's route ([`route`]) with each unit's prediction, each class's manifest record ([`class_meta`]), whether the previous sidecars are copied forward ([`copy_forward`]), and the runs the write hands out ([`write_width`], [`write_runs`]). [`route_classes`] runs them in the build's order.
//!
//! [`stage_route`] is the `route` stage of the stage fixtures.

use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;

use super::audit::stage_line;
use super::drive::{through_units, units_pool_width};
use super::gzip::{GzipError, GzipReader};
use super::input::{BuildInput, LedgerEntry, Widths};
use super::passes::{Passes, Triage, passes, triage};
use super::rowdelta::py_str;
use super::scan::{ScanError, ScanNames, index_is_current, manifest_and_identity, stage_lines};
use super::signatures::{dump_prior, get, int_equal, is_str};
use super::store::UnitStore;
use super::table::{ColumnDigest, UNIT_PREFIX, UnitTable, int};
use super::{BuildError, PriorShards, failed};
use crate::fragment::py_eq;
use crate::pyfmt::{PyValue, json};
use crate::wire::{Result, WireError, error};

/// How the write writes one class (`build.APPEND`, `DIRECT`, `FOLD`, `FIRST`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Route {
    /// Appended to its parts from the previous corpus.
    Append,
    /// Written directly by the share rule.
    Direct,
    /// Written directly as a fold, its dead bytes having reached its live bytes.
    Fold,
    /// Written directly as a first base, plain: the class has no parts in the previous corpus, or one of them is gone.
    First,
}

impl Route {
    /// The route's name, as `build` spells it.
    pub fn name(self) -> &'static str {
        match self {
            Self::Append => "append",
            Self::Direct => "direct",
            Self::Fold => "fold",
            Self::First => "first",
        }
    }
}

/// The vocabulary the routing reads: the bytes that close a shard part (`unit_index.PART_CLOSING`), the machine checks in their order (`audit.MACHINE_CHECKS`), the app index's and the locator's names and formats and the locator rows' name (`app_index`), and the names the units index and the manifest's identity are read by.
#[derive(Clone, Debug)]
pub struct RouteNames {
    pub part_closing: Vec<u8>,
    pub machine_checks: Vec<String>,
    pub app_index_name: String,
    pub app_index_format: String,
    pub locator_name: String,
    pub locator_format: String,
    pub locator_rows_name: String,
    pub scan: ScanNames,
}

impl RouteNames {
    /// The names as the build input's vocabulary gives them.
    pub fn of(input: &BuildInput) -> Result<Self> {
        let constant = |dotted: &str| {
            input
                .constant(dotted)
                .ok_or_else(|| WireError(format!("the build input's vocabulary holds no {dotted}")))
        };
        let word = |dotted: &str| match constant(dotted)? {
            PyValue::Str(value) => Ok(value.clone()),
            other => error(format!("{dotted} is {}, not a str", other.kind())),
        };
        let machine_checks = match constant("rebuild.review.audit.MACHINE_CHECKS")? {
            PyValue::Tuple(values) | PyValue::List(values) => values
                .iter()
                .map(|value| match value {
                    PyValue::Str(value) => Ok(value.clone()),
                    other => error(format!("MACHINE_CHECKS holds {}, not a str", other.kind())),
                })
                .collect::<Result<Vec<_>>>()?,
            other => return error(format!("MACHINE_CHECKS is {}, not a tuple", other.kind())),
        };
        Ok(Self {
            part_closing: match constant("rebuild.review.unit_index.PART_CLOSING")? {
                PyValue::Bytes(value) => value.clone(),
                other => return error(format!("PART_CLOSING is {}, not bytes", other.kind())),
            },
            machine_checks,
            app_index_name: word("rebuild.review.app_index.APP_INDEX_NAME")?,
            app_index_format: word("rebuild.review.app_index.APP_INDEX_FORMAT")?,
            locator_name: word("rebuild.review.app_index.LOCATOR_NAME")?,
            locator_format: word("rebuild.review.app_index.LOCATOR_FORMAT")?,
            locator_rows_name: word("rebuild.review.app_index.LOCATOR_ROWS_NAME")?,
            scan: ScanNames::of(input)?,
        })
    }
}

fn scan_failure(failure: ScanError) -> BuildError {
    match failure {
        ScanError::Unheld(why) => failed(format!("a value the reader does not hold: {why}")),
        other => failed(other),
    }
}

/// The previous corpus's parts by class, in the manifest's order, each class's key as the manifest gives it.
pub type PriorParts = Vec<(PyValue, Vec<String>)>;

/// `build._prior_parts` over the manifest's bytes, None for a corpus with no manifest that reads: each class entry's id with its parts (`unit_index.class_shards`), a repeated id keeping its first place with its last parts. A manifest that is not UTF-8, does not parse, or whose classes Python cannot read as `_prior_parts` reads them (an `OSError`, `ValueError`, `KeyError`, `TypeError` or `AttributeError`) gives none; one holding what the JSON reader does not hold fails.
pub fn prior_parts_of(raw: Option<&[u8]>) -> std::result::Result<PriorParts, BuildError> {
    let Some(text) = raw.and_then(|raw| std::str::from_utf8(raw).ok()) else {
        return Ok(Vec::new());
    };
    let document = match json::loads(text) {
        Ok(document) => document,
        Err(failure) if json::unheld(&failure) => {
            return Err(failed(format!(
                "a value the reader does not hold: {}",
                failure.0
            )));
        }
        Err(_) => return Ok(Vec::new()),
    };
    let PyValue::Dict(manifest) = &document else {
        return Ok(Vec::new());
    };
    let Some(PyValue::List(classes)) = get(manifest, "classes") else {
        return Ok(Vec::new());
    };
    let mut parts: PriorParts = Vec::new();
    for meta in classes {
        let PyValue::Dict(entries) = meta else {
            return Ok(Vec::new());
        };
        let Some(id) = get(entries, "id") else {
            return Ok(Vec::new());
        };
        let shards = match get(entries, "shards") {
            None | Some(PyValue::None) => match get(entries, "shard") {
                Some(shard) => vec![py_str(shard)],
                None => return Ok(Vec::new()),
            },
            Some(PyValue::List(items)) => items.iter().map(py_str).collect(),
            Some(PyValue::Str(text)) => text.chars().map(String::from).collect(),
            Some(PyValue::Dict(keys)) => keys.iter().map(|(key, _)| py_str(key)).collect(),
            Some(_) => return Ok(Vec::new()),
        };
        if matches!(id, PyValue::List(_) | PyValue::Dict(_)) {
            return Ok(Vec::new());
        }
        match parts.iter_mut().find(|(held, _)| py_eq(held, id)) {
            Some(found) => found.1 = shards,
            None => parts.push((id.clone(), shards)),
        }
    }
    Ok(parts)
}

/// `build._prior_parts`: the previous corpus's parts by class, read from the manifest under `out_dir` ([`prior_parts_of`]); none when it does not read.
pub fn prior_parts(out_dir: &Path) -> std::result::Result<PriorParts, BuildError> {
    prior_parts_of(std::fs::read(out_dir.join("manifest.json")).ok().as_deref())
}

/// One class as the write receives it (`build._ClassToWrite`): the fields of its ledger entry or change group the manifest records, the section it was put in, its units' ordinals in shard order (by id word, which sorts like the ids), the previous corpus's parts for it and its route.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClassToWrite {
    pub id: String,
    pub title: String,
    pub summary: String,
    pub note: String,
    pub section: String,
    pub status: String,
    pub ink_identical: bool,
    pub no_verdict: bool,
    pub why: String,
    pub ordinals: Vec<u32>,
    pub prior_parts: Vec<String>,
    pub route: Route,
}

impl PriorShards {
    /// `PriorShards.dead_and_live`: the class's dead and live bytes over `parts`, a part being `[`, its slots each with the comma or bracket before it, and `closing`, so what no live fragment takes is dead.
    pub fn dead_and_live(&self, parts: &[String], closing: i64) -> (i64, i64) {
        let live: i64 = parts
            .iter()
            .map(|part| self.live.get(part).copied().unwrap_or(0))
            .sum();
        let held: i64 = parts
            .iter()
            .map(|part| self.sizes.get(part).copied().unwrap_or(0) - closing)
            .sum();
        (held - live, live)
    }
}

/// `build._route`: how the write writes one class, before any of its bytes are written. Each unit is predicted unchanged or changed from what the store holds ([`UnitStore::predicted_unchanged`], which sets its place in `predicted`), weighed at its plain shard bytes with its comma. A class with no prior parts is a first base; one whose dead bytes on disk reach its live bytes is a fold; otherwise the share rule writes it directly when its changed bytes pass half its live bytes, and appends to it otherwise.
#[allow(clippy::too_many_arguments)]
pub fn route(
    entry_id: &str,
    ordinals: &[u32],
    prior_parts: &[String],
    table: &UnitTable,
    store: &UnitStore,
    prior_shards: Option<&PriorShards>,
    closing: i64,
    predicted: &mut [bool],
) -> Route {
    let mut live = 0i64;
    let mut changed = 0i64;
    for &ordinal in ordinals {
        let ordinal = ordinal as usize;
        let weight = i64::from(store.shard_length(ordinal)) + 1;
        live += weight;
        if store.predicted_unchanged(
            ordinal,
            entry_id,
            table.duplicate_group(ordinal),
            table.exemplar(ordinal),
            table.no_verdict(ordinal),
        ) {
            predicted[ordinal] = true;
        } else {
            changed += weight;
        }
    }
    if prior_parts.is_empty() {
        return Route::First;
    }
    if let Some(prior_shards) = prior_shards {
        let (dead, held) = prior_shards.dead_and_live(prior_parts, closing);
        if dead > 0 && dead >= held {
            return Route::Fold;
        }
    }
    if 2 * changed > live {
        Route::Direct
    } else {
        Route::Append
    }
}

/// `build._class_meta`: a class's manifest record, its `shards` left empty for the write to fill, from the table's and the store's columns over every unit of the class: its ledger fields, a `note` of None for an empty one, its unit and row counts, how many units a machine check approves and how many each check approves, in `machine_checks` order, and the batches its units fall in, sorted.
pub fn class_meta(
    entry: &ClassToWrite,
    table: &UnitTable,
    store: &UnitStore,
    machine_checks: &[String],
) -> std::result::Result<PyValue, BuildError> {
    let mut checks: Vec<(String, i64)> = machine_checks
        .iter()
        .map(|check| (check.clone(), 0))
        .collect();
    let mut rows = 0i64;
    let mut batches: Vec<u32> = Vec::new();
    for &ordinal in &entry.ordinals {
        let ordinal = ordinal as usize;
        if let Some(check) = store.machine_check(ordinal) {
            let Some(count) = checks.iter_mut().find(|(held, _)| held == check) else {
                return Err(failed(format!("KeyError: '{check}'")));
            };
            count.1 += 1;
        }
        rows += i64::from(table.row_count(ordinal));
        batches.extend(table.batch(ordinal));
    }
    batches.sort_unstable();
    batches.dedup();
    let approved = checks.iter().map(|(_, count)| count).sum();
    let text = |value: &str| PyValue::str(value);
    Ok(PyValue::Dict(vec![
        (text("id"), text(&entry.id)),
        (text("title"), text(&entry.title)),
        (text("summary"), text(&entry.summary)),
        (
            text("note"),
            PyValue::optional_str(Some(entry.note.as_str()).filter(|note| !note.is_empty())),
        ),
        (text("section"), text(&entry.section)),
        (text("status"), text(&entry.status)),
        (text("ink_identical"), PyValue::Bool(entry.ink_identical)),
        (text("no_verdict"), PyValue::Bool(entry.no_verdict)),
        (text("why"), text(&entry.why)),
        (
            text("unit_count"),
            PyValue::Int(entry.ordinals.len() as i64),
        ),
        (text("row_count"), PyValue::Int(rows)),
        (text("machine_approved_count"), PyValue::Int(approved)),
        (
            text("machine_checks"),
            PyValue::Dict(
                checks
                    .into_iter()
                    .map(|(check, count)| (PyValue::Str(check), PyValue::Int(count)))
                    .collect(),
            ),
        ),
        (text("shards"), PyValue::List(Vec::new())),
        (
            text("batches"),
            PyValue::List(batches.into_iter().map(int).collect()),
        ),
    ]))
}

/// `build._write_runs` over the classes' unit counts, in shard order: contiguous stretches of them, each closed before the class that would take it past an even share of the units (the unit total over `width`, at least one, rounded up), a class larger than that share a run of its own; at least one run, empty when there are no classes. Each run holds its classes' places.
pub fn write_runs(sizes: &[usize], width: i64) -> Vec<Vec<usize>> {
    let total: u64 = sizes.iter().map(|&size| size as u64).sum();
    let share = total.div_ceil(width.max(1) as u64);
    let mut runs: Vec<Vec<usize>> = Vec::new();
    let mut current: Vec<usize> = Vec::new();
    let mut held = 0u64;
    for (place, &size) in sizes.iter().enumerate() {
        if !current.is_empty() && held + size as u64 > share {
            runs.push(std::mem::take(&mut current));
            held = 0;
        }
        current.push(place);
        held += size as u64;
    }
    if !current.is_empty() || runs.is_empty() {
        runs.push(current);
    }
    runs
}

/// `build._write_width`: how many threads the write runs on, the most that fit (`fit`) but no more than the unit total over the largest class's units, rounded up, and at least one.
pub fn write_width(sizes: &[usize], fit: i64) -> usize {
    let largest = sizes.iter().copied().max().unwrap_or(0) as u64;
    let bound = if largest == 0 {
        1
    } else {
        sizes
            .iter()
            .map(|&size| size as u64)
            .sum::<u64>()
            .div_ceil(largest)
    };
    fit.min(i64::try_from(bound).unwrap_or(i64::MAX)).max(1) as usize
}

/// The most threads the write may take, before `write_width` narrows it: the input's write width, or the units pool's width as the write starts, the gates-idle marker read again.
pub fn write_fit(widths: &Widths) -> i64 {
    widths
        .write_threads
        .unwrap_or_else(|| units_pool_width(widths).0 as i64)
}

/// `app_index.artifact_header`: a sidecar's header line, the first line of its gzip stream read as UTF-8 text with universal newlines, when it is an object; None when the file is missing, is not a regular file, does not open, ends, is not gzip, or does not decode or parse. Deflate data that does not inflate fails, as `zlib.error` passes the `except`.
fn artifact_header(
    corpus: &Path,
    name: &str,
) -> std::result::Result<Option<Vec<(PyValue, PyValue)>>, BuildError> {
    let path = corpus.join(name);
    if !path.is_file() {
        return Ok(None);
    }
    let Ok(file) = File::open(&path) else {
        return Ok(None);
    };
    let mut line = Vec::new();
    match BufReader::new(GzipReader::new(file)).read_until(b'\n', &mut line) {
        Ok(0) => return Ok(None),
        Ok(_) => {}
        Err(failure) => {
            return match GzipError::of(&failure) {
                Some(GzipError::Zlib(why)) => Err(failed(format!("error: {why}"))),
                _ => Ok(None),
            };
        }
    }
    if let Some(end) = line.iter().position(|&byte| byte == b'\r') {
        line.truncate(end);
    }
    let Ok(text) = std::str::from_utf8(&line) else {
        return Ok(None);
    };
    match json::loads(text) {
        Ok(PyValue::Dict(record)) => Ok(Some(record)),
        Ok(_) => Ok(None),
        Err(failure) if json::unheld(&failure) => Err(failed(format!(
            "a value the reader does not hold: {}",
            failure.0
        ))),
        Err(_) => Ok(None),
    }
}

/// `app_index.artifact_is_current`: whether the sidecar `name` under `corpus` matches the manifest beside it: present, in format `format`, and stamped with the manifest's identity; for the locator, the rows file's size is also the `rows_bytes` its header records.
pub fn artifact_is_current(
    corpus: &Path,
    name: &str,
    format: &str,
    names: &RouteNames,
) -> std::result::Result<bool, BuildError> {
    let Some(record) = artifact_header(corpus, name)? else {
        return Ok(false);
    };
    if !is_str(get(&record, "format"), format) {
        return Ok(false);
    }
    let Some((_, identity)) = manifest_and_identity(corpus, &names.scan).map_err(scan_failure)?
    else {
        return Ok(false);
    };
    if !is_str(get(&record, "manifest_sha256"), &identity) {
        return Ok(false);
    }
    if name == names.locator_name {
        let Ok(metadata) = std::fs::metadata(corpus.join(&names.locator_rows_name)) else {
            return Ok(false);
        };
        let Ok(size) = i64::try_from(metadata.len()) else {
            return Ok(false);
        };
        return Ok(get(&record, "rows_bytes").is_some_and(|held| int_equal(held, size)));
    }
    Ok(true)
}

/// Whether the write copies the previous sidecars forward: when the cache supplied any unit, the units index ([`index_is_current`]) and the app index ([`artifact_is_current`]) are both stamped for the manifest beside them.
pub fn copy_forward(
    corpus: &Path,
    cached: usize,
    names: &RouteNames,
) -> std::result::Result<bool, BuildError> {
    Ok(cached > 0
        && index_is_current(corpus, &names.scan).map_err(scan_failure)?
        && artifact_is_current(
            corpus,
            &names.app_index_name,
            &names.app_index_format,
            names,
        )?)
}

/// What [`route_classes`] gives the write: the classes in shard order with their routes, each unit's prediction, whether the previous sidecars are copied forward, the width the runs were cut at, and the runs, each its classes' places in shard order.
#[derive(Clone, Debug)]
pub struct Routed {
    pub classes: Vec<ClassToWrite>,
    pub predicted: Vec<bool>,
    pub copy_forward: bool,
    pub width: usize,
    pub runs: Vec<Vec<usize>>,
}

/// The write's decisions before it writes, as `build._write_corpus` makes them: the classes of `classes`, in manifest order, sorted into shard order (by `<id>.json`), each with its section, its units in the passes' rows sorted by id word, the previous corpus's parts for it, none when one of them is not a file under `out_dir`, and its route ([`route`]); whether the sidecars are copied forward ([`copy_forward`]) when the cache supplied `cached` units; and the runs, cut at the width `fit` narrows to ([`write_width`], [`write_runs`]).
#[allow(clippy::too_many_arguments)]
pub fn route_classes(
    out_dir: &Path,
    table: &UnitTable,
    store: &UnitStore,
    classes: &[LedgerEntry],
    sections: &[(String, String)],
    by_class: &[(String, Vec<u32>)],
    prior_shards: Option<&PriorShards>,
    cached: usize,
    fit: i64,
    names: &RouteNames,
) -> std::result::Result<Routed, BuildError> {
    let mut ordered: Vec<&LedgerEntry> = classes.iter().collect();
    ordered.sort_by_cached_key(|entry| format!("{}.json", entry.id));
    let prior = prior_parts(out_dir)?;
    let closing = names.part_closing.len() as i64;
    let mut predicted = vec![false; table.n];
    let mut to_write = Vec::with_capacity(ordered.len());
    for entry in ordered {
        let mut ordinals: Vec<u32> = by_class
            .iter()
            .find(|(class_id, _)| *class_id == entry.id)
            .map(|(_, members)| members.clone())
            .unwrap_or_default();
        ordinals.sort_by_key(|&ordinal| store.id_word(ordinal as usize));
        let mut parts: Vec<String> = prior
            .iter()
            .find(|(key, _)| matches!(key, PyValue::Str(key) if *key == entry.id))
            .map(|(_, parts)| parts.clone())
            .unwrap_or_default();
        if !parts.iter().all(|part| out_dir.join(part).is_file()) {
            parts.clear();
        }
        let section = sections
            .iter()
            .find(|(class_id, _)| *class_id == entry.id)
            .map(|(_, section)| section.clone())
            .ok_or_else(|| failed(format!("KeyError: '{}'", entry.id)))?;
        let route = route(
            &entry.id,
            &ordinals,
            &parts,
            table,
            store,
            prior_shards,
            closing,
            &mut predicted,
        );
        to_write.push(ClassToWrite {
            id: entry.id.clone(),
            title: entry.title.clone(),
            summary: entry.summary.clone(),
            note: entry.note.clone(),
            section,
            status: entry.status.clone(),
            ink_identical: entry.ink_identical,
            no_verdict: entry.no_verdict,
            why: entry.why.clone(),
            ordinals,
            prior_parts: parts,
            route,
        });
    }
    let copy_forward = copy_forward(out_dir, cached, names)?;
    let sizes: Vec<usize> = to_write.iter().map(|entry| entry.ordinals.len()).collect();
    let width = write_width(&sizes, fit);
    let runs = write_runs(&sizes, width as i64);
    Ok(Routed {
        classes: to_write,
        predicted,
        copy_forward,
        width,
        runs,
    })
}

/// The keys of the `route` stage's lines beside its units (`units_stage_fixtures.ROUTE_*`).
const CLASS_PREFIX: &str = "class:";
const ORDINALS_COLUMN: &str = "class-ordinals";
const RUNS_KEY: &str = "runs";
const COPY_FORWARD_KEY: &str = "copy-forward";
const RUNS_GENERATED: &str = "generated:runs:";
const PRIOR_PARTS_GENERATED: &str = "generated:prior-parts:";
const CURRENT_GENERATED: &str = "generated:current";

fn runs_value(runs: &[Vec<usize>]) -> PyValue {
    PyValue::List(
        runs.iter()
            .map(|run| {
                PyValue::List(
                    run.iter()
                        .map(|&place| PyValue::Int(place as i64))
                        .collect(),
                )
            })
            .collect(),
    )
}

/// Write the `route` stage's run lines, as the Python stage's `route_boundary` lays them out: a `unit:<ordinal>` line for each ordinal in `units`, whether the unit's fragment is predicted unchanged, and the column over every unit; a `class:<place>` line a class in shard order, its id, route, prior parts and manifest record; the column over each class's id and ordinals; the width and the runs; and whether the sidecars are copied forward.
fn route_lines(
    table: &UnitTable,
    store: &UnitStore,
    routed: &Routed,
    names: &RouteNames,
    units: &std::collections::HashSet<usize>,
    out: &mut impl Write,
) -> Result<()> {
    let mut digest = ColumnDigest::new();
    for (ordinal, &predicted) in routed.predicted.iter().enumerate() {
        let value = PyValue::Bool(predicted);
        if units.contains(&ordinal) {
            stage_line(out, &format!("{UNIT_PREFIX}{ordinal}"), &value)?;
        }
        digest.add(&value);
    }
    digest.write(out, "value")?;
    for (place, entry) in routed.classes.iter().enumerate() {
        let meta = class_meta(entry, table, store, &names.machine_checks)
            .map_err(|failure| WireError(format!("the class record failed: {failure}")))?;
        stage_line(
            out,
            &format!("{CLASS_PREFIX}{place}"),
            &PyValue::Tuple(vec![
                PyValue::str(&entry.id),
                PyValue::str(entry.route.name()),
                PyValue::str_tuple(&entry.prior_parts),
                meta,
            ]),
        )?;
    }
    let mut ordinals = ColumnDigest::new();
    for entry in &routed.classes {
        ordinals.add(&PyValue::Tuple(vec![
            PyValue::str(&entry.id),
            PyValue::Tuple(entry.ordinals.iter().copied().map(int).collect()),
        ]));
    }
    ordinals.write(out, ORDINALS_COLUMN)?;
    stage_line(
        out,
        RUNS_KEY,
        &PyValue::Tuple(vec![
            PyValue::Int(routed.width as i64),
            runs_value(&routed.runs),
        ]),
    )?;
    stage_line(out, COPY_FORWARD_KEY, &PyValue::Bool(routed.copy_forward))
}

/// A generated `runs` line's input, `(sizes, fit)`, read.
fn runs_input(input: &PyValue) -> Option<(Vec<usize>, i64)> {
    let PyValue::Tuple(items) = input else {
        return None;
    };
    let [PyValue::Tuple(sizes), PyValue::Int(fit)] = items.as_slice() else {
        return None;
    };
    let sizes = sizes
        .iter()
        .map(|size| match size {
            PyValue::Int(size) => usize::try_from(*size).ok(),
            _ => None,
        })
        .collect::<Option<Vec<_>>>()?;
    Some((sizes, *fit))
}

/// Answer one generated line of the `route` stage from its key and input: the width and the runs over class sizes, the prior parts of a manifest's bytes, or whether the dump's prior corpus holds a current units index, app index and locator.
fn answer_generated(
    key: &str,
    input: &PyValue,
    stage_file: &Path,
    names: &RouteNames,
) -> Result<PyValue> {
    let fail = |failure: BuildError| WireError(format!("{key}: {failure}"));
    if key.starts_with(RUNS_GENERATED) {
        let Some((sizes, fit)) = runs_input(input) else {
            return error(format!("{key}: the input is not (sizes, fit)"));
        };
        return Ok(PyValue::Tuple(vec![
            PyValue::Int(write_width(&sizes, fit) as i64),
            runs_value(&write_runs(&sizes, fit)),
        ]));
    }
    if key.starts_with(PRIOR_PARTS_GENERATED) {
        let raw = match input {
            PyValue::Bytes(raw) => Some(raw.as_slice()),
            PyValue::None => None,
            other => return error(format!("{key}: the input is {}, not bytes", other.kind())),
        };
        let parts = prior_parts_of(raw).map_err(fail)?;
        return Ok(PyValue::List(
            parts
                .into_iter()
                .map(|(id, parts)| {
                    PyValue::Tuple(vec![
                        id,
                        PyValue::List(parts.into_iter().map(PyValue::Str).collect()),
                    ])
                })
                .collect(),
        ));
    }
    if key == CURRENT_GENERATED {
        let prior = dump_prior(stage_file)?;
        let index =
            index_is_current(&prior, &names.scan).map_err(|failure| fail(scan_failure(failure)))?;
        let app = artifact_is_current(
            &prior,
            &names.app_index_name,
            &names.app_index_format,
            names,
        )
        .map_err(fail)?;
        let locator =
            artifact_is_current(&prior, &names.locator_name, &names.locator_format, names)
                .map_err(fail)?;
        return Ok(PyValue::Tuple(vec![
            PyValue::Bool(index),
            PyValue::Bool(app),
            PyValue::Bool(locator),
        ]));
    }
    error(format!("{key}: not a line the route stage answers"))
}

/// The `route` stage: when the stage file holds lines from the build's run, run the build through phase 1 ([`through_units`]), then [`triage`] and [`passes`], then [`route_classes`] over the corpus directory the build ran in, at the write width the input gives ([`write_fit`]), and answer with the lines `route_lines` writes; then answer each generated line ([`answer_generated`]).
pub fn stage_route(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let names = RouteNames::of(&input)?;
    let lines = stage_lines(stage_file)?;
    if lines.ran {
        through_units(&input, stage_file, "route", |run| {
            let failure = |failure: BuildError| WireError(format!("the routing failed: {failure}"));
            let triage: Triage = triage(
                &input,
                &run.classes_present,
                &mut run.table,
                &mut run.planned.store,
            )
            .map_err(failure)?;
            let passes: Passes = passes(
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
                &names,
            )
            .map_err(failure)?;
            route_lines(
                &run.table,
                &run.planned.store,
                &routed,
                &names,
                &lines.units,
                out,
            )
        })?;
    }
    for (key, key_input) in &lines.generated {
        let value = answer_generated(key, key_input, stage_file, &names)?;
        stage_line(out, key, &value)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_runs_close_before_a_class_that_passes_an_even_share() {
        assert_eq!(write_runs(&[], 4), vec![Vec::<usize>::new()]);
        assert_eq!(write_runs(&[0, 0], 2), vec![vec![0, 1]]);
        assert_eq!(write_runs(&[3, 3, 3], 3), vec![vec![0], vec![1], vec![2]]);
        assert_eq!(
            write_runs(&[10, 1, 1, 1, 1, 1], 2),
            vec![vec![0], vec![1, 2, 3, 4, 5]]
        );
        assert_eq!(write_runs(&[1, 1, 1, 10], 0), vec![vec![0, 1, 2, 3]]);
        assert_eq!(write_runs(&[4, 4, 4, 4], -1), vec![vec![0, 1, 2, 3]]);
    }

    #[test]
    fn the_width_is_bounded_by_the_largest_class_and_is_at_least_one() {
        assert_eq!(write_width(&[], 8), 1);
        assert_eq!(write_width(&[0, 0], 8), 1);
        assert_eq!(write_width(&[10, 1, 1, 1, 1, 1], 8), 2);
        assert_eq!(write_width(&[4, 4, 4, 4], 3), 3);
        assert_eq!(write_width(&[4, 4, 4, 4], 0), 1);
    }

    #[test]
    fn the_prior_parts_read_both_entry_forms_and_give_none_for_a_manifest_python_cannot_read() {
        let parts = |raw: &[u8]| prior_parts_of(Some(raw)).expect("read");
        assert_eq!(
            parts(br#"{"classes": [{"id": "a", "shards": ["x", "y"]}, {"id": "b", "shard": 5}, {"id": "a", "shards": null, "shard": "z"}]}"#),
            vec![
                (PyValue::str("a"), vec!["z".to_owned()]),
                (PyValue::str("b"), vec!["5".to_owned()]),
            ]
        );
        assert_eq!(
            parts(br#"{"classes": [{"id": 1, "shards": []}, {"id": true, "shards": "ab"}]}"#),
            vec![(PyValue::Int(1), vec!["a".to_owned(), "b".to_owned()])]
        );
        assert!(parts(br#"{"classes": [{"id": "a", "shards": 3}]}"#).is_empty());
        assert!(parts(br#"{"classes": [{"id": ["a"], "shards": []}]}"#).is_empty());
        assert!(parts(br#"{"classes": "ab"}"#).is_empty());
        assert!(parts(b"\xff").is_empty());
        assert!(prior_parts_of(None).expect("read").is_empty());
    }

    #[test]
    fn a_fold_takes_a_class_whose_dead_bytes_reach_its_live_bytes() {
        let shards = PriorShards {
            sizes: [("a".to_owned(), 102), ("b".to_owned(), 52)].into(),
            live: [("a".to_owned(), 40)].into(),
        };
        assert_eq!(
            shards.dead_and_live(&["a".to_owned(), "b".to_owned()], 2),
            (110, 40)
        );
        assert_eq!(shards.dead_and_live(&["c".to_owned()], 2), (-2, 0));
    }
}
