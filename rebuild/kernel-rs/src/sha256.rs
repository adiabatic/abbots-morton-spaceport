//! SHA-256, FIPS 180-4, for content-addressed deep-class ids and table contract digests, both of which agree with Python's `hashlib.sha256` digit for digit.
//!
//! The incremental state retains one 64-byte block and the message length, so hashing a table requires no corpus-sized message or padding allocation. `digest_hex` supplies the one-shot interface for tab-joined rune names; table hashing feeds the same state a row at a time.

use std::fmt::Write as _;

/// The round constants, the first 32 bits of the fractional parts of the cube roots of the first 64 primes.
const ROUND_CONSTANTS: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

/// The initial hash state, the first 32 bits of the fractional parts of the square roots of the first eight primes.
const INITIAL_STATE: [u32; 8] = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
];

/// One message's digest as 64 lowercase hex digits, which is what `hashlib.sha256(...).hexdigest()` returns.
pub fn digest_hex(message: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(message);
    digest.finish()
}

pub struct Sha256 {
    state: [u32; 8],
    buffer: [u8; 64],
    buffered: usize,
    bytes: u64,
}

impl Default for Sha256 {
    fn default() -> Self {
        Self::new()
    }
}

impl Sha256 {
    pub fn new() -> Self {
        Self {
            state: INITIAL_STATE,
            buffer: [0; 64],
            buffered: 0,
            bytes: 0,
        }
    }

    pub fn update(&mut self, mut message: &[u8]) {
        self.bytes = self.bytes.wrapping_add(message.len() as u64);
        if self.buffered > 0 {
            let take = message.len().min(64 - self.buffered);
            self.buffer[self.buffered..self.buffered + take].copy_from_slice(&message[..take]);
            self.buffered += take;
            message = &message[take..];
            if self.buffered < 64 {
                return;
            }
            compress(&mut self.state, &self.buffer);
            self.buffered = 0;
        }
        let (blocks, remainder) = message.as_chunks::<64>();
        for block in blocks {
            compress(&mut self.state, block);
        }
        self.buffer[..remainder.len()].copy_from_slice(remainder);
        self.buffered = remainder.len();
    }

    pub fn finish(mut self) -> String {
        let bits = self.bytes.wrapping_mul(8);
        self.buffer[self.buffered] = 0x80;
        self.buffered += 1;
        self.buffer[self.buffered..].fill(0);
        if self.buffered > 56 {
            compress(&mut self.state, &self.buffer);
            self.buffer.fill(0);
        }
        self.buffer[56..].copy_from_slice(&bits.to_be_bytes());
        compress(&mut self.state, &self.buffer);
        let mut out = String::with_capacity(64);
        for word in self.state {
            write!(&mut out, "{word:08x}").unwrap();
        }
        out
    }
}

/// One 64-byte block folded into the state: the message schedule, then the sixty-four rounds over the eight working variables, then the Davies-Meyer addition back into the state.
fn compress(state: &mut [u32; 8], block: &[u8]) {
    let mut schedule = [0u32; 64];
    for (seat, word) in block.as_chunks::<4>().0.iter().enumerate() {
        schedule[seat] = u32::from_be_bytes(*word);
    }
    for seat in 16..64 {
        let near = schedule[seat - 15];
        let far = schedule[seat - 2];
        let sigma0 = near.rotate_right(7) ^ near.rotate_right(18) ^ (near >> 3);
        let sigma1 = far.rotate_right(17) ^ far.rotate_right(19) ^ (far >> 10);
        schedule[seat] = schedule[seat - 16]
            .wrapping_add(sigma0)
            .wrapping_add(schedule[seat - 7])
            .wrapping_add(sigma1);
    }
    // The eight working variables in the standard's own order, a through h, so that a round reads as the standard writes it.
    let mut work = *state;
    for (constant, word) in ROUND_CONSTANTS.iter().zip(schedule.iter()) {
        let sum1 = work[4].rotate_right(6) ^ work[4].rotate_right(11) ^ work[4].rotate_right(25);
        let choose = (work[4] & work[5]) ^ (!work[4] & work[6]);
        let temp1 = work[7]
            .wrapping_add(sum1)
            .wrapping_add(choose)
            .wrapping_add(*constant)
            .wrapping_add(*word);
        let sum0 = work[0].rotate_right(2) ^ work[0].rotate_right(13) ^ work[0].rotate_right(22);
        let majority = (work[0] & work[1]) ^ (work[0] & work[2]) ^ (work[1] & work[2]);
        let temp2 = sum0.wrapping_add(majority);
        work = [
            temp1.wrapping_add(temp2),
            work[0],
            work[1],
            work[2],
            work[3].wrapping_add(temp1),
            work[4],
            work[5],
            work[6],
        ];
    }
    for (seat, value) in work.iter().enumerate() {
        state[seat] = state[seat].wrapping_add(*value);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixpoint::deep_class_id;

    /// The published vectors, plus one message long enough to need a second block and one exactly on the padding boundary — the two places a hand-written padding rule goes wrong.
    #[test]
    fn the_digests_are_the_published_ones() {
        assert_eq!(
            digest_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            digest_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            digest_hex(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        );
        assert_eq!(
            digest_hex(&vec![b'a'; 1_000_000]),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
        );
    }

    #[test]
    fn published_vectors_accept_awkward_chunks_and_empty_updates() {
        for (message, expected) in [
            (
                b"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu".as_slice(),
                "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1",
            ),
            (
                b"".as_slice(),
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ),
            (
                b"abc".as_slice(),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            ),
            (
                b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq".as_slice(),
                "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
            ),
        ] {
            for width in [1, 7, 55, 56, 63, 64, 65] {
                let mut digest = Sha256::new();
                digest.update(b"");
                for chunk in message.chunks(width) {
                    digest.update(chunk);
                    digest.update(b"");
                }
                assert_eq!(digest.finish(), expected, "chunk width {width}");
            }
        }
        let mut digest = Sha256::new();
        let chunk = [b'a'; 997];
        for _ in 0..(1_000_000 / chunk.len()) {
            digest.update(&chunk);
            digest.update(b"");
        }
        digest.update(&chunk[..1_000_000 % chunk.len()]);
        assert_eq!(
            digest.finish(),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
        );
    }

    /// Independently computed `shasum -a 256` answers cover the padding and block boundaries, with every two-part split exercising partial-buffer completion.
    #[test]
    fn padding_boundaries_accept_every_split() {
        for (length, expected) in [
            (
                55,
                "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318",
            ),
            (
                56,
                "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a",
            ),
            (
                63,
                "7d3e74a05d7db15bce4ad9ec0658ea98e3f06eeecf16b4c6fff2da457ddc2f34",
            ),
            (
                64,
                "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb",
            ),
            (
                65,
                "635361c48bb9eab14198e76ea8ab7f1a41685d6ad62aa9146d301d4f17eb0ae0",
            ),
            (
                119,
                "31eba51c313a5c08226adf18d4a359cfdfd8d2e816b13f4af952f7ea6584dcfb",
            ),
            (
                120,
                "2f3d335432c70b580af0e8e1b3674a7c020d683aa5f73aaaedfdc55af904c21c",
            ),
            (
                127,
                "c57e9278af78fa3cab38667bef4ce29d783787a2f731d4e12200270f0c32320a",
            ),
            (
                128,
                "6836cf13bac400e9105071cd6af47084dfacad4e5e302c94bfed24e013afb73e",
            ),
            (
                129,
                "c12cb024a2e5551cca0e08fce8f1c5e314555cc3fef6329ee994a3db752166ae",
            ),
        ] {
            let message = vec![b'a'; length];
            assert_eq!(digest_hex(&message), expected);
            for split in 0..=length {
                let mut digest = Sha256::new();
                digest.update(&message[..split]);
                digest.update(b"");
                digest.update(&message[split..]);
                digest.update(b"");
                assert_eq!(digest.finish(), expected, "length {length}, split {split}");
            }
        }
    }

    /// The shape the class ids are cut from: a tab-joined member list, hashed and cut to twelve digits. Every constant here is `hashlib`'s own answer for the same input, computed outside this crate — the ids ride the transitions stream, so agreeing with Python digit for digit is the whole contract, and a comparison of the crate against itself would pin nothing.
    #[test]
    fn a_tab_joined_member_list_takes_the_class_id_python_computes() {
        let members = |names: &[&str]| -> Vec<String> {
            names.iter().map(|name| (*name).to_owned()).collect()
        };
        assert_eq!(deep_class_id(&members(&[])), "#Ce3b0c44298fc");
        assert_eq!(deep_class_id(&members(&["qsPea"])), "#C55e3af9ab7e8");
        assert_eq!(
            deep_class_id(&members(&["qsPea", "qsTea"])),
            "#C6bcf85c3d950"
        );
        assert_eq!(
            deep_class_id(&members(&["qsTea", "qsPea"])),
            "#Cece81054052f",
            "the id addresses the members in the order it is handed them, which is why the emission sorts them first"
        );
        assert_eq!(
            digest_hex(b"qsPea\tqsTea"),
            "6bcf85c3d9502637153a67b448cdd8facc82698dcf7064a78d0168cdc9ad0ba8",
            "and the id above is the first twelve digits of exactly this digest"
        );
    }
}
