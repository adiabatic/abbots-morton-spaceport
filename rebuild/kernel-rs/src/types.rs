//! The settlement vocabulary: the values the engine hands between its stages, packed so that every string a dump or a Python caller spells is one of three things here — an interned [`Sym`] when the spec authored it, a closed enum when the kernel itself closed the set, or an owned `String` when nothing but formatting ever reads it. `rebuild/pipeline/settle.py` keeps the same vocabulary as plain classes, for the tokenizing and ligature-forming that happen before a window reaches this crate and for the answers that come back out of it.
//!
//! That split is the module's whole rule. A rune, a stance, a height, a bitmap name and a class name are authored, so they ride as symbols and compare on a `u32`; the six right-token kinds, the four word positions, the elimination and decided stages, and the adjustments grammar `model.py` documents are the kernel's own closed vocabularies, so they ride as enums and cannot be misspelled. A test like `left.kind != cond.is_token`, which a dump spells as two plain strings, compares one of each — a kernel kind against an authored value — and here it compares the condition's symbol against [`Vocab`], the one place the closed vocabulary is looked up in the spec's own string pool.
//!
//! Entry and exit *states* are the subtle case and are deliberately not `Option<Sym>`. Wherever a height is compared against the literal `"none"` — pairings, `cells:` rows, `joined_at`, `self_entry`, each of them spelled that way in the dump — the value being compared is a state, a single symbol that is either a height or the none state, and [`Vocab::height_state`] is the one crossing between it and the `Option<Sym>` form that means "this side is live at this height, or is not live at all". Keeping the two shapes distinct is what stops a `None` from silently comparing unequal to an authored `none`.
//!
//! Nothing in this module reads the spec beyond resolving symbols for the two places settlement formats a name into prose — [`cell_label`], which the E-STRANDED message and the TSV artifacts read, and [`adjustment_text`], which spells the generated tokens. Both take the [`SpecIndex`] rather than a bare interner, because a label also needs the registry's height-to-y map, and because the index is what every caller already has in hand.

use std::collections::HashMap;
use std::num::NonZeroU32;

use crate::index::SpecIndex;
use crate::model::{Provenance, Sym};

/// The boundary kinds that split a run, and therefore the ones word position is derived from. `settle.SPLITTING_KINDS`; [`TokenKind::splits_runs`] is the form the code actually asks, and a test pins the two in agreement.
pub const SPLITTING_KINDS: [&str; 3] = ["edge", "space", "zwnj"];

/// Every kind that is not a letter. `settle.BOUNDARY_KINDS` — the namer dot joins the splitting three here, because it is a boundary that does not split.
pub const BOUNDARY_KINDS: [&str; 4] = ["edge", "space", "zwnj", "namer-dot"];

/// The stance name a boundary cell carries, and the marker [`is_boundary_settled`] reads. `settle.BOUNDARY_STANCE`.
pub const BOUNDARY_STANCE: &str = "boundary";

/// The state a side that did not join is spelled with. `model.NONE_STATE`.
pub const NONE_STATE: &str = "none";

/// The state a side that did join is spelled with in `self_entry:` and `self_exit:`, whose vocabulary is `live` or `none` rather than a height. Python spells it inline; it is named here so the interned vocabulary has one source.
pub const LIVE_STATE: &str = "live";

/// The suffix a `cells:` row spells a withdrawn exit state with. `model.WITHDRAWN_SUFFIX`; [`SpecIndex::withdrawn_state`] is where a height and this suffix become one symbol.
pub const WITHDRAWN_SUFFIX: &str = "-withdrawn";

/// The exit index a non-joining candidate carries, `settle._NO_EXIT_INDEX`. It is a sentinel rather than an `Option` because it exists to sort after every real exit row in the structural floor and in the ranked tiebreak, and real exit counts are single digits, so an ordinary comparison against a large number is both the Python behavior and the cheap one.
pub const NO_EXIT_INDEX: usize = 9999;

/// What a window slot holds, apart from which letter. The six kinds are closed by `settle.RightToken`'s own comment, and `unknown` is the one that means "outside the evaluated window" rather than a thing in the text — the value the three-valued right-condition matching turns into its `None` verdict.
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
    /// The spelling the spec authors this kind with, and the one every artifact prints.
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

    /// The kind a spelling names, or `None` for text that is not one of the six. The reader half of [`TokenKind::as_str`], for the corpus replay that meets these as JSON strings.
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

    /// Whether this kind ends a run, which is what word position is derived from. Membership in [`SPLITTING_KINDS`]: the namer dot is a boundary that deliberately does not split, so it leaves both of its neighbors medial.
    pub fn splits_runs(self) -> bool {
        matches!(self, Self::Edge | Self::Space | Self::Zwnj)
    }

    /// Whether this kind is one of the four boundaries — membership in [`BOUNDARY_KINDS`], which is what an `is: boundary` condition expands to.
    pub fn is_boundary(self) -> bool {
        matches!(self, Self::Edge | Self::Space | Self::Zwnj | Self::NamerDot)
    }
}

/// One raw window slot: a boundary, an unknown, or a letter naming its rune. `settle.RightToken`, whose equality this reproduces exactly — the kind is part of the value, so UNKNOWN and EDGE are different tokens rather than two spellings of "nothing useful", and two letter tokens are equal exactly when their runes are.
///
/// The derived ordering exists so a token can key a `BTreeMap` and compares on interning order, which is the order the dump happened to mention names in and nothing else. Anywhere an *output* order depends on a token — the guard sweep's rows, for one — sort by the resolved name instead.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum RightToken {
    Edge,
    Space,
    Zwnj,
    NamerDot,
    Unknown,
    Letter(Sym),
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
            Self::Letter(_) => TokenKind::Letter,
        }
    }

    /// The rune this slot names, or `None` when it is not a letter — `RightToken.rune`, the field reads that have not yet established there is one.
    pub fn rune(self) -> Option<Sym> {
        match self {
            Self::Letter(rune) => Some(rune),
            _ => None,
        }
    }

    /// The rune this slot names, for the reads that have already established there is one. Panics on any other kind, exactly as `RightToken.letter` raises `ValueError`: reaching it means a caller skipped the kind check, which is a kernel bug and not a settlement outcome.
    pub fn letter(self) -> Sym {
        match self {
            Self::Letter(rune) => rune,
            other => panic!("{} token has no rune", other.kind().as_str()),
        }
    }

    /// The boundary or unknown token for a kind, or `None` for [`TokenKind::Letter`], which needs a rune. The reader half of [`RightToken::kind`], for the corpus replay that rebuilds tokens from their JSON.
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

/// Where in a word a position sits, derived from run-splitting boundaries alone. The vocabulary a `word:` condition names is closed to these four.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum WordPosition {
    Initial,
    Medial,
    Final,
    Isolated,
}

impl WordPosition {
    /// The spelling a `word:` condition names this position with.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Initial => "initial",
            Self::Medial => "medial",
            Self::Final => "final",
            Self::Isolated => "isolated",
        }
    }
}

/// Word position from the two kinds around a position, `settle.word_position`. `None` means the right slot is outside the evaluated window and the position is therefore not yet decidable — the unknown that `when_matches` propagates rather than guesses at.
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
    /// The two-letter prefix the adjustments grammar spells this side with — `en` or `ex`.
    pub fn prefix(self) -> &'static str {
        match self {
            Self::Entry => "en",
            Self::Exit => "ex",
        }
    }

    /// The spelling a `require:` entry or a `cells:` key names this side with — `entry` or `exit`.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Entry => "entry",
            Self::Exit => "exit",
        }
    }
}

/// One generated `CellId.adjustments` token in the closed grammar `model.py` documents, held apart rather than as text so that a token cannot be spelled wrong and so the later packing has something already packed. `model.parse_adjustment` is the Python reader of the same grammar, and [`adjustment_text`] writes the spelling both agree on.
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

/// The token's spelling — what geometry reads and what the corpus carries as a JSON string.
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

/// The reader half of [`adjustment_text`]: the token one spelling names, or `None` for text that is not one — a side prefix the grammar does not spell, a count that is not a number, or a bitmap name this spec never interned. The one caller is the memo file's reader, which meets these as the text the writer spelled.
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

/// One cell's identity, `model.CellId`. `entry` and `exit` are the live heights of the two sides, `None` meaning the side did not join; the adjustments are ordered and generated, never authored.
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

/// The seat one distinct [`Settled`] record holds in the table a product carries, and what a recorded row holds in its place. A configuration reaches millions of rows over a few thousand distinct settled records, so a row that held the record by value was holding a copy hundreds of thousands of its neighbors held too — each with the cell's own heap allocation for the adjustments — where four bytes name the same record, and two rows' settled halves compare as one integer compare. A seat means nothing without the table it indexes, which is why it rides only inside a product and never in the stream: the head's cell seat is what the stream spells, resolved through the table on the way out.
///
/// The integer is the index's successor in a `NonZeroU32`, for the reason [`Sym`] is: a row's left seat is absent for a boundary left, and `Option<SettledSeat>` costs a whole word beside a plain `u32` where the zero niche folds `None` into the same four bytes (issue #163). The offset is this type's business alone — [`SettledSeat::at`] and [`SettledSeat::index`] are the two crossings, and nothing else reads the integer.
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

/// The table one fixpoint seats its settled records through: every distinct record once, in the order the enumeration first reached it, and the seat each one holds. The map answers a record's seat and the table answers a seat's record, and the two copies of each record it holds between them are a few thousand entries against the millions of rows the seats stand in for.
#[derive(Clone, Debug, Default)]
pub struct SettledPool {
    seats: HashMap<Settled, SettledSeat>,
    table: Vec<Settled>,
}

impl SettledPool {
    /// This record's seat, minted on the first reach and answered from the map on every later one.
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

    /// How many distinct records have been seated.
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

    /// The table alone, which is the half a product carries: seats resolve by index from here on and nothing past the fixpoint mints one.
    pub fn into_table(self) -> Vec<Settled> {
        self.table
    }
}

/// The seat one distinct provenance list holds in the table a product carries, and what a recorded row holds as its provenance. A row's notes are the pointers of the records that eliminated, preferred and adjusted at its window, in first-seen order, and a configuration's millions of rows spell a few thousand distinct such lists between them — so a row that owned its list was holding a vector and one heap string per pointer that hundreds of thousands of its neighbors held too, where four bytes name the same list (issue #163). The rule fold reads a sample row's list back through the table, so what it joins is what it always joined.
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

/// The table one fixpoint seats its rows' provenance through, the same shape as [`SettledPool`] for the same reason: every distinct list once, in the order the enumeration first traced it, and the seat each one holds.
#[derive(Clone, Debug, Default)]
pub struct NotesPool {
    seats: HashMap<Vec<String>, NotesSeat>,
    table: Vec<Vec<String>>,
}

impl NotesPool {
    /// This list's seat, minted on the first trace that carried it and answered from the map on every later one. The list arrives owned because the trace it came off is done with it: a miss keeps the allocation and a hit drops it, and neither copies a string.
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

    /// How many distinct lists have been seated.
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

    /// The table alone, which is the half a product carries.
    pub fn into_table(self) -> Vec<Vec<String>> {
        self.table
    }
}

/// The resolved left neighbor a window is settled against, `settle.LeftContext`. The kind is never [`TokenKind::Unknown`] — a left is always already settled or already known to be a boundary — and `settled` is present exactly for a letter left and for the boundary cells the fold records.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct LeftContext {
    pub kind: TokenKind,
    pub settled: Option<Settled>,
}

impl LeftContext {
    /// A boundary left, carrying no settled cell — `LeftContext("edge")` and its three siblings.
    pub fn boundary(kind: TokenKind) -> Self {
        Self {
            kind,
            settled: None,
        }
    }

    /// A letter left, carrying the cell it settled into.
    pub fn letter(settled: Settled) -> Self {
        Self {
            kind: TokenKind::Letter,
            settled: Some(settled),
        }
    }
}

/// One pair candidate, `settle.Candidate`: a cell of this rune together with the seam state it offers toward the next position. `order_index` is the stance's rank in the rune's declared order and `exit_index` its exit row's declaration seat, both of which the ranking's later stages read; a non-joining candidate carries [`NO_EXIT_INDEX`].
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Candidate {
    pub stance: Sym,
    pub entry: Option<Sym>,
    pub seam: Option<Sym>,
    pub order_index: usize,
    pub exit_index: usize,
}

impl Candidate {
    /// A candidate that offers a seam, at the exit row seat it was enumerated from.
    pub fn joining(
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
        }
    }

    /// The stance's non-joining candidate — no seam, and the sentinel exit index that sorts after every real row.
    pub fn non_joining(stance: Sym, entry: Option<Sym>, order_index: usize) -> Self {
        Self {
            stance,
            entry,
            seam: None,
            order_index,
            exit_index: NO_EXIT_INDEX,
        }
    }
}

/// Which enumeration test killed a candidate, `settle.Elimination`'s stage field. The six are the whole closed set `_candidates_uncached` records.
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
    /// The stage's spelling, as the trace carries it.
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

/// One candidate that did not survive enumeration, `settle.Elimination`. The description is a formatted sentence rather than a structure because it is read by people — explain output and the decision-rule TSVs — and its exact wording is contract against the Python original; the provenance is the authored record that did the killing, where one did.
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

/// Which stage of the lexicographic ranking decided the window, `settle.TransitionTrace.decided_stage`. `Boundary` is the short-circuit a non-letter input takes, and the stages after it are the pipeline in the order it runs.
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
    /// The stage one spelling names, or `None` for text that is not one of the seven — the reader half of [`DecidedStage::as_str`], for the memo file that carries a stage per entry.
    pub fn from_text(text: &str) -> Option<Self> {
        [
            Self::Boundary,
            Self::OnlyCandidate,
            Self::AbsolutePrefer,
            Self::JoinCount,
            Self::YieldingPrefer,
            Self::Order,
            Self::Floor,
        ]
        .into_iter()
        .find(|stage| stage.as_str() == text)
    }

    /// The stage's spelling, as the trace carries it.
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

/// How a window was decided, beyond what it decided — `settle.TransitionTrace`'s explain half: the ranking every survivor was scored into, every candidate that did not survive with the sentence naming why, and the closest loser. Nobody reads it to build a font; the explain CLI, the probe and the review surface's panel are its whole audience.
///
/// It is a type of its own, and boxed where a trace carries one, because the table fixpoint asks for millions of traces and reads none of this: a ladder formatted for a reader who will never arrive is the largest avoidable allocation in the enumeration.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TraceLadder {
    pub ranked: Vec<RankedCandidate>,
    pub eliminations: Vec<Elimination>,
    pub runner_up: Option<Candidate>,
}

/// The ladder a trace that carries none answers with, so that a reader may ask any trace for its ranking and get an honest empty one.
static NO_LADDER: TraceLadder = TraceLadder {
    ranked: Vec::new(),
    eliminations: Vec::new(),
    runner_up: None,
};

/// The rich settlement result, `settle.TransitionTrace`: what the window settled into plus everything the table build, the explain CLI and the review surface read about how it got there. Notes are formatted strings — YAML pointers and the two authored sentences the kernel writes — because nothing downstream keys on them.
///
/// The explain half hangs off [`TransitionTrace::ladder`] and is absent wherever the engine was built without [`crate::engine::EngineModes::explain_ladder`] — which is the table fixpoint and nothing else.
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
    /// How this window was decided, or the empty ladder where the engine was not asked to record one.
    pub fn ladder(&self) -> &TraceLadder {
        self.ladder.as_deref().unwrap_or(&NO_LADDER)
    }
}

/// The closed vocabulary interned against one spec's own string pool, so that every comparison the kernel makes against an authored value is a symbol comparison rather than a string one. [`SpecIndex`] builds exactly one of these per spec and hands it out; the symbols are only meaningful against that spec's interner.
#[derive(Clone, Debug)]
pub struct Vocab {
    pub edge: Sym,
    pub space: Sym,
    pub zwnj: Sym,
    pub namer_dot: Sym,
    pub letter: Sym,
    pub unknown: Sym,
    /// `boundary`, which is both the `is:` value that expands to the four boundary kinds and the stance name every boundary cell carries — one string, and therefore one symbol, in both roles.
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
    /// Intern the whole closed vocabulary through one minting closure. Interning rather than looking up is what guarantees every symbol exists: a spec that never mentions `live` still needs a symbol for it, and one that does gets the same symbol its own text resolves to.
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

    /// The state a live-or-not side is in, in the `live` / `none` vocabulary `self_entry:` and `self_exit:` are written against.
    pub fn liveness_state(&self, height: Option<Sym>) -> Sym {
        match height {
            Some(_) => self.live,
            None => self.none,
        }
    }

    /// The state a side is in, in the height-or-`none` vocabulary pairings, `cells:` rows and `joined_at:` are written against.
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

/// The settled record a boundary contributes, `settle.boundary_settled` — no seam and no extension, because a boundary offers neither.
pub fn boundary_settled(vocab: &Vocab, kind: TokenKind) -> Settled {
    Settled {
        cell: boundary_cell(vocab, kind),
        seam: None,
        extension: 0,
    }
}

/// Whether a settled record is a boundary's rather than a letter's, `settle.is_boundary_settled`. The stance is the marker, because no rune declares a stance by that name.
pub fn is_boundary_settled(vocab: &Vocab, settled: &Settled) -> bool {
    settled.cell.stance == vocab.boundary
}

/// One authored record's YAML pointer in the spelling `str(Provenance)` gives it — `file:path`. This is the string the fired set holds, the notes carry, and every raise message pastes into its sentence.
pub fn provenance_pointer(index: &SpecIndex, provenance: &Provenance) -> String {
    format!(
        "{}:{}",
        index.resolve(provenance.file),
        index.resolve(provenance.path)
    )
}

/// A deterministic textual form of a cell, `settle.cell_label`: the diff-stable name the TSV artifacts, the explain output and the E-STRANDED message all read. Deliberately shaped like geometry's compiled display name without being it — geometry's carries a 63-byte cap this one does not.
///
/// A boundary cell labels as the glyph its kind ships as, and a boundary whose kind has no glyph — the run edge — panics here exactly as the Python mapping raises `KeyError` for it: nothing labels an edge, and reaching this with one means a caller labeled a cell the fold never records.
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
        let pea = fixtures::sym(&index, "qsPea");
        assert_ne!(UNKNOWN, EDGE);
        assert_ne!(RightToken::Letter(tea), RightToken::Letter(pea));
        assert_eq!(RightToken::Letter(tea), RightToken::Letter(tea));
        assert_eq!(RightToken::Letter(tea).kind(), TokenKind::Letter);
        assert_eq!(RightToken::Letter(tea).letter(), tea);
        assert_eq!(EDGE.rune(), None);
        assert_eq!(RightToken::of_kind(TokenKind::Zwnj), Some(ZWNJ));
        assert_eq!(RightToken::of_kind(TokenKind::Letter), None);
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
        let stance = fixtures::sym(&index, "half");
        let seam = fixtures::sym(&index, "baseline");
        let joining = Candidate::joining(stance, None, seam, 0, 3);
        let non_joining = Candidate::non_joining(stance, None, 0);
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
