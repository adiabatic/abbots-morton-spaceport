//! Reading fragments back for the write. [`PriorFragmentReader`] reads the previous corpus's fragments by address: one `pread` on a part kept open, up to [`OPEN_PARTS`] parts at once, into a buffer the reader reuses, each read checked against its address, a deflated fragment inflated to its recorded length first ([`inflate_fragment`](super::gzip::inflate_fragment)) through one raw inflate stream the reader resets for each fragment. [`read_arena`] reads a recomputed fragment back from the units phase's [`Arena`]: one zlib stream under the build's preset dictionary, ending where the span ends, holding the unit and stamp its address carries; [`PriorFragmentReader::read_arena_at`] reads it through a zlib stream the reader keeps.

use std::fmt;
use std::fs::File;
use std::io;
use std::os::unix::fs::FileExt;
use std::path::{Path, PathBuf};

use super::drive::{Arena, ArenaSpan};
use super::gzip::{Decompressor, StreamFailure, StreamInflater};
use super::store::{FragmentAt, PriorFragment};
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
/// The stored bytes a reader's buffer keeps between reads; a buffer grown past it is given up once the read is done.
const RETAINED_BYTES: usize = 256 << 10;

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
    holds_field(body, "id", Some(unit_id), &mut String::new(), false)
}

/// Whether `body` holds `"<key>": <value>` with the value, a str or None, as `json.dumps` writes it. The text is written into `needle`, and its places in `body` are tried back to front when `from_end`, which finds a field the fragment writes last sooner.
fn holds_field(
    body: &[u8],
    key: &str,
    value: Option<&str>,
    needle: &mut String,
    from_end: bool,
) -> bool {
    needle.clear();
    needle.push('"');
    needle.push_str(key);
    needle.push_str("\": ");
    match value {
        Some(text) => json::write_string(needle, text),
        None => needle.push_str("null"),
    }
    occurs(body, needle.as_bytes(), from_end)
}

/// Whether `needle` occurs anywhere in `haystack`: each place is tried by the needle's first and last bytes before the whole, front to back, or back to front when `from_end`.
fn occurs(haystack: &[u8], needle: &[u8], from_end: bool) -> bool {
    let (Some(&first), Some(&last)) = (needle.first(), needle.last()) else {
        return true;
    };
    let at =
        |window: &[u8]| window[0] == first && window[window.len() - 1] == last && window == needle;
    let mut windows = haystack.windows(needle.len());
    if from_end {
        windows.rev().any(at)
    } else {
        windows.any(at)
    }
}

/// Whether a parsed fragment is a dict holding the unit id and content key `located` carries, by Python's `!=`: a content key of None matches a fragment whose key is null or missing.
fn holds_stamp(fragment: &PyValue, located: FragmentAt<'_>) -> bool {
    if !matches!(fragment, PyValue::Dict(_)) {
        return false;
    }
    let id = matches!(lookup(fragment, "id"), Some(PyValue::Str(id)) if id == located.unit_id);
    let content_key = match (lookup(fragment, "content_key"), located.content_key) {
        (None | Some(PyValue::None), None) => true,
        (Some(PyValue::Str(held)), Some(expected)) => held == expected,
        _ => false,
    };
    id && content_key
}

/// Reads the prior corpus's fragments under `out_dir` by address, each part opened once and kept open, the [`OPEN_PARTS`]th closing the one opened first. The stored bytes are read into a buffer the reader keeps, a deflated fragment is inflated through a raw stream it keeps, an arena fragment through a zlib stream it keeps, and a needle is written into a text it keeps.
pub struct PriorFragmentReader {
    out_dir: PathBuf,
    open: Vec<(String, File)>,
    stored: Vec<u8>,
    fragments: StreamInflater,
    arena: StreamInflater,
    needle: String,
}

impl PriorFragmentReader {
    pub fn new(out_dir: &Path) -> Self {
        Self {
            out_dir: out_dir.to_path_buf(),
            open: Vec::new(),
            stored: Vec::new(),
            fragments: StreamInflater::new(RAW_WINDOW),
            arena: StreamInflater::new(ARENA_WINDOW),
            needle: String::new(),
        }
    }

    /// The text of a read refused because the prior corpus changed.
    fn refusal(located: FragmentAt<'_>) -> String {
        format!(
            "{} no longer holds unit {} at bytes {}+{}: the prior corpus changed underneath this build",
            located.part, located.unit_id, located.start, located.length
        )
    }

    /// Give up a buffer grown past what a reader keeps between reads.
    fn trim(&mut self) {
        if self.stored.capacity() > RETAINED_BYTES {
            self.stored = Vec::new();
        }
        self.fragments.trim();
        self.arena.trim();
    }

    /// Read the bytes at `located` into the reader's buffer, one `os.pread` on its part: fewer than its length where the part ends first.
    fn bytes(&mut self, located: FragmentAt<'_>) -> std::result::Result<(), PriorError> {
        let index = match self.open.iter().position(|(part, _)| part == located.part) {
            Some(index) => index,
            None => {
                if self.open.len() >= OPEN_PARTS {
                    self.open.remove(0);
                }
                let file = File::open(self.out_dir.join(located.part)).map_err(PriorError::Os)?;
                self.open.push((located.part.to_owned(), file));
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
        self.stored.clear();
        self.stored.resize(length, 0);
        let read = loop {
            match self.open[index].1.read_at(&mut self.stored, start) {
                Err(failure) if failure.kind() == io::ErrorKind::Interrupted => {}
                read => break read.map_err(PriorError::Os)?,
            }
        };
        self.stored.truncate(read);
        Ok(())
    }

    /// `body`'s plain bytes, a deflated fragment's inflated through `fragments` and checked against its recorded length.
    fn plain<'b>(
        located: FragmentAt<'_>,
        body: &'b [u8],
        fragments: &'b mut StreamInflater,
    ) -> std::result::Result<&'b [u8], PriorError> {
        if located.inflated == 0 {
            return Ok(body);
        }
        let inflated = match usize::try_from(located.inflated) {
            Ok(length) => fragments.inflate_fragment(body, Some(length)),
            Err(_) if located.inflated == -1 => match Decompressor::new(RAW_WINDOW)
                .map_err(|failure| PriorError::Os(io::Error::other(failure)))?
                .decompress(body, 0)
            {
                Ok(_) => Err("it inflates past its -1 bytes".to_owned()),
                Err(failure) => Err(format!("it does not inflate ({failure})")),
            },
            Err(_) => Err("max_length must be non-negative".to_owned()),
        };
        inflated.map_err(|why| PriorError::Value(format!("{} ({why})", Self::refusal(located))))
    }

    /// The fragment at `located` parsed, a deflated one inflated first, and checked to be the unit and stamp `located` carries.
    pub fn read(&mut self, located: &PriorFragment) -> std::result::Result<PyValue, PriorError> {
        self.read_at(located.at())
    }

    /// [`PriorFragmentReader::read`] at a borrowed address.
    pub fn read_at(&mut self, located: FragmentAt<'_>) -> std::result::Result<PyValue, PriorError> {
        self.bytes(located)?;
        let parsed = Self::plain(located, &self.stored, &mut self.fragments)
            .and_then(|plain| json::loads_bytes(plain).map_err(PriorError::Json));
        self.trim();
        let fragment = parsed?;
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
        self.read_bytes_at(located.at())
    }

    /// [`PriorFragmentReader::read_bytes`] at a borrowed address, the bytes in the reader's own buffer, which [`PriorFragmentReader::recycle`] hands back for the next read.
    pub fn read_bytes_at(
        &mut self,
        located: FragmentAt<'_>,
    ) -> std::result::Result<Vec<u8>, PriorError> {
        self.bytes(located)?;
        let held = Self::plain(located, &self.stored, &mut self.fragments).map(|plain| {
            !located.byte_copied
                || (holds_field(plain, "id", Some(located.unit_id), &mut self.needle, false)
                    && holds_field(
                        plain,
                        "content_key",
                        located.content_key,
                        &mut self.needle,
                        true,
                    ))
        });
        self.fragments.trim();
        if !held? {
            return Err(PriorError::Value(Self::refusal(located)));
        }
        Ok(std::mem::take(&mut self.stored))
    }

    /// Take back the bytes a [`PriorFragmentReader::read_bytes_at`] gave, for the next read to reuse, unless they outgrew what the reader keeps.
    pub fn recycle(&mut self, mut bytes: Vec<u8>) {
        if bytes.capacity() <= RETAINED_BYTES && bytes.capacity() > self.stored.capacity() {
            bytes.clear();
            self.stored = bytes;
        }
    }

    /// Whether the prior corpus holds the plain shard bytes `body` at `located`, compared over inflated bytes. A fragment of another length is not read, and a deflated one that does not inflate to its recorded length compares unequal.
    pub fn same_bytes(
        &mut self,
        located: &PriorFragment,
        body: &[u8],
    ) -> std::result::Result<bool, PriorError> {
        self.same_bytes_at(located.at(), body)
    }

    /// [`PriorFragmentReader::same_bytes`] at a borrowed address.
    pub fn same_bytes_at(
        &mut self,
        located: FragmentAt<'_>,
        body: &[u8],
    ) -> std::result::Result<bool, PriorError> {
        let length = i64::try_from(body.len()).unwrap_or(i64::MAX);
        if located.inflated == 0 {
            if located.length != length {
                return Ok(false);
            }
            self.bytes(located)?;
            let same = self.stored == body;
            self.trim();
            return Ok(same);
        }
        if located.inflated != length {
            return Ok(false);
        }
        self.bytes(located)?;
        let same = self
            .fragments
            .inflate_fragment(&self.stored, Some(body.len()))
            .is_ok_and(|plain| plain == body);
        self.trim();
        Ok(same)
    }

    /// [`read_arena`] through the reader's own zlib stream and output buffer.
    pub fn read_arena_at(
        &mut self,
        arena: &Arena,
        located: FragmentAt<'_>,
        dictionary: &[u8],
    ) -> std::result::Result<PyValue, PriorError> {
        let packed = arena_bytes(arena, located)?;
        let fragment = checked_arena_fragment(&mut self.arena, located, &packed, dictionary);
        self.arena.trim();
        fragment
    }
}

/// The text of an arena read refused for `why`.
fn arena_refusal(located: FragmentAt<'_>, why: &str) -> String {
    format!(
        "the arena's bytes {}+{} do not hold unit {}: {why}",
        located.start, located.length, located.unit_id
    )
}

/// [`arena_fragment`]'s checks through `inflater`, a zlib-framed [`StreamInflater`].
fn checked_arena_fragment(
    inflater: &mut StreamInflater,
    located: FragmentAt<'_>,
    packed: &[u8],
    dictionary: &[u8],
) -> std::result::Result<PyValue, PriorError> {
    let end =
        inflater
            .decompress(packed, 0, Some(dictionary))
            .map_err(|failure| match failure {
                StreamFailure::Start(failure) => PriorError::Os(io::Error::other(failure)),
                StreamFailure::Inflate(failure) => PriorError::Value(arena_refusal(
                    located,
                    &format!("it does not inflate ({failure})"),
                )),
            })?;
    if !end.eof || end.unused != 0 {
        return Err(PriorError::Value(arena_refusal(
            located,
            "its deflated stream does not end where the span ends",
        )));
    }
    let fragment = json::loads_bytes(inflater.output()).map_err(PriorError::Json)?;
    if !holds_stamp(&fragment, located) {
        return Err(PriorError::Value(arena_refusal(
            located,
            "it holds another unit or stamp",
        )));
    }
    Ok(fragment)
}

/// The arena read's checks over the deflated bytes `packed` read at `located`: exactly one zlib stream under `dictionary`, ending where `packed` ends, whose fragment is the unit with the stamp `located` carries.
pub fn arena_fragment(
    located: &PriorFragment,
    packed: &[u8],
    dictionary: &[u8],
) -> std::result::Result<PyValue, PriorError> {
    checked_arena_fragment(
        &mut StreamInflater::new(ARENA_WINDOW),
        located.at(),
        packed,
        dictionary,
    )
}

/// The deflated bytes of the span of `arena` at `located`, under the part name of its spill file ([`ArenaSpan::part`]).
fn arena_bytes(arena: &Arena, located: FragmentAt<'_>) -> std::result::Result<Vec<u8>, PriorError> {
    let span = match (
        Arena::file_of(located.part),
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
    arena
        .read(span)
        .map_err(|failure| PriorError::Os(io::Error::other(failure)))
}

/// The recomputed fragment at `located`, a span of `arena` under the part name of its spill file ([`ArenaSpan::part`]), inflated with `dictionary`, parsed and checked ([`arena_fragment`]).
pub fn read_arena(
    arena: &Arena,
    located: &PriorFragment,
    dictionary: &[u8],
) -> std::result::Result<PyValue, PriorError> {
    let packed = arena_bytes(arena, located.at())?;
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
    fn a_field_is_found_wherever_it_occurs_searched_from_either_end() {
        let body = b"{\"id\": \"u-a\", \"content_key\": null, \"id\": \"u-b\"}";
        let mut needle = String::new();
        for from_end in [false, true] {
            let mut holds = |body: &[u8], key: &str, value: Option<&str>| {
                holds_field(body, key, value, &mut needle, from_end)
            };
            assert!(holds(body, "id", Some("u-a")));
            assert!(holds(body, "id", Some("u-b")));
            assert!(holds(body, "content_key", None));
            assert!(!holds(body, "id", Some("u-c")));
            assert!(!holds(body, "id", Some("u-")));
            assert!(!holds(body, "content_key", Some("k")));
            assert!(!holds(b"\"id\": \"u-", "id", Some("u-a")));
            assert!(holds(
                "\"k\": \"\\u00e9\\\"\"".as_bytes(),
                "k",
                Some("\u{e9}\"")
            ));
            assert!(occurs(b"aXa", b"a", from_end) && !occurs(b"ab", b"abc", from_end));
        }
        assert!(holds_unit(body, "u-b") && !holds_unit(body, "u-"));
    }

    #[test]
    fn deflated_fragments_read_back_one_after_another_through_one_stream() {
        use super::super::gzip::deflate_fragment;
        let directory = scratch("deflated");
        let first = b"\n {\n  \"id\": \"u-a\",\n  \"content_key\": \"k\"\n }".to_vec();
        let second = b"\n {\n  \"id\": \"u-b\",\n  \"content_key\": \"k\"\n }".to_vec();
        let one = deflate_fragment(&first).expect("deflate");
        let two = deflate_fragment(&second).expect("deflate");
        let part = [b"[".as_slice(), &one, b",", &two, b"\n]\n"].concat();
        fs::write(directory.join("units/d.json"), &part).expect("write the part");
        let mut reader = PriorFragmentReader::new(&directory);
        let at_one = located("units/d.json", 1, one.len() as i64, first.len() as i64);
        let at_two = PriorFragment {
            unit_id: "u-b".to_owned(),
            start: 2 + one.len() as i64,
            length: two.len() as i64,
            inflated: second.len() as i64,
            ..at_one.clone()
        };
        for _ in 0..2 {
            let bytes = reader.read_bytes(&at_one).expect("read the bytes");
            assert_eq!(bytes, one);
            reader.recycle(bytes);
            assert_eq!(reader.read_bytes(&at_two).expect("read the bytes"), two);
            assert!(reader.same_bytes(&at_two, &second).expect("compare"));
            assert!(!reader.same_bytes(&at_two, &first).expect("compare"));
            let read = reader.read(&at_one).expect("read");
            assert!(matches!(lookup(&read, "id"), Some(PyValue::Str(id)) if id == "u-a"));
        }
        let swapped = PriorFragment {
            unit_id: "u-a".to_owned(),
            ..at_two.clone()
        };
        assert_eq!(
            reader
                .read_bytes(&swapped)
                .expect_err("refused")
                .to_string(),
            PriorFragmentReader::refusal(swapped.at())
        );
        let short = PriorFragment {
            inflated: second.len() as i64 - 1,
            ..at_two.clone()
        };
        assert_eq!(
            reader.read_bytes(&short).expect_err("refused").to_string(),
            format!(
                "{} (it inflates past its {} bytes)",
                PriorFragmentReader::refusal(short.at()),
                second.len() - 1
            )
        );
        assert!(reader.read_bytes(&at_two).is_ok());
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
