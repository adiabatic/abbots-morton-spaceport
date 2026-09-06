//! The interned model: `rebuild/pipeline/model.py`'s dataclass tree with every vocabulary string collapsed to a `Sym` and every number to an `i64`. Prose — `ductus` values, `notes`, `why`, `description` — stays an owned `String`, because nothing downstream keys on a paragraph; everything else rides as an integer, which is the whole reason the port exists (`doc/rebuild-design.md` §14.1: the packing is the win, not the language).
//!
//! Order is part of the model rather than an accident of how it was parsed. Stance declaration order ranks candidates, exit declaration order is the structural floor's final tiebreak, and a rune's policy lists gather in declaration order, so every mapping the dump preserves lands in a [`Table`] — an insertion-ordered association list — and never in a `HashMap`. The two resolved membership sets (`Policy.groups`, `ScriptRegistry.predicate_classes`) arrive already sorted and are stored and re-emitted in the order they arrived.
//!
//! Heights stay ordinary symbols at this stage. The registry's `heights` mapping is the authority on what each one means, and the deeper packing belongs to the sub-issues after this one; enum-ifying the vocabulary now would freeze something the dump is allowed to grow. The prior art's derived accelerators — feature masks, entry-bearing flags, letter bitmasks — are likewise absent on purpose: ingest is faithful and lossless and nothing more.

use std::collections::HashMap;
use std::num::NonZeroU32;

/// An interned vocabulary string, valid only against the [`Interner`] that minted it. Comparison, hashing, and later packing all happen on the integer, never on the text.
///
/// The integer is a `NonZeroU32` rather than a `u32` so that `Option<Sym>` is four bytes: zero is the niche the compiler folds `None` into, and a side that did not join — `CellId.entry`, `Settled.seam`, `Candidate.entry`, the engine's left-context fields — costs no discriminant beside the value (issue #164). The pool mints from one to keep zero free, and that offset is the interner's business alone: nothing outside it reads the integer, and a symbol's order is still its minting order, so anything sorted on symbols sorts as it did.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Sym(NonZeroU32);

/// The one string pool a parsed dump resolves through: a `Vec<String>` for `Sym` to text and a `HashMap` for text to `Sym`, both on the standard SipHash hasher. A finalizer-less fast hasher measured far slower than SipHash on this project's keys, whose low bits are a five-value alphabet, so the standard hasher is a measured choice rather than a default left in place.
#[derive(Clone, Debug, Default)]
pub struct Interner {
    strings: Vec<String>,
    ids: HashMap<String, Sym>,
}

impl Sym {
    /// The symbol for the pool's `seat`-th string. The seat's successor is the integer, which is what keeps zero free for the niche.
    fn at(seat: usize) -> Self {
        let raw = u32::try_from(seat)
            .ok()
            .and_then(|seat| seat.checked_add(1))
            .expect("a spec dump interns far fewer than four billion strings");
        Self(NonZeroU32::new(raw).expect("a seat's successor is never zero"))
    }

    /// The seat this symbol's text sits at in the pool that minted it.
    fn seat(self) -> usize {
        (self.0.get() - 1) as usize
    }
}

impl Interner {
    /// An empty pool.
    pub fn new() -> Self {
        Self::default()
    }

    /// The symbol for `value`, minting one on first sight. Interning the same text twice returns the same symbol.
    pub fn intern(&mut self, value: &str) -> Sym {
        if let Some(&known) = self.ids.get(value) {
            return known;
        }
        let minted = Sym::at(self.strings.len());
        self.strings.push(value.to_owned());
        self.ids.insert(value.to_owned(), minted);
        minted
    }

    /// The text `symbol` stands for. An out-of-range symbol panics; an in-range symbol minted by another interner resolves, silently, to whatever this pool holds at that seat — nothing detects the crossing, which is why a [`Spec`] carries its own `Interner` and the two only ever travel together.
    pub fn resolve(&self, symbol: Sym) -> &str {
        &self.strings[symbol.seat()]
    }

    /// Every symbol the pool has minted beside its text, in minting order — the one way to enumerate a pool, so that no caller has to know where the seats start.
    pub fn iter(&self) -> impl Iterator<Item = (Sym, &str)> {
        self.strings
            .iter()
            .enumerate()
            .map(|(seat, text)| (Sym::at(seat), text.as_str()))
    }

    /// How many distinct strings the pool holds.
    pub fn len(&self) -> usize {
        self.strings.len()
    }

    /// Whether the pool has interned anything yet.
    pub fn is_empty(&self) -> bool {
        self.strings.is_empty()
    }
}

/// An insertion-ordered mapping from interned key to value — the model's stand-in for every JSON object whose key order the dump preserves. Lookup is not offered because ingest never needs one and a linear scan would quietly become a hot loop later; the later sub-issues that need indexed access will build the index they need.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Table<T>(Vec<(Sym, T)>);

impl<T> Table<T> {
    /// An empty table.
    pub fn new() -> Self {
        Self(Vec::new())
    }

    /// Append one entry, keeping the order it arrived in.
    pub fn push(&mut self, key: Sym, value: T) {
        self.0.push((key, value));
    }

    /// The entries in their stored order.
    pub fn iter(&self) -> std::slice::Iter<'_, (Sym, T)> {
        self.0.iter()
    }

    /// How many entries the table holds.
    pub fn len(&self) -> usize {
        self.0.len()
    }

    /// Whether the table holds no entries.
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

impl<T> Default for Table<T> {
    fn default() -> Self {
        Self::new()
    }
}

impl<'a, T> IntoIterator for &'a Table<T> {
    type Item = &'a (Sym, T);
    type IntoIter = std::slice::Iter<'a, (Sym, T)>;

    fn into_iter(self) -> Self::IntoIter {
        self.iter()
    }
}

/// One parsed dump: the tree, plus the interner every `Sym` inside it resolves through. The two travel together because a symbol means nothing without its pool, and emission needs both.
#[derive(Clone, Debug)]
pub struct Spec {
    pub symbols: Interner,
    pub root: ResolvedSpec,
}

/// `spec_load`'s whole product: the modeled runes and the script registry.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResolvedSpec {
    pub runes: Table<Rune>,
    pub registry: ScriptRegistry,
}

/// One letter or ligature. A ligature declares `sequence` instead of a `codepoint`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Rune {
    pub name: Sym,
    pub codepoint: Option<i64>,
    pub sequence: Option<Vec<Sym>>,
    pub ductus: Table<String>,
    pub notes: Option<String>,
    pub mono: Option<Bitmap>,
    pub stances: Table<Stance>,
    pub policy: Policy,
}

/// A rune's riders, each list in declaration order because that is the order they gather in.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Policy {
    pub order: Vec<Sym>,
    pub refuse: Vec<PolicyRecord>,
    pub prefer: Vec<PolicyRecord>,
    pub extend: Vec<PolicyRecord>,
    pub contract: Vec<PolicyRecord>,
    pub resolve: Vec<PolicyRecord>,
    pub groups: Table<Vec<Sym>>,
}

/// One rider in the single grammatical shape all five kinds share.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PolicyRecord {
    pub kind: Sym,
    pub when: When,
    pub id: Option<Sym>,
    pub stance: Option<Sym>,
    pub entry: Option<Sym>,
    pub exit: Option<Sym>,
    pub cell: Option<Table<Sym>>,
    pub over: Option<Table<Sym>>,
    pub mode: Option<Sym>,
    pub by: Option<i64>,
    pub ok: Option<(i64, i64)>,
    pub bind: Option<Sym>,
    pub trim: Option<i64>,
    pub split: Option<(i64, i64)>,
    pub against: Option<(Sym, Option<Sym>)>,
    pub pick: Option<Table<Sym>>,
    pub migrated: Option<Sym>,
    pub why: Option<String>,
    pub provenance: Option<Provenance>,
}

/// The gate a rider or an unlock is read under.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct When {
    pub left: Option<Condition>,
    pub right: Option<Condition>,
    pub self_entry: Option<Sym>,
    pub self_exit: Option<Sym>,
    pub word: Option<Sym>,
    pub feature: Option<Sym>,
}

/// One side of a `when:`. An empty axis is unconstrained, `except_` carves members back out, and `then` is the right-only static hop.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Condition {
    pub family: Vec<Sym>,
    pub klass: Vec<Sym>,
    pub stance: Vec<Sym>,
    pub joined_at: Option<Sym>,
    pub stroke: Option<Sym>,
    pub is_token: Option<Sym>,
    pub except_: Vec<Condition>,
    pub then: Option<Box<Condition>>,
}

/// One drawing of a rune, with the join surface it offers.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Stance {
    pub name: Sym,
    pub motion: Sym,
    pub traits: Vec<Sym>,
    pub bitmap: Bitmap,
    pub bitmaps: Table<Bitmap>,
    pub surface: Surface,
}

/// Ink rows top to bottom, with the glyph-space y of the bottom row. Rows are interned because the alphabet repeats a great many of them verbatim.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Bitmap {
    pub rows: Vec<Sym>,
    pub y_offset: i64,
}

/// What a stance offers each side, plus the bindings, pairings, and unlocks that qualify it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Surface {
    pub entries: Table<SurfaceRow>,
    pub exits: Table<SurfaceRow>,
    pub pairings: Pairings,
    pub cells: Vec<CellBinding>,
    pub unlocks: Vec<Unlock>,
    pub require: Vec<Sym>,
}

/// One entry or exit row: a height, an anchor x, and the optional bindings, scopes, and oddities.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SurfaceRow {
    pub height: Sym,
    pub x: i64,
    pub stroke: Option<Sym>,
    pub joined: Option<Sym>,
    pub joined_x: Option<i64>,
    pub withdrawal: Option<Sym>,
    pub stub: Option<Stub>,
    pub scope: Vec<Condition>,
    pub selectable: bool,
    pub ink_y: Option<i64>,
    pub x_off_convention: bool,
    pub provenance: Option<Provenance>,
}

/// Same-row attachment ink at a side's anchor row, inked in the liveness state `inks_when` names.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Stub {
    pub cols: Vec<i64>,
    pub inks_when: Sym,
}

/// One entry-state and exit-state combination, named by a pairing rule.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Pairing {
    pub entry: Sym,
    pub exit: Sym,
}

/// A stance's pairing rules: the forbidden combinations, and optionally the closed list of admitted ones.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Pairings {
    pub never: Vec<Pairing>,
    pub only: Option<Vec<Pairing>>,
}

/// An explicit `cells:` row — the named composition to use when both side bindings touch one cell.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CellBinding {
    pub entry: Sym,
    pub exit: Sym,
    pub bitmap: Sym,
    pub entry_x: Option<i64>,
    pub exit_x: Option<i64>,
    pub provenance: Option<Provenance>,
}

/// A capability a stylistic set turns on for this stance.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Unlock {
    pub feature: Sym,
    pub entry: Option<Sym>,
    pub exit: Option<Sym>,
    pub pairing: Option<Pairing>,
    pub when: Option<When>,
    pub why: Option<String>,
    pub provenance: Option<Provenance>,
}

/// Where an authored fact came from, spelled in the dump as the `[file, path]` pair — the tree's one hand-spelled leaf.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Provenance {
    pub file: Sym,
    pub path: Sym,
}

/// The script-wide vocabulary the runes are read against.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ScriptRegistry {
    pub heights: Table<i64>,
    pub boundary_tokens: Table<BoundaryToken>,
    pub features: Table<FeatureInfo>,
    pub interactions: Vec<Vec<Sym>>,
    pub predicate_classes: Table<Vec<Sym>>,
    pub families: Table<FamilyInfo>,
}

/// A non-letter input the sweep can meet, and whether it splits a run.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BoundaryToken {
    pub codepoint: i64,
    pub splits_runs: bool,
}

/// One stylistic set: whether it is a capability or a taste, its prose, and the overlay it draws through.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FeatureInfo {
    pub kind: Sym,
    pub description: String,
    pub overlay: Option<Sym>,
}

/// A family the registry knows about, whether or not it is modeled as a rune.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FamilyInfo {
    pub codepoint: Option<i64>,
    pub sequence: Option<Vec<Sym>>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_string_interns_to_one_symbol() {
        let mut symbols = Interner::new();
        let first = symbols.intern("x-height");
        let again = symbols.intern("x-height");
        assert_eq!(first, again);
        assert_eq!(symbols.len(), 1);
    }

    #[test]
    fn distinct_strings_intern_to_distinct_symbols() {
        let mut symbols = Interner::new();
        let height = symbols.intern("x-height");
        let baseline = symbols.intern("baseline");
        assert_ne!(height, baseline);
        assert_eq!(symbols.len(), 2);
    }

    #[test]
    fn resolving_a_symbol_returns_the_string_it_was_minted_from() {
        let mut symbols = Interner::new();
        let empty = symbols.intern("");
        let dot = symbols.intern("\u{b7}Zoo");
        assert_eq!(symbols.resolve(empty), "");
        assert_eq!(symbols.resolve(dot), "\u{b7}Zoo");
        assert!(!symbols.is_empty());
    }

    #[test]
    fn an_absent_symbol_costs_no_more_than_a_present_one() {
        assert_eq!(std::mem::size_of::<Sym>(), 4);
        assert_eq!(std::mem::size_of::<Option<Sym>>(), 4);
    }

    #[test]
    fn symbols_order_and_enumerate_in_minting_order() {
        let mut symbols = Interner::new();
        let minted: Vec<Sym> = ["top", "x-height", "baseline"]
            .into_iter()
            .map(|name| symbols.intern(name))
            .collect();
        assert!(minted[0] < minted[1] && minted[1] < minted[2]);
        let walked: Vec<(Sym, &str)> = symbols.iter().collect();
        assert_eq!(
            walked,
            [
                (minted[0], "top"),
                (minted[1], "x-height"),
                (minted[2], "baseline")
            ]
        );
    }

    #[test]
    fn a_table_keeps_the_order_it_was_filled_in() {
        let mut symbols = Interner::new();
        let mut table = Table::new();
        for name in ["x-height", "baseline", "top"] {
            table.push(symbols.intern(name), name.len());
        }
        let order: Vec<&str> = table.iter().map(|(key, _)| symbols.resolve(*key)).collect();
        assert_eq!(order, ["x-height", "baseline", "top"]);
        assert_eq!(table.len(), 3);
        assert!(Table::<usize>::new().is_empty());
    }
}
