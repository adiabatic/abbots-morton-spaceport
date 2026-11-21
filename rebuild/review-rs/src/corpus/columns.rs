//! The interning tables and indexes the corpus build's columns are built on, as `rebuild/review/columns.py` holds them. [`StringTable`] interns the strings an id column names, in one text arena with a hash index over it; [`TuplePool`] interns the tuples a column names, in one item arena, and [`MappingPool`] the per-config class maps as tuples of pairs in their insertion order; each assigns ids in first-seen order with id 0 the empty value. [`WindowIndex`] holds units in window order beside each one's window packed into a word ([`window_word`]), [`sorted_by_word`] orders a column of 64-bit words, and [`KeyIndex`] maps a column of fixed-width keys to their ordinals in an open-addressed table, refusing two ordinals with one key ([`RepeatedKey`]).
//!
//! [`stage_columns`] is the `columns` stage of the stage fixtures.

use std::cmp::Ordering;
use std::collections::{BTreeMap, HashMap};
use std::fmt;
use std::fs::File;
use std::hash::{BuildHasher, BuildHasherDefault, DefaultHasher, Hash};
use std::io::{BufRead, BufReader, Write};
use std::path::Path;

use serde_json::{Value, json};

use crate::wire::{Result, WireError, error};

/// The empty slot of an open-addressed table, which no id or ordinal takes (`columns.KEY_EMPTY`).
pub const KEY_EMPTY: u32 = u32::MAX;

/// The code points a window word holds at most (`columns.WINDOW_SLOTS`).
pub const WINDOW_SLOTS: usize = 4;

fn hash_of<H: Hash + ?Sized>(value: &H) -> u64 {
    BuildHasherDefault::<DefaultHasher>::default().hash_one(value)
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

/// The interning table for the strings an id column names (`columns.StringTable`): the distinct strings in one text arena in first-seen order, id 0 the empty string, beside a hash index over them.
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

    /// The distinct strings without the empty one.
    pub fn len(&self) -> usize {
        self.bounds.len() - 2
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// The distinct strings' UTF-8 bytes summed (`StringTable.chars`).
    pub fn chars(&self) -> usize {
        self.text.len()
    }
}

/// What a sealed tuple pool answers an id with (`TuplePool.id`'s `ValueError`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Sealed;

impl fmt::Display for Sealed {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(
            "the tuple pool is sealed: its columns are written and it assigns no further id",
        )
    }
}

/// The interning pool for the tuples a column names (`columns.TuplePool`): the distinct tuples' items in one arena in first-seen order, id 0 the empty tuple, beside a hash index over them that [`TuplePool::seal`] drops once no further id is wanted.
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

    /// The pool's own copy of the tuple, interned as [`TuplePool::id`] interns it.
    pub fn pooled(&mut self, value: &[T]) -> std::result::Result<&[T], Sealed> {
        let id = self.id(value)?;
        Ok(self.get(id))
    }

    pub fn get(&self, id: u32) -> &[T] {
        bounded(&self.items, &self.bounds, id)
    }

    /// The distinct tuples without the empty one.
    pub fn len(&self) -> usize {
        self.bounds.len() - 2
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// The distinct tuples' lengths summed (`TuplePool.elements`).
    pub fn elements(&self) -> usize {
        self.items.len()
    }

    /// Drop the index; reads by id go on, and every id call after it is refused.
    pub fn seal(&mut self) {
        self.slots = None;
    }

    pub fn sealed(&self) -> bool {
        self.slots.is_none()
    }
}

/// The interning pool for the per-config class maps (`columns.MappingPool`), each held as its `(key, value)` pairs in insertion order, which the fragment's bytes keep, id 0 the empty mapping.
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

    pub fn pooled(&mut self, value: &[(T, T)]) -> &[(T, T)] {
        let id = self.id(value);
        self.get(id)
    }

    pub fn get(&self, id: u32) -> &[(T, T)] {
        self.pairs.get(id)
    }

    /// The distinct mappings without the empty one.
    pub fn len(&self) -> usize {
        self.pairs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.pairs.is_empty()
    }

    /// Two a distinct mapping's entry, summed: a key id and a value id (`MappingPool.elements`).
    pub fn elements(&self) -> usize {
        2 * self.pairs.elements()
    }
}

/// The positions of `words` in ascending word order, equal words in position order (`columns.sorted_by_word`).
pub fn sorted_by_word(words: &[u64]) -> Vec<u32> {
    let mut order: Vec<u32> = (0..id_for(words.len())).collect();
    order.sort_by_key(|&at| words[at as usize]);
    order
}

/// A window's code points packed into one word, 16 bits each and right-aligned, so words order windows by length and then by code points; None for a window longer than [`WINDOW_SLOTS`], empty, leading with zero, or holding a code point above U+FFFF (`columns.window_word`).
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

/// One window's key in an index ([`WindowIndex::key`]).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum WindowKey {
    Word(u64),
    Window(Box<[u32]>),
}

/// A table's units in window order, the units of one window in the order they were given, beside each one's key (`columns.WindowIndex`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WindowIndex {
    pub ordinals: Vec<u32>,
    pub keys: WindowKeys,
}

impl WindowIndex {
    /// The index over `(ordinal, window)` pairs, sorted by window, equal windows in the order given (`WindowIndex.build`).
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

    pub fn packed(&self) -> bool {
        matches!(self.keys, WindowKeys::Words(_))
    }

    /// The key a window has in this index, None for a window a packed index cannot hold.
    pub fn key(&self, window: &[u32]) -> Option<WindowKey> {
        match self.keys {
            WindowKeys::Words(_) => window_word(window).map(WindowKey::Word),
            WindowKeys::Windows(_) => Some(WindowKey::Window(window.into())),
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

    /// How many distinct windows the index holds.
    pub fn windows(&self) -> usize {
        (0..self.keys.len())
            .filter(|&position| position == 0 || !self.keys.same(position, position - 1))
            .count()
    }

    /// The index over a table compacted with this `survivor` map and `removed` bytes: each kept unit at its new ordinal, in the same order (`WindowIndex.remapped`).
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

    /// The bytes Python's index holds in arrays: four an ordinal, and eight a word when packed.
    pub fn nbytes(&self) -> usize {
        4 * self.ordinals.len()
            + match &self.keys {
                WindowKeys::Words(words) => 8 * words.len(),
                WindowKeys::Windows(_) => 0,
            }
    }
}

/// Two ordinals that hold one key, the first such pair in ordinal order (`columns.RepeatedKey`).
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

/// An open-addressed table of `u32` ordinals over a column of `width`-byte keys at a load below one half (`columns.KeyIndex`): a key's slot is its first eight bytes read as a native integer, and a slot's holder is checked against the whole key in the column. The index holds no borrow of the column; each [`KeyIndex::find`] is passed the column it was built over.
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

    /// The table's bytes, four a slot.
    pub fn nbytes(&self) -> usize {
        4 * self.table.len()
    }
}

fn items<'a>(value: &'a Value, what: &str) -> Result<&'a [Value]> {
    match value
        .as_object()
        .filter(|object| object.len() == 1)
        .and_then(|object| object.get("tuple").or_else(|| object.get("list")))
        .and_then(Value::as_array)
    {
        Some(items) => Ok(items),
        None => error(format!("{what} is not a typed tuple or list: {value:.200}")),
    }
}

fn arguments<'a, const COUNT: usize>(value: &'a Value, what: &str) -> Result<&'a [Value; COUNT]> {
    match items(value, what)?.try_into() {
        Ok(arguments) => Ok(arguments),
        Err(_) => error(format!("{what} does not hold {COUNT} values: {value:.200}")),
    }
}

fn text<'a>(value: &'a Value, what: &str) -> Result<&'a str> {
    value
        .as_str()
        .ok_or_else(|| WireError(format!("{what} is not a str: {value:.200}")))
}

fn count(value: &Value, what: &str) -> Result<usize> {
    value
        .as_u64()
        .and_then(|number| usize::try_from(number).ok())
        .ok_or_else(|| WireError(format!("{what} is not a count: {value:.200}")))
}

fn ordinal(value: &Value, what: &str) -> Result<u32> {
    value
        .as_u64()
        .and_then(|number| u32::try_from(number).ok())
        .ok_or_else(|| WireError(format!("{what} is not a u32: {value:.200}")))
}

fn bytes(value: &Value, what: &str) -> Result<Vec<u8>> {
    let hex = value
        .as_object()
        .filter(|object| object.len() == 1)
        .and_then(|object| object.get("bytes"))
        .and_then(Value::as_str)
        .filter(|hex| hex.len() % 2 == 0)
        .ok_or_else(|| WireError(format!("{what} is not typed bytes: {value:.200}")))?;
    (0..hex.len())
        .step_by(2)
        .map(|at| {
            u8::from_str_radix(&hex[at..at + 2], 16)
                .map_err(|_| WireError(format!("{what} is not typed bytes: {value:.200}")))
        })
        .collect()
}

fn window(value: &Value, what: &str) -> Result<Vec<u32>> {
    items(value, what)?
        .iter()
        .map(|item| ordinal(item, what))
        .collect()
}

/// A call's name and its arguments.
fn split_call<'a>(call: &'a Value, what: &str) -> Result<(&'a str, &'a [Value])> {
    items(call, what)?
        .split_first()
        .and_then(|(name, arguments)| Some((name.as_str()?, arguments)))
        .ok_or_else(|| WireError(format!("{what}: a call has no name: {call:.200}")))
}

fn tuple(items: Vec<Value>) -> Value {
    json!({ "tuple": items })
}

fn list(items: Vec<Value>) -> Value {
    json!({ "list": items })
}

fn listed<T: Into<Value> + Copy>(items: &[T]) -> Value {
    list(items.iter().map(|&item| item.into()).collect())
}

fn raised(class: &str, message: String) -> Value {
    tuple(vec![json!("raised"), json!(class), json!(message)])
}

fn found(id: Option<u32>) -> Value {
    id.map_or(Value::Null, Value::from)
}

fn window_value(window: &[u32]) -> Value {
    tuple(window.iter().map(|&value| value.into()).collect())
}

fn key_value(key: Option<WindowKey>) -> Value {
    match key {
        None => Value::Null,
        Some(WindowKey::Word(word)) => word.into(),
        Some(WindowKey::Window(window)) => tuple(vec![window.len().into(), window_value(&window)]),
    }
}

fn keys_value(keys: &WindowKeys) -> Value {
    match keys {
        WindowKeys::Words(words) => listed(words),
        WindowKeys::Windows(windows) => list(
            windows
                .iter()
                .map(|window| tuple(vec![window.len().into(), window_value(window)]))
                .collect(),
        ),
    }
}

fn index_value(index: &WindowIndex) -> Value {
    tuple(vec![listed(&index.ordinals), keys_value(&index.keys)])
}

fn runs_value(index: &WindowIndex) -> Value {
    list(
        index
            .runs()
            .map(|(start, end)| tuple(vec![start.into(), end.into()]))
            .collect(),
    )
}

/// A window index from a state line's packing, ordinals and keys: words when packed, `(length, window)` pairs otherwise.
fn window_index(packed: &Value, ordinals: &Value, keys: &Value, what: &str) -> Result<WindowIndex> {
    let ordinals = window(ordinals, what)?;
    let keys = items(keys, what)?;
    let keys = if packed
        .as_bool()
        .ok_or_else(|| WireError(format!("{what}: packed is not a bool")))?
    {
        WindowKeys::Words(
            keys.iter()
                .map(|key| {
                    key.as_u64()
                        .ok_or_else(|| WireError(format!("{what}: not a word: {key}")))
                })
                .collect::<Result<_>>()?,
        )
    } else {
        WindowKeys::Windows(
            keys.iter()
                .map(|key| {
                    let [length, values] = arguments(key, what)?;
                    let values = window(values, what)?;
                    if count(length, what)? != values.len() {
                        return error(format!("{what}: a key's length is not its window's"));
                    }
                    Ok(values.into_boxed_slice())
                })
                .collect::<Result<_>>()?,
        )
    };
    WindowIndex::new_checked(ordinals, keys, what)
}

impl WindowIndex {
    fn new_checked(ordinals: Vec<u32>, keys: WindowKeys, what: &str) -> Result<Self> {
        if ordinals.len() != keys.len() {
            return error(format!(
                "{what}: the ordinals and the keys differ in length"
            ));
        }
        Ok(Self { ordinals, keys })
    }
}

/// One call on a window index state and its answer.
fn window_call(index: &WindowIndex, call: &Value, what: &str) -> Result<Value> {
    let (name, arguments) = split_call(call, what)?;
    Ok(match (name, arguments) {
        ("candidates", [asked]) => listed(index.candidates(&window(asked, what)?)),
        ("key", [asked]) => key_value(index.key(&window(asked, what)?)),
        ("runs", []) => runs_value(index),
        ("nbytes", []) => index.nbytes().into(),
        ("remapped", [survivor, removed]) => {
            let survivor = window(survivor, what)?;
            let removed = bytes(removed, what)?;
            if index.ordinals.iter().any(|&ordinal| {
                ordinal as usize >= survivor.len() || ordinal as usize >= removed.len()
            }) {
                return error(format!("{what}: an ordinal past the survivor map"));
            }
            index_value(&index.remapped(&survivor, &removed))
        }
        _ => {
            return error(format!(
                "{what}: no window index call {name} of {} arguments",
                arguments.len()
            ));
        }
    })
}

/// A followed collection's calls, by their order in its trace: each line's key and call.
type Trace = Vec<(usize, String, Value)>;

/// Replay a string table's trace, answering each call with its result and `chars` after it.
fn replay_strings(
    trace: &[(usize, String, Value)],
    answers: &mut Vec<(String, Value)>,
) -> Result<()> {
    let mut table = StringTable::new();
    for (_, key, call) in trace {
        let result = match split_call(call, key)? {
            ("new", []) => Value::Null,
            ("id", [value]) => table.id(text(value, key)?).into(),
            ("find", [value]) => found(table.find(text(value, key)?)),
            ("optional", [value]) => {
                let value = if value.is_null() {
                    None
                } else {
                    Some(text(value, key)?)
                };
                table.optional(value).into()
            }
            ("getitem", [id]) => {
                let id = ordinal(id, key)?;
                if id as usize > table.len() {
                    return error(format!("{key}: no string {id}"));
                }
                table.get(id).into()
            }
            ("len", []) => table.len().into(),
            _ => return error(format!("{key}: no string table call {call:.200}")),
        };
        answers.push((key.clone(), tuple(vec![result, table.chars().into()])));
    }
    Ok(())
}

/// A tuple pool's members as the stage holds them: each element as its typed encoding's text, which equal Python values share.
fn members(value: &Value, what: &str) -> Result<Vec<String>> {
    items(value, what)?
        .iter()
        .map(|item| {
            serde_json::to_string(item).map_err(|failure| WireError(format!("{what}: {failure}")))
        })
        .collect()
}

fn members_value(members: &[String], what: &str) -> Result<Value> {
    Ok(tuple(
        members
            .iter()
            .map(|member| {
                serde_json::from_str(member)
                    .map_err(|failure| WireError(format!("{what}: {failure}")))
            })
            .collect::<Result<_>>()?,
    ))
}

/// Replay a tuple pool's trace, answering each call with its result, or its refusal, beside `elements` and `sealed` after it.
fn replay_tuples(
    trace: &[(usize, String, Value)],
    answers: &mut Vec<(String, Value)>,
) -> Result<()> {
    let mut pool: TuplePool<String> = TuplePool::new();
    for (_, key, call) in trace {
        let result = match split_call(call, key)? {
            ("new", []) => Value::Null,
            ("id", [value]) => match pool.id(&members(value, key)?) {
                Ok(id) => id.into(),
                Err(sealed) => raised("ValueError", sealed.to_string()),
            },
            ("pooled", [value]) => match pool.pooled(&members(value, key)?) {
                Ok(pooled) => members_value(pooled, key)?,
                Err(sealed) => raised("ValueError", sealed.to_string()),
            },
            ("getitem", [id]) => {
                let id = ordinal(id, key)?;
                if id as usize > pool.len() {
                    return error(format!("{key}: no tuple {id}"));
                }
                members_value(pool.get(id), key)?
            }
            ("len", []) => pool.len().into(),
            ("seal", []) => {
                pool.seal();
                Value::Null
            }
            _ => return error(format!("{key}: no tuple pool call {call:.200}")),
        };
        answers.push((
            key.clone(),
            tuple(vec![result, pool.elements().into(), pool.sealed().into()]),
        ));
    }
    Ok(())
}

/// A mapping's entries as strings, or None for one that holds a key or a value that is not a str, which a mapping pool refuses.
fn entries(value: &Value, what: &str) -> Result<Option<Vec<(String, String)>>> {
    let Some(entries) = value
        .as_object()
        .filter(|object| object.len() == 1)
        .and_then(|object| object.get("dict"))
        .and_then(Value::as_array)
    else {
        return error(format!("{what} is not a typed dict: {value:.200}"));
    };
    let mut held = Vec::with_capacity(entries.len());
    for entry in entries {
        let [name, member] = entry
            .as_array()
            .and_then(|pair| <&[Value; 2]>::try_from(pair.as_slice()).ok())
            .ok_or_else(|| WireError(format!("{what} is not a typed dict: {value:.200}")))?;
        match (name.as_str(), member.as_str()) {
            (Some(name), Some(member)) => held.push((name.to_owned(), member.to_owned())),
            _ => return Ok(None),
        }
    }
    Ok(Some(held))
}

fn mapping_value(pairs: &[(String, String)]) -> Value {
    json!({ "dict": pairs.iter().map(|(name, member)| json!([name, member])).collect::<Vec<_>>() })
}

/// Replay a mapping pool's trace, answering each call with its result, or its refusal, beside `elements` after it. A mapping with a key or a value that is not a str is refused unless it is empty, which every pool holds as id 0 before it looks at a type.
fn replay_mappings(
    trace: &[(usize, String, Value)],
    answers: &mut Vec<(String, Value)>,
) -> Result<()> {
    let mut pool: MappingPool<String> = MappingPool::new();
    let refused = || {
        raised(
            "TypeError",
            "a mapping pool holds string keys and string values".into(),
        )
    };
    for (_, key, call) in trace {
        let result = match split_call(call, key)? {
            ("new", []) => Value::Null,
            ("id", [value]) => match entries(value, key)? {
                Some(pairs) => pool.id(&pairs).into(),
                None => refused(),
            },
            ("pooled", [value]) => match entries(value, key)? {
                Some(pairs) => mapping_value(pool.pooled(&pairs)),
                None => refused(),
            },
            ("getitem", [id]) => {
                let id = ordinal(id, key)?;
                if id as usize > pool.len() {
                    return error(format!("{key}: no mapping {id}"));
                }
                mapping_value(pool.get(id))
            }
            ("len", []) => pool.len().into(),
            _ => return error(format!("{key}: no mapping pool call {call:.200}")),
        };
        answers.push((key.clone(), tuple(vec![result, pool.elements().into()])));
    }
    Ok(())
}

/// Replay a key index's trace: its making over the column bytes it was given, answered `("built", nbytes)` or its refusal, then each find and size.
fn replay_keys(trace: &[(usize, String, Value)], answers: &mut Vec<(String, Value)>) -> Result<()> {
    let ((_, made_key, made), rest) = trace.split_first().expect("a trace holds its making");
    let [_, column, width, held] =
        <&[Value; 4]>::try_from(items(made, made_key)?).map_err(|_| {
            WireError(format!(
                "{made_key}: a key index is made of a column, a width and a count"
            ))
        })?;
    let column = bytes(column, made_key)?;
    let index = match KeyIndex::new(&column, count(width, made_key)?, count(held, made_key)?) {
        Ok(index) => {
            answers.push((
                made_key.clone(),
                tuple(vec![json!("built"), index.nbytes().into()]),
            ));
            index
        }
        Err(KeyIndexError::Repeated(RepeatedKey { first, second })) => {
            answers.push((
                made_key.clone(),
                tuple(vec![
                    json!("raised"),
                    json!("RepeatedKey"),
                    first.into(),
                    second.into(),
                ]),
            ));
            return match rest.first() {
                Some((_, key, _)) => {
                    error(format!("{key}: a call on a key index that was refused"))
                }
                None => Ok(()),
            };
        }
        Err(shape) => {
            answers.push((made_key.clone(), raised("ValueError", shape.to_string())));
            return match rest.first() {
                Some((_, key, _)) => {
                    error(format!("{key}: a call on a key index that was refused"))
                }
                None => Ok(()),
            };
        }
    };
    for (_, key, call) in rest {
        let result = match split_call(call, key)? {
            ("find", [asked]) => found(index.find(&column, &bytes(asked, key)?)),
            ("nbytes", []) => index.nbytes().into(),
            _ => return error(format!("{key}: no key index call {call:.200}")),
        };
        answers.push((key.clone(), result));
    }
    Ok(())
}

/// Replay each followed collection's trace in its calls' order, which must run from its making with no call missing.
fn replay(
    form: &str,
    name: &str,
    mut trace: Trace,
    answers: &mut Vec<(String, Value)>,
) -> Result<()> {
    trace.sort_by_key(|(index, _, _)| *index);
    if trace
        .iter()
        .enumerate()
        .any(|(position, (index, _, _))| position != *index)
    {
        return error(format!(
            "{name}: its calls are not numbered from 0 without a gap"
        ));
    }
    let Some((_, key, made)) = trace.first() else {
        return error(format!("{name}: no calls"));
    };
    if split_call(made, key)?.0 != "new" {
        return error(format!("{key}: {name} does not start from its making"));
    }
    for (_, key, call) in &trace[1..] {
        if split_call(call, key)?.0 == "new" {
            return error(format!("{key}: {name} is made twice"));
        }
    }
    match form {
        "string-table" => replay_strings(&trace, answers),
        "tuple-pool" => replay_tuples(&trace, answers),
        "mapping-pool" => replay_mappings(&trace, answers),
        _ => replay_keys(&trace, answers),
    }
}

/// The `columns` stage: answer each line of `stage_file` by its `form`, in one `{"key", "value"}` line each in the typed encoding. A followed collection's lines (`string-table`, `tuple-pool`, `mapping-pool`, `key-index`), whose `input` is the collection's name, the call's place in its trace and the call, are replayed per collection from its making in their order ([`StringTable`], [`TuplePool`] over each element's typed text, [`MappingPool`] over str pairs, [`KeyIndex`] over the column its making holds), each answered with what the call gave and the collection's size after it; a `window-index-state` line holds a window index's ordinals and keys, answered with `packed`, `windows`, `runs` and `nbytes`, and a `window-index` line names a state and a call on it ([`WindowIndex`]); `window-index-build`, `window-word` and `sorted-by-word` lines are answered alone ([`WindowIndex::build`], [`window_word`], [`sorted_by_word`]).
pub fn stage_columns(stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let read = |failure: std::io::Error| {
        WireError(format!("cannot read {}: {failure}", stage_file.display()))
    };
    let mut records = Vec::new();
    for line in BufReader::new(File::open(stage_file).map_err(read)?).lines() {
        let line = line.map_err(read)?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| WireError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str().map(str::to_owned) else {
            return error(format!("a stage line has no key: {line:.200}"));
        };
        records.push((key, record));
    }
    let mut states: HashMap<String, WindowIndex> = HashMap::new();
    for (key, record) in &records {
        if record["form"] == "window-index-state" {
            let [digest, packed, ordinals, keys] = arguments(&record["input"], key)?;
            states.insert(
                text(digest, key)?.to_owned(),
                window_index(packed, ordinals, keys, key)?,
            );
        }
    }
    let mut followed: BTreeMap<(String, String), Trace> = BTreeMap::new();
    let mut answers: Vec<(String, Value)> = Vec::new();
    for (key, record) in records {
        let input = &record["input"];
        let form = record["form"].as_str().unwrap_or_default();
        let value = match form {
            "string-table" | "tuple-pool" | "mapping-pool" | "key-index" => {
                let [name, index, call] = arguments(input, &key)?;
                followed
                    .entry((form.to_owned(), text(name, &key)?.to_owned()))
                    .or_default()
                    .push((count(index, &key)?, key, call.clone()));
                continue;
            }
            "window-index-state" => {
                let index = &states[text(&arguments::<4>(input, &key)?[0], &key)?];
                tuple(vec![
                    index.packed().into(),
                    index.windows().into(),
                    runs_value(index),
                    index.nbytes().into(),
                ])
            }
            "window-index" => {
                let [state, call] = arguments(input, &key)?;
                let Some(index) = states.get(text(state, &key)?) else {
                    return error(format!("{key}: no window index state {state}"));
                };
                window_call(index, call, &key)?
            }
            "window-index-build" => {
                let pairs = items(input, &key)?
                    .iter()
                    .map(|pair| {
                        let [held, values] = arguments(pair, &key)?;
                        Ok((ordinal(held, &key)?, window(values, &key)?))
                    })
                    .collect::<Result<Vec<_>>>()?;
                index_value(&WindowIndex::build(pairs))
            }
            "window-word" => window_word(&window(input, &key)?).map_or(Value::Null, Value::from),
            "sorted-by-word" => {
                let words = items(input, &key)?
                    .iter()
                    .map(|word| {
                        word.as_u64()
                            .ok_or_else(|| WireError(format!("{key}: not a 64-bit word: {word}")))
                    })
                    .collect::<Result<Vec<u64>>>()?;
                listed(&sorted_by_word(&words))
            }
            other => return error(format!("{key}: no columns form {other:?}")),
        };
        answers.push((key, value));
    }
    for ((form, name), trace) in followed {
        replay(&form, &name, trace, &mut answers)?;
    }
    for (key, value) in answers {
        writeln!(out, "{}", json!({ "key": key, "value": value }))
            .map_err(|failure| WireError(format!("cannot write a stage line: {failure}")))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_string_table_assigns_ids_in_first_seen_order_from_the_empty_string() {
        let mut table = StringTable::new();
        assert_eq!(
            (table.len(), table.chars(), table.find("")),
            (0, 0, Some(0))
        );
        assert_eq!(
            ["a", "é·Day", "a", "", "b"].map(|value| table.id(value)),
            [1, 2, 1, 0, 3]
        );
        assert_eq!((table.optional(None), table.optional(Some("b"))), (0, 3));
        assert_eq!(
            (table.find("c"), table.get(2), table.len()),
            (None, "é·Day", 3)
        );
        assert_eq!(table.chars(), 1 + 7 + 1);
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
        assert_eq!(
            (pool.len(), pool.elements(), pool.get(1)),
            (2, 3, &[1, 2][..])
        );
        pool.seal();
        assert_eq!(
            (pool.id(&[1]), pool.pooled(&[]).err(), pool.get(2)),
            (Err(Sealed), Some(Sealed), &[1][..])
        );
        assert_eq!(
            Sealed.to_string(),
            "the tuple pool is sealed: its columns are written and it assigns no further id"
        );
    }

    #[test]
    fn a_mapping_pool_keeps_insertion_order_and_counts_two_ids_an_entry() {
        let mut pool: MappingPool<u32> = MappingPool::new();
        assert_eq!(pool.id(&[(1, 2), (3, 4)]), 1);
        assert_eq!(pool.id(&[(3, 4), (1, 2)]), 2);
        assert_eq!((pool.id(&[]), pool.id(&[(1, 2), (3, 4)])), (0, 1));
        assert_eq!((pool.len(), pool.elements()), (2, 8));
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
        assert_eq!(
            (
                index.runs().collect::<Vec<_>>(),
                index.windows(),
                index.nbytes()
            ),
            (vec![(1, 3)], 3, 48)
        );
        let remapped = index.remapped(&[0, 0, 1, 2, 3], &[1, 0, 0, 0, 0]);
        assert_eq!(remapped.ordinals, [2, 3, 0]);
        assert_eq!(remapped.keys, WindowKeys::Words(vec![1, 2, 0x1_0001]));
        let unpacked = WindowIndex::build([(0, vec![1, 2, 3, 4, 5]), (1, vec![9]), (2, vec![])]);
        assert!(!unpacked.packed());
        assert_eq!(unpacked.ordinals, [2, 1, 0]);
        assert_eq!(
            (unpacked.candidates(&[]), unpacked.nbytes()),
            (&[2][..], 12)
        );
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
            (
                index.find(&column, &[0; 16]),
                index.find(&column, &[5; 8]),
                index.nbytes()
            ),
            (None, None, 256)
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
        assert_eq!(KeyIndex::new(&[], 32, 0).map(|index| index.nbytes()), Ok(8));
    }
}
