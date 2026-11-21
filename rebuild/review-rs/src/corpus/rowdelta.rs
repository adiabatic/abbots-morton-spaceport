//! The segment format, `rebuild/pipeline/row_delta.py` as the unit store and the units index read and write it. Its readers: a head read and parsed as `parse_head` and `Part.parse` read one, values taken as Python's `int` and `str` take them ([`read_head`], [`parse_head`], [`Head::document`]); every part it lists opened and checked against it ([`open_parts`]); a gzipped base's rows past its header line ([`base_rows`]), a segment's rows after its header is checked ([`check_segment_header`], [`segment_rows`], [`keyed_segment_rows`]), and the parts merged by place and by key as `heapq.merge` streams them ([`merge_placed`], [`merge_keyed`]); and a part held as gzip members inflated and checked member by member ([`part_stream`], [`Inflating`]).
//!
//! Each reader fails as Python's does, with the class and text it raises ([`ReadError`]), and a value Python reads that the reader does not hold (an int beyond 64 bits, or what [`json::unheld`] names) fails as [`ReadError::Unheld`], which no Python path takes. A reader reads a part by position from its start (`pread`, [`PartReader`]), so readers of one opened part never move each other.
//!
//! Its writers: places cut between two others in exact base-36 arithmetic ([`places_between`]), a segment row's line and its length ([`row_line`], [`row_length`]), a segment's header line ([`segment_header`]); a part written as one gzip stream through the sink's staging file, whole ([`write_part`], [`write_segment`]) or as its writer pushes its rows ([`StreamedPart`]), each counted and hashed as it is written; a head committed and the parts and staging files no head lists removed ([`commit`], [`remove_unlisted`], [`remove_staging`]); and the rules that choose a new base over a segment ([`writes_base`], [`fold_due`], [`fold_forced`], [`FoldClaim`], [`Head::with_segment`]). Each fails as Python's does ([`WriteError`]).
//!
//! [`stage_row_delta_read`] and [`stage_row_delta_write`] are the `row-delta-read` and `row-delta-write` stages of the stage fixtures.

use std::cmp::Reverse;
use std::collections::{HashMap, VecDeque};
use std::fs::File;
use std::io::{self, BufRead, BufReader, BufWriter, Read, Write};
use std::os::unix::fs::FileExt;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, PoisonError};

use ams_m1_kernel::emit::json_string;
use serde_json::Value;
use sha2::Digest as _;

use crate::corpus::gzip::{
    Decompressor, GzipError, GzipReader, GzipWriter, MTIME, PART_LEVEL, bytes_repr,
};
use crate::corpus::input::BuildInput;
use crate::corpus::sink::{Replacing, STAGING_SUFFIX, Sink, staging_path};
use crate::fragment::sha256_hex;
use crate::pyfmt::repr::{repr, repr_str};
use crate::pyfmt::{
    INT_MAX_STR_DIGITS, PyValue, decimal_value, int_digits_exceeded, is_space, json,
};
use crate::wire::{Result, WireError, error};

pub const HEAD_FORMAT: &str = "ams-row-delta-head/1";
pub const SEGMENT_FORMAT: &str = "ams-row-delta-segment/1";
pub const MERGE_PLACE: &str = "place";
pub const MERGE_KEY: &str = "key";
pub const MERGE_PROBE: &str = "probe";
pub const MERGE_PLAIN_KEY: &str = "plain-key";
pub const MERGE_ORDINAL: &str = "ordinal";
/// Every merge a head may name (`row_delta._MERGES`).
pub const MERGES: [&str; 5] = [
    MERGE_PLACE,
    MERGE_KEY,
    MERGE_PROBE,
    MERGE_PLAIN_KEY,
    MERGE_ORDINAL,
];
/// What a reader of a part held as gzip members reads of it at a time (`row_delta.INFLATE_READ_BYTES`).
pub const INFLATE_READ_BYTES: usize = 1 << 16;
pub const UPSERT: u8 = b'+';
pub const TOMBSTONE: u8 = b'-';
/// The stage's directory, beside its stage file, of the parts its lines name by sha256.
pub const STAGE_BLOBS: &str = "row-delta-read.blobs";

const GZIP_WINDOW: i32 = 31;
/// The most of a literal `int` shows in its refusal, as CPython's `%.200R` and its 200-byte cut of `bytes` show it.
const LITERAL_SHOWN: usize = 200;

/// What a reader raises, by the Python class it mirrors.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReadError {
    /// `row_delta.HeadError`: a head or a part that cannot be trusted.
    Head(String),
    /// Another `ValueError` a row or a merge raises, by its class (`ValueError` or `UnicodeDecodeError`).
    Value(&'static str, String),
    /// What Python's gzip reader raises.
    Gzip(GzipError),
    /// An `OSError` other than a missing part.
    Io(String),
    /// A value Python reads that the reader does not hold, which fails the Rust build.
    Unheld(String),
}

impl ReadError {
    /// The class Python raises, by its `__name__`; `Unheld` for what Python reads.
    pub fn class(&self) -> &str {
        match self {
            Self::Head(_) => "HeadError",
            Self::Value(class, _) => class,
            Self::Gzip(failure) => failure.class(),
            Self::Io(_) => "OSError",
            Self::Unheld(_) => "Unheld",
        }
    }

    pub fn message(&self) -> &str {
        match self {
            Self::Head(message)
            | Self::Value(_, message)
            | Self::Io(message)
            | Self::Unheld(message) => message,
            Self::Gzip(failure) => failure.message(),
        }
    }

    fn value(message: String) -> Self {
        Self::Value("ValueError", message)
    }

    fn of_io(failure: &io::Error) -> Self {
        match GzipError::of(failure) {
            Some(gzip) => Self::Gzip(gzip.clone()),
            None => Self::Io(failure.to_string()),
        }
    }
}

impl std::fmt::Display for ReadError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.class(), self.message())
    }
}

impl std::error::Error for ReadError {}

/// An exception a parse raises and catches, written as its `!r` writes it.
struct Raised {
    class: &'static str,
    message: String,
}

impl Raised {
    fn new(class: &'static str, message: impl Into<String>) -> Self {
        Self {
            class,
            message: message.into(),
        }
    }

    fn repr(&self) -> String {
        format!("{}({})", self.class, repr_str(&self.message))
    }
}

/// Why a value does not read: what Python raises, or a value Python reads that the reader does not hold.
enum Unread {
    Raised(Raised),
    Unheld(String),
}

fn raised(class: &'static str, message: impl Into<String>) -> Unread {
    Unread::Raised(Raised::new(class, message))
}

/// `repr(value)` for a value `json.loads` gives, which `repr` always writes.
fn value_repr(value: &PyValue) -> String {
    repr(value).unwrap_or_else(|failure| failure.0)
}

/// `str(value)`: a `str` itself, anything else its `repr`.
fn py_str(value: &PyValue) -> String {
    match value {
        PyValue::Str(text) => text.clone(),
        other => value_repr(other),
    }
}

fn type_name(value: &PyValue) -> &'static str {
    match value {
        PyValue::None => "NoneType",
        PyValue::Bool(_) => "bool",
        PyValue::Int(_) => "int",
        PyValue::Str(_) => "str",
        PyValue::Bytes(_) => "bytes",
        PyValue::Tuple(_) => "tuple",
        PyValue::List(_) => "list",
        PyValue::Dict(_) => "dict",
        PyValue::Set(_) => "set",
        PyValue::Record(..) => "object",
    }
}

/// Why [`parse_int`] gives no int.
#[derive(Debug, PartialEq, Eq)]
enum NotInt {
    /// A literal CPython refuses as invalid.
    Invalid,
    /// A valid literal of this many digits, past [`INT_MAX_STR_DIGITS`], which CPython refuses.
    Long(usize),
    /// An int CPython reads that is beyond 64 bits.
    Wide,
}

/// An int literal at base 10 ([`scan_int`]), refused as [`NotInt::Wide`] beyond 64 bits.
fn parse_int(text: &[u8]) -> std::result::Result<i64, NotInt> {
    let (negative, digits) = scan_int(text, 10)?;
    let mut value: i128 = 0;
    for digit in digits {
        if value <= i128::from(i64::MAX) + 1 {
            value = value * 10 + i128::from(digit);
        }
    }
    let value = if negative { -value } else { value };
    i64::try_from(value).map_err(|_| NotInt::Wide)
}

/// An int literal as CPython's `PyLong_FromString` reads one at `radix`, up to 36: ASCII whitespace around it, a sign, and digits (letters past nine either case) with single underscores between them, given as its sign and its digits' values, the most significant first. CPython reads only up to the first NUL, so a NUL makes the literal invalid unless what comes before it is already one past [`INT_MAX_STR_DIGITS`].
fn scan_int(text: &[u8], radix: u32) -> std::result::Result<(bool, Vec<u8>), NotInt> {
    let (text, nul) = match text.iter().position(|&byte| byte == 0) {
        Some(end) => (&text[..end], true),
        None => (text, false),
    };
    let space = |byte: u8| matches!(byte, b' ' | b'\t' | b'\n' | 0x0b | 0x0c | b'\r');
    let mut at = 0;
    while at < text.len() && space(text[at]) {
        at += 1;
    }
    let negative = match text.get(at) {
        Some(b'-') => {
            at += 1;
            true
        }
        Some(b'+') => {
            at += 1;
            false
        }
        _ => false,
    };
    let digit = |byte: u8| char::from(byte).to_digit(radix);
    let mut values = Vec::new();
    while let Some(&byte) = text.get(at) {
        if let Some(value) = digit(byte) {
            values.push(value as u8);
            at += 1;
        } else if byte == b'_'
            && !values.is_empty()
            && text.get(at + 1).is_some_and(|&next| digit(next).is_some())
        {
            at += 1;
        } else {
            break;
        }
    }
    let digits = values.len();
    if digits == 0 {
        return Err(NotInt::Invalid);
    }
    while at < text.len() && space(text[at]) {
        at += 1;
    }
    if at != text.len() {
        return Err(NotInt::Invalid);
    }
    if digits > INT_MAX_STR_DIGITS {
        return Err(NotInt::Long(digits));
    }
    if nul {
        return Err(NotInt::Invalid);
    }
    Ok((negative, values))
}

/// A `str` as CPython hands it to `PyLong_FromString`: its other characters' whitespace and decimal digits read as ASCII ones, and every other character as one no literal holds.
fn ascii_literal(text: &str) -> Vec<u8> {
    text.chars()
        .map(|letter| {
            if letter.is_ascii() {
                letter as u8
            } else if is_space(letter) {
                b' '
            } else {
                decimal_value(letter).map_or(b'?', |digit| b'0' + digit as u8)
            }
        })
        .collect()
}

/// `int(value)` for a value `json.loads` gives: an int, a bool, or a `str` CPython reads at base 10 ([`ascii_literal`]).
fn py_int(value: &PyValue) -> std::result::Result<i64, Unread> {
    match value {
        PyValue::Int(number) => Ok(*number),
        PyValue::Bool(flag) => Ok(i64::from(*flag)),
        PyValue::Str(text) => match parse_int(&ascii_literal(text)) {
            Ok(number) => Ok(number),
            Err(NotInt::Wide) => Err(Unread::Unheld(format!(
                "the int {} does not fit 64 bits",
                repr_str(text)
            ))),
            Err(NotInt::Long(digits)) => Err(raised("ValueError", int_digits_exceeded(digits))),
            Err(NotInt::Invalid) => Err(raised(
                "ValueError",
                format!(
                    "invalid literal for int() with base 10: {}",
                    repr_str(text)
                        .chars()
                        .take(LITERAL_SHOWN)
                        .collect::<String>()
                ),
            )),
        },
        other => Err(raised(
            "TypeError",
            format!(
                "int() argument must be a string, a bytes-like object or a real number, not '{}'",
                type_name(other)
            ),
        )),
    }
}

/// `int(data)` for `bytes`, a segment row's anchor. An invalid literal's refusal shows the `repr` of its first [`LITERAL_SHOWN`] bytes, as `_PyLong_FromBytes` cuts them before `%.200R`.
fn bytes_int(data: &[u8]) -> std::result::Result<i64, ReadError> {
    match parse_int(data) {
        Ok(number) => Ok(number),
        Err(NotInt::Wide) => Err(ReadError::Unheld(format!(
            "the anchor {} does not fit 64 bits",
            bytes_repr(data)
        ))),
        Err(NotInt::Long(digits)) => Err(ReadError::value(int_digits_exceeded(digits))),
        Err(NotInt::Invalid) => Err(ReadError::value(format!(
            "invalid literal for int() with base 10: {}",
            bytes_repr(&data[..data.len().min(LITERAL_SHOWN)])
                .chars()
                .take(LITERAL_SHOWN)
                .collect::<String>()
        ))),
    }
}

/// `value == number` as Python compares a value `json.loads` gives with an int, a bool equal to its int.
fn equals_int(value: &PyValue, number: i64) -> bool {
    match value {
        PyValue::Int(held) => *held == number,
        PyValue::Bool(flag) => i64::from(*flag) == number,
        _ => false,
    }
}

fn is_str(value: Option<&PyValue>, text: &str) -> bool {
    matches!(value, Some(PyValue::Str(held)) if held == text)
}

fn get<'a>(entries: &'a [(PyValue, PyValue)], name: &str) -> Option<&'a PyValue> {
    entries.iter().find_map(|(key, value)| match key {
        PyValue::Str(key) if key == name => Some(value),
        _ => None,
    })
}

fn entry(name: &str, value: PyValue) -> (PyValue, PyValue) {
    (PyValue::str(name), value)
}

/// The stamp and the family keys one part's rows were settled under (`row_delta.PartStamp`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PartStamp {
    pub stamp: String,
    pub keys: i64,
}

/// One gzip member of a part held as members (`row_delta.Member`): its section, `""` for the header line's, its bytes on disk and its inflated bytes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Member {
    pub section: String,
    pub length: i64,
    pub inflated: i64,
}

/// One part as a head lists it (`row_delta.Part`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Part {
    pub name: String,
    pub size: i64,
    pub sha256: String,
    pub rows: i64,
    pub rows_bytes: i64,
    pub settled: Option<PartStamp>,
    pub members: Option<Vec<Member>>,
}

impl Part {
    /// `Part.document`: the fields in their order, the stamp and the members only when the part holds them.
    pub fn document(&self) -> PyValue {
        let mut entries = vec![
            entry("name", PyValue::str(&self.name)),
            entry("size", PyValue::Int(self.size)),
            entry("sha256", PyValue::str(&self.sha256)),
            entry("rows", PyValue::Int(self.rows)),
            entry("rows_bytes", PyValue::Int(self.rows_bytes)),
        ];
        if let Some(settled) = &self.settled {
            entries.push(entry("stamp", PyValue::str(&settled.stamp)));
            entries.push(entry("family_keys", PyValue::Int(settled.keys)));
        }
        if let Some(members) = &self.members {
            let members = members
                .iter()
                .map(|member| {
                    PyValue::List(vec![
                        PyValue::str(&member.section),
                        PyValue::Int(member.length),
                        PyValue::Int(member.inflated),
                    ])
                })
                .collect();
            entries.push(entry("members", PyValue::List(members)));
        }
        PyValue::Dict(entries)
    }

    /// `Part.parse`: a dict whose fields `str` and `int` take, a stamp only with a family keys place, and a member index whose lengths sum to the part's size and whose inflated lengths past the header's sum to its row bytes.
    pub fn parse(document: &PyValue) -> std::result::Result<Self, ReadError> {
        let PyValue::Dict(entries) = document else {
            return Err(ReadError::Head(format!(
                "a head lists a part as {}",
                value_repr(document)
            )));
        };
        let part = match Self::described(entries) {
            Ok(part) => part,
            Err(Unread::Raised(raised)) => {
                return Err(ReadError::Head(format!(
                    "a head lists a part it does not describe: {}",
                    raised.repr()
                )));
            }
            Err(Unread::Unheld(why)) => return Err(ReadError::Unheld(why)),
        };
        if let Some(members) = &part.members {
            let length: i128 = members.iter().map(|member| i128::from(member.length)).sum();
            if length != i128::from(part.size) {
                return Err(ReadError::Head(format!(
                    "a head records members of {} that do not sum to its {} bytes",
                    part.name, part.size
                )));
            }
            let inflated: i128 = members[1..]
                .iter()
                .map(|member| i128::from(member.inflated))
                .sum();
            if inflated != i128::from(part.rows_bytes) {
                return Err(ReadError::Head(format!(
                    "a head records members of {} that do not inflate to its header line and its {} row bytes",
                    part.name, part.rows_bytes
                )));
            }
        }
        Ok(part)
    }

    fn described(entries: &[(PyValue, PyValue)]) -> std::result::Result<Self, Unread> {
        let field = |name: &str| get(entries, name).ok_or_else(|| raised("KeyError", name));
        let mut settled = None;
        if get(entries, "stamp").is_some() || get(entries, "family_keys").is_some() {
            let stamp = field("stamp")?;
            let keys = match stamp {
                PyValue::Str(_) => match field("family_keys")? {
                    PyValue::Int(keys) => Some(*keys),
                    _ => None,
                },
                _ => None,
            };
            let (PyValue::Str(stamp), Some(keys)) = (stamp, keys) else {
                return Err(raised(
                    "ValueError",
                    "a part's stamp is not a string or its family keys not a place",
                ));
            };
            settled = Some(PartStamp {
                stamp: stamp.clone(),
                keys,
            });
        }
        let name = py_str(field("name")?);
        let size = py_int(field("size")?)?;
        let sha256 = py_str(field("sha256")?);
        let rows = py_int(field("rows")?)?;
        let rows_bytes = py_int(field("rows_bytes")?)?;
        let members = match get(entries, "members") {
            Some(members) => Some(parse_members(members)?),
            None => None,
        };
        Ok(Self {
            name,
            size,
            sha256,
            rows,
            rows_bytes,
            settled,
            members,
        })
    }
}

/// `row_delta._parse_members`.
fn parse_members(entries: &PyValue) -> std::result::Result<Vec<Member>, Unread> {
    let items = match entries {
        PyValue::List(items) if !items.is_empty() => items,
        _ => {
            return Err(raised(
                "ValueError",
                "a part's member index is not a list of members",
            ));
        }
    };
    let mut members = Vec::with_capacity(items.len());
    for item in items {
        let member = match item {
            PyValue::List(fields) => match fields.as_slice() {
                [
                    PyValue::Str(section),
                    PyValue::Int(length),
                    PyValue::Int(inflated),
                ] if *length >= 0 && *inflated >= 0 => Some(Member {
                    section: section.clone(),
                    length: *length,
                    inflated: *inflated,
                }),
                _ => None,
            },
            _ => None,
        };
        let Some(member) = member else {
            return Err(raised(
                "ValueError",
                format!("a part's member index lists {}", value_repr(item)),
            ));
        };
        members.push(member);
    }
    if !members[0].section.is_empty() {
        return Err(raised(
            "ValueError",
            "a part's member index does not start with its header line's member",
        ));
    }
    Ok(members)
}

/// One output's head (`row_delta.Head`): `stamps` is the dict the file holds, and `family_keys` each distinct keys map its parts record.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Head {
    pub output: String,
    pub generation: i64,
    pub content: i64,
    pub stamps: PyValue,
    pub base: Part,
    pub segments: Vec<Part>,
    pub merge: String,
    pub family_keys: Vec<PyValue>,
}

impl Head {
    /// The base, then each segment.
    pub fn parts(&self) -> impl Iterator<Item = &Part> {
        std::iter::once(&self.base).chain(&self.segments)
    }

    /// `Head.document`: the merge only when it is not by place, the family keys only when there are any.
    pub fn document(&self) -> PyValue {
        let mut entries = vec![
            entry("format", PyValue::str(HEAD_FORMAT)),
            entry("output", PyValue::str(&self.output)),
            entry("generation", PyValue::Int(self.generation)),
            entry("content", PyValue::Int(self.content)),
            entry("stamps", self.stamps.clone()),
            entry("base", self.base.document()),
            entry(
                "segments",
                PyValue::List(self.segments.iter().map(Part::document).collect()),
            ),
        ];
        if self.merge != MERGE_PLACE {
            entries.push(entry("merge", PyValue::str(&self.merge)));
        }
        if !self.family_keys.is_empty() {
            entries.push(entry(
                "family_keys",
                PyValue::List(self.family_keys.clone()),
            ));
        }
        PyValue::Dict(entries)
    }

    /// `parse_head` past its `json.loads`: the document of `output`'s head checked in `parse_head`'s order.
    pub fn from_document(document: &PyValue, output: &str) -> std::result::Result<Self, ReadError> {
        let name = head_name(output);
        let refuse = |why: String| Err(ReadError::Head(format!("{name} {why}")));
        let entries = match document {
            PyValue::Dict(entries) if is_str(get(entries, "format"), HEAD_FORMAT) => entries,
            _ => return refuse(format!("is not an {HEAD_FORMAT} head")),
        };
        if !is_str(get(entries, "output"), output) {
            let named = get(entries, "output").unwrap_or(&PyValue::None);
            return refuse(format!("names the output {}", value_repr(named)));
        }
        let (Some(stamps @ PyValue::Dict(_)), Some(PyValue::List(segments))) =
            (get(entries, "stamps"), get(entries, "segments"))
        else {
            return refuse("holds no stamps or no segment list".to_owned());
        };
        let number = |field: &str| match get(entries, field) {
            Some(value) => py_int(value),
            None => Err(raised("KeyError", field)),
        };
        let (generation, content) = match (number("generation"), number("content")) {
            (Ok(generation), Ok(content)) => (generation, content),
            (Ok(_) | Err(Unread::Unheld(_)), Err(Unread::Unheld(why)))
            | (Err(Unread::Unheld(why)), Ok(_)) => return Err(ReadError::Unheld(why)),
            _ => return refuse("names no generation".to_owned()),
        };
        let merge = match get(entries, "merge") {
            None => MERGE_PLACE.to_owned(),
            Some(PyValue::Str(merge)) if MERGES.contains(&merge.as_str()) => merge.clone(),
            Some(other) => return refuse(format!("merges its parts by {}", value_repr(other))),
        };
        let family_keys = match get(entries, "family_keys") {
            None => Vec::new(),
            Some(PyValue::List(maps))
                if maps.iter().all(|map| {
                    matches!(map, PyValue::Dict(pairs) if pairs.iter().all(|pair| {
                        matches!(pair, (PyValue::Str(_), PyValue::Str(_)))
                    }))
                }) =>
            {
                maps.clone()
            }
            Some(_) => {
                return refuse("holds family keys that are not maps of names to keys".to_owned());
            }
        };
        let base = Part::parse(get(entries, "base").unwrap_or(&PyValue::None))?;
        let segments = segments
            .iter()
            .map(Part::parse)
            .collect::<std::result::Result<Vec<_>, _>>()?;
        let head = Self {
            output: output.to_owned(),
            generation,
            content,
            stamps: stamps.clone(),
            base,
            segments,
            merge,
            family_keys,
        };
        if head.base.name != base_name(output, generation) {
            return refuse(format!(
                "lists {} as generation {generation}'s base",
                head.base.name
            ));
        }
        for (index, part) in head.segments.iter().enumerate() {
            let number = index as i64 + 1;
            if part.name != segment_name(output, generation, number) {
                return refuse(format!(
                    "lists {} as segment {number} of generation {generation}",
                    part.name
                ));
            }
        }
        for part in head.parts() {
            if let Some(settled) = &part.settled
                && !(0..head.family_keys.len() as i64).contains(&settled.keys)
            {
                return refuse(format!(
                    "records {} under family keys it does not hold",
                    part.name
                ));
            }
        }
        Ok(head)
    }
}

pub fn head_name(output: &str) -> String {
    format!("{output}.head")
}

pub fn base_name(output: &str, generation: i64) -> String {
    format!("{output}.{generation}.base")
}

pub fn segment_name(output: &str, generation: i64, number: i64) -> String {
    format!("{output}.{generation}.{number}.delta")
}

pub fn head_path(directory: &Path, output: &str) -> PathBuf {
    directory.join(head_name(output))
}

/// `row_delta.read_head`: the head of `output` under `directory`, or None when there is none.
pub fn read_head(directory: &Path, output: &str) -> std::result::Result<Option<Head>, ReadError> {
    match std::fs::read(head_path(directory, output)) {
        Ok(raw) => parse_head(&raw, output).map(Some),
        Err(failure) if failure.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(failure) => Err(ReadError::Io(failure.to_string())),
    }
}

/// `row_delta.parse_head`: `raw` read as `json.loads` reads bytes, then checked ([`Head::from_document`]). A parse failure's refusal leaves out the decoder's own text, which no output carries.
pub fn parse_head(raw: &[u8], output: &str) -> std::result::Result<Head, ReadError> {
    match json::loads_bytes(raw) {
        Ok(document) => Head::from_document(&document, output),
        Err(failure) if json::unheld(&failure) => Err(ReadError::Unheld(failure.0)),
        Err(_) => Err(ReadError::Head(format!(
            "{} does not parse",
            head_name(output)
        ))),
    }
}

/// A part read by position from its start, leaving the file's own offset where it is.
pub struct PartReader<'a> {
    file: &'a File,
    offset: u64,
}

impl<'a> PartReader<'a> {
    pub fn new(file: &'a File) -> Self {
        Self { file, offset: 0 }
    }
}

impl Read for PartReader<'_> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let read = self.file.read_at(buf, self.offset)?;
        self.offset += read as u64;
        Ok(read)
    }
}

/// `row_delta.open_parts`: open every part `head` lists under `directory`, base first, each checked against the head's size and, with `sha256`, its digest.
pub fn open_parts(
    directory: &Path,
    head: &Head,
    sha256: bool,
) -> std::result::Result<Vec<File>, ReadError> {
    let mut opened = Vec::with_capacity(head.segments.len() + 1);
    for part in head.parts() {
        let file = match File::open(directory.join(&part.name)) {
            Ok(file) => file,
            Err(failure) if failure.kind() == io::ErrorKind::NotFound => {
                return Err(ReadError::Head(format!("{} is missing", part.name)));
            }
            Err(failure) => return Err(ReadError::Io(failure.to_string())),
        };
        let metadata = file
            .metadata()
            .map_err(|failure| ReadError::Io(failure.to_string()))?;
        if metadata.is_dir() {
            return Err(ReadError::Io(format!(
                "[Errno 21] Is a directory: {}",
                repr_str(&directory.join(&part.name).to_string_lossy())
            )));
        }
        if i128::from(metadata.len()) != i128::from(part.size) {
            return Err(ReadError::Head(format!(
                "{} is not the {} bytes its head records",
                part.name, part.size
            )));
        }
        if sha256 {
            let mut digest = sha2::Sha256::new();
            let mut reader = PartReader::new(&file);
            let mut buffer = vec![0; INFLATE_READ_BYTES];
            loop {
                match reader.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(read) => digest.update(&buffer[..read]),
                    Err(failure) if failure.kind() == io::ErrorKind::Interrupted => {}
                    Err(failure) => return Err(ReadError::Io(failure.to_string())),
                }
            }
            if hex_digest(digest) != part.sha256 {
                return Err(ReadError::Head(format!(
                    "{} does not hash to the sha256 its head records",
                    part.name
                )));
            }
        }
        opened.push(file);
    }
    Ok(opened)
}

/// A gzipped part's lines, read from its start as `gzip.GzipFile` iterates them.
struct GzipLines<'a> {
    reader: BufReader<GzipReader<PartReader<'a>>>,
}

impl<'a> GzipLines<'a> {
    fn new(file: &'a File) -> Self {
        Self {
            reader: BufReader::new(GzipReader::new(PartReader::new(file))),
        }
    }

    fn line(&mut self) -> std::result::Result<Option<Vec<u8>>, ReadError> {
        let mut line = Vec::new();
        match self.reader.read_until(b'\n', &mut line) {
            Ok(0) => Ok(None),
            Ok(_) => Ok(Some(line)),
            Err(failure) => Err(ReadError::of_io(&failure)),
        }
    }
}

/// A gzipped base's rows past its header line, as `unit_index._base_lines` reads the index's base: nothing for a part with no header line, where the store's reader, `unit_index.store_base_lines`, raises.
pub struct BaseRows<'a> {
    lines: GzipLines<'a>,
    started: bool,
    done: bool,
}

pub fn base_rows(file: &File) -> BaseRows<'_> {
    BaseRows {
        lines: GzipLines::new(file),
        started: false,
        done: false,
    }
}

impl Iterator for BaseRows<'_> {
    type Item = std::result::Result<Vec<u8>, ReadError>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        if !self.started {
            self.started = true;
            match self.lines.line() {
                Ok(Some(_)) => {}
                Ok(None) => {
                    self.done = true;
                    return None;
                }
                Err(failure) => {
                    self.done = true;
                    return Some(Err(failure));
                }
            }
        }
        let line = self.lines.line().transpose();
        if !matches!(line, Some(Ok(_))) {
            self.done = true;
        }
        line
    }
}

/// A segment row's place: the base row it follows and a fraction after it, `(-1, f)` before the first.
pub type Place = (i64, String);

/// One segment row (`row_delta.Row`): its operation, [`UPSERT`] or [`TOMBSTONE`], its place, and its payload with its newline.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub op: u8,
    pub place: Place,
    pub payload: Vec<u8>,
}

/// `row_delta._parse_row`.
fn parse_row(line: &[u8]) -> std::result::Result<Row, ReadError> {
    let fields: Vec<&[u8]> = line.splitn(4, |&byte| byte == b'\t').collect();
    let [op, anchor, fraction, payload] = fields.as_slice() else {
        return Err(ReadError::value(format!(
            "not enough values to unpack (expected 4, got {})",
            fields.len()
        )));
    };
    let op = match op {
        [UPSERT] => UPSERT,
        [TOMBSTONE] => TOMBSTONE,
        other => {
            return Err(ReadError::value(format!(
                "a segment row's operation is {}",
                bytes_repr(other)
            )));
        }
    };
    let anchor = bytes_int(anchor)?;
    if let Some(at) = fraction.iter().position(|byte| !byte.is_ascii()) {
        return Err(ReadError::Value(
            "UnicodeDecodeError",
            format!(
                "'ascii' codec can't decode byte {:#04x} in position {at}: ordinal not in range(128)",
                fraction[at]
            ),
        ));
    }
    Ok(Row {
        op,
        place: (
            anchor,
            String::from_utf8(fraction.to_vec()).expect("ASCII is UTF-8"),
        ),
        payload: payload.to_vec(),
    })
}

/// `format(value)` for a value `json.loads` gives, as an f-string writes it.
fn formatted(value: Option<&PyValue>) -> String {
    py_str(value.unwrap_or(&PyValue::None))
}

/// `row_delta.check_segment_header`: a segment's header line checked against the head that lists it. A header that does not parse is refused without the decoder's own text.
pub fn check_segment_header(
    line: &[u8],
    output: &str,
    generation: i64,
    number: i64,
    merge: &str,
) -> std::result::Result<(), ReadError> {
    let header = match json::loads_bytes(line) {
        Ok(header) => header,
        Err(failure) if json::unheld(&failure) => return Err(ReadError::Unheld(failure.0)),
        Err(_) => {
            return Err(ReadError::Head(format!("segment {number} has no header")));
        }
    };
    let entries = match &header {
        PyValue::Dict(entries) if is_str(get(entries, "format"), SEGMENT_FORMAT) => entries,
        _ => {
            return Err(ReadError::Head(format!(
                "segment {number} is not an {SEGMENT_FORMAT} segment"
            )));
        }
    };
    let (named_generation, named_number) = (get(entries, "generation"), get(entries, "segment"));
    if !is_str(get(entries, "output"), output)
        || !named_generation.is_some_and(|value| equals_int(value, generation))
        || !named_number.is_some_and(|value| equals_int(value, number))
    {
        return Err(ReadError::Head(format!(
            "{output} lists segment {number} of generation {generation}, whose header names segment {} of generation {}",
            formatted(named_number),
            formatted(named_generation)
        )));
    }
    let named_merge = get(entries, "merge");
    if !(named_merge.is_none() && merge == MERGE_PLACE) && !is_str(named_merge, merge) {
        let named = named_merge.map_or_else(|| MERGE_PLACE.to_owned(), py_str);
        return Err(ReadError::Head(format!(
            "{output} merges by {merge}, and segment {number}'s header says it merges by {named}"
        )));
    }
    Ok(())
}

/// The header line of a gzipped segment, refused as `row_delta.gzipped_header` refuses a segment that is not gzipped, then checked.
fn checked_header(
    lines: &mut GzipLines<'_>,
    output: &str,
    generation: i64,
    number: i64,
    merge: &str,
) -> std::result::Result<(), ReadError> {
    let header = match lines.line() {
        Ok(line) => line.unwrap_or_default(),
        Err(ReadError::Gzip(GzipError::BadGzipFile(why))) => {
            return Err(ReadError::Head(format!(
                "segment {number} of {output} is not a gzipped segment: {why}"
            )));
        }
        Err(failure) => return Err(failure),
    };
    check_segment_header(&header, output, generation, number, merge)
}

/// One segment's rows, its header checked at the first read as `row_delta.segment_rows` and `keyed_segment_rows` check it.
struct SegmentLines<'a> {
    lines: GzipLines<'a>,
    output: String,
    generation: i64,
    number: i64,
    merge: &'static str,
    started: bool,
    done: bool,
}

impl<'a> SegmentLines<'a> {
    fn new(
        file: &'a File,
        output: &str,
        generation: i64,
        number: i64,
        merge: &'static str,
    ) -> Self {
        Self {
            lines: GzipLines::new(file),
            output: output.to_owned(),
            generation,
            number,
            merge,
            started: false,
            done: false,
        }
    }

    fn line(&mut self) -> Option<std::result::Result<Vec<u8>, ReadError>> {
        if self.done {
            return None;
        }
        if !self.started {
            self.started = true;
            if let Err(failure) = checked_header(
                &mut self.lines,
                &self.output,
                self.generation,
                self.number,
                self.merge,
            ) {
                self.done = true;
                return Some(Err(failure));
            }
        }
        let line = self.lines.line().transpose();
        if !matches!(line, Some(Ok(_))) {
            self.done = true;
        }
        line
    }
}

/// `row_delta.segment_rows`: a segment by place's rows.
pub struct SegmentRows<'a>(SegmentLines<'a>);

pub fn segment_rows<'a>(
    file: &'a File,
    output: &str,
    generation: i64,
    number: i64,
) -> SegmentRows<'a> {
    SegmentRows(SegmentLines::new(
        file,
        output,
        generation,
        number,
        MERGE_PLACE,
    ))
}

impl Iterator for SegmentRows<'_> {
    type Item = std::result::Result<Row, ReadError>;

    fn next(&mut self) -> Option<Self::Item> {
        let row = self.0.line()?.and_then(|line| parse_row(&line));
        if row.is_err() {
            self.0.done = true;
        }
        Some(row)
    }
}

/// `row_delta.keyed_segment_rows`: a segment by key's rows, each its operation and its payload with its newline.
pub struct KeyedSegmentRows<'a>(SegmentLines<'a>);

pub fn keyed_segment_rows<'a>(
    file: &'a File,
    output: &str,
    generation: i64,
    number: i64,
) -> KeyedSegmentRows<'a> {
    KeyedSegmentRows(SegmentLines::new(
        file, output, generation, number, MERGE_KEY,
    ))
}

impl Iterator for KeyedSegmentRows<'_> {
    type Item = std::result::Result<(u8, Vec<u8>), ReadError>;

    fn next(&mut self) -> Option<Self::Item> {
        let (output, number) = (self.0.output.clone(), self.0.number);
        let row = self.0.line()?.and_then(|line| {
            let tab = line.iter().position(|&byte| byte == b'\t');
            match (tab, line.first()) {
                (Some(1), Some(&op)) if op == UPSERT || op == TOMBSTONE => {
                    Ok((op, line[2..].to_vec()))
                }
                _ => Err(ReadError::value(format!(
                    "segment {number} of {output} holds a row that is not an upsert or a tombstone"
                ))),
            }
        });
        if row.is_err() {
            self.0.done = true;
        }
        Some(row)
    }
}

type Stream<'a, T> = Box<dyn Iterator<Item = std::result::Result<T, ReadError>> + 'a>;

/// `heapq.merge` over streams that each ascend: the streams are first read in order, then after each item the stream that gave it, and the least item of the streams' heads comes next, the earlier stream first between equal ones. A stream that fails ends the merge with its failure.
struct HeapMerge<'a, T: Ord> {
    streams: Vec<Stream<'a, T>>,
    heads: Vec<Option<T>>,
    started: bool,
    last: Option<usize>,
    done: bool,
}

impl<'a, T: Ord> HeapMerge<'a, T> {
    fn new(streams: Vec<Stream<'a, T>>) -> Self {
        let heads = streams.iter().map(|_| None).collect();
        Self {
            streams,
            heads,
            started: false,
            last: None,
            done: false,
        }
    }

    fn pull(&mut self, index: usize) -> std::result::Result<(), ReadError> {
        self.heads[index] = self.streams[index].next().transpose()?;
        Ok(())
    }
}

impl<T: Ord> Iterator for HeapMerge<'_, T> {
    type Item = std::result::Result<T, ReadError>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        let pulled = if self.started {
            self.last.map_or(Ok(()), |index| self.pull(index))
        } else {
            self.started = true;
            (0..self.streams.len()).try_for_each(|index| self.pull(index))
        };
        if let Err(failure) = pulled {
            self.done = true;
            return Some(Err(failure));
        }
        let least = (0..self.heads.len())
            .filter(|&index| self.heads[index].is_some())
            .min_by(|&left, &right| self.heads[left].cmp(&self.heads[right]));
        let Some(index) = least else {
            self.done = true;
            return None;
        };
        self.last = Some(index);
        self.heads[index].take().map(Ok)
    }
}

/// A row in the merge by place: its place, its part's number, its operation and its payload.
type Placed = (Place, usize, u8, Vec<u8>);

/// `row_delta.merge_placed`: the live rows of a base and its segments in place order, each with its place; the latest part's row at a place is live unless it is a tombstone.
pub struct MergePlaced<'a> {
    merge: HeapMerge<'a, Placed>,
    held: Option<Placed>,
    done: bool,
}

pub fn merge_placed<'a>(
    base: impl Iterator<Item = std::result::Result<Vec<u8>, ReadError>> + 'a,
    segments: Vec<Stream<'a, Row>>,
) -> MergePlaced<'a> {
    let mut streams: Vec<Stream<'a, Placed>> =
        vec![Box::new(base.enumerate().map(|(index, row)| {
            row.map(|payload| ((index as i64, String::new()), 0, UPSERT, payload))
        }))];
    for (index, rows) in segments.into_iter().enumerate() {
        streams.push(Box::new(rows.map(move |row| {
            row.map(|row| (row.place, index + 1, row.op, row.payload))
        })));
    }
    MergePlaced {
        merge: HeapMerge::new(streams),
        held: None,
        done: false,
    }
}

impl Iterator for MergePlaced<'_> {
    type Item = std::result::Result<(Place, Vec<u8>), ReadError>;

    fn next(&mut self) -> Option<Self::Item> {
        while !self.done {
            match self.merge.next() {
                Some(Ok(item)) => {
                    let held = self.held.replace(item);
                    if let Some((place, _, op, payload)) = held
                        && op == UPSERT
                        && self.held.as_ref().is_some_and(|item| item.0 != place)
                    {
                        return Some(Ok((place, payload)));
                    }
                }
                Some(Err(failure)) => {
                    self.done = true;
                    return Some(Err(failure));
                }
                None => {
                    self.done = true;
                    if let Some((place, _, UPSERT, payload)) = self.held.take() {
                        return Some(Ok((place, payload)));
                    }
                }
            }
        }
        None
    }
}

/// A key a merge by key orders rows by, its fields compared in turn as Python compares lists of bytes.
pub type Key = Vec<Vec<u8>>;
/// What gives a row's key, or a tombstone's.
pub type KeyOf<'a> = &'a dyn Fn(&[u8]) -> std::result::Result<Key, ReadError>;

/// `repr` of a key, a list of bytes.
fn key_repr(key: &Key) -> String {
    let fields: Vec<String> = key.iter().map(|field| bytes_repr(field)).collect();
    format!("[{}]", fields.join(", "))
}

/// A row in the merge of the segments by key: its key, its part's number reversed so the latest comes first, its operation and its payload.
type Keyed = (Key, Reverse<usize>, u8, Vec<u8>);

/// The latest row at each key of the segments merged by key (`merge_keyed`'s `latest`), each segment's keys checked to strictly ascend as they are read.
struct Latest<'a> {
    merge: HeapMerge<'a, Keyed>,
    held: Option<Keyed>,
    done: bool,
}

impl<'a> Latest<'a> {
    fn new(
        segments: Vec<Stream<'a, (u8, Vec<u8>)>>,
        key: KeyOf<'a>,
        tombstone_key: KeyOf<'a>,
    ) -> Self {
        let streams = segments
            .into_iter()
            .enumerate()
            .map(|(index, rows)| {
                let number = index + 1;
                let mut previous: Option<Key> = None;
                let keyed = rows.map(move |row| {
                    let (op, payload) = row?;
                    let at = if op == UPSERT {
                        key(&payload)
                    } else {
                        tombstone_key(&payload)
                    }?;
                    if previous.as_ref().is_some_and(|previous| at <= *previous) {
                        return Err(ReadError::value(format!(
                            "segment {number} is out of key order at {}",
                            key_repr(&at)
                        )));
                    }
                    previous = Some(at.clone());
                    Ok((at, Reverse(number), op, payload))
                });
                Box::new(keyed) as Stream<'a, Keyed>
            })
            .collect();
        Self {
            merge: HeapMerge::new(streams),
            held: None,
            done: false,
        }
    }
}

impl Iterator for Latest<'_> {
    type Item = std::result::Result<Keyed, ReadError>;

    fn next(&mut self) -> Option<Self::Item> {
        while !self.done {
            match self.merge.next() {
                Some(Ok(item)) => {
                    if self.held.as_ref().is_none_or(|held| held.0 != item.0)
                        && let Some(held) = self.held.replace(item)
                    {
                        return Some(Ok(held));
                    }
                }
                Some(Err(failure)) => {
                    self.done = true;
                    return Some(Err(failure));
                }
                None => {
                    self.done = true;
                    return self.held.take().map(Ok);
                }
            }
        }
        None
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum KeyedState {
    Start,
    Base,
    Streaming,
    Tail,
    Done,
}

/// `row_delta.merge_keyed`: the live rows of a base and its segments merged by key, in key order. The segments are merged among themselves first, the latest winning at each key, and the base streams past them, its keys checked up to its last change; its rows past the last change stream through unchecked.
pub struct MergeKeyed<'a> {
    base: Stream<'a, Vec<u8>>,
    changes: Latest<'a>,
    key: KeyOf<'a>,
    change: Option<Keyed>,
    previous: Option<Key>,
    state: KeyedState,
    ready: VecDeque<Vec<u8>>,
    failure: Option<ReadError>,
}

pub fn merge_keyed<'a>(
    base: impl Iterator<Item = std::result::Result<Vec<u8>, ReadError>> + 'a,
    segments: Vec<Stream<'a, (u8, Vec<u8>)>>,
    key: KeyOf<'a>,
    tombstone_key: KeyOf<'a>,
) -> MergeKeyed<'a> {
    MergeKeyed {
        base: Box::new(base),
        changes: Latest::new(segments, key, tombstone_key),
        key,
        change: None,
        previous: None,
        state: KeyedState::Start,
        ready: VecDeque::new(),
        failure: None,
    }
}

impl MergeKeyed<'_> {
    fn next_change(&mut self) -> std::result::Result<(), ReadError> {
        self.change = self.changes.next().transpose()?;
        Ok(())
    }

    /// Yield the change in hand when it is an upsert, then read the next.
    fn take_change(&mut self) -> std::result::Result<(), ReadError> {
        if let Some((_, _, UPSERT, payload)) = self.change.take() {
            self.ready.push_back(payload);
        }
        self.next_change()
    }

    /// Run the merge on to its next row or its end, as the generator runs between two of its yields.
    fn step(&mut self) -> std::result::Result<(), ReadError> {
        match self.state {
            KeyedState::Start => {
                self.state = KeyedState::Base;
                self.next_change()
            }
            KeyedState::Base => {
                let Some(row) = self.base.next().transpose()? else {
                    self.state = KeyedState::Tail;
                    return Ok(());
                };
                if self.change.is_none() {
                    self.ready.push_back(row);
                    self.state = KeyedState::Streaming;
                    return Ok(());
                }
                let at = (self.key)(&row)?;
                if self
                    .previous
                    .as_ref()
                    .is_some_and(|previous| at <= *previous)
                {
                    return Err(ReadError::value(format!(
                        "the base is out of key order at {}",
                        key_repr(&at)
                    )));
                }
                self.previous = Some(at.clone());
                while self.change.as_ref().is_some_and(|change| change.0 < at) {
                    self.take_change()?;
                }
                if self.change.as_ref().is_some_and(|change| change.0 == at) {
                    self.take_change()
                } else {
                    self.ready.push_back(row);
                    Ok(())
                }
            }
            KeyedState::Streaming => {
                match self.base.next().transpose()? {
                    Some(row) => self.ready.push_back(row),
                    None => self.state = KeyedState::Done,
                }
                Ok(())
            }
            KeyedState::Tail => {
                if self.change.is_none() {
                    self.state = KeyedState::Done;
                    Ok(())
                } else {
                    self.take_change()
                }
            }
            KeyedState::Done => Ok(()),
        }
    }
}

impl Iterator for MergeKeyed<'_> {
    type Item = std::result::Result<Vec<u8>, ReadError>;

    fn next(&mut self) -> Option<Self::Item> {
        loop {
            if let Some(row) = self.ready.pop_front() {
                return Some(Ok(row));
            }
            if let Some(failure) = self.failure.take() {
                return Some(Err(failure));
            }
            if self.state == KeyedState::Done {
                return None;
            }
            if let Err(failure) = self.step() {
                self.state = KeyedState::Done;
                self.failure = Some(failure);
            }
        }
    }
}

/// `row_delta._Inflating`: a part held as gzip members, each inflated in file order with gzip framing, no read giving more than it is asked for and none crossing from one member into the next, each member checked against its index as its end is reached.
pub struct Inflating<'a> {
    file: &'a File,
    name: String,
    members: std::vec::IntoIter<Member>,
    member: Option<Member>,
    offset: u64,
    end: u64,
    inflater: Option<Decompressor>,
    pending: Vec<u8>,
    inflated: u64,
}

impl<'a> Inflating<'a> {
    /// The reader over `file` of `part`, which holds a member index.
    pub fn new(file: &'a File, part: &Part) -> Self {
        Self {
            file,
            name: part.name.clone(),
            members: part.members.clone().unwrap_or_default().into_iter(),
            member: None,
            offset: 0,
            end: 0,
            inflater: None,
            pending: Vec::new(),
            inflated: 0,
        }
    }

    fn fail(&self, why: &str) -> ReadError {
        let section = self
            .member
            .as_ref()
            .map_or_else(String::new, |member| member.section.clone());
        let which = if section.is_empty() {
            "its header line".to_owned()
        } else {
            format!("the {} section", repr_str(&section))
        };
        ReadError::Head(format!("{}'s member of {which} {why}", self.name))
    }

    /// `readinto`: inflate into `buffer`, at most its length, and give how much; 0 once every member is read.
    pub fn read_into(&mut self, buffer: &mut [u8]) -> std::result::Result<usize, ReadError> {
        if buffer.is_empty() {
            return Ok(0);
        }
        loop {
            if self.inflater.is_none() {
                let Some(member) = self.members.next() else {
                    return Ok(0);
                };
                self.end = self.offset + member.length as u64;
                self.member = Some(member);
                self.inflater = Some(Decompressor::new(GZIP_WINDOW).map_err(ReadError::Io)?);
                self.inflated = 0;
            }
            let out = self.inflate(buffer.len())?;
            if !out.is_empty() {
                buffer[..out.len()].copy_from_slice(&out);
                return Ok(out.len());
            }
            self.finish()?;
        }
    }

    fn recorded(&self) -> (u64, u64) {
        self.member.as_ref().map_or((0, 0), |member| {
            (member.length as u64, member.inflated as u64)
        })
    }

    fn inflate(&mut self, limit: usize) -> std::result::Result<Vec<u8>, ReadError> {
        while !self.inflater.as_ref().is_some_and(Decompressor::eof) {
            if self.pending.is_empty() && self.offset < self.end {
                let mut piece = vec![0; INFLATE_READ_BYTES.min((self.end - self.offset) as usize)];
                let read = self
                    .file
                    .read_at(&mut piece, self.offset)
                    .map_err(|failure| ReadError::Io(failure.to_string()))?;
                if read == 0 {
                    return Err(self.fail(&format!(
                        "ends at byte {} of the file, inside its recorded length",
                        self.offset
                    )));
                }
                piece.truncate(read);
                self.pending = piece;
                self.offset += read as u64;
            }
            let inflater = self.inflater.as_mut().expect("a member is open");
            let out = match inflater.decompress(&self.pending, limit) {
                Ok(out) => out,
                Err(failure) => return Err(self.fail(&format!("does not inflate: {failure}"))),
            };
            self.pending = inflater.unconsumed_tail().to_vec();
            if !out.is_empty() {
                self.inflated += out.len() as u64;
                let (_, inflated) = self.recorded();
                if self.inflated > inflated {
                    return Err(self.fail(&format!(
                        "inflates past the {inflated} bytes its index records"
                    )));
                }
                return Ok(out);
            }
            if self.pending.is_empty() && self.offset >= self.end {
                break;
            }
        }
        Ok(Vec::new())
    }

    fn finish(&mut self) -> std::result::Result<(), ReadError> {
        let (length, inflated) = self.recorded();
        let inflater = self.inflater.as_ref().expect("a member is open");
        if !inflater.eof() {
            return Err(self.fail(&format!("runs past its recorded {length} bytes")));
        }
        if !inflater.unused_data().is_empty() || !self.pending.is_empty() || self.offset < self.end
        {
            return Err(self.fail(&format!("ends before its recorded {length} bytes")));
        }
        if self.inflated != inflated {
            return Err(self.fail(&format!(
                "inflates to {} bytes where its index records {inflated}",
                self.inflated
            )));
        }
        self.offset = self.end;
        self.inflater = None;
        Ok(())
    }
}

impl Read for Inflating<'_> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        self.read_into(buf).map_err(io::Error::other)
    }
}

/// `row_delta.part_stream`: a whole part's bytes from its start, a plain part's as they are and a part held as gzip members inflated ([`Inflating`]).
pub fn part_stream<'a>(file: &'a File, part: &Part) -> Box<dyn Read + 'a> {
    match part.members {
        None => Box::new(PartReader::new(file)),
        Some(_) => Box::new(Inflating::new(file, part)),
    }
}

/// What a writer raises, by the Python class it mirrors: a `ValueError` (segment rows out of place order, no room for places, a fraction `int` does not read at base 36), an `OSError`, or a value Python handles that the writer does not hold, which fails the Rust build and which no Python path takes.
#[derive(Debug)]
pub enum WriteError {
    Value(String),
    Io(io::Error),
    Unheld(String),
}

impl WriteError {
    /// The class Python raises, by its `__name__`; `Unheld` for what Python handles.
    pub fn class(&self) -> &'static str {
        match self {
            Self::Value(_) => "ValueError",
            Self::Io(_) => "OSError",
            Self::Unheld(_) => "Unheld",
        }
    }

    pub fn message(&self) -> String {
        match self {
            Self::Value(message) | Self::Unheld(message) => message.clone(),
            Self::Io(failure) => failure.to_string(),
        }
    }
}

impl From<io::Error> for WriteError {
    fn from(failure: io::Error) -> Self {
        Self::Io(failure)
    }
}

impl std::fmt::Display for WriteError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.class(), self.message())
    }
}

impl std::error::Error for WriteError {}

/// A sink that hashes and counts what passes through it into `inner` (`row_delta._Counting`), so a writer learns its part's size and sha256 without reading the part back.
struct Counting<W: Write> {
    inner: W,
    digest: sha2::Sha256,
    size: u64,
}

impl<W: Write> Counting<W> {
    fn new(inner: W) -> Self {
        Self {
            inner,
            digest: sha2::Sha256::new(),
            size: 0,
        }
    }

    /// The sink, and the part named `name` of what passed through, holding `rows` rows of `rows_bytes` bytes.
    fn part(self, name: &str, rows: i64, rows_bytes: i64) -> (W, Part) {
        let part = Part {
            name: name.to_owned(),
            size: self.size as i64,
            sha256: hex_digest(self.digest),
            rows,
            rows_bytes,
            settled: None,
            members: None,
        };
        (self.inner, part)
    }
}

impl<W: Write> Write for Counting<W> {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        let written = self.inner.write(data)?;
        self.digest.update(&data[..written]);
        self.size += written as u64;
        Ok(written)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}

/// `row_delta.write_part`: write one part, `name` under `directory`, through `sink`'s staging file, and return its [`Part`]. `fill` writes the part's rows into a gzip stream at level 1 and mtime 0 and returns the rows and their uncompressed bytes. The gzip header names `gzip_name`, by default the part's own name, never the staging file's; an empty `gzip_name` names no file. A failure in `fill` leaves the target as it was and removes the staging file.
pub fn write_part<E: From<io::Error>>(
    sink: &Sink,
    directory: &Path,
    name: &str,
    gzip_name: Option<&str>,
    fill: impl FnOnce(&mut dyn Write) -> std::result::Result<(i64, i64), E>,
) -> std::result::Result<Part, E> {
    let mut written = None;
    sink.replace_output_with::<E>(&directory.join(name), None, |staging| {
        let counting = Counting::new(BufWriter::new(File::create(staging)?));
        let mut stream = GzipWriter::new(counting, gzip_name.unwrap_or(name), PART_LEVEL, MTIME)?;
        let (rows, rows_bytes) = fill(&mut stream)?;
        let (mut file, part) = stream.finish()?.part(name, rows, rows_bytes);
        file.flush()?;
        written = Some(part);
        Ok(())
    })?;
    Ok(written.expect("a filled part"))
}

/// `row_delta.row_line`: a segment row as its line, the operation, the place's base row and fraction, and the payload, tab-separated.
pub fn row_line(row: &Row) -> Vec<u8> {
    let anchor = row.place.0.to_string();
    let mut line = Vec::with_capacity(4 + anchor.len() + row.place.1.len() + row.payload.len());
    line.push(row.op);
    line.push(b'\t');
    line.extend_from_slice(anchor.as_bytes());
    line.push(b'\t');
    line.extend_from_slice(row.place.1.as_bytes());
    line.push(b'\t');
    line.extend_from_slice(&row.payload);
    line
}

/// `row_delta.row_length`: the length of a segment row's line from its payload's length, the fraction counted in characters, as `len` counts a `str`.
pub fn row_length(place: &Place, payload_length: i64) -> i64 {
    4 + place.0.to_string().len() as i64 + place.1.chars().count() as i64 + payload_length
}

/// `row_delta.segment_header`: a segment's header line, its format, output, generation and number, and its merge when it does not merge by place, as `json.dumps` writes it.
pub fn segment_header(output: &str, generation: i64, number: i64, merge: &str) -> Vec<u8> {
    let mut header = vec![
        entry("format", PyValue::str(SEGMENT_FORMAT)),
        entry("output", PyValue::str(output)),
        entry("generation", PyValue::Int(generation)),
        entry("segment", PyValue::Int(number)),
    ];
    if merge != MERGE_PLACE {
        header.push(entry("merge", PyValue::str(merge)));
    }
    let mut line = json::dumps(&PyValue::Dict(header)).expect("a header of strings and ints");
    line.push('\n');
    line.into_bytes()
}

/// A place as an f-string writes its tuple, or `None`.
fn place_text(place: Option<&Place>) -> String {
    place.map_or_else(
        || "None".to_owned(),
        |(anchor, fraction)| format!("({anchor}, {})", repr_str(fraction)),
    )
}

/// `row_delta.write_segment`: write segment `number` of `generation` from `rows`, which must arrive in place order, one row a place, and return its [`Part`].
pub fn write_segment(
    sink: &Sink,
    directory: &Path,
    output: &str,
    generation: i64,
    number: i64,
    rows: impl IntoIterator<Item = Row>,
) -> std::result::Result<Part, WriteError> {
    let name = segment_name(output, generation, number);
    write_part(sink, directory, &name, None, |stream| {
        stream.write_all(&segment_header(output, generation, number, MERGE_PLACE))?;
        let (mut count, mut total) = (0, 0);
        let mut previous: Option<Place> = None;
        for row in rows {
            if let Some(previous) = &previous
                && row.place <= *previous
            {
                return Err(WriteError::Value(format!(
                    "segment rows arrive out of place order: {} after {}",
                    place_text(Some(&row.place)),
                    place_text(Some(previous))
                )));
            }
            let line = row_line(&row);
            stream.write_all(&line)?;
            count += 1;
            total += line.len() as i64;
            previous = Some(row.place);
        }
        Ok((count, total))
    })
}

/// One part written as its writer pushes its rows over a span of its own (`row_delta.StreamedPart` gzipped, the form the units index's segment takes), under its final name, which no head lists until the writer's commit: opened at the first [`StreamedPart::write`] through [`Sink::replacing`], so a part no row reaches is never made, with its header written first through a gzip stream at level 1 and mtime 0 naming the part. [`StreamedPart::close`] returns its [`Part`], or None when nothing was written; dropping it unclosed abandons it, removing its staging file. `rows` and `rows_bytes` count what follows the header.
pub struct StreamedPart {
    sink: Sink,
    directory: PathBuf,
    name: String,
    header: Vec<u8>,
    stream: Option<GzipWriter<Counting<Replacing>>>,
    pub rows: i64,
    pub rows_bytes: i64,
}

impl StreamedPart {
    pub fn new(sink: &Sink, directory: &Path, name: &str, header: Vec<u8>) -> Self {
        Self {
            sink: sink.clone(),
            directory: directory.to_owned(),
            name: name.to_owned(),
            header,
            stream: None,
            rows: 0,
            rows_bytes: 0,
        }
    }

    pub fn write(&mut self, data: &[u8]) -> io::Result<()> {
        let stream = match &mut self.stream {
            Some(stream) => stream,
            None => {
                let replacing = self.sink.replacing(&self.directory.join(&self.name))?;
                let mut stream =
                    GzipWriter::new(Counting::new(replacing), &self.name, PART_LEVEL, MTIME)?;
                stream.write_all(&self.header)?;
                self.stream.insert(stream)
            }
        };
        stream.write_all(data)?;
        self.rows += data.iter().filter(|&&byte| byte == b'\n').count() as i64;
        self.rows_bytes += data.len() as i64;
        Ok(())
    }

    /// Put the part in place and return it, or None when nothing was written.
    pub fn close(mut self) -> io::Result<Option<Part>> {
        let Some(stream) = self.stream.take() else {
            return Ok(None);
        };
        let (replacing, part) = stream
            .finish()?
            .part(&self.name, self.rows, self.rows_bytes);
        replacing.finish()?;
        Ok(Some(part))
    }
}

/// A whole number in base 36, its digits least significant first, with no zero at the top: zero has none.
type Base36 = Vec<u8>;

const DIGITS36: &[u8; 36] = b"0123456789abcdefghijklmnopqrstuvwxyz";
/// The base-36 digits of a run's length `places_between` spreads its fractions by, kept modulo `36 ** 8`, as `_digits(count + 1, 8)` keeps them.
const COUNT_DIGITS: u32 = 8;

fn trimmed(mut digits: Base36) -> Base36 {
    while digits.last() == Some(&0) {
        digits.pop();
    }
    digits
}

fn compare36(left: &[u8], right: &[u8]) -> std::cmp::Ordering {
    left.len()
        .cmp(&right.len())
        .then_with(|| left.iter().rev().cmp(right.iter().rev()))
}

fn add36(left: &[u8], right: &[u8]) -> Base36 {
    let mut sum = Vec::with_capacity(left.len().max(right.len()) + 1);
    let mut carry = 0;
    for at in 0..left.len().max(right.len()) {
        let total =
            left.get(at).copied().unwrap_or(0) + right.get(at).copied().unwrap_or(0) + carry;
        sum.push(total % 36);
        carry = total / 36;
    }
    sum.push(carry);
    trimmed(sum)
}

/// `left - right` for `left` at least `right`.
fn subtract36(left: &[u8], right: &[u8]) -> Base36 {
    let mut difference = Vec::with_capacity(left.len());
    let mut borrow = 0;
    for (at, &digit) in left.iter().enumerate() {
        let taken = right.get(at).copied().unwrap_or(0) + borrow;
        if digit >= taken {
            difference.push(digit - taken);
            borrow = 0;
        } else {
            difference.push(digit + 36 - taken);
            borrow = 1;
        }
    }
    trimmed(difference)
}

fn divide36(dividend: &[u8], divisor: u64) -> Base36 {
    let mut quotient = vec![0; dividend.len()];
    let mut remainder: u128 = 0;
    for at in (0..dividend.len()).rev() {
        remainder = remainder * 36 + u128::from(dividend[at]);
        quotient[at] = (remainder / u128::from(divisor)) as u8;
        remainder %= u128::from(divisor);
    }
    trimmed(quotient)
}

/// A signed whole number in base 36; zero is never negative.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Signed {
    negative: bool,
    magnitude: Base36,
}

impl Signed {
    fn new(negative: bool, magnitude: Base36) -> Self {
        let magnitude = trimmed(magnitude);
        Self {
            negative: negative && !magnitude.is_empty(),
            magnitude,
        }
    }

    fn negated(&self) -> Self {
        Self::new(!self.negative, self.magnitude.clone())
    }

    fn plus(&self, other: &Self) -> Self {
        if self.negative == other.negative {
            return Self::new(self.negative, add36(&self.magnitude, &other.magnitude));
        }
        match compare36(&self.magnitude, &other.magnitude) {
            std::cmp::Ordering::Less => Self::new(
                other.negative,
                subtract36(&other.magnitude, &self.magnitude),
            ),
            _ => Self::new(self.negative, subtract36(&self.magnitude, &other.magnitude)),
        }
    }

    /// `row_delta._digits(value, width)`: the value modulo `36 ** width` as `width` base-36 digits, the most significant first.
    fn digits(&self, width: usize) -> String {
        let mut low: Vec<u8> = (0..width)
            .map(|at| self.magnitude.get(at).copied().unwrap_or(0))
            .collect();
        if self.negative && low.iter().any(|&digit| digit != 0) {
            for digit in &mut low {
                *digit = 35 - *digit;
            }
            for digit in &mut low {
                if *digit == 35 {
                    *digit = 0;
                } else {
                    *digit += 1;
                    break;
                }
            }
        }
        low.iter()
            .rev()
            .map(|&digit| char::from(DIGITS36[usize::from(digit)]))
            .collect()
    }
}

/// `int(text, 36)` for a `str` ([`ascii_literal`], [`scan_int`]), refused with CPython's text.
fn base36_int(text: &str) -> std::result::Result<Signed, WriteError> {
    match scan_int(&ascii_literal(text), 36) {
        Ok((negative, digits)) => Ok(Signed::new(negative, digits.into_iter().rev().collect())),
        Err(NotInt::Long(digits)) => Err(WriteError::Value(int_digits_exceeded(digits))),
        Err(_) => Err(WriteError::Value(format!(
            "invalid literal for int() with base 36: {}",
            repr_str(text)
                .chars()
                .take(LITERAL_SHOWN)
                .collect::<String>()
        ))),
    }
}

/// `row_delta.places_between`: `count` places strictly between `low` and `high`, ascending, `low` None before the first base row and `high` None after the last. A fraction is base-36 digits read as a fraction with no trailing zero, read from its text as `int(text, 36)` reads it, and the fractions are spaced evenly over the gap, so a run of rows takes fractions a few digits longer than its bounds'. The arithmetic is exact at any width.
pub fn places_between(
    low: Option<&Place>,
    high: Option<&Place>,
    count: i64,
) -> std::result::Result<Vec<Place>, WriteError> {
    if count <= 0 {
        return Ok(Vec::new());
    }
    let (anchor, below) = low.map_or((-1, ""), |(anchor, fraction)| (*anchor, fraction.as_str()));
    let above = high
        .filter(|(at, _)| *at == anchor)
        .map(|(_, fraction)| fraction.as_str());
    if let Some((at, fraction)) = high
        && (*at, fraction.as_str()) <= (anchor, below)
    {
        return Err(WriteError::Value(format!(
            "no place lies between {} and {}",
            place_text(low),
            place_text(high)
        )));
    }
    let mut spread = (count as u128 + 1) % 36u128.pow(COUNT_DIGITS);
    let mut spread_digits = 0;
    while spread > 0 {
        spread /= 36;
        spread_digits += 1;
    }
    let width = below
        .chars()
        .count()
        .max(above.map_or(0, |above| above.chars().count()))
        + spread_digits
        + 1;
    let padded = |text: &str| format!("{text}{}", "0".repeat(width - text.chars().count()));
    let floor = if below.is_empty() {
        Signed::new(false, Vec::new())
    } else {
        base36_int(&padded(below))?
    };
    let ceiling = match above {
        Some(above) => base36_int(&padded(above))?,
        None => {
            let mut power = vec![0; width];
            power.push(1);
            Signed::new(false, power)
        }
    };
    let gap = ceiling.plus(&floor.negated());
    let step = if gap.negative {
        Vec::new()
    } else {
        divide36(&gap.magnitude, count as u64 + 1)
    };
    if step.is_empty() {
        return Err(WriteError::Value(format!(
            "no room for {count} places between {} and {}",
            place_text(low),
            place_text(high)
        )));
    }
    let step = Signed::new(false, step);
    let mut value = floor;
    let mut places = Vec::new();
    for _ in 0..count {
        value = value.plus(&step);
        let digits = value.digits(width);
        places.push((anchor, digits.trim_end_matches('0').to_owned()));
    }
    Ok(places)
}

/// `row_delta.writes_base`, the share rule: whether a writer writes a new base instead of its segment.
pub fn writes_base(
    segment_rows_bytes: i64,
    base_rows_bytes: i64,
    every_row_rederived: bool,
) -> bool {
    every_row_rederived || 2 * i128::from(segment_rows_bytes) > i128::from(base_rows_bytes)
}

/// `row_delta.fold_due`: whether the writer that finds `head` writes the materialized form as the next generation's base, once it holds the pass's fold. `cap` is the output's segment cap, `SEGMENT_CAP` unless the output names another.
pub fn fold_due(head: &Head, cap: i64) -> bool {
    let segment_rows_bytes: i128 = head
        .segments
        .iter()
        .map(|part| i128::from(part.rows_bytes))
        .sum();
    !head.segments.is_empty()
        && (segment_rows_bytes >= i128::from(head.base.rows_bytes)
            || head.segments.len() as i128 >= i128::from(cap))
}

/// `row_delta.fold_forced`: whether `head` lists `overrun` (`FOLD_OVERRUN`) segments past its cap, so its writer folds whether or not another output holds the pass's fold.
pub fn fold_forced(head: &Head, cap: i64, overrun: i64) -> bool {
    head.segments.len() as i128 >= i128::from(cap) + i128::from(overrun)
}

impl Head {
    /// `row_delta.with_segment`: this head with `part` as its next segment, its stamps replaced and its content moved.
    pub fn with_segment(&self, part: Part, stamps: PyValue) -> Self {
        let mut head = self.clone();
        head.stamps = stamps;
        head.segments.push(part);
        head.content += 1;
        head
    }
}

/// The pass's one fold (`row_delta.claim_fold`): under a cycle's run, the claim file in the cycle's directory, holding the run and the output that holds the fold, a claim another run left counting as none; outside a cycle, this process's own claim.
#[derive(Debug)]
pub struct FoldClaim {
    run: Option<String>,
    file: PathBuf,
    holder: Mutex<Option<String>>,
}

impl FoldClaim {
    /// The claim of the cycle's `run`, if any, whose claim file is `file`.
    pub fn new(run: Option<String>, file: PathBuf) -> Self {
        Self {
            run,
            file,
            holder: Mutex::new(None),
        }
    }

    /// Claim the pass's one fold for `output` and return whether `output` holds it: true when no other output claimed it this pass, or `output` did, and false when another did.
    pub fn claim(&self, sink: &Sink, output: &str) -> std::result::Result<bool, WriteError> {
        let Some(run) = self.run.as_deref().filter(|run| !run.is_empty()) else {
            let mut holder = self.holder.lock().unwrap_or_else(PoisonError::into_inner);
            return Ok(holder.get_or_insert_with(|| output.to_owned()) == output);
        };
        let held = match std::fs::read(&self.file) {
            Ok(raw) => match json::loads_bytes(&raw) {
                Ok(held) => Some(held),
                Err(failure) if json::unheld(&failure) => {
                    return Err(WriteError::Unheld(failure.0));
                }
                Err(_) => None,
            },
            Err(failure) if failure.kind() == io::ErrorKind::NotFound => None,
            Err(failure) => return Err(failure.into()),
        };
        if let Some(PyValue::Dict(entries)) = &held
            && is_str(get(entries, "run"), run)
        {
            return Ok(is_str(get(entries, "output"), output));
        }
        let claim = PyValue::Dict(vec![
            entry("run", PyValue::str(run)),
            entry("output", PyValue::str(output)),
        ]);
        let mut text = json::dumps(&claim).expect("a claim of strings");
        text.push('\n');
        sink.replace_output(&self.file, text.as_bytes(), false)?;
        Ok(true)
    }
}

/// `text` past a run of one or more decimal digits, as a `str` pattern's `\d+` matches them, or None when it does not start with one.
fn past_digits(text: &str) -> Option<&str> {
    let rest = text.trim_start_matches(|letter| decimal_value(letter).is_some());
    (rest.len() < text.len()).then_some(rest)
}

/// Whether `name` is a base or segment name of `output`, `<output>.<gen>.base` or `<output>.<gen>.<n>.delta`, as `row_delta._part_pattern` matches it whole.
fn is_part_name(name: &str, output: &str) -> bool {
    let Some(rest) = name
        .strip_prefix(output)
        .and_then(|rest| rest.strip_prefix('.'))
        .and_then(past_digits)
        .and_then(|rest| rest.strip_prefix('.'))
    else {
        return false;
    };
    rest == "base" || past_digits(rest) == Some(".delta")
}

/// `row_delta.remove_matching`: delete every file under `directory` whose name `matches` and `listed` does not hold, in name order, and return their names; with `links` false, a symbolic link is left where it is.
fn remove_matching(
    directory: &Path,
    matches: impl Fn(&str) -> bool,
    listed: &[&str],
    links: bool,
) -> io::Result<Vec<String>> {
    let mut names = Vec::new();
    for found in std::fs::read_dir(directory)? {
        if let Ok(name) = found?.file_name().into_string() {
            names.push(name);
        }
    }
    names.sort();
    let mut removed = Vec::new();
    for name in names {
        if !matches(&name) || listed.contains(&name.as_str()) {
            continue;
        }
        let path = directory.join(&name);
        if !links
            && path
                .symlink_metadata()
                .is_ok_and(|metadata| metadata.file_type().is_symlink())
        {
            continue;
        }
        match std::fs::remove_file(&path) {
            Err(failure) if failure.kind() != io::ErrorKind::NotFound => return Err(failure),
            _ => {}
        }
        removed.push(name);
    }
    Ok(removed)
}

/// `row_delta.remove_unlisted`: delete every base and segment of `output` under `directory` that `head` does not list (all of them when `head` is None) and return their names.
pub fn remove_unlisted(
    directory: &Path,
    output: &str,
    head: Option<&Head>,
) -> io::Result<Vec<String>> {
    let listed: Vec<&str> = head.map_or_else(Vec::new, |head| {
        head.parts().map(|part| part.name.as_str()).collect()
    });
    remove_matching(directory, |name| is_part_name(name, output), &listed, true)
}

/// `row_delta.remove_staging`: delete every staging file of `output`'s parts and head under `directory` and return their names; with `links` false, a symbolic link at a staging name is left.
pub fn remove_staging(directory: &Path, output: &str, links: bool) -> io::Result<Vec<String>> {
    let head = head_name(output);
    remove_matching(
        directory,
        |name| {
            name.strip_suffix(STAGING_SUFFIX)
                .is_some_and(|stem| is_part_name(stem, output) || stem == head)
        },
        &[],
        links,
    )
}

/// `row_delta.commit`: put `head` in place, written as `json.dumps(document, indent=1)` and a newline and kept when the file already holds it, the commit point, then delete the parts it does not list. Returns the names deleted.
pub fn commit(
    sink: &Sink,
    directory: &Path,
    head: &Head,
) -> std::result::Result<Vec<String>, WriteError> {
    let mut text =
        json::dumps_indent(&head.document()).map_err(|failure| WriteError::Unheld(failure.0))?;
    text.push('\n');
    sink.replace_output(&head_path(directory, &head.output), text.as_bytes(), true)?;
    Ok(remove_unlisted(directory, &head.output, Some(head))?)
}

fn hex_digest(digest: sha2::Sha256) -> String {
    digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn fail(what: &str, failure: impl std::fmt::Display) -> WireError {
    WireError(format!("cannot {what}: {failure}"))
}

fn tuple(items: Vec<PyValue>) -> PyValue {
    PyValue::Tuple(items)
}

fn tagged(tag: &str, items: impl IntoIterator<Item = PyValue>) -> PyValue {
    tuple(std::iter::once(PyValue::str(tag)).chain(items).collect())
}

fn raised_value(failure: &ReadError) -> PyValue {
    tagged(
        "raised",
        [
            PyValue::str(failure.class()),
            PyValue::str(failure.message()),
        ],
    )
}

/// A refusal as a line's value: `("refused", message)` for a `HeadError`, `("raised", class, message)` for anything else.
fn refused_value(failure: &ReadError) -> PyValue {
    match failure {
        ReadError::Head(message) => tagged("refused", [PyValue::str(message)]),
        other => raised_value(other),
    }
}

/// What reading `items` through gives: `("rows", count, sha256)` over each item's typed encoding and a newline, or `("raised", class, message)` for the first failure.
fn rows_outcome(items: impl Iterator<Item = std::result::Result<PyValue, ReadError>>) -> PyValue {
    let mut digest = sha2::Sha256::new();
    let mut count = 0;
    for item in items {
        match item {
            Ok(item) => {
                digest.update(item.to_typed().as_bytes());
                digest.update(b"\n");
                count += 1;
            }
            Err(failure) => return raised_value(&failure),
        }
    }
    tagged(
        "rows",
        [PyValue::Int(count), PyValue::str(hex_digest(digest))],
    )
}

fn op_bytes(op: u8) -> PyValue {
    PyValue::Bytes(vec![op])
}

/// The stage's blobs and the scratch directory its lines' files are set up in.
struct Workspace {
    blobs: PathBuf,
    read: HashMap<String, Vec<u8>>,
    scratch: PathBuf,
    cases: usize,
}

impl Workspace {
    fn blob(&mut self, digest: &str) -> Result<&[u8]> {
        if !self.read.contains_key(digest) {
            let path = self.blobs.join(digest);
            let bytes = std::fs::read(&path)
                .map_err(|failure| fail(&format!("read {}", path.display()), failure))?;
            if sha256_hex(&bytes) != digest {
                return error(format!(
                    "{} does not hold the bytes it is named for",
                    path.display()
                ));
            }
            self.read.insert(digest.to_owned(), bytes);
        }
        Ok(&self.read[digest])
    }

    /// A fresh directory for one line.
    fn directory(&mut self) -> Result<PathBuf> {
        self.cases += 1;
        let directory = self.scratch.join(self.cases.to_string());
        std::fs::create_dir_all(&directory)
            .map_err(|failure| fail("make a scratch directory", failure))?;
        Ok(directory)
    }

    /// Write the blob of `digest` to `path`.
    fn place(&mut self, digest: &str, path: &Path) -> Result<()> {
        let data = self.blob(digest)?.to_vec();
        std::fs::write(path, data)
            .map_err(|failure| fail(&format!("write {}", path.display()), failure))
    }
}

fn text<'a>(value: &'a PyValue, key: &str) -> Result<&'a str> {
    match value {
        PyValue::Str(text) => Ok(text),
        other => error(format!("{key}: expected a str, not {}", other.kind())),
    }
}

fn integer(value: &PyValue, key: &str) -> Result<i64> {
    match value {
        PyValue::Int(number) => Ok(*number),
        other => error(format!("{key}: expected an int, not {}", other.kind())),
    }
}

fn items<'a>(value: &'a PyValue, key: &str) -> Result<&'a [PyValue]> {
    match value {
        PyValue::Tuple(items) | PyValue::List(items) => Ok(items),
        other => error(format!("{key}: expected a tuple, not {}", other.kind())),
    }
}

fn arguments<'a, const COUNT: usize>(
    value: &'a PyValue,
    key: &str,
) -> Result<&'a [PyValue; COUNT]> {
    items(value, key)?
        .try_into()
        .map_err(|_| WireError(format!("{key}: the input is not {COUNT} arguments")))
}

/// A file a line names, `("file", sha256)`, or None for `("missing",)`.
fn held_file<'a>(value: &'a PyValue, key: &str) -> Result<Option<&'a str>> {
    match items(value, key)? {
        [PyValue::Str(tag)] if tag == "missing" => Ok(None),
        [PyValue::Str(tag), PyValue::Str(digest)] if tag == "file" => Ok(Some(digest)),
        _ => error(format!("{key}: not a held file: {}", value.to_typed())),
    }
}

/// One part's rows as the line's kind reads them (`_part_items`).
fn part_items<'a>(
    kind: &str,
    file: &'a File,
    output: &str,
    generation: i64,
    number: i64,
    key: &str,
) -> Result<Stream<'a, PyValue>> {
    Ok(match kind {
        "base" => Box::new(base_rows(file).map(|row| row.map(PyValue::Bytes))),
        MERGE_PLACE => Box::new(segment_rows(file, output, generation, number).map(|row| {
            row.map(|row| {
                tuple(vec![
                    op_bytes(row.op),
                    PyValue::Int(row.place.0),
                    PyValue::Str(row.place.1),
                    PyValue::Bytes(row.payload),
                ])
            })
        })),
        MERGE_KEY => Box::new(
            keyed_segment_rows(file, output, generation, number).map(|row| {
                row.map(|(op, payload)| tuple(vec![op_bytes(op), PyValue::Bytes(payload)]))
            }),
        ),
        other => return error(format!("{key}: no part kind {other:?}")),
    })
}

/// The `keys` of a `merge` line, each payload's key as Python's key function gave it.
fn payload_keys(value: &PyValue, key: &str) -> Result<HashMap<Vec<u8>, Key>> {
    let mut keys = HashMap::new();
    for pair in items(value, key)? {
        let [PyValue::Bytes(payload), fields] = items(pair, key)? else {
            return error(format!(
                "{key}: not a payload and its key: {}",
                pair.to_typed()
            ));
        };
        let fields = items(fields, key)?
            .iter()
            .map(|field| match field {
                PyValue::Bytes(field) => Ok(field.clone()),
                other => error(format!("{key}: a key field is {}", other.kind())),
            })
            .collect::<Result<Key>>()?;
        keys.insert(payload.clone(), fields);
    }
    Ok(keys)
}

/// The materialized rows of a `merge` line's parts (`_merged_items`).
fn merged(
    merge: &str,
    files: &[File],
    output: &str,
    generation: i64,
    keys: &HashMap<Vec<u8>, Key>,
    key: &str,
) -> Result<PyValue> {
    let base = base_rows(&files[0]);
    Ok(match merge {
        MERGE_PLACE => {
            let segments = files[1..]
                .iter()
                .enumerate()
                .map(|(index, file)| {
                    Box::new(segment_rows(file, output, generation, index as i64 + 1))
                        as Stream<'_, Row>
                })
                .collect();
            rows_outcome(merge_placed(base, segments).map(|row| {
                row.map(|((anchor, fraction), payload)| {
                    tuple(vec![
                        tuple(vec![PyValue::Int(anchor), PyValue::Str(fraction)]),
                        PyValue::Bytes(payload),
                    ])
                })
            }))
        }
        MERGE_KEY => {
            let looked_up = |payload: &[u8]| {
                keys.get(payload).cloned().ok_or_else(|| {
                    ReadError::Unheld(format!("the line holds no key for {}", bytes_repr(payload)))
                })
            };
            let segments = files[1..]
                .iter()
                .enumerate()
                .map(|(index, file)| {
                    Box::new(keyed_segment_rows(
                        file,
                        output,
                        generation,
                        index as i64 + 1,
                    )) as Stream<'_, (u8, Vec<u8>)>
                })
                .collect();
            rows_outcome(
                merge_keyed(base, segments, &looked_up, &looked_up)
                    .map(|row| row.map(PyValue::Bytes)),
            )
        }
        other => return error(format!("{key}: no merge {other:?}")),
    })
}

/// One line's answer, by its form.
fn answer(record: &Value, key: &str, workspace: &mut Workspace) -> Result<PyValue> {
    let input = PyValue::from_typed(&record["input"])
        .map_err(|failure| WireError(format!("{key}: {failure}")))?;
    let open = |path: &Path| {
        File::open(path).map_err(|failure| fail(&format!("open {}", path.display()), failure))
    };
    match record["form"].as_str() {
        Some("read-head") => {
            let [output, held] = arguments(&input, key)?;
            let output = text(output, key)?;
            let directory = workspace.directory()?;
            match items(held, key)? {
                [PyValue::Str(tag)] if tag == "missing" => {}
                [PyValue::Str(tag), PyValue::Bytes(data)] if tag == "file" => {
                    let path = head_path(&directory, output);
                    std::fs::write(&path, data)
                        .map_err(|failure| fail(&format!("write {}", path.display()), failure))?;
                }
                _ => return error(format!("{key}: not a held head: {}", held.to_typed())),
            }
            Ok(match read_head(&directory, output) {
                Ok(None) => tagged("none", []),
                Ok(Some(head)) => tagged("head", [head.document()]),
                Err(failure) => refused_value(&failure),
            })
        }
        Some("check-segment-header") => {
            let [line, output, generation, number, merge] = arguments(&input, key)?;
            let PyValue::Bytes(line) = line else {
                return error(format!("{key}: the header line is {}", line.kind()));
            };
            Ok(
                match check_segment_header(
                    line,
                    text(output, key)?,
                    integer(generation, key)?,
                    integer(number, key)?,
                    text(merge, key)?,
                ) {
                    Ok(()) => tagged("checked", []),
                    Err(failure) => refused_value(&failure),
                },
            )
        }
        Some("open-parts") => {
            let [document, sha256, files] = arguments(&input, key)?;
            let PyValue::Bool(sha256) = sha256 else {
                return error(format!("{key}: sha256 is {}", sha256.kind()));
            };
            let output = match document {
                PyValue::Dict(entries) => {
                    text(get(entries, "output").unwrap_or(&PyValue::None), key)?
                }
                other => return error(format!("{key}: the head is {}", other.kind())),
            };
            let head = Head::from_document(document, output).map_err(|failure| {
                WireError(format!("{key}: the head does not read: {failure}"))
            })?;
            let directory = workspace.directory()?;
            for file in items(files, key)? {
                let [name, held] = arguments(file, key)?;
                if let Some(digest) = held_file(held, key)? {
                    workspace.place(digest, &directory.join(text(name, key)?))?;
                }
            }
            Ok(match open_parts(&directory, &head, *sha256) {
                Ok(_) => tagged("opened", []),
                Err(failure) => refused_value(&failure),
            })
        }
        Some("part-rows") => {
            let [kind, output, generation, number, digest] = arguments(&input, key)?;
            let path = workspace.directory()?.join("part");
            workspace.place(text(digest, key)?, &path)?;
            let file = open(&path)?;
            let rows = part_items(
                text(kind, key)?,
                &file,
                text(output, key)?,
                integer(generation, key)?,
                integer(number, key)?,
                key,
            )?;
            Ok(rows_outcome(rows))
        }
        Some("merge") => {
            let [merge, output, generation, digests, keys] = arguments(&input, key)?;
            let directory = workspace.directory()?;
            let mut files = Vec::new();
            for (index, digest) in items(digests, key)?.iter().enumerate() {
                let path = directory.join(index.to_string());
                workspace.place(text(digest, key)?, &path)?;
                files.push(open(&path)?);
            }
            if files.is_empty() {
                return error(format!("{key}: a merge of no parts"));
            }
            merged(
                text(merge, key)?,
                &files,
                text(output, key)?,
                integer(generation, key)?,
                &payload_keys(keys, key)?,
                key,
            )
        }
        Some("inflating") => {
            let [document, held] = arguments(&input, key)?;
            let part = Part::parse(document).map_err(|failure| {
                WireError(format!("{key}: the part does not read: {failure}"))
            })?;
            let Some(digest) = held_file(held, key)? else {
                return error(format!("{key}: the part is missing"));
            };
            let path = workspace.directory()?.join("part");
            workspace.place(digest, &path)?;
            let file = open(&path)?;
            let mut reader = Inflating::new(&file, &part);
            let mut buffer = vec![0; INFLATE_READ_BYTES];
            let mut digest = sha2::Sha256::new();
            let mut length = 0;
            loop {
                match reader.read_into(&mut buffer) {
                    Ok(0) => break,
                    Ok(count) => {
                        digest.update(&buffer[..count]);
                        length += count as i64;
                    }
                    Err(failure) => return Ok(raised_value(&failure)),
                }
            }
            Ok(tagged(
                "bytes",
                [PyValue::str(hex_digest(digest)), PyValue::Int(length)],
            ))
        }
        other => error(format!("{key}: no row-delta-read form {other:?}")),
    }
}

/// The `row-delta-read` stage: for each line of `stage_file`, set up the files its input names, from the bytes it holds or the blobs it names by sha256 in [`STAGE_BLOBS`] beside the stage file, in a scratch directory, read them again by its `form`, and write one `{"key", "value"}` line in the typed encoding: `read-head` reads a head ([`read_head`]) to `("none",)`, `("head", document)` or `("refused", message)`; `check-segment-header` checks a header line ([`check_segment_header`]) to `("checked",)` or a refusal; `open-parts` opens a head's parts ([`open_parts`]) to `("opened",)` or a refusal; `part-rows` reads one part's rows by its kind (`base`, `place` or `key`) and `merge` merges a base and its segments by place or by key, the payloads' keys looked up in the line, each to `("rows", count, sha256)` over its items' typed encodings or `("raised", class, message)`; and `inflating` reads a part held as gzip members ([`Inflating`]) a buffer of [`INFLATE_READ_BYTES`] at a time to `("bytes", sha256, length)` or what it raised.
pub fn stage_row_delta_read(stage_file: &Path, out: &mut impl Write) -> Result<()> {
    answer_stage(stage_file, STAGE_BLOBS, "read", out, answer)
}

/// Answer every line of `stage_file` with `answer` over a [`Workspace`] whose blobs are the directory `blobs` beside the stage file and whose scratch is a directory of its own, removed when the stage ends.
fn answer_stage(
    stage_file: &Path,
    blobs: &str,
    label: &str,
    out: &mut impl Write,
    mut answer: impl FnMut(&Value, &str, &mut Workspace) -> Result<PyValue>,
) -> Result<()> {
    let scratch = std::env::temp_dir().join(format!(
        "ams-review-row-delta-{label}-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&scratch);
    std::fs::create_dir_all(&scratch)
        .map_err(|failure| fail("make the stage's scratch", failure))?;
    let mut workspace = Workspace {
        blobs: stage_file
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(blobs),
        read: HashMap::new(),
        scratch: scratch.clone(),
        cases: 0,
    };
    let answered = answer_lines(stage_file, out, |record, key| {
        answer(record, key, &mut workspace)
    });
    let _ = std::fs::remove_dir_all(&scratch);
    answered
}

fn answer_lines(
    stage_file: &Path,
    out: &mut impl Write,
    mut answer: impl FnMut(&Value, &str) -> Result<PyValue>,
) -> Result<()> {
    let file = File::open(stage_file)
        .map_err(|failure| fail(&format!("read {}", stage_file.display()), failure))?;
    for line in BufReader::new(file).lines() {
        let line =
            line.map_err(|failure| fail(&format!("read {}", stage_file.display()), failure))?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| WireError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return error(format!("a stage line has no key: {line:.200}"));
        };
        let value = answer(&record, key)?;
        writeln!(
            out,
            "{{\"key\":{},\"value\":{}}}",
            json_string(key),
            value.to_typed()
        )
        .map_err(|failure| fail("write a stage line", failure))?;
    }
    Ok(())
}

/// The `row-delta-write` stage's directory, beside its stage file, of the files its lines name by sha256.
pub const WRITE_STAGE_BLOBS: &str = "row-delta-write.blobs";

/// The segment cap and the fold overrun the build input's vocabulary gives (`row_delta.SEGMENT_CAP`, `row_delta.FOLD_OVERRUN`).
#[derive(Clone, Copy, Debug)]
struct Folding {
    cap: i64,
    overrun: i64,
}

impl Folding {
    fn of(input: &BuildInput) -> Result<Self> {
        let constant = |name: &str| match input.constant(name) {
            Some(PyValue::Int(value)) => Ok(*value),
            _ => error(format!("the build input's vocabulary holds no int {name}")),
        };
        Ok(Self {
            cap: constant("rebuild.pipeline.row_delta.SEGMENT_CAP")?,
            overrun: constant("rebuild.pipeline.row_delta.FOLD_OVERRUN")?,
        })
    }
}

/// What a file holds now: `("missing",)`, or `("file", sha256)`.
fn held_now(path: &Path) -> Result<PyValue> {
    match std::fs::read(path) {
        Ok(data) => Ok(tagged("file", [PyValue::str(sha256_hex(&data))])),
        Err(failure) if failure.kind() == io::ErrorKind::NotFound => Ok(tagged("missing", [])),
        Err(failure) => Err(fail(&format!("read {}", path.display()), failure)),
    }
}

/// A file's inode and modification time, or None when there is none, which tell a kept file from one put in its place.
fn identity(path: &Path) -> Option<(u64, i64, i64)> {
    use std::os::unix::fs::MetadataExt as _;
    std::fs::metadata(path)
        .ok()
        .map(|metadata| (metadata.ino(), metadata.mtime(), metadata.mtime_nsec()))
}

/// Put what a line's `held` names at `path`: nothing for `("missing",)`, or the blob of `("file", sha256)`.
fn place_held(workspace: &mut Workspace, held: &PyValue, path: &Path, key: &str) -> Result<()> {
    match held_file(held, key)? {
        Some(digest) => workspace.place(digest, path),
        None => Ok(()),
    }
}

/// Make the directory entries a line names, each `(name, kind)`: an empty file, a directory, or a symbolic link to the null device.
fn place_entries(directory: &Path, entries: &PyValue, key: &str) -> Result<()> {
    for found in items(entries, key)? {
        let [name, kind] = arguments(found, key)?;
        let path = directory.join(text(name, key)?);
        match text(kind, key)? {
            "file" => std::fs::write(&path, b""),
            "dir" => std::fs::create_dir(&path),
            "link" => std::os::unix::fs::symlink("/dev/null", &path),
            other => return error(format!("{key}: no entry kind {other:?}")),
        }
        .map_err(|failure| fail(&format!("make {}", path.display()), failure))?;
    }
    Ok(())
}

fn optional_place(value: &PyValue, key: &str) -> Result<Option<Place>> {
    match value {
        PyValue::None => Ok(None),
        place => {
            let [anchor, fraction] = arguments(place, key)?;
            Ok(Some((
                integer(anchor, key)?,
                text(fraction, key)?.to_owned(),
            )))
        }
    }
}

fn place_value(place: &Place) -> PyValue {
    tuple(vec![PyValue::Int(place.0), PyValue::str(&place.1)])
}

fn one_op(value: &PyValue, key: &str) -> Result<u8> {
    match value {
        PyValue::Bytes(op) if op.len() == 1 => Ok(op[0]),
        other => error(format!("{key}: an operation is {}", other.to_typed())),
    }
}

fn byte_string<'a>(value: &'a PyValue, key: &str) -> Result<&'a [u8]> {
    match value {
        PyValue::Bytes(data) => Ok(data),
        other => error(format!("{key}: expected bytes, not {}", other.kind())),
    }
}

fn flag(value: &PyValue, key: &str) -> Result<bool> {
    match value {
        PyValue::Bool(flag) => Ok(*flag),
        other => error(format!("{key}: expected a bool, not {}", other.kind())),
    }
}

fn optional_text<'a>(value: &'a PyValue, key: &str) -> Result<Option<&'a str>> {
    match value {
        PyValue::None => Ok(None),
        other => text(other, key).map(Some),
    }
}

/// A head a line holds as its document, read for its own output.
fn head_of(document: &PyValue, key: &str) -> Result<Head> {
    let output = match document {
        PyValue::Dict(entries) => text(get(entries, "output").unwrap_or(&PyValue::None), key)?,
        other => return error(format!("{key}: the head is {}", other.kind())),
    };
    Head::from_document(document, output)
        .map_err(|failure| WireError(format!("{key}: the head does not read: {failure}")))
}

fn raised_write(failure: &WriteError, after: impl IntoIterator<Item = PyValue>) -> PyValue {
    tagged(
        "raised",
        [
            PyValue::str(failure.class()),
            PyValue::str(failure.message()),
        ]
        .into_iter()
        .chain(after),
    )
}

/// A written part's outcome: `("part", document, target, staging)`, or what the write raised with the target and its staging file as it left them.
fn written(outcome: std::result::Result<Part, WriteError>, target: &Path) -> Result<PyValue> {
    let after = [held_now(target)?, held_now(&staging_path(target))?];
    Ok(match outcome {
        Ok(part) => tagged("part", std::iter::once(part.document()).chain(after)),
        Err(failure) => raised_write(&failure, after),
    })
}

/// One `row-delta-write` line's answer, by its form.
fn write_answer(
    record: &Value,
    key: &str,
    workspace: &mut Workspace,
    folding: Folding,
) -> Result<PyValue> {
    let input = PyValue::from_typed(&record["input"])
        .map_err(|failure| WireError(format!("{key}: {failure}")))?;
    let sink = Sink::new(None);
    match record["form"].as_str() {
        Some("places-between") => {
            let [low, high, count] = arguments(&input, key)?;
            let (low, high) = (optional_place(low, key)?, optional_place(high, key)?);
            Ok(
                match places_between(low.as_ref(), high.as_ref(), integer(count, key)?) {
                    Ok(places) => {
                        tagged("places", [tuple(places.iter().map(place_value).collect())])
                    }
                    Err(failure) => raised_write(&failure, []),
                },
            )
        }
        Some("row-line") => {
            let [op, anchor, fraction, payload] = arguments(&input, key)?;
            let row = Row {
                op: one_op(op, key)?,
                place: (integer(anchor, key)?, text(fraction, key)?.to_owned()),
                payload: byte_string(payload, key)?.to_vec(),
            };
            Ok(tagged("line", [PyValue::Bytes(row_line(&row))]))
        }
        Some("row-length") => {
            let [op, place, length] = arguments(&input, key)?;
            one_op(op, key)?;
            let Some(place) = optional_place(place, key)? else {
                return error(format!("{key}: a row with no place"));
            };
            Ok(tagged(
                "length",
                [PyValue::Int(row_length(&place, integer(length, key)?))],
            ))
        }
        Some("segment-header") => {
            let [output, generation, number, merge] = arguments(&input, key)?;
            Ok(tagged(
                "header",
                [PyValue::Bytes(segment_header(
                    text(output, key)?,
                    integer(generation, key)?,
                    integer(number, key)?,
                    text(merge, key)?,
                ))],
            ))
        }
        Some("write-part") => {
            let [name, gzip_name, held, plain, cuts, returned, failure] = arguments(&input, key)?;
            let name = text(name, key)?;
            let directory = workspace.directory()?;
            let target = directory.join(name);
            place_held(workspace, held, &target, key)?;
            let plain = workspace.blob(text(plain, key)?)?.to_vec();
            let cuts = items(cuts, key)?
                .iter()
                .map(|cut| integer(cut, key).map(|cut| cut as usize))
                .collect::<Result<Vec<_>>>()?;
            if cuts.iter().sum::<usize>() != plain.len() {
                return error(format!("{key}: the cuts do not cover the part's bytes"));
            }
            let [rows, rows_bytes] = arguments(returned, key)?;
            let returned = (integer(rows, key)?, integer(rows_bytes, key)?);
            let failure = optional_text(failure, key)?;
            let outcome = write_part(
                &sink,
                &directory,
                name,
                optional_text(gzip_name, key)?,
                |stream| {
                    let mut at = 0;
                    for cut in &cuts {
                        stream.write_all(&plain[at..at + cut])?;
                        at += cut;
                    }
                    match failure {
                        Some(message) => Err(WriteError::Value(message.to_owned())),
                        None => Ok(returned),
                    }
                },
            );
            written(outcome, &target)
        }
        Some("write-segment") => {
            let [output, generation, number, held, rows] = arguments(&input, key)?;
            let (output, generation, number) = (
                text(output, key)?,
                integer(generation, key)?,
                integer(number, key)?,
            );
            let directory = workspace.directory()?;
            let target = directory.join(segment_name(output, generation, number));
            place_held(workspace, held, &target, key)?;
            let rows = items(rows, key)?
                .iter()
                .map(|row| {
                    let [op, anchor, fraction, payload] = arguments(row, key)?;
                    Ok(Row {
                        op: one_op(op, key)?,
                        place: (integer(anchor, key)?, text(fraction, key)?.to_owned()),
                        payload: byte_string(payload, key)?.to_vec(),
                    })
                })
                .collect::<Result<Vec<_>>>()?;
            let outcome = write_segment(&sink, &directory, output, generation, number, rows);
            written(outcome, &target)
        }
        Some("streamed-part") => {
            let [name, header, held, data, ending] = arguments(&input, key)?;
            let name = text(name, key)?;
            let directory = workspace.directory()?;
            let target = directory.join(name);
            place_held(workspace, held, &target, key)?;
            let mut part =
                StreamedPart::new(&sink, &directory, name, byte_string(header, key)?.to_vec());
            if let Some(digest) = optional_text(data, key)? {
                let data = workspace.blob(digest)?.to_vec();
                part.write(&data)
                    .map_err(|failure| fail("write a streamed part", failure))?;
            }
            let after = |target: &Path| -> Result<[PyValue; 2]> {
                Ok([held_now(target)?, held_now(&staging_path(target))?])
            };
            match text(ending, key)? {
                "close" => {
                    let closed = part
                        .close()
                        .map_err(|failure| fail("close a streamed part", failure))?;
                    Ok(match closed {
                        Some(part) => tagged(
                            "part",
                            std::iter::once(part.document()).chain(after(&target)?),
                        ),
                        None => tagged("none", after(&target)?),
                    })
                }
                "abandon" => {
                    drop(part);
                    Ok(tagged("abandoned", after(&target)?))
                }
                other => error(format!("{key}: no ending {other:?}")),
            }
        }
        Some("commit") => {
            let [document, entries, held] = arguments(&input, key)?;
            let head = head_of(document, key)?;
            let directory = workspace.directory()?;
            place_entries(&directory, entries, key)?;
            let path = head_path(&directory, &head.output);
            place_held(workspace, held, &path, key)?;
            let before = identity(&path);
            let removed = commit(&sink, &directory, &head)
                .map_err(|failure| WireError(format!("{key}: the commit failed: {failure}")))?;
            let kept = before.is_some() && before == identity(&path);
            Ok(tagged(
                "committed",
                [
                    PyValue::str_tuple(&removed),
                    held_now(&path)?,
                    PyValue::Bool(kept),
                ],
            ))
        }
        Some("remove-unlisted") => {
            let [output, document, entries] = arguments(&input, key)?;
            let head = match document {
                PyValue::None => None,
                document => Some(head_of(document, key)?),
            };
            let directory = workspace.directory()?;
            place_entries(&directory, entries, key)?;
            let removed = remove_unlisted(&directory, text(output, key)?, head.as_ref())
                .map_err(|failure| fail("remove the unlisted parts", failure))?;
            Ok(tagged("removed", [PyValue::str_tuple(&removed)]))
        }
        Some("remove-staging") => {
            let [output, links, entries] = arguments(&input, key)?;
            let directory = workspace.directory()?;
            place_entries(&directory, entries, key)?;
            let removed = remove_staging(&directory, text(output, key)?, flag(links, key)?)
                .map_err(|failure| fail("remove the staging files", failure))?;
            Ok(tagged("removed", [PyValue::str_tuple(&removed)]))
        }
        Some("writes-base") => {
            let [segment, base, rederived] = arguments(&input, key)?;
            Ok(tagged(
                "decided",
                [PyValue::Bool(writes_base(
                    integer(segment, key)?,
                    integer(base, key)?,
                    flag(rederived, key)?,
                ))],
            ))
        }
        Some(form @ ("fold-due" | "fold-forced")) => {
            let [document, cap] = arguments(&input, key)?;
            let head = head_of(document, key)?;
            let cap = match cap {
                PyValue::None => folding.cap,
                cap => integer(cap, key)?,
            };
            let decided = if form == "fold-due" {
                fold_due(&head, cap)
            } else {
                fold_forced(&head, cap, folding.overrun)
            };
            Ok(tagged("decided", [PyValue::Bool(decided)]))
        }
        Some("claim-fold") => {
            let [output, run, held, holder] = arguments(&input, key)?;
            let file = workspace.directory()?.join("fold-claim.json");
            place_held(workspace, held, &file, key)?;
            let claim = FoldClaim {
                run: optional_text(run, key)?.map(str::to_owned),
                file: file.clone(),
                holder: Mutex::new(optional_text(holder, key)?.map(str::to_owned)),
            };
            let claimed = claim
                .claim(&sink, text(output, key)?)
                .map_err(|failure| WireError(format!("{key}: the claim failed: {failure}")))?;
            let holder = claim
                .holder
                .into_inner()
                .unwrap_or_else(PoisonError::into_inner);
            Ok(tagged(
                "claimed",
                [
                    PyValue::Bool(claimed),
                    held_now(&file)?,
                    PyValue::optional_str(holder.as_deref()),
                ],
            ))
        }
        Some("with-segment") => {
            let [document, part, stamps] = arguments(&input, key)?;
            let head = head_of(document, key)?;
            let part = Part::parse(part).map_err(|failure| {
                WireError(format!("{key}: the part does not read: {failure}"))
            })?;
            Ok(tagged(
                "head",
                [head.with_segment(part, stamps.clone()).document()],
            ))
        }
        other => error(format!("{key}: no row-delta-write form {other:?}")),
    }
}

/// The `row-delta-write` stage: for each line of `stage_file`, set up the files and directory entries its input names, from the blobs it names by sha256 in [`WRITE_STAGE_BLOBS`] beside the stage file, in a scratch directory, make the writer's call again by its `form`, and write one `{"key", "value"}` line in the typed encoding: `places-between` cuts places ([`places_between`]); `row-line`, `row-length` and `segment-header` build a row's line, its length and a segment's header line; `write-part` (its plain bytes written in the line's cuts, then its rows returned or its failure raised), `write-segment` and `streamed-part` (closed or abandoned) write a part and answer its document, or what the write raised, with what the target and its staging file then hold; `commit` commits a head over the directory entries the line names, answering the parts it removed, the head file and whether it was kept; `remove-unlisted` and `remove-staging` answer what they remove; `writes-base`, `fold-due` and `fold-forced` decide, a cap of None read as the build input's `SEGMENT_CAP` beside its `FOLD_OVERRUN`; `claim-fold` claims the fold under the line's run, claim file and process holder ([`FoldClaim`]); and `with-segment` adds a segment to a head.
pub fn stage_row_delta_write(
    input_path: &Path,
    stage_file: &Path,
    out: &mut impl Write,
) -> Result<()> {
    let folding = Folding::of(&BuildInput::read(input_path)?)?;
    answer_stage(
        stage_file,
        WRITE_STAGE_BLOBS,
        "write",
        out,
        |record, key, workspace| write_answer(record, key, workspace, folding),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rows(lines: &[&[u8]]) -> Vec<Vec<u8>> {
        lines.iter().map(|line| line.to_vec()).collect()
    }

    #[test]
    fn int_reads_what_cpython_reads_at_base_ten() {
        for (literal, value) in [
            (" 7 ".as_bytes(), 7),
            (b"+2", 2),
            (b"-0", 0),
            (b"1_0", 10),
            (b"007", 7),
            (b"\x0b3\x0c", 3),
            (b"-9223372036854775808", i64::MIN),
        ] {
            assert_eq!(parse_int(literal), Ok(value), "{literal:?}");
        }
        for literal in [
            "".as_bytes(),
            b"x",
            b"1__0",
            b"_1",
            b"1_",
            b"+_1",
            b"- 5",
            b"1 2",
            b"\x1c5",
            b"5\0",
        ] {
            assert_eq!(parse_int(literal), Err(NotInt::Invalid), "{literal:?}");
        }
        assert_eq!(parse_int(b"9223372036854775808"), Err(NotInt::Wide));
        let long = [b'1'; INT_MAX_STR_DIGITS + 1];
        assert_eq!(parse_int(&long[1..]), Err(NotInt::Wide));
        assert_eq!(parse_int(&long), Err(NotInt::Long(INT_MAX_STR_DIGITS + 1)));
        assert_eq!(parse_int(&[&long[..], b"x"].concat()), Err(NotInt::Invalid));
        assert_eq!(
            parse_int(&[&long[..], b" \0x"].concat()),
            Err(NotInt::Long(INT_MAX_STR_DIGITS + 1))
        );
        assert_eq!(
            parse_int(&[&long[..], b"x\0"].concat()),
            Err(NotInt::Invalid)
        );
        let quoted = [&[b'x'; 250][..], b"'"].concat();
        assert_eq!(
            bytes_int(&quoted),
            Err(ReadError::value(format!(
                "invalid literal for int() with base 10: b'{}",
                "x".repeat(LITERAL_SHOWN - 2)
            )))
        );
        assert!(matches!(py_int(&PyValue::str("\u{a0}5\u{2003}")), Ok(5)));
        assert!(matches!(py_int(&PyValue::str("\u{663}_\u{664}")), Ok(34)));
        assert!(matches!(py_int(&PyValue::Bool(true)), Ok(1)));
    }

    #[test]
    fn a_head_refuses_as_parse_head_refuses() {
        let refused = |raw: &str| match parse_head(raw.as_bytes(), "out") {
            Err(ReadError::Head(message)) => message,
            other => panic!("{raw} read as {other:?}"),
        };
        assert_eq!(refused("{"), "out.head does not parse");
        assert_eq!(
            refused("[]"),
            "out.head is not an ams-row-delta-head/1 head"
        );
        let head = r#"{"format": "ams-row-delta-head/1", "output": "out", "generation": "1", "content": 0, "stamps": {}, "segments": [], "base": {"name": "out.1.base", "size": null, "sha256": "", "rows": 0, "rows_bytes": 0}}"#;
        assert_eq!(
            refused(head),
            "a head lists a part it does not describe: TypeError(\"int() argument must be a string, a bytes-like object or a real number, not 'NoneType'\")"
        );
        let read = parse_head(head.replace("null", "\" 3 \"").as_bytes(), "out").expect("read");
        assert_eq!((read.generation, read.base.size), (1, 3));
        assert!(matches!(
            parse_head(head.replace("null", "1.5").as_bytes(), "out"),
            Err(ReadError::Unheld(_))
        ));
    }

    #[test]
    fn a_header_check_compares_a_bool_as_its_int() {
        let header = br#"{"format": "ams-row-delta-segment/1", "output": "out", "generation": true, "segment": true}"#;
        assert_eq!(
            check_segment_header(header, "out", 1, 1, MERGE_PLACE),
            Ok(())
        );
        assert_eq!(
            check_segment_header(header, "out", 1, 1, MERGE_KEY),
            Err(ReadError::Head(
                "out merges by key, and segment 1's header says it merges by place".to_owned()
            ))
        );
    }

    #[test]
    fn places_are_cut_as_python_cuts_them() {
        let place = |anchor: i64, fraction: &str| (anchor, fraction.to_owned());
        let cut = |low: Option<Place>, high: Option<Place>, count: i64| {
            places_between(low.as_ref(), high.as_ref(), count)
                .map(|places| {
                    places
                        .into_iter()
                        .map(|(_, fraction)| fraction)
                        .collect::<Vec<_>>()
                })
                .map_err(|failure| failure.message())
        };
        assert_eq!(cut(None, None, 2), Ok(vec!["c".to_owned(), "o".to_owned()]));
        assert_eq!(
            cut(Some(place(5, "-1")), Some(place(5, "0")), 3),
            Ok(vec!["zz9".to_owned(), "zzi".to_owned(), "zzr".to_owned()])
        );
        let long = "1".repeat(30);
        assert_eq!(
            cut(Some(place(5, &long)), None, 1),
            Ok(vec!["i".repeat(31)])
        );
        assert_eq!(
            cut(Some(place(5, "1")), Some(place(5, "1_")), 1),
            Err("no room for 1 places between (5, '1') and (5, '1_')".to_owned())
        );
        assert_eq!(
            cut(Some(place(5, "!")), None, 1),
            Err("invalid literal for int() with base 36: '!00'".to_owned())
        );
    }

    #[test]
    fn the_merges_take_the_latest_part_at_each_place_and_key() {
        let base = || rows(&[b"a\n", b"b\n", b"c\n"]).into_iter().map(Ok);
        let first: Stream<'_, Row> = Box::new(
            [
                Row {
                    op: TOMBSTONE,
                    place: (0, String::new()),
                    payload: b"x\n".to_vec(),
                },
                Row {
                    op: UPSERT,
                    place: (1, "5".to_owned()),
                    payload: b"b5\n".to_vec(),
                },
            ]
            .into_iter()
            .map(Ok),
        );
        let second: Stream<'_, Row> = Box::new(
            [Row {
                op: UPSERT,
                place: (0, String::new()),
                payload: b"a2\n".to_vec(),
            }]
            .into_iter()
            .map(Ok),
        );
        let placed: Vec<Vec<u8>> = merge_placed(base(), vec![first, second])
            .map(|row| row.expect("merged").1)
            .collect();
        assert_eq!(placed, rows(&[b"a2\n", b"b\n", b"b5\n", b"c\n"]));
        let key = |payload: &[u8]| Ok(vec![payload[..1].to_vec()]);
        let segment: Stream<'_, (u8, Vec<u8>)> = Box::new(
            [
                (UPSERT, b"0\n".to_vec()),
                (TOMBSTONE, b"b\n".to_vec()),
                (UPSERT, b"d\n".to_vec()),
            ]
            .into_iter()
            .map(Ok),
        );
        let keyed: Vec<Vec<u8>> = merge_keyed(base(), vec![segment], &key, &key)
            .map(|row| row.expect("merged"))
            .collect();
        assert_eq!(keyed, rows(&[b"0\n", b"a\n", b"c\n", b"d\n"]));
        let disordered: Stream<'_, (u8, Vec<u8>)> = Box::new(
            [(UPSERT, b"b\n".to_vec()), (UPSERT, b"a\n".to_vec())]
                .into_iter()
                .map(Ok),
        );
        let failure = merge_keyed(base(), vec![disordered], &key, &key)
            .find_map(std::result::Result::err)
            .expect("refused");
        assert_eq!(
            failure,
            ReadError::value("segment 1 is out of key order at [b'a']".to_owned())
        );
    }
}
