//! The held divergence audit's layout, found through the descriptors the process inherits: the header checked, and each part's sections found, a plain part's by bisecting on its line starts with `pread` ([`audit_sections`], over [`line_at`], [`next_line_start`], [`last_line_start`] and [`first_line`]) and a part held as gzip members from its member index ([`member_sections`]), then sorted by rank and part ([`audit_layout`]). A member is read a piece at a time by position, inflated with bounded output and checked against its index at its end ([`MemberReader`], [`member_bytes`], [`check_members`]), and a member that does not inflate to what its index records fails as `MemberError` ([`AuditError`]).

use std::fs::File;
use std::io;
use std::os::unix::fs::FileExt;

use crate::corpus::gzip::Decompressor;
use crate::corpus::input::{BuildInput, Member};
use crate::pyfmt::PyValue;
use crate::pyfmt::repr::repr_str;
use crate::wire::{Result, WireError, error};

/// What a cursor reads of a base section at a time, plain or inflated.
pub const READ_BYTES: usize = 1 << 20;
/// The share of its read a member reader reads compressed at a time.
pub const PIECE_SHARE: usize = 16;
/// What a line probe reads at a time.
pub const PROBE_BYTES: usize = 4096;

const GZIP_WINDOW: i32 = 31;

/// One configuration's rows in one part: its configuration and rank, the byte range `[start, end)` of its lines on disk (a member's bytes for a section held as one), its part, and, for a section held as a gzip member, the member's inflated bytes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Section {
    pub config: String,
    pub rank: i64,
    pub start: i64,
    pub end: i64,
    pub part: usize,
    pub inflated: Option<i64>,
}

/// What reading the audit's layout raises, by the Python exception class its text names: a `ValueError` for an audit with no header or another header, a `MemberError` for a member that does not inflate to what its index records, and an `OSError` from a read.
#[derive(Debug)]
pub enum AuditError {
    Value(String),
    Member(String),
    Os(io::Error),
}

impl AuditError {
    pub fn class(&self) -> &'static str {
        match self {
            Self::Value(_) => "ValueError",
            Self::Member(_) => "MemberError",
            Self::Os(_) => "OSError",
        }
    }

    pub fn message(&self) -> String {
        match self {
            Self::Value(message) | Self::Member(message) => message.clone(),
            Self::Os(failure) => failure.to_string(),
        }
    }
}

impl std::fmt::Display for AuditError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.class(), self.message())
    }
}

impl From<io::Error> for AuditError {
    fn from(failure: io::Error) -> Self {
        Self::Os(failure)
    }
}

/// What `audit_layout` finds: the sections across the parts, sorted by rank and then part, or None and why the audit is not in sorted sections, beside the byte the base's rows start at.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AuditLayout {
    pub sections: Option<Vec<Section>>,
    pub why: String,
    pub rows_start: i64,
}

/// The vocabulary the layout reads: the acceptance configurations, whose order ranks a section, and the audit's header fields (`audit.AUDIT_HEADER`).
#[derive(Clone, Debug)]
pub struct Vocabulary {
    pub configs: Vec<String>,
    pub header: Vec<String>,
}

impl Vocabulary {
    pub fn of(input: &BuildInput) -> Result<Self> {
        let strings = |name: &str| match input.constant(name) {
            Some(PyValue::Tuple(items)) => items
                .iter()
                .map(|item| match item {
                    PyValue::Str(text) => Ok(text.clone()),
                    other => error(format!("{name} holds {}", other.kind())),
                })
                .collect::<Result<Vec<String>>>(),
            _ => error(format!(
                "the build input's vocabulary holds no tuple {name}"
            )),
        };
        Ok(Self {
            configs: strings("rebuild.review.audit.ACCEPTANCE_CONFIGS")?,
            header: strings("rebuild.review.audit.AUDIT_HEADER")?,
        })
    }

    /// A configuration's rank: its place among the acceptance configurations, or one past them for any other.
    pub fn rank(&self, config: &str) -> i64 {
        self.configs
            .iter()
            .position(|held| held == config)
            .unwrap_or(self.configs.len()) as i64
    }
}

fn offset(position: i64) -> io::Result<u64> {
    u64::try_from(position)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "negative offset"))
}

/// `os.pread(descriptor, length, position)`: one read of at most `length` bytes at `position`, fewer at the file's end.
pub(crate) fn pread(file: &File, length: i64, position: i64) -> io::Result<Vec<u8>> {
    let mut buffer = vec![0; length.max(0) as usize];
    let at = offset(position)?;
    loop {
        match file.read_at(&mut buffer, at) {
            Ok(count) => {
                buffer.truncate(count);
                return Ok(buffer);
            }
            Err(failure) if failure.kind() == io::ErrorKind::Interrupted => {}
            Err(failure) => return Err(failure),
        }
    }
}

/// [`pread`] onto the end of `buffer`, giving how many bytes it added.
pub(crate) fn pread_into(
    file: &File,
    length: i64,
    position: i64,
    buffer: &mut Vec<u8>,
) -> io::Result<usize> {
    let held = buffer.len();
    buffer.resize(held + length.max(0) as usize, 0);
    let at = match offset(position) {
        Ok(at) => at,
        Err(failure) => {
            buffer.truncate(held);
            return Err(failure);
        }
    };
    loop {
        match file.read_at(&mut buffer[held..], at) {
            Ok(count) => {
                buffer.truncate(held + count);
                return Ok(count);
            }
            Err(failure) if failure.kind() == io::ErrorKind::Interrupted => {}
            Err(failure) => {
                buffer.truncate(held);
                return Err(failure);
            }
        }
    }
}

fn size_of(file: &File) -> io::Result<i64> {
    i64::try_from(file.metadata()?.len())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "a part outruns an int"))
}

fn probe(start: i64, end: i64) -> i64 {
    (PROBE_BYTES as i64).min(end - start)
}

/// The first line start at or after `offset` and before `end`, or `end`; `lower` is a line start at or before `offset`.
pub fn next_line_start(file: &File, offset: i64, lower: i64, end: i64) -> io::Result<i64> {
    if offset <= lower {
        return Ok(lower);
    }
    let mut position = offset - 1;
    while position < end {
        let chunk = pread(file, probe(position, end), position)?;
        if chunk.is_empty() {
            return Ok(end);
        }
        if let Some(found) = chunk.iter().position(|&byte| byte == b'\n') {
            return Ok((position + found as i64 + 1).min(end));
        }
        position += chunk.len() as i64;
    }
    Ok(end)
}

/// The line that starts at `start`, without its newline, and the start of the line after it, reading no further than `end`.
pub fn line_at(file: &File, start: i64, end: i64) -> io::Result<(Vec<u8>, i64)> {
    let mut line = Vec::new();
    let mut position = start;
    while position < end {
        let chunk = pread(file, probe(position, end), position)?;
        if chunk.is_empty() {
            break;
        }
        if let Some(found) = chunk.iter().position(|&byte| byte == b'\n') {
            line.extend_from_slice(&chunk[..found]);
            return Ok((line, position + found as i64 + 1));
        }
        line.extend_from_slice(&chunk);
        position += chunk.len() as i64;
    }
    Ok((line, end))
}

/// The start of the last line that ends at or before `end`, reading back no further than `lower`, a line start.
pub fn last_line_start(file: &File, lower: i64, end: i64) -> io::Result<i64> {
    let mut position = end - 1;
    while position > lower {
        let start = lower.max(position - PROBE_BYTES as i64);
        let chunk = pread(file, position - start, start)?;
        if let Some(found) = chunk.iter().rposition(|&byte| byte == b'\n') {
            return Ok(start + found as i64 + 1);
        }
        position = start;
    }
    Ok(lower)
}

/// The start of the first line in `[low, high)` that `satisfies`, or `high` when none does, given that `low` is a line start and every line that satisfies follows every line that does not (`audit_stream.first_line`).
pub fn first_line(
    file: &File,
    mut low: i64,
    mut high: i64,
    satisfies: impl Fn(&[u8]) -> bool,
) -> io::Result<i64> {
    while low < high {
        let middle = (low + high).div_euclid(2);
        let start = next_line_start(file, middle, low, high)?;
        if start >= high {
            let (line, after) = line_at(file, low, high)?;
            if satisfies(&line) {
                return Ok(low);
            }
            low = after;
            continue;
        }
        let (line, after) = line_at(file, start, high)?;
        if satisfies(&line) {
            high = start;
        } else {
            low = after;
        }
    }
    Ok(low)
}

/// A line's field `index` between tabs, its invalid UTF-8 replaced, or `""` when the line has fewer fields.
pub fn field(line: &[u8], index: usize) -> String {
    line.split(|&byte| byte == b'\t')
        .nth(index)
        .map(|field| String::from_utf8_lossy(field).into_owned())
        .unwrap_or_default()
}

/// The sections of one plain part from byte `start`, the first line after its header, to `end`, or why the part is not in sections. A segment's line (`part` above 0) holds its configuration one field later than a row's.
pub fn audit_sections(
    file: &File,
    start: i64,
    end: i64,
    vocabulary: &Vocabulary,
    part: usize,
) -> io::Result<std::result::Result<Vec<Section>, String>> {
    let shift = usize::from(part > 0);
    let mut sections = Vec::new();
    let mut position = start;
    let mut last = -1;
    while position < end {
        let (line, _after) = line_at(file, position, end)?;
        let config = field(&line, shift);
        let ranked = vocabulary.rank(&config);
        if ranked <= last {
            return Ok(Err(format!(
                "the {} rows at byte {position} of part {part} do not rank above the section before them",
                repr_str(&config)
            )));
        }
        let position_end = first_line(file, position, end, |line| {
            vocabulary.rank(&field(line, shift)) > ranked
        })?;
        let (closing, _after) = line_at(
            file,
            last_line_start(file, position, position_end)?,
            position_end,
        )?;
        let closing = field(&closing, shift);
        if closing != config {
            return Ok(Err(format!(
                "the {} rows from byte {position} of part {part} end in a row of {}",
                repr_str(&config),
                repr_str(&closing)
            )));
        }
        sections.push(Section {
            config,
            rank: ranked,
            start: position,
            end: position_end,
            part,
            inflated: None,
        });
        last = ranked;
        position = position_end;
    }
    Ok(Ok(sections))
}

/// The sections of a part held as gzip members, one a member after its header's, from its index, or why the part is not in sections.
pub fn member_sections(
    members: &[Member],
    part: usize,
    vocabulary: &Vocabulary,
) -> std::result::Result<Vec<Section>, String> {
    let mut sections = Vec::new();
    let mut offset = members.first().map_or(0, |header| header.length);
    let mut last = -1;
    for member in members.iter().skip(1) {
        let ranked = vocabulary.rank(&member.section);
        if ranked <= last {
            return Err(format!(
                "the {} member of part {part} does not rank above the section before it",
                repr_str(&member.section)
            ));
        }
        sections.push(Section {
            config: member.section.clone(),
            rank: ranked,
            start: offset,
            end: offset + member.length,
            part,
            inflated: Some(member.inflated),
        });
        offset += member.length;
        last = ranked;
    }
    Ok(sections)
}

fn member_failure(section: &Section, why: &str) -> AuditError {
    let part = if section.part == 0 {
        "base".to_owned()
    } else {
        format!("segment {}", section.part)
    };
    let held = if section.config.is_empty() {
        "its header line".to_owned()
    } else {
        format!("the {} section", repr_str(&section.config))
    };
    AuditError::Member(format!(
        "the audit's {part} holds {held} in a member that {why}"
    ))
}

/// The inflated bytes of a section's gzip member from its start, at most `read_bytes` a call, read by `pread` a [`PIECE_SHARE`] of `read_bytes` at a time and inflated with gzip framing; a call at the member's end gives nothing. Read to its end, the member is checked against its section, and one that fails to inflate, whose stream ends before the section's end or runs past it, or that inflates past or short of its recorded bytes fails as [`AuditError::Member`].
pub struct MemberReader<'a> {
    file: &'a File,
    section: Section,
    inflated: i64,
    read_bytes: usize,
    piece: i64,
    inflater: Option<Decompressor>,
    position: i64,
    pending: Vec<u8>,
    total: i64,
}

impl<'a> MemberReader<'a> {
    pub fn new(file: &'a File, section: &Section, read_bytes: usize) -> Result<Self> {
        let Some(inflated) = section.inflated else {
            return error(format!(
                "the {} section of part {} is not held as a member",
                repr_str(&section.config),
                section.part
            ));
        };
        Ok(Self {
            file,
            section: section.clone(),
            inflated,
            read_bytes,
            piece: (read_bytes / PIECE_SHARE).max(1) as i64,
            inflater: Some(Decompressor::new(GZIP_WINDOW).map_err(WireError)?),
            position: section.start,
            pending: Vec::new(),
            total: 0,
        })
    }

    /// The member's next inflated bytes, or nothing at its end.
    pub fn read(&mut self) -> std::result::Result<Vec<u8>, AuditError> {
        let Self {
            file,
            section,
            inflated,
            read_bytes,
            piece,
            inflater: held,
            position,
            pending,
            total,
        } = self;
        let Some(inflater) = held.as_mut() else {
            return Ok(Vec::new());
        };
        let end = section.end;
        while !inflater.eof() {
            if pending.is_empty() && *position < end {
                *pending = pread(file, (*piece).min(end - *position), *position)?;
                if pending.is_empty() {
                    return Err(member_failure(
                        section,
                        &format!("ends at byte {position} of its part, inside its recorded length"),
                    ));
                }
                *position += pending.len() as i64;
            }
            let out = inflater
                .decompress(pending, *read_bytes)
                .map_err(|failure| {
                    member_failure(section, &format!("does not inflate: {failure}"))
                })?;
            *pending = inflater.unconsumed_tail().to_vec();
            if !out.is_empty() {
                *total += out.len() as i64;
                if *total > *inflated {
                    return Err(member_failure(
                        section,
                        &format!("inflates past the {inflated} bytes its index records"),
                    ));
                }
                return Ok(out);
            }
            if pending.is_empty() && *position >= end {
                break;
            }
        }
        let inflater = held.take().expect("the inflater is held");
        let recorded = end - section.start;
        if !inflater.eof() {
            return Err(member_failure(
                section,
                &format!("runs past its recorded {recorded} bytes"),
            ));
        }
        if !inflater.unused_data().is_empty() || !pending.is_empty() || *position < end {
            return Err(member_failure(
                section,
                &format!("ends before its recorded {recorded} bytes"),
            ));
        }
        if *total != *inflated {
            return Err(member_failure(
                section,
                &format!("inflates to {total} bytes where its index records {inflated}"),
            ));
        }
        Ok(Vec::new())
    }
}

/// A small member's whole inflated bytes, checked as [`MemberReader`] checks a member it reads to its end.
pub fn member_bytes(file: &File, section: &Section) -> std::result::Result<Vec<u8>, AuditError> {
    let mut reader = MemberReader::new(file, section, READ_BYTES)
        .map_err(|failure| AuditError::Value(failure.0))?;
    let mut bytes = Vec::new();
    loop {
        let out = reader.read()?;
        if out.is_empty() {
            return Ok(bytes);
        }
        bytes.extend_from_slice(&out);
    }
}

/// Inflate every section held as a gzip member to its end, discarding what it gives, so a member that does not inflate to what its index records fails.
pub fn check_members(files: &[File], sections: &[Section]) -> std::result::Result<(), AuditError> {
    for section in sections {
        if section.inflated.is_some() {
            let file = files
                .get(section.part)
                .ok_or_else(|| AuditError::Value(format!("no part {} is held", section.part)))?;
            let mut reader = MemberReader::new(file, section, READ_BYTES)
                .map_err(|failure| AuditError::Value(failure.0))?;
            while !reader.read()?.is_empty() {}
        }
    }
    Ok(())
}

fn header_repr(fields: &[&str]) -> String {
    let shown: Vec<String> = fields.iter().map(|field| repr_str(field)).collect();
    format!("[{}]", shown.join(", "))
}

/// Check the audit's header through its base and find the sections of the base and of each segment, sorted by rank and then part, or why it has none: an empty base or another header fails as `ValueError` with `path` in its text, a header that is not UTF-8 or holds a carriage return is why, and so are sections out of rank order in any part. `members` gives each part's member index, aligned with `files`, None for a plain part; a base held as members has its header member inflated for the check, a member that fails it failing as `MemberError`.
pub fn audit_layout(
    files: &[File],
    path: &str,
    members: &[Option<Vec<Member>>],
    vocabulary: &Vocabulary,
) -> std::result::Result<AuditLayout, AuditError> {
    if files.len() != members.len() || files.is_empty() {
        return Err(AuditError::Value(format!(
            "{path}: the audit holds {} parts for {} member indexes",
            files.len(),
            members.len()
        )));
    }
    let base = &files[0];
    let (line, start) = match &members[0] {
        None => {
            let size = size_of(base)?;
            if size == 0 {
                return Err(AuditError::Value(format!("{path}: empty audit, no header")));
            }
            line_at(base, 0, size)?
        }
        Some(index) => {
            let header = index.first().ok_or_else(|| {
                AuditError::Value(format!("{path}: the base's member index is empty"))
            })?;
            let mut line = member_bytes(
                base,
                &Section {
                    config: String::new(),
                    rank: -1,
                    start: 0,
                    end: header.length,
                    part: 0,
                    inflated: Some(header.inflated),
                },
            )?;
            if line.last() == Some(&b'\n') {
                line.pop();
            }
            (line, header.length)
        }
    };
    let not_sorted = |why: &str| AuditLayout {
        sections: None,
        why: why.to_owned(),
        rows_start: start,
    };
    let Ok(text) = std::str::from_utf8(&line) else {
        return Ok(not_sorted("its header is not UTF-8"));
    };
    if text.contains('\r') {
        return Ok(not_sorted("its header ends in a carriage return"));
    }
    let header: Vec<&str> = text.split('\t').collect();
    if header.len() != vocabulary.header.len()
        || header
            .iter()
            .zip(&vocabulary.header)
            .any(|(found, held)| found != held)
    {
        return Err(AuditError::Value(format!(
            "{path}: unexpected audit header {}",
            header_repr(&header)
        )));
    }
    let mut found = Vec::new();
    for (part, (file, index)) in files.iter().zip(members).enumerate() {
        let sections = match index {
            None => {
                let end = size_of(file)?;
                let rows = if part == 0 {
                    start
                } else {
                    line_at(file, 0, end)?.1
                };
                audit_sections(file, rows, end, vocabulary, part)?
            }
            Some(index) => member_sections(index, part, vocabulary),
        };
        match sections {
            Ok(sections) => found.extend(sections),
            Err(why) => return Ok(not_sorted(&why)),
        }
    }
    found.sort_by_key(|section| (section.rank, section.part));
    Ok(AuditLayout {
        sections: Some(found),
        why: String::new(),
        rows_start: start,
    })
}

pub(crate) fn integer(value: &PyValue, what: &str) -> Result<i64> {
    match value {
        PyValue::Int(number) => Ok(*number),
        other => error(format!("{what} is {}, not an int", other.kind())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::corpus::gzip::compress_member;

    struct Scratch(std::path::PathBuf);

    impl Scratch {
        fn new(label: &str) -> Self {
            let path = std::env::temp_dir()
                .join(format!("ams-review-audit-{label}-{}", std::process::id()));
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

    fn vocabulary() -> Vocabulary {
        Vocabulary {
            configs: ["default", "ss03", "ss04", "ss05", "ss03+ss05", "ss10"]
                .map(str::to_owned)
                .to_vec(),
            header: [
                "config",
                "codepoints",
                "kinds",
                "matched_entry",
                "baseline",
                "new",
            ]
            .map(str::to_owned)
            .to_vec(),
        }
    }

    const HEADER: &[u8] = b"config\tcodepoints\tkinds\tmatched_entry\tbaseline\tnew\n";

    fn rows(config: &str, count: usize, width: usize) -> Vec<u8> {
        (0..count)
            .flat_map(|index| {
                format!(
                    "{config}\t{:04X}\tcell\tentry\tbase\t{}\n",
                    0xE650 + index,
                    "x".repeat(width)
                )
                .into_bytes()
            })
            .collect()
    }

    #[test]
    fn lines_are_found_across_probes() {
        let scratch = Scratch::new("lines");
        let long = vec![b'a'; PROBE_BYTES * 2 + 5];
        let bytes = [&long[..], b"\nb\n", &long[..]].concat();
        let file = scratch.file("lines", &bytes);
        let end = bytes.len() as i64;
        let second = long.len() as i64 + 1;
        assert_eq!(line_at(&file, 0, end).unwrap(), (long.clone(), second));
        assert_eq!(line_at(&file, second + 2, end).unwrap(), (long, end));
        assert_eq!(next_line_start(&file, 3, 0, end).unwrap(), second);
        assert_eq!(next_line_start(&file, 0, 0, end).unwrap(), 0);
        assert_eq!(last_line_start(&file, 0, end).unwrap(), second + 2);
        assert_eq!(last_line_start(&file, 0, second + 2).unwrap(), second);
        assert_eq!(
            first_line(&file, 0, end, |line| line.starts_with(b"b")).unwrap(),
            second
        );
    }

    #[test]
    fn a_field_replaces_what_is_not_utf8() {
        assert_eq!(field(b"a\tb\tc", 1), "b");
        assert_eq!(field(b"a", 1), "");
        assert_eq!(field(b"\xf0\x80\x80\tx", 0), "\u{fffd}\u{fffd}\u{fffd}");
        assert_eq!(field(b"d\xe2\x82\tx", 0), "d\u{fffd}");
    }

    #[test]
    fn a_plain_audit_is_found_in_sections() {
        let scratch = Scratch::new("plain");
        let bytes = [HEADER, &rows("default", 5, 3), &rows("ss04", 2, 5000)].concat();
        let file = scratch.file("audit", &bytes);
        let layout = audit_layout(&[file], "audit.tsv", &[None], &vocabulary()).unwrap();
        let start = HEADER.len() as i64;
        let middle = start + rows("default", 5, 3).len() as i64;
        assert_eq!(layout.rows_start, start);
        assert_eq!(layout.why, "");
        let sections = layout.sections.unwrap();
        assert_eq!(
            sections
                .iter()
                .map(|section| (
                    section.config.as_str(),
                    section.rank,
                    section.start,
                    section.end
                ))
                .collect::<Vec<_>>(),
            [
                ("default", 0, start, middle),
                ("ss04", 2, middle, bytes.len() as i64)
            ]
        );
    }

    #[test]
    fn an_audit_out_of_order_says_why() {
        let scratch = Scratch::new("order");
        let bytes = [HEADER, &rows("ss03", 2, 1), &rows("default", 2, 1)].concat();
        let layout = audit_layout(
            &[scratch.file("audit", &bytes)],
            "audit.tsv",
            &[None],
            &vocabulary(),
        )
        .unwrap();
        assert_eq!(layout.sections, None);
        assert_eq!(
            layout.why,
            format!(
                "the 'ss03' rows from byte {} of part 0 end in a row of 'default'",
                HEADER.len()
            )
        );
        let crlf = [&HEADER[..HEADER.len() - 1], b"\r\n"].concat();
        let layout = audit_layout(
            &[scratch.file("crlf", &crlf)],
            "audit.tsv",
            &[None],
            &vocabulary(),
        )
        .unwrap();
        assert_eq!(layout.why, "its header ends in a carriage return");
        let failure = audit_layout(
            &[scratch.file("empty", b"")],
            "audit.tsv",
            &[None],
            &vocabulary(),
        )
        .unwrap_err();
        assert_eq!(failure.message(), "audit.tsv: empty audit, no header");
        let failure = audit_layout(
            &[scratch.file("other", b"config\tx'\n")],
            "audit.tsv",
            &[None],
            &vocabulary(),
        )
        .unwrap_err();
        assert_eq!(
            failure.message(),
            r#"audit.tsv: unexpected audit header ['config', "x'"]"#
        );
    }

    #[test]
    fn a_base_held_as_members_reads_its_header_member() {
        let scratch = Scratch::new("members");
        let header = compress_member(HEADER, 1, 0).unwrap();
        let body = rows("default", 3, 2);
        let section = compress_member(&body, 1, 0).unwrap();
        let bytes = [&header[..], &section[..]].concat();
        let index = vec![
            Member {
                section: String::new(),
                length: header.len() as i64,
                inflated: HEADER.len() as i64,
            },
            Member {
                section: "default".into(),
                length: section.len() as i64,
                inflated: body.len() as i64,
            },
        ];
        let layout = audit_layout(
            &[scratch.file("base", &bytes)],
            "audit.tsv",
            &[Some(index.clone())],
            &vocabulary(),
        )
        .unwrap();
        assert_eq!(layout.rows_start, header.len() as i64);
        assert_eq!(
            layout.sections.unwrap()[0].inflated,
            Some(body.len() as i64)
        );
        let mut short = index;
        short[0].inflated -= 1;
        let failure = audit_layout(
            &[scratch.file("short", &bytes)],
            "audit.tsv",
            &[Some(short)],
            &vocabulary(),
        )
        .unwrap_err();
        assert_eq!(failure.class(), "MemberError");
        assert_eq!(
            failure.message(),
            format!(
                "the audit's base holds its header line in a member that inflates past the {} bytes its index records",
                HEADER.len() - 1
            )
        );
    }
}
