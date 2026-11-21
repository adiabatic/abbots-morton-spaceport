//! Reading fragments back for the write. [`PriorFragmentReader`] reads the previous corpus's fragments by address, as `unit_cache.PriorFragmentReader` does: one `pread` on a part kept open, up to [`OPEN_PARTS`] parts at once, each read checked against its address, a deflated fragment inflated to its recorded length first ([`inflate_fragment`]). [`read_arena`] reads a recomputed fragment back from the units phase's [`Arena`], as `build._FragmentArena.read` does: one zlib stream under the build's preset dictionary, ending where the span ends, holding the unit and stamp its address carries.
//!
//! [`stage_prior_read`] is the `prior-read` stage of the stage fixtures.

use std::borrow::Cow;
use std::collections::HashMap;
use std::fmt;
use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, Write};
use std::os::unix::fs::FileExt;
use std::path::{Path, PathBuf};

use serde_json::Value;

use super::audit::{integer, items, stage_line, text};
use super::drive::{Arena, ArenaSpan};
use super::gzip::{Decompressor, inflate_fragment};
use super::shards::clone;
use super::store::PriorFragment;
use crate::fragment::{lookup, sha256_hex};
use crate::pyfmt::{PyFmtError, PyValue, json};
use crate::wire::{Result, WireError, error};

/// How many prior parts a reader keeps open at once, `PriorFragmentReader.OPEN_PARTS`; the one opened first is closed to open another.
pub const OPEN_PARTS: usize = 64;
/// The window bits `_FragmentArena.read` inflates with, `zlib.decompressobj`'s default: the zlib framing.
const ARENA_WINDOW: i32 = 15;
/// The window bits of a deflated fragment's raw stream.
const RAW_WINDOW: i32 = -15;
/// `EINVAL`, which `os.pread` raises for a negative offset or length.
const INVALID_ARGUMENT: i32 = 22;

/// What a read raises, by the class Python raises it as.
#[derive(Debug)]
pub enum PriorError {
    /// `ValueError`: the bytes at the address are not the unit and stamp it was recorded with, or a deflated fragment does not inflate to its recorded length.
    Value(String),
    /// What `json.loads` raises reading the fragment's bytes, a `JSONDecodeError` or a `UnicodeDecodeError`, worded as the JSON reader words it; or a text `json.loads` reads into a value the reader does not hold ([`json::unheld`]).
    Json(PyFmtError),
    /// `OSError`: a part that does not open or read, or an arena span that does not read.
    Os(io::Error),
}

impl fmt::Display for PriorError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Value(message) => formatter.write_str(message),
            Self::Json(failure) => formatter.write_str(&failure.0),
            Self::Os(failure) => write!(formatter, "{failure}"),
        }
    }
}

impl std::error::Error for PriorError {}

/// `unit_index.holds_unit`: whether a fragment's plain shard bytes hold `"id": <unit_id>` as `json.dumps` writes it, which the shard writer's framing puts on a line of its own.
pub fn holds_unit(body: &[u8], unit_id: &str) -> bool {
    holds_field(body, "id", &PyValue::str(unit_id))
}

/// Whether `body` holds `"<key>": <value>` with the value as `json.dumps` writes it.
fn holds_field(body: &[u8], key: &str, value: &PyValue) -> bool {
    let Ok(quoted) = json::dumps(value) else {
        return false;
    };
    let needle = format!("\"{key}\": {quoted}");
    body.windows(needle.len())
        .any(|window| window == needle.as_bytes())
}

/// Whether a parsed fragment is a dict holding the unit id and content key `located` carries, by Python's `!=`: a content key of None matches a fragment whose key is null or missing.
fn holds_stamp(fragment: &PyValue, located: &PriorFragment) -> bool {
    if !matches!(fragment, PyValue::Dict(_)) {
        return false;
    }
    let id = matches!(lookup(fragment, "id"), Some(PyValue::Str(id)) if *id == located.unit_id);
    let content_key = match (lookup(fragment, "content_key"), &located.content_key) {
        (None | Some(PyValue::None), None) => true,
        (Some(PyValue::Str(held)), Some(expected)) => held == expected,
        _ => false,
    };
    id && content_key
}

/// `unit_cache.PriorFragmentReader`: reads the prior corpus's fragments under `out_dir` by address, each part opened once and kept open, the [`OPEN_PARTS`]th closing the one opened first.
pub struct PriorFragmentReader {
    out_dir: PathBuf,
    open: Vec<(String, File)>,
}

impl PriorFragmentReader {
    pub fn new(out_dir: &Path) -> Self {
        Self {
            out_dir: out_dir.to_path_buf(),
            open: Vec::new(),
        }
    }

    /// `PriorFragmentReader._refusal`'s text.
    fn refusal(located: &PriorFragment) -> String {
        format!(
            "{} no longer holds unit {} at bytes {}+{}: the prior corpus changed underneath this build",
            located.part, located.unit_id, located.start, located.length
        )
    }

    /// The bytes at `located`, one `os.pread` on its part: fewer than its length where the part ends first.
    fn bytes(&mut self, located: &PriorFragment) -> std::result::Result<Vec<u8>, PriorError> {
        let index = match self.open.iter().position(|(part, _)| *part == located.part) {
            Some(index) => index,
            None => {
                if self.open.len() >= OPEN_PARTS {
                    self.open.remove(0);
                }
                let file = File::open(self.out_dir.join(&located.part)).map_err(PriorError::Os)?;
                self.open.push((located.part.clone(), file));
                self.open.len() - 1
            }
        };
        let (Ok(start), Ok(length)) = (
            u64::try_from(located.start),
            usize::try_from(located.length),
        ) else {
            return Err(PriorError::Os(io::Error::from_raw_os_error(
                INVALID_ARGUMENT,
            )));
        };
        let mut bytes = vec![0; length];
        let read = loop {
            match self.open[index].1.read_at(&mut bytes, start) {
                Err(failure) if failure.kind() == io::ErrorKind::Interrupted => {}
                read => break read.map_err(PriorError::Os)?,
            }
        };
        bytes.truncate(read);
        Ok(bytes)
    }

    /// `PriorFragmentReader._plain`: `body`'s plain bytes, a deflated fragment's inflated and checked against its recorded length.
    fn plain<'a>(
        located: &PriorFragment,
        body: &'a [u8],
    ) -> std::result::Result<Cow<'a, [u8]>, PriorError> {
        if located.inflated == 0 {
            return Ok(Cow::Borrowed(body));
        }
        let inflated = match usize::try_from(located.inflated) {
            Ok(length) => inflate_fragment(body, Some(length)),
            Err(_) if located.inflated == -1 => match Decompressor::new(RAW_WINDOW)
                .map_err(|failure| PriorError::Os(io::Error::other(failure)))?
                .decompress(body, 0)
            {
                Ok(_) => Err("it inflates past its -1 bytes".to_owned()),
                Err(failure) => Err(format!("it does not inflate ({failure})")),
            },
            Err(_) => Err("max_length must be non-negative".to_owned()),
        };
        inflated
            .map(Cow::Owned)
            .map_err(|why| PriorError::Value(format!("{} ({why})", Self::refusal(located))))
    }

    /// `PriorFragmentReader.read`: the fragment at `located` parsed, a deflated one inflated first, and checked to be the unit and stamp `located` carries.
    pub fn read(&mut self, located: &PriorFragment) -> std::result::Result<PyValue, PriorError> {
        let body = self.bytes(located)?;
        let plain = Self::plain(located, &body)?;
        let fragment = json::loads_bytes(&plain).map_err(PriorError::Json)?;
        if !holds_stamp(&fragment, located) {
            return Err(PriorError::Value(Self::refusal(located)));
        }
        Ok(fragment)
    }

    /// `PriorFragmentReader.read_bytes`: the fragment's bytes as they are stored, a deflated fragment's deflated, unparsed. A deflated fragment is inflated and checked against its recorded length; a byte-copied address's plain bytes must hold its id and stamp as the shard writer frames them.
    pub fn read_bytes(
        &mut self,
        located: &PriorFragment,
    ) -> std::result::Result<Vec<u8>, PriorError> {
        let body = self.bytes(located)?;
        let plain = Self::plain(located, &body)?;
        if located.byte_copied
            && (!holds_unit(&plain, &located.unit_id)
                || !holds_field(
                    &plain,
                    "content_key",
                    &PyValue::optional_str(located.content_key.as_deref()),
                ))
        {
            return Err(PriorError::Value(Self::refusal(located)));
        }
        Ok(body)
    }

    /// `PriorFragmentReader.same_bytes`: whether the prior corpus holds the plain shard bytes `body` at `located`, compared over inflated bytes. A fragment of another length is not read, and a deflated one that does not inflate to its recorded length compares unequal.
    pub fn same_bytes(
        &mut self,
        located: &PriorFragment,
        body: &[u8],
    ) -> std::result::Result<bool, PriorError> {
        let length = i64::try_from(body.len()).unwrap_or(i64::MAX);
        if located.inflated == 0 {
            return Ok(located.length == length && self.bytes(located)? == body);
        }
        if located.inflated != length {
            return Ok(false);
        }
        let stored = self.bytes(located)?;
        Ok(inflate_fragment(&stored, Some(body.len())).is_ok_and(|plain| plain == body))
    }
}

/// `_FragmentArena._refusal`'s text.
fn arena_refusal(located: &PriorFragment, why: &str) -> String {
    format!(
        "the arena's bytes {}+{} do not hold unit {}: {why}",
        located.start, located.length, located.unit_id
    )
}

/// `_FragmentArena.read`'s checks over the deflated bytes `packed` read at `located`: exactly one zlib stream under `dictionary`, ending where `packed` ends, whose fragment is the unit with the stamp `located` carries.
pub fn arena_fragment(
    located: &PriorFragment,
    packed: &[u8],
    dictionary: &[u8],
) -> std::result::Result<PyValue, PriorError> {
    let mut inflater = Decompressor::with_dictionary(ARENA_WINDOW, dictionary)
        .map_err(|failure| PriorError::Os(io::Error::other(failure)))?;
    let body = inflater
        .decompress_with_dictionary(packed, 0, dictionary)
        .map_err(|failure| {
            PriorError::Value(arena_refusal(
                located,
                &format!("it does not inflate ({failure})"),
            ))
        })?;
    if !inflater.eof() || !inflater.unused_data().is_empty() {
        return Err(PriorError::Value(arena_refusal(
            located,
            "its deflated stream does not end where the span ends",
        )));
    }
    let fragment = json::loads_bytes(&body).map_err(PriorError::Json)?;
    if !holds_stamp(&fragment, located) {
        return Err(PriorError::Value(arena_refusal(
            located,
            "it holds another unit or stamp",
        )));
    }
    Ok(fragment)
}

/// `_FragmentArena.read`: the recomputed fragment at `located`, a span of `arena` under the part name of its spill file ([`ArenaSpan::part`]), inflated with `dictionary`, parsed and checked ([`arena_fragment`]).
pub fn read_arena(
    arena: &Arena,
    located: &PriorFragment,
    dictionary: &[u8],
) -> std::result::Result<PyValue, PriorError> {
    let span = match (
        Arena::file_of(&located.part),
        u64::try_from(located.start),
        u64::try_from(located.length),
    ) {
        (Some(file), Ok(start), Ok(length)) => ArenaSpan {
            file,
            start,
            length,
        },
        _ => {
            return Err(PriorError::Os(io::Error::other(format!(
                "{}+{} under {} is not a span of the arena",
                located.start, located.length, located.part
            ))));
        }
    };
    let packed = arena
        .read(span)
        .map_err(|failure| PriorError::Os(io::Error::other(failure)))?;
    arena_fragment(located, &packed, dictionary)
}

/// The `prior-read` stage's directory, beside its stage file, of the parts, fragments and dictionaries its lines name by sha256.
pub const STAGE_BLOBS: &str = "prior-read.blobs";

fn fail(what: &str, failure: impl fmt::Display) -> WireError {
    WireError(format!("cannot {what}: {failure}"))
}

fn tagged(tag: &str, values: impl IntoIterator<Item = PyValue>) -> PyValue {
    PyValue::Tuple(std::iter::once(PyValue::str(tag)).chain(values).collect())
}

/// The bytes a blob holds, checked against its name.
fn blob(blobs: &Path, digest: &str) -> Result<Vec<u8>> {
    let path = blobs.join(digest);
    let bytes =
        fs::read(&path).map_err(|failure| fail(&format!("read {}", path.display()), failure))?;
    if sha256_hex(&bytes) != digest {
        return error(format!(
            "{} does not hold the bytes it is named for",
            path.display()
        ));
    }
    Ok(bytes)
}

/// A line's address: `(part, start, length, unit_id, content_key, byte_copied, inflated)`.
fn located_of(value: &PyValue) -> Result<PriorFragment> {
    let [
        part,
        start,
        length,
        unit_id,
        content_key,
        byte_copied,
        inflated,
    ] = items(value, "an address")?
    else {
        return error("an address is not the seven fields of a PriorFragment");
    };
    Ok(PriorFragment {
        part: text(part, "the part")?.to_owned(),
        start: integer(start, "the start")?,
        length: integer(length, "the length")?,
        unit_id: text(unit_id, "the unit id")?.to_owned(),
        content_key: match content_key {
            PyValue::None => None,
            other => Some(text(other, "the content key")?.to_owned()),
        },
        byte_copied: match byte_copied {
            PyValue::Bool(flag) => *flag,
            other => return error(format!("byte_copied is {}, not a bool", other.kind())),
        },
        inflated: integer(inflated, "the inflated length")?,
    })
}

/// A read's outcome as the stage records it: what it gave, `("raised", "ValueError", message)` for a refusal of the reader's own, `("undecoded",)` where `json.loads` refuses the bytes, and `("os-error", errno)`.
fn outcome(read: std::result::Result<PyValue, PriorError>) -> PyValue {
    match read {
        Ok(value) => value,
        Err(PriorError::Value(message)) => tagged(
            "raised",
            [PyValue::str("ValueError"), PyValue::str(message)],
        ),
        Err(PriorError::Json(failure)) if json::unheld(&failure) => {
            tagged("unheld", [PyValue::str(failure.0)])
        }
        Err(PriorError::Json(_)) => tagged("undecoded", []),
        Err(PriorError::Os(failure)) => tagged(
            "os-error",
            [failure.raw_os_error().map_or_else(
                || PyValue::str(failure.to_string()),
                |code| PyValue::Int(code.into()),
            )],
        ),
    }
}

/// The stage's scratch: a directory a set of held parts, made once, and the arena the arena lines place their fragments in.
struct Scratch {
    root: PathBuf,
    directories: HashMap<Vec<(String, String)>, PathBuf>,
}

impl Scratch {
    /// The directory holding exactly the parts `held` names, each a clone of its blob at its path there.
    fn directory(&mut self, held: &PyValue, blobs: &Path) -> Result<PathBuf> {
        let mut parts = Vec::new();
        for part in items(held, "the held parts")? {
            let [name, digest] = items(part, "a held part")? else {
                return error("a held part is not a name and a blob");
            };
            parts.push((
                text(name, "a held part's name")?.to_owned(),
                text(digest, "a held part's blob")?.to_owned(),
            ));
        }
        if let Some(directory) = self.directories.get(&parts) {
            return Ok(directory.clone());
        }
        let directory = self.root.join(self.directories.len().to_string());
        fs::create_dir_all(&directory)
            .map_err(|failure| fail("make a read's directory", failure))?;
        for (name, digest) in &parts {
            let target = directory.join(name);
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)
                    .map_err(|failure| fail("make a part's directory", failure))?;
            }
            blob(blobs, digest)?;
            let source = blobs.join(digest);
            if !clone(&source, &target).map_err(|failure| fail("clone a held part", failure))? {
                fs::copy(&source, &target).map_err(|failure| fail("copy a held part", failure))?;
            }
        }
        self.directories.insert(parts, directory.clone());
        Ok(directory)
    }
}

/// Answer one line: the read its form names, made over a directory holding the parts the line holds, or over an arena holding the line's span where Python's arena held it.
fn answer(key: &str, input: &PyValue, blobs: &Path, scratch: &mut Scratch) -> Result<PyValue> {
    let fields = items(input, "a line's input")?;
    let Some(form) = fields.first() else {
        return error(format!("{key}: the input names no form"));
    };
    Ok(match (text(form, "the form")?, &fields[1..]) {
        ("read", [held, located]) => {
            let directory = scratch.directory(held, blobs)?;
            outcome(
                PriorFragmentReader::new(&directory)
                    .read(&located_of(located)?)
                    .map(|fragment| tagged("fragment", [fragment])),
            )
        }
        ("read-bytes", [held, located]) => {
            let directory = scratch.directory(held, blobs)?;
            outcome(
                PriorFragmentReader::new(&directory)
                    .read_bytes(&located_of(located)?)
                    .map(|body| tagged("bytes", [PyValue::str(sha256_hex(&body))])),
            )
        }
        ("same-bytes", [held, located, body]) => {
            let directory = scratch.directory(held, blobs)?;
            let body = blob(blobs, text(body, "the body's blob")?)?;
            outcome(
                PriorFragmentReader::new(&directory)
                    .same_bytes(&located_of(located)?, &body)
                    .map(|same| tagged("same", [PyValue::Bool(same)])),
            )
        }
        ("arena", [dictionary, packed, located]) => {
            let dictionary = blob(blobs, text(dictionary, "the dictionary's blob")?)?;
            let packed = blob(blobs, text(packed, "the span's blob")?)?;
            let mut located = located_of(located)?;
            if usize::try_from(located.length).ok() != Some(packed.len()) {
                return error(format!(
                    "{key}: the span's {} bytes are not its address's {}",
                    packed.len(),
                    located.length
                ));
            }
            let start = u64::try_from(located.start)
                .map_err(|_| WireError(format!("{key}: a span starts before the arena")))?;
            let arena = Arena::new(&scratch.root);
            let span = arena.place(start, &packed).map_err(WireError)?;
            located.part = span.part();
            outcome(
                read_arena(&arena, &located, &dictionary)
                    .map(|fragment| tagged("fragment", [fragment])),
            )
        }
        (form, rest) => {
            return error(format!("{key}: no read {form} over {} values", rest.len()));
        }
    })
}

/// The `prior-read` stage: for each line of `stage_file`, make the read it names, a prior fragment's (`read`, `read-bytes` or `same-bytes`) over a scratch directory holding clones of the parts the line says the reader's directory held, or an arena fragment's (`arena`) over an arena holding the line's span at its start, the bytes read by sha256 from [`STAGE_BLOBS`] beside the stage file, and write one line with what it gave in the typed encoding: `("fragment", value)`, `("bytes", sha256)` or `("same", flag)`, or the refusal ([`outcome`]).
pub fn stage_prior_read(stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let blobs = stage_file
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(STAGE_BLOBS);
    let root = std::env::temp_dir().join(format!("ams-review-prior-read-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).map_err(|failure| fail("make the stage's scratch", failure))?;
    let mut scratch = Scratch {
        root: root.clone(),
        directories: HashMap::new(),
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
            let input = PyValue::from_typed(&record["input"])
                .map_err(|failure| WireError(format!("{key}: {failure}")))?;
            let value = answer(key, &input, &blobs, &mut scratch)?;
            stage_line(out, key, &value)?;
        }
        Ok(())
    })();
    let _ = fs::remove_dir_all(&root);
    answered
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ffi::Deflater;

    fn located(part: &str, start: i64, length: i64, inflated: i64) -> PriorFragment {
        PriorFragment {
            part: part.to_owned(),
            start,
            length,
            unit_id: "u-a".to_owned(),
            content_key: Some("k".to_owned()),
            byte_copied: true,
            inflated,
        }
    }

    fn scratch(name: &str) -> PathBuf {
        let directory = std::env::temp_dir().join(format!(
            "ams-review-prior-test-{name}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&directory);
        fs::create_dir_all(directory.join("units")).expect("make the scratch");
        directory
    }

    #[test]
    fn a_plain_fragment_reads_back_and_a_moved_one_is_refused() {
        let directory = scratch("plain");
        let fragment = b"\n {\n  \"id\": \"u-a\",\n  \"content_key\": \"k\"\n }";
        let mut part = b"[".to_vec();
        part.extend_from_slice(fragment);
        part.extend_from_slice(b"\n]\n");
        fs::write(directory.join("units/c.json"), &part).expect("write the part");
        let mut reader = PriorFragmentReader::new(&directory);
        let at = located("units/c.json", 1, fragment.len() as i64, 0);
        let read = reader.read(&at).expect("read");
        assert!(matches!(lookup(&read, "id"), Some(PyValue::Str(id)) if id == "u-a"));
        assert_eq!(reader.read_bytes(&at).expect("read the bytes"), fragment);
        assert!(reader.same_bytes(&at, fragment).expect("compare"));
        let moved = PriorFragment {
            unit_id: "u-b".to_owned(),
            ..at.clone()
        };
        assert_eq!(
            reader.read(&moved).expect_err("refused").to_string(),
            "units/c.json no longer holds unit u-b at bytes 1+42: the prior corpus changed underneath this build"
        );
        fs::remove_dir_all(&directory).expect("remove the scratch");
    }

    #[test]
    fn an_arena_fragment_inflates_under_its_dictionary_and_not_under_another() {
        let dictionary = b"\"content_key\": \"id\": ".repeat(8);
        let body = b"{\"id\": \"u-a\", \"content_key\": \"k\"}";
        let packed = Deflater::primed(1, &dictionary)
            .expect("prime")
            .deflate(body)
            .expect("deflate");
        let at = located("arena.0", 7, packed.len() as i64, 0);
        assert!(arena_fragment(&at, &packed, &dictionary).is_ok());
        assert_eq!(
            arena_fragment(&at, &packed, b"another")
                .expect_err("refused")
                .to_string(),
            format!(
                "the arena's bytes 7+{} do not hold unit u-a: it does not inflate (Error -3 while setting zdict: invalid input data)",
                packed.len()
            )
        );
    }
}
