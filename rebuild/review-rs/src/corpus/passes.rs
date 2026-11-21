//! The build's whole-corpus passes between the units phase and the write, as `build._build_m1` runs them once phase 1 has loaded every unit. [`triage`] builds the store's id index, promotes each re-routed or unmatched unit to its change group, puts every class the units reach in its section ([`class_section`]) and the classes in manifest order with the change groups' classes after the ledger's ([`synthesize_unmatched_group_classes`]), orders the units for triage ([`sort_for_triage`]) and numbers the human units along that order into batches ([`assign_batches`]).
//!
//! [`stage_triage`] is the `triage` stage of the stage fixtures.

use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::Path;

use super::audit::stage_line;
use super::drive::through_units;
use super::input::{BuildInput, LedgerEntry};
use super::store::UnitStore;
use super::table::{
    Buckets, ColumnDigest, UNIT_PREFIX, UnitTable, counting_permutation_by_id, family_ranks, int,
    stage_units,
};
use super::{BuildError, failed};
use crate::pyfmt::PyValue;
use crate::pyfmt::repr::repr;
use crate::wire::{Result, WireError, error};

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

    #[test]
    fn a_batch_is_a_place_divided_by_the_batch_size() {
        assert_eq!(batch_of(None, 300), None);
        assert_eq!(batch_of(Some(0), 300), Some(0));
        assert_eq!(batch_of(Some(299), 300), Some(0));
        assert_eq!(batch_of(Some(300), 300), Some(1));
    }
}
