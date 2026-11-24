//! The unit store's records: a line parsed ([`parse_line`], [`parsed_cached_unit`], [`ParsedCachedUnit`]); what the plan keeps of a line it does not load, sliced from the line ([`prior_record`], [`PriorRecord`]); the unit a line names, by its input key ([`named_key`], [`named_ordinal`]); a record built and written as a store line ([`CachedUnit`], [`record_line`]); the CRC-32 a prior record keeps its primary units by ([`primary_units_crc`]); and a unit id's word ([`id_word`]).
//!
//! A parse takes each value as Python's `json.loads` reads it and Python's operators take it, so it fails where Python raises, and it says how the build takes the failure ([`RecordError`]): a `KeyError`, `TypeError` or `ValueError`, which the plan answers by restarting over an emptied store; any other exception, which fails the build; or a value the record does not hold, a type no record is written with, which fails the build too.

use std::collections::HashSet;
use std::fmt;

use super::columns::KeyIndex;
use super::rowdelta::{Unread, py_int, py_str, type_name};
use crate::ffi::crc32_of;
use crate::fragment::{BASE58_ALPHABET, ID_SYMBOLS, lookup, py_eq};
use crate::pyfmt::{PyFmtError, PyValue, json};

/// What every store line starts with, `to_record` writing the input key first.
pub const KEY_PREFIX: &[u8] = b"{\"key\": \"";
/// The hex digits of an input key.
const KEY_HEX: usize = 64;
/// Where a line's unit id starts, after its input key (`unit_index._ID_START`).
const ID_START: usize = KEY_PREFIX.len() + KEY_HEX + b"\", \"id\": \"".len();
const CLASS_SEAM: &[u8] = b"\", \"class\": ";
const CONTENT_KEY_SEAM: &[u8] = b", \"content_key\": ";
const PATCHED_SEAM: &[u8] = b", \"duplicate_group\": ";
const POLICY_SEAM: &[u8] = b", \"policy_file\": ";
const ADDRESS_SEAM: &[u8] = b", \"address\": ";
/// The prefix of a unit id (`unit_cache._ID_PREFIX`).
const ID_PREFIX: &str = "u-";

/// A fragment's address in the prior corpus: its shard part, as the manifest names it, and its byte start and length on disk.
pub type Address = (String, i64, i64);

/// What reading a store record raises, by how the build takes it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RecordError {
    /// A `KeyError`, `TypeError` or `ValueError` (`unit_cache._STORE_ERRORS`), which `StoreUnreadable` wraps: the plan drops what it loaded from the store and restarts over an emptied one.
    Unreadable(String),
    /// Another exception (an `IndexError`, an `AttributeError`), which fails the build.
    Failed(String),
    /// A value the record does not hold, which fails the build.
    Unheld(String),
}

impl RecordError {
    /// How the build takes it: `unreadable`, `failed` or `unheld`.
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Unreadable(_) => "unreadable",
            Self::Failed(_) => "failed",
            Self::Unheld(_) => "unheld",
        }
    }
}

impl fmt::Display for RecordError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unreadable(message) | Self::Failed(message) | Self::Unheld(message) => {
                write!(formatter, "{}: {message}", self.kind())
            }
        }
    }
}

impl std::error::Error for RecordError {}

type Raised<T> = std::result::Result<T, RecordError>;

fn unreadable(class: &str, message: impl fmt::Display) -> RecordError {
    RecordError::Unreadable(format!("{class}: {message}"))
}

fn failed(class: &str, message: impl fmt::Display) -> RecordError {
    RecordError::Failed(format!("{class}: {message}"))
}

// --- Python's operations on a value `json.loads` gives -------------------------------------------

pub(crate) fn truthy(value: &PyValue) -> bool {
    match value {
        PyValue::None => false,
        PyValue::Bool(flag) => *flag,
        PyValue::Int(number) => *number != 0,
        PyValue::Str(text) => !text.is_empty(),
        PyValue::Bytes(bytes) => !bytes.is_empty(),
        PyValue::Tuple(items) | PyValue::List(items) | PyValue::Set(items) => !items.is_empty(),
        PyValue::Dict(entries) => !entries.is_empty(),
        PyValue::Record(..) => true,
    }
}

/// `len(value)`.
fn length(value: &PyValue) -> Raised<usize> {
    match value {
        PyValue::Str(text) => Ok(text.chars().count()),
        PyValue::Bytes(bytes) => Ok(bytes.len()),
        PyValue::Tuple(items) | PyValue::List(items) | PyValue::Set(items) => Ok(items.len()),
        PyValue::Dict(entries) => Ok(entries.len()),
        other => Err(unreadable(
            "TypeError",
            format!("object of type '{}' has no len()", type_name(other)),
        )),
    }
}

/// `value[index]`.
fn item(value: &PyValue, index: usize) -> Raised<PyValue> {
    let out_of_range = |what: &str| failed("IndexError", format!("{what} index out of range"));
    match value {
        PyValue::Tuple(items) => items
            .get(index)
            .cloned()
            .ok_or_else(|| out_of_range("tuple")),
        PyValue::List(items) => items
            .get(index)
            .cloned()
            .ok_or_else(|| out_of_range("list")),
        PyValue::Str(text) => text
            .chars()
            .nth(index)
            .map(|letter| PyValue::Str(letter.to_string()))
            .ok_or_else(|| out_of_range("string")),
        PyValue::Bytes(bytes) => bytes
            .get(index)
            .map(|&byte| PyValue::Int(i64::from(byte)))
            .ok_or_else(|| out_of_range("index")),
        PyValue::Dict(entries) => {
            let wanted = PyValue::Int(index as i64);
            entries
                .iter()
                .find(|(key, _)| py_eq(key, &wanted))
                .map(|(_, held)| held.clone())
                .ok_or_else(|| unreadable("KeyError", index))
        }
        other => Err(unreadable(
            "TypeError",
            format!("'{}' object is not subscriptable", type_name(other)),
        )),
    }
}

/// `value[name]` for a `str` key.
fn field<'a>(value: &'a PyValue, name: &str) -> Raised<&'a PyValue> {
    match value {
        PyValue::Dict(_) => {
            lookup(value, name).ok_or_else(|| unreadable("KeyError", format!("'{name}'")))
        }
        PyValue::Tuple(_) | PyValue::List(_) | PyValue::Str(_) | PyValue::Bytes(_) => {
            Err(unreadable(
                "TypeError",
                format!("{} indices must be integers", type_name(value)),
            ))
        }
        other => Err(unreadable(
            "TypeError",
            format!("'{}' object is not subscriptable", type_name(other)),
        )),
    }
}

/// What iterating `value` yields: a sequence's items, a `str`'s characters, `bytes`' ints, or a dict's keys.
fn iterate(value: &PyValue) -> Raised<Vec<PyValue>> {
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
        other => Err(unreadable(
            "TypeError",
            format!("'{}' object is not iterable", type_name(other)),
        )),
    }
}

/// `first, second = value`.
fn unpacked(value: &PyValue) -> Raised<(PyValue, PyValue)> {
    let items = iterate(value).map_err(|_| {
        unreadable(
            "TypeError",
            format!("cannot unpack non-iterable {} object", type_name(value)),
        )
    })?;
    match <[PyValue; 2]>::try_from(items) {
        Ok([first, second]) => Ok((first, second)),
        Err(items) => Err(unreadable(
            "ValueError",
            format!("expected 2 values to unpack, got {}", items.len()),
        )),
    }
}

/// `sys.intern(value)`, which takes only a `str`.
fn intern(value: &PyValue) -> Raised<String> {
    match value {
        PyValue::Str(text) => Ok(text.clone()),
        other => Err(unreadable(
            "TypeError",
            format!("intern() argument must be str, not {}", type_name(other)),
        )),
    }
}

/// Whether `hash(value)` returns, as pooling a tuple of it needs.
pub(crate) fn hashable(value: &PyValue) -> Raised<()> {
    match value {
        PyValue::List(_) | PyValue::Dict(_) | PyValue::Set(_) => Err(unreadable(
            "TypeError",
            format!("unhashable type: '{}'", type_name(value)),
        )),
        PyValue::Tuple(items) => items.iter().try_for_each(hashable),
        _ => Ok(()),
    }
}

/// `json.loads(data)` for bytes.
fn loads(data: &[u8]) -> Raised<PyValue> {
    json::loads_bytes(data).map_err(|failure| {
        if json::unheld(&failure) {
            RecordError::Unheld(failure.0)
        } else {
            unreadable("ValueError", failure.0)
        }
    })
}

/// `haystack.index(needle, start)`, None where it raises.
fn find(haystack: &[u8], needle: &[u8], start: usize) -> Option<usize> {
    if start > haystack.len() {
        return None;
    }
    haystack[start..]
        .windows(needle.len())
        .position(|window| window == needle)
        .map(|at| start + at)
}

/// `haystack.rindex(needle)`, None where it raises.
fn rfind(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .rposition(|window| window == needle)
}

fn not_found() -> RecordError {
    unreadable("ValueError", "subsection not found")
}

/// The first value a record does not hold, kept while the parse goes on, since Python reads on past it and may yet raise.
#[derive(Default)]
struct Held(Option<String>);

impl Held {
    fn note(&mut self, why: impl FnOnce() -> String) {
        if self.0.is_none() {
            self.0 = Some(why());
        }
    }

    fn noted(&mut self, what: &str, value: &PyValue) {
        self.note(|| format!("{what} is {}", value.kind()));
    }

    /// A value kept as it is that `to_record` writes as a `str`.
    fn text(&mut self, what: &str, value: &PyValue) -> String {
        match value {
            PyValue::Str(text) => text.clone(),
            other => {
                self.noted(what, other);
                String::new()
            }
        }
    }

    /// A value kept as it is that `to_record` writes as a `str` or None.
    fn optional_text(&mut self, what: &str, value: &PyValue) -> Option<String> {
        match value {
            PyValue::None => None,
            PyValue::Str(text) => Some(text.clone()),
            other => {
                self.noted(what, other);
                None
            }
        }
    }

    /// A value kept as it is that `to_record` writes as a bool.
    fn flag(&mut self, what: &str, value: &PyValue) -> bool {
        match value {
            PyValue::Bool(flag) => *flag,
            other => {
                self.noted(what, other);
                false
            }
        }
    }

    /// A value kept as it is that `to_record` writes as an int.
    fn int(&mut self, what: &str, value: &PyValue) -> i64 {
        match value {
            PyValue::Int(number) => *number,
            other => {
                self.noted(what, other);
                0
            }
        }
    }

    /// `int(value)`.
    fn converted(&mut self, value: &PyValue) -> Raised<i64> {
        match py_int(value) {
            Ok(number) => Ok(number),
            Err(Unread::Raised(raised)) => Err(unreadable(raised.class, raised.message)),
            Err(Unread::Unheld(why)) => {
                self.note(|| why);
                Ok(0)
            }
        }
    }

    fn finish<T>(self, value: T) -> Raised<T> {
        match self.0 {
            Some(why) => Err(RecordError::Unheld(why)),
            None => Ok(value),
        }
    }
}

// --- a parsed record ------------------------------------------------------------------------------

/// One parsed store record: the fields the plan reads, its address kept only in a trusted part, the projection the primary-unit resolution pass reads as pairs and names, and `junction_rects`, the record's `junctions` unchanged.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ParsedCachedUnit {
    pub key: String,
    pub prior_id: String,
    pub prior_class: String,
    pub content_key: String,
    pub slim: bool,
    pub address: Option<Address>,
    pub ink_identical: bool,
    pub picture_identical: bool,
    pub junior_equivalent: bool,
    pub ink_deltas: Vec<(String, String)>,
    pub diffs_digest: String,
    pub cluster: String,
    pub change_group: String,
    pub ledger_class: String,
    pub pair_codepoints: Option<(i64, i64)>,
    pub duplicate_group: Option<String>,
    pub exemplar: bool,
    pub no_verdict: bool,
    pub primary_units: Vec<(Option<String>, bool)>,
    pub policy_file: Option<String>,
    pub junction_rects: PyValue,
    pub mismatches: Vec<String>,
    pub pair: Option<(i64, i64)>,
    pub after_spans: Vec<(i64, i64)>,
    pub after_cells: Vec<String>,
    pub after_junctions: Vec<String>,
    pub before_spans: Vec<(i64, i64)>,
    pub before_glyphs: Vec<String>,
    pub before_junctions: Vec<String>,
    pub junction_pairs: Vec<(i64, i64)>,
    pub inflated: i64,
}

fn deltas_value(deltas: &[(String, String)]) -> PyValue {
    PyValue::Dict(
        deltas
            .iter()
            .map(|(config, delta)| (PyValue::str(config.as_str()), PyValue::str(delta.as_str())))
            .collect(),
    )
}

fn primary_units_value(units: &[(Option<String>, bool)]) -> PyValue {
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

fn texts_value(texts: &[String]) -> PyValue {
    PyValue::List(
        texts
            .iter()
            .map(|text| PyValue::str(text.as_str()))
            .collect(),
    )
}

/// `(span[0], span[1])` as `_parsed_cached_unit` pools it, which hashes both.
fn span_of(held: &mut Held, span: &PyValue) -> Raised<(i64, i64)> {
    let left = item(span, 0)?;
    let right = item(span, 1)?;
    hashable(&left)?;
    hashable(&right)?;
    Ok((
        held.int("a span's start", &left),
        held.int("a span's end", &right),
    ))
}

/// A parsed record's `spans`.
fn spans(held: &mut Held, values: &PyValue) -> Raised<Vec<(i64, i64)>> {
    iterate(values)?
        .iter()
        .map(|span| span_of(held, span))
        .collect()
}

/// A parsed record's `names`.
fn names(values: &PyValue) -> Raised<Vec<String>> {
    iterate(values)?.iter().map(intern).collect()
}

/// One parsed store line as a [`ParsedCachedUnit`], its address, three elements or a deflated fragment's four, kept only when its part is in `trusted`. Each value is taken as Python takes it, in Python's order, so the first exception Python raises is the one refused ([`RecordError`]).
pub fn parsed_cached_unit(record: &PyValue, trusted: &HashSet<String>) -> Raised<ParsedCachedUnit> {
    if !matches!(record, PyValue::Dict(_)) {
        return Err(failed(
            "AttributeError",
            format!("'{}' object has no attribute 'get'", type_name(record)),
        ));
    }
    let mut held = Held::default();
    let given = lookup(record, "address").unwrap_or(&PyValue::None);
    let mut inflated = 0;
    let mut address = None;
    if truthy(given) {
        let count = length(given)?;
        if count != 3 && count != 4 {
            return Err(unreadable(
                "ValueError",
                format!("an address holds three or four elements, not {count}"),
            ));
        }
        let part = item(given, 0)?;
        hashable(&part)?;
        if let PyValue::Str(part) = part
            && trusted.contains(&part)
        {
            if count == 4 {
                inflated = held.converted(&item(given, 3)?)?;
            }
            let start = held.converted(&item(given, 1)?)?;
            let span = held.converted(&item(given, 2)?)?;
            address = Some((part, start, span));
        }
    }
    let pair_codepoints = field(record, "pair_codepoints")?;
    let proj = field(record, "proj")?;
    let junctions = field(record, "junctions")?;
    let key = held.text("the key", field(record, "key")?);
    let prior_id = held.text("the id", field(record, "id")?);
    let prior_class = intern(field(record, "class")?)?;
    let content_key = held.text("the content key", field(record, "content_key")?);
    let slim = held.flag("slim", field(record, "slim")?);
    let ink_identical = held.flag("ink_identical", field(record, "ink_identical")?);
    let picture_identical = held.flag("picture_identical", field(record, "picture_identical")?);
    let junior_equivalent = held.flag("junior_equivalent", field(record, "junior_equivalent")?);
    let ink_deltas = match field(record, "ink_deltas")? {
        PyValue::Dict(entries) => entries
            .iter()
            .map(|(config, delta)| Ok((intern(config)?, intern(delta)?)))
            .collect::<Raised<Vec<_>>>()?,
        other => {
            return Err(failed(
                "AttributeError",
                format!("'{}' object has no attribute 'items'", type_name(other)),
            ));
        }
    };
    let diffs_digest = intern(field(record, "diffs_digest")?)?;
    let cluster = intern(field(record, "cluster")?)?;
    let change_group = intern(field(record, "change_group")?)?;
    let ledger_class = intern(field(record, "ledger_class")?)?;
    let pair_codepoints = if truthy(pair_codepoints) {
        let left = item(pair_codepoints, 0)?;
        let right = item(pair_codepoints, 1)?;
        Some((
            held.int("a judged code point", &left),
            held.int("a judged code point", &right),
        ))
    } else {
        None
    };
    let duplicate_group =
        held.optional_text("the duplicate group", field(record, "duplicate_group")?);
    let exemplar = held.flag("exemplar", field(record, "exemplar")?);
    let no_verdict = held.flag("no_verdict", field(record, "no_verdict")?);
    let mut primary_units = Vec::new();
    for entry in iterate(field(record, "primary_units")?)? {
        let (unit, suppressed) = unpacked(&entry)?;
        primary_units.push((
            held.optional_text("a primary unit", &unit),
            truthy(&suppressed),
        ));
    }
    let policy_file = held.optional_text("the policy file", field(record, "policy_file")?);
    let junction_rects = junctions.clone();
    let mut mismatches = Vec::new();
    for line in iterate(field(record, "mismatches")?)? {
        mismatches.push(held.text("a mismatch line", &line));
    }
    let given_pair = field(proj, "pair")?;
    let pair = if truthy(given_pair) {
        Some(span_of(&mut held, given_pair)?)
    } else {
        None
    };
    let after_spans = spans(&mut held, field(proj, "after_spans")?)?;
    let after_cells = names(field(proj, "after_cells")?)?;
    let after_junctions = names(field(proj, "after_junctions")?)?;
    let before_spans = spans(&mut held, field(proj, "before_spans")?)?;
    let before_glyphs = names(field(proj, "before_glyphs")?)?;
    let before_junctions = names(field(proj, "before_junctions")?)?;
    let mut junction_pairs = Vec::new();
    for junction in iterate(junctions)? {
        junction_pairs.push(span_of(&mut held, field(&junction, "pair")?)?);
    }
    held.finish(ParsedCachedUnit {
        key,
        prior_id,
        prior_class,
        content_key,
        slim,
        address,
        ink_identical,
        picture_identical,
        junior_equivalent,
        ink_deltas,
        diffs_digest,
        cluster,
        change_group,
        ledger_class,
        pair_codepoints,
        duplicate_group,
        exemplar,
        no_verdict,
        primary_units,
        policy_file,
        junction_rects,
        mismatches,
        pair,
        after_spans,
        after_cells,
        after_junctions,
        before_spans,
        before_glyphs,
        before_junctions,
        junction_pairs,
        inflated,
    })
}

/// A store line read and parsed as the plan's scans parse one.
pub fn parse_line(line: &[u8], trusted: &HashSet<String>) -> Raised<ParsedCachedUnit> {
    parsed_cached_unit(&loads(line)?, trusted)
}

// --- a prior record -------------------------------------------------------------------------------

/// What the plan keeps of a prior store record it does not load: the unit's id and class, the fields its fragment was patched with, its primary units by CRC-32 ([`primary_units_crc`]), and its fragment's address with its inflated length, 0 for a plain fragment.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PriorRecord {
    pub unit_id: String,
    pub class_id: String,
    pub duplicate_group: Option<String>,
    pub exemplar: bool,
    pub no_verdict: bool,
    pub primary_units: u32,
    pub address: Option<Address>,
    pub inflated: i64,
}

/// `unit_index.record_id`: a line's unit id, sliced from where `to_record` writes it, after the input key.
fn record_id(line: &[u8]) -> Raised<String> {
    let end = find(line, b"\"", ID_START).ok_or_else(not_found)?;
    let id = &line[ID_START..end];
    if !id.is_ascii() {
        return Err(unreadable(
            "UnicodeDecodeError",
            "'ascii' codec can't decode the id",
        ));
    }
    Ok(String::from_utf8_lossy(id).into_owned())
}

/// `unit_index.record_class`: a line's class, read from where `to_record` writes it, after the unit id.
fn record_class(line: &[u8]) -> Raised<PyValue> {
    let start = find(line, CLASS_SEAM, ID_START).ok_or_else(not_found)? + CLASS_SEAM.len();
    let end = find(line, CONTENT_KEY_SEAM, start).ok_or_else(not_found)?;
    loads(&line[start..end])
}

/// `unit_index.record_address`: a line's address, read from its end, where `to_record` writes it, as its part, start, length on disk and inflated length, the last 0 for a three-element address, or None for a record that has none.
fn record_address(line: &[u8], held: &mut Held) -> Raised<Option<(String, i64, i64, i64)>> {
    let at = rfind(line, ADDRESS_SEAM).ok_or_else(not_found)?;
    let rest = &line[at + ADDRESS_SEAM.len()..];
    let end = rest
        .iter()
        .rposition(|byte| !matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c))
        .map_or(0, |last| last + 1);
    let value = loads(&rest[..end.saturating_sub(1)])?;
    if value == PyValue::None {
        return Ok(None);
    }
    let items = iterate(&value).map_err(|_| {
        unreadable(
            "TypeError",
            format!("cannot unpack non-iterable {} object", type_name(&value)),
        )
    })?;
    if items.len() < 3 {
        return Err(unreadable(
            "ValueError",
            format!(
                "not enough values to unpack (expected at least 3, got {})",
                items.len()
            ),
        ));
    }
    if items.len() > 4 {
        return Err(unreadable(
            "ValueError",
            format!(
                "an address holds three or four elements, not {}",
                items.len()
            ),
        ));
    }
    let part = py_str(&items[0]);
    let start = held.converted(&items[1])?;
    let length = held.converted(&items[2])?;
    let inflated = match items.get(3) {
        Some(inflated) => held.converted(inflated)?,
        None => 0,
    };
    Ok(Some((part, start, length, inflated)))
}

/// `unit_index.record_id`, as the live-fragment iterator slices a line it places by.
pub(crate) fn line_id(line: &[u8]) -> Raised<String> {
    record_id(line)
}

/// `unit_index.record_class`, as the live-fragment iterator slices a line it places by.
pub(crate) fn line_class(line: &[u8]) -> Raised<PyValue> {
    record_class(line)
}

/// `unit_index.record_address`, as the live-fragment iterator slices a line it places by.
pub(crate) fn line_address(line: &[u8]) -> Raised<Option<(String, i64, i64, i64)>> {
    let mut held = Held::default();
    let address = record_address(line, &mut held)?;
    held.finish(address)
}

fn sliced(line: &[u8], held: &mut Held) -> Raised<PriorRecord> {
    let patched = find(line, PATCHED_SEAM, 0).ok_or_else(not_found)?;
    let policy = find(line, POLICY_SEAM, patched).ok_or_else(not_found)?;
    let mut text = vec![b'{'];
    text.extend_from_slice(line.get(patched + 2..policy).unwrap_or_default());
    text.push(b'}');
    let fields = loads(&text)?;
    let address = record_address(line, held)?;
    let unit_id = record_id(line)?;
    let class_id = held.text("the class", &record_class(line)?);
    let duplicate_group =
        held.optional_text("the duplicate group", field(&fields, "duplicate_group")?);
    let exemplar = truthy(field(&fields, "exemplar")?);
    let no_verdict = truthy(field(&fields, "no_verdict")?);
    let primary_units = primary_units_crc(field(&fields, "primary_units")?)
        .map_err(|failure| failed("TypeError", failure.0))?;
    let (address, inflated) = match address {
        Some((part, start, length, inflated)) => (Some((part, start, length)), inflated),
        None => (None, 0),
    };
    Ok(PriorRecord {
        unit_id,
        class_id,
        duplicate_group,
        exemplar,
        no_verdict,
        primary_units,
        address,
        inflated,
    })
}

/// The line sliced into a [`PriorRecord`] without parsing the rest, or None for a line in another shape, whose unit then has no prior record. Refused, with why, for a line Python slices into a value the record does not hold.
pub fn prior_record(line: &[u8]) -> std::result::Result<Option<PriorRecord>, String> {
    let mut held = Held::default();
    match sliced(line, &mut held) {
        Ok(record) => match held.0 {
            Some(why) => Err(why),
            None => Ok(Some(record)),
        },
        Err(RecordError::Unheld(why)) => Err(why),
        Err(_) => Ok(None),
    }
}

/// Zlib's CRC-32 of the field's JSON as `to_record` writes it, or 0 for none.
pub fn primary_units_crc(primary_units: &PyValue) -> std::result::Result<u32, PyFmtError> {
    if !truthy(primary_units) {
        return Ok(0);
    }
    Ok(crc32_of(0, json::dumps(primary_units)?.as_bytes()))
}

// --- the unit a line names ------------------------------------------------------------------------

/// The input key a store line's `key` field holds, read before the index is asked: the bytes of up to the first 64 characters after [`KEY_PREFIX`], or None for a line without the prefix or whose characters there are not lowercase hex in pairs.
pub fn named_key(line: &[u8]) -> Option<Vec<u8>> {
    let text = line.strip_prefix(KEY_PREFIX)?;
    let text = &text[..text.len().min(KEY_HEX)];
    if text.iter().any(u8::is_ascii_uppercase) || !text.len().is_multiple_of(2) {
        return None;
    }
    let digit = |byte: u8| char::from(byte).to_digit(16);
    text.chunks(2)
        .map(|pair| Some((digit(pair[0])? * 16 + digit(pair[1])?) as u8))
        .collect()
}

/// The ordinal of the unit whose input key a store line names, over the index `named` of the input-key column `column`, or None.
pub fn named_ordinal(named: &KeyIndex, column: &[u8], line: &[u8]) -> Option<u32> {
    named.find(column, &named_key(line)?)
}

// --- a record built for the store -----------------------------------------------------------------

/// One store record as the build writes it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CachedUnit {
    pub key: String,
    pub prior_id: String,
    pub prior_class: String,
    pub content_key: String,
    pub slim: bool,
    pub address: Option<Address>,
    pub ink_identical: bool,
    pub picture_identical: bool,
    pub junior_equivalent: bool,
    pub ink_deltas: Vec<(String, String)>,
    pub diffs_digest: String,
    pub cluster: String,
    pub change_group: String,
    pub ledger_class: String,
    pub pair_codepoints: Option<(i64, i64)>,
    pub proj: PyValue,
    pub junctions: PyValue,
    pub mismatches: Vec<String>,
    pub duplicate_group: Option<String>,
    pub exemplar: bool,
    pub no_verdict: bool,
    pub primary_units: Vec<(Option<String>, bool)>,
    pub policy_file: Option<String>,
    pub inflated: i64,
}

/// A fragment's address as a record holds it, with the inflated length as a fourth element only for a deflated fragment.
pub fn address_value(part: &str, start: i64, length: i64, inflated: i64) -> PyValue {
    let mut items = vec![
        PyValue::str(part),
        PyValue::Int(start),
        PyValue::Int(length),
    ];
    if inflated != 0 {
        items.push(PyValue::Int(inflated));
    }
    PyValue::List(items)
}

impl CachedUnit {
    /// The record's dict, in the order its line holds the fields.
    pub fn to_record(&self) -> PyValue {
        let entries = vec![
            ("key", PyValue::str(self.key.as_str())),
            ("id", PyValue::str(self.prior_id.as_str())),
            ("class", PyValue::str(self.prior_class.as_str())),
            ("content_key", PyValue::str(self.content_key.as_str())),
            ("slim", PyValue::Bool(self.slim)),
            ("ink_identical", PyValue::Bool(self.ink_identical)),
            ("picture_identical", PyValue::Bool(self.picture_identical)),
            ("junior_equivalent", PyValue::Bool(self.junior_equivalent)),
            ("ink_deltas", deltas_value(&self.ink_deltas)),
            ("diffs_digest", PyValue::str(self.diffs_digest.as_str())),
            ("cluster", PyValue::str(self.cluster.as_str())),
            ("change_group", PyValue::str(self.change_group.as_str())),
            ("ledger_class", PyValue::str(self.ledger_class.as_str())),
            (
                "pair_codepoints",
                self.pair_codepoints.map_or(PyValue::None, |(left, right)| {
                    PyValue::List(vec![PyValue::Int(left), PyValue::Int(right)])
                }),
            ),
            ("proj", self.proj.clone()),
            ("junctions", self.junctions.clone()),
            ("mismatches", texts_value(&self.mismatches)),
            (
                "duplicate_group",
                PyValue::optional_str(self.duplicate_group.as_deref()),
            ),
            ("exemplar", PyValue::Bool(self.exemplar)),
            ("no_verdict", PyValue::Bool(self.no_verdict)),
            ("primary_units", primary_units_value(&self.primary_units)),
            (
                "policy_file",
                PyValue::optional_str(self.policy_file.as_deref()),
            ),
            (
                "address",
                self.address
                    .as_ref()
                    .map_or(PyValue::None, |(part, start, length)| {
                        address_value(part, *start, *length, self.inflated)
                    }),
            ),
        ];
        PyValue::Dict(
            entries
                .into_iter()
                .map(|(name, value)| (PyValue::str(name), value))
                .collect(),
        )
    }
}

/// The record's line in the store, `json.dumps` of [`CachedUnit::to_record`] in the default form and a newline.
pub fn record_line(record: &CachedUnit) -> std::result::Result<Vec<u8>, PyFmtError> {
    let mut line = json::dumps(&record.to_record())?.into_bytes();
    line.push(b'\n');
    Ok(line)
}

// --- unit ids -------------------------------------------------------------------------------------

/// `unit_cache.id_word`: the 64-bit word a unit id encodes, or None for a string of another shape.
pub fn id_word(unit_id: &str) -> Option<u64> {
    let symbols = unit_id.strip_prefix(ID_PREFIX)?;
    if symbols.chars().count() != ID_SYMBOLS {
        return None;
    }
    let mut value: u128 = 0;
    for symbol in symbols.chars() {
        let index = BASE58_ALPHABET
            .iter()
            .position(|&held| char::from(held) == symbol)?;
        value = value * 58 + index as u128;
    }
    u64::try_from(value).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEY: &str = "7fb17df3c1e0a9aac242a7534fc72bebc1efeb48002666aa4b6436b5569f45a7";

    fn cached() -> CachedUnit {
        CachedUnit {
            key: KEY.to_owned(),
            prior_id: "u-c36hWTyMBQB".to_owned(),
            prior_class: "new-joins-near-tea-oy".to_owned(),
            content_key: "d177".to_owned(),
            slim: false,
            address: Some(("units/new-joins-near-tea-oy.json".to_owned(), 1, 5064)),
            ink_identical: false,
            picture_identical: false,
            junior_equivalent: false,
            ink_deltas: vec![("default".to_owned(), "d-34947e8c5c7e".to_owned())],
            diffs_digest: "758a".to_owned(),
            cluster: "c-33bad1c7".to_owned(),
            change_group: String::new(),
            ledger_class: String::new(),
            pair_codepoints: Some((0, 1)),
            proj: json::loads(
                r#"{"pair": [0, 1], "after_spans": [[0, 1], [1, 2]], "after_cells": ["qsMay/loop", "qsIt/sole"], "after_junctions": ["y5"], "before_spans": [[0, 2]], "before_glyphs": ["qsMay"], "before_junctions": []}"#,
            )
            .expect("JSON"),
            junctions: json::loads(
                r#"[{"pair": [1, 2], "before": {"x_min": 300, "x_max": 750, "advance_total": 750}, "after": {"x_min": 300, "x_max": 650, "advance_total": 650}}]"#,
            )
            .expect("JSON"),
            mismatches: vec!["·Pea".to_owned()],
            duplicate_group: Some("e-f8P2bEFApF9".to_owned()),
            exemplar: false,
            no_verdict: true,
            primary_units: vec![(None, false), (Some("u-DuyicL6fVGo".to_owned()), true)],
            policy_file: Some("glyph_data/runes/qsMay.yaml".to_owned()),
            inflated: 0,
        }
    }

    #[test]
    fn a_record_line_is_the_default_json_form_in_to_record_order() {
        let line = record_line(&cached()).expect("a line");
        let text = String::from_utf8(line).expect("ASCII");
        assert!(text.starts_with(&format!(
            "{{\"key\": \"{KEY}\", \"id\": \"u-c36hWTyMBQB\", \"class\": \"new-joins-near-tea-oy\", \"content_key\": "
        )));
        assert!(
            text.contains(
                "\"mismatches\": [\"\\u00b7Pea\"], \"duplicate_group\": \"e-f8P2bEFApF9\""
            )
        );
        assert!(
            text.ends_with(", \"address\": [\"units/new-joins-near-tea-oy.json\", 1, 5064]}\n")
        );
        let mut deflated = cached();
        deflated.inflated = 9000;
        let text = String::from_utf8(record_line(&deflated).expect("a line")).expect("ASCII");
        assert!(text.ends_with(", 1, 5064, 9000]}\n"));
    }

    #[test]
    fn a_written_line_parses_back_and_slices_to_its_prior_record() {
        let line = record_line(&cached()).expect("a line");
        let trusted: HashSet<String> = ["units/new-joins-near-tea-oy.json".to_owned()].into();
        let parsed = parse_line(&line, &trusted).expect("a record");
        assert_eq!(parsed.address, cached().address);
        assert_eq!(parsed.pair, Some((0, 1)));
        assert_eq!(parsed.after_spans, [(0, 1), (1, 2)]);
        assert_eq!(parsed.junction_pairs, [(1, 2)]);
        assert_eq!(parsed.primary_units, cached().primary_units);
        assert_eq!(
            parse_line(&line, &HashSet::new())
                .expect("a record")
                .address,
            None
        );
        let prior = prior_record(&line).expect("held").expect("a record");
        assert_eq!(prior.unit_id, "u-c36hWTyMBQB");
        assert_eq!(prior.class_id, "new-joins-near-tea-oy");
        assert!(prior.no_verdict && !prior.exemplar);
        assert_eq!(
            prior.primary_units,
            crc32_of(0, br#"[[null, false], ["u-DuyicL6fVGo", true]]"#)
        );
        assert_eq!(named_key(&line).map(|key| key.len()), Some(32));
    }

    #[test]
    fn a_parse_raises_where_python_raises_and_says_how_the_build_takes_it() {
        let line = String::from_utf8(record_line(&cached()).expect("a line")).expect("ASCII");
        let edited = |edits: &[(&str, &str)]| {
            let text = edits
                .iter()
                .fold(line.clone(), |text, (from, to)| text.replacen(from, to, 1));
            parse_line(text.as_bytes(), &HashSet::new())
                .map(|_| ())
                .map_err(|failure| failure.kind())
        };
        assert_eq!(edited(&[]), Ok(()));
        let ink_deltas = "\"ink_deltas\": {\"default\": \"d-34947e8c5c7e\"}";
        assert_eq!(
            edited(&[("\"class\": \"new", "\"klass\": \"new")]),
            Err("unreadable")
        );
        assert_eq!(
            edited(&[("\"pair_codepoints\": [0, 1]", "\"pair_codepoints\": [0]")]),
            Err("failed")
        );
        assert_eq!(
            edited(&[(ink_deltas, "\"ink_deltas\": [\"default\"]")]),
            Err("failed")
        );
        assert_eq!(edited(&[("\"slim\": false", "\"slim\": 0")]), Err("unheld"));
        assert_eq!(
            edited(&[
                ("\"slim\": false", "\"slim\": 0"),
                ("\"diffs_digest\": \"758a\"", "\"diffs_digest\": 5")
            ]),
            Err("unreadable")
        );
        assert_eq!(
            edited(&[
                ("\"class\": \"new-joins-near-tea-oy\"", "\"class\": 5"),
                (ink_deltas, "\"ink_deltas\": []")
            ]),
            Err("unreadable")
        );
    }

    #[test]
    fn an_id_word_reads_eleven_base58_symbols_that_fit_64_bits() {
        assert_eq!(id_word("u-11111111111"), Some(0));
        assert_eq!(id_word("u-11111111112"), Some(1));
        assert_eq!(id_word("u-jpXCZedGfVQ"), Some(u64::MAX));
        assert_eq!(id_word("u-jpXCZedGfVR"), None);
        assert_eq!(id_word("e-11111111111"), None);
        assert_eq!(id_word("u-1111111111"), None);
        assert_eq!(id_word("u-1111111111l"), None);
    }

    #[test]
    fn a_named_key_is_up_to_64_lowercase_hex_digits_in_pairs() {
        let line = |text: &str| [KEY_PREFIX, text.as_bytes()].concat();
        assert_eq!(named_key(&line("0a1b")), Some(vec![0x0a, 0x1b]));
        assert_eq!(named_key(&line("0A1B")), None);
        assert_eq!(named_key(&line("0a1")), None);
        assert_eq!(named_key(&line("0g")), None);
        assert_eq!(named_key(b"{\"key\":\"00\""), None);
    }
}
