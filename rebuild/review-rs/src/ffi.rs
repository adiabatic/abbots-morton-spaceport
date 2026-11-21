//! The HarfBuzz the crate links (`build.rs`), through hand-written declarations against the 12.3.2 headers, and the safe types the rest of the crate shapes with ([`Font`], [`Feature`], [`Buffer`]); the C library's `mmap`, behind the read-only file mapping the subset pack is read through ([`Mapping`]); the system zlib, behind the primed compressor the fragments are deflated with ([`Deflater`]) and the pool its buffers are drawn from ([`BufferPool`]), the plain compressor and decompressor the corpus build's containers are written and read with ([`Compressor`], [`Inflater`]), its CRC-32 ([`crc32_of`]) and its version ([`zlib_version`]); the process's peak footprint, which the `stopped` reply carries ([`peak_footprint`]), and its resident peak and resident set, which the build's phase lines carry ([`peak_rss_self_bytes`], [`resident_bytes`]); and the descriptors a parent passes the process, taken as files ([`inherited_files`]). This is the only module that allows `unsafe`; no compiler checks the declarations' ABI, so `rebuild/test_units_exec.py::test_harfbuzz_shapes_like_uharfbuzz_on_the_mini_texts` guards HarfBuzz's by comparing every field with uharfbuzz, and the `fragment` stage's answer check and the `containers` stage guard zlib's by inflating every deflated fragment through Python's `zlib` and by reproducing every container the Python build writes.

#![allow(unsafe_code, non_camel_case_types)]

use std::alloc::{Layout, alloc, dealloc};
use std::cell::RefCell;
use std::ffi::{CStr, CString, c_char, c_int, c_long, c_uint, c_ulong, c_void};
use std::os::fd::{AsRawFd, FromRawFd};
use std::path::Path;
use std::ptr::NonNull;
use std::sync::atomic::{AtomicBool, Ordering};

#[repr(C)]
struct hb_blob_t {
    _opaque: [u8; 0],
}

#[repr(C)]
struct hb_face_t {
    _opaque: [u8; 0],
}

#[repr(C)]
struct hb_font_t {
    _opaque: [u8; 0],
}

#[repr(C)]
struct hb_buffer_t {
    _opaque: [u8; 0],
}

type hb_bool_t = c_int;

#[repr(C)]
#[derive(Clone, Copy)]
struct hb_glyph_info_t {
    codepoint: u32,
    mask: u32,
    cluster: u32,
    var1: u32,
    var2: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct hb_glyph_position_t {
    x_advance: i32,
    y_advance: i32,
    x_offset: i32,
    y_offset: i32,
    var: u32,
}

#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct hb_feature_t {
    tag: u32,
    value: u32,
    start: c_uint,
    end: c_uint,
}

unsafe extern "C" {
    fn hb_version_string() -> *const c_char;

    fn hb_blob_create_from_file_or_fail(file_name: *const c_char) -> *mut hb_blob_t;
    fn hb_blob_destroy(blob: *mut hb_blob_t);
    fn hb_face_create(blob: *mut hb_blob_t, index: c_uint) -> *mut hb_face_t;
    fn hb_face_destroy(face: *mut hb_face_t);
    fn hb_face_get_glyph_count(face: *const hb_face_t) -> c_uint;
    fn hb_font_create(face: *mut hb_face_t) -> *mut hb_font_t;
    fn hb_font_make_immutable(font: *mut hb_font_t);
    fn hb_font_destroy(font: *mut hb_font_t);

    fn hb_buffer_create() -> *mut hb_buffer_t;
    fn hb_buffer_destroy(buffer: *mut hb_buffer_t);
    fn hb_buffer_allocation_successful(buffer: *mut hb_buffer_t) -> hb_bool_t;
    fn hb_buffer_clear_contents(buffer: *mut hb_buffer_t);
    fn hb_buffer_add_utf32(
        buffer: *mut hb_buffer_t,
        text: *const u32,
        text_length: c_int,
        item_offset: c_uint,
        item_length: c_int,
    );
    fn hb_buffer_guess_segment_properties(buffer: *mut hb_buffer_t);
    fn hb_buffer_get_glyph_infos(
        buffer: *mut hb_buffer_t,
        length: *mut c_uint,
    ) -> *mut hb_glyph_info_t;
    fn hb_buffer_get_glyph_positions(
        buffer: *mut hb_buffer_t,
        length: *mut c_uint,
    ) -> *mut hb_glyph_position_t;

    fn hb_feature_from_string(
        text: *const c_char,
        len: c_int,
        feature: *mut hb_feature_t,
    ) -> hb_bool_t;
    fn hb_shape(
        font: *mut hb_font_t,
        buffer: *mut hb_buffer_t,
        features: *const hb_feature_t,
        num_features: c_uint,
    );
}

unsafe extern "C" {
    fn mmap(
        addr: *mut c_void,
        len: usize,
        prot: c_int,
        flags: c_int,
        fd: c_int,
        offset: i64,
    ) -> *mut c_void;
    fn munmap(addr: *mut c_void, len: usize) -> c_int;
}

const PROT_READ: c_int = 1;
const MAP_SHARED: c_int = 1;

/// A whole file mapped read-only and shared, as Python's `mmap.mmap(fileno, 0, access=mmap.ACCESS_READ)` maps it, which every thread may read at once. The file must not change while it is mapped: the subset pack is written to a temporary file and renamed into place, never rewritten in place.
pub struct Mapping {
    raw: NonNull<u8>,
    len: usize,
}

// SAFETY: the mapping is read-only and owned here; nothing writes through it.
unsafe impl Send for Mapping {}
// SAFETY: as for Send.
unsafe impl Sync for Mapping {}

impl Mapping {
    /// Map the file at `path`, refusing an empty file, which `mmap` cannot map.
    pub fn open(path: &Path) -> std::io::Result<Self> {
        let file = std::fs::File::open(path)?;
        let len = usize::try_from(file.metadata()?.len()).map_err(|_| {
            std::io::Error::new(std::io::ErrorKind::InvalidData, "the file outruns memory")
        })?;
        if len == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "cannot map an empty file",
            ));
        }
        // SAFETY: a fresh read-only shared mapping of `len` bytes of an open file; the mapping outlives the descriptor, which closes when `file` drops.
        let raw = unsafe {
            mmap(
                std::ptr::null_mut(),
                len,
                PROT_READ,
                MAP_SHARED,
                file.as_raw_fd(),
                0,
            )
        };
        if raw.addr() == usize::MAX {
            return Err(std::io::Error::last_os_error());
        }
        let raw = NonNull::new(raw.cast::<u8>()).ok_or_else(|| {
            std::io::Error::new(std::io::ErrorKind::InvalidData, "mmap returned NULL")
        })?;
        Ok(Self { raw, len })
    }

    /// The file's bytes.
    pub fn bytes(&self) -> &[u8] {
        // SAFETY: `raw` maps `len` readable bytes until `drop` unmaps them.
        unsafe { std::slice::from_raw_parts(self.raw.as_ptr(), self.len) }
    }
}

impl Drop for Mapping {
    fn drop(&mut self) {
        // SAFETY: the mapping is owned here and unmapped once.
        unsafe {
            munmap(self.raw.as_ptr().cast::<c_void>(), self.len);
        }
    }
}

unsafe extern "C" {
    fn fcntl(fd: c_int, cmd: c_int, ...) -> c_int;
}

const F_GETFD: c_int = 1;

static DESCRIPTORS_TAKEN: AtomicBool = AtomicBool::new(false);

/// The descriptors the parent passed this process (`subprocess.Popen(pass_fds=…)`, as the corpus build hands over the audit it holds), each taken as the owned file it is, in their order. They are taken once a process, so no descriptor gets two owners that would each close it: a second call is refused, and so is a descriptor below 3 (standard input, output and error), one beyond `c_int`, one listed twice, or one not open in this process, each with the reason and before any is taken.
pub fn inherited_files(descriptors: &[i64]) -> Result<Vec<std::fs::File>, String> {
    let mut checked: Vec<c_int> = Vec::with_capacity(descriptors.len());
    for &descriptor in descriptors {
        let fd = c_int::try_from(descriptor)
            .ok()
            .filter(|fd| *fd > 2)
            .ok_or_else(|| format!("{descriptor} is not a descriptor a parent passes"))?;
        if checked.contains(&fd) {
            return Err(format!("the descriptor {fd} is listed twice"));
        }
        // SAFETY: F_GETFD reads the descriptor's flags and changes nothing; an unopened one answers -1.
        if unsafe { fcntl(fd, F_GETFD) } == -1 {
            return Err(format!(
                "the descriptor {fd} is not open in this process: {}",
                std::io::Error::last_os_error()
            ));
        }
        checked.push(fd);
    }
    if DESCRIPTORS_TAKEN.swap(true, Ordering::SeqCst) {
        return Err("the inherited descriptors are already taken".into());
    }
    Ok(checked
        .into_iter()
        // SAFETY: each descriptor is open, came from the parent at the process's start, so no file of this process owns it, and is taken once, by the guard above, so the file made here is its one owner.
        .map(|fd| unsafe { std::fs::File::from_raw_fd(fd) })
        .collect())
}

/// The linked HarfBuzz's version, `hb_version_string()`.
pub fn version() -> String {
    // SAFETY: HarfBuzz returns a static, NUL-terminated string.
    unsafe { CStr::from_ptr(hb_version_string()) }
        .to_string_lossy()
        .into_owned()
}

/// One font, made from its file at the default scale in font units as uharfbuzz's `hb.Font(hb.Face(blob))` is, and made immutable, so every thread can shape with it at once while each owns its [`Buffer`].
pub struct Font {
    raw: NonNull<hb_font_t>,
    glyph_count: u32,
}

// SAFETY: an immutable hb_font_t is safe to shape with from several threads at once; HarfBuzz documents its font objects as thread-safe once immutable, and nothing here mutates one after `open`.
unsafe impl Send for Font {}
// SAFETY: as for Send.
unsafe impl Sync for Font {}

impl Font {
    /// Open the font file at `path`, refusing a file HarfBuzz cannot read or one with no glyphs.
    pub fn open(path: &Path) -> Result<Self, String> {
        let name = path
            .to_str()
            .and_then(|text| CString::new(text).ok())
            .ok_or_else(|| format!("the font path {} cannot reach HarfBuzz", path.display()))?;
        // SAFETY: `name` is NUL-terminated; each object is destroyed once, after its successor holds its own reference.
        unsafe {
            let blob = hb_blob_create_from_file_or_fail(name.as_ptr());
            if blob.is_null() {
                return Err(format!("HarfBuzz cannot read the font {}", path.display()));
            }
            let face = hb_face_create(blob, 0);
            hb_blob_destroy(blob);
            let glyph_count = hb_face_get_glyph_count(face);
            let raw = hb_font_create(face);
            hb_face_destroy(face);
            hb_font_make_immutable(raw);
            let raw = NonNull::new(raw).expect("hb_font_create never returns NULL");
            let font = Self { raw, glyph_count };
            if glyph_count == 0 {
                return Err(format!("{} holds no glyphs", path.display()));
            }
            Ok(font)
        }
    }

    /// The face's glyph count.
    pub fn glyph_count(&self) -> u32 {
        self.glyph_count
    }
}

impl Drop for Font {
    fn drop(&mut self) {
        // SAFETY: the font is owned here and destroyed once.
        unsafe { hb_font_destroy(self.raw.as_ptr()) }
    }
}

/// One feature over the whole text, built as uharfbuzz builds an entry of a feature dict: `hb_feature_from_string` over the tag, then the value replaced.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(transparent)]
pub struct Feature(hb_feature_t);

impl Feature {
    /// The feature `tag` at `value` over the whole text, or None when HarfBuzz cannot parse the tag.
    pub fn global(tag: &str, value: u32) -> Option<Self> {
        let length = c_int::try_from(tag.len()).ok()?;
        let mut feature = hb_feature_t::default();
        // SAFETY: the pointer and length describe `tag`'s bytes, which HarfBuzz reads without needing a NUL.
        let parsed = unsafe { hb_feature_from_string(tag.as_ptr().cast(), length, &mut feature) };
        if parsed == 0 {
            return None;
        }
        feature.value = value;
        Some(Self(feature))
    }
}

/// One shaped glyph as HarfBuzz leaves it: the glyph id, the cluster (a code point index into the text) and both offsets and advances, in font units.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Glyph {
    pub id: u32,
    pub cluster: u32,
    pub x_offset: i32,
    pub y_offset: i32,
    pub x_advance: i32,
    pub y_advance: i32,
}

/// One HarfBuzz buffer, owned by one thread.
pub struct Buffer {
    raw: NonNull<hb_buffer_t>,
}

// SAFETY: a buffer is used by one thread at a time; moving it to another thread is safe.
unsafe impl Send for Buffer {}

impl Default for Buffer {
    fn default() -> Self {
        Self::new()
    }
}

impl Buffer {
    /// A new, empty buffer.
    pub fn new() -> Self {
        // SAFETY: hb_buffer_create returns a valid buffer, an inert one when allocation fails, which `shape` detects.
        let raw = unsafe { hb_buffer_create() };
        Self {
            raw: NonNull::new(raw).expect("hb_buffer_create never returns NULL"),
        }
    }

    /// Shape `text`, code points in order, with `font` and `features` (no features when it is empty), into `out`, in the sequence `shaping.Shaper.shape` runs through uharfbuzz: clear the buffer, add the text, guess the segment properties, shape. A buffer that cannot allocate is refused.
    pub fn shape(
        &mut self,
        font: &Font,
        text: &[u32],
        features: &[Feature],
        out: &mut Vec<Glyph>,
    ) -> Result<(), String> {
        out.clear();
        let length =
            c_int::try_from(text.len()).map_err(|_| "a text too long to shape".to_string())?;
        let count =
            c_uint::try_from(features.len()).map_err(|_| "too many features".to_string())?;
        let buffer = self.raw.as_ptr();
        // SAFETY: the buffer and the font are live; the text and feature pointers describe their slices, and `Feature` is `repr(transparent)` over the `repr(C)` struct HarfBuzz reads; HarfBuzz's info and position arrays hold `length` entries each and are copied out before the buffer is touched again.
        unsafe {
            hb_buffer_clear_contents(buffer);
            hb_buffer_add_utf32(buffer, text.as_ptr(), length, 0, -1);
            hb_buffer_guess_segment_properties(buffer);
            let features = if features.is_empty() {
                std::ptr::null()
            } else {
                features.as_ptr().cast::<hb_feature_t>()
            };
            hb_shape(font.raw.as_ptr(), buffer, features, count);
            if hb_buffer_allocation_successful(buffer) == 0 {
                return Err("HarfBuzz could not allocate the buffer".to_string());
            }
            let mut infos_length: c_uint = 0;
            let infos = hb_buffer_get_glyph_infos(buffer, &mut infos_length);
            let mut positions_length: c_uint = 0;
            let positions = hb_buffer_get_glyph_positions(buffer, &mut positions_length);
            if infos_length != positions_length {
                return Err("HarfBuzz returned unequal glyph and position counts".to_string());
            }
            if infos_length == 0 {
                return Ok(());
            }
            let infos = std::slice::from_raw_parts(infos, infos_length as usize);
            let positions = std::slice::from_raw_parts(positions, positions_length as usize);
            out.extend(infos.iter().zip(positions).map(|(info, position)| Glyph {
                id: info.codepoint,
                cluster: info.cluster,
                x_offset: position.x_offset,
                y_offset: position.y_offset,
                x_advance: position.x_advance,
                y_advance: position.y_advance,
            }));
        }
        Ok(())
    }
}

impl Drop for Buffer {
    fn drop(&mut self) {
        // SAFETY: the buffer is owned here and destroyed once.
        unsafe { hb_buffer_destroy(self.raw.as_ptr()) }
    }
}

/// zlib's `z_stream`, as `zlib.h` declares it.
#[repr(C)]
struct z_stream {
    next_in: *const u8,
    avail_in: c_uint,
    total_in: c_ulong,
    next_out: *mut u8,
    avail_out: c_uint,
    total_out: c_ulong,
    msg: *const c_char,
    state: *mut c_void,
    zalloc: Option<unsafe extern "C" fn(*mut c_void, c_uint, c_uint) -> *mut c_void>,
    zfree: Option<unsafe extern "C" fn(*mut c_void, *mut c_void)>,
    opaque: *mut c_void,
    data_type: c_int,
    adler: c_ulong,
    reserved: c_ulong,
}

impl z_stream {
    /// A stream with every field zero, which leaves zlib its default allocator.
    fn zeroed() -> Self {
        Self {
            next_in: std::ptr::null(),
            avail_in: 0,
            total_in: 0,
            next_out: std::ptr::null_mut(),
            avail_out: 0,
            total_out: 0,
            msg: std::ptr::null(),
            state: std::ptr::null_mut(),
            zalloc: None,
            zfree: None,
            opaque: std::ptr::null_mut(),
            data_type: 0,
            adler: 0,
            reserved: 0,
        }
    }
}

const Z_OK: c_int = 0;
const Z_STREAM_END: c_int = 1;
const Z_BUF_ERROR: c_int = -5;
const Z_NO_FLUSH: c_int = 0;
const Z_SYNC_FLUSH: c_int = 2;
const Z_FINISH: c_int = 4;
const Z_DEFLATED: c_int = 8;
const DEF_MEM_LEVEL: c_int = 8;
const Z_DEFAULT_STRATEGY: c_int = 0;
/// The most a zlib call takes or gives at once, its `uInt` lengths' range.
const ZLIB_SPAN: usize = c_uint::MAX as usize;

#[link(name = "z")]
unsafe extern "C" {
    fn zlibVersion() -> *const c_char;
    fn deflateInit_(
        strm: *mut z_stream,
        level: c_int,
        version: *const c_char,
        stream_size: c_int,
    ) -> c_int;
    fn deflateInit2_(
        strm: *mut z_stream,
        level: c_int,
        method: c_int,
        window_bits: c_int,
        mem_level: c_int,
        strategy: c_int,
        version: *const c_char,
        stream_size: c_int,
    ) -> c_int;
    fn deflateSetDictionary(strm: *mut z_stream, dictionary: *const u8, length: c_uint) -> c_int;
    fn deflateCopy(dest: *mut z_stream, source: *mut z_stream) -> c_int;
    fn deflateBound(strm: *mut z_stream, source_length: c_ulong) -> c_ulong;
    fn deflate(strm: *mut z_stream, flush: c_int) -> c_int;
    fn deflateEnd(strm: *mut z_stream) -> c_int;
    fn inflateInit2_(
        strm: *mut z_stream,
        window_bits: c_int,
        version: *const c_char,
        stream_size: c_int,
    ) -> c_int;
    fn inflate(strm: *mut z_stream, flush: c_int) -> c_int;
    fn inflateEnd(strm: *mut z_stream) -> c_int;
    fn crc32(crc: c_ulong, buf: *const u8, len: c_uint) -> c_ulong;
}

/// The linked zlib's version, `zlibVersion()`, which Python's `zlib.ZLIB_RUNTIME_VERSION` reads from the library it loads.
pub fn zlib_version() -> String {
    // SAFETY: zlibVersion returns a static NUL-terminated string.
    unsafe { CStr::from_ptr(zlibVersion()) }
        .to_string_lossy()
        .into_owned()
}

/// zlib's CRC-32 of `bytes` continued from `crc`, as `zlib.crc32(bytes, crc)` computes it.
pub fn crc32_of(crc: u32, bytes: &[u8]) -> u32 {
    let mut value = c_ulong::from(crc);
    for piece in bytes.chunks(ZLIB_SPAN) {
        // SAFETY: `piece` is a live buffer of the length passed, which fits a `uInt`.
        value = unsafe { crc32(value, piece.as_ptr(), piece.len() as c_uint) };
    }
    u32::try_from(value).expect("a CRC-32 fits 32 bits")
}

fn stream_size() -> c_int {
    c_int::try_from(std::mem::size_of::<z_stream>()).expect("a z_stream's size fits")
}

/// One initialized deflate or inflate stream on the heap, where zlib's state points back at it, ended once when dropped.
struct Stream {
    raw: NonNull<z_stream>,
    inflating: bool,
}

impl Stream {
    /// A zeroed stream on the heap for zlib to initialize as a compressor; dropping it before zlib has initialized it frees it without ending it.
    fn allocate() -> Self {
        Self::zeroed(false)
    }

    /// A zeroed stream on the heap for zlib to initialize as a decompressor.
    fn allocate_inflating() -> Self {
        Self::zeroed(true)
    }

    fn zeroed(inflating: bool) -> Self {
        Self {
            raw: NonNull::from(Box::leak(Box::new(z_stream::zeroed()))),
            inflating,
        }
    }

    fn raw(&self) -> *mut z_stream {
        self.raw.as_ptr()
    }
}

impl Drop for Stream {
    fn drop(&mut self) {
        // SAFETY: the stream was leaked from a Box in `zeroed` and is owned here; deflateEnd and inflateEnd ignore a stream zlib never initialized (its state is NULL) and free an initialized one's state once.
        unsafe {
            if self.inflating {
                inflateEnd(self.raw());
            } else {
                deflateEnd(self.raw());
            }
            drop(Box::from_raw(self.raw()));
        }
    }
}

/// A deflate stream as `zlib.compressobj(level, DEFLATED, window_bits, 8, 0)` makes one, the stream `zlib.compress(data, level, window_bits)` deflates through too: raw deflate for `window_bits` −15, the gzip framing for 31 and the zlib framing for 15, with zlib's default memory level and strategy and no dictionary. zlib writes the same stream however its input is cut into calls, so [`Compressor::compress`] may be handed the input in any pieces.
pub struct Compressor {
    stream: Stream,
}

// SAFETY: the stream is owned here and reached only through `&mut self`; moving it to another thread is safe.
unsafe impl Send for Compressor {}

impl Compressor {
    /// A compressor at `level` with `window_bits`, refusing parameters zlib refuses.
    pub fn new(level: i32, window_bits: i32) -> Result<Self, String> {
        let stream = Stream::allocate();
        // SAFETY: the stream is zeroed and pinned on the heap; zlib reads the version string and keeps no pointer to it.
        let status = unsafe {
            deflateInit2_(
                stream.raw(),
                level,
                Z_DEFLATED,
                window_bits,
                DEF_MEM_LEVEL,
                Z_DEFAULT_STRATEGY,
                zlibVersion(),
                stream_size(),
            )
        };
        if status != Z_OK {
            return Err(format!(
                "zlib cannot start a compressor at level {level} with window bits {window_bits} (status {status})"
            ));
        }
        Ok(Self { stream })
    }

    /// Deflate `input` with no flush, as `compressobj.compress` does, and append what zlib writes to `out`.
    pub fn compress(&mut self, input: &[u8], out: &mut Vec<u8>) -> Result<(), String> {
        for piece in input.chunks(ZLIB_SPAN) {
            self.run(piece, Z_NO_FLUSH, out)?;
        }
        Ok(())
    }

    /// End the stream, as `compressobj.flush()` does, and append what zlib writes to `out`.
    pub fn finish(&mut self, out: &mut Vec<u8>) -> Result<(), String> {
        self.run(&[], Z_FINISH, out)
    }

    fn run(&mut self, input: &[u8], flush: c_int, out: &mut Vec<u8>) -> Result<(), String> {
        let raw = self.stream.raw();
        // SAFETY: the stream is initialized and owned here; `next_in` and `next_out` describe live buffers of `avail_in` and `avail_out` bytes while deflate runs, and the output is cut to what zlib wrote.
        unsafe {
            (*raw).next_in = input.as_ptr();
            (*raw).avail_in = input.len() as c_uint;
            loop {
                out.reserve((input.len() / 2).clamp(1 << 12, 1 << 20));
                let written = out.len();
                let offered = (out.capacity() - written).min(ZLIB_SPAN) as c_uint;
                (*raw).next_out = out.as_mut_ptr().add(written);
                (*raw).avail_out = offered;
                let status = deflate(raw, flush);
                out.set_len(written + (offered - (*raw).avail_out) as usize);
                match status {
                    Z_STREAM_END => return Ok(()),
                    Z_OK | Z_BUF_ERROR => {
                        if flush != Z_FINISH && (*raw).avail_out != 0 {
                            return Ok(());
                        }
                    }
                    _ => return Err(format!("zlib cannot deflate the input (status {status})")),
                }
            }
        }
    }
}

/// `data` deflated whole, as `zlib.compress(data, level, wbits=window_bits)` deflates it.
pub fn compress(data: &[u8], level: i32, window_bits: i32) -> Result<Vec<u8>, String> {
    let mut compressor = Compressor::new(level, window_bits)?;
    let mut out = Vec::with_capacity(data.len() / 2 + 64);
    compressor.compress(data, &mut out)?;
    compressor.finish(&mut out)?;
    Ok(out)
}

/// How one [`Inflater::inflate`] call ended: zlib's status and message, how much input it took and how much output it gave.
pub struct Inflated {
    /// zlib's status: 0 (`Z_OK`), 1 (`Z_STREAM_END`), −5 (`Z_BUF_ERROR`) or an error.
    pub status: i32,
    /// The stream's message, which zlib sets with an error.
    pub message: Option<String>,
    pub consumed: usize,
    pub produced: usize,
}

impl Inflated {
    /// The end of the stream was reached.
    pub fn ended(&self) -> bool {
        self.status == Z_STREAM_END
    }

    /// zlib reported an error: any status but `Z_OK`, `Z_STREAM_END` and `Z_BUF_ERROR`, which only says no progress was possible.
    pub fn failed(&self) -> bool {
        !matches!(self.status, Z_OK | Z_STREAM_END | Z_BUF_ERROR)
    }
}

/// An inflate stream as `zlib.decompressobj(window_bits)` makes one: raw deflate for −15, the gzip framing, checked against its CRC-32 and length trailer, for 31, and the zlib framing for 15.
pub struct Inflater {
    stream: Stream,
}

// SAFETY: the stream is owned here and reached only through `&mut self`; moving it to another thread is safe.
unsafe impl Send for Inflater {}

impl Inflater {
    /// A decompressor with `window_bits`, refusing parameters zlib refuses.
    pub fn new(window_bits: i32) -> Result<Self, String> {
        let stream = Stream::allocate_inflating();
        // SAFETY: the stream is zeroed and pinned on the heap; zlib reads the version string and keeps no pointer to it.
        let status =
            unsafe { inflateInit2_(stream.raw(), window_bits, zlibVersion(), stream_size()) };
        if status != Z_OK {
            return Err(format!(
                "zlib cannot start a decompressor with window bits {window_bits} (status {status})"
            ));
        }
        Ok(Self { stream })
    }

    /// One `inflate` call with `Z_SYNC_FLUSH`, the flush `decompressobj.decompress` passes, over `input` into `output`, each cut to a `uInt`'s range.
    pub fn inflate(&mut self, input: &[u8], output: &mut [u8]) -> Inflated {
        let raw = self.stream.raw();
        let offered_in = input.len().min(ZLIB_SPAN) as c_uint;
        let offered_out = output.len().min(ZLIB_SPAN) as c_uint;
        // SAFETY: the stream is initialized and owned here; `next_in` and `next_out` describe live buffers of `avail_in` and `avail_out` bytes while inflate runs, and zlib's message, when set, is a static NUL-terminated string.
        unsafe {
            (*raw).next_in = input.as_ptr();
            (*raw).avail_in = offered_in;
            (*raw).next_out = output.as_mut_ptr();
            (*raw).avail_out = offered_out;
            let status = inflate(raw, Z_SYNC_FLUSH);
            let message = (!(*raw).msg.is_null())
                .then(|| CStr::from_ptr((*raw).msg).to_string_lossy().into_owned());
            Inflated {
                status,
                message,
                consumed: (offered_in - (*raw).avail_in) as usize,
                produced: (offered_out - (*raw).avail_out) as usize,
            }
        }
    }
}

/// The buffers zlib allocates for a [`Deflater`]'s primed stream and its copies, each kept when zlib frees it and handed out again for the next request of its length, so each fragment's copy reuses the last copy's buffers instead of taking fresh pages from the allocator and returning them. Each buffer carries its length in a header of `POOL_HEADER` bytes.
struct BufferPool {
    free: RefCell<Vec<(usize, *mut u8)>>,
}

const POOL_HEADER: usize = 16;

impl BufferPool {
    fn layout(length: usize) -> Layout {
        Layout::from_size_align(length + POOL_HEADER, POOL_HEADER)
            .expect("a zlib buffer's layout is valid")
    }
}

impl Drop for BufferPool {
    fn drop(&mut self) {
        for (length, buffer) in self.free.get_mut().drain(..) {
            // SAFETY: each kept buffer was allocated by `pool_alloc` with this layout and is freed once.
            unsafe { dealloc(buffer, BufferPool::layout(length)) }
        }
    }
}

unsafe extern "C" fn pool_alloc(opaque: *mut c_void, items: c_uint, size: c_uint) -> *mut c_void {
    let length = items as usize * size as usize;
    // SAFETY: `opaque` is the deflater's live pool, used by one thread at a time.
    let pool = unsafe { &*opaque.cast::<BufferPool>() };
    let mut free = pool.free.borrow_mut();
    let buffer = match free.iter().position(|&(kept, _)| kept == length) {
        Some(index) => free.swap_remove(index).1,
        None => {
            // SAFETY: the layout is nonzero in size.
            let buffer = unsafe { alloc(BufferPool::layout(length)) };
            if buffer.is_null() {
                return std::ptr::null_mut();
            }
            // SAFETY: the header's first word lies inside the allocation.
            unsafe { buffer.cast::<usize>().write(length) };
            buffer
        }
    };
    // SAFETY: the header lies inside the allocation.
    unsafe { buffer.add(POOL_HEADER).cast() }
}

unsafe extern "C" fn pool_free(opaque: *mut c_void, address: *mut c_void) {
    if address.is_null() {
        return;
    }
    // SAFETY: `address` came from `pool_alloc`, so its header holds its length; `opaque` is the deflater's live pool.
    unsafe {
        let buffer = address.cast::<u8>().sub(POOL_HEADER);
        let length = buffer.cast::<usize>().read();
        (*opaque.cast::<BufferPool>())
            .free
            .borrow_mut()
            .push((length, buffer));
    }
}

/// A deflate compressor primed with a preset dictionary, which deflates each input alone in the zlib framing through a copy of itself, as `build._FragmentPacker` deflates with a copy of its primed `zlib.compressobj`: the header carries the dictionary's Adler-32 and the trailer the input's, and the window and memory level are zlib's defaults, as `compressobj`'s are. zlib allocates the primed stream and every copy from the deflater's [`BufferPool`].
pub struct Deflater {
    primed: Stream,
    /// Declared after `primed`, so it is dropped after the stream that frees into it.
    _pool: Box<BufferPool>,
}

// SAFETY: the primed stream is owned here and only read, by deflateCopy, after `primed` returns, and the pool is owned here and reached only through the streams of whichever thread holds the deflater; moving both to another thread is safe.
unsafe impl Send for Deflater {}

impl Deflater {
    /// A compressor at `level` primed with `dictionary`, refusing a dictionary zlib cannot take.
    pub fn primed(level: i32, dictionary: &[u8]) -> Result<Self, String> {
        let length = c_uint::try_from(dictionary.len())
            .map_err(|_| "the dictionary is too long".to_string())?;
        let size = stream_size();
        let pool = Box::new(BufferPool {
            free: RefCell::new(Vec::new()),
        });
        let stream = Stream::allocate();
        // SAFETY: the pool outlives the stream and its copies, which take its allocator from the stream (deflateCopy copies `zalloc`, `zfree` and `opaque`).
        unsafe {
            (*stream.raw()).zalloc = Some(pool_alloc);
            (*stream.raw()).zfree = Some(pool_free);
            (*stream.raw()).opaque = std::ptr::from_ref::<BufferPool>(&pool).cast_mut().cast();
        }
        // SAFETY: the stream is zeroed and pinned on the heap; zlib reads the version string and `length` bytes of the dictionary, and keeps no pointer to either.
        unsafe {
            let status = deflateInit_(stream.raw(), level, zlibVersion(), size);
            if status != Z_OK {
                return Err(format!("zlib cannot start a compressor (status {status})"));
            }
            let status = deflateSetDictionary(stream.raw(), dictionary.as_ptr(), length);
            if status != Z_OK {
                return Err(format!("zlib refuses the dictionary (status {status})"));
            }
        }
        Ok(Self {
            primed: stream,
            _pool: pool,
        })
    }

    /// `input` deflated alone through a copy of the primed compressor, finished, as one zlib stream.
    pub fn deflate(&self, input: &[u8]) -> Result<Vec<u8>, String> {
        let length = c_uint::try_from(input.len())
            .map_err(|_| "the input is too long to deflate".to_string())?;
        let copy = Stream::allocate();
        // SAFETY: both streams are pinned on the heap and the primed one is initialized; deflateCopy only reads it. On failure deflateCopy leaves the copy's state NULL or frees it, so the copy's drop ends nothing twice. `next_in` and `next_out` describe live buffers of `avail_in` and `avail_out` bytes while deflate runs, and the output is cut to what zlib wrote.
        unsafe {
            let status = deflateCopy(copy.raw(), self.primed.raw());
            if status != Z_OK {
                (*copy.raw()).state = std::ptr::null_mut();
                return Err(format!(
                    "zlib cannot copy the primed compressor (status {status})"
                ));
            }
            let bound = usize::try_from(deflateBound(copy.raw(), c_ulong::from(length)))
                .map_err(|_| "the deflated bound outruns memory".to_string())?;
            let mut out: Vec<u8> = Vec::with_capacity(bound.max(64));
            let raw = copy.raw();
            (*raw).next_in = input.as_ptr();
            (*raw).avail_in = length;
            loop {
                let written = out.len();
                let offered = c_uint::try_from(out.capacity() - written).unwrap_or(c_uint::MAX);
                (*raw).next_out = out.as_mut_ptr().add(written);
                (*raw).avail_out = offered;
                let status = deflate(raw, Z_FINISH);
                out.set_len(written + (offered - (*raw).avail_out) as usize);
                match status {
                    Z_STREAM_END => return Ok(out),
                    Z_OK | Z_BUF_ERROR => out.reserve(out.capacity().max(64)),
                    _ => return Err(format!("zlib cannot deflate the input (status {status})")),
                }
            }
        }
    }
}

/// `struct rusage`, as `sys/resource.h` declares it on Darwin and Linux: two `timeval`s, then `ru_maxrss` and thirteen more `long` counters.
#[repr(C)]
struct rusage {
    ru_utime: [i64; 2],
    ru_stime: [i64; 2],
    ru_maxrss: c_long,
    ru_rest: [c_long; 13],
}

const RUSAGE_SELF: c_int = 0;

unsafe extern "C" {
    fn getrusage(who: c_int, usage: *mut rusage) -> c_int;
}

/// `getrusage`'s `ru_maxrss` in bytes, which Darwin reports in bytes and Linux in KiB (`peak_rss.peak_rss_self_bytes`), or 0 when it cannot be read.
pub fn peak_rss_self_bytes() -> u64 {
    let mut usage = rusage {
        ru_utime: [0; 2],
        ru_stime: [0; 2],
        ru_maxrss: 0,
        ru_rest: [0; 13],
    };
    // SAFETY: `usage` is a writable `struct rusage` that getrusage fills.
    if unsafe { getrusage(RUSAGE_SELF, &raw mut usage) } != 0 {
        return 0;
    }
    let peak = u64::try_from(usage.ru_maxrss).unwrap_or(0);
    if cfg!(target_os = "macos") {
        peak
    } else {
        peak * 1024
    }
}

/// `rusage_info_v4`, as `sys/resource.h` declares it on Darwin: a UUID, then 35 64-bit fields, `ri_lifetime_max_phys_footprint` the 29th.
#[cfg(target_os = "macos")]
#[repr(C)]
struct rusage_info_v4 {
    ri_uuid: [u8; 16],
    ri_fields: [u64; 35],
}

#[cfg(target_os = "macos")]
const RUSAGE_INFO_V4: c_int = 4;

#[cfg(target_os = "macos")]
const LIFETIME_MAX_PHYS_FOOTPRINT: usize = 28;

#[cfg(target_os = "macos")]
const RESIDENT_SIZE: usize = 6;

#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn getpid() -> c_int;
    fn proc_pid_rusage(pid: c_int, flavor: c_int, buffer: *mut c_void) -> c_int;
}

/// This process's peak footprint as `peak_rss.peak_footprint_self_bytes` reads it: `proc_pid_rusage`'s `ri_lifetime_max_phys_footprint` on Darwin, else the resident peak.
#[cfg(target_os = "macos")]
pub fn peak_footprint() -> u64 {
    let mut info = rusage_info_v4 {
        ri_uuid: [0; 16],
        ri_fields: [0; 35],
    };
    // SAFETY: `info` is a writable `rusage_info_v4`, the flavor proc_pid_rusage fills.
    let status =
        unsafe { proc_pid_rusage(getpid(), RUSAGE_INFO_V4, (&raw mut info).cast::<c_void>()) };
    if status == 0 {
        info.ri_fields[LIFETIME_MAX_PHYS_FOOTPRINT]
    } else {
        peak_rss_self_bytes()
    }
}

/// This process's resident set now, as `peak_rss.current_rss_bytes` reads it with `ps -o rss=`: `proc_pid_rusage`'s `ri_resident_size`, the task's resident size `ps` reports, or None when it cannot be read.
#[cfg(target_os = "macos")]
pub fn resident_bytes() -> Option<u64> {
    let mut info = rusage_info_v4 {
        ri_uuid: [0; 16],
        ri_fields: [0; 35],
    };
    // SAFETY: `info` is a writable `rusage_info_v4`, the flavor proc_pid_rusage fills.
    let status =
        unsafe { proc_pid_rusage(getpid(), RUSAGE_INFO_V4, (&raw mut info).cast::<c_void>()) };
    (status == 0).then_some(info.ri_fields[RESIDENT_SIZE])
}

/// This process's resident set now, as `peak_rss.current_rss_bytes` reads it from `/proc/self/statm`'s resident pages times the page size: `/proc/self/status`'s `VmRSS`, the same count in KiB, or None when it cannot be read.
#[cfg(not(target_os = "macos"))]
pub fn resident_bytes() -> Option<u64> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    status.lines().find_map(|line| {
        let kib = line.strip_prefix("VmRSS:")?.trim().strip_suffix("kB")?;
        kib.trim().parse::<u64>().ok().map(|kib| kib * 1024)
    })
}

/// This process's peak footprint as `peak_rss.peak_footprint_self_bytes` reads it: `/proc/self/status`'s `VmHWM` where it can be read, else the resident peak.
#[cfg(not(target_os = "macos"))]
pub fn peak_footprint() -> u64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|status| {
            status.lines().find_map(|line| {
                let kib = line.strip_prefix("VmHWM:")?.trim().strip_suffix("kB")?;
                kib.trim().parse::<u64>().ok()
            })
        })
        .map_or_else(peak_rss_self_bytes, |kib| kib * 1024)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_peak_footprint_holds_at_least_what_this_process_allocated() {
        let held = vec![1u8; 32 << 20];
        assert!(peak_footprint() >= held.len() as u64);
        assert!(peak_rss_self_bytes() > 0);
    }

    #[test]
    fn the_resident_set_holds_what_this_process_touched_and_stays_under_the_resident_peak() {
        let held = std::hint::black_box(vec![1u8; 32 << 20]);
        let now = resident_bytes().expect("the resident set reads");
        assert!(now >= held.len() as u64, "{now}");
        assert!(now <= peak_rss_self_bytes(), "{now}");
    }

    #[test]
    fn a_feature_takes_its_tag_from_harfbuzz_and_its_value_from_the_caller() {
        let feature = Feature::global("kern", 0).expect("kern parses");
        assert_eq!(feature.0.tag, u32::from_be_bytes(*b"kern"));
        assert_eq!(
            (feature.0.value, feature.0.start, feature.0.end),
            (0, 0, c_uint::MAX)
        );
        assert_eq!(Feature::global("ss10", 1).expect("ss10 parses").0.value, 1);
        assert_eq!(Feature::global("", 1), None);
    }

    #[test]
    fn inherited_descriptors_are_taken_once_as_files_and_a_bad_list_is_refused_first() {
        use std::io::Read;
        use std::os::fd::IntoRawFd;
        let path = std::env::temp_dir().join(format!("ams-review-held-{}", std::process::id()));
        std::fs::write(&path, b"held").expect("write");
        let fd = i64::from(std::fs::File::open(&path).expect("open").into_raw_fd());
        std::fs::remove_file(&path).expect("remove");
        for (descriptors, reason) in [
            (vec![2], "is not a descriptor"),
            (vec![i64::from(c_int::MAX) + 1], "is not a descriptor"),
            (vec![fd, fd], "listed twice"),
            (vec![fd, i64::from(c_int::MAX)], "is not open"),
        ] {
            let refusal = inherited_files(&descriptors).expect_err("refused");
            assert!(refusal.contains(reason), "{refusal}");
        }
        let mut files = inherited_files(&[fd]).expect("taken");
        let mut text = String::new();
        files[0].read_to_string(&mut text).expect("read");
        assert_eq!(text, "held");
        assert!(
            inherited_files(&[])
                .expect_err("again")
                .contains("already taken")
        );
    }

    #[test]
    fn zlib_checksums_deflates_and_inflates_as_its_reference_values_say() {
        assert_eq!(crc32_of(0, b"123456789"), 0xcbf4_3926);
        assert_eq!(crc32_of(crc32_of(0, b"1234"), b"56789"), 0xcbf4_3926);
        assert!(zlib_version().starts_with("1."), "{}", zlib_version());
        let plain = b"abcabcabcabc".repeat(1000);
        let packed = compress(&plain, 1, -15).expect("deflates");
        assert!(packed.len() < plain.len() / 10);
        let mut inflater = Inflater::new(-15).expect("inflater");
        let mut out = vec![0; plain.len() + 1];
        let step = inflater.inflate(&packed, &mut out);
        assert!(step.ended() && !step.failed());
        assert_eq!(
            (step.consumed, &out[..step.produced]),
            (packed.len(), &plain[..])
        );
        let mut broken = Inflater::new(-15).expect("inflater");
        let step = broken.inflate(&[0xff; 8], &mut out);
        assert!(step.failed());
        assert_eq!(step.message.as_deref(), Some("invalid block type"));
    }

    #[test]
    fn the_linked_harfbuzz_is_the_pinned_release() {
        assert_eq!(version(), "12.3.2");
    }
}
