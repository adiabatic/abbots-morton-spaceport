// A hand-rolled FxHash (rustc-hash algorithm), because no crates can be fetched in this environment.
use std::collections::{HashMap, HashSet};
use std::hash::{BuildHasherDefault, Hasher};

#[derive(Default, Clone, Copy)]
pub struct FxHasher {
    hash: u64,
}

const SEED: u64 = 0x51_7c_c1_b7_27_22_0a_95;

impl FxHasher {
    #[inline(always)]
    fn add(&mut self, i: u64) {
        self.hash = (self.hash.rotate_left(5) ^ i).wrapping_mul(SEED);
    }
}

impl Hasher for FxHasher {
    #[inline(always)]
    fn write(&mut self, bytes: &[u8]) {
        let mut chunks = bytes.chunks_exact(8);
        for c in &mut chunks {
            self.add(u64::from_le_bytes(c.try_into().unwrap()));
        }
        let rest = chunks.remainder();
        if !rest.is_empty() {
            let mut buf = [0u8; 8];
            buf[..rest.len()].copy_from_slice(rest);
            self.add(u64::from_le_bytes(buf));
        }
    }
    #[inline(always)]
    fn write_u8(&mut self, i: u8) {
        self.add(i as u64);
    }
    #[inline(always)]
    fn write_i8(&mut self, i: i8) {
        self.add(i as u8 as u64);
    }
    #[inline(always)]
    fn write_u16(&mut self, i: u16) {
        self.add(i as u64);
    }
    #[inline(always)]
    fn write_u32(&mut self, i: u32) {
        self.add(i as u64);
    }
    #[inline(always)]
    fn write_u64(&mut self, i: u64) {
        self.add(i);
    }
    #[inline(always)]
    fn write_usize(&mut self, i: usize) {
        self.add(i as u64);
    }
    #[inline(always)]
    fn finish(&self) -> u64 {
        self.hash
    }
}

pub type FxBuild = BuildHasherDefault<FxHasher>;
pub type FxMap<K, V> = HashMap<K, V, FxBuild>;
pub type FxSet<K> = HashSet<K, FxBuild>;
