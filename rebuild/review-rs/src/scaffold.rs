//! A fragment's scaffold, as `rebuild/review/build.py` builds it: `unit_scaffold`, the config badge (`config_badge`, `_config_badge`, `_candidate_constraint_sets`, `_gate_clauses`, `_config_features`), the class note (`_config_class_note`), and the cluster id (`_cluster_id_from_repr`).
//!
//! [`stage_scaffold`] is the `scaffold` stage of the stage fixtures.

use std::collections::BTreeSet;
use std::fmt;
use std::io::{BufRead, Write};
use std::path::Path;

use ams_m1_kernel::emit::json_string;
use ams_m1_kernel::index::SpecIndex;
use serde_json::Value;

use crate::change_groups::{
    ChangeGroupError, ChangeGroupInput, UNMATCHED, assign_change_group, config_features, reroutes,
};
use crate::enrich::{EnrichError, Enricher, Flags};
use crate::fontfacts::FontFacts;
use crate::ink::{InkError, InkReader, sha1_hex};
use crate::pyfmt::PyValue;
use crate::pyfmt::repr::repr_str;
use crate::settle::{SettleError, Settlement, Settler, features_for_config};
use crate::shape::{Fonts, ShapeError, Shaper};
use crate::subset_pack::{SubsetPack, SubsetPackError};
use crate::wire::{Init, UnitRecord, WireError};

/// The vocabulary name of `audit.ACCEPTANCE_CONFIGS`, the configurations a unit can hold, which the badge resolves a unit's configurations against and `_config_index` orders them by.
pub const ACCEPTANCE_CONFIGS_NAME: &str = "rebuild.review.audit.ACCEPTANCE_CONFIGS";
/// The vocabulary name of `audit.WINDOW_SCOPED_CONFIGS`, the configurations a unit holds only when its window spells one of their letters, which [`Roster::window`] drops from the badge's configurations for any other window.
pub const WINDOW_SCOPED_CONFIGS_NAME: &str = "rebuild.review.audit.WINDOW_SCOPED_CONFIGS";

/// `build.GATE_CONSTRAINT_CAP`: the most on/off constraints a config gate holds.
pub const GATE_CONSTRAINT_CAP: usize = 3;

/// A scaffold the engine cannot build as Python builds it, or a `scaffold` stage input it cannot read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ScaffoldError(pub String);

impl fmt::Display for ScaffoldError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ScaffoldError {}

macro_rules! from_error {
    ($($kind:ty),*) => {
        $(impl From<$kind> for ScaffoldError {
            fn from(error: $kind) -> Self {
                Self(error.0)
            }
        })*
    };
}

from_error!(
    ChangeGroupError,
    EnrichError,
    InkError,
    SettleError,
    ShapeError,
    SubsetPackError,
    WireError
);

type Result<T> = std::result::Result<T, ScaffoldError>;

fn error<T>(message: impl Into<String>) -> Result<T> {
    Err(ScaffoldError(message.into()))
}

/// The init's `ACCEPTANCE_CONFIGS`, which must be a tuple of `str`.
pub fn acceptance_configs(init: &Init) -> Result<Vec<String>> {
    let Some(PyValue::Tuple(items)) = init.constant(ACCEPTANCE_CONFIGS_NAME) else {
        return error(format!(
            "the init's {ACCEPTANCE_CONFIGS_NAME} is not a tuple"
        ));
    };
    items
        .iter()
        .map(|item| match item {
            PyValue::Str(config) => Ok(config.clone()),
            other => error(format!(
                "the init's {ACCEPTANCE_CONFIGS_NAME} holds {}, not a str",
                other.kind()
            )),
        })
        .collect()
}

/// The configurations a unit can hold, `audit.ACCEPTANCE_CONFIGS`, and the window-scoped ones among them, `audit.WINDOW_SCOPED_CONFIGS`, each with the code points a window must spell for a unit of it to hold that configuration.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Roster {
    pub acceptance: Vec<String>,
    pub scoped: Vec<(String, Vec<String>)>,
}

impl Roster {
    /// The init's `ACCEPTANCE_CONFIGS` and `WINDOW_SCOPED_CONFIGS`, the latter a tuple of (configuration, tuple of code point strings) pairs.
    pub fn read(init: &Init) -> Result<Self> {
        let Some(PyValue::Tuple(items)) = init.constant(WINDOW_SCOPED_CONFIGS_NAME) else {
            return error(format!(
                "the init's {WINDOW_SCOPED_CONFIGS_NAME} is not a tuple"
            ));
        };
        let mut scoped = Vec::with_capacity(items.len());
        for item in items {
            let PyValue::Tuple(pair) = item else {
                return error(format!(
                    "the init's {WINDOW_SCOPED_CONFIGS_NAME} holds {}, not a pair",
                    item.kind()
                ));
            };
            let [PyValue::Str(config), PyValue::Tuple(letters)] = pair.as_slice() else {
                return error(format!(
                    "the init's {WINDOW_SCOPED_CONFIGS_NAME} holds a pair that is not (str, tuple)"
                ));
            };
            let letters = letters
                .iter()
                .map(|letter| match letter {
                    PyValue::Str(letter) => Ok(letter.clone()),
                    other => error(format!(
                        "the init's {WINDOW_SCOPED_CONFIGS_NAME} names {}, not a str, as a letter of {config}",
                        other.kind()
                    )),
                })
                .collect::<Result<_>>()?;
            scoped.push((config.clone(), letters));
        }
        Ok(Self {
            acceptance: acceptance_configs(init)?,
            scoped,
        })
    }

    /// `audit.window_configs`: the configurations a unit of the window `codepoints` (colon-separated) can hold, `acceptance` less each window-scoped configuration none of whose letters the window spells.
    pub fn window(&self, codepoints: &str) -> Vec<String> {
        let letters: Vec<&str> = codepoints.split(':').collect();
        let dropped: Vec<&str> = self
            .scoped
            .iter()
            .filter(|(_, scope)| {
                !scope
                    .iter()
                    .any(|letter| letters.contains(&letter.as_str()))
            })
            .map(|(config, _)| config.as_str())
            .collect();
        self.acceptance
            .iter()
            .filter(|config| !dropped.contains(&config.as_str()))
            .cloned()
            .collect()
    }
}

/// One clause of a config gate: a feature, whether it is `on` or `off`, and the text the app prints for it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Clause {
    pub feature: String,
    pub state: &'static str,
    pub text: String,
}

impl Clause {
    /// The clause as Python holds it, a dict in `_gate_clauses`' key order.
    pub fn to_py(&self) -> PyValue {
        PyValue::Dict(vec![
            (PyValue::str("feature"), PyValue::str(&self.feature)),
            (PyValue::str("state"), PyValue::str(self.state)),
            (PyValue::str("text"), PyValue::str(&self.text)),
        ])
    }
}

/// A unit's config badge, `config_badge`'s `(gate, note)`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Badge {
    pub gate: Option<Vec<Clause>>,
    pub note: Option<String>,
}

impl Badge {
    /// The gate as Python holds it: a list of clause dicts, or None.
    pub fn gate_py(&self) -> PyValue {
        self.gate.as_ref().map_or(PyValue::None, |clauses| {
            PyValue::List(clauses.iter().map(Clause::to_py).collect())
        })
    }

    /// The note as Python holds it: a `str`, or None.
    pub fn note_py(&self) -> PyValue {
        PyValue::optional_str(self.note.as_deref())
    }
}

/// `itertools.combinations(range(n), k)` in its order.
fn combinations(n: usize, k: usize) -> Vec<Vec<usize>> {
    let mut out = Vec::new();
    if k > n {
        return out;
    }
    let mut indices: Vec<usize> = (0..k).collect();
    loop {
        out.push(indices.clone());
        let Some(position) = (0..k)
            .rev()
            .find(|&position| indices[position] != position + n - k)
        else {
            return out;
        };
        indices[position] += 1;
        for next in position + 1..k {
            indices[next] = indices[next - 1] + 1;
        }
    }
}

/// `_candidate_constraint_sets`: every assignment of on/off to `size` of the `tags`, most-on first and then in tag order, each as the dict's `(tag, on)` items in the combination's order.
fn candidate_constraint_sets<'t>(tags: &[&'t str], size: usize) -> Vec<Vec<(&'t str, bool)>> {
    let mut sets = Vec::new();
    for on_count in (0..=size).rev() {
        for combination in combinations(tags.len(), size) {
            for on in combinations(size, on_count) {
                sets.push(
                    combination
                        .iter()
                        .enumerate()
                        .map(|(slot, &tag)| (tags[tag], on.contains(&slot)))
                        .collect(),
                );
            }
        }
    }
    sets
}

/// `_gate_clauses`: a resolved constraint mapping as the badge's ordered clauses, on before off and each group in tag order; a lone ss10-on gate reads "only under ss10".
fn gate_clauses(constraints: &[(&str, bool)]) -> Vec<Clause> {
    let mut sorted = constraints.to_vec();
    sorted.sort();
    let ordered: Vec<(&str, &'static str)> = [true, false]
        .into_iter()
        .flat_map(|wanted| {
            sorted
                .iter()
                .filter(move |(_, on)| *on == wanted)
                .map(|&(tag, on)| (tag, if on { "on" } else { "off" }))
        })
        .collect();
    if ordered == [("ss10", "on")] {
        return vec![Clause {
            feature: "ss10".to_owned(),
            state: "on",
            text: "only under ss10".to_owned(),
        }];
    }
    ordered
        .into_iter()
        .enumerate()
        .map(|(index, (tag, state))| Clause {
            feature: tag.to_owned(),
            state,
            text: format!(
                "{} {tag} is {state}",
                if index == 0 { "only when" } else { "and" }
            ),
        })
        .collect()
}

/// `config_badge`: the smallest conjunction of at most [`GATE_CONSTRAINT_CAP`] feature on/off constraints that selects exactly the unit's configurations within `full_configs`, as the gate and its clauses' texts joined as the note; neither when the unit covers every non-ss10 configuration; and the literal `only under: <set>` note with no gate when no conjunction does. A set that holds no ss10 configuration is resolved against the non-ss10 configurations alone.
pub fn config_badge(unit_configs: &[String], full_configs: &[String]) -> Badge {
    let covered: BTreeSet<&str> = unit_configs.iter().map(String::as_str).collect();
    let isolated = |config: &str| config_features(config).contains("ss10");
    let non_isolated: Vec<&str> = full_configs
        .iter()
        .map(String::as_str)
        .filter(|config| !isolated(config))
        .collect();
    if non_isolated.iter().all(|config| covered.contains(config)) {
        return Badge {
            gate: None,
            note: None,
        };
    }
    let universe: Vec<&str> = if covered.iter().any(|config| isolated(config)) {
        full_configs.iter().map(String::as_str).collect()
    } else {
        non_isolated
    };
    let tags: Vec<&str> = universe
        .iter()
        .flat_map(|config| config_features(config))
        .collect::<BTreeSet<&str>>()
        .into_iter()
        .collect();
    for size in 1..=GATE_CONSTRAINT_CAP.min(tags.len()) {
        for constraints in candidate_constraint_sets(&tags, size) {
            let selected: BTreeSet<&str> = universe
                .iter()
                .copied()
                .filter(|config| {
                    let features = config_features(config);
                    constraints
                        .iter()
                        .all(|(tag, on)| features.contains(tag) == *on)
                })
                .collect();
            if selected == covered {
                let clauses = gate_clauses(&constraints);
                let note = clauses
                    .iter()
                    .map(|clause| clause.text.as_str())
                    .collect::<Vec<_>>()
                    .join(" ");
                return Badge {
                    gate: Some(clauses),
                    note: Some(note),
                };
            }
        }
    }
    Badge {
        gate: None,
        note: Some(format!("only under: {}", unit_configs.join(", "))),
    }
}

/// `audit._config_index`: a configuration's place in `acceptance`, or past the end for one outside it.
fn config_index(acceptance: &[String], config: &str) -> usize {
    acceptance
        .iter()
        .position(|held| held == config)
        .unwrap_or(acceptance.len())
}

/// `_config_class_note`: for a unit UNMATCHED under some configurations and of a ledger class under others, `blessed as <class> under <configs>` for each class in the order its first configuration takes, then `novel under <configs>`, each list in `acceptance` order; None for a unit of one class under every configuration.
pub fn config_class_note(
    config_classes: &[(String, String)],
    acceptance: &[String],
) -> Option<String> {
    let mut novel: Vec<&str> = Vec::new();
    let mut blessed: Vec<&str> = Vec::new();
    for (config, class) in config_classes {
        if class == UNMATCHED {
            novel.push(config);
        } else {
            blessed.push(config);
        }
    }
    if novel.is_empty() || blessed.is_empty() {
        return None;
    }
    blessed.sort_by_key(|config| config_index(acceptance, config));
    novel.sort_by_key(|config| config_index(acceptance, config));
    let mut by_class: Vec<(&str, Vec<&str>)> = Vec::new();
    for config in blessed {
        let class = config_classes
            .iter()
            .rev()
            .find(|(held, _)| held == config)
            .map_or("", |(_, class)| class.as_str());
        match by_class.iter_mut().find(|(held, _)| *held == class) {
            Some((_, configs)) => configs.push(config),
            None => by_class.push((class, vec![config])),
        }
    }
    let blessed_phrase = by_class
        .iter()
        .map(|(class, configs)| format!("blessed as {class} under {}", configs.join(", ")))
        .collect::<Vec<_>>()
        .join("; ");
    Some(format!(
        "{blessed_phrase}; novel under {}",
        novel.join(", ")
    ))
}

fn str_list(items: &[String]) -> PyValue {
    PyValue::List(items.iter().map(PyValue::str).collect())
}

fn str_dict(pairs: &[(String, String)]) -> PyValue {
    PyValue::Dict(
        pairs
            .iter()
            .map(|(key, value)| (PyValue::str(key), PyValue::str(value)))
            .collect(),
    )
}

/// `build.unit_scaffold` with its default `full_configs`, the roster's `acceptance`, its badge resolved against the configurations the unit's window can hold (`Roster::window`): the fragment fields the build derives from the workload, a dict in its literal order, with the unit's phase-1 `flags` and the comparator's `ink_deltas`.
pub fn unit_scaffold(
    unit: &UnitRecord,
    flags: &Flags,
    ink_deltas: &[(String, String)],
    roster: &Roster,
) -> PyValue {
    let badge = config_badge(&unit.configs, &roster.window(&unit.codepoints));
    let optional = |value: &Option<String>| PyValue::optional_str(value.as_deref());
    let entries = vec![
        ("id", PyValue::str(&unit.unit_id)),
        ("ink_identical", PyValue::Bool(flags.ink_identical)),
        ("picture_identical", PyValue::Bool(flags.picture_identical)),
        ("junior_equivalent", PyValue::Bool(flags.junior_equivalent)),
        ("ink_deltas", str_dict(ink_deltas)),
        ("no_verdict", PyValue::Bool(unit.no_verdict)),
        ("duplicate_group", optional(&unit.duplicate_group)),
        ("cluster", optional(&unit.cluster)),
        ("class", PyValue::str(&unit.class)),
        (
            "ledger_class",
            if unit.ledger_class.is_empty() {
                PyValue::None
            } else {
                PyValue::str(&unit.ledger_class)
            },
        ),
        ("group", PyValue::str(&unit.group)),
        ("codepoints", PyValue::str(&unit.codepoints)),
        ("configs", str_list(&unit.configs)),
        ("config_note", badge.note_py()),
        ("config_gate", badge.gate_py()),
        (
            "config_classes",
            if unit.config_classes.is_empty() {
                PyValue::None
            } else {
                str_dict(&unit.config_classes)
            },
        ),
        (
            "config_class_note",
            PyValue::optional_str(
                config_class_note(&unit.config_classes, &roster.acceptance).as_deref(),
            ),
        ),
        (
            "render_groups",
            PyValue::List(
                unit.render_groups
                    .iter()
                    .map(|group| PyValue::Dict(vec![(PyValue::str("configs"), str_list(group))]))
                    .collect(),
            ),
        ),
        ("kinds", str_list(&unit.kinds)),
        ("exemplar", PyValue::Bool(unit.exemplar)),
    ];
    PyValue::Dict(
        entries
            .into_iter()
            .map(|(key, value)| (PyValue::str(key), value))
            .collect(),
    )
}

/// `build._cluster_id_from_repr`: `c-` and the first eight hex digits of the sha1 of `repr((tuple(configs), class_id, diffs))`, given the diffs' repr.
pub fn cluster_id_from_repr(configs: &[String], class_id: &str, diffs_repr: &str) -> String {
    let mut key = String::with_capacity(diffs_repr.len() + 64);
    key.push('(');
    key.push('(');
    for (index, config) in configs.iter().enumerate() {
        if index > 0 {
            key.push_str(", ");
        }
        key.push_str(&repr_str(config));
    }
    if configs.len() == 1 {
        key.push(',');
    }
    key.push_str("), ");
    key.push_str(&repr_str(class_id));
    key.push_str(", ");
    key.push_str(diffs_repr);
    key.push(')');
    format!("c-{}", &sha1_hex(key.as_bytes())[..8])
}

fn line_ink_deltas(record: &Value, key: &str) -> Result<Vec<(String, String)>> {
    let value = PyValue::from_typed(&record["ink_deltas"])
        .map_err(|failure| ScaffoldError(format!("{key}: the ink deltas: {failure}")))?;
    let PyValue::Dict(entries) = value else {
        return error(format!("{key}: the ink deltas are not a dict"));
    };
    entries
        .into_iter()
        .map(|entry| match entry {
            (PyValue::Str(config), PyValue::Str(digest)) => Ok((config, digest)),
            _ => error(format!(
                "{key}: the ink deltas hold an entry that is not two str"
            )),
        })
        .collect()
}

fn line_final_class(record: &Value, key: &str) -> Result<Option<String>> {
    match PyValue::from_typed(&record["final_class"])
        .map_err(|failure| ScaffoldError(format!("{key}: the final class: {failure}")))?
    {
        PyValue::None => Ok(None),
        PyValue::Str(class) => Ok(Some(class)),
        other => error(format!(
            "{key}: the final class is {}, not a str or None",
            other.kind()
        )),
    }
}

fn line_configs(record: &Value, key: &str) -> Result<Vec<String>> {
    let PyValue::Tuple(items) = PyValue::from_typed(&record["configs"])
        .map_err(|failure| ScaffoldError(format!("{key}: the configs: {failure}")))?
    else {
        return error(format!("{key}: the configs are not a tuple"));
    };
    items
        .into_iter()
        .map(|item| match item {
            PyValue::Str(config) => Ok(config),
            other => error(format!(
                "{key}: the configs hold {}, not a str",
                other.kind()
            )),
        })
        .collect()
}

/// The `scaffold` stage. A `scaffold:` line's unit, which its `record` carries as phase 1 received it, goes through phase 1 up to its fragment: its ink is read and checked first against the line's `ink_deltas`, which the worker's comparator found, so a failure there names the ink stage; the unit is re-routed (`reroutes`), which sets its ledger class and clears its exemplar flag, and enriched under its first configuration; and the line's answer is a dict of the `scaffold` `unit_scaffold` returns, the `change_group` of an UNMATCHED or re-routed unit (empty for any other), and the `cluster` over the unit's configurations, the line's `final_class` or else the unit's class, and the diffs' repr. A `badge:` line's answer is the `gate` and the `note` `config_badge` gives its `configs` against `ACCEPTANCE_CONFIGS`. The shaper's and the settler's memos carry from line to line as a batch's do.
pub fn stage_scaffold(init_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let init = Init::read(init_path)?;
    let roster = Roster::read(&init)?;
    let facts = FontFacts::read(&init.font_facts)?;
    let fonts = Fonts::open(&init, &facts)?;
    let reader = InkReader::new(&init, &facts)?;
    let settlement = Settlement::new(SpecIndex::new(init.read_spec()?), init.settlement.clone())?;
    let mut settler = Settler::new(&settlement);
    let pack = SubsetPack::open(&init.subset_pack)?;
    let enricher = Enricher::new(&init, &settlement, &pack, &facts)?;
    let mut shaper = Shaper::new(&fonts, &facts);
    let file = std::fs::File::open(stage_file).map_err(|failure| {
        ScaffoldError(format!("cannot read {}: {failure}", stage_file.display()))
    })?;
    for line in std::io::BufReader::new(file).lines() {
        let line = line.map_err(|failure| {
            ScaffoldError(format!("cannot read {}: {failure}", stage_file.display()))
        })?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| ScaffoldError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return error(format!("a stage line has no key: {line:.200}"));
        };
        let value = if key.starts_with("scaffold:") {
            let Some(text) = record["record"].as_str() else {
                return error(format!("{key}: the line has no unit record"));
            };
            let unit: Value = serde_json::from_str(text)
                .map_err(|failure| ScaffoldError(format!("{key}: the unit record: {failure}")))?;
            let mut unit = UnitRecord::from_json(&unit)?;
            let ink = reader.read_unit(&mut shaper, &unit)?;
            let recorded = line_ink_deltas(&record, key)?;
            if ink.ink_deltas != recorded {
                return error(format!(
                    "{key}: the ink stage reads the ink deltas {:?}, and the worker's comparator found {recorded:?}",
                    ink.ink_deltas
                ));
            }
            let flags = Flags {
                ink_identical: ink.ink_identical,
                picture_identical: ink.picture_identical,
                junior_equivalent: ink.junior_equivalent,
            };
            let machine_approved =
                flags.ink_identical || flags.picture_identical || flags.junior_equivalent;
            let rerouted = reroutes(
                &unit.class,
                unit.no_verdict,
                machine_approved,
                &init.identical_classes,
            );
            unit.ledger_class = if rerouted {
                unit.class.clone()
            } else {
                String::new()
            };
            if rerouted {
                unit.exemplar = false;
            }
            let config = unit.configs.first().map_or("", String::as_str);
            let report = settler
                .explain(&unit.codepoint_values, &features_for_config(config))
                .map_err(|failure| ScaffoldError(format!("{key}: the settle stage: {failure}")))?;
            let enriched = enricher
                .enrich(&mut shaper, &unit, &flags, &report)
                .map_err(|failure| ScaffoldError(format!("{key}: the enrich stage: {failure}")))?;
            let change_group = if rerouted || unit.class == UNMATCHED {
                let geometry = &enriched.geometry;
                assign_change_group(
                    &ChangeGroupInput {
                        configs: &unit.configs,
                        config_classes: &unit.config_classes,
                        before_junctions: &geometry.before_junctions,
                        after_junctions: &geometry.after_junctions,
                        after_cells: &geometry.after_cells,
                        advance_before: geometry.highlight_before.advance_total,
                        advance_after: geometry.highlight_after.advance_total,
                    },
                    &ink.ink_deltas,
                    &init.identical_classes,
                )
                .map_err(|failure| ScaffoldError(format!("{key}: {failure}")))?
            } else {
                ""
            };
            let final_class = line_final_class(&record, key)?;
            let cluster = cluster_id_from_repr(
                &unit.configs,
                final_class.as_deref().unwrap_or(&unit.class),
                &ink.diffs_repr,
            );
            PyValue::Dict(vec![
                (
                    PyValue::str("scaffold"),
                    unit_scaffold(&unit, &flags, &ink.ink_deltas, &roster),
                ),
                (PyValue::str("change_group"), PyValue::str(change_group)),
                (PyValue::str("cluster"), PyValue::str(cluster)),
            ])
        } else if key.starts_with("badge:") {
            let badge = config_badge(&line_configs(&record, key)?, &roster.acceptance);
            PyValue::Dict(vec![
                (PyValue::str("gate"), badge.gate_py()),
                (PyValue::str("note"), badge.note_py()),
            ])
        } else {
            return error(format!(
                "{key}: the scaffold stage answers scaffold: and badge: lines"
            ));
        };
        writeln!(
            out,
            "{{\"key\":{},\"value\":{}}}",
            json_string(key),
            value.to_typed()
        )
        .map_err(|failure| ScaffoldError(format!("cannot write a stage line: {failure}")))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn texts(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| (*item).to_owned()).collect()
    }

    fn acceptance() -> Vec<String> {
        texts(&["default", "ss03", "ss04", "ss05", "ss03+ss05", "ss10"])
    }

    #[test]
    fn combinations_come_in_itertools_order() {
        assert_eq!(
            combinations(4, 2),
            [[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]]
        );
        assert_eq!(combinations(2, 0), [Vec::<usize>::new()]);
        assert!(combinations(1, 2).is_empty());
    }

    #[test]
    fn constraint_sets_come_most_on_first_then_in_tag_order() {
        assert_eq!(
            candidate_constraint_sets(&["a", "b"], 1),
            [
                vec![("a", true)],
                vec![("b", true)],
                vec![("a", false)],
                vec![("b", false)]
            ]
        );
    }

    #[test]
    fn a_badge_names_the_smallest_gate_or_falls_back_to_the_set() {
        let badge = |configs: &[&str]| config_badge(&texts(configs), &acceptance());
        assert_eq!(
            badge(&["default", "ss03", "ss04", "ss05", "ss03+ss05"]),
            Badge {
                gate: None,
                note: None
            }
        );
        assert_eq!(
            badge(&["ss03", "ss03+ss05"]).note.as_deref(),
            Some("only when ss03 is on")
        );
        assert_eq!(badge(&["ss10"]).note.as_deref(), Some("only under ss10"));
        assert_eq!(
            badge(&["ss03"]).note.as_deref(),
            Some("only when ss03 is on and ss05 is off")
        );
        assert_eq!(
            badge(&["default"]).note.as_deref(),
            Some("only when ss03 is off and ss04 is off and ss05 is off")
        );
        let fallback = badge(&["default", "ss03+ss05"]);
        assert_eq!(fallback.gate, None);
        assert_eq!(
            fallback.note.as_deref(),
            Some("only under: default, ss03+ss05")
        );
    }

    #[test]
    fn a_window_without_a_scoped_configurations_letters_cannot_hold_it() {
        let with = texts(&[
            "default",
            "ss03",
            "ss04",
            "ss05",
            "ss03+ss05",
            "ss07",
            "ss10",
        ]);
        let without = acceptance();
        let roster = Roster {
            acceptance: with.clone(),
            scoped: vec![("ss07".to_owned(), vec!["E67C".to_owned()])],
        };
        assert_eq!(roster.window("E650:E651"), without);
        assert_eq!(roster.window("E650:E67C"), with);
        assert_eq!(
            config_badge(
                &texts(&["default", "ss04", "ss05", "ss10"]),
                &roster.window("E650")
            ),
            config_badge(&texts(&["default", "ss04", "ss05", "ss10"]), &without),
            "a window without ·Owe reads as it did before ss07 joined the roster"
        );
    }

    #[test]
    fn the_class_note_lists_blessed_classes_then_novel_configs_in_acceptance_order() {
        let classes = vec![
            ("ss05".to_owned(), "UNMATCHED".to_owned()),
            ("ss03+ss05".to_owned(), "tea".to_owned()),
            ("default".to_owned(), "UNMATCHED".to_owned()),
            ("ss03".to_owned(), "tea".to_owned()),
            ("ss04".to_owned(), "it".to_owned()),
        ];
        assert_eq!(
            config_class_note(&classes, &acceptance()).as_deref(),
            Some(
                "blessed as tea under ss03, ss03+ss05; blessed as it under ss04; novel under default, ss05"
            )
        );
        assert_eq!(config_class_note(&classes[1..2], &acceptance()), None);
        assert_eq!(config_class_note(&[], &acceptance()), None);
    }

    #[test]
    fn the_cluster_hashes_the_tuple_repr_python_writes() {
        let key = "(('default',), 'UNMATCHED', (((), (), 0),))";
        assert_eq!(
            cluster_id_from_repr(&texts(&["default"]), "UNMATCHED", "(((), (), 0),)"),
            format!("c-{}", &sha1_hex(key.as_bytes())[..8])
        );
        let two = "(('default', 'ss03'), 'x', ())";
        assert_eq!(
            cluster_id_from_repr(&texts(&["default", "ss03"]), "x", "()"),
            format!("c-{}", &sha1_hex(two.as_bytes())[..8])
        );
    }
}
