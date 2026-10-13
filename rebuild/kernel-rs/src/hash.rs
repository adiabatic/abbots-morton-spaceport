//! The crate's hash maps and sets: [`HashMap`] and [`HashSet`] are the standard library's tables on [`FastHasher`], a multiply-rotate hasher with an avalanche finalizer, in place of the standard `RandomState` SipHash. Every module imports the two aliases from here and constructs them through `Default`, so no call site names the hasher. [`ShardedMap`] splits one map over many of these tables, for a map large enough that one table's doubling would set a peak.
//!
//! The crate's map keys are small `Copy` structs of packed integers: `TraceKey`, `CandidatesKey` and `ProspectKey` in `engine.rs` (each a run of `u16` field ordinals and one packed word of token kinds), `Pointer` beside them, `Candidate` in `types.rs`, `Sym` in `model.rs`, `StanceId` in `index.rs`, and the fixpoint's and the replay's window keys. The three memo keys write their fields packed into two to four words (their `Hash` impls in `engine.rs`), and the others' derived `Hash` makes one write per field. The tables they key are the largest the build holds (`--cache-stats` reports their sizes). The standard SipHash-1-3 has no integer fast path: every write goes through its byte buffer, it runs a compression round per eight bytes, and `finish` runs four more. Settlement pays that on every probe. This hasher folds each word into the state with one rotate, one xor and one multiply.
//!
//! The finalizer is required because of how hashbrown reads the hash: the low bits pick the bucket, and the top seven bits are the control byte it compares a group at a time. Without a finalizer, the last multiply carries each bit of the last word written only upward, so the low bits cannot see that word's high bits. Such a hasher measured far slower than SipHash on these keys, and the finalized one, whose every output bit depends on every input bit, measured far faster (`doc/rebuild-design.md` §14.1).
//!
//! Two consequences follow. Iteration order over these tables depends only on the keys and is the same in every process, where the standard tables reseed per process. No result depends on that order. Every table that reaches output is drained and sorted first, except the replay's window memo: [`crate::replay::Replay::write_window_memo`] writes its rows in table order, and the file's reader indexes them by window. And the hasher is not collision-resistant: a caller that could choose the keys could choose colliding ones. That does not matter for a build tool reading a spec this repository authors, but a crate fed untrusted input should use the standard hasher.

use std::hash::{BuildHasher, BuildHasherDefault, Hash, Hasher};

/// A hash map on [`FastHasher`]. Build one through `Default` (`HashMap::default()`, `HashMap::with_capacity_and_hasher(n, Default::default())`), because `new` is defined only for the standard hasher.
pub type HashMap<K, V, S = BuildHasherDefault<FastHasher>> = std::collections::HashMap<K, V, S>;

/// A hash set on [`FastHasher`], built through `Default` for the same reason as [`HashMap`].
pub type HashSet<T, S = BuildHasherDefault<FastHasher>> = std::collections::HashSet<T, S>;

/// How many bits of a key's hash pick its [`ShardedMap`] shard: four, for sixteen shards, so a doubling allocates a sixteenth of what one table's would. More shards would not lower a peak further: each shard spends more of its growth in tables small enough for the macOS allocator's medium regions, which keep a freed table's pages in the resident size, though not in the footprint, while a freed large table goes back to the system at once. The comment on `SCRATCH_PEAK_BYTES` in `rebuild/pipeline/kernel_exec.py` has the readings.
const SHARD_BITS: u32 = 4;

/// A hash map split into [`HashMap`] shards by bits of each key's hash that no shard's table reads, so that it grows one shard at a time. A table grows by allocating one of twice as many buckets and moving every entry across before freeing the old one, so while it doubles it holds both, one and a half times the size it grows to; a table of millions of windows that doubles beside the memos near an enumeration's peak raises that peak by its whole new size. The shards take keys at one rate, since the bits that pick a shard are as uniform as the rest of the hash, so they reach each size at about the point one table holding every key would, one after another, and each doubling holds only one shard's two tables beside the others. [`ShardedMap::get`] and [`ShardedMap::insert`] pick the key's shard on each call; a caller that looks a key up and then inserts it can reach the shard once through [`ShardedMap::shard_mut`] and call the table's own methods there.
#[derive(Clone, Debug)]
pub struct ShardedMap<K, V> {
    shards: Vec<HashMap<K, V>>,
}

impl<K, V> Default for ShardedMap<K, V> {
    fn default() -> Self {
        Self {
            shards: (0..1 << SHARD_BITS).map(|_| HashMap::default()).collect(),
        }
    }
}

impl<K: Hash + Eq, V> ShardedMap<K, V> {
    /// The index of the shard that holds `key`: the bits just below the hash's top seven. Hashbrown picks a bucket with the low bits and compares the top seven as the control byte, so the keys one shard holds still spread over its buckets and its control bytes.
    fn index(key: &K) -> usize {
        let hash = BuildHasherDefault::<FastHasher>::default().hash_one(key);
        (hash >> (64 - 7 - SHARD_BITS)) as usize & ((1 << SHARD_BITS) - 1)
    }

    /// The shard that holds `key`, or would hold it once inserted. Insert into it only `key` itself: a different key put there is in the wrong shard, so no later lookup finds it.
    pub fn shard_mut(&mut self, key: &K) -> &mut HashMap<K, V> {
        &mut self.shards[Self::index(key)]
    }

    /// The value `key` maps to, if any.
    pub fn get(&self, key: &K) -> Option<&V> {
        self.shards[Self::index(key)].get(key)
    }

    /// Whether `key` maps to a value.
    pub fn contains_key(&self, key: &K) -> bool {
        self.shards[Self::index(key)].contains_key(key)
    }

    /// Map `key` to `value` in its shard, returning the value it replaced.
    pub fn insert(&mut self, key: K, value: V) -> Option<V> {
        self.shard_mut(&key).insert(key, value)
    }

    /// The shards' lengths added up.
    pub fn len(&self) -> usize {
        self.shards.iter().map(HashMap::len).sum()
    }

    /// Whether every shard is empty.
    pub fn is_empty(&self) -> bool {
        self.shards.iter().all(HashMap::is_empty)
    }

    /// The shards' capacities added up.
    pub fn capacity(&self) -> usize {
        self.shards.iter().map(HashMap::capacity).sum()
    }

    /// The shards, to be drained one at a time, so that a drain into another collection frees each shard's table once its entries have moved instead of holding every table until the last entry has.
    pub fn into_shards(self) -> std::vec::IntoIter<HashMap<K, V>> {
        self.shards.into_iter()
    }
}

impl<K: Hash + Eq, V> FromIterator<(K, V)> for ShardedMap<K, V> {
    fn from_iter<I: IntoIterator<Item = (K, V)>>(entries: I) -> Self {
        let mut map = Self::default();
        for (key, value) in entries {
            map.insert(key, value);
        }
        map
    }
}

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
    fn a_sharded_map_holds_each_key_once_and_spreads_the_keys_evenly_over_its_shards() {
        let key = |n: u32| [n & 63, (n >> 6) & 63, n >> 12, 0, 0, 0].map(crate::stream::Label);
        let count = 1u32 << 16;
        let mut map: ShardedMap<[crate::stream::Label; 6], u32> = ShardedMap::default();
        for n in 0..count {
            assert_eq!(map.shard_mut(&key(n)).insert(key(n), n), None);
        }
        for n in 0..count {
            assert_eq!(map.get(&key(n)), Some(&n));
        }
        assert_eq!(map.len(), count as usize);
        let mean = count as usize >> SHARD_BITS;
        let mut drained = Vec::new();
        for shard in map.into_shards() {
            assert!(
                shard.len().abs_diff(mean) < mean / 8,
                "a shard holds {} keys where the mean is {mean}",
                shard.len()
            );
            drained.extend(shard.into_values());
        }
        drained.sort_unstable();
        assert_eq!(drained, (0..count).collect::<Vec<u32>>());
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
    fn the_import_check_catches_a_renamed_collections_module() {
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
