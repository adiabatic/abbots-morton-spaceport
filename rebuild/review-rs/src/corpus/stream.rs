//! The held audit read one window at a time: each section's rows read through its part's file a read at a time, a plain section's by `pread` and a section held as a gzip member's inflated with bounded output ([`SectionRows`]), a configuration's sections merged across their parts by window, the latest part's row winning and a tombstone removing it ([`Merged`]), and every configuration's merged rows walked together, a window at a time in window order ([`Lockstep`]), each window's rows copied into one reused arena ([`WindowRows`]). Each step reads in a fixed order, so a walk yields the same windows before the same break: a [`StreamError::Break`] (`OrderBreak`), raised once every member has been checked to its end ([`check_members`]), or the member's own failure ([`AuditError::Member`]). [`window_ranges`] cuts the windows into ranges, [`FamilyLines`] gives a window's family lines, and [`signature_key`], [`input_key`] and [`sibling_window`] key and sort a window's rows.

use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io;

use ams_m1_kernel::hash::HashMap as FastMap;
use sha2::{Digest, Sha256};

use crate::corpus::audit::{
    self, AuditError, MemberReader, READ_BYTES, Section, check_members, field, first_line, line_at,
    next_line_start,
};
use crate::pyfmt::repr::repr_str;
use crate::wire::{Result, WireError};

/// What a cursor reads of a segment's section at a time, plain or inflated.
pub const SEGMENT_READ_BYTES: usize = 1 << 16;
/// The fields of an audit row.
pub const FIELDS: usize = 6;
/// The room a section's body buffer is given beyond the bytes it must hold when it is allocated, so a longer carried partial line seldom needs another.
const CARRY_BYTES: usize = 1 << 12;
/// The capacity past which a window arena's text buffer is dropped rather than kept for the next window.
const ARENA_BYTES: usize = 1 << 20;

/// A canonical window's order: its length in code points, then its text.
pub type WindowKey = (usize, String);

/// The order of a window field: its length in code points, then its text.
pub fn window_key(field: &str) -> WindowKey {
    (field.chars().count(), field.to_owned())
}

/// What a walk raises: an `OrderBreak` for an audit not in sorted sections, or what reading the audit raises ([`AuditError`]).
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

/// Where in `body`, whole lines without the last newline, its first line whose window is at or past `low` starts, or `body.len()` when its last line's is not.
fn from_window(body: &[u8], low: &WindowKey, shift: usize) -> usize {
    let last = body
        .iter()
        .rposition(|&byte| byte == b'\n')
        .map_or(0, |at| at + 1);
    if window_key(&field(&body[last..], 1 + shift)) < *low {
        return body.len();
    }
    let mut start = 0;
    loop {
        let end = find_newline(body, start);
        let line = end.map_or(&body[start..], |end| &body[start..end]);
        if window_key(&field(line, 1 + shift)) >= *low {
            return start;
        }
        let Some(end) = end else {
            return start;
        };
        start = end + 1;
    }
}

/// The tab-separated fields of `text[start..end]`: the spans of the first [`FIELDS`] of them in `fields`, and how many it holds, counted no further than one past [`FIELDS`].
fn split_fields(
    text: &str,
    start: usize,
    end: usize,
    fields: &mut [(usize, usize); FIELDS],
) -> usize {
    let mut count = 0;
    let mut at = start;
    loop {
        let stop = text[at..end].find('\t').map_or(end, |found| at + found);
        if count < FIELDS {
            fields[count] = (at, stop);
        }
        count += 1;
        if stop == end || count > FIELDS {
            return count;
        }
        at = stop + 1;
    }
}

/// Drop `buffer`'s allocation when it has grown past `limit`, so one long line does not hold its size for the rest of the walk.
fn bounded(buffer: &mut Vec<u8>, limit: usize) {
    if buffer.capacity() > limit {
        *buffer = Vec::new();
    }
}

/// Give the empty `buffer` room for `needed` bytes, replacing it with a fresh allocation of that size and [`CARRY_BYTES`] more when it holds less, so nothing is copied into the new allocation.
fn fit(buffer: &mut Vec<u8>, needed: usize) {
    if buffer.capacity() < needed {
        *buffer = Vec::with_capacity(needed + CARRY_BYTES);
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

/// The row a cursor last gave, as spans of its body: its line (empty for a segment's tombstone), its fields (the first two only for a tombstone), and its window's length in code points.
#[derive(Clone, Copy, Debug, Default)]
struct RowSpan {
    line: (usize, usize),
    fields: [(usize, usize); FIELDS],
    count: usize,
}

/// One section's rows from a byte: read through its part's file [`READ_BYTES`] at a time from the base and [`SEGMENT_READ_BYTES`] from a segment, a plain section by `pread` and a section held as a gzip member inflated from its start, until a window at or past `stop`; with `low`, a member's rows below it are passed over a read at a time. The current body of whole lines is one buffer, checked as UTF-8 and for carriage returns as a whole before any of its lines is given, and the row given last is held as spans of it, read through [`SectionRows::key`], [`SectionRows::line`] and [`SectionRows::tombstone`] until the next call. A segment's upsert gives its row and its tombstone a row with an empty line. A row of another configuration, one without six fields, a segment line that is neither an upsert nor a tombstone, a window that does not follow the one before it, a carriage return or bytes that are not UTF-8 are a [`StreamError::Break`] with Python's text.
pub struct SectionRows<'a> {
    section: Section,
    segment: bool,
    read_bytes: usize,
    reader: Reader<'a>,
    remainder: Vec<u8>,
    body: String,
    next: Option<usize>,
    span: RowSpan,
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
            body: String::new(),
            next: None,
            span: RowSpan::default(),
            previous: None,
            stop,
            low,
            finished: false,
        })
    }

    /// Move to the next row, or give false once the section ends or reaches `stop`; after a failure, false.
    pub fn next_row(&mut self) -> std::result::Result<bool, StreamError> {
        if self.finished {
            return Ok(false);
        }
        let result = self.advance();
        if !matches!(result, Ok(true)) {
            self.finished = true;
        }
        result
    }

    /// The last row's window key: its length in code points and its text.
    pub fn key(&self) -> (usize, &str) {
        (self.span.count, self.window())
    }

    /// The last row's window field.
    pub fn window(&self) -> &str {
        let (start, end) = self.span.fields[1];
        &self.body[start..end]
    }

    /// The last row's line without its newline, a segment's upsert's without its operation, empty for a tombstone.
    pub fn line(&self) -> &str {
        &self.body[self.span.line.0..self.span.line.1]
    }

    /// Whether the last row is a segment's tombstone.
    pub fn tombstone(&self) -> bool {
        self.span.line.0 == self.span.line.1
    }

    fn advance(&mut self) -> std::result::Result<bool, StreamError> {
        loop {
            if let Some(start) = self.next {
                let end = match self.body[start..].find('\n') {
                    Some(found) => {
                        self.next = Some(start + found + 1);
                        start + found
                    }
                    None => {
                        self.next = None;
                        self.body.len()
                    }
                };
                return self.row(start, end);
            }
            if !self.body()? {
                return Ok(false);
            }
        }
    }

    /// Fill the empty `data` with the partial line carried from the last read and the next read after it, giving how many bytes the read added, none at the section's end. `data` is replaced by a fresh allocation of the size it needs, and a little more for the carried line, when it holds less.
    fn read_into(&mut self, data: &mut Vec<u8>) -> std::result::Result<usize, StreamError> {
        let held = self.remainder.len();
        match &mut self.reader {
            Reader::Plain {
                file,
                position,
                end,
            } => {
                let length = if *position >= *end {
                    0
                } else {
                    (self.read_bytes as i64).min(*end - *position)
                };
                fit(data, held + length as usize);
                data.extend_from_slice(&self.remainder);
                if length == 0 {
                    return Ok(0);
                }
                let count = audit::pread_into(file, length, *position, data)?;
                if count == 0 {
                    return Err(StreamError::Break(format!(
                        "the audit ended at byte {position}, inside the {} section",
                        repr_str(&self.section.config)
                    )));
                }
                *position += count as i64;
                Ok(count)
            }
            Reader::Member(reader) => {
                let chunk = reader.read()?;
                fit(data, held + chunk.len());
                data.extend_from_slice(&self.remainder);
                data.extend_from_slice(&chunk);
                Ok(chunk.len())
            }
        }
    }

    /// Read the next body of whole lines into the body buffer and point the cursor at its first line, or give false at the section's end.
    fn body(&mut self) -> std::result::Result<bool, StreamError> {
        let limit = 4 * self.read_bytes;
        let mut data = std::mem::take(&mut self.body).into_bytes();
        bounded(&mut data, limit);
        loop {
            data.clear();
            let held = self.remainder.len();
            if self.read_into(&mut data)? > 0 {
                match data[held..].iter().rposition(|&byte| byte == b'\n') {
                    None => {
                        std::mem::swap(&mut self.remainder, &mut data);
                        bounded(&mut data, limit);
                        continue;
                    }
                    Some(cut) => {
                        let cut = held + cut;
                        bounded(&mut self.remainder, self.read_bytes);
                        self.remainder.clear();
                        self.remainder.extend_from_slice(&data[cut + 1..]);
                        data.truncate(cut);
                    }
                }
            } else if held > 0 {
                self.remainder.clear();
            } else {
                return Ok(false);
            }
            if let Some(low) = &self.low {
                let start = from_window(&data, low, usize::from(self.segment));
                if start == data.len() {
                    continue;
                }
                self.low = None;
                data.drain(..start);
            }
            let text = String::from_utf8(data).map_err(|failure| {
                StreamError::Break(format!(
                    "the {} section holds bytes that are not UTF-8: {}",
                    repr_str(&self.section.config),
                    utf8_failure(failure.as_bytes(), failure.utf8_error())
                ))
            })?;
            if text.contains('\r') {
                return Err(StreamError::Break(format!(
                    "the {} section holds a carriage return, which a text read takes as a line end",
                    repr_str(&self.section.config)
                )));
            }
            self.body = text;
            self.next = Some(0);
            return Ok(true);
        }
    }

    /// Take the body's line `[start, end)` as the current row, or give false at `stop`.
    fn row(&mut self, start: usize, end: usize) -> std::result::Result<bool, StreamError> {
        let Self {
            section,
            segment,
            body,
            span,
            previous,
            stop,
            ..
        } = self;
        let config = &section.config;
        let fields = &mut span.fields;
        let line = if *segment {
            let (op_end, rest) = match body[start..end].find('\t') {
                Some(found) => (start + found, start + found + 1),
                None => (end, end),
            };
            let held = split_fields(body, rest, end, fields);
            let op = &body[start..op_end];
            if op == "-" && held == 2 {
                (end, end)
            } else if op != "+" || held != FIELDS {
                return Err(StreamError::Break(format!(
                    "the {} section of part {} holds the line {op}\t{}",
                    repr_str(config),
                    section.part,
                    repr_str(&head(&body[rest..end]))
                )));
            } else {
                (rest, end)
            }
        } else {
            if split_fields(body, start, end, fields) != FIELDS {
                return Err(StreamError::Break(format!(
                    "the {} section holds the row {}",
                    repr_str(config),
                    repr_str(&head(&body[start..end]))
                )));
            }
            (start, end)
        };
        if body[fields[0].0..fields[0].1] != *config.as_str() {
            return Err(StreamError::Break(format!(
                "the {} section holds the row {}",
                repr_str(config),
                repr_str(&head(&body[line.0..line.1]))
            )));
        }
        let window = &body[fields[1].0..fields[1].1];
        let count = window.chars().count();
        if let Some((held_count, held)) = previous.as_ref()
            && (count, window) <= (*held_count, held.as_str())
        {
            return Err(StreamError::Break(format!(
                "the {} section's windows do not strictly increase at {}",
                repr_str(config),
                window
            )));
        }
        if let Some((stop_count, stop)) = stop.as_ref()
            && (count, window) >= (*stop_count, stop.as_str())
        {
            return Ok(false);
        }
        match previous {
            Some((held_count, held)) => {
                *held_count = count;
                held.clear();
                held.push_str(window);
            }
            None => *previous = Some((count, window.to_owned())),
        }
        span.line = line;
        span.count = count;
        Ok(true)
    }
}

/// The segments' rows of one configuration merged among themselves by window, the latest part's row or tombstone alone at each (as `heapq.merge` merges them): every cursor primed in order on the first call, and after each yield the cursor that gave the row advanced on the next, so the row given stays readable in its cursor until then.
pub struct Latest<'a> {
    cursors: Vec<(usize, SectionRows<'a>)>,
    heads: Vec<bool>,
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

    fn cursor(&self, index: usize) -> &SectionRows<'a> {
        &self.cursors[index].1
    }

    fn merged(&mut self) -> std::result::Result<Option<usize>, StreamError> {
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
        for (index, &head) in self.heads.iter().enumerate() {
            if !head {
                continue;
            }
            let better = match least {
                None => true,
                Some(held) => {
                    (
                        self.cursor(index).key(),
                        std::cmp::Reverse(self.cursors[index].0),
                    ) < (
                        self.cursor(held).key(),
                        std::cmp::Reverse(self.cursors[held].0),
                    )
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
        self.heads[index] = false;
        Ok(Some(index))
    }

    /// The index of the cursor that holds the next row, or None at the end.
    fn next_row(&mut self) -> std::result::Result<Option<usize>, StreamError> {
        loop {
            let Some(index) = self.merged()? else {
                return Ok(None);
            };
            let (count, window) = self.cursors[index].1.key();
            match &mut self.last {
                Some((_count, last)) if last.as_str() == window => {}
                Some((last_count, last)) => {
                    *last_count = count;
                    last.clear();
                    last.push_str(window);
                    return Ok(Some(index));
                }
                None => {
                    self.last = Some((count, window.to_owned()));
                    return Ok(Some(index));
                }
            }
        }
    }
}

/// Which cursor of a [`Merged`] holds the row it gave last: the base's, or a segment's by its index among the segments.
#[derive(Clone, Copy, Debug)]
enum Given {
    Base,
    Segment(usize),
}

/// One configuration's rows merged across its parts' cursors: at each window the latest part's row wins, and a tombstone there removes the row; the segments merged among themselves first ([`Latest`]) and that one stream merged with the base's cursor, read in the order the Python generator reads them. The row given stays in the cursor that read it, readable through [`Merged::row`] until the next call.
pub struct Merged<'a> {
    base: Option<SectionRows<'a>>,
    latest: Option<Latest<'a>>,
    change: Option<usize>,
    started: bool,
    advance: bool,
    held: bool,
    tail: bool,
    given: Given,
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
            held: false,
            tail,
            given: Given::Base,
        }
    }

    /// The cursor that holds the row given last.
    pub fn row(&self) -> &SectionRows<'a> {
        match self.given {
            Given::Base => self.base.as_ref().expect("a row from the base"),
            Given::Segment(index) => self
                .latest
                .as_ref()
                .expect("a row from a segment")
                .cursor(index),
        }
    }

    /// Move to the next row, or give false at the end.
    pub fn next_row(&mut self) -> std::result::Result<bool, StreamError> {
        let Self {
            base,
            latest,
            change,
            started,
            advance,
            held,
            tail,
            given,
        } = self;
        let Some(latest) = latest else {
            *given = Given::Base;
            return match base {
                Some(base) => base.next_row(),
                None => Ok(false),
            };
        };
        if !*started {
            *started = true;
            *change = latest.next_row()?;
        }
        loop {
            if *advance {
                *advance = false;
                *change = latest.next_row()?;
            }
            if *tail {
                let Some(index) = change.take() else {
                    return Ok(false);
                };
                *advance = true;
                if !latest.cursor(index).tombstone() {
                    *given = Given::Segment(index);
                    return Ok(true);
                }
                continue;
            }
            let row = base.as_mut().expect("a merge with a base");
            if !*held {
                if !row.next_row()? {
                    *tail = true;
                    continue;
                }
                if change.is_none_or(|index| row.key() < latest.cursor(index).key()) {
                    *given = Given::Base;
                    return Ok(true);
                }
                *held = true;
            }
            let key = row.key();
            if let Some(index) = *change
                && latest.cursor(index).key() < key
            {
                *advance = true;
                *change = None;
                if !latest.cursor(index).tombstone() {
                    *given = Given::Segment(index);
                    return Ok(true);
                }
                continue;
            }
            *held = false;
            if let Some(index) = *change
                && latest.cursor(index).key() == key
            {
                *advance = true;
                *change = None;
                if !latest.cursor(index).tombstone() {
                    *given = Given::Segment(index);
                    return Ok(true);
                }
                continue;
            }
            *given = Given::Base;
            return Ok(true);
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

/// Whether a window is written: four uppercase hex digits a code point, joined by colons.
pub fn canonical(window: &str) -> bool {
    window.split(':').all(|piece| {
        piece.len() == 4
            && piece
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'A'..=b'F').contains(&byte))
    })
}

/// One row of a [`WindowRows`]: its configuration's index in the sections, where each of its fields starts in the arena's text, and where its line ends.
#[derive(Clone, Copy, Debug)]
struct RowAt {
    section: usize,
    starts: [usize; FIELDS],
    end: usize,
}

/// One window's rows as [`Lockstep`] gives them, in one arena the walk clears and refills at each window: the window's text, every row's line end to end in one text buffer, and an index of each row's configuration (the index of its configuration's first section) and its six tab-separated fields' starts and line end.
#[derive(Clone, Debug, Default)]
pub struct WindowRows {
    window: String,
    text: String,
    rows: Vec<RowAt>,
}

impl WindowRows {
    /// The window's text.
    pub fn window(&self) -> &str {
        &self.window
    }

    pub fn len(&self) -> usize {
        self.rows.len()
    }

    pub fn is_empty(&self) -> bool {
        self.rows.is_empty()
    }

    /// The index of the row's configuration's first section.
    pub fn section(&self, row: usize) -> usize {
        self.rows[row].section
    }

    /// Every row's line end to end, which [`WindowRows::field_span`] addresses.
    pub fn text(&self) -> &str {
        &self.text
    }

    /// The row's line without its newline.
    pub fn line(&self, row: usize) -> &str {
        let at = &self.rows[row];
        &self.text[at.starts[0]..at.end]
    }

    /// The span of the row's field `index` in [`WindowRows::text`].
    pub fn field_span(&self, row: usize, index: usize) -> (usize, usize) {
        let at = &self.rows[row];
        let end = if index + 1 < FIELDS {
            at.starts[index + 1] - 1
        } else {
            at.end
        };
        (at.starts[index], end)
    }

    /// The row's field `index`.
    pub fn field(&self, row: usize, index: usize) -> &str {
        let (start, end) = self.field_span(row, index);
        &self.text[start..end]
    }

    /// The row's six fields.
    pub fn fields(&self, row: usize) -> [&str; FIELDS] {
        std::array::from_fn(|index| self.field(row, index))
    }

    /// Empty the arena for `window`'s rows, dropping a text buffer one long line grew past [`ARENA_BYTES`].
    pub(crate) fn begin(&mut self, window: &str) {
        self.window.clear();
        self.window.push_str(window);
        if self.text.capacity() > ARENA_BYTES {
            self.text = String::new();
        }
        self.text.clear();
        self.rows.clear();
    }

    /// Copy the row `cursor` gave last, of the configuration whose first section is `section`.
    fn push(&mut self, section: usize, cursor: &SectionRows<'_>) {
        let base = self.text.len();
        let from = cursor.span.line.0;
        self.text.push_str(cursor.line());
        let starts = std::array::from_fn(|index| base + cursor.span.fields[index].0 - from);
        self.rows.push(RowAt {
            section,
            starts,
            end: self.text.len(),
        });
    }

    /// Copy a row from its line, which must hold six fields.
    #[cfg(test)]
    pub(crate) fn push_line(&mut self, section: usize, line: &str) {
        let base = self.text.len();
        self.text.push_str(line);
        let mut fields = [(0, 0); FIELDS];
        let end = self.text.len();
        assert_eq!(split_fields(&self.text, base, end, &mut fields), FIELDS);
        self.rows.push(RowAt {
            section,
            starts: fields.map(|(start, _end)| start),
            end,
        });
    }
}

/// What gives a load its windows one at a time, each window's rows readable until the next call: [`Lockstep`] over the held audit, or windows held in memory.
pub trait WindowSource {
    fn next_window(&mut self) -> std::result::Result<Option<&WindowRows>, StreamError>;
}

/// Every window in `[low, high)` in window order with its rows, each configuration's in rank order (`audit_stream.lockstep`), given in one arena the walk refills at each window. A break ([`StreamError::Break`]) comes once every section held as a gzip member has inflated to what its index records, a member that does not failing in its place; after a failure the walk gives nothing more.
pub struct Lockstep<'a> {
    files: &'a [File],
    sections: &'a [Section],
    named: Vec<usize>,
    cursors: Vec<Merged<'a>>,
    heads: Option<Vec<bool>>,
    finished: bool,
    rows: WindowRows,
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
            rows: WindowRows::default(),
        })
    }

    /// The next window's rows, or None once the walk ends.
    pub fn next_window(&mut self) -> std::result::Result<Option<&WindowRows>, StreamError> {
        if self.finished {
            return Ok(None);
        }
        match self.step() {
            Ok(true) => Ok(Some(&self.rows)),
            Ok(false) => {
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

    fn step(&mut self) -> std::result::Result<bool, StreamError> {
        if self.heads.is_none() {
            let mut heads = Vec::with_capacity(self.cursors.len());
            for cursor in &mut self.cursors {
                heads.push(cursor.next_row()?);
            }
            self.heads = Some(heads);
        }
        let heads = self.heads.as_mut().expect("primed heads");
        let mut least: Option<usize> = None;
        for (index, &head) in heads.iter().enumerate() {
            if head
                && least.is_none_or(|held| {
                    self.cursors[index].row().key() < self.cursors[held].row().key()
                })
            {
                least = Some(index);
            }
        }
        let Some(least) = least else {
            return Ok(false);
        };
        let window = self.cursors[least].row().window();
        if !canonical(window) {
            return Err(StreamError::Break(format!(
                "the window {} is not written as four uppercase hex digits a code point",
                repr_str(window)
            )));
        }
        self.rows.begin(window);
        for index in 0..heads.len() {
            let cursor = &mut self.cursors[index];
            if heads[index] && cursor.row().window() == self.rows.window {
                self.rows.push(self.named[index], cursor.row());
                heads[index] = cursor.next_row()?;
            }
        }
        Ok(true)
    }
}

impl WindowSource for Lockstep<'_> {
    fn next_window(&mut self) -> std::result::Result<Option<&WindowRows>, StreamError> {
        Lockstep::next_window(self)
    }
}

fn content(section: &Section) -> i64 {
    section.inflated.unwrap_or(section.end - section.start)
}

/// The line, without its newline, that starts at the first line start at or after each of `targets`, ascending offsets into the bytes `read` returns end to end.
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

/// A canonical window's code points.
pub fn parse_window(window: &str) -> Result<Vec<i64>> {
    let mut values = Vec::new();
    parse_window_into(window, &mut values)?;
    Ok(values)
}

/// [`parse_window`] into `values`, which it empties first.
pub fn parse_window_into(window: &str, values: &mut Vec<i64>) -> Result<()> {
    values.clear();
    for piece in window.split(':') {
        values.push(
            i64::from_str_radix(piece, 16)
                .map_err(|_| WireError(format!("the window {window:?} is not hex code points")))?,
        );
    }
    Ok(())
}

/// A window's family lines as one suffix, `\n<family>\t<key>` for each family the window cites in name order, memoized a set of families: a family is cited when every one of its `_`-joined components is a window letter's family. Each family is held as its place among the families in name order, so a set of families is its sorted places.
pub struct FamilyLines {
    family_of: FastMap<i64, u32>,
    families: Vec<String>,
    keys: HashMap<String, String>,
    names: Vec<String>,
    places: Vec<u32>,
    memo: FastMap<Vec<u32>, usize>,
    suffixes: Vec<String>,
}

impl FamilyLines {
    pub fn new(family_of: &[(i64, String)], family_keys: &[(String, String)]) -> Self {
        let named: HashMap<i64, &str> = family_of
            .iter()
            .map(|(value, family)| (*value, family.as_str()))
            .collect();
        let mut families: Vec<String> = named.values().map(|family| (*family).to_owned()).collect();
        families.sort();
        families.dedup();
        let family_of = named
            .iter()
            .map(|(value, family)| {
                let place = families
                    .binary_search_by(|held| held.as_str().cmp(family))
                    .expect("a family the list holds");
                (*value, place as u32)
            })
            .collect();
        let keys: HashMap<String, String> = family_keys.iter().cloned().collect();
        let mut names: Vec<String> = keys.keys().cloned().collect();
        names.sort();
        Self {
            family_of,
            families,
            keys,
            names,
            places: Vec::new(),
            memo: FastMap::default(),
            suffixes: Vec::new(),
        }
    }

    pub fn suffix(&mut self, values: &[i64]) -> &str {
        self.places.clear();
        self.places.extend(
            values
                .iter()
                .filter_map(|value| self.family_of.get(value).copied()),
        );
        self.places.sort_unstable();
        self.places.dedup();
        if let Some(&at) = self.memo.get(self.places.as_slice()) {
            return &self.suffixes[at];
        }
        let cited: HashSet<&str> = self
            .places
            .iter()
            .map(|&place| self.families[place as usize].as_str())
            .collect();
        let found: String = self
            .names
            .iter()
            .filter(|name| name.split('_').all(|component| cited.contains(component)))
            .map(|name| format!("\n{name}\t{}", self.keys[name]))
            .collect();
        self.memo.insert(self.places.clone(), self.suffixes.len());
        self.suffixes.push(found);
        self.suffixes.last().expect("a suffix just pushed")
    }
}

/// The ink-signature store key of one audit row from its raw fields: the first 64 bits of the sha256 of its config, window and both rendered-name fields, tab-joined, then its window's family lines.
pub fn signature_key<S: AsRef<str>>(fields: &[S], suffix: &str) -> u64 {
    let mut digest = Sha256::new();
    digest.update(fields[0].as_ref().as_bytes());
    for at in [1, 4, 5] {
        digest.update(b"\t");
        digest.update(fields[at].as_ref().as_bytes());
    }
    digest.update(suffix.as_bytes());
    let digest = digest.finalize();
    u64::from_be_bytes(digest[..8].try_into().expect("eight bytes"))
}

/// The unit store's input key of one unit from its run's audit lines, in run order: the sha256 of the lines joined by newlines, then its window's family lines.
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

/// Whether a window's rows name two or more (baseline, new) pairs.
pub fn sibling_window(rows: &WindowRows) -> bool {
    if rows.len() < 2 {
        return false;
    }
    let first = (rows.field(0, 4), rows.field(0, 5));
    (1..rows.len()).any(|row| (rows.field(row, 4), rows.field(row, 5)) != first)
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
                Ok(Some(window)) => found.push((window.window().to_owned(), window.clone())),
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
            .map(|(window, rows)| (window.as_str(), rows.field(0, 5)))
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
            signature_key(&fields, suffix),
            u64::from_be_bytes(digest[..8].try_into().unwrap())
        );
        assert_eq!(
            input_key(["a", "b"], "\ns"),
            <[u8; 32]>::from(Sha256::digest(b"a\nb\ns"))
        );
    }

    #[test]
    fn fields_split_at_tabs_and_a_window_arena_reads_them_back() {
        let mut fields = [(0, 0); FIELDS];
        assert_eq!(split_fields("xa\tb\t", 1, 5, &mut fields), 3);
        assert_eq!(fields[..3], [(1, 2), (3, 4), (5, 5)]);
        assert_eq!(split_fields("", 0, 0, &mut fields), 1);
        assert_eq!(
            split_fields("1\t2\t3\t4\t5\t6\t7\t8", 0, 15, &mut fields),
            7
        );
        let mut rows = WindowRows::default();
        rows.begin("E650");
        rows.push_line(2, "default\tE650\tk\tclass\tb|c\tn");
        rows.push_line(0, "ss03\tE650\t\t\t\t");
        assert_eq!((rows.window(), rows.len()), ("E650", 2));
        assert_eq!(
            rows.fields(0),
            ["default", "E650", "k", "class", "b|c", "n"]
        );
        assert_eq!(rows.fields(1), ["ss03", "E650", "", "", "", ""]);
        assert_eq!((rows.section(1), rows.line(1)), (0, "ss03\tE650\t\t\t\t"));
        let (start, end) = rows.field_span(0, 4);
        assert_eq!(&rows.text()[start..end], "b|c");
        rows.begin("E651");
        assert!(rows.is_empty() && rows.text().is_empty());
    }

    #[test]
    fn a_line_longer_than_a_read_carries_into_the_next_body() {
        let scratch = Scratch::new("carry");
        let long = "n".repeat(SEGMENT_READ_BYTES * 2 + 7);
        let segment = [
            format!("+\t{}", row("default", "E650", "a")),
            format!("+\t{}", row("default", "E651", &long)),
            "-\tdefault\tE652\n".to_owned(),
            format!("+\t{}", row("default", "E653", "d")),
        ]
        .concat();
        let files = [
            scratch.file("base", b""),
            scratch.file("segment", segment.as_bytes()),
        ];
        let (found, end) = windows(&files, &[plain("default", 0, &segment, 1)]);
        assert_eq!(end, "");
        let news: Vec<(&str, usize)> = found
            .iter()
            .map(|(window, rows)| (window.as_str(), rows.field(0, 5).len()))
            .collect();
        assert_eq!(news, [("E650", 1), ("E651", long.len()), ("E653", 1)]);
        let tombstone = "-\tss03\tE650\n";
        let files = [
            scratch.file("base", b""),
            scratch.file("tombstone", tombstone.as_bytes()),
        ];
        let (_found, end) = windows(&files, &[plain("default", 0, tombstone, 1)]);
        assert_eq!(end, "OrderBreak: the 'default' section holds the row ''");
    }
}
