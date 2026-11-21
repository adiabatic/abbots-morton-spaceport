//! Each run's sidecar lines as the write projects them, as `build._SidecarLines` does: a unit's units-index line, and its app-index row or, outside the triage index, its locator row ([`SidecarLines::unit`]), each carrying the span the shard writer returned and the trailing `"deflated": true` exactly when that span is a deflated fragment's; and a byte-copied unit's lines copied forward from the previous corpus's sidecars, read in step through a cursor over each ([`LineCursor`]), with this build's place and address put in, or projected from its stored bytes when a cursor cannot reach it ([`SidecarLines::cached`]). The index's and the app index's lines are appended plain to spill files in the build's scratch directory, unlinked as they are made, so a run holds none of them in memory; the locator's rows are cut into blocks as they come ([`LocatorBlocks`]) and each block appended to a third spill file as the gzip member the rows file takes. The headers wait for the manifest, so the sidecars are written from the spill files once it is on disk.
//!
//! The projections are `unit_index.index_record` and `index_line`, `app_index.app_row`, `locator_row` and their lines, and the copy-forward surgery `unit_index.copy_forward_index_line` and `app_index.copy_forward_app_line` does; each fails with the class Python raises over a fragment it cannot read. [`stage_sidecar_lines`] is the `sidecar-lines` stage of the stage fixtures.

use std::fs::{File, OpenOptions};
use std::io::{self, BufRead, BufReader, BufWriter, Write};
use std::os::unix::fs::FileExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::Value;

use super::BuildError;
use super::audit::stage_line;
use super::gzip::{APP_LEVEL, GzipError, GzipReader, MTIME, compress_member, inflate_fragment};
use super::input::BuildInput;
use super::records::truthy;
use super::route::RouteNames;
use super::rowdelta::{PartReader, ReadError, py_str};
use super::scan::{Lines, OpenIndex, ScanNames, Thrown, open_index};
use super::shards::Span;
use super::signatures::dump_prior;
use crate::fragment::{lookup, py_eq, sha256_hex};
use crate::pyfmt::{PyValue, json, repr::repr_str};
use crate::wire::{Result as WireResult, WireError};

/// The directory, beside the stage file, of the bytes the `sidecar-lines` stage's lines name by sha256.
pub const STAGE_BLOBS: &str = "sidecar-lines.blobs";
/// The flag `app_index._SLIMMED_FLAGS` adds to the machine checks.
const NO_VERDICT: &str = "no_verdict";
/// The field a cursor finds a line by (`unit_index.LineCursor`'s `field`).
const ID_FIELD: &str = "id";
/// `unit_index.CLASS_SEAM`, where an index line's place ends.
const CLASS_SEAM: &[u8] = b", \"class\": ";
/// Where an app-index row's address starts (`app_index.copy_forward_app_line`).
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

/// The names the sidecar lines read from the build input's vocabulary: the three sidecars' files, the locator's block size, the flags no app-index row may carry (`app_index._SLIMMED_FLAGS`, the machine checks and `no_verdict`), and the names the units index is read by.
#[derive(Clone, Debug)]
pub struct SidecarNames {
    pub index_name: String,
    pub app_index_name: String,
    pub locator_rows_name: String,
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
            locator_rows_name: route.locator_rows_name,
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
            format!("'{}' object has no attribute 'get'", other.kind()),
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
                value.kind()
            ),
        )),
        other => Err(SidecarError::new(
            "TypeError",
            format!("'{}' object is not subscriptable", other.kind()),
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
                format!("object of type '{}' has no len()", other.kind()),
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
                    left.kind(),
                    right.kind()
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

/// `unit_index.line_head`: the start of an index or app-index line, its id and place, without the closing brace.
pub fn line_head(unit_id: &str, order: Option<i64>, batch: Option<i64>) -> Raised<Vec<u8>> {
    let mut head = unicode_text(&dict(vec![
        ("id", PyValue::str(unit_id)),
        ("order", optional_int(order)),
        ("batch", optional_int(batch)),
    ]))?;
    head.pop();
    Ok(head)
}

/// `unit_index.copy_forward_index_line`: a previous index line, a base's with its place or a segment's with none, with this build's id and place joined to its tail from the class on.
pub fn copy_forward_index_line(
    line: &[u8],
    unit_id: &str,
    order: Option<i64>,
    batch: Option<i64>,
) -> Raised<Vec<u8>> {
    let mut copied = line_head(unit_id, order, batch)?;
    let at = find(line, CLASS_SEAM).ok_or_else(not_found)?;
    copied.extend_from_slice(&line[at..]);
    Ok(copied)
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

/// `app_index.copy_forward_app_line`: a previous app-index row with this build's id and place at its head and this build's address and mark at its tail, the fields between them its own.
pub fn copy_forward_app_line(
    line: &[u8],
    unit_id: &str,
    order: i64,
    batch: i64,
    span: &Span,
    deflated: bool,
) -> Raised<Vec<u8>> {
    let start = find(line, b", \"class\": ").ok_or_else(not_found)?;
    let end = rfind(line, SHARD_PART_SEAM).ok_or_else(not_found)?;
    let middle = line.get(start..end).unwrap_or_default();
    let tail = json::dumps(&with_mark(
        vec![
            ("shard_part", PyValue::Int(span.part as i64)),
            ("byte_start", PyValue::Int(span.start)),
            ("byte_length", PyValue::Int(span.length)),
        ],
        deflated,
    ))
    .map_err(|failure| SidecarError::new("Unwritten", failure.0))?;
    let mut copied = line_head(unit_id, Some(order), Some(batch))?;
    copied.extend_from_slice(middle);
    copied.extend_from_slice(b", ");
    copied.extend_from_slice(&tail.as_bytes()[1..]);
    copied.push(b'\n');
    Ok(copied)
}

// --- spill files --------------------------------------------------------------------------------

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
            writer: BufWriter::new(file),
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

impl Spilled {
    /// Every byte the spill file holds.
    pub fn bytes(&self) -> io::Result<Vec<u8>> {
        let mut data = vec![0; usize::try_from(self.length).unwrap_or_default()];
        self.file.read_exact_at(&mut data, 0)?;
        Ok(data)
    }
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

/// A gzip file's lines past its header line, read as iterating a `gzip.GzipFile` reads them.
struct GzipFileLines<'a> {
    reader: BufReader<GzipReader<PartReader<'a>>>,
    done: bool,
}

impl<'a> GzipFileLines<'a> {
    fn new(file: &'a File) -> Self {
        Self {
            reader: BufReader::new(GzipReader::new(PartReader::new(file))),
            done: false,
        }
    }
}

impl Iterator for GzipFileLines<'_> {
    type Item = std::result::Result<Vec<u8>, ReadError>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        let mut line = Vec::new();
        match self.reader.read_until(b'\n', &mut line) {
            Ok(0) => {
                self.done = true;
                None
            }
            Ok(_) => Some(Ok(line)),
            Err(failure) => {
                self.done = true;
                Some(Err(ReadError::of_io(&failure)))
            }
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

    /// `LineCursor.over_index`: a cursor over the units index's record lines, a base's and its segments' merged by key.
    pub fn index_cursor(&self, names: &SidecarNames) -> LineCursor<'_> {
        LineCursor {
            lines: self
                .index
                .as_ref()
                .map(|opened| opened.lines(&names.index_name)),
        }
    }

    /// `LineCursor` over the app index's file: its header line read now, a file whose header does not read leaving the cursor closed, but for deflate data that does not inflate, which fails.
    pub fn app_cursor(&self) -> Raised<LineCursor<'_>> {
        let Some(file) = &self.app else {
            return Ok(LineCursor { lines: None });
        };
        let mut lines = GzipFileLines::new(file);
        Ok(LineCursor {
            lines: match lines.next() {
                Some(Ok(_)) => Some(Box::new(lines)),
                None => None,
                Some(Err(ReadError::Gzip(GzipError::Zlib(why)))) => {
                    return Err(SidecarError::new("error", why));
                }
                Some(Err(_)) => None,
            },
        })
    }
}

/// `unit_index.LineCursor`: a forward-only reader over a sidecar's lines that returns the line whose leading id is the one asked for, skipping every line before it. Once an id is not found the cursor is closed, and every later request returns None. A read that fails fails the request.
pub struct LineCursor<'a> {
    lines: Option<Lines<'a, Vec<u8>>>,
}

impl LineCursor<'_> {
    /// Whether the cursor has lines to read.
    pub fn is_open(&self) -> bool {
        self.lines.is_some()
    }

    pub fn take(&mut self, value: &str) -> Raised<Option<Vec<u8>>> {
        let Some(lines) = self.lines.as_mut() else {
            return Ok(None);
        };
        let quoted = |text: &str| {
            json::dumps(&PyValue::str(text))
                .map_err(|failure| SidecarError::new("Unwritten", failure.0))
        };
        let prefix = format!("{{{}: {}, ", quoted(ID_FIELD)?, quoted(value)?).into_bytes();
        loop {
            match lines.next() {
                Some(Ok(line)) if line.starts_with(&prefix) => return Ok(Some(line)),
                Some(Ok(_)) => {}
                Some(Err(failure)) => {
                    self.lines = None;
                    return Err(failure.into());
                }
                None => {
                    self.lines = None;
                    return Ok(None);
                }
            }
        }
    }
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

/// What a run did, in order, as the `sidecar-lines` stage follows it: each cursor's take with the id and the line it returned, and each line written with the sidecar it went to.
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

/// `build._SidecarLines`: one run's sidecar lines, in shard order, spilled as they are projected or copied forward. With `trace` set, every take and write is kept there in order.
pub struct SidecarLines<'a> {
    names: &'a SidecarNames,
    index: Spill,
    app: Spill,
    locator: LocatorBlocks,
    index_cursor: Option<LineCursor<'a>>,
    app_cursor: Option<LineCursor<'a>>,
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
            human: 0,
            machine: 0,
            copied_forward: 0,
            trace: None,
        })
    }

    /// Whether each cursor, the units index's and the app index's, is open to lines.
    pub fn cursors_open(&self) -> (bool, bool) {
        let open =
            |cursor: &Option<LineCursor<'_>>| cursor.as_ref().is_some_and(LineCursor::is_open);
        (open(&self.index_cursor), open(&self.app_cursor))
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

    fn take(&mut self, which: Cursor, unit_id: &str) -> Raised<Option<Vec<u8>>> {
        let cursor = match which {
            Cursor::Index => &mut self.index_cursor,
            Cursor::App => &mut self.app_cursor,
        };
        let Some(cursor) = cursor else {
            return Ok(None);
        };
        let line = cursor.take(unit_id)?;
        if let Some(trace) = &mut self.trace {
            trace.push(Event::Take(which, unit_id.to_owned(), line.clone()));
        }
        Ok(line)
    }

    /// `_SidecarLines.unit`: a fragment's index line, and its app-index row when it has a place in the triage index, else its locator row, at `span`.
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
            let line = locator_line(fragment, &span, deflated)?;
            self.write(Sidecar::Locator, &line)?;
            self.machine += 1;
        }
        Ok(())
    }

    /// `_SidecarLines.cached`: a byte-copied unit's lines copied forward from the previous sidecars, its index line and, with a place, its app-index row, each taken from its cursor; a unit a cursor cannot reach is projected from its stored bytes, inflated to `inflated` bytes when it is deflated, as [`SidecarLines::unit`] projects a fragment. Its locator row is made from `identity`.
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
        let mut row = None;
        if line.is_some() && order.is_some() && batch.is_some() && self.app_cursor.is_some() {
            row = self.take(Cursor::App, unit_id)?;
        }
        let Some(line) = line.filter(|_| order.is_none() || row.is_some()) else {
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
        };
        let copied = copy_forward_index_line(&line, unit_id, order, batch)?;
        self.write(Sidecar::Index, &copied)?;
        let deflated = span.inflated != 0;
        match (order, batch, row) {
            (Some(order), Some(batch), Some(row)) => {
                let copied = copy_forward_app_line(&row, unit_id, order, batch, &span, deflated)?;
                self.write(Sidecar::App, &copied)?;
                self.human += 1;
            }
            _ => {
                let line = locator_line(identity, &span, deflated)?;
                self.write(Sidecar::Locator, &line)?;
                self.machine += 1;
            }
        }
        self.copied_forward += 1;
        Ok(())
    }

    /// `_SidecarLines.close`: the cursors released, the last locator block written, and the run's counts, blocks and spill files.
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

// --- the stage ----------------------------------------------------------------------------------

fn failure(key: &str, message: impl std::fmt::Display) -> WireError {
    WireError(format!("{key}: {message}"))
}

fn field(line: &Value, key: &str, name: &str) -> WireResult<PyValue> {
    PyValue::from_typed(&line[name]).map_err(|message| failure(key, message))
}

fn int_field(line: &Value, key: &str, name: &str) -> WireResult<Option<i64>> {
    match field(line, key, name)? {
        PyValue::None => Ok(None),
        PyValue::Int(value) => Ok(Some(value)),
        other => Err(failure(
            key,
            format!("{name} is {}, not an int", other.kind()),
        )),
    }
}

fn span_field(line: &Value, key: &str) -> WireResult<Span> {
    let span = field(line, key, "span")?;
    let (PyValue::Tuple(items) | PyValue::List(items)) = &span else {
        return Err(failure(key, "the span is not a tuple"));
    };
    let ints: Vec<i64> = items
        .iter()
        .map(|item| match item {
            PyValue::Int(value) => Ok(*value),
            other => Err(failure(key, format!("the span holds {}", other.kind()))),
        })
        .collect::<WireResult<_>>()?;
    let [part, start, length, inflated] = ints[..] else {
        return Err(failure(key, "the span is not four ints"));
    };
    Ok(Span {
        part: usize::try_from(part).map_err(|_| failure(key, "the span's part is negative"))?,
        start,
        length,
        inflated,
    })
}

fn str_field(line: &Value, key: &str, name: &str) -> WireResult<String> {
    match field(line, key, name)? {
        PyValue::Str(text) => Ok(text),
        other => Err(failure(
            key,
            format!("{name} is {}, not a str", other.kind()),
        )),
    }
}

fn raised(failure: &SidecarError) -> PyValue {
    dict(vec![("raised", PyValue::str(&failure.class))])
}

fn digest(data: &[u8]) -> PyValue {
    PyValue::Tuple(vec![
        PyValue::Int(data.len() as i64),
        PyValue::str(sha256_hex(data)),
    ])
}

fn line_outcome(outcome: Raised<Vec<u8>>) -> PyValue {
    match outcome {
        Ok(line) => PyValue::Bytes(line),
        Err(failure) => raised(&failure),
    }
}

fn blob(blobs: &Path, digest: &str) -> WireResult<Vec<u8>> {
    std::fs::read(blobs.join(digest))
        .map_err(|why| WireError(format!("cannot read the blob {digest}: {why}")))
}

/// A directory the stage makes beside its file and removes when it is dropped.
struct Scratch(PathBuf);

impl Scratch {
    fn new(stage_file: &Path, what: &str) -> WireResult<Self> {
        let directory = stage_file.with_file_name(format!(
            ".sidecar-lines-{what}-{}-{}",
            std::process::id(),
            SPILLS.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&directory)
            .map_err(|why| WireError(format!("cannot make {}: {why}", directory.display())))?;
        Ok(Self(directory))
    }

    /// The scratch directory holding each file a corpus names by its blob.
    fn holding(stage_file: &Path, blobs: &Path, corpus: &PyValue, key: &str) -> WireResult<Self> {
        let scratch = Self::new(stage_file, "corpus")?;
        let PyValue::Dict(files) = corpus else {
            return Err(failure(key, "the corpus is not a dict"));
        };
        for (name, sha) in files {
            let (PyValue::Str(name), PyValue::Str(sha)) = (name, sha) else {
                return Err(failure(key, "the corpus names a file by what is not a str"));
            };
            std::fs::write(scratch.0.join(name), blob(blobs, sha)?)
                .map_err(|why| failure(key, format!("cannot write {name}: {why}")))?;
        }
        Ok(scratch)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn event_value(event: &Event, names: &SidecarNames) -> PyValue {
    match event {
        Event::Take(cursor, value, line) => PyValue::Tuple(vec![
            PyValue::str("take"),
            PyValue::str(match cursor {
                Cursor::Index => "index",
                Cursor::App => "app",
            }),
            PyValue::str(value),
            line.as_deref().map_or(PyValue::None, digest),
        ]),
        Event::Write(sidecar, line) => {
            let name = match sidecar {
                Sidecar::Index => &names.index_name,
                Sidecar::App => &names.app_index_name,
                Sidecar::Locator => &names.locator_rows_name,
            };
            match digest(line) {
                PyValue::Tuple(mut parts) => {
                    parts.insert(0, PyValue::str(name));
                    parts.insert(0, PyValue::str("write"));
                    PyValue::Tuple(parts)
                }
                other => other,
            }
        }
    }
}

/// Make one recorded call on `lines`: a `unit` call with its fragment read from its blob, or a `cached` call with its stored bytes read from theirs.
fn call(
    lines: &mut SidecarLines<'_>,
    line: &Value,
    key: &str,
    blobs: &Path,
) -> WireResult<Raised<()>> {
    let span = span_field(line, key)?;
    let order = int_field(line, key, "order")?;
    let batch = int_field(line, key, "batch")?;
    match str_field(line, key, "call")?.as_str() {
        "unit" => {
            let text = blob(blobs, &str_field(line, key, "fragment")?)?;
            let typed: Value = serde_json::from_slice(&text)
                .map_err(|why| failure(key, format!("the fragment's blob is not JSON: {why}")))?;
            let fragment = PyValue::from_typed(&typed).map_err(|why| failure(key, why))?;
            Ok(lines.unit(&fragment, span, order, batch))
        }
        "cached" => {
            let body = blob(blobs, &str_field(line, key, "body")?)?;
            let inflated = int_field(line, key, "inflated")?.unwrap_or_default();
            let identity = field(line, key, "identity")?;
            let unit_id = str_field(line, key, "unit_id")?;
            Ok(lines.cached(&unit_id, &body, inflated, &identity, span, order, batch))
        }
        other => Err(failure(
            key,
            format!("the call {other} is not unit or cached"),
        )),
    }
}

/// Replay one run's lines, its `open`, its calls and its `close`, over the dump's prior corpus or the run's own corpus made again from the blobs, and answer each.
fn replay(
    names: &SidecarNames,
    stage_file: &Path,
    blobs: &Path,
    run: &[(String, Value)],
    out: &mut impl Write,
) -> WireResult<()> {
    let (open_key, open) = &run[0];
    let copy_forward = matches!(field(open, open_key, "copy_forward")?, PyValue::Bool(true));
    let corpus = field(open, open_key, "corpus")?;
    let held = match corpus {
        PyValue::None => None,
        corpus => Some(Scratch::holding(stage_file, blobs, &corpus, open_key)?),
    };
    let directory = match &held {
        Some(held) => held.0.clone(),
        None => dump_prior(stage_file)?,
    };
    let spills = Scratch::new(stage_file, "spill")?;
    let opened = |why: SidecarError| failure(open_key, format!("the run does not open: {why}"));
    let prior = PriorSidecars::open(&directory, copy_forward, names).map_err(opened)?;
    let mut lines = SidecarLines::new(&prior, &spills.0, names).map_err(opened)?;
    lines.trace = Some(Vec::new());
    let (index_open, app_open) = lines.cursors_open();
    stage_line(
        out,
        open_key,
        &PyValue::Tuple(vec![PyValue::Bool(index_open), PyValue::Bool(app_open)]),
    )?;
    for (key, line) in &run[1..] {
        if key.ends_with(":close") {
            let closed = lines
                .close()
                .map_err(|why| failure(key, format!("the run does not close: {why}")))?;
            let spilled = |spilled: &Spilled| {
                spilled
                    .bytes()
                    .map(|data| digest(&data))
                    .map_err(|why| failure(key, format!("cannot read a spill file: {why}")))
            };
            let value = dict(vec![
                ("human", PyValue::Int(closed.human)),
                ("machine", PyValue::Int(closed.machine)),
                ("copied_forward", PyValue::Int(closed.copied_forward)),
                (
                    "blocks",
                    PyValue::List(closed.blocks.iter().map(|block| block.value(0)).collect()),
                ),
                ("index", spilled(&closed.index)?),
                ("app", spilled(&closed.app)?),
                ("rows", spilled(&closed.rows)?),
            ]);
            return stage_line(out, key, &value);
        }
        let outcome = call(&mut lines, line, key, blobs)?;
        let events = lines.trace.replace(Vec::new()).unwrap_or_default();
        let value = match &outcome {
            Ok(()) => PyValue::Tuple(vec![
                PyValue::List(
                    events
                        .iter()
                        .map(|event| event_value(event, names))
                        .collect(),
                ),
                PyValue::Tuple(vec![
                    PyValue::Int(lines.human),
                    PyValue::Int(lines.machine),
                    PyValue::Int(lines.copied_forward),
                ]),
            ]),
            Err(why) => raised(why),
        };
        stage_line(out, key, &value)?;
        if outcome.is_err() {
            break;
        }
    }
    Ok(())
}

/// A generated projection: the fragment's index line, app-index row and locator row at the line's span and place, each its bytes or `{"raised": name}`.
fn projected(names: &SidecarNames, line: &Value, key: &str) -> WireResult<PyValue> {
    let fragment = field(line, key, "fragment")?;
    let span = span_field(line, key)?;
    let order = int_field(line, key, "order")?;
    let batch = int_field(line, key, "batch")?;
    let deflated = span.inflated != 0;
    Ok(dict(vec![
        ("index", line_outcome(index_line(&fragment, order, batch))),
        (
            "app",
            line_outcome(app_line(
                &fragment,
                &span,
                order,
                batch,
                deflated,
                &names.slimmed,
            )),
        ),
        (
            "locator",
            line_outcome(locator_line(&fragment, &span, deflated)),
        ),
    ]))
}

/// A generated copy-forward: the line rewritten the units index's way and, at a place with an order and a batch, the app index's.
fn copied(line: &Value, key: &str) -> WireResult<PyValue> {
    let PyValue::Bytes(held) = field(line, key, "line")? else {
        return Err(failure(key, "the line is not bytes"));
    };
    let unit_id = str_field(line, key, "unit_id")?;
    let span = span_field(line, key)?;
    let order = int_field(line, key, "order")?;
    let batch = int_field(line, key, "batch")?;
    let mut entries = vec![(
        "index",
        line_outcome(copy_forward_index_line(&held, &unit_id, order, batch)),
    )];
    if let (Some(order), Some(batch)) = (order, batch) {
        entries.push((
            "app",
            line_outcome(copy_forward_app_line(
                &held,
                &unit_id,
                order,
                batch,
                &span,
                span.inflated != 0,
            )),
        ));
    }
    Ok(dict(entries))
}

/// A generated cursor over its corpus made again from the blobs: whether it opened to lines, and each take's line until one raises.
fn cursor(
    names: &SidecarNames,
    stage_file: &Path,
    blobs: &Path,
    line: &Value,
    key: &str,
) -> WireResult<PyValue> {
    let held = Scratch::holding(stage_file, blobs, &field(line, key, "corpus")?, key)?;
    let prior = PriorSidecars::open(&held.0, true, names)
        .map_err(|why| failure(key, format!("the corpus does not open: {why}")))?;
    let mut cursor = match str_field(line, key, "which")?.as_str() {
        "index" => prior.index_cursor(names),
        "app" => prior
            .app_cursor()
            .map_err(|why| failure(key, format!("the cursor does not open: {why}")))?,
        other => return Err(failure(key, format!("no cursor {other}"))),
    };
    let opened = cursor.is_open();
    let PyValue::List(takes) = field(line, key, "takes")? else {
        return Err(failure(key, "the takes are not a list"));
    };
    let mut results = Vec::new();
    for value in &takes {
        let PyValue::Str(value) = value else {
            return Err(failure(key, "a take is not a str"));
        };
        match cursor.take(value) {
            Ok(line) => results.push(line.map_or(PyValue::None, PyValue::Bytes)),
            Err(why) => {
                results.push(raised(&why));
                break;
            }
        }
    }
    Ok(dict(vec![
        ("opened", PyValue::Bool(opened)),
        ("results", PyValue::List(results)),
    ]))
}

/// A generated locator: the line's rows, each made a locator line at its own address, cut into blocks; the table entries and the rows' length and sha256, or the refusal and its message.
fn located(
    names: &SidecarNames,
    stage_file: &Path,
    line: &Value,
    key: &str,
) -> WireResult<PyValue> {
    let PyValue::List(rows) = field(line, key, "rows")? else {
        return Err(failure(key, "the rows are not a list"));
    };
    let spills = Scratch::new(stage_file, "spill")?;
    let spill = Spill::new(&spills.0).map_err(|why| failure(key, why))?;
    let mut blocks = LocatorBlocks::new(spill, names.block_rows);
    let cut = (|| -> Raised<(Vec<LocatorBlock>, Spilled)> {
        for (index, row) in rows.iter().enumerate() {
            let (PyValue::Tuple(row) | PyValue::List(row)) = row else {
                return Err(SidecarError::new("Unread", "a row is not a pair"));
            };
            let [unit_id, class_id] = &row[..] else {
                return Err(SidecarError::new("Unread", "a row is not a pair"));
            };
            let fragment = dict(vec![("id", unit_id.clone()), ("class", class_id.clone())]);
            let index = index as i64;
            let span = Span {
                part: (index % 3) as usize,
                start: index * 100,
                length: 50 + index,
                inflated: 0,
            };
            blocks.add(&locator_line(&fragment, &span, index % 4 == 0)?)?;
        }
        blocks.close()
    })();
    match cut {
        Ok((entries, rows)) => Ok(dict(vec![
            (
                "blocks",
                PyValue::List(entries.iter().map(|block| block.value(0)).collect()),
            ),
            (
                "rows",
                digest(&rows.bytes().map_err(|why| failure(key, why))?),
            ),
        ])),
        Err(why) if why.class == "ValueError" => Ok(dict(vec![
            ("raised", PyValue::str(&why.class)),
            ("message", PyValue::str(&why.message)),
        ])),
        Err(why) => Err(failure(key, why)),
    }
}

/// The run a run line belongs to, by its key's prefix, or None for a line that is not a run's.
fn run_of(key: &str) -> Option<&str> {
    if !(key.starts_with("run:") || key.starts_with("generated:run:")) {
        return None;
    }
    key.strip_suffix(":open")
        .or_else(|| key.strip_suffix(":close"))
        .or_else(|| key.rfind(":call:").map(|at| &key[..at]))
}

/// The `sidecar-lines` stage: replay each run the stage file records, the build's over the dump's prior corpus and each generated one over its own corpus made again from the [`STAGE_BLOBS`] directory beside it, through [`SidecarLines`] with its trace kept, answering its `open` with whether each cursor opened to lines, each call with its events and the run's counts after it, or `{"raised": name}` for a call that fails, which ends the run, and its `close` with the counts, the locator blocks and the digests of what each spill file holds; and answer each generated projection, copy-forward, cursor and locator line.
pub fn stage_sidecar_lines(
    input_path: &Path,
    stage_file: &Path,
    out: &mut impl Write,
) -> WireResult<()> {
    let input = BuildInput::read(input_path)?;
    let names = SidecarNames::of(&input)?;
    let blobs = stage_file.with_file_name(STAGE_BLOBS);
    let file = File::open(stage_file)
        .map_err(|why| WireError(format!("cannot read {}: {why}", stage_file.display())))?;
    let mut lines = Vec::new();
    for line in BufReader::new(file).lines() {
        let line = line.map_err(|why| WireError(format!("cannot read a stage line: {why}")))?;
        let line: Value = serde_json::from_str(&line)
            .map_err(|why| WireError(format!("a stage line is not JSON: {why}")))?;
        let Some(key) = line["key"].as_str().map(str::to_owned) else {
            return Err(WireError("a stage line has no key".to_owned()));
        };
        lines.push((key, line));
    }
    let mut at = 0;
    while at < lines.len() {
        let key = lines[at].0.clone();
        if let Some(run) = run_of(&key) {
            if !key.ends_with(":open") {
                return Err(failure(&key, "a run's line comes before its open"));
            }
            let end = lines[at..]
                .iter()
                .position(|(other, _)| run_of(other) != Some(run))
                .map_or(lines.len(), |length| at + length);
            replay(&names, stage_file, &blobs, &lines[at..end], out)?;
            at = end;
            continue;
        }
        let line = &lines[at].1;
        let value = if key.starts_with("generated:line:") {
            projected(&names, line, &key)?
        } else if key.starts_with("generated:copy:") {
            copied(line, &key)?
        } else if key.starts_with("generated:cursor:") {
            cursor(&names, stage_file, &blobs, line, &key)?
        } else if key.starts_with("generated:locator:") {
            located(&names, stage_file, line, &key)?
        } else {
            return Err(failure(&key, "not a line of the sidecar-lines stage"));
        };
        stage_line(out, &key, &value)?;
        at += 1;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
