//! Each unit as the write receives it, as `build._emission` gives it: a recomputed fragment read back from the units phase's arena and patched with this build's scaffold, ink deltas, ledger class and primary units under the scaffold check; a cached fragment whose patched fields all hold, taken as its stored bytes with the identity that stands in for it (`_cached_identity`); or any other cached fragment read from the previous corpus and patched ([`Emitter::emission`]). A fragment that no longer reads back as the unit and stamp at its address stops the build.
//!
//! [`stage_emit`] is the `emit` stage of the stage fixtures.

use std::io::Write;
use std::path::Path;

use super::audit::stage_line;
use super::drive::{Arena, through_units, unit_record};
use super::input::BuildInput;
use super::passes::{Passes, Triage, passes, triage};
use super::prior::{PriorError, PriorFragmentReader, read_arena};
use super::records::truthy;
use super::route::{RouteNames, route_classes, write_fit};
use super::scan::stage_lines;
use super::store::{PriorFragment, UnitStore};
use super::table::{UNIT_PREFIX, UnitTable};
use super::{BuildError, failed};
use crate::enrich::{Flags, Highlight, SecondaryJunction};
use crate::fragment::{FragmentBuilder, fragment_bytes, lookup, sha256_hex};
use crate::pyfmt::PyValue;
use crate::scaffold::{acceptance_configs, config_badge};
use crate::wire::{Init, Result, WireError};

/// What the write takes from one unit (`build._Emission`): a fragment to serialize, or a cached fragment's bytes as they are stored, deflated when its address records an inflated length, with that address and the identity the cross-unit checks and the sidecars read in place of the fragment.
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

/// What every emission reads besides the unit: the patcher over the units init's acceptance configurations, those configurations, which the config badge reads, and the preset dictionary the arena's fragments inflate under.
pub struct Emitter {
    builder: FragmentBuilder,
    acceptance: Vec<String>,
    dictionary: Vec<u8>,
}

/// `build._policy_file`: the rune file a fragment's policy draft names, or None.
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

/// The store's secondary-junction rects for the unit (`UnitStore.junction_rects`) as the patch reads them.
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
        let acceptance = acceptance_configs(&init).map_err(failed)?;
        let dictionary = std::fs::read(&init.dictionary).map_err(|failure| {
            failed(format!(
                "cannot read the preset dictionary {}: {failure}",
                init.dictionary
            ))
        })?;
        Ok(Self {
            builder,
            acceptance,
            dictionary,
        })
    }

    /// `build.config_note` over `configs` and the acceptance configurations.
    pub fn config_note(&self, configs: &[String]) -> PyValue {
        config_badge(configs, &self.acceptance).note_py()
    }

    /// `build._cached_identity`: the dict that stands in for a byte-copied fragment when the cross-unit check and the locator row read it, from the table's and the store's columns.
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
            ("codepoints", PyValue::str(table.codepoints_text(ordinal))),
            (
                "configs",
                PyValue::List(configs.iter().map(PyValue::str).collect()),
            ),
            (
                "config_gate",
                config_badge(configs, &self.acceptance).gate_py(),
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

    /// `build._emission`: the unit at `ordinal` read by the address the store holds for it (`source`). A recomputed fragment is read from `arena`, patched and checked by the scaffold check; a cached one whose patched fields all hold (`UnitStore.cached_as_is`) is taken as its stored bytes from `prior`, with its identity from the columns; any other cached one is read from `prior` and patched. A fragment that does not read back as the unit and stamp its address carries stops the build with Python's `SystemExit` text, as does a scaffold check that fails.
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

    /// The config note the write keeps for the unit: a fragment's own, or `config_note` over the table's configurations for stored bytes.
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
            Emitted::Bytes { .. } => Ok(self.config_note(table.configs(ordinal))),
        }
    }
}

/// An emission as an `emit` line's value (`units_stage_fixtures._emission_value`): its kind, unit id, content key and policy file, the sha256 of the bytes the write takes from it, its config note, and, for stored bytes, their address and identity.
fn emission_value(
    emitter: &Emitter,
    emission: &Emission,
    table: &UnitTable,
    ordinal: usize,
) -> std::result::Result<PyValue, BuildError> {
    let note = emitter.emitted_note(emission, table, ordinal)?;
    let (kind, digest, address, identity) = match &emission.emitted {
        Emitted::Fragment(fragment) => (
            "fragment",
            sha256_hex(fragment_bytes(fragment).map_err(failed)?.as_bytes()),
            PyValue::None,
            PyValue::None,
        ),
        Emitted::Bytes {
            body,
            source,
            identity,
        } => (
            "bytes",
            sha256_hex(body),
            PyValue::Tuple(vec![
                PyValue::str(&source.part),
                PyValue::Int(source.start),
                PyValue::Int(source.length),
                PyValue::str(&source.unit_id),
                PyValue::optional_str(source.content_key.as_deref()),
                PyValue::Bool(source.byte_copied),
                PyValue::Int(source.inflated),
            ]),
            identity.clone(),
        ),
    };
    Ok(PyValue::Tuple(vec![
        PyValue::str(kind),
        PyValue::str(&emission.unit_id),
        PyValue::str(&emission.content_key),
        PyValue::optional_str(emission.policy_file.as_deref()),
        PyValue::str(digest),
        note,
        address,
        identity,
    ]))
}

/// The `emit` stage: when the stage file holds lines from the build's run, run the build through phase 1 ([`through_units`]), then [`triage`], [`passes`] and [`route_classes`] over the corpus directory the build ran in, and emit every unit of every class in shard order, each class's units by id, as the serial write does, through one reader over that directory and the units phase's arena; answer with a `unit:<ordinal>` line an emission.
pub fn stage_emit(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let names = RouteNames::of(&input)?;
    if !stage_lines(stage_file)?.ran {
        return Ok(());
    }
    through_units(&input, stage_file, "emit", |run| {
        let failure = |failure: BuildError| WireError(format!("the emission failed: {failure}"));
        let emitter = Emitter::open(&input).map_err(failure)?;
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
        let mut prior = PriorFragmentReader::new(&run.corpus);
        let store = &run.planned.store;
        for entry in &routed.classes {
            for &ordinal in &entry.ordinals {
                let ordinal = ordinal as usize;
                let source = store
                    .source(ordinal)
                    .ok_or_else(|| WireError(format!("ordinal {ordinal} has no source")))?;
                let emission = emitter
                    .emission(
                        ordinal,
                        &source,
                        &run.table,
                        store,
                        run.drive.arena(),
                        &mut prior,
                    )
                    .map_err(failure)?;
                let value =
                    emission_value(&emitter, &emission, &run.table, ordinal).map_err(failure)?;
                stage_line(out, &format!("{UNIT_PREFIX}{ordinal}"), &value)?;
            }
        }
        Ok(())
    })
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
