//! The interning tables and indexes the corpus build's columns are built on. [`StringTable`] interns the strings an id column names, in one text arena with a hash index over it; [`TuplePool`] interns the tuples a column names, in one item arena, and [`MappingPool`] the per-config class maps as tuples of pairs in their insertion order; each assigns ids in first-seen order with id 0 the empty value. [`WindowIndex`] holds units in window order beside each one's window packed into a word ([`window_word`]), [`sorted_by_word`] orders a column of 64-bit words, and [`KeyIndex`] maps a column of fixed-width keys to their ordinals in an open-addressed table, refusing two ordinals with one key ([`RepeatedKey`]).

use std::cmp::Ordering;
use std::fmt;
use std::hash::{BuildHasher, BuildHasherDefault, Hash};

use ams_m1_kernel::hash::FastHasher;

/// The empty slot of an open-addressed table, which no id or ordinal takes.
pub const KEY_EMPTY: u32 = u32::MAX;

/// The code points a window word holds at most.
pub const WINDOW_SLOTS: usize = 4;

/// A value's hash under the kernel's [`FastHasher`], which the tables probe from; no table's ids or lookups depend on which hasher it is.
fn hash_of<H: Hash + ?Sized>(value: &H) -> u64 {
    BuildHasherDefault::<FastHasher>::default().hash_one(value)
}

/// The `u32` a table stores for `count`, which stays below [`KEY_EMPTY`].
fn id_for(count: usize) -> u32 {
    u32::try_from(count)
        .ok()
        .filter(|&id| id != KEY_EMPTY)
        .expect("a column table holds fewer values than a u32 names")
}

/// An open-addressed table of ids at a load of at most one half, probed linearly from a value's hash; the values themselves live in the table that owns the slots, which tells the probe whether an id holds the value sought.
#[derive(Clone, Debug)]
struct Slots {
    slots: Vec<u32>,
    held: usize,
}

impl Slots {
    fn new() -> Self {
        Self {
            slots: vec![KEY_EMPTY; 8],
            held: 0,
        }
    }

    /// The id whose value `holds` accepts among those a probe from `hash` meets, or the vacant slot the probe ends on.
    fn probe(
        &self,
        hash: u64,
        mut holds: impl FnMut(u32) -> bool,
    ) -> std::result::Result<u32, usize> {
        let mask = self.slots.len() - 1;
        let mut at = hash as usize & mask;
        loop {
            match self.slots[at] {
                KEY_EMPTY => return Err(at),
                id if holds(id) => return Ok(id),
                _ => at = (at + 1) & mask,
            }
        }
    }

    /// Put `id` in the vacant slot `at` a probe ended on; past half load the slots double and every id is placed again by its value's hash, `hash_of`.
    fn place(&mut self, at: usize, id: u32, hash_of: impl Fn(u32) -> u64) {
        self.slots[at] = id;
        self.held += 1;
        if self.held * 2 > self.slots.len() {
            let size = self.slots.len() * 2;
            let held = std::mem::replace(&mut self.slots, vec![KEY_EMPTY; size]);
            for id in held.into_iter().filter(|&id| id != KEY_EMPTY) {
                let mut at = hash_of(id) as usize & (size - 1);
                while self.slots[at] != KEY_EMPTY {
                    at = (at + 1) & (size - 1);
                }
                self.slots[at] = id;
            }
        }
    }
}

fn bounded<'a, T>(items: &'a [T], bounds: &[u32], id: u32) -> &'a [T] {
    let id = id as usize;
    &items[bounds[id] as usize..bounds[id + 1] as usize]
}

fn text_at<'a>(text: &'a str, bounds: &[u32], id: u32) -> &'a str {
    let id = id as usize;
    &text[bounds[id] as usize..bounds[id + 1] as usize]
}

fn offset(length: usize) -> u32 {
    u32::try_from(length).expect("a column table's arena holds fewer than 4 GiB")
}

/// The interning table for the strings an id column names: the distinct strings in one text arena in first-seen order, id 0 the empty string, beside a hash index over them.
#[derive(Clone, Debug)]
pub struct StringTable {
    text: String,
    bounds: Vec<u32>,
    slots: Slots,
}

impl Default for StringTable {
    fn default() -> Self {
        Self::new()
    }
}

impl StringTable {
    pub fn new() -> Self {
        let mut table = Self {
            text: String::new(),
            bounds: vec![0],
            slots: Slots::new(),
        };
        table.id("");
        table
    }

    /// The string's id, assigned as the next one when the table does not hold it.
    pub fn id(&mut self, value: &str) -> u32 {
        let (text, bounds) = (&self.text, &self.bounds);
        match self
            .slots
            .probe(hash_of(value), |id| text_at(text, bounds, id) == value)
        {
            Ok(id) => id,
            Err(at) => {
                let id = id_for(self.bounds.len() - 1);
                self.text.push_str(value);
                self.bounds.push(offset(self.text.len()));
                let (text, bounds) = (&self.text, &self.bounds);
                self.slots
                    .place(at, id, |held| hash_of(text_at(text, bounds, held)));
                id
            }
        }
    }

    /// The string's id, or None when the table does not hold it; nothing is assigned.
    pub fn find(&self, value: &str) -> Option<u32> {
        let (text, bounds) = (&self.text, &self.bounds);
        self.slots
            .probe(hash_of(value), |id| text_at(text, bounds, id) == value)
            .ok()
    }

    /// Id 0 for None, the string's id otherwise.
    pub fn optional(&mut self, value: Option<&str>) -> u32 {
        value.map_or(0, |value| self.id(value))
    }

    pub fn get(&self, id: u32) -> &str {
        text_at(&self.text, &self.bounds, id)
    }
}

/// What a sealed tuple pool answers an id with, a `ValueError`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Sealed;

impl fmt::Display for Sealed {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(
            "the tuple pool is sealed: its columns are written and it assigns no further id",
        )
    }
}

/// The interning pool for the tuples a column names: the distinct tuples' items in one arena in first-seen order, id 0 the empty tuple, beside a hash index over them that [`TuplePool::seal`] drops once no further id is wanted.
#[derive(Clone, Debug)]
pub struct TuplePool<T> {
    items: Vec<T>,
    bounds: Vec<u32>,
    slots: Option<Slots>,
}

impl<T: Clone + Eq + Hash> Default for TuplePool<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T: Clone + Eq + Hash> TuplePool<T> {
    pub fn new() -> Self {
        let mut pool = Self {
            items: Vec::new(),
            bounds: vec![0],
            slots: Some(Slots::new()),
        };
        pool.id(&[]).expect("a new pool is not sealed");
        pool
    }

    /// The tuple's id, assigned as the next one when the pool does not hold it; a sealed pool assigns none and finds none.
    pub fn id(&mut self, value: &[T]) -> std::result::Result<u32, Sealed> {
        let Some(slots) = &mut self.slots else {
            return Err(Sealed);
        };
        let (items, bounds) = (&self.items, &self.bounds);
        match slots.probe(hash_of(value), |id| bounded(items, bounds, id) == value) {
            Ok(id) => Ok(id),
            Err(at) => {
                let id = id_for(self.bounds.len() - 1);
                self.items.extend_from_slice(value);
                self.bounds.push(offset(self.items.len()));
                let (items, bounds) = (&self.items, &self.bounds);
                slots.place(at, id, |held| hash_of(bounded(items, bounds, held)));
                Ok(id)
            }
        }
    }

    pub fn get(&self, id: u32) -> &[T] {
        bounded(&self.items, &self.bounds, id)
    }

    /// Drop the index; reads by id go on, and every id call after it is refused.
    pub fn seal(&mut self) {
        self.slots = None;
    }

    pub fn sealed(&self) -> bool {
        self.slots.is_none()
    }
}

/// The interning pool for the per-config class maps, each held as its `(key, value)` pairs in insertion order, which the fragment's bytes keep, id 0 the empty mapping.
#[derive(Clone, Debug)]
pub struct MappingPool<T> {
    pairs: TuplePool<(T, T)>,
}

impl<T: Clone + Eq + Hash> Default for MappingPool<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<T: Clone + Eq + Hash> MappingPool<T> {
    pub fn new() -> Self {
        Self {
            pairs: TuplePool::new(),
        }
    }

    pub fn id(&mut self, value: &[(T, T)]) -> u32 {
        self.pairs
            .id(value)
            .expect("a mapping pool is never sealed")
    }

    pub fn get(&self, id: u32) -> &[(T, T)] {
        self.pairs.get(id)
    }
}

/// The positions of `words` in ascending word order, equal words in position order.
pub fn sorted_by_word(words: &[u64]) -> Vec<u32> {
    let mut order: Vec<u32> = (0..id_for(words.len())).collect();
    order.sort_by_key(|&at| words[at as usize]);
    order
}

/// A window's code points packed into one word, 16 bits each and right-aligned, so words order windows by length and then by code points; None for a window longer than [`WINDOW_SLOTS`], empty, leading with zero, or holding a code point above U+FFFF.
pub fn window_word(values: &[u32]) -> Option<u64> {
    if values.is_empty() || values.len() > WINDOW_SLOTS || values[0] == 0 {
        return None;
    }
    values.iter().try_fold(0u64, |word, &value| {
        (value <= 0xFFFF).then(|| (word << 16) | u64::from(value))
    })
}

/// Windows in `audit.triage_key`'s order: by length, then by code points.
fn window_order(left: &[u32], right: &[u32]) -> Ordering {
    left.len().cmp(&right.len()).then_with(|| left.cmp(right))
}

/// A window index's keys: every window as its word, or, when some window has no word, every window itself, ordered by [`window_order`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum WindowKeys {
    Words(Vec<u64>),
    Windows(Vec<Box<[u32]>>),
}

impl WindowKeys {
    fn len(&self) -> usize {
        match self {
            Self::Words(words) => words.len(),
            Self::Windows(windows) => windows.len(),
        }
    }

    fn same(&self, left: usize, right: usize) -> bool {
        match self {
            Self::Words(words) => words[left] == words[right],
            Self::Windows(windows) => windows[left] == windows[right],
        }
    }
}

/// A table's units in window order, the units of one window in the order they were given, beside each one's key.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WindowIndex {
    pub ordinals: Vec<u32>,
    pub keys: WindowKeys,
}

impl WindowIndex {
    /// The index over `(ordinal, window)` pairs, sorted by window, equal windows in the order given.
    pub fn build(pairs: impl IntoIterator<Item = (u32, Vec<u32>)>) -> Self {
        let (ordinals, windows): (Vec<u32>, Vec<Vec<u32>>) = pairs.into_iter().unzip();
        let mut order: Vec<usize> = (0..ordinals.len()).collect();
        let ordered = |order: &[usize]| order.iter().map(|&at| ordinals[at]).collect();
        match windows
            .iter()
            .map(|window| window_word(window))
            .collect::<Option<Vec<u64>>>()
        {
            Some(words) => {
                order.sort_by_key(|&at| words[at]);
                Self {
                    ordinals: ordered(&order),
                    keys: WindowKeys::Words(order.iter().map(|&at| words[at]).collect()),
                }
            }
            None => {
                order.sort_by(|&left, &right| window_order(&windows[left], &windows[right]));
                Self {
                    ordinals: ordered(&order),
                    keys: WindowKeys::Windows(
                        order
                            .iter()
                            .map(|&at| windows[at].clone().into_boxed_slice())
                            .collect(),
                    ),
                }
            }
        }
    }

    /// The ordinals of the units whose window is `window`, in the index's order.
    pub fn candidates(&self, window: &[u32]) -> &[u32] {
        let (low, high) = match &self.keys {
            WindowKeys::Words(words) => {
                let Some(word) = window_word(window) else {
                    return &[];
                };
                (
                    words.partition_point(|&held| held < word),
                    words.partition_point(|&held| held <= word),
                )
            }
            WindowKeys::Windows(windows) => (
                windows.partition_point(|held| window_order(held, window) == Ordering::Less),
                windows.partition_point(|held| window_order(held, window) != Ordering::Greater),
            ),
        };
        &self.ordinals[low..high]
    }

    /// Every `(start, end)` stretch of positions into the ordinals of two or more units that share a window, in window order.
    pub fn runs(&self) -> impl Iterator<Item = (usize, usize)> + '_ {
        let count = self.keys.len();
        let (mut start, mut position) = (0, 0);
        std::iter::from_fn(move || {
            while position < count {
                position += 1;
                if position == count || !self.keys.same(position, start) {
                    let run = (start, position);
                    start = position;
                    if run.1 - run.0 > 1 {
                        return Some(run);
                    }
                }
            }
            None
        })
    }

    /// The index over a table compacted with this `survivor` map and `removed` bytes: each kept unit at its new ordinal, in the same order.
    pub fn remapped(&self, survivor: &[u32], removed: &[u8]) -> Self {
        let kept: Vec<usize> = (0..self.ordinals.len())
            .filter(|&position| removed[self.ordinals[position] as usize] == 0)
            .collect();
        Self {
            ordinals: kept
                .iter()
                .map(|&position| survivor[self.ordinals[position] as usize])
                .collect(),
            keys: match &self.keys {
                WindowKeys::Words(words) => {
                    WindowKeys::Words(kept.iter().map(|&position| words[position]).collect())
                }
                WindowKeys::Windows(windows) => WindowKeys::Windows(
                    kept.iter()
                        .map(|&position| windows[position].clone())
                        .collect(),
                ),
            },
        }
    }
}

/// Two ordinals that hold one key, the first such pair in ordinal order.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RepeatedKey {
    pub first: u32,
    pub second: u32,
}

/// Why a key index was not built.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KeyIndexError {
    /// The width is not a multiple of eight, or the column holds fewer than `count` keys (`KeyIndex`'s `ValueError`).
    Shape {
        count: usize,
        width: usize,
    },
    Repeated(RepeatedKey),
}

impl fmt::Display for KeyIndexError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Shape { count, width } => write!(
                formatter,
                "a key index reads {count} keys of {width} bytes, a multiple of eight"
            ),
            Self::Repeated(RepeatedKey { first, second }) => {
                write!(formatter, "ordinals {first} and {second} hold one key")
            }
        }
    }
}

/// A key's first eight bytes read as a native integer, its slot before the mask, as `KeyIndex` reads them through `memoryview.cast("Q")` and `int.from_bytes(key[:8], sys.byteorder)`.
fn slot_word(key: &[u8]) -> u64 {
    let mut word = [0u8; 8];
    let held = key.len().min(8);
    word[..held].copy_from_slice(&key[..held]);
    u64::from_ne_bytes(word)
}

/// An open-addressed table of `u32` ordinals over a column of `width`-byte keys at a load below one half: a key's slot is its first eight bytes read as a native integer, and a slot's holder is checked against the whole key in the column. The index holds no borrow of the column; each [`KeyIndex::find`] is passed the column it was built over.
#[derive(Clone, Debug)]
pub struct KeyIndex {
    table: Vec<u32>,
    width: usize,
}

impl KeyIndex {
    /// Index the first `count` keys of `column`, inserting the ordinals in ascending order; refuse a width that is not a multiple of eight or a column shorter than `count` keys, and the first ordinal whose key an earlier one holds.
    pub fn new(
        column: &[u8],
        width: usize,
        count: usize,
    ) -> std::result::Result<Self, KeyIndexError> {
        if !width.is_multiple_of(8)
            || width
                .checked_mul(count)
                .is_none_or(|span| column.len() < span)
        {
            return Err(KeyIndexError::Shape { count, width });
        }
        let size = 1usize << (usize::BITS - count.saturating_mul(2).leading_zeros()).max(1);
        let mask = size - 1;
        let mut table = vec![KEY_EMPTY; size];
        for ordinal in 0..count {
            let key = &column[ordinal * width..(ordinal + 1) * width];
            let mut slot = slot_word(key) as usize & mask;
            loop {
                let held = table[slot];
                if held == KEY_EMPTY {
                    table[slot] = id_for(ordinal);
                    break;
                }
                let start = held as usize * width;
                if &column[start..start + width] == key {
                    return Err(KeyIndexError::Repeated(RepeatedKey {
                        first: held,
                        second: id_for(ordinal),
                    }));
                }
                slot = (slot + 1) & mask;
            }
        }
        Ok(Self { table, width })
    }

    /// The ordinal whose key in `column` is `key`, or None; a key of another width is held by none.
    pub fn find(&self, column: &[u8], key: &[u8]) -> Option<u32> {
        if key.len() != self.width {
            return None;
        }
        let mask = self.table.len() - 1;
        let mut slot = slot_word(key) as usize & mask;
        loop {
            let held = self.table[slot];
            if held == KEY_EMPTY {
                return None;
            }
            let start = held as usize * self.width;
            if &column[start..start + self.width] == key {
                return Some(held);
            }
            slot = (slot + 1) & mask;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_string_table_assigns_ids_in_first_seen_order_from_the_empty_string() {
        let mut table = StringTable::new();
        assert_eq!(table.find(""), Some(0));
        assert_eq!(
            ["a", "é·Day", "a", "", "b"].map(|value| table.id(value)),
            [1, 2, 1, 0, 3]
        );
        assert_eq!((table.optional(None), table.optional(Some("b"))), (0, 3));
        assert_eq!((table.find("c"), table.get(2)), (None, "é·Day"));
        let many: Vec<u32> = (0..1000).map(|at| table.id(&format!("s{at}"))).collect();
        assert_eq!(many, (4..1004).collect::<Vec<u32>>());
        assert!((0..1000).all(|at| table.find(&format!("s{at}")) == Some(at + 4)));
    }

    #[test]
    fn a_sealed_tuple_pool_reads_by_id_and_refuses_every_id_call() {
        let mut pool: TuplePool<u32> = TuplePool::new();
        assert_eq!(
            (
                pool.id(&[]),
                pool.id(&[1, 2]),
                pool.id(&[1]),
                pool.id(&[1, 2])
            ),
            (Ok(0), Ok(1), Ok(2), Ok(1))
        );
        assert_eq!(pool.get(1), &[1, 2][..]);
        pool.seal();
        assert_eq!((pool.id(&[1]), pool.get(2)), (Err(Sealed), &[1][..]));
        assert_eq!(
            Sealed.to_string(),
            "the tuple pool is sealed: its columns are written and it assigns no further id"
        );
    }

    #[test]
    fn a_mapping_pool_keeps_insertion_order() {
        let mut pool: MappingPool<u32> = MappingPool::new();
        assert_eq!(pool.id(&[(1, 2), (3, 4)]), 1);
        assert_eq!(pool.id(&[(3, 4), (1, 2)]), 2);
        assert_eq!((pool.id(&[]), pool.id(&[(1, 2), (3, 4)])), (0, 1));
        assert_eq!(pool.get(2), &[(3, 4), (1, 2)][..]);
    }

    #[test]
    fn a_window_word_holds_up_to_four_code_points_below_u_10000_with_no_leading_zero() {
        assert_eq!(window_word(&[0xE650, 0xE670]), Some(0xE650_E670));
        assert_eq!(window_word(&[0xFFFF; 4]), Some(u64::MAX));
        assert_eq!(window_word(&[1, 0]), Some(0x1_0000));
        for unpacked in [
            &[][..],
            &[0, 1],
            &[1, 2, 3, 4, 5],
            &[0x10000],
            &[1, 0x10000],
        ] {
            assert_eq!(window_word(unpacked), None);
        }
    }

    #[test]
    fn a_window_index_orders_by_length_then_code_points_and_keeps_given_order_within_a_window() {
        let index = WindowIndex::build([(4, vec![2]), (1, vec![1, 1]), (0, vec![2]), (3, vec![1])]);
        assert_eq!(index.ordinals, [3, 4, 0, 1]);
        assert_eq!(
            (index.candidates(&[2]), index.candidates(&[5])),
            (&[4, 0][..], &[][..])
        );
        assert_eq!(index.runs().collect::<Vec<_>>(), vec![(1, 3)]);
        let remapped = index.remapped(&[0, 0, 1, 2, 3], &[1, 0, 0, 0, 0]);
        assert_eq!(remapped.ordinals, [2, 3, 0]);
        assert_eq!(remapped.keys, WindowKeys::Words(vec![1, 2, 0x1_0001]));
        let unpacked = WindowIndex::build([(0, vec![1, 2, 3, 4, 5]), (1, vec![9]), (2, vec![])]);
        assert!(matches!(unpacked.keys, WindowKeys::Windows(_)));
        assert_eq!(unpacked.ordinals, [2, 1, 0]);
        assert_eq!(unpacked.candidates(&[]), &[2][..]);
    }

    #[test]
    fn sorting_by_word_keeps_equal_words_in_position_order() {
        assert_eq!(sorted_by_word(&[3, u64::MAX, 3, 0, 1]), [3, 4, 0, 2, 1]);
        assert!(sorted_by_word(&[]).is_empty());
    }

    #[test]
    fn a_key_index_finds_each_key_and_refuses_a_repeat_naming_the_first_pair() {
        let keys: Vec<[u8; 16]> = (0u8..20)
            .map(|at| [5, 5, 5, 5, 5, 5, 5, 5, at, 0, 0, 0, 0, 0, 0, 1])
            .collect();
        let column = keys.concat();
        let index = KeyIndex::new(&column, 16, keys.len()).expect("distinct keys");
        assert!(
            keys.iter()
                .zip(0..)
                .all(|(key, at)| index.find(&column, key) == Some(at))
        );
        assert_eq!(
            (index.find(&column, &[0; 16]), index.find(&column, &[5; 8])),
            (None, None)
        );
        let repeated = [keys[0], keys[1], keys[2], keys[1], keys[0]].concat();
        assert_eq!(
            KeyIndex::new(&repeated, 16, 5).err(),
            Some(KeyIndexError::Repeated(RepeatedKey {
                first: 1,
                second: 3
            }))
        );
        assert_eq!(
            KeyIndex::new(&column, 12, 1)
                .err()
                .map(|refused| refused.to_string()),
            Some("a key index reads 1 keys of 12 bytes, a multiple of eight".to_owned())
        );
        assert!(KeyIndex::new(&column, 16, 21).is_err());
        assert!(KeyIndex::new(&[], 32, 0).is_ok());
    }
}
