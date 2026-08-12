//! The section 6.2 extensional specificity order, `rebuild/pipeline/specificity.py`.
//!
//! A record's specificity is not a number and not a declaration order; it is the set of windows the record's `when:` matches. Every constrained axis expands to its concrete match set over the finite registry, and record A outranks B when A's set is contained in B's on every axis B constrains, strictly so on at least one. That is what makes a literal family list, a predicate class, and a mixed literal-plus-class condition comparable for free — narrowness within an axis is set inclusion after expansion, so nothing has to know that `qsTea` happens to be a member of some class. Non-nested overlap with conflicting demands is the hard error E-INCOMPARABLE, because the records provably co-match a window and the kernel refuses to guess which one the author meant.
//!
//! The port's one structural decision is how an axis is keyed. Python keys the expansion by a dotted path — `left.family`, `right.then.then.is` — and only ever uses those keys to line two records' axes up against each other, so the spelling never reaches an output. [`AxisKey`] is that path as a packed value instead: which side, how many `then:` hops deep, and which of the five condition axes. It has to distinguish exactly what the strings distinguish, arbitrarily deep chains included, which is why the depth is a counter rather than a `then`-or-not flag: a fact stated two hops out and the same fact stated one hop out are different constraints, and collapsing them would silently make one record outrank another it merely resembles.
//!
//! Evaluation is stratified and stays that way here: predicate-class membership arrives pre-resolved from the registry through [`SpecIndex::class_members`], so expanding a policy condition never re-enters settlement. The one place expansion is deliberately approximate is `except:` — a carve-out that constrains anything beyond the family axis is ignored rather than modeled, an over-approximation that can only push a pair toward INCOMPARABLE, which is the refuse-to-guess direction.

use std::collections::{BTreeSet, HashMap, HashSet};

use crate::error::SettleError;
use crate::index::SpecIndex;
use crate::model::{Condition, PolicyRecord, Sym, When};
use crate::types::provenance_pointer;

/// How two records' match sets sit relative to each other, `specificity.Ordering`.
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

/// Which of a condition's five expandable axes this is. `family:` and `class:` share one axis because they are conjunctive constraints on the same set of families; `stance:`, `joined_at:`, `stroke:` and `is:` each get their own.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ConditionAxis {
    Family,
    Stance,
    JoinedAt,
    Stroke,
    Is,
}

/// One expanded axis's identity — Python's dotted key, packed. `Side` carries the `then:` depth, so `right.family` is `depth 0` and `right.then.then.is` is `depth 2`.
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

/// Every constrained axis of one `when:`, expanded. An absent key means the axis is unconstrained, which is the axis universe and not the empty set — the asymmetry the comparison in [`compare_axes`] turns on.
pub type AxisSets = HashMap<AxisKey, BTreeSet<Sym>>;

/// Expand every constrained axis of a `when:` to its concrete match set, `specificity.axis_sets`. `owner` is the rune whose local groups a `class:` reference may resolve through.
pub fn axis_sets(
    index: &SpecIndex,
    when: &When,
    owner: Option<Sym>,
) -> Result<AxisSets, SettleError> {
    let mut axes = AxisSets::new();
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

/// The family-axis match set, or `None` when the axis is unconstrained. `specificity._family_set`: `family:` and `class:` on one condition are conjunctive, and `except:` entries that constrain only the family axis subtract from the result — a carve-out with any other axis is conservatively ignored, since modeling it would need the window that is not in hand.
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

/// The `is:` axis's match set, `specificity._is_set`. `boundary` is the one value that expands rather than standing for itself, and it expands to the four boundary kinds — which is what makes `is: boundary` comparable with `is: space`.
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

/// Compare two records' conditions extensionally, `specificity.outranks`.
pub fn outranks(
    index: &SpecIndex,
    a: &PolicyRecord,
    b: &PolicyRecord,
    owner_a: Option<Sym>,
    owner_b: Option<Sym>,
) -> Result<Ordering, SettleError> {
    let axes_a = axis_sets(index, &a.when, owner_a)?;
    let axes_b = axis_sets(index, &b.when, owner_b)?;
    Ok(compare_axes(&axes_a, &axes_b))
}

/// The comparison itself, over axes already expanded. Split out from [`outranks`] because the ranking stage compares every applicable record against every other one, so a caller that expands each record's axes once and calls this pairwise does the same work Python does without expanding the same `when:` a quadratic number of times.
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

/// What a record asks for, as the tie-collapsing comparison in [`pick_most_specific`] reads it: two maximal records demanding the same thing are not a conflict, whichever one is written first.
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

/// The demand a record makes, `specificity.pick_most_specific`'s default `demand` closure. `ok:` defaults to `[by, by]` per design section 3.3, so a record spelling that band out loud demands exactly what a record leaving it implicit does, and the two collapse instead of colliding.
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

/// Among records that all matched one concrete window, the unique most-specific one — `specificity.pick_most_specific`. Nesting resolves silently, because the narrow record wins by membership; several maximal records demanding the same thing collapse to the first in declaration order; several maximal records demanding different things are E-INCOMPARABLE, and the overlap is a fact rather than a possibility because the records have already co-matched.
///
/// `records` and `owners` are parallel, and a record is identified by its address exactly as Python identifies it by `is`, so the same record handed in twice is skipped against itself rather than compared with its twin. An empty `records` panics, as the Python original raises `ValueError`: it is a caller bug, and returning a settlement error instead would hand it to `_prospect`'s fallback, which swallows settlement errors and would therefore hide the bug in a wrong prospect rather than a crash.
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
    for (seat, record) in records.iter().enumerate() {
        let mut beaten = false;
        for (other_seat, other) in records.iter().enumerate() {
            if std::ptr::eq(*other, *record) {
                continue;
            }
            if outranks(index, other, record, owners[other_seat], owners[seat])?
                == Ordering::AOutranks
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
        "E-INCOMPARABLE: {} records co-match one window with non-nested conditions and conflicting demands: {}. Record a resolve with migrated: provenance.",
        maximal.len(),
        described.join("; ")
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;

    const HOST: &str = "qsHost";

    /// One `qsHost` policy record with an id, a pointer built from that id, and `by: 1` — each of which an override may replace, since the caller's fields are read first.
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

    /// The spec every test in this module reads: one rune, `qsHost`, whose `extend` list carries the conditions the section 6.2 cases are stated over, against the shared four-family registry and its one predicate class.
    fn host_spec() -> SpecIndex {
        let halves = fixtures::names(&["halves-that-exit-at-x-height"]);
        let tea = fixtures::names(&["qsTea"]);
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
                    &left(&[("klass", &fixtures::names(&["utter-pass-through-vetoes"]))]),
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
                            "utter-pass-through-vetoes",
                            &fixtures::names(&["qsMay", "qsPea"]),
                        )]),
                    ),
                ]),
            )],
        );
        fixtures::index_of(&fixtures::dump(
            &fixtures::map(&[(HOST, &host)]),
            &fixtures::four_family_registry(),
        ))
    }

    fn axes_of(index: &SpecIndex, id: &str) -> AxisSets {
        let record = fixtures::extend(index, HOST, id);
        axis_sets(index, &record.when, Some(fixtures::sym(index, HOST)))
            .expect("every fixture class resolves")
    }

    /// The names one expanded axis holds, sorted so the assertion reads as a set rather than as interning order.
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
    fn a_multi_axis_except_is_ignored_rather_than_guessed_at() {
        let index = host_spec();
        assert_eq!(
            axes_of(&index, "left-class-carved-multi-axis"),
            axes_of(&index, "left-class"),
            "the carve-out constrains a stance too, so it subtracts nothing"
        );
        assert_eq!(
            ranked(&index, "left-class-carved-multi-axis", "left-class"),
            Ordering::Equal
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
        // The deeper hop is a constraint of its own, so the longer chain is strictly narrower rather than equal.
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
    fn crossing_axes_and_overlapping_lists_are_incomparable() {
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
        let complaint = picked(&index, &["self-entry-live", "right-it-by-two"])
            .expect_err("by 1 and by 2 are different demands");
        assert_eq!(
            complaint.kind(),
            crate::error::SettleErrorKind::Incomparable
        );
        assert_eq!(
            complaint.message(),
            "E-INCOMPARABLE: 2 records co-match one window with non-nested conditions and conflicting demands: qsHost.yaml:policy.extend.self-entry-live; qsHost.yaml:policy.extend.right-it-by-two. Record a resolve with migrated: provenance."
        );
    }

    #[test]
    fn a_record_with_no_provenance_is_described_by_its_kind() {
        let index = host_spec();
        let complaint = picked(&index, &["no-provenance", "right-it-by-two"])
            .expect_err("by 3 and by 2 are different demands");
        assert!(
            complaint
                .message()
                .contains("demands: extend; qsHost.yaml:policy.extend.right-it-by-two."),
            "{}",
            complaint.message()
        );
    }

    #[test]
    fn the_contract_versus_extend_overlap_resolves_by_membership() {
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
    fn a_record_keyed_on_a_declined_seam_is_narrower_than_its_sibling() {
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
        // The same record handed in twice is skipped against itself rather than compared with its twin.
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
            compare_axes(&AxisSets::new(), &AxisSets::new()),
            Ordering::Equal
        );
    }
}
