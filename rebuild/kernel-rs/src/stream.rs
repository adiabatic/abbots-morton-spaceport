//! The transitions stream: one configuration's whole fixpoint product in the `ams-m1-transitions/1` spelling, byte-identical to what `rebuild/pipeline/kernel_io.py`'s `write_transitions` writes. That module is the binding contract for the format — the head's key order, the cell vocabulary's sort, which absences spell `null` and which spell the empty string, and the sentence a cell outside the head raises are all its, not this crate's — and where the two disagree it is right.
//!
//! What lands here rather than in [`crate::emit`] is the return leg of the boundary: `emit` echoes a spec that was read, this writes what the fixpoint produced. The escaping is shared rather than restated, because two canonical-JSON escapers are one escaper plus one silent divergence.
//!
//! The stream this module builds is uncompressed. Python's writer gzips it with a zeroed stamp into a file; the kernel writes the identical bytes to stdout and the harness that runs it does the gzipping, so the crate carries no compressor and the boundary stays one format rather than two.
//!
//! A cell is spelled once, in the head, and every row names its settled cell by its seat there, which is why the emitter rather than the fixpoint owns the cell sort: a seat is an index into [`cell_key`] order and nothing else. A row naming a cell the product does not count among its reachable cells is refused here, at the boundary, exactly as `write_transitions` raises `PartitionError` rather than letting the fold meet the disagreement later.
//!
//! The product's own seats are a different table from the head's. A row holds its settled record and its left's as a [`SettledSeat`] apiece into [`FixpointProduct::seats`], in the order the fixpoint first reached each record, and that table never crosses the boundary: the writer resolves a row's seat to the record, and the record's cell to the head's seat, and spells only the latter. Two tables rather than one because they answer different questions — the head's is the cell vocabulary in `_cell_key` order, a contract Python reads, and the product's is every distinct settled triple, an economy the crate keeps to itself. A row's provenance is seated the same way, as a [`NotesSeat`] into [`FixpointProduct::notes`], and that table stays on this side of the boundary too: the writer spells the list the seat names, in the order the trace left it.

use std::collections::BTreeSet;
use std::fmt::Write as _;
use std::io::Write as _;
use std::rc::Rc;

use crate::emit::{escape_into, json_string};
use crate::hash::{HashMap, HashSet};
use crate::index::SpecIndex;
use crate::model::Sym;
use crate::types::{CellId, NotesSeat, Settled, SettledSeat, adjustment_text};

/// The marker the head line carries, `kernel_io.TRANSITIONS_FORMAT`. A stream naming anything else is another format and not a newer spelling of this one.
pub const TRANSITIONS_FORMAT: &str = "ams-m1-transitions/1";

/// One window label's id in the [`LabelPool`]: the position its spelling was minted at, and nothing about the text. Within one pool, two ids are equal exactly when their spellings are, because the pool mints each spelling once; their order is minting order, which no consumer reads — the product's lexicographic order is reached through [`LabelPool::ranks`] instead.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Label(pub(crate) u32);

/// Product-local spellings shared by the fixpoint, fold, and decision table. Rows retain compact IDs; only consumers that need text resolve them through this pool. IDs from different pools are unrelated, and lexical ordering uses spelling ranks rather than minting order.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct LabelPool {
    ids: HashMap<Rc<str>, Label>,
    texts: Vec<Rc<str>>,
}

impl LabelPool {
    pub(crate) fn len(&self) -> usize {
        self.texts.len()
    }
    pub(crate) fn capacity(&self) -> usize {
        self.texts.capacity()
    }

    /// The pool's id for this spelling, minting one only where the pool has none.
    pub(crate) fn intern(&mut self, text: &str) -> Label {
        if let Some(&found) = self.ids.get(text) {
            return found;
        }
        self.mint(Rc::from(text))
    }

    /// The same for a spelling the caller had to build anyway, so that a miss reuses the buffer rather than copying it a second time.
    pub(crate) fn intern_owned(&mut self, text: String) -> Label {
        if let Some(&found) = self.ids.get(text.as_str()) {
            return found;
        }
        self.mint(Rc::from(text))
    }

    /// A spelling the pool has never seen, seated at the next id.
    fn mint(&mut self, shared: Rc<str>) -> Label {
        let id = Label(
            u32::try_from(self.texts.len())
                .expect("a configuration's distinct labels number in the tens of thousands, nowhere near the u32 ids can seat"),
        );
        self.ids.insert(Rc::clone(&shared), id);
        self.texts.push(shared);
        id
    }

    /// The spelling one id was minted for, as a shared handle.
    pub(crate) fn text(&self, label: Label) -> &Rc<str> {
        &self.texts[label.0 as usize]
    }

    /// One key's six spellings, in the textual `Debug` form diagnostics use to identify a window.
    pub(crate) fn spelled(&self, key: &[Label; 6]) -> [&str; 6] {
        key.map(|label| &**self.text(label))
    }

    /// Each id's position among every spelling the pool holds, sorted as text: `ranks[id]` compares as the spelling does, so ordering rows by their rank tuple is ordering them by their key tuple without touching a string.
    pub(crate) fn ranks(&self) -> Vec<u32> {
        let mut by_text: Vec<usize> = (0..self.texts.len()).collect();
        by_text.sort_unstable_by(|&left, &right| self.texts[left].cmp(&self.texts[right]));
        let mut ranks = vec![0u32; self.texts.len()];
        for (rank, id) in by_text.into_iter().enumerate() {
            ranks[id] = u32::try_from(rank).expect("a rank is one of the ids it ranks");
        }
        ranks
    }
}

/// Everything one configuration's fixpoint produces, `table.FixpointProduct`. The rows arrive already sorted on [`TransitionRow::key`] — that order is the product's own and the stream keeps it, because `assemble_tables` expands and flags in it.
///
/// Three of the fields are `frozenset`s and a `Mapping` on the Python side and vectors here, so their canonical order is the emitter's business rather than the fixpoint's: `cells` and `cited_provenance` are sorted (and repeats collapsed, which is what a frozenset does to them) and `deep_classes` is sorted by token. `deep_classes` is empty at label grain and at every grain of the pinned world, and the emitter spells it either way.
///
/// `seats` and `notes` have no Python counterpart at all: they are the tables every row's [`SettledSeat`]s and [`NotesSeat`] index — one entry per distinct settled record, and one per distinct provenance list, each in the order the fixpoint first reached it — and [`FixpointProduct::settled`], [`FixpointProduct::left_settled`] and [`FixpointProduct::provenance`] are how a row's three are read back. Python's `Transition` holds all three by value. The label pool and settled-seat outcome table remain local to the product and pass intact into the decision table.
#[derive(Clone, Debug, Default, Eq)]
pub struct FixpointProduct {
    pub config: String,
    pub transitions: Vec<TransitionRow>,
    pub labels: LabelPool,
    pub outcomes: Vec<Label>,
    pub deep_classes: Vec<(String, Vec<String>)>,
    pub cited_provenance: Vec<String>,
    pub cells: Vec<CellId>,
    pub seats: Vec<Settled>,
    pub notes: Vec<Vec<String>>,
}

/// Product equality resolves labels through each product's pool; numeric IDs from separate enumerations do not share an identity.
impl PartialEq for FixpointProduct {
    fn eq(&self, other: &Self) -> bool {
        self.config == other.config
            && self.deep_classes == other.deep_classes
            && self.cited_provenance == other.cited_provenance
            && self.cells == other.cells
            && self.seats == other.seats
            && self.notes == other.notes
            && self.transitions.len() == other.transitions.len()
            && self
                .transitions
                .iter()
                .zip(&other.transitions)
                .all(|(left, right)| {
                    left.key(&self.labels) == right.key(&other.labels)
                        && self.outcome(left) == other.outcome(right)
                        && left.settled == right.settled
                        && left.left_settled == right.left_settled
                        && left.provenance == right.provenance
                        && left.prospect == right.prospect
                        && left.joint == right.joint
                })
    }
}

impl FixpointProduct {
    /// The outcome belongs to the settled seat, shared by every row that reaches it.
    pub fn outcome(&self, row: &TransitionRow) -> &Rc<str> {
        self.labels.text(self.outcomes[row.settled.index()])
    }

    /// The record one row settled into.
    pub fn settled(&self, row: &TransitionRow) -> &Settled {
        &self.seats[row.settled.index()]
    }

    /// The record the row's left settled into — present for a letter left and for the boundary cells the fold records, absent otherwise.
    pub fn left_settled(&self, row: &TransitionRow) -> Option<&Settled> {
        row.left_settled.map(|seat| &self.seats[seat.index()])
    }

    /// The pointers one row's trace noted, in the first-seen order the rule fold joins them in.
    pub fn provenance(&self, row: &TransitionRow) -> &[String] {
        &self.notes[row.provenance.index()]
    }
}

/// One window's six product-local label IDs and seated trace data. Resolve labels through the owning product's pool and the outcome through its settled-seat table. Equality and debug IDs are meaningful only within that pool; external ordering and diagnostics use resolved spellings.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransitionRow {
    pub input_glyph: Label,
    pub left: Label,
    pub right1: Label,
    pub right2: Label,
    pub right3: Label,
    pub right4: Label,
    pub settled: SettledSeat,
    pub left_settled: Option<SettledSeat>,
    pub provenance: NotesSeat,
    pub prospect: i8,
    pub joint: bool,
}

impl TransitionRow {
    /// The six labels that key this row, `table.Window.key`: the order the product is sorted in and the tuple every raise names the offending row by. An array rather than a tuple so that comparing two of them is the one lexicographic string comparison Python's tuple comparison is.
    pub fn key<'a>(&self, labels: &'a LabelPool) -> [&'a str; 6] {
        labels.spelled(&self.labels())
    }

    pub fn labels(&self) -> [Label; 6] {
        [
            self.input_glyph,
            self.left,
            self.right1,
            self.right2,
            self.right3,
            self.right4,
        ]
    }
}

/// What [`cell_key`] returns: `table._cell_key`'s tuple, resolved. Named so the sort reads as one comparison rather than five, and so the signature stays inside clippy's complexity budget.
pub type CellKey = (String, String, String, String, Vec<String>);

/// The order the head's cell vocabulary is sorted in, `table._cell_key`: the rune, the stance, the two heights with an absent side spelled as the empty string rather than dropped, and the adjustment tokens in their own order. Every component is the resolved string, never the symbol — a `Sym` sorts in the order the dump happened to mention names in, which is not an order Python has.
pub fn cell_key(index: &SpecIndex, cell: &CellId) -> CellKey {
    (
        index.resolve(cell.rune).to_owned(),
        index.resolve(cell.stance).to_owned(),
        cell.entry
            .map_or_else(String::new, |height| index.resolve(height).to_owned()),
        cell.exit
            .map_or_else(String::new, |height| index.resolve(height).to_owned()),
        cell.adjustments
            .iter()
            .map(|token| adjustment_text(index, *token))
            .collect(),
    )
}

/// The token the no-feature configuration is spelled by, `model.feature_config_token`'s `"default"`. It is a filename component and a stream head's `config` before it is anything else, so it is named rather than spelled twice.
pub const DEFAULT_CONFIG: &str = "default";

/// The configuration one feature set names, `model.feature_config_token`: the enabled sets sorted and joined with `+`, or `default` when nothing is enabled. This is the `config` field of the product and the `<config>` of every artifact filename, so it is a name two builds have to agree on letter for letter.
///
/// Sorting is on the resolved string, as everywhere, and a name handed in twice counts once — the declared parameter is a set at every Python call site. Taking names rather than symbols is what lets a command line's own spelling be checked against the canonical one before any spec has been read.
pub fn config_token<'a>(features: impl IntoIterator<Item = &'a str>) -> String {
    let enabled: BTreeSet<&str> = features.into_iter().collect();
    if enabled.is_empty() {
        return DEFAULT_CONFIG.to_owned();
    }
    enabled.into_iter().collect::<Vec<&str>>().join("+")
}

/// [`config_token`] over symbols, which is how everything holding a resolved feature set names its configuration.
pub fn feature_config_token(index: &SpecIndex, features: impl IntoIterator<Item = Sym>) -> String {
    config_token(features.into_iter().map(|feature| index.resolve(feature)))
}

/// Why a product did not reach the sink: the emitter refused it, or the sink would not take the bytes. Told apart because the two blame different things, exactly as the fan-out's own failure does.
#[derive(Debug)]
pub enum WriteFailure {
    /// The product's rows and its cells disagree, in `write_transitions`'s own sentence.
    Refused(String),
    /// The sink the stream was being written to would not take it.
    Sink(std::io::Error),
}

/// One product written to `sink` as the whole stream, `kernel_io.write_transitions` without the gzip: the `# ams-m1-transitions/1<tab><head json>` line, then one compact JSON array per transition in the product's own order, every line newline-terminated.
///
/// Every row's cells are seated before the first byte is written, which is what keeps `write_transitions`'s promise that nothing lands on the sink for a product whose rows and cells disagree. The refusal is that function's `PartitionError` sentence verbatim, tuple reprs included. A row's settled cell is checked before its left-settled one because that is the order Python's list literal evaluates its two `seated` calls in, so a row missing both is named by its settled cell.
///
/// The bytes go out a line at a time through one reused buffer rather than through a single `String` of the whole stream: a configuration's stream is hundreds of megabytes, and holding it entire — with the doubling transient a growing `String` carries — is the emitter's whole memory cost for no benefit at all.
pub fn write_transitions(
    index: &SpecIndex,
    product: &FixpointProduct,
    sink: &mut dyn std::io::Write,
) -> Result<(), WriteFailure> {
    let mut cells: Vec<(CellKey, &CellId)> = product
        .cells
        .iter()
        .map(|cell| (cell_key(index, cell), cell))
        .collect();
    cells.sort_by(|left, right| left.0.cmp(&right.0));
    // A whole-list dedup rather than the adjacent-only one: equal cells always sort together, but the sort key is the label view, so only injectivity of `cell_key` would make adjacency sufficient — and that premise belongs to Python's `_cell_key`, not to this emitter.
    let mut counted: HashSet<&CellId> = HashSet::default();
    cells.retain(|(_, cell)| counted.insert(*cell));
    let seats: HashMap<&CellId, usize> = cells
        .iter()
        .enumerate()
        .map(|(seat, (_, cell))| (*cell, seat))
        .collect();

    for row in &product.transitions {
        seat_of(
            index,
            &seats,
            product.settled(row),
            row.key(&product.labels),
            "settles into",
        )
        .map_err(WriteFailure::Refused)?;
        if let Some(left) = product.left_settled(row) {
            seat_of(
                index,
                &seats,
                left,
                row.key(&product.labels),
                "carries the left-settled cell",
            )
            .map_err(WriteFailure::Refused)?;
        }
    }

    let mut out = std::io::BufWriter::with_capacity(1 << 20, sink);
    let mut line = String::new();
    line.push_str("# ");
    line.push_str(TRANSITIONS_FORMAT);
    line.push('\t');
    head_into(&mut line, index, product, &cells);
    line.push('\n');
    out.write_all(line.as_bytes()).map_err(WriteFailure::Sink)?;
    for row in &product.transitions {
        line.clear();
        row_into(&mut line, index, &seats, product, row).map_err(WriteFailure::Refused)?;
        line.push('\n');
        out.write_all(line.as_bytes()).map_err(WriteFailure::Sink)?;
    }
    out.flush().map_err(WriteFailure::Sink)
}

/// [`write_transitions`] into a `String`, which is what a test that wants to read the whole stream back asks for. Nothing on the shipping path builds one: a configuration's stream is hundreds of megabytes.
pub fn emit_transitions(index: &SpecIndex, product: &FixpointProduct) -> Result<String, String> {
    let mut bytes: Vec<u8> = Vec::new();
    match write_transitions(index, product, &mut bytes) {
        Ok(()) => Ok(String::from_utf8(bytes).expect("the emitter writes text")),
        Err(WriteFailure::Refused(complaint)) => Err(complaint),
        Err(WriteFailure::Sink(error)) => {
            unreachable!("a growing byte buffer cannot refuse a write: {error}")
        }
    }
}

/// The head object, its four keys in the order Python's dict literal inserts them — `config`, `cells`, `deep_classes`, `cited_provenance` — with the set-valued ones sorted here rather than by whoever produced them. A `deep_classes` entry sorts on the whole pair, as `sorted(mapping.items())` does, which is the same order as by token alone for the unique tokens a map can hold.
fn head_into(
    out: &mut String,
    index: &SpecIndex,
    product: &FixpointProduct,
    cells: &[(CellKey, &CellId)],
) {
    let cells: Vec<String> = cells
        .iter()
        .map(|(_, cell)| cell_json(index, cell))
        .collect();
    let mut classes: Vec<&(String, Vec<String>)> = product.deep_classes.iter().collect();
    classes.sort();
    let classes: Vec<String> = classes
        .iter()
        .map(|(token, members)| format!("[{},{}]", json_string(token), strings_json(members)))
        .collect();
    let mut cited: Vec<&str> = product
        .cited_provenance
        .iter()
        .map(String::as_str)
        .collect();
    cited.sort_unstable();
    cited.dedup();
    let cited: Vec<String> = cited.iter().map(|pointer| json_string(pointer)).collect();
    out.push_str(&format!(
        "{{\"config\":{},\"cells\":[{}],\"deep_classes\":[{}],\"cited_provenance\":[{}]}}",
        json_string(&product.config),
        cells.join(","),
        classes.join(","),
        cited.join(",")
    ));
}

/// One cell as the head spells it: the rune, the stance, the two heights — `null` here for an absent side, where [`cell_key`] uses the empty string — and the adjustment tokens.
fn cell_json(index: &SpecIndex, cell: &CellId) -> String {
    let adjustments: Vec<String> = cell
        .adjustments
        .iter()
        .map(|token| json_string(&adjustment_text(index, *token)))
        .collect();
    format!(
        "[{},{},{},{},[{}]]",
        json_string(index.resolve(cell.rune)),
        json_string(index.resolve(cell.stance)),
        height_json(index, cell.entry),
        height_json(index, cell.exit),
        adjustments.join(",")
    )
}

/// One row as the body spells it, appended to the caller's buffer: the six window labels and the outcome, the settled triple, the left-settled triple or `null`, the joint flag, the prospect, and the provenance in the first-seen order the rule fold joins pointers in.
fn row_into(
    out: &mut String,
    index: &SpecIndex,
    seats: &HashMap<&CellId, usize>,
    product: &FixpointProduct,
    row: &TransitionRow,
) -> Result<(), String> {
    out.push('[');
    for label in row.key(&product.labels) {
        escape_into(out, label);
        out.push(',');
    }
    escape_into(out, product.outcome(row));
    out.push(',');
    settled_into(
        out,
        index,
        seats,
        product.settled(row),
        row.key(&product.labels),
        "settles into",
    )?;
    out.push(',');
    match product.left_settled(row) {
        Some(left) => settled_into(
            out,
            index,
            seats,
            left,
            row.key(&product.labels),
            "carries the left-settled cell",
        )?,
        None => out.push_str("null"),
    }
    out.push_str(if row.joint { ",true," } else { ",false," });
    let _ = write!(out, "{}", row.prospect);
    out.push(',');
    strings_into(out, product.provenance(row));
    out.push(']');
    Ok(())
}

/// One settled record as a row carries it: the cell's seat in the head, the seam it committed, and the connector pixels on that seam.
fn settled_into(
    out: &mut String,
    index: &SpecIndex,
    seats: &HashMap<&CellId, usize>,
    settled: &Settled,
    key: [&str; 6],
    relation: &str,
) -> Result<(), String> {
    let seat = seat_of(index, seats, settled, key, relation)?;
    out.push('[');
    let _ = write!(out, "{seat}");
    out.push(',');
    height_into(out, index, settled.seam);
    out.push(',');
    let _ = write!(out, "{}", settled.extension);
    out.push(']');
    Ok(())
}

/// One settled cell's seat in the head. A cell with no seat is the boundary's own refusal, in `write_transitions`'s sentence — the row named by its six-label key and the cell by its `_cell_key` tuple, both in Python's repr.
fn seat_of(
    index: &SpecIndex,
    seats: &HashMap<&CellId, usize>,
    settled: &Settled,
    key: [&str; 6],
    relation: &str,
) -> Result<usize, String> {
    seats.get(&settled.cell).copied().ok_or_else(|| {
        format!(
            "the transition {} {relation} {}, which the product does not count among its reachable cells",
            key_repr(key),
            cell_key_repr(&cell_key(index, &settled.cell))
        )
    })
}

fn height_into(out: &mut String, index: &SpecIndex, height: Option<Sym>) {
    match height {
        Some(height) => escape_into(out, index.resolve(height)),
        None => out.push_str("null"),
    }
}

fn strings_into(out: &mut String, values: &[String]) {
    out.push('[');
    for (seat, value) in values.iter().enumerate() {
        if seat > 0 {
            out.push(',');
        }
        escape_into(out, value);
    }
    out.push(']');
}

fn height_json(index: &SpecIndex, height: Option<Sym>) -> String {
    match height {
        Some(height) => json_string(index.resolve(height)),
        None => "null".to_owned(),
    }
}

fn strings_json(values: &[String]) -> String {
    let quoted: Vec<String> = values.iter().map(|value| json_string(value)).collect();
    format!("[{}]", quoted.join(","))
}

pub(crate) fn key_repr(key: [&str; 6]) -> String {
    python_tuple(&key.map(python_repr))
}

pub(crate) fn cell_key_repr(key: &CellKey) -> String {
    let (rune, stance, entry, exit, adjustments) = key;
    let adjustments: Vec<String> = adjustments.iter().map(|token| python_repr(token)).collect();
    python_tuple(&[
        python_repr(rune),
        python_repr(stance),
        python_repr(entry),
        python_repr(exit),
        python_tuple(&adjustments),
    ])
}

/// A tuple in Python's own repr — comma-space between items, and the trailing comma a one-item tuple needs to still read as one.
pub(crate) fn python_tuple(items: &[String]) -> String {
    match items {
        [] => "()".to_owned(),
        [only] => format!("({only},)"),
        _ => format!("({})", items.join(", ")),
    }
}

/// One string in Python's repr, which is what a raise message pastes a tuple's members in: single quotes unless the text carries a single quote and no double one, backslash and the chosen quote escaped, the three whitespace escapes spelled short, and every other ASCII control character as `\xNN`.
///
/// Non-ASCII passes through, which is Python's behavior for every printable code point and therefore for every name the alphabet spells. A non-printable one would differ, and nothing authored carries one — a rune name, a stance name, a height and an adjustment token are all drawn from the ASCII vocabulary the dump's own grammar admits.
pub(crate) fn python_repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(value.len() + 2);
    out.push(quote);
    for letter in value.chars() {
        match letter {
            '\\' => out.push_str("\\\\"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            _ if letter == quote => {
                out.push('\\');
                out.push(letter);
            }
            '\0'..='\u{1f}' | '\u{7f}' => out.push_str(&format!("\\x{:02x}", letter as u32)),
            _ => out.push(letter),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;
    use crate::types::{AdjustmentToken, Side, TokenKind, boundary_cell};

    /// The product the byte tests are grounded on, built cell by cell so the sort has something to do: the cells arrive in an order `cell_key` has to undo, and the rows exercise a `None` seam, a left-settled letter, a left-settled boundary, a negative extension and a negative prospect.
    fn worked_product(index: &SpecIndex) -> FixpointProduct {
        worked_product_with_labels(index, LabelPool::default())
    }

    fn worked_product_with_labels(index: &SpecIndex, mut labels: LabelPool) -> FixpointProduct {
        FixpointProduct {
            config: "ss03+ss05".to_owned(),
            transitions: vec![
                edge_row(&mut labels),
                left_settled_row(&mut labels),
                boundary_left_row(&mut labels),
            ],
            deep_classes: Vec::new(),
            cited_provenance: vec![
                "qsTea.yaml:policy.refuse[0]".to_owned(),
                "qsIt.yaml:policy.groups".to_owned(),
                "qsPea.yaml:policy.prefer[0]".to_owned(),
            ],
            cells: vec![
                tea_bound(index),
                pea_cell(index),
                boundary_cell(index.vocab(), TokenKind::Space),
                tea_locked(index),
            ],
            seats: seated(index),
            notes: noted(),
            outcomes: outcomes(&mut labels),
            labels,
        }
    }

    fn outcomes(labels: &mut LabelPool) -> Vec<Label> {
        [
            "qsPea.half",
            "qsTea.half.ex-y0.locked",
            "qsPea.half",
            "qsTea.half.en-y5.en-ext-1.ex-bind-pulled-back",
            "space",
        ]
        .map(|text| labels.intern(text))
        .to_vec()
    }

    #[test]
    fn label_ranks_follow_spelling_order_and_reuse_repeated_spellings() {
        let mut labels = LabelPool::default();
        let tea = labels.intern("qsTea");
        let edge = labels.intern("#EDGE");
        let pea = labels.intern("qsPea");
        assert_eq!(labels.intern("qsTea"), tea);
        let ranks = labels.ranks();
        let mut keys = [[tea; 6], [pea; 6], [edge; 6]];
        keys.sort_unstable_by_key(|key| key.map(|label| ranks[label.0 as usize]));
        assert_eq!(
            keys.map(|key| labels.spelled(&key)),
            [["#EDGE"; 6], ["qsPea"; 6], ["qsTea"; 6]]
        );
    }

    #[test]
    fn label_mint_order_does_not_change_product_equality_or_stream_bytes() {
        let index = fixtures::mini();
        let original = worked_product(&index);
        let mut labels = LabelPool::default();
        labels.intern("qsTea");
        labels.intern("#NA");
        labels.intern("qsPea");
        let reordered = worked_product_with_labels(&index, labels);
        assert_ne!(
            original.transitions[0].input_glyph,
            reordered.transitions[0].input_glyph
        );
        assert_eq!(original, reordered);
        assert_eq!(
            emit_transitions(&index, &original),
            emit_transitions(&index, &reordered)
        );
    }

    /// The provenance table the three worked rows index: the empty list, a two-pointer list in the order the trace left it, and a list of one.
    fn noted() -> Vec<Vec<String>> {
        vec![
            Vec::new(),
            vec![
                "qsTea.yaml:policy.refuse[0]".to_owned(),
                "qsPea.yaml:policy.prefer[0]".to_owned(),
            ],
            vec!["qsIt.yaml:policy.groups".to_owned()],
        ]
    }

    /// The seat table the three worked rows index: their five distinct settled records, in the order the rows below name them.
    fn seated(index: &SpecIndex) -> Vec<Settled> {
        vec![
            Settled {
                cell: pea_cell(index),
                seam: None,
                extension: 0,
            },
            Settled {
                cell: tea_locked(index),
                seam: Some(fixtures::sym(index, "baseline")),
                extension: 2,
            },
            Settled {
                cell: pea_cell(index),
                seam: Some(fixtures::sym(index, "x-height")),
                extension: 1,
            },
            Settled {
                cell: tea_bound(index),
                seam: None,
                extension: -1,
            },
            Settled {
                cell: boundary_cell(index.vocab(), TokenKind::Space),
                seam: None,
                extension: 0,
            },
        ]
    }

    fn pea_cell(index: &SpecIndex) -> CellId {
        CellId {
            rune: fixtures::sym(index, "qsPea"),
            stance: fixtures::sym(index, "half"),
            entry: Some(fixtures::sym(index, "baseline")),
            exit: Some(fixtures::sym(index, "x-height")),
            adjustments: Vec::new(),
        }
    }

    fn tea_locked(index: &SpecIndex) -> CellId {
        CellId {
            rune: fixtures::sym(index, "qsTea"),
            stance: fixtures::sym(index, "half"),
            entry: None,
            exit: Some(fixtures::sym(index, "baseline")),
            adjustments: vec![AdjustmentToken::Locked],
        }
    }

    fn tea_bound(index: &SpecIndex) -> CellId {
        CellId {
            rune: fixtures::sym(index, "qsTea"),
            stance: fixtures::sym(index, "half"),
            entry: Some(fixtures::sym(index, "x-height")),
            exit: None,
            adjustments: vec![
                AdjustmentToken::Extend(Side::Entry, 1),
                AdjustmentToken::Bind(Side::Exit, fixtures::sym(index, "pulled-back")),
            ],
        }
    }

    fn edge_row(labels: &mut LabelPool) -> TransitionRow {
        TransitionRow {
            input_glyph: labels.intern("qsPea"),
            left: labels.intern("#EDGE"),
            right1: labels.intern("space"),
            right2: labels.intern("#NA"),
            right3: labels.intern("#NA"),
            right4: labels.intern("#NA"),
            settled: SettledSeat::at(0),
            left_settled: None,
            provenance: NotesSeat::at(0),
            prospect: 0,
            joint: false,
        }
    }

    fn left_settled_row(labels: &mut LabelPool) -> TransitionRow {
        TransitionRow {
            input_glyph: labels.intern("qsTea.noentry"),
            left: labels.intern("qsPea.half.en-y0.ex-y5"),
            right1: labels.intern("qsIt"),
            right2: labels.intern("qsMay"),
            right3: labels.intern("qsPea"),
            right4: labels.intern("#NA"),
            settled: SettledSeat::at(1),
            left_settled: Some(SettledSeat::at(2)),
            provenance: NotesSeat::at(1),
            prospect: 3,
            joint: true,
        }
    }

    fn boundary_left_row(labels: &mut LabelPool) -> TransitionRow {
        TransitionRow {
            input_glyph: labels.intern("qsTea"),
            left: labels.intern("space"),
            right1: labels.intern("qsPea"),
            right2: labels.intern("#EDGE"),
            right3: labels.intern("#NA"),
            right4: labels.intern("#NA"),
            settled: SettledSeat::at(3),
            left_settled: Some(SettledSeat::at(4)),
            provenance: NotesSeat::at(2),
            prospect: -2,
            joint: false,
        }
    }

    /// The bytes `kernel_io.write_transitions` writes for this product, gunzipped — captured from the Python writer itself rather than derived by hand.
    #[test]
    fn a_product_writes_the_head_and_the_rows_python_writes() {
        let index = fixtures::mini();
        let stream =
            emit_transitions(&index, &worked_product(&index)).expect("every cell is seated");
        assert_eq!(
            stream,
            concat!(
                "# ams-m1-transitions/1\t{\"config\":\"ss03+ss05\",\"cells\":[[\"qsPea\",\"half\",\"baseline\",\"x-height\",[]],[\"qsTea\",\"half\",null,\"baseline\",[\"locked\"]],[\"qsTea\",\"half\",\"x-height\",null,[\"en-ext-1\",\"ex-bind-pulled-back\"]],[\"space\",\"boundary\",null,null,[]]],\"deep_classes\":[],\"cited_provenance\":[\"qsIt.yaml:policy.groups\",\"qsPea.yaml:policy.prefer[0]\",\"qsTea.yaml:policy.refuse[0]\"]}\n",
                "[\"qsPea\",\"#EDGE\",\"space\",\"#NA\",\"#NA\",\"#NA\",\"qsPea.half\",[0,null,0],null,false,0,[]]\n",
                "[\"qsTea.noentry\",\"qsPea.half.en-y0.ex-y5\",\"qsIt\",\"qsMay\",\"qsPea\",\"#NA\",\"qsTea.half.ex-y0.locked\",[1,\"baseline\",2],[0,\"x-height\",1],true,3,[\"qsTea.yaml:policy.refuse[0]\",\"qsPea.yaml:policy.prefer[0]\"]]\n",
                "[\"qsTea\",\"space\",\"qsPea\",\"#EDGE\",\"#NA\",\"#NA\",\"qsTea.half.en-y5.en-ext-1.ex-bind-pulled-back\",[2,null,-1],[3,null,0],false,-2,[\"qsIt.yaml:policy.groups\"]]\n",
            )
        );
    }

    #[test]
    fn an_empty_product_is_the_head_line_and_nothing_else() {
        let index = fixtures::mini();
        let product = FixpointProduct {
            config: "default".to_owned(),
            ..FixpointProduct::default()
        };
        let stream = emit_transitions(&index, &product).expect("nothing to seat");
        assert_eq!(
            stream,
            "# ams-m1-transitions/1\t{\"config\":\"default\",\"cells\":[],\"deep_classes\":[],\"cited_provenance\":[]}\n"
        );
    }

    /// The class map's spelling: one pair per entry, sorted by token, members in the map's own order. The bytes are `kernel_io.write_transitions`'s own for the same product, captured from the Python writer rather than derived by hand.
    #[test]
    fn a_deep_class_map_rides_the_head_sorted_by_token() {
        let index = fixtures::mini();
        let mut labels = LabelPool::default();
        let product = FixpointProduct {
            config: "ss04".to_owned(),
            transitions: vec![edge_row(&mut labels)],
            deep_classes: vec![
                (
                    "#Cbbb".to_owned(),
                    vec!["qsPea".to_owned(), "qsTea".to_owned()],
                ),
                ("#Caaa".to_owned(), vec!["qsMay".to_owned()]),
            ],
            cited_provenance: Vec::new(),
            cells: vec![
                pea_cell(&index),
                CellId {
                    rune: fixtures::sym(&index, "qsTea"),
                    stance: fixtures::sym(&index, "full"),
                    entry: None,
                    exit: None,
                    adjustments: Vec::new(),
                },
            ],
            seats: seated(&index),
            notes: noted(),
            outcomes: outcomes(&mut labels),
            labels,
        };
        let stream = emit_transitions(&index, &product).expect("every cell is seated");
        assert_eq!(
            stream,
            concat!(
                "# ams-m1-transitions/1\t{\"config\":\"ss04\",\"cells\":[[\"qsPea\",\"half\",\"baseline\",\"x-height\",[]],[\"qsTea\",\"full\",null,null,[]]],\"deep_classes\":[[\"#Caaa\",[\"qsMay\"]],[\"#Cbbb\",[\"qsPea\",\"qsTea\"]]],\"cited_provenance\":[]}\n",
                "[\"qsPea\",\"#EDGE\",\"space\",\"#NA\",\"#NA\",\"#NA\",\"qsPea.half\",[0,null,0],null,false,0,[]]\n",
            )
        );
    }

    #[test]
    fn a_settled_cell_the_product_never_counted_stops_the_stream() {
        let index = fixtures::mini();
        let mut labels = LabelPool::default();
        let product = FixpointProduct {
            config: "default".to_owned(),
            transitions: vec![left_settled_row(&mut labels)],
            deep_classes: Vec::new(),
            cited_provenance: Vec::new(),
            cells: vec![pea_cell(&index)],
            seats: seated(&index),
            notes: noted(),
            outcomes: outcomes(&mut labels),
            labels,
        };
        assert_eq!(
            emit_transitions(&index, &product),
            Err("the transition ('qsTea.noentry', 'qsPea.half.en-y0.ex-y5', 'qsIt', 'qsMay', 'qsPea', '#NA') settles into ('qsTea', 'half', '', 'baseline', ('locked',)), which the product does not count among its reachable cells".to_owned())
        );
    }

    #[test]
    fn a_left_settled_cell_the_product_never_counted_stops_the_stream() {
        let index = fixtures::mini();
        let mut labels = LabelPool::default();
        let product = FixpointProduct {
            config: "default".to_owned(),
            transitions: vec![left_settled_row(&mut labels)],
            deep_classes: Vec::new(),
            cited_provenance: Vec::new(),
            cells: vec![tea_locked(&index)],
            seats: seated(&index),
            notes: noted(),
            outcomes: outcomes(&mut labels),
            labels,
        };
        assert_eq!(
            emit_transitions(&index, &product),
            Err("the transition ('qsTea.noentry', 'qsPea.half.en-y0.ex-y5', 'qsIt', 'qsMay', 'qsPea', '#NA') carries the left-settled cell ('qsPea', 'half', 'baseline', 'x-height', ()), which the product does not count among its reachable cells".to_owned())
        );
    }

    /// The cells are a `frozenset` on the Python side, so a cell named twice is one seat and one head entry here too — otherwise the seats would depend on how the fixpoint happened to spell a set the writer's contract says it has.
    #[test]
    fn a_cell_the_product_counts_twice_takes_one_seat() {
        let index = fixtures::mini();
        let mut product = worked_product(&index);
        product.cells.push(pea_cell(&index));
        product.cells.push(tea_locked(&index));
        assert_eq!(
            emit_transitions(&index, &product),
            emit_transitions(&index, &worked_product(&index))
        );
    }

    /// An absent side sorts as the empty string, which is before every height rather than after every one, and the adjustments sequence is the last component rather than an afterthought.
    #[test]
    fn cell_key_spells_an_absent_side_as_the_empty_string() {
        let index = fixtures::mini();
        assert_eq!(
            cell_key(&index, &tea_locked(&index)),
            (
                "qsTea".to_owned(),
                "half".to_owned(),
                String::new(),
                "baseline".to_owned(),
                vec!["locked".to_owned()]
            )
        );
        assert!(cell_key(&index, &tea_locked(&index)) < cell_key(&index, &tea_bound(&index)));
        assert!(cell_key(&index, &pea_cell(&index)) < cell_key(&index, &tea_locked(&index)));
    }

    /// The sort that a `Sym`-id sort would silently pass: the fixture interns `x-height` long before `ss03`, so a token built on symbol order would spell the configuration backwards.
    #[test]
    fn a_config_token_sorts_its_features_by_the_resolved_string() {
        let index = fixtures::mini();
        let early = fixtures::sym(&index, "x-height");
        let late = fixtures::sym(&index, "ss03");
        assert!(early < late, "the fixture interns x-height before ss03");
        assert_eq!(feature_config_token(&index, [early, late]), "ss03+x-height");
        assert_eq!(feature_config_token(&index, [late, late]), "ss03");
        assert_eq!(feature_config_token(&index, Vec::<Sym>::new()), "default");
    }

    #[test]
    fn a_python_repr_quotes_the_way_python_quotes() {
        assert_eq!(python_repr("qsPea"), "'qsPea'");
        assert_eq!(python_repr(""), "''");
        assert_eq!(python_repr("it's"), "\"it's\"");
        assert_eq!(python_repr("it's \"so\""), "'it\\'s \"so\"'");
        assert_eq!(python_repr("a\\b\tc\n\u{1}"), "'a\\\\b\\tc\\n\\x01'");
        assert_eq!(python_tuple(&[]), "()");
    }
}
