//! The HarfBuzz the crate links (`build.rs`), through hand-written declarations against the 12.3.2 headers, and the safe types the rest of the crate shapes with ([`Font`], [`Feature`], [`Buffer`]); and the C library's `mmap`, behind the read-only file mapping the subset pack is read through ([`Mapping`]). This is the only module that allows `unsafe`; no compiler checks the declarations' ABI, so `rebuild/test_units_exec.py::test_harfbuzz_shapes_like_uharfbuzz_on_the_mini_texts` guards them by comparing every field with uharfbuzz.

#![allow(unsafe_code, non_camel_case_types)]

use std::ffi::{CStr, CString, c_char, c_int, c_uint, c_void};
use std::os::fd::AsRawFd;
use std::path::Path;
use std::ptr::NonNull;

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

#[cfg(test)]
mod tests {
    use super::*;

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
    fn the_linked_harfbuzz_is_the_pinned_release() {
        assert_eq!(version(), "12.3.2");
    }
}
