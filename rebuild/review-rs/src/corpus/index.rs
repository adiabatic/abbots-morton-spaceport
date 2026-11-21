//! The units index, as `unit_index.write_index_lines` writes it once the manifest is on disk, from the index lines the write's runs spilled ([`spilled_lines`]): stamped with the manifest's identity, it takes one route, decided from the prior head before anything of the index is written ([`plan_write`]). With no prior index it writes a first base, and with one that does not read an `unusable` base, counted as rewritten whole; with the prior's fold due and the pass's one fold held or forced it writes the lines as the next generation's base, walking the prior's records beside them so the head's content moves only when a record did; and otherwise the records whose lines, their places cut ([`cut`]), differ from the prior materialized index's ([`compare`]) as the prior generation's next segment, upserts and tombstones ([`tombstone`]) in key order, or the head alone when none differs. A base is gzip members of the index's member size deflated on threads ([`Members`]); a segment one gzip stream. The single file a corpus from before the segment format holds is first put under a head as generation 0's base ([`adopt_single_file`]), and deleted once a later head is committed. What a write did and its note are [`IndexWrite`]'s.
//!
//! [`stage_index_write`] is the `index-write` stage of the stage fixtures.

use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};

use super::BuildError;
use super::audit::stage_line;
use super::gzip::{GzipReader, Members};
use super::input::BuildInput;
use super::manifest::{ManifestNames, held_manifest, write_manifest};
use super::rowdelta::{
    FoldClaim, Folding, Head, Key, MERGE_KEY, Part, PartReader, ReadError, StreamedPart, TOMBSTONE,
    UPSERT, WriteError, base_name, check_gzipped_header, commit, fold_due, fold_forced, head_path,
    open_parts, read_head, remove_staging, remove_unlisted, segment_header, segment_name,
};
use super::scan::{OpenIndex, ScanError, ScanNames, index_key, manifest_and_identity, stage_lines};
use super::sidecars::SidecarRun;
use super::sink::Sink;
use super::thousands;
use super::write::{Scratch, case_input, counts_of, file_sha256, files_held, hold, through_write};
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

/// What the index's write raises, by the Python class it mirrors, with its text; `Unheld` for a value Python reads that the writer does not hold, which fails the Rust build.
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

    /// The outcome's name, as `IndexWrite.outcome` holds it.
    pub fn outcome_name(&self) -> &'static str {
        match self.outcome {
            Outcome::Head => "head",
            Outcome::Segment => "segment",
            Outcome::Base => "base",
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

    /// The write as the `index-write` stage records it: its outcome, a base's reason, and its rows and their bytes.
    pub fn value(&self) -> PyValue {
        PyValue::Tuple(vec![
            PyValue::str(self.outcome_name()),
            PyValue::str(self.reason),
            PyValue::Int(self.rows),
            PyValue::Int(self.rows_bytes),
        ])
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

/// `unit_index._cut`: an index line with its place cut out, as a segment holds the record.
pub fn cut(line: &[u8]) -> Raised<Vec<u8>> {
    let end = id_end(line)?;
    let at = find(line, CLASS_SEAM, end).ok_or_else(IndexError::not_found)?;
    if at == end + 1 {
        return Ok(line.to_vec());
    }
    let mut cut = line[..=end].to_vec();
    cut.extend_from_slice(&line[at..]);
    Ok(cut)
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
    let mut ours = new.next().transpose()?;
    let mut theirs = old.next().transpose()?;
    let mut rows = 0;
    let mut last: Option<Key> = None;
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
        let mut line = Vec::with_capacity(payload.len() + 2);
        line.push(op);
        line.push(b'\t');
        line.extend_from_slice(payload);
        emit(&line)?;
        rows += 1;
        Ok(())
    };
    while ours.is_some() || theirs.is_some() {
        if ours.is_some() && ours == theirs {
            ours = new.next().transpose()?;
            theirs = old.next().transpose()?;
            continue;
        }
        let cut_ours = ours.as_deref().map(cut).transpose()?;
        let cut_theirs = theirs.as_deref().map(cut).transpose()?;
        if cut_ours.is_some() && cut_ours == cut_theirs {
            ours = new.next().transpose()?;
            theirs = old.next().transpose()?;
            continue;
        }
        let key_ours = cut_ours.as_deref().map(key_of).transpose()?;
        let key_theirs = cut_theirs.as_deref().map(key_of).transpose()?;
        match (&cut_ours, key_ours) {
            (Some(cut_ours), Some(key_ours))
                if key_theirs.as_ref().is_none_or(|theirs| key_ours <= *theirs) =>
            {
                let same = key_theirs.as_ref() == Some(&key_ours);
                row(UPSERT, cut_ours, key_ours)?;
                if same {
                    theirs = old.next().transpose()?;
                }
                ours = new.next().transpose()?;
            }
            _ => {
                let (Some(cut_theirs), Some(key_theirs)) = (cut_theirs, key_theirs) else {
                    unreachable!("a record on neither side");
                };
                row(TOMBSTONE, &tombstone(&cut_theirs)?, key_theirs)?;
                theirs = old.next().transpose()?;
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
    lines: &'a mut dyn Iterator<Item = Raised<Vec<u8>>>,
    stream: &'a mut dyn Write,
    rows: i64,
    rows_bytes: i64,
}

impl Iterator for Through<'_> {
    type Item = Raised<Vec<u8>>;

    fn next(&mut self) -> Option<Self::Item> {
        let line = match self.lines.next()? {
            Ok(line) => line,
            Err(failure) => return Some(Err(failure)),
        };
        if let Err(failure) = self.stream.write_all(&line) {
            return Some(Err(failure.into()));
        }
        self.rows += 1;
        self.rows_bytes += line.len() as i64;
        Some(Ok(line))
    }
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
    lines: &mut dyn Iterator<Item = Raised<Vec<u8>>>,
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
        let (rows, rows_bytes) = match &route.opened {
            None => {
                let (mut rows, mut rows_bytes) = (0i64, 0i64);
                for line in &mut *lines {
                    let line = line?;
                    members.write_all(&line)?;
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
                differs = compare(&mut through, &mut opened.lines(name), &mut |_| Ok(()))?;
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
    lines: &mut dyn Iterator<Item = Raised<Vec<u8>>>,
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
    compare(lines, &mut opened.lines(name), &mut |row| {
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
    lines: &mut dyn Iterator<Item = Raised<Vec<u8>>>,
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

/// The index lines the write's runs spilled, run after run in shard order, each line with its newline, as `build._write_sidecars` hands them to the index's write.
pub fn spilled_lines(runs: &[SidecarRun]) -> impl Iterator<Item = Raised<Vec<u8>>> + '_ {
    let mut runs = runs.iter();
    let mut reading: Option<BufReader<io::Take<PartReader<'_>>>> = None;
    std::iter::from_fn(move || {
        loop {
            if reading.is_none() {
                let run = runs.next()?;
                let length = u64::try_from(run.index.length).unwrap_or_default();
                reading = Some(BufReader::new(
                    PartReader::new(&run.index.file).take(length),
                ));
            }
            let stream = reading.as_mut().expect("a run being read");
            let mut line = Vec::new();
            match stream.read_until(b'\n', &mut line) {
                Ok(0) => reading = None,
                Ok(_) => return Some(Ok(line)),
                Err(failure) => {
                    reading = None;
                    return Some(Err(failure.into()));
                }
            }
        }
    })
}

// --- the stage ----------------------------------------------------------------------------------

/// The keys of the `index-write` stage's lines (`units_stage_fixtures.INDEX_WRITE_*`).
const HEAD_KEY: &str = "head";
const FILES_KEY: &str = "files";
const OUTCOME_KEY: &str = "write";
const NOTE_KEY: &str = "note";
const GENERATED: &str = "generated:";

/// Each regular file directly under `corpus` whose name starts with the index's, by name in code-point order, with its size and sha256.
fn index_files(corpus: &Path, name: &str) -> Result<PyValue> {
    let fail =
        |failure: io::Error| WireError(format!("cannot list {}: {failure}", corpus.display()));
    let mut found: Vec<String> = Vec::new();
    for entry in fs::read_dir(corpus).map_err(fail)? {
        let entry_name = entry
            .map_err(fail)?
            .file_name()
            .to_string_lossy()
            .into_owned();
        if entry_name.starts_with(name) {
            found.push(entry_name);
        }
    }
    found.sort();
    let mut held = Vec::new();
    for entry_name in found {
        let path = corpus.join(&entry_name);
        if path.is_file() {
            held.push(PyValue::Tuple(vec![
                PyValue::Str(entry_name),
                PyValue::Int(fs::metadata(&path).map_err(fail)?.len() as i64),
                PyValue::Str(file_sha256(&path).map_err(fail)?),
            ]));
        }
    }
    Ok(PyValue::List(held))
}

/// Answer one generated line of the `index-write` stage: [`write_index_lines`] over a corpus holding the case's files, its lines, under a fold claim this process holds for the case's output or none, valued what the write did and its note, or the class and text of what it raised, the files the corpus holds then and the counts lines it appended.
fn answer_generated(
    names: &IndexNames,
    stage_file: &Path,
    key: &str,
    line_input: &PyValue,
) -> Result<PyValue> {
    if !key.starts_with(GENERATED) {
        return error(format!("{key}: not a line the index-write stage answers"));
    }
    let scratch = Scratch::new(stage_file, "index-write-case")?;
    let corpus = scratch.0.join("out");
    fs::create_dir_all(&corpus).map_err(|why| WireError(format!("{key}: {why}")))?;
    let (held, rest) = case_input(key, line_input)?;
    hold(&corpus, held, key)?;
    let [PyValue::List(lines), holder] = rest else {
        return error(format!(
            "{key}: the input does not end with the lines and the holder"
        ));
    };
    let lines = lines
        .iter()
        .map(|line| match line {
            PyValue::Bytes(line) => Ok(line.clone()),
            other => error(format!("{key}: a line is {}, not bytes", other.kind())),
        })
        .collect::<Result<Vec<_>>>()?;
    let counts = scratch.0.join("counts");
    fs::write(&counts, b"").map_err(|why| WireError(format!("{key}: {why}")))?;
    let sink = Sink::new(Some(counts.clone()));
    let claim = FoldClaim::new(None, scratch.0.join("cycle").join("fold-claim.json"));
    match holder {
        PyValue::None => {}
        PyValue::Str(holder) => {
            claim
                .claim(&sink, holder)
                .map_err(|why| WireError(format!("{key}: {why}")))?;
        }
        other => return error(format!("{key}: the holder is {}", other.kind())),
    }
    let outcome = match write_index_lines(
        &corpus,
        &mut lines.into_iter().map(Ok),
        names,
        &claim,
        &sink,
    ) {
        Ok(written) => {
            let PyValue::Tuple(mut items) = written.value() else {
                unreachable!("a write's value is a tuple");
            };
            items.push(PyValue::Str(written.note()));
            PyValue::Tuple(items)
        }
        Err(failure) if failure.class == "Unheld" => {
            return error(format!("{key}: {}", failure.message));
        }
        Err(failure) => PyValue::Dict(vec![
            (PyValue::str("raised"), PyValue::Str(failure.class)),
            (PyValue::str("message"), PyValue::Str(failure.message)),
        ]),
    };
    Ok(PyValue::Tuple(vec![
        outcome,
        files_held(&corpus)?,
        counts_of(&counts, key)?,
    ]))
}

/// The `index-write` stage: when the stage file holds lines from the build's run, run the build through the write's commit ([`through_write`]), write the manifest over what it holds ([`write_manifest`]), then the index from the runs' spilled lines ([`write_index_lines`]) under the pass's fold claim the build input names, and answer with the head it committed, the files whose names start with the index's, what it did and its note; then answer each generated line ([`answer_generated`]).
pub fn stage_index_write(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let names = IndexNames::of(&input)?;
    let manifest_names = ManifestNames::of(&input)?;
    let lines = stage_lines(stage_file)?;
    if lines.ran {
        through_write(&input, stage_file, "index-write", |held| {
            let failure =
                |failure: BuildError| WireError(format!("the index write failed: {failure}"));
            let manifest = held_manifest(&input, &manifest_names, &held).map_err(failure)?;
            let corpus = &held.run.corpus;
            write_manifest(corpus, &manifest, &mut io::sink()).map_err(failure)?;
            let claim = FoldClaim::new(
                input.cycle.run.clone(),
                PathBuf::from(&input.cycle.fold_claim),
            );
            let written = write_index_lines(
                corpus,
                &mut spilled_lines(&held.written.runs),
                &names,
                &claim,
                &Sink::new(None),
            )
            .map_err(|why| WireError(format!("the index write failed: {why}")))?;
            let head = fs::read(head_path(corpus, names.name()))
                .map_err(|why| WireError(format!("cannot read the index's head: {why}")))?;
            stage_line(out, HEAD_KEY, &PyValue::Bytes(head))?;
            stage_line(out, FILES_KEY, &index_files(corpus, names.name())?)?;
            stage_line(out, OUTCOME_KEY, &written.value())?;
            stage_line(out, NOTE_KEY, &PyValue::Str(written.note()))
        })?;
    }
    for (key, key_input) in &lines.generated {
        let value = answer_generated(&names, stage_file, key, key_input)?;
        stage_line(out, key, &value)?;
    }
    Ok(())
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
}
