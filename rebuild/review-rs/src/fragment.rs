//! A unit's shard fragment, drafted, stamped and patched: the fragment built ([`FragmentBuilder::unit_to_json`]), its shard bytes ([`fragment_bytes`]), the patch with its scaffold and stamp checks ([`FragmentBuilder::patch_fragment`], [`check_stamp`]) and the secondary junctions' records ([`junction_records`]); and the unit store's content key and id, as `rebuild/review/unit_cache.py` derives them (`carry_projection`, `carry_content_hash`, `base58_64`, `unit_id_for`).
//!
//! A fragment is a [`PyValue::Dict`] whose keys keep the order of the fragment's literal and the scaffold's, and the patch assigns in place, so the shard bytes ([`fragment_bytes`]) and the carry projection come out in CPython's forms.

use std::fmt;

use sha2::Digest as _;

use crate::change_groups::ChangeGroupError;
use crate::drafts::{DraftError, Drafts};
use crate::enrich::{EnrichError, Enriched, Flags, SecondaryJunction};
use crate::ink::InkError;
use crate::pack::PackError;
use crate::pyfmt::json::{JsonSink, fragment_text, write_fragment_text, write_sorted_dict};
use crate::pyfmt::{PyFmtError, PyValue, push_lower_hex};
use crate::scaffold::{Roster, ScaffoldError, unit_scaffold};
use crate::schema::SchemaError;
use crate::settle::SettleError;
use crate::shape::ShapeError;
use crate::subset_pack::SubsetPackError;
use crate::wire::{Init, UnitRecord, WireError};

/// The vocabulary name of `audit.SLIM_OMITTED_KEYS`, the keys a slim fragment leaves out.
pub const SLIM_OMITTED_KEYS_NAME: &str = "rebuild.review.audit.SLIM_OMITTED_KEYS";

/// `unit_cache.CARRY_PRESENTATION_KEYS`: the fragment keys the carry projection leaves out.
pub const CARRY_PRESENTATION_KEYS: [&str; 20] = [
    "id",
    "batch",
    "no_verdict",
    "exemplar",
    "class",
    "ledger_class",
    "summary",
    "config_note",
    "config_gate",
    "config_classes",
    "config_class_note",
    "explain",
    "drafts",
    "provenance",
    "secondary_junctions",
    "duplicate_group",
    "cluster",
    "ink_deltas",
    "picture_identical",
    "content_key",
];

/// The scaffold keys a fragment leads with.
pub const SCAFFOLD_HEAD: [&str; 12] = [
    "id",
    "ink_identical",
    "picture_identical",
    "junior_equivalent",
    "ink_deltas",
    "no_verdict",
    "duplicate_group",
    "cluster",
    "class",
    "ledger_class",
    "group",
    "codepoints",
];

/// The scaffold keys after the notation.
pub const SCAFFOLD_TAIL: [&str; 8] = [
    "configs",
    "config_note",
    "config_gate",
    "config_classes",
    "config_class_note",
    "render_groups",
    "kinds",
    "exemplar",
];

/// The keys the checked scaffold keys add to the stamped scaffold keys ([`stamped_scaffold_keys`]).
const CHECKED_OUTSIDE_STAMP: [&str; 11] = [
    "id",
    "no_verdict",
    "class",
    "ledger_class",
    "config_note",
    "config_gate",
    "config_classes",
    "config_class_note",
    "exemplar",
    "picture_identical",
    "ink_deltas",
];

/// `unit_cache.BASE58_ALPHABET`: Bitcoin's base58 alphabet, in ascending ASCII order.
pub const BASE58_ALPHABET: &[u8; 58] =
    b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

/// `unit_cache.ID_SYMBOLS`: the base58 symbols an id holds.
pub const ID_SYMBOLS: usize = 11;

/// A fragment the engine cannot draft, stamp or patch. A failed scaffold or stamp check ([`check_stamp`]) is one too.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FragmentError(pub String);

impl fmt::Display for FragmentError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for FragmentError {}

macro_rules! from_error {
    ($($kind:ty),*) => {
        $(impl From<$kind> for FragmentError {
            fn from(error: $kind) -> Self {
                Self(error.0)
            }
        })*
    };
}

from_error!(
    ChangeGroupError,
    DraftError,
    EnrichError,
    InkError,
    PackError,
    PyFmtError,
    ScaffoldError,
    SchemaError,
    SettleError,
    ShapeError,
    SubsetPackError,
    WireError
);

type Result<T> = std::result::Result<T, FragmentError>;

fn error<T>(message: impl Into<String>) -> Result<T> {
    Err(FragmentError(message.into()))
}

/// The scaffold keys inside the carry projection, in scaffold order.
pub fn stamped_scaffold_keys() -> impl Iterator<Item = &'static str> {
    SCAFFOLD_HEAD
        .into_iter()
        .chain(SCAFFOLD_TAIL)
        .filter(|key| !CARRY_PRESENTATION_KEYS.contains(key))
}

/// The stamped scaffold keys, then the scaffold keys outside the projection the patch writes back unchanged.
pub fn checked_scaffold_keys() -> impl Iterator<Item = &'static str> {
    stamped_scaffold_keys().chain(CHECKED_OUTSIDE_STAMP)
}

// --- the content key and the id ------------------------------------------------------------------

/// Whether a fragment key is one of [`CARRY_PRESENTATION_KEYS`], which the carry projection leaves out.
fn presentation_key(key: &PyValue) -> bool {
    matches!(key, PyValue::Str(key) if CARRY_PRESENTATION_KEYS.contains(&key.as_str()))
}

/// The fragment's entries the carry projection keeps, borrowed in the fragment's order.
fn carried_entries(fragment: &PyValue) -> Result<Vec<&(PyValue, PyValue)>> {
    Ok(entries(fragment)?
        .iter()
        .filter(|(key, _)| !presentation_key(key))
        .collect())
}

/// `unit_cache.carry_projection`: the fragment's keys outside [`CARRY_PRESENTATION_KEYS`] as sorted-key JSON.
pub fn carry_projection(fragment: &PyValue) -> Result<String> {
    let mut out = String::new();
    write_sorted_dict(&mut out, &carried_entries(fragment)?)?;
    Ok(out)
}

/// A SHA-256 that takes JSON text as a writer writes it, gathering the small pieces in a buffer it hashes whenever the buffer fills, so it hashes the bytes the text holds, in order.
struct DigestSink {
    hasher: sha2::Sha256,
    buffer: [u8; DIGEST_BUFFER],
    held: usize,
}

/// The bytes a [`DigestSink`] gathers before it hashes them.
const DIGEST_BUFFER: usize = 1024;

impl DigestSink {
    fn new() -> Self {
        Self {
            hasher: sha2::Sha256::new(),
            buffer: [0; DIGEST_BUFFER],
            held: 0,
        }
    }

    fn finish(mut self) -> [u8; 32] {
        self.hasher.update(&self.buffer[..self.held]);
        self.hasher.finalize().into()
    }
}

impl JsonSink for DigestSink {
    fn push_text(&mut self, text: &str) {
        let bytes = text.as_bytes();
        if self.held + bytes.len() > DIGEST_BUFFER {
            self.hasher.update(&self.buffer[..self.held]);
            self.held = 0;
            if bytes.len() > DIGEST_BUFFER {
                self.hasher.update(bytes);
                return;
            }
        }
        self.buffer[self.held..self.held + bytes.len()].copy_from_slice(bytes);
        self.held += bytes.len();
    }

    fn push_ascii(&mut self, byte: u8) {
        if self.held == DIGEST_BUFFER {
            self.hasher.update(self.buffer.as_slice());
            self.held = 0;
        }
        self.buffer[self.held] = byte;
        self.held += 1;
    }
}

/// The sha256 digest of the carry projection's bytes, hashed as the projection is written.
fn carry_digest(fragment: &PyValue) -> Result<[u8; 32]> {
    let mut sink = DigestSink::new();
    write_sorted_dict(&mut sink, &carried_entries(fragment)?)?;
    Ok(sink.finish())
}

/// `hashlib.sha256(data).hexdigest()`.
pub fn sha256_hex(data: &[u8]) -> String {
    let mut out = String::with_capacity(64);
    push_lower_hex(&mut out, &sha2::Sha256::digest(data));
    out
}

/// `unit_cache.carry_content_hash`: the sha256 of the carry projection, a unit's content key.
pub fn carry_content_hash(fragment: &PyValue) -> Result<String> {
    let mut out = String::with_capacity(64);
    push_lower_hex(&mut out, &carry_digest(fragment)?);
    Ok(out)
}

/// The content key ([`carry_content_hash`]) and the id derived from it ([`unit_id_for`]), the id read from the digest's first eight bytes, which are the 64 bits its first sixteen hex digits spell.
fn content_key_and_id(fragment: &PyValue) -> Result<(String, String)> {
    let digest = carry_digest(fragment)?;
    let mut content_key = String::with_capacity(64);
    push_lower_hex(&mut content_key, &digest);
    let head = u64::from_be_bytes(digest[..8].try_into().expect("eight bytes"));
    let mut id = String::with_capacity(2 + ID_SYMBOLS);
    id.push_str("u-");
    id.extend(
        base58_symbols(head)
            .iter()
            .map(|&symbol| char::from(symbol)),
    );
    Ok((content_key, id))
}

/// A 64-bit value as [`ID_SYMBOLS`] base58 symbols, most significant first, padded with the zero symbol.
fn base58_symbols(mut value: u64) -> [u8; ID_SYMBOLS] {
    let mut symbols = [0u8; ID_SYMBOLS];
    for symbol in symbols.iter_mut().rev() {
        *symbol = BASE58_ALPHABET[(value % 58) as usize];
        value /= 58;
    }
    symbols
}

/// `unit_cache.base58_64`: the digest's first 64 bits as [`ID_SYMBOLS`] base58 symbols, most significant first, padded with the zero symbol.
pub fn base58_64(digest_hex: &str) -> Result<String> {
    let head = digest_hex.get(..16).unwrap_or(digest_hex);
    let Ok(value) = u64::from_str_radix(head, 16) else {
        return error(format!("{digest_hex:?} does not lead with 64 bits of hex"));
    };
    Ok(base58_symbols(value)
        .iter()
        .map(|&symbol| char::from(symbol))
        .collect())
}

/// `unit_cache.unit_id_for`: `u-` and the content key's first 64 bits in base58.
pub fn unit_id_for(content_key: &str) -> Result<String> {
    Ok(format!("u-{}", base58_64(content_key)?))
}

// --- the fragment's dict ------------------------------------------------------------------------

fn entries(fragment: &PyValue) -> Result<&Vec<(PyValue, PyValue)>> {
    match fragment {
        PyValue::Dict(entries) => Ok(entries),
        other => error(format!("a fragment is a dict, not {}", other.kind())),
    }
}

fn entries_mut(fragment: &mut PyValue) -> Result<&mut Vec<(PyValue, PyValue)>> {
    match fragment {
        PyValue::Dict(entries) => Ok(entries),
        other => error(format!("a fragment is a dict, not {}", other.kind())),
    }
}

/// The value under `key`, or None.
pub fn lookup<'v>(fragment: &'v PyValue, key: &str) -> Option<&'v PyValue> {
    match fragment {
        PyValue::Dict(entries) => entries
            .iter()
            .find(|(held, _)| matches!(held, PyValue::Str(held) if held == key))
            .map(|(_, value)| value),
        _ => None,
    }
}

/// The value under `key`, moved out of the dict with None left in its place, refusing a dict without the key as a lookup of it is refused.
fn take_required(fragment: &mut PyValue, key: &str) -> Result<PyValue> {
    let held = match fragment {
        PyValue::Dict(entries) => entries
            .iter_mut()
            .find(|(held, _)| matches!(held, PyValue::Str(held) if held == key))
            .map(|(_, value)| value),
        _ => None,
    };
    held.map(|value| std::mem::replace(value, PyValue::None))
        .ok_or_else(|| FragmentError(format!("the fragment has no {key}")))
}

/// `fragment[key] = value`: replace the value in place, keeping the key's position, or append the key.
fn assign(entries: &mut Vec<(PyValue, PyValue)>, key: &str, value: PyValue) {
    match entries
        .iter_mut()
        .find(|(held, _)| matches!(held, PyValue::Str(held) if held == key))
    {
        Some((_, held)) => *held = value,
        None => entries.push((PyValue::str(key), value)),
    }
}

/// Python's `==` between two values: a dict equals a dict with the same keys and equal values in any order, a bool equals the int it counts as, and a tuple never equals a list.
pub fn py_eq(left: &PyValue, right: &PyValue) -> bool {
    match (left, right) {
        (PyValue::Bool(left), PyValue::Int(right)) | (PyValue::Int(right), PyValue::Bool(left)) => {
            i64::from(*left) == *right
        }
        (PyValue::Tuple(left), PyValue::Tuple(right))
        | (PyValue::List(left), PyValue::List(right)) => {
            left.len() == right.len() && left.iter().zip(right).all(|(a, b)| py_eq(a, b))
        }
        (PyValue::Dict(left), PyValue::Dict(right)) => {
            left.len() == right.len()
                && left.iter().all(|(key, value)| {
                    right
                        .iter()
                        .find(|(held, _)| py_eq(held, key))
                        .is_some_and(|(_, held)| py_eq(held, value))
                })
        }
        (PyValue::Set(left), PyValue::Set(right)) => {
            left.len() == right.len()
                && left
                    .iter()
                    .all(|member| right.iter().any(|held| py_eq(held, member)))
        }
        _ => left == right,
    }
}

fn str_list(items: &[String]) -> PyValue {
    PyValue::List(items.iter().map(PyValue::str).collect())
}

fn int(value: usize) -> PyValue {
    PyValue::Int(i64::try_from(value).expect("an index fits an i64"))
}

fn dict(items: Vec<(&str, PyValue)>) -> PyValue {
    PyValue::Dict(
        items
            .into_iter()
            .map(|(key, value)| (PyValue::str(key), value))
            .collect(),
    )
}

fn pair_dict(left: usize, right: usize) -> PyValue {
    dict(vec![("left", int(left)), ("right", int(right))])
}

/// The fragment's shard bytes, ASCII text framed as a shard part frames a list element.
pub fn fragment_bytes(fragment: &PyValue) -> Result<String> {
    Ok(fragment_text(fragment)?)
}

/// [`fragment_bytes`] written into `out`, which is cleared first.
pub fn fragment_bytes_into(out: &mut String, fragment: &PyValue) -> Result<()> {
    out.clear();
    Ok(write_fragment_text(out, fragment)?)
}

/// The secondary junctions' rects in the shape `patch_fragment` reads and the unit store persists, `[{"pair": [left, right], "before": ..., "after": ...}, ...]`.
pub fn junction_records(junctions: &[SecondaryJunction]) -> PyValue {
    PyValue::List(
        junctions
            .iter()
            .map(|junction| {
                dict(vec![
                    (
                        "pair",
                        PyValue::List(vec![int(junction.pair.0), int(junction.pair.1)]),
                    ),
                    ("before", junction.highlight_before.to_py()),
                    ("after", junction.highlight_after.to_py()),
                ])
            })
            .collect(),
    )
}

/// `check_stamp`: refuse a fragment whose `content_key` is not the hash of the fragment as it stands.
pub fn check_stamp(fragment: &PyValue) -> Result<()> {
    let stamp = lookup(fragment, "content_key");
    let recomputed = carry_content_hash(fragment)?;
    if stamp != Some(&PyValue::str(&recomputed)) {
        return error(format!(
            "unit {}: the content key stamped at drafting ({}) is not the key of the fragment as written ({recomputed}); a field outside the carry projection moved between the two",
            shown(lookup(fragment, "id")),
            shown(stamp)
        ));
    }
    Ok(())
}

/// A fragment value as an f-string writes it, for the checks' messages.
fn shown(value: Option<&PyValue>) -> String {
    match value {
        Some(PyValue::Str(text)) => text.clone(),
        Some(other) => other.to_typed(),
        None => "None".to_owned(),
    }
}

/// `check_scaffold`: refuse a fragment whose checked scaffold keys the patch's `scaffold` would change.
pub fn check_scaffold(fragment: &PyValue, scaffold: &PyValue) -> Result<()> {
    let mut moved = Vec::new();
    for key in checked_scaffold_keys() {
        let (Some(held), Some(patched)) = (lookup(fragment, key), lookup(scaffold, key)) else {
            return error(format!("the fragment or its scaffold has no {key}"));
        };
        if !py_eq(held, patched) {
            moved.push(key);
        }
    }
    if moved.is_empty() {
        return Ok(());
    }
    let stamped: Vec<&str> = stamped_scaffold_keys().collect();
    let inside: Vec<&str> = moved
        .iter()
        .copied()
        .filter(|key| stamped.contains(key))
        .collect();
    let outside: Vec<&str> = moved
        .iter()
        .copied()
        .filter(|key| !stamped.contains(key))
        .collect();
    let mut reasons = Vec::new();
    if !inside.is_empty() {
        reasons.push(format!(
            "{} inside the carry projection, so the stamp names other content",
            inside.join(", ")
        ));
    }
    if !outside.is_empty() {
        reasons.push(format!(
            "{} outside the carry projection, so the drafting-time check answered for other bytes than the ones that ship",
            outside.join(", ")
        ));
    }
    error(format!(
        "unit {}: the fragment was drafted under other values of {} than the write patches onto the fragment: {}",
        shown(lookup(fragment, "id")),
        moved.join(", "),
        reasons.join("; ")
    ))
}

// --- drafting and patching ----------------------------------------------------------------------

/// What every fragment one engine drafts shares: the configurations a unit can hold, which the scaffold's badge and class note read, and the keys a slim fragment leaves out.
pub struct FragmentBuilder {
    roster: Roster,
    slim_omitted: Vec<String>,
}

impl FragmentBuilder {
    /// The builder over the init's `ACCEPTANCE_CONFIGS`, `WINDOW_SCOPED_CONFIGS` (`Roster::read`) and `SLIM_OMITTED_KEYS`, which must be a tuple of `str`.
    pub fn new(init: &Init) -> Result<Self> {
        let Some(PyValue::Tuple(items)) = init.constant(SLIM_OMITTED_KEYS_NAME) else {
            return error(format!(
                "the init's {SLIM_OMITTED_KEYS_NAME} is not a tuple"
            ));
        };
        let slim_omitted = items
            .iter()
            .map(|item| match item {
                PyValue::Str(key) => Ok(key.clone()),
                other => error(format!(
                    "the init's {SLIM_OMITTED_KEYS_NAME} holds {}, not a str",
                    other.kind()
                )),
            })
            .collect::<Result<_>>()?;
        Ok(Self {
            roster: Roster::read(init)?,
            slim_omitted,
        })
    }

    /// Machine-approved, or exempt by its ledger class.
    fn slim(unit: &UnitRecord, flags: &Flags) -> bool {
        flags.ink_identical || flags.picture_identical || flags.junior_equivalent || unit.no_verdict
    }

    /// The unit's fragment as phase 1 drafts it, with `final_class` (the change group) as its class and `ink_deltas` as the comparator found them, stamped with its content key and the id derived from it, which is written onto the unit too, and then, unless the unit is slim, given the `drafts` the closure returns for the stamped unit. A slim fragment omits the `SLIM_OMITTED_KEYS`.
    pub fn unit_to_json(
        &self,
        unit: &mut UnitRecord,
        flags: &Flags,
        ink_deltas: &[(String, String)],
        final_class: Option<&str>,
        enriched: &Enriched,
        drafts: impl FnOnce(&UnitRecord) -> std::result::Result<Drafts, DraftError>,
    ) -> Result<PyValue> {
        let mut scaffold = unit_scaffold(unit, flags, ink_deltas, &self.roster);
        if let Some(class) = final_class {
            assign(entries_mut(&mut scaffold)?, "class", PyValue::str(class));
        }
        let geometry = &enriched.geometry;
        let mut fragment: Vec<(PyValue, PyValue)> = Vec::with_capacity(44);
        for key in SCAFFOLD_HEAD {
            fragment.push((PyValue::str(key), take_required(&mut scaffold, key)?));
        }
        fragment.push((
            PyValue::str("text_entities"),
            PyValue::str(&enriched.text_entities),
        ));
        fragment.push((PyValue::str("notation"), PyValue::str(&enriched.notation)));
        fragment.push((
            PyValue::str("notation_tokens"),
            str_list(&enriched.notation_tokens),
        ));
        for key in SCAFFOLD_TAIL {
            fragment.push((PyValue::str(key), take_required(&mut scaffold, key)?));
        }
        let rest = vec![
            (
                "before",
                dict(vec![
                    ("glyphs", str_list(&geometry.before_glyphs)),
                    ("junctions", str_list(&geometry.before_junctions)),
                ]),
            ),
            (
                "after",
                dict(vec![
                    ("cells", str_list(&geometry.after_cells)),
                    ("junctions", str_list(&geometry.after_junctions)),
                    (
                        "extensions",
                        PyValue::List(
                            geometry
                                .after_extensions
                                .iter()
                                .map(|&value| PyValue::Int(value))
                                .collect(),
                        ),
                    ),
                ]),
            ),
            (
                "diff_positions",
                PyValue::List(
                    geometry
                        .diff_positions
                        .iter()
                        .map(|&value| int(value))
                        .collect(),
                ),
            ),
            (
                "pair",
                geometry
                    .pair
                    .map_or(PyValue::None, |(left, right)| pair_dict(left, right)),
            ),
            (
                "pair_codepoints",
                geometry
                    .pair_codepoints
                    .map_or(PyValue::None, |(start, end)| {
                        PyValue::List(vec![int(start), int(end)])
                    }),
            ),
            (
                "highlight",
                dict(vec![
                    ("before", geometry.highlight_before.to_py()),
                    ("after", geometry.highlight_after.to_py()),
                ]),
            ),
            (
                "boundary_marks",
                PyValue::List(
                    geometry
                        .boundary_marks
                        .iter()
                        .map(|mark| mark.to_py())
                        .collect(),
                ),
            ),
            (
                "secondary_junctions",
                if geometry.secondary_junctions.is_empty() {
                    PyValue::None
                } else {
                    PyValue::List(
                        geometry
                            .secondary_junctions
                            .iter()
                            .map(|junction| {
                                dict(vec![
                                    ("pair", pair_dict(junction.pair.0, junction.pair.1)),
                                    ("before", junction.highlight_before.to_py()),
                                    ("after", junction.highlight_after.to_py()),
                                    ("primary_unit", PyValue::None),
                                ])
                            })
                            .collect(),
                    )
                },
            ),
            ("summary", PyValue::str(&enriched.summary)),
            ("explain", PyValue::str(&enriched.explain_text)),
            ("provenance", str_list(&enriched.provenance)),
            ("drafts", PyValue::None),
            ("content_key", PyValue::None),
        ];
        fragment.extend(
            rest.into_iter()
                .map(|(key, value)| (PyValue::str(key), value)),
        );
        let slim = Self::slim(unit, flags);
        if slim {
            for key in &self.slim_omitted {
                let Some(position) = fragment
                    .iter()
                    .position(|(held, _)| matches!(held, PyValue::Str(held) if held == key))
                else {
                    return error(format!("a slim fragment has no {key} to omit"));
                };
                fragment.remove(position);
            }
        }
        let mut fragment = PyValue::Dict(fragment);
        let (content_key, id) = content_key_and_id(&fragment)?;
        let entries = entries_mut(&mut fragment)?;
        assign(entries, "content_key", PyValue::Str(content_key));
        unit.unit_id.clone_from(&id);
        assign(entries, "id", PyValue::Str(id));
        if !slim {
            let drafts = drafts(unit)?;
            assign(entries_mut(&mut fragment)?, "drafts", drafts.to_py());
        }
        Ok(fragment)
    }

    /// Rewrite the fragment's scaffold from the unit as the write holds it (its injected duplicate group, cluster and class among them) with the phase-1 `flags` and the store's `ink_deltas`, and rebuild its secondary junctions from `junctions` under `primary_units`, pairing the two as `zip` does and dropping a suppressed one. With `check`, `check_scaffold` runs first.
    #[allow(clippy::too_many_arguments)]
    pub fn patch_fragment(
        &self,
        fragment: &mut PyValue,
        unit: &UnitRecord,
        flags: &Flags,
        ink_deltas: &[(String, String)],
        junctions: &[SecondaryJunction],
        primary_units: &[(Option<String>, bool)],
        check: bool,
    ) -> Result<()> {
        let scaffold = unit_scaffold(unit, flags, ink_deltas, &self.roster);
        if check {
            check_scaffold(fragment, &scaffold)?;
        }
        let PyValue::Dict(scaffold) = scaffold else {
            return error("a scaffold is a dict");
        };
        let entries = entries_mut(fragment)?;
        for (key, value) in scaffold {
            let PyValue::Str(key) = key else {
                return error("a scaffold key is a str");
            };
            assign(entries, &key, value);
        }
        let rebuilt: Vec<PyValue> = junctions
            .iter()
            .zip(primary_units)
            .filter(|(_, (_, suppressed))| !suppressed)
            .map(|(junction, (primary_unit, _))| {
                dict(vec![
                    ("pair", pair_dict(junction.pair.0, junction.pair.1)),
                    ("before", junction.highlight_before.to_py()),
                    ("after", junction.highlight_after.to_py()),
                    (
                        "primary_unit",
                        PyValue::optional_str(primary_unit.as_deref()),
                    ),
                ])
            })
            .collect();
        assign(
            entries,
            "secondary_junctions",
            if rebuilt.is_empty() {
                PyValue::None
            } else {
                PyValue::List(rebuilt)
            },
        );
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::enrich::Highlight;

    fn fragment(items: Vec<(&str, PyValue)>) -> PyValue {
        dict(items)
    }

    #[test]
    fn an_id_is_the_content_keys_first_64_bits_in_fixed_width_base58() {
        assert_eq!(base58_64(&"0".repeat(64)).expect("hex"), "11111111111");
        assert_eq!(
            base58_64(&format!("{}{}", "f".repeat(16), "0".repeat(48))).expect("hex"),
            "jpXCZedGfVQ"
        );
        assert_eq!(
            unit_id_for(&format!("{:016x}{}", 58u64, "0".repeat(48))).expect("hex"),
            "u-11111111121"
        );
        assert!(base58_64("not hex at all, not at all").is_err());
    }

    #[test]
    fn the_sha256_is_hashlibs() {
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn the_carry_projection_drops_the_presentation_keys_and_sorts_the_rest() {
        let unit = fragment(vec![
            ("id", PyValue::str("u-x")),
            ("summary", PyValue::str("prose")),
            ("kinds", PyValue::List(vec![PyValue::str("join")])),
            ("codepoints", PyValue::str("E650")),
            ("content_key", PyValue::None),
        ]);
        assert_eq!(
            carry_projection(&unit).expect("dumps"),
            r#"{"codepoints": "E650", "kinds": ["join"]}"#
        );
    }

    #[test]
    fn the_streamed_content_key_and_id_are_the_hash_of_the_projection_text() {
        let long = "\u{b7}x\"".repeat(900);
        let unit = fragment(vec![
            ("id", PyValue::str("u-x")),
            ("summary", PyValue::str("prose")),
            ("notation", PyValue::str(&long)),
            (
                "highlight",
                fragment(vec![
                    ("x_max", PyValue::Int(-4)),
                    ("advance_total", PyValue::Int(9)),
                ]),
            ),
            (
                "kinds",
                PyValue::List(vec![PyValue::str("join"), PyValue::None]),
            ),
            ("codepoints", PyValue::str("E650")),
            ("content_key", PyValue::None),
        ]);
        let PyValue::Dict(entries) = &unit else {
            panic!("a dict");
        };
        let kept = PyValue::Dict(
            entries
                .iter()
                .filter(|(key, _)| !presentation_key(key))
                .cloned()
                .collect(),
        );
        let text = crate::pyfmt::json::dumps_sorted(&kept).expect("dumps");
        assert!(text.len() > DIGEST_BUFFER * 2);
        assert_eq!(carry_projection(&unit).expect("dumps"), text);
        let key = sha256_hex(text.as_bytes());
        assert_eq!(carry_content_hash(&unit).expect("hashes"), key);
        assert_eq!(
            content_key_and_id(&unit).expect("hashes"),
            (key.clone(), unit_id_for(&key).expect("an id"))
        );
        let empty = fragment(vec![("id", PyValue::str("u-x"))]);
        assert_eq!(
            carry_content_hash(&empty).expect("hashes"),
            sha256_hex(b"{}")
        );
        let refused = fragment(vec![(
            "kinds",
            PyValue::Dict(vec![(PyValue::Int(1), PyValue::None)]),
        )]);
        assert_eq!(
            carry_content_hash(&refused).map_err(|failure| failure.0),
            Err("a JSON object key must be a str, not an int".to_owned())
        );
    }

    #[test]
    fn the_scaffold_key_lists_follow_the_build() {
        assert_eq!(
            stamped_scaffold_keys().collect::<Vec<_>>(),
            [
                "ink_identical",
                "junior_equivalent",
                "group",
                "codepoints",
                "configs",
                "render_groups",
                "kinds"
            ]
        );
        assert_eq!(checked_scaffold_keys().count(), 18);
    }

    #[test]
    fn python_equality_ignores_dict_order_and_counts_a_bool_as_an_int() {
        let ab = fragment(vec![("a", PyValue::Int(1)), ("b", PyValue::Bool(true))]);
        let ba = fragment(vec![("b", PyValue::Int(1)), ("a", PyValue::Int(1))]);
        assert!(py_eq(&ab, &ba));
        assert!(!py_eq(
            &PyValue::List(vec![PyValue::Int(1)]),
            &PyValue::Tuple(vec![PyValue::Int(1)])
        ));
    }

    fn rect(x: i64) -> Highlight {
        Highlight {
            x_min: x,
            x_max: x + 1,
            advance_total: 10,
        }
    }

    #[test]
    fn the_stamp_holds_while_only_presentation_moves() {
        let mut unit = fragment(vec![
            ("id", PyValue::str("")),
            ("codepoints", PyValue::str("E650:E652")),
            ("cluster", PyValue::None),
            ("content_key", PyValue::None),
        ]);
        let key = carry_content_hash(&unit).expect("hashes");
        let entries = entries_mut(&mut unit).expect("a dict");
        assign(entries, "content_key", PyValue::str(&key));
        assign(entries, "cluster", PyValue::str("c-1234abcd"));
        assert_eq!(check_stamp(&unit), Ok(()));
        assign(
            entries_mut(&mut unit).expect("a dict"),
            "codepoints",
            PyValue::str("E650"),
        );
        let refused = check_stamp(&unit).expect_err("the stamp no longer holds");
        assert!(
            refused
                .0
                .contains("a field outside the carry projection moved")
        );
    }

    #[test]
    fn the_scaffold_check_names_the_keys_on_each_side_of_the_stamp() {
        let drafted = fragment(vec![
            ("id", PyValue::str("u-a")),
            ("kinds", PyValue::List(vec![PyValue::str("join")])),
            ("class", PyValue::str("A")),
        ]);
        let mut patched = drafted.clone();
        let entries = entries_mut(&mut patched).expect("a dict");
        assign(entries, "kinds", PyValue::List(Vec::new()));
        assign(entries, "class", PyValue::str("B"));
        let missing = check_scaffold(&drafted, &patched).expect_err("the lists lack keys");
        assert!(missing.0.contains("has no"));
        let mut full_drafted = Vec::new();
        let mut full_patched = Vec::new();
        for key in checked_scaffold_keys() {
            let value = lookup(&drafted, key).cloned().unwrap_or(PyValue::None);
            let moved = lookup(&patched, key).cloned().unwrap_or(PyValue::None);
            full_drafted.push((PyValue::str(key), value));
            full_patched.push((PyValue::str(key), moved));
        }
        let (drafted, patched) = (PyValue::Dict(full_drafted), PyValue::Dict(full_patched));
        assert_eq!(check_scaffold(&drafted, &drafted), Ok(()));
        let refused = check_scaffold(&drafted, &patched).expect_err("two keys moved");
        assert_eq!(
            refused.0,
            "unit u-a: the fragment was drafted under other values of kinds, class than the write patches onto the fragment: kinds inside the carry projection, so the stamp names other content; class outside the carry projection, so the drafting-time check answered for other bytes than the ones that ship"
        );
    }

    #[test]
    fn the_junction_records_keep_the_pair_as_a_list() {
        let junction = SecondaryJunction {
            pair: (1, 2),
            highlight_before: rect(0),
            highlight_after: rect(5),
        };
        assert_eq!(
            junction_records(&[junction]),
            PyValue::List(vec![dict(vec![
                (
                    "pair",
                    PyValue::List(vec![PyValue::Int(1), PyValue::Int(2)])
                ),
                ("before", rect(0).to_py()),
                ("after", rect(5).to_py()),
            ])])
        );
    }

    fn unit() -> UnitRecord {
        let configs = vec!["default".to_owned()];
        UnitRecord {
            ordinal: 0,
            codepoints: "E650:E652".to_owned(),
            codepoint_values: vec![0xE650, 0xE652],
            new: Vec::new(),
            class: "UNMATCHED".to_owned(),
            configs: configs.clone(),
            kinds: vec!["join".to_owned()],
            group: "qsPea:qsTea".to_owned(),
            exemplar: false,
            unit_id: "u-11111111111".to_owned(),
            input_key: String::new(),
            render_groups: vec![configs],
            no_verdict: false,
            config_classes: vec![("default".to_owned(), "UNMATCHED".to_owned())],
            duplicate_group: None,
            cluster: None,
            ledger_class: String::new(),
        }
    }

    #[test]
    fn a_patch_rewrites_the_scaffold_in_place_and_keeps_the_unsuppressed_junctions() {
        let builder = FragmentBuilder {
            roster: Roster {
                acceptance: vec!["default".to_owned(), "ss10".to_owned()],
                scoped: Vec::new(),
            },
            slim_omitted: Vec::new(),
        };
        let flags = Flags {
            ink_identical: false,
            picture_identical: false,
            junior_equivalent: false,
        };
        let deltas = vec![("default".to_owned(), "d-1".to_owned())];
        let mut drafted = unit();
        let PyValue::Dict(mut items) = unit_scaffold(&drafted, &flags, &deltas, &builder.roster)
        else {
            panic!("a scaffold is a dict");
        };
        items.insert(1, (PyValue::str("notation"), PyValue::str("·Pea ·Tea")));
        items.push((PyValue::str("secondary_junctions"), PyValue::None));
        items.push((PyValue::str("content_key"), PyValue::None));
        let mut fragment = PyValue::Dict(items);
        let key = carry_content_hash(&fragment).expect("hashes");
        assign(
            entries_mut(&mut fragment).expect("a dict"),
            "content_key",
            PyValue::str(&key),
        );
        let order = |fragment: &PyValue| -> Vec<PyValue> {
            entries(fragment)
                .expect("a dict")
                .iter()
                .map(|(key, _)| key.clone())
                .collect()
        };
        let before = order(&fragment);
        drafted.duplicate_group = Some("e-22222222222".to_owned());
        drafted.cluster = Some("c-0123abcd".to_owned());
        drafted.class = "qsPea-qsTea-joins".to_owned();
        let junctions = [
            SecondaryJunction {
                pair: (0, 1),
                highlight_before: rect(0),
                highlight_after: rect(1),
            },
            SecondaryJunction {
                pair: (1, 2),
                highlight_before: rect(2),
                highlight_after: rect(3),
            },
        ];
        let primary_units = [(Some("u-33333333333".to_owned()), false), (None, true)];
        let refused = builder
            .patch_fragment(
                &mut fragment.clone(),
                &drafted,
                &flags,
                &deltas,
                &junctions,
                &primary_units,
                true,
            )
            .expect_err("the class moved outside the stamp");
        assert!(refused.0.contains("other values of class"));
        builder
            .patch_fragment(
                &mut fragment,
                &drafted,
                &flags,
                &deltas,
                &junctions,
                &primary_units,
                false,
            )
            .expect("patches");
        assert_eq!(order(&fragment), before);
        assert_eq!(check_stamp(&fragment), Ok(()));
        assert_eq!(
            lookup(&fragment, "cluster"),
            Some(&PyValue::str("c-0123abcd"))
        );
        assert_eq!(
            lookup(&fragment, "secondary_junctions"),
            Some(&PyValue::List(vec![dict(vec![
                ("pair", pair_dict(0, 1)),
                ("before", rect(0).to_py()),
                ("after", rect(1).to_py()),
                ("primary_unit", PyValue::str("u-33333333333")),
            ])]))
        );
        builder
            .patch_fragment(
                &mut fragment,
                &drafted,
                &flags,
                &deltas,
                &junctions,
                &[(None, true)],
                false,
            )
            .expect("patches");
        assert_eq!(
            lookup(&fragment, "secondary_junctions"),
            Some(&PyValue::None)
        );
    }
}
