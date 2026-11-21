//! The orders a corpus build puts its unit table's rows in, as `rebuild/review/audit.py` computes them. [`family_ranks`] maps each family to its code point, [`triage_key`] is the order a corpus pages its human units in, and [`counting_permutation`] and [`counting_permutation_by_id`] place a table's rows in that order with a counting sort over (class, group) buckets walked in window order, the permutation [`triage_permutation`] gives by sorting one key a row. [`merge_members`] merges a window's ink-duplicate units as `audit._merge_members` does, over runs of rows [`merged_rows`] merges.
//!
//! [`UnitTable`] holds the workload as columns over the unit's ordinal, as `audit.UnitTable` does, and [`stream_workload`] builds it from the held audit a window at a time, as `audit.stream_workload` does: each window's units classed, flagged and pooled, its ink duplicates merged through the stream's digests, each unit's input key taken over its run's lines, and the merged units placed in load order beside the pre-merge units' columns and the compaction between them.
//!
//! [`stage_permutation`] is the `permutation` stage of the stage fixtures, [`stage_merge`] the `merge` stage, and [`stage_workload`] the `workload` stage.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;

use ams_m1_kernel::emit::json_string;
use serde_json::{Value, json};
use sha2::{Digest as _, Sha256};

use super::audit::{Section, Vocabulary, audit_layout, stage_line};
use super::columns::{
    MappingPool, Sealed, StringTable, TuplePool, WindowIndex, WindowKeys, window_word,
};
use super::input::{BuildInput, LedgerEntry};
use super::signatures::{
    HeldWindows, InkShaping, StoreNames, dump_store, find_misses, key_only_count,
};
use super::stream::{
    FamilyLines, Lockstep, StreamError, WindowRows, input_key, parse_window, signature_key,
};
use crate::change_groups::UNMATCHED;
use crate::pyfmt::PyValue;
use crate::pyfmt::repr::repr_str;
use crate::wire::{Result, WireError, error};

/// The rank `audit.triage_key` gives a family the ranks do not name (`10**6`).
pub const UNRANKED_FAMILY: i64 = 1_000_000;

/// Each family's code point, in the order `audit.family_ranks`' dict holds them: by each family's first appearance, with its last code point.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct FamilyRanks {
    entries: Vec<(String, i64)>,
    index: HashMap<String, usize>,
}

impl FamilyRanks {
    /// The ranks a dict built from `entries` in order holds: a repeated family keeps its first place and takes its last rank.
    pub fn from_entries(entries: impl IntoIterator<Item = (String, i64)>) -> Self {
        let mut ranks = Self::default();
        for (name, rank) in entries {
            match ranks.index.get(&name) {
                Some(&at) => ranks.entries[at].1 = rank,
                None => {
                    ranks.index.insert(name.clone(), ranks.entries.len());
                    ranks.entries.push((name, rank));
                }
            }
        }
        ranks
    }

    pub fn get(&self, name: &str) -> Option<i64> {
        self.index.get(name).map(|&at| self.entries[at].1)
    }

    pub fn entries(&self) -> &[(String, i64)] {
        &self.entries
    }

    /// A group's families' ranks, [`UNRANKED_FAMILY`] for a family the ranks do not name: the term `triage_key` orders a group by.
    pub fn group(&self, group: &str) -> Vec<i64> {
        group
            .split(':')
            .map(|name| self.get(name).unwrap_or(UNRANKED_FAMILY))
            .collect()
    }
}

/// Each family's code point over `family_of`'s `(code point, family)` entries in order (`audit.family_ranks`).
pub fn family_ranks(family_of: &[(i64, String)]) -> FamilyRanks {
    FamilyRanks::from_entries(family_of.iter().map(|(value, name)| (name.clone(), *value)))
}

/// `audit.triage_key`'s tuple, ordered as Python orders it: the class's index, the group's families' ranks, the window's length and code points, and the unit's id.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct TriageKey {
    pub class_index: usize,
    pub families: Vec<i64>,
    pub length: usize,
    pub codepoints: Vec<u32>,
    pub unit_id: String,
}

/// The key the corpus pages its human units by (`audit.triage_key`).
pub fn triage_key(
    class_index: usize,
    group: &str,
    codepoints: &[u32],
    unit_id: &str,
    family_rank: &FamilyRanks,
) -> TriageKey {
    TriageKey {
        class_index,
        families: family_rank.group(group),
        length: codepoints.len(),
        codepoints: codepoints.to_vec(),
        unit_id: unit_id.to_owned(),
    }
}

/// What a row's (class, group) bucket is read from, the first two terms of `audit.triage_key`: each row's class and group ids in the string table, the classes' order, and the families' ranks.
pub struct Buckets<'a> {
    pub class_col: &'a [u32],
    pub group_col: &'a [u32],
    pub strings: &'a StringTable,
    pub class_order: &'a HashMap<String, usize>,
    pub family_rank: &'a FamilyRanks,
}

/// Each class id's rank, its class's place in the order or one past the last for a class the order does not name, and each group id's rank among the distinct family-rank tuples of the groups, equal tuples sharing a rank; with the count of those tuples. Every id in the columns counts, as Python's `set` over each column does.
struct Terms {
    class_rank: HashMap<u32, usize>,
    group_rank: HashMap<u32, usize>,
    groups: usize,
}

impl Terms {
    fn new(buckets: &Buckets<'_>) -> Self {
        let unranked = buckets.class_order.len();
        let class_rank = buckets
            .class_col
            .iter()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .map(|&id| {
                let rank = buckets.class_order.get(buckets.strings.get(id)).copied();
                (id, rank.unwrap_or(unranked))
            })
            .collect();
        let group_tuples: Vec<(u32, Vec<i64>)> = buckets
            .group_col
            .iter()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .map(|&id| (id, buckets.family_rank.group(buckets.strings.get(id))))
            .collect();
        let ranked: Vec<&Vec<i64>> = group_tuples
            .iter()
            .map(|(_id, ranks)| ranks)
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        let group_rank = group_tuples
            .iter()
            .map(|(id, ranks)| (*id, ranked.binary_search(&ranks).unwrap_or_default()))
            .collect();
        Self {
            class_rank,
            group_rank,
            groups: ranked.len(),
        }
    }
}

/// The rows' buckets and each bucket's next free place, `_counting_permutation`'s first pass.
struct Placer {
    bucket: Vec<usize>,
    fill: Vec<usize>,
}

impl Placer {
    fn new(buckets: &Buckets<'_>, n: usize) -> Self {
        let terms = Terms::new(buckets);
        let width = terms.groups.max(1);
        let numbers = (terms.class_rank.values().copied().max().unwrap_or(0) + 1) * width;
        let mut bucket = vec![0; n];
        let mut counts = vec![0usize; numbers];
        for (ordinal, number) in bucket.iter_mut().enumerate() {
            *number = terms.class_rank[&buckets.class_col[ordinal]] * width
                + terms.group_rank[&buckets.group_col[ordinal]];
            counts[*number] += 1;
        }
        let mut fill = Vec::with_capacity(numbers + 1);
        fill.push(0);
        for count in counts {
            fill.push(fill[fill.len() - 1] + count);
        }
        Self { bucket, fill }
    }

    /// Place `ordinal` at its bucket's next free place in `order`, and return its bucket and that place.
    fn place(&mut self, ordinal: u32, order: &mut [u32]) -> (usize, usize) {
        let number = self.bucket[ordinal as usize];
        let at = self.fill[number];
        order[at] = ordinal;
        self.fill[number] = at + 1;
        (number, at)
    }
}

/// `items[start:end]` as Python slices a sequence, each bound clamped to its length and an end before the start giving nothing.
fn py_slice<T>(items: &[T], start: usize, end: usize) -> &[T] {
    let start = start.min(items.len());
    &items[start..end.clamp(start, items.len())]
}

/// `n` rows in `triage_key` order without a comparison sort (`audit._counting_permutation` with no runs or no id word): each row's (class, group) bucket counted in one pass, then the rows placed into their buckets' slices in the order `walk` gives, which must be window order with the rows of one window in ascending order, so a tie keeps `walk`'s order.
pub fn counting_permutation(buckets: &Buckets<'_>, walk: &[u32], n: usize) -> Vec<u32> {
    let mut placer = Placer::new(buckets, n);
    let mut order = vec![0; n];
    for &ordinal in walk {
        placer.place(ordinal, &mut order);
    }
    order
}

/// [`counting_permutation`] with the ties of one window broken by id word (`audit._counting_permutation` with `runs` and `id_word`): `runs` gives the stretches of `walk` that share a window (`WindowIndex::runs`), and the rows of each that land in one bucket, which sit side by side there, are sorted by `id_word`, keeping ascending order among equal words.
pub fn counting_permutation_by_id(
    buckets: &Buckets<'_>,
    walk: &[u32],
    n: usize,
    runs: impl IntoIterator<Item = (usize, usize)>,
    id_word: impl Fn(u32) -> u64,
) -> Vec<u32> {
    let mut placer = Placer::new(buckets, n);
    let mut order = vec![0; n];
    let mut position = 0;
    let last = (walk.len(), walk.len());
    for (start, end) in runs.into_iter().chain(std::iter::once(last)) {
        for &ordinal in py_slice(walk, position, start) {
            placer.place(ordinal, &mut order);
        }
        let mut placed: Vec<(usize, Vec<usize>)> = Vec::new();
        for &ordinal in py_slice(walk, start, end) {
            let (number, at) = placer.place(ordinal, &mut order);
            match placed.iter_mut().find(|(held, _spots)| *held == number) {
                Some((_number, spots)) => spots.push(at),
                None => placed.push((number, vec![at])),
            }
        }
        for (_number, spots) in placed {
            if let [low, .., high] = spots[..] {
                order[low..=high].sort_by_key(|&ordinal| id_word(ordinal));
            }
        }
        position = end;
    }
    order
}

/// A loaded table's window columns, which `triage_permutation` orders by: each row's window length, its start in `values`, and the windows' code points.
pub struct Windows<'a> {
    pub counts: &'a [u8],
    pub starts: &'a [u32],
    pub values: &'a [u16],
}

/// The table's `n` rows in `triage_key` order by one sort over every row (`audit._triage_permutation`): by class rank, group rank, window length, window code points, the id word when `id_word` is given, and ordinal. Python packs those terms into one integer a row, each field wide enough for its largest value, so its order is this order of the terms.
pub fn triage_permutation(
    buckets: &Buckets<'_>,
    windows: &Windows<'_>,
    n: usize,
    id_word: Option<&dyn Fn(u32) -> u64>,
) -> Vec<u32> {
    let terms = Terms::new(buckets);
    let words: Vec<u64> = match id_word {
        Some(id_word) => (0..n).map(|ordinal| id_word(ordinal as u32)).collect(),
        None => Vec::new(),
    };
    let window = |ordinal: usize| {
        let start = windows.starts[ordinal] as usize;
        &windows.values[start..start + usize::from(windows.counts[ordinal])]
    };
    let mut order: Vec<u32> = (0..n as u32).collect();
    order.sort_by(|&left, &right| {
        let key = |ordinal: u32| {
            let at = ordinal as usize;
            (
                terms.class_rank[&buckets.class_col[at]],
                terms.group_rank[&buckets.group_col[at]],
                windows.counts[at],
                window(at),
                words.get(at).copied().unwrap_or(0),
            )
        };
        key(left).cmp(&key(right)).then(left.cmp(&right))
    });
    order
}

/// One sibling unit of a window as the ink-duplicate merge reads and widens it (`audit._Member`): its place in its window (or its row in the table), its config rank, its configs, its kinds and its per-config classes in their order, its exemplar flag, its class and exemption, whether it absorbed a sibling, and the ordinal of the member that absorbed it (Python's -1 as `None`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Member {
    pub ordinal: usize,
    pub rank: i64,
    pub configs: Vec<String>,
    pub kinds: Vec<String>,
    pub config_classes: Vec<(String, String)>,
    pub exemplar: bool,
    pub class_id: String,
    pub no_verdict: bool,
    pub merged: bool,
    pub survivor: Option<usize>,
}

/// The ink-duplicate merge's counts, the stats dict `audit.stream_workload` starts in this order.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct MergeStats {
    pub windows_merged: i64,
    pub units_merged: i64,
    pub kept_split_matched_classes: i64,
}

impl MergeStats {
    /// The stats as Python holds them, a dict of the counts in their order.
    pub fn to_py(&self) -> PyValue {
        let counts = [
            self.windows_merged,
            self.units_merged,
            self.kept_split_matched_classes,
        ];
        PyValue::Dict(
            MERGE_STATS
                .iter()
                .zip(counts)
                .map(|(name, count)| (PyValue::str(*name), PyValue::Int(count)))
                .collect(),
        )
    }
}

/// What `audit._merge_members` raises: `IndexError` for no members, and `KeyError` from `set.pop` when an absorbed member leaves the survivor no class, which only members with no config classes reach.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MergeError {
    NoMembers,
    NoClass,
}

/// Merge one window's members whose every config gives one ink signature (`audit._merge_members`): the survivor is the member of the lowest config rank, the first of them in `members`' order (a stable sort, which `members` is left in), and each later member is absorbed unless the two members' per-config classes name two matched ledger classes, which is counted. `absorb(survivor, other)` merges the two members' runs of rows, the survivor's first at an equal rank, and gives the merged run's configs; the survivor takes the union of the kinds, sorted, the other's per-config classes over its own (a config both name keeping its place and taking the other's class), the unmatched class when any of them is unmatched and the matched one otherwise, the exemption of that class, and either exemplar flag. Returns whether any member was absorbed.
pub fn merge_members(
    members: &mut [Member],
    exempt: impl Fn(&str) -> bool,
    stats: &mut MergeStats,
    mut absorb: impl FnMut(&Member, &Member) -> Vec<String>,
) -> std::result::Result<bool, MergeError> {
    members.sort_by_key(|member| member.rank);
    let Some((survivor, others)) = members.split_first_mut() else {
        return Err(MergeError::NoMembers);
    };
    let mut merged_any = false;
    for other in others {
        let mut matched: Vec<String> = Vec::new();
        for (_config, class) in survivor.config_classes.iter().chain(&other.config_classes) {
            if class != UNMATCHED && !matched.contains(class) {
                matched.push(class.clone());
            }
        }
        if matched.len() > 1 {
            stats.kept_split_matched_classes += 1;
            continue;
        }
        survivor.configs = absorb(survivor, other);
        let kinds: BTreeSet<String> = survivor
            .kinds
            .drain(..)
            .chain(other.kinds.iter().cloned())
            .collect();
        survivor.kinds = kinds.into_iter().collect();
        for (config, class) in &other.config_classes {
            match survivor
                .config_classes
                .iter_mut()
                .find(|(held, _class)| held == config)
            {
                Some(entry) => entry.1.clone_from(class),
                None => survivor
                    .config_classes
                    .push((config.clone(), class.clone())),
            }
        }
        survivor.class_id = if survivor
            .config_classes
            .iter()
            .any(|(_config, class)| class == UNMATCHED)
        {
            UNMATCHED.to_owned()
        } else {
            matched.pop().ok_or(MergeError::NoClass)?
        };
        survivor.no_verdict = exempt(&survivor.class_id);
        survivor.exemplar = survivor.exemplar || other.exemplar;
        survivor.merged = true;
        other.survivor = Some(survivor.ordinal);
        stats.units_merged += 1;
        merged_any = true;
    }
    Ok(merged_any)
}

/// Two runs of rows merged by `key`, the first run's row ahead at an equal key (`audit._merged_rows`, which merges by section as `RowColumns.merge_runs` merges by config rank).
pub fn merged_rows<T: Clone, K: Ord>(first: &[T], second: &[T], key: impl Fn(&T) -> K) -> Vec<T> {
    let mut merged = Vec::with_capacity(first.len() + second.len());
    let (mut i, mut j) = (0, 0);
    while i < first.len() && j < second.len() {
        if key(&second[j]) < key(&first[i]) {
            merged.push(second[j].clone());
            j += 1;
        } else {
            merged.push(first[i].clone());
            i += 1;
        }
    }
    merged.extend_from_slice(&first[i..]);
    merged.extend_from_slice(&second[j..]);
    merged
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

fn rank(value: &Value, what: &str) -> Result<i64> {
    value
        .as_i64()
        .ok_or_else(|| WireError(format!("{what} is not a 64-bit int: {value:.200}")))
}

fn ids(value: &Value, what: &str) -> Result<Vec<u32>> {
    items(value, what)?
        .iter()
        .map(|item| {
            item.as_u64()
                .and_then(|number| u32::try_from(number).ok())
                .ok_or_else(|| WireError(format!("{what} holds a value that is not a u32: {item}")))
        })
        .collect()
}

fn words(value: &Value, what: &str) -> Result<Vec<u64>> {
    items(value, what)?
        .iter()
        .map(|item| {
            item.as_u64().ok_or_else(|| {
                WireError(format!("{what} holds a value that is not a word: {item}"))
            })
        })
        .collect()
}

fn entries<'a>(value: &'a Value, what: &str) -> Result<&'a [Value]> {
    value
        .as_object()
        .filter(|object| object.len() == 1)
        .and_then(|object| object.get("dict"))
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .ok_or_else(|| WireError(format!("{what} is not a typed dict: {value:.200}")))
}

fn pair<'a>(entry: &'a Value, what: &str) -> Result<(&'a Value, &'a Value)> {
    match entry.as_array().map(Vec::as_slice) {
        Some([key, value]) => Ok((key, value)),
        _ => error(format!("{what}: a dict entry is not a pair: {entry:.200}")),
    }
}

fn string_table(value: &Value, what: &str) -> Result<StringTable> {
    let mut table = StringTable::new();
    for (at, string) in items(value, what)?.iter().enumerate() {
        let id = table.id(text(string, what)?);
        if id as usize != at {
            return error(format!("{what}: the string table holds {string} twice"));
        }
    }
    Ok(table)
}

fn class_order(value: &Value, what: &str) -> Result<HashMap<String, usize>> {
    entries(value, what)?
        .iter()
        .map(|entry| {
            let (name, index) = pair(entry, what)?;
            Ok((text(name, what)?.to_owned(), count(index, what)?))
        })
        .collect()
}

fn family_rank(value: &Value, what: &str) -> Result<FamilyRanks> {
    let ranks = entries(value, what)?
        .iter()
        .map(|entry| {
            let (name, value) = pair(entry, what)?;
            Ok((text(name, what)?.to_owned(), rank(value, what)?))
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(FamilyRanks::from_entries(ranks))
}

fn runs(value: &Value, what: &str) -> Result<Vec<(usize, usize)>> {
    items(value, what)?
        .iter()
        .map(|run| {
            let [start, end] = arguments(run, what)?;
            Ok((count(start, what)?, count(end, what)?))
        })
        .collect()
}

fn listed(values: &[u32]) -> Value {
    json!({ "list": values })
}

fn tuple(items: Vec<Value>) -> Value {
    json!({ "tuple": items })
}

/// One stage line's answer in the typed encoding.
fn answer(record: &Value, key: &str) -> Result<Value> {
    let input = &record["input"];
    match record["form"].as_str() {
        Some("counting") => {
            let [
                class_col,
                group_col,
                strings,
                order,
                ranks,
                walk,
                n,
                given_runs,
                id_words,
            ] = arguments(input, key)?;
            let (class_col, group_col) = (ids(class_col, key)?, ids(group_col, key)?);
            let (strings, order) = (string_table(strings, key)?, class_order(order, key)?);
            let ranks = family_rank(ranks, key)?;
            let buckets = Buckets {
                class_col: &class_col,
                group_col: &group_col,
                strings: &strings,
                class_order: &order,
                family_rank: &ranks,
            };
            let (walk, n) = (ids(walk, key)?, count(n, key)?);
            Ok(listed(&if given_runs.is_null() || id_words.is_null() {
                counting_permutation(&buckets, &walk, n)
            } else {
                let id_words = words(id_words, key)?;
                counting_permutation_by_id(&buckets, &walk, n, runs(given_runs, key)?, |ordinal| {
                    id_words[ordinal as usize]
                })
            }))
        }
        Some("triage") => {
            let [
                class_col,
                group_col,
                strings,
                counts,
                starts,
                values,
                n,
                order,
                ranks,
                id_words,
            ] = arguments(input, key)?;
            let counts = ids(counts, key)?
                .into_iter()
                .map(|value| {
                    u8::try_from(value)
                        .map_err(|_| WireError(format!("{key}: a window length past a byte")))
                })
                .collect::<Result<Vec<u8>>>()?;
            let values = ids(values, key)?
                .into_iter()
                .map(|value| {
                    u16::try_from(value)
                        .map_err(|_| WireError(format!("{key}: a code point past 16 bits")))
                })
                .collect::<Result<Vec<u16>>>()?;
            let starts = ids(starts, key)?;
            let windows = Windows {
                counts: &counts,
                starts: &starts,
                values: &values,
            };
            let id_words = if id_words.is_null() {
                None
            } else {
                Some(words(id_words, key)?)
            };
            let id_word = id_words
                .as_ref()
                .map(|id_words| move |ordinal: u32| id_words[ordinal as usize]);
            let (class_col, group_col) = (ids(class_col, key)?, ids(group_col, key)?);
            let (strings, order) = (string_table(strings, key)?, class_order(order, key)?);
            let ranks = family_rank(ranks, key)?;
            let buckets = Buckets {
                class_col: &class_col,
                group_col: &group_col,
                strings: &strings,
                class_order: &order,
                family_rank: &ranks,
            };
            Ok(listed(&triage_permutation(
                &buckets,
                &windows,
                count(n, key)?,
                id_word
                    .as_ref()
                    .map(|id_word| id_word as &dyn Fn(u32) -> u64),
            )))
        }
        Some("triage-key") => {
            let [class_index, group, codepoints, unit_id, ranks] = arguments(input, key)?;
            let found = triage_key(
                count(class_index, key)?,
                text(group, key)?,
                &ids(codepoints, key)?,
                text(unit_id, key)?,
                &family_rank(ranks, key)?,
            );
            Ok(tuple(vec![
                found.class_index.into(),
                tuple(found.families.into_iter().map(Value::from).collect()),
                found.length.into(),
                tuple(found.codepoints.into_iter().map(Value::from).collect()),
                found.unit_id.into(),
            ]))
        }
        Some("family-ranks") => {
            let family_of = entries(input, key)?
                .iter()
                .map(|entry| {
                    let (value, name) = pair(entry, key)?;
                    Ok((rank(value, key)?, text(name, key)?.to_owned()))
                })
                .collect::<Result<Vec<_>>>()?;
            Ok(json!({
                "dict": family_ranks(&family_of)
                    .entries()
                    .iter()
                    .map(|(name, rank)| json!([name, rank]))
                    .collect::<Vec<_>>()
            }))
        }
        other => error(format!("{key}: no permutation form {other:?}")),
    }
}

/// The `permutation` stage: for each line of `stage_file`, answer its `input` by its `form` in one `{"key", "value"}` line, in the typed encoding: `counting` places rows as `audit._counting_permutation` does, by id word within a window ([`counting_permutation_by_id`]) when the line gives both runs and id words and in walk order ([`counting_permutation`]) otherwise; `triage` sorts a table's rows as `audit._triage_permutation` does ([`triage_permutation`]); `triage-key` gives `audit.triage_key`'s tuple ([`triage_key`]); and `family-ranks` gives `audit.family_ranks`' dict in its order ([`family_ranks`]). A line's string table is the table's strings in id order, and its id words are each row's word by ordinal.
pub fn stage_permutation(stage_file: &Path, out: &mut impl Write) -> Result<()> {
    answer_lines(stage_file, out, answer)
}

fn flag(value: &Value, what: &str) -> Result<bool> {
    value
        .as_bool()
        .ok_or_else(|| WireError(format!("{what} is not a bool: {value:.200}")))
}

fn texts(value: &Value, what: &str) -> Result<Vec<String>> {
    items(value, what)?
        .iter()
        .map(|item| Ok(text(item, what)?.to_owned()))
        .collect()
}

/// A typed record's field values, checked against its class name and its field names in order.
fn record<'a, const COUNT: usize>(
    value: &'a Value,
    class: &str,
    names: [&str; COUNT],
    what: &str,
) -> Result<[&'a Value; COUNT]> {
    let fields = match value["record"].as_array().map(Vec::as_slice) {
        Some([name, fields]) if name == class => fields.as_array(),
        _ => None,
    };
    let Some(fields) = fields.filter(|fields| fields.len() == COUNT) else {
        return error(format!("{what} is not a {class} record: {value:.200}"));
    };
    let mut values = [&Value::Null; COUNT];
    for ((slot, field), name) in values.iter_mut().zip(fields).zip(names) {
        match field.as_array().map(Vec::as_slice) {
            Some([held, field_value]) if held == name => *slot = field_value,
            _ => {
                return error(format!(
                    "{what}: a {class} record's field is not {name}: {field:.200}"
                ));
            }
        }
    }
    Ok(values)
}

const MEMBER_FIELDS: [&str; 10] = [
    "ordinal",
    "rank",
    "configs",
    "kinds",
    "config_classes",
    "exemplar",
    "class_id",
    "no_verdict",
    "merged",
    "survivor",
];

const MERGE_STATS: [&str; 3] = [
    "windows_merged",
    "units_merged",
    "kept_split_matched_classes",
];

fn member(value: &Value, what: &str) -> Result<Member> {
    let [
        ordinal,
        rank_value,
        configs,
        kinds,
        config_classes,
        exemplar,
        class_id,
        no_verdict,
        merged,
        survivor,
    ] = record(value, "_Member", MEMBER_FIELDS, what)?;
    let survivor = match rank(survivor, what)? {
        -1 => None,
        _ => Some(count(survivor, what)?),
    };
    Ok(Member {
        ordinal: count(ordinal, what)?,
        rank: rank(rank_value, what)?,
        configs: texts(configs, what)?,
        kinds: texts(kinds, what)?,
        config_classes: entries(config_classes, what)?
            .iter()
            .map(|entry| {
                let (config, class) = pair(entry, what)?;
                Ok((
                    text(config, what)?.to_owned(),
                    text(class, what)?.to_owned(),
                ))
            })
            .collect::<Result<_>>()?,
        exemplar: flag(exemplar, what)?,
        class_id: text(class_id, what)?.to_owned(),
        no_verdict: flag(no_verdict, what)?,
        merged: flag(merged, what)?,
        survivor,
    })
}

fn member_value(member: &Member) -> Value {
    let survivor = member.survivor.map_or(-1, |ordinal| ordinal as i64);
    let fields: [Value; 10] = [
        member.ordinal.into(),
        member.rank.into(),
        tuple(
            member
                .configs
                .iter()
                .map(|config| config.as_str().into())
                .collect(),
        ),
        tuple(
            member
                .kinds
                .iter()
                .map(|kind| kind.as_str().into())
                .collect(),
        ),
        json!({ "dict": member.config_classes.iter().map(|(config, class)| json!([config, class])).collect::<Vec<_>>() }),
        member.exemplar.into(),
        member.class_id.as_str().into(),
        member.no_verdict.into(),
        member.merged.into(),
        survivor.into(),
    ];
    json!({
        "record": [
            "_Member",
            MEMBER_FIELDS.iter().zip(fields).map(|(name, value)| json!([name, value])).collect::<Vec<_>>(),
        ]
    })
}

fn merge_stats(value: &Value, what: &str) -> Result<MergeStats> {
    let entries = entries(value, what)?;
    let mut counts = [0i64; 3];
    if entries.len() != counts.len() {
        return error(format!(
            "{what}: the stats do not hold {MERGE_STATS:?}: {value:.200}"
        ));
    }
    for ((slot, entry), name) in counts.iter_mut().zip(entries).zip(MERGE_STATS) {
        let (key, held) = pair(entry, what)?;
        if key != name {
            return error(format!(
                "{what}: the stats do not hold {MERGE_STATS:?}: {value:.200}"
            ));
        }
        *slot = rank(held, what)?;
    }
    let [windows_merged, units_merged, kept_split_matched_classes] = counts;
    Ok(MergeStats {
        windows_merged,
        units_merged,
        kept_split_matched_classes,
    })
}

fn stats_value(stats: &MergeStats) -> Value {
    let counts = [
        stats.windows_merged,
        stats.units_merged,
        stats.kept_split_matched_classes,
    ];
    json!({ "dict": MERGE_STATS.iter().zip(counts).map(|(name, count)| json!([name, count])).collect::<Vec<_>>() })
}

type Run = Vec<(i64, String)>;

fn run(value: &Value, what: &str) -> Result<Run> {
    items(value, what)?
        .iter()
        .map(|row| {
            let [key, config] = arguments(row, what)?;
            Ok((rank(key, what)?, text(config, what)?.to_owned()))
        })
        .collect()
}

fn run_value(run: &Run) -> Value {
    json!({ "list": run.iter().map(|(key, config)| tuple(vec![(*key).into(), config.as_str().into()])).collect::<Vec<_>>() })
}

/// One `merge` line's answer in the typed encoding.
fn merge_answer(record: &Value, key: &str) -> Result<Value> {
    let [members, exempt, stats, runs] = arguments(&record["input"], key)?;
    let mut members = items(members, key)?
        .iter()
        .map(|value| member(value, key))
        .collect::<Result<Vec<_>>>()?;
    let exempt = texts(exempt, key)?;
    let mut stats = merge_stats(stats, key)?;
    let given: Vec<usize> = members.iter().map(|member| member.ordinal).collect();
    let mut held: HashMap<usize, Run> = HashMap::new();
    let runs = items(runs, key)?;
    if runs.len() != given.len() {
        return error(format!("{key}: the runs are not one a member"));
    }
    for (&ordinal, value) in given.iter().zip(runs) {
        let run = run(value, key)?;
        held.entry(ordinal).or_insert(run);
    }
    let absorb = |survivor: &Member, other: &Member| {
        let merged = merged_rows(&held[&survivor.ordinal], &held[&other.ordinal], |row| row.0);
        let configs = merged.iter().map(|(_key, config)| config.clone()).collect();
        held.insert(survivor.ordinal, merged);
        configs
    };
    let merged = merge_members(
        &mut members,
        |class| exempt.iter().any(|held| held == class),
        &mut stats,
        absorb,
    );
    Ok(match merged {
        Ok(merged) => tuple(vec![
            merged.into(),
            json!({ "list": members.iter().map(member_value).collect::<Vec<_>>() }),
            stats_value(&stats),
            json!({ "list": given.iter().map(|ordinal| run_value(&held[ordinal])).collect::<Vec<_>>() }),
        ]),
        Err(MergeError::NoMembers) => tuple(vec![
            "raised".into(),
            "IndexError".into(),
            "list index out of range".into(),
        ]),
        Err(MergeError::NoClass) => tuple(vec![
            "raised".into(),
            "KeyError".into(),
            "'pop from an empty set'".into(),
        ]),
    })
}

/// The `merge` stage: for each line of `stage_file`, merge its window's members as `audit._merge_members` does ([`merge_members`]), each member's run of `(merge key, config)` rows merged by [`merged_rows`] as the line's `absorb` merged them, and answer in one `{"key", "value"}` line, in the typed encoding: whether any member was absorbed, the members in the order the merge leaves them, the stats and each member's run, aligned with the members as given; or the exception Python raised, as `("raised", class, message)`.
pub fn stage_merge(stage_file: &Path, out: &mut impl Write) -> Result<()> {
    answer_lines(stage_file, out, merge_answer)
}

/// Answer each line of `stage_file` by `answer` in one `{"key", "value"}` line.
fn answer_lines(
    stage_file: &Path,
    out: &mut impl Write,
    answer: impl Fn(&Value, &str) -> Result<Value>,
) -> Result<()> {
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

/// A `u32` column's value for a row it names nothing for (`audit.NONE`).
pub const NONE: u32 = u32::MAX;
/// The bytes of a unit's input key (`audit.INPUT_KEY_BYTES`).
pub const INPUT_KEY_BYTES: usize = 32;
/// The flag of a unit the ledger names as an exemplar (`audit.EXEMPLAR`).
pub const EXEMPLAR: u8 = 1;
/// The flag of a unit whose class takes no verdict (`audit.NO_VERDICT`).
pub const NO_VERDICT: u8 = 2;
/// The flag of a unit the ink-duplicate merge left in the table (`audit.LIVE`).
pub const LIVE: u8 = 4;
/// The group of a window none of whose code points has a family (`audit.group_for`).
pub const BOUNDARIES_GROUP: &str = "(boundaries)";

/// A window's group (`audit.group_for`): the families of its first two code points that have one, joined by `:`, or [`BOUNDARIES_GROUP`] when none has.
pub fn group_for(values: &[i64], family_of: &HashMap<i64, String>) -> String {
    let families: Vec<&str> = values
        .iter()
        .filter_map(|value| family_of.get(value).map(String::as_str))
        .collect();
    if families.is_empty() {
        BOUNDARIES_GROUP.to_owned()
    } else {
        families[..families.len().min(2)].join(":")
    }
}

/// A configuration's rank (`audit._config_index`): its place among `acceptance`, or one past them for any other.
pub fn config_index(config: &str, acceptance: &[String]) -> i64 {
    acceptance
        .iter()
        .position(|held| held == config)
        .unwrap_or(acceptance.len()) as i64
}

/// A unit's configs partitioned by rendered outcome (`audit.render_groups_for_rows`): each row's (baseline, new) pair and config, grouped by pair in the order the rows first name each.
pub fn render_groups_for_rows<K: PartialEq>(
    rows: impl IntoIterator<Item = (K, String)>,
) -> Vec<Vec<String>> {
    let mut groups: Vec<(K, Vec<String>)> = Vec::new();
    for (pair, config) in rows {
        match groups.iter_mut().find(|(held, _configs)| *held == pair) {
            Some((_pair, configs)) => configs.push(config),
            None => groups.push((pair, vec![config])),
        }
    }
    groups.into_iter().map(|(_pair, configs)| configs).collect()
}

/// The pool of the rendered-name tuples a unit's baseline and new columns name (`UnitTable.names`, a `TuplePool[str]`): each tuple held as the ids of its names in a table of their own, so its ids are the ones the Python pool gives the tuples of strings.
#[derive(Clone, Debug, Default)]
pub struct NamePool {
    labels: StringTable,
    tuples: TuplePool<u32>,
}

impl NamePool {
    pub fn new() -> Self {
        Self::default()
    }

    /// The tuple's id, assigned as the next one when the pool does not hold it; a sealed pool assigns none.
    pub fn id<'n>(
        &mut self,
        names: impl IntoIterator<Item = &'n str>,
    ) -> std::result::Result<u32, Sealed> {
        if self.tuples.sealed() {
            return Err(Sealed);
        }
        let ids: Vec<u32> = names.into_iter().map(|name| self.labels.id(name)).collect();
        self.tuples.id(&ids)
    }

    pub fn get(&self, id: u32) -> Vec<&str> {
        self.tuples
            .get(id)
            .iter()
            .map(|&label| self.labels.get(label))
            .collect()
    }

    /// The distinct tuples without the empty one.
    pub fn len(&self) -> usize {
        self.tuples.len()
    }

    pub fn is_empty(&self) -> bool {
        self.tuples.is_empty()
    }

    /// The distinct tuples' lengths summed.
    pub fn elements(&self) -> usize {
        self.tuples.elements()
    }

    pub fn seal(&mut self) {
        self.tuples.seal();
    }

    pub fn sealed(&self) -> bool {
        self.tuples.sealed()
    }
}

/// What [`UnitTable::compact`] gives (`audit.Compaction`): each pre-merge row's survivor's post-merge row, and a byte a pre-merge row, 1 for a row the merge removed.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Compaction {
    pub survivor: Vec<u32>,
    pub removed: Vec<u8>,
}

/// What a unit store answers about a row, for [`UnitTable::unit`] (`audit.UnitStoreView`).
pub trait UnitStoreView {
    fn input_key_hex(&self, ordinal: usize) -> String;
    fn loaded(&self, ordinal: usize) -> bool;
    fn unit_id(&self, ordinal: usize) -> String;
    fn machine_flags(&self, ordinal: usize) -> (bool, bool, bool);
}

/// One unit materialized from the table (`audit.Unit`), with every field but `ink_deltas`, which a materialized unit leaves empty.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Unit {
    pub codepoints: String,
    pub baseline: Vec<String>,
    pub new: Vec<String>,
    pub class_id: String,
    pub row_count: u32,
    pub rows_start: u32,
    pub configs: Vec<String>,
    pub kinds: Vec<String>,
    pub group: String,
    pub exemplar: bool,
    pub unit_id: String,
    pub input_key: String,
    pub ordinal: usize,
    pub order: Option<u32>,
    pub batch: Option<u32>,
    pub render_groups: Vec<Vec<String>>,
    pub ink_identical: bool,
    pub picture_identical: bool,
    pub junior_equivalent: bool,
    pub no_verdict: bool,
    pub config_classes: Vec<(String, String)>,
    pub unmatched_group: String,
    pub duplicate_group: Option<String>,
    pub cluster: Option<String>,
    pub ledger_class: String,
}

fn optional_int(value: Option<u32>) -> PyValue {
    value.map_or(PyValue::None, |value| PyValue::Int(i64::from(value)))
}

impl Unit {
    /// The unit as Python holds it, an `audit.Unit` record of its fields in their order but `ink_deltas`.
    pub fn to_py(&self) -> PyValue {
        let fields = [
            ("codepoints", PyValue::str(&self.codepoints)),
            ("baseline", PyValue::str_tuple(&self.baseline)),
            ("new", PyValue::str_tuple(&self.new)),
            ("class_id", PyValue::str(&self.class_id)),
            ("row_count", PyValue::Int(i64::from(self.row_count))),
            ("rows_start", PyValue::Int(i64::from(self.rows_start))),
            ("configs", PyValue::str_tuple(&self.configs)),
            ("kinds", PyValue::str_tuple(&self.kinds)),
            ("group", PyValue::str(&self.group)),
            ("exemplar", PyValue::Bool(self.exemplar)),
            ("unit_id", PyValue::str(&self.unit_id)),
            ("input_key", PyValue::str(&self.input_key)),
            ("ordinal", PyValue::Int(self.ordinal as i64)),
            ("order", optional_int(self.order)),
            ("batch", optional_int(self.batch)),
            (
                "render_groups",
                PyValue::Tuple(
                    self.render_groups
                        .iter()
                        .map(|group| PyValue::str_tuple(group))
                        .collect(),
                ),
            ),
            ("ink_identical", PyValue::Bool(self.ink_identical)),
            ("picture_identical", PyValue::Bool(self.picture_identical)),
            ("junior_equivalent", PyValue::Bool(self.junior_equivalent)),
            ("no_verdict", PyValue::Bool(self.no_verdict)),
            ("config_classes", mapping_value(&self.config_classes)),
            ("unmatched_group", PyValue::str(&self.unmatched_group)),
            (
                "duplicate_group",
                PyValue::optional_str(self.duplicate_group.as_deref()),
            ),
            ("cluster", PyValue::optional_str(self.cluster.as_deref())),
            ("ledger_class", PyValue::str(&self.ledger_class)),
        ];
        PyValue::Record(
            "Unit".to_owned(),
            fields
                .into_iter()
                .map(|(name, value)| (name.to_owned(), value))
                .collect(),
        )
    }
}

fn mapping_value(pairs: &[(String, String)]) -> PyValue {
    PyValue::Dict(
        pairs
            .iter()
            .map(|(key, value)| (PyValue::str(key), PyValue::str(value)))
            .collect(),
    )
}

fn gather<T: Copy>(column: &[T], ordinals: &[u32]) -> Vec<T> {
    ordinals
        .iter()
        .map(|&ordinal| column[ordinal as usize])
        .collect()
}

/// The workload as columns over the unit's ordinal (`audit.UnitTable`): every name an id into [`UnitTable::strings`], the config and kind tuples ids into [`UnitTable::tuples`], the render groups into [`UnitTable::groups`], the per-config class maps into [`UnitTable::mappings`] in their insertion order, and the baseline and new name tuples into [`UnitTable::names`] until [`UnitTable::release_names`] drops both columns and the pool. The window is an offset and a count into one `u16` column; the ledger's two flags and the merge's [`LIVE`] bit share a byte; `order`, `batch` and `survivor` hold [`NONE`] for a unit they name nothing for; `rows_start` and `row_count` address the unit's run of the audit's rows. Each tuple id carries the rank of its first config (`_ranks`), which [`UnitTable::config_rank`] reads, and the window index ([`UnitTable::window_index`]) holds the rows in window order.
#[derive(Clone, Debug)]
pub struct UnitTable {
    pub n: usize,
    pub strings: StringTable,
    pub names: Option<NamePool>,
    pub tuples: TuplePool<String>,
    pub groups: TuplePool<Vec<String>>,
    pub mappings: MappingPool<String>,
    acceptance: Vec<String>,
    class: Vec<u32>,
    group: Vec<u32>,
    unmatched_group: Vec<u32>,
    duplicate_group: Vec<u32>,
    cluster: Vec<u32>,
    configs: Vec<u32>,
    kinds: Vec<u32>,
    render_groups: Vec<u32>,
    baseline: Option<Vec<u32>>,
    new: Option<Vec<u32>>,
    config_classes: Vec<u32>,
    flags: Vec<u8>,
    window_start: Vec<u32>,
    window_n: Vec<u8>,
    window_values: Vec<u16>,
    rows_start: Vec<u32>,
    row_count: Vec<u32>,
    order: Vec<u32>,
    batch: Vec<u32>,
    survivor: Vec<u32>,
    ranks: Vec<i64>,
    window_index: Option<WindowIndex>,
}

impl UnitTable {
    /// A table of `n` rows with every column at its starting value, over the acceptance configurations `acceptance` that rank a config tuple.
    pub fn new(n: usize, acceptance: &[String]) -> Self {
        Self {
            n,
            strings: StringTable::new(),
            names: Some(NamePool::new()),
            tuples: TuplePool::new(),
            groups: TuplePool::new(),
            mappings: MappingPool::new(),
            acceptance: acceptance.to_vec(),
            class: vec![0; n],
            group: vec![0; n],
            unmatched_group: vec![0; n],
            duplicate_group: vec![0; n],
            cluster: vec![0; n],
            configs: vec![0; n],
            kinds: vec![0; n],
            render_groups: vec![0; n],
            baseline: Some(vec![0; n]),
            new: Some(vec![0; n]),
            config_classes: vec![0; n],
            flags: vec![LIVE; n],
            window_start: vec![0; n],
            window_n: vec![0; n],
            window_values: Vec::new(),
            rows_start: vec![0; n],
            row_count: vec![0; n],
            order: vec![NONE; n],
            batch: vec![NONE; n],
            survivor: vec![NONE; n],
            ranks: vec![acceptance.len() as i64],
            window_index: None,
        }
    }

    pub fn len(&self) -> usize {
        self.n
    }

    pub fn is_empty(&self) -> bool {
        self.n == 0
    }

    /// A config or kind tuple's id, the rank of its first value recorded beside a new one (`UnitTable._tuple_id`).
    pub fn tuple_id(&mut self, value: &[String]) -> u32 {
        let found = self
            .tuples
            .id(value)
            .expect("the config and kind tuples are never sealed");
        if found as usize == self.ranks.len() {
            self.ranks.push(match value.first() {
                Some(first) => config_index(first, &self.acceptance),
                None => self.acceptance.len() as i64,
            });
        }
        found
    }

    pub fn class_id(&self, ordinal: usize) -> &str {
        self.strings.get(self.class[ordinal])
    }

    pub fn group(&self, ordinal: usize) -> &str {
        self.strings.get(self.group[ordinal])
    }

    pub fn unmatched_group(&self, ordinal: usize) -> &str {
        self.strings.get(self.unmatched_group[ordinal])
    }

    pub fn duplicate_group(&self, ordinal: usize) -> Option<&str> {
        match self.duplicate_group[ordinal] {
            0 => None,
            index => Some(self.strings.get(index)),
        }
    }

    pub fn cluster(&self, ordinal: usize) -> Option<&str> {
        match self.cluster[ordinal] {
            0 => None,
            index => Some(self.strings.get(index)),
        }
    }

    pub fn configs(&self, ordinal: usize) -> &[String] {
        self.tuples.get(self.configs[ordinal])
    }

    pub fn kinds(&self, ordinal: usize) -> &[String] {
        self.tuples.get(self.kinds[ordinal])
    }

    pub fn render_groups(&self, ordinal: usize) -> &[Vec<String>] {
        self.groups.get(self.render_groups[ordinal])
    }

    /// The unit's baseline names, none once released.
    pub fn baseline(&self, ordinal: usize) -> Vec<&str> {
        match (&self.names, &self.baseline) {
            (Some(names), Some(column)) => names.get(column[ordinal]),
            _ => Vec::new(),
        }
    }

    /// The unit's new names, none once released.
    pub fn new_names(&self, ordinal: usize) -> Vec<&str> {
        match (&self.names, &self.new) {
            (Some(names), Some(column)) => names.get(column[ordinal]),
            _ => Vec::new(),
        }
    }

    pub fn config_classes(&self, ordinal: usize) -> &[(String, String)] {
        self.mappings.get(self.config_classes[ordinal])
    }

    pub fn exemplar(&self, ordinal: usize) -> bool {
        self.flags[ordinal] & EXEMPLAR != 0
    }

    pub fn no_verdict(&self, ordinal: usize) -> bool {
        self.flags[ordinal] & NO_VERDICT != 0
    }

    pub fn live(&self, ordinal: usize) -> bool {
        self.flags[ordinal] & LIVE != 0
    }

    /// The row a removed unit merged into, or the row itself while it is live.
    pub fn survivor(&self, ordinal: usize) -> u32 {
        if self.live(ordinal) {
            ordinal as u32
        } else {
            self.survivor[ordinal]
        }
    }

    /// The rows in window order beside their packed windows, sorted from the window column on the first call when no source wrote it.
    pub fn window_index(&mut self) -> &WindowIndex {
        if self.window_index.is_none() {
            let index = WindowIndex::build(
                (0..self.n).map(|ordinal| (ordinal as u32, self.codepoints(ordinal))),
            );
            self.window_index = Some(index);
        }
        self.window_index.as_ref().expect("an index built")
    }

    /// The window index as the table holds it, None while no source has written it and nothing has asked for it.
    pub fn held_window_index(&self) -> Option<&WindowIndex> {
        self.window_index.as_ref()
    }

    /// Drop the window index once the primary-unit search, its last reader, has run.
    pub fn release_window_index(&mut self) {
        self.window_index = None;
    }

    pub fn codepoints(&self, ordinal: usize) -> Vec<u32> {
        let start = self.window_start[ordinal] as usize;
        self.window_values[start..start + usize::from(self.window_n[ordinal])]
            .iter()
            .map(|&value| u32::from(value))
            .collect()
    }

    /// The window in the audit's format: colon-joined uppercase hex, at least four digits a code point (`audit.format_codepoints`).
    pub fn codepoints_text(&self, ordinal: usize) -> String {
        self.codepoints(ordinal)
            .iter()
            .map(|value| format!("{value:04X}"))
            .collect::<Vec<_>>()
            .join(":")
    }

    /// The rank of the unit's first config (`UnitTable.config_rank`).
    pub fn config_rank(&self, ordinal: usize) -> i64 {
        self.ranks[self.configs[ordinal] as usize]
    }

    pub fn rows_start(&self, ordinal: usize) -> u32 {
        self.rows_start[ordinal]
    }

    pub fn row_count(&self, ordinal: usize) -> u32 {
        self.row_count[ordinal]
    }

    pub fn order(&self, ordinal: usize) -> Option<u32> {
        Some(self.order[ordinal]).filter(|&value| value != NONE)
    }

    pub fn batch(&self, ordinal: usize) -> Option<u32> {
        Some(self.batch[ordinal]).filter(|&value| value != NONE)
    }

    /// Mark the row as removed by the ink-duplicate merge into `survivor`, which must be live; [`UnitTable::compact`] drops it.
    pub fn merge_into(&mut self, ordinal: usize, survivor: usize) -> Result<()> {
        if !self.live(survivor) {
            return error(format!(
                "row {ordinal} merges into row {survivor}, which is not live"
            ));
        }
        self.flags[ordinal] &= !LIVE;
        self.survivor[ordinal] = survivor as u32;
        Ok(())
    }

    /// Drop every row the merge removed, renumber the rest in place, and give each pre-merge row's survivor's post-merge row (`UnitTable.compact`). The window values, the pools and the string table do not change, and a row's window offsets move with it.
    pub fn compact(&mut self) -> Compaction {
        let n = self.n;
        let mut remap = vec![NONE; n];
        let mut removed = vec![0u8; n];
        let mut kept: Vec<u32> = Vec::new();
        for ordinal in 0..n {
            if self.flags[ordinal] & LIVE != 0 {
                remap[ordinal] = kept.len() as u32;
                kept.push(ordinal as u32);
            } else {
                removed[ordinal] = 1;
            }
        }
        for ordinal in 0..n {
            if removed[ordinal] != 0 {
                remap[ordinal] = remap[self.survivor[ordinal] as usize];
            }
        }
        if kept.len() != n {
            for column in [
                &mut self.class,
                &mut self.group,
                &mut self.unmatched_group,
                &mut self.duplicate_group,
                &mut self.cluster,
                &mut self.configs,
                &mut self.kinds,
                &mut self.render_groups,
                &mut self.config_classes,
                &mut self.window_start,
                &mut self.rows_start,
                &mut self.row_count,
                &mut self.order,
                &mut self.batch,
            ] {
                *column = gather(column, &kept);
            }
            if let (Some(baseline), Some(new)) = (&mut self.baseline, &mut self.new) {
                *baseline = gather(baseline, &kept);
                *new = gather(new, &kept);
            }
            self.flags = gather(&self.flags, &kept);
            self.window_n = gather(&self.window_n, &kept);
            self.survivor = vec![NONE; kept.len()];
            self.n = kept.len();
            if let Some(index) = &self.window_index {
                self.window_index = Some(index.remapped(&remap, &removed));
            }
        }
        Compaction {
            survivor: remap,
            removed,
        }
    }

    /// Drop the baseline and new columns and their name pool; afterward both read empty.
    pub fn release_names(&mut self) {
        self.baseline = None;
        self.new = None;
        self.names = None;
    }

    /// Each change group's unit count, in the order the column first names each (`UnitTable.unmatched_group_counts`).
    pub fn unmatched_group_counts(&self) -> Vec<(String, i64)> {
        let mut counts: Vec<(u32, i64)> = Vec::new();
        let mut at: HashMap<u32, usize> = HashMap::new();
        for &index in &self.unmatched_group {
            if index != 0 {
                match at.get(&index) {
                    Some(&slot) => counts[slot].1 += 1,
                    None => {
                        at.insert(index, counts.len());
                        counts.push((index, 1));
                    }
                }
            }
        }
        counts
            .into_iter()
            .map(|(index, count)| (self.strings.get(index).to_owned(), count))
            .collect()
    }

    /// The class ids the rows hold.
    pub fn classes_present(&self) -> BTreeSet<&str> {
        self.class
            .iter()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .map(|&index| self.strings.get(index))
            .collect()
    }

    /// Every class's ordinals in the order `order` states them, the classes in the order they first appear along it (`UnitTable.rows_by_class`).
    pub fn rows_by_class(&self, order: impl IntoIterator<Item = u32>) -> Vec<(String, Vec<u32>)> {
        let mut grouped: Vec<(u32, Vec<u32>)> = Vec::new();
        let mut at: HashMap<u32, usize> = HashMap::new();
        for ordinal in order {
            let index = self.class[ordinal as usize];
            let slot = *at.entry(index).or_insert_with(|| {
                grouped.push((index, Vec::new()));
                grouped.len() - 1
            });
            grouped[slot].1.push(ordinal);
        }
        grouped
            .into_iter()
            .map(|(index, members)| (self.strings.get(index).to_owned(), members))
            .collect()
    }

    /// The unit at `ordinal` materialized from the table (`UnitTable.unit`); with a store, its input key from the store, and its id and machine flags once the store has loaded it.
    pub fn unit(&self, ordinal: usize, store: Option<&dyn UnitStoreView>) -> Unit {
        let (mut unit_id, mut input_key) = (String::new(), String::new());
        let mut flags = (false, false, false);
        if let Some(store) = store {
            input_key = store.input_key_hex(ordinal);
            if store.loaded(ordinal) {
                unit_id = store.unit_id(ordinal);
                flags = store.machine_flags(ordinal);
            }
        }
        let owned = |names: Vec<&str>| names.into_iter().map(str::to_owned).collect();
        Unit {
            codepoints: self.codepoints_text(ordinal),
            baseline: owned(self.baseline(ordinal)),
            new: owned(self.new_names(ordinal)),
            class_id: self.class_id(ordinal).to_owned(),
            row_count: self.row_count[ordinal],
            rows_start: self.rows_start[ordinal],
            configs: self.configs(ordinal).to_vec(),
            kinds: self.kinds(ordinal).to_vec(),
            group: self.group(ordinal).to_owned(),
            exemplar: self.exemplar(ordinal),
            unit_id,
            input_key,
            ordinal,
            order: self.order(ordinal),
            batch: self.batch(ordinal),
            render_groups: self.render_groups(ordinal).to_vec(),
            ink_identical: flags.0,
            picture_identical: flags.1,
            junior_equivalent: flags.2,
            no_verdict: self.no_verdict(ordinal),
            config_classes: self.config_classes(ordinal).to_vec(),
            unmatched_group: self.unmatched_group(ordinal).to_owned(),
            duplicate_group: self.duplicate_group(ordinal).map(str::to_owned),
            cluster: self.cluster(ordinal).map(str::to_owned),
            ledger_class: String::new(),
        }
    }

    /// A row's ids in the table's columns, in `UnitTable.columns`' order, the name ids None once released.
    pub fn ids(&self, ordinal: usize) -> [Option<u32>; 19] {
        let name = |column: &Option<Vec<u32>>| column.as_ref().map(|column| column[ordinal]);
        [
            Some(self.class[ordinal]),
            Some(self.group[ordinal]),
            Some(self.unmatched_group[ordinal]),
            Some(self.duplicate_group[ordinal]),
            Some(self.cluster[ordinal]),
            Some(self.configs[ordinal]),
            Some(self.kinds[ordinal]),
            Some(self.render_groups[ordinal]),
            name(&self.baseline),
            name(&self.new),
            Some(self.config_classes[ordinal]),
            Some(u32::from(self.flags[ordinal])),
            Some(self.window_start[ordinal]),
            Some(u32::from(self.window_n[ordinal])),
            Some(self.rows_start[ordinal]),
            Some(self.row_count[ordinal]),
            Some(self.order[ordinal]),
            Some(self.batch[ordinal]),
            Some(self.survivor[ordinal]),
        ]
    }

    /// The rank of each tuple id's first config, by id.
    pub fn ranks(&self) -> &[i64] {
        &self.ranks
    }

    /// The window values every row's window offsets address.
    pub fn window_values(&self) -> &[u16] {
        &self.window_values
    }
}

/// The stream's pre-merge units in load order, in the columns the pre-merge snapshot holds (`audit.PremergeColumns`): each one's class id and config-set id, its exemption, and its window's offsets into the table's value column.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PremergeColumns {
    pub class_id: Vec<u32>,
    pub no_verdict: Vec<u8>,
    pub configs: Vec<u32>,
    pub window_start: Vec<u32>,
    pub window_n: Vec<u8>,
}

/// What the load gives the build (`audit.Workload` from the stream): the unit table, the audit's row count, and the places in the ledger of the classes the units reach.
#[derive(Clone, Debug)]
pub struct Workload {
    pub table: UnitTable,
    pub row_count: usize,
    pub classes_present: Vec<usize>,
}

/// What [`stream_workload`] gives (`audit.StreamedLoad`): the workload, the pre-merge units' columns and the compaction from each to its merged unit's row, each merged unit's input key in load order, and the merge's stats.
#[derive(Clone, Debug)]
pub struct StreamedLoad {
    pub workload: Workload,
    pub premerge: PremergeColumns,
    pub compaction: Compaction,
    pub input_keys: Vec<u8>,
    pub merge_stats: MergeStats,
}

/// What stops the stream's load: what the walk raised (an `audit_stream.OrderBreak` or a failure reading the audit), a `ValueError` with Python's text, or any other failure.
#[derive(Debug)]
pub enum WorkloadError {
    Stream(StreamError),
    Value(String),
    Failed(String),
}

impl std::fmt::Display for WorkloadError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Stream(failure) => write!(formatter, "{failure}"),
            Self::Value(message) => write!(formatter, "ValueError: {message}"),
            Self::Failed(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for WorkloadError {}

impl From<StreamError> for WorkloadError {
    fn from(failure: StreamError) -> Self {
        Self::Stream(failure)
    }
}

/// The ink digest the stream asks for each row of a window of siblings: its signature key, its window and its config.
pub type InkDigest<'d> = &'d mut dyn FnMut(u64, &str, &str) -> Result<[u8; 32]>;

/// One window's units grouped by `(baseline, new)` in the order the window's rows first name each pair.
fn window_units(found: WindowRows) -> Vec<WindowRows> {
    if found.len() == 1 {
        return vec![found];
    }
    let mut units: Vec<WindowRows> = Vec::new();
    for row in found {
        match units
            .iter_mut()
            .find(|unit| unit[0].1[4] == row.1[4] && unit[0].1[5] == row.1[5])
        {
            Some(unit) => unit.push(row),
            None => units.push(vec![row]),
        }
    }
    units
}

/// The configs of a merged run of rows, by its rows' sections.
fn run_configs(rows: &WindowRows, sections: &[Section]) -> Vec<String> {
    rows.iter()
        .map(|(index, _fields, _line)| sections[*index].config.clone())
        .collect()
}

/// Load the audit from its sorted sections one window at a time, the build's stream source (`audit.stream_workload`): `next_window` gives each window and its rows as [`Lockstep::next_window`] walks them over `sections`. Each window's rows group into units by `(baseline, new)` in the order the file first names them; a unit is classed, flagged and pooled as Python does it, a unit whose rows match two ledger classes stops the load with Python's `ValueError`, and so does a row whose class the ledger does not declare once every row is read. In a window of two or more units each row's signature is `ink_digest` of its signature key, its window and its config, and the window's units whose rows give one signature merge as [`merge_members`] merges them; with no `ink_digest` the merge is skipped. Each merged unit's input key is [`input_key`] over its run's lines. The merged units are written in window order, the pre-merge units beside them, then placed in load order by [`counting_permutation`] over the pre-merge units' classes and groups, the merged units keeping their survivors' places; the window index is the merged units in window order when every window packs into a word, and left unbuilt otherwise.
#[allow(clippy::too_many_arguments)]
pub fn stream_workload(
    mut next_window: impl FnMut() -> std::result::Result<Option<(String, WindowRows)>, StreamError>,
    audit_path: &str,
    ledger_path: &str,
    sections: &[Section],
    ledger: &[LedgerEntry],
    family_of: &[(i64, String)],
    family_keys: &[(String, String)],
    acceptance: &[String],
    mut ink_digest: Option<InkDigest<'_>>,
) -> std::result::Result<StreamedLoad, WorkloadError> {
    let mut table = UnitTable::new(0, acceptance);
    let mut lines_of = FamilyLines::new(family_of, family_keys);
    let families: HashMap<i64, String> = family_of.iter().cloned().collect();
    let exempt: HashSet<&str> = ledger
        .iter()
        .filter(|entry| entry.no_verdict)
        .map(|entry| entry.id.as_str())
        .collect();
    let exemplar_keys: HashSet<(&str, &str)> = ledger
        .iter()
        .flat_map(|entry| {
            entry
                .exemplar_keys
                .iter()
                .map(|(config, window)| (config.as_str(), window.as_str()))
        })
        .collect();
    let mut stats = MergeStats::default();
    let mut names = NamePool::new();

    let (mut class_col, mut group_col, mut configs_col, mut kinds_col) =
        (Vec::new(), Vec::new(), Vec::new(), Vec::new());
    let (mut render_col, mut baseline_col, mut new_col, mut mapping_col) =
        (Vec::new(), Vec::new(), Vec::new(), Vec::new());
    let (mut flags_col, mut start_col, mut count_col, mut rows_col) =
        (Vec::<u8>::new(), Vec::new(), Vec::<u8>::new(), Vec::new());
    let mut words: Vec<u64> = Vec::new();
    let mut keys: Vec<u8> = Vec::new();

    let (mut pre_class, mut pre_group, mut pre_exempt, mut pre_configs) =
        (Vec::new(), Vec::new(), Vec::<u8>::new(), Vec::new());
    let (mut pre_start, mut pre_count) = (Vec::new(), Vec::<u8>::new());
    let (mut post_of, mut absorbed) = (Vec::<u32>::new(), Vec::<u8>::new());

    let mut entries_seen: HashSet<String> = HashSet::new();
    let mut packed = true;
    let mut row_total = 0usize;

    while let Some((window, found)) = next_window()? {
        row_total += found.len();
        let values = parse_window(&window).map_err(|failure| WorkloadError::Value(failure.0))?;
        let offset = table.window_values.len() as u32;
        for &value in &values {
            table.window_values.push(u16::try_from(value).map_err(|_| {
                WorkloadError::Value(format!(
                    "the window {window} holds a code point a u16 column cannot"
                ))
            })?);
        }
        let length = u8::try_from(values.len()).map_err(|_| {
            WorkloadError::Value(format!(
                "the window {window} holds more code points than a byte counts"
            ))
        })?;
        let group = table.strings.id(&group_for(&values, &families));
        let word = match values
            .iter()
            .map(|&value| u32::try_from(value).ok())
            .collect::<Option<Vec<u32>>>()
            .and_then(|values| window_word(&values))
        {
            Some(word) => word,
            None => {
                packed = false;
                0
            }
        };
        let suffix = lines_of.suffix(&values);
        let mut units = window_units(found);
        let mut members: Vec<Member> = Vec::with_capacity(units.len());
        let mut identities: Vec<(u32, u32, u32, u32, bool)> = Vec::with_capacity(units.len());
        for (place, unit_rows) in units.iter().enumerate() {
            let configs = run_configs(unit_rows, sections);
            let configs_id = table.tuple_id(&configs);
            let kinds: Vec<String> = unit_rows
                .iter()
                .flat_map(|(_index, fields, _line)| fields[2].split(','))
                .map(str::to_owned)
                .collect::<BTreeSet<String>>()
                .into_iter()
                .collect();
            let kinds_id = table.tuple_id(&kinds);
            let entries: Vec<&str> = unit_rows
                .iter()
                .map(|(_index, fields, _line)| fields[3].as_str())
                .collect();
            let classes: BTreeSet<&str> = entries.iter().copied().collect();
            let matched: Vec<&str> = classes
                .iter()
                .copied()
                .filter(|class| *class != UNMATCHED)
                .collect();
            if matched.len() > 1 {
                let listed: Vec<String> = matched.iter().map(|class| repr_str(class)).collect();
                return Err(WorkloadError::Value(format!(
                    "unit {window} spans multiple matched ledger classes: [{}]",
                    listed.join(", ")
                )));
            }
            entries_seen.extend(classes.iter().map(|class| (*class).to_owned()));
            let class_name = if classes.contains(UNMATCHED) {
                UNMATCHED
            } else {
                matched[0]
            };
            let mut mapping: Vec<(String, String)> = Vec::new();
            for ((index, _fields, _line), entry) in unit_rows.iter().zip(&entries) {
                let config = &sections[*index].config;
                match mapping.iter_mut().find(|(held, _entry)| held == config) {
                    Some(pair) => pair.1 = (*entry).to_owned(),
                    None => mapping.push((config.clone(), (*entry).to_owned())),
                }
            }
            let mapping_id = table.mappings.id(&mapping);
            let class_index = table.strings.id(class_name);
            let exemplar = !exemplar_keys.is_empty()
                && configs
                    .iter()
                    .any(|config| exemplar_keys.contains(&(config.as_str(), window.as_str())));
            let exempted = exempt.contains(class_name);
            members.push(Member {
                ordinal: place,
                rank: sections[unit_rows[0].0].rank,
                configs,
                kinds,
                config_classes: mapping,
                exemplar,
                class_id: class_name.to_owned(),
                no_verdict: exempted,
                merged: false,
                survivor: None,
            });
            identities.push((class_index, configs_id, kinds_id, mapping_id, exempted));
        }
        if units.len() > 1
            && let Some(digest) = ink_digest.as_mut()
        {
            let mut by_signature: Vec<([u8; 32], Vec<usize>)> = Vec::new();
            for (place, unit_rows) in units.iter().enumerate() {
                let mut signatures: Vec<[u8; 32]> = Vec::new();
                for (_index, fields, _line) in unit_rows {
                    let found = (*digest)(signature_key(fields, &suffix), &window, &fields[0])
                        .map_err(|failure| WorkloadError::Failed(failure.0))?;
                    if !signatures.contains(&found) {
                        signatures.push(found);
                    }
                }
                if let [signature] = signatures[..] {
                    match by_signature
                        .iter_mut()
                        .find(|(held, _places)| *held == signature)
                    {
                        Some((_signature, places)) => places.push(place),
                        None => by_signature.push((signature, vec![place])),
                    }
                }
            }
            for (_signature, places) in by_signature {
                if places.len() < 2 {
                    continue;
                }
                let mut group: Vec<Member> =
                    places.iter().map(|&place| members[place].clone()).collect();
                let absorb = |survivor: &Member, other: &Member| {
                    let merged =
                        merged_rows(&units[survivor.ordinal], &units[other.ordinal], |row| row.0);
                    let configs = run_configs(&merged, sections);
                    units[survivor.ordinal] = merged;
                    configs
                };
                let merged = merge_members(
                    &mut group,
                    |class| exempt.contains(class),
                    &mut stats,
                    absorb,
                )
                .map_err(|failure| {
                    WorkloadError::Failed(match failure {
                        MergeError::NoMembers => "IndexError: list index out of range".to_owned(),
                        MergeError::NoClass => "KeyError: 'pop from an empty set'".to_owned(),
                    })
                })?;
                if merged {
                    stats.windows_merged += 1;
                }
                for member in group {
                    let place = member.ordinal;
                    members[place] = member;
                }
            }
        }
        let mut posts = vec![0u32; members.len()];
        for (member, unit_rows) in members.iter().zip(&units) {
            if member.survivor.is_some() {
                continue;
            }
            posts[member.ordinal] = class_col.len() as u32;
            let (mut class_index, mut configs_id, mut kinds_id, mut mapping_id, _exempted) =
                identities[member.ordinal];
            if member.merged {
                class_index = table.strings.id(&member.class_id);
                configs_id = table.tuple_id(&member.configs);
                kinds_id = table.tuple_id(&member.kinds);
                mapping_id = table.mappings.id(&member.config_classes);
            }
            class_col.push(class_index);
            group_col.push(group);
            configs_col.push(configs_id);
            kinds_col.push(kinds_id);
            render_col.push(
                table
                    .groups
                    .id(std::slice::from_ref(&member.configs))
                    .expect("the render groups are never sealed"),
            );
            let first = &unit_rows[0].1;
            baseline_col.push(
                names
                    .id(first[4].split('|'))
                    .expect("the names are sealed only once the stream ends"),
            );
            new_col.push(
                names
                    .id(first[5].split('|'))
                    .expect("the names are sealed only once the stream ends"),
            );
            mapping_col.push(mapping_id);
            flags_col.push(
                LIVE | if member.exemplar { EXEMPLAR } else { 0 }
                    | if member.no_verdict { NO_VERDICT } else { 0 },
            );
            start_col.push(offset);
            count_col.push(length);
            rows_col.push(unit_rows.len() as u32);
            words.push(word);
            keys.extend_from_slice(&input_key(
                unit_rows
                    .iter()
                    .map(|(_index, _fields, line)| line.as_str()),
                &suffix,
            ));
        }
        for member in &members {
            let (class_index, configs_id, _kinds_id, _mapping_id, exempted) =
                identities[member.ordinal];
            pre_class.push(class_index);
            pre_group.push(group);
            pre_exempt.push(u8::from(exempted));
            pre_configs.push(configs_id);
            pre_start.push(offset);
            pre_count.push(length);
            absorbed.push(u8::from(member.survivor.is_some()));
            post_of.push(posts[member.survivor.unwrap_or(member.ordinal)]);
        }
    }
    names.seal();
    let n_pre = pre_class.len();
    let n = class_col.len();
    let declared: HashSet<&str> = ledger.iter().map(|entry| entry.id.as_str()).collect();
    let mut unknown: Vec<&String> = entries_seen
        .iter()
        .filter(|class| class.as_str() != UNMATCHED && !declared.contains(class.as_str()))
        .collect();
    if !unknown.is_empty() {
        unknown.sort();
        let listed: Vec<&str> = unknown.iter().map(|class| class.as_str()).collect();
        return Err(WorkloadError::Value(format!(
            "{audit_path}: rows match classes {ledger_path} does not declare: {}",
            listed.join(", ")
        )));
    }

    let mut class_order: HashMap<String, usize> = HashMap::new();
    for (index, entry) in ledger.iter().enumerate() {
        class_order.insert(entry.id.clone(), index);
    }
    let ranks = family_ranks(family_of);
    let walk: Vec<u32> = (0..n_pre as u32).collect();
    let load = counting_permutation(
        &Buckets {
            class_col: &pre_class,
            group_col: &pre_group,
            strings: &table.strings,
            class_order: &class_order,
            family_rank: &ranks,
        },
        &walk,
        n_pre,
    );
    drop(walk);
    let order: Vec<u32> = load
        .iter()
        .filter(|&&pre| absorbed[pre as usize] == 0)
        .map(|&pre| post_of[pre as usize])
        .collect();
    let mut place_of = vec![0u32; n];
    for (position, &post) in order.iter().enumerate() {
        place_of[post as usize] = position as u32;
    }
    let premerge = PremergeColumns {
        class_id: gather(&pre_class, &load),
        no_verdict: gather(&pre_exempt, &load),
        configs: gather(&pre_configs, &load),
        window_start: gather(&pre_start, &load),
        window_n: gather(&pre_count, &load),
    };
    let compaction = Compaction {
        survivor: load
            .iter()
            .map(|&pre| place_of[post_of[pre as usize] as usize])
            .collect(),
        removed: gather(&absorbed, &load),
    };
    drop((
        load,
        pre_class,
        pre_group,
        pre_exempt,
        pre_configs,
        pre_start,
    ));
    drop((pre_count, post_of, absorbed));

    table.n = n;
    table.class = gather(&class_col, &order);
    table.group = gather(&group_col, &order);
    table.configs = gather(&configs_col, &order);
    table.kinds = gather(&kinds_col, &order);
    table.render_groups = gather(&render_col, &order);
    table.baseline = Some(gather(&baseline_col, &order));
    table.new = Some(gather(&new_col, &order));
    table.config_classes = gather(&mapping_col, &order);
    table.flags = gather(&flags_col, &order);
    table.window_start = gather(&start_col, &order);
    table.window_n = gather(&count_col, &order);
    table.row_count = gather(&rows_col, &order);
    drop((class_col, group_col, configs_col, kinds_col, render_col));
    drop((baseline_col, new_col, mapping_col, flags_col, start_col));
    drop((count_col, rows_col));
    table.unmatched_group = vec![0; n];
    table.duplicate_group = vec![0; n];
    table.cluster = vec![0; n];
    table.rows_start = vec![0; n];
    table.order = vec![NONE; n];
    table.batch = vec![NONE; n];
    table.survivor = vec![NONE; n];
    table.names = Some(names);
    let mut input_keys = vec![0u8; INPUT_KEY_BYTES * n];
    for (position, &post) in order.iter().enumerate() {
        let from = post as usize * INPUT_KEY_BYTES;
        input_keys[position * INPUT_KEY_BYTES..(position + 1) * INPUT_KEY_BYTES]
            .copy_from_slice(&keys[from..from + INPUT_KEY_BYTES]);
    }
    drop((keys, order));
    if packed {
        let mut index = WindowIndex {
            ordinals: place_of,
            keys: WindowKeys::Words(words),
        };
        let runs: Vec<(usize, usize)> = index.runs().collect();
        for (start, end) in runs {
            index.ordinals[start..end].sort_unstable();
        }
        table.window_index = Some(index);
    }
    let present = table.classes_present();
    let classes_present = ledger
        .iter()
        .enumerate()
        .filter(|(_index, entry)| present.contains(entry.id.as_str()))
        .map(|(index, _entry)| index)
        .collect();
    Ok(StreamedLoad {
        workload: Workload {
            table,
            row_count: row_total,
            classes_present,
        },
        premerge,
        compaction,
        input_keys,
        merge_stats: stats,
    })
}

/// A column line's count and the sha256 of its values' typed encodings, each followed by a newline, taken as the values come.
pub(crate) struct ColumnDigest {
    count: i64,
    digest: Sha256,
}

impl ColumnDigest {
    pub(crate) fn new() -> Self {
        Self {
            count: 0,
            digest: Sha256::new(),
        }
    }

    fn add_typed(&mut self, typed: &str) {
        self.digest.update(typed.as_bytes());
        self.digest.update(b"\n");
        self.count += 1;
    }

    pub(crate) fn add(&mut self, value: &PyValue) {
        self.add_typed(&value.to_typed());
    }

    /// The sha256 of the values added, as hex.
    pub(crate) fn hex(self) -> String {
        self.digest
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect()
    }

    pub(crate) fn write(self, out: &mut impl Write, name: &str) -> Result<()> {
        let digest: String = self
            .digest
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        writeln!(
            out,
            "{{\"key\":{},\"values\":{},\"sha256\":{}}}",
            json_string(&format!("column:{name}")),
            self.count,
            json_string(&digest)
        )
        .map_err(|failure| WireError(format!("cannot write a stage line: {failure}")))
    }
}

fn column_of(
    out: &mut impl Write,
    name: &str,
    values: impl IntoIterator<Item = PyValue>,
) -> Result<()> {
    let mut digest = ColumnDigest::new();
    for value in values {
        digest.add(&value);
    }
    digest.write(out, name)
}

pub(crate) fn int(value: impl Into<i64>) -> PyValue {
    PyValue::Int(value.into())
}

pub(crate) fn count_value(value: usize) -> PyValue {
    PyValue::Int(value as i64)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// A merged unit as the `workload` stage holds it: the unit [`UnitTable::unit`] materializes without a store, whether it is live, its survivor, its config rank, its row of the id columns and its input key as hex.
pub(crate) fn workload_unit(table: &UnitTable, input_keys: &[u8], ordinal: usize) -> PyValue {
    let ids = table
        .ids(ordinal)
        .iter()
        .map(|id| id.map_or(PyValue::None, int))
        .collect();
    PyValue::Tuple(vec![
        table.unit(ordinal, None).to_py(),
        PyValue::Bool(table.live(ordinal)),
        int(table.survivor(ordinal)),
        int(table.config_rank(ordinal)),
        PyValue::Tuple(ids),
        PyValue::str(hex(
            &input_keys[ordinal * INPUT_KEY_BYTES..(ordinal + 1) * INPUT_KEY_BYTES]
        )),
    ])
}

/// Write the `workload` stage's lines over what [`stream_workload`] gave: a `unit:<ordinal>` line for each ordinal in `units`, then the columns and the further lines the Python stage's `workload_boundary` lays out.
fn workload_lines(
    loaded: &StreamedLoad,
    ledger: &[LedgerEntry],
    units: &HashSet<usize>,
    out: &mut impl Write,
) -> Result<()> {
    let table = &loaded.workload.table;
    let mut digest = ColumnDigest::new();
    for ordinal in 0..table.n {
        let value = workload_unit(table, &loaded.input_keys, ordinal);
        digest.add(&value);
        if units.contains(&ordinal) {
            stage_line(out, &format!("{UNIT_PREFIX}{ordinal}"), &value)?;
        }
    }
    digest.write(out, "value")?;
    column_of(
        out,
        "strings",
        (0..=table.strings.len()).map(|id| PyValue::str(table.strings.get(id as u32))),
    )?;
    column_of(
        out,
        "tuples",
        (0..=table.tuples.len()).map(|id| PyValue::str_tuple(table.tuples.get(id as u32))),
    )?;
    column_of(
        out,
        "groups",
        (0..=table.groups.len()).map(|id| {
            PyValue::Tuple(
                table
                    .groups
                    .get(id as u32)
                    .iter()
                    .map(|group| PyValue::str_tuple(group))
                    .collect(),
            )
        }),
    )?;
    column_of(
        out,
        "mappings",
        (0..=table.mappings.len()).map(|id| mapping_value(table.mappings.get(id as u32))),
    )?;
    let names: Vec<PyValue> = match &table.names {
        Some(names) => (0..=names.len())
            .map(|id| PyValue::str_tuple(&names.get(id as u32)))
            .collect(),
        None => Vec::new(),
    };
    column_of(out, "names", names)?;
    column_of(out, "ranks", table.ranks().iter().map(|&rank| int(rank)))?;
    column_of(
        out,
        "window-values",
        table.window_values().iter().map(|&value| int(value)),
    )?;
    let index = table.held_window_index();
    column_of(
        out,
        "window-ordinals",
        index
            .map(|index| index.ordinals.clone())
            .unwrap_or_default()
            .into_iter()
            .map(int),
    )?;
    let mut keys = ColumnDigest::new();
    match index.map(|index| &index.keys) {
        Some(WindowKeys::Words(words)) => {
            for word in words {
                keys.add_typed(&word.to_string());
            }
        }
        Some(WindowKeys::Windows(windows)) => {
            for window in windows {
                keys.add(&PyValue::Tuple(vec![
                    count_value(window.len()),
                    PyValue::Tuple(window.iter().map(|&value| int(value)).collect()),
                ]));
            }
        }
        None => {}
    }
    keys.write(out, "window-keys")?;
    let premerge = &loaded.premerge;
    column_of(
        out,
        "premerge",
        (0..premerge.class_id.len()).map(|at| {
            PyValue::Tuple(vec![
                int(premerge.class_id[at]),
                PyValue::Bool(premerge.no_verdict[at] != 0),
                int(premerge.configs[at]),
                int(premerge.window_start[at]),
                int(premerge.window_n[at]),
            ])
        }),
    )?;
    let compaction = &loaded.compaction;
    column_of(
        out,
        "compaction",
        compaction
            .survivor
            .iter()
            .zip(&compaction.removed)
            .map(|(&survivor, &removed)| {
                PyValue::Tuple(vec![int(survivor), PyValue::Bool(removed != 0)])
            }),
    )?;
    column_of(
        out,
        "rows-by-class",
        table
            .rows_by_class(0..table.n as u32)
            .into_iter()
            .map(|(class, members)| {
                PyValue::Tuple(vec![
                    PyValue::str(class),
                    PyValue::Tuple(members.into_iter().map(int).collect()),
                ])
            }),
    )?;
    let pools = PyValue::Tuple(vec![
        count_value(table.strings.len()),
        count_value(table.strings.chars()),
        count_value(table.tuples.len()),
        count_value(table.tuples.elements()),
        count_value(table.groups.len()),
        count_value(table.groups.elements()),
        count_value(table.mappings.len()),
        count_value(table.mappings.elements()),
        table.names.as_ref().map_or(PyValue::None, |names| {
            PyValue::Tuple(vec![
                count_value(names.len()),
                count_value(names.elements()),
                PyValue::Bool(names.sealed()),
            ])
        }),
    ]);
    stage_line(out, POOLS_KEY, &pools)?;
    let shape = index.map_or(PyValue::None, |index| {
        PyValue::Tuple(vec![
            PyValue::Bool(index.packed()),
            count_value(index.windows()),
        ])
    });
    stage_line(out, INDEX_KEY, &shape)?;
    stage_line(out, MERGE_KEY, &loaded.merge_stats.to_py())?;
    stage_line(out, ROWS_KEY, &count_value(loaded.workload.row_count))?;
    stage_line(
        out,
        REACHED_KEY,
        &PyValue::Tuple(
            loaded
                .workload
                .classes_present
                .iter()
                .map(|&index| PyValue::str(&ledger[index].id))
                .collect(),
        ),
    )?;
    stage_line(
        out,
        PRESENT_KEY,
        &PyValue::List(
            table
                .classes_present()
                .into_iter()
                .map(PyValue::str)
                .collect(),
        ),
    )?;
    stage_line(
        out,
        GROUPS_KEY,
        &PyValue::Dict(
            table
                .unmatched_group_counts()
                .into_iter()
                .map(|(group, count)| (PyValue::str(group), int(count)))
                .collect(),
        ),
    )
}

pub(crate) const UNIT_PREFIX: &str = "unit:";
/// The keys of the `workload` stage's lines beside its units and columns (`units_stage_fixtures.WORKLOAD_*_KEY`).
const POOLS_KEY: &str = "pools";
const INDEX_KEY: &str = "window-index";
const MERGE_KEY: &str = "merge";
const ROWS_KEY: &str = "rows";
const REACHED_KEY: &str = "classes-reached";
const PRESENT_KEY: &str = "classes-present";
const GROUPS_KEY: &str = "unmatched-groups";

/// The `unit:<ordinal>` lines a boundary stage's file asks for, and how many lines it holds.
pub(crate) fn stage_units(stage_file: &Path) -> Result<(HashSet<usize>, usize)> {
    let read = |failure: std::io::Error| {
        WireError(format!("cannot read {}: {failure}", stage_file.display()))
    };
    let mut units: HashSet<usize> = HashSet::new();
    let mut lines = 0usize;
    for line in BufReader::new(File::open(stage_file).map_err(read)?).lines() {
        let line = line.map_err(read)?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| WireError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return error(format!("a stage line has no key: {line:.200}"));
        };
        if let Some(ordinal) = key.strip_prefix(UNIT_PREFIX) {
            units.insert(
                ordinal
                    .parse()
                    .map_err(|_| WireError(format!("the key {key} names no ordinal")))?,
            );
        }
        lines += 1;
    }
    Ok((units, lines))
}

/// Load the audit the build input `input` names as the build's stream does, for the build of the dump `stage_file` lies in: read through the descriptors the process inherits and laid out as [`audit_layout`] lays it out, its signatures resolved as the build resolves them before the stream (the store the dump's build loads, [`find_misses`] at the vocabulary's `KEY_ONLY_PROCESSES` ranges on the input's threads, the misses shaped on its signature threads) and the stream's digests looked up through the late shaper ([`stream_workload`]).
pub(crate) fn dump_stream(input: &BuildInput, stage_file: &Path) -> Result<StreamedLoad> {
    let vocabulary = Vocabulary::of(input)?;
    let names = StoreNames::of(input)?;
    let count = key_only_count(input)?;
    let files = input.held_audit()?;
    let layout = audit_layout(&files, &input.audit.path, &input.audit.members, &vocabulary)
        .map_err(|failure| WireError(format!("cannot lay out the audit: {failure}")))?;
    let Some(sections) = layout.sections else {
        return error(format!(
            "the audit is not in sorted sections: {}",
            layout.why
        ));
    };
    let held = HeldWindows {
        files: &files,
        sections: &sections,
        family_of: &input.family_of,
        family_keys: &input.family_keys,
    };
    let width = |value: i64| usize::try_from(value).unwrap_or(1);
    let mut signatures = dump_store(input, &names, stage_file)?;
    let mut found = find_misses(
        held,
        &mut signatures,
        &input.audit.identity,
        &input.family_keys_digest,
        count,
        width(input.widths.threads),
    )
    .map_err(|failure| WireError(format!("the key-only read failed: {failure}")))?;
    if let Some(why) = &found.broken {
        return error(format!(
            "the key-only read broke, so the build reads no stream: {why}"
        ));
    }
    let shaping = InkShaping::open(Path::new(&input.units_init))?;
    shaping.shape_found_misses(
        &mut found,
        &sections,
        &mut signatures,
        width(input.widths.signature_threads),
    )?;
    let mut late = shaping.late(&mut signatures);
    let digest: InkDigest<'_> =
        &mut |key: u64, window: &str, config: &str| late.digest(key, window, config);
    let mut stream = Lockstep::new(&files, &sections, None, None)
        .map_err(|failure| WireError(format!("cannot walk the audit: {failure}")))?;
    stream_workload(
        || stream.next_window(),
        &input.audit.path,
        &input.ledger_path,
        &sections,
        &input.ledger,
        &input.family_of,
        &input.family_keys,
        &vocabulary.configs,
        Some(digest),
    )
    .map_err(|failure| WireError(format!("the stream's load failed: {failure}")))
}

/// The `workload` stage: load the audit its build input names as the build's stream does ([`dump_stream`]), and answer with the lines `workload_lines` writes, a `unit:<ordinal>` line for each one the stage file asks for. A stage file with no lines, from a build whose stream did not run, is answered with none.
pub fn stage_workload(input_path: &Path, stage_file: &Path, out: &mut impl Write) -> Result<()> {
    let input = BuildInput::read(input_path)?;
    let (units, lines) = stage_units(stage_file)?;
    if lines == 0 {
        return Ok(());
    }
    let loaded = dump_stream(&input, stage_file)?;
    workload_lines(&loaded, &input.ledger, &units, out)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// A string table over `strings` in order after the empty one, with their ids.
    fn table(strings: &[&str]) -> (StringTable, Vec<u32>) {
        let mut table = StringTable::new();
        let ids = strings.iter().map(|string| table.id(string)).collect();
        (table, ids)
    }

    fn ranks() -> FamilyRanks {
        family_ranks(&[
            (0xE650, "qsPea".to_owned()),
            (0xE652, "qsTea".to_owned()),
            (0xE670, "qsIt".to_owned()),
        ])
    }

    #[test]
    fn family_ranks_keep_a_familys_first_place_and_its_last_code_point() {
        let ranks = family_ranks(&[
            (1, "a".to_owned()),
            (2, "b".to_owned()),
            (3, "a".to_owned()),
        ]);
        assert_eq!(ranks.entries(), [("a".to_owned(), 3), ("b".to_owned(), 2)]);
        assert_eq!(ranks.group("b:c:a"), [2, UNRANKED_FAMILY, 3]);
        assert_eq!(ranks.group(""), [UNRANKED_FAMILY]);
    }

    #[test]
    fn triage_keys_order_by_class_group_window_and_id() {
        let ranks = ranks();
        let key =
            |class, group, codepoints: &[u32], id| triage_key(class, group, codepoints, id, &ranks);
        let mut keys = [
            key(1, "qsPea:qsTea", &[0xE650, 0xE652], "u-a"),
            key(0, "qsTea:qsPea", &[0xE652, 0xE650], "u-b"),
            key(0, "qsPea:qsTea", &[0x20, 0xE650, 0xE652], "u-c"),
            key(0, "qsPea:qsTea", &[0xE650, 0xE652], "u-é"),
            key(0, "qsPea:qsTea", &[0xE650, 0xE652], "u-d"),
            key(0, "qsPea:qsNope", &[0xE650, 0xE652], "u-e"),
        ];
        keys.sort();
        let ids: Vec<&str> = keys.iter().map(|key| key.unit_id.as_str()).collect();
        assert_eq!(ids, ["u-d", "u-é", "u-c", "u-e", "u-b", "u-a"]);
    }

    /// Rows of two classes and two groups over windows given in window order, with their windows' columns, as a table the in-memory source loads holds them.
    struct Tied {
        strings: StringTable,
        class_col: Vec<u32>,
        group_col: Vec<u32>,
        counts: Vec<u8>,
        starts: Vec<u32>,
        values: Vec<u16>,
        walk: Vec<u32>,
        runs: Vec<(usize, usize)>,
        words: Vec<u64>,
    }

    fn tied() -> Tied {
        let (strings, ids) = table(&[
            "class-a",
            "class-b",
            "qsPea:qsTea",
            "qsTea:qsPea",
            "class-z",
        ]);
        let windows: [&[u16]; 4] = [
            &[0xE650, 0xE652],
            &[0xE652, 0xE650],
            &[0x20, 0xE650, 0xE652],
            &[0xE650, 0xE652, 0xE650],
        ];
        let (mut class_col, mut group_col, mut counts, mut starts, mut values, mut words) =
            (vec![], vec![], vec![], vec![], vec![], vec![]);
        for ordinal in 0..48u32 {
            let window = windows[(ordinal as usize * 7) % windows.len()];
            class_col.push(ids[[0, 1, 4][ordinal as usize % 3]]);
            group_col.push(ids[2 + (window[0] == 0xE652) as usize]);
            counts.push(window.len() as u8);
            starts.push(values.len() as u32);
            values.extend_from_slice(window);
            words.push(if ordinal % 5 == 0 {
                7 << 56
            } else {
                u64::from(ordinal).wrapping_mul(0x9E37_79B9_7F4A_7C15)
            });
        }
        let mut walk: Vec<u32> = (0..48).collect();
        let window_of = |ordinal: u32| {
            let at = ordinal as usize;
            (
                counts[at],
                &values[starts[at] as usize..starts[at] as usize + counts[at] as usize],
            )
        };
        walk.sort_by_key(|&ordinal| window_of(ordinal));
        let mut runs = vec![];
        let mut start = 0;
        for position in 1..=walk.len() {
            if position == walk.len() || window_of(walk[position]) != window_of(walk[start]) {
                if position - start > 1 {
                    runs.push((start, position));
                }
                start = position;
            }
        }
        Tied {
            strings,
            class_col,
            group_col,
            counts,
            starts,
            values,
            walk,
            runs,
            words,
        }
    }

    #[test]
    fn the_counting_sort_is_the_comparison_sorts_permutation_ties_included() {
        let tied = tied();
        let ranks = ranks();
        let order: HashMap<String, usize> =
            [("class-b".to_owned(), 0), ("class-a".to_owned(), 1)].into();
        let buckets = Buckets {
            class_col: &tied.class_col,
            group_col: &tied.group_col,
            strings: &tied.strings,
            class_order: &order,
            family_rank: &ranks,
        };
        let windows = Windows {
            counts: &tied.counts,
            starts: &tied.starts,
            values: &tied.values,
        };
        let n = tied.class_col.len();
        let id_word = |ordinal: u32| tied.words[ordinal as usize];
        let by_id =
            counting_permutation_by_id(&buckets, &tied.walk, n, tied.runs.iter().copied(), id_word);
        assert_eq!(
            by_id,
            triage_permutation(&buckets, &windows, n, Some(&id_word))
        );
        let plain = counting_permutation(&buckets, &tied.walk, n);
        assert_eq!(plain, triage_permutation(&buckets, &windows, n, None));
        assert_ne!(plain, by_id);
        let mut sorted = by_id.clone();
        sorted.sort_unstable();
        assert_eq!(sorted, (0..n as u32).collect::<Vec<_>>());
        assert_eq!(
            tied.strings.get(tied.class_col[by_id[0] as usize]),
            "class-b"
        );
        assert_eq!(
            tied.strings.get(tied.class_col[by_id[n - 1] as usize]),
            "class-z"
        );
    }

    #[test]
    fn nothing_sorts_to_nothing_and_a_short_walk_leaves_its_places_zero() {
        let (strings, _ids) = table(&[]);
        let (order, ranks) = (HashMap::new(), FamilyRanks::default());
        let empty = Buckets {
            class_col: &[],
            group_col: &[],
            strings: &strings,
            class_order: &order,
            family_rank: &ranks,
        };
        assert!(counting_permutation(&empty, &[], 0).is_empty());
        let three = Buckets {
            class_col: &[0, 0, 0],
            group_col: &[0, 0, 0],
            ..empty
        };
        assert_eq!(counting_permutation(&three, &[2], 3), [2, 0, 0]);
    }

    fn sibling(ordinal: usize, rank: i64, classes: &[(&str, &str)], kinds: &[&str]) -> Member {
        Member {
            ordinal,
            rank,
            configs: classes
                .iter()
                .map(|(config, _)| (*config).to_owned())
                .collect(),
            kinds: kinds.iter().map(|kind| (*kind).to_owned()).collect(),
            config_classes: classes
                .iter()
                .map(|(config, class)| ((*config).to_owned(), (*class).to_owned()))
                .collect(),
            exemplar: false,
            class_id: String::new(),
            no_verdict: false,
            merged: false,
            survivor: None,
        }
    }

    #[test]
    fn rows_merge_by_key_with_the_first_runs_row_ahead_at_a_tie() {
        let first = [(0, "a"), (2, "b"), (2, "c")];
        let second = [(1, "x"), (2, "y"), (5, "z")];
        let merged = merged_rows(&first, &second, |row| row.0);
        assert_eq!(
            merged,
            [(0, "a"), (1, "x"), (2, "b"), (2, "c"), (2, "y"), (5, "z")]
        );
    }

    #[test]
    fn the_earliest_member_absorbs_until_two_matched_classes_meet() {
        let mut members = vec![
            sibling(2, 2, &[("ss02", "class-b")], &["b"]),
            sibling(1, 1, &[("ss01", UNMATCHED)], &["é", "a"]),
            sibling(0, 0, &[("default", "class-a")], &["z"]),
            sibling(3, 1, &[("ss03", UNMATCHED)], &["a"]),
        ];
        members[3].exemplar = true;
        let mut stats = MergeStats::default();
        let mut absorbed = Vec::new();
        let merged = merge_members(
            &mut members,
            |class| class == UNMATCHED,
            &mut stats,
            |survivor, other| {
                absorbed.push((survivor.ordinal, other.ordinal));
                vec![format!("{}+{}", survivor.ordinal, other.ordinal)]
            },
        );
        assert_eq!(merged, Ok(true));
        assert_eq!(absorbed, [(0, 1), (0, 3)]);
        let order: Vec<usize> = members.iter().map(|member| member.ordinal).collect();
        assert_eq!(order, [0, 1, 3, 2]);
        let survivor = &members[0];
        assert_eq!(survivor.configs, ["0+3"]);
        assert_eq!(survivor.kinds, ["a", "z", "é"]);
        assert_eq!(
            survivor.config_classes,
            [
                ("default".to_owned(), "class-a".to_owned()),
                ("ss01".to_owned(), UNMATCHED.to_owned()),
                ("ss03".to_owned(), UNMATCHED.to_owned())
            ]
        );
        assert_eq!(survivor.class_id, UNMATCHED);
        assert!(survivor.no_verdict && survivor.exemplar && survivor.merged);
        assert_eq!(members[2].survivor, Some(0));
        assert_eq!(members[3].survivor, None);
        assert_eq!(
            stats,
            MergeStats {
                windows_merged: 0,
                units_merged: 2,
                kept_split_matched_classes: 1,
            }
        );
    }

    #[test]
    fn a_merge_with_no_class_to_take_fails_as_python_does() {
        let mut stats = MergeStats::default();
        let configs = |_survivor: &Member, _other: &Member| Vec::new();
        assert_eq!(
            merge_members(&mut [], |_class| false, &mut stats, configs),
            Err(MergeError::NoMembers)
        );
        let mut members = [sibling(0, 0, &[], &[]), sibling(1, 0, &[], &[])];
        assert_eq!(
            merge_members(&mut members, |_class| false, &mut stats, configs),
            Err(MergeError::NoClass)
        );
    }

    fn section(config: &str, rank: i64) -> Section {
        Section {
            config: config.to_owned(),
            rank,
            start: 0,
            end: 0,
            part: 0,
            inflated: None,
        }
    }

    fn row(section: usize, fields: [&str; 6]) -> (usize, Vec<String>, String) {
        (
            section,
            fields.iter().map(|field| (*field).to_owned()).collect(),
            fields.join("\t"),
        )
    }

    fn entry(id: &str, no_verdict: bool, exemplar_keys: &[(&str, &str)]) -> LedgerEntry {
        LedgerEntry {
            id: id.to_owned(),
            status: "per-unit".to_owned(),
            why: String::new(),
            ink_identical: false,
            no_verdict,
            exemplar_keys: exemplar_keys
                .iter()
                .map(|(config, window)| ((*config).to_owned(), (*window).to_owned()))
                .collect(),
            title: String::new(),
            summary: String::new(),
            note: String::new(),
        }
    }

    fn acceptance() -> Vec<String> {
        ["default", "ss03", "ss04"].map(str::to_owned).to_vec()
    }

    fn families() -> Vec<(i64, String)> {
        vec![(0xE650, "qsPea".to_owned()), (0xE652, "qsTea".to_owned())]
    }

    fn family_keys() -> Vec<(String, String)> {
        vec![
            ("qsPea".to_owned(), "k1".to_owned()),
            ("qsTea".to_owned(), "k2".to_owned()),
        ]
    }

    /// Load `windows` in order as the stream loads them, every row's digest its window's unless the window is `E652`, where it is its config's.
    pub(crate) fn load(
        windows: Vec<(&str, WindowRows)>,
        ledger: &[LedgerEntry],
    ) -> std::result::Result<StreamedLoad, WorkloadError> {
        let sections = [
            section("default", 0),
            section("ss03", 1),
            section("ss04", 2),
        ];
        let mut windows = windows
            .into_iter()
            .map(|(window, rows)| (window.to_owned(), rows));
        let mut digest = |_key: u64, window: &str, config: &str| -> Result<[u8; 32]> {
            let seed = if window == "E652" { config } else { window };
            Ok(Sha256::digest(seed.as_bytes()).into())
        };
        stream_workload(
            || Ok(windows.next()),
            "audit.tsv",
            "ledger.yaml",
            &sections,
            ledger,
            &families(),
            &family_keys(),
            &acceptance(),
            Some(&mut digest),
        )
    }

    pub(crate) fn mini_windows() -> Vec<(&'static str, WindowRows)> {
        vec![
            (
                "E650",
                vec![row(0, ["default", "E650", "k", "class-b", "a", "b"])],
            ),
            (
                "E652",
                vec![
                    row(0, ["default", "E652", "k", "class-a", "a", "b"]),
                    row(1, ["ss03", "E652", "k", "class-b", "c", "d"]),
                ],
            ),
            (
                "E650:E652",
                vec![
                    row(0, ["default", "E650:E652", "z,k", UNMATCHED, "a|x", "b"]),
                    row(1, ["ss03", "E650:E652", "j", "class-a", "c", "d"]),
                    row(2, ["ss04", "E650:E652", "k", UNMATCHED, "a|x", "b"]),
                ],
            ),
        ]
    }

    pub(crate) fn mini_ledger() -> Vec<LedgerEntry> {
        vec![
            entry("class-a", false, &[("ss03", "E650:E652")]),
            entry("class-b", true, &[]),
        ]
    }

    #[test]
    fn the_stream_merges_ink_duplicates_and_places_the_units_in_load_order() {
        let loaded = load(mini_windows(), &mini_ledger()).expect("the stream loads");
        assert_eq!(
            loaded.merge_stats,
            MergeStats {
                windows_merged: 1,
                units_merged: 1,
                kept_split_matched_classes: 0,
            }
        );
        let table = &loaded.workload.table;
        assert_eq!(table.n, 4);
        assert_eq!(loaded.workload.row_count, 6);
        assert_eq!(loaded.workload.classes_present, [0, 1]);
        let windows: Vec<String> = (0..4).map(|at| table.codepoints_text(at)).collect();
        assert_eq!(windows, ["E652", "E650", "E652", "E650:E652"]);
        let classes: Vec<&str> = (0..4).map(|at| table.class_id(at)).collect();
        assert_eq!(classes, ["class-a", "class-b", "class-b", UNMATCHED]);
        assert!(table.no_verdict(1) && table.no_verdict(2) && !table.no_verdict(3));
        assert_eq!(table.config_rank(2), 1);
        let merged = table.unit(3, None);
        assert_eq!(merged.configs, ["default", "ss03", "ss04"]);
        assert_eq!(merged.kinds, ["j", "k", "z"]);
        assert_eq!(merged.baseline, ["a", "x"]);
        assert_eq!(merged.render_groups, [vec!["default", "ss03", "ss04"]]);
        assert_eq!(merged.row_count, 3);
        assert!(merged.exemplar && !merged.no_verdict);
        assert_eq!(
            merged.config_classes,
            [
                ("default".to_owned(), UNMATCHED.to_owned()),
                ("ss04".to_owned(), UNMATCHED.to_owned()),
                ("ss03".to_owned(), "class-a".to_owned()),
            ]
        );
        assert_eq!(merged.group, "qsPea:qsTea");
        assert_eq!(
            loaded.compaction,
            Compaction {
                survivor: vec![3, 0, 1, 2, 3],
                removed: vec![1, 0, 0, 0, 0],
            }
        );
        assert_eq!(loaded.premerge.no_verdict, [0, 0, 1, 1, 0]);
        let index = table.held_window_index().expect("every window packs");
        assert_eq!(index.ordinals, [1, 0, 2, 3]);
        assert_eq!(
            &loaded.input_keys[..INPUT_KEY_BYTES],
            input_key(["default\tE652\tk\tclass-a\ta\tb"], "\nqsTea\tk2")
        );
        assert_eq!(
            table.rows_by_class([3, 0, 2, 1]),
            [
                (UNMATCHED.to_owned(), vec![3]),
                ("class-a".to_owned(), vec![0]),
                ("class-b".to_owned(), vec![2, 1]),
            ]
        );
    }

    #[test]
    fn a_unit_of_two_matched_classes_and_an_undeclared_class_fail_as_python_does() {
        let split = vec![(
            "E650",
            vec![
                row(0, ["default", "E650", "k", "class-b", "a", "b"]),
                row(1, ["ss03", "E650", "k", "class-a", "a", "b"]),
            ],
        )];
        match load(split, &mini_ledger()) {
            Err(WorkloadError::Value(message)) => assert_eq!(
                message,
                "unit E650 spans multiple matched ledger classes: ['class-a', 'class-b']"
            ),
            other => panic!("the split unit loaded: {other:?}"),
        }
        let undeclared = vec![(
            "E650",
            vec![row(0, ["default", "E650", "k", "class-z", "a", "b"])],
        )];
        match load(undeclared, &mini_ledger()) {
            Err(WorkloadError::Value(message)) => assert_eq!(
                message,
                "audit.tsv: rows match classes ledger.yaml does not declare: class-z"
            ),
            other => panic!("the undeclared class loaded: {other:?}"),
        }
    }

    #[test]
    fn a_compaction_drops_merged_rows_and_carries_the_window_index() {
        let mut table = load(mini_windows(), &mini_ledger())
            .expect("the stream loads")
            .workload
            .table;
        assert!(table.merge_into(0, 2).is_ok());
        assert!(table.merge_into(1, 0).is_err());
        assert_eq!(table.survivor(0), 2);
        let compaction = table.compact();
        assert_eq!(compaction.survivor, [1, 0, 1, 2]);
        assert_eq!(compaction.removed, [1, 0, 0, 0]);
        assert_eq!(table.n, 3);
        assert_eq!(table.class_id(0), "class-b");
        assert_eq!(
            table
                .held_window_index()
                .map(|index| index.ordinals.clone()),
            Some(vec![0, 1, 2])
        );
        assert_eq!(table.baseline(2), ["a", "x"]);
        table.release_names();
        assert!(table.baseline(2).is_empty() && table.new_names(2).is_empty());
        table.release_window_index();
        assert_eq!(table.window_index().ordinals, [0, 1, 2]);
    }

    #[test]
    fn groups_and_render_groups_follow_the_first_two_families_and_the_first_named_pair() {
        let families: HashMap<i64, String> = families().into_iter().collect();
        assert_eq!(
            group_for(&[0x20, 0xE652, 0xE650, 0xE650], &families),
            "qsTea:qsPea"
        );
        assert_eq!(group_for(&[0x20], &families), BOUNDARIES_GROUP);
        let groups = render_groups_for_rows([
            (("a", "b"), "default".to_owned()),
            (("c", "d"), "ss03".to_owned()),
            (("a", "b"), "ss04".to_owned()),
        ]);
        assert_eq!(groups, [vec!["default", "ss04"], vec!["ss03"]]);
    }
}
