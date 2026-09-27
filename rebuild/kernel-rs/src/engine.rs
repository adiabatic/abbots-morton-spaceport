//! The settlement engine: condition matching, the capability reads that decide what a stance can offer, refusals, candidate enumeration, the refusal-aware lookahead closure, and the lexicographic ranking (absolute prefers, the window join count, yielding prefers, the runes' declared order, then the final tiebreak), followed by the adjustments and the commit that turn the winner into a settled cell. This crate is the only implementation of settlement.
//!
//! Each ranking stage narrows the survivor list the next stage reads, so the order of the stages determines the result, and `decided_stage` names the stage that narrowed the list to one. The two prefer stages can raise instead of choosing: prefer records that demand different outcomes at non-nested specificity raise E-AMBIGUOUS within one rune and E-INCOMPARABLE across two. The E-INCOMPARABLE message ends in a paste-ready `resolve:` stub that the author copies into the rune's YAML, so its wording must stay as it is.
//!
//! An engine is one (spec, feature configuration) pair. It borrows the spec as a [`SpecIndex`], so the guard's engines for every feature combination and every replay share one index and one string pool. Everything the engine holds is cache, because the table build's fixpoint asks the same questions about the same windows many times. An engine can also read another engine's finished trace memo: the memo leaves as a [`crate::memo::MemoSnapshot`] and is passed to another engine as a shared memo, behind an exclusion naming the runes and classes the two engines do not settle alike ([`Engine::attach_shared_memos`]). That is how a configuration enumerates as a delta over `default`. Per-stance caches are ordinary maps with no size cap, because a [`StanceId`] is a stable index pair that is never reused.
//!
//! The fired-provenance journal must be exact. `fired` is the set of authored records that fired under this configuration, and the dead-policy check reads it, so a memo hit must not lose the firings its first evaluation performed. Every cache entry stores the delta its computation journaled, and every hit replays that delta into whatever capture is open. A hit adds the delta to the set itself only when the set may not hold it yet, which is a shared memo entry's delta on its first hit: an own entry's pointers entered the set when the entry was recorded. This makes each entry's delta independent of evaluation order and a warm engine's `fired` equal to a cold one's. The journal runs only in trace-memo mode, the only mode that asks for a per-evaluation delta. Outside it there is no journal, and [`Engine::candidates`] skips its cache, since an entry could not carry a delta to replay.
//!
//! A [`Pointer`] is a provenance's `file:path` pair, kept as two symbols so it is `Copy` and hashes on two integers. The string is built only where one is written out.
//!
//! Condition matching raises three spec defects: a left condition carrying `then:`, a right condition carrying a left-only axis, and an unresolvable class name (raised by [`SpecIndex::class_members`]). Enumeration reports every other rejection as an elimination: an unavailable entry, a forbidden pairing, an exit the closure rules out, and a refusal. A window with no candidates at all is reported by [`Engine::transition_trace`].

use std::num::NonZeroU16;

use crate::error::SettleError;
use crate::hash::{HashMap, HashSet};
use crate::index::{Ordinal, Read, SpecIndex, StanceId};
use crate::memo::{MemoSnapshot, SharedMemo};
use crate::model::{
    Condition, PolicyRecord, Provenance, Rune, Stance, SurfaceRow, Sym, Table, When,
};
use crate::specificity;
use crate::types::{
    AdjustmentToken, Candidate, CandidateOrdinals, CellId, DecidedStage, Elimination,
    EliminationStage, LeftContext, NotesId, NotesPool, PackedKinds, RankedCandidate, RightToken,
    Settled, SettledId, SettledPool, Side, TokenKind, TraceRanking, TransitionTrace, UNKNOWN,
    Vocab, boundary_settled, cell_label, provenance_pointer, word_position,
};

/// Where a candidate enumeration's eliminations go, and whether their descriptions are formatted. The table fixpoint needs each elimination's stage and pointer, because a row's notes are built from them, but not the text.
struct EliminationSink<'a> {
    list: Option<&'a mut Vec<Elimination>>,
    describe: bool,
}

/// One authored record's YAML pointer: `model.Provenance`'s file and path, kept apart so the value is `Copy` and hashes on two integers. [`Pointer::text`] builds the `file:path` string the corpus and the notes carry.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Pointer {
    pub file: Sym,
    pub path: Sym,
}

impl Pointer {
    /// The pointer one authored record's provenance names.
    pub fn of(provenance: &Provenance) -> Self {
        Self {
            file: provenance.file,
            path: provenance.path,
        }
    }

    /// The `file:path` string, the only form that reaches an output.
    pub fn text(self, index: &SpecIndex) -> String {
        provenance_pointer(
            index,
            &Provenance {
                file: self.file,
                path: self.path,
            },
        )
    }
}

/// One collection's length and capacity, as the `--cache-stats` diagnostic reports it. Capacity beside length shows whether a table is large or mostly empty slack, which decides whether shrinking it would save memory.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CacheSize {
    pub name: &'static str,
    pub len: usize,
    pub capacity: usize,
}

impl CacheSize {
    /// One named collection's pair.
    pub fn of(name: &'static str, len: usize, capacity: usize) -> Self {
        Self {
            name,
            len,
            capacity,
        }
    }

    /// The line the cache stats write for it.
    pub fn line(&self, config: &str) -> String {
        format!(
            "[c] {config} {} len={} cap={}",
            self.name, self.len, self.capacity
        )
    }
}

/// The four raw lookahead slots one window is read against. A caller that knows only two slots uses [`Slots::pair`], which sets the other two to `UNKNOWN` explicitly.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Slots {
    pub right1: RightToken,
    pub right2: RightToken,
    pub right3: RightToken,
    pub right4: RightToken,
}

impl Slots {
    /// All four slots given explicitly.
    pub fn new(
        right1: RightToken,
        right2: RightToken,
        right3: RightToken,
        right4: RightToken,
    ) -> Self {
        Self {
            right1,
            right2,
            right3,
            right4,
        }
    }

    /// The two-slot window every capability and refusal read is evaluated against, with the deeper two slots `UNKNOWN`.
    pub fn pair(right1: RightToken, right2: RightToken) -> Self {
        Self::new(right1, right2, UNKNOWN, UNKNOWN)
    }

    /// The slots as the token run a right condition walks, one raw token per `then:` hop.
    pub fn as_array(self) -> [RightToken; 4] {
        [self.right1, self.right2, self.right3, self.right4]
    }
}

/// The tail [`Engine::cond_matches_right`] walks once a `then:` chain runs past the supplied slots.
const UNKNOWN_TAIL: [RightToken; 1] = [UNKNOWN];

/// The modes an engine is built with. The crate reads no environment, so the caller passes them. [`Default`] gives the default modes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct EngineModes {
    /// The follower prefer's slots past `right1` when `follower_prefer_slots` is off. `UNKNOWN` is the optimistic comparison state. The section 5.7 guard's engines set it to `EDGE`, so a follower prefer that needs deeper text than the guard's verdict is keyed on can never change a formation verdict.
    pub follower_prefer_deep_slot: RightToken,
    /// Whether the third join-count term is the follower's simulated transition (the default) or the optimistic candidacy estimate.
    pub simulated_prospect: bool,
    /// Whether a follower prefer reads the window's slots shifted by one, or reads `follower_prefer_deep_slot` for everything past its own `right1`.
    pub follower_prefer_slots: bool,
    /// Whether the engine memoizes whole windows and journals a fired delta per memoized evaluation. On only in the table fixpoint, the `settle-cases` and `liveness-cases` subcommands, and the string replay.
    pub trace_memo: bool,
    /// Whether a trace carries its ranking: every ranked survivor with its scores, the eliminations with their descriptions, and the runner-up. On wherever a person reads a trace (the explain report, the review corpus, the probe). Off in the table fixpoint, whose rows read only the settled triple, the prospect, the joint tiebreak and the notes, and in the string replay, which reads only the settled record. Formatting rankings nobody reads is the largest avoidable allocation in either.
    pub explain_ranking: bool,
}

impl Default for EngineModes {
    fn default() -> Self {
        Self {
            follower_prefer_deep_slot: UNKNOWN,
            simulated_prospect: true,
            follower_prefer_slots: true,
            trace_memo: false,
            explain_ranking: true,
        }
    }
}

/// One exit a stance can offer: a declared row at its declaration index, or a row-less height an active unlock grants, at an index past the declared ones. The `Unlock` behind such a height is not stored because no caller reads it. Its only effect is the provenance the enumeration fires, which the cache replays.
#[derive(Clone, Copy, Debug)]
struct ExitSource<'i> {
    height: Sym,
    row: Option<&'i SurfaceRow>,
    index: usize,
}

/// One stance's pairing rules as sets of (entry, exit) pairs, cached per [`StanceId`] with no size cap.
#[derive(Clone, Debug)]
struct PairingSets {
    never: HashSet<(Sym, Sym)>,
    only: Option<HashSet<(Sym, Sym)>>,
}

/// The index of one distinct candidate list in the candidate memo's list pool, stored in a memo entry in place of the list. An instrumented run (issue #167) measured under half a million entries per configuration sharing a few dozen distinct lists, so four bytes per entry replace a vector that thousands of other entries repeat. [`CandidateListId::at`] and [`CandidateListId::index`] are the only conversions.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct CandidateListId(u32);

impl CandidateListId {
    /// The id for the pool's `index`-th list.
    fn at(index: usize) -> Self {
        Self(
            u32::try_from(index)
                .expect("a configuration enumerates fewer than 2^32 distinct candidate lists"),
        )
    }

    /// The id as the pool's index.
    fn index(self) -> usize {
        self.0 as usize
    }
}

/// The table the candidate memo stores its candidate lists in, shaped like [`NotesPool`]: each distinct list once, in first-seen order, with its id. A hit clones the list out of the table.
#[derive(Clone, Debug, Default)]
struct CandidateListPool {
    ids: HashMap<Vec<Candidate>, CandidateListId>,
    table: Vec<Vec<Candidate>>,
}

impl CandidateListPool {
    /// This list's id, minted the first time the list is seen. The list is passed owned: a miss keeps the allocation and a hit drops it.
    fn intern(&mut self, candidates: Vec<Candidate>) -> CandidateListId {
        if let Some(&id) = self.ids.get(candidates.as_slice()) {
            return id;
        }
        let id = CandidateListId::at(self.table.len());
        self.ids.insert(candidates.clone(), id);
        self.table.push(candidates);
        id
    }

    /// The list one id names.
    fn get(&self, id: CandidateListId) -> &[Candidate] {
        &self.table[id.index()]
    }

    /// How many distinct lists have been interned.
    fn len(&self) -> usize {
        self.table.len()
    }

    /// The table's capacity, which the cache stats report beside the length.
    fn capacity(&self) -> usize {
        self.table.capacity()
    }
}

/// The index of one distinct elimination list in the candidate memo's elimination pool, stored in a memo entry in place of the list. The pool is keyed on the whole list (each elimination's stage, description and provenance), so explain mode, where descriptions are filled in, stays exact. In fixpoint mode the descriptions are empty, so the lists repeat across entries: issue #167's instrumented run measured a few dozen per configuration.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct EliminationListId(u32);

impl EliminationListId {
    /// The id for the pool's `index`-th list.
    fn at(index: usize) -> Self {
        Self(
            u32::try_from(index)
                .expect("a configuration enumerates fewer than 2^32 distinct elimination lists"),
        )
    }

    /// The id as the pool's index.
    fn index(self) -> usize {
        self.0 as usize
    }
}

/// The table the candidate memo stores its elimination lists in, shaped like [`CandidateListPool`].
#[derive(Clone, Debug, Default)]
struct EliminationListPool {
    ids: HashMap<Vec<Elimination>, EliminationListId>,
    table: Vec<Vec<Elimination>>,
}

impl EliminationListPool {
    /// This list's id, minted the first time the list is seen. The list is passed owned: a miss keeps the allocation and a hit drops it without copying a description.
    fn intern(&mut self, eliminations: Vec<Elimination>) -> EliminationListId {
        if let Some(&id) = self.ids.get(eliminations.as_slice()) {
            return id;
        }
        let id = EliminationListId::at(self.table.len());
        self.ids.insert(eliminations.clone(), id);
        self.table.push(eliminations);
        id
    }

    /// The list one id names.
    fn get(&self, id: EliminationListId) -> &[Elimination] {
        &self.table[id.index()]
    }

    /// Every distinct list once, in id order. [`Engine::elimination_text_bytes`] measures these, since a list is held once however many entries name it.
    fn lists(&self) -> &[Vec<Elimination>] {
        &self.table
    }

    /// How many distinct lists have been interned.
    fn len(&self) -> usize {
        self.table.len()
    }

    /// The table's capacity, which the cache stats report beside the length.
    fn capacity(&self) -> usize {
        self.table.capacity()
    }
}

/// What the candidate memo holds per window: ids for the candidate list, the elimination list, the fired delta and the read set, sixteen bytes with no heap. An instrumented run of an earlier layout, which kept the lists and the delta on the heap per entry, measured fewer than half a million entries per configuration sharing a few dozen distinct candidate lists, a few dozen distinct elimination lists and a few thousand distinct deltas (issue #167). The delta lets a hit replay the records the enumeration fired. Without it, a window that hits this key would leave those records looking dead.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct CandidatesEntry {
    candidates: CandidateListId,
    eliminations: EliminationListId,
    delta: DeltaId,
    reads: ReadsId,
}

/// The candidate memo and its two list pools, released together because an id means nothing without the table it indexes. The delta id resolves through [`Engine::deltas`], which every memo shares, so each distinct delta is held once.
#[derive(Clone, Debug, Default)]
struct CandidatesMemo {
    entries: HashMap<CandidatesKey, CandidatesEntry>,
    candidates: CandidateListPool,
    eliminations: EliminationListPool,
}

/// The candidate memo's key. The left is reduced to its kind and the settled cell's rune, stance and junction. The left's entry, adjustments and extension are left out because enumeration reads none of them, so two lefts differing only there share one entry. The trace memo's key keeps the extension, because the commit's same-junction suppression reads it.
///
/// It is packed to fourteen bytes like [`TraceKey`]: each rune, stance and junction is its field's [`Ordinal`], each slot is its rune's ordinal beside its kind, and the left's kind and the two slot kinds share one [`PackedKinds`] word. Each token has exactly one encoding, so two windows share a key exactly when their slots are equal. A key is only compared and hashed, never resolved.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct CandidatesKey {
    left_rune: Option<Ordinal>,
    left_stance: Option<Ordinal>,
    left_junction: Option<Ordinal>,
    rune: Ordinal,
    runes: [Option<Ordinal>; 2],
    /// The left's kind at slot zero, then the two slots' kinds.
    kinds: PackedKinds,
}

/// The lookahead closure's key: the proposed candidate, the follower, and the raw slot past it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct ClosureKey {
    rune: Sym,
    stance: Sym,
    entry: Option<Sym>,
    junction: Option<Sym>,
    right1: Sym,
    right2: RightToken,
}

/// The trace memo's key: the reduced left with its extension, the input rune, and all four raw slots. The kernel reads the left only through these fields, so lefts differing only in their cell's entry or adjustments share one entry.
///
/// It is packed to twenty bytes because the memo holds one per window, over a million windows per configuration, as measured in issues #165 and #266. Each rune, stance and junction field is its field's [`Ordinal`]: two bytes naming one of the few symbols the spec offers for that position, where a `Sym` into the whole pool takes four. Each slot is its rune's ordinal beside its kind, which is what a [`RightToken`] is: a letter is its kind with a rune, and every other kind has no rune, so each token has one encoding and two windows share a key exactly when their slots are equal. The left's kind and the four slot kinds share one [`PackedKinds`] word, and the extension is an `i16` count of connector pixels. Eight ordinals, the word and the count sit at alignment two with no padding. A key is compared, hashed and sorted, and is read back only by the memo writer, which resolves each ordinal through the index that minted it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub(crate) struct TraceKey {
    pub(crate) left_rune: Option<Ordinal>,
    pub(crate) left_stance: Option<Ordinal>,
    pub(crate) left_junction: Option<Ordinal>,
    pub(crate) left_extension: i16,
    pub(crate) token: Ordinal,
    pub(crate) runes: [Option<Ordinal>; 4],
    /// The left's kind at slot zero, then the four raw slots' kinds.
    pub(crate) kinds: PackedKinds,
}

impl TraceKey {
    /// Every rune this key names, as rune-field ordinals: the left cell's, the input's, and each letter slot's. [`crate::memo::Exclusion::names`] tests these, and the exclusion also tests the entry's read set.
    pub(crate) fn runes_named(&self) -> impl Iterator<Item = Ordinal> + '_ {
        self.left_rune
            .into_iter()
            .chain(std::iter::once(self.token))
            .chain(self.runes.iter().flatten().copied())
    }

    /// The left's kind.
    pub(crate) fn left_kind(&self) -> TokenKind {
        self.kinds.get(0)
    }

    /// The kind of raw slot `slot`, zero through three.
    pub(crate) fn slot_kind(&self, slot: usize) -> TokenKind {
        self.kinds.get(slot + 1)
    }

    /// A key over a boundary left and the given slots, for a test that wants one without an engine.
    #[cfg(test)]
    pub(crate) fn for_test(index: &SpecIndex, token: Sym, runes: [Option<Sym>; 4]) -> Self {
        let ordinal = |rune: Sym| {
            index
                .rune_ordinal(rune)
                .expect("a test key names modeled runes")
        };
        let kinds = runes.map(|rune| rune.map_or(TokenKind::Edge, |_| TokenKind::Letter));
        Self {
            left_rune: None,
            left_stance: None,
            left_junction: None,
            left_extension: 0,
            token: ordinal(token),
            runes: runes.map(|rune| rune.map(ordinal)),
            kinds: PackedKinds::of(&[TokenKind::Edge, kinds[0], kinds[1], kinds[2], kinds[3]]),
        }
    }
}

/// The prospect memo's key, with one shape per prospect mode. An engine's mode is fixed at construction, so one engine only ever uses one shape. The candidacy key ends at `right2`'s rune because the estimate reads nothing past the follower's right. The simulated key carries the three slots from `right2` on because the follower's replayed settlement reads them.
///
/// Each field is its [`Ordinal`], as in [`TraceKey`] (issues #166 and #266), and the simulated key's three slots are their runes' ordinals beside one [`PackedKinds`] word of their kinds, so two asks share a key exactly when their slots are equal. The larger variant is eight ordinals and the word, eighteen bytes. The enum discriminant is stored in the unused zero value of a bare `Ordinal` field. An `Option<Ordinal>` field cannot hold it, because its `None` already uses that zero. A key is only compared and hashed, never resolved.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum ProspectKey {
    Candidacy {
        rune: Ordinal,
        stance: Ordinal,
        entry: Option<Ordinal>,
        junction: Option<Ordinal>,
        right1: Ordinal,
        right2: Ordinal,
    },
    Simulated {
        rune: Ordinal,
        stance: Ordinal,
        entry: Option<Ordinal>,
        junction: Option<Ordinal>,
        right1: Ordinal,
        runes: [Option<Ordinal>; 3],
        kinds: PackedKinds,
    },
}

/// How one prospect ask was computed, which [`Engine::prospect`] reads to decide whether to store an entry (issue #166). A term read from the follower's simulated trace left that trace in the trace memo, when there is one, and the next ask reads it there. A term from the candidacy estimate, because the mode asks for it or because the follower's replayed settlement raised and fell back, left nothing a later ask could read, so the prospect memo keeps it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ProspectTerm {
    Simulated,
    Estimated,
}

/// Which of a rune's two adjustment lists an adjustment is picked from.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum AdjustmentKind {
    Extend,
    Contract,
}

/// One policy record with the rune that owns it. The prefer stage gathers records from both junction runes this way and passes the two colliding ones to the resolution and the error message.
#[derive(Clone, Copy, Debug)]
struct OwnedRecord<'i> {
    owner: Sym,
    record: &'i PolicyRecord,
}

/// One gathered prefer record that applies to this window: it is relevant, it favors something, and what it favors is a strict subset of the survivor list. `favored` is a set because the narrowing only tests membership.
struct Applicable<'i> {
    owner: Sym,
    record: &'i PolicyRecord,
    favored: HashSet<Candidate>,
}

/// The index of one distinct fired delta in the engine's delta pool, stored in a memoized window, enumeration, prospect or closure entry in place of the delta. A configuration's memoized windows far outnumber the distinct deltas they journal (issue #165; `default`'s memo file lists both), so four bytes per entry replace a boxed slice that many other entries repeat. [`DeltaId::at`] and [`DeltaId::index`] are the only conversions.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) struct DeltaId(u32);

impl DeltaId {
    /// The id for the pool's `index`-th delta.
    pub(crate) fn at(index: usize) -> Self {
        Self(
            u32::try_from(index)
                .expect("a configuration journals fewer than 2^32 distinct fired deltas"),
        )
    }

    /// The id as the pool's index.
    pub(crate) fn index(self) -> usize {
        self.0 as usize
    }
}

/// The table every memoized fired delta is stored in, shaped like [`NotesPool`]: each distinct delta once, in first-journaled order, with its id. A replay reads the delta from the table.
#[derive(Clone, Debug, Default)]
struct DeltaPool {
    ids: HashMap<Box<[Pointer]>, DeltaId>,
    table: Vec<Box<[Pointer]>>,
}

impl DeltaPool {
    /// This delta's id, minted the first time the delta is journaled. The delta is passed owned because its capture is closed: a miss keeps the allocation and a hit drops it.
    fn intern(&mut self, delta: Box<[Pointer]>) -> DeltaId {
        if let Some(&id) = self.ids.get(&*delta) {
            return id;
        }
        let id = DeltaId::at(self.table.len());
        self.ids.insert(delta.clone(), id);
        self.table.push(delta);
        id
    }

    /// The delta one id names.
    fn get(&self, id: DeltaId) -> &[Pointer] {
        &self.table[id.index()]
    }

    /// How many distinct deltas have been interned.
    fn len(&self) -> usize {
        self.table.len()
    }

    /// The table's capacity, which the cache stats report beside the length.
    fn capacity(&self) -> usize {
        self.table.capacity()
    }
}

/// The index of one distinct read set in the engine's reads pool, stored in every memoized evaluation in place of the runes and classes it read (issue #184). A configuration's memoized evaluations share a few tens of thousands of distinct sets, so each set is stored once, like a delta. [`ReadsId::at`] and [`ReadsId::index`] are the only conversions.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) struct ReadsId(u32);

impl ReadsId {
    pub(crate) fn at(index: usize) -> Self {
        Self(
            u32::try_from(index)
                .expect("a configuration journals fewer than 2^32 distinct read sets"),
        )
    }

    pub(crate) fn index(self) -> usize {
        self.0 as usize
    }
}

/// The table every memoized read set is stored in, shaped like [`DeltaPool`].
#[derive(Clone, Debug, Default)]
struct ReadsPool {
    ids: HashMap<Box<[Read]>, ReadsId>,
    table: Vec<Box<[Read]>>,
}

impl ReadsPool {
    fn intern(&mut self, reads: Box<[Read]>) -> ReadsId {
        if let Some(&id) = self.ids.get(&*reads) {
            return id;
        }
        let id = ReadsId::at(self.table.len());
        self.ids.insert(reads.clone(), id);
        self.table.push(reads);
        id
    }

    fn get(&self, id: ReadsId) -> &[Read] {
        &self.table[id.index()]
    }

    fn len(&self) -> usize {
        self.table.len()
    }

    fn capacity(&self) -> usize {
        self.table.capacity()
    }
}

/// What one closed capture returns: the pointers the evaluation fired, in first-fired order, and the set of runes and classes it read.
struct Captured {
    delta: Box<[Pointer]>,
    reads: Box<[Read]>,
}

/// A [`SettledId`] narrowed to two bytes for a [`TraceEntry`] (issue #266), which panics past its range instead of wrapping. It is a separate type because the shared id must stay four bytes wide: its `NonZeroU32` niche keeps a fixpoint product row's `Option<SettledId>` at four bytes ([`crate::fixpoint`]), and a product holds every record a whole fixpoint reaches. This memo holds only what one engine traced, and `default`'s memo file names a few hundred distinct settled records. The integer is the index plus one, so the range is [`TraceSettledId::CAPACITY`] records. [`TraceSettledId::at`] and [`TraceSettledId::index`] are the conversions, and [`TraceSettledId::widen`] returns the pool's own id.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) struct TraceSettledId(NonZeroU16);

impl TraceSettledId {
    /// How many records a trace memo can index: every index whose successor fits in a `u16`.
    pub(crate) const CAPACITY: usize = u16::MAX as usize;

    /// The id for the pool's `index`-th record, or `None` past the range. The memo reader uses this and rejects a file past the range instead of panicking.
    pub(crate) fn try_at(index: usize) -> Option<Self> {
        let raw = u16::try_from(index.checked_add(1)?).ok()?;
        NonZeroU16::new(raw).map(Self)
    }

    /// The id for the pool's `index`-th record. Panics past the range instead of wrapping.
    pub(crate) fn at(index: usize) -> Self {
        Self::try_at(index).expect("a trace memo holds fewer than 65,536 distinct settled records")
    }

    /// The id as the pool's index.
    pub(crate) fn index(self) -> usize {
        usize::from(self.0.get()) - 1
    }

    /// The pool's own id for the record this one names.
    pub(crate) fn widen(self) -> SettledId {
        SettledId::at(self.index())
    }
}

/// A [`NotesId`] narrowed to two bytes for a [`TraceEntry`], like [`TraceSettledId`], with the same conversions and the same panic past [`TraceNotesId::CAPACITY`] lists.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) struct TraceNotesId(NonZeroU16);

impl TraceNotesId {
    /// How many lists a trace memo can index: every index whose successor fits in a `u16`.
    pub(crate) const CAPACITY: usize = u16::MAX as usize;

    /// The id for the pool's `index`-th list, or `None` past the range.
    pub(crate) fn try_at(index: usize) -> Option<Self> {
        let raw = u16::try_from(index.checked_add(1)?).ok()?;
        NonZeroU16::new(raw).map(Self)
    }

    /// The id for the pool's `index`-th list. Panics past the range instead of wrapping.
    pub(crate) fn at(index: usize) -> Self {
        Self::try_at(index).expect("a trace memo holds fewer than 65,536 distinct notes lists")
    }

    /// The id as the pool's index.
    pub(crate) fn index(self) -> usize {
        usize::from(self.0.get()) - 1
    }

    /// The pool's own id for the list this one names.
    pub(crate) fn widen(self) -> NotesId {
        NotesId::at(self.index())
    }
}

/// What the trace memo holds per window: two-byte indexes into the memo's settled and notes pools, four-byte indexes into [`Engine::deltas`] and the engine's reads pool, and one byte packing the prospect, the joint flag and the stage. That is sixteen bytes at four-byte alignment, with no heap. Packing the three fields into a byte saves nothing alone, because the alignment pads it. The two-byte indexes are what take the entry from twenty bytes to sixteen. An instrumented run of an earlier layout (issue #165), which held the whole [`TransitionTrace`] and a boxed delta per entry, measured over a million entries per configuration naming a couple of hundred distinct settled records, about a hundred distinct notes lists and a few tens of thousands of distinct deltas. The ranking is stored separately in [`TraceMemo::rankings`], because only an engine built with [`EngineModes::explain_ranking`] has one, and neither the fixpoint nor the string replay is.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct TraceEntry {
    pub(crate) settled: TraceSettledId,
    pub(crate) notes: TraceNotesId,
    pub(crate) delta: DeltaId,
    pub(crate) reads: ReadsId,
    /// The prospect bit at the bottom (the term is a junction count of zero or one), the joint flag above it, and the stage's ordinal, at most six, from bit two up. [`TraceEntry::prospect`], [`TraceEntry::joint_tiebreak`] and [`TraceEntry::decided_stage`] read them back.
    packed: u8,
}

impl TraceEntry {
    /// One entry over its four ids and the three packed fields. The prospect is passed as the `i64` the ranking sums, and a value outside zero or one panics.
    pub(crate) fn new(
        settled: TraceSettledId,
        notes: TraceNotesId,
        delta: DeltaId,
        reads: ReadsId,
        prospect: i64,
        joint_tiebreak: bool,
        decided_stage: DecidedStage,
    ) -> Self {
        let prospect = u8::try_from(prospect)
            .ok()
            .filter(|term| *term <= 1)
            .expect("a prospect is a junction count, zero or one");
        Self {
            settled,
            notes,
            delta,
            reads,
            packed: prospect | (u8::from(joint_tiebreak) << 1) | (decided_stage.ordinal() << 2),
        }
    }

    /// The prospect the ranking summed for this window, zero or one.
    pub(crate) fn prospect(self) -> i8 {
        (self.packed & 1) as i8
    }

    /// Whether the final tiebreak decided between a joining and a non-joining candidate.
    pub(crate) fn joint_tiebreak(self) -> bool {
        self.packed & 2 != 0
    }

    /// The stage that decided the window.
    pub(crate) fn decided_stage(self) -> DecidedStage {
        DecidedStage::from_ordinal(self.packed >> 2)
            .expect("a packed entry holds one of the seven stages")
    }
}

/// The window memo and its fired journal, with the settled and notes pools its entries index. The delta is an id in the entry, not a second map on the same twenty-byte key, which would cost the key and its hash-table slack again for a value only read with its trace. It resolves through [`Engine::deltas`] because the candidate, closure and prospect memos store their deltas in the same table (issue #167). The pools belong to the memo, not to a fixpoint, because the memo lives no longer than a fixpoint and is released as one piece. `rankings` uses the same key and is filled only when the engine records rankings, so a fixpoint's memo has no ranking slot per entry, and an explain-mode hit returns the ranking its miss recorded.
#[derive(Clone, Debug, Default)]
struct TraceMemo {
    entries: HashMap<TraceKey, TraceEntry>,
    settled: SettledPool,
    notes: NotesPool,
    rankings: HashMap<TraceKey, Box<TraceRanking>>,
}

impl TraceMemo {
    /// Record one settled window: the trace's settled record and notes stored in the pools, the ranking stored separately when the trace has one, and the ids of the delta and read set the capture journaled.
    fn insert(&mut self, key: TraceKey, trace: &TransitionTrace, delta: DeltaId, reads: ReadsId) {
        let entry = TraceEntry::new(
            TraceSettledId::at(self.settled.intern(&trace.settled).index()),
            TraceNotesId::at(self.notes.intern(trace.notes.clone()).index()),
            delta,
            reads,
            trace.prospect,
            trace.joint_tiebreak,
            trace.decided_stage,
        );
        self.entries.insert(key, entry);
        if let Some(ranking) = &trace.ranking {
            self.rankings.insert(key, ranking.clone());
        }
    }

    /// The trace one entry stands for, rebuilt from the pools as its miss returned it: the settled record and the notes cloned out, the prospect widened back to `i64`, and the ranking read from the side map when one was recorded.
    fn trace(&self, key: &TraceKey, entry: TraceEntry) -> TransitionTrace {
        TransitionTrace {
            settled: self.settled.get(entry.settled.widen()).clone(),
            joint_tiebreak: entry.joint_tiebreak(),
            prospect: i64::from(entry.prospect()),
            decided_stage: entry.decided_stage(),
            notes: self.notes.get(entry.notes.widen()).to_vec(),
            ranking: self.rankings.get(key).cloned(),
        }
    }
}

/// One settlement engine per (spec, feature configuration).
pub struct Engine<'i> {
    index: &'i SpecIndex,
    features: HashSet<Sym>,
    follower_prefer_deep_slot: RightToken,
    simulated_prospect: bool,
    follower_prefer_slots: bool,
    simulated_prospect_fallbacks: u64,
    fired: HashSet<Pointer>,
    fired_log: Option<Vec<Pointer>>,
    capture_starts: Vec<usize>,
    /// The table every memoized fired delta is stored in, whichever memo journaled it. The trace, candidate, prospect and closure memos all hold [`DeltaId`]s into it, so a delta journaled by several memos is held once. It belongs to the engine because the closure and prospect memos store entries in every mode. Outside trace-memo mode nothing is journaled and there is no trace memo, and those two memos store the empty delta here, so the pool never grows past that one delta (issue #167). [`Engine::release_memos`] resets the pool with every memo that indexes it, so no id outlives its table.
    deltas: DeltaPool,
    /// The table every memoized read set is stored in (issue #184): the runes and classes an evaluation read from the spec, journaled by the index's accessors while the capture was open ([`crate::index`]), so a memo entry records which runes and classes its result depends on. Released with the delta pool, and detached into a snapshot with it.
    reads: ReadsPool,
    /// Where the read journal stood when each open capture began, one per open capture beside [`Engine::capture_starts`].
    read_starts: Vec<usize>,
    /// Each small memo stores its fired delta beside its verdict, as an id into [`Engine::deltas`], instead of in a second map on the same key: the delta is only read with its verdict, and a second table would pay for the key and its hash-table slack twice.
    closure_cache: HashMap<ClosureKey, (bool, DeltaId, ReadsId)>,
    candidates_cache: CandidatesMemo,
    /// The prospect memo: the term as an `i8`, since it is zero or one, beside the ids of its fired delta and read set (issue #166). With a trace memo in simulated-prospect mode it holds only the asks whose replayed settlement raised. A settling ask's answer is one field of a window the trace memo holds, so [`Engine::prospect`] skips the entry and reads the trace memo on the next ask. A probe's ask, whose window the trace memo also skips (issue #168), settles the window again, which the probes' own memos make rare.
    prospect_cache: HashMap<ProspectKey, (i8, DeltaId, ReadsId)>,
    exit_sources_cache: HashMap<StanceId, (Vec<ExitSource<'i>>, Vec<Pointer>)>,
    pairing_sets: HashMap<StanceId, PairingSets>,
    explain_ranking: bool,
    /// The window memo, present only in trace-memo mode. It is the engine's largest collection and sets the enumeration's peak memory, which is why its entries are ids into the [`TraceMemo`] pools instead of whole traces (issue #165). A hit rebuilds the trace from the pools, and the caller sees the same trace a stored one would give.
    trace_cache: Option<TraceMemo>,
    /// Finished memos of other enumerations this engine may read, in lookup order, each behind an exclusion that says which keys it may not supply ([`crate::memo`]). A window the engine's own memo misses is looked up here before it is settled. A hit is returned, with its delta replayed, as an own hit is, but is not copied into the own memo: the shared memos are read-only across a whole fan-out, and copying each hit would rebuild the memory the sharing saves.
    shared_memos: Vec<SharedMemo>,
    /// Per shared memo, which of its delta ids this engine has already added to its fired set, so a second hit on the same shared delta skips the set.
    shared_memo_fired: Vec<Vec<bool>>,
    /// How many windows each shared memo supplied, in lookup order, for the cache stats.
    shared_memo_hits: Vec<u64>,
}

impl<'i> Engine<'i> {
    /// An engine over one spec and one feature configuration, in the default modes: `simulated_prospect` and `follower_prefer_slots` on, the follower prefer's deep slot `UNKNOWN`, and no trace memo.
    pub fn new(index: &'i SpecIndex, features: impl IntoIterator<Item = Sym>) -> Self {
        Self::with_modes(index, features, EngineModes::default())
    }

    /// An engine with explicit modes.
    pub fn with_modes(
        index: &'i SpecIndex,
        features: impl IntoIterator<Item = Sym>,
        modes: EngineModes,
    ) -> Self {
        Self {
            index,
            features: features.into_iter().collect(),
            follower_prefer_deep_slot: modes.follower_prefer_deep_slot,
            simulated_prospect: modes.simulated_prospect,
            follower_prefer_slots: modes.follower_prefer_slots,
            simulated_prospect_fallbacks: 0,
            fired: HashSet::default(),
            fired_log: modes.trace_memo.then(Vec::new),
            capture_starts: Vec::new(),
            deltas: DeltaPool::default(),
            reads: ReadsPool::default(),
            read_starts: Vec::new(),
            closure_cache: HashMap::default(),
            candidates_cache: CandidatesMemo::default(),
            prospect_cache: HashMap::default(),
            exit_sources_cache: HashMap::default(),
            pairing_sets: HashMap::default(),
            explain_ranking: modes.explain_ranking,
            trace_cache: modes.trace_memo.then(TraceMemo::default),
            shared_memos: Vec::new(),
            shared_memo_fired: Vec::new(),
            shared_memo_hits: Vec::new(),
        }
    }

    /// Give this engine the finished memos it may read windows from, in lookup order. Only a trace-memo engine reads them, because only it journals and so can replay a shared memo hit's delta. Attaching them to any other engine panics.
    pub fn attach_shared_memos(&mut self, memos: Vec<SharedMemo>) {
        assert!(
            self.trace_cache.is_some(),
            "a shared memo can only answer an engine that journals, which is the trace-memo engine"
        );
        self.shared_memo_fired = memos
            .iter()
            .map(|shared| vec![false; shared.memo.deltas.len()])
            .collect();
        self.shared_memo_hits = vec![0; memos.len()];
        self.shared_memos = memos;
    }

    /// How many windows the shared memos supplied so far.
    pub fn shared_memo_hits(&self) -> u64 {
        self.shared_memo_hits.iter().sum()
    }

    /// How many windows each shared memo supplied, in lookup order, since they were attached.
    pub fn shared_memo_hits_by_id(&self) -> &[u64] {
        &self.shared_memo_hits
    }

    /// Detach this engine's trace memo: the entries compacted into an immutable array, with the tables their ids index and every fired delta and read set the engine stored. The candidate, closure and prospect memos are released before compaction, as [`Engine::release_memos`] releases them, because they index the delta table that leaves with the snapshot. The live trace map is consumed and freed before the array is partitioned and sorted. Returns `None` for an engine without a trace memo. The shared memos are not included: a snapshot holds only what this engine settled, and a caller that wants the union reads the shared memos beside it.
    pub fn take_memo(&mut self) -> Option<MemoSnapshot> {
        let memo = self.trace_cache.take()?;
        let deltas = std::mem::take(&mut self.deltas);
        let reads = std::mem::take(&mut self.reads);
        self.trace_cache = Some(TraceMemo::default());
        self.candidates_cache = CandidatesMemo::default();
        self.prospect_cache = HashMap::default();
        self.closure_cache = HashMap::default();
        Some(MemoSnapshot {
            entries: memo.entries.into(),
            settled: memo.settled.into_table(),
            notes: memo.notes.into_table(),
            deltas: deltas.table,
            reads: reads.table,
        })
    }

    /// The indexed spec this engine settles against.
    pub fn index(&self) -> &'i SpecIndex {
        self.index
    }

    /// The stylistic sets active in this configuration.
    pub fn features(&self) -> &HashSet<Sym> {
        &self.features
    }

    /// Whether the third join-count term is the follower's simulated transition or the candidacy estimate.
    pub fn simulated_prospect(&self) -> bool {
        self.simulated_prospect
    }

    /// Whether follower prefers read the window's slots shifted by one.
    pub fn follower_prefer_slots(&self) -> bool {
        self.follower_prefer_slots
    }

    /// The pin a follower prefer's beyond-`right1` slots take when [`Engine::follower_prefer_slots`] is off.
    pub fn follower_prefer_deep_slot(&self) -> RightToken {
        self.follower_prefer_deep_slot
    }

    /// How often a simulated prospect's replayed settlement raised and fell back to the candidacy estimate. Diagnostic only.
    pub fn simulated_prospect_fallbacks(&self) -> u64 {
        self.simulated_prospect_fallbacks
    }

    /// Whether this engine memoizes whole windows and journals a delta per memoized evaluation.
    pub fn trace_memo(&self) -> bool {
        self.trace_cache.is_some()
    }

    /// Drop every memo this engine holds, keeping the fired set and the small per-stance tables. After a fixpoint's last trace comes a drain and sort of the whole product, and keeping the window memos alive through it would set the enumeration's peak memory.
    ///
    /// Every memo is a pure cache: each entry replays the pointers its computation fired, so after a release the next evaluation fires them again, and no later result changes. A caller that reports the fired set, such as the table fixpoint, reads [`Engine::fired`] before releasing, so evaluations after the release are left out of the report. Each memo's pools go with its entries, since an id means nothing without its table, and the delta and reads pools go with every memo that indexes them.
    pub fn release_memos(&mut self) {
        self.trace_cache = self.trace_cache.as_ref().map(|_| TraceMemo::default());
        self.candidates_cache = CandidatesMemo::default();
        self.prospect_cache = HashMap::default();
        self.closure_cache = HashMap::default();
        self.deltas = DeltaPool::default();
        self.reads = ReadsPool::default();
    }

    /// Every authored record that fired under this configuration: refusals that removed a candidate, unlocks that granted a capability, row scopes that admitted a side, and the adjustments, prefers and resolves that shaped a committed cell. The dead-policy check reads this. Iteration order never reaches an output, so a hash set is enough.
    pub fn fired(&self) -> &HashSet<Pointer> {
        &self.fired
    }

    /// The fired-pointer delta this engine journaled while settling one window, in first-fired order, or `None` when the window was never traced through this engine. The corpus carries this per-window delta beside its result. It exists only in trace-memo mode.
    pub fn trace_delta(
        &self,
        left: &LeftContext,
        token: RightToken,
        slots: Slots,
    ) -> Option<&[Pointer]> {
        let memo = self.trace_cache.as_ref()?;
        let entry = memo
            .entries
            .get(&Self::trace_key(left, token.ordinal()?, slots))?;
        Some(self.deltas.get(entry.delta))
    }

    /// The runes and classes this engine read while settling one window, as the set its capture journaled, or `None` when that window was never traced through this engine.
    pub fn trace_reads(
        &self,
        left: &LeftContext,
        token: RightToken,
        slots: Slots,
    ) -> Option<&[Read]> {
        let memo = self.trace_cache.as_ref()?;
        let entry = memo
            .entries
            .get(&Self::trace_key(left, token.ordinal()?, slots))?;
        Some(self.reads.get(entry.reads))
    }

    /// Every memo this engine holds, as `--cache-stats` reports them: each one's length and capacity. The rows follow the fields' declaration order, each memo's pools after its entries, so two runs' cache stats line up row for row.
    pub fn cache_stats(&self) -> Vec<CacheSize> {
        let mut out = vec![
            CacheSize::of("fired", self.fired.len(), self.fired.capacity()),
            CacheSize::of("deltas", self.deltas.len(), self.deltas.capacity()),
            CacheSize::of("reads", self.reads.len(), self.reads.capacity()),
            CacheSize::of(
                "closure_cache",
                self.closure_cache.len(),
                self.closure_cache.capacity(),
            ),
            CacheSize::of(
                "candidates_cache",
                self.candidates_cache.entries.len(),
                self.candidates_cache.entries.capacity(),
            ),
            CacheSize::of(
                "candidate_lists",
                self.candidates_cache.candidates.len(),
                self.candidates_cache.candidates.capacity(),
            ),
            CacheSize::of(
                "elimination_lists",
                self.candidates_cache.eliminations.len(),
                self.candidates_cache.eliminations.capacity(),
            ),
            CacheSize::of(
                "prospect_cache",
                self.prospect_cache.len(),
                self.prospect_cache.capacity(),
            ),
            CacheSize::of(
                "exit_sources_cache",
                self.exit_sources_cache.len(),
                self.exit_sources_cache.capacity(),
            ),
            CacheSize::of(
                "pairing_sets",
                self.pairing_sets.len(),
                self.pairing_sets.capacity(),
            ),
        ];
        if let Some(memo) = self.trace_cache.as_ref() {
            out.push(CacheSize::of(
                "trace_cache",
                memo.entries.len(),
                memo.entries.capacity(),
            ));
            out.push(CacheSize::of(
                "trace_settled",
                memo.settled.len(),
                memo.settled.capacity(),
            ));
            out.push(CacheSize::of(
                "trace_notes",
                memo.notes.len(),
                memo.notes.capacity(),
            ));
            out.push(CacheSize::of(
                "trace_rankings",
                memo.rankings.len(),
                memo.rankings.capacity(),
            ));
        }
        out
    }

    /// How many bytes of elimination description the candidate and trace memos hold: explain-only text that a run which never reads a ranking still stores. It is counted, not estimated, because it decides whether formatting the descriptions is worth the memory. The candidate memo's part counts each distinct list in its elimination pool once, however many entries name it, because that is what is held (issue #167).
    pub fn elimination_text_bytes(&self) -> usize {
        let cached: usize = self
            .candidates_cache
            .eliminations
            .lists()
            .iter()
            .flatten()
            .map(|elimination| elimination.description.len())
            .sum();
        let traced: usize = self
            .trace_cache
            .iter()
            .flat_map(|memo| memo.rankings.values())
            .flat_map(|ranking| ranking.eliminations.iter())
            .map(|elimination| elimination.description.len())
            .sum();
        cached + traced
    }

    // --- the fired journal ---------------------------------------------------

    fn record_pointer(&mut self, pointer: Pointer) {
        if !self.capture_starts.is_empty()
            && let Some(log) = self.fired_log.as_mut()
        {
            log.push(pointer);
        }
        self.fired.insert(pointer);
    }

    fn record_fired(&mut self, provenance: Option<&Provenance>) {
        if let Some(provenance) = provenance {
            self.record_pointer(Pointer::of(provenance));
        }
    }

    fn begin_capture(&mut self) {
        let log = self
            .fired_log
            .as_ref()
            .expect("captures only open in trace-memo mode");
        self.capture_starts.push(log.len());
        self.read_starts.push(crate::index::journal_len());
        crate::index::journal_arm(true);
    }

    /// Close the innermost capture and return what fired inside it, deduplicated keeping each pointer's first firing, with the set of runes and classes read inside it. When the outermost capture closes, both journals are emptied, so neither grows past one top-level evaluation.
    fn end_capture(&mut self) -> Captured {
        let start = self
            .capture_starts
            .pop()
            .expect("every end_capture closes a begin_capture");
        let read_start = self
            .read_starts
            .pop()
            .expect("every end_capture closes a begin_capture");
        let log = self
            .fired_log
            .as_mut()
            .expect("captures only open in trace-memo mode");
        let mut seen: HashSet<Pointer> = HashSet::default();
        let mut delta = Vec::new();
        for pointer in &log[start..] {
            if seen.insert(*pointer) {
                delta.push(*pointer);
            }
        }
        let reads = crate::index::journal_since(read_start);
        if self.capture_starts.is_empty() {
            log.clear();
            crate::index::journal_clear();
            crate::index::journal_arm(false);
        }
        Captured {
            delta: delta.into_boxed_slice(),
            reads,
        }
    }

    /// Abandon the innermost capture. A raising evaluation records no delta, since it is never cached, but its firings stay journaled for any enclosing capture, because they fired during that evaluation and a fresh evaluation would fire them again. A simulated prospect whose replayed settlement succeeds also discards its capture instead of closing it (issue #166): everything it journaled belongs to the follower's window, which the trace memo holds under its own delta, so the enclosing window's delta is unchanged.
    fn abort_capture(&mut self) {
        self.capture_starts.pop();
        self.read_starts.pop();
        if self.capture_starts.is_empty() {
            self.fired_log
                .as_mut()
                .expect("captures only open in trace-memo mode")
                .clear();
            crate::index::journal_clear();
            crate::index::journal_arm(false);
        }
    }

    // --- condition matching ---------------------------------------------------

    fn left_exit_stroke(&self, left: &LeftContext) -> Option<Sym> {
        if left.kind != TokenKind::Letter {
            return None;
        }
        let settled = left.settled.as_ref()?;
        let junction = settled.junction?;
        let cell = &settled.cell;
        let index = self.index();
        index.rune(cell.rune)?;
        let id = index.stance_id(cell.rune, cell.stance).unwrap_or_else(|| {
            panic!(
                "{} declares no stance {}, exactly as rune.stances[…] raises KeyError",
                index.resolve(cell.rune),
                index.resolve(cell.stance)
            )
        });
        index.exit_row(id, junction).and_then(|(_, row)| row.stroke)
    }

    /// Whether a condition matches the resolved left neighbor. `junction` is the height of the join being decided between the left and this position — the candidate's entry, or `None` when unentered — which is what `joined_at:` and a from-scope condition read.
    pub fn cond_matches_left(
        &self,
        owner: Option<Sym>,
        cond: &Condition,
        left: &LeftContext,
        junction: Option<Sym>,
    ) -> Result<bool, SettleError> {
        let index = self.index();
        let vocab = index.vocab();
        if let Some(token) = cond.is_token {
            if token == vocab.boundary {
                if left.kind == TokenKind::Letter {
                    return Ok(false);
                }
            } else if vocab.kind(left.kind) != token {
                return Ok(false);
            }
        }
        let needs_letter = !cond.family.is_empty()
            || !cond.klass.is_empty()
            || !cond.stance.is_empty()
            || cond.joined_at.is_some()
            || cond.stroke.is_some();
        if needs_letter {
            if left.kind != TokenKind::Letter {
                return Ok(false);
            }
            let Some(settled) = left.settled.as_ref() else {
                return Ok(false);
            };
            let cell = &settled.cell;
            if !cond.family.is_empty() && !cond.family.contains(&cell.rune) {
                return Ok(false);
            }
            for klass in &cond.klass {
                if !index.class_members(*klass, owner)?.contains(&cell.rune) {
                    return Ok(false);
                }
            }
            if !cond.stance.is_empty() && !cond.stance.contains(&cell.stance) {
                return Ok(false);
            }
            if let Some(joined_at) = cond.joined_at
                && joined_at != vocab.height_state(junction)
            {
                return Ok(false);
            }
            if cond.stroke.is_some() && self.left_exit_stroke(left) != cond.stroke {
                return Ok(false);
            }
        }
        if cond.then.is_some() {
            return Err(SettleError::Plain(
                "left conditions cannot carry then: (window depth, design section 3.4)".to_owned(),
            ));
        }
        for excepted in &cond.except_ {
            if self.cond_matches_left(owner, excepted, left, junction)? {
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// Whether a condition matches the raw slots to the right. `tokens[0]` is the slot this condition tests. A `then:` hop recurses on the tail, and an `except:` entry tests the same slot with its own hops walking the same tail, so a chain reads one raw token per hop and reads `UNKNOWN` past the supplied window. `None` means the verdict depends on a slot outside the evaluated window. Refusals, unlocks and the closure all resolve `None` in the permissive direction, so none of them rules out a candidate because of a slot outside the window.
    pub fn cond_matches_right(
        &self,
        owner: Option<Sym>,
        cond: &Condition,
        tokens: &[RightToken],
    ) -> Result<Option<bool>, SettleError> {
        let index = self.index();
        let vocab = index.vocab();
        let token = tokens[0];
        let tail: &[RightToken] = if tokens.len() > 1 {
            &tokens[1..]
        } else {
            &UNKNOWN_TAIL
        };
        let mut unknown = false;
        if let Some(wanted) = cond.is_token {
            if token.kind() == TokenKind::Unknown {
                unknown = true;
            } else if wanted == vocab.boundary {
                if token.kind() == TokenKind::Letter {
                    return Ok(Some(false));
                }
            } else if vocab.kind(token.kind()) != wanted {
                return Ok(Some(false));
            }
        }
        if !cond.stance.is_empty() || cond.joined_at.is_some() {
            return Err(SettleError::Plain(
                "right conditions are raw: stance/joined_at are left-only axes (design section 3.4)"
                    .to_owned(),
            ));
        }
        let needs_letter =
            !cond.family.is_empty() || !cond.klass.is_empty() || cond.stroke.is_some();
        if needs_letter {
            if token.kind() == TokenKind::Unknown {
                unknown = true;
            } else if token.kind() != TokenKind::Letter {
                return Ok(Some(false));
            } else {
                let letter = token.letter();
                if !cond.family.is_empty() && !cond.family.contains(&letter) {
                    return Ok(Some(false));
                }
                for klass in &cond.klass {
                    if !index.class_members(*klass, owner)?.contains(&letter) {
                        return Ok(Some(false));
                    }
                }
                if let Some(stroke) = cond.stroke
                    && !index.entry_strokes(letter).contains(&stroke)
                {
                    return Ok(Some(false));
                }
            }
        }
        for excepted in &cond.except_ {
            match self.cond_matches_right(owner, excepted, tokens)? {
                Some(true) => return Ok(Some(false)),
                None => unknown = true,
                Some(false) => {}
            }
        }
        if let Some(then) = cond.then.as_deref() {
            match self.cond_matches_right(owner, then, tail)? {
                Some(false) => return Ok(Some(false)),
                None => unknown = true,
                Some(true) => {}
            }
        }
        Ok(if unknown { None } else { Some(true) })
    }

    /// Whether a `when:` gate holds for this window. `None` means the verdict depends on a slot outside the evaluated window. A definite `false` on any axis makes the verdict `false`, but an unknown on one axis makes the whole verdict unknown even when every other axis matched.
    pub fn when_matches(
        &self,
        owner: Option<Sym>,
        when: &When,
        left: &LeftContext,
        entry: Option<Sym>,
        junction: Option<Sym>,
        slots: Slots,
    ) -> Result<Option<bool>, SettleError> {
        let vocab = self.index().vocab();
        if let Some(feature) = when.feature
            && !self.features.contains(&feature)
        {
            return Ok(Some(false));
        }
        if let Some(state) = when.self_entry
            && state != vocab.liveness_state(entry)
        {
            return Ok(Some(false));
        }
        if let Some(state) = when.self_exit
            && state != vocab.liveness_state(junction)
        {
            return Ok(Some(false));
        }
        let mut unknown = false;
        if let Some(wanted) = when.word {
            match word_position(left.kind, slots.right1.kind()) {
                None => unknown = true,
                Some(position) if vocab.word(position) != wanted => return Ok(Some(false)),
                Some(_) => {}
            }
        }
        if let Some(cond) = when.left.as_ref()
            && !self.cond_matches_left(owner, cond, left, entry)?
        {
            return Ok(Some(false));
        }
        if let Some(cond) = when.right.as_ref() {
            match self.cond_matches_right(owner, cond, &slots.as_array())? {
                Some(false) => return Ok(Some(false)),
                None => unknown = true,
                Some(true) => {}
            }
        }
        Ok(if unknown { None } else { Some(true) })
    }

    // --- capability -------------------------------------------------------------

    /// Whether this stance offers a live entry at `height` against the left, and the note the commit carries when it does. A selectable declared row grants it when it has no from-scope or its from-scope admits the left. So does any unlock naming the height whose feature is active and whose `when:` does not definitely refuse the window. Treating an unknown verdict as a grant matches the closure.
    fn entry_available(
        &mut self,
        rune: &'i Rune,
        stance: &'i Stance,
        height: Sym,
        left: &LeftContext,
        right1: RightToken,
        right2: RightToken,
    ) -> Result<(bool, Option<String>), SettleError> {
        let index = self.index();
        let id = index
            .stance_id(rune.name, stance.name)
            .expect("the stance was reached through its own rune");
        if let Some((_, row)) = index.entry_row(id, height)
            && row.selectable
        {
            if row.scope.is_empty() {
                return Ok((true, None));
            }
            // This loop stops at the first match, so a later from-scope condition that would raise is never evaluated. The toward-scope loop in `candidates_uncached` evaluates every condition first. Keep both as they are: changing either changes which specs raise.
            let mut admitted = false;
            for cond in &row.scope {
                if self.cond_matches_left(Some(rune.name), cond, left, Some(height))? {
                    admitted = true;
                    break;
                }
            }
            if admitted {
                self.record_fired(row.provenance.as_ref());
                return Ok((true, None));
            }
        }
        for unlock in &stance.surface.unlocks {
            if unlock.entry != Some(height) || !self.features.contains(&unlock.feature) {
                continue;
            }
            let granted = match unlock.when.as_ref() {
                None => true,
                Some(when) => {
                    self.when_matches(
                        Some(rune.name),
                        when,
                        left,
                        Some(height),
                        None,
                        Slots::pair(right1, right2),
                    )? != Some(false)
                }
            };
            if granted {
                self.record_fired(unlock.provenance.as_ref());
                return Ok((
                    true,
                    Some(format!("unlocked by {}", index.resolve(unlock.feature))),
                ));
            }
        }
        Ok((false, None))
    }

    /// Every exit this stance can offer: the declared rows in declaration order at their own indexes, then the heights an active unlock grants that no declared row already declares, at indexes past the declared ones. The unlocks fire on every call, cache hit included, because an enumeration that hits the cache depends on them as much as the one that filled it.
    fn exit_sources(&mut self, id: StanceId) -> Vec<ExitSource<'i>> {
        if let Some((sources, fired)) = self.exit_sources_cache.get(&id) {
            let sources = sources.clone();
            let fired = fired.clone();
            for pointer in fired {
                self.record_pointer(pointer);
            }
            return sources;
        }
        let (sources, fired) = self.exit_sources_uncached(id);
        self.exit_sources_cache
            .insert(id, (sources.clone(), fired.clone()));
        for pointer in fired {
            self.record_pointer(pointer);
        }
        sources
    }

    fn exit_sources_uncached(&self, id: StanceId) -> (Vec<ExitSource<'i>>, Vec<Pointer>) {
        let index = self.index();
        let stance = index.stance(id);
        let mut sources: Vec<ExitSource<'i>> = stance
            .surface
            .exits
            .iter()
            .enumerate()
            .map(|(position, (height, row))| ExitSource {
                height: *height,
                row: Some(row),
                index: position,
            })
            .collect();
        let mut fired: Vec<Pointer> = Vec::new();
        let mut offset = sources.len();
        for unlock in &stance.surface.unlocks {
            if let Some(exit) = unlock.exit
                && !index.declares_exit(id, exit)
                && self.features.contains(&unlock.feature)
            {
                if let Some(provenance) = unlock.provenance.as_ref() {
                    fired.push(Pointer::of(provenance));
                }
                sources.push(ExitSource {
                    height: exit,
                    row: None,
                    index: offset,
                });
                offset += 1;
            }
        }
        (sources, fired)
    }

    /// The (entry-state, exit-state) pairs an active unlock admits in this window. An unlock with no `when:` is unconditional, and one whose `when:` is unknown still counts, as on the entry side.
    fn active_pairing_unlocks(
        &mut self,
        rune: &'i Rune,
        stance: &'i Stance,
        left: &LeftContext,
        entry: Option<Sym>,
        right1: RightToken,
        right2: RightToken,
    ) -> Result<Vec<(Sym, Sym)>, SettleError> {
        let mut active: Vec<(Sym, Sym)> = Vec::new();
        for unlock in &stance.surface.unlocks {
            let Some(pairing) = unlock.pairing.as_ref() else {
                continue;
            };
            if !self.features.contains(&unlock.feature) {
                continue;
            }
            if let Some(when) = unlock.when.as_ref()
                && self.when_matches(
                    Some(rune.name),
                    when,
                    left,
                    entry,
                    None,
                    Slots::pair(right1, right2),
                )? == Some(false)
            {
                continue;
            }
            self.record_fired(unlock.provenance.as_ref());
            active.push((pairing.entry, pairing.exit));
        }
        Ok(active)
    }

    /// Whether a stance admits this (entry-state, exit-state) combination: an unlocked pair is admitted outright, a `never:` pair is refused, and an `only:` list closes the set to itself.
    fn pairing_allowed(
        &mut self,
        id: StanceId,
        entry_state: Sym,
        exit_state: Sym,
        unlocked: &[(Sym, Sym)],
    ) -> bool {
        let pair = (entry_state, exit_state);
        if unlocked.contains(&pair) {
            return true;
        }
        let index = self.index();
        let sets = self.pairing_sets.entry(id).or_insert_with(|| {
            let pairings = &index.stance(id).surface.pairings;
            PairingSets {
                never: pairings
                    .never
                    .iter()
                    .map(|rule| (rule.entry, rule.exit))
                    .collect(),
                only: pairings.only.as_ref().map(|only| {
                    only.iter()
                        .map(|rule| (rule.entry, rule.exit))
                        .collect::<HashSet<(Sym, Sym)>>()
                }),
            }
        });
        if sets.never.contains(&pair) {
            return false;
        }
        match sets.only.as_ref() {
            Some(only) => only.contains(&pair),
            None => true,
        }
    }

    // --- refusals ----------------------------------------------------------------

    /// The first refuse record on this rune that removes the candidate. A record targets the whole join (no target fields, which removes only joining candidates), a stance, or a surface row. Only a definite verdict removes a candidate. An unknown one does not fire, so a refusal never fires because of a slot outside the window.
    fn refusal_hit(
        &mut self,
        rune: &'i Rune,
        candidate: &Candidate,
        left: &LeftContext,
        right1: RightToken,
        right2: RightToken,
    ) -> Result<Option<&'i PolicyRecord>, SettleError> {
        for record in &rune.policy.refuse {
            if record.stance.is_some() && record.stance != Some(candidate.stance) {
                continue;
            }
            if record.entry.is_some() && record.entry != candidate.entry {
                continue;
            }
            if record.exit.is_some() && record.exit != candidate.junction {
                continue;
            }
            if record.stance.is_none()
                && record.entry.is_none()
                && record.exit.is_none()
                && candidate.junction.is_none()
            {
                continue;
            }
            let verdict = self.when_matches(
                Some(rune.name),
                &record.when,
                left,
                candidate.entry,
                candidate.junction,
                Slots::pair(right1, right2),
            )?;
            if verdict == Some(true) {
                self.record_fired(record.provenance.as_ref());
                return Ok(Some(record));
            }
        }
        Ok(None)
    }

    // --- candidate enumeration -----------------------------------------------------

    /// Every pair candidate this rune offers in this window (a cell of the rune with the junction state it offers toward the next position), appending each eliminated candidate's reason to `eliminations` when that is given.
    ///
    /// The memo runs only in trace-memo mode: outside it there is no journal, so an entry could carry no delta to replay and a hit would lose the firings of its first evaluation. An entry holds four ids (issue #167), so a hit clones the candidate list out of the memo's pool, extends the caller's eliminations from the pool, and replays the delta and the read set.
    pub fn candidates(
        &mut self,
        left: &LeftContext,
        rune_name: Sym,
        right1: RightToken,
        right2: RightToken,
        eliminations: Option<&mut Vec<Elimination>>,
    ) -> Result<Vec<Candidate>, SettleError> {
        if self.fired_log.is_none() {
            return self.candidates_uncached(left, rune_name, right1, right2, eliminations);
        }
        let key = Self::candidates_key(self.index, left, rune_name, right1, right2);
        let entry = match self.candidates_cache.entries.get(&key) {
            Some(&cached) => {
                replay_into(
                    &mut self.fired_log,
                    &self.capture_starts,
                    self.deltas.get(cached.delta),
                );
                crate::index::journal_extend(self.reads.get(cached.reads));
                cached
            }
            None => {
                let mut local: Vec<Elimination> = Vec::new();
                self.begin_capture();
                let out = match self.candidates_uncached(
                    left,
                    rune_name,
                    right1,
                    right2,
                    Some(&mut local),
                ) {
                    Ok(out) => out,
                    Err(error) => {
                        self.abort_capture();
                        return Err(error);
                    }
                };
                let Captured { delta, reads } = self.end_capture();
                let memo = &mut self.candidates_cache;
                let entry = CandidatesEntry {
                    candidates: memo.candidates.intern(out),
                    eliminations: memo.eliminations.intern(local),
                    delta: self.deltas.intern(delta),
                    reads: self.reads.intern(reads),
                };
                memo.entries.insert(key, entry);
                entry
            }
        };
        let memo = &self.candidates_cache;
        if let Some(list) = eliminations {
            list.extend(memo.eliminations.get(entry.eliminations).iter().cloned());
        }
        Ok(memo.candidates.get(entry.candidates).to_vec())
    }

    /// The candidate memo's key. The rune's ordinal is the only lookup here. The rune field has an ordinal for every registered family, so this panics only for a name the registry has no family for. A registered but unmodeled rune panics in the enumeration itself.
    fn candidates_key(
        index: &SpecIndex,
        left: &LeftContext,
        rune_name: Sym,
        right1: RightToken,
        right2: RightToken,
    ) -> CandidatesKey {
        let rune = index.rune_ordinal(rune_name).unwrap_or_else(|| {
            panic!(
                "{} is no registered family, so it has no ordinal in the memo's rune field",
                index.resolve(rune_name)
            )
        });
        CandidatesKey {
            left_rune: left.ordinals.rune,
            left_stance: left.ordinals.stance,
            left_junction: left.ordinals.junction,
            rune,
            runes: [right1.ordinal(), right2.ordinal()],
            kinds: PackedKinds::of(&[left.kind, right1.kind(), right2.kind()]),
        }
    }

    fn candidates_uncached(
        &mut self,
        left: &LeftContext,
        rune_name: Sym,
        right1: RightToken,
        right2: RightToken,
        eliminations: Option<&mut Vec<Elimination>>,
    ) -> Result<Vec<Candidate>, SettleError> {
        let mut eliminations = EliminationSink {
            list: eliminations,
            describe: self.explain_ranking,
        };
        let index = self.index();
        let vocab = index.vocab();
        let position = index.rune_position(rune_name).unwrap_or_else(|| {
            panic!(
                "{} is not a modeled rune, exactly as spec.runes[…] raises KeyError",
                index.resolve(rune_name)
            )
        });
        let rune = index.rune_at(position);
        let committed = if left.kind == TokenKind::Letter {
            left.settled.as_ref().and_then(|settled| settled.junction)
        } else {
            None
        };
        let mut out: Vec<Candidate> = Vec::new();
        for (stance_position, (stance_name, stance)) in rune.stances.iter().enumerate() {
            let id = StanceId::new(
                position,
                u32::try_from(stance_position)
                    .expect("a rune declares far fewer than four billion stances"),
            );
            let order_index = index.order_index(id);
            let mut entry: Option<Sym> = None;
            if let Some(committed) = committed {
                let (available, _note) =
                    self.entry_available(rune, stance, committed, left, right1, right2)?;
                if !available {
                    record_elimination(
                        &mut eliminations,
                        EliminationStage::EntryBinding,
                        || {
                            format!(
                                "{}.{}: no available entry row at {} against the committed junction",
                                index.resolve(rune_name),
                                index.resolve(*stance_name),
                                index.resolve(committed)
                            )
                        },
                        None,
                    );
                    continue;
                }
                entry = Some(committed);
            }
            if stance.surface.require.contains(&vocab.entry) && entry.is_none() {
                record_elimination(
                    &mut eliminations,
                    EliminationStage::Require,
                    || {
                        format!(
                            "{}.{}: requires a live entry",
                            index.resolve(rune_name),
                            index.resolve(*stance_name)
                        )
                    },
                    None,
                );
                continue;
            }
            let unlocked =
                self.active_pairing_unlocks(rune, stance, left, entry, right1, right2)?;
            let entry_state = vocab.height_state(entry);
            if right1.kind() == TokenKind::Letter {
                for source in self.exit_sources(id) {
                    let height = source.height;
                    let candidate = Candidate::joining(
                        index,
                        rune_name,
                        *stance_name,
                        entry,
                        height,
                        order_index,
                        source.index,
                    );
                    if !self.pairing_allowed(id, entry_state, height, &unlocked) {
                        record_elimination(
                            &mut eliminations,
                            EliminationStage::Pairings,
                            || {
                                format!(
                                    "{}.{}: pairing ({}, {}) not allowed",
                                    index.resolve(rune_name),
                                    index.resolve(*stance_name),
                                    index.resolve(entry_state),
                                    index.resolve(height)
                                )
                            },
                            None,
                        );
                        continue;
                    }
                    if let Some(row) = source.row
                        && !row.scope.is_empty()
                    {
                        // Every scope condition is evaluated before the verdicts are read, so a later condition that raises still raises after an earlier one has matched.
                        let mut verdicts: Vec<Option<bool>> = Vec::with_capacity(row.scope.len());
                        for cond in &row.scope {
                            verdicts.push(self.cond_matches_right(
                                Some(rune_name),
                                cond,
                                &[right1, right2],
                            )?);
                        }
                        let scoped = verdicts.iter().any(|verdict| *verdict != Some(false));
                        if verdicts.contains(&Some(true)) {
                            self.record_fired(row.provenance.as_ref());
                        }
                        if !scoped {
                            record_elimination(
                                &mut eliminations,
                                EliminationStage::RowScope,
                                || {
                                    format!(
                                        "{}.{}: exit {} toward-scope does not admit {}",
                                        index.resolve(rune_name),
                                        index.resolve(*stance_name),
                                        index.resolve(height),
                                        index.resolve(right1.letter())
                                    )
                                },
                                row.provenance.as_ref(),
                            );
                            continue;
                        }
                    }
                    if !self.acceptor_exists(&candidate, rune_name, right1, right2)? {
                        record_elimination(
                            &mut eliminations,
                            EliminationStage::LookaheadClosure,
                            || {
                                format!(
                                    "{}.{}: exit {} has no refusal-aware acceptor cell on {}",
                                    index.resolve(rune_name),
                                    index.resolve(*stance_name),
                                    index.resolve(height),
                                    index.resolve(right1.letter())
                                )
                            },
                            None,
                        );
                        continue;
                    }
                    if let Some(record) =
                        self.refusal_hit(rune, &candidate, left, right1, right2)?
                    {
                        record_elimination(
                            &mut eliminations,
                            EliminationStage::Refuse,
                            || {
                                let mut description = format!(
                                    "{}.{}: exit {} refused",
                                    index.resolve(rune_name),
                                    index.resolve(*stance_name),
                                    index.resolve(height)
                                );
                                if let Some(why) = record.why.as_ref()
                                    && !why.is_empty()
                                {
                                    description.push_str(&format!(" \u{2014} {why}"));
                                }
                                description
                            },
                            record.provenance.as_ref(),
                        );
                        continue;
                    }
                    out.push(candidate);
                }
            }
            if stance.surface.require.contains(&vocab.exit) {
                continue;
            }
            let non_joining =
                Candidate::non_joining(index, rune_name, *stance_name, entry, order_index);
            if !self.pairing_allowed(id, entry_state, vocab.none, &unlocked) {
                record_elimination(
                    &mut eliminations,
                    EliminationStage::Pairings,
                    || {
                        format!(
                            "{}.{}: pairing ({}, none) not allowed",
                            index.resolve(rune_name),
                            index.resolve(*stance_name),
                            index.resolve(entry_state)
                        )
                    },
                    None,
                );
                continue;
            }
            if let Some(record) = self.refusal_hit(rune, &non_joining, left, right1, right2)? {
                record_elimination(
                    &mut eliminations,
                    EliminationStage::Refuse,
                    || {
                        format!(
                            "{}.{}: non-joining cell refused",
                            index.resolve(rune_name),
                            index.resolve(*stance_name)
                        )
                    },
                    record.provenance.as_ref(),
                );
                continue;
            }
            out.push(non_joining);
        }
        Ok(out)
    }

    /// The left a follower would settle against if this candidate won: the candidate's cell with no adjustments and no extension, which is everything the follower's enumeration reads. It is built on every call, not memoized: construction moves two arguments, creates an empty `Vec` without allocating, and reuses the candidate's ordinals, so a memo lookup on code that runs this often would cost more than it saves.
    fn synthetic_left(rune_name: Sym, candidate: Candidate) -> LeftContext {
        LeftContext::with_ordinals(
            Settled {
                cell: CellId {
                    rune: rune_name,
                    stance: candidate.stance,
                    entry: candidate.entry,
                    exit: candidate.junction,
                    adjustments: Vec::new(),
                },
                junction: candidate.junction,
                extension: 0,
            },
            candidate.ordinals.as_left(),
        )
    }

    /// Step 2's lookahead closure (design section 6.1): whether some cell of the follower survives its own pairings, require, unlocks, row scopes and every window-decidable refusal, with this candidate as the follower's resolved left and the raw slot past it as the follower's right. An exit with no refusal-aware acceptor is never a candidate, and slots past the window are treated optimistically.
    fn acceptor_exists(
        &mut self,
        candidate: &Candidate,
        rune_name: Sym,
        right1: RightToken,
        right2: RightToken,
    ) -> Result<bool, SettleError> {
        let Some(follower) = right1.rune() else {
            return Ok(false);
        };
        if !self.index().is_modeled(follower) {
            return Ok(false);
        }
        let key = ClosureKey {
            rune: rune_name,
            stance: candidate.stance,
            entry: candidate.entry,
            junction: candidate.junction,
            right1: follower,
            right2,
        };
        if let Some(&(cached, delta, reads)) = self.closure_cache.get(&key) {
            replay_into(
                &mut self.fired_log,
                &self.capture_starts,
                self.deltas.get(delta),
            );
            crate::index::journal_extend(self.reads.get(reads));
            return Ok(cached);
        }
        let synthetic_left = Self::synthetic_left(rune_name, *candidate);
        if self.fired_log.is_none() {
            let result = !self
                .candidates(&synthetic_left, follower, right2, UNKNOWN, None)?
                .is_empty();
            let delta = self.deltas.intern(Box::default());
            let reads = self.reads.intern(Box::default());
            self.closure_cache.insert(key, (result, delta, reads));
            return Ok(result);
        }
        self.begin_capture();
        let result = match self.candidates(&synthetic_left, follower, right2, UNKNOWN, None) {
            Ok(cells) => !cells.is_empty(),
            Err(error) => {
                self.abort_capture();
                return Err(error);
            }
        };
        let Captured { delta, reads } = self.end_capture();
        let delta = self.deltas.intern(delta);
        let reads = self.reads.intern(reads);
        self.closure_cache.insert(key, (result, delta, reads));
        Ok(result)
    }

    /// The trace memo's key. `token` is the input rune's ordinal, not the whole token, because a non-letter input returns the boundary trace before any key is built. Nothing is looked up: the left carries its ordinals and each letter token its rune's.
    fn trace_key(left: &LeftContext, token: Ordinal, slots: Slots) -> TraceKey {
        let tokens = slots.as_array();
        TraceKey {
            left_rune: left.ordinals.rune,
            left_stance: left.ordinals.stance,
            left_junction: left.ordinals.junction,
            left_extension: i16::try_from(
                left.settled.as_ref().map_or(0, |settled| settled.extension),
            )
            .expect("an extension is a count of connector pixels"),
            token,
            runes: tokens.map(RightToken::ordinal),
            kinds: PackedKinds::of(&[
                left.kind,
                tokens[0].kind(),
                tokens[1].kind(),
                tokens[2].kind(),
                tokens[3].kind(),
            ]),
        }
    }

    // --- the prospect term -----------------------------------------------------------

    /// What the junction past this one is worth given this candidate: the join count's third term, in either of its modes.
    ///
    /// With `simulated_prospect` on (the default), the term is the follower's simulated transition: the follower's full settlement run one position over, with this candidate as the follower's left and the window shifted right. It scores 1 when the simulated winner has a junction. The recursion only moves right, over strictly fewer slots, and stops at the window edge, where a non-letter slot scores 0, so text past the window stays unknown. With the mode off (the section 5.7 guard's setting and the comparison state), the term is the optimistic candidacy estimate: 1 when any junction-bearing follower cell survives enumeration. That estimate respects refusals but ignores the follower's prefers and ordering.
    ///
    /// A replayed settlement can raise where real settlement never would, for example on a prefer conflict in a window whose candidate never wins. A raising replay falls back to the candidacy estimate and counts in [`Engine::simulated_prospect_fallbacks`]. The fallback catches every [`SettleError`], including the unresolvable-class spec defect, which `spec_load` rejects long before settlement.
    ///
    /// The memo stores a term beside the id of its fired delta, and with a trace memo in simulated mode it holds only the asks whose replayed settlement raised (issue #166). A settling replay's delta equals the trace memo's delta for the follower's window: the synthetic left journals nothing, and deduplicating what [`Engine::with_settled`] journaled (a replayed trace delta, or the raw firings the trace memo deduplicated into that same delta) gives that delta again. The trace memo already holds that entry, under a key without this candidate's entry, so the capture is discarded instead of stored ([`Engine::abort_capture`]). The next ask with this key reads the trace memo through `with_settled`, which replays the same first-fired sequence into the same enclosing capture at the same point. A raising replay is never stored in the trace memo, so its fallback is what this memo is for. Candidacy mode runs no replay and memoizes every ask, and so does simulated mode without a trace memo, where nothing else can answer the next ask.
    ///
    /// A replayed settlement asked for while no window is being evaluated is a probe's ask. [`Engine::probe_prospect`] is the only caller that reaches the term that way, because the ranking asks only from inside a trace. Its follower window is settled through [`Engine::with_settled_unrecorded`]: read from the trace memo when the memo holds it, and not added when it does not (issue #168). The probes memoize their verdicts above this call on their own keys, so the window is asked for again only by a row whose ranking reaches the same shifted window. An instrumented run over the whole alphabet measured how rarely that happens: the probes' replays wrote well over a third of the trace memo's entries and nearly all were never read, and the fourth-slot probes' windows (a letter third and an unknown fourth, which a row reaches only past a live fourth slot) almost never. In that run, recording them pushed the memo's bucket table past a power-of-two doubling, and leaving them out kept it under. The replays that a probe's replay runs in turn are recorded as usual, since every ranking shares those windows.
    fn prospect(
        &mut self,
        rune_name: Sym,
        candidate: Candidate,
        slots: Slots,
    ) -> Result<i64, SettleError> {
        let Some(follower) = slots.right1.rune() else {
            return Ok(0);
        };
        if slots.right2.kind() != TokenKind::Letter {
            return Ok(0);
        }
        let CandidateOrdinals {
            rune,
            stance,
            entry,
            junction,
        } = candidate.ordinals;
        let right1 = slots.right1.letter_ordinal();
        let key = if self.simulated_prospect {
            let deep = [slots.right2, slots.right3, slots.right4];
            ProspectKey::Simulated {
                rune,
                stance,
                entry,
                junction,
                right1,
                runes: deep.map(RightToken::ordinal),
                kinds: PackedKinds::of(&[deep[0].kind(), deep[1].kind(), deep[2].kind()]),
            }
        } else {
            ProspectKey::Candidacy {
                rune,
                stance,
                entry,
                junction,
                right1,
                right2: slots.right2.letter_ordinal(),
            }
        };
        if let Some(&(cached, delta_id, reads)) = self.prospect_cache.get(&key) {
            replay_into(
                &mut self.fired_log,
                &self.capture_starts,
                self.deltas.get(delta_id),
            );
            crate::index::journal_extend(self.reads.get(reads));
            return Ok(i64::from(cached));
        }
        let capturing = self.fired_log.is_some();
        let recorded = !self.capture_starts.is_empty();
        if capturing {
            self.begin_capture();
        }
        let (result, term) =
            match self.prospect_uncached(rune_name, candidate, slots, follower, recorded) {
                Ok(answer) => answer,
                Err(error) => {
                    if capturing {
                        self.abort_capture();
                    }
                    return Err(error);
                }
            };
        if capturing && term == ProspectTerm::Simulated {
            self.abort_capture();
            return Ok(result);
        }
        let (delta, reads) = if capturing {
            let Captured { delta, reads } = self.end_capture();
            (delta, reads)
        } else {
            (Box::default(), Box::default())
        };
        let delta_id = self.deltas.intern(delta);
        let reads = self.reads.intern(reads);
        let prospect = i8::try_from(result).expect("a prospect is a junction count, zero or one");
        self.prospect_cache.insert(key, (prospect, delta_id, reads));
        Ok(result)
    }

    /// The term computed from scratch, with how it was computed: from the follower's simulated trace, or by the candidacy estimate, either as the mode's own term or as the fallback after a raising replay. `recorded` says whether the follower's window may enter the trace memo. [`Engine::prospect`] withholds that for a probe's ask.
    fn prospect_uncached(
        &mut self,
        rune_name: Sym,
        candidate: Candidate,
        slots: Slots,
        follower: Sym,
        recorded: bool,
    ) -> Result<(i64, ProspectTerm), SettleError> {
        let synthetic_left = Self::synthetic_left(rune_name, candidate);
        if !self.simulated_prospect {
            let estimate =
                self.junction_bearing_follower_exists(&synthetic_left, follower, slots.right2)?;
            return Ok((estimate, ProspectTerm::Estimated));
        }
        let shifted = Slots::new(slots.right2, slots.right3, slots.right4, UNKNOWN);
        let read_junction = |settled: &Settled| i64::from(settled.junction.is_some());
        let simulated = if recorded {
            self.with_settled(&synthetic_left, slots.right1, shifted, read_junction)
        } else {
            self.with_settled_unrecorded(&synthetic_left, slots.right1, shifted, read_junction)
        };
        match simulated {
            Ok(junction_bearing) => Ok((junction_bearing, ProspectTerm::Simulated)),
            Err(_) => {
                self.simulated_prospect_fallbacks += 1;
                let estimate =
                    self.junction_bearing_follower_exists(&synthetic_left, follower, slots.right2)?;
                Ok((estimate, ProspectTerm::Estimated))
            }
        }
    }

    /// The candidacy estimate: whether any follower cell that survives enumeration offers a junction onward.
    fn junction_bearing_follower_exists(
        &mut self,
        synthetic_left: &LeftContext,
        follower: Sym,
        right2: RightToken,
    ) -> Result<i64, SettleError> {
        let cells = self.candidates(synthetic_left, follower, right2, UNKNOWN, None)?;
        Ok(i64::from(cells.iter().any(|cell| cell.junction.is_some())))
    }

    // --- prefers ---------------------------------------------------------------------

    /// Whether one prefer record favors this candidate. `None` means the record has nothing to say about this window, which keeps an irrelevant record out of the stage instead of counting it against the candidate.
    ///
    /// A record of our own rune targets the candidate's stance or cell directly and reads the window's deep slots as they are. A record with both a stance and a cell compares cells only within that stance: the stance limits where the preference applies and is not itself the demand. A follower's record is instead a follower prefer: it favors the candidates under which its own preferred continuation is admissible, evaluated one position over with `joined_at` bound to the candidate's junction. With `follower_prefer_slots` on, the follower prefer reads the window's slots shifted by one, so a chained condition resolves inside the window. With it off, everything past the follower prefer's own `right1` is `follower_prefer_deep_slot`, and unknown verdicts there count as firing, so a deep-chained condition has to be repeated on every possible left rune instead of written once on the rune that owns it.
    fn prefer_favors(
        &mut self,
        owner: Sym,
        record: &PolicyRecord,
        rune_name: Sym,
        candidate: Candidate,
        left: &LeftContext,
        slots: Slots,
    ) -> Result<Option<bool>, SettleError> {
        let vocab = self.index().vocab();
        if owner == rune_name {
            let verdict = self.when_matches(
                Some(owner),
                &record.when,
                left,
                candidate.entry,
                candidate.junction,
                slots,
            )?;
            if verdict == Some(false) {
                return Ok(None);
            }
            if let Some(stance) = record.stance {
                if record.cell.is_none() {
                    return Ok(Some(candidate.stance == stance));
                }
                if candidate.stance != stance {
                    return Ok(None);
                }
            }
            if let Some(pattern) = record.cell.as_ref() {
                let favored = cell_pattern_matches(vocab, pattern, &candidate);
                if let Some(over) = record.over.as_ref()
                    && !favored
                    && !cell_pattern_matches(vocab, over, &candidate)
                {
                    return Ok(None);
                }
                return Ok(Some(favored));
            }
            return Ok(None);
        }
        if slots.right1.rune() != Some(owner) {
            return Ok(None);
        }
        let synthetic_left = Self::synthetic_left(rune_name, candidate);
        let (follower_prefer_right2, follower_prefer_right3) = if self.follower_prefer_slots {
            (slots.right3, slots.right4)
        } else {
            (self.follower_prefer_deep_slot, UNKNOWN)
        };
        let follower_cells = self.candidates(
            &synthetic_left,
            owner,
            slots.right2,
            follower_prefer_right2,
            None,
        )?;
        let shifted_slots = Slots::new(
            slots.right2,
            follower_prefer_right2,
            follower_prefer_right3,
            UNKNOWN,
        );
        let mut relevant = false;
        for cell in &follower_cells {
            if record.cell.is_some() && record.stance.is_some_and(|stance| cell.stance != stance) {
                continue;
            }
            let verdict = self.when_matches(
                Some(owner),
                &record.when,
                &synthetic_left,
                cell.entry,
                cell.junction,
                shifted_slots,
            )?;
            if verdict == Some(false) {
                continue;
            }
            if record.stance.is_some()
                && let Some(pattern) = record.cell.as_ref()
                && !cell_pattern_matches(vocab, pattern, cell)
                && record
                    .over
                    .as_ref()
                    .is_some_and(|over| !cell_pattern_matches(vocab, over, cell))
            {
                continue;
            }
            relevant = true;
            if let Some(stance) = record.stance
                && record.cell.is_none()
                && cell.stance == stance
            {
                return Ok(Some(true));
            }
            if let Some(pattern) = record.cell.as_ref()
                && cell_pattern_matches(vocab, pattern, cell)
            {
                return Ok(Some(true));
            }
        }
        Ok(if relevant { Some(false) } else { None })
    }

    // --- the probe surface -------------------------------------------------------------

    /// [`Engine::prospect`], exposed to the deep-slot liveness probes.
    ///
    /// The probes read an internal ranking term, and these two wrappers keep that access visible without widening the settlement API. The probes are the only callers, and the wrappers only delegate. The candidate a probe passes is [`crate::liveness`]'s probe candidate (no entry, order index 0, and the `NO_EXIT_INDEX` sentinel), not one the enumeration produced.
    #[allow(dead_code)]
    pub(crate) fn probe_prospect(
        &mut self,
        rune_name: Sym,
        candidate: Candidate,
        slots: Slots,
    ) -> Result<i64, SettleError> {
        self.prospect(rune_name, candidate, slots)
    }

    /// [`Engine::prefer_favors`], exposed to the liveness probe's follower prefer branch, like [`Engine::probe_prospect`].
    #[allow(dead_code)]
    pub(crate) fn probe_prefer_favors(
        &mut self,
        owner: Sym,
        record: &PolicyRecord,
        rune_name: Sym,
        candidate: Candidate,
        left: &LeftContext,
        slots: Slots,
    ) -> Result<Option<bool>, SettleError> {
        self.prefer_favors(owner, record, rune_name, candidate, left, slots)
    }

    /// One prefer stage, absolute or yielding, over the records of both junction runes, most specific first.
    ///
    /// Records are gathered in declaration order, our own rune's before the follower's, then sorted by how many other applicable records outrank them, so the narrowest applies first and a nested conflict resolves by set membership without an error. When a record's favored set no longer overlaps the survivors, the stage looks for an already applied record of equal or incomparable specificity. Within one rune that raises E-AMBIGUOUS. Across two runes, a `resolve:` naming the collision settles it, and without one it raises E-INCOMPARABLE.
    fn apply_prefers(
        &mut self,
        mode_absolute: bool,
        rune_name: Sym,
        survivors: &[Candidate],
        left: &LeftContext,
        slots: Slots,
        notes: &mut Vec<String>,
    ) -> Result<Vec<Candidate>, SettleError> {
        if survivors.len() <= 1 {
            return Ok(survivors.to_vec());
        }
        let index = self.index();
        let vocab = index.vocab();
        let follower_owner = match slots.right1.rune() {
            Some(rune) if index.is_modeled(rune) => Some(rune),
            _ => None,
        };
        let mut gathered: Vec<OwnedRecord<'i>> = Vec::new();
        for owner in [Some(rune_name), follower_owner].into_iter().flatten() {
            let rune = index
                .rune(owner)
                .expect("both gathered owners were checked modeled before they got here");
            for record in &rune.policy.prefer {
                if (record.mode == Some(vocab.absolute)) != mode_absolute {
                    continue;
                }
                gathered.push(OwnedRecord { owner, record });
            }
        }
        if gathered.is_empty() {
            return Ok(survivors.to_vec());
        }
        let mut applicable: Vec<Applicable<'i>> = Vec::new();
        for OwnedRecord { owner, record } in gathered {
            let mut favored: HashSet<Candidate> = HashSet::default();
            let mut supported = false;
            for candidate in survivors {
                let Some(favors) =
                    self.prefer_favors(owner, record, rune_name, *candidate, left, slots)?
                else {
                    if record.stance.is_some() && record.cell.is_some() {
                        favored.insert(*candidate);
                    }
                    continue;
                };
                if favors {
                    supported = true;
                    favored.insert(*candidate);
                }
            }
            if supported && favored.len() < survivors.len() {
                applicable.push(Applicable {
                    owner,
                    record,
                    favored,
                });
            }
        }
        if applicable.is_empty() {
            return Ok(survivors.to_vec());
        }
        // Comparing precomputed axes gives the same result as `specificity::outranks` on each pair, without re-expanding each record's `when:` per pair.
        let mut axes = Vec::with_capacity(applicable.len());
        for entry in &applicable {
            axes.push(specificity::axis_sets(
                index,
                &entry.record.when,
                Some(entry.owner),
            )?);
        }
        let mut outranked_by: Vec<usize> = Vec::with_capacity(applicable.len());
        for (position, own) in axes.iter().enumerate() {
            let mut beaten = 0;
            for (other_position, other) in axes.iter().enumerate() {
                if other_position != position
                    && specificity::compare_axes(other, own) == specificity::Ordering::AOutranks
                {
                    beaten += 1;
                }
            }
            outranked_by.push(beaten);
        }
        let mut ordered: Vec<usize> = (0..applicable.len()).collect();
        ordered.sort_by_key(|position| outranked_by[*position]);

        let mut current = survivors.to_vec();
        let mut applied: Vec<OwnedRecord<'i>> = Vec::new();
        for position in ordered {
            let Applicable {
                owner,
                record,
                favored,
            } = &applicable[position];
            let (owner, record) = (*owner, *record);
            let narrowed: Vec<Candidate> = current
                .iter()
                .copied()
                .filter(|candidate| favored.contains(candidate))
                .collect();
            if !narrowed.is_empty() {
                current = narrowed;
                applied.push(OwnedRecord { owner, record });
                self.record_fired(record.provenance.as_ref());
                notes.push(format!(
                    "prefer applied: {}",
                    provenance_text(index, record.provenance.as_ref())
                ));
                continue;
            }
            let mut conflicted = 0;
            while conflicted < applied.len() {
                let previous = applied[conflicted];
                conflicted += 1;
                let rank = specificity::outranks(
                    index,
                    previous.record,
                    record,
                    Some(previous.owner),
                    Some(owner),
                )?;
                if rank != specificity::Ordering::Equal
                    && rank != specificity::Ordering::Incomparable
                {
                    continue;
                }
                if previous.owner == owner {
                    return Err(SettleError::Ambiguous(format!(
                        "E-AMBIGUOUS: prefer records demand different outcomes at non-nested specificity: {} vs {}",
                        provenance_text(index, previous.record.provenance.as_ref()),
                        provenance_text(index, record.provenance.as_ref())
                    )));
                }
                let held = OwnedRecord { owner, record };
                let resolved =
                    self.apply_resolution(previous, held, survivors, left, slots, notes)?;
                let Some(resolved) = resolved else {
                    return Err(SettleError::Incomparable(self.incomparable_message(
                        previous, held, rune_name, survivors, left, slots,
                    )));
                };
                current = resolved;
                applied.push(held);
                break;
            }
        }
        Ok(current)
    }

    /// The design section 5.8 resolution against a named record: a conflict between two runes' prefers resolves without an error when a `resolve:` on either rune names the other record in `against:` and its own `when:` does not definitely refuse this window. Unknown deep slots count as matching, as they do for refusals and unlocks.
    ///
    /// The `pick:` pattern filters the stage's whole survivor set, not the narrowed list, because the resolve overrides both colliding records. Its provenance is added to the fired set and the notes, so explain output and the dead-policy check both see it. `None` means no resolve covers this conflict, which makes the collision E-INCOMPARABLE. Two matching resolves with different picks, and a pick that admits no survivor, raise E-INCOMPARABLE themselves.
    fn apply_resolution(
        &mut self,
        a: OwnedRecord<'i>,
        b: OwnedRecord<'i>,
        survivors: &[Candidate],
        left: &LeftContext,
        slots: Slots,
        notes: &mut Vec<String>,
    ) -> Result<Option<Vec<Candidate>>, SettleError> {
        let index = self.index();
        let vocab = index.vocab();
        let mut matches: Vec<OwnedRecord<'i>> = Vec::new();
        for (holder, other) in [(a, b), (b, a)] {
            let Some(holder_rune) = index.rune(holder.owner) else {
                continue;
            };
            for resolution in &holder_rune.policy.resolve {
                let (Some((target_name, target_id)), Some(_)) =
                    (resolution.against, resolution.pick.as_ref())
                else {
                    continue;
                };
                if target_name != other.owner {
                    continue;
                }
                if target_id.is_some() && target_id != other.record.id {
                    continue;
                }
                let verdict = self.when_matches(
                    Some(holder.owner),
                    &resolution.when,
                    left,
                    None,
                    None,
                    slots,
                )?;
                if verdict == Some(false) {
                    continue;
                }
                matches.push(OwnedRecord {
                    owner: holder.owner,
                    record: resolution,
                });
            }
        }
        if matches.is_empty() {
            return Ok(None);
        }
        let picks: HashSet<Vec<(&str, &str)>> = matches
            .iter()
            .filter_map(|entry| entry.record.pick.as_ref())
            .map(|pick| sorted_pick_items(index, pick))
            .collect();
        if picks.len() > 1 {
            let described: Vec<String> = matches
                .iter()
                .map(|entry| provenance_text(index, entry.record.provenance.as_ref()))
                .collect();
            return Err(SettleError::Incomparable(format!(
                "E-INCOMPARABLE: conflicting resolve records match one window: {}",
                described.join("; ")
            )));
        }
        let chosen = matches[0].record;
        let pick = chosen
            .pick
            .as_ref()
            .expect("a resolve reaches the matches only with a pick");
        let picked: Vec<Candidate> = survivors
            .iter()
            .copied()
            .filter(|candidate| resolve_pick_matches(vocab, pick, candidate))
            .collect();
        if picked.is_empty() {
            return Err(SettleError::Incomparable(format!(
                "E-INCOMPARABLE: resolve {} matched but its pick admits no surviving candidate",
                provenance_text(index, chosen.provenance.as_ref())
            )));
        }
        self.record_fired(chosen.provenance.as_ref());
        notes.push(format!(
            "resolve applied: {}",
            provenance_text(index, chosen.provenance.as_ref())
        ));
        Ok(Some(picked))
    }

    /// The E-INCOMPARABLE message: the two records, an example window written in rune names, the candidates they conflicted over, and a paste-ready `resolve:` record for the rune that owns the window. All of it, the stub included, must stay as it is, because the author copies the stub into the rune's YAML. A record with no `id:` prints the instruction to give it one instead of an empty field.
    ///
    /// Three fields treat an empty string as absent, and all three empty values can occur in a dump: a rune named `""` is left out of the example window instead of adding a space, a height named `""` prints `none`, and a record whose `id:` is `""` prints the instruction to give it one. See [`text_or`].
    fn incomparable_message(
        &self,
        a: OwnedRecord<'i>,
        b: OwnedRecord<'i>,
        rune_name: Sym,
        survivors: &[Candidate],
        left: &LeftContext,
        right: Slots,
    ) -> String {
        let index = self.index();
        let vocab = index.vocab();
        let mut window: Vec<&str> = Vec::new();
        if left.kind == TokenKind::Letter
            && let Some(settled) = left.settled.as_ref()
        {
            window.push(index.resolve(settled.cell.rune));
        }
        window.push(index.resolve(rune_name));
        for token in [right.right1, right.right2] {
            if let Some(rune) = token.rune() {
                window.push(index.resolve(rune));
            }
        }
        window.retain(|name| !name.is_empty());
        let cells: Vec<String> = survivors
            .iter()
            .map(|candidate| {
                format!(
                    "({}, entry {}, exit {})",
                    index.resolve(candidate.stance),
                    text_or(index.resolve(vocab.height_state(candidate.entry)), "none"),
                    text_or(
                        index.resolve(vocab.height_state(candidate.junction)),
                        "none"
                    )
                )
            })
            .collect();
        let other = if a.owner == rune_name { b } else { a };
        let against_id = text_or(
            other.record.id.map_or("", |id| index.resolve(id)),
            "<give that record an id: first>",
        );
        let mut when_clause = String::new();
        if let Some(follower) = right.right1.rune() {
            let mut inner = format!("family: {}", index.resolve(follower));
            if let Some(second) = right.right2.rune() {
                inner.push_str(&format!(", then: {{family: {}}}", index.resolve(second)));
            }
            when_clause = format!("    when: {{right: {{{inner}}}}}\n");
        }
        let rune_text = index.resolve(rune_name);
        format!(
            "E-INCOMPARABLE: prefer records demand different outcomes at non-nested specificity: {} vs {}.\n  example window: {}\n  conflicted candidates on {rune_text}: {}\n  paste-ready resolve for glyph_data/runes/{rune_text}.yaml policy.resolve (design section 5.8):\n  - against: {{rune: {}, id: {against_id}}}\n{when_clause}    pick: {{exit: <the winning cell>}}\n    why: <author rationale, mandatory>",
            provenance_text(index, a.record.provenance.as_ref()),
            provenance_text(index, b.record.provenance.as_ref()),
            window.join(" "),
            cells.join(", "),
            index.resolve(other.owner),
        )
    }

    // --- extensions and the commit -----------------------------------------------------

    /// The extend or contract record that shapes one side of the winning cell: records naming this side's height and nothing on the other side, limited to the candidate's stance, whose `when:` holds definitely. An adjustment moves pixels, so an unknown slot is not enough. Several matches go to the design section 6.2 order, where tied records with the same demand collapse to one and tied records with different demands raise E-INCOMPARABLE.
    fn pick_adjustment(
        &mut self,
        kind: AdjustmentKind,
        rune: &'i Rune,
        candidate: &Candidate,
        side: Side,
        left: &LeftContext,
        right: Slots,
    ) -> Result<Option<&'i PolicyRecord>, SettleError> {
        let height = match side {
            Side::Entry => candidate.entry,
            Side::Exit => candidate.junction,
        }
        .expect("a side is only shaped once it is known to be live");
        let records = match kind {
            AdjustmentKind::Extend => &rune.policy.extend,
            AdjustmentKind::Contract => &rune.policy.contract,
        };
        let mut matching: Vec<&'i PolicyRecord> = Vec::new();
        for record in records {
            let (target_height, other_height) = match side {
                Side::Entry => (record.entry, record.exit),
                Side::Exit => (record.exit, record.entry),
            };
            if target_height != Some(height) || other_height.is_some() {
                continue;
            }
            if record.stance.is_some() && record.stance != Some(candidate.stance) {
                continue;
            }
            let verdict = self.when_matches(
                Some(rune.name),
                &record.when,
                left,
                candidate.entry,
                candidate.junction,
                right,
            )?;
            if verdict == Some(true) {
                matching.push(record);
            }
        }
        let chosen = match matching.len() {
            0 => return Ok(None),
            1 => matching[0],
            _ => {
                let owners = vec![Some(rune.name); matching.len()];
                specificity::pick_most_specific(self.index(), &matching, &owners)?
            }
        };
        self.record_fired(chosen.provenance.as_ref());
        Ok(Some(chosen))
    }

    /// The unjoined bindings a declined exit is drawn with. When a join does not happen mid-word the exit state is none, and each exit row that names an unjoined bitmap adds that drawing to the cell's identity as an `ex-bind-<bitmap>` token. An `unjoined: safe` row adds nothing, leaving the plain exit-none cell. A `cells:` composition for this (entry-state, unjoined-height) pair overrides the row's bitmap, and the last matching composition wins, because the scan does not stop early.
    fn unjoined_tokens(&self, stance: &Stance, entry: Option<Sym>) -> Vec<AdjustmentToken> {
        let index = self.index();
        let vocab = index.vocab();
        let entry_state = vocab.height_state(entry);
        let mut tokens: Vec<AdjustmentToken> = Vec::new();
        for (height, row) in stance.surface.exits.iter() {
            let Some(unjoined) = row.unjoined else {
                continue;
            };
            if unjoined == vocab.safe {
                continue;
            }
            let unjoined_exit = index.unjoined_state(*height);
            let mut bitmap = unjoined;
            for binding in &stance.surface.cells {
                if binding.entry == entry_state && Some(binding.exit) == unjoined_exit {
                    bitmap = binding.bitmap;
                }
            }
            tokens.push(AdjustmentToken::Bind(Side::Exit, bitmap));
        }
        tokens
    }

    /// The window join count: the junction behind us, the junction we offer, and what the junction past us is worth, which the caller asks [`Engine::prospect`] for once and passes in.
    fn score(candidate: Candidate, committed: Option<Sym>, prospect: i64) -> i64 {
        let left_term = i64::from(committed.is_some());
        let own_term = i64::from(candidate.junction.is_some());
        left_term + own_term + prospect
    }

    /// Turn the winning candidate into the cell it settles as: the ZWNJ lock first, then each live side's extend and contract, then the exit side's extension in pixels, then, for a declined join mid-word, the unjoined bindings.
    ///
    /// Same-junction extensions do not add up (the `same-junction-extension-non-summing` divergence class): a follower's entry extension is suppressed when the predecessor's exit already carries the junction's connector pixels, because both would otherwise draw them. The suppressed record still fires and is still noted as applied, because it matched and the dead-policy check should see it, and the suppression adds its own note.
    ///
    /// `right` is the two-slot window: the caller passes only `right1` and `right2`, with the deeper slots `UNKNOWN`, so an adjustment record whose condition reaches past the follower reads the window edge, and geometry never depends on a slot the emitted lookup cannot key on.
    fn commit(
        &mut self,
        rune: &'i Rune,
        winner: Candidate,
        locked: bool,
        left: &LeftContext,
        right: Slots,
        notes: &mut Vec<String>,
    ) -> Result<Settled, SettleError> {
        let index = self.index();
        let id = index
            .stance_id(rune.name, winner.stance)
            .unwrap_or_else(|| {
                panic!(
                    "{} declares no stance {}, exactly as rune.stances[…] raises KeyError",
                    index.resolve(rune.name),
                    index.resolve(winner.stance)
                )
            });
        let stance = index.stance(id);
        let mut adjustments: Vec<AdjustmentToken> = Vec::new();
        if locked {
            adjustments.push(AdjustmentToken::Locked);
        }
        if let Some(entry) = winner.entry {
            let (available, unlock_note) =
                self.entry_available(rune, stance, entry, left, right.right1, right.right2)?;
            if available
                && let Some(note) = unlock_note
                && !notes.contains(&note)
            {
                notes.push(note);
            }
            let mut extend = self.pick_adjustment(
                AdjustmentKind::Extend,
                rune,
                &winner,
                Side::Entry,
                left,
                right,
            )?;
            let contract = self.pick_adjustment(
                AdjustmentKind::Contract,
                rune,
                &winner,
                Side::Entry,
                left,
                right,
            )?;
            note_applied(index, notes, extend);
            note_applied(index, notes, contract);
            if extend.is_some()
                && left
                    .settled
                    .as_ref()
                    .is_some_and(|settled| settled.extension > 0)
            {
                notes.push(
                    "entry extension suppressed: the predecessor's exit already carries the junction's connector pixels (same-junction non-summing)"
                        .to_owned(),
                );
                extend = None;
            }
            adjustments.extend(adjustment_tokens(index, Side::Entry, extend, contract)?);
        }
        let mut extension = 0;
        if winner.junction.is_some() {
            let extend = self.pick_adjustment(
                AdjustmentKind::Extend,
                rune,
                &winner,
                Side::Exit,
                left,
                right,
            )?;
            let contract = self.pick_adjustment(
                AdjustmentKind::Contract,
                rune,
                &winner,
                Side::Exit,
                left,
                right,
            )?;
            note_applied(index, notes, extend);
            note_applied(index, notes, contract);
            if let Some(record) = extend
                && let Some(by) = record.by
                && by != 0
            {
                extension += by;
            }
            if let Some(record) = contract
                && let Some(by) = record.by
                && by != 0
                && record.bind.is_none()
                && record.trim.is_none()
            {
                extension -= by;
            }
            adjustments.extend(adjustment_tokens(index, Side::Exit, extend, contract)?);
        } else if right.right1.kind() == TokenKind::Letter {
            adjustments.extend(self.unjoined_tokens(stance, winner.entry));
        }
        Ok(Settled {
            cell: CellId {
                rune: rune.name,
                stance: winner.stance,
                entry: winner.entry,
                exit: winner.junction,
                adjustments,
            },
            junction: winner.junction,
            extension,
        })
    }

    // --- the kernel ---------------------------------------------------------------------

    /// Settle one window, returning the full trace the table builder and the explain CLI read.
    ///
    /// In trace-memo mode the result is memoized over the reduced left key. The kernel reads the left only through its kind and the settled cell's rune, stance, junction and extension: condition matching reads the rune and stance, the stroke axis the committed junction, the scoring the junction's presence, and the same-junction suppression the extension. It never reads the left cell's entry or adjustments, so two lefts differing only there share one entry. The memo holds ids, not traces, so a hit is rebuilt from the memo's pools, returns what its miss returned, and replays the miss's fired delta. A window the own memo misses is looked up in the shared memos next and answered from the first one that holds and admits it. Only then is it settled. Raising windows are never cached: the E-UNACCEPTED-EXIT message includes the left's full label, which the key does not, and the liveness probes that hit settlement errors memoize their own verdicts above this call.
    pub fn transition_trace(
        &mut self,
        left: &LeftContext,
        token: RightToken,
        slots: Slots,
    ) -> Result<TransitionTrace, SettleError> {
        if token.kind() != TokenKind::Letter {
            return Ok(TransitionTrace {
                settled: boundary_settled(self.index().vocab(), token.kind()),
                joint_tiebreak: false,
                prospect: 0,
                decided_stage: DecidedStage::Boundary,
                notes: Vec::new(),
                ranking: None,
            });
        }
        if self.trace_cache.is_none() {
            return self.transition_trace_uncached(left, token, slots);
        }
        let key = Self::trace_key(left, token.letter_ordinal(), slots);
        if let Some(memo) = self.trace_cache.as_ref()
            && let Some(&entry) = memo.entries.get(&key)
        {
            let trace = memo.trace(&key, entry);
            replay_into(
                &mut self.fired_log,
                &self.capture_starts,
                self.deltas.get(entry.delta),
            );
            crate::index::journal_extend(self.reads.get(entry.reads));
            return Ok(trace);
        }
        if let Some((id, shared, entry)) = shared_memo_entry(&self.shared_memos, &key) {
            let trace = shared.memo.trace(entry);
            replay_shared(
                &mut self.fired,
                &mut self.fired_log,
                &self.capture_starts,
                &mut self.shared_memo_fired[id],
                entry.delta,
                shared.memo.delta(entry),
            );
            crate::index::journal_extend(shared.memo.reads(entry));
            self.shared_memo_hits[id] += 1;
            return Ok(trace);
        }
        self.begin_capture();
        let trace = match self.transition_trace_uncached(left, token, slots) {
            Ok(trace) => trace,
            Err(error) => {
                self.abort_capture();
                return Err(error);
            }
        };
        let Captured { delta, reads } = self.end_capture();
        let delta = self.deltas.intern(delta);
        let reads = self.reads.intern(reads);
        self.trace_cache
            .as_mut()
            .expect("the memo is what brought us here")
            .insert(key, &trace, delta, reads);
        Ok(trace)
    }

    /// Read one or two fields from a window's settled record where the record sits in the memo, without copying it. [`Engine::transition_trace`] returns a whole owned trace, and the callers here want only a junction or a cell from the settled record, so a memo hit through it would clone the notes and the adjustment list to answer a question about one `Option`.
    ///
    /// A hit replays the fired delta as [`Engine::transition_trace`] does, because that keeps a warm engine's fired set equal to a cold one's.
    pub(crate) fn with_settled<T>(
        &mut self,
        left: &LeftContext,
        token: RightToken,
        slots: Slots,
        read: impl Fn(&Settled) -> T,
    ) -> Result<T, SettleError> {
        if let Some(answer) = self.settled_from_memo(left, token, slots, &read) {
            return Ok(answer);
        }
        let trace = self.transition_trace(left, token, slots)?;
        Ok(read(&trace.settled))
    }

    /// [`Engine::with_settled`] for a letter window not worth a memo entry: a hit is returned the same way, delta replayed, and a miss settles the window without recording it. The miss opens no capture of its own, so its firings go straight into the enclosing capture, as a recorded miss's raw firings do, and every window evaluated beneath it is memoized as usual. The caller is [`Engine::prospect`]'s probe path, whose doc comment has the measurement that justifies skipping the entry.
    fn with_settled_unrecorded<T>(
        &mut self,
        left: &LeftContext,
        token: RightToken,
        slots: Slots,
        read: impl Fn(&Settled) -> T,
    ) -> Result<T, SettleError> {
        if let Some(answer) = self.settled_from_memo(left, token, slots, &read) {
            return Ok(answer);
        }
        let trace = self.transition_trace_uncached(left, token, slots)?;
        Ok(read(&trace.settled))
    }

    /// The memo's answer for one window, from the engine's own trace memo or a shared memo: the read applied to the settled record where it sits, with the entry's fired delta replayed. `None` is a miss (a non-letter token, an engine with no memo, or a window no memo has) and says nothing about how the caller should settle it.
    fn settled_from_memo<T>(
        &mut self,
        left: &LeftContext,
        token: RightToken,
        slots: Slots,
        read: impl Fn(&Settled) -> T,
    ) -> Option<T> {
        if token.kind() != TokenKind::Letter {
            return None;
        }
        let memo = self.trace_cache.as_ref()?;
        let key = Self::trace_key(left, token.letter_ordinal(), slots);
        if let Some(&entry) = memo.entries.get(&key) {
            let answer = read(memo.settled.get(entry.settled.widen()));
            replay_into(
                &mut self.fired_log,
                &self.capture_starts,
                self.deltas.get(entry.delta),
            );
            crate::index::journal_extend(self.reads.get(entry.reads));
            return Some(answer);
        }
        let (id, shared, entry) = shared_memo_entry(&self.shared_memos, &key)?;
        let answer = read(shared.memo.settled(entry));
        replay_shared(
            &mut self.fired,
            &mut self.fired_log,
            &self.capture_starts,
            &mut self.shared_memo_fired[id],
            entry.delta,
            shared.memo.delta(entry),
        );
        crate::index::journal_extend(shared.memo.reads(entry));
        self.shared_memo_hits[id] += 1;
        Some(answer)
    }

    fn transition_trace_uncached(
        &mut self,
        left: &LeftContext,
        token: RightToken,
        slots: Slots,
    ) -> Result<TransitionTrace, SettleError> {
        let index = self.index();
        let rune_name = token.letter();
        let Some(rune) = index.rune(rune_name) else {
            return Err(SettleError::Plain(format!(
                "{} is not a modeled rune",
                index.resolve(rune_name)
            )));
        };
        let committed = if left.kind == TokenKind::Letter {
            left.settled.as_ref().and_then(|settled| settled.junction)
        } else {
            None
        };
        let locked = left.kind == TokenKind::Zwnj && index.is_entry_bearing(rune_name);
        let mut notes: Vec<String> = Vec::new();
        let mut eliminations: Vec<Elimination> = Vec::new();
        let survivors = self.candidates(
            left,
            rune_name,
            slots.right1,
            slots.right2,
            Some(&mut eliminations),
        )?;
        // Design section 6.3 compensation (b): the pointer of every record that eliminated a candidate here goes into the notes, so the decision-rule TSVs and the emitted FEA carry per-rule provenance comments.
        for elimination in &eliminations {
            if let Some(provenance) = elimination.provenance.as_ref() {
                let pointer = provenance_pointer(index, provenance);
                if !notes.contains(&pointer) {
                    notes.push(pointer);
                }
            }
        }
        if survivors.is_empty() {
            if let Some(committed) = committed {
                let settled = left
                    .settled
                    .as_ref()
                    .expect("a committed junction comes from a settled left");
                return Err(SettleError::UnacceptedExit(format!(
                    "E-UNACCEPTED-EXIT: {} committed an exit at {} but {} has no acceptor cell (the lookahead closure should have prevented this commitment)",
                    cell_label(index, &settled.cell),
                    index.resolve(committed),
                    index.resolve(rune_name)
                )));
            }
            return Err(SettleError::Plain(format!(
                "{} has no candidate cells at all in this window",
                index.resolve(rune_name)
            )));
        }

        let mut ranked_order: Vec<Candidate> = Vec::new();
        let mut ranked: HashMap<Candidate, RankedCandidate> = HashMap::default();
        for candidate in &survivors {
            let prospect = self.prospect(rune_name, *candidate, slots)?;
            let join_count = Self::score(*candidate, committed, prospect);
            let scored = RankedCandidate {
                candidate: *candidate,
                join_count,
                prospect,
            };
            if ranked.insert(*candidate, scored).is_none() {
                ranked_order.push(*candidate);
            }
        }
        let mut decided_stage = DecidedStage::OnlyCandidate;
        let mut runner_up: Option<Candidate> = None;

        let mut survivors =
            self.apply_prefers(true, rune_name, &survivors, left, slots, &mut notes)?;
        if survivors.len() == 1 && ranked_order.len() > 1 {
            decided_stage = DecidedStage::AbsolutePrefer;
        }

        if survivors.len() > 1 {
            let best = survivors
                .iter()
                .map(|candidate| ranked[candidate].join_count)
                .max()
                .expect("the survivor list is not empty");
            let narrowed: Vec<Candidate> = survivors
                .iter()
                .copied()
                .filter(|candidate| ranked[candidate].join_count == best)
                .collect();
            if narrowed.len() < survivors.len() {
                runner_up = survivors
                    .iter()
                    .copied()
                    .find(|candidate| !narrowed.contains(candidate));
                if narrowed.len() == 1 {
                    decided_stage = DecidedStage::JoinCount;
                }
            }
            survivors = narrowed;
        }

        if survivors.len() > 1 {
            let before = survivors.clone();
            survivors =
                self.apply_prefers(false, rune_name, &survivors, left, slots, &mut notes)?;
            if survivors.len() == 1 {
                decided_stage = DecidedStage::YieldingPrefer;
                runner_up = before
                    .into_iter()
                    .find(|candidate| !survivors.contains(candidate));
            }
        }

        if survivors.len() > 1 {
            let best_order = survivors
                .iter()
                .map(|candidate| candidate.order_index)
                .min()
                .expect("the survivor list is not empty");
            let narrowed: Vec<Candidate> = survivors
                .iter()
                .copied()
                .filter(|candidate| candidate.order_index == best_order)
                .collect();
            if narrowed.len() == 1 {
                decided_stage = DecidedStage::Order;
                runner_up = survivors
                    .iter()
                    .copied()
                    .find(|candidate| !narrowed.contains(candidate));
            }
            survivors = narrowed;
        }

        let mut joint_tiebreak = false;
        if survivors.len() > 1 {
            let mut ordered = survivors.clone();
            ordered.sort_by_key(|candidate| tiebreak_key(index, candidate));
            decided_stage = DecidedStage::Tiebreak;
            runner_up = Some(ordered[1]);
            joint_tiebreak = ordered[0].junction.is_none() != ordered[1].junction.is_none();
            survivors = vec![ordered[0]];
        }

        let winner = survivors[0];
        let settled = self.commit(
            rune,
            winner,
            locked,
            left,
            Slots::pair(slots.right1, slots.right2),
            &mut notes,
        )?;
        let ranking = self.explain_ranking.then(|| {
            let mut scored: Vec<RankedCandidate> = ranked_order
                .iter()
                .map(|candidate| ranked[candidate])
                .collect();
            scored.sort_by_key(|entry| {
                (
                    -entry.join_count,
                    entry.candidate.order_index,
                    entry.candidate.exit_index,
                )
            });
            Box::new(TraceRanking {
                ranked: scored,
                eliminations,
                runner_up,
            })
        });
        Ok(TransitionTrace {
            settled,
            joint_tiebreak,
            prospect: ranked[&winner].prospect,
            decided_stage,
            notes,
            ranking,
        })
    }
}

/// Append one candidate's elimination when the caller asked for eliminations. The description is built lazily because the closure and the prospect enumerate with eliminations off, and formatting unread text is avoidable cost in the enumeration's inner loop. For the same reason, a sink that is not building a ranking stores an empty description and keeps only the stage and the pointer the notes are built from.
fn record_elimination(
    sink: &mut EliminationSink<'_>,
    stage: EliminationStage,
    description: impl FnOnce() -> String,
    provenance: Option<&Provenance>,
) {
    let describe = sink.describe;
    if let Some(list) = sink.list.as_deref_mut() {
        list.push(Elimination {
            stage,
            description: if describe {
                description()
            } else {
                String::new()
            },
            provenance: provenance.cloned(),
        });
    }
}

/// The first shared memo that holds `key` and admits it, with its index among the shared memos and the entry. A shared memo whose exclusion covers the key's runes or the entry's reads is skipped, because that entry was settled under runes or classes this engine does not share.
fn shared_memo_entry<'b>(
    shared_memos: &'b [SharedMemo],
    key: &TraceKey,
) -> Option<(usize, &'b SharedMemo, TraceEntry)> {
    shared_memos.iter().enumerate().find_map(|(id, shared)| {
        shared
            .memo
            .entries
            .get(key)
            .filter(|entry| shared.excluded.admits(key, shared.memo.reads(**entry)))
            .map(|&entry| (id, shared, entry))
    })
}

/// Replay one own-memo entry's fired delta into the journal. It takes only the two fields it touches, not the whole engine, so a hit can replay the delta from the memo without copying it: the memo and the journal are disjoint fields. The fired set is not touched, because an own entry's pointers entered it when the entry was recorded (the journal inserts every pointer it logs). An open capture still needs the delta, because the enclosing window's delta must include what its evaluation would have fired. A configuration's windows hit their own memo tens of millions of times, and a hash insert per pointer per hit measured as most of a hit's cost.
fn replay_into(fired_log: &mut Option<Vec<Pointer>>, capture_starts: &[usize], delta: &[Pointer]) {
    if delta.is_empty() || capture_starts.is_empty() {
        return;
    }
    let log = fired_log
        .as_mut()
        .expect("a capture is only ever open while the journal exists");
    log.extend_from_slice(delta);
}

/// Replay a shared memo entry's fired delta the same way, except that the shared memo's pointers were journaled by another engine, so the fired set holds them only after this engine has replayed that delta id once. `replayed` has one flag per delta id of the shared memo.
fn replay_shared(
    fired: &mut HashSet<Pointer>,
    fired_log: &mut Option<Vec<Pointer>>,
    capture_starts: &[usize],
    replayed: &mut [bool],
    id: DeltaId,
    delta: &[Pointer],
) {
    if delta.is_empty() {
        return;
    }
    if !replayed[id.index()] {
        fired.extend(delta.iter().copied());
        replayed[id.index()] = true;
    }
    replay_into(fired_log, capture_starts, delta);
}

/// One authored value from a `cell:`, `over:` or `pick:` mapping. The model keeps these as ordered lists of two or three keys, so a linear scan is the lookup.
fn pattern_value(pattern: &Table<Sym>, key: Sym) -> Option<Sym> {
    pattern
        .iter()
        .find(|(field, _)| *field == key)
        .map(|(_, value)| *value)
}

/// Whether a cell pattern describes this candidate. The pattern names states rather than live heights, so an absent side is the `none` state and matches a pattern that asks for it.
fn cell_pattern_matches(vocab: &Vocab, pattern: &Table<Sym>, candidate: &Candidate) -> bool {
    if let Some(wanted) = pattern_value(pattern, vocab.entry)
        && wanted != vocab.height_state(candidate.entry)
    {
        return false;
    }
    if let Some(wanted) = pattern_value(pattern, vocab.exit)
        && wanted != vocab.height_state(candidate.junction)
    {
        return false;
    }
    true
}

/// Whether a resolve's `pick:` admits this candidate: the stance when one is named, and the entry and exit keys read as a cell pattern. A pick that names only a stance imposes no cell pattern at all.
fn resolve_pick_matches(vocab: &Vocab, pick: &Table<Sym>, candidate: &Candidate) -> bool {
    if let Some(wanted) = pattern_value(pick, vocab.stance)
        && candidate.stance != wanted
    {
        return false;
    }
    let names_a_side =
        pattern_value(pick, vocab.entry).is_some() || pattern_value(pick, vocab.exit).is_some();
    !names_a_side || cell_pattern_matches(vocab, pick, candidate)
}

/// A `pick:` as its (key, value) text pairs, sorted: the form in which two resolves' picks are compared for agreement.
fn sorted_pick_items<'a>(index: &'a SpecIndex, pick: &Table<Sym>) -> Vec<(&'a str, &'a str)> {
    let mut items: Vec<(&str, &str)> = pick
        .iter()
        .map(|(key, value)| (index.resolve(*key), index.resolve(*value)))
        .collect();
    items.sort();
    items
}

/// `text`, or `fallback` when `text` is empty. An empty value is possible: `id: ""` and a registry height named `""` both survive `kernel_io`'s reader, so a test for absence alone would print an empty field. Only the message-formatting sites use this. The `when:` clause beside them writes its family names as they are, empty or not.
fn text_or<'a>(text: &'a str, fallback: &'a str) -> &'a str {
    if text.is_empty() { fallback } else { text }
}

/// A provenance as the notes and the error messages write it. A record with no provenance prints `None`, which also shows that it was authored without a pointer.
fn provenance_text(index: &SpecIndex, provenance: Option<&Provenance>) -> String {
    match provenance {
        Some(provenance) => provenance_pointer(index, provenance),
        None => "None".to_owned(),
    }
}

/// Note one applied adjustment record on the trace, once. A record with no provenance notes nothing, because there is no pointer for the TSV to carry.
fn note_applied(index: &SpecIndex, notes: &mut Vec<String>, record: Option<&PolicyRecord>) {
    if let Some(provenance) = record.and_then(|record| record.provenance.as_ref()) {
        let pointer = provenance_pointer(index, provenance);
        if !notes.contains(&pointer) {
            notes.push(pointer);
        }
    }
}

/// The adjustment tokens one side's chosen extend and contract produce. Both records apply, so their pixels add up on the side. The tokens come in grammar order: the side's binding, then the extension, then the contract's trim and, only when the contract has neither a binding nor a trim, its plain contraction. The binding comes first, whichever record names it, because geometry applies tokens in order and the bound drawing is the one the connector arithmetic then lengthens or shortens. A side is drawn only one way, so two records that bind it to different drawings raise E-INCOMPARABLE, and two that bind the same drawing write it once. An extend of zero pixels produces no extension token, while a contract of zero pixels still produces its token: the extension is read for a nonzero value and the contraction for presence.
fn adjustment_tokens(
    index: &SpecIndex,
    side: Side,
    extend: Option<&PolicyRecord>,
    contract: Option<&PolicyRecord>,
) -> Result<Vec<AdjustmentToken>, SettleError> {
    if let (Some(extend_record), Some(contract_record)) = (extend, contract)
        && let (Some(extend_bind), Some(contract_bind)) = (extend_record.bind, contract_record.bind)
        && extend_bind != contract_bind
    {
        let described = |record: &PolicyRecord| match &record.provenance {
            Some(provenance) => provenance_pointer(index, provenance),
            None => index.resolve(record.kind).to_owned(),
        };
        let side_name = match side {
            Side::Entry => "entry",
            Side::Exit => "exit",
        };
        return Err(SettleError::Incomparable(format!(
            "E-INCOMPARABLE: an extend record and a contract record bind the {side_name} side to different drawings: {} binds {}; {} binds {}. A side is drawn only one way, so settle it by editing the records: bind the same drawing, drop one record's bind:, or narrow one record's when: so the two no longer co-match.",
            described(extend_record),
            index.resolve(extend_bind),
            described(contract_record),
            index.resolve(contract_bind),
        )));
    }
    let mut tokens: Vec<AdjustmentToken> = Vec::new();
    if let Some(bind) = extend
        .and_then(|record| record.bind)
        .or_else(|| contract.and_then(|record| record.bind))
    {
        tokens.push(AdjustmentToken::Bind(side, bind));
    }
    if let Some(record) = extend
        && let Some(by) = record.by
        && by != 0
    {
        tokens.push(AdjustmentToken::Extend(side, by));
    }
    if let Some(record) = contract {
        if let Some(trim) = record.trim {
            tokens.push(AdjustmentToken::Trim(side, trim));
        }
        if let Some(by) = record.by
            && record.bind.is_none()
            && record.trim.is_none()
        {
            tokens.push(AdjustmentToken::Contract(side, by));
        }
    }
    Ok(tokens)
}

/// The final tiebreak's sort key: realizing the junction beats declining it, a lower junction beats a higher one, and the exit row's declaration index decides the rest. Realizing the left junction is the same for every candidate, because entry binding is bilateral, so it is not part of the key.
fn tiebreak_key(index: &SpecIndex, candidate: &Candidate) -> (usize, i64, usize) {
    match candidate.junction {
        Some(junction) => (
            0,
            index.y_of(junction).expect(
                "a candidate's junction is registry-declared, as registry.heights[…] assumes",
            ),
            candidate.exit_index,
        ),
        None => (1, 1_000_000, candidate.exit_index),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::error::SettleErrorKind;
    use crate::index::fixtures;
    use crate::types::{EDGE, NO_EXIT_INDEX, SPACE};

    /// A JSON object over already-built pieces, for mappings whose keys the fixtures build.
    fn object(entries: &[(String, String)]) -> String {
        let pairs: Vec<String> = entries
            .iter()
            .map(|(key, value)| format!("\"{key}\":{value}"))
            .collect();
        format!("{{{}}}", pairs.join(","))
    }

    fn row(height: &str, overrides: &[(&str, &str)]) -> (String, String) {
        (height.to_owned(), fixtures::row(height, overrides))
    }

    fn surface(entries: &str, exits: &str, extra: &[(&str, &str)]) -> String {
        let mut fields = vec![("entries", entries), ("exits", exits)];
        fields.extend_from_slice(extra);
        fixtures::surface(&fields)
    }

    fn stance(name: &str, surface: &str) -> (String, String) {
        (
            name.to_owned(),
            fixtures::stance(name, &[("surface", surface)]),
        )
    }

    fn letter(name: &str, stances: &[(String, String)], policy: &str) -> (String, String) {
        let stances = object(stances);
        (
            name.to_owned(),
            fixtures::rune(name, &[("stances", stances.as_str()), ("policy", policy)]),
        )
    }

    fn spec_of(runes: &[(String, String)]) -> SpecIndex {
        fixtures::index_of(&fixtures::dump(
            &object(runes),
            &fixtures::four_family_registry(),
        ))
    }

    fn plain_policy() -> String {
        fixtures::policy(&[])
    }

    /// The small alphabet most of these tests enumerate over.
    ///
    /// `qsPea` is the rune under enumeration: `half` enters at the baseline and exits at both heights, and `full` has no entry surface and exits only at the baseline. `qsTea` accepts a baseline entry and offers no exit, so it accepts at the baseline and not at the x-height, which rules out `qsPea`'s x-height exit. `qsMay` accepts a baseline entry only through an `ss03` unlock, and `qsIt` has no surface at all, so nothing reaches it.
    fn alphabet() -> SpecIndex {
        let pea = letter(
            "qsPea",
            &[
                stance(
                    "half",
                    &surface(
                        &object(&[row("baseline", &[])]),
                        &object(&[row("baseline", &[]), row("x-height", &[])]),
                        &[],
                    ),
                ),
                stance(
                    "full",
                    &surface("{}", &object(&[row("baseline", &[])]), &[]),
                ),
            ],
            &plain_policy(),
        );
        let tea = letter(
            "qsTea",
            &[stance(
                "plain",
                &surface(&object(&[row("baseline", &[])]), "{}", &[]),
            )],
            &plain_policy(),
        );
        let may = letter(
            "qsMay",
            &[stance(
                "alt",
                &surface(
                    &object(&[row("baseline", &[("selectable", "false")])]),
                    "{}",
                    &[(
                        "unlocks",
                        &fixtures::seq(&[
                            r#"{"feature":"ss03","entry":"baseline","exit":null,"pairing":null,"when":null,"why":null,"provenance":["qsMay.yaml","stances.alt.unlocks[0]"]}"#,
                        ]),
                    )],
                ),
            )],
            &plain_policy(),
        );
        let it = letter(
            "qsIt",
            &[stance("solo", &surface("{}", "{}", &[]))],
            &plain_policy(),
        );
        spec_of(&[pea, tea, may, it])
    }

    fn no_features() -> Vec<Sym> {
        Vec::new()
    }

    fn letter_token(index: &SpecIndex, name: &str) -> RightToken {
        fixtures::letter(index, name)
    }

    /// A settled letter left carrying `junction`, built from a real (rune, stance) pair, since [`LeftContext::letter`] panics on any other.
    fn settled_left(
        index: &SpecIndex,
        rune: &str,
        stance: &str,
        junction: Option<&str>,
    ) -> LeftContext {
        let junction = junction.map(|height| fixtures::sym(index, height));
        LeftContext::letter(
            index,
            Settled {
                cell: CellId {
                    rune: fixtures::sym(index, rune),
                    stance: fixtures::sym(index, stance),
                    entry: None,
                    exit: junction,
                    adjustments: Vec::new(),
                },
                junction,
                extension: 0,
            },
        )
    }

    fn descriptions(eliminations: &[Elimination]) -> Vec<&str> {
        eliminations
            .iter()
            .map(|elimination| elimination.description.as_str())
            .collect()
    }

    #[test]
    fn enumeration_offers_every_stance_and_every_exit_row_the_closure_admits() {
        let index = alphabet();
        let mut engine = Engine::new(&index, no_features());
        let mut eliminations = Vec::new();
        let out = engine
            .candidates(
                &LeftContext::boundary(TokenKind::Edge),
                fixtures::sym(&index, "qsPea"),
                letter_token(&index, "qsTea"),
                EDGE,
                Some(&mut eliminations),
            )
            .expect("the fixture raises nothing");
        let half = fixtures::sym(&index, "half");
        let full = fixtures::sym(&index, "full");
        let baseline = fixtures::sym(&index, "baseline");
        assert_eq!(
            out,
            vec![
                Candidate::joining(
                    &index,
                    fixtures::sym(&index, "qsPea"),
                    half,
                    None,
                    baseline,
                    0,
                    0
                ),
                Candidate::non_joining(&index, fixtures::sym(&index, "qsPea"), half, None, 0),
                Candidate::joining(
                    &index,
                    fixtures::sym(&index, "qsPea"),
                    full,
                    None,
                    baseline,
                    1,
                    0
                ),
                Candidate::non_joining(&index, fixtures::sym(&index, "qsPea"), full, None, 1),
            ]
        );
        assert_eq!(
            descriptions(&eliminations),
            ["qsPea.half: exit x-height has no refusal-aware acceptor cell on qsTea"],
            "qsTea accepts nothing at the x-height, so that exit is never a candidate"
        );
        assert_eq!(eliminations[0].stage, EliminationStage::LookaheadClosure);
    }

    #[test]
    fn a_committed_junction_binds_the_entry_or_eliminates_the_stance() {
        let index = alphabet();
        let mut engine = Engine::new(&index, no_features());
        let baseline = fixtures::sym(&index, "baseline");
        let tea = fixtures::sym(&index, "qsTea");
        let mut eliminations = Vec::new();
        let out = engine
            .candidates(
                &settled_left(&index, "qsPea", "half", Some("baseline")),
                tea,
                EDGE,
                EDGE,
                Some(&mut eliminations),
            )
            .expect("the fixture raises nothing");
        assert_eq!(
            out,
            vec![Candidate::non_joining(
                &index,
                tea,
                fixtures::sym(&index, "plain"),
                Some(baseline),
                0
            )]
        );
        assert!(eliminations.is_empty());

        let mut eliminations = Vec::new();
        let out = engine
            .candidates(
                &settled_left(&index, "qsPea", "half", Some("x-height")),
                tea,
                EDGE,
                EDGE,
                Some(&mut eliminations),
            )
            .expect("the fixture raises nothing");
        assert!(out.is_empty());
        assert_eq!(
            descriptions(&eliminations),
            ["qsTea.plain: no available entry row at x-height against the committed junction"]
        );
        assert_eq!(eliminations[0].stage, EliminationStage::EntryBinding);
    }

    #[test]
    fn an_unlock_grants_the_entry_its_feature_names_and_fires_saying_so() {
        let index = alphabet();
        let may = fixtures::sym(&index, "qsMay");
        let left = settled_left(&index, "qsPea", "half", Some("baseline"));

        let mut locked = Engine::new(&index, no_features());
        let mut eliminations = Vec::new();
        let out = locked
            .candidates(&left, may, EDGE, EDGE, Some(&mut eliminations))
            .expect("the fixture raises nothing");
        assert!(out.is_empty());
        assert_eq!(
            descriptions(&eliminations),
            ["qsMay.alt: no available entry row at baseline against the committed junction"]
        );
        assert!(locked.fired().is_empty());

        let mut unlocked = Engine::new(&index, [fixtures::sym(&index, "ss03")]);
        let out = unlocked
            .candidates(&left, may, EDGE, EDGE, None)
            .expect("the fixture raises nothing");
        assert_eq!(
            out,
            vec![Candidate::non_joining(
                &index,
                may,
                fixtures::sym(&index, "alt"),
                Some(fixtures::sym(&index, "baseline")),
                0
            )]
        );
        let pointers: Vec<String> = unlocked
            .fired()
            .iter()
            .map(|pointer| pointer.text(&index))
            .collect();
        assert_eq!(pointers, ["qsMay.yaml:stances.alt.unlocks[0]"]);
    }

    #[test]
    fn an_entry_unlock_names_its_feature_in_the_note_the_commit_carries() {
        let index = alphabet();
        let ss03 = fixtures::sym(&index, "ss03");
        let mut engine = Engine::new(&index, [ss03]);
        let rune = index
            .rune(fixtures::sym(&index, "qsMay"))
            .expect("qsMay is modeled");
        let stance = index.stance(
            index
                .stance_id(rune.name, fixtures::sym(&index, "alt"))
                .expect("qsMay declares alt"),
        );
        let (available, note) = engine
            .entry_available(
                rune,
                stance,
                fixtures::sym(&index, "baseline"),
                &LeftContext::boundary(TokenKind::Edge),
                EDGE,
                EDGE,
            )
            .expect("the fixture raises nothing");
        assert!(available);
        assert_eq!(note.as_deref(), Some("unlocked by ss03"));
    }

    /// `qsPea.half` enters at the baseline, exits at both heights, and carries the caller's `pairings`. `qsTea` enters at either height, so only the pairing rule can remove a candidate.
    fn pairing_spec(pairings: &str) -> SpecIndex {
        let pea = letter(
            "qsPea",
            &[stance(
                "half",
                &surface(
                    &object(&[row("baseline", &[])]),
                    &object(&[row("baseline", &[]), row("x-height", &[])]),
                    &[("pairings", pairings)],
                ),
            )],
            &plain_policy(),
        );
        let tea = letter(
            "qsTea",
            &[stance(
                "plain",
                &surface(
                    &object(&[row("baseline", &[]), row("x-height", &[])]),
                    "{}",
                    &[],
                ),
            )],
            &plain_policy(),
        );
        spec_of(&[pea, tea])
    }

    #[test]
    fn a_never_pairing_kills_only_the_combination_it_names() {
        let index =
            pairing_spec(r#"{"never":[{"entry":"baseline","exit":"x-height"}],"only":null}"#);
        let mut engine = Engine::new(&index, no_features());
        let mut eliminations = Vec::new();
        let out = engine
            .candidates(
                &settled_left(&index, "qsPea", "half", Some("baseline")),
                fixtures::sym(&index, "qsPea"),
                letter_token(&index, "qsTea"),
                EDGE,
                Some(&mut eliminations),
            )
            .expect("the fixture raises nothing");
        let half = fixtures::sym(&index, "half");
        let baseline = fixtures::sym(&index, "baseline");
        assert_eq!(
            out,
            vec![
                Candidate::joining(
                    &index,
                    fixtures::sym(&index, "qsPea"),
                    half,
                    Some(baseline),
                    baseline,
                    0,
                    0
                ),
                Candidate::non_joining(
                    &index,
                    fixtures::sym(&index, "qsPea"),
                    half,
                    Some(baseline),
                    0
                ),
            ]
        );
        assert_eq!(
            descriptions(&eliminations),
            ["qsPea.half: pairing (baseline, x-height) not allowed"]
        );
        assert_eq!(eliminations[0].stage, EliminationStage::Pairings);
    }

    #[test]
    fn an_only_list_closes_the_set_and_can_withdraw_the_non_joining_cell() {
        let index = pairing_spec(r#"{"never":[],"only":[{"entry":"baseline","exit":"baseline"}]}"#);
        let mut engine = Engine::new(&index, no_features());
        let mut eliminations = Vec::new();
        let out = engine
            .candidates(
                &settled_left(&index, "qsPea", "half", Some("baseline")),
                fixtures::sym(&index, "qsPea"),
                letter_token(&index, "qsTea"),
                EDGE,
                Some(&mut eliminations),
            )
            .expect("the fixture raises nothing");
        assert_eq!(
            out,
            vec![Candidate::joining(
                &index,
                fixtures::sym(&index, "qsPea"),
                fixtures::sym(&index, "half"),
                Some(fixtures::sym(&index, "baseline")),
                fixtures::sym(&index, "baseline"),
                0,
                0
            )]
        );
        assert_eq!(
            descriptions(&eliminations),
            [
                "qsPea.half: pairing (baseline, x-height) not allowed",
                "qsPea.half: pairing (baseline, none) not allowed",
            ]
        );
    }

    /// `qsPea` refuses to join any letter except `qsTea`. Both followers enter at the baseline, so the closure admits either one and only the refusal tells them apart.
    fn refusal_spec() -> SpecIndex {
        let carve = fixtures::condition(&[("family", &fixtures::names(&["qsTea"]))]);
        let right = fixtures::condition(&[
            ("is_token", "\"letter\""),
            ("except_", &fixtures::seq(&[carve.as_str()])),
        ]);
        let refuse = fixtures::record(&[
            ("kind", "\"refuse\""),
            ("when", &fixtures::when(&[("right", right.as_str())])),
            ("why", "\"the reach is unsupported\""),
            (
                "provenance",
                &fixtures::names(&["qsPea.yaml", "policy.refuse[0]"]),
            ),
        ]);
        let pea = letter(
            "qsPea",
            &[stance(
                "half",
                &surface("{}", &object(&[row("baseline", &[])]), &[]),
            )],
            &fixtures::policy(&[("refuse", &fixtures::seq(&[refuse.as_str()]))]),
        );
        let acceptor = |name: &str, stance_name: &str| {
            letter(
                name,
                &[stance(
                    stance_name,
                    &surface(&object(&[row("baseline", &[])]), "{}", &[]),
                )],
                &plain_policy(),
            )
        };
        spec_of(&[pea, acceptor("qsTea", "plain"), acceptor("qsIt", "solo")])
    }

    #[test]
    fn a_refusal_kills_the_joining_cell_and_its_except_carve_out_spares_it() {
        let index = refusal_spec();
        let mut engine = Engine::new(&index, no_features());
        let pea = fixtures::sym(&index, "qsPea");
        let half = fixtures::sym(&index, "half");
        let baseline = fixtures::sym(&index, "baseline");

        let mut eliminations = Vec::new();
        let out = engine
            .candidates(
                &LeftContext::boundary(TokenKind::Edge),
                pea,
                letter_token(&index, "qsIt"),
                EDGE,
                Some(&mut eliminations),
            )
            .expect("the fixture raises nothing");
        assert_eq!(
            out,
            vec![Candidate::non_joining(&index, pea, half, None, 0)],
            "a whole-join refusal never speaks to the non-joining cell"
        );
        assert_eq!(
            descriptions(&eliminations),
            ["qsPea.half: exit baseline refused \u{2014} the reach is unsupported"]
        );
        assert_eq!(eliminations[0].stage, EliminationStage::Refuse);
        assert_eq!(
            eliminations[0]
                .provenance
                .as_ref()
                .map(|provenance| Pointer::of(provenance).text(&index)),
            Some("qsPea.yaml:policy.refuse[0]".to_owned())
        );
        assert!(engine.fired().contains(&Pointer {
            file: fixtures::sym(&index, "qsPea.yaml"),
            path: fixtures::sym(&index, "policy.refuse[0]"),
        }));

        let mut eliminations = Vec::new();
        let out = engine
            .candidates(
                &LeftContext::boundary(TokenKind::Edge),
                pea,
                letter_token(&index, "qsTea"),
                EDGE,
                Some(&mut eliminations),
            )
            .expect("the fixture raises nothing");
        assert_eq!(
            out,
            vec![
                Candidate::joining(&index, pea, half, None, baseline, 0, 0),
                Candidate::non_joining(&index, pea, half, None, 0),
            ]
        );
        assert!(eliminations.is_empty());
    }

    /// `qsPea.half`'s baseline exit is scoped toward `qsTea` only, and carries provenance so a test can check whether it fired.
    fn row_scope_spec() -> SpecIndex {
        let scope = fixtures::condition(&[("family", &fixtures::names(&["qsTea"]))]);
        let exit = row(
            "baseline",
            &[
                ("scope", &fixtures::seq(&[scope.as_str()])),
                (
                    "provenance",
                    &fixtures::names(&["qsPea.yaml", "stances.half.exits.baseline"]),
                ),
            ],
        );
        let pea = letter(
            "qsPea",
            &[stance("half", &surface("{}", &object(&[exit]), &[]))],
            &plain_policy(),
        );
        let acceptor = |name: &str, stance_name: &str| {
            letter(
                name,
                &[stance(
                    stance_name,
                    &surface(&object(&[row("baseline", &[])]), "{}", &[]),
                )],
                &plain_policy(),
            )
        };
        spec_of(&[pea, acceptor("qsTea", "plain"), acceptor("qsIt", "solo")])
    }

    #[test]
    fn a_toward_scope_admits_only_the_followers_it_names() {
        let index = row_scope_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let pointer = Pointer {
            file: fixtures::sym(&index, "qsPea.yaml"),
            path: fixtures::sym(&index, "stances.half.exits.baseline"),
        };

        let mut refused = Engine::new(&index, no_features());
        let mut eliminations = Vec::new();
        let out = refused
            .candidates(
                &LeftContext::boundary(TokenKind::Edge),
                pea,
                letter_token(&index, "qsIt"),
                EDGE,
                Some(&mut eliminations),
            )
            .expect("the fixture raises nothing");
        assert_eq!(
            out,
            vec![Candidate::non_joining(
                &index,
                pea,
                fixtures::sym(&index, "half"),
                None,
                0
            )]
        );
        assert_eq!(
            descriptions(&eliminations),
            ["qsPea.half: exit baseline toward-scope does not admit qsIt"]
        );
        assert_eq!(eliminations[0].stage, EliminationStage::RowScope);
        assert!(
            !refused.fired().contains(&pointer),
            "a scope that admitted nothing did not fire"
        );

        let mut admitted = Engine::new(&index, no_features());
        admitted
            .candidates(
                &LeftContext::boundary(TokenKind::Edge),
                pea,
                letter_token(&index, "qsTea"),
                EDGE,
                None,
            )
            .expect("the fixture raises nothing");
        assert!(admitted.fired().contains(&pointer));
    }

    #[test]
    fn require_withholds_the_side_the_stance_says_it_cannot_do_without() {
        let index = spec_of(&[
            letter(
                "qsPea",
                &[
                    stance(
                        "half",
                        &surface(
                            &object(&[row("baseline", &[])]),
                            &object(&[row("baseline", &[])]),
                            &[("require", &fixtures::names(&["entry"]))],
                        ),
                    ),
                    stance(
                        "full",
                        &surface(
                            "{}",
                            &object(&[row("baseline", &[])]),
                            &[("require", &fixtures::names(&["exit"]))],
                        ),
                    ),
                ],
                &plain_policy(),
            ),
            letter(
                "qsTea",
                &[stance(
                    "plain",
                    &surface(&object(&[row("baseline", &[])]), "{}", &[]),
                )],
                &plain_policy(),
            ),
        ]);
        let mut engine = Engine::new(&index, no_features());
        let mut eliminations = Vec::new();
        let out = engine
            .candidates(
                &LeftContext::boundary(TokenKind::Edge),
                fixtures::sym(&index, "qsPea"),
                letter_token(&index, "qsTea"),
                EDGE,
                Some(&mut eliminations),
            )
            .expect("the fixture raises nothing");
        assert_eq!(
            out,
            vec![Candidate::joining(
                &index,
                fixtures::sym(&index, "qsPea"),
                fixtures::sym(&index, "full"),
                None,
                fixtures::sym(&index, "baseline"),
                1,
                0
            )],
            "half needs an entry it cannot have at the run edge, and full may not stand unjoined"
        );
        assert_eq!(
            descriptions(&eliminations),
            ["qsPea.half: requires a live entry"]
        );
        assert_eq!(eliminations[0].stage, EliminationStage::Require);
    }

    #[test]
    fn an_unlock_exit_is_placed_past_the_declared_rows_and_never_shadows_one() {
        let index = spec_of(&[
            letter(
                "qsPea",
                &[stance(
                    "half",
                    &surface(
                        "{}",
                        &object(&[row("baseline", &[])]),
                        &[(
                            "unlocks",
                            &fixtures::seq(&[
                                r#"{"feature":"ss03","entry":null,"exit":"x-height","pairing":null,"when":null,"why":null,"provenance":["qsPea.yaml","stances.half.unlocks[0]"]}"#,
                                r#"{"feature":"ss03","entry":null,"exit":"baseline","pairing":null,"when":null,"why":null,"provenance":["qsPea.yaml","stances.half.unlocks[1]"]}"#,
                            ]),
                        )],
                    ),
                )],
                &plain_policy(),
            ),
            letter(
                "qsTea",
                &[stance(
                    "plain",
                    &surface(
                        &object(&[row("baseline", &[]), row("x-height", &[])]),
                        "{}",
                        &[],
                    ),
                )],
                &plain_policy(),
            ),
        ]);
        let pea = fixtures::sym(&index, "qsPea");
        let half = fixtures::sym(&index, "half");
        let baseline = fixtures::sym(&index, "baseline");
        let x_height = fixtures::sym(&index, "x-height");
        let tea = letter_token(&index, "qsTea");

        let mut locked = Engine::new(&index, no_features());
        let out = locked
            .candidates(
                &LeftContext::boundary(TokenKind::Edge),
                pea,
                tea,
                EDGE,
                None,
            )
            .expect("the fixture raises nothing");
        assert_eq!(
            out,
            vec![
                Candidate::joining(&index, pea, half, None, baseline, 0, 0),
                Candidate::non_joining(&index, pea, half, None, 0),
            ]
        );

        let mut unlocked = Engine::new(&index, [fixtures::sym(&index, "ss03")]);
        let out = unlocked
            .candidates(
                &LeftContext::boundary(TokenKind::Edge),
                pea,
                tea,
                EDGE,
                None,
            )
            .expect("the fixture raises nothing");
        assert_eq!(
            out,
            vec![
                Candidate::joining(&index, pea, half, None, baseline, 0, 0),
                Candidate::joining(&index, pea, half, None, x_height, 0, 1),
                Candidate::non_joining(&index, pea, half, None, 0),
            ],
            "the granted x-height sits past the declared row; the baseline unlock is shadowed"
        );
        let pointers: Vec<String> = {
            let mut texts: Vec<String> = unlocked
                .fired()
                .iter()
                .map(|pointer| pointer.text(&index))
                .collect();
            texts.sort();
            texts
        };
        assert_eq!(pointers, ["qsPea.yaml:stances.half.unlocks[0]"]);
    }

    #[test]
    fn a_left_condition_carrying_then_is_a_spec_defect_with_pythons_sentence() {
        let index = alphabet();
        let engine = Engine::new(&index, no_features());
        let text = fixtures::condition(&[("then", &fixtures::condition(&[]))]);
        let spec = fixtures::index_of(&fixtures::dump(
            &object(&[letter(
                "qsPea",
                &[stance("half", &surface("{}", "{}", &[]))],
                &fixtures::policy(&[(
                    "refuse",
                    &fixtures::seq(&[&fixtures::record(&[(
                        "when",
                        &fixtures::when(&[("left", text.as_str())]),
                    )])]),
                )]),
            )]),
            &fixtures::four_family_registry(),
        ));
        let cond = spec
            .rune(fixtures::sym(&spec, "qsPea"))
            .expect("qsPea is modeled")
            .policy
            .refuse[0]
            .when
            .left
            .as_ref()
            .expect("the fixture spells a left condition");
        let error = engine
            .cond_matches_left(None, cond, &LeftContext::boundary(TokenKind::Edge), None)
            .expect_err("a left condition may not reach into the window");
        assert_eq!(
            error.message(),
            "left conditions cannot carry then: (window depth, design section 3.4)"
        );
    }

    #[test]
    fn a_right_condition_carrying_a_left_only_axis_is_a_spec_defect() {
        let spec = fixtures::index_of(&fixtures::dump(
            &object(&[letter(
                "qsPea",
                &[stance("half", &surface("{}", "{}", &[]))],
                &fixtures::policy(&[(
                    "refuse",
                    &fixtures::seq(&[&fixtures::record(&[(
                        "when",
                        &fixtures::when(&[(
                            "right",
                            &fixtures::condition(&[("stance", &fixtures::names(&["half"]))]),
                        )]),
                    )])]),
                )]),
            )]),
            &fixtures::four_family_registry(),
        ));
        let engine = Engine::new(&spec, no_features());
        let cond = spec
            .rune(fixtures::sym(&spec, "qsPea"))
            .expect("qsPea is modeled")
            .policy
            .refuse[0]
            .when
            .right
            .as_ref()
            .expect("the fixture spells a right condition");
        let error = engine
            .cond_matches_right(None, cond, &[EDGE, EDGE])
            .expect_err("stance is a left-only axis");
        assert_eq!(
            error.message(),
            "right conditions are raw: stance/joined_at are left-only axes (design section 3.4)"
        );
    }

    /// Four conditions placed on refuse records so a test can read them as parsed `Condition`s. `qsPea` has a horizontal entry and a rising exit, and `qsTea`'s only entry row is unselectable.
    ///
    /// No rune condition uses `stroke:`, or `is:` with a value other than `boundary`, so no sweep over the live alphabet reaches these branches. These tests are their only coverage.
    fn axis_spec() -> SpecIndex {
        let pea = letter(
            "qsPea",
            &[stance(
                "half",
                &surface(
                    &object(&[row("baseline", &[("stroke", "\"horizontal\"")])]),
                    &object(&[row("baseline", &[("stroke", "\"rising\"")])]),
                    &[],
                ),
            )],
            &fixtures::policy(&[(
                "refuse",
                &fixtures::seq(&[
                    &fixtures::record(&[(
                        "when",
                        &fixtures::when(&[(
                            "left",
                            &fixtures::condition(&[("stroke", "\"rising\"")]),
                        )]),
                    )]),
                    &fixtures::record(&[(
                        "when",
                        &fixtures::when(&[(
                            "right",
                            &fixtures::condition(&[("stroke", "\"horizontal\"")]),
                        )]),
                    )]),
                    &fixtures::record(&[(
                        "when",
                        &fixtures::when(&[(
                            "left",
                            &fixtures::condition(&[("is_token", "\"zwnj\"")]),
                        )]),
                    )]),
                    &fixtures::record(&[(
                        "when",
                        &fixtures::when(&[(
                            "right",
                            &fixtures::condition(&[("is_token", "\"boundary\"")]),
                        )]),
                    )]),
                ]),
            )]),
        );
        let tea = letter(
            "qsTea",
            &[stance(
                "plain",
                &surface(
                    &object(&[row(
                        "baseline",
                        &[("stroke", "\"falling\""), ("selectable", "false")],
                    )]),
                    "{}",
                    &[],
                ),
            )],
            &plain_policy(),
        );
        spec_of(&[pea, tea])
    }

    #[test]
    fn a_stroke_axis_reads_the_lefts_committed_exit_row_and_the_rights_selectable_entries() {
        let index = axis_spec();
        let engine = Engine::new(&index, no_features());
        let refusals = &index
            .rune(fixtures::sym(&index, "qsPea"))
            .expect("qsPea is modeled")
            .policy
            .refuse;
        let baseline = fixtures::sym(&index, "baseline");
        let on_left = refusals[0].when.left.as_ref().expect("a left condition");
        let on_right = refusals[1].when.right.as_ref().expect("a right condition");

        assert_eq!(
            engine.cond_matches_left(
                None,
                on_left,
                &settled_left(&index, "qsPea", "half", Some("baseline")),
                Some(baseline)
            ),
            Ok(true)
        );
        assert_eq!(
            engine.cond_matches_left(
                None,
                on_left,
                &settled_left(&index, "qsTea", "plain", Some("baseline")),
                Some(baseline)
            ),
            Ok(false),
            "qsTea.plain declares no exit row, so it has no exit stroke to match"
        );
        assert_eq!(
            engine.cond_matches_left(
                None,
                on_left,
                &settled_left(&index, "qsPea", "half", None),
                None
            ),
            Ok(false),
            "an unjoined left committed no junction and so exits at no stroke"
        );

        assert_eq!(
            engine.cond_matches_right(None, on_right, &[letter_token(&index, "qsPea")]),
            Ok(Some(true))
        );
        assert_eq!(
            engine.cond_matches_right(None, on_right, &[letter_token(&index, "qsTea")]),
            Ok(Some(false)),
            "an unselectable entry row offers no stroke"
        );
        assert_eq!(
            engine.cond_matches_right(None, on_right, &[EDGE]),
            Ok(Some(false))
        );
        assert_eq!(
            engine.cond_matches_right(None, on_right, &[UNKNOWN]),
            Ok(None)
        );
    }

    #[test]
    fn an_is_axis_names_one_kind_or_expands_to_every_boundary() {
        let index = axis_spec();
        let engine = Engine::new(&index, no_features());
        let refusals = &index
            .rune(fixtures::sym(&index, "qsPea"))
            .expect("qsPea is modeled")
            .policy
            .refuse;
        let named = refusals[2].when.left.as_ref().expect("a left condition");
        let boundary = refusals[3].when.right.as_ref().expect("a right condition");

        assert_eq!(
            engine.cond_matches_left(None, named, &LeftContext::boundary(TokenKind::Zwnj), None),
            Ok(true)
        );
        assert_eq!(
            engine.cond_matches_left(None, named, &LeftContext::boundary(TokenKind::Space), None),
            Ok(false)
        );
        assert_eq!(
            engine.cond_matches_left(
                None,
                named,
                &settled_left(&index, "qsPea", "half", None),
                None
            ),
            Ok(false)
        );

        for kind in [
            TokenKind::Edge,
            TokenKind::Space,
            TokenKind::Zwnj,
            TokenKind::NamerDot,
        ] {
            let token = RightToken::of_kind(kind).expect("a boundary token");
            assert_eq!(
                engine.cond_matches_right(None, boundary, &[token]),
                Ok(Some(true)),
                "is: boundary expands to every boundary kind"
            );
        }
        assert_eq!(
            engine.cond_matches_right(None, boundary, &[letter_token(&index, "qsPea")]),
            Ok(Some(false))
        );
        assert_eq!(
            engine.cond_matches_right(None, boundary, &[UNKNOWN]),
            Ok(None),
            "a slot outside the window is neither a boundary nor a letter yet"
        );
    }

    #[test]
    fn a_right_chain_reads_one_raw_slot_per_hop_and_exhausts_to_unknown() {
        let spec = fixtures::index_of(&fixtures::dump(
            &object(&[letter(
                "qsPea",
                &[stance("half", &surface("{}", "{}", &[]))],
                &fixtures::policy(&[(
                    "refuse",
                    &fixtures::seq(&[&fixtures::record(&[(
                        "when",
                        &fixtures::when(&[(
                            "right",
                            &fixtures::condition(&[
                                ("is_token", "\"letter\""),
                                ("then", &fixtures::condition(&[("is_token", "\"letter\"")])),
                            ]),
                        )]),
                    )])]),
                )]),
            )]),
            &fixtures::four_family_registry(),
        ));
        let engine = Engine::new(&spec, no_features());
        let cond = spec
            .rune(fixtures::sym(&spec, "qsPea"))
            .expect("qsPea is modeled")
            .policy
            .refuse[0]
            .when
            .right
            .as_ref()
            .expect("the fixture spells a right condition");
        let pea = letter_token(&spec, "qsPea");
        assert_eq!(
            engine.cond_matches_right(None, cond, &[pea, pea]),
            Ok(Some(true))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[pea, SPACE]),
            Ok(Some(false))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[pea]),
            Ok(None),
            "the hop past the supplied window exhausts to UNKNOWN and the verdict is unknown"
        );
        assert_eq!(engine.cond_matches_right(None, cond, &[UNKNOWN]), Ok(None));
    }

    #[test]
    fn a_when_answers_false_definitely_and_unknown_only_where_the_window_ends() {
        let spec = fixtures::index_of(&fixtures::dump(
            &object(&[letter(
                "qsPea",
                &[stance("half", &surface("{}", "{}", &[]))],
                &fixtures::policy(&[(
                    "refuse",
                    &fixtures::seq(&[
                        &fixtures::record(&[("when", &fixtures::when(&[("feature", "\"ss03\"")]))]),
                        &fixtures::record(&[(
                            "when",
                            &fixtures::when(&[("self_entry", "\"live\"")]),
                        )]),
                        &fixtures::record(&[("when", &fixtures::when(&[("word", "\"initial\"")]))]),
                    ]),
                )]),
            )]),
            &fixtures::four_family_registry(),
        ));
        let refusals = &spec
            .rune(fixtures::sym(&spec, "qsPea"))
            .expect("qsPea is modeled")
            .policy
            .refuse;
        let edge = LeftContext::boundary(TokenKind::Edge);
        let baseline = fixtures::sym(&spec, "baseline");
        let pea = letter_token(&spec, "qsPea");

        let plain = Engine::new(&spec, no_features());
        let featured = Engine::new(&spec, [fixtures::sym(&spec, "ss03")]);
        let gate = |engine: &Engine<'_>, position: usize, entry: Option<Sym>, slots: Slots| {
            engine
                .when_matches(None, &refusals[position].when, &edge, entry, None, slots)
                .expect("the fixture raises nothing")
        };
        assert_eq!(gate(&plain, 0, None, Slots::pair(EDGE, EDGE)), Some(false));
        assert_eq!(
            gate(&featured, 0, None, Slots::pair(EDGE, EDGE)),
            Some(true)
        );
        assert_eq!(gate(&plain, 1, None, Slots::pair(EDGE, EDGE)), Some(false));
        assert_eq!(
            gate(&plain, 1, Some(baseline), Slots::pair(EDGE, EDGE)),
            Some(true)
        );
        assert_eq!(gate(&plain, 2, None, Slots::pair(pea, EDGE)), Some(true));
        assert_eq!(gate(&plain, 2, None, Slots::pair(EDGE, EDGE)), Some(false));
        assert_eq!(
            gate(&plain, 2, None, Slots::pair(UNKNOWN, EDGE)),
            None,
            "word position is undecidable while the slot past us is outside the window"
        );
    }

    /// Four refusals over a three-hop and a four-hop family chain. Refusals 0 and 2 put the chain on the condition itself, and refusals 1 and 3 put it inside an `except_` entry. `qsPea`'s surface is empty because no test here settles a window: each reads a condition off the policy and matches it against slots it builds by hand.
    fn deep_chain_spec() -> SpecIndex {
        let tea = fixtures::names(&["qsTea"]);
        let may = fixtures::names(&["qsMay"]);
        let it = fixtures::names(&["qsIt"]);
        let tea_or_it = fixtures::names(&["qsTea", "qsIt"]);
        let three_hop = fixtures::condition(&[
            ("family", tea.as_str()),
            (
                "then",
                &fixtures::condition(&[
                    ("family", may.as_str()),
                    ("then", &fixtures::condition(&[("family", it.as_str())])),
                ]),
            ),
        ]);
        let four_hop = fixtures::condition(&[
            ("family", tea.as_str()),
            (
                "then",
                &fixtures::condition(&[
                    ("family", may.as_str()),
                    (
                        "then",
                        &fixtures::condition(&[
                            ("family", it.as_str()),
                            ("then", &fixtures::condition(&[("family", tea.as_str())])),
                        ]),
                    ),
                ]),
            ),
        ]);
        let carved = |chain: &str| {
            fixtures::condition(&[
                ("family", tea_or_it.as_str()),
                ("except_", &fixtures::seq(&[chain])),
            ])
        };
        let refusals = fixtures::seq(&[
            &fixtures::record(&[("when", &fixtures::when(&[("right", three_hop.as_str())]))]),
            &fixtures::record(&[(
                "when",
                &fixtures::when(&[("right", carved(&three_hop).as_str())]),
            )]),
            &fixtures::record(&[("when", &fixtures::when(&[("right", four_hop.as_str())]))]),
            &fixtures::record(&[(
                "when",
                &fixtures::when(&[("right", carved(&four_hop).as_str())]),
            )]),
        ]);
        spec_of(&[letter(
            "qsPea",
            &[stance("half", &surface("{}", "{}", &[]))],
            &fixtures::policy(&[("refuse", refusals.as_str())]),
        )])
    }

    /// The `when:` of one [`deep_chain_spec`] refusal.
    fn chain_when(index: &SpecIndex, position: usize) -> &When {
        &index
            .rune(fixtures::sym(index, "qsPea"))
            .expect("qsPea is modeled")
            .policy
            .refuse[position]
            .when
    }

    /// The right condition of one [`deep_chain_spec`] refusal.
    fn chain_condition(index: &SpecIndex, position: usize) -> &Condition {
        chain_when(index, position)
            .right
            .as_ref()
            .expect("every deep-chain refusal spells a right condition")
    }

    #[test]
    fn a_three_hop_chain_reads_three_raw_slots_and_exhausts_to_unknown() {
        let index = deep_chain_spec();
        let engine = Engine::new(&index, no_features());
        let cond = chain_condition(&index, 0);
        let tea = letter_token(&index, "qsTea");
        let may = letter_token(&index, "qsMay");
        let it = letter_token(&index, "qsIt");
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, it]),
            Ok(Some(true))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, tea]),
            Ok(Some(false)),
            "the last hop is refuted inside the window, so the verdict is definite"
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, it, it]),
            Ok(Some(false))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[may, may, it]),
            Ok(Some(false))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, UNKNOWN]),
            Ok(None)
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may]),
            Ok(None),
            "a hop past the supplied slots reads the window's edge, not a mismatch"
        );
        assert_eq!(engine.cond_matches_right(None, cond, &[tea]), Ok(None));
    }

    #[test]
    fn an_except_entry_carrying_a_chain_walks_the_same_tail() {
        let index = deep_chain_spec();
        let engine = Engine::new(&index, no_features());
        let cond = chain_condition(&index, 1);
        let tea = letter_token(&index, "qsTea");
        let may = letter_token(&index, "qsMay");
        let it = letter_token(&index, "qsIt");
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, it]),
            Ok(Some(false)),
            "the carve-out's own chain matches, so the condition it hangs off does not"
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, tea]),
            Ok(Some(true))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, tea, it]),
            Ok(Some(true))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[it, may, it]),
            Ok(Some(true)),
            "the carve-out tests its parent's slot too, and qsIt is not the family it names"
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[may, may, it]),
            Ok(Some(false))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, UNKNOWN]),
            Ok(None)
        );
        assert_eq!(engine.cond_matches_right(None, cond, &[tea]), Ok(None));
    }

    #[test]
    fn a_four_hop_chain_reads_four_raw_slots() {
        let index = deep_chain_spec();
        let engine = Engine::new(&index, no_features());
        let cond = chain_condition(&index, 2);
        let tea = letter_token(&index, "qsTea");
        let may = letter_token(&index, "qsMay");
        let it = letter_token(&index, "qsIt");
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, it, tea]),
            Ok(Some(true))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, it, may]),
            Ok(Some(false))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, tea, tea]),
            Ok(Some(false)),
            "a hop refuted mid-chain answers false without reading the slots behind it"
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, it, UNKNOWN]),
            Ok(None)
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, it]),
            Ok(None)
        );
    }

    #[test]
    fn an_except_entry_carrying_a_four_hop_chain_walks_the_same_tail() {
        let index = deep_chain_spec();
        let engine = Engine::new(&index, no_features());
        let cond = chain_condition(&index, 3);
        let tea = letter_token(&index, "qsTea");
        let may = letter_token(&index, "qsMay");
        let it = letter_token(&index, "qsIt");
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, it, tea]),
            Ok(Some(false))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, it, may]),
            Ok(Some(true))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, tea, tea]),
            Ok(Some(true))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[may, may, it, tea]),
            Ok(Some(false))
        );
        assert_eq!(
            engine.cond_matches_right(None, cond, &[tea, may, it, UNKNOWN]),
            Ok(None)
        );
    }

    #[test]
    fn a_when_gate_carries_a_deep_chains_unknown_out_of_the_window() {
        let index = deep_chain_spec();
        let engine = Engine::new(&index, no_features());
        let edge = LeftContext::boundary(TokenKind::Edge);
        let tea = letter_token(&index, "qsTea");
        let may = letter_token(&index, "qsMay");
        let it = letter_token(&index, "qsIt");
        let gate = |position: usize, slots: Slots| {
            engine
                .when_matches(None, chain_when(&index, position), &edge, None, None, slots)
                .expect("the fixture raises nothing")
        };
        assert_eq!(gate(0, Slots::new(tea, may, it, UNKNOWN)), Some(true));
        assert_eq!(gate(0, Slots::new(tea, may, tea, UNKNOWN)), Some(false));
        assert_eq!(
            gate(0, Slots::pair(tea, may)),
            None,
            "the third hop reads the deep slot the two-slot window leaves at UNKNOWN"
        );
        assert_eq!(gate(2, Slots::new(tea, may, it, tea)), Some(true));
        assert_eq!(gate(2, Slots::new(tea, may, it, may)), Some(false));
        assert_eq!(
            gate(2, Slots::new(tea, may, it, UNKNOWN)),
            None,
            "the fourth hop runs past the window's end, and the gate carries that out"
        );
    }

    #[test]
    fn the_journal_dedups_on_first_firing_and_empties_when_the_outermost_capture_closes() {
        let index = alphabet();
        let mut engine = Engine::with_modes(
            &index,
            no_features(),
            EngineModes {
                trace_memo: true,
                ..EngineModes::default()
            },
        );
        let file = fixtures::sym(&index, "qsPea");
        let one = Pointer {
            file,
            path: fixtures::sym(&index, "half"),
        };
        let two = Pointer {
            file,
            path: fixtures::sym(&index, "full"),
        };
        let three = Pointer {
            file,
            path: fixtures::sym(&index, "baseline"),
        };

        engine.record_pointer(one);
        assert!(
            engine
                .fired_log
                .as_ref()
                .expect("trace-memo journals")
                .is_empty(),
            "a firing outside every capture is remembered but not journaled"
        );
        assert!(engine.fired().contains(&one));

        engine.begin_capture();
        engine.record_pointer(one);
        engine.begin_capture();
        engine.record_pointer(two);
        engine.record_pointer(one);
        assert_eq!(*engine.end_capture().delta, *vec![two, one]);
        engine.record_pointer(three);
        assert_eq!(*engine.end_capture().delta, *vec![one, two, three]);
        assert!(
            engine
                .fired_log
                .as_ref()
                .expect("trace-memo journals")
                .is_empty()
        );
    }

    #[test]
    fn an_aborted_capture_records_nothing_but_leaves_its_firings_to_the_enclosing_one() {
        let index = alphabet();
        let mut engine = Engine::with_modes(
            &index,
            no_features(),
            EngineModes {
                trace_memo: true,
                ..EngineModes::default()
            },
        );
        let file = fixtures::sym(&index, "qsPea");
        let one = Pointer {
            file,
            path: fixtures::sym(&index, "half"),
        };
        let two = Pointer {
            file,
            path: fixtures::sym(&index, "full"),
        };
        engine.begin_capture();
        engine.record_pointer(one);
        engine.begin_capture();
        engine.record_pointer(two);
        engine.abort_capture();
        assert_eq!(*engine.end_capture().delta, *vec![one, two]);
    }

    #[test]
    fn only_a_trace_memo_engine_memoizes_an_enumeration_and_a_hit_replays_its_delta() {
        let index = alphabet();
        let may = fixtures::sym(&index, "qsMay");
        let ss03 = fixtures::sym(&index, "ss03");
        let left = settled_left(&index, "qsPea", "half", Some("baseline"));
        let unlock = Pointer {
            file: fixtures::sym(&index, "qsMay.yaml"),
            path: fixtures::sym(&index, "stances.alt.unlocks[0]"),
        };

        let mut plain = Engine::new(&index, [ss03]);
        plain
            .candidates(&left, may, EDGE, EDGE, None)
            .expect("the fixture raises nothing");
        assert!(
            plain.candidates_cache.entries.is_empty(),
            "outside trace-memo mode there is no journal, so an entry could carry no delta to replay"
        );

        let mut memoized = Engine::with_modes(
            &index,
            [ss03],
            EngineModes {
                trace_memo: true,
                ..EngineModes::default()
            },
        );
        let first = memoized
            .candidates(&left, may, EDGE, EDGE, None)
            .expect("the fixture raises nothing");
        assert_eq!(memoized.candidates_cache.entries.len(), 1);
        let entry = *memoized
            .candidates_cache
            .entries
            .get(&Engine::candidates_key(&index, &left, may, EDGE, EDGE))
            .expect("the window this test enumerated is memoized");
        assert_eq!(memoized.deltas.get(entry.delta), [unlock]);
        assert!(
            memoized.fired().contains(&unlock),
            "the first evaluation journaled the unlock into the set"
        );
        memoized.begin_capture();
        let again = memoized
            .candidates(&left, may, EDGE, EDGE, None)
            .expect("the fixture raises nothing");
        assert_eq!(again, first);
        assert_eq!(
            &*memoized.end_capture().delta,
            [unlock],
            "the hit replayed the delta its first evaluation journaled into the open capture"
        );
    }

    /// One stance in which every capability check fires: a scoped entry row, a pairing unlock, an exit unlock, and a scoped exit row. Each carries provenance so a test can read the order of the journal.
    fn firing_spec() -> SpecIndex {
        let toward_tea = fixtures::condition(&[("family", &fixtures::names(&["qsTea"]))]);
        let entry = row(
            "baseline",
            &[
                ("scope", &fixtures::seq(&[toward_tea.as_str()])),
                (
                    "provenance",
                    &fixtures::names(&["qsPea.yaml", "stances.half.entries.baseline"]),
                ),
            ],
        );
        let exit = row(
            "baseline",
            &[
                ("scope", &fixtures::seq(&[toward_tea.as_str()])),
                (
                    "provenance",
                    &fixtures::names(&["qsPea.yaml", "stances.half.exits.baseline"]),
                ),
            ],
        );
        let pea = letter(
            "qsPea",
            &[stance(
                "half",
                &surface(
                    &object(&[entry]),
                    &object(&[exit]),
                    &[
                        (
                            "pairings",
                            r#"{"never":[],"only":[{"entry":"baseline","exit":"baseline"}]}"#,
                        ),
                        (
                            "unlocks",
                            &fixtures::seq(&[
                                r#"{"feature":"ss03","entry":null,"exit":null,"pairing":{"entry":"baseline","exit":"x-height"},"when":null,"why":null,"provenance":["qsPea.yaml","stances.half.unlocks[0]"]}"#,
                                r#"{"feature":"ss03","entry":null,"exit":"x-height","pairing":null,"when":null,"why":null,"provenance":["qsPea.yaml","stances.half.unlocks[1]"]}"#,
                            ]),
                        ),
                    ],
                ),
            )],
            &plain_policy(),
        );
        let tea = letter(
            "qsTea",
            &[stance(
                "plain",
                &surface(
                    &object(&[row("baseline", &[]), row("x-height", &[])]),
                    "{}",
                    &[],
                ),
            )],
            &plain_policy(),
        );
        spec_of(&[pea, tea])
    }

    #[test]
    fn the_journaled_delta_keeps_the_order_the_enumeration_fired_in() {
        let index = firing_spec();
        let mut engine = Engine::with_modes(
            &index,
            [fixtures::sym(&index, "ss03")],
            EngineModes {
                trace_memo: true,
                ..EngineModes::default()
            },
        );
        let half = fixtures::sym(&index, "half");
        let baseline = fixtures::sym(&index, "baseline");
        let x_height = fixtures::sym(&index, "x-height");
        let mut eliminations = Vec::new();
        let out = engine
            .candidates(
                &settled_left(&index, "qsTea", "plain", Some("baseline")),
                fixtures::sym(&index, "qsPea"),
                letter_token(&index, "qsTea"),
                EDGE,
                Some(&mut eliminations),
            )
            .expect("the fixture raises nothing");
        assert_eq!(
            out,
            vec![
                Candidate::joining(
                    &index,
                    fixtures::sym(&index, "qsPea"),
                    half,
                    Some(baseline),
                    baseline,
                    0,
                    0
                ),
                Candidate::joining(
                    &index,
                    fixtures::sym(&index, "qsPea"),
                    half,
                    Some(baseline),
                    x_height,
                    0,
                    1
                ),
            ]
        );
        assert_eq!(
            descriptions(&eliminations),
            ["qsPea.half: pairing (baseline, none) not allowed"]
        );
        let entry = *engine
            .candidates_cache
            .entries
            .get(&Engine::candidates_key(
                &index,
                &settled_left(&index, "qsTea", "plain", Some("baseline")),
                fixtures::sym(&index, "qsPea"),
                letter_token(&index, "qsTea"),
                EDGE,
            ))
            .expect("the window this test enumerated is memoized");
        let delta: Vec<String> = engine
            .deltas
            .get(entry.delta)
            .iter()
            .map(|pointer| pointer.text(&index))
            .collect();
        assert_eq!(
            delta,
            [
                "qsPea.yaml:stances.half.entries.baseline",
                "qsPea.yaml:stances.half.unlocks[0]",
                "qsPea.yaml:stances.half.unlocks[1]",
                "qsPea.yaml:stances.half.exits.baseline",
            ],
            "the entry's from-scope, the pairing unlock, the exit unlock, and the exit's toward-scope, in the order the enumeration consults them"
        );
    }

    #[test]
    fn a_warm_engine_fires_exactly_what_a_cold_one_fires() {
        let index = alphabet();
        let ss03 = fixtures::sym(&index, "ss03");
        let modes = EngineModes {
            trace_memo: true,
            ..EngineModes::default()
        };
        let pea = fixtures::sym(&index, "qsPea");
        let may = fixtures::sym(&index, "qsMay");
        let tea = letter_token(&index, "qsTea");
        let left = settled_left(&index, "qsPea", "half", Some("baseline"));

        let mut cold = Engine::with_modes(&index, [ss03], modes);
        cold.candidates(&left, may, EDGE, EDGE, None)
            .expect("the fixture raises nothing");

        let mut warm = Engine::with_modes(&index, [ss03], modes);
        warm.candidates(
            &LeftContext::boundary(TokenKind::Edge),
            pea,
            tea,
            EDGE,
            None,
        )
        .expect("the fixture raises nothing");
        warm.fired.clear();
        warm.candidates(&left, may, EDGE, EDGE, None)
            .expect("the fixture raises nothing");
        assert_eq!(warm.fired(), cold.fired());
    }

    #[test]
    fn a_trace_delta_is_absent_until_a_window_has_been_traced() {
        let index = alphabet();
        let engine = Engine::with_modes(
            &index,
            no_features(),
            EngineModes {
                trace_memo: true,
                ..EngineModes::default()
            },
        );
        assert!(engine.trace_memo());
        assert_eq!(
            engine.trace_delta(
                &LeftContext::boundary(TokenKind::Edge),
                letter_token(&index, "qsPea"),
                Slots::pair(EDGE, EDGE)
            ),
            None
        );
        assert_eq!(
            engine.trace_delta(
                &LeftContext::boundary(TokenKind::Edge),
                SPACE,
                Slots::pair(EDGE, EDGE)
            ),
            None,
            "a non-letter input never reaches the memo, so it has no delta rather than a bad key"
        );
        assert!(!Engine::new(&index, no_features()).trace_memo());
    }

    /// A trace memo entry is two two-byte indexes, two four-byte indexes, and one byte packing the prospect, the joint flag, and the stage: sixteen bytes with no heap allocation. Its key is twenty bytes. [`TraceEntry`] and [`TraceKey`] say why the sizes matter.
    #[test]
    fn a_memoized_window_is_sixteen_bytes_under_a_twenty_byte_key() {
        assert_eq!(std::mem::size_of::<TraceEntry>(), 16);
        assert_eq!(std::mem::size_of::<TraceKey>(), 20);
    }

    /// The packed byte reads back every combination of prospect, joint flag, and stage.
    #[test]
    fn a_packed_entry_reads_back_every_prospect_joint_flag_and_stage() {
        for prospect in [0i64, 1] {
            for joint_tiebreak in [false, true] {
                for stage in DecidedStage::ALL {
                    let entry = TraceEntry::new(
                        TraceSettledId::at(3),
                        TraceNotesId::at(5),
                        DeltaId::at(7),
                        ReadsId::at(11),
                        prospect,
                        joint_tiebreak,
                        stage,
                    );
                    assert_eq!(
                        (
                            entry.settled.index(),
                            entry.notes.index(),
                            i64::from(entry.prospect()),
                            entry.joint_tiebreak(),
                            entry.decided_stage()
                        ),
                        (3, 5, prospect, joint_tiebreak, stage)
                    );
                }
            }
        }
    }

    /// A two-byte index covers every pool index below its `CAPACITY`. The last one widens to the pool's own id, and `try_at` returns `None` for the first index past the range instead of wrapping to a low id.
    #[test]
    fn a_trace_id_covers_its_range_and_wraps_past_none_of_it() {
        let last = TraceSettledId::CAPACITY - 1;
        assert_eq!(TraceSettledId::at(last).widen(), SettledId::at(last));
        assert_eq!(TraceNotesId::at(last).widen(), NotesId::at(last));
        assert_eq!(TraceSettledId::at(0).index(), 0);
        assert_eq!(TraceNotesId::at(0).index(), 0);
        assert!(TraceSettledId::try_at(TraceSettledId::CAPACITY).is_none());
        assert!(TraceNotesId::try_at(TraceNotesId::CAPACITY).is_none());
        assert!(TraceSettledId::try_at(usize::MAX).is_none());
    }

    /// `TraceSettledId::at` panics past the `u16` range instead of wrapping.
    #[test]
    #[should_panic(expected = "fewer than 65,536 distinct settled records")]
    fn a_settled_table_past_the_range_raises_at_the_mint() {
        let _ = TraceSettledId::at(TraceSettledId::CAPACITY);
    }

    /// `TraceNotesId::at` panics past the `u16` range instead of wrapping.
    #[test]
    #[should_panic(expected = "fewer than 65,536 distinct notes lists")]
    fn a_notes_table_past_the_range_raises_at_the_mint() {
        let _ = TraceNotesId::at(TraceNotesId::CAPACITY);
    }

    /// `TraceEntry::new` panics on a prospect other than zero or one instead of truncating it into the bit.
    #[test]
    #[should_panic(expected = "a prospect is a junction count, zero or one")]
    fn a_prospect_past_one_raises_at_the_entry() {
        let _ = TraceEntry::new(
            TraceSettledId::at(0),
            TraceNotesId::at(0),
            DeltaId::at(0),
            ReadsId::at(0),
            2,
            false,
            DecidedStage::Order,
        );
    }

    /// A candidate memo entry is four ids in sixteen bytes with no heap allocation, under a fourteen-byte key. A closure memo value is its result and two ids in twelve bytes.
    #[test]
    fn a_memoized_enumeration_is_sixteen_bytes_under_a_fourteen_byte_key() {
        assert_eq!(std::mem::size_of::<CandidatesEntry>(), 16);
        assert_eq!(std::mem::size_of::<CandidatesKey>(), 14);
        assert_eq!(std::mem::size_of::<(bool, DeltaId, ReadsId)>(), 12);
    }

    /// Two trace keys with the same ordinals that differ only in one slot's kind, or only in the left's kind, compare unequal and hash differently.
    #[test]
    fn two_keys_differing_in_one_packed_kind_stay_distinct_and_hash_apart() {
        use std::hash::BuildHasher as _;
        let index = fixtures::mini();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let hasher = std::hash::BuildHasherDefault::<crate::hash::FastHasher>::default();
        let base = TraceKey::for_test(&index, pea, [Some(tea), None, None, None]);
        let mut third_space = base;
        third_space.kinds = PackedKinds::of(&[
            TokenKind::Edge,
            TokenKind::Letter,
            TokenKind::Edge,
            TokenKind::Space,
            TokenKind::Edge,
        ]);
        let mut left_space = base;
        left_space.kinds = PackedKinds::of(&[
            TokenKind::Space,
            TokenKind::Letter,
            TokenKind::Edge,
            TokenKind::Edge,
            TokenKind::Edge,
        ]);
        assert_eq!(base.left_kind(), TokenKind::Edge);
        assert_eq!(base.slot_kind(0), TokenKind::Letter);
        assert_eq!(third_space.slot_kind(2), TokenKind::Space);
        assert_eq!(left_space.left_kind(), TokenKind::Space);
        assert_ne!(base, third_space);
        assert_ne!(base, left_space);
        assert_ne!(third_space, left_space);
        assert_ne!(hasher.hash_one(base), hasher.hash_one(third_space));
        assert_ne!(hasher.hash_one(base), hasher.hash_one(left_space));
        assert_eq!(base.runes_named().count(), 2);
    }

    /// A traced window's entry records the runes its evaluation read: at least the input's and the follower's, and none the window does not name. A hit replays those reads into an open capture, as it replays its delta.
    #[test]
    fn a_trace_journals_the_runes_it_read_and_a_hit_replays_them() {
        let index = firing_spec();
        let modes = EngineModes {
            trace_memo: true,
            ..EngineModes::default()
        };
        let ss03 = fixtures::sym(&index, "ss03");
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let left = settled_left(&index, "qsTea", "plain", Some("baseline"));
        let token = letter_token(&index, "qsPea");
        let slots = Slots::pair(letter_token(&index, "qsTea"), EDGE);
        let mut engine = Engine::with_modes(&index, [ss03], modes);
        engine
            .transition_trace(&left, token, slots)
            .expect("the fixture settles");
        let reads = engine
            .trace_reads(&left, token, slots)
            .expect("the window was traced")
            .to_vec();
        assert!(reads.contains(&Read::Rune(pea)), "{reads:?}");
        assert!(reads.contains(&Read::Rune(tea)), "{reads:?}");
        for read in &reads {
            if let Read::Rune(rune) = read {
                assert!([pea, tea].contains(rune), "{reads:?}");
            }
        }
        engine.begin_capture();
        engine
            .transition_trace(&left, token, slots)
            .expect("the fixture settles");
        let replayed = engine.end_capture().reads;
        assert_eq!(&*replayed, reads.as_slice());
    }

    #[test]
    fn shared_memo_hit_stats_track_trace_and_settled_reads_by_id() {
        let index = firing_spec();
        let modes = EngineModes {
            trace_memo: true,
            ..EngineModes::default()
        };
        let ss03 = fixtures::sym(&index, "ss03");
        let left = settled_left(&index, "qsTea", "plain", Some("baseline"));
        let token = letter_token(&index, "qsPea");
        let windows = [
            Slots::pair(letter_token(&index, "qsTea"), EDGE),
            Slots::pair(letter_token(&index, "qsTea"), UNKNOWN),
        ];
        let shared: Vec<_> = windows
            .iter()
            .map(|&slots| {
                let mut source = Engine::with_modes(&index, [ss03], modes);
                source
                    .transition_trace(&left, token, slots)
                    .expect("the source window settles");
                let key = Engine::trace_key(&left, token.letter_ordinal(), slots);
                let mut memo = source.take_memo().expect("the source journals");
                memo.entries = [(key, *memo.entries.get(&key).expect("the source window"))]
                    .into_iter()
                    .collect::<HashMap<_, _>>()
                    .into();
                assert_eq!(memo.len(), 1);
                SharedMemo {
                    memo: std::sync::Arc::new(memo),
                    excluded: crate::memo::Exclusion::none(),
                }
            })
            .collect();
        let mut engine = Engine::with_modes(&index, [ss03], modes);
        engine.attach_shared_memos(shared.clone());
        assert_eq!(engine.shared_memo_hits_by_id(), &[0, 0]);
        for slots in windows {
            engine
                .transition_trace(&left, token, slots)
                .expect("the shared memo answers the trace");
        }
        assert_eq!(engine.shared_memo_hits_by_id(), &[1, 1]);
        for slots in windows {
            assert!(
                engine
                    .settled_from_memo(&left, token, slots, |_| ())
                    .is_some()
            );
        }
        assert_eq!(engine.shared_memo_hits_by_id(), &[2, 2]);
        assert_eq!(engine.shared_memo_hits(), 4);
        assert_eq!(
            engine.shared_memo_hits(),
            engine.shared_memo_hits_by_id().iter().sum::<u64>()
        );
        engine.take_memo().expect("the reader journals");
        engine.release_memos();
        assert_eq!(engine.shared_memo_hits_by_id(), &[2, 2]);
        engine
            .transition_trace(&left, token, windows[1])
            .expect("retained shared memos still answer after releasing own memos");
        assert_eq!(engine.shared_memo_hits_by_id(), &[2, 3]);
        engine.attach_shared_memos(shared);
        assert_eq!(engine.shared_memo_hits_by_id(), &[0, 0]);
        assert_eq!(engine.shared_memo_hits(), 0);
        engine.attach_shared_memos(Vec::new());
        assert!(engine.shared_memo_hits_by_id().is_empty());
        assert_eq!(engine.shared_memo_hits(), 0);
    }

    /// Two windows that enumerate the same lists share one id in each pool, and an entry's lists read back from the pools as its miss returned them, descriptions included. `elimination_text_bytes` counts each description the pool holds once, not once per entry that names it.
    #[test]
    fn windows_with_the_same_lists_share_one_id_in_each_pool() {
        let index = firing_spec();
        let mut engine = Engine::with_modes(
            &index,
            [fixtures::sym(&index, "ss03")],
            EngineModes {
                trace_memo: true,
                ..EngineModes::default()
            },
        );
        let left = settled_left(&index, "qsTea", "plain", Some("baseline"));
        let pea = fixtures::sym(&index, "qsPea");
        let tea = letter_token(&index, "qsTea");
        let mut narrow = Vec::new();
        let first = engine
            .candidates(&left, pea, tea, EDGE, Some(&mut narrow))
            .expect("the fixture raises nothing");
        let mut wide = Vec::new();
        let second = engine
            .candidates(&left, pea, tea, UNKNOWN, Some(&mut wide))
            .expect("the fixture raises nothing");
        assert_eq!(first, second);
        assert_eq!(narrow, wide);
        assert!(!narrow.is_empty());
        let memo = &engine.candidates_cache;
        let entry = |right2| {
            *memo
                .entries
                .get(&Engine::candidates_key(&index, &left, pea, tea, right2))
                .expect("the window this test enumerated is memoized")
        };
        let (narrow_entry, wide_entry) = (entry(EDGE), entry(UNKNOWN));
        assert_eq!(narrow_entry.candidates, wide_entry.candidates);
        assert_eq!(narrow_entry.eliminations, wide_entry.eliminations);
        assert_eq!(memo.candidates.get(wide_entry.candidates), first);
        assert_eq!(memo.eliminations.get(wide_entry.eliminations), narrow);
        let per_entry: usize = memo
            .entries
            .values()
            .flat_map(|entry| memo.eliminations.get(entry.eliminations))
            .map(|elimination| elimination.description.len())
            .sum();
        assert!(engine.elimination_text_bytes() < per_entry);
    }

    /// An engine without trace-memo mode journals nothing, so every closure memo value holds the id of the empty delta and the delta pool holds only that one delta.
    #[test]
    fn a_journal_less_engine_interns_only_the_empty_delta() {
        let index = firing_spec();
        let mut engine = Engine::new(&index, [fixtures::sym(&index, "ss03")]);
        engine
            .candidates(
                &settled_left(&index, "qsTea", "plain", Some("baseline")),
                fixtures::sym(&index, "qsPea"),
                letter_token(&index, "qsTea"),
                EDGE,
                None,
            )
            .expect("the fixture raises nothing");
        assert!(!engine.closure_cache.is_empty());
        assert_eq!(engine.deltas.len(), 1);
        for &(_, delta, _) in engine.closure_cache.values() {
            assert!(engine.deltas.get(delta).is_empty());
        }
        engine.release_memos();
        assert_eq!(engine.deltas.len(), 0);
    }

    /// The trace memo keeps rankings in a separate map. An engine built without `explain_ranking` records none, and with it a hit returns the ranking its miss recorded.
    #[test]
    fn a_hit_reads_its_ranking_back_only_where_its_miss_recorded_one() {
        let index = firing_spec();
        let ss03 = fixtures::sym(&index, "ss03");
        let left = settled_left(&index, "qsTea", "plain", Some("baseline"));
        let token = letter_token(&index, "qsPea");
        let slots = Slots::pair(letter_token(&index, "qsTea"), EDGE);
        for explain_ranking in [false, true] {
            let mut engine = Engine::with_modes(
                &index,
                [ss03],
                EngineModes {
                    trace_memo: true,
                    explain_ranking,
                    ..EngineModes::default()
                },
            );
            let miss = engine
                .transition_trace(&left, token, slots)
                .expect("the fixture settles");
            let hit = engine
                .transition_trace(&left, token, slots)
                .expect("the fixture settles");
            assert_eq!(miss.ranking.is_some(), explain_ranking);
            assert_eq!(hit, miss);
            let memo = engine.trace_cache.as_ref().expect("trace-memo memoizes");
            assert!(!memo.entries.is_empty());
            assert_eq!(
                memo.rankings.len(),
                if explain_ranking {
                    memo.entries.len()
                } else {
                    0
                }
            );
        }
    }

    /// Recording the ranking does not change settlement. Two trace-memo engines, one recording rankings and one not, settle the same windows to the same trace once the ranking is removed, on the miss and on the hit. The windows cover boundary and settled lefts, two and four slots, and letters and boundaries on the right. The string replay and the table fixpoint build their engines without the ranking because of this.
    #[test]
    fn the_ranking_moves_no_settled_window() {
        let index = firing_spec();
        let ss03 = fixtures::sym(&index, "ss03");
        let pea = letter_token(&index, "qsPea");
        let tea = letter_token(&index, "qsTea");
        let windows = [
            (
                settled_left(&index, "qsTea", "plain", Some("baseline")),
                pea,
                Slots::pair(tea, EDGE),
            ),
            (
                settled_left(&index, "qsTea", "plain", Some("baseline")),
                pea,
                Slots::new(tea, pea, tea, EDGE),
            ),
            (
                settled_left(&index, "qsTea", "plain", Some("baseline")),
                pea,
                Slots::pair(tea, SPACE),
            ),
            (
                settled_left(&index, "qsPea", "half", Some("baseline")),
                tea,
                Slots::pair(pea, EDGE),
            ),
            (
                settled_left(&index, "qsPea", "half", Some("x-height")),
                tea,
                Slots::new(SPACE, UNKNOWN, UNKNOWN, UNKNOWN),
            ),
            (
                settled_left(&index, "qsPea", "half", Some("baseline")),
                tea,
                Slots::pair(EDGE, UNKNOWN),
            ),
            (
                LeftContext::boundary(TokenKind::Edge),
                tea,
                Slots::pair(pea, tea),
            ),
            (
                LeftContext::boundary(TokenKind::Space),
                tea,
                Slots::new(pea, tea, pea, EDGE),
            ),
            (
                LeftContext::boundary(TokenKind::Zwnj),
                tea,
                Slots::pair(EDGE, UNKNOWN),
            ),
        ];
        let engine = |explain_ranking| {
            Engine::with_modes(
                &index,
                [ss03],
                EngineModes {
                    trace_memo: true,
                    explain_ranking,
                    ..EngineModes::default()
                },
            )
        };
        let mut without = engine(false);
        let mut with = engine(true);
        for pass in ["miss", "hit"] {
            for (at, (left, token, slots)) in windows.iter().enumerate() {
                let settle = |engine: &mut Engine<'_>| {
                    engine
                        .transition_trace(left, *token, *slots)
                        .unwrap_or_else(|error| panic!("window {at} settles: {error:?}"))
                };
                let bare = settle(&mut without);
                let mut explained = settle(&mut with);
                assert!(bare.ranking.is_none(), "window {at} on the {pass}");
                assert!(
                    explained.ranking.take().is_some(),
                    "window {at} on the {pass}"
                );
                assert_eq!(bare, explained, "window {at} on the {pass}");
            }
        }
    }

    /// The ranking test spec: `rebuild/pipeline/fixtures.py`'s `synthetic_spec` written with this crate's four-family registry.
    ///
    /// `qsPea` declares `stroke`, which exits at the x-height, and then `flourish`, which has no surface. `qsTea` enters at the x-height and exits at the baseline but forbids pairing the two, so an entered `qsTea` has no exit. `qsMay` enters at the baseline. Whichever way the qsPea·qsTea junction goes, the window makes one join, so the join count ties and the later stages decide.
    fn ranking_spec(pea_policy: &str, tea_policy: &str) -> SpecIndex {
        let pea = letter(
            "qsPea",
            &[
                stance(
                    "stroke",
                    &surface(
                        "{}",
                        &object(&[row("x-height", &[("unjoined", "\"safe\"")])]),
                        &[],
                    ),
                ),
                stance("flourish", &surface("{}", "{}", &[])),
            ],
            pea_policy,
        );
        let tea = letter(
            "qsTea",
            &[stance(
                "hook",
                &surface(
                    &object(&[row("x-height", &[])]),
                    &object(&[row("baseline", &[("unjoined", "\"safe\"")])]),
                    &[(
                        "pairings",
                        r#"{"never":[{"entry":"x-height","exit":"baseline"}],"only":null}"#,
                    )],
                ),
            )],
            tea_policy,
        );
        let may = letter(
            "qsMay",
            &[stance(
                "base",
                &surface(&object(&[row("baseline", &[])]), "{}", &[]),
            )],
            &plain_policy(),
        );
        spec_of(&[pea, tea, may])
    }

    /// One policy record with a provenance pointer, so notes and error messages have a name to print.
    fn pointed_record(
        kind: &str,
        rune: &str,
        position: usize,
        overrides: &[(&str, &str)],
    ) -> String {
        let quoted = quoted(kind);
        let pointer = fixtures::names(&[
            &format!("{rune}.yaml"),
            &format!("policy.{kind}[{position}]"),
        ]);
        let mut fields: Vec<(&str, &str)> =
            vec![("kind", quoted.as_str()), ("provenance", pointer.as_str())];
        fields.extend_from_slice(overrides);
        fixtures::record(&fields)
    }

    fn quoted(value: &str) -> String {
        fixtures::quote(value)
    }

    /// A prefer record for `qsPea`'s `flourish` stance, which has no surface, in the mode the caller passes. Most of the stage tests use it.
    fn flourish_policy(mode: &str) -> String {
        fixtures::policy(&[(
            "prefer",
            &fixtures::seq(&[&pointed_record(
                "prefer",
                "qsPea",
                0,
                &[("stance", "\"flourish\""), ("mode", mode)],
            )]),
        )])
    }

    /// Settles `qsPea` with the run edge on its left and the given slots on its right.
    fn settle_pea(engine: &mut Engine<'_>, slots: Slots) -> Result<TransitionTrace, SettleError> {
        let token = letter_token(engine.index(), "qsPea");
        engine.transition_trace(&LeftContext::boundary(TokenKind::Edge), token, slots)
    }

    /// A `qsPea.stroke` left that committed the x-height junction with `extension` connector pixels.
    fn committed_left(index: &SpecIndex, extension: i64) -> LeftContext {
        let x_height = fixtures::sym(index, "x-height");
        LeftContext::letter(
            index,
            Settled {
                cell: CellId {
                    rune: fixtures::sym(index, "qsPea"),
                    stance: fixtures::sym(index, "stroke"),
                    entry: None,
                    exit: Some(x_height),
                    adjustments: Vec::new(),
                },
                junction: Some(x_height),
                extension,
            },
        )
    }

    #[test]
    fn a_boundary_input_settles_without_ranking_anything() {
        let index = ranking_spec(&plain_policy(), &plain_policy());
        let mut engine = Engine::new(&index, no_features());
        let trace = engine
            .transition_trace(
                &LeftContext::boundary(TokenKind::Edge),
                SPACE,
                Slots::pair(EDGE, EDGE),
            )
            .expect("a boundary settles into itself");
        assert_eq!(trace.decided_stage, DecidedStage::Boundary);
        assert_eq!(
            trace.settled,
            boundary_settled(index.vocab(), TokenKind::Space)
        );
        assert_eq!(trace.prospect, 0);
        assert!(!trace.joint_tiebreak);
        assert!(trace.ranking().ranked.is_empty());
        assert!(trace.ranking().eliminations.is_empty());
        assert!(trace.notes.is_empty());
        assert_eq!(trace.ranking().runner_up, None);
    }

    #[test]
    fn the_final_tiebreak_breaks_a_realization_tie_toward_the_join_and_flags_it_joint() {
        let index = ranking_spec(&plain_policy(), &plain_policy());
        let mut engine = Engine::new(&index, no_features());
        let trace = settle_pea(
            &mut engine,
            Slots::pair(letter_token(&index, "qsTea"), letter_token(&index, "qsMay")),
        )
        .expect("the fixture settles");
        let stroke = fixtures::sym(&index, "stroke");
        let flourish = fixtures::sym(&index, "flourish");
        let x_height = fixtures::sym(&index, "x-height");
        assert_eq!(trace.decided_stage, DecidedStage::Tiebreak);
        assert!(
            trace.joint_tiebreak,
            "the final tiebreak chose between realizing the junction and declining it"
        );
        assert_eq!(trace.settled.cell.stance, stroke);
        assert_eq!(trace.settled.cell.exit, Some(x_height));
        assert_eq!(trace.settled.junction, Some(x_height));
        assert_eq!(
            trace.ranking().runner_up,
            Some(Candidate::non_joining(
                &index,
                fixtures::sym(&index, "qsPea"),
                stroke,
                None,
                0
            ))
        );
        assert_eq!(trace.prospect, 0);
        assert_eq!(
            trace
                .ranking()
                .ranked
                .iter()
                .map(|entry| (entry.candidate, entry.join_count))
                .collect::<Vec<_>>(),
            [
                (
                    Candidate::joining(
                        &index,
                        fixtures::sym(&index, "qsPea"),
                        stroke,
                        None,
                        x_height,
                        0,
                        0
                    ),
                    1
                ),
                (
                    Candidate::non_joining(&index, fixtures::sym(&index, "qsPea"), stroke, None, 0),
                    1
                ),
                (
                    Candidate::non_joining(
                        &index,
                        fixtures::sym(&index, "qsPea"),
                        flourish,
                        None,
                        1
                    ),
                    1
                ),
            ],
            "every candidate is worth one join, so the ranked list falls back to declared order"
        );
    }

    #[test]
    fn the_join_count_decides_before_a_yielding_prefer_and_after_an_absolute_one() {
        let stroke_of = |index: &SpecIndex| fixtures::sym(index, "stroke");
        let plain = ranking_spec(&plain_policy(), &plain_policy());
        let mut engine = Engine::new(&plain, no_features());
        let trace = settle_pea(
            &mut engine,
            Slots::pair(letter_token(&plain, "qsTea"), EDGE),
        )
        .expect("the fixture settles");
        assert_eq!(trace.decided_stage, DecidedStage::JoinCount);
        assert_eq!(
            trace.settled.cell.exit,
            Some(fixtures::sym(&plain, "x-height"))
        );
        assert_eq!(
            trace.ranking().runner_up,
            Some(Candidate::non_joining(
                &plain,
                fixtures::sym(&plain, "qsPea"),
                stroke_of(&plain),
                None,
                0
            ))
        );

        let yielding = ranking_spec(&flourish_policy("null"), &plain_policy());
        let mut engine = Engine::new(&yielding, no_features());
        let trace = settle_pea(
            &mut engine,
            Slots::pair(letter_token(&yielding, "qsTea"), EDGE),
        )
        .expect("the fixture settles");
        assert_eq!(trace.decided_stage, DecidedStage::JoinCount);
        assert_eq!(trace.settled.cell.stance, stroke_of(&yielding));
        assert!(
            trace.notes.is_empty(),
            "the join count decided, so the yielding stage never ran and nothing was applied"
        );

        let absolute = ranking_spec(&flourish_policy("\"absolute\""), &plain_policy());
        let mut engine = Engine::new(&absolute, no_features());
        let trace = settle_pea(
            &mut engine,
            Slots::pair(letter_token(&absolute, "qsTea"), EDGE),
        )
        .expect("the fixture settles");
        assert_eq!(trace.decided_stage, DecidedStage::AbsolutePrefer);
        assert_eq!(
            trace.settled.cell.stance,
            fixtures::sym(&absolute, "flourish")
        );
        assert_eq!(trace.settled.junction, None);
        assert_eq!(trace.notes, ["prefer applied: qsPea.yaml:policy.prefer[0]"]);
    }

    #[test]
    fn a_yielding_prefer_decides_a_join_count_tie_and_names_the_loser_it_displaced() {
        let index = ranking_spec(&flourish_policy("null"), &plain_policy());
        let mut engine = Engine::new(&index, no_features());
        let trace = settle_pea(
            &mut engine,
            Slots::pair(letter_token(&index, "qsTea"), letter_token(&index, "qsMay")),
        )
        .expect("the fixture settles");
        assert_eq!(trace.decided_stage, DecidedStage::YieldingPrefer);
        assert_eq!(trace.settled.cell.stance, fixtures::sym(&index, "flourish"));
        assert_eq!(
            trace.ranking().runner_up,
            Some(Candidate::joining(
                &index,
                fixtures::sym(&index, "qsPea"),
                fixtures::sym(&index, "stroke"),
                None,
                fixtures::sym(&index, "x-height"),
                0,
                0
            )),
            "the runner-up is the first candidate the stage displaced, in the order it read them"
        );
        assert_eq!(trace.notes, ["prefer applied: qsPea.yaml:policy.prefer[0]"]);
    }

    #[test]
    fn per_stance_cell_preferences_do_not_choose_a_stance_or_conflict_with_each_other() {
        let records: Vec<String> = ["full", "half"]
            .into_iter()
            .enumerate()
            .map(|(position, name)| {
                pointed_record(
                    "prefer",
                    "qsPea",
                    position,
                    &[
                        ("stance", &quoted(name)),
                        ("cell", r#"{"exit":"none"}"#),
                        ("over", r#"{"exit":"baseline"}"#),
                        ("mode", "\"absolute\""),
                    ],
                )
            })
            .collect();
        let policy = fixtures::policy(&[(
            "prefer",
            &fixtures::seq(&records.iter().map(String::as_str).collect::<Vec<_>>()),
        )]);
        let outgoing = surface(
            "{}",
            &object(&[row("baseline", &[("unjoined", "\"safe\"")])]),
            &[],
        );
        for has_unmapped_stance in [false, true] {
            let mut stances = vec![stance("full", &outgoing), stance("half", &outgoing)];
            if has_unmapped_stance {
                stances.push(stance("unmapped", &outgoing));
            }
            let index = spec_of(&[
                letter("qsPea", &stances, &policy),
                letter(
                    "qsTea",
                    &[stance(
                        "hook",
                        &surface(&object(&[row("baseline", &[])]), "{}", &[]),
                    )],
                    &plain_policy(),
                ),
            ]);
            let mut engine = Engine::new(&index, no_features());
            let trace = settle_pea(
                &mut engine,
                Slots::pair(letter_token(&index, "qsTea"), EDGE),
            )
            .expect("each record narrows only the cells of its mapped stance");
            assert_eq!(
                trace.settled.cell.stance,
                fixtures::sym(
                    &index,
                    if has_unmapped_stance {
                        "unmapped"
                    } else {
                        "full"
                    }
                )
            );
            assert_eq!(
                trace.settled.junction,
                has_unmapped_stance.then(|| fixtures::sym(&index, "baseline")),
                "the unmapped stance keeps its join; mapped stances both yield without selecting between them"
            );
            assert_eq!(
                trace.notes,
                [
                    "prefer applied: qsPea.yaml:policy.prefer[0]",
                    "prefer applied: qsPea.yaml:policy.prefer[1]"
                ]
            );
        }
    }

    #[test]
    fn a_follower_cell_preference_reads_only_its_mapped_stance() {
        let tea_policy = fixtures::policy(&[(
            "prefer",
            &fixtures::seq(&[&pointed_record(
                "prefer",
                "qsTea",
                0,
                &[
                    ("stance", "\"full\""),
                    ("cell", r#"{"exit":"baseline"}"#),
                    ("over", r#"{"exit":"none"}"#),
                ],
            )]),
        )]);
        let incoming = object(&[row("x-height", &[])]);
        let outgoing = object(&[row("baseline", &[("unjoined", "\"safe\"")])]);
        let index = spec_of(&[
            letter(
                "qsPea",
                &[stance(
                    "stroke",
                    &surface(
                        "{}",
                        &object(&[row("x-height", &[("unjoined", "\"safe\"")])]),
                        &[],
                    ),
                )],
                &plain_policy(),
            ),
            letter(
                "qsTea",
                &[
                    stance(
                        "full",
                        &surface(
                            &incoming,
                            &outgoing,
                            &[(
                                "pairings",
                                r#"{"never":[{"entry":"x-height","exit":"baseline"}],"only":null}"#,
                            )],
                        ),
                    ),
                    stance("half", &surface(&incoming, &outgoing, &[])),
                ],
                &tea_policy,
            ),
            letter(
                "qsMay",
                &[stance(
                    "base",
                    &surface(&object(&[row("baseline", &[])]), "{}", &[]),
                )],
                &plain_policy(),
            ),
        ]);
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let record = &index.rune(tea).expect("qsTea is modeled").policy.prefer[0];
        let stroke = fixtures::sym(&index, "stroke");
        let mut engine = Engine::new(&index, no_features());
        for (candidate, expected) in [
            (Candidate::non_joining(&index, pea, stroke, None, 0), true),
            (
                Candidate::joining(
                    &index,
                    pea,
                    stroke,
                    None,
                    fixtures::sym(&index, "x-height"),
                    0,
                    0,
                ),
                false,
            ),
        ] {
            assert_eq!(
                engine.prefer_favors(
                    tea,
                    record,
                    pea,
                    candidate,
                    &LeftContext::boundary(TokenKind::Edge),
                    Slots::pair(letter_token(&index, "qsTea"), letter_token(&index, "qsMay")),
                ),
                Ok(Some(expected)),
                "an entered full stance cannot exit; the half stance's available exit is not one this record favors"
            );
        }
    }

    #[test]
    fn the_declared_order_decides_when_nothing_can_join() {
        let index = ranking_spec(&plain_policy(), &plain_policy());
        let mut engine = Engine::new(&index, no_features());
        let trace = settle_pea(&mut engine, Slots::pair(EDGE, EDGE)).expect("the fixture settles");
        assert_eq!(trace.decided_stage, DecidedStage::Order);
        assert_eq!(trace.settled.cell.stance, fixtures::sym(&index, "stroke"));
        assert_eq!(
            trace.ranking().runner_up,
            Some(Candidate::non_joining(
                &index,
                fixtures::sym(&index, "qsPea"),
                fixtures::sym(&index, "flourish"),
                None,
                1
            ))
        );
        assert!(
            trace.settled.cell.adjustments.is_empty(),
            "at a boundary the exit was never declined, so the base drawing stands"
        );
    }

    #[test]
    fn two_prefers_on_one_rune_demanding_different_stances_are_ambiguous() {
        let pea_policy = fixtures::policy(&[(
            "prefer",
            &fixtures::seq(&[
                &pointed_record("prefer", "qsPea", 0, &[("stance", "\"stroke\"")]),
                &pointed_record("prefer", "qsPea", 1, &[("stance", "\"flourish\"")]),
            ]),
        )]);
        let index = ranking_spec(&pea_policy, &plain_policy());
        let mut engine = Engine::new(&index, no_features());
        let error = settle_pea(
            &mut engine,
            Slots::pair(letter_token(&index, "qsTea"), letter_token(&index, "qsMay")),
        )
        .expect_err("equal records demanding disjoint stances cannot both be honored");
        assert_eq!(error.kind(), SettleErrorKind::Ambiguous);
        assert_eq!(
            error.message(),
            "E-AMBIGUOUS: prefer records demand different outcomes at non-nested specificity: qsPea.yaml:policy.prefer[0] vs qsPea.yaml:policy.prefer[1]"
        );
    }

    /// [`ranking_spec`] with two chained prefers for the follower prefer tests. `qsPea`'s record favors its `stroke` stance when the slots after `qsPea` are qsTea·qsMay·qsPea. `qsTea`'s record favors its `hook` stance when the slots after `qsTea` are qsMay·qsPea. Its chain is one hop shorter because a follower's record is evaluated one position to the right.
    fn follower_prefer_slot_spec() -> SpecIndex {
        let pea_chain = fixtures::condition(&[
            ("family", &fixtures::names(&["qsTea"])),
            (
                "then",
                &fixtures::condition(&[
                    ("family", &fixtures::names(&["qsMay"])),
                    (
                        "then",
                        &fixtures::condition(&[("family", &fixtures::names(&["qsPea"]))]),
                    ),
                ]),
            ),
        ]);
        let tea_chain = fixtures::condition(&[
            ("family", &fixtures::names(&["qsMay"])),
            (
                "then",
                &fixtures::condition(&[("family", &fixtures::names(&["qsPea"]))]),
            ),
        ]);
        let pea_policy = fixtures::policy(&[(
            "prefer",
            &fixtures::seq(&[&pointed_record(
                "prefer",
                "qsPea",
                0,
                &[
                    ("stance", "\"stroke\""),
                    ("when", &fixtures::when(&[("right", pea_chain.as_str())])),
                ],
            )]),
        )]);
        let tea_policy = fixtures::policy(&[(
            "prefer",
            &fixtures::seq(&[&pointed_record(
                "prefer",
                "qsTea",
                0,
                &[
                    ("stance", "\"hook\""),
                    ("when", &fixtures::when(&[("right", tea_chain.as_str())])),
                ],
            )]),
        )]);
        ranking_spec(&pea_policy, &tea_policy)
    }

    #[test]
    fn the_prefer_branches_read_their_own_deep_slots_and_the_follower_prefer_reads_them_shifted() {
        let index = follower_prefer_slot_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let own = &index.rune(pea).expect("qsPea is modeled").policy.prefer[0];
        let follower = &index.rune(tea).expect("qsTea is modeled").policy.prefer[0];
        let candidate = Candidate::joining(
            &index,
            pea,
            fixtures::sym(&index, "stroke"),
            None,
            fixtures::sym(&index, "x-height"),
            0,
            NO_EXIT_INDEX,
        );
        let edge = LeftContext::boundary(TokenKind::Edge);
        let pea_token = letter_token(&index, "qsPea");
        let tea_token = letter_token(&index, "qsTea");
        let may_token = letter_token(&index, "qsMay");
        let pinned = EngineModes {
            follower_prefer_slots: false,
            ..EngineModes::default()
        };

        for modes in [EngineModes::default(), pinned] {
            let mut engine = Engine::with_modes(&index, no_features(), modes);
            assert_eq!(
                engine.prefer_favors(
                    pea,
                    own,
                    pea,
                    candidate,
                    &edge,
                    Slots::new(tea_token, may_token, pea_token, UNKNOWN)
                ),
                Ok(Some(true)),
                "our own rune's record reads the window's raw deep slots whatever the follower prefer's mode"
            );
            assert_eq!(
                engine.prefer_favors(
                    pea,
                    own,
                    pea,
                    candidate,
                    &edge,
                    Slots::new(tea_token, may_token, may_token, UNKNOWN)
                ),
                Ok(None),
                "the third slot refutes the chain, so the record has nothing to say about this window"
            );
        }

        let follower_prefer = |engine: &mut Engine<'_>, slots: Slots| {
            engine
                .prefer_favors(tea, follower, pea, candidate, &edge, slots)
                .expect("the fixture raises nothing")
        };
        let mut pinned_engine = Engine::with_modes(&index, no_features(), pinned);
        assert_eq!(
            follower_prefer(&mut pinned_engine, Slots::pair(tea_token, may_token)),
            Some(true),
            "everything past the follower prefer's own right1 is pinned, so the chain's tail is unknown and the follower prefer fires optimistically"
        );
        assert_eq!(
            follower_prefer(
                &mut pinned_engine,
                Slots::new(tea_token, may_token, pea_token, pea_token)
            ),
            Some(true)
        );
        assert_eq!(
            follower_prefer(
                &mut pinned_engine,
                Slots::new(tea_token, may_token, may_token, may_token)
            ),
            Some(true),
            "the pinned follower prefer answers the same whatever the window's deep slots hold"
        );

        let mut shifted = Engine::new(&index, no_features());
        assert_eq!(
            follower_prefer(
                &mut shifted,
                Slots::new(tea_token, may_token, pea_token, UNKNOWN)
            ),
            Some(true),
            "shifted once, the follower prefer's chain resolves inside the window and fires"
        );
        assert_eq!(
            follower_prefer(
                &mut shifted,
                Slots::new(tea_token, may_token, may_token, UNKNOWN)
            ),
            None,
            "the same slots refute it definitively, and an irrelevant record does not count against the candidate"
        );
        assert_eq!(
            follower_prefer(&mut shifted, Slots::pair(tea_token, may_token)),
            Some(true),
            "where the window really does end, the shifted reading also treats an unknown slot as a match"
        );
    }

    /// Two prefers from different runes with equal specificity and conflicting demands, the case a `resolve` record settles. `qsPea` prefers a cell that uses its x-height exit, and `qsTea`'s follower prefer favors whichever `qsPea` cell leaves its own baseline exit usable. The caller passes `qsPea`'s `resolve` list.
    fn conflicting_prefers_spec(pea_resolve: &str) -> SpecIndex {
        let pea_policy = fixtures::policy(&[
            (
                "prefer",
                &fixtures::seq(&[&pointed_record(
                    "prefer",
                    "qsPea",
                    0,
                    &[("cell", &fixtures::map(&[("exit", "\"x-height\"")]))],
                )]),
            ),
            ("resolve", pea_resolve),
        ]);
        let tea_policy = fixtures::policy(&[(
            "prefer",
            &fixtures::seq(&[&pointed_record(
                "prefer",
                "qsTea",
                0,
                &[("cell", &fixtures::map(&[("exit", "\"baseline\"")]))],
            )]),
        )]);
        ranking_spec(&pea_policy, &tea_policy)
    }

    fn conflicting_prefers_slots(index: &SpecIndex) -> Slots {
        Slots::pair(letter_token(index, "qsTea"), letter_token(index, "qsMay"))
    }

    #[test]
    fn a_conflict_between_two_runes_prefers_prints_the_resolve_that_would_settle_it() {
        let index = conflicting_prefers_spec("[]");
        let mut engine = Engine::new(&index, no_features());
        let error = settle_pea(&mut engine, conflicting_prefers_slots(&index))
            .expect_err("neither rune's prefer contains the other's");
        assert_eq!(error.kind(), SettleErrorKind::Incomparable);
        assert_eq!(
            error.message(),
            concat!(
                "E-INCOMPARABLE: prefer records demand different outcomes at non-nested specificity: qsPea.yaml:policy.prefer[0] vs qsTea.yaml:policy.prefer[0].\n",
                "  example window: qsPea qsTea qsMay\n",
                "  conflicted candidates on qsPea: (stroke, entry none, exit x-height), (stroke, entry none, exit none), (flourish, entry none, exit none)\n",
                "  paste-ready resolve for glyph_data/runes/qsPea.yaml policy.resolve (design section 5.8):\n",
                "  - against: {rune: qsTea, id: <give that record an id: first>}\n",
                "    when: {right: {family: qsTea, then: {family: qsMay}}}\n",
                "    pick: {exit: <the winning cell>}\n",
                "    why: <author rationale, mandatory>"
            )
        );
    }

    /// A spec with an empty string in each place the E-INCOMPARABLE message falls back on a default: a rune named `""`, whose provenance is `.yaml:…`, a registry height named `""`, and a prefer record whose `id:` is `""`. The empty rune name and the empty height are the same interned symbol.
    fn empty_spelling_spec() -> SpecIndex {
        let registry = fixtures::registry(&[
            ("heights", &fixtures::map(&[("", "0"), ("x-height", "5")])),
            (
                "families",
                &fixtures::map(&[
                    ("qsPea", r#"{"codepoint":58960,"sequence":null}"#),
                    ("", r#"{"codepoint":58962,"sequence":null}"#),
                ]),
            ),
        ]);
        let prefer_of = |rune: &str, id: &str| {
            fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&pointed_record(
                    "prefer",
                    rune,
                    0,
                    &[("id", &fixtures::quote(id))],
                )]),
            )])
        };
        let pea = letter(
            "qsPea",
            &[stance("bare", &surface("{}", "{}", &[]))],
            &prefer_of("qsPea", "pea-x"),
        );
        let nameless = letter(
            "",
            &[stance("bare", &surface("{}", "{}", &[]))],
            &prefer_of("", ""),
        );
        fixtures::index_of(&fixtures::dump(&object(&[pea, nameless]), &registry))
    }

    /// Three fields of this message treat an empty authored string as absent: the example window leaves out the nameless rune instead of printing an extra space, the candidate that entered at the empty height prints `entry none`, and the empty `id:` prints the instruction to give the record one. The `when:` clause is the control. It prints family names as they are, so the nameless follower appears there as `family: ` in the expected text.
    ///
    /// The expected text was taken from the Python implementation's message for the same arguments.
    #[test]
    fn the_incomparable_sentence_reads_every_empty_spelling_the_way_python_does() {
        let index = empty_spelling_spec();
        let engine = Engine::new(&index, no_features());
        let pea = fixtures::sym(&index, "qsPea");
        let empty = fixtures::sym(&index, "");
        let bare = fixtures::sym(&index, "bare");
        let prefer_of = |owner: Sym| OwnedRecord {
            owner,
            record: &index
                .rune(owner)
                .expect("the fixture models both runes")
                .policy
                .prefer[0],
        };
        let survivors = [
            Candidate::non_joining(&index, pea, bare, Some(empty), 0),
            Candidate::joining(
                &index,
                pea,
                bare,
                None,
                fixtures::sym(&index, "x-height"),
                0,
                0,
            ),
        ];
        let left = LeftContext::letter(
            &index,
            Settled {
                cell: CellId {
                    rune: empty,
                    stance: bare,
                    entry: None,
                    exit: None,
                    adjustments: Vec::new(),
                },
                junction: None,
                extension: 0,
            },
        );
        let message = engine.incomparable_message(
            prefer_of(pea),
            prefer_of(empty),
            pea,
            &survivors,
            &left,
            Slots::pair(
                letter_token(&index, "qsPea"),
                index
                    .letter(empty)
                    .expect("the fixture models the empty rune"),
            ),
        );
        assert_eq!(
            message,
            concat!(
                "E-INCOMPARABLE: prefer records demand different outcomes at non-nested specificity: qsPea.yaml:policy.prefer[0] vs .yaml:policy.prefer[0].\n",
                "  example window: qsPea qsPea\n",
                "  conflicted candidates on qsPea: (bare, entry none, exit none), (bare, entry none, exit x-height)\n",
                "  paste-ready resolve for glyph_data/runes/qsPea.yaml policy.resolve (design section 5.8):\n",
                "  - against: {rune: , id: <give that record an id: first>}\n",
                "    when: {right: {family: qsPea, then: {family: }}}\n",
                "    pick: {exit: <the winning cell>}\n",
                "    why: <author rationale, mandatory>"
            )
        );
    }

    #[test]
    fn a_resolve_naming_the_other_rune_picks_the_winner_and_says_so_in_the_notes() {
        let resolve = fixtures::seq(&[&pointed_record(
            "resolve",
            "qsPea",
            0,
            &[
                ("against", &fixtures::seq(&["\"qsTea\"", "null"])),
                ("pick", &fixtures::map(&[("exit", "\"x-height\"")])),
            ],
        )]);
        let index = conflicting_prefers_spec(&resolve);
        let mut engine = Engine::new(&index, no_features());
        let trace = settle_pea(&mut engine, conflicting_prefers_slots(&index))
            .expect("the resolve settles the conflict");
        assert_eq!(
            trace.settled.cell.exit,
            Some(fixtures::sym(&index, "x-height"))
        );
        assert_eq!(trace.decided_stage, DecidedStage::YieldingPrefer);
        assert_eq!(
            trace.notes,
            [
                "prefer applied: qsPea.yaml:policy.prefer[0]",
                "resolve applied: qsPea.yaml:policy.resolve[0]",
            ]
        );
        assert!(engine.fired().contains(&Pointer {
            file: fixtures::sym(&index, "qsPea.yaml"),
            path: fixtures::sym(&index, "policy.resolve[0]"),
        }));
    }

    #[test]
    fn a_resolve_whose_pick_admits_nothing_and_two_resolves_that_disagree_stay_hard_errors() {
        let empty_pick = fixtures::seq(&[&pointed_record(
            "resolve",
            "qsPea",
            0,
            &[
                ("against", &fixtures::seq(&["\"qsTea\"", "null"])),
                ("pick", &fixtures::map(&[("stance", "\"ghost\"")])),
            ],
        )]);
        let index = conflicting_prefers_spec(&empty_pick);
        let mut engine = Engine::new(&index, no_features());
        let error = settle_pea(&mut engine, conflicting_prefers_slots(&index))
            .expect_err("a pick naming no stance of this rune admits no survivor");
        assert_eq!(error.kind(), SettleErrorKind::Incomparable);
        assert_eq!(
            error.message(),
            "E-INCOMPARABLE: resolve qsPea.yaml:policy.resolve[0] matched but its pick admits no surviving candidate"
        );

        let disagreeing = fixtures::seq(&[
            &pointed_record(
                "resolve",
                "qsPea",
                0,
                &[
                    ("against", &fixtures::seq(&["\"qsTea\"", "null"])),
                    ("pick", &fixtures::map(&[("exit", "\"x-height\"")])),
                ],
            ),
            &pointed_record(
                "resolve",
                "qsPea",
                1,
                &[
                    ("against", &fixtures::seq(&["\"qsTea\"", "null"])),
                    ("pick", &fixtures::map(&[("exit", "\"none\"")])),
                ],
            ),
        ]);
        let index = conflicting_prefers_spec(&disagreeing);
        let mut engine = Engine::new(&index, no_features());
        let error = settle_pea(&mut engine, conflicting_prefers_slots(&index))
            .expect_err("two resolves cannot both name the winner");
        assert_eq!(
            error.message(),
            "E-INCOMPARABLE: conflicting resolve records match one window: qsPea.yaml:policy.resolve[0]; qsPea.yaml:policy.resolve[1]"
        );
    }

    #[test]
    fn an_entry_extension_is_suppressed_when_the_predecessor_already_carries_the_junction() {
        let tea_policy = fixtures::policy(&[(
            "extend",
            &fixtures::seq(&[&pointed_record(
                "extend",
                "qsTea",
                0,
                &[("entry", "\"x-height\""), ("by", "1")],
            )]),
        )]);
        let index = ranking_spec(&plain_policy(), &tea_policy);
        let mut engine = Engine::new(&index, no_features());
        let token = letter_token(&index, "qsTea");
        let slots = Slots::pair(letter_token(&index, "qsMay"), EDGE);

        let trace = engine
            .transition_trace(&committed_left(&index, 0), token, slots)
            .expect("the fixture settles");
        assert_eq!(
            trace.settled.cell.adjustments,
            [AdjustmentToken::Extend(Side::Entry, 1)]
        );
        assert_eq!(trace.notes, ["qsTea.yaml:policy.extend[0]"]);

        let trace = engine
            .transition_trace(&committed_left(&index, 1), token, slots)
            .expect("the fixture settles");
        assert!(
            trace.settled.cell.adjustments.is_empty(),
            "the predecessor's exit already drew the connector pixels"
        );
        assert_eq!(
            trace.notes,
            [
                "qsTea.yaml:policy.extend[0]",
                "entry extension suppressed: the predecessor's exit already carries the junction's connector pixels (same-junction non-summing)",
            ],
            "the suppressed record still matched and still fired, and the suppression says so"
        );
    }

    /// `adjustment_tokens` and `Engine::commit` treat a zero `by` differently. The token list includes an extend only for a nonzero `by` and a contract whenever `by` is present, so a zero-pixel extend adds no token while a zero-pixel contract adds `ex-con-0` and stays visible in the cell's identity. The extension arithmetic in `commit` skips a zero `by` for both, so neither moves the extension. Both records matched and fired regardless of `by`, and the notes list both.
    #[test]
    fn a_zero_pixel_extend_spells_nothing_while_a_zero_pixel_contract_spells_itself() {
        let pea_policy = fixtures::policy(&[
            (
                "extend",
                &fixtures::seq(&[&pointed_record(
                    "extend",
                    "qsPea",
                    0,
                    &[("exit", "\"x-height\""), ("by", "0")],
                )]),
            ),
            (
                "contract",
                &fixtures::seq(&[&pointed_record(
                    "contract",
                    "qsPea",
                    0,
                    &[("exit", "\"x-height\""), ("by", "0")],
                )]),
            ),
        ]);
        let index = ranking_spec(&pea_policy, &plain_policy());
        let mut engine = Engine::new(&index, no_features());
        let trace = settle_pea(
            &mut engine,
            Slots::pair(letter_token(&index, "qsTea"), EDGE),
        )
        .expect("the fixture settles");
        assert_eq!(
            trace.settled.cell.exit,
            Some(fixtures::sym(&index, "x-height")),
            "the junction the two records shape has to be the one that won"
        );
        assert_eq!(
            trace.settled.cell.adjustments,
            [AdjustmentToken::Contract(Side::Exit, 0)]
        );
        assert_eq!(trace.settled.extension, 0);
        assert_eq!(
            trace.notes,
            [
                "qsPea.yaml:policy.extend[0]",
                "qsPea.yaml:policy.contract[0]"
            ]
        );
    }

    /// An extend with a `bind:` writes the binding token before the extension token, so geometry swaps in the drawing before it lengthens the connector. The pixels still count toward the junction's extension.
    #[test]
    fn a_bound_extend_spells_its_binding_before_its_extension() {
        let pea_policy = fixtures::policy(&[(
            "extend",
            &fixtures::seq(&[&pointed_record(
                "extend",
                "qsPea",
                0,
                &[
                    ("exit", "\"x-height\""),
                    ("by", "1"),
                    ("bind", "\"reaching\""),
                ],
            )]),
        )]);
        let index = ranking_spec(&pea_policy, &plain_policy());
        let mut engine = Engine::new(&index, no_features());
        let trace = settle_pea(
            &mut engine,
            Slots::pair(letter_token(&index, "qsTea"), EDGE),
        )
        .expect("the fixture settles");
        assert_eq!(
            trace.settled.cell.adjustments,
            [
                AdjustmentToken::Bind(Side::Exit, fixtures::sym(&index, "reaching")),
                AdjustmentToken::Extend(Side::Exit, 1),
            ]
        );
        assert_eq!(trace.settled.extension, 1);
        assert_eq!(trace.notes, ["qsPea.yaml:policy.extend[0]"]);
    }

    /// `qsPea`'s policy with one extend and one contract on its x-height exit, each carrying the caller's fields.
    fn extend_and_contract_policy(extend: &[(&str, &str)], contract: &[(&str, &str)]) -> String {
        let mut extend_fields = vec![("exit", "\"x-height\"")];
        extend_fields.extend_from_slice(extend);
        let mut contract_fields = vec![("exit", "\"x-height\"")];
        contract_fields.extend_from_slice(contract);
        fixtures::policy(&[
            (
                "extend",
                &fixtures::seq(&[&pointed_record("extend", "qsPea", 0, &extend_fields)]),
            ),
            (
                "contract",
                &fixtures::seq(&[&pointed_record("contract", "qsPea", 0, &contract_fields)]),
            ),
        ])
    }

    fn settle_before_tea(pea_policy: &str) -> Result<Settled, SettleError> {
        let index = ranking_spec(pea_policy, &plain_policy());
        let mut engine = Engine::new(&index, no_features());
        settle_pea(
            &mut engine,
            Slots::pair(letter_token(&index, "qsTea"), EDGE),
        )
        .map(|trace| trace.settled)
    }

    #[test]
    fn an_extend_and_a_contract_on_one_side_both_apply() {
        let policy = extend_and_contract_policy(&[("by", "2")], &[("by", "1")]);
        let settled = settle_before_tea(&policy).expect("the fixture settles");
        assert_eq!(
            settled.cell.adjustments,
            [
                AdjustmentToken::Extend(Side::Exit, 2),
                AdjustmentToken::Contract(Side::Exit, 1),
            ]
        );
        assert_eq!(settled.extension, 1);
    }

    /// The contract's binding is written before the extend's extension, so the extend lengthens the drawing the contract binds instead of being replaced by it.
    #[test]
    fn a_bound_contract_spells_its_binding_before_the_extension() {
        let policy = extend_and_contract_policy(&[("by", "1")], &[("bind", "\"raked\"")]);
        let index = ranking_spec(&policy, &plain_policy());
        let settled = settle_before_tea(&policy).expect("the fixture settles");
        assert_eq!(
            settled.cell.adjustments,
            [
                AdjustmentToken::Bind(Side::Exit, fixtures::sym(&index, "raked")),
                AdjustmentToken::Extend(Side::Exit, 1),
            ]
        );
        assert_eq!(settled.extension, 1);
    }

    #[test]
    fn two_bindings_of_one_side_agree_or_raise() {
        let same = extend_and_contract_policy(
            &[("bind", "\"reaching\""), ("by", "1")],
            &[("bind", "\"reaching\"")],
        );
        let index = ranking_spec(&same, &plain_policy());
        let settled = settle_before_tea(&same).expect("one drawing twice is one binding");
        assert_eq!(
            settled.cell.adjustments,
            [
                AdjustmentToken::Bind(Side::Exit, fixtures::sym(&index, "reaching")),
                AdjustmentToken::Extend(Side::Exit, 1),
            ]
        );
        let different =
            extend_and_contract_policy(&[("bind", "\"reaching\"")], &[("bind", "\"raked\"")]);
        let error = settle_before_tea(&different).expect_err("a side is drawn only one way");
        assert_eq!(error.kind(), SettleErrorKind::Incomparable);
        assert_eq!(
            error.message(),
            "E-INCOMPARABLE: an extend record and a contract record bind the exit side to different drawings: qsPea.yaml:policy.extend[0] binds reaching; qsPea.yaml:policy.contract[0] binds raked. A side is drawn only one way, so settle it by editing the records: bind the same drawing, drop one record's bind:, or narrow one record's when: so the two no longer co-match."
        );
    }

    /// [`ranking_spec`] without `qsPea`'s `flourish` stance and with no policy records. `qsTea`'s baseline exit binds the `pulled-back` drawing when unjoined instead of `safe`, and `qsTea.hook` carries the caller's `cells:` list.
    fn unjoined_spec(cells: &str) -> SpecIndex {
        let tea = letter(
            "qsTea",
            &[stance(
                "hook",
                &surface(
                    &object(&[row("x-height", &[])]),
                    &object(&[row("baseline", &[("unjoined", "\"pulled-back\"")])]),
                    &[
                        (
                            "pairings",
                            r#"{"never":[{"entry":"x-height","exit":"baseline"}],"only":null}"#,
                        ),
                        ("cells", cells),
                    ],
                ),
            )],
            &plain_policy(),
        );
        let pea = letter(
            "qsPea",
            &[stance(
                "stroke",
                &surface(
                    "{}",
                    &object(&[row("x-height", &[("unjoined", "\"safe\"")])]),
                    &[],
                ),
            )],
            &plain_policy(),
        );
        let may = letter(
            "qsMay",
            &[stance(
                "base",
                &surface(&object(&[row("baseline", &[])]), "{}", &[]),
            )],
            &plain_policy(),
        );
        spec_of(&[pea, tea, may])
    }

    #[test]
    fn a_declined_exit_binds_its_unjoined_drawing_and_an_explicit_cell_overrides_it() {
        let index = unjoined_spec("[]");
        let mut engine = Engine::new(&index, no_features());
        let trace = engine
            .transition_trace(
                &committed_left(&index, 0),
                letter_token(&index, "qsTea"),
                Slots::pair(letter_token(&index, "qsMay"), EDGE),
            )
            .expect("the fixture settles");
        assert_eq!(
            cell_label(&index, &trace.settled.cell),
            "qsTea.hook.en-y5.ex-bind-pulled-back"
        );

        let index = unjoined_spec(
            r#"[{"entry":"x-height","exit":"baseline-unjoined","bitmap":"hook-after-pea","entry_x":null,"exit_x":null,"provenance":null}]"#,
        );
        let mut engine = Engine::new(&index, no_features());
        let trace = engine
            .transition_trace(
                &committed_left(&index, 0),
                letter_token(&index, "qsTea"),
                Slots::pair(letter_token(&index, "qsMay"), EDGE),
            )
            .expect("the fixture settles");
        assert_eq!(
            cell_label(&index, &trace.settled.cell),
            "qsTea.hook.en-y5.ex-bind-hook-after-pea",
            "an explicit cells: composition for the unjoined pair overrides the row's binding"
        );
    }

    /// A spec whose one adjustment record reads `depth` raw slots to the right. `qsTea` enters at the x-height, has no exit, and extends its entry by one pixel when the slots after it are `qsMay` and then `depth - 1` `qsIt`s. `qsMay` and `qsIt` are modeled so that every slot of the deep window names a rune the prospect can settle.
    fn deep_adjustment_spec(depth: usize) -> SpecIndex {
        let mut condition = fixtures::condition(&[("family", &fixtures::names(&["qsIt"]))]);
        for _ in 2..depth {
            condition = fixtures::condition(&[
                ("family", &fixtures::names(&["qsIt"])),
                ("then", &condition),
            ]);
        }
        let chain = fixtures::condition(&[
            ("family", &fixtures::names(&["qsMay"])),
            ("then", &condition),
        ]);
        let entering = |name: &str, stance_name: &str| {
            letter(
                name,
                &[stance(
                    stance_name,
                    &surface(&object(&[row("baseline", &[])]), "{}", &[]),
                )],
                &plain_policy(),
            )
        };
        let pea = letter(
            "qsPea",
            &[stance(
                "stroke",
                &surface(
                    "{}",
                    &object(&[row("x-height", &[("unjoined", "\"safe\"")])]),
                    &[],
                ),
            )],
            &plain_policy(),
        );
        let tea = letter(
            "qsTea",
            &[stance(
                "hook",
                &surface(&object(&[row("x-height", &[])]), "{}", &[]),
            )],
            &fixtures::policy(&[(
                "extend",
                &fixtures::seq(&[&pointed_record(
                    "extend",
                    "qsTea",
                    0,
                    &[
                        ("entry", "\"x-height\""),
                        ("by", "1"),
                        ("when", &fixtures::when(&[("right", &chain)])),
                    ],
                )]),
            )]),
        );
        spec_of(&[
            pea,
            tea,
            entering("qsMay", "base"),
            entering("qsIt", "base"),
        ])
    }

    #[test]
    fn an_adjustment_reads_two_raw_slots_and_never_the_deeper_window() {
        let deep_window = |index: &SpecIndex| {
            let it = letter_token(index, "qsIt");
            Slots::new(letter_token(index, "qsMay"), it, it, EDGE)
        };

        let index = deep_adjustment_spec(2);
        let mut engine = Engine::new(&index, no_features());
        let trace = engine
            .transition_trace(
                &committed_left(&index, 0),
                letter_token(&index, "qsTea"),
                deep_window(&index),
            )
            .expect("the fixture settles");
        assert_eq!(
            trace.settled.cell.adjustments,
            [AdjustmentToken::Extend(Side::Entry, 1)],
            "a chain reaching only the follower and the slot past it decides inside the commit's window"
        );

        let index = deep_adjustment_spec(3);
        let mut engine = Engine::new(&index, no_features());
        let trace = engine
            .transition_trace(
                &committed_left(&index, 0),
                letter_token(&index, "qsTea"),
                deep_window(&index),
            )
            .expect("the fixture settles");
        assert!(
            trace.settled.cell.adjustments.is_empty(),
            "the third slot is UNKNOWN to the commit however the real window reads, so the record never fires definitively"
        );
    }

    /// The issue-28 shape, as in `rebuild/pipeline/fixtures.py`'s `prospect_spec`. `qsPea` exits at both heights and prefers the x-height as a yielding tie-break. `qsTea` enters at both heights, has no exit when entered at the x-height, and prefers to decline its baseline exit before qsMay·qsIt. An entered `qsMay` has no exit, so `qsTea` joining `qsMay` prevents the qsMay·qsIt join, and `qsTea` declining allows it. The candidacy estimate therefore scores `qsPea`'s baseline exit as if `qsTea`'s onward join will happen, and the simulated prospect sees `qsTea` decline it one position later.
    fn prospect_spec() -> SpecIndex {
        let safe = |height: &str| row(height, &[("unjoined", "\"safe\"")]);
        let pea = letter(
            "qsPea",
            &[stance(
                "stroke",
                &surface("{}", &object(&[safe("x-height"), safe("baseline")]), &[]),
            )],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&pointed_record(
                    "prefer",
                    "qsPea",
                    0,
                    &[
                        ("cell", &fixtures::map(&[("exit", "\"x-height\"")])),
                        ("over", &fixtures::map(&[("exit", "\"baseline\"")])),
                    ],
                )]),
            )]),
        );
        let toward_may_then_it = fixtures::condition(&[
            ("family", &fixtures::names(&["qsMay"])),
            (
                "then",
                &fixtures::condition(&[("family", &fixtures::names(&["qsIt"]))]),
            ),
        ]);
        let tea = letter(
            "qsTea",
            &[stance(
                "hook",
                &surface(
                    &object(&[row("x-height", &[]), row("baseline", &[])]),
                    &object(&[safe("baseline")]),
                    &[(
                        "pairings",
                        r#"{"never":[{"entry":"x-height","exit":"baseline"}],"only":null}"#,
                    )],
                ),
            )],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&pointed_record(
                    "prefer",
                    "qsTea",
                    0,
                    &[
                        ("when", &fixtures::when(&[("right", &toward_may_then_it)])),
                        ("cell", &fixtures::map(&[("exit", "\"none\"")])),
                        ("over", &fixtures::map(&[("exit", "\"baseline\"")])),
                    ],
                )]),
            )]),
        );
        let may = letter(
            "qsMay",
            &[stance(
                "base",
                &surface(
                    &object(&[row("baseline", &[])]),
                    &object(&[safe("baseline")]),
                    &[(
                        "pairings",
                        r#"{"never":[{"entry":"baseline","exit":"baseline"}],"only":null}"#,
                    )],
                ),
            )],
            &plain_policy(),
        );
        let it = letter(
            "qsIt",
            &[stance(
                "base",
                &surface(&object(&[row("baseline", &[])]), "{}", &[]),
            )],
            &plain_policy(),
        );
        spec_of(&[pea, tea, may, it])
    }

    #[test]
    fn the_simulated_prospect_sees_the_follower_yield_the_join_the_estimate_promised() {
        let index = prospect_spec();
        let slots = Slots::new(
            letter_token(&index, "qsTea"),
            letter_token(&index, "qsMay"),
            letter_token(&index, "qsIt"),
            EDGE,
        );
        let candidacy = EngineModes {
            simulated_prospect: false,
            ..EngineModes::default()
        };

        let mut estimating = Engine::with_modes(&index, no_features(), candidacy);
        let trace = settle_pea(&mut estimating, slots).expect("the fixture settles");
        assert_eq!(
            trace.settled.cell.exit,
            Some(fixtures::sym(&index, "baseline")),
            "the estimate scores the baseline exit as if qsTea's onward join will happen"
        );
        assert_eq!(trace.decided_stage, DecidedStage::JoinCount);

        let mut simulating = Engine::new(&index, no_features());
        let trace = settle_pea(&mut simulating, slots).expect("the fixture settles");
        assert_eq!(
            trace.settled.cell.exit,
            Some(fixtures::sym(&index, "x-height")),
            "the simulated term sees qsTea yield that join, so the two exits tie and the prefer decides"
        );
        assert_eq!(trace.decided_stage, DecidedStage::YieldingPrefer);
        assert_eq!(simulating.simulated_prospect_fallbacks(), 0);
    }

    #[test]
    fn the_prospect_bottoms_out_at_the_window_edge_where_both_modes_agree() {
        let index = prospect_spec();
        let slots = Slots::pair(letter_token(&index, "qsTea"), EDGE);
        let mut estimating = Engine::with_modes(
            &index,
            no_features(),
            EngineModes {
                simulated_prospect: false,
                ..EngineModes::default()
            },
        );
        let mut simulating = Engine::new(&index, no_features());
        let estimated = settle_pea(&mut estimating, slots).expect("the fixture settles");
        let simulated = settle_pea(&mut simulating, slots).expect("the fixture settles");
        assert_eq!(estimated, simulated);
        assert_eq!(
            estimated
                .ranking()
                .ranked
                .iter()
                .map(|entry| entry.prospect)
                .collect::<Vec<_>>(),
            [0, 0, 0],
            "a non-letter second slot is as unknowable as it looks, in either mode"
        );
    }

    /// A window in which the follower's replayed settlement raises. `qsTea` exits toward `qsPea`, whose two entry-only stances tie at every score and whose two prefer records each demand one of them. The simulated prospect's settlement of `qsPea` is therefore E-AMBIGUOUS, although `qsTea`'s own window settles. Sweeps over the live alphabet reach the fallback only through replays that raise E-UNACCEPTED-EXIT or E-INCOMPARABLE, never E-AMBIGUOUS.
    fn raising_follower_spec() -> SpecIndex {
        let conflicting = fixtures::policy(&[(
            "prefer",
            &fixtures::seq(&[
                &pointed_record("prefer", "qsPea", 0, &[("stance", "\"stroke\"")]),
                &pointed_record("prefer", "qsPea", 1, &[("stance", "\"flourish\"")]),
            ]),
        )]);
        let entries = || object(&[row("baseline", &[])]);
        let pea = letter(
            "qsPea",
            &[
                stance("stroke", &surface(&entries(), "{}", &[])),
                stance("flourish", &surface(&entries(), "{}", &[])),
            ],
            &conflicting,
        );
        let tea = letter(
            "qsTea",
            &[stance(
                "hook",
                &surface(
                    "{}",
                    &object(&[row("baseline", &[("unjoined", "\"safe\"")])]),
                    &[],
                ),
            )],
            &plain_policy(),
        );
        let may = letter(
            "qsMay",
            &[stance(
                "base",
                &surface(&object(&[row("baseline", &[])]), "{}", &[]),
            )],
            &plain_policy(),
        );
        spec_of(&[pea, tea, may])
    }

    #[test]
    fn a_counterfactual_cascade_that_raises_falls_back_to_the_candidacy_estimate() {
        let index = raising_follower_spec();
        let token = letter_token(&index, "qsTea");
        let slots = Slots::pair(letter_token(&index, "qsPea"), letter_token(&index, "qsMay"));
        let edge = LeftContext::boundary(TokenKind::Edge);
        let baseline = fixtures::sym(&index, "baseline");

        let mut simulating = Engine::new(&index, no_features());
        let trace = simulating
            .transition_trace(&edge, token, slots)
            .expect("the window qsTea settles is not the window that raised");
        assert_eq!(trace.settled.junction, Some(baseline));
        assert_eq!(trace.decided_stage, DecidedStage::JoinCount);
        assert_eq!(
            simulating.simulated_prospect_fallbacks(),
            2,
            "both of qsTea's candidates opened a cascade that raised, and both fell back"
        );

        let mut estimating = Engine::with_modes(
            &index,
            no_features(),
            EngineModes {
                simulated_prospect: false,
                ..EngineModes::default()
            },
        );
        let estimated = estimating
            .transition_trace(&edge, token, slots)
            .expect("the estimate never opens a cascade at all");
        assert_eq!(estimated, trace, "the fallback is the estimate, exactly");
        assert_eq!(estimating.simulated_prospect_fallbacks(), 0);
    }

    /// A prospect memo key is eighteen bytes: the simulated key's slots are ordinals beside one packed word of kinds.
    #[test]
    fn a_memoized_prospect_is_eight_bytes_under_an_eighteen_byte_key() {
        assert_eq!(std::mem::size_of::<ProspectKey>(), 18);
        assert_eq!(std::mem::size_of::<(i8, DeltaId)>(), 8);
    }

    /// Under a trace memo, when the follower's replayed settlement succeeds, the trace memo keeps the follower's window and the prospect memo keeps no entry. A second window asking the same prospects reads them through the trace memo and journals the same delta as the first. When the replayed settlement raises, the prospect memo keeps the fallback estimate, and the second window's asks hit that entry instead of settling the follower again.
    #[test]
    fn a_prospect_keeps_its_entry_only_where_its_cascade_raised() {
        let modes = EngineModes {
            trace_memo: true,
            ..EngineModes::default()
        };
        let edge = LeftContext::boundary(TokenKind::Edge);
        let space = LeftContext::boundary(TokenKind::Space);

        let index = prospect_spec();
        let token = letter_token(&index, "qsPea");
        let slots = Slots::new(
            letter_token(&index, "qsTea"),
            letter_token(&index, "qsMay"),
            letter_token(&index, "qsIt"),
            EDGE,
        );
        let mut settling = Engine::with_modes(&index, no_features(), modes);
        settling
            .transition_trace(&edge, token, slots)
            .expect("the fixture settles");
        assert_eq!(settling.simulated_prospect_fallbacks(), 0);
        assert!(
            settling.prospect_cache.is_empty(),
            "every cascade settled, so every prospect is the trace memo's to answer"
        );
        let windows = settling
            .trace_cache
            .as_ref()
            .expect("trace-memo memoizes")
            .entries
            .len();
        settling
            .transition_trace(&space, token, slots)
            .expect("the fixture settles");
        assert!(settling.prospect_cache.is_empty());
        assert_eq!(
            settling
                .trace_cache
                .as_ref()
                .expect("trace-memo memoizes")
                .entries
                .len(),
            windows + 1,
            "the second window added itself and nothing else: its prospects were read off windows already there"
        );
        assert_eq!(
            settling.trace_delta(&edge, token, slots),
            settling.trace_delta(&space, token, slots),
            "a prospect read through the trace memo replays what its own entry would have"
        );

        let index = raising_follower_spec();
        let token = letter_token(&index, "qsTea");
        let slots = Slots::pair(letter_token(&index, "qsPea"), letter_token(&index, "qsMay"));
        let mut raising = Engine::with_modes(&index, no_features(), modes);
        raising
            .transition_trace(&edge, token, slots)
            .expect("the window qsTea settles is not the window that raised");
        assert_eq!(raising.simulated_prospect_fallbacks(), 2);
        assert_eq!(
            raising.prospect_cache.len(),
            2,
            "both cascades raised, and a raising window is the one the trace memo cannot hold"
        );
        raising
            .transition_trace(&space, token, slots)
            .expect("the window qsTea settles is not the window that raised");
        assert_eq!(
            raising.simulated_prospect_fallbacks(),
            2,
            "the second window's asks hit the memo instead of re-running the cascade that raised"
        );
        assert_eq!(
            raising.trace_delta(&edge, token, slots),
            raising.trace_delta(&space, token, slots)
        );
    }

    /// Without a trace memo, or in candidacy mode, no trace memo entry can answer the next ask, so the prospect memo keeps every ask. Outside trace-memo mode each entry holds the id of the empty delta, because nothing was journaled.
    #[test]
    fn a_prospect_with_no_trace_memo_behind_it_is_memoized_however_it_was_answered() {
        let index = prospect_spec();
        let slots = Slots::new(
            letter_token(&index, "qsTea"),
            letter_token(&index, "qsMay"),
            letter_token(&index, "qsIt"),
            EDGE,
        );
        let mut simulating = Engine::new(&index, no_features());
        settle_pea(&mut simulating, slots).expect("the fixture settles");
        assert!(!simulating.prospect_cache.is_empty());
        assert!(
            simulating
                .prospect_cache
                .values()
                .all(|&(_, id, _)| simulating.deltas.get(id).is_empty())
        );
        let mut estimating = Engine::with_modes(
            &index,
            no_features(),
            EngineModes {
                trace_memo: true,
                simulated_prospect: false,
                ..EngineModes::default()
            },
        );
        settle_pea(&mut estimating, slots).expect("the fixture settles");
        assert!(
            !estimating.prospect_cache.is_empty(),
            "the estimate runs no cascade, so no trace memo could answer its next ask"
        );
    }

    /// The ranking asks each surviving candidate's prospect once: a shared memo that holds every follower window but not the ranked window itself supplies one window per ranked candidate.
    #[test]
    fn the_ranking_asks_each_candidates_prospect_once() {
        let modes = EngineModes {
            trace_memo: true,
            ..EngineModes::default()
        };
        let edge = LeftContext::boundary(TokenKind::Edge);
        let index = prospect_spec();
        let token = letter_token(&index, "qsPea");
        let slots = Slots::new(
            letter_token(&index, "qsTea"),
            letter_token(&index, "qsMay"),
            letter_token(&index, "qsIt"),
            EDGE,
        );
        let mut source = Engine::with_modes(&index, no_features(), modes);
        source
            .transition_trace(&edge, token, slots)
            .expect("the fixture settles");
        let ranked_key = Engine::trace_key(&edge, token.letter_ordinal(), slots);
        let mut memo = source.take_memo().expect("the source journals");
        memo.entries = memo
            .entries
            .iter()
            .filter(|(key, _)| **key != ranked_key)
            .map(|(key, entry)| (*key, *entry))
            .collect::<HashMap<_, _>>()
            .into();
        let mut engine = Engine::with_modes(&index, no_features(), modes);
        engine.attach_shared_memos(vec![SharedMemo {
            memo: std::sync::Arc::new(memo),
            excluded: crate::memo::Exclusion::none(),
        }]);
        let trace = engine
            .transition_trace(&edge, token, slots)
            .expect("the fixture settles");
        let ranked = trace.ranking().ranked.len();
        assert!(ranked > 1, "the fixture ranks several candidates");
        assert_eq!(engine.shared_memo_hits(), ranked as u64);
    }

    #[test]
    fn a_raising_window_is_never_memoized_and_leaves_no_capture_open() {
        let pea_policy = fixtures::policy(&[(
            "prefer",
            &fixtures::seq(&[
                &pointed_record("prefer", "qsPea", 0, &[("stance", "\"stroke\"")]),
                &pointed_record("prefer", "qsPea", 1, &[("stance", "\"flourish\"")]),
            ]),
        )]);
        let index = ranking_spec(&pea_policy, &plain_policy());
        let mut engine = Engine::with_modes(
            &index,
            no_features(),
            EngineModes {
                trace_memo: true,
                ..EngineModes::default()
            },
        );
        let slots = Slots::pair(letter_token(&index, "qsTea"), letter_token(&index, "qsMay"));
        let first = settle_pea(&mut engine, slots).expect_err("the window is ambiguous");
        assert!(engine.capture_starts.is_empty());
        assert!(
            engine
                .fired_log
                .as_ref()
                .expect("trace-memo journals")
                .is_empty(),
            "the aborted capture was the outermost one, so the journal emptied"
        );
        let key = Engine::trace_key(
            &LeftContext::boundary(TokenKind::Edge),
            fixtures::letter(&index, "qsPea").letter_ordinal(),
            slots,
        );
        assert!(
            !engine
                .trace_cache
                .as_ref()
                .expect("trace-memo memoizes")
                .entries
                .contains_key(&key),
            "the raising window itself is not cached, though the simulated windows it opened are"
        );
        assert!(
            engine
                .trace_cache
                .as_ref()
                .is_none_or(|memo| !memo.entries.contains_key(&key))
        );
        assert!(
            engine.fired().contains(&Pointer {
                file: fixtures::sym(&index, "qsPea.yaml"),
                path: fixtures::sym(&index, "policy.prefer[0]"),
            }),
            "the record that applied before the conflict demonstrably fired"
        );
        let again = settle_pea(&mut engine, slots).expect_err("the window is still ambiguous");
        assert_eq!(first, again);
    }

    #[test]
    fn a_warm_trace_replays_exactly_what_a_cold_one_fired() {
        let index = firing_spec();
        let modes = EngineModes {
            trace_memo: true,
            ..EngineModes::default()
        };
        let ss03 = fixtures::sym(&index, "ss03");
        let left = settled_left(&index, "qsTea", "plain", Some("baseline"));
        let token = letter_token(&index, "qsPea");
        let slots = Slots::pair(letter_token(&index, "qsTea"), EDGE);

        let mut cold = Engine::with_modes(&index, [ss03], modes);
        let first = cold
            .transition_trace(&left, token, slots)
            .expect("the fixture settles");
        let cold_delta: Vec<Pointer> = cold
            .trace_delta(&left, token, slots)
            .expect("the window was traced")
            .to_vec();
        assert_eq!(
            cold_delta
                .iter()
                .map(|pointer| pointer.text(&index))
                .collect::<Vec<_>>(),
            [
                "qsPea.yaml:stances.half.entries.baseline",
                "qsPea.yaml:stances.half.unlocks[0]",
                "qsPea.yaml:stances.half.unlocks[1]",
                "qsPea.yaml:stances.half.exits.baseline",
            ]
        );

        let mut warm = Engine::with_modes(&index, [ss03], modes);
        warm.transition_trace(&left, token, slots)
            .expect("the fixture settles");
        warm.begin_capture();
        let again = warm
            .transition_trace(&left, token, slots)
            .expect("the fixture settles");
        assert_eq!(again, first);
        assert_eq!(
            &*warm.end_capture().delta,
            cold_delta.as_slice(),
            "the hit replayed the cold delta into the open capture"
        );
        assert_eq!(warm.fired(), cold.fired());
        assert_eq!(
            warm.trace_delta(&left, token, slots)
                .map(<[Pointer]>::to_vec),
            Some(cold_delta),
            "the memo's delta is order-independent, so a hit fires what the computation did"
        );
    }

    #[test]
    fn a_left_that_committed_a_junction_nothing_accepts_is_an_unaccepted_exit() {
        let index = ranking_spec(&plain_policy(), &plain_policy());
        let mut engine = Engine::new(&index, no_features());
        let error = engine
            .transition_trace(
                &committed_left(&index, 0),
                letter_token(&index, "qsMay"),
                Slots::pair(EDGE, EDGE),
            )
            .expect_err("qsMay enters at the baseline alone");
        assert_eq!(error.kind(), SettleErrorKind::UnacceptedExit);
        assert_eq!(
            error.message(),
            "E-UNACCEPTED-EXIT: qsPea.stroke.ex-y5 committed an exit at x-height but qsMay has no acceptor cell (the lookahead closure should have prevented this commitment)"
        );
    }

    #[test]
    fn a_window_with_no_candidates_and_an_unmodeled_input_are_plain_settle_errors() {
        let index = spec_of(&[letter(
            "qsPea",
            &[stance(
                "stroke",
                &surface("{}", "{}", &[("require", &fixtures::names(&["entry"]))]),
            )],
            &plain_policy(),
        )]);
        let mut engine = Engine::new(&index, no_features());
        let error = settle_pea(&mut engine, Slots::pair(EDGE, EDGE))
            .expect_err("the only stance requires an entry the run edge cannot give it");
        assert_eq!(error.kind(), SettleErrorKind::Plain);
        assert_eq!(
            error.message(),
            "qsPea has no candidate cells at all in this window"
        );

        let error = engine
            .transition_trace(
                &LeftContext::boundary(TokenKind::Edge),
                letter_token(&index, "qsIt"),
                Slots::pair(EDGE, EDGE),
            )
            .expect_err("the registry knows qsIt, but this spec does not model it");
        assert_eq!(error.kind(), SettleErrorKind::Plain);
        assert_eq!(error.message(), "qsIt is not a modeled rune");
    }
}
