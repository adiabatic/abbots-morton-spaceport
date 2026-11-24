//! A fragment's scaffold ([`unit_scaffold`]): the config badge ([`config_badge`]), the class note ([`config_class_note`]) and the cluster id ([`cluster_id_from_repr`]).

use std::collections::BTreeSet;
use std::fmt;

use crate::change_groups::{ChangeGroupError, UNMATCHED, config_features};
use crate::enrich::{EnrichError, Flags};
use crate::ink::{InkError, sha1};
use crate::pyfmt::repr::repr_str;
use crate::pyfmt::{PyValue, push_lower_hex};
use crate::settle::SettleError;
use crate::shape::ShapeError;
use crate::subset_pack::SubsetPackError;
use crate::wire::{Init, UnitRecord, WireError};

/// The vocabulary name of `audit.ACCEPTANCE_CONFIGS`, the configurations a unit can hold, which the badge resolves a unit's configurations against and the class note orders them by.
pub const ACCEPTANCE_CONFIGS_NAME: &str = "rebuild.review.audit.ACCEPTANCE_CONFIGS";
/// The vocabulary name of `audit.WINDOW_SCOPED_CONFIGS`, the configurations a unit holds only when its window spells one of their letters, which [`Roster::window`] drops from the badge's configurations for any other window.
pub const WINDOW_SCOPED_CONFIGS_NAME: &str = "rebuild.review.audit.WINDOW_SCOPED_CONFIGS";

/// `build.GATE_CONSTRAINT_CAP`: the most on/off constraints a config gate holds.
pub const GATE_CONSTRAINT_CAP: usize = 3;

/// A scaffold the engine cannot build.
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

    /// The configurations a unit of the window `codepoints` (colon-separated) can hold, `acceptance` less each window-scoped configuration none of whose letters the window spells.
    pub fn window(&self, codepoints: &str) -> Vec<String> {
        self.window_configs(codepoints)
            .into_iter()
            .map(str::to_owned)
            .collect()
    }

    /// [`Roster::window`]'s configurations, borrowed from `acceptance`.
    fn window_configs(&self, codepoints: &str) -> Vec<&str> {
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
            .map(String::as_str)
            .filter(|config| !dropped.contains(config))
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
    let full: Vec<&str> = full_configs.iter().map(String::as_str).collect();
    badge_within(unit_configs, &full)
}

/// [`config_badge`] over `full_configs` borrowed.
fn badge_within(unit_configs: &[String], full_configs: &[&str]) -> Badge {
    let covered: BTreeSet<&str> = unit_configs.iter().map(String::as_str).collect();
    let isolated = |config: &str| config != "default" && config.split('+').any(|tag| tag == "ss10");
    let non_isolated: Vec<&str> = full_configs
        .iter()
        .copied()
        .filter(|config| !isolated(config))
        .collect();
    if non_isolated.iter().all(|config| covered.contains(config)) {
        return Badge {
            gate: None,
            note: None,
        };
    }
    let universe: Vec<&str> = if covered.iter().any(|config| isolated(config)) {
        full_configs.to_vec()
    } else {
        non_isolated
    };
    let tags: Vec<&str> = universe
        .iter()
        .flat_map(|config| config_features(config))
        .collect::<BTreeSet<&str>>()
        .into_iter()
        .collect();
    let mut distinct: Vec<&str> = Vec::with_capacity(universe.len());
    for config in &universe {
        if !distinct.contains(config) {
            distinct.push(config);
        }
    }
    let found = if tags.len() <= 64 && distinct.len() <= 64 {
        gate_by_masks(&covered, &distinct, &tags)
    } else {
        gate_by_sets(&covered, &universe, &tags)
    };
    if let Some(constraints) = found {
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
    Badge {
        gate: None,
        note: Some(format!("only under: {}", unit_configs.join(", "))),
    }
}

/// The first constraint set, in `config_badge`'s order (sizes up to [`GATE_CONSTRAINT_CAP`], each in [`candidate_constraint_sets`]' order), whose selection from `universe` is the `covered` set, with each configuration and each candidate held as a bit mask: a configuration's features over `tags` (at most 64, sorted), and the configurations a candidate selects over `universe` (at most 64, distinct). A configuration a candidate selects is one whose features hold each of the candidate's tags exactly when the candidate turns it on, so it is chosen when its features masked by the candidate's tags are the candidate's on tags.
fn gate_by_masks<'t>(
    covered: &BTreeSet<&str>,
    universe: &[&str],
    tags: &[&'t str],
) -> Option<Vec<(&'t str, bool)>> {
    if covered.iter().any(|config| !universe.contains(config)) {
        return None;
    }
    let bit = |tag: &str| {
        tags.iter()
            .position(|held| *held == tag)
            .map_or(0, |at| 1u64 << at)
    };
    let features: Vec<u64> = universe
        .iter()
        .map(|config| {
            config_features(config)
                .into_iter()
                .map(bit)
                .fold(0, |mask, tag| mask | tag)
        })
        .collect();
    let covered_mask = universe
        .iter()
        .enumerate()
        .filter(|(_, config)| covered.contains(*config))
        .fold(0u64, |mask, (at, _)| mask | (1 << at));
    for size in 1..=GATE_CONSTRAINT_CAP.min(tags.len()) {
        let combinations_of_tags = combinations(tags.len(), size);
        for on_count in (0..=size).rev() {
            let ons = combinations(size, on_count);
            for combination in &combinations_of_tags {
                let mask = combination
                    .iter()
                    .fold(0u64, |mask, &tag| mask | (1 << tag));
                for on in &ons {
                    let wanted = on
                        .iter()
                        .fold(0u64, |wanted, &slot| wanted | (1 << combination[slot]));
                    let selected = features
                        .iter()
                        .enumerate()
                        .filter(|&(_, held)| held & mask == wanted)
                        .fold(0u64, |selected, (at, _)| selected | (1 << at));
                    if selected == covered_mask {
                        return Some(
                            combination
                                .iter()
                                .enumerate()
                                .map(|(slot, &tag)| (tags[tag], on.contains(&slot)))
                                .collect(),
                        );
                    }
                }
            }
        }
    }
    None
}

/// The first constraint set, in `config_badge`'s order, whose selection from `universe` is the `covered` set, each candidate's selection built as a set of configurations.
fn gate_by_sets<'t>(
    covered: &BTreeSet<&str>,
    universe: &[&str],
    tags: &[&'t str],
) -> Option<Vec<(&'t str, bool)>> {
    for size in 1..=GATE_CONSTRAINT_CAP.min(tags.len()) {
        for constraints in candidate_constraint_sets(tags, size) {
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
            if selected == *covered {
                return Some(constraints);
            }
        }
    }
    None
}

/// `config_badge` as it builds each candidate's selection as a set, for the tests to hold [`config_badge`] to.
#[cfg(test)]
fn config_badge_by_sets(unit_configs: &[String], full_configs: &[String]) -> Badge {
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

/// A configuration's place in `acceptance`, or past the end for one outside it.
fn config_index(acceptance: &[String], config: &str) -> usize {
    acceptance
        .iter()
        .position(|held| held == config)
        .unwrap_or(acceptance.len())
}

/// For a unit UNMATCHED under some configurations and of a ledger class under others, `blessed as <class> under <configs>` for each class in the order its first configuration takes, then `novel under <configs>`, each list in `acceptance` order; None for a unit of one class under every configuration.
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

/// The unit's scaffold, with the roster's `acceptance` as its full configurations and its badge resolved against the configurations the unit's window can hold (`Roster::window`): the fragment fields the build derives from the workload, a dict in its fixed key order, with the unit's phase-1 `flags` and the comparator's `ink_deltas`.
pub fn unit_scaffold(
    unit: &UnitRecord,
    flags: &Flags,
    ink_deltas: &[(String, String)],
    roster: &Roster,
) -> PyValue {
    let badge = badge_within(&unit.configs, &roster.window_configs(&unit.codepoints));
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

/// `c-` and the first eight hex digits of the sha1 of `repr((tuple(configs), class_id, diffs))`, given the diffs' repr.
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
    let mut id = String::with_capacity(10);
    id.push_str("c-");
    push_lower_hex(&mut id, &sha1(key.as_bytes())[..4]);
    id
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ink::sha1_hex;

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
    fn the_masked_badge_search_finds_what_the_set_search_finds() {
        let rosters = [
            texts(&[
                "default",
                "ss03",
                "ss04",
                "ss05",
                "ss03+ss05",
                "ss07",
                "ss10",
            ]),
            acceptance(),
            texts(&[
                "default",
                "ss03",
                "ss03",
                "ss10+ss03",
                "ss02+ss04+ss06",
                "ss08",
            ]),
            texts(&["ss10"]),
            Vec::new(),
        ];
        for full in &rosters {
            let mut pool = full.clone();
            pool.extend(texts(&["ss09", "ss10+ss05"]));
            for subset in 0..1u32 << pool.len() {
                let unit: Vec<String> = pool
                    .iter()
                    .enumerate()
                    .filter(|(at, _)| subset & (1 << at) != 0)
                    .map(|(_, config)| config.clone())
                    .collect();
                assert_eq!(
                    config_badge(&unit, full),
                    config_badge_by_sets(&unit, full),
                    "{unit:?} in {full:?}"
                );
            }
        }
        let wide: Vec<String> = (0..66).map(|tag| format!("ss{tag:02}")).collect();
        let unit = texts(&["ss03"]);
        assert_eq!(
            config_badge(&unit, &wide),
            config_badge_by_sets(&unit, &wide)
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
