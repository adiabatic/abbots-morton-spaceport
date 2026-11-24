//! Each unit as the write receives it: a recomputed fragment read back from the units phase's arena and patched with this build's scaffold, ink deltas, ledger class and primary units under the scaffold check; a cached fragment whose patched fields all hold, taken as its stored bytes with the identity that stands in for it; or any other cached fragment read from the previous corpus and patched ([`Emitter::emission`]). A fragment that does not read back as the unit and stamp at its address stops the build.

use std::path::Path;

use super::drive::{Arena, unit_record};
use super::input::BuildInput;
use super::prior::{PriorError, PriorFragmentReader, read_arena};
use super::records::truthy;
use super::store::{PriorFragment, UnitStore};
use super::table::UnitTable;
use super::{BuildError, failed};
use crate::enrich::{Flags, Highlight, SecondaryJunction};
use crate::fragment::{FragmentBuilder, lookup};
use crate::pyfmt::PyValue;
use crate::scaffold::{Badge, Roster, config_badge};
use crate::wire::Init;

/// What the write takes from one unit: a fragment to serialize, or a cached fragment's bytes as they are stored, deflated when its address records an inflated length, with that address and the identity the cross-unit checks and the sidecars read in place of the fragment.
#[derive(Clone, Debug, PartialEq)]
pub enum Emitted {
    Fragment(PyValue),
    Bytes {
        body: Vec<u8>,
        source: PriorFragment,
        identity: PyValue,
    },
}

/// One unit as the write receives it, with the content key and policy file the fragment or the store record gives.
#[derive(Clone, Debug, PartialEq)]
pub struct Emission {
    pub unit_id: String,
    pub content_key: String,
    pub policy_file: Option<String>,
    pub emitted: Emitted,
}

/// What every emission reads besides the unit: the patcher over the units init's acceptance configurations, those configurations with the window-scoped ones among them, which the config badge reads, and the preset dictionary the arena's fragments inflate under.
pub struct Emitter {
    builder: FragmentBuilder,
    roster: Roster,
    dictionary: Vec<u8>,
}

/// The rune file a fragment's policy draft names, or None.
pub fn policy_file(fragment: &PyValue) -> std::result::Result<Option<String>, BuildError> {
    let empty = PyValue::Dict(Vec::new());
    let or_empty = |value: Option<&PyValue>, what: &str| match value {
        Some(value) if truthy(value) => match value {
            PyValue::Dict(_) => Ok(value.clone()),
            other => Err(failed(format!(
                "AttributeError: '{}' object has no attribute 'get', reading a fragment's {what}",
                other.kind()
            ))),
        },
        _ => Ok(empty.clone()),
    };
    let drafts = or_empty(lookup(fragment, "drafts"), "drafts")?;
    let policy = or_empty(lookup(&drafts, "policy"), "policy draft")?;
    Ok(match lookup(&policy, "file") {
        Some(PyValue::Str(file)) => Some(file.clone()),
        _ => None,
    })
}

/// The store's secondary-junction rects for the unit as the patch reads them.
fn junctions(
    store: &UnitStore,
    ordinal: usize,
) -> std::result::Result<Vec<SecondaryJunction>, BuildError> {
    let rect = |value: &PyValue| -> std::result::Result<Highlight, BuildError> {
        let edge = |name: &str| match lookup(value, name) {
            Some(PyValue::Int(edge)) => Ok(*edge),
            _ => Err(failed(format!(
                "a junction rect of ordinal {ordinal} has no int {name}"
            ))),
        };
        Ok(Highlight {
            x_min: edge("x_min")?,
            x_max: edge("x_max")?,
            advance_total: edge("advance_total")?,
        })
    };
    let PyValue::List(rects) = store.junction_rects(ordinal) else {
        return Err(failed("the store's junction rects are not a list"));
    };
    rects
        .iter()
        .map(|junction| {
            let cell = |index: usize| match lookup(junction, "pair") {
                Some(PyValue::List(pair)) => match pair.get(index) {
                    Some(PyValue::Int(cell)) => usize::try_from(*cell).map_err(|_| {
                        failed(format!(
                            "a junction pair of ordinal {ordinal} names cell {cell}, which a fragment's junction does not hold"
                        ))
                    }),
                    _ => Err(failed(format!("a junction pair of ordinal {ordinal} is not two ints"))),
                },
                _ => Err(failed(format!("a junction of ordinal {ordinal} has no pair"))),
            };
            let side = |name: &str| {
                lookup(junction, name)
                    .ok_or_else(|| failed(format!("a junction of ordinal {ordinal} has no {name}")))
                    .and_then(rect)
            };
            Ok(SecondaryJunction {
                pair: (cell(0)?, cell(1)?),
                highlight_before: side("before")?,
                highlight_after: side("after")?,
            })
        })
        .collect()
}

impl Emitter {
    /// The emitter over the units init the build input names.
    pub fn open(input: &BuildInput) -> std::result::Result<Self, BuildError> {
        let init = Init::read(Path::new(&input.units_init))?;
        let builder = FragmentBuilder::new(&init).map_err(failed)?;
        let roster = Roster::read(&init).map_err(failed)?;
        let dictionary = std::fs::read(&init.dictionary).map_err(|failure| {
            failed(format!(
                "cannot read the preset dictionary {}: {failure}",
                init.dictionary
            ))
        })?;
        Ok(Self {
            builder,
            roster,
            dictionary,
        })
    }

    /// The config badge ([`crate::scaffold::config_badge`]) over `configs` and the configurations a unit of the window `codepoints` can hold, as the write resolves a byte-copied fragment's badge.
    fn window_badge(&self, configs: &[String], codepoints: &str) -> Badge {
        config_badge(configs, &self.roster.window(codepoints))
    }

    /// The config note over the table's configurations of the unit at `ordinal` and the configurations its window can hold.
    pub fn config_note(&self, table: &UnitTable, ordinal: usize) -> PyValue {
        self.window_badge(table.configs(ordinal), &table.codepoints_text(ordinal))
            .note_py()
    }

    /// The dict that stands in for a byte-copied fragment when the cross-unit check and the locator row read it, from the table's and the store's columns.
    fn cached_identity(
        &self,
        table: &UnitTable,
        store: &UnitStore,
        ordinal: usize,
        unit_id: &str,
        junction_assign: &[(Option<String>, bool)],
    ) -> PyValue {
        let (ink_identical, picture_identical, junior_equivalent) = store.machine_flags(ordinal);
        let policy_file = store.policy_file(ordinal).filter(|file| !file.is_empty());
        let configs = table.configs(ordinal);
        let codepoints = table.codepoints_text(ordinal);
        let primary_units: Vec<PyValue> = junction_assign
            .iter()
            .filter(|(_, suppressed)| !suppressed)
            .map(|(primary_unit, _)| {
                PyValue::Dict(vec![(
                    PyValue::str("primary_unit"),
                    PyValue::optional_str(primary_unit.as_deref()),
                )])
            })
            .collect();
        let entries = vec![
            ("id", PyValue::str(unit_id)),
            ("ink_identical", PyValue::Bool(ink_identical)),
            ("picture_identical", PyValue::Bool(picture_identical)),
            ("junior_equivalent", PyValue::Bool(junior_equivalent)),
            ("no_verdict", PyValue::Bool(table.no_verdict(ordinal))),
            (
                "duplicate_group",
                PyValue::optional_str(table.duplicate_group(ordinal)),
            ),
            ("cluster", PyValue::optional_str(table.cluster(ordinal))),
            ("class", PyValue::str(table.class_id(ordinal))),
            ("group", PyValue::str(table.group(ordinal))),
            ("codepoints", PyValue::str(&codepoints)),
            (
                "configs",
                PyValue::List(configs.iter().map(PyValue::str).collect()),
            ),
            (
                "config_gate",
                self.window_badge(configs, &codepoints).gate_py(),
            ),
            (
                "pair",
                match store.cell_pair(ordinal) {
                    Some((left, right)) => PyValue::Dict(vec![
                        (PyValue::str("left"), PyValue::Int(left)),
                        (PyValue::str("right"), PyValue::Int(right)),
                    ]),
                    None => PyValue::None,
                },
            ),
            (
                "secondary_junctions",
                if primary_units.is_empty() {
                    PyValue::None
                } else {
                    PyValue::List(primary_units)
                },
            ),
            (
                "drafts",
                match policy_file {
                    Some(file) => PyValue::Dict(vec![(
                        PyValue::str("policy"),
                        PyValue::Dict(vec![(PyValue::str("file"), PyValue::str(file))]),
                    )]),
                    None => PyValue::None,
                },
            ),
        ];
        PyValue::Dict(
            entries
                .into_iter()
                .map(|(key, value)| (PyValue::str(key), value))
                .collect(),
        )
    }

    /// The unit at `ordinal` read by the address the store holds for it (`source`). A recomputed fragment is read from `arena`, patched and checked by the scaffold check; a cached one whose patched fields all hold is taken as its stored bytes from `prior`, with its identity from the columns; any other cached one is read from `prior` and patched. A fragment that does not read back as the unit and stamp its address carries stops the build with Python's `SystemExit` text, as does a scaffold check that fails.
    pub fn emission(
        &self,
        ordinal: usize,
        source: &PriorFragment,
        table: &UnitTable,
        store: &UnitStore,
        arena: &Arena,
        prior: &mut PriorFragmentReader,
    ) -> std::result::Result<Emission, BuildError> {
        let refused = |failure: PriorError| match failure {
            PriorError::Os(failure) => failed(format!("OSError: {failure}")),
            failure => BuildError::Exit(format!(
                "the fragment for {} cannot be read back: {failure}",
                source.unit_id
            )),
        };
        let recomputed = !store.flags(ordinal).cached;
        let junction_assign = store.primary_units(ordinal);
        let mut fragment = if recomputed {
            read_arena(arena, source, &self.dictionary).map_err(refused)?
        } else if store.cached_as_is(
            ordinal,
            table.class_id(ordinal),
            table.duplicate_group(ordinal),
            table.exemplar(ordinal),
            table.no_verdict(ordinal),
        ) {
            let body = prior.read_bytes(source).map_err(refused)?;
            let identity =
                self.cached_identity(table, store, ordinal, &source.unit_id, &junction_assign);
            return Ok(Emission {
                unit_id: source.unit_id.clone(),
                content_key: store.content_key_hex(ordinal),
                policy_file: store.policy_file(ordinal).map(str::to_owned),
                emitted: Emitted::Bytes {
                    body,
                    source: source.clone(),
                    identity,
                },
            });
        } else {
            prior.read(source).map_err(refused)?
        };
        let mut unit = table.unit(ordinal, Some(store));
        unit.ledger_class = store.ledger_class(ordinal).to_owned();
        let flags = Flags {
            ink_identical: unit.ink_identical,
            picture_identical: unit.picture_identical,
            junior_equivalent: unit.junior_equivalent,
        };
        let ink_deltas: Vec<(String, String)> = store
            .ink_deltas(ordinal)
            .into_iter()
            .map(|(config, delta)| (config.to_owned(), delta.to_owned()))
            .collect();
        self.builder
            .patch_fragment(
                &mut fragment,
                &unit_record(&unit, table),
                &flags,
                &ink_deltas,
                &junctions(store, ordinal)?,
                &junction_assign,
                recomputed,
            )
            .map_err(|failure| BuildError::Exit(failure.to_string()))?;
        let content_key = match lookup(&fragment, "content_key") {
            Some(PyValue::Str(content_key)) => content_key.clone(),
            _ => {
                return Err(failed(format!(
                    "unit {}: the fragment's content key is not a str",
                    unit.unit_id
                )));
            }
        };
        Ok(Emission {
            unit_id: unit.unit_id,
            content_key,
            policy_file: policy_file(&fragment)?,
            emitted: Emitted::Fragment(fragment),
        })
    }

    /// The config note the write keeps for the unit: a fragment's own, or [`Emitter::config_note`] for stored bytes.
    pub fn emitted_note(
        &self,
        emission: &Emission,
        table: &UnitTable,
        ordinal: usize,
    ) -> std::result::Result<PyValue, BuildError> {
        match &emission.emitted {
            Emitted::Fragment(fragment) => {
                lookup(fragment, "config_note").cloned().ok_or_else(|| {
                    failed(format!(
                        "KeyError: 'config_note' in unit {}",
                        emission.unit_id
                    ))
                })
            }
            Emitted::Bytes { .. } => Ok(self.config_note(table, ordinal)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dict(entries: Vec<(&str, PyValue)>) -> PyValue {
        PyValue::Dict(
            entries
                .into_iter()
                .map(|(key, value)| (PyValue::str(key), value))
                .collect(),
        )
    }

    #[test]
    fn the_policy_file_is_a_str_under_the_drafts_policy_and_none_otherwise() {
        let with = |drafts: PyValue| policy_file(&dict(vec![("drafts", drafts)]));
        let policy = |file: PyValue| dict(vec![("policy", dict(vec![("file", file)]))]);
        assert_eq!(
            with(policy(PyValue::str("runes/qsPea.yaml"))).expect("read"),
            Some("runes/qsPea.yaml".to_owned())
        );
        assert_eq!(with(policy(PyValue::Int(3))).expect("read"), None);
        assert_eq!(with(policy(PyValue::None)).expect("read"), None);
        assert_eq!(
            with(dict(vec![("policy", PyValue::None)])).expect("read"),
            None
        );
        assert_eq!(with(PyValue::None).expect("read"), None);
        assert_eq!(with(PyValue::List(Vec::new())).expect("read"), None);
        assert_eq!(policy_file(&dict(Vec::new())).expect("read"), None);
        assert!(with(PyValue::List(vec![PyValue::Int(1)])).is_err());
        assert!(with(dict(vec![("policy", PyValue::str("x"))])).is_err());
    }
}
