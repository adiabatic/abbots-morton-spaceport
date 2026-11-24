//! The units index, as `unit_index.write_index_lines` writes it once the manifest is on disk, from the index lines the write's runs spilled, lent one at a time ([`SpilledLines`]): stamped with the manifest's identity, it takes one route, decided from the prior head before anything of the index is written ([`plan_write`]). With no prior index it writes a first base, and with one that does not read an `unusable` base, counted as rewritten whole; with the prior's fold due and the pass's one fold held or forced it writes the lines as the next generation's base, walking the prior's records beside them so the head's content moves only when a record did; and otherwise the records whose lines, their places cut ([`cut`]), differ from the prior materialized index's ([`compare`]) as the prior generation's next segment, upserts and tombstones ([`tombstone`]) in key order, or the head alone when none differs. A base is gzip members of the index's member size deflated on threads ([`Members`]); a segment one gzip stream. The single file a corpus from before the segment format holds is first put under a head as generation 0's base ([`adopt_single_file`]), and deleted once a later head is committed. What a write did and its note are [`IndexWrite`]'s.

use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, BufWriter, Read, Write};
use std::path::Path;

use super::BuildError;
use super::gzip::{GzipReader, Members};
use super::input::BuildInput;
use super::rowdelta::{
    FoldClaim, Folding, Head, Key, KeyBuf, KeyedLines, LineReader, MERGE_KEY, Part, PartReader,
    ReadError, StreamedPart, TOMBSTONE, UPSERT, WriteError, base_name, check_gzipped_header,
    commit, fold_due, fold_forced, open_parts, read_head, remove_staging, remove_unlisted,
    segment_header, segment_name,
};
use super::scan::{OpenIndex, ScanError, ScanNames, index_key, manifest_and_identity};
use super::sidecars::SidecarRun;
use super::sink::Sink;
use super::thousands;
use super::write::file_sha256;
use crate::pyfmt::{PyValue, json};
use crate::wire::{Result, WireError, error};

/// `unit_index.ID_OPEN`, how every index line starts.
const ID_OPEN: &[u8] = b"{\"id\": \"";
/// `unit_index.CLASS_SEAM`, where a line's place ends and its class starts.
const CLASS_SEAM: &[u8] = b", \"class\": ";
/// `unit_index._CLUSTER_SEAM`, where a line's class ends.
const CLUSTER_SEAM: &[u8] = b", \"cluster\": ";
/// The text of the `ValueError` `bytes.index` and `bytes.rindex` raise.
const NOT_FOUND: &str = "subsection not found";

/// What the index's write raises, by the Python exception class its text names; `Unheld` for a value the writer does not hold.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct IndexError {
    pub class: String,
    pub message: String,
}

impl IndexError {
    fn new(class: &str, message: impl Into<String>) -> Self {
        Self {
            class: class.to_owned(),
            message: message.into(),
        }
    }

    fn not_found() -> Self {
        Self::new("ValueError", NOT_FOUND)
    }
}

impl std::fmt::Display for IndexError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.class, self.message)
    }
}

impl std::error::Error for IndexError {}

impl From<ReadError> for IndexError {
    fn from(failure: ReadError) -> Self {
        Self::new(failure.class(), failure.message())
    }
}

impl From<WriteError> for IndexError {
    fn from(failure: WriteError) -> Self {
        Self::new(failure.class(), failure.message())
    }
}

impl From<io::Error> for IndexError {
    fn from(failure: io::Error) -> Self {
        match ReadError::of_io(&failure) {
            ReadError::Io(message) => Self::new("OSError", message),
            other => other.into(),
        }
    }
}

impl From<IndexError> for BuildError {
    fn from(failure: IndexError) -> Self {
        Self::Failed(failure.to_string())
    }
}

type Raised<T> = std::result::Result<T, IndexError>;

/// The names and widths the index's write reads from the build input's vocabulary: the index's name and format and the manifest's asset components (through [`ScanNames`]), a base member's plain bytes (`INDEX_MEMBER_BYTES`), the threads its members deflate on (`INDEX_DEFLATE_THREADS`), and the segment cap and fold overrun.
#[derive(Clone, Debug)]
pub struct IndexNames {
    pub scan: ScanNames,
    pub member_bytes: usize,
    pub deflate_threads: usize,
    pub folding: Folding,
}

impl IndexNames {
    /// The names as the build input's vocabulary gives them.
    pub fn of(input: &BuildInput) -> Result<Self> {
        let count = |name: &str| match input.constant(name) {
            Some(PyValue::Int(value)) if *value > 0 => usize::try_from(*value)
                .map_err(|_| WireError(format!("{name} is {value}, past this machine's sizes"))),
            Some(other) => error(format!("{name} is {other:?}, not a count")),
            None => error(format!("the build input's vocabulary holds no {name}")),
        };
        Ok(Self {
            scan: ScanNames::of(input)?,
            member_bytes: count("rebuild.review.unit_index.INDEX_MEMBER_BYTES")?,
            deflate_threads: count("rebuild.review.unit_index.INDEX_DEFLATE_THREADS")?,
            folding: Folding::of(input)?,
        })
    }

    fn name(&self) -> &str {
        &self.scan.index_name
    }
}

/// What one write of the index did (`unit_index.IndexWrite`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Outcome {
    Head,
    Segment,
    Base,
}

/// What one write of the index did (`unit_index.IndexWrite`): wrote the head alone, a segment, or a new base with its reason (`first`, `unusable` or `fold`), and the segment's rows and their bytes or the base's records and theirs.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct IndexWrite {
    pub outcome: Outcome,
    pub reason: &'static str,
    pub rows: i64,
    pub rows_bytes: i64,
}

impl IndexWrite {
    fn head() -> Self {
        Self {
            outcome: Outcome::Head,
            reason: "",
            rows: 0,
            rows_bytes: 0,
        }
    }

    /// `IndexWrite.note`: what the build's `manifest+check` line says of the write.
    pub fn note(&self) -> String {
        let count = |rows: i64| thousands(usize::try_from(rows).unwrap_or_default());
        match self.outcome {
            Outcome::Head => "head only".to_owned(),
            Outcome::Segment => format!(
                "a segment of {} rows, {:.1} MB",
                count(self.rows),
                self.rows_bytes as f64 / 1e6
            ),
            Outcome::Base => format!("a new base ({}), {} records", self.reason, count(self.rows)),
        }
    }
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

/// `unit_index._id_end`: where an index line's id ends, at the quote that closes it after [`ID_OPEN`].
fn id_end(line: &[u8]) -> Raised<usize> {
    find(line, b"\"", ID_OPEN.len()).ok_or_else(IndexError::not_found)
}

/// [`cut`] as the two pieces of the line it joins, the second empty when the line has no place to cut.
fn cut_pieces(line: &[u8]) -> Raised<(&[u8], &[u8])> {
    let end = id_end(line)?;
    let at = find(line, CLASS_SEAM, end).ok_or_else(IndexError::not_found)?;
    if at == end + 1 {
        return Ok((line, &[]));
    }
    Ok((&line[..=end], &line[at..]))
}

/// Whether two pairs of pieces join into the same bytes.
fn joined_equal(left: (&[u8], &[u8]), right: (&[u8], &[u8])) -> bool {
    if left.0.len() + left.1.len() != right.0.len() + right.1.len() {
        return false;
    }
    let (short, long) = if left.0.len() <= right.0.len() {
        (left, right)
    } else {
        (right, left)
    };
    let split = long.0.len() - short.0.len();
    short.0 == &long.0[..short.0.len()]
        && short.1[..split] == long.0[short.0.len()..]
        && short.1[split..] == *long.1
}

/// `unit_index._cut`: an index line with its place cut out, as a segment holds the record.
pub fn cut(line: &[u8]) -> Raised<Vec<u8>> {
    let (head, tail) = cut_pieces(line)?;
    Ok([head, tail].concat())
}

/// `unit_index._tombstone`: the payload of the tombstone that removes the record whose line, its place cut, is `cut`: its id and class, which `index_key` reads as the record's key.
pub fn tombstone(cut: &[u8]) -> Raised<Vec<u8>> {
    let start = id_end(cut)? + 1 + CLASS_SEAM.len();
    let stop = match find(cut, CLUSTER_SEAM, start) {
        Some(stop) => stop,
        None => cut
            .iter()
            .rposition(|&byte| byte == b'}')
            .ok_or_else(IndexError::not_found)?,
    };
    let mut payload = cut[..stop].to_vec();
    payload.extend_from_slice(b"}\n");
    Ok(payload)
}

fn key_of(line: &[u8]) -> Raised<Key> {
    Ok(index_key(line)?)
}

/// A JSON text that is a string literal `json.loads` reads as its own bytes, the bytes between its quotes: no quote, backslash or control character inside, and UTF-8.
fn plain_json_string(raw: &[u8]) -> Option<&[u8]> {
    let inner = raw.strip_prefix(b"\"")?.strip_suffix(b"\"")?;
    let plain = inner
        .iter()
        .all(|&byte| byte >= 0x20 && byte != b'"' && byte != b'\\');
    (plain && std::str::from_utf8(inner).is_ok()).then_some(inner)
}

/// `unit_index.index_key` ([`index_key`]) written into `key`: a line whose class is a plain string is keyed from its bytes in place, and any other line by [`index_key`] itself, which fails as it fails.
pub fn index_key_into(line: &[u8], key: &mut KeyBuf) -> std::result::Result<(), ReadError> {
    let pieces = find(line, b"\"", ID_OPEN.len()).and_then(|end| {
        let start = find(line, CLASS_SEAM, end)? + CLASS_SEAM.len();
        let stop = match find(line, CLUSTER_SEAM, start) {
            Some(stop) => stop,
            None => line.iter().rposition(|&byte| byte == b'}')?,
        };
        let class = plain_json_string(line.get(start..stop)?)?;
        Some((class, &line[ID_OPEN.len()..end]))
    });
    match pieces {
        Some((class, id)) => {
            key.clear();
            key.push(&[class, b".json"]);
            key.push(&[id]);
        }
        None => key.set(&index_key(line)?),
    }
    Ok(())
}

/// A source of index lines lent one at a time: [`IndexLines::advance`] steps to the next line, false at the end, and [`IndexLines::current`] is that line until the next step.
pub trait IndexLines {
    fn advance(&mut self) -> Raised<bool>;
    fn current(&self) -> &[u8];
}

impl IndexLines for KeyedLines<'_> {
    fn advance(&mut self) -> Raised<bool> {
        Ok(KeyedLines::advance(self)?)
    }

    fn current(&self) -> &[u8] {
        KeyedLines::current(self)
    }
}

/// An iterator of owned lines as [`IndexLines`], each held until the next.
struct OwnedLines<'a, E> {
    lines: &'a mut dyn Iterator<Item = std::result::Result<Vec<u8>, E>>,
    line: Vec<u8>,
}

impl<E: Into<IndexError>> IndexLines for OwnedLines<'_, E> {
    fn advance(&mut self) -> Raised<bool> {
        match self.lines.next() {
            Some(Ok(line)) => {
                self.line = line;
                Ok(true)
            }
            Some(Err(failure)) => Err(failure.into()),
            None => Ok(false),
        }
    }

    fn current(&self) -> &[u8] {
        &self.line
    }
}

/// `repr` of a key, a list of bytes.
fn key_repr(key: &Key) -> String {
    let fields: Vec<String> = key
        .iter()
        .map(|field| super::gzip::bytes_repr(field))
        .collect();
    format!("[{}]", fields.join(", "))
}

/// `unit_index._compare`: walk this pass's index lines beside the prior materialized index's, both in key order, `emit` a segment row for each record that differs, places cut on both sides ([`cut`]), and return how many: an upsert of this pass's line for a record new or changed, a tombstone ([`tombstone`]) for a prior record this pass lacks, and nothing for an equal one, a line equal to its prior byte for byte passed without cutting either. A row whose key does not ascend past the last row's is refused, naming the key.
pub fn compare(
    new: &mut dyn Iterator<Item = Raised<Vec<u8>>>,
    old: &mut dyn Iterator<Item = std::result::Result<Vec<u8>, ReadError>>,
    emit: &mut dyn FnMut(&[u8]) -> Raised<()>,
) -> Raised<i64> {
    compare_lines(
        &mut OwnedLines {
            lines: new,
            line: Vec::new(),
        },
        &mut OwnedLines {
            lines: old,
            line: Vec::new(),
        },
        emit,
    )
}

/// [`compare`] over lent lines: two lines are compared in place, byte for byte and then cut ([`cut_pieces`]), and only a record that differs is copied out to be keyed and emitted.
fn compare_lines(
    new: &mut dyn IndexLines,
    old: &mut dyn IndexLines,
    emit: &mut dyn FnMut(&[u8]) -> Raised<()>,
) -> Raised<i64> {
    let mut has_ours = new.advance()?;
    let mut has_theirs = old.advance()?;
    let mut rows = 0;
    let mut last: Option<Key> = None;
    let mut line = Vec::new();
    let mut row = |op: u8, payload: &[u8], key: Key| -> Raised<()> {
        if last.as_ref().is_some_and(|last| key <= *last) {
            return Err(IndexError::new(
                "ValueError",
                format!(
                    "the index's records are out of key order at {}",
                    key_repr(&key)
                ),
            ));
        }
        last = Some(key);
        line.clear();
        line.push(op);
        line.push(b'\t');
        line.extend_from_slice(payload);
        emit(&line)?;
        rows += 1;
        Ok(())
    };
    while has_ours || has_theirs {
        let ours = has_ours.then(|| new.current());
        let theirs = has_theirs.then(|| old.current());
        if ours.is_some() && ours == theirs {
            has_ours = new.advance()?;
            has_theirs = old.advance()?;
            continue;
        }
        let pieces_ours = ours.map(cut_pieces).transpose()?;
        let pieces_theirs = theirs.map(cut_pieces).transpose()?;
        if let (Some(left), Some(right)) = (pieces_ours, pieces_theirs)
            && joined_equal(left, right)
        {
            has_ours = new.advance()?;
            has_theirs = old.advance()?;
            continue;
        }
        let cut_ours = pieces_ours.map(|(head, tail)| [head, tail].concat());
        let cut_theirs = pieces_theirs.map(|(head, tail)| [head, tail].concat());
        let key_ours = cut_ours.as_deref().map(key_of).transpose()?;
        let key_theirs = cut_theirs.as_deref().map(key_of).transpose()?;
        match (&cut_ours, key_ours) {
            (Some(cut_ours), Some(key_ours))
                if key_theirs.as_ref().is_none_or(|theirs| key_ours <= *theirs) =>
            {
                let same = key_theirs.as_ref() == Some(&key_ours);
                row(UPSERT, cut_ours, key_ours)?;
                if same {
                    has_theirs = old.advance()?;
                }
                has_ours = new.advance()?;
            }
            _ => {
                let (Some(cut_theirs), Some(key_theirs)) = (cut_theirs, key_theirs) else {
                    unreachable!("a record on neither side");
                };
                row(TOMBSTONE, &tombstone(&cut_theirs)?, key_theirs)?;
                has_theirs = old.advance()?;
            }
        }
    }
    drop(row);
    Ok(rows)
}

/// A write's route, decided before anything of the index is written (`unit_index._IndexRoute`): `reason` names a new base's and is empty for a segment or the head alone, `prior` is the head the write follows, and `opened` its parts, checked.
struct IndexRoute {
    reason: &'static str,
    prior: Option<Head>,
    opened: Option<OpenIndex>,
}

impl IndexRoute {
    fn base(reason: &'static str, prior: Option<Head>) -> Self {
        Self {
            reason,
            prior,
            opened: None,
        }
    }
}

/// Whether `failure` is one `unit_index._checked` and `_adopt_single_file` catch: an `OSError`, an `EOFError`, a `ValueError` or a `zlib.error`.
fn caught(failure: &ReadError) -> bool {
    matches!(
        failure,
        ReadError::Head(_) | ReadError::Value(..) | ReadError::Gzip(_) | ReadError::Io(_)
    )
}

/// `unit_index._adopt_single_file`: put the single file a corpus from before the segment format holds under a head, as generation 0's base by a hard link, its stamps the file's inline header and its rows and their bytes counted from every byte after that header, committed at once; None when the file does not read in this format.
fn adopt_single_file(corpus: &Path, names: &IndexNames, sink: &Sink) -> Raised<Option<Head>> {
    let single = corpus.join(names.name());
    let read = || -> std::result::Result<Option<(PyValue, i64, i64)>, ReadError> {
        let file = File::open(&single).map_err(|failure| ReadError::of_io(&failure))?;
        let mut stream = BufReader::new(GzipReader::new(file));
        let mut line = Vec::new();
        stream
            .read_until(b'\n', &mut line)
            .map_err(|failure| ReadError::of_io(&failure))?;
        let header = match json::loads_bytes(&line) {
            Ok(header) => header,
            Err(failure) if json::unheld(&failure) => return Err(ReadError::Unheld(failure.0)),
            Err(_) => return Ok(None),
        };
        let PyValue::Dict(entries) = &header else {
            return Ok(None);
        };
        if !entries.iter().any(|(key, value)| {
            matches!((key, value), (PyValue::Str(key), PyValue::Str(format)) if key == "format" && *format == names.scan.index_format)
        }) {
            return Ok(None);
        }
        let (mut rows, mut rows_bytes) = (0i64, 0i64);
        let mut chunk = vec![0; 1 << 20];
        loop {
            let read = match stream.read(&mut chunk) {
                Ok(0) => break,
                Ok(read) => read,
                Err(failure) if failure.kind() == io::ErrorKind::Interrupted => continue,
                Err(failure) => return Err(ReadError::of_io(&failure)),
            };
            rows += chunk[..read].iter().filter(|&&byte| byte == b'\n').count() as i64;
            rows_bytes += read as i64;
        }
        Ok(Some((header, rows, rows_bytes)))
    };
    let (stamps, rows, rows_bytes) = match read() {
        Ok(Some(read)) => read,
        Ok(None) => return Ok(None),
        Err(failure) if caught(&failure) => return Ok(None),
        Err(failure) => return Err(failure.into()),
    };
    let name = base_name(names.name(), 0);
    let path = corpus.join(&name);
    fs::hard_link(&single, &path)?;
    let base = Part {
        name,
        size: fs::metadata(&path)?.len() as i64,
        sha256: file_sha256(&path)?,
        rows,
        rows_bytes,
        settled: None,
        members: None,
    };
    let head = Head {
        output: names.name().to_owned(),
        generation: 0,
        content: 0,
        stamps,
        base,
        segments: Vec::new(),
        merge: MERGE_KEY.to_owned(),
        family_keys: Vec::new(),
    };
    commit(sink, corpus, &head)?;
    Ok(Some(head))
}

/// `unit_index._checked`: `head`'s parts opened for the compare, each checked against the head, its sha256 too with `sha256`, and each segment's header line against the head's output, generation, number and merge; None when one fails.
fn checked(
    corpus: &Path,
    head: &Head,
    sha256: bool,
    names: &IndexNames,
) -> Raised<Option<OpenIndex>> {
    let files = match open_parts(corpus, head, sha256) {
        Ok(files) => files,
        Err(ReadError::Head(_)) => return Ok(None),
        Err(failure) => return Err(failure.into()),
    };
    for (index, file) in files.iter().enumerate().skip(1) {
        match check_gzipped_header(file, names.name(), head.generation, index as i64, MERGE_KEY) {
            Ok(()) => {}
            Err(failure) if caught(&failure) => return Ok(None),
            Err(failure) => return Err(failure.into()),
        }
    }
    let PyValue::Dict(header) = head.stamps.clone() else {
        return Err(IndexError::new(
            "TypeError",
            "a head's stamps are not a dict",
        ));
    };
    Ok(Some(OpenIndex::new(header, head.clone(), files)))
}

/// Whether `stamps` name `format` as theirs.
fn stamped_in(stamps: &PyValue, format: &str) -> bool {
    matches!(stamps, PyValue::Dict(entries) if entries.iter().any(|(key, value)| {
        matches!((key, value), (PyValue::Str(key), PyValue::Str(held)) if key == "format" && held == format)
    }))
}

/// `unit_index._plan_write`: decide a write's route from the prior head, before anything of the index is written. It deletes the staging files a killed write left, leaving a link at a staging name, and the parts no head lists, adopts the single file a corpus from before the segment format holds ([`adopt_single_file`]), opens the prior's parts and checks each ([`checked`]), every byte against its sha256 but for an adopted file's, and claims the pass's one fold where the head's fold is due. A head that does not read, merges another way or names another format, a part that fails its check and a single file that does not read in this format take the `unusable` route.
fn plan_write(
    corpus: &Path,
    names: &IndexNames,
    claim: &FoldClaim,
    sink: &Sink,
) -> Raised<IndexRoute> {
    let name = names.name();
    remove_staging(corpus, name, false)?;
    let head = match read_head(corpus, name) {
        Ok(head) => head,
        Err(ReadError::Head(_)) => {
            remove_unlisted(corpus, name, None)?;
            return Ok(IndexRoute::base("unusable", None));
        }
        Err(failure) => return Err(failure.into()),
    };
    remove_unlisted(corpus, name, head.as_ref())?;
    let Some(head) = head else {
        if !corpus.join(name).exists() {
            return Ok(IndexRoute::base("first", None));
        }
        let Some(head) = adopt_single_file(corpus, names, sink)? else {
            return Ok(IndexRoute::base("unusable", None));
        };
        return Ok(match checked(corpus, &head, false, names)? {
            None => IndexRoute::base("unusable", Some(head)),
            Some(opened) => IndexRoute {
                reason: "",
                prior: Some(head),
                opened: Some(opened),
            },
        });
    };
    if head.merge != MERGE_KEY || !stamped_in(&head.stamps, &names.scan.index_format) {
        return Ok(IndexRoute::base("unusable", Some(head)));
    }
    let Some(opened) = checked(corpus, &head, true, names)? else {
        return Ok(IndexRoute::base("unusable", Some(head)));
    };
    let Folding { cap, overrun } = names.folding;
    let reason =
        if fold_due(&head, cap) && (claim.claim(sink, name)? || fold_forced(&head, cap, overrun)) {
            "fold"
        } else {
            ""
        };
    Ok(IndexRoute {
        reason,
        prior: Some(head),
        opened: Some(opened),
    })
}

/// `unit_index._header_line`: the stamps as their header line, `json.dumps` in the default form.
fn header_line(stamps: &PyValue) -> Raised<Vec<u8>> {
    let mut line = json::dumps(stamps).map_err(|failure| IndexError::new("Unheld", failure.0))?;
    line.push('\n');
    Ok(line.into_bytes())
}

/// The lines a fold walks beside the prior's, each written to the base as it is drawn and counted (`_write_base`'s `through`).
struct Through<'a> {
    lines: &'a mut dyn IndexLines,
    stream: &'a mut dyn Write,
    rows: i64,
    rows_bytes: i64,
}

impl IndexLines for Through<'_> {
    fn advance(&mut self) -> Raised<bool> {
        if !self.lines.advance()? {
            return Ok(false);
        }
        let line = self.lines.current();
        self.stream.write_all(line)?;
        self.rows += 1;
        self.rows_bytes += line.len() as i64;
        Ok(true)
    }

    fn current(&self) -> &[u8] {
        self.lines.current()
    }
}

/// The prior index's materialized lines, its base and segments merged by key ([`KeyedLines`]), as `OpenIndex.lines` reads them.
pub(crate) fn prior_lines<'a>(opened: &'a OpenIndex, output: &str) -> KeyedLines<'a> {
    let generation = opened.head.as_ref().map_or(0, |head| head.generation);
    KeyedLines::new(
        &opened.files,
        output,
        generation,
        index_key_into,
        index_key_into,
    )
}

/// Delete the single file a corpus from before the segment format holds, once a later head is committed.
fn unlink_single(corpus: &Path, names: &IndexNames) -> Raised<()> {
    match fs::remove_file(corpus.join(names.name())) {
        Err(failure) if failure.kind() != io::ErrorKind::NotFound => Err(failure.into()),
        _ => Ok(()),
    }
}

/// `unit_index._write_base`: write this pass's lines whole as the next generation's base, its header line then its lines as gzip members ([`Members`]), and commit a head that lists it alone. A fold walks the prior's records beside the lines ([`compare`]), writing no row, so its head's content moves only when a record did.
fn write_base(
    corpus: &Path,
    route: &mut IndexRoute,
    stamps: &PyValue,
    lines: &mut dyn IndexLines,
    names: &IndexNames,
    sink: &Sink,
) -> Raised<IndexWrite> {
    let name = names.name();
    if route.reason == "unusable" {
        sink.note_rewritten(name);
    }
    let generation = route.prior.as_ref().map_or(0, |prior| prior.generation + 1);
    let part_name = base_name(name, generation);
    let header = header_line(stamps)?;
    let mut differs = 0;
    let mut written = None;
    sink.replace_output_with::<IndexError>(&corpus.join(&part_name), None, |staging| {
        let mut members = Members::new(
            BufWriter::new(File::create(staging)?),
            names.member_bytes,
            names.deflate_threads,
        );
        members.write_all(&header)?;
        let (rows, rows_bytes) =
            match &route.opened {
                None => {
                    let (mut rows, mut rows_bytes) = (0i64, 0i64);
                    while lines.advance()? {
                        let line = lines.current();
                        members.write_all(line)?;
                        rows += 1;
                        rows_bytes += line.len() as i64;
                    }
                    (rows, rows_bytes)
                }
                Some(opened) => {
                    let mut through = Through {
                        lines: &mut *lines,
                        stream: &mut members,
                        rows: 0,
                        rows_bytes: 0,
                    };
                    differs =
                        compare_lines(&mut through, &mut prior_lines(opened, name), &mut |_| {
                            Ok(())
                        })?;
                    (through.rows, through.rows_bytes)
                }
            };
        let (mut file, size, sha256) = members.finish()?;
        file.flush()?;
        written = Some(Part {
            name: part_name.clone(),
            size: size as i64,
            sha256,
            rows,
            rows_bytes,
            settled: None,
            members: None,
        });
        Ok(())
    })?;
    let base = written.expect("a written base");
    route.opened = None;
    let content = route.prior.as_ref().map_or(0, |prior| {
        prior.content + i64::from(route.reason != "fold" || differs != 0)
    });
    let (rows, rows_bytes) = (base.rows, base.rows_bytes);
    let head = Head {
        output: name.to_owned(),
        generation,
        content,
        stamps: stamps.clone(),
        base,
        segments: Vec::new(),
        merge: MERGE_KEY.to_owned(),
        family_keys: Vec::new(),
    };
    commit(sink, corpus, &head)?;
    unlink_single(corpus, names)?;
    Ok(IndexWrite {
        outcome: Outcome::Base,
        reason: route.reason,
        rows,
        rows_bytes,
    })
}

/// `unit_index._write_segment`: write the records that differ from the prior's ([`compare`]) as the prior generation's next segment, one gzip stream that is made only when a row reaches it ([`StreamedPart`]), and commit a head that lists it, or, when none differs, the prior head under this pass's stamps alone, which the commit leaves in place when it comes out the same.
fn write_segment(
    corpus: &Path,
    route: &mut IndexRoute,
    stamps: &PyValue,
    lines: &mut dyn IndexLines,
    names: &IndexNames,
    sink: &Sink,
) -> Raised<IndexWrite> {
    let name = names.name();
    let (Some(prior), Some(opened)) = (route.prior.as_ref(), route.opened.as_ref()) else {
        unreachable!("a segment's route follows a head it opened");
    };
    let number = prior.segments.len() as i64 + 1;
    let mut part = StreamedPart::new(
        sink,
        corpus,
        &segment_name(name, prior.generation, number),
        segment_header(name, prior.generation, number, MERGE_KEY),
    );
    compare_lines(lines, &mut prior_lines(opened, name), &mut |row| {
        part.write(row).map_err(IndexError::from)
    })?;
    let written = part.close()?;
    let (head, outcome) = match written {
        None => {
            let mut head = prior.clone();
            head.stamps = stamps.clone();
            (head, IndexWrite::head())
        }
        Some(part) => {
            let outcome = IndexWrite {
                outcome: Outcome::Segment,
                reason: "",
                rows: part.rows,
                rows_bytes: part.rows_bytes,
            };
            (prior.with_segment(part, stamps.clone()), outcome)
        }
    };
    route.opened = None;
    commit(sink, corpus, &head)?;
    unlink_single(corpus, names)?;
    Ok(outcome)
}

/// `unit_index.write_index_lines`: write the index under `corpus` from already-projected lines, each with its place, in key order, stamped with the format and the identity of the manifest beside it, so this runs after the manifest is written. It takes one route, decided before anything of the index is written ([`plan_write`]), a new base ([`write_base`]) or a segment or the head alone ([`write_segment`]), and commits by putting the head in place last. `claim` is the pass's fold claim and `sink` counts the writes.
pub fn write_index_lines(
    corpus: &Path,
    lines: &mut dyn IndexLines,
    names: &IndexNames,
    claim: &FoldClaim,
    sink: &Sink,
) -> Raised<IndexWrite> {
    let identity = match manifest_and_identity(corpus, &names.scan) {
        Ok(Some((_, identity))) => identity,
        Ok(None) => {
            return Err(IndexError::new(
                "OSError",
                format!("cannot read {}", corpus.join("manifest.json").display()),
            ));
        }
        Err(ScanError::Unheld(why)) => return Err(IndexError::new("Unheld", why)),
        Err(failure) => return Err(IndexError::new("Exception", failure.to_string())),
    };
    let stamps = PyValue::Dict(vec![
        (
            PyValue::str("format"),
            PyValue::str(&names.scan.index_format),
        ),
        (PyValue::str("manifest_sha256"), PyValue::Str(identity)),
    ]);
    let mut route = plan_write(corpus, names, claim, sink)?;
    if route.reason.is_empty() {
        write_segment(corpus, &mut route, &stamps, lines, names, sink)
    } else {
        write_base(corpus, &mut route, &stamps, lines, names, sink)
    }
}

/// The index lines the write's runs spilled, run after run in shard order, each line with its newline, as the index's write takes them.
pub fn spilled_lines(runs: &[SidecarRun]) -> impl Iterator<Item = Raised<Vec<u8>>> + '_ {
    let mut lines = SpilledLines::new(runs);
    std::iter::from_fn(move || match lines.advance() {
        Ok(true) => Some(Ok(lines.current().to_vec())),
        Ok(false) => None,
        Err(failure) => Some(Err(failure)),
    })
}

/// What a spill file's reader reads of it at once.
const SPILL_READ_BYTES: usize = 128 * 1024;

/// [`spilled_lines`] lent one at a time ([`IndexLines`]).
pub struct SpilledLines<'a> {
    runs: std::slice::Iter<'a, SidecarRun>,
    reading: Option<LineReader<io::Take<PartReader<'a>>>>,
}

impl<'a> SpilledLines<'a> {
    pub fn new(runs: &'a [SidecarRun]) -> Self {
        Self {
            runs: runs.iter(),
            reading: None,
        }
    }
}

impl IndexLines for SpilledLines<'_> {
    fn advance(&mut self) -> Raised<bool> {
        loop {
            let reading = match &mut self.reading {
                Some(reading) => reading,
                None => {
                    let Some(run) = self.runs.next() else {
                        return Ok(false);
                    };
                    let length = u64::try_from(run.index.length).unwrap_or_default();
                    self.reading.insert(LineReader::with_capacity(
                        SPILL_READ_BYTES,
                        PartReader::new(&run.index.file).take(length),
                    ))
                }
            };
            match reading.advance() {
                Ok(true) => return Ok(true),
                Ok(false) => self.reading = None,
                Err(failure) => {
                    self.reading = None;
                    return Err(failure.into());
                }
            }
        }
    }

    fn current(&self) -> &[u8] {
        self.reading.as_ref().map_or(&[], LineReader::current)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_line_is_cut_of_its_place_and_its_tombstone_keeps_its_id_and_class() {
        let line =
            b"{\"id\": \"u1\", \"order\": 3, \"batch\": 1, \"class\": \"a\", \"cluster\": \"k\"}\n";
        let cut_line = cut(line).expect("cut");
        assert_eq!(
            cut_line,
            b"{\"id\": \"u1\", \"class\": \"a\", \"cluster\": \"k\"}\n"
        );
        assert_eq!(cut(&cut_line).expect("cut again"), cut_line);
        assert_eq!(
            tombstone(&cut_line).expect("tombstone"),
            b"{\"id\": \"u1\", \"class\": \"a\"}\n"
        );
        assert_eq!(
            tombstone(b"{\"id\": \"u1\", \"class\": null}\n").expect("tombstone"),
            b"{\"id\": \"u1\", \"class\": null}\n"
        );
        assert_eq!(cut(b"{\"id\": ").unwrap_err().message, NOT_FOUND);
    }

    #[test]
    fn the_compare_emits_upserts_and_tombstones_in_key_order_and_refuses_a_key_out_of_order() {
        let line = |id: &str, class: &str, summary: &str| {
            format!("{{\"id\": \"{id}\", \"order\": null, \"batch\": null, \"class\": \"{class}\", \"cluster\": \"k\", \"summary\": \"{summary}\"}}\n").into_bytes()
        };
        let old = [
            line("u1", "a", "one"),
            line("u2", "a", "two"),
            line("u3", "b", "three"),
        ];
        let new = [
            line("u1", "a", "one"),
            line("u2", "a", "two!"),
            line("u4", "b", "four"),
        ];
        let mut rows = Vec::new();
        let count = compare(
            &mut new.iter().cloned().map(Ok),
            &mut old.iter().cloned().map(Ok),
            &mut |row| {
                rows.push(String::from_utf8(row.to_vec()).expect("text"));
                Ok(())
            },
        )
        .expect("compared");
        assert_eq!(count, 3);
        assert_eq!(
            rows,
            [
                "+\t{\"id\": \"u2\", \"class\": \"a\", \"cluster\": \"k\", \"summary\": \"two!\"}\n",
                "-\t{\"id\": \"u3\", \"class\": \"b\"}\n",
                "+\t{\"id\": \"u4\", \"class\": \"b\", \"cluster\": \"k\", \"summary\": \"four\"}\n",
            ]
        );
        let failure = compare(
            &mut new.iter().rev().cloned().map(Ok),
            &mut old.iter().cloned().map(Ok),
            &mut |_| Ok(()),
        )
        .unwrap_err();
        assert_eq!(
            failure.message,
            "the index's records are out of key order at [b'a.json', b'u2']"
        );
    }

    #[test]
    fn the_note_says_what_the_write_did() {
        let write = |outcome, reason, rows, rows_bytes| IndexWrite {
            outcome,
            reason,
            rows,
            rows_bytes,
        };
        assert_eq!(write(Outcome::Head, "", 0, 0).note(), "head only");
        assert_eq!(
            write(Outcome::Segment, "", 12_345, 2_250_000).note(),
            "a segment of 12,345 rows, 2.2 MB"
        );
        assert_eq!(
            write(Outcome::Base, "fold", 1_234_567, 9).note(),
            "a new base (fold), 1,234,567 records"
        );
    }

    #[test]
    fn a_key_read_in_place_is_the_key_index_key_reads_and_fails_as_it_fails() {
        let lines: [&[u8]; 13] = [
            b"{\"id\": \"u1\", \"order\": 3, \"batch\": 1, \"class\": \"a\", \"cluster\": \"k\"}\n",
            b"{\"id\": \"u1\", \"class\": \"a\"}\n",
            b"{\"id\": \"u1\", \"class\": \"\", \"cluster\": null}\n",
            "{\"id\": \"u\u{e9}\", \"class\": \"\u{e9}t\u{e9}\", \"cluster\": 1}\n".as_bytes(),
            b"{\"id\": \"u1\", \"class\": \"a\\\"b\", \"cluster\": 1}\n",
            b"{\"id\": \"u1\", \"class\": \"a\\u0041\", \"cluster\": 1}\n",
            b"{\"id\": \"u1\", \"class\": null, \"cluster\": 1}\n",
            b"{\"id\": \"u1\", \"class\": 7}\n",
            b"{\"id\": \"u1\", \"class\": \"a\tb\", \"cluster\": 1}\n",
            b"{\"id\": \"u1\", \"class\": \"\xff\", \"cluster\": 1}\n",
            b"{\"id\": \"u1\", \"class\": \"a\"x, \"cluster\": 1}\n",
            b"{\"id\": \"u1\", \"order\": 3}\n",
            b"{\"id\": ",
        ];
        for line in lines {
            let mut key = KeyBuf::default();
            key.push(&[b"stale"]);
            let read = index_key_into(line, &mut key).map(|()| key.to_key());
            assert_eq!(read, index_key(line), "{}", String::from_utf8_lossy(line));
        }
    }

    #[test]
    fn pieces_join_equal_exactly_when_their_bytes_do() {
        let text = b"abcabcab";
        for left in 0..=text.len() {
            for right in 0..=text.len() {
                for end in [text.len(), text.len() - 1] {
                    let a = (&text[..left], &text[left..]);
                    let b = (&text[..right.min(end)], &text[right.min(end)..end]);
                    assert_eq!(
                        joined_equal(a, b),
                        [a.0, a.1].concat() == [b.0, b.1].concat(),
                        "{left} {right} {end}"
                    );
                }
            }
        }
        let shifted = (&b"bca"[..], &b"bcab"[..]);
        assert!(!joined_equal((&text[..3], &text[3..7]), shifted));
    }
}
