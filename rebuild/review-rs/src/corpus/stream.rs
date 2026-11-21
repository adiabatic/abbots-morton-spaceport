//! The held audit read one window at a time, as `audit_stream` reads it: each section's rows read through its part's file a read at a time, a plain section's by `pread` and a section held as a gzip member's inflated with bounded output ([`SectionRows`], `section_rows`), a configuration's sections merged across their parts by window, the latest part's row winning and a tombstone removing it ([`Merged`], `_merged` over `_latest`'s `heapq.merge`), and every configuration's merged rows walked together, a window at a time in window order ([`Lockstep`], `lockstep`). Each step reads in the order the Python generators read, so a walk yields the same windows before the same break: an [`StreamError::Break`] (`OrderBreak`) with Python's text, raised once every member has been checked to its end ([`check_members`]), or the member's own failure ([`AuditError::Member`]). [`window_ranges`] cuts the windows into ranges as `audit_stream.window_ranges` does, [`FamilyLines`] gives a window's family lines, and [`signature_key`], [`input_key`] and [`sibling_window`] key and sort a window's rows as `audit_stream`'s functions do.
//!
//! [`stage_stream`] is the `stream` stage of the stage fixtures.

use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{self, BufRead, BufReader, Write};
use std::path::Path;

use ams_m1_kernel::emit::json_string;
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::corpus::audit::{
    self, AuditError, MemberReader, READ_BYTES, Section, Vocabulary, audit_layout, check_members,
    field, first_line, generated_parts, integer, items, line_at, next_line_start, stage_line, text,
};
use crate::corpus::input::BuildInput;
use crate::pyfmt::PyValue;
use crate::pyfmt::repr::repr_str;
use crate::wire::{Result, WireError, error};

/// What a cursor reads of a segment's section at a time, plain or inflated (`audit_stream.SEGMENT_READ_BYTES`).
pub const SEGMENT_READ_BYTES: usize = 1 << 16;
/// The fields of an audit row (`audit_stream.FIELDS`).
pub const FIELDS: usize = 6;
/// The stage's directory, beside its stage file, of the audit parts its generated lines name by sha256.
pub const STAGE_BLOBS: &str = "stream.blobs";
/// The key of the stage's line for how the build's walk ended.
pub const END_KEY: &str = "end";
/// The key of the stage's line for the build's window ranges and each range's walk.
pub const RANGES_KEY: &str = "ranges";
/// The stage's one column, a window's record.
pub const COLUMN: &str = "value";

const UNIT_PREFIX: &str = "unit:";
const KEY_ONLY_PROCESSES: &str = "rebuild.review.build.KEY_ONLY_PROCESSES";

/// A canonical window's order (`audit_stream.window_key`): its length in code points, then its text.
pub type WindowKey = (usize, String);

/// A window's rows as `lockstep` yields them: each configuration's index in the sections, its row's fields and its line.
pub type WindowRows = Vec<(usize, Vec<String>, String)>;

/// The order of a window field: its length in code points, then its text.
pub fn window_key(field: &str) -> WindowKey {
    (field.chars().count(), field.to_owned())
}

/// One row a cursor yields: its window's key, its fields (none for a segment's tombstone) and its line without the newline (empty for a tombstone).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub key: WindowKey,
    pub fields: Vec<String>,
    pub line: String,
}

/// What a walk raises, by the Python class it mirrors: `audit_stream.OrderBreak` for an audit not in sorted sections, or what reading the audit raises ([`AuditError`]).
#[derive(Debug)]
pub enum StreamError {
    Break(String),
    Audit(AuditError),
}

impl StreamError {
    pub fn class(&self) -> &'static str {
        match self {
            Self::Break(_) => "OrderBreak",
            Self::Audit(failure) => failure.class(),
        }
    }

    pub fn message(&self) -> String {
        match self {
            Self::Break(message) => message.clone(),
            Self::Audit(failure) => failure.message(),
        }
    }
}

impl std::fmt::Display for StreamError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.class(), self.message())
    }
}

impl From<AuditError> for StreamError {
    fn from(failure: AuditError) -> Self {
        Self::Audit(failure)
    }
}

impl From<io::Error> for StreamError {
    fn from(failure: io::Error) -> Self {
        Self::Audit(AuditError::Os(failure))
    }
}

fn part_file<'a>(
    files: &'a [File],
    section: &Section,
) -> std::result::Result<&'a File, AuditError> {
    files
        .get(section.part)
        .ok_or_else(|| AuditError::Value(format!("no part {} is held", section.part)))
}

/// A line's first 120 code points, as `line[:120]` cuts it.
fn head(line: &str) -> String {
    line.chars().take(120).collect()
}

fn is_start_byte(byte: u8) -> bool {
    byte < 0x80 || (0xc2..=0xf4).contains(&byte)
}

/// The text of the `UnicodeDecodeError` CPython's UTF-8 decoder raises over `bytes` at `failure`: the maximal invalid subpart's byte or range, and why.
pub fn utf8_failure(bytes: &[u8], failure: std::str::Utf8Error) -> String {
    let start = failure.valid_up_to();
    let (end, reason) = match failure.error_len() {
        None => (bytes.len(), "unexpected end of data"),
        Some(length) if is_start_byte(bytes[start]) => {
            (start + length, "invalid continuation byte")
        }
        Some(length) => (start + length, "invalid start byte"),
    };
    if end - start == 1 {
        format!(
            "'utf-8' codec can't decode byte {:#04x} in position {start}: {reason}",
            bytes[start]
        )
    } else {
        format!(
            "'utf-8' codec can't decode bytes in position {start}-{}: {reason}",
            end - 1
        )
    }
}

fn find_newline(bytes: &[u8], from: usize) -> Option<usize> {
    bytes[from..]
        .iter()
        .position(|&byte| byte == b'\n')
        .map(|at| from + at)
}

/// `body`, whole lines without the last newline, from its first line whose window is at or past `low`, or nothing when its last line's is not (`audit_stream._from_window`).
fn from_window(body: &[u8], low: &WindowKey, shift: usize) -> Vec<u8> {
    let last = body
        .iter()
        .rposition(|&byte| byte == b'\n')
        .map_or(0, |at| at + 1);
    if window_key(&field(&body[last..], 1 + shift)) < *low {
        return Vec::new();
    }
    let mut start = 0;
    loop {
        let end = find_newline(body, start);
        let line = end.map_or(&body[start..], |end| &body[start..end]);
        if window_key(&field(line, 1 + shift)) >= *low {
            return body[start..].to_vec();
        }
        let Some(end) = end else {
            return body[start..].to_vec();
        };
        start = end + 1;
    }
}

enum Reader<'a> {
    Plain {
        file: &'a File,
        position: i64,
        end: i64,
    },
    Member(MemberReader<'a>),
}

/// One section's rows from a byte (`audit_stream.section_rows`): read through its part's file [`READ_BYTES`] at a time from the base and [`SEGMENT_READ_BYTES`] from a segment, a plain section by `pread` and a section held as a gzip member inflated from its start, until a window at or past `stop`; with `low`, a member's rows below it are passed over a read at a time. A segment's upsert yields its row and its tombstone an empty field list and line. A row of another configuration, one without six fields, a segment line that is neither an upsert nor a tombstone, a window that does not follow the one before it, a carriage return or bytes that are not UTF-8 are a [`StreamError::Break`] with Python's text.
pub struct SectionRows<'a> {
    section: Section,
    segment: bool,
    read_bytes: usize,
    reader: Reader<'a>,
    remainder: Vec<u8>,
    lines: std::vec::IntoIter<String>,
    previous: Option<WindowKey>,
    stop: Option<WindowKey>,
    low: Option<WindowKey>,
    finished: bool,
}

impl<'a> SectionRows<'a> {
    pub fn new(
        files: &'a [File],
        section: &Section,
        start: i64,
        stop: Option<WindowKey>,
        low: Option<WindowKey>,
    ) -> std::result::Result<Self, StreamError> {
        let file = part_file(files, section)?;
        let segment = section.part > 0;
        let read_bytes = if segment {
            SEGMENT_READ_BYTES
        } else {
            READ_BYTES
        };
        let reader = if section.inflated.is_none() {
            Reader::Plain {
                file,
                position: start,
                end: section.end,
            }
        } else {
            Reader::Member(
                MemberReader::new(file, section, read_bytes)
                    .map_err(|failure| AuditError::Value(failure.0))?,
            )
        };
        Ok(Self {
            section: section.clone(),
            segment,
            read_bytes,
            reader,
            remainder: Vec::new(),
            lines: Vec::new().into_iter(),
            previous: None,
            stop,
            low,
            finished: false,
        })
    }

    /// The next row, or None once the section ends or reaches `stop`; after a failure, nothing more.
    pub fn next_row(&mut self) -> std::result::Result<Option<Row>, StreamError> {
        if self.finished {
            return Ok(None);
        }
        let result = self.advance();
        if !matches!(result, Ok(Some(_))) {
            self.finished = true;
        }
        result
    }

    fn advance(&mut self) -> std::result::Result<Option<Row>, StreamError> {
        loop {
            if let Some(line) = self.lines.next() {
                return self.row(line);
            }
            if !self.body()? {
                return Ok(None);
            }
        }
    }

    fn read(&mut self) -> std::result::Result<Vec<u8>, StreamError> {
        match &mut self.reader {
            Reader::Plain {
                file,
                position,
                end,
            } => {
                if *position >= *end {
                    return Ok(Vec::new());
                }
                let chunk = audit::pread(
                    file,
                    (self.read_bytes as i64).min(*end - *position),
                    *position,
                )?;
                if chunk.is_empty() {
                    return Err(StreamError::Break(format!(
                        "the audit ended at byte {position}, inside the {} section",
                        repr_str(&self.section.config)
                    )));
                }
                *position += chunk.len() as i64;
                Ok(chunk)
            }
            Reader::Member(reader) => Ok(reader.read()?),
        }
    }

    /// Read the next body of whole lines and split it into lines, or give false at the section's end.
    fn body(&mut self) -> std::result::Result<bool, StreamError> {
        let config = self.section.config.clone();
        loop {
            let chunk = self.read()?;
            let body = if !chunk.is_empty() {
                let mut data = std::mem::take(&mut self.remainder);
                data.extend_from_slice(&chunk);
                match data.iter().rposition(|&byte| byte == b'\n') {
                    None => {
                        self.remainder = data;
                        continue;
                    }
                    Some(cut) => {
                        self.remainder = data[cut + 1..].to_vec();
                        data.truncate(cut);
                        data
                    }
                }
            } else if !self.remainder.is_empty() {
                std::mem::take(&mut self.remainder)
            } else {
                return Ok(false);
            };
            let body = match self.low.take() {
                Some(low) => {
                    let kept = from_window(&body, &low, usize::from(self.segment));
                    if kept.is_empty() {
                        self.low = Some(low);
                        continue;
                    }
                    kept
                }
                None => body,
            };
            let text = String::from_utf8(body).map_err(|failure| {
                StreamError::Break(format!(
                    "the {} section holds bytes that are not UTF-8: {}",
                    repr_str(&config),
                    utf8_failure(failure.as_bytes(), failure.utf8_error())
                ))
            })?;
            if text.contains('\r') {
                return Err(StreamError::Break(format!(
                    "the {} section holds a carriage return, which a text read takes as a line end",
                    repr_str(&config)
                )));
            }
            self.lines = text
                .split('\n')
                .map(str::to_owned)
                .collect::<Vec<_>>()
                .into_iter();
            return Ok(true);
        }
    }

    fn row(&mut self, line: String) -> std::result::Result<Option<Row>, StreamError> {
        let config = &self.section.config;
        let (fields, line) = if self.segment {
            let (op, rest) = match line.split_once('\t') {
                Some((op, rest)) => (op.to_owned(), rest.to_owned()),
                None => (line, String::new()),
            };
            let fields: Vec<String> = rest.split('\t').map(str::to_owned).collect();
            if op == "-" && fields.len() == 2 {
                (fields, String::new())
            } else if op != "+" || fields.len() != FIELDS {
                return Err(StreamError::Break(format!(
                    "the {} section of part {} holds the line {op}\t{}",
                    repr_str(config),
                    self.section.part,
                    repr_str(&head(&rest))
                )));
            } else {
                (fields, rest)
            }
        } else {
            let fields: Vec<String> = line.split('\t').map(str::to_owned).collect();
            if fields.len() != FIELDS {
                return Err(StreamError::Break(format!(
                    "the {} section holds the row {}",
                    repr_str(config),
                    repr_str(&head(&line))
                )));
            }
            (fields, line)
        };
        if fields[0] != *config {
            return Err(StreamError::Break(format!(
                "the {} section holds the row {}",
                repr_str(config),
                repr_str(&head(&line))
            )));
        }
        let key = window_key(&fields[1]);
        if let Some(previous) = &self.previous
            && key <= *previous
        {
            return Err(StreamError::Break(format!(
                "the {} section's windows do not strictly increase at {}",
                repr_str(config),
                fields[1]
            )));
        }
        if let Some(stop) = &self.stop
            && key >= *stop
        {
            return Ok(None);
        }
        self.previous = Some(key.clone());
        let fields = if line.is_empty() { Vec::new() } else { fields };
        Ok(Some(Row { key, fields, line }))
    }
}

fn change_key_below(change: &Option<Row>, key: &WindowKey) -> bool {
    change.as_ref().is_some_and(|change| change.key < *key)
}

/// The segments' rows of one configuration merged among themselves by window, the latest part's row or tombstone alone at each (`audit_stream._latest` over `heapq.merge`): every cursor primed in order on the first call, and after each yield the cursor that gave the row advanced on the next.
pub struct Latest<'a> {
    cursors: Vec<(usize, SectionRows<'a>)>,
    heads: Vec<Option<Row>>,
    primed: bool,
    advance: Option<usize>,
    last: Option<WindowKey>,
}

impl<'a> Latest<'a> {
    fn new(cursors: Vec<(usize, SectionRows<'a>)>) -> Self {
        Self {
            cursors,
            heads: Vec::new(),
            primed: false,
            advance: None,
            last: None,
        }
    }

    fn merged(&mut self) -> std::result::Result<Option<Row>, StreamError> {
        if !self.primed {
            self.primed = true;
            for (_part, cursor) in &mut self.cursors {
                let head = cursor.next_row()?;
                self.heads.push(head);
            }
        }
        if let Some(index) = self.advance.take() {
            self.heads[index] = self.cursors[index].1.next_row()?;
        }
        let mut least: Option<usize> = None;
        for (index, head) in self.heads.iter().enumerate() {
            let Some(head) = head else { continue };
            let better = match least {
                None => true,
                Some(held) => {
                    let other = self.heads[held].as_ref().expect("a held head");
                    (&head.key, std::cmp::Reverse(self.cursors[index].0))
                        < (&other.key, std::cmp::Reverse(self.cursors[held].0))
                }
            };
            if better {
                least = Some(index);
            }
        }
        let Some(index) = least else {
            return Ok(None);
        };
        self.advance = Some(index);
        Ok(self.heads[index].take())
    }

    fn next_row(&mut self) -> std::result::Result<Option<Row>, StreamError> {
        loop {
            let Some(row) = self.merged()? else {
                return Ok(None);
            };
            if self.last.as_ref() != Some(&row.key) {
                self.last = Some(row.key.clone());
                return Ok(Some(row));
            }
        }
    }
}

/// One configuration's rows merged across its parts' cursors (`audit_stream._merged`): at each window the latest part's row wins, and a tombstone there removes the row; the segments merged among themselves first ([`Latest`]) and that one stream merged with the base's cursor, read in the order the Python generator reads them.
pub struct Merged<'a> {
    base: Option<SectionRows<'a>>,
    latest: Option<Latest<'a>>,
    change: Option<Row>,
    started: bool,
    advance: bool,
    held: Option<Row>,
    tail: bool,
}

impl<'a> Merged<'a> {
    fn new(parts: Vec<(usize, SectionRows<'a>)>) -> Self {
        let mut base = None;
        let mut segments = Vec::new();
        for (part, cursor) in parts {
            if part == 0 {
                if base.is_none() {
                    base = Some(cursor);
                }
            } else {
                segments.push((part, cursor));
            }
        }
        let tail = base.is_none();
        Self {
            base,
            latest: (!segments.is_empty()).then(|| Latest::new(segments)),
            change: None,
            started: false,
            advance: false,
            held: None,
            tail,
        }
    }

    pub fn next_row(&mut self) -> std::result::Result<Option<Row>, StreamError> {
        let Some(latest) = &mut self.latest else {
            return match &mut self.base {
                Some(base) => base.next_row(),
                None => Ok(None),
            };
        };
        if !self.started {
            self.started = true;
            self.change = latest.next_row()?;
        }
        loop {
            if self.advance {
                self.advance = false;
                self.change = latest.next_row()?;
            }
            if self.tail {
                let Some(change) = self.change.take() else {
                    return Ok(None);
                };
                self.advance = true;
                if !change.line.is_empty() {
                    return Ok(Some(change));
                }
                continue;
            }
            if self.held.is_none() {
                let base = self.base.as_mut().expect("a merge with a base");
                match base.next_row()? {
                    None => {
                        self.tail = true;
                        continue;
                    }
                    Some(row) => {
                        if self
                            .change
                            .as_ref()
                            .is_none_or(|change| row.key < change.key)
                        {
                            return Ok(Some(row));
                        }
                        self.held = Some(row);
                    }
                }
            }
            let key = self.held.as_ref().expect("a held row").key.clone();
            if change_key_below(&self.change, &key) {
                self.advance = true;
                let change = self.change.take().expect("a change below the row");
                if !change.line.is_empty() {
                    return Ok(Some(change));
                }
                continue;
            }
            let row = self.held.take().expect("a held row");
            if self.change.as_ref().is_some_and(|change| change.key == key) {
                self.advance = true;
                let change = self.change.take().expect("a change at the row");
                if !change.line.is_empty() {
                    return Ok(Some(change));
                }
                continue;
            }
            return Ok(Some(row));
        }
    }
}

/// Each configuration's merged cursor over `[low, high)`, in the sections' order, beside the index of its first section (`audit_stream._cursors`): a plain section's cursor starts at its first window at or past `low`, found by bisection, and a member's at its start, passing over the rows below `low`.
fn cursors<'a>(
    files: &'a [File],
    sections: &[Section],
    low: Option<&WindowKey>,
    high: Option<&WindowKey>,
) -> std::result::Result<(Vec<usize>, Vec<Merged<'a>>), StreamError> {
    let mut named: Vec<usize> = Vec::new();
    let mut groups: Vec<Vec<(usize, SectionRows<'a>)>> = Vec::new();
    for (index, section) in sections.iter().enumerate() {
        let mut start = section.start;
        let mut below = None;
        if let Some(low) = low {
            if section.inflated.is_some() {
                below = Some(low.clone());
            } else {
                let shift = usize::from(section.part > 0);
                start = first_line(
                    part_file(files, section)?,
                    section.start,
                    section.end,
                    |line| window_key(&field(line, 1 + shift)) >= *low,
                )?;
            }
        }
        let cursor = (
            section.part,
            SectionRows::new(files, section, start, high.cloned(), below)?,
        );
        if let Some(&last) = named.last()
            && sections[last].config == section.config
        {
            groups.last_mut().expect("a group").push(cursor);
        } else {
            named.push(index);
            groups.push(vec![cursor]);
        }
    }
    Ok((named, groups.into_iter().map(Merged::new).collect()))
}

/// Whether a window is written as `audit_stream.CANONICAL_WINDOW` writes it: four uppercase hex digits a code point, joined by colons.
pub fn canonical(window: &str) -> bool {
    window.split(':').all(|piece| {
        piece.len() == 4
            && piece
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'A'..=b'F').contains(&byte))
    })
}

/// Every window in `[low, high)` in window order with its rows, each configuration's in rank order (`audit_stream.lockstep`). A break ([`StreamError::Break`]) comes once every section held as a gzip member has inflated to what its index records, a member that does not failing in its place; after a failure the walk gives nothing more.
pub struct Lockstep<'a> {
    files: &'a [File],
    sections: &'a [Section],
    named: Vec<usize>,
    cursors: Vec<Merged<'a>>,
    heads: Option<Vec<Option<Row>>>,
    finished: bool,
}

impl<'a> Lockstep<'a> {
    pub fn new(
        files: &'a [File],
        sections: &'a [Section],
        low: Option<&WindowKey>,
        high: Option<&WindowKey>,
    ) -> std::result::Result<Self, StreamError> {
        let (named, cursors) = cursors(files, sections, low, high)?;
        Ok(Self {
            files,
            sections,
            named,
            cursors,
            heads: None,
            finished: false,
        })
    }

    /// The next window and its rows, or None once the walk ends.
    pub fn next_window(
        &mut self,
    ) -> std::result::Result<Option<(String, WindowRows)>, StreamError> {
        if self.finished {
            return Ok(None);
        }
        match self.step() {
            Ok(Some(found)) => Ok(Some(found)),
            Ok(None) => {
                self.finished = true;
                Ok(None)
            }
            Err(StreamError::Break(why)) => {
                self.finished = true;
                check_members(self.files, self.sections)?;
                Err(StreamError::Break(why))
            }
            Err(failure) => {
                self.finished = true;
                Err(failure)
            }
        }
    }

    fn step(&mut self) -> std::result::Result<Option<(String, WindowRows)>, StreamError> {
        if self.heads.is_none() {
            let mut heads = Vec::with_capacity(self.cursors.len());
            for cursor in &mut self.cursors {
                heads.push(cursor.next_row()?);
            }
            self.heads = Some(heads);
        }
        let heads = self.heads.as_mut().expect("primed heads");
        let mut least: Option<WindowKey> = None;
        for head in heads.iter().flatten() {
            if least.as_ref().is_none_or(|least| head.key < *least) {
                least = Some(head.key.clone());
            }
        }
        let Some(least) = least else {
            return Ok(None);
        };
        if !canonical(&least.1) {
            return Err(StreamError::Break(format!(
                "the window {} is not written as four uppercase hex digits a code point",
                repr_str(&least.1)
            )));
        }
        let mut rows = Vec::new();
        for index in 0..heads.len() {
            if heads[index].as_ref().is_some_and(|row| row.key == least) {
                let row = heads[index].take().expect("a head at the window");
                rows.push((self.named[index], row.fields, row.line));
                heads[index] = self.cursors[index].next_row()?;
            }
        }
        Ok(Some((least.1, rows)))
    }
}

fn content(section: &Section) -> i64 {
    section.inflated.unwrap_or(section.end - section.start)
}

/// The line, without its newline, that starts at the first line start at or after each of `targets`, ascending offsets into the bytes `read` returns end to end (`audit_stream._lines_at`).
fn lines_at(
    mut read: impl FnMut() -> std::result::Result<Vec<u8>, AuditError>,
    targets: &[i64],
) -> std::result::Result<Vec<Vec<u8>>, AuditError> {
    let mut buffer: Vec<u8> = Vec::new();
    let mut origin: i64 = 0;
    let mut starts_line = true;
    let mut found = Vec::new();
    for &target in targets {
        if target > origin || !starts_line {
            while origin + (buffer.len() as i64) < target {
                origin += buffer.len() as i64;
                buffer = read()?;
                if buffer.is_empty() {
                    return Ok(found);
                }
            }
            let mut at = (target - 1 - origin).max(0) as usize;
            let newline = loop {
                if let Some(newline) = find_newline(&buffer, at) {
                    break newline;
                }
                at = buffer.len();
                let chunk = read()?;
                if chunk.is_empty() {
                    return Ok(found);
                }
                buffer.extend_from_slice(&chunk);
            };
            buffer.drain(..=newline);
            origin += newline as i64 + 1;
            starts_line = true;
        }
        let end = loop {
            if let Some(end) = find_newline(&buffer, 0) {
                break end;
            }
            let chunk = read()?;
            if chunk.is_empty() {
                break buffer.len();
            }
            buffer.extend_from_slice(&chunk);
        };
        if buffer.is_empty() {
            return Ok(found);
        }
        found.push(buffer[..end].to_vec());
    }
    Ok(found)
}

/// A window range, either end open.
pub type WindowRange = (Option<WindowKey>, Option<WindowKey>);

/// Split the windows into at most `count` ranges `[low, high)` at the windows found at equal fractions of the lines' bytes of the largest section, by its lines' bytes (`audit_stream.window_ranges`).
pub fn window_ranges(
    files: &[File],
    sections: &[Section],
    count: i64,
) -> std::result::Result<Vec<WindowRange>, AuditError> {
    let Some(mut largest) = sections.first() else {
        return Ok(Vec::new());
    };
    for section in sections {
        if content(section) > content(largest) {
            largest = section;
        }
    }
    let file = part_file(files, largest)?;
    let shift = usize::from(largest.part > 0);
    let lines = match largest.inflated {
        None => {
            let mut lines = Vec::new();
            for part in 1..count {
                let offset = largest.start + (largest.end - largest.start) * part / count;
                let start = next_line_start(file, offset, largest.start, largest.end)?;
                if start >= largest.end {
                    continue;
                }
                lines.push(line_at(file, start, largest.end)?.0);
            }
            lines
        }
        Some(inflated) => {
            let mut reader = MemberReader::new(file, largest, READ_BYTES)
                .map_err(|failure| AuditError::Value(failure.0))?;
            let targets: Vec<i64> = (1..count).map(|part| inflated * part / count).collect();
            lines_at(|| reader.read(), &targets)?
        }
    };
    let mut cuts: Vec<WindowKey> = Vec::new();
    for line in &lines {
        let key = window_key(&field(line, 1 + shift));
        if cuts.last().is_none_or(|last| key > *last) {
            cuts.push(key);
        }
    }
    let mut bounds: Vec<Option<WindowKey>> = vec![None];
    bounds.extend(cuts.into_iter().map(Some));
    bounds.push(None);
    Ok(bounds
        .windows(2)
        .map(|pair| (pair[0].clone(), pair[1].clone()))
        .collect())
}

/// A canonical window's code points (`audit_stream.parse_window`).
pub fn parse_window(window: &str) -> Result<Vec<i64>> {
    window
        .split(':')
        .map(|piece| {
            i64::from_str_radix(piece, 16)
                .map_err(|_| WireError(format!("the window {window:?} is not hex code points")))
        })
        .collect()
}

/// A window's family lines as one suffix, `\n<family>\t<key>` for each family the window cites in name order, memoized a set of families (`audit_stream.FamilyLines`): a family is cited when every one of its `_`-joined components is a window letter's family.
pub struct FamilyLines {
    family_of: HashMap<i64, String>,
    keys: HashMap<String, String>,
    names: Vec<String>,
    suffixes: HashMap<Vec<String>, String>,
}

impl FamilyLines {
    pub fn new(family_of: &[(i64, String)], family_keys: &[(String, String)]) -> Self {
        let family_of: HashMap<i64, String> = family_of.iter().cloned().collect();
        let keys: HashMap<String, String> = family_keys.iter().cloned().collect();
        let mut names: Vec<String> = keys.keys().cloned().collect();
        names.sort();
        Self {
            family_of,
            keys,
            names,
            suffixes: HashMap::new(),
        }
    }

    pub fn suffix(&mut self, values: &[i64]) -> String {
        let mut families: Vec<String> = values
            .iter()
            .filter_map(|value| self.family_of.get(value).cloned())
            .collect();
        families.sort();
        families.dedup();
        if let Some(found) = self.suffixes.get(&families) {
            return found.clone();
        }
        let cited: HashSet<&str> = families.iter().map(String::as_str).collect();
        let found: String = self
            .names
            .iter()
            .filter(|name| name.split('_').all(|component| cited.contains(component)))
            .map(|name| format!("\n{name}\t{}", self.keys[name]))
            .collect();
        self.suffixes.insert(families, found.clone());
        found
    }
}

/// The ink-signature store key of one audit row from its raw fields (`audit_stream.signature_key`): the first 64 bits of the sha256 of its config, window and both rendered-name fields, tab-joined, then its window's family lines.
pub fn signature_key(fields: &[String], suffix: &str) -> u64 {
    let text = format!(
        "{}\t{}\t{}\t{}{suffix}",
        fields[0], fields[1], fields[4], fields[5]
    );
    let digest = Sha256::digest(text.as_bytes());
    u64::from_be_bytes(digest[..8].try_into().expect("eight bytes"))
}

/// The unit store's input key of one unit from its run's audit lines, in run order (`audit_stream.input_key`): the sha256 of the lines joined by newlines, then its window's family lines.
pub fn input_key<'l>(lines: impl IntoIterator<Item = &'l str>, suffix: &str) -> [u8; 32] {
    let mut digest = Sha256::new();
    for (index, line) in lines.into_iter().enumerate() {
        if index > 0 {
            digest.update(b"\n");
        }
        digest.update(line.as_bytes());
    }
    digest.update(suffix.as_bytes());
    digest.finalize().into()
}

/// Whether a window's rows name two or more (baseline, new) pairs (`audit_stream.sibling_window`).
pub fn sibling_window(rows: &WindowRows) -> bool {
    if rows.len() < 2 {
        return false;
    }
    let first = (&rows[0].1[4], &rows[0].1[5]);
    rows[1..]
        .iter()
        .any(|(_index, fields, _line)| (&fields[4], &fields[5]) != first)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn rows_value(rows: &WindowRows) -> PyValue {
    PyValue::List(
        rows.iter()
            .map(|(index, fields, line)| {
                PyValue::Tuple(vec![
                    PyValue::Int(*index as i64),
                    PyValue::List(fields.iter().map(PyValue::str).collect()),
                    PyValue::str(line),
                ])
            })
            .collect(),
    )
}

/// A window's record as the stage holds it: the window, its rows, its family lines, whether it holds siblings, each row's signature key as `%016x`, and the input key of its lines in order, as hex.
pub fn window_record(family: &mut FamilyLines, window: &str, rows: &WindowRows) -> Result<PyValue> {
    let suffix = family.suffix(&parse_window(window)?);
    Ok(PyValue::Tuple(vec![
        PyValue::str(window),
        rows_value(rows),
        PyValue::str(&suffix),
        PyValue::Bool(sibling_window(rows)),
        PyValue::Tuple(
            rows.iter()
                .map(|(_index, fields, _line)| {
                    PyValue::str(format!("{:016x}", signature_key(fields, &suffix)))
                })
                .collect(),
        ),
        PyValue::str(hex(&input_key(
            rows.iter().map(|(_index, _fields, line)| line.as_str()),
            &suffix,
        ))),
    ]))
}

fn os_failure(failure: StreamError) -> WireError {
    WireError(format!("cannot read the audit: {failure}"))
}

/// Walk `[low, high)` in lockstep, handing each window and its rows to `each`, and give how the walk ended: `("done",)`, or `("raised", class, message)` for a break or a member that fails; a read that fails is an error.
fn walk(
    files: &[File],
    sections: &[Section],
    low: Option<&WindowKey>,
    high: Option<&WindowKey>,
    mut each: impl FnMut(String, WindowRows) -> Result<()>,
) -> Result<PyValue> {
    let raised = |failure: StreamError| -> Result<PyValue> {
        match failure {
            StreamError::Audit(AuditError::Os(_)) | StreamError::Audit(AuditError::Value(_)) => {
                Err(os_failure(failure))
            }
            failure => Ok(PyValue::Tuple(vec![
                PyValue::str("raised"),
                PyValue::str(failure.class()),
                PyValue::str(failure.message()),
            ])),
        }
    };
    let mut stream = match Lockstep::new(files, sections, low, high) {
        Ok(stream) => stream,
        Err(failure) => return raised(failure),
    };
    loop {
        match stream.next_window() {
            Ok(Some((window, rows))) => each(window, rows)?,
            Ok(None) => return Ok(PyValue::Tuple(vec![PyValue::str("done")])),
            Err(failure) => return raised(failure),
        }
    }
}

fn entry(value: &PyValue) -> Vec<u8> {
    let mut bytes = value.to_typed().into_bytes();
    bytes.push(b'\n');
    bytes
}

fn key_value(key: &Option<WindowKey>) -> PyValue {
    key.as_ref().map_or(PyValue::None, |(length, text)| {
        PyValue::Tuple(vec![PyValue::Int(*length as i64), PyValue::str(text)])
    })
}

/// Each range with its walk: the windows it holds, the sha256 of their `(window, rows)` entries, and how it ended.
fn split_walks(files: &[File], sections: &[Section], ranges: &[WindowRange]) -> Result<PyValue> {
    let mut walks = Vec::new();
    for (low, high) in ranges {
        let mut digest = Sha256::new();
        let mut count = 0i64;
        let end = walk(
            files,
            sections,
            low.as_ref(),
            high.as_ref(),
            |window, rows| {
                digest.update(entry(&PyValue::Tuple(vec![
                    PyValue::str(window),
                    rows_value(&rows),
                ])));
                count += 1;
                Ok(())
            },
        )?;
        walks.push(PyValue::Tuple(vec![
            key_value(low),
            key_value(high),
            PyValue::Tuple(vec![
                PyValue::Int(count),
                PyValue::str(hex(&digest.finalize())),
                end,
            ]),
        ]));
    }
    Ok(PyValue::Tuple(walks))
}

fn section_of(value: &PyValue) -> Result<Section> {
    let PyValue::Record(name, fields) = value else {
        return error(format!("a section is {}, not a record", value.kind()));
    };
    let get = |wanted: &str| {
        fields
            .iter()
            .find(|(field, _)| field == wanted)
            .map(|(_, value)| value)
            .ok_or_else(|| WireError(format!("the {name} record has no {wanted}")))
    };
    Ok(Section {
        config: text(get("config")?, "a section's config")?.to_owned(),
        rank: integer(get("rank")?, "a section's rank")?,
        start: integer(get("start")?, "a section's start")?,
        end: integer(get("end")?, "a section's end")?,
        part: usize::try_from(integer(get("part")?, "a section's part")?)
            .map_err(|_| WireError("a section's part is negative".into()))?,
        inflated: match get("inflated")? {
            PyValue::None => None,
            inflated => Some(integer(inflated, "a section's inflated bytes")?),
        },
    })
}

/// A generated line's value: every window's record, how the walk ended, and for each count the ranges it is split into, each with its walk, or what splitting raised.
fn generated_value(input: &PyValue, blobs: &Path, family: &mut FamilyLines) -> Result<PyValue> {
    let [parts, sections, counts] = items(input, "the input")? else {
        return error("the input is not parts, sections and counts");
    };
    let (files, _indexes) = generated_parts(parts, blobs)?;
    let sections = items(sections, "the sections")?
        .iter()
        .map(section_of)
        .collect::<Result<Vec<Section>>>()?;
    let mut records = Vec::new();
    let end = walk(&files, &sections, None, None, |window, rows| {
        records.push(window_record(family, &window, &rows)?);
        Ok(())
    })?;
    let mut splits = Vec::new();
    for count in items(counts, "the counts")? {
        let count = integer(count, "a count")?;
        let split = match window_ranges(&files, &sections, count) {
            Ok(ranges) => PyValue::Tuple(vec![
                PyValue::str("ranges"),
                split_walks(&files, &sections, &ranges)?,
            ]),
            Err(failure @ AuditError::Member(_)) => PyValue::Tuple(vec![
                PyValue::str("raised"),
                PyValue::str(failure.class()),
                PyValue::str(failure.message()),
            ]),
            Err(failure) => return error(format!("cannot split the audit: {failure}")),
        };
        splits.push(PyValue::Tuple(vec![PyValue::Int(count), split]));
    }
    Ok(PyValue::Tuple(vec![
        PyValue::Tuple(records),
        end,
        PyValue::Tuple(splits),
    ]))
}

fn column_line(out: &mut impl Write, count: i64, digest: String) -> Result<()> {
    writeln!(
        out,
        "{{\"key\":{},\"values\":{count},\"sha256\":{}}}",
        json_string(&format!("column:{COLUMN}")),
        json_string(&digest)
    )
    .map_err(|failure| WireError(format!("cannot write a stage line: {failure}")))
}

/// The `stream` stage: answer the build's walk of the audit its build input names, read through the descriptors the process inherits, its sections found as [`audit_layout`] finds them, with a `unit:<ordinal>` line for each window the stage file asks for, the `column:value` line's count and digest over every window's record, and the walk's end under [`END_KEY`]; the build's window ranges at the vocabulary's `KEY_ONLY_PROCESSES`, each with its walk, under [`RANGES_KEY`]; and every generated line with the walk and the splits of the parts it names in the `stream.blobs` directory beside `stage_file`, over the sections it gives. A window's record is [`window_record`]'s, with the input's family keys.
pub fn stage_stream(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let mut family = FamilyLines::new(&input.family_of, &input.family_keys);
    let blobs = stage_file
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(STAGE_BLOBS);
    let file = File::open(stage_file)
        .map_err(|failure| WireError(format!("cannot read {}: {failure}", stage_file.display())))?;
    let mut generated = Vec::new();
    let mut units: HashSet<i64> = HashSet::new();
    let (mut walked, mut ranged) = (false, false);
    for line in BufReader::new(file).lines() {
        let line = line.map_err(|failure| {
            WireError(format!("cannot read {}: {failure}", stage_file.display()))
        })?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| WireError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return error(format!("a stage line has no key: {line:.200}"));
        };
        if let Some(ordinal) = key.strip_prefix(UNIT_PREFIX) {
            units.insert(
                ordinal
                    .parse()
                    .map_err(|_| WireError(format!("the key {key} names no ordinal")))?,
            );
            walked = true;
        } else if key == END_KEY || key == format!("column:{COLUMN}") {
            walked = true;
        } else if key == RANGES_KEY {
            ranged = true;
        } else {
            let held = PyValue::from_typed(&record["input"])
                .map_err(|failure| WireError(format!("{key}: {failure}")))?;
            generated.push((key.to_owned(), held));
        }
    }
    if walked || ranged {
        let files = input.held_audit()?;
        let layout = audit_layout(
            &files,
            &input.audit.path,
            &input.audit.members,
            &Vocabulary::of(&input)?,
        )
        .map_err(|failure| WireError(format!("cannot lay out the audit: {failure}")))?;
        let Some(sections) = layout.sections else {
            return error(format!(
                "the audit is not in sorted sections: {}",
                layout.why
            ));
        };
        if walked {
            let mut digest = Sha256::new();
            let mut ordinal = 0i64;
            let end = walk(&files, &sections, None, None, |window, rows| {
                let record = window_record(&mut family, &window, &rows)?;
                digest.update(entry(&record));
                if units.contains(&ordinal) {
                    stage_line(out, &format!("{UNIT_PREFIX}{ordinal}"), &record)?;
                }
                ordinal += 1;
                Ok(())
            })?;
            column_line(out, ordinal, hex(&digest.finalize()))?;
            stage_line(out, END_KEY, &end)?;
        }
        if ranged {
            let count = match input.constant(KEY_ONLY_PROCESSES) {
                Some(PyValue::Int(count)) => *count,
                _ => {
                    return error(format!(
                        "the build input's vocabulary holds no int {KEY_ONLY_PROCESSES}"
                    ));
                }
            };
            let ranges = window_ranges(&files, &sections, count)
                .map_err(|failure| WireError(format!("cannot split the audit: {failure}")))?;
            let value = PyValue::Tuple(vec![
                PyValue::Int(count),
                split_walks(&files, &sections, &ranges)?,
            ]);
            stage_line(out, RANGES_KEY, &value)?;
        }
    }
    for (key, held) in generated {
        let value = generated_value(&held, &blobs, &mut family)
            .map_err(|failure| WireError(format!("{key}: {}", failure.0)))?;
        stage_line(out, &key, &value)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::corpus::gzip::compress_member;

    struct Scratch(std::path::PathBuf);

    impl Scratch {
        fn new(label: &str) -> Self {
            let path = std::env::temp_dir()
                .join(format!("ams-review-stream-{label}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&path);
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }

        fn file(&self, name: &str, bytes: &[u8]) -> File {
            let path = self.0.join(name);
            std::fs::write(&path, bytes).unwrap();
            File::open(path).unwrap()
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn row(config: &str, window: &str, new: &str) -> String {
        format!("{config}\t{window}\tcell\tentry\tbase\t{new}\n")
    }

    fn plain(config: &str, start: usize, bytes: &str, part: usize) -> Section {
        Section {
            config: config.into(),
            rank: 0,
            start: start as i64,
            end: (start + bytes.len()) as i64,
            part,
            inflated: None,
        }
    }

    fn windows(files: &[File], sections: &[Section]) -> (Vec<(String, WindowRows)>, String) {
        let mut stream = Lockstep::new(files, sections, None, None).unwrap();
        let mut found = Vec::new();
        loop {
            match stream.next_window() {
                Ok(Some(window)) => found.push(window),
                Ok(None) => return (found, String::new()),
                Err(failure) => return (found, failure.to_string()),
            }
        }
    }

    #[test]
    fn utf8_failures_read_as_cpython_says_them() {
        let failure = |bytes: &[u8]| utf8_failure(bytes, std::str::from_utf8(bytes).unwrap_err());
        assert_eq!(
            failure(b"ab\xff"),
            "'utf-8' codec can't decode byte 0xff in position 2: invalid start byte"
        );
        assert_eq!(
            failure(b"\xe2\x82"),
            "'utf-8' codec can't decode bytes in position 0-1: unexpected end of data"
        );
        assert_eq!(
            failure(b"\xe1\x80A"),
            "'utf-8' codec can't decode bytes in position 0-1: invalid continuation byte"
        );
        assert_eq!(
            failure(b"\xed\xa0\x80"),
            "'utf-8' codec can't decode byte 0xed in position 0: invalid continuation byte"
        );
        assert_eq!(
            failure(b"\xc0\xaf"),
            "'utf-8' codec can't decode byte 0xc0 in position 0: invalid start byte"
        );
    }

    #[test]
    fn a_segment_supersedes_and_removes_base_rows() {
        let scratch = Scratch::new("merge");
        let base = [
            row("default", "E650", "a"),
            row("default", "E651", "b"),
            row("default", "E652", "c"),
        ]
        .concat();
        let segment = [
            format!("+\t{}", row("default", "E651", "B")),
            "-\tdefault\tE652\n".to_owned(),
            format!("+\t{}", row("default", "E653", "d")),
        ]
        .concat();
        let files = [
            scratch.file("base", base.as_bytes()),
            scratch.file("segment", segment.as_bytes()),
        ];
        let sections = [
            plain("default", 0, &base, 0),
            plain("default", 0, &segment, 1),
        ];
        let (found, end) = windows(&files, &sections);
        assert_eq!(end, "");
        let news: Vec<(&str, &str)> = found
            .iter()
            .map(|(window, rows)| (window.as_str(), rows[0].1[5].as_str()))
            .collect();
        assert_eq!(news, [("E650", "a"), ("E651", "B"), ("E653", "d")]);
    }

    #[test]
    fn a_break_names_its_row() {
        let scratch = Scratch::new("break");
        let base = [row("default", "E651", "a"), row("default", "E650", "b")].concat();
        let files = [scratch.file("base", base.as_bytes())];
        let (found, end) = windows(&files, &[plain("default", 0, &base, 0)]);
        assert_eq!(found.len(), 0);
        assert_eq!(
            end,
            "OrderBreak: the 'default' section's windows do not strictly increase at E650"
        );
        let lower = row("default", "e650", "a");
        let files = [scratch.file("lower", lower.as_bytes())];
        let (_found, end) = windows(&files, &[plain("default", 0, &lower, 0)]);
        assert_eq!(
            end,
            "OrderBreak: the window 'e650' is not written as four uppercase hex digits a code point"
        );
    }

    #[test]
    fn a_member_is_split_at_its_inflated_fractions() {
        let scratch = Scratch::new("ranges");
        let body: String = (0..40)
            .map(|index| row("default", &format!("{:04X}", 0xE650 + index), "n"))
            .collect();
        let member = compress_member(body.as_bytes(), 1, 0).unwrap();
        let files = [scratch.file("base", &member)];
        let section = Section {
            config: "default".into(),
            rank: 0,
            start: 0,
            end: member.len() as i64,
            part: 0,
            inflated: Some(body.len() as i64),
        };
        let ranges = window_ranges(&files, std::slice::from_ref(&section), 4).unwrap();
        assert_eq!(ranges.len(), 4);
        assert_eq!(ranges[0].0, None);
        assert_eq!(ranges[3].1, None);
        let mut total = 0;
        for (low, high) in &ranges {
            let mut stream = Lockstep::new(
                &files,
                std::slice::from_ref(&section),
                low.as_ref(),
                high.as_ref(),
            )
            .unwrap();
            while stream.next_window().unwrap().is_some() {
                total += 1;
            }
        }
        assert_eq!(total, 40);
    }

    #[test]
    fn family_lines_cite_whole_ligatures() {
        let mut family = FamilyLines::new(
            &[(0xE653, "qsDay".into()), (0xE67A, "qsUtter".into())],
            &[
                ("qsUtter".into(), "u".into()),
                ("qsDay_qsUtter".into(), "l".into()),
                ("qsDay".into(), "d".into()),
            ],
        );
        assert_eq!(family.suffix(&[0xE653]), "\nqsDay\td");
        assert_eq!(
            family.suffix(&[0xE67A, 0xE653, 0x41]),
            "\nqsDay\td\nqsDay_qsUtter\tl\nqsUtter\tu"
        );
        let fields: Vec<String> = ["default", "E653", "cell", "entry", "b", "n"]
            .map(str::to_owned)
            .to_vec();
        let suffix = family.suffix(&[0xE653]);
        let digest = Sha256::digest(format!("default\tE653\tb\tn{suffix}").as_bytes());
        assert_eq!(
            signature_key(&fields, &suffix),
            u64::from_be_bytes(digest[..8].try_into().unwrap())
        );
        assert_eq!(
            input_key(["a", "b"], "\ns"),
            <[u8; 32]>::from(Sha256::digest(b"a\nb\ns"))
        );
    }
}
