//! The ink-signature store (`doc/rebuild-design.md` §14.2): the prior store's keys in one sorted column beside their 32-byte digests and a hit bit an entry, the misses shaped before the stream and the late ones it shapes itself in two more, the new store's lines merged from them in key order, and the store written as one gzip stream at level 1 through its staging file. A store reads as absent on any read error but `zlib.error`, which fails the load ([`StoreError`]), as does JSON the reader reads into a value it does not hold.
//!
//! A window's signature text ([`signature_text`]) is what the misses' shaping hands [`InkComparator::signature`].
//!
//! The misses are found before the stream reads the audit ([`find_misses`]): skipped when the prior store's stamps hold, otherwise by the key-only read ([`key_only_read`]), which cuts the windows into `KEY_ONLY_PROCESSES` ranges and walks each on a thread of its own, reading the audit through the inherited files, the hits marked and the misses kept in range order. [`InkShaping`] shapes them ([`InkShaping::shape_found_misses`]) on threads that share the font facts and the before and after fonts, each thread's shape memo released after each chunk, and the stream's late shaper ([`LateShaper`]) shapes any key neither the prior store nor the misses hold.

use std::collections::{BTreeSet, HashMap};
use std::fs::File;
use std::io::{self, BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use crate::corpus::audit::{AuditError, Section};
use crate::corpus::columns::sorted_by_word;
use crate::corpus::gzip::{GzipError, GzipReader, GzipWriter, MTIME, PART_LEVEL};
use crate::corpus::input::{BuildInput, Stamp};
use crate::corpus::sink::staging_path;
use crate::corpus::stream::{
    FamilyLines, Lockstep, StreamError, WindowKey, parse_window, sibling_window, signature_key,
    window_ranges,
};
use crate::fontfacts::{FontFacts, Side};
use crate::ink::{self, InkComparator};
use crate::pyfmt::repr::repr;
use crate::pyfmt::{PyValue, json};
use crate::shape::{Fonts, Shaper};
use crate::wire::{Init, Result, WireError, error};

/// The header key of the audit's identity the store was written over.
pub const AUDIT_IDENTITY_KEY: &str = "audit_identity";
/// The header key of the family keys' digest the store was written over.
pub const FAMILY_KEYS_KEY: &str = "family_keys_digest";
/// The stamp's label whose per-file lines a miss note expands (`signature_miss_note`'s code label).
pub const CODE_LABEL: &str = "comparator_code";
/// The changed files a miss note names inside the code label.
pub const CODE_FILES_SHOWN: usize = 4;
/// The changed labels a miss note names (`fingerprint.moved_note`'s default limit).
pub const LABELS_SHOWN: usize = 8;
/// What a key-only read thread fails with when its walk fails but for a break or a member.
pub const KEY_ONLY_EXIT: &str = "a key-only read process exited 1";

/// One entry line: 16 hex digits, a tab, 64 hex digits and a newline.
const LINE_BYTES: usize = 82;
/// The entry lines read and checked at once.
const READ_LINES: usize = 12_800;
/// What `gzip.open`'s reader buffers its plain bytes in (`gzip.READ_BUFFER_SIZE`).
const READ_BUFFER_BYTES: usize = 128 * 1024;
const HEX: &[u8; 16] = b"0123456789abcdef";

/// The vocabulary's names of the store: its format and file name (`SIGNATURE_STORE_FORMAT`, `SIGNATURE_STORE_NAME`), and the notes for no store and a store that does not read (`NO_STORE_NOTE`, `UNREADABLE_NOTE`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StoreNames {
    pub format: String,
    pub name: String,
    pub no_store: String,
    pub unreadable: String,
}

impl StoreNames {
    /// The names as the build input's vocabulary gives them.
    pub fn of(input: &BuildInput) -> Result<Self> {
        let constant = |name: &str| {
            let dotted = format!("rebuild.review.unit_cache.{name}");
            match input.constant(&dotted) {
                Some(PyValue::Str(value)) => Ok(value.clone()),
                _ => error(format!(
                    "the build input's vocabulary holds no str {dotted}"
                )),
            }
        };
        Ok(Self {
            format: constant("SIGNATURE_STORE_FORMAT")?,
            name: constant("SIGNATURE_STORE_NAME")?,
            no_store: constant("NO_STORE_NOTE")?,
            unreadable: constant("UNREADABLE_NOTE")?,
        })
    }

    /// The store's path under `out_dir` (`unit_cache.signature_store_path`).
    pub fn path(&self, out_dir: &Path) -> PathBuf {
        out_dir.join(&self.name)
    }
}

/// A read of the store that fails rather than reading as absent.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum StoreError {
    /// `zlib.error`: deflate data that does not inflate, which the readers' `except` does not name.
    Zlib(String),
    /// A header `json.loads` reads into a value the reader does not hold.
    Unheld(String),
}

impl StoreError {
    /// The class Python raises, by its `__name__`, or `Unheld` where Python raises nothing.
    pub fn class(&self) -> &'static str {
        match self {
            Self::Zlib(_) => "error",
            Self::Unheld(_) => "Unheld",
        }
    }

    pub fn message(&self) -> &str {
        match self {
            Self::Zlib(message) | Self::Unheld(message) => message,
        }
    }
}

impl std::fmt::Display for StoreError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.class(), self.message())
    }
}

impl std::error::Error for StoreError {}

type Read<T> = std::result::Result<T, StoreError>;

/// What a read failure gives: [`StoreError::Zlib`] for `zlib.error`, which raises, and no store for any other, an `OSError`, a `BadGzipFile` or an `EOFError`.
fn unread<T>(failure: &io::Error) -> Read<Option<T>> {
    match GzipError::of(failure) {
        Some(GzipError::Zlib(message)) => Err(StoreError::Zlib(message.clone())),
        _ => Ok(None),
    }
}

/// The store's file opened as `gzip.open(path, "rb")` reads it, or None when `path` is not a file or does not open.
fn open_store(path: &Path) -> Option<BufReader<GzipReader<File>>> {
    if !path.is_file() {
        return None;
    }
    File::open(path)
        .ok()
        .map(|file| BufReader::with_capacity(READ_BUFFER_BYTES, GzipReader::new(file)))
}

/// The header line `next(stream)` reads, None for an empty stream (`StopIteration`) or a read that fails but for `zlib.error`, and the header it parses to, None when `json.loads` refuses it.
fn read_header_line(stream: &mut impl BufRead) -> Read<Option<PyValue>> {
    let mut line = Vec::new();
    if let Err(failure) = stream.read_until(b'\n', &mut line) {
        return unread(&failure);
    }
    if line.is_empty() {
        return Ok(None);
    }
    match json::loads_bytes(&line) {
        Ok(header) => Ok(Some(header)),
        Err(failure) if json::unheld(&failure) => Err(StoreError::Unheld(failure.0)),
        Err(_) => Ok(None),
    }
}

/// Fill `buffer` from `stream`, short only at its end, as `io.BufferedReader.read(n)` reads.
fn read_full(stream: &mut impl io::Read, buffer: &mut [u8]) -> io::Result<usize> {
    let mut filled = 0;
    while filled < buffer.len() {
        match stream.read(&mut buffer[filled..]) {
            Ok(0) => break,
            Ok(count) => filled += count,
            Err(failure) if failure.kind() == io::ErrorKind::Interrupted => {}
            Err(failure) => return Err(failure),
        }
    }
    Ok(filled)
}

fn hex_value(digit: u8) -> Option<u8> {
    match digit {
        b'0'..=b'9' => Some(digit - b'0'),
        b'a'..=b'f' => Some(digit - b'a' + 10),
        _ => None,
    }
}

/// One entry line's key and digest, or None for a line other than 16 lowercase hex digits, a tab, 64 lowercase hex digits and a newline.
fn entry(line: &[u8; LINE_BYTES]) -> Option<(u64, [u8; 32])> {
    if line[16] != b'\t' || line[81] != b'\n' {
        return None;
    }
    let mut key = 0u64;
    for &digit in &line[..16] {
        key = (key << 4) | u64::from(hex_value(digit)?);
    }
    let mut digest = [0u8; 32];
    for (byte, pair) in digest.iter_mut().zip(line[17..81].as_chunks::<2>().0) {
        *byte = (hex_value(pair[0])? << 4) | hex_value(pair[1])?;
    }
    Some((key, digest))
}

pub(crate) fn get<'a>(entries: &'a [(PyValue, PyValue)], name: &str) -> Option<&'a PyValue> {
    entries.iter().find_map(|(key, value)| match key {
        PyValue::Str(key) if key == name => Some(value),
        _ => None,
    })
}

pub(crate) fn is_str(value: Option<&PyValue>, text: &str) -> bool {
    matches!(value, Some(PyValue::Str(held)) if held == text)
}

/// Whether a value Python's `==` holds equal to the int `value`: an equal int, or the bool of that int.
pub(crate) fn int_equal(held: &PyValue, value: i64) -> bool {
    match held {
        PyValue::Int(held) => *held == value,
        PyValue::Bool(flag) => i64::from(*flag) == value,
        _ => false,
    }
}

/// `dict[key] = value` over a dict's entries: a held key keeps its place.
fn update(entries: &mut Vec<(PyValue, PyValue)>, key: &PyValue, value: &PyValue) {
    match entries.iter_mut().find(|(held, _)| held == key) {
        Some(found) => found.1 = value.clone(),
        None => entries.push((key.clone(), value.clone())),
    }
}

/// Of each run of equal words in `order`, the last position: the entry a later assignment to one dict key keeps.
fn last_of_each(words: &[u64], order: &[u32]) -> Vec<usize> {
    order
        .iter()
        .enumerate()
        .filter(|&(position, &at)| {
            position + 1 == order.len() || words[order[position + 1] as usize] != words[at as usize]
        })
        .map(|(_, &at)| at as usize)
        .collect()
}

/// One entry's line as `write_signature_store` writes it: `%016x`, a tab, the digest's hex and a newline.
fn entry_line(key: u64, digest: &[u8; 32]) -> [u8; LINE_BYTES] {
    let mut line = [0u8; LINE_BYTES];
    for (at, slot) in line[..16].iter_mut().enumerate() {
        *slot = HEX[((key >> (60 - 4 * at)) & 0xf) as usize];
    }
    line[16] = b'\t';
    for (at, byte) in digest.iter().enumerate() {
        line[17 + 2 * at] = HEX[usize::from(byte >> 4)];
        line[18 + 2 * at] = HEX[usize::from(byte & 0xf)];
    }
    line[81] = b'\n';
    line
}

/// The ink-signature store in memory, as columns: the prior store's keys, strictly ascending, beside their digests, a bit a prior entry marking each the build hit, and the prior store's header, None when there was no usable store; the misses, sorted by key once they are shaped ([`SignatureArrays::set_misses`]); and the late misses the stream shaped itself ([`SignatureArrays::add_late`]).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SignatureArrays {
    keys: Vec<u64>,
    digests: Vec<[u8; 32]>,
    hit: Vec<u8>,
    header: Option<PyValue>,
    miss_keys: Vec<u64>,
    miss_digests: Vec<[u8; 32]>,
    late_keys: Vec<u64>,
    late_digests: Vec<[u8; 32]>,
}

impl SignatureArrays {
    fn with_prior(keys: Vec<u64>, digests: Vec<[u8; 32]>, header: PyValue) -> Self {
        let hit = vec![0; keys.len().div_ceil(8)];
        Self {
            keys,
            digests,
            hit,
            header: Some(header),
            ..Self::default()
        }
    }

    /// Read the prior store under `out_dir` into columns: None when it is not a file, does not read, holds a header that is not a dict or names another format or stamp than `environment`, holds a line other than an entry line, checked [`READ_LINES`] lines at a time before more are read, or holds keys that do not strictly ascend.
    pub fn load(out_dir: &Path, names: &StoreNames, environment: &str) -> Read<Option<Self>> {
        let Some(mut stream) = open_store(&names.path(out_dir)) else {
            return Ok(None);
        };
        let Some(header) = read_header_line(&mut stream)? else {
            return Ok(None);
        };
        let PyValue::Dict(entries) = &header else {
            return Ok(None);
        };
        if !is_str(get(entries, "format"), &names.format)
            || !is_str(get(entries, "environment"), environment)
        {
            return Ok(None);
        }
        let mut keys = Vec::new();
        let mut digests = Vec::new();
        let mut chunk = vec![0u8; LINE_BYTES * READ_LINES];
        loop {
            let filled = match read_full(&mut stream, &mut chunk) {
                Ok(filled) => filled,
                Err(failure) => return unread(&failure),
            };
            if filled == 0 {
                break;
            }
            if filled % LINE_BYTES != 0 {
                return Ok(None);
            }
            for line in chunk[..filled].as_chunks::<LINE_BYTES>().0 {
                let Some((key, digest)) = entry(line) else {
                    return Ok(None);
                };
                keys.push(key);
                digests.push(digest);
            }
        }
        if keys.windows(2).any(|pair| pair[1] <= pair[0]) {
            return Ok(None);
        }
        Ok(Some(Self::with_prior(keys, digests, header)))
    }

    pub fn is_empty(&self) -> bool {
        self.keys.is_empty()
    }

    /// The prior store's keys, ascending, as the key-only read hands them over.
    pub fn prior_keys(&self) -> &[u64] {
        &self.keys
    }

    /// The prior store's header, None when there was no usable store.
    pub fn header(&self) -> Option<&PyValue> {
        self.header.as_ref()
    }

    /// Whether the prior store was written over this audit and these family keys: its header's identity equals `identity` and its family keys' digest `family_digest`, each as Python's `==` compares them.
    pub fn stamps_hold(&self, identity: &[i64], family_digest: &str) -> bool {
        let Some(PyValue::Dict(entries)) = &self.header else {
            return false;
        };
        let identity_holds = match get(entries, AUDIT_IDENTITY_KEY) {
            Some(PyValue::List(held)) => {
                held.len() == identity.len()
                    && held
                        .iter()
                        .zip(identity)
                        .all(|(held, &value)| int_equal(held, value))
            }
            _ => false,
        };
        identity_holds && is_str(get(entries, FAMILY_KEYS_KEY), family_digest)
    }

    /// The prior entry holding `key`, or None.
    pub fn prior_index(&self, key: u64) -> Option<usize> {
        self.keys.binary_search(&key).ok()
    }

    /// Mark a prior entry hit.
    pub fn mark_hit(&mut self, position: usize) {
        self.hit[position >> 3] |= 1 << (position & 7);
    }

    fn is_hit(&self, position: usize) -> bool {
        self.hit[position >> 3] & (1 << (position & 7)) != 0
    }

    /// The prior entry's digest at `position`.
    pub fn prior_digest(&self, position: usize) -> &[u8; 32] {
        &self.digests[position]
    }

    /// The digest for `key`: a prior entry's, which it marks hit, or a shaped miss's; None when neither holds it. The late misses are not searched, since the stream asks for each key once.
    pub fn digest_of(&mut self, key: u64) -> Option<[u8; 32]> {
        if let Some(position) = self.prior_index(key) {
            self.mark_hit(position);
            return Some(self.digests[position]);
        }
        self.miss_keys
            .binary_search(&key)
            .ok()
            .map(|position| self.miss_digests[position])
    }

    /// Take the shaped misses, each key with its digest in miss order, sorted by key; of two misses with one key the later is kept.
    pub fn set_misses(&mut self, keys: &[u64], digests: &[[u8; 32]]) {
        let kept = last_of_each(keys, &sorted_by_word(keys));
        self.miss_keys = kept.iter().map(|&at| keys[at]).collect();
        self.miss_digests = kept.iter().map(|&at| digests[at]).collect();
    }

    /// Take one miss the stream met and shaped itself.
    pub fn add_late(&mut self, key: u64, digest: [u8; 32]) {
        self.late_keys.push(key);
        self.late_digests.push(digest);
    }

    pub fn hits(&self) -> usize {
        self.hit.iter().map(|byte| byte.count_ones() as usize).sum()
    }

    pub fn misses(&self) -> usize {
        self.miss_keys.len() + self.late_keys.len()
    }

    pub fn unhit(&self) -> usize {
        self.keys.len() - self.hits()
    }

    /// Whether the store a build would write is the prior store's lines: there was one, every prior entry was hit, and nothing missed.
    pub fn unchanged(&self) -> bool {
        self.header.is_some() && self.misses() == 0 && self.hits() == self.keys.len()
    }

    /// The misses with the late ones among them, sorted by key, a miss ahead of a late miss with its key, of late misses with one key the later only.
    fn merged_extras(&self) -> (Vec<u64>, Vec<[u8; 32]>) {
        let late = last_of_each(&self.late_keys, &sorted_by_word(&self.late_keys));
        let mut merged: Vec<(u64, [u8; 32])> = self
            .miss_keys
            .iter()
            .copied()
            .zip(self.miss_digests.iter().copied())
            .chain(
                late.iter()
                    .map(|&at| (self.late_keys[at], self.late_digests[at])),
            )
            .collect();
        merged.sort_by_key(|&(key, _)| key);
        merged.into_iter().unzip()
    }

    /// Write the new store's entry lines in key order: the hit prior entries merged with the misses, the late ones among them, a miss ahead of a prior entry only when its key is smaller. Returns the lines written.
    pub fn write_lines(&self, out: &mut impl Write) -> io::Result<usize> {
        let merged;
        let (extra_keys, extra_digests): (&[u64], &[[u8; 32]]) = if self.late_keys.is_empty() {
            (&self.miss_keys, &self.miss_digests)
        } else {
            merged = self.merged_extras();
            (&merged.0, &merged.1)
        };
        let mut extra = 0;
        let mut written = 0;
        for (position, &key) in self.keys.iter().enumerate() {
            if !self.is_hit(position) {
                continue;
            }
            while extra < extra_keys.len() && extra_keys[extra] < key {
                out.write_all(&entry_line(extra_keys[extra], &extra_digests[extra]))?;
                extra += 1;
                written += 1;
            }
            out.write_all(&entry_line(key, &self.digests[position]))?;
            written += 1;
        }
        for at in extra..extra_keys.len() {
            out.write_all(&entry_line(extra_keys[at], &extra_digests[at]))?;
            written += 1;
        }
        Ok(written)
    }
}

/// Write the store under `out_dir` from `signatures`: a header line in `json.dumps`' default form of the format, then `stamp_header` (the stamp's fields, `unit_cache._stamp_header`) and `stamps` merged over it as a dict display merges them, then the entry lines ([`SignatureArrays::write_lines`]), as one gzip stream at level 1 and mtime 0 named for the store, through its staging file, renamed into place; the staging file is removed whether or not the write succeeds.
pub fn write_signature_arrays(
    out_dir: &Path,
    names: &StoreNames,
    stamp_header: &[(PyValue, PyValue)],
    signatures: &SignatureArrays,
    stamps: &[(PyValue, PyValue)],
) -> io::Result<()> {
    let mut header = vec![(PyValue::str("format"), PyValue::str(&names.format))];
    for (key, value) in stamp_header.iter().chain(stamps) {
        update(&mut header, key, value);
    }
    let mut line = json::dumps(&PyValue::Dict(header)).map_err(io::Error::other)?;
    line.push('\n');
    let path = names.path(out_dir);
    let staging = staging_path(&path);
    let written = (|| {
        let file = File::create(&staging)?;
        let mut stream = GzipWriter::new(BufWriter::new(file), &names.name, PART_LEVEL, MTIME)?;
        stream.write_all(line.as_bytes())?;
        signatures.write_lines(&mut stream)?;
        stream
            .finish()?
            .into_inner()
            .map_err(|failure| failure.into_error())?;
        std::fs::rename(&staging, &path)
    })();
    let _ = std::fs::remove_file(&staging);
    written
}

/// The store's write on a thread of its own, which owns the columns and drops them once the store is written. The build starts it as the units phase starts and joins it in the cache phase; dropped unjoined, as a build that stops early drops it, it waits for the write to end, so the build's result follows the store's write.
pub struct SignatureWrite {
    thread: Option<std::thread::JoinHandle<io::Result<()>>>,
}

impl SignatureWrite {
    /// Start writing the store under `out_dir` as [`write_signature_arrays`] writes it.
    pub fn start(
        out_dir: PathBuf,
        names: StoreNames,
        stamp_header: Vec<(PyValue, PyValue)>,
        signatures: SignatureArrays,
        stamps: Vec<(PyValue, PyValue)>,
    ) -> io::Result<Self> {
        let thread = std::thread::Builder::new()
            .name("ink-signature-store".to_owned())
            .spawn(move || {
                write_signature_arrays(&out_dir, &names, &stamp_header, &signatures, &stamps)
            })?;
        Ok(Self {
            thread: Some(thread),
        })
    }

    /// Wait for the write and give what it gave, a panic in it as an error.
    pub fn join(mut self) -> io::Result<()> {
        match self.thread.take() {
            Some(thread) => thread.join().map_err(|panic| {
                io::Error::other(format!(
                    "the ink-signature store's write panicked: {}",
                    crate::pool::panic_message(&*panic)
                ))
            })?,
            None => Ok(()),
        }
    }
}

impl Drop for SignatureWrite {
    fn drop(&mut self) {
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// The store's header as `unit_cache.read_header` reads it: its first line parsed, None when it does not read or is not a dict.
pub(crate) fn read_header(path: &Path) -> Read<Option<Vec<(PyValue, PyValue)>>> {
    let Ok(file) = File::open(path) else {
        return Ok(None);
    };
    let mut stream = BufReader::with_capacity(READ_BUFFER_BYTES, GzipReader::new(file));
    Ok(match read_header_line(&mut stream)? {
        Some(PyValue::Dict(entries)) => Some(entries),
        _ => None,
    })
}

/// `label\tdigest` lines as a label's digest by label, a later line with one label taking it (`fingerprint._labels_of`).
pub(crate) fn labels_of<S: AsRef<str>>(lines: &[S]) -> HashMap<String, String> {
    lines
        .iter()
        .map(|line| {
            let (label, digest) = line
                .as_ref()
                .split_once('\t')
                .unwrap_or((line.as_ref(), ""));
            (label.to_owned(), digest.to_owned())
        })
        .collect()
}

/// A header's recorded lines as labels: None unless a list, each item taken as `str` takes it.
pub(crate) fn recorded_labels(lines: Option<&PyValue>) -> Option<HashMap<String, String>> {
    let Some(PyValue::List(lines)) = lines else {
        return None;
    };
    let lines: Vec<String> = lines
        .iter()
        .map(|line| match line {
            PyValue::Str(line) => line.clone(),
            other => repr(other).expect("a JSON value has a repr"),
        })
        .collect();
    Some(labels_of(&lines))
}

/// The labels that differ between two label maps, as `fingerprint.moved_note` names them: each changed shared label in order, as `label: note` where `expand` gives a note for it and `label (changed)` otherwise, then the new labels and the gone ones, each in order, at most `limit` of them and a count of the rest; None when none differ.
fn moved_note(
    recorded: &HashMap<String, String>,
    current: &HashMap<String, String>,
    limit: usize,
    expand: Option<(&str, Option<&str>)>,
) -> Option<String> {
    let recorded_names: BTreeSet<&String> = recorded.keys().collect();
    let current_names: BTreeSet<&String> = current.keys().collect();
    let mut moved: Vec<String> = Vec::new();
    for &name in recorded_names.intersection(&current_names) {
        if recorded[name] == current[name] {
            continue;
        }
        let inner = expand.and_then(|(label, note)| if label == name { note } else { None });
        moved.push(match inner {
            Some(inner) if !inner.is_empty() => format!("{name}: {inner}"),
            _ => format!("{name} (changed)"),
        });
    }
    moved.extend(
        current_names
            .difference(&recorded_names)
            .map(|name| format!("{name} (new)")),
    );
    moved.extend(
        recorded_names
            .difference(&current_names)
            .map(|name| format!("{name} (gone)")),
    );
    if moved.is_empty() {
        return None;
    }
    let shown = moved[..limit.min(moved.len())].join(", ");
    Some(if moved.len() > limit {
        format!("{shown} and {} more", moved.len() - limit)
    } else {
        shown
    })
}

/// Why [`SignatureArrays::load`] rejects the store under `out_dir`, or None when its header matches `environment`: no store, a header that does not read, or a stamp that moved, named through its lines with the code label's per-file lines expanded.
pub fn signature_miss_note(
    out_dir: &Path,
    names: &StoreNames,
    environment: &Stamp,
) -> Read<Option<String>> {
    let path = names.path(out_dir);
    if !path.is_file() {
        return Ok(Some(names.no_store.clone()));
    }
    let header = read_header(&path)?;
    Ok(miss_note(
        header.as_deref(),
        environment,
        CODE_LABEL,
        None,
        &names.unreadable,
    ))
}

/// Why a load of a store whose header, or head's stamps, is `header` fails under `environment`: `unreadable` for no header, a stamp that moved, named through its lines with `code_label`'s per-file lines expanded, and, where `manifest` names the manifest's identity and its note, that note for a header stamped for another manifest; None when everything checked matches.
pub(crate) fn miss_note(
    header: Option<&[(PyValue, PyValue)]>,
    environment: &Stamp,
    code_label: &str,
    manifest: Option<(&str, &str)>,
    unreadable: &str,
) -> Option<String> {
    let Some(entries) = header else {
        return Some(unreadable.to_owned());
    };
    if !is_str(get(entries, "environment"), &environment.value) {
        let Some(recorded) = recorded_labels(get(entries, "environment_lines")) else {
            return Some("the stamp moved".to_owned());
        };
        let recorded_code = match get(entries, "environment_detail") {
            Some(PyValue::Dict(detail)) => recorded_labels(get(detail, code_label)),
            _ => None,
        };
        let current_code = environment
            .detail
            .iter()
            .find(|(label, _)| label == code_label)
            .map(|(_, lines)| labels_of(lines))
            .unwrap_or_default();
        let code_note = recorded_code
            .map(|recorded_code| moved_note(&recorded_code, &current_code, CODE_FILES_SHOWN, None));
        let expand = code_note.as_ref().map(|note| (code_label, note.as_deref()));
        return Some(
            match moved_note(
                &recorded,
                &labels_of(&environment.lines),
                LABELS_SHOWN,
                expand,
            ) {
                Some(moved) => format!("the stamp moved at {moved}"),
                None => "the stamp moved".to_owned(),
            },
        );
    }
    match manifest {
        Some((stamp, moved)) if !is_str(get(entries, "manifest_sha256"), stamp) => {
            Some(moved.to_owned())
        }
        _ => None,
    }
}

/// The text a window's ink signature is taken over, its code points as characters. A code point that is no Unicode scalar value is refused, since no text the shaper reads holds one.
pub fn signature_text(window: &str) -> Result<String> {
    parse_window(window)?
        .into_iter()
        .map(|point| {
            u32::try_from(point)
                .ok()
                .and_then(char::from_u32)
                .ok_or_else(|| {
                    WireError(format!(
                        "the window {window:?} holds {point:#X}, which is no Unicode scalar value"
                    ))
                })
        })
        .collect()
}

/// Run `job` over the indices `0..count` on at most `width` scoped threads, each thread making its own state with `make` and taking the next index as it finishes one, and give the results in index order. A thread that panics panics the caller.
fn indexed<S, T: Send>(
    count: usize,
    width: usize,
    make: impl Fn() -> S + Sync,
    job: impl Fn(&mut S, usize) -> T + Sync,
) -> Vec<T> {
    if count == 0 {
        return Vec::new();
    }
    let next = AtomicUsize::new(0);
    let mut slots: Vec<Option<T>> = std::iter::repeat_with(|| None).take(count).collect();
    std::thread::scope(|scope| {
        let workers: Vec<_> = (0..width.clamp(1, count))
            .map(|_| {
                scope.spawn(|| {
                    let mut state = make();
                    let mut done = Vec::new();
                    loop {
                        let at = next.fetch_add(1, Ordering::Relaxed);
                        if at >= count {
                            break done;
                        }
                        done.push((at, job(&mut state, at)));
                    }
                })
            })
            .collect();
        for worker in workers {
            match worker.join() {
                Ok(done) => {
                    for (at, value) in done {
                        slots[at] = Some(value);
                    }
                }
                Err(panic) => std::panic::resume_unwind(panic),
            }
        }
    });
    slots
        .into_iter()
        .map(|slot| slot.expect("every index ran"))
        .collect()
}

/// What the key-only read and the late walk read: the held audit's files and its sections, and the family keys a window's family lines name.
#[derive(Clone, Copy)]
pub struct HeldWindows<'a> {
    pub files: &'a [File],
    pub sections: &'a [Section],
    pub family_of: &'a [(i64, String)],
    pub family_keys: &'a [(String, String)],
}

/// What the key-only read found: whether it ran, false when the prior store's stamps held; its seconds and its window ranges, a thread each at most; the misses' keys, section indices and windows in window order; and the first break in range order, for which the build refuses the audit ([`super::not_a_stream`]).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct KeyOnly {
    pub ran: bool,
    pub seconds: f64,
    pub ranges: usize,
    pub keys: Vec<u64>,
    pub sections: Vec<u8>,
    pub windows: Vec<String>,
    pub broken: Option<String>,
}

/// A key-only read that fails the build.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MissError {
    /// A member that does not inflate to what its index records.
    Member(String),
    /// A `SystemExit` with this text.
    Exit(String),
    /// Any other failure, which Python raises as itself.
    Failed(String),
}

impl std::fmt::Display for MissError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Member(message) => write!(formatter, "MemberError: {message}"),
            Self::Exit(message) => write!(formatter, "SystemExit: {message}"),
            Self::Failed(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for MissError {}

/// How one range's walk ended: done, at a break, at a member that fails, or at any other failure.
enum RangeEnd {
    Done,
    Break(String),
    Member(String),
    Failed,
}

/// One range's walk: the prior entries it hit and the misses it found, each a key, a section index and a window, in window order, and how it ended.
struct RangeFound {
    hits: Vec<usize>,
    misses: Vec<(u64, usize, String)>,
    end: RangeEnd,
}

/// Walk one window range of the audit in lockstep, and key every row of each window that holds siblings against the prior store's keys, as one key-only read process does.
fn read_range(
    audit: HeldWindows,
    prior: &[u64],
    (low, high): &(Option<WindowKey>, Option<WindowKey>),
) -> RangeFound {
    let mut found = RangeFound {
        hits: Vec::new(),
        misses: Vec::new(),
        end: RangeEnd::Done,
    };
    let mut family = FamilyLines::new(audit.family_of, audit.family_keys);
    let mut walk = |found: &mut RangeFound| -> std::result::Result<(), StreamError> {
        let mut stream = Lockstep::new(audit.files, audit.sections, low.as_ref(), high.as_ref())?;
        while let Some((window, rows)) = stream.next_window()? {
            if !sibling_window(&rows) {
                continue;
            }
            let values = parse_window(&window)
                .map_err(|failure| StreamError::Audit(AuditError::Value(failure.0)))?;
            let suffix = family.suffix(&values);
            for (section, fields, _line) in &rows {
                let key = signature_key(fields, &suffix);
                match prior.binary_search(&key) {
                    Ok(position) => found.hits.push(position),
                    Err(_) => found.misses.push((key, *section, window.clone())),
                }
            }
        }
        Ok(())
    };
    found.end = match walk(&mut found) {
        Ok(()) => RangeEnd::Done,
        Err(StreamError::Break(why)) => RangeEnd::Break(why),
        Err(StreamError::Audit(AuditError::Member(message))) => RangeEnd::Member(message),
        Err(_) => RangeEnd::Failed,
    };
    found
}

/// The key-only read: cut the windows into at most `count` ranges, walk each on one of at most `threads` threads, and take what they found in range order: the hits marked in `signatures`, the misses kept, and the first break. A range whose walk fails fails the read, the first failing range in range order deciding how: a member that does not inflate as [`MissError::Member`], any other failure as [`KEY_ONLY_EXIT`].
pub fn key_only_read(
    audit: HeldWindows,
    signatures: &mut SignatureArrays,
    count: i64,
    threads: usize,
) -> std::result::Result<KeyOnly, MissError> {
    let started = Instant::now();
    let ranges =
        window_ranges(audit.files, audit.sections, count).map_err(|failure| match failure {
            AuditError::Member(message) => MissError::Member(message),
            other => MissError::Failed(other.to_string()),
        })?;
    let prior = signatures.prior_keys();
    let found = indexed(
        ranges.len(),
        threads,
        || (),
        |_, at| read_range(audit, prior, &ranges[at]),
    );
    for range in &found {
        match &range.end {
            RangeEnd::Member(message) => return Err(MissError::Member(message.clone())),
            RangeEnd::Failed => return Err(MissError::Exit(KEY_ONLY_EXIT.to_owned())),
            RangeEnd::Done | RangeEnd::Break(_) => {}
        }
    }
    let mut key_only = KeyOnly {
        ran: true,
        ranges: ranges.len(),
        ..KeyOnly::default()
    };
    for range in found {
        for position in range.hits {
            signatures.mark_hit(position);
        }
        for (key, section, window) in range.misses {
            key_only.keys.push(key);
            key_only.sections.push(u8::try_from(section).map_err(|_| {
                MissError::Failed(format!(
                    "the section index {section} does not fit the misses' byte column"
                ))
            })?);
            key_only.windows.push(window);
        }
        if let RangeEnd::Break(why) = range.end
            && key_only.broken.is_none()
        {
            key_only.broken = Some(why);
        }
    }
    key_only.seconds = started.elapsed().as_secs_f64();
    Ok(key_only)
}

/// Find every miss before the stream reads the audit: nothing, and the read not run, when the prior store's stamps hold for this audit's `identity` and the family keys' `family_digest`, since no key can then miss; otherwise the key-only read ([`key_only_read`]).
pub fn find_misses(
    audit: HeldWindows,
    signatures: &mut SignatureArrays,
    identity: &[i64],
    family_digest: &str,
    count: i64,
    threads: usize,
) -> std::result::Result<KeyOnly, MissError> {
    if signatures.stamps_hold(identity, family_digest) {
        return Ok(KeyOnly::default());
    }
    key_only_read(audit, signatures, count, threads)
}

/// What the misses and the late shaper shape with, opened from the units init: the font facts, the before and after fonts as HarfBuzz fonts and the ink's grid, shared by every shaping thread.
pub struct InkShaping {
    facts: FontFacts,
    fonts: Fonts,
    pixel_size: i64,
}

impl InkShaping {
    /// Open the units init at `units_init`'s font facts and its before and after fonts, refusing an init whose HarfBuzz version is not the linked one.
    pub fn open(units_init: &Path) -> Result<Self> {
        let init = Init::read(units_init)?;
        let facts = FontFacts::read(&init.font_facts)?;
        let fonts =
            Fonts::open_before_after(&init, &facts).map_err(|failure| WireError(failure.0))?;
        let pixel_size = ink::pixel_size(&init).map_err(|failure| WireError(failure.0))?;
        Ok(Self {
            facts,
            fonts,
            pixel_size,
        })
    }

    /// One text's signature digest under one configuration (`signature_digest(comparator.signature(text, config))`).
    fn digest(&self, shaper: &mut Shaper, text: &str, config: &str) -> Result<[u8; 32]> {
        let comparator =
            InkComparator::new(&self.facts, self.pixel_size, Side::Before, Side::After);
        let signature = comparator
            .signature(shaper, text, config)
            .map_err(|failure| {
                WireError(format!("cannot shape {text:?} under {config}: {failure}"))
            })?;
        Ok(signature.digest(&self.facts))
    }

    /// Shape `count` misses, `miss` giving each one's window and configuration, and give their digests in miss order with the threads they were shaped on: the misses cut into chunks of about an eighth of a thread's share, as the Python pool cuts them, taken in turn by at most `threads` threads, each with its own shaper whose memo it releases after each chunk; a window's text ([`signature_text`]) is made once for a run of misses in one window.
    pub fn shape_misses<'m>(
        &self,
        count: usize,
        miss: impl Fn(usize) -> (&'m str, &'m str) + Sync,
        threads: usize,
    ) -> Result<(Vec<[u8; 32]>, usize)> {
        if count == 0 {
            return Ok((Vec::new(), 1));
        }
        let width = threads.clamp(1, count);
        let chunk = (count / (width * 8)).max(1);
        let chunks = count.div_ceil(chunk);
        let shaped = indexed(
            chunks,
            width,
            || Shaper::new(&self.fonts, &self.facts),
            |shaper, at| -> Result<Vec<[u8; 32]>> {
                let mut digests = Vec::with_capacity(chunk);
                let mut last: Option<&str> = None;
                let mut text = String::new();
                for index in at * chunk..((at + 1) * chunk).min(count) {
                    let (window, config) = miss(index);
                    if last != Some(window) {
                        text = signature_text(window)?;
                        last = Some(window);
                    }
                    digests.push(self.digest(shaper, &text, config)?);
                }
                shaper.release();
                Ok(digests)
            },
        );
        let mut digests = Vec::with_capacity(count);
        for chunk in shaped {
            digests.extend(chunk?);
        }
        Ok((digests, width.min(chunks)))
    }

    /// Shape the key-only read's misses ([`InkShaping::shape_misses`]) into the store's columns in miss order, which is window order, each under its section's configuration, and drop the read's lists of them; give how many were shaped and on how many threads.
    pub fn shape_found_misses(
        &self,
        key_only: &mut KeyOnly,
        sections: &[Section],
        signatures: &mut SignatureArrays,
        threads: usize,
    ) -> Result<(usize, usize)> {
        let (windows, indices) = (&key_only.windows, &key_only.sections);
        let (digests, width) = self.shape_misses(
            key_only.keys.len(),
            |at| {
                (
                    windows[at].as_str(),
                    sections[usize::from(indices[at])].config.as_str(),
                )
            },
            threads,
        )?;
        signatures.set_misses(&key_only.keys, &digests);
        let shaped = key_only.keys.len();
        key_only.keys = Vec::new();
        key_only.sections = Vec::new();
        key_only.windows = Vec::new();
        Ok((shaped, width))
    }

    /// The stream's late shaper over `signatures`, shaping on this thread through one shaper made at its first shape, whose memo it releases after each.
    pub fn late<'s>(
        &'s self,
        signatures: &'s mut SignatureArrays,
    ) -> LateShaper<'s, impl FnMut(&str, &str) -> Result<[u8; 32]> + 's> {
        let mut shaper: Option<Shaper<'s>> = None;
        LateShaper::new(signatures, move |window: &str, config: &str| {
            let shaper = shaper.get_or_insert_with(|| Shaper::new(&self.fonts, &self.facts));
            let digest = signature_text(window).and_then(|text| self.digest(shaper, &text, config));
            shaper.release();
            digest
        })
    }
}

/// The stream's digest lookup: a key's digest from the store's columns ([`SignatureArrays::digest_of`]), or, for a key neither the prior store nor the misses hold, which only a wrong skip of the key-only read gives, the digest `shape` gives the window under the configuration, kept as a late miss ([`SignatureArrays::add_late`]) and counted in `shaped`.
pub struct LateShaper<'s, F> {
    signatures: &'s mut SignatureArrays,
    shape: F,
    pub shaped: usize,
}

impl<'s, F: FnMut(&str, &str) -> Result<[u8; 32]>> LateShaper<'s, F> {
    pub fn new(signatures: &'s mut SignatureArrays, shape: F) -> Self {
        Self {
            signatures,
            shape,
            shaped: 0,
        }
    }

    /// The digest of the row keyed `key` in `window` under `config`.
    pub fn digest(&mut self, key: u64, window: &str, config: &str) -> Result<[u8; 32]> {
        if let Some(digest) = self.signatures.digest_of(key) {
            return Ok(digest);
        }
        let digest = (self.shape)(window, config)?;
        self.signatures.add_late(key, digest);
        self.shaped += 1;
        Ok(digest)
    }
}

const KEY_ONLY_PROCESSES: &str = "rebuild.review.build.KEY_ONLY_PROCESSES";

/// The key-only read's ranges, the vocabulary's `KEY_ONLY_PROCESSES`.
pub(crate) fn key_only_count(input: &BuildInput) -> Result<i64> {
    match input.constant(KEY_ONLY_PROCESSES) {
        Some(PyValue::Int(count)) => Ok(*count),
        _ => error(format!(
            "the build input's vocabulary holds no int {KEY_ONLY_PROCESSES}"
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::corpus::gzip::tests::read_gzip;
    use serde_json::Map;

    fn names() -> StoreNames {
        StoreNames {
            format: "ams-review-ink-signatures/2".to_owned(),
            name: "ink-signatures.tsv.gz".to_owned(),
            no_store: "no store on disk".to_owned(),
            unreadable: "the store will not read".to_owned(),
        }
    }

    fn scratch(label: &str) -> PathBuf {
        let directory = std::env::temp_dir().join(format!(
            "ams-review-signatures-test-{label}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&directory);
        std::fs::create_dir_all(&directory).expect("scratch");
        directory
    }

    fn filled(byte: u8) -> [u8; 32] {
        [byte; 32]
    }

    #[test]
    fn a_window_s_signature_text_is_its_code_points_as_characters() {
        assert_eq!(
            signature_text("E650:0020:1F600").expect("a window"),
            "\u{E650} \u{1F600}"
        );
        assert!(signature_text("E650:D800").is_err());
        assert!(signature_text("E650:110000").is_err());
    }

    #[test]
    fn the_lines_merge_hits_misses_and_late_misses_in_key_order() {
        let header = PyValue::Dict(Vec::new());
        let mut signatures = SignatureArrays::with_prior(
            vec![2, 5, 9],
            vec![filled(2), filled(5), filled(9)],
            header,
        );
        assert_eq!(signatures.digest_of(5), Some(filled(5)));
        assert_eq!(signatures.prior_index(9), Some(2));
        signatures.mark_hit(2);
        signatures.set_misses(&[7, 1, 7], &[filled(70), filled(1), filled(71)]);
        assert_eq!(signatures.digest_of(7), Some(filled(71)));
        assert_eq!(signatures.digest_of(3), None);
        signatures.add_late(6, filled(60));
        signatures.add_late(6, filled(61));
        signatures.add_late(9, filled(90));
        assert_eq!(
            (signatures.hits(), signatures.misses(), signatures.unhit()),
            (2, 5, 1)
        );
        assert!(!signatures.unchanged());
        let mut lines = Vec::new();
        assert_eq!(signatures.write_lines(&mut lines).expect("lines"), 6);
        let keys: Vec<(u64, u8)> = lines
            .as_chunks::<LINE_BYTES>()
            .0
            .iter()
            .map(|line| {
                let (key, digest) = entry(line).expect("an entry line");
                (key, digest[0])
            })
            .collect();
        assert_eq!(keys, [(1, 1), (5, 5), (6, 61), (7, 71), (9, 9), (9, 90)]);
    }

    #[test]
    fn the_threaded_write_writes_what_the_inline_write_writes_joined_or_dropped() {
        let names = names();
        let mut signatures = SignatureArrays::default();
        signatures.set_misses(&[3, 1], &[filled(3), filled(1)]);
        let stamp_header = vec![(PyValue::str("environment"), PyValue::str("e"))];
        let stamps = vec![(PyValue::str(FAMILY_KEYS_KEY), PyValue::str("k"))];
        let inline = scratch("inline");
        write_signature_arrays(&inline, &names, &stamp_header, &signatures, &stamps)
            .expect("written");
        let expected = std::fs::read(names.path(&inline)).expect("the store");
        for joined in [true, false] {
            let directory = scratch(if joined { "joined" } else { "dropped" });
            let write = SignatureWrite::start(
                directory.clone(),
                names.clone(),
                stamp_header.clone(),
                signatures.clone(),
                stamps.clone(),
            )
            .expect("started");
            if joined {
                write.join().expect("written");
            } else {
                drop(write);
            }
            assert_eq!(
                std::fs::read(names.path(&directory)).expect("the store"),
                expected
            );
            assert!(!staging_path(&names.path(&directory)).exists());
            let _ = std::fs::remove_dir_all(&directory);
        }
        let missing = inline.join("missing");
        let write = SignatureWrite::start(missing, names.clone(), stamp_header, signatures, stamps)
            .expect("started");
        assert!(write.join().is_err());
        let _ = std::fs::remove_dir_all(&inline);
    }

    #[test]
    fn a_store_round_trips_and_reads_as_absent_where_python_drops_it() {
        let directory = scratch("round-trip");
        let names = names();
        let mut written = SignatureArrays::default();
        written.set_misses(&[u64::MAX, 0], &[filled(0xab), filled(0)]);
        let stamp_header = [(PyValue::str("environment"), PyValue::str("e"))];
        let stamps = [
            (
                PyValue::str(AUDIT_IDENTITY_KEY),
                PyValue::List(vec![PyValue::Int(1), PyValue::Int(2)]),
            ),
            (PyValue::str(FAMILY_KEYS_KEY), PyValue::str("f")),
        ];
        write_signature_arrays(&directory, &names, &stamp_header, &written, &stamps)
            .expect("written");
        assert!(!staging_path(&names.path(&directory)).exists());
        let plain =
            read_gzip(&std::fs::read(names.path(&directory)).expect("store")).expect("gzip");
        assert!(plain.starts_with(
            b"{\"format\": \"ams-review-ink-signatures/2\", \"environment\": \"e\", \"audit_identity\": [1, 2]"
        ));
        let loaded = SignatureArrays::load(&directory, &names, "e")
            .expect("read")
            .expect("loaded");
        assert_eq!(loaded.prior_keys(), [0, u64::MAX]);
        assert!(loaded.stamps_hold(&[1, 2], "f"));
        assert!(!loaded.stamps_hold(&[1, 3], "f"));
        assert!(!loaded.stamps_hold(&[1, 2], "g"));
        assert_eq!(SignatureArrays::load(&directory, &names, "other"), Ok(None));
        assert_eq!(
            signature_miss_note(
                &directory,
                &names,
                &Stamp {
                    value: "other".to_owned(),
                    lines: Vec::new(),
                    detail: Vec::new(),
                    header: Map::new(),
                }
            ),
            Ok(Some("the stamp moved".to_owned()))
        );
        std::fs::write(names.path(&directory), b"not gzip").expect("overwrite");
        assert_eq!(SignatureArrays::load(&directory, &names, "e"), Ok(None));
        let _ = std::fs::remove_dir_all(&directory);
        assert_eq!(
            signature_miss_note(
                &directory,
                &names,
                &Stamp {
                    value: String::new(),
                    lines: Vec::new(),
                    detail: Vec::new(),
                    header: Map::new(),
                }
            ),
            Ok(Some("no store on disk".to_owned()))
        );
    }

    #[test]
    fn indexed_work_comes_back_in_index_order_at_any_width() {
        for width in [0, 1, 3, 64] {
            let found = indexed(10, width, Vec::new, |seen: &mut Vec<usize>, at| {
                seen.push(at);
                at * at
            });
            assert_eq!(found, (0..10).map(|at| at * at).collect::<Vec<_>>());
        }
        assert!(indexed(0, 4, || (), |_, at| at).is_empty());
    }

    fn audit_row(config: &str, window: &str, new: &str) -> String {
        format!("{config}\t{window}\tcell\tentry\tbase\t{new}\n")
    }

    /// A single-file audit of two configurations' sections, and the sections, ranked in that order.
    fn two_sections(
        label: &str,
        default: &[String],
        ss03: &[String],
    ) -> (PathBuf, File, Vec<Section>) {
        let directory = scratch(label);
        let (default, ss03) = (default.concat(), ss03.concat());
        let path = directory.join("audit.tsv");
        std::fs::write(&path, format!("{default}{ss03}")).expect("audit");
        let section = |config: &str, rank: i64, start: usize, length: usize| Section {
            config: config.to_owned(),
            rank,
            start: start as i64,
            end: (start + length) as i64,
            part: 0,
            inflated: None,
        };
        let sections = vec![
            section("default", 0, 0, default.len()),
            section("ss03", 1, default.len(), ss03.len()),
        ];
        (directory, File::open(&path).expect("open"), sections)
    }

    #[test]
    fn the_key_only_read_keys_sibling_rows_against_the_prior_store_in_window_order() {
        let windows = [
            ("E650", "b"),
            ("E651", "a"),
            ("E652", "b"),
            ("E650:E651", "b"),
            ("E651:E652", "a"),
            ("E652:E653", "b"),
        ];
        let default: Vec<String> = windows
            .iter()
            .map(|(window, _new)| audit_row("default", window, "a"))
            .collect();
        let ss03: Vec<String> = windows
            .iter()
            .map(|(window, new)| audit_row("ss03", window, new))
            .collect();
        let (directory, file, sections) = two_sections("key-only", &default, &ss03);
        let files = [file];
        let family_of = [(0xE650, "qsPea".to_owned()), (0xE651, "qsBay".to_owned())];
        let family_keys = [
            ("qsPea".to_owned(), "p".to_owned()),
            ("qsBay".to_owned(), "b".to_owned()),
        ];
        let audit = HeldWindows {
            files: &files,
            sections: &sections,
            family_of: &family_of,
            family_keys: &family_keys,
        };
        let mut family = FamilyLines::new(&family_of, &family_keys);
        let fields = |config: &str, window: &str, new: &str| -> Vec<String> {
            audit_row(config, window, new)
                .trim_end()
                .split('\t')
                .map(str::to_owned)
                .collect()
        };
        let key = |family: &mut FamilyLines, config: &str, window: &str, new: &str| {
            let suffix = family.suffix(&parse_window(window).expect("a window"));
            signature_key(&fields(config, window, new), &suffix)
        };
        let held = key(&mut family, "ss03", "E650:E651", "b");
        let mut misses = Vec::new();
        for window in ["E650", "E652", "E650:E651", "E652:E653"] {
            misses.push((key(&mut family, "default", window, "a"), 0u8, window));
            if window != "E650:E651" {
                misses.push((key(&mut family, "ss03", window, "b"), 1u8, window));
            }
        }
        let mut prior = SignatureArrays::with_prior(
            vec![held - 1, held, held + 1],
            vec![filled(1), filled(2), filled(3)],
            PyValue::Dict(Vec::new()),
        );
        let mut answers = Vec::new();
        for (count, threads) in [(1, 1), (2, 1), (3, 2), (6, 6), (6, 1)] {
            let mut signatures = prior.clone();
            let found = key_only_read(audit, &mut signatures, count, threads).expect("a read");
            assert!(found.ran && found.broken.is_none());
            let got: Vec<(u64, u8, &str)> = found
                .keys
                .iter()
                .zip(&found.sections)
                .zip(&found.windows)
                .map(|((key, section), window)| (*key, *section, window.as_str()))
                .collect();
            assert_eq!(got, misses);
            assert_eq!((signatures.hits(), signatures.unhit()), (1, 2));
            answers.push(found.keys);
        }
        assert!(answers.windows(2).all(|pair| pair[0] == pair[1]));
        assert!(!prior.stamps_hold(&[], ""));
        prior.header = Some(PyValue::Dict(vec![
            (
                PyValue::str(AUDIT_IDENTITY_KEY),
                PyValue::List(vec![PyValue::Int(7)]),
            ),
            (PyValue::str(FAMILY_KEYS_KEY), PyValue::str("d")),
        ]));
        let skipped = find_misses(audit, &mut prior, &[7], "d", 6, 2).expect("a skip");
        assert!(!skipped.ran && skipped.keys.is_empty() && prior.hits() == 0);
        let _ = std::fs::remove_dir_all(directory);
    }

    #[test]
    fn a_break_ends_its_range_and_the_first_break_in_range_order_is_kept() {
        let default = vec![
            audit_row("default", "E650", "a"),
            audit_row("default", "E652", "a"),
            audit_row("default", "E651", "a"),
        ];
        let ss03 = vec![
            audit_row("ss03", "E650", "b"),
            audit_row("ss03", "E651", "b"),
            audit_row("ss03", "E652", "b"),
        ];
        let (directory, file, sections) = two_sections("key-only-break", &default, &ss03);
        let files = [file];
        let audit = HeldWindows {
            files: &files,
            sections: &sections,
            family_of: &[],
            family_keys: &[],
        };
        let mut signatures = SignatureArrays::default();
        let found = key_only_read(audit, &mut signatures, 1, 1).expect("a read");
        assert!(
            found
                .broken
                .as_deref()
                .is_some_and(|why| why.contains("E651"))
        );
        assert_eq!(found.windows, ["E650", "E650"]);
        let _ = std::fs::remove_dir_all(directory);
    }

    #[test]
    fn the_late_shaper_shapes_only_what_neither_the_prior_store_nor_the_misses_hold() {
        let mut signatures = SignatureArrays::with_prior(
            vec![10, 20],
            vec![filled(10), filled(20)],
            PyValue::Dict(Vec::new()),
        );
        signatures.set_misses(&[15], &[filled(15)]);
        let mut asked = Vec::new();
        let mut late = LateShaper::new(&mut signatures, |window: &str, config: &str| {
            asked.push(format!("{window} {config}"));
            Ok(filled(99))
        });
        assert_eq!(
            late.digest(20, "E650", "default").expect("a hit"),
            filled(20)
        );
        assert_eq!(late.digest(15, "E650", "ss03").expect("a miss"), filled(15));
        assert_eq!(late.digest(30, "E651", "ss05").expect("a late"), filled(99));
        assert_eq!(late.shaped, 1);
        assert_eq!(
            (
                late.signatures.hits(),
                late.signatures.misses(),
                late.signatures.unhit()
            ),
            (1, 2, 1)
        );
        assert_eq!(asked, ["E651 ss05"]);
    }

    #[test]
    fn a_moved_note_names_the_changed_new_and_gone_labels() {
        let labels = |pairs: &[(&str, &str)]| -> HashMap<String, String> {
            pairs
                .iter()
                .map(|(label, digest)| ((*label).to_owned(), (*digest).to_owned()))
                .collect()
        };
        let recorded = labels(&[("a", "1"), ("b", "1"), ("c", "1"), ("gone", "1")]);
        let current = labels(&[("a", "2"), ("b", "1"), ("c", "2"), ("new", "1")]);
        assert_eq!(
            moved_note(&recorded, &current, 8, Some(("c", Some("x.py (changed)")))),
            Some("a (changed), c: x.py (changed), new (new), gone (gone)".to_owned())
        );
        assert_eq!(
            moved_note(&recorded, &current, 2, None),
            Some("a (changed), c (changed) and 2 more".to_owned())
        );
        assert_eq!(moved_note(&recorded, &recorded, 8, None), None);
    }
}
