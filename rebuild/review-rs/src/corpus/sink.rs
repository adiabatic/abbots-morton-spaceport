//! The writer for whole-file outputs, as `rebuild/pipeline/output_sink.py` writes them ([`Sink`]): in place ([`Sink::write_output`]) or through a `<name>.partial` staging file renamed over the target ([`Sink::replace_output`], [`Sink::replace_output_from`], [`Sink::replace_output_with`], [`Sink::replacing`]), each optionally keeping a regular file that already holds the new bytes, so it keeps its inode and modification time. The compare follows a link, opens only a regular file and reads it in chunks; a missing target, a FIFO, a device or a directory is written as the plain form writes it. Each write appends its line to the counts file the cycle names (`<outcome>\t<bytes>`, or `rewritten\t<output>` from [`Sink::note_rewritten`]), opened for appending and never created, and a counts file that cannot be written loses its line rather than failing the write.

use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufWriter, Read, Write};
use std::path::{Path, PathBuf};

use sha2::Digest as _;

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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fragment::sha256_hex;
    use std::os::unix::fs::MetadataExt;

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
