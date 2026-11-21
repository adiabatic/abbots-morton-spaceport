//! `build._FragmentPacker`: one thread's freshly drafted fragments deflated for the parent's arena, each alone, at level 1 against the preset dictionary, in the zlib framing (§ Boundary, Replies out, of `rebuild/CORPUS-UNITS-PORT-PLAN.md`).
//!
//! The deflated bytes are not a byte-identity target; what the arena requires is that each fragment's bytes hold one zlib stream under the build's dictionary that inflates to the fragment's shard bytes and ends exactly where the bytes end (`_FragmentArena.read`), which the `fragment` stage's answer check proves through Python's `zlib`.

use std::fmt;
use std::path::Path;

use crate::ffi::Deflater;

/// `zlib.compressobj`'s level as `_FragmentPacker` makes it.
pub const LEVEL: i32 = 1;

/// A dictionary the engine cannot read or zlib cannot take, or a fragment zlib cannot deflate.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PackError(pub String);

impl fmt::Display for PackError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for PackError {}

/// `_FragmentPacker`: one compressor primed with the dictionary when the packer is made, copied for each fragment.
pub struct Packer {
    primed: Deflater,
}

impl Packer {
    /// The packer over the preset dictionary's bytes.
    pub fn new(dictionary: &[u8]) -> Result<Self, PackError> {
        Ok(Self {
            primed: Deflater::primed(LEVEL, dictionary).map_err(PackError)?,
        })
    }

    /// The packer over the preset dictionary the init's `dictionary` path holds.
    pub fn read(dictionary: &Path) -> Result<Self, PackError> {
        let bytes = std::fs::read(dictionary).map_err(|failure| {
            PackError(format!(
                "cannot read the dictionary {}: {failure}",
                dictionary.display()
            ))
        })?;
        Self::new(&bytes)
    }

    /// `_FragmentPacker.pack`: a fragment's shard bytes deflated, and their length.
    pub fn pack(&self, body: &[u8]) -> Result<(Vec<u8>, usize), PackError> {
        let packed = self.primed.deflate(body).map_err(PackError)?;
        Ok((packed, body.len()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn adler32(data: &[u8]) -> u32 {
        let (mut a, mut b) = (1u32, 0u32);
        for &byte in data {
            a = (a + u32::from(byte)) % 65521;
            b = (b + a) % 65521;
        }
        (b << 16) | a
    }

    #[test]
    fn a_packed_fragment_is_a_zlib_stream_naming_the_dictionary_and_the_contents() {
        let dictionary = b"\n {\n  \"id\": \"u-\",\n  \"ink_identical\": false,\n".repeat(40);
        let packer = Packer::new(&dictionary).expect("zlib takes the dictionary");
        let body = b"\n {\n  \"id\": \"u-3mJ7kPq2Xw9\",\n  \"ink_identical\": false\n }";
        let (packed, length) = packer.pack(body).expect("zlib deflates the body");
        assert_eq!(length, body.len());
        assert_eq!(packed[0], 0x78);
        assert_eq!(packed[1] & 0x20, 0x20, "the header sets FDICT");
        assert_eq!(u16::from_be_bytes([packed[0], packed[1]]) % 31, 0);
        assert_eq!(packed[2..6], adler32(&dictionary).to_be_bytes());
        assert_eq!(packed[packed.len() - 4..], adler32(body).to_be_bytes());
        assert!(
            packed.len() < body.len(),
            "the dictionary shortens the body"
        );
    }

    #[test]
    fn each_fragment_is_deflated_alone_by_a_copy_of_the_primed_compressor() {
        let packer = Packer::new(b"some dictionary bytes").expect("zlib takes the dictionary");
        let first = packer.pack(b"first fragment").expect("deflates");
        let second = packer.pack(b"second fragment").expect("deflates");
        assert_eq!(
            packer.pack(b"first fragment").expect("deflates"),
            first,
            "the primed compressor does not move between packs"
        );
        assert_ne!(first.0, second.0);
        let large: Vec<u8> = (0..200_000u32)
            .map(|value| (value * 7919 % 251) as u8)
            .collect();
        let (packed, length) = packer
            .pack(&large)
            .expect("deflates a body past one buffer");
        assert_eq!(length, large.len());
        assert_eq!(packed[packed.len() - 4..], adler32(&large).to_be_bytes());
    }
}
