//! The orders a corpus build puts its unit table's rows in, as `rebuild/review/audit.py` computes them. [`family_ranks`] maps each family to its code point, [`triage_key`] is the order a corpus pages its human units in, and [`counting_permutation`] and [`counting_permutation_by_id`] place a table's rows in that order with a counting sort over (class, group) buckets walked in window order, the permutation [`triage_permutation`] gives by sorting one key a row. [`merge_members`] merges a window's ink-duplicate units as `audit._merge_members` does, over runs of rows [`merged_rows`] merges.
//!
//! [`stage_permutation`] is the `permutation` stage of the stage fixtures, and [`stage_merge`] the `merge` stage.

use std::collections::{BTreeSet, HashMap};
use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;

use serde_json::{Value, json};

use super::columns::StringTable;
use crate::change_groups::UNMATCHED;
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

#[cfg(test)]
mod tests {
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
}
