//! The table build's fold, run in the crate on the product the worklist just produced: the class-grain rows expanded to label grain, the prospect-divergence flag pass over that expansion, the per-input rule fold ([`crate::rulefold`]), the join fold, and the assertions the build checks its tables against.
//!
//! This is the only implementation of the fold. `rebuild/pipeline/table.py` keeps the data model, the artifact readers and the digests. Three independent checks cover the fold. A refactor of it is checked by byte identity of the artifacts [`crate::artifacts`] writes against a build from before the change, which catches a change in rule order, the shipped GSUB order. `rebuild/test_table.py` replays these rules against these rows on the mini fixture with its own first-match-wins implementation. `gate:conform` shapes the compiled font with HarfBuzz on every cycle and compares the result with this crate's per-window settlement.
//!
//! Expansion is where the fold's memory goes, because a class row expands to its full member product at right3 × right4. An expanded row ([`FoldRow`]) therefore holds only the index of its class row, its two deep labels as ids in the product's label pool, and its joint flag. Everything else about it (the input, the left, the two near slots, the outcome, the settled cells, the prospect and the provenance) is read from the class row through that index, which keeps the fold's working set small beside the enumeration's.
//!
//! Expansion order is `table.Window.key` order, reached without a global sort. The product's rows are already in key order, so rows sharing an (input, left, right1, right2) prefix are contiguous, and sorting each such run by its two deep labels leaves the whole vector in key order. The per-run sort is stable, so rows that tie on the full key keep their class-row order.
//!
//! A build of several configurations runs the same steps through [`Prepared`]: the expansion, the prospect pass and the row chains once, then the rule fold, again for each input that takes in windows another configuration keeps live ([`crate::crossconfig`]), then the checks and the certificates over the configuration's own rows and the imported ones ([`Prepared::finish`]). An imported row reaches [`crate::rulefold`] through a [`MergedRows`] view beside the own rows; the prospect pass, the join fold, the reachable-cells check, the row chains, the windows and the digest read only the own rows.
//!
//! The replay that checks the outcome partition also records, for each rule, up to [`crate::certificate::ROW_CAP`] of the replayed rows that first-match it, preferring the rows with the shortest row chains. [`crate::certificate`] completes the chain of one of those rows into a string the rule first-matches at the row's own position. These certificates, one per rule, are written into the windows head beside the rules, and the witness stage settles each one to show that every rule is reachable.

use std::ops::Range;
use std::rc::Rc;
use std::time::{Duration, Instant};

use crate::certificate;
use crate::crossconfig::{ImportedRow, Imports, Spellings};
use crate::hash::{HashMap, HashSet};
use crate::index::SpecIndex;
use crate::options::WindowOptions;
use crate::rulefold::{RuleFold, rules_for_input};
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

/// The first right slot (2 to 5 in key order) a key leaves open after a letter, where `#NA` stands for every label; `None` when the key carries every slot up to a boundary or its end. A `#NA` after a boundary stands for nothing, since no window reads past a boundary.
pub fn open_slot(key: &[&str; 6]) -> Option<usize> {
    (2..=5).find(|&slot| key[slot] == NA_LABEL && (slot == 2 || !boundaryish(key[slot - 1])))
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

/// One join row, `table.JoinRow`: the two settled cells a junction joins, the height it joins at (or `break`), and the connector pixels the junction carries. `kern` is always zero and is written anyway, because the TSV has the column.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct JoinRow {
    pub left: Rc<str>,
    pub right: Rc<str>,
    pub junction: String,
    pub extension: i64,
    pub kern: i64,
}

#[derive(Debug)]
/// One configuration's decision table, `table.DecisionTable` as the fold produces it: the class-grain rows with the fold's joint flags, the ordered rules, and the head fields the windows artifact and its readers use.
///
/// The decision table keeps the product's label pool and settled-id outcome table, through which windows and digests resolve a row's six labels and its outcome. Settled records and provenance lists are needed only while folding, so they are not kept.
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
    /// The windows the table imported from other configurations ([`crate::crossconfig`]), in key order, each as its six labels, its outcome, and the token of the configuration it is live in. The windows head carries them.
    pub imports: Vec<[String; 8]>,
    /// The build's configurations in [`Spellings::rank`] order and each rule's [`crate::crossconfig::bucket`] in rule order: where the exchange expects the emitter to ship the rules, which the windows head carries for `emit_gsub._fold_rules` to check. Both are empty for a fold outside a table build.
    pub fold_order: Vec<String>,
    pub buckets: Vec<u8>,
}

impl DecisionTable {
    pub fn outcome(&self, row: &TransitionRow) -> &Rc<str> {
        self.labels.text(self.outcomes[row.settled.index()])
    }
}

/// One configuration's join table, `table.JoinTable`.
#[derive(Debug)]
pub struct JoinTable {
    pub config: String,
    pub rows: Vec<JoinRow>,
}

/// What one configuration's fold produced: its two tables, and the lefts the reduced replay covered.
///
/// The lefts are returned so that a caller that perturbs the rules can re-run the replay the build ran and check whether the reduced replay still notices. A whole-table replay would check a different statement.
#[derive(Debug)]
pub struct Folded {
    pub decision: DecisionTable,
    pub joins: JoinTable,
    pub replay_lefts: ReplayLefts,
}

/// The lefts a first-match-wins replay has to cover, per input glyph.
pub type ReplayLefts = HashMap<Rc<str>, HashSet<Rc<str>>>;

/// One label-grain row: the index (`class_row`) of the class row it expanded from, its two deep labels as ids in the product's label pool, and the joint flag the prospect pass sets. Everything else about the row is read from the class row. A configuration's expansion runs to tens of millions of rows and stays parked through the cross-configuration exchange, so the row holds pool ids, not shared strings, in sixteen bytes.
pub struct FoldRow {
    pub class_row: u32,
    pub right3: Label,
    pub right4: Label,
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
        &self.product.transitions[self.fold[row].class_row as usize]
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
        self.product.labels.text(self.fold[row].right3)
    }

    pub fn right4(&self, row: usize) -> &'a Rc<str> {
        self.product.labels.text(self.fold[row].right4)
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
            self.product.labels.text(self.fold[row].right3),
            self.product.labels.text(self.fold[row].right4),
        ]
    }

    /// The product the rows expand.
    pub(crate) fn product(&self) -> &'a FixpointProduct {
        self.product
    }

    /// The index of the class row one expanded row came from.
    pub(crate) fn class_row(&self, row: usize) -> u32 {
        self.fold[row].class_row
    }
}

/// What [`crate::rulefold`] reads of one input's rows: a view in `table.Window.key` order that gives each row's labels, outcome, joint flag and provenance. [`LabelRows`] is a configuration's own rows; [`MergedRows`] adds the rows another configuration keeps live that this one imports ([`crate::crossconfig`]).
pub trait RowSource: Copy {
    fn len(&self) -> usize;
    fn is_empty(&self) -> bool {
        self.len() == 0
    }
    fn left(&self, row: usize) -> &Rc<str>;
    fn right1(&self, row: usize) -> &Rc<str>;
    fn right2(&self, row: usize) -> &Rc<str>;
    fn right3(&self, row: usize) -> &Rc<str>;
    fn right4(&self, row: usize) -> &Rc<str>;
    fn outcome(&self, row: usize) -> &Rc<str>;
    fn joint(&self, row: usize) -> bool;
    fn provenance(&self, row: usize) -> &[String];
    /// Whether the row is one of this configuration's own rows rather than an imported one.
    fn own(&self, row: usize) -> bool;
}

impl RowSource for LabelRows<'_> {
    fn len(&self) -> usize {
        LabelRows::len(self)
    }
    fn left(&self, row: usize) -> &Rc<str> {
        LabelRows::left(self, row)
    }
    fn right1(&self, row: usize) -> &Rc<str> {
        LabelRows::right1(self, row)
    }
    fn right2(&self, row: usize) -> &Rc<str> {
        LabelRows::right2(self, row)
    }
    fn right3(&self, row: usize) -> &Rc<str> {
        LabelRows::right3(self, row)
    }
    fn right4(&self, row: usize) -> &Rc<str> {
        LabelRows::right4(self, row)
    }
    fn outcome(&self, row: usize) -> &Rc<str> {
        LabelRows::outcome(self, row)
    }
    fn joint(&self, row: usize) -> bool {
        LabelRows::joint(self, row)
    }
    fn provenance(&self, row: usize) -> &[String] {
        LabelRows::provenance(self, row)
    }
    fn own(&self, _row: usize) -> bool {
        true
    }
}

/// One row of a [`MergedRows`] view: an index into the configuration's own rows, or, with [`MergedRows::FOREIGN`] set, into its imported rows.
pub type RowRef = u32;

/// One input's own rows merged with the rows it imports, in `table.Window.key` order. No imported row has an own row's key, because [`crate::crossconfig::Imports`] drops a covered key instead of importing it.
#[derive(Clone, Copy)]
pub struct MergedRows<'a> {
    own: LabelRows<'a>,
    imported: &'a [ImportedRow],
    order: &'a [RowRef],
}

impl<'a> MergedRows<'a> {
    /// The flag that marks a [`RowRef`] as an imported row.
    pub const FOREIGN: RowRef = 1 << 31;

    /// Merges one input's own rows `own` with its imported rows `imported`, both in key order, into the reference list a [`MergedRows`] reads.
    pub fn order(own: &LabelRows<'_>, imported: &[ImportedRow]) -> Vec<RowRef> {
        let mut order: Vec<RowRef> = Vec::with_capacity(own.len() + imported.len());
        let (mut at, mut next) = (0usize, 0usize);
        while at < own.len() || next < imported.len() {
            let take_own = next >= imported.len()
                || (at < own.len() && own.key(at) < imported[next].key_text());
            if take_own {
                order.push(at as RowRef);
                at += 1;
            } else {
                order.push(next as RowRef | Self::FOREIGN);
                next += 1;
            }
        }
        order
    }

    pub fn new(own: LabelRows<'a>, imported: &'a [ImportedRow], order: &'a [RowRef]) -> Self {
        Self {
            own,
            imported,
            order,
        }
    }

    fn resolve(&self, row: usize) -> Result<usize, &'a ImportedRow> {
        let reference = self.order[row];
        if reference & Self::FOREIGN == 0 {
            Ok(reference as usize)
        } else {
            Err(&self.imported[(reference & !Self::FOREIGN) as usize])
        }
    }
}

impl RowSource for MergedRows<'_> {
    fn len(&self) -> usize {
        self.order.len()
    }
    fn left(&self, row: usize) -> &Rc<str> {
        match self.resolve(row) {
            Ok(own) => self.own.left(own),
            Err(imported) => &imported.key[1],
        }
    }
    fn right1(&self, row: usize) -> &Rc<str> {
        match self.resolve(row) {
            Ok(own) => self.own.right1(own),
            Err(imported) => &imported.key[2],
        }
    }
    fn right2(&self, row: usize) -> &Rc<str> {
        match self.resolve(row) {
            Ok(own) => self.own.right2(own),
            Err(imported) => &imported.key[3],
        }
    }
    fn right3(&self, row: usize) -> &Rc<str> {
        match self.resolve(row) {
            Ok(own) => self.own.right3(own),
            Err(imported) => &imported.key[4],
        }
    }
    fn right4(&self, row: usize) -> &Rc<str> {
        match self.resolve(row) {
            Ok(own) => self.own.right4(own),
            Err(imported) => &imported.key[5],
        }
    }
    fn outcome(&self, row: usize) -> &Rc<str> {
        match self.resolve(row) {
            Ok(own) => self.own.outcome(own),
            Err(imported) => &imported.outcome,
        }
    }
    fn joint(&self, row: usize) -> bool {
        match self.resolve(row) {
            Ok(own) => self.own.joint(own),
            Err(imported) => imported.joint,
        }
    }
    fn provenance(&self, row: usize) -> &[String] {
        match self.resolve(row) {
            Ok(own) => self.own.provenance(own),
            Err(imported) => &imported.provenance,
        }
    }
    fn own(&self, row: usize) -> bool {
        self.resolve(row).is_ok()
    }
}

/// [`fold_with`] over a fresh [`WindowOptions`], for a caller that has none.
pub fn fold_product(index: &SpecIndex, product: FixpointProduct) -> Result<Folded, String> {
    let mut options = WindowOptions::new(index).map_err(|error| error.to_string())?;
    fold_with(index, product, &mut options)
}

/// One configuration's two tables, folded from the product the worklist produced and nothing else. The steps run in this order: the key-order check, the expansion and the prospect pass, the row-chain search, the rule fold, the reachable-cells cross-check, the join fold, the reduced first-match-wins replay, the deep-class union check, and last the certificates. Each check returns an error where its invariant does not hold. A build of several configurations runs the same steps through [`Prepared`], with the rows the others keep live imported between the rule fold and the rest ([`crate::crossconfig`]).
///
/// `options` is the enumeration's own, so the certificates read the formation guard's verdicts from the memo the worklist already filled instead of sweeping them again.
pub fn fold_with(
    index: &SpecIndex,
    product: FixpointProduct,
    options: &mut WindowOptions<'_>,
) -> Result<Folded, String> {
    fold_with_report(index, product, options, None)
}

/// [`fold_with`] with wall-clock reports for the row-chain search (`prefixes`) and the outcome partition (`partition`). The phase names carry no configuration suffix, so the caller chooses the grouping and output format. [`fold_with`] reads no clocks.
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
    product: FixpointProduct,
    options: &mut WindowOptions<'_>,
    mut report: Option<&mut FoldReporter<'_>>,
) -> Result<Folded, String> {
    let prepared = Prepared::new(product, report.as_deref_mut())?;
    let imports = Imports::default();
    let mut folds = prepared.unfolded();
    prepared.fold_inputs(index, &imports, None, &mut folds)?;
    prepared.finish(index, options, folds, &imports, None, report)
}

/// One configuration's rows ready to fold: the product after the key-order check, its label-grain expansion with the prospect pass's joint flags written back to the class rows, each input's run of the expansion, and every row's shortest row chain. Everything here depends on the configuration's own rows only, so it is computed once, before any rows are imported.
pub struct Prepared {
    product: FixpointProduct,
    fold_rows: Vec<FoldRow>,
    runs: Vec<(usize, usize)>,
    chains: certificate::RowChains,
}

impl Prepared {
    /// The key-order check, the expansion, the prospect pass, and the row-chain search, which `report` times as `prefixes`.
    pub fn new(
        mut product: FixpointProduct,
        report: Option<&mut FoldReporter<'_>>,
    ) -> Result<Self, String> {
        assert_key_sorted(&product)?;
        let mut fold_rows = expand(&mut product);
        flag_prospect_joints(&product, &mut fold_rows);
        let mut class_joint: Vec<bool> = product.transitions.iter().map(|row| row.joint).collect();
        for row in &fold_rows {
            if row.joint {
                class_joint[row.class_row as usize] = true;
            }
        }
        for (row, joint) in product.transitions.iter_mut().zip(&class_joint) {
            row.joint = *joint;
        }
        let rows = LabelRows::new(&product, &fold_rows);
        let runs = input_runs(&rows);
        let started = report.is_some().then(Instant::now);
        let chains = certificate::RowChains::over(&rows);
        if let (Some(report), Some(started)) = (report, started) {
            report("prefixes", started.elapsed());
        }
        Ok(Self {
            product,
            fold_rows,
            runs,
            chains,
        })
    }

    /// The configuration's own label-grain rows.
    pub fn rows(&self) -> LabelRows<'_> {
        LabelRows::new(&self.product, &self.fold_rows)
    }

    pub fn chains(&self) -> &certificate::RowChains {
        &self.chains
    }

    /// One empty slot per input, for [`Prepared::fold_inputs`] to fill.
    pub fn unfolded(&self) -> Vec<Option<RuleFold>> {
        self.runs.iter().map(|_| None).collect()
    }

    /// Folds the rules of every input `inputs` names, or of every input when it is `None`, into that input's slot of `folds`, reading the input's own rows merged with what `imports` holds for it. Inputs fold in key order, so the first error is the first input's.
    pub fn fold_inputs(
        &self,
        index: &SpecIndex,
        imports: &Imports,
        inputs: Option<&HashSet<Rc<str>>>,
        folds: &mut [Option<RuleFold>],
    ) -> Result<(), String> {
        let rows = self.rows();
        for (slot, (start, end)) in folds.iter_mut().zip(&self.runs) {
            let slice = rows.slice(*start, *end);
            let input_glyph = Rc::clone(slice.input_glyph(0));
            if inputs.is_some_and(|wanted| !wanted.contains(&input_glyph)) {
                continue;
            }
            let rune = input_glyph.split('.').next().unwrap_or(&input_glyph);
            let Some(modeled) = index.sym_of(rune).filter(|name| index.is_modeled(*name)) else {
                return Err(format!(
                    "{input_glyph}: the spec models no rune {}",
                    python_repr(rune)
                ));
            };
            let never_locked = !index.is_entry_bearing(modeled);
            let imported = imports.for_input(&input_glyph);
            *slot = Some(if imported.is_empty() {
                rules_for_input(&input_glyph, &slice, never_locked)?
            } else {
                let order = MergedRows::order(&slice, imported);
                let merged = MergedRows::new(slice, imported, &order);
                rules_for_input(&input_glyph, &merged, never_locked)?
            });
        }
        Ok(())
    }

    /// The rules of every input in table order, from the slots [`Prepared::fold_inputs`] filled.
    pub fn rules(folds: &[Option<RuleFold>]) -> Vec<Rule> {
        folds
            .iter()
            .flatten()
            .flat_map(|fold| fold.rules.iter().cloned())
            .collect()
    }

    /// Finishes the fold from the per-input rule folds: the reachable-cells cross-check, the join fold, the reduced first-match-wins replay over the own rows and every imported row, the deep-class union check, and the certificates, which `report` times as `partition` around the replay. `crossing` names the build's spellings and this configuration's position in them, which a guard rule's certificate needs; it is `None` when nothing is imported.
    ///
    /// An imported row must first-match a rule with its outcome, like an own row, and a rule an imported row first-matches is reached. A rule that only imported rows first-match is a guard rule, whose certificate is completed from one of those rows' chains in the configuration the row is live in ([`certificate::guard_certificate`]).
    pub fn finish(
        self,
        index: &SpecIndex,
        options: &mut WindowOptions<'_>,
        folds: Vec<Option<RuleFold>>,
        imports: &Imports,
        crossing: Option<(&Spellings, usize)>,
        report: Option<&mut FoldReporter<'_>>,
    ) -> Result<Folded, String> {
        let Self {
            product,
            fold_rows,
            runs,
            chains,
        } = self;
        let rows = LabelRows::new(&product, &fold_rows);
        let mut rules: Vec<Rule> = Vec::new();
        let mut identity_guards: i64 = 0;
        let mut replay_lefts: ReplayLefts = HashMap::default();
        for ((start, _end), fold) in runs.iter().zip(folds) {
            let fold = fold.ok_or_else(|| {
                format!(
                    "{}: the input's rules were never folded",
                    rows.input_glyph(*start)
                )
            })?;
            identity_guards += fold.identity_guards;
            rules.extend(fold.rules);
            replay_lefts.insert(Rc::clone(rows.input_glyph(*start)), fold.replay_lefts);
        }

        assert_reachable_cells(index, &rows, &product.settled_records, &product.cells)?;

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
            match left_settled.junction {
                None => {
                    seen.insert((
                        Rc::clone(rows.left(row)),
                        Rc::clone(rows.outcome(row)),
                        "break".to_owned(),
                        0,
                    ));
                }
                Some(junction) => {
                    seen.insert((
                        Rc::clone(rows.left(row)),
                        Rc::clone(rows.outcome(row)),
                        index.resolve(junction).to_owned(),
                        left_settled.extension + entry_extensions[&product.settled(base).cell],
                    ));
                }
            }
        }
        // Sort on the whole row: two rows tying on (left, right, junction) would otherwise come out in hash-set order.
        let mut join_rows: Vec<JoinRow> = seen
            .into_iter()
            .map(|(left, right, junction, extension)| JoinRow {
                left,
                right,
                junction,
                extension,
                kern: 0,
            })
            .collect();
        join_rows.sort();

        let started = report.is_some().then(Instant::now);
        let first_rows = first_match_rows_open(
            &rows,
            &rules,
            Some(&replay_lefts),
            certificate::ROW_CAP,
            Some(chains.dist()),
        )?;
        let imported_first = imported_first_rows(&rules, imports, crossing)?;
        assert_reached(&rules, |rule| {
            !first_rows[rule].is_empty() || !imported_first[rule].is_empty()
        })?;
        if let (Some(report), Some(started)) = (report, started) {
            report("partition", started.elapsed());
        }
        assert_deep_class_unions(&product, &rules)?;
        let by_input = rules_by_input(&rules);
        let certificates = certificate::certify(
            index,
            options,
            &chains,
            &rows,
            &rules,
            &first_rows,
            &mut |options, rule_index| {
                let Some((spellings, receiver)) = crossing else {
                    return Err(format!(
                        "rule {rule_index} has no replayed row, and nothing is imported"
                    ));
                };
                for &at in &imported_first[rule_index] {
                    if let Some(certificate) = certificate::guard_certificate(
                        index,
                        options,
                        spellings,
                        receiver,
                        &by_input,
                        rule_index,
                        &imports.rows()[at],
                    )? {
                        return Ok(certificate);
                    }
                }
                let rule = &rules[rule_index];
                Err(format!(
                    "guard rule {rule_index} ({} -> {}) first-matches only windows another configuration keeps live ({} of them), and none of them completes into a string it first-matches there",
                    rule.input_glyph,
                    rule.outcome,
                    imported_first[rule_index].len()
                ))
            },
        )?;

        let config = product.config.clone();
        let buckets = crossing.map_or_else(Vec::new, |(spellings, own)| {
            crate::crossconfig::rule_buckets(spellings, own, &rules)
        });
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
            imports: imports
                .rows()
                .iter()
                .map(|row| {
                    let key = row.key_text();
                    let live = crossing.map_or_else(String::new, |(spellings, _)| {
                        spellings.tokens()[row.source].clone()
                    });
                    [
                        key[0].to_owned(),
                        key[1].to_owned(),
                        key[2].to_owned(),
                        key[3].to_owned(),
                        key[4].to_owned(),
                        key[5].to_owned(),
                        row.outcome.to_string(),
                        live,
                    ]
                })
                .collect(),
            fold_order: crossing.map_or_else(Vec::new, |(spellings, _)| spellings.fold_order()),
            buckets,
        };
        Ok(Folded {
            decision,
            joins: JoinTable {
                config,
                rows: join_rows,
            },
            replay_lefts,
        })
    }
}

/// For each rule, up to [`certificate::ROW_CAP`] of the imported rows that first-match it, in key order. An imported row whose first match gives another outcome is an error naming the configuration it is live in.
fn imported_first_rows(
    rules: &[Rule],
    imports: &Imports,
    crossing: Option<(&Spellings, usize)>,
) -> Result<Vec<Vec<usize>>, String> {
    let mut first: Vec<Vec<usize>> = vec![Vec::new(); rules.len()];
    if imports.is_empty() {
        return Ok(first);
    }
    let by_input = rules_by_input(rules);
    let mut failures: Vec<String> = Vec::new();
    let mut count = 0usize;
    for (at, row) in imports.rows().iter().enumerate() {
        let key = row.key_text();
        let matched = first_match(&by_input, key);
        let predicted: &str = matched.map_or(key[0], |position| &rules[position].outcome);
        if predicted != &*row.outcome {
            count += 1;
            if failures.len() < 5 {
                let live = crossing.map_or("another configuration", |(spellings, _)| {
                    &spellings.tokens()[row.source]
                });
                failures.push(format!(
                    "{}: live in {live}, which settles it to {}, rules say {predicted}",
                    key_repr(key),
                    row.outcome
                ));
            }
            continue;
        }
        if let Some(position) = matched
            && first[position].len() < certificate::ROW_CAP
        {
            first[position].push(at);
        }
    }
    if count > 0 {
        return Err(format!(
            "{count} first-match-wins replay mismatches on imported windows: {}",
            failures.join("; ")
        ));
    }
    Ok(first)
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

/// The label-grain expansion of one product, in `table.Window.key` order. The module doc says why sorting each prefix run is enough. Every deep class's members are interned into the product's label pool, which the rows' ids index, and the expansion is allocated at its exact length. It is public so a caller replaying a perturbed rule list can build the rows the fold checked.
pub fn expand(product: &mut FixpointProduct) -> Vec<FoldRow> {
    let mut members: HashMap<Label, Vec<Label>> = HashMap::default();
    for (token, names) in &product.deep_classes {
        let token = product.labels.intern(token);
        let interned = names
            .iter()
            .map(|name| product.labels.intern(name))
            .collect();
        members.insert(token, interned);
    }

    let product = &*product;
    let rows = &product.transitions;
    let width = |label: Label| members.get(&label).map_or(1, Vec::len);
    let mut expanded: Vec<FoldRow> = Vec::with_capacity(
        rows.iter()
            .map(|row| width(row.right3) * width(row.right4))
            .sum(),
    );
    let mut start = 0;
    while start < rows.len() {
        let mut end = start + 1;
        while end < rows.len() && near_slots(&rows[end]) == near_slots(&rows[start]) {
            end += 1;
        }
        let run = expanded.len();
        for (class_row, row) in rows.iter().enumerate().take(end).skip(start) {
            let members3 = members
                .get(&row.right3)
                .map_or(std::slice::from_ref(&row.right3), Vec::as_slice);
            let members4 = members
                .get(&row.right4)
                .map_or(std::slice::from_ref(&row.right4), Vec::as_slice);
            for &right3 in members3 {
                for &right4 in members4 {
                    expanded.push(FoldRow {
                        class_row: class_row as u32,
                        right3,
                        right4,
                        joint: row.joint,
                    });
                }
            }
        }
        let text = |label: Label| &**product.labels.text(label);
        expanded[run..].sort_by(|left, right| {
            (text(left.right3), text(left.right4)).cmp(&(text(right.right3), text(right.right4)))
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
/// A row's followers are the rows whose (left, input, right1) is the row's own (outcome, right1, right2). When the row carries a right3, only the followers whose right2 is that label count, and when it carries a right4, only those whose right3 is that label. The expansion is in key order, so the rows sharing an (input, left, right1) are one contiguous run, sorted by right2. The index maps each run to its range of the expansion. A row that carries a right3 narrows the range to that label's rows by binary search, and a row whose right3 is `#NA` walks the whole range. Either way, a carried right4 is checked against each follower's right3. The pass reads only the junction each follower settled (through the product's `settled_records` table), never a follower's joint flag, so the order the rows are visited in does not change the result, and the flags are applied together at the end.
fn flag_prospect_joints(product: &FixpointProduct, fold: &mut [FoldRow]) {
    let class = &product.transitions;
    let prefix_of = |row: &FoldRow| {
        let [input, left, right1, _] = near_slots(&class[row.class_row as usize]);
        [left, input, right1]
    };
    let mut prefixes: HashMap<[Label; 3], Range<usize>> = HashMap::default();
    let mut start = 0;
    while start < fold.len() {
        let prefix = prefix_of(&fold[start]);
        let mut end = start + 1;
        while end < fold.len() && prefix_of(&fold[end]) == prefix {
            end += 1;
        }
        prefixes.insert(prefix, start..end);
        start = end;
    }
    let mut flagged: Vec<u32> = Vec::new();
    for (position, row) in fold.iter().enumerate() {
        if row.joint {
            continue;
        }
        let base = &class[row.class_row as usize];
        if boundaryish(product.labels.text(base.right1))
            || boundaryish(product.labels.text(base.right2))
        {
            continue;
        }
        let outcome = product.outcomes[base.settled.index()];
        let Some(prefix) = prefixes.get(&[outcome, base.right1, base.right2]) else {
            continue;
        };
        let mut followers = &fold[prefix.clone()];
        let right3 = &**product.labels.text(row.right3);
        if right3 != NA_LABEL {
            let right2 = |follower: &FoldRow| {
                &**product
                    .labels
                    .text(class[follower.class_row as usize].right2)
            };
            let start = followers.partition_point(|follower| right2(follower) < right3);
            let end = followers.partition_point(|follower| right2(follower) <= right3);
            followers = &followers[start..end];
        }
        let carries_right4 = &**product.labels.text(row.right4) != NA_LABEL;
        let diverges = followers.iter().any(|follower| {
            if carries_right4 && follower.right3 != row.right4 {
                return false;
            }
            let followed = &class[follower.class_row as usize];
            i8::from(
                product.settled_records[followed.settled.index()]
                    .junction
                    .is_some(),
            ) != base.prospect
        });
        if diverges {
            flagged.push(position as u32);
        }
    }
    for position in flagged {
        fold[position as usize].joint = true;
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

/// How far one settled cell's own adjustments move its entry. The join fold adds this to the left's extension.
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

/// Checks that the two grains agree: the set of cells the fold rows settle into, read through the product's settled table, equals the product's `cells`.
fn assert_reachable_cells(
    index: &SpecIndex,
    rows: &LabelRows<'_>,
    settled_records: &[Settled],
    cells: &[CellId],
) -> Result<(), String> {
    let folded: HashSet<&CellId> = (0..rows.len())
        .map(|row| &settled_records[rows.base(row).settled.index()].cell)
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
/// The reduced replay checks the same statement as the whole-table replay, for two reasons. A committed block's rules carry the whole block in `backtrack`, so every member of the block matches the same slots as the representative and its rows first-match the same rule. Replaying one member therefore decides the block. And every rule reachable only from a boundary left (the default rules, and the ZWNJ backtrack replicas and identity catch-all that [`crate::rulefold`] mints, since `uni200C` is boundaryish) is replayed against every member of the boundary block, which the reduction keeps whole. So a rule unreachable under the reduction is unreachable over the whole table too. `rebuild/test_table.py`'s `replay` checks both claims on the mini fixture with its own first-match-wins implementation.
///
/// The fold runs this replay under the reduction (through [`first_match_rows`]), so `build-tables` fails on an unreachable rule as it folds, and a Python caller sees a `KernelRunError`.
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
    for (position, rule) in rules.iter().enumerate() {
        by_input
            .entry(&rule.input_glyph)
            .or_default()
            .push((position, rule));
    }
    by_input
}

/// First-match-wins over one window's six labels: the index of the input's first rule whose five constrained slots all admit the labels at them, or `None` when no rule matches and the input is left unchanged. This is the semantics the emitted lookup compiles to. The certificates and the shipped-order walk use this function. The fold's replay uses `IndexedMatcher`, which the tests check against it.
pub fn first_match(by_input: &HashMap<&str, Vec<(usize, &Rule)>>, key: [&str; 6]) -> Option<usize> {
    for (position, rule) in by_input.get(key[0]).map_or(&[][..], Vec::as_slice) {
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
        return Some(*position);
    }
    None
}

const LINEAR_CLASS_MAX: usize = 8;

struct IndexedRule {
    position: usize,
    slots: [Option<Box<[u32]>>; 5],
}

struct IndexedMatcher<'a> {
    rows: LabelRows<'a>,
    by_input: HashMap<u32, Vec<IndexedRule>>,
}

impl<'a> IndexedMatcher<'a> {
    /// Compiles the ordered rules [`first_match`] reads into sorted classes of product-local label IDs. The cloned pool keeps every existing row ID, the expansion's deep labels included ([`expand`] interns them), and rule members that the pool lacks are interned after them. The IDs are used only to test membership, so the order they are minted in affects no result.
    fn new(rows: &LabelRows<'a>, rules: &[Rule]) -> Self {
        let mut labels = rows.product.labels.clone();
        let mut by_input: HashMap<u32, Vec<IndexedRule>> = HashMap::default();
        for (position, rule) in rules.iter().enumerate() {
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
                .push(IndexedRule { position, slots });
        }
        Self {
            rows: *rows,
            by_input,
        }
    }

    fn first_match(&self, row: usize) -> Option<usize> {
        let rows = self.rows;
        let base = rows.base(row);
        let deep = &rows.fold[row];
        let slots = [
            base.left.0,
            base.right1.0,
            base.right2.0,
            deep.right3.0,
            deep.right4.0,
        ];
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
            return Some(rule.position);
        }
        None
    }
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
        if let Some(position) = first_match(&by_input, key) {
            predicted = &rules[position].outcome;
            match dist {
                None => {
                    if first_rows[position].len() < keep {
                        first_rows[position].push(row);
                    }
                }
                Some(dist) => {
                    let rank = dist[row];
                    let kept = &mut first_rows[position];
                    let ranked = &mut ranks[position];
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
    let unreachable: Vec<usize> = (0..rules.len())
        .filter(|position| first_rows[*position].is_empty())
        .collect();
    if unreachable.is_empty() {
        return Ok(first_rows);
    }
    let listed: Vec<String> = unreachable
        .iter()
        .take(5)
        .map(|position| rule_repr(&rules[*position]))
        .collect();
    Err(format!(
        "{} unreachable rule(s), which no replayed row first-matches: {}",
        unreachable.len(),
        listed.join("; ")
    ))
}

/// [`assert_outcome_partition`]'s replay, returning for every rule up to `keep` of the replayed rows that first-match it, as indices into `rows`. When `dist` ranks the rows ([`crate::certificate::RowChains`]), these are the rows with the shortest row chains, an unreached row ranking last. Otherwise they are the first rows in replay order. The errors are the assertion's: an outcome mismatch, or a rule no replayed row first-matches. The `IndexedMatcher` is built and dropped inside this call, so its build time and memory count toward the `partition` phase.
pub fn first_match_rows(
    rows: &LabelRows<'_>,
    rules: &[Rule],
    lefts: Option<&ReplayLefts>,
    keep: usize,
    dist: Option<&[u32]>,
) -> Result<Vec<Vec<usize>>, String> {
    let first_rows = first_match_rows_open(rows, rules, lefts, keep, dist)?;
    assert_reached(rules, |rule| !first_rows[rule].is_empty())?;
    Ok(first_rows)
}

/// The unreachable-rule error for the rules `reached` says no replayed row first-matches, or `Ok` when every rule is reached.
fn assert_reached(rules: &[Rule], reached: impl Fn(usize) -> bool) -> Result<(), String> {
    let unreachable: Vec<usize> = (0..rules.len()).filter(|&rule| !reached(rule)).collect();
    if unreachable.is_empty() {
        return Ok(());
    }
    let listed: Vec<String> = unreachable
        .iter()
        .take(5)
        .map(|position| rule_repr(&rules[*position]))
        .collect();
    Err(format!(
        "{} unreachable rule(s), which no replayed row first-matches: {}",
        unreachable.len(),
        listed.join("; ")
    ))
}

/// [`first_match_rows`] without its reachability check: a rule no replayed row first-matches gets an empty list. [`Prepared::finish`] checks reachability over these rows and the imported ones together. The replay returns, for every rule, up to `keep` of the replayed rows that first-match it, as indices into `rows`. When `dist` ranks the rows ([`crate::certificate::RowChains`]), these are the rows with the shortest row chains, an unreached row ranking last. Otherwise they are the first rows in replay order. The errors are the assertion's: an outcome mismatch, or a rule no replayed row first-matches. The `IndexedMatcher` is built and dropped inside this call, so its build time and memory count toward the `partition` phase.
pub fn first_match_rows_open(
    rows: &LabelRows<'_>,
    rules: &[Rule],
    lefts: Option<&ReplayLefts>,
    keep: usize,
    dist: Option<&[u32]>,
) -> Result<Vec<Vec<usize>>, String> {
    let matcher = IndexedMatcher::new(rows, rules);
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
        if let Some(position) = matcher.first_match(row) {
            predicted = &rules[position].outcome;
            match dist {
                None => {
                    if first_rows[position].len() < keep {
                        first_rows[position].push(row);
                    }
                }
                Some(dist) => {
                    let rank = dist[row];
                    let kept = &mut first_rows[position];
                    let ranked = &mut ranks[position];
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
    Ok(first_rows)
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

/// Checks that every emitted lookahead class in look3 or look4, among the rules that match a class row's near slots, contains that row's deep class either whole or not at all. This makes conform's rule-membership tests, which test one representative member per deep class, exact.
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
                    return Err(partial_class(
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
                        return Err(partial_class(
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

fn partial_class(
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
        "{}: an emitted {slot} class holds only part of deep class {token} at {}: {} of {}",
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
    use crate::crossconfig::{ForeignRow, SharedRules, Source, SourceRows};
    use crate::fixpoint::{EnumerationModes, deep_class_id, enumerate_transitions};
    use crate::index::fixtures;
    use crate::types::{NotesId, SettledId};
    use std::cell::RefCell;

    /// The default modes, which the fixture is folded in.
    const DEFAULT_MODES: EnumerationModes = EnumerationModes {
        simulated_prospect: true,
        follower_prefer_slots: true,
        deep_classes: true,
    };

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

    /// The mini fixture's fixpoint and the tables it folds into.
    fn built() -> (SpecIndex, FixpointProduct, Folded) {
        let index = fixtures::mini();
        let product = enumerate_transitions(&index, &[], DEFAULT_MODES)
            .expect("the fixture's fixpoint closes and settles");
        let folded = fold_product(&index, product.clone()).expect("and folds");
        (index, product, folded)
    }

    #[test]
    fn the_fixtures_fixpoint_folds_into_rules_windows_and_join_rows() {
        let (_index, _product, folded) = built();
        assert!(!folded.decision.rules.is_empty());
        assert!(!folded.decision.transitions.is_empty());
        assert!(!folded.joins.rows.is_empty());
        assert!(!folded.decision.cited_provenance.is_empty());
    }

    /// The ordered rules predict every row under the whole-table replay, which the fixture is small enough to afford, and under the reduced replay the build runs.
    #[test]
    fn every_enumerated_row_is_what_the_ordered_rules_predict() {
        let (_index, mut product, folded) = built();
        let fold_rows = expand(&mut product);
        let rows = LabelRows::new(&product, &fold_rows);
        assert_outcome_partition(&rows, &folded.decision.rules, None)
            .expect("first-match-wins over the whole table");
        assert_outcome_partition(&rows, &folded.decision.rules, Some(&folded.replay_lefts))
            .expect("and over the reduction the build replays");
    }

    #[test]
    fn production_proof_preserves_reference_row_chains_matches_rows_failures_and_certificates() {
        let (index, mut product, folded) = built();
        let fold_rows = expand(&mut product);
        let rows = LabelRows::new(&product, &fold_rows);
        let rules = &folded.decision.rules;
        let by_input = rules_by_input(rules);
        let indexed = IndexedMatcher::new(&rows, rules);
        for row in 0..rows.len() {
            assert_eq!(
                first_match(&by_input, rows.key(row)),
                indexed.first_match(row),
                "{}",
                key_repr(rows.key(row))
            );
        }

        let reference_chains = certificate::RowChains::over_reference(&rows);
        let chains = certificate::RowChains::over(&rows);
        certificate::assert_same_chains(&reference_chains, &chains);
        let reference_rows = first_match_rows_reference(
            &rows,
            rules,
            Some(&folded.replay_lefts),
            certificate::ROW_CAP,
            Some(reference_chains.dist()),
        )
        .expect("the reference replay accepts the folded rules");
        let production_rows = first_match_rows(
            &rows,
            rules,
            Some(&folded.replay_lefts),
            certificate::ROW_CAP,
            Some(chains.dist()),
        )
        .expect("the production replay accepts the folded rules");
        assert_eq!(reference_rows, production_rows);
        let mut options = WindowOptions::new(&index).expect("the fixture has valid options");
        let reference_certificates = certificate::certify(
            &index,
            &mut options,
            &reference_chains,
            &rows,
            rules,
            &reference_rows,
            &mut certificate::no_guards,
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
                    Some(chains.dist()),
                );
                let production = first_match_rows(
                    &rows,
                    rules,
                    lefts,
                    certificate::ROW_CAP,
                    Some(chains.dist()),
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
        let (_index, mut product, folded) = built();
        let fold_rows = expand(&mut product);
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
            message.contains("1 unreachable rule(s), which no replayed row first-matches"),
            "{message}"
        );
        assert!(message.contains("qsNever.loop"), "{message}");
        assert!(message.contains("a dead rule"), "{message}");
    }

    /// A duplicate of the last rule matches the same rows as the original, which precedes it, so first-match-wins never reaches the duplicate and every prediction is unchanged.
    #[test]
    fn a_shadowed_duplicate_is_refused() {
        let (_index, mut product, folded) = built();
        let fold_rows = expand(&mut product);
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
        let (_index, mut product, folded) = built();
        let fold_rows = expand(&mut product);
        let rows = LabelRows::new(&product, &fold_rows);
        let rules = &folded.decision.rules;
        let mut perturbations: Vec<Vec<Rule>> = Vec::new();
        for position in 0..rules.len() {
            let mut dropped = rules.clone();
            dropped.remove(position);
            perturbations.push(dropped);
        }
        for position in 0..rules.len() - 1 {
            let mut swapped = rules.clone();
            swapped.swap(position, position + 1);
            perturbations.push(swapped);
        }
        for position in 0..rules.len() {
            if rules[position].look1.is_none() {
                continue;
            }
            let mut widened = rules.clone();
            widened[position].look1 = None;
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
                .filter(|position| {
                    rules[*position].look1.as_ref() == Some(&boundary)
                        && rules[*position].look2.is_none()
                })
                .collect();
            let lettered: Vec<usize> = (0..rules.len())
                .filter(|position| {
                    rules[*position]
                        .look1
                        .as_ref()
                        .is_some_and(|look| look != &boundary)
                })
                .collect();
            let fallback: Vec<usize> = (0..rules.len())
                .filter(|position| {
                    rules[*position].look1.is_none() && rules[*position].look2.is_none()
                })
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

    /// The join rows are sorted and distinct, at least one break row has extension 0, and `kern` is always 0.
    #[test]
    fn the_join_rows_are_sorted_and_distinct() {
        let (_index, _product, folded) = built();
        let rows = &folded.joins.rows;
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
            artifacts::join_tsv(&folded.joins),
            artifacts::join_tsv(&twice.joins)
        );
        let whole = artifacts::table_digest(&index, &folded.decision, &folded.joins);
        assert_eq!(
            whole,
            artifacts::table_digest(&index, &twice.decision, &twice.joins)
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
            artifacts::table_digest(&index, &fewer_rules, &folded.joins),
            artifacts::table_digest(&index, &fewer_rows, &folded.joins),
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
        junction: crate::model::Sym,
        settled_records: RefCell<Vec<Settled>>,
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
            let junction = fixtures::sym(&index, "baseline");
            Self {
                index,
                cell,
                junction,
                settled_records: RefCell::new(Vec::new()),
                labels: RefCell::new(LabelPool::default()),
                outcomes: RefCell::new(Vec::new()),
            }
        }

        /// Appends a settled record and its outcome to the bench's tables, which every product the bench builds carries, and returns its index.
        fn push_settled(&self, settled: Settled, outcome: &str) -> SettledId {
            let mut settled_records = self.settled_records.borrow_mut();
            let id = SettledId::at(settled_records.len());
            settled_records.push(settled);
            self.outcomes
                .borrow_mut()
                .push(self.labels.borrow_mut().intern(outcome));
            id
        }

        /// One row from its six key labels and its outcome label, the prospect its trace claimed, and whether it committed a junction.
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
                settled: self.push_settled(
                    Settled {
                        cell: self.cell.clone(),
                        junction: joins.then_some(self.junction),
                        extension: 0,
                    },
                    labels[6],
                ),
                left_settled: None,
                provenance: NotesId::at(0),
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

        /// The bench cell with the given adjustments, so two rows under one left can have different entry extensions and produce two join rows that tie on (left, right, junction).
        fn adjusted(&self, adjustments: Vec<AdjustmentToken>) -> CellId {
            CellId {
                adjustments,
                ..self.cell.clone()
            }
        }

        /// One row whose left committed a junction. The join fold skips rows without `left_settled`, and [`Bench::row`] leaves it absent, so a product of those rows folds into no join rows.
        fn joined(&self, labels: [&str; 7], cell: CellId, left_extension: i64) -> TransitionRow {
            let mut row = self.row(labels, 0, false);
            row.settled = self.push_settled(
                Settled {
                    cell,
                    junction: None,
                    extension: 0,
                },
                labels[6],
            );
            row.left_settled = Some(self.push_settled(
                Settled {
                    cell: self.cell.clone(),
                    junction: Some(self.junction),
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
                settled_records: self.settled_records.borrow().clone(),
                labels,
                outcomes: self.outcomes.borrow().clone(),
                notes: vec![Vec::new()],
            }
        }
    }

    /// A row whose optimistic prospect claims a junction that the follower's settled choice does not make is flagged joint, and the follower's row is not.
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

    /// A row that carries a right3 compares its prospect only with the followers whose right2 is that label, and a row whose right3 is `#NA` compares it with every follower.
    #[test]
    fn a_prospect_meets_only_the_followers_its_right3_admits() {
        let bench = Bench::new();
        let leader = |right3: &'static str| {
            bench.row(
                ["qsIt", "#EDGE", "qsMay", "C", right3, "#NA", "qsIt.x"],
                1,
                true,
            )
        };
        let mut product = bench.product(
            vec![
                leader("#NA"),
                leader("D"),
                leader("E"),
                bench.row(
                    ["qsMay", "qsIt.x", "C", "D", "#NA", "#NA", "qsMay.y"],
                    0,
                    true,
                ),
                bench.row(
                    ["qsMay", "qsIt.x", "C", "E", "#NA", "#NA", "qsMay.y"],
                    0,
                    false,
                ),
            ],
            Vec::new(),
        );
        let mut rows = expand(&mut product);
        flag_prospect_joints(&product, &mut rows);
        let flagged: Vec<(&str, &str, &str, bool)> = rows
            .iter()
            .map(|row| {
                let base = &product.transitions[row.class_row as usize];
                (
                    &**product.labels.text(base.input_glyph),
                    &**product.labels.text(base.right2),
                    &**product.labels.text(row.right3),
                    row.joint,
                )
            })
            .collect();
        assert_eq!(
            flagged,
            [
                ("qsIt", "C", "#NA", true),
                ("qsIt", "C", "D", false),
                ("qsIt", "C", "E", true),
                ("qsMay", "D", "#NA", false),
                ("qsMay", "E", "#NA", false),
            ]
        );
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
    fn indexed_membership_preserves_overlap_guards_and_identity() {
        let bench = Bench::new();
        let third = deep_class_id(&["D".to_owned(), "E".to_owned()]);
        let fourth = deep_class_id(&["F".to_owned(), "G".to_owned()]);
        let mut product = bench.product(
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
        let fold_rows = expand(&mut product);
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
        let indexed = IndexedMatcher::new(&rows, &rules);
        let mut saw_identity = false;
        for row in 0..rows.len() {
            let key = rows.key(row);
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
    fn a_rule_that_holds_part_of_a_deep_class_is_refused() {
        let (bench, product, tokens) = deep_bench();
        let folded = fold_product(&bench.index, product.clone()).expect("the product folds");
        assert_deep_class_unions(&product, &folded.decision.rules)
            .expect("the emitted classes are whole");
        let mut partial = folded.decision.rules.clone();
        partial.push(Rule {
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
        let error = assert_deep_class_unions(&product, &partial)
            .expect_err("half a class is a partial class");
        assert!(
            error.contains("an emitted look3 class holds only part of deep class"),
            "{error}"
        );
        assert!(error.contains(&tokens[0]), "{error}");
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
        let error =
            fold_product(&bench.index, product).expect_err("the extra cell is a disagreement");
        assert!(
            error.starts_with("the product's reachable cells disagree with the fold rows': [("),
            "{error}"
        );
        assert!(error.contains("'qsTea', 'full'"), "{error}");
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
        let error = fold_product(&bench.index, product).expect_err("two default blocks");
        assert_eq!(
            error,
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
        let error = fold_product(&bench.index, product).expect_err("nothing to sample");
        assert_eq!(error, "qsIt: boundary lookaheads disagree: set()");
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
        let error = fold_product(&bench.index, product).expect_err("the rows are out of order");
        assert_eq!(
            error,
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
            let error =
                fold_product(&bench.index, product).expect_err("the spec models no such rune");
            assert_eq!(error, format!("{input}: the spec models no rune '{rune}'"));
        }
    }

    /// The rows the two ZWNJ lock tests fold: one committed left with a split on the second slot and an end-of-run row, and a default block of boundary lefts, ZWNJ included, whose end-of-run row settles to the bare input. That last row gives the identity guard a row to match. A slot-dropped row that settles to the input is an identity fallback the fold omits, so no shallower rule stands between a ZWNJ-backtrack row and the identity guard. If another rule already matched that row, the guard would be a rule no replayed row first-matches.
    fn zwnj_lock(bench: &Bench, input: &str) -> FixpointProduct {
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

    /// An input the ZWNJ lock never replaces (`qsIt`, which is not entry-bearing) starts its rules with the default block replicated under an explicit `uni200C` backtrack, and ends that run with the identity guard, so no later rule with a backtrack class can match across a ZWNJ.
    #[test]
    fn an_input_the_zwnj_lock_never_replaces_leads_its_rules_with_zwnj_guards() {
        let bench = Bench::new();
        assert!(
            !bench
                .index
                .is_entry_bearing(fixtures::sym(&bench.index, "qsIt")),
            "the fixture stopped being the one this case needs"
        );
        let folded =
            fold_product(&bench.index, zwnj_lock(&bench, "qsIt")).expect("the product folds");
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

    /// The same rows under a rune the ZWNJ lock replaces (`qsPea`) emit no `uni200C` backtrack, because after a ZWNJ that input enumerates under its locked copy's label. Its rule list is shorter than `qsIt`'s by the number of guards.
    #[test]
    fn an_input_the_zwnj_lock_replaces_gets_no_zwnj_guards() {
        let bench = Bench::new();
        assert!(
            bench
                .index
                .is_entry_bearing(fixtures::sym(&bench.index, "qsPea")),
            "the fixture stopped being the one this case needs"
        );
        let locked =
            fold_product(&bench.index, zwnj_lock(&bench, "qsPea")).expect("the product folds");
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
            fold_product(&bench.index, zwnj_lock(&bench, "qsIt")).expect("the product folds");
        let guards = never_locked
            .decision
            .rules
            .iter()
            .filter(|rule| rule.backtrack.as_ref() == Some(&zwnj))
            .count();
        assert_eq!(
            locked.decision.rules.len() + guards,
            never_locked.decision.rules.len(),
            "the two cases differ by the guards and nothing else"
        );
    }

    /// Two join rows that tie on (left, right, junction) are ordered by the whole row. No live join table has such a tie (every `joins-<config>.tsv` has as many distinct triples as rows), but a sort on the triple alone would leave a tied pair in hash-set order.
    #[test]
    fn join_rows_tying_on_the_triple_are_ordered_by_the_whole_row() {
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
            .joins
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
            artifacts::table_digest(&bench.index, &once.decision, &once.joins),
            artifacts::table_digest(&bench.index, &twice.decision, &twice.joins)
        );
    }

    /// What one configuration of an exchange holds once it ends: its prepared rows, its imports, and its rule folds.
    struct Exchanged {
        prepared: Prepared,
        imports: Imports,
        folds: Vec<Option<RuleFold>>,
    }

    impl Exchanged {
        fn rules(&self) -> Vec<Rule> {
            Prepared::rules(&self.folds)
        }
    }

    /// The exchange `fanout::run_configs_tables` runs across threads, run here on one: every configuration folds its own rows and publishes, then in each round every configuration evaluates every configuration's rules over its own rows for the inputs refolded last, and every configuration takes in what it was sent and refolds, until a round moves nothing. The products are each configuration's, in list order.
    fn exchange(
        index: &SpecIndex,
        spellings: &Spellings,
        products: Vec<FixpointProduct>,
    ) -> Result<Vec<Exchanged>, String> {
        let mut held: Vec<Exchanged> = Vec::new();
        for product in products {
            let prepared = Prepared::new(product, None)?;
            let mut folds = prepared.unfolded();
            prepared.fold_inputs(index, &Imports::default(), None, &mut folds)?;
            held.push(Exchanged {
                prepared,
                imports: Imports::default(),
                folds,
            });
        }
        let indexed: Vec<SourceRows> = held
            .iter()
            .map(|config| SourceRows::of(&config.prepared.rows()))
            .collect();
        let mut published: Vec<SharedRules> = held
            .iter()
            .enumerate()
            .map(|(at, config)| SharedRules::of(spellings, at, &config.rules()))
            .collect();
        let mut refolded: Option<HashSet<Box<str>>> = None;
        for _round in 0..16 {
            let everyone: Vec<&SharedRules> = published.iter().collect();
            let mut mail: Vec<Vec<Vec<ForeignRow>>> = held
                .iter()
                .map(|_| held.iter().map(|_| Vec::new()).collect())
                .collect();
            for (source, config) in held.iter().enumerate() {
                let from = Source {
                    index,
                    spellings,
                    config: source,
                    rows: config.prepared.rows(),
                    indexed: &indexed[source],
                    chains: config.prepared.chains(),
                    published: &everyone,
                };
                for (receiver, rows) in crate::crossconfig::exports(&from, refolded.as_ref())
                    .into_iter()
                    .enumerate()
                {
                    mail[receiver][source] = rows;
                }
            }
            drop(everyone);
            let mut next: HashSet<Box<str>> = HashSet::default();
            for (receiver, batches) in mail.into_iter().enumerate() {
                let config = &mut held[receiver];
                let gained =
                    config
                        .imports
                        .absorb(spellings, receiver, &config.prepared.rows(), batches)?;
                if gained.is_empty() {
                    continue;
                }
                next.extend(
                    gained
                        .iter()
                        .map(|input| Box::from(&*spellings.canonical(receiver, input))),
                );
                let wanted: HashSet<Rc<str>> = gained.into_iter().collect();
                config.prepared.fold_inputs(
                    index,
                    &config.imports,
                    Some(&wanted),
                    &mut config.folds,
                )?;
                published[receiver] = SharedRules::of(spellings, receiver, &config.rules());
            }
            if next.is_empty() {
                return Ok(held);
            }
            refolded = Some(next);
        }
        Err("the exchange did not end".to_owned())
    }

    /// The spellings of a `default` and an `ss03` configuration over the fixture, where `ss03` unlocks a `qsMay` entry, so `qsMay` is a marker rune the two spell differently.
    fn default_and_ss03(index: &SpecIndex) -> Spellings {
        let ss03 = [fixtures::sym(index, "ss03")];
        Spellings::new(index, [("default", &[][..]), ("ss03", &ss03[..])])
    }

    /// The rows of one left that settle the way the boundary lefts do for `qsPea` in [`a_left_one_configuration_reaches_only_before_some_letters`]: before the run edge or a boundary glyph to `qsPea.f`, before `a` to `qsPea.n`, and before each of `more` to its outcome.
    fn pea_rows(bench: &Bench, left: &str, more: &[(&str, &str)]) -> Vec<TransitionRow> {
        let mut rows = vec![
            bench.row(
                ["qsPea", left, "#EDGE", "#NA", "#NA", "#NA", "qsPea.f"],
                0,
                false,
            ),
            bench.row(
                ["qsPea", left, "a", "#NA", "#NA", "#NA", "qsPea.n"],
                0,
                false,
            ),
        ];
        rows.extend(bench.edge_kin(["qsPea", left, "#EDGE", "#NA", "#NA", "#NA", "qsPea.f"]));
        for (right, outcome) in more {
            rows.push(bench.row(
                ["qsPea", left, right, "#NA", "#NA", "#NA", outcome],
                0,
                false,
            ));
        }
        rows
    }

    /// `default`'s and `ss03`'s products for [`a_left_one_configuration_reaches_only_before_some_letters`].
    fn tea_left_products(bench: &Bench) -> Vec<FixpointProduct> {
        let mut default_rows: Vec<TransitionRow> = Vec::new();
        let mut ss03_rows: Vec<TransitionRow> = Vec::new();
        for left in ["#EDGE", "space", "periodcentered", "uni200C"] {
            default_rows.extend(pea_rows(bench, left, &[("qsMay", "qsPea.m")]));
            ss03_rows.extend(pea_rows(bench, left, &[("qsMay", "qsPea.k")]));
        }
        default_rows.extend(pea_rows(bench, "qsTea.y", &[("qsMay", "qsPea.m")]));
        ss03_rows.extend(pea_rows(bench, "qsTea.y", &[]));
        let mut ss03 = bench.product(ss03_rows, Vec::new());
        ss03.config = "ss03".to_owned();
        vec![bench.product(default_rows, Vec::new()), ss03]
    }

    /// `default`'s and `ss03`'s products over two lefts, `qsMay.x` and `qsTea.y`, before `a` and then `b` or `c`. `ss03` reaches both lefts before both letters, and so does `default` when `default_c` names the lefts it reaches before `c`.
    fn two_left_products(bench: &Bench, default_c: &[&str]) -> Vec<FixpointProduct> {
        let rows = |c_lefts: &[&str]| {
            let mut rows = bench.boundary_block("qsPea", "a", "qsPea.n");
            for left in ["qsMay.x", "qsTea.y"] {
                rows.push(bench.row(["qsPea", left, "a", "b", "#NA", "#NA", "qsPea.x"], 0, false));
                if c_lefts.contains(&left) {
                    rows.push(bench.row(
                        ["qsPea", left, "a", "c", "#NA", "#NA", "qsPea.y"],
                        0,
                        false,
                    ));
                }
            }
            rows
        };
        let mut ss03 = bench.product(rows(&["qsMay.x", "qsTea.y"]), Vec::new());
        ss03.config = "ss03".to_owned();
        vec![bench.product(rows(default_c), Vec::new()), ss03]
    }

    /// The live fault's shape (·Et·Tea·See·At·May under the widened `ss05`): `default` reaches the left `qsTea.y` before every right slot, so it sits in `default`'s default block, whose rules ship after every committed rule; `ss03` reaches it only before a boundary or `a`, so it is a committed block there, with a fallback. Folded alone, `ss03`'s fallback answers `default`'s window before `qsMay` with its own outcome, and it would ship first. The exchange sends that window to `ss03` with `qsMay` as a tag naming `default`'s spelling, `ss03` folds a rule for it, and the rule's provenance names `default`. Nothing else moves: `ss03`'s answer for its own spelling of `qsMay` is a marker-copy rule that ships ahead of `default`'s fallback, and `default`'s rules answer every `ss03` window right or after `ss03`'s own rule.
    #[test]
    fn a_left_one_configuration_reaches_only_before_some_letters() {
        let bench = Bench::new();
        let spellings = default_and_ss03(&bench.index);
        let products = tea_left_products(&bench);
        let alone = fold_product(&bench.index, products[1].clone()).expect("ss03 folds alone");
        let window = ["qsPea", "qsTea.y", "qsMay@", "#NA", "#NA", "#NA"];
        let by_input = rules_by_input(&alone.decision.rules);
        let fired = first_match(&by_input, window).expect("the fallback matches");
        assert_eq!(&*alone.decision.rules[fired].outcome, "qsPea.f");

        let held = exchange(&bench.index, &spellings, products).expect("the exchange ends");
        assert!(held[0].imports.is_empty(), "default imports nothing");
        let imported: Vec<([&str; 6], &str, usize)> = held[1]
            .imports
            .rows()
            .iter()
            .map(|row| (row.key_text(), &*row.outcome, row.source))
            .collect();
        assert_eq!(imported, [(window, "qsPea.m", 0)]);
        let rules = held[1].rules();
        assert_overlaps_keep_their_order(&rules);
        let by_input = rules_by_input(&rules);
        let guard = &rules[first_match(&by_input, window).expect("a rule answers it")];
        assert_eq!(&*guard.outcome, "qsPea.m");
        assert_eq!(guard.backtrack.as_deref(), Some(&[Rc::from("qsTea.y")][..]));
        assert_eq!(guard.look1.as_deref(), Some(&[Rc::from("qsMay@")][..]));
        assert_eq!(
            guard.provenance.last().map(String::as_str),
            Some("window live in default")
        );
        let rows = held[1].prepared.rows();
        first_match_rows(&rows, &rules, None, 1, None)
            .expect_err("the guard rule is reached only by the imported window");
        first_match_rows_open(&rows, &rules, None, 1, None)
            .expect("ss03's own rows still get their outcomes");
    }

    /// `default`'s and `ss03`'s products for [`a_window_another_configuration_keeps_live_splits_a_block`].
    fn split_block_products(bench: &Bench) -> Vec<FixpointProduct> {
        let mut default_rows = bench.boundary_block("qsPea", "a", "qsPea.n");
        default_rows.push(bench.row(
            ["qsPea", "qsMay.x", "a", "b", "#NA", "#NA", "qsPea.x"],
            0,
            false,
        ));
        default_rows.push(bench.row(
            ["qsPea", "qsTea.y", "a", "b", "#NA", "#NA", "qsPea.x"],
            0,
            false,
        ));
        let mut ss03_rows = bench.boundary_block("qsPea", "a", "qsPea.n");
        ss03_rows.push(bench.row(
            ["qsPea", "qsMay.x", "a", "b", "#NA", "#NA", "qsPea.x"],
            0,
            false,
        ));
        ss03_rows.push(bench.row(
            ["qsPea", "qsMay.x", "a", "c", "#NA", "#NA", "qsPea.y"],
            0,
            false,
        ));
        let mut ss03 = bench.product(ss03_rows, Vec::new());
        ss03.config = "ss03".to_owned();
        vec![bench.product(default_rows, Vec::new()), ss03]
    }

    /// Two lefts one configuration keeps in one block, because its rows for both are the same, split once another configuration's window shows they differ there; the other configuration's window under the left whose rows it has the most of is not sent, because its own rule for it ships first. `default` ranks first in the emitter's fold, so its committed rule ships ahead of `ss03`'s in the same bucket.
    #[test]
    fn a_window_another_configuration_keeps_live_splits_a_block() {
        let bench = Bench::new();
        let spellings = default_and_ss03(&bench.index);
        let products = split_block_products(&bench);
        let window = ["qsPea", "qsMay.x", "a", "c", "#NA", "#NA"];
        let alone = fold_product(&bench.index, products[0].clone()).expect("default folds alone");
        let by_input = rules_by_input(&alone.decision.rules);
        let fired =
            &alone.decision.rules[first_match(&by_input, window).expect("the block matches")];
        assert_eq!(&*fired.outcome, "qsPea.x");
        assert_eq!(
            fired.backtrack.as_ref().map(Vec::len),
            Some(2),
            "both lefts share the block"
        );

        let held = exchange(&bench.index, &spellings, products).expect("the exchange ends");
        assert!(held[1].imports.is_empty(), "ss03 imports nothing");
        let imported: Vec<[&str; 6]> = held[0]
            .imports
            .rows()
            .iter()
            .map(ImportedRow::key_text)
            .collect();
        assert_eq!(imported, [window]);
        let rules = held[0].rules();
        assert_overlaps_keep_their_order(&rules);
        let by_input = rules_by_input(&rules);
        assert_eq!(
            &*rules[first_match(&by_input, window).expect("answered")].outcome,
            "qsPea.y"
        );
        let tea = &rules[first_match(&by_input, ["qsPea", "qsTea.y", "a", "b", "#NA", "#NA"])
            .expect("answered")];
        assert_eq!(
            tea.backtrack.as_deref(),
            Some(&[Rc::from("qsTea.y")][..]),
            "the block split"
        );
    }

    /// Two configurations that settle one shipped window differently, or a window of one that overlaps a window of the other through an open slot with a different outcome, cannot share one lookup, and the receiver's error names both.
    #[test]
    fn windows_two_configurations_settle_differently_are_refused() {
        let bench = Bench::new();
        let spellings = default_and_ss03(&bench.index);
        let products = |ss03_tail: Vec<TransitionRow>| {
            let mut default_rows = bench.boundary_block("qsPea", "a", "qsPea.n");
            default_rows.push(bench.row(
                ["qsPea", "qsMay.x", "a", "b", "z", "#NA", "qsPea.x"],
                0,
                false,
            ));
            default_rows.push(bench.row(
                ["qsPea", "qsMay.x", "a", "b", "w", "#NA", "qsPea.y"],
                0,
                false,
            ));
            let mut ss03_rows = bench.boundary_block("qsPea", "a", "qsPea.n");
            ss03_rows.extend(ss03_tail);
            let mut ss03 = bench.product(ss03_rows, Vec::new());
            ss03.config = "ss03".to_owned();
            vec![bench.product(default_rows, Vec::new()), ss03]
        };
        let same = products(vec![bench.row(
            ["qsPea", "qsMay.x", "a", "b", "z", "#NA", "qsPea.q"],
            0,
            false,
        )]);
        let error = exchange(&bench.index, &spellings, same)
            .err()
            .expect("one window, two outcomes");
        assert!(
            error.contains("one shipped lookup cannot give both"),
            "{error}"
        );
        assert!(
            error.contains("ss03") && error.contains("default"),
            "{error}"
        );
        let shallow = products(vec![bench.row(
            ["qsPea", "qsMay.x", "a", "b", "#NA", "#NA", "qsPea.y"],
            0,
            false,
        )]);
        let error = exchange(&bench.index, &spellings, shallow)
            .err()
            .expect("the open slot overlaps a window with another outcome");
        assert!(error.contains("the two overlap"), "{error}");
        assert!(error.contains("'z'"), "{error}");
    }

    /// A guard rule's certificate is the chain of the imported row in the configuration it is live in, completed, and kept when the window at the row's position, spelled in the receiver's labels, first-matches the rule there.
    #[test]
    fn a_guard_rule_s_certificate_comes_from_the_configuration_the_window_is_live_in() {
        let (index, product, _folded) = built();
        let spellings = default_and_ss03(&index);
        let prepared = Prepared::new(product, None).expect("the fixture prepares");
        let rows = prepared.rows();
        let row = (0..rows.len())
            .find(|&row| prepared.chains().dist()[row] > 0 && rows.key(row)[1].contains('.'))
            .expect("the fixture has a row a chain of letters reaches");
        let key = rows.key(row);
        let local: [Rc<str>; 6] = std::array::from_fn(|slot| {
            Rc::from(if slot == 1 {
                key[1].to_owned()
            } else {
                spellings.translate(0, 1, key[slot])
            })
        });
        let imported = ImportedRow {
            key: local.clone(),
            outcome: Rc::from("guarded"),
            source: 0,
            provenance: Vec::new(),
            joint: false,
            chain: certificate::fixed_tokens(&index, prepared.chains(), &rows, row)
                .expect("the fixture spells its rows"),
        };
        let guard = Rule {
            input_glyph: Rc::clone(&local[0]),
            backtrack: Some(vec![Rc::clone(&local[1])]),
            look1: None,
            look2: None,
            look3: None,
            look4: None,
            outcome: Rc::from("guarded"),
            provenance: Vec::new(),
            joint: false,
        };
        let rules = vec![guard];
        let by_input = rules_by_input(&rules);
        let mut options = WindowOptions::new(&index).expect("the fixture has valid options");
        let found = certificate::guard_certificate(
            &index,
            &mut options,
            &spellings,
            1,
            &by_input,
            0,
            &imported,
        )
        .expect("the guard is evaluated")
        .expect("and certified");
        assert_eq!(found[0], certificate::GUARD_MARKER);
        assert_eq!(found[1], "default");
        assert!(found.len() > 2);
        let no_chain = ImportedRow {
            chain: None,
            ..imported
        };
        assert!(
            certificate::guard_certificate(
                &index,
                &mut options,
                &spellings,
                1,
                &by_input,
                0,
                &no_chain
            )
            .expect("evaluated")
            .is_none()
        );
    }

    /// Asserts the fact the shipped order rests on, over one table's rules: when an earlier rule and a later rule of one input both match some window, the emitter's sort (`emit_gsub._ordered_settle_rules`, [`crate::crossconfig::bucket`]) never moves the later one ahead. A backtrack-free rule never precedes a backtrack rule it overlaps, a ZWNJ backtrack guard never follows a rule it overlaps that is not one, and between two rules of one status every lookahead slot the later one constrains, the earlier one constrains with the same class. So whatever labels count as marker copies, the later rule's bucket is never earlier.
    fn assert_overlaps_keep_their_order(rules: &[Rule]) -> usize {
        let zwnj = |rule: &Rule| {
            rule.backtrack
                .as_ref()
                .is_some_and(|members| members.iter().any(|member| &**member == "uni200C"))
        };
        let overlap = |one: &Rule, other: &Rule| {
            one.slots()
                .iter()
                .zip(other.slots())
                .all(|(left, right)| match (left, right) {
                    (Some(left), Some(right)) => left.iter().any(|member| right.contains(member)),
                    _ => true,
                })
        };
        let mut pairs = 0;
        for (_input, indexed) in rules_by_input(rules) {
            for (at, (_, earlier)) in indexed.iter().enumerate() {
                for (_, later) in &indexed[at + 1..] {
                    if !overlap(earlier, later) {
                        continue;
                    }
                    pairs += 1;
                    assert!(
                        earlier.backtrack.is_some() || later.backtrack.is_none(),
                        "{} precedes the backtrack rule {} it overlaps",
                        rule_repr(earlier),
                        rule_repr(later)
                    );
                    assert!(
                        zwnj(earlier) || !zwnj(later),
                        "{} precedes the ZWNJ guard {} it overlaps",
                        rule_repr(earlier),
                        rule_repr(later)
                    );
                    if earlier.backtrack.is_some() == later.backtrack.is_some()
                        && zwnj(earlier) == zwnj(later)
                    {
                        for (held, wider) in earlier.slots()[1..].iter().zip(&later.slots()[1..]) {
                            assert!(
                                wider.is_none() || held == wider,
                                "{} constrains a slot {} leaves open or holds otherwise",
                                rule_repr(later),
                                rule_repr(earlier)
                            );
                        }
                    }
                    assert!(
                        crate::crossconfig::bucket(earlier.slots().map(|slot| slot.as_deref()))
                            <= crate::crossconfig::bucket(
                                later.slots().map(|slot| slot.as_deref())
                            )
                    );
                }
            }
        }
        pairs
    }

    /// The fixture's tables, the ZWNJ lock's guards, and both exchanges' refolded tables keep every overlapping pair in an order the emitter's sort cannot invert.
    #[test]
    fn the_emitter_s_sort_never_moves_an_overlapping_rule_ahead() {
        let (index, product, folded) = built();
        let mut pairs = assert_overlaps_keep_their_order(&folded.decision.rules);
        let ss03 = enumerate_transitions(&index, &[fixtures::sym(&index, "ss03")], DEFAULT_MODES)
            .expect("the fixture enumerates under ss03");
        pairs += assert_overlaps_keep_their_order(
            &fold_product(&index, ss03)
                .expect("and folds")
                .decision
                .rules,
        );
        drop(product);
        let bench = Bench::new();
        pairs += assert_overlaps_keep_their_order(
            &fold_product(&bench.index, zwnj_lock(&bench, "qsIt"))
                .expect("the lock folds")
                .decision
                .rules,
        );
        assert!(
            pairs > 0,
            "the fixture stopped overlapping rules, so this checks nothing"
        );
    }

    /// A committed block with no fallback leaves the rows its rules do not answer to the default block's rules, which is right for its own rows (they settle to the input, or the replay would fail) but not for an imported row the default rules would answer otherwise. The block then ends with an identity catch-all over its backtrack, which answers that row and every other row falling through the block, and takes its provenance from the imported row.
    #[test]
    fn an_imported_row_falling_through_a_block_without_a_fallback_gets_an_identity_catch_all() {
        let bench = Bench::new();
        let spellings = default_and_ss03(&bench.index);
        let mut rows = bench.boundary_block("qsPea", "b", "qsPea.n");
        rows.push(bench.row(
            ["qsPea", "qsMay.x", "a", "#NA", "#NA", "#NA", "qsPea.x"],
            0,
            false,
        ));
        let prepared = Prepared::new(bench.product(rows, Vec::new()), None).expect("prepares");
        let mut imports = Imports::default();
        let foreign = ForeignRow {
            key: ["qsPea", "qsMay.x", "b", "#NA", "#NA", "#NA"].map(str::to_owned),
            outcome: "qsPea".to_owned(),
            source: 1,
            provenance: vec!["qsMay.yaml".to_owned()],
            joint: false,
            chain: None,
        };
        let gained = imports
            .absorb(
                &spellings,
                0,
                &prepared.rows(),
                vec![Vec::new(), vec![foreign]],
            )
            .expect("nothing overlaps it");
        assert_eq!(gained, [Rc::from("qsPea")]);
        let mut folds = prepared.unfolded();
        prepared
            .fold_inputs(&bench.index, &imports, None, &mut folds)
            .expect("folds");
        let rules = Prepared::rules(&folds);
        let by_input = rules_by_input(&rules);
        let caught = &rules[first_match(&by_input, imports.rows()[0].key_text()).expect("caught")];
        assert_eq!(&*caught.outcome, "qsPea");
        assert_eq!(
            caught.backtrack.as_deref(),
            Some(&[Rc::from("qsMay.x")][..])
        );
        assert!(caught.slots()[1..].iter().all(|slot| slot.is_none()));
        assert_eq!(
            caught.provenance,
            ["qsMay.yaml", "window live in ss03", "identity catch-all"]
        );
        first_match_rows_open(&prepared.rows(), &rules, None, 1, None)
            .expect("the own rows keep their outcomes");
        let alone = fold_product(&bench.index, {
            let mut rows = bench.boundary_block("qsPea", "b", "qsPea.n");
            rows.push(bench.row(
                ["qsPea", "qsMay.x", "a", "#NA", "#NA", "#NA", "qsPea.x"],
                0,
                false,
            ));
            bench.product(rows, Vec::new())
        })
        .expect("folds alone");
        assert_eq!(
            alone.decision.rules.len() + 1,
            rules.len(),
            "the catch-all is the only new rule"
        );
    }

    /// The rows of each configuration that the font's one settlement lookup answers otherwise than the configuration's table: every configuration's rules in canonical labels ([`SharedRules`]), kept at their first occurrence in [`Spellings::rank`] order and table order and stably sorted by [`crate::crossconfig::bucket`], as `emit_gsub._fold_rules` and `_ordered_settle_rules` ship them, then first-matched against every own row of every configuration in canonical labels.
    fn shipped_misses(
        spellings: &Spellings,
        configs: &[(LabelRows<'_>, Vec<Rule>)],
    ) -> Vec<String> {
        let published: Vec<SharedRules> = configs
            .iter()
            .enumerate()
            .map(|(at, (_rows, rules))| SharedRules::of(spellings, at, rules))
            .collect();
        let mut order: Vec<usize> = (0..configs.len()).collect();
        order.sort_by_key(|config| spellings.rank(*config));
        let mut shipped: HashMap<&str, Vec<&crate::crossconfig::SharedRule>> = HashMap::default();
        let mut seen: HashSet<&str> = HashSet::default();
        for config in order {
            for (input, rules) in &published[config].by_input {
                for rule in rules {
                    if seen.insert(&rule.key) {
                        shipped.entry(input).or_default().push(rule);
                    }
                }
            }
        }
        for rules in shipped.values_mut() {
            rules.sort_by_key(|rule| rule.bucket);
        }
        let mut misses: Vec<String> = Vec::new();
        for (config, (rows, _rules)) in configs.iter().enumerate() {
            for row in 0..rows.len() {
                let key = rows.key(row);
                let canonical: [String; 6] =
                    key.map(|label| spellings.canonical(config, label).into_owned());
                let outcome = rows.outcome(row);
                let wanted = if **outcome == *key[0] {
                    spellings.canonical(config, outcome).into_owned()
                } else {
                    outcome.to_string()
                };
                let fired = shipped
                    .get(canonical[0].as_str())
                    .and_then(|rules| {
                        rules.iter().find(|rule| {
                            rule.slots.iter().zip(&canonical[1..]).all(|(slot, label)| {
                                slot.as_ref().is_none_or(|members| {
                                    members.iter().any(|member| **member == **label)
                                })
                            })
                        })
                    })
                    .map_or_else(|| canonical[0].clone(), |rule| rule.outcome.to_string());
                if fired != wanted {
                    misses.push(format!(
                        "{}: {} ships as {fired}, not {wanted}",
                        spellings.tokens()[config],
                        key_repr(key)
                    ));
                }
            }
        }
        misses
    }

    /// Each configuration's own rows and its rules folded from them alone, with nothing imported.
    fn folded_alone(products: Vec<FixpointProduct>) -> Vec<Prepared> {
        products
            .into_iter()
            .map(|product| Prepared::new(product, None).expect("the product prepares"))
            .collect()
    }

    /// A fixture's products, one per configuration in list order.
    type Products = fn(&Bench) -> Vec<FixpointProduct>;

    /// The property the exchange exists for: in the order the font ships every configuration's rules, every own row of every configuration first-matches its own outcome. Each fixture breaks it when each configuration folds alone, and the exchange restores it.
    #[test]
    fn the_shipped_order_answers_every_configuration_s_rows_once_they_exchange() {
        let bench = Bench::new();
        let spellings = default_and_ss03(&bench.index);
        let fixtures: [(&str, Products); 3] = [
            ("tea_left_products", tea_left_products),
            ("split_block_products", split_block_products),
            ("two_left_products", |bench| {
                two_left_products(bench, &["qsTea.y"])
            }),
        ];
        for (name, products) in fixtures {
            let alone = folded_alone(products(&bench));
            let configs: Vec<(LabelRows<'_>, Vec<Rule>)> = alone
                .iter()
                .map(|prepared| {
                    let mut folds = prepared.unfolded();
                    prepared
                        .fold_inputs(&bench.index, &Imports::default(), None, &mut folds)
                        .expect("each configuration folds alone");
                    (prepared.rows(), Prepared::rules(&folds))
                })
                .collect();
            assert!(
                !shipped_misses(&spellings, &configs).is_empty(),
                "{name}: folded alone, the shipped order already answers every row"
            );
            let held = exchange(&bench.index, &spellings, products(&bench))
                .unwrap_or_else(|error| panic!("{name}: {error}"));
            let configs: Vec<(LabelRows<'_>, Vec<Rule>)> = held
                .iter()
                .map(|config| (config.prepared.rows(), config.rules()))
                .collect();
            assert_eq!(
                shipped_misses(&spellings, &configs),
                Vec::<String>::new(),
                "{name}"
            );
        }
    }

    /// Two lefts `default` folds apart until a window `ss03` keeps live arrives for one of them, after which both share one block. The window's key has an own row under the other left, so that row, not the imported one, decides the rule's provenance, and the replay reduction keeps that left, so an own row reaches the rule and its certificate is an ordinary one rather than a guard's.
    #[test]
    fn a_block_an_import_merges_reads_its_rule_from_another_left_s_own_row() {
        let bench = Bench::new();
        let spellings = default_and_ss03(&bench.index);
        let held = exchange(
            &bench.index,
            &spellings,
            two_left_products(&bench, &["qsTea.y"]),
        )
        .expect("the exchange ends");
        let window = ["qsPea", "qsMay.x", "a", "c", "#NA", "#NA"];
        let imported: Vec<[&str; 6]> = held[0]
            .imports
            .rows()
            .iter()
            .map(ImportedRow::key_text)
            .collect();
        assert_eq!(imported, [window]);
        let rules = held[0].rules();
        let by_input = rules_by_input(&rules);
        let at = first_match(&by_input, window).expect("answered");
        let rule = &rules[at];
        assert_eq!(&*rule.outcome, "qsPea.y");
        assert_eq!(
            rule.backtrack.as_deref(),
            Some(&[Rc::from("qsMay.x"), Rc::from("qsTea.y")][..]),
            "the import merged the two lefts"
        );
        assert!(
            !rule
                .provenance
                .iter()
                .any(|pointer| pointer.starts_with("window live in")),
            "{:?}",
            rule.provenance
        );
        let mut lefts: ReplayLefts = HashMap::default();
        for fold in held[0].folds.iter().flatten() {
            lefts
                .entry(Rc::from("qsPea"))
                .or_default()
                .extend(fold.replay_lefts.iter().cloned());
        }
        assert!(
            lefts["qsPea"].contains("qsTea.y"),
            "the lending left is replayed"
        );
        let rows = held[0].prepared.rows();
        let first_rows = first_match_rows_open(&rows, &rules, Some(&lefts), 1, None)
            .expect("the own rows keep their outcomes");
        assert!(
            !first_rows[at].is_empty(),
            "an own row reaches the rule, so its certificate is not a guard's"
        );
    }

    /// One committed left, `qsMay.x`, whose group fallback settles to `qsPea.f`, and whose rows past `a` settle to the bare input before a boundary at slot `depth` (2, 3 or 4) and to `qsPea.x` before a letter there. `group_outcome` replaces `qsPea.f` before a boundary right after the left. The default block's near letter is `z`, so its rules never answer the left's rows.
    fn identity_boundary_rows(
        bench: &Bench,
        depth: usize,
        group_outcome: &str,
    ) -> Vec<TransitionRow> {
        let mut rows = bench.boundary_block("qsPea", "z", "qsPea.n");
        let edge = [
            "qsPea",
            "qsMay.x",
            "#EDGE",
            "#NA",
            "#NA",
            "#NA",
            group_outcome,
        ];
        rows.push(bench.row(edge, 0, false));
        rows.extend(bench.edge_kin(edge));
        let letters = ["a", "b", "c", "d"];
        let mut boundary = ["qsPea", "qsMay.x", "#NA", "#NA", "#NA", "#NA", "qsPea"];
        let mut letter = ["qsPea", "qsMay.x", "#NA", "#NA", "#NA", "#NA", "qsPea.x"];
        boundary[2..=depth].copy_from_slice(&letters[..depth - 1]);
        letter[2..=depth].copy_from_slice(&letters[..depth - 1]);
        boundary[depth + 1] = "#EDGE";
        letter[depth + 1] = letters[depth - 1];
        rows.push(bench.row(boundary, 0, false));
        rows.extend(bench.edge_kin(boundary));
        rows.push(bench.row(letter, 0, false));
        rows
    }

    /// An identity outcome before a boundary at a deeper slot is kept as an identity guard, beside the slot's identity fallback, when the nearest enclosing fallback has another outcome, so that fallback does not answer the boundary rows; with the enclosing fallback an identity too, both are left out and the rows fall through to the bare input. The replay over every row checks either way that every row gets its outcome.
    #[test]
    fn an_identity_boundary_slot_is_guarded_only_under_a_fallback_with_another_outcome() {
        let bench = Bench::new();
        for depth in 2..=4 {
            let guarded = fold_product(
                &bench.index,
                bench.product(identity_boundary_rows(&bench, depth, "qsPea.f"), Vec::new()),
            )
            .unwrap_or_else(|error| panic!("slot {depth}: {error}"));
            let open = fold_product(
                &bench.index,
                bench.product(identity_boundary_rows(&bench, depth, "qsPea"), Vec::new()),
            )
            .unwrap_or_else(|error| panic!("slot {depth} without a fallback: {error}"));
            let boundary_slot = |rule: &&Rule| {
                rule.backtrack.as_deref() == Some(&[Rc::from("qsMay.x")][..])
                    && rule.slots()[depth].as_ref().is_some_and(|members| {
                        members
                            .iter()
                            .map(|member| &**member)
                            .eq(BOUNDARY_LOOKAHEAD_CLASS)
                    })
            };
            let kept: Vec<&Rule> = guarded
                .decision
                .rules
                .iter()
                .filter(boundary_slot)
                .collect();
            assert_eq!(kept.len(), 1, "slot {depth}");
            assert_eq!(&*kept[0].outcome, "qsPea", "slot {depth}");
            assert!(
                !open.decision.rules.iter().any(|rule| boundary_slot(&rule)),
                "slot {depth}: with no fallback to guard against, the identity rule is left out"
            );
            assert_eq!(
                guarded.decision.identity_guard_rules,
                open.decision.identity_guard_rules + 2,
                "slot {depth}: the boundary rule and the slot's fallback are the guards"
            );
        }
    }
}
