//! The three verdict-export drafts, as `drafts.Drafter` (`rebuild/review/drafts.py`) drafts them: the approve pin (`draft_pin`, checked by its replay, `validate_semantics`), the reject policy record (`draft_policy` with `_policy_position`, `_new_join_side`, `_gained_extension_side`, `_junction_identical`, `_baseline_cell_pin`, `_window_for_side`, `_window_when`, `_baseline_exit` and `_before_junction_at_gap`), and the fine-either-way any-of record (`draft_any_of`), with `stylistic_set_value`, `features_dict` and the three `to_json` methods ([`Drafter`], [`Drafts::to_py`]).
//!
//! A draft that fails its check is a [`DraftError`], where Python raises `DraftError`: a pin that does not parse or that the after font refutes, a policy record the rune schema rejects, and a before-behavior candidate that does not parse. So is every failure Python meets as another exception (a missing letter or boundary, an index past a tuple's end), since a worker that meets either fails the unit.
//!
//! [`stage_draft`] is the `draft` stage of the stage fixtures.

use std::collections::{HashMap, HashSet};
use std::fmt;
use std::io::{BufRead, Write};
use std::path::Path;

use ams_m1_kernel::emit::json_string;
use ams_m1_kernel::index::SpecIndex;
use ams_m1_kernel::model::Sym;
use ams_m1_kernel::types::{CellId, adjustment_text, is_boundary_settled};
use serde_json::Value;

use crate::change_groups::reroutes;
use crate::enrich::{EnrichError, Enriched, Enricher, Flags, init_letters};
use crate::expect::{ExpectError, Replayer, expect_string};
use crate::explain::python_int;
use crate::fontfacts::FontFacts;
use crate::ink::{InkError, InkReader};
use crate::pyfmt::repr::repr_str;
use crate::pyfmt::yaml::flow_dump;
use crate::pyfmt::{PyFmtError, PyValue};
use crate::schema::{Checker, RuneSchema, SchemaError};
use crate::settle::{SettleError, Settlement, Settler, features_for_config};
use crate::shape::{Features, Fonts, ShapeError, Shaper};
use crate::subset_pack::{SubsetPack, SubsetPackError};
use crate::wire::{Alias, CorpusEntry, Init, UnitRecord, WireError};

/// `drafts.SUGGESTED_HOME`: the page a drafted pin suggests it be added to.
pub const SUGGESTED_HOME: &str = "site/the-manual.html";

/// `drafts._BOUNDARY_QS_TEXT`: each boundary code point's word in an any-of record's text.
const BOUNDARY_QS_TEXT: [(u32, &str); 3] = [(0x0020, "space"), (0x200C, "ZWNJ"), (0x00B7, "·")];

/// The junction token `_before_junction_at_gap` compares with, a break.
const BREAK: &str = "break";

/// `_baseline_exit`'s reading of a before junction token as the exit height a prefer record pins.
const BASELINE_EXITS: [(&str, &str); 4] = [
    ("y0", "baseline"),
    ("y5", "x-height"),
    ("y6", "y6"),
    ("y8", "top"),
];

/// A draft that fails its check, or a `draft` stage input the engine cannot read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DraftError(pub String);

impl fmt::Display for DraftError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for DraftError {}

macro_rules! from_error {
    ($($kind:ty),*) => {
        $(impl From<$kind> for DraftError {
            fn from(error: $kind) -> Self {
                Self(error.0)
            }
        })*
    };
}

from_error!(
    EnrichError,
    InkError,
    PyFmtError,
    SchemaError,
    SettleError,
    ShapeError,
    SubsetPackError,
    WireError
);

impl From<ExpectError> for DraftError {
    fn from(error: ExpectError) -> Self {
        Self(error.to_string())
    }
}

type Result<T> = std::result::Result<T, DraftError>;

fn error<T>(message: impl Into<String>) -> Result<T> {
    Err(DraftError(message.into()))
}

/// `drafts.stylistic_set_value`: None when the configurations include the default, else the first configuration's set numbers, space-separated.
pub fn stylistic_set_value(configs: &[String]) -> Option<String> {
    if configs.iter().any(|config| config == "default") {
        return None;
    }
    let first = configs.first().map_or("", String::as_str);
    Some(
        first
            .split('+')
            .map(|tag| tag.strip_prefix("ss").unwrap_or(tag))
            .collect::<Vec<_>>()
            .join(" "),
    )
}

/// `drafts.features_dict`: no features when the configurations include the default, else each tag of the first configuration turned on.
pub fn features_dict(configs: &[String]) -> Features {
    let mut features = Features::new();
    if configs.iter().any(|config| config == "default") {
        return features;
    }
    for tag in configs.first().map_or("", String::as_str).split('+') {
        features.set(tag, true);
    }
    features
}

/// `PinDraft`: the approve pin. Its syntax and its semantics against the after font are always `pass`, since a pin that fails either is a [`DraftError`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PinDraft {
    pub expect: String,
    pub attribute: &'static str,
    pub stylistic_set: Option<String>,
    pub duplicate_of: Option<String>,
}

impl PinDraft {
    /// `PinDraft.to_json`.
    pub fn to_py(&self) -> PyValue {
        entries(vec![
            ("expect", PyValue::str(&self.expect)),
            ("attribute", PyValue::str(self.attribute)),
            (
                "stylistic_set",
                PyValue::optional_str(self.stylistic_set.as_deref()),
            ),
            ("syntax", PyValue::str("pass")),
            ("semantics_after_font", PyValue::str("pass")),
            (
                "duplicate_of",
                PyValue::optional_str(self.duplicate_of.as_deref()),
            ),
            ("suggested_home", PyValue::str(SUGGESTED_HOME)),
        ])
    }
}

/// `PolicyDraft`: the reject policy record. Its schema check always passed, since a record that fails it is a [`DraftError`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PolicyDraft {
    pub file: String,
    pub keypath: &'static str,
    pub suggested_record: String,
    pub names_provenance: Vec<String>,
    pub decided_stage: String,
    pub why_stub: String,
}

impl PolicyDraft {
    /// `PolicyDraft.to_json`.
    pub fn to_py(&self) -> PyValue {
        entries(vec![
            ("file", PyValue::str(&self.file)),
            ("keypath", PyValue::str(self.keypath)),
            ("suggested_record", PyValue::str(&self.suggested_record)),
            (
                "names_provenance",
                PyValue::List(self.names_provenance.iter().map(PyValue::str).collect()),
            ),
            ("decided_stage", PyValue::str(&self.decided_stage)),
            ("schema_valid", PyValue::Bool(true)),
            ("why_stub", PyValue::str(&self.why_stub)),
        ])
    }
}

/// `AnyOfDraft`: the fine-either-way record.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AnyOfDraft {
    pub text: String,
    pub features: Features,
    pub candidates: Vec<String>,
}

impl AnyOfDraft {
    /// `AnyOfDraft.to_json`.
    pub fn to_py(&self) -> PyValue {
        entries(vec![
            ("text", PyValue::str(&self.text)),
            ("features", self.features.to_py()),
            (
                "candidates",
                PyValue::List(self.candidates.iter().map(PyValue::str).collect()),
            ),
        ])
    }
}

/// One unit's three drafts.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Drafts {
    pub pin: PinDraft,
    pub policy: Option<PolicyDraft>,
    pub any_of: AnyOfDraft,
}

impl Drafts {
    /// The `drafts` value `build.unit_to_json` puts on a full fragment.
    pub fn to_py(&self) -> PyValue {
        entries(vec![
            ("pin", self.pin.to_py()),
            (
                "policy",
                self.policy
                    .as_ref()
                    .map_or(PyValue::None, PolicyDraft::to_py),
            ),
            ("any_of", self.any_of.to_py()),
        ])
    }
}

fn entries(items: Vec<(&str, PyValue)>) -> PyValue {
    PyValue::Dict(
        items
            .into_iter()
            .map(|(key, value)| (PyValue::str(key), value))
            .collect(),
    )
}

/// A policy record as `draft_policy` builds it, an insertion-ordered dict.
type Record = Vec<(PyValue, PyValue)>;

fn family_window(direction: &str, rune: &str) -> PyValue {
    entries(vec![(
        direction,
        entries(vec![("family", PyValue::List(vec![PyValue::str(rune)]))]),
    )])
}

/// `Drafter`: the after font's replay, the letters, the alias map with later entries winning, the site corpus index with the first entry for each key winning, and the rune schema's three checkers.
pub struct Drafter<'a> {
    index: &'a SpecIndex,
    letters: HashMap<u32, String>,
    replayer: Replayer<'a>,
    aliases: HashMap<&'a str, &'a Alias>,
    corpus_index: HashMap<(&'a str, &'a str), &'a CorpusEntry>,
    refuse: Checker<'a>,
    prefer: Checker<'a>,
    contract: Checker<'a>,
}

impl<'a> Drafter<'a> {
    /// The drafter over the init, the spec's index, the font facts and the rune schema; a schema without one of the three definitions is refused, as `for_definition` raises.
    pub fn new(
        init: &'a Init,
        index: &'a SpecIndex,
        facts: &'a FontFacts,
        schema: &'a RuneSchema,
    ) -> Result<Self> {
        let mut corpus_index = HashMap::new();
        for entry in &init.corpus_index {
            corpus_index
                .entry((entry.text.as_str(), entry.config.as_str()))
                .or_insert(entry);
        }
        Ok(Self {
            index,
            letters: init_letters(init)?,
            replayer: Replayer::new(init, facts),
            aliases: init
                .aliases
                .iter()
                .map(|(name, alias)| (name.as_str(), alias))
                .collect(),
            corpus_index,
            refuse: schema.for_definition("refuseRecord")?,
            prefer: schema.for_definition("preferRecord")?,
            contract: schema.for_definition("contractRecord")?,
        })
    }

    /// The three drafts `build.unit_to_json` adds to a full fragment, in its order: the pin, the policy record and the any-of record.
    pub fn draft(
        &self,
        shaper: &mut Shaper,
        unit: &UnitRecord,
        enriched: &Enriched,
    ) -> Result<Drafts> {
        Ok(Drafts {
            pin: self.draft_pin(shaper, unit, enriched)?,
            policy: self.draft_policy(unit, enriched)?,
            any_of: self.draft_any_of(unit, enriched)?,
        })
    }

    fn resolve(&self, sym: Sym) -> &str {
        self.index.resolve(sym)
    }

    fn height(&self, height: Option<Sym>) -> Option<&str> {
        height.map(|height| self.resolve(height))
    }

    fn is_boundary(&self, enriched: &Enriched, position: usize) -> bool {
        is_boundary_settled(self.index.vocab(), &enriched.positions[position].0)
    }

    // --- the approve pin -----------------------------------------------------

    /// `Drafter.draft_pin`: the after behavior's whole-word expect string, which must parse and which the after font must bear out, with the attribute and the duplicate the site corpus index gives the text under the unit's first configuration.
    pub fn draft_pin(
        &self,
        shaper: &mut Shaper,
        unit: &UnitRecord,
        enriched: &Enriched,
    ) -> Result<PinDraft> {
        let geometry = &enriched.geometry;
        let expect = expect_string(
            &self.letters,
            &unit.codepoint_values,
            &geometry.after_spans,
            &geometry.after_junctions,
        )?;
        let text = unit_text(unit)?;
        let config_token = first_config(unit)?;
        if let Err(message) = self.replayer.parser().parse(&expect) {
            return error(format!(
                "{} under {config_token}: the drafted pin {} does not parse: {message}",
                unit.codepoints,
                repr_str(&expect)
            ));
        }
        let semantics = self.replayer.validate_semantics(
            shaper,
            &text,
            &expect,
            &features_dict(&unit.configs),
        )?;
        if semantics != "pass" {
            return error(format!(
                "{} under {config_token}: the after font refutes the drafted pin {}: {semantics}",
                unit.codepoints,
                repr_str(&expect)
            ));
        }
        let hit = self
            .corpus_index
            .get(&(text.as_str(), config_token))
            .copied();
        let canonical = hit.is_some_and(|hit| hit.attribute == "data-expect");
        Ok(PinDraft {
            expect,
            attribute: if canonical {
                "data-expect"
            } else {
                "data-expect-noncanonically"
            },
            stylistic_set: stylistic_set_value(&unit.configs),
            duplicate_of: hit.map(|hit| hit.source.clone()),
        })
    }

    // --- the reject policy edit ----------------------------------------------

    /// `Drafter.draft_policy` with no reviewer note: the one-line record that reverses the change at the first divergent position that is not a boundary, checked against its rune-schema definition, or None when no position qualifies or a name-grain divergence has no one-line reversing edit.
    pub fn draft_policy(
        &self,
        unit: &UnitRecord,
        enriched: &Enriched,
    ) -> Result<Option<PolicyDraft>> {
        let Some(position) = self.policy_position(enriched) else {
            return Ok(None);
        };
        let (settled, decided_stage) = &enriched.positions[position];
        let cell = &settled.cell;
        let rune = self.resolve(cell.rune);
        let why = format!(
            "Reviewer rejected the M1 outcome for {} ({})",
            unit.codepoints, enriched.notation
        );
        let provenance = &enriched.provenance;

        let new_join_side = self.new_join_side(enriched, position)?;
        let extension_side = self.gained_extension_side(enriched, position)?;
        let (record, keypath, checker): (Record, &'static str, &Checker) =
            if let (Some(side), false) = (new_join_side, provenance.is_empty()) {
                let anchor = if side == "exit" {
                    cell.exit
                } else {
                    cell.entry
                };
                let record = vec![
                    (
                        PyValue::str(side),
                        PyValue::optional_str(self.height(anchor)),
                    ),
                    (
                        PyValue::str("when"),
                        self.window_for_side(enriched, position, side),
                    ),
                    (PyValue::str("why"), PyValue::str(&why)),
                ];
                (record, "policy.refuse[+]", &self.refuse)
            } else if let (Some((side, height, amount)), true) = (
                extension_side,
                provenance
                    .iter()
                    .any(|pointer| pointer.contains("policy.extend")),
            ) {
                let when = self.window_for_side(enriched, position, side);
                let record = vec![
                    (PyValue::str(side), PyValue::str(height)),
                    (PyValue::str("by"), PyValue::Int(amount)),
                    (PyValue::str("when"), when),
                    (PyValue::str("why"), PyValue::str(&why)),
                ];
                (record, "policy.contract[+]", &self.contract)
            } else if !provenance.is_empty() && junction_identical(enriched) {
                let Some(record) = self.baseline_cell_pin(enriched, position, cell, &why)? else {
                    return Ok(None);
                };
                (record, "policy.prefer[+]", &self.prefer)
            } else if !provenance.is_empty() {
                let mut record = match self.height(cell.exit) {
                    Some(exit) => vec![(PyValue::str("exit"), PyValue::str(exit))],
                    None => vec![(
                        PyValue::str("stance"),
                        PyValue::str(self.resolve(cell.stance)),
                    )],
                };
                record.push((PyValue::str("when"), self.window_when(enriched, position)));
                record.push((PyValue::str("why"), PyValue::str(&why)));
                (record, "policy.refuse[+]", &self.refuse)
            } else {
                let baseline_exit = self.baseline_exit(unit, enriched, position)?;
                let when = self.window_when(enriched, position);
                let record = vec![
                    (
                        PyValue::str("cell"),
                        entries(vec![("exit", PyValue::str(baseline_exit))]),
                    ),
                    (PyValue::str("when"), when),
                    (PyValue::str("why"), PyValue::str(&why)),
                ];
                (record, "policy.prefer[+]", &self.prefer)
            };
        let record = PyValue::Dict(record);
        if !checker.check(&record) {
            return error(format!(
                "{} under {}: the drafted {keypath} record for {rune} does not validate against the rune schema",
                unit.codepoints,
                first_config(unit)?
            ));
        }
        Ok(Some(PolicyDraft {
            file: format!("glyph_data/runes/{rune}.yaml"),
            keypath,
            suggested_record: flow_dump(&record)?,
            names_provenance: provenance.clone(),
            decided_stage: (*decided_stage).to_owned(),
            why_stub: why,
        }))
    }

    /// `_policy_position`: the first divergent position within the trace that is not a boundary, else the first position that is not one.
    fn policy_position(&self, enriched: &Enriched) -> Option<usize> {
        let count = enriched.positions.len();
        enriched
            .geometry
            .diff_positions
            .iter()
            .copied()
            .find(|&position| position < count && !self.is_boundary(enriched, position))
            .or_else(|| (0..count).find(|&position| !self.is_boundary(enriched, position)))
    }

    /// `_new_join_side`: `exit` when the divergent cell joins across its exit gap where the baseline broke, else `entry` when it does so across its entry gap, else None.
    fn new_join_side(&self, enriched: &Enriched, position: usize) -> Result<Option<&'static str>> {
        let geometry = &enriched.geometry;
        let cell = &enriched.positions[position].0.cell;
        if position < geometry.after_junctions.len()
            && geometry.after_junctions[position] != BREAK
            && cell.exit.is_some()
            && before_junction_at_gap(enriched, signed(span_at(enriched, position)?.1) - 1)?
                == Some(BREAK)
        {
            return Ok(Some("exit"));
        }
        if position > 0
            && junction_at(enriched, position - 1)? != BREAK
            && cell.entry.is_some()
            && before_junction_at_gap(enriched, signed(span_at(enriched, position)?.0) - 1)?
                == Some(BREAK)
        {
            return Ok(Some("entry"));
        }
        Ok(None)
    }

    /// `_baseline_cell_pin`: the prefer record that pins the baseline glyph's alias cell where it differs from the new cell in an anchor (`cell` and `over`, scoped across the first differing side, exit first) or else in its stance, or None when the alias is no cell or differs in neither.
    fn baseline_cell_pin(
        &self,
        enriched: &Enriched,
        position: usize,
        cell: &CellId,
        why: &str,
    ) -> Result<Option<Record>> {
        let before_glyph = before_glyph_at(enriched, position)?;
        let Some(Alias::Cell {
            stance,
            entry,
            exit,
            ..
        }) = self.aliases.get(before_glyph)
        else {
            return Ok(None);
        };
        let mut pin = Vec::new();
        let mut over = Vec::new();
        for (anchor, old_value, new_value) in [
            ("entry", entry.as_deref(), self.height(cell.entry)),
            ("exit", exit.as_deref(), self.height(cell.exit)),
        ] {
            if old_value != new_value {
                pin.push((anchor, PyValue::str(or_none(old_value))));
                over.push((anchor, PyValue::str(or_none(new_value))));
            }
        }
        if !pin.is_empty() {
            let side = if pin.iter().any(|(anchor, _)| *anchor == "exit") {
                "exit"
            } else {
                "entry"
            };
            let when = self.window_for_side(enriched, position, side);
            return Ok(Some(vec![
                (PyValue::str("cell"), entries(pin)),
                (PyValue::str("over"), entries(over)),
                (PyValue::str("mode"), PyValue::str("absolute")),
                (PyValue::str("when"), when),
                (PyValue::str("why"), PyValue::str(why)),
            ]));
        }
        if stance != self.resolve(cell.stance) {
            let when = self.window_when(enriched, position);
            return Ok(Some(vec![
                (PyValue::str("stance"), PyValue::str(stance)),
                (PyValue::str("mode"), PyValue::str("absolute")),
                (PyValue::str("when"), when),
                (PyValue::str("why"), PyValue::str(why)),
            ]));
        }
        Ok(None)
    }

    /// `_gained_extension_side`: the side keyword, height and pixels of the first `en-ext-` or `ex-ext-` adjustment of the divergent cell that the baseline glyph's name does not carry, on a side the cell joins, or None.
    fn gained_extension_side(
        &self,
        enriched: &Enriched,
        position: usize,
    ) -> Result<Option<(&'static str, &str, i64)>> {
        let cell = &enriched.positions[position].0.cell;
        let before_parts: HashSet<&str> = before_glyph_at(enriched, position)?
            .split('.')
            .skip(1)
            .collect();
        for token in &cell.adjustments {
            let token = adjustment_text(self.index, *token);
            if before_parts.contains(token.as_str()) {
                continue;
            }
            let side = if token.starts_with("en-ext-") {
                self.height(cell.entry).map(|height| ("entry", height))
            } else if token.starts_with("ex-ext-") {
                self.height(cell.exit).map(|height| ("exit", height))
            } else {
                None
            };
            if let Some((side, height)) = side {
                let amount = token
                    .rsplit_once('-')
                    .map_or(token.as_str(), |(_, tail)| tail);
                let Some(amount) = python_int(amount) else {
                    return error(format!(
                        "the adjustment {} ends in no int Python reads",
                        repr_str(&token)
                    ));
                };
                return Ok(Some((side, height, amount)));
            }
        }
        Ok(None)
    }

    /// `_window_for_side`: an exit-side record scoped to its right neighbor's family and an entry-side record to its left neighbor's, falling back to `_window_when` when that neighbor is a boundary or absent.
    fn window_for_side(&self, enriched: &Enriched, position: usize, side: &str) -> PyValue {
        let positions = &enriched.positions;
        if side == "exit"
            && position + 1 < positions.len()
            && !self.is_boundary(enriched, position + 1)
        {
            return family_window("right", self.resolve(positions[position + 1].0.cell.rune));
        }
        if side == "entry" && position > 0 && !self.is_boundary(enriched, position - 1) {
            return family_window("left", self.resolve(positions[position - 1].0.cell.rune));
        }
        self.window_when(enriched, position)
    }

    /// `_window_when`: the right neighbor's family, else the left neighbor's, else the isolated word.
    fn window_when(&self, enriched: &Enriched, position: usize) -> PyValue {
        let positions = &enriched.positions;
        if position + 1 < positions.len() && !self.is_boundary(enriched, position + 1) {
            return family_window("right", self.resolve(positions[position + 1].0.cell.rune));
        }
        if position > 0 && !self.is_boundary(enriched, position - 1) {
            return family_window("left", self.resolve(positions[position - 1].0.cell.rune));
        }
        entries(vec![("word", PyValue::str("isolated"))])
    }

    /// `_baseline_exit`: the exit height the baseline's junction at the code point gap after the divergent cell names, or `none` when the gap ends the text or the baseline broke there.
    fn baseline_exit(
        &self,
        unit: &UnitRecord,
        enriched: &Enriched,
        position: usize,
    ) -> Result<&'static str> {
        let gap = signed(span_at(enriched, position)?.1) - 1;
        if gap < signed(unit.codepoint_values.len()) - 1
            && let Some(junction) = before_junction_at_gap(enriched, gap)?
            && junction.starts_with('y')
        {
            return Ok(BASELINE_EXITS
                .iter()
                .find(|(token, _)| *token == junction)
                .map_or("none", |(_, exit)| exit));
        }
        Ok("none")
    }

    // --- the fine-either-way any-of record -------------------------------------

    /// `Drafter.draft_any_of`: the after behavior's expect string and, when it differs, the before behavior's, which must parse; the text as letter families and boundary words; and the unit's features.
    pub fn draft_any_of(&self, unit: &UnitRecord, enriched: &Enriched) -> Result<AnyOfDraft> {
        let geometry = &enriched.geometry;
        let values = &unit.codepoint_values;
        let after = expect_string(
            &self.letters,
            values,
            &geometry.after_spans,
            &geometry.after_junctions,
        )?;
        let before = expect_string(
            &self.letters,
            values,
            &geometry.before_spans,
            &geometry.before_junctions,
        )?;
        let mut candidates = vec![after.clone()];
        if before != after {
            if let Err(message) = self.replayer.parser().parse(&before) {
                return error(format!(
                    "{} under {}: the drafted before-behavior candidate {} does not parse: {message}",
                    unit.codepoints,
                    first_config(unit)?,
                    repr_str(&before)
                ));
            }
            candidates.push(before);
        }
        let tokens = values
            .iter()
            .map(|value| {
                self.letters
                    .get(value)
                    .map(String::as_str)
                    .or_else(|| {
                        BOUNDARY_QS_TEXT
                            .iter()
                            .find(|(boundary, _)| boundary == value)
                            .map(|(_, word)| *word)
                    })
                    .ok_or_else(|| {
                        DraftError(format!("{value:04X} is neither a letter nor a boundary"))
                    })
            })
            .collect::<Result<Vec<_>>>()?;
        Ok(AnyOfDraft {
            text: tokens.join(" "),
            features: features_dict(&unit.configs),
            candidates,
        })
    }
}

fn or_none(height: Option<&str>) -> &str {
    height.filter(|height| !height.is_empty()).unwrap_or("none")
}

fn signed(value: usize) -> i64 {
    i64::try_from(value).expect("an index fits an i64")
}

fn first_config(unit: &UnitRecord) -> Result<&str> {
    unit.configs.first().map(String::as_str).ok_or_else(|| {
        DraftError(format!(
            "{}: the unit has no configuration",
            unit.codepoints
        ))
    })
}

fn unit_text(unit: &UnitRecord) -> Result<String> {
    unit.codepoint_values
        .iter()
        .map(|&value| {
            char::from_u32(value).ok_or_else(|| DraftError(format!("{value:04X} is no character")))
        })
        .collect()
}

fn span_at(enriched: &Enriched, position: usize) -> Result<(usize, usize)> {
    enriched
        .geometry
        .after_spans
        .get(position)
        .copied()
        .ok_or_else(|| DraftError(format!("the after spans hold no position {position}")))
}

fn junction_at(enriched: &Enriched, position: usize) -> Result<&str> {
    enriched
        .geometry
        .after_junctions
        .get(position)
        .map(String::as_str)
        .ok_or_else(|| DraftError(format!("the after junctions hold no position {position}")))
}

/// `_junction_identical`: as many before glyphs as after cells, and the same junctions.
fn junction_identical(enriched: &Enriched) -> bool {
    let geometry = &enriched.geometry;
    geometry.before_glyphs.len() == geometry.after_cells.len()
        && geometry.before_junctions == geometry.after_junctions
}

/// `_before_junction_at_gap`: the before junction after the glyph whose successor starts just after the code point gap, or None.
fn before_junction_at_gap(enriched: &Enriched, gap: i64) -> Result<Option<&str>> {
    let geometry = &enriched.geometry;
    for index in 0..geometry.before_spans.len().saturating_sub(1) {
        if signed(geometry.before_spans[index + 1].0) - 1 == gap {
            return geometry
                .before_junctions
                .get(index)
                .map(|junction| Some(junction.as_str()))
                .ok_or_else(|| DraftError(format!("the before junctions hold no gap {index}")));
        }
    }
    Ok(None)
}

/// The baseline glyph at the divergent position: the last before glyph whose span holds the after span's start, else the first.
fn before_glyph_at(enriched: &Enriched, position: usize) -> Result<&str> {
    let geometry = &enriched.geometry;
    let span_start = span_at(enriched, position)?.0;
    let mut before_index = 0;
    for (index, &(start, end)) in geometry.before_spans.iter().enumerate() {
        if start <= span_start && span_start < end {
            before_index = index;
        }
    }
    geometry
        .before_glyphs
        .get(before_index)
        .map(String::as_str)
        .ok_or_else(|| DraftError(format!("the before glyphs hold no glyph {before_index}")))
}

fn line_flags(record: &Value, key: &str) -> Result<Flags> {
    let PyValue::Dict(entries) = PyValue::from_typed(&record["flags"])
        .map_err(|failure| DraftError(format!("{key}: the flags: {failure}")))?
    else {
        return error(format!("{key}: the flags are not a dict"));
    };
    let flag = |name: &str| match entries.iter().find(|(held, _)| *held == PyValue::str(name)) {
        Some((_, PyValue::Bool(value))) => Ok(*value),
        _ => error(format!("{key}: the flags hold no bool {name}")),
    };
    Ok(Flags {
        ink_identical: flag("ink_identical")?,
        picture_identical: flag("picture_identical")?,
        junior_equivalent: flag("junior_equivalent")?,
    })
}

/// The `draft` stage: for each `draft:` line of `stage_file`, the unit its `record` carries as phase 1 received it goes through phase 1 up to its drafts: its ink is read and its flags checked first against the line's `flags`, which the worker's phase 1 set, so a failure there names the ink stage; the unit is re-routed (`reroutes`), which sets its ledger class and clears its exemplar flag, enriched under its first configuration, and drafted, and the line's answer is the `drafts` value [`Drafts::to_py`] gives, written as one `{"key", "value"}` line in the typed encoding. The shaper's and the settler's memos carry from line to line as a batch's do.
pub fn stage_draft(init_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let init = Init::read(init_path)?;
    let facts = FontFacts::read(&init.font_facts)?;
    let fonts = Fonts::open(&init, &facts)?;
    let reader = InkReader::new(&init, &facts)?;
    let settlement = Settlement::new(SpecIndex::new(init.read_spec()?), init.settlement.clone())?;
    let mut settler = Settler::new(&settlement);
    let pack = SubsetPack::open(&init.subset_pack)?;
    let enricher = Enricher::new(&init, &settlement, &pack, &facts)?;
    let schema = RuneSchema::read(Path::new(&init.rune_schema))?;
    let drafter = Drafter::new(&init, settlement.index(), &facts, &schema)?;
    let mut shaper = Shaper::new(&fonts, &facts);
    let file = std::fs::File::open(stage_file).map_err(|failure| {
        DraftError(format!("cannot read {}: {failure}", stage_file.display()))
    })?;
    for line in std::io::BufReader::new(file).lines() {
        let line = line.map_err(|failure| {
            DraftError(format!("cannot read {}: {failure}", stage_file.display()))
        })?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| DraftError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return error(format!("a stage line has no key: {line:.200}"));
        };
        if !key.starts_with("draft:") {
            return error(format!("{key}: the draft stage answers draft: lines"));
        }
        let Some(text) = record["record"].as_str() else {
            return error(format!("{key}: the line has no unit record"));
        };
        let unit: Value = serde_json::from_str(text)
            .map_err(|failure| DraftError(format!("{key}: the unit record: {failure}")))?;
        let mut unit = UnitRecord::from_json(&unit)?;
        let flags = line_flags(&record, key)?;
        let ink = reader.read_unit(&mut shaper, &unit)?;
        let read = Flags {
            ink_identical: ink.ink_identical,
            picture_identical: ink.picture_identical,
            junior_equivalent: ink.junior_equivalent,
        };
        if read != flags {
            return error(format!(
                "{key}: the ink stage reads {read:?}, and the worker's phase 1 set {flags:?}"
            ));
        }
        let machine_approved =
            flags.ink_identical || flags.picture_identical || flags.junior_equivalent;
        if reroutes(
            &unit.class,
            unit.no_verdict,
            machine_approved,
            &init.identical_classes,
        ) {
            unit.ledger_class = unit.class.clone();
            unit.exemplar = false;
        } else {
            unit.ledger_class = String::new();
        }
        let config = unit.configs.first().map_or("", String::as_str);
        let report = settler
            .explain(&unit.codepoint_values, &features_for_config(config))
            .map_err(|failure| DraftError(format!("{key}: the settle stage: {failure}")))?;
        let enriched = enricher
            .enrich(&mut shaper, &unit, &flags, &report)
            .map_err(|failure| DraftError(format!("{key}: the enrich stage: {failure}")))?;
        let drafts = drafter
            .draft(&mut shaper, &unit, &enriched)
            .map_err(|failure| DraftError(format!("{key}: {failure}")))?;
        writeln!(
            out,
            "{{\"key\":{},\"value\":{}}}",
            json_string(key),
            drafts.to_py().to_typed()
        )
        .map_err(|failure| DraftError(format!("cannot write a stage line: {failure}")))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn configs(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| (*item).to_owned()).collect()
    }

    #[test]
    fn the_stylistic_set_is_the_first_configs_set_numbers_unless_the_default_is_held() {
        assert_eq!(stylistic_set_value(&configs(&["ss03", "default"])), None);
        assert_eq!(
            stylistic_set_value(&configs(&["ss03+ss05", "ss10"])),
            Some("03 05".to_owned())
        );
        assert_eq!(
            stylistic_set_value(&configs(&["ss10"])),
            Some("10".to_owned())
        );
    }

    #[test]
    fn the_features_turn_on_each_tag_of_the_first_config_unless_the_default_is_held() {
        assert_eq!(
            features_dict(&configs(&["default", "ss03"])),
            Features::new()
        );
        let mut expected = Features::new();
        expected.set("ss03", true);
        expected.set("ss05", true);
        assert_eq!(features_dict(&configs(&["ss03+ss05+ss03"])), expected);
    }

    #[test]
    fn an_absent_or_empty_height_reads_as_none() {
        assert_eq!(or_none(None), "none");
        assert_eq!(or_none(Some("")), "none");
        assert_eq!(or_none(Some("baseline")), "baseline");
    }

    #[test]
    fn the_drafts_keep_the_to_json_key_order() {
        let drafts = Drafts {
            pin: PinDraft {
                expect: "·Pea ~b~ ·Tea".to_owned(),
                attribute: "data-expect-noncanonically",
                stylistic_set: None,
                duplicate_of: Some("site/x.html:3".to_owned()),
            },
            policy: None,
            any_of: AnyOfDraft {
                text: "qsPea qsTea".to_owned(),
                features: Features::new(),
                candidates: vec!["·Pea ~b~ ·Tea".to_owned()],
            },
        };
        let PyValue::Dict(items) = drafts.to_py() else {
            panic!("the drafts are a dict");
        };
        let keys: Vec<_> = items.iter().map(|(key, _)| key.clone()).collect();
        assert_eq!(
            keys,
            [
                PyValue::str("pin"),
                PyValue::str("policy"),
                PyValue::str("any_of")
            ]
        );
        let PyValue::Dict(pin) = &items[0].1 else {
            panic!("the pin is a dict");
        };
        let pin_keys: Vec<_> = pin.iter().map(|(key, _)| key.clone()).collect();
        assert_eq!(
            pin_keys,
            [
                "expect",
                "attribute",
                "stylistic_set",
                "syntax",
                "semantics_after_font",
                "duplicate_of",
                "suggested_home"
            ]
            .map(PyValue::str)
        );
        assert_eq!(items[1].1, PyValue::None);
    }
}
