//! One worker's per-unit work, as `rebuild/review/build.py`'s `_corpus_worker` does it: `_phase1_unit` in its order (ink, flags, re-route, enrich, change group, draft, pack, check), batched as `_phase1_batches` batches it, and `_recompute_fragment` for the verification sample.
//!
//! [`Resources`] holds what every worker reads and never changes, shared by every thread: the init, the font facts, the HarfBuzz fonts, the settlement, the mapped subset pack, the rune schema, the fragment builder, the preset dictionary and the contract check. A [`Worker`] over it holds one thread's ink readers, enricher, drafter, shaper, settler and packer, and answers a batch ([`Worker::run_batch`]) and a verification sample ([`Worker::verify`]); its memos are released at each settlement batch's end. [`Projection`] is `_UnitProjection`, written in the reply's record shapes ([`Projection::to_json`]).

use std::fmt;
use std::path::Path;

use ams_m1_kernel::index::SpecIndex;
use serde_json::{Map, Value};

use crate::change_groups::{
    ChangeGroupError, ChangeGroupInput, UNMATCHED, assign_change_group, reroutes,
};
use crate::check::{CheckError, ContractCheck};
use crate::drafts::{DraftError, Drafter};
use crate::enrich::{
    EnrichError, Enriched, Enricher, Flags, Geometry, Highlight, Pair, SecondaryJunction, Span,
};
use crate::fontfacts::FontFacts;
use crate::fragment::{FragmentBuilder, FragmentError, check_stamp, fragment_bytes, lookup};
use crate::ink::{InkError, InkReader};
use crate::pack::{PackError, Packer};
use crate::pyfmt::PyValue;
use crate::scaffold::{ScaffoldError, cluster_id_from_repr};
use crate::schema::{RuneSchema, SchemaError};
use crate::settle::{Report, SettleError, Settlement, Settler, features_for_config};
use crate::shape::{Fonts, ShapeError, Shaper};
use crate::subset_pack::{SubsetPack, SubsetPackError};
use crate::wire::{Init, Injection, UnitRecord, WireError};

/// The vocabulary name of `enrich.EXPLAIN_UNIT_BATCH_SIZE`, the settlement batch after which the memos are released.
pub const EXPLAIN_UNIT_BATCH_SIZE_NAME: &str = "rebuild.review.enrich.EXPLAIN_UNIT_BATCH_SIZE";

/// The vocabulary name of `build.CONTRACT_ERRORS_SHOWN`, the most drafting-time errors a batch reply keeps.
pub const CONTRACT_ERRORS_SHOWN_NAME: &str = "rebuild.review.build.CONTRACT_ERRORS_SHOWN";

/// A worker that cannot start, or a unit it cannot answer, with the reason.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UnitError(pub String);

impl fmt::Display for UnitError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for UnitError {}

macro_rules! from_error {
    ($($kind:ty),*) => {
        $(impl From<$kind> for UnitError {
            fn from(error: $kind) -> Self {
                Self(error.to_string())
            }
        })*
    };
}

from_error!(
    ChangeGroupError,
    CheckError,
    DraftError,
    EnrichError,
    FragmentError,
    InkError,
    PackError,
    ScaffoldError,
    SchemaError,
    SettleError,
    ShapeError,
    SubsetPackError,
    WireError
);

type Result<T> = std::result::Result<T, UnitError>;

/// A failure while answering one unit, naming the unit by its ordinal and code points.
fn unit_failure(unit: &UnitRecord, failure: &UnitError) -> UnitError {
    UnitError(format!(
        "unit {} ({}): {failure}",
        unit.ordinal, unit.codepoints
    ))
}

/// The init's `name`, which must be an int of at least `least`.
fn vocabulary_count(init: &Init, name: &str, least: i64) -> Result<usize> {
    match init.constant(name) {
        Some(PyValue::Int(value)) if *value >= least => usize::try_from(*value)
            .map_err(|_| UnitError(format!("the init's {name} is {value}, past a usize"))),
        other => Err(UnitError(format!(
            "the init's {name} is {other:?}, not an int of at least {least}"
        ))),
    }
}

/// What every worker reads and never changes, opened once from the init file and shared by every thread.
pub struct Resources {
    init: Init,
    facts: FontFacts,
    fonts: Fonts,
    settlement: Settlement,
    pack: SubsetPack,
    schema: RuneSchema,
    builder: FragmentBuilder,
    dictionary: Vec<u8>,
    check: ContractCheck,
    batch_size: usize,
    errors_shown: usize,
}

impl Resources {
    /// Open the init at `init_path` and everything it names: the font facts, the three fonts (refusing an init whose HarfBuzz version is not the linked one), the spec, the subset pack (refusing a header that disagrees with the init), the rune schema and the preset dictionary.
    pub fn open(init_path: &Path) -> Result<Self> {
        let init = Init::read(init_path)?;
        let facts = FontFacts::read(&init.font_facts)?;
        let fonts = Fonts::open(&init, &facts)?;
        let settlement =
            Settlement::new(SpecIndex::new(init.read_spec()?), init.settlement.clone())?;
        let pack = SubsetPack::open(&init.subset_pack)?;
        let schema = RuneSchema::read(Path::new(&init.rune_schema))?;
        let builder = FragmentBuilder::new(&init)?;
        let dictionary = std::fs::read(&init.dictionary).map_err(|failure| {
            UnitError(format!(
                "cannot read the dictionary {}: {failure}",
                init.dictionary
            ))
        })?;
        let check = ContractCheck::new(&init)?;
        let batch_size = vocabulary_count(&init, EXPLAIN_UNIT_BATCH_SIZE_NAME, 1)?;
        let errors_shown = vocabulary_count(&init, CONTRACT_ERRORS_SHOWN_NAME, 0)?;
        Ok(Self {
            init,
            facts,
            fonts,
            settlement,
            pack,
            schema,
            builder,
            dictionary,
            check,
            batch_size,
            errors_shown,
        })
    }
}

/// `_UnitProjection`: what the parent's whole-corpus passes read of one recomputed unit, with its deflated fragment (empty for the verification sample, which deflates nothing) and the length of its shard bytes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Projection {
    pub ordinal: i64,
    pub codepoints: String,
    pub unit_id: String,
    pub input_key: String,
    pub content_key: String,
    pub flags: Flags,
    pub ink_deltas: Vec<(String, String)>,
    pub diffs_digest: String,
    pub cluster: String,
    pub change_group: String,
    pub ledger_class: String,
    pub geometry: Geometry,
    pub mismatches: Vec<String>,
    pub raw_length: usize,
    pub packed: Vec<u8>,
}

fn pair_json((left, right): Pair) -> Value {
    Value::Array(vec![Value::from(left), Value::from(right)])
}

fn spans_json(spans: &[Span]) -> Value {
    Value::Array(spans.iter().map(|&span| pair_json(span)).collect())
}

fn strings_json(items: &[String]) -> Value {
    Value::Array(
        items
            .iter()
            .map(|item| Value::from(item.as_str()))
            .collect(),
    )
}

fn object(entries: Vec<(&str, Value)>) -> Value {
    Value::Object(
        entries
            .into_iter()
            .map(|(key, value)| (key.to_owned(), value))
            .collect::<Map<String, Value>>(),
    )
}

/// `_highlight`'s rect in its key order.
fn highlight_json(highlight: &Highlight) -> Value {
    object(vec![
        ("x_min", Value::from(highlight.x_min)),
        ("x_max", Value::from(highlight.x_max)),
        ("advance_total", Value::from(highlight.advance_total)),
    ])
}

/// A unit's ink deltas as an object in the unit's configuration order.
pub fn deltas_json(ink_deltas: &[(String, String)]) -> Value {
    Value::Object(
        ink_deltas
            .iter()
            .map(|(config, digest)| (config.clone(), Value::from(digest.as_str())))
            .collect(),
    )
}

/// `build._junction_records`: the secondary junctions' rects as the unit store keeps them.
fn junctions_json(junctions: &[SecondaryJunction]) -> Value {
    Value::Array(
        junctions
            .iter()
            .map(|junction| {
                object(vec![
                    ("pair", pair_json(junction.pair)),
                    ("before", highlight_json(&junction.highlight_before)),
                    ("after", highlight_json(&junction.highlight_after)),
                ])
            })
            .collect(),
    )
}

impl Projection {
    /// The projection as a `batch` reply carries it (§ Replies out), in the unit store's record shapes; the deflated fragment itself follows the frame, and `packed_length` is its span there.
    pub fn to_json(&self) -> Value {
        let geometry = &self.geometry;
        let proj = object(vec![
            ("pair", geometry.pair.map_or(Value::Null, pair_json)),
            ("after_spans", spans_json(&geometry.after_spans)),
            ("after_cells", strings_json(&geometry.after_cells)),
            ("after_junctions", strings_json(&geometry.after_junctions)),
            ("before_spans", spans_json(&geometry.before_spans)),
            ("before_glyphs", strings_json(&geometry.before_glyphs)),
            ("before_junctions", strings_json(&geometry.before_junctions)),
        ]);
        object(vec![
            ("ordinal", Value::from(self.ordinal)),
            ("codepoints", Value::from(self.codepoints.as_str())),
            ("id", Value::from(self.unit_id.as_str())),
            ("input_key", Value::from(self.input_key.as_str())),
            ("content_key", Value::from(self.content_key.as_str())),
            ("ink_identical", Value::from(self.flags.ink_identical)),
            (
                "picture_identical",
                Value::from(self.flags.picture_identical),
            ),
            (
                "junior_equivalent",
                Value::from(self.flags.junior_equivalent),
            ),
            ("ink_deltas", deltas_json(&self.ink_deltas)),
            ("diffs_digest", Value::from(self.diffs_digest.as_str())),
            ("cluster", Value::from(self.cluster.as_str())),
            ("change_group", Value::from(self.change_group.as_str())),
            ("ledger_class", Value::from(self.ledger_class.as_str())),
            (
                "pair_codepoints",
                geometry.pair_codepoints.map_or(Value::Null, pair_json),
            ),
            ("proj", proj),
            ("junctions", junctions_json(&geometry.secondary_junctions)),
            ("mismatches", strings_json(&self.mismatches)),
            ("raw_length", Value::from(self.raw_length)),
            ("packed_length", Value::from(self.packed.len())),
        ])
    }
}

/// One batch's answer: each unit's projection in the batch's order, and the drafting-time contract errors, capped as `_keep_contract_errors` caps them.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Batch {
    pub projections: Vec<Projection>,
    pub contract_errors: Vec<String>,
}

/// One verified unit's key as `_recompute_fragment` returns it, under the id its recomputation stamped: the content key and the ink deltas.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VerifyKey {
    pub unit_id: String,
    pub content_key: String,
    pub ink_deltas: Vec<(String, String)>,
}

/// One unit through phase 1: its projection, its drafted fragment, and the contract check's errors over it.
struct Phase1 {
    projection: Projection,
    fragment: PyValue,
    errors: Vec<String>,
}

/// One thread's worker over shared [`Resources`], with its own engines, HarfBuzz buffer, shape memo and primed compressor.
pub struct Worker<'r> {
    resources: &'r Resources,
    reader: InkReader<'r>,
    enricher: Enricher<'r>,
    drafter: Drafter<'r>,
    shaper: Shaper<'r>,
    settler: Settler<'r>,
    packer: Packer,
}

impl<'r> Worker<'r> {
    /// A worker over `resources`, with empty memos.
    pub fn new(resources: &'r Resources) -> Result<Self> {
        let shared = resources;
        Ok(Self {
            resources,
            reader: InkReader::new(&shared.init, &shared.facts)?,
            enricher: Enricher::new(
                &shared.init,
                &shared.settlement,
                &shared.pack,
                &shared.facts,
            )?,
            drafter: Drafter::new(
                &shared.init,
                shared.settlement.index(),
                &shared.facts,
                &shared.schema,
            )?,
            shaper: Shaper::new(&shared.fonts, &shared.facts),
            settler: Settler::new(&shared.settlement),
            packer: Packer::new(&shared.dictionary)?,
        })
    }

    /// The `phase1` message: each unit through phase 1 with its fragment deflated, in settlement batches of `EXPLAIN_UNIT_BATCH_SIZE` with the memos released after each (`_phase1_batches`), and the contract errors kept up to `CONTRACT_ERRORS_SHOWN`. A unit that fails stops the batch with a failure naming it.
    pub fn run_batch(&mut self, mut units: Vec<UnitRecord>) -> Result<Batch> {
        let mut batch = Batch::default();
        for chunk in units.chunks_mut(self.resources.batch_size) {
            for unit in chunk {
                let done = self
                    .phase1(unit, true)
                    .map_err(|failure| unit_failure(unit, &failure))?;
                batch.projections.push(done.projection);
                let room = self
                    .resources
                    .errors_shown
                    .saturating_sub(batch.contract_errors.len());
                batch
                    .contract_errors
                    .extend(done.errors.into_iter().take(room));
            }
            self.release();
        }
        Ok(batch)
    }

    /// The `verify` message: each sampled unit recomputed from nothing and patched with its injection (`_recompute_fragment`), in settlement batches with the memos released after each (`_released_batches`), keyed by the id the recomputation stamped.
    pub fn verify(&mut self, mut items: Vec<(UnitRecord, Injection)>) -> Result<Vec<VerifyKey>> {
        let mut keys = Vec::with_capacity(items.len());
        for chunk in items.chunks_mut(self.resources.batch_size) {
            for (unit, injection) in chunk {
                let key = self
                    .recompute(unit, injection)
                    .map_err(|failure| unit_failure(unit, &failure))?;
                keys.push(key);
            }
            self.release();
        }
        Ok(keys)
    }

    /// Forget the shapes and the settlement engines held, at a batch's end (`release_shape_memos`).
    fn release(&mut self) {
        self.shaper.release();
        self.settler.release();
    }

    /// `_recompute_fragment`: phase 1 without the deflate, then the injected duplicate group, cluster and class on the unit, the fragment patched as the write patches it under the injected primary units, and its stamp checked.
    fn recompute(&mut self, unit: &mut UnitRecord, injection: &Injection) -> Result<VerifyKey> {
        let Phase1 {
            projection,
            mut fragment,
            ..
        } = self.phase1(unit, false)?;
        unit.duplicate_group.clone_from(&injection.duplicate_group);
        unit.cluster.clone_from(&injection.cluster);
        unit.class.clone_from(&injection.class);
        self.resources.builder.patch_fragment(
            &mut fragment,
            unit,
            &projection.flags,
            &projection.ink_deltas,
            &projection.geometry.secondary_junctions,
            &injection.primary_units,
            false,
        )?;
        check_stamp(&fragment)?;
        Ok(VerifyKey {
            unit_id: unit.unit_id.clone(),
            content_key: fragment_str(&fragment, "content_key")?,
            ink_deltas: projection.ink_deltas,
        })
    }

    /// `_phase1_unit`: the ink flags and deltas, the re-route, the enrichment under the unit's first configuration, the change group of an UNMATCHED or re-routed unit, the drafted fragment, deflated when `pack`, the projection, and the contract check at `DRAFTED`. The unit's id becomes the drafting's stamp.
    fn phase1(&mut self, unit: &mut UnitRecord, pack: bool) -> Result<Phase1> {
        let resources = self.resources;
        let config = unit.configs.first().map_or("", String::as_str);
        let report: Report = self
            .settler
            .explain(&unit.codepoint_values, &features_for_config(config))?;
        let text: String = unit
            .codepoint_values
            .iter()
            .map(|&point| char::from_u32(point))
            .collect::<Option<String>>()
            .ok_or_else(|| UnitError("a code point is not a Unicode scalar value".to_owned()))?;
        let ink = self.reader.read(&mut self.shaper, &text, &unit.configs)?;
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
            &resources.init.identical_classes,
        );
        unit.ledger_class = if rerouted {
            unit.class.clone()
        } else {
            String::new()
        };
        if rerouted {
            unit.exemplar = false;
        }
        let enriched = self
            .enricher
            .enrich(&mut self.shaper, unit, &flags, &report)?;
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
                &resources.init.identical_classes,
            )?
        } else {
            ""
        };
        let final_class = (!change_group.is_empty()).then_some(change_group);
        let (drafter, shaper) = (&self.drafter, &mut self.shaper);
        let fragment = resources.builder.unit_to_json(
            unit,
            &flags,
            &ink.ink_deltas,
            final_class,
            &enriched,
            |unit| drafter.draft(shaper, unit, &enriched),
        )?;
        let (packed, raw_length) = if pack {
            let body = fragment_bytes(&fragment)?;
            self.packer.pack(body.as_bytes())?
        } else {
            (Vec::new(), 0)
        };
        let cluster = cluster_id_from_repr(
            &unit.configs,
            final_class.unwrap_or(&unit.class),
            &ink.diffs_repr,
        );
        let Enriched {
            geometry,
            mismatches,
            ..
        } = enriched;
        let projection = Projection {
            ordinal: unit.ordinal,
            codepoints: unit.codepoints.clone(),
            unit_id: unit.unit_id.clone(),
            input_key: unit.input_key.clone(),
            content_key: fragment_str(&fragment, "content_key")?,
            flags,
            ink_deltas: ink.ink_deltas,
            diffs_digest: ink.diffs_digest,
            cluster,
            change_group: change_group.to_owned(),
            ledger_class: unit.ledger_class.clone(),
            geometry,
            mismatches,
            raw_length,
            packed,
        };
        let errors = resources.check.check_drafted(&fragment)?;
        Ok(Phase1 {
            projection,
            fragment,
            errors,
        })
    }
}

fn fragment_str(fragment: &PyValue, key: &str) -> Result<String> {
    match lookup(fragment, key) {
        Some(PyValue::Str(value)) => Ok(value.clone()),
        other => Err(UnitError(format!(
            "the fragment's {key} is {other:?}, not a str"
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rect(x: i64) -> Highlight {
        Highlight {
            x_min: x,
            x_max: x + 1,
            advance_total: 10,
        }
    }

    #[test]
    fn every_thread_can_share_one_set_of_resources() {
        fn shared<T: Send + Sync>() {}
        shared::<Resources>();
    }

    #[test]
    fn a_projection_carries_the_stores_record_shapes() {
        let geometry = Geometry {
            before_glyphs: vec!["qsPea".to_owned(), "qsTea".to_owned()],
            before_junctions: vec!["y0".to_owned()],
            after_cells: vec!["qsPea.half".to_owned(), "qsTea".to_owned()],
            after_junctions: vec!["break".to_owned()],
            after_extensions: vec![0, 0],
            diff_positions: vec![0],
            ink_positions: vec![0],
            pair: Some((0, 1)),
            pair_codepoints: None,
            highlight_before: rect(0),
            highlight_after: rect(2),
            boundary_marks: Vec::new(),
            after_spans: vec![(0, 1), (1, 2)],
            before_spans: vec![(0, 1), (1, 2)],
            secondary_junctions: vec![SecondaryJunction {
                pair: (1, 2),
                highlight_before: rect(4),
                highlight_after: rect(5),
            }],
        };
        let projection = Projection {
            ordinal: 7,
            codepoints: "E650:E652".to_owned(),
            unit_id: "u-11111111111".to_owned(),
            input_key: "k".to_owned(),
            content_key: "c".to_owned(),
            flags: Flags {
                ink_identical: false,
                picture_identical: false,
                junior_equivalent: true,
            },
            ink_deltas: vec![
                ("ss10".to_owned(), "d-2".to_owned()),
                ("default".to_owned(), "d-1".to_owned()),
            ],
            diffs_digest: "f".to_owned(),
            cluster: "c-01234567".to_owned(),
            change_group: String::new(),
            ledger_class: String::new(),
            geometry,
            mismatches: vec!["line".to_owned()],
            raw_length: 40,
            packed: vec![1, 2, 3],
        };
        assert_eq!(
            projection.to_json().to_string(),
            concat!(
                r#"{"ordinal":7,"codepoints":"E650:E652","id":"u-11111111111","input_key":"k","content_key":"c","#,
                r#""ink_identical":false,"picture_identical":false,"junior_equivalent":true,"#,
                r#""ink_deltas":{"ss10":"d-2","default":"d-1"},"diffs_digest":"f","cluster":"c-01234567","#,
                r#""change_group":"","ledger_class":"","pair_codepoints":null,"#,
                r#""proj":{"pair":[0,1],"after_spans":[[0,1],[1,2]],"after_cells":["qsPea.half","qsTea"],"#,
                r#""after_junctions":["break"],"before_spans":[[0,1],[1,2]],"before_glyphs":["qsPea","qsTea"],"#,
                r#""before_junctions":["y0"]},"#,
                r#""junctions":[{"pair":[1,2],"before":{"x_min":4,"x_max":5,"advance_total":10},"#,
                r#""after":{"x_min":5,"x_max":6,"advance_total":10}}],"#,
                r#""mismatches":["line"],"raw_length":40,"packed_length":3}"#
            )
        );
    }
}
