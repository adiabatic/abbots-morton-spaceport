//! Each run's sidecar lines as the write projects them: a unit's units-index line, and its app-index row or, outside the triage index, its locator row ([`SidecarLines::unit`]), each carrying the span the shard writer returned and the trailing `"deflated": true` exactly when that span is a deflated fragment's; and a byte-copied unit's lines copied forward from the previous corpus's sidecars, read in step through a cursor over each ([`LineCursor`]), with this build's place and address put in, or projected from its stored bytes when a cursor cannot reach it ([`SidecarLines::cached`]). The index's and the app index's lines are appended plain to spill files in the build's scratch directory, unlinked as they are made, so a run holds none of them in memory; the locator's rows are cut into blocks as they come ([`LocatorBlocks`]) and each block appended to a third spill file as the gzip member the rows file takes. The headers wait for the manifest, so the sidecars are written from the spill files once it is on disk.
//!
//! The projections are the units index's record and line, the app index's row, the locator's row and their lines, and the copy-forward surgery on a prior index or app line; each fails with the exception class Python raises over a fragment it cannot read.

use std::fs::{File, OpenOptions};
use std::io::{self, BufWriter, Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

use super::BuildError;
use super::gzip::{APP_LEVEL, GzipError, GzipWriter, MTIME, compress_member, inflate_fragment};
use super::index::{IndexNames, IndexWrite, SpilledLines, prior_lines, write_index_lines};
use super::input::BuildInput;
use super::records::truthy;
use super::route::RouteNames;
use super::rowdelta::{
    FoldClaim, GzipLineReader, KeyedLines, PartReader, ReadError, gzip_line_reader, py_str,
    type_name,
};
use super::scan::{OpenIndex, ScanError, ScanNames, Thrown, manifest_and_identity, open_index};
use super::shards::Span;
use super::sink::Sink;
use crate::fragment::{lookup, py_eq};
use crate::pyfmt::{PyValue, json, repr::repr_str};
use crate::wire::{Result as WireResult, WireError};

/// The flag `app_index._SLIMMED_FLAGS` adds to the machine checks.
const NO_VERDICT: &str = "no_verdict";
/// The field a cursor finds a line by.
const ID_FIELD: &str = "id";
/// `unit_index.CLASS_SEAM`, where an index line's place ends.
const CLASS_SEAM: &[u8] = b", \"class\": ";
/// Where an app-index row's address starts.
const SHARD_PART_SEAM: &[u8] = b", \"shard_part\": ";

/// Each spill file's serial in this process, so two runs over one directory never name one file.
static SPILLS: AtomicU64 = AtomicU64::new(0);

/// What a projection, a cursor or a spill file raises, by the Python class it mirrors, with its text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SidecarError {
    pub class: String,
    pub message: String,
}

impl SidecarError {
    fn new(class: &str, message: impl Into<String>) -> Self {
        Self {
            class: class.to_owned(),
            message: message.into(),
        }
    }

    fn os(failure: &io::Error) -> Self {
        Self::new("OSError", failure.to_string())
    }
}

impl std::fmt::Display for SidecarError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.class, self.message)
    }
}

impl std::error::Error for SidecarError {}

impl From<ReadError> for SidecarError {
    fn from(failure: ReadError) -> Self {
        Self::new(failure.class(), failure.message())
    }
}

impl From<SidecarError> for BuildError {
    fn from(failure: SidecarError) -> Self {
        Self::Failed(failure.to_string())
    }
}

type Raised<T> = std::result::Result<T, SidecarError>;

/// The names the sidecar lines read from the build input's vocabulary: the units index's and the app index's files, the locator's block size, the flags no app-index row may carry (`app_index._SLIMMED_FLAGS`, the machine checks and `no_verdict`), and the names the units index is read by.
#[derive(Clone, Debug)]
pub struct SidecarNames {
    pub index_name: String,
    pub app_index_name: String,
    pub block_rows: usize,
    pub slimmed: Vec<String>,
    pub scan: ScanNames,
}

impl SidecarNames {
    /// The names as the build input's vocabulary gives them.
    pub fn of(input: &BuildInput) -> WireResult<Self> {
        let route = RouteNames::of(input)?;
        let name = "rebuild.review.app_index.LOCATOR_BLOCK_ROWS";
        let block_rows = match input.constant(name) {
            Some(PyValue::Int(rows)) if *rows > 0 => usize::try_from(*rows)
                .map_err(|_| WireError(format!("{name} is {rows}, past this machine's sizes")))?,
            Some(other) => return Err(WireError(format!("{name} is {other:?}, not a count"))),
            None => {
                return Err(WireError(format!(
                    "the build input's vocabulary holds no {name}"
                )));
            }
        };
        let mut slimmed = route.machine_checks;
        slimmed.push(NO_VERDICT.to_owned());
        Ok(Self {
            index_name: route.scan.index_name.clone(),
            app_index_name: route.app_index_name,
            block_rows,
            slimmed,
            scan: route.scan,
        })
    }
}

// --- Python's operations on a fragment ----------------------------------------------------------

/// `value.get(key)`: a dict's value, None when it lacks the key; any other value has no `get`.
fn get(value: &PyValue, key: &str) -> Raised<PyValue> {
    match value {
        PyValue::Dict(_) => Ok(lookup(value, key).cloned().unwrap_or(PyValue::None)),
        other => Err(SidecarError::new(
            "AttributeError",
            format!("'{}' object has no attribute 'get'", type_name(other)),
        )),
    }
}

/// `value[key]` for a `str` key.
fn subscript(value: &PyValue, key: &str) -> Raised<PyValue> {
    match value {
        PyValue::Dict(_) => lookup(value, key)
            .cloned()
            .ok_or_else(|| SidecarError::new("KeyError", repr_str(key))),
        PyValue::Str(_) => Err(SidecarError::new(
            "TypeError",
            "string indices must be integers, not 'str'",
        )),
        PyValue::List(_) | PyValue::Tuple(_) => Err(SidecarError::new(
            "TypeError",
            format!(
                "{} indices must be integers or slices, not str",
                type_name(value)
            ),
        )),
        other => Err(SidecarError::new(
            "TypeError",
            format!("'{}' object is not subscriptable", type_name(other)),
        )),
    }
}

/// `value or {}`.
fn or_empty(value: PyValue) -> PyValue {
    if truthy(&value) {
        value
    } else {
        PyValue::Dict(Vec::new())
    }
}

/// `value or []`.
fn or_list(value: PyValue) -> PyValue {
    if truthy(&value) {
        value
    } else {
        PyValue::List(Vec::new())
    }
}

/// `len(value or [])`.
fn length(value: PyValue) -> Raised<PyValue> {
    let count = match or_list(value) {
        PyValue::Str(text) => text.chars().count(),
        PyValue::Bytes(bytes) => bytes.len(),
        PyValue::Tuple(items) | PyValue::List(items) | PyValue::Set(items) => items.len(),
        PyValue::Dict(entries) => entries.len(),
        other => {
            return Err(SidecarError::new(
                "TypeError",
                format!("object of type '{}' has no len()", type_name(&other)),
            ));
        }
    };
    Ok(PyValue::Int(count as i64))
}

/// `a <= b` between two unit ids.
fn at_most(left: &PyValue, right: &PyValue) -> Raised<bool> {
    let number = |value: &PyValue| match value {
        PyValue::Int(number) => Some(*number),
        PyValue::Bool(flag) => Some(i64::from(*flag)),
        _ => None,
    };
    match (left, right) {
        (PyValue::Str(left), PyValue::Str(right)) => Ok(left <= right),
        _ => match (number(left), number(right)) {
            (Some(left), Some(right)) => Ok(left <= right),
            _ => Err(SidecarError::new(
                "TypeError",
                format!(
                    "'<=' not supported between instances of '{}' and '{}'",
                    type_name(left),
                    type_name(right)
                ),
            )),
        },
    }
}

fn dict(entries: Vec<(&str, PyValue)>) -> PyValue {
    PyValue::Dict(
        entries
            .into_iter()
            .map(|(key, value)| (PyValue::str(key), value))
            .collect(),
    )
}

fn optional_int(value: Option<i64>) -> PyValue {
    value.map_or(PyValue::None, PyValue::Int)
}

/// `json.dumps(record, ensure_ascii=False)` as bytes; a value the writer refuses where Python writes it (a dict key that is not a `str`) fails.
fn unicode_text(record: &PyValue) -> Raised<Vec<u8>> {
    json::dumps_unicode(record)
        .map(String::into_bytes)
        .map_err(|failure| SidecarError::new("Unwritten", failure.0))
}

/// `app_index._line`: a record's line, `ensure_ascii=False`, with its newline.
fn line_of(record: &PyValue) -> Raised<Vec<u8>> {
    let mut line = unicode_text(record)?;
    line.push(b'\n');
    Ok(line)
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

fn rfind(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .rposition(|window| window == needle)
}

fn not_found() -> SidecarError {
    SidecarError::new("ValueError", "subsection not found")
}

/// `app_index._DEFLATED_MARK`: `"deflated": true` after the address of a deflated fragment's row, nothing after a plain one's.
fn with_mark(mut entries: Vec<(&'static str, PyValue)>, deflated: bool) -> PyValue {
    if deflated {
        entries.push(("deflated", PyValue::Bool(true)));
    }
    dict(entries)
}

// --- the projections ----------------------------------------------------------------------------

/// `unit_index.index_record`: a fragment's units-index record, with its place in the triage index.
pub fn index_record(fragment: &PyValue, order: Option<i64>, batch: Option<i64>) -> Raised<PyValue> {
    let before = or_empty(get(fragment, "before")?);
    let after = or_empty(get(fragment, "after")?);
    let policy = get(&or_empty(get(fragment, "drafts")?), "policy")?;
    let field = |key: &str| get(fragment, key);
    let listed = |key: &str| get(fragment, key).map(or_list);
    Ok(dict(vec![
        ("id", subscript(fragment, "id")?),
        ("order", optional_int(order)),
        ("batch", optional_int(batch)),
        ("class", field("class")?),
        ("cluster", field("cluster")?),
        ("duplicate_group", field("duplicate_group")?),
        ("group", field("group")?),
        ("notation", field("notation")?),
        ("notation_tokens", listed("notation_tokens")?),
        ("codepoints", field("codepoints")?),
        ("configs", listed("configs")?),
        ("kinds", listed("kinds")?),
        ("ink_identical", field("ink_identical")?),
        ("picture_identical", field("picture_identical")?),
        ("junior_equivalent", field("junior_equivalent")?),
        ("ink_deltas", field("ink_deltas")?),
        ("no_verdict", field("no_verdict")?),
        ("content_key", field("content_key")?),
        ("render_groups", length(field("render_groups")?)?),
        ("summary", field("summary")?),
        ("provenance", listed("provenance")?),
        ("pair", field("pair")?),
        (
            "secondary_junctions",
            length(field("secondary_junctions")?)?,
        ),
        (
            "before",
            dict(vec![
                ("glyphs", or_list(get(&before, "glyphs")?)),
                ("junctions", or_list(get(&before, "junctions")?)),
            ]),
        ),
        (
            "after",
            dict(vec![
                ("cells", or_list(get(&after, "cells")?)),
                ("junctions", or_list(get(&after, "junctions")?)),
            ]),
        ),
        (
            "policy",
            if truthy(&policy) {
                dict(vec![
                    ("file", subscript(&policy, "file")?),
                    ("keypath", subscript(&policy, "keypath")?),
                    ("suggested_record", get(&policy, "suggested_record")?),
                ])
            } else {
                PyValue::None
            },
        ),
    ]))
}

/// `unit_index.index_line`: a fragment's units-index line, with its place.
pub fn index_line(fragment: &PyValue, order: Option<i64>, batch: Option<i64>) -> Raised<Vec<u8>> {
    line_of(&index_record(fragment, order, batch)?)
}

/// Append an int as `json.dumps` writes it.
fn push_int(out: &mut Vec<u8>, value: i64) {
    let mut digits = [0u8; 20];
    let mut at = digits.len();
    let mut rest = value.unsigned_abs();
    loop {
        at -= 1;
        digits[at] = b'0' + (rest % 10) as u8;
        rest /= 10;
        if rest == 0 {
            break;
        }
    }
    if value < 0 {
        out.push(b'-');
    }
    out.extend_from_slice(&digits[at..]);
}

/// Append `value` as `json.dumps(value, ensure_ascii=False)` writes it when it is None, a bool, an int or a str, through `text`, a string buffer the caller keeps; false, with nothing appended, for any other value.
fn push_scalar(out: &mut Vec<u8>, text: &mut String, value: &PyValue) -> bool {
    match value {
        PyValue::None => out.extend_from_slice(b"null"),
        PyValue::Bool(true) => out.extend_from_slice(b"true"),
        PyValue::Bool(false) => out.extend_from_slice(b"false"),
        PyValue::Int(number) => push_int(out, *number),
        PyValue::Str(value) => {
            text.clear();
            json::write_unicode_string(text, value);
            out.extend_from_slice(text.as_bytes());
        }
        _ => return false,
    }
    true
}

fn push_optional_int(out: &mut Vec<u8>, value: Option<i64>) {
    match value {
        Some(value) => push_int(out, value),
        None => out.extend_from_slice(b"null"),
    }
}

/// Append [`line_head`]'s bytes.
fn push_line_head(
    out: &mut Vec<u8>,
    text: &mut String,
    unit_id: &str,
    order: Option<i64>,
    batch: Option<i64>,
) {
    out.extend_from_slice(b"{\"id\": ");
    text.clear();
    json::write_unicode_string(text, unit_id);
    out.extend_from_slice(text.as_bytes());
    out.extend_from_slice(b", \"order\": ");
    push_optional_int(out, order);
    out.extend_from_slice(b", \"batch\": ");
    push_optional_int(out, batch);
}

/// Append a row's address, its mark when it is a deflated fragment's ([`with_mark`]), and the closing brace, as `json.dumps` writes the tail of a dict that ends with them.
fn push_address(out: &mut Vec<u8>, span: &Span, deflated: bool) {
    out.extend_from_slice(b"\"shard_part\": ");
    push_int(out, span.part as i64);
    out.extend_from_slice(b", \"byte_start\": ");
    push_int(out, span.start);
    out.extend_from_slice(b", \"byte_length\": ");
    push_int(out, span.length);
    if deflated {
        out.extend_from_slice(b", \"deflated\": true");
    }
    out.push(b'}');
}

/// The start of an index or app-index line, its id and place, without the closing brace.
pub fn line_head(unit_id: &str, order: Option<i64>, batch: Option<i64>) -> Raised<Vec<u8>> {
    let mut head = Vec::new();
    push_line_head(&mut head, &mut String::new(), unit_id, order, batch);
    Ok(head)
}

/// A previous index line, a base's with its place or a segment's with none, with this build's id and place joined to its tail from the class on.
pub fn copy_forward_index_line(
    line: &[u8],
    unit_id: &str,
    order: Option<i64>,
    batch: Option<i64>,
) -> Raised<Vec<u8>> {
    let mut copied = Vec::new();
    copy_forward_index_into(&mut copied, &mut String::new(), line, unit_id, order, batch)?;
    Ok(copied)
}

/// [`copy_forward_index_line`] appended to `out`, through `text`, a string buffer the caller keeps.
fn copy_forward_index_into(
    out: &mut Vec<u8>,
    text: &mut String,
    line: &[u8],
    unit_id: &str,
    order: Option<i64>,
    batch: Option<i64>,
) -> Raised<()> {
    push_line_head(out, text, unit_id, order, batch);
    let at = find(line, CLASS_SEAM).ok_or_else(not_found)?;
    out.extend_from_slice(&line[at..]);
    Ok(())
}

/// `app_index.app_row`: a human unit's app-index row at its address. A fragment carrying a machine check or `no_verdict` fails as the assertion does.
pub fn app_row(
    fragment: &PyValue,
    span: &Span,
    order: Option<i64>,
    batch: Option<i64>,
    deflated: bool,
    slimmed: &[String],
) -> Raised<PyValue> {
    for flag in slimmed {
        if truthy(&get(fragment, flag)?) {
            return Err(SidecarError::new(
                "AssertionError",
                py_str(&get(fragment, "id")?),
            ));
        }
    }
    let field = |key: &str| get(fragment, key);
    let listed = |key: &str| get(fragment, key).map(or_list);
    Ok(with_mark(
        vec![
            ("id", subscript(fragment, "id")?),
            ("order", optional_int(order)),
            ("batch", optional_int(batch)),
            ("class", field("class")?),
            ("ledger_class", field("ledger_class")?),
            ("group", field("group")?),
            ("duplicate_group", field("duplicate_group")?),
            ("cluster", field("cluster")?),
            ("notation", field("notation")?),
            ("notation_tokens", listed("notation_tokens")?),
            ("codepoints", field("codepoints")?),
            ("pair", field("pair")?),
            ("pair_codepoints", field("pair_codepoints")?),
            ("boundary_marks", listed("boundary_marks")?),
            ("secondary_junctions", field("secondary_junctions")?),
            ("configs", listed("configs")?),
            ("config_gate", field("config_gate")?),
            ("config_note", field("config_note")?),
            ("config_classes", field("config_classes")?),
            ("config_class_note", field("config_class_note")?),
            ("render_groups", field("render_groups")?),
            ("summary", field("summary")?),
            ("exemplar", field("exemplar")?),
            ("kinds", listed("kinds")?),
            ("shard_part", PyValue::Int(span.part as i64)),
            ("byte_start", PyValue::Int(span.start)),
            ("byte_length", PyValue::Int(span.length)),
        ],
        deflated,
    ))
}

/// `app_index.app_line`: [`app_row`]'s line.
pub fn app_line(
    fragment: &PyValue,
    span: &Span,
    order: Option<i64>,
    batch: Option<i64>,
    deflated: bool,
    slimmed: &[String],
) -> Raised<Vec<u8>> {
    line_of(&app_row(fragment, span, order, batch, deflated, slimmed)?)
}

/// `app_index.locator_row`: a unit's id, class and address.
pub fn locator_row(fragment: &PyValue, span: &Span, deflated: bool) -> Raised<PyValue> {
    Ok(with_mark(
        vec![
            ("id", subscript(fragment, "id")?),
            ("class", get(fragment, "class")?),
            ("shard_part", PyValue::Int(span.part as i64)),
            ("byte_start", PyValue::Int(span.start)),
            ("byte_length", PyValue::Int(span.length)),
        ],
        deflated,
    ))
}

/// `app_index.locator_line`: [`locator_row`]'s line.
pub fn locator_line(fragment: &PyValue, span: &Span, deflated: bool) -> Raised<Vec<u8>> {
    line_of(&locator_row(fragment, span, deflated)?)
}

/// [`locator_line`] appended to `out`, through `text`, a string buffer the caller keeps, when the fragment's id and class are each None, a bool, an int or a str, with the two values, which reading the line back gives; None, with nothing appended, for any other id or class.
fn push_locator_line(
    out: &mut Vec<u8>,
    text: &mut String,
    fragment: &PyValue,
    span: &Span,
    deflated: bool,
) -> Raised<Option<(PyValue, PyValue)>> {
    let unit_id = subscript(fragment, "id")?;
    let class_id = get(fragment, "class")?;
    let start = out.len();
    out.extend_from_slice(b"{\"id\": ");
    if !push_scalar(out, text, &unit_id) {
        out.truncate(start);
        return Ok(None);
    }
    out.extend_from_slice(b", \"class\": ");
    if !push_scalar(out, text, &class_id) {
        out.truncate(start);
        return Ok(None);
    }
    out.extend_from_slice(b", ");
    push_address(out, span, deflated);
    out.push(b'\n');
    Ok(Some((unit_id, class_id)))
}

/// A previous app-index row with this build's id and place at its head and this build's address and mark at its tail, the fields between them its own.
pub fn copy_forward_app_line(
    line: &[u8],
    unit_id: &str,
    order: i64,
    batch: i64,
    span: &Span,
    deflated: bool,
) -> Raised<Vec<u8>> {
    let mut copied = Vec::new();
    copy_forward_app_into(
        &mut copied,
        &mut String::new(),
        line,
        unit_id,
        (order, batch),
        span,
        deflated,
    )?;
    Ok(copied)
}

/// [`copy_forward_app_line`] appended to `out`, through `text`, a string buffer the caller keeps; `place` is the order and the batch.
fn copy_forward_app_into(
    out: &mut Vec<u8>,
    text: &mut String,
    line: &[u8],
    unit_id: &str,
    place: (i64, i64),
    span: &Span,
    deflated: bool,
) -> Raised<()> {
    let start = find(line, b", \"class\": ").ok_or_else(not_found)?;
    let end = rfind(line, SHARD_PART_SEAM).ok_or_else(not_found)?;
    let middle = line.get(start..end).unwrap_or_default();
    push_line_head(out, text, unit_id, Some(place.0), Some(place.1));
    out.extend_from_slice(middle);
    out.extend_from_slice(b", ");
    push_address(out, span, deflated);
    out.push(b'\n');
    Ok(())
}

// --- spill files --------------------------------------------------------------------------------

/// What a spill file's writer gathers before it writes to the file.
const SPILL_WRITE_BYTES: usize = 64 * 1024;

/// A spill file being appended to, unlinked as it is made, and how many bytes it holds.
pub struct Spill {
    writer: BufWriter<File>,
    length: i64,
}

impl Spill {
    /// A new spill file in `directory`.
    pub fn new(directory: &Path) -> Raised<Self> {
        let path = directory.join(format!(
            ".ams-review-sidecar-{}-{}",
            std::process::id(),
            SPILLS.fetch_add(1, Ordering::Relaxed)
        ));
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|failure| SidecarError::os(&failure))?;
        std::fs::remove_file(&path).map_err(|failure| SidecarError::os(&failure))?;
        Ok(Self {
            writer: BufWriter::with_capacity(SPILL_WRITE_BYTES, file),
            length: 0,
        })
    }

    fn write(&mut self, data: &[u8]) -> Raised<()> {
        self.writer
            .write_all(data)
            .map_err(|failure| SidecarError::os(&failure))?;
        self.length += data.len() as i64;
        Ok(())
    }

    /// The spill file once every byte is written.
    pub fn finish(self) -> Raised<Spilled> {
        let length = self.length;
        let file = self
            .writer
            .into_inner()
            .map_err(|failure| SidecarError::os(failure.error()))?;
        Ok(Spilled { file, length })
    }
}

/// A finished spill file and how many bytes it holds.
pub struct Spilled {
    pub file: File,
    pub length: i64,
}

// --- the locator's blocks -----------------------------------------------------------------------

/// One line of the locator's table (`app_index.locator_block`): its gzip member's span in the run's rows, the class every row in it belongs to, its first and last ids and how many rows it holds.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LocatorBlock {
    pub class: PyValue,
    pub start: i64,
    pub length: i64,
    pub first: PyValue,
    pub last: PyValue,
    pub units: i64,
}

impl LocatorBlock {
    /// The table line's record, its `byte_start` moved by `base`, the bytes of the rows file before the run's first block.
    pub fn value(&self, base: i64) -> PyValue {
        dict(vec![
            ("class", self.class.clone()),
            ("byte_start", PyValue::Int(base + self.start)),
            ("byte_length", PyValue::Int(self.length)),
            ("first", self.first.clone()),
            ("last", self.last.clone()),
            ("units", PyValue::Int(self.units)),
        ])
    }
}

/// `app_index.LocatorBlocks`: the locator's lines cut into blocks as they come, each block written to the rows spill file as its own gzip member (level 6, mtime 0) once it holds `block_rows` rows or the class changes. A row whose id does not ascend past the last one in its class is refused.
pub struct LocatorBlocks {
    rows: Spill,
    block_rows: usize,
    blocks: Vec<LocatorBlock>,
    pending: Vec<u8>,
    count: usize,
    class: PyValue,
    first: PyValue,
    last: Option<PyValue>,
}

impl LocatorBlocks {
    pub fn new(rows: Spill, block_rows: usize) -> Self {
        Self {
            rows,
            block_rows,
            blocks: Vec::new(),
            pending: Vec::new(),
            count: 0,
            class: PyValue::None,
            first: PyValue::None,
            last: None,
        }
    }

    /// `LocatorBlocks.add`: one locator line, read back for its id and class.
    pub fn add(&mut self, line: &[u8]) -> Raised<()> {
        let row = json::loads_bytes(line).map_err(|failure| {
            let class = if json::unheld(&failure) {
                "Unheld"
            } else {
                "JSONDecodeError"
            };
            SidecarError::new(class, failure.0)
        })?;
        let unit_id = subscript(&row, "id")?;
        let class_id = get(&row, "class")?;
        self.add_row(line, unit_id, class_id)
    }

    /// [`LocatorBlocks::add`] for a line whose id and class, as reading it back gives them, the caller holds.
    fn add_row(&mut self, line: &[u8], unit_id: PyValue, class_id: PyValue) -> Raised<()> {
        if self.count > 0 && !py_eq(&class_id, &self.class) {
            self.flush()?;
            self.last = None;
        } else if self.count >= self.block_rows {
            self.flush()?;
        }
        if let Some(last) = &self.last
            && at_most(&unit_id, last)?
        {
            return Err(SidecarError::new(
                "ValueError",
                format!(
                    "{} follows {} in {}: a class's rows must ascend",
                    py_str(&unit_id),
                    py_str(last),
                    py_str(&class_id)
                ),
            ));
        }
        if self.count == 0 {
            self.class = class_id;
            self.first = unit_id.clone();
        }
        self.pending.extend_from_slice(line);
        self.count += 1;
        self.last = Some(unit_id);
        Ok(())
    }

    fn flush(&mut self) -> Raised<()> {
        let member = compress_member(&self.pending, APP_LEVEL, MTIME)
            .map_err(|failure| SidecarError::new("error", failure))?;
        let start = self.rows.length;
        self.rows.write(&member)?;
        self.blocks.push(LocatorBlock {
            class: self.class.clone(),
            start,
            length: member.len() as i64,
            first: self.first.clone(),
            last: self.last.clone().unwrap_or(PyValue::None),
            units: self.count as i64,
        });
        self.pending.clear();
        self.count = 0;
        Ok(())
    }

    /// `LocatorBlocks.close`: the last block written, the table entries and the rows.
    pub fn close(mut self) -> Raised<(Vec<LocatorBlock>, Spilled)> {
        if self.count > 0 {
            self.flush()?;
        }
        Ok((self.blocks, self.rows.finish()?))
    }
}

// --- the cursors --------------------------------------------------------------------------------

/// The lines a cursor reads, lent one at a time: the units index's record lines, a base's and its segments' merged by key, or a gzip file's lines past its header line, read as iterating a `gzip.GzipFile` reads them.
enum CursorLines<'a> {
    Index(Box<KeyedLines<'a>>),
    File(GzipLineReader<'a>),
}

impl CursorLines<'_> {
    fn advance(&mut self) -> std::result::Result<bool, ReadError> {
        match self {
            Self::Index(lines) => lines.advance(),
            Self::File(lines) => lines
                .advance()
                .map_err(|failure| ReadError::of_io(&failure)),
        }
    }

    fn current(&self) -> &[u8] {
        match self {
            Self::Index(lines) => lines.current(),
            Self::File(lines) => lines.current(),
        }
    }
}

/// The previous corpus's sidecars a run's cursors read, opened as the run opens them: the units index as `unit_index.open_index` opens it, and the app index's file. A sidecar that does not open, as `LineCursor` catches it (an `OSError`, `EOFError`, `ValueError` or `StopIteration`), is none; with `copy_forward` false neither is opened.
pub struct PriorSidecars {
    copy_forward: bool,
    index: Option<OpenIndex>,
    app: Option<File>,
}

impl PriorSidecars {
    pub fn open(out_dir: &Path, copy_forward: bool, names: &SidecarNames) -> Raised<Self> {
        if !copy_forward {
            return Ok(Self {
                copy_forward,
                index: None,
                app: None,
            });
        }
        let index = match open_index(out_dir, &names.scan) {
            Ok(opened) => opened,
            Err(Thrown::Os(_) | Thrown::Value(_) | Thrown::Other("EOFError", _)) => None,
            Err(Thrown::Other(class, why)) => return Err(SidecarError::new(class, why)),
            Err(Thrown::Unheld(why)) => return Err(SidecarError::new("Unheld", why)),
        };
        Ok(Self {
            copy_forward,
            index,
            app: File::open(out_dir.join(&names.app_index_name)).ok(),
        })
    }

    /// A cursor over the units index's record lines, a base's and its segments' merged by key.
    pub fn index_cursor(&self, names: &SidecarNames) -> LineCursor<'_> {
        LineCursor::new(
            self.index
                .as_ref()
                .map(|opened| CursorLines::Index(Box::new(prior_lines(opened, &names.index_name)))),
        )
    }

    /// `LineCursor` over the app index's file: its header line read now, a file whose header does not read leaving the cursor closed, but for deflate data that does not inflate, which fails.
    pub fn app_cursor(&self) -> Raised<LineCursor<'_>> {
        let Some(file) = &self.app else {
            return Ok(LineCursor::new(None));
        };
        let mut lines = gzip_line_reader(file);
        let lines = match lines.advance() {
            Ok(true) => Some(CursorLines::File(lines)),
            Ok(false) => None,
            Err(failure) => match ReadError::of_io(&failure) {
                ReadError::Gzip(GzipError::Zlib(why)) => {
                    return Err(SidecarError::new("error", why));
                }
                _ => None,
            },
        };
        Ok(LineCursor::new(lines))
    }
}

/// A forward-only reader over a sidecar's lines that stops on the line whose leading id is the one asked for, skipping every line before it, and lends it until the next request ([`LineCursor::seek`], [`LineCursor::current`]). Once an id is not found the cursor is closed, and every later request finds nothing. A read that fails fails the request.
pub struct LineCursor<'a> {
    lines: Option<CursorLines<'a>>,
    prefix: String,
}

impl<'a> LineCursor<'a> {
    fn new(lines: Option<CursorLines<'a>>) -> Self {
        Self {
            lines,
            prefix: String::new(),
        }
    }

    /// Step to the line whose leading id is `value`, its prefix `{"id": <value>, ` as `json.dumps` writes the two strings: true when the cursor stops on it.
    pub fn seek(&mut self, value: &str) -> Raised<bool> {
        let Some(lines) = self.lines.as_mut() else {
            return Ok(false);
        };
        self.prefix.clear();
        self.prefix.push('{');
        json::write_string(&mut self.prefix, ID_FIELD);
        self.prefix.push_str(": ");
        json::write_string(&mut self.prefix, value);
        self.prefix.push_str(", ");
        loop {
            match lines.advance() {
                Ok(true) => {
                    if lines.current().starts_with(self.prefix.as_bytes()) {
                        return Ok(true);
                    }
                }
                Ok(false) => {
                    self.lines = None;
                    return Ok(false);
                }
                Err(failure) => {
                    self.lines = None;
                    return Err(failure.into());
                }
            }
        }
    }

    /// The line the last [`LineCursor::seek`] that found its id stopped on.
    pub fn current(&self) -> &[u8] {
        self.lines.as_ref().map_or(&[], CursorLines::current)
    }

    /// The line whose leading id is `value` ([`LineCursor::seek`]), None when the cursor does not find it.
    pub fn take(&mut self, value: &str) -> Raised<Option<Vec<u8>>> {
        Ok(self.seek(value)?.then(|| self.current().to_vec()))
    }
}

/// The line a cursor last stopped on, nothing for a cursor the run does not hold.
fn current_of<'a>(cursor: &'a Option<LineCursor<'_>>) -> &'a [u8] {
    cursor.as_ref().map_or(&[], LineCursor::current)
}

// --- one run's lines ----------------------------------------------------------------------------

/// The sidecar a line is written to.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Sidecar {
    Index,
    App,
    Locator,
}

/// The cursor a take reads.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cursor {
    Index,
    App,
}

/// What a run did, in order: each cursor's take with the id and the line it returned, and each line written with the sidecar it went to.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Event {
    Take(Cursor, String, Option<Vec<u8>>),
    Write(Sidecar, Vec<u8>),
}

/// What a closed run leaves: its counts, its locator blocks' table entries, whose `byte_start` counts from the run's first block, and the spill files holding its index lines, its app-index lines and its locator rows.
pub struct SidecarRun {
    pub human: i64,
    pub machine: i64,
    pub copied_forward: i64,
    pub blocks: Vec<LocatorBlock>,
    pub index: Spilled,
    pub app: Spilled,
    pub rows: Spilled,
}

/// One run's sidecar lines, in shard order, spilled as they are projected or copied forward. A line copied forward or a locator row is built in `line`, through `text`, buffers the run keeps. With `trace` set, every take and write is kept there in order.
pub struct SidecarLines<'a> {
    names: &'a SidecarNames,
    index: Spill,
    app: Spill,
    locator: LocatorBlocks,
    index_cursor: Option<LineCursor<'a>>,
    app_cursor: Option<LineCursor<'a>>,
    line: Vec<u8>,
    text: String,
    pub human: i64,
    pub machine: i64,
    pub copied_forward: i64,
    pub trace: Option<Vec<Event>>,
}

impl<'a> SidecarLines<'a> {
    /// A run whose spill files go in `scratch` and whose cursors, when `prior` copies forward, read the previous corpus's sidecars `prior` holds open.
    pub fn new(prior: &'a PriorSidecars, scratch: &Path, names: &'a SidecarNames) -> Raised<Self> {
        let index = Spill::new(scratch)?;
        let app = Spill::new(scratch)?;
        let locator = LocatorBlocks::new(Spill::new(scratch)?, names.block_rows);
        let (index_cursor, app_cursor) = if prior.copy_forward {
            (Some(prior.index_cursor(names)), Some(prior.app_cursor()?))
        } else {
            (None, None)
        };
        Ok(Self {
            names,
            index,
            app,
            locator,
            index_cursor,
            app_cursor,
            line: Vec::new(),
            text: String::new(),
            human: 0,
            machine: 0,
            copied_forward: 0,
            trace: None,
        })
    }

    fn write(&mut self, sidecar: Sidecar, line: &[u8]) -> Raised<()> {
        if let Some(trace) = &mut self.trace {
            trace.push(Event::Write(sidecar, line.to_vec()));
        }
        match sidecar {
            Sidecar::Index => self.index.write(line),
            Sidecar::App => self.app.write(line),
            Sidecar::Locator => self.locator.add(line),
        }
    }

    /// A locator row at `span`, from the id and class `fragment` holds, written straight to the locator's blocks with the two values when they are plain, else projected and read back.
    fn write_locator(&mut self, fragment: &PyValue, span: &Span, deflated: bool) -> Raised<()> {
        let mut line = std::mem::take(&mut self.line);
        line.clear();
        match push_locator_line(&mut line, &mut self.text, fragment, span, deflated)? {
            Some((unit_id, class_id)) => {
                if let Some(trace) = &mut self.trace {
                    trace.push(Event::Write(Sidecar::Locator, line.clone()));
                }
                self.locator.add_row(&line, unit_id, class_id)?;
            }
            None => {
                let projected = locator_line(fragment, span, deflated)?;
                self.write(Sidecar::Locator, &projected)?;
            }
        }
        self.line = line;
        Ok(())
    }

    /// Step `which` cursor to `unit_id`'s line, and whether it stopped there; the line is the cursor's until its next take.
    fn take(&mut self, which: Cursor, unit_id: &str) -> Raised<bool> {
        let cursor = match which {
            Cursor::Index => &mut self.index_cursor,
            Cursor::App => &mut self.app_cursor,
        };
        let Some(cursor) = cursor else {
            return Ok(false);
        };
        let found = cursor.seek(unit_id)?;
        if let Some(trace) = &mut self.trace {
            let line = found.then(|| cursor.current().to_vec());
            trace.push(Event::Take(which, unit_id.to_owned(), line));
        }
        Ok(found)
    }

    /// A fragment's index line, and its app-index row when it has a place in the triage index, else its locator row, at `span`.
    pub fn unit(
        &mut self,
        fragment: &PyValue,
        span: Span,
        order: Option<i64>,
        batch: Option<i64>,
    ) -> Raised<()> {
        let line = index_line(fragment, order, batch)?;
        self.write(Sidecar::Index, &line)?;
        let deflated = span.inflated != 0;
        if let (Some(_), Some(_)) = (order, batch) {
            let line = app_line(fragment, &span, order, batch, deflated, &self.names.slimmed)?;
            self.write(Sidecar::App, &line)?;
            self.human += 1;
        } else {
            self.write_locator(fragment, &span, deflated)?;
            self.machine += 1;
        }
        Ok(())
    }

    /// A byte-copied unit's lines copied forward from the previous sidecars, its index line and, with a place, its app-index row, each taken from its cursor; a unit a cursor cannot reach is projected from its stored bytes, inflated to `inflated` bytes when it is deflated, as [`SidecarLines::unit`] projects a fragment. Its locator row is made from `identity`.
    #[allow(clippy::too_many_arguments)]
    pub fn cached(
        &mut self,
        unit_id: &str,
        body: &[u8],
        inflated: i64,
        identity: &PyValue,
        span: Span,
        order: Option<i64>,
        batch: Option<i64>,
    ) -> Raised<()> {
        let line = self.take(Cursor::Index, unit_id)?;
        let mut row = false;
        if line && order.is_some() && batch.is_some() && self.app_cursor.is_some() {
            row = self.take(Cursor::App, unit_id)?;
        }
        if !line || (order.is_some() && !row) {
            let plain = if inflated != 0 {
                let length = usize::try_from(inflated)
                    .map_err(|_| SidecarError::new("ValueError", "a negative length"))?;
                inflate_fragment(body, Some(length))
                    .map_err(|why| SidecarError::new("ValueError", why))?
            } else {
                body.to_vec()
            };
            let fragment = json::loads_bytes(&plain).map_err(|failure| {
                let class = if json::unheld(&failure) {
                    "Unheld"
                } else {
                    "JSONDecodeError"
                };
                SidecarError::new(class, failure.0)
            })?;
            return self.unit(&fragment, span, order, batch);
        }
        let mut copied = std::mem::take(&mut self.line);
        copied.clear();
        copy_forward_index_into(
            &mut copied,
            &mut self.text,
            current_of(&self.index_cursor),
            unit_id,
            order,
            batch,
        )?;
        self.write(Sidecar::Index, &copied)?;
        let deflated = span.inflated != 0;
        match (order, batch, row) {
            (Some(order), Some(batch), true) => {
                copied.clear();
                copy_forward_app_into(
                    &mut copied,
                    &mut self.text,
                    current_of(&self.app_cursor),
                    unit_id,
                    (order, batch),
                    &span,
                    deflated,
                )?;
                self.write(Sidecar::App, &copied)?;
                self.line = copied;
                self.human += 1;
            }
            _ => {
                self.line = copied;
                self.write_locator(identity, &span, deflated)?;
                self.machine += 1;
            }
        }
        self.copied_forward += 1;
        Ok(())
    }

    /// The cursors released, the last locator block written, and the run's counts, blocks and spill files.
    pub fn close(self) -> Raised<SidecarRun> {
        let (blocks, rows) = self.locator.close()?;
        Ok(SidecarRun {
            human: self.human,
            machine: self.machine,
            copied_forward: self.copied_forward,
            blocks,
            index: self.index.finish()?,
            app: self.app.finish()?,
            rows,
        })
    }
}

// --- the app sidecars ---------------------------------------------------------------------------

/// The suffix a sidecar is staged under until all three are written (`app_index.write_app_artifacts_lines`).
const STAGING_SUFFIX: &str = ".partial";

/// The names the app sidecars' writer reads from the build input's vocabulary: the three files and their formats, the locator's block size its header records, and the names the manifest's identity is taken by.
#[derive(Clone, Debug)]
pub struct AppNames {
    pub app_index_name: String,
    pub app_index_format: String,
    pub locator_name: String,
    pub locator_format: String,
    pub locator_rows_name: String,
    pub block_rows: i64,
    pub scan: ScanNames,
}

impl AppNames {
    /// The names as the build input's vocabulary gives them.
    pub fn of(input: &BuildInput) -> WireResult<Self> {
        let route = RouteNames::of(input)?;
        let sidecars = SidecarNames::of(input)?;
        Ok(Self {
            app_index_name: route.app_index_name,
            app_index_format: route.app_index_format,
            locator_name: route.locator_name,
            locator_format: route.locator_format,
            locator_rows_name: route.locator_rows_name,
            block_rows: sidecars.block_rows as i64,
            scan: route.scan,
        })
    }
}

/// One run's locator blocks as the app writer takes them: the bytes of their gzip members, which the rows file takes as they are, and their table entries, whose `byte_start` counts from the run's first member.
pub struct LocatorRun<'a> {
    pub members: Box<dyn Read + 'a>,
    pub blocks: &'a [LocatorBlock],
}

/// `app_index.header`: a sidecar's header record in `format`, stamped with the identity of the manifest beside it (`unit_index.manifest_sha256`) and carrying its `generated_at`, read as `header` reads the manifest, as UTF-8 text.
fn app_header(corpus: &Path, format: &str, names: &AppNames) -> Raised<Vec<(PyValue, PyValue)>> {
    let raw = std::fs::read(corpus.join("manifest.json"))
        .map_err(|failure| SidecarError::os(&failure))?;
    let text = std::str::from_utf8(&raw)
        .map_err(|failure| SidecarError::new("UnicodeDecodeError", failure.to_string()))?;
    let manifest = json::loads(text).map_err(|failure| {
        let class = if json::unheld(&failure) {
            "Unheld"
        } else {
            "JSONDecodeError"
        };
        SidecarError::new(class, failure.0)
    })?;
    let generated_at = get(&manifest, "generated_at")?;
    let identity = match manifest_and_identity(corpus, &names.scan) {
        Ok(Some((_, identity))) => identity,
        Ok(None) => {
            return Err(SidecarError::new(
                "OSError",
                format!("cannot read {}", corpus.join("manifest.json").display()),
            ));
        }
        Err(ScanError::Unheld(why)) => return Err(SidecarError::new("Unheld", why)),
        Err(failure) => return Err(SidecarError::new("Exception", failure.to_string())),
    };
    Ok(vec![
        (PyValue::str("format"), PyValue::str(format)),
        (PyValue::str("manifest_sha256"), PyValue::Str(identity)),
        (PyValue::str("generated_at"), generated_at),
    ])
}

/// A header record with `extra` after its stamps, as `{**header(corpus, fmt), …}` builds it.
fn header_with(stamps: &[(PyValue, PyValue)], extra: Vec<(&str, PyValue)>) -> PyValue {
    let mut entries = stamps.to_vec();
    entries.extend(
        extra
            .into_iter()
            .map(|(key, value)| (PyValue::str(key), value)),
    );
    PyValue::Dict(entries)
}

/// A gzip stream at the app sidecars' level, mtime 0, staged at `path` and named by its staging file's name, as `gzip.GzipFile(path, mode="wb", mtime=0, compresslevel=COMPRESS_LEVEL)` opens one.
fn app_stream(path: &Path) -> Raised<GzipWriter<BufWriter<File>>> {
    let file = File::create(path).map_err(|failure| SidecarError::os(&failure))?;
    GzipWriter::new(BufWriter::new(file), &names_of(path), APP_LEVEL, MTIME)
        .map_err(|failure| SidecarError::os(&failure))
}

/// End a gzip stream and flush its file.
fn close_stream(stream: GzipWriter<BufWriter<File>>) -> Raised<()> {
    stream
        .finish()
        .and_then(|mut file| file.flush())
        .map_err(|failure| SidecarError::os(&failure))
}

/// `app_index.write_app_artifacts_lines`: write the three app sidecars under `corpus` from the app index's already-projected lines, read from `app` in the order they arrive, and the locator's blocks, already cut and compressed, one run at a time. Each file is staged under its name and [`STAGING_SUFFIX`]: the app index, its header (`human` its units) then its lines, one gzip stream; the rows file, each run's members as they are, each run's table entries moved to count from the rows file's start; the table, its header (`machine` its units, the blocks, their size and the rows file's length) then a line a block, one gzip stream; the three renamed back to back, the app index, the rows and the table, once all three are written. Whatever happens, no staged file is left. Run once the manifest is on disk, since each header carries its identity.
pub fn write_app_artifacts_lines<'a>(
    corpus: &Path,
    app: &mut dyn Read,
    locator_runs: &mut dyn Iterator<Item = Raised<LocatorRun<'a>>>,
    human: i64,
    machine: i64,
    names: &AppNames,
) -> Raised<()> {
    let paths = [
        corpus.join(&names.app_index_name),
        corpus.join(&names.locator_name),
        corpus.join(&names.locator_rows_name),
    ];
    let staged = paths
        .clone()
        .map(|path| path.with_file_name(format!("{}{STAGING_SUFFIX}", names_of(&path))));
    let written = (|| -> Raised<()> {
        let mut index = app_stream(&staged[0])?;
        let stamps = app_header(corpus, &names.app_index_format, names)?;
        index
            .write_all(&line_of(&header_with(
                &stamps,
                vec![("units", PyValue::Int(human))],
            ))?)
            .map_err(|failure| SidecarError::os(&failure))?;
        io::copy(app, &mut index).map_err(|failure| SidecarError::os(&failure))?;
        close_stream(index)?;
        let mut blocks = Vec::new();
        let mut rows =
            BufWriter::new(File::create(&staged[2]).map_err(|failure| SidecarError::os(&failure))?);
        let mut rows_bytes = 0i64;
        for run in locator_runs {
            let LocatorRun {
                mut members,
                blocks: entries,
            } = run?;
            let base = rows_bytes;
            rows_bytes += io::copy(&mut members, &mut rows)
                .map_err(|failure| SidecarError::os(&failure))? as i64;
            blocks.extend(entries.iter().map(|entry| entry.value(base)));
        }
        rows.into_inner()
            .map_err(|failure| SidecarError::os(failure.error()))?;
        let mut locator = app_stream(&staged[1])?;
        let stamps = app_header(corpus, &names.locator_format, names)?;
        let header = header_with(
            &stamps,
            vec![
                ("units", PyValue::Int(machine)),
                ("blocks", PyValue::Int(blocks.len() as i64)),
                ("block_rows", PyValue::Int(names.block_rows)),
                ("rows_bytes", PyValue::Int(rows_bytes)),
            ],
        );
        locator
            .write_all(&line_of(&header)?)
            .map_err(|failure| SidecarError::os(&failure))?;
        for block in &blocks {
            locator
                .write_all(&line_of(block)?)
                .map_err(|failure| SidecarError::os(&failure))?;
        }
        close_stream(locator)?;
        for (from, to) in [
            (&staged[0], &paths[0]),
            (&staged[2], &paths[2]),
            (&staged[1], &paths[1]),
        ] {
            std::fs::rename(from, to).map_err(|failure| SidecarError::os(&failure))?;
        }
        Ok(())
    })();
    for path in &staged {
        match std::fs::remove_file(path) {
            Err(failure) if failure.kind() != io::ErrorKind::NotFound && written.is_ok() => {
                return Err(SidecarError::os(&failure));
            }
            _ => {}
        }
    }
    written
}

/// A path's file name as text.
fn names_of(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// The bytes of one spill file of each run in turn, run after run in shard order.
struct SpilledRuns<'a> {
    spills: std::vec::IntoIter<&'a Spilled>,
    reading: Option<io::Take<PartReader<'a>>>,
}

impl<'a> SpilledRuns<'a> {
    fn new(spills: Vec<&'a Spilled>) -> Self {
        Self {
            spills: spills.into_iter(),
            reading: None,
        }
    }
}

impl Read for SpilledRuns<'_> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if buffer.is_empty() {
            return Ok(0);
        }
        loop {
            if self.reading.is_none() {
                let Some(spill) = self.spills.next() else {
                    return Ok(0);
                };
                let length = u64::try_from(spill.length).unwrap_or_default();
                self.reading = Some(PartReader::new(&spill.file).take(length));
            }
            let read = self
                .reading
                .as_mut()
                .expect("a spill being read")
                .read(buffer)?;
            if read > 0 {
                return Ok(read);
            }
            self.reading = None;
        }
    }
}

/// Write the units index ([`write_index_lines`] over the runs' spilled index lines, under the pass's fold `claim`) on this thread and the three app sidecars ([`write_app_artifacts_lines`] over the runs' spilled app-index lines and locator blocks) on a thread of their own, named `corpus-app-sidecars_0`, side by side, and return what the index's write did. A writer that fails fails here once the other has finished, the index's first. Run once the manifest is on disk.
pub fn write_sidecars(
    corpus: &Path,
    runs: &[SidecarRun],
    index_names: &IndexNames,
    app_names: &AppNames,
    claim: &FoldClaim,
    sink: &Sink,
) -> std::result::Result<IndexWrite, BuildError> {
    let human = runs.iter().map(|run| run.human).sum();
    let machine = runs.iter().map(|run| run.machine).sum();
    std::thread::scope(|scope| {
        let app = std::thread::Builder::new()
            .name("corpus-app-sidecars_0".to_owned())
            .spawn_scoped(scope, || {
                let mut app = SpilledRuns::new(runs.iter().map(|run| &run.app).collect());
                let mut locator = runs.iter().map(|run| {
                    let length = u64::try_from(run.rows.length).unwrap_or_default();
                    Ok(LocatorRun {
                        members: Box::new(PartReader::new(&run.rows.file).take(length)),
                        blocks: &run.blocks,
                    })
                });
                write_app_artifacts_lines(corpus, &mut app, &mut locator, human, machine, app_names)
            })
            .map_err(|failure| {
                BuildError::Failed(format!("cannot start the app sidecars' writer: {failure}"))
            })?;
        let index = write_index_lines(
            corpus,
            &mut SpilledLines::new(runs),
            index_names,
            claim,
            sink,
        );
        let app = app
            .join()
            .unwrap_or_else(|panic| std::panic::resume_unwind(panic));
        let index = index?;
        app?;
        Ok(index)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::corpus::gzip::tests::read_gzip;
    use crate::corpus::scan::manifest_identity;
    use std::path::PathBuf;

    fn fragment() -> PyValue {
        dict(vec![
            ("id", PyValue::str("u-1")),
            ("class", PyValue::str("A")),
            ("summary", PyValue::str("·Pea \u{7f}\n")),
            ("render_groups", PyValue::List(vec![PyValue::Int(1)])),
            (
                "drafts",
                dict(vec![(
                    "policy",
                    dict(vec![
                        ("file", PyValue::str("f")),
                        ("keypath", PyValue::str("k")),
                    ]),
                )]),
            ),
        ])
    }

    const SPAN: Span = Span {
        part: 2,
        start: 10,
        length: 20,
        inflated: 30,
    };

    #[test]
    fn an_index_line_writes_text_as_itself_and_counts_render_groups() {
        let line = index_line(&fragment(), Some(3), Some(1)).expect("projected");
        let text = String::from_utf8(line).expect("utf-8");
        assert!(
            text.starts_with("{\"id\": \"u-1\", \"order\": 3, \"batch\": 1, \"class\": \"A\", ")
        );
        assert!(text.contains("\"summary\": \"·Pea \u{7f}\\n\""));
        assert!(text.contains("\"render_groups\": 1, "));
        assert!(text.ends_with(
            "\"policy\": {\"file\": \"f\", \"keypath\": \"k\", \"suggested_record\": null}}\n"
        ));
    }

    #[test]
    fn a_copied_line_is_the_line_projected_at_the_new_place_and_address() {
        let slimmed = vec!["ink_identical".to_owned(), NO_VERDICT.to_owned()];
        let index = index_line(&fragment(), Some(0), Some(0)).expect("projected");
        assert_eq!(
            copy_forward_index_line(&index, "u-1", Some(7), Some(2)).expect("copied"),
            index_line(&fragment(), Some(7), Some(2)).expect("projected")
        );
        let plain = Span {
            inflated: 0,
            ..SPAN
        };
        let row = app_line(&fragment(), &plain, Some(0), Some(0), false, &slimmed).expect("row");
        assert_eq!(
            copy_forward_app_line(&row, "u-1", 7, 2, &SPAN, true).expect("copied"),
            app_line(&fragment(), &SPAN, Some(7), Some(2), true, &slimmed).expect("row")
        );
        assert_eq!(
            copy_forward_index_line(b"{\"id\": \"u-1\"}\n", "u-1", None, None)
                .expect_err("no class")
                .class,
            "ValueError"
        );
    }

    #[test]
    fn a_slimmed_fragment_has_no_app_row() {
        let slimmed = vec!["ink_identical".to_owned()];
        let PyValue::Dict(mut entries) = fragment() else {
            unreachable!()
        };
        entries.push((PyValue::str("ink_identical"), PyValue::Int(1)));
        let failure = app_row(
            &PyValue::Dict(entries),
            &SPAN,
            Some(0),
            Some(0),
            false,
            &slimmed,
        )
        .expect_err("slimmed");
        assert_eq!(failure.class, "AssertionError");
    }

    #[test]
    fn a_locator_line_written_in_place_is_the_projected_line_with_the_values_it_reads_back_as() {
        let ids = [
            PyValue::str("u-1"),
            PyValue::str("\u{e9}\"\\\n\u{1}\u{7f}\u{2028}"),
            PyValue::Int(-9_223_372_036_854_775_808),
            PyValue::Int(0),
            PyValue::Bool(true),
            PyValue::None,
        ];
        let classes = [PyValue::str("A"), PyValue::None, PyValue::Int(42)];
        let mut text = String::new();
        for id in &ids {
            for class in &classes {
                for deflated in [false, true] {
                    let fragment = dict(vec![("class", class.clone()), ("id", id.clone())]);
                    let mut line = b"kept".to_vec();
                    let values =
                        push_locator_line(&mut line, &mut text, &fragment, &SPAN, deflated)
                            .expect("written");
                    let projected = locator_line(&fragment, &SPAN, deflated).expect("projected");
                    assert_eq!(&line[4..], &projected[..]);
                    let row = json::loads_bytes(&projected).expect("read back");
                    let read = (
                        subscript(&row, "id").expect("id"),
                        get(&row, "class").expect("class"),
                    );
                    assert_eq!(values, Some(read));
                }
            }
        }
        let listed = dict(vec![("id", PyValue::List(Vec::new()))]);
        let mut line = Vec::new();
        assert_eq!(
            push_locator_line(&mut line, &mut text, &listed, &SPAN, false).expect("unwritten"),
            None
        );
        assert!(line.is_empty());
        assert_eq!(
            push_locator_line(&mut line, &mut text, &dict(Vec::new()), &SPAN, false)
                .expect_err("no id")
                .class,
            "KeyError"
        );
        let mut head = Vec::new();
        push_line_head(&mut head, &mut text, "\u{e9}", Some(-3), None);
        assert_eq!(
            head,
            "{\"id\": \"\u{e9}\", \"order\": -3, \"batch\": null".as_bytes()
        );
    }

    #[test]
    fn blocks_close_at_their_size_and_at_a_class_change_and_refuse_ids_that_fall() {
        let spill = Spill::new(&std::env::temp_dir()).expect("spill");
        let mut blocks = LocatorBlocks::new(spill, 2);
        let row = |id: &str, class: &str| {
            locator_line(
                &dict(vec![
                    ("id", PyValue::str(id)),
                    ("class", PyValue::str(class)),
                ]),
                &SPAN,
                false,
            )
            .expect("row")
        };
        for (id, class) in [("u-1", "A"), ("u-2", "A"), ("u-3", "A"), ("u-0", "B")] {
            blocks.add(&row(id, class)).expect("added");
        }
        let failure = blocks.add(&row("u-0", "B")).expect_err("repeated");
        assert_eq!(
            failure.message,
            "u-0 follows u-0 in B: a class's rows must ascend"
        );
        let (entries, rows) = blocks.close().expect("closed");
        assert_eq!(
            entries.iter().map(|block| block.units).collect::<Vec<_>>(),
            vec![2, 1, 1]
        );
        assert_eq!(
            entries.last().map(|block| block.start + block.length),
            Some(rows.length)
        );
    }

    fn app_names() -> AppNames {
        let text = |value: &str| value.to_owned();
        AppNames {
            app_index_name: text("app-units.ndjson.gz"),
            app_index_format: text("ams-review-app-index/1"),
            locator_name: text("app-locator.ndjson.gz"),
            locator_format: text("ams-review-app-locator/2"),
            locator_rows_name: text("app-locator-rows.ndjson.gz"),
            block_rows: 1024,
            scan: ScanNames {
                store_name: text("unit-cache.ndjson.gz"),
                store_format: text("s"),
                index_name: text("units-index.ndjson.gz"),
                index_format: text("i"),
                no_store: String::new(),
                unreadable: String::new(),
                manifest_moved: String::new(),
                rederiving: Vec::new(),
                asset_components: vec![text("static")],
            },
        }
    }

    fn scratch(name: &str) -> PathBuf {
        let directory =
            std::env::temp_dir().join(format!("ams-review-app-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&directory);
        std::fs::create_dir_all(&directory).expect("scratch");
        directory
    }

    fn names_held(corpus: &Path) -> Vec<String> {
        let mut held: Vec<String> = std::fs::read_dir(corpus)
            .expect("listed")
            .map(|entry| {
                entry
                    .expect("entry")
                    .file_name()
                    .to_string_lossy()
                    .into_owned()
            })
            .collect();
        held.sort();
        held
    }

    #[test]
    fn the_app_sidecars_carry_the_manifests_stamp_and_move_each_runs_blocks_past_the_runs_before() {
        let corpus = scratch("write");
        std::fs::write(
            corpus.join("manifest.json"),
            b"{\"generated_at\": \"now\", \"inputs_fingerprint\": {\"static\": \"s\"}}\n",
        )
        .expect("manifest");
        std::fs::write(corpus.join("app-units.ndjson.gz.partial"), b"left\n").expect("left");
        let block = |start: i64, length: i64| LocatorBlock {
            class: PyValue::str("a"),
            start,
            length,
            first: PyValue::str("m1"),
            last: PyValue::str("m2"),
            units: 2,
        };
        let (first, second) = ([block(0, 3)], [block(0, 2), block(2, 4)]);
        let runs: [(&[u8], &[LocatorBlock]); 2] = [(b"abc", &first), (b"defghi", &second)];
        let mut locator = runs.iter().map(|(members, blocks)| {
            Ok(LocatorRun {
                members: Box::new(*members),
                blocks,
            })
        });
        let lines = "{\"id\": \"·u\"}\n".as_bytes();
        write_app_artifacts_lines(&corpus, &mut &lines[..], &mut locator, 1, 4, &app_names())
            .expect("written");
        assert_eq!(
            names_held(&corpus),
            [
                "app-locator-rows.ndjson.gz",
                "app-locator.ndjson.gz",
                "app-units.ndjson.gz",
                "manifest.json"
            ]
        );
        assert_eq!(
            std::fs::read(corpus.join("app-locator-rows.ndjson.gz")).expect("rows"),
            b"abcdefghi"
        );
        let identity = manifest_identity(
            &json::loads("{\"generated_at\": \"now\", \"inputs_fingerprint\": {}}")
                .expect("parsed"),
            &[],
        )
        .expect("identity");
        let read = |name: &str| {
            let data = std::fs::read(corpus.join(name)).expect("read");
            String::from_utf8(read_gzip(&data).expect("gzip")).expect("text")
        };
        assert_eq!(
            read("app-units.ndjson.gz"),
            format!(
                "{{\"format\": \"ams-review-app-index/1\", \"manifest_sha256\": \"{identity}\", \"generated_at\": \"now\", \"units\": 1}}\n{{\"id\": \"·u\"}}\n"
            )
        );
        let table = read("app-locator.ndjson.gz");
        let mut table_lines = table.lines();
        assert_eq!(
            table_lines.next(),
            Some(format!("{{\"format\": \"ams-review-app-locator/2\", \"manifest_sha256\": \"{identity}\", \"generated_at\": \"now\", \"units\": 4, \"blocks\": 3, \"block_rows\": 1024, \"rows_bytes\": 9}}").as_str())
        );
        let starts: Vec<&str> = table_lines
            .map(|line| &line[line.find("\"byte_start\": ").expect("start")..])
            .map(|tail| &tail[..tail.find(',').expect("comma")])
            .collect();
        assert_eq!(
            starts,
            [
                "\"byte_start\": 0",
                "\"byte_start\": 3",
                "\"byte_start\": 5"
            ]
        );
        std::fs::remove_dir_all(&corpus).expect("removed");
    }

    #[test]
    fn a_manifest_that_is_not_an_object_writes_nothing_and_leaves_no_staging_file() {
        let corpus = scratch("refused");
        std::fs::write(corpus.join("manifest.json"), b"[]\n").expect("manifest");
        let failure = write_app_artifacts_lines(
            &corpus,
            &mut &b""[..],
            &mut std::iter::empty(),
            0,
            0,
            &app_names(),
        )
        .expect_err("refused");
        assert_eq!(
            (failure.class.as_str(), failure.message.as_str()),
            ("AttributeError", "'list' object has no attribute 'get'")
        );
        assert_eq!(names_held(&corpus), ["manifest.json"]);
        std::fs::remove_dir_all(&corpus).expect("removed");
    }
}
