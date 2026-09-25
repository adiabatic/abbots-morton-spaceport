//! Writes one configuration's fixpoint product as the `ams-m1-transitions/1` stream, byte-identical to what `write_transitions` in `rebuild/pipeline/kernel_io.py` writes. That function defines the format: the head's key order, the sort of the cell vocabulary, which absent values are written as `null` and which as the empty string, and the error message for a cell missing from the head. Where the two disagree, the Python is correct.
//!
//! [`crate::emit`] writes back a spec that was read; this module writes what the fixpoint produced. Both use the same JSON string escaper, so the two outputs cannot escape strings differently.
//!
//! The stream is uncompressed. Python's writer gzips it into a file with a zeroed timestamp. The kernel writes the same bytes uncompressed, to stdout for `enumerate` and to `transitions-<config>.ndjson` files for `enumerate-configs`, and `kernel_exec.read_stream` reads them without decompressing, so the crate needs no compressor.
//!
//! Each cell is written once, in the head, and every row names its settled cell by its index there. That index is a position in [`cell_key`] order, which is why this writer, not the fixpoint, sorts the cells. A row whose cell is not among the product's reachable cells is an error here, matching the `PartitionError` that `write_transitions` raises.
//!
//! The product's own index tables are separate from the head's cell indexes and are never written. A row holds its settled record and its left neighbor's as a [`SettledSeat`] each, indexing [`FixpointProduct::seats`], which lists each distinct settled record in the order the fixpoint first reached it. A row's provenance is a [`NotesSeat`] into [`FixpointProduct::notes`]. The writer resolves a row's seat to the record and the record's cell to its head index, and writes the provenance list the notes seat names, in the order the trace recorded it. The head's cell vocabulary in `_cell_key` order is part of the format Python reads; the seat table only saves memory inside the crate.

use std::collections::BTreeSet;
use std::fmt::Write as _;
use std::io::Write as _;
use std::rc::Rc;

use crate::emit::{escape_into, json_string};
use crate::hash::{HashMap, HashSet};
use crate::index::SpecIndex;
use crate::model::Sym;
use crate::types::{CellId, NotesSeat, Settled, SettledSeat, adjustment_text};

/// The format marker on the head line, `kernel_io.TRANSITIONS_FORMAT`. A stream with any other marker is a different format.
pub const TRANSITIONS_FORMAT: &str = "ams-m1-transitions/1";

/// A window label's id in a [`LabelPool`]: the index at which its text was first interned. Within one pool, two ids are equal exactly when their texts are. Id order is interning order, which nothing reads; lexicographic order comes from [`LabelPool::ranks`].
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Label(pub(crate) u32);

/// Interned window labels for one product, shared by the fixpoint, the fold, and the decision table. Rows store compact ids, and only code that needs the text resolves them through this pool. Ids from different pools are unrelated.
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

    /// The pool's id for this text, interning the text if the pool does not have it.
    pub(crate) fn intern(&mut self, text: &str) -> Label {
        if let Some(&found) = self.ids.get(text) {
            return found;
        }
        self.mint(Rc::from(text))
    }

    /// [`LabelPool::intern`] for a `String` the caller already built, so that a new label reuses the buffer instead of copying it.
    pub(crate) fn intern_owned(&mut self, text: String) -> Label {
        if let Some(&found) = self.ids.get(text.as_str()) {
            return found;
        }
        self.mint(Rc::from(text))
    }

    /// Adds a text the pool does not have, at the next id.
    fn mint(&mut self, shared: Rc<str>) -> Label {
        let id = Label(
            u32::try_from(self.texts.len())
                .expect("a configuration's distinct labels number in the tens of thousands, nowhere near the u32 ids can seat"),
        );
        self.ids.insert(Rc::clone(&shared), id);
        self.texts.push(shared);
        id
    }

    /// The text an id was interned for, as a shared handle.
    pub(crate) fn text(&self, label: Label) -> &Rc<str> {
        &self.texts[label.0 as usize]
    }

    /// The texts of one key's six labels, which diagnostics use to identify a window.
    pub(crate) fn spelled(&self, key: &[Label; 6]) -> [&str; 6] {
        key.map(|label| &**self.text(label))
    }

    /// Each id's position among the pool's texts in lexicographic order. `ranks[id]` compares as the text does, so sorting rows by their rank tuple sorts them by their key tuple without comparing strings.
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

/// Everything one configuration's fixpoint produces, the counterpart of `table.FixpointProduct`. The rows arrive sorted on [`TransitionRow::key`], and the stream keeps that order. The fold's expansion and its per-input rule fold rely on that order, and `fold::assert_key_sorted` fails on a product that is out of it.
///
/// On the Python side `cells` and `cited_provenance` are `frozenset`s and `deep_classes` is a `Mapping`. Here they are vectors, and the writer puts them in canonical order: `cells` and `cited_provenance` are sorted with repeats removed, and `deep_classes` is sorted by token. `deep_classes` is empty at label grain and in the pinned world, and the head includes it either way.
///
/// `seats` and `notes` have no Python counterpart. They are the tables that each row's [`SettledSeat`]s and [`NotesSeat`] index: one entry per distinct settled record and one per distinct provenance list, each in the order the fixpoint first reached it. [`FixpointProduct::settled`], [`FixpointProduct::left_settled`], and [`FixpointProduct::provenance`] read a row's values back; Python's `Transition` holds all three by value. The label pool and the outcome table (indexed by settled seat) pass unchanged into the decision table.
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

/// Compares labels by text through each product's own pool, because label ids from separate enumerations are unrelated.
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
    /// A row's outcome. It is stored per settled seat, so every row with that seat shares it.
    pub fn outcome(&self, row: &TransitionRow) -> &Rc<str> {
        self.labels.text(self.outcomes[row.settled.index()])
    }

    /// The record one row settled into.
    pub fn settled(&self, row: &TransitionRow) -> &Settled {
        &self.seats[row.settled.index()]
    }

    /// The record the row's left neighbor settled into: present for a letter on the left and for the boundary cells the fold records, absent otherwise.
    pub fn left_settled(&self, row: &TransitionRow) -> Option<&Settled> {
        row.left_settled.map(|seat| &self.seats[seat.index()])
    }

    /// The provenance pointers one row's trace recorded, in first-seen order, which is the order the rule fold joins them in.
    pub fn provenance(&self, row: &TransitionRow) -> &[String] {
        &self.notes[row.provenance.index()]
    }
}

/// One window's six label ids, plus the indexes of its settled records and provenance. Labels resolve through the owning product's pool, and the outcome through its outcome table. Id equality and `Debug` output mean something only within that pool; ordering and diagnostics use the resolved texts.
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
    /// The six labels that key this row, as `table.Window.key` does. The product is sorted on this key, and error messages name a row by it. It is an array so that comparing two keys is one lexicographic comparison, as Python's tuple comparison is.
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

/// What [`cell_key`] returns: `table._cell_key`'s tuple, with every component resolved to a string. The alias keeps signatures within clippy's type-complexity limit.
pub type CellKey = (String, String, String, String, Vec<String>);

/// The sort key of the head's cell vocabulary, matching `table._cell_key`: the rune, the stance, the entry and exit heights (the empty string for an absent side), and the adjustment tokens in their stored order. Every component is the resolved string, because `Sym` order is the order the dump first mentioned names in, which Python does not use.
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

/// The configuration token for no features, `"default"` in `model.feature_config_token`. It appears in artifact filenames and as a stream head's `config`.
pub const DEFAULT_CONFIG: &str = "default";

/// The configuration token for a feature set, matching `model.feature_config_token`: the enabled feature names sorted and joined with `+`, or `default` when none is enabled. It is the product's `config` field and the `<config>` in every artifact filename, so the crate and Python must produce the same string.
///
/// Names sort as strings, and a name passed twice counts once, because every Python caller passes a set. It takes names, not symbols, so the unit tests in `main.rs` can check that file's command-line token rule against it without loading a spec.
pub fn config_token<'a>(features: impl IntoIterator<Item = &'a str>) -> String {
    let enabled: BTreeSet<&str> = features.into_iter().collect();
    if enabled.is_empty() {
        return DEFAULT_CONFIG.to_owned();
    }
    enabled.into_iter().collect::<Vec<&str>>().join("+")
}

/// [`config_token`] for a feature set given as symbols.
pub fn feature_config_token(index: &SpecIndex, features: impl IntoIterator<Item = Sym>) -> String {
    config_token(features.into_iter().map(|feature| index.resolve(feature)))
}

/// Why a product was not written: the writer rejected it, or the sink failed. The two are kept apart because they have different causes, as in the fan-out's own `Failure`.
#[derive(Debug)]
pub enum WriteFailure {
    /// The product's rows and cells disagree. The message is `write_transitions`'s.
    Refused(String),
    /// Writing to the sink failed.
    Sink(std::io::Error),
}

/// Writes one product to `sink` as the whole stream, as `kernel_io.write_transitions` does without the gzip: the `# ams-m1-transitions/1<tab><head json>` line, then one compact JSON array per transition in the product's order, each line ending in a newline.
///
/// Every row's cells are looked up before the first byte is written, so, as in `write_transitions`, nothing is written for a product whose rows and cells disagree. The error message is that function's `PartitionError` message, Python tuple reprs included. A row's settled cell is checked before its left-settled cell because Python's list literal evaluates its two `seated` calls in that order, so a row missing both is reported by its settled cell.
///
/// The bytes are written a line at a time through one reused buffer. A configuration's stream is hundreds of megabytes, and building it as one `String`, with the reallocations as it grows, would cost that much memory for no benefit.
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
    // Deduplicate across the whole list. The sort compares only `cell_key`, so equal cells are guaranteed to be adjacent only if `cell_key` is injective, which this writer does not assume.
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

/// [`write_transitions`] into a `String`, for tests that read the whole stream back. Only tests call it, because a configuration's stream is hundreds of megabytes.
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

/// Writes the head object with its four keys in the order Python's dict literal inserts them (`config`, `cells`, `deep_classes`, `cited_provenance`), sorting the set-valued ones here. A `deep_classes` entry sorts on the whole pair, as `sorted(mapping.items())` does; because tokens are unique, that is the same as sorting by token.
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

/// One cell as the head writes it: the rune, the stance, the two heights (`null` for an absent side, where [`cell_key`] uses the empty string), and the adjustment tokens.
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

/// Appends one row to `out`: the six window labels and the outcome, the settled triple, the left-settled triple or `null`, the joint flag, the prospect, and the provenance in first-seen order.
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

/// One settled record as a row writes it: the cell's index in the head, the seam it committed, and the connector pixels on that seam.
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

/// One settled cell's index in the head. A cell without one is an error in `write_transitions`'s message format: the row named by its six-label key and the cell by its `_cell_key` tuple, both as Python reprs.
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

/// A tuple in Python's repr: items separated by a comma and a space, and the trailing comma a one-item tuple needs.
pub(crate) fn python_tuple(items: &[String]) -> String {
    match items {
        [] => "()".to_owned(),
        [only] => format!("({only},)"),
        _ => format!("({})", items.join(", ")),
    }
}

/// One string in Python's repr, the form error messages use for a tuple's members: single quotes unless the text contains a single quote and no double quote, backslash and the chosen quote escaped, tab, newline, and carriage return as `\t`, `\n`, and `\r`, and every other ASCII control character as `\xNN`.
///
/// Non-ASCII characters pass through unchanged, which matches Python for every printable code point. A non-printable one would differ, but no authored name contains one: rune names, stance names, heights, and adjustment tokens all come from the ASCII vocabulary the dump's grammar accepts.
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

    /// The product the byte tests use, built by hand. Its cells arrive in an order the `cell_key` sort must change, and its rows cover a `None` seam, a left-settled letter, a left-settled boundary, a negative extension, and a negative prospect.
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

    /// The provenance table the three worked rows index: the empty list, a two-pointer list in trace order, and a one-pointer list.
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

    /// The seat table the three worked rows index: their five distinct settled records, in the order the rows below use them.
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

    /// The bytes `kernel_io.write_transitions` writes for this product, decompressed, captured from the Python writer.
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

    /// How the head writes the class map: one pair per entry, sorted by token, members in stored order. The expected bytes are `kernel_io.write_transitions`'s output for the same product, captured from the Python writer.
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

    /// Python's `cells` is a `frozenset`, so a cell listed twice gets one index and one head entry here too. Otherwise the indexes would depend on how the fixpoint listed what the format treats as a set.
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

    /// An absent side sorts as the empty string, before every height, and the adjustments are the last component.
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

    /// Features sort by resolved string, not by `Sym` id: the fixture interns `x-height` before `ss03`, so a token sorted by symbol would list them in the wrong order.
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
