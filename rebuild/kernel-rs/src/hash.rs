//! The crate's hash maps and sets: [`HashMap`] and [`HashSet`] are the standard library's tables on [`FastHasher`], a multiply-rotate hasher with an avalanche finalizer, in place of the `RandomState` SipHash the standard aliases default to. Every module that keys a table imports the two aliases from here and constructs them through `Default`, so the hasher is chosen once and no call site names it.
//!
//! The crate's map keys are small `Copy` structs of packed integers — `TraceKey`, `CandidatesKey`, `ProspectKey` and `Pointer` in `engine.rs`, `Candidate` in `types.rs`, `Sym` in `model.rs`, `StanceId` in `index.rs`, the fixpoint's and the replay's window keys — whose derived `Hash` emits one write per field, and the tables they key are the widest the build holds (`--cache-census` reports how wide). SipHash costs a whole compression round per write on those keys and the settlement pays it on every probe; this hasher folds each word into the state with one rotate, one xor and one multiply. What makes the finalizer necessary is how hashbrown reads the finished word: the low bits pick the bucket and the top seven bits are the control byte it matches sixteen at a time, so a hasher whose low bits carry only the last field written — a five-value alphabet in these keys — measures far slower than SipHash, while a finalized one, whose every output bit depends on every input bit, measures far faster (`doc/rebuild-design.md` §14.1). The finalizer is the reason the fast hasher wins here, and the reason a first pass without one lost.
//!
//! Two consequences worth holding onto. Iteration order over these tables is a function of the keys alone and identical across processes, where the standard tables reseed per process; nothing downstream reads that order, since every table that reaches output is drained and sorted first, so this is a narrowing of what the crate already tolerated. And the hasher is not collision-resistant against chosen keys: a caller that could pick the keys could pick colliding ones, which is irrelevant for a build tool reading a spec this repository authors and the reason to reach for the standard hasher instead were this crate ever fed untrusted input.

use std::hash::{BuildHasherDefault, Hasher};

/// A hash map on [`FastHasher`]. The third parameter is what the alias exists to fix, so a map is built through `Default` (`HashMap::default()`, `HashMap::with_capacity_and_hasher(n, Default::default())`) rather than `new`, which is defined only on the standard hasher.
pub type HashMap<K, V, S = BuildHasherDefault<FastHasher>> = std::collections::HashMap<K, V, S>;

/// A hash set on [`FastHasher`], built through `Default` for the reason the map is.
pub type HashSet<T, S = BuildHasherDefault<FastHasher>> = std::collections::HashSet<T, S>;

/// The hasher behind the crate's tables: one rotate-xor-multiply per word written, and an avalanche finalizer on `finish`. Every unsigned integer write folds one word; the signed writes keep the trait's defaults, which forward to the unsigned ones, and that is what carries a derived `Hash`'s enum and `Option` discriminants, which go out as `isize`. A byte slice folds eight bytes at a time and then a four, two and one byte tail, each read little-endian so the hash of a text is the same word on every host.
#[derive(Clone, Copy, Default)]
pub struct FastHasher {
    hash: u64,
}

const FOLD: u64 = 0x517c_c1b7_2722_0a95;

impl FastHasher {
    fn add(&mut self, word: u64) {
        self.hash = (self.hash.rotate_left(5) ^ word).wrapping_mul(FOLD);
    }
}

impl Hasher for FastHasher {
    fn finish(&self) -> u64 {
        let mut word = self.hash;
        word ^= word >> 30;
        word = word.wrapping_mul(0xbf58_476d_1ce4_e5b9);
        word ^= word >> 27;
        word = word.wrapping_mul(0x94d0_49bb_1331_11eb);
        word ^ (word >> 31)
    }

    fn write(&mut self, mut bytes: &[u8]) {
        while let Some((word, rest)) = bytes.split_first_chunk::<8>() {
            self.add(u64::from_le_bytes(*word));
            bytes = rest;
        }
        if let Some((word, rest)) = bytes.split_first_chunk::<4>() {
            self.add(u64::from(u32::from_le_bytes(*word)));
            bytes = rest;
        }
        if let Some((word, rest)) = bytes.split_first_chunk::<2>() {
            self.add(u64::from(u16::from_le_bytes(*word)));
            bytes = rest;
        }
        if let Some(&byte) = bytes.first() {
            self.add(u64::from(byte));
        }
    }

    fn write_u8(&mut self, value: u8) {
        self.add(u64::from(value));
    }

    fn write_u16(&mut self, value: u16) {
        self.add(u64::from(value));
    }

    fn write_u32(&mut self, value: u32) {
        self.add(u64::from(value));
    }

    fn write_u64(&mut self, value: u64) {
        self.add(value);
    }

    fn write_u128(&mut self, value: u128) {
        self.add(value as u64);
        self.add((value >> 64) as u64);
    }

    fn write_usize(&mut self, value: usize) {
        self.add(value as u64);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::hash::{BuildHasher, Hash};
    use std::rc::Rc;

    fn hash_of<T: Hash>(value: &T) -> u64 {
        BuildHasherDefault::<FastHasher>::default().hash_one(value)
    }

    #[test]
    fn two_tables_built_from_the_same_insertions_iterate_in_the_same_order() {
        let build = || {
            (0..64u32)
                .map(|key| (key, ()))
                .collect::<HashMap<u32, ()>>()
        };
        let first: Vec<u32> = build().keys().copied().collect();
        let second: Vec<u32> = build().keys().copied().collect();
        assert_eq!(first, second);
    }

    #[test]
    fn the_low_bits_of_the_hash_see_the_high_bits_of_the_last_word_written() {
        let finished = |early: u64, last: u64| {
            let mut hasher = FastHasher::default();
            hasher.write_u64(early);
            hasher.write_u64(last);
            hasher.finish()
        };
        let mut stuck = Vec::new();
        for key in 0..64u64 {
            let early = key.wrapping_mul(0x9e37_79b9_7f4a_7c15);
            let last = key.wrapping_mul(0x2545_f491_4f6c_dd1d);
            let base = finished(early, last);
            for bit in 32..64 {
                if (base ^ finished(early, last ^ (1 << bit))) & 0xffff_ffff == 0 {
                    stuck.push((key, bit));
                }
            }
        }
        assert!(
            stuck.is_empty(),
            "flipping these (key, bit) pairs in the last word left the low 32 bits of the hash unchanged, which is what a finalizer-less multiply does to every one of them: {stuck:?}"
        );
    }

    #[test]
    fn an_owned_key_is_found_by_its_borrowed_form() {
        let mut by_text: HashMap<String, u32> = HashMap::default();
        by_text.insert("qsPea".to_owned(), 1);
        by_text.insert(String::new(), 2);
        assert_eq!(by_text.get("qsPea"), Some(&1));
        assert_eq!(by_text.get(""), Some(&2));
        assert_eq!(by_text.get("qsPe"), None);

        let mut pool: HashSet<Rc<str>> = HashSet::default();
        pool.insert(Rc::from("a fairly long label that outruns one word"));
        assert!(pool.contains("a fairly long label that outruns one word"));
        assert!(!pool.contains("a fairly long label that outruns one wor"));

        let mut by_row: HashMap<Vec<u32>, u32> = HashMap::default();
        by_row.insert(vec![1, 2, 3], 7);
        assert_eq!(by_row.get([1, 2, 3].as_slice()), Some(&7));
        assert_eq!(by_row.get([1, 2].as_slice()), None);

        let mut by_bytes: HashMap<Box<[u8]>, u32> = HashMap::default();
        by_bytes.insert(Box::from(&b"0123456789abcdef"[..]), 9);
        assert_eq!(by_bytes.get(&b"0123456789abcdef"[..]), Some(&9));
    }

    #[test]
    fn a_byte_slice_hashes_by_its_bytes_and_its_length() {
        let text = b"0123456789abcdefghij";
        let mut whole = FastHasher::default();
        whole.write(text);
        let mut prefixed = FastHasher::default();
        prefixed.write(&text[..3]);
        assert_ne!(whole.finish(), prefixed.finish());
        assert_eq!(hash_of(&[1u8, 2, 3]), hash_of(&vec![1u8, 2, 3]));
        assert_ne!(hash_of(&[0u8; 8]), hash_of(&[0u8; 9]));
    }

    #[test]
    fn no_crate_module_imports_the_standard_hash_tables() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let mut pending: Vec<std::path::PathBuf> =
            ["src", "tests"].iter().map(|dir| root.join(dir)).collect();
        let mut strays = Vec::new();
        while let Some(dir) = pending.pop() {
            for entry in std::fs::read_dir(&dir).expect("the crate's source directories read") {
                let path = entry.expect("a directory entry reads").path();
                if path.is_dir() {
                    pending.push(path);
                    continue;
                }
                if path.extension().is_none_or(|ext| ext != "rs") || path.ends_with("src/hash.rs") {
                    continue;
                }
                let source = std::fs::read_to_string(&path).expect("a crate source file reads");
                let reaches_for_a_table = source.match_indices("collections::").any(|(at, _)| {
                    let statement = source[at..].split(';').next().unwrap_or_default();
                    statement.contains("Hash")
                        || statement.contains("hash_")
                        || statement.contains('*')
                });
                if reaches_for_a_table {
                    strays.push(
                        path.strip_prefix(root)
                            .unwrap_or(&path)
                            .display()
                            .to_string(),
                    );
                }
            }
        }
        assert!(
            strays.is_empty(),
            "these files reach for the standard library's hash tables; import HashMap and HashSet from crate::hash instead: {}",
            strays.join(", ")
        );
    }
}
