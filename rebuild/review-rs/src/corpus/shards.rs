//! The shard writer the write puts each class's fragments into: a class written directly goes into base parts cut by [`PartLayout`] and named by [`base_part_names`], plain or deflated as the class's writes are; a class appended to keeps its parts from the previous corpus and takes every fragment that changed in its overflow parts ([`overflow_part_name`]), the last one cloned ([`clone`]) and cut before its closing bytes. Every part is staged under a `.partial` name, and the renames are handed to the caller ([`ShardWriter::take_pending`]); [`discard_runs`] deletes what a failed write staged.

use std::borrow::Cow;
use std::collections::HashMap;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use super::gzip::{deflate_fragment, inflate_fragment};
use crate::ffi;
use crate::pyfmt::decimal_value;
use crate::pyfmt::repr::repr_str;

/// What a class with no fragment's one part holds (`PartLayout.sizes`).
const EMPTY_PART: &[u8] = b"[]\n";

/// What the shard writer raises, by the class Python raises it as.
#[derive(Debug)]
pub enum ShardError {
    /// `ValueError`: a last overflow part that does not end as a part ends, or a stored deflated fragment that does not inflate to its length.
    Value(String),
    /// `KeyError`: a kept fragment's part that is not one of the open class's, as its key's `repr`.
    Key(String),
    /// `AssertionError`: a class opened over an open one, or closed with none open.
    Assertion(&'static str),
    /// `zlib.error`: a fragment that does not deflate.
    Zlib(String),
    /// `OSError`.
    Os(io::Error),
}

impl ShardError {
    /// The class Python raises, by its `__name__`.
    pub fn class(&self) -> &'static str {
        match self {
            Self::Value(_) => "ValueError",
            Self::Key(_) => "KeyError",
            Self::Assertion(_) => "AssertionError",
            Self::Zlib(_) => "error",
            Self::Os(_) => "OSError",
        }
    }
}

impl fmt::Display for ShardError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Value(message) | Self::Key(message) | Self::Zlib(message) => {
                formatter.write_str(message)
            }
            Self::Assertion(message) => formatter.write_str(message),
            Self::Os(failure) => write!(formatter, "{failure}"),
        }
    }
}

impl From<io::Error> for ShardError {
    fn from(failure: io::Error) -> Self {
        Self::Os(failure)
    }
}

/// `unit_index.PartLayout`: the rule a from-scratch build cuts one class's fragments into parts by. A part is `[`, its fragments joined by commas, and the closing bytes, and it closes before the fragment that would take it past the cap, so every part stays within the cap but one holding a single fragment that alone exceeds it.
#[derive(Clone, Debug)]
pub struct PartLayout {
    cap: i64,
    closing: i64,
    sizes: Vec<i64>,
    open: bool,
}

impl PartLayout {
    /// A layout under `cap` bytes a part, whose parts close with `closing` bytes.
    pub fn new(cap: i64, closing: usize) -> Self {
        Self {
            cap,
            closing: closing as i64,
            sizes: Vec::new(),
            open: false,
        }
    }

    /// The next fragment's part index and byte start, and whether it opens its part (else a comma precedes it).
    pub fn place(&mut self, length: i64) -> (usize, i64, bool) {
        if let Some(last) = self.sizes.last_mut()
            && self.open
            && *last + 1 + length + self.closing > self.cap
        {
            *last += self.closing;
            self.open = false;
        }
        if !self.open {
            self.sizes.push(1 + length);
            self.open = true;
            return (self.sizes.len() - 1, 1, true);
        }
        let last = self.sizes.last_mut().expect("an open part");
        let start = *last + 1;
        *last = start + length;
        (self.sizes.len() - 1, start, false)
    }

    /// Close the layout and return each part's size; a class with no fragment is one part, `[]\n`.
    pub fn sizes(&mut self) -> Vec<i64> {
        if self.open {
            if let Some(last) = self.sizes.last_mut() {
                *last += self.closing;
            }
            self.open = false;
        }
        if self.sizes.is_empty() {
            self.sizes.push(EMPTY_PART.len() as i64);
        }
        self.sizes.clone()
    }
}

/// `unit_index.base_part_names`: the bare `units/<class>.json` for one part, and `units/<class>.000.json` onward for more.
pub fn base_part_names(class_id: &str, count: usize) -> Vec<String> {
    if count == 1 {
        return vec![format!("units/{class_id}.json")];
    }
    (0..count)
        .map(|index| format!("units/{class_id}.{index:03}.json"))
        .collect()
}

/// Overflow part `index`, `units/<class>.x<NNN>.json`, with `index` as its decimal digits.
pub fn overflow_part_name(class_id: &str, index: &str) -> String {
    format!("units/{class_id}.x{index:0>3}.json")
}

/// `unit_index.overflow_index`: the number of `part` if it is `units/<class>.x<digits>.json` with three digits or more, each any decimal digit, as its value's decimal digits with no leading zero; else None. The number can pass 64 bits, as Python's `int` reads it.
pub fn overflow_index(class_id: &str, part: &str) -> Option<String> {
    let digits = part
        .strip_prefix("units/")?
        .strip_prefix(class_id)?
        .strip_prefix(".x")?
        .strip_suffix(".json")?;
    let values = digits
        .chars()
        .map(decimal_value)
        .collect::<Option<Vec<u32>>>()?;
    if values.len() < 3 {
        return None;
    }
    let number: String = values
        .iter()
        .skip_while(|&&value| value == 0)
        .map(|&value| char::from_digit(value, 10).expect("a decimal digit"))
        .collect();
    Some(if number.is_empty() {
        "0".to_owned()
    } else {
        number
    })
}

/// Two numbers' decimal digits, neither with a leading zero, compared as the numbers.
fn numeric_order(left: &str, right: &str) -> std::cmp::Ordering {
    left.len().cmp(&right.len()).then_with(|| left.cmp(right))
}

/// One more than a number, as decimal digits.
fn one_more(digits: &str) -> String {
    let mut bytes = digits.as_bytes().to_vec();
    for byte in bytes.iter_mut().rev() {
        if *byte == b'9' {
            *byte = b'0';
        } else {
            *byte += 1;
            return String::from_utf8(bytes).expect("ASCII digits");
        }
    }
    bytes.insert(0, b'1');
    String::from_utf8(bytes).expect("ASCII digits")
}

/// `build._clone`: make `target`, which must not exist, a copy-on-write clone of `source` (`clonefile(2)`) and return true, or return false, having written nothing, where the platform or the volume cannot clone.
pub fn clone(source: &Path, target: &Path) -> io::Result<bool> {
    match ffi::clone_file(source, target) {
        None => Ok(false),
        Some(Ok(())) => Ok(true),
        Some(Err(failure))
            if failure
                .raw_os_error()
                .is_some_and(|code| ffi::CANNOT_CLONE.contains(&code)) =>
        {
            Ok(false)
        }
        Some(Err(failure)) => Err(failure),
    }
}

/// `shutil.copyfile`: `target` made or truncated, holding `source`'s bytes.
fn copy_file(source: &Path, target: &Path) -> io::Result<()> {
    io::copy(&mut File::open(source)?, &mut File::create(target)?).map(|_| ())
}

fn unlink_missing_ok(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Err(failure) if failure.kind() == io::ErrorKind::NotFound => Ok(()),
        other => other,
    }
}

/// A fragment's address as the writer returns it: its part index, its byte start and length on disk, and its inflated length, 0 for a plain fragment.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Span {
    pub part: usize,
    pub start: i64,
    pub length: i64,
    pub inflated: i64,
}

/// Classes written one fragment at a time into byte-capped shard parts staged under `.partial` names, directly into base parts or appended to the overflow parts after the parts the previous corpus holds. It holds only the open part's handle and the running byte count between calls. `appended` counts the bytes appended to overflow parts, each fragment with its comma, `appended_inflated` the plain bytes those inflate to, and `deflated` the fragments written deflated by any route.
pub struct ShardWriter {
    out_dir: PathBuf,
    units_dir: PathBuf,
    cap: i64,
    closing: Vec<u8>,
    pending: Vec<(PathBuf, PathBuf)>,
    staged: Vec<PathBuf>,
    class_id: Option<String>,
    appending: bool,
    deflating: bool,
    parts: Vec<String>,
    index: HashMap<String, usize>,
    layout: PartLayout,
    handle: Option<BufWriter<File>>,
    open_index: usize,
    size: i64,
    next_overflow: String,
    last_overflow: Option<String>,
    pub appended: i64,
    pub appended_inflated: i64,
    pub deflated: i64,
}

impl ShardWriter {
    /// A writer into `out_dir`'s `units/`, which it makes, under `cap` bytes a part (`SHARD_PART_BYTES`), each part closed by `closing` (`PART_CLOSING`).
    pub fn new(out_dir: &Path, cap: i64, closing: Vec<u8>) -> io::Result<Self> {
        let units_dir = out_dir.join("units");
        fs::create_dir_all(&units_dir)?;
        let layout = PartLayout::new(cap, closing.len());
        Ok(Self {
            out_dir: out_dir.to_path_buf(),
            units_dir,
            cap,
            closing,
            pending: Vec::new(),
            staged: Vec::new(),
            class_id: None,
            appending: false,
            deflating: false,
            parts: Vec::new(),
            index: HashMap::new(),
            layout,
            handle: None,
            open_index: 0,
            size: 0,
            next_overflow: "0".to_owned(),
            last_overflow: None,
            appended: 0,
            appended_inflated: 0,
            deflated: 0,
        })
    }

    /// Whether the open class is appended to, keeping its parts from the previous corpus.
    pub fn appending(&self) -> bool {
        self.appending
    }

    /// Open a class: appended to over its parts from the previous corpus (`prior_parts`) when `append`, else written directly; its fragments written deflated when `deflate`.
    pub fn open(
        &mut self,
        class_id: &str,
        prior_parts: &[String],
        append: bool,
        deflate: bool,
    ) -> std::result::Result<(), ShardError> {
        if self.class_id.is_some() {
            return Err(ShardError::Assertion("close the open class first"));
        }
        self.class_id = Some(class_id.to_owned());
        self.appending = append;
        self.deflating = deflate;
        self.parts = if append {
            prior_parts.to_vec()
        } else {
            Vec::new()
        };
        self.index = self
            .parts
            .iter()
            .enumerate()
            .map(|(index, part)| (part.clone(), index))
            .collect();
        self.layout = PartLayout::new(self.cap, self.closing.len());
        self.handle = None;
        self.open_index = 0;
        self.size = 0;
        let overflow: Vec<(String, &String)> = self
            .parts
            .iter()
            .filter_map(|part| overflow_index(class_id, part).map(|number| (number, part)))
            .collect();
        self.next_overflow = overflow
            .iter()
            .map(|(number, _)| number.as_str())
            .max_by(|left, right| numeric_order(left, right))
            .map_or_else(|| "0".to_owned(), one_more);
        self.last_overflow = overflow.last().map(|(_, part)| (*part).clone());
        Ok(())
    }

    /// Whether `part` is one of the open class's parts from the previous corpus, where a kept fragment can stay.
    pub fn holds(&self, part: &str) -> bool {
        self.appending && self.index.contains_key(part)
    }

    /// The span of a fragment that stays where the previous corpus holds it, in one of the open class's parts, with its inflated length.
    pub fn keep(
        &self,
        part: &str,
        start: i64,
        length: i64,
        inflated: i64,
    ) -> std::result::Result<Span, ShardError> {
        let Some(&index) = self.index.get(part) else {
            return Err(ShardError::Key(repr_str(part)));
        };
        Ok(Span {
            part: index,
            start,
            length,
            inflated,
        })
    }

    fn class_id(&self) -> &str {
        self.class_id.as_deref().unwrap_or("None")
    }

    fn staging_base(&self, index: usize) -> PathBuf {
        self.units_dir
            .join(format!("{}.{index:03}.json.partial", self.class_id()))
    }

    fn stage(&mut self, path: PathBuf) -> io::Result<BufWriter<File>> {
        self.staged.push(path.clone());
        Ok(BufWriter::new(File::create(path)?))
    }

    fn end_open(&mut self) -> io::Result<()> {
        if let Some(mut handle) = self.handle.take() {
            handle.write_all(&self.closing)?;
            handle.flush()?;
        }
        Ok(())
    }

    fn write(&mut self, bytes: &[u8]) -> io::Result<()> {
        self.handle.as_mut().expect("an open part").write_all(bytes)
    }

    fn add_direct(&mut self, body: &[u8]) -> io::Result<(usize, i64, i64)> {
        let length = body.len() as i64;
        let (index, start, first) = self.layout.place(length);
        if first {
            self.end_open()?;
            let handle = self.stage(self.staging_base(index))?;
            self.handle = Some(handle);
            self.write(b"[")?;
        } else {
            self.write(b",")?;
        }
        self.write(body)?;
        Ok((index, start, length))
    }

    /// Open the overflow part the next appended fragment goes to: the last one, cloned and cut before its closing bytes, while the fragment fits beside what it holds, else a new one.
    fn open_overflow(&mut self, length: i64) -> std::result::Result<(), ShardError> {
        let closing = self.closing.len() as i64;
        if let Some(last) = self.last_overflow.take() {
            let size = fs::metadata(self.out_dir.join(&last))?.len() as i64 - closing;
            if size + 1 + length + closing <= self.cap {
                let staging = self.out_dir.join(format!("{last}.partial"));
                unlink_missing_ok(&staging)?;
                self.staged.push(staging.clone());
                if !clone(&self.out_dir.join(&last), &staging)? {
                    copy_file(&self.out_dir.join(&last), &staging)?;
                }
                let mut handle = OpenOptions::new().read(true).write(true).open(&staging)?;
                let Ok(at) = u64::try_from(size) else {
                    return Err(ShardError::Os(io::Error::from(io::ErrorKind::InvalidInput)));
                };
                handle.seek(SeekFrom::Start(at))?;
                let mut tail = Vec::new();
                handle.read_to_end(&mut tail)?;
                if tail != self.closing {
                    return Err(ShardError::Value(format!(
                        "{last} does not end as a shard part ends"
                    )));
                }
                handle.set_len(at)?;
                handle.seek(SeekFrom::Start(at))?;
                self.handle = Some(BufWriter::new(handle));
                self.open_index = self.index[&last];
                self.size = size;
                return Ok(());
            }
        }
        let name = overflow_part_name(self.class_id(), &self.next_overflow);
        self.next_overflow = one_more(&self.next_overflow);
        let handle = self.stage(self.out_dir.join(format!("{name}.partial")))?;
        self.handle = Some(handle);
        self.write(b"[")?;
        self.open_index = self.parts.len();
        self.parts.push(name);
        self.size = 1;
        Ok(())
    }

    fn append(&mut self, body: &[u8]) -> std::result::Result<(usize, i64, i64), ShardError> {
        let length = body.len() as i64;
        if self.handle.is_some() && self.size + 1 + length + self.closing.len() as i64 > self.cap {
            self.end_open()?;
        }
        if self.handle.is_none() {
            self.open_overflow(length)?;
        }
        let start = if self.size > 1 {
            self.write(b",")?;
            self.size + 1
        } else {
            1
        };
        self.write(body)?;
        self.size = start + length;
        self.appended += length + 1;
        Ok((self.open_index, start, length))
    }

    /// Write one fragment into the next base slot, or appended to the overflow parts, and return its span. `body` is the fragment's plain shard bytes, deflated here when the class's writes deflate, or, with `inflated` nonzero, its deflated bytes as a deflated slot stores them and `inflated` their plain length: written as they were read when the class's writes deflate, and inflated, checked, for a class written plain.
    pub fn add_bytes(
        &mut self,
        body: &[u8],
        inflated: u32,
    ) -> std::result::Result<Span, ShardError> {
        let (body, inflated): (Cow<[u8]>, i64) = if inflated != 0 && !self.deflating {
            (
                Cow::Owned(
                    inflate_fragment(body, Some(inflated as usize)).map_err(ShardError::Value)?,
                ),
                0,
            )
        } else if inflated == 0 && self.deflating {
            (
                Cow::Owned(deflate_fragment(body).map_err(ShardError::Zlib)?),
                body.len() as i64,
            )
        } else {
            (Cow::Borrowed(body), i64::from(inflated))
        };
        let (part, start, length) = if self.appending {
            let placed = self.append(&body)?;
            self.appended_inflated += if inflated != 0 { inflated } else { placed.2 } + 1;
            placed
        } else {
            self.add_direct(&body)?
        };
        if inflated != 0 {
            self.deflated += 1;
        }
        Ok(Span {
            part,
            start,
            length,
            inflated,
        })
    }

    /// Close the open class and return its parts as the manifest lists them, base parts then overflow parts, with their total bytes; their renames join the pending ones.
    pub fn close(&mut self) -> std::result::Result<(Vec<String>, i64), ShardError> {
        let Some(class_id) = self.class_id.clone() else {
            return Err(ShardError::Assertion("no class is open"));
        };
        self.end_open()?;
        let (names, total) = if self.appending {
            let names = self.parts.clone();
            let mut total = 0;
            for name in &names {
                let staging = self.out_dir.join(format!("{name}.partial"));
                if self.staged.contains(&staging) {
                    total += fs::metadata(&staging)?.len() as i64;
                    self.pending.push((staging, self.out_dir.join(name)));
                } else {
                    total += fs::metadata(self.out_dir.join(name))?.len() as i64;
                }
            }
            (names, total)
        } else {
            let empty = self.staged.is_empty();
            let sizes = self.layout.sizes();
            let names = base_part_names(&class_id, sizes.len());
            if empty {
                let mut handle = self.stage(self.staging_base(0))?;
                handle.write_all(EMPTY_PART)?;
                handle.flush()?;
            }
            for (index, name) in names.iter().enumerate() {
                let staging = self.staging_base(index);
                self.pending.push((staging, self.out_dir.join(name)));
            }
            (names, sizes.iter().sum())
        };
        self.staged.clear();
        self.class_id = None;
        self.parts.clear();
        self.index.clear();
        Ok((names, total))
    }

    /// The pending renames, staging file then target, handed to the caller to make, after which [`Self::abort`] leaves their staging files in place.
    pub fn take_pending(&mut self) -> Vec<(PathBuf, PathBuf)> {
        std::mem::take(&mut self.pending)
    }

    /// Delete every staging file the writer made and has not handed over.
    pub fn abort(&mut self) -> io::Result<()> {
        if let Some(mut handle) = self.handle.take() {
            handle.flush()?;
        }
        for path in &self.staged {
            unlink_missing_ok(path)?;
        }
        for (staging, _) in &self.pending {
            unlink_missing_ok(staging)?;
        }
        self.staged.clear();
        self.pending.clear();
        self.class_id = None;
        Ok(())
    }
}

/// Delete the shard parts the write's runs staged for the classes `class_ids`, every entry of `out_dir/units/` that `Path.glob("<class>.*.json.partial")` finds for one of them, matched as `glob.translate` reads the pattern, the class id's own wildcards included. A directory that does not list finds nothing; a pattern `re` refuses fails as it does.
pub fn discard_runs<'a>(
    out_dir: &Path,
    class_ids: impl IntoIterator<Item = &'a str>,
) -> io::Result<()> {
    let units_dir = out_dir.join("units");
    for class_id in class_ids {
        let pattern =
            glob_pattern(&format!("{class_id}.*.json.partial")).map_err(io::Error::other)?;
        let Ok(entries) = fs::read_dir(&units_dir) else {
            continue;
        };
        for entry in entries.flatten() {
            if glob_matches(
                &pattern,
                &surrogate_escaped(entry.file_name().as_encoded_bytes()),
            ) {
                unlink_missing_ok(&entry.path())?;
            }
        }
    }
    Ok(())
}

/// A name's code points as Python decodes it with `surrogateescape`: each byte that is not UTF-8 as U+DC80 and up.
fn surrogate_escaped(name: &[u8]) -> Vec<u32> {
    let mut points = Vec::with_capacity(name.len());
    for chunk in name.utf8_chunks() {
        points.extend(chunk.valid().chars().map(u32::from));
        points.extend(chunk.invalid().iter().map(|&byte| 0xdc00 + u32::from(byte)));
    }
    points
}

/// One element of a glob pattern as `fnmatch._translate` translates it.
#[derive(Clone, Debug, PartialEq, Eq)]
enum Glob {
    /// `*`, any run of characters.
    Star,
    /// `?`, or the negated empty set `[!]`'s `.`: any one character.
    Any,
    /// A character that matches itself.
    Literal(u32),
    /// A set as `re` reads the one `fnmatch` writes: whether it is negated, and its characters and inclusive ranges; empty and not negated, it never matches (`(?!)`).
    Set(bool, Vec<(u32, u32)>),
}

/// `fnmatch._translate` over one path segment, as `glob.translate` hands it the segment: `*` and `?`, sets in brackets, and every other character itself; a `[` with no closing bracket is itself.
fn glob_pattern(pattern: &str) -> std::result::Result<Vec<Glob>, String> {
    let pat: Vec<char> = pattern.chars().collect();
    let n = pat.len();
    let mut globs = Vec::new();
    let mut i = 0;
    while i < n {
        let c = pat[i];
        i += 1;
        match c {
            '*' => {
                globs.push(Glob::Star);
                while i < n && pat[i] == '*' {
                    i += 1;
                }
            }
            '?' => globs.push(Glob::Any),
            '[' => {
                let mut j = i;
                if j < n && pat[j] == '!' {
                    j += 1;
                }
                if j < n && pat[j] == ']' {
                    j += 1;
                }
                while j < n && pat[j] != ']' {
                    j += 1;
                }
                if j >= n {
                    globs.push(Glob::Literal(u32::from('[')));
                } else {
                    globs.push(glob_set(&pat[i..j])?);
                    i = j + 1;
                }
            }
            other => globs.push(Glob::Literal(u32::from(other))),
        }
    }
    Ok(globs)
}

/// `chars` with each backslash and, when `hyphens`, each hyphen escaped, as `fnmatch._translate` escapes a set's chunks.
fn escaped(chars: &[char], hyphens: bool) -> Vec<char> {
    let mut out = Vec::with_capacity(chars.len());
    for &letter in chars {
        if letter == '\\' || hyphens && letter == '-' {
            out.push('\\');
        }
        out.push(letter);
    }
    out
}

/// One bracketed set, from what stands between its brackets: the regex set `fnmatch._translate` writes for it, its chunks cut at each `-` that makes a range and its empty ranges removed, read back as `re` reads a set.
fn glob_set(inside: &[char]) -> std::result::Result<Glob, String> {
    let stuff: Vec<char> = if inside.contains(&'-') {
        let mut chunks: Vec<Vec<char>> = Vec::new();
        let mut i = 0;
        let mut k = if inside[0] == '!' { 2 } else { 1 };
        while let Some(found) = inside
            .get(k..)
            .and_then(|rest| rest.iter().position(|&letter| letter == '-'))
        {
            k += found;
            chunks.push(inside[i..k].to_vec());
            i = k + 1;
            k += 3;
        }
        let chunk = inside[i..].to_vec();
        if chunk.is_empty() {
            chunks
                .last_mut()
                .expect("a chunk before a final hyphen")
                .push('-');
        } else {
            chunks.push(chunk);
        }
        for k in (1..chunks.len()).rev() {
            if chunks[k - 1].last() > chunks[k].first() {
                let mut joined = chunks[k - 1][..chunks[k - 1].len() - 1].to_vec();
                joined.extend_from_slice(&chunks[k][1..]);
                chunks[k - 1] = joined;
                chunks.remove(k);
            }
        }
        let mut stuff = Vec::new();
        for (place, chunk) in chunks.iter().enumerate() {
            if place > 0 {
                stuff.push('-');
            }
            stuff.extend(escaped(chunk, true));
        }
        stuff
    } else {
        escaped(inside, false)
    };
    if stuff.is_empty() {
        return Ok(Glob::Set(false, Vec::new()));
    }
    if stuff == ['!'] {
        return Ok(Glob::Any);
    }
    let mut set = Vec::with_capacity(stuff.len() + 2);
    for letter in stuff {
        if matches!(letter, '&' | '~' | '|') {
            set.push('\\');
        }
        set.push(letter);
    }
    if set[0] == '!' {
        set[0] = '^';
    } else if set[0] == '^' || set[0] == '[' {
        set.insert(0, '\\');
    }
    set.push(']');
    regex_set(&set)
}

/// One item of a regex set: an escaped character, or a character with whether it is a bare `]`.
fn set_item(set: &[char], at: &mut usize) -> Option<(u32, bool)> {
    let letter = *set.get(*at)?;
    *at += 1;
    if letter == '\\' {
        let escaped = *set.get(*at)?;
        *at += 1;
        return Some((u32::from(escaped), false));
    }
    Some((u32::from(letter), letter == ']'))
}

/// A regex set as `re._parser` reads it after its `[`: `^` negating it, a bare `]` closing it once it holds something, and a `-` between two items making a range, or a literal before the closing `]`.
fn regex_set(set: &[char]) -> std::result::Result<Glob, String> {
    let mut at = 0;
    let negated = set.first() == Some(&'^');
    if negated {
        at = 1;
    }
    let mut members: Vec<(u32, u32)> = Vec::new();
    loop {
        let Some((low, closing)) = set_item(set, &mut at) else {
            return Err("unterminated character set".to_owned());
        };
        if closing && !members.is_empty() {
            break;
        }
        if set.get(at) == Some(&'-') {
            at += 1;
            let Some((high, closing)) = set_item(set, &mut at) else {
                return Err("unterminated character set".to_owned());
            };
            if closing {
                members.push((low, low));
                members.push((u32::from('-'), u32::from('-')));
                break;
            }
            if high < low {
                return Err("bad character range".to_owned());
            }
            members.push((low, high));
        } else {
            members.push((low, low));
        }
    }
    Ok(Glob::Set(negated, members))
}

fn glob_one(glob: &Glob, letter: u32) -> bool {
    match glob {
        Glob::Star | Glob::Any => true,
        Glob::Literal(own) => *own == letter,
        Glob::Set(negated, members) => {
            members
                .iter()
                .any(|&(low, high)| (low..=high).contains(&letter))
                != *negated
        }
    }
}

/// Whether the whole name matches the pattern.
fn glob_matches(pattern: &[Glob], name: &[u32]) -> bool {
    let (mut at, mut letter) = (0, 0);
    let mut star: Option<(usize, usize)> = None;
    while letter < name.len() {
        match pattern.get(at) {
            Some(Glob::Star) => {
                star = Some((at, letter));
                at += 1;
            }
            Some(glob) if glob_one(glob, name[letter]) => {
                at += 1;
                letter += 1;
            }
            _ => match star {
                Some((star_at, star_letter)) => {
                    at = star_at + 1;
                    letter = star_letter + 1;
                    star = Some((star_at, star_letter + 1));
                }
                None => return false,
            },
        }
    }
    pattern[at..].iter().all(|glob| *glob == Glob::Star)
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Scratch(PathBuf);

    impl Scratch {
        fn new(name: &str) -> Self {
            let path = std::env::temp_dir().join(format!(
                "ams-review-shards-test-{name}-{}",
                std::process::id()
            ));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).expect("scratch");
            Self(path)
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    const CLOSING: &[u8] = b"\n]\n";

    #[test]
    fn a_layout_closes_a_part_before_the_fragment_that_would_pass_the_cap() {
        let mut layout = PartLayout::new(20, CLOSING.len());
        assert_eq!(layout.place(5), (0, 1, true));
        assert_eq!(layout.place(5), (0, 7, false));
        assert_eq!(layout.place(4), (0, 13, false));
        assert_eq!(layout.place(1), (1, 1, true));
        assert_eq!(layout.place(30), (2, 1, true));
        assert_eq!(layout.sizes(), vec![20, 5, 34]);
        assert_eq!(PartLayout::new(20, 3).sizes(), vec![3]);
    }

    #[test]
    fn parts_are_named_bare_numbered_and_overflowing_as_unit_index_names_them() {
        assert_eq!(base_part_names("c", 1), vec!["units/c.json"]);
        assert_eq!(
            base_part_names("c", 2),
            vec!["units/c.000.json", "units/c.001.json"]
        );
        assert_eq!(overflow_part_name("c", "7"), "units/c.x007.json");
        assert_eq!(overflow_part_name("c", "1000"), "units/c.x1000.json");
        assert_eq!(
            overflow_index("c", "units/c.x007.json").as_deref(),
            Some("7")
        );
        assert_eq!(
            overflow_index("c", "units/c.x000.json").as_deref(),
            Some("0")
        );
        assert_eq!(
            overflow_index("c", "units/c.x٠١٢.json").as_deref(),
            Some("12")
        );
        assert_eq!(overflow_index("c", "units/c.x00.json"), None);
        assert_eq!(overflow_index("a.b", "units/aXb.x000.json"), None);
        assert_eq!(one_more("0"), "1");
        assert_eq!(one_more("999"), "1000");
        assert_eq!(one_more("18446744073709551615"), "18446744073709551616");
    }

    #[test]
    fn an_append_clones_the_last_overflow_part_and_cuts_its_closing_bytes() {
        let scratch = Scratch::new("append");
        fs::create_dir_all(scratch.0.join("units")).expect("units");
        fs::write(scratch.0.join("units/c.json"), b"[aaaa\n]\n").expect("base");
        fs::write(scratch.0.join("units/c.x000.json"), b"[bb\n]\n").expect("overflow");
        let mut writer = ShardWriter::new(&scratch.0, 64, CLOSING.to_vec()).expect("writer");
        let parts = ["units/c.json".to_owned(), "units/c.x000.json".to_owned()];
        writer.open("c", &parts, true, false).expect("open");
        assert!(writer.holds("units/c.json") && !writer.holds("units/d.json"));
        let span = writer.add_bytes(b"cc", 0).expect("add");
        assert_eq!(
            span,
            Span {
                part: 1,
                start: 4,
                length: 2,
                inflated: 0
            }
        );
        let (names, total) = writer.close().expect("close");
        assert_eq!(names, parts);
        assert_eq!(total, 8 + 9);
        for (staging, target) in writer.take_pending() {
            fs::rename(staging, target).expect("rename");
        }
        assert_eq!(
            fs::read(scratch.0.join("units/c.x000.json")).expect("read"),
            b"[bb,cc\n]\n"
        );
    }

    #[test]
    fn a_staged_part_is_discarded_by_its_class_ids_glob() {
        let matches = |pattern: &str, name: &str| {
            glob_matches(
                &glob_pattern(pattern).expect("a pattern re reads"),
                &name.chars().map(u32::from).collect::<Vec<_>>(),
            )
        };
        assert!(matches("a.*.json.partial", "a.000.json.partial"));
        assert!(matches("a.*.json.partial", "a..json.partial"));
        assert!(!matches("a.*.json.partial", "ab.000.json.partial"));
        assert!(matches("[ab].*.json.partial", "b.1.json.partial"));
        assert!(!matches("[!a].*.json.partial", "a.1.json.partial"));
        assert!(matches("[!a].*.json.partial", "b.1.json.partial"));
        assert!(!matches("[z-a].*.json.partial", "z.1.json.partial"));
        assert!(matches("a[.*.json.partial", "a[.1.json.partial"));
        assert!(matches("?.*.json.partial", "].1.json.partial"));
        assert!(matches("[a-c-e].*.json.partial", "-.1.json.partial"));
        assert!(!matches("[a-c-e].*.json.partial", "d.1.json.partial"));
    }
}
