//! The writer for whole-file outputs, as `rebuild/pipeline/output_sink.py` writes them ([`Sink`]): in place ([`Sink::write_output`]) or through a `<name>.partial` staging file renamed over the target ([`Sink::replace_output`], [`Sink::replace_output_from`], [`Sink::replace_output_with`], [`Sink::replacing`]), each optionally keeping a regular file that already holds the new bytes, so it keeps its inode and modification time. The compare follows a link, opens only a regular file and reads it in chunks; a missing target, a FIFO, a device or a directory is written as the plain form writes it. Each write appends its line to the counts file the cycle names (`<outcome>\t<bytes>`, or `rewritten\t<output>` from [`Sink::note_rewritten`]), opened for appending and never created, and a counts file that cannot be written loses its line rather than failing the write.
//!
//! [`stage_sink`] is the `sink` stage of the stage fixtures: it makes each recorded or generated call again over a target set up as the call found it, and answers what the call returned, the counts lines it appended and what it left at the target.

use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, BufWriter, Read, Write};
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::path::{Path, PathBuf};

use ams_m1_kernel::emit::json_string;
use serde_json::Value;
use sha2::Digest as _;

use crate::fragment::sha256_hex;
use crate::pyfmt::PyValue;
use crate::wire::{Result, WireError, error};

/// The suffix of a staging file, `output_sink.STAGING_SUFFIX`.
pub const STAGING_SUFFIX: &str = ".partial";
/// The outcome of a write after a compare found other bytes or no file, `output_sink.CHANGED`.
pub const CHANGED: &str = "changed";
/// The outcome of a write with no compare, `output_sink.WRITTEN`.
pub const WRITTEN: &str = "written";
/// The outcome of a write the compare left in place, `output_sink.KEPT`.
pub const KEPT: &str = "kept";
/// The counts line's tag for an output rewritten whole, `output_sink.REWRITTEN`.
pub const REWRITTEN: &str = "rewritten";
/// The `sink` stage's directory of the bytes its lines name by sha256, beside the stage file.
pub const STAGE_BLOBS: &str = "sink.blobs";

const CHUNK: usize = 1 << 18;

/// The staging file beside `target`: its name with [`STAGING_SUFFIX`] added.
pub fn staging_path(target: &Path) -> PathBuf {
    let mut name = target
        .file_name()
        .map_or_else(OsString::new, ToOwned::to_owned);
    name.push(STAGING_SUFFIX);
    target.with_file_name(name)
}

/// Where a build's whole-file writes count what they did: the counts file the cycle names (`output_sink.OUTPUT_COUNTS_ENV`, which the build input carries), or none.
#[derive(Clone, Debug, Default)]
pub struct Sink {
    counts: Option<PathBuf>,
}

impl Sink {
    /// A sink that appends its counts lines to `counts`, or counts nothing.
    pub fn new(counts: Option<PathBuf>) -> Self {
        Self { counts }
    }

    /// Write `data` to `target` in place, as `Path.write_bytes` does, and return true; with `keep_if_equal`, write nothing and return false when `target` is a regular file that already holds those bytes (`output_sink.write_output`).
    pub fn write_output(
        &self,
        target: &Path,
        data: &[u8],
        keep_if_equal: bool,
    ) -> io::Result<bool> {
        let size = data.len() as u64;
        let outcome = if keep_if_equal {
            compare(target, Some(size), |handle| holds_bytes(handle, data))?
        } else {
            WRITTEN
        };
        if outcome == KEPT {
            self.count(KEPT, size);
            return Ok(false);
        }
        fs::write(target, data)?;
        self.count(outcome, size);
        Ok(true)
    }

    /// Put `data` in place at `target` through its staging file and return true; with `keep_if_equal`, write nothing and return false when `target` is a regular file that already holds those bytes (`output_sink.replace_output` with `data`).
    pub fn replace_output(
        &self,
        target: &Path,
        data: &[u8],
        keep_if_equal: bool,
    ) -> io::Result<bool> {
        let size = data.len() as u64;
        let outcome = if keep_if_equal {
            compare(target, Some(size), |handle| holds_bytes(handle, data))?
        } else {
            WRITTEN
        };
        if outcome == KEPT {
            self.count(KEPT, size);
            return Ok(false);
        }
        self.staged(target, outcome, |staging| fs::write(staging, data))
    }

    /// Put a copy of the file `source` in place at `target` through its staging file, copying its bytes alone as `shutil.copyfile` does, and return true; with `keep_if_equal`, write nothing and return false when `target` is a regular file that already holds the same bytes (`output_sink.replace_output` with `source`).
    pub fn replace_output_from(
        &self,
        target: &Path,
        source: &Path,
        keep_if_equal: bool,
    ) -> io::Result<bool> {
        let (outcome, kept) = if keep_if_equal {
            let size = fs::metadata(source)?.len();
            (
                compare(target, Some(size), |handle| holds_file(handle, source))?,
                size,
            )
        } else {
            (WRITTEN, 0)
        };
        if outcome == KEPT {
            self.count(KEPT, kept);
            return Ok(false);
        }
        self.staged(target, outcome, |staging| {
            io::copy(&mut File::open(source)?, &mut File::create(staging)?).map(drop)
        })
    }

    /// Put in place at `target` what `fill` writes into the staging path it is given, and return true; with `keep_against`, the sha256 of the bytes `fill` would write, write nothing and return false when `target` is a regular file with that digest (`output_sink.replace_output` with `writer` and `sha256`). A failure in `fill` leaves `target` as it was and removes the staging file.
    pub fn replace_output_with<E: From<io::Error>>(
        &self,
        target: &Path,
        keep_against: Option<&str>,
        fill: impl FnOnce(&Path) -> std::result::Result<(), E>,
    ) -> std::result::Result<bool, E> {
        let mut outcome = WRITTEN;
        if let Some(sha256) = keep_against {
            outcome = compare(target, None, |handle| digest_is(handle, sha256))?;
            if outcome == KEPT {
                self.count(KEPT, fs::metadata(target)?.len());
                return Ok(false);
            }
        }
        self.staged(target, outcome, fill)
    }

    /// A staging file beside `target` for a writer that pushes its bytes over a span of its own (`output_sink.replacing`): [`Replacing::finish`] renames it over `target` and counts it as written, and dropping it unfinished removes it, leaving `target` as it was.
    pub fn replacing(&self, target: &Path) -> io::Result<Replacing> {
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)?;
        }
        let staging = staging_path(target);
        let file = BufWriter::new(File::create(&staging)?);
        Ok(Replacing {
            sink: self.clone(),
            target: target.to_owned(),
            staging,
            file: Some(file),
        })
    }

    /// Count that `output`, held as a base and segments, was rewritten whole outside a fold, the share rule or a letter batch (`output_sink.note_rewritten`).
    pub fn note_rewritten(&self, output: &str) {
        self.append(&format!("{REWRITTEN}\t{output}\n"));
    }

    fn staged<E: From<io::Error>>(
        &self,
        target: &Path,
        outcome: &str,
        fill: impl FnOnce(&Path) -> std::result::Result<(), E>,
    ) -> std::result::Result<bool, E> {
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)?;
        }
        let staging = staging_path(target);
        let filled = fill(&staging).and_then(|()| {
            let written = fs::metadata(&staging)?.len();
            fs::rename(&staging, target)?;
            Ok(written)
        });
        let removed = remove_staging(&staging);
        let written = filled?;
        removed?;
        self.count(outcome, written);
        Ok(true)
    }

    fn count(&self, outcome: &str, size: u64) {
        self.append(&format!("{outcome}\t{size}\n"));
    }

    fn append(&self, line: &str) {
        if let Some(path) = &self.counts
            && let Ok(mut handle) = OpenOptions::new().append(true).open(path)
        {
            let _ = handle.write_all(line.as_bytes());
        }
    }
}

/// A staging file open for writing, which [`Sink::replacing`] makes.
pub struct Replacing {
    sink: Sink,
    target: PathBuf,
    staging: PathBuf,
    file: Option<BufWriter<File>>,
}

impl Replacing {
    /// Close the staging file, rename it over the target and count it as written. A failure removes the staging file.
    pub fn finish(mut self) -> io::Result<()> {
        let file = self.file.take().expect("an unfinished staging file");
        let renamed = file
            .into_inner()
            .map_err(io::IntoInnerError::into_error)
            .and_then(|file| {
                drop(file);
                let written = fs::metadata(&self.staging)?.len();
                fs::rename(&self.staging, &self.target)?;
                Ok(written)
            });
        let removed = remove_staging(&self.staging);
        let written = renamed?;
        removed?;
        self.sink.count(WRITTEN, written);
        Ok(())
    }
}

impl Write for Replacing {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        self.file
            .as_mut()
            .expect("an unfinished staging file")
            .write(data)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.file
            .as_mut()
            .expect("an unfinished staging file")
            .flush()
    }
}

impl Drop for Replacing {
    fn drop(&mut self) {
        if let Some(file) = self.file.take() {
            drop(file);
            let _ = remove_staging(&self.staging);
        }
    }
}

fn remove_staging(staging: &Path) -> io::Result<()> {
    match fs::remove_file(staging) {
        Err(failure) if failure.kind() != io::ErrorKind::NotFound => Err(failure),
        _ => Ok(()),
    }
}

/// `output_sink._compare`: [`KEPT`] when `target` is a regular file of `size` bytes (any size when None) for which `same` holds, [`CHANGED`] when it is missing or another regular file, and [`WRITTEN`] when it is not a regular file, which it never opens. The metadata follows a link.
fn compare(
    target: &Path,
    size: Option<u64>,
    same: impl FnOnce(&mut File) -> io::Result<bool>,
) -> io::Result<&'static str> {
    let found = match fs::metadata(target) {
        Err(failure) if failure.kind() == io::ErrorKind::NotFound => return Ok(CHANGED),
        found => found?,
    };
    if !found.file_type().is_file() {
        return Ok(WRITTEN);
    }
    if size.is_some_and(|size| size != found.len()) {
        return Ok(CHANGED);
    }
    match File::open(target) {
        Err(failure) if failure.kind() == io::ErrorKind::NotFound => Ok(CHANGED),
        handle => Ok(if same(&mut handle?)? { KEPT } else { CHANGED }),
    }
}

fn read_chunk(handle: &mut File, buffer: &mut [u8]) -> io::Result<usize> {
    let mut filled = 0;
    while filled < buffer.len() {
        match handle.read(&mut buffer[filled..]) {
            Ok(0) => break,
            Ok(count) => filled += count,
            Err(failure) if failure.kind() == io::ErrorKind::Interrupted => {}
            Err(failure) => return Err(failure),
        }
    }
    Ok(filled)
}

fn holds_bytes(handle: &mut File, payload: &[u8]) -> io::Result<bool> {
    let mut buffer = vec![0; CHUNK];
    let mut offset = 0;
    loop {
        let count = read_chunk(handle, &mut buffer)?;
        if count == 0 {
            return Ok(offset == payload.len());
        }
        if offset + count > payload.len() || buffer[..count] != payload[offset..offset + count] {
            return Ok(false);
        }
        offset += count;
    }
}

fn holds_file(handle: &mut File, source: &Path) -> io::Result<bool> {
    let mut other = File::open(source)?;
    let (mut ours, mut theirs) = (vec![0; CHUNK], vec![0; CHUNK]);
    loop {
        let count = read_chunk(handle, &mut ours)?;
        if read_chunk(&mut other, &mut theirs)? != count {
            return Ok(false);
        }
        if count == 0 {
            return Ok(true);
        }
        if ours[..count] != theirs[..count] {
            return Ok(false);
        }
    }
}

fn digest_is(handle: &mut File, sha256: &str) -> io::Result<bool> {
    let mut digest = sha2::Sha256::new();
    let mut buffer = vec![0; CHUNK];
    loop {
        let count = read_chunk(handle, &mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    let found: String = digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    Ok(found == sha256)
}

/// What a `sink` line says the target held before the call.
enum Held {
    Missing,
    Absent,
    File(Vec<u8>),
    Fifo,
    Directory,
    LinkNull,
    LinkFile(Vec<u8>),
}

/// The call a `sink` line makes, with the bytes it writes.
enum Call {
    WriteOutput(Vec<u8>),
    ReplaceData(Vec<u8>),
    ReplaceSource(Vec<u8>),
    ReplaceWriter { bytes: Vec<u8>, raises: bool },
    Replacing { bytes: Vec<u8>, raises: bool },
    NoteRewritten(String),
}

fn typed(record: &Value, name: &str, key: &str) -> Result<PyValue> {
    PyValue::from_typed(&record[name])
        .map_err(|failure| WireError(format!("{key} {name}: {failure}")))
}

fn text<'a>(value: &'a PyValue, what: &str) -> Result<&'a str> {
    match value {
        PyValue::Str(text) => Ok(text),
        other => error(format!("{what} is {}, not a str", other.kind())),
    }
}

fn items<'a>(value: &'a PyValue, what: &str) -> Result<&'a [PyValue]> {
    match value {
        PyValue::Tuple(items) if !items.is_empty() => Ok(items),
        other => error(format!(
            "{what} is not a tagged tuple: {}",
            other.to_typed()
        )),
    }
}

/// The bytes a stage line names by sha256, read from the stage's blob directory and checked against that digest.
fn blob(blobs: &Path, sha256: &PyValue) -> Result<Vec<u8>> {
    let sha256 = text(sha256, "a blob's digest")?;
    let path = blobs.join(sha256);
    let bytes = fs::read(&path)
        .map_err(|failure| WireError(format!("cannot read {}: {failure}", path.display())))?;
    if sha256_hex(&bytes) != sha256 {
        return error(format!(
            "{} does not hold the bytes it is named for",
            path.display()
        ));
    }
    Ok(bytes)
}

fn held(value: &PyValue, blobs: &Path) -> Result<Held> {
    let parts = items(value, "held")?;
    Ok(match (text(&parts[0], "held's tag")?, &parts[1..]) {
        ("missing", []) => Held::Missing,
        ("absent", []) => Held::Absent,
        ("file", [sha256]) => Held::File(blob(blobs, sha256)?),
        ("fifo", []) => Held::Fifo,
        ("directory", []) => Held::Directory,
        ("link", [kind]) if text(kind, "a link's kind")? == "null" => Held::LinkNull,
        ("link", [kind, sha256]) if text(kind, "a link's kind")? == "file" => {
            Held::LinkFile(blob(blobs, sha256)?)
        }
        _ => return error(format!("no held state {}", value.to_typed())),
    })
}

fn call(record: &Value, key: &str, blobs: &Path) -> Result<Call> {
    let name = typed(record, "call", key)?;
    let payload = typed(record, "payload", key)?;
    let parts = items(&payload, "payload")?;
    let kind = text(&parts[0], "the payload's kind")?;
    let [value] = &parts[1..] else {
        return error(format!("{key}: no payload {}", payload.to_typed()));
    };
    if kind == "output" {
        return match text(&name, "the call")? {
            "note_rewritten" => Ok(Call::NoteRewritten(text(value, "the output")?.to_owned())),
            other => error(format!("{key}: {other} takes no output name")),
        };
    }
    let bytes = blob(blobs, value)?;
    Ok(match (text(&name, "the call")?, kind) {
        ("write_output", "bytes" | "str") => Call::WriteOutput(bytes),
        ("replace_output", "bytes" | "str") => Call::ReplaceData(bytes),
        ("replace_output", "source") => Call::ReplaceSource(bytes),
        ("replace_output", "writer") => Call::ReplaceWriter {
            bytes,
            raises: false,
        },
        ("replace_output", "writer-raises") => Call::ReplaceWriter {
            bytes,
            raises: true,
        },
        ("replacing", "stream") => Call::Replacing {
            bytes,
            raises: false,
        },
        ("replacing", "stream-raises") => Call::Replacing {
            bytes,
            raises: true,
        },
        (other, kind) => return error(format!("{key}: {other} takes no {kind} payload")),
    })
}

fn fail(what: &str, failure: impl std::fmt::Display) -> WireError {
    WireError(format!("cannot {what}: {failure}"))
}

/// Set the target up in `directory` as the line says the call found it, and return its path and the inode it holds, if any. An absent target sits in a directory that does not exist; a linked one links to a sibling.
fn set_up(directory: &Path, name: &str, held: &Held) -> Result<(PathBuf, Option<u64>)> {
    fs::create_dir_all(directory).map_err(|failure| fail("make a case directory", failure))?;
    let target = match held {
        Held::Absent => directory.join("absent").join(name),
        _ => directory.join(name),
    };
    let made = match held {
        Held::Missing | Held::Absent => Ok(()),
        Held::File(bytes) => fs::write(&target, bytes),
        Held::Directory => fs::create_dir(&target),
        Held::LinkNull => std::os::unix::fs::symlink("/dev/null", &target),
        Held::LinkFile(bytes) => {
            let linked = format!("{name}.linked");
            fs::write(directory.join(&linked), bytes)
                .and_then(|()| std::os::unix::fs::symlink(&linked, &target))
        }
        Held::Fifo => std::process::Command::new("mkfifo")
            .arg(&target)
            .status()
            .and_then(|status| {
                status
                    .success()
                    .then_some(())
                    .ok_or_else(|| io::Error::other(format!("mkfifo exited {status}")))
            }),
    };
    made.map_err(|failure| fail("set the target up", failure))?;
    let inode = fs::symlink_metadata(&target).ok().map(|found| found.ino());
    Ok((target, inode))
}

/// What the call left at `target`, as the Python side's `_sink_state` reads it: its kind by `lstat`, a file's sha256 (through a link to one), and whether it is still the inode the call found (None when it found nothing).
fn state(target: &Path, held: Option<u64>) -> Result<PyValue> {
    let found = match fs::symlink_metadata(target) {
        Err(failure) if failure.kind() == io::ErrorKind::NotFound => {
            return Ok(PyValue::Tuple(vec![PyValue::str("missing")]));
        }
        found => found.map_err(|failure| fail("stat the target", failure))?,
    };
    let kept = held.map_or(PyValue::None, |inode| PyValue::Bool(inode == found.ino()));
    let digest = || {
        fs::read(target)
            .map(|bytes| PyValue::str(sha256_hex(&bytes)))
            .map_err(|failure| fail("read the target", failure))
    };
    let kind = found.file_type();
    Ok(PyValue::Tuple(if kind.is_symlink() {
        let referent = fs::read_link(target).map_err(|failure| fail("read a link", failure))?;
        if referent == Path::new("/dev/null") {
            vec![PyValue::str("link"), PyValue::str("null"), kept]
        } else {
            vec![PyValue::str("link"), PyValue::str("file"), digest()?, kept]
        }
    } else if kind.is_file() {
        vec![PyValue::str("file"), digest()?, kept]
    } else if kind.is_fifo() {
        vec![PyValue::str("fifo"), kept]
    } else if kind.is_dir() {
        vec![PyValue::str("directory"), kept]
    } else {
        vec![PyValue::str("other"), kept]
    }))
}

fn raised() -> PyValue {
    PyValue::Tuple(vec![PyValue::str("raised")])
}

fn returned(result: io::Result<Option<bool>>) -> PyValue {
    match result {
        Ok(Some(written)) => PyValue::Bool(written),
        Ok(None) => PyValue::None,
        Err(_) => raised(),
    }
}

/// Make one line's call over a target set up in `directory`, with its counts file at `counts`, and return what it returned, the counts lines it left and what it left at the target.
fn answer(
    record: &Value,
    key: &str,
    blobs: &Path,
    directory: &Path,
    counts: &Path,
) -> Result<[PyValue; 3]> {
    let counts_file = typed(record, "counts_file", key)?;
    let counts = match text(&counts_file, "the counts file")? {
        "none" => None,
        "missing" => Some(counts.to_owned()),
        "present" => {
            fs::write(counts, b"").map_err(|failure| fail("make a counts file", failure))?;
            Some(counts.to_owned())
        }
        other => return error(format!("{key}: no counts file state {other:?}")),
    };
    let sink = Sink::new(counts.clone());
    let made = call(record, key, blobs)?;
    let counted = |sink_counts: &Option<PathBuf>| -> PyValue {
        sink_counts
            .as_ref()
            .and_then(|path| fs::read(path).ok())
            .map_or(PyValue::None, |bytes| {
                PyValue::str(String::from_utf8_lossy(&bytes).into_owned())
            })
    };
    if let Call::NoteRewritten(output) = &made {
        sink.note_rewritten(output);
        return Ok([PyValue::None, counted(&counts), PyValue::None]);
    }
    let name_value = typed(record, "name", key)?;
    let name = text(&name_value, "the target's name")?;
    let (target, inode) = set_up(directory, name, &held(&typed(record, "held", key)?, blobs)?)?;
    let keep = match typed(record, "keep_if_equal", key)? {
        PyValue::Bool(keep) => keep,
        other => return error(format!("{key}: keep_if_equal is {}", other.kind())),
    };
    let sha256 = match typed(record, "sha256", key)? {
        PyValue::None => None,
        PyValue::Str(sha256) => Some(sha256),
        other => return error(format!("{key}: sha256 is {}", other.kind())),
    };
    let result = match &made {
        Call::WriteOutput(bytes) => sink.write_output(&target, bytes, keep).map(Some),
        Call::ReplaceData(bytes) => sink.replace_output(&target, bytes, keep).map(Some),
        Call::ReplaceSource(bytes) => {
            let source = directory.with_extension("source");
            fs::write(&source, bytes).map_err(|failure| fail("write a source", failure))?;
            sink.replace_output_from(&target, &source, keep).map(Some)
        }
        Call::ReplaceWriter { bytes, raises } => {
            let keep_against = if keep {
                match &sha256 {
                    Some(sha256) => Some(sha256.as_str()),
                    None => {
                        return error(format!("{key}: a kept writer's output needs its sha256"));
                    }
                }
            } else {
                None
            };
            sink.replace_output_with(&target, keep_against, |staging| {
                fs::write(staging, bytes)?;
                if *raises {
                    return Err(io::Error::other("the writer refuses"));
                }
                Ok(())
            })
            .map(Some)
        }
        Call::Replacing { bytes, raises } => sink.replacing(&target).and_then(|mut staged| {
            staged.write_all(bytes)?;
            if *raises {
                return Err(io::Error::other("the block refuses"));
            }
            staged.finish().map(|()| None)
        }),
        Call::NoteRewritten(_) => unreachable!("answered above"),
    };
    let left = fs::symlink_metadata(staging_path(&target)).is_ok();
    Ok([
        returned(result),
        counted(&counts),
        PyValue::Tuple(vec![state(&target, inode)?, PyValue::Bool(left)]),
    ])
}

/// The `sink` stage: for each line of `stage_file`, make its call again through [`Sink`] over a target set up in a scratch directory as the line says the call found it, with the bytes the line names by sha256 read from [`STAGE_BLOBS`] beside the stage file and a counts file in the state the line names, and write one `{"key", "returned", "counts", "after"}` line in the typed encoding: true or false for a write, None for a call that returns nothing, `("raised",)` for one that fails; the counts file's text, None when there is no counts file; and the target's state with whether a staging file is left, None for `note_rewritten`.
pub fn stage_sink(stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let blobs = stage_file
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(STAGE_BLOBS);
    let scratch = std::env::temp_dir().join(format!("ams-review-sink-{}", std::process::id()));
    let _ = fs::remove_dir_all(&scratch);
    fs::create_dir_all(&scratch).map_err(|failure| fail("make the stage's scratch", failure))?;
    let answered = answer_lines(stage_file, out, &blobs, &scratch);
    let _ = fs::remove_dir_all(&scratch);
    answered
}

fn answer_lines(
    stage_file: &Path,
    out: &mut impl Write,
    blobs: &Path,
    scratch: &Path,
) -> Result<()> {
    let file = File::open(stage_file)
        .map_err(|failure| fail(&format!("read {}", stage_file.display()), failure))?;
    for (index, line) in BufReader::new(file).lines().enumerate() {
        let line =
            line.map_err(|failure| fail(&format!("read {}", stage_file.display()), failure))?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| WireError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return error(format!("a stage line has no key: {line:.200}"));
        };
        let [returned, counts, after] = answer(
            &record,
            key,
            blobs,
            &scratch.join(index.to_string()),
            &scratch.join(format!("{index}.counts")),
        )?;
        writeln!(
            out,
            "{{\"key\":{},\"returned\":{},\"counts\":{},\"after\":{}}}",
            json_string(key),
            returned.to_typed(),
            counts.to_typed(),
            after.to_typed()
        )
        .map_err(|failure| fail("write a stage line", failure))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Scratch(PathBuf);

    impl Scratch {
        fn new(name: &str) -> Self {
            let path = std::env::temp_dir().join(format!(
                "ams-review-sink-test-{name}-{}",
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

    fn counted(scratch: &Scratch) -> (Sink, PathBuf) {
        let counts = scratch.0.join("counts");
        fs::write(&counts, b"").expect("counts");
        (Sink::new(Some(counts.clone())), counts)
    }

    #[test]
    fn an_equal_file_is_kept_with_its_inode_and_counted_as_kept() {
        let scratch = Scratch::new("kept");
        let (sink, counts) = counted(&scratch);
        let target = scratch.0.join("facts.json");
        fs::write(&target, b"{}\n").expect("write");
        let inode = fs::metadata(&target).expect("stat").ino();
        assert!(!sink.write_output(&target, b"{}\n", true).expect("kept"));
        assert!(!sink.replace_output(&target, b"{}\n", true).expect("kept"));
        assert!(
            !sink
                .replace_output_with(&target, Some(&sha256_hex(b"{}\n")), |_| -> io::Result<()> {
                    panic!("a kept writer never runs")
                })
                .expect("kept")
        );
        assert_eq!(fs::metadata(&target).expect("stat").ino(), inode);
        assert_eq!(
            fs::read_to_string(counts).expect("counts"),
            "kept\t3\nkept\t3\nkept\t3\n"
        );
    }

    #[test]
    fn a_replaced_file_is_renamed_into_place_and_its_staging_file_is_gone() {
        let scratch = Scratch::new("replaced");
        let (sink, counts) = counted(&scratch);
        let target = scratch.0.join("fonts").join("after.otf");
        assert!(sink.replace_output(&target, b"one", true).expect("changed"));
        let inode = fs::metadata(&target).expect("stat").ino();
        assert!(
            sink.replace_output(&target, b"two", false)
                .expect("written")
        );
        assert_ne!(fs::metadata(&target).expect("stat").ino(), inode);
        assert!(sink.write_output(&target, b"three", true).expect("changed"));
        assert_eq!(fs::read(&target).expect("read"), b"three");
        assert!(!staging_path(&target).exists());
        assert_eq!(
            fs::read_to_string(counts).expect("counts"),
            "changed\t3\nwritten\t3\nchanged\t5\n"
        );
    }

    #[test]
    fn a_failing_writer_leaves_the_target_and_no_staging_file() {
        let scratch = Scratch::new("refused");
        let (sink, counts) = counted(&scratch);
        let target = scratch.0.join("head");
        fs::write(&target, b"old").expect("write");
        let refused = sink.replace_output_with(&target, None, |staging| {
            fs::write(staging, b"new")?;
            Err(io::Error::other("refused"))
        });
        assert!(refused.is_err());
        assert_eq!(fs::read(&target).expect("read"), b"old");
        assert!(!staging_path(&target).exists());
        let mut staged = sink.replacing(&target).expect("staged");
        staged.write_all(b"dropped").expect("write");
        drop(staged);
        assert_eq!(fs::read(&target).expect("read"), b"old");
        assert!(!staging_path(&target).exists());
        let mut staged = sink.replacing(&target).expect("staged");
        staged.write_all(b"streamed").expect("write");
        staged.finish().expect("finished");
        assert_eq!(fs::read(&target).expect("read"), b"streamed");
        assert_eq!(fs::read_to_string(counts).expect("counts"), "written\t8\n");
    }

    #[test]
    fn a_counts_file_is_never_created_and_a_rewrite_names_its_output() {
        let scratch = Scratch::new("counts");
        let missing = scratch.0.join("counts");
        let sink = Sink::new(Some(missing.clone()));
        sink.write_output(&scratch.0.join("x"), b"x", false)
            .expect("written");
        sink.note_rewritten("units-index.ndjson.gz");
        assert!(!missing.exists());
        let (sink, counts) = counted(&scratch);
        sink.note_rewritten("units-index.ndjson.gz");
        assert_eq!(
            fs::read_to_string(counts).expect("counts"),
            "rewritten\tunits-index.ndjson.gz\n"
        );
    }

    #[test]
    fn a_target_that_is_not_a_regular_file_is_written_without_a_compare() {
        let scratch = Scratch::new("special");
        let (sink, counts) = counted(&scratch);
        let target = scratch.0.join("null");
        std::os::unix::fs::symlink("/dev/null", &target).expect("link");
        assert!(sink.write_output(&target, b"", true).expect("written"));
        assert!(
            fs::symlink_metadata(&target)
                .expect("stat")
                .file_type()
                .is_symlink()
        );
        assert!(sink.replace_output(&target, b"", true).expect("written"));
        assert!(
            fs::symlink_metadata(&target)
                .expect("stat")
                .file_type()
                .is_file()
        );
        assert_eq!(
            fs::read_to_string(counts).expect("counts"),
            "written\t0\nwritten\t0\n"
        );
    }

    #[test]
    fn a_compare_reads_past_one_chunk() {
        let scratch = Scratch::new("chunks");
        let (sink, _counts) = counted(&scratch);
        let target = scratch.0.join("large");
        let mut payload: Vec<u8> = (0..CHUNK + 7).map(|index| (index % 251) as u8).collect();
        fs::write(&target, &payload).expect("write");
        assert!(!sink.write_output(&target, &payload, true).expect("kept"));
        let source = scratch.0.join("source");
        fs::write(&source, &payload).expect("write");
        assert!(
            !sink
                .replace_output_from(&target, &source, true)
                .expect("kept")
        );
        *payload.last_mut().expect("bytes") ^= 1;
        fs::write(&source, &payload).expect("write");
        assert!(
            sink.replace_output_from(&target, &source, true)
                .expect("changed")
        );
        assert_eq!(fs::read(&target).expect("read"), payload);
    }
}
