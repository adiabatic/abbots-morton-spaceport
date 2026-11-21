//! The review facts, as `facts.py` reduces the build to them once the manifest is on disk: the pre-merge units' ink flags and change groups projected from their survivors ([`derive_premerge`]), the refusal of an ink-identical pre-merge unit that carries a change group, which `build._build_m1` raises ([`grouped_on_identical`]), the sidecar's payload ([`build_facts`]) and `review-facts.json` written as compact ASCII JSON, kept when it already holds those bytes ([`write_facts`]). [`review_facts`] runs them in the build's order.
//!
//! [`stage_facts`] is the `facts` stage of the stage fixtures.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs;
use std::io::{self, Write};
use std::path::Path;

use super::audit::stage_line;
use super::input::BuildInput;
use super::manifest::{ManifestNames, held_manifest, write_manifest};
use super::premerge::PremergeSnapshot;
use super::records::truthy;
use super::scan::stage_lines;
use super::sink::Sink;
use super::store::UnitStore;
use super::table::UnitTable;
use super::write::through_write;
use super::{BuildError, failed};
use crate::change_groups::{reroutes, set_gate_group, unexplained_configs};
use crate::pyfmt::{PyValue, json, repr};
use crate::wire::{Result, WireError, error};

/// The names the review facts read from the build input's vocabulary: the sidecar's file name and format (`facts.FACTS_FILENAME`, `FACTS_FORMAT`), the classes whose unit counts the manifest group copies (`CLASS_UNIT_COUNT_KEYS`), the reference window (`REFERENCE_WINDOW_CODEPOINTS`, parsed as `audit.parse_codepoints` parses it), the batch size the ink group cuts human units by (`audit.BATCH_SIZE`) and the class of a unit no ruling covers (`audit.UNMATCHED_CLASS`).
#[derive(Clone, Debug)]
pub struct FactsNames {
    pub filename: String,
    pub format: String,
    pub class_unit_count_keys: Vec<String>,
    pub reference_window: Vec<u32>,
    pub batch_size: i64,
    pub unmatched: String,
}

impl FactsNames {
    /// The names as the build input's vocabulary gives them.
    pub fn of(input: &BuildInput) -> Result<Self> {
        let constant = |name: &str| {
            input
                .constant(name)
                .ok_or_else(|| WireError(format!("the build input's vocabulary holds no {name}")))
        };
        let text = |name: &str| match constant(name)? {
            PyValue::Str(value) => Ok(value.clone()),
            _ => error(format!("{name} is not a str")),
        };
        let keys = "rebuild.review.facts.CLASS_UNIT_COUNT_KEYS";
        let class_unit_count_keys = match constant(keys)? {
            PyValue::Tuple(items) | PyValue::List(items) => items
                .iter()
                .map(|item| match item {
                    PyValue::Str(name) => Ok(name.clone()),
                    _ => error(format!("{keys} holds a value that is not a str")),
                })
                .collect::<Result<_>>()?,
            _ => return error(format!("{keys} is not a sequence")),
        };
        let window = text("rebuild.review.facts.REFERENCE_WINDOW_CODEPOINTS")?;
        let reference_window = window
            .split(':')
            .map(|part| {
                u32::from_str_radix(part, 16)
                    .map_err(|_| WireError(format!("the reference window {window:?} is not hex")))
            })
            .collect::<Result<_>>()?;
        let batch = "rebuild.review.audit.BATCH_SIZE";
        let PyValue::Int(batch_size) = constant(batch)? else {
            return error(format!("{batch} is not an int"));
        };
        if *batch_size <= 0 {
            return error(format!("{batch} is not a positive int"));
        }
        Ok(Self {
            filename: text("rebuild.review.facts.FACTS_FILENAME")?,
            format: text("rebuild.review.facts.FACTS_FORMAT")?,
            class_unit_count_keys,
            reference_window,
            batch_size: *batch_size,
            unmatched: text("rebuild.review.audit.UNMATCHED_CLASS")?,
        })
    }
}

/// What [`derive_premerge`] gives (`facts.PremergeFacts`): one `0` or `1` ink flag a pre-merge unit in capture order, and the change group of every unit that takes one with its index in that order.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PremergeFacts {
    pub ink_flags: String,
    pub unmatched_groups: Vec<(usize, String)>,
}

impl PremergeFacts {
    /// The facts as the `facts` stage records them: the flags and a list of `(index, group)` pairs.
    pub fn to_py(&self) -> PyValue {
        PyValue::Tuple(vec![
            PyValue::str(&self.ink_flags),
            PyValue::List(
                self.unmatched_groups
                    .iter()
                    .map(|(index, group)| {
                        PyValue::Tuple(vec![PyValue::Int(*index as i64), PyValue::str(group)])
                    })
                    .collect(),
            ),
        ])
    }

    /// The facts the `facts` stage's generated lines carry, as [`PremergeFacts::to_py`] gives them.
    fn from_py(value: &PyValue) -> std::result::Result<Self, String> {
        let PyValue::Tuple(items) = value else {
            return Err("the facts are not a tuple".to_owned());
        };
        let [
            PyValue::Str(ink_flags),
            PyValue::List(groups) | PyValue::Tuple(groups),
        ] = items.as_slice()
        else {
            return Err("the facts are not the flags and the groups".to_owned());
        };
        let unmatched_groups = groups
            .iter()
            .map(|group| match group {
                PyValue::Tuple(pair) | PyValue::List(pair) => match pair.as_slice() {
                    [PyValue::Int(index), PyValue::Str(group)] => usize::try_from(*index)
                        .map(|index| (index, group.clone()))
                        .map_err(|_| format!("a negative index {index}")),
                    _ => Err("a group is not an index and a str".to_owned()),
                },
                _ => Err("a group is not a pair".to_owned()),
            })
            .collect::<std::result::Result<_, _>>()?;
        Ok(Self {
            ink_flags: ink_flags.clone(),
            unmatched_groups,
        })
    }
}

fn value_error(message: impl std::fmt::Display) -> BuildError {
    failed(format!("ValueError: {message}"))
}

/// A window in the audit's format (`audit.format_codepoints`).
fn format_codepoints(values: &[u16]) -> String {
    values
        .iter()
        .map(|value| format!("{value:04X}"))
        .collect::<Vec<_>>()
        .join(":")
}

/// `facts._settled_class`: UNMATCHED when any of the classes is, otherwise the one class every configuration matched, refusing a unit that matches several.
fn settled_class<'c>(
    config_classes: &'c [(String, String)],
    unmatched: &str,
) -> std::result::Result<&'c str, BuildError> {
    let mut classes: BTreeSet<&str> = BTreeSet::new();
    let mut held: Option<&'c str> = None;
    for (_, class) in config_classes {
        classes.insert(class);
        if class == unmatched {
            held = Some(class);
        }
    }
    if let Some(class) = held {
        return Ok(class);
    }
    match (classes.len(), classes.first()) {
        (1, Some(&class)) => Ok(class),
        (count, _) => {
            let sorted = PyValue::List(classes.iter().map(|&class| PyValue::str(class)).collect());
            Err(value_error(format!(
                "a unit matches {count} ledger classes, not one: {}",
                repr::repr(&sorted).map_err(failed)?
            )))
        }
    }
}

/// `facts.derive_premerge`: each pre-merge unit's ink flag read from its survivor in `store`, and the change group of each unit that takes one, an UNMATCHED unit or one the build re-routes out of an `ink_identical` class, with its set gate's group over its own configurations or else its survivor's group from `table`. A unit the review facts re-route and the build does not list under a change group, or the reverse, a unit whose group resolves to none and a unit with the default configuration that the merge removed are refused with Python's text.
pub fn derive_premerge(
    snapshot: &PremergeSnapshot,
    table: &UnitTable,
    store: &UnitStore,
    unmatched_class: &str,
) -> std::result::Result<PremergeFacts, BuildError> {
    let (Some(survivor), Some(removed)) = (&snapshot.survivor, &snapshot.removed) else {
        return Err(value_error(
            "the pre-merge snapshot is derived once the merge's compaction has been rebased onto it",
        ));
    };
    let strings = &table.strings;
    let unmatched = strings.find(unmatched_class);
    let identical_classes: Vec<String> = snapshot.identical_classes.iter().cloned().collect();
    let identical: HashSet<u32> = identical_classes
        .iter()
        .filter_map(|class| strings.find(class))
        .collect();
    let mut flags = Vec::with_capacity(snapshot.n);
    let mut assigned = Vec::new();
    for row in 0..snapshot.n {
        let target = survivor[row] as usize;
        flags.push(if store.flags(target).ink_identical {
            b'1'
        } else {
            b'0'
        });
        let survivor_group = table.unmatched_group(target);
        let class_index = snapshot.class_id[row];
        if Some(class_index) != unmatched {
            let class_id = strings.get(class_index);
            let rerouted = identical.contains(&class_index)
                && reroutes(
                    class_id,
                    snapshot.no_verdict[row] != 0,
                    store.machine_approved(target),
                    &identical_classes,
                );
            if !rerouted && survivor_group.is_empty() {
                continue;
            }
            if settled_class(table.config_classes(target), unmatched_class)? != class_id {
                continue;
            }
            let window = format_codepoints(snapshot.codepoints(table, row));
            if rerouted && survivor_group.is_empty() {
                return Err(value_error(format!(
                    "window {window}: the review facts re-route this {class_id} unit, but the build lists its survivor under no change group"
                )));
            }
            if !rerouted {
                return Err(value_error(format!(
                    "window {window}: the build lists this {class_id} unit under change group {survivor_group}, but the review facts do not re-route it"
                )));
            }
        }
        let row_configs = table.tuples.get(snapshot.configs[row]);
        let survivor_classes = table.config_classes(target);
        let survivor_deltas = store.ink_deltas(target);
        let config_classes = row_configs
            .iter()
            .map(|config| {
                survivor_classes
                    .iter()
                    .rev()
                    .find(|(held, _)| held == config)
                    .map(|(_, class)| (config.clone(), class.clone()))
                    .ok_or_else(|| failed(format!("KeyError: {}", repr::repr_str(config))))
            })
            .collect::<std::result::Result<Vec<_>, _>>()?;
        let ink_deltas: Vec<(String, String)> = row_configs
            .iter()
            .filter_map(|config| {
                survivor_deltas
                    .iter()
                    .find(|(held, _)| held == config)
                    .map(|(_, delta)| (config.clone(), (*delta).to_owned()))
            })
            .collect();
        let unexplained = unexplained_configs(
            row_configs,
            &config_classes,
            &ink_deltas,
            &identical_classes,
        );
        let set_group = set_gate_group(&unexplained).map_err(value_error)?;
        if set_group.is_none() && removed[row] != 0 {
            return Err(failed(format!(
                "AssertionError: window {}: a unit with the default config was merged away, so its change group cannot be read off its own phase-1 row",
                format_codepoints(snapshot.codepoints(table, row))
            )));
        }
        let group = set_group.unwrap_or(survivor_group);
        if group.is_empty() {
            return Err(value_error(format!(
                "window {}: a unit that takes a change group resolved to none",
                format_codepoints(snapshot.codepoints(table, row))
            )));
        }
        assigned.push((row, group.to_owned()));
    }
    Ok(PremergeFacts {
        ink_flags: String::from_utf8(flags).map_err(failed)?,
        unmatched_groups: assigned,
    })
}

/// The refusal `build._build_m1` raises once the pre-merge facts are derived: a window that takes a change group is never ink-identical, so a change group on a unit flagged ink-identical stops the build, naming how many and the first's capture index.
pub fn grouped_on_identical(facts: &PremergeFacts) -> std::result::Result<(), BuildError> {
    let flags = facts.ink_flags.as_bytes();
    let mut grouped = Vec::new();
    for (index, _) in &facts.unmatched_groups {
        match flags.get(*index) {
            Some(b'1') => grouped.push(*index),
            Some(_) => {}
            None => return Err(failed("IndexError: string index out of range")),
        }
    }
    match grouped.first() {
        Some(first) => Err(BuildError::Exit(format!(
            "{} ink-identical pre-merge units carry a change group (first at capture index {first})",
            grouped.len()
        ))),
        None => Ok(()),
    }
}

fn text(value: &str) -> PyValue {
    PyValue::str(value)
}

fn dict(items: Vec<(&str, PyValue)>) -> PyValue {
    PyValue::Dict(
        items
            .into_iter()
            .map(|(key, value)| (text(key), value))
            .collect(),
    )
}

/// `value[key]` for a dict, Python's `KeyError` or `TypeError` otherwise.
fn item<'v>(value: &'v PyValue, key: &str) -> std::result::Result<&'v PyValue, BuildError> {
    let PyValue::Dict(entries) = value else {
        return Err(failed(format!(
            "TypeError: {} indices are not {}",
            value.kind(),
            repr::repr_str(key)
        )));
    };
    entries
        .iter()
        .find(|(held, _)| matches!(held, PyValue::Str(held) if held == key))
        .map(|(_, value)| value)
        .ok_or_else(|| failed(format!("KeyError: {}", repr::repr_str(key))))
}

fn entries(value: &PyValue) -> std::result::Result<&[(PyValue, PyValue)], BuildError> {
    match value {
        PyValue::Dict(entries) => Ok(entries),
        _ => Err(failed(format!(
            "TypeError: a {} is not a dict",
            value.kind()
        ))),
    }
}

fn items(value: &PyValue) -> std::result::Result<&[PyValue], BuildError> {
    match value {
        PyValue::List(items) | PyValue::Tuple(items) => Ok(items),
        _ => Err(failed(format!(
            "TypeError: a {} is not a list",
            value.kind()
        ))),
    }
}

fn key_text(key: &PyValue) -> std::result::Result<&str, BuildError> {
    match key {
        PyValue::Str(key) => Ok(key),
        _ => Err(failed(format!("KeyError: a {} key", key.kind()))),
    }
}

/// `facts.class_rank`: the ledger classes in the order of the manifest's `ledger_classes` lookup, then the change groups in `group_order`, a later place winning for a name that is both.
struct ClassRank(HashMap<String, (u8, usize)>);

impl ClassRank {
    fn of(manifest: &PyValue, group_order: &[String]) -> std::result::Result<Self, BuildError> {
        let mut ranks = HashMap::new();
        for (index, (key, _)) in entries(item(manifest, "ledger_classes")?)?
            .iter()
            .enumerate()
        {
            ranks.insert(key_text(key)?.to_owned(), (0, index));
        }
        for (index, group) in group_order.iter().enumerate() {
            ranks.insert(group.clone(), (1, index));
        }
        Ok(Self(ranks))
    }

    fn rank(&self, class_id: &str) -> std::result::Result<(u8, usize), BuildError> {
        self.0
            .get(class_id)
            .copied()
            .ok_or_else(|| failed(format!("KeyError: {}", repr::repr_str(class_id))))
    }

    /// `names` sorted by their rank, stably.
    fn sorted<'n>(&self, names: Vec<&'n str>) -> std::result::Result<Vec<&'n str>, BuildError> {
        let mut ranked = names
            .into_iter()
            .map(|name| Ok((self.rank(name)?, name)))
            .collect::<std::result::Result<Vec<_>, BuildError>>()?;
        ranked.sort_by_key(|&(rank, _)| rank);
        Ok(ranked.into_iter().map(|(_, name)| name).collect())
    }
}

/// `facts.manifest_group`: the manifest's totals, its machine-approved units with their classes in rank order, the unit counts of `CLASS_UNIT_COUNT_KEYS`' classes and the secondary-junction counts.
fn manifest_group(
    manifest: &PyValue,
    rank: &ClassRank,
    names: &FactsNames,
) -> std::result::Result<PyValue, BuildError> {
    let mut by_id: HashMap<&str, &PyValue> = HashMap::new();
    for meta in items(item(manifest, "classes")?)? {
        by_id.insert(key_text(item(meta, "id")?)?, meta);
    }
    let machine_approved = item(manifest, "machine_approved")?;
    let by_class = entries(item(machine_approved, "by_class")?)?;
    let counts: HashMap<&str, &PyValue> = by_class
        .iter()
        .map(|(key, count)| Ok((key_text(key)?, count)))
        .collect::<std::result::Result<_, BuildError>>()?;
    let ordered = rank.sorted(
        by_class
            .iter()
            .map(|(key, _)| key_text(key))
            .collect::<std::result::Result<_, _>>()?,
    )?;
    let class_unit_count = names
        .class_unit_count_keys
        .iter()
        .map(|key| {
            let meta = by_id
                .get(key.as_str())
                .ok_or_else(|| failed(format!("KeyError: {}", repr::repr_str(key))))?;
            Ok((text(key), item(meta, "unit_count")?.clone()))
        })
        .collect::<std::result::Result<Vec<_>, BuildError>>()?;
    Ok(dict(vec![
        (
            "totals",
            PyValue::Dict(entries(item(manifest, "totals")?)?.to_vec()),
        ),
        (
            "machine_approved",
            dict(vec![
                ("units", item(machine_approved, "units")?.clone()),
                (
                    "by_class",
                    PyValue::Dict(
                        ordered
                            .into_iter()
                            .map(|key| (text(key), counts[key].clone()))
                            .collect(),
                    ),
                ),
            ]),
        ),
        ("class_unit_count", PyValue::Dict(class_unit_count)),
        (
            "secondary_junctions",
            PyValue::Dict(entries(item(manifest, "secondary_junctions")?)?.to_vec()),
        ),
    ]))
}

/// `facts.invariant_group`: the classes the manifest ships, the machine-approved classes and the no-verdict classes, each in rank order, and the change groups `groups` counts, in its order.
fn invariant_group(
    manifest: &PyValue,
    rank: &ClassRank,
    groups: &[(String, i64)],
) -> std::result::Result<PyValue, BuildError> {
    let mut classes = items(item(manifest, "classes")?)?
        .iter()
        .map(|meta| Ok((rank.rank(key_text(item(meta, "id")?)?)?, meta)))
        .collect::<std::result::Result<Vec<_>, BuildError>>()?;
    classes.sort_by_key(|&(rank, _)| rank);
    let ids = classes
        .iter()
        .map(|(_, meta)| Ok(text(key_text(item(meta, "id")?)?)))
        .collect::<std::result::Result<Vec<_>, BuildError>>()?;
    let by_class = entries(item(item(manifest, "machine_approved")?, "by_class")?)?;
    let machine = rank.sorted(
        by_class
            .iter()
            .map(|(key, _)| key_text(key))
            .collect::<std::result::Result<_, _>>()?,
    )?;
    let mut no_verdict = Vec::new();
    for (_, meta) in &classes {
        if truthy(item(meta, "no_verdict")?) {
            no_verdict.push(text(key_text(item(meta, "id")?)?));
        }
    }
    Ok(dict(vec![
        ("classes", PyValue::List(ids)),
        (
            "machine_approved_classes",
            PyValue::List(machine.into_iter().map(text).collect()),
        ),
        ("no_verdict_classes", PyValue::List(no_verdict)),
        (
            "unmatched_groups",
            PyValue::List(groups.iter().map(|(group, _)| text(group)).collect()),
        ),
    ]))
}

/// `facts.change_group_counts`: the units a change group, in `group_order` and then by name for a group outside it.
fn change_group_counts<'g>(
    assignments: impl IntoIterator<Item = &'g str>,
    group_order: &[String],
) -> Vec<(String, i64)> {
    let mut counts: Vec<(String, i64)> = Vec::new();
    let mut place: HashMap<&str, usize> = HashMap::new();
    for group in assignments {
        match place.get(group) {
            Some(&at) => counts[at].1 += 1,
            None => {
                place.insert(group, counts.len());
                counts.push((group.to_owned(), 1));
            }
        }
    }
    let mut order: HashMap<&str, usize> = HashMap::new();
    for (index, group) in group_order.iter().enumerate() {
        order.insert(group, index);
    }
    let last = order.len();
    counts.sort_by(|(left, _), (right, _)| {
        let rank = |group: &String| order.get(group.as_str()).copied().unwrap_or(last);
        (rank(left), left).cmp(&(rank(right), right))
    });
    counts
}

/// `facts.rerouted_counts`: how many pre-merge units each class of `identical` re-routed, every class keyed in that order, refusing a class outside it.
fn rerouted_counts<'c>(
    identical: &[&str],
    rerouted: impl IntoIterator<Item = &'c str>,
) -> std::result::Result<Vec<(String, i64)>, BuildError> {
    let mut counts: Vec<(String, i64)> = Vec::new();
    let mut place: HashMap<&str, usize> = HashMap::new();
    for &class_id in identical {
        if !place.contains_key(class_id) {
            place.insert(class_id, counts.len());
            counts.push((class_id.to_owned(), 0));
        }
    }
    for class_id in rerouted {
        let Some(&at) = place.get(class_id) else {
            return Err(value_error(format!(
                "a unit is re-routed out of {class_id}, which is not an ink_identical class"
            )));
        };
        counts[at].1 += 1;
    }
    Ok(counts)
}

fn counted(counts: &[(String, i64)]) -> PyValue {
    PyValue::Dict(
        counts
            .iter()
            .map(|(name, count)| (text(name), PyValue::Int(*count)))
            .collect(),
    )
}

/// `facts.ink_group_from_flags`: the machine-approved pre-merge units a class, keyed in the order of each class's first flagged unit, the unflagged no-verdict units, and the other unflagged units as human units cut into batches of `batch_size`.
fn ink_group<'c>(
    class_rows: impl Iterator<Item = (&'c str, bool)>,
    flags: &str,
    batch_size: i64,
) -> std::result::Result<PyValue, BuildError> {
    let flags = flags.as_bytes();
    let mut machine: Vec<(String, i64)> = Vec::new();
    let mut place: HashMap<&str, usize> = HashMap::new();
    let (mut exempt, mut human, mut total) = (0i64, 0i64, 0usize);
    for (class_id, no_verdict) in class_rows {
        let Some(&flag) = flags.get(total) else {
            return Err(value_error("zip() argument 2 is shorter than argument 1"));
        };
        total += 1;
        if flag == b'1' {
            match place.get(class_id) {
                Some(&at) => machine[at].1 += 1,
                None => {
                    place.insert(class_id, machine.len());
                    machine.push((class_id.to_owned(), 1));
                }
            }
        } else if no_verdict {
            exempt += 1;
        } else {
            human += 1;
        }
    }
    if total != flags.len() {
        return Err(value_error("zip() argument 2 is longer than argument 1"));
    }
    let machine_total: i64 = machine.iter().map(|(_, count)| count).sum();
    Ok(dict(vec![
        ("machine_total", PyValue::Int(machine_total)),
        ("non_identical", PyValue::Int(total as i64 - machine_total)),
        ("by_class", counted(&machine)),
        ("boundary_window_exempt", PyValue::Int(exempt)),
        ("human_units", PyValue::Int(human)),
        (
            "batches",
            PyValue::Int((human + batch_size - 1).div_euclid(batch_size)),
        ),
    ]))
}

/// `facts.built_group_from_memory`: the human units, the distinct windows of the reference window's duplicate group, None when the reference window is no human unit, and the config-note histogram as `[note, count]` pairs, None first and then by note.
fn built_group(
    table: &UnitTable,
    store: &UnitStore,
    reference: &[u32],
) -> std::result::Result<PyValue, BuildError> {
    let mut human = 0i64;
    let mut distribution: HashMap<Option<&str>, i64> = HashMap::new();
    let mut reference_group: Option<&str> = None;
    let mut windows: HashMap<&str, HashSet<Vec<u32>>> = HashMap::new();
    for ordinal in 0..table.n {
        if table.batch(ordinal).is_some() {
            let Some(group) = table.duplicate_group(ordinal) else {
                return Err(failed("AssertionError"));
            };
            human += 1;
            let window = table.codepoints(ordinal);
            if window == reference {
                reference_group = Some(group);
            }
            windows.entry(group).or_default().insert(window);
        }
        *distribution.entry(store.config_note(ordinal)).or_default() += 1;
    }
    let mut notes: Vec<(Option<&str>, i64)> = distribution.into_iter().collect();
    notes.sort_by_key(|&(note, _)| note);
    Ok(dict(vec![
        ("human_units", PyValue::Int(human)),
        (
            "reference_window_duplicate_siblings",
            reference_group.map_or(PyValue::None, |group| {
                PyValue::Int(windows[group].len() as i64)
            }),
        ),
        (
            "config_note_distribution",
            PyValue::List(
                notes
                    .into_iter()
                    .map(|(note, count)| {
                        PyValue::List(vec![PyValue::optional_str(note), PyValue::Int(count)])
                    })
                    .collect(),
            ),
        ),
    ]))
}

/// What the review facts read beside the derived pre-merge facts: the manifest as the build wrote it, the table and the store over every unit, the pre-merge snapshot rebased onto the merge, the audit's row count and the change groups' order.
pub struct FactsParts<'p> {
    pub manifest: &'p PyValue,
    pub table: &'p UnitTable,
    pub store: &'p UnitStore,
    pub snapshot: &'p PremergeSnapshot,
    pub row_count: usize,
    pub group_order: &'p [String],
}

/// `facts.build_facts`: the sidecar's payload, its format, the corpus's identity from the manifest and the pins, the invariant block and the volatile groups, the ink and change-group groups reduced from `premerge`, a change group on a unit whose class is not UNMATCHED counted as re-routed out of that class.
pub fn build_facts(
    parts: &FactsParts<'_>,
    names: &FactsNames,
    premerge: &PremergeFacts,
) -> std::result::Result<PyValue, BuildError> {
    let (manifest, table, snapshot) = (parts.manifest, parts.table, parts.snapshot);
    let rank = ClassRank::of(manifest, parts.group_order)?;
    let strings = &table.strings;
    let unmatched = strings.find(&names.unmatched);
    let identical = rank.sorted(
        snapshot
            .identical_classes
            .iter()
            .map(String::as_str)
            .collect(),
    )?;
    let rerouted = rerouted_counts(
        &identical,
        premerge
            .unmatched_groups
            .iter()
            .map(|&(index, _)| snapshot.class_id[index])
            .filter(|&class_index| Some(class_index) != unmatched)
            .map(|class_index| strings.get(class_index)),
    )?;
    let groups = change_group_counts(
        premerge
            .unmatched_groups
            .iter()
            .map(|(_, group)| group.as_str()),
        parts.group_order,
    );
    let total: i64 = groups.iter().map(|(_, count)| count).sum();
    let unmatched_groups = dict(vec![
        ("counts", counted(&groups)),
        ("total", PyValue::Int(total)),
        ("rerouted", counted(&rerouted)),
    ]);
    let invariant = invariant_group(manifest, &rank, &groups)?;
    Ok(dict(vec![
        ("format", text(&names.format)),
        (
            "corpus",
            dict(vec![
                ("generated_at", item(manifest, "generated_at")?.clone()),
                ("repo_head", item(manifest, "repo_head")?.clone()),
                (
                    "inputs_fingerprint",
                    item(manifest, "inputs_fingerprint")?.clone(),
                ),
            ]),
        ),
        (
            "pins",
            dict(vec![
                ("invariant", invariant),
                (
                    "volatile",
                    dict(vec![
                        ("manifest", manifest_group(manifest, &rank, names)?),
                        (
                            "built",
                            built_group(table, parts.store, &names.reference_window)?,
                        ),
                        (
                            "audit",
                            dict(vec![
                                ("row_count", PyValue::Int(parts.row_count as i64)),
                                ("units", PyValue::Int(snapshot.n as i64)),
                            ]),
                        ),
                        (
                            "ink",
                            ink_group(
                                snapshot.class_rows(table),
                                &premerge.ink_flags,
                                names.batch_size,
                            )?,
                        ),
                        ("unmatched_groups", unmatched_groups),
                    ]),
                ),
            ]),
        ),
    ]))
}

/// `facts.write_facts`: the payload as compact ASCII JSON and a newline, written in place beside the manifest, a file that already holds those bytes left as it is.
pub fn write_facts(
    out_dir: &Path,
    sink: &Sink,
    names: &FactsNames,
    facts: &PyValue,
) -> std::result::Result<(), BuildError> {
    let mut body = json::dumps_compact(facts).map_err(failed)?;
    body.push('\n');
    sink.write_output(&out_dir.join(&names.filename), body.as_bytes(), true)
        .map_err(failed)?;
    Ok(())
}

/// The review-facts phase as `build._build_m1` runs it once the manifest is on disk: derive the pre-merge facts ([`derive_premerge`]), refuse a change group on an ink-identical unit ([`grouped_on_identical`]), then build and write the sidecar ([`build_facts`], [`write_facts`]); return the derived facts.
pub fn review_facts(
    out_dir: &Path,
    sink: &Sink,
    names: &FactsNames,
    parts: &FactsParts<'_>,
) -> std::result::Result<PremergeFacts, BuildError> {
    let premerge = derive_premerge(parts.snapshot, parts.table, parts.store, &names.unmatched)?;
    grouped_on_identical(&premerge)?;
    let facts = build_facts(parts, names, &premerge)?;
    write_facts(out_dir, sink, names, &facts)?;
    Ok(premerge)
}

// --- the stage ----------------------------------------------------------------------------------

/// The keys of the `facts` stage's lines (`units_stage_fixtures.FACTS_*_KEY`) and the prefix of its generated ones.
const PREMERGE_KEY: &str = "premerge";
const FILE_KEY: &str = "review-facts";
const GENERATED: &str = "generated:";

/// Put a fresh copy in place of `path` when it is there, so a write in place reaches no other link to the file it held.
fn unlinked(path: &Path) -> Result<()> {
    let fail = |why: io::Error| WireError(format!("cannot copy {}: {why}", path.display()));
    match fs::read(path) {
        Ok(data) => {
            fs::remove_file(path).map_err(fail)?;
            fs::write(path, data).map_err(fail)
        }
        Err(why) if why.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(why) => Err(fail(why)),
    }
}

/// The `facts` stage: when the stage file holds lines from the build's run, run the build through the write's commit ([`through_write`]), write the manifest over what it holds ([`write_manifest`]) and the review facts after it ([`review_facts`]), and answer with the derived pre-merge facts and the bytes of `review-facts.json` as written; then answer each generated line, a set of pre-merge facts, with the refusal [`grouped_on_identical`] makes of it or None.
pub fn stage_facts(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let manifest_names = ManifestNames::of(&input)?;
    let names = FactsNames::of(&input)?;
    let lines = stage_lines(stage_file)?;
    if lines.ran {
        through_write(&input, stage_file, "facts", |held| {
            let failure = |failure: BuildError| WireError(format!("the facts failed: {failure}"));
            let manifest = held_manifest(&input, &manifest_names, &held).map_err(failure)?;
            let corpus = &held.run.corpus;
            write_manifest(corpus, &manifest, &mut io::sink()).map_err(failure)?;
            let path = corpus.join(&names.filename);
            unlinked(&path)?;
            let parts = FactsParts {
                manifest: &manifest,
                table: &held.run.table,
                store: &held.run.planned.store,
                snapshot: &held.run.premerge,
                row_count: held.run.row_count,
                group_order: &input.group_order,
            };
            let premerge =
                review_facts(corpus, &Sink::new(None), &names, &parts).map_err(failure)?;
            stage_line(out, PREMERGE_KEY, &premerge.to_py())?;
            let written = fs::read(&path)
                .map_err(|why| WireError(format!("cannot read {}: {why}", path.display())))?;
            stage_line(out, FILE_KEY, &PyValue::Bytes(written))
        })?;
    }
    for (key, key_input) in &lines.generated {
        if !key.starts_with(GENERATED) {
            return error(format!("{key}: not a line the facts stage answers"));
        }
        let facts =
            PremergeFacts::from_py(key_input).map_err(|why| WireError(format!("{key}: {why}")))?;
        let value = match grouped_on_identical(&facts) {
            Ok(()) => PyValue::None,
            Err(BuildError::Exit(message)) => PyValue::Str(message),
            Err(failure) => return error(format!("{key}: {failure}")),
        };
        stage_line(out, key, &value)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn facts(flags: &str, groups: &[(usize, &str)]) -> PremergeFacts {
        PremergeFacts {
            ink_flags: flags.to_owned(),
            unmatched_groups: groups
                .iter()
                .map(|&(index, group)| (index, group.to_owned()))
                .collect(),
        }
    }

    #[test]
    fn a_change_group_on_an_ink_identical_unit_is_refused_naming_how_many_and_the_first() {
        assert_eq!(grouped_on_identical(&facts("0101", &[(0, "g")])), Ok(()));
        assert_eq!(
            grouped_on_identical(&facts("0101", &[(0, "g"), (3, "g"), (1, "h")])),
            Err(BuildError::Exit(
                "2 ink-identical pre-merge units carry a change group (first at capture index 3)"
                    .to_owned()
            ))
        );
        assert!(matches!(
            grouped_on_identical(&facts("01", &[(2, "g")])),
            Err(BuildError::Failed(_))
        ));
    }

    #[test]
    fn change_groups_count_in_their_order_then_by_name_and_reroutes_refuse_another_class() {
        let order = ["b".to_owned(), "a".to_owned()];
        assert_eq!(
            change_group_counts(["z", "a", "y", "b", "a"], &order),
            [
                ("b".to_owned(), 1),
                ("a".to_owned(), 2),
                ("y".to_owned(), 1),
                ("z".to_owned(), 1)
            ]
        );
        assert_eq!(
            rerouted_counts(&["c", "d"], ["d", "d"]).expect("counted"),
            [("c".to_owned(), 0), ("d".to_owned(), 2)]
        );
        assert_eq!(
            rerouted_counts(&["c"], ["e"]),
            Err(failed(
                "ValueError: a unit is re-routed out of e, which is not an ink_identical class"
            ))
        );
    }

    #[test]
    fn the_ink_group_counts_flagged_units_by_class_and_batches_the_human_ones() {
        let rows = [
            ("a", false),
            ("b", false),
            ("a", true),
            ("b", false),
            ("c", false),
        ];
        let group = ink_group(rows.into_iter(), "11000", 2).expect("grouped");
        assert_eq!(
            json::dumps(&group).expect("json"),
            r#"{"machine_total": 2, "non_identical": 3, "by_class": {"a": 1, "b": 1}, "boundary_window_exempt": 1, "human_units": 2, "batches": 1}"#
        );
        assert!(ink_group(rows.into_iter(), "1100", 2).is_err());
    }

    #[test]
    fn the_facts_are_written_compact_and_kept_when_equal() {
        let directory =
            std::env::temp_dir().join(format!("ams-review-facts-write-{}", std::process::id()));
        let _ = fs::remove_dir_all(&directory);
        fs::create_dir_all(&directory).expect("scratch");
        let counts = directory.join("counts");
        fs::write(&counts, b"").expect("counts");
        let names = FactsNames {
            filename: "review-facts.json".to_owned(),
            format: "f".to_owned(),
            class_unit_count_keys: Vec::new(),
            reference_window: Vec::new(),
            batch_size: 1,
            unmatched: "UNMATCHED".to_owned(),
        };
        let value = dict(vec![
            ("note", text("\u{b7}Pea")),
            ("pairs", PyValue::List(vec![PyValue::None, PyValue::Int(2)])),
        ]);
        let sink = Sink::new(Some(counts.clone()));
        for _ in 0..2 {
            write_facts(&directory, &sink, &names, &value).expect("written");
        }
        assert_eq!(
            fs::read_to_string(directory.join("review-facts.json")).expect("facts"),
            "{\"note\":\"\\u00b7Pea\",\"pairs\":[null,2]}\n"
        );
        assert_eq!(
            fs::read_to_string(&counts).expect("counts"),
            "changed\t38\nkept\t38\n"
        );
        let _ = fs::remove_dir_all(&directory);
    }
}
