//! The plan's reads of the prior corpus: the unit store opened ([`open_store`]), its materialized lines read in order as `unit_index.store_lines` merges its base and segments ([`OpenStore::lines`]), each matched to a unit of this build by its input key and, where the plan may reuse it, parsed, the lines read on one thread and parsed on several and handed back in the store's order ([`scan_store_threaded`]), or read flat into each batch's buffers where they can be ([`scan_store_flat`]), and the lines the walk placed parsed again alone ([`scan_marked`]); the parts a record's address is trusted in ([`trusted_parts`]) and the manifest identity the store is stamped with ([`manifest_stamp`], [`manifest_identity`]); the records the scan found without an address ([`WantedFragments`]) placed by a walk over the prior corpus's live fragments ([`locate_prior_fragments`], [`LocatedFragments`]), read through the live-fragment iterator from the store's addresses, the index's ids or the parts themselves; the single file a store written before the segment format leaves put under a head ([`adopt_single_file`]); and why a store does not load ([`store_miss_note`]) and whether its stamps say every record is re-derived ([`rederiving_moved`]).
//!
//! What a read raises is taken as the plan takes it ([`ScanError`]): a store that does not read, which the plan answers by restarting over an emptied store; another exception, or a value the reader does not hold, either of which fails the build. The walk keeps what it placed before an `OSError` or a `ValueError` stops it, and fails on anything else.

use std::collections::{HashMap, HashSet};
use std::fmt;
use std::fs::File;
use std::io::{self, BufRead, BufReader};
use std::os::unix::fs::FileExt;
use std::path::{Path, PathBuf};

use super::columns::{KeyIndex, sorted_by_word};
use super::gzip::{GzipError, GzipReader, inflate_fragment};
use super::input::{BuildInput, Stamp};
use super::prior::holds_unit;
use super::records::{
    Cached, CachedRecords, ParsedCachedUnit, RecordError, hashable, id_word, line_address,
    line_class, line_id, named_ordinal, parse_line, truthy,
};
use super::rowdelta::{
    Head, Key, MERGE_PLACE, Part, PartReader, Place, ReadError, base_name, base_rows, commit,
    head_path, keyed_segment_rows, merge_keyed, merge_placed, open_parts, py_str, read_head,
    remove_unlisted, segment_rows,
};
use super::shards::overflow_index;
use super::signatures::{
    StoreError, get, int_equal, is_str, labels_of, miss_note, read_header, recorded_labels,
};
use super::sink::Sink;
use super::store::PriorFragment;
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
    /// Another exception, by its class and text, which no caller in the plan catches, so it fails the build.
    Failed(String),
    /// A value the reader does not hold, which fails the build.
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

impl StoreBaseLines<'_> {
    /// The next record line appended to `buffer`, as [`Iterator::next`] gives it: Some(Ok) with the line appended, None at the end, and the failure with nothing appended.
    fn next_into(&mut self, buffer: &mut Vec<u8>) -> Option<std::result::Result<(), ReadError>> {
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
        let mark = buffer.len();
        match self.reader.read_until(b'\n', buffer) {
            Ok(0) => {
                self.done = true;
                None
            }
            Ok(_) => Some(Ok(())),
            Err(failure) => {
                buffer.truncate(mark);
                self.done = true;
                Some(Err(ReadError::of_io(&failure)))
            }
        }
    }
}

impl Iterator for StoreBaseLines<'_> {
    type Item = std::result::Result<Vec<u8>, ReadError>;

    fn next(&mut self) -> Option<Self::Item> {
        let mut line = Vec::new();
        self.next_into(&mut line).map(|read| read.map(|()| line))
    }
}

/// A unit store's materialized record lines as the threaded scan reads them: the base's straight into the caller's buffer for a store with no head or no segment, else [`store_lines`]' merge of the base and the segments.
enum LineSource<'a> {
    Base(StoreBaseLines<'a>),
    Merged(Lines<'a, (Place, Vec<u8>)>),
}

impl LineSource<'_> {
    /// The next line appended to `buffer`, as [`StoreBaseLines::next_into`] appends one.
    fn next_into(&mut self, buffer: &mut Vec<u8>) -> Option<std::result::Result<(), ReadError>> {
        match self {
            Self::Base(lines) => lines.next_into(buffer),
            Self::Merged(lines) => lines
                .next()
                .map(|read| read.map(|(_, line)| buffer.extend_from_slice(&line))),
        }
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

    /// The same lines as [`OpenStore::lines`], as the threaded scan reads them.
    fn line_source(&self) -> LineSource<'_> {
        match &self.head {
            Some(head) if !head.segments.is_empty() => LineSource::Merged(self.lines()),
            _ => LineSource::Base(StoreBaseLines::new(&self.files[0])),
        }
    }
}

/// `unit_cache.open_store`: the store under `out_dir` opened, every part its head lists checked against its size, or the single file a store written before the segment format leaves read by its inline header; None when there is neither. A store that exists and does not open is [`ScanError::Unreadable`]; a head or a part that does not read for another reason than the ones `open_store` turns into `StoreUnreadable` fails.
pub fn open_store(
    out_dir: &Path,
    names: &ScanNames,
) -> std::result::Result<Option<OpenStore>, ScanError> {
    open_store_checked(out_dir, names, false)
}

/// `unit_cache.open_store(out_dir, sha256=…)`: [`open_store`], every part its head lists checked against its sha256 too with `sha256`, as a reader that merges every byte checks it.
pub fn open_store_checked(
    out_dir: &Path,
    names: &ScanNames,
    sha256: bool,
) -> std::result::Result<Option<OpenStore>, ScanError> {
    let output = names.store_name.clone();
    match read_head(out_dir, &output) {
        Ok(Some(head)) => {
            let files = match open_parts(out_dir, &head, sha256) {
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

/// The parts the store's stamps record whose size on disk is the recorded one. Recorded parts that are a list or a `str` trust none, and fail, as `recorded[part]` raises outside any `except`, once a part they name is on disk; a name that is not a `str` fails, as do recorded parts of another type.
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

/// One line of the prior store as the plan scans it: its place in the materialized order, the ordinal whose input key it names, the record parsed when the scan parses named lines, and the raw line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ScannedLine {
    pub index: usize,
    pub ordinal: Option<u32>,
    pub cached: Option<ParsedCachedUnit>,
    pub line: Vec<u8>,
}

/// How many lines the threaded scan hands a parse thread at once.
const SCAN_BATCH_LINES: usize = 512;

/// A run of lines the reading thread hands the parse threads: its place among the batches, the index of its first line, its lines one after another in one buffer with each one's end, and the failure that ended the read after them.
struct ReadBatch {
    sequence: usize,
    first: usize,
    bytes: Vec<u8>,
    ends: Vec<usize>,
    failure: Option<ReadError>,
}

impl ReadBatch {
    /// The batch's lines in order.
    fn lines(&self) -> impl Iterator<Item = &[u8]> {
        let mut start = 0;
        self.ends.iter().map(move |&end| {
            let line = &self.bytes[start..end];
            start = end;
            line
        })
    }
}

/// A batch as the parse threads give it back: what the work made of its lines, and the failure that ends the scan after them.
struct WorkedBatch<B> {
    sequence: usize,
    worked: B,
    failure: Option<ScanError>,
}

/// The threads of a scan of an open store: the store's lines read and merged in order on one thread a batch at a time, each batch given to `work` on one of `threads` threads, and what `work` made of each batch handed to `apply` in the batches' order on the calling thread. `work` gives what it made of the batch's lines up to the first that does not parse, beside the failure that ends the scan after them, the read's or the parse's; the first failure, the scan's or `apply`'s, ends it, a panic in a parse thread among them. At most two batches a parse thread are in flight between the read and `apply`.
fn scan_batches<B, E>(
    opened: &OpenStore,
    threads: usize,
    work: impl Fn(ReadBatch) -> (B, Option<ScanError>) + Sync,
    mut apply: impl FnMut(B) -> std::result::Result<(), E>,
) -> std::result::Result<(), E>
where
    B: Send + Default,
    E: From<ScanError>,
{
    use std::panic::{AssertUnwindSafe, catch_unwind};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Mutex, mpsc};

    let threads = threads.max(1);
    let stopped = AtomicBool::new(false);
    let in_flight = 2 * threads;
    let (tokens, token_source) = mpsc::sync_channel::<()>(in_flight);
    for _ in 0..in_flight {
        let _ = tokens.send(());
    }
    let (batches, batch_source) = mpsc::channel::<ReadBatch>();
    let batch_source = Mutex::new(batch_source);
    let (worked, worked_source) = mpsc::channel::<WorkedBatch<B>>();
    std::thread::scope(|scope| {
        let stopped = &stopped;
        std::thread::Builder::new()
            .name("store-scan-read".to_owned())
            .spawn_scoped(scope, move || {
                let mut lines = opened.line_source();
                let mut first = 0;
                let mut reserved = 0;
                for sequence in 0.. {
                    if token_source.recv().is_err() || stopped.load(Ordering::Relaxed) {
                        return;
                    }
                    let mut batch = ReadBatch {
                        sequence,
                        first,
                        bytes: Vec::with_capacity(reserved),
                        ends: Vec::with_capacity(SCAN_BATCH_LINES),
                        failure: None,
                    };
                    let mut ended = false;
                    while batch.ends.len() < SCAN_BATCH_LINES {
                        match lines.next_into(&mut batch.bytes) {
                            None => {
                                ended = true;
                                break;
                            }
                            Some(Err(failure)) => {
                                batch.failure = Some(failure);
                                ended = true;
                                break;
                            }
                            Some(Ok(())) => batch.ends.push(batch.bytes.len()),
                        }
                    }
                    first += batch.ends.len();
                    reserved = batch.bytes.len() + batch.bytes.len() / 8;
                    if (!batch.ends.is_empty() || batch.failure.is_some())
                        && batches.send(batch).is_err()
                    {
                        return;
                    }
                    if ended {
                        return;
                    }
                }
            })
            .expect("the store scan's reading thread starts");
        for _ in 0..threads {
            let worked = worked.clone();
            let batch_source = &batch_source;
            let work = &work;
            std::thread::Builder::new()
                .name("store-scan-parse".to_owned())
                .spawn_scoped(scope, move || {
                    loop {
                        let next = match batch_source.lock() {
                            Ok(source) => source.recv(),
                            Err(_) => return,
                        };
                        let Ok(batch) = next else {
                            return;
                        };
                        if stopped.load(Ordering::Relaxed) {
                            return;
                        }
                        let sequence = batch.sequence;
                        // A batch lost to a panic would leave `apply` waiting on its sequence forever, so the panic comes back in its place as the scan's failure.
                        let (made, failure) = catch_unwind(AssertUnwindSafe(|| work(batch)))
                            .unwrap_or_else(|panic| {
                                (
                                    B::default(),
                                    Some(ScanError::Failed(format!(
                                        "a store scan's parse thread panicked: {}",
                                        crate::pool::panic_message(&*panic)
                                    ))),
                                )
                            });
                        let out = WorkedBatch {
                            sequence,
                            worked: made,
                            failure,
                        };
                        if worked.send(out).is_err() {
                            return;
                        }
                    }
                })
                .expect("a store scan's parse thread starts");
        }
        drop(worked);
        let mut waiting: std::collections::BTreeMap<usize, WorkedBatch<B>> =
            std::collections::BTreeMap::new();
        let mut following = 0;
        let outcome = (|| {
            for batch in worked_source {
                waiting.insert(batch.sequence, batch);
                while let Some(batch) = waiting.remove(&following) {
                    following += 1;
                    apply(batch.worked)?;
                    if let Some(failure) = batch.failure {
                        return Err(failure.into());
                    }
                    let _ = tokens.send(());
                }
            }
            Ok(())
        })();
        stopped.store(true, Ordering::Relaxed);
        drop(tokens);
        outcome
    })
}

/// The scan of an open store on threads: every line of it in its materialized order, each matched to this build's units by input key over `named`, the index of the input-key column `column`, and, with `parse`, a named line parsed with its address kept only in a `trusted` part. The store's lines are read and merged in order on one thread, matched and parsed a batch at a time on `threads` threads, each line with what `beside` computes from it there, and handed to `apply` in the store's materialized order on the calling thread; the first failure, the scan's or `apply`'s, ends it, a panic in a parse thread among them ([`scan_batches`]). At most two batches a parse thread are in flight between the read and `apply`.
#[allow(clippy::too_many_arguments)]
pub fn scan_store_threaded<T, E>(
    opened: &OpenStore,
    named: &KeyIndex,
    column: &[u8],
    parse: bool,
    trusted: &HashSet<String>,
    threads: usize,
    beside: impl Fn(&ScannedLine) -> T + Sync,
    mut apply: impl FnMut(ScannedLine, T) -> std::result::Result<(), E>,
) -> std::result::Result<(), E>
where
    T: Send,
    E: From<ScanError>,
{
    scan_batches(
        opened,
        threads,
        |batch: ReadBatch| {
            let mut failure = None;
            let mut scanned = Vec::with_capacity(batch.ends.len());
            for (offset, line) in batch.lines().enumerate() {
                let ordinal = named_ordinal(named, column, line);
                let mut cached = None;
                if parse && ordinal.is_some() {
                    match parse_line(line, trusted) {
                        Ok(record) => cached = Some(record),
                        Err(fault) => {
                            failure = Some(record_failure(fault));
                            break;
                        }
                    }
                }
                let line = ScannedLine {
                    index: batch.first + offset,
                    ordinal,
                    cached,
                    line: line.to_vec(),
                };
                let extra = beside(&line);
                scanned.push((line, extra));
            }
            (
                scanned,
                failure.or_else(|| batch.failure.map(store_failure)),
            )
        },
        |scanned: Vec<(ScannedLine, T)>| {
            for (line, extra) in scanned {
                apply(line, extra)?;
            }
            Ok(())
        },
    )
}

/// What a line of the flat scan holds of its record: none, a record read flat, or a record parsed, by its index among the batch's.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum HeldRecord {
    None,
    Flat(usize),
    Parsed(usize),
}

/// One line of a flat scan's batch: its place in the materialized order, the ordinal whose input key it names, its length in bytes, and its record.
#[derive(Clone, Copy, Debug)]
struct ScannedEntry {
    index: usize,
    ordinal: Option<u32>,
    length: usize,
    record: HeldRecord,
}

/// A batch as the flat scan's parse threads give it back: its lines' entries beside what `beside` gave for each, the records read flat, and the records parsed where a line is not read flat.
struct ScannedBatch<T> {
    entries: Vec<ScannedEntry>,
    extras: Vec<T>,
    records: CachedRecords,
    parsed: Vec<ParsedCachedUnit>,
}

impl<T> Default for ScannedBatch<T> {
    fn default() -> Self {
        Self {
            entries: Vec::new(),
            extras: Vec::new(),
            records: CachedRecords::new(),
            parsed: Vec::new(),
        }
    }
}

/// The record a batch's entry names, borrowed from the batch's records.
fn scanned_record<'a>(
    records: &'a CachedRecords,
    parsed: &'a [ParsedCachedUnit],
    entry: &ScannedEntry,
) -> ScannedRecord<'a> {
    ScannedRecord {
        index: entry.index,
        ordinal: entry.ordinal,
        length: entry.length,
        cached: match entry.record {
            HeldRecord::None => None,
            HeldRecord::Flat(at) => Some(Cached::Flat(records.get(at))),
            HeldRecord::Parsed(at) => Some(Cached::Parsed(&parsed[at])),
        },
    }
}

/// One line of the prior store as the flat scan gives it: its place in the materialized order, the ordinal whose input key it names, its length in bytes, and its record when the scan parses named lines, read flat where it can be ([`CachedRecords`]).
#[derive(Clone, Copy, Debug)]
pub struct ScannedRecord<'a> {
    pub index: usize,
    pub ordinal: Option<u32>,
    pub length: usize,
    pub cached: Option<Cached<'a>>,
}

/// [`scan_store_threaded`] with each named line's record read flat where it can be and parsed where it cannot, held in its batch's buffers: `beside` is given each line's record and its bytes on the parse thread, and `apply` each line's record, borrowed from its batch, in the store's materialized order. A line is refused where [`parse_line`] refuses it, with the same failure.
#[allow(clippy::too_many_arguments)]
pub fn scan_store_flat<T, E>(
    opened: &OpenStore,
    named: &KeyIndex,
    column: &[u8],
    parse: bool,
    trusted: &HashSet<String>,
    threads: usize,
    beside: impl Fn(&ScannedRecord<'_>, &[u8]) -> T + Sync,
    mut apply: impl FnMut(&ScannedRecord<'_>, T) -> std::result::Result<(), E>,
) -> std::result::Result<(), E>
where
    T: Send,
    E: From<ScanError>,
{
    scan_batches(
        opened,
        threads,
        |batch: ReadBatch| {
            let mut failure = None;
            let mut out = ScannedBatch {
                entries: Vec::with_capacity(batch.ends.len()),
                extras: Vec::with_capacity(batch.ends.len()),
                records: if parse {
                    CachedRecords::with_capacity(batch.ends.len(), batch.bytes.len())
                } else {
                    CachedRecords::new()
                },
                parsed: Vec::new(),
            };
            for (offset, line) in batch.lines().enumerate() {
                let ordinal = named_ordinal(named, column, line);
                let mut record = HeldRecord::None;
                if parse && ordinal.is_some() {
                    record = match out.records.read(line, trusted) {
                        Some(at) => HeldRecord::Flat(at),
                        None => match parse_line(line, trusted) {
                            Ok(parsed) => {
                                out.parsed.push(parsed);
                                HeldRecord::Parsed(out.parsed.len() - 1)
                            }
                            Err(fault) => {
                                failure = Some(record_failure(fault));
                                break;
                            }
                        },
                    };
                }
                let entry = ScannedEntry {
                    index: batch.first + offset,
                    ordinal,
                    length: line.len(),
                    record,
                };
                let extra = beside(&scanned_record(&out.records, &out.parsed, &entry), line);
                out.entries.push(entry);
                out.extras.push(extra);
            }
            (out, failure.or_else(|| batch.failure.map(store_failure)))
        },
        |batch: ScannedBatch<T>| {
            let ScannedBatch {
                entries,
                extras,
                records,
                parsed,
            } = batch;
            for (entry, extra) in entries.iter().zip(extras) {
                apply(&scanned_record(&records, &parsed, entry), extra)?;
            }
            Ok(())
        },
    )
}

/// The lines of an open store at the materialized places `marked` names, ascending, each with its place and parsed as the scan parses it ([`scan_store_threaded`]). A line is read before the first place is looked for, as the loop's first iteration reads one before it can stop.
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

/// The prior records the scan could not place by address, as columns in scan order: each one's class, an index into `classes`, and its id word.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct WantedFragments {
    pub classes: Vec<String>,
    class_ids: ams_m1_kernel::hash::HashMap<String, u32>,
    pub class_of: Vec<u32>,
    pub words: Vec<u64>,
}

impl WantedFragments {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add(&mut self, prior_class: &str, word: u64) {
        let found = match self.class_ids.get(prior_class) {
            Some(&found) => found,
            None => {
                let next = self.classes.len() as u32;
                self.class_ids.insert(prior_class.to_owned(), next);
                self.classes.push(prior_class.to_owned());
                next
            }
        };
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

/// Where the walk found the wanted records' fragments, as columns over the wanted records sorted by id word: the word and the class index, and for each found fragment its part (an index into `parts`, plus one; 0 when not found), start, length on disk, inflated length and 32-byte content key.
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

    /// The fragment the walk found for the record of this class and id, stamped with its content key, or None.
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
pub(crate) fn class_shards(meta: &PyValue) -> std::result::Result<Vec<String>, Thrown> {
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
pub(crate) fn index_key(line: &[u8]) -> std::result::Result<Key, ReadError> {
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
    pub(crate) head: Option<Head>,
    pub(crate) files: Vec<File>,
}

impl OpenIndex {
    /// An index opened by a writer that checked its parts itself (`unit_index._checked`): its header, its head and its parts, base first.
    pub(crate) fn new(header: Vec<(PyValue, PyValue)>, head: Head, files: Vec<File>) -> Self {
        Self {
            header,
            head: Some(head),
            files,
        }
    }

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

/// Where the prior shards hold the wanted records' fragments, with the content key each carries, read through the live-fragment iterator over the classes the records name. A corpus the iterator refuses, or an `OSError` or a `ValueError` partway, keeps what was placed before it; any other exception fails.
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

/// Put the single file a store written before the segment format leaves under a head, as generation 0's base, by a hard link, with `stamps` as the head's stamps and a base of `lines` records of `rows_bytes` bytes, committed at once.
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

/// Why the store under `out_dir` does not load under `environment`, as the plan warns it: no store, a header that does not read, a stamp that moved, or a store stamped for another manifest; None when its stamps match.
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

/// Whether one of `labels` moved between a store's stamps and `environment`; a store whose stamps carry no lines counts as moved.
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::corpus::records::Address;
    use crate::corpus::store::KEY_BYTES;
    use std::io::Write;

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

    fn record(key: &str, index: usize) -> Vec<u8> {
        let unit = crate::corpus::records::CachedUnit {
            key: key.to_owned(),
            prior_id: format!("u-{:011}", index + 10_000_000_000),
            prior_class: "new-joins-near-tea-oy".to_owned(),
            content_key: "d177".to_owned(),
            slim: false,
            address: Some(("units/new-joins-near-tea-oy.json".to_owned(), 1, 5064)),
            ink_identical: false,
            picture_identical: false,
            junior_equivalent: false,
            ink_deltas: vec![("default".to_owned(), "d-34947e8c5c7e".to_owned())],
            diffs_digest: "758a".to_owned(),
            cluster: "c-33bad1c7".to_owned(),
            change_group: String::new(),
            ledger_class: String::new(),
            pair_codepoints: Some((0, 1)),
            proj: json::loads(
                r#"{"pair": [0, 1], "after_spans": [[0, 1], [1, 2]], "after_cells": ["qsMay/loop", "qsIt/sole"], "after_junctions": ["y5"], "before_spans": [[0, 2]], "before_glyphs": ["qsMay"], "before_junctions": []}"#,
            )
            .expect("JSON"),
            junctions: json::loads("[]").expect("JSON"),
            mismatches: Vec::new(),
            duplicate_group: None,
            exemplar: false,
            no_verdict: false,
            primary_units: Vec::new(),
            policy_file: None,
            inflated: 0,
        };
        crate::corpus::records::record_line(&unit).expect("a line")
    }

    /// A single-file store under a directory of its own, of `count` record lines, every third one naming a unit outside the column and the line at `broken` (if any) not a record, cut to `kept` percent of its bytes; and the column of the named units' keys.
    fn single_file_store(
        name: &str,
        count: usize,
        broken: Option<usize>,
        kept: Option<usize>,
    ) -> (PathBuf, ScanNames, Vec<u8>) {
        use crate::corpus::gzip::{GzipWriter, MTIME, PART_LEVEL};
        let directory = std::env::temp_dir().join(format!(
            "ams-review-threaded-scan-{name}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&directory);
        std::fs::create_dir_all(&directory).expect("a directory");
        let names = ScanNames {
            store_name: "unit-cache.ndjson.gz".to_owned(),
            store_format: "unit-cache/1".to_owned(),
            index_name: "units-index.ndjson.gz".to_owned(),
            index_format: "units-index/1".to_owned(),
            no_store: String::new(),
            unreadable: String::new(),
            manifest_moved: String::new(),
            rederiving: Vec::new(),
            asset_components: Vec::new(),
        };
        let mut column = Vec::new();
        let mut stream =
            GzipWriter::new(Vec::new(), &names.store_name, PART_LEVEL, MTIME).expect("opens");
        stream
            .write_all(b"{\"format\": \"unit-cache/1\"}\n")
            .expect("written");
        for index in 0..count {
            let key = sha256_hex(format!("unit {index}").as_bytes());
            if index % 3 != 2 {
                column.extend(content_bytes(&key).expect("hex"));
            }
            let line = if broken == Some(index) {
                format!("{{\"key\": \"{key}\", \"id\": 5}}\n").into_bytes()
            } else {
                record(&key, index)
            };
            stream.write_all(&line).expect("written");
        }
        let bytes = stream.finish().expect("finished");
        let bytes = &bytes[..bytes.len() * kept.unwrap_or(100) / 100];
        std::fs::write(names.store_path(&directory), bytes).expect("written");
        (directory, names, column)
    }

    /// The store scanned one line at a time on this thread, each line matched over `named` and parsed: what the threaded scan must apply, in its order, up to the first failure.
    fn serial_scan(
        opened: &OpenStore,
        named: &KeyIndex,
        column: &[u8],
        trusted: &HashSet<String>,
    ) -> Vec<std::result::Result<ScannedLine, ScanError>> {
        let mut scanned = Vec::new();
        for (index, line) in opened.lines().enumerate() {
            let line = match line {
                Ok((_, line)) => line,
                Err(failure) => {
                    scanned.push(Err(store_failure(failure)));
                    break;
                }
            };
            let ordinal = named_ordinal(named, column, &line);
            let cached = match ordinal.map(|_| parse_line(&line, trusted)).transpose() {
                Ok(cached) => cached,
                Err(failure) => {
                    scanned.push(Err(record_failure(failure)));
                    break;
                }
            };
            scanned.push(Ok(ScannedLine {
                index,
                ordinal,
                cached,
                line,
            }));
        }
        scanned
    }

    #[test]
    fn the_threaded_scan_applies_what_the_serial_scan_yields_in_its_order() {
        let cases = [
            ("whole", 1500, None, None, None),
            ("broken", 1500, Some(1101), None, None),
            ("cut", 1500, None, Some(80), None),
            ("stopped", 1500, None, None, Some(1201)),
        ];
        for (name, count, broken, kept, stop) in cases {
            let (directory, names, column) = single_file_store(name, count, broken, kept);
            let named = KeyIndex::new(&column, KEY_BYTES, column.len() / KEY_BYTES).expect("keys");
            let trusted = HashSet::new();
            let opened = open_store(&directory, &names)
                .expect("opens")
                .expect("a store");
            let mut serial = Vec::new();
            let mut serial_end = Ok(());
            for item in serial_scan(&opened, &named, &column, &trusted) {
                match item {
                    Ok(scanned) if Some(scanned.index) == stop => {
                        serial_end =
                            Err(ScanError::Failed(format!("stopped at {}", scanned.index)));
                        break;
                    }
                    Ok(scanned) => serial.push((scanned.index * 2, scanned)),
                    Err(failure) => {
                        serial_end = Err(failure);
                        break;
                    }
                }
            }
            assert!(serial.iter().any(|(_, scanned)| scanned.cached.is_some()));
            assert!(serial.len() > 2 * SCAN_BATCH_LINES, "{name}");
            assert_eq!(serial_end.is_err(), name != "whole", "{name}");
            for threads in [1, 2, 5] {
                let mut applied = Vec::new();
                let end = scan_store_threaded(
                    &opened,
                    &named,
                    &column,
                    true,
                    &trusted,
                    threads,
                    |scanned| scanned.index * 2,
                    |scanned, doubled| -> std::result::Result<(), ScanError> {
                        if Some(scanned.index) == stop {
                            return Err(ScanError::Failed(format!("stopped at {}", scanned.index)));
                        }
                        applied.push((doubled, scanned));
                        Ok(())
                    },
                );
                assert_eq!(end, serial_end, "{name} at {threads}");
                assert!(applied == serial, "{name} at {threads}");
            }
            let _ = std::fs::remove_dir_all(&directory);
        }
    }

    #[test]
    fn the_flat_scan_applies_what_the_serial_scan_yields_in_its_order() {
        type Seen = (
            usize,
            Option<u32>,
            usize,
            Option<(String, Option<Address>)>,
            Vec<u8>,
        );
        let held = |cached: Option<Cached<'_>>| {
            cached.map(|cached| {
                (
                    cached.prior_id().to_owned(),
                    cached
                        .address()
                        .map(|(part, start, length)| (part.to_owned(), start, length)),
                )
            })
        };
        for (name, broken, kept) in [
            ("flat-whole", None, None),
            ("flat-broken", Some(1101), None),
            ("flat-cut", None, Some(80)),
        ] {
            let (directory, names, column) = single_file_store(name, 1500, broken, kept);
            let named = KeyIndex::new(&column, KEY_BYTES, column.len() / KEY_BYTES).expect("keys");
            let trusted: HashSet<String> = ["units/new-joins-near-tea-oy.json".to_owned()].into();
            let opened = open_store(&directory, &names)
                .expect("opens")
                .expect("a store");
            let mut serial: Vec<Seen> = Vec::new();
            let mut serial_end = Ok(());
            for item in serial_scan(&opened, &named, &column, &trusted) {
                match item {
                    Ok(scanned) => serial.push((
                        scanned.index,
                        scanned.ordinal,
                        scanned.line.len(),
                        held(scanned.cached.as_ref().map(Cached::Parsed)),
                        scanned.line,
                    )),
                    Err(failure) => {
                        serial_end = Err(failure);
                        break;
                    }
                }
            }
            assert!(serial.iter().any(|seen| seen.3.is_some()));
            for threads in [1, 2, 5] {
                let mut applied: Vec<Seen> = Vec::new();
                let end = scan_store_flat(
                    &opened,
                    &named,
                    &column,
                    true,
                    &trusted,
                    threads,
                    |_, line| line.to_vec(),
                    |scanned, line| -> std::result::Result<(), ScanError> {
                        assert!(matches!(
                            (scanned.ordinal, scanned.cached),
                            (None, None) | (Some(_), Some(Cached::Flat(_)))
                        ));
                        applied.push((
                            scanned.index,
                            scanned.ordinal,
                            scanned.length,
                            held(scanned.cached),
                            line,
                        ));
                        Ok(())
                    },
                );
                assert_eq!(end, serial_end, "{name} at {threads}");
                assert!(applied == serial, "{name} at {threads}");
            }
            let _ = std::fs::remove_dir_all(&directory);
        }
    }

    #[test]
    fn a_panic_in_a_parse_thread_or_in_apply_ends_the_threaded_scan_past_its_in_flight_batches() {
        let threads_tried = [1, 2, 5];
        let count = 3 * threads_tried[2] * SCAN_BATCH_LINES;
        let (directory, names, column) = single_file_store("panic", count, None, None);
        for threads in threads_tried {
            for in_beside in [true, false] {
                let (sender, receiver) = std::sync::mpsc::channel();
                let (directory, names, column) = (directory.clone(), names.clone(), column.clone());
                std::thread::spawn(move || {
                    let named =
                        KeyIndex::new(&column, KEY_BYTES, column.len() / KEY_BYTES).expect("keys");
                    let trusted = HashSet::new();
                    let opened = open_store(&directory, &names)
                        .expect("opens")
                        .expect("a store");
                    let end = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        scan_store_threaded(
                            &opened,
                            &named,
                            &column,
                            true,
                            &trusted,
                            threads,
                            |scanned| assert!(!in_beside || scanned.index != 700, "beside"),
                            |scanned, ()| -> std::result::Result<(), ScanError> {
                                assert!(in_beside || scanned.index != 700, "apply");
                                Ok(())
                            },
                        )
                    }));
                    let _ = sender.send(end.map_err(|panic| crate::pool::panic_message(&*panic)));
                });
                let end = receiver
                    .recv_timeout(std::time::Duration::from_secs(60))
                    .unwrap_or_else(|_| {
                        panic!("hung at {threads} with the panic in beside: {in_beside}")
                    });
                if in_beside {
                    let Ok(Err(ScanError::Failed(why))) = end else {
                        panic!("{end:?} at {threads}");
                    };
                    assert_eq!(why, "a store scan's parse thread panicked: beside");
                } else {
                    assert_eq!(end, Err("apply".to_owned()), "at {threads}");
                }
            }
        }
        let _ = std::fs::remove_dir_all(&directory);
    }
}
