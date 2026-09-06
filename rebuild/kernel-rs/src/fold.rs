//! The table build's fold, which since the `build-tables` verb landed runs in this crate on the product the worklist just produced rather than on a stream Python parsed back: the class-grain rows expanded to label grain, the prospect-divergence flag pass over that expansion, the per-input rule fold ([`crate::rulefold`]), the treaty fold, and the assertions the build states its tables under.
//!
//! This is the fold, and there is no other: `table.py`'s `assemble_tables` was transcribed into these modules, held byte-identical to them across the whole scaling ladder and the live alphabet, and then deleted — git preserves it, and `rebuild/pipeline/table.py` now carries the data model, the artifact readers and the digests alone. What binds this fold is therefore not a twin but three independent checks: byte-identity of the artifacts [`crate::artifacts`] writes against a stamped baseline, which is where a rule-ordering divergence shows since that ordering is the shipped GSUB; `rebuild/test_table.py`, which replays these rules against these rows on the mini fixture in its own implementation of first-match-wins; and `gate:conform`, which shapes the compiled font through HarfBuzz against this crate's own per-window settlement on every cycle.
//!
//! The expansion is where this fold's whole memory argument sits. Python materialized the label-grain stream as whole `Transition` objects — a second copy of a product that already cost gigabytes — because a class row expands to its full member product at right3 x right4. Here an expanded row is the seat of the class row it came from plus its two deep labels, which is what makes the fold's own working set a rounding error beside the enumeration's: everything else an expanded row says (the input, the left, the two near slots, the outcome, the settled cells, the prospect and the provenance) is the class row's and is read through the seat.
//!
//! Expansion order is the sort Python performs over the whole stream, arrived at without performing it: the product's rows are already in key order, so rows sharing an (input, left, right1, right2) prefix are contiguous, and sorting each such run by its two deep labels alone leaves the whole vector in the order a global sort would. Both sorts are stable, so rows that tie on the full key keep the class-row order Python's stable sort would have kept them in.
//!
//! The replay that asserts the partition is also where the rules meet their realizing strings. It records, per rule, the replayed rows with the shortest producer chains that first-match it, and [`crate::certificate`] closes each such row's chain into a string the rule first-matches at the row's own position — one certificate per rule, written into the windows head beside the rules, which is how the build proves every rule reachable by settling rather than by searching.

use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use crate::certificate;
use crate::index::SpecIndex;
use crate::options::WindowOptions;
use crate::rulefold::rules_for_input;
use crate::stream::{
    FixpointProduct, TransitionRow, cell_key, cell_key_repr, key_repr, python_repr, python_tuple,
};
use crate::types::{AdjustmentToken, CellId, Settled, Side};

/// The label a slot the window does not carry is spelled with, `table.NA_LABEL`.
pub const NA_LABEL: &str = "#NA";

/// The lookahead class every boundary-outcome rule carries, `table.BOUNDARY_LOOKAHEAD_CLASS`, in its own order rather than sorted.
pub const BOUNDARY_LOOKAHEAD_CLASS: [&str; 3] = ["uni200C", "space", "periodcentered"];

/// Every label a window slot can carry that is not a letter, `table.BOUNDARYISH`. A deep-class id is never one.
pub fn boundaryish(label: &str) -> bool {
    matches!(
        label,
        "#EDGE" | "#NA" | "space" | "uni200C" | "periodcentered"
    )
}

/// One ordered settlement rule, `table.Rule`: the input it rewrites, the four lookahead slots and the backtrack slot as positive classes or `None` for "unconstrained", the outcome, the authored pointers that produced it, and the section 6.1 joint flag.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Rule {
    pub input_glyph: Rc<str>,
    pub backtrack: Option<Vec<Rc<str>>>,
    pub look1: Option<Vec<Rc<str>>>,
    pub look2: Option<Vec<Rc<str>>>,
    pub look3: Option<Vec<Rc<str>>>,
    pub look4: Option<Vec<Rc<str>>>,
    pub outcome: Rc<str>,
    pub provenance: Vec<String>,
    pub joint: bool,
}

impl Rule {
    /// The five constrained slots in the order a replay tests them.
    fn slots(&self) -> [&Option<Vec<Rc<str>>>; 5] {
        [
            &self.backtrack,
            &self.look1,
            &self.look2,
            &self.look3,
            &self.look4,
        ]
    }
}

/// One treaty row, `table.TreatyRow`: the two settled cells a seam joins, the height it joins at (or `break`), and the connector pixels the seam carries. `kern` is always zero and is spelled anyway, because the TSV column is part of the artifact.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct TreatyRow {
    pub left: Rc<str>,
    pub right: Rc<str>,
    pub junction: String,
    pub extension: i64,
    pub kern: i64,
}

#[derive(Debug)]
/// One configuration's decision table, `table.DecisionTable` as a freshly folded one stands: the class-grain rows with the fold's own joint flags, the ordered rules, and the head fields the windows artifact and every downstream consumer read.
///
/// The rows' two settled seats index the product's own seat table, which the decision table does not carry: nothing past the fold reads a settled record — the windows artifact and the digest spell a row's six labels and its outcome and no more — so the table would ride along unread.
pub struct DecisionTable {
    pub config: String,
    pub transitions: Vec<TransitionRow>,
    pub rules: Vec<Rule>,
    pub identity_guard_rules: i64,
    pub cited_provenance: Vec<String>,
    pub deep_classes: Vec<(String, Vec<String>)>,
    pub cells: Vec<CellId>,
    /// One realizing string per rule, in rule order, as the tokens the text spells — rune names and the three boundary glyph labels — closed by [`crate::certificate`] so the rule first-matches at the row's own position. The windows head carries them beside the rules.
    pub certificates: Vec<Vec<String>>,
}

/// One configuration's treaty table, `table.TreatyTable`.
#[derive(Debug)]
pub struct TreatyTable {
    pub config: String,
    pub rows: Vec<TreatyRow>,
}

/// What one configuration's fold produced: its two tables, and the lefts the reduced replay covered.
///
/// The lefts ride out because the assertion has already run under them: a caller that perturbs the rules and wants to know whether the reduction still notices — the negative control the reduction owes — has to re-run the same replay the build runs rather than a whole-table one, which would prove a different statement.
#[derive(Debug)]
pub struct Folded {
    pub decision: DecisionTable,
    pub treaty: TreatyTable,
    pub replay_lefts: ReplayLefts,
}

/// The lefts a first-match-wins replay has to cover, per input glyph.
pub type ReplayLefts = HashMap<Rc<str>, HashSet<Rc<str>>>;

/// One label-grain row: the seat of the class row it expanded from, the two deep labels it stands at, and the joint flag the prospect pass leaves on it. Everything else it says is read through the seat.
pub struct FoldRow {
    pub seat: u32,
    pub right3: Rc<str>,
    pub right4: Rc<str>,
    pub joint: bool,
}

/// The label-grain stream as the fold and the rule fold read it: the class rows a seat indexes into, the provenance table those rows' notes seats index, and the expanded rows themselves. Sliced by input, which is what [`rules_for_input`] is handed.
#[derive(Clone, Copy)]
pub struct LabelRows<'a> {
    class: &'a [TransitionRow],
    notes: &'a [Vec<String>],
    fold: &'a [FoldRow],
}

impl<'a> LabelRows<'a> {
    pub fn new(class: &'a [TransitionRow], notes: &'a [Vec<String>], fold: &'a [FoldRow]) -> Self {
        Self { class, notes, fold }
    }

    /// The rows between two seats of the expansion, over the same class rows.
    pub fn slice(&self, start: usize, end: usize) -> Self {
        Self {
            class: self.class,
            notes: self.notes,
            fold: &self.fold[start..end],
        }
    }

    pub fn len(&self) -> usize {
        self.fold.len()
    }

    pub fn is_empty(&self) -> bool {
        self.fold.is_empty()
    }

    pub fn base(&self, row: usize) -> &'a TransitionRow {
        &self.class[self.fold[row].seat as usize]
    }

    pub fn input_glyph(&self, row: usize) -> &'a Rc<str> {
        &self.base(row).input_glyph
    }

    pub fn left(&self, row: usize) -> &'a Rc<str> {
        &self.base(row).left
    }

    pub fn right1(&self, row: usize) -> &'a Rc<str> {
        &self.base(row).right1
    }

    pub fn right2(&self, row: usize) -> &'a Rc<str> {
        &self.base(row).right2
    }

    pub fn right3(&self, row: usize) -> &'a Rc<str> {
        &self.fold[row].right3
    }

    pub fn right4(&self, row: usize) -> &'a Rc<str> {
        &self.fold[row].right4
    }

    pub fn outcome(&self, row: usize) -> &'a Rc<str> {
        &self.base(row).outcome
    }

    pub fn joint(&self, row: usize) -> bool {
        self.fold[row].joint
    }

    pub fn provenance(&self, row: usize) -> &'a [String] {
        &self.notes[self.base(row).provenance.index()]
    }

    /// The six labels one row is keyed by, in `table.Window.key` order.
    pub fn key(&self, row: usize) -> [&'a str; 6] {
        let base = self.base(row);
        [
            &base.input_glyph,
            &base.left,
            &base.right1,
            &base.right2,
            &self.fold[row].right3,
            &self.fold[row].right4,
        ]
    }
}

/// [`fold_with`] over a fresh [`WindowOptions`], for the callers that hold none — the tests, and any fold of a product that did not come straight out of this process's own enumeration.
pub fn fold_product(index: &SpecIndex, product: FixpointProduct) -> Result<Folded, String> {
    let mut options = WindowOptions::new(index).map_err(|error| error.to_string())?;
    fold_with(index, product, &mut options)
}

/// One configuration's two tables, folded from the product the worklist produced, with no stream between them: the expansion, then the passes in order, then the raises where a fold-side invariant does not hold, and last the certificates, one realizing string per rule closed over `options` — the enumeration's own, lent so the formation guard's verdicts are read out of the memo that already answered the worklist rather than swept a second time.
///
/// The assertions run where the transcribed fold ran them — the reachable-cells cross-check between the rule fold and the treaty fold, the reduced first-match-wins replay last — with the deep-class union check after them, which the Python original stated only on its fixture because there it cost nothing, and which costs almost nothing here either.
pub fn fold_with(
    index: &SpecIndex,
    mut product: FixpointProduct,
    options: &mut WindowOptions<'_>,
) -> Result<Folded, String> {
    assert_key_sorted(&product.transitions)?;
    let mut fold_rows = expand(&product);
    flag_prospect_joints(&product.transitions, &product.seats, &mut fold_rows);
    let mut class_joint: Vec<bool> = product.transitions.iter().map(|row| row.joint).collect();
    for row in &fold_rows {
        if row.joint {
            class_joint[row.seat as usize] = true;
        }
    }
    for (row, joint) in product.transitions.iter_mut().zip(&class_joint) {
        row.joint = *joint;
    }

    let rows = LabelRows::new(&product.transitions, &product.notes, &fold_rows);
    let mut rules: Vec<Rule> = Vec::new();
    let mut identity_guards: i64 = 0;
    let mut replay_lefts: ReplayLefts = HashMap::new();
    for (start, end) in input_runs(&rows) {
        let slice = rows.slice(start, end);
        let input_glyph = Rc::clone(slice.input_glyph(0));
        let rune = input_glyph.split('.').next().unwrap_or(&input_glyph);
        let Some(modeled) = index.sym_of(rune).filter(|name| index.is_modeled(*name)) else {
            return Err(format!(
                "{input_glyph}: the spec models no rune {}",
                python_repr(rune)
            ));
        };
        let never_locked = !index.is_entry_bearing(modeled);
        let folded = rules_for_input(&input_glyph, &slice, never_locked)?;
        rules.extend(folded.rules);
        identity_guards += folded.identity_guards;
        replay_lefts.insert(input_glyph, folded.replay_lefts);
    }

    assert_reachable_cells(index, &rows, &product.seats, &product.cells)?;

    let entry_extensions: HashMap<&CellId, i64> = product
        .cells
        .iter()
        .map(|cell| (cell, entry_extension(cell)))
        .collect();
    let mut seen: HashSet<(Rc<str>, Rc<str>, String, i64)> = HashSet::new();
    for row in 0..rows.len() {
        let base = rows.base(row);
        let Some(left_settled) = product.left_settled(base) else {
            continue;
        };
        match left_settled.seam {
            None => {
                seen.insert((
                    Rc::clone(&base.left),
                    Rc::clone(&base.outcome),
                    "break".to_owned(),
                    0,
                ));
            }
            Some(seam) => {
                seen.insert((
                    Rc::clone(&base.left),
                    Rc::clone(&base.outcome),
                    index.resolve(seam).to_owned(),
                    left_settled.extension + entry_extensions[&product.settled(base).cell],
                ));
            }
        }
    }
    // Both folds sort the whole row: a set iterated in hash order is all that would separate two rows tying on (left, right, junction), and that is not an order either side can reproduce.
    let mut treaty_rows: Vec<TreatyRow> = seen
        .into_iter()
        .map(|(left, right, junction, extension)| TreatyRow {
            left,
            right,
            junction,
            extension,
            kern: 0,
        })
        .collect();
    treaty_rows.sort();

    let prefixes = certificate::Prefixes::over(&rows);
    let first_rows = first_match_rows(
        &rows,
        &rules,
        Some(&replay_lefts),
        certificate::ROW_CAP,
        Some(prefixes.dist()),
    )?;
    assert_deep_class_unions(&product, &rules)?;
    let certificates = certificate::certify(index, options, &prefixes, &rows, &rules, &first_rows)?;

    let config = product.config.clone();
    let decision = DecisionTable {
        config: product.config,
        transitions: product.transitions,
        rules,
        identity_guard_rules: identity_guards,
        cited_provenance: product.cited_provenance,
        deep_classes: product.deep_classes,
        cells: product.cells,
        certificates,
    };
    Ok(Folded {
        decision,
        treaty: TreatyTable {
            config,
            rows: treaty_rows,
        },
        replay_lefts,
    })
}

/// The precondition [`fold_product`] and [`expand`] read a product under: its rows are in `table.Window.key` order, which is what makes an input's rows one contiguous run, a left's rows one contiguous run inside that, and the per-prefix expansion sort a global one. The transcribed fold re-sorted and grouped through dicts instead, so it folded any row order; here the order is the contract `FixpointProduct` states it carries, and a product that breaks it is refused rather than folded into duplicated blocks.
fn assert_key_sorted(rows: &[TransitionRow]) -> Result<(), String> {
    for pair in rows.windows(2) {
        if pair[1].key() < pair[0].key() {
            return Err(format!(
                "the product's rows are not in key order: {} follows {}",
                key_repr(pair[1].key()),
                key_repr(pair[0].key())
            ));
        }
    }
    Ok(())
}

/// The label-grain expansion of one product, in `table.Window.key` order. See the module docstring for why the sort is per prefix run rather than global. Public so a caller replaying a perturbed rule list can build the same rows the fold asserted over.
pub fn expand(product: &FixpointProduct) -> Vec<FoldRow> {
    let mut pool: HashSet<Rc<str>> = HashSet::new();
    let mut members: HashMap<&str, Vec<Rc<str>>> = HashMap::new();
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

    let rows = &product.transitions;
    let mut expanded: Vec<FoldRow> = Vec::with_capacity(rows.len());
    let mut start = 0;
    while start < rows.len() {
        let mut end = start + 1;
        while end < rows.len() && near_slots(&rows[end]) == near_slots(&rows[start]) {
            end += 1;
        }
        let run = expanded.len();
        for (seat, row) in rows.iter().enumerate().take(end).skip(start) {
            let own3 = std::slice::from_ref(&row.right3);
            let own4 = std::slice::from_ref(&row.right4);
            let members3 = members.get(&*row.right3).map_or(own3, Vec::as_slice);
            let members4 = members.get(&*row.right4).map_or(own4, Vec::as_slice);
            for right3 in members3 {
                for right4 in members4 {
                    expanded.push(FoldRow {
                        seat: seat as u32,
                        right3: Rc::clone(right3),
                        right4: Rc::clone(right4),
                        joint: row.joint,
                    });
                }
            }
        }
        expanded[run..].sort_by(|left, right| {
            (&*left.right3, &*left.right4).cmp(&(&*right.right3, &*right.right4))
        });
        start = end;
    }
    expanded
}

/// The four labels a run of the product shares while its deep slots vary.
fn near_slots(row: &TransitionRow) -> [&str; 4] {
    [&row.input_glyph, &row.left, &row.right1, &row.right2]
}

/// Compare every row's optimistic prospect against the follower's actual settled choice and flag divergent rows joint (design section 6.1 step 4.2).
///
/// The successor index is keyed on the follower's (left, input, right1), which is the row's own (outcome, right1, right2), so the scan never touches a window the first three slots already rule out. Nothing here reads a successor's joint flag, only the seam it settled — read through `seats`, the product's table the follower's settled seat indexes — so the pass is order-free and the flags can be applied in one sweep afterwards.
fn flag_prospect_joints(class: &[TransitionRow], seats: &[Settled], fold: &mut [FoldRow]) {
    let mut successors: HashMap<(&str, &str, &str), Vec<u32>> = HashMap::new();
    for (seat, row) in fold.iter().enumerate() {
        let base = &class[row.seat as usize];
        successors
            .entry((&base.left, &base.input_glyph, &base.right1))
            .or_default()
            .push(seat as u32);
    }
    let mut flagged: Vec<u32> = Vec::new();
    for (seat, row) in fold.iter().enumerate() {
        if row.joint {
            continue;
        }
        let base = &class[row.seat as usize];
        if boundaryish(&base.right1) || boundaryish(&base.right2) {
            continue;
        }
        let Some(candidates) = successors.get(&(&*base.outcome, &*base.right1, &*base.right2))
        else {
            continue;
        };
        for &candidate in candidates {
            let successor = &fold[candidate as usize];
            let followed = &class[successor.seat as usize];
            if &*row.right3 != NA_LABEL && followed.right2 != row.right3 {
                continue;
            }
            if &*row.right4 != NA_LABEL && successor.right3 != row.right4 {
                continue;
            }
            if i8::from(seats[followed.settled.index()].seam.is_some()) != base.prospect {
                flagged.push(seat as u32);
                break;
            }
        }
    }
    for seat in flagged {
        fold[seat as usize].joint = true;
    }
}

/// The half-open ranges of the expansion each input glyph occupies. The rows are key-sorted and the input is the key's first component, so an input's rows are one contiguous run and the runs arrive in the sorted order `assemble_tables` folds them in.
fn input_runs(rows: &LabelRows<'_>) -> Vec<(usize, usize)> {
    let mut runs: Vec<(usize, usize)> = Vec::new();
    let mut start = 0;
    while start < rows.len() {
        let mut end = start + 1;
        while end < rows.len() && rows.input_glyph(end) == rows.input_glyph(start) {
            end += 1;
        }
        runs.push((start, end));
        start = end;
    }
    runs
}

/// How far one settled cell's own adjustments move its entry, `table._entry_extension` — the term the treaty fold adds to the left's extension.
fn entry_extension(cell: &CellId) -> i64 {
    let mut total = 0;
    for token in &cell.adjustments {
        match *token {
            AdjustmentToken::Extend(Side::Entry, by) => total += by,
            AdjustmentToken::Contract(Side::Entry, by) => total -= by,
            _ => {}
        }
    }
    total
}

/// One cheap loud check that the two grains still agree: the cells the fold rows settle into, read through the product's seat table, are the product's own.
fn assert_reachable_cells(
    index: &SpecIndex,
    rows: &LabelRows<'_>,
    seats: &[Settled],
    cells: &[CellId],
) -> Result<(), String> {
    let folded: HashSet<&CellId> = (0..rows.len())
        .map(|row| &seats[rows.base(row).settled.index()].cell)
        .collect();
    let counted: HashSet<&CellId> = cells.iter().collect();
    if folded == counted {
        return Ok(());
    }
    let mut different: Vec<(crate::stream::CellKey, String)> = folded
        .symmetric_difference(&counted)
        .map(|cell| {
            let key = cell_key(index, cell);
            let repr = cell_key_repr(&key);
            (key, repr)
        })
        .collect();
    different.sort_by(|left, right| left.0.cmp(&right.0));
    let listed: Vec<&str> = different.iter().map(|(_, repr)| repr.as_str()).collect();
    Err(format!(
        "the product's reachable cells disagree with the fold rows': [{}]",
        listed.join(", ")
    ))
}

/// The hard build invariant (prototype follow-up 1): replay reachable transitions against the ordered rules under first-match-wins semantics and require the rules to predict what settlement enumerated, `table.DecisionTable.assert_outcome_partition`.
///
/// `lefts` is the reduction the rule fold hands back — one representative of every committed left block plus every member of the boundary block — and that docstring in `table.py` carries the argument for why replaying those lefts proves the same statement as replaying all of them, the ZWNJ backtrack guards included. `None` replays every row, which is what a fixture small enough to afford it is held to.
///
/// The same pass also tallies which rule each replayed row first-matches, and a rule no replayed row ever reaches is refused alongside an outcome mismatch: a rule the ordering has shadowed is dead GSUB, and this replay is the only place in the fold that knows which rule won a row. The tally costs nothing beyond a flag per rule, because the replay already stops at the first match and had only to say which one that was.
///
/// It is exact under the reduction rather than merely suggestive, for two reasons. A committed block's rules carry the whole block in `backtrack`, so every member of that block matches the same slots the representative does and its rows first-match the same rule — replaying one member decides the block. And every rule reachable only from a boundary left — the default rules, the ZWNJ backtrack replicas and the identity catch-all [`crate::rulefold`] mints, `uni200C` being boundaryish — is replayed against every member of the boundary block, which the reduction keeps whole rather than reducing to a representative. So a rule that is never first under the reduction is never first over the whole table either, and `rebuild/test_table.py`'s `replay` restates both claims on the mini fixture in its own implementation of first-match-wins.
///
/// [`fold_product`] runs this under the reduction, so `build-tables` refuses a never-first rule where it folds one rather than after Python has parsed the artifact back, and a Python caller sees a `KernelRunError`. There is no second replay.
pub fn assert_outcome_partition(
    rows: &LabelRows<'_>,
    rules: &[Rule],
    lefts: Option<&ReplayLefts>,
) -> Result<(), String> {
    first_match_rows(rows, rules, lefts, 1, None).map(|_| ())
}

/// The rules grouped by the input they rewrite, each seat beside its rule in table order — the shape a first-match-wins replay walks.
pub fn rules_by_input(rules: &[Rule]) -> HashMap<&str, Vec<(usize, &Rule)>> {
    let mut by_input: HashMap<&str, Vec<(usize, &Rule)>> = HashMap::new();
    for (seat, rule) in rules.iter().enumerate() {
        by_input
            .entry(&rule.input_glyph)
            .or_default()
            .push((seat, rule));
    }
    by_input
}

/// First-match-wins over one window's six labels: the seat of the first rule of the input whose five constrained slots all admit the labels standing at them, or `None` where no rule matches and the input stands. This is the semantics the emitted lookup compiles to, stated once for the replay and the certificates both.
pub fn first_match(by_input: &HashMap<&str, Vec<(usize, &Rule)>>, key: [&str; 6]) -> Option<usize> {
    for (seat, rule) in by_input.get(key[0]).map_or(&[][..], Vec::as_slice) {
        if rule
            .slots()
            .iter()
            .zip([key[1], key[2], key[3], key[4], key[5]])
            .any(|(slot, label)| {
                slot.as_ref()
                    .is_some_and(|members| !members.iter().any(|member| &**member == label))
            })
        {
            continue;
        }
        return Some(*seat);
    }
    None
}

/// [`assert_outcome_partition`]'s replay, handing back what it learned on the way: for every rule, up to `keep` of the replayed rows that first-match it, as seats into `rows` — the ones with the shortest producer chains when `dist` ranks the rows ([`crate::certificate::Prefixes`]), an unreached row ranking last, else the first in replay order. The refusals are the assertion's own — an outcome mismatch, or a rule no replayed row first-matches — so a caller that gets rows back gets a whole partition with them.
pub fn first_match_rows(
    rows: &LabelRows<'_>,
    rules: &[Rule],
    lefts: Option<&ReplayLefts>,
    keep: usize,
    dist: Option<&[u32]>,
) -> Result<Vec<Vec<usize>>, String> {
    let by_input = rules_by_input(rules);
    let mut failures: Vec<String> = Vec::new();
    let mut count = 0usize;
    let mut first_rows: Vec<Vec<usize>> = vec![Vec::new(); rules.len()];
    let mut ranks: Vec<Vec<u32>> = vec![Vec::new(); rules.len()];
    for row in 0..rows.len() {
        let key = rows.key(row);
        if let Some(lefts) = lefts
            && !lefts
                .get(key[0])
                .is_some_and(|covered| covered.contains(key[1]))
        {
            continue;
        }
        let mut predicted: &str = key[0];
        if let Some(seat) = first_match(&by_input, key) {
            predicted = &rules[seat].outcome;
            match dist {
                None => {
                    if first_rows[seat].len() < keep {
                        first_rows[seat].push(row);
                    }
                }
                Some(dist) => {
                    let rank = dist[row];
                    let kept = &mut first_rows[seat];
                    let ranked = &mut ranks[seat];
                    if kept.len() < keep || rank < *ranked.last().expect("a full list has a last") {
                        let at = ranked.partition_point(|held| *held <= rank);
                        ranked.insert(at, rank);
                        kept.insert(at, row);
                        if kept.len() > keep {
                            ranked.pop();
                            kept.pop();
                        }
                    }
                }
            }
        }
        let settled: &str = rows.outcome(row);
        if predicted != settled {
            count += 1;
            if failures.len() < 5 {
                failures.push(format!(
                    "{}: settlement says {settled}, rules say {predicted}",
                    key_repr(key)
                ));
            }
        }
    }
    if count > 0 {
        return Err(format!(
            "{count} first-match-wins replay mismatches: {}",
            failures.join("; ")
        ));
    }
    let never: Vec<usize> = (0..rules.len())
        .filter(|seat| first_rows[*seat].is_empty())
        .collect();
    if never.is_empty() {
        return Ok(first_rows);
    }
    let listed: Vec<String> = never
        .iter()
        .take(5)
        .map(|seat| rule_repr(&rules[*seat]))
        .collect();
    Err(format!(
        "{} rule(s) no replayed row first-matches: {}",
        never.len(),
        listed.join("; ")
    ))
}

/// One rule as a refusal names it: the input it rewrites, its five constrained slots in the order a replay tests them with `any` for an unconstrained one, the outcome it would have written, and the first authored pointer that produced it.
fn rule_repr(rule: &Rule) -> String {
    let slots: Vec<String> = rule.slots().iter().map(|slot| slot_repr(slot)).collect();
    let provenance = match rule.provenance.first() {
        Some(line) => python_repr(line),
        None => "no provenance".to_owned(),
    };
    format!(
        "{} {} -> {}, from {provenance}",
        python_repr(&rule.input_glyph),
        python_tuple(&slots),
        python_repr(&rule.outcome)
    )
}

fn slot_repr(slot: &Option<Vec<Rc<str>>>) -> String {
    match slot {
        None => "any".to_owned(),
        Some(members) => {
            let names: Vec<&str> = members.iter().map(|member| &**member).collect();
            python_str_list(&names)
        }
    }
}

/// Every emitted look3/look4 letter class holds each class row's member set all-in or all-out within the row's own context — the fold-output assertion that licenses conform's representative-membership tests as exact rather than heuristic.
pub fn assert_deep_class_unions(product: &FixpointProduct, rules: &[Rule]) -> Result<(), String> {
    if product.deep_classes.is_empty() {
        return Ok(());
    }
    let members: HashMap<&str, HashSet<&str>> = product
        .deep_classes
        .iter()
        .map(|(token, names)| {
            (
                token.as_str(),
                names.iter().map(String::as_str).collect::<HashSet<&str>>(),
            )
        })
        .collect();
    let mut by_input: HashMap<&str, Vec<&Rule>> = HashMap::new();
    for rule in rules {
        by_input.entry(&rule.input_glyph).or_default().push(rule);
    }
    for row in &product.transitions {
        let set3 = members.get(&*row.right3);
        let set4 = members.get(&*row.right4);
        if set3.is_none() && set4.is_none() {
            continue;
        }
        for rule in by_input
            .get(&*row.input_glyph)
            .map_or(&[][..], Vec::as_slice)
        {
            if !matches_slot(&rule.backtrack, &row.left)
                || !matches_slot(&rule.look1, &row.right1)
                || !matches_slot(&rule.look2, &row.right2)
            {
                continue;
            }
            if let (Some(set3), Some(look3)) = (set3, &rule.look3) {
                let inside = intersection(set3, look3);
                if !inside.is_empty() && inside.len() != set3.len() {
                    return Err(split_class(row, &row.right3, "look3", &inside, set3));
                }
            }
            if let (Some(set4), Some(look4)) = (set4, &rule.look4) {
                let reaches = match &rule.look3 {
                    None => true,
                    Some(look3) => match set3 {
                        Some(set3) => !intersection(set3, look3).is_empty(),
                        None => look3.iter().any(|member| **member == *row.right3),
                    },
                };
                if reaches {
                    let inside = intersection(set4, look4);
                    if !inside.is_empty() && inside.len() != set4.len() {
                        return Err(split_class(row, &row.right4, "look4", &inside, set4));
                    }
                }
            }
        }
    }
    Ok(())
}

fn matches_slot(slot: &Option<Vec<Rc<str>>>, label: &str) -> bool {
    slot.as_ref()
        .is_none_or(|members| members.iter().any(|member| &**member == label))
}

fn intersection<'a>(set: &HashSet<&'a str>, look: &[Rc<str>]) -> Vec<&'a str> {
    let mut inside: Vec<&str> = look
        .iter()
        .filter_map(|member| set.get(&**member).copied())
        .collect();
    inside.sort_unstable();
    inside.dedup();
    inside
}

fn split_class(
    row: &TransitionRow,
    token: &str,
    slot: &str,
    inside: &[&str],
    whole: &HashSet<&str>,
) -> String {
    let mut all: Vec<&str> = whole.iter().copied().collect();
    all.sort_unstable();
    format!(
        "{}: an emitted {slot} class splits deep class {token} at {}: {} of {}",
        row.input_glyph,
        key_repr(row.key()),
        python_str_list(inside),
        python_str_list(&all)
    )
}

/// A list of strings in Python's own repr, which is what a raise message pastes a sorted member set in.
fn python_str_list(values: &[&str]) -> String {
    let quoted: Vec<String> = values.iter().map(|value| python_repr(value)).collect();
    format!("[{}]", quoted.join(", "))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::artifacts;
    use crate::fixpoint::{EnumerationModes, deep_class_id, enumerate_transitions};
    use crate::index::fixtures;
    use crate::types::{NotesSeat, SettledPool, SettledSeat};
    use std::cell::RefCell;

    /// The world the fixture is folded in — the shipping one, where a class-grain row's representative is output-visible.
    const SHIPPING: EnumerationModes = EnumerationModes {
        simulated_prospect: true,
        vote_slots: true,
        deep_classes: true,
    };

    /// The fixture's own fixpoint and the tables it folds into, which is the whole build over four families.
    fn built() -> (SpecIndex, FixpointProduct, Folded) {
        let index = fixtures::mini();
        let product = enumerate_transitions(&index, &[], SHIPPING)
            .expect("the fixture's fixpoint closes and settles");
        let folded = fold_product(&index, product.clone()).expect("and folds");
        (index, product, folded)
    }

    #[test]
    fn the_fixtures_fixpoint_folds_into_rules_windows_and_treaty_rows() {
        let (_index, _product, folded) = built();
        assert!(!folded.decision.rules.is_empty());
        assert!(!folded.decision.transitions.is_empty());
        assert!(!folded.treaty.rows.is_empty());
        assert!(!folded.decision.cited_provenance.is_empty());
    }

    /// The whole-table replay, which is what a fixture small enough to afford it is held to and what the reduction the build runs is measured against.
    #[test]
    fn every_enumerated_row_is_what_the_ordered_rules_predict() {
        let (_index, product, folded) = built();
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product.transitions, &product.notes, &fold_rows);
        assert_outcome_partition(&rows, &folded.decision.rules, None)
            .expect("first-match-wins over the whole table");
        assert_outcome_partition(&rows, &folded.decision.rules, Some(&folded.replay_lefts))
            .expect("and over the reduction the build replays");
    }

    /// A rule nothing can reach is dead GSUB, and this replay is the only pass that knows which rule won a row — so it refuses one. A backtrack naming a left the fixture never enumerates matches nothing, which leaves every prediction and therefore the outcome partition exactly as it was: what fails is the tally alone.
    #[test]
    fn a_rule_no_replayed_row_first_matches_is_refused() {
        let (_index, product, folded) = built();
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product.transitions, &product.notes, &fold_rows);
        let input = Rc::clone(&folded.decision.rules[0].input_glyph);
        let mut rules = folded.decision.rules.clone();
        rules.push(Rule {
            input_glyph: Rc::clone(&input),
            backtrack: Some(vec![Rc::from("qsNever.loop")]),
            look1: None,
            look2: None,
            look3: None,
            look4: None,
            outcome: Rc::clone(&input),
            provenance: vec!["a dead rule".into()],
            joint: false,
        });
        let message = assert_outcome_partition(&rows, &rules, Some(&folded.replay_lefts))
            .expect_err("a rule no row reaches is refused");
        assert!(
            message.contains("1 rule(s) no replayed row first-matches"),
            "{message}"
        );
        assert!(message.contains("qsNever.loop"), "{message}");
        assert!(message.contains("a dead rule"), "{message}");
    }

    /// The tally's other half, the one an unreachable slot cannot stand for: a duplicate of a rule already in the list matches exactly what its twin matches and the twin precedes it, so first-match-wins reaches it never while predicting every row the way it always did.
    #[test]
    fn a_shadowed_duplicate_is_refused() {
        let (_index, product, folded) = built();
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product.transitions, &product.notes, &fold_rows);
        let mut rules = folded.decision.rules.clone();
        let twin = rules.last().expect("the fixture folds rules").clone();
        rules.push(twin);
        let message = assert_outcome_partition(&rows, &rules, Some(&folded.replay_lefts))
            .expect_err("a shadowed duplicate is refused");
        assert!(
            message.contains("no replayed row first-matches"),
            "{message}"
        );
    }

    /// The negative control the reduction owes: every single-rule drop, every adjacent swap and every widened first-lookahead class that the whole-table replay notices is noticed by the reduced replay too. Perturbations neither catches are redundant rules, which is a fact about the fold rather than about the reduction.
    #[test]
    fn the_reduced_replay_catches_what_the_whole_table_replay_catches() {
        let (_index, product, folded) = built();
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product.transitions, &product.notes, &fold_rows);
        let rules = &folded.decision.rules;
        let mut perturbations: Vec<Vec<Rule>> = Vec::new();
        for seat in 0..rules.len() {
            let mut dropped = rules.clone();
            dropped.remove(seat);
            perturbations.push(dropped);
        }
        for seat in 0..rules.len() - 1 {
            let mut swapped = rules.clone();
            swapped.swap(seat, seat + 1);
            perturbations.push(swapped);
        }
        for seat in 0..rules.len() {
            if rules[seat].look1.is_none() {
                continue;
            }
            let mut widened = rules.clone();
            widened[seat].look1 = None;
            perturbations.push(widened);
        }
        let mut noticed = 0;
        for perturbed in &perturbations {
            if assert_outcome_partition(&rows, perturbed, Some(&folded.replay_lefts)).is_err() {
                noticed += 1;
                continue;
            }
            assert!(
                assert_outcome_partition(&rows, perturbed, None).is_ok(),
                "a perturbation the whole-table replay catches slipped past the reduced one"
            );
        }
        assert!(
            noticed > 0,
            "{noticed} of {} perturbations noticed",
            perturbations.len()
        );
        let reduced = folded.replay_lefts.iter().any(|(input, lefts)| {
            let all: HashSet<&str> = folded
                .decision
                .transitions
                .iter()
                .filter(|row| row.input_glyph == *input)
                .map(|row| &*row.left)
                .collect();
            lefts.len() < all.len()
        });
        assert!(
            reduced,
            "the fixture stopped reducing, so the control proves nothing"
        );
    }

    /// The proven rule-ordering discipline, `rebuild/test_table.py`'s own statement of it: within one (input, backtrack) group the boundary-outcome row with `uni200C` explicit in the class precedes every letter-lookahead row, and the slot-dropped fallback comes last.
    #[test]
    fn a_boundary_rule_leads_its_group_and_the_slot_dropped_fallback_ends_it() {
        let (_index, _product, folded) = built();
        /// One (input, backtrack) group and the rules it holds, in emission order.
        type Group<'a> = (&'a str, &'a Option<Vec<Rc<str>>>, Vec<&'a Rule>);
        let mut groups: Vec<Group<'_>> = Vec::new();
        for rule in &folded.decision.rules {
            match groups.iter_mut().find(|(input, backtrack, _)| {
                *input == &*rule.input_glyph && *backtrack == &rule.backtrack
            }) {
                Some((_, _, held)) => held.push(rule),
                None => groups.push((&rule.input_glyph, &rule.backtrack, vec![rule])),
            }
        }
        let boundary: Vec<Rc<str>> = BOUNDARY_LOOKAHEAD_CLASS
            .iter()
            .map(|l| Rc::from(*l))
            .collect();
        let mut saw_boundary = false;
        for (_input, _backtrack, rules) in &groups {
            let leading: Vec<usize> = (0..rules.len())
                .filter(|seat| {
                    rules[*seat].look1.as_ref() == Some(&boundary) && rules[*seat].look2.is_none()
                })
                .collect();
            let lettered: Vec<usize> = (0..rules.len())
                .filter(|seat| {
                    rules[*seat]
                        .look1
                        .as_ref()
                        .is_some_and(|look| look != &boundary)
                })
                .collect();
            let fallback: Vec<usize> = (0..rules.len())
                .filter(|seat| rules[*seat].look1.is_none() && rules[*seat].look2.is_none())
                .collect();
            if let (Some(first), Some(letter)) = (leading.first(), lettered.first()) {
                saw_boundary = true;
                assert!(first < letter, "a boundary rule follows a letter rule");
            }
            if let Some(last) = fallback.last() {
                assert_eq!(
                    *last,
                    rules.len() - 1,
                    "the slot-dropped fallback is not last"
                );
            }
        }
        assert!(saw_boundary, "the fixture stopped emitting boundary rules");
    }

    /// The prospect pass genuinely raises joints the fixpoint left unflagged, and never clears one the trace floor set.
    /// The four-family fixture reaches no divergent window of its own, so the pass's monotonicity is all it can say; [`tests::a_prospect_the_follower_contradicts_flags_its_row_joint`] states the flag itself over a hand-built product.
    #[test]
    fn the_prospect_pass_raises_joints_and_clears_none() {
        let (_index, product, folded) = built();
        let before: Vec<bool> = product.transitions.iter().map(|row| row.joint).collect();
        let after: Vec<bool> = folded
            .decision
            .transitions
            .iter()
            .map(|row| row.joint)
            .collect();
        assert_eq!(before.len(), after.len());
        assert!(before.iter().zip(&after).all(|(was, now)| *now || !*was));
    }

    /// The treaty fold: rows sorted, one per distinct seam, and the extension the seam's own connector pixels plus what the receiver's adjustments move its entry by.
    #[test]
    fn the_treaty_rows_are_sorted_and_distinct() {
        let (_index, _product, folded) = built();
        let rows = &folded.treaty.rows;
        let mut sorted = rows.clone();
        sorted.sort();
        assert_eq!(rows, &sorted);
        sorted.dedup();
        assert_eq!(rows.len(), sorted.len());
        assert!(
            rows.iter()
                .any(|row| row.junction == "break" && row.extension == 0)
        );
        assert!(rows.iter().all(|row| row.kern == 0));
    }

    /// Two folds of one product write the same bytes, and any rule or row that moves moves the contract digest.
    #[test]
    fn the_artifacts_are_diff_stable_and_the_digest_covers_them() {
        let (index, product, folded) = built();
        let twice = fold_product(&index, product).expect("the same product folds the same way");
        assert_eq!(
            artifacts::settlement_tsv(&folded.decision),
            artifacts::settlement_tsv(&twice.decision)
        );
        assert_eq!(
            artifacts::treaty_tsv(&folded.treaty),
            artifacts::treaty_tsv(&twice.treaty)
        );
        let whole = artifacts::table_digest(&index, &folded.decision, &folded.treaty);
        assert_eq!(
            whole,
            artifacts::table_digest(&index, &twice.decision, &twice.treaty)
        );
        let mut fewer_rules = twice.decision;
        fewer_rules.rules.pop();
        let mut fewer_rows = fold_product(&index, {
            let (_i, product, _f) = built();
            product
        })
        .expect("a third fold")
        .decision;
        fewer_rows.transitions.pop();
        let digests: HashSet<String> = [
            whole,
            artifacts::table_digest(&index, &fewer_rules, &folded.treaty),
            artifacts::table_digest(&index, &fewer_rows, &folded.treaty),
        ]
        .into_iter()
        .collect();
        assert_eq!(digests.len(), 3);
    }

    /// A product built by hand rather than enumerated: what the four-family fixture is too small to reach — a divergent prospect, a deep-class row, the ZWNJ backtrack guards on both arms, and the fold-side refusals.
    ///
    /// **Every input glyph names a rune the fixture models**, because the one verdict the fold reads off the spec is `is_entry_bearing` on that rune and the fold refuses a name the spec models no rune for, exactly as `assemble_tables` raises `KeyError` on one. `qsIt` is the fixture's rune with no entry surface at all, so an input under it takes the ZWNJ backtrack-guard arm and one under `qsPea` does not. The other slots' labels stay synthetic: nothing resolves them. Every row settles into the bench's one cell unless it is handed another, so the reachable-cells cross-check is satisfied by construction and a test that wants it to fail has to break it on purpose.
    struct Bench {
        index: SpecIndex,
        cell: CellId,
        seam: crate::model::Sym,
        seats: RefCell<SettledPool>,
    }

    impl Bench {
        fn new() -> Self {
            let index = fixtures::mini();
            let cell = CellId {
                rune: fixtures::sym(&index, "qsPea"),
                stance: fixtures::sym(&index, "half"),
                entry: None,
                exit: Some(fixtures::sym(&index, "baseline")),
                adjustments: Vec::new(),
            };
            let seam = fixtures::sym(&index, "baseline");
            Self {
                index,
                cell,
                seam,
                seats: RefCell::new(SettledPool::default()),
            }
        }

        /// One settled record's seat in the bench's own table, which every product the bench builds carries.
        fn seat(&self, settled: Settled) -> SettledSeat {
            self.seats.borrow_mut().seat(&settled)
        }

        /// One row: its seven labels, the prospect its trace claimed, and whether it committed a seam.
        fn row(&self, labels: [&str; 7], prospect: i8, joins: bool) -> TransitionRow {
            TransitionRow {
                input_glyph: Rc::from(labels[0]),
                left: Rc::from(labels[1]),
                right1: Rc::from(labels[2]),
                right2: Rc::from(labels[3]),
                right3: Rc::from(labels[4]),
                right4: Rc::from(labels[5]),
                outcome: Rc::from(labels[6]),
                settled: self.seat(Settled {
                    cell: self.cell.clone(),
                    seam: joins.then_some(self.seam),
                    extension: 0,
                }),
                left_settled: None,
                provenance: NotesSeat::at(0),
                prospect,
                joint: false,
            }
        }

        /// The rows a fixpoint enumerates beside one whose lookahead reaches the edge of the buffer: the same window with each real boundary glyph in that slot, settling the way the edge settles. `#EDGE` is a label no GSUB lookup can see, so the fold states the boundary case over [`BOUNDARY_LOOKAHEAD_CLASS`] instead — which means a hand-built product carrying the `#EDGE` row alone leaves that rule unreachable, and since the fold refuses a rule no replayed row first-matches, it is refused as the product no fixpoint would have produced.
        fn edge_kin(&self, labels: [&str; 7]) -> Vec<TransitionRow> {
            let slot = (2..6)
                .find(|slot| labels[*slot] == "#EDGE")
                .expect("the row reaches the edge of the buffer somewhere");
            BOUNDARY_LOOKAHEAD_CLASS
                .iter()
                .map(|glyph| {
                    let mut kin = labels;
                    kin[slot] = glyph;
                    self.row(kin, 0, false)
                })
                .collect()
        }

        /// The default block a fixpoint leaves behind every committed one: each boundary left — ZWNJ among them — carrying the same near lookahead and the same run edge, with that edge settling into the input's bare self. A hand-built product needs the whole block rather than one `#EDGE` left, because the rules the fold states over it are stated over the boundary glyphs and replicated under a `uni200C` backtrack, and the fold refuses any of those a replayed row cannot reach.
        fn boundary_block(&self, input: &str, near: &str, outcome: &str) -> Vec<TransitionRow> {
            let mut rows = Vec::new();
            for left in ["#EDGE", "space", "periodcentered", "uni200C"] {
                rows.push(self.row([input, left, near, "#NA", "#NA", "#NA", outcome], 0, false));
                rows.push(self.row([input, left, "#EDGE", "#NA", "#NA", "#NA", input], 0, false));
                rows.extend(self.edge_kin([input, left, "#EDGE", "#NA", "#NA", "#NA", input]));
            }
            rows
        }

        /// The bench cell with adjustments of its own, which is what gives two rows under one left different entry extensions and so two treaty rows tying on the triple.
        fn adjusted(&self, adjustments: Vec<AdjustmentToken>) -> CellId {
            CellId {
                adjustments,
                ..self.cell.clone()
            }
        }

        /// One row whose left committed a seam, which is the only shape the treaty fold reads: [`Bench::row`] leaves `left_settled` absent, so a product of those folds into no treaty rows at all.
        fn joined(&self, labels: [&str; 7], cell: CellId, left_extension: i64) -> TransitionRow {
            let mut row = self.row(labels, 0, false);
            row.settled = self.seat(Settled {
                cell,
                seam: None,
                extension: 0,
            });
            row.left_settled = Some(self.seat(Settled {
                cell: self.cell.clone(),
                seam: Some(self.seam),
                extension: left_extension,
            }));
            row
        }

        /// The rows as a product settling into the bench's one cell, sorted into the key order the fixpoint would have left them in. Every bench row's trace noted nothing, so the provenance table is the empty list alone.
        fn product(
            &self,
            rows: Vec<TransitionRow>,
            deep_classes: Vec<(String, Vec<String>)>,
        ) -> FixpointProduct {
            self.product_of(rows, deep_classes, vec![self.cell.clone()])
        }

        /// The same, for rows that settle into cells of their own — the cell vocabulary is what the cross-check holds the fold to, so a caller handing a row an adjusted cell counts it here.
        fn product_of(
            &self,
            mut rows: Vec<TransitionRow>,
            deep_classes: Vec<(String, Vec<String>)>,
            cells: Vec<CellId>,
        ) -> FixpointProduct {
            rows.sort_by(|left, right| left.key().cmp(&right.key()));
            FixpointProduct {
                config: "default".to_owned(),
                transitions: rows,
                deep_classes,
                cited_provenance: Vec::new(),
                cells,
                seats: self.seats.borrow().clone().into_table(),
                notes: vec![Vec::new()],
            }
        }
    }

    /// The prospect-divergence flag itself: a window whose optimistic third join-count term claims a seam the follower's own settled choice then refuses is flagged joint, and the row it was scored against is not.
    #[test]
    fn a_prospect_the_follower_contradicts_flags_its_row_joint() {
        let bench = Bench::new();
        let product = bench.product(
            vec![
                bench.row(
                    ["qsIt", "#EDGE", "qsMay", "C", "#NA", "#NA", "qsIt.x"],
                    1,
                    true,
                ),
                bench.row(
                    ["qsMay", "qsIt.x", "C", "#EDGE", "#NA", "#NA", "qsMay.y"],
                    0,
                    false,
                ),
            ],
            Vec::new(),
        );
        let folded = fold_product(&bench.index, product).expect("the hand-built product folds");
        let flagged: Vec<(&str, bool)> = folded
            .decision
            .transitions
            .iter()
            .map(|row| (&*row.input_glyph, row.joint))
            .collect();
        assert_eq!(flagged, [("qsIt", true), ("qsMay", false)]);
    }

    /// The same window with the prospect the follower confirms is left alone, which is what makes the flag a comparison rather than a constant.
    #[test]
    fn a_prospect_the_follower_confirms_leaves_its_row_alone() {
        let bench = Bench::new();
        let product = bench.product(
            vec![
                bench.row(
                    ["qsIt", "#EDGE", "qsMay", "C", "#NA", "#NA", "qsIt.x"],
                    0,
                    true,
                ),
                bench.row(
                    ["qsMay", "qsIt.x", "C", "#EDGE", "#NA", "#NA", "qsMay.y"],
                    0,
                    false,
                ),
            ],
            Vec::new(),
        );
        let folded = fold_product(&bench.index, product).expect("the hand-built product folds");
        assert!(folded.decision.transitions.iter().all(|row| !row.joint));
    }

    /// A class-grain row expanded to its members: two deep classes at the third slot, each settling its own outcome, compile to one look3 rule per class holding that class's whole member set — which is what the union assertion then licenses conform to read as exact.
    fn deep_bench() -> (Bench, FixpointProduct, [String; 2]) {
        let bench = Bench::new();
        let first = deep_class_id(&["D".to_owned(), "E".to_owned()]);
        let second = deep_class_id(&["F".to_owned(), "G".to_owned()]);
        let product = bench.product(
            vec![
                bench.row(
                    ["qsIt", "#EDGE", "qsMay", "C", &first, "#NA", "qsIt.x"],
                    0,
                    false,
                ),
                bench.row(
                    ["qsIt", "#EDGE", "qsMay", "C", &second, "#NA", "qsIt.y"],
                    0,
                    false,
                ),
                bench.row(
                    ["qsIt", "#EDGE", "qsMay", "#EDGE", "#NA", "#NA", "qsIt.z"],
                    0,
                    false,
                ),
                bench.row(
                    ["qsIt", "#EDGE", "#EDGE", "#NA", "#NA", "#NA", "qsIt.b"],
                    0,
                    false,
                ),
            ]
            .into_iter()
            .chain(bench.edge_kin(["qsIt", "#EDGE", "#EDGE", "#NA", "#NA", "#NA", "qsIt.b"]))
            .chain(bench.edge_kin(["qsIt", "#EDGE", "qsMay", "#EDGE", "#NA", "#NA", "qsIt.z"]))
            .collect(),
            vec![
                (first.clone(), vec!["D".to_owned(), "E".to_owned()]),
                (second.clone(), vec!["F".to_owned(), "G".to_owned()]),
            ],
        );
        (bench, product, [first, second])
    }

    #[test]
    fn a_deep_class_row_compiles_to_a_look3_rule_over_its_whole_member_set() {
        let (bench, product, _tokens) = deep_bench();
        let folded = fold_product(&bench.index, product).expect("the class-grain product folds");
        let deep: Vec<(&str, Vec<&str>)> = folded
            .decision
            .rules
            .iter()
            .filter_map(|rule| {
                rule.look3
                    .as_ref()
                    .map(|look| (&*rule.outcome, look.iter().map(|m| &**m).collect()))
            })
            .collect();
        assert_eq!(
            deep,
            [("qsIt.x", vec!["D", "E"]), ("qsIt.y", vec!["F", "G"])]
        );
    }

    /// The fold-output assertion and its negative control: a look3 class that held only half a deep class's members would make conform's representative-membership test a guess, so a rule spelling one member of a two-member class is refused.
    #[test]
    fn a_rule_that_splits_a_deep_class_is_refused() {
        let (bench, product, tokens) = deep_bench();
        let folded = fold_product(&bench.index, product.clone()).expect("the product folds");
        assert_deep_class_unions(&product, &folded.decision.rules)
            .expect("the emitted classes are whole");
        let mut split = folded.decision.rules.clone();
        split.push(Rule {
            input_glyph: Rc::from("qsIt"),
            backtrack: None,
            look1: None,
            look2: None,
            look3: Some(vec![Rc::from("D")]),
            look4: None,
            outcome: Rc::from("whatever"),
            provenance: Vec::new(),
            joint: false,
        });
        let complaint =
            assert_deep_class_unions(&product, &split).expect_err("half a class is a split");
        assert!(
            complaint.contains("an emitted look3 class splits deep class"),
            "{complaint}"
        );
        assert!(complaint.contains(&tokens[0]), "{complaint}");
    }

    /// The cheap loud check that the two grains still agree, made to fail on purpose: a product counting a cell no row settles into is refused before any artifact is written.
    #[test]
    fn a_product_whose_cells_disagree_with_its_rows_is_refused() {
        let bench = Bench::new();
        let mut product = bench.product(
            vec![bench.row(
                ["qsIt", "#EDGE", "qsMay", "C", "#NA", "#NA", "qsIt.x"],
                0,
                false,
            )],
            Vec::new(),
        );
        product.cells.push(CellId {
            rune: fixtures::sym(&bench.index, "qsTea"),
            stance: fixtures::sym(&bench.index, "full"),
            entry: None,
            exit: None,
            adjustments: Vec::new(),
        });
        let complaint =
            fold_product(&bench.index, product).expect_err("the extra cell is a disagreement");
        assert!(
            complaint.starts_with("the product's reachable cells disagree with the fold rows': [("),
            "{complaint}"
        );
        assert!(complaint.contains("'qsTea', 'full'"), "{complaint}");
    }

    /// The first of the rule fold's refusals: two boundary lefts that settle differently would need two default rule groups, and a second group carrying no backtrack could only shadow the first.
    #[test]
    fn boundary_lefts_that_settle_differently_are_refused() {
        let bench = Bench::new();
        let product = bench.product(
            vec![
                bench.row(
                    ["qsIt", "#EDGE", "qsMay", "#NA", "#NA", "#NA", "qsIt.x"],
                    0,
                    false,
                ),
                bench.row(
                    ["qsIt", "space", "qsMay", "#NA", "#NA", "#NA", "qsIt.y"],
                    0,
                    false,
                ),
            ],
            Vec::new(),
        );
        let complaint = fold_product(&bench.index, product).expect_err("two default blocks");
        assert_eq!(
            complaint,
            "qsIt: boundary left contexts split across outcome blocks: [('#EDGE',), ('space',)]"
        );
    }

    /// The second of them, over a boundary block no row drops its later slots in: the block has no `(r1, #NA, #NA, #NA)` row to sample, so the disagreeing set is empty and the sentence spells it Python's way.
    #[test]
    fn a_boundary_block_with_no_slot_dropped_row_is_refused() {
        let bench = Bench::new();
        let product = bench.product(
            vec![
                bench.row(
                    ["qsIt", "qsMay.x", "#EDGE", "qsTea", "#NA", "#NA", "qsIt.a"],
                    0,
                    false,
                ),
                bench.row(
                    ["qsIt", "qsMay.x", "qsTea", "#NA", "#NA", "#NA", "qsIt.b"],
                    0,
                    false,
                ),
                bench.row(
                    ["qsIt", "#EDGE", "#EDGE", "#NA", "#NA", "#NA", "qsIt.a"],
                    0,
                    false,
                ),
            ],
            Vec::new(),
        );
        let complaint = fold_product(&bench.index, product).expect_err("nothing to sample");
        assert_eq!(complaint, "qsIt: boundary lookaheads disagree: set()");
    }

    /// The order this fold reads its rows in is contract, not preference: an input's rows are one contiguous run and a left's rows one run inside it, so a product whose rows are not key-sorted would fold a left into two blocks. It is refused by the sentence rather than by whichever `expect` the duplicate reached first.
    #[test]
    fn a_product_whose_rows_are_not_key_sorted_is_refused() {
        let bench = Bench::new();
        let mut product = bench.product(
            vec![
                bench.row(
                    ["qsIt", "#EDGE", "qsTea", "#NA", "#NA", "#NA", "qsIt.a"],
                    0,
                    false,
                ),
                bench.row(
                    ["qsIt", "qsMay.x", "qsTea", "#NA", "#NA", "#NA", "qsIt.b"],
                    0,
                    false,
                ),
                bench.row(
                    ["qsIt", "#EDGE", "#EDGE", "#NA", "#NA", "#NA", "qsIt.a"],
                    0,
                    false,
                ),
            ],
            Vec::new(),
        );
        product.transitions.swap(1, 2);
        let complaint = fold_product(&bench.index, product).expect_err("the rows are out of order");
        assert_eq!(
            complaint,
            "the product's rows are not in key order: ('qsIt', '#EDGE', 'qsTea', '#NA', '#NA', '#NA') follows ('qsIt', 'qsMay.x', 'qsTea', '#NA', '#NA', '#NA')"
        );
    }

    /// The one verdict the fold reads off the spec is `is_entry_bearing` on the input's rune, and a name the spec models no rune for has no such verdict: `assemble_tables` raises `KeyError` there, so this refuses rather than answering never-locked and emitting ZWNJ guards nothing asked for. A name the spec interned as something else — a stance, a height — is no more a rune than one it never mentioned.
    #[test]
    fn an_input_glyph_the_spec_models_no_rune_for_is_refused() {
        let bench = Bench::new();
        for (input, rune) in [("qsOoze", "qsOoze"), ("half.a", "half")] {
            let product = bench.product(
                vec![bench.row(
                    [input, "#EDGE", "qsTea", "#NA", "#NA", "#NA", "outcome"],
                    0,
                    false,
                )],
                Vec::new(),
            );
            let complaint =
                fold_product(&bench.index, product).expect_err("the spec models no such rune");
            assert_eq!(
                complaint,
                format!("{input}: the spec models no rune '{rune}'")
            );
        }
    }

    /// The rows a chokepoint arm is stated over: one committed left carrying a two-slot split and a boundary row, and the boundary lefts — ZWNJ among them — sharing one default block whose run edge settles into the input's bare self. That last is what leaves the identity catch-all a rule with work to do: a default block whose slot-dropped row settles into the input is a fallback the dedup drops, so nothing shallower stands between a ZWNJ-backtrack row and the guard, and a guard the whole default block already answered for would be a rule no replayed row first-matches.
    fn chokepoint(bench: &Bench, input: &str) -> FixpointProduct {
        let outcome = |suffix: &str| format!("{input}.{suffix}");
        let mut rows = vec![
            bench.row(
                [
                    input,
                    "qsMay.x",
                    "qsTea",
                    "qsMay",
                    "#NA",
                    "#NA",
                    &outcome("a"),
                ],
                0,
                false,
            ),
            bench.row(
                [
                    input,
                    "qsMay.x",
                    "qsTea",
                    "qsOy",
                    "#NA",
                    "#NA",
                    &outcome("b"),
                ],
                0,
                false,
            ),
            bench.row(
                [
                    input,
                    "qsMay.x",
                    "#EDGE",
                    "#NA",
                    "#NA",
                    "#NA",
                    &outcome("d"),
                ],
                0,
                false,
            ),
        ];
        rows.extend(bench.edge_kin([
            input,
            "qsMay.x",
            "#EDGE",
            "#NA",
            "#NA",
            "#NA",
            &outcome("d"),
        ]));
        rows.extend(bench.boundary_block(input, "qsTea", &outcome("e")));
        bench.product(rows, Vec::new())
    }

    /// The ZWNJ backtrack-slot guards, stated over a rune the fixture genuinely does not entry-bear: an input the chokepoint never locks leads its rules with the default block replayed under an explicit `uni200C` backtrack, and closes that run with the identity catch-all no later backtrack-classed rule may match across.
    #[test]
    fn an_input_the_chokepoint_never_locks_leads_its_rules_with_zwnj_guards() {
        let bench = Bench::new();
        assert!(
            !bench
                .index
                .is_entry_bearing(fixtures::sym(&bench.index, "qsIt")),
            "the fixture stopped being the one this arm needs"
        );
        let folded =
            fold_product(&bench.index, chokepoint(&bench, "qsIt")).expect("the product folds");
        let zwnj: Vec<Rc<str>> = vec![Rc::from("uni200C")];
        let guards = folded
            .decision
            .rules
            .iter()
            .take_while(|rule| rule.backtrack.as_ref() == Some(&zwnj))
            .count();
        assert!(guards > 1, "{guards} guards lead the input's rules");
        assert!(
            folded.decision.rules[guards..]
                .iter()
                .all(|rule| rule.backtrack.as_ref() != Some(&zwnj)),
            "a guard follows a rule it was ordered ahead of"
        );
        let last = &folded.decision.rules[guards - 1];
        assert_eq!(&*last.outcome, "qsIt");
        assert!(last.slots()[1..].iter().all(|slot| slot.is_none()));
        assert_eq!(last.provenance, ["ZWNJ backtrack-slot identity guard"]);
        assert_eq!(folded.decision.identity_guard_rules, 1);
        assert!(
            folded.decision.rules[..guards - 1].iter().all(|rule| rule
                .provenance
                .last()
                .map(String::as_str)
                == Some("ZWNJ backtrack-slot coverage row"))
        );
    }

    /// The other arm, and what makes the first a verdict rather than a constant: the same rows under a rune the chokepoint does lock emit no `uni200C` backtrack at all, because after ZWNJ that input enumerates under its locked twin's own label.
    #[test]
    fn an_input_the_chokepoint_locks_gets_no_zwnj_guards() {
        let bench = Bench::new();
        assert!(
            bench
                .index
                .is_entry_bearing(fixtures::sym(&bench.index, "qsPea")),
            "the fixture stopped being the one this arm needs"
        );
        let locked =
            fold_product(&bench.index, chokepoint(&bench, "qsPea")).expect("the product folds");
        let zwnj: Vec<Rc<str>> = vec![Rc::from("uni200C")];
        assert!(
            locked
                .decision
                .rules
                .iter()
                .all(|rule| rule.backtrack.as_ref() != Some(&zwnj))
        );
        assert_eq!(locked.decision.identity_guard_rules, 0);
        let never_locked =
            fold_product(&bench.index, chokepoint(&bench, "qsIt")).expect("the product folds");
        let guards = never_locked
            .decision
            .rules
            .iter()
            .filter(|rule| rule.backtrack.as_ref() == Some(&zwnj))
            .count();
        assert_eq!(
            locked.decision.rules.len() + guards,
            never_locked.decision.rules.len(),
            "the two arms differ by the guards and nothing else"
        );
    }

    /// Two treaty rows tying on (left, right, junction) come out ordered by the whole row. Nothing on the live alphabet ties — every shipped `treaties-<config>.tsv` has as many distinct triples as rows — but a sort on the triple alone would leave the pair in whatever order the dedupe set was iterated in, which is not an order either fold can reproduce.
    #[test]
    fn treaty_rows_tying_on_the_triple_are_ordered_by_the_whole_row() {
        let bench = Bench::new();
        let extended = bench.adjusted(vec![AdjustmentToken::Extend(Side::Entry, 2)]);
        let contracted = bench.adjusted(vec![AdjustmentToken::Contract(Side::Entry, 3)]);
        let product = bench.product_of(
            vec![
                bench.joined(
                    ["qsIt", "qsMay.x", "qsTea", "qsMay", "#NA", "#NA", "qsIt.a"],
                    extended.clone(),
                    4,
                ),
                bench.joined(
                    ["qsIt", "qsMay.x", "qsTea", "qsOy", "#NA", "#NA", "qsIt.a"],
                    contracted.clone(),
                    4,
                ),
            ]
            .into_iter()
            .chain(bench.boundary_block("qsIt", "qsTea", "qsIt.a"))
            .collect(),
            Vec::new(),
            vec![bench.cell.clone(), extended, contracted],
        );
        let folded = fold_product(&bench.index, product).expect("the tied product folds");
        let rows: Vec<(&str, &str, &str, i64)> = folded
            .treaty
            .rows
            .iter()
            .map(|row| {
                (
                    &*row.left,
                    &*row.right,
                    row.junction.as_str(),
                    row.extension,
                )
            })
            .collect();
        assert_eq!(
            rows,
            [
                ("qsMay.x", "qsIt.a", "baseline", 1),
                ("qsMay.x", "qsIt.a", "baseline", 6)
            ]
        );
    }

    /// `DecisionTable._cells` is a frozenset, so a cell a product happens to count twice is still one cell everywhere the vocabulary is spelled — the windows head and the digest both.
    #[test]
    fn a_cell_counted_twice_is_spelled_once() {
        let bench = Bench::new();
        let mut rows = vec![
            bench.row(
                ["qsIt", "#EDGE", "qsTea", "#NA", "#NA", "#NA", "qsIt.a"],
                0,
                false,
            ),
            bench.row(
                ["qsIt", "#EDGE", "#EDGE", "#NA", "#NA", "#NA", "qsIt.a"],
                0,
                false,
            ),
        ];
        rows.extend(bench.edge_kin(["qsIt", "#EDGE", "#EDGE", "#NA", "#NA", "#NA", "qsIt.a"]));
        let once = fold_product(&bench.index, bench.product(rows.clone(), Vec::new()))
            .expect("the product folds");
        let twice = fold_product(
            &bench.index,
            bench.product_of(
                rows,
                Vec::new(),
                vec![bench.cell.clone(), bench.cell.clone()],
            ),
        )
        .expect("and so does the one counting its cell twice");
        assert_eq!(twice.decision.cells.len(), 2);
        assert_eq!(
            artifacts::table_digest(&bench.index, &once.decision, &once.treaty),
            artifacts::table_digest(&bench.index, &twice.decision, &twice.treaty)
        );
    }
}
