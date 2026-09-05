//! One input's ordered rules — the largest and subtlest part of the fold, and the one whose output *is* the shipped GSUB ordering, so a divergence here is invisible to every count-based check and visible only in `settlement-<config>.tsv` and `M1.generated.fea`. Transcribed from `table._rules_for_input` and not re-derived, down to which sample row a rule takes its provenance from and which raise fires first; that function was held byte-identical to this one and then deleted, so what states the ordering now is the discipline below and the artifacts it writes.
//!
//! The discipline the ordering follows, restated from that function's own comment because it is what the transcription has to preserve: within one (input, backtrack) group the boundary-outcome row with `uni200C` explicit in the class comes first, so no later row of the window can match across a skipped ZWNJ; then the third- and fourth-slot bundles, each replaying the same shape one slot over, so deeper rules precede every shallower one; then the letter-constrained two-slot rules, where an identity outcome becomes an identity guard whenever a slot-dropped fallback follows; then the fallback, which catches the run edge that no positive lookahead class can match. Across groups the ZWNJ backtrack-slot guards lead, then the committed blocks, then the default block.
//!
//! Two structural facts make this cheaper here than in Python without changing an answer. The rows arrive key-sorted, so an input's rows are grouped by left and each left's rows are already in `(r1, r2, r3, r4)` order — which was both the insertion order the Python original's `group_rows` dict had and the order its `sorted(group_rows.items())` calls asked for, so one sorted slice answers both and a prefix range is a binary search rather than a scan. And a signature is a sorted, deduplicated vector rather than a hash set, which is the same equivalence relation `frozenset` imposes with an order that makes it hashable.

use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};
use std::hash::Hash;
use std::rc::Rc;

use crate::fold::{BOUNDARY_LOOKAHEAD_CLASS, LabelRows, NA_LABEL, Rule, boundaryish};
use crate::stream::{python_repr, python_tuple};

/// What one input's fold produced: its ordered rules, how many of them are identity guards, and the lefts a first-match-wins replay has to cover to cover them all.
pub struct RuleFold {
    pub rules: Vec<Rule>,
    pub identity_guards: i64,
    pub replay_lefts: HashSet<Rc<str>>,
}

/// A set of left blocks, which is what the outermost grouping partitions into a default one and the committed ones.
type Blocks = Vec<Vec<Rc<str>>>;

/// One member of a signature: the other slots' labels and the outcome they settle to. Four coordinates at every nesting depth but the outermost, which carries all four right slots.
type Signature<const N: usize> = Vec<[Rc<str>; N]>;

/// The canonical form of a signature — sorted and deduplicated, which is `frozenset`'s equivalence relation with an order it can be hashed and compared by.
fn canonical<const N: usize>(mut members: Signature<N>) -> Signature<N> {
    members.sort();
    members.dedup();
    members
}

/// Group the values by signature, sort each group's members, and sort the groups. Callers pass signatures built from present rows only, never the full other-slot label product: every value's product is the same per grouping, so identical present-maps imply identical missing-key sets, and grouping by the sparse signature yields exactly the partition the (missing -> None) product signature would — at O(rows) instead of O(label product), which is what keeps folding from regrowing quartically as depth-3/4 windows are authored. Class tokens are sound signature coordinates for the same reason the premise needs: ids are content-addressed by member set, so identical token signatures imply identical member sets, never two spellings of one set.
fn signature_blocks<K: Eq + Hash>(
    values: &[Rc<str>],
    mut signature_of: impl FnMut(&Rc<str>) -> K,
) -> Blocks {
    let mut groups: HashMap<K, Vec<Rc<str>>> = HashMap::new();
    for value in values {
        groups
            .entry(signature_of(value))
            .or_default()
            .push(Rc::clone(value));
    }
    let mut blocks: Blocks = groups
        .into_values()
        .map(|mut members| {
            members.sort();
            members
        })
        .collect();
    blocks.sort();
    blocks
}

/// One key against a prefix of labels, which is the comparison every lookup and range search in a group is one of.
fn compare(held: &[Rc<str>; 4], prefix: &[&Rc<str>]) -> Ordering {
    for (seat, wanted) in prefix.iter().enumerate() {
        let order = Ord::cmp(&held[seat], *wanted);
        if order != Ordering::Equal {
            return order;
        }
    }
    Ordering::Equal
}

/// One left's rows as the group logic reads them: the four right labels in sorted order, and the seat of the row in the input's slice.
struct GroupRows<'a> {
    rows: LabelRows<'a>,
    keys: Vec<([Rc<str>; 4], usize)>,
}

impl<'a> GroupRows<'a> {
    /// The rows of one left, which the key sort already leaves contiguous and in `(r1, r2, r3, r4)` order.
    fn of(rows: LabelRows<'a>, span: (usize, usize)) -> Self {
        let keys = (span.0..span.1)
            .map(|row| {
                (
                    [
                        Rc::clone(rows.right1(row)),
                        Rc::clone(rows.right2(row)),
                        Rc::clone(rows.right3(row)),
                        Rc::clone(rows.right4(row)),
                    ],
                    row,
                )
            })
            .collect();
        Self { rows, keys }
    }

    /// One row by its four right labels, `group_rows[(r1, r2, r3, r4)]`.
    fn at(&self, key: [&Rc<str>; 4]) -> Option<usize> {
        self.keys
            .binary_search_by(|(held, _)| compare(held, &key))
            .ok()
            .map(|seat| self.keys[seat].1)
    }

    /// The half-open range of rows whose first labels are `prefix`, which the sort leaves contiguous.
    fn under(&self, prefix: &[&Rc<str>]) -> (usize, usize) {
        let start = self
            .keys
            .partition_point(|(held, _)| compare(held, prefix) == Ordering::Less);
        let end = start
            + self.keys[start..]
                .partition_point(|(held, _)| compare(held, prefix) == Ordering::Equal);
        (start, end)
    }

    /// The distinct labels one slot takes over a prefix range, sorted.
    fn slot_values(&self, span: (usize, usize), slot: usize) -> Vec<Rc<str>> {
        let mut values: Vec<Rc<str>> = self.keys[span.0..span.1]
            .iter()
            .map(|(held, _)| Rc::clone(&held[slot]))
            .collect();
        values.sort();
        values.dedup();
        values
    }
}

/// One input's ordered rules, how many of them are identity guards, and the lefts a first-match-wins replay has to cover to cover them all. `rows` is the input's own slice of the label-grain stream; `never_locked` is `not settle.is_entry_bearing(spec, rune)`, the one verdict the fold reads off the spec.
pub fn rules_for_input(
    input_glyph: &Rc<str>,
    rows: &LabelRows<'_>,
    never_locked: bool,
) -> Result<RuleFold, String> {
    let boundary_class: Vec<Rc<str>> = BOUNDARY_LOOKAHEAD_CLASS
        .iter()
        .map(|label| Rc::from(*label))
        .collect();

    // The rows of one input are key-sorted, so each left's rows are one contiguous run and the runs arrive in the `sorted(rows_by_left)` order.
    let mut spans: Vec<(Rc<str>, (usize, usize))> = Vec::new();
    let mut start = 0;
    while start < rows.len() {
        let mut end = start + 1;
        while end < rows.len() && rows.left(end) == rows.left(start) {
            end += 1;
        }
        spans.push((Rc::clone(rows.left(start)), (start, end)));
        start = end;
    }
    let by_left: HashMap<Rc<str>, (usize, usize)> = spans.iter().cloned().collect();
    let lefts: Vec<Rc<str>> = spans.iter().map(|(left, _)| Rc::clone(left)).collect();

    let mut left_signatures: HashMap<Rc<str>, Signature<5>> = HashMap::new();
    for (left, span) in &spans {
        left_signatures.insert(
            Rc::clone(left),
            canonical(
                (span.0..span.1)
                    .map(|row| {
                        [
                            Rc::clone(rows.right1(row)),
                            Rc::clone(rows.right2(row)),
                            Rc::clone(rows.right3(row)),
                            Rc::clone(rows.right4(row)),
                            Rc::clone(rows.outcome(row)),
                        ]
                    })
                    .collect(),
            ),
        );
    }
    let left_blocks = signature_blocks(&lefts, |left| {
        left_signatures
            .remove(left)
            .expect("every left of this input has a signature")
    });
    let (default_blocks, committed_blocks): (Blocks, Blocks) = left_blocks
        .into_iter()
        .partition(|block| block.iter().any(|left| boundaryish(left)));
    if default_blocks.len() > 1 {
        return Err(format!(
            "{input_glyph}: boundary left contexts split across outcome blocks: {}",
            block_list(&default_blocks)
        ));
    }

    let mut state = Emission {
        input_glyph: Rc::clone(input_glyph),
        boundary_class,
        identity_guards: 0,
    };
    let mut committed_rules: Vec<Rule> = Vec::new();
    let mut default_rules: Vec<Rule> = Vec::new();
    for block in &committed_blocks {
        let group = GroupRows::of(*rows, by_left[&block[0]]);
        state.emit_group(&group, Some(block), &mut committed_rules)?;
    }
    for block in &default_blocks {
        let group = GroupRows::of(*rows, by_left[&block[0]]);
        state.emit_group(&group, None, &mut default_rules)?;
    }

    // ZWNJ coverage at the backtrack slot: an input the chokepoint never locks can sit immediately after ZWNJ as its raw self, and a backtrack-classed rule could match across the skipped ZWNJ. Defense: replicate the boundary-left behavior with uni200C explicit in the backtrack slot, ordered ahead of every backtrack-classed rule, then an identity catch-all. Lockable inputs need none of this: after ZWNJ they are locked twins whose rows enumerate under the twin's own input label.
    let mut guards: Vec<Rule> = Vec::new();
    if never_locked
        && committed_rules.iter().any(|rule| {
            rule.backtrack
                .as_ref()
                .is_some_and(|block| !block.is_empty())
        })
    {
        let zwnj: Vec<Rc<str>> = vec![Rc::from("uni200C")];
        for rule in &default_rules {
            let mut provenance = rule.provenance.clone();
            provenance.push("ZWNJ backtrack-slot coverage row".to_owned());
            guards.push(Rule {
                input_glyph: Rc::clone(input_glyph),
                backtrack: Some(zwnj.clone()),
                look1: rule.look1.clone(),
                look2: rule.look2.clone(),
                look3: rule.look3.clone(),
                look4: rule.look4.clone(),
                outcome: Rc::clone(&rule.outcome),
                provenance,
                joint: rule.joint,
            });
        }
        state.identity_guards += 1;
        guards.push(Rule {
            input_glyph: Rc::clone(input_glyph),
            backtrack: Some(zwnj),
            look1: None,
            look2: None,
            look3: None,
            look4: None,
            outcome: Rc::clone(input_glyph),
            provenance: vec!["ZWNJ backtrack-slot identity guard".to_owned()],
            joint: false,
        });
    }

    let mut replay_lefts: HashSet<Rc<str>> = committed_blocks
        .iter()
        .map(|block| Rc::clone(&block[0]))
        .collect();
    for block in &default_blocks {
        replay_lefts.extend(block.iter().cloned());
    }

    guards.extend(committed_rules);
    guards.extend(default_rules);
    Ok(RuleFold {
        rules: guards,
        identity_guards: state.identity_guards,
        replay_lefts,
    })
}

/// The per-input state `emit_group` mutates: the input every rule names, the boundary class every boundary rule carries, and the running identity-guard count.
struct Emission {
    input_glyph: Rc<str>,
    boundary_class: Vec<Rc<str>>,
    identity_guards: i64,
}

impl Emission {
    fn rule(
        &self,
        backtrack: Option<&Vec<Rc<str>>>,
        looks: [Option<&Vec<Rc<str>>>; 4],
        outcome: &Rc<str>,
        rows: &LabelRows<'_>,
        sample: usize,
        joint: bool,
    ) -> Rule {
        Rule {
            input_glyph: Rc::clone(&self.input_glyph),
            backtrack: backtrack.cloned(),
            look1: looks[0].cloned(),
            look2: looks[1].cloned(),
            look3: looks[2].cloned(),
            look4: looks[3].cloned(),
            outcome: Rc::clone(outcome),
            provenance: rows.provenance(sample).to_vec(),
            joint,
        }
    }

    /// One left block's rules, appended in emission order. `backtrack` is the block itself for a committed block and `None` for the default one.
    fn emit_group(
        &mut self,
        group: &GroupRows<'_>,
        backtrack: Option<&Vec<Rc<str>>>,
        out: &mut Vec<Rule>,
    ) -> Result<(), String> {
        let rows = &group.rows;
        let whole = (0usize, group.keys.len());
        let group_r1s = group.slot_values(whole, 0);

        let mut r1_signatures: HashMap<Rc<str>, Signature<4>> = HashMap::new();
        for (key, row) in &group.keys {
            r1_signatures.entry(Rc::clone(&key[0])).or_default().push([
                Rc::clone(&key[1]),
                Rc::clone(&key[2]),
                Rc::clone(&key[3]),
                Rc::clone(rows.outcome(*row)),
            ]);
        }
        let r1_blocks = signature_blocks(&group_r1s, |r1| {
            canonical(r1_signatures.remove(r1).unwrap_or_default())
        });

        let boundary_block = r1_blocks
            .iter()
            .find(|block| block.iter().any(|label| boundaryish(label)));
        let mut fallback_outcome = Rc::clone(&self.input_glyph);
        let mut boundary_rules: Vec<Rule> = Vec::new();
        let mut fallback_rules: Vec<Rule> = Vec::new();
        if let Some(block) = boundary_block {
            let na: Rc<str> = Rc::from(NA_LABEL);
            let sampled: Vec<usize> = block
                .iter()
                .filter_map(|r1| group.at([r1, &na, &na, &na]))
                .collect();
            let mut outcomes: Vec<&str> = sampled.iter().map(|row| &**rows.outcome(*row)).collect();
            outcomes.sort_unstable();
            outcomes.dedup();
            if outcomes.len() != 1 {
                return Err(format!(
                    "{}: boundary lookaheads disagree: {}",
                    self.input_glyph,
                    python_set(&outcomes)
                ));
            }
            let sample = sampled[0];
            fallback_outcome = Rc::clone(rows.outcome(sample));
            if fallback_outcome != self.input_glyph {
                let joint = rows.joint(sample);
                boundary_rules.push(self.rule(
                    backtrack,
                    [Some(&self.boundary_class), None, None, None],
                    &fallback_outcome,
                    rows,
                    sample,
                    joint,
                ));
                fallback_rules.push(self.rule(
                    backtrack,
                    [None, None, None, None],
                    &fallback_outcome,
                    rows,
                    sample,
                    joint,
                ));
            }
        }

        let mut letter_rules: Vec<Rule> = Vec::new();
        for r1_block in &r1_blocks {
            if Some(r1_block) == boundary_block {
                continue;
            }
            let letters = letters_of(r1_block);
            if letters.len() != r1_block.len() {
                return Err(format!(
                    "{}: mixed letter/boundary lookahead block {}",
                    self.input_glyph,
                    label_tuple(r1_block)
                ));
            }
            let r1_span = group.under(&[&r1_block[0]]);
            let block_r2s = group.slot_values(r1_span, 1);
            let r1_members: HashSet<&Rc<str>> = r1_block.iter().collect();

            let mut r2_signatures: HashMap<Rc<str>, Signature<4>> = HashMap::new();
            let mut distinct_outcomes: Vec<&str> = Vec::new();
            let mut block_joint = false;
            for (key, row) in &group.keys {
                if !r1_members.contains(&key[0]) {
                    continue;
                }
                r2_signatures.entry(Rc::clone(&key[1])).or_default().push([
                    Rc::clone(&key[0]),
                    Rc::clone(&key[2]),
                    Rc::clone(&key[3]),
                    Rc::clone(rows.outcome(*row)),
                ]);
                distinct_outcomes.push(rows.outcome(*row));
                block_joint |= rows.joint(*row);
            }
            distinct_outcomes.sort_unstable();
            distinct_outcomes.dedup();
            let r2_blocks = signature_blocks(&block_r2s, |r2| {
                canonical(r2_signatures.remove(r2).unwrap_or_default())
            });

            if distinct_outcomes.len() == 1 {
                let sample = group.under(&[&r1_block[0], &block_r2s[0]]).0;
                let sample = group.keys[sample].1;
                let out_label = Rc::clone(rows.outcome(sample));
                if out_label == fallback_outcome {
                    continue;
                }
                if out_label == self.input_glyph {
                    if fallback_outcome != self.input_glyph {
                        self.identity_guards += 1;
                        letter_rules.push(self.rule(
                            backtrack,
                            [Some(&letters), None, None, None],
                            &out_label,
                            rows,
                            sample,
                            block_joint,
                        ));
                    }
                    continue;
                }
                letter_rules.push(self.rule(
                    backtrack,
                    [Some(&letters), None, None, None],
                    &out_label,
                    rows,
                    sample,
                    block_joint,
                ));
                continue;
            }

            // Outcome depends on a later lookahead slot; see the module docstring for the ordering discipline the split replays at each depth.
            let mut slot_fallback: Option<Rule> = None;
            let mut boundary_slot_rule: Option<Rule> = None;
            let mut deep_rules: Vec<Rule> = Vec::new();
            let mut two_slot_rules: Vec<Rule> = Vec::new();
            for r2_block in &r2_blocks {
                let r2_letters = letters_of(r2_block);
                let r2_span = group.under(&[&r1_block[0], &r2_block[0]]);
                let block_r3s = group.slot_values(r2_span, 2);
                let mut block_outcomes: Vec<&str> = group.keys[r2_span.0..r2_span.1]
                    .iter()
                    .map(|(_, row)| &**rows.outcome(*row))
                    .collect();
                block_outcomes.sort_unstable();
                block_outcomes.dedup();
                if block_outcomes.len() == 1 {
                    let sample =
                        group.keys[group.under(&[&r1_block[0], &r2_block[0], &block_r3s[0]]).0].1;
                    let out_label = Rc::clone(rows.outcome(sample));
                    if r2_block.iter().any(|label| boundaryish(label)) {
                        if r2_letters.len() + count_boundaryish(r2_block) != r2_block.len() {
                            return Err(format!(
                                "{}: unexpected labels in r2 block {}",
                                self.input_glyph,
                                label_tuple(r2_block)
                            ));
                        }
                        if out_label != self.input_glyph {
                            boundary_slot_rule = Some(self.rule(
                                backtrack,
                                [Some(&letters), Some(&self.boundary_class), None, None],
                                &out_label,
                                rows,
                                sample,
                                block_joint,
                            ));
                            slot_fallback = Some(self.rule(
                                backtrack,
                                [Some(&letters), None, None, None],
                                &out_label,
                                rows,
                                sample,
                                block_joint,
                            ));
                        }
                        continue;
                    }
                    two_slot_rules.push(self.rule(
                        backtrack,
                        [Some(&letters), Some(&r2_letters), None, None],
                        &out_label,
                        rows,
                        sample,
                        block_joint,
                    ));
                    continue;
                }
                if r2_block.iter().any(|label| boundaryish(label)) {
                    return Err(format!(
                        "{}: boundary second-slot block {} splits by the third slot",
                        self.input_glyph,
                        label_tuple(r2_block)
                    ));
                }
                let r2_members: HashSet<&Rc<str>> = r2_block.iter().collect();
                let mut r3_signatures: HashMap<Rc<str>, Signature<4>> = HashMap::new();
                for (key, row) in &group.keys {
                    if !r1_members.contains(&key[0]) || !r2_members.contains(&key[1]) {
                        continue;
                    }
                    r3_signatures.entry(Rc::clone(&key[2])).or_default().push([
                        Rc::clone(&key[0]),
                        Rc::clone(&key[1]),
                        Rc::clone(&key[3]),
                        Rc::clone(rows.outcome(*row)),
                    ]);
                }
                let r3_blocks = signature_blocks(&block_r3s, |r3| {
                    canonical(r3_signatures.remove(r3).unwrap_or_default())
                });
                let mut slot3_fallback: Option<Rule> = None;
                let mut boundary_slot3_rule: Option<Rule> = None;
                let mut three_slot_rules: Vec<Rule> = Vec::new();
                for r3_block in &r3_blocks {
                    let r3_letters = letters_of(r3_block);
                    let r3_span = group.under(&[&r1_block[0], &r2_block[0], &r3_block[0]]);
                    let block_r4s = group.slot_values(r3_span, 3);
                    let mut block4_outcomes: Vec<&str> = block_r4s
                        .iter()
                        .map(|r4| {
                            let seat = group
                                .at([&r1_block[0], &r2_block[0], &r3_block[0], r4])
                                .expect("a fourth label taken from this prefix seats a row");
                            &**rows.outcome(seat)
                        })
                        .collect();
                    block4_outcomes.sort_unstable();
                    block4_outcomes.dedup();
                    if block4_outcomes.len() == 1 {
                        let sample = group
                            .at([&r1_block[0], &r2_block[0], &r3_block[0], &block_r4s[0]])
                            .expect("the first fourth label of this prefix seats a row");
                        let out_label = Rc::clone(rows.outcome(sample));
                        if r3_block.iter().any(|label| boundaryish(label)) {
                            if r3_letters.len() + count_boundaryish(r3_block) != r3_block.len() {
                                return Err(format!(
                                    "{}: unexpected labels in r3 block {}",
                                    self.input_glyph,
                                    label_tuple(r3_block)
                                ));
                            }
                            if out_label != self.input_glyph {
                                boundary_slot3_rule = Some(self.rule(
                                    backtrack,
                                    [
                                        Some(&letters),
                                        Some(&r2_letters),
                                        Some(&self.boundary_class),
                                        None,
                                    ],
                                    &out_label,
                                    rows,
                                    sample,
                                    block_joint,
                                ));
                                slot3_fallback = Some(self.rule(
                                    backtrack,
                                    [Some(&letters), Some(&r2_letters), None, None],
                                    &out_label,
                                    rows,
                                    sample,
                                    block_joint,
                                ));
                            }
                            continue;
                        }
                        three_slot_rules.push(self.rule(
                            backtrack,
                            [Some(&letters), Some(&r2_letters), Some(&r3_letters), None],
                            &out_label,
                            rows,
                            sample,
                            block_joint,
                        ));
                        continue;
                    }
                    if r3_block.iter().any(|label| boundaryish(label)) {
                        return Err(format!(
                            "{}: boundary third-slot block {} splits by the fourth slot",
                            self.input_glyph,
                            label_tuple(r3_block)
                        ));
                    }
                    let r3_members: HashSet<&Rc<str>> = r3_block.iter().collect();
                    let mut r4_signatures: HashMap<Rc<str>, Signature<4>> = HashMap::new();
                    for (key, row) in &group.keys {
                        if !r1_members.contains(&key[0])
                            || !r2_members.contains(&key[1])
                            || !r3_members.contains(&key[2])
                        {
                            continue;
                        }
                        r4_signatures.entry(Rc::clone(&key[3])).or_default().push([
                            Rc::clone(&key[0]),
                            Rc::clone(&key[1]),
                            Rc::clone(&key[2]),
                            Rc::clone(rows.outcome(*row)),
                        ]);
                    }
                    let r4_blocks = signature_blocks(&block_r4s, |r4| {
                        canonical(r4_signatures.remove(r4).unwrap_or_default())
                    });
                    let mut slot4_fallback: Option<Rule> = None;
                    let mut boundary_slot4_rule: Option<Rule> = None;
                    let mut four_slot_rules: Vec<Rule> = Vec::new();
                    for r4_block in &r4_blocks {
                        let sample = group
                            .at([&r1_block[0], &r2_block[0], &r3_block[0], &r4_block[0]])
                            .expect("a fourth block's first label seats a row of this prefix");
                        let out_label = Rc::clone(rows.outcome(sample));
                        let r4_letters = letters_of(r4_block);
                        if r4_block.iter().any(|label| boundaryish(label)) {
                            if r4_letters.len() + count_boundaryish(r4_block) != r4_block.len() {
                                return Err(format!(
                                    "{}: unexpected labels in r4 block {}",
                                    self.input_glyph,
                                    label_tuple(r4_block)
                                ));
                            }
                            if out_label != self.input_glyph {
                                boundary_slot4_rule = Some(self.rule(
                                    backtrack,
                                    [
                                        Some(&letters),
                                        Some(&r2_letters),
                                        Some(&r3_letters),
                                        Some(&self.boundary_class),
                                    ],
                                    &out_label,
                                    rows,
                                    sample,
                                    block_joint,
                                ));
                                slot4_fallback = Some(self.rule(
                                    backtrack,
                                    [Some(&letters), Some(&r2_letters), Some(&r3_letters), None],
                                    &out_label,
                                    rows,
                                    sample,
                                    block_joint,
                                ));
                            }
                            continue;
                        }
                        four_slot_rules.push(self.rule(
                            backtrack,
                            [
                                Some(&letters),
                                Some(&r2_letters),
                                Some(&r3_letters),
                                Some(&r4_letters),
                            ],
                            &out_label,
                            rows,
                            sample,
                            block_joint,
                        ));
                    }
                    deep_rules.extend(boundary_slot4_rule);
                    self.screen(four_slot_rules, slot4_fallback.as_ref(), &mut deep_rules);
                    deep_rules.extend(slot4_fallback);
                }
                deep_rules.extend(boundary_slot3_rule);
                self.screen(three_slot_rules, slot3_fallback.as_ref(), &mut deep_rules);
                deep_rules.extend(slot3_fallback);
            }
            letter_rules.extend(boundary_slot_rule);
            letter_rules.append(&mut deep_rules);
            self.screen(two_slot_rules, slot_fallback.as_ref(), &mut letter_rules);
            letter_rules.extend(slot_fallback);
        }

        out.extend(boundary_rules);
        out.extend(letter_rules);
        out.extend(fallback_rules);
        Ok(())
    }

    /// The dedup one bundle's rules pass through before they are emitted: an identity outcome is dropped unless a slot-dropped fallback follows it, in which case it is the identity guard that keeps the fallback from swallowing the window; and a rule the fallback would say the same thing about is redundant. A bundle with no fallback screens nothing away but its identities.
    fn screen(&mut self, rules: Vec<Rule>, fallback: Option<&Rule>, out: &mut Vec<Rule>) {
        for rule in rules {
            if rule.outcome == self.input_glyph {
                if fallback.is_none() {
                    continue;
                }
                self.identity_guards += 1;
            } else if fallback.is_some_and(|fallback| fallback.outcome == rule.outcome) {
                continue;
            }
            out.push(rule);
        }
    }
}

/// The non-boundary members of one block, in the block's own order.
fn letters_of(block: &[Rc<str>]) -> Vec<Rc<str>> {
    block
        .iter()
        .filter(|label| !boundaryish(label))
        .cloned()
        .collect()
}

fn count_boundaryish(block: &[Rc<str>]) -> usize {
    block.iter().filter(|label| boundaryish(label)).count()
}

/// One block as Python's tuple repr, which is what a raise message names it by.
fn label_tuple(block: &[Rc<str>]) -> String {
    let members: Vec<String> = block.iter().map(|label| python_repr(label)).collect();
    python_tuple(&members)
}

/// A list of blocks as Python's list-of-tuples repr.
fn block_list(blocks: &Blocks) -> String {
    let listed: Vec<String> = blocks.iter().map(|block| label_tuple(block)).collect();
    format!("[{}]", listed.join(", "))
}

/// A set of labels as Python's set repr, sorted so a raise reads the same twice. An empty one spells `set()`, which is what Python's repr says; `{}` is its empty dict.
fn python_set(values: &[&str]) -> String {
    if values.is_empty() {
        return "set()".to_owned();
    }
    let quoted: Vec<String> = values.iter().map(|value| python_repr(value)).collect();
    format!("{{{}}}", quoted.join(", "))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// What no replay over every row of every configuration could catch, since it is a property of the grouping and not of any input: [`signature_blocks`] groups its values in a map keyed by signature, so the blocks partition the values and each one is exactly one signature's preimage. Stated here over hand-made signatures, at no cost to a build.
    #[test]
    fn the_blocks_are_a_disjoint_cover_grouped_by_signature() {
        let signatures: Vec<(&str, u32)> =
            vec![("a", 1), ("b", 1), ("c", 2), ("d", 0), ("e", 2), ("f", 3)];
        let values: Vec<Rc<str>> = signatures.iter().map(|(name, _)| Rc::from(*name)).collect();
        let blocks = signature_blocks(&values, |value| {
            signatures
                .iter()
                .find(|(name, _)| *name == &**value)
                .expect("every value has a signature")
                .1
        });
        let mut sorted = blocks.clone();
        sorted.sort();
        assert_eq!(blocks, sorted, "the blocks come back sorted");
        let distinct: HashSet<u32> = signatures.iter().map(|(_, signature)| *signature).collect();
        assert_eq!(blocks.len(), distinct.len());
        let mut members: Vec<&str> = Vec::new();
        for block in &blocks {
            let mut own = block.clone();
            own.sort();
            assert_eq!(block, &own, "a block's members come back sorted");
            let held: HashSet<u32> = block
                .iter()
                .map(|value| {
                    signatures
                        .iter()
                        .find(|(name, _)| *name == &**value)
                        .expect("every member has a signature")
                        .1
                })
                .collect();
            assert_eq!(held.len(), 1, "a block holds one signature's preimage");
            members.extend(block.iter().map(|value| &**value));
        }
        members.sort_unstable();
        let mut all: Vec<&str> = signatures.iter().map(|(name, _)| *name).collect();
        all.sort_unstable();
        assert_eq!(members, all, "every value lands in exactly one block");
    }
}
