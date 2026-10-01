//! Cross-configuration liveness: the windows each configuration's table takes from the others before it is folded for the last time.
//!
//! The font ships one settlement lookup folded from every configuration's table (`emit_gsub._ordered_settle_rules`), and a settled left's label is the same under every configuration, so a rule one configuration folds can match a window that only another configuration reaches. A configuration's fold decides a block's rules from the rows it has, and a key it never reaches is a don't-care there: a block may answer it with whatever its other rows settle to. The shipped order sorts every rule with a backtrack ahead of every rule without one, so such a rule can fire on another configuration's window before that configuration's own rule does (`doc/rebuild-design.md` §10, the fault table's row 6).
//!
//! For any window, the rule the shipped lookup fires is the first match of the earliest configuration (in fold order) whose table holds it, and that configuration's own first match: the emitter's sort never moves a later rule of one table ahead of an earlier rule of that table that matches some window both match, because within one table a later rule that overlaps an earlier one is never in an earlier [`bucket`] (a backtrack-free rule never precedes a backtrack rule it overlaps, a ZWNJ backtrack guard never follows a non-guard it overlaps, and between rules of one status the later one constrains only lookahead slots the earlier one constrains, with the same classes; `fold.rs`'s `the_emitter_s_sort_never_moves_an_overlapping_rule_ahead` checks this), and `_fold_rules` keeps each rule at its first occurrence. So the fired rule is whichever configuration's own first match ships earliest, and the shipped lookup answers every window every configuration keeps live exactly when, for every such window, no configuration's first match that gives another outcome ships ahead of every first match that gives the window's own. Where a rule ships is its [`bucket`] under the emitter's sort, then, inside a bucket, the [`Spellings::rank`] of the earliest configuration that holds it. This module enforces that statement on the tables before they are written. Both are this module's model of the emitter's order, so each table carries them to the emitter ([`Spellings::fold_order`], [`rule_buckets`], the windows head's `fold_order` and `buckets`), and `emit_gsub._fold_rules` raises where its own order disagrees.
//!
//! The exchange runs in rounds between the configurations of one build ([`crate::fanout::run_configs_tables`]). Each configuration publishes its current rules ([`SharedRules`]). Each configuration then reads every configuration's rules and, as a source, evaluates them over its own rows ([`exports`]), and sends each configuration exactly the rows whose wrong answer there would ship first ([`ForeignRow`]). A receiver takes them in ([`Imports::absorb`]), refolds the inputs that gained rows, and publishes again. The next round evaluates only the inputs some configuration refolded, and the rounds stop when no configuration sends anything. An imported row is a row like the configuration's own to [`crate::rulefold`], so the refolded rules answer it, and the fold's replay checks that they do. The rules an imported row is the only reason for are guard rules, and each carries a certificate settled under the configuration the row is live in ([`crate::certificate::guard_certificate`]).
//!
//! Labels cross between configurations through [`Spellings`]. A rune whose stances unlock under a stylistic set has a marker copy for each set of its unlock features, and each configuration renames the rune's raw labels to its own copy before settlement (`model.raw_rename_map`). So a raw label means a different glyph under two configurations that set the rune's features differently. Between configurations the crate writes such a label as a tag, `<raw>@<features>`, naming the features the source configuration sets for the rune (`qsTea@` for the bare rune, `qsTea.noentry@ss03_ss05` for a locked copy under both sets), and a configuration writes its own copy as the raw label. A tag in a configuration's rules names a glyph its own stream never holds, so the crate's replays never match it, and the emitter spells it as the marker copy it names (`emit_gsub._renamed`). The crate never needs a marker copy's name.
//!
//! A row's `#NA` after a letter means that the window does not carry that slot, so the outcome is the same for every label there. The evaluation reads it so: a source row is answered wrongly when any continuation through the slot reaches a rule with another outcome first. A receiver refuses an imported row that overlaps one of its own rows, or another imported row, with a different outcome, since one shipped lookup cannot give both, and names both configurations.

use std::borrow::Cow;
use std::collections::BTreeMap;
use std::rc::Rc;

use crate::certificate::{self, RowChains};
use crate::fold::{LabelRows, NA_LABEL, Rule, boundaryish};
use crate::hash::{HashMap, HashSet};
use crate::index::SpecIndex;
use crate::model::Sym;
use crate::stream::key_repr;
use crate::types::RightToken;

/// The separator between a raw label and the feature set a tag names.
pub const TAG_SEPARATOR: char = '@';

/// The suffix of a locked copy's label, `model.locked_glyph_name`.
const LOCKED_SUFFIX: &str = ".noentry";

/// The boundary labels a left slot can carry, in the order a boundary closure tries them.
const BOUNDARY_LEFTS: [&str; 4] = ["#EDGE", "space", "periodcentered", "uni200C"];

/// The boundary labels a right slot can carry.
const BOUNDARY_RIGHTS: [&str; 4] = ["#EDGE", "space", "uni200C", "periodcentered"];

/// How the configurations of one build spell the raw labels of the runes whose stances unlock under a stylistic set: each such rune's unlock features (`model.relevant_marker_features`), and each configuration's subset of them.
pub struct Spellings {
    tokens: Vec<String>,
    states: Vec<HashMap<String, String>>,
    ranks: Vec<usize>,
}

impl Spellings {
    /// The spellings of the configurations `configs`, each a token and its features, in list order. A rune is a marker rune when some stance of it has an unlock row, whatever the row's own `when:`.
    pub fn new<'c>(
        index: &SpecIndex,
        configs: impl IntoIterator<Item = (&'c str, &'c [Sym])>,
    ) -> Self {
        let mut unlocks: Vec<(String, Vec<String>)> = Vec::new();
        for (name, rune) in index.runes().iter() {
            let mut features: Vec<String> = rune
                .stances
                .iter()
                .flat_map(|(_, stance)| stance.surface.unlocks.iter())
                .map(|unlock| index.resolve(unlock.feature).to_owned())
                .collect();
            features.sort();
            features.dedup();
            if !features.is_empty() {
                unlocks.push((index.resolve(*name).to_owned(), features));
            }
        }
        let mut tokens: Vec<String> = Vec::new();
        let mut states: Vec<HashMap<String, String>> = Vec::new();
        let mut sorted_features: Vec<Vec<&str>> = Vec::new();
        for (token, features) in configs {
            let active: HashSet<&str> = features
                .iter()
                .map(|feature| index.resolve(*feature))
                .collect();
            let mut named: Vec<&str> = active.iter().copied().collect();
            named.sort_unstable();
            sorted_features.push(named);
            states.push(
                unlocks
                    .iter()
                    .map(|(rune, unlocked)| {
                        let on: Vec<&str> = unlocked
                            .iter()
                            .map(String::as_str)
                            .filter(|feature| active.contains(feature))
                            .collect();
                        (rune.clone(), on.join("_"))
                    })
                    .collect(),
            );
            tokens.push(token.to_owned());
        }
        let ranks = fold_ranks(&sorted_features);
        Self {
            tokens,
            states,
            ranks,
        }
    }

    /// Where the emitter's fold appends a configuration's rules (`emit_gsub._fold_rules`, which visits the configurations sorted by their sorted feature lists): `default` first. Inside one [`bucket`], a rule of a configuration with a lower rank ships first.
    pub fn rank(&self, config: usize) -> usize {
        self.ranks[config]
    }

    /// The configurations' tokens, in list order.
    pub fn tokens(&self) -> &[String] {
        &self.tokens
    }

    /// The configurations' tokens in [`Spellings::rank`] order, the order the windows head carries for the emitter to check against its own.
    pub fn fold_order(&self) -> Vec<String> {
        let mut order: Vec<(usize, &String)> = self
            .tokens
            .iter()
            .enumerate()
            .map(|(config, token)| (self.ranks[config], token))
            .collect();
        order.sort_unstable();
        order.into_iter().map(|(_, token)| token.clone()).collect()
    }

    /// The marker rune a raw label names, or `None` for any other label, a tag included.
    fn marker_rune<'l>(&self, label: &'l str) -> Option<&'l str> {
        if label.contains(TAG_SEPARATOR) {
            return None;
        }
        let rune = label.strip_suffix(LOCKED_SUFFIX).unwrap_or(label);
        self.states
            .first()
            .is_some_and(|state| state.contains_key(rune))
            .then_some(rune)
    }

    /// A raw label of `config` in the form every configuration reads alike: a marker rune's label as a tag naming the features `config` sets for the rune, and any other label, a tag included, unchanged.
    pub fn canonical<'l>(&self, config: usize, label: &'l str) -> Cow<'l, str> {
        match self.marker_rune(label) {
            Some(rune) => Cow::Owned(format!(
                "{label}{TAG_SEPARATOR}{}",
                self.states[config][rune]
            )),
            None => Cow::Borrowed(label),
        }
    }

    /// A canonical label as `config` writes it: a tag naming the features `config` sets for its rune as the raw label, and anything else unchanged.
    pub fn local<'l>(&self, config: usize, label: &'l str) -> Cow<'l, str> {
        let Some((raw, state)) = label.split_once(TAG_SEPARATOR) else {
            return Cow::Borrowed(label);
        };
        let rune = raw.strip_suffix(LOCKED_SUFFIX).unwrap_or(raw);
        match self.states[config].get(rune) {
            Some(own) if own == state => Cow::Borrowed(raw),
            _ => Cow::Borrowed(label),
        }
    }

    /// A label of `from` as `to` writes it.
    pub fn translate(&self, from: usize, to: usize, label: &str) -> String {
        self.local(to, &self.canonical(from, label)).into_owned()
    }

    /// A key of `config`'s rows in canonical form, every slot through [`Spellings::canonical`]: a left whose row settled to its own raw input is a marker rune's raw label, which the emitter renames per configuration in the backtrack as in the lookahead.
    fn canonical_key(&self, config: usize, key: [&str; 6]) -> [String; 6] {
        key.map(|label| self.canonical(config, label).into_owned())
    }

    /// An outcome of `config` in canonical form. Only an identity outcome, which is the raw input, can name a marker copy.
    fn canonical_outcome(&self, config: usize, input: &str, outcome: &str) -> String {
        if outcome == input {
            self.canonical(config, outcome).into_owned()
        } else {
            outcome.to_owned()
        }
    }
}

/// Each configuration's position when the configurations are sorted by their sorted feature lists, ties kept in list order.
fn fold_ranks(sorted_features: &[Vec<&str>]) -> Vec<usize> {
    let mut order: Vec<usize> = (0..sorted_features.len()).collect();
    order.sort_by(|left, right| sorted_features[*left].cmp(&sorted_features[*right]));
    let mut ranks = vec![0; order.len()];
    for (rank, config) in order.into_iter().enumerate() {
        ranks[config] = rank;
    }
    ranks
}

/// One published rule in canonical labels: its five slots (the backtrack and the four lookahead classes, `None` where unconstrained), its outcome, and its [`bucket`].
pub struct SharedRule {
    pub slots: [CanonicalClass; 5],
    pub outcome: Box<str>,
    pub bucket: u8,
    pub key: Box<str>,
}

/// Where the emitter's sort puts a rule (`emit_gsub._ordered_settle_rules`), from its slots in canonical labels: the rules with a backtrack ahead of those without, the ZWNJ backtrack guards ahead of the other backtrack rules, and inside each, the rules whose lookahead names a marker copy (a tag with features, or its locked copy) or `uni200C` ahead of the rest. A lower bucket ships earlier, and the sort is stable, so rules of one bucket ship in the order the fold appends them.
pub fn bucket<S: AsRef<str>>(slots: [Option<&[S]>; 5]) -> u8 {
    let named = |slot: Option<&[S]>, wanted: &dyn Fn(&str) -> bool| {
        slot.is_some_and(|members| members.iter().any(|member| wanted(member.as_ref())))
    };
    let marker = |label: &str| {
        label
            .split_once(TAG_SEPARATOR)
            .is_some_and(|(_, state)| !state.is_empty())
            || label == "uni200C"
    };
    let unconstrained_left = slots[0].is_none_or(|members| members.is_empty());
    let zwnj_guard = named(slots[0], &|label| label == "uni200C");
    let early = slots[1..].iter().any(|slot| named(*slot, &marker));
    (u8::from(unconstrained_left) << 2) | (u8::from(!zwnj_guard) << 1) | u8::from(!early)
}

/// One configuration's rules as it publishes them for the others to evaluate, grouped by canonical input in table order, with the configuration's list position. They hold owned text, so they can cross threads.
#[derive(Default)]
pub struct SharedRules {
    pub config: usize,
    pub by_input: BTreeMap<Box<str>, Vec<SharedRule>>,
    pub keys: HashSet<Box<str>>,
}

/// The text two rules share exactly when the emitter folds them into one shipped rule (`emit_gsub._fold_rules` keys a rule by its input and its five slots, members in order, after renaming): the canonical input and slots, tab-separated.
fn rule_key<S: AsRef<str>>(input: &str, slots: [Option<&[S]>; 5]) -> Box<str> {
    let mut key = String::from(input);
    for slot in slots {
        key.push('\t');
        match slot {
            Some(members) => {
                for (at, member) in members.iter().enumerate() {
                    if at > 0 {
                        key.push(' ');
                    }
                    key.push_str(member.as_ref());
                }
            }
            None => key.push('-'),
        }
    }
    key.into_boxed_str()
}

/// A rule's five slots in `config`'s canonical labels.
fn canonical_slots(spellings: &Spellings, config: usize, rule: &Rule) -> [CanonicalClass; 5] {
    let class = |slot: &Option<Vec<Rc<str>>>| {
        slot.as_ref().map(|members| {
            members
                .iter()
                .map(|member| Box::from(&*spellings.canonical(config, member)))
                .collect::<Box<[Box<str>]>>()
        })
    };
    [
        class(&rule.backtrack),
        class(&rule.look1),
        class(&rule.look2),
        class(&rule.look3),
        class(&rule.look4),
    ]
}

/// One slot's class in canonical labels, `None` where the slot is unconstrained.
pub type CanonicalClass = Option<Box<[Box<str>]>>;

/// Each of `config`'s rules' [`bucket`], in rule order. The windows head carries them beside [`Spellings::fold_order`], and `emit_gsub._fold_rules` raises when either disagrees with the order it ships, so this module's model of that order is checked against the emitter on every build.
pub fn rule_buckets(spellings: &Spellings, config: usize, rules: &[Rule]) -> Vec<u8> {
    rules
        .iter()
        .map(|rule| {
            let slots = canonical_slots(spellings, config, rule);
            bucket(slots.each_ref().map(|slot| slot.as_deref()))
        })
        .collect()
}

impl SharedRules {
    /// The rules of `config` in canonical labels.
    pub fn of(spellings: &Spellings, config: usize, rules: &[Rule]) -> Self {
        let mut by_input: BTreeMap<Box<str>, Vec<SharedRule>> = BTreeMap::new();
        let mut keys: HashSet<Box<str>> = HashSet::default();
        for rule in rules {
            let slots = canonical_slots(spellings, config, rule);
            let bucket = bucket(slots.each_ref().map(|slot| slot.as_deref()));
            let input = spellings.canonical(config, &rule.input_glyph);
            let key = rule_key(&input, slots.each_ref().map(|slot| slot.as_deref()));
            keys.insert(key.clone());
            by_input
                .entry(Box::from(&*input))
                .or_default()
                .push(SharedRule {
                    slots,
                    outcome: Box::from(&*spellings.canonical_outcome(
                        config,
                        &rule.input_glyph,
                        &rule.outcome,
                    )),
                    bucket,
                    key,
                });
        }
        Self {
            config,
            by_input,
            keys,
        }
    }
}

/// One row a source sends a receiver: its key and outcome in canonical labels, the configuration it is live in (by list position), the source row's provenance and joint flag, and the tokens its shortest row chain fixes with its input's position among them (`certificate::fixed_tokens`), which a guard rule's certificate is completed from.
#[derive(Clone, Debug)]
pub struct ForeignRow {
    pub key: [String; 6],
    pub outcome: String,
    pub source: usize,
    pub provenance: Vec<String>,
    pub joint: bool,
    pub chain: Option<(Vec<RightToken>, usize)>,
}

/// One received row, in the receiver's labels.
#[derive(Clone, Debug)]
pub struct ImportedRow {
    pub key: [Rc<str>; 6],
    pub outcome: Rc<str>,
    pub source: usize,
    pub provenance: Vec<String>,
    pub joint: bool,
    pub chain: Option<(Vec<RightToken>, usize)>,
}

impl ImportedRow {
    pub fn key_text(&self) -> [&str; 6] {
        self.key.each_ref().map(|label| &**label)
    }
}

/// The first right slot (2 to 5 in key order) a key leaves open after a letter, where `#NA` stands for every label; `None` when the key carries every slot up to a boundary or its end. A `#NA` after a boundary stands for nothing, since no window reads past a boundary.
pub fn open_slot(key: &[&str; 6]) -> Option<usize> {
    (2..=5).find(|&slot| key[slot] == NA_LABEL && (slot == 2 || !boundaryish(key[slot - 1])))
}

/// Whether two keys with the same input and left describe a common window: they agree on every slot both carry, up to the first slot either leaves open.
fn overlapping(one: [&str; 6], other: [&str; 6]) -> bool {
    let end = open_slot(&one)
        .unwrap_or(6)
        .min(open_slot(&other).unwrap_or(6));
    (2..end).all(|slot| one[slot] == other[slot])
}

/// One rule of another configuration in the source's own labels: each class sorted, without the members that are tags here, which no row of the source carries.
struct Compiled {
    slots: [Option<Vec<Box<str>>>; 5],
    outcome: Box<str>,
    bucket: u8,
    key: Box<str>,
}

impl Compiled {
    fn admits(&self, slot: usize, label: &str) -> bool {
        self.slots[slot].as_ref().is_none_or(|members| {
            members
                .binary_search_by(|member| (**member).cmp(label))
                .is_ok()
        })
    }

    fn constrains(&self, slot: usize) -> bool {
        self.slots[slot].is_some()
    }
}

/// `rules` in `source`'s labels. A rule a class of which keeps no member can match no row of the source, so it is left out, which changes no first match.
fn compile(spellings: &Spellings, source: usize, rules: &[SharedRule]) -> Vec<Compiled> {
    rules
        .iter()
        .filter_map(|rule| {
            let mut slots: [Option<Vec<Box<str>>>; 5] = Default::default();
            for (held, slot) in slots.iter_mut().zip(&rule.slots) {
                if let Some(members) = slot {
                    let mut local: Vec<Box<str>> = members
                        .iter()
                        .map(|member| spellings.local(source, member))
                        .filter(|member| !member.contains(TAG_SEPARATOR))
                        .map(|member| Box::from(&*member))
                        .collect();
                    if local.is_empty() {
                        return None;
                    }
                    local.sort();
                    local.dedup();
                    *held = Some(local);
                }
            }
            Some(Compiled {
                slots,
                outcome: Box::from(&*spellings.local(source, &rule.outcome)),
                bucket: rule.bucket,
                key: rule.key.clone(),
            })
        })
        .collect()
}

/// The configurations whose first match on some window of a row's key gives an outcome other than `outcome` from a rule that ships ahead of every first match that gives it, as a bit per list position, never the source's own. `candidates` holds each configuration's rules for the row's input that admit its left, in rule order. The key's slots up to its open slot must match as they stand, and from the open slot on every continuation counts ([`open_slot`]). A first match's place in the shipped order is its [`bucket`] and then, inside a bucket, the configuration's [`Spellings::rank`] for a wrong rule and `shipped_rank` for a right one (where its earliest holder ships it). A wrong rule at the same place as a right one is its configuration's own and precedes it in that table.
fn misanswering(
    candidates: &[Vec<&Compiled>],
    key: [&str; 6],
    outcome: &str,
    source: usize,
    ranks: &[usize],
    shipped_rank: &dyn Fn(&Compiled) -> usize,
) -> u64 {
    let open = open_slot(&key);
    let fixed = open.unwrap_or(6);
    let matching: Vec<Vec<&Compiled>> = candidates
        .iter()
        .map(|rules| {
            rules
                .iter()
                .copied()
                .filter(|rule| (2..fixed).all(|slot| rule.admits(slot - 1, key[slot])))
                .collect()
        })
        .collect();
    let leaf = |lists: &[Vec<&Compiled>]| -> u64 {
        let mut right: (u8, usize) = (u8::MAX, usize::MAX);
        for list in lists {
            if let Some(first) = list.first()
                && &*first.outcome == outcome
            {
                right = right.min((first.bucket, shipped_rank(first)));
            }
        }
        let mut flagged = 0u64;
        for (config, list) in lists.iter().enumerate() {
            if config == source {
                continue;
            }
            if let Some(first) = list.first()
                && &*first.outcome != outcome
                && (first.bucket, ranks[config]) <= right
            {
                flagged |= 1 << config;
            }
        }
        flagged
    };
    match open {
        None => leaf(&matching),
        Some(slot) => continuations(&matching, slot - 1, &leaf),
    }
}

/// [`misanswering`] over the continuations from rule slot `slot` on: every label some configuration's candidate names there, and one label none names (the run edge, which no class holds), each with the candidates of every configuration that admit it, and `leaf` at each continuation every configuration's first match decides.
fn continuations(
    lists: &[Vec<&Compiled>],
    slot: usize,
    leaf: &dyn Fn(&[Vec<&Compiled>]) -> u64,
) -> u64 {
    let settled = |list: &Vec<&Compiled>| {
        list.first()
            .is_none_or(|first| (slot..5).all(|later| !first.constrains(later)))
    };
    if slot >= 5 || lists.iter().all(settled) {
        return leaf(lists);
    }
    let reach = |list: &Vec<&Compiled>| {
        list.iter()
            .position(|rule| (slot..5).all(|later| !rule.constrains(later)))
            .map_or(list.len(), |at| at + 1)
    };
    let lists: Vec<&[&Compiled]> = lists.iter().map(|list| &list[..reach(list)]).collect();
    let mut atoms: Vec<Option<&str>> = lists
        .iter()
        .flat_map(|list| list.iter())
        .filter_map(|rule| rule.slots[slot].as_ref())
        .flatten()
        .map(|member| Some(&**member))
        .collect();
    atoms.sort_unstable();
    atoms.dedup();
    atoms.push(None);
    let mut tried: HashSet<Vec<Vec<usize>>> = HashSet::default();
    let mut flagged = 0u64;
    for atom in atoms {
        let admitting: Vec<Vec<usize>> = lists
            .iter()
            .map(|list| {
                (0..list.len())
                    .filter(|&at| match atom {
                        Some(label) => list[at].admits(slot, label),
                        None => !list[at].constrains(slot),
                    })
                    .collect()
            })
            .collect();
        if !tried.insert(admitting.clone()) {
            continue;
        }
        let narrowed: Vec<Vec<&Compiled>> = admitting
            .iter()
            .zip(&lists)
            .map(|(picked, list)| picked.iter().map(|&at| list[at]).collect())
            .collect();
        flagged |= continuations(&narrowed, slot + 1, leaf);
    }
    flagged
}

/// One left of one input in a source's rows: its label-grain span and the class of lefts whose rows it shares.
struct SourceLeft {
    left: Rc<str>,
    start: usize,
    end: usize,
    class: u32,
}

/// One input of a source's rows: its span and its lefts in key order.
struct SourceInput {
    start: usize,
    end: usize,
    lefts: Vec<SourceLeft>,
}

/// A configuration's own rows indexed for evaluating other configurations' rules: per input, each left's span and a class id shared by the lefts whose rows are the same. Two lefts with the same rows that another configuration's rules admit alike are answered alike, so a source evaluates one left per such pair and repeats what it finds for the rest.
pub struct SourceRows {
    inputs: HashMap<Rc<str>, SourceInput>,
}

impl SourceRows {
    /// Indexes `rows`, a configuration's own label-grain rows in key order. Two lefts share a class when their class-grain rows carry the same right slots and outcomes, read as ids of the product's one label pool.
    pub fn of(rows: &LabelRows<'_>) -> Self {
        let product = rows.product();
        let mut inputs: HashMap<Rc<str>, SourceInput> = HashMap::default();
        let mut start = 0;
        while start < rows.len() {
            let input_id = rows.base(start).input_glyph;
            let mut end = start + 1;
            while end < rows.len() && rows.base(end).input_glyph == input_id {
                end += 1;
            }
            let mut classes: HashMap<Vec<[u32; 5]>, u32> = HashMap::default();
            let mut lefts: Vec<SourceLeft> = Vec::new();
            let mut at = start;
            while at < end {
                let left_id = rows.base(at).left;
                let mut stop = at + 1;
                while stop < end && rows.base(stop).left == left_id {
                    stop += 1;
                }
                let first = rows.class_row(at) as usize;
                let last = rows.class_row(stop - 1) as usize;
                let signature: Vec<[u32; 5]> = product.transitions[first..=last]
                    .iter()
                    .map(|row| {
                        [
                            row.right1.0,
                            row.right2.0,
                            row.right3.0,
                            row.right4.0,
                            product.outcomes[row.settled.index()].0,
                        ]
                    })
                    .collect();
                let next = classes.len() as u32;
                let class = *classes.entry(signature).or_insert(next);
                lefts.push(SourceLeft {
                    left: Rc::clone(rows.left(at)),
                    start: at,
                    end: stop,
                    class,
                });
                at = stop;
            }
            inputs.insert(
                Rc::clone(rows.input_glyph(start)),
                SourceInput { start, end, lefts },
            );
            start = end;
        }
        Self { inputs }
    }
}

/// One configuration as a source of foreign rows: the spec, the build's spellings, its position among them, its own label-grain rows with their index and row chains, and every configuration's published rules, its own included, indexed by list position.
#[derive(Clone, Copy)]
pub struct Source<'a> {
    pub index: &'a SpecIndex,
    pub spellings: &'a Spellings,
    pub config: usize,
    pub rows: LabelRows<'a>,
    pub indexed: &'a SourceRows,
    pub chains: &'a RowChains,
    pub published: &'a [&'a SharedRules],
}

/// The row of `rows[start..end]` whose key is `key`, by binary search over the key order.
fn find_row(rows: &LabelRows<'_>, start: usize, end: usize, key: [&str; 6]) -> Option<usize> {
    let mut low = start;
    let mut high = end;
    while low < high {
        let mid = low + (high - low) / 2;
        match rows.key(mid).cmp(&key) {
            std::cmp::Ordering::Less => low = mid + 1,
            std::cmp::Ordering::Greater => high = mid,
            std::cmp::Ordering::Equal => return Some(mid),
        }
    }
    None
}

/// The rows of the source `from` that each other configuration's rules answer wrongly where the shipped order lets that answer fire, one list per configuration in list order (the source's own empty), each sorted by key. `inputs` limits the evaluation to those canonical inputs, `None` meaning every input.
///
/// The shipped first match on a window is the first match of whichever configuration's own first match ships earliest, since the emitter's sort keeps each table's order among the rules that match one window and the fold keeps a rule at its first occurrence. So a row is sent to a configuration when that configuration's first match on some window of the row gives another outcome and ships ahead of every first match, in any configuration, that gives the row's outcome (`misanswering`). The source's own first match is one of those, so a wrong rule shipped after it never needs a row; this is how a marker-copy rule's head start in the shipped order already protects the windows that name the copy. A row whose outcome no configuration's rule gives, because it settles to the input, is sent to every configuration whose rule matches it.
///
/// For each input, each class of lefts that share their rows and that every configuration's backtrack classes admit alike is evaluated once, over its first left's rows, and every left of the class sends the rows found. A row with a boundary in some slot also sends the rows that differ from it only by another boundary there, which the source holds beside it: the receiver's boundary lefts must keep one signature, and a boundary rule and the fallback behind it each need a row to reach them.
pub fn exports(from: &Source<'_>, inputs: Option<&HashSet<Box<str>>>) -> Vec<Vec<ForeignRow>> {
    let Source {
        index,
        spellings,
        config: source,
        rows,
        indexed: source_rows,
        chains,
        published,
    } = *from;
    let count = published.len();
    let ranks: Vec<usize> = (0..count).map(|config| spellings.rank(config)).collect();
    let shipped_rank = |rule: &Compiled| {
        published
            .iter()
            .filter(|rules| rules.keys.contains(&rule.key))
            .map(|rules| spellings.rank(rules.config))
            .min()
            .unwrap_or(usize::MAX)
    };
    let all_inputs: std::collections::BTreeSet<&Box<str>> = published
        .iter()
        .flat_map(|rules| rules.by_input.keys())
        .collect();
    let mut picked: Vec<Vec<usize>> = vec![Vec::new(); count];
    for canonical_input in all_inputs {
        if inputs.is_some_and(|wanted| !wanted.contains(canonical_input)) {
            continue;
        }
        let input = spellings.local(source, canonical_input);
        if input.contains(TAG_SEPARATOR) {
            continue;
        }
        let Some(own) = source_rows.inputs.get(&*input) else {
            continue;
        };
        let compiled: Vec<Vec<Compiled>> = published
            .iter()
            .map(|rules| {
                rules
                    .by_input
                    .get(canonical_input)
                    .map(|rules| compile(spellings, source, rules))
                    .unwrap_or_default()
            })
            .collect();
        if compiled
            .iter()
            .enumerate()
            .all(|(config, rules)| config == source || rules.is_empty())
        {
            continue;
        }
        let admitted: Vec<HashMap<Box<str>, Vec<u32>>> = compiled
            .iter()
            .map(|rules| {
                let mut by_left: HashMap<Box<str>, Vec<u32>> = HashMap::default();
                for (at, rule) in rules.iter().enumerate() {
                    for member in rule.slots[0].iter().flatten() {
                        by_left.entry(member.clone()).or_default().push(at as u32);
                    }
                }
                by_left
            })
            .collect();
        let mut classes: HashMap<(u32, Vec<Vec<u32>>), usize> = HashMap::default();
        let mut groups: Vec<Vec<usize>> = Vec::new();
        for (at, left) in own.lefts.iter().enumerate() {
            let class = (
                left.class,
                admitted
                    .iter()
                    .map(|by_left| by_left.get(&*left.left).cloned().unwrap_or_default())
                    .collect(),
            );
            let next = groups.len();
            let group = *classes.entry(class).or_insert(next);
            if group == next {
                groups.push(Vec::new());
            }
            groups[group].push(at);
        }
        let mut found: Vec<Vec<usize>> = vec![Vec::new(); count];
        for group in &groups {
            let first = &own.lefts[group[0]];
            let candidates: Vec<Vec<&Compiled>> = compiled
                .iter()
                .map(|rules| {
                    rules
                        .iter()
                        .filter(|rule| rule.admits(0, &first.left))
                        .collect()
                })
                .collect();
            if candidates
                .iter()
                .enumerate()
                .all(|(config, rules)| config == source || rules.is_empty())
            {
                continue;
            }
            for row in first.start..first.end {
                let key = rows.key(row);
                let flagged = misanswering(
                    &candidates,
                    key,
                    rows.outcome(row),
                    source,
                    &ranks,
                    &shipped_rank,
                );
                if flagged == 0 {
                    continue;
                }
                for &other in group {
                    let left = &own.lefts[other];
                    let mut wanted = key;
                    wanted[1] = &left.left;
                    if let Some(at) = find_row(&rows, left.start, left.end, wanted) {
                        for (receiver, held) in found.iter_mut().enumerate() {
                            if flagged & (1 << receiver) != 0 {
                                held.push(at);
                            }
                        }
                    }
                }
            }
        }
        for (receiver, mut queue) in found.into_iter().enumerate() {
            let mut seen: HashSet<usize> = HashSet::default();
            while let Some(row) = queue.pop() {
                if !seen.insert(row) {
                    continue;
                }
                picked[receiver].push(row);
                let key = rows.key(row);
                for slot in 1..=5 {
                    if key[slot] == NA_LABEL || !boundaryish(key[slot]) {
                        continue;
                    }
                    let kin: &[&str] = if slot == 1 {
                        &BOUNDARY_LEFTS
                    } else {
                        &BOUNDARY_RIGHTS
                    };
                    for boundary in kin {
                        let mut wanted = key;
                        wanted[slot] = boundary;
                        if let Some(at) = find_row(&rows, own.start, own.end, wanted) {
                            queue.push(at);
                        }
                    }
                }
            }
        }
    }
    picked
        .into_iter()
        .map(|mut rows_for| {
            rows_for.sort_unstable();
            rows_for.dedup();
            rows_for
                .into_iter()
                .map(|row| {
                    let key = rows.key(row);
                    ForeignRow {
                        key: spellings.canonical_key(source, key),
                        outcome: spellings.canonical_outcome(source, key[0], rows.outcome(row)),
                        source,
                        provenance: rows.provenance(row).to_vec(),
                        joint: rows.joint(row),
                        chain: certificate::fixed_tokens(index, chains, &rows, row)
                            .ok()
                            .flatten(),
                    }
                })
                .collect()
        })
        .collect()
}

/// The rows one configuration has imported from the others, in key order, with their labels interned so the merged views and the rules share them.
#[derive(Default)]
pub struct Imports {
    rows: Vec<ImportedRow>,
    pool: HashSet<Rc<str>>,
}

impl Imports {
    pub fn rows(&self) -> &[ImportedRow] {
        &self.rows
    }

    pub fn is_empty(&self) -> bool {
        self.rows.is_empty()
    }

    /// The imported rows of one input, which the key order keeps contiguous.
    pub fn for_input(&self, input: &str) -> &[ImportedRow] {
        let start = self.rows.partition_point(|row| &*row.key[0] < input);
        let end = start + self.rows[start..].partition_point(|row| &*row.key[0] == input);
        &self.rows[start..end]
    }

    fn intern(&mut self, text: &str) -> Rc<str> {
        if let Some(found) = self.pool.get(text) {
            return Rc::clone(found);
        }
        let shared: Rc<str> = Rc::from(text);
        self.pool.insert(Rc::clone(&shared));
        shared
    }

    /// Takes in what each source sent `receiver` this round, one batch per source in list order, and returns the inputs that gained a row, sorted. `own` is the receiver's own rows.
    ///
    /// A key two sources send with different outcomes is an error naming both, and so is a key that overlaps one of the receiver's own rows, or a row it imported, with a different outcome (`overlapping` reads `#NA` after a letter as every label): the shipped lookup cannot answer both. A key the receiver already holds with the same outcome is dropped, so an imported row never repeats an own row's key. A row with a boundary left must arrive for each boundary left the receiver has for that input, so its default block keeps one signature.
    pub fn absorb(
        &mut self,
        spellings: &Spellings,
        receiver: usize,
        own: &LabelRows<'_>,
        batches: Vec<Vec<ForeignRow>>,
    ) -> Result<Vec<Rc<str>>, String> {
        let tokens = spellings.tokens();
        let mut fresh: BTreeMap<[String; 6], ForeignRow> = BTreeMap::new();
        for batch in batches {
            for row in batch {
                let key: [String; 6] = row
                    .key
                    .each_ref()
                    .map(|label| spellings.local(receiver, label).into_owned());
                let outcome = spellings.local(receiver, &row.outcome).into_owned();
                if let Some(held) = fresh.get(&key) {
                    if held.outcome != outcome {
                        return Err(format!(
                            "{}: the window {} settles to {} under {} and to {} under {}; one shipped lookup cannot give both",
                            tokens[receiver],
                            key_repr(key.each_ref().map(String::as_str)),
                            held.outcome,
                            tokens[held.source],
                            outcome,
                            tokens[row.source],
                        ));
                    }
                    continue;
                }
                fresh.insert(key, ForeignRow { outcome, ..row });
            }
        }
        let mut accepted: Vec<([String; 6], ForeignRow)> = Vec::new();
        let mut pair_start = 0usize;
        for (key, row) in fresh {
            let text = key.each_ref().map(String::as_str);
            if accepted
                .get(pair_start)
                .is_some_and(|(held, _)| held[..2] != key[..2])
            {
                pair_start = accepted.len();
            }
            let held_at = self
                .rows
                .binary_search_by(|held| held.key_text().cmp(&text));
            if let Ok(at) = held_at {
                let held = &self.rows[at];
                if *held.outcome != *row.outcome {
                    return Err(conflict(
                        tokens,
                        receiver,
                        text,
                        &row,
                        held.key_text(),
                        &held.outcome,
                        &tokens[held.source],
                    ));
                }
                continue;
            }
            let mut covered = false;
            for at in overlapping_rows(own, text) {
                let held = own.key(at);
                if **own.outcome(at) != *row.outcome {
                    return Err(conflict(
                        tokens,
                        receiver,
                        text,
                        &row,
                        held,
                        own.outcome(at),
                        &tokens[receiver],
                    ));
                }
                covered |= held == text;
            }
            if covered {
                continue;
            }
            let first = self
                .rows
                .partition_point(|held| held.key_text()[..2] < text[..2]);
            for held in self.rows[first..]
                .iter()
                .take_while(|held| held.key_text()[..2] == text[..2])
            {
                if overlapping(held.key_text(), text) && *held.outcome != *row.outcome {
                    return Err(conflict(
                        tokens,
                        receiver,
                        text,
                        &row,
                        held.key_text(),
                        &held.outcome,
                        &tokens[held.source],
                    ));
                }
            }
            for (other, other_row) in &accepted[pair_start..] {
                let other_text = other.each_ref().map(String::as_str);
                if other_text[..2] == text[..2]
                    && overlapping(other_text, text)
                    && other_row.outcome != row.outcome
                {
                    return Err(conflict(
                        tokens,
                        receiver,
                        text,
                        &row,
                        other_text,
                        &other_row.outcome,
                        &tokens[other_row.source],
                    ));
                }
            }
            accepted.push((key, row));
        }
        check_boundary_lefts(tokens, receiver, own, &accepted)?;
        let mut changed: Vec<Rc<str>> = Vec::new();
        for (key, row) in accepted {
            let key: [Rc<str>; 6] = key.each_ref().map(|label| self.intern(label));
            if changed.last() != Some(&key[0]) {
                changed.push(Rc::clone(&key[0]));
            }
            let outcome = self.intern(&row.outcome);
            let mut provenance = row.provenance;
            provenance.push(format!("window live in {}", tokens[row.source]));
            self.rows.push(ImportedRow {
                key,
                outcome,
                source: row.source,
                provenance,
                joint: row.joint,
                chain: row.chain,
            });
        }
        self.rows
            .sort_by(|left, right| left.key_text().cmp(&right.key_text()));
        changed.sort();
        changed.dedup();
        Ok(changed)
    }
}

/// The own rows that overlap `key` ([`overlapping`]): those that carry every slot `key` carries up to its open slot, which are contiguous in key order, and those that stop at an earlier slot after agreeing with `key` up to it, at most one per slot. Each is a binary search, so a row with a large left costs no scan of its rows.
fn overlapping_rows(rows: &LabelRows<'_>, key: [&str; 6]) -> Vec<usize> {
    let open = open_slot(&key).unwrap_or(6);
    let mut found: Vec<usize> = Vec::new();
    for stop in 3..open {
        if boundaryish(key[stop - 1]) {
            break;
        }
        let mut shallow = key;
        for slot in &mut shallow[stop..] {
            *slot = NA_LABEL;
        }
        if let Some(at) = find_row(rows, 0, rows.len(), shallow) {
            found.push(at);
        }
    }
    let prefix = &key[..open];
    let start = partition(rows.len(), |row| rows.key(row)[..open] < *prefix);
    let end = partition(rows.len(), |row| rows.key(row)[..open] <= *prefix);
    found.extend(start..end);
    found
}

/// The half-open range of `rows` whose input is `input` and whose left is `left`.
fn pair_span(rows: &LabelRows<'_>, input: &str, left: &str) -> (usize, usize) {
    let before = |row: usize| {
        let key = rows.key(row);
        (key[0], key[1]) < (input, left)
    };
    let through = |row: usize| {
        let key = rows.key(row);
        (key[0], key[1]) <= (input, left)
    };
    (
        partition(rows.len(), before),
        partition(rows.len(), through),
    )
}

fn partition(count: usize, before: impl Fn(usize) -> bool) -> usize {
    let mut low = 0usize;
    let mut high = count;
    while low < high {
        let mid = low + (high - low) / 2;
        if before(mid) {
            low = mid + 1;
        } else {
            high = mid;
        }
    }
    low
}

/// The error for a received row that overlaps a held row (`held`, live in `holder`) with a different outcome.
fn conflict(
    tokens: &[String],
    receiver: usize,
    key: [&str; 6],
    row: &ForeignRow,
    held: [&str; 6],
    held_outcome: &str,
    holder: &str,
) -> String {
    format!(
        "{}: the window {} settles to {} under {}, but {} settles to {} under {}, and the two overlap; one shipped lookup cannot give both",
        tokens[receiver],
        key_repr(key),
        row.outcome,
        tokens[row.source],
        key_repr(held),
        held_outcome,
        holder,
    )
}

/// Checks that every accepted row with a boundary left arrived for exactly the boundary lefts `own` has for its input.
fn check_boundary_lefts(
    tokens: &[String],
    receiver: usize,
    own: &LabelRows<'_>,
    accepted: &[([String; 6], ForeignRow)],
) -> Result<(), String> {
    let mut arrived: BTreeMap<[&str; 5], Vec<&str>> = BTreeMap::new();
    for (key, _row) in accepted {
        if boundaryish(&key[1]) {
            arrived
                .entry([&key[0], &key[2], &key[3], &key[4], &key[5]])
                .or_default()
                .push(&key[1]);
        }
    }
    let mut held: HashMap<&str, Vec<&str>> = HashMap::default();
    for (rest, lefts) in &mut arrived {
        let input = rest[0];
        let wanted = held.entry(input).or_insert_with(|| {
            let mut found: Vec<&str> = BOUNDARY_LEFTS
                .iter()
                .copied()
                .filter(|left| {
                    let (start, end) = pair_span(own, input, left);
                    start < end
                })
                .collect();
            found.sort_unstable();
            found
        });
        lefts.sort_unstable();
        if lefts != wanted {
            return Err(format!(
                "{}: {} has the boundary lefts {:?} here, but the windows another configuration keeps live after {} arrive for {:?}",
                tokens[receiver], input, wanted, rest[1], lefts
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_open_slot_follows_a_letter_and_never_a_boundary() {
        assert_eq!(
            open_slot(&["qsSee", "L", "qsAt", "qsMay", "#NA", "#NA"]),
            Some(4)
        );
        assert_eq!(
            open_slot(&["qsSee", "L", "#EDGE", "#NA", "#NA", "#NA"]),
            None
        );
        assert_eq!(
            open_slot(&["qsSee", "L", "qsAt", "space", "#NA", "#NA"]),
            None
        );
        assert_eq!(open_slot(&["qsSee", "L", "a", "b", "c", "d"]), None);
    }

    #[test]
    fn keys_overlap_through_an_open_slot_and_not_past_a_difference() {
        let shallow = ["I", "L", "a", "b", "#NA", "#NA"];
        assert!(overlapping(shallow, ["I", "L", "a", "b", "c", "#NA"]));
        assert!(overlapping(["I", "L", "a", "b", "c", "#NA"], shallow));
        assert!(!overlapping(shallow, ["I", "L", "a", "c", "#NA", "#NA"]));
        assert!(!overlapping(
            shallow,
            ["I", "L", "a", "#EDGE", "#NA", "#NA"]
        ));
        assert!(overlapping(
            ["I", "L", "#EDGE", "#NA", "#NA", "#NA"],
            ["I", "L", "#EDGE", "#NA", "#NA", "#NA"]
        ));
    }

    fn rule(slots: [Option<&[&str]>; 5], outcome: &str, bucket: u8) -> Compiled {
        Compiled {
            slots: slots.map(|slot| {
                slot.map(|members| {
                    let mut held: Vec<Box<str>> = members.iter().map(|m| Box::from(*m)).collect();
                    held.sort();
                    held
                })
            }),
            outcome: Box::from(outcome),
            bucket,
            key: Box::from(""),
        }
    }

    /// A row whose third slot is open meets every continuation: a configuration whose rule constraining that slot fires first for some label there with another outcome, ahead of every right answer, is flagged, and one whose every continuation gets the row's outcome, or whose wrong rule ships after a right one, is not. A carried slot is read as it stands.
    #[test]
    fn an_open_slot_reads_every_continuation_against_the_shipped_order() {
        let ranks = [0, 1, 2];
        let own_rank =
            |rule: &Compiled| -> usize { rule.key.parse().expect("the test keys a rank") };
        let ranked =
            |slots: [Option<&[&str]>; 5], outcome: &str, bucket: u8, rank: &str| Compiled {
                key: Box::from(rank),
                ..rule(slots, outcome, bucket)
            };
        let source = vec![ranked([None, Some(&["a"]), None, None, None], "Y", 3, "0")];
        let deep = ranked(
            [None, Some(&["a"]), Some(&["b"]), Some(&["z"]), None],
            "Z",
            3,
            "1",
        );
        let fallback = ranked([None, Some(&["a"]), None, None, None], "Y", 3, "1");
        let late = ranked([None, Some(&["a"]), None, None, None], "W", 7, "2");
        let row = ["I", "L", "a", "b", "#NA", "#NA"];
        fn lists<'r>(
            source: &'r [Compiled],
            one: Vec<&'r Compiled>,
            two: Vec<&'r Compiled>,
        ) -> Vec<Vec<&'r Compiled>> {
            vec![source.iter().collect(), one, two]
        }
        assert_eq!(
            misanswering(
                &lists(&source, vec![&deep, &fallback], vec![]),
                row,
                "Y",
                0,
                &ranks,
                &own_rank
            ),
            0,
            "configuration 1 ranks after the source's right rule in the same bucket"
        );
        let early = ranked(
            [None, Some(&["a"]), Some(&["b"]), Some(&["z"]), None],
            "Z",
            2,
            "1",
        );
        assert_eq!(
            misanswering(
                &lists(&source, vec![&early, &fallback], vec![&late]),
                row,
                "Y",
                0,
                &ranks,
                &own_rank
            ),
            0b10,
            "an earlier bucket ships first; a later one does not"
        );
        assert_eq!(
            misanswering(
                &lists(&source, vec![&early, &fallback], vec![]),
                ["I", "L", "a", "b", "q", "#NA"],
                "Y",
                0,
                &ranks,
                &own_rank
            ),
            0,
            "a carried slot is read as it stands"
        );
        assert_eq!(
            misanswering(
                &[vec![], vec![&late], vec![]],
                row,
                "I",
                0,
                &ranks,
                &own_rank
            ),
            0b10,
            "with no rule giving the outcome, any wrong rule fires"
        );
    }

    /// The emitter's sort key in buckets: a backtrack ahead of none, a ZWNJ backtrack guard ahead of the other backtracks, and a marker copy or `uni200C` in the lookahead ahead of the rest. A tag without features is the bare rune, which is not a marker copy.
    #[test]
    fn a_rule_s_bucket_is_the_emitter_s_sort_key() {
        let of = |slots: [Option<&[&str]>; 5]| bucket(slots);
        let zwnj: &[&str] = &["uni200C"];
        let left: &[&str] = &["qsTea.full"];
        let copy: &[&str] = &["qsTea@ss03", "qsAt"];
        let bare: &[&str] = &["qsTea@", "qsAt"];
        let boundary: &[&str] = &["uni200C", "space", "periodcentered"];
        assert_eq!(of([Some(zwnj), Some(copy), None, None, None]), 0);
        assert_eq!(of([Some(zwnj), None, None, None, None]), 1);
        assert_eq!(of([Some(left), Some(boundary), None, None, None]), 2);
        assert_eq!(of([Some(left), Some(bare), None, None, None]), 3);
        assert_eq!(of([None, None, Some(copy), None, None]), 6);
        assert_eq!(of([None, Some(bare), None, None, None]), 7);
    }

    /// A row that settles to its own raw input leaves a marker rune's raw label as the next window's left, and the emitter renames it per configuration in the backtrack as in the lookahead. So the left is canonical like every other slot, and two configurations' rules over that raw left are two shipped rules, never one; a settled left reads the same under both.
    #[test]
    fn a_raw_left_is_spelled_per_configuration() {
        let states: Vec<HashMap<String, String>> = ["", "ss03"]
            .iter()
            .map(|state| {
                [("qsTea".to_owned(), (*state).to_owned())]
                    .into_iter()
                    .collect()
            })
            .collect();
        let spellings = Spellings {
            tokens: ["default", "ss03"].map(str::to_owned).to_vec(),
            states,
            ranks: fold_ranks(&[vec![], vec!["ss03"]]),
        };
        let key = ["qsPea", "qsTea", "qsAt", "#NA", "#NA", "#NA"];
        assert_eq!(spellings.canonical_key(0, key)[1], "qsTea@");
        assert_eq!(spellings.canonical_key(1, key)[1], "qsTea@ss03");
        let after = |left: &str| Rule {
            input_glyph: Rc::from("qsPea"),
            backtrack: Some(vec![Rc::from(left)]),
            look1: None,
            look2: None,
            look3: None,
            look4: None,
            outcome: Rc::from("qsPea.half"),
            provenance: Vec::new(),
            joint: false,
        };
        let raw = [after("qsTea")];
        let default = SharedRules::of(&spellings, 0, &raw);
        let ss03 = SharedRules::of(&spellings, 1, &raw);
        assert!(default.keys.is_disjoint(&ss03.keys));
        let settled = [after("qsTea.full")];
        assert_eq!(
            SharedRules::of(&spellings, 0, &settled).keys,
            SharedRules::of(&spellings, 1, &settled).keys
        );
    }

    /// The marker partition: a rune unlocked by ss03 only, one by ss05 only, and one by both, under default, ss03, ss05 and ss03+ss05. A configuration writes its own copy as the raw label and another's as a tag naming that configuration's features, and a translation through the canonical form round-trips.
    #[test]
    fn the_marker_partition_spells_each_configuration_s_copy() {
        let mut states: Vec<HashMap<String, String>> = Vec::new();
        for features in [vec![], vec!["ss03"], vec!["ss05"], vec!["ss03", "ss05"]] {
            let state: HashMap<String, String> = [
                ("qsMay", vec!["ss03"]),
                ("qsIt", vec!["ss05"]),
                ("qsTea", vec!["ss03", "ss05"]),
            ]
            .into_iter()
            .map(|(rune, unlocked)| {
                let on: Vec<&str> = unlocked
                    .into_iter()
                    .filter(|feature| features.contains(feature))
                    .collect();
                (rune.to_owned(), on.join("_"))
            })
            .collect();
            states.push(state);
        }
        let spellings = Spellings {
            tokens: ["default", "ss03", "ss05", "ss03+ss05"]
                .map(str::to_owned)
                .to_vec(),
            states,
            ranks: fold_ranks(&[vec![], vec!["ss03"], vec!["ss05"], vec!["ss03", "ss05"]]),
        };
        assert_eq!(
            (0..4)
                .map(|config| spellings.rank(config))
                .collect::<Vec<_>>(),
            [0, 1, 3, 2],
            "the emitter folds ss03+ss05 before ss05"
        );
        assert_eq!(
            spellings.fold_order(),
            ["default", "ss03", "ss03+ss05", "ss05"]
        );
        assert_eq!(spellings.canonical(0, "qsTea"), "qsTea@");
        assert_eq!(spellings.canonical(3, "qsTea"), "qsTea@ss03_ss05");
        assert_eq!(
            spellings.canonical(1, "qsTea.noentry"),
            "qsTea.noentry@ss03"
        );
        assert_eq!(spellings.canonical(1, "qsPea"), "qsPea");
        assert_eq!(
            spellings.canonical(1, "qsTea.full.en-y0"),
            "qsTea.full.en-y0"
        );
        assert_eq!(
            spellings.translate(0, 2, "qsMay"),
            "qsMay",
            "ss05 sets nothing for qsMay"
        );
        assert_eq!(spellings.translate(0, 1, "qsMay"), "qsMay@");
        assert_eq!(spellings.translate(1, 3, "qsMay"), "qsMay", "both set ss03");
        assert_eq!(spellings.translate(2, 0, "qsIt"), "qsIt@ss05");
        assert_eq!(spellings.translate(0, 3, "qsTea.noentry"), "qsTea.noentry@");
        assert_eq!(spellings.translate(3, 0, "qsTea"), "qsTea@ss03_ss05");
        assert_eq!(
            spellings.translate(0, 3, &spellings.translate(3, 0, "qsTea")),
            "qsTea",
            "a tag goes home as the raw label"
        );
    }
}
