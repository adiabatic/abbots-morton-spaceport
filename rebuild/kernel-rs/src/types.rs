//! The values settlement passes between its stages. Every string a dump or a Python caller writes is represented here in one of three ways: an interned [`Sym`] when the spec authored it, a closed enum when the kernel defines the set, or an owned `String` when only formatting reads it. `rebuild/pipeline/settle.py` keeps the same types as plain Python classes, for the tokenizing and ligature formation that happen before a window reaches this crate and for decoding the answers that come back.
//!
//! Runes, stances, heights, bitmap names, and class names are authored, so they are symbols and compare as a `u32`. The six right-token kinds, the four word positions, the elimination and decided stages, and the adjustments grammar that `model.py` documents are the kernel's own closed sets, so they are enums and cannot be misspelled. A comparison such as `left.kind != cond.is_token`, which a dump writes as two plain strings, compares a kernel enum against an authored value. The kernel makes it by comparing the condition's symbol with the kind's symbol in [`Vocab`], the one place the closed vocabulary is interned into the spec's string pool.
//!
//! Entry and exit states are not `Option<Sym>`. Pairings, `cells:` rows, and `joined_at` compare a height against the literal `"none"`, so the value they compare is a state: one symbol that is either a height or `none`. [`Vocab::height_state`] converts to it from the `Option<Sym>` form, which means "live at this height, or not live". `self_entry` and `self_exit` use the `live`/`none` vocabulary instead, through [`Vocab::liveness_state`]. Keeping the forms separate prevents a `None` from comparing unequal to an authored `none`.
//!
//! The functions here that take a [`SpecIndex`] only resolve symbols and ordinals. They take the index, not a bare interner, because [`cell_label`] also needs the registry's height-to-y map, and every caller already has the index.

use std::num::NonZeroU32;

use crate::emit::json_string;
use crate::hash::HashMap;
use crate::index::{Ordinal, SpecIndex};
use crate::model::{Provenance, Sym};

/// The boundary kinds that split a run; word position is derived from them. `settle.SPLITTING_KINDS`. [`TokenKind::splits_runs`] is the form the code uses, and `the_kind_predicates_agree_with_the_constant_lists` checks that the two agree.
pub const SPLITTING_KINDS: [&str; 3] = ["edge", "space", "zwnj"];

/// Every kind except `letter` and `unknown`, `settle.BOUNDARY_KINDS`: the splitting kinds plus the namer dot, which is a boundary that does not split a run.
pub const BOUNDARY_KINDS: [&str; 4] = ["edge", "space", "zwnj", "namer-dot"];

/// The stance name a boundary cell carries, and the marker [`is_boundary_settled`] reads. `settle.BOUNDARY_STANCE`.
pub const BOUNDARY_STANCE: &str = "boundary";

/// The state of a side that did not join. `model.NONE_STATE`.
pub const NONE_STATE: &str = "none";

/// The state of a side that did join, in `self_entry:` and `self_exit:`, whose vocabulary is `live` or `none` instead of a height. Python writes the string inline; it is a constant here so the interned vocabulary has one source.
pub const LIVE_STATE: &str = "live";

/// The suffix a `cells:` row adds to a height to name its withdrawn exit state. `model.WITHDRAWN_SUFFIX`. [`SpecIndex::withdrawn_state`] combines a height and this suffix into one symbol.
pub const WITHDRAWN_SUFFIX: &str = "-withdrawn";

/// The exit index of a non-joining candidate, `settle._NO_EXIT_INDEX`. It is a large number instead of an `Option` so that it sorts after every real exit index in the structural floor and in the ranked list, as in Python. Real exit counts are single digits, so 9999 is always larger.
pub const NO_EXIT_INDEX: usize = 9999;

/// What a window slot holds, apart from which letter. The six kinds are the ones the comment on `settle.RightToken.kind` lists. `Unknown` means the slot is outside the evaluated window, not something in the text; three-valued right-condition matching turns it into a `None` result.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum TokenKind {
    Edge,
    Space,
    Zwnj,
    NamerDot,
    Letter,
    Unknown,
}

impl TokenKind {
    /// The name the spec and every artifact use for this kind.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Edge => "edge",
            Self::Space => "space",
            Self::Zwnj => "zwnj",
            Self::NamerDot => "namer-dot",
            Self::Letter => "letter",
            Self::Unknown => "unknown",
        }
    }

    /// The kind a name denotes, or `None` for text that is not one of the six. The inverse of [`TokenKind::as_str`], used by the `settle-cases` reader in `cases.rs`, which reads kinds from tab-separated fields, and by [`crate::replay::alphabet`], which reads the registry's boundary token names.
    pub fn from_text(text: &str) -> Option<Self> {
        match text {
            "edge" => Some(Self::Edge),
            "space" => Some(Self::Space),
            "zwnj" => Some(Self::Zwnj),
            "namer-dot" => Some(Self::NamerDot),
            "letter" => Some(Self::Letter),
            "unknown" => Some(Self::Unknown),
            _ => None,
        }
    }

    /// Whether this kind ends a run; word position is derived from these. Matches [`SPLITTING_KINDS`]. The namer dot is a boundary that does not split, so both of its neighbors stay medial.
    pub fn splits_runs(self) -> bool {
        matches!(self, Self::Edge | Self::Space | Self::Zwnj)
    }

    /// Whether this kind is one of the four boundaries, matching [`BOUNDARY_KINDS`]. An `is: boundary` condition expands to these.
    pub fn is_boundary(self) -> bool {
        matches!(self, Self::Edge | Self::Space | Self::Zwnj | Self::NamerDot)
    }

    /// The three-bit code a [`PackedKinds`] word stores: declaration order, `Edge` at zero through `Unknown` at five.
    fn code(self) -> u16 {
        self as u16
    }

    /// The kind a three-bit code denotes, the inverse of [`TokenKind::code`]. No packing writes a code above five, so one is a kernel bug and panics.
    fn of_code(code: u16) -> Self {
        match code {
            0 => Self::Edge,
            1 => Self::Space,
            2 => Self::Zwnj,
            3 => Self::NamerDot,
            4 => Self::Letter,
            5 => Self::Unknown,
            _ => panic!("a packed kind is one of the six"),
        }
    }
}

/// Up to five [`TokenKind`]s in one `u16`, three bits each. Memo keys store a kind for each slot beside the slot's rune. A kind has six values and fits in three bits, so the five kinds of a [`crate::engine::TraceKey`] take two bytes instead of five. Slot zero is the low three bits. The derived ordering and hash are the word's, which is all a key needs.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct PackedKinds(u16);

impl PackedKinds {
    /// How many kinds one word holds.
    pub const CAPACITY: usize = 5;
    const WIDTH: u32 = 3;
    const MASK: u16 = 0b111;

    /// The word holding these kinds, slot by slot.
    pub fn of(kinds: &[TokenKind]) -> Self {
        assert!(
            kinds.len() <= Self::CAPACITY,
            "a packed kinds word holds at most five kinds"
        );
        let mut word = 0u16;
        for (slot, kind) in kinds.iter().enumerate() {
            word |= kind.code() << (slot as u32 * Self::WIDTH);
        }
        Self(word)
    }

    /// The kind at `slot`.
    pub fn get(self, slot: usize) -> TokenKind {
        assert!(
            slot < Self::CAPACITY,
            "a packed kinds word holds five slots"
        );
        TokenKind::of_code((self.0 >> (slot as u32 * Self::WIDTH)) & Self::MASK)
    }
}

/// One raw window slot: a boundary, an unknown, or a letter naming its rune. `settle.RightToken`, with the same equality: the kind is part of the value, so `UNKNOWN` and `EDGE` are different tokens, and two letter tokens are equal when their runes are.
///
/// A letter also carries its rune's [`Ordinal`], because the memo keys store ordinals. A token created by [`SpecIndex::letter`] passes its ordinal to every key built from it, so building a key needs no rune lookup. Under the index that created it, the ordinal is a function of the name, so the derived equality is still equality of runes.
///
/// The derived ordering exists so a token can key a `BTreeMap`. It orders letters by interning order, which is the order the dump happened to mention names in. Where an output order depends on a token, as in the guard sweep's rows, sort by the resolved name instead.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum RightToken {
    Edge,
    Space,
    Zwnj,
    NamerDot,
    Unknown,
    Letter(Sym, Ordinal),
}

/// The run edge, `settle.EDGE`.
pub const EDGE: RightToken = RightToken::Edge;
/// A space, `settle.SPACE`.
pub const SPACE: RightToken = RightToken::Space;
/// A zero-width non-joiner, `settle.ZWNJ`.
pub const ZWNJ: RightToken = RightToken::Zwnj;
/// The namer dot, `settle.NAMER_DOT`.
pub const NAMER_DOT: RightToken = RightToken::NamerDot;
/// A slot outside the evaluated window, `settle.UNKNOWN`.
pub const UNKNOWN: RightToken = RightToken::Unknown;

impl RightToken {
    /// Which kind of slot this is.
    pub fn kind(self) -> TokenKind {
        match self {
            Self::Edge => TokenKind::Edge,
            Self::Space => TokenKind::Space,
            Self::Zwnj => TokenKind::Zwnj,
            Self::NamerDot => TokenKind::NamerDot,
            Self::Unknown => TokenKind::Unknown,
            Self::Letter(_, _) => TokenKind::Letter,
        }
    }

    /// The rune this slot names, or `None` when it is not a letter. `RightToken.rune`, for reads that have not yet checked the kind.
    pub fn rune(self) -> Option<Sym> {
        match self {
            Self::Letter(rune, _) => Some(rune),
            _ => None,
        }
    }

    /// The rune this slot names, for reads that have already checked it is a letter. Panics on any other kind, as `RightToken.letter` raises `ValueError`: reaching it means a caller skipped the kind check, which is a kernel bug and not a settlement outcome.
    pub fn letter(self) -> Sym {
        match self {
            Self::Letter(rune, _) => rune,
            other => panic!("{} token has no rune", other.kind().as_str()),
        }
    }

    /// The rune-field ordinal of the rune this slot names, or `None` when it is not a letter: [`RightToken::rune`] in the form the memo keys store.
    pub fn ordinal(self) -> Option<Ordinal> {
        match self {
            Self::Letter(_, ordinal) => Some(ordinal),
            _ => None,
        }
    }

    /// The rune-field ordinal, for reads that have already checked it is a letter. Panics on any other kind, as [`RightToken::letter`] does.
    pub fn letter_ordinal(self) -> Ordinal {
        match self {
            Self::Letter(_, ordinal) => ordinal,
            other => panic!("{} token has no rune", other.kind().as_str()),
        }
    }

    /// The boundary or unknown token for a kind, or `None` for [`TokenKind::Letter`], which needs a rune. The inverse of [`RightToken::kind`], used by the `settle-cases` reader and [`crate::replay::alphabet`] to rebuild boundary tokens from a kind's name.
    pub fn of_kind(kind: TokenKind) -> Option<Self> {
        match kind {
            TokenKind::Edge => Some(Self::Edge),
            TokenKind::Space => Some(Self::Space),
            TokenKind::Zwnj => Some(Self::Zwnj),
            TokenKind::NamerDot => Some(Self::NamerDot),
            TokenKind::Unknown => Some(Self::Unknown),
            TokenKind::Letter => None,
        }
    }
}

/// Where in a word a position sits, derived from run-splitting boundaries alone. A `word:` condition can name only these four.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum WordPosition {
    Initial,
    Medial,
    Final,
    Isolated,
}

impl WordPosition {
    /// The name a `word:` condition uses for this position.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Initial => "initial",
            Self::Medial => "medial",
            Self::Final => "final",
            Self::Isolated => "isolated",
        }
    }
}

/// Word position from the kinds on either side of a position, `settle.word_position`. `None` means the right slot is outside the evaluated window, so the position cannot be decided yet; `when_matches` propagates that unknown result instead of guessing.
pub fn word_position(left: TokenKind, right1: TokenKind) -> Option<WordPosition> {
    let initial = left.splits_runs();
    if right1 == TokenKind::Unknown {
        return None;
    }
    let ends = right1.splits_runs();
    Some(match (initial, ends) {
        (true, true) => WordPosition::Isolated,
        (true, false) => WordPosition::Initial,
        (false, true) => WordPosition::Final,
        (false, false) => WordPosition::Medial,
    })
}

/// Which side of a cell an adjustment or a `require:` names.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Side {
    Entry,
    Exit,
}

impl Side {
    /// The two-letter prefix the adjustments grammar uses for this side: `en` or `ex`.
    pub fn prefix(self) -> &'static str {
        match self {
            Self::Entry => "en",
            Self::Exit => "ex",
        }
    }

    /// The name a `require:` entry or a `cells:` key uses for this side: `entry` or `exit`.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Entry => "entry",
            Self::Exit => "exit",
        }
    }
}

/// One generated `CellId.adjustments` token in the closed grammar `model.py` documents. It is an enum, not text, so a token cannot be misspelled. `model.parse_adjustment` reads the same grammar in Python, and [`adjustment_text`] writes the text both use.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum AdjustmentToken {
    /// `locked`: the ZWNJ chokepoint twin, entry side severed.
    Locked,
    /// `en-ext-N` / `ex-ext-N`: same-row connector lengthening by N pixels.
    Extend(Side, i64),
    /// `en-con-N` / `ex-con-N`: the contract inverse.
    Contract(Side, i64),
    /// `en-trim-N` / `ex-trim-N`: receiver-side ink blanking with the anchor left in place.
    Trim(Side, i64),
    /// `en-bind-<bitmap>` / `ex-bind-<bitmap>`: a named hand-drawn sibling substituting for the base drawing.
    Bind(Side, Sym),
}

/// The token's text, which geometry reads and the corpus stores as a JSON string.
pub fn adjustment_text(index: &SpecIndex, token: AdjustmentToken) -> String {
    match token {
        AdjustmentToken::Locked => "locked".to_owned(),
        AdjustmentToken::Extend(side, by) => format!("{}-ext-{by}", side.prefix()),
        AdjustmentToken::Contract(side, by) => format!("{}-con-{by}", side.prefix()),
        AdjustmentToken::Trim(side, by) => format!("{}-trim-{by}", side.prefix()),
        AdjustmentToken::Bind(side, bitmap) => {
            format!("{}-bind-{}", side.prefix(), index.resolve(bitmap))
        }
    }
}

/// The inverse of [`adjustment_text`]: the token a text denotes, or `None` when it is not one (a side prefix other than `en` or `ex`, a count that is not a number, or a bitmap name this spec never interned). The only caller is the memo file reader in `memo.rs`.
pub fn adjustment_from_text(index: &SpecIndex, text: &str) -> Option<AdjustmentToken> {
    if text == "locked" {
        return Some(AdjustmentToken::Locked);
    }
    let (prefix, rest) = text.split_once('-')?;
    let side = match prefix {
        "en" => Side::Entry,
        "ex" => Side::Exit,
        _ => return None,
    };
    let (kind, value) = rest.split_once('-')?;
    match kind {
        "ext" => Some(AdjustmentToken::Extend(side, value.parse().ok()?)),
        "con" => Some(AdjustmentToken::Contract(side, value.parse().ok()?)),
        "trim" => Some(AdjustmentToken::Trim(side, value.parse().ok()?)),
        "bind" => Some(AdjustmentToken::Bind(side, index.sym_of(value)?)),
        _ => None,
    }
}

/// One cell's identity, `model.CellId`. `entry` and `exit` are the live heights of the two sides, and `None` means the side did not join. The adjustments are ordered and generated, never authored.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct CellId {
    pub rune: Sym,
    pub stance: Sym,
    pub entry: Option<Sym>,
    pub exit: Option<Sym>,
    pub adjustments: Vec<AdjustmentToken>,
}

/// What one position settled into, `model.Settled`: the cell, the seam committed toward the next position, and the connector pixels this side carries on that seam.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Settled {
    pub cell: CellId,
    pub seam: Option<Sym>,
    pub extension: i64,
}

/// The index of one distinct [`Settled`] record in a product's table, stored in a row in place of the record. A configuration reaches millions of rows but only a few thousand distinct settled records. Storing the record by value would copy it, with its heap-allocated adjustments, into hundreds of thousands of rows; a seat names it in four bytes, and comparing two rows' settled records is one integer comparison. A seat means nothing without its table, so it is used only inside a product and never written to the stream. The stream writer resolves the seat through the table and writes the cell's index in the stream head.
///
/// The integer is the index plus one, in a `NonZeroU32`, for the same reason as [`Sym`]: a row's left seat is absent for a boundary left, and the zero niche keeps `Option<SettledSeat>` at four bytes where `Option<u32>` takes eight. [`SettledSeat::at`] and [`SettledSeat::index`] are the only conversions, and nothing else reads the integer.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct SettledSeat(NonZeroU32);

impl SettledSeat {
    /// The seat for the table's `index`-th record.
    pub fn at(index: usize) -> Self {
        let raw = u32::try_from(index)
            .ok()
            .and_then(|index| index.checked_add(1))
            .expect("a configuration reaches fewer than 2^32 distinct settled records");
        Self(NonZeroU32::new(raw).expect("an index's successor is never zero"))
    }

    /// The seat as the table's index.
    pub fn index(self) -> usize {
        (self.0.get() - 1) as usize
    }
}

/// The table that gives each of one fixpoint's settled records a seat: each distinct record once, in the order the enumeration first reached it, with its seat. The map returns a record's seat and the table returns a seat's record. Together they hold two copies of a few thousand records, against the millions of rows the seats stand in for.
#[derive(Clone, Debug, Default)]
pub struct SettledPool {
    seats: HashMap<Settled, SettledSeat>,
    table: Vec<Settled>,
}

impl SettledPool {
    /// This record's seat, created the first time the record is seen and looked up in the map afterward.
    pub fn seat(&mut self, settled: &Settled) -> SettledSeat {
        if let Some(&seat) = self.seats.get(settled) {
            return seat;
        }
        let seat = SettledSeat::at(self.table.len());
        self.seats.insert(settled.clone(), seat);
        self.table.push(settled.clone());
        seat
    }

    /// The record one seat names.
    pub fn get(&self, seat: SettledSeat) -> &Settled {
        &self.table[seat.index()]
    }

    /// How many distinct records have seats.
    pub fn len(&self) -> usize {
        self.table.len()
    }

    pub fn is_empty(&self) -> bool {
        self.table.is_empty()
    }

    /// How many the table has room for, which is what the cache census reports beside the length.
    pub fn capacity(&self) -> usize {
        self.table.capacity()
    }

    /// The table alone, which is the part a product keeps. From here on seats resolve by index, and nothing after the fixpoint creates one.
    pub fn into_table(self) -> Vec<Settled> {
        self.table
    }
}

/// The index of one distinct provenance list in a product's table, stored in a row as its provenance. A row's notes name the records that eliminated, preferred, and adjusted at its window, in first-seen order. A configuration's millions of rows share a few thousand distinct lists, so a row that owned its list would duplicate a vector and one heap string per pointer across hundreds of thousands of rows; a seat names the list in four bytes. The rule fold reads a sample row's list through the table.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct NotesSeat(u32);

impl NotesSeat {
    /// The seat for the table's `index`-th list.
    pub fn at(index: usize) -> Self {
        Self(
            u32::try_from(index)
                .expect("a configuration reaches fewer than 2^32 distinct provenance lists"),
        )
    }

    /// The seat as the table's index.
    pub fn index(self) -> usize {
        self.0 as usize
    }
}

/// The table that gives each of one fixpoint's provenance lists a seat, built like [`SettledPool`] for the same reason: each distinct list once, in the order the enumeration first traced it, with its seat.
#[derive(Clone, Debug, Default)]
pub struct NotesPool {
    seats: HashMap<Vec<String>, NotesSeat>,
    table: Vec<Vec<String>>,
}

impl NotesPool {
    /// This list's seat, created the first time a trace carries the list and looked up in the map afterward. The list is passed by value because its trace is finished with it: a miss keeps the allocation, a hit drops it, and neither copies a string.
    pub fn seat(&mut self, notes: Vec<String>) -> NotesSeat {
        if let Some(&seat) = self.seats.get(notes.as_slice()) {
            return seat;
        }
        let seat = NotesSeat::at(self.table.len());
        self.seats.insert(notes.clone(), seat);
        self.table.push(notes);
        seat
    }

    /// The list one seat names.
    pub fn get(&self, seat: NotesSeat) -> &[String] {
        &self.table[seat.index()]
    }

    /// How many distinct lists have seats.
    pub fn len(&self) -> usize {
        self.table.len()
    }

    pub fn is_empty(&self) -> bool {
        self.table.is_empty()
    }

    /// How many the table has room for, which is what the cache census reports beside the length.
    pub fn capacity(&self) -> usize {
        self.table.capacity()
    }

    /// The table alone, which is the part a product keeps.
    pub fn into_table(self) -> Vec<Vec<String>> {
        self.table
    }
}

/// The left neighbor in the form the memo keys store: the settled cell's rune and stance and the committed seam, each as the [`Ordinal`] of its key field. They are resolved once, when the left is built, so building a key looks nothing up. All three are absent for a boundary left. Every read the kernel makes of a left's rune, stance, or seam first checks that the left is a letter. The only read of a settled record without that check is the commit's same-seam check of the extension, and `TraceKey` keys it by its own `left_extension` field. So a boundary left gets the same key whether or not its case question gave a record beside its kind.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub struct LeftOrdinals {
    pub rune: Option<Ordinal>,
    pub stance: Option<Ordinal>,
    pub seam: Option<Ordinal>,
}

impl LeftOrdinals {
    /// The three ordinals of a settled record used as a left, or `None` when its rune is not a registered family, its stance is not declared by any rune, or its seam is not a height the spec offers. No settlement of this spec produces such a cell, and `cases.rs` rejects a case question whose left names one.
    pub fn of(index: &SpecIndex, settled: &Settled) -> Option<Self> {
        let rune = index.rune_ordinal(settled.cell.rune)?;
        let stance = index.stance_ordinal(settled.cell.stance)?;
        let seam = match settled.seam {
            Some(seam) => Some(index.seam_ordinal(seam)?),
            None => None,
        };
        Some(Self {
            rune: Some(rune),
            stance: Some(stance),
            seam,
        })
    }
}

/// The resolved left neighbor a window is settled against, `settle.LeftContext`. The kind is never [`TokenKind::Unknown`], because a left is always already settled or known to be a boundary. `settled` is present for a letter left, and for a boundary left only when a case question gives it a record. The ordinals are the settled record's, as [`LeftOrdinals`] describes, stored beside it so the keys read them from the left instead of resolving them for each window.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct LeftContext {
    pub kind: TokenKind,
    pub settled: Option<Settled>,
    pub ordinals: LeftOrdinals,
}

impl LeftContext {
    /// A boundary left with no settled cell, as in `LeftContext("edge")` and the other boundary kinds.
    pub fn boundary(kind: TokenKind) -> Self {
        Self {
            kind,
            settled: None,
            ordinals: LeftOrdinals::default(),
        }
    }

    /// A letter left whose ordinals the caller already has, such as a candidate's, for the virtual left a follower is settled against.
    pub fn seated(settled: Settled, ordinals: LeftOrdinals) -> Self {
        Self {
            kind: TokenKind::Letter,
            settled: Some(settled),
            ordinals,
        }
    }

    /// A letter left, with the cell it settled into and that cell's key ordinals. Panics on a cell no settlement of this spec produces, because a letter left is always the settled record of a window this spec settled. A reader that takes a record from outside checks [`LeftOrdinals::of`] first.
    pub fn letter(index: &SpecIndex, settled: Settled) -> Self {
        let ordinals = LeftOrdinals::of(index, &settled).unwrap_or_else(|| {
            panic!(
                "a letter left settles into a cell of a registered family in a declared stance at a height the spec offers, and {} at {} does not",
                cell_label(index, &settled.cell),
                height_text(index, settled.seam)
            )
        });
        Self {
            kind: TokenKind::Letter,
            settled: Some(settled),
            ordinals,
        }
    }
}

/// A candidate's cell in the form the memo keys store: its rune, stance, entry, and seam, each as the [`Ordinal`] of its key field, resolved once when the candidate is enumerated. The prospect memo keys on these and the follower's virtual left carries them, so a lookup resolves nothing.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct CandidateOrdinals {
    pub rune: Ordinal,
    pub stance: Ordinal,
    pub entry: Option<Ordinal>,
    pub seam: Option<Ordinal>,
}

impl CandidateOrdinals {
    /// The ordinals of a cell of `rune`'s `stance` at these heights. Panics on a cell the enumeration never produces: a rune the registry does not know, a stance no rune declares, or a height no key field holds.
    pub fn of(
        index: &SpecIndex,
        rune: Sym,
        stance: Sym,
        entry: Option<Sym>,
        seam: Option<Sym>,
    ) -> Self {
        let missing = || {
            panic!(
                "a candidate is a stance of a modeled rune at heights the spec offers, and {}.{} entering at {} toward {} is not",
                index.resolve(rune),
                index.resolve(stance),
                height_text(index, entry),
                height_text(index, seam)
            )
        };
        Self {
            rune: index.rune_ordinal(rune).unwrap_or_else(missing),
            stance: index.stance_ordinal(stance).unwrap_or_else(missing),
            entry: entry.map(|height| index.entry_ordinal(height).unwrap_or_else(missing)),
            seam: seam.map(|height| index.seam_ordinal(height).unwrap_or_else(missing)),
        }
    }

    /// The left a follower settles against if this candidate wins: the cell's rune and stance at its seam.
    pub fn as_left(self) -> LeftOrdinals {
        LeftOrdinals {
            rune: Some(self.rune),
            stance: Some(self.stance),
            seam: self.seam,
        }
    }
}

/// One pair candidate, `settle.Candidate`: a cell of this rune with the seam state it offers toward the next position. `order_index` is the stance's rank in the rune's declared order, and `exit_index` is its exit row's declaration index; the later ranking stages read both. A non-joining candidate carries [`NO_EXIT_INDEX`]. The ordinals are the last field, so the derived ordering is determined by the five fields before them.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Candidate {
    pub stance: Sym,
    pub entry: Option<Sym>,
    pub seam: Option<Sym>,
    pub order_index: usize,
    pub exit_index: usize,
    pub ordinals: CandidateOrdinals,
}

impl Candidate {
    /// A candidate that offers a seam, with the index of the exit row it was enumerated from.
    pub fn joining(
        index: &SpecIndex,
        rune: Sym,
        stance: Sym,
        entry: Option<Sym>,
        seam: Sym,
        order_index: usize,
        exit_index: usize,
    ) -> Self {
        Self {
            stance,
            entry,
            seam: Some(seam),
            order_index,
            exit_index,
            ordinals: CandidateOrdinals::of(index, rune, stance, entry, Some(seam)),
        }
    }

    /// The stance's non-joining candidate: no seam, and the sentinel exit index that sorts after every real one.
    pub fn non_joining(
        index: &SpecIndex,
        rune: Sym,
        stance: Sym,
        entry: Option<Sym>,
        order_index: usize,
    ) -> Self {
        Self {
            stance,
            entry,
            seam: None,
            order_index,
            exit_index: NO_EXIT_INDEX,
            ordinals: CandidateOrdinals::of(index, rune, stance, entry, None),
        }
    }
}

/// Which enumeration test eliminated a candidate, `settle.Elimination`'s stage field. These six are all the stages `Engine::candidates_uncached` records.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum EliminationStage {
    EntryBinding,
    Require,
    Pairings,
    RowScope,
    LookaheadClosure,
    Refuse,
}

impl EliminationStage {
    /// The stage's name, as the trace writes it.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::EntryBinding => "entry-binding",
            Self::Require => "require",
            Self::Pairings => "pairings",
            Self::RowScope => "row-scope",
            Self::LookaheadClosure => "lookahead-closure",
            Self::Refuse => "refuse",
        }
    }
}

/// One candidate that did not survive enumeration, `settle.Elimination`. The description is a formatted message for people; `explain` and the review surface's explain view show it. The provenance is the authored record that eliminated the candidate, where there is one.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Elimination {
    pub stage: EliminationStage,
    pub description: String,
    pub provenance: Option<Provenance>,
}

/// A survivor with the two scores the ranking reads, `settle.RankedCandidate`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RankedCandidate {
    pub candidate: Candidate,
    pub join_count: i64,
    pub prospect: i64,
}

/// Which stage of the lexicographic ranking decided the window, `settle.TransitionTrace.decided_stage`. `Boundary` is the shortcut a non-letter input takes, and the other stages are listed in the order the ranking runs them.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum DecidedStage {
    Boundary,
    OnlyCandidate,
    AbsolutePrefer,
    JoinCount,
    YieldingPrefer,
    Order,
    Floor,
}

impl DecidedStage {
    /// The seven stages in ranking order, each at the index [`DecidedStage::ordinal`] returns for it. [`DecidedStage::from_text`] and the tests iterate over this table.
    pub const ALL: [Self; 7] = [
        Self::Boundary,
        Self::OnlyCandidate,
        Self::AbsolutePrefer,
        Self::JoinCount,
        Self::YieldingPrefer,
        Self::Order,
        Self::Floor,
    ];

    /// The stage's position in ranking order, zero through six. The trace memo's packed entry stores a stage as this value, in three bits of its one byte. It is an exhaustive match, not `as u8`, so adding a stage to the enum is a compile error here and in [`DecidedStage::from_ordinal`].
    pub fn ordinal(self) -> u8 {
        match self {
            Self::Boundary => 0,
            Self::OnlyCandidate => 1,
            Self::AbsolutePrefer => 2,
            Self::JoinCount => 3,
            Self::YieldingPrefer => 4,
            Self::Order => 5,
            Self::Floor => 6,
        }
    }

    /// The stage at one ordinal, or `None` above six. The inverse of [`DecidedStage::ordinal`], for the packed entry.
    pub fn from_ordinal(ordinal: u8) -> Option<Self> {
        match ordinal {
            0 => Some(Self::Boundary),
            1 => Some(Self::OnlyCandidate),
            2 => Some(Self::AbsolutePrefer),
            3 => Some(Self::JoinCount),
            4 => Some(Self::YieldingPrefer),
            5 => Some(Self::Order),
            6 => Some(Self::Floor),
            _ => None,
        }
    }

    /// The stage a name denotes, or `None` for text that is not one of the seven. The inverse of [`DecidedStage::as_str`], for the memo file, which stores a stage for each entry.
    pub fn from_text(text: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|stage| stage.as_str() == text)
    }

    /// The stage's name, as the trace writes it.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Boundary => "boundary",
            Self::OnlyCandidate => "only-candidate",
            Self::AbsolutePrefer => "absolute-prefer",
            Self::JoinCount => "join-count",
            Self::YieldingPrefer => "yielding-prefer",
            Self::Order => "order",
            Self::Floor => "floor",
        }
    }
}

/// How a window was decided, as opposed to what it settled into. This is the explain part of `settle.TransitionTrace`: the ranking every survivor was scored into, every eliminated candidate with the message saying why, and the runner-up. Only the explain CLI, the probe, and the review surface's explain panel read it; building the font does not.
///
/// It is a separate type, boxed where a trace carries one, because the table fixpoint settles millions of windows and reads none of this. Building a ladder there would be a large allocation that nothing uses.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TraceLadder {
    pub ranked: Vec<RankedCandidate>,
    pub eliminations: Vec<Elimination>,
    pub runner_up: Option<Candidate>,
}

/// The ladder returned by a trace that carries none, so any trace can be asked for its ranking and return an empty one.
static NO_LADDER: TraceLadder = TraceLadder {
    ranked: Vec::new(),
    eliminations: Vec::new(),
    runner_up: None,
};

/// The full settlement result, `settle.TransitionTrace`: what the window settled into, plus the details of how it was decided that the table build, the explain CLI, and the review surface read. Notes are formatted strings: YAML pointers and short messages such as `prefer applied: <pointer>` and `unlocked by <feature>`.
///
/// The explain part is in [`TransitionTrace::ladder`]. It is absent when the engine was built without [`crate::engine::EngineModes::explain_ladder`], as the table fixpoint and the string replay are.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TransitionTrace {
    pub settled: Settled,
    pub joint_floor: bool,
    pub prospect: i64,
    pub decided_stage: DecidedStage,
    pub notes: Vec<String>,
    pub ladder: Option<Box<TraceLadder>>,
}

impl TransitionTrace {
    /// How this window was decided, or the empty ladder when the engine was not asked to record one.
    pub fn ladder(&self) -> &TraceLadder {
        self.ladder.as_deref().unwrap_or(&NO_LADDER)
    }
}

/// The closed vocabulary interned into one spec's string pool, so every comparison the kernel makes against an authored value compares symbols, not strings. [`SpecIndex`] builds one per spec. The symbols are meaningful only with that spec's interner.
#[derive(Clone, Debug)]
pub struct Vocab {
    pub edge: Sym,
    pub space: Sym,
    pub zwnj: Sym,
    pub namer_dot: Sym,
    pub letter: Sym,
    pub unknown: Sym,
    /// `boundary`, which is both the `is:` value that expands to the four boundary kinds and the stance name of every boundary cell. It is one string, so it is one symbol in both roles.
    pub boundary: Sym,
    pub none: Sym,
    pub live: Sym,
    pub initial: Sym,
    pub medial: Sym,
    pub final_: Sym,
    pub isolated: Sym,
    pub entry: Sym,
    pub exit: Sym,
    pub stance: Sym,
    /// `absolute`, the `prefer` mode that ranks before join count rather than after it.
    pub absolute: Sym,
    /// `safe`, the withdrawal that collapses to the plain exit-none cell instead of binding a sibling bitmap.
    pub safe: Sym,
}

impl Vocab {
    /// Interns the closed vocabulary through one interning closure. Interning, not looking up, guarantees every symbol exists: a spec that never mentions `live` still gets a symbol for it, and one that does gets the same symbol its own text resolves to.
    pub fn build(mut intern: impl FnMut(&str) -> Sym) -> Self {
        Self {
            edge: intern("edge"),
            space: intern("space"),
            zwnj: intern("zwnj"),
            namer_dot: intern("namer-dot"),
            letter: intern("letter"),
            unknown: intern("unknown"),
            boundary: intern("boundary"),
            none: intern(NONE_STATE),
            live: intern(LIVE_STATE),
            initial: intern("initial"),
            medial: intern("medial"),
            final_: intern("final"),
            isolated: intern("isolated"),
            entry: intern("entry"),
            exit: intern("exit"),
            stance: intern("stance"),
            absolute: intern("absolute"),
            safe: intern("safe"),
        }
    }

    /// The symbol an `is:` condition names this kind with.
    pub fn kind(&self, kind: TokenKind) -> Sym {
        match kind {
            TokenKind::Edge => self.edge,
            TokenKind::Space => self.space,
            TokenKind::Zwnj => self.zwnj,
            TokenKind::NamerDot => self.namer_dot,
            TokenKind::Letter => self.letter,
            TokenKind::Unknown => self.unknown,
        }
    }

    /// The symbol a `word:` condition names this position with.
    pub fn word(&self, position: WordPosition) -> Sym {
        match position {
            WordPosition::Initial => self.initial,
            WordPosition::Medial => self.medial,
            WordPosition::Final => self.final_,
            WordPosition::Isolated => self.isolated,
        }
    }

    /// The symbol a `require:` list or a `cells:` row names this side with.
    pub fn side(&self, side: Side) -> Sym {
        match side {
            Side::Entry => self.entry,
            Side::Exit => self.exit,
        }
    }

    /// A side's state in the `live`/`none` vocabulary that `self_entry:` and `self_exit:` use.
    pub fn liveness_state(&self, height: Option<Sym>) -> Sym {
        match height {
            Some(_) => self.live,
            None => self.none,
        }
    }

    /// A side's state in the height-or-`none` vocabulary that pairings, `cells:` rows, and `joined_at:` use.
    pub fn height_state(&self, height: Option<Sym>) -> Sym {
        height.unwrap_or(self.none)
    }
}

/// The cell a boundary settles into, `settle.boundary_cell`: the kind's own name as the rune, and the boundary stance.
pub fn boundary_cell(vocab: &Vocab, kind: TokenKind) -> CellId {
    CellId {
        rune: vocab.kind(kind),
        stance: vocab.boundary,
        entry: None,
        exit: None,
        adjustments: Vec::new(),
    }
}

/// The settled record for a boundary, `settle.boundary_settled`: no seam and no extension, because a boundary offers neither.
pub fn boundary_settled(vocab: &Vocab, kind: TokenKind) -> Settled {
    Settled {
        cell: boundary_cell(vocab, kind),
        seam: None,
        extension: 0,
    }
}

/// Whether a settled record belongs to a boundary, `settle.is_boundary_settled`. The stance is the marker, because no rune declares a stance by that name.
pub fn is_boundary_settled(vocab: &Vocab, settled: &Settled) -> bool {
    settled.cell.stance == vocab.boundary
}

/// One authored record's YAML pointer, `file:path`, as `str(Provenance)` formats it. The fired set, the notes, and every settlement error message use this string.
pub fn provenance_pointer(index: &SpecIndex, provenance: &Provenance) -> String {
    format!(
        "{}:{}",
        index.resolve(provenance.file),
        index.resolve(provenance.path)
    )
}

/// One settled record as JSON, in the shape `kernel_exec.settled_of_row` reads: `{"cell":[rune,stance,entry,exit,[adjustments]],"seam":…,"extension":…}`, with a height as its name or `null`. The `settle-cases` trace answer writes its `settled` key with this function, and the replay's window memo writes one record per line with it, so Python decodes one JSON shape. [`settled_fields`] is the tab-separated form the settled-only answer uses.
pub(crate) fn settled_json(index: &SpecIndex, settled: &Settled) -> String {
    let adjustments: Vec<String> = settled
        .cell
        .adjustments
        .iter()
        .map(|token| json_string(&adjustment_text(index, *token)))
        .collect();
    format!(
        "{{\"cell\":[{},{},{},{},[{}]],\"seam\":{},\"extension\":{}}}",
        json_string(index.resolve(settled.cell.rune)),
        json_string(index.resolve(settled.cell.stance)),
        height_json(index, settled.cell.entry),
        height_json(index, settled.cell.exit),
        adjustments.join(","),
        height_json(index, settled.seam),
        settled.extension
    )
}

/// One settled record as seven tab-separated fields, in the shape `kernel_exec._settled_of_fields` reads: rune, stance, entry, exit, comma-joined adjustments, seam, extension, with an empty field for a missing height. A `settle-cases` question gives its left record in the same seven fields (`cases::parse_settled` reads them), so a settled-only answer can serve as the next question's left.
pub(crate) fn settled_fields(index: &SpecIndex, settled: &Settled) -> String {
    let adjustments: Vec<String> = settled
        .cell
        .adjustments
        .iter()
        .map(|token| adjustment_text(index, *token))
        .collect();
    format!(
        "{}\t{}\t{}\t{}\t{}\t{}\t{}",
        index.resolve(settled.cell.rune),
        index.resolve(settled.cell.stance),
        height_text(index, settled.cell.entry),
        height_text(index, settled.cell.exit),
        adjustments.join(","),
        height_text(index, settled.seam),
        settled.extension
    )
}

/// A height as its name, or empty: the tab-separated counterpart of [`height_json`].
pub(crate) fn height_text(index: &SpecIndex, height: Option<Sym>) -> &str {
    height.map_or("", |height| index.resolve(height))
}

/// A height as its name or `null`, for every JSON record with a height field.
pub(crate) fn height_json(index: &SpecIndex, height: Option<Sym>) -> String {
    match height {
        Some(height) => json_string(index.resolve(height)),
        None => "null".to_owned(),
    }
}

/// A deterministic text form of a cell, `settle.cell_label`: the stable name the kernel's TSV artifacts and the E-STRANDED message use. It has the same shape as geometry's compiled display name but is not that name; geometry's is capped at 63 bytes and this one is not.
///
/// A boundary cell is labeled with the glyph its kind ships as. The run edge has no glyph, so labeling it panics, as the Python mapping raises `KeyError`. Reaching that means a caller labeled a cell the fold never records.
pub fn cell_label(index: &SpecIndex, cell: &CellId) -> String {
    let vocab = index.vocab();
    if cell.stance == vocab.boundary {
        if cell.rune == vocab.space {
            return "space".to_owned();
        }
        if cell.rune == vocab.zwnj {
            return "uni200C".to_owned();
        }
        if cell.rune == vocab.namer_dot {
            return "periodcentered".to_owned();
        }
        panic!(
            "the {} boundary has no cell label, exactly as settle.cell_label's mapping has no key for it",
            index.resolve(cell.rune)
        );
    }
    let mut label = String::new();
    label.push_str(index.resolve(cell.rune));
    label.push('.');
    label.push_str(index.resolve(cell.stance));
    if let Some(entry) = cell.entry {
        label.push_str(&format!(".en-y{}", height_y(index, entry)));
    }
    if let Some(exit) = cell.exit {
        label.push_str(&format!(".ex-y{}", height_y(index, exit)));
    }
    for token in &cell.adjustments {
        label.push('.');
        label.push_str(&adjustment_text(index, *token));
    }
    label
}

fn height_y(index: &SpecIndex, height: Sym) -> i64 {
    index
        .y_of(height)
        .expect("every cell height is registry-declared, as model.ScriptRegistry.y_of assumes")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;

    /// The ordinal table and the enum agree: every stage is in `ALL` at its own ordinal, every ordinal reads back its stage, the first ordinal past the seven reads nothing, and the seven fit in the packed entry's three bits.
    #[test]
    fn a_stage_round_trips_through_its_ordinal() {
        for (index, stage) in DecidedStage::ALL.into_iter().enumerate() {
            let ordinal = u8::try_from(index).expect("seven ordinals");
            assert_eq!(stage.ordinal(), ordinal);
            assert_eq!(DecidedStage::from_ordinal(ordinal), Some(stage));
            assert_eq!(DecidedStage::from_text(stage.as_str()), Some(stage));
        }
        assert_eq!(
            DecidedStage::from_ordinal(u8::try_from(DecidedStage::ALL.len()).expect("seven")),
            None
        );
        assert!(DecidedStage::ALL.len() <= 8);
    }

    #[test]
    fn word_position_reads_only_the_splitting_boundaries() {
        use TokenKind::{Edge, Letter, NamerDot, Space, Unknown, Zwnj};
        assert_eq!(word_position(Edge, Edge), Some(WordPosition::Isolated));
        assert_eq!(word_position(Space, Letter), Some(WordPosition::Initial));
        assert_eq!(word_position(Letter, Zwnj), Some(WordPosition::Final));
        assert_eq!(word_position(Letter, Letter), Some(WordPosition::Medial));
        assert_eq!(word_position(NamerDot, Letter), Some(WordPosition::Medial));
        assert_eq!(word_position(Letter, NamerDot), Some(WordPosition::Medial));
        assert_eq!(word_position(Edge, Unknown), None);
        assert_eq!(word_position(Letter, Unknown), None);
    }

    #[test]
    fn the_kind_predicates_agree_with_the_constant_lists() {
        for kind in [
            TokenKind::Edge,
            TokenKind::Space,
            TokenKind::Zwnj,
            TokenKind::NamerDot,
            TokenKind::Letter,
            TokenKind::Unknown,
        ] {
            assert_eq!(kind.splits_runs(), SPLITTING_KINDS.contains(&kind.as_str()));
            assert_eq!(kind.is_boundary(), BOUNDARY_KINDS.contains(&kind.as_str()));
            assert_eq!(TokenKind::from_text(kind.as_str()), Some(kind));
        }
        assert_eq!(TokenKind::from_text("boundary"), None);
    }

    #[test]
    fn a_token_compares_on_its_kind_and_its_rune() {
        let index = fixtures::mini();
        let tea = fixtures::sym(&index, "qsTea");
        let tea_token = fixtures::letter(&index, "qsTea");
        let pea_token = fixtures::letter(&index, "qsPea");
        assert_ne!(UNKNOWN, EDGE);
        assert_ne!(tea_token, pea_token);
        assert_eq!(tea_token, fixtures::letter(&index, "qsTea"));
        assert_eq!(tea_token.kind(), TokenKind::Letter);
        assert_eq!(tea_token.letter(), tea);
        assert_eq!(tea_token.ordinal(), index.rune_ordinal(tea));
        assert_eq!(Some(tea_token.letter_ordinal()), index.rune_ordinal(tea));
        assert_eq!(EDGE.rune(), None);
        assert_eq!(EDGE.ordinal(), None);
        assert_eq!(RightToken::of_kind(TokenKind::Zwnj), Some(ZWNJ));
        assert_eq!(RightToken::of_kind(TokenKind::Letter), None);
        assert_eq!(std::mem::size_of::<RightToken>(), 8);
    }

    /// Five kinds in one word: every slot reads back what was packed into it, and changing any one slot's kind changes the word.
    #[test]
    fn packed_kinds_read_back_slot_by_slot() {
        let all = [
            TokenKind::Edge,
            TokenKind::Space,
            TokenKind::Zwnj,
            TokenKind::NamerDot,
            TokenKind::Letter,
            TokenKind::Unknown,
        ];
        let packed = PackedKinds::of(&[
            TokenKind::Unknown,
            TokenKind::Letter,
            TokenKind::Edge,
            TokenKind::NamerDot,
            TokenKind::Space,
        ]);
        assert_eq!(packed.get(0), TokenKind::Unknown);
        assert_eq!(packed.get(1), TokenKind::Letter);
        assert_eq!(packed.get(2), TokenKind::Edge);
        assert_eq!(packed.get(3), TokenKind::NamerDot);
        assert_eq!(packed.get(4), TokenKind::Space);
        assert_eq!(PackedKinds::of(&[]).get(4), TokenKind::Edge);
        let mut words = std::collections::BTreeSet::new();
        for slot in 0..PackedKinds::CAPACITY {
            for kind in all {
                let mut kinds = [TokenKind::Edge; PackedKinds::CAPACITY];
                kinds[slot] = kind;
                let word = PackedKinds::of(&kinds);
                assert_eq!(word.get(slot), kind);
                words.insert(word);
            }
        }
        assert_eq!(words.len(), 1 + PackedKinds::CAPACITY * (all.len() - 1));
        assert_eq!(std::mem::size_of::<PackedKinds>(), 2);
    }

    #[test]
    fn a_boundary_token_with_no_rune_panics_when_read_as_a_letter() {
        let complaint = std::panic::catch_unwind(|| UNKNOWN.letter())
            .expect_err("reading a boundary token's rune is a kernel bug, not an outcome");
        let message = complaint
            .downcast_ref::<String>()
            .expect("the panic carries its sentence");
        assert_eq!(message, "unknown token has no rune");
    }

    #[test]
    fn a_boundary_settles_into_its_own_kind_named_cell() {
        let index = fixtures::mini();
        let vocab = index.vocab();
        let settled = boundary_settled(vocab, TokenKind::Zwnj);
        assert!(is_boundary_settled(vocab, &settled));
        assert_eq!(settled.seam, None);
        assert_eq!(settled.extension, 0);
        assert_eq!(settled.cell, boundary_cell(vocab, TokenKind::Zwnj));
        assert_eq!(cell_label(&index, &settled.cell), "uni200C");
        assert_eq!(
            cell_label(&index, &boundary_cell(vocab, TokenKind::Space)),
            "space"
        );
        assert_eq!(
            cell_label(&index, &boundary_cell(vocab, TokenKind::NamerDot)),
            "periodcentered"
        );
    }

    #[test]
    fn a_cell_labels_as_its_rune_stance_heights_and_adjustments() {
        let index = fixtures::mini();
        let cell = CellId {
            rune: fixtures::sym(&index, "qsTea"),
            stance: fixtures::sym(&index, "half"),
            entry: Some(fixtures::sym(&index, "baseline")),
            exit: Some(fixtures::sym(&index, "x-height")),
            adjustments: vec![
                AdjustmentToken::Locked,
                AdjustmentToken::Extend(Side::Entry, 1),
                AdjustmentToken::Contract(Side::Exit, 2),
                AdjustmentToken::Trim(Side::Entry, 3),
                AdjustmentToken::Bind(Side::Exit, fixtures::sym(&index, "pulled-back")),
            ],
        };
        assert_eq!(
            cell_label(&index, &cell),
            "qsTea.half.en-y0.ex-y5.locked.en-ext-1.ex-con-2.en-trim-3.ex-bind-pulled-back"
        );
        let bare = CellId {
            rune: fixtures::sym(&index, "qsTea"),
            stance: fixtures::sym(&index, "half"),
            entry: None,
            exit: None,
            adjustments: Vec::new(),
        };
        assert_eq!(cell_label(&index, &bare), "qsTea.half");
    }

    #[test]
    fn a_state_is_the_height_or_the_none_symbol() {
        let index = fixtures::mini();
        let vocab = index.vocab();
        let baseline = fixtures::sym(&index, "baseline");
        assert_eq!(vocab.height_state(Some(baseline)), baseline);
        assert_eq!(vocab.height_state(None), vocab.none);
        assert_eq!(vocab.liveness_state(Some(baseline)), vocab.live);
        assert_eq!(vocab.liveness_state(None), vocab.none);
        assert_eq!(index.resolve(vocab.none), NONE_STATE);
        assert_eq!(index.resolve(vocab.live), LIVE_STATE);
        assert_eq!(index.resolve(vocab.boundary), BOUNDARY_STANCE);
        assert_eq!(index.resolve(vocab.kind(TokenKind::NamerDot)), "namer-dot");
        assert_eq!(index.resolve(vocab.word(WordPosition::Final)), "final");
        assert_eq!(index.resolve(vocab.side(Side::Exit)), "exit");
    }

    #[test]
    fn a_non_joining_candidate_sorts_after_every_real_exit_row() {
        let index = fixtures::mini();
        let pea = fixtures::sym(&index, "qsPea");
        let stance = fixtures::sym(&index, "half");
        let seam = fixtures::sym(&index, "baseline");
        let joining = Candidate::joining(&index, pea, stance, None, seam, 0, 3);
        let non_joining = Candidate::non_joining(&index, pea, stance, None, 0);
        assert_eq!(
            joining.ordinals.rune,
            index.rune_ordinal(pea).expect("modeled")
        );
        assert_eq!(
            joining.ordinals.stance,
            index.stance_ordinal(stance).expect("declared")
        );
        assert_eq!(joining.ordinals.seam, index.seam_ordinal(seam));
        assert_eq!(non_joining.ordinals.seam, None);
        assert_eq!(non_joining.ordinals.entry, None);
        assert_eq!(non_joining.exit_index, NO_EXIT_INDEX);
        assert!(joining.exit_index < non_joining.exit_index);
        assert_eq!(non_joining.seam, None);
        assert_eq!(joining.seam, Some(seam));
    }

    #[test]
    fn the_stage_spellings_are_the_ones_the_trace_carries() {
        assert_eq!(EliminationStage::EntryBinding.as_str(), "entry-binding");
        assert_eq!(
            EliminationStage::LookaheadClosure.as_str(),
            "lookahead-closure"
        );
        assert_eq!(DecidedStage::OnlyCandidate.as_str(), "only-candidate");
        assert_eq!(DecidedStage::YieldingPrefer.as_str(), "yielding-prefer");
        assert_eq!(DecidedStage::Boundary.as_str(), "boundary");
    }

    #[test]
    fn a_provenance_prints_as_the_pointer_the_fired_set_holds() {
        let index = fixtures::mini();
        let provenance = index
            .rune(fixtures::sym(&index, "qsTea"))
            .expect("qsTea is modeled")
            .policy
            .refuse[0]
            .provenance
            .clone()
            .expect("the fixture's refusal carries provenance");
        assert_eq!(
            provenance_pointer(&index, &provenance),
            "qsTea.yaml:policy.refuse[0]"
        );
    }
}
