//! A lossless label-grain view of a class-grain product without a resident `right3` by `right4` row for every concrete pair. The view still has the full conceptual expansion: callers visit every concrete row, and certificate prefixes still allocate their distance and parent arrays per concrete row, so this changes the fold's resident row representation rather than bounding its running time or all of its memory.
//!
//! Within one near-prefix run, concrete `right3` labels with the same active source rows share one leaf pattern. A pattern expands only `right4`, as sorted `(right4, source seat)` leaves; stable sorting preserves source-seat order where labels tie. Concrete `right3` labels stay in lexical order with cumulative pattern lengths, so an ordinal resolves to exactly the seat and two labels [`crate::fold::expand`] materializes at that ordinal.

use std::collections::BTreeMap;
use std::rc::Rc;

use crate::hash::{HashMap, HashSet};
use crate::stream::{FixpointProduct, Label, TransitionRow};

struct Leaf {
    right4: Rc<str>,
    seat: u32,
}

struct Pattern {
    leaves: Vec<Leaf>,
}

struct Third {
    right3: Rc<str>,
    pattern: Rc<Pattern>,
    end: usize,
}

struct NearRun {
    thirds: Vec<Third>,
    end: usize,
}

/// The exact rows of a label-grain expansion, indexed virtually through shared one-slot leaf patterns.
pub struct VirtualRows {
    runs: Vec<NearRun>,
    len: usize,
}

impl VirtualRows {
    pub fn new(product: &FixpointProduct) -> Self {
        let members = deep_members(product);
        let mut runs: Vec<NearRun> = Vec::new();
        let mut total = 0usize;
        let mut start = 0usize;
        while start < product.transitions.len() {
            let mut end = start + 1;
            while end < product.transitions.len()
                && near_slots(&product.transitions[end]) == near_slots(&product.transitions[start])
            {
                end += 1;
            }
            let mut incidence: BTreeMap<Rc<str>, Vec<u32>> = BTreeMap::new();
            for (seat, row) in product.transitions.iter().enumerate().take(end).skip(start) {
                for right3 in concrete_members(product, &members, row.right3) {
                    incidence
                        .entry(Rc::clone(right3))
                        .or_default()
                        .push(seat as u32);
                }
            }
            let mut patterns: HashMap<Vec<u32>, Rc<Pattern>> = HashMap::default();
            let mut thirds: Vec<Third> = Vec::with_capacity(incidence.len());
            let mut local = 0usize;
            for (right3, seats) in incidence {
                let pattern = match patterns.get(&seats) {
                    Some(pattern) => Rc::clone(pattern),
                    None => {
                        let mut leaves: Vec<Leaf> = Vec::new();
                        for &seat in &seats {
                            let row = &product.transitions[seat as usize];
                            for right4 in concrete_members(product, &members, row.right4) {
                                leaves.push(Leaf {
                                    right4: Rc::clone(right4),
                                    seat,
                                });
                            }
                        }
                        leaves.sort_by(|left, right| left.right4.cmp(&right.right4));
                        let pattern = Rc::new(Pattern { leaves });
                        patterns.insert(seats, Rc::clone(&pattern));
                        pattern
                    }
                };
                local += pattern.leaves.len();
                thirds.push(Third {
                    right3,
                    pattern,
                    end: local,
                });
            }
            total += local;
            runs.push(NearRun { thirds, end: total });
            start = end;
        }
        Self { runs, len: total }
    }

    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    pub fn seat(&self, row: usize) -> u32 {
        self.resolve(row).2.seat
    }

    pub fn right3(&self, row: usize) -> &Rc<str> {
        &self.resolve(row).1.right3
    }

    pub fn right4(&self, row: usize) -> &Rc<str> {
        &self.resolve(row).2.right4
    }

    pub(crate) fn parts(&self, row: usize) -> (u32, &Rc<str>, &Rc<str>) {
        let (_, third, leaf) = self.resolve(row);
        (leaf.seat, &third.right3, &leaf.right4)
    }

    fn resolve(&self, row: usize) -> (&NearRun, &Third, &Leaf) {
        assert!(row < self.len, "a virtual row ordinal is in range");
        let run_seat = self.runs.partition_point(|run| run.end <= row);
        let run = &self.runs[run_seat];
        let run_start = run_seat
            .checked_sub(1)
            .map_or(0, |previous| self.runs[previous].end);
        let within_run = row - run_start;
        let third_seat = run.thirds.partition_point(|third| third.end <= within_run);
        let third = &run.thirds[third_seat];
        let third_start = third_seat
            .checked_sub(1)
            .map_or(0, |previous| run.thirds[previous].end);
        let leaf = &third.pattern.leaves[within_run - third_start];
        (run, third, leaf)
    }
}

fn near_slots(row: &TransitionRow) -> [Label; 4] {
    [row.input_glyph, row.left, row.right1, row.right2]
}

fn deep_members(product: &FixpointProduct) -> HashMap<&str, Vec<Rc<str>>> {
    let mut pool: HashSet<Rc<str>> = HashSet::default();
    let mut members: HashMap<&str, Vec<Rc<str>>> = HashMap::default();
    for (token, names) in &product.deep_classes {
        let interned = names
            .iter()
            .map(|name| match pool.get(name.as_str()) {
                Some(found) => Rc::clone(found),
                None => {
                    let shared: Rc<str> = Rc::from(name.as_str());
                    pool.insert(Rc::clone(&shared));
                    shared
                }
            })
            .collect();
        members.insert(token.as_str(), interned);
    }
    members
}

fn concrete_members<'a>(
    product: &'a FixpointProduct,
    members: &'a HashMap<&str, Vec<Rc<str>>>,
    label: Label,
) -> &'a [Rc<str>] {
    let own = std::slice::from_ref(product.labels.text(label));
    members
        .get(&**product.labels.text(label))
        .map_or(own, Vec::as_slice)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixpoint::{EnumerationModes, enumerate_transitions};
    use crate::fold::{LabelRows, expand};
    use crate::index::fixtures;

    const SHIPPING: EnumerationModes = EnumerationModes {
        simulated_prospect: true,
        vote_slots: true,
        deep_classes: true,
    };

    #[test]
    fn the_virtual_view_is_the_materialized_expansion_row_for_row() {
        let index = fixtures::mini();
        let product = enumerate_transitions(&index, &[], SHIPPING)
            .expect("the fixture's fixpoint closes and settles");
        let expanded = expand(&product);
        let virtual_rows = VirtualRows::new(&product);
        let materialized = LabelRows::new(&product, &expanded);
        let virtualized = LabelRows::virtual_rows(&product, &virtual_rows);
        assert_eq!(virtualized.len(), materialized.len());
        for row in 0..materialized.len() {
            assert_eq!(virtualized.key(row), materialized.key(row));
            assert_eq!(virtualized.base(row), materialized.base(row));
            assert_eq!(virtualized.outcome(row), materialized.outcome(row));
            assert_eq!(virtualized.provenance(row), materialized.provenance(row));
            assert_eq!(virtualized.joint(row), materialized.joint(row));
        }
    }
}
