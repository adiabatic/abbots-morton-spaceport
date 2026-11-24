//! CPython's `random.Random`, draw for draw, as the verification sample draws from it ([`verification_sample`]): seeded with a string at version 2 (`Random.seed`: the string's UTF-8 bytes followed by their sha512, read as one big-endian integer whose 32-bit words, least significant first, go to MT19937's `init_by_array`), with `getrandbits`, `_randbelow_with_getrandbits` and both branches of `sample`, whose choice between them turns on `setsize` from `math.log` as CPython computes it ([`setsize`]), so the sample is the one CPython draws from the same seed.

use std::collections::HashSet;

use sha2::{Digest as _, Sha512};

/// The units the verification sample draws at most (`build.VERIFICATION_SAMPLE`).
pub const VERIFICATION_SAMPLE: usize = 200;

const N: usize = 624;
const M: usize = 397;
const MATRIX_A: u32 = 0x9908_b0df;
const UPPER_MASK: u32 = 0x8000_0000;
const LOWER_MASK: u32 = 0x7fff_ffff;

/// CPython's Mersenne Twister, `_random.Random`, and the `random.Random` methods over it that the verification sample reaches.
pub struct Random {
    state: [u32; N],
    index: usize,
}

impl Random {
    /// `random.Random(seed)` for a `str` seed, at the default version 2.
    pub fn seeded(seed: &str) -> Self {
        let mut bytes = seed.as_bytes().to_vec();
        bytes.extend_from_slice(&Sha512::digest(seed.as_bytes()));
        let start = bytes
            .iter()
            .position(|&byte| byte != 0)
            .unwrap_or(bytes.len());
        let significant = &bytes[start..];
        let mut key: Vec<u32> = significant
            .rchunks(4)
            .map(|chunk| {
                chunk
                    .iter()
                    .fold(0u32, |word, &byte| (word << 8) | u32::from(byte))
            })
            .collect();
        if key.is_empty() {
            key.push(0);
        }
        Self::by_array(&key)
    }

    fn by_array(key: &[u32]) -> Self {
        let mut state = [0u32; N];
        state[0] = 19_650_218;
        for i in 1..N {
            state[i] = 1_812_433_253u32
                .wrapping_mul(state[i - 1] ^ (state[i - 1] >> 30))
                .wrapping_add(i as u32);
        }
        let (mut i, mut j) = (1usize, 0usize);
        for _ in 0..N.max(key.len()) {
            state[i] = (state[i] ^ (state[i - 1] ^ (state[i - 1] >> 30)).wrapping_mul(1_664_525))
                .wrapping_add(key[j])
                .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= N {
                state[0] = state[N - 1];
                i = 1;
            }
            if j >= key.len() {
                j = 0;
            }
        }
        for _ in 0..N - 1 {
            state[i] = (state[i]
                ^ (state[i - 1] ^ (state[i - 1] >> 30)).wrapping_mul(1_566_083_941))
            .wrapping_sub(i as u32);
            i += 1;
            if i >= N {
                state[0] = state[N - 1];
                i = 1;
            }
        }
        state[0] = 0x8000_0000;
        Self { state, index: N }
    }

    fn next_u32(&mut self) -> u32 {
        if self.index >= N {
            let mt = &mut self.state;
            let twist = |y: u32| (y >> 1) ^ if y & 1 == 1 { MATRIX_A } else { 0 };
            for kk in 0..N - M {
                let y = (mt[kk] & UPPER_MASK) | (mt[kk + 1] & LOWER_MASK);
                mt[kk] = mt[kk + M] ^ twist(y);
            }
            for kk in N - M..N - 1 {
                let y = (mt[kk] & UPPER_MASK) | (mt[kk + 1] & LOWER_MASK);
                mt[kk] = mt[kk + M - N] ^ twist(y);
            }
            let y = (mt[N - 1] & UPPER_MASK) | (mt[0] & LOWER_MASK);
            mt[N - 1] = mt[M - 1] ^ twist(y);
            self.index = 0;
        }
        let mut y = self.state[self.index];
        self.index += 1;
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c_5680;
        y ^= (y << 15) & 0xefc6_0000;
        y ^ (y >> 18)
    }

    /// `Random.getrandbits(k)` for `k` up to 64: one 32-bit draw for 32 bits or fewer, its low bits dropped, and otherwise one draw a 32-bit word from the least significant, the last word's low bits dropped.
    pub fn getrandbits(&mut self, k: u32) -> u64 {
        assert!(k <= 64, "getrandbits takes at most 64 bits here, not {k}");
        if k == 0 {
            return 0;
        }
        if k <= 32 {
            return u64::from(self.next_u32() >> (32 - k));
        }
        let low = u64::from(self.next_u32());
        let high = u64::from(self.next_u32() >> (64 - k));
        low | (high << 32)
    }

    /// `Random._randbelow_with_getrandbits(n)`, an int in `[0, n)` for `n` above 0: draws of `n`'s bit length until one falls below `n`.
    pub fn randbelow(&mut self, n: u64) -> u64 {
        assert!(n > 0, "randbelow takes a positive bound");
        let k = u64::BITS - n.leading_zeros();
        loop {
            let r = self.getrandbits(k);
            if r < n {
                return r;
            }
        }
    }

    /// `Random.sample(population, k)` without `counts`: None where it raises ValueError, a `k` past the population. A population no larger than [`setsize`] is drawn from a pool whose vacancies the last unselected item fills; a larger one by redrawing an index already selected.
    pub fn sample<T: Clone>(&mut self, population: &[T], k: usize) -> Option<Vec<T>> {
        let n = population.len();
        if k > n {
            return None;
        }
        let mut result = Vec::with_capacity(k);
        if n as u128 <= setsize(k) {
            let mut pool = population.to_vec();
            for i in 0..k {
                let j = self.randbelow((n - i) as u64) as usize;
                result.push(pool[j].clone());
                pool[j] = pool[n - i - 1].clone();
            }
        } else {
            let mut selected = HashSet::with_capacity(k);
            for _ in 0..k {
                let mut j = self.randbelow(n as u64) as usize;
                while !selected.insert(j) {
                    j = self.randbelow(n as u64) as usize;
                }
                result.push(population[j].clone());
            }
        }
        Some(result)
    }
}

/// `Random.sample`'s `setsize` for `k` selections: 21, and for `k` above 5 also `4 ** ceil(log(k * 3, 4))`, the logarithm as `math.log` with a base computes it, `log(3k) / log(4)` in the C library's `log`.
pub fn setsize(k: usize) -> u128 {
    if k <= 5 {
        return 21;
    }
    let exponent = ((k as u128 * 3) as f64).ln() / 4f64.ln();
    21 + 4u128.pow(exponent.ceil() as u32)
}

/// The id words of the cached units the build recomputes and compares, drawn from `cached`, ascending, by `random.Random(seed).sample` at `min(size, len(cached))`, and none from none.
pub fn verification_sample(cached: &[u64], seed: &str, size: usize) -> Vec<u64> {
    if cached.is_empty() {
        return Vec::new();
    }
    Random::seeded(seed)
        .sample(cached, size.min(cached.len()))
        .expect("the sample is no larger than the population")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn draws_what_cpython_draws_for_a_string_seed() {
        let mut random = Random::seeded("a");
        let draws: Vec<u64> = [1, 32, 33, 64, 0, 7]
            .into_iter()
            .map(|k| random.getrandbits(k))
            .collect();
        assert_eq!(
            draws,
            [
                0,
                2_173_948_116,
                4_584_175_934,
                12_637_388_259_657_513_179,
                0,
                82
            ]
        );
        let mut random = Random::seeded("");
        let draws: Vec<u64> = [1, 2, 3, 1000, (1 << 32) + 1, u64::MAX]
            .into_iter()
            .map(|n| random.randbelow(n))
            .collect();
        assert_eq!(
            draws,
            [0, 0, 1, 885, 2_677_076_721, 11_093_857_099_016_460_722]
        );
        assert_eq!(
            Random::seeded(&"seed ".repeat(600)).getrandbits(32),
            669_333_542
        );
    }

    #[test]
    fn samples_what_cpython_samples_from_a_pool_and_from_a_set() {
        let ten: Vec<u64> = (0..10).collect();
        assert_eq!(
            Random::seeded("seed").sample(&ten, 4),
            Some(vec![2, 0, 3, 6])
        );
        let hundred: Vec<u64> = (100..200).collect();
        assert_eq!(
            Random::seeded("seed").sample(&hundred, 3),
            Some(vec![117, 107, 130])
        );
        let thousand: Vec<u64> = (0..1000).collect();
        assert_eq!(
            Random::seeded("\0").sample(&thousand, 10),
            Some(vec![779, 782, 461, 717, 532, 951, 891, 114, 948, 119])
        );
        assert_eq!(Random::seeded("seed").sample(&ten, 11), None);
        assert_eq!(Random::seeded("seed").sample(&ten, 0), Some(vec![]));
    }

    #[test]
    fn the_set_size_steps_where_three_selections_pass_a_power_of_four() {
        assert_eq!(setsize(5), 21);
        assert_eq!(setsize(6), 85);
        assert_eq!(setsize(21), 85);
        assert_eq!(setsize(22), 277);
        assert_eq!(setsize(VERIFICATION_SAMPLE), 1045);
    }

    #[test]
    fn the_verification_sample_draws_at_most_its_size_and_nothing_from_nothing() {
        assert!(verification_sample(&[], "seed", VERIFICATION_SAMPLE).is_empty());
        let few: Vec<u64> = (0..7).map(|index| u64::MAX - index).collect();
        let mut drawn = verification_sample(&few, "seed", VERIFICATION_SAMPLE);
        drawn.sort_unstable();
        assert_eq!(drawn, few.iter().rev().copied().collect::<Vec<_>>());
    }
}
