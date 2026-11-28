//! One configuration's fixpoint product and the vocabulary it is keyed in: [`FixpointProduct`], with its interned window labels ([`LabelPool`]) and its packed rows ([`TransitionRow`]), which the fixpoint fills and the fold, the decision table and the windows writer read; the sort key the windows head lists its cells in ([`cell_key`]); the configuration token every artifact filename carries ([`config_token`]); and the Python reprs the build's error messages name a window and a cell in ([`python_repr`]).
//!
//! The product's index tables stay inside the crate. A row holds its settled record and its left neighbor's as a [`SettledId`] each, indexing [`FixpointProduct::settled_records`], which lists each distinct settled record in the order the fixpoint first reached it. A row's provenance is a [`NotesId`] into [`FixpointProduct::notes`]. A reader resolves a row's ids through the product ([`FixpointProduct::settled`], [`FixpointProduct::left_settled`], [`FixpointProduct::provenance`]); the tables only save memory.

use std::collections::BTreeSet;
use std::rc::Rc;

use crate::hash::HashMap;
use crate::index::SpecIndex;
use crate::model::Sym;
use crate::types::{CellId, NotesId, Settled, SettledId, adjustment_text};

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
                .expect("a configuration's distinct labels number in the tens of thousands, nowhere near the u32 ids can index"),
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

/// Everything one configuration's fixpoint produces. The rows arrive sorted on [`TransitionRow::key`]. The fold's expansion and its per-input rule fold rely on that order, and `fold::assert_key_sorted` fails on a product that is out of it.
///
/// `cells` and `cited_provenance` are sets held as vectors in the order the fixpoint gathered them, so a reader that lists them sorts them and drops repeats, as the windows writer does ([`crate::artifacts`]). `deep_classes` is empty at label grain and in the pinned mode set.
///
/// `settled_records` and `notes` are the tables that each row's [`SettledId`]s and [`NotesId`] index: one entry per distinct settled record and one per distinct provenance list, each in the order the fixpoint first reached it. [`FixpointProduct::settled`], [`FixpointProduct::left_settled`], and [`FixpointProduct::provenance`] read a row's values back. The label pool and the outcome table (indexed by settled id) pass unchanged into the decision table.
#[derive(Clone, Debug, Default, Eq)]
pub struct FixpointProduct {
    pub config: String,
    pub transitions: Vec<TransitionRow>,
    pub labels: LabelPool,
    pub outcomes: Vec<Label>,
    pub deep_classes: Vec<(String, Vec<String>)>,
    pub cited_provenance: Vec<String>,
    pub cells: Vec<CellId>,
    pub settled_records: Vec<Settled>,
    pub notes: Vec<Vec<String>>,
}

/// Compares labels by text through each product's own pool, because label ids from separate enumerations are unrelated.
impl PartialEq for FixpointProduct {
    fn eq(&self, other: &Self) -> bool {
        self.config == other.config
            && self.deep_classes == other.deep_classes
            && self.cited_provenance == other.cited_provenance
            && self.cells == other.cells
            && self.settled_records == other.settled_records
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
    /// A row's outcome. It is stored per settled id, so every row with that id shares it.
    pub fn outcome(&self, row: &TransitionRow) -> &Rc<str> {
        self.labels.text(self.outcomes[row.settled.index()])
    }

    /// The record one row settled into.
    pub fn settled(&self, row: &TransitionRow) -> &Settled {
        &self.settled_records[row.settled.index()]
    }

    /// The record the row's left neighbor settled into: present for a letter on the left and for the boundary cells the fold records, absent otherwise.
    pub fn left_settled(&self, row: &TransitionRow) -> Option<&Settled> {
        row.left_settled.map(|id| &self.settled_records[id.index()])
    }

    /// The provenance pointers one row's trace recorded, in first-seen order, which is the order the rule fold joins them in.
    pub fn provenance(&self, row: &TransitionRow) -> &[String] {
        &self.notes[row.provenance.index()]
    }

    /// What the product holds with every id resolved and its set-valued fields in canonical order, so two enumerations compare by their rows, classes, fired provenance and cells whatever order their pools, tables and sets were filled in. [`PartialEq`] compares the settled and provenance tables by position instead, and `cited_provenance` comes from a hash set, so two products that hold the same can compare unequal there.
    #[cfg(test)]
    pub(crate) fn resolved(&self, index: &SpecIndex) -> Resolved {
        let mut deep_classes = self.deep_classes.clone();
        deep_classes.sort();
        let mut cited_provenance = self.cited_provenance.clone();
        cited_provenance.sort_unstable();
        cited_provenance.dedup();
        let mut cells: Vec<CellKey> = self
            .cells
            .iter()
            .map(|cell| cell_key(index, cell))
            .collect();
        cells.sort();
        cells.dedup();
        Resolved {
            config: self.config.clone(),
            rows: self
                .transitions
                .iter()
                .map(|row| ResolvedRow {
                    key: row.key(&self.labels).map(str::to_owned),
                    outcome: self.outcome(row).to_string(),
                    settled: self.settled(row).clone(),
                    left_settled: self.left_settled(row).cloned(),
                    provenance: self.provenance(row).to_vec(),
                    prospect: row.prospect,
                    joint: row.joint,
                })
                .collect(),
            deep_classes,
            cited_provenance,
            cells,
        }
    }
}

/// A product as [`FixpointProduct::resolved`] reads it.
#[cfg(test)]
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct Resolved {
    config: String,
    rows: Vec<ResolvedRow>,
    deep_classes: Vec<(String, Vec<String>)>,
    cited_provenance: Vec<String>,
    cells: Vec<CellKey>,
}

/// One row as [`FixpointProduct::resolved`] reads it: its key, outcome, settled records and provenance as values.
#[cfg(test)]
#[derive(Debug, PartialEq, Eq)]
struct ResolvedRow {
    key: [String; 6],
    outcome: String,
    settled: Settled,
    left_settled: Option<Settled>,
    provenance: Vec<String>,
    prospect: i8,
    joint: bool,
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
    pub settled: SettledId,
    pub left_settled: Option<SettledId>,
    pub provenance: NotesId,
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

/// The configuration token for no features, `"default"` in `model.feature_config_token`. It appears in artifact filenames and as a windows head's `config`.
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
/// Non-ASCII characters pass through unchanged, which matches Python for every printable code point. A non-printable one would differ, but no authored name contains one: `engine/schema/rune.schema.json`, which `spec_load` checks every rune against, admits only ASCII rune names, stance names, bitmap names, and heights, and an adjustment token is built from ASCII words, counts, and those bitmap names.
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

    /// The product the byte tests use, built by hand. Its cells arrive in an order the `cell_key` sort must change, and its rows cover a `None` junction, a left-settled letter, a left-settled boundary, a negative extension, and a negative prospect.
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
            settled_records: settled_table(index),
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
    fn label_mint_order_does_not_change_product_equality() {
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
        assert_eq!(original.resolved(&index), reordered.resolved(&index));
    }

    /// The resolved view lists the set-valued fields sorted with repeats removed, so a cell or a pointer listed twice, or the sets gathered in another order, read the same.
    #[test]
    fn the_resolved_view_reads_a_set_as_a_set() {
        let index = fixtures::mini();
        let mut shuffled = worked_product(&index);
        shuffled.cells.reverse();
        shuffled.cells.push(pea_cell(&index));
        shuffled.cited_provenance.reverse();
        shuffled
            .cited_provenance
            .push("qsIt.yaml:policy.groups".to_owned());
        assert_ne!(shuffled, worked_product(&index));
        assert_eq!(
            shuffled.resolved(&index),
            worked_product(&index).resolved(&index)
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

    /// The settled table the three worked rows index: their five distinct settled records, in the order the rows below use them.
    fn settled_table(index: &SpecIndex) -> Vec<Settled> {
        vec![
            Settled {
                cell: pea_cell(index),
                junction: None,
                extension: 0,
            },
            Settled {
                cell: tea_locked(index),
                junction: Some(fixtures::sym(index, "baseline")),
                extension: 2,
            },
            Settled {
                cell: pea_cell(index),
                junction: Some(fixtures::sym(index, "x-height")),
                extension: 1,
            },
            Settled {
                cell: tea_bound(index),
                junction: None,
                extension: -1,
            },
            Settled {
                cell: boundary_cell(index.vocab(), TokenKind::Space),
                junction: None,
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
            settled: SettledId::at(0),
            left_settled: None,
            provenance: NotesId::at(0),
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
            settled: SettledId::at(1),
            left_settled: Some(SettledId::at(2)),
            provenance: NotesId::at(1),
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
            settled: SettledId::at(3),
            left_settled: Some(SettledId::at(4)),
            provenance: NotesId::at(2),
            prospect: -2,
            joint: false,
        }
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
