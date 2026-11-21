//! CPython's `random.Random` as `build._verification_sample` calls it, draw for draw: seeded with a string at version 2 (`Random.seed`: the string's UTF-8 bytes followed by their sha512, read as one big-endian integer whose 32-bit words, least significant first, go to MT19937's `init_by_array`), with `getrandbits`, `_randbelow_with_getrandbits` and both branches of `sample`, whose choice between them turns on `setsize` from `math.log` as CPython computes it (`rebuild/CORPUS-PARENT-PORT-PLAN.md` § Byte-identity hazards).
//!
//! [`stage_sample`] is the `sample` stage of the stage fixtures.

use std::collections::HashSet;
use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;

use serde_json::{Value, json};
use sha2::{Digest as _, Sha512};

use crate::wire::{Result, WireError, error};

/// The units `build._verification_sample` draws at most (`build.VERIFICATION_SAMPLE`).
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

/// `build._verification_sample`: the id words of the cached units the build recomputes and compares, drawn from `cached`, ascending, by `random.Random(seed).sample` at `min(size, len(cached))`, and none from none.
pub fn verification_sample(cached: &[u64], seed: &str, size: usize) -> Vec<u64> {
    if cached.is_empty() {
        return Vec::new();
    }
    Random::seeded(seed)
        .sample(cached, size.min(cached.len()))
        .expect("the sample is no larger than the population")
}

fn typed_items<'a>(value: &'a Value, key: &str) -> Result<&'a [Value]> {
    match value
        .as_object()
        .filter(|object| object.len() == 1)
        .and_then(|object| object.get("tuple").or_else(|| object.get("list")))
        .and_then(Value::as_array)
    {
        Some(items) => Ok(items),
        None => error(format!("{key}: not a typed tuple or list: {value:.200}")),
    }
}

fn word(value: &Value, key: &str) -> Result<u64> {
    match value.as_u64() {
        Some(word) => Ok(word),
        None => error(format!("{key}: not an unsigned 64-bit int: {value:.200}")),
    }
}

fn words(value: &Value, key: &str) -> Result<Vec<u64>> {
    typed_items(value, key)?
        .iter()
        .map(|item| word(item, key))
        .collect()
}

fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str> {
    match value.as_str() {
        Some(text) => Ok(text),
        None => error(format!("{key}: not a str: {value:.200}")),
    }
}

fn arguments<'a, const COUNT: usize>(value: &'a Value, key: &str) -> Result<&'a [Value; COUNT]> {
    match typed_items(value, key)?.try_into() {
        Ok(items) => Ok(items),
        Err(_) => error(format!("{key}: the input is not {COUNT} arguments")),
    }
}

fn listed(values: Vec<u64>) -> Value {
    json!({ "list": values })
}

/// One stage line's answer in the typed encoding.
fn answer(record: &Value, key: &str) -> Result<Value> {
    let input = &record["input"];
    match record["form"].as_str() {
        Some("verification") => {
            let [cached, seed, size] = arguments(input, key)?;
            let size = usize::try_from(word(size, key)?)
                .map_err(|_| WireError(format!("{key}: the size is out of range")))?;
            Ok(listed(verification_sample(
                &words(cached, key)?,
                text(seed, key)?,
                size,
            )))
        }
        Some("sample") => {
            let [seed, population, k] = arguments(input, key)?;
            let seed = text(seed, key)?;
            let population = words(population, key)?;
            let Some(k) = k.as_i64() else {
                return error(format!("{key}: k is not an int: {k:.200}"));
            };
            let drawn = usize::try_from(k)
                .ok()
                .and_then(|k| Random::seeded(seed).sample(&population, k));
            Ok(drawn.map_or_else(|| json!({ "tuple": ["refused"] }), listed))
        }
        Some("getrandbits") => {
            let [seed, widths] = arguments(input, key)?;
            let mut random = Random::seeded(text(seed, key)?);
            let mut draws = Vec::new();
            for width in words(widths, key)? {
                match u32::try_from(width) {
                    Ok(width) if width <= 64 => draws.push(random.getrandbits(width)),
                    _ => return error(format!("{key}: no draw of {width} bits")),
                }
            }
            Ok(listed(draws))
        }
        Some("randbelow") => {
            let [seed, bounds] = arguments(input, key)?;
            let mut random = Random::seeded(text(seed, key)?);
            let mut draws = Vec::new();
            for bound in words(bounds, key)? {
                if bound == 0 {
                    return error(format!("{key}: randbelow takes a positive bound"));
                }
                draws.push(random.randbelow(bound));
            }
            Ok(listed(draws))
        }
        other => error(format!("{key}: no sample form {other:?}")),
    }
}

/// The `sample` stage: for each line of `stage_file`, answer its `input` by its `form` in one `{"key", "value"}` line, in the typed encoding: `verification` draws `build._verification_sample(cached, seed, size)` ([`verification_sample`]); `sample` draws `random.Random(seed).sample(population, k)`, or `("refused",)` where that raises; `getrandbits` and `randbelow` make one draw a width or a bound in order from `random.Random(seed)`. Every value is a list of unsigned 64-bit ints, which the stage reads and writes as JSON numbers rather than as `PyValue`'s 64-bit signed ints.
pub fn stage_sample(stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let read = |failure: std::io::Error| {
        WireError(format!("cannot read {}: {failure}", stage_file.display()))
    };
    for line in BufReader::new(File::open(stage_file).map_err(read)?).lines() {
        let line = line.map_err(read)?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| WireError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return error(format!("a stage line has no key: {line:.200}"));
        };
        let value = answer(&record, key)?;
        writeln!(out, "{}", json!({ "key": key, "value": value }))
            .map_err(|failure| WireError(format!("cannot write a stage line: {failure}")))?;
    }
    Ok(())
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
