//! The unit store, `rebuild/review/unit_store.py`'s `UnitStore`: every phase-1 product the whole-corpus passes and the store writer read, held as fixed-width columns over the unit's ordinal, with each variable-length field an offset and a count into a side column, and every string an id into a [`StringTable`].
//!
//! A row is loaded once, by [`UnitStore::load_projection`] for a recomputed unit or [`UnitStore::load_cached`] for a unit taken from the prior store, and the whole-corpus passes and the write store their results through the `set_*` methods. Each column has the width of the `array` Python holds it in, and a value that does not fit raises the `OverflowError` the `array` raises; every other refusal is the exception Python raises, with its text, in the order Python reaches it ([`StoreError`]). The unit id is not stored: it is the content key's first eight bytes ([`UnitStore::id_word`]), and [`UnitStore::index`] sorts those words once for [`UnitStore::ordinal_of`]. Beside the rows the store keeps the prior store's line of each unit ([`UnitStore::set_prior_line`]) and the prior records the plan did not load, by unit id ([`UnitStore::add_prior_record`], [`UnitStore::seal_prior_records`]), which [`UnitStore::prior_fragment`] and [`UnitStore::predicted_unchanged`] read.
//!
//! [`stage_store_load`] is the `store-load` stage of the stage fixtures.

use std::collections::{BTreeSet, HashMap};
use std::fmt;
use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;

use serde_json::Value;

use super::audit::stage_line;
use super::columns::{KeyIndex, KeyIndexError, StringTable, sorted_by_word};
use super::records::{self, CachedUnit, ParsedCachedUnit, PriorRecord};
use super::rowdelta::{Unread, py_int, type_name};
use crate::fragment::{base58_64, lookup, py_eq};
use crate::pyfmt::PyValue;
use crate::pyfmt::repr::{repr, repr_str};
use crate::wire::{Result, WireError, error};

/// The bytes of a content key and of an input key.
pub const KEY_BYTES: usize = 32;
/// The bytes of a content key a unit id encodes.
pub const ID_BYTES: usize = 8;
/// The primary-unit column's value for a junction without one.
pub const NO_PRIMARY_UNIT: u32 = u32::MAX;
/// The prior-line column's value for a unit the prior store does not name (`unit_cache.NO_PRIOR_LINE`).
pub const NO_PRIOR_LINE: u32 = u32::MAX;
/// A highlight rect's keys, in `enrich._highlight`'s order.
pub const RECT_EDGES: [&str; 3] = ["x_min", "x_max", "advance_total"];
/// `audit.MACHINE_CHECKS`, in the flag column's bit order.
pub const MACHINE_CHECKS: [&str; 3] = ["ink_identical", "picture_identical", "junior_equivalent"];

const INK_IDENTICAL: u8 = 1;
const PICTURE_IDENTICAL: u8 = 2;
const JUNIOR_EQUIVALENT: u8 = 4;
const CACHED: u8 = 8;
const SLIM: u8 = 16;
const EXEMPLAR: u8 = 32;
const NO_VERDICT: u8 = 64;
const BYTE_COPIED: u8 = 128;
const PRIOR_EXEMPLAR: u8 = 1;
const PRIOR_NO_VERDICT: u8 = 2;

/// An exception a store call raises, as Python raises it: its class and its message.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StoreError {
    pub class: &'static str,
    pub message: String,
}

impl fmt::Display for StoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.class, self.message)
    }
}

impl std::error::Error for StoreError {}

type Raised<T> = std::result::Result<T, StoreError>;

fn raise<T>(class: &'static str, message: impl Into<String>) -> Raised<T> {
    Err(StoreError {
        class,
        message: message.into(),
    })
}

fn value_error<T>(message: impl Into<String>) -> Raised<T> {
    raise("ValueError", message)
}

fn overflow<T>(message: &str) -> Raised<T> {
    raise("OverflowError", message)
}

// --- the widths of Python's `array` columns ---------------------------------------------------------

/// A value stored in a `B` column.
fn unsigned_byte(value: i64) -> Raised<u8> {
    u8::try_from(value).or_else(|_| {
        overflow(if value < 0 {
            "unsigned byte integer is less than minimum"
        } else {
            "unsigned byte integer is greater than maximum"
        })
    })
}

/// A value stored in a `b` column.
fn signed_char(value: i64) -> Raised<i8> {
    i8::try_from(value).or_else(|_| {
        overflow(if value < 0 {
            "signed char is less than minimum"
        } else {
            "signed char is greater than maximum"
        })
    })
}

/// A value stored in an `H` column.
fn unsigned_short(value: i64) -> Raised<u16> {
    u16::try_from(value).or_else(|_| {
        overflow(if value < 0 {
            "unsigned short is less than minimum"
        } else {
            "unsigned short is greater than maximum"
        })
    })
}

/// A value stored in an `i` column.
fn signed_int(value: i64) -> Raised<i32> {
    i32::try_from(value).or_else(|_| {
        overflow(if value < 0 {
            "signed integer is less than minimum"
        } else {
            "signed integer is greater than maximum"
        })
    })
}

/// A value stored in an `I` column.
fn unsigned_int(value: i64) -> Raised<u32> {
    u32::try_from(value).or_else(|_| {
        overflow(if value < 0 {
            "can't convert negative value to unsigned int"
        } else {
            "unsigned int is greater than maximum"
        })
    })
}

/// A value stored in a `Q` column, which holds every non-negative `i64`.
fn unsigned_long_long(value: i64) -> Raised<u64> {
    u64::try_from(value).or_else(|_| overflow("can't convert negative int to unsigned"))
}

/// A count stored in a `B` column.
fn count_byte(count: usize) -> Raised<u8> {
    unsigned_byte(i64::try_from(count).unwrap_or(i64::MAX))
}

fn offset(length: usize) -> u32 {
    u32::try_from(length).expect("a store's side column holds fewer than 2**32 entries")
}

// --- keys and ids -------------------------------------------------------------------------------------

/// What `bytes.fromhex` skips between two digits.
fn hex_space(letter: char) -> bool {
    matches!(letter, ' ' | '\t' | '\n' | '\r' | '\x0b' | '\x0c')
}

/// `bytes.fromhex(text)`: pairs of hex digits in either case, ASCII whitespace skipped before each pair.
fn from_hex(text: &str) -> Raised<Vec<u8>> {
    let letters: Vec<char> = text.chars().collect();
    let mut bytes = Vec::with_capacity(letters.len() / 2);
    let mut at = 0;
    while at < letters.len() {
        if hex_space(letters[at]) {
            while at < letters.len() && hex_space(letters[at]) {
                at += 1;
            }
            if at >= letters.len() {
                break;
            }
        }
        let Some(top) = letters[at].to_digit(16) else {
            return value_error(format!(
                "non-hexadecimal number found in fromhex() arg at position {at}"
            ));
        };
        at += 1;
        let Some(bottom) = letters.get(at).and_then(|letter| letter.to_digit(16)) else {
            return value_error(if at >= letters.len() {
                "fromhex() arg must contain an even number of hexadecimal digits".to_owned()
            } else {
                format!("non-hexadecimal number found in fromhex() arg at position {at}")
            });
        };
        at += 1;
        bytes.push((top * 16 + bottom) as u8);
    }
    Ok(bytes)
}

fn hex_of(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// `unit_cache.unit_id_for(content_key)`, which reads the key's first sixteen characters as `int(head, 16)` does: ASCII whitespace stripped from both ends, then hex digits in either case.
fn unit_id_for(content_key: &str) -> Raised<String> {
    let head: String = content_key.chars().take(16).collect();
    let digits = head.trim_matches(hex_space);
    let word = if !digits.is_empty() && digits.chars().all(|letter| letter.is_ascii_hexdigit()) {
        u64::from_str_radix(digits, 16).ok()
    } else {
        None
    };
    match word {
        Some(word) => Ok(id_of_word(word)),
        None => value_error(format!(
            "invalid literal for int() with base 16: {}",
            repr_str(&head)
        )),
    }
}

/// The unit id a 64-bit word encodes (`unit_cache.unit_id_for` of a key that leads with it).
fn id_of_word(word: u64) -> String {
    format!(
        "u-{}",
        base58_64(&format!("{word:016x}")).expect("sixteen hex digits read as a word")
    )
}

/// `unit_store.id_word_of`: the word a unit id encodes, or the `KeyError` Python raises for an id of any other shape.
pub fn id_word_of(unit_id: &str) -> Raised<u64> {
    records::id_word(unit_id).map_or_else(|| raise("KeyError", repr_str(unit_id)), Ok)
}

/// `audit.format_codepoints`: a window as colon-joined uppercase hex, at least four digits a code point.
fn format_codepoints(values: &[u32]) -> String {
    values
        .iter()
        .map(|value| format!("{value:04X}"))
        .collect::<Vec<_>>()
        .join(":")
}

/// `bisect.bisect_left(words, word)`, probing as CPython probes, so a search of a column not yet sorted lands where Python's does.
fn bisect_left(words: &[u64], word: u64) -> usize {
    let (mut low, mut high) = (0, words.len());
    while low < high {
        let middle = (low + high) / 2;
        if words[middle] < word {
            low = middle + 1;
        } else {
            high = middle;
        }
    }
    low
}

// --- Python's operations on a rect, as a value a store line or a projection carries ---------------------

/// `tuple(value)`'s items: a sequence's items, a `str`'s characters, `bytes`' ints, or a dict's keys.
fn items_of(value: &PyValue) -> Raised<Vec<PyValue>> {
    match value {
        PyValue::Tuple(items) | PyValue::List(items) | PyValue::Set(items) => Ok(items.clone()),
        PyValue::Str(text) => Ok(text
            .chars()
            .map(|letter| PyValue::Str(letter.to_string()))
            .collect()),
        PyValue::Bytes(bytes) => Ok(bytes
            .iter()
            .map(|&byte| PyValue::Int(i64::from(byte)))
            .collect()),
        PyValue::Dict(entries) => Ok(entries.iter().map(|(key, _)| key.clone()).collect()),
        other => raise(
            "TypeError",
            format!("'{}' object is not iterable", type_name(other)),
        ),
    }
}

/// `value[name]` for a `str` key.
fn subscript<'a>(value: &'a PyValue, name: &str) -> Raised<&'a PyValue> {
    match value {
        PyValue::Dict(_) => {
            lookup(value, name).map_or_else(|| raise("KeyError", repr_str(name)), Ok)
        }
        PyValue::List(_) => raise(
            "TypeError",
            "list indices must be integers or slices, not str",
        ),
        PyValue::Tuple(_) => raise(
            "TypeError",
            "tuple indices must be integers or slices, not str",
        ),
        PyValue::Str(_) => raise("TypeError", "string indices must be integers, not 'str'"),
        PyValue::Bytes(_) => raise(
            "TypeError",
            "byte indices must be integers or slices, not str",
        ),
        other => raise(
            "TypeError",
            format!("'{}' object is not subscriptable", type_name(other)),
        ),
    }
}

/// `int(value)`.
fn int_of(value: &PyValue) -> Raised<i64> {
    py_int(value).map_err(|failure| match failure {
        Unread::Raised(raised) => StoreError {
            class: raised.class,
            message: raised.message,
        },
        Unread::Unheld(why) => StoreError {
            class: "Unheld",
            message: why,
        },
    })
}

fn repr_of(value: &PyValue) -> String {
    repr(value).unwrap_or_else(|_| format!("<{}>", type_name(value)))
}

/// `unit_store._rect_edges(edge)`: a rect's three edges, the dict holding exactly [`RECT_EDGES`] in that order.
fn rect_edges(edge: &PyValue) -> Raised<[i64; 3]> {
    let keys = items_of(edge)?;
    let expected: Vec<PyValue> = RECT_EDGES.iter().map(|&name| PyValue::str(name)).collect();
    if keys.len() != expected.len() || !keys.iter().zip(&expected).all(|(a, b)| py_eq(a, b)) {
        return value_error(format!(
            "a junction rect edge holds ('x_min', 'x_max', 'advance_total'), not {}",
            repr_of(&PyValue::Tuple(keys))
        ));
    }
    Ok([
        int_of(subscript(edge, RECT_EDGES[0])?)?,
        int_of(subscript(edge, RECT_EDGES[1])?)?,
        int_of(subscript(edge, RECT_EDGES[2])?)?,
    ])
}

/// A pair as a junction pair's message prints it: a list's `[a, b]` or a tuple's `(a, b)`.
fn pair_text(pair: (i64, i64), list: bool) -> String {
    if list {
        format!("[{}, {}]", pair.0, pair.1)
    } else {
        format!("({}, {})", pair.0, pair.1)
    }
}

// --- what a load takes ----------------------------------------------------------------------------------

/// The fields `load_projection` reads from a recomputed unit's primary-unit projection (`unit_store.PrimaryUnitRows`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PrimaryRows {
    pub codepoint_values: Vec<i64>,
    pub ink_identical: bool,
    pub picture_identical: bool,
    pub pair: Option<(i64, i64)>,
    pub after_spans: Vec<(i64, i64)>,
    pub after_cells: Vec<String>,
    pub after_junctions: Vec<String>,
    pub before_spans: Vec<(i64, i64)>,
    pub before_glyphs: Vec<String>,
    pub before_junctions: Vec<String>,
    pub junction_pairs: Vec<(i64, i64)>,
}

/// One recomputed unit's phase-1 projection as `load_projection` reads it (`unit_store.RecomputedProjection`): its junction rects as `(pair, before, after)` values, the pair a list as the units engine's reply carries it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Recomputed {
    pub ordinal: i64,
    pub unit_id: String,
    pub input_key: String,
    pub content_key: String,
    pub ink_identical: bool,
    pub picture_identical: bool,
    pub junior_equivalent: bool,
    pub ink_deltas: Vec<(String, String)>,
    pub diffs_digest: String,
    pub cluster: String,
    pub change_group: String,
    pub ledger_class: String,
    pub pair_codepoints: Option<(i64, i64)>,
    pub rows: PrimaryRows,
    pub junction_rects: Vec<(PyValue, PyValue, PyValue)>,
    pub mismatches: Vec<String>,
}

/// Where a prior corpus's fragment is (`unit_cache.PriorFragment`): its shard part, its byte start and length on disk, the unit id and content key it is stamped with, whether its bytes are the shard writer's own, and a deflated fragment's inflated length, 0 for a plain one.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PriorFragment {
    pub part: String,
    pub start: i64,
    pub length: i64,
    pub unit_id: String,
    pub content_key: Option<String>,
    pub byte_copied: bool,
    pub inflated: i64,
}

impl PriorFragment {
    /// The fragment as Python holds it, a `PriorFragment` with its fields in declaration order.
    pub fn value(&self) -> PyValue {
        PyValue::Record(
            "PriorFragment".to_owned(),
            vec![
                ("part".to_owned(), PyValue::str(self.part.as_str())),
                ("start".to_owned(), PyValue::Int(self.start)),
                ("length".to_owned(), PyValue::Int(self.length)),
                ("unit_id".to_owned(), PyValue::str(self.unit_id.as_str())),
                (
                    "content_key".to_owned(),
                    PyValue::optional_str(self.content_key.as_deref()),
                ),
                ("byte_copied".to_owned(), PyValue::Bool(self.byte_copied)),
                ("inflated".to_owned(), PyValue::Int(self.inflated)),
            ],
        )
    }
}

impl ParsedCachedUnit {
    /// `ParsedCachedUnit.located`: the record's address as a byte-copied [`PriorFragment`] stamped with its id and content key, or None for a record the walk has to locate.
    pub fn located(&self) -> Option<PriorFragment> {
        self.address
            .as_ref()
            .map(|(part, start, length)| PriorFragment {
                part: part.clone(),
                start: *start,
                length: *length,
                unit_id: self.prior_id.clone(),
                content_key: Some(self.content_key.clone()),
                byte_copied: true,
                inflated: self.inflated,
            })
    }
}

/// The address a recomputed unit is loaded with: a `(part, start, length)` span, or a [`PriorFragment`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LoadAddress {
    Span(String, i64, i64),
    Fragment(PriorFragment),
}

/// A primary unit as `set_primary_units` takes it: none, an ordinal, or an id the index resolves.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Target {
    Absent,
    Ordinal(i64),
    Id(String),
}

/// One unit's flag byte, unpacked (`unit_store.Flags`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Flags {
    pub ink_identical: bool,
    pub picture_identical: bool,
    pub junior_equivalent: bool,
    pub cached: bool,
    pub slim: bool,
    pub exemplar: bool,
    pub no_verdict: bool,
    pub byte_copied: bool,
}

impl Flags {
    fn of(bits: u8) -> Self {
        Self {
            ink_identical: bits & INK_IDENTICAL != 0,
            picture_identical: bits & PICTURE_IDENTICAL != 0,
            junior_equivalent: bits & JUNIOR_EQUIVALENT != 0,
            cached: bits & CACHED != 0,
            slim: bits & SLIM != 0,
            exemplar: bits & EXEMPLAR != 0,
            no_verdict: bits & NO_VERDICT != 0,
            byte_copied: bits & BYTE_COPIED != 0,
        }
    }

    /// The flags as Python holds them, a `Flags` named tuple.
    pub fn value(&self) -> PyValue {
        let fields = [
            ("ink_identical", self.ink_identical),
            ("picture_identical", self.picture_identical),
            ("junior_equivalent", self.junior_equivalent),
            ("cached", self.cached),
            ("slim", self.slim),
            ("exemplar", self.exemplar),
            ("no_verdict", self.no_verdict),
            ("byte_copied", self.byte_copied),
        ];
        PyValue::Record(
            "Flags".to_owned(),
            fields
                .iter()
                .map(|&(name, flag)| (name.to_owned(), PyValue::Bool(flag)))
                .collect(),
        )
    }
}

/// A unit as the primary-unit resolution pass compares it (`enrich.PrimaryUnitProjection`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PrimaryUnitProjection {
    pub unit_id: String,
    pub codepoint_values: Vec<u32>,
    pub ink_identical: bool,
    pub picture_identical: bool,
    pub pair: Option<(i64, i64)>,
    pub after_spans: Vec<(i64, i64)>,
    pub after_cells: Vec<String>,
    pub after_junctions: Vec<String>,
    pub before_spans: Vec<(i64, i64)>,
    pub before_glyphs: Vec<String>,
    pub before_junctions: Vec<String>,
    pub junction_pairs: Vec<(i64, i64)>,
}

fn pair_tuple(pair: (i64, i64)) -> PyValue {
    PyValue::Tuple(vec![PyValue::Int(pair.0), PyValue::Int(pair.1)])
}

fn pair_list(pair: (i64, i64)) -> PyValue {
    PyValue::List(vec![PyValue::Int(pair.0), PyValue::Int(pair.1)])
}

fn optional_pair(pair: Option<(i64, i64)>) -> PyValue {
    pair.map_or(PyValue::None, pair_tuple)
}

fn pairs_tuple(pairs: &[(i64, i64)]) -> PyValue {
    PyValue::Tuple(pairs.iter().map(|&pair| pair_tuple(pair)).collect())
}

fn texts_list(texts: &[String]) -> PyValue {
    PyValue::List(
        texts
            .iter()
            .map(|text| PyValue::str(text.as_str()))
            .collect(),
    )
}

impl PrimaryUnitProjection {
    /// The projection as Python holds it, a `PrimaryUnitProjection` with tuples.
    pub fn value(&self) -> PyValue {
        PyValue::Record(
            "PrimaryUnitProjection".to_owned(),
            vec![
                ("unit_id".to_owned(), PyValue::str(self.unit_id.as_str())),
                (
                    "codepoint_values".to_owned(),
                    PyValue::Tuple(
                        self.codepoint_values
                            .iter()
                            .map(|&value| PyValue::Int(i64::from(value)))
                            .collect(),
                    ),
                ),
                (
                    "ink_identical".to_owned(),
                    PyValue::Bool(self.ink_identical),
                ),
                (
                    "picture_identical".to_owned(),
                    PyValue::Bool(self.picture_identical),
                ),
                ("pair".to_owned(), optional_pair(self.pair)),
                ("after_spans".to_owned(), pairs_tuple(&self.after_spans)),
                (
                    "after_cells".to_owned(),
                    PyValue::str_tuple(&self.after_cells),
                ),
                (
                    "after_junctions".to_owned(),
                    PyValue::str_tuple(&self.after_junctions),
                ),
                ("before_spans".to_owned(), pairs_tuple(&self.before_spans)),
                (
                    "before_glyphs".to_owned(),
                    PyValue::str_tuple(&self.before_glyphs),
                ),
                (
                    "before_junctions".to_owned(),
                    PyValue::str_tuple(&self.before_junctions),
                ),
                (
                    "junction_pairs".to_owned(),
                    pairs_tuple(&self.junction_pairs),
                ),
            ],
        )
    }
}

/// Which side of the primary-unit projection a row loads.
#[derive(Clone, Copy)]
enum Side {
    After,
    Before,
}

// --- the store ------------------------------------------------------------------------------------------

/// Per-unit state as columns over the ordinal (`unit_store.UnitStore`); see the module's documentation.
#[derive(Clone, Debug)]
pub struct UnitStore {
    n: usize,
    loaded: Vec<u8>,
    strings: StringTable,
    flags: Vec<u8>,
    diffs_digest: Vec<u32>,
    cluster: Vec<u32>,
    change_group: Vec<u32>,
    ledger_class: Vec<u32>,
    pair_l: Vec<i8>,
    pair_r: Vec<i8>,
    cell_pair_l: Vec<i8>,
    cell_pair_r: Vec<i8>,
    content_keys: Vec<u8>,
    input_keys: Vec<u8>,
    src_part: Vec<u32>,
    src_start: Vec<u64>,
    src_len: Vec<u32>,
    src_inflated: Vec<u32>,
    out_part: Vec<u32>,
    out_start: Vec<u64>,
    out_len: Vec<u32>,
    out_inflated: Vec<u32>,
    prior_class: Vec<u32>,
    duplicate_group: Vec<u32>,
    policy_file: Vec<u32>,
    config_note: Vec<u32>,
    codepoints_start: Vec<u32>,
    codepoints_n: Vec<u8>,
    deltas_start: Vec<u32>,
    deltas_n: Vec<u8>,
    after_start: Vec<u32>,
    after_junction_start: Vec<u32>,
    after_n: Vec<u8>,
    before_start: Vec<u32>,
    before_junction_start: Vec<u32>,
    before_n: Vec<u8>,
    junction_start: Vec<u32>,
    junction_n: Vec<u8>,
    codepoint_values: Vec<u16>,
    delta_config: Vec<u32>,
    delta_digest: Vec<u32>,
    after_spans: Vec<u16>,
    after_cells: Vec<u32>,
    after_junctions: Vec<u32>,
    before_spans: Vec<u16>,
    before_glyphs: Vec<u32>,
    before_junctions: Vec<u32>,
    junction_pairs: Vec<i8>,
    rect_edges: Vec<i32>,
    primary_unit: Vec<u32>,
    primary_unit_suppressed: Vec<u8>,
    cached_primary_unit: Vec<u32>,
    cached_primary_unit_suppressed: Vec<u8>,
    mismatches: HashMap<usize, Vec<String>>,
    id_index: Option<(Vec<u64>, Vec<u32>)>,
    shard_len: Vec<u32>,
    prior_line: Vec<u32>,
    by_id_parts: Vec<String>,
    by_id_part_ids: HashMap<String, usize>,
    by_id_words: Vec<u64>,
    by_id_part: Vec<u16>,
    by_id_start: Vec<u32>,
    by_id_len: Vec<u32>,
    by_id_inflated: Vec<u32>,
    by_id_class: Vec<u32>,
    by_id_group: Vec<u32>,
    by_id_bits: Vec<u8>,
    by_id_units: Vec<u32>,
}

impl UnitStore {
    /// A store of `n` rows over `strings`, taking `input_keys`, the load's input-key column, as its own, or a zeroed column without one; a column of another length is refused.
    pub fn new(n: usize, strings: StringTable, input_keys: Option<Vec<u8>>) -> Raised<Self> {
        if let Some(keys) = &input_keys
            && keys.len() != n * KEY_BYTES
        {
            return value_error(format!(
                "an input-key column for {n} units is {} bytes, not {}",
                n * KEY_BYTES,
                keys.len()
            ));
        }
        Ok(Self {
            n,
            loaded: vec![0; n],
            strings,
            flags: vec![0; n],
            diffs_digest: vec![0; n],
            cluster: vec![0; n],
            change_group: vec![0; n],
            ledger_class: vec![0; n],
            pair_l: vec![-1; n],
            pair_r: vec![-1; n],
            cell_pair_l: vec![-1; n],
            cell_pair_r: vec![-1; n],
            content_keys: vec![0; n * KEY_BYTES],
            input_keys: input_keys.unwrap_or_else(|| vec![0; n * KEY_BYTES]),
            src_part: vec![0; n],
            src_start: vec![0; n],
            src_len: vec![0; n],
            src_inflated: vec![0; n],
            out_part: vec![0; n],
            out_start: vec![0; n],
            out_len: vec![0; n],
            out_inflated: vec![0; n],
            prior_class: vec![0; n],
            duplicate_group: vec![0; n],
            policy_file: vec![0; n],
            config_note: vec![0; n],
            codepoints_start: vec![0; n],
            codepoints_n: vec![0; n],
            deltas_start: vec![0; n],
            deltas_n: vec![0; n],
            after_start: vec![0; n],
            after_junction_start: vec![0; n],
            after_n: vec![0; n],
            before_start: vec![0; n],
            before_junction_start: vec![0; n],
            before_n: vec![0; n],
            junction_start: vec![0; n],
            junction_n: vec![0; n],
            codepoint_values: Vec::new(),
            delta_config: Vec::new(),
            delta_digest: Vec::new(),
            after_spans: Vec::new(),
            after_cells: Vec::new(),
            after_junctions: Vec::new(),
            before_spans: Vec::new(),
            before_glyphs: Vec::new(),
            before_junctions: Vec::new(),
            junction_pairs: Vec::new(),
            rect_edges: Vec::new(),
            primary_unit: Vec::new(),
            primary_unit_suppressed: Vec::new(),
            cached_primary_unit: Vec::new(),
            cached_primary_unit_suppressed: Vec::new(),
            mismatches: HashMap::new(),
            id_index: None,
            shard_len: vec![0; n],
            prior_line: vec![NO_PRIOR_LINE; n],
            by_id_parts: Vec::new(),
            by_id_part_ids: HashMap::new(),
            by_id_words: Vec::new(),
            by_id_part: Vec::new(),
            by_id_start: Vec::new(),
            by_id_len: Vec::new(),
            by_id_inflated: Vec::new(),
            by_id_class: Vec::new(),
            by_id_group: Vec::new(),
            by_id_bits: Vec::new(),
            by_id_units: Vec::new(),
        })
    }

    pub fn len(&self) -> usize {
        self.n
    }

    pub fn is_empty(&self) -> bool {
        self.n == 0
    }

    /// `UnitStore.emptied`: a store over the same rows, string table and input keys with nothing loaded, which the plan restarts with when the prior store stops reading partway.
    pub fn emptied(self) -> Self {
        Self::new(self.n, self.strings, Some(self.input_keys))
            .expect("a store's own input-key column has its length")
    }

    /// The string table the store's ids name.
    pub fn strings(&self) -> &StringTable {
        &self.strings
    }

    fn begin(&mut self, ordinal: i64) -> Raised<usize> {
        let Some(row) = usize::try_from(ordinal).ok().filter(|&row| row < self.n) else {
            return raise(
                "IndexError",
                format!("ordinal {ordinal} is outside a store of {} units", self.n),
            );
        };
        if self.loaded[row] != 0 {
            return value_error(format!("ordinal {ordinal} is loaded twice"));
        }
        self.loaded[row] = 1;
        self.id_index = None;
        Ok(row)
    }

    fn key_range(row: usize) -> std::ops::Range<usize> {
        row * KEY_BYTES..(row + 1) * KEY_BYTES
    }

    /// `UnitStore.set_input_key`: write the unit's input key into its row before any load.
    pub fn set_input_key(&mut self, ordinal: usize, input_key: &str) -> Raised<()> {
        let inputs = from_hex(input_key)?;
        if inputs.len() != KEY_BYTES {
            return value_error(format!(
                "ordinal {ordinal}: an input key is {KEY_BYTES} bytes"
            ));
        }
        if ordinal >= self.n {
            return raise("IndexError", "bytearray index out of range");
        }
        self.input_keys[Self::key_range(ordinal)].copy_from_slice(&inputs);
        Ok(())
    }

    fn load_keys(
        &mut self,
        row: usize,
        unit_id: &str,
        content_key: &str,
        input_key: &str,
    ) -> Raised<()> {
        let content = from_hex(content_key)?;
        let inputs = from_hex(input_key)?;
        if content.len() != KEY_BYTES || inputs.len() != KEY_BYTES {
            return value_error(format!(
                "ordinal {row}: a content key and an input key are {KEY_BYTES} bytes each"
            ));
        }
        if unit_id_for(content_key)? != unit_id {
            return value_error(format!(
                "ordinal {row}: id {unit_id} is not the id of content key {content_key}"
            ));
        }
        let held = &mut self.input_keys[Self::key_range(row)];
        if held.iter().any(|&byte| byte != 0) {
            if *held != *inputs {
                return value_error(format!(
                    "ordinal {row}: the load carries input key {input_key}, not the key the plan wrote for the row"
                ));
            }
        } else {
            held.copy_from_slice(&inputs);
        }
        self.content_keys[Self::key_range(row)].copy_from_slice(&content);
        Ok(())
    }

    fn load_deltas<'d>(
        &mut self,
        row: usize,
        deltas: impl IntoIterator<Item = (&'d str, &'d str)>,
    ) -> Raised<()> {
        self.deltas_start[row] = offset(self.delta_config.len());
        let mut count = 0;
        for (config, delta) in deltas {
            let config = self.strings.id(config);
            self.delta_config.push(config);
            let delta = self.strings.id(delta);
            self.delta_digest.push(delta);
            count += 1;
        }
        self.deltas_n[row] = count_byte(count)?;
        Ok(())
    }

    /// `UnitStore._load_row`: one side of the primary-unit projection, `n` spans, `n` names and `n - 1` junctions.
    fn load_row(
        &mut self,
        row: usize,
        side: Side,
        spans: &[(i64, i64)],
        names: &[String],
        junctions: &[String],
    ) -> Raised<()> {
        let count = spans.len();
        if names.len() != count || junctions.len() != count.saturating_sub(1) {
            let label = match side {
                Side::After => "after",
                Side::Before => "before",
            };
            return value_error(format!(
                "ordinal {row}: {label} row holds {count} spans, {} names and {} junctions",
                names.len(),
                junctions.len()
            ));
        }
        let strings = &mut self.strings;
        let (start, junction_start, counts, span_column, name_column, junction_column) = match side
        {
            Side::After => (
                &mut self.after_start,
                &mut self.after_junction_start,
                &mut self.after_n,
                &mut self.after_spans,
                &mut self.after_cells,
                &mut self.after_junctions,
            ),
            Side::Before => (
                &mut self.before_start,
                &mut self.before_junction_start,
                &mut self.before_n,
                &mut self.before_spans,
                &mut self.before_glyphs,
                &mut self.before_junctions,
            ),
        };
        start[row] = offset(name_column.len());
        junction_start[row] = offset(junction_column.len());
        counts[row] = count_byte(count)?;
        for &(left, right) in spans {
            span_column.push(unsigned_short(left)?);
            span_column.push(unsigned_short(right)?);
        }
        name_column.extend(names.iter().map(|name| strings.id(name)));
        junction_column.extend(junctions.iter().map(|junction| strings.id(junction)));
        Ok(())
    }

    fn load_projection_rows(
        &mut self,
        row: usize,
        codepoint_values: &[i64],
        pair: Option<(i64, i64)>,
        after: (&[(i64, i64)], &[String], &[String]),
        before: (&[(i64, i64)], &[String], &[String]),
    ) -> Raised<()> {
        self.codepoints_start[row] = offset(self.codepoint_values.len());
        self.codepoints_n[row] = count_byte(codepoint_values.len())?;
        for &value in codepoint_values {
            self.codepoint_values.push(unsigned_short(value)?);
        }
        if let Some((left, right)) = pair {
            self.cell_pair_l[row] = signed_char(left)?;
            self.cell_pair_r[row] = signed_char(right)?;
        }
        self.load_row(row, Side::After, after.0, after.1, after.2)?;
        self.load_row(row, Side::Before, before.0, before.1, before.2)
    }

    /// `UnitStore._load_junctions`: the secondary-junction side column, one count over the junction pairs, the rects and both primary-unit columns. `lists` says whether the junction pairs print as lists in a message, as the units engine's reply holds them, or as tuples, as a parsed record does.
    fn load_junctions(
        &mut self,
        row: usize,
        junction_pairs: &[(i64, i64)],
        lists: bool,
        rects: &[(PyValue, PyValue, PyValue)],
        cached_primary_units: Option<&[(Option<String>, bool)]>,
    ) -> Raised<()> {
        let count = junction_pairs.len();
        if rects.len() != count {
            return value_error(format!(
                "ordinal {row}: {count} junction pairs beside {} junction rects",
                rects.len()
            ));
        }
        if let Some(units) = cached_primary_units
            && units.len() != count
        {
            return value_error(format!(
                "ordinal {row}: {count} junctions beside {} cached primary_units",
                units.len()
            ));
        }
        self.junction_start[row] = offset(self.primary_unit.len());
        self.junction_n[row] = count_byte(count)?;
        for (&pair, (rect_pair, before, after)) in junction_pairs.iter().zip(rects) {
            let held = items_of(rect_pair)?;
            let wanted = [PyValue::Int(pair.0), PyValue::Int(pair.1)];
            if held.len() != 2 || !held.iter().zip(&wanted).all(|(a, b)| py_eq(a, b)) {
                return value_error(format!(
                    "ordinal {row}: junction rect pair {} is not junction pair {}",
                    repr_of(rect_pair),
                    pair_text(pair, lists)
                ));
            }
            self.junction_pairs.push(signed_char(pair.0)?);
            self.junction_pairs.push(signed_char(pair.1)?);
            for edge in [before, after] {
                for value in rect_edges(edge)? {
                    self.rect_edges.push(signed_int(value)?);
                }
            }
        }
        self.primary_unit
            .extend(std::iter::repeat_n(NO_PRIMARY_UNIT, count));
        self.primary_unit_suppressed
            .extend(std::iter::repeat_n(0, count));
        match cached_primary_units {
            None => {
                self.cached_primary_unit
                    .extend(std::iter::repeat_n(0, count));
                self.cached_primary_unit_suppressed
                    .extend(std::iter::repeat_n(0, count));
            }
            Some(units) => {
                for (unit, suppressed) in units {
                    let id = self.strings.optional(unit.as_deref());
                    self.cached_primary_unit.push(id);
                    self.cached_primary_unit_suppressed
                        .push(u8::from(*suppressed));
                }
            }
        }
        Ok(())
    }

    fn load_source(&mut self, row: usize, address: Option<&LoadAddress>) -> Raised<()> {
        let (part, start, length) = match address {
            None => return Ok(()),
            Some(LoadAddress::Span(part, start, length)) => (part, *start, *length),
            Some(LoadAddress::Fragment(fragment)) => {
                if fragment.byte_copied {
                    self.flags[row] |= BYTE_COPIED;
                }
                self.src_inflated[row] = unsigned_int(fragment.inflated)?;
                (&fragment.part, fragment.start, fragment.length)
            }
        };
        self.src_part[row] = self.strings.id(part);
        self.src_start[row] = unsigned_long_long(start)?;
        self.src_len[row] = unsigned_int(length)?;
        Ok(())
    }

    /// `UnitStore.load_projection`: load one recomputed unit's projection into its row and return the row. The ordinal is `ordinal`, else the projection's; `length` is the drafted fragment's shard bytes, its [`UnitStore::shard_length`]; `no_verdict` is the ledger's exemption, which with the machine flags sets the slim bit.
    pub fn load_projection(
        &mut self,
        projection: &Recomputed,
        no_verdict: bool,
        ordinal: Option<i64>,
        address: Option<&LoadAddress>,
        length: i64,
    ) -> Raised<usize> {
        let ordinal = match ordinal {
            Some(ordinal) => ordinal,
            None if projection.ordinal < 0 => {
                return value_error(format!("no ordinal for unit {}", projection.unit_id));
            }
            None => projection.ordinal,
        };
        let rows = &projection.rows;
        if (rows.ink_identical, rows.picture_identical)
            != (projection.ink_identical, projection.picture_identical)
        {
            return value_error(format!(
                "ordinal {ordinal}: the primary-unit projection's ink flags are not the unit's"
            ));
        }
        let row = self.begin(ordinal)?;
        self.load_keys(
            row,
            &projection.unit_id,
            &projection.content_key,
            &projection.input_key,
        )?;
        let approved = projection.ink_identical
            || projection.picture_identical
            || projection.junior_equivalent;
        self.flags[row] = (if projection.ink_identical {
            INK_IDENTICAL
        } else {
            0
        }) | (if projection.picture_identical {
            PICTURE_IDENTICAL
        } else {
            0
        }) | (if projection.junior_equivalent {
            JUNIOR_EQUIVALENT
        } else {
            0
        }) | (if approved || no_verdict { SLIM } else { 0 });
        self.diffs_digest[row] = self.strings.id(&projection.diffs_digest);
        self.cluster[row] = self.strings.id(&projection.cluster);
        self.change_group[row] = self.strings.id(&projection.change_group);
        self.ledger_class[row] = self.strings.id(&projection.ledger_class);
        if let Some((left, right)) = projection.pair_codepoints {
            self.pair_l[row] = signed_char(left)?;
            self.pair_r[row] = signed_char(right)?;
        }
        self.load_deltas(
            row,
            projection
                .ink_deltas
                .iter()
                .map(|(config, delta)| (config.as_str(), delta.as_str())),
        )?;
        self.load_projection_rows(
            row,
            &rows.codepoint_values,
            rows.pair,
            (&rows.after_spans, &rows.after_cells, &rows.after_junctions),
            (
                &rows.before_spans,
                &rows.before_glyphs,
                &rows.before_junctions,
            ),
        )?;
        self.load_junctions(
            row,
            &rows.junction_pairs,
            true,
            &projection.junction_rects,
            None,
        )?;
        self.load_source(row, address)?;
        self.shard_len[row] = unsigned_int(length)?;
        if !projection.mismatches.is_empty() {
            self.mismatches.insert(row, projection.mismatches.clone());
        }
        Ok(row)
    }

    /// `UnitStore.load_cached`: load one cached unit's store record into its row and return the row. `found` is the prior fragment the plan located for the record, by default the record's own address; it must carry the record's id and content key. `codepoints` is the unit's window, which the workload table has and the record does not.
    pub fn load_cached(
        &mut self,
        ordinal: i64,
        cached: &ParsedCachedUnit,
        codepoints: &[i64],
        found: Option<&PriorFragment>,
    ) -> Raised<usize> {
        let Some(found) = found.cloned().or_else(|| cached.located()) else {
            return value_error(format!(
                "ordinal {ordinal}: a cached unit is loaded with the fragment the plan located"
            ));
        };
        if found.unit_id != cached.prior_id
            || found.content_key.as_deref() != Some(cached.content_key.as_str())
        {
            return value_error(format!(
                "ordinal {ordinal}: the located fragment {} is not the record's {}",
                found.unit_id, cached.prior_id
            ));
        }
        let row = self.begin(ordinal)?;
        self.load_keys(row, &cached.prior_id, &cached.content_key, &cached.key)?;
        self.flags[row] = CACHED
            | (if cached.ink_identical {
                INK_IDENTICAL
            } else {
                0
            })
            | (if cached.picture_identical {
                PICTURE_IDENTICAL
            } else {
                0
            })
            | (if cached.junior_equivalent {
                JUNIOR_EQUIVALENT
            } else {
                0
            })
            | (if cached.slim { SLIM } else { 0 })
            | (if cached.exemplar { EXEMPLAR } else { 0 })
            | (if cached.no_verdict { NO_VERDICT } else { 0 });
        self.diffs_digest[row] = self.strings.id(&cached.diffs_digest);
        self.cluster[row] = self.strings.id(&cached.cluster);
        self.change_group[row] = self.strings.id(&cached.change_group);
        self.ledger_class[row] = self.strings.id(&cached.ledger_class);
        if let Some((left, right)) = cached.pair_codepoints {
            self.pair_l[row] = signed_char(left)?;
            self.pair_r[row] = signed_char(right)?;
        }
        self.prior_class[row] = self.strings.id(&cached.prior_class);
        self.duplicate_group[row] = self.strings.optional(cached.duplicate_group.as_deref());
        self.policy_file[row] = self.strings.optional(cached.policy_file.as_deref());
        self.load_deltas(
            row,
            cached
                .ink_deltas
                .iter()
                .map(|(config, delta)| (config.as_str(), delta.as_str())),
        )?;
        self.load_projection_rows(
            row,
            codepoints,
            cached.pair,
            (
                &cached.after_spans,
                &cached.after_cells,
                &cached.after_junctions,
            ),
            (
                &cached.before_spans,
                &cached.before_glyphs,
                &cached.before_junctions,
            ),
        )?;
        let mut rects = Vec::new();
        for junction in items_of(&cached.junction_rects)? {
            rects.push((
                subscript(&junction, "pair")?.clone(),
                subscript(&junction, "before")?.clone(),
                subscript(&junction, "after")?.clone(),
            ));
        }
        self.load_junctions(
            row,
            &cached.junction_pairs,
            false,
            &rects,
            Some(&cached.primary_units),
        )?;
        self.load_source(row, Some(&LoadAddress::Fragment(found.clone())))?;
        self.shard_len[row] = unsigned_int(if found.inflated != 0 {
            found.inflated
        } else {
            found.length
        })?;
        if !cached.mismatches.is_empty() {
            self.mismatches.insert(row, cached.mismatches.clone());
        }
        Ok(row)
    }

    /// `UnitStore.index`: the units' id words sorted, beside each word's ordinal, built on the first call and kept until the next load. An unloaded row is refused, and so is a repeated id, with the `SystemExit` Python raises naming both windows.
    pub fn index(&mut self) -> Raised<(&[u64], &[u32])> {
        if self.id_index.is_none() {
            if let Some(unloaded) = self.loaded.iter().position(|&loaded| loaded == 0) {
                return value_error(format!("ordinal {unloaded} was never loaded"));
            }
            let words: Vec<u64> = (0..self.n).map(|row| self.id_word(row)).collect();
            let order = sorted_by_word(&words);
            let sorted: Vec<u64> = order.iter().map(|&at| words[at as usize]).collect();
            for at in 1..self.n {
                if sorted[at] == sorted[at - 1] {
                    let (first, second) = (order[at - 1] as usize, order[at] as usize);
                    return raise(
                        "SystemExit",
                        format!(
                            "two units share the id {}: {} and {}; their carry projections hash to one 64-bit prefix",
                            self.unit_id(first),
                            format_codepoints(&self.codepoints(first)),
                            format_codepoints(&self.codepoints(second))
                        ),
                    );
                }
            }
            self.id_index = Some((sorted, order));
        }
        let (words, order) = self.id_index.as_ref().expect("built above");
        Ok((words, order))
    }

    /// `UnitStore.ordinal_of`: the ordinal of the unit with this id, by a bisect of the index; a `KeyError` for an id no unit has.
    pub fn ordinal_of(&mut self, unit_id: &str) -> Raised<usize> {
        self.index()?;
        let word = id_word_of(unit_id)?;
        let (words, order) = self.id_index.as_ref().expect("built above");
        let at = bisect_left(words, word);
        if at < words.len() && words[at] == word {
            return Ok(order[at] as usize);
        }
        raise("KeyError", repr_str(unit_id))
    }

    /// The unit's id as a word: its content key's first eight bytes, big-endian.
    pub fn id_word(&self, ordinal: usize) -> u64 {
        let start = ordinal * KEY_BYTES;
        u64::from_be_bytes(
            self.content_keys[start..start + ID_BYTES]
                .try_into()
                .expect("eight bytes"),
        )
    }

    pub fn unit_id(&self, ordinal: usize) -> String {
        id_of_word(self.id_word(ordinal))
    }

    pub fn content_key_hex(&self, ordinal: usize) -> String {
        hex_of(&self.content_keys[Self::key_range(ordinal)])
    }

    pub fn input_key_hex(&self, ordinal: usize) -> String {
        hex_of(&self.input_keys[Self::key_range(ordinal)])
    }

    /// The input-key column, which an [`UnitStore::input_key_index`] is read over.
    pub fn input_keys(&self) -> &[u8] {
        &self.input_keys
    }

    /// `UnitStore.input_key_index`: an index from each unit's input key to its ordinal, refusing a key two rows hold.
    pub fn input_key_index(&self) -> std::result::Result<KeyIndex, KeyIndexError> {
        KeyIndex::new(&self.input_keys, KEY_BYTES, self.n)
    }

    pub fn loaded(&self, ordinal: usize) -> bool {
        self.loaded[ordinal] != 0
    }

    pub fn flags(&self, ordinal: usize) -> Flags {
        Flags::of(self.flags[ordinal])
    }

    /// Whether the unit is ink- or picture-identical, which suppresses a junction naming it.
    pub fn invisible(&self, ordinal: usize) -> bool {
        self.flags[ordinal] & (INK_IDENTICAL | PICTURE_IDENTICAL) != 0
    }

    /// The three machine checks in [`MACHINE_CHECKS`] order.
    pub fn machine_flags(&self, ordinal: usize) -> (bool, bool, bool) {
        let flags = self.flags(ordinal);
        (
            flags.ink_identical,
            flags.picture_identical,
            flags.junior_equivalent,
        )
    }

    pub fn machine_approved(&self, ordinal: usize) -> bool {
        self.flags[ordinal] & (INK_IDENTICAL | PICTURE_IDENTICAL | JUNIOR_EQUIVALENT) != 0
    }

    /// The first of [`MACHINE_CHECKS`] that approves the unit.
    pub fn machine_check(&self, ordinal: usize) -> Option<&'static str> {
        let bits = self.flags[ordinal];
        MACHINE_CHECKS
            .iter()
            .zip([INK_IDENTICAL, PICTURE_IDENTICAL, JUNIOR_EQUIVALENT])
            .find(|&(_, bit)| bits & bit != 0)
            .map(|(&name, _)| name)
    }

    pub fn diffs_digest(&self, ordinal: usize) -> &str {
        self.strings.get(self.diffs_digest[ordinal])
    }

    pub fn cluster(&self, ordinal: usize) -> &str {
        self.strings.get(self.cluster[ordinal])
    }

    pub fn change_group(&self, ordinal: usize) -> &str {
        self.strings.get(self.change_group[ordinal])
    }

    pub fn ledger_class(&self, ordinal: usize) -> &str {
        self.strings.get(self.ledger_class[ordinal])
    }

    pub fn pair_codepoints(&self, ordinal: usize) -> Option<(i64, i64)> {
        let left = self.pair_l[ordinal];
        (left >= 0).then(|| (i64::from(left), i64::from(self.pair_r[ordinal])))
    }

    /// The judged pair as after-cell indices.
    pub fn cell_pair(&self, ordinal: usize) -> Option<(i64, i64)> {
        let left = self.cell_pair_l[ordinal];
        (left >= 0).then(|| (i64::from(left), i64::from(self.cell_pair_r[ordinal])))
    }

    fn optional(&self, id: u32) -> Option<&str> {
        (id != 0).then(|| self.strings.get(id))
    }

    /// The class the cached fragment was written under; None for a recomputed unit.
    pub fn cached_class(&self, ordinal: usize) -> Option<&str> {
        self.optional(self.prior_class[ordinal])
    }

    pub fn cached_duplicate_group(&self, ordinal: usize) -> Option<&str> {
        self.optional(self.duplicate_group[ordinal])
    }

    pub fn policy_file(&self, ordinal: usize) -> Option<&str> {
        self.optional(self.policy_file[ordinal])
    }

    pub fn set_policy_file(&mut self, ordinal: usize, value: Option<&str>) {
        self.policy_file[ordinal] = self.strings.optional(value);
    }

    pub fn config_note(&self, ordinal: usize) -> Option<&str> {
        self.optional(self.config_note[ordinal])
    }

    pub fn set_config_note(&mut self, ordinal: usize, value: Option<&str>) {
        self.config_note[ordinal] = self.strings.optional(value);
    }

    /// The unit's ink deltas in the loaded order, which is the fragment's JSON order, as a dict holds them: a repeated config keeps its first place and its last delta.
    pub fn ink_deltas(&self, ordinal: usize) -> Vec<(&str, &str)> {
        let start = self.deltas_start[ordinal] as usize;
        let stop = start + usize::from(self.deltas_n[ordinal]);
        let mut deltas: Vec<(&str, &str)> = Vec::new();
        for at in start..stop {
            let config = self.strings.get(self.delta_config[at]);
            let delta = self.strings.get(self.delta_digest[at]);
            match deltas.iter_mut().find(|(held, _)| *held == config) {
                Some(entry) => entry.1 = delta,
                None => deltas.push((config, delta)),
            }
        }
        deltas
    }

    pub fn codepoints(&self, ordinal: usize) -> Vec<u32> {
        let start = self.codepoints_start[ordinal] as usize;
        self.codepoint_values[start..start + usize::from(self.codepoints_n[ordinal])]
            .iter()
            .map(|&value| u32::from(value))
            .collect()
    }

    pub fn mismatches(&self, ordinal: usize) -> &[String] {
        self.mismatches.get(&ordinal).map_or(&[], Vec::as_slice)
    }

    /// Where the unit's fragment is read from, stamped with the row's id and content key; None for a row loaded without an address.
    pub fn source(&self, ordinal: usize) -> Option<PriorFragment> {
        let part = self.src_part[ordinal];
        (part != 0).then(|| PriorFragment {
            part: self.strings.get(part).to_owned(),
            start: self.src_start[ordinal] as i64,
            length: i64::from(self.src_len[ordinal]),
            unit_id: self.unit_id(ordinal),
            content_key: Some(self.content_key_hex(ordinal)),
            byte_copied: self.flags[ordinal] & BYTE_COPIED != 0,
            inflated: i64::from(self.src_inflated[ordinal]),
        })
    }

    /// `UnitStore.rebase_source`: move the unit's source to byte `start` of the same part.
    pub fn rebase_source(&mut self, ordinal: usize, start: i64) -> Raised<()> {
        self.src_start[ordinal] = unsigned_long_long(start)?;
        Ok(())
    }

    /// This build's shard address for the unit, as the write recorded it.
    pub fn written_address(&self, ordinal: usize) -> Option<(&str, u64, u32)> {
        let part = self.out_part[ordinal];
        (part != 0).then(|| {
            (
                self.strings.get(part),
                self.out_start[ordinal],
                self.out_len[ordinal],
            )
        })
    }

    pub fn written_inflated(&self, ordinal: usize) -> u32 {
        self.out_inflated[ordinal]
    }

    pub fn set_written_address(
        &mut self,
        ordinal: usize,
        (part, start, length): (&str, i64, i64),
        inflated: i64,
    ) -> Raised<()> {
        self.set_written_part(ordinal, part);
        self.set_written_span(ordinal, start, length, inflated)
    }

    /// `UnitStore.set_written_span`: the unit's byte start and length on disk in its shard part, with a deflated fragment's inflated length.
    pub fn set_written_span(
        &mut self,
        ordinal: usize,
        start: i64,
        length: i64,
        inflated: i64,
    ) -> Raised<()> {
        self.out_start[ordinal] = unsigned_long_long(start)?;
        self.out_len[ordinal] = unsigned_int(length)?;
        self.out_inflated[ordinal] = unsigned_int(inflated)?;
        Ok(())
    }

    pub fn set_written_part(&mut self, ordinal: usize, part: &str) {
        self.out_part[ordinal] = self.strings.id(part);
    }

    fn spans(column: &[u16], start: usize, count: usize) -> Vec<(i64, i64)> {
        column[2 * start..2 * (start + count)]
            .chunks(2)
            .map(|pair| (i64::from(pair[0]), i64::from(pair[1])))
            .collect()
    }

    fn names(&self, column: &[u32], start: usize, count: usize) -> Vec<String> {
        column[start..start + count]
            .iter()
            .map(|&id| self.strings.get(id).to_owned())
            .collect()
    }

    pub fn junction_count(&self, ordinal: usize) -> usize {
        usize::from(self.junction_n[ordinal])
    }

    pub fn junction_pairs(&self, ordinal: usize) -> Vec<(i64, i64)> {
        let start = self.junction_start[ordinal] as usize;
        self.junction_pairs[2 * start..2 * (start + self.junction_count(ordinal))]
            .chunks(2)
            .map(|pair| (i64::from(pair[0]), i64::from(pair[1])))
            .collect()
    }

    /// The unit's `PrimaryUnitProjection`, equal to the one loaded.
    pub fn primary_unit_projection(&self, ordinal: usize) -> PrimaryUnitProjection {
        self.projection_as(ordinal, self.unit_id(ordinal))
    }

    /// The unit as the primary-unit resolution pass compares it: [`UnitStore::primary_unit_projection`] with an empty id.
    pub fn projection(&self, ordinal: usize) -> PrimaryUnitProjection {
        self.projection_as(ordinal, String::new())
    }

    fn projection_as(&self, ordinal: usize, unit_id: String) -> PrimaryUnitProjection {
        let (after_start, after_n) = (
            self.after_start[ordinal] as usize,
            usize::from(self.after_n[ordinal]),
        );
        let (before_start, before_n) = (
            self.before_start[ordinal] as usize,
            usize::from(self.before_n[ordinal]),
        );
        PrimaryUnitProjection {
            unit_id,
            codepoint_values: self.codepoints(ordinal),
            ink_identical: self.flags[ordinal] & INK_IDENTICAL != 0,
            picture_identical: self.flags[ordinal] & PICTURE_IDENTICAL != 0,
            pair: self.cell_pair(ordinal),
            after_spans: Self::spans(&self.after_spans, after_start, after_n),
            after_cells: self.names(&self.after_cells, after_start, after_n),
            after_junctions: self.names(
                &self.after_junctions,
                self.after_junction_start[ordinal] as usize,
                after_n.saturating_sub(1),
            ),
            before_spans: Self::spans(&self.before_spans, before_start, before_n),
            before_glyphs: self.names(&self.before_glyphs, before_start, before_n),
            before_junctions: self.names(
                &self.before_junctions,
                self.before_junction_start[ordinal] as usize,
                before_n.saturating_sub(1),
            ),
            junction_pairs: self.junction_pairs(ordinal),
        }
    }

    /// The `proj` dict of a store record, key for key and list for list.
    pub fn primary_unit_projection_record(&self, ordinal: usize) -> PyValue {
        let projection = self.projection_as(ordinal, String::new());
        let spans = |spans: &[(i64, i64)]| {
            PyValue::List(spans.iter().map(|&span| pair_list(span)).collect())
        };
        PyValue::Dict(vec![
            (
                PyValue::str("pair"),
                projection.pair.map_or(PyValue::None, pair_list),
            ),
            (PyValue::str("after_spans"), spans(&projection.after_spans)),
            (
                PyValue::str("after_cells"),
                texts_list(&projection.after_cells),
            ),
            (
                PyValue::str("after_junctions"),
                texts_list(&projection.after_junctions),
            ),
            (
                PyValue::str("before_spans"),
                spans(&projection.before_spans),
            ),
            (
                PyValue::str("before_glyphs"),
                texts_list(&projection.before_glyphs),
            ),
            (
                PyValue::str("before_junctions"),
                texts_list(&projection.before_junctions),
            ),
        ])
    }

    /// The unit's secondary-junction rects as `patch_fragment` reads them and the store record keeps them: `[{"pair", "before", "after"}]`, each edge dict in `enrich._highlight`'s key order.
    pub fn junction_rects(&self, ordinal: usize) -> PyValue {
        let start = self.junction_start[ordinal] as usize;
        let count = self.junction_count(ordinal);
        let pairs = &self.junction_pairs[2 * start..2 * (start + count)];
        let edges = &self.rect_edges[6 * start..6 * (start + count)];
        let edge = |values: &[i32]| {
            PyValue::Dict(
                RECT_EDGES
                    .iter()
                    .zip(values)
                    .map(|(&name, &value)| (PyValue::str(name), PyValue::Int(i64::from(value))))
                    .collect(),
            )
        };
        PyValue::List(
            (0..count)
                .map(|at| {
                    PyValue::Dict(vec![
                        (
                            PyValue::str("pair"),
                            pair_list((i64::from(pairs[2 * at]), i64::from(pairs[2 * at + 1]))),
                        ),
                        (PyValue::str("before"), edge(&edges[6 * at..6 * at + 3])),
                        (PyValue::str("after"), edge(&edges[6 * at + 3..6 * at + 6])),
                    ])
                })
                .collect(),
        )
    }

    /// `UnitStore.set_primary_units`: the primary-unit resolution pass's result for the unit, one `(primary unit, suppressed)` a junction, in junction order. Each entry is written as it is resolved, so a refusal partway leaves the entries before it written, as Python does.
    pub fn set_primary_units(&mut self, ordinal: usize, assign: &[(Target, bool)]) -> Raised<()> {
        let start = self.junction_start[ordinal] as usize;
        let count = self.junction_count(ordinal);
        if assign.len() != count {
            return value_error(format!(
                "ordinal {ordinal}: {count} junctions beside {} primary-unit assignments",
                assign.len()
            ));
        }
        for (at, (unit, suppressed)) in assign.iter().enumerate() {
            let target = match unit {
                Target::Absent => NO_PRIMARY_UNIT,
                Target::Id(unit_id) => offset(self.ordinal_of(unit_id)?),
                Target::Ordinal(target) => match usize::try_from(*target) {
                    Ok(target) if target < self.n => offset(target),
                    _ => {
                        return raise(
                            "IndexError",
                            format!(
                                "ordinal {ordinal}: primary-unit ordinal {target} is outside a store of {} units",
                                self.n
                            ),
                        );
                    }
                },
            };
            self.primary_unit[start + at] = target;
            self.primary_unit_suppressed[start + at] = u8::from(*suppressed);
        }
        Ok(())
    }

    /// The id of every unit an unsuppressed secondary junction names as its primary unit.
    pub fn primary_unit_ids(&self) -> BTreeSet<String> {
        self.primary_unit
            .iter()
            .zip(&self.primary_unit_suppressed)
            .filter(|&(&target, &suppressed)| target != NO_PRIMARY_UNIT && suppressed == 0)
            .map(|(&target, _)| self.unit_id(target as usize))
            .collect()
    }

    pub fn primary_unit_ordinals(&self, ordinal: usize) -> Vec<(Option<u32>, bool)> {
        let start = self.junction_start[ordinal] as usize;
        let count = self.junction_count(ordinal);
        self.primary_unit[start..start + count]
            .iter()
            .zip(&self.primary_unit_suppressed[start..start + count])
            .map(|(&target, &suppressed)| {
                (
                    (target != NO_PRIMARY_UNIT).then_some(target),
                    suppressed != 0,
                )
            })
            .collect()
    }

    /// This build's primary unit assignments for the unit, with primary units as ids.
    pub fn primary_units(&self, ordinal: usize) -> Vec<(Option<String>, bool)> {
        self.primary_unit_ordinals(ordinal)
            .into_iter()
            .map(|(target, suppressed)| {
                (
                    target.map(|target| self.unit_id(target as usize)),
                    suppressed,
                )
            })
            .collect()
    }

    /// This build's primary units as a store record's `primary_units`: a `[[primary_unit, suppressed]]` list.
    pub fn primary_units_record(&self, ordinal: usize) -> PyValue {
        units_record(&self.primary_units(ordinal))
    }

    /// The primary units the cached fragment was written with, as its store record holds them.
    pub fn cached_primary_units(&self, ordinal: usize) -> Vec<(Option<String>, bool)> {
        let start = self.junction_start[ordinal] as usize;
        let count = self.junction_count(ordinal);
        self.cached_primary_unit[start..start + count]
            .iter()
            .zip(&self.cached_primary_unit_suppressed[start..start + count])
            .map(|(&unit, &suppressed)| (self.optional(unit).map(str::to_owned), suppressed != 0))
            .collect()
    }

    /// `UnitStore.cached_as_is`: whether the cached fragment's bytes on disk already equal what this build writes for the unit.
    pub fn cached_as_is(
        &self,
        ordinal: usize,
        class_id: &str,
        duplicate_group: Option<&str>,
        exemplar: bool,
        no_verdict: bool,
    ) -> bool {
        let flags = self.flags(ordinal);
        flags.cached
            && flags.byte_copied
            && self.cached_class(ordinal) == Some(class_id)
            && self.cached_duplicate_group(ordinal) == duplicate_group
            && flags.exemplar == exemplar
            && flags.no_verdict == no_verdict
            && self.cached_primary_units(ordinal) == self.primary_units(ordinal)
    }

    /// The unit's fragment's plain shard bytes as the parent knows them before the write.
    pub fn shard_length(&self, ordinal: usize) -> u32 {
        self.shard_len[ordinal]
    }

    pub fn set_prior_line(&mut self, ordinal: usize, line: i64) -> Raised<()> {
        self.prior_line[ordinal] = unsigned_int(line)?;
        Ok(())
    }

    /// The unit's line in the prior unit store's materialized order, or [`NO_PRIOR_LINE`].
    pub fn prior_line(&self, ordinal: usize) -> u32 {
        self.prior_line[ordinal]
    }

    /// `UnitStore.add_prior_record`: keep one prior store record the plan did not load, by its unit id. A record without an address is not kept. Each column is appended in Python's order, so a refusal partway leaves the columns before it appended, as Python does.
    pub fn add_prior_record(&mut self, record: &PriorRecord) -> Raised<()> {
        let Some((part, start, length)) = &record.address else {
            return Ok(());
        };
        let index = match self.by_id_part_ids.get(part) {
            Some(&index) => index,
            None => {
                let index = self.by_id_parts.len();
                self.by_id_part_ids.insert(part.clone(), index);
                self.by_id_parts.push(part.clone());
                self.strings.id(part);
                index
            }
        };
        self.by_id_words.push(id_word_of(&record.unit_id)?);
        self.by_id_part
            .push(unsigned_short(i64::try_from(index).unwrap_or(i64::MAX))?);
        self.by_id_start.push(unsigned_int(*start)?);
        self.by_id_len.push(unsigned_int(*length)?);
        self.by_id_inflated.push(unsigned_int(record.inflated)?);
        let class = self.strings.id(&record.class_id);
        self.by_id_class.push(class);
        let group = self.strings.optional(record.duplicate_group.as_deref());
        self.by_id_group.push(group);
        self.by_id_bits.push(
            (if record.exemplar { PRIOR_EXEMPLAR } else { 0 })
                | (if record.no_verdict {
                    PRIOR_NO_VERDICT
                } else {
                    0
                }),
        );
        self.by_id_units.push(record.primary_units);
        Ok(())
    }

    /// `UnitStore.seal_prior_records`: sort the kept prior records by unit id, stably, for the bisects that read them.
    pub fn seal_prior_records(&mut self) {
        let words = &self.by_id_words;
        if words.windows(2).all(|pair| pair[0] <= pair[1]) {
            return;
        }
        let mut order: Vec<usize> = (0..words.len()).collect();
        order.sort_by_key(|&at| words[at]);
        fn permuted<T: Copy>(column: &[T], order: &[usize]) -> Vec<T> {
            order.iter().map(|&at| column[at]).collect()
        }
        self.by_id_words = permuted(&self.by_id_words, &order);
        self.by_id_part = permuted(&self.by_id_part, &order);
        self.by_id_start = permuted(&self.by_id_start, &order);
        self.by_id_len = permuted(&self.by_id_len, &order);
        self.by_id_inflated = permuted(&self.by_id_inflated, &order);
        self.by_id_class = permuted(&self.by_id_class, &order);
        self.by_id_group = permuted(&self.by_id_group, &order);
        self.by_id_bits = permuted(&self.by_id_bits, &order);
        self.by_id_units = permuted(&self.by_id_units, &order);
    }

    pub fn prior_record_count(&self) -> usize {
        self.by_id_words.len()
    }

    /// Each kept prior record in the store's order: its id word, part, start, length, inflated length, class, duplicate group, flag bits and primary units' CRC-32.
    pub fn prior_records(
        &self,
    ) -> impl Iterator<Item = (u64, &str, u32, u32, u32, &str, Option<&str>, u8, u32)> + '_ {
        (0..self.by_id_words.len()).map(|at| {
            (
                self.by_id_words[at],
                self.by_id_parts[usize::from(self.by_id_part[at])].as_str(),
                self.by_id_start[at],
                self.by_id_len[at],
                self.by_id_inflated[at],
                self.strings.get(self.by_id_class[at]),
                self.optional(self.by_id_group[at]),
                self.by_id_bits[at],
                self.by_id_units[at],
            )
        })
    }

    fn prior_index(&self, ordinal: usize) -> Option<usize> {
        let word = self.id_word(ordinal);
        let at = bisect_left(&self.by_id_words, word);
        (at < self.by_id_words.len() && self.by_id_words[at] == word).then_some(at)
    }

    /// The address of the prior record with the unit's id: its part, start, length on disk and inflated length.
    pub fn prior_address(&self, ordinal: usize) -> Option<(&str, u32, u32, u32)> {
        self.prior_index(ordinal).map(|at| {
            (
                self.by_id_parts[usize::from(self.by_id_part[at])].as_str(),
                self.by_id_start[at],
                self.by_id_len[at],
                self.by_id_inflated[at],
            )
        })
    }

    /// The prior fragment the write compares the unit's new fragment with: a cached unit's source, or the fragment at [`UnitStore::prior_address`] stamped with the unit's id and content key.
    pub fn prior_fragment(&self, ordinal: usize) -> Option<PriorFragment> {
        if self.flags[ordinal] & CACHED != 0 {
            return self.source(ordinal);
        }
        let (part, start, length, inflated) = self.prior_address(ordinal)?;
        Some(PriorFragment {
            part: part.to_owned(),
            start: i64::from(start),
            length: i64::from(length),
            unit_id: self.unit_id(ordinal),
            content_key: Some(self.content_key_hex(ordinal)),
            byte_copied: true,
            inflated: i64::from(inflated),
        })
    }

    /// `UnitStore.predicted_unchanged`: whether the unit's fragment is predicted to come out as its prior one, from what the parent already holds.
    pub fn predicted_unchanged(
        &self,
        ordinal: usize,
        class_id: &str,
        duplicate_group: Option<&str>,
        exemplar: bool,
        no_verdict: bool,
    ) -> bool {
        if self.flags[ordinal] & CACHED != 0 {
            return self.cached_as_is(ordinal, class_id, duplicate_group, exemplar, no_verdict);
        }
        let Some(at) = self.prior_index(ordinal) else {
            return false;
        };
        let bits = self.by_id_bits[at];
        let units = if self.junction_n[ordinal] != 0 {
            records::primary_units_crc(&self.primary_units_record(ordinal))
                .expect("a primary units record writes as JSON")
        } else {
            0
        };
        self.strings.get(self.by_id_class[at]) == class_id
            && self.optional(self.by_id_group[at]) == duplicate_group
            && (bits & PRIOR_EXEMPLAR != 0) == exemplar
            && (bits & PRIOR_NO_VERDICT != 0) == no_verdict
            && self.by_id_units[at] == units
    }

    /// Every unit's ordinal and window.
    pub fn windows(&self) -> impl Iterator<Item = (usize, Vec<u32>)> + '_ {
        (0..self.n).map(|ordinal| (ordinal, self.codepoints(ordinal)))
    }

    /// `UnitStore.cached_unit`: the unit's store record for this build, its class, duplicate group, exemplar and exemption this build's, passed by the caller.
    pub fn cached_unit(
        &self,
        ordinal: usize,
        class_id: &str,
        duplicate_group: Option<&str>,
        exemplar: bool,
        no_verdict: bool,
    ) -> CachedUnit {
        let flags = self.flags(ordinal);
        CachedUnit {
            key: self.input_key_hex(ordinal),
            prior_id: self.unit_id(ordinal),
            prior_class: class_id.to_owned(),
            content_key: self.content_key_hex(ordinal),
            slim: flags.ink_identical
                || flags.picture_identical
                || flags.junior_equivalent
                || no_verdict,
            address: self
                .written_address(ordinal)
                .map(|(part, start, length)| (part.to_owned(), start as i64, i64::from(length))),
            ink_identical: flags.ink_identical,
            picture_identical: flags.picture_identical,
            junior_equivalent: flags.junior_equivalent,
            ink_deltas: self
                .ink_deltas(ordinal)
                .into_iter()
                .map(|(config, delta)| (config.to_owned(), delta.to_owned()))
                .collect(),
            diffs_digest: self.diffs_digest(ordinal).to_owned(),
            cluster: self.cluster(ordinal).to_owned(),
            change_group: self.change_group(ordinal).to_owned(),
            ledger_class: self.ledger_class(ordinal).to_owned(),
            pair_codepoints: self.pair_codepoints(ordinal),
            proj: self.primary_unit_projection_record(ordinal),
            junctions: self.junction_rects(ordinal),
            mismatches: self.mismatches(ordinal).to_vec(),
            duplicate_group: duplicate_group.map(str::to_owned),
            exemplar,
            no_verdict,
            primary_units: self.primary_units(ordinal),
            policy_file: self.policy_file(ordinal).map(str::to_owned),
            inflated: i64::from(self.written_inflated(ordinal)),
        }
    }
}

/// Primary units as a store record's `[[primary_unit, suppressed]]` list.
fn units_record(units: &[(Option<String>, bool)]) -> PyValue {
    PyValue::List(
        units
            .iter()
            .map(|(unit, suppressed)| {
                PyValue::List(vec![
                    PyValue::optional_str(unit.as_deref()),
                    PyValue::Bool(*suppressed),
                ])
            })
            .collect(),
    )
}

impl super::table::UnitStoreView for UnitStore {
    fn input_key_hex(&self, ordinal: usize) -> String {
        UnitStore::input_key_hex(self, ordinal)
    }

    fn loaded(&self, ordinal: usize) -> bool {
        UnitStore::loaded(self, ordinal)
    }

    fn unit_id(&self, ordinal: usize) -> String {
        UnitStore::unit_id(self, ordinal)
    }

    fn machine_flags(&self, ordinal: usize) -> (bool, bool, bool) {
        UnitStore::machine_flags(self, ordinal)
    }
}

// --- the stage ------------------------------------------------------------------------------------------

fn tuple(items: Vec<PyValue>) -> PyValue {
    PyValue::Tuple(items)
}

fn int(value: impl Into<i64>) -> PyValue {
    PyValue::Int(value.into())
}

fn count(value: usize) -> PyValue {
    PyValue::Int(i64::try_from(value).expect("a count fits 64 bits"))
}

fn raised(failure: &StoreError) -> PyValue {
    tuple(vec![
        PyValue::str("raised"),
        PyValue::str(failure.class),
        PyValue::str(failure.message.as_str()),
    ])
}

fn optional_text(value: Option<&str>) -> PyValue {
    PyValue::optional_str(value)
}

fn units_tuple(units: &[(Option<String>, bool)]) -> PyValue {
    tuple(
        units
            .iter()
            .map(|(unit, suppressed)| {
                tuple(vec![
                    PyValue::optional_str(unit.as_deref()),
                    PyValue::Bool(*suppressed),
                ])
            })
            .collect(),
    )
}

fn fragment_value(fragment: Option<PriorFragment>) -> PyValue {
    fragment.map_or(PyValue::None, |fragment| fragment.value())
}

fn written_value(store: &UnitStore, ordinal: usize) -> PyValue {
    tuple(vec![
        PyValue::str("written"),
        store
            .written_address(ordinal)
            .map_or(PyValue::None, |(part, start, length)| {
                tuple(vec![
                    PyValue::str(part),
                    int(i64::try_from(start).expect("a start below 2**63")),
                    int(length),
                ])
            }),
        int(store.written_inflated(ordinal)),
    ])
}

/// The store's kept prior records in its order, as `units_stage_fixtures._sealed_records` holds them: each one's id word as 16 hex digits, its part, start, length, inflated length, class, duplicate group, flag bits and primary units' CRC-32.
pub(crate) fn sealed_records(store: &UnitStore) -> impl Iterator<Item = PyValue> + '_ {
    store.prior_records().map(
        |(word, part, start, length, inflated, class, group, bits, units)| {
            tuple(vec![
                PyValue::str(format!("{word:016x}")),
                PyValue::str(part),
                int(start),
                int(length),
                int(inflated),
                PyValue::str(class),
                optional_text(group),
                int(bits),
                int(units),
            ])
        },
    )
}

/// What the stage reads of one row after a call: every accessor that takes only the ordinal, by name, in the order `units_stage_fixtures._store_reading` reads them.
pub(crate) fn reading(store: &UnitStore, ordinal: usize) -> PyValue {
    let (ink, picture, junior) = store.machine_flags(ordinal);
    let fields: Vec<(&str, PyValue)> = vec![
        ("loaded", PyValue::Bool(store.loaded(ordinal))),
        ("flags", store.flags(ordinal).value()),
        ("invisible", PyValue::Bool(store.invisible(ordinal))),
        (
            "machine_flags",
            tuple(vec![
                PyValue::Bool(ink),
                PyValue::Bool(picture),
                PyValue::Bool(junior),
            ]),
        ),
        (
            "machine_approved",
            PyValue::Bool(store.machine_approved(ordinal)),
        ),
        ("machine_check", optional_text(store.machine_check(ordinal))),
        (
            "id_word",
            PyValue::str(format!("{:016x}", store.id_word(ordinal))),
        ),
        ("unit_id", PyValue::str(store.unit_id(ordinal))),
        ("content_key", PyValue::str(store.content_key_hex(ordinal))),
        ("input_key", PyValue::str(store.input_key_hex(ordinal))),
        ("diffs_digest", PyValue::str(store.diffs_digest(ordinal))),
        ("cluster", PyValue::str(store.cluster(ordinal))),
        ("change_group", PyValue::str(store.change_group(ordinal))),
        ("ledger_class", PyValue::str(store.ledger_class(ordinal))),
        (
            "pair_codepoints",
            optional_pair(store.pair_codepoints(ordinal)),
        ),
        ("cell_pair", optional_pair(store.cell_pair(ordinal))),
        ("cached_class", optional_text(store.cached_class(ordinal))),
        (
            "cached_duplicate_group",
            optional_text(store.cached_duplicate_group(ordinal)),
        ),
        ("policy_file", optional_text(store.policy_file(ordinal))),
        ("config_note", optional_text(store.config_note(ordinal))),
        (
            "ink_deltas",
            PyValue::Dict(
                store
                    .ink_deltas(ordinal)
                    .into_iter()
                    .map(|(config, delta)| (PyValue::str(config), PyValue::str(delta)))
                    .collect(),
            ),
        ),
        (
            "codepoints",
            tuple(store.codepoints(ordinal).into_iter().map(int).collect()),
        ),
        ("mismatches", texts_list(store.mismatches(ordinal))),
        ("source", fragment_value(store.source(ordinal))),
        ("written", written_value(store, ordinal)),
        ("junction_count", count(store.junction_count(ordinal))),
        (
            "junction_pairs",
            pairs_tuple(&store.junction_pairs(ordinal)),
        ),
        (
            "primary_unit_projection",
            store.primary_unit_projection(ordinal).value(),
        ),
        ("projection", store.projection(ordinal).value()),
        (
            "primary_unit_projection_record",
            store.primary_unit_projection_record(ordinal),
        ),
        ("junction_rects", store.junction_rects(ordinal)),
        (
            "primary_unit_ordinals",
            tuple(
                store
                    .primary_unit_ordinals(ordinal)
                    .into_iter()
                    .map(|(target, suppressed)| {
                        tuple(vec![
                            target.map_or(PyValue::None, int),
                            PyValue::Bool(suppressed),
                        ])
                    })
                    .collect(),
            ),
        ),
        ("primary_units", units_tuple(&store.primary_units(ordinal))),
        ("primary_units_record", store.primary_units_record(ordinal)),
        (
            "cached_primary_units",
            units_record(&store.cached_primary_units(ordinal)),
        ),
        ("shard_length", int(store.shard_length(ordinal))),
        ("prior_line", int(store.prior_line(ordinal))),
        (
            "prior_address",
            store.prior_address(ordinal).map_or(
                PyValue::None,
                |(part, start, length, inflated)| {
                    tuple(vec![
                        PyValue::str(part),
                        int(start),
                        int(length),
                        int(inflated),
                    ])
                },
            ),
        ),
        (
            "prior_fragment",
            fragment_value(store.prior_fragment(ordinal)),
        ),
    ];
    PyValue::Dict(
        fields
            .into_iter()
            .map(|(name, value)| (PyValue::str(name), value))
            .collect(),
    )
}

// --- reading a line's values ------------------------------------------------------------------------------

fn record_fields<'a>(value: &'a PyValue, name: &str, key: &str) -> Result<&'a [(String, PyValue)]> {
    match value {
        PyValue::Record(held, fields) if held == name => Ok(fields),
        other => error(format!("{key}: {} is not a {name}", other.kind())),
    }
}

fn field<'a>(fields: &'a [(String, PyValue)], name: &str, key: &str) -> Result<&'a PyValue> {
    fields
        .iter()
        .find(|(held, _)| held == name)
        .map(|(_, value)| value)
        .ok_or_else(|| WireError(format!("{key}: no {name}")))
}

fn sequence<'a>(value: &'a PyValue, what: &str) -> Result<&'a [PyValue]> {
    match value {
        PyValue::Tuple(items) | PyValue::List(items) => Ok(items),
        other => error(format!("{what} is {}, not a sequence", other.kind())),
    }
}

fn text(value: &PyValue, what: &str) -> Result<String> {
    match value {
        PyValue::Str(text) => Ok(text.clone()),
        other => error(format!("{what} is {}, not a str", other.kind())),
    }
}

fn optional_text_of(value: &PyValue, what: &str) -> Result<Option<String>> {
    match value {
        PyValue::None => Ok(None),
        other => text(other, what).map(Some),
    }
}

fn flag(value: &PyValue, what: &str) -> Result<bool> {
    match value {
        PyValue::Bool(flag) => Ok(*flag),
        other => error(format!("{what} is {}, not a bool", other.kind())),
    }
}

fn integer(value: &PyValue, what: &str) -> Result<i64> {
    match value {
        PyValue::Int(number) => Ok(*number),
        PyValue::Bool(flag) => Ok(i64::from(*flag)),
        other => error(format!("{what} is {}, not an int", other.kind())),
    }
}

fn texts(value: &PyValue, what: &str) -> Result<Vec<String>> {
    sequence(value, what)?
        .iter()
        .map(|item| text(item, what))
        .collect()
}

fn pair_of(value: &PyValue, what: &str) -> Result<(i64, i64)> {
    match sequence(value, what)? {
        [left, right] => Ok((integer(left, what)?, integer(right, what)?)),
        _ => error(format!("{what} is not a pair")),
    }
}

fn optional_pair_of(value: &PyValue, what: &str) -> Result<Option<(i64, i64)>> {
    match value {
        PyValue::None => Ok(None),
        other => pair_of(other, what).map(Some),
    }
}

fn pairs_of(value: &PyValue, what: &str) -> Result<Vec<(i64, i64)>> {
    sequence(value, what)?
        .iter()
        .map(|item| pair_of(item, what))
        .collect()
}

fn ints_of(value: &PyValue, what: &str) -> Result<Vec<i64>> {
    sequence(value, what)?
        .iter()
        .map(|item| integer(item, what))
        .collect()
}

fn deltas_of(value: &PyValue, what: &str) -> Result<Vec<(String, String)>> {
    match value {
        PyValue::Dict(entries) => entries
            .iter()
            .map(|(config, delta)| Ok((text(config, what)?, text(delta, what)?)))
            .collect(),
        other => sequence(other, what)?
            .iter()
            .map(|entry| match sequence(entry, what)? {
                [config, delta] => Ok((text(config, what)?, text(delta, what)?)),
                _ => error(format!("{what} holds an entry that is not a pair")),
            })
            .collect(),
    }
}

fn units_of(value: &PyValue, what: &str) -> Result<Vec<(Option<String>, bool)>> {
    sequence(value, what)?
        .iter()
        .map(|entry| match sequence(entry, what)? {
            [unit, suppressed] => Ok((optional_text_of(unit, what)?, flag(suppressed, what)?)),
            _ => error(format!("{what} holds an entry that is not a pair")),
        })
        .collect()
}

/// A `Recomputed` record, as the stage writes a projection (`_projection_value`).
fn recomputed_of(value: &PyValue, key: &str) -> Result<Recomputed> {
    let fields = record_fields(value, "Recomputed", key)?;
    let get = |name: &str| field(fields, name, key);
    let rows = record_fields(get("primary_unit_projection")?, "PrimaryUnitRows", key)?;
    let row = |name: &str| field(rows, name, key);
    let junction_rects = sequence(get("junction_rects")?, "the junction rects")?
        .iter()
        .map(|rect| match sequence(rect, "a junction rect")? {
            [pair, before, after] => Ok((pair.clone(), before.clone(), after.clone())),
            _ => error(format!("{key}: a junction rect is not a triple")),
        })
        .collect::<Result<_>>()?;
    Ok(Recomputed {
        ordinal: integer(get("ordinal")?, "the ordinal")?,
        unit_id: text(get("unit_id")?, "the id")?,
        input_key: text(get("input_key")?, "the input key")?,
        content_key: text(get("content_key")?, "the content key")?,
        ink_identical: flag(get("ink_identical")?, "ink_identical")?,
        picture_identical: flag(get("picture_identical")?, "picture_identical")?,
        junior_equivalent: flag(get("junior_equivalent")?, "junior_equivalent")?,
        ink_deltas: deltas_of(get("ink_deltas")?, "the ink deltas")?,
        diffs_digest: text(get("diffs_digest")?, "the diffs digest")?,
        cluster: text(get("cluster")?, "the cluster")?,
        change_group: text(get("change_group")?, "the change group")?,
        ledger_class: text(get("ledger_class")?, "the ledger class")?,
        pair_codepoints: optional_pair_of(get("pair_codepoints")?, "the judged code points")?,
        rows: PrimaryRows {
            codepoint_values: ints_of(row("codepoint_values")?, "the code points")?,
            ink_identical: flag(row("ink_identical")?, "the rows' ink_identical")?,
            picture_identical: flag(row("picture_identical")?, "the rows' picture_identical")?,
            pair: optional_pair_of(row("pair")?, "the judged pair")?,
            after_spans: pairs_of(row("after_spans")?, "the after spans")?,
            after_cells: texts(row("after_cells")?, "the after cells")?,
            after_junctions: texts(row("after_junctions")?, "the after junctions")?,
            before_spans: pairs_of(row("before_spans")?, "the before spans")?,
            before_glyphs: texts(row("before_glyphs")?, "the before glyphs")?,
            before_junctions: texts(row("before_junctions")?, "the before junctions")?,
            junction_pairs: pairs_of(row("junction_pairs")?, "the junction pairs")?,
        },
        junction_rects,
        mismatches: texts(get("mismatches")?, "the mismatches")?,
    })
}

/// A `ParsedCachedUnit` record.
fn parsed_of(value: &PyValue, key: &str) -> Result<ParsedCachedUnit> {
    let fields = record_fields(value, "ParsedCachedUnit", key)?;
    let get = |name: &str| field(fields, name, key);
    let address = match get("address")? {
        PyValue::None => None,
        other => match sequence(other, "the address")? {
            [part, start, length] => Some((
                text(part, "the address's part")?,
                integer(start, "the address's start")?,
                integer(length, "the address's length")?,
            )),
            _ => return error(format!("{key}: the address is not a triple")),
        },
    };
    Ok(ParsedCachedUnit {
        key: text(get("key")?, "the key")?,
        prior_id: text(get("prior_id")?, "the id")?,
        prior_class: text(get("prior_class")?, "the class")?,
        content_key: text(get("content_key")?, "the content key")?,
        slim: flag(get("slim")?, "slim")?,
        address,
        ink_identical: flag(get("ink_identical")?, "ink_identical")?,
        picture_identical: flag(get("picture_identical")?, "picture_identical")?,
        junior_equivalent: flag(get("junior_equivalent")?, "junior_equivalent")?,
        ink_deltas: deltas_of(get("ink_deltas")?, "the ink deltas")?,
        diffs_digest: text(get("diffs_digest")?, "the diffs digest")?,
        cluster: text(get("cluster")?, "the cluster")?,
        change_group: text(get("change_group")?, "the change group")?,
        ledger_class: text(get("ledger_class")?, "the ledger class")?,
        pair_codepoints: optional_pair_of(get("pair_codepoints")?, "the judged code points")?,
        duplicate_group: optional_text_of(get("duplicate_group")?, "the duplicate group")?,
        exemplar: flag(get("exemplar")?, "exemplar")?,
        no_verdict: flag(get("no_verdict")?, "no_verdict")?,
        primary_units: units_of(get("primary_units")?, "the primary units")?,
        policy_file: optional_text_of(get("policy_file")?, "the policy file")?,
        junction_rects: get("junction_rects")?.clone(),
        mismatches: texts(get("mismatches")?, "the mismatches")?,
        pair: optional_pair_of(get("pair")?, "the judged pair")?,
        after_spans: pairs_of(get("after_spans")?, "the after spans")?,
        after_cells: texts(get("after_cells")?, "the after cells")?,
        after_junctions: texts(get("after_junctions")?, "the after junctions")?,
        before_spans: pairs_of(get("before_spans")?, "the before spans")?,
        before_glyphs: texts(get("before_glyphs")?, "the before glyphs")?,
        before_junctions: texts(get("before_junctions")?, "the before junctions")?,
        junction_pairs: pairs_of(get("junction_pairs")?, "the junction pairs")?,
        inflated: integer(get("inflated")?, "the inflated length")?,
    })
}

/// A `PriorFragment` record.
fn fragment_of(value: &PyValue, key: &str) -> Result<PriorFragment> {
    let fields = record_fields(value, "PriorFragment", key)?;
    let get = |name: &str| field(fields, name, key);
    Ok(PriorFragment {
        part: text(get("part")?, "the part")?,
        start: integer(get("start")?, "the start")?,
        length: integer(get("length")?, "the length")?,
        unit_id: text(get("unit_id")?, "the id")?,
        content_key: optional_text_of(get("content_key")?, "the content key")?,
        byte_copied: flag(get("byte_copied")?, "byte_copied")?,
        inflated: integer(get("inflated")?, "the inflated length")?,
    })
}

/// A `PriorRecord` record.
fn prior_record_of(value: &PyValue, key: &str) -> Result<PriorRecord> {
    let fields = record_fields(value, "PriorRecord", key)?;
    let get = |name: &str| field(fields, name, key);
    let address = match get("address")? {
        PyValue::None => None,
        other => match sequence(other, "the address")? {
            [part, start, length] => Some((
                text(part, "the address's part")?,
                integer(start, "the address's start")?,
                integer(length, "the address's length")?,
            )),
            _ => return error(format!("{key}: the address is not a triple")),
        },
    };
    Ok(PriorRecord {
        unit_id: text(get("unit_id")?, "the id")?,
        class_id: text(get("class_id")?, "the class")?,
        duplicate_group: optional_text_of(get("duplicate_group")?, "the duplicate group")?,
        exemplar: flag(get("exemplar")?, "exemplar")?,
        no_verdict: flag(get("no_verdict")?, "no_verdict")?,
        primary_units: u32::try_from(integer(get("primary_units")?, "the primary units")?)
            .map_err(|_| WireError(format!("{key}: the primary units' CRC is not 32 bits")))?,
        address,
        inflated: integer(get("inflated")?, "the inflated length")?,
    })
}

fn address_of(value: &PyValue, key: &str) -> Result<Option<LoadAddress>> {
    Ok(match value {
        PyValue::None => None,
        PyValue::Record(..) => Some(LoadAddress::Fragment(fragment_of(value, key)?)),
        other => match sequence(other, "the address")? {
            [part, start, length] => Some(LoadAddress::Span(
                text(part, "the address's part")?,
                integer(start, "the address's start")?,
                integer(length, "the address's length")?,
            )),
            _ => return error(format!("{key}: the address is not a triple")),
        },
    })
}

fn targets_of(value: &PyValue, key: &str) -> Result<Vec<(Target, bool)>> {
    sequence(value, "the primary units")?
        .iter()
        .map(|entry| match sequence(entry, "a primary unit")? {
            [unit, suppressed] => Ok((
                match unit {
                    PyValue::None => Target::Absent,
                    PyValue::Str(unit_id) => Target::Id(unit_id.clone()),
                    other => Target::Ordinal(integer(other, "a primary unit")?),
                },
                flag(suppressed, "a primary unit's suppression")?,
            )),
            _ => error(format!("{key}: a primary unit is not a pair")),
        })
        .collect()
}

fn row_of(store: &UnitStore, value: &PyValue, key: &str) -> Result<usize> {
    let ordinal = integer(value, "the ordinal")?;
    usize::try_from(ordinal)
        .ok()
        .filter(|&row| row < store.len())
        .ok_or_else(|| {
            WireError(format!(
                "{key}: ordinal {ordinal} is not a row of the store"
            ))
        })
}

fn outcome(result: Raised<PyValue>) -> PyValue {
    result.unwrap_or_else(|failure| raised(&failure))
}

/// One call of a `store-load` trace on `store`, answered as `units_stage_fixtures._store_after` values it, or the exception it raises.
fn answer(
    traces: &mut HashMap<String, UnitStore>,
    name: &str,
    call: &[PyValue],
    key: &str,
) -> Result<PyValue> {
    let Some((PyValue::Str(op), arguments)) = call.split_first() else {
        return error(format!("{key}: a call names no op"));
    };
    if op == "new" {
        let [size, keys] = arguments else {
            return error(format!("{key}: new takes a size and a column"));
        };
        let size = usize::try_from(integer(size, "the size")?)
            .map_err(|_| WireError(format!("{key}: the size is negative")))?;
        let keys = match keys {
            PyValue::None => None,
            PyValue::Bytes(bytes) => Some(bytes.clone()),
            other => return error(format!("{key}: the input keys are {}", other.kind())),
        };
        return Ok(match UnitStore::new(size, StringTable::new(), keys) {
            Ok(store) => {
                traces.insert(name.to_owned(), store);
                tuple(vec![PyValue::str("made"), count(size)])
            }
            Err(failure) => raised(&failure),
        });
    }
    let Some(store) = traces.get_mut(name) else {
        return error(format!("{key}: no store {name} is made"));
    };
    Ok(match (op.as_str(), arguments) {
        ("load-projection", [projection, no_verdict, ordinal, address, length]) => {
            let projection = recomputed_of(projection, key)?;
            let ordinal = match ordinal {
                PyValue::None => None,
                other => Some(integer(other, "the ordinal")?),
            };
            let address = address_of(address, key)?;
            outcome(
                store
                    .load_projection(
                        &projection,
                        flag(no_verdict, "no_verdict")?,
                        ordinal,
                        address.as_ref(),
                        integer(length, "the length")?,
                    )
                    .map(|row| {
                        tuple(vec![
                            PyValue::str("loaded"),
                            count(row),
                            reading(store, row),
                        ])
                    }),
            )
        }
        ("load-cached", [ordinal, cached, codepoints, found]) => {
            let cached = parsed_of(cached, key)?;
            let found = match found {
                PyValue::None => None,
                other => Some(fragment_of(other, key)?),
            };
            outcome(
                store
                    .load_cached(
                        integer(ordinal, "the ordinal")?,
                        &cached,
                        &ints_of(codepoints, "the code points")?,
                        found.as_ref(),
                    )
                    .map(|row| {
                        tuple(vec![
                            PyValue::str("loaded"),
                            count(row),
                            reading(store, row),
                        ])
                    }),
            )
        }
        ("add-prior-record", [record]) => outcome(
            store
                .add_prior_record(&prior_record_of(record, key)?)
                .map(|()| {
                    tuple(vec![
                        PyValue::str("added"),
                        count(store.prior_record_count()),
                    ])
                }),
        ),
        ("seal", []) => {
            store.seal_prior_records();
            tuple(vec![
                PyValue::str("sealed"),
                PyValue::List(sealed_records(store).collect()),
            ])
        }
        ("set-prior-line", [ordinal, line]) => {
            let row = row_of(store, ordinal, key)?;
            outcome(
                store
                    .set_prior_line(row, integer(line, "the line")?)
                    .map(|()| tuple(vec![PyValue::str("line"), int(store.prior_line(row))])),
            )
        }
        ("input-key-index", []) => match store.input_key_index() {
            Ok(index) => tuple(vec![PyValue::str("built"), count(index.nbytes())]),
            Err(KeyIndexError::Repeated(repeated)) => tuple(vec![
                PyValue::str("raised"),
                PyValue::str("RepeatedKey"),
                int(repeated.first),
                int(repeated.second),
            ]),
            Err(other) => return error(format!("{key}: {other}")),
        },
        ("set-input-key", [ordinal, input_key]) => {
            let row = row_of(store, ordinal, key)?;
            outcome(
                store
                    .set_input_key(row, &text(input_key, "the input key")?)
                    .map(|()| {
                        tuple(vec![
                            PyValue::str("input-key"),
                            PyValue::str(store.input_key_hex(row)),
                        ])
                    }),
            )
        }
        ("set-primary-units", [ordinal, assign]) => {
            let row = row_of(store, ordinal, key)?;
            let assign = targets_of(assign, key)?;
            outcome(store.set_primary_units(row, &assign).map(|()| {
                tuple(vec![
                    PyValue::str("set"),
                    tuple(
                        store
                            .primary_unit_ordinals(row)
                            .into_iter()
                            .map(|(target, suppressed)| {
                                tuple(vec![
                                    target.map_or(PyValue::None, int),
                                    PyValue::Bool(suppressed),
                                ])
                            })
                            .collect(),
                    ),
                ])
            }))
        }
        ("set-written-span", [ordinal, start, length, inflated]) => {
            let row = row_of(store, ordinal, key)?;
            outcome(
                store
                    .set_written_span(
                        row,
                        integer(start, "the start")?,
                        integer(length, "the length")?,
                        integer(inflated, "the inflated length")?,
                    )
                    .map(|()| written_value(store, row)),
            )
        }
        ("set-written-part", [ordinal, part]) => {
            let row = row_of(store, ordinal, key)?;
            store.set_written_part(row, &text(part, "the part")?);
            written_value(store, row)
        }
        ("set-written-address", [ordinal, address, inflated]) => {
            let row = row_of(store, ordinal, key)?;
            let [part, start, length] = sequence(address, "the address")? else {
                return error(format!("{key}: the address is not a triple"));
            };
            outcome(
                store
                    .set_written_address(
                        row,
                        (
                            &text(part, "the part")?,
                            integer(start, "the start")?,
                            integer(length, "the length")?,
                        ),
                        integer(inflated, "the inflated length")?,
                    )
                    .map(|()| written_value(store, row)),
            )
        }
        ("set-policy-file", [ordinal, value]) => {
            let row = row_of(store, ordinal, key)?;
            store.set_policy_file(row, optional_text_of(value, "the policy file")?.as_deref());
            tuple(vec![
                PyValue::str("policy"),
                optional_text(store.policy_file(row)),
            ])
        }
        ("set-config-note", [ordinal, value]) => {
            let row = row_of(store, ordinal, key)?;
            store.set_config_note(row, optional_text_of(value, "the config note")?.as_deref());
            tuple(vec![
                PyValue::str("note"),
                optional_text(store.config_note(row)),
            ])
        }
        ("rebase-source", [ordinal, start]) => {
            let row = row_of(store, ordinal, key)?;
            outcome(
                store
                    .rebase_source(row, integer(start, "the start")?)
                    .map(|()| {
                        tuple(vec![
                            PyValue::str("source"),
                            fragment_value(store.source(row)),
                        ])
                    }),
            )
        }
        ("index", []) => outcome(store.index().map(|(words, order)| {
            tuple(vec![
                PyValue::str("indexed"),
                PyValue::List(
                    words
                        .iter()
                        .map(|word| PyValue::str(format!("{word:016x}")))
                        .collect(),
                ),
                PyValue::List(order.iter().map(|&at| int(at)).collect()),
            ])
        })),
        ("ordinal-of", [unit_id]) => outcome(
            store
                .ordinal_of(&text(unit_id, "the id")?)
                .map(|row| tuple(vec![PyValue::str("ordinal"), count(row)])),
        ),
        ("read", [ordinal]) => reading(store, row_of(store, ordinal, key)?),
        ("patched", [ordinal, class_id, group, exemplar, no_verdict]) => {
            let row = row_of(store, ordinal, key)?;
            let class_id = text(class_id, "the class")?;
            let group = optional_text_of(group, "the duplicate group")?;
            let (exemplar, no_verdict) =
                (flag(exemplar, "exemplar")?, flag(no_verdict, "no_verdict")?);
            let line = records::record_line(&store.cached_unit(
                row,
                &class_id,
                group.as_deref(),
                exemplar,
                no_verdict,
            ))
            .map_err(|failure| WireError(format!("{key}: {}", failure.0)))?;
            tuple(vec![
                PyValue::str("patched"),
                PyValue::Bool(store.cached_as_is(
                    row,
                    &class_id,
                    group.as_deref(),
                    exemplar,
                    no_verdict,
                )),
                PyValue::Bool(store.predicted_unchanged(
                    row,
                    &class_id,
                    group.as_deref(),
                    exemplar,
                    no_verdict,
                )),
                PyValue::Bytes(line),
            ])
        }
        ("primary-unit-ids", []) => PyValue::List(
            store
                .primary_unit_ids()
                .into_iter()
                .map(PyValue::Str)
                .collect(),
        ),
        ("windows", []) => PyValue::List(
            store
                .windows()
                .map(|(row, window)| {
                    tuple(vec![
                        count(row),
                        tuple(window.into_iter().map(int).collect()),
                    ])
                })
                .collect(),
        ),
        ("prior-record-count", []) => count(store.prior_record_count()),
        ("emptied", []) => {
            let emptied = store.clone().emptied();
            tuple(vec![
                PyValue::str("emptied"),
                count(emptied.len()),
                PyValue::Bytes(emptied.input_keys().to_vec()),
                count(
                    (0..emptied.len())
                        .filter(|&row| emptied.loaded(row))
                        .count(),
                ),
            ])
        }
        _ => {
            return error(format!(
                "{key}: no store call {op:?} with {} arguments",
                arguments.len()
            ));
        }
    })
}

/// The `store-load` stage: for each line of `stage_file`, in order, make the call its `input` names, `(trace, index, call)`, on the store that trace follows, and answer it in one `{"key", "value"}` line in the typed encoding. A `new` call makes the trace's store; every other call is a `UnitStore` method the build or the stage calls on it, valued as `units_stage_fixtures._store_after` values it, the loads with what [`reading`] reads of the loaded row, and a refused call `("raised", class, message)`.
pub fn stage_store_load(stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let read = |failure: std::io::Error| {
        WireError(format!("cannot read {}: {failure}", stage_file.display()))
    };
    let mut traces: HashMap<String, UnitStore> = HashMap::new();
    for line in BufReader::new(File::open(stage_file).map_err(read)?).lines() {
        let line = line.map_err(read)?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| WireError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return error(format!("a stage line has no key: {line:.200}"));
        };
        let input = PyValue::from_typed(&record["input"])
            .map_err(|failure| WireError(format!("{key}: {failure}")))?;
        let PyValue::Tuple(parts) = &input else {
            return error(format!("{key}: the input is not a tuple"));
        };
        let [PyValue::Str(name), _index, PyValue::Tuple(call)] = parts.as_slice() else {
            return error(format!("{key}: the input is not (trace, index, call)"));
        };
        let value = answer(&mut traces, name, call, key)?;
        stage_line(out, key, &value)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rect(values: [i64; 3]) -> PyValue {
        PyValue::Dict(
            RECT_EDGES
                .iter()
                .zip(values)
                .map(|(&name, value)| (PyValue::str(name), PyValue::Int(value)))
                .collect(),
        )
    }

    fn projection() -> Recomputed {
        let content_key = "d177b592".repeat(8);
        Recomputed {
            ordinal: 1,
            unit_id: unit_id_for(&content_key).expect("an id"),
            input_key: "0123456789abcdef".repeat(4),
            content_key,
            ink_identical: false,
            picture_identical: false,
            junior_equivalent: true,
            ink_deltas: vec![("default".to_owned(), "d-34947e8c5c7e".to_owned())],
            diffs_digest: "758a".to_owned(),
            cluster: "c-33bad1c7".to_owned(),
            change_group: String::new(),
            ledger_class: String::new(),
            pair_codepoints: Some((0, 1)),
            rows: PrimaryRows {
                codepoint_values: vec![0xE650, 0xE670],
                ink_identical: false,
                picture_identical: false,
                pair: Some((0, 1)),
                after_spans: vec![(0, 1), (1, 2)],
                after_cells: vec!["qsMay/loop".to_owned(), "qsIt/sole".to_owned()],
                after_junctions: vec!["y5".to_owned()],
                before_spans: vec![(0, 2)],
                before_glyphs: vec!["qsMay".to_owned()],
                before_junctions: Vec::new(),
                junction_pairs: vec![(1, 2)],
            },
            junction_rects: vec![(
                PyValue::List(vec![PyValue::Int(1), PyValue::Int(2)]),
                rect([300, 750, 750]),
                rect([300, 650, 650]),
            )],
            mismatches: Vec::new(),
        }
    }

    #[test]
    fn a_loaded_projection_reads_back_as_it_was_loaded() {
        let mut store = UnitStore::new(2, StringTable::new(), None).expect("a store");
        let address = LoadAddress::Span("arena".to_owned(), 7, 40);
        let row = store
            .load_projection(&projection(), false, None, Some(&address), 120)
            .expect("loaded");
        assert_eq!(row, 1);
        assert_eq!(store.unit_id(1), projection().unit_id);
        assert!(store.flags(1).slim && store.flags(1).junior_equivalent);
        assert_eq!(store.machine_check(1), Some("junior_equivalent"));
        assert_eq!(store.codepoints(1), [0xE650, 0xE670]);
        assert_eq!(store.junction_pairs(1), [(1, 2)]);
        assert_eq!(store.shard_length(1), 120);
        assert_eq!(store.source(1).map(|source| source.start), Some(7));
        assert_eq!(store.primary_units(1), [(None, false)]);
        assert_eq!(
            store.load_projection(&projection(), false, None, None, 0),
            value_error("ordinal 1 is loaded twice")
        );
    }

    #[test]
    fn a_load_refuses_what_python_refuses_with_its_text() {
        let mut store = UnitStore::new(2, StringTable::new(), None).expect("a store");
        let mut wide = projection();
        wide.rows.after_spans[0].1 = 70_000;
        assert_eq!(
            store.load_projection(&wide, false, None, None, 0),
            overflow("unsigned short is greater than maximum")
        );
        let mut far = projection();
        far.pair_codepoints = Some((0, 128));
        let mut fresh = UnitStore::new(2, StringTable::new(), None).expect("a store");
        assert_eq!(
            fresh.load_projection(&far, false, None, None, 0),
            overflow("signed char is greater than maximum")
        );
        let mut rows = UnitStore::new(2, StringTable::new(), None).expect("a store");
        let mut short = projection();
        short.rows.after_junctions.clear();
        assert_eq!(
            rows.load_projection(&short, false, None, None, 0),
            value_error("ordinal 1: after row holds 2 spans, 2 names and 0 junctions")
        );
        assert_eq!(
            UnitStore::new(1, StringTable::new(), None)
                .expect("a store")
                .load_projection(&projection(), false, None, None, 0),
            raise("IndexError", "ordinal 1 is outside a store of 1 units")
        );
        assert_eq!(
            from_hex("a b"),
            value_error("non-hexadecimal number found in fromhex() arg at position 1")
        );
        assert_eq!(
            from_hex("abc"),
            value_error("fromhex() arg must contain an even number of hexadecimal digits")
        );
        assert_eq!(from_hex(" ab\tCD "), Ok(vec![0xab, 0xcd]));
    }

    #[test]
    fn the_index_bisects_ids_and_refuses_a_repeat() {
        let mut store = UnitStore::new(2, StringTable::new(), None).expect("a store");
        let mut first = projection();
        first.ordinal = 0;
        store
            .load_projection(&first, false, None, None, 0)
            .expect("loaded");
        assert_eq!(
            store.ordinal_of(&first.unit_id),
            value_error("ordinal 1 was never loaded")
        );
        let mut second = projection();
        second.input_key = "fedcba9876543210".repeat(4);
        store
            .load_projection(&second, false, None, None, 0)
            .expect("loaded");
        let refusal = store.index().expect_err("a repeated id");
        assert_eq!(refusal.class, "SystemExit");
        assert!(refusal.message.ends_with(
            ": E650:E670 and E650:E670; their carry projections hash to one 64-bit prefix"
        ));
    }
}
