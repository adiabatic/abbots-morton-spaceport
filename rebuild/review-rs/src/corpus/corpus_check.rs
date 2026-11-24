//! The corpus check the build's write runs over the units it writes, in its `m1-audit` mode at `PATCHED`: each fragment fed in shard order is checked per unit at `PATCHED` unless the unit cache supplied it ([`PatchedCheck`]), each class against its manifest record, and the whole corpus against the manifest's totals, its triage index and batches, its machine-approved counts, its duplicate groups and clusters, its secondary junctions' primary units and the policy files its fragments name.
//!
//! [`CorpusCheck`] is the accumulator one run of the write feeds: [`CorpusCheck::class_start`] with the class's manifest record, [`CorpusCheck::unit`] for each fragment, [`CorpusCheck::class_end`] after the class's last, and [`CorpusCheck::take_facts`] to hand what it has accumulated over as [`Facts`], which one checker folds class by class in shard order ([`CorpusCheck::absorb`]) before [`CorpusCheck::finish`] runs the cross-unit predicates over the manifest. It reads each fragment and record as the [`PyValue`] it is, so where Python hashes, compares, iterates or `.get`s a value of another type, it answers as Python does: the same errors, or a raise where Python raises ([`CheckError::raised`]).

use std::collections::{BTreeSet, HashMap};
use std::hash::{DefaultHasher, Hash, Hasher};
use std::path::PathBuf;

use super::input::BuildInput;
use super::table::{FamilyRanks, family_ranks};
use crate::check::{
    PatchedCheck, Result, engine, hashable, int_of, is_true, members, raise, sorted, text_of,
    truthy,
};
use crate::fragment::{lookup, py_eq};
use crate::pyfmt::PyValue;
use crate::pyfmt::repr::repr;
use crate::subset_pack::python_hex_int;

/// The secondary-junction counts `finish` compares with the manifest's, in Python's order.
const JUNCTION_COUNTS: [&str; 3] = [
    "units_with_markers",
    "junctions_with_primary_unit",
    "junctions_without_primary_unit",
];

/// Python's hash of a hashable value, consistent with [`py_eq`]: a bool hashes as the int it counts as. An unhashable value raises TypeError.
fn hash_of(value: &PyValue) -> Result<u64> {
    if !hashable(value) {
        return raise("TypeError", format!("unhashable type: {}", value.kind()));
    }
    let mut hasher = DefaultHasher::new();
    feed(value, &mut hasher);
    Ok(hasher.finish())
}

fn feed(value: &PyValue, hasher: &mut DefaultHasher) {
    match value {
        PyValue::None => 0u8.hash(hasher),
        PyValue::Bool(flag) => {
            1u8.hash(hasher);
            i64::from(*flag).hash(hasher);
        }
        PyValue::Int(number) => {
            1u8.hash(hasher);
            number.hash(hasher);
        }
        PyValue::Str(text) => {
            2u8.hash(hasher);
            text.hash(hasher);
        }
        PyValue::Bytes(bytes) => {
            3u8.hash(hasher);
            bytes.hash(hasher);
        }
        PyValue::Tuple(items) => {
            4u8.hash(hasher);
            items.len().hash(hasher);
            for item in items {
                feed(item, hasher);
            }
        }
        PyValue::List(_) | PyValue::Dict(_) | PyValue::Set(_) | PyValue::Record(..) => {
            5u8.hash(hasher)
        }
    }
}

const NO_ENTRY: usize = usize::MAX;

/// A Python dict keyed by hashable values: entries in insertion order, a key matched by [`py_eq`], so a key keeps the first value that took its place and an assignment keeps its position. With `()` values it is a Python set.
#[derive(Clone, Debug)]
pub struct PyMap<V> {
    keys: Vec<PyValue>,
    values: Vec<V>,
    heads: HashMap<u64, usize>,
    next: Vec<usize>,
}

impl<V> Default for PyMap<V> {
    fn default() -> Self {
        Self {
            keys: Vec::new(),
            values: Vec::new(),
            heads: HashMap::new(),
            next: Vec::new(),
        }
    }
}

impl<V> PyMap<V> {
    fn find(&self, key: &PyValue, hash: u64) -> Option<usize> {
        let mut at = self.heads.get(&hash).copied().unwrap_or(NO_ENTRY);
        while at != NO_ENTRY {
            if py_eq(&self.keys[at], key) {
                return Some(at);
            }
            at = self.next[at];
        }
        None
    }

    fn push(&mut self, key: PyValue, hash: u64, value: V) -> usize {
        let at = self.keys.len();
        self.next
            .push(self.heads.insert(hash, at).unwrap_or(NO_ENTRY));
        self.keys.push(key);
        self.values.push(value);
        at
    }

    pub fn len(&self) -> usize {
        self.keys.len()
    }

    pub fn is_empty(&self) -> bool {
        self.keys.is_empty()
    }

    /// `key in self`.
    pub fn contains(&self, key: &PyValue) -> Result<bool> {
        Ok(self.find(key, hash_of(key)?).is_some())
    }

    /// `self.get(key)`.
    pub fn get(&self, key: &PyValue) -> Result<Option<&V>> {
        Ok(self.find(key, hash_of(key)?).map(|at| &self.values[at]))
    }

    /// `self.setdefault(key, default())`, the value at the key.
    pub fn entry(&mut self, key: &PyValue, default: impl FnOnce() -> V) -> Result<&mut V> {
        let hash = hash_of(key)?;
        let at = match self.find(key, hash) {
            Some(at) => at,
            None => self.push(key.clone(), hash, default()),
        };
        Ok(&mut self.values[at])
    }

    /// `self[key] = value`.
    pub fn insert(&mut self, key: PyValue, value: V) -> Result<()> {
        let hash = hash_of(&key)?;
        match self.find(&key, hash) {
            Some(at) => self.values[at] = value,
            None => {
                self.push(key, hash, value);
            }
        }
        Ok(())
    }

    pub fn iter(&self) -> impl Iterator<Item = (&PyValue, &V)> {
        self.keys.iter().zip(&self.values)
    }

    pub fn keys(&self) -> &[PyValue] {
        &self.keys
    }

    pub fn values(&self) -> &[V] {
        &self.values
    }
}

impl PyMap<()> {
    /// `self.add(key)`.
    pub fn add(&mut self, key: &PyValue) -> Result<()> {
        self.entry(key, || ()).map(|_| ())
    }
}

/// What a checker accumulates from the units fed to it, `_CorpusCheck._FACTS`: the errors in order, the units and rows of its closed classes, the ids it saw, each class's machine-approved count, each secondary junction's unit and primary unit, the junction counts, each duplicate group's and each cluster's (class, configs) keys and each duplicate group's first cluster, each human unit's class, group and window, each kept unit's identity (its window, whether it has a primary pair, and whether it is ink- or picture-identical), and the policy files the fragments name.
#[derive(Clone, Debug, Default)]
pub struct Facts {
    pub errors: Vec<String>,
    seen_units: i64,
    seen_rows: i64,
    seen_ids: PyMap<()>,
    seen_machine_by_class: PyMap<i64>,
    primary_units: Vec<(PyValue, PyValue)>,
    junction_units: i64,
    junctions_with_primary_unit: i64,
    junctions_without_primary_unit: i64,
    duplicate_group_keys: PyMap<PyMap<()>>,
    cluster_keys: PyMap<PyMap<()>>,
    duplicate_group_cluster: PyMap<PyValue>,
    human_units: PyMap<[String; 3]>,
    identity: PyMap<(PyValue, bool, bool)>,
    policy_files: BTreeSet<String>,
}

/// What every checker of one build reads besides the units: the per-unit check at `PATCHED` with its machine checks, the manifest's `feature_descriptions` a config gate's features are glossed by, the batch size, the root the policy files are looked up under, if any, and the families' ranks the triage order reads.
pub struct CheckSettings {
    patched: PatchedCheck,
    descriptions: PyValue,
    batch_size: PyValue,
    repo_root: Option<PathBuf>,
    family_rank: FamilyRanks,
}

impl CheckSettings {
    pub fn new(
        patched: PatchedCheck,
        descriptions: PyValue,
        batch_size: PyValue,
        repo_root: Option<PathBuf>,
        family_of: &[(i64, String)],
    ) -> Self {
        Self {
            patched,
            descriptions,
            batch_size,
            repo_root,
            family_rank: family_ranks(family_of),
        }
    }

    /// The settings the build's write checks with: the build input's machine checks, `FEATURE_DESCRIPTIONS`, batch size, repo root and families.
    pub fn of(input: &BuildInput) -> Result<Self> {
        let descriptions = PyValue::Dict(
            input
                .manifest
                .feature_descriptions
                .iter()
                .map(|(feature, text)| (PyValue::str(feature), PyValue::str(text)))
                .collect(),
        );
        Ok(Self::new(
            PatchedCheck::of(input)?,
            descriptions,
            PyValue::Int(input.manifest.batch_size),
            Some(PathBuf::from(&input.repo_root)),
            &input.family_of,
        ))
    }
}

/// `value.get(key)` on a value that must be a dict, as Python's `.get` raises AttributeError on any other.
fn dict_get<'v>(value: &'v PyValue, key: &str) -> Result<Option<&'v PyValue>> {
    match value {
        PyValue::Dict(_) => Ok(lookup(value, key)),
        other => raise(
            "AttributeError",
            format!("{} has no attribute 'get'", other.kind()),
        ),
    }
}

/// `dictionary.get(key)` for any key, which must be hashable.
fn dict_lookup<'v>(dictionary: &'v PyValue, key: &PyValue) -> Result<Option<&'v PyValue>> {
    hash_of(key)?;
    match dictionary {
        PyValue::Dict(entries) => Ok(entries
            .iter()
            .find(|(held, _)| py_eq(held, key))
            .map(|(_, value)| value)),
        other => raise(
            "AttributeError",
            format!("{} has no attribute 'get'", other.kind()),
        ),
    }
}

/// `value or default`, a falsy or absent value reading as the default.
fn or<'v>(value: Option<&'v PyValue>, default: &'v PyValue) -> &'v PyValue {
    value.filter(|value| truthy(value)).unwrap_or(default)
}

/// `str` of a value that may be absent, which reads as None.
fn text(value: Option<&PyValue>) -> Result<String> {
    text_of(value.unwrap_or(&PyValue::None))
}

/// `repr` of a value.
fn shown(value: &PyValue) -> Result<String> {
    repr(value).or_else(|failure| engine(failure.0))
}

/// Python's `!=` between a value that may be absent, which reads as None, and another.
fn differs(value: Option<&PyValue>, other: &PyValue) -> bool {
    !py_eq(value.unwrap_or(&PyValue::None), other)
}

/// `(value or "").split(":")` over a unit's window as the identity keeps it: AttributeError for a truthy value without `split`, TypeError for bytes, whose `split` refuses a `str`.
fn window_tokens(value: &PyValue) -> Result<Vec<&str>> {
    match value {
        value if !truthy(value) => Ok(vec![""]),
        PyValue::Str(text) => Ok(text.split(':').collect()),
        PyValue::Bytes(_) => raise("TypeError", "a bytes-like object is required, not 'str'"),
        other => raise(
            "AttributeError",
            format!("{} has no attribute 'split'", other.kind()),
        ),
    }
}

/// `audit.parse_codepoints`: each `:`-separated part read by `int(part, 16)`, which raises ValueError for text it refuses.
fn parse_codepoints(text: &str) -> Result<Vec<i128>> {
    text.split(':')
        .map(|part| match python_hex_int(part) {
            Some((negative, magnitude)) => {
                let value = i128::from(magnitude);
                Ok(if negative { -value } else { value })
            }
            None => raise(
                "ValueError",
                format!("invalid literal for int() with base 16: {part:?}"),
            ),
        })
        .collect()
}

/// `audit.triage_key`'s tuple for one human unit, ordered as Python orders it.
#[derive(Debug, PartialEq, Eq, PartialOrd, Ord)]
struct TriageTerms<'a> {
    class_index: usize,
    families: Vec<i64>,
    length: usize,
    codepoints: Vec<i128>,
    unit_id: &'a str,
}

/// The accumulator of `build._CorpusCheck` in `m1-audit` mode at `PATCHED`: the per-unit check over every fragment the unit cache did not supply, the class-level checks against each class's record, and the facts the cross-unit predicates read at [`CorpusCheck::finish`]. With `identity_for`, the ids some unit names as its primary unit, it keeps the identity of those units and of the units naming one alone; without it, of every unit.
pub struct CorpusCheck<'a> {
    settings: &'a CheckSettings,
    identity_for: Option<&'a BTreeSet<String>>,
    facts: Facts,
    meta: PyValue,
    class_units: i64,
    machine_count: i64,
    check_counts: Vec<(String, i64)>,
}

impl<'a> CorpusCheck<'a> {
    pub fn new(settings: &'a CheckSettings, identity_for: Option<&'a BTreeSet<String>>) -> Self {
        Self {
            settings,
            identity_for,
            facts: Facts::default(),
            meta: PyValue::Dict(Vec::new()),
            class_units: 0,
            machine_count: 0,
            check_counts: Vec::new(),
        }
    }

    /// Open a class with its manifest record: a no-verdict class must carry no batches.
    pub fn class_start(&mut self, meta: &PyValue) -> Result<()> {
        self.meta = meta.clone();
        self.class_units = 0;
        self.machine_count = 0;
        self.check_counts = self
            .settings
            .patched
            .machine_checks()
            .iter()
            .map(|check| (check.clone(), 0))
            .collect();
        if dict_get(meta, "no_verdict")?.is_some_and(truthy)
            && dict_get(meta, "batches")?.is_some_and(truthy)
        {
            let id = text(dict_get(meta, "id")?)?;
            self.facts.errors.push(format!(
                "class {id}: a no-verdict class must carry no batches"
            ));
        }
        Ok(())
    }

    /// Check one fragment, in shard order; `cached` says whether the unit cache supplied it, which skips the per-unit check.
    pub fn unit(&mut self, unit: &PyValue, cached: bool) -> Result<()> {
        self.class_units += 1;
        let unit_id = dict_get(unit, "id")?;
        let get = |key: &str| lookup(unit, key);
        if !cached {
            let errors = self.settings.patched.check(unit)?;
            self.facts.errors.extend(errors);
        }
        let empty = PyValue::Dict(Vec::new());
        let drafts = or(get("drafts"), &empty);
        let policy = or(dict_get(drafts, "policy")?, &empty);
        if let Some(PyValue::Str(file)) = dict_get(policy, "file")? {
            self.facts.policy_files.insert(file.clone());
        }
        for clause in members(or(get("config_gate"), &PyValue::Tuple(Vec::new())))? {
            if !matches!(clause, PyValue::Dict(_)) {
                continue;
            }
            let feature = lookup(&clause, "feature").unwrap_or(&PyValue::None);
            let glossed = dict_lookup(&self.settings.descriptions, feature)?;
            if !glossed.is_some_and(truthy) {
                self.facts.errors.push(format!(
                    "unit {}: config_gate names {}, which the manifest's feature_descriptions does not gloss",
                    text(unit_id)?,
                    shown(feature)?
                ));
            }
        }
        let machine_checks = self.settings.patched.machine_checks();
        let approved = machine_checks.iter().any(|check| is_true(get(check)));
        let human = !(approved || is_true(get("no_verdict")));
        let none = PyValue::None;
        let field = |key: &str| get(key).unwrap_or(&none);
        let human_id = match unit_id {
            Some(PyValue::Str(id)) if human => Some(id),
            _ => None,
        };
        if let Some(id) = human_id {
            let configs = members(or(get("configs"), &PyValue::Tuple(Vec::new())))?;
            let key = PyValue::Tuple(vec![field("class").clone(), PyValue::Tuple(configs)]);
            self.facts
                .duplicate_group_keys
                .entry(field("duplicate_group"), PyMap::default)?
                .add(&key)?;
            self.facts
                .cluster_keys
                .entry(field("cluster"), PyMap::default)?
                .add(&key)?;
            let first = self
                .facts
                .duplicate_group_cluster
                .entry(field("duplicate_group"), || field("cluster").clone())?;
            if !py_eq(first, field("cluster")) {
                self.facts.errors.push(format!(
                    "unit {id}: duplicate group {} spans two clusters",
                    text_of(field("duplicate_group"))?
                ));
            }
        }
        let meta_id = dict_get(&self.meta, "id")?;
        if differs(get("class"), meta_id.unwrap_or(&none)) {
            self.facts.errors.push(format!(
                "unit {}: class {} in shard {}",
                text_of(field("id"))?,
                text_of(field("class"))?,
                text(meta_id)?
            ));
        }
        if self.facts.seen_ids.contains(field("id"))? {
            self.facts
                .errors
                .push(format!("duplicate unit id {}", text_of(field("id"))?));
        }
        self.facts.seen_ids.add(field("id"))?;
        if let Some(id) = human_id {
            let terms = [
                text_of(field("class"))?,
                text_of(or(get("group"), &PyValue::str("")))?,
                text_of(or(get("codepoints"), &PyValue::str("")))?,
            ];
            self.facts
                .human_units
                .insert(PyValue::Str(id.clone()), terms)?;
        }
        let meta_no_verdict = dict_get(&self.meta, "no_verdict")?;
        let class_no_verdict = PyValue::Bool(meta_no_verdict.is_some_and(truthy));
        if differs(get("no_verdict"), &class_no_verdict) {
            self.facts.errors.push(format!(
                "unit {}: no_verdict {} in a class whose no_verdict is {}",
                text_of(field("id"))?,
                text_of(field("no_verdict"))?,
                text(meta_no_verdict)?
            ));
        }
        if approved {
            self.machine_count += 1;
            for check in machine_checks {
                if is_true(get(check)) {
                    match self.check_counts.iter_mut().find(|(held, _)| held == check) {
                        Some((_, count)) => *count += 1,
                        None => return raise("KeyError", check.clone()),
                    }
                }
            }
        }
        let mut names_a_primary_unit = false;
        if let Some(junctions) = get("secondary_junctions").filter(|value| truthy(value)) {
            self.facts.junction_units += 1;
            for junction in members(junctions)? {
                if !matches!(junction, PyValue::Dict(_)) {
                    continue;
                }
                match lookup(&junction, "primary_unit") {
                    None | Some(PyValue::None) => self.facts.junctions_without_primary_unit += 1,
                    Some(primary_unit) => {
                        self.facts.junctions_with_primary_unit += 1;
                        self.facts
                            .primary_units
                            .push((field("id").clone(), primary_unit.clone()));
                        names_a_primary_unit = true;
                    }
                }
            }
        }
        let keep = match self.identity_for {
            None => true,
            Some(_) if names_a_primary_unit => true,
            Some(ids) => {
                let id = unit_id.unwrap_or(&none);
                hash_of(id)?;
                matches!(id, PyValue::Str(id) if ids.contains(id))
            }
        };
        if keep {
            let identical = is_true(get("ink_identical")) || is_true(get("picture_identical"));
            let paired = !matches!(get("pair"), None | Some(PyValue::None));
            self.facts.identity.insert(
                unit_id.unwrap_or(&none).clone(),
                (field("codepoints").clone(), paired, identical),
            )?;
        }
        Ok(())
    }

    /// Close the open class: its unit count, machine-approved count and per-check counts against its record, then its units and rows into the totals.
    pub fn class_end(&mut self) -> Result<()> {
        let meta = &self.meta;
        let id = |meta: &PyValue| match lookup(meta, "id") {
            Some(value) => Ok(value.clone()),
            None => raise("KeyError", "'id'"),
        };
        let unit_count = dict_get(meta, "unit_count")?;
        if differs(unit_count, &PyValue::Int(self.class_units)) {
            let shard = text_of(&id(meta)?)?;
            self.facts.errors.push(format!(
                "shard {shard}: {} units, manifest says {}",
                self.class_units,
                text(unit_count)?
            ));
        }
        let approved_count = dict_get(meta, "machine_approved_count")?;
        if differs(approved_count, &PyValue::Int(self.machine_count)) {
            self.facts.errors.push(format!(
                "class {}: {} machine-approved units, manifest says {}",
                text(dict_get(meta, "id")?)?,
                self.machine_count,
                text(approved_count)?
            ));
        }
        let counts = PyValue::Dict(
            self.check_counts
                .iter()
                .map(|(check, count)| (PyValue::str(check), PyValue::Int(*count)))
                .collect(),
        );
        if let Some(declared @ PyValue::Dict(_)) = dict_get(meta, "machine_checks")?
            && !py_eq(declared, &counts)
        {
            self.facts.errors.push(format!(
                "class {}: machine_checks {} != {} in the shards",
                text(dict_get(meta, "id")?)?,
                shown(declared)?,
                shown(&counts)?
            ));
        }
        if self.machine_count != 0 {
            let class = id(meta)?;
            self.facts
                .seen_machine_by_class
                .insert(class, self.machine_count)?;
        }
        self.facts.seen_units += self.class_units;
        let rows = match dict_get(meta, "row_count")? {
            None => 0,
            Some(value) => match int_of(Some(value)) {
                Some(rows) => rows,
                None => {
                    return raise(
                        "TypeError",
                        format!(
                            "unsupported operand type(s) for +=: 'int' and {}",
                            value.kind()
                        ),
                    );
                }
            },
        };
        self.facts.seen_rows += rows;
        Ok(())
    }

    /// Hand over what has accumulated since the checker was made or last asked, and go on from nothing; the open class's record and counters stay.
    pub fn take_facts(&mut self) -> Facts {
        std::mem::take(&mut self.facts)
    }

    /// Fold in facts another checker gave: their errors after this checker's, then a repeated id and a duplicate group whose cluster differs from the one absorbed first, each checked against everything absorbed before; the rest is unions and sums.
    pub fn absorb(&mut self, other: Facts) -> Result<()> {
        let facts = &mut self.facts;
        facts.errors.extend(other.errors);
        let (walked, held) = if other.seen_ids.len() > facts.seen_ids.len() {
            (&facts.seen_ids, &other.seen_ids)
        } else {
            (&other.seen_ids, &facts.seen_ids)
        };
        let mut repeated = Vec::new();
        for id in walked.keys() {
            if held.contains(id)? {
                repeated.push(text_of(id)?);
            }
        }
        repeated.sort();
        facts.errors.extend(
            repeated
                .into_iter()
                .map(|id| format!("duplicate unit id {id}")),
        );
        for id in other.seen_ids.keys() {
            facts.seen_ids.add(id)?;
        }
        for (group, cluster) in other.duplicate_group_cluster.iter() {
            let first = facts
                .duplicate_group_cluster
                .entry(group, || cluster.clone())?;
            if !py_eq(first, cluster) {
                facts.errors.push(format!(
                    "duplicate group {} spans two clusters",
                    text_of(group)?
                ));
            }
        }
        facts.seen_units += other.seen_units;
        facts.seen_rows += other.seen_rows;
        for (class, count) in other.seen_machine_by_class.iter() {
            facts.seen_machine_by_class.insert(class.clone(), *count)?;
        }
        facts.primary_units.extend(other.primary_units);
        facts.junction_units += other.junction_units;
        facts.junctions_with_primary_unit += other.junctions_with_primary_unit;
        facts.junctions_without_primary_unit += other.junctions_without_primary_unit;
        for (into, from) in [
            (&mut facts.duplicate_group_keys, &other.duplicate_group_keys),
            (&mut facts.cluster_keys, &other.cluster_keys),
        ] {
            for (group, keys) in from.iter() {
                let held = into.entry(group, PyMap::default)?;
                for key in keys.keys() {
                    held.add(key)?;
                }
            }
        }
        for (id, terms) in other.human_units.iter() {
            facts.human_units.insert(id.clone(), terms.clone())?;
        }
        for (id, identity) in other.identity.iter() {
            facts.identity.insert(id.clone(), identity.clone())?;
        }
        facts.policy_files.extend(other.policy_files);
        Ok(())
    }

    /// Run the cross-unit predicates over the folded facts against `manifest`, and return every error, those found as the units were fed first.
    pub fn finish(self, manifest: &PyValue) -> Result<Vec<String>> {
        let settings = self.settings;
        let mut facts = self.facts;
        let mut errors = std::mem::take(&mut facts.errors);
        let empty = PyValue::Dict(Vec::new());
        let totals = dict_get(manifest, "totals")?.unwrap_or(&empty);
        let total_units = dict_get(totals, "units")?;
        if differs(total_units, &PyValue::Int(facts.seen_units)) {
            errors.push(format!(
                "totals.units {} != {} shard units",
                text(total_units)?,
                facts.seen_units
            ));
        }
        let total_rows = dict_get(totals, "rows")?;
        if differs(total_rows, &PyValue::Int(facts.seen_rows)) {
            errors.push(format!(
                "totals.rows {} != {} summed class rows",
                text(total_rows)?,
                facts.seen_rows
            ));
        }
        let recorded_index = dict_get(manifest, "human_unit_ids")?;
        let (human_unit_ids, index_valid): (Vec<&str>, bool) = match recorded_index {
            Some(PyValue::List(items)) => {
                let ids: Vec<&str> = items
                    .iter()
                    .filter_map(|item| match item {
                        PyValue::Str(id) => Some(id.as_str()),
                        _ => None,
                    })
                    .collect();
                let valid = ids.len() == items.len();
                (ids, valid)
            }
            _ => (Vec::new(), false),
        };
        let human_ids = |key: &PyValue| match key {
            PyValue::Str(id) => id.clone(),
            other => unreachable!("a human unit is keyed by its str id, not {}", other.kind()),
        };
        if index_valid {
            let listed: BTreeSet<&str> = human_unit_ids.iter().copied().collect();
            let held: BTreeSet<String> = facts.human_units.keys().iter().map(human_ids).collect();
            if listed.len() != held.len() || !held.iter().all(|id| listed.contains(id.as_str())) {
                errors.push("human_unit_ids does not match the shards' human units".to_owned());
            }
        }
        let machine = or(dict_get(manifest, "machine_approved")?, &empty);
        let machine_units: i64 = facts.seen_machine_by_class.values().iter().sum();
        let recorded_units = dict_get(machine, "units")?;
        if differs(recorded_units, &PyValue::Int(machine_units)) {
            errors.push(format!(
                "machine_approved.units {} != {machine_units} machine-approved shard units",
                text(recorded_units)?
            ));
        }
        let by_class = or(dict_get(machine, "by_class")?, &empty);
        if !matches!(by_class, PyValue::Dict(_)) {
            return raise(
                "AttributeError",
                format!("{} has no attribute 'items'", by_class.kind()),
            );
        }
        let seen_by_class = PyValue::Dict(
            facts
                .seen_machine_by_class
                .iter()
                .map(|(class, count)| (class.clone(), PyValue::Int(*count)))
                .collect(),
        );
        if !py_eq(&seen_by_class, by_class) {
            errors.push(
                "machine_approved.by_class does not match the shards' machine-approved counts"
                    .to_owned(),
            );
        }
        for (label, what, map) in [
            ("duplicate group", "group", &facts.duplicate_group_keys),
            ("cluster", "signature", &facts.cluster_keys),
        ] {
            for (key, keys) in map.iter() {
                if keys.len() > 1 {
                    let first: Vec<PyValue> =
                        sorted(keys.keys())?.into_iter().take(2).cloned().collect();
                    errors.push(format!(
                        "{label} {}: one {what} spans {}",
                        text_of(key)?,
                        shown(&PyValue::List(first))?
                    ));
                }
            }
        }
        let classes = || {
            members(or(
                dict_get(manifest, "classes")?,
                &PyValue::Tuple(Vec::new()),
            ))
        };
        let batch_size = int_of(Some(&settings.batch_size)).filter(|&size| size > 0);
        if index_valid {
            let classes = classes()?;
            let mut class_index: HashMap<String, usize> = HashMap::new();
            for (index, meta) in classes.iter().enumerate() {
                class_index.insert(text(dict_get(meta, "id")?)?, index);
            }
            let mut keyed = Vec::with_capacity(facts.human_units.len());
            for (key, [class, group, codepoints]) in facts.human_units.iter() {
                let PyValue::Str(unit_id) = key else {
                    unreachable!("a human unit is keyed by its str id");
                };
                let codepoints = parse_codepoints(codepoints)?;
                keyed.push(TriageTerms {
                    class_index: class_index.get(class).copied().unwrap_or(class_index.len()),
                    families: settings.family_rank.group(group),
                    length: codepoints.len(),
                    codepoints,
                    unit_id,
                });
            }
            keyed.sort();
            if human_unit_ids.len() != keyed.len()
                || human_unit_ids
                    .iter()
                    .zip(&keyed)
                    .any(|(listed, terms)| *listed != terms.unit_id)
            {
                errors.push(
                    "human_unit_ids is not the triage-ordered sequence of the shards' human units"
                        .to_owned(),
                );
            }
        }
        if let Some(size) = batch_size.filter(|_| index_valid) {
            let mut occupied: HashMap<&str, BTreeSet<i64>> = HashMap::new();
            for (position, unit_id) in human_unit_ids.iter().enumerate() {
                if let Some([class, ..]) = facts.human_units.get(&PyValue::str(*unit_id))? {
                    occupied
                        .entry(class.as_str())
                        .or_default()
                        .insert(position as i64 / size);
                }
            }
            for meta in &classes()? {
                let class = text(dict_get(meta, "id")?)?;
                let slices = PyValue::List(
                    occupied
                        .get(class.as_str())
                        .into_iter()
                        .flatten()
                        .map(|&batch| PyValue::Int(batch))
                        .collect(),
                );
                let batches = dict_get(meta, "batches")?;
                if differs(batches, &slices) {
                    errors.push(format!(
                        "class {}: batches {} are not the slices of {} its units occupy in human_unit_ids",
                        text(dict_get(meta, "id")?)?,
                        text(batches)?,
                        text_of(&settings.batch_size)?
                    ));
                }
            }
            let count = (human_unit_ids.len() as i64 + size - 1) / size;
            if differs(dict_get(totals, "batches")?, &PyValue::Int(count)) {
                errors.push(
                    "totals.batches does not count the slices human_unit_ids partitions into"
                        .to_owned(),
                );
            }
        }
        for (unit_id, primary_unit) in &facts.primary_units {
            if py_eq(primary_unit, unit_id) {
                errors.push(format!(
                    "unit {}: a secondary junction names itself as its primary unit",
                    text_of(unit_id)?
                ));
            } else if !facts.seen_ids.contains(primary_unit)? {
                errors.push(format!(
                    "unit {}: secondary junction's primary unit {} is not a unit in this output",
                    text_of(unit_id)?,
                    text_of(primary_unit)?
                ));
            }
        }
        let junction_counts = dict_get(manifest, "secondary_junctions")?;
        if let Some(PyValue::Dict(_)) = junction_counts {
            let identity = &facts.identity;
            for (unit_id, primary_unit) in &facts.primary_units {
                let Some(primary) = identity.get(primary_unit)? else {
                    continue;
                };
                let Some(unit) = identity.get(unit_id)? else {
                    continue;
                };
                let tokens = window_tokens(&unit.0)?;
                let primary_tokens = window_tokens(&primary.0)?;
                let contained = primary_tokens.len() <= tokens.len()
                    && tokens
                        .windows(primary_tokens.len())
                        .any(|window| window == primary_tokens);
                let (unit_text, primary_text) = (text_of(unit_id)?, text_of(primary_unit)?);
                if !contained {
                    errors.push(format!(
                        "unit {unit_text}: secondary junction's primary unit {primary_text} is not a substring window"
                    ));
                }
                if !primary.1 {
                    errors.push(format!(
                        "unit {unit_text}: secondary junction's primary unit {primary_text} has no primary pair"
                    ));
                }
                if primary.2 {
                    errors.push(format!(
                        "unit {unit_text}: secondary junction's primary unit {primary_text} shows no visible change"
                    ));
                }
                if unit.2 {
                    errors.push(format!(
                        "unit {unit_text}: a unit with no visible change carries a secondary junction"
                    ));
                }
            }
            let counts = junction_counts.unwrap_or(&empty);
            for (key, observed) in JUNCTION_COUNTS.into_iter().zip([
                facts.junction_units,
                facts.junctions_with_primary_unit,
                facts.junctions_without_primary_unit,
            ]) {
                let recorded = lookup(counts, key);
                if differs(recorded, &PyValue::Int(observed)) {
                    errors.push(format!(
                        "secondary_junctions.{key} {} != {observed} in the shards",
                        text(recorded)?
                    ));
                }
            }
        }
        if let Some(root) = &settings.repo_root {
            for name in &facts.policy_files {
                if !root.join(name).is_file() {
                    errors.push(format!(
                        "drafts.policy names {name}, which is not a file in the repo"
                    ));
                }
            }
        }
        Ok(errors)
    }
}

#[cfg(test)]
mod tests {
    use std::cmp::Ordering;

    use super::*;

    fn dict(entries: Vec<(&str, PyValue)>) -> PyValue {
        PyValue::Dict(
            entries
                .into_iter()
                .map(|(key, value)| (PyValue::str(key), value))
                .collect(),
        )
    }

    fn settings() -> CheckSettings {
        CheckSettings::new(
            PatchedCheck::new(vec!["ink_identical".to_owned()]),
            dict(vec![("ss03", PyValue::str("gloss"))]),
            PyValue::Int(2),
            None,
            &[(0xE650, "qsPea".to_owned()), (0xE652, "qsTea".to_owned())],
        )
    }

    fn unit(id: &str, codepoints: &str, junctions: PyValue) -> PyValue {
        dict(vec![
            ("id", PyValue::str(id)),
            ("ink_identical", PyValue::Bool(false)),
            ("no_verdict", PyValue::Bool(false)),
            ("duplicate_group", PyValue::str("e-1")),
            ("cluster", PyValue::str("c-1")),
            ("class", PyValue::str("A")),
            ("group", PyValue::str("qsPea")),
            ("codepoints", PyValue::str(codepoints)),
            ("configs", PyValue::List(vec![PyValue::str("default")])),
            ("pair", PyValue::None),
            ("secondary_junctions", junctions),
        ])
    }

    #[test]
    fn a_folded_corpus_is_checked_against_its_manifest_and_its_primary_units() {
        let settings = settings();
        let meta = dict(vec![
            ("id", PyValue::str("A")),
            ("no_verdict", PyValue::Bool(false)),
            ("unit_count", PyValue::Int(2)),
            ("row_count", PyValue::Int(3)),
            ("machine_approved_count", PyValue::Int(0)),
            ("batches", PyValue::List(vec![PyValue::Int(0)])),
        ]);
        let junction = dict(vec![("primary_unit", PyValue::str("u-a"))]);
        let mut run = CorpusCheck::new(&settings, None);
        run.class_start(&meta).unwrap();
        run.unit(&unit("u-a", "E650", PyValue::None), true).unwrap();
        let first = run.take_facts();
        run.unit(&unit("u-a", "E652", PyValue::List(vec![junction])), true)
            .unwrap();
        run.class_end().unwrap();
        let second = run.take_facts();
        let manifest = dict(vec![
            (
                "totals",
                dict(vec![
                    ("units", PyValue::Int(2)),
                    ("rows", PyValue::Int(3)),
                    ("batches", PyValue::Int(1)),
                ]),
            ),
            ("human_unit_ids", PyValue::List(vec![PyValue::str("u-a")])),
            ("classes", PyValue::List(vec![meta])),
            ("secondary_junctions", dict(vec![])),
        ]);
        let mut check = CorpusCheck::new(&settings, None);
        check.absorb(first).unwrap();
        check.absorb(second).unwrap();
        assert_eq!(
            check.finish(&manifest),
            Ok(vec![
                "duplicate unit id u-a".to_owned(),
                "machine_approved.units None != 0 machine-approved shard units".to_owned(),
                "unit u-a: a secondary junction names itself as its primary unit".to_owned(),
                "unit u-a: secondary junction's primary unit u-a has no primary pair".to_owned(),
                "secondary_junctions.units_with_markers None != 1 in the shards".to_owned(),
                "secondary_junctions.junctions_with_primary_unit None != 1 in the shards"
                    .to_owned(),
                "secondary_junctions.junctions_without_primary_unit None != 0 in the shards"
                    .to_owned(),
            ])
        );
    }

    #[test]
    fn an_unhashable_id_raises_as_python_hashes_it() {
        let settings = settings();
        let mut run = CorpusCheck::new(&settings, None);
        let unhashable = dict(vec![("id", PyValue::List(Vec::new()))]);
        assert_eq!(
            run.unit(&unhashable, true).map_err(|error| error.raised),
            Err(Some("TypeError"))
        );
        let mut keys: PyMap<()> = PyMap::default();
        keys.add(&PyValue::Int(1)).unwrap();
        assert_eq!(keys.contains(&PyValue::Bool(true)), Ok(true));
        assert_eq!(
            keys.contains(&PyValue::str("1"))
                .map(|held| held.cmp(&false)),
            Ok(Ordering::Equal)
        );
    }
}
