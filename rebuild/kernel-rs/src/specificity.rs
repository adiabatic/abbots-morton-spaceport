//! The §6.2 extensional specificity order, and its only implementation. Settlement uses it for every ranking question: the order prefers apply in, which extend or contract record supplies a window's adjustment, and whether two records that match the same window conflict.
//!
//! A record's specificity is its match set: every combination of axis values its `when:` admits, in a product space with one axis per [`AxisKey`]. Record A outranks B when A's set is a strict subset of B's. Equal sets are [`Ordering::Equal`], and every other pair is [`Ordering::Incomparable`], two records with disjoint sets included. Extend or contract records that match the same window, overlap without nesting, and demand different things raise E-INCOMPARABLE ([`pick_most_specific`]), because the kernel does not guess which one the author meant.
//!
//! Each constrained axis expands to its concrete match set over the finite registry. An unconstrained axis stands for every value, so it is wider than any list, even one that names every value the registry declares. Within an axis, narrowness is therefore set inclusion after expansion, which makes a literal family list, a predicate class, and a mixed literal-plus-class condition comparable without special cases: no code needs to know that `qsTea` is a member of some class. The axes are independent by definition, so the space also holds combinations no window presents, such as a family on a slot that holds a boundary, and the order is exact relative to this per-axis model, not to the windows the alphabet produces.
//!
//! [`AxisKey`] identifies an axis by side, `then:` depth, and condition axis. The depth is a counter, not a flag, because a fact stated two hops out and the same fact stated one hop out are different constraints. Treating them as one would let a record outrank another that only resembles it.
//!
//! `except:` is subtracted exactly. An entry that names only families, through `family:` or `class:`, narrows its condition's family axis ([`family_set`]). Any other entry, one that constrains another axis, carries a `then:` chain, or nests an `except:` of its own, is expanded the same way at the slot its condition tests, with its hops reading the slots after it as they do in the matcher, and subtracted from the whole match set ([`match_set`]). The result is a union of boxes, each box one value set per axis. A condition that lists no family of its own but carries a family-only `except:` starts from the registry's letter families, so its match set leaves out the boundary slots the matcher lets through.
//!
//! Evaluation is stratified: predicate-class membership comes pre-resolved from the registry through [`SpecIndex::class_members`], so expanding a policy condition never calls back into settlement.

use std::borrow::Cow;
use std::collections::{BTreeMap, BTreeSet};

use crate::error::SettleError;
use crate::hash::{HashMap, HashSet};
use crate::index::SpecIndex;
use crate::model::{Condition, PolicyRecord, Sym, When};
use crate::types::provenance_pointer;

/// How two records' match sets sit relative to each other.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Ordering {
    AOutranks,
    BOutranks,
    Equal,
    Incomparable,
}

/// Which side of a `when:` an axis belongs to.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum WhenSide {
    Left,
    Right,
}

/// One of a condition's five expandable axes. `family:` and `class:` share one axis because both constrain the same set of families and they intersect. `stance:`, `joined_at:`, `stroke:`, and `is:` each have their own.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ConditionAxis {
    Family,
    Stance,
    JoinedAt,
    Stroke,
    Is,
}

/// The identity of one expanded axis. `Side` records the `then:` depth: `right.family` is depth 0 and `right.then.then.is` is depth 2.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum AxisKey {
    Side {
        side: WhenSide,
        depth: u32,
        axis: ConditionAxis,
    },
    SelfEntry,
    SelfExit,
    Word,
    Feature,
}

/// Every constrained axis of one `when:`, expanded. A missing key means the axis is unconstrained, so it stands for every value, not for the empty set. [`compare_axes`] depends on this. It leaves out every `except:` entry that reaches past the family axis, which [`match_set`] subtracts.
pub type AxisSets = HashMap<AxisKey, BTreeSet<Sym>>;

/// The values one axis of a box admits: the listed ones, or every value but the listed ones. Subtracting a carve-out from an unconstrained axis leaves the second form, which is never empty, because an unconstrained axis is wider than any list.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Values {
    Only(BTreeSet<Sym>),
    AllBut(BTreeSet<Sym>),
}

/// What a box admits on an axis it does not constrain.
static EVERY: Values = Values::AllBut(BTreeSet::new());

impl Values {
    fn is_empty(&self) -> bool {
        matches!(self, Values::Only(listed) if listed.is_empty())
    }

    fn meet(&self, other: &Values) -> Values {
        match (self, other) {
            (Values::Only(a), Values::Only(b)) => {
                Values::Only(a.intersection(b).copied().collect())
            }
            (Values::Only(listed), Values::AllBut(dropped))
            | (Values::AllBut(dropped), Values::Only(listed)) => {
                Values::Only(listed.difference(dropped).copied().collect())
            }
            (Values::AllBut(a), Values::AllBut(b)) => Values::AllBut(a.union(b).copied().collect()),
        }
    }

    fn without(&self, other: &Values) -> Values {
        match (self, other) {
            (Values::Only(a), Values::Only(b)) => Values::Only(a.difference(b).copied().collect()),
            (Values::Only(a), Values::AllBut(b)) => {
                Values::Only(a.intersection(b).copied().collect())
            }
            (Values::AllBut(a), Values::Only(b)) => Values::AllBut(a.union(b).copied().collect()),
            (Values::AllBut(a), Values::AllBut(b)) => {
                Values::Only(b.difference(a).copied().collect())
            }
        }
    }
}

/// One box of a match set: the values each axis admits. A missing key admits every value, as in [`AxisSets`].
pub type AxisBox = BTreeMap<AxisKey, Values>;

/// A record's match set, as [`match_set`] expands it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MatchSet {
    /// One box, for a `when:` whose every `except:` entry narrows the family axis alone. No axis is empty.
    Axes(AxisSets),
    /// A union of boxes, each admitting at least one combination. An empty union is the empty set.
    Boxes(Vec<AxisBox>),
}

impl MatchSet {
    fn boxes(&self) -> Cow<'_, [AxisBox]> {
        match self {
            MatchSet::Axes(axes) => Cow::Owned(boxed(axes.clone())),
            MatchSet::Boxes(boxes) => Cow::Borrowed(boxes),
        }
    }
}

/// Expand a `when:` to its match set: the box [`axis_sets`] describes, minus every `except:` entry that reaches past the family axis, each expanded at the slot it tests. Nothing here is cached: every call reads class membership through [`SpecIndex::class_members`], so the settlement capture open around the call journals what the ranking read.
pub fn match_set(
    index: &SpecIndex,
    when: &When,
    owner: Option<Sym>,
) -> Result<MatchSet, SettleError> {
    let axes = axis_sets(index, when, owner)?;
    let carved = [when.left.as_ref(), when.right.as_ref()]
        .into_iter()
        .flatten()
        .any(carves_past_family);
    if !carved && !axes.values().any(BTreeSet::is_empty) {
        return Ok(MatchSet::Axes(axes));
    }
    let mut boxes = boxed(axes);
    subtract_carves(
        index,
        &mut boxes,
        when.left.as_ref(),
        owner,
        WhenSide::Left,
        0,
    )?;
    subtract_carves(
        index,
        &mut boxes,
        when.right.as_ref(),
        owner,
        WhenSide::Right,
        0,
    )?;
    Ok(MatchSet::Boxes(boxes))
}

/// Whether some `except:` entry on `cond` or down its `then:` chain reaches past the family axis, which makes the match set more than the one box its axes describe.
fn carves_past_family(cond: &Condition) -> bool {
    cond.except_
        .iter()
        .any(|excepted| !condition_constrains_only_family(excepted))
        || cond.then.as_deref().is_some_and(carves_past_family)
}

/// Subtract from `boxes` every `except:` entry on `cond` and down its `then:` chain that [`family_set`] leaves out. An entry tests the slot its condition tests, at `depth` on `side`, with its own hops reading the slots after it, as the matcher's `except:` does.
fn subtract_carves(
    index: &SpecIndex,
    boxes: &mut Vec<AxisBox>,
    cond: Option<&Condition>,
    owner: Option<Sym>,
    side: WhenSide,
    depth: u32,
) -> Result<(), SettleError> {
    let Some(cond) = cond else {
        return Ok(());
    };
    for excepted in &cond.except_ {
        if condition_constrains_only_family(excepted) {
            continue;
        }
        let mut carved = AxisSets::default();
        side_axes(index, Some(excepted), owner, side, depth, &mut carved)?;
        let mut carved = boxed(carved);
        subtract_carves(index, &mut carved, Some(excepted), owner, side, depth)?;
        *boxes = subtract(std::mem::take(boxes), &carved);
    }
    subtract_carves(index, boxes, cond.then.as_deref(), owner, side, depth + 1)
}

/// The one box `axes` describes, or no box when some axis admits nothing.
fn boxed(axes: AxisSets) -> Vec<AxisBox> {
    if axes.values().any(BTreeSet::is_empty) {
        return Vec::new();
    }
    vec![
        axes.into_iter()
            .map(|(key, listed)| (key, Values::Only(listed)))
            .collect(),
    ]
}

/// `boxes` minus the union `carved`, as a union of boxes.
fn subtract(mut boxes: Vec<AxisBox>, carved: &[AxisBox]) -> Vec<AxisBox> {
    for cut in carved {
        let mut kept = Vec::with_capacity(boxes.len());
        for whole in &boxes {
            box_minus(whole, cut, &mut kept);
        }
        boxes = kept;
    }
    boxes
}

/// One box minus another, pushed onto `out` as disjoint boxes: for each axis `cut` constrains, the part of `whole` outside `cut` on that axis and inside it on every axis visited before. A box that misses `cut` on some axis is kept whole.
fn box_minus(whole: &AxisBox, cut: &AxisBox, out: &mut Vec<AxisBox>) {
    if cut
        .iter()
        .any(|(key, carved)| admitted(whole, key).meet(carved).is_empty())
    {
        out.push(whole.clone());
        return;
    }
    let mut rest = whole.clone();
    for (key, carved) in cut {
        let values = admitted(&rest, key);
        let outside = values.without(carved);
        let inside = values.meet(carved);
        if !outside.is_empty() {
            let mut piece = rest.clone();
            piece.insert(*key, outside);
            out.push(piece);
        }
        rest.insert(*key, inside);
    }
}

fn admitted<'b>(region: &'b AxisBox, key: &AxisKey) -> &'b Values {
    region.get(key).unwrap_or(&EVERY)
}

/// Expand every constrained axis of a `when:` to its concrete match set. `owner` is the rune whose local groups a `class:` reference may resolve through.
pub fn axis_sets(
    index: &SpecIndex,
    when: &When,
    owner: Option<Sym>,
) -> Result<AxisSets, SettleError> {
    let mut axes = AxisSets::default();
    side_axes(
        index,
        when.left.as_ref(),
        owner,
        WhenSide::Left,
        0,
        &mut axes,
    )?;
    side_axes(
        index,
        when.right.as_ref(),
        owner,
        WhenSide::Right,
        0,
        &mut axes,
    )?;
    if let Some(state) = when.self_entry {
        axes.insert(AxisKey::SelfEntry, BTreeSet::from([state]));
    }
    if let Some(state) = when.self_exit {
        axes.insert(AxisKey::SelfExit, BTreeSet::from([state]));
    }
    if let Some(position) = when.word {
        axes.insert(AxisKey::Word, BTreeSet::from([position]));
    }
    if let Some(feature) = when.feature {
        axes.insert(AxisKey::Feature, BTreeSet::from([feature]));
    }
    Ok(axes)
}

fn side_axes(
    index: &SpecIndex,
    cond: Option<&Condition>,
    owner: Option<Sym>,
    side: WhenSide,
    depth: u32,
    axes: &mut AxisSets,
) -> Result<(), SettleError> {
    let Some(cond) = cond else {
        return Ok(());
    };
    let key = |axis: ConditionAxis| AxisKey::Side { side, depth, axis };
    if let Some(families) = family_set(index, cond, owner)? {
        axes.insert(key(ConditionAxis::Family), families);
    }
    if !cond.stance.is_empty() {
        axes.insert(
            key(ConditionAxis::Stance),
            cond.stance.iter().copied().collect(),
        );
    }
    if let Some(height) = cond.joined_at {
        axes.insert(key(ConditionAxis::JoinedAt), BTreeSet::from([height]));
    }
    if let Some(stroke) = cond.stroke {
        axes.insert(key(ConditionAxis::Stroke), BTreeSet::from([stroke]));
    }
    if let Some(kinds) = is_set(index, cond) {
        axes.insert(key(ConditionAxis::Is), kinds);
    }
    side_axes(index, cond.then.as_deref(), owner, side, depth + 1, axes)
}

/// The family-axis match set, or `None` when the axis is unconstrained. `family:` and `class:` on one condition intersect. `except:` entries that constrain only the family axis are subtracted, from the registry's letter families when the condition lists none of its own. Every other entry is left to [`match_set`], which subtracts it from the whole match set.
fn family_set(
    index: &SpecIndex,
    cond: &Condition,
    owner: Option<Sym>,
) -> Result<Option<BTreeSet<Sym>>, SettleError> {
    let mut base: Option<BTreeSet<Sym>> = if cond.family.is_empty() {
        None
    } else {
        Some(cond.family.iter().copied().collect())
    };
    for klass in &cond.klass {
        let members = index.class_members(*klass, owner)?;
        base = Some(match base {
            None => members.clone(),
            Some(narrowed) => narrowed.intersection(members).copied().collect(),
        });
    }
    if !cond.except_.is_empty() {
        let mut carve: BTreeSet<Sym> = BTreeSet::new();
        for excepted in &cond.except_ {
            if condition_constrains_only_family(excepted)
                && let Some(carved) = family_set(index, excepted, owner)?
            {
                carve.extend(carved);
            }
        }
        if !carve.is_empty() {
            let whole = base.unwrap_or_else(|| index.families().clone());
            base = Some(whole.difference(&carve).copied().collect());
        }
    }
    Ok(base)
}

fn condition_constrains_only_family(cond: &Condition) -> bool {
    (!cond.family.is_empty() || !cond.klass.is_empty())
        && cond.stance.is_empty()
        && cond.joined_at.is_none()
        && cond.stroke.is_none()
        && cond.is_token.is_none()
        && cond.then.is_none()
        && cond.except_.is_empty()
}

/// The `is:` axis's match set. `boundary` expands to the four boundary kinds, which makes `is: boundary` comparable with `is: space`. Every other value stands for itself.
fn is_set(index: &SpecIndex, cond: &Condition) -> Option<BTreeSet<Sym>> {
    let token = cond.is_token?;
    let vocab = index.vocab();
    if token == vocab.boundary {
        return Some(BTreeSet::from([
            vocab.edge,
            vocab.space,
            vocab.zwnj,
            vocab.namer_dot,
        ]));
    }
    Some(BTreeSet::from([token]))
}

/// Compare two records' conditions extensionally.
pub fn outranks(
    index: &SpecIndex,
    a: &PolicyRecord,
    b: &PolicyRecord,
    owner_a: Option<Sym>,
    owner_b: Option<Sym>,
) -> Result<Ordering, SettleError> {
    let set_a = match_set(index, &a.when, owner_a)?;
    let set_b = match_set(index, &b.when, owner_b)?;
    Ok(compare_match_sets(&set_a, &set_b))
}

/// Orders two already-expanded match sets by inclusion: A is inside B when A minus B is empty. Two single boxes go to [`compare_axes`]. The ranking stage compares every applicable record with every other, so a caller can expand each record's match set once and call this for each pair instead of re-expanding each `when:` per pair.
pub fn compare_match_sets(a: &MatchSet, b: &MatchSet) -> Ordering {
    if let (MatchSet::Axes(a), MatchSet::Axes(b)) = (a, b) {
        return compare_axes(a, b);
    }
    let (a, b) = (a.boxes(), b.boxes());
    let b_within = subtract(b.to_vec(), &a).is_empty();
    let a_within = subtract(a.into_owned(), &b).is_empty();
    match (a_within, b_within) {
        (true, true) => Ordering::Equal,
        (true, false) => Ordering::AOutranks,
        (false, true) => Ordering::BOutranks,
        (false, false) => Ordering::Incomparable,
    }
}

/// Compares two single-box match sets given as their axes: the order on two records whose every `except:` entry narrows the family axis alone. One box lies inside another exactly when, on every axis the outer box constrains, the inner box's values lie inside the outer box's, so the comparison runs axis by axis. It answers what [`compare_match_sets`] answers on the same boxes as long as no axis is empty, and [`match_set`] never hands it one that is.
pub fn compare_axes(a: &AxisSets, b: &AxisSets) -> Ordering {
    let mut a_le_b = true;
    let mut b_le_a = true;
    let mut strict_a = false;
    let mut strict_b = false;
    for (axis, set_a) in a {
        match b.get(axis) {
            // B leaves the axis unconstrained, so B's set is the universe and A's is inside it.
            None => {
                strict_a = true;
                b_le_a = false;
            }
            Some(set_b) => {
                if !set_a.is_subset(set_b) {
                    a_le_b = false;
                } else if set_a.len() < set_b.len() {
                    strict_a = true;
                }
                if !set_b.is_subset(set_a) {
                    b_le_a = false;
                } else if set_b.len() < set_a.len() {
                    strict_b = true;
                }
            }
        }
    }
    for axis in b.keys() {
        if !a.contains_key(axis) {
            strict_b = true;
            a_le_b = false;
        }
    }
    if a_le_b && b_le_a && !strict_a && !strict_b {
        return Ordering::Equal;
    }
    if a_le_b && strict_a {
        return Ordering::AOutranks;
    }
    if b_le_a && strict_b {
        return Ordering::BOutranks;
    }
    Ordering::Incomparable
}

/// What a record asks for. [`pick_most_specific`] compares demands so that two maximal records asking for the same thing are not a conflict.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Demand {
    pub by: Option<i64>,
    pub ok: Option<(i64, i64)>,
    pub bind: Option<Sym>,
    pub trim: Option<i64>,
    pub split: Option<(i64, i64)>,
    pub stance: Option<Sym>,
    pub entry: Option<Sym>,
    pub exit: Option<Sym>,
}

/// The demand a record makes. `ok:` defaults to `[by, by]` (design §3.3), so a record that writes that band explicitly makes the same demand as one that leaves it implicit, and the two do not conflict.
pub fn default_demand(record: &PolicyRecord) -> Demand {
    Demand {
        by: record.by,
        ok: record.ok.or_else(|| record.by.map(|by| (by, by))),
        bind: record.bind,
        trim: record.trim,
        split: record.split,
        stance: record.stance,
        entry: record.entry,
        exit: record.exit,
    }
}

/// Among records that all matched one concrete window, returns the unique most-specific one. Of two nested records, the narrower one wins. Several maximal records with the same demand collapse to the first in `records` order. Several maximal records with different demands raise E-INCOMPARABLE; the overlap is certain because the records have already matched the same window.
///
/// `records` and `owners` are parallel. Records are compared by address, so a record passed twice is not compared with its own copy. An empty `records` panics because it is a caller bug: a settlement error would reach the prospect's fallback, which catches settlement errors and would hide the bug as a wrong prospect.
pub fn pick_most_specific<'r>(
    index: &SpecIndex,
    records: &[&'r PolicyRecord],
    owners: &[Option<Sym>],
) -> Result<&'r PolicyRecord, SettleError> {
    assert!(
        !records.is_empty(),
        "pick_most_specific needs at least one record"
    );
    assert_eq!(
        records.len(),
        owners.len(),
        "pick_most_specific reads records and owners in parallel"
    );
    let mut maximal: Vec<&'r PolicyRecord> = Vec::new();
    for (position, record) in records.iter().enumerate() {
        let mut beaten = false;
        for (other_position, other) in records.iter().enumerate() {
            if std::ptr::eq(*other, *record) {
                continue;
            }
            if outranks(
                index,
                other,
                record,
                owners[other_position],
                owners[position],
            )? == Ordering::AOutranks
            {
                beaten = true;
                break;
            }
        }
        if !beaten {
            maximal.push(record);
        }
    }
    if maximal.len() == 1 {
        return Ok(maximal[0]);
    }
    let demands: HashSet<Demand> = maximal
        .iter()
        .map(|record| default_demand(record))
        .collect();
    if demands.len() == 1 {
        return Ok(maximal[0]);
    }
    let described: Vec<String> = maximal
        .iter()
        .map(|record| match &record.provenance {
            Some(provenance) => provenance_pointer(index, provenance),
            None => index.resolve(record.kind).to_owned(),
        })
        .collect();
    Err(SettleError::Incomparable(format!(
        "E-INCOMPARABLE: {} records co-match one window with non-nested conditions and conflicting demands: {}. Settle it by editing the records: narrow one record's when: so the conditions nest (the narrower record wins) or no longer overlap, make the records demand the same thing, or remove one.",
        maximal.len(),
        described.join("; ")
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;

    const HOST: &str = "qsHost";

    /// One `qsHost` policy record with an id, a provenance pointer built from that id, and `by: 1`. An override replaces any of these, because the first value given for a field wins and the caller's come first.
    fn authored(id: &str, overrides: &[(&str, &str)]) -> String {
        let named = fixtures::quote(id);
        let pointer = fixtures::names(&["qsHost.yaml", &format!("policy.extend.{id}")]);
        let mut fields: Vec<(&str, &str)> = overrides.to_vec();
        fields.extend_from_slice(&[
            ("kind", "\"extend\""),
            ("id", named.as_str()),
            ("provenance", pointer.as_str()),
            ("by", "1"),
        ]);
        fixtures::record(&fields)
    }

    fn left(overrides: &[(&str, &str)]) -> String {
        fixtures::when(&[("left", &fixtures::condition(overrides))])
    }

    fn right(overrides: &[(&str, &str)]) -> String {
        fixtures::when(&[("right", &fixtures::condition(overrides))])
    }

    /// The spec every test here reads: one rune, `qsHost`, whose `extend` list holds the conditions the §6.2 cases use, over the shared four-family registry with its one predicate class and one ligature family.
    fn host_spec() -> SpecIndex {
        let halves = fixtures::names(&["halves-that-exit-at-x-height"]);
        let tea = fixtures::names(&["qsTea"]);
        let it_then_may = fixtures::condition(&[
            ("family", &fixtures::names(&["qsIt"])),
            (
                "then",
                &fixtures::condition(&[("family", &fixtures::names(&["qsMay"]))]),
            ),
        ]);
        let extends = [
            authored("left-tea", &[("when", &left(&[("family", &tea)]))]),
            authored("left-class", &[("when", &left(&[("klass", &halves)]))]),
            authored(
                "left-tea-and-class",
                &[(
                    "when",
                    &left(&[
                        ("family", &fixtures::names(&["qsTea", "qsMay"])),
                        ("klass", &halves),
                    ]),
                )],
            ),
            authored(
                "left-tea-joined",
                &[(
                    "when",
                    &left(&[("family", &tea), ("joined_at", "\"x-height\"")]),
                )],
            ),
            authored("left-tea-twin", &[("when", &left(&[("family", &tea)]))]),
            authored(
                "left-tea-by-two",
                &[("when", &left(&[("family", &tea)])), ("by", "2")],
            ),
            authored(
                "left-tea-ok-band",
                &[("when", &left(&[("family", &tea)])), ("ok", "[1,1]")],
            ),
            authored(
                "left-class-carved",
                &[(
                    "when",
                    &left(&[
                        ("klass", &halves),
                        (
                            "except_",
                            &fixtures::seq(&[&fixtures::condition(&[(
                                "family",
                                &fixtures::names(&["qsPea"]),
                            )])]),
                        ),
                    ]),
                )],
            ),
            authored(
                "left-class-carved-multi-axis",
                &[(
                    "when",
                    &left(&[
                        ("klass", &halves),
                        (
                            "except_",
                            &fixtures::seq(&[&fixtures::condition(&[
                                ("family", &fixtures::names(&["qsPea"])),
                                ("stance", &fixtures::names(&["half"])),
                            ])]),
                        ),
                    ]),
                )],
            ),
            authored(
                "right-it-carved-chain",
                &[(
                    "when",
                    &right(&[
                        ("family", &fixtures::names(&["qsIt"])),
                        ("except_", &fixtures::seq(&[&it_then_may])),
                    ]),
                )],
            ),
            authored(
                "right-it-or-tea-carved-chain",
                &[(
                    "when",
                    &right(&[
                        ("family", &fixtures::names(&["qsIt", "qsTea"])),
                        ("except_", &fixtures::seq(&[&it_then_may])),
                    ]),
                )],
            ),
            authored(
                "right-it-or-tea-carved-back",
                &[(
                    "when",
                    &right(&[
                        ("family", &fixtures::names(&["qsIt", "qsTea"])),
                        (
                            "except_",
                            &fixtures::seq(&[&fixtures::condition(&[
                                ("family", &fixtures::names(&["qsIt"])),
                                ("except_", &fixtures::seq(&[&it_then_may])),
                            ])]),
                        ),
                    ]),
                )],
            ),
            authored(
                "right-it-then-may",
                &[("when", &fixtures::when(&[("right", &it_then_may)]))],
            ),
            authored(
                "right-tea-then-carved",
                &[(
                    "when",
                    &right(&[
                        ("family", &tea),
                        (
                            "then",
                            &fixtures::condition(&[(
                                "except_",
                                &fixtures::seq(&[
                                    &fixtures::condition(&[(
                                        "family",
                                        &fixtures::names(&["qsPea"]),
                                    )]),
                                    &it_then_may,
                                ]),
                            )]),
                        ),
                    ]),
                )],
            ),
            authored(
                "right-tea-it-may",
                &[("when", &right(&[("family", &tea), ("then", &it_then_may)]))],
            ),
            authored(
                "left-liga",
                &[(
                    "when",
                    &left(&[("family", &fixtures::names(&["qsPea_qsTea"]))]),
                )],
            ),
            authored(
                "left-liga-and-parts",
                &[(
                    "when",
                    &left(&[(
                        "family",
                        &fixtures::names(&["qsPea", "qsTea", "qsPea_qsTea"]),
                    )]),
                )],
            ),
            authored(
                "left-tea-may",
                &[(
                    "when",
                    &left(&[("family", &fixtures::names(&["qsTea", "qsMay"]))]),
                )],
            ),
            authored(
                "left-tea-it",
                &[(
                    "when",
                    &left(&[("family", &fixtures::names(&["qsTea", "qsIt"]))]),
                )],
            ),
            authored(
                "left-broad-four",
                &[(
                    "when",
                    &left(&[(
                        "family",
                        &fixtures::names(&["qsPea", "qsTea", "qsMay", "qsIt"]),
                    )]),
                )],
            ),
            authored(
                "left-group",
                &[(
                    "when",
                    &left(&[(
                        "klass",
                        &fixtures::names(&["utter-both-sides-baseline-vetoes"]),
                    )]),
                )],
            ),
            authored(
                "right-it",
                &[("when", &right(&[("family", &fixtures::names(&["qsIt"]))]))],
            ),
            authored(
                "right-it-by-two",
                &[
                    ("when", &right(&[("family", &fixtures::names(&["qsIt"]))])),
                    ("by", "2"),
                ],
            ),
            authored(
                "self-entry-live",
                &[("when", &fixtures::when(&[("self_entry", "\"live\"")]))],
            ),
            authored(
                "keyed-none",
                &[(
                    "when",
                    &fixtures::when(&[
                        ("self_entry", "\"none\""),
                        (
                            "right",
                            &fixtures::condition(&[("family", &fixtures::names(&["qsIt"]))]),
                        ),
                    ]),
                )],
            ),
            authored(
                "right-boundary",
                &[("when", &right(&[("is_token", "\"boundary\"")]))],
            ),
            authored(
                "right-chain",
                &[(
                    "when",
                    &right(&[
                        ("family", &tea),
                        (
                            "then",
                            &fixtures::condition(&[
                                ("family", &fixtures::names(&["qsMay"])),
                                (
                                    "then",
                                    &fixtures::condition(&[("is_token", "\"boundary\"")]),
                                ),
                            ]),
                        ),
                    ]),
                )],
            ),
            authored(
                "right-chain-shallow",
                &[(
                    "when",
                    &right(&[
                        ("family", &tea),
                        (
                            "then",
                            &fixtures::condition(&[("family", &fixtures::names(&["qsMay"]))]),
                        ),
                    ]),
                )],
            ),
            authored("unconstrained", &[]),
            authored(
                "no-provenance",
                &[
                    ("when", &fixtures::when(&[("self_entry", "\"live\"")])),
                    ("provenance", "null"),
                    ("by", "3"),
                ],
            ),
        ];
        let borrowed: Vec<&str> = extends.iter().map(String::as_str).collect();
        let contracts = [authored(
            "narrow-contract",
            &[
                ("kind", "\"contract\""),
                ("when", &left(&[("family", &tea)])),
            ],
        )];
        let host = fixtures::rune(
            HOST,
            &[(
                "policy",
                &fixtures::policy(&[
                    ("extend", &fixtures::seq(&borrowed)),
                    ("contract", &fixtures::seq(&[contracts[0].as_str()])),
                    (
                        "groups",
                        &fixtures::map(&[(
                            "utter-both-sides-baseline-vetoes",
                            &fixtures::names(&["qsMay", "qsPea"]),
                        )]),
                    ),
                ]),
            )],
        );
        fixtures::index_of(&fixtures::dump(
            &fixtures::map(&[(HOST, &host)]),
            &fixtures::ligature_family_registry(),
        ))
    }

    fn axes_of(index: &SpecIndex, id: &str) -> AxisSets {
        let record = fixtures::extend(index, HOST, id);
        axis_sets(index, &record.when, Some(fixtures::sym(index, HOST)))
            .expect("every fixture class resolves")
    }

    /// The names one expanded axis holds, sorted so the assertion does not depend on interning order.
    fn axis(index: &SpecIndex, id: &str, key: AxisKey) -> Vec<String> {
        let axes = axes_of(index, id);
        let set = axes
            .get(&key)
            .unwrap_or_else(|| panic!("{id} constrains {key:?}"));
        let mut names: Vec<String> = set
            .iter()
            .map(|name| index.resolve(*name).to_owned())
            .collect();
        names.sort();
        names
    }

    fn side_axis(side: WhenSide, depth: u32, axis: ConditionAxis) -> AxisKey {
        AxisKey::Side { side, depth, axis }
    }

    fn ranked(index: &SpecIndex, a: &str, b: &str) -> Ordering {
        let host = Some(fixtures::sym(index, HOST));
        outranks(
            index,
            fixtures::extend(index, HOST, a),
            fixtures::extend(index, HOST, b),
            host,
            host,
        )
        .expect("every fixture class resolves")
    }

    fn picked<'a>(index: &'a SpecIndex, ids: &[&str]) -> Result<&'a PolicyRecord, SettleError> {
        let records: Vec<&PolicyRecord> = ids
            .iter()
            .map(|id| fixtures::extend(index, HOST, id))
            .collect();
        let owners = vec![Some(fixtures::sym(index, HOST)); records.len()];
        pick_most_specific(index, &records, &owners)
    }

    #[test]
    fn a_class_reference_expands_to_its_registry_membership() {
        let index = host_spec();
        assert_eq!(
            axis(
                &index,
                "left-class",
                side_axis(WhenSide::Left, 0, ConditionAxis::Family)
            ),
            ["qsPea", "qsTea"]
        );
    }

    #[test]
    fn a_rune_local_group_expands_through_the_owner_scan() {
        let index = host_spec();
        assert_eq!(
            axis(
                &index,
                "left-group",
                side_axis(WhenSide::Left, 0, ConditionAxis::Family)
            ),
            ["qsMay", "qsPea"]
        );
    }

    #[test]
    fn a_family_list_and_a_class_are_conjunctive() {
        let index = host_spec();
        assert_eq!(
            axis(
                &index,
                "left-tea-and-class",
                side_axis(WhenSide::Left, 0, ConditionAxis::Family)
            ),
            ["qsTea"]
        );
    }

    #[test]
    fn an_except_entry_carves_the_family_axis() {
        let index = host_spec();
        assert_eq!(
            axis(
                &index,
                "left-class-carved",
                side_axis(WhenSide::Left, 0, ConditionAxis::Family)
            ),
            ["qsTea"]
        );
    }

    #[test]
    fn a_multi_axis_except_subtracts_only_where_every_axis_matches() {
        let index = host_spec();
        assert_eq!(
            axes_of(&index, "left-class-carved-multi-axis"),
            axes_of(&index, "left-class"),
            "the carve-out constrains a stance too, so it narrows no single axis"
        );
        assert_eq!(
            ranked(&index, "left-class-carved-multi-axis", "left-class"),
            Ordering::AOutranks,
            "it still removes qsPea in its half stance"
        );
        assert_eq!(
            ranked(&index, "left-class-carved", "left-class-carved-multi-axis"),
            Ordering::AOutranks,
            "carving out every qsPea removes more than carving out the half one"
        );
    }

    #[test]
    fn an_except_entry_carrying_a_chain_subtracts_only_what_its_chain_matches() {
        let index = host_spec();
        assert_eq!(
            axes_of(&index, "right-it-carved-chain"),
            axes_of(&index, "right-it"),
            "only a condition's own spine keys an axis, so the carve-out's chain adds none"
        );
        assert_eq!(
            ranked(&index, "right-it-carved-chain", "right-it"),
            Ordering::AOutranks,
            "qsIt before qsMay is carved out, so the carved record is a strict subset"
        );
    }

    #[test]
    fn a_chain_carve_out_that_leaves_records_overlapping_without_nesting_is_incomparable() {
        let index = host_spec();
        assert_eq!(
            ranked(&index, "right-it-or-tea-carved-chain", "right-it"),
            Ordering::Incomparable,
            "qsTea is only in the carved record, and qsIt before qsMay only in right-it"
        );
    }

    #[test]
    fn a_nested_except_gives_back_what_its_parent_carve_out_took() {
        let index = host_spec();
        assert_eq!(
            ranked(&index, "right-it-then-may", "right-it-or-tea-carved-back"),
            Ordering::AOutranks,
            "the carve-out takes qsIt except before qsMay, so qsIt before qsMay stays in"
        );
        assert_eq!(
            ranked(
                &index,
                "right-it-or-tea-carved-back",
                "right-it-or-tea-carved-chain"
            ),
            Ordering::Incomparable,
            "one record keeps qsIt only before qsMay and the other keeps it only before anything else"
        );
    }

    #[test]
    fn an_except_entry_inside_a_then_hop_subtracts_at_that_hop() {
        let index = host_spec();
        assert_eq!(
            ranked(&index, "right-tea-it-may", "right-tea-then-carved"),
            Ordering::Incomparable,
            "the hop's carve-out removes exactly qsTea, qsIt, qsMay, so the two sets are disjoint"
        );
    }

    #[test]
    fn a_ligature_family_name_ranks_as_an_ordinary_family() {
        let index = host_spec();
        assert_eq!(
            axis(
                &index,
                "left-liga",
                side_axis(WhenSide::Left, 0, ConditionAxis::Family)
            ),
            ["qsPea_qsTea"],
            "a ligature name expands to itself, never to the components it is spelled from"
        );
        assert_eq!(
            ranked(&index, "left-liga", "left-liga-and-parts"),
            Ordering::AOutranks
        );
        assert_eq!(
            ranked(&index, "left-liga-and-parts", "left-liga"),
            Ordering::BOutranks
        );
    }

    #[test]
    fn is_boundary_expands_to_the_four_boundary_kinds() {
        let index = host_spec();
        assert_eq!(
            axis(
                &index,
                "right-boundary",
                side_axis(WhenSide::Right, 0, ConditionAxis::Is)
            ),
            ["edge", "namer-dot", "space", "zwnj"]
        );
    }

    #[test]
    fn a_then_chain_keys_one_axis_set_per_hop() {
        let index = host_spec();
        assert_eq!(
            axis(
                &index,
                "right-chain",
                side_axis(WhenSide::Right, 0, ConditionAxis::Family)
            ),
            ["qsTea"]
        );
        assert_eq!(
            axis(
                &index,
                "right-chain",
                side_axis(WhenSide::Right, 1, ConditionAxis::Family)
            ),
            ["qsMay"]
        );
        assert_eq!(
            axis(
                &index,
                "right-chain",
                side_axis(WhenSide::Right, 2, ConditionAxis::Is)
            ),
            ["edge", "namer-dot", "space", "zwnj"]
        );
        assert_eq!(axes_of(&index, "right-chain").len(), 3);
        // The deeper hop is a constraint of its own, so the longer chain is strictly narrower.
        assert_eq!(
            ranked(&index, "right-chain", "right-chain-shallow"),
            Ordering::AOutranks
        );
    }

    #[test]
    fn a_literal_singleton_outranks_the_class_it_belongs_to() {
        let index = host_spec();
        assert_eq!(
            ranked(&index, "left-tea", "left-class"),
            Ordering::AOutranks
        );
        assert_eq!(
            ranked(&index, "left-class", "left-tea"),
            Ordering::BOutranks
        );
    }

    #[test]
    fn one_more_constrained_axis_outranks() {
        let index = host_spec();
        assert_eq!(
            ranked(&index, "left-tea-joined", "left-tea"),
            Ordering::AOutranks
        );
        assert_eq!(
            ranked(&index, "unconstrained", "left-tea"),
            Ordering::BOutranks
        );
    }

    #[test]
    fn identical_conditions_are_equal_whatever_they_demand() {
        let index = host_spec();
        assert_eq!(ranked(&index, "left-tea", "left-tea-twin"), Ordering::Equal);
        assert_eq!(
            ranked(&index, "left-tea", "left-tea-by-two"),
            Ordering::Equal
        );
    }

    #[test]
    fn non_nested_axes_and_overlapping_lists_are_incomparable() {
        let index = host_spec();
        assert_eq!(
            ranked(&index, "left-tea", "right-it"),
            Ordering::Incomparable
        );
        assert_eq!(
            ranked(&index, "left-tea-may", "left-tea-it"),
            Ordering::Incomparable
        );
    }

    #[test]
    fn an_except_narrowing_is_a_strict_subset() {
        let index = host_spec();
        assert_eq!(
            ranked(&index, "left-class-carved", "left-class"),
            Ordering::AOutranks
        );
    }

    #[test]
    fn a_nested_conflict_resolves_silently_to_the_narrow_record() {
        let index = host_spec();
        let winner =
            picked(&index, &["left-class", "left-tea-by-two"]).expect("nesting is not a conflict");
        assert_eq!(winner.id, Some(fixtures::sym(&index, "left-tea-by-two")));
    }

    #[test]
    fn an_equal_demand_at_a_non_nested_overlap_is_tolerated() {
        let index = host_spec();
        let winner = picked(&index, &["right-it", "self-entry-live"]).expect("both demand by 1");
        assert_eq!(
            winner.id,
            Some(fixtures::sym(&index, "right-it")),
            "the tie collapses to the first record in the order it was gathered in"
        );
    }

    #[test]
    fn the_ok_band_defaults_to_the_by_it_repeats() {
        let index = host_spec();
        let plain = fixtures::extend(&index, HOST, "left-tea");
        let spelled = fixtures::extend(&index, HOST, "left-tea-ok-band");
        assert_eq!(default_demand(plain), default_demand(spelled));
        assert_eq!(default_demand(plain).ok, Some((1, 1)));
        let winner = picked(&index, &["left-tea", "left-tea-ok-band"])
            .expect("an explicit band equal to the default is the same demand");
        assert_eq!(winner.id, Some(fixtures::sym(&index, "left-tea")));
    }

    #[test]
    fn conflicting_demands_at_a_non_nested_overlap_refuse_to_guess() {
        let index = host_spec();
        let error = picked(&index, &["self-entry-live", "right-it-by-two"])
            .expect_err("by 1 and by 2 are different demands");
        assert_eq!(error.kind(), crate::error::SettleErrorKind::Incomparable);
        assert_eq!(
            error.message(),
            "E-INCOMPARABLE: 2 records co-match one window with non-nested conditions and conflicting demands: qsHost.yaml:policy.extend.self-entry-live; qsHost.yaml:policy.extend.right-it-by-two. Settle it by editing the records: narrow one record's when: so the conditions nest (the narrower record wins) or no longer overlap, make the records demand the same thing, or remove one."
        );
    }

    #[test]
    fn a_record_with_no_provenance_is_described_by_its_kind() {
        let index = host_spec();
        let error = picked(&index, &["no-provenance", "right-it-by-two"])
            .expect_err("by 3 and by 2 are different demands");
        assert!(
            error
                .message()
                .contains("demands: extend; qsHost.yaml:policy.extend.right-it-by-two."),
            "{}",
            error.message()
        );
    }

    #[test]
    fn specificity_ranks_a_contract_and_an_extend_by_their_conditions_alone() {
        let index = host_spec();
        let host = Some(fixtures::sym(&index, HOST));
        let narrow = fixtures::contract(&index, HOST, "narrow-contract");
        let broad = fixtures::extend(&index, HOST, "left-broad-four");
        assert_eq!(
            outranks(&index, narrow, broad, host, host).expect("both expand"),
            Ordering::AOutranks
        );
    }

    #[test]
    fn a_record_keyed_on_a_declined_junction_is_narrower_than_its_sibling() {
        let index = host_spec();
        assert_eq!(
            ranked(&index, "keyed-none", "right-it"),
            Ordering::AOutranks
        );
        let winner =
            picked(&index, &["right-it", "keyed-none"]).expect("nesting is not a conflict");
        assert_eq!(winner.id, Some(fixtures::sym(&index, "keyed-none")));
    }

    #[test]
    fn one_record_is_its_own_winner() {
        let index = host_spec();
        let winner = picked(&index, &["left-tea"]).expect("a single record is maximal");
        assert_eq!(winner.id, Some(fixtures::sym(&index, "left-tea")));
        // The same record passed twice is not compared with its own copy.
        let record = fixtures::extend(&index, HOST, "left-tea");
        let owners = vec![Some(fixtures::sym(&index, HOST)); 2];
        let winner = pick_most_specific(&index, &[record, record], &owners)
            .expect("one record demands one thing");
        assert_eq!(winner.id, Some(fixtures::sym(&index, "left-tea")));
    }

    #[test]
    fn comparing_pre_expanded_axes_answers_what_outranks_answers() {
        let index = host_spec();
        let narrow = axes_of(&index, "left-tea");
        let broad = axes_of(&index, "left-class");
        assert_eq!(compare_axes(&narrow, &broad), Ordering::AOutranks);
        assert_eq!(compare_axes(&broad, &narrow), Ordering::BOutranks);
        assert_eq!(compare_axes(&narrow, &narrow), Ordering::Equal);
        assert_eq!(
            compare_axes(&AxisSets::default(), &AxisSets::default()),
            Ordering::Equal
        );
    }

    #[test]
    fn the_axis_by_axis_comparison_answers_what_the_subtraction_answers() {
        let index = host_spec();
        let ids = [
            "left-tea",
            "left-class",
            "left-tea-and-class",
            "left-tea-joined",
            "left-class-carved",
            "left-tea-may",
            "left-tea-it",
            "left-liga-and-parts",
            "right-it",
            "keyed-none",
            "right-boundary",
            "right-chain",
            "right-chain-shallow",
            "self-entry-live",
            "unconstrained",
        ];
        for a in ids {
            for b in ids {
                let (axes_a, axes_b) = (axes_of(&index, a), axes_of(&index, b));
                let boxes_a = MatchSet::Boxes(boxed(axes_a.clone()));
                let boxes_b = MatchSet::Boxes(boxed(axes_b.clone()));
                assert_eq!(
                    compare_match_sets(&boxes_a, &boxes_b),
                    compare_axes(&axes_a, &axes_b),
                    "{a} against {b}"
                );
            }
        }
    }
}
