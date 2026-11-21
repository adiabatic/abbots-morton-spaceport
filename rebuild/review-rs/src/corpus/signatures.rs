//! The ink-signature store as the Python build holds, reads and writes it (`unit_cache.SignatureArrays`, `write_signature_arrays`, `signature_miss_note`; `rebuild/CORPUS-PARENT-PORT-PLAN.md` § Containers and § Reading what Python wrote): the prior store's keys in one sorted column beside their 32-byte digests and a hit bit an entry, the misses shaped before the stream and the late ones it shapes itself in two more, the new store's lines merged from them in key order, and the store written as one gzip stream at level 1 through its staging file. A store reads as absent on any read error but `zlib.error`, which Python's readers do not catch, so it fails the load ([`StoreError`]), as does JSON Python reads into a value the reader does not hold.
//!
//! A window's signature text ([`signature_text`], `build.signature_text`) is what the misses' shaping hands [`InkComparator::signature`].
//!
//! [`stage_signature_store`] is the `signature-store` stage of the stage fixtures, and [`stage_signature`] the `signature` stage.

use std::collections::{BTreeSet, HashMap};
use std::fs::File;
use std::io::{self, BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};

use ams_m1_kernel::emit::json_string;
use serde_json::{Map, Value};
use sha2::{Digest as _, Sha256};

use crate::corpus::audit::{integer, items, stage_line, text};
use crate::corpus::gzip::{GzipError, GzipReader, GzipWriter, MTIME, PART_LEVEL};
use crate::corpus::input::{BuildInput, Stamp};
use crate::corpus::sink::staging_path;
use crate::corpus::stream::parse_window;
use crate::fontfacts::{FontFacts, Side};
use crate::fragment::sha256_hex;
use crate::ink::{self, InkComparator};
use crate::pyfmt::repr::repr;
use crate::pyfmt::{PyValue, json};
use crate::shape::{Fonts, Shaper};
use crate::wire::{Init, Result, WireError, error};

/// The header key of the audit's identity the store was written over (`unit_cache.AUDIT_IDENTITY_KEY`).
pub const AUDIT_IDENTITY_KEY: &str = "audit_identity";
/// The header key of the family keys' digest the store was written over (`unit_cache.FAMILY_KEYS_KEY`).
pub const FAMILY_KEYS_KEY: &str = "family_keys_digest";
/// The stamp's label whose per-file lines a miss note expands (`signature_miss_note`'s code label).
pub const CODE_LABEL: &str = "comparator_code";
/// The changed files a miss note names inside the code label (`unit_cache.CODE_FILES_SHOWN`).
pub const CODE_FILES_SHOWN: usize = 4;
/// The changed labels a miss note names (`fingerprint.moved_note`'s default limit).
pub const LABELS_SHOWN: usize = 8;
/// The `signature-store` stage's directory, beside its stage file, of the files its lines name by sha256.
pub const STAGE_BLOBS: &str = "signature-store.blobs";

/// One entry line: 16 hex digits, a tab, 64 hex digits and a newline (`unit_cache._SIGNATURE_LINE_BYTES`).
const LINE_BYTES: usize = 82;
/// The entry lines read and checked at once (`unit_cache._SIGNATURE_READ_LINES`).
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

/// One entry line's key and digest, or None for a line other than 16 lowercase hex digits, a tab, 64 lowercase hex digits and a newline (`unit_cache._SIGNATURE_LINE`).
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

fn get<'a>(entries: &'a [(PyValue, PyValue)], name: &str) -> Option<&'a PyValue> {
    entries.iter().find_map(|(key, value)| match key {
        PyValue::Str(key) if key == name => Some(value),
        _ => None,
    })
}

fn is_str(value: Option<&PyValue>, text: &str) -> bool {
    matches!(value, Some(PyValue::Str(held)) if held == text)
}

/// Whether a value Python's `==` holds equal to the int `value`: an equal int, or the bool of that int.
fn int_equal(held: &PyValue, value: i64) -> bool {
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

/// The positions of `words` in ascending word order, equal words in position order (`columns.sorted_by_word`).
fn sorted_by_word(words: &[u64]) -> Vec<usize> {
    let mut order: Vec<usize> = (0..words.len()).collect();
    order.sort_by_key(|&at| words[at]);
    order
}

/// Of each run of equal words in `order`, the last position: the entry a later assignment to one dict key keeps.
fn last_of_each(words: &[u64], order: &[usize]) -> Vec<usize> {
    order
        .iter()
        .enumerate()
        .filter(|&(position, &at)| {
            position + 1 == order.len() || words[order[position + 1]] != words[at]
        })
        .map(|(_, &at)| at)
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

/// The ink-signature store in memory, as columns (`unit_cache.SignatureArrays`): the prior store's keys, strictly ascending, beside their digests, a bit a prior entry marking each the build hit, and the prior store's header, None when there was no usable store; the misses, sorted by key once they are shaped ([`SignatureArrays::set_misses`]); and the late misses the stream shaped itself ([`SignatureArrays::add_late`]).
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
    /// A store with no prior entries and no header, `SignatureArrays()`.
    pub fn new() -> Self {
        Self::default()
    }

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

    /// Read the prior store under `out_dir` into columns, as `SignatureArrays.load` reads it: None when it is not a file, does not read, holds a header that is not a dict or names another format or stamp than `environment`, holds a line other than an entry line, checked [`READ_LINES`] lines at a time before more are read, or holds keys that do not strictly ascend.
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

    /// The prior entries.
    pub fn len(&self) -> usize {
        self.keys.len()
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

    /// Write the new store's entry lines in key order (`SignatureArrays.lines`): the hit prior entries merged with the misses, the late ones among them, a miss ahead of a prior entry only when its key is smaller. Returns the lines written.
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

/// Write the store under `out_dir` from `signatures` as `unit_cache.write_signature_arrays` writes it: a header line in `json.dumps`' default form of the format, then `stamp_header` (the stamp's fields, `unit_cache._stamp_header`) and `stamps` merged over it as a dict display merges them, then the entry lines ([`SignatureArrays::write_lines`]), as one gzip stream at level 1 and mtime 0 named for the store, through its staging file, renamed into place; the staging file is removed whether or not the write succeeds.
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

/// The store's header as `unit_cache.read_header` reads it: its first line parsed, None when it does not read or is not a dict.
fn read_header(path: &Path) -> Read<Option<Vec<(PyValue, PyValue)>>> {
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
fn labels_of<S: AsRef<str>>(lines: &[S]) -> HashMap<String, String> {
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

/// A header's recorded lines as labels (`unit_cache._recorded_labels`): None unless a list, each item taken as `str` takes it.
fn recorded_labels(lines: Option<&PyValue>) -> Option<HashMap<String, String>> {
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

/// Why [`SignatureArrays::load`] rejects the store under `out_dir`, as `unit_cache.signature_miss_note` says it, or None when its header matches `environment`: no store, a header that does not read, or a stamp that moved, named through its lines with the code label's per-file lines expanded.
pub fn signature_miss_note(
    out_dir: &Path,
    names: &StoreNames,
    environment: &Stamp,
) -> Read<Option<String>> {
    let path = names.path(out_dir);
    if !path.is_file() {
        return Ok(Some(names.no_store.clone()));
    }
    let Some(entries) = read_header(&path)? else {
        return Ok(Some(names.unreadable.clone()));
    };
    if is_str(get(&entries, "environment"), &environment.value) {
        return Ok(None);
    }
    let Some(recorded) = recorded_labels(get(&entries, "environment_lines")) else {
        return Ok(Some("the stamp moved".to_owned()));
    };
    let recorded_code = match get(&entries, "environment_detail") {
        Some(PyValue::Dict(detail)) => recorded_labels(get(detail, CODE_LABEL)),
        _ => None,
    };
    let current_code = environment
        .detail
        .iter()
        .find(|(label, _)| label == CODE_LABEL)
        .map(|(_, lines)| labels_of(lines))
        .unwrap_or_default();
    let code_note = recorded_code
        .map(|recorded_code| moved_note(&recorded_code, &current_code, CODE_FILES_SHOWN, None));
    let expand = code_note.as_ref().map(|note| (CODE_LABEL, note.as_deref()));
    Ok(Some(
        match moved_note(
            &recorded,
            &labels_of(&environment.lines),
            LABELS_SHOWN,
            expand,
        ) {
            Some(moved) => format!("the stamp moved at {moved}"),
            None => "the stamp moved".to_owned(),
        },
    ))
}

fn fail(what: &str, failure: impl std::fmt::Display) -> WireError {
    WireError(format!("cannot {what}: {failure}"))
}

fn tagged(tag: &str, items: impl IntoIterator<Item = PyValue>) -> PyValue {
    PyValue::Tuple(std::iter::once(PyValue::str(tag)).chain(items).collect())
}

fn arguments<'a, const COUNT: usize>(
    value: &'a PyValue,
    key: &str,
) -> Result<&'a [PyValue; COUNT]> {
    items(value, key)?.try_into().map_err(|_| {
        WireError(format!(
            "{key}: not {COUNT} arguments: {}",
            value.to_typed()
        ))
    })
}

fn word(value: &PyValue, key: &str) -> Result<u64> {
    let hex = text(value, key)?;
    u64::from_str_radix(hex, 16).map_err(|_| WireError(format!("{key}: {hex:?} is not a key")))
}

fn digest(value: &PyValue, key: &str) -> Result<[u8; 32]> {
    let hex = text(value, key)?;
    let mut digest = [0u8; 32];
    if hex.len() != 64 {
        return error(format!("{key}: {hex:?} is not a digest"));
    }
    for (byte, pair) in digest.iter_mut().zip(hex.as_bytes().as_chunks::<2>().0) {
        *byte = match (hex_value(pair[0]), hex_value(pair[1])) {
            (Some(high), Some(low)) => (high << 4) | low,
            _ => return error(format!("{key}: {hex:?} is not a digest")),
        };
    }
    Ok(digest)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn raised(failure: &StoreError) -> PyValue {
    tagged(
        "raised",
        [
            PyValue::str(failure.class()),
            PyValue::str(failure.message()),
        ],
    )
}

/// The stage's blobs and the scratch directory its lines' files are set up in.
struct Workspace {
    blobs: PathBuf,
    read: HashMap<String, Vec<u8>>,
    scratch: PathBuf,
    cases: usize,
    names: StoreNames,
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

    /// Put what a line's `held` names at `path`: nothing for `("missing",)`, a directory for `("directory",)`, or the blob of `("file", sha256)`.
    fn place(&mut self, held: &PyValue, path: &Path, key: &str) -> Result<()> {
        match items(held, key)? {
            [PyValue::Str(tag)] if tag == "missing" => Ok(()),
            [PyValue::Str(tag)] if tag == "directory" => std::fs::create_dir(path)
                .map_err(|failure| fail(&format!("make {}", path.display()), failure)),
            [PyValue::Str(tag), PyValue::Str(digest)] if tag == "file" => {
                let data = self.blob(digest)?.to_vec();
                std::fs::write(path, data)
                    .map_err(|failure| fail(&format!("write {}", path.display()), failure))
            }
            _ => error(format!("{key}: not a held file: {}", held.to_typed())),
        }
    }

    /// A directory holding the store a line's `held` names.
    fn store(&mut self, held: &PyValue, key: &str) -> Result<PathBuf> {
        let directory = self.directory()?;
        let path = self.names.path(&directory);
        self.place(held, &path, key)?;
        Ok(directory)
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

/// A loaded store's columns as a line's value names them: the count, the sha256 of the keys as 8-byte big-endian words, the sha256 of the digests, and the header.
fn columns_value(signatures: &SignatureArrays) -> PyValue {
    let mut keys = Sha256::new();
    for key in &signatures.keys {
        keys.update(key.to_be_bytes());
    }
    let mut digests = Sha256::new();
    for digest in &signatures.digests {
        digests.update(digest);
    }
    tagged(
        "columns",
        [
            PyValue::Int(signatures.keys.len() as i64),
            PyValue::str(hex(&keys.finalize())),
            PyValue::str(hex(&digests.finalize())),
            signatures.header.clone().unwrap_or(PyValue::None),
        ],
    )
}

/// The store a trace starts from: `("empty",)`, `SignatureArrays()`, or `("load", held, environment)`, the store `held` names loaded under the stamp `environment`, which must load.
fn origin(workspace: &mut Workspace, value: &PyValue, key: &str) -> Result<SignatureArrays> {
    match items(value, key)? {
        [PyValue::Str(tag)] if tag == "empty" => Ok(SignatureArrays::new()),
        [PyValue::Str(tag), held, environment] if tag == "load" => {
            let directory = workspace.store(held, key)?;
            match SignatureArrays::load(&directory, &workspace.names, text(environment, key)?) {
                Ok(Some(signatures)) => Ok(signatures),
                Ok(None) => error(format!("{key}: the trace's store does not load")),
                Err(failure) => error(format!("{key}: the trace's store raised {failure}")),
            }
        }
        _ => error(format!("{key}: no origin {}", value.to_typed())),
    }
}

fn count(value: usize) -> PyValue {
    PyValue::Int(value as i64)
}

/// Make one call a trace records on `signatures` and give its result as the line records it: keys and digests as hex, a position of -1 for none.
fn replay(signatures: &mut SignatureArrays, op: &PyValue, key: &str) -> Result<PyValue> {
    let call = items(op, key)?;
    let Some((name, arguments)) = call.split_first() else {
        return error(format!("{key}: an empty call"));
    };
    let argument = |at: usize| {
        arguments
            .get(at)
            .ok_or_else(|| WireError(format!("{key}: {} lacks an argument", op.to_typed())))
    };
    Ok(match text(name, key)? {
        "stamps_hold" => {
            let identity = items(argument(0)?, key)?
                .iter()
                .map(|value| integer(value, key))
                .collect::<Result<Vec<_>>>()?;
            PyValue::Bool(signatures.stamps_hold(&identity, text(argument(1)?, key)?))
        }
        "prior_index" => PyValue::Int(
            signatures
                .prior_index(word(argument(0)?, key)?)
                .map_or(-1, |position| position as i64),
        ),
        "mark_hit" => {
            let position = integer(argument(0)?, key)?;
            match usize::try_from(position) {
                Ok(position) if position < signatures.len() => signatures.mark_hit(position),
                _ => return error(format!("{key}: no prior entry {position}")),
            }
            PyValue::None
        }
        "digest_of" => match signatures.digest_of(word(argument(0)?, key)?) {
            Some(found) => PyValue::str(hex(&found)),
            None => PyValue::None,
        },
        "set_misses" => {
            let keys = items(argument(0)?, key)?
                .iter()
                .map(|value| word(value, key))
                .collect::<Result<Vec<_>>>()?;
            let digests = items(argument(1)?, key)?
                .iter()
                .map(|value| digest(value, key))
                .collect::<Result<Vec<_>>>()?;
            if digests.len() < keys.len() {
                return error(format!("{key}: fewer digests than misses"));
            }
            signatures.set_misses(&keys, &digests);
            PyValue::None
        }
        "add_late" => {
            signatures.add_late(word(argument(0)?, key)?, digest(argument(1)?, key)?);
            PyValue::None
        }
        "hits" => count(signatures.hits()),
        "misses" => count(signatures.misses()),
        "unhit" => count(signatures.unhit()),
        "unchanged" => PyValue::Bool(signatures.unchanged()),
        other => return error(format!("{key}: no SignatureArrays call {other:?}")),
    })
}

/// A store's new lines as a trace's value ends with them: `("lines", count, sha256)` over the lines joined.
fn lines_value(signatures: &SignatureArrays) -> Result<PyValue> {
    let mut joined = Vec::new();
    let written = signatures
        .write_lines(&mut joined)
        .map_err(|failure| fail("write the lines", failure))?;
    Ok(tagged(
        "lines",
        [count(written), PyValue::str(sha256_hex(&joined))],
    ))
}

/// Start from a trace's origin and replay its calls, answering each call's result.
fn traced(
    workspace: &mut Workspace,
    start: &PyValue,
    ops: &PyValue,
    key: &str,
) -> Result<(SignatureArrays, Vec<PyValue>)> {
    let mut signatures = origin(workspace, start, key)?;
    let results = items(ops, key)?
        .iter()
        .map(|op| replay(&mut signatures, op, key))
        .collect::<Result<Vec<_>>>()?;
    Ok((signatures, results))
}

fn dict_entries<'a>(value: &'a PyValue, key: &str) -> Result<&'a [(PyValue, PyValue)]> {
    match value {
        PyValue::Dict(entries) => Ok(entries),
        PyValue::None => Ok(&[]),
        other => error(format!("{key}: expected a dict, not {}", other.kind())),
    }
}

/// A stamp a line names as `(value, lines, detail)`, with no header fields, which a miss note does not read.
fn stamp_of(value: &PyValue, key: &str) -> Result<Stamp> {
    let [value, lines, detail] = arguments(value, key)?;
    let strings = |value: &PyValue| -> Result<Vec<String>> {
        items(value, key)?
            .iter()
            .map(|line| text(line, key).map(str::to_owned))
            .collect()
    };
    Ok(Stamp {
        value: text(value, key)?.to_owned(),
        lines: strings(lines)?,
        detail: items(detail, key)?
            .iter()
            .map(|pair| {
                let [label, lines] = arguments(pair, key)?;
                Ok((text(label, key)?.to_owned(), strings(lines)?))
            })
            .collect::<Result<_>>()?,
        header: Map::new(),
    })
}

/// One `signature-store` line's answer, by its form.
fn answer(record: &Value, key: &str, workspace: &mut Workspace) -> Result<PyValue> {
    let input = PyValue::from_typed(&record["input"])
        .map_err(|failure| WireError(format!("{key}: {failure}")))?;
    match record["form"].as_str() {
        Some("load") => {
            let [held, environment] = arguments(&input, key)?;
            let directory = workspace.store(held, key)?;
            Ok(
                match SignatureArrays::load(&directory, &workspace.names, text(environment, key)?) {
                    Ok(Some(signatures)) => columns_value(&signatures),
                    Ok(None) => tagged("none", []),
                    Err(failure) => raised(&failure),
                },
            )
        }
        Some("miss-note") => {
            let [held, environment] = arguments(&input, key)?;
            let directory = workspace.store(held, key)?;
            let environment = stamp_of(environment, key)?;
            Ok(
                match signature_miss_note(&directory, &workspace.names, &environment) {
                    Ok(note) => tagged("note", [PyValue::optional_str(note.as_deref())]),
                    Err(failure) => raised(&failure),
                },
            )
        }
        Some("trace") => {
            let [start, ops] = arguments(&input, key)?;
            let (signatures, results) = traced(workspace, start, ops, key)?;
            Ok(PyValue::Tuple(vec![
                PyValue::List(results),
                lines_value(&signatures)?,
            ]))
        }
        Some("write") => {
            let [start, ops, stamp_header, stamps, held, staging_held] = arguments(&input, key)?;
            let (signatures, _results) = traced(workspace, start, ops, key)?;
            let directory = workspace.directory()?;
            let target = workspace.names.path(&directory);
            let staging = staging_path(&target);
            workspace.place(held, &target, key)?;
            workspace.place(staging_held, &staging, key)?;
            let names = workspace.names.clone();
            let written = write_signature_arrays(
                &directory,
                &names,
                dict_entries(stamp_header, key)?,
                &signatures,
                dict_entries(stamps, key)?,
            );
            let after = [held_now(&target)?, held_now(&staging)?];
            Ok(match written {
                Ok(()) => tagged("written", after),
                Err(failure) => {
                    return error(format!("{key}: the write failed: {failure}"));
                }
            })
        }
        other => error(format!("{key}: no signature-store form {other:?}")),
    }
}

/// The `signature-store` stage: for each line of `stage_file`, set up the store its input names, from the blobs it names by sha256 in [`STAGE_BLOBS`] beside the stage file, in a scratch directory under the names the build input's vocabulary gives ([`StoreNames`]), make the line's call again by its `form`, and write one `{"key", "value"}` line in the typed encoding: `load` loads the store ([`SignatureArrays::load`]) to `("columns", count, keys sha256, digests sha256, header)`, `("none",)` or `("raised", class, message)`; `miss-note` answers [`signature_miss_note`] as `("note", note)`; `trace` starts from its origin, empty or a loaded store, replays its calls ([`replay`]) and answers their results with the new store's lines' count and sha256; and `write` replays its trace, puts what its target and staging file held in place, writes the store ([`write_signature_arrays`]) and answers `("written", target, staging)` with what each then holds.
pub fn stage_signature_store(
    input_path: &Path,
    stage_file: &Path,
    out: &mut impl Write,
) -> Result<()> {
    let names = StoreNames::of(&BuildInput::read(input_path)?)?;
    let scratch =
        std::env::temp_dir().join(format!("ams-review-signature-store-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&scratch);
    std::fs::create_dir_all(&scratch)
        .map_err(|failure| fail("make the stage's scratch", failure))?;
    let mut workspace = Workspace {
        blobs: stage_file
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(STAGE_BLOBS),
        read: HashMap::new(),
        scratch: scratch.clone(),
        cases: 0,
        names,
    };
    let answered = (|| {
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
            let value = answer(&record, key, &mut workspace)?;
            stage_line(out, key, &value)?;
        }
        Ok(())
    })();
    let _ = std::fs::remove_dir_all(&scratch);
    answered
}

/// `build.signature_text`: the text a window's ink signature is taken over, its code points as characters. A code point that is no Unicode scalar value is refused, since no text the shaper reads holds one.
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

/// The `signature` stage: for each line of `stage_file`, the signature of its `input`, a `pair`'s text or a `late` or `window` line's window as [`signature_text`] gives its text, under its configuration, shaped over the before and after fonts of the build input's units init through one [`Shaper`] ([`InkComparator::signature`]), written as one `{"key", "signature", "value"}` line in the typed encoding with the signature as Python holds it and its digest ([`Signature::digest`](crate::ink::Signature::digest)) as hex.
pub fn stage_signature(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let init = Init::read(Path::new(&input.units_init))?;
    let facts = FontFacts::read(&init.font_facts)?;
    let fonts = Fonts::open(&init, &facts).map_err(|failure| WireError(failure.0))?;
    let pixel_size = ink::pixel_size(&init).map_err(|failure| WireError(failure.0))?;
    let comparator = InkComparator::new(&facts, pixel_size, Side::Before, Side::After);
    let mut shaper = Shaper::new(&fonts, &facts);
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
        let typed = |field: &str| {
            PyValue::from_typed(&record[field])
                .map_err(|failure| WireError(format!("{key}: the {field}: {failure}")))
        };
        let inputs = typed("input")?;
        let [subject, config] = items(&inputs, "the input")? else {
            return error(format!(
                "{key}: the input is not a subject and a configuration"
            ));
        };
        let (subject, config) = (
            text(subject, "the subject")?,
            text(config, "the configuration")?,
        );
        let shaped = match typed("form")? {
            PyValue::Str(form) if form == "pair" => subject.to_owned(),
            PyValue::Str(form) if form == "late" || form == "window" => signature_text(subject)?,
            other => return error(format!("{key}: no signature form {other:?}")),
        };
        let signature = comparator
            .signature(&mut shaper, &shaped, config)
            .map_err(|failure| WireError(format!("{key}: {failure}")))?;
        let digest: String = signature
            .digest(&facts)
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        writeln!(
            out,
            "{{\"key\":{},\"signature\":{},\"value\":{}}}",
            json_string(key),
            signature.to_py(&facts).to_typed(),
            PyValue::str(&digest).to_typed()
        )
        .map_err(|failure| WireError(format!("cannot write a stage line: {failure}")))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::corpus::gzip::read_gzip;

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
    fn a_store_round_trips_and_reads_as_absent_where_python_drops_it() {
        let directory = scratch("round-trip");
        let names = names();
        let mut written = SignatureArrays::new();
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
