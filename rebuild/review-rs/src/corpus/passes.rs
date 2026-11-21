//! The build's whole-corpus passes between the units phase and the write, as `build._build_m1` runs them once phase 1 has loaded every unit. [`triage`] builds the store's id index, promotes each re-routed or unmatched unit to its change group, puts every class the units reach in its section ([`class_section`]) and the classes in manifest order with the change groups' classes after the ledger's ([`synthesize_unmatched_group_classes`]), orders the units for triage ([`sort_for_triage`]) and numbers the human units along that order into batches ([`assign_batches`]). [`passes`] runs the rest: each human unit's duplicate group ([`duplicate_group_id`]) and cluster, the classes' rows in triage order, the primary unit of every secondary junction ([`resolve_primary_unit_assignments`]), the verification of the sampled units against a recomputation and its stale check, and the re-settlement mismatches the write refuses; [`units_note`] is the units phase's line after them.
//!
//! [`stage_triage`] is the `triage` stage of the stage fixtures, and [`stage_passes`] the `passes` stage.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::io::Write;
use std::path::Path;

use super::audit::stage_line;
use super::columns::WindowIndex;
use super::drive::{Drive, through_units};
use super::input::{BuildInput, LedgerEntry};
use super::scan::stage_lines;
use super::signatures::SignatureArrays;
use super::store::{PrimaryUnitProjection, Target, UnitStore};
use super::table::{
    Buckets, ColumnDigest, UNIT_PREFIX, Unit, UnitTable, counting_permutation_by_id, family_ranks,
    int, stage_units,
};
use super::{BuildError, failed, thousands};
use crate::fragment::{base58_64, sha256_hex};
use crate::pyfmt::PyValue;
use crate::pyfmt::repr::repr;
use crate::unit::VerifyKey;
use crate::wire::{Injection, Result, WireError, error};

/// The vocabulary the passes read: the class of a unit no ledger ruling covers (`audit.UNMATCHED_CLASS`), the review app's sections in manifest order (`audit.SECTIONS`), the ledger's statuses (`audit.LEDGER_STATUSES`) and the ones that rule a class as a whole (`audit.RULED_STATUSES`).
#[derive(Clone, Debug)]
pub struct PassNames {
    pub unmatched: String,
    pub sections: Vec<String>,
    pub statuses: Vec<String>,
    pub ruled: Vec<String>,
}

impl PassNames {
    /// The names as the build input's vocabulary gives them.
    pub fn of(input: &BuildInput) -> Result<Self> {
        let constant = |dotted: &str| {
            input
                .constant(dotted)
                .ok_or_else(|| WireError(format!("the build input's vocabulary holds no {dotted}")))
        };
        let words = |dotted: &str| match constant(dotted)? {
            PyValue::Tuple(values) => values
                .iter()
                .map(|value| match value {
                    PyValue::Str(value) => Ok(value.clone()),
                    other => error(format!("{dotted} holds {}, not a str", other.kind())),
                })
                .collect::<Result<Vec<_>>>(),
            other => error(format!("{dotted} is {}, not a tuple", other.kind())),
        };
        let unmatched = match constant("rebuild.review.audit.UNMATCHED_CLASS")? {
            PyValue::Str(value) => value.clone(),
            other => return error(format!("UNMATCHED_CLASS is {}, not a str", other.kind())),
        };
        Ok(Self {
            unmatched,
            sections: words("rebuild.review.audit.SECTIONS")?,
            statuses: words("rebuild.review.audit.LEDGER_STATUSES")?,
            ruled: words("rebuild.review.audit.RULED_STATUSES")?,
        })
    }
}

/// The review app's section for a class (`audit.class_section`): the last section for a class that is `no_verdict` or whose every unit a machine check approves (`human_units` is zero), whatever its status; otherwise the second for a ruled status and the first for any other, None among them. A status the ledger does not have is Python's `ValueError`.
pub fn class_section(
    status: Option<&str>,
    no_verdict: bool,
    human_units: i64,
    names: &PassNames,
) -> std::result::Result<String, BuildError> {
    if let Some(status) = status
        && !names.statuses.iter().any(|held| held == status)
    {
        let quoted = repr(&PyValue::str(status)).map_err(failed)?;
        return Err(failed(format!(
            "ValueError: {quoted} is not a ledger status ({})",
            names.statuses.join(", ")
        )));
    }
    let section = if no_verdict || human_units == 0 {
        2
    } else if status.is_some_and(|status| names.ruled.iter().any(|held| held == status)) {
        1
    } else {
        0
    };
    names
        .sections
        .get(section)
        .cloned()
        .ok_or_else(|| failed(format!("SECTIONS holds no section {section}")))
}

/// The change groups present in the table's unmatched-group column as classes (`audit.synthesize_unmatched_group_classes`), in `group_order`, each `per-unit` with its title, summary and why from `group_prose` and no exemplar; a present group with no prose is Python's `KeyError`.
pub fn synthesize_unmatched_group_classes(
    table: &UnitTable,
    group_order: &[String],
    group_prose: &[(String, super::input::GroupProse)],
) -> std::result::Result<Vec<LedgerEntry>, BuildError> {
    let counts: HashSet<String> = table
        .unmatched_group_counts()
        .into_iter()
        .map(|(group, _count)| group)
        .collect();
    let mut classes = Vec::new();
    for group_id in group_order.iter().filter(|group| counts.contains(*group)) {
        let Some((_, prose)) = group_prose.iter().find(|(held, _)| held == group_id) else {
            let quoted = repr(&PyValue::str(group_id)).map_err(failed)?;
            return Err(failed(format!("KeyError: {quoted}")));
        };
        classes.push(LedgerEntry {
            id: group_id.clone(),
            status: "per-unit".to_owned(),
            why: prose.why.clone(),
            ink_identical: false,
            no_verdict: false,
            exemplar_keys: Vec::new(),
            title: prose.title.clone(),
            summary: prose.summary.clone(),
            note: String::new(),
        });
    }
    Ok(classes)
}

/// The triage order as a permutation of the table's rows (`audit.sort_for_triage`): `audit.triage_key` order, by each unit's final class through `class_order`, its group's families ranked by `family_of`, its window, and its id word in the store, by the counting sort over the table's window index.
pub fn sort_for_triage(
    table: &mut UnitTable,
    store: &UnitStore,
    class_order: &HashMap<String, usize>,
    family_of: &[(i64, String)],
) -> Vec<u32> {
    table.window_index();
    let index = table
        .held_window_index()
        .expect("the window index was built above");
    let family_rank = family_ranks(family_of);
    let buckets = Buckets {
        class_col: table.class_ids(),
        group_col: table.group_ids(),
        strings: &table.strings,
        class_order,
        family_rank: &family_rank,
    };
    counting_permutation_by_id(
        &buckets,
        &index.ordinals,
        table.n,
        index.runs(),
        |ordinal| store.id_word(ordinal as usize),
    )
}

/// Number the human units along `order` (`audit.assign_batches`): a unit no machine check approves, per the store, and whose ledger class does not exempt it, per the table, gets its place among the human units and the batch of `batch_size` that place falls in; every other unit gets neither. Returns the batch count.
pub fn assign_batches(
    table: &mut UnitTable,
    store: &UnitStore,
    order: &[u32],
    batch_size: u32,
) -> u32 {
    let mut index = 0u32;
    for &ordinal in order {
        let ordinal = ordinal as usize;
        if store.machine_approved(ordinal) || table.no_verdict(ordinal) {
            table.set_order_batch(ordinal, None, None);
        } else {
            table.set_order_batch(ordinal, Some(index), Some(index / batch_size));
            index += 1;
        }
    }
    index.div_ceil(batch_size)
}

/// The batch a triage-index place falls in, or None for a unit outside the index (`audit.batch_of`).
pub fn batch_of(order: Option<u32>, batch_size: u32) -> Option<u32> {
    order.map(|order| order / batch_size)
}

/// What [`triage`] gives the passes after it: the classes in manifest order, each class's section in the order the classes were met before the sort, the triage order, and the batch count.
#[derive(Clone, Debug)]
pub struct Triage {
    pub classes: Vec<LedgerEntry>,
    pub sections: Vec<(String, String)>,
    pub order: Vec<u32>,
    pub batches: u32,
}

/// The passes from the id index to the batches, as `build._build_m1` runs them once phase 1 has loaded every unit into `store`. The store's id index is built, refusing a repeated id. Each unit re-routed out of an `ink_identical` ledger class loses its exemplar flag, and it and each unmatched unit take their change group as their class and unmatched group; the units no machine check approves are counted by final class. The ledger classes the load reached (`classes_present`, places in the input's ledger) that a unit still holds, then the change groups' classes, are each put in their section and stably sorted by it into manifest order; the units are sorted for triage over that order and numbered into batches of the manifest's batch size.
pub fn triage(
    input: &BuildInput,
    classes_present: &[usize],
    table: &mut UnitTable,
    store: &mut UnitStore,
) -> std::result::Result<Triage, BuildError> {
    let names = PassNames::of(input)?;
    store.index().map_err(|failure| {
        if failure.class == "SystemExit" {
            BuildError::Exit(failure.message)
        } else {
            failed(failure)
        }
    })?;
    let mut human_units: HashMap<String, i64> = HashMap::new();
    for ordinal in 0..table.n {
        let ledger_class = store.ledger_class(ordinal);
        if !ledger_class.is_empty() {
            if ledger_class != table.class_id(ordinal) {
                return Err(failed(format!("AssertionError: {ordinal}")));
            }
            table.set_exemplar(ordinal, false);
        }
        if !ledger_class.is_empty() || table.class_id(ordinal) == names.unmatched {
            let group_id = store.change_group(ordinal);
            table.set_unmatched_group(ordinal, group_id);
            table.set_class(ordinal, group_id);
        }
        if !store.machine_approved(ordinal) {
            *human_units
                .entry(table.class_id(ordinal).to_owned())
                .or_insert(0) += 1;
        }
    }
    let present = table.classes_present();
    let mut classes: Vec<LedgerEntry> = Vec::new();
    for &place in classes_present {
        let entry = input
            .ledger
            .get(place)
            .ok_or_else(|| failed(format!("the ledger holds no class {place}")))?;
        if present.contains(entry.id.as_str()) {
            classes.push(entry.clone());
        }
    }
    classes.extend(synthesize_unmatched_group_classes(
        table,
        &input.group_order,
        &input.group_prose,
    )?);
    let mut sections: Vec<(String, String)> = Vec::new();
    let mut section_of: HashMap<String, usize> = HashMap::new();
    for entry in &classes {
        let section = class_section(
            Some(&entry.status),
            entry.no_verdict,
            human_units.get(&entry.id).copied().unwrap_or(0),
            &names,
        )?;
        match section_of.get(&entry.id) {
            Some(&at) => sections[at].1 = section,
            None => {
                section_of.insert(entry.id.clone(), sections.len());
                sections.push((entry.id.clone(), section));
            }
        }
    }
    let mut ranked = Vec::with_capacity(classes.len());
    for entry in classes {
        let section = &sections[section_of[&entry.id]].1;
        let rank = names
            .sections
            .iter()
            .position(|held| held == section)
            .ok_or_else(|| failed(format!("ValueError: {section} is not in SECTIONS")))?;
        ranked.push((rank, entry));
    }
    ranked.sort_by_key(|(rank, _entry)| *rank);
    let classes: Vec<LedgerEntry> = ranked.into_iter().map(|(_rank, entry)| entry).collect();
    let class_order: HashMap<String, usize> = classes
        .iter()
        .enumerate()
        .map(|(index, entry)| (entry.id.clone(), index))
        .collect();
    let order = sort_for_triage(table, store, &class_order, &input.family_of);
    let batch_size = u32::try_from(input.manifest.batch_size)
        .ok()
        .filter(|&size| size > 0)
        .ok_or_else(|| {
            failed(format!(
                "the batch size {} is not a positive int",
                input.manifest.batch_size
            ))
        })?;
    let batches = assign_batches(table, store, &order, batch_size);
    Ok(Triage {
        classes,
        sections,
        order,
        batches,
    })
}

/// A duplicate group's id (`unit_cache.duplicate_group_id_for` over `repr(key)`): `e-` and the base58 of the sha256 of the key's repr.
pub fn duplicate_group_id(key: &PyValue) -> std::result::Result<String, BuildError> {
    let text = repr(key).map_err(failed)?;
    Ok(format!(
        "e-{}",
        base58_64(&sha256_hex(text.as_bytes())).map_err(failed)?
    ))
}

/// The key a human unit's duplicate group is keyed on, as `build._build_m1` builds it: the unit's configs as the table gives them, a tuple; its judged pair as the code point values at the store's pair positions, or None; its final class; and its ink diffs' digest.
pub fn duplicate_group_key(
    table: &UnitTable,
    store: &UnitStore,
    ordinal: usize,
) -> std::result::Result<PyValue, BuildError> {
    let pair = match store.pair_codepoints(ordinal) {
        None => PyValue::None,
        Some((left, right)) => {
            let values = table.codepoints(ordinal);
            PyValue::Tuple(vec![
                PyValue::Int(i64::from(*at(&values, left)?)),
                PyValue::Int(i64::from(*at(&values, right)?)),
            ])
        }
    };
    Ok(PyValue::Tuple(vec![
        PyValue::Tuple(table.configs(ordinal).iter().map(PyValue::str).collect()),
        pair,
        PyValue::str(table.class_id(ordinal)),
        PyValue::str(store.diffs_digest(ordinal)),
    ]))
}

/// `items[index]` as Python indexes a sequence, a negative index counting from the end; out of range is Python's `IndexError`.
fn at<T>(items: &[T], index: i64) -> std::result::Result<&T, BuildError> {
    let length = items.len() as i64;
    let position = if index < 0 { index + length } else { index };
    usize::try_from(position)
        .ok()
        .and_then(|position| items.get(position))
        .ok_or_else(|| failed("IndexError: index out of range"))
}

/// `values[start:stop]` as Python slices a sequence, for a `start` of at least 0.
fn slice(values: &[u32], start: i64, stop: i64) -> &[u32] {
    let length = values.len() as i64;
    let bound = |index: i64| (if index < 0 { index + length } else { index }).clamp(0, length);
    let (start, stop) = (bound(start), bound(stop));
    if stop <= start {
        &[]
    } else {
        &values[start as usize..stop as usize]
    }
}

/// `enrich._covering`: the first span holding the position, or the last span's index when none does, -1 for no span.
fn covering(spans: &[(i64, i64)], position: i64) -> i64 {
    spans
        .iter()
        .position(|&(start, end)| start <= position && position < end)
        .map_or(spans.len() as i64 - 1, |index| index as i64)
}

/// `enrich._junction_outcomes_match`: whether `candidate`, found at code point `offset` inside `item`, has the same before and after outcomes at the junction between item's after cells `left` and `right` (the same covering spans after the offset, glyph and cell names, and junction tokens) and has that junction as its own primary pair.
fn junction_outcomes_match(
    item: &PrimaryUnitProjection,
    left: i64,
    right: i64,
    candidate: &PrimaryUnitProjection,
    offset: i64,
) -> std::result::Result<bool, BuildError> {
    let span_left = *at(&item.after_spans, left)?;
    let span_right = *at(&item.after_spans, right)?;
    let shifted_left = (span_left.0 - offset, span_left.1 - offset);
    let shifted_right = (span_right.0 - offset, span_right.1 - offset);
    let Some(candidate_left) = candidate
        .after_spans
        .iter()
        .position(|&span| span == shifted_left)
    else {
        return Ok(false);
    };
    let candidate_left = candidate_left as i64;
    let candidate_right = candidate_left + 1;
    if candidate_right >= candidate.after_spans.len() as i64 {
        return Ok(false);
    }
    if *at(&candidate.after_spans, candidate_right)? != shifted_right {
        return Ok(false);
    }
    if at(&candidate.after_cells, candidate_left)? != at(&item.after_cells, left)? {
        return Ok(false);
    }
    if at(&candidate.after_cells, candidate_right)? != at(&item.after_cells, right)? {
        return Ok(false);
    }
    if at(&candidate.after_junctions, candidate_left)? != at(&item.after_junctions, left)? {
        return Ok(false);
    }
    let gap = span_left.1 - 1;
    let mine_left = covering(&item.before_spans, gap);
    let mine_right = covering(&item.before_spans, gap + 1);
    let theirs_left = covering(&candidate.before_spans, gap - offset);
    let theirs_right = covering(&candidate.before_spans, gap + 1 - offset);
    for (mine, theirs) in [(mine_left, theirs_left), (mine_right, theirs_right)] {
        let their_span = *at(&candidate.before_spans, theirs)?;
        if (their_span.0 + offset, their_span.1 + offset) != *at(&item.before_spans, mine)? {
            return Ok(false);
        }
        if at(&candidate.before_glyphs, theirs)? != at(&item.before_glyphs, mine)? {
            return Ok(false);
        }
    }
    if mine_left != mine_right
        && at(&candidate.before_junctions, theirs_left)? != at(&item.before_junctions, mine_left)?
    {
        return Ok(false);
    }
    Ok(candidate.pair == Some((candidate_left, candidate_right)))
}

/// The corpus as the primary-unit search reads it, by ordinal (`enrich.PrimaryUnitSource`): the unit store, or a list of projections in the crate's tests. `id_word` ranks units as their ids do, `invisible` is whether a unit is ink- or picture-identical, and `set_primary_units` takes each junction's primary unit as an ordinal or none.
pub trait PrimaryUnitSource {
    fn unit_count(&self) -> usize;
    fn junction_count(&self, ordinal: usize) -> usize;
    fn projection(&self, ordinal: usize) -> PrimaryUnitProjection;
    fn id_word(&self, ordinal: usize) -> u64;
    fn invisible(&self, ordinal: usize) -> bool;
    fn set_primary_units(
        &mut self,
        ordinal: usize,
        assign: &[(Option<usize>, bool)],
    ) -> std::result::Result<(), BuildError>;
}

impl PrimaryUnitSource for UnitStore {
    fn unit_count(&self) -> usize {
        UnitStore::len(self)
    }

    fn junction_count(&self, ordinal: usize) -> usize {
        UnitStore::junction_count(self, ordinal)
    }

    fn projection(&self, ordinal: usize) -> PrimaryUnitProjection {
        UnitStore::projection(self, ordinal)
    }

    fn id_word(&self, ordinal: usize) -> u64 {
        UnitStore::id_word(self, ordinal)
    }

    fn invisible(&self, ordinal: usize) -> bool {
        UnitStore::invisible(self, ordinal)
    }

    fn set_primary_units(
        &mut self,
        ordinal: usize,
        assign: &[(Option<usize>, bool)],
    ) -> std::result::Result<(), BuildError> {
        let assign: Vec<(Target, bool)> = assign
            .iter()
            .map(|&(primary, suppressed)| {
                let target =
                    primary.map_or(Target::Absent, |primary| Target::Ordinal(primary as i64));
                (target, suppressed)
            })
            .collect();
        UnitStore::set_primary_units(self, ordinal, &assign).map_err(failed)
    }
}

/// `enrich._find_primary_unit`: the ordinal of the junction's primary unit, the shortest other unit whose window is a stretch of `item`'s holding the junction's two cells, with the same outcomes at the junction and that junction as its primary pair; ties break to the lowest id word. `held` keeps the candidates already read for this item.
fn find_primary_unit(
    item: &PrimaryUnitProjection,
    ordinal: usize,
    (left, right): (i64, i64),
    windows: &WindowIndex,
    source: &impl PrimaryUnitSource,
    held: &mut HashMap<u32, PrimaryUnitProjection>,
) -> std::result::Result<Option<usize>, BuildError> {
    let values = &item.codepoint_values;
    let count = values.len() as i64;
    let start = at(&item.after_spans, left)?.0;
    let end = at(&item.after_spans, right)?.1;
    for length in (end - start)..=count {
        let mut first: Option<(u64, usize)> = None;
        for offset in 0.max(end - length)..=start.min(count - length) {
            for &candidate in windows.candidates(slice(values, offset, offset + length)) {
                if candidate as usize == ordinal {
                    continue;
                }
                let projection = held
                    .entry(candidate)
                    .or_insert_with(|| source.projection(candidate as usize));
                if junction_outcomes_match(item, left, right, projection, offset)? {
                    let word = source.id_word(candidate as usize);
                    if first.is_none_or(|(least, _)| word < least) {
                        first = Some((word, candidate as usize));
                    }
                }
            }
        }
        if let Some((_, found)) = first {
            return Ok(Some(found));
        }
    }
    Ok(None)
}

/// The secondary-junction counts the primary-unit resolution pass tallies, in the order its dict holds them.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct JunctionCounts {
    pub units_with_markers: i64,
    pub junctions_with_primary_unit: i64,
    pub junctions_without_primary_unit: i64,
    pub junctions_suppressed_invisible: i64,
}

impl JunctionCounts {
    /// The counts as their dict's items, in its order.
    pub fn items(&self) -> [(&'static str, i64); 4] {
        [
            ("units_with_markers", self.units_with_markers),
            (
                "junctions_with_primary_unit",
                self.junctions_with_primary_unit,
            ),
            (
                "junctions_without_primary_unit",
                self.junctions_without_primary_unit,
            ),
            (
                "junctions_suppressed_invisible",
                self.junctions_suppressed_invisible,
            ),
        ]
    }
}

/// `enrich.resolve_primary_unit_assignments` over `source` with the table's window index: each unit with secondary junctions gets, a junction at a time in junction order, its primary unit, None and suppressed when that unit is ink- or picture-identical, or None and visible when no unit qualifies, written back through `source.set_primary_units`; returns the counts.
pub fn resolve_primary_unit_assignments(
    source: &mut impl PrimaryUnitSource,
    windows: &WindowIndex,
) -> std::result::Result<JunctionCounts, BuildError> {
    let mut counts = JunctionCounts::default();
    for ordinal in 0..source.unit_count() {
        if source.junction_count(ordinal) == 0 {
            continue;
        }
        let item = source.projection(ordinal);
        let mut held = HashMap::new();
        let mut visible = 0;
        let mut assign = Vec::with_capacity(item.junction_pairs.len());
        for &pair in &item.junction_pairs {
            match find_primary_unit(&item, ordinal, pair, windows, source, &mut held)? {
                None => {
                    counts.junctions_without_primary_unit += 1;
                    visible += 1;
                    assign.push((None, false));
                }
                Some(primary) if source.invisible(primary) => {
                    counts.junctions_suppressed_invisible += 1;
                    assign.push((None, true));
                }
                Some(primary) => {
                    counts.junctions_with_primary_unit += 1;
                    visible += 1;
                    assign.push((Some(primary), false));
                }
            }
        }
        source.set_primary_units(ordinal, &assign)?;
        if visible > 0 {
            counts.units_with_markers += 1;
        }
    }
    Ok(counts)
}

/// What [`passes`] gives the write: how many duplicate groups the human units fall in, every class's ordinals in triage order with the classes in the order they first appear along it, the secondary-junction counts, the verification's keys by the id each recomputation stamped, and every unit's re-settlement mismatches in ordinal order.
#[derive(Clone, Debug)]
pub struct Passes {
    pub duplicate_groups: usize,
    pub by_class: Vec<(String, Vec<u32>)>,
    pub junction_counts: JunctionCounts,
    pub verified: Vec<VerifyKey>,
    pub mismatches: Vec<String>,
}

/// The passes after [`triage`], through the stale check, as `build._build_m1` runs them. Each human unit, one with a batch, takes the duplicate group its [`duplicate_group_key`] names and the cluster the store holds for it; the classes' rows are taken in the triage order; every secondary junction's primary unit is resolved over the table's window index, which is released after; each sampled unit of `verify_units` is recomputed on `drive`'s pool with its duplicate group, cluster, final class and primary units injected; and a sampled unit whose recomputed content key or ink deltas differ from the store's fails the build with the first ten such ids, sorted.
pub fn passes(
    table: &mut UnitTable,
    store: &mut UnitStore,
    triage: &Triage,
    drive: &Drive,
    verify_units: &[Unit],
) -> std::result::Result<Passes, BuildError> {
    let mut duplicate_groups: HashSet<String> = HashSet::new();
    for ordinal in 0..table.n {
        if table.batch(ordinal).is_none() {
            continue;
        }
        let duplicate_group = duplicate_group_id(&duplicate_group_key(table, store, ordinal)?)?;
        table.set_duplicate_group(ordinal, Some(&duplicate_group));
        duplicate_groups.insert(duplicate_group);
        table.set_cluster(ordinal, Some(store.cluster(ordinal)));
    }
    let by_class = table.rows_by_class(triage.order.iter().copied());
    let junction_counts = resolve_primary_unit_assignments(store, table.window_index())?;
    table.release_window_index();
    let items = verify_units
        .iter()
        .map(|unit| {
            let ordinal = unit.ordinal;
            let injection = Injection {
                duplicate_group: table.duplicate_group(ordinal).map(str::to_owned),
                cluster: table.cluster(ordinal).map(str::to_owned),
                class: table.class_id(ordinal).to_owned(),
                primary_units: store.primary_units(ordinal),
            };
            (unit.clone(), injection)
        })
        .collect();
    let verified = drive.verify(table, items)?;
    let mut stale: Vec<&str> = Vec::new();
    for key in &verified {
        let ordinal = store.ordinal_of(&key.unit_id).map_err(failed)?;
        let recomputed: BTreeMap<&str, &str> = key
            .ink_deltas
            .iter()
            .map(|(config, delta)| (config.as_str(), delta.as_str()))
            .collect();
        let held: BTreeMap<&str, &str> = store.ink_deltas(ordinal).into_iter().collect();
        if key.content_key != store.content_key_hex(ordinal) || recomputed != held {
            stale.push(&key.unit_id);
        }
    }
    if !stale.is_empty() {
        stale.sort_unstable();
        return Err(BuildError::Exit(format!(
            "the unit cache supplied {} of {} sampled units whose content key or ink deltas do not match a recomputation: {}",
            stale.len(),
            verified.len(),
            stale[..stale.len().min(10)].join(", ")
        )));
    }
    let mismatches = (0..table.n)
        .flat_map(|ordinal| store.mismatches(ordinal).iter().cloned())
        .collect();
    Ok(Passes {
        duplicate_groups: duplicate_groups.len(),
        by_class,
        junction_counts,
        verified,
        mismatches,
    })
}

/// The ink-signature store's part of the units phase's note: written with its entries, or left as it is.
pub fn signature_note(signatures: &SignatureArrays) -> String {
    if signatures.unchanged() {
        "ink-signature store left as it is, every prior entry hit and none missed".to_owned()
    } else {
        format!(
            "ink-signature store written, {} entries",
            thousands(signatures.hits() + signatures.misses())
        )
    }
}

/// The units phase's note, as `build._build_m1` closes its line after the stale check: the pool's width under the Rust units engine, the gate lane's state when the width read it, the units recomputed and the sampled units verified, the arena's note and the ink-signature store's.
pub fn units_note(
    jobs: usize,
    lane: &str,
    recomputed: usize,
    verified: usize,
    arena: &str,
    signatures: &str,
) -> String {
    let lane = if lane.is_empty() {
        String::new()
    } else {
        format!("{lane}; ")
    };
    format!(
        "jobs={jobs} engine=rust ({lane}recomputed={}, verified={} cached; {arena}; {signatures})",
        thousands(recomputed),
        thousands(verified)
    )
}

/// The keys of the `triage` stage's lines beside its units (`units_stage_fixtures.TRIAGE_*`).
const CLASSES_KEY: &str = "classes";
const SECTIONS_KEY: &str = "sections";
const ORDER_COLUMN: &str = "order";
const BATCHES_KEY: &str = "batches";

fn optional_int(value: Option<u32>) -> PyValue {
    value.map_or(PyValue::None, int)
}

/// One unit as the `triage` stage reads it from the table: its final class, its unmatched group, its exemplar flag, and its place in the triage index and its batch, each None for a unit outside the index.
fn triage_reading(table: &UnitTable, ordinal: usize) -> PyValue {
    PyValue::Tuple(vec![
        PyValue::str(table.class_id(ordinal)),
        PyValue::str(table.unmatched_group(ordinal)),
        PyValue::Bool(table.exemplar(ordinal)),
        optional_int(table.order(ordinal)),
        optional_int(table.batch(ordinal)),
    ])
}

/// Write the `triage` stage's lines, as the Python stage's `triage_boundary` lays them out: a `unit:<ordinal>` line for each ordinal in `units`, the unit as [`triage_reading`] reads it, and the column over every unit; the classes in manifest order; each class's section, in the order the classes were met; the triage order's column; and the batch count.
fn triage_lines(
    table: &UnitTable,
    triage: &Triage,
    units: &HashSet<usize>,
    out: &mut impl Write,
) -> Result<()> {
    let mut digest = ColumnDigest::new();
    for ordinal in 0..table.n {
        let value = triage_reading(table, ordinal);
        if units.contains(&ordinal) {
            stage_line(out, &format!("{UNIT_PREFIX}{ordinal}"), &value)?;
        }
        digest.add(&value);
    }
    digest.write(out, "value")?;
    stage_line(
        out,
        CLASSES_KEY,
        &PyValue::List(triage.classes.iter().map(LedgerEntry::to_py).collect()),
    )?;
    stage_line(
        out,
        SECTIONS_KEY,
        &PyValue::List(
            triage
                .sections
                .iter()
                .map(|(id, section)| PyValue::Tuple(vec![PyValue::str(id), PyValue::str(section)]))
                .collect(),
        ),
    )?;
    let mut order = ColumnDigest::new();
    for &ordinal in &triage.order {
        order.add(&int(ordinal));
    }
    order.write(out, ORDER_COLUMN)?;
    stage_line(out, BATCHES_KEY, &int(triage.batches))
}

/// The `triage` stage: run the build through phase 1 ([`through_units`]), then the passes from the id index to the batches ([`triage`]), and answer with the lines `triage_lines` writes, a `unit:<ordinal>` line for each one the stage file asks for. A stage file with no lines, from a build whose passes did not finish, is answered with none.
pub fn stage_triage(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let (units, lines) = stage_units(stage_file)?;
    if lines == 0 {
        return Ok(());
    }
    through_units(&input, stage_file, "triage", |run| {
        let triage = triage(
            &input,
            &run.classes_present,
            &mut run.table,
            &mut run.planned.store,
        )
        .map_err(|failure| WireError(format!("the passes failed: {failure}")))?;
        triage_lines(&run.table, &triage, &units, out)
    })
}

/// The keys of the `passes` stage's lines beside its units (`units_stage_fixtures.PASSES_*`).
const DUPLICATE_GROUPS_KEY: &str = "duplicate_groups";
const BY_CLASS_COLUMN: &str = "by_class";
const JUNCTIONS_KEY: &str = "secondary_junctions";
const VERIFIED_KEY: &str = "verified";
const MISMATCHES_KEY: &str = "mismatches";
const NOTE_KEY: &str = "note";

fn optional_text(value: Option<&str>) -> PyValue {
    PyValue::optional_str(value)
}

/// One unit as the `passes` stage reads it: its duplicate group and cluster from the table, and its primary units from the store, a `(primary unit, suppressed)` tuple a secondary junction.
fn passes_reading(table: &UnitTable, store: &UnitStore, ordinal: usize) -> PyValue {
    PyValue::Tuple(vec![
        optional_text(table.duplicate_group(ordinal)),
        optional_text(table.cluster(ordinal)),
        PyValue::Tuple(
            store
                .primary_units(ordinal)
                .into_iter()
                .map(|(primary, suppressed)| {
                    PyValue::Tuple(vec![
                        optional_text(primary.as_deref()),
                        PyValue::Bool(suppressed),
                    ])
                })
                .collect(),
        ),
    ])
}

/// Write the `passes` stage's run lines, as the Python stage's `passes_boundary` lays them out: a `unit:<ordinal>` line for each ordinal in `units`, the unit as [`passes_reading`] reads it, and the column over every unit; the duplicate-group count; a column over the classes' rows, a `(class, ordinals)` pair a class; the secondary-junction counts as their items; the verification's keys sorted, each `(id, content key, ink deltas)`; the mismatches; and the units phase's note.
fn passes_lines(
    table: &UnitTable,
    store: &UnitStore,
    passes: &Passes,
    note: &str,
    units: &HashSet<usize>,
    out: &mut impl Write,
) -> Result<()> {
    let mut digest = ColumnDigest::new();
    for ordinal in 0..table.n {
        let value = passes_reading(table, store, ordinal);
        if units.contains(&ordinal) {
            stage_line(out, &format!("{UNIT_PREFIX}{ordinal}"), &value)?;
        }
        digest.add(&value);
    }
    digest.write(out, "value")?;
    stage_line(
        out,
        DUPLICATE_GROUPS_KEY,
        &PyValue::Int(passes.duplicate_groups as i64),
    )?;
    let mut by_class = ColumnDigest::new();
    for (class_id, members) in &passes.by_class {
        by_class.add(&PyValue::Tuple(vec![
            PyValue::str(class_id),
            PyValue::Tuple(members.iter().copied().map(int).collect()),
        ]));
    }
    by_class.write(out, BY_CLASS_COLUMN)?;
    stage_line(
        out,
        JUNCTIONS_KEY,
        &PyValue::List(
            passes
                .junction_counts
                .items()
                .iter()
                .map(|&(name, count)| PyValue::Tuple(vec![PyValue::str(name), PyValue::Int(count)]))
                .collect(),
        ),
    )?;
    let mut verified: Vec<PyValue> = passes
        .verified
        .iter()
        .map(|key| {
            PyValue::Tuple(vec![
                PyValue::str(&key.unit_id),
                PyValue::str(&key.content_key),
                PyValue::Tuple(
                    key.ink_deltas
                        .iter()
                        .map(|(config, delta)| {
                            PyValue::Tuple(vec![PyValue::str(config), PyValue::str(delta)])
                        })
                        .collect(),
                ),
            ])
        })
        .collect();
    verified.sort_by(|left, right| {
        let id = |value: &PyValue| match value {
            PyValue::Tuple(items) => match items.first() {
                Some(PyValue::Str(id)) => id.clone(),
                _ => String::new(),
            },
            _ => String::new(),
        };
        id(left).cmp(&id(right))
    });
    stage_line(out, VERIFIED_KEY, &PyValue::List(verified))?;
    stage_line(
        out,
        MISMATCHES_KEY,
        &PyValue::List(passes.mismatches.iter().map(PyValue::str).collect()),
    )?;
    stage_line(out, NOTE_KEY, &PyValue::str(note))
}

/// The `passes` stage: when the stage file holds lines from the build's run, run the build through phase 1 ([`through_units`]), then [`triage`] and [`passes`], and answer with the lines `passes_lines` writes, the note the units phase's line would close with ([`units_note`]) among them; then answer each generated line, a duplicate-group key, with its id ([`duplicate_group_id`]).
pub fn stage_passes(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let lines = stage_lines(stage_file)?;
    if lines.ran {
        through_units(&input, stage_file, "passes", |run| {
            let failure = |failure: BuildError| WireError(format!("the passes failed: {failure}"));
            let triage = triage(
                &input,
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
            let (jobs, lane) = super::drive::units_pool_width(&input.widths);
            let note = units_note(
                jobs,
                lane,
                run.planned.recomputed.len(),
                passes.verified.len(),
                &run.drive.arena().note(),
                &signature_note(&run.signatures),
            );
            passes_lines(
                &run.table,
                &run.planned.store,
                &passes,
                &note,
                &lines.units,
                out,
            )
        })?;
    }
    for (key, key_value) in &lines.generated {
        let id = duplicate_group_id(key_value).map_err(|failure| WireError(failure.to_string()))?;
        stage_line(out, key, &PyValue::str(id))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names() -> PassNames {
        let words = |values: &[&str]| values.iter().map(|&value| value.to_owned()).collect();
        PassNames {
            unmatched: "UNMATCHED".to_owned(),
            sections: words(&["open-questions", "spot-check", "no-review-needed"]),
            statuses: words(&["by-design", "approved", "per-unit"]),
            ruled: words(&["by-design", "approved"]),
        }
    }

    #[test]
    fn a_section_follows_the_exemption_the_human_units_and_the_status() {
        let names = names();
        let section = |status, no_verdict, human_units| {
            class_section(status, no_verdict, human_units, &names).expect("a section")
        };
        assert_eq!(section(Some("approved"), true, 4), "no-review-needed");
        assert_eq!(section(Some("per-unit"), false, 0), "no-review-needed");
        assert_eq!(section(Some("by-design"), false, 1), "spot-check");
        assert_eq!(section(Some("approved"), false, 1), "spot-check");
        assert_eq!(section(Some("per-unit"), false, 1), "open-questions");
        assert_eq!(section(None, false, 1), "open-questions");
        assert_eq!(
            class_section(Some("ruled"), false, 1, &names),
            Err(failed(
                "ValueError: 'ruled' is not a ledger status (by-design, approved, per-unit)"
            ))
        );
    }

    /// One unit's primary units as the list source records them: its id, and each junction's primary unit id or none and whether it is suppressed.
    type Assigned = (String, Vec<(Option<String>, bool)>);

    /// A list of projections as a primary-unit source, as `enrich._ListSource` is: the id word is the id's bytes read big-endian.
    struct ListSource {
        projections: Vec<PrimaryUnitProjection>,
        assignments: Vec<Assigned>,
    }

    impl PrimaryUnitSource for ListSource {
        fn unit_count(&self) -> usize {
            self.projections.len()
        }

        fn junction_count(&self, ordinal: usize) -> usize {
            self.projections[ordinal].junction_pairs.len()
        }

        fn projection(&self, ordinal: usize) -> PrimaryUnitProjection {
            self.projections[ordinal].clone()
        }

        fn id_word(&self, ordinal: usize) -> u64 {
            let mut bytes = [0u8; 8];
            let id = self.projections[ordinal].unit_id.as_bytes();
            bytes[8 - id.len()..].copy_from_slice(id);
            u64::from_be_bytes(bytes)
        }

        fn invisible(&self, ordinal: usize) -> bool {
            let item = &self.projections[ordinal];
            item.ink_identical || item.picture_identical
        }

        fn set_primary_units(
            &mut self,
            ordinal: usize,
            assign: &[(Option<usize>, bool)],
        ) -> std::result::Result<(), BuildError> {
            let named = assign
                .iter()
                .map(|&(primary, suppressed)| {
                    (
                        primary.map(|primary| self.projections[primary].unit_id.clone()),
                        suppressed,
                    )
                })
                .collect();
            let unit_id = self.projections[ordinal].unit_id.clone();
            self.assignments.push((unit_id, named));
            Ok(())
        }
    }

    /// `test_review_enrich._stub_enriched`'s projection: one code point a cell, before glyphs named for the cells, every before junction a break.
    fn stub(
        unit_id: &str,
        values: &[u32],
        cells: &[&str],
        junctions: &[&str],
        pair: (i64, i64),
        flags: (bool, bool),
        junction_pairs: &[(i64, i64)],
    ) -> PrimaryUnitProjection {
        let spans: Vec<(i64, i64)> = (0..values.len() as i64).map(|at| (at, at + 1)).collect();
        let texts = |items: &[&str]| items.iter().map(|&item| item.to_owned()).collect();
        PrimaryUnitProjection {
            unit_id: unit_id.to_owned(),
            codepoint_values: values.to_vec(),
            ink_identical: flags.0,
            picture_identical: flags.1,
            pair: Some(pair),
            after_spans: spans.clone(),
            after_cells: texts(cells),
            after_junctions: texts(junctions),
            before_spans: spans,
            before_glyphs: cells.iter().map(|cell| format!("old-{cell}")).collect(),
            before_junctions: vec!["break".to_owned(); cells.len() - 1],
            junction_pairs: junction_pairs.to_vec(),
        }
    }

    fn resolved(projections: Vec<PrimaryUnitProjection>) -> (Vec<Assigned>, [i64; 4]) {
        let windows = WindowIndex::build(
            projections
                .iter()
                .enumerate()
                .map(|(ordinal, item)| (ordinal as u32, item.codepoint_values.clone())),
        );
        let mut source = ListSource {
            projections,
            assignments: Vec::new(),
        };
        let counts = resolve_primary_unit_assignments(&mut source, &windows).expect("resolved");
        (
            source.assignments,
            counts.items().map(|(_name, count)| count),
        )
    }

    #[test]
    fn a_secondary_junction_resolves_as_the_python_tests_corpora_do() {
        let none = (false, false);
        let item = stub(
            "u-0001",
            &[0xE650, 0xE665, 0xE652, 0xE670],
            &["A", "B", "C", "D"],
            &["y0", "y5", "break"],
            (0, 1),
            none,
            &[(1, 2)],
        );
        let pair = |unit_id, cells: &[&str], flags| {
            stub(
                unit_id,
                &[0xE665, 0xE652],
                cells,
                &["y5"],
                (0, 1),
                flags,
                &[],
            )
        };
        let triple = |unit_id, judged| {
            stub(
                unit_id,
                &[0xE665, 0xE652, 0xE670],
                &["B", "C", "D"],
                &["y5", "break"],
                judged,
                none,
                &[],
            )
        };
        let short = pair("u-0002", &["B", "C"], none);
        let longer = triple("u-0003", (0, 1));
        let wrong_cell = pair("u-0004", &["B", "C-other"], none);
        let secondary_there_too = triple("u-0005", (1, 2));
        let ink_identical = pair("u-0006", &["B", "C"], (true, false));
        let picture_identical = pair("u-0007", &["B", "C"], (false, true));
        let assigned = |primary: Option<&str>, suppressed| {
            vec![(
                "u-0001".to_owned(),
                vec![(primary.map(str::to_owned), suppressed)],
            )]
        };
        assert_eq!(
            resolved(vec![item.clone(), short.clone(), longer.clone()]),
            (assigned(Some("u-0002"), false), [1, 1, 0, 0])
        );
        assert_eq!(
            resolved(vec![item.clone(), wrong_cell.clone(), longer.clone()]),
            (assigned(Some("u-0003"), false), [1, 1, 0, 0])
        );
        assert_eq!(
            resolved(vec![item.clone(), secondary_there_too]),
            (assigned(None, false), [1, 0, 1, 0])
        );
        assert_eq!(
            resolved(vec![item.clone(), ink_identical]),
            (assigned(None, true), [0, 0, 0, 1])
        );
        assert_eq!(
            resolved(vec![item.clone(), picture_identical]),
            (assigned(None, true), [0, 0, 0, 1])
        );
        assert_eq!(resolved(vec![item]), (assigned(None, false), [1, 0, 1, 0]));
        assert_eq!(
            resolved(vec![short, longer, wrong_cell]),
            (Vec::new(), [0, 0, 0, 0])
        );
    }

    #[test]
    fn a_duplicate_group_id_hashes_the_keys_repr_as_python_does() {
        let configs = |items: &[&str]| items.iter().map(|&item| PyValue::str(item)).collect();
        let key = |configs, pair| {
            PyValue::Tuple(vec![
                configs,
                pair,
                PyValue::str("tea-utter"),
                PyValue::str("d"),
            ])
        };
        let pair = PyValue::Tuple(vec![PyValue::Int(58960), PyValue::Int(59002)]);
        assert_eq!(
            duplicate_group_id(&PyValue::Tuple(vec![
                PyValue::Tuple(configs(&["default"])),
                PyValue::None,
                PyValue::str("UNMATCHED"),
                PyValue::str(""),
            ])),
            Ok("e-QymS4oCuiKb".to_owned())
        );
        assert_eq!(
            duplicate_group_id(&key(
                PyValue::Tuple(configs(&["default", "ss03"])),
                pair.clone()
            )),
            Ok("e-eH5qPr3YQb9".to_owned())
        );
        assert_eq!(
            duplicate_group_id(&key(PyValue::List(configs(&["default", "ss03"])), pair)),
            Ok("e-LzTXtn6tfLA".to_owned())
        );
    }

    #[test]
    fn the_units_note_names_the_lane_only_when_the_width_read_one() {
        assert_eq!(
            units_note(11, "", 0, 200, "arena empty", "signatures"),
            "jobs=11 engine=rust (recomputed=0, verified=200 cached; arena empty; signatures)"
        );
        assert_eq!(
            units_note(4, "gate lane idle", 1234, 200, "arena", "signatures"),
            "jobs=4 engine=rust (gate lane idle; recomputed=1,234, verified=200 cached; arena; signatures)"
        );
    }

    #[test]
    fn a_python_slice_and_index_clamp_and_count_from_the_end() {
        let values = [1, 2, 3, 4];
        assert_eq!(slice(&values, 1, 3), &[2, 3]);
        assert_eq!(slice(&values, 2, 9), &[3, 4]);
        assert_eq!(slice(&values, 3, 1), &[] as &[u32]);
        assert_eq!(slice(&values, 0, -1), &[1, 2, 3]);
        assert_eq!(at(&values, -1), Ok(&4));
        assert!(at(&values, 4).is_err());
        assert_eq!(covering(&[(0, 1), (1, 3)], 2), 1);
        assert_eq!(covering(&[(0, 1), (1, 3)], 7), 1);
        assert_eq!(covering(&[], 0), -1);
    }

    #[test]
    fn a_batch_is_a_place_divided_by_the_batch_size() {
        assert_eq!(batch_of(None, 300), None);
        assert_eq!(batch_of(Some(0), 300), Some(0));
        assert_eq!(batch_of(Some(299), 300), Some(0));
        assert_eq!(batch_of(Some(300), 300), Some(1));
    }
}
