//! The table build's worklist fixpoint, and since issue #78 the only one: every window one configuration's alphabet can reach, each settled exactly once, recorded as the row [`crate::fold`] folds into the two tables a build persists. This is the half of the build the port replaced first — every line here consults the settlement engine — and the fold that once stood on the Python side of the boundary has since come across too, so the return type below is a value handed to the next module rather than a value serialized out of the process.
//!
//! The worklist is the exactness argument rather than a traversal detail, and what follows is its specification, ported from the Python fixpoint retired at issue #78; git history holds it. An item is a left state together with the pins that left was reached under: a settled left is reachable only alongside the right1 that was the producing window's right2, because an entry refusal or an unlock conditioned on the follower makes any other combination contradictory — the left would never have committed there. The right2 allowed-set carries the late-formation guard's second slot onto a surviving pair's trail window, and the right3 allowed-set carries a producing window's enumerated right4 the same way, pinning a depth-4-decided left's successor windows to the third lookahead that was actually behind them. `None` is unrestricted in both, and both are frozen sets compared by content, never by identity.
//!
//! LIFO discipline with the `seen` check at pop time is contract rather than convenience. In the pinned candidacy world the product is order-independent — the dedup is by window key, a hit reuses the recorded settled because the left label is injective into the trace's inputs, and the fired set is the union over a window set no traversal order can change — but under class grain the first visitor of a fiber fixes its representative, so the order rows are traced in reaches the output there. Holding the push order fixed is cheaper than re-deriving, on every later reading, whether it still matters.
//!
//! Both grains live here. Where the deep world holds and the deep-classes flag is on, the deep slots enumerate at class grain (issue 26): the same static option lists, their letters split by [`crate::fiber::DeepFiberDeriver`]'s outcome fibers, one in-flight row per `(base, fiber identity pair)` accumulating the union of admitted members across worklist items, successor pins carrying those member sets instead of singletons, and a content-addressed id per multi-member set in the product's `deep_classes` map. Two standing guards ride with it: the section 2.6 echo check re-traces a second member of every multi-member row at the row's real left and demands the identical row-visible record, and the class-grain partition assertion `DeepPartitionCheck` runs is replayed over the finished product before it is handed back. Where the flag is off, or in the pinned world where class grain cannot arise at all, the label-grain path is the whole function and the deep slots still enumerate — the censuses and the filters are what decide that, not the grain.
//!
//! One engine settles everything, and the two slot filters, the liveness probe and the fiber deriver all borrow it rather than building their own. That is load-bearing twice over: the trace memo makes a re-reached window free, and `Engine::fired` is the product's `cited_provenance`, so a probe running through a second engine would silently shrink what the dead-policy gate is told fired. The same argument makes the liveness probe a single instance lent to both filters and to the deriver.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::rc::Rc;

use crate::census::{FourthSlotFilter, ThirdSlotFilter, fourth_slot_inputs, third_slot_inputs};
use crate::engine::{CacheSize, Engine, EngineModes, Slots};
use crate::error::SettleError;
use crate::fiber::DeepFiberDeriver;
use crate::index::SpecIndex;
use crate::liveness::ProspectLiveness;
use crate::model::Sym;
use crate::options::{FollowerMap, WindowOptions};
use crate::sha256;
use crate::stream::{FixpointProduct, TransitionRow, feature_config_token};
use crate::types::{
    CellId, EDGE, LeftContext, NotesPool, NotesSeat, RightToken, Settled, SettledPool, SettledSeat,
    TokenKind, TransitionTrace, cell_label,
};

/// The label a slot the window does not carry is spelled with, `table.NA_LABEL`. A boundary at right1 puts it in the second slot as well: nothing follows a run edge inside one window.
const NA_LABEL: &str = "#NA";

/// The label the run edge carries, `table.EDGE_LABEL`. The other three boundaries label as the glyphs they ship as, which is why only this one needs a name of its own.
pub const EDGE_LABEL: &str = "#EDGE";

/// The prefix every deep-class id carries, `table.DEEP_CLASS_PREFIX`. The `#` keeps ids outside the glyph namespace, which is what lets a slot label be read as "class or letter" by looking at its first character.
const DEEP_CLASS_PREFIX: &str = "#C";

/// Every label a window slot can carry that is not a letter, `table.BOUNDARYISH`. A deep-class id is never a member of it.
const BOUNDARYISH: [&str; 5] = [EDGE_LABEL, NA_LABEL, "space", "uni200C", "periodcentered"];

/// The boundary lefts the fixpoint seeds from, in the order it seeds them. Every reachable left state is a settled letter or one of these four.
const SEED_KINDS: [TokenKind; 4] = [
    TokenKind::Edge,
    TokenKind::Space,
    TokenKind::Zwnj,
    TokenKind::NamerDot,
];

/// The world one enumeration answers, and at which grain. Python reads all three from module-level defaults an environment variable moves — `kernel_exec.SIMULATED_PROSPECT_DEFAULT`, `kernel_exec.VOTE_SLOTS_DEFAULT` and `kernel_exec.DEEP_CLASSES_DEFAULT` — and this crate has no environment, so the caller passes them and [`Default`] is the shipping configuration.
///
/// The two engine modes are also the deep-world verdict: either one on widens both deep-slot censuses to every rune and hands the filters their liveness arm. `deep_classes` is the issue-26 flag and is an intersection with that verdict rather than a switch, so in the pinned world it is accepted and does nothing, there being no fiber source there to enumerate at class grain.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct EnumerationModes {
    pub simulated_prospect: bool,
    pub vote_slots: bool,
    pub deep_classes: bool,
}

impl Default for EnumerationModes {
    fn default() -> Self {
        Self {
            simulated_prospect: true,
            vote_slots: true,
            deep_classes: true,
        }
    }
}

/// The content-addressed id one deep-slot member set carries, `table.deep_class_id`: `#C` plus the first twelve hex digits of the SHA-256 of the tab-joined members.
///
/// Identical member sets share one id across contexts, across configurations and across builds, which is what keeps cross-config artifact comparison and the ss04 row-identity pin meaningful. The members arrive in the order they are to be hashed in — sorted by letter, as the emission sorts them — because the digest is over the joined text and not over a set.
pub fn deep_class_id(members: &[String]) -> String {
    let digest = sha256::digest_hex(members.join("\t").as_bytes());
    format!("{DEEP_CLASS_PREFIX}{}", &digest[..12])
}

/// What a worklist pin allows: a set compared and hashed by content so two items pinned to the same tokens are one item, behind an [`Rc`] so an item is cheap to clone into the `seen` set. The ordering the `BTreeSet` imposes is interning order and is never read — membership, intersection and equality are the only questions asked.
type Allowed = Rc<BTreeSet<RightToken>>;

/// The six labels one window is keyed by, `table.Window.key`: the input glyph, the left, and the four right slots, each as the id the pool minted for its spelling.
type WindowKey = [Label; 6];

/// One window label's id in the [`LabelPool`]: the position its spelling was minted at, and nothing about the text. Two ids are equal exactly when their spellings are, because the pool mints each spelling once; their order is minting order, which no consumer reads — the product's lexicographic order is reached through [`LabelPool::ranks`] instead.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct Label(u32);

/// The pool one enumeration interns its window labels through: every distinct spelling allocated once, minted a [`Label`] on first sight, and named by that id in every key and row until the run is over.
///
/// A configuration reaches millions of windows over a few tens of thousands of distinct labels, so an owned `String` per slot was both the largest allocation count in the run and a needless copy of the same handful of names in every key, and the shared handle that replaced it was still a fat pointer per slot per row and a cache miss per slot per compare in the sort. A `u32` per slot is a quarter of that, hashes as the integer it is, and costs the run one rank table at the end: the ids sorted by their text once, so the rows can be ordered by rank tuple and come out in exactly the lexicographic order the stream contracts. The spellings are only resolved back to shared handles when the sorted product is materialized, so every raise message and every row reads as it always did.
#[derive(Default)]
struct LabelPool {
    ids: HashMap<Rc<str>, Label>,
    texts: Vec<Rc<str>>,
}

impl LabelPool {
    /// The pool's id for this spelling, minting one only where the pool has none.
    fn intern(&mut self, text: &str) -> Label {
        if let Some(&found) = self.ids.get(text) {
            return found;
        }
        self.mint(Rc::from(text))
    }

    /// The same for a spelling the caller had to build anyway, so that a miss reuses the buffer rather than copying it a second time.
    fn intern_owned(&mut self, text: String) -> Label {
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

    /// The spelling one id was minted for, as the shared handle the product's rows carry.
    fn text(&self, label: Label) -> &Rc<str> {
        &self.texts[label.0 as usize]
    }

    /// One key's six spellings, which is how a complaint names the window it is about: the `Debug` form of six `&str`s is the form the same six shared handles printed as, so the sentences read as they always did.
    fn spelled(&self, key: &WindowKey) -> [&str; 6] {
        key.map(|label| &**self.text(label))
    }

    /// Each id's position among every spelling the pool holds, sorted as text: `ranks[id]` compares as the spelling does, so ordering rows by their rank tuple is ordering them by their key tuple without touching a string.
    fn ranks(&self) -> Vec<u32> {
        let mut by_text: Vec<usize> = (0..self.texts.len()).collect();
        by_text.sort_unstable_by(|&left, &right| self.texts[left].cmp(&self.texts[right]));
        let mut ranks = vec![0u32; self.texts.len()];
        for (rank, id) in by_text.into_iter().enumerate() {
            ranks[id] = u32::try_from(rank).expect("a rank is one of the ids it ranks");
        }
        ranks
    }

    /// One right slot's label, interned — [`right_token_label`] without minting the `String` that function returns.
    fn token(&mut self, index: &SpecIndex, token: RightToken) -> Label {
        match token {
            RightToken::Letter(rune) => {
                let name = index.resolve(rune);
                self.intern(name)
            }
            other => {
                let name = boundary_left_label(other.kind());
                self.intern(name)
            }
        }
    }

    /// A deep slot's label, interned, with an absent slot spelling [`NA_LABEL`].
    fn slot(&mut self, index: &SpecIndex, token: Option<RightToken>) -> Label {
        match token {
            Some(token) => self.token(index, token),
            None => self.intern(NA_LABEL),
        }
    }
}

/// One worklist item: the left state, the input rune, the right1 the left was reached alongside, and the two allowed-sets pinning the slots past it. Its equality is the `seen` key exactly — a [`LeftContext`] is a kind and a settled cell and nothing else, so the derived equality compares the pair the key is meant to be.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct Item {
    left: LeftContext,
    rune: Sym,
    right1: Option<RightToken>,
    right2_allowed: Option<Allowed>,
    right3_allowed: Option<Allowed>,
}

/// What a recorded window carries beyond the six labels that key it — `table.Transition`'s remaining fields, kept apart from the key so the labels are stored once rather than in both the map's key and its value. The two settled records are seats into the run's [`SettledPool`]: a configuration reaches a few thousand distinct records over millions of rows, so a row holding its record by value was holding a copy — adjustments allocation and all — that hundreds of thousands of its neighbors held too (issue #162). The provenance is a seat into the run's [`NotesPool`] by the same argument, the prospect is the byte its zero-or-one range needs, and the joint flag sits beside it, so the row is sixteen bytes with no padding and no heap of its own (issue #163). The outcome is not carried at all: it is the settled cell's label, a property of the seat, and is resolved once per seat when the product is materialized rather than once per row while the worklist runs.
struct Row {
    settled: SettledSeat,
    left_settled: Option<SettledSeat>,
    provenance: NotesSeat,
    prospect: i8,
    joint: bool,
}

/// The prospect term as a row holds it. The engine answers in the `i64` its join-count arithmetic sums, but the term itself is a seam count — one when the follower's seam is claimed and zero otherwise, in either candidacy world — so a byte carries it, and a wider answer is an engine that stopped answering with a count.
fn prospect_byte(prospect: i64) -> i8 {
    i8::try_from(prospect).expect("a prospect is a seam count, zero or one")
}

/// One third-slot entry of a class-grain window: the boundary token where the entry is a boundary, the seat of the fiber where it is a fiber, and the members this item's pins admitted.
type Slot3Entry = (Option<RightToken>, Option<usize>, Vec<RightToken>);

/// One fourth-slot entry of a class-grain window: the r4 group, or `None` where the fourth slot is dead and the row carries `#NA` there.
type Slot4Entry = Option<Vec<RightToken>>;

/// What an in-flight class-grain row is keyed by while the worklist runs: the four near labels, the third slot's identity, and the fourth's full member group.
type PendingKey = (Label, Label, Label, Label, Identity3, Slot4Entry);

/// The third slot's identity inside a [`PendingKey`]: the boundary token itself where the entry is a boundary, and the fiber's full member tuple where it is a fiber. Naming the two alternatives is that distinction made checkable rather than left to a coincidence of representation.
///
/// The members are the fiber's whole membership rather than the admitted subset, which is what lets two worklist items whose pins admit different subsets of one fiber accumulate into a single row.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
enum Identity3 {
    Boundary(RightToken),
    Members(Vec<RightToken>),
}

/// One in-flight class-grain row: the representative trace's row-visible record, the r3 members accumulating across worklist items, and the frame the echo traces replay after the drain.
///
/// The r4 members carry no pins and so are full from the first item, which is why they are a plain group here where the third slot's are a set.
struct PendingDeepRow {
    left_context: LeftContext,
    left_label: Label,
    input_label: Label,
    token: RightToken,
    right1: RightToken,
    right2: RightToken,
    boundary3: Option<RightToken>,
    admitted3: BTreeSet<RightToken>,
    members4: Slot4Entry,
    rep3: RightToken,
    rep4: Option<RightToken>,
    settled: SettledSeat,
    left_settled: Option<SettledSeat>,
    provenance: NotesSeat,
    prospect: i8,
    joint: bool,
}

impl PendingDeepRow {
    /// Whether an echo trace's record is the representative's, over exactly the four fields a row carries: the settled triple and the notes — the representative's, each read back through the pool its seat names — the prospect and the joint-floor flag. Nothing about the ranking that reached them is compared, because nothing about it reaches the row.
    fn echoes(&self, seats: &SettledPool, notes: &NotesPool, echo: &TransitionTrace) -> bool {
        echo.settled == *seats.get(self.settled)
            && echo.prospect == i64::from(self.prospect)
            && echo.joint_floor == self.joint
            && echo.notes == notes.get(self.provenance)
    }
}

/// One configuration's whole fixpoint, and the value [`crate::fold::fold_product`] folds: the rows in their key order, the deep-class map their class tokens resolve through, the cells they settle into, and the provenance the engine fired while tabulating. Serialized it is `table.FixpointProduct`, which `kernel_exec.enumerate_transitions` parses back — the enumeration at the grain a table drops on its way to a window row. No build stage and no tool asks for it; the rebuild suite is what keeps that path exercised.
///
/// The engine is built here rather than handed in, out of `modes` — which is also what decides whether the censuses widen, whether the filters carry their liveness arm, and whether the deep slots enumerate at class grain, because none of those three is meaningful without the others.
pub fn enumerate_transitions(
    index: &SpecIndex,
    features: &[Sym],
    modes: EnumerationModes,
) -> Result<FixpointProduct, String> {
    enumerate_seeded(index, features, modes, contract_seeds, None)
}

/// [`enumerate_transitions`] with the `--cache-census` diagnostic switched on: the same product, plus a `[c]` line per collection on the way past the drain saying how many entries it held and in how many buckets, the elimination text the memos were carrying, and the process's resident size sampled either side of the drain and the sort. Nothing here reaches the stream — the lines are the caller's to put on stderr — and nothing is computed unless the caller asked, so the shipping path pays nothing for it.
///
/// The instrument exists because every RAM decision this crate faces reduces to entry counts, and a count read off a live alphabet settles in one run what a struct-size argument can only estimate.
pub fn enumerate_censused(
    index: &SpecIndex,
    features: &[Sym],
    modes: EnumerationModes,
    census: &mut Vec<String>,
) -> Result<FixpointProduct, String> {
    enumerate_seeded(index, features, modes, contract_seeds, Some(census))
}

/// [`enumerate_transitions`] with the seeding left open, which is how the order-independence of the pinned world is testable at all. Production always passes [`contract_seeds`]; a test passes a permutation and asserts the same product, which is a statement about that world rather than about the discipline, since class grain makes the first visitor of a fiber decide its representative.
fn enumerate_seeded(
    index: &SpecIndex,
    features: &[Sym],
    modes: EnumerationModes,
    seeds: fn(&WindowOptions<'_>) -> Vec<Item>,
    mut census: Option<&mut Vec<String>>,
) -> Result<FixpointProduct, String> {
    let mut engine = Engine::with_modes(
        index,
        features.iter().copied(),
        EngineModes {
            simulated_prospect: modes.simulated_prospect,
            vote_slots: modes.vote_slots,
            trace_memo: true,
            // The rows this fixpoint writes read the settled triple, the prospect, the joint floor and the notes; nothing here ever asks a trace how it was decided, and the ladder that would answer costs more than every other explain-only allocation together.
            explain_ladder: false,
            ..EngineModes::default()
        },
    );
    let config = feature_config_token(index, features.iter().copied());
    let mut options = WindowOptions::new(index).map_err(complaint)?;
    // The deep-world verdict over this engine's own modes, which is the one place the two flags are read as a single question.
    let deep_world = modes.simulated_prospect || modes.vote_slots;
    let deep_inputs = third_slot_inputs(index, deep_world);
    let deep4_inputs = fourth_slot_inputs(index, deep_world);
    let mut third_slot_matters = ThirdSlotFilter::new(index);
    let mut fourth_slot_matters = FourthSlotFilter::new(index);
    let mut liveness = deep_world.then(|| ProspectLiveness::new(index));
    let class_grain = modes.deep_classes && deep_world;
    let mut deriver = class_grain.then(DeepFiberDeriver::new);

    let mut labels = LabelPool::default();
    let mut seats = SettledPool::default();
    let mut notes = NotesPool::default();
    let mut transitions: HashMap<WindowKey, Row> = HashMap::new();
    // The pending class-grain rows, split from the seats their keys hold, so that the echo pass walks them in the order they were created.
    let mut pending_rows: Vec<PendingDeepRow> = Vec::new();
    let mut pending_seats: HashMap<PendingKey, usize> = HashMap::new();
    let mut seen: HashSet<Item> = HashSet::new();
    let mut worklist = seeds(&options);

    while let Some(item) = worklist.pop() {
        // The `seen` set is tested at pop time and added to there too; one insertion answers both, since a set that already held the item is exactly the skip.
        if !seen.insert(item.clone()) {
            continue;
        }
        let Item {
            left,
            rune,
            right1: right1_constraint,
            right2_allowed,
            right3_allowed,
        } = item;
        let locked = left.kind == TokenKind::Zwnj && index.is_entry_bearing(rune);
        let raw = index.resolve(rune);
        let input_label = if locked {
            labels.intern_owned(locked_glyph_name(raw))
        } else {
            labels.intern(raw)
        };
        let left_label = if left.kind == TokenKind::Letter {
            let settled = left.settled.as_ref().expect(
                "a letter left carries the cell it settled into, which the fixpoint asserts on the way in",
            );
            labels.intern_owned(cell_label(index, &settled.cell))
        } else {
            labels.intern(boundary_left_label(left.kind))
        };
        // A letter left is the settled record of a row already recorded, so this is a hit on every item past the seeds; it is asked once per item and compared by integer on every window the item reaches.
        let left_seat: Option<SettledSeat> =
            left.settled.as_ref().map(|settled| seats.seat(settled));
        // The trace reads the raw letter whatever the label says: locking is a fact about the glyph the emitted lookup substitutes, not about what settles.
        let token = RightToken::Letter(rune);
        let right1_options: Vec<RightToken> = match right1_constraint {
            Some(constraint) => vec![constraint],
            None => boundaries_then_letters(&options),
        };

        for right1 in right1_options {
            let follower_map: Option<Rc<FollowerMap>> = if right1.kind() == TokenKind::Letter
                && options.formation_pairs.contains(&(rune, right1.letter()))
            {
                match options.survivable.get(&(rune, right1.letter())) {
                    Some(map) => Some(Rc::clone(map)),
                    // A formation pair with no survivable window at all is inadmissible outright: the pair always forms, so no window of it enumerates.
                    None => continue,
                }
            } else {
                None
            };
            let right2_options: Vec<RightToken> = if right1.kind() == TokenKind::Letter {
                let lead = right1.letter();
                let mut kept: Vec<RightToken> = boundaries_then_letters(&options);
                kept.retain(|option| {
                    !(option.kind() == TokenKind::Letter
                        && options.formation_pairs.contains(&(lead, option.letter()))
                        && !options.survivable.contains_key(&(lead, option.letter())))
                });
                if let Some(map) = &follower_map {
                    kept.retain(|option| {
                        option.kind() == TokenKind::Letter && map.contains_key(&option.letter())
                    });
                }
                if let Some(pin) = &right2_allowed {
                    kept.retain(|option| pin.contains(option));
                }
                if options.liga_sequences.contains_key(&rune) {
                    kept = retain_formed_before(&mut options, kept, rune, |option| {
                        (right1, Some(option))
                    })?;
                }
                if options.liga_sequences.contains_key(&lead) {
                    kept = retain_formed_before(&mut options, kept, lead, |option| (option, None))?;
                }
                kept
            } else {
                vec![EDGE]
            };

            for right2 in right2_options {
                let deep3_live = deep_inputs.contains(&rune)
                    && right1.kind() == TokenKind::Letter
                    && right2.kind() == TokenKind::Letter
                    && third_slot_matters
                        .matters(
                            &mut engine,
                            liveness.as_mut(),
                            rune,
                            right1.letter(),
                            right2.letter(),
                        )
                        .map_err(complaint)?;

                if deep3_live && let Some(deriver) = deriver.as_mut() {
                    let probe = liveness
                        .as_mut()
                        .expect("class grain is a deep world, where the liveness probe exists");
                    let context = deriver
                        .context(
                            &mut engine,
                            probe,
                            &mut fourth_slot_matters,
                            &mut options,
                            rune,
                            right1.letter(),
                            right2.letter(),
                        )
                        .map_err(complaint)?;
                    let mut slot3_entries: Vec<Slot3Entry> = Vec::new();
                    for &option in &context.boundary_options {
                        if right3_allowed
                            .as_ref()
                            .is_none_or(|pin| pin.contains(&option))
                        {
                            slot3_entries.push((Some(option), None, vec![option]));
                        }
                    }
                    for (seat, fiber) in context.fibers.iter().enumerate() {
                        let admitted: Vec<RightToken> = fiber
                            .members
                            .iter()
                            .copied()
                            .filter(|member| {
                                right3_allowed
                                    .as_ref()
                                    .is_none_or(|pin| pin.contains(member))
                            })
                            .collect();
                        if !admitted.is_empty() {
                            slot3_entries.push((None, Some(seat), admitted));
                        }
                    }
                    for (boundary3, fiber3, admitted3) in slot3_entries {
                        // The census gate is applied here rather than inside the deriver: a fiber's own `fourth_matters` is the raw filter verdict, and only the enumeration knows whether this input is censused deep enough to spend it.
                        let slot4_entries: Vec<Slot4Entry> = match fiber3 {
                            Some(seat)
                                if deep4_inputs.contains(&rune)
                                    && context.fibers[seat].fourth_matters =>
                            {
                                context.fibers[seat]
                                    .r4_groups
                                    .iter()
                                    .cloned()
                                    .map(Some)
                                    .collect()
                            }
                            _ => vec![None],
                        };
                        // The identity is the fiber's *full* member tuple rather than the admitted subset, so two items whose pins admit different subsets of one fiber accumulate into one row instead of splitting it.
                        let identity3 = match boundary3 {
                            Some(token) => Identity3::Boundary(token),
                            None => Identity3::Members(fiber3.map_or_else(Vec::new, |seat| {
                                context.fibers[seat].members.clone()
                            })),
                        };
                        for members4 in slot4_entries {
                            let rep3 = admitted3[0];
                            let rep4 = members4.as_ref().map(|group| group[0]);
                            let pending_key: PendingKey = (
                                input_label,
                                left_label,
                                labels.token(index, right1),
                                labels.token(index, right2),
                                identity3.clone(),
                                members4.clone(),
                            );
                            let settled = match pending_seats.get(&pending_key) {
                                Some(&seat) => {
                                    let record = &mut pending_rows[seat];
                                    if record.left_settled != left_seat {
                                        let display: WindowKey = [
                                            input_label,
                                            left_label,
                                            labels.token(index, right1),
                                            labels.token(index, right2),
                                            labels.token(index, rep3),
                                            labels.slot(index, rep4),
                                        ];
                                        return Err(partition_complaint(
                                            index,
                                            &labels.spelled(&display),
                                            record.left_settled.map(|seat| seats.get(seat)),
                                            left.settled.as_ref(),
                                        ));
                                    }
                                    record.admitted3.extend(admitted3.iter().copied());
                                    seats.get(record.settled).clone()
                                }
                                None => {
                                    let trace = engine
                                        .transition_trace(
                                            &left,
                                            token,
                                            Slots::new(right1, right2, rep3, rep4.unwrap_or(EDGE)),
                                        )
                                        .map_err(complaint)?;
                                    pending_seats.insert(pending_key, pending_rows.len());
                                    pending_rows.push(PendingDeepRow {
                                        left_context: left.clone(),
                                        left_label,
                                        input_label,
                                        token,
                                        right1,
                                        right2,
                                        boundary3,
                                        admitted3: admitted3.iter().copied().collect(),
                                        members4: members4.clone(),
                                        rep3,
                                        rep4,
                                        settled: seats.seat(&trace.settled),
                                        left_settled: left_seat,
                                        provenance: notes.seat(trace.notes),
                                        prospect: prospect_byte(trace.prospect),
                                        joint: trace.joint_floor,
                                    });
                                    trace.settled
                                }
                            };
                            worklist.push(Item {
                                left: LeftContext::letter(settled),
                                rune: right1.letter(),
                                right1: Some(right2),
                                right2_allowed: Some(Rc::new(admitted3.iter().copied().collect())),
                                right3_allowed: members4
                                    .as_ref()
                                    .map(|group| Rc::new(group.iter().copied().collect())),
                            });
                        }
                    }
                    continue;
                }

                let right3_slots: Vec<Option<RightToken>> = if deep3_live {
                    let mut candidates = options
                        .right3_options(right1, right2, follower_map.as_deref())
                        .map_err(complaint)?;
                    if let Some(pin) = &right3_allowed {
                        candidates.retain(|option| pin.contains(option));
                    }
                    candidates.into_iter().map(Some).collect()
                } else {
                    vec![None]
                };

                for right3 in right3_slots {
                    let fourth_live = match right3 {
                        Some(third) => {
                            deep4_inputs.contains(&rune)
                                && third.kind() == TokenKind::Letter
                                && fourth_slot_matters
                                    .matters(
                                        &mut engine,
                                        liveness.as_mut(),
                                        rune,
                                        right1.letter(),
                                        right2.letter(),
                                        third.letter(),
                                    )
                                    .map_err(complaint)?
                        }
                        None => false,
                    };
                    let right4_slots: Vec<Option<RightToken>> = if fourth_live {
                        options
                            .right4_options(
                                right1,
                                right2,
                                right3.expect("a live fourth slot has a concrete third"),
                            )
                            .map_err(complaint)?
                            .into_iter()
                            .map(Some)
                            .collect()
                    } else {
                        vec![None]
                    };

                    for right4 in right4_slots {
                        let window_key: WindowKey = [
                            input_label,
                            left_label,
                            labels.token(index, right1),
                            if right1.kind() == TokenKind::Letter {
                                labels.token(index, right2)
                            } else {
                                labels.intern(NA_LABEL)
                            },
                            labels.slot(index, right3),
                            labels.slot(index, right4),
                        ];
                        // A worklist item with different pins can re-reach a window key already recorded; the recorded row's settled state is what a re-trace would return, because the left label is injective into the trace's inputs, so a hit skips straight to the successor enqueue — whose pins still differ per item. The left-state comparison is that premise made executable, and can only fire if `cell_label` stops being injective over settled lefts.
                        let settled = if let Some(existing) = transitions.get(&window_key) {
                            if existing.left_settled != left_seat {
                                return Err(partition_complaint(
                                    index,
                                    &labels.spelled(&window_key),
                                    existing.left_settled.map(|seat| seats.get(seat)),
                                    left.settled.as_ref(),
                                ));
                            }
                            seats.get(existing.settled).clone()
                        } else {
                            let trace = engine
                                .transition_trace(
                                    &left,
                                    token,
                                    Slots::new(
                                        right1,
                                        right2,
                                        right3.unwrap_or(EDGE),
                                        right4.unwrap_or(EDGE),
                                    ),
                                )
                                .map_err(complaint)?;
                            transitions.insert(
                                window_key,
                                Row {
                                    settled: seats.seat(&trace.settled),
                                    left_settled: left_seat,
                                    provenance: notes.seat(trace.notes),
                                    prospect: prospect_byte(trace.prospect),
                                    joint: trace.joint_floor,
                                },
                            );
                            trace.settled
                        };

                        if right1.kind() == TokenKind::Letter {
                            let successor_allowed = if let Some(third) = right3 {
                                Some(singleton(third))
                            } else {
                                let from_map = follower_map
                                    .as_ref()
                                    .and_then(|map| map.get(&right2.letter()).cloned().flatten());
                                // A right3 pin this window could not enumerate — the input is not deep — still names the raw token one past it, which is the successor's right2. Forward it, or a depth-4-decided left leaks follower windows no text can reach and the conform transition gate reports them as dead.
                                match (from_map, &right3_allowed) {
                                    (allowed, None) => allowed.map(Rc::new),
                                    (None, Some(pin)) => Some(Rc::clone(pin)),
                                    (Some(allowed), Some(pin)) => Some(Rc::new(
                                        allowed.intersection(pin.as_ref()).copied().collect(),
                                    )),
                                }
                            };
                            worklist.push(Item {
                                left: LeftContext::letter(settled),
                                rune: right1.letter(),
                                right1: Some(right2),
                                right2_allowed: successor_allowed,
                                right3_allowed: right4.map(singleton),
                            });
                        }
                    }
                }
            }
        }
    }

    let mut deep_classes: Vec<(String, Vec<String>)> = Vec::new();
    let mut named_classes: HashSet<String> = HashSet::new();
    // The section 2.6 echo check, and the class rows' emission with it: for every multi-member row the last admitted member is re-traced at the row's real left — and the last r4 member at the representative third — and its whole row-visible record must equal the representative's. That is the standing real-left, real-entry, real-adjustment guard on the virtual-left collapse the fibers import, two members deep on every build.
    for pending in &pending_rows {
        let (label3, admitted3) = match pending.boundary3 {
            Some(token) => (labels.token(index, token), vec![token]),
            None => {
                let mut members: Vec<RightToken> = pending.admitted3.iter().copied().collect();
                members.sort_by(|left, right| {
                    index
                        .resolve(left.letter())
                        .cmp(index.resolve(right.letter()))
                });
                let names: Vec<String> = members
                    .iter()
                    .map(|member| index.resolve(member.letter()).to_owned())
                    .collect();
                (
                    labels.intern_owned(deep_label(&mut deep_classes, &mut named_classes, names)),
                    members,
                )
            }
        };
        let label4 = match pending.members4.as_deref() {
            None => labels.intern(NA_LABEL),
            Some(group) if group[0].kind() != TokenKind::Letter => labels.token(index, group[0]),
            Some(group) => labels.intern_owned(deep_label(
                &mut deep_classes,
                &mut named_classes,
                group
                    .iter()
                    .map(|member| index.resolve(member.letter()).to_owned())
                    .collect(),
            )),
        };
        let window_key: WindowKey = [
            pending.input_label,
            pending.left_label,
            labels.token(index, pending.right1),
            labels.token(index, pending.right2),
            label3,
            label4,
        ];
        let rep4 = pending.rep4.unwrap_or(EDGE);
        if pending.boundary3.is_none() && admitted3.len() > 1 {
            let last3 = echo_member(&admitted3, pending.rep3);
            let echo = engine
                .transition_trace(
                    &pending.left_context,
                    pending.token,
                    Slots::new(pending.right1, pending.right2, last3, rep4),
                )
                .map_err(complaint)?;
            if !pending.echoes(&seats, &notes, &echo) {
                return Err(echo_mismatch(
                    index,
                    &labels.spelled(&window_key),
                    last3,
                    pending,
                    seats.get(pending.settled),
                    notes.get(pending.provenance),
                    &echo,
                ));
            }
        }
        if let Some(group) = pending.members4.as_deref()
            && group[0].kind() == TokenKind::Letter
            && group.len() > 1
        {
            let last4 = group[group.len() - 1];
            let echo = engine
                .transition_trace(
                    &pending.left_context,
                    pending.token,
                    Slots::new(pending.right1, pending.right2, pending.rep3, last4),
                )
                .map_err(complaint)?;
            if !pending.echoes(&seats, &notes, &echo) {
                return Err(echo_mismatch(
                    index,
                    &labels.spelled(&window_key),
                    last4,
                    pending,
                    seats.get(pending.settled),
                    notes.get(pending.provenance),
                    &echo,
                ));
            }
        }
        if transitions.contains_key(&window_key) {
            return Err(format!(
                "deep-class window {:?} collides with an existing row",
                labels.spelled(&window_key)
            ));
        }
        transitions.insert(
            window_key,
            Row {
                settled: pending.settled,
                left_settled: pending.left_settled,
                provenance: pending.provenance,
                prospect: pending.prospect,
                joint: pending.joint,
            },
        );
    }

    if let Some(lines) = census.as_mut() {
        lines.push(
            CacheSize::of("transitions", transitions.len(), transitions.capacity()).line(&config),
        );
        lines.push(CacheSize::of("seen", seen.len(), seen.capacity()).line(&config));
        lines.push(CacheSize::of("settled_seats", seats.len(), seats.capacity()).line(&config));
        lines.push(CacheSize::of("notes", notes.len(), notes.capacity()).line(&config));
        lines.push(
            CacheSize::of("labels", labels.texts.len(), labels.texts.capacity()).line(&config),
        );
        lines.push(
            CacheSize::of("pending_rows", pending_rows.len(), pending_rows.capacity())
                .line(&config),
        );
        lines.push(
            CacheSize::of(
                "pending_seats",
                pending_seats.len(),
                pending_seats.capacity(),
            )
            .line(&config),
        );
        for size in engine.cache_census() {
            lines.push(size.line(&config));
        }
        lines.push(format!(
            "[c] {config} elimination_text bytes={}",
            engine.elimination_text_bytes()
        ));
        lines.push(format!(
            "[c] {config} resident_before_release kb={}",
            resident_kb()
        ));
    }

    // Snapshotted here rather than past the sort, and before the memos go: what follows is a drain, a sort and an assertion, none of which touches the fired set, and the assertion's own re-tracing is deliberately outside it — anything it were to fire could not reach the stream anyway.
    let cited_provenance = engine
        .fired()
        .iter()
        .map(|pointer| pointer.text(index))
        .collect();
    // The drain and the sort below are the run's other working set, and the memos that answered the worklist are of no further use to them. Releasing here rather than at the end of the function is what keeps the two from coexisting, which would otherwise be the enumeration's peak.
    engine.release_memos();
    if let Some(lines) = census.as_mut() {
        lines.push(format!(
            "[c] {config} resident_after_release kb={}",
            resident_kb()
        ));
    }

    // A row's outcome is its settled cell's label, so the spelling is interned once per seat here rather than once per row in the loop above; every seat is a cell some row settled into, so nothing is interned that no row names.
    let seat_table = seats.into_table();
    let outcomes: Vec<Label> = seat_table
        .iter()
        .map(|settled| labels.intern_owned(cell_label(index, &settled.cell)))
        .collect();
    // The sort runs over the ids, through the rank table, before a single spelling is resolved: a rank tuple compares as the key tuple does, because every id ranks as its text, and the keys are distinct, so the unstable sort reaches the one order a stable one would. Only then are the spellings put back, in the order the rows will be written in.
    let ranks = labels.ranks();
    let mut keyed: Vec<(WindowKey, Row)> = transitions.into_iter().collect();
    keyed.sort_unstable_by_key(|(key, _)| key.map(|label| ranks[label.0 as usize]));
    let rows: Vec<TransitionRow> = keyed
        .into_iter()
        .map(|(key, row)| {
            let [input_glyph, left, right1, right2, right3, right4] =
                key.map(|label| Rc::clone(labels.text(label)));
            TransitionRow {
                input_glyph,
                left,
                right1,
                right2,
                right3,
                right4,
                outcome: Rc::clone(labels.text(outcomes[row.settled.index()])),
                settled: row.settled,
                left_settled: row.left_settled,
                provenance: row.provenance,
                prospect: row.prospect,
                joint: row.joint,
            }
        })
        .collect();
    if let Some(lines) = census.as_mut() {
        lines.push(format!(
            "[c] {config} resident_after_sort kb={}",
            resident_kb()
        ));
    }
    // The product's cells are a set rather than a per-row list; collapsing the repeats here rather than at the emitter keeps one cell per seat out of one clone per row. A row's seat is checked before its cell because most rows share a seat already seen, and an integer set answers that without touching the cell at all.
    let mut seen_seats: HashSet<SettledSeat> = HashSet::new();
    let mut counted: HashSet<&CellId> = HashSet::new();
    let mut cells: Vec<CellId> = Vec::new();
    for row in &rows {
        if seen_seats.insert(row.settled) {
            let cell = &seat_table[row.settled.index()].cell;
            if counted.insert(cell) {
                cells.push(cell.clone());
            }
        }
    }
    let product = FixpointProduct {
        config,
        transitions: rows,
        deep_classes,
        cited_provenance,
        cells,
        seats: seat_table,
        notes: notes.into_table(),
    };
    if let Some(deriver) = deriver.as_mut() {
        let mut check = DeepPartitionCheck {
            engine: &mut engine,
            options: &mut options,
            deriver,
            liveness: liveness.as_mut(),
            third_slot_matters: &mut third_slot_matters,
            fourth_slot_matters: &mut fourth_slot_matters,
            deep_inputs: &deep_inputs,
            deep4_inputs: &deep4_inputs,
            contexts: HashMap::new(),
            r4_lists: HashMap::new(),
        };
        check.run(&product)?;
    }
    Ok(product)
}

/// The seeds the fixpoint starts from: every letter against every boundary left, boundary-major, unpinned. Pushed in this order and popped from the back, which is the traversal class grain reads: the first item to reach a fiber fixes that row's representative.
fn contract_seeds(options: &WindowOptions<'_>) -> Vec<Item> {
    let mut seeds = Vec::with_capacity(SEED_KINDS.len() * options.letters.len());
    for kind in SEED_KINDS {
        for &rune in &options.letters {
            seeds.push(Item {
                left: LeftContext::boundary(kind),
                rune,
                right1: None,
                right2_allowed: None,
                right3_allowed: None,
            });
        }
    }
    seeds
}

/// The option list every right slot starts from: the four boundaries, then the letters in sorted-name order.
fn boundaries_then_letters(options: &WindowOptions<'_>) -> Vec<RightToken> {
    let mut all = Vec::with_capacity(options.right_boundaries.len() + options.right_letters.len());
    all.extend_from_slice(&options.right_boundaries);
    all.extend_from_slice(&options.right_letters);
    all
}

/// The two ligature filters of the right2 pipeline: keep the options the formed `liga` can still stand before, with `slots` naming the two post-formation neighbors each option supplies. A loop rather than a `retain`, because the verdict consults the guard and can fail.
///
/// The deeper slots' pipelines run the same shape inside [`WindowOptions`], and the split is deliberate: the second slot's filters are spelled inline here while the third and fourth slots' live in [`WindowOptions`], because only the deeper two have a second caller in the partition assertion. A filter added to this pipeline therefore belongs here, and one added to a deeper pipeline belongs there.
fn retain_formed_before(
    options: &mut WindowOptions<'_>,
    candidates: Vec<RightToken>,
    liga: Sym,
    slots: impl Fn(RightToken) -> (RightToken, Option<RightToken>),
) -> Result<Vec<RightToken>, String> {
    let mut kept = Vec::with_capacity(candidates.len());
    for option in candidates {
        let (next1, next2) = slots(option);
        if options
            .liga_formed_before(liga, next1, next2)
            .map_err(complaint)?
        {
            kept.push(option);
        }
    }
    Ok(kept)
}

/// This process's resident size in kibibytes, or `0` where the platform would not say. Asked of `ps` rather than of the C library because the crate takes no dependency and declares no foreign functions for a diagnostic; it runs twice per censused configuration and never on the shipping path.
fn resident_kb() -> u64 {
    let pid = std::process::id();
    std::process::Command::new("/bin/ps")
        .args(["-o", "rss=", "-p", &pid.to_string()])
        .output()
        .ok()
        .and_then(|out| String::from_utf8(out.stdout).ok())
        .and_then(|text| text.trim().parse::<u64>().ok())
        .unwrap_or(0)
}

/// One token as a pin's allowed-set.
fn singleton(token: RightToken) -> Allowed {
    Rc::new(BTreeSet::from([token]))
}

/// The ZWNJ chokepoint twin's display name for a raw input glyph, `model.locked_glyph_name`. Public because the string replay labels an entry-bearing input after a ZWNJ the way the enumeration does.
pub fn locked_glyph_name(raw_name: &str) -> String {
    format!("{raw_name}.noentry")
}

/// One right slot's label: a letter is its rune's name and every boundary its own spelling. Public because the `liveness-cases` verb answers in the same vocabulary.
pub fn right_token_label(index: &SpecIndex, token: RightToken) -> String {
    match token {
        RightToken::Letter(rune) => index.resolve(rune).to_owned(),
        other => boundary_left_label(other.kind()).to_owned(),
    }
}

/// The label a boundary carries at either end of a window, `table.BOUNDARY_LEFT_LABELS`: the run edge's own name, and for the other three the glyph the boundary ships as. A letter or an unknown panics here exactly as the Python mapping raises `KeyError` for it.
fn boundary_left_label(kind: TokenKind) -> &'static str {
    match kind {
        TokenKind::Edge => EDGE_LABEL,
        TokenKind::Space => "space",
        TokenKind::Zwnj => "uni200C",
        TokenKind::NamerDot => "periodcentered",
        other => panic!(
            "{} has no boundary label, exactly as table.BOUNDARY_LEFT_LABELS has no key for it",
            other.as_str()
        ),
    }
}

/// One settlement outcome as the verb's one-line complaint. The two failure families the fixpoint can raise — a window that will not settle, and the partition premise below — are one sentence at this boundary, because exit 1 with the sentence on stderr is the verb's whole answer to either.
fn complaint(error: SettleError) -> String {
    error.to_string()
}

/// The partition premise's sentence: one window label reached from two different left states, which means `cell_label` has stopped telling those states apart. The window and the two states are spelled in the crate's own idiom — nothing compares this text, and a `Settled` printed structurally would name its heights by interning id.
fn partition_complaint(
    index: &SpecIndex,
    key: &[&str; 6],
    existing: Option<&Settled>,
    arriving: Option<&Settled>,
) -> String {
    format!(
        "window {key:?} reached from two left states sharing one label: {} vs {}",
        left_state_text(index, existing),
        left_state_text(index, arriving)
    )
}

/// One left state as the partition complaint names it: the cell it settled into, the seam it committed, and the connector pixels on that seam.
fn left_state_text(index: &SpecIndex, settled: Option<&Settled>) -> String {
    match settled {
        None => "a boundary left".to_owned(),
        Some(state) => format!(
            "{} (seam {}, extension {})",
            cell_label(index, &state.cell),
            state.seam.map_or("none", |height| index.resolve(height)),
            state.extension
        ),
    }
}

/// The label one deep-slot member set is spelled by: the bare letter for a class of one, and a content-addressed id otherwise, recorded in the map on the way past.
///
/// A class of one is deliberately not given an id. The expansion downstream reads a bare label as itself, so an id there would cost a map entry and buy nothing, and it would put a `#C` token in front of consumers for a slot that names exactly one letter.
fn deep_label(
    classes: &mut Vec<(String, Vec<String>)>,
    named: &mut HashSet<String>,
    members: Vec<String>,
) -> String {
    if members.len() == 1 {
        return members
            .into_iter()
            .next()
            .expect("one member is one member");
    }
    let token = deep_class_id(&members);
    if named.insert(token.clone()) {
        classes.push((token.clone(), members));
    }
    token
}

/// The member a class row's echo re-traces: the last of the admitted members, or the first of them where that last one is the representative the row was built from. A class whose last member is its representative would otherwise echo the very window the row already carries, which would check nothing at all.
fn echo_member(members: &[RightToken], representative: RightToken) -> RightToken {
    let last = members[members.len() - 1];
    if last == representative {
        members[0]
    } else {
        last
    }
}

/// The echo check's `PartitionError` sentence: a member of a class row traced something the representative did not, which is the virtual-left fiber collapse failing at real-left grain. The representative's settled record and notes arrive resolved, since the row holds only their seats.
fn echo_mismatch(
    index: &SpecIndex,
    key: &[&str; 6],
    member: RightToken,
    expected: &PendingDeepRow,
    expected_settled: &Settled,
    expected_notes: &[String],
    got: &TransitionTrace,
) -> String {
    format!(
        "deep-class echo mismatch at {key:?}: member {} traces {} where the representative traced {}",
        right_token_label(index, member),
        row_record_text(
            index,
            &got.settled,
            got.prospect,
            got.joint_floor,
            &got.notes
        ),
        row_record_text(
            index,
            expected_settled,
            i64::from(expected.prospect),
            expected.joint,
            expected_notes
        )
    )
}

/// One row-visible record as the echo complaint names it — the four fields the check compares and nothing else.
fn row_record_text(
    index: &SpecIndex,
    settled: &Settled,
    prospect: i64,
    joint: bool,
    provenance: &[String],
) -> String {
    format!(
        "{} prospect {prospect}, joint {joint}, provenance {provenance:?}",
        left_state_text(index, Some(settled))
    )
}

/// Whether a slot label is one of the five non-letter spellings, `table.BOUNDARYISH`.
fn boundaryish(label: &str) -> bool {
    BOUNDARYISH.contains(&label)
}

/// The rune one slot label names, or `None` when the label is not a modeled rune's name — a boundary spelling, a class id, or a name this spec never interned.
fn rune_of(index: &SpecIndex, label: &str) -> Option<Sym> {
    index.sym_of(label).filter(|name| index.is_modeled(*name))
}

/// The member labels one deep-slot field stands for, `DecisionTable.token_members`: the class map's entry for a class id, else the label itself, so a caller can expand any right3 or right4 field uniformly.
fn token_members<'p>(classes: &HashMap<&'p str, &'p [String]>, token: &'p str) -> Vec<&'p str> {
    match classes.get(token) {
        Some(members) => members.iter().map(String::as_str).collect(),
        None => vec![token],
    }
}

/// One live context's fiber partition as the assertion reads it: which letters the static option list admits, and which fiber each one sits in.
struct ContextPartition {
    static_letters: HashSet<Sym>,
    fiber_of: HashMap<Sym, usize>,
}

/// The class-grain hard invariant (issue 26), together with the enumeration-side scaffolding it replays against.
///
/// It runs over the crate's own product before the stream is written, which is the only place it can run at all: the scaffolding it consults never crosses the boundary the stream is, so a fold-side reader could not restate it. Everything it consults was already consulted during enumeration — the two filters' memos are warm, every live context's fibers are derived, and `right4_options` is pure — so the replay adds no probes and therefore no provenance.
///
/// What it asserts, per base: the observed r3 letter tokens' member sets are pairwise disjoint, each inside the recomputed static option list and inside one fiber of its context's partition; right3 is non-`#NA` exactly where the pre-gate and the third filter say live, which is the `#NA` biconditional restated over tokens; one slot deeper, r4 member sets are disjoint per `(base, r3 token)`, every member of an r3 token agrees on the `fourth_slot_matters` verdict and induces the identical computed r4 option list; and every class id resolves through the product's map with every map entry used. Disjointness is per base rather than per context because worklist pins are per left state, so two bases in one context can legitimately admit nested subsets of one fiber. Cover against the static option list is deliberately not asserted: pins legitimately exclude unreachable members, exactly as label grain excludes their rows.
struct DeepPartitionCheck<'a, 'i> {
    engine: &'a mut Engine<'i>,
    options: &'a mut WindowOptions<'i>,
    deriver: &'a mut DeepFiberDeriver,
    liveness: Option<&'a mut ProspectLiveness<'i>>,
    third_slot_matters: &'a mut ThirdSlotFilter<'i>,
    fourth_slot_matters: &'a mut FourthSlotFilter<'i>,
    deep_inputs: &'a HashSet<Sym>,
    deep4_inputs: &'a HashSet<Sym>,
    contexts: HashMap<(Sym, Sym, Sym), ContextPartition>,
    r4_lists: HashMap<(Sym, Sym, Sym, Sym), Vec<String>>,
}

impl DeepPartitionCheck<'_, '_> {
    /// The assertion over one product, or the `PartitionError` sentence of the first clause it broke.
    fn run(&mut self, product: &FixpointProduct) -> Result<(), String> {
        let index = self.engine.index();
        let classes: HashMap<&str, &[String]> = product
            .deep_classes
            .iter()
            .map(|(token, members)| (token.as_str(), members.as_slice()))
            .collect();
        let mut used: HashSet<&str> = HashSet::new();
        let mut seen3: HashMap<[&str; 4], HashMap<&str, &str>> = HashMap::new();
        let mut seen4: HashMap<([&str; 4], &str), HashMap<&str, &str>> = HashMap::new();
        for row in &product.transitions {
            let key = row.key();
            let family = rune_of(index, row.input_glyph.split('.').next().unwrap_or_default());
            let right1 = rune_of(index, &row.right1);
            let right2 = rune_of(index, &row.right2);
            let letters_window = !boundaryish(&row.right1) && !boundaryish(&row.right2);
            let mut live = false;
            if letters_window
                && let (Some(family), Some(right1), Some(right2)) = (family, right1, right2)
                && self.deep_inputs.contains(&family)
            {
                live = self
                    .third_slot_matters
                    .matters(
                        self.engine,
                        self.liveness.as_deref_mut(),
                        family,
                        right1,
                        right2,
                    )
                    .map_err(complaint)?;
            }
            if !live {
                if &*row.right3 != NA_LABEL {
                    return Err(format!(
                        "{key:?}: right3 enumerated where the filters say dead"
                    ));
                }
                continue;
            }
            if &*row.right3 == NA_LABEL {
                return Err(format!("{key:?}: right3 #NA where the filters say live"));
            }
            if row.right3.starts_with(DEEP_CLASS_PREFIX) {
                if !classes.contains_key(&*row.right3) {
                    return Err(format!(
                        "{key:?}: right3 token {} is not in the class map",
                        row.right3
                    ));
                }
                used.insert(&*row.right3);
            }
            if boundaryish(&row.right3) {
                if &*row.right4 != NA_LABEL {
                    return Err(format!(
                        "{key:?}: right4 enumerated past a boundary third slot"
                    ));
                }
                continue;
            }
            let family = family.expect("a live row's input glyph names a modeled rune");
            let right1 = right1.expect("a live row's right1 is a letter");
            let right2 = right2.expect("a live row's right2 is a letter");
            self.ensure_context(family, right1, right2)?;
            let members3 = token_members(&classes, &row.right3);
            let base = [&*row.input_glyph, &*row.left, &*row.right1, &*row.right2];
            let taken3 = seen3.entry(base).or_default();
            for member in &members3 {
                if let Some(claimed) = taken3.get(member)
                    && *claimed != &*row.right3
                {
                    return Err(format!(
                        "{key:?}: r3 member {member} belongs to two tokens at one base: {claimed} and {}",
                        row.right3
                    ));
                }
                taken3.insert(member, &*row.right3);
            }
            {
                let partition = &self.contexts[&(family, right1, right2)];
                let mut outside: Vec<&str> = members3
                    .iter()
                    .copied()
                    .filter(|member| {
                        rune_of(index, member)
                            .is_none_or(|name| !partition.static_letters.contains(&name))
                    })
                    .collect();
                if !outside.is_empty() {
                    outside.sort_unstable();
                    return Err(format!(
                        "{key:?}: r3 members outside the static option list: {outside:?}"
                    ));
                }
                let touched: HashSet<usize> = members3
                    .iter()
                    .filter_map(|member| rune_of(index, member))
                    .filter_map(|name| partition.fiber_of.get(&name).copied())
                    .collect();
                if touched.len() > 1 {
                    let mut names = members3.clone();
                    names.sort_unstable();
                    return Err(format!(
                        "{key:?}: r3 members straddle two fibers: {names:?}"
                    ));
                }
            }
            let mut verdicts: HashSet<bool> = HashSet::new();
            for member in &members3 {
                let third = rune_of(index, member).expect("the member is inside the option list");
                verdicts.insert(
                    self.fourth_slot_matters
                        .matters(
                            self.engine,
                            self.liveness.as_deref_mut(),
                            family,
                            right1,
                            right2,
                            third,
                        )
                        .map_err(complaint)?,
                );
            }
            if verdicts.len() > 1 {
                let mut names = members3.clone();
                names.sort_unstable();
                return Err(format!(
                    "{key:?}: members disagree on the fourth_slot_matters verdict: {names:?}"
                ));
            }
            // The census gate is ANDed in here rather than inside the filter, which is the same split the enumeration makes when it decides whether a fiber's r4 groups become slot-4 entries.
            let fourth =
                verdicts.into_iter().next().unwrap_or(false) && self.deep4_inputs.contains(&family);
            if &*row.right4 == NA_LABEL {
                if fourth {
                    return Err(format!("{key:?}: right4 #NA where the filters say live"));
                }
                continue;
            }
            if !fourth {
                return Err(format!(
                    "{key:?}: right4 enumerated where the filters say dead"
                ));
            }
            let mut shared: Option<Vec<String>> = None;
            for member in &members3 {
                let third = rune_of(index, member).expect("the member is inside the option list");
                self.ensure_r4_list(family, right1, right2, third)?;
                let option_list = self.r4_lists[&(family, right1, right2, third)].as_slice();
                match &shared {
                    None => shared = Some(option_list.to_vec()),
                    Some(first) if first.as_slice() != option_list => {
                        return Err(format!(
                            "{key:?}: members induce different computed r4 option lists: {} vs {member}",
                            members3[0]
                        ));
                    }
                    Some(_) => {}
                }
            }
            if row.right4.starts_with(DEEP_CLASS_PREFIX) {
                if !classes.contains_key(&*row.right4) {
                    return Err(format!(
                        "{key:?}: right4 token {} is not in the class map",
                        row.right4
                    ));
                }
                used.insert(&*row.right4);
            }
            if boundaryish(&row.right4) {
                continue;
            }
            let members4 = token_members(&classes, &row.right4);
            let taken4 = seen4.entry((base, &*row.right3)).or_default();
            for member in &members4 {
                if let Some(claimed) = taken4.get(member)
                    && *claimed != &*row.right4
                {
                    return Err(format!(
                        "{key:?}: r4 member {member} belongs to two tokens at one base: {claimed} and {}",
                        row.right4
                    ));
                }
                taken4.insert(member, &*row.right4);
            }
            if let Some(shared) = shared {
                let missing: Vec<&str> = members4
                    .iter()
                    .copied()
                    .filter(|member| !shared.iter().any(|option| option == member))
                    .collect();
                if !missing.is_empty() {
                    return Err(format!(
                        "{key:?}: r4 members outside the computed option list: {missing:?}"
                    ));
                }
            }
        }
        let mut unused: Vec<&str> = classes
            .keys()
            .copied()
            .filter(|token| !used.contains(token))
            .collect();
        if !unused.is_empty() {
            unused.sort_unstable();
            return Err(format!("unused deep-class map entries: {unused:?}"));
        }
        Ok(())
    }

    /// This context's partition in the cache, derived through the fiber deriver on a miss. The cache is lazy on purpose: a row whose third slot is a boundary never reaches it.
    fn ensure_context(&mut self, family: Sym, right1: Sym, right2: Sym) -> Result<(), String> {
        if self.contexts.contains_key(&(family, right1, right2)) {
            return Ok(());
        }
        let liveness = self.liveness.as_deref_mut().ok_or_else(|| {
            "the class-grain partition assertion needs the liveness probe its fibers were derived through".to_owned()
        })?;
        let fibers = self
            .deriver
            .context(
                self.engine,
                liveness,
                self.fourth_slot_matters,
                self.options,
                family,
                right1,
                right2,
            )
            .map_err(complaint)?;
        let mut static_letters: HashSet<Sym> = HashSet::new();
        let mut fiber_of: HashMap<Sym, usize> = HashMap::new();
        for (seat, fiber) in fibers.fibers.iter().enumerate() {
            for member in &fiber.members {
                static_letters.insert(member.letter());
                fiber_of.insert(member.letter(), seat);
            }
        }
        self.contexts.insert(
            (family, right1, right2),
            ContextPartition {
                static_letters,
                fiber_of,
            },
        );
        Ok(())
    }

    /// The computed r4 option list for one `(context, r3 member)`, cached because a class row asks for one per member and the members of two rows overlap.
    fn ensure_r4_list(
        &mut self,
        family: Sym,
        right1: Sym,
        right2: Sym,
        third: Sym,
    ) -> Result<(), String> {
        if self.r4_lists.contains_key(&(family, right1, right2, third)) {
            return Ok(());
        }
        let index = self.engine.index();
        let options = self
            .options
            .right4_options(
                RightToken::Letter(right1),
                RightToken::Letter(right2),
                RightToken::Letter(third),
            )
            .map_err(complaint)?;
        let labels: Vec<String> = options
            .into_iter()
            .map(|option| right_token_label(index, option))
            .collect();
        self.r4_lists
            .insert((family, right1, right2, third), labels);
        Ok(())
    }

    /// One context's partition stated rather than derived — the assertion tests' way of handing in exactly what a real build's enumeration would already have put in the cache, since the deriver itself is the escalated module's.
    #[cfg(test)]
    fn seed_context(&mut self, index: &SpecIndex, context: [&str; 3], fibers: &[&[&str]]) {
        let named = |name: &str| {
            index
                .sym_of(name)
                .unwrap_or_else(|| panic!("the fixture mentions {name}"))
        };
        let mut static_letters: HashSet<Sym> = HashSet::new();
        let mut fiber_of: HashMap<Sym, usize> = HashMap::new();
        for (seat, fiber) in fibers.iter().enumerate() {
            for member in *fiber {
                static_letters.insert(named(member));
                fiber_of.insert(named(member), seat);
            }
        }
        self.contexts.insert(
            (named(context[0]), named(context[1]), named(context[2])),
            ContextPartition {
                static_letters,
                fiber_of,
            },
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;
    use crate::stream::emit_transitions;

    /// The two heights the ordinary fixtures join at.
    const HEIGHTS: &[(&str, &str)] = &[("baseline", "0"), ("x-height", "5")];

    /// The row's whole point: sixteen bytes, four of them the absent-or-present left seat, with nothing padded and nothing on the heap.
    #[test]
    fn a_row_is_its_fields_and_no_padding() {
        assert_eq!(std::mem::size_of::<Option<SettledSeat>>(), 4);
        assert_eq!(std::mem::size_of::<Row>(), 16);
    }

    /// The registry the fixpoint fixtures share — `fixtures::four_family_registry` with the height table left open, so the partition check can declare the aliasing pair it needs beside the ordinary heights.
    fn registry(heights: &[(&str, &str)]) -> String {
        fixtures::registry(&[
            ("heights", &fixtures::map(heights)),
            (
                "boundary_tokens",
                &fixtures::map(&[
                    ("space", r#"{"codepoint":32,"splits_runs":true}"#),
                    ("zwnj", r#"{"codepoint":8204,"splits_runs":false}"#),
                ]),
            ),
            (
                "families",
                &fixtures::map(&[
                    ("qsPea", r#"{"codepoint":58960,"sequence":null}"#),
                    ("qsTea", r#"{"codepoint":58962,"sequence":null}"#),
                    ("qsMay", r#"{"codepoint":58981,"sequence":null}"#),
                ]),
            ),
        ])
    }

    /// One stance declaring an entry row and an exit row per named height, which is every surface field these fixtures read.
    fn stance(name: &str, entries: &[&str], exits: &[&str]) -> String {
        fixtures::stance(
            name,
            &[(
                "surface",
                &fixtures::surface(&[("entries", &rows(entries)), ("exits", &rows(exits))]),
            )],
        )
    }

    /// A surface side as the mapping from height name to its row.
    fn rows(heights: &[&str]) -> String {
        let built: Vec<(String, String)> = heights
            .iter()
            .map(|height| ((*height).to_owned(), fixtures::row(height, &[])))
            .collect();
        let entries: Vec<(&str, &str)> = built
            .iter()
            .map(|(height, row)| (height.as_str(), row.as_str()))
            .collect();
        fixtures::map(&entries)
    }

    fn rune(name: &str, stances: &[(&str, String)], extra: &[(&str, &str)]) -> String {
        let entries: Vec<(&str, &str)> = stances
            .iter()
            .map(|(name, body)| (*name, body.as_str()))
            .collect();
        let stances = fixtures::map(&entries);
        let mut fields = vec![("stances", stances.as_str())];
        fields.extend_from_slice(extra);
        fixtures::rune(name, &fields)
    }

    fn spec_of(runes: &[(&str, String)], registry: &str) -> SpecIndex {
        let entries: Vec<(&str, &str)> = runes
            .iter()
            .map(|(name, body)| (*name, body.as_str()))
            .collect();
        fixtures::index_of(&fixtures::dump(&fixtures::map(&entries), registry))
    }

    /// A right condition testing each family in turn, one `then:` hop per name — the shape the deep censuses count hops on.
    fn chain(families: &[&str]) -> String {
        let (head, rest) = families
            .split_first()
            .expect("a chain names at least one slot");
        let family = fixtures::names(&[*head]);
        if rest.is_empty() {
            return fixtures::condition(&[("family", &family)]);
        }
        fixtures::condition(&[("family", &family), ("then", &chain(rest))])
    }

    /// A `prefer` favoring one stance, gated on a right condition and nothing else.
    fn prefer_stance(stance: &str, right: &str) -> String {
        fixtures::record(&[
            ("kind", "\"prefer\""),
            ("stance", &fixtures::quote(stance)),
            ("when", &fixtures::when(&[("right", right)])),
        ])
    }

    /// A `prefer` favoring whichever candidate commits one named exit, gated the same way.
    fn prefer_exit(height: &str, right: &str) -> String {
        let wanted = fixtures::quote(height);
        fixtures::record(&[
            ("kind", "\"prefer\""),
            ("cell", &fixtures::map(&[("exit", wanted.as_str())])),
            ("when", &fixtures::when(&[("right", right)])),
        ])
    }

    fn policy(records: &[&str]) -> String {
        fixtures::policy(&[("prefer", &fixtures::seq(records))])
    }

    /// The pinned candidacy world, which is the world every fixture below is read in: both issue-28 flags off, so there is no deep world, the censuses are the chain censuses, and class grain cannot arise whatever the deep-classes flag says.
    const PINNED: EnumerationModes = EnumerationModes {
        simulated_prospect: false,
        vote_slots: false,
        deep_classes: true,
    };

    fn product(index: &SpecIndex) -> FixpointProduct {
        enumerate_transitions(index, &[], PINNED).expect("the fixture's fixpoint closes")
    }

    /// The rows an input reaches at one left, as the four right slots alone.
    fn slots_at(product: &FixpointProduct, input: &str, left: &str) -> Vec<[String; 4]> {
        product
            .transitions
            .iter()
            .filter(|row| &*row.input_glyph == input && &*row.left == left)
            .map(|row| {
                [
                    row.right1.to_string(),
                    row.right2.to_string(),
                    row.right3.to_string(),
                    row.right4.to_string(),
                ]
            })
            .collect()
    }

    /// The distinct `(input, left)` pairs a product records, in its own row order.
    fn heads(product: &FixpointProduct) -> Vec<(String, String)> {
        let mut pairs: Vec<(String, String)> = Vec::new();
        for row in &product.transitions {
            let pair = (row.input_glyph.to_string(), row.left.to_string());
            if !pairs.contains(&pair) {
                pairs.push(pair);
            }
        }
        pairs
    }

    /// The single-letter alphabet the closure arithmetic is read against: one stance that neither accepts an entry nor offers an exit, so every window settles into the one cell and the fixpoint reaches exactly one letter left.
    fn lone_letter() -> SpecIndex {
        spec_of(
            &[(
                "qsPea",
                rune("qsPea", &[("half", stance("half", &[], &[]))], &[]),
            )],
            &registry(HEIGHTS),
        )
    }

    /// The three-letter alphabet the deep slots and the pins are read against.
    ///
    /// `qsPea` carries the only deep chain — a `prefer` whose right condition reaches three slots on, so `qsTea qsMay qsPea qsTea` is the one continuation that decides its window — and the two stances it chooses between exit at different heights, which is what makes the choice visible one letter later: `qsTea` accepts an entry at either height, so the left the deep window commits is a different cell for each. `qsMay` accepts a baseline entry and offers no exit, so it ends every chain.
    fn deep_alphabet() -> SpecIndex {
        let pea = rune(
            "qsPea",
            &[
                ("half", stance("half", &[], &["baseline"])),
                ("full", stance("full", &[], &["x-height"])),
            ],
            &[(
                "policy",
                &policy(&[&prefer_stance(
                    "full",
                    &chain(&["qsTea", "qsMay", "qsPea", "qsTea"]),
                )]),
            )],
        );
        let tea = rune(
            "qsTea",
            &[(
                "plain",
                stance("plain", &["baseline", "x-height"], &["baseline"]),
            )],
            &[],
        );
        let may = rune(
            "qsMay",
            &[("plain", stance("plain", &["baseline"], &[]))],
            &[],
        );
        spec_of(
            &[("qsPea", pea), ("qsTea", tea), ("qsMay", may)],
            &registry(HEIGHTS),
        )
    }

    #[test]
    fn a_one_letter_alphabet_closes_over_the_lefts_it_reaches() {
        let index = lone_letter();
        let product = product(&index);
        assert_eq!(
            heads(&product),
            [
                ("qsPea".to_owned(), "#EDGE".to_owned()),
                ("qsPea".to_owned(), "periodcentered".to_owned()),
                ("qsPea".to_owned(), "qsPea.half".to_owned()),
                ("qsPea".to_owned(), "space".to_owned()),
                ("qsPea".to_owned(), "uni200C".to_owned()),
            ],
            "the four boundary lefts the seeds start from, and the one settled letter left they reach"
        );
        // Nine windows at every left: the four boundary right1s, whose second slot is #NA because nothing follows a boundary inside one window, and the letter right1 with its five right2 options.
        assert_eq!(
            slots_at(&product, "qsPea", "#EDGE")
                .iter()
                .map(|slots| slots.join(" "))
                .collect::<Vec<String>>(),
            [
                "#EDGE #NA #NA #NA",
                "periodcentered #NA #NA #NA",
                "qsPea #EDGE #NA #NA",
                "qsPea periodcentered #NA #NA",
                "qsPea qsPea #NA #NA",
                "qsPea space #NA #NA",
                "qsPea uni200C #NA #NA",
                "space #NA #NA #NA",
                "uni200C #NA #NA #NA",
            ]
        );
        assert_eq!(
            product.transitions.len(),
            45,
            "nine windows at each of five lefts"
        );
        // The input never joins, so every window settles into the one cell and the head seats exactly it.
        assert_eq!(product.cells.len(), 1);
        assert!(
            product
                .transitions
                .iter()
                .all(|row| &*row.outcome == "qsPea.half")
        );
        assert_eq!(product.config, "default");
    }

    #[test]
    fn a_zwnj_left_locks_an_entry_bearing_input_and_leaves_the_rest_bare() {
        let index = spec_of(
            &[
                (
                    "qsPea",
                    rune(
                        "qsPea",
                        &[("half", stance("half", &["baseline"], &[]))],
                        &[],
                    ),
                ),
                (
                    "qsMay",
                    rune("qsMay", &[("plain", stance("plain", &[], &[]))], &[]),
                ),
            ],
            &registry(HEIGHTS),
        );
        assert!(index.is_entry_bearing(fixtures::sym(&index, "qsPea")));
        assert!(!index.is_entry_bearing(fixtures::sym(&index, "qsMay")));
        let product = product(&index);
        let locked: Vec<&str> = product
            .transitions
            .iter()
            .filter(|row| &*row.left == "uni200C")
            .map(|row| &*row.input_glyph)
            .collect();
        assert!(
            locked.contains(&"qsPea.noentry") && locked.contains(&"qsMay"),
            "the chokepoint twin is the entry-bearing input's label alone: {locked:?}"
        );
        assert!(!locked.contains(&"qsPea"));
        // The lock is the row's label and nothing else — the trace settles the raw letter, whose cell is the one the outcome names.
        assert!(
            product
                .transitions
                .iter()
                .filter(|row| &*row.input_glyph == "qsPea.noentry")
                .all(|row| row.outcome.starts_with("qsPea.half")),
            "the locked twin still settles as qsPea"
        );
        // Nothing else in the product carries the suffix: a ZWNJ at any other slot is an ordinary boundary.
        assert!(
            product
                .transitions
                .iter()
                .all(|row| &*row.left == "uni200C" || !row.input_glyph.ends_with(".noentry"))
        );
    }

    #[test]
    fn the_deep_slots_split_only_the_windows_the_census_and_both_filters_admit() {
        let index = deep_alphabet();
        let product = product(&index);
        let deep: Vec<&str> = product
            .transitions
            .iter()
            .filter(|row| &*row.right3 != NA_LABEL)
            .map(|row| &*row.input_glyph)
            .collect();
        assert!(
            !deep.is_empty() && deep.iter().all(|input| *input == "qsPea"),
            "the censused input carries a third slot and nothing else does"
        );
        assert!(
            product
                .transitions
                .iter()
                .filter(|row| &*row.right3 != NA_LABEL)
                .all(|row| &*row.right1 == "qsTea" && &*row.right2 == "qsMay"),
            "and only where its chain is still unanswered two slots in"
        );
        let split: Vec<String> = slots_at(&product, "qsPea", "#EDGE")
            .iter()
            .filter(|slots| slots[0] == "qsTea" && slots[1] == "qsMay")
            .map(|slots| format!("{} {}", slots[2], slots[3]))
            .collect();
        assert_eq!(
            split,
            [
                // The third slot's whole option list, and the fourth opening only under the one third token the chain's last hop reads.
                "#EDGE #NA",
                "periodcentered #NA",
                "qsMay #NA",
                "qsPea #EDGE",
                "qsPea periodcentered",
                "qsPea qsMay",
                "qsPea qsPea",
                "qsPea qsTea",
                "qsPea space",
                "qsPea uni200C",
                "qsTea #NA",
                "space #NA",
                "uni200C #NA",
            ]
        );
        // The chain's one full match is what the prefer answers, so the fourth slot is not decorative: it moves the cell the window settles into.
        let outcomes: Vec<(&str, &str)> = product
            .transitions
            .iter()
            .filter(|row| {
                &*row.input_glyph == "qsPea" && &*row.left == "#EDGE" && &*row.right3 == "qsPea"
            })
            .map(|row| (&*row.right4, &*row.outcome))
            .collect();
        assert_eq!(
            outcomes,
            [
                ("#EDGE", "qsPea.half.ex-y0"),
                ("periodcentered", "qsPea.half.ex-y0"),
                ("qsMay", "qsPea.half.ex-y0"),
                ("qsPea", "qsPea.half.ex-y0"),
                ("qsTea", "qsPea.full.ex-y5"),
                ("space", "qsPea.half.ex-y0"),
                ("uni200C", "qsPea.half.ex-y0"),
            ]
        );
    }

    #[test]
    fn a_depth_four_left_pins_the_second_slot_of_the_window_after_its_successor() {
        let index = deep_alphabet();
        let product = product(&index);
        // The left only the fourth slot's one live token reaches: qsPea commits the x-height seam there and nowhere else, so qsTea's entry at that height is the fingerprint of that one continuation.
        assert_eq!(
            slots_at(&product, "qsTea", "qsPea.full.ex-y5")
                .iter()
                .map(|slots| slots.join(" "))
                .collect::<Vec<String>>(),
            ["qsMay qsPea #NA #NA"],
            "the successor's own second slot is pinned to the third lookahead that was enumerated behind it"
        );
        // And the pin the window could not enumerate — qsTea is not deep, so it has no third slot to spend it on — is forwarded onto its own successor's second slot, which is the raw token one past that window.
        assert_eq!(
            slots_at(&product, "qsMay", "qsTea.plain.en-y5.ex-y0")
                .iter()
                .map(|slots| slots.join(" "))
                .collect::<Vec<String>>(),
            ["qsPea qsTea #NA #NA"],
            "without the forward this left would carry every second-slot option, and the extra windows are ones no text can reach"
        );
        // The sibling left is the contrast: reached by plenty of unpinned items, it carries the whole option list behind the same right1.
        let unpinned: Vec<String> = slots_at(&product, "qsMay", "qsTea.plain.en-y0.ex-y0")
            .iter()
            .filter(|slots| slots[0] == "qsPea")
            .map(|slots| slots[1].clone())
            .collect();
        assert_eq!(
            unpinned,
            [
                "#EDGE",
                "periodcentered",
                "qsMay",
                "qsPea",
                "qsTea",
                "space",
                "uni200C"
            ]
        );
    }

    /// A right condition testing a list of families per hop, one `then:` hop per entry — [`chain`] with the alternatives at each slot spelled out, which is how a fixture makes one deep slot live under two different tokens.
    fn chain_families(hops: &[&[&str]]) -> String {
        let (head, rest) = hops.split_first().expect("a chain names at least one slot");
        let family = fixtures::names(head);
        if rest.is_empty() {
            return fixtures::condition(&[("family", &family)]);
        }
        fixtures::condition(&[("family", &family), ("then", &chain_families(rest))])
    }

    /// The registry the ligature fixture reads: the ordinary heights, plus the formed ligature among the families so the guard has a token to ask about.
    fn liga_registry() -> String {
        fixtures::registry(&[
            ("heights", &fixtures::map(HEIGHTS)),
            (
                "boundary_tokens",
                &fixtures::map(&[
                    ("space", r#"{"codepoint":32,"splits_runs":true}"#),
                    ("zwnj", r#"{"codepoint":8204,"splits_runs":false}"#),
                ]),
            ),
            (
                "families",
                &fixtures::map(&[
                    ("qsPea", r#"{"codepoint":58960,"sequence":null}"#),
                    ("qsTea", r#"{"codepoint":58962,"sequence":null}"#),
                    ("qsMay", r#"{"codepoint":58981,"sequence":null}"#),
                    (
                        "qsPeaMay",
                        r#"{"codepoint":63000,"sequence":["qsPea","qsMay"]}"#,
                    ),
                ]),
            ),
        ])
    }

    /// `deep_alphabet` with two changes the r4 option lists need: `qsPea`'s chain reads its third hop as either `qsPea` or `qsTea`, so the fourth slot is live under both, and a `qsPeaMay` ligature makes `(qsPea, qsMay)` a formation pair. That pair is what makes `right4_options` differ by third token — the option `qsMay` survives behind `qsTea` and cannot survive behind `qsPea`.
    fn liga_alphabet() -> SpecIndex {
        let pea = rune(
            "qsPea",
            &[
                ("half", stance("half", &[], &["baseline"])),
                ("full", stance("full", &[], &["x-height"])),
            ],
            &[(
                "policy",
                &policy(&[&prefer_stance(
                    "full",
                    &chain_families(&[&["qsTea"], &["qsMay"], &["qsPea", "qsTea"], &["qsTea"]]),
                )]),
            )],
        );
        let tea = rune(
            "qsTea",
            &[(
                "plain",
                stance("plain", &["baseline", "x-height"], &["baseline"]),
            )],
            &[],
        );
        let may = rune(
            "qsMay",
            &[("plain", stance("plain", &["baseline"], &[]))],
            &[],
        );
        let liga = rune(
            "qsPeaMay",
            &[("plain", stance("plain", &["baseline"], &["baseline"]))],
            &[("sequence", &fixtures::names(&["qsPea", "qsMay"]))],
        );
        spec_of(
            &[
                ("qsPea", pea),
                ("qsTea", tea),
                ("qsMay", may),
                ("qsPeaMay", liga),
            ],
            &liga_registry(),
        )
    }

    /// One hand-built row. The partition assertion reads the six window labels and nothing else, so every row here names the same settled seat, into a table the product below does not bother to carry.
    fn deep_row(labels: [&str; 6]) -> TransitionRow {
        let [input_glyph, left, right1, right2, right3, right4] = labels.map(Rc::from);
        TransitionRow {
            input_glyph,
            left,
            right1,
            right2,
            right3,
            right4,
            outcome: Rc::from("qsMay.plain"),
            settled: SettledSeat::at(0),
            left_settled: None,
            provenance: NotesSeat::at(0),
            prospect: 0,
            joint: false,
        }
    }

    /// A product assembled out of hand-built rows and a stated class map — everything the assertion reads, and nothing it does not.
    fn hand_product(rows: Vec<TransitionRow>, classes: &[(&str, &[&str])]) -> FixpointProduct {
        FixpointProduct {
            config: "default".to_owned(),
            transitions: rows,
            deep_classes: classes
                .iter()
                .map(|(token, members)| {
                    (
                        (*token).to_owned(),
                        members.iter().map(|member| (*member).to_owned()).collect(),
                    )
                })
                .collect(),
            cited_provenance: Vec::new(),
            cells: Vec::new(),
            seats: Vec::new(),
            notes: Vec::new(),
        }
    }

    /// The class token a member list is spelled by, so a test states the same id the emission would.
    fn class_of(members: &[&str]) -> String {
        let owned: Vec<String> = members.iter().map(|member| (*member).to_owned()).collect();
        deep_class_id(&owned)
    }

    /// One member list as the emission hands it over, owned.
    fn owned(members: &[&str]) -> Vec<String> {
        members.iter().map(|member| (*member).to_owned()).collect()
    }

    /// A class of one is spelled by its own letter and records nothing; a class of two takes a content-addressed id, recorded the first time it is spelled and shared by every later row that reaches the same members.
    #[test]
    fn a_deep_label_is_the_bare_letter_alone_and_a_class_id_otherwise() {
        let mut classes: Vec<(String, Vec<String>)> = Vec::new();
        let mut named: HashSet<String> = HashSet::new();
        assert_eq!(
            deep_label(&mut classes, &mut named, owned(&["qsPea"])),
            "qsPea"
        );
        assert!(
            classes.is_empty(),
            "an id for a class of one would cost a map entry and buy nothing the bare label does not already say"
        );

        let token = deep_label(&mut classes, &mut named, owned(&["qsPea", "qsTea"]));
        assert_eq!(token, class_of(&["qsPea", "qsTea"]));
        assert_eq!(classes, [(token.clone(), owned(&["qsPea", "qsTea"]))]);
        assert_eq!(
            deep_label(&mut classes, &mut named, owned(&["qsPea", "qsTea"])),
            token
        );
        assert_eq!(
            classes.len(),
            1,
            "the same member set spells the same id, and the map records it once however many rows carry it"
        );
    }

    /// The echo re-traces the last admitted member, and the first one instead exactly where that last member is the representative the row was already built from — a class of two would otherwise echo the very window it is being checked against.
    #[test]
    fn the_echo_member_is_the_last_admitted_unless_that_is_the_representative() {
        let index = deep_alphabet();
        let [pea, tea, may] =
            ["qsPea", "qsTea", "qsMay"].map(|name| RightToken::Letter(fixtures::sym(&index, name)));
        assert_eq!(echo_member(&[pea, tea, may], pea), may);
        assert_eq!(echo_member(&[pea, tea, may], may), pea);
        assert_eq!(echo_member(&[pea, tea], tea), pea);
        assert_eq!(echo_member(&[pea, tea], pea), tea);
    }

    /// The partition assertion over a hand-built product, with each live context's fiber partition stated rather than derived.
    ///
    /// Stating it is not a shortcut around the deriver: by the time a real build runs this assertion every live context has already been derived, so the cache is warm and the deriver is never reached. A test that states the partition is handing in exactly what the enumeration would have left there — which is also what lets these assertions be read in the pinned world, where there is no liveness probe and the filters answer on their chain arm alone.
    fn checked(
        index: &SpecIndex,
        product: &FixpointProduct,
        contexts: &[([&str; 3], &[&[&str]])],
    ) -> Result<(), String> {
        let mut engine = Engine::with_modes(
            index,
            Vec::<Sym>::new(),
            EngineModes {
                simulated_prospect: false,
                vote_slots: false,
                trace_memo: true,
                ..EngineModes::default()
            },
        );
        let mut options = WindowOptions::new(index).expect("the fixture's guard closes");
        let mut deriver = DeepFiberDeriver::new();
        let mut third = ThirdSlotFilter::new(index);
        let mut fourth = FourthSlotFilter::new(index);
        let deep_inputs = third_slot_inputs(index, false);
        let deep4_inputs = fourth_slot_inputs(index, false);
        let mut check = DeepPartitionCheck {
            engine: &mut engine,
            options: &mut options,
            deriver: &mut deriver,
            liveness: None,
            third_slot_matters: &mut third,
            fourth_slot_matters: &mut fourth,
            deep_inputs: &deep_inputs,
            deep4_inputs: &deep4_inputs,
            contexts: HashMap::new(),
            r4_lists: HashMap::new(),
        };
        for (context, fibers) in contexts {
            check.seed_context(index, *context, fibers);
        }
        check.run(product)
    }

    /// The one live context every hand-built product below sits in: `qsPea`'s chain is unanswered two slots into `qsTea qsMay`, and nowhere else.
    const LIVE: [&str; 3] = ["qsPea", "qsTea", "qsMay"];

    /// The `#NA` biconditional, restated over tokens in both directions.
    #[test]
    fn the_third_slot_is_enumerated_exactly_where_the_filters_say_live() {
        let index = deep_alphabet();
        let dead = hand_product(
            vec![deep_row([
                "qsPea", "#EDGE", "qsPea", "qsMay", "qsTea", "#NA",
            ])],
            &[],
        );
        assert!(
            checked(&index, &dead, &[])
                .expect_err("the chain answered at the first hop")
                .ends_with(": right3 enumerated where the filters say dead"),
        );
        let live = hand_product(
            vec![deep_row(["qsPea", "#EDGE", "qsTea", "qsMay", "#NA", "#NA"])],
            &[],
        );
        assert!(
            checked(&index, &live, &[])
                .expect_err("the chain is still reading the third slot")
                .ends_with(": right3 #NA where the filters say live"),
        );
    }

    /// A class token is only a token because the map says what it stands for, and the assertion refuses to guess.
    #[test]
    fn a_class_token_the_map_never_names_stops_the_build() {
        let index = deep_alphabet();
        let product = hand_product(
            vec![deep_row([
                "qsPea",
                "#EDGE",
                "qsTea",
                "qsMay",
                "#Cfeedfacefeed",
                "#NA",
            ])],
            &[],
        );
        let complaint = checked(&index, &product, &[]).expect_err("the map is empty");
        assert!(
            complaint.ends_with(": right3 token #Cfeedfacefeed is not in the class map"),
            "{complaint}"
        );
    }

    /// No record ever peeks past a boundary, so nothing follows one inside a window.
    #[test]
    fn a_boundary_third_slot_carries_no_fourth() {
        let index = deep_alphabet();
        let product = hand_product(
            vec![deep_row([
                "qsPea", "#EDGE", "qsTea", "qsMay", "#EDGE", "qsPea",
            ])],
            &[],
        );
        assert!(
            checked(&index, &product, &[])
                .expect_err("the third slot is a run edge")
                .ends_with(": right4 enumerated past a boundary third slot"),
        );
    }

    /// An entry no row resolves through is a map that has stopped describing the stream it rides with.
    #[test]
    fn a_class_map_entry_no_row_uses_stops_the_build() {
        let index = deep_alphabet();
        let orphan = class_of(&["qsPea", "qsTea"]);
        let product = hand_product(
            vec![deep_row(["qsPea", "#EDGE", "qsPea", "qsMay", "#NA", "#NA"])],
            &[(&orphan, &["qsPea", "qsTea"])],
        );
        assert_eq!(
            checked(&index, &product, &[]),
            Err(format!("unused deep-class map entries: [\"{orphan}\"]"))
        );
    }

    /// A class may only hold members the static option list admits, and only members of one fiber — the two halves of "this row stands for a piece of the partition".
    #[test]
    fn a_class_must_sit_inside_one_fiber_of_the_static_option_list() {
        let index = deep_alphabet();
        let token = class_of(&["qsPea", "qsTea"]);
        let product = hand_product(
            vec![deep_row([
                "qsPea", "#EDGE", "qsTea", "qsMay", &token, "#NA",
            ])],
            &[(&token, &["qsPea", "qsTea"])],
        );
        let outside = checked(&index, &product, &[(LIVE, &[&["qsPea"]])])
            .expect_err("qsTea is not in the stated option list");
        assert!(
            outside.ends_with(": r3 members outside the static option list: [\"qsTea\"]"),
            "{outside}"
        );
        let straddle = checked(&index, &product, &[(LIVE, &[&["qsPea"], &["qsTea"]])])
            .expect_err("the two members sit in two fibers");
        assert!(
            straddle.ends_with(": r3 members straddle two fibers: [\"qsPea\", \"qsTea\"]"),
            "{straddle}"
        );
    }

    /// The fiber key carries the `fourth_slot_matters` verdict, so two members that disagree about it could never have been one fiber.
    #[test]
    fn a_class_whose_members_disagree_about_the_fourth_slot_stops_the_build() {
        let index = deep_alphabet();
        let token = class_of(&["qsPea", "qsTea"]);
        let product = hand_product(
            vec![deep_row([
                "qsPea", "#EDGE", "qsTea", "qsMay", &token, "#NA",
            ])],
            &[(&token, &["qsPea", "qsTea"])],
        );
        let complaint = checked(&index, &product, &[(LIVE, &[&["qsPea", "qsTea"]])])
            .expect_err("the chain's last hop reads qsPea alone");
        assert!(
            complaint.ends_with(
                ": members disagree on the fourth_slot_matters verdict: [\"qsPea\", \"qsTea\"]"
            ),
            "{complaint}"
        );
    }

    /// The biconditional again, one slot deeper and per r3 token.
    #[test]
    fn the_fourth_slot_is_enumerated_exactly_where_the_filters_say_live() {
        let index = deep_alphabet();
        let live = hand_product(
            vec![deep_row([
                "qsPea", "#EDGE", "qsTea", "qsMay", "qsPea", "#NA",
            ])],
            &[],
        );
        assert!(
            checked(&index, &live, &[(LIVE, &[&["qsPea"]])])
                .expect_err("the chain's last hop reads the fourth slot behind qsPea")
                .ends_with(": right4 #NA where the filters say live"),
        );
        let dead = hand_product(
            vec![deep_row([
                "qsPea", "#EDGE", "qsTea", "qsMay", "qsTea", "qsPea",
            ])],
            &[],
        );
        assert!(
            checked(&index, &dead, &[(LIVE, &[&["qsTea"]])])
                .expect_err("behind qsTea the chain has already answered")
                .ends_with(": right4 enumerated where the filters say dead"),
        );
    }

    /// The fiber key records the computed r4 option list structurally, so two members inducing different lists is a key that has stopped matching the pipeline — which is exactly what a filter added to `right4_options` without a key update would look like.
    #[test]
    fn a_class_whose_members_induce_different_r4_option_lists_stops_the_build() {
        let index = liga_alphabet();
        let token = class_of(&["qsPea", "qsTea"]);
        let product = hand_product(
            vec![deep_row([
                "qsPea", "#EDGE", "qsTea", "qsMay", &token, "qsTea",
            ])],
            &[(&token, &["qsPea", "qsTea"])],
        );
        let complaint = checked(&index, &product, &[(LIVE, &[&["qsPea", "qsTea"]])]).expect_err(
            "the qsPeaMay formation pair narrows one member's list and not the other's",
        );
        assert!(
            complaint
                .ends_with(": members induce different computed r4 option lists: qsPea vs qsTea"),
            "{complaint}"
        );
    }

    /// The seeds in the exact reverse of the contract order — the deepest permutation available, since it pops last what the shipping order pops first.
    fn reversed_seeds(options: &WindowOptions<'_>) -> Vec<Item> {
        let mut seeds = contract_seeds(options);
        seeds.reverse();
        seeds
    }

    #[test]
    fn a_permuted_seed_order_reaches_the_same_pinned_world_product() {
        let index = deep_alphabet();
        let contract = enumerate_seeded(&index, &[], PINNED, contract_seeds, None)
            .expect("the fixpoint closes");
        let reversed = enumerate_seeded(&index, &[], PINNED, reversed_seeds, None)
            .expect("the fixpoint closes");
        // Compared as the stream rather than as the product, because two of the product's fields are sets whose vector spelling is the emitter's business: `cited_provenance` comes out of a hash set and has no order of its own.
        assert_eq!(
            emit_transitions(&index, &contract),
            emit_transitions(&index, &reversed)
        );
        assert!(
            contract
                .transitions
                .iter()
                .any(|row| &*row.right4 != NA_LABEL),
            "and the product both orders reached is the one carrying the pinned deep windows, not a trivially equal pair"
        );
        // Order-independence here is a fact about this world, not about the discipline: the dedup is by window key, a re-reached window reuses the settled a re-trace would return, and the fired set is a union over a window set no traversal can change. Under class grain the first visitor of a fiber fixes its representative, and the push order becomes output-visible.
    }

    #[test]
    fn one_window_label_reached_from_two_left_states_stops_the_build() {
        // Two heights at one y is what makes `cell_label` non-injective, and the prefer picks between them under the one continuation its chain reads — so the deep window commits a cell that labels exactly like its siblings' and compares unequal to them.
        let pea = rune(
            "qsPea",
            &[("half", stance("half", &[], &["baseline", "floor"]))],
            &[(
                "policy",
                &policy(&[&prefer_exit(
                    "floor",
                    &chain(&["qsTea", "qsMay", "qsPea", "qsTea"]),
                )]),
            )],
        );
        let tea = rune(
            "qsTea",
            &[(
                "plain",
                stance("plain", &["baseline", "floor"], &["baseline"]),
            )],
            &[],
        );
        let may = rune(
            "qsMay",
            &[("plain", stance("plain", &["baseline"], &[]))],
            &[],
        );
        let index = spec_of(
            &[("qsPea", pea), ("qsTea", tea), ("qsMay", may)],
            &registry(&[("baseline", "0"), ("floor", "0"), ("x-height", "5")]),
        );
        let complaint = enumerate_transitions(&index, &[], PINNED).expect_err("the labels collide");
        assert!(
            complaint.starts_with(
                "window [\"qsTea\", \"qsPea.half.ex-y0\", \"qsMay\", \"qsPea\", \"#NA\", \"#NA\"] reached from two left states sharing one label: "
            ),
            "{complaint}"
        );
        assert!(
            complaint.contains("qsPea.half.ex-y0 (seam floor, extension 0)")
                && complaint.contains("qsPea.half.ex-y0 (seam baseline, extension 0)"),
            "{complaint}"
        );
    }
}
