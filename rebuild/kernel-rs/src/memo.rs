//! A finished trace memo detached from the engine that filled it, and the rule under which another engine may read it. An entry records what one engine settled for one window (a collapsed left, an input and four raw slots), with the pointers that evaluation fired and the runes and classes it read. By the window locality rule (`doc/rebuild-design.md` §10), that result depends only on the crate, the script registry and the rune files the key names. So one enumeration's memo can answer another enumeration's windows wherever the two agree on every rune a key names. Applied across configurations, the same rule means a configuration can differ from `default` only on the windows naming a rune with an unlock, a `feature:`-conditioned record, or an unlock gate under that configuration.
//!
//! This is how the per-configuration delta enumeration works. `default` enumerates first and returns its memo as a [`MemoSnapshot`]. Every other configuration's engine takes that snapshot as a [`SharedMemo`] whose [`Exclusion`] names the configuration's unlocking runes ([`unlocking_runes`]), runs the same worklist from the same seeds in the same order, and reads from the shared memo every window whose key names no unlocking rune and whose evaluation read none. The worklist is not seeded: each traversal re-derives reachability, because the memo records what a window settles to, never whether the window exists. So a cell that another configuration reaches first, or reaches only there, is still found. The fired journal carries over as it does for a hit on the engine's own memo: a shared memo entry stores the delta its evaluation journaled and a hit replays it, so a delta configuration's `cited_provenance` is the union over the windows it visited, as in a from-scratch enumeration.
//!
//! Which windows a shared memo may answer is decided by what each entry's evaluation read. While a capture is open, the index journals every rune whose resolved content and every predicate class whose membership its accessors return ([`crate::index`]), and the entry stores that set beside its fired delta. An [`Exclusion`] naming runes or classes therefore rejects the entries that read one of them. A delta's exclusion names its unlocking runes, so a window whose deep slots name an unlocking rune its evaluation never consulted is still read from `default`'s memo. The six runes a key names are also rejected, as a redundant check on the journal: over-invalidation costs a trace, and under-invalidation costs a wrong table.
//!
//! The snapshot is shared behind an [`Arc`] instead of copied per configuration, because the memo is the largest structure the enumeration holds, and a copy per delta configuration would again limit by memory how many delta configurations run at once. So it holds no `Rc`, no reference into any engine, and no ranking (the fixpoint never records one), and a shared memo is read-only once built. Its records are packed in sorted blocks ([`SnapshotEntries`]), and each engine that reads it keeps its own cache of decoded blocks ([`StoreReader`]), since several threads read one snapshot at once.

use std::hash::{Hash, Hasher};
use std::num::NonZeroU16;
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::engine::{
    DeltaId, Pointer, ReadsId, TraceEntry, TraceKey, TraceNotesId, TraceSettledId, VerdictEntry,
    VerdictKey,
};
use crate::hash::{FastHasher, HashMap, HashSet, ShardedMap};
use crate::index::{Read, SpecIndex};
use crate::model::{PolicyRecord, Sym, When};
use crate::types::{AdjustmentToken, PackedKinds, Settled, TransitionTrace};

/// How many decoded blocks a reading engine's cache holds for each shared memo unless the caller sets another: 455 blocks of [`SnapshotPacking::BLOCK_RECORDS`] thirty-six-byte records, 8.4 MB. `build-tables` takes `--snapshot-cache-blocks=` to set another, zero for no cache. The tables are the same bytes at any size.
pub const SNAPSHOT_CACHE_BLOCKS: usize = 455;

/// How a snapshot's records are packed at the release ([`SnapshotEntries`]): [`SnapshotPacking::BLOCK_RECORDS`] records a block, a whole record every `restart_records` of them inside a block, and whether a filter is built over the keys. The default is a block's first record alone whole, with the filter. `build-tables` takes `--snapshot-restart-records=` and `--no-snapshot-filter` to set others. The tables are the same bytes under any packing.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SnapshotPacking {
    block_records: usize,
    restart_records: usize,
    filter: bool,
    chunk_bytes: usize,
}

impl SnapshotPacking {
    /// How many records a block holds.
    pub const BLOCK_RECORDS: usize = 512;
    /// The size of the chunks the coded blocks are held in, 16 MiB. macOS's allocator keeps the pages of a freed allocation of 2 MiB or less resident, out of the footprint, until the kernel reclaims them, even with `MallocLargeCache=0` (`kernel_exec.KERNEL_ALLOCATOR_ENVIRONMENT`), so smaller chunks would stay in the process's resident size after the snapshot drops, through the rendezvous; a chunk above that size goes back to the system when it is freed.
    const CHUNK_BYTES: usize = 1 << 24;

    /// A packing with a whole record every `restart_records` inside a block, and a filter when `filter` is set, or `None` when `restart_records` does not divide [`SnapshotPacking::BLOCK_RECORDS`].
    pub fn new(restart_records: usize, filter: bool) -> Option<Self> {
        (restart_records > 0 && Self::BLOCK_RECORDS % restart_records == 0).then_some(Self {
            block_records: Self::BLOCK_RECORDS,
            restart_records,
            filter,
            chunk_bytes: Self::CHUNK_BYTES,
        })
    }

    /// A packing of blocks of `block_records` held in chunks of `chunk_bytes`, for a test whose snapshot holds too few records to fill several blocks of the shipped size.
    #[cfg(test)]
    pub(crate) fn for_test(
        block_records: usize,
        restart_records: usize,
        filter: bool,
        chunk_bytes: usize,
    ) -> Self {
        assert!(
            block_records <= Self::BLOCK_RECORDS && block_records.is_multiple_of(restart_records)
        );
        Self {
            block_records,
            restart_records,
            filter,
            chunk_bytes,
        }
    }
}

impl Default for SnapshotPacking {
    fn default() -> Self {
        Self::new(Self::BLOCK_RECORDS, true).expect("a block's length divides itself")
    }
}

/// `default`'s finished trace memo as the release packs it ([`SnapshotEntries::release`]): every record sorted by [`TraceKey`]'s derived order and coded in blocks, a fence a block, and a negative filter over the keys.
///
/// A block's first record is whole: the key's nine byte fields in field order (each optional ordinal as its value or zero, the extension as a signed byte), its kinds word, the entry's settled, notes, delta and reads ids as varints, and its packed byte. Each later record is a header byte, whose low four bits count the leading byte fields its key shares with the previous key and whose high four bits flag which of the four ids equal the previous entry's, then the byte fields past the shared ones, the kinds word, the ids not flagged as varints, and the packed byte. A packing with restart points also codes every `restart_records`-th record of a block whole and starts the block with their two-byte offsets. Blocks are held in chunks of [`SnapshotPacking::CHUNK_BYTES`], none straddling two, so the release never holds a doubling buffer beside the records it drains.
///
/// A fence is twenty-four bytes: its block's first key and where the block starts. A lookup tests the filter, which answers most absent keys with no decode; binary-searches the fences for the one block that could hold the key; and finds the key in that block as the reader's cache holds it decoded, or, with no cache, decodes the block in place from the nearest whole record at or before the key. The filter is a blocked Bloom filter of ten bits a key, 512-bit blocks and seven probes keyed on [`SnapshotEntries::hash`], `rebuild/pipeline/row_delta.py`'s shape. The hash never stands in for equality: a key the filter admits is answered only by comparing whole keys.
#[derive(Debug, Default)]
pub(crate) struct SnapshotEntries {
    records: usize,
    block_records: usize,
    restart_records: usize,
    fences: Box<[Fence]>,
    chunks: Vec<Vec<u8>>,
    filter: Box<[FilterBlock]>,
    /// How long the release's sort and its encoding took, for the cache stats.
    sort: Duration,
    encode: Duration,
}

/// One block's fence: its first key, and where its bytes start, as the chunk's index above [`Fence::CHUNK_SHIFT`] bits and the offset within the chunk below them.
#[derive(Clone, Copy, Debug)]
struct Fence {
    first: TraceKey,
    at: u32,
}

const _: () = assert!(std::mem::size_of::<Fence>() == 24);

impl Fence {
    const CHUNK_SHIFT: u32 = SnapshotPacking::CHUNK_BYTES.trailing_zeros();
    const OFFSET_MASK: u32 = (1 << Self::CHUNK_SHIFT) - 1;
    /// How many chunks a fence can name, 4 GiB of blocks in chunks of [`SnapshotPacking::CHUNK_BYTES`].
    const CHUNKS: usize = 1 << (32 - Self::CHUNK_SHIFT);
}

const _: () = assert!(SnapshotPacking::CHUNK_BYTES.is_power_of_two());

/// One 512-bit block of the filter.
type FilterBlock = [u64; 8];

/// How many byte fields a key codes before its kinds word.
const KEY_BYTES: usize = 9;

/// The bits of a later record's header that count its shared byte fields; the four bits above them flag its ids.
const SHARED_MASK: u8 = 0x0f;

/// The filter's bits a key, its probes a key, the bits of the mixed hash each probe reads, and the odd constant the hash is mixed by, `row_delta.py`'s `BLOOM_*` values.
const FILTER_BITS_PER_KEY: usize = 10;
const FILTER_PROBES: u32 = 7;
const FILTER_FIELD: u32 = 9;
const FILTER_REMIX: u64 = 0xC2B2_AE3D_27D4_EB4F;

impl SnapshotEntries {
    /// The key's hash for the filter: its own packed `Hash` ([`TraceKey`]'s, the one the engine's live trace memo uses) folded through [`FastHasher`].
    fn hash(key: &TraceKey) -> u64 {
        let mut hash = FastHasher::default();
        key.hash(&mut hash);
        hash.finish()
    }

    /// The live memo's records drained one shard at a time, each shard's table freed once its entries have moved, then packed ([`SnapshotEntries::pack`]).
    pub(crate) fn release(
        entries: ShardedMap<TraceKey, TraceEntry>,
        packing: SnapshotPacking,
    ) -> Result<Self, String> {
        let mut records = Vec::with_capacity(entries.len());
        for shard in entries.into_shards() {
            records.extend(shard);
        }
        Self::pack(records, packing)
    }

    /// Sorts `records` in place with an unstable sort, since a live memo holds no duplicate key and a stable sort would allocate half the records again, codes them block by block into chunks beside the fences and the filter, and drops them. A key equal to the one before it after the sort is refused, naming the key, as is a key field that does not fit its byte, naming the field.
    pub(crate) fn pack(
        mut records: Vec<(TraceKey, TraceEntry)>,
        packing: SnapshotPacking,
    ) -> Result<Self, String> {
        let started = Instant::now();
        records.sort_unstable_by_key(|(key, _)| *key);
        let sort = started.elapsed();
        let started = Instant::now();
        let mut filter: Vec<FilterBlock> = if packing.filter {
            vec![[0; 8]; (records.len() * FILTER_BITS_PER_KEY).div_ceil(512)]
        } else {
            Vec::new()
        };
        let mut fences: Vec<Fence> =
            Vec::with_capacity(records.len().div_ceil(packing.block_records));
        let mut chunks: Vec<Vec<u8>> = Vec::new();
        let mut block: Vec<u8> = Vec::new();
        let mut last: Option<&TraceKey> = None;
        for held in records.chunks(packing.block_records) {
            block.clear();
            block.resize(2 * ((held.len() - 1) / packing.restart_records), 0);
            let mut previous: Option<([u8; KEY_BYTES], [u32; 4])> = None;
            for (at, (key, entry)) in held.iter().enumerate() {
                if last == Some(key) {
                    return Err(format!(
                        "the trace memo holds {key:?} twice, which a live memo never does"
                    ));
                }
                last = Some(key);
                if !filter.is_empty() {
                    let hash = Self::hash(key);
                    let blocks = filter.len();
                    for (word, bits) in filter[filter_block(hash, blocks)]
                        .iter_mut()
                        .zip(filter_mask(hash))
                    {
                        *word |= bits;
                    }
                }
                let fields = key_bytes(key)?;
                let ids = entry_ids(entry);
                if at % packing.restart_records == 0 {
                    if at > 0 {
                        let offset = u16::try_from(block.len())
                            .expect("a block of 512 records codes in fewer than 65,536 bytes");
                        let slot = 2 * (at / packing.restart_records - 1);
                        block[slot..slot + 2].copy_from_slice(&offset.to_le_bytes());
                    }
                    block.extend_from_slice(&fields);
                    block.extend_from_slice(&key.kinds.word().to_le_bytes());
                    for id in ids {
                        put_varint(&mut block, id);
                    }
                } else {
                    let (before, before_ids) = previous.expect("a later record follows another");
                    let shared = fields
                        .iter()
                        .zip(&before)
                        .take_while(|(field, was)| field == was)
                        .count();
                    let mut header = shared as u8;
                    for (slot, (id, was)) in ids.iter().zip(before_ids).enumerate() {
                        if *id == was {
                            header |= 0x10 << slot;
                        }
                    }
                    block.push(header);
                    block.extend_from_slice(&fields[shared..]);
                    block.extend_from_slice(&key.kinds.word().to_le_bytes());
                    for (slot, id) in ids.into_iter().enumerate() {
                        if header & (0x10 << slot) == 0 {
                            put_varint(&mut block, id);
                        }
                    }
                }
                block.push(entry.packed);
                previous = Some((fields, ids));
            }
            let room = chunks
                .last()
                .map_or(0, |chunk| chunk.capacity() - chunk.len());
            if room < block.len() {
                if chunks.len() == Fence::CHUNKS {
                    return Err(format!(
                        "the trace memo's packed blocks outgrow the {} chunks a fence names",
                        Fence::CHUNKS
                    ));
                }
                chunks.push(Vec::with_capacity(packing.chunk_bytes.max(block.len())));
            }
            let index = chunks.len() as u32 - 1;
            let chunk = chunks.last_mut().expect("a chunk with room was pushed");
            let at = (index << Fence::CHUNK_SHIFT) | chunk.len() as u32;
            chunk.extend_from_slice(&block);
            fences.push(Fence {
                first: held[0].0,
                at,
            });
        }
        let encode = started.elapsed();
        let count = records.len();
        drop(records);
        if let Some(chunk) = chunks.last_mut() {
            chunk.shrink_to_fit();
        }
        Ok(Self {
            records: count,
            block_records: packing.block_records,
            restart_records: packing.restart_records,
            fences: fences.into_boxed_slice(),
            chunks,
            filter: filter.into_boxed_slice(),
            sort,
            encode,
        })
    }

    pub(crate) fn len(&self) -> usize {
        self.records
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.records == 0
    }

    /// How many records a block holds, a reader's cache sizing its slots by it.
    pub(crate) fn block_records(&self) -> usize {
        self.block_records
    }

    /// The entry recorded under `key`, by value, or `None` when the store holds no such key, with `reader`'s cache holding the block it decoded and its counts recording how the get was answered.
    pub(crate) fn get(&self, key: &TraceKey, reader: &mut StoreReader) -> Option<TraceEntry> {
        reader.gets += 1;
        if self.is_empty() {
            return None;
        }
        if !self.filter.is_empty() {
            let hash = Self::hash(key);
            let block = &self.filter[filter_block(hash, self.filter.len())];
            if block
                .iter()
                .zip(filter_mask(hash))
                .any(|(word, bits)| word & bits != bits)
            {
                reader.filtered += 1;
                return None;
            }
        }
        let block = self
            .fences
            .partition_point(|fence| fence.first <= *key)
            .checked_sub(1)?;
        if reader.capacity == 0 {
            reader.decoded += 1;
            return self.find_in_place(block, key);
        }
        let records = reader.decoded_block(block, self);
        records
            .binary_search_by(|(held, _)| held.cmp(key))
            .ok()
            .map(|at| records[at].1)
    }

    /// How many records block `block` holds: [`SnapshotEntries::block_records`] but in the last block.
    fn block_len(&self, block: usize) -> usize {
        if block + 1 < self.fences.len() {
            self.block_records
        } else {
            self.records - block * self.block_records
        }
    }

    /// Block `block`'s bytes, from its restart offsets on to the end of its chunk.
    fn block_bytes(&self, block: usize) -> &[u8] {
        let at = self.fences[block].at;
        &self.chunks[(at >> Fence::CHUNK_SHIFT) as usize][(at & Fence::OFFSET_MASK) as usize..]
    }

    /// Every record of block `block`, decoded in order and handed to `put`.
    fn decode_block(&self, block: usize, mut put: impl FnMut((TraceKey, TraceEntry))) {
        let bytes = self.block_bytes(block);
        let len = self.block_len(block);
        let mut cursor = Cursor::at(bytes, 2 * ((len - 1) / self.restart_records));
        for at in 0..len {
            if at % self.restart_records == 0 {
                cursor.whole();
            } else {
                cursor.next();
            }
            put((cursor.key(), cursor.entry()));
        }
    }

    /// The entry under `key` in block `block`, decoded in place with no cache: the restart points are binary-searched on their whole records' keys, and the records from the last one at or before `key` are decoded until one reaches it.
    fn find_in_place(&self, block: usize, key: &TraceKey) -> Option<TraceEntry> {
        let bytes = self.block_bytes(block);
        let len = self.block_len(block);
        let restarts = (len - 1) / self.restart_records;
        let start = |restart: usize| {
            if restart == 0 {
                2 * restarts
            } else {
                usize::from(u16::from_le_bytes([
                    bytes[2 * restart - 2],
                    bytes[2 * restart - 1],
                ]))
            }
        };
        let (mut low, mut high) = (0, restarts);
        while low < high {
            let middle = (low + high).div_ceil(2);
            let mut cursor = Cursor::at(bytes, start(middle));
            cursor.whole();
            if cursor.key() <= *key {
                low = middle;
            } else {
                high = middle - 1;
            }
        }
        let mut cursor = Cursor::at(bytes, start(low));
        cursor.whole();
        let end = ((low + 1) * self.restart_records).min(len);
        let mut at = low * self.restart_records;
        loop {
            match cursor.key().cmp(key) {
                std::cmp::Ordering::Equal => return Some(cursor.entry()),
                std::cmp::Ordering::Greater => return None,
                std::cmp::Ordering::Less => {}
            }
            at += 1;
            if at == end {
                return None;
            }
            cursor.next();
        }
    }

    /// The bytes the chunks of coded blocks hold as allocated.
    fn packed_bytes(&self) -> usize {
        self.chunks.iter().map(Vec::capacity).sum::<usize>()
            + self.chunks.capacity() * size_of::<Vec<u8>>()
    }

    /// Every record, decoded block by block in key order.
    #[cfg(test)]
    pub(crate) fn records(&self) -> Vec<(TraceKey, TraceEntry)> {
        let mut all = Vec::with_capacity(self.records);
        for at in 0..self.fences.len() {
            self.decode_block(at, |record| all.push(record));
        }
        all
    }

    /// The entry under `key`, read with no cache.
    #[cfg(test)]
    pub(crate) fn lookup(&self, key: &TraceKey) -> Option<TraceEntry> {
        self.get(key, &mut StoreReader::new(0, self.block_records))
    }

    /// How many blocks the store codes.
    #[cfg(test)]
    pub(crate) fn blocks(&self) -> usize {
        self.fences.len()
    }
}

/// The block of the filter a 64-bit hash falls in, from its top 32 bits scaled onto `blocks`.
fn filter_block(hash: u64, blocks: usize) -> usize {
    (((hash >> 32) * blocks as u64) >> 32) as usize
}

/// The [`FILTER_PROBES`] bits a 64-bit hash sets within its block: nine-bit fields of the hash multiplied by an odd constant, so the bits do not follow the top bits that chose the block.
fn filter_mask(hash: u64) -> FilterBlock {
    let mixed = hash.wrapping_mul(FILTER_REMIX);
    let mut mask = [0; 8];
    for probe in 0..FILTER_PROBES {
        let bit = (mixed >> (FILTER_FIELD * probe)) & 511;
        mask[(bit / 64) as usize] |= 1 << (bit % 64);
    }
    mask
}

/// A key's nine byte fields in field order: each optional ordinal as its value or zero, which no ordinal takes, and the extension as a signed byte. A field that does not fit its byte is refused, naming it.
fn key_bytes(key: &TraceKey) -> Result<[u8; KEY_BYTES], String> {
    let ordinal = |field: &str, ordinal: Option<NonZeroU16>| {
        let value = ordinal.map_or(0, NonZeroU16::get);
        u8::try_from(value).map_err(|_| {
            format!("a trace memo key's {field} ordinal, {value}, does not fit the snapshot's byte")
        })
    };
    let extension = i8::try_from(key.left_extension).map_err(|_| {
        format!(
            "a trace memo key's left extension, {}, does not fit the snapshot's signed byte",
            key.left_extension
        )
    })?;
    Ok([
        ordinal("left rune", key.left_rune)?,
        ordinal("left stance", key.left_stance)?,
        ordinal("left junction", key.left_junction)?,
        extension as u8,
        ordinal("input rune", Some(key.token))?,
        ordinal("first slot's rune", key.runes[0])?,
        ordinal("second slot's rune", key.runes[1])?,
        ordinal("third slot's rune", key.runes[2])?,
        ordinal("fourth slot's rune", key.runes[3])?,
    ])
}

/// An entry's settled, notes, delta and reads ids as the indexes they name.
fn entry_ids(entry: &TraceEntry) -> [u32; 4] {
    [
        entry.settled.index() as u32,
        entry.notes.index() as u32,
        entry.delta.index() as u32,
        entry.reads.index() as u32,
    ]
}

/// `value` as a little-endian base-128 varint: seven bits a byte, the high bit set on every byte but the last.
fn put_varint(out: &mut Vec<u8>, mut value: u32) {
    while value >= 0x80 {
        out.push((value as u8) | 0x80);
        value >>= 7;
    }
    out.push(value as u8);
}

/// Reads one block's records in order, holding the last record decoded, whose key fields and ids the next record codes against.
struct Cursor<'a> {
    bytes: &'a [u8],
    at: usize,
    fields: [u8; KEY_BYTES],
    kinds: u16,
    ids: [u32; 4],
    packed: u8,
}

impl<'a> Cursor<'a> {
    /// A cursor at `at` in `bytes`, before a whole record.
    fn at(bytes: &'a [u8], at: usize) -> Self {
        Self {
            bytes,
            at,
            fields: [0; KEY_BYTES],
            kinds: 0,
            ids: [0; 4],
            packed: 0,
        }
    }

    fn byte(&mut self) -> u8 {
        let byte = self.bytes[self.at];
        self.at += 1;
        byte
    }

    fn varint(&mut self) -> u32 {
        let mut value = 0u32;
        let mut shift = 0;
        loop {
            let byte = self.byte();
            value |= u32::from(byte & 0x7f) << shift;
            if byte & 0x80 == 0 {
                return value;
            }
            shift += 7;
        }
    }

    fn kinds_and_rest(&mut self, header: u8) {
        self.kinds = u16::from_le_bytes([self.byte(), self.byte()]);
        for slot in 0..4 {
            if header & (0x10 << slot) == 0 {
                self.ids[slot] = self.varint();
            }
        }
        self.packed = self.byte();
    }

    /// Decode a whole record.
    fn whole(&mut self) {
        for field in 0..KEY_BYTES {
            self.fields[field] = self.byte();
        }
        self.kinds_and_rest(0);
    }

    /// Decode a later record against the one before it.
    fn next(&mut self) {
        let header = self.byte();
        for field in usize::from(header & SHARED_MASK)..KEY_BYTES {
            self.fields[field] = self.byte();
        }
        self.kinds_and_rest(header);
    }

    fn key(&self) -> TraceKey {
        let ordinal = |byte: u8| NonZeroU16::new(u16::from(byte));
        let fields = self.fields;
        TraceKey {
            left_rune: ordinal(fields[0]),
            left_stance: ordinal(fields[1]),
            left_junction: ordinal(fields[2]),
            left_extension: i16::from(fields[3] as i8),
            token: ordinal(fields[4]).expect("a key's input names a rune"),
            runes: [
                ordinal(fields[5]),
                ordinal(fields[6]),
                ordinal(fields[7]),
                ordinal(fields[8]),
            ],
            kinds: PackedKinds::from_word(self.kinds),
        }
    }

    fn entry(&self) -> TraceEntry {
        TraceEntry {
            settled: TraceSettledId::at(self.ids[0] as usize),
            notes: TraceNotesId::at(self.ids[1] as usize),
            delta: DeltaId::at(self.ids[2] as usize),
            reads: ReadsId::at(self.ids[3] as usize),
            packed: self.packed,
        }
    }
}

/// One reading engine's cache of one shared memo's decoded blocks, and the counts of what its gets did, for the cache stats. An engine runs on one thread, so this is the per-thread cache the snapshot's readers keep beside it. It holds up to `capacity` blocks, each decoded whole into thirty-six-byte records, and evicts by the clock: a get its cache answers marks the block, and a decode that finds the cache full takes the first unmarked block from the hand on, unmarking the marked blocks it passes. Nothing an output reads depends on which block it evicts. Its slots share one allocation, reserved whole when the reader is made and filled as blocks are decoded, so that at the default size it goes back to the system when the reader drops, which a block of its own would not ([`SnapshotPacking::CHUNK_BYTES`] says why).
#[derive(Debug, Default)]
pub(crate) struct StoreReader {
    capacity: usize,
    block_records: usize,
    /// Every slot's records, slot `s` from `s * block_records` on, a block shorter than that padded to it with copies of its last record.
    records: Vec<(TraceKey, TraceEntry)>,
    slots: Vec<CachedBlock>,
    places: HashMap<usize, usize>,
    hand: usize,
    gets: u64,
    filtered: u64,
    cached: u64,
    decoded: u64,
}

/// One block a reader's cache holds decoded, how many records it has, and whether a get has read it since the clock last passed it.
#[derive(Debug)]
struct CachedBlock {
    block: usize,
    len: usize,
    marked: bool,
}

impl StoreReader {
    /// An empty cache of `capacity` blocks of `block_records` records.
    pub(crate) fn new(capacity: usize, block_records: usize) -> Self {
        Self {
            capacity,
            block_records,
            records: Vec::with_capacity(capacity * block_records),
            ..Self::default()
        }
    }

    /// Block `block`'s records, decoded, from the cache or into it.
    fn decoded_block(
        &mut self,
        block: usize,
        entries: &SnapshotEntries,
    ) -> &[(TraceKey, TraceEntry)] {
        let slot = if let Some(&slot) = self.places.get(&block) {
            self.cached += 1;
            self.slots[slot].marked = true;
            slot
        } else {
            self.decoded += 1;
            let slot = if self.slots.len() < self.capacity {
                self.slots.push(CachedBlock {
                    block,
                    len: 0,
                    marked: false,
                });
                self.slots.len() - 1
            } else {
                let slot = loop {
                    let slot = self.hand;
                    self.hand = (self.hand + 1) % self.capacity;
                    if !std::mem::take(&mut self.slots[slot].marked) {
                        break slot;
                    }
                };
                self.places.remove(&self.slots[slot].block);
                self.slots[slot].block = block;
                slot
            };
            self.places.insert(block, slot);
            let at = slot * self.block_records;
            let mut end = at;
            if at == self.records.len() {
                entries.decode_block(block, |record| self.records.push(record));
                end = self.records.len();
                let last = self.records[end - 1];
                self.records.resize(at + self.block_records, last);
            } else {
                entries.decode_block(block, |record| {
                    self.records[end] = record;
                    end += 1;
                });
            }
            self.slots[slot].len = end - at;
            slot
        };
        let at = slot * self.block_records;
        &self.records[at..at + self.slots[slot].len]
    }

    /// The gets that reached the store, those its filter answered absent, those a cached block answered, and the blocks decoded, in that order.
    pub(crate) fn counts(&self) -> [u64; 4] {
        [self.gets, self.filtered, self.cached, self.decoded]
    }

    /// The bytes the cache's records hold at its full size.
    pub(crate) fn cache_bytes(&self) -> usize {
        self.capacity * self.block_records * size_of::<(TraceKey, TraceEntry)>()
    }
}

/// The liveness verdicts one engine published ([`crate::engine::Engine::publish_verdicts`]), whose delta ids index the snapshot's delta pool as its windows' do. `groups` are the unlocking-rune sets they were published for, one per distinct set among the deltas that read them. Each record's [`VerdictEntry::slots`] is where its slots start in `slots`, one per group in group order: the fired delta of the probe's asks that name none of that group's runes, or `None` where the verdict is not published for that group ([`crate::engine::Engine::end_verdict`], [`crate::liveness`]).
#[derive(Debug, Default)]
pub(crate) struct SnapshotVerdicts {
    pub(crate) groups: Vec<HashSet<Sym>>,
    pub(crate) records: HashMap<VerdictKey, VerdictEntry>,
    pub(crate) slots: Box<[Option<DeltaId>]>,
}

/// One engine's finished trace memo: the packed entries and the four tables their ids index. The tables are the engine memo's own pools flattened, so an entry read through the snapshot resolves as it did in the engine that recorded it. `verdicts` holds the liveness verdicts the engine published, empty for every other engine, and `verdict_pooled` the bytes of the fired deltas they added to `deltas`, which [`crate::engine::Engine::published_verdict_bytes`] counts.
#[derive(Debug, Default)]
pub struct MemoSnapshot {
    pub(crate) entries: SnapshotEntries,
    pub(crate) settled: Vec<Settled>,
    pub(crate) notes: Vec<Vec<String>>,
    pub(crate) deltas: Vec<Box<[Pointer]>>,
    pub(crate) reads: Vec<Box<[Read]>>,
    pub(crate) verdicts: SnapshotVerdicts,
    pub(crate) verdict_pooled: usize,
}

impl MemoSnapshot {
    /// How many windows this snapshot holds.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// How many liveness verdicts this snapshot carries.
    pub fn verdicts(&self) -> usize {
        self.verdicts.records.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The `[c] <config> snapshot` line: the records and blocks, the bytes the chunks of coded blocks hold as allocated, the fences', the filter's, and the settled, notes, deltas and reads tables' as held (each table's slots and what each slot holds on the heap, less the fired deltas the published verdicts added, which the `published_verdicts` line counts), and the release's sort and encoding times in milliseconds.
    pub fn footprint_line(&self, config: &str) -> String {
        let entries = &self.entries;
        format!(
            "[c] {config} snapshot records={} blocks={} packed_bytes={} fence_bytes={} filter_bytes={} pool_bytes={} sort_ms={} encode_ms={}",
            entries.records,
            entries.fences.len(),
            entries.packed_bytes(),
            size_of_val::<[Fence]>(&entries.fences),
            size_of_val::<[FilterBlock]>(&entries.filter),
            self.pool_bytes(),
            entries.sort.as_millis(),
            entries.encode.as_millis()
        )
    }

    /// The bytes the four tables the entries index hold, less the fired deltas the published verdicts added to the delta table.
    fn pool_bytes(&self) -> usize {
        let settled = self.settled.capacity() * size_of::<Settled>()
            + self
                .settled
                .iter()
                .map(|settled| settled.cell.adjustments.capacity() * size_of::<AdjustmentToken>())
                .sum::<usize>();
        let notes = self.notes.capacity() * size_of::<Vec<String>>()
            + self
                .notes
                .iter()
                .map(|notes| {
                    notes.capacity() * size_of::<String>()
                        + notes.iter().map(String::capacity).sum::<usize>()
                })
                .sum::<usize>();
        let deltas = self.deltas.capacity() * size_of::<Box<[Pointer]>>()
            + self
                .deltas
                .iter()
                .map(|delta| size_of_val::<[Pointer]>(delta))
                .sum::<usize>()
            - self.verdict_pooled;
        let reads = self.reads.capacity() * size_of::<Box<[Read]>>()
            + self
                .reads
                .iter()
                .map(|reads| size_of_val::<[Read]>(reads))
                .sum::<usize>();
        settled + notes + deltas + reads
    }

    /// The trace one entry stands for, rebuilt from the tables as the recording engine's miss returned it, without the ranking, which the fixpoint never records.
    pub(crate) fn trace(&self, entry: TraceEntry) -> TransitionTrace {
        TransitionTrace {
            settled: self.settled[entry.settled.index()].clone(),
            joint_tiebreak: entry.joint_tiebreak(),
            prospect: i64::from(entry.prospect()),
            decided_stage: entry.decided_stage(),
            notes: self.notes[entry.notes.index()].to_vec(),
            ranking: None,
        }
    }

    /// The settled record one entry names.
    pub(crate) fn settled(&self, entry: TraceEntry) -> &Settled {
        &self.settled[entry.settled.index()]
    }

    /// The fired delta one entry names.
    pub(crate) fn delta(&self, entry: TraceEntry) -> &[Pointer] {
        &self.deltas[entry.delta.index()]
    }

    /// The runes and classes one entry's evaluation read.
    pub(crate) fn reads(&self, entry: TraceEntry) -> &[Read] {
        &self.reads[entry.reads.index()]
    }
}

/// What a shared memo may not answer for. An entry whose evaluation read any of these runes' content or any of these classes' membership is a miss on that shared memo. So is an entry whose key names one of the runes: the left cell's, the input's, or a raw slot's ([`TraceKey::runes_named`]). The read journal makes that second test redundant; the module doc says why it is kept.
#[derive(Clone, Debug, Default)]
pub struct Exclusion {
    runes: HashSet<Sym>,
    classes: HashSet<Sym>,
    /// The runes as the keys name them: one flag per rune-field [`crate::index::Ordinal`], indexed by ordinal up to the highest one named, so that [`Exclusion::names`] tests a key's ordinals against a slice on every shared memo probe without resolving or hashing. A name the registry knows no family by has no ordinal and no flag, and no key can name it.
    named: Box<[bool]>,
}

impl Exclusion {
    /// An exclusion over these runes and no classes.
    pub fn of(index: &SpecIndex, runes: impl IntoIterator<Item = Sym>) -> Self {
        let runes: HashSet<Sym> = runes.into_iter().collect();
        let mut named: Vec<bool> = Vec::new();
        for rune in &runes {
            if let Some(ordinal) = index.rune_ordinal(*rune) {
                let at = usize::from(ordinal.get());
                if named.len() <= at {
                    named.resize(at + 1, false);
                }
                named[at] = true;
            }
        }
        Self {
            runes,
            classes: HashSet::default(),
            named: named.into_boxed_slice(),
        }
    }

    /// The same exclusion naming these predicate classes as well.
    pub fn with_classes(mut self, classes: impl IntoIterator<Item = Sym>) -> Self {
        self.classes = classes.into_iter().collect();
        self
    }

    /// An exclusion naming nothing, under which a shared memo supplies every key it holds.
    pub fn none() -> Self {
        Self::default()
    }

    /// Whether `key` names any of this exclusion's runes, on its left, as its input, or in any slot.
    pub(crate) fn names(&self, key: &TraceKey) -> bool {
        !self.runes.is_empty()
            && key
                .runes_named()
                .any(|ordinal| self.named.get(usize::from(ordinal.get())).copied() == Some(true))
    }

    /// Whether this shared memo may answer for `key` given what its entry read: no named rune among the key's, and no excluded rune or class among the reads.
    #[cfg(test)]
    pub(crate) fn admits(&self, key: &TraceKey, reads: &[Read]) -> bool {
        if self.runes.is_empty() && self.classes.is_empty() {
            return true;
        }
        !self.names(key) && self.admits_reads(reads)
    }

    /// Whether no excluded rune or class is among an entry's reads: what a lookup tests once [`Exclusion::names`] has cleared the key, which it tests first because that needs no entry.
    pub(crate) fn admits_reads(&self, reads: &[Read]) -> bool {
        (self.runes.is_empty() && self.classes.is_empty())
            || !reads.iter().any(|read| match read {
                Read::Rune(rune) => self.runes.contains(rune),
                Read::Class(class) => self.classes.contains(class),
            })
    }

    /// One flag per rune-field ordinal, true for this exclusion's runes, as [`Exclusion::names`] tests them.
    pub(crate) fn named(&self) -> &[bool] {
        &self.named
    }

    /// The runes this exclusion names.
    pub fn runes(&self) -> &HashSet<Sym> {
        &self.runes
    }

    /// The classes this exclusion names.
    pub fn classes(&self) -> &HashSet<Sym> {
        &self.classes
    }
}

/// One memo another engine may read, and the runes it may not read it for.
#[derive(Clone, Debug)]
pub struct SharedMemo {
    pub memo: Arc<MemoSnapshot>,
    pub excluded: Exclusion,
}

/// Every rune whose settlement can differ between `default` and the configuration enabling `features`: a rune with an unlock for one of them, or with any record whose `when:` names one (an unlock's own gate, a refusal, a prefer, an extension, a contraction or a resolution). The scan reads every `when:` a rune can carry rather than the record kinds `rebuild/test_spec_load.py` pins feature conditions to, so a kind that gains a feature gate widens this set without an edit here.
///
/// A rune outside this set has no record whose outcome depends on these features, so every window naming only such runes settles identically under both configurations. That is the window locality rule applied across configurations, and the reason the delta enumeration excludes this set from its shared memo.
pub fn unlocking_runes(index: &SpecIndex, features: &[Sym]) -> HashSet<Sym> {
    let enabled: HashSet<Sym> = features.iter().copied().collect();
    let gated = |when: &When| {
        when.feature
            .is_some_and(|feature| enabled.contains(&feature))
    };
    let record_gated = |records: &[PolicyRecord]| records.iter().any(|record| gated(&record.when));
    index
        .runes()
        .iter()
        .filter(|(_, rune)| {
            let policy = &rune.policy;
            rune.stances.iter().any(|(_, stance)| {
                stance.surface.unlocks.iter().any(|unlock| {
                    enabled.contains(&unlock.feature) || unlock.when.as_ref().is_some_and(gated)
                })
            }) || record_gated(&policy.refuse)
                || record_gated(&policy.prefer)
                || record_gated(&policy.extend)
                || record_gated(&policy.contract)
                || record_gated(&policy.resolve)
        })
        .map(|(name, _)| *name)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;
    use crate::types::DecidedStage;

    /// The entry the store-shape tests record at sorted position `at`: the settled id runs in fives and the reads id in fifties, so an id often equals the previous record's, the notes id cycles through three, and the delta id takes zero, one past 2^21, one past 2^28 and `u32::MAX`, so its varint takes one, four and five bytes.
    fn entry_at(at: usize) -> TraceEntry {
        TraceEntry::new(
            TraceSettledId::at(at / 5),
            TraceNotesId::at(at % 3),
            DeltaId::at(match at % 4 {
                0 => 0,
                1 => (1 << 21) + at,
                2 => (1 << 28) + at,
                _ => u32::MAX as usize,
            }),
            ReadsId::at(at / 50),
            (at % 2) as i64,
            at.is_multiple_of(3),
            DecidedStage::from_ordinal((at % 7) as u8).expect("the stages are seven"),
        )
    }

    /// Keys over every value the coding treats apart, generated in key order: an absent left rune and stance beside the smallest and largest ordinals a byte holds, extensions at both ends of a signed byte and on either side of zero, an absent and a present first slot, an absent and the largest fourth slot, and three kinds words.
    fn store_keys() -> Vec<TraceKey> {
        let ordinal = NonZeroU16::new;
        let mut keys = Vec::new();
        for left_rune in [0, 1, 255] {
            for left_stance in [0, 7] {
                for left_extension in [-128, -1, 0, 127] {
                    for token in [1, 255] {
                        for first in [0, 3] {
                            for fourth in [0, 255] {
                                for kinds in [0, 0b1001, 0x7fff] {
                                    keys.push(TraceKey {
                                        left_rune: ordinal(left_rune),
                                        left_stance: ordinal(left_stance),
                                        left_junction: None,
                                        left_extension,
                                        token: ordinal(token).expect("a token names a rune"),
                                        runes: [ordinal(first), None, ordinal(9), ordinal(fourth)],
                                        kinds: PackedKinds::from_word(kinds),
                                    });
                                }
                            }
                        }
                    }
                }
            }
        }
        keys
    }

    /// Keys beside `key` that differ in one field: the kinds word one either way, the extension one either way and past a signed byte, the fourth slot, the input rune and the left junction.
    fn neighbors(key: &TraceKey) -> Vec<TraceKey> {
        let word = key.kinds.word();
        let mut near = vec![
            TraceKey {
                kinds: PackedKinds::from_word(word + 1),
                ..*key
            },
            TraceKey {
                left_extension: key.left_extension + 1,
                ..*key
            },
            TraceKey {
                left_extension: key.left_extension - 1,
                ..*key
            },
            TraceKey {
                left_extension: 300,
                ..*key
            },
            TraceKey {
                runes: [
                    key.runes[0],
                    key.runes[1],
                    key.runes[2],
                    NonZeroU16::new(254),
                ],
                ..*key
            },
            TraceKey {
                token: NonZeroU16::new(2).expect("two is no zero"),
                ..*key
            },
            TraceKey {
                left_junction: NonZeroU16::new(1),
                ..*key
            },
        ];
        if word > 0 {
            near.push(TraceKey {
                kinds: PackedKinds::from_word(word - 1),
                ..*key
            });
        }
        near
    }

    /// The packed store answers the map it was packed from by the whole key: every present key gets its entry and every near neighbor misses, at caches of 0, 1, 2 and 455 blocks, under the shipped packing, with the filter off, with restart points every eight records, and at blocks of sixteen records held in chunks of 64 bytes, with restarts every four records, so that records cross block and chunk boundaries and each lookup path runs: a fence search, the filter's negative answer, a decode in place from a restart point, a cached block and an eviction. A one-record store answers its one key, and an empty one answers nothing. The counts show which path answered: the filter answers some gets absent only when it is built, no get is cached without a cache, every block is decoded at most once when the cache holds them all, and a cache smaller than the store decodes more blocks than it holds. A cache holds its blocks, the shipped packing's short last block among them, in the one allocation it reserves, which never grows.
    #[test]
    fn a_packed_store_answers_the_map_it_was_packed_from_by_the_whole_key() {
        assert_eq!(std::mem::size_of::<(TraceKey, TraceEntry)>(), 36);
        let keys = store_keys();
        let records: Vec<(TraceKey, TraceEntry)> = keys
            .iter()
            .enumerate()
            .map(|(at, key)| (*key, entry_at(at)))
            .collect();
        assert!(records.windows(2).all(|pair| pair[0].0 < pair[1].0));
        let expected: HashMap<TraceKey, TraceEntry> = records.iter().copied().collect();
        let shuffled: Vec<(TraceKey, TraceEntry)> = (0..records.len())
            .map(|at| records[at * 389 % records.len()])
            .collect();
        assert_eq!(
            shuffled
                .iter()
                .map(|(key, _)| *key)
                .collect::<HashSet<_>>()
                .len(),
            records.len()
        );
        let packings = [
            SnapshotPacking::default(),
            SnapshotPacking::new(SnapshotPacking::BLOCK_RECORDS, false).expect("it divides"),
            SnapshotPacking::new(8, true).expect("eight divides 512"),
            SnapshotPacking::for_test(16, 16, true, 64),
            SnapshotPacking::for_test(16, 4, false, 64),
            SnapshotPacking::for_test(16, 4, true, 64),
        ];
        for packing in packings {
            let entries =
                SnapshotEntries::pack(shuffled.clone(), packing).expect("every field fits");
            assert_eq!(entries.len(), records.len());
            assert_eq!(
                entries.blocks(),
                records.len().div_ceil(packing.block_records)
            );
            assert_eq!(entries.records(), records, "{packing:?}");
            if packing.chunk_bytes == 64 {
                assert!(entries.chunks.len() > 1, "{packing:?}");
            }
            let blocks = entries.blocks() as u64;
            for cache in [0, 1, 2, SNAPSHOT_CACHE_BLOCKS] {
                let mut reader = StoreReader::new(cache, entries.block_records());
                let mut gets = 0;
                for (key, _) in &shuffled {
                    assert_eq!(
                        entries.get(key, &mut reader),
                        expected.get(key).copied(),
                        "{packing:?} at {cache}: {key:?}"
                    );
                    gets += 1;
                    for near in neighbors(key) {
                        if expected.contains_key(&near) {
                            continue;
                        }
                        assert_eq!(
                            entries.get(&near, &mut reader),
                            None,
                            "{packing:?} at {cache}: {near:?}"
                        );
                        gets += 1;
                    }
                }
                let [counted, filtered, cached, decoded] = reader.counts();
                assert_eq!(counted, gets);
                assert_eq!(filtered > 0, packing.filter, "{packing:?} at {cache}");
                if cache == 0 {
                    assert_eq!(cached, 0);
                } else {
                    assert!(cached > 0, "{packing:?} at {cache}");
                }
                if cache as u64 >= blocks {
                    assert!(decoded <= blocks, "{packing:?} at {cache}: {decoded}");
                } else if cache > 0 {
                    assert!(decoded > cache as u64, "{packing:?} at {cache}: {decoded}");
                }
                assert_eq!(reader.cache_bytes(), cache * packing.block_records * 36);
                assert_eq!(
                    reader.records.capacity(),
                    cache * packing.block_records,
                    "{packing:?} at {cache}: the cache's blocks stay in the allocation it reserved"
                );
            }
            let (key, entry) = records[records.len() / 2];
            let single = SnapshotEntries::pack(vec![(key, entry)], packing).expect("it fits");
            assert_eq!(single.blocks(), 1);
            let empty = SnapshotEntries::pack(Vec::new(), packing).expect("nothing fits");
            assert!(empty.is_empty() && empty.blocks() == 0 && empty.records().is_empty());
            for cache in [0, 1, 2, SNAPSHOT_CACHE_BLOCKS] {
                let mut reader = StoreReader::new(cache, packing.block_records);
                assert_eq!(single.get(&key, &mut reader), Some(entry));
                for near in neighbors(&key) {
                    assert_eq!(single.get(&near, &mut reader), None, "{near:?}");
                }
                assert_eq!(empty.get(&key, &mut reader), None);
            }
        }
        assert!(SnapshotPacking::new(0, true).is_none());
        assert!(SnapshotPacking::new(3, true).is_none());
        assert!(SnapshotPacking::new(1024, true).is_none());
        assert!(SnapshotEntries::default().lookup(&keys[0]).is_none());
    }

    /// A key field that does not fit its byte is refused, naming the field and its value, and so is a key held twice, naming the key, since a live memo never holds one twice.
    #[test]
    fn a_packing_refuses_a_field_past_its_byte_and_a_key_held_twice() {
        let keys = store_keys();
        let base = keys[0];
        let wide = NonZeroU16::new(256);
        let refused = [
            (
                TraceKey {
                    left_rune: wide,
                    ..base
                },
                "left rune ordinal, 256,",
            ),
            (
                TraceKey {
                    left_stance: wide,
                    ..base
                },
                "left stance ordinal, 256,",
            ),
            (
                TraceKey {
                    left_junction: NonZeroU16::new(300),
                    ..base
                },
                "left junction ordinal, 300,",
            ),
            (
                TraceKey {
                    left_extension: 128,
                    ..base
                },
                "left extension, 128,",
            ),
            (
                TraceKey {
                    left_extension: -129,
                    ..base
                },
                "left extension, -129,",
            ),
            (
                TraceKey {
                    token: wide.expect("not zero"),
                    ..base
                },
                "input rune ordinal, 256,",
            ),
            (
                TraceKey {
                    runes: [None, None, None, NonZeroU16::new(u16::MAX)],
                    ..base
                },
                "fourth slot's rune ordinal, 65535,",
            ),
        ];
        for (key, field) in refused {
            let error = SnapshotEntries::pack(
                vec![(base, entry_at(0)), (key, entry_at(1))],
                SnapshotPacking::default(),
            )
            .expect_err(field);
            assert!(error.contains(field), "{error}");
        }
        let error = SnapshotEntries::pack(
            vec![
                (base, entry_at(0)),
                (keys[1], entry_at(1)),
                (base, entry_at(2)),
            ],
            SnapshotPacking::default(),
        )
        .expect_err("a key held twice");
        assert!(error.contains(&format!("{base:?} twice")), "{error}");
    }

    /// The mini fixture unlocks a `qsMay` entry under `ss03` and nothing else under any feature, so `ss03` names `qsMay` alone and an empty feature set names no rune.
    #[test]
    fn the_unlocking_runes_of_a_configuration_are_the_ones_reading_its_features() {
        let index = fixtures::mini();
        let ss03 = fixtures::sym(&index, "ss03");
        let named = unlocking_runes(&index, &[ss03]);
        let names: Vec<&str> = {
            let mut names: Vec<&str> = named.iter().map(|rune| index.resolve(*rune)).collect();
            names.sort_unstable();
            names
        };
        assert_eq!(names, ["qsMay"]);
        assert!(unlocking_runes(&index, &[]).is_empty());
    }

    /// An exclusion rejects any key naming one of its runes, on the left, as the input, or in any raw slot, and an empty exclusion admits everything.
    #[test]
    fn an_exclusion_refuses_a_key_naming_any_of_its_runes_anywhere() {
        let index = fixtures::mini();
        let may = fixtures::sym(&index, "qsMay");
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may_ordinal = index.rune_ordinal(may).expect("qsMay is modeled");
        let excluded = Exclusion::of(&index, [may]);
        let mut key = TraceKey::for_test(&index, pea, [Some(tea), None, None, None]);
        assert!(excluded.admits(&key, &[]));
        assert!(!excluded.names(&key));
        assert!(Exclusion::none().admits(&key, &[Read::Rune(may)]));
        key.runes[2] = Some(may_ordinal);
        assert!(!excluded.admits(&key, &[]));
        assert!(excluded.names(&key));
        key.runes[2] = None;
        key.left_rune = Some(may_ordinal);
        assert!(!excluded.admits(&key, &[]));
        key.left_rune = None;
        key.token = may_ordinal;
        assert!(!excluded.admits(&key, &[]));
        assert!(
            Exclusion::of(&index, [fixtures::sym(&index, "half")]).admits(&key, &[]),
            "a name that is no rune flags no ordinal"
        );
    }

    /// The exclusion tests the read journal: an entry whose evaluation read an excluded rune or class is rejected even though its key names neither, and one that read neither is admitted. A lookup that clears the key with `names` and then tests the reads alone answers as `admits` does.
    #[test]
    fn an_exclusion_refuses_an_entry_by_what_it_read() {
        let index = fixtures::mini();
        let may = fixtures::sym(&index, "qsMay");
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let class = fixtures::sym(&index, "halves-that-exit-at-x-height");
        let key = TraceKey::for_test(&index, pea, [Some(tea), None, None, None]);
        let by_reads = Exclusion::of(&index, [may]).with_classes([class]);
        assert!(by_reads.admits(&key, &[Read::Rune(pea), Read::Rune(tea)]));
        assert!(!by_reads.admits(&key, &[Read::Rune(pea), Read::Rune(may)]));
        assert!(!by_reads.admits(&key, &[Read::Class(class)]));
        assert!(
            Exclusion::of(&index, [])
                .with_classes([class])
                .admits(&key, &[Read::Rune(may)])
        );
        let may_ordinal = index.rune_ordinal(may).expect("qsMay is modeled");
        let mut naming = key;
        naming.runes[1] = Some(may_ordinal);
        let exclusions = [
            Exclusion::none(),
            Exclusion::of(&index, [may]),
            Exclusion::of(&index, []).with_classes([class]),
            by_reads,
        ];
        let reads: [&[Read]; 4] = [
            &[],
            &[Read::Rune(pea)],
            &[Read::Rune(may)],
            &[Read::Class(class)],
        ];
        for exclusion in &exclusions {
            for key in [&key, &naming] {
                for read in reads {
                    assert_eq!(
                        exclusion.admits(key, read),
                        !exclusion.names(key) && exclusion.admits_reads(read)
                    );
                }
            }
        }
    }
}
