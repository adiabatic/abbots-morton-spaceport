//! The plan's reads of the prior corpus, as `rebuild/review/unit_cache.py` makes them: the unit store opened ([`open_store`], `unit_cache.open_store`), its materialized lines read in order as `unit_index.store_lines` merges its base and segments ([`OpenStore::lines`]), each matched to a unit of this build by its input key and, where the plan may reuse it, parsed ([`scan_store`]), and the lines the walk placed parsed again alone ([`scan_marked`]); the parts a record's address is trusted in ([`trusted_parts`]) and the manifest identity the store is stamped with ([`manifest_stamp`], [`manifest_identity`]); the records the scan found without an address ([`WantedFragments`]) placed by a walk over the prior corpus's live fragments ([`locate_prior_fragments`], [`LocatedFragments`]), read through the live-fragment iterator as `unit_index.LiveCorpus` reads them, from the store's addresses, the index's ids or the parts themselves; the single file a store written before the segment format leaves put under a head ([`adopt_single_file`]); and why a store does not load ([`store_miss_note`]) and whether its stamps say every record is re-derived ([`rederiving_moved`]).
//!
//! What a read raises is taken as the plan takes it ([`ScanError`]): what `unit_cache.StoreUnreadable` stands for, which the plan answers by restarting over an emptied store; another exception, which fails the Python build; or a value Python reads that the reader does not hold, which fails the Rust build. The walk keeps what it placed before an `OSError` or a `ValueError` stops it, as `locate_prior_fragments` does, and fails on anything else.
//!
//! [`stage_scan`] is the `scan` stage of the stage fixtures.

use std::collections::{HashMap, HashSet};
use std::fmt;
use std::fs::File;
use std::io::{self, BufRead, BufReader, Write};
use std::os::unix::fs::FileExt;
use std::path::{Path, PathBuf};

use serde_json::Value;

use super::audit::{integer, items, stage_line, text};
use super::columns::{KeyIndex, sorted_by_word};
use super::gzip::{GzipError, GzipReader, inflate_fragment};
use super::input::{BuildInput, Stamp};
use super::prior::holds_unit;
use super::records::{
    ParsedCachedUnit, RecordError, hashable, id_word, line_address, line_class, line_id,
    named_ordinal, parse_line, truthy,
};
use super::rowdelta::{
    Head, Key, MERGE_PLACE, Part, PartReader, Place, ReadError, base_name, base_rows, commit,
    head_path, keyed_segment_rows, merge_keyed, merge_placed, open_parts, py_str, read_head,
    remove_unlisted, segment_rows,
};
use super::shards::overflow_index;
use super::signatures::{
    StoreError, dump_prior, get, int_equal, is_str, labels_of, miss_note, read_header,
    recorded_labels,
};
use super::sink::Sink;
use super::store::{KEY_BYTES, PriorFragment};
use super::table::{ColumnDigest, UNIT_PREFIX, count_value};
use crate::fragment::{lookup, py_eq, sha256_hex};
use crate::pyfmt::{PyValue, json};
use crate::wire::{Result, WireError, error};

/// What `io.BufferedReader` over a `gzip.GzipFile` reads its plain bytes in (`gzip.READ_BUFFER_SIZE`).
const READ_BUFFER_BYTES: usize = 128 * 1024;
/// The stamp label whose per-file lines the unit store's miss note expands (`store_miss_note`'s code label).
pub const CODE_LABEL: &str = "corpus_code";
/// The width a live slot's id is padded to, and the longest id a class's slots hold (`unit_index.ClassSlots.ID_WIDTH`).
const ID_WIDTH: usize = 13;
/// Where an index line's id starts (`unit_index._ID_AT`).
const ID_OPEN: &[u8] = b"{\"id\": \"";
const CLASS_SEAM: &[u8] = b", \"class\": ";
const CLUSTER_SEAM: &[u8] = b", \"cluster\": ";
/// The `scan` stage's directory, beside its stage file, of the files its generated lines name by sha256.
pub const STAGE_BLOBS: &str = "scan.blobs";

/// The names the scan reads from the build input's vocabulary: the unit store's and the units index's files and formats, the store's notes, the stamp labels that say every record is re-derived, and the manifest's fingerprint components its identity leaves out.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ScanNames {
    pub store_name: String,
    pub store_format: String,
    pub index_name: String,
    pub index_format: String,
    pub no_store: String,
    pub unreadable: String,
    pub manifest_moved: String,
    pub rederiving: Vec<String>,
    pub asset_components: Vec<String>,
}

impl ScanNames {
    /// The names as the build input's vocabulary gives them.
    pub fn of(input: &BuildInput) -> Result<Self> {
        let constant = |dotted: &str| {
            input
                .constant(dotted)
                .ok_or_else(|| WireError(format!("the build input's vocabulary holds no {dotted}")))
        };
        let word = |dotted: &str| match constant(dotted)? {
            PyValue::Str(value) => Ok(value.clone()),
            other => error(format!("{dotted} is {}, not a str", other.kind())),
        };
        let words = |dotted: &str| match constant(dotted)? {
            PyValue::Tuple(values) | PyValue::List(values) => values
                .iter()
                .map(|value| match value {
                    PyValue::Str(value) => Ok(value.clone()),
                    other => error(format!("{dotted} holds {}, not a str", other.kind())),
                })
                .collect::<Result<Vec<_>>>(),
            other => error(format!("{dotted} is {}, not a tuple", other.kind())),
        };
        Ok(Self {
            store_name: word("rebuild.review.unit_cache.STORE_NAME")?,
            store_format: word("rebuild.review.unit_cache.STORE_FORMAT")?,
            index_name: word("rebuild.review.unit_index.INDEX_NAME")?,
            index_format: word("rebuild.review.unit_index.INDEX_FORMAT")?,
            no_store: word("rebuild.review.unit_cache.NO_STORE_NOTE")?,
            unreadable: word("rebuild.review.unit_cache.UNREADABLE_NOTE")?,
            manifest_moved: word("rebuild.review.unit_cache.MANIFEST_MOVED_NOTE")?,
            rederiving: words("rebuild.review.unit_cache.REDERIVING_LABELS")?,
            asset_components: words("rebuild.review.unit_index.ASSET_COMPONENTS")?,
        })
    }

    /// The single file a store written before the segment format leaves (`unit_cache.store_path`).
    pub fn store_path(&self, out_dir: &Path) -> PathBuf {
        out_dir.join(&self.store_name)
    }

    fn index_path(&self, corpus: &Path) -> PathBuf {
        corpus.join(&self.index_name)
    }
}

/// What a read of the prior store raises, by how the plan takes it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ScanError {
    /// `unit_cache.StoreUnreadable`, with why: the plan drops what it loaded and restarts over an emptied store.
    Unreadable(String),
    /// Another exception, by its class and text, which no caller in the plan catches and which fails the Python build.
    Failed(String),
    /// A value Python reads that the reader does not hold, which fails the Rust build.
    Unheld(String),
}

impl ScanError {
    /// How the plan takes it: `unreadable`, `failed` or `unheld`.
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Unreadable(_) => "unreadable",
            Self::Failed(_) => "failed",
            Self::Unheld(_) => "unheld",
        }
    }
}

impl fmt::Display for ScanError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unreadable(why) | Self::Failed(why) | Self::Unheld(why) => {
                write!(formatter, "{}: {why}", self.kind())
            }
        }
    }
}

impl std::error::Error for ScanError {}

/// A read failure as a store's reader raises it: what `unit_cache._STORE_ERRORS` holds makes the store unreadable, and `zlib.error` or a generator's `RuntimeError` fails.
fn store_failure(failure: ReadError) -> ScanError {
    match failure {
        ReadError::Unheld(why) => ScanError::Unheld(why),
        ReadError::Gzip(GzipError::Zlib(_)) | ReadError::Raised(..) => {
            ScanError::Failed(failure.to_string())
        }
        other => ScanError::Unreadable(other.to_string()),
    }
}

/// A read failure no caller catches.
fn uncaught(failure: ReadError) -> ScanError {
    match failure {
        ReadError::Unheld(why) => ScanError::Unheld(why),
        other => ScanError::Failed(other.to_string()),
    }
}

fn record_failure(failure: RecordError) -> ScanError {
    match failure {
        RecordError::Unreadable(why) => ScanError::Unreadable(why),
        RecordError::Failed(why) => ScanError::Failed(why),
        RecordError::Unheld(why) => ScanError::Unheld(why),
    }
}

fn header_failure(failure: StoreError) -> ScanError {
    match failure {
        StoreError::Zlib(why) => ScanError::Failed(format!("error: {why}")),
        StoreError::Unheld(why) => ScanError::Unheld(why),
    }
}

/// A gzipped part read from its start as `gzip.GzipFile` iterates its lines.
fn gzip_lines(file: &File) -> BufReader<GzipReader<PartReader<'_>>> {
    BufReader::with_capacity(READ_BUFFER_BYTES, GzipReader::new(PartReader::new(file)))
}

fn next_line(reader: &mut impl BufRead) -> std::result::Result<Option<Vec<u8>>, ReadError> {
    let mut line = Vec::new();
    match reader.read_until(b'\n', &mut line) {
        Ok(0) => Ok(None),
        Ok(_) => Ok(Some(line)),
        Err(failure) => Err(ReadError::of_io(&failure)),
    }
}

/// `unit_index.store_base_lines`: a store base's record lines past its header line. A part with no header line raises `RuntimeError`, as `next` on its empty stream does inside the generator.
struct StoreBaseLines<'a> {
    reader: BufReader<GzipReader<PartReader<'a>>>,
    started: bool,
    done: bool,
}

impl<'a> StoreBaseLines<'a> {
    fn new(file: &'a File) -> Self {
        Self {
            reader: gzip_lines(file),
            started: false,
            done: false,
        }
    }
}

impl Iterator for StoreBaseLines<'_> {
    type Item = std::result::Result<Vec<u8>, ReadError>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        if !self.started {
            self.started = true;
            match next_line(&mut self.reader) {
                Ok(Some(_)) => {}
                Ok(None) => {
                    self.done = true;
                    return Some(Err(ReadError::Raised(
                        "RuntimeError",
                        "generator raised StopIteration".to_owned(),
                    )));
                }
                Err(failure) => {
                    self.done = true;
                    return Some(Err(failure));
                }
            }
        }
        let line = next_line(&mut self.reader).transpose();
        if !matches!(line, Some(Ok(_))) {
            self.done = true;
        }
        line
    }
}

pub(crate) type Lines<'a, T> = Box<dyn Iterator<Item = std::result::Result<T, ReadError>> + 'a>;

/// A unit store's materialized record lines in order, each with its place (`unit_index.store_lines`): the base's alone for a store with no head or no segment, else the base and the segments merged by place.
fn store_lines<'a>(
    head: Option<&Head>,
    files: &'a [File],
    output: &str,
) -> Lines<'a, (Place, Vec<u8>)> {
    let base = StoreBaseLines::new(&files[0]);
    match head {
        Some(head) if !head.segments.is_empty() => {
            let segments = files[1..]
                .iter()
                .enumerate()
                .map(|(index, file)| {
                    Box::new(segment_rows(
                        file,
                        output,
                        head.generation,
                        index as i64 + 1,
                    )) as Lines<'a, _>
                })
                .collect();
            Box::new(merge_placed(base, segments))
        }
        _ => Box::new(
            base.enumerate()
                .map(|(index, line)| line.map(|line| ((index as i64, String::new()), line))),
        ),
    }
}

/// The unit store open for reading (`unit_cache.OpenStore`): its stamps, the head's or the single file's inline header; its head, None for the single file; and its parts, each opened as the head was read.
pub struct OpenStore {
    pub stamps: Vec<(PyValue, PyValue)>,
    pub head: Option<Head>,
    output: String,
    files: Vec<File>,
}

impl OpenStore {
    pub fn stamp(&self, name: &str) -> Option<&PyValue> {
        get(&self.stamps, name)
    }

    /// The store's materialized record lines in order, each with its place.
    pub fn lines(&self) -> Lines<'_, (Place, Vec<u8>)> {
        store_lines(self.head.as_ref(), &self.files, &self.output)
    }
}

/// `unit_cache.open_store`: the store under `out_dir` opened, every part its head lists checked against its size, or the single file a store written before the segment format leaves read by its inline header; None when there is neither. A store that exists and does not open is [`ScanError::Unreadable`]; a head or a part that does not read for another reason than the ones `open_store` turns into `StoreUnreadable` fails.
pub fn open_store(
    out_dir: &Path,
    names: &ScanNames,
) -> std::result::Result<Option<OpenStore>, ScanError> {
    let output = names.store_name.clone();
    match read_head(out_dir, &output) {
        Ok(Some(head)) => {
            let files = match open_parts(out_dir, &head, false) {
                Ok(files) => files,
                Err(ReadError::Head(why)) => return Err(ScanError::Unreadable(why)),
                Err(other) => return Err(uncaught(other)),
            };
            let PyValue::Dict(stamps) = head.stamps.clone() else {
                return Err(ScanError::Failed(
                    "a head's stamps are not a dict".to_owned(),
                ));
            };
            Ok(Some(OpenStore {
                stamps,
                head: Some(head),
                output,
                files,
            }))
        }
        Ok(None) => {
            let path = names.store_path(out_dir);
            let file = match File::open(&path) {
                Ok(file) => file,
                Err(failure) if failure.kind() == io::ErrorKind::NotFound => return Ok(None),
                Err(failure) => return Err(ScanError::Unreadable(format!("OSError: {failure}"))),
            };
            if file.metadata().is_ok_and(|metadata| metadata.is_dir()) {
                return Err(ScanError::Unreadable(format!(
                    "IsADirectoryError: {}",
                    path.display()
                )));
            }
            let line = match next_line(&mut gzip_lines(&file)) {
                Ok(Some(line)) => line,
                Ok(None) => {
                    return Err(ScanError::Unreadable(
                        "the header does not read: StopIteration()".to_owned(),
                    ));
                }
                Err(failure) => return Err(store_failure(failure)),
            };
            match json::loads_bytes(&line) {
                Ok(PyValue::Dict(stamps)) => Ok(Some(OpenStore {
                    stamps,
                    head: None,
                    output,
                    files: vec![file],
                })),
                Ok(_) => Err(ScanError::Unreadable(
                    "the header does not read: ValueError('the header is not an object')"
                        .to_owned(),
                )),
                Err(failure) if json::unheld(&failure) => Err(ScanError::Unheld(failure.0)),
                Err(failure) => Err(ScanError::Unreadable(format!(
                    "the header does not read: {}",
                    failure.0
                ))),
            }
        }
        Err(ReadError::Head(why)) => Err(ScanError::Unreadable(why)),
        Err(other) => Err(uncaught(other)),
    }
}

/// A manifest's entries, empty when it does not parse as an object, beside its identity.
type Manifest = (Vec<(PyValue, PyValue)>, String);

/// `unit_index._manifest_and_identity`: the manifest under `corpus` parsed, its entries empty when it does not parse as an object, beside its identity ([`manifest_identity`], or the sha256 of the raw bytes of one that does not parse); None when the manifest does not read.
pub(crate) fn manifest_and_identity(
    corpus: &Path,
    names: &ScanNames,
) -> std::result::Result<Option<Manifest>, ScanError> {
    let Ok(raw) = std::fs::read(corpus.join("manifest.json")) else {
        return Ok(None);
    };
    match json::loads_bytes(&raw) {
        Ok(document) => {
            let identity = manifest_identity(&document, &names.asset_components)?;
            let entries = match document {
                PyValue::Dict(entries) => entries,
                _ => Vec::new(),
            };
            Ok(Some((entries, identity)))
        }
        Err(failure) if json::unheld(&failure) => Err(ScanError::Unheld(failure.0)),
        Err(_) => Ok(Some((Vec::new(), sha256_hex(&raw)))),
    }
}

/// `unit_index.manifest_identity`: the sha256 of a parsed manifest written with sorted keys, its `inputs_fingerprint` without the components `assets` names.
pub fn manifest_identity(
    document: &PyValue,
    assets: &[String],
) -> std::result::Result<String, ScanError> {
    let mut document = document.clone();
    if let PyValue::Dict(entries) = &mut document {
        for (key, value) in entries.iter_mut() {
            if matches!(key, PyValue::Str(key) if key == "inputs_fingerprint")
                && let PyValue::Dict(recorded) = value
            {
                recorded.retain(
                    |(name, _)| !matches!(name, PyValue::Str(name) if assets.contains(name)),
                );
            }
        }
    }
    json::dumps_sorted(&document)
        .map(|text| sha256_hex(text.as_bytes()))
        .map_err(|failure| ScanError::Unheld(failure.0))
}

/// `unit_cache._manifest_stamp`: the identity of the manifest under `out_dir`, or `missing` when it does not read.
pub fn manifest_stamp(out_dir: &Path, names: &ScanNames) -> std::result::Result<String, ScanError> {
    Ok(manifest_and_identity(out_dir, names)?
        .map_or_else(|| "missing".to_owned(), |(_, identity)| identity))
}

/// `unit_cache._trusted_parts`: the parts the store's stamps record whose size on disk is the recorded one. Recorded parts that are a list or a `str` trust none, and fail, as `recorded[part]` raises outside any `except`, once a part they name is on disk; a name that is not a `str` fails, as do recorded parts of another type.
pub fn trusted_parts(
    out_dir: &Path,
    stamps: &[(PyValue, PyValue)],
) -> std::result::Result<HashSet<String>, ScanError> {
    let Some(recorded) = get(stamps, "parts").filter(|recorded| truthy(recorded)) else {
        return Ok(HashSet::new());
    };
    let type_error = |why: &str| ScanError::Failed(format!("TypeError: {why}"));
    let size_of = |part: &str| {
        if part.contains('\0') {
            return Err(ScanError::Failed(
                "ValueError: embedded null byte".to_owned(),
            ));
        }
        Ok(std::fs::metadata(out_dir.join(part))
            .ok()
            .and_then(|metadata| i64::try_from(metadata.len()).ok()))
    };
    let names: Vec<String> = match recorded {
        PyValue::Dict(entries) => {
            let mut trusted = HashSet::new();
            for (part, size) in entries {
                let PyValue::Str(part) = part else {
                    continue;
                };
                if size_of(part)?.is_some_and(|length| int_equal(size, length)) {
                    trusted.insert(part.clone());
                }
            }
            return Ok(trusted);
        }
        PyValue::List(items) => items
            .iter()
            .map(|item| match item {
                PyValue::Str(name) => Ok(name.clone()),
                other => Err(type_error(&format!("a part named by {}", other.kind()))),
            })
            .collect::<std::result::Result<_, _>>()?,
        PyValue::Str(text) => text.chars().map(String::from).collect(),
        other => {
            return Err(type_error(&format!(
                "'{}' object is not iterable",
                other.kind()
            )));
        }
    };
    for name in names.iter().collect::<std::collections::BTreeSet<_>>() {
        if size_of(name)?.is_some() {
            return Err(type_error(
                "the recorded parts are indexed by a part's name",
            ));
        }
    }
    Ok(HashSet::new())
}

/// One line of the prior store as the plan scans it (`unit_cache.ScannedLine`): its place in the materialized order, the ordinal whose input key it names, the record parsed when the scan parses named lines, and the raw line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ScannedLine {
    pub index: usize,
    pub ordinal: Option<u32>,
    pub cached: Option<ParsedCachedUnit>,
    pub line: Vec<u8>,
}

impl ScannedLine {
    /// The line as the `scan` stage records it: its index, its ordinal, its record and the sha256 of its bytes.
    pub fn value(&self) -> PyValue {
        PyValue::Tuple(vec![
            count_value(self.index),
            self.ordinal
                .map_or(PyValue::None, |ordinal| PyValue::Int(i64::from(ordinal))),
            self.cached
                .as_ref()
                .map_or(PyValue::None, ParsedCachedUnit::value),
            PyValue::str(sha256_hex(&self.line)),
        ])
    }
}

/// `unit_cache.scan_store`: every line of an open store in its materialized order, each matched to this build's units by input key over `named`, the index of the input-key column `column`, and, with `parse`, a named line parsed with its address kept only in a `trusted` part. The first failure ends it.
pub struct Scan<'a> {
    lines: Lines<'a, (Place, Vec<u8>)>,
    named: &'a KeyIndex,
    column: &'a [u8],
    parse: bool,
    trusted: &'a HashSet<String>,
    index: usize,
    done: bool,
}

pub fn scan_store<'a>(
    opened: &'a OpenStore,
    named: &'a KeyIndex,
    column: &'a [u8],
    parse: bool,
    trusted: &'a HashSet<String>,
) -> Scan<'a> {
    Scan {
        lines: opened.lines(),
        named,
        column,
        parse,
        trusted,
        index: 0,
        done: false,
    }
}

impl Iterator for Scan<'_> {
    type Item = std::result::Result<ScannedLine, ScanError>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        let line = match self.lines.next() {
            None => {
                self.done = true;
                return None;
            }
            Some(Err(failure)) => {
                self.done = true;
                return Some(Err(store_failure(failure)));
            }
            Some(Ok((_, line))) => line,
        };
        let ordinal = named_ordinal(self.named, self.column, &line);
        let mut cached = None;
        if self.parse && ordinal.is_some() {
            match parse_line(&line, self.trusted) {
                Ok(parsed) => cached = Some(parsed),
                Err(failure) => {
                    self.done = true;
                    return Some(Err(record_failure(failure)));
                }
            }
        }
        let index = self.index;
        self.index += 1;
        Some(Ok(ScannedLine {
            index,
            ordinal,
            cached,
            line,
        }))
    }
}

/// `unit_cache.scan_marked`: the lines of an open store at the materialized places `marked` names, ascending, each with its place and parsed as [`scan_store`] parses it. A line is read before the first place is looked for, as the loop's first iteration reads one before it can stop.
pub struct Marked<'a> {
    lines: Lines<'a, (Place, Vec<u8>)>,
    marked: std::slice::Iter<'a, u32>,
    following: Option<u32>,
    trusted: &'a HashSet<String>,
    index: usize,
    done: bool,
}

pub fn scan_marked<'a>(
    opened: &'a OpenStore,
    marked: &'a [u32],
    trusted: &'a HashSet<String>,
) -> Marked<'a> {
    let mut wanted = marked.iter();
    let following = wanted.next().copied();
    Marked {
        lines: opened.lines(),
        marked: wanted,
        following,
        trusted,
        index: 0,
        done: false,
    }
}

impl Iterator for Marked<'_> {
    type Item = std::result::Result<(usize, ParsedCachedUnit), ScanError>;

    fn next(&mut self) -> Option<Self::Item> {
        while !self.done {
            let line = match self.lines.next() {
                None => {
                    self.done = true;
                    return None;
                }
                Some(Err(failure)) => {
                    self.done = true;
                    return Some(Err(store_failure(failure)));
                }
                Some(Ok((_, line))) => line,
            };
            let index = self.index;
            self.index += 1;
            let Some(following) = self.following else {
                self.done = true;
                return None;
            };
            if index != following as usize {
                continue;
            }
            self.following = self.marked.next().copied();
            return Some(match parse_line(&line, self.trusted) {
                Ok(parsed) => Ok((index, parsed)),
                Err(failure) => {
                    self.done = true;
                    Err(record_failure(failure))
                }
            });
        }
        None
    }
}

/// The prior records the scan could not place by address, as columns in scan order (`unit_cache.WantedFragments`): each one's class, an index into `classes`, and its id word.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct WantedFragments {
    pub classes: Vec<String>,
    class_ids: HashMap<String, u32>,
    pub class_of: Vec<u32>,
    pub words: Vec<u64>,
}

impl WantedFragments {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add(&mut self, prior_class: &str, word: u64) {
        let next = self.classes.len() as u32;
        let found = *self
            .class_ids
            .entry(prior_class.to_owned())
            .or_insert_with(|| next);
        if found == next {
            self.classes.push(prior_class.to_owned());
        }
        self.class_of.push(found);
        self.words.push(word);
    }

    pub fn len(&self) -> usize {
        self.words.len()
    }

    pub fn is_empty(&self) -> bool {
        self.words.is_empty()
    }
}

/// Where the walk found the wanted records' fragments (`unit_cache.LocatedFragments`), as columns over the wanted records sorted by id word: the word and the class index, and for each found fragment its part (an index into `parts`, plus one; 0 when not found), start, length on disk, inflated length and 32-byte content key.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LocatedFragments {
    pub classes: Vec<String>,
    pub words: Vec<u64>,
    pub class_of: Vec<u32>,
    pub part: Vec<u32>,
    pub start: Vec<u64>,
    pub length: Vec<u32>,
    pub inflated: Vec<u32>,
    pub content_keys: Vec<u8>,
    pub parts: Vec<String>,
}

impl LocatedFragments {
    pub fn new(wanted: &WantedFragments) -> Self {
        let order = sorted_by_word(&wanted.words);
        let count = order.len();
        Self {
            classes: wanted.classes.clone(),
            words: order.iter().map(|&at| wanted.words[at as usize]).collect(),
            class_of: order
                .iter()
                .map(|&at| wanted.class_of[at as usize])
                .collect(),
            part: vec![0; count],
            start: vec![0; count],
            length: vec![0; count],
            inflated: vec![0; count],
            content_keys: vec![0; 32 * count],
            parts: Vec::new(),
        }
    }

    fn positions(&self, word: u64, class_id: &str) -> Vec<usize> {
        let first = self.words.partition_point(|&held| held < word);
        (first..self.words.len())
            .take_while(|&position| self.words[position] == word)
            .filter(|&position| self.classes[self.class_of[position] as usize] == class_id)
            .collect()
    }

    /// `LocatedFragments.find`: the fragment the walk found for the record of this class and id, stamped with its content key, or None.
    pub fn find(&self, prior_class: &str, unit_id: &str) -> Option<PriorFragment> {
        let word = id_word(unit_id)?;
        self.positions(word, prior_class)
            .into_iter()
            .find(|&position| self.part[position] != 0)
            .map(|position| PriorFragment {
                part: self.parts[self.part[position] as usize - 1].clone(),
                start: self.start[position] as i64,
                length: i64::from(self.length[position]),
                unit_id: unit_id.to_owned(),
                content_key: Some(hex(&self.content_keys[position * 32..position * 32 + 32])),
                byte_copied: false,
                inflated: i64::from(self.inflated[position]),
            })
    }

    pub fn found(&self) -> usize {
        self.part.iter().filter(|&&part| part != 0).count()
    }

    pub fn len(&self) -> usize {
        self.words.len()
    }

    pub fn is_empty(&self) -> bool {
        self.words.is_empty()
    }

    /// Record a live fragment the walk reads for each wanted record of its class and id word, as `locate_prior_fragments` does: a content key that is not 64 lowercase hex digits leaves that record unplaced, clearing a placement an earlier copy gave it.
    fn place(
        &mut self,
        class_id: &str,
        slot: &LiveSlot,
        fragment: &PyValue,
        part_ids: &mut HashMap<String, u32>,
    ) -> std::result::Result<(), Thrown> {
        let Some(word) = id_word(&slot.unit_id) else {
            return Ok(());
        };
        for position in self.positions(word, class_id) {
            let content = match lookup(fragment, "content_key") {
                Some(PyValue::Str(key)) => content_bytes(key),
                _ => None,
            };
            let Some(content) = content else {
                self.part[position] = 0;
                continue;
            };
            let part = match part_ids.get(&slot.part) {
                Some(&part) => part,
                None => {
                    self.parts.push(slot.part.clone());
                    let part = self.parts.len() as u32;
                    part_ids.insert(slot.part.clone(), part);
                    part
                }
            };
            self.part[position] = part;
            self.start[position] = slot.start;
            self.length[position] = narrow(slot.length)?;
            self.inflated[position] = narrow(slot.inflated)?;
            self.content_keys[position * 32..position * 32 + 32].copy_from_slice(&content);
        }
        Ok(())
    }

    /// The columns as the `scan` stage records them, beside the wanted records they were made from.
    pub fn value(&self, wanted: &WantedFragments) -> PyValue {
        let ints = |values: &mut dyn Iterator<Item = i64>| {
            PyValue::Tuple(values.map(PyValue::Int).collect())
        };
        PyValue::Tuple(vec![
            PyValue::str_tuple(&wanted.classes),
            ints(&mut wanted.class_of.iter().map(|&value| i64::from(value))),
            words_value(&wanted.words),
            words_value(&self.words),
            ints(&mut self.class_of.iter().map(|&value| i64::from(value))),
            ints(&mut self.part.iter().map(|&value| i64::from(value))),
            ints(&mut self.start.iter().map(|&value| value as i64)),
            ints(&mut self.length.iter().map(|&value| i64::from(value))),
            ints(&mut self.inflated.iter().map(|&value| i64::from(value))),
            PyValue::str(hex(&self.content_keys)),
            PyValue::str_tuple(&self.parts),
        ])
    }
}

/// Id words as the stage records them, each as `%016x`, since a word past 63 bits is no `i64`.
fn words_value(words: &[u64]) -> PyValue {
    PyValue::Tuple(
        words
            .iter()
            .map(|word| PyValue::str(format!("{word:016x}")))
            .collect(),
    )
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// A content key's bytes when it is the lowercase hex of 32 bytes, as `bytes.fromhex(key).hex() == key` holds.
fn content_bytes(key: &str) -> Option<Vec<u8>> {
    if key.len() != 64
        || !key
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
    {
        return None;
    }
    let digit = |byte: u8| char::from(byte).to_digit(16).expect("checked hex") as u8;
    Some(
        key.as_bytes()
            .chunks(2)
            .map(|pair| digit(pair[0]) * 16 + digit(pair[1]))
            .collect(),
    )
}

fn narrow(value: u64) -> std::result::Result<u32, Thrown> {
    u32::try_from(value).map_err(|_| {
        Thrown::Other(
            "OverflowError",
            format!("{value} does not fit an unsigned int"),
        )
    })
}

// --- the live-fragment iterator -------------------------------------------------------------------

/// An exception the live-fragment iterator raises, by the class its callers test, or a value Python reads that the reader does not hold.
#[derive(Debug)]
pub(crate) enum Thrown {
    /// `OSError` or a subclass.
    Os(String),
    /// `ValueError` or a subclass, `unit_index.LayoutError` among them.
    Value(String),
    /// Any other exception, by its class.
    Other(&'static str, String),
    Unheld(String),
}

fn layout(why: impl Into<String>) -> Thrown {
    Thrown::Value(why.into())
}

impl From<ReadError> for Thrown {
    fn from(failure: ReadError) -> Self {
        match failure {
            ReadError::Head(why) | ReadError::Value(_, why) => Self::Value(why),
            ReadError::Gzip(GzipError::Eof(why)) => Self::Other("EOFError", why),
            ReadError::Gzip(GzipError::BadGzipFile(why)) | ReadError::Io(why) => Self::Os(why),
            ReadError::Gzip(GzipError::Zlib(why)) => Self::Other("error", why),
            ReadError::Unheld(why) => Self::Unheld(why),
            ReadError::Raised(class, why) => Self::Other(class, why),
        }
    }
}

impl From<RecordError> for Thrown {
    fn from(failure: RecordError) -> Self {
        match failure {
            RecordError::Unreadable(why) => Self::Value(why),
            RecordError::Failed(why) => Self::Other("Exception", why),
            RecordError::Unheld(why) => Self::Unheld(why),
        }
    }
}

/// What `unit_index._store_slots` and `_index_classes` take as a corpus the store or the index cannot give the live fragments of: an `OSError`, `EOFError`, `ValueError`, `KeyError`, `TypeError` or `StopIteration`, and for the index `zlib.error` too.
fn into_layout(failure: Thrown, zlib: bool) -> Thrown {
    match failure {
        Thrown::Os(why) | Thrown::Value(why) => layout(why),
        Thrown::Other(class, why)
            if matches!(
                class,
                "EOFError" | "KeyError" | "TypeError" | "StopIteration"
            ) || (zlib && class == "error") =>
        {
            layout(format!("{class}: {why}"))
        }
        other => other,
    }
}

fn attribute(value: &PyValue, name: &str) -> Thrown {
    Thrown::Other(
        "AttributeError",
        format!("'{}' object has no attribute '{name}'", value.kind()),
    )
}

/// `meta.get(name)` on a manifest class entry: None when it is missing, `AttributeError` when the entry is not a dict.
fn meta_get<'a>(meta: &'a PyValue, name: &str) -> std::result::Result<Option<&'a PyValue>, Thrown> {
    match meta {
        PyValue::Dict(_) => Ok(lookup(meta, name)),
        other => Err(attribute(other, "get")),
    }
}

/// `meta[name]`.
fn meta_item<'a>(meta: &'a PyValue, name: &str) -> std::result::Result<&'a PyValue, Thrown> {
    match meta {
        PyValue::Dict(_) => {
            lookup(meta, name).ok_or_else(|| Thrown::Other("KeyError", format!("'{name}'")))
        }
        other => Err(Thrown::Other(
            "TypeError",
            format!("'{}' object is not subscriptable", other.kind()),
        )),
    }
}

/// `str(meta.get("id", ""))`, the id a class's slots and walks go by.
fn class_name(meta: &PyValue) -> std::result::Result<String, Thrown> {
    Ok(meta_get(meta, "id")?.map_or_else(String::new, py_str))
}

/// What iterating a value gives as `str` items: a list's items, a `str`'s characters, a dict's keys.
fn str_items(value: &PyValue) -> std::result::Result<Vec<String>, Thrown> {
    match value {
        PyValue::List(items) | PyValue::Tuple(items) => Ok(items.iter().map(py_str).collect()),
        PyValue::Str(text) => Ok(text.chars().map(String::from).collect()),
        PyValue::Dict(entries) => Ok(entries.iter().map(|(key, _)| py_str(key)).collect()),
        other => Err(Thrown::Other(
            "TypeError",
            format!("'{}' object is not iterable", other.kind()),
        )),
    }
}

/// `unit_index.class_shards`: a manifest class entry's shard parts, its `shards` or its one `shard`.
fn class_shards(meta: &PyValue) -> std::result::Result<Vec<String>, Thrown> {
    match meta_get(meta, "shards")? {
        None | Some(PyValue::None) => Ok(vec![py_str(meta_item(meta, "shard")?)]),
        Some(shards) => str_items(shards),
    }
}

/// `unit_index.overflow_index(class_id, part) is not None`: whether `part` is one of the class's overflow parts ([`overflow_index`]).
fn is_overflow(class_id: &str, part: &str) -> bool {
    overflow_index(class_id, part).is_some()
}

/// `unit_index._manifest`: the manifest under `corpus` read as UTF-8 text and parsed, empty when it does not read, parse or hold an object.
fn manifest_entries(corpus: &Path) -> std::result::Result<Vec<(PyValue, PyValue)>, Thrown> {
    let Ok(raw) = std::fs::read(corpus.join("manifest.json")) else {
        return Ok(Vec::new());
    };
    let Ok(text) = String::from_utf8(raw) else {
        return Ok(Vec::new());
    };
    match json::loads(&text) {
        Ok(PyValue::Dict(entries)) => Ok(entries),
        Ok(_) => Ok(Vec::new()),
        Err(failure) if json::unheld(&failure) => Err(Thrown::Unheld(failure.0)),
        Err(_) => Ok(Vec::new()),
    }
}

/// `list(manifest.get("classes") or ())`.
fn class_entries(manifest: &[(PyValue, PyValue)]) -> std::result::Result<Vec<PyValue>, Thrown> {
    match get(manifest, "classes") {
        None => Ok(Vec::new()),
        Some(classes) if !truthy(classes) => Ok(Vec::new()),
        Some(PyValue::List(items)) => Ok(items.clone()),
        Some(PyValue::Str(text)) => Ok(text.chars().map(PyValue::str).collect()),
        Some(PyValue::Dict(entries)) => Ok(entries.iter().map(|(key, _)| key.clone()).collect()),
        Some(other) => Err(Thrown::Other(
            "TypeError",
            format!("'{}' object is not iterable", other.kind()),
        )),
    }
}

/// Where one live fragment lies (`unit_index.LiveSlot`).
#[derive(Clone, Debug, PartialEq, Eq)]
struct LiveSlot {
    unit_id: String,
    part: String,
    start: u64,
    length: u64,
    inflated: u64,
}

/// One class's live slots (`unit_index.ClassSlots`), by its parts.
#[derive(Clone, Debug)]
struct ClassSlots {
    parts: Vec<String>,
    index: HashMap<String, usize>,
    slots: Vec<LiveSlot>,
}

impl ClassSlots {
    fn new(parts: Vec<String>) -> Self {
        let index = parts
            .iter()
            .enumerate()
            .map(|(at, part)| (part.clone(), at))
            .collect();
        Self {
            parts,
            index,
            slots: Vec::new(),
        }
    }

    fn add(
        &mut self,
        unit_id: &str,
        part: &str,
        start: i64,
        length: i64,
        inflated: i64,
    ) -> std::result::Result<(), Thrown> {
        if !unit_id.is_ascii() {
            return Err(layout(format!(
                "UnicodeEncodeError: {} is not ASCII",
                py_str(&PyValue::str(unit_id))
            )));
        }
        if unit_id.len() > ID_WIDTH || unit_id.contains('\0') {
            return Err(layout(format!("{unit_id:?} is not a unit id")));
        }
        let Some(&index) = self.index.get(part) else {
            return Err(layout(format!(
                "{unit_id} is addressed in {part}, which its class does not list"
            )));
        };
        let overflow = |what: &str| Thrown::Other("OverflowError", format!("{what} does not fit"));
        if index > usize::from(u16::MAX) {
            return Err(overflow("the part index"));
        }
        let start = u64::try_from(start).map_err(|_| overflow("the start"))?;
        let length = u32::try_from(length).map_err(|_| overflow("the length"))?;
        let inflated = u32::try_from(inflated).map_err(|_| overflow("the inflated length"))?;
        self.slots.push(LiveSlot {
            unit_id: unit_id.to_owned(),
            part: self.parts[index].clone(),
            start,
            length: u64::from(length),
            inflated: u64::from(inflated),
        });
        Ok(())
    }

    /// The slots in id order; an id held twice is refused.
    fn ordered(&self) -> std::result::Result<Vec<LiveSlot>, Thrown> {
        let mut slots = self.slots.clone();
        slots.sort_by(|left, right| left.unit_id.cmp(&right.unit_id));
        if let Some(pair) = slots
            .windows(2)
            .find(|pair| pair[0].unit_id == pair[1].unit_id)
        {
            return Err(layout(format!(
                "{} is live twice in one class",
                pair[0].unit_id
            )));
        }
        Ok(slots)
    }
}

/// `unit_index.index_key`: an index line's key, its class as `class_shard_key` gives it and its id, each as bytes.
fn index_key(line: &[u8]) -> std::result::Result<Key, ReadError> {
    let missing = || ReadError::Value("ValueError", "subsection not found".to_owned());
    let end = find(line, b"\"", ID_OPEN.len()).ok_or_else(missing)?;
    let start = find(line, CLASS_SEAM, end).ok_or_else(missing)? + CLASS_SEAM.len();
    let stop = match find(line, CLUSTER_SEAM, start) {
        Some(stop) => stop,
        None => line
            .iter()
            .rposition(|&byte| byte == b'}')
            .ok_or_else(missing)?,
    };
    let raw = line.get(start..stop).unwrap_or_default();
    let class = match json::loads_bytes(raw) {
        Ok(PyValue::Str(class)) => format!("{class}.json"),
        Ok(_) => ".json".to_owned(),
        Err(failure) if json::unheld(&failure) => return Err(ReadError::Unheld(failure.0)),
        Err(failure) => return Err(ReadError::Value("JSONDecodeError", failure.0)),
    };
    Ok(vec![class.into_bytes(), line[ID_OPEN.len()..end].to_vec()])
}

fn find(haystack: &[u8], needle: &[u8], start: usize) -> Option<usize> {
    if start > haystack.len() {
        return None;
    }
    haystack[start..]
        .windows(needle.len())
        .position(|window| window == needle)
        .map(|at| start + at)
}

/// The units index open for reading (`unit_index.OpenIndex`): its header, its head (None for the single file) and its parts.
pub(crate) struct OpenIndex {
    header: Vec<(PyValue, PyValue)>,
    head: Option<Head>,
    files: Vec<File>,
}

impl OpenIndex {
    /// `OpenIndex.lines`: the base's lines past its header line, or the base and the segments merged by key.
    pub(crate) fn lines(&self, output: &str) -> Lines<'_, Vec<u8>> {
        let base = base_rows(&self.files[0]);
        match &self.head {
            Some(head) if !head.segments.is_empty() => {
                let segments = self.files[1..]
                    .iter()
                    .enumerate()
                    .map(|(index, file)| {
                        Box::new(keyed_segment_rows(
                            file,
                            output,
                            head.generation,
                            index as i64 + 1,
                        )) as Lines<'_, _>
                    })
                    .collect();
                Box::new(merge_keyed(base, segments, &index_key, &index_key))
            }
            _ => Box::new(base),
        }
    }
}

/// `unit_index.open_index`: the index's head and parts, or the single file read by its inline header; None when there is neither. What `_current` catches is an `OSError` or a `ValueError` here.
pub(crate) fn open_index(
    corpus: &Path,
    names: &ScanNames,
) -> std::result::Result<Option<OpenIndex>, Thrown> {
    if let Some(head) = read_head(corpus, &names.index_name)? {
        let files = open_parts(corpus, &head, false)?;
        let PyValue::Dict(header) = head.stamps.clone() else {
            return Err(layout("a head's stamps are not a dict"));
        };
        return Ok(Some(OpenIndex {
            header,
            head: Some(head),
            files,
        }));
    }
    let file = match File::open(names.index_path(corpus)) {
        Ok(file) => file,
        Err(failure) if failure.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(failure) => return Err(Thrown::Os(failure.to_string())),
    };
    if file.metadata().is_ok_and(|metadata| metadata.is_dir()) {
        return Err(Thrown::Os("Is a directory".to_owned()));
    }
    let line = next_line(&mut gzip_lines(&file))
        .map_err(|failure| match Thrown::from(failure) {
            Thrown::Unheld(why) => Thrown::Unheld(why),
            other => layout(format!("the header does not read: {other:?}")),
        })?
        .unwrap_or_default();
    match json::loads_bytes(&line) {
        Ok(PyValue::Dict(header)) => Ok(Some(OpenIndex {
            header,
            head: None,
            files: vec![file],
        })),
        Ok(_) => Err(layout("the header is not an object")),
        Err(failure) if json::unheld(&failure) => Err(Thrown::Unheld(failure.0)),
        Err(failure) => Err(layout(failure.0)),
    }
}

/// `unit_index._current`: the index opened when it describes the manifest beside it, in this reader's format and stamped with the manifest's identity; None when it does not.
fn current_index(
    corpus: &Path,
    names: &ScanNames,
) -> std::result::Result<Option<OpenIndex>, Thrown> {
    let opened = match open_index(corpus, names) {
        Ok(Some(opened)) => opened,
        Ok(None) | Err(Thrown::Os(_) | Thrown::Value(_)) => return Ok(None),
        Err(other) => return Err(other),
    };
    let identity = match manifest_and_identity(corpus, names) {
        Ok(Some((_, identity))) => identity,
        Ok(None) => return Ok(None),
        Err(ScanError::Unheld(why)) => return Err(Thrown::Unheld(why)),
        Err(other) => return Err(Thrown::Other("Exception", other.to_string())),
    };
    let header = &opened.header;
    Ok((is_str(get(header, "format"), &names.index_format)
        && is_str(get(header, "manifest_sha256"), &identity))
    .then_some(opened))
}

/// `unit_index.index_is_current`: whether the index under `corpus` describes the manifest beside it ([`current_index`]). A failure no caller of `_current` catches fails, by its class.
pub(crate) fn index_is_current(
    corpus: &Path,
    names: &ScanNames,
) -> std::result::Result<bool, ScanError> {
    match current_index(corpus, names) {
        Ok(opened) => Ok(opened.is_some()),
        Err(Thrown::Unheld(why)) => Err(ScanError::Unheld(why)),
        Err(Thrown::Other(class, why)) => Err(ScanError::Failed(format!("{class}: {why}"))),
        Err(Thrown::Os(why) | Thrown::Value(why)) => Err(ScanError::Failed(why)),
    }
}

/// `unit_index._index_classes`: each class's live ids as the current index lists them; an index that is not current or does not read to its end is a `LayoutError`.
fn index_classes(
    corpus: &Path,
    names: &ScanNames,
) -> std::result::Result<HashMap<String, HashSet<String>>, Thrown> {
    let opened = current_index(corpus, names)?
        .ok_or_else(|| layout("the index does not describe the manifest beside it"))?;
    let mut classes: HashMap<String, HashSet<String>> = HashMap::new();
    for line in opened.lines(&names.index_name) {
        let line = line.map_err(|failure| into_layout(failure.into(), true))?;
        let cut = find(&line, CLUSTER_SEAM, 0).ok_or_else(|| layout("subsection not found"))?;
        let mut head = line[..cut].to_vec();
        head.push(b'}');
        let record = match json::loads_bytes(&head) {
            Ok(record) => record,
            Err(failure) if json::unheld(&failure) => return Err(Thrown::Unheld(failure.0)),
            Err(failure) => return Err(layout(failure.0)),
        };
        let class = meta_item(&record, "class").map_err(|failure| into_layout(failure, true))?;
        hashable(class).map_err(|failure| into_layout(failure.into(), true))?;
        let id = meta_item(&record, "id").map_err(|failure| into_layout(failure, true))?;
        hashable(id).map_err(|failure| into_layout(failure.into(), true))?;
        if let PyValue::Str(class) = class {
            let ids = classes.entry(class.clone()).or_default();
            if let PyValue::Str(id) = id {
                ids.insert(id.clone());
            }
        }
    }
    Ok(classes)
}

/// `unit_index._describing_store_head`: the unit store's head when the store describes the shards beside it: this format, stamped with the manifest's identity, recording every part the manifest lists at the size it has on disk.
fn describing_store_head(
    corpus: &Path,
    metas: &[PyValue],
    names: &ScanNames,
) -> std::result::Result<Head, Thrown> {
    let head = read_head(corpus, &names.store_name)
        .map_err(|failure| into_layout(failure.into(), false))?
        .ok_or_else(|| layout("no unit store head"))?;
    let PyValue::Dict(stamps) = &head.stamps else {
        return Err(layout("a head's stamps are not a dict"));
    };
    let describes = is_str(get(stamps, "format"), &names.store_format)
        && match manifest_and_identity(corpus, names) {
            Ok(Some((_, identity))) => is_str(get(stamps, "manifest_sha256"), &identity),
            Ok(None) => return Err(layout("the manifest does not read")),
            Err(ScanError::Unheld(why)) => return Err(Thrown::Unheld(why)),
            Err(other) => return Err(Thrown::Other("Exception", other.to_string())),
        };
    if !describes {
        return Err(layout("the unit store describes another manifest"));
    }
    let mut parts = Vec::new();
    for meta in metas {
        parts.extend(class_shards(meta).map_err(|failure| into_layout(failure, false))?);
    }
    let shard_moved = || layout("a shard part is not the one the unit store records");
    let recorded = match get(stamps, "parts") {
        Some(recorded) if truthy(recorded) => match recorded {
            PyValue::Dict(entries) => entries,
            _ => return Err(shard_moved()),
        },
        _ => &Vec::new(),
    };
    let recorded_names: HashSet<String> = recorded.iter().map(|(name, _)| py_str(name)).collect();
    let listed: HashSet<String> = parts.iter().cloned().collect();
    if recorded_names != listed {
        return Err(shard_moved());
    }
    for part in &parts {
        let size = std::fs::metadata(corpus.join(part)).map_err(|_| shard_moved())?;
        let held = get(recorded, part).unwrap_or(&PyValue::None);
        if !i64::try_from(size.len()).is_ok_and(|size| int_equal(held, size)) {
            return Err(shard_moved());
        }
    }
    Ok(head)
}

/// `unit_index._store_slots`: every class's live slots as the unit store's records give them; a store that does not describe the shards, or a record not addressed in a part its class lists, is a `LayoutError`.
fn store_slots(
    corpus: &Path,
    metas: &[PyValue],
    names: &ScanNames,
) -> std::result::Result<HashMap<String, ClassSlots>, Thrown> {
    let caught = |failure: Thrown| into_layout(failure, false);
    let head = describing_store_head(corpus, metas, names)?;
    let mut part_class: HashMap<String, PyValue> = HashMap::new();
    for meta in metas {
        for part in class_shards(meta).map_err(caught)? {
            part_class.insert(part, meta_item(meta, "id").map_err(caught)?.clone());
        }
    }
    let mut slots: HashMap<String, ClassSlots> = HashMap::new();
    for meta in metas {
        let id = py_str(meta_item(meta, "id").map_err(caught)?);
        slots.insert(id, ClassSlots::new(class_shards(meta).map_err(caught)?));
    }
    let files = open_parts(corpus, &head, false).map_err(|failure| caught(failure.into()))?;
    for line in store_lines(Some(&head), &files, &names.store_name) {
        let (_, line) = line.map_err(|failure| caught(failure.into()))?;
        let address = line_address(&line).map_err(|failure| caught(failure.into()))?;
        let unit_id = line_id(&line).map_err(|failure| caught(failure.into()))?;
        let class_id = address
            .as_ref()
            .and_then(|address| part_class.get(&address.0))
            .filter(|class_id| **class_id != PyValue::None);
        let (Some((part, start, length, inflated)), Some(class_id)) = (&address, class_id) else {
            return Err(layout(format!(
                "{unit_id} is not addressed in a part its class lists"
            )));
        };
        let recorded = line_class(&line).map_err(|failure| caught(failure.into()))?;
        if !py_eq(&recorded, class_id) {
            return Err(layout(format!(
                "{unit_id} is not addressed in a part its class lists"
            )));
        }
        let PyValue::Str(class_id) = class_id else {
            return Err(layout(format!("KeyError: {}", py_str(class_id))));
        };
        let held = slots
            .get_mut(class_id)
            .ok_or_else(|| layout(format!("KeyError: {class_id}")))?;
        held.add(&unit_id, part, *start, *length, *inflated)
            .map_err(caught)?;
    }
    Ok(slots)
}

enum Source {
    Store(HashMap<String, ClassSlots>),
    Index(HashMap<String, HashSet<String>>),
    Parts,
}

/// `unit_index.LiveCorpus`, as `locate_prior_fragments` reads it: the live fragments of a corpus's classes, each class's in id order, decided by the unit store where it describes the shards, else by the current index, else, for a corpus with no store head and no overflow part, by the parts themselves.
struct LiveCorpus {
    corpus: PathBuf,
    metas: Vec<PyValue>,
    source: Source,
    descriptors: HashMap<String, File>,
}

/// What `read_head` says of the store's head, as `LiveCorpus` holds it: none, one, or one that does not read, which counts as present.
#[derive(PartialEq)]
enum HeadState {
    Absent,
    Present,
}

impl LiveCorpus {
    fn open(corpus: &Path, names: &ScanNames) -> std::result::Result<Self, Thrown> {
        let manifest = manifest_entries(corpus)?;
        let metas = class_entries(&manifest)?;
        let head = match read_head(corpus, &names.store_name) {
            Ok(None) => HeadState::Absent,
            Ok(Some(_)) | Err(ReadError::Head(_)) => HeadState::Present,
            Err(other) => return Err(other.into()),
        };
        let opened = |source| Self {
            corpus: corpus.to_path_buf(),
            metas: metas.clone(),
            source,
            descriptors: HashMap::new(),
        };
        if head == HeadState::Present {
            match store_slots(corpus, &metas, names) {
                Ok(slots) => return Ok(opened(Source::Store(slots))),
                Err(Thrown::Value(_)) => {}
                Err(other) => return Err(other),
            }
        }
        if current_index(corpus, names)?.is_some() {
            match index_classes(corpus, names) {
                Ok(index) => return Ok(opened(Source::Index(index))),
                Err(Thrown::Value(_)) => {}
                Err(other) => return Err(other),
            }
        }
        if head == HeadState::Absent {
            let mut overflow = false;
            for meta in &metas {
                let class_id = class_name(meta)?;
                if class_shards(meta)?
                    .iter()
                    .any(|part| is_overflow(&class_id, part))
                {
                    overflow = true;
                    break;
                }
            }
            if !overflow {
                return Ok(opened(Source::Parts));
            }
        }
        let found = if head == HeadState::Present {
            "has a unit store head, but neither the store nor the index describes its shards"
        } else {
            "lists an overflow part, which a build appended, but has no unit store head, and its index does not describe its shards"
        };
        Err(layout(format!(
            "{} {found}, so its live fragments cannot be told from its dead slots",
            corpus.display()
        )))
    }

    /// One class's live slots in id order (`LiveCorpus.slots`).
    fn slots(&self, meta: &PyValue) -> std::result::Result<Vec<LiveSlot>, Thrown> {
        let class_id = class_name(meta)?;
        let parts = class_shards(meta)?;
        let live = match &self.source {
            Source::Store(slots) => {
                return slots
                    .get(&class_id)
                    .map_or(Ok(Vec::new()), ClassSlots::ordered);
            }
            Source::Index(index) => index.get(&class_id),
            Source::Parts => None,
        };
        let empty = HashSet::new();
        let live = live.unwrap_or(&empty);
        let mut last: Vec<(String, (String, usize, usize))> = Vec::new();
        let mut at: HashMap<String, usize> = HashMap::new();
        for part in &parts {
            walk_part(&self.corpus.join(part), &mut |start, length, fragment| {
                if let Some(PyValue::Str(unit_id)) = lookup(fragment, "id")
                    && live.contains(unit_id)
                {
                    let placed = (part.clone(), start, length);
                    match at.get(unit_id) {
                        Some(&index) => last[index].1 = placed,
                        None => {
                            at.insert(unit_id.clone(), last.len());
                            last.push((unit_id.clone(), placed));
                        }
                    }
                }
                Ok(())
            })?;
        }
        let mut collected = ClassSlots::new(parts);
        for (unit_id, (part, start, length)) in last {
            collected.add(&unit_id, &part, start as i64, length as i64, 0)?;
        }
        collected.ordered()
    }

    /// One live fragment's plain bytes (`LiveCorpus.read`), a deflated slot inflated and checked to hold its unit.
    fn read(&mut self, slot: &LiveSlot) -> std::result::Result<Vec<u8>, Thrown> {
        if !self.descriptors.contains_key(&slot.part) {
            let file = File::open(self.corpus.join(&slot.part))
                .map_err(|failure| Thrown::Os(failure.to_string()))?;
            self.descriptors.insert(slot.part.clone(), file);
        }
        let file = &self.descriptors[&slot.part];
        let size = file
            .metadata()
            .map_err(|failure| Thrown::Os(failure.to_string()))?
            .len();
        let mut body = vec![0; slot.length.min(size.saturating_sub(slot.start)) as usize];
        let mut filled = 0;
        while filled < body.len() {
            match file.read_at(&mut body[filled..], slot.start + filled as u64) {
                Ok(0) => break,
                Ok(read) => filled += read,
                Err(failure) if failure.kind() == io::ErrorKind::Interrupted => {}
                Err(failure) => return Err(Thrown::Os(failure.to_string())),
            }
        }
        body.truncate(filled);
        if body.len() as u64 != slot.length {
            return Err(layout(format!(
                "{} ends before {}'s {} bytes at {}",
                slot.part, slot.unit_id, slot.length, slot.start
            )));
        }
        if slot.inflated == 0 {
            return Ok(body);
        }
        let body = inflate_fragment(&body, Some(slot.inflated as usize)).map_err(|why| {
            layout(format!(
                "{} at {}+{} does not hold {}: {why}",
                slot.part, slot.start, slot.length, slot.unit_id
            ))
        })?;
        if !holds_unit(&body, &slot.unit_id) {
            return Err(layout(format!(
                "{} at {}+{} inflates to another unit than {}",
                slot.part, slot.start, slot.length, slot.unit_id
            )));
        }
        Ok(body)
    }

    /// Visit every live fragment parsed, with its class's id and its slot, classes in shard order, only those `wanted` names (`LiveCorpus.fragments`).
    fn fragments(
        &mut self,
        wanted: &HashSet<String>,
        visit: &mut dyn FnMut(&str, &LiveSlot, &PyValue) -> std::result::Result<(), Thrown>,
    ) -> std::result::Result<(), Thrown> {
        let mut keyed = Vec::with_capacity(self.metas.len());
        for (at, meta) in self.metas.iter().enumerate() {
            keyed.push((format!("{}.json", class_name(meta)?), at));
        }
        keyed.sort_by(|left, right| left.0.cmp(&right.0));
        let metas = self.metas.clone();
        for (_, at) in keyed {
            let meta = &metas[at];
            let class_id = match meta_get(meta, "id")? {
                Some(PyValue::Str(id)) if wanted.contains(id) => id.clone(),
                Some(unhashable @ (PyValue::List(_) | PyValue::Dict(_))) => {
                    return Err(Thrown::Other(
                        "TypeError",
                        format!("unhashable type: '{}'", unhashable.kind()),
                    ));
                }
                _ => continue,
            };
            if matches!(self.source, Source::Parts) {
                for part in class_shards(meta)? {
                    walk_part(&self.corpus.join(&part), &mut |start, length, fragment| {
                        let unit_id = match fragment {
                            PyValue::Dict(_) => lookup(fragment, "id").unwrap_or(&PyValue::None),
                            _ => &PyValue::None,
                        };
                        let slot = LiveSlot {
                            unit_id: py_str(unit_id),
                            part: part.clone(),
                            start: start as u64,
                            length: length as u64,
                            inflated: 0,
                        };
                        visit(&class_id, &slot, fragment)
                    })?;
                }
                continue;
            }
            for slot in self.slots(meta)? {
                let body = self.read(&slot)?;
                let fragment = match json::loads_bytes(&body) {
                    Ok(fragment) => fragment,
                    Err(failure) if json::unheld(&failure) => {
                        return Err(Thrown::Unheld(failure.0));
                    }
                    Err(failure) => return Err(layout(failure.0)),
                };
                let holds = matches!(&fragment, PyValue::Dict(_))
                    && matches!(lookup(&fragment, "id"), Some(PyValue::Str(id)) if *id == slot.unit_id);
                if !holds {
                    return Err(layout(format!(
                        "{} does not hold {} at {}+{}",
                        slot.part, slot.unit_id, slot.start, slot.length
                    )));
                }
                visit(&class_id, &slot, &fragment)?;
            }
        }
        Ok(())
    }
}

fn skip_whitespace(text: &[u8], mut at: usize) -> usize {
    while at < text.len() && matches!(text[at], b' ' | b'\t' | b'\n' | b'\r') {
        at += 1;
    }
    at
}

/// `unit_index._walk_part`: each element of one shard part with its byte offset and length, parsed one at a time and visited before the next is parsed. A part that is not ASCII is a `LayoutError`, and one that is not a JSON array a `ValueError`.
fn walk_part(
    path: &Path,
    visit: &mut dyn FnMut(usize, usize, &PyValue) -> std::result::Result<(), Thrown>,
) -> std::result::Result<(), Thrown> {
    let raw = std::fs::read(path).map_err(|failure| Thrown::Os(failure.to_string()))?;
    if !raw.is_ascii() {
        let name = path
            .file_name()
            .map_or_else(String::new, |name| name.to_string_lossy().into_owned());
        return Err(layout(format!(
            "{name} is not ASCII, so it holds a deflated fragment only the unit store can place"
        )));
    }
    let text = String::from_utf8(raw).expect("ASCII is UTF-8");
    let bytes = text.as_bytes();
    let mut index = skip_whitespace(bytes, 0);
    if index >= bytes.len() || bytes[index] != b'[' {
        return Err(layout("a shard part is a JSON array"));
    }
    index = skip_whitespace(bytes, index + 1);
    while index < bytes.len() && bytes[index] != b']' {
        let (fragment, end) = match json::raw_decode(&text, index) {
            Ok(decoded) => decoded,
            Err(failure) if json::unheld(&failure) => return Err(Thrown::Unheld(failure.0)),
            Err(failure) => return Err(layout(failure.0)),
        };
        visit(index, end - index, &fragment)?;
        index = skip_whitespace(bytes, end);
        if index < bytes.len() && bytes[index] == b',' {
            index = skip_whitespace(bytes, index + 1);
        }
    }
    Ok(())
}

/// `unit_cache.locate_prior_fragments`: where the prior shards hold the wanted records' fragments, with the content key each carries, read through the live-fragment iterator over the classes the records name. A corpus the iterator refuses, or an `OSError` or a `ValueError` partway, keeps what was placed before it; any other exception fails.
pub fn locate_prior_fragments(
    out_dir: &Path,
    wanted: &WantedFragments,
    names: &ScanNames,
) -> std::result::Result<LocatedFragments, ScanError> {
    let mut located = LocatedFragments::new(wanted);
    if wanted.is_empty() {
        return Ok(located);
    }
    let classes: HashSet<String> = wanted.classes.iter().cloned().collect();
    let mut part_ids: HashMap<String, u32> = HashMap::new();
    let walked = LiveCorpus::open(out_dir, names).and_then(|mut live| {
        live.fragments(&classes, &mut |class_id, slot, fragment| {
            located.place(class_id, slot, fragment, &mut part_ids)
        })
    });
    match walked {
        Ok(()) | Err(Thrown::Os(_) | Thrown::Value(_)) => Ok(located),
        Err(Thrown::Unheld(why)) => Err(ScanError::Unheld(why)),
        Err(Thrown::Other(class, why)) => Err(ScanError::Failed(format!("{class}: {why}"))),
    }
}

/// `unit_cache.adopt_single_file`: put the single file a store written before the segment format leaves under a head, as generation 0's base, by a hard link, with `stamps` as the head's stamps and a base of `lines` records of `rows_bytes` bytes, committed at once.
pub fn adopt_single_file(
    out_dir: &Path,
    names: &ScanNames,
    stamps: &[(PyValue, PyValue)],
    lines: i64,
    rows_bytes: i64,
    sink: &Sink,
) -> std::result::Result<Head, ScanError> {
    let failed = |failure: &dyn fmt::Display| ScanError::Failed(format!("OSError: {failure}"));
    remove_unlisted(out_dir, &names.store_name, None).map_err(|failure| failed(&failure))?;
    let name = base_name(&names.store_name, 0);
    let path = out_dir.join(&name);
    std::fs::hard_link(names.store_path(out_dir), &path).map_err(|failure| failed(&failure))?;
    let bytes = std::fs::read(&path).map_err(|failure| failed(&failure))?;
    let base = Part {
        name,
        size: bytes.len() as i64,
        sha256: sha256_hex(&bytes),
        rows: lines,
        rows_bytes,
        settled: None,
        members: None,
    };
    let head = Head {
        output: names.store_name.clone(),
        generation: 0,
        content: 0,
        stamps: PyValue::Dict(stamps.to_vec()),
        base,
        segments: Vec::new(),
        merge: MERGE_PLACE.to_owned(),
        family_keys: Vec::new(),
    };
    commit(sink, out_dir, &head).map_err(|failure| ScanError::Failed(failure.to_string()))?;
    Ok(head)
}

/// `unit_cache.store_stamps`: the store's stamps, the head's or the single file's inline header; None when neither reads.
fn store_stamps(
    out_dir: &Path,
    names: &ScanNames,
) -> std::result::Result<Option<Vec<(PyValue, PyValue)>>, ScanError> {
    match read_head(out_dir, &names.store_name) {
        Ok(Some(head)) => match head.stamps {
            PyValue::Dict(stamps) => Ok(Some(stamps)),
            _ => Ok(None),
        },
        Ok(None) => read_header(&names.store_path(out_dir)).map_err(header_failure),
        Err(ReadError::Head(_)) => Ok(None),
        Err(other) => Err(uncaught(other)),
    }
}

/// `unit_cache.store_miss_note`: why the store under `out_dir` does not load under `environment`, as the plan warns it: no store, a header that does not read, a stamp that moved, or a store stamped for another manifest; None when its stamps match.
pub fn store_miss_note(
    out_dir: &Path,
    names: &ScanNames,
    environment: &Stamp,
) -> std::result::Result<Option<String>, ScanError> {
    if !head_path(out_dir, &names.store_name).is_file() && !names.store_path(out_dir).is_file() {
        return Ok(Some(names.no_store.clone()));
    }
    let stamps = store_stamps(out_dir, names)?;
    let manifest = manifest_stamp(out_dir, names)?;
    Ok(miss_note(
        stamps.as_deref(),
        environment,
        CODE_LABEL,
        Some((&manifest, &names.manifest_moved)),
        &names.unreadable,
    ))
}

/// `unit_cache.rederiving_moved`: whether one of `labels` moved between a store's stamps and `environment`; a store whose stamps carry no lines counts as moved.
pub fn rederiving_moved(
    stamps: &[(PyValue, PyValue)],
    environment: &Stamp,
    labels: &[String],
) -> bool {
    let Some(recorded) = recorded_labels(get(stamps, "environment_lines")) else {
        return true;
    };
    let current = labels_of(&environment.lines);
    labels
        .iter()
        .any(|label| recorded.get(label) != current.get(label))
}

// --- the stage ------------------------------------------------------------------------------------

fn tagged(tag: &str, values: impl IntoIterator<Item = PyValue>) -> PyValue {
    PyValue::Tuple(std::iter::once(PyValue::str(tag)).chain(values).collect())
}

/// A failure as a stage value: `("raised", kind)`.
fn raised(failure: &ScanError) -> PyValue {
    tagged("raised", [PyValue::str(failure.kind())])
}

/// What a recorded or generated value Python reads gives where the Rust reader does not hold it: the stage fails, as the build would.
fn held<T>(
    outcome: std::result::Result<T, ScanError>,
) -> Result<std::result::Result<T, ScanError>> {
    match outcome {
        Err(ScanError::Unheld(why)) => error(format!("a value the reader does not hold: {why}")),
        other => Ok(other),
    }
}

fn sorted_texts(texts: &HashSet<String>) -> PyValue {
    let mut texts: Vec<&String> = texts.iter().collect();
    texts.sort();
    PyValue::Tuple(texts.into_iter().map(PyValue::str).collect())
}

fn opened_value(opened: &OpenStore) -> PyValue {
    tagged(
        "opened",
        [
            PyValue::Dict(opened.stamps.clone()),
            opened.head.as_ref().map_or(PyValue::None, Head::document),
        ],
    )
}

fn entries_of(value: &PyValue, what: &str) -> Result<Vec<(PyValue, PyValue)>> {
    match value {
        PyValue::Dict(entries) => Ok(entries.clone()),
        other => error(format!("{what} is {}, not a dict", other.kind())),
    }
}

fn stamp_of(value: &PyValue, what: &str) -> Result<Stamp> {
    let [stamp, lines, detail] = items(value, what)? else {
        return error(format!("{what} is not a stamp's value, lines and detail"));
    };
    let texts = |value: &PyValue| -> Result<Vec<String>> {
        items(value, what)?
            .iter()
            .map(|line| text(line, what).map(str::to_owned))
            .collect()
    };
    Ok(Stamp {
        value: text(stamp, what)?.to_owned(),
        lines: texts(lines)?,
        detail: items(detail, what)?
            .iter()
            .map(|pair| {
                let [label, lines] = items(pair, what)? else {
                    return error(format!("{what} holds a detail that is not a pair"));
                };
                Ok((text(label, what)?.to_owned(), texts(lines)?))
            })
            .collect::<Result<_>>()?,
        header: serde_json::Map::new(),
    })
}

/// The stage's blobs and the scratch directory its generated lines' corpora are set up in.
struct Workspace {
    blobs: PathBuf,
    scratch: PathBuf,
    cases: usize,
}

impl Workspace {
    /// A fresh directory holding `files`, each `(name, ("file", sha256))` or `(name, ("dir",))`.
    fn corpus(&mut self, files: &PyValue, what: &str) -> Result<PathBuf> {
        self.cases += 1;
        let directory = self.scratch.join(self.cases.to_string());
        let made = |failure: io::Error| WireError(format!("cannot set up {what}: {failure}"));
        std::fs::create_dir_all(&directory).map_err(made)?;
        for entry in items(files, what)? {
            let [name, held] = items(entry, what)? else {
                return error(format!("{what} holds an entry that is not a pair"));
            };
            let path = directory.join(text(name, what)?);
            match items(held, what)? {
                [kind] if text(kind, what)? == "dir" => {
                    std::fs::create_dir_all(&path).map_err(made)?;
                }
                [kind, digest] if text(kind, what)? == "file" => {
                    let digest = text(digest, what)?;
                    let source = self.blobs.join(digest);
                    let bytes = std::fs::read(&source).map_err(made)?;
                    if sha256_hex(&bytes) != digest {
                        return error(format!(
                            "{} does not hold the bytes it is named for",
                            source.display()
                        ));
                    }
                    if let Some(parent) = path.parent() {
                        std::fs::create_dir_all(parent).map_err(made)?;
                    }
                    std::fs::write(&path, bytes).map_err(made)?;
                }
                _ => return error(format!("{what} holds an entry of no known kind")),
            }
        }
        Ok(directory)
    }
}

/// What a scan gives as a generated line's value: the lines' count, the sha256 of their values, their bytes, and how it ended.
fn scanned_value(
    scan: impl Iterator<Item = std::result::Result<ScannedLine, ScanError>>,
) -> Result<PyValue> {
    let mut digest = ColumnDigest::new();
    let mut count = 0usize;
    let mut bytes = 0usize;
    let mut ending = tagged("done", []);
    for scanned in scan {
        match held(scanned)? {
            Ok(scanned) => {
                digest.add(&scanned.value());
                count += 1;
                bytes += scanned.line.len();
            }
            Err(failure) => {
                ending = raised(&failure);
                break;
            }
        }
    }
    Ok(tagged(
        "scanned",
        [
            count_value(count),
            PyValue::str(digest.hex()),
            count_value(bytes),
            ending,
        ],
    ))
}

fn marked_value(
    marked: impl Iterator<Item = std::result::Result<(usize, ParsedCachedUnit), ScanError>>,
) -> Result<PyValue> {
    let mut digest = ColumnDigest::new();
    let mut count = 0usize;
    let mut ending = tagged("done", []);
    for item in marked {
        match held(item)? {
            Ok((index, cached)) => {
                digest.add(&PyValue::Tuple(vec![count_value(index), cached.value()]));
                count += 1;
            }
            Err(failure) => {
                ending = raised(&failure);
                break;
            }
        }
    }
    Ok(tagged(
        "marked",
        [count_value(count), PyValue::str(digest.hex()), ending],
    ))
}

fn lines_value(opened: &OpenStore) -> Result<PyValue> {
    let mut digest = ColumnDigest::new();
    let mut count = 0usize;
    for line in opened.lines() {
        match held(line.map_err(store_failure))? {
            Ok(((anchor, fraction), line)) => {
                digest.add(&PyValue::Tuple(vec![
                    PyValue::Tuple(vec![PyValue::Int(anchor), PyValue::str(fraction)]),
                    PyValue::Bytes(line),
                ]));
                count += 1;
            }
            Err(failure) => return Ok(raised(&failure)),
        }
    }
    Ok(tagged(
        "lines",
        [count_value(count), PyValue::str(digest.hex())],
    ))
}

/// A store opened for a generated line, or the value the line takes when it does not open.
fn opened_for(
    directory: &Path,
    names: &ScanNames,
) -> Result<std::result::Result<OpenStore, PyValue>> {
    Ok(match held(open_store(directory, names))? {
        Ok(Some(opened)) => Ok(opened),
        Ok(None) => Err(tagged("none", [])),
        Err(failure) => Err(raised(&failure)),
    })
}

fn u32s(value: &PyValue, what: &str) -> Result<Vec<u32>> {
    items(value, what)?
        .iter()
        .map(|item| {
            u32::try_from(integer(item, what)?)
                .map_err(|_| WireError(format!("{what} holds an int that is not a u32")))
        })
        .collect()
}

fn texts_set(value: &PyValue, what: &str) -> Result<HashSet<String>> {
    items(value, what)?
        .iter()
        .map(|item| text(item, what).map(str::to_owned))
        .collect()
}

/// A generated line's value, by its form.
fn answer(
    input: &PyValue,
    names: &ScanNames,
    workspace: &mut Workspace,
    key: &str,
) -> Result<PyValue> {
    let fields = items(input, key)?;
    let Some((form, fields)) = fields.split_first() else {
        return error(format!("{key}: an empty input"));
    };
    match (text(form, key)?, fields) {
        ("open", [files]) => {
            let directory = workspace.corpus(files, key)?;
            Ok(match opened_for(&directory, names)? {
                Ok(opened) => {
                    let PyValue::Tuple(mut value) = opened_value(&opened) else {
                        unreachable!("a tagged value is a tuple");
                    };
                    value.push(lines_value(&opened)?);
                    PyValue::Tuple(value)
                }
                Err(value) => value,
            })
        }
        ("scan", [files, keys, parse, trusted]) => {
            let directory = workspace.corpus(files, key)?;
            let keys = items(keys, key)?;
            let mut column = Vec::with_capacity(keys.len() * KEY_BYTES);
            for held in keys {
                match held {
                    PyValue::Bytes(bytes) => column.extend_from_slice(bytes),
                    other => return error(format!("{key}: a key is {}", other.kind())),
                }
            }
            let named = KeyIndex::new(&column, KEY_BYTES, keys.len())
                .map_err(|failure| WireError(format!("{key}: {failure:?}")))?;
            let trusted = texts_set(trusted, key)?;
            let parse = matches!(parse, PyValue::Bool(true));
            Ok(match opened_for(&directory, names)? {
                Ok(opened) => scanned_value(scan_store(&opened, &named, &column, parse, &trusted))?,
                Err(value) => value,
            })
        }
        ("marked", [files, marked, trusted]) => {
            let directory = workspace.corpus(files, key)?;
            let marked = u32s(marked, key)?;
            let trusted = texts_set(trusted, key)?;
            Ok(match opened_for(&directory, names)? {
                Ok(opened) => marked_value(scan_marked(&opened, &marked, &trusted))?,
                Err(value) => value,
            })
        }
        ("trusted", [files, stamps]) => {
            let directory = workspace.corpus(files, key)?;
            Ok(
                match held(trusted_parts(&directory, &entries_of(stamps, key)?))? {
                    Ok(trusted) => tagged("trusted", [sorted_texts(&trusted)]),
                    Err(failure) => raised(&failure),
                },
            )
        }
        ("manifest", [files]) => {
            let directory = workspace.corpus(files, key)?;
            Ok(match held(manifest_stamp(&directory, names))? {
                Ok(stamp) => PyValue::str(stamp),
                Err(failure) => raised(&failure),
            })
        }
        ("miss-note", [files, environment]) => {
            let directory = workspace.corpus(files, key)?;
            let environment = stamp_of(environment, key)?;
            Ok(
                match held(store_miss_note(&directory, names, &environment))? {
                    Ok(note) => PyValue::optional_str(note.as_deref()),
                    Err(failure) => raised(&failure),
                },
            )
        }
        ("rederiving", [stamps, lines]) => {
            let environment = Stamp {
                value: String::new(),
                lines: items(lines, key)?
                    .iter()
                    .map(|line| text(line, key).map(str::to_owned))
                    .collect::<Result<_>>()?,
                detail: Vec::new(),
                header: serde_json::Map::new(),
            };
            Ok(PyValue::Bool(rederiving_moved(
                &entries_of(stamps, key)?,
                &environment,
                &names.rederiving,
            )))
        }
        ("locate", [files, classes, class_of, words]) => {
            let directory = workspace.corpus(files, key)?;
            let classes = items(classes, key)?;
            let class_of = u32s(class_of, key)?;
            let words = items(words, key)?;
            let mut wanted = WantedFragments::new();
            for (class, word) in class_of.iter().zip(words) {
                let class = text(&classes[*class as usize], key)?;
                let word = u64::from_str_radix(text(word, key)?, 16)
                    .map_err(|_| WireError(format!("{key}: a word is not hex")))?;
                wanted.add(class, word);
            }
            Ok(
                match held(locate_prior_fragments(&directory, &wanted, names))? {
                    Ok(located) => located.value(&wanted),
                    Err(failure) => raised(&failure),
                },
            )
        }
        ("adopt", [files, stamps, lines, rows_bytes]) => {
            let directory = workspace.corpus(files, key)?;
            let adopted = adopt_single_file(
                &directory,
                names,
                &entries_of(stamps, key)?,
                integer(lines, key)?,
                integer(rows_bytes, key)?,
                &Sink::new(None),
            );
            Ok(match held(adopted)? {
                Ok(head) => PyValue::Tuple(vec![head.document(), directory_value(&directory)?]),
                Err(failure) => raised(&failure),
            })
        }
        (form, _) => error(format!("{key}: no form {form} with these arguments")),
    }
}

/// A directory's files as the `scan` stage records them: each name, sorted, with the sha256 of its bytes.
fn directory_value(directory: &Path) -> Result<PyValue> {
    let fail =
        |failure: io::Error| WireError(format!("cannot list {}: {failure}", directory.display()));
    let mut files = Vec::new();
    for entry in std::fs::read_dir(directory).map_err(fail)? {
        let entry = entry.map_err(fail)?;
        if entry.file_type().map_err(fail)?.is_file() {
            let bytes = std::fs::read(entry.path()).map_err(fail)?;
            files.push((
                entry.file_name().to_string_lossy().into_owned(),
                sha256_hex(&bytes),
            ));
        }
    }
    files.sort();
    Ok(PyValue::Tuple(
        files
            .into_iter()
            .map(|(name, digest)| PyValue::Tuple(vec![PyValue::str(name), PyValue::str(digest)]))
            .collect(),
    ))
}

/// The stage file's lines: the `unit:<ordinal>` ordinals it asks for, whether the build's run recorded any line, and each generated line's key and input.
pub(crate) struct StageLines {
    pub(crate) units: HashSet<usize>,
    pub(crate) ran: bool,
    pub(crate) generated: Vec<(String, PyValue)>,
}

pub(crate) fn stage_lines(stage_file: &Path) -> Result<StageLines> {
    let read =
        |failure: io::Error| WireError(format!("cannot read {}: {failure}", stage_file.display()));
    let mut lines = StageLines {
        units: HashSet::new(),
        ran: false,
        generated: Vec::new(),
    };
    for line in BufReader::new(File::open(stage_file).map_err(read)?).lines() {
        let line = line.map_err(read)?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| WireError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return error(format!("a stage line has no key: {line:.200}"));
        };
        if record["from"] == "generated" {
            let input = PyValue::from_typed(&record["input"])
                .map_err(|failure| WireError(format!("{key}: {failure}")))?;
            lines.generated.push((key.to_owned(), input));
            continue;
        }
        lines.ran = true;
        if let Some(ordinal) = key.strip_prefix(UNIT_PREFIX) {
            lines.units.insert(
                ordinal
                    .parse()
                    .map_err(|_| WireError(format!("the key {key} names no ordinal")))?,
            );
        }
    }
    Ok(lines)
}

/// The plan's reads of the prior store, from its opening to its end, as `_build_m1` makes them, written as the `scan` stage's run lines: the store's opening; its miss note where the plan warns one; each scanned line, a `unit:<index>` line for each index in `units`, the column over them and how the scan ended; the walk's placements of the records the scan found without an address, the second scan's lines and each placement it looked up; and, once the store read to its end, whether a re-deriving stamp moved and, for a single file, the head the plan puts it under, in a scratch copy of the store.
fn plan_reads(
    input: &BuildInput,
    names: &ScanNames,
    prior: &Path,
    (named, column): (&KeyIndex, &[u8]),
    units: &HashSet<usize>,
    scratch: &Path,
    out: &mut impl Write,
) -> Result<()> {
    let opened = match held(open_store(prior, names))? {
        Ok(Some(opened)) => opened,
        Ok(None) => return stage_line(out, "open", &tagged("none", [])),
        Err(ScanError::Unreadable(_)) => return Ok(()),
        Err(failure) => return error(format!("the store's opening failed: {failure}")),
    };
    stage_line(out, "open", &opened_value(&opened))?;
    let environment = &input.environment;
    let note = || -> Result<PyValue> {
        let note = held(store_miss_note(prior, names, environment))?
            .map_err(|failure| WireError(format!("the miss note failed: {failure}")))?;
        Ok(PyValue::optional_str(note.as_deref()))
    };
    if !is_str(opened.stamp("format"), &names.store_format) {
        return stage_line(out, "miss-note", &note()?);
    }
    let manifest = held(manifest_stamp(prior, names))?
        .map_err(|failure| WireError(format!("the manifest stamp failed: {failure}")))?;
    let manifest_holds = is_str(opened.stamp("manifest_sha256"), &manifest);
    let trusted = if manifest_holds {
        held(trusted_parts(prior, &opened.stamps))?
            .map_err(|failure| WireError(format!("the trusted parts failed: {failure}")))?
    } else {
        HashSet::new()
    };
    let loadable = !input.recompute_all
        && manifest_holds
        && is_str(opened.stamp("environment"), &environment.value);
    if !loadable && !input.recompute_all {
        stage_line(out, "miss-note", &note()?)?;
    }
    let mut digest = ColumnDigest::new();
    let mut wanted = WantedFragments::new();
    let mut addressless: Vec<(u32, u32)> = Vec::new();
    let (mut lines, mut rows_bytes) = (0usize, 0usize);
    let mut ending = None;
    for scanned in scan_store(&opened, named, column, loadable, &trusted) {
        let scanned = match held(scanned)? {
            Ok(scanned) => scanned,
            Err(failure) => {
                ending = Some(failure);
                break;
            }
        };
        let value = scanned.value();
        if units.contains(&scanned.index) {
            stage_line(out, &format!("{UNIT_PREFIX}{}", scanned.index), &value)?;
        }
        digest.add(&value);
        lines += 1;
        rows_bytes += scanned.line.len();
        if let (Some(ordinal), Some(cached)) = (scanned.ordinal, &scanned.cached)
            && cached.located().is_none()
            && let Some(word) = id_word(&cached.prior_id)
        {
            addressless.push((scanned.index as u32, ordinal));
            wanted.add(&cached.prior_class, word);
        }
    }
    digest.write(out, "value")?;
    let scan = PyValue::Tuple(vec![PyValue::Bool(loadable), sorted_texts(&trusted)]);
    let ended = |ending: &Option<ScanError>| match ending {
        Some(failure) => raised(failure),
        None => tagged("done", [count_value(lines), count_value(rows_bytes)]),
    };
    let PyValue::Tuple(mut scan) = scan else {
        unreachable!("the scan's value is a tuple");
    };
    scan.push(ended(&ending));
    stage_line(out, "scan", &PyValue::Tuple(scan))?;
    match ending {
        Some(ScanError::Unreadable(_)) => return Ok(()),
        Some(failure) => return error(format!("the scan failed: {failure}")),
        None => {}
    }
    if !wanted.is_empty() {
        let located = held(locate_prior_fragments(prior, &wanted, names))?
            .map_err(|failure| WireError(format!("the walk failed: {failure}")))?;
        stage_line(out, "locate", &located.value(&wanted))?;
        let again = match held(open_store(prior, names))? {
            Ok(Some(again)) => again,
            Ok(None) | Err(ScanError::Unreadable(_)) => return Ok(()),
            Err(failure) => return error(format!("the store's second opening failed: {failure}")),
        };
        let marked: Vec<u32> = addressless.iter().map(|&(line, _)| line).collect();
        let mut parsed = Vec::new();
        let mut ending = None;
        for item in scan_marked(&again, &marked, &trusted) {
            match held(item)? {
                Ok((index, cached)) => {
                    stage_line(
                        out,
                        &format!("marked:{index}"),
                        &PyValue::Tuple(vec![count_value(index), cached.value()]),
                    )?;
                    parsed.push((index, cached));
                }
                Err(failure) => {
                    ending = Some(failure);
                    break;
                }
            }
        }
        let marked_ending = match &ending {
            Some(failure) => raised(failure),
            None => tagged("done", [count_value(parsed.len())]),
        };
        stage_line(
            out,
            "marked",
            &PyValue::Tuple(vec![
                PyValue::Tuple(
                    marked
                        .iter()
                        .map(|&line| PyValue::Int(i64::from(line)))
                        .collect(),
                ),
                sorted_texts(&trusted),
                marked_ending,
            ]),
        )?;
        for ((index, cached), &(line, _)) in parsed.iter().zip(&addressless) {
            if *index != line as usize {
                return Ok(());
            }
            let found = located.find(&cached.prior_class, &cached.prior_id);
            let asked = json::dumps(&PyValue::List(vec![
                PyValue::str(cached.prior_class.as_str()),
                PyValue::str(cached.prior_id.as_str()),
            ]))
            .map_err(|failure| WireError(failure.0))?;
            stage_line(
                out,
                &format!("find:{asked}"),
                &found.map_or(PyValue::None, |found| found.value()),
            )?;
        }
        match ending {
            Some(ScanError::Unreadable(_)) => return Ok(()),
            Some(failure) => return error(format!("the second scan failed: {failure}")),
            None => {}
        }
    }
    stage_line(
        out,
        "rederiving",
        &PyValue::Bool(rederiving_moved(
            &opened.stamps,
            environment,
            &names.rederiving,
        )),
    )?;
    if opened.head.is_none() {
        let _ = std::fs::remove_dir_all(scratch);
        std::fs::create_dir_all(scratch).map_err(|failure| {
            WireError(format!("cannot make the adoption's scratch: {failure}"))
        })?;
        std::fs::copy(names.store_path(prior), names.store_path(scratch))
            .map_err(|failure| WireError(format!("cannot copy the single file: {failure}")))?;
        let head = held(adopt_single_file(
            scratch,
            names,
            &opened.stamps,
            lines as i64,
            rows_bytes as i64,
            &Sink::new(None),
        ))?
        .map_err(|failure| WireError(format!("the adoption failed: {failure}")))?;
        stage_line(out, "adopt", &head.document())?;
    }
    Ok(())
}

/// The `scan` stage: when the dump's build reached the plan's reads of the prior store, run the build's load ([`super::load_corpus`]) over the audit its build input names, read through the descriptors the process inherits, and the prior corpus of the dump, index the units' input keys and make the plan's reads of the dump's prior store as `_build_m1` makes them ([`plan_reads`]); then answer each generated line by its form over a corpus set up from the blobs its input names in [`STAGE_BLOBS`] beside the stage file: `open` ([`open_store`] and the store's lines), `scan` ([`scan_store`] over the keys it names), `marked` ([`scan_marked`]), `trusted` ([`trusted_parts`]), `manifest` ([`manifest_stamp`]), `miss-note` ([`store_miss_note`]), `rederiving` ([`rederiving_moved`]), `locate` ([`locate_prior_fragments`]) and `adopt` ([`adopt_single_file`], with the directory's files after it).
pub fn stage_scan(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let names = ScanNames::of(&input)?;
    let lines = stage_lines(stage_file)?;
    let directory = stage_file.parent().unwrap_or_else(|| Path::new("."));
    let scratch = std::env::temp_dir().join(format!("ams-review-scan-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&scratch);
    let outcome = (|| -> Result<()> {
        if lines.ran {
            let files = input.held_audit()?;
            let prior = dump_prior(stage_file)?;
            let loaded = super::load_corpus(&input, &files, &prior, &mut Vec::new())
                .map_err(|failure| WireError(format!("the load failed: {failure}")))?;
            let column = loaded.input_keys;
            let named = KeyIndex::new(&column, KEY_BYTES, loaded.workload.table.n)
                .map_err(|failure| WireError(format!("the input keys: {failure:?}")))?;
            plan_reads(
                &input,
                &names,
                &prior,
                (&named, &column),
                &lines.units,
                &scratch.join("adopt"),
                out,
            )?;
        }
        let mut workspace = Workspace {
            blobs: directory.join(STAGE_BLOBS),
            scratch: scratch.join("generated"),
            cases: 0,
        };
        for (key, input) in &lines.generated {
            let value = answer(input, &names, &mut workspace, key)?;
            stage_line(out, key, &value)?;
        }
        Ok(())
    })();
    let _ = std::fs::remove_dir_all(&scratch);
    outcome
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wanted(entries: &[(&str, u64)]) -> WantedFragments {
        let mut wanted = WantedFragments::new();
        for &(class, word) in entries {
            wanted.add(class, word);
        }
        wanted
    }

    #[test]
    fn the_located_columns_sort_by_word_and_find_the_first_placed_copy() {
        let mut located = LocatedFragments::new(&wanted(&[("b", 9), ("a", 3), ("a", 9)]));
        assert_eq!(located.words, [3, 9, 9]);
        assert_eq!(located.classes, ["b", "a"]);
        assert_eq!(located.class_of, [1, 0, 1]);
        let mut part_ids = HashMap::new();
        let unit_id = "u-11111111112";
        let word = id_word(unit_id).expect("a unit id");
        let mut located_word = LocatedFragments::new(&wanted(&[("a", word)]));
        let slot = LiveSlot {
            unit_id: unit_id.to_owned(),
            part: "units/a.json".to_owned(),
            start: 1,
            length: 5,
            inflated: 0,
        };
        let fragment = PyValue::Dict(vec![(
            PyValue::str("content_key"),
            PyValue::str("ab".repeat(32)),
        )]);
        located_word
            .place("a", &slot, &fragment, &mut part_ids)
            .expect("placed");
        assert_eq!(located_word.found(), 1);
        let found = located_word.find("a", unit_id).expect("found");
        assert_eq!(
            (found.part.as_str(), found.start, found.length),
            ("units/a.json", 1, 5)
        );
        assert_eq!(located_word.find("b", unit_id), None);
        let upper = PyValue::Dict(vec![(
            PyValue::str("content_key"),
            PyValue::str("AB".repeat(32)),
        )]);
        located_word
            .place("a", &slot, &upper, &mut part_ids)
            .expect("placed");
        assert_eq!(located_word.found(), 0);
        located.part[0] = 0;
        assert_eq!(located.find("a", "not an id"), None);
    }

    #[test]
    fn an_overflow_part_is_numbered_from_three_decimal_digits() {
        assert!(is_overflow("c", "units/c.x000.json"));
        assert!(is_overflow("c", "units/c.x1234.json"));
        assert!(is_overflow("c", "units/c.x٠١٢.json"));
        assert!(!is_overflow("c", "units/c.x00.json"));
        assert!(!is_overflow("c", "units/c.000.json"));
        assert!(!is_overflow("c", "units/c.x000.json.partial"));
    }

    #[test]
    fn a_part_walks_its_elements_with_their_offsets() {
        let scratch =
            std::env::temp_dir().join(format!("ams-review-scan-walk-{}", std::process::id()));
        std::fs::create_dir_all(&scratch).expect("scratch");
        let path = scratch.join("part.json");
        std::fs::write(&path, b"[\n {\"id\": 1}\n,\n {\"id\": 2} {\"id\": 3}\n]\n").expect("part");
        let mut seen = Vec::new();
        walk_part(&path, &mut |start, length, fragment| {
            seen.push((start, length, lookup(fragment, "id").cloned()));
            Ok(())
        })
        .expect("walked");
        assert_eq!(
            seen,
            [
                (3, 9, Some(PyValue::Int(1))),
                (16, 9, Some(PyValue::Int(2))),
                (26, 9, Some(PyValue::Int(3))),
            ]
        );
        std::fs::write(&path, b"[{\"id\": \"\xc3\xa9\"}]").expect("part");
        let refused = walk_part(&path, &mut |_, _, _| Ok(()));
        assert!(matches!(refused, Err(Thrown::Value(why)) if why.contains("not ASCII")));
        let _ = std::fs::remove_dir_all(&scratch);
    }

    #[test]
    fn a_rederiving_label_that_moved_or_is_missing_counts() {
        let environment = Stamp {
            value: String::new(),
            lines: vec!["spec_structure\ta".to_owned(), "other\tb".to_owned()],
            detail: Vec::new(),
            header: serde_json::Map::new(),
        };
        let labels = ["spec_structure".to_owned()];
        let stamps = |lines: &[&str]| {
            vec![(
                PyValue::str("environment_lines"),
                PyValue::List(lines.iter().map(|line| PyValue::str(*line)).collect()),
            )]
        };
        assert!(!rederiving_moved(
            &stamps(&["spec_structure\ta", "other\tc"]),
            &environment,
            &labels
        ));
        assert!(rederiving_moved(
            &stamps(&["spec_structure\tz"]),
            &environment,
            &labels
        ));
        assert!(rederiving_moved(
            &stamps(&["other\tb"]),
            &environment,
            &labels
        ));
        assert!(rederiving_moved(&[], &environment, &labels));
    }
}
