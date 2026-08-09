//! k1-micro: primitive-operation benchmark, Rust side. No dependencies (std only),
//! so it builds with the network down. Built with `cargo build --release`
//! (opt-level 3, LTO, codegen-units = 1, panic = abort).
//!
//! Every kernel mirrors bench.py's loop skeleton exactly: iterate a prebuilt
//! slice, store the product into a preallocated heap buffer, and fold a cheap
//! portable integer into an accumulator that is printed. Dead-code elimination
//! is defeated three ways at once: the escaping store into a heap Vec, the
//! `black_box` on the constructed value, and the printed accumulator, which is
//! also checked against Python's.
//!
//! Strong checksums are computed in separate, untimed verify passes using the
//! same 64-bit mixer as bench.py: h = (h ^ v).wrapping_mul(0x100000001b3).

use std::collections::HashMap;
use std::fs;
use std::hash::{BuildHasher, BuildHasherDefault, Hash, Hasher};
use std::hint::black_box;
use std::time::Instant;

const MASK_PRIME: u64 = 0x100000001b3;
const FNV_OFFSET: u64 = 0xcbf29ce484222325;

const M1: usize = 400_000;
const M8: usize = 700_000;
const NPROBE: usize = 1_000_000;
const NALLOC: usize = 10_000_000;
const NLISTS: usize = 60_000;
const NLEGACY: usize = 500_000;
const REPS: usize = 5;
const MED_REPS: usize = 3;
const HEAVY_REPS: usize = 2;
const WARMUP: usize = 1;
const VERIFY_CAP: usize = 200_000;
const PROBE_MUL: u64 = 2654435761;

#[inline(always)]
fn mix(h: u64, v: u64) -> u64 {
    (h ^ v).wrapping_mul(MASK_PRIME)
}

// ---------------------------------------------------------------- FxHasher
// Hand-rolled (rustc-hash's algorithm) because the crate index may be
// unreachable; this is the "fast non-cryptographic hasher" column.

#[derive(Default, Clone, Copy)]
struct FxHasher {
    hash: u64,
}
const FXSEED: u64 = 0x51_7c_c1_b7_27_22_0a_95;

impl FxHasher {
    #[inline(always)]
    fn add(&mut self, w: u64) {
        self.hash = (self.hash.rotate_left(5) ^ w).wrapping_mul(FXSEED);
    }
}

impl Hasher for FxHasher {
    #[inline]
    fn write(&mut self, bytes: &[u8]) {
        let mut chunks = bytes.chunks_exact(8);
        for c in &mut chunks {
            self.add(u64::from_le_bytes(c.try_into().unwrap()));
        }
        let rem = chunks.remainder();
        if !rem.is_empty() {
            let mut b = [0u8; 8];
            b[..rem.len()].copy_from_slice(rem);
            self.add(u64::from_le_bytes(b));
        }
    }
    #[inline]
    fn write_u8(&mut self, i: u8) {
        self.add(i as u64)
    }
    #[inline]
    fn write_u32(&mut self, i: u32) {
        self.add(i as u64)
    }
    #[inline]
    fn write_u64(&mut self, i: u64) {
        self.add(i)
    }
    #[inline]
    fn write_usize(&mut self, i: usize) {
        self.add(i as u64)
    }
    #[inline]
    fn finish(&self) -> u64 {
        // A strong finalizer, not Fx's bare multiply. Measured on this data:
        // returning `self.hash` puts mapU64/insert-fx at 25,744 ns/op (a 600x
        // blowup) and rustc-hash 2.x's `rotate_left(20)` still leaves it at 448.
        // Fx's single multiply concentrates entropy in bits the hashbrown bucket
        // index does not read, and this key's low bits are a 5-value slot
        // alphabet. Three extra cycles of splitmix-style avalanche fix it, and a
        // real port would ship this, not the bare Fx.
        let mut h = self.hash;
        h ^= h >> 32;
        h = h.wrapping_mul(0xd6e8feb86659fd93);
        h ^= h >> 32;
        h
    }
}

type FxBuild = BuildHasherDefault<FxHasher>;

// ------------------------------------------------------------------ records

type S = &'static str;

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
struct R8 {
    a: S,
    b: S,
    c: Option<S>,
    d: Option<S>,
    e: Option<S>,
    n1: i32,
    n2: i32,
    n3: i32,
}

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct Cand5 {
    stance: S,
    entry: Option<S>,
    seam: Option<S>,
    order_index: i32,
    exit_index: i32,
}

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct Key10([Option<S>; 10]);

#[derive(Clone, Copy)]
struct Cand {
    st: u8,
    sy: i32,
    ei: i32,
    oi: i32,
    jc: i32,
}

// ------------------------------------------------------------------ reporting

struct Row {
    op: String,
    ops: usize,
    raw: f64,
    minv: f64,
    maxv: f64,
    spread: f64,
    control: f64,
    net: f64,
    reps: usize,
    acc: u64,
    extra: Vec<(String, String)>,
}

static mut ROWS: Vec<Row> = Vec::new();

fn record(
    name: &str,
    ops: usize,
    raw_ns: &mut Vec<u128>,
    ctl_ns: &mut Vec<u128>,
    reps: usize,
    acc: u64,
    extra: Vec<(String, String)>,
) {
    raw_ns.sort();
    let med = raw_ns[raw_ns.len() / 2] as f64;
    let (cmed, net) = if ctl_ns.is_empty() {
        (0.0, med)
    } else {
        ctl_ns.sort();
        let c = ctl_ns[ctl_ns.len() / 2] as f64;
        (c, med - c)
    };
    let n = ops as f64;
    let r = Row {
        op: name.to_string(),
        ops,
        raw: med / n,
        minv: raw_ns[0] as f64 / n,
        maxv: raw_ns[raw_ns.len() - 1] as f64 / n,
        spread: 100.0 * (raw_ns[raw_ns.len() - 1] - raw_ns[0]) as f64 / med,
        control: cmed / n,
        net: net / n,
        reps,
        acc,
        extra,
    };
    eprintln!("  {:44} net {:9.2}  raw {:9.2} ns/op", r.op, r.net, r.raw);
    unsafe {
        let rows = &mut *std::ptr::addr_of_mut!(ROWS);
        rows.push(r);
    }
}

/// Run `f` WARMUP+reps times and `c` the same, timing each. `f` returns a
/// portable accumulator that is checked against Python's.
fn bench<F: FnMut() -> u64, C: FnMut() -> u64>(
    name: &str,
    ops: usize,
    reps: usize,
    mut f: F,
    ctl: Option<C>,
    extra: Vec<(String, String)>,
) -> u64 {
    let mut acc = 0u64;
    for _ in 0..WARMUP {
        acc = black_box(f());
    }
    let mut raw = Vec::new();
    for _ in 0..reps {
        let t = Instant::now();
        acc = black_box(f());
        raw.push(t.elapsed().as_nanos());
    }
    let mut cn = Vec::new();
    if let Some(mut c) = ctl {
        for _ in 0..WARMUP {
            black_box(c());
        }
        for _ in 0..reps {
            let t = Instant::now();
            black_box(c());
            cn.push(t.elapsed().as_nanos());
        }
    }
    record(name, ops, &mut raw, &mut cn, reps, acc, extra);
    acc
}

fn no_ctl() -> Option<fn() -> u64> {
    None
}

fn ex(pairs: &[(&str, String)]) -> Vec<(String, String)> {
    pairs.iter().map(|(k, v)| (k.to_string(), v.clone())).collect()
}

// ---------------------------------------------------------------------- main

fn main() {
    let dir = std::env::args().nth(1).unwrap_or_else(|| "../data".to_string());
    let out = std::env::args().nth(2).unwrap_or_else(|| "../out/rust.json".to_string());

    let sym_text = fs::read_to_string(format!("{dir}/symbols.txt")).unwrap();
    let syms: Vec<S> = sym_text
        .lines()
        .map(|s| Box::leak(s.to_string().into_boxed_str()) as S)
        .collect();
    assert_eq!(syms[0], "-");
    let mut sym_id: HashMap<S, u64> = HashMap::new();
    for (i, s) in syms.iter().enumerate() {
        sym_id.insert(s, i as u64);
    }
    let symn: Vec<Option<S>> = syms
        .iter()
        .enumerate()
        .map(|(i, s)| if i == 0 { None } else { Some(*s) })
        .collect();

    let gbuf = fs::read(format!("{dir}/keys-global.u8")).unwrap();
    let pbuf = fs::read(format!("{dir}/keys-packed.u64")).unwrap();
    let n = gbuf.len() / 10;
    assert_eq!(pbuf.len(), n * 8);
    eprintln!("rust: N={n} symbols={}", syms.len());

    let packed: Vec<u64> = (0..n)
        .map(|i| u64::from_le_bytes(pbuf[i * 8..i * 8 + 8].try_into().unwrap()))
        .collect();
    let keys: Vec<Key10> = (0..n)
        .map(|i| {
            let mut k = [None; 10];
            for s in 0..10 {
                k[s] = symn[gbuf[i * 10 + s] as usize];
            }
            Key10(k)
        })
        .collect();
    let ids10: Vec<[u8; 10]> = (0..n)
        .map(|i| gbuf[i * 10..i * 10 + 10].try_into().unwrap())
        .collect();

    let fields: Vec<R8> = (0..M1)
        .map(|i| {
            let k = keys[i].0;
            R8 {
                a: k[0].unwrap(),
                b: k[5].unwrap(),
                c: k[1],
                d: k[2],
                e: k[3],
                n1: k[4].unwrap().parse::<i32>().unwrap(),
                n2: (i % 97) as i32,
                n3: (i % 13) as i32,
            }
        })
        .collect();

    let verify = |buf: &[R8]| -> u64 {
        let mut h = FNV_OFFSET;
        for r in buf.iter().take(VERIFY_CAP) {
            for v in [Some(r.a), Some(r.b), r.c, r.d, r.e] {
                h = mix(h, v.map_or(0, |s| sym_id[s]));
            }
            h = mix(h, r.n1 as i64 as u64);
            h = mix(h, r.n2 as i64 as u64);
            h = mix(h, r.n3 as i64 as u64);
        }
        h
    };

    // ------------------------------------------------- B1 construct 8 fields
    eprintln!("B1 construct 8-field record");
    {
        let mut buf: Vec<R8> = fields.clone();
        let dummy = fields[0];
        let f = &fields;
        {
            let mut run = || {
                let mut acc = 0u64;
                for (j, r) in f.iter().enumerate() {
                    buf[j] = black_box(R8 {
                        a: r.a,
                        b: r.b,
                        c: r.c,
                        d: r.d,
                        e: r.e,
                        n1: r.n1,
                        n2: r.n2,
                        n3: r.n3,
                    });
                    acc ^= r.n2 as u64;
                }
                acc
            };
            run();
        }
        let cksum = verify(&buf);
        let mut buf2: Vec<R8> = fields.clone();
        let mut ctl = || {
            let mut acc = 0u64;
            for (j, r) in f.iter().enumerate() {
                buf2[j].a = black_box(r.a);
                acc ^= r.n2 as u64;
            }
            acc
        };
        let mut buf3: Vec<R8> = fields.clone();
        let run = || {
            let mut acc = 0u64;
            for (j, r) in f.iter().enumerate() {
                buf3[j] = black_box(R8 {
                    a: r.a,
                    b: r.b,
                    c: r.c,
                    d: r.d,
                    e: r.e,
                    n1: r.n1,
                    n2: r.n2,
                    n3: r.n3,
                });
                acc ^= r.n2 as u64;
            }
            acc
        };
        black_box(dummy);
        bench(
            "construct8/struct-copy",
            M1,
            REPS,
            run,
            Some(&mut ctl),
            ex(&[("checksum", cksum.to_string())]),
        );
    }

    // ------------------------------------------- B1x legacy 5-field Candidate
    eprintln!("B1x legacy 5-field Candidate");
    {
        let ctext = fs::read_to_string(format!("{dir}/candidates.tsv")).unwrap();
        let rows: Vec<Vec<S>> = ctext
            .lines()
            .skip(1)
            .filter(|l| !l.is_empty())
            .map(|l| {
                l.split('\t')
                    .map(|s| Box::leak(s.to_string().into_boxed_str()) as S)
                    .collect()
            })
            .collect();
        let cf: Vec<Cand5> = rows
            .iter()
            .enumerate()
            .map(|(i, r)| Cand5 {
                stance: r[1],
                entry: if r[2] == "-" { None } else { Some(r[2]) },
                seam: if r[3] == "-" { None } else { Some(r[3]) },
                order_index: i as i32,
                exit_index: i as i32 + 1,
            })
            .collect();
        let reps_in = NLEGACY / cf.len();
        let nops = reps_in * cf.len();
        let mut buf: Vec<Cand5> = cf.clone();
        let mut buf2: Vec<Cand5> = cf.clone();
        let run = || {
            let mut acc = 0u64;
            for _ in 0..reps_in {
                for (j, c) in cf.iter().enumerate() {
                    buf[j] = black_box(Cand5 {
                        stance: c.stance,
                        entry: c.entry,
                        seam: c.seam,
                        order_index: c.order_index,
                        exit_index: c.exit_index,
                    });
                    acc ^= c.order_index as u64;
                }
            }
            acc
        };
        let mut ctl = || {
            let mut acc = 0u64;
            for _ in 0..reps_in {
                for (j, c) in cf.iter().enumerate() {
                    buf2[j].stance = black_box(c.stance);
                    acc ^= c.order_index as u64;
                }
            }
            acc
        };
        bench(
            "legacy5/struct-construct",
            nops,
            MED_REPS,
            run,
            Some(&mut ctl),
            vec![],
        );

        let sip = std::collections::hash_map::RandomState::new();
        let pre: Vec<u64> = cf.iter().map(|c| sip.hash_one(c)).collect();
        let run = || {
            let mut acc = 0u64;
            for _ in 0..reps_in {
                for c in cf.iter() {
                    acc ^= sip.hash_one(c);
                }
            }
            acc
        };
        let mut ctl = || {
            let mut acc = 0u64;
            for _ in 0..reps_in {
                for h in pre.iter() {
                    acc ^= *h;
                }
            }
            acc
        };
        bench("legacy5/struct-hash-siphash", nops, MED_REPS, run, Some(&mut ctl), vec![]);
        let fx = FxBuild::default();
        let run = || {
            let mut acc = 0u64;
            for _ in 0..reps_in {
                for c in cf.iter() {
                    acc ^= fx.hash_one(c);
                }
            }
            acc
        };
        let mut ctl = || {
            let mut acc = 0u64;
            for _ in 0..reps_in {
                for h in pre.iter() {
                    acc ^= *h;
                }
            }
            acc
        };
        bench("legacy5/struct-hash-fx", nops, MED_REPS, run, Some(&mut ctl), vec![]);
    }

    // ------------------------------------------------------- B2 hash 8-field
    eprintln!("B2 hash 8-field record");
    {
        let sip = std::collections::hash_map::RandomState::new();
        let fx = FxBuild::default();
        let f = &fields;
        let pre: Vec<u64> = f.iter().map(|r| sip.hash_one(r)).collect();
        let distinct = {
            let mut v = pre.clone();
            v.sort_unstable();
            v.dedup();
            v.len()
        };
        let run = || {
            let mut acc = 0u64;
            for r in f.iter() {
                acc ^= sip.hash_one(r);
            }
            acc
        };
        let mut ctl = || {
            let mut acc = 0u64;
            for h in pre.iter() {
                acc ^= *h;
            }
            acc
        };
        bench(
            "hash8/struct-siphash",
            M1,
            REPS,
            run,
            Some(&mut ctl),
            ex(&[("distinct_hash_values", distinct.to_string())]),
        );
        let prefx: Vec<u64> = f.iter().map(|r| fx.hash_one(r)).collect();
        let distinct_fx = {
            let mut v = prefx.clone();
            v.sort_unstable();
            v.dedup();
            v.len()
        };
        let run = || {
            let mut acc = 0u64;
            for r in f.iter() {
                acc ^= fx.hash_one(r);
            }
            acc
        };
        let mut ctl = || {
            let mut acc = 0u64;
            for h in prefx.iter() {
                acc ^= *h;
            }
            acc
        };
        bench(
            "hash8/struct-fx",
            M1,
            REPS,
            run,
            Some(&mut ctl),
            ex(&[("distinct_hash_values", distinct_fx.to_string())]),
        );
    }

    // ------------------------------------------------------------- B3 equality
    eprintln!("B3 equality compare");
    {
        let x = &fields;
        let y: Vec<R8> = fields.clone();
        let mut z: Vec<R8> = fields.clone();
        z.rotate_left(1);
        let run = || {
            let mut c = 0u64;
            for j in 0..M1 {
                c += (black_box(x[j]) == black_box(y[j])) as u64;
            }
            c
        };
        let mut ctl = || {
            let mut c = 0u64;
            for j in 0..M1 {
                c += (black_box(x[j].n2) == black_box(y[j].n2)) as u64;
            }
            c
        };
        bench("eq8/struct-equal", M1, REPS, run, Some(&mut ctl), vec![]);
        let run = || {
            let mut c = 0u64;
            for j in 0..M1 {
                c += (black_box(x[j]) == black_box(z[j])) as u64;
            }
            c
        };
        let mut ctl = || {
            let mut c = 0u64;
            for j in 0..M1 {
                c += (black_box(x[j].n2) == black_box(z[j].n2)) as u64;
            }
            c
        };
        bench("eq8/struct-unequal", M1, REPS, run, Some(&mut ctl), vec![]);
    }

    // ------------------------------------------- B5 strings vs u8 symbol ids
    eprintln!("B5 interned strings vs u8 symbol ids");
    {
        let sip = std::collections::hash_map::RandomState::new();
        let fx = FxBuild::default();
        let a: Vec<Key10> = keys[..M1].to_vec();
        let b: Vec<Key10> = keys[..M1].to_vec();
        let ia: Vec<[u8; 10]> = ids10[..M1].to_vec();
        let ib: Vec<[u8; 10]> = ids10[..M1].to_vec();
        let pa: Vec<u64> = packed[..M1].to_vec();
        let pb: Vec<u64> = packed[..M1].to_vec();

        let run = || {
            let mut c = 0u64;
            for j in 0..M1 {
                c += (black_box(a[j]) == black_box(b[j])) as u64;
            }
            c
        };
        let mut ctl = || {
            let mut c = 0u64;
            for j in 0..M1 {
                c += (black_box(a[j].0[0]) == black_box(b[j].0[0])) as u64;
            }
            c
        };
        bench("sym/eq-10str-tuple", M1, REPS, run, Some(&mut ctl), vec![]);
        let run = || {
            let mut c = 0u64;
            for j in 0..M1 {
                c += (black_box(ia[j]) == black_box(ib[j])) as u64;
            }
            c
        };
        let mut ctl = || {
            let mut c = 0u64;
            for j in 0..M1 {
                c += (black_box(ia[j][0]) == black_box(ib[j][0])) as u64;
            }
            c
        };
        bench("sym/eq-10u8-bytes", M1, REPS, run, Some(&mut ctl), vec![]);
        let run = || {
            let mut c = 0u64;
            for j in 0..M1 {
                c += (black_box(pa[j]) == black_box(pb[j])) as u64;
            }
            c
        };
        let mut ctl = || {
            let mut c = 0u64;
            for j in 0..M1 {
                c += (black_box(pa[j] & 0xff) == black_box(pb[j] & 0xff)) as u64;
            }
            c
        };
        bench("sym/eq-packed-u64", M1, REPS, run, Some(&mut ctl), vec![]);

        let pre: Vec<u64> = a.iter().map(|k| sip.hash_one(k)).collect();
        macro_rules! hbench {
            ($name:expr, $src:expr, $bh:expr) => {{
                let run = || {
                    let mut acc = 0u64;
                    for o in $src.iter() {
                        acc ^= $bh.hash_one(o);
                    }
                    acc
                };
                let mut ctl = || {
                    let mut acc = 0u64;
                    for h in pre.iter() {
                        acc ^= *h;
                    }
                    acc
                };
                bench($name, M1, REPS, run, Some(&mut ctl), vec![]);
            }};
        }
        hbench!("sym/hash-10str-tuple", a, sip);
        hbench!("sym/hash-10str-tuple-fx", a, fx);
        hbench!("sym/hash-10u8-bytes", ia, sip);
        hbench!("sym/hash-10u8-bytes-fx", ia, fx);
        hbench!("sym/hash-packed-u64", pa, sip);
        hbench!("sym/hash-packed-u64-fx", pa, fx);
    }

    // ------------------------------------------------------------- B6 ranking
    eprintln!("B6 rank a 3-8 candidate list");
    {
        let ctext = fs::read_to_string(format!("{dir}/candidates.tsv")).unwrap();
        let rows: Vec<Vec<String>> = ctext
            .lines()
            .skip(1)
            .filter(|l| !l.is_empty())
            .map(|l| l.split('\t').map(|s| s.to_string()).collect())
            .collect();
        let mut stances: Vec<String> = rows.iter().map(|r| r[1].clone()).collect();
        stances.sort();
        stances.dedup();
        let base: Vec<(u8, i32, i32)> = rows
            .iter()
            .map(|r| {
                let st = stances.iter().position(|s| *s == r[1]).unwrap() as u8;
                let sy = match r[3].as_str() {
                    "-" => -1,
                    "ex-y0" => 0,
                    "ex-y5" => 5,
                    "ex-y6" => 6,
                    o => panic!("seam {o}"),
                };
                (st, sy, r[4].parse::<i32>().unwrap())
            })
            .collect();
        let nc = base.len();
        let mut lists: Vec<Vec<Cand>> = Vec::with_capacity(NLISTS);
        for j in 0..NLISTS {
            let l = 3 + (j % 6);
            let mut item = Vec::with_capacity(l);
            for k in 0..l {
                let (st, sy, cnt) = base[(j * 7 + k * 13) % nc];
                item.push(Cand {
                    st,
                    sy,
                    ei: k as i32,
                    oi: ((j + k) % 11) as i32,
                    jc: cnt % 5,
                });
            }
            lists.push(item);
        }
        #[inline(always)]
        fn floor_key(c: &Cand) -> (i32, i32, i32) {
            (
                if c.sy < 0 { 1 } else { 0 },
                if c.sy < 0 { 1_000_000 } else { c.sy },
                c.ei,
            )
        }
        #[inline(always)]
        fn rank_key(c: &Cand) -> (i32, i32, i32) {
            (-c.jc, c.oi, c.ei)
        }
        let mut h = FNV_OFFSET;
        let mut scratch = [Cand { st: 0, sy: 0, ei: 0, oi: 0, jc: 0 }; 8];
        for item in lists.iter() {
            let l = item.len();
            scratch[..l].copy_from_slice(item);
            scratch[..l].sort_by_key(floor_key);
            let (o0, o1) = (scratch[0], scratch[1]);
            scratch[..l].copy_from_slice(item);
            scratch[..l].sort_by_key(rank_key);
            let r0 = scratch[0];
            h = mix(h, o0.st as u64);
            h = mix(h, o1.ei as i64 as u64);
            h = mix(h, r0.st as u64);
            h = mix(h, r0.oi as i64 as u64);
        }
        let ls = &lists;
        let run = || {
            let mut acc = 0u64;
            let mut sc = [Cand { st: 0, sy: 0, ei: 0, oi: 0, jc: 0 }; 8];
            for item in ls.iter() {
                let l = item.len();
                sc[..l].copy_from_slice(item);
                sc[..l].sort_by_key(floor_key);
                acc ^= (sc[0].ei * 3 + sc[1].ei * 5) as u64;
                sc[..l].copy_from_slice(item);
                sc[..l].sort_by_key(rank_key);
                acc ^= (sc[0].oi * 7) as u64;
                black_box(&sc);
            }
            acc
        };
        let mut ctl = || {
            let mut acc = 0u64;
            for item in ls.iter() {
                acc ^= (item[0].ei * 3 + item[1].ei * 5) as u64;
                acc ^= (item[0].oi * 7) as u64;
            }
            acc
        };
        bench(
            "rank/two-stable-sorts-per-list",
            NLISTS,
            REPS,
            run,
            Some(&mut ctl),
            ex(&[("checksum", h.to_string()), ("lists", NLISTS.to_string())]),
        );
    }

    // -------------------------------------------------------------- B8 filter
    eprintln!("B8 filter a 700k-row table");
    {
        let table: Vec<R8> = (0..M8)
            .map(|i| {
                let k = keys[i].0;
                R8 {
                    a: k[0].unwrap(),
                    b: k[5].unwrap(),
                    c: k[1],
                    d: k[2],
                    e: k[3],
                    n1: k[4].unwrap().parse::<i32>().unwrap(),
                    n2: (i % 97) as i32,
                    n3: (i % 13) as i32,
                }
            })
            .collect();
        #[inline(always)]
        fn keep(r: &R8) -> bool {
            r.c.is_some()
                && r.d.is_some()
                && (r.b == "qsNo" || r.b == "qsMay" || r.b == "qsPea")
                && r.n1 >= 0
                && r.a != "space"
        }
        let mut matched = 0u64;
        let mut h = FNV_OFFSET;
        for r in table.iter() {
            if keep(r) {
                matched += 1;
                for v in [Some(r.a), Some(r.b), r.c, r.d, r.e] {
                    h = mix(h, v.map_or(0, |s| sym_id[s]));
                }
                h = mix(h, r.n1 as i64 as u64);
                h = mix(h, r.n2 as i64 as u64);
                h = mix(h, r.n3 as i64 as u64);
            }
        }
        let t = &table;
        let run = || {
            let mut c = 0u64;
            let mut acc = 0u64;
            for r in t.iter() {
                if keep(black_box(r)) {
                    c += 1;
                    acc ^= r.n2 as u64;
                }
            }
            (c << 8) ^ acc
        };
        let mut ctl = || {
            let mut c = 0u64;
            for r in t.iter() {
                black_box(r);
                c += 1;
            }
            c << 8
        };
        bench(
            "filter700k/struct-vec",
            M8,
            MED_REPS,
            run,
            Some(&mut ctl),
            ex(&[("matched", matched.to_string()), ("checksum", h.to_string())]),
        );
    }

    // ----------------------------------------------------------------- B4 map
    eprintln!("B4 map with a 10-slot optional-string key");
    {
        let miss: S = Box::leak("MISS".to_string().into_boxed_str());
        let ks = &keys;
        let run = || {
            let mut m: HashMap<Key10, u32> = HashMap::new();
            for (i, k) in ks.iter().enumerate() {
                m.insert(*k, i as u32);
            }
            black_box(&m);
            m.len() as u64
        };
        bench("map10str/insert-siphash", n, HEAVY_REPS, run, no_ctl(), vec![]);
        let run = || {
            let mut m: HashMap<Key10, u32, FxBuild> = HashMap::default();
            for (i, k) in ks.iter().enumerate() {
                m.insert(*k, i as u32);
            }
            black_box(&m);
            m.len() as u64
        };
        bench("map10str/insert-fx", n, HEAVY_REPS, run, no_ctl(), vec![]);
        let run = || {
            let mut m: HashMap<Key10, u32, FxBuild> =
                HashMap::with_capacity_and_hasher(n, FxBuild::default());
            for (i, k) in ks.iter().enumerate() {
                m.insert(*k, i as u32);
            }
            black_box(&m);
            m.len() as u64
        };
        bench("map10str/insert-fx-presized", n, HEAVY_REPS, run, no_ctl(), vec![]);

        let mut probes: Vec<Key10> = Vec::with_capacity(NPROBE);
        for p in 0..NPROBE {
            let mut k = keys[((p as u64 * PROBE_MUL) % n as u64) as usize];
            if p % 4 == 3 {
                k.0[9] = Some(miss);
            }
            probes.push(k);
        }
        let pr = &probes;
        let mut sipmap: HashMap<Key10, u32> = HashMap::new();
        let mut fxmap: HashMap<Key10, u32, FxBuild> = HashMap::default();
        for (i, k) in keys.iter().enumerate() {
            sipmap.insert(*k, i as u32);
            fxmap.insert(*k, i as u32);
        }
        let mut hits = 0u64;
        let mut sum = 0u64;
        for k in probes.iter() {
            if let Some(v) = sipmap.get(k) {
                hits += 1;
                sum = sum.wrapping_add(*v as u64);
            }
        }
        macro_rules! lookup_bench {
            ($name:expr, $m:expr) => {{
                let run = || {
                    let mut acc = 0u64;
                    let mut h = 0u64;
                    for k in pr.iter() {
                        if let Some(v) = $m.get(black_box(k)) {
                            h += 1;
                            acc = acc.wrapping_add(*v as u64);
                        }
                    }
                    (acc << 1) ^ h
                };
                let mut ctl = || {
                    let mut acc = 0u64;
                    for k in pr.iter() {
                        black_box(k);
                        acc ^= 1;
                    }
                    acc
                };
                bench(
                    $name,
                    NPROBE,
                    MED_REPS,
                    run,
                    Some(&mut ctl),
                    ex(&[("hits", hits.to_string()), ("checksum", sum.to_string())]),
                );
            }};
        }
        lookup_bench!("map10str/lookup-siphash", sipmap);
        lookup_bench!("map10str/lookup-fx", fxmap);

        let pk = &packed;
        let run = || {
            let mut m: HashMap<u64, u32> = HashMap::new();
            for (i, k) in pk.iter().enumerate() {
                m.insert(*k, i as u32);
            }
            black_box(&m);
            m.len() as u64
        };
        bench("mapU64/insert-siphash", n, HEAVY_REPS, run, no_ctl(), vec![]);
        let run = || {
            let mut m: HashMap<u64, u32, FxBuild> = HashMap::default();
            for (i, k) in pk.iter().enumerate() {
                m.insert(*k, i as u32);
            }
            black_box(&m);
            m.len() as u64
        };
        bench("mapU64/insert-fx", n, HEAVY_REPS, run, no_ctl(), vec![]);
        let run = || {
            let mut m: HashMap<u64, u32, FxBuild> =
                HashMap::with_capacity_and_hasher(n, FxBuild::default());
            for (i, k) in pk.iter().enumerate() {
                m.insert(*k, i as u32);
            }
            black_box(&m);
            m.len() as u64
        };
        bench("mapU64/insert-fx-presized", n, HEAVY_REPS, run, no_ctl(), vec![]);

        let mut probesp: Vec<u64> = Vec::with_capacity(NPROBE);
        for p in 0..NPROBE {
            let mut k = packed[((p as u64 * PROBE_MUL) % n as u64) as usize];
            if p % 4 == 3 {
                k = (k & !(31u64 << 45)) | (31u64 << 45);
            }
            probesp.push(k);
        }
        let prp = &probesp;
        let mut sipu: HashMap<u64, u32> = HashMap::new();
        let mut fxu: HashMap<u64, u32, FxBuild> = HashMap::default();
        for (i, k) in packed.iter().enumerate() {
            sipu.insert(*k, i as u32);
            fxu.insert(*k, i as u32);
        }
        let mut hitsp = 0u64;
        let mut sump = 0u64;
        for k in probesp.iter() {
            if let Some(v) = sipu.get(k) {
                hitsp += 1;
                sump = sump.wrapping_add(*v as u64);
            }
        }
        macro_rules! lookup_bench_u {
            ($name:expr, $m:expr) => {{
                let run = || {
                    let mut acc = 0u64;
                    let mut h = 0u64;
                    for k in prp.iter() {
                        if let Some(v) = $m.get(black_box(k)) {
                            h += 1;
                            acc = acc.wrapping_add(*v as u64);
                        }
                    }
                    (acc << 1) ^ h
                };
                let mut ctl = || {
                    let mut acc = 0u64;
                    for k in prp.iter() {
                        black_box(k);
                        acc ^= 1;
                    }
                    acc
                };
                bench(
                    $name,
                    NPROBE,
                    MED_REPS,
                    run,
                    Some(&mut ctl),
                    ex(&[("hits", hitsp.to_string()), ("checksum", sump.to_string())]),
                );
            }};
        }
        lookup_bench_u!("mapU64/lookup-siphash", sipu);
        lookup_bench_u!("mapU64/lookup-fx", fxu);
    }

    // --------------------------------------------------------------- B7 alloc
    eprintln!("B7 allocate and drop 10M small objects");
    {
        let f0 = fields[0];
        let mut boxes: Vec<Option<Box<R8>>> = (0..8).map(|_| None).collect();
        let run = || {
            let mut acc = 0u64;
            for i in 0..NALLOC {
                let n2 = (i % 97) as i32;
                boxes[i & 7] = Some(Box::new(R8 { n2, n3: (i % 13) as i32, ..f0 }));
                acc ^= n2 as u64;
            }
            black_box(&boxes);
            acc
        };
        let mut sink: Vec<R8> = vec![f0; 8];
        let mut ctl = || {
            let mut acc = 0u64;
            for i in 0..NALLOC {
                let n2 = (i % 97) as i32;
                sink[i & 7].n2 = black_box(n2);
                acc ^= n2 as u64;
            }
            black_box(&sink);
            acc
        };
        bench("alloc10M/box-heap", NALLOC, HEAVY_REPS, run, Some(&mut ctl), vec![]);

        let mut slots: Vec<R8> = vec![f0; 8];
        let run = || {
            let mut acc = 0u64;
            for i in 0..NALLOC {
                let n2 = (i % 97) as i32;
                slots[i & 7] = black_box(R8 { n2, n3: (i % 13) as i32, ..f0 });
                acc ^= n2 as u64;
            }
            black_box(&slots);
            acc
        };
        let mut sink2: Vec<R8> = vec![f0; 8];
        let mut ctl = || {
            let mut acc = 0u64;
            for i in 0..NALLOC {
                let n2 = (i % 97) as i32;
                sink2[i & 7].n2 = black_box(n2);
                acc ^= n2 as u64;
            }
            black_box(&sink2);
            acc
        };
        bench("alloc10M/by-value-no-alloc", NALLOC, HEAVY_REPS, run, Some(&mut ctl), vec![]);

        let run = || {
            let mut arena: Vec<R8> = Vec::with_capacity(NALLOC);
            let mut acc = 0u64;
            for i in 0..NALLOC {
                let n2 = (i % 97) as i32;
                arena.push(R8 { n2, n3: (i % 13) as i32, ..f0 });
                acc ^= n2 as u64;
            }
            black_box(&arena);
            acc
        };
        bench("alloc10M/arena-push", NALLOC, HEAVY_REPS, run, no_ctl(), vec![]);
    }

    // ----------------------------------------------------------------- output
    let rows = unsafe { &*std::ptr::addr_of!(ROWS) };
    let mut s = String::from("{\n \"lang\": \"rust\",\n");
    s.push_str(&format!(" \"runtime\": \"{}\",\n", env!("CARGO_PKG_VERSION")));
    s.push_str(&format!(" \"n_keys\": {n},\n \"results\": [\n"));
    for (i, r) in rows.iter().enumerate() {
        s.push_str(&format!(
            "  {{\"op\": \"{}\", \"lang\": \"rust\", \"ops\": {}, \"raw_ns_per_op\": {:.4}, \"min_ns_per_op\": {:.4}, \"max_ns_per_op\": {:.4}, \"spread_pct\": {:.2}, \"control_ns_per_op\": {:.4}, \"net_ns_per_op\": {:.4}, \"reps\": {}, \"acc\": \"{}\"",
            r.op, r.ops, r.raw, r.minv, r.maxv, r.spread, r.control, r.net, r.reps, r.acc
        ));
        for (k, v) in r.extra.iter() {
            s.push_str(&format!(", \"{k}\": \"{v}\""));
        }
        s.push('}');
        if i + 1 < rows.len() {
            s.push(',');
        }
        s.push('\n');
    }
    s.push_str(" ]\n}\n");
    fs::write(&out, s).unwrap();
    eprintln!("rust bench written to {out}");
}
