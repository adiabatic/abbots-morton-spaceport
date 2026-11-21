//! The parent's zlib and gzip containers, byte for byte as the Python build writes them (`rebuild/CORPUS-PARENT-PORT-PLAN.md` § Containers), and its readers with Python's tolerances (§ Reading what Python wrote), all through the system zlib the crate links (`ffi`), which deflates the same stream however its input is cut into calls.
//!
//! Writers: a `gzip.GzipFile` stream at a level and mtime under a file name ([`GzipWriter`], whose header [`header`] writes), a `gzip.compress` member ([`compress_member`]), the index's run of members cut at a fixed size and deflated on threads (`unit_index._Members`, [`Members`]) and a fragment's raw deflate (`unit_index.deflate_fragment`, [`deflate_fragment`]). Each file's name and level are the constants below. Readers: `zlib.decompressobj` with its `unconsumed_tail` and `unused_data` ([`Decompressor`]), a deflated fragment checked (`unit_index.inflate_fragment`, [`inflate_fragment`]) and a whole gzip file read as `gzip.GzipFile` reads it ([`GzipReader`], [`read_gzip`]), each failing with the class and text Python raises.
//!
//! [`stage_containers`] is the `containers` stage of the stage fixtures.

use std::collections::{HashMap, VecDeque};
use std::fs::File;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::Path;
use std::thread::JoinHandle;

use ams_m1_kernel::emit::json_string;
use serde_json::Value;
use sha2::Digest as _;

use crate::ffi::{self, Compressor, Inflater};
use crate::fragment::sha256_hex;
use crate::pyfmt::PyValue;
use crate::wire::{Result, WireError, error};

/// Every gzip header's MTIME.
pub const MTIME: u32 = 0;
/// Every gzip header's OS byte, which `gzip.GzipFile` writes and `gzip.compress` sets.
pub const OS_UNKNOWN: u8 = 255;
/// A deflated fragment's level (`unit_index.deflate_fragment`).
pub const FRAGMENT_LEVEL: i32 = 1;
/// A store or index part's level, a base's, a segment's and the signature store's (`row_delta.write_part`, `row_delta.StreamedPart`, `unit_cache._write_signature_lines`).
pub const PART_LEVEL: i32 = 1;
/// The name a unit store base's header carries whatever the part's name (`unit_cache._BASE_GZIP_NAME`).
pub const STORE_BASE_GZIP_NAME: &str = "unit-cache.ndjson.gz.partial";
/// The name the signature store's header is written under, which loses its `.gz` there (`unit_cache.SIGNATURE_STORE_NAME`).
pub const SIGNATURE_STORE_GZIP_NAME: &str = "ink-signatures.tsv.gz";
/// The app sidecars' level, the two streams' and the locator blocks' (`app_index.COMPRESS_LEVEL`).
pub const APP_LEVEL: i32 = 6;
/// The name the app index's header carries, its staging file's (`app_index.write_app_artifacts_lines`).
pub const APP_INDEX_GZIP_NAME: &str = "app-units.ndjson.gz.partial";
/// The name the locator table's header carries, its staging file's.
pub const LOCATOR_GZIP_NAME: &str = "app-locator.ndjson.gz.partial";
/// An index member's level (`unit_index._deflate_member`).
pub const INDEX_MEMBER_LEVEL: i32 = 1;
/// The `EOFError` text for a gzip file that ends inside a member (`gzip._read_exact`, `gzip._GzipReader.read`).
pub const ENDED_EARLY: &str = "Compressed file ended before the end-of-stream marker was reached";
/// The `containers` stage's directory of the bytes its lines name by sha256, beside the stage file.
pub const STAGE_BLOBS: &str = "containers.blobs";

const RAW_WINDOW: i32 = -15;
const GZIP_WINDOW: i32 = 31;
const MAGIC: [u8; 2] = [0x1f, 0x8b];
const FHCRC: u8 = 2;
const FEXTRA: u8 = 4;
const FNAME: u8 = 8;
const FCOMMENT: u8 = 16;
/// The buffer a `gzip.GzipFile` gathers its writes in before it deflates them (`gzip._WRITE_BUFFER_SIZE`).
const WRITE_BUFFER_SIZE: usize = 512 * 1024;
/// What the reader takes from its source at once (`gzip.READ_BUFFER_SIZE`).
const READ_BUFFER_SIZE: usize = 128 * 1024;
/// The output a [`Decompressor`] call with no limit grows by at once.
const DECOMPRESS_STEP: usize = 64 * 1024;

/// The bytes a gzip header names a stream by, as `gzip.GzipFile._write_gzip_header` takes them from its name: the base name, as Latin-1, with a `.gz` suffix dropped, or nothing when the name is not Latin-1.
pub fn header_name(name: &str) -> Vec<u8> {
    let base = name.rsplit('/').next().unwrap_or(name);
    let Some(mut bytes) = base
        .chars()
        .map(|letter| u8::try_from(u32::from(letter)).ok())
        .collect::<Option<Vec<u8>>>()
    else {
        return Vec::new();
    };
    if bytes.ends_with(b".gz") {
        bytes.truncate(bytes.len() - 3);
    }
    bytes
}

/// A `gzip.GzipFile` stream's header: the magic, deflate, FNAME when the name gives bytes, MTIME, XFL (2 at level 9, 4 at level 1, otherwise 0), OS 255, and the name with its NUL.
pub fn header(name: &str, level: i32, mtime: u32) -> Vec<u8> {
    let name = header_name(name);
    let mut header = vec![
        MAGIC[0],
        MAGIC[1],
        8,
        if name.is_empty() { 0 } else { FNAME },
    ];
    header.extend_from_slice(&mtime.to_le_bytes());
    header.push(match level {
        9 => 2,
        1 => 4,
        _ => 0,
    });
    header.push(OS_UNKNOWN);
    if !name.is_empty() {
        header.extend_from_slice(&name);
        header.push(0);
    }
    header
}

/// A `gzip.GzipFile` opened for writing: the header at once, the writes gathered as its `io.BufferedWriter` gathers them, in a buffer of [`WRITE_BUFFER_SIZE`] that a write which does not fit empties first and a write longer than it passes, and each run deflated with no flush into the sink; at [`GzipWriter::finish`] the stream's end, its CRC-32 and its length modulo 2^32. [`Write::flush`] flushes only the sink and adds no sync point to the stream.
pub struct GzipWriter<W: Write> {
    sink: W,
    compressor: Compressor,
    crc: u32,
    size: u64,
    gathered: Vec<u8>,
    deflated: Vec<u8>,
}

impl<W: Write> GzipWriter<W> {
    /// Write [`header`] for `name`, `level` and `mtime` to `sink` and open the stream.
    pub fn new(mut sink: W, name: &str, level: i32, mtime: u32) -> io::Result<Self> {
        let compressor = Compressor::new(level, RAW_WINDOW).map_err(io::Error::other)?;
        sink.write_all(&header(name, level, mtime))?;
        Ok(Self {
            sink,
            compressor,
            crc: 0,
            size: 0,
            gathered: Vec::new(),
            deflated: Vec::new(),
        })
    }

    /// Deflate one run of the stream's bytes into the sink, as `GzipFile._write_raw` does.
    fn deflate(&mut self, data: &[u8]) -> io::Result<()> {
        if data.is_empty() {
            return Ok(());
        }
        self.deflated.clear();
        self.compressor
            .compress(data, &mut self.deflated)
            .map_err(io::Error::other)?;
        self.sink.write_all(&self.deflated)?;
        self.crc = ffi::crc32_of(self.crc, data);
        self.size += data.len() as u64;
        Ok(())
    }

    fn drain(&mut self) -> io::Result<()> {
        let gathered = std::mem::take(&mut self.gathered);
        self.deflate(&gathered)?;
        self.gathered = gathered;
        self.gathered.clear();
        Ok(())
    }

    /// End the stream and return the sink.
    pub fn finish(mut self) -> io::Result<W> {
        self.drain()?;
        self.deflated.clear();
        self.compressor
            .finish(&mut self.deflated)
            .map_err(io::Error::other)?;
        self.sink.write_all(&self.deflated)?;
        self.sink.write_all(&self.crc.to_le_bytes())?;
        self.sink
            .write_all(&((self.size & 0xffff_ffff) as u32).to_le_bytes())?;
        Ok(self.sink)
    }
}

impl<W: Write> Write for GzipWriter<W> {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        if data.len() <= WRITE_BUFFER_SIZE - self.gathered.len() {
            self.gathered.extend_from_slice(data);
            return Ok(data.len());
        }
        self.drain()?;
        if data.len() > WRITE_BUFFER_SIZE {
            self.deflate(data)?;
        } else {
            self.gathered.extend_from_slice(data);
        }
        Ok(data.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        self.sink.flush()
    }
}

/// `piece` as one gzip member, as `gzip.compress(piece, level, mtime=mtime)` writes it: zlib's own gzip framing (no name, XFL from zlib), with MTIME set and the OS byte 255.
pub fn compress_member(
    piece: &[u8],
    level: i32,
    mtime: u32,
) -> std::result::Result<Vec<u8>, String> {
    let mut member = ffi::compress(piece, level, GZIP_WINDOW)?;
    member[4..8].copy_from_slice(&mtime.to_le_bytes());
    member[9] = OS_UNKNOWN;
    Ok(member)
}

/// A fragment's shard bytes as a shard part holds them deflated, as `unit_index.deflate_fragment` deflates them: raw deflate at level 1, no header, trailer or dictionary.
pub fn deflate_fragment(body: &[u8]) -> std::result::Result<Vec<u8>, String> {
    ffi::compress(body, FRAGMENT_LEVEL, RAW_WINDOW)
}

/// The sink an index base is written into, as `unit_index._Members` writes one: the bytes written, cut at every multiple of `member_bytes` into gzip members ([`compress_member`] at level 1, mtime 0) deflated on up to `threads` threads and written to `raw` in order, with their size and sha256 counted. A write that ends with nothing written writes one empty member. A member that would be one more in flight than `threads` waits for the oldest to be written.
pub struct Members<W: Write> {
    raw: W,
    member_bytes: usize,
    threads: usize,
    filling: Vec<u8>,
    deflating: VecDeque<JoinHandle<std::result::Result<Vec<u8>, String>>>,
    members: usize,
    digest: sha2::Sha256,
    size: u64,
}

impl<W: Write> Members<W> {
    pub fn new(raw: W, member_bytes: usize, threads: usize) -> Self {
        let member_bytes = member_bytes.max(1);
        Self {
            raw,
            member_bytes,
            threads: threads.max(1),
            filling: Vec::new(),
            deflating: VecDeque::new(),
            members: 0,
            digest: sha2::Sha256::new(),
            size: 0,
        }
    }

    fn hand(&mut self, plain: Vec<u8>) -> io::Result<()> {
        if self.deflating.len() >= self.threads {
            self.take()?;
        }
        self.deflating.push_back(std::thread::spawn(move || {
            compress_member(&plain, INDEX_MEMBER_LEVEL, MTIME)
        }));
        self.members += 1;
        Ok(())
    }

    fn take(&mut self) -> io::Result<()> {
        let Some(deflating) = self.deflating.pop_front() else {
            return Ok(());
        };
        let member = deflating
            .join()
            .map_err(|_| io::Error::other("a member's deflate panicked"))?
            .map_err(io::Error::other)?;
        self.raw.write_all(&member)?;
        self.digest.update(&member);
        self.size += member.len() as u64;
        Ok(())
    }

    /// Write the last member, and an empty one when nothing was written, wait for every member still deflating, and return the sink with the bytes written to it and their sha256.
    pub fn finish(mut self) -> io::Result<(W, u64, String)> {
        if !self.filling.is_empty() || self.members == 0 {
            let last = std::mem::take(&mut self.filling);
            self.hand(last)?;
        }
        while !self.deflating.is_empty() {
            self.take()?;
        }
        let digest: String = self
            .digest
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        Ok((self.raw, self.size, digest))
    }
}

impl<W: Write> Write for Members<W> {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        let mut rest = data;
        while !rest.is_empty() {
            if self.filling.capacity() == 0 {
                self.filling.reserve_exact(self.member_bytes);
            }
            let room = self.member_bytes - self.filling.len();
            let (piece, tail) = rest.split_at(room.min(rest.len()));
            self.filling.extend_from_slice(piece);
            rest = tail;
            if self.filling.len() == self.member_bytes {
                let full = std::mem::take(&mut self.filling);
                self.hand(full)?;
            }
        }
        Ok(data.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// A `zlib.error` from a decompressor, with Python's text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ZlibError(pub String);

impl std::fmt::Display for ZlibError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

/// CPython's `zlib_error` for a decompressor: zlib's message, or a fixed one for three statuses when zlib gives none, cut at 200 bytes.
fn zlib_error(status: i32, message: Option<String>) -> ZlibError {
    let message = message.or_else(|| {
        match status {
            -5 => Some("incomplete or truncated stream"),
            -2 => Some("inconsistent stream state"),
            -3 => Some("invalid input data"),
            _ => None,
        }
        .map(str::to_owned)
    });
    ZlibError(match message {
        Some(message) => format!(
            "Error {status} while decompressing data: {}",
            message.chars().take(200).collect::<String>()
        ),
        None => format!("Error {status} while decompressing data"),
    })
}

/// `zlib.decompressobj(window_bits)` as the parent's readers drive it: [`Decompressor::decompress`] with an optional output limit, and the input left over in [`Decompressor::unconsumed_tail`] when the limit stopped it, or in [`Decompressor::unused_data`] after the stream's end, by CPython's rules for both.
pub struct Decompressor {
    inflater: Inflater,
    eof: bool,
    unused_data: Vec<u8>,
    unconsumed_tail: Vec<u8>,
}

impl Decompressor {
    pub fn new(window_bits: i32) -> std::result::Result<Self, String> {
        Ok(Self {
            inflater: Inflater::new(window_bits)?,
            eof: false,
            unused_data: Vec::new(),
            unconsumed_tail: Vec::new(),
        })
    }

    pub fn eof(&self) -> bool {
        self.eof
    }

    pub fn unused_data(&self) -> &[u8] {
        &self.unused_data
    }

    pub fn unconsumed_tail(&self) -> &[u8] {
        &self.unconsumed_tail
    }

    /// Inflate `data`, giving at most `max_length` bytes when it is not 0, as `Decompress.decompress(data, max_length)` does. The stream's end sets [`Decompressor::eof`] and adds what follows it to `unused_data`; input the limit left is `unconsumed_tail`, which a call that consumes everything empties.
    pub fn decompress(
        &mut self,
        data: &[u8],
        max_length: usize,
    ) -> std::result::Result<Vec<u8>, ZlibError> {
        let mut out = Vec::new();
        let mut at = 0;
        let mut ended = false;
        let mut failure = None;
        loop {
            let room = if max_length > 0 {
                (max_length - out.len()).min(DECOMPRESS_STEP.max(data.len()))
            } else {
                DECOMPRESS_STEP.max(data.len().saturating_mul(2))
            };
            if room == 0 {
                break;
            }
            let start = out.len();
            out.resize(start + room, 0);
            let step = self.inflater.inflate(&data[at..], &mut out[start..]);
            out.truncate(start + step.produced);
            at += step.consumed;
            if step.failed() {
                failure = Some(zlib_error(step.status, step.message));
                break;
            }
            if step.ended() {
                ended = true;
                break;
            }
            if step.produced < room && at == data.len() {
                break;
            }
        }
        let left = &data[at..];
        if ended && !left.is_empty() {
            self.unused_data.extend_from_slice(left);
        }
        if (!ended && !left.is_empty()) || !self.unconsumed_tail.is_empty() {
            self.unconsumed_tail = left.to_vec();
        }
        if ended {
            self.eof = true;
        }
        match failure {
            Some(failure) => Err(failure),
            None => Ok(out),
        }
    }
}

/// A deflated fragment's plain bytes, checked as `unit_index.inflate_fragment` checks them: exactly one raw deflate stream ending where `stored` ends, inflated at most one byte past `length` when it is given, and to `length` bytes. The refusal is the ValueError's text.
pub fn inflate_fragment(
    stored: &[u8],
    length: Option<usize>,
) -> std::result::Result<Vec<u8>, String> {
    let mut inflater = Decompressor::new(RAW_WINDOW)?;
    let body = inflater
        .decompress(stored, length.map_or(0, |length| length + 1))
        .map_err(|failure| format!("it does not inflate ({failure})"))?;
    if let Some(length) = length
        && body.len() > length
    {
        return Err(format!("it inflates past its {length} bytes"));
    }
    if !inflater.eof() {
        return Err("its deflated stream is cut short".to_owned());
    }
    if !inflater.unused_data().is_empty() {
        return Err("bytes follow the end of its deflated stream".to_owned());
    }
    if let Some(length) = length
        && body.len() != length
    {
        return Err(format!("it inflates to {} bytes, not {length}", body.len()));
    }
    Ok(body)
}

/// What Python's gzip reader raises, by class, with its text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum GzipError {
    /// `EOFError`: the file ends inside a member.
    Eof(String),
    /// `gzip.BadGzipFile`: a header, trailer or trailing bytes that are not gzip.
    BadGzipFile(String),
    /// `zlib.error`: deflate data that does not inflate.
    Zlib(String),
}

impl GzipError {
    /// The class Python raises, by its `__name__`.
    pub fn class(&self) -> &'static str {
        match self {
            Self::Eof(_) => "EOFError",
            Self::BadGzipFile(_) => "BadGzipFile",
            Self::Zlib(_) => "error",
        }
    }

    pub fn message(&self) -> &str {
        match self {
            Self::Eof(message) | Self::BadGzipFile(message) | Self::Zlib(message) => message,
        }
    }

    /// The gzip refusal an I/O error from a [`GzipReader`] carries, or None for a failure of its source.
    pub fn of(failure: &io::Error) -> Option<&Self> {
        failure.get_ref()?.downcast_ref::<Self>()
    }
}

impl std::fmt::Display for GzipError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: {}", self.class(), self.message())
    }
}

impl std::error::Error for GzipError {}

fn refuse(failure: GzipError) -> io::Error {
    io::Error::other(failure)
}

/// Python's `repr` of a few bytes, for `BadGzipFile`'s text.
fn bytes_repr(bytes: &[u8]) -> String {
    let quote = if bytes.contains(&b'\'') && !bytes.contains(&b'"') {
        '"'
    } else {
        '\''
    };
    let mut text = format!("b{quote}");
    for &byte in bytes {
        match byte {
            b'\\' => text.push_str("\\\\"),
            b'\t' => text.push_str("\\t"),
            b'\n' => text.push_str("\\n"),
            b'\r' => text.push_str("\\r"),
            b'\'' if quote == '\'' => text.push_str("\\'"),
            0x20..=0x7e => text.push(char::from(byte)),
            _ => text.push_str(&format!("\\x{byte:02x}")),
        }
    }
    text.push(quote);
    text
}

/// A whole gzip file read as `gzip.GzipFile` reads one: its members in order as one stream, each header's FEXTRA, FNAME, FCOMMENT and FHCRC skipped (the header CRC unchecked), each trailer's CRC-32 and length checked, NUL bytes after a member skipped, and an empty source read as nothing. The file ending inside a member is an `EOFError`, a bad header, trailer or trailing bytes a `BadGzipFile` and bad deflate data a `zlib.error`, each with Python's text, carried as a [`GzipError`] in the I/O error.
pub struct GzipReader<R: Read> {
    source: R,
    input: Vec<u8>,
    at: usize,
    inflater: Option<Inflater>,
    ended: bool,
    done: bool,
    crc: u32,
    size: u64,
}

impl<R: Read> GzipReader<R> {
    pub fn new(source: R) -> Self {
        Self {
            source,
            input: Vec::new(),
            at: 0,
            inflater: None,
            ended: false,
            done: false,
            crc: 0,
            size: 0,
        }
    }

    /// Make sure unread input is held, reading more from the source when none is; false at the source's end.
    fn fill(&mut self) -> io::Result<bool> {
        if self.at < self.input.len() {
            return Ok(true);
        }
        self.input.resize(READ_BUFFER_SIZE, 0);
        self.at = 0;
        let read = loop {
            match self.source.read(&mut self.input) {
                Ok(read) => break read,
                Err(failure) if failure.kind() == io::ErrorKind::Interrupted => {}
                Err(failure) => {
                    self.input.clear();
                    return Err(failure);
                }
            }
        };
        self.input.truncate(read);
        Ok(read > 0)
    }

    /// Up to `wanted` bytes, fewer only at the source's end.
    fn take(&mut self, wanted: usize) -> io::Result<Vec<u8>> {
        let mut taken = Vec::with_capacity(wanted);
        while taken.len() < wanted && self.fill()? {
            let count = (wanted - taken.len()).min(self.input.len() - self.at);
            taken.extend_from_slice(&self.input[self.at..self.at + count]);
            self.at += count;
        }
        Ok(taken)
    }

    fn take_exact(&mut self, wanted: usize) -> io::Result<Vec<u8>> {
        let taken = self.take(wanted)?;
        if taken.len() < wanted {
            return Err(refuse(GzipError::Eof(ENDED_EARLY.to_owned())));
        }
        Ok(taken)
    }

    /// Skip a NUL-terminated header field, or everything to the source's end when no NUL comes.
    fn skip_string(&mut self) -> io::Result<()> {
        while self.fill()? {
            let unread = &self.input[self.at..];
            if let Some(found) = unread.iter().position(|&byte| byte == 0) {
                self.at += found + 1;
                return Ok(());
            }
            self.at = self.input.len();
        }
        Ok(())
    }

    /// Read a member's header (`gzip._read_gzip_header`), or return false at the source's end.
    fn header(&mut self) -> io::Result<bool> {
        let magic = self.take(2)?;
        if magic.is_empty() {
            return Ok(false);
        }
        if magic != MAGIC {
            return Err(refuse(GzipError::BadGzipFile(format!(
                "Not a gzipped file ({})",
                bytes_repr(&magic)
            ))));
        }
        let fixed = self.take_exact(8)?;
        if fixed[0] != 8 {
            return Err(refuse(GzipError::BadGzipFile(
                "Unknown compression method".to_owned(),
            )));
        }
        let flags = fixed[1];
        if flags & FEXTRA != 0 {
            let length = self.take_exact(2)?;
            self.take_exact(usize::from(u16::from_le_bytes([length[0], length[1]])))?;
        }
        if flags & FNAME != 0 {
            self.skip_string()?;
        }
        if flags & FCOMMENT != 0 {
            self.skip_string()?;
        }
        if flags & FHCRC != 0 {
            self.take_exact(2)?;
        }
        Ok(true)
    }

    /// Check a member's trailer against what it inflated to (`gzip._GzipReader._read_eof`) and skip the NUL bytes after it.
    fn trailer(&mut self) -> io::Result<()> {
        let trailer = self.take_exact(8)?;
        let stored = u32::from_le_bytes([trailer[0], trailer[1], trailer[2], trailer[3]]);
        let length = u32::from_le_bytes([trailer[4], trailer[5], trailer[6], trailer[7]]);
        if stored != self.crc {
            return Err(refuse(GzipError::BadGzipFile(format!(
                "CRC check failed {stored:#x} != {:#x}",
                self.crc
            ))));
        }
        if u64::from(length) != self.size & 0xffff_ffff {
            return Err(refuse(GzipError::BadGzipFile(
                "Incorrect length of data produced".to_owned(),
            )));
        }
        while self.fill()? {
            if self.input[self.at] != 0 {
                break;
            }
            self.at += 1;
        }
        Ok(())
    }
}

impl<R: Read> Read for GzipReader<R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if buf.is_empty() {
            return Ok(0);
        }
        loop {
            if self.done {
                return Ok(0);
            }
            if self.ended {
                self.trailer()?;
                self.inflater = None;
                self.ended = false;
            }
            if self.inflater.is_none() {
                if !self.header()? {
                    self.done = true;
                    return Ok(0);
                }
                self.inflater = Some(Inflater::new(RAW_WINDOW).map_err(io::Error::other)?);
                self.crc = 0;
                self.size = 0;
            }
            let more = self.fill()?;
            let inflater = self.inflater.as_mut().expect("a member is open");
            let step = inflater.inflate(&self.input[self.at..], buf);
            self.at += step.consumed;
            if step.failed() {
                return Err(refuse(GzipError::Zlib(
                    zlib_error(step.status, step.message).0,
                )));
            }
            self.ended = step.ended();
            if step.produced > 0 {
                self.crc = ffi::crc32_of(self.crc, &buf[..step.produced]);
                self.size += step.produced as u64;
                return Ok(step.produced);
            }
            if !self.ended && !more {
                return Err(refuse(GzipError::Eof(ENDED_EARLY.to_owned())));
            }
        }
    }
}

/// A whole gzip file's bytes read as [`GzipReader`] reads them.
pub fn read_gzip(data: &[u8]) -> std::result::Result<Vec<u8>, GzipError> {
    let mut plain = Vec::new();
    match GzipReader::new(data).read_to_end(&mut plain) {
        Ok(_) => Ok(plain),
        Err(failure) => Err(GzipError::of(&failure)
            .cloned()
            .unwrap_or_else(|| GzipError::Eof(failure.to_string()))),
    }
}

fn fail(what: &str, failure: impl std::fmt::Display) -> WireError {
    WireError(format!("cannot {what}: {failure}"))
}

/// The stage's blobs, each read once from the blob directory and checked against its name.
struct Blobs<'a> {
    directory: &'a Path,
    read: HashMap<String, Vec<u8>>,
}

impl Blobs<'_> {
    /// A line's input, `(sha256, length)`: the first `length` bytes of the blob of that digest.
    fn input(&mut self, value: &PyValue, key: &str) -> Result<&[u8]> {
        let PyValue::Tuple(parts) = value else {
            return error(format!("{key}: the input is {}", value.kind()));
        };
        let [PyValue::Str(digest), PyValue::Int(length)] = parts.as_slice() else {
            return error(format!("{key}: the input is {}", value.to_typed()));
        };
        if !self.read.contains_key(digest) {
            let path = self.directory.join(digest);
            let bytes = std::fs::read(&path)
                .map_err(|failure| fail(&format!("read {}", path.display()), failure))?;
            if sha256_hex(&bytes) != *digest {
                return error(format!(
                    "{} does not hold the bytes it is named for",
                    path.display()
                ));
            }
            self.read.insert(digest.clone(), bytes);
        }
        let bytes = &self.read[digest];
        usize::try_from(*length)
            .ok()
            .and_then(|length| bytes.get(..length))
            .ok_or_else(|| WireError(format!("{key}: the blob {digest} holds no {length} bytes")))
    }
}

fn typed(record: &Value, name: &str, key: &str) -> Result<PyValue> {
    PyValue::from_typed(&record[name])
        .map_err(|failure| WireError(format!("{key} {name}: {failure}")))
}

fn integer(record: &Value, name: &str, key: &str) -> Result<i64> {
    match typed(record, name, key)? {
        PyValue::Int(value) => Ok(value),
        other => error(format!("{key}: {name} is {}", other.kind())),
    }
}

fn count(record: &Value, name: &str, key: &str) -> Result<usize> {
    usize::try_from(integer(record, name, key)?)
        .map_err(|_| WireError(format!("{key}: {name} is negative")))
}

fn level(record: &Value, key: &str) -> Result<i32> {
    i32::try_from(integer(record, "level", key)?)
        .map_err(|_| WireError(format!("{key}: the level is out of range")))
}

fn mtime(record: &Value, key: &str) -> Result<u32> {
    u32::try_from(integer(record, "mtime", key)?)
        .map_err(|_| WireError(format!("{key}: the mtime is out of range")))
}

/// The line's writes, each a size, cut from `data` in order; their sum must be its length.
fn cuts<'a>(record: &Value, key: &str, data: &'a [u8]) -> Result<Vec<&'a [u8]>> {
    let PyValue::Tuple(sizes) = typed(record, "writes", key)? else {
        return error(format!("{key}: the writes are not a tuple"));
    };
    let mut pieces = Vec::with_capacity(sizes.len());
    let mut rest = data;
    for size in sizes {
        let PyValue::Int(size) = size else {
            return error(format!("{key}: a write's size is {}", size.kind()));
        };
        let size = usize::try_from(size)
            .ok()
            .filter(|&size| size <= rest.len())
            .ok_or_else(|| WireError(format!("{key}: the writes overrun the input")))?;
        let (piece, tail) = rest.split_at(size);
        pieces.push(piece);
        rest = tail;
    }
    if !rest.is_empty() {
        return error(format!("{key}: the writes leave {} bytes", rest.len()));
    }
    Ok(pieces)
}

fn bytes_output(bytes: &[u8]) -> PyValue {
    PyValue::Tuple(vec![
        PyValue::str("bytes"),
        PyValue::str(sha256_hex(bytes)),
        PyValue::Int(bytes.len() as i64),
    ])
}

fn raised(class: &str, message: &str) -> PyValue {
    PyValue::Tuple(vec![
        PyValue::str("raised"),
        PyValue::str(class),
        PyValue::str(message),
    ])
}

fn written(result: io::Result<Vec<u8>>, key: &str) -> Result<PyValue> {
    result
        .map(|bytes| bytes_output(&bytes))
        .map_err(|failure| WireError(format!("{key}: cannot write the container: {failure}")))
}

/// The member readers' loop (`audit_stream._MemberReader`, `row_delta._Inflating`) over `data` handed in `piece`-byte reads: each call inflates what the last left unconsumed, or the next read, at most `max_length` bytes, until the stream ends or nothing is left. Each call's output, tail and unused lengths; then what the stream gave, whether it ended and what follows it, or the `zlib.error`.
fn member_read(
    data: &[u8],
    window_bits: i32,
    piece: usize,
    max_length: usize,
) -> std::result::Result<PyValue, String> {
    let mut inflater = Decompressor::new(window_bits)?;
    let mut steps = Vec::new();
    let mut plain = Vec::new();
    let mut at = 0;
    let mut pending: Vec<u8> = Vec::new();
    let steps_of = |steps: &Vec<PyValue>| PyValue::Tuple(steps.clone());
    while !inflater.eof() {
        if pending.is_empty() && at < data.len() {
            let end = (at + piece.max(1)).min(data.len());
            pending = data[at..end].to_vec();
            at = end;
        }
        let out = match inflater.decompress(&pending, max_length) {
            Ok(out) => out,
            Err(failure) => {
                return Ok(PyValue::Tuple(vec![
                    PyValue::str("raised"),
                    PyValue::str("error"),
                    PyValue::str(failure.0),
                    steps_of(&steps),
                ]));
            }
        };
        pending = inflater.unconsumed_tail().to_vec();
        plain.extend_from_slice(&out);
        steps.push(PyValue::Tuple(vec![
            PyValue::Int(out.len() as i64),
            PyValue::Int(pending.len() as i64),
            PyValue::Int(inflater.unused_data().len() as i64),
        ]));
        if out.is_empty() && pending.is_empty() && at >= data.len() {
            break;
        }
    }
    Ok(PyValue::Tuple(vec![
        PyValue::str("member"),
        PyValue::str(sha256_hex(&plain)),
        PyValue::Int(plain.len() as i64),
        PyValue::Bool(inflater.eof()),
        PyValue::Int(inflater.unused_data().len() as i64),
        steps_of(&steps),
    ]))
}

/// One line's answer, by its framing.
fn answer(record: &Value, key: &str, blobs: &mut Blobs<'_>) -> Result<PyValue> {
    let framing = match typed(record, "framing", key)? {
        PyValue::Str(framing) => framing,
        other => return error(format!("{key}: the framing is {}", other.kind())),
    };
    let data = blobs.input(&typed(record, "input", key)?, key)?;
    match framing.as_str() {
        "gzipfile" => {
            let name = match typed(record, "name", key)? {
                PyValue::Str(name) => name,
                other => return error(format!("{key}: the name is {}", other.kind())),
            };
            let pieces = cuts(record, key, data)?;
            let (level, mtime) = (level(record, key)?, mtime(record, key)?);
            written(
                GzipWriter::new(Vec::new(), &name, level, mtime).and_then(|mut stream| {
                    for piece in pieces {
                        stream.write_all(piece)?;
                    }
                    stream.finish()
                }),
                key,
            )
        }
        "compress" => written(
            compress_member(data, level(record, key)?, mtime(record, key)?)
                .map_err(io::Error::other),
            key,
        ),
        "members" => {
            let pieces = cuts(record, key, data)?;
            let mut members = Members::new(
                Vec::new(),
                count(record, "member_bytes", key)?,
                count(record, "member_threads", key)?,
            );
            written(
                pieces
                    .into_iter()
                    .try_for_each(|piece| members.write_all(piece))
                    .and_then(|()| members.finish())
                    .map(|(raw, _, _)| raw),
                key,
            )
        }
        "raw" => written(deflate_fragment(data).map_err(io::Error::other), key),
        "inflate-fragment" => {
            let length = match typed(record, "length", key)? {
                PyValue::None => None,
                PyValue::Int(length) => Some(
                    usize::try_from(length)
                        .map_err(|_| WireError(format!("{key}: the length is negative")))?,
                ),
                other => return error(format!("{key}: the length is {}", other.kind())),
            };
            Ok(match inflate_fragment(data, length) {
                Ok(body) => bytes_output(&body),
                Err(refusal) => raised("ValueError", &refusal),
            })
        }
        "read" => Ok(match read_gzip(data) {
            Ok(plain) => bytes_output(&plain),
            Err(failure) => raised(failure.class(), failure.message()),
        }),
        "member-read" => {
            let window_bits = i32::try_from(integer(record, "wbits", key)?)
                .map_err(|_| WireError(format!("{key}: wbits is out of range")))?;
            member_read(
                data,
                window_bits,
                count(record, "piece", key)?,
                count(record, "max_length", key)?,
            )
            .map_err(|failure| WireError(format!("{key}: {failure}")))
        }
        "crc32" => {
            let start = u32::try_from(integer(record, "start", key)?)
                .map_err(|_| WireError(format!("{key}: the start is out of range")))?;
            Ok(PyValue::Tuple(vec![
                PyValue::str("crc32"),
                PyValue::Int(i64::from(ffi::crc32_of(start, data))),
            ]))
        }
        other => error(format!("{key}: no framing {other:?}")),
    }
}

/// The `containers` stage: for each line of `stage_file`, make the container or read its line names again, from the input the line names by sha256 and length in [`STAGE_BLOBS`] beside the stage file, and write one `{"key", "output"}` line in the typed encoding: `("bytes", sha256, length)` for what a writer wrote or a reader read, `("raised", class, message)` for what a reader refused, the member readers' loop's steps for `member-read`, and `("crc32", value)`.
pub fn stage_containers(stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let directory = stage_file
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(STAGE_BLOBS);
    let mut blobs = Blobs {
        directory: &directory,
        read: HashMap::new(),
    };
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
        let output = answer(&record, key, &mut blobs)?;
        writeln!(
            out,
            "{{\"key\":{},\"output\":{}}}",
            json_string(key),
            output.to_typed()
        )
        .map_err(|failure| fail("write a stage line", failure))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gzip_file(name: &str, level: i32, plain: &[u8]) -> Vec<u8> {
        let mut stream = GzipWriter::new(Vec::new(), name, level, MTIME).expect("opens");
        stream.write_all(plain).expect("writes");
        stream.finish().expect("finishes")
    }

    #[test]
    fn each_files_header_names_it_as_gzip_file_does() {
        assert_eq!(
            header(STORE_BASE_GZIP_NAME, PART_LEVEL, MTIME),
            b"\x1f\x8b\x08\x08\0\0\0\0\x04\xffunit-cache.ndjson.gz.partial\0"
        );
        assert_eq!(
            header(SIGNATURE_STORE_GZIP_NAME, PART_LEVEL, MTIME),
            b"\x1f\x8b\x08\x08\0\0\0\0\x04\xffink-signatures.tsv\0"
        );
        assert_eq!(
            header(APP_INDEX_GZIP_NAME, APP_LEVEL, MTIME),
            b"\x1f\x8b\x08\x08\0\0\0\0\0\xffapp-units.ndjson.gz.partial\0"
        );
        assert_eq!(
            header(LOCATOR_GZIP_NAME, APP_LEVEL, MTIME),
            b"\x1f\x8b\x08\x08\0\0\0\0\0\xffapp-locator.ndjson.gz.partial\0"
        );
        assert_eq!(header("", 9, 7), b"\x1f\x8b\x08\0\x07\0\0\0\x02\xff");
        assert_eq!(
            header_name("out/units-index.ndjson.gz"),
            b"units-index.ndjson"
        );
        assert_eq!(header_name("caf\u{e9}.gz"), b"caf\xe9");
        assert_eq!(header_name("\u{160}.tsv"), b"");
    }

    #[test]
    fn a_stream_is_the_same_however_its_writes_are_cut_and_reads_back() {
        let plain: Vec<u8> = (0..300_000u32)
            .map(|index| (index * 7 % 251) as u8)
            .collect();
        let whole = gzip_file("part", 1, &plain);
        let mut cut = GzipWriter::new(Vec::new(), "part", 1, MTIME).expect("opens");
        for piece in plain.chunks(4093) {
            cut.write_all(piece).expect("writes");
        }
        assert_eq!(cut.finish().expect("finishes"), whole);
        assert_eq!(read_gzip(&whole), Ok(plain.clone()));
        let member = compress_member(&plain, 6, MTIME).expect("compresses");
        assert_eq!(
            (member[3], &member[4..8], member[9]),
            (0, &[0u8; 4][..], 255)
        );
        assert_eq!(read_gzip(&member), Ok(plain));
    }

    #[test]
    fn members_cut_at_their_size_and_an_empty_write_is_one_empty_member() {
        let plain = b"0123456789".repeat(5);
        let mut members = Members::new(Vec::new(), 16, 2);
        for piece in plain.chunks(7) {
            members.write_all(piece).expect("writes");
        }
        let (bytes, size, digest) = members.finish().expect("finishes");
        let expected: Vec<u8> = plain
            .chunks(16)
            .flat_map(|piece| compress_member(piece, 1, MTIME).expect("compresses"))
            .collect();
        assert_eq!(bytes, expected);
        assert_eq!(
            (size, digest),
            (expected.len() as u64, sha256_hex(&expected))
        );
        let (empty, _, _) = Members::new(Vec::new(), 16, 2).finish().expect("finishes");
        assert_eq!(empty, compress_member(b"", 1, MTIME).expect("compresses"));
        let mut exact = Members::new(Vec::new(), 10, 1);
        exact.write_all(&plain[..20]).expect("writes");
        let (bytes, _, _) = exact.finish().expect("finishes");
        assert_eq!(read_gzip(&bytes), Ok(plain[..20].to_vec()));
        assert_eq!(
            bytes.len(),
            2 * compress_member(&plain[..10], 1, 0).expect("c").len()
        );
    }

    #[test]
    fn the_reader_keeps_pythons_tolerances_and_refusals() {
        let one = gzip_file("one.gz", 1, b"first\n");
        let two = compress_member(b"second\n", 6, MTIME).expect("compresses");
        let joined = [one.clone(), vec![0, 0, 0], two.clone(), vec![0]].concat();
        assert_eq!(read_gzip(&joined), Ok(b"first\nsecond\n".to_vec()));
        assert_eq!(read_gzip(b""), Ok(Vec::new()));
        let refused = |data: &[u8]| {
            let failure = read_gzip(data).expect_err("refused");
            (failure.class(), failure.message().to_owned())
        };
        assert_eq!(
            refused(&[one.as_slice(), b"\x1f"].concat()),
            ("BadGzipFile", "Not a gzipped file (b'\\x1f')".to_owned())
        );
        assert_eq!(
            refused(&[one.as_slice(), b"ab"].concat()),
            ("BadGzipFile", "Not a gzipped file (b'ab')".to_owned())
        );
        assert_eq!(
            refused(&one[..one.len() - 3]),
            ("EOFError", ENDED_EARLY.to_owned())
        );
        assert_eq!(refused(&one[..5]), ("EOFError", ENDED_EARLY.to_owned()));
        let mut bad_crc = one.clone();
        let at = bad_crc.len() - 8;
        bad_crc[at] ^= 1;
        assert_eq!(refused(&bad_crc).0, "BadGzipFile");
        assert!(refused(&bad_crc).1.starts_with("CRC check failed 0x"));
        let mut bad_length = one.clone();
        let at = bad_length.len() - 4;
        bad_length[at] ^= 1;
        assert_eq!(
            refused(&bad_length),
            (
                "BadGzipFile",
                "Incorrect length of data produced".to_owned()
            )
        );
        let garbage = [&header("", 1, 0)[..], &[0xff; 20]].concat();
        assert_eq!(
            refused(&garbage),
            (
                "error",
                "Error -3 while decompressing data: invalid block type".to_owned()
            )
        );
        assert_eq!(
            refused(b"\x1f\x8b\x07\0\0\0\0\0\0\xff"),
            ("BadGzipFile", "Unknown compression method".to_owned())
        );
        assert_eq!(bytes_repr(b"it's"), "b\"it's\"");
    }

    #[test]
    fn a_fragment_inflates_only_to_its_recorded_length_and_ends_where_it_is_stored() {
        let body = b"{\"id\": \"u-1\"}".repeat(40);
        let stored = deflate_fragment(&body).expect("deflates");
        assert_eq!(
            inflate_fragment(&stored, Some(body.len())),
            Ok(body.clone())
        );
        assert_eq!(inflate_fragment(&stored, None), Ok(body.clone()));
        assert_eq!(
            inflate_fragment(&stored, Some(body.len() - 1)),
            Err(format!("it inflates past its {} bytes", body.len() - 1))
        );
        assert_eq!(
            inflate_fragment(&stored, Some(body.len() + 1)),
            Err(format!(
                "it inflates to {} bytes, not {}",
                body.len(),
                body.len() + 1
            ))
        );
        assert_eq!(
            inflate_fragment(&stored[..stored.len() - 1], None),
            Err("its deflated stream is cut short".to_owned())
        );
        assert_eq!(
            inflate_fragment(&[stored.as_slice(), b"x"].concat(), None),
            Err("bytes follow the end of its deflated stream".to_owned())
        );
        assert_eq!(
            inflate_fragment(&[0xff; 4], None),
            Err(
                "it does not inflate (Error -3 while decompressing data: invalid block type)"
                    .to_owned()
            )
        );
    }

    #[test]
    fn a_limited_decompress_leaves_its_tail_and_the_streams_end_its_unused_bytes() {
        let plain = b"abcdefghij".repeat(100);
        let member = compress_member(&plain, 1, MTIME).expect("compresses");
        let data = [member.as_slice(), b"tail"].concat();
        let mut inflater = Decompressor::new(GZIP_WINDOW).expect("opens");
        let first = inflater.decompress(&data, 10).expect("inflates");
        assert_eq!(first, plain[..10]);
        assert!(!inflater.eof() && !inflater.unconsumed_tail().is_empty());
        let tail = inflater.unconsumed_tail().to_vec();
        let rest = inflater.decompress(&tail, 0).expect("inflates");
        assert_eq!(rest, plain[10..]);
        assert!(inflater.eof());
        assert_eq!(inflater.unused_data(), b"tail");
        assert_eq!(inflater.unconsumed_tail(), b"tail");
    }
}
