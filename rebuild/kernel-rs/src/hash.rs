//! The crate's hash maps and sets: [`HashMap`] and [`HashSet`] are the standard library's tables on [`FastHasher`], a multiply-rotate hasher with an avalanche finalizer, in place of the standard `RandomState` SipHash. Every module imports the two aliases from here and constructs them through `Default`, so no call site names the hasher.
//!
//! The crate's map keys are small `Copy` structs of packed integers: `TraceKey`, `CandidatesKey` and `ProspectKey` in `engine.rs` (each a run of `u16` field ordinals and one packed word of token kinds), `Pointer` beside them, `Candidate` in `types.rs`, `Sym` in `model.rs`, `StanceId` in `index.rs`, and the fixpoint's and the replay's window keys. Their derived `Hash` makes one write per field, and the tables they key are the largest the build holds (`--cache-census` reports their sizes). The standard SipHash-1-3 has no integer fast path: every write goes through its byte buffer, it runs a compression round per eight bytes, and `finish` runs four more. Settlement pays that on every probe. This hasher folds each word into the state with one rotate, one xor and one multiply.
//!
//! The finalizer is required because of how hashbrown reads the hash: the low bits pick the bucket, and the top seven bits are the control byte it compares a group at a time. Without a finalizer, the last multiply carries each bit of the last word written only upward, so the low bits cannot see that word's high bits. Such a hasher measured far slower than SipHash on these keys, and the finalized one, whose every output bit depends on every input bit, measured far faster (`doc/rebuild-design.md` §14.1).
//!
//! Two consequences follow. Iteration order over these tables depends only on the keys and is the same in every process, where the standard tables reseed per process. Nothing reads that order, since every table that reaches output is drained and sorted first. And the hasher is not collision-resistant: a caller that could choose the keys could choose colliding ones. That does not matter for a build tool reading a spec this repository authors, but a crate fed untrusted input should use the standard hasher.

use std::hash::{BuildHasherDefault, Hasher};

/// A hash map on [`FastHasher`]. Build one through `Default` (`HashMap::default()`, `HashMap::with_capacity_and_hasher(n, Default::default())`), because `new` is defined only for the standard hasher.
pub type HashMap<K, V, S = BuildHasherDefault<FastHasher>> = std::collections::HashMap<K, V, S>;

/// A hash set on [`FastHasher`], built through `Default` for the same reason as [`HashMap`].
pub type HashSet<T, S = BuildHasherDefault<FastHasher>> = std::collections::HashSet<T, S>;

/// The hasher behind the crate's tables: one rotate-xor-multiply per word written, and an avalanche finalizer on `finish`. Every unsigned integer write folds one word. The signed writes keep the trait's defaults, which forward to the unsigned ones; a derived `Hash` writes enum and `Option` discriminants as `isize` this way. A byte slice folds eight bytes at a time, then a four-, two- and one-byte tail, each read little-endian so a text hashes the same on every host.
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

    /// Whether a source names a standard hash table, a hash module or a glob under a `collections` path, or renames the `collections` module, after which a `c::HashMap` names no `collections` path.
    fn reaches_for_a_standard_table(source: &str) -> bool {
        source.match_indices("collections").any(|(at, name)| {
            if source[..at].ends_with(|c: char| c.is_alphanumeric() || c == '_') {
                return false;
            }
            let statement = source[at + name.len()..]
                .split(';')
                .next()
                .unwrap_or_default();
            let words: Vec<&str> = statement
                .split(|c: char| !(c.is_alphanumeric() || c == '_'))
                .filter(|word| !word.is_empty())
                .collect();
            match statement.strip_prefix("::") {
                Some(path) => {
                    path.contains("Hash")
                        || path.contains("hash_")
                        || path.contains('*')
                        || words.windows(2).any(|pair| pair == ["self", "as"])
                }
                None => words.first() == Some(&"as"),
            }
        })
    }

    #[test]
    fn the_import_tripwire_catches_a_renamed_collections_module() {
        for stray in [
            "use std::collections::HashMap;",
            "use std::collections::hash_map::Entry;",
            "use std::collections::*;",
            "fn f() -> std::collections::HashSet<u32> { Default::default() }",
            "use std::collections as c;\nfn f() -> c::HashMap<u32, u32> { c::HashMap::new() }",
            "use std::{collections as c, fmt};",
            "use std::collections::{self as c};",
        ] {
            assert!(reaches_for_a_standard_table(stray), "missed: {stray}");
        }
        for clean in [
            "use std::collections::BTreeSet;",
            "use std::collections::{BTreeMap as Map, VecDeque};",
            "use crate::hash::{HashMap, HashSet};",
            "let collections_as_text = 1;",
        ] {
            assert!(!reaches_for_a_standard_table(clean), "flagged: {clean}");
        }
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
                if reaches_for_a_standard_table(&source) {
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
