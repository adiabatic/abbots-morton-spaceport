// The memo sub-question in isolation: the same key stream as memo.py, on a 10-byte packed struct.
use crate::engine::MemoKey;
use crate::fx::FxMap;
use std::time::Instant;

const N_KEYS: u64 = 900_000;
const N_LOOKUPS: u64 = 2_428_420;

#[inline(always)]
fn mix(x: u64) -> u64 {
    let mut z = x.wrapping_add(0x9E3779B97F4A7C15);
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D049BB133111EB);
    z ^ (z >> 31)
}

#[inline(always)]
fn key_for(i: u64) -> MemoKey {
    let s = mix(i);
    MemoKey {
        left_kind: (s % 5) as u8,
        lrune: ((s >> 3) % 18) as i8,
        lstance: ((s >> 8) % 4) as i8,
        lseam: ((s >> 12) % 5) as i8 - 1,
        lext: ((s >> 16) % 3) as i8,
        token: ((s >> 20) % 18) as u8,
        r1: ((s >> 24) % 23) as u8,
        r2: ((s >> 29) % 23) as u8,
        r3: ((s >> 34) % 23) as u8,
        r4: ((s >> 39) % 23) as u8,
    }
}

pub fn run() {
    let t0 = Instant::now();
    let mut memo: FxMap<MemoKey, u64> = FxMap::default();
    for i in 0..N_KEYS {
        memo.insert(key_for(i), i);
    }
    let build = t0.elapsed().as_secs_f64();
    let n_keys = memo.len();

    let t1 = Instant::now();
    let mut checksum: u64 = 0;
    let mut hits: u64 = 0;
    for j in 0..N_LOOKUPS {
        let idx = mix(j ^ 0xABCDEF) % (N_KEYS * 2);
        if let Some(&v) = memo.get(&key_for(idx)) {
            checksum = checksum.wrapping_add(v);
            hits += 1;
        }
    }
    let lookup = t1.elapsed().as_secs_f64();
    println!(
        "{{\"impl\":\"rust\",\"n_keys\":{},\"n_lookups\":{},\"build_seconds\":{:.6},\"lookup_seconds\":{:.6},\"ns_per_lookup\":{:.4},\"hits\":{},\"checksum\":{},\"key_struct_bytes\":{}}}",
        n_keys,
        N_LOOKUPS,
        build,
        lookup,
        lookup * 1e9 / N_LOOKUPS as f64,
        hits,
        checksum,
        std::mem::size_of::<MemoKey>()
    );
}
