//! Reading fragments back for the write. [`PriorFragmentReader`] reads the previous corpus's fragments by address: one `pread` on a part kept open, up to [`OPEN_PARTS`] parts at once, each read checked against its address, a deflated fragment inflated to its recorded length first ([`inflate_fragment`]). [`read_arena`] reads a recomputed fragment back from the units phase's [`Arena`]: one zlib stream under the build's preset dictionary, ending where the span ends, holding the unit and stamp its address carries.

use std::borrow::Cow;
use std::fmt;
use std::fs::File;
use std::io;
use std::os::unix::fs::FileExt;
use std::path::{Path, PathBuf};

use super::drive::{Arena, ArenaSpan};
use super::gzip::{Decompressor, inflate_fragment};
use super::store::PriorFragment;
use crate::fragment::lookup;
use crate::pyfmt::{PyFmtError, PyValue, json};

/// How many prior parts a reader keeps open at once; the one opened first is closed to open another.
pub const OPEN_PARTS: usize = 64;
/// The window bits [`read_arena`] inflates with, `zlib.decompressobj`'s default: the zlib framing.
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

/// Reads the prior corpus's fragments under `out_dir` by address, each part opened once and kept open, the [`OPEN_PARTS`]th closing the one opened first.
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

    /// The text of a read refused because the prior corpus changed.
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

    /// `body`'s plain bytes, a deflated fragment's inflated and checked against its recorded length.
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

    /// The fragment at `located` parsed, a deflated one inflated first, and checked to be the unit and stamp `located` carries.
    pub fn read(&mut self, located: &PriorFragment) -> std::result::Result<PyValue, PriorError> {
        let body = self.bytes(located)?;
        let plain = Self::plain(located, &body)?;
        let fragment = json::loads_bytes(&plain).map_err(PriorError::Json)?;
        if !holds_stamp(&fragment, located) {
            return Err(PriorError::Value(Self::refusal(located)));
        }
        Ok(fragment)
    }

    /// The fragment's bytes as they are stored, a deflated fragment's deflated, unparsed. A deflated fragment is inflated and checked against its recorded length; a byte-copied address's plain bytes must hold its id and stamp as the shard writer frames them.
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

    /// Whether the prior corpus holds the plain shard bytes `body` at `located`, compared over inflated bytes. A fragment of another length is not read, and a deflated one that does not inflate to its recorded length compares unequal.
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

/// The text of an arena read refused for `why`.
fn arena_refusal(located: &PriorFragment, why: &str) -> String {
    format!(
        "the arena's bytes {}+{} do not hold unit {}: {why}",
        located.start, located.length, located.unit_id
    )
}

/// The arena read's checks over the deflated bytes `packed` read at `located`: exactly one zlib stream under `dictionary`, ending where `packed` ends, whose fragment is the unit with the stamp `located` carries.
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

/// The recomputed fragment at `located`, a span of `arena` under the part name of its spill file ([`ArenaSpan::part`]), inflated with `dictionary`, parsed and checked ([`arena_fragment`]).
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ffi::Deflater;
    use std::fs;

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
