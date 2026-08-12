//! Strict ingest of an `ams-m1-spec/1` dump, mirroring `kernel_io._decode` and `kernel_io.spec_of` refusal for refusal.
//!
//! Strict means the dump is read against the field sets `rebuild/pipeline/model.py` declares rather than against whatever happens to be present: a missing field and an unknown field are the same failure, because a dump from another `model.py` is a wrong dump and not a partial one. Types are checked the way Python checks them, which is stricter than JSON is — a boolean is not an integer and an integer is not a boolean, a float or an exponent-form number is not an integer at all, and an integer past `i64` is refused rather than wrapped. Fixed-arity tuples check their length, and a `Provenance` is exactly the two-element `[file, path]` array.
//!
//! Key *order* inside a record is not checked, matching Python's set comparison; canonical dumps arrive in declaration order regardless, and re-emission spells declaration order back out. Key order inside a *mapping* is preserved, because that order is load-bearing.
//!
//! Two refusals are knowingly stricter than Python's, and neither is reachable from a canonical dump. serde_json caps JSON nesting near 128 levels where Python's own recursion limit sits several times higher — authored `then:` chains are lint-capped at `model.RIGHT_CHAIN_CAP` hops, so a real dump nests a couple dozen levels at most. And a lone surrogate escape, which Python's `str` can hold and its codec round-trips, cannot land in a Rust `String` at all, so a dump carrying one is refused outright rather than resolved lossily; `spec_load`'s sources are UTF-8 YAML, which cannot produce one. The mirror is exact everywhere a dump can actually come from.
//!
//! [`parse_spec`] is where the `serde_json::Value` lives and dies. Its return type is the interned model, so nothing downstream can echo a dump by handing the parse tree back.

use serde_json::{Map, Value};

use crate::SPEC_FORMAT;
use crate::error::IngestError;
use crate::model::{
    Bitmap, BoundaryToken, CellBinding, Condition, FamilyInfo, FeatureInfo, Interner, Pairing,
    Pairings, Policy, PolicyRecord, Provenance, ResolvedSpec, Rune, ScriptRegistry, Spec, Stance,
    Stub, Surface, SurfaceRow, Sym, Table, Unlock, When,
};

const DUMP_FIELDS: &[&str] = &["format", "runes", "registry"];
const RUNE_FIELDS: &[&str] = &[
    "name",
    "codepoint",
    "sequence",
    "ductus",
    "notes",
    "mono",
    "stances",
    "policy",
];
const POLICY_FIELDS: &[&str] = &[
    "order", "refuse", "prefer", "extend", "contract", "resolve", "groups",
];
const POLICY_RECORD_FIELDS: &[&str] = &[
    "kind",
    "when",
    "id",
    "stance",
    "entry",
    "exit",
    "cell",
    "over",
    "mode",
    "by",
    "ok",
    "bind",
    "trim",
    "split",
    "against",
    "pick",
    "migrated",
    "why",
    "provenance",
];
const WHEN_FIELDS: &[&str] = &[
    "left",
    "right",
    "self_entry",
    "self_exit",
    "word",
    "feature",
];
const CONDITION_FIELDS: &[&str] = &[
    "family",
    "klass",
    "stance",
    "joined_at",
    "stroke",
    "is_token",
    "except_",
    "then",
];
const STANCE_FIELDS: &[&str] = &["name", "motion", "traits", "bitmap", "bitmaps", "surface"];
const BITMAP_FIELDS: &[&str] = &["rows", "y_offset"];
const SURFACE_FIELDS: &[&str] = &[
    "entries", "exits", "pairings", "cells", "unlocks", "require",
];
const SURFACE_ROW_FIELDS: &[&str] = &[
    "height",
    "x",
    "stroke",
    "joined",
    "joined_x",
    "withdrawal",
    "stub",
    "scope",
    "selectable",
    "ink_y",
    "x_off_convention",
    "provenance",
];
const STUB_FIELDS: &[&str] = &["cols", "inks_when"];
const PAIRING_FIELDS: &[&str] = &["entry", "exit"];
const PAIRINGS_FIELDS: &[&str] = &["never", "only"];
const CELL_BINDING_FIELDS: &[&str] =
    &["entry", "exit", "bitmap", "entry_x", "exit_x", "provenance"];
const UNLOCK_FIELDS: &[&str] = &[
    "feature",
    "entry",
    "exit",
    "pairing",
    "when",
    "why",
    "provenance",
];
const SCRIPT_REGISTRY_FIELDS: &[&str] = &[
    "heights",
    "boundary_tokens",
    "features",
    "interactions",
    "predicate_classes",
    "families",
];
const BOUNDARY_TOKEN_FIELDS: &[&str] = &["codepoint", "splits_runs"];
const FEATURE_INFO_FIELDS: &[&str] = &["kind", "description", "overlay"];
const FAMILY_INFO_FIELDS: &[&str] = &["codepoint", "sequence"];

/// Read one dump into the interned model. The `serde_json::Value` this builds is local and is dropped on the way out, so the returned [`Spec`] is the only thing emission can be written against.
pub fn parse_spec(text: &str) -> Result<Spec, IngestError> {
    let value: Value = serde_json::from_str(text)
        .map_err(|error| IngestError::new(format!("not an {SPEC_FORMAT} dump: {error}")))?;
    let Value::Object(top) = &value else {
        return Err(IngestError::new(format!(
            "not an {SPEC_FORMAT} dump: the text is not a JSON object"
        )));
    };
    match top.get("format") {
        Some(Value::String(marker)) if marker == SPEC_FORMAT => {}
        found => {
            return Err(IngestError::new(format!(
                "not an {SPEC_FORMAT} dump: format marker is {}",
                marker_of(found)
            )));
        }
    }
    let mut parser = Parser::default();
    let root = parser.resolved_spec(&value)?;
    Ok(Spec {
        symbols: parser.symbols,
        root,
    })
}

fn marker_of(found: Option<&Value>) -> String {
    match found {
        None => "absent".to_owned(),
        Some(Value::String(marker)) => format!("{marker:?}"),
        Some(other) => shape_of(other).to_owned(),
    }
}

fn shape_of(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "a boolean",
        Value::Number(_) => "a number",
        Value::String(_) => "a string",
        Value::Array(_) => "an array",
        Value::Object(_) => "an object",
    }
}

fn text(value: &Value) -> Result<&str, IngestError> {
    match value {
        Value::String(found) => Ok(found),
        other => Err(IngestError::new(format!(
            "expected a string, got {}",
            shape_of(other)
        ))),
    }
}

fn integer(value: &Value) -> Result<i64, IngestError> {
    match value {
        Value::Number(found) => found.as_i64().ok_or_else(|| {
            IngestError::new(format!(
                "expected an integer within i64, got the number {found}"
            ))
        }),
        other => Err(IngestError::new(format!(
            "expected an integer, got {}",
            shape_of(other)
        ))),
    }
}

fn boolean(value: &Value) -> Result<bool, IngestError> {
    match value {
        Value::Bool(found) => Ok(*found),
        other => Err(IngestError::new(format!(
            "expected a boolean, got {}",
            shape_of(other)
        ))),
    }
}

fn sequence(value: &Value) -> Result<&[Value], IngestError> {
    match value {
        Value::Array(found) => Ok(found),
        other => Err(IngestError::new(format!(
            "expected an array, got {}",
            shape_of(other)
        ))),
    }
}

fn mapping(value: &Value) -> Result<&Map<String, Value>, IngestError> {
    match value {
        Value::Object(found) => Ok(found),
        other => Err(IngestError::new(format!(
            "expected an object, got {}",
            shape_of(other)
        ))),
    }
}

fn fixed(value: &Value, arity: usize) -> Result<&[Value], IngestError> {
    let items = sequence(value)?;
    if items.len() != arity {
        return Err(IngestError::new(format!(
            "expected an array of {arity} entries, got {}",
            items.len()
        )));
    }
    Ok(items)
}

/// One record's object, already checked to carry exactly the fields its dataclass declares.
struct Record<'a> {
    fields: &'a Map<String, Value>,
}

impl<'a> Record<'a> {
    fn open(value: &'a Value, name: &str, declared: &[&str]) -> Result<Self, IngestError> {
        let fields = mapping(value).map_err(|_| {
            IngestError::new(format!(
                "a {name} is a JSON object, not {}",
                shape_of(value)
            ))
        })?;
        let missing: Vec<&str> = declared
            .iter()
            .copied()
            .filter(|field| !fields.contains_key(*field))
            .collect();
        let unknown: Vec<&str> = fields
            .keys()
            .map(String::as_str)
            .filter(|key| !declared.contains(key))
            .collect();
        if !missing.is_empty() || !unknown.is_empty() {
            return Err(IngestError::new(format!(
                "a {name} carries exactly the fields model.py declares: missing {missing:?}, unknown {unknown:?}"
            )));
        }
        Ok(Self { fields })
    }

    fn take<T>(
        &self,
        field: &'static str,
        read: impl FnOnce(&'a Value) -> Result<T, IngestError>,
    ) -> Result<T, IngestError> {
        read(&self.fields[field]).map_err(|error| error.at(field))
    }
}

#[derive(Default)]
struct Parser {
    symbols: Interner,
}

impl Parser {
    fn symbol(&mut self, value: &Value) -> Result<Sym, IngestError> {
        Ok(self.symbols.intern(text(value)?))
    }

    fn prose(&mut self, value: &Value) -> Result<String, IngestError> {
        Ok(text(value)?.to_owned())
    }

    fn number(&mut self, value: &Value) -> Result<i64, IngestError> {
        integer(value)
    }

    fn flag(&mut self, value: &Value) -> Result<bool, IngestError> {
        boolean(value)
    }

    fn optional<T>(
        &mut self,
        value: &Value,
        read: impl FnOnce(&mut Self, &Value) -> Result<T, IngestError>,
    ) -> Result<Option<T>, IngestError> {
        if value.is_null() {
            Ok(None)
        } else {
            read(self, value).map(Some)
        }
    }

    fn list<T>(
        &mut self,
        value: &Value,
        read: impl Fn(&mut Self, &Value) -> Result<T, IngestError>,
    ) -> Result<Vec<T>, IngestError> {
        let items = sequence(value)?;
        let mut parsed = Vec::with_capacity(items.len());
        for (seat, item) in items.iter().enumerate() {
            parsed.push(read(self, item).map_err(|error| error.at(seat.to_string()))?);
        }
        Ok(parsed)
    }

    fn table<T>(
        &mut self,
        value: &Value,
        read: impl Fn(&mut Self, &Value) -> Result<T, IngestError>,
    ) -> Result<Table<T>, IngestError> {
        let entries = mapping(value)?;
        let mut parsed = Table::new();
        for (key, item) in entries {
            let interned = self.symbols.intern(key);
            parsed.push(
                interned,
                read(self, item).map_err(|error| error.at(key.clone()))?,
            );
        }
        Ok(parsed)
    }

    fn symbol_list(&mut self, value: &Value) -> Result<Vec<Sym>, IngestError> {
        self.list(value, Self::symbol)
    }

    fn integer_list(&mut self, value: &Value) -> Result<Vec<i64>, IngestError> {
        self.list(value, Self::number)
    }

    fn symbol_table(&mut self, value: &Value) -> Result<Table<Sym>, IngestError> {
        self.table(value, Self::symbol)
    }

    fn integer_pair(&mut self, value: &Value) -> Result<(i64, i64), IngestError> {
        let items = fixed(value, 2)?;
        let first = integer(&items[0]).map_err(|error| error.at("0"))?;
        let second = integer(&items[1]).map_err(|error| error.at("1"))?;
        Ok((first, second))
    }

    fn against(&mut self, value: &Value) -> Result<(Sym, Option<Sym>), IngestError> {
        let items = fixed(value, 2)?;
        let rune = self.symbol(&items[0]).map_err(|error| error.at("0"))?;
        let record = self
            .optional(&items[1], Self::symbol)
            .map_err(|error| error.at("1"))?;
        Ok((rune, record))
    }

    fn provenance(&mut self, value: &Value) -> Result<Provenance, IngestError> {
        let items = fixed(value, 2).map_err(|_| {
            IngestError::new(format!(
                "a provenance is a [file, path] pair, not {}",
                shape_of(value)
            ))
        })?;
        let file = self.symbol(&items[0]).map_err(|error| error.at("0"))?;
        let path = self.symbol(&items[1]).map_err(|error| error.at("1"))?;
        Ok(Provenance { file, path })
    }

    fn resolved_spec(&mut self, value: &Value) -> Result<ResolvedSpec, IngestError> {
        let record = Record::open(value, "ams-m1-spec/1 dump", DUMP_FIELDS)?;
        Ok(ResolvedSpec {
            runes: record.take("runes", |item| self.table(item, Self::rune))?,
            registry: record.take("registry", |item| self.script_registry(item))?,
        })
    }

    fn rune(&mut self, value: &Value) -> Result<Rune, IngestError> {
        let record = Record::open(value, "Rune", RUNE_FIELDS)?;
        Ok(Rune {
            name: record.take("name", |item| self.symbol(item))?,
            codepoint: record.take("codepoint", |item| self.optional(item, Self::number))?,
            sequence: record.take("sequence", |item| self.optional(item, Self::symbol_list))?,
            ductus: record.take("ductus", |item| self.table(item, Self::prose))?,
            notes: record.take("notes", |item| self.optional(item, Self::prose))?,
            mono: record.take("mono", |item| self.optional(item, Self::bitmap))?,
            stances: record.take("stances", |item| self.table(item, Self::stance))?,
            policy: record.take("policy", |item| self.policy(item))?,
        })
    }

    fn policy(&mut self, value: &Value) -> Result<Policy, IngestError> {
        let record = Record::open(value, "Policy", POLICY_FIELDS)?;
        Ok(Policy {
            order: record.take("order", |item| self.symbol_list(item))?,
            refuse: record.take("refuse", |item| self.list(item, Self::policy_record))?,
            prefer: record.take("prefer", |item| self.list(item, Self::policy_record))?,
            extend: record.take("extend", |item| self.list(item, Self::policy_record))?,
            contract: record.take("contract", |item| self.list(item, Self::policy_record))?,
            resolve: record.take("resolve", |item| self.list(item, Self::policy_record))?,
            groups: record.take("groups", |item| self.table(item, Self::symbol_list))?,
        })
    }

    fn policy_record(&mut self, value: &Value) -> Result<PolicyRecord, IngestError> {
        let record = Record::open(value, "PolicyRecord", POLICY_RECORD_FIELDS)?;
        Ok(PolicyRecord {
            kind: record.take("kind", |item| self.symbol(item))?,
            when: record.take("when", |item| self.when(item))?,
            id: record.take("id", |item| self.optional(item, Self::symbol))?,
            stance: record.take("stance", |item| self.optional(item, Self::symbol))?,
            entry: record.take("entry", |item| self.optional(item, Self::symbol))?,
            exit: record.take("exit", |item| self.optional(item, Self::symbol))?,
            cell: record.take("cell", |item| self.optional(item, Self::symbol_table))?,
            over: record.take("over", |item| self.optional(item, Self::symbol_table))?,
            mode: record.take("mode", |item| self.optional(item, Self::symbol))?,
            by: record.take("by", |item| self.optional(item, Self::number))?,
            ok: record.take("ok", |item| self.optional(item, Self::integer_pair))?,
            bind: record.take("bind", |item| self.optional(item, Self::symbol))?,
            trim: record.take("trim", |item| self.optional(item, Self::number))?,
            split: record.take("split", |item| self.optional(item, Self::integer_pair))?,
            against: record.take("against", |item| self.optional(item, Self::against))?,
            pick: record.take("pick", |item| self.optional(item, Self::symbol_table))?,
            migrated: record.take("migrated", |item| self.optional(item, Self::symbol))?,
            why: record.take("why", |item| self.optional(item, Self::prose))?,
            provenance: record.take("provenance", |item| self.optional(item, Self::provenance))?,
        })
    }

    fn when(&mut self, value: &Value) -> Result<When, IngestError> {
        let record = Record::open(value, "When", WHEN_FIELDS)?;
        Ok(When {
            left: record.take("left", |item| self.optional(item, Self::condition))?,
            right: record.take("right", |item| self.optional(item, Self::condition))?,
            self_entry: record.take("self_entry", |item| self.optional(item, Self::symbol))?,
            self_exit: record.take("self_exit", |item| self.optional(item, Self::symbol))?,
            word: record.take("word", |item| self.optional(item, Self::symbol))?,
            feature: record.take("feature", |item| self.optional(item, Self::symbol))?,
        })
    }

    fn condition(&mut self, value: &Value) -> Result<Condition, IngestError> {
        let record = Record::open(value, "Condition", CONDITION_FIELDS)?;
        Ok(Condition {
            family: record.take("family", |item| self.symbol_list(item))?,
            klass: record.take("klass", |item| self.symbol_list(item))?,
            stance: record.take("stance", |item| self.symbol_list(item))?,
            joined_at: record.take("joined_at", |item| self.optional(item, Self::symbol))?,
            stroke: record.take("stroke", |item| self.optional(item, Self::symbol))?,
            is_token: record.take("is_token", |item| self.optional(item, Self::symbol))?,
            except_: record.take("except_", |item| self.list(item, Self::condition))?,
            then: record.take("then", |item| self.optional(item, Self::boxed_condition))?,
        })
    }

    fn boxed_condition(&mut self, value: &Value) -> Result<Box<Condition>, IngestError> {
        self.condition(value).map(Box::new)
    }

    fn stance(&mut self, value: &Value) -> Result<Stance, IngestError> {
        let record = Record::open(value, "Stance", STANCE_FIELDS)?;
        Ok(Stance {
            name: record.take("name", |item| self.symbol(item))?,
            motion: record.take("motion", |item| self.symbol(item))?,
            traits: record.take("traits", |item| self.symbol_list(item))?,
            bitmap: record.take("bitmap", |item| self.bitmap(item))?,
            bitmaps: record.take("bitmaps", |item| self.table(item, Self::bitmap))?,
            surface: record.take("surface", |item| self.surface(item))?,
        })
    }

    fn bitmap(&mut self, value: &Value) -> Result<Bitmap, IngestError> {
        let record = Record::open(value, "Bitmap", BITMAP_FIELDS)?;
        Ok(Bitmap {
            rows: record.take("rows", |item| self.symbol_list(item))?,
            y_offset: record.take("y_offset", |item| self.number(item))?,
        })
    }

    fn surface(&mut self, value: &Value) -> Result<Surface, IngestError> {
        let record = Record::open(value, "Surface", SURFACE_FIELDS)?;
        Ok(Surface {
            entries: record.take("entries", |item| self.table(item, Self::surface_row))?,
            exits: record.take("exits", |item| self.table(item, Self::surface_row))?,
            pairings: record.take("pairings", |item| self.pairings(item))?,
            cells: record.take("cells", |item| self.list(item, Self::cell_binding))?,
            unlocks: record.take("unlocks", |item| self.list(item, Self::unlock))?,
            require: record.take("require", |item| self.symbol_list(item))?,
        })
    }

    fn surface_row(&mut self, value: &Value) -> Result<SurfaceRow, IngestError> {
        let record = Record::open(value, "SurfaceRow", SURFACE_ROW_FIELDS)?;
        Ok(SurfaceRow {
            height: record.take("height", |item| self.symbol(item))?,
            x: record.take("x", |item| self.number(item))?,
            stroke: record.take("stroke", |item| self.optional(item, Self::symbol))?,
            joined: record.take("joined", |item| self.optional(item, Self::symbol))?,
            joined_x: record.take("joined_x", |item| self.optional(item, Self::number))?,
            withdrawal: record.take("withdrawal", |item| self.optional(item, Self::symbol))?,
            stub: record.take("stub", |item| self.optional(item, Self::stub))?,
            scope: record.take("scope", |item| self.list(item, Self::condition))?,
            selectable: record.take("selectable", |item| self.flag(item))?,
            ink_y: record.take("ink_y", |item| self.optional(item, Self::number))?,
            x_off_convention: record.take("x_off_convention", |item| self.flag(item))?,
            provenance: record.take("provenance", |item| self.optional(item, Self::provenance))?,
        })
    }

    fn stub(&mut self, value: &Value) -> Result<Stub, IngestError> {
        let record = Record::open(value, "Stub", STUB_FIELDS)?;
        Ok(Stub {
            cols: record.take("cols", |item| self.integer_list(item))?,
            inks_when: record.take("inks_when", |item| self.symbol(item))?,
        })
    }

    fn pairing(&mut self, value: &Value) -> Result<Pairing, IngestError> {
        let record = Record::open(value, "Pairing", PAIRING_FIELDS)?;
        Ok(Pairing {
            entry: record.take("entry", |item| self.symbol(item))?,
            exit: record.take("exit", |item| self.symbol(item))?,
        })
    }

    fn pairing_list(&mut self, value: &Value) -> Result<Vec<Pairing>, IngestError> {
        self.list(value, Self::pairing)
    }

    fn pairings(&mut self, value: &Value) -> Result<Pairings, IngestError> {
        let record = Record::open(value, "Pairings", PAIRINGS_FIELDS)?;
        Ok(Pairings {
            never: record.take("never", |item| self.list(item, Self::pairing))?,
            only: record.take("only", |item| self.optional(item, Self::pairing_list))?,
        })
    }

    fn cell_binding(&mut self, value: &Value) -> Result<CellBinding, IngestError> {
        let record = Record::open(value, "CellBinding", CELL_BINDING_FIELDS)?;
        Ok(CellBinding {
            entry: record.take("entry", |item| self.symbol(item))?,
            exit: record.take("exit", |item| self.symbol(item))?,
            bitmap: record.take("bitmap", |item| self.symbol(item))?,
            entry_x: record.take("entry_x", |item| self.optional(item, Self::number))?,
            exit_x: record.take("exit_x", |item| self.optional(item, Self::number))?,
            provenance: record.take("provenance", |item| self.optional(item, Self::provenance))?,
        })
    }

    fn unlock(&mut self, value: &Value) -> Result<Unlock, IngestError> {
        let record = Record::open(value, "Unlock", UNLOCK_FIELDS)?;
        Ok(Unlock {
            feature: record.take("feature", |item| self.symbol(item))?,
            entry: record.take("entry", |item| self.optional(item, Self::symbol))?,
            exit: record.take("exit", |item| self.optional(item, Self::symbol))?,
            pairing: record.take("pairing", |item| self.optional(item, Self::pairing))?,
            when: record.take("when", |item| self.optional(item, Self::when))?,
            why: record.take("why", |item| self.optional(item, Self::prose))?,
            provenance: record.take("provenance", |item| self.optional(item, Self::provenance))?,
        })
    }

    fn script_registry(&mut self, value: &Value) -> Result<ScriptRegistry, IngestError> {
        let record = Record::open(value, "ScriptRegistry", SCRIPT_REGISTRY_FIELDS)?;
        Ok(ScriptRegistry {
            heights: record.take("heights", |item| self.table(item, Self::number))?,
            boundary_tokens: record.take("boundary_tokens", |item| {
                self.table(item, Self::boundary_token)
            })?,
            features: record.take("features", |item| self.table(item, Self::feature_info))?,
            interactions: record.take("interactions", |item| self.list(item, Self::symbol_list))?,
            predicate_classes: record.take("predicate_classes", |item| {
                self.table(item, Self::symbol_list)
            })?,
            families: record.take("families", |item| self.table(item, Self::family_info))?,
        })
    }

    fn boundary_token(&mut self, value: &Value) -> Result<BoundaryToken, IngestError> {
        let record = Record::open(value, "BoundaryToken", BOUNDARY_TOKEN_FIELDS)?;
        Ok(BoundaryToken {
            codepoint: record.take("codepoint", |item| self.number(item))?,
            splits_runs: record.take("splits_runs", |item| self.flag(item))?,
        })
    }

    fn feature_info(&mut self, value: &Value) -> Result<FeatureInfo, IngestError> {
        let record = Record::open(value, "FeatureInfo", FEATURE_INFO_FIELDS)?;
        Ok(FeatureInfo {
            kind: record.take("kind", |item| self.symbol(item))?,
            description: record.take("description", |item| self.prose(item))?,
            overlay: record.take("overlay", |item| self.optional(item, Self::symbol))?,
        })
    }

    fn family_info(&mut self, value: &Value) -> Result<FamilyInfo, IngestError> {
        let record = Record::open(value, "FamilyInfo", FAMILY_INFO_FIELDS)?;
        Ok(FamilyInfo {
            codepoint: record.take("codepoint", |item| self.optional(item, Self::number))?,
            sequence: record.take("sequence", |item| self.optional(item, Self::symbol_list))?,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::emit::emit_spec;

    const MINIMAL: &str = r#"{"format":"ams-m1-spec/1","runes":{},"registry":{"heights":{},"boundary_tokens":{},"features":{},"interactions":[],"predicate_classes":{},"families":{}}}"#;

    const SAMPLE: &str = r#"{"format":"ams-m1-spec/1","runes":{"qsZoo":{"name":"qsZoo","codepoint":58971,"sequence":null,"ductus":{"down-then-round":"Start at the top, pull straight down, then loop back \u201cwiddershins\u201d.","flat":"A single flat pull."},"notes":"The \u00b7Zoo exit is the one that cannot join twice.\tSee the plan.","mono":{"rows":["  ## "," #  #"," #   "],"y_offset":-3},"stances":{"deep":{"name":"deep","motion":"down-then-round","traits":["half","alt"],"bitmap":{"rows":[" ####"," #  #"],"y_offset":-3},"bitmaps":{"pulled-back":{"rows":[" ###"," #  "],"y_offset":-3},"flat-top":{"rows":[" ####"],"y_offset":0}},"surface":{"entries":{"x-height":{"height":"x-height","x":0,"stroke":"rising","joined":"pulled-back","joined_x":1,"withdrawal":"safe","stub":{"cols":[0,1,2],"inks_when":"joined"},"scope":[{"family":["qsZoo"],"klass":["reaches-back"],"stance":["deep"],"joined_at":"x-height","stroke":"horizontal","is_token":"zwnj","except_":[{"family":["qsBay"],"klass":[],"stance":[],"joined_at":null,"stroke":null,"is_token":null,"except_":[{"family":[],"klass":["nub"],"stance":[],"joined_at":null,"stroke":null,"is_token":null,"except_":[],"then":null}],"then":null},{"family":[],"klass":[],"stance":["half"],"joined_at":null,"stroke":null,"is_token":null,"except_":[],"then":{"family":["qsAh"],"klass":[],"stance":[],"joined_at":null,"stroke":null,"is_token":null,"except_":[],"then":null}}],"then":{"family":["qsAh"],"klass":[],"stance":[],"joined_at":"none","stroke":null,"is_token":null,"except_":[],"then":null}}],"selectable":false,"ink_y":4,"x_off_convention":true,"provenance":["qsZoo.yaml","stances.deep.entries.x-height"]},"baseline":{"height":"baseline","x":2,"stroke":null,"joined":null,"joined_x":null,"withdrawal":null,"stub":null,"scope":[],"selectable":true,"ink_y":null,"x_off_convention":false,"provenance":null}},"exits":{"top":{"height":"top","x":5,"stroke":"falling","joined":null,"joined_x":null,"withdrawal":null,"stub":null,"scope":[],"selectable":true,"ink_y":null,"x_off_convention":false,"provenance":null},"baseline":{"height":"baseline","x":4,"stroke":null,"joined":null,"joined_x":null,"withdrawal":null,"stub":{"cols":[],"inks_when":"withdrawn"},"scope":[],"selectable":true,"ink_y":null,"x_off_convention":false,"provenance":["qsZoo.yaml","stances.deep.exits.baseline"]}},"pairings":{"never":[{"entry":"top","exit":"baseline"}],"only":[{"entry":"none","exit":"top"},{"entry":"x-height","exit":"x-height"}]},"cells":[{"entry":"x-height-withdrawn","exit":"none","bitmap":"flat-top","entry_x":1,"exit_x":null,"provenance":["qsZoo.yaml","stances.deep.cells[0]"]},{"entry":"none","exit":"top","bitmap":"pulled-back","entry_x":null,"exit_x":-2,"provenance":null}],"unlocks":[{"feature":"ss03","entry":"top","exit":null,"pairing":{"entry":"top","exit":"none"},"when":{"left":{"family":["qsBay"],"klass":[],"stance":[],"joined_at":null,"stroke":null,"is_token":null,"except_":[],"then":null},"right":{"family":["qsZoo"],"klass":["reaches-back"],"stance":["deep"],"joined_at":"x-height","stroke":"horizontal","is_token":"zwnj","except_":[{"family":["qsBay"],"klass":[],"stance":[],"joined_at":null,"stroke":null,"is_token":null,"except_":[{"family":[],"klass":["nub"],"stance":[],"joined_at":null,"stroke":null,"is_token":null,"except_":[],"then":null}],"then":null},{"family":[],"klass":[],"stance":["half"],"joined_at":null,"stroke":null,"is_token":null,"except_":[],"then":{"family":["qsAh"],"klass":[],"stance":[],"joined_at":null,"stroke":null,"is_token":null,"except_":[],"then":null}}],"then":{"family":["qsAh"],"klass":[],"stance":[],"joined_at":"none","stroke":null,"is_token":null,"except_":[],"then":null}},"self_entry":"live","self_exit":"none","word":"medial","feature":"ss03"},"why":"The author\u2019s own reason \u2014 it reads \"wrong\" otherwise.","provenance":["qsZoo.yaml","stances.deep.unlocks[0]"]}],"require":["entry","exit"]}},"bare":{"name":"bare","motion":"flat","traits":[],"bitmap":{"rows":[],"y_offset":0},"bitmaps":{},"surface":{"entries":{},"exits":{},"pairings":{"never":[],"only":null},"cells":[],"unlocks":[],"require":[]}}},"policy":{"order":["deep","bare"],"refuse":[{"kind":"refuse","when":{"left":null,"right":{"family":[],"klass":[],"stance":[],"joined_at":null,"stroke":null,"is_token":"boundary","except_":[],"then":null},"self_entry":null,"self_exit":null,"word":null,"feature":null},"id":"no-boundary","stance":null,"entry":null,"exit":null,"cell":null,"over":null,"mode":null,"by":null,"ok":null,"bind":null,"trim":null,"split":null,"against":null,"pick":null,"migrated":null,"why":null,"provenance":["qsZoo.yaml","policy.refuse[0]"]}],"prefer":[{"kind":"prefer","when":{"left":{"family":["qsAh","qsBay"],"klass":[],"stance":[],"joined_at":null,"stroke":null,"is_token":null,"except_":[],"then":null},"right":null,"self_entry":null,"self_exit":null,"word":null,"feature":null},"id":null,"stance":"bare","entry":"x-height","exit":"baseline","cell":{"stance":"deep","entry":"x-height"},"over":{"stance":"bare"},"mode":"absolute","by":null,"ok":null,"bind":null,"trim":null,"split":null,"against":null,"pick":null,"migrated":null,"why":null,"provenance":null}],"extend":[{"kind":"extend","when":{"left":null,"right":null,"self_entry":null,"self_exit":null,"word":null,"feature":null},"id":null,"stance":null,"entry":null,"exit":null,"cell":null,"over":null,"mode":null,"by":2,"ok":[0,3],"bind":null,"trim":null,"split":[1,-1],"against":null,"pick":null,"migrated":null,"why":null,"provenance":null}],"contract":[{"kind":"contract","when":{"left":null,"right":null,"self_entry":null,"self_exit":null,"word":null,"feature":null},"id":null,"stance":null,"entry":null,"exit":null,"cell":null,"over":null,"mode":null,"by":-1,"ok":null,"bind":"flat-top","trim":2,"split":null,"against":null,"pick":null,"migrated":null,"why":null,"provenance":null}],"resolve":[{"kind":"resolve","when":{"left":null,"right":null,"self_entry":null,"self_exit":null,"word":null,"feature":null},"id":null,"stance":null,"entry":null,"exit":null,"cell":null,"over":null,"mode":null,"by":null,"ok":null,"bind":null,"trim":null,"split":null,"against":["qsBay","no-boundary"],"pick":{"stance":"deep"},"migrated":"2026-03-01","why":"Because the \u00b7Zoo\u2014\u00b7Bay seam is the one the author settled by hand.\nIt stays settled.","provenance":null},{"kind":"resolve","when":{"left":null,"right":null,"self_entry":null,"self_exit":null,"word":null,"feature":null},"id":null,"stance":null,"entry":null,"exit":null,"cell":null,"over":null,"mode":null,"by":null,"ok":null,"bind":null,"trim":null,"split":null,"against":["qsAh",null],"pick":{},"migrated":null,"why":null,"provenance":null}],"groups":{"reaches-back":["qsAh","qsBay","qsZoo"],"nub":[]}}},"qsAh":{"name":"qsAh","codepoint":58998,"sequence":null,"ductus":{},"notes":null,"mono":null,"stances":{"bare":{"name":"bare","motion":"flat","traits":[],"bitmap":{"rows":[],"y_offset":0},"bitmaps":{},"surface":{"entries":{},"exits":{},"pairings":{"never":[],"only":null},"cells":[],"unlocks":[],"require":[]}}},"policy":{"order":[],"refuse":[],"prefer":[],"extend":[],"contract":[],"resolve":[],"groups":{}}},"qsZoo_qsAh":{"name":"qsZoo_qsAh","codepoint":null,"sequence":["qsZoo","qsAh"],"ductus":{},"notes":null,"mono":null,"stances":{"bare":{"name":"bare","motion":"flat","traits":[],"bitmap":{"rows":[],"y_offset":0},"bitmaps":{},"surface":{"entries":{},"exits":{},"pairings":{"never":[],"only":null},"cells":[],"unlocks":[],"require":[]}}},"policy":{"order":[],"refuse":[],"prefer":[],"extend":[],"contract":[],"resolve":[],"groups":{}}}},"registry":{"heights":{"x-height":5,"baseline":0,"top":8,"y6":6},"boundary_tokens":{"zwnj":{"codepoint":8204,"splits_runs":false},"space":{"codepoint":32,"splits_runs":true}},"features":{"ss03":{"kind":"capability","description":"The \ud834\udd1e overlay, spelled out.","overlay":"ss03-overlay"},"ss02":{"kind":"taste","description":"","overlay":null}},"interactions":[["ss02","ss03"],[]],"predicate_classes":{"reaches-back":["qsAh","qsZoo"],"empty":[]},"families":{"qsZoo":{"codepoint":58971,"sequence":null},"qsZoo_qsAh":{"codepoint":null,"sequence":["qsZoo","qsAh"]}}}}"#;

    fn refusal(text: &str) -> String {
        parse_spec(text)
            .expect_err("this dump should have been refused")
            .to_string()
    }

    #[test]
    fn the_minimal_dump_round_trips() {
        let spec = parse_spec(MINIMAL).expect("the minimal dump parses");
        assert_eq!(emit_spec(&spec), MINIMAL);
    }

    #[test]
    fn a_full_dump_re_emits_byte_for_byte_from_the_model() {
        let spec = parse_spec(SAMPLE).expect("the sample dump parses");
        assert_eq!(emit_spec(&spec), SAMPLE);
        assert_eq!(spec.root.runes.len(), 3);
        assert_eq!(spec.root.registry.heights.len(), 4);
    }

    #[test]
    fn re_emission_is_stable_across_a_second_pass() {
        let once = emit_spec(&parse_spec(SAMPLE).expect("the sample dump parses"));
        let twice = emit_spec(&parse_spec(&once).expect("the re-emitted dump parses"));
        assert_eq!(once, twice);
    }

    #[test]
    fn a_mapping_keeps_the_order_the_dump_stated_it_in() {
        let spec = parse_spec(SAMPLE).expect("the sample dump parses");
        let heights: Vec<&str> = spec
            .root
            .registry
            .heights
            .iter()
            .map(|(name, _)| spec.symbols.resolve(*name))
            .collect();
        assert_eq!(heights, ["x-height", "baseline", "top", "y6"]);
    }

    #[test]
    fn another_format_marker_is_refused_by_name() {
        let complaint = refusal(&MINIMAL.replace("ams-m1-spec/1", "ams-m1-spec/2"));
        assert!(
            complaint.contains("format marker is \"ams-m1-spec/2\""),
            "{complaint}"
        );
        let absent = refusal(&MINIMAL.replace("\"format\":\"ams-m1-spec/1\",", ""));
        assert!(absent.contains("format marker is absent"), "{absent}");
        let unparsable = refusal("not json at all");
        assert!(
            unparsable.contains("not an ams-m1-spec/1 dump"),
            "{unparsable}"
        );
        let scalar = refusal("\"ams-m1-spec/1\"");
        assert!(scalar.contains("the text is not a JSON object"), "{scalar}");
    }

    #[test]
    fn a_missing_field_is_refused() {
        let complaint = refusal(&MINIMAL.replace(",\"families\":{}", ""));
        assert!(complaint.contains("missing [\"families\"]"), "{complaint}");
    }

    #[test]
    fn an_unknown_field_is_refused() {
        let complaint = refusal(&MINIMAL.replace("\"families\":{}", "\"families\":{},\"depth\":4"));
        assert!(complaint.contains("unknown [\"depth\"]"), "{complaint}");
    }

    #[test]
    fn a_boolean_where_an_integer_is_declared_is_refused() {
        let complaint =
            refusal(&MINIMAL.replace("\"heights\":{}", "\"heights\":{\"baseline\":true}"));
        assert!(
            complaint.contains("expected an integer, got a boolean"),
            "{complaint}"
        );
    }

    #[test]
    fn an_integer_where_a_boolean_is_declared_is_refused() {
        let complaint = refusal(&MINIMAL.replace(
            "\"boundary_tokens\":{}",
            "\"boundary_tokens\":{\"space\":{\"codepoint\":32,\"splits_runs\":1}}",
        ));
        assert!(
            complaint.contains("expected a boolean, got a number"),
            "{complaint}"
        );
    }

    #[test]
    fn a_non_integer_number_is_refused() {
        for spelling in ["0.5", "1e2", "99999999999999999999"] {
            let complaint = refusal(&MINIMAL.replace(
                "\"heights\":{}",
                &format!("\"heights\":{{\"baseline\":{spelling}}}"),
            ));
            assert!(
                complaint.contains("expected an integer within i64"),
                "{complaint}"
            );
        }
    }

    #[test]
    fn a_negative_zero_token_is_the_integer_zero_python_reads_it_as() {
        let accepted =
            parse_spec(&MINIMAL.replace("\"heights\":{}", "\"heights\":{\"baseline\":-0}"))
                .expect("kernel_io.spec_of reads -0 as the int 0, so this side must too");
        assert_eq!(
            emit_spec(&accepted),
            MINIMAL.replace("\"heights\":{}", "\"heights\":{\"baseline\":0}")
        );
        for spelling in ["-0.0", "0.0"] {
            let complaint = refusal(&MINIMAL.replace(
                "\"heights\":{}",
                &format!("\"heights\":{{\"baseline\":{spelling}}}"),
            ));
            assert!(
                complaint.contains("expected an integer within i64"),
                "{complaint}"
            );
        }
    }

    #[test]
    fn a_provenance_that_is_not_a_pair_is_refused() {
        let complaint = refusal(&SAMPLE.replace(
            "[\"qsZoo.yaml\",\"policy.refuse[0]\"]",
            "[\"qsZoo.yaml\",\"policy.refuse[0]\",\"extra\"]",
        ));
        assert!(
            complaint.contains("a provenance is a [file, path] pair"),
            "{complaint}"
        );
        let scalar = refusal(&SAMPLE.replace(
            "[\"qsZoo.yaml\",\"policy.refuse[0]\"]",
            "\"qsZoo.yaml:policy.refuse[0]\"",
        ));
        assert!(
            scalar.contains("a provenance is a [file, path] pair"),
            "{scalar}"
        );
    }

    #[test]
    fn a_tuple_of_the_wrong_arity_is_refused() {
        let complaint = refusal(&SAMPLE.replace("\"ok\":[0,3]", "\"ok\":[0,3,5]"));
        assert!(
            complaint.contains("expected an array of 2 entries, got 3"),
            "{complaint}"
        );
    }

    #[test]
    fn a_refusal_names_where_in_the_tree_it_happened() {
        let complaint = refusal(&SAMPLE.replace("\"y_offset\":-3", "\"y_offset\":\"deep\""));
        assert!(
            complaint.contains("runes.qsZoo.mono.y_offset"),
            "{complaint}"
        );
    }
}
