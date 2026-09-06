//! The settlement engine, and the only implementation of settlement there is: the three-valued condition matching, the capability reads that decide what a stance can offer, the refusals, the candidate enumeration, the refusal-aware lookahead closure that makes mutuality definitional, and on top of those the strictly lexicographic ranking — absolute prefers, then the window join count whose third term is the follower's own simulated choice, then yielding prefers, then the runes' declared order, then the structural floor — with the adjustments and the commit that turn the winner into a settled cell.
//!
//! The ranking's stages are lexicographic and each one narrows the survivor list the next reads, so the order they run in is the whole semantics and `decided_stage` names the stage that got the list down to one. Two of them can refuse to decide rather than guess: prefer records that demand different outcomes at non-nested specificity are E-AMBIGUOUS within one rune and E-INCOMPARABLE across two, and the messages those raise are contract down to the paste-ready `resolve:` stub they print, because the author's next move is to copy it into the rune's YAML.
//!
//! An engine is one (spec, feature configuration) pair, and the spec arrives as a [`SpecIndex`] the engine borrows rather than owns, so the guard's whole engine powerset and every replay share one index and one string pool. Everything the engine itself holds is cache: the caches exist because the table build's fixpoint asks the same questions about the same windows thousands of times. The trace memo is also what one engine may read of another's: a finished memo leaves its engine as a [`crate::memo::MemoSnapshot`] and enters another as a base, read behind an exclusion naming the runes the two engines do not settle alike ([`Engine::seed_bases`]), which is how a configuration enumerates as a delta over `default`. The module-level `id()`-keyed dictionaries Python used to fake per-spec state are ordinary fields here, because [`StanceId`] is a stable seat pair rather than a recyclable address and therefore needs neither an identity re-check nor an LRU cap.
//!
//! The fired-provenance journal is the subtle part and is contract rather than bookkeeping. `fired` is the set of authored records that demonstrably fired under this configuration, and the dead-policy gate reads it, so a memoized sub-result must not silently swallow the firings its first evaluation performed: every cache entry stores the delta its computation journaled, and every hit replays that delta into whatever capture is open — and into the set itself only where the set may not hold it yet, which is a base entry's delta on its first hit, an own entry's pointers having entered the set when the entry was recorded. That is what makes each entry's delta order-independent and a warm engine's `fired` equal to a cold one's. The journal only runs in trace-memo mode, because that is the only mode where anything asks for a per-evaluation delta; outside it there is no journal, and — following Python exactly — [`Engine::candidates`] does not consult its cache at all, since the entry it would store could not carry a delta to replay.
//!
//! A pointer is Python's `str(Provenance)`, the `file:path` spelling. It rides here as a [`Pointer`], the two symbols side by side, rather than as the composed string: the pair is what the provenance already is, it is `Copy` and hashes on two integers, and the string is built only where one is emitted. Nothing else in the engine holds an owned `String` except the elimination descriptions, whose exact wording is contract against the Python original and which are read by people rather than keyed on.
//!
//! Three raises live in this half, all of them spec defects rather than settlement outcomes, and all three keep Python's sentence: a left condition carrying `then:`, a right condition carrying a left-only axis, and an unresolvable class name (which [`SpecIndex::class_members`] raises). Everything else here answers rather than raises — an unavailable entry, a forbidden pairing, a closed-out exit, and a refusal are all eliminations, and a window with no candidates at all is the ranking's problem, not enumeration's.

use std::collections::{HashMap, HashSet};

use crate::error::SettleError;
use crate::index::{SpecIndex, StanceId};
use crate::memo::{MemoBase, MemoSnapshot};
use crate::model::{
    Condition, PolicyRecord, Provenance, Rune, Stance, SurfaceRow, Sym, Table, When,
};
use crate::specificity;
use crate::types::{
    AdjustmentToken, Candidate, CellId, DecidedStage, Elimination, EliminationStage, LeftContext,
    NotesPool, NotesSeat, RankedCandidate, RightToken, Settled, SettledPool, SettledSeat, Side,
    TokenKind, TraceLadder, TransitionTrace, UNKNOWN, Vocab, boundary_settled, cell_label,
    provenance_pointer, word_position,
};

/// Where a candidate enumeration's eliminations go, together with whether their sentences are wanted at all. Separating the two is what lets the table fixpoint keep every elimination's stage and pointer — the notes on a row are built from those — while formatting none of the prose that names them.
struct EliminationSink<'a> {
    list: Option<&'a mut Vec<Elimination>>,
    describe: bool,
}

/// One authored record's YAML pointer, as the fired set and every journaled delta hold it — `model.Provenance`'s two halves, kept apart so the value is `Copy` and hashes on two integers instead of on a composed string. [`Pointer::text`] builds the `file:path` spelling Python's `str(Provenance)` gives, which is what the corpus and the notes carry.
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

    /// The `file:path` spelling, which is the only form that reaches an output.
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

/// One collection's occupancy, as the `--cache-census` diagnostic reports it. A capacity beside a length is what tells a table that is merely large apart from one that is mostly empty slack, which is the difference between a shrink worth taking and one that would buy nothing.
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

    /// The line the census writes for it.
    pub fn line(&self, config: &str) -> String {
        format!(
            "[c] {config} {} len={} cap={}",
            self.name, self.len, self.capacity
        )
    }
}

/// The four raw lookahead slots one window is read against. Python passes them as four parameters with the deeper two defaulting to `UNKNOWN`; bundling them is what keeps the deep-window discipline visible at every call site, because a caller that has only two honest slots writes [`Slots::pair`] and thereby says out loud that the rest of the window is unknown rather than quietly reaching for something it does not know.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Slots {
    pub right1: RightToken,
    pub right2: RightToken,
    pub right3: RightToken,
    pub right4: RightToken,
}

impl Slots {
    /// All four slots spelled out.
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

    /// The two-slot window every capability and refusal read is evaluated against, with the deeper two at their honest `UNKNOWN` — Python's defaulted `right3` / `right4` parameters.
    pub fn pair(right1: RightToken, right2: RightToken) -> Self {
        Self::new(right1, right2, UNKNOWN, UNKNOWN)
    }

    /// The slots as the token run a right condition walks, one raw token per `then:` hop.
    pub fn as_array(self) -> [RightToken; 4] {
        [self.right1, self.right2, self.right3, self.right4]
    }
}

/// The window past the supplied slots, which a `then:` chain exhausts to: the tail [`Engine::cond_matches_right`] walks once the supplied slots run out.
const UNKNOWN_TAIL: [RightToken; 1] = [UNKNOWN];

/// The mode pins an engine is built with. Python reads two of these from module-level defaults that an environment variable moves; the crate has no environment to read, so the caller passes them and the [`Default`] spelling is the shipping configuration.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct EngineModes {
    /// The follower vote's beyond-`right1` slot when `vote_slots` is off. `UNKNOWN` is the optimistic comparison state; the section 5.7 guard's engines pin it to `EDGE` so a vote needing deeper text than the verdict is keyed on can never flip a formation verdict.
    pub vote_deep_slot: RightToken,
    /// Whether the third join-count term is the follower's actual simulated transition (issue 28's shipping default) rather than the pre-issue-28 optimistic candidacy estimate.
    pub simulated_prospect: bool,
    /// Whether a follower vote is evaluated over the seat's real shifted slots rather than pinning everything past its own `right1` to `vote_deep_slot`.
    pub vote_slots: bool,
    /// Whether the engine memoizes whole windows and journals a fired delta per memoized evaluation. Off everywhere but the table fixpoint and the case replay.
    pub trace_memo: bool,
    /// Whether a trace carries its explain ladder — the ranking, the eliminations with their sentences, and the runner-up. On everywhere a person reads a trace — the explain report, the review surface, the probe; off in the table fixpoint, whose rows read the settled triple, the prospect, the joint floor and the notes, and nothing else. Formatting a ladder nobody reads is the enumeration's largest avoidable allocation, so this is where that decision is spelled.
    pub explain_ladder: bool,
}

impl Default for EngineModes {
    fn default() -> Self {
        Self {
            vote_deep_slot: UNKNOWN,
            simulated_prospect: true,
            vote_slots: true,
            trace_memo: false,
            explain_ladder: true,
        }
    }
}

/// One exit a stance can offer: a declared row at its declaration seat, or a row-less height an active unlock grants at a seat past the declared ones. The `Unlock` behind such a height is deliberately not carried, because no caller reads it — the unlock's only observable effect is the provenance the enumeration fires, which the cache already replays.
#[derive(Clone, Copy, Debug)]
struct ExitSource<'i> {
    height: Sym,
    row: Option<&'i SurfaceRow>,
    index: usize,
}

/// One stance's pairing rules, resolved to the pairs the check compares against. Python keeps these in a module-level LRU keyed on `id(stance)` with an identity re-check; a [`StanceId`] cannot be recycled, so this is an ordinary per-engine map with no cap and no dance.
#[derive(Clone, Debug)]
struct PairingSets {
    never: HashSet<(Sym, Sym)>,
    only: Option<HashSet<(Sym, Sym)>>,
}

/// The seat one distinct candidate list holds in the candidate memo's list pool, and what a memoized enumeration holds in place of its candidates. Under half a million entries a configuration name a few dozen distinct lists between them, so an entry that owned its list was holding a vector hundreds of thousands of its neighbors held too, where four bytes name the same one (issue #167). The offset is the pool's business alone: [`CandidateListSeat::at`] and [`CandidateListSeat::index`] are the two crossings.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct CandidateListSeat(u32);

impl CandidateListSeat {
    /// The seat for the pool's `index`-th list.
    fn at(index: usize) -> Self {
        Self(
            u32::try_from(index)
                .expect("a configuration enumerates fewer than 2^32 distinct candidate lists"),
        )
    }

    /// The seat as the pool's index.
    fn index(self) -> usize {
        self.0 as usize
    }
}

/// The table the candidate memo seats its candidate lists through, the same shape as [`NotesPool`] for the same reason: every distinct list once, in the order an enumeration first produced it, and the seat each one holds. A hit clones the list out of the table exactly as it cloned it out of the entry, so seating it moves nothing about what a hit answers.
#[derive(Clone, Debug, Default)]
struct CandidateListPool {
    seats: HashMap<Vec<Candidate>, CandidateListSeat>,
    table: Vec<Vec<Candidate>>,
}

impl CandidateListPool {
    /// This list's seat, minted on the first enumeration that produced it and answered from the map on every later one. The list arrives owned because the enumeration is done with it: a miss keeps the allocation and a hit drops it.
    fn seat(&mut self, candidates: Vec<Candidate>) -> CandidateListSeat {
        if let Some(&seat) = self.seats.get(candidates.as_slice()) {
            return seat;
        }
        let seat = CandidateListSeat::at(self.table.len());
        self.seats.insert(candidates.clone(), seat);
        self.table.push(candidates);
        seat
    }

    /// The list one seat names.
    fn get(&self, seat: CandidateListSeat) -> &[Candidate] {
        &self.table[seat.index()]
    }

    /// How many distinct lists have been seated.
    fn len(&self) -> usize {
        self.table.len()
    }

    /// How many the table has room for, which is what the cache census reports beside the length.
    fn capacity(&self) -> usize {
        self.table.capacity()
    }
}

/// The seat one distinct elimination list holds in the candidate memo's elimination pool, and what a memoized enumeration holds in place of its eliminations. The pool is keyed on the whole list — every elimination's stage, sentence and provenance — so explain mode, where the sentences are real prose, stays exact; in fixpoint mode the sentences are empty and a list is its stages and its pointers, which is why a configuration's entries share a few dozen lists between them (issue #167).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct EliminationListSeat(u32);

impl EliminationListSeat {
    /// The seat for the pool's `index`-th list.
    fn at(index: usize) -> Self {
        Self(
            u32::try_from(index)
                .expect("a configuration enumerates fewer than 2^32 distinct elimination lists"),
        )
    }

    /// The seat as the pool's index.
    fn index(self) -> usize {
        self.0 as usize
    }
}

/// The table the candidate memo seats its elimination lists through, the same shape as [`CandidateListPool`] for the same reason. A hit extends the caller's eliminations from the list where it sits, exactly as it extended them from the entry.
#[derive(Clone, Debug, Default)]
struct EliminationListPool {
    seats: HashMap<Vec<Elimination>, EliminationListSeat>,
    table: Vec<Vec<Elimination>>,
}

impl EliminationListPool {
    /// This list's seat, minted on the first enumeration that produced it and answered from the map on every later one. The list arrives owned because the enumeration is done with it: a miss keeps the allocation and a hit drops it, and neither copies a sentence.
    fn seat(&mut self, eliminations: Vec<Elimination>) -> EliminationListSeat {
        if let Some(&seat) = self.seats.get(eliminations.as_slice()) {
            return seat;
        }
        let seat = EliminationListSeat::at(self.table.len());
        self.seats.insert(eliminations.clone(), seat);
        self.table.push(eliminations);
        seat
    }

    /// The list one seat names.
    fn get(&self, seat: EliminationListSeat) -> &[Elimination] {
        &self.table[seat.index()]
    }

    /// Every distinct list once, in seat order — what [`Engine::elimination_text_bytes`] measures, since a list is held once however many entries name it.
    fn lists(&self) -> &[Vec<Elimination>] {
        &self.table
    }

    /// How many distinct lists have been seated.
    fn len(&self) -> usize {
        self.table.len()
    }

    /// How many the table has room for, which is what the cache census reports beside the length.
    fn capacity(&self) -> usize {
        self.table.capacity()
    }
}

/// What the candidate memo holds per window: seats for the candidate list, the elimination list and the fired delta — twelve bytes and no heap. An entry holding an `Rc` over the two lists and a boxed delta is four heap allocations and a reference-count header behind every table slot, and an instrumented run of that layout said what it holds: under half a million entries a configuration sharing a few dozen distinct candidate lists, a few dozen distinct elimination lists and a few thousand distinct deltas, so nearly every entry owned a private copy of what hundreds of thousands of its neighbors owned too (issue #167). The delta is what makes the entry replayable: a later window that hits this key never runs the enumeration, so without replaying the delta those records would read as dead.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct CandidatesEntry {
    candidates: CandidateListSeat,
    eliminations: EliminationListSeat,
    delta: DeltaSeat,
}

/// The candidate memo and the two list pools its entries seat into, released as one piece for the reason the trace memo's pools ride with its entries: a seat means nothing without the table it indexes. The delta seat resolves through [`Engine::deltas`] rather than a pool of this memo's own, because three memos journal deltas and one table holds each distinct delta once, whichever memo journaled it first.
#[derive(Clone, Debug, Default)]
struct CandidatesMemo {
    entries: HashMap<CandidatesKey, CandidatesEntry>,
    candidates: CandidateListPool,
    eliminations: EliminationListPool,
}

/// The candidate memo's key, which collapses the left exactly as Python's does: the kind, and the settled cell's rune, stance and seam. The left's entry, its adjustments and its extension are deliberately absent — enumeration reads none of them, so two settled lefts differing only there enumerate identically and share one entry. The trace memo's key keeps the extension, because the commit's same-seam suppression does read it.
///
/// It is packed to twenty-eight bytes the way [`TraceKey`] is (issue #167): the two slots ride as their kinds beside their `Option<Sym>` runes, which spells each token exactly once, so two windows collide exactly when their slots are equal and the pair costs four bytes a slot instead of the eight a tagged enum pads to. Nothing reads a key back: it is compared and hashed and never resolved.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct CandidatesKey {
    left_kind: TokenKind,
    left_rune: Option<Sym>,
    left_stance: Option<Sym>,
    left_seam: Option<Sym>,
    rune: Sym,
    kinds: [TokenKind; 2],
    runes: [Option<Sym>; 2],
}

/// The lookahead closure's key: the candidate we are proposing, spelled out, plus the follower and the raw slot past it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct ClosureKey {
    rune: Sym,
    stance: Sym,
    entry: Option<Sym>,
    seam: Option<Sym>,
    right1: Sym,
    right2: RightToken,
}

/// The trace memo's key: the collapsed left with its extension, the input rune, and all four raw slots. Every window read the kernel makes goes through these fields, which is why lefts differing only in their cell's entry or adjustments may share one entry.
///
/// It is packed to forty bytes because the memo holds one per window over a million and more windows a configuration (issue #165). The four slots ride as their kinds beside their `Option<Sym>` runes, which is what a [`RightToken`] is — a letter is its kind with a rune, and every other kind carries none — so the pair spells each token exactly once and two windows collide exactly when their slots are equal, in four bytes a slot instead of the eight a tagged enum pads to. The extension is an `i16` because it is a count of connector pixels. Nothing reads a key back: it is compared and hashed and never resolved.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub(crate) struct TraceKey {
    pub(crate) left_kind: TokenKind,
    pub(crate) left_rune: Option<Sym>,
    pub(crate) left_stance: Option<Sym>,
    pub(crate) left_seam: Option<Sym>,
    pub(crate) left_extension: i16,
    pub(crate) token: Sym,
    pub(crate) kinds: [TokenKind; 4],
    pub(crate) runes: [Option<Sym>; 4],
}

impl TraceKey {
    /// Every rune this key names — the left cell's, the input, and each letter slot's — which is every rune file the engine reads while settling the window, and so the whole of what a [`crate::memo::Exclusion`] tests.
    pub(crate) fn runes_named(&self) -> impl Iterator<Item = Sym> + '_ {
        self.left_rune
            .into_iter()
            .chain(std::iter::once(self.token))
            .chain(self.runes.iter().flatten().copied())
    }

    /// A key over a boundary left and the given slots, for a test that wants one without an engine.
    #[cfg(test)]
    pub(crate) fn for_test(token: Sym, runes: [Option<Sym>; 4]) -> Self {
        Self {
            left_kind: TokenKind::Edge,
            left_rune: None,
            left_stance: None,
            left_seam: None,
            left_extension: 0,
            token,
            kinds: runes.map(|rune| rune.map_or(TokenKind::Edge, |_| TokenKind::Letter)),
            runes,
        }
    }
}

/// The prospect memo's key, in the two shapes the two candidacy worlds need. An engine's mode is fixed at construction, so only one of them ever occurs on any given engine, and one map holds both; the asymmetry between them is the terms' own — the candidacy key ends in `right2.letter`, because the estimate reads nothing past the follower's own right, while the simulated key carries the whole token and the two slots behind it, because the cascade it runs does.
///
/// The simulated key's three slots ride as their kinds beside their `Option<Sym>` runes, the way [`TraceKey`] carries its four (issue #166): the pair spells each token exactly once, so two asks collide exactly when their slots are equal, in four bytes a slot instead of the eight a tagged enum pads to, which is what brings the key down to thirty-six bytes. Nothing reads a key back: it is compared and hashed and never resolved.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum ProspectKey {
    Candidacy {
        rune: Sym,
        stance: Sym,
        entry: Option<Sym>,
        seam: Option<Sym>,
        right1: Sym,
        right2: Sym,
    },
    Simulated {
        rune: Sym,
        stance: Sym,
        entry: Option<Sym>,
        seam: Option<Sym>,
        right1: Sym,
        kinds: [TokenKind; 3],
        runes: [Option<Sym>; 3],
    },
}

/// How one prospect ask was answered, which [`Engine::prospect`] reads to decide whether the answer is worth an entry of its own (issue #166). A term read off the follower's simulated trace left that trace in the trace memo, in a mode that has one, and the next ask reads it there; a term the candidacy estimate answered — because the mode asks for nothing else, or because the cascade raised and fell back — left nothing behind that a later ask could read, so the memo keeps it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ProspectTerm {
    Simulated,
    Estimated,
}

/// Which of a rune's two adjustment lists an adjustment is picked from. Python passes the kind as a string and chooses the list from it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum AdjustmentKind {
    Extend,
    Contract,
}

/// One policy record together with the rune that owns it — how the prefer stage gathers records from both seam runes, and how the two colliding ones reach the resolution and the message.
#[derive(Clone, Copy, Debug)]
struct OwnedRecord<'i> {
    owner: Sym,
    record: &'i PolicyRecord,
}

/// One gathered prefer record that speaks to this window: it is relevant, it favors something, and what it favors is strictly narrower than the survivor list — the three tests `_apply_prefers` admits a record by. `favored` is a set because the narrowing only ever asks it for membership.
struct Applicable<'i> {
    owner: Sym,
    record: &'i PolicyRecord,
    favored: HashSet<Candidate>,
}

/// The seat one distinct fired delta holds in the engine's delta pool, and what a memoized window, enumeration, prospect or closure verdict holds in place of its delta. A configuration's million and more memoized windows journal a few tens of thousands of distinct deltas between them, so an entry that owned its delta was holding a boxed slice hundreds of its neighbors held too, where four bytes name the same one (issue #165). The offset is the pool's business alone: [`DeltaSeat::at`] and [`DeltaSeat::index`] are the two crossings.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) struct DeltaSeat(u32);

impl DeltaSeat {
    /// The seat for the pool's `index`-th delta.
    pub(crate) fn at(index: usize) -> Self {
        Self(
            u32::try_from(index)
                .expect("a configuration journals fewer than 2^32 distinct fired deltas"),
        )
    }

    /// The seat as the pool's index.
    pub(crate) fn index(self) -> usize {
        self.0 as usize
    }
}

/// The table every memoized fired delta is seated through, the same shape as [`NotesPool`] for the same reason: every distinct delta once, in the order an evaluation first journaled it, and the seat each one holds. A replay reads the delta where it sits, so seating it moves nothing about what a hit fires.
#[derive(Clone, Debug, Default)]
struct DeltaPool {
    seats: HashMap<Box<[Pointer]>, DeltaSeat>,
    table: Vec<Box<[Pointer]>>,
}

impl DeltaPool {
    /// This delta's seat, minted on the first evaluation that journaled it and answered from the map on every later one. The delta arrives owned because the capture that produced it is closed: a miss keeps the allocation and a hit drops it.
    fn seat(&mut self, delta: Box<[Pointer]>) -> DeltaSeat {
        if let Some(&seat) = self.seats.get(&*delta) {
            return seat;
        }
        let seat = DeltaSeat::at(self.table.len());
        self.seats.insert(delta.clone(), seat);
        self.table.push(delta);
        seat
    }

    /// The delta one seat names.
    fn get(&self, seat: DeltaSeat) -> &[Pointer] {
        &self.table[seat.index()]
    }

    /// How many distinct deltas have been seated.
    fn len(&self) -> usize {
        self.table.len()
    }

    /// How many the table has room for, which is what the cache census reports beside the length.
    fn capacity(&self) -> usize {
        self.table.capacity()
    }
}

/// What the trace memo holds per window: seats into the memo's two pools for the settled record and the notes and into [`Engine::deltas`] for the fired delta, the prospect as the byte its zero-or-one range needs, the joint flag and the stage — sixteen bytes and no heap. An entry holding the whole [`TransitionTrace`] by value beside a boxed delta is what an instrumented run of that layout measured: over a million entries a configuration naming a couple of hundred distinct settled records, about a hundred distinct notes lists and a few tens of thousands of distinct deltas, so nearly every entry held by value what hundreds of its neighbors held too (issue #165). The ladder is not here at all: it exists only where the engine was built with [`EngineModes::explain_ladder`], which the fixpoint never is, so it lives in [`TraceMemo::ladders`] rather than as an empty slot on every entry the fixpoint records.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct TraceEntry {
    pub(crate) settled: SettledSeat,
    pub(crate) notes: NotesSeat,
    pub(crate) delta: DeltaSeat,
    pub(crate) prospect: i8,
    pub(crate) joint_floor: bool,
    pub(crate) decided_stage: DecidedStage,
}

/// The window memo and its fired journal in one table, with the settled and notes pools its entries seat into beside it. The delta rides in the entry as a seat rather than in a shadow map on the same forty-byte key, which would cost the key and its hashbrown slack a second time for a value that is only ever read alongside the trace it belongs to; it resolves through [`Engine::deltas`] rather than a pool of this memo's own because the candidate and closure memos seat their deltas in the same table (issue #167). The two pools here are the memo's own rather than a fixpoint's because the memo outlives no fixpoint and is released as one piece. The ladders map is keyed on the same key and is populated only in explain-ladder mode, so a fixpoint's memo carries no ladder slot per entry and an explain-mode hit still answers with the ladder its miss recorded.
#[derive(Clone, Debug, Default)]
struct TraceMemo {
    entries: HashMap<TraceKey, TraceEntry>,
    settled: SettledPool,
    notes: NotesPool,
    ladders: HashMap<TraceKey, Box<TraceLadder>>,
}

impl TraceMemo {
    /// Record one settled window: the trace's two heavy halves seated, the ladder set aside where the trace carries one, and the seat of the delta the capture journaled for it.
    fn insert(&mut self, key: TraceKey, trace: &TransitionTrace, delta: DeltaSeat) {
        let entry = TraceEntry {
            settled: self.settled.seat(&trace.settled),
            notes: self.notes.seat(trace.notes.clone()),
            delta,
            prospect: i8::try_from(trace.prospect)
                .expect("a prospect is a seam count, zero or one"),
            joint_floor: trace.joint_floor,
            decided_stage: trace.decided_stage,
        };
        self.entries.insert(key, entry);
        if let Some(ladder) = &trace.ladder {
            self.ladders.insert(key, ladder.clone());
        }
    }

    /// The trace one entry stands for, rebuilt out of the pools exactly as its miss returned it — the settled record and the notes cloned out, the prospect widened back to the `i64` the ranking sums, and the ladder read back from the side map where one was recorded.
    fn trace(&self, key: &TraceKey, entry: TraceEntry) -> TransitionTrace {
        TransitionTrace {
            settled: self.settled.get(entry.settled).clone(),
            joint_floor: entry.joint_floor,
            prospect: i64::from(entry.prospect),
            decided_stage: entry.decided_stage,
            notes: self.notes.get(entry.notes).to_vec(),
            ladder: self.ladders.get(key).cloned(),
        }
    }
}

/// One settlement engine per (spec, feature configuration).
pub struct Engine<'i> {
    index: &'i SpecIndex,
    features: HashSet<Sym>,
    vote_deep_slot: RightToken,
    simulated_prospect: bool,
    vote_slots: bool,
    simulated_prospect_fallbacks: u64,
    fired: HashSet<Pointer>,
    fired_log: Option<Vec<Pointer>>,
    capture_starts: Vec<usize>,
    /// The one table every memoized fired delta is seated through, whichever memo journaled it: the trace memo's, the candidate memo's, the prospect memo's and the lookahead closure's entries all hold a [`DeltaSeat`] into it, so a delta four memos journaled is held once. It is the engine's rather than the trace memo's, where issue #165 introduced it, because the closure and prospect memos store entries in every mode, and outside trace-memo mode — where nothing is journaled and no trace memo exists — they seat the empty delta here, the one delta a journal-less engine can hold, so that engine's pool never grows past it (issue #167). [`Engine::release_memos`] resets the pool beside every memo that seats into it, so no seat outlives its table.
    deltas: DeltaPool,
    /// Each of the small memos carries its own fired delta beside its verdict rather than in a shadow map on the same key: the delta is only ever read alongside the verdict it belongs to, and a second table would pay for the key and its hashbrown slack twice over. The closure, candidate and prospect memos hold theirs as seats into [`Engine::deltas`], as the trace memo does.
    closure_cache: HashMap<ClosureKey, (bool, DeltaSeat)>,
    candidates_cache: CandidatesMemo,
    /// The prospect memo: the term as the byte its zero-or-one range needs, beside the seat of its fired delta (issue #166). Under a trace memo in simulated-prospect mode it holds only the asks whose cascade raised: a settling cascade's answer is one field of a window the trace memo holds, so [`Engine::prospect`] declines the entry and reads it there on the next ask — or, for a probe's ask, whose window the trace memo declines in turn (issue #168), settles it again, which the probe arms' own memos make rare.
    prospect_cache: HashMap<ProspectKey, (i8, DeltaSeat)>,
    exit_sources_cache: HashMap<StanceId, (Vec<ExitSource<'i>>, Vec<Pointer>)>,
    virtual_left_cache: HashMap<(Sym, Candidate), LeftContext>,
    pairing_sets: HashMap<StanceId, PairingSets>,
    explain_ladder: bool,
    /// The window memo, present in trace-memo mode alone. It is the engine's largest pile and the enumeration's high-water mark, which is why its entries are seats into the pools the [`TraceMemo`] carries beside them rather than whole traces (issue #165): a hit rebuilds the trace out of the pools, and everything a caller sees is what it saw when the entry held the trace by value.
    trace_cache: Option<TraceMemo>,
    /// The finished memos of other enumerations this engine may read, in the order it reads them, each behind the exclusion that says which keys it may not answer ([`crate::memo`]). A window the engine's own memo misses is looked up here before it is settled, and a hit is answered — delta replayed — exactly as an own hit is, without being copied into the own memo: the bases are shared read-only across a whole fan-out, and a copy per hit would rebuild the pile the sharing exists to avoid.
    bases: Vec<MemoBase>,
    /// Per base, which of its delta seats this engine has replayed into its fired set, so a base delta hit for the second time costs the set nothing.
    base_fired: Vec<Vec<bool>>,
    /// How many windows the bases answered, for the cache census.
    base_hits: u64,
}

impl<'i> Engine<'i> {
    /// An engine over one spec and one feature configuration, in the shipping modes: both issue-28 flags on, the vote's deep slot at its honest `UNKNOWN`, and no trace memo.
    pub fn new(index: &'i SpecIndex, features: impl IntoIterator<Item = Sym>) -> Self {
        Self::with_modes(index, features, EngineModes::default())
    }

    /// An engine with its modes spelled out — what the guard's dedicated engines, the table fixpoint and the case replay build.
    pub fn with_modes(
        index: &'i SpecIndex,
        features: impl IntoIterator<Item = Sym>,
        modes: EngineModes,
    ) -> Self {
        Self {
            index,
            features: features.into_iter().collect(),
            vote_deep_slot: modes.vote_deep_slot,
            simulated_prospect: modes.simulated_prospect,
            vote_slots: modes.vote_slots,
            simulated_prospect_fallbacks: 0,
            fired: HashSet::new(),
            fired_log: modes.trace_memo.then(Vec::new),
            capture_starts: Vec::new(),
            deltas: DeltaPool::default(),
            closure_cache: HashMap::new(),
            candidates_cache: CandidatesMemo::default(),
            prospect_cache: HashMap::new(),
            exit_sources_cache: HashMap::new(),
            virtual_left_cache: HashMap::new(),
            pairing_sets: HashMap::new(),
            explain_ladder: modes.explain_ladder,
            trace_cache: modes.trace_memo.then(TraceMemo::default),
            bases: Vec::new(),
            base_fired: Vec::new(),
            base_hits: 0,
        }
    }

    /// Hand this engine the finished memos it may read windows out of, in lookup order. Only a trace-memo engine reads them — outside that mode nothing journals, and a base hit has a delta to replay — so seeding any other engine is refused rather than quietly ignored.
    pub fn seed_bases(&mut self, bases: Vec<MemoBase>) {
        assert!(
            self.trace_cache.is_some(),
            "a memo base can only answer an engine that journals, which is the trace-memo engine"
        );
        self.base_fired = bases
            .iter()
            .map(|base| vec![false; base.memo.deltas.len()])
            .collect();
        self.bases = bases;
    }

    /// How many windows the bases answered so far.
    pub fn base_hits(&self) -> u64 {
        self.base_hits
    }

    /// This engine's trace memo, detached: the entries and the tables their seats index, together with every fired delta the engine seated. Every other memo is released at the same time, as [`Engine::release_memos`] releases it, because the candidate, closure and prospect memos seat their deltas in the table that leaves with the snapshot. `None` for an engine without a trace memo. The bases are not folded in: a snapshot is what this engine settled itself, and a caller that wants the union reads the bases beside it.
    pub fn take_memo(&mut self) -> Option<MemoSnapshot> {
        let memo = self.trace_cache.take()?;
        let deltas = std::mem::take(&mut self.deltas);
        self.trace_cache = Some(TraceMemo::default());
        self.candidates_cache = CandidatesMemo::default();
        self.prospect_cache = HashMap::new();
        self.closure_cache = HashMap::new();
        Some(MemoSnapshot {
            entries: memo.entries,
            settled: memo.settled.into_table(),
            notes: memo.notes.into_table(),
            deltas: deltas.table,
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

    /// Whether the third join-count term is the follower's simulated transition rather than the candidacy-grain estimate.
    pub fn simulated_prospect(&self) -> bool {
        self.simulated_prospect
    }

    /// Whether follower votes read the seat's real shifted slots.
    pub fn vote_slots(&self) -> bool {
        self.vote_slots
    }

    /// The pin a follower vote's beyond-`right1` slots take when [`Engine::vote_slots`] is off.
    pub fn vote_deep_slot(&self) -> RightToken {
        self.vote_deep_slot
    }

    /// How often a simulated prospect's counterfactual cascade raised and fell back to the candidacy-grain estimate. Diagnostic only, exactly as in Python.
    pub fn simulated_prospect_fallbacks(&self) -> u64 {
        self.simulated_prospect_fallbacks
    }

    /// Whether this engine memoizes whole windows and journals a delta per memoized evaluation.
    pub fn trace_memo(&self) -> bool {
        self.trace_cache.is_some()
    }

    /// Drop every memo this engine holds, keeping the fired set and the small per-spec tables. What follows a fixpoint's last trace is a drain and a sort of the whole product, and holding the window memos alive across it is the enumeration's peak — two working sets that never need to coexist.
    ///
    /// A memo is a pure cache here: every entry replays the pointers its computation fired, so a cleared one re-fires them rather than swallowing them, and nothing a later evaluation decides can move. Callers still snapshot [`Engine::fired`] before releasing, because re-firing after the snapshot is what makes the release invisible rather than merely harmless. The trace memo's pools and its ladders go with its entries and the candidate memo's list pools with its, since a seat means nothing without the table it indexes, and the delta pool goes after every memo that seats into it.
    pub fn release_memos(&mut self) {
        self.trace_cache = self.trace_cache.as_ref().map(|_| TraceMemo::default());
        self.candidates_cache = CandidatesMemo::default();
        self.prospect_cache = HashMap::new();
        self.closure_cache = HashMap::new();
        self.deltas = DeltaPool::default();
    }

    /// Every authored record that demonstrably fired under this configuration — refusals that killed a candidate, unlocks that granted capability, row scopes that admitted a side, and the adjustments and prefers that shaped a committed cell. The dead-policy gate reads this; iteration order never reaches an output, which is why a plain hash set serves.
    pub fn fired(&self) -> &HashSet<Pointer> {
        &self.fired
    }

    /// The fired-pointer delta this engine journaled while settling one window, in first-fired order, or `None` when that window was never traced through this engine. This is the per-window delta the corpus carries beside its result; it exists only in trace-memo mode, since only there is anything journaled.
    pub fn trace_delta(
        &self,
        left: &LeftContext,
        token: RightToken,
        slots: Slots,
    ) -> Option<&[Pointer]> {
        let memo = self.trace_cache.as_ref()?;
        let entry = memo
            .entries
            .get(&Self::trace_key(left, token.rune()?, slots))?;
        Some(self.deltas.get(entry.delta))
    }

    /// Every memo this engine holds, as `--cache-census` reports them: the entries each one carries and the buckets it carries them in. The order is the declaration order of the fields, with each memo's pools following its entries in their own declaration order, so two runs' censuses line up row for row.
    pub fn cache_census(&self) -> Vec<CacheSize> {
        let mut out = vec![
            CacheSize::of("fired", self.fired.len(), self.fired.capacity()),
            CacheSize::of("deltas", self.deltas.len(), self.deltas.capacity()),
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
                "virtual_left_cache",
                self.virtual_left_cache.len(),
                self.virtual_left_cache.capacity(),
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
                "trace_ladders",
                memo.ladders.len(),
                memo.ladders.capacity(),
            ));
        }
        out
    }

    /// How many bytes of elimination sentence the two memos are holding — the explain-only text a run that never reads a ladder still pays for. Counted rather than estimated, because it is the figure that decides whether formatting them at all is worth its RAM. The candidate memo's half counts each distinct list in its elimination pool once, however many entries name it, because that is what is held (issue #167): an entry carries a seat, and the sentences a hit reads back through it cost the memo nothing beyond the seat.
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
            .flat_map(|memo| memo.ladders.values())
            .flat_map(|ladder| ladder.eliminations.iter())
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
    }

    /// Close the innermost capture and hand back what fired inside it, deduplicated with the first firing of each pointer kept — Python's `dict.fromkeys`. When the outermost capture closes the journal empties, so it never grows past one top-level evaluation.
    fn end_capture(&mut self) -> Box<[Pointer]> {
        let start = self
            .capture_starts
            .pop()
            .expect("every end_capture closes a begin_capture");
        let log = self
            .fired_log
            .as_mut()
            .expect("captures only open in trace-memo mode");
        let mut seen: HashSet<Pointer> = HashSet::new();
        let mut delta = Vec::new();
        for pointer in &log[start..] {
            if seen.insert(*pointer) {
                delta.push(*pointer);
            }
        }
        if self.capture_starts.is_empty() {
            log.clear();
        }
        delta.into_boxed_slice()
    }

    /// Abandon the innermost capture. A raising evaluation records no delta — it is never cached — but its firings stay journaled for any enclosing capture, because they demonstrably fired during that evaluation and a fresh replay would fire them again. A settling simulated prospect declines its memo entry the same way (issue #166): everything its capture journaled is the follower's window, which the trace memo now holds under a delta of its own, so the capture is discarded rather than closed and the enclosing window's delta is exactly what it was.
    fn abort_capture(&mut self) {
        self.capture_starts.pop();
        if self.capture_starts.is_empty() {
            self.fired_log
                .as_mut()
                .expect("captures only open in trace-memo mode")
                .clear();
        }
    }

    // --- condition matching ---------------------------------------------------

    fn left_exit_stroke(&self, left: &LeftContext) -> Option<Sym> {
        if left.kind != TokenKind::Letter {
            return None;
        }
        let settled = left.settled.as_ref()?;
        let seam = settled.seam?;
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
        index.exit_row(id, seam).and_then(|(_, row)| row.stroke)
    }

    /// Whether a condition matches the resolved left neighbor. `seam` is the height of the join being decided between the left and this position — the candidate's entry, or `None` when unentered — which is what `joined_at:` and a from-scope condition read.
    pub fn cond_matches_left(
        &self,
        owner: Option<Sym>,
        cond: &Condition,
        left: &LeftContext,
        seam: Option<Sym>,
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
                && joined_at != vocab.height_state(seam)
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
            if self.cond_matches_left(owner, excepted, left, seam)? {
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// Whether a condition matches the raw slots to the right. `tokens[0]` is the slot this condition tests, a `then:` hop recurses on the tail, and an `except:` entry tests the same slot with its own hops walking the same tail, so a chain reads one raw token per hop and exhausts to `UNKNOWN` past the supplied window. `None` is the verdict that depends on a slot outside the evaluated window; refusals, unlocks and the closure all treat it optimistically, which is what makes their reach honest about what the window cannot see.
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

    /// Whether a `when:` gate holds for this window. `None` is the verdict that depends on a slot outside the evaluated window, and it propagates: a definite `false` on any axis wins outright, but an unknown on one axis leaves the whole verdict unknown even when every other axis matched.
    pub fn when_matches(
        &self,
        owner: Option<Sym>,
        when: &When,
        left: &LeftContext,
        entry: Option<Sym>,
        seam: Option<Sym>,
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
            && state != vocab.liveness_state(seam)
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

    /// Whether this stance offers a live entry at `height` against the left, and the note the commit carries when it does. A declared selectable row whose from-scope admits the left grants it, and so does any unlock naming the height whose feature is active and whose `when:` does not definitively refuse the window; the optimism there is deliberate and matches the closure's.
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
            // This loop stops at the first match, unlike the toward-scope's below, so a later from-scope condition that would raise never gets the chance. The asymmetry between the two is deliberate, not an oversight.
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

    /// Every exit this stance can offer: the declared rows in declaration order at their own seats, then the heights an active unlock grants that no declared row shadows, at seats past the declared ones. The unlocks fire on every consult, cache hit included, because the enumeration that hit the cache is exactly as dependent on them as the one that filled it.
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
            .map(|(seat, (height, row))| ExitSource {
                height: *height,
                row: Some(row),
                index: seat,
            })
            .collect();
        // Python journals the unlock's `provenance` even when it is None, which `_record_fired` then drops; keeping only the pointers that exist is the same replay with nothing to drop.
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

    /// The (entry-state, exit-state) pairs an active unlock admits in this window. An unlock with no `when:` is unconditional, and one whose `when:` is merely unknown still counts — the same optimism the entry side takes.
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

    /// The first refuse record on this rune that kills the candidate. The three grains are whole-join (no target fields, which kills only joining candidates), stance, and surface row. Only a definite verdict kills: an unknown one is the optimistic non-fire that keeps a refusal from reaching past the window it can see.
    ///
    /// Python returns the record paired with whether the verdict was definite, and every call site reads only the record — the flag is `True` at the one place the function returns at all — so the pair is not reproduced here.
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
            if record.exit.is_some() && record.exit != candidate.seam {
                continue;
            }
            if record.stance.is_none()
                && record.entry.is_none()
                && record.exit.is_none()
                && candidate.seam.is_none()
            {
                continue;
            }
            let verdict = self.when_matches(
                Some(rune.name),
                &record.when,
                left,
                candidate.entry,
                candidate.seam,
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

    /// Every pair candidate this rune offers in this window — a cell of the rune together with the seam state it offers toward the next position — with each eliminated candidate's reason appended to `eliminations` when one is asked for.
    ///
    /// The memo only runs in trace-memo mode, faithfully to Python: outside it there is no journal, so a stored entry could carry no delta to replay and a later hit would silently swallow the firings its first evaluation performed. What an entry holds is three seats (issue #167), so a hit clones the candidate list out of the memo's pool exactly as it cloned it out of the entry, extends the caller's eliminations from the pool the same way, and replays the seated delta.
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
        let key = Self::candidates_key(left, rune_name, right1, right2);
        let entry = match self.candidates_cache.entries.get(&key) {
            Some(&cached) => {
                replay_into(
                    &mut self.fired_log,
                    &self.capture_starts,
                    self.deltas.get(cached.delta),
                );
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
                let delta = self.end_capture();
                let memo = &mut self.candidates_cache;
                let entry = CandidatesEntry {
                    candidates: memo.candidates.seat(out),
                    eliminations: memo.eliminations.seat(local),
                    delta: self.deltas.seat(delta),
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

    fn candidates_key(
        left: &LeftContext,
        rune_name: Sym,
        right1: RightToken,
        right2: RightToken,
    ) -> CandidatesKey {
        let tokens = [right1, right2];
        CandidatesKey {
            left_kind: left.kind,
            left_rune: left.settled.as_ref().map(|settled| settled.cell.rune),
            left_stance: left.settled.as_ref().map(|settled| settled.cell.stance),
            left_seam: left.settled.as_ref().and_then(|settled| settled.seam),
            rune: rune_name,
            kinds: tokens.map(RightToken::kind),
            runes: tokens.map(RightToken::rune),
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
            describe: self.explain_ladder,
        };
        let index = self.index();
        let vocab = index.vocab();
        let seat = index.rune_seat(rune_name).unwrap_or_else(|| {
            panic!(
                "{} is not a modeled rune, exactly as spec.runes[…] raises KeyError",
                index.resolve(rune_name)
            )
        });
        let rune = index.rune_at(seat);
        let committed = if left.kind == TokenKind::Letter {
            left.settled.as_ref().and_then(|settled| settled.seam)
        } else {
            None
        };
        let mut out: Vec<Candidate> = Vec::new();
        for (stance_seat, (stance_name, stance)) in rune.stances.iter().enumerate() {
            let id = StanceId::new(
                seat,
                u32::try_from(stance_seat)
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
                                "{}.{}: no available entry row at {} against the committed seam",
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
                    let candidate =
                        Candidate::joining(*stance_name, entry, height, order_index, source.index);
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
                        // Python builds the whole verdict list before reading it, so a later scope condition that raises still raises even once an earlier one has matched.
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
            let non_joining = Candidate::non_joining(*stance_name, entry, order_index);
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

    /// The left a follower would settle against if this candidate won: the candidate's cell with no adjustments and no extension, which is everything the follower's own enumeration reads.
    fn virtual_left(&mut self, rune_name: Sym, candidate: Candidate) -> LeftContext {
        if let Some(cached) = self.virtual_left_cache.get(&(rune_name, candidate)) {
            return cached.clone();
        }
        let built = LeftContext::letter(Settled {
            cell: CellId {
                rune: rune_name,
                stance: candidate.stance,
                entry: candidate.entry,
                exit: candidate.seam,
                adjustments: Vec::new(),
            },
            seam: candidate.seam,
            extension: 0,
        });
        self.virtual_left_cache
            .insert((rune_name, candidate), built.clone());
        built
    }

    /// Step 2's lookahead closure: whether some cell of the follower survives its own pairings, require, unlocks, row scopes and every window-decidable refusal, evaluated with this candidate as the follower's resolved left and the raw slot past it as the follower's right. Mutuality is definitional — an exit with no refusal-aware acceptor is never a candidate — and the slots past the window are optimistic by construction.
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
            seam: candidate.seam,
            right1: follower,
            right2,
        };
        if let Some(&(cached, delta)) = self.closure_cache.get(&key) {
            replay_into(
                &mut self.fired_log,
                &self.capture_starts,
                self.deltas.get(delta),
            );
            return Ok(cached);
        }
        let virtual_left = self.virtual_left(rune_name, *candidate);
        if self.fired_log.is_none() {
            let result = !self
                .candidates(&virtual_left, follower, right2, UNKNOWN, None)?
                .is_empty();
            let delta = self.deltas.seat(Box::default());
            self.closure_cache.insert(key, (result, delta));
            return Ok(result);
        }
        self.begin_capture();
        let result = match self.candidates(&virtual_left, follower, right2, UNKNOWN, None) {
            Ok(cells) => !cells.is_empty(),
            Err(error) => {
                self.abort_capture();
                return Err(error);
            }
        };
        let delta = self.end_capture();
        let delta = self.deltas.seat(delta);
        self.closure_cache.insert(key, (result, delta));
        Ok(result)
    }

    /// The trace memo's key. `token` is the input rune rather than the whole token, because a non-letter input short-circuits to the boundary trace before any key is built and therefore has no memo entry to name.
    fn trace_key(left: &LeftContext, token: Sym, slots: Slots) -> TraceKey {
        let tokens = slots.as_array();
        TraceKey {
            left_kind: left.kind,
            left_rune: left.settled.as_ref().map(|settled| settled.cell.rune),
            left_stance: left.settled.as_ref().map(|settled| settled.cell.stance),
            left_seam: left.settled.as_ref().and_then(|settled| settled.seam),
            left_extension: i16::try_from(
                left.settled.as_ref().map_or(0, |settled| settled.extension),
            )
            .expect("an extension is a count of connector pixels"),
            token,
            kinds: tokens.map(RightToken::kind),
            runes: tokens.map(RightToken::rune),
        }
    }

    // --- the prospect term -----------------------------------------------------------

    /// What the seam past this one is worth given this candidate — the join count's third term, in both of its meanings.
    ///
    /// With `simulated_prospect` on (issue 28's shipping default) the term is the follower's *actual* simulated transition: the whole cascade run one position over, with this candidate standing as the follower's left and the window shifted right, scoring 1 exactly when the simulated winner carries a seam. The recursion that opens only ever moves rightward with strictly shrinking slots and bottoms out at the window edge, where a non-letter slot answers 0 — today's epistemic state, kept on purpose, so beyond-window text stays exactly as unknowable as it is. With the mode off (the section 5.7 guard's pin and the comparison state) the term is the pre-issue-28 optimistic candidacy estimate: 1 when any seam-bearing follower cell survives enumeration, refusal-aware but blind to the follower's prefers and ordering.
    ///
    /// A counterfactual cascade can raise where real settlement never would — a prefer conflict, or a definitively firing unlock scope, in a window whose candidate never wins — so a raising cascade falls back to the candidacy estimate, the honest cannot-rank answer, and counts in [`Engine::simulated_prospect_fallbacks`]. Python's catch there names all four settlement outcomes, which is every error this crate raises, so the fallback here is a plain catch-all; the one thing it swallows that Python's does not is the unresolvable-class spec defect, which `spec_load` refuses long before settlement.
    ///
    /// The memo holds a term beside the seat of its fired delta, and under a trace memo in simulated mode it holds only the asks whose cascade raised (issue #166). A settling cascade's delta is exactly the trace memo's delta for the follower's window: the virtual left journals nothing, and the capture's dedup of what [`Engine::with_settled`] journaled — a replayed trace delta, or the raw firings the trace memo deduplicated into that same delta — is that delta again. An entry for it would be a second copy of an entry the trace memo already holds, under a key that collapses this candidate's entry, so the capture is discarded instead, as [`Engine::abort_capture`] says, and the next ask with this key reads the trace memo through `with_settled`, which replays the same first-fired sequence into the same enclosing capture at the same point. The raising cascade is the one window the trace memo can never hold, so its fallback verdict is what this memo is for. Candidacy mode runs no cascade and memoizes every ask, and so does simulated mode without a trace memo, where nothing stands behind this memo to answer the next ask.
    ///
    /// A cascade asked for with no window under evaluation is a probe's ask — [`Engine::probe_prospect`] is the one caller that reaches the term that way, because the ranking only ever asks from inside a trace — and its follower window is settled through [`Engine::with_settled_unrecorded`]: read off the trace memo where the memo holds it, and left out of the memo where it does not (issue #168). The probe arms memoize their verdicts above this call on keys of their own, so nothing asks that window again except a row whose ranking reaches the same shifted window, and an instrumented run of the whole alphabet says how rarely that is: the probes' cascades wrote well over a third of the trace memo's entries, nearly every one of them was never read, and the fourth-slot probes' — a letter third and an unknown fourth, a window a row reaches only past a live fourth slot — all but never. Recording them is what carries the memo's bucket table past a power-of-two doubling at the whole alphabet, and leaving them out is what keeps it under. The cascades a probe's cascade runs in turn are recorded as any ranking's are, since those are the windows every ranking shares.
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
        let key = if self.simulated_prospect {
            let deep = [slots.right2, slots.right3, slots.right4];
            ProspectKey::Simulated {
                rune: rune_name,
                stance: candidate.stance,
                entry: candidate.entry,
                seam: candidate.seam,
                right1: follower,
                kinds: deep.map(RightToken::kind),
                runes: deep.map(RightToken::rune),
            }
        } else {
            ProspectKey::Candidacy {
                rune: rune_name,
                stance: candidate.stance,
                entry: candidate.entry,
                seam: candidate.seam,
                right1: follower,
                right2: slots.right2.letter(),
            }
        };
        if let Some(&(cached, seat)) = self.prospect_cache.get(&key) {
            replay_into(
                &mut self.fired_log,
                &self.capture_starts,
                self.deltas.get(seat),
            );
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
        let delta = if capturing {
            self.end_capture()
        } else {
            Box::default()
        };
        let seat = self.deltas.seat(delta);
        let prospect = i8::try_from(result).expect("a prospect is a seam count, zero or one");
        self.prospect_cache.insert(key, (prospect, seat));
        Ok(result)
    }

    /// The term computed afresh, together with how it was answered: off the follower's simulated trace, or by the candidacy estimate — the mode's own term, or the fallback a raising cascade takes. `recorded` is whether the follower's window may enter the trace memo, which [`Engine::prospect`] withholds from a probe's ask.
    fn prospect_uncached(
        &mut self,
        rune_name: Sym,
        candidate: Candidate,
        slots: Slots,
        follower: Sym,
        recorded: bool,
    ) -> Result<(i64, ProspectTerm), SettleError> {
        let virtual_left = self.virtual_left(rune_name, candidate);
        if !self.simulated_prospect {
            let estimate =
                self.seam_bearing_follower_exists(&virtual_left, follower, slots.right2)?;
            return Ok((estimate, ProspectTerm::Estimated));
        }
        let shifted = Slots::new(slots.right2, slots.right3, slots.right4, UNKNOWN);
        let read_seam = |settled: &Settled| i64::from(settled.seam.is_some());
        let simulated = if recorded {
            self.with_settled(&virtual_left, slots.right1, shifted, read_seam)
        } else {
            self.with_settled_unrecorded(&virtual_left, slots.right1, shifted, read_seam)
        };
        match simulated {
            Ok(seam_bearing) => Ok((seam_bearing, ProspectTerm::Simulated)),
            Err(_) => {
                self.simulated_prospect_fallbacks += 1;
                let estimate =
                    self.seam_bearing_follower_exists(&virtual_left, follower, slots.right2)?;
                Ok((estimate, ProspectTerm::Estimated))
            }
        }
    }

    /// The candidacy-grain estimate itself: whether any cell of the follower that survives enumeration offers a seam onward.
    fn seam_bearing_follower_exists(
        &mut self,
        virtual_left: &LeftContext,
        follower: Sym,
        right2: RightToken,
    ) -> Result<i64, SettleError> {
        let cells = self.candidates(virtual_left, follower, right2, UNKNOWN, None)?;
        Ok(i64::from(cells.iter().any(|cell| cell.seam.is_some())))
    }

    // --- prefers ---------------------------------------------------------------------

    /// Whether one prefer record speaks for this candidate. `None` is the verdict "this record has nothing to say about this window at all", which is what keeps an irrelevant record out of the stage rather than counting it as a vote against.
    ///
    /// Our own rune's record targets the candidate's stance or cell directly and reads the seat's raw deep slots as they are. A follower's record instead *votes*: it speaks for the candidates under which its own preferred continuation is admissible, evaluated one position over with `joined_at` bound to the candidate's seam. That reading is the stage-4b flag's whole subject — with `vote_slots` on the vote is handed the seat's slots shifted once, so a chained condition resolves inside the window; with it off everything past the vote's own `right1` is pinned to `vote_deep_slot`, whose unknown verdicts count as firing, which is the older optimism that forced a deep-chained fact to be restated on every possible left rune instead of living once on the rune that owns it.
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
                candidate.seam,
                slots,
            )?;
            if verdict == Some(false) {
                return Ok(None);
            }
            if let Some(stance) = record.stance {
                return Ok(Some(candidate.stance == stance));
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
        let virtual_left = self.virtual_left(rune_name, candidate);
        let (vote_right2, vote_right3) = if self.vote_slots {
            (slots.right3, slots.right4)
        } else {
            (self.vote_deep_slot, UNKNOWN)
        };
        let follower_cells =
            self.candidates(&virtual_left, owner, slots.right2, vote_right2, None)?;
        let vote_slots = Slots::new(slots.right2, vote_right2, vote_right3, UNKNOWN);
        let mut relevant = false;
        for cell in &follower_cells {
            let verdict = self.when_matches(
                Some(owner),
                &record.when,
                &virtual_left,
                cell.entry,
                cell.seam,
                vote_slots,
            )?;
            if verdict == Some(false) {
                continue;
            }
            relevant = true;
            if let Some(stance) = record.stance
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

    /// [`Engine::prospect`] under the name the deep-slot liveness probes call it by.
    ///
    /// The probes reach into what is otherwise an internal ranking term, and this pair of wrappers is what keeps that reach visible instead of widening the settlement surface for it: the probes are the only callers, the delegation is total, and nothing about the term changes by being asked for from [`crate::liveness`] rather than from the ranking. The candidate a probe hands in is the bare `Candidate(stance, None, seam, 0)` shape of the input frame, not a candidate the enumeration produced.
    #[allow(dead_code)]
    pub(crate) fn probe_prospect(
        &mut self,
        rune_name: Sym,
        candidate: Candidate,
        slots: Slots,
    ) -> Result<i64, SettleError> {
        self.prospect(rune_name, candidate, slots)
    }

    /// [`Engine::prefer_favors`] under the name the vote arm calls it by. The same total delegation as [`Engine::probe_prospect`], for the same reason.
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

    /// One prefer stage — absolute or yielding — over the records of both seam runes, most-specific first.
    ///
    /// Records are gathered in declaration order, our own rune's before the follower's, then ranked by how many other applicable records outrank them, so the narrowest applies first and a nested conflict resolves silently by membership. A record whose demand has already been narrowed away is where the stage either finds a `resolve:` naming the collision or refuses: E-AMBIGUOUS when both records belong to one rune, E-INCOMPARABLE when they belong to two.
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
            let mut favored: HashSet<Candidate> = HashSet::new();
            let mut relevant = false;
            for candidate in survivors {
                let Some(vote) =
                    self.prefer_favors(owner, record, rune_name, *candidate, left, slots)?
                else {
                    continue;
                };
                relevant = true;
                if vote {
                    favored.insert(*candidate);
                }
            }
            if relevant && !favored.is_empty() && favored.len() < survivors.len() {
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
        // Python re-expands both records' axes inside every pairwise `outranks` call; expanding each record's once and comparing the expansions is the same comparison without the quadratic re-expansion.
        let mut axes = Vec::with_capacity(applicable.len());
        for entry in &applicable {
            axes.push(specificity::axis_sets(
                index,
                &entry.record.when,
                Some(entry.owner),
            )?);
        }
        let mut outranked_by: Vec<usize> = Vec::with_capacity(applicable.len());
        for (seat, own) in axes.iter().enumerate() {
            let mut beaten = 0;
            for (other_seat, other) in axes.iter().enumerate() {
                if other_seat != seat
                    && specificity::compare_axes(other, own) == specificity::Ordering::AOutranks
                {
                    beaten += 1;
                }
            }
            outranked_by.push(beaten);
        }
        let mut ordered: Vec<usize> = (0..applicable.len()).collect();
        ordered.sort_by_key(|seat| outranked_by[*seat]);

        let mut current = survivors.to_vec();
        let mut applied: Vec<OwnedRecord<'i>> = Vec::new();
        for seat in ordered {
            let Applicable {
                owner,
                record,
                favored,
            } = &applicable[seat];
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
            let mut crossed = 0;
            while crossed < applied.len() {
                let previous = applied[crossed];
                crossed += 1;
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

    /// The section 5.8 against-a-named-record slice: a crossing between two runes' prefers resolves without an error when a `resolve:` on either rune names the other record in `against:` and its own `when:` does not definitively refuse this window — unknown deep slots count as matching, the same optimism the refusals and the unlocks take.
    ///
    /// The `pick:` pattern filters the stage's whole survivor set rather than the narrowed list, because the resolve overrides both colliding records and not merely the later one, and its provenance lands in the fired set and the notes so that explain output and the dead-policy gate both see it. `None` is the answer "no resolve speaks to this crossing", which is what turns the collision into E-INCOMPARABLE. Two matching resolves that disagree on the pick, and a pick that admits no survivor, stay hard errors of their own.
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

    /// The E-INCOMPARABLE sentence: the two records, an example window spelled in rune names, the candidates they conflicted over, and a paste-ready `resolve:` record for the rune that owns the window. Every byte of it is contract, the stub included — the author's next move is to copy it into the rune's YAML, so a record with no `id:` prints the instruction to give it one rather than an empty field.
    ///
    /// Three of its fields fall back on Python's `or`, which reads an *empty* string the way it reads an absent one, and all three empty spellings are authorable in a dump: a rune named `""` drops out of the example window rather than widening it with a space, a height named `""` prints `none` beside a candidate that never joined, and a record whose `id:` is `""` prints the instruction to give it one. See [`text_or`].
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
                    text_or(index.resolve(vocab.height_state(candidate.seam)), "none")
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

    /// The extend or contract record that shapes one side of the winning cell: the records naming this side's height and nothing on the other side, filtered to the candidate's stance, and only those whose `when:` holds definitively — an adjustment is geometry, so an unknown slot is not enough to move a pixel. Several matches go to the section 6.2 order, where a tie among equals with the same demand collapses and a tie with different demands is E-INCOMPARABLE.
    ///
    /// Python takes the height as its own parameter; every call site passes the candidate's own height for the side being shaped, so it is derived here instead.
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
            Side::Exit => candidate.seam,
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
                candidate.seam,
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

    /// The withdrawal bindings a declined exit renders with. A join that does not realize mid-word leaves the exit state none, and where the declined row names a withdrawal bitmap that drawing becomes part of the cell's identity as an `ex-bind-<bitmap>` token; a `withdrawal: safe` row collapses to the plain exit-none cell instead. An explicit `cells:` composition for this (entry-state, withdrawn-height) pair overrides the row's binding, and the *last* such row wins, because the scan never breaks out early.
    fn withdrawal_tokens(&self, stance: &Stance, entry: Option<Sym>) -> Vec<AdjustmentToken> {
        let index = self.index();
        let vocab = index.vocab();
        let entry_state = vocab.height_state(entry);
        let mut tokens: Vec<AdjustmentToken> = Vec::new();
        for (height, row) in stance.surface.exits.iter() {
            let Some(withdrawal) = row.withdrawal else {
                continue;
            };
            if withdrawal == vocab.safe {
                continue;
            }
            let withdrawn = index.withdrawn_state(*height);
            let mut bitmap = withdrawal;
            for binding in &stance.surface.cells {
                if binding.entry == entry_state && Some(binding.exit) == withdrawn {
                    bitmap = binding.bitmap;
                }
            }
            tokens.push(AdjustmentToken::Bind(Side::Exit, bitmap));
        }
        tokens
    }

    /// The window join count: the seam behind us, the seam we offer, and what the seam past us is worth.
    fn score(
        &mut self,
        rune_name: Sym,
        candidate: Candidate,
        committed: Option<Sym>,
        slots: Slots,
    ) -> Result<i64, SettleError> {
        let left_term = i64::from(committed.is_some());
        let own_term = i64::from(candidate.seam.is_some());
        Ok(left_term + own_term + self.prospect(rune_name, candidate, slots)?)
    }

    /// Turn the winning candidate into the cell it settles as: the ZWNJ lock first, then each live side's extend and contract, then the extension the exit side carries in pixels, then — for a declined join mid-word — the withdrawal bindings.
    ///
    /// The one subtlety is the same-seam non-summing rule (prototype divergence 3): a follower's entry extension is suppressed when the predecessor's exit already carries the seam's connector pixels, because the two would otherwise both draw them. The suppressed record still fired and still notes itself as applied — it did match, and the dead-policy gate should see it — and the suppression appends its own sentence saying so.
    ///
    /// `right` is the *two*-slot window, deliberately: Python hands the commit `right1` and `right2` alone and lets the deeper slots default to `UNKNOWN`, so an adjustment record whose condition reaches past the follower reads the window edge rather than the text, and geometry never turns on a slot the emitted lookup could not key on.
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
                    "entry extension suppressed: the predecessor's exit already carries the seam's connector pixels (same-seam non-summing)"
                        .to_owned(),
                );
                extend = None;
            }
            adjustments.extend(adjustment_tokens(Side::Entry, extend, contract));
        }
        let mut extension = 0;
        if winner.seam.is_some() {
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
            adjustments.extend(adjustment_tokens(Side::Exit, extend, contract));
        } else if right.right1.kind() == TokenKind::Letter {
            adjustments.extend(self.withdrawal_tokens(stance, winner.entry));
        }
        Ok(Settled {
            cell: CellId {
                rune: rune.name,
                stance: winner.stance,
                entry: winner.entry,
                exit: winner.seam,
                adjustments,
            },
            seam: winner.seam,
            extension,
        })
    }

    // --- the kernel ---------------------------------------------------------------------

    /// Settle one window — the rich form the table builder and the explain CLI read.
    ///
    /// In trace-memo mode the result is memoized over the collapsed left key: every left read the kernel makes goes through the kind and the settled cell's rune, stance, seam and extension — condition matching consults the rune and the stance, the stroke axis the committed seam, the scoring the seam's presence, and the same-seam suppression the extension — and never the left cell's entry or its adjustments, so two settled lefts differing only there trace identically and share one entry. What the memo holds is seats rather than the trace (issue #165), so a hit is rebuilt out of the memo's pools — the settled record and the notes cloned out, the ladder read back where one was recorded — and returns what its miss returned, with the miss's fired delta replayed. A window the own memo misses is looked up in the bases next, and answered from there on the same terms where a base holds it and admits it; only then is it settled. Raising windows are never cached: the E-STRANDED sentence reads the left's full label, and the liveness probes that trip settlement errors memoize their own verdicts above this call.
    pub fn transition_trace(
        &mut self,
        left: &LeftContext,
        token: RightToken,
        slots: Slots,
    ) -> Result<TransitionTrace, SettleError> {
        if token.kind() != TokenKind::Letter {
            return Ok(TransitionTrace {
                settled: boundary_settled(self.index().vocab(), token.kind()),
                joint_floor: false,
                prospect: 0,
                decided_stage: DecidedStage::Boundary,
                notes: Vec::new(),
                ladder: None,
            });
        }
        if self.trace_cache.is_none() {
            return self.transition_trace_uncached(left, token, slots);
        }
        let key = Self::trace_key(left, token.letter(), slots);
        if let Some(memo) = self.trace_cache.as_ref()
            && let Some(&entry) = memo.entries.get(&key)
        {
            let trace = memo.trace(&key, entry);
            replay_into(
                &mut self.fired_log,
                &self.capture_starts,
                self.deltas.get(entry.delta),
            );
            return Ok(trace);
        }
        if let Some((seat, base, entry)) = base_entry(&self.bases, &key) {
            let trace = base.memo.trace(entry);
            replay_base(
                &mut self.fired,
                &mut self.fired_log,
                &self.capture_starts,
                &mut self.base_fired[seat],
                entry.delta,
                base.memo.delta(entry),
            );
            self.base_hits += 1;
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
        let delta = self.end_capture();
        let delta = self.deltas.seat(delta);
        self.trace_cache
            .as_mut()
            .expect("the memo is what brought us here")
            .insert(key, &trace, delta);
        Ok(trace)
    }

    /// One field or two off a window's settled record, read where the record already sits in the memo rather than through a copy of it. [`Engine::transition_trace`] answers with a whole owned trace, and the two callers that come through here want a seam or a cell out of the settled record and nothing else — so a hit on the memo would otherwise rebuild a whole trace, notes and adjustment list cloned out of the pools, to answer a question about one `Option`. It reads the settled record rather than the trace because the memo holds no trace any more, only seats (issue #165), and the record is the one half its callers ever asked for.
    ///
    /// The fired delta is replayed on a hit exactly as [`Engine::transition_trace`] replays it, because that is what makes a warm engine's fired set equal a cold one's, and no reading shortcut may skip it.
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

    /// [`Engine::with_settled`] for a letter window not worth a memo entry: a hit answers exactly as it does there, delta replayed and all, and a miss settles the window without recording it (issue #168). The miss opens no capture of its own, so what it fires journals straight into the enclosing capture — which is where a recorded miss's firings land too, replayed out of its fresh entry — and every window the evaluation asks for beneath it is memoized as usual. The one caller is the probe's cascade in [`Engine::prospect`], whose docstring carries the measurement that makes the window not worth an entry.
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

    /// The trace memo's answer for one window, where it holds one: the read applied to the settled record where it sits in the pool, with the entry's fired delta replayed. `None` is a miss — a non-letter token, an engine with no memo, or a window the memo has not seen — and says nothing about how the caller should settle it.
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
        let key = Self::trace_key(left, token.letter(), slots);
        if let Some(&entry) = memo.entries.get(&key) {
            let answer = read(memo.settled.get(entry.settled));
            replay_into(
                &mut self.fired_log,
                &self.capture_starts,
                self.deltas.get(entry.delta),
            );
            return Some(answer);
        }
        let (seat, base, entry) = base_entry(&self.bases, &key)?;
        let answer = read(base.memo.settled(entry));
        replay_base(
            &mut self.fired,
            &mut self.fired_log,
            &self.capture_starts,
            &mut self.base_fired[seat],
            entry.delta,
            base.memo.delta(entry),
        );
        self.base_hits += 1;
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
            left.settled.as_ref().and_then(|settled| settled.seam)
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
        // Section 6.3 compensation (b): the pointer of every record that eliminated a candidate here rides the notes, so the decision-rule TSVs and the emitted FEA carry per-rule provenance comments.
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
                    .expect("a committed seam comes from a settled left");
                return Err(SettleError::Stranded(format!(
                    "E-STRANDED: {} committed an exit at {} but {} has no acceptor cell (the lookahead closure should have prevented this commitment)",
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
        let mut ranked: HashMap<Candidate, RankedCandidate> = HashMap::new();
        for candidate in &survivors {
            let join_count = self.score(rune_name, *candidate, committed, slots)?;
            let prospect = self.prospect(rune_name, *candidate, slots)?;
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

        let mut joint_floor = false;
        if survivors.len() > 1 {
            let mut ordered = survivors.clone();
            ordered.sort_by_key(|candidate| floor_key(index, candidate));
            decided_stage = DecidedStage::Floor;
            runner_up = Some(ordered[1]);
            joint_floor = ordered[0].seam.is_none() != ordered[1].seam.is_none();
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
        let ladder = self.explain_ladder.then(|| {
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
            Box::new(TraceLadder {
                ranked: scored,
                eliminations,
                runner_up,
            })
        });
        Ok(TransitionTrace {
            settled,
            joint_floor,
            prospect: ranked[&winner].prospect,
            decided_stage,
            notes,
            ladder,
        })
    }
}

/// Append one candidate's elimination when the caller asked for eliminations at all. The description is built lazily because the closure and the prospect both enumerate with eliminations off, and formatting a sentence nobody reads is the one avoidable cost in the enumeration's inner loop — which is also why a sink that is not recording a ladder leaves the sentence empty and keeps only the stage and the pointer the notes are built from.
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

/// The first base holding `key` and admitting it, with its seat among the bases and the entry it holds. A base holding the key under an exclusion that names one of its runes is passed over rather than answered, because what it recorded was settled under runes this engine does not share.
fn base_entry<'b>(
    bases: &'b [MemoBase],
    key: &TraceKey,
) -> Option<(usize, &'b MemoBase, TraceEntry)> {
    bases.iter().enumerate().find_map(|(seat, base)| {
        base.memo
            .entries
            .get(key)
            .filter(|_| base.excluded.admits(key))
            .map(|&entry| (seat, base, entry))
    })
}

/// One own-memo entry's fired delta replayed into the journal, spelled over the two fields it touches rather than over the whole engine. That is what lets a hit replay the delta where it sits in the memo instead of copying it out first: the memo and the journal are disjoint fields, and only a receiver that named the whole engine made them look otherwise. The fired set is not touched, because an own entry's pointers entered it when the entry was recorded — a delta is what the journal captured, and the journal inserts every pointer it logs — so a hit owes the set nothing and owes an open capture the delta: the enclosing window's delta must carry what its evaluation would have fired. A configuration's windows hit their own memo tens of millions of times, and a hash insert per pointer per hit was the larger part of what a hit cost.
fn replay_into(fired_log: &mut Option<Vec<Pointer>>, capture_starts: &[usize], delta: &[Pointer]) {
    if delta.is_empty() || capture_starts.is_empty() {
        return;
    }
    let log = fired_log
        .as_mut()
        .expect("a capture is only ever open while the journal exists");
    log.extend_from_slice(delta);
}

/// A base entry's fired delta replayed the same way, with the one difference a base makes: its pointers were journaled by another engine, so the fired set holds them only once this engine has replayed that delta seat before, which `replayed` — one flag per delta seat of the base — records.
fn replay_base(
    fired: &mut HashSet<Pointer>,
    fired_log: &mut Option<Vec<Pointer>>,
    capture_starts: &[usize],
    replayed: &mut [bool],
    seat: DeltaSeat,
    delta: &[Pointer],
) {
    if delta.is_empty() {
        return;
    }
    if !replayed[seat.index()] {
        fired.extend(delta.iter().copied());
        replayed[seat.index()] = true;
    }
    replay_into(fired_log, capture_starts, delta);
}

/// One authored value out of a `cell:` / `over:` / `pick:` mapping. The model keeps these ordered rather than hashed, and they hold two or three keys, so the scan is the lookup.
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
        && wanted != vocab.height_state(candidate.seam)
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

/// A `pick:` in the form two resolves are compared for agreement by — Python's `tuple(sorted(res.pick.items()))`. The comparison is on the authored text rather than on symbols, because two specs' identical picks must count as one demand and interning order says nothing about a pick's content.
///
/// The sort is stable, like every sort in this crate: Python's `sorted` is, and a pick cannot carry one key twice, so no tie can arise here — but the rule holds crate-wide rather than per call site precisely so that no reader has to re-derive that argument.
fn sorted_pick_items<'a>(index: &'a SpecIndex, pick: &Table<Sym>) -> Vec<(&'a str, &'a str)> {
    let mut items: Vec<(&str, &str)> = pick
        .iter()
        .map(|(key, value)| (index.resolve(*key), index.resolve(*value)))
        .collect();
    items.sort();
    items
}

/// Python's `x or fallback` over a string, which is a truthiness test and therefore substitutes the fallback for an *empty* string as well as an absent one. The distinction is not academic: `id: ""` and a registry height named `""` both survive `kernel_io`'s reader, so a site that tested absence alone would print an empty field where Python prints the fallback. Only the sentence-formatting sites read this way — the `when:` clause beside them spells its families straight through, empty or not, exactly as Python's f-string does.
fn text_or<'a>(text: &'a str, fallback: &'a str) -> &'a str {
    if text.is_empty() { fallback } else { text }
}

/// A provenance as the notes and the raise messages spell it. Python formats these with an f-string over the field itself, so a record with no provenance prints Python's `str(None)` rather than nothing — the same sentence, and the same tell that a record was authored without a pointer.
fn provenance_text(index: &SpecIndex, provenance: Option<&Provenance>) -> String {
    match provenance {
        Some(provenance) => provenance_pointer(index, provenance),
        None => "None".to_owned(),
    }
}

/// Note one applied adjustment record on the trace, once — `_commit`'s inner `note_applied`. A record with no provenance notes nothing, because there is no pointer for the TSV to carry.
fn note_applied(index: &SpecIndex, notes: &mut Vec<String>, record: Option<&PolicyRecord>) {
    if let Some(provenance) = record.and_then(|record| record.provenance.as_ref()) {
        let pointer = provenance_pointer(index, provenance);
        if !notes.contains(&pointer) {
            notes.push(pointer);
        }
    }
}

/// The adjustment tokens one side's chosen records spell, in the order the grammar writes them: the extension, then the contract's binding, its trim, and — only when it names neither — its plain contraction. An extend of zero pixels spells nothing, while a contract of zero pixels still spells itself: the extension is read for a nonzero value and the contraction for presence.
fn adjustment_tokens(
    side: Side,
    extend: Option<&PolicyRecord>,
    contract: Option<&PolicyRecord>,
) -> Vec<AdjustmentToken> {
    let mut tokens: Vec<AdjustmentToken> = Vec::new();
    if let Some(record) = extend
        && let Some(by) = record.by
        && by != 0
    {
        tokens.push(AdjustmentToken::Extend(side, by));
    }
    if let Some(record) = contract {
        if let Some(bind) = record.bind {
            tokens.push(AdjustmentToken::Bind(side, bind));
        }
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
    tokens
}

/// The structural floor's sort key, `_transition_trace_uncached`'s `floor_key`: realizing the seam beats declining it, a lower seam beats a higher one, and the exit row's declaration seat settles the rest. Realizing the *left* seam is constant across candidates — entry binding is bilateral — so it is not part of the key.
fn floor_key(index: &SpecIndex, candidate: &Candidate) -> (usize, i64, usize) {
    match candidate.seam {
        Some(seam) => (
            0,
            index
                .y_of(seam)
                .expect("a candidate's seam is registry-declared, as registry.heights[…] assumes"),
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

    /// A JSON object over already-built pieces, for the mappings whose keys the fixtures compose rather than spell.
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

    /// The little alphabet most of these tests enumerate over.
    ///
    /// `qsPea` is the rune under enumeration: `half` enters at the baseline and exits at both heights, `full` has no entry surface at all and exits only at the baseline. `qsTea` accepts a baseline entry and offers no exit, so it is an acceptor at the baseline and none at the x-height — which is what closes `qsPea`'s x-height exit out. `qsMay` accepts a baseline entry only through an `ss03` unlock, and `qsIt` has no surface at all, so nothing reaches it.
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
        RightToken::Letter(fixtures::sym(index, name))
    }

    /// A settled letter left carrying `seam`, spelled through a real (rune, stance) pair the way every left the kernel meets is.
    fn settled_left(
        index: &SpecIndex,
        rune: &str,
        stance: &str,
        seam: Option<&str>,
    ) -> LeftContext {
        let seam = seam.map(|height| fixtures::sym(index, height));
        LeftContext::letter(Settled {
            cell: CellId {
                rune: fixtures::sym(index, rune),
                stance: fixtures::sym(index, stance),
                entry: None,
                exit: seam,
                adjustments: Vec::new(),
            },
            seam,
            extension: 0,
        })
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
                Candidate::joining(half, None, baseline, 0, 0),
                Candidate::non_joining(half, None, 0),
                Candidate::joining(full, None, baseline, 1, 0),
                Candidate::non_joining(full, None, 1),
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
    fn a_committed_seam_binds_the_entry_or_eliminates_the_stance() {
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
            ["qsTea.plain: no available entry row at x-height against the committed seam"]
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
            ["qsMay.alt: no available entry row at baseline against the committed seam"]
        );
        assert!(locked.fired().is_empty());

        let mut unlocked = Engine::new(&index, [fixtures::sym(&index, "ss03")]);
        let out = unlocked
            .candidates(&left, may, EDGE, EDGE, None)
            .expect("the fixture raises nothing");
        assert_eq!(
            out,
            vec![Candidate::non_joining(
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

    /// `qsPea.half` offers both heights but forbids pairing a live baseline entry with the x-height exit; `qsTea` accepts either height, so nothing but the pairing rule can be what kills the candidate.
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
                Candidate::joining(half, Some(baseline), baseline, 0, 0),
                Candidate::non_joining(half, Some(baseline), 0),
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

    /// `qsPea` refuses every joining cell toward a letter except `qsTea`. Both followers accept a baseline entry, so the closure admits either and the refusal is the only thing that can differ.
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
            vec![Candidate::non_joining(half, None, 0)],
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
                Candidate::joining(half, None, baseline, 0, 0),
                Candidate::non_joining(half, None, 0),
            ]
        );
        assert!(eliminations.is_empty());
    }

    /// `qsPea.half`'s baseline exit is scoped toward `qsTea` alone, and carries provenance so the fired set can be watched.
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
    fn an_unlock_exit_lands_past_the_declared_rows_and_never_shadows_one() {
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
                Candidate::joining(half, None, baseline, 0, 0),
                Candidate::non_joining(half, None, 0),
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
                Candidate::joining(half, None, baseline, 0, 0),
                Candidate::joining(half, None, x_height, 0, 1),
                Candidate::non_joining(half, None, 0),
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
        let complaint = engine
            .cond_matches_left(None, cond, &LeftContext::boundary(TokenKind::Edge), None)
            .expect_err("a left condition may not reach into the window");
        assert_eq!(
            complaint.message(),
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
        let complaint = engine
            .cond_matches_right(None, cond, &[EDGE, EDGE])
            .expect_err("stance is a left-only axis");
        assert_eq!(
            complaint.message(),
            "right conditions are raw: stance/joined_at are left-only axes (design section 3.4)"
        );
    }

    /// Four conditions hung on refuse records so a test can reach them as parsed `Condition`s, over a spec where `qsPea` offers a horizontal entry and a rising exit and `qsTea`'s only entry row is unselectable.
    ///
    /// The live alphabet authors no `stroke:` and no `is:` other than `boundary`, so no sweep over it can reach these branches however large; they are covered here or nowhere.
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
            "an unjoined left committed no seam and so exits at no stroke"
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
        let gate = |engine: &Engine<'_>, seat: usize, entry: Option<Sym>, slots: Slots| {
            engine
                .when_matches(None, &refusals[seat].when, &edge, entry, None, slots)
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

    /// The deep-chain testbed: four refusals spelling the same three- and four-hop family chains twice over, once on a condition's own spine and once inside an `except_` whose entry carries the chain. `qsPea`'s surface is empty because nothing here settles — every test below reads a condition off the policy and matches it against slots it spells by hand.
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

    /// The `when:` one [`deep_chain_spec`] refusal is keyed on.
    fn chain_when(index: &SpecIndex, seat: usize) -> &When {
        &index
            .rune(fixtures::sym(index, "qsPea"))
            .expect("qsPea is modeled")
            .policy
            .refuse[seat]
            .when
    }

    /// The right condition of one [`deep_chain_spec`] refusal.
    fn chain_condition(index: &SpecIndex, seat: usize) -> &Condition {
        chain_when(index, seat)
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
        let gate = |seat: usize, slots: Slots| {
            engine
                .when_matches(None, chain_when(&index, seat), &edge, None, None, slots)
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
        assert_eq!(*engine.end_capture(), *vec![two, one]);
        engine.record_pointer(three);
        assert_eq!(*engine.end_capture(), *vec![one, two, three]);
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
        assert_eq!(*engine.end_capture(), *vec![one, two]);
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
            .get(&Engine::candidates_key(&left, may, EDGE, EDGE))
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
            &*memoized.end_capture(),
            [unlock],
            "the hit replayed the delta its first evaluation journaled into the open capture"
        );
    }

    /// One stance whose every capability read fires: a scoped entry row, a pairing unlock, an exit unlock, and a scoped exit row, all of them provenanced so the journal's order can be read off.
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
                Candidate::joining(half, Some(baseline), baseline, 0, 0),
                Candidate::joining(half, Some(baseline), x_height, 0, 1),
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

    /// The memo's whole point (issue #165): an entry is its three seats, the prospect byte, the joint flag and the stage in sixteen bytes with nothing on the heap, under a key packed to forty.
    #[test]
    fn a_memoized_window_is_sixteen_bytes_under_a_forty_byte_key() {
        assert_eq!(std::mem::size_of::<TraceEntry>(), 16);
        assert_eq!(std::mem::size_of::<TraceKey>(), 40);
    }

    /// The candidate memo's whole point (issue #167): an entry is its three seats in twelve bytes with nothing on the heap, under a key packed to twenty-eight, and the closure memo's verdict rides beside a seat in eight.
    #[test]
    fn a_memoized_enumeration_is_twelve_bytes_under_a_twenty_eight_byte_key() {
        assert_eq!(std::mem::size_of::<CandidatesEntry>(), 12);
        assert_eq!(std::mem::size_of::<CandidatesKey>(), 28);
        assert_eq!(std::mem::size_of::<(bool, DeltaSeat)>(), 8);
    }

    /// Two windows that enumerate the same lists share one seat into each pool, an entry's lists read back through the pools exactly as its miss returned them, sentences included, and the sentence count is of what the pools hold rather than of what the entries name.
    #[test]
    fn windows_with_the_same_lists_share_one_seat_into_each_pool() {
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
                .get(&Engine::candidates_key(&left, pea, tea, right2))
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

    /// A journal-less engine seats nothing but the empty delta: its closure verdicts carry a seat like any memo's, and the pool they seat into never grows past the one delta such an engine can hold.
    #[test]
    fn a_journal_less_engine_seats_only_the_empty_delta() {
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
        for &(_, delta) in engine.closure_cache.values() {
            assert!(engine.deltas.get(delta).is_empty());
        }
        engine.release_memos();
        assert_eq!(engine.deltas.len(), 0);
    }

    /// The ladder lives beside the memo rather than in it: a fixpoint-mode engine records none at all, and an explain-mode hit answers with the one its miss recorded.
    #[test]
    fn a_hit_reads_its_ladder_back_only_where_its_miss_recorded_one() {
        let index = firing_spec();
        let ss03 = fixtures::sym(&index, "ss03");
        let left = settled_left(&index, "qsTea", "plain", Some("baseline"));
        let token = letter_token(&index, "qsPea");
        let slots = Slots::pair(letter_token(&index, "qsTea"), EDGE);
        for explain_ladder in [false, true] {
            let mut engine = Engine::with_modes(
                &index,
                [ss03],
                EngineModes {
                    trace_memo: true,
                    explain_ladder,
                    ..EngineModes::default()
                },
            );
            let miss = engine
                .transition_trace(&left, token, slots)
                .expect("the fixture settles");
            let hit = engine
                .transition_trace(&left, token, slots)
                .expect("the fixture settles");
            assert_eq!(miss.ladder.is_some(), explain_ladder);
            assert_eq!(hit, miss);
            let memo = engine.trace_cache.as_ref().expect("trace-memo memoizes");
            assert!(!memo.entries.is_empty());
            assert_eq!(
                memo.ladders.len(),
                if explain_ladder {
                    memo.entries.len()
                } else {
                    0
                }
            );
        }
    }

    /// The ranking testbed, `rebuild/pipeline/fixtures.py`'s `synthetic_spec` in this crate's four-family vocabulary.
    ///
    /// `qsPea` draws `stroke`, which exits at the x-height, and then `flourish`, which offers no surface at all. `qsTea` enters at the x-height and exits at the baseline but forbids pairing the two, so an entered `qsTea` is exitless; `qsMay` enters at the baseline. Every way the qsPea·qsTea seam can go is therefore worth exactly one window join, which is what leaves the stages past the join count something to decide.
    fn ranking_spec(pea_policy: &str, tea_policy: &str) -> SpecIndex {
        let pea = letter(
            "qsPea",
            &[
                stance(
                    "stroke",
                    &surface(
                        "{}",
                        &object(&[row("x-height", &[("withdrawal", "\"safe\"")])]),
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
                    &object(&[row("baseline", &[("withdrawal", "\"safe\"")])]),
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

    /// One policy record with a pointer, so that the notes and the raise messages have something legible to print.
    fn pointed_record(kind: &str, rune: &str, seat: usize, overrides: &[(&str, &str)]) -> String {
        let quoted = quoted(kind);
        let pointer =
            fixtures::names(&[&format!("{rune}.yaml"), &format!("policy.{kind}[{seat}]")]);
        let mut fields: Vec<(&str, &str)> =
            vec![("kind", quoted.as_str()), ("provenance", pointer.as_str())];
        fields.extend_from_slice(overrides);
        fixtures::record(&fields)
    }

    fn quoted(value: &str) -> String {
        fixtures::quote(value)
    }

    /// The prefer record most of the stage tests hang on: it speaks for `qsPea`'s surfaceless `flourish` stance, in whichever mode the caller names.
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

    /// The window these tests settle: the run edge on the left, `qsPea` under the pen, and the slots named.
    fn settle_pea(engine: &mut Engine<'_>, slots: Slots) -> Result<TransitionTrace, SettleError> {
        let token = letter_token(engine.index(), "qsPea");
        engine.transition_trace(&LeftContext::boundary(TokenKind::Edge), token, slots)
    }

    /// A `qsPea.stroke` left that committed the x-height seam, carrying `extension` connector pixels on it.
    fn committed_left(index: &SpecIndex, extension: i64) -> LeftContext {
        let x_height = fixtures::sym(index, "x-height");
        LeftContext::letter(Settled {
            cell: CellId {
                rune: fixtures::sym(index, "qsPea"),
                stance: fixtures::sym(index, "stroke"),
                entry: None,
                exit: Some(x_height),
                adjustments: Vec::new(),
            },
            seam: Some(x_height),
            extension,
        })
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
        assert!(!trace.joint_floor);
        assert!(trace.ladder().ranked.is_empty());
        assert!(trace.ladder().eliminations.is_empty());
        assert!(trace.notes.is_empty());
        assert_eq!(trace.ladder().runner_up, None);
    }

    #[test]
    fn the_floor_breaks_a_realization_tie_toward_the_join_and_flags_it_joint() {
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
        assert_eq!(trace.decided_stage, DecidedStage::Floor);
        assert!(
            trace.joint_floor,
            "the floor chose between realizing the seam and declining it"
        );
        assert_eq!(trace.settled.cell.stance, stroke);
        assert_eq!(trace.settled.cell.exit, Some(x_height));
        assert_eq!(trace.settled.seam, Some(x_height));
        assert_eq!(
            trace.ladder().runner_up,
            Some(Candidate::non_joining(stroke, None, 0))
        );
        assert_eq!(trace.prospect, 0);
        assert_eq!(
            trace
                .ladder()
                .ranked
                .iter()
                .map(|entry| (entry.candidate, entry.join_count))
                .collect::<Vec<_>>(),
            [
                (Candidate::joining(stroke, None, x_height, 0, 0), 1),
                (Candidate::non_joining(stroke, None, 0), 1),
                (Candidate::non_joining(flourish, None, 1), 1),
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
            trace.ladder().runner_up,
            Some(Candidate::non_joining(stroke_of(&plain), None, 0))
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
        assert_eq!(trace.settled.seam, None);
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
            trace.ladder().runner_up,
            Some(Candidate::joining(
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
    fn the_declared_order_decides_when_nothing_can_join() {
        let index = ranking_spec(&plain_policy(), &plain_policy());
        let mut engine = Engine::new(&index, no_features());
        let trace = settle_pea(&mut engine, Slots::pair(EDGE, EDGE)).expect("the fixture settles");
        assert_eq!(trace.decided_stage, DecidedStage::Order);
        assert_eq!(trace.settled.cell.stance, fixtures::sym(&index, "stroke"));
        assert_eq!(
            trace.ladder().runner_up,
            Some(Candidate::non_joining(
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
        let complaint = settle_pea(
            &mut engine,
            Slots::pair(letter_token(&index, "qsTea"), letter_token(&index, "qsMay")),
        )
        .expect_err("equal records demanding disjoint stances cannot both be honored");
        assert_eq!(complaint.kind(), SettleErrorKind::Ambiguous);
        assert_eq!(
            complaint.message(),
            "E-AMBIGUOUS: prefer records demand different outcomes at non-nested specificity: qsPea.yaml:policy.prefer[0] vs qsPea.yaml:policy.prefer[1]"
        );
    }

    /// [`ranking_spec`] carrying the two chained prefers a vote is read through. `qsPea`'s own record speaks for its `stroke` stance where the slots past it spell qsTea·qsMay·qsPea; `qsTea`'s speaks for its `hook` stance where the slots past *it* spell qsMay·qsPea — one hop shallower, because a follower's record is evaluated one position over.
    fn vote_slot_spec() -> SpecIndex {
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
    fn the_prefer_arms_read_their_own_deep_slots_and_the_vote_reads_them_shifted() {
        let index = vote_slot_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let own = &index.rune(pea).expect("qsPea is modeled").policy.prefer[0];
        let follower = &index.rune(tea).expect("qsTea is modeled").policy.prefer[0];
        let candidate = Candidate::joining(
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
            vote_slots: false,
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
                "our own rune's record reads the seat's raw deep slots whatever the vote's mode"
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

        let vote = |engine: &mut Engine<'_>, slots: Slots| {
            engine
                .prefer_favors(tea, follower, pea, candidate, &edge, slots)
                .expect("the fixture raises nothing")
        };
        let mut pinned_engine = Engine::with_modes(&index, no_features(), pinned);
        assert_eq!(
            vote(&mut pinned_engine, Slots::pair(tea_token, may_token)),
            Some(true),
            "everything past the vote's own right1 is pinned, so the chain's tail is unknown and the vote fires optimistically"
        );
        assert_eq!(
            vote(
                &mut pinned_engine,
                Slots::new(tea_token, may_token, pea_token, pea_token)
            ),
            Some(true)
        );
        assert_eq!(
            vote(
                &mut pinned_engine,
                Slots::new(tea_token, may_token, may_token, may_token)
            ),
            Some(true),
            "the pinned vote answers the same whatever the seat's deep slots hold"
        );

        let mut shifted = Engine::new(&index, no_features());
        assert_eq!(
            vote(
                &mut shifted,
                Slots::new(tea_token, may_token, pea_token, UNKNOWN)
            ),
            Some(true),
            "shifted once, the vote's chain resolves inside the window and fires"
        );
        assert_eq!(
            vote(
                &mut shifted,
                Slots::new(tea_token, may_token, may_token, UNKNOWN)
            ),
            None,
            "the same slots refute it definitively, and an irrelevant record is no vote against"
        );
        assert_eq!(
            vote(&mut shifted, Slots::pair(tea_token, may_token)),
            Some(true),
            "where the window really does end, the shifted reading is unknown-optimistic too"
        );
    }

    /// The crossing the resolve slice exists for: `qsPea` prefers realizing its x-height exit, while `qsTea` votes for whichever `qsPea` cell lets its own baseline exit live — two runes, equal specificity, disjoint demands.
    fn crossing_spec(pea_resolve: &str) -> SpecIndex {
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

    fn crossing_slots(index: &SpecIndex) -> Slots {
        Slots::pair(letter_token(index, "qsTea"), letter_token(index, "qsMay"))
    }

    #[test]
    fn a_crossing_between_two_runes_prefers_prints_the_resolve_that_would_settle_it() {
        let index = crossing_spec("[]");
        let mut engine = Engine::new(&index, no_features());
        let complaint = settle_pea(&mut engine, crossing_slots(&index))
            .expect_err("neither rune's prefer contains the other's");
        assert_eq!(complaint.kind(), SettleErrorKind::Incomparable);
        assert_eq!(
            complaint.message(),
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

    /// The dump every empty spelling of a name is authorable in, for the three sentence fields that fall back on Python's `or`: a rune named `""`, whose provenance therefore reads `.yaml:…`, a registry height named `""`, and a prefer record whose `id:` is `""`. The empty name and the empty height are one symbol, there being one empty string in the interner.
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

    /// Three of this sentence's fields are spelled with `or`, so each reads an empty authored string as absent: the example window drops the nameless rune instead of widening itself with a space, the candidate that entered at the empty height prints `entry none`, and the empty `id:` prints the instruction to give the record one. The `when:` clause is the control — it spells its families through an f-string, so the nameless follower lands there as `family: ` and belongs in the expected bytes.
    ///
    /// The expected bytes were taken from the Python original's own sentence for the same arguments, which read nothing off its engine; the sentence is this crate's now, and these are the bytes it owes.
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
            Candidate::non_joining(bare, Some(empty), 0),
            Candidate::joining(bare, None, fixtures::sym(&index, "x-height"), 0, 0),
        ];
        let left = LeftContext::letter(Settled {
            cell: CellId {
                rune: empty,
                stance: bare,
                entry: None,
                exit: None,
                adjustments: Vec::new(),
            },
            seam: None,
            extension: 0,
        });
        let message = engine.incomparable_message(
            prefer_of(pea),
            prefer_of(empty),
            pea,
            &survivors,
            &left,
            Slots::pair(letter_token(&index, "qsPea"), RightToken::Letter(empty)),
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
        let index = crossing_spec(&resolve);
        let mut engine = Engine::new(&index, no_features());
        let trace = settle_pea(&mut engine, crossing_slots(&index))
            .expect("the resolve settles the crossing");
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
        let index = crossing_spec(&empty_pick);
        let mut engine = Engine::new(&index, no_features());
        let complaint = settle_pea(&mut engine, crossing_slots(&index))
            .expect_err("a pick naming no stance of this rune admits no survivor");
        assert_eq!(complaint.kind(), SettleErrorKind::Incomparable);
        assert_eq!(
            complaint.message(),
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
        let index = crossing_spec(&disagreeing);
        let mut engine = Engine::new(&index, no_features());
        let complaint = settle_pea(&mut engine, crossing_slots(&index))
            .expect_err("two resolves cannot both name the winner");
        assert_eq!(
            complaint.message(),
            "E-INCOMPARABLE: conflicting resolve records match one window: qsPea.yaml:policy.resolve[0]; qsPea.yaml:policy.resolve[1]"
        );
    }

    #[test]
    fn an_entry_extension_is_suppressed_when_the_predecessor_already_carries_the_seam() {
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
                "entry extension suppressed: the predecessor's exit already carries the seam's connector pixels (same-seam non-summing)",
            ],
            "the suppressed record still matched and still fired, and the suppression says so"
        );
    }

    /// The deliberate asymmetry between `_adjustment_tokens` and `_commit`: the token list tests the extend's `by` truthily and the contract's for absence, so a zero-pixel extend spells nothing while a zero-pixel contract still spells `ex-con-0` and thereby stays visible in the cell's identity. The pixel arithmetic tests both truthily, so neither of them moves the extension. Either record matched and fired whatever its `by`, which is what the notes say.
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
            "the seam the two records shape has to be the one that won"
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

    /// The ranking testbed again, with `qsTea`'s baseline exit withdrawing to a named drawing rather than safely, and whatever `cells:` compositions the caller spells.
    fn withdrawal_spec(cells: &str) -> SpecIndex {
        let tea = letter(
            "qsTea",
            &[stance(
                "hook",
                &surface(
                    &object(&[row("x-height", &[])]),
                    &object(&[row("baseline", &[("withdrawal", "\"pulled-back\"")])]),
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
                    &object(&[row("x-height", &[("withdrawal", "\"safe\"")])]),
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
    fn a_declined_exit_binds_its_withdrawal_drawing_and_an_explicit_cell_overrides_it() {
        let index = withdrawal_spec("[]");
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

        let index = withdrawal_spec(
            r#"[{"entry":"x-height","exit":"baseline-withdrawn","bitmap":"hook-after-pea","entry_x":null,"exit_x":null,"provenance":null}]"#,
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
            "an explicit cells: composition for the withdrawn pair overrides the row's binding"
        );
    }

    /// A spec whose one adjustment record reaches `depth` raw slots to the right: `qsTea` enters at the x-height, offers no exit at all, and extends its entry by a pixel when the window past it reads the way the chain spells. `qsMay` and `qsIt` are modeled so that every slot of the deep window names a rune the prospect can settle.
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
                    &object(&[row("x-height", &[("withdrawal", "\"safe\"")])]),
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

    /// The issue-28 signature, `rebuild/pipeline/fixtures.py`'s `prospect_spec`: `qsPea` exits at both heights and prefers the x-height as a yielding tie-break; `qsTea` enters at both, is exitless when entered at the x-height, and yields its own baseline exit before qsMay·qsIt; an entered `qsMay` is exitless, so `qsTea` joining `qsMay` forecloses the qsMay·qsIt join while `qsTea` declining buys it. The optimistic estimate therefore scores `qsPea`'s baseline exit as if the onward join will happen, and the simulated term sees `qsTea` provably yield it one seat later.
    fn prospect_spec() -> SpecIndex {
        let safe = |height: &str| row(height, &[("withdrawal", "\"safe\"")]);
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
                .ladder()
                .ranked
                .iter()
                .map(|entry| entry.prospect)
                .collect::<Vec<_>>(),
            [0, 0, 0],
            "a non-letter second slot is as unknowable as it looks, in either mode"
        );
    }

    /// A window whose *follower's* cascade raises. `qsTea` reaches toward `qsPea`, whose two entry-only stances tie at every score and whose two prefer records demand one each — so the counterfactual transition the simulated prospect runs is ambiguous, in a window `qsTea` itself settles perfectly well. No sweep over the live alphabet reaches this: E-AMBIGUOUS is unauthored there, and a stranded inner cascade cannot happen either, because the closure already proved the follower has cells.
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
                    &object(&[row("baseline", &[("withdrawal", "\"safe\"")])]),
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
        assert_eq!(trace.settled.seam, Some(baseline));
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

    /// The prospect memo's whole point (issue #166): a key packed to thirty-six bytes, the simulated shape's slots as kinds beside runes, over a value that is the term's byte and a seat.
    #[test]
    fn a_memoized_prospect_is_eight_bytes_under_a_thirty_six_byte_key() {
        assert_eq!(std::mem::size_of::<ProspectKey>(), 36);
        assert_eq!(std::mem::size_of::<(i8, DeltaSeat)>(), 8);
    }

    /// Under a trace memo a settling cascade leaves its window there and declines its prospect entry, so a second window asking the same prospects reads them through the trace memo and journals exactly the delta the first did; a raising cascade's fallback is the entry the memo keeps, and the second window's asks hit it rather than re-running the cascade that raised.
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

    /// Without a trace memo, or in candidacy mode, nothing stands behind the prospect memo to answer a next ask, so every ask is memoized as before — and outside trace-memo mode the entry seats the empty delta, because nothing was journaled.
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
                .all(|&(_, seat)| simulating.deltas.get(seat).is_empty())
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
            fixtures::sym(&index, "qsPea"),
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
            "the record that applied before the crossing demonstrably fired"
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
            &*warm.end_capture(),
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
    fn a_left_that_committed_a_seam_nothing_accepts_is_stranded() {
        let index = ranking_spec(&plain_policy(), &plain_policy());
        let mut engine = Engine::new(&index, no_features());
        let complaint = engine
            .transition_trace(
                &committed_left(&index, 0),
                letter_token(&index, "qsMay"),
                Slots::pair(EDGE, EDGE),
            )
            .expect_err("qsMay enters at the baseline alone");
        assert_eq!(complaint.kind(), SettleErrorKind::Stranded);
        assert_eq!(
            complaint.message(),
            "E-STRANDED: qsPea.stroke.ex-y5 committed an exit at x-height but qsMay has no acceptor cell (the lookahead closure should have prevented this commitment)"
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
        let complaint = settle_pea(&mut engine, Slots::pair(EDGE, EDGE))
            .expect_err("the only stance requires an entry the run edge cannot give it");
        assert_eq!(complaint.kind(), SettleErrorKind::Plain);
        assert_eq!(
            complaint.message(),
            "qsPea has no candidate cells at all in this window"
        );

        let complaint = engine
            .transition_trace(
                &LeftContext::boundary(TokenKind::Edge),
                letter_token(&index, "qsIt"),
                Slots::pair(EDGE, EDGE),
            )
            .expect_err("the registry knows qsIt, but this spec does not model it");
        assert_eq!(complaint.kind(), SettleErrorKind::Plain);
        assert_eq!(complaint.message(), "qsIt is not a modeled rune");
    }
}
