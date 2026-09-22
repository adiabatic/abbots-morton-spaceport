//! An experimental quotient of the fold's two deep label axes. Labels share an atom only when every class row of one input contains either both or neither, and the successor sets read by the prospect-joint pass cannot distinguish them. One row per atom pair is therefore enough for [`crate::rulefold`]; emitted deep classes are expanded back to the atom members before they leave the experiment. The prototype requires the fixpoint's ordinary partition invariant: at one near key, two source rows cannot claim the same concrete deep pair. Overlap across different near contexts is refined exactly; a hand-built product with duplicate same-context rows is refused rather than assigned the production expansion's stable tie order.

use std::rc::Rc;

use crate::fold::{FoldRow, LabelRows, NA_LABEL, Rule, boundaryish};
use crate::hash::{HashMap, HashSet};
use crate::stream::{FixpointProduct, Label, TransitionRow, key_repr};

/// Counts at the two grains, for the experiment's timing and memory report.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct CompressedStats {
    pub class_rows: usize,
    pub concrete_rows: usize,
    pub atom_rows: usize,
    pub slot3_atoms: usize,
    pub slot4_atoms: usize,
}

/// The atom-grain rows handed to the existing rule fold, plus the maps that turn its deep-slot classes back into concrete labels.
pub struct CompressedRows {
    fold_rows: Vec<FoldRow>,
    inputs: HashMap<Rc<str>, InputAtoms>,
    class_joint: Vec<bool>,
    stats: CompressedStats,
}

struct InputAtoms {
    slot3: AxisAtoms,
    slot4: AxisAtoms,
}

#[derive(Default)]
struct AxisAtoms {
    atoms: Vec<Atom>,
    by_representative: HashMap<Rc<str>, Vec<Rc<str>>>,
}

struct Atom {
    representative: Rc<str>,
    members: Vec<Rc<str>>,
}

impl CompressedRows {
    /// Build the exact per-input atom quotient. Source classes are cuts on their own axis. For each successor lookup and prospect, the divergent successors are grouped by their third-slot member set: the union of their concrete second slots cuts slot 3 and that member set cuts slot 4. Each group is one rectangle of the joint relation, so the two axes' incidence signatures make the relation constant over every atom pair without forcing every successor's second slot to a singleton. Boundary labels are singleton atoms so the rule fold continues to recognize its boundary arms directly.
    pub fn new(product: &FixpointProduct) -> Result<Self, String> {
        let classes = deep_classes(product);
        let successors = successor_index(product);

        let mut fold_rows: Vec<FoldRow> = Vec::new();
        let mut inputs: HashMap<Rc<str>, InputAtoms> = HashMap::default();
        let mut class_joint: Vec<bool> = product.transitions.iter().map(|row| row.joint).collect();
        let mut stats = CompressedStats {
            class_rows: product.transitions.len(),
            ..CompressedStats::default()
        };

        for (input, start, end) in input_runs(product) {
            let mut cuts3: Vec<Vec<Rc<str>>> = Vec::new();
            let mut cuts4: Vec<Vec<Rc<str>>> = Vec::new();
            let mut universe3: HashSet<Rc<str>> = HashSet::default();
            let mut universe4: HashSet<Rc<str>> = HashSet::default();
            let mut source3: HashSet<Label> = HashSet::default();
            let mut source4: HashSet<Label> = HashSet::default();
            let mut successor_queries: HashSet<((Label, Label, Label), i8)> = HashSet::default();
            for seat in start..end {
                let row = &product.transitions[seat];
                source3.insert(row.right3);
                source4.insert(row.right4);
                if !row.joint
                    && !boundaryish(product.labels.text(row.right1))
                    && !boundaryish(product.labels.text(row.right2))
                {
                    successor_queries.insert((successor_key(product, row), row.prospect));
                }
            }
            for &label in &source3 {
                let members3 = token_members(product, &classes, label);
                universe3.extend(members3.iter().cloned());
                add_cut(&mut cuts3, members3);
            }
            for &label in &source4 {
                let members4 = token_members(product, &classes, label);
                universe4.extend(members4.iter().cloned());
                add_cut(&mut cuts4, members4);
            }
            for (key, prospect) in successor_queries {
                let mut rectangles: HashMap<Vec<Rc<str>>, HashSet<Label>> = HashMap::default();
                for followed in successors.get(&key).map_or(&[][..], Vec::as_slice) {
                    if i8::from(followed.seam) == prospect {
                        continue;
                    }
                    let mut members4 = token_members(product, &classes, followed.right3).to_vec();
                    members4.sort();
                    members4.dedup();
                    rectangles
                        .entry(members4)
                        .or_default()
                        .insert(followed.right2);
                }
                for (members4, seconds) in rectangles {
                    let members3: Vec<Rc<str>> = seconds
                        .into_iter()
                        .map(|label| Rc::clone(product.labels.text(label)))
                        .collect();
                    add_cut(&mut cuts3, &members3);
                    add_cut(&mut cuts4, &members4);
                }
            }
            singleton_boundaries(&universe3, &mut cuts3);
            singleton_boundaries(&universe4, &mut cuts4);
            let atoms3 = AxisAtoms::from_cuts(universe3, &cuts3);
            let atoms4 = AxisAtoms::from_cuts(universe4, &cuts4);
            stats.slot3_atoms += atoms3.atoms.len();
            stats.slot4_atoms += atoms4.atoms.len();
            let mut held3: HashMap<Label, Vec<usize>> = HashMap::default();
            let mut held4: HashMap<Label, Vec<usize>> = HashMap::default();
            for label in source3 {
                held3.insert(
                    label,
                    atoms3.inside_indices(token_members(product, &classes, label))?,
                );
            }
            for label in source4 {
                held4.insert(
                    label,
                    atoms4.inside_indices(token_members(product, &classes, label))?,
                );
            }

            for (seat, class_joint) in class_joint.iter_mut().enumerate().take(end).skip(start) {
                let row = &product.transitions[seat];
                let members3 = token_members(product, &classes, row.right3);
                let members4 = token_members(product, &classes, row.right4);
                stats.concrete_rows += members3.len() * members4.len();
                for &atom3 in &held3[&row.right3] {
                    for &atom4 in &held4[&row.right4] {
                        let atom3 = &atoms3.atoms[atom3];
                        let atom4 = &atoms4.atoms[atom4];
                        let joint = atom_joint(product, &classes, &successors, row, atom3, atom4);
                        *class_joint |= joint;
                        fold_rows.push(FoldRow {
                            seat: seat as u32,
                            right3: Rc::clone(&atom3.representative),
                            right4: Rc::clone(&atom4.representative),
                            joint,
                        });
                    }
                }
            }
            inputs.insert(
                Rc::clone(product.labels.text(input)),
                InputAtoms {
                    slot3: atoms3,
                    slot4: atoms4,
                },
            );
        }

        // The quotient's representatives are real labels. Their ordinary lexical order is therefore the concrete stream's order after each atom has been contracted to its least member. Stable sorting keeps the source-row order for a malformed overlap so the diagnostic below can name it deterministically.
        fold_rows.sort_by(|left, right| {
            let left_base = &product.transitions[left.seat as usize];
            let right_base = &product.transitions[right.seat as usize];
            (
                product.labels.text(left_base.input_glyph),
                product.labels.text(left_base.left),
                product.labels.text(left_base.right1),
                product.labels.text(left_base.right2),
                &left.right3,
                &left.right4,
            )
                .cmp(&(
                    product.labels.text(right_base.input_glyph),
                    product.labels.text(right_base.left),
                    product.labels.text(right_base.right1),
                    product.labels.text(right_base.right2),
                    &right.right3,
                    &right.right4,
                ))
        });
        for pair in fold_rows.windows(2) {
            if atom_key(product, &pair[0]) == atom_key(product, &pair[1]) {
                return Err(format!(
                    "compressed fold: two source rows claim atom key {}",
                    key_repr(atom_key(product, &pair[0]))
                ));
            }
        }
        stats.atom_rows = fold_rows.len();
        Ok(Self {
            fold_rows,
            inputs,
            class_joint,
            stats,
        })
    }

    /// The quotient in the interface the production rule fold already consumes.
    pub fn rows<'a>(&'a self, product: &'a FixpointProduct) -> LabelRows<'a> {
        LabelRows::new(product, &self.fold_rows)
    }

    pub fn fold_rows(&self) -> &[FoldRow] {
        &self.fold_rows
    }

    /// Each class row's section 6.1 flag, ORed over its atom rectangles exactly as the production pass ORs over its concrete rows.
    pub fn class_joint(&self) -> &[bool] {
        &self.class_joint
    }

    pub fn stats(&self) -> CompressedStats {
        self.stats
    }

    /// Replace deep atom representatives with the concrete labels they stand for. The final sort is load-bearing: atoms ordered by their least members can interleave (`[a, z]`, `[b, c]`), while the settlement artifact orders every class member lexically.
    pub fn expand_rules(&self, input: &str, rules: &mut [Rule]) {
        let Some(atoms) = self.inputs.get(input) else {
            return;
        };
        for rule in rules.iter_mut().filter(|rule| &*rule.input_glyph == input) {
            expand_slot(&mut rule.look3, &atoms.slot3.by_representative);
            expand_slot(&mut rule.look4, &atoms.slot4.by_representative);
        }
    }
}

impl AxisAtoms {
    fn from_cuts(universe: HashSet<Rc<str>>, cuts: &[Vec<Rc<str>>]) -> Self {
        let mut groups: HashMap<Vec<bool>, Vec<Rc<str>>> = HashMap::default();
        for label in universe {
            let signature: Vec<bool> = cuts.iter().map(|cut| cut.contains(&label)).collect();
            groups.entry(signature).or_default().push(label);
        }
        let mut atoms: Vec<Atom> = groups
            .into_values()
            .map(|mut members| {
                members.sort();
                Atom {
                    representative: Rc::clone(&members[0]),
                    members,
                }
            })
            .collect();
        atoms.sort_by(|left, right| left.representative.cmp(&right.representative));
        let by_representative = atoms
            .iter()
            .map(|atom| (Rc::clone(&atom.representative), atom.members.to_vec()))
            .collect();
        Self {
            atoms,
            by_representative,
        }
    }

    fn inside_indices(&self, members: &[Rc<str>]) -> Result<Vec<usize>, String> {
        let mut held: Vec<usize> = Vec::new();
        for (seat, atom) in self.atoms.iter().enumerate() {
            let count = atom
                .members
                .iter()
                .filter(|member| members.iter().any(|held| held == *member))
                .count();
            if count == atom.members.len() {
                held.push(seat);
            } else if count != 0 {
                return Err(format!(
                    "compressed fold: source class splits atom {:?}",
                    atom.members
                ));
            }
        }
        Ok(held)
    }
}

fn deep_classes(product: &FixpointProduct) -> HashMap<&str, Vec<Rc<str>>> {
    product
        .deep_classes
        .iter()
        .map(|(token, members)| {
            (
                token.as_str(),
                members
                    .iter()
                    .map(|member| Rc::from(member.as_str()))
                    .collect(),
            )
        })
        .collect()
}

fn token_members<'a>(
    product: &'a FixpointProduct,
    classes: &'a HashMap<&str, Vec<Rc<str>>>,
    label: Label,
) -> &'a [Rc<str>] {
    let text = product.labels.text(label);
    classes
        .get(&**text)
        .map_or(std::slice::from_ref(text), Vec::as_slice)
}

fn add_cut(cuts: &mut Vec<Vec<Rc<str>>>, members: &[Rc<str>]) {
    let mut cut = members.to_vec();
    cut.sort();
    cut.dedup();
    if !cuts.contains(&cut) {
        cuts.push(cut);
    }
}

fn singleton_boundaries(universe: &HashSet<Rc<str>>, cuts: &mut Vec<Vec<Rc<str>>>) {
    for label in universe.iter().filter(|label| boundaryish(label)) {
        add_cut(cuts, std::slice::from_ref(label));
    }
}

fn input_runs(product: &FixpointProduct) -> Vec<(Label, usize, usize)> {
    let mut runs: Vec<(Label, usize, usize)> = Vec::new();
    let mut start = 0usize;
    while start < product.transitions.len() {
        let input = product.transitions[start].input_glyph;
        let mut end = start + 1;
        while end < product.transitions.len() && product.transitions[end].input_glyph == input {
            end += 1;
        }
        runs.push((input, start, end));
        start = end;
    }
    runs
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct Successor {
    right2: Label,
    right3: Label,
    seam: bool,
}

fn successor_index(product: &FixpointProduct) -> HashMap<(Label, Label, Label), Vec<Successor>> {
    let mut gathered: HashMap<(Label, Label, Label), HashSet<Successor>> = HashMap::default();
    for row in &product.transitions {
        gathered
            .entry((row.left, row.input_glyph, row.right1))
            .or_default()
            .insert(Successor {
                right2: row.right2,
                right3: row.right3,
                seam: product.settled(row).seam.is_some(),
            });
    }
    gathered
        .into_iter()
        .map(|(key, facts)| (key, facts.into_iter().collect()))
        .collect()
}

fn successor_key(product: &FixpointProduct, row: &TransitionRow) -> (Label, Label, Label) {
    (
        product.outcomes[row.settled.index()],
        row.right1,
        row.right2,
    )
}

fn matching_successors<'a>(
    product: &FixpointProduct,
    successors: &'a HashMap<(Label, Label, Label), Vec<Successor>>,
    row: &TransitionRow,
) -> &'a [Successor] {
    successors
        .get(&successor_key(product, row))
        .map_or(&[], Vec::as_slice)
}

fn atom_joint(
    product: &FixpointProduct,
    classes: &HashMap<&str, Vec<Rc<str>>>,
    successors: &HashMap<(Label, Label, Label), Vec<Successor>>,
    row: &TransitionRow,
    atom3: &Atom,
    atom4: &Atom,
) -> bool {
    if row.joint {
        return true;
    }
    if boundaryish(product.labels.text(row.right1)) || boundaryish(product.labels.text(row.right2))
    {
        return false;
    }
    let wildcard3 = atom3.members.iter().any(|member| &**member == NA_LABEL);
    let wildcard4 = atom4.members.iter().any(|member| &**member == NA_LABEL);
    for followed in matching_successors(product, successors, row) {
        if i8::from(followed.seam) == row.prospect {
            continue;
        }
        if !wildcard3
            && !atom3
                .members
                .iter()
                .any(|member| member == product.labels.text(followed.right2))
        {
            continue;
        }
        let followed3 = token_members(product, classes, followed.right3);
        if !wildcard4
            && !atom4
                .members
                .iter()
                .any(|member| followed3.contains(member))
        {
            continue;
        }
        return true;
    }
    false
}

fn atom_key<'a>(product: &'a FixpointProduct, row: &'a FoldRow) -> [&'a str; 6] {
    let base = &product.transitions[row.seat as usize];
    [
        product.labels.text(base.input_glyph),
        product.labels.text(base.left),
        product.labels.text(base.right1),
        product.labels.text(base.right2),
        &row.right3,
        &row.right4,
    ]
}

fn expand_slot(slot: &mut Option<Vec<Rc<str>>>, atoms: &HashMap<Rc<str>, Vec<Rc<str>>>) {
    let Some(held) = slot else {
        return;
    };
    if !held.iter().any(|member| {
        atoms
            .get(member)
            .is_some_and(|members| members.as_slice() != std::slice::from_ref(member))
    }) {
        return;
    }
    let mut concrete: Vec<Rc<str>> = held
        .iter()
        .flat_map(|member| {
            atoms
                .get(member)
                .map_or_else(|| vec![Rc::clone(member)], |members| members.clone())
        })
        .collect();
    concrete.sort();
    concrete.dedup();
    *held = concrete;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fold::expand;
    use crate::index::fixtures;
    use crate::rulefold::rules_for_input;
    use crate::stream::{LabelPool, TransitionRow};
    use crate::types::{CellId, NotesSeat, Settled, SettledSeat};

    fn labels(names: &[&str]) -> HashSet<Rc<str>> {
        names.iter().map(|name| Rc::from(*name)).collect()
    }

    fn cuts(groups: &[&[&str]]) -> (HashSet<Rc<str>>, Vec<Vec<Rc<str>>>) {
        let universe = groups
            .iter()
            .flat_map(|group| group.iter().copied())
            .map(Rc::from)
            .collect();
        let cuts = groups
            .iter()
            .map(|group| {
                let mut members: Vec<Rc<str>> = group.iter().map(|name| Rc::from(*name)).collect();
                members.sort();
                members.dedup();
                members
            })
            .collect();
        (universe, cuts)
    }

    struct RowSpec<'a> {
        input: &'a str,
        left: &'a str,
        right1: &'a str,
        right2: &'a str,
        right3: &'a str,
        right4: &'a str,
        outcome: &'a str,
        provenance: &'a str,
        prospect: i8,
        seam: bool,
    }

    fn product(rows: &[RowSpec<'_>], classes: &[(&str, &[&str])]) -> FixpointProduct {
        let index = fixtures::mini();
        let rune = index.sym_of("qsPea").expect("the fixture models qsPea");
        let cell = CellId {
            rune,
            stance: rune,
            entry: None,
            exit: None,
            adjustments: Vec::new(),
        };
        let mut labels = LabelPool::default();
        let mut transitions: Vec<TransitionRow> = Vec::new();
        let mut outcomes: Vec<Label> = Vec::new();
        let mut seats: Vec<Settled> = Vec::new();
        let mut notes: Vec<Vec<String>> = Vec::new();
        for row in rows {
            let settled = SettledSeat::at(seats.len());
            seats.push(Settled {
                cell: cell.clone(),
                seam: row.seam.then_some(rune),
                extension: 0,
            });
            outcomes.push(labels.intern(row.outcome));
            let provenance = NotesSeat::at(notes.len());
            notes.push(vec![row.provenance.to_owned()]);
            transitions.push(TransitionRow {
                input_glyph: labels.intern(row.input),
                left: labels.intern(row.left),
                right1: labels.intern(row.right1),
                right2: labels.intern(row.right2),
                right3: labels.intern(row.right3),
                right4: labels.intern(row.right4),
                settled,
                left_settled: None,
                provenance,
                prospect: row.prospect,
                joint: false,
            });
        }
        transitions.sort_by(|left, right| left.key(&labels).cmp(&right.key(&labels)));
        FixpointProduct {
            config: "compressed-test".to_owned(),
            transitions,
            labels,
            outcomes,
            deep_classes: classes
                .iter()
                .map(|(token, members)| {
                    (
                        (*token).to_owned(),
                        members.iter().map(|member| (*member).to_owned()).collect(),
                    )
                })
                .collect(),
            cited_provenance: Vec::new(),
            cells: vec![cell],
            seats,
            notes,
        }
    }

    fn boundary_rows() -> Vec<RowSpec<'static>> {
        ["#EDGE", "periodcentered", "space", "uni200C"]
            .into_iter()
            .map(|right1| RowSpec {
                input: "qsPea",
                left: "#EDGE",
                right1,
                right2: "#NA",
                right3: "#NA",
                right4: "#NA",
                outcome: "qsPea",
                provenance: "boundary",
                prospect: 0,
                seam: false,
            })
            .collect()
    }

    #[test]
    fn crossing_context_classes_refine_to_exact_atoms() {
        let (universe, cuts) = cuts(&[&["a", "b"], &["b", "c"]]);
        let atoms = AxisAtoms::from_cuts(universe, &cuts);
        let held: Vec<Vec<&str>> = atoms
            .atoms
            .iter()
            .map(|atom| atom.members.iter().map(|member| &**member).collect())
            .collect();
        assert_eq!(held, [vec!["a"], vec!["b"], vec!["c"]]);
    }

    #[test]
    fn labels_with_the_same_context_incidence_share_an_atom() {
        let (universe, cuts) = cuts(&[&["a", "z", "b", "c"], &["a", "z"]]);
        let atoms = AxisAtoms::from_cuts(universe, &cuts);
        let held: Vec<Vec<&str>> = atoms
            .atoms
            .iter()
            .map(|atom| atom.members.iter().map(|member| &**member).collect())
            .collect();
        assert_eq!(held, [vec!["a", "z"], vec!["b", "c"]]);
    }

    #[test]
    fn expanding_interleaved_atoms_restores_concrete_lexical_order() {
        let mut map: HashMap<Rc<str>, Vec<Rc<str>>> = HashMap::default();
        map.insert(Rc::from("a"), vec![Rc::from("a"), Rc::from("z")]);
        map.insert(Rc::from("b"), vec![Rc::from("b"), Rc::from("c")]);
        let mut slot = Some(vec![Rc::from("a"), Rc::from("b")]);
        expand_slot(&mut slot, &map);
        let held: Vec<&str> = slot
            .as_ref()
            .expect("the constrained slot stays constrained")
            .iter()
            .map(|member| &**member)
            .collect();
        assert_eq!(held, ["a", "b", "c", "z"]);
    }

    #[test]
    fn a_literal_deep_boundary_class_keeps_its_required_order() {
        let mut map: HashMap<Rc<str>, Vec<Rc<str>>> = HashMap::default();
        let mut slot = Some(
            crate::fold::BOUNDARY_LOOKAHEAD_CLASS
                .into_iter()
                .map(Rc::from)
                .collect(),
        );
        for member in slot.as_ref().expect("the boundary slot is constrained") {
            map.insert(Rc::clone(member), vec![Rc::clone(member)]);
        }
        expand_slot(&mut slot, &map);
        assert_eq!(
            slot.expect("the boundary slot stays constrained"),
            crate::fold::BOUNDARY_LOOKAHEAD_CLASS.map(Rc::from)
        );
    }

    #[test]
    fn boundaries_are_forced_to_singleton_atoms() {
        let universe = labels(&["#NA", "space", "a", "b"]);
        let mut cuts = Vec::new();
        add_cut(&mut cuts, &universe.iter().cloned().collect::<Vec<_>>());
        singleton_boundaries(&universe, &mut cuts);
        let atoms = AxisAtoms::from_cuts(universe, &cuts);
        for boundary in ["#NA", "space"] {
            assert_eq!(
                atoms.by_representative[boundary]
                    .iter()
                    .map(|member| &**member)
                    .collect::<Vec<_>>(),
                [boundary]
            );
        }
    }

    #[test]
    fn successors_with_the_same_bad_fourth_set_share_one_third_slot_cut() {
        // Two bad successors with the same third-slot member set form one `{a, b} x {d}` rectangle. The quotient keeps a and b together while separating the rectangle from c and from e.
        let source3 = labels(&["a", "b", "c"]);
        let source4 = labels(&["d", "e"]);
        let source3_cut: Vec<Rc<str>> = source3.iter().cloned().collect();
        let source4_cut: Vec<Rc<str>> = source4.iter().cloned().collect();
        let atoms3 = AxisAtoms::from_cuts(
            source3.clone(),
            &[source3_cut, vec![Rc::from("a"), Rc::from("b")]],
        );
        let atoms4 = AxisAtoms::from_cuts(source4.clone(), &[source4_cut, vec![Rc::from("d")]]);
        assert_eq!(atoms3.atoms.len(), 2);
        assert_eq!(atoms4.atoms.len(), 2);
        assert_eq!(&*atoms3.atoms[0].representative, "a");
        assert_eq!(
            atoms3.atoms[0]
                .members
                .iter()
                .map(|member| &**member)
                .collect::<Vec<_>>(),
            ["a", "b"]
        );
        assert_eq!(&*atoms4.atoms[0].representative, "d");
    }

    #[test]
    fn grouped_divergent_successors_leave_the_joint_relation_rectangular() {
        let product = product(
            &[
                RowSpec {
                    input: "qsPea",
                    left: "#EDGE",
                    right1: "next",
                    right2: "after",
                    right3: "#C3abc",
                    right4: "#C4de",
                    outcome: "state",
                    provenance: "source",
                    prospect: 0,
                    seam: false,
                },
                RowSpec {
                    input: "next",
                    left: "state",
                    right1: "after",
                    right2: "a",
                    right3: "d",
                    right4: "#NA",
                    outcome: "next-a",
                    provenance: "successor a",
                    prospect: 0,
                    seam: true,
                },
                RowSpec {
                    input: "next",
                    left: "state",
                    right1: "after",
                    right2: "b",
                    right3: "d",
                    right4: "#NA",
                    outcome: "next-b",
                    provenance: "successor b",
                    prospect: 0,
                    seam: true,
                },
            ],
            &[("#C3abc", &["a", "b", "c"]), ("#C4de", &["d", "e"])],
        );
        let compressed = CompressedRows::new(&product).expect("the relation builds");
        let source = product
            .transitions
            .iter()
            .position(|row| {
                &**product.labels.text(row.input_glyph) == "qsPea"
                    && &**product.labels.text(row.right3) == "#C3abc"
            })
            .expect("the source row is present");
        let mut rectangles: Vec<(&str, &str, bool)> = compressed
            .fold_rows()
            .iter()
            .filter(|row| row.seat as usize == source)
            .map(|row| (&*row.right3, &*row.right4, row.joint))
            .collect();
        rectangles.sort();
        assert_eq!(
            rectangles,
            [
                ("a", "d", true),
                ("a", "e", false),
                ("c", "d", false),
                ("c", "e", false),
            ]
        );
        assert!(compressed.class_joint()[source]);
    }

    #[test]
    fn a_source_class_is_always_a_union_of_its_atoms() {
        let (universe, cuts) = cuts(&[&["a", "b", "c"], &["b", "c"], &["c", "d"]]);
        let atoms = AxisAtoms::from_cuts(universe, &cuts);
        for cut in &cuts {
            let mut rebuilt: Vec<Rc<str>> = atoms
                .inside_indices(cut)
                .expect("the defining cuts cannot split their own atoms")
                .into_iter()
                .flat_map(|seat| atoms.atoms[seat].members.iter().cloned())
                .collect();
            rebuilt.sort();
            assert_eq!(&rebuilt, cut);
        }
    }

    #[test]
    fn atom_members_are_sorted_and_represented_by_their_minimum() {
        let universe = labels(&["z", "a", "m"]);
        let mut cut: Vec<Rc<str>> = universe.iter().cloned().collect();
        cut.sort();
        let atoms = AxisAtoms::from_cuts(universe, &[cut]);
        assert_eq!(&*atoms.atoms[0].representative, "a");
        assert_eq!(
            atoms.atoms[0]
                .members
                .iter()
                .map(|member| &**member)
                .collect::<Vec<_>>(),
            ["a", "m", "z"]
        );
    }

    #[test]
    fn overlapping_classes_in_different_near_contexts_fold_to_the_same_ordered_rules() {
        let mut rows = boundary_rows();
        for (right3, right4, outcome, provenance) in [
            ("#C3az", "#C4dy", "out-1", "a/z then d/y"),
            ("#C3az", "#C4ef", "out-2", "a/z then e/f"),
            ("#C3bc", "#C4dy", "out-3", "b/c then d/y"),
            ("#C3bc", "#C4ef", "out-4", "b/c then e/f"),
        ] {
            rows.push(RowSpec {
                input: "qsPea",
                left: "#EDGE",
                right1: "lead",
                right2: "mid",
                right3,
                right4,
                outcome,
                provenance,
                prospect: 0,
                seam: false,
            });
        }
        for (right3, right4, outcome, provenance) in [
            ("#C3ab", "#C4de", "out-5", "a/b then d/e"),
            ("#C3ab", "#C4fy", "out-6", "a/b then f/y"),
            ("#C3cz", "#C4de", "out-7", "c/z then d/e"),
            ("#C3cz", "#C4fy", "out-8", "c/z then f/y"),
        ] {
            rows.push(RowSpec {
                input: "qsPea",
                left: "#EDGE",
                right1: "other",
                right2: "mid-2",
                right3,
                right4,
                outcome,
                provenance,
                prospect: 0,
                seam: false,
            });
        }
        let product = product(
            &rows,
            &[
                ("#C3ab", &["a", "b"]),
                ("#C3az", &["a", "z"]),
                ("#C3bc", &["b", "c"]),
                ("#C3cz", &["c", "z"]),
                ("#C4de", &["d", "e"]),
                ("#C4dy", &["d", "y"]),
                ("#C4ef", &["e", "f"]),
                ("#C4fy", &["f", "y"]),
            ],
        );
        let input: Rc<str> = Rc::from("qsPea");
        let expanded = expand(&product);
        let expected = rules_for_input(&input, &LabelRows::new(&product, &expanded), false)
            .expect("the concrete relation folds");
        let compressed = CompressedRows::new(&product).expect("the atom relation builds");
        let mut actual = rules_for_input(&input, &compressed.rows(&product), false)
            .expect("the atom relation folds");
        compressed.expand_rules(&input, &mut actual.rules);
        assert_eq!(actual.rules, expected.rules);
        assert_eq!(actual.identity_guards, expected.identity_guards);
        assert_eq!(actual.replay_lefts, expected.replay_lefts);
        assert!(actual.rules.iter().any(|rule| rule.look3.is_some()));
        assert!(actual.rules.iter().any(|rule| rule.look4.is_some()));
        assert!(actual.rules.iter().any(|rule| {
            rule.provenance
                .first()
                .is_some_and(|pointer| pointer == "a/z then d/y")
        }));
    }
}
