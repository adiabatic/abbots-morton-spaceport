//! The table build's fold, run in the crate on the product the worklist just produced: the class-grain rows expanded to label grain, the prospect-divergence flag pass over that expansion, the per-input rule fold ([`crate::rulefold`]), the treaty fold, and the assertions the build checks its tables against.
//!
//! This is the only implementation of the fold. `rebuild/pipeline/table.py` keeps the data model, the artifact readers and the digests. Three independent checks cover the fold. Byte-identity of the artifacts [`crate::artifacts`] writes against a stamped baseline catches a change in rule order, which is the shipped GSUB order. `rebuild/test_table.py` replays these rules against these rows on the mini fixture with its own first-match-wins implementation. `gate:conform` shapes the compiled font with HarfBuzz on every cycle and compares the result with this crate's per-window settlement.
//!
//! Expansion is where the fold's memory goes, because a class row expands to its full member product at right3 × right4. An expanded row ([`FoldRow`]) therefore holds only the index of its class row, its two deep labels and its joint flag. Everything else about it (the input, the left, the two near slots, the outcome, the settled cells, the prospect and the provenance) is read from the class row through that index, which keeps the fold's working set small beside the enumeration's.
//!
//! Expansion order is `table.Window.key` order, reached without a global sort. The product's rows are already in key order, so rows sharing an (input, left, right1, right2) prefix are contiguous, and sorting each such run by its two deep labels leaves the whole vector in key order. The per-run sort is stable, so rows that tie on the full key keep their class-row order.
//!
//! The replay that checks the outcome partition also records, for each rule, up to [`crate::certificate::ROW_CAP`] of the replayed rows that first-match it, preferring the rows with the shortest producer chains. [`crate::certificate`] closes the chain of one of those rows into a string the rule first-matches at the row's own position. These certificates, one per rule, are written into the windows head beside the rules, and the witness stage settles each one to show that every rule is reachable.

use std::rc::Rc;
use std::time::{Duration, Instant};

use crate::certificate;
use crate::hash::{HashMap, HashSet};
use crate::index::SpecIndex;
use crate::options::WindowOptions;
use crate::rulefold::rules_for_input;
use crate::stream::{
    FixpointProduct, Label, LabelPool, TransitionRow, cell_key, cell_key_repr, key_repr,
    python_repr, python_tuple,
};
use crate::types::{AdjustmentToken, CellId, Settled, Side};

type FoldReporter<'a> = dyn FnMut(&str, Duration) + 'a;

/// The label for a slot the window does not carry, `table.NA_LABEL`.
pub const NA_LABEL: &str = "#NA";

/// The lookahead class every boundary-outcome rule carries, `table.BOUNDARY_LOOKAHEAD_CLASS`. Its order is fixed here and is not sorted.
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

/// One treaty row, `table.TreatyRow`: the two settled cells a seam joins, the height it joins at (or `break`), and the connector pixels the seam carries. `kern` is always zero and is written anyway, because the TSV has the column.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct TreatyRow {
    pub left: Rc<str>,
    pub right: Rc<str>,
    pub junction: String,
    pub extension: i64,
    pub kern: i64,
}

#[derive(Debug)]
/// One configuration's decision table, `table.DecisionTable` as the fold produces it: the class-grain rows with the fold's joint flags, the ordered rules, and the head fields the windows artifact and its readers use.
///
/// The decision table keeps the product's label pool and settled-seat outcome table, through which windows and digests resolve a row's six labels and its outcome. Settled records and provenance lists are needed only while folding, so they are not kept.
pub struct DecisionTable {
    pub config: String,
    pub transitions: Vec<TransitionRow>,
    pub labels: LabelPool,
    pub outcomes: Vec<Label>,
    pub rules: Vec<Rule>,
    pub identity_guard_rules: i64,
    pub cited_provenance: Vec<String>,
    pub deep_classes: Vec<(String, Vec<String>)>,
    pub cells: Vec<CellId>,
    /// One certificate per rule, in rule order: a token stream of rune names and the three boundary glyph labels, which [`crate::certificate`] builds so the rule first-matches at its row's position. The windows head carries them beside the rules.
    pub certificates: Vec<Vec<String>>,
}

impl DecisionTable {
    pub fn outcome(&self, row: &TransitionRow) -> &Rc<str> {
        self.labels.text(self.outcomes[row.settled.index()])
    }
}

/// One configuration's treaty table, `table.TreatyTable`.
#[derive(Debug)]
pub struct TreatyTable {
    pub config: String,
    pub rows: Vec<TreatyRow>,
}

/// What one configuration's fold produced: its two tables, and the lefts the reduced replay covered.
///
/// The lefts are returned so that a caller that perturbs the rules can re-run the replay the build ran and check whether the reduced replay still notices. A whole-table replay would check a different statement.
#[derive(Debug)]
pub struct Folded {
    pub decision: DecisionTable,
    pub treaty: TreatyTable,
    pub replay_lefts: ReplayLefts,
}

/// The lefts a first-match-wins replay has to cover, per input glyph.
pub type ReplayLefts = HashMap<Rc<str>, HashSet<Rc<str>>>;

/// One label-grain row: the index (`seat`) of the class row it expanded from, its two deep labels, and the joint flag the prospect pass sets. Everything else about the row is read from the class row.
pub struct FoldRow {
    pub seat: u32,
    pub right3: Rc<str>,
    pub right4: Rc<str>,
    pub joint: bool,
}

/// The label-grain stream the fold and the rule fold read: expanded rows that reference their product's class rows, label pool, outcomes and provenance. [`rules_for_input`] receives one input's slice.
#[derive(Clone, Copy)]
pub struct LabelRows<'a> {
    product: &'a FixpointProduct,
    fold: &'a [FoldRow],
}

impl<'a> LabelRows<'a> {
    pub fn new(product: &'a FixpointProduct, fold: &'a [FoldRow]) -> Self {
        Self { product, fold }
    }

    /// The expanded rows `start..end`, over the same class rows.
    pub fn slice(&self, start: usize, end: usize) -> Self {
        Self {
            product: self.product,
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
        &self.product.transitions[self.fold[row].seat as usize]
    }

    pub fn input_glyph(&self, row: usize) -> &'a Rc<str> {
        self.product.labels.text(self.base(row).input_glyph)
    }

    pub fn left(&self, row: usize) -> &'a Rc<str> {
        self.product.labels.text(self.base(row).left)
    }

    pub fn right1(&self, row: usize) -> &'a Rc<str> {
        self.product.labels.text(self.base(row).right1)
    }

    pub fn right2(&self, row: usize) -> &'a Rc<str> {
        self.product.labels.text(self.base(row).right2)
    }

    pub fn right3(&self, row: usize) -> &'a Rc<str> {
        &self.fold[row].right3
    }

    pub fn right4(&self, row: usize) -> &'a Rc<str> {
        &self.fold[row].right4
    }

    pub fn outcome(&self, row: usize) -> &'a Rc<str> {
        self.product.outcome(self.base(row))
    }

    pub fn joint(&self, row: usize) -> bool {
        self.fold[row].joint
    }

    pub fn provenance(&self, row: usize) -> &'a [String] {
        &self.product.notes[self.base(row).provenance.index()]
    }

    /// The six labels one row is keyed by, in `table.Window.key` order.
    pub fn key(&self, row: usize) -> [&'a str; 6] {
        let base = self.base(row);
        [
            self.product.labels.text(base.input_glyph),
            self.product.labels.text(base.left),
            self.product.labels.text(base.right1),
            self.product.labels.text(base.right2),
            &self.fold[row].right3,
            &self.fold[row].right4,
        ]
    }
}

/// [`fold_with`] over a fresh [`WindowOptions`], for a caller that has none.
pub fn fold_product(index: &SpecIndex, product: FixpointProduct) -> Result<Folded, String> {
    let mut options = WindowOptions::new(index).map_err(|error| error.to_string())?;
    fold_with(index, product, &mut options)
}

/// One configuration's two tables, folded from the product the worklist produced. The steps run in this order: the key-order check, the expansion and the prospect pass, the rule fold, the reachable-cells cross-check, the treaty fold, the reduced first-match-wins replay, the deep-class union check, and last the certificates. Each check returns an error where its invariant does not hold.
///
/// `options` is the enumeration's own, so the certificates read the formation guard's verdicts from the memo the worklist already filled instead of sweeping them again.
pub fn fold_with(
    index: &SpecIndex,
    product: FixpointProduct,
    options: &mut WindowOptions<'_>,
) -> Result<Folded, String> {
    fold_with_report(index, product, options, None)
}

/// [`fold_with`] with wall-clock reports for the prefix search (`prefixes`) and the outcome partition (`partition`). The phase names carry no configuration suffix, so the caller chooses the grouping and output format. [`fold_with`] reads no clocks.
pub fn fold_with_profile(
    index: &SpecIndex,
    product: FixpointProduct,
    options: &mut WindowOptions<'_>,
    mut report: impl FnMut(&str, Duration),
) -> Result<Folded, String> {
    fold_with_report(index, product, options, Some(&mut report))
}

fn fold_with_report(
    index: &SpecIndex,
    mut product: FixpointProduct,
    options: &mut WindowOptions<'_>,
    mut report: Option<&mut FoldReporter<'_>>,
) -> Result<Folded, String> {
    assert_key_sorted(&product)?;
    let mut fold_rows = expand(&product);
    flag_prospect_joints(&product, &mut fold_rows);
    let mut class_joint: Vec<bool> = product.transitions.iter().map(|row| row.joint).collect();
    for row in &fold_rows {
        if row.joint {
            class_joint[row.seat as usize] = true;
        }
    }
    for (row, joint) in product.transitions.iter_mut().zip(&class_joint) {
        row.joint = *joint;
    }

    let rows = LabelRows::new(&product, &fold_rows);
    let mut rules: Vec<Rule> = Vec::new();
    let mut identity_guards: i64 = 0;
    let mut replay_lefts: ReplayLefts = HashMap::default();
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
    let mut seen: HashSet<(Rc<str>, Rc<str>, String, i64)> = HashSet::default();
    for row in 0..rows.len() {
        let base = rows.base(row);
        let Some(left_settled) = product.left_settled(base) else {
            continue;
        };
        match left_settled.seam {
            None => {
                seen.insert((
                    Rc::clone(rows.left(row)),
                    Rc::clone(rows.outcome(row)),
                    "break".to_owned(),
                    0,
                ));
            }
            Some(seam) => {
                seen.insert((
                    Rc::clone(rows.left(row)),
                    Rc::clone(rows.outcome(row)),
                    index.resolve(seam).to_owned(),
                    left_settled.extension + entry_extensions[&product.settled(base).cell],
                ));
            }
        }
    }
    // Sort on the whole row: two rows tying on (left, right, junction) would otherwise come out in hash-set order.
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

    let started = report.is_some().then(Instant::now);
    let prefixes = certificate::Prefixes::over(&rows);
    if let (Some(report), Some(started)) = (report.as_deref_mut(), started) {
        report("prefixes", started.elapsed());
    }

    let started = report.is_some().then(Instant::now);
    let first_rows = first_match_rows(
        &rows,
        &rules,
        Some(&replay_lefts),
        certificate::ROW_CAP,
        Some(prefixes.dist()),
    )?;
    if let (Some(report), Some(started)) = (report, started) {
        report("partition", started.elapsed());
    }
    assert_deep_class_unions(&product, &rules)?;
    let certificates = certificate::certify(index, options, &prefixes, &rows, &rules, &first_rows)?;

    let config = product.config.clone();
    let decision = DecisionTable {
        config: product.config,
        transitions: product.transitions,
        labels: product.labels,
        outcomes: product.outcomes,
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

/// Checks the precondition the fold and [`expand`] rely on: the product's rows are in `table.Window.key` order, as [`FixpointProduct`] documents. That order makes an input's rows one contiguous run, a left's rows one contiguous run inside it, and the per-prefix expansion sort a global one. A product out of order would fold a left into duplicated blocks, so it is an error.
fn assert_key_sorted(product: &FixpointProduct) -> Result<(), String> {
    for pair in product.transitions.windows(2) {
        if pair[1].key(&product.labels) < pair[0].key(&product.labels) {
            return Err(format!(
                "the product's rows are not in key order: {} follows {}",
                key_repr(pair[1].key(&product.labels)),
                key_repr(pair[0].key(&product.labels))
            ));
        }
    }
    Ok(())
}

/// The label-grain expansion of one product, in `table.Window.key` order. The module doc says why sorting each prefix run is enough. It is public so a caller replaying a perturbed rule list can build the rows the fold checked.
pub fn expand(product: &FixpointProduct) -> Vec<FoldRow> {
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
            let own3 = std::slice::from_ref(product.labels.text(row.right3));
            let own4 = std::slice::from_ref(product.labels.text(row.right4));
            let members3 = members
                .get(&**product.labels.text(row.right3))
                .map_or(own3, Vec::as_slice);
            let members4 = members
                .get(&**product.labels.text(row.right4))
                .map_or(own4, Vec::as_slice);
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
fn near_slots(row: &TransitionRow) -> [Label; 4] {
    [row.input_glyph, row.left, row.right1, row.right2]
}

/// Compares every row's optimistic prospect with the follower's actual settled choice and flags divergent rows joint (design section 6.1 step 4.2).
///
/// The successor index is keyed on the follower's (left, input, right1), which is the row's own (outcome, right1, right2), so the scan skips every window those three slots rule out. The pass reads only the seam each successor settled (through the product's `seats` table), never a successor's joint flag, so the result does not depend on row order and the flags are applied together at the end.
fn flag_prospect_joints(product: &FixpointProduct, fold: &mut [FoldRow]) {
    let class = &product.transitions;
    let mut successors: HashMap<(Label, Label, Label), Vec<u32>> = HashMap::default();
    for (seat, row) in fold.iter().enumerate() {
        let base = &class[row.seat as usize];
        successors
            .entry((base.left, base.input_glyph, base.right1))
            .or_default()
            .push(seat as u32);
    }
    let mut flagged: Vec<u32> = Vec::new();
    for (seat, row) in fold.iter().enumerate() {
        if row.joint {
            continue;
        }
        let base = &class[row.seat as usize];
        if boundaryish(product.labels.text(base.right1))
            || boundaryish(product.labels.text(base.right2))
        {
            continue;
        }
        let Some(candidates) = successors.get(&(
            product.outcomes[base.settled.index()],
            base.right1,
            base.right2,
        )) else {
            continue;
        };
        for &candidate in candidates {
            let successor = &fold[candidate as usize];
            let followed = &class[successor.seat as usize];
            if &*row.right3 != NA_LABEL && *product.labels.text(followed.right2) != row.right3 {
                continue;
            }
            if &*row.right4 != NA_LABEL && successor.right3 != row.right4 {
                continue;
            }
            if i8::from(product.seats[followed.settled.index()].seam.is_some()) != base.prospect {
                flagged.push(seat as u32);
                break;
            }
        }
    }
    for seat in flagged {
        fold[seat as usize].joint = true;
    }
}

/// The half-open range of the expansion that each input glyph occupies. The rows are key-sorted and the input is the key's first component, so each input's rows are one contiguous run and the runs come in sorted input order.
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

/// How far one settled cell's own adjustments move its entry. The treaty fold adds this to the left's extension.
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

/// Checks that the two grains agree: the set of cells the fold rows settle into, read through the product's seat table, equals the product's `cells`.
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

/// The hard build invariant (prototype follow-up 1 in `rebuild/M1-PLAN.md`): replays the reachable rows against the ordered rules under first-match-wins and requires the rules to predict every outcome settlement enumerated.
///
/// `lefts` is the reduction the rule fold returns: one representative of every committed left block plus every member of the boundary block. `None` replays every row, which only a small fixture can afford.
///
/// The same pass records which rule each replayed row first-matches, and a rule that no replayed row reaches is an error just as an outcome mismatch is. Such a rule is dead GSUB, and the replay is where the fold learns which rule won each row. Recording it costs little, because the replay already stops at the first match.
///
/// The reduced replay checks the same statement as the whole-table replay, for two reasons. A committed block's rules carry the whole block in `backtrack`, so every member of the block matches the same slots as the representative and its rows first-match the same rule. Replaying one member therefore decides the block. And every rule reachable only from a boundary left (the default rules, and the ZWNJ backtrack replicas and identity catch-all that [`crate::rulefold`] mints, since `uni200C` is boundaryish) is replayed against every member of the boundary block, which the reduction keeps whole. So a rule that is never first under the reduction is never first over the whole table either. `rebuild/test_table.py`'s `replay` checks both claims on the mini fixture with its own first-match-wins implementation.
///
/// The fold runs this replay under the reduction (through [`first_match_rows`]), so `build-tables` fails on a never-first rule as it folds, and a Python caller sees a `KernelRunError`.
pub fn assert_outcome_partition(
    rows: &LabelRows<'_>,
    rules: &[Rule],
    lefts: Option<&ReplayLefts>,
) -> Result<(), String> {
    first_match_rows(rows, rules, lefts, 1, None).map(|_| ())
}

/// The rules grouped by the input they rewrite, each with its index in the table, in table order.
pub fn rules_by_input(rules: &[Rule]) -> HashMap<&str, Vec<(usize, &Rule)>> {
    let mut by_input: HashMap<&str, Vec<(usize, &Rule)>> = HashMap::default();
    for (seat, rule) in rules.iter().enumerate() {
        by_input
            .entry(&rule.input_glyph)
            .or_default()
            .push((seat, rule));
    }
    by_input
}

/// First-match-wins over one window's six labels: the index of the input's first rule whose five constrained slots all admit the labels at them, or `None` when no rule matches and the input is left unchanged. This is the semantics the emitted lookup compiles to. The certificates and the shipped-order walk use this function. The fold's replay uses `IndexedMatcher`, which the tests check against it.
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

const LINEAR_CLASS_MAX: usize = 8;

struct IndexedRule {
    seat: usize,
    slots: [Option<Box<[u32]>>; 5],
}

struct IndexedMatcher<'a> {
    rows: LabelRows<'a>,
    labels: LabelPool,
    deep_ids: HashMap<(usize, usize), u32>,
    by_input: HashMap<u32, Vec<IndexedRule>>,
}

impl<'a> IndexedMatcher<'a> {
    /// Compiles the ordered rules [`first_match`] reads into sorted classes of product-local label IDs. The cloned pool keeps every existing row ID, and rule members and deep labels that the pool lacks are interned after them. `deep_ids` caches one entry per deep-label allocation, keyed by address, not one per row. The held row view keeps those allocations alive, so an address cannot be reused while the matcher exists. Equal labels in distinct allocations intern to the same ID. The IDs are used only to test membership, so the order they are minted in affects no result.
    fn new(rows: &LabelRows<'a>, rules: &[Rule]) -> Self {
        let mut labels = rows.product.labels.clone();
        let mut by_input: HashMap<u32, Vec<IndexedRule>> = HashMap::default();
        for (seat, rule) in rules.iter().enumerate() {
            let input = labels.intern(&rule.input_glyph).0;
            let slots = rule.slots().map(|slot| {
                slot.as_ref().map(|members| {
                    let mut ids: Vec<u32> = members
                        .iter()
                        .map(|member| labels.intern(member).0)
                        .collect();
                    ids.sort_unstable();
                    ids.dedup();
                    ids.into_boxed_slice()
                })
            });
            by_input
                .entry(input)
                .or_default()
                .push(IndexedRule { seat, slots });
        }
        Self {
            rows: *rows,
            labels,
            deep_ids: HashMap::default(),
            by_input,
        }
    }

    fn first_match(&mut self, row: usize) -> Option<usize> {
        let rows = self.rows;
        let base = rows.base(row);
        let deep3 = self.deep_id(rows.right3(row));
        let deep4 = self.deep_id(rows.right4(row));
        let slots = [base.left.0, base.right1.0, base.right2.0, deep3, deep4];
        for rule in self
            .by_input
            .get(&base.input_glyph.0)
            .map_or(&[][..], Vec::as_slice)
        {
            if rule
                .slots
                .iter()
                .zip(slots)
                .any(|(class, label)| class.as_deref().is_some_and(|class| !admits(class, label)))
            {
                continue;
            }
            return Some(rule.seat);
        }
        None
    }

    fn deep_id(&mut self, text: &Rc<str>) -> u32 {
        let pointer = label_pointer(text);
        if let Some(&found) = self.deep_ids.get(&pointer) {
            return found;
        }
        let id = self.labels.intern(text).0;
        self.deep_ids.insert(pointer, id);
        id
    }
}

fn label_pointer(text: &Rc<str>) -> (usize, usize) {
    (text.as_ptr() as usize, text.len())
}

fn admits(class: &[u32], label: u32) -> bool {
    if class.len() <= LINEAR_CLASS_MAX {
        class.contains(&label)
    } else {
        class.binary_search(&label).is_ok()
    }
}

#[cfg(test)]
fn first_match_rows_reference(
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

/// [`assert_outcome_partition`]'s replay, returning for every rule up to `keep` of the replayed rows that first-match it, as indices into `rows`. When `dist` ranks the rows ([`crate::certificate::Prefixes`]), these are the rows with the shortest producer chains, an unreached row ranking last. Otherwise they are the first rows in replay order. The errors are the assertion's: an outcome mismatch, or a rule no replayed row first-matches. The `IndexedMatcher` is built and dropped inside this call, so its build time and memory count toward the `partition` phase.
pub fn first_match_rows(
    rows: &LabelRows<'_>,
    rules: &[Rule],
    lefts: Option<&ReplayLefts>,
    keep: usize,
    dist: Option<&[u32]>,
) -> Result<Vec<Vec<usize>>, String> {
    let mut matcher = IndexedMatcher::new(rows, rules);
    let mut failures: Vec<String> = Vec::new();
    let mut count = 0usize;
    let mut first_rows: Vec<Vec<usize>> = vec![Vec::new(); rules.len()];
    let mut ranks: Vec<Vec<u32>> = vec![Vec::new(); rules.len()];
    for row in 0..rows.len() {
        let input = rows.input_glyph(row);
        let left = rows.left(row);
        if let Some(lefts) = lefts
            && !lefts
                .get(&**input)
                .is_some_and(|covered| covered.contains(&**left))
        {
            continue;
        }
        let mut predicted: &str = input;
        if let Some(seat) = matcher.first_match(row) {
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
                    key_repr(rows.key(row))
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

/// One rule as an error message names it: the input it rewrites, its five slots in replay order with `any` for an unconstrained one, the outcome it would write, and the first authored pointer that produced it.
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

/// Checks that every emitted look3/look4 class, among the rules that match a class row's near slots, contains that row's deep class either whole or not at all. This makes conform's rule-membership tests, which test one representative member per deep class, exact.
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
    let mut by_input: HashMap<&str, Vec<&Rule>> = HashMap::default();
    for rule in rules {
        by_input.entry(&rule.input_glyph).or_default().push(rule);
    }
    for row in &product.transitions {
        let set3 = members.get(&**product.labels.text(row.right3));
        let set4 = members.get(&**product.labels.text(row.right4));
        if set3.is_none() && set4.is_none() {
            continue;
        }
        for rule in by_input
            .get(&**product.labels.text(row.input_glyph))
            .map_or(&[][..], Vec::as_slice)
        {
            if !matches_slot(&rule.backtrack, product.labels.text(row.left))
                || !matches_slot(&rule.look1, product.labels.text(row.right1))
                || !matches_slot(&rule.look2, product.labels.text(row.right2))
            {
                continue;
            }
            if let (Some(set3), Some(look3)) = (set3, &rule.look3) {
                let inside = intersection(set3, look3);
                if !inside.is_empty() && inside.len() != set3.len() {
                    return Err(split_class(
                        row,
                        &product.labels,
                        product.labels.text(row.right3),
                        "look3",
                        &inside,
                        set3,
                    ));
                }
            }
            if let (Some(set4), Some(look4)) = (set4, &rule.look4) {
                let reaches = match &rule.look3 {
                    None => true,
                    Some(look3) => match set3 {
                        Some(set3) => !intersection(set3, look3).is_empty(),
                        None => look3
                            .iter()
                            .any(|member| **member == **product.labels.text(row.right3)),
                    },
                };
                if reaches {
                    let inside = intersection(set4, look4);
                    if !inside.is_empty() && inside.len() != set4.len() {
                        return Err(split_class(
                            row,
                            &product.labels,
                            product.labels.text(row.right4),
                            "look4",
                            &inside,
                            set4,
                        ));
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
    labels: &LabelPool,
    token: &str,
    slot: &str,
    inside: &[&str],
    whole: &HashSet<&str>,
) -> String {
    let mut all: Vec<&str> = whole.iter().copied().collect();
    all.sort_unstable();
    format!(
        "{}: an emitted {slot} class splits deep class {token} at {}: {} of {}",
        labels.text(row.input_glyph),
        key_repr(row.key(labels)),
        python_str_list(inside),
        python_str_list(&all)
    )
}

/// A list of strings in Python's repr, the form error messages use for a member set.
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
    use crate::types::{NotesSeat, SettledSeat};
    use std::cell::RefCell;

    /// The shipping modes, which the fixture is folded in.
    const SHIPPING: EnumerationModes = EnumerationModes {
        simulated_prospect: true,
        vote_slots: true,
        deep_classes: true,
    };

    /// The mini fixture's fixpoint and the tables it folds into.
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

    /// The ordered rules predict every row under the whole-table replay, which the fixture is small enough to afford, and under the reduced replay the build runs.
    #[test]
    fn every_enumerated_row_is_what_the_ordered_rules_predict() {
        let (_index, product, folded) = built();
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product, &fold_rows);
        assert_outcome_partition(&rows, &folded.decision.rules, None)
            .expect("first-match-wins over the whole table");
        assert_outcome_partition(&rows, &folded.decision.rules, Some(&folded.replay_lefts))
            .expect("and over the reduction the build replays");
    }

    #[test]
    fn production_proof_preserves_reference_prefixes_matches_rows_failures_and_certificates() {
        let (index, product, folded) = built();
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product, &fold_rows);
        let rules = &folded.decision.rules;
        let by_input = rules_by_input(rules);
        let mut indexed = IndexedMatcher::new(&rows, rules);
        for row in 0..rows.len() {
            assert_eq!(
                first_match(&by_input, rows.key(row)),
                indexed.first_match(row),
                "{}",
                key_repr(rows.key(row))
            );
        }

        let reference_prefixes = certificate::Prefixes::over_reference(&rows);
        let prefixes = certificate::Prefixes::over(&rows);
        certificate::assert_same_prefixes(&reference_prefixes, &prefixes);
        let reference_rows = first_match_rows_reference(
            &rows,
            rules,
            Some(&folded.replay_lefts),
            certificate::ROW_CAP,
            Some(reference_prefixes.dist()),
        )
        .expect("the reference replay accepts the folded rules");
        let production_rows = first_match_rows(
            &rows,
            rules,
            Some(&folded.replay_lefts),
            certificate::ROW_CAP,
            Some(prefixes.dist()),
        )
        .expect("the production replay accepts the folded rules");
        assert_eq!(reference_rows, production_rows);
        let mut options = WindowOptions::new(&index).expect("the fixture has valid options");
        let reference_certificates = certificate::certify(
            &index,
            &mut options,
            &reference_prefixes,
            &rows,
            rules,
            &reference_rows,
        )
        .expect("the reference proof certifies the folded rules");
        assert_eq!(reference_certificates, folded.decision.certificates);

        let mut cases: Vec<Vec<Rule>> = vec![rules.clone()];
        let mut shadowed = rules.clone();
        shadowed.push(rules.last().expect("the fixture folds rules").clone());
        cases.push(shadowed);
        let mut outcome_mismatch = rules.clone();
        outcome_mismatch[0].outcome = Rc::from("qsWrong.outcome");
        cases.push(outcome_mismatch);
        let mut omitted = rules.clone();
        omitted.remove(0);
        cases.push(omitted);
        let mut reordered = rules.clone();
        reordered.swap(0, 1);
        cases.push(reordered);

        for (case, rules) in cases.iter().enumerate() {
            for lefts in [None, Some(&folded.replay_lefts)] {
                let reference = first_match_rows_reference(
                    &rows,
                    rules,
                    lefts,
                    certificate::ROW_CAP,
                    Some(prefixes.dist()),
                );
                let production = first_match_rows(
                    &rows,
                    rules,
                    lefts,
                    certificate::ROW_CAP,
                    Some(prefixes.dist()),
                );
                assert_eq!(
                    reference,
                    production,
                    "case {case}, reduced={}",
                    lefts.is_some()
                );
            }
        }
    }

    /// A rule no row reaches is dead GSUB, so the replay fails on it. A backtrack naming a left the fixture never enumerates matches nothing, so every prediction is unchanged and only the reachability check fails.
    #[test]
    fn a_rule_no_replayed_row_first_matches_is_refused() {
        let (_index, product, folded) = built();
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product, &fold_rows);
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

    /// A duplicate of the last rule matches the same rows as the original, which precedes it, so first-match-wins never reaches the duplicate and every prediction is unchanged.
    #[test]
    fn a_shadowed_duplicate_is_refused() {
        let (_index, product, folded) = built();
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product, &fold_rows);
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

    /// A negative control for the reduction: every single-rule drop, adjacent swap and widened first-lookahead class that the whole-table replay catches, the reduced replay catches too. A perturbation that neither catches touches a redundant rule, which says something about the fold and nothing about the reduction.
    #[test]
    fn the_reduced_replay_catches_what_the_whole_table_replay_catches() {
        let (_index, product, folded) = built();
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product, &fold_rows);
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
                .filter(|row| folded.decision.labels.text(row.input_glyph) == input)
                .map(|row| &**folded.decision.labels.text(row.left))
                .collect();
            lefts.len() < all.len()
        });
        assert!(
            reduced,
            "the fixture stopped reducing, so the control proves nothing"
        );
    }

    /// The rule-ordering convention that `rebuild/test_table.py`'s `test_boundary_rows_lead_their_groups` also checks: within one (input, backtrack) group, the boundary-outcome rule with `uni200C` explicit in its class precedes every letter-lookahead rule, and the slot-dropped fallback comes last.
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

    /// The prospect pass never clears a joint flag the fixpoint set. The four-family fixture reaches no divergent window of its own, so this is all it can check. [`tests::a_prospect_the_follower_contradicts_flags_its_row_joint`] checks the flag itself over a hand-built product.
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

    /// The treaty rows are sorted and distinct, at least one break row has extension 0, and `kern` is always 0.
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

    /// Two folds of one product write the same bytes, and dropping a rule or a row changes the table digest.
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

    /// Builds products by hand for what the four-family fixture is too small to reach: a divergent prospect, a deep-class row, inputs with and without ZWNJ backtrack guards, and the fold's errors.
    ///
    /// **Every input glyph names a rune the fixture models**, because the fold reads `is_entry_bearing` off that rune and returns an error for a name the spec models no rune for. `qsIt` is not entry-bearing, so an input under it gets ZWNJ backtrack guards and one under `qsPea` does not. The other slots' labels are synthetic, because nothing resolves them. Every row settles into the bench's one cell unless it is given another, so the reachable-cells cross-check passes unless a test breaks it.
    struct Bench {
        index: SpecIndex,
        cell: CellId,
        seam: crate::model::Sym,
        seats: RefCell<Vec<Settled>>,
        labels: RefCell<LabelPool>,
        outcomes: RefCell<Vec<Label>>,
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
                seats: RefCell::new(Vec::new()),
                labels: RefCell::new(LabelPool::default()),
                outcomes: RefCell::new(Vec::new()),
            }
        }

        /// Appends a settled record and its outcome to the bench's tables, which every product the bench builds carries, and returns its index.
        fn seat(&self, settled: Settled, outcome: &str) -> SettledSeat {
            let mut seats = self.seats.borrow_mut();
            let seat = SettledSeat::at(seats.len());
            seats.push(settled);
            self.outcomes
                .borrow_mut()
                .push(self.labels.borrow_mut().intern(outcome));
            seat
        }

        /// One row from its six key labels and its outcome label, the prospect its trace claimed, and whether it committed a seam.
        fn row(&self, labels: [&str; 7], prospect: i8, joins: bool) -> TransitionRow {
            let [input_glyph, left, right1, right2, right3, right4, _] = {
                let mut pool = self.labels.borrow_mut();
                labels.map(|text| pool.intern(text))
            };
            TransitionRow {
                input_glyph,
                left,
                right1,
                right2,
                right3,
                right4,
                settled: self.seat(
                    Settled {
                        cell: self.cell.clone(),
                        seam: joins.then_some(self.seam),
                        extension: 0,
                    },
                    labels[6],
                ),
                left_settled: None,
                provenance: NotesSeat::at(0),
                prospect,
                joint: false,
            }
        }

        /// The rows a fixpoint enumerates beside a row whose lookahead reaches the end of the buffer: the same window with each boundary glyph in that slot, settling the same way. No GSUB lookup can see `#EDGE`, so the fold writes the boundary rule over [`BOUNDARY_LOOKAHEAD_CLASS`]. A hand-built product with only the `#EDGE` row would leave that rule unreached, and the fold would return an error for it.
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

        /// The default block a fixpoint produces beside the committed blocks: every boundary left, ZWNJ included, with the same near lookahead and the same end-of-run row, which settles to the bare input. A hand-built product needs the whole block, not only a `#EDGE` left, because the fold writes these rules over the boundary glyphs and replicates them under a `uni200C` backtrack, and it returns an error for any of them that no replayed row reaches.
        fn boundary_block(&self, input: &str, near: &str, outcome: &str) -> Vec<TransitionRow> {
            let mut rows = Vec::new();
            for left in ["#EDGE", "space", "periodcentered", "uni200C"] {
                rows.push(self.row([input, left, near, "#NA", "#NA", "#NA", outcome], 0, false));
                rows.push(self.row([input, left, "#EDGE", "#NA", "#NA", "#NA", input], 0, false));
                rows.extend(self.edge_kin([input, left, "#EDGE", "#NA", "#NA", "#NA", input]));
            }
            rows
        }

        /// The bench cell with the given adjustments, so two rows under one left can have different entry extensions and produce two treaty rows that tie on (left, right, junction).
        fn adjusted(&self, adjustments: Vec<AdjustmentToken>) -> CellId {
            CellId {
                adjustments,
                ..self.cell.clone()
            }
        }

        /// One row whose left committed a seam. The treaty fold skips rows without `left_settled`, and [`Bench::row`] leaves it absent, so a product of those rows folds into no treaty rows.
        fn joined(&self, labels: [&str; 7], cell: CellId, left_extension: i64) -> TransitionRow {
            let mut row = self.row(labels, 0, false);
            row.settled = self.seat(
                Settled {
                    cell,
                    seam: None,
                    extension: 0,
                },
                labels[6],
            );
            row.left_settled = Some(self.seat(
                Settled {
                    cell: self.cell.clone(),
                    seam: Some(self.seam),
                    extension: left_extension,
                },
                labels[1],
            ));
            row
        }

        /// The rows as a product whose only cell is the bench cell, sorted into key order. No bench row has provenance, so the provenance table holds only the empty list.
        fn product(
            &self,
            rows: Vec<TransitionRow>,
            deep_classes: Vec<(String, Vec<String>)>,
        ) -> FixpointProduct {
            self.product_of(rows, deep_classes, vec![self.cell.clone()])
        }

        /// The same with an explicit cell list, for rows that settle into other cells. The reachable-cells cross-check compares the rows against this list, so it must include every cell a row uses.
        fn product_of(
            &self,
            mut rows: Vec<TransitionRow>,
            deep_classes: Vec<(String, Vec<String>)>,
            cells: Vec<CellId>,
        ) -> FixpointProduct {
            let labels = self.labels.borrow().clone();
            rows.sort_by(|left, right| left.key(&labels).cmp(&right.key(&labels)));
            FixpointProduct {
                config: "default".to_owned(),
                transitions: rows,
                deep_classes,
                cited_provenance: Vec::new(),
                cells,
                seats: self.seats.borrow().clone(),
                labels,
                outcomes: self.outcomes.borrow().clone(),
                notes: vec![Vec::new()],
            }
        }
    }

    /// A row whose optimistic prospect claims a seam that the follower's settled choice does not make is flagged joint, and the follower's row is not.
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
            .map(|row| (&**folded.decision.labels.text(row.input_glyph), row.joint))
            .collect();
        assert_eq!(flagged, [("qsIt", true), ("qsMay", false)]);
    }

    /// The same window with a prospect the follower's settled choice confirms is not flagged.
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

    /// A product with two deep classes at the third slot, each settling to its own outcome, plus the boundary rows the fold needs. Each class should compile to one look3 rule holding its whole member set. Returns the bench, the product, and the two class tokens.
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
    fn indexed_membership_preserves_overlap_guards_identity_and_distinct_deep_allocations() {
        let bench = Bench::new();
        let third = deep_class_id(&["D".to_owned(), "E".to_owned()]);
        let fourth = deep_class_id(&["F".to_owned(), "G".to_owned()]);
        let product = bench.product(
            vec![
                bench.row(
                    ["qsIt", "#EDGE", "qsMay", "C", &third, &fourth, "same"],
                    0,
                    false,
                ),
                bench.row(
                    ["qsIt", "uni200C", "qsMay", "C", &third, &fourth, "qsIt"],
                    0,
                    false,
                ),
                bench.row(
                    ["qsIt", "#EDGE", "qsTea", "Direct", "D", "F", "direct"],
                    0,
                    false,
                ),
            ],
            vec![
                (third, vec!["D".to_owned(), "E".to_owned()]),
                (fourth, vec!["F".to_owned(), "G".to_owned()]),
            ],
        );
        let fold_rows = expand(&product);
        let rows = LabelRows::new(&product, &fold_rows);
        let wide_edge: Vec<Rc<str>> = [
            "#EDGE", "edge-a", "edge-b", "edge-c", "edge-d", "edge-e", "edge-f", "edge-g",
            "edge-h", "edge-i",
        ]
        .into_iter()
        .map(Rc::from)
        .collect();
        let rules = vec![
            Rule {
                input_glyph: Rc::from("qsIt"),
                backtrack: Some(wide_edge.clone()),
                look1: Some(vec![Rc::from("qsMay")]),
                look2: Some(vec![Rc::from("C")]),
                look3: Some(vec![Rc::from("D")]),
                look4: Some(vec![Rc::from("F"), Rc::from("G")]),
                outcome: Rc::from("same"),
                provenance: vec!["partial overlap first".to_owned()],
                joint: false,
            },
            Rule {
                input_glyph: Rc::from("qsIt"),
                backtrack: Some(wide_edge.clone()),
                look1: Some(vec![Rc::from("qsMay")]),
                look2: Some(vec![Rc::from("C")]),
                look3: Some(vec![Rc::from("D"), Rc::from("E")]),
                look4: Some(vec![Rc::from("G")]),
                outcome: Rc::from("same"),
                provenance: vec!["same outcome, distinct rule".to_owned()],
                joint: false,
            },
            Rule {
                input_glyph: Rc::from("qsIt"),
                backtrack: Some(vec![Rc::from("uni200C")]),
                look1: Some(vec![Rc::from("qsMay")]),
                look2: Some(vec![Rc::from("C")]),
                look3: Some(vec![Rc::from("D"), Rc::from("E")]),
                look4: Some(vec![Rc::from("F"), Rc::from("G")]),
                outcome: Rc::from("qsIt"),
                provenance: vec!["ZWNJ guard".to_owned()],
                joint: false,
            },
            Rule {
                input_glyph: Rc::from("qsIt"),
                backtrack: Some(wide_edge),
                look1: Some(vec![Rc::from("qsTea")]),
                look2: Some(
                    [
                        "Direct", "near-a", "near-b", "near-c", "near-d", "near-e", "near-f",
                        "near-g", "near-h", "near-i",
                    ]
                    .into_iter()
                    .map(Rc::from)
                    .collect(),
                ),
                look3: Some(vec![Rc::from("D")]),
                look4: Some(vec![Rc::from("F")]),
                outcome: Rc::from("direct"),
                provenance: vec!["direct deep labels".to_owned()],
                joint: false,
            },
        ];
        let by_input = rules_by_input(&rules);
        let mut indexed = IndexedMatcher::new(&rows, &rules);
        let mut d_allocations: HashSet<(usize, usize)> = HashSet::default();
        let mut saw_identity = false;
        for row in 0..rows.len() {
            let key = rows.key(row);
            if key[4] == "D" {
                d_allocations.insert(label_pointer(rows.right3(row)));
            }
            let reference = first_match(&by_input, key);
            assert_eq!(reference, indexed.first_match(row), "{}", key_repr(key));
            let expected = if key[1] == "uni200C" {
                Some(2)
            } else if key[2] == "qsTea" {
                Some(3)
            } else if key[4] == "D" {
                Some(0)
            } else if key[5] == "G" {
                Some(1)
            } else {
                saw_identity = true;
                None
            };
            assert_eq!(reference, expected, "{}", key_repr(key));
        }
        assert!(
            saw_identity,
            "the fixture stopped exercising input fallback"
        );
        assert!(
            d_allocations.len() > 1,
            "the concrete deep member and direct label share one allocation"
        );
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

    /// The deep-class union check passes on the folded rules and fails on an added rule whose look3 class holds one member of a two-member deep class. Such a class would make conform's one-member membership test unreliable.
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

    /// The reachable-cells cross-check fails when the product lists a cell that no row settles into.
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

    /// The rule fold's first error: two boundary lefts that settle differently would need two default rule groups, and with no backtrack to tell them apart the second group could never match.
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

    /// The rule fold's second error, for a boundary block with no slot-dropped row: there is no `(r1, #NA, #NA, #NA)` row to sample, so the disagreeing set is empty and the message writes it as Python's `set()`.
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

    /// The fold relies on key order: an input's rows form one contiguous run and a left's rows one run inside it, so a product whose rows are not key-sorted would fold a left into two blocks. The key-order check returns an error message instead of a panic at whichever `expect` the duplicate reaches first.
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

    /// The fold reads `is_entry_bearing` off the input's rune, and a name the spec models no rune for has no such rune. The fold returns an error instead of treating the input as never locked and emitting ZWNJ guards for it. A name the spec interned as something other than a rune, such as a stance, gets the same error as an unknown name.
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

    /// The rows the two chokepoint tests fold: one committed left with a split on the second slot and an end-of-run row, and a default block of boundary lefts, ZWNJ included, whose end-of-run row settles to the bare input. That last row gives the identity guard a row to match. A slot-dropped row that settles to the input is an identity fallback the fold omits, so no shallower rule stands between a ZWNJ-backtrack row and the identity guard. If another rule already matched that row, the guard would be a rule no replayed row first-matches.
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

    /// An input the chokepoint never locks (`qsIt`, which is not entry-bearing) starts its rules with the default block replicated under an explicit `uni200C` backtrack, and ends that run with the identity guard, so no later rule with a backtrack class can match across a ZWNJ.
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

    /// The same rows under a rune the chokepoint locks (`qsPea`) emit no `uni200C` backtrack, because after a ZWNJ that input enumerates under its locked twin's label. Its rule list is shorter than `qsIt`'s by the number of guards.
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

    /// Two treaty rows that tie on (left, right, junction) are ordered by the whole row. No live treaty table has such a tie (every `treaties-<config>.tsv` has as many distinct triples as rows), but a sort on the triple alone would leave a tied pair in hash-set order.
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

    /// A cell the product lists twice counts once in the digest, as in Python's `DecisionTable._cells` frozenset. The windows head deduplicates cells the same way (`artifacts::sorted_cells`).
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
