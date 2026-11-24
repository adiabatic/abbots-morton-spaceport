//! The unit store's records: a line parsed ([`parse_line`], [`parsed_cached_unit`], [`ParsedCachedUnit`]), or read straight from its bytes where it holds exactly what [`parse_line`] parses without refusing it ([`CachedRecords`], [`CachedView`], [`Cached`]); what the plan keeps of a line it does not load, sliced from the line ([`prior_record`], [`PriorRecord`]); the unit a line names, by its input key ([`named_key`], [`named_ordinal`]); a record built and written as a store line ([`CachedUnit`], [`record_line`]); the CRC-32 a prior record keeps its primary units by ([`primary_units_crc`]); and a unit id's word ([`id_word`]).
//!
//! A parse takes each value as Python's `json.loads` reads it and Python's operators take it, so it fails where Python raises, and it says how the build takes the failure ([`RecordError`]): a `KeyError`, `TypeError` or `ValueError`, which the plan answers by restarting over an emptied store; any other exception, which fails the build; or a value the record does not hold, a type no record is written with, which fails the build too.

use std::collections::HashSet;
use std::fmt;

use super::columns::KeyIndex;
use super::rowdelta::{Unread, py_int, py_str, type_name};
use crate::ffi::crc32_of;
use crate::fragment::{BASE58_ALPHABET, ID_SYMBOLS, lookup, py_eq};
use crate::pyfmt::json::Cursor;
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

// --- records read flat ----------------------------------------------------------------------------

/// A string's place in a [`CachedRecords`]' text: its start and its length in bytes, or [`NO_TEXT`] for None.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct TextSpan {
    start: u32,
    length: u32,
}

/// The span of a string that is None.
const NO_TEXT: TextSpan = TextSpan {
    start: u32::MAX,
    length: 0,
};

/// A list's place in one of a [`CachedRecords`]' item columns: its first item and its count.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct ItemRun {
    start: u32,
    count: u32,
}

impl ItemRun {
    fn range(self) -> std::ops::Range<usize> {
        self.start as usize..(self.start + self.count) as usize
    }
}

/// One secondary junction of a record read flat: its pair, and its before and after rects' edges in `store::RECT_EDGES` order.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct FlatJunction {
    pub pair: (i64, i64),
    pub before: [i64; 3],
    pub after: [i64; 3],
}

const SLIM: u8 = 1;
const INK_IDENTICAL: u8 = 2;
const PICTURE_IDENTICAL: u8 = 4;
const JUNIOR_EQUIVALENT: u8 = 8;
const EXEMPLAR: u8 = 16;
const NO_VERDICT: u8 = 32;

/// One record read flat: each string a span of the text, each list a run of an item column, the address present when its part is.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct FlatEntry {
    key: TextSpan,
    prior_id: TextSpan,
    prior_class: TextSpan,
    content_key: TextSpan,
    diffs_digest: TextSpan,
    cluster: TextSpan,
    change_group: TextSpan,
    ledger_class: TextSpan,
    duplicate_group: TextSpan,
    policy_file: TextSpan,
    part: TextSpan,
    start: i64,
    length: i64,
    inflated: i64,
    pair_codepoints: Option<(i64, i64)>,
    pair: Option<(i64, i64)>,
    bits: u8,
    ink_deltas: ItemRun,
    primary_units: ItemRun,
    mismatches: ItemRun,
    after_spans: ItemRun,
    after_cells: ItemRun,
    after_junctions: ItemRun,
    before_spans: ItemRun,
    before_glyphs: ItemRun,
    before_junctions: ItemRun,
    junctions: ItemRun,
}

/// The top-level fields a record read flat takes, by their bit in the set of fields seen; every one but `address` is required.
const RECORD_FIELDS: [&[u8]; 23] = [
    b"key",
    b"id",
    b"class",
    b"content_key",
    b"slim",
    b"ink_identical",
    b"picture_identical",
    b"junior_equivalent",
    b"ink_deltas",
    b"diffs_digest",
    b"cluster",
    b"change_group",
    b"ledger_class",
    b"pair_codepoints",
    b"proj",
    b"junctions",
    b"mismatches",
    b"duplicate_group",
    b"exemplar",
    b"no_verdict",
    b"primary_units",
    b"policy_file",
    b"address",
];

/// The fields of a record's `proj` a record read flat takes, by their bit; every one is required.
const PROJECTION_FIELDS: [&[u8]; 7] = [
    b"pair",
    b"after_spans",
    b"after_cells",
    b"after_junctions",
    b"before_spans",
    b"before_glyphs",
    b"before_junctions",
];

/// The bit of the one name in `names` that `key` is, the name at `hint` tried first, as a line written in the names' order holds its keys.
fn field_bit(names: &[&[u8]], key: &[u8], hint: u32) -> Option<u32> {
    if names.get(hint as usize) == Some(&key) {
        return Some(hint);
    }
    names
        .iter()
        .position(|&name| name == key)
        .map(|at| at as u32)
}

/// Records of the unit store read straight from their lines' bytes ([`CachedRecords::read`]), held flat: every string in one text, every list's items in a column per kind, and a fixed-size entry per record. A line is read flat only when [`parse_line`] parses it without refusing it and every value has the one type the build writes it with, so a record read flat is the [`ParsedCachedUnit`] [`parse_line`] gives, with its junctions read into numbers as `UnitStore::load_cached` reads them without refusing them; any other line is left to [`parse_line`].
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CachedRecords {
    text: String,
    texts: Vec<TextSpan>,
    units: Vec<(TextSpan, bool)>,
    pairs: Vec<(i64, i64)>,
    junctions: Vec<FlatJunction>,
    entries: Vec<FlatEntry>,
}

impl CachedRecords {
    pub fn new() -> Self {
        Self::default()
    }

    /// Records with room for `records` entries and `text` bytes of strings, the most a run of lines of that many bytes holds.
    pub fn with_capacity(records: usize, text: usize) -> Self {
        Self {
            text: String::with_capacity(text),
            entries: Vec::with_capacity(records),
            ..Self::default()
        }
    }

    /// How many records it holds.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The record at `index`, in the order they were read.
    pub fn get(&self, index: usize) -> CachedView<'_> {
        CachedView {
            records: self,
            entry: &self.entries[index],
        }
    }

    /// Read `line` as one more record, its address kept only when its part is in `trusted`, and give its index; None, with nothing kept, for a line that is not read flat.
    pub fn read(&mut self, line: &[u8], trusted: &HashSet<String>) -> Option<usize> {
        let marks = (
            self.text.len(),
            self.texts.len(),
            self.units.len(),
            self.pairs.len(),
            self.junctions.len(),
        );
        match self.record(line, trusted) {
            Some(entry) => {
                self.entries.push(entry);
                Some(self.entries.len() - 1)
            }
            None => {
                self.text.truncate(marks.0);
                self.texts.truncate(marks.1);
                self.units.truncate(marks.2);
                self.pairs.truncate(marks.3);
                self.junctions.truncate(marks.4);
                None
            }
        }
    }

    fn record(&mut self, line: &[u8], trusted: &HashSet<String>) -> Option<FlatEntry> {
        if line.first() != Some(&b'{') {
            return None;
        }
        let mut cursor = Cursor::new(std::str::from_utf8(line).ok()?);
        cursor.open_object()?;
        let mut entry = FlatEntry {
            duplicate_group: NO_TEXT,
            policy_file: NO_TEXT,
            part: NO_TEXT,
            ..FlatEntry::default()
        };
        let mut seen = 0u32;
        let mut first = true;
        while let Some(key) = cursor.key(first)? {
            first = false;
            let Some(bit) = field_bit(&RECORD_FIELDS, key.as_bytes(), seen.trailing_ones()) else {
                cursor.skip()?;
                continue;
            };
            if seen & (1 << bit) != 0 {
                return None;
            }
            seen |= 1 << bit;
            let cursor = &mut cursor;
            match bit {
                0 => entry.key = self.string(cursor)?,
                1 => entry.prior_id = self.string(cursor)?,
                2 => entry.prior_class = self.string(cursor)?,
                3 => entry.content_key = self.string(cursor)?,
                4 => entry.bits |= flag_bit(cursor, SLIM)?,
                5 => entry.bits |= flag_bit(cursor, INK_IDENTICAL)?,
                6 => entry.bits |= flag_bit(cursor, PICTURE_IDENTICAL)?,
                7 => entry.bits |= flag_bit(cursor, JUNIOR_EQUIVALENT)?,
                8 => entry.ink_deltas = self.deltas(cursor)?,
                9 => entry.diffs_digest = self.string(cursor)?,
                10 => entry.cluster = self.string(cursor)?,
                11 => entry.change_group = self.string(cursor)?,
                12 => entry.ledger_class = self.string(cursor)?,
                13 => entry.pair_codepoints = optional_pair(cursor)?,
                14 => self.projection(cursor, &mut entry)?,
                15 => entry.junctions = self.flat_junctions(cursor)?,
                16 => entry.mismatches = self.strings(cursor)?,
                17 => entry.duplicate_group = self.optional_string(cursor)?,
                18 => entry.bits |= flag_bit(cursor, EXEMPLAR)?,
                19 => entry.bits |= flag_bit(cursor, NO_VERDICT)?,
                20 => entry.primary_units = self.primary_units(cursor)?,
                21 => entry.policy_file = self.optional_string(cursor)?,
                _ => self.address(cursor, trusted, &mut entry)?,
            }
        }
        cursor.end()?;
        let required = (1u32 << (RECORD_FIELDS.len() - 1)) - 1;
        (seen & required == required).then_some(entry)
    }

    fn string(&mut self, cursor: &mut Cursor<'_>) -> Option<TextSpan> {
        let start = self.text.len();
        cursor.string(&mut self.text)?;
        Some(TextSpan {
            start: u32::try_from(start).ok()?,
            length: u32::try_from(self.text.len() - start).ok()?,
        })
    }

    fn optional_string(&mut self, cursor: &mut Cursor<'_>) -> Option<TextSpan> {
        if cursor.peek()? == b'n' {
            cursor.null()?;
            return Some(NO_TEXT);
        }
        self.string(cursor)
    }

    fn text_at(&self, span: TextSpan) -> &str {
        &self.text[span.start as usize..(span.start + span.length) as usize]
    }

    fn texts_run(&self) -> Option<u32> {
        u32::try_from(self.texts.len()).ok()
    }

    fn close_run(start: u32, end: usize) -> Option<ItemRun> {
        Some(ItemRun {
            start,
            count: u32::try_from(end).ok()? - start,
        })
    }

    /// A list of strings.
    fn strings(&mut self, cursor: &mut Cursor<'_>) -> Option<ItemRun> {
        let start = self.texts_run()?;
        cursor.open_array()?;
        let mut first = true;
        while cursor.item(first)? {
            let span = self.string(cursor)?;
            self.texts.push(span);
            first = false;
        }
        Self::close_run(start, self.texts.len())
    }

    /// `ink_deltas`, a dict of strings, as its keys and values in turn; a key held twice is not read flat.
    fn deltas(&mut self, cursor: &mut Cursor<'_>) -> Option<ItemRun> {
        let start = self.texts_run()?;
        cursor.open_object()?;
        let mut first = true;
        while let Some(key) = cursor.key(first)? {
            first = false;
            if self.texts[start as usize..]
                .iter()
                .step_by(2)
                .any(|&held| self.text_at(held) == key)
            {
                return None;
            }
            let key_start = self.text.len();
            self.text.push_str(key);
            self.texts.push(TextSpan {
                start: u32::try_from(key_start).ok()?,
                length: u32::try_from(key.len()).ok()?,
            });
            let value = self.string(cursor)?;
            self.texts.push(value);
        }
        Some(ItemRun {
            start,
            count: (u32::try_from(self.texts.len()).ok()? - start) / 2,
        })
    }

    /// A list of `[int, int]` spans.
    fn spans(&mut self, cursor: &mut Cursor<'_>) -> Option<ItemRun> {
        let start = u32::try_from(self.pairs.len()).ok()?;
        cursor.open_array()?;
        let mut first = true;
        while cursor.item(first)? {
            let pair = int_pair(cursor)?;
            self.pairs.push(pair);
            first = false;
        }
        Self::close_run(start, self.pairs.len())
    }

    /// `primary_units`, a list of `[str or null, bool]` pairs.
    fn primary_units(&mut self, cursor: &mut Cursor<'_>) -> Option<ItemRun> {
        let start = u32::try_from(self.units.len()).ok()?;
        cursor.open_array()?;
        let mut first = true;
        while cursor.item(first)? {
            first = false;
            cursor.open_array()?;
            if !cursor.item(true)? {
                return None;
            }
            let unit = self.optional_string(cursor)?;
            if !cursor.item(false)? {
                return None;
            }
            let suppressed = cursor.bool()?;
            if cursor.item(false)? {
                return None;
            }
            self.units.push((unit, suppressed));
        }
        Self::close_run(start, self.units.len())
    }

    /// `proj`, a dict holding each of [`PROJECTION_FIELDS`] once.
    fn projection(&mut self, cursor: &mut Cursor<'_>, entry: &mut FlatEntry) -> Option<()> {
        cursor.open_object()?;
        let mut seen = 0u32;
        let mut first = true;
        while let Some(key) = cursor.key(first)? {
            first = false;
            let Some(bit) = field_bit(&PROJECTION_FIELDS, key.as_bytes(), seen.trailing_ones())
            else {
                cursor.skip()?;
                continue;
            };
            if seen & (1 << bit) != 0 {
                return None;
            }
            seen |= 1 << bit;
            match bit {
                0 => entry.pair = optional_pair(cursor)?,
                1 => entry.after_spans = self.spans(cursor)?,
                2 => entry.after_cells = self.strings(cursor)?,
                3 => entry.after_junctions = self.strings(cursor)?,
                4 => entry.before_spans = self.spans(cursor)?,
                5 => entry.before_glyphs = self.strings(cursor)?,
                _ => entry.before_junctions = self.strings(cursor)?,
            }
        }
        (seen == (1 << PROJECTION_FIELDS.len()) - 1).then_some(())
    }

    /// `junctions`, a list of dicts each holding its `pair` and its `before` and `after` rects once.
    fn flat_junctions(&mut self, cursor: &mut Cursor<'_>) -> Option<ItemRun> {
        let start = u32::try_from(self.junctions.len()).ok()?;
        cursor.open_array()?;
        let mut first = true;
        while cursor.item(first)? {
            first = false;
            cursor.open_object()?;
            let mut junction = FlatJunction::default();
            let mut seen = 0u8;
            let mut first_key = true;
            while let Some(key) = cursor.key(first_key)? {
                first_key = false;
                let bit = match key {
                    "pair" => 1,
                    "before" => 2,
                    "after" => 4,
                    _ => {
                        cursor.skip()?;
                        continue;
                    }
                };
                if seen & bit != 0 {
                    return None;
                }
                seen |= bit;
                match bit {
                    1 => junction.pair = int_pair(cursor)?,
                    2 => junction.before = rect(cursor)?,
                    _ => junction.after = rect(cursor)?,
                }
            }
            if seen != 7 {
                return None;
            }
            self.junctions.push(junction);
        }
        Self::close_run(start, self.junctions.len())
    }

    /// `address`: None, or a list of three or four whose first item is a `str`; the place is kept, its other items ints, only when that part is in `trusted`.
    fn address(
        &mut self,
        cursor: &mut Cursor<'_>,
        trusted: &HashSet<String>,
        entry: &mut FlatEntry,
    ) -> Option<()> {
        if cursor.peek()? == b'n' {
            return cursor.null();
        }
        cursor.open_array()?;
        if !cursor.item(true)? {
            return Some(());
        }
        let part = self.string(cursor)?;
        let kept = trusted.contains(self.text_at(part));
        let mut items = [0i64; 3];
        let mut count = 1;
        while cursor.item(false)? {
            if count == 4 {
                return None;
            }
            if kept {
                items[count - 1] = cursor.int()?;
            } else {
                cursor.skip()?;
            }
            count += 1;
        }
        if count < 3 {
            return None;
        }
        if kept {
            entry.part = part;
            entry.start = items[0];
            entry.length = items[1];
            entry.inflated = if count == 4 { items[2] } else { 0 };
        } else {
            self.text.truncate(part.start as usize);
        }
        Some(())
    }
}

/// `true` or `false`, as `bit` or 0.
fn flag_bit(cursor: &mut Cursor<'_>, bit: u8) -> Option<u8> {
    Some(if cursor.bool()? { bit } else { 0 })
}

/// A list of exactly two ints.
fn int_pair(cursor: &mut Cursor<'_>) -> Option<(i64, i64)> {
    cursor.open_array()?;
    if !cursor.item(true)? {
        return None;
    }
    let left = cursor.int()?;
    if !cursor.item(false)? {
        return None;
    }
    let right = cursor.int()?;
    if cursor.item(false)? {
        return None;
    }
    Some((left, right))
}

/// None for `null` or `[]`, which are falsy, or a list of exactly two ints.
fn optional_pair(cursor: &mut Cursor<'_>) -> Option<Option<(i64, i64)>> {
    if cursor.peek()? == b'n' {
        cursor.null()?;
        return Some(None);
    }
    if cursor.peek()? == b'[' {
        let mut probe = cursor.clone();
        probe.open_array()?;
        if !probe.item(true)? {
            *cursor = probe;
            return Some(None);
        }
    }
    int_pair(cursor).map(Some)
}

/// A rect, a dict of exactly `x_min`, `x_max` and `advance_total` in that order, each an int.
fn rect(cursor: &mut Cursor<'_>) -> Option<[i64; 3]> {
    cursor.open_object()?;
    let mut edges = [0i64; 3];
    for (at, name) in ["x_min", "x_max", "advance_total"].into_iter().enumerate() {
        if cursor.key(at == 0)? != Some(name) {
            return None;
        }
        edges[at] = cursor.int()?;
    }
    cursor.key(false)?.is_none().then_some(edges)
}

/// A list of strings of a record read flat, in order.
#[derive(Clone, Debug)]
pub struct Texts<'a> {
    records: &'a CachedRecords,
    spans: std::slice::Iter<'a, TextSpan>,
}

impl<'a> Iterator for Texts<'a> {
    type Item = &'a str;

    fn next(&mut self) -> Option<&'a str> {
        self.spans.next().map(|&span| self.records.text_at(span))
    }

    fn size_hint(&self) -> (usize, Option<usize>) {
        self.spans.size_hint()
    }
}

impl ExactSizeIterator for Texts<'_> {}

/// A record read flat ([`CachedRecords`]), with the fields [`ParsedCachedUnit`] has.
#[derive(Clone, Copy, Debug)]
pub struct CachedView<'a> {
    records: &'a CachedRecords,
    entry: &'a FlatEntry,
}

impl<'a> CachedView<'a> {
    fn text(&self, span: TextSpan) -> &'a str {
        self.records.text_at(span)
    }

    fn optional(&self, span: TextSpan) -> Option<&'a str> {
        (span != NO_TEXT).then(|| self.text(span))
    }

    fn texts(&self, run: ItemRun) -> Texts<'a> {
        Texts {
            records: self.records,
            spans: self.records.texts[run.range()].iter(),
        }
    }

    fn bit(&self, bit: u8) -> bool {
        self.entry.bits & bit != 0
    }

    pub fn key(&self) -> &'a str {
        self.text(self.entry.key)
    }

    pub fn prior_id(&self) -> &'a str {
        self.text(self.entry.prior_id)
    }

    pub fn prior_class(&self) -> &'a str {
        self.text(self.entry.prior_class)
    }

    pub fn content_key(&self) -> &'a str {
        self.text(self.entry.content_key)
    }

    pub fn slim(&self) -> bool {
        self.bit(SLIM)
    }

    /// The address kept in a trusted part: its part, start and length on disk.
    pub fn address(&self) -> Option<(&'a str, i64, i64)> {
        self.optional(self.entry.part)
            .map(|part| (part, self.entry.start, self.entry.length))
    }

    pub fn inflated(&self) -> i64 {
        self.entry.inflated
    }

    pub fn ink_identical(&self) -> bool {
        self.bit(INK_IDENTICAL)
    }

    pub fn picture_identical(&self) -> bool {
        self.bit(PICTURE_IDENTICAL)
    }

    pub fn junior_equivalent(&self) -> bool {
        self.bit(JUNIOR_EQUIVALENT)
    }

    /// `ink_deltas`' configs and digests, in the record's order.
    pub fn ink_deltas(&self) -> impl ExactSizeIterator<Item = (&'a str, &'a str)> + use<'a> {
        let records = self.records;
        let run = self.entry.ink_deltas;
        let start = run.start as usize;
        records.texts[start..start + 2 * run.count as usize]
            .as_chunks::<2>()
            .0
            .iter()
            .map(move |pair| (records.text_at(pair[0]), records.text_at(pair[1])))
    }

    pub fn diffs_digest(&self) -> &'a str {
        self.text(self.entry.diffs_digest)
    }

    pub fn cluster(&self) -> &'a str {
        self.text(self.entry.cluster)
    }

    pub fn change_group(&self) -> &'a str {
        self.text(self.entry.change_group)
    }

    pub fn ledger_class(&self) -> &'a str {
        self.text(self.entry.ledger_class)
    }

    pub fn pair_codepoints(&self) -> Option<(i64, i64)> {
        self.entry.pair_codepoints
    }

    pub fn duplicate_group(&self) -> Option<&'a str> {
        self.optional(self.entry.duplicate_group)
    }

    pub fn exemplar(&self) -> bool {
        self.bit(EXEMPLAR)
    }

    pub fn no_verdict(&self) -> bool {
        self.bit(NO_VERDICT)
    }

    pub fn primary_units(
        &self,
    ) -> impl ExactSizeIterator<Item = (Option<&'a str>, bool)> + use<'a> {
        let view = *self;
        self.records.units[self.entry.primary_units.range()]
            .iter()
            .map(move |&(unit, suppressed)| (view.optional(unit), suppressed))
    }

    pub fn policy_file(&self) -> Option<&'a str> {
        self.optional(self.entry.policy_file)
    }

    pub fn mismatches(&self) -> Texts<'a> {
        self.texts(self.entry.mismatches)
    }

    pub fn pair(&self) -> Option<(i64, i64)> {
        self.entry.pair
    }

    pub fn after_spans(&self) -> &'a [(i64, i64)] {
        &self.records.pairs[self.entry.after_spans.range()]
    }

    pub fn after_cells(&self) -> Texts<'a> {
        self.texts(self.entry.after_cells)
    }

    pub fn after_junctions(&self) -> Texts<'a> {
        self.texts(self.entry.after_junctions)
    }

    pub fn before_spans(&self) -> &'a [(i64, i64)] {
        &self.records.pairs[self.entry.before_spans.range()]
    }

    pub fn before_glyphs(&self) -> Texts<'a> {
        self.texts(self.entry.before_glyphs)
    }

    pub fn before_junctions(&self) -> Texts<'a> {
        self.texts(self.entry.before_junctions)
    }

    /// The secondary junctions, each pair with its rects' edges.
    pub fn junctions(&self) -> &'a [FlatJunction] {
        &self.records.junctions[self.entry.junctions.range()]
    }
}

/// A record of the prior store as the threaded scan gives it: read flat, or parsed where it is not read flat.
#[derive(Clone, Copy, Debug)]
pub enum Cached<'a> {
    Flat(CachedView<'a>),
    Parsed(&'a ParsedCachedUnit),
}

impl<'a> Cached<'a> {
    pub fn prior_id(&self) -> &'a str {
        match self {
            Self::Flat(view) => view.prior_id(),
            Self::Parsed(cached) => &cached.prior_id,
        }
    }

    pub fn prior_class(&self) -> &'a str {
        match self {
            Self::Flat(view) => view.prior_class(),
            Self::Parsed(cached) => &cached.prior_class,
        }
    }

    /// The address kept in a trusted part: its part, start and length on disk.
    pub fn address(&self) -> Option<(&'a str, i64, i64)> {
        match self {
            Self::Flat(view) => view.address(),
            Self::Parsed(cached) => cached
                .address
                .as_ref()
                .map(|(part, start, length)| (part.as_str(), *start, *length)),
        }
    }

    pub fn slim(&self) -> bool {
        match self {
            Self::Flat(view) => view.slim(),
            Self::Parsed(cached) => cached.slim,
        }
    }

    /// `ink_identical`, `picture_identical` and `junior_equivalent`.
    pub fn machine_flags(&self) -> (bool, bool, bool) {
        match self {
            Self::Flat(view) => (
                view.ink_identical(),
                view.picture_identical(),
                view.junior_equivalent(),
            ),
            Self::Parsed(cached) => (
                cached.ink_identical,
                cached.picture_identical,
                cached.junior_equivalent,
            ),
        }
    }
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
    let mut key = [0u8; KEY_HEX / 2];
    let length = named_key_into(line, &mut key)?;
    Some(key[..length].to_vec())
}

/// [`named_key`] written into `key`, giving how many of its bytes it fills.
fn named_key_into(line: &[u8], key: &mut [u8; KEY_HEX / 2]) -> Option<usize> {
    let text = line.strip_prefix(KEY_PREFIX)?;
    let text = &text[..text.len().min(KEY_HEX)];
    if text.iter().any(u8::is_ascii_uppercase) || !text.len().is_multiple_of(2) {
        return None;
    }
    for (byte, pair) in key.iter_mut().zip(text.as_chunks::<2>().0) {
        *byte = (hex_digit(pair[0])? << 4) | hex_digit(pair[1])?;
    }
    Some(text.len() / 2)
}

/// The ordinal of the unit whose input key a store line names, over the index `named` of the input-key column `column`, or None.
pub fn named_ordinal(named: &KeyIndex, column: &[u8], line: &[u8]) -> Option<u32> {
    let mut key = [0u8; KEY_HEX / 2];
    let length = named_key_into(line, &mut key)?;
    named.find(column, &key[..length])
}

/// Each byte's value as a hex digit in either case, or [`NOT_HEX`].
const HEX_DIGITS: [u8; 256] = {
    let mut digits = [NOT_HEX; 256];
    let mut byte = 0;
    while byte < 10 {
        digits[b'0' as usize + byte] = byte as u8;
        byte += 1;
    }
    let mut letter = 0;
    while letter < 6 {
        digits[b'a' as usize + letter] = 10 + letter as u8;
        digits[b'A' as usize + letter] = 10 + letter as u8;
        letter += 1;
    }
    digits
};

/// What [`HEX_DIGITS`] holds for a byte that is not a hex digit.
const NOT_HEX: u8 = 0xff;

/// The byte's value as a hex digit in either case.
pub(crate) fn hex_digit(byte: u8) -> Option<u8> {
    let digit = HEX_DIGITS[usize::from(byte)];
    (digit != NOT_HEX).then_some(digit)
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

    /// Whether a record read flat holds what [`parse_line`] parses from the same line, its junction rects read as `UnitStore::load_cached` reads them.
    fn same_record(view: &CachedView<'_>, parsed: &ParsedCachedUnit) -> bool {
        let owned = |texts: Texts<'_>| texts.map(str::to_owned).collect::<Vec<_>>();
        let edges = |rect: &PyValue| {
            ["x_min", "x_max", "advance_total"].map(|name| match lookup(rect, name) {
                Some(PyValue::Int(value)) => *value,
                _ => i64::MIN,
            })
        };
        let PyValue::List(rects) = &parsed.junction_rects else {
            return false;
        };
        let junctions: Vec<FlatJunction> = rects
            .iter()
            .zip(&parsed.junction_pairs)
            .map(|(junction, &pair)| FlatJunction {
                pair,
                before: edges(lookup(junction, "before").unwrap_or(&PyValue::None)),
                after: edges(lookup(junction, "after").unwrap_or(&PyValue::None)),
            })
            .collect();
        view.key() == parsed.key
            && view.prior_id() == parsed.prior_id
            && view.prior_class() == parsed.prior_class
            && view.content_key() == parsed.content_key
            && view.slim() == parsed.slim
            && view
                .address()
                .map(|(part, start, length)| (part.to_owned(), start, length))
                == parsed.address
            && view.inflated() == parsed.inflated
            && (
                view.ink_identical(),
                view.picture_identical(),
                view.junior_equivalent(),
            ) == (
                parsed.ink_identical,
                parsed.picture_identical,
                parsed.junior_equivalent,
            )
            && view
                .ink_deltas()
                .map(|(config, delta)| (config.to_owned(), delta.to_owned()))
                .collect::<Vec<_>>()
                == parsed.ink_deltas
            && view.diffs_digest() == parsed.diffs_digest
            && view.cluster() == parsed.cluster
            && view.change_group() == parsed.change_group
            && view.ledger_class() == parsed.ledger_class
            && view.pair_codepoints() == parsed.pair_codepoints
            && view.duplicate_group() == parsed.duplicate_group.as_deref()
            && view.exemplar() == parsed.exemplar
            && view.no_verdict() == parsed.no_verdict
            && view
                .primary_units()
                .map(|(unit, suppressed)| (unit.map(str::to_owned), suppressed))
                .collect::<Vec<_>>()
                == parsed.primary_units
            && view.policy_file() == parsed.policy_file.as_deref()
            && owned(view.mismatches()) == parsed.mismatches
            && view.pair() == parsed.pair
            && view.after_spans() == parsed.after_spans
            && owned(view.after_cells()) == parsed.after_cells
            && owned(view.after_junctions()) == parsed.after_junctions
            && view.before_spans() == parsed.before_spans
            && owned(view.before_glyphs()) == parsed.before_glyphs
            && owned(view.before_junctions()) == parsed.before_junctions
            && view.junctions() == junctions
    }

    /// Read `line` flat and parse it: a line read flat must parse to the same record. Gives whether it was read flat.
    fn read_as_parsed(records: &mut CachedRecords, line: &[u8], trusted: &HashSet<String>) -> bool {
        let before = records.clone();
        let Some(at) = records.read(line, trusted) else {
            assert_eq!(*records, before, "a line not read flat keeps nothing");
            return false;
        };
        let parsed = parse_line(line, trusted)
            .unwrap_or_else(|failure| panic!("{failure} for {}", String::from_utf8_lossy(line)));
        assert!(
            same_record(&records.get(at), &parsed),
            "{}",
            String::from_utf8_lossy(line)
        );
        true
    }

    /// Store lines, each beside whether it is read flat: the written forms, then edits of the first.
    fn sample_lines() -> Vec<(String, bool)> {
        let written = |unit: &CachedUnit| {
            String::from_utf8(record_line(unit).expect("a line")).expect("ASCII")
        };
        let line = written(&cached());
        let mut deflated = cached();
        deflated.inflated = 9000;
        deflated.address = Some(("units/a.json".to_owned(), 7, 50));
        deflated.pair_codepoints = None;
        deflated.junctions = PyValue::List(Vec::new());
        deflated.primary_units = Vec::new();
        deflated.duplicate_group = None;
        deflated.policy_file = None;
        deflated.ink_deltas = Vec::new();
        deflated.mismatches = vec!["a\"b\\c\n\u{e9}".to_owned(), String::new()];
        let mut unaddressed = cached();
        unaddressed.address = None;
        let mut astral = cached();
        astral.mismatches = vec!["\u{1f600}".to_owned()];
        let edited = |from: &str, to: &str| line.replacen(from, to, 1);
        let rect = "\"advance_total\": 750";
        vec![
            (line.clone(), true),
            (written(&deflated), true),
            (written(&unaddressed), true),
            (written(&astral), false),
            (
                edited("\"slim\"", "\"extra\": [1, {\"a\": null}], \"slim\""),
                true,
            ),
            (line.replace(", \"", " ,\n\t\"").replace(": ", " :  "), true),
            (
                edited("\"pair_codepoints\": [0, 1]", "\"pair_codepoints\": []"),
                true,
            ),
            (
                edited("\"pair\": [0, 1], \"after", "\"pair\": null, \"after"),
                true,
            ),
            (edited("\"c-33bad1c7\"", "\"c-\\u0033\\/\""), true),
            (edited("\"y5\"", "\"y5\", \"y6\""), true),
            (
                edited(rect, "\"advance_total\": -9223372036854775808"),
                true,
            ),
            (edited(rect, "\"advance_total\": -0"), true),
            (
                edited(
                    "\"units/new-joins-near-tea-oy.json\", 1",
                    "\"units/b.json\", \"x\"",
                ),
                true,
            ),
            (line.replace("}\n", "}\n\n"), true),
            (
                edited("\"after\": {", "\"z\": \"\\ud83d\\ude00\", \"after\": {"),
                false,
            ),
            (edited("\"key\": \"", "\"\\u006bey\": \""), false),
            (
                edited(
                    "\"exemplar\": false",
                    "\"exemplar\": false, \"exemplar\": true",
                ),
                false,
            ),
            (
                edited("\"default\": ", "\"default\": \"x\", \"default\": "),
                false,
            ),
            (
                edited(
                    "\"before_spans\": [[0, 2]]",
                    "\"before_spans\": [[0, 2, 3]]",
                ),
                false,
            ),
            (edited(rect, "\"advance_total\": 750.0"), false),
            (edited(rect, "\"advance_total\": 1e3"), false),
            (
                edited(rect, "\"advance_total\": 99999999999999999999"),
                false,
            ),
            (edited(rect, "\"advance_total\": 0750"), false),
            (edited(rect, "\"x_max\": 1"), false),
            (edited("\"slim\": false", "\"slim\": 0"), false),
            (edited("[null, false]", "[null, 0]"), false),
            (edited("5064]", "5064, 1, 2]"), false),
            (edited("5064]", "\"5064\"]"), false),
            (line.replace("}\n", "}  x\n"), false),
            (edited("\"d177\"", "\"\\ud800\""), false),
            (format!("\u{feff}{line}"), false),
            (format!(" {line}"), false),
        ]
    }

    #[test]
    fn a_line_read_flat_is_the_record_parse_line_parses() {
        let trusted: HashSet<String> = [
            "units/new-joins-near-tea-oy.json".to_owned(),
            "units/a.json".to_owned(),
        ]
        .into();
        let mut records = CachedRecords::new();
        for (line, flat) in sample_lines() {
            assert_eq!(
                read_as_parsed(&mut records, line.as_bytes(), &trusted),
                flat,
                "{line}"
            );
        }
        let view = records.get(1);
        assert_eq!(
            view.mismatches().collect::<Vec<_>>(),
            ["a\"b\\c\n\u{e9}", ""]
        );
        assert_eq!(
            (view.address(), view.inflated()),
            (Some(("units/a.json", 7, 50)), 9000)
        );
        assert_eq!(records.get(7).cluster(), "c-3/");
        let line = &sample_lines()[0].0;
        assert!(read_as_parsed(
            &mut records,
            line.as_bytes(),
            &HashSet::new()
        ));
        assert_eq!(records.get(records.len() - 1).address(), None);
    }

    #[test]
    fn a_mangled_line_is_read_flat_only_as_the_record_parse_line_parses() {
        let trusted: HashSet<String> = ["units/new-joins-near-tea-oy.json".to_owned()].into();
        let pieces: [&[u8]; 16] = [
            b"\"",
            b"\\",
            b"\\u00e9",
            b"{",
            b"}",
            b"[",
            b"]",
            b",",
            b":",
            b" ",
            b"0",
            b"-",
            b".5",
            b"null",
            b"\xc3\xa9",
            b"\xed\xa0\x80",
        ];
        let mut state = 0x2545_f491_4f6c_dd1du64;
        let mut next = |bound: usize| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            (state % bound as u64) as usize
        };
        let mut records = CachedRecords::new();
        let mut flat = 0;
        for (line, _) in sample_lines() {
            for _ in 0..400 {
                let mut mangled = line.clone().into_bytes();
                for _ in 0..1 + next(3) {
                    let at = next(mangled.len());
                    match next(3) {
                        0 => {
                            mangled.remove(at);
                        }
                        1 => {
                            let piece = pieces[next(pieces.len())];
                            mangled.splice(at..at, piece.iter().copied());
                        }
                        _ => {
                            let end = (at + 1 + next(12)).min(mangled.len());
                            let copied = mangled[at..end].to_vec();
                            let to = next(mangled.len());
                            mangled.splice(to..to, copied);
                        }
                    }
                }
                if read_as_parsed(&mut records, &mangled, &trusted) {
                    flat += 1;
                }
            }
        }
        assert!(flat > 0);
    }
}
