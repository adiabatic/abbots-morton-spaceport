//! The table build's worklist fixpoint: it settles every window one configuration's alphabet can reach, once each, and returns the rows [`crate::fold`] folds into the two tables a build persists.
//!
//! A worklist item is a left state together with the pins it was reached under. A settled left is reachable only alongside the right1 that was the producing window's right2, because an entry refusal or an unlock conditioned on the follower makes any other combination contradictory: the left would never have committed there. The right2 allowed-set carries the producing window's enumerated right3 when it has one, and otherwise the late-formation guard's allowed second slots for a surviving formation pair, intersected with any right3 pin the producing window could not use. The right3 allowed-set carries a producing window's enumerated right4 the same way, which pins the successor windows of a left decided at depth 4 to the third lookahead that was actually behind them. `None` means unrestricted in both, and both sets compare by content.
//!
//! The product depends only on the set of rows, not on the traversal order, at either grain. At label grain the dedup is by window key, and a hit reuses the recorded settled state because the left label is injective into the trace's inputs, so the fired set is a union over a window set that no order changes. At class grain a fiber's row is traced at the fiber's representative, its least member under the label order (the first entry of the fiber's sorted member list), whichever item reaches the fiber first and whatever subset of it that item's pins admit. The admitted members accumulate as a union across items, which is also order-independent. The worklist is LIFO with the `seen` check at pop time, so the traversal is a fixed function of the seeds, and the two permuted-seed tests below check order-independence at each grain.
//!
//! In the deep world with the deep-classes flag on, the deep slots enumerate at class grain: the same static option lists, their letters split into the outcome fibers of [`crate::fiber::DeepFiberDeriver`], one in-flight row per base and fiber identity accumulating the admitted members across items, successor pins carrying those member sets instead of singletons, and a content-addressed id per multi-member set in the product's `deep_classes` map. Two checks run with it. The echo check re-traces a second member of every multi-member row at the row's real left and requires the same row-visible record. `DeepPartitionCheck` runs over the finished product before it is returned. With the flag off, or in the pinned world where class grain cannot arise, only the label-grain path runs, and the deep slots still enumerate: the censuses and the filters decide which deep slots are live.
//!
//! One engine settles everything, and the two slot filters, the liveness probe and the fiber deriver all borrow it. The trace memo makes a re-reached window free, and `Engine::fired` becomes the product's `cited_provenance`, so a probe running on a second engine would silently drop entries from what the dead-policy gate sees as fired. For the same reason one liveness probe is lent to both filters and to the deriver.

use std::collections::BTreeSet;
use std::rc::Rc;
use std::time::{Duration, Instant};

use crate::census::{FourthSlotFilter, ThirdSlotFilter, fourth_slot_inputs, third_slot_inputs};
use crate::engine::{CacheSize, Engine, EngineModes, Slots};
use crate::error::SettleError;
use crate::fiber::DeepFiberDeriver;
use crate::hash::{HashMap, HashSet};
use crate::index::SpecIndex;
use crate::liveness::ProspectLiveness;
use crate::memo::{MemoBase, MemoFile, MemoSnapshot, write_memo};
use crate::model::Sym;
use crate::options::{FollowerMap, WindowOptions};
use crate::sha256;
use crate::stream::{FixpointProduct, Label, LabelPool, TransitionRow, feature_config_token};
use crate::types::{
    CellId, EDGE, LeftContext, NotesPool, NotesSeat, RightToken, Settled, SettledPool, SettledSeat,
    TokenKind, TransitionTrace, cell_label,
};

/// The label of a slot the window does not carry, `table.NA_LABEL`. A boundary at right1 puts it in the second slot as well, because nothing follows a boundary inside one window.
const NA_LABEL: &str = "#NA";

/// The label of the run edge, `table.EDGE_LABEL`. The other three boundaries are labeled with the glyphs they ship as, so only this one needs its own constant.
pub const EDGE_LABEL: &str = "#EDGE";

/// The prefix of every deep-class id, `table.DEEP_CLASS_PREFIX`. The `#` keeps ids outside the glyph namespace, so a slot label's first character tells a class id from a letter.
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

/// The world one enumeration runs in, and at which grain. Python reads the three flags from module-level defaults that environment variables override (`kernel_exec.SIMULATED_PROSPECT_DEFAULT`, `kernel_exec.VOTE_SLOTS_DEFAULT` and `kernel_exec.DEEP_CLASSES_DEFAULT`). This crate reads no environment, so the caller passes them, and [`Default`] is the shipping configuration. [`EnumerationModes::world_token`] names the world; a memo file's head carries it so a memo traced in one world is never read in another.
///
/// Either engine mode on makes a deep world: both deep-slot censuses widen to every rune and the filters get their liveness probe. `deep_classes` only takes effect in a deep world. In the pinned world it is accepted and does nothing, because there is no fiber source to enumerate at class grain.
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

impl EnumerationModes {
    /// The world's name as `kernel_exec.enumeration_tokens` forms it: each enabled flag's token, `+`-joined, or `pinned` when none is on, so Python and Rust name a world with the same string.
    pub fn world_token(self) -> String {
        let mut tokens: Vec<&str> = Vec::new();
        if self.simulated_prospect {
            tokens.push("simulated-prospect");
        }
        if self.vote_slots {
            tokens.push("vote-slots");
        }
        if self.deep_classes && (self.simulated_prospect || self.vote_slots) {
            tokens.push("deep-classes");
        }
        if tokens.is_empty() {
            return "pinned".to_owned();
        }
        tokens.join("+")
    }
}

/// What one enumeration may read before settling a window itself, and whether it returns its own memo ([`crate::memo`]). The bases are consulted in the order given. `keep_memo` is set for the configuration other enumerations will read; it returns the snapshot to the caller, at the cost of holding it through the drain and the sort. Writing the memo to a file is independent of it: [`enumerate_for_tables`] writes the file at the release point whether or not the snapshot is kept.
#[derive(Debug, Default)]
pub struct Seed {
    pub bases: Vec<MemoBase>,
    pub keep_memo: bool,
}

/// The content-addressed id one deep-slot member set carries, `table.deep_class_id`: `#C` plus the first twelve hex digits of the SHA-256 of the tab-joined members.
///
/// Identical member sets share one id across contexts, configurations and builds, which is what keeps cross-config artifact comparison and the ss04 row-identity pin meaningful. The caller passes the members sorted by letter, as the emission sorts them, because the digest is over the joined text.
pub fn deep_class_id(members: &[String]) -> String {
    let digest = sha256::digest_hex(members.join("\t").as_bytes());
    format!("{DEEP_CLASS_PREFIX}{}", &digest[..12])
}

/// A worklist pin's allowed tokens: a set compared and hashed by content, so two items pinned to the same tokens are one item, behind an [`Rc`] so an item is cheap to clone into the `seen` set. The `BTreeSet` order is interning order and is never read.
type Allowed = Rc<BTreeSet<RightToken>>;

/// The six labels one window is keyed by, `table.Window.key`: the input glyph, the left, and the four right slots, each as the id the pool minted for its text.
type WindowKey = [Label; 6];

impl LabelPool {
    /// One right slot's label, interned: [`right_token_label`] without allocating the `String` that function returns.
    fn token(&mut self, index: &SpecIndex, token: RightToken) -> Label {
        match token {
            RightToken::Letter(rune, _) => {
                let name = index.resolve(rune);
                self.intern(name)
            }
            other => {
                let name = boundary_left_label(other.kind());
                self.intern(name)
            }
        }
    }

    /// A deep slot's label, interned, with an absent slot labeled [`NA_LABEL`].
    fn slot(&mut self, index: &SpecIndex, token: Option<RightToken>) -> Label {
        match token {
            Some(token) => self.token(index, token),
            None => self.intern(NA_LABEL),
        }
    }
}

/// One worklist item: the left state, the input rune, the right1 the left was reached alongside, and the two allowed-sets pinning the slots past it. Its equality is the `seen` key. A [`LeftContext`] holds a kind, a settled record and ordinals computed from that record, so its derived equality amounts to comparing the kind and the settled left.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct Item {
    left: LeftContext,
    rune: Sym,
    right1: Option<RightToken>,
    right2_allowed: Option<Allowed>,
    right3_allowed: Option<Allowed>,
}

/// A recorded window's fields other than the six labels that key it (the rest of `table.Transition`). The settled records and the provenance are indexes into the run's [`SettledPool`] and [`NotesPool`]: a configuration reaches millions of rows but only a few thousand distinct records, so holding each record by value would copy its heap allocations into hundreds of thousands of rows. The prospect fits in a byte. The row is sixteen bytes, two of them padding, with no heap allocation of its own. The outcome is not stored: it is the settled cell's label, resolved once per settled record when the product is built.
struct Row {
    settled: SettledSeat,
    left_settled: Option<SettledSeat>,
    provenance: NotesSeat,
    prospect: i8,
    joint: bool,
}

/// The prospect term as a row stores it. The engine returns an `i64` because its join-count arithmetic sums these terms, but the term itself is zero or one (whether the follower's seam is claimed) in either candidacy world, so it fits in a byte. A wider value means the engine is no longer returning a count, and the conversion panics.
fn prospect_byte(prospect: i64) -> i8 {
    i8::try_from(prospect).expect("a prospect is a seam count, zero or one")
}

/// One third-slot entry of a class-grain window: the boundary token when the entry is a boundary, the fiber's index in the context when it is a fiber, and the members this item's pins admitted.
type Slot3Entry = (Option<RightToken>, Option<usize>, Vec<RightToken>);

/// One fourth-slot entry of a class-grain window: the r4 group, or `None` where the fourth slot is dead and the row carries `#NA` there.
type Slot4Entry = Option<Vec<RightToken>>;

/// What an in-flight class-grain row is keyed by while the worklist runs: the four near labels, the third slot's identity, and the fourth's full member group.
type PendingKey = (Label, Label, Label, Label, Identity3, Slot4Entry);

/// The third slot's identity inside a [`PendingKey`]: the boundary token for a boundary entry, and the fiber's full member list for a fiber.
///
/// The members are the fiber's whole membership, not the admitted subset, so two worklist items whose pins admit different subsets of one fiber accumulate into a single row.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
enum Identity3 {
    Boundary(RightToken),
    Members(Vec<RightToken>),
}

/// One in-flight class-grain row: the representative trace's row-visible record, the r3 members accumulating across worklist items, and the frame the echo traces replay after the drain.
///
/// The representative is the fiber's least member under the label order, the first entry of [`crate::fiber::Fiber::members`], whether or not the first item to reach the row admitted it. The r4 members carry no pins and so are complete from the first item, which is why they are a plain group here while the third slot's are a set.
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
    /// Whether an echo trace's record matches the representative's in the four fields a row carries: the settled triple and the notes (each read back through its pool), the prospect, and the joint-floor flag. The ranking that reached them is not compared because the row does not store it.
    fn echoes(&self, seats: &SettledPool, notes: &NotesPool, echo: &TransitionTrace) -> bool {
        echo.settled == *seats.get(self.settled)
            && echo.prospect == i64::from(self.prospect)
            && echo.joint_floor == self.joint
            && echo.notes == notes.get(self.provenance)
    }
}

/// One configuration's whole fixpoint, the value [`crate::fold::fold_product`] folds: the rows in key order, the deep-class map their class tokens resolve through, the cells they settle into, and the provenance the engine fired while tabulating. Serialized, it is `table.FixpointProduct`, which `kernel_exec.enumerate_transitions` parses. No build stage or tool calls it; only the rebuild suite exercises it.
///
/// The engine is built here from `modes`, which also decide whether the censuses widen, whether the filters get their liveness probe, and whether the deep slots enumerate at class grain, because none of the three is meaningful without the others.
pub fn enumerate_transitions(
    index: &SpecIndex,
    features: &[Sym],
    modes: EnumerationModes,
) -> Result<FixpointProduct, String> {
    enumerate_seeded(
        index,
        features,
        modes,
        contract_seeds,
        None,
        Seed::default(),
        None,
    )
    .map(|enumeration| enumeration.product)
}

/// What [`enumerate_for_tables`] returns: the fixpoint, the [`WindowOptions`] it ran over, the engine's finished memo when the seed asked to keep it, and how long the memo file took to write when one was named. The write runs inside the enumeration, so it is timed here for the caller to report under its own label.
pub struct TablesEnumeration<'i> {
    pub product: FixpointProduct,
    pub options: WindowOptions<'i>,
    pub memo: Option<MemoSnapshot>,
    pub memo_write: Option<Duration>,
}

/// [`enumerate_transitions`] that also returns the [`WindowOptions`] it ran over, the census lines when `census` is given, and the engine's finished memo when the seed asks for it. The table build folds the product it holds, and the fold's certificates read the formation guard through the same options, whose verdict memo the worklist already filled, instead of sweeping the guard again. The seed lets one configuration's enumeration read another's settled windows ([`crate::memo`]). With a `file`, the finished memo is written there at the release point, after the engine's other memos are freed, so a configuration whose seed does not keep the memo holds none through its drain or its sort.
pub fn enumerate_for_tables<'i>(
    index: &'i SpecIndex,
    features: &[Sym],
    modes: EnumerationModes,
    census: Option<&mut Vec<String>>,
    seed: Seed,
    file: Option<MemoFile>,
) -> Result<TablesEnumeration<'i>, String> {
    enumerate_seeded(index, features, modes, contract_seeds, census, seed, file)
}

/// [`enumerate_transitions`] that also appends the `--cache-census` lines to `census`: each collection's length and capacity once the worklist finishes, the size of the elimination text the memos hold, the memo base hits in total and per base, and the process's resident size before the memo release, after it, after the memo file is written (when [`enumerate_for_tables`] names one), and after the sort. The caller writes the lines to stderr. None of this is computed unless asked for.
///
/// Memory decisions in this crate come down to entry counts, and a count read from a live alphabet settles in one run what a struct-size argument can only estimate.
pub fn enumerate_censused(
    index: &SpecIndex,
    features: &[Sym],
    modes: EnumerationModes,
    census: &mut Vec<String>,
) -> Result<FixpointProduct, String> {
    enumerate_seeded(
        index,
        features,
        modes,
        contract_seeds,
        Some(census),
        Seed::default(),
        None,
    )
    .map(|enumeration| enumeration.product)
}

/// [`enumerate_transitions`] with the seeding passed in, so a test can permute the seed order and check that the product does not change. Production always passes [`contract_seeds`]. The permuted-seed tests cover both the pinned world and class grain, where each fiber's row is traced at its least member regardless of which item reaches it first.
fn enumerate_seeded<'i>(
    index: &'i SpecIndex,
    features: &[Sym],
    modes: EnumerationModes,
    seeds: fn(&WindowOptions<'_>) -> Vec<Item>,
    mut census: Option<&mut Vec<String>>,
    seed: Seed,
    file: Option<MemoFile>,
) -> Result<TablesEnumeration<'i>, String> {
    let mut engine = Engine::with_modes(
        index,
        features.iter().copied(),
        EngineModes {
            simulated_prospect: modes.simulated_prospect,
            vote_slots: modes.vote_slots,
            trace_memo: true,
            // The rows read only the settled triple, the prospect, the joint floor and the notes, never how a trace was decided, and the explain ladder costs more than every other explain-only allocation together.
            explain_ladder: false,
            ..EngineModes::default()
        },
    );
    engine.seed_bases(seed.bases);
    let config = feature_config_token(index, features.iter().copied());
    let mut options = WindowOptions::new(index).map_err(complaint)?;
    // Either engine mode makes a deep world. This is the only place the enumeration combines the two flags.
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
    let mut transitions: HashMap<WindowKey, Row> = HashMap::default();
    // The in-flight class-grain rows in creation order, which the echo pass walks, and a map from each row's key to its index.
    let mut pending_rows: Vec<PendingDeepRow> = Vec::new();
    let mut pending_seats: HashMap<PendingKey, usize> = HashMap::default();
    let mut seen: HashSet<Item> = HashSet::default();
    let mut worklist = seeds(&options);

    while let Some(item) = worklist.pop() {
        // One insert both tests and updates `seen` at pop time: an item already present is skipped.
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
        // A letter left is the settled record of a row already recorded, so past the seeds this lookup always hits. It runs once per item, and each window the item reaches compares the resulting index as an integer.
        let left_seat: Option<SettledSeat> =
            left.settled.as_ref().map(|settled| seats.seat(settled));
        // The trace reads the raw letter whatever the label says: locking is a property of the glyph the emitted lookup substitutes, not of what settles.
        let token = index
            .letter(rune)
            .expect("a worklist item's input is a modeled rune");
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
                        // The census check is applied here, not inside the deriver: a fiber's own `fourth_matters` is the raw filter result, and only the enumeration knows whether this input is in the depth-4 census.
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
                        // The identity is the fiber's full member list, not the admitted subset, so two items whose pins admit different subsets of one fiber accumulate into one row.
                        let identity3 = match boundary3 {
                            Some(token) => Identity3::Boundary(token),
                            None => Identity3::Members(fiber3.map_or_else(Vec::new, |seat| {
                                context.fibers[seat].members.clone()
                            })),
                        };
                        // The row is traced at the fiber's least member whether or not this item's pins admit it. The fiber invariant makes every member's record the same, and tracing a fixed member makes the traced window set, and so the fired set, independent of which item reached the fiber first.
                        let rep3 = match fiber3 {
                            Some(seat) => context.fibers[seat].members[0],
                            None => admitted3[0],
                        };
                        for members4 in slot4_entries {
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
                                left: LeftContext::letter(index, settled),
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
                        // A worklist item with different pins can reach a window key already recorded. The recorded settled state is what a re-trace would return, because the left label is injective into the trace's inputs, so a hit goes straight to the successor enqueue, whose pins still differ per item. The left-state comparison checks that premise and fails only if `cell_label` stops being injective over settled lefts.
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
                                // A right3 pin this window could not enumerate (the input is not deep) still names the raw token one past it, which is the successor's right2. Forward it, or a left decided at depth 4 gains follower windows no text can reach and the conform transition gate reports them as dead.
                                match (from_map, &right3_allowed) {
                                    (allowed, None) => allowed.map(Rc::new),
                                    (None, Some(pin)) => Some(Rc::clone(pin)),
                                    (Some(allowed), Some(pin)) => Some(Rc::new(
                                        allowed.intersection(pin.as_ref()).copied().collect(),
                                    )),
                                }
                            };
                            worklist.push(Item {
                                left: LeftContext::letter(index, settled),
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
    let mut named_classes: HashSet<String> = HashSet::default();
    // The echo check, and the emission of the class rows: for every multi-member row, the last admitted third-slot member is re-traced at the row's real left (and the last r4 member at the representative third), and its whole row-visible record must equal the representative's. This checks, on every build, the virtual-left collapse the fibers rely on at real lefts, entries and adjustments.
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
        lines.push(CacheSize::of("labels", labels.len(), labels.capacity()).line(&config));
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
            "[c] {config} memo_base_hits count={}",
            engine.base_hits()
        ));
        for (seat, count) in engine.base_hits_by_seat().iter().enumerate() {
            lines.push(format!(
                "[c] {config} memo_base_hits seat={seat} count={count}"
            ));
        }
        lines.push(format!(
            "[c] {config} resident_before_release kb={}",
            resident_kb()
        ));
    }

    // Taken here, before the memos are released: the drain, the sort and the partition check that follow do not change what the product reports as fired, and the partition check's re-traces are left out of it. The echo traces above are included.
    let cited_provenance = engine
        .fired()
        .iter()
        .map(|pointer| pointer.text(index))
        .collect();
    // The drain and the sort below are the run's other large working set, and they do not need the memos. Releasing the memos here keeps the two from coexisting, which would otherwise be the enumeration's peak memory. The memo file is written here too, from the trace memo the engine returns as a snapshot after freeing its prospect, candidate and closure memos, so the writer's buffers never coexist with them. The snapshot is then dropped, except for the configuration other enumerations will read, whose snapshot is held through the drain and the sort.
    let memo = if seed.keep_memo || file.is_some() {
        engine.take_memo()
    } else {
        engine.release_memos();
        None
    };
    if let Some(lines) = census.as_mut() {
        lines.push(format!(
            "[c] {config} resident_after_release kb={}",
            resident_kb()
        ));
    }
    let mut memo_write = None;
    if let Some(file) = file {
        let started = Instant::now();
        write_memo(
            index,
            &file.path,
            &file.head,
            memo.as_ref()
                .expect("a memo file is written from a kept memo"),
            &file.carried,
        )?;
        memo_write = Some(started.elapsed());
    }
    let memo = memo.filter(|_| seed.keep_memo);
    if memo_write.is_some()
        && let Some(lines) = census.as_mut()
    {
        lines.push(format!(
            "[c] {config} resident_after_memo_write kb={}",
            resident_kb()
        ));
    }

    // A row's outcome is its settled cell's label, so it is interned once per settled record here instead of once per row in the worklist. Every settled record is a cell some row settled into, so no unused label is interned.
    let seat_table = seats.into_table();
    let outcomes: Vec<Label> = seat_table
        .iter()
        .map(|settled| labels.intern_owned(cell_label(index, &settled.cell)))
        .collect();
    // Rank tuples compare in the same order as the label texts, while the rows keep their product-local ids.
    let ranks = labels.ranks();
    let mut rows: Vec<TransitionRow> = transitions
        .into_iter()
        .map(|(key, row)| {
            let [input_glyph, left, right1, right2, right3, right4] = key;
            TransitionRow {
                input_glyph,
                left,
                right1,
                right2,
                right3,
                right4,
                settled: row.settled,
                left_settled: row.left_settled,
                provenance: row.provenance,
                prospect: row.prospect,
                joint: row.joint,
            }
        })
        .collect();
    rows.sort_unstable_by_key(|row| row.labels().map(|label| ranks[label.0 as usize]));
    if let Some(lines) = census.as_mut() {
        lines.push(format!(
            "[c] {config} resident_after_sort kb={}",
            resident_kb()
        ));
    }
    // The product's cells are a set. Deduplicating here instead of in the emitter clones one cell per settled record instead of one per row. A row's settled index is checked first because most rows share an index already seen, and an integer set answers that without touching the cell.
    let mut seen_seats: HashSet<SettledSeat> = HashSet::default();
    let mut counted: HashSet<&CellId> = HashSet::default();
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
        labels,
        outcomes,
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
            contexts: HashMap::default(),
            r4_lists: HashMap::default(),
        };
        check.run(&product)?;
    }
    Ok(TablesEnumeration {
        product,
        options,
        memo,
        memo_write,
    })
}

/// The seeds the fixpoint starts from: every letter against every boundary left, boundary-major, unpinned. The worklist pops them from the back.
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

/// The two ligature filters of the right2 pipeline: keep the options before which the formed `liga` can still stand, with `slots` naming the two post-formation neighbors each option supplies. A loop instead of `retain`, because the check consults the guard and can fail.
///
/// The third and fourth slots' pipelines have the same shape but live in [`WindowOptions`], because the partition check calls them too. The second slot's filters are written inline here because nothing else calls them. A filter added to the second-slot pipeline belongs here, and one added to a deeper pipeline belongs in [`WindowOptions`].
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

/// This process's resident size in kibibytes, or `0` when `ps` gives no answer. It asks `ps` instead of the C library because the crate has no dependencies and declares no foreign functions for a diagnostic. It runs only on censused runs: a few times per table configuration, and twice per release plus once at the end of a replay.
pub(crate) fn resident_kb() -> u64 {
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

/// The display name of the ZWNJ chokepoint twin of a raw input glyph, `model.locked_glyph_name`. Public because the string replay labels an entry-bearing input after a ZWNJ the same way the enumeration does.
pub fn locked_glyph_name(raw_name: &str) -> String {
    format!("{raw_name}.noentry")
}

/// One right slot's label: a letter's rune name, or a boundary's own label. Public because the `liveness-cases` subcommand uses the same labels.
pub fn right_token_label(index: &SpecIndex, token: RightToken) -> String {
    match token {
        RightToken::Letter(rune, _) => index.resolve(rune).to_owned(),
        other => boundary_left_label(other.kind()).to_owned(),
    }
}

/// The label of a boundary at either end of a window, `table.BOUNDARY_LEFT_LABELS`: the run edge's own name, and for the other three the glyph the boundary ships as. A letter or an unknown token panics here, as the Python mapping raises `KeyError` for it.
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

/// A settlement error as a one-line message. Every fixpoint error is a plain string, because the subcommand reports any of them by printing it to stderr and exiting with status 1.
fn complaint(error: SettleError) -> String {
    error.to_string()
}

/// The error for one window label reached from two different left states, which means `cell_label` no longer distinguishes those states. The left states are formatted by name, because a `Settled` printed structurally would name its heights by interning id.
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

/// The label of one deep-slot member set: the bare letter for a class of one, and otherwise a content-addressed id, which is recorded in the class map the first time it appears.
///
/// A class of one gets no id. Downstream expansion reads a bare label as itself, so an id would only add a map entry and show consumers a `#C` token for a slot that names one letter.
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

/// The member a class row's echo re-traces: the last admitted member, or the first one when the last is the representative, since echoing the representative would re-trace the window the row already carries. A representative the pins never admitted is echoed against an admitted member, which still checks the fiber at a real left.
fn echo_member(members: &[RightToken], representative: RightToken) -> RightToken {
    let last = members[members.len() - 1];
    if last == representative {
        members[0]
    } else {
        last
    }
}

/// The echo check's `PartitionError` message: a member of a class row traced a different record than the representative, meaning the virtual-left fiber collapse fails at a real left. The representative's settled record and notes are passed in resolved, since the row holds only their indexes.
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

/// One row-visible record as the echo error names it: the four fields the check compares.
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

/// Whether a slot label is one of the five non-letter labels, `table.BOUNDARYISH`.
fn boundaryish(label: &str) -> bool {
    BOUNDARYISH.contains(&label)
}

/// The rune one slot label names, or `None` when the label is not a modeled rune's name — a boundary label, a class id, or a name this spec never interned.
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

/// The class-grain partition check, with the caches it fills while replaying the enumeration's decisions.
///
/// It runs over the crate's own product before the stream is written, because the filters, fibers and option lists it consults are not in the stream. Everything it consults was already computed during enumeration: the two filters' memos are filled, every live context's fibers are derived, and `right4_options` returns the same list for the same inputs. The product's fired set is taken before this check runs, so the check adds no provenance.
///
/// It checks, per base: the member sets of the observed r3 letter tokens are pairwise disjoint, and each lies inside the recomputed static option list and inside one fiber of its context's partition; right3 is not `#NA` exactly where the census and the third-slot filter say live; one slot deeper, r4 member sets are disjoint per base and r3 token, every member of an r3 token gets the same `fourth_slot_matters` result and the same computed r4 option list; and every class id resolves through the product's map, with every map entry used. Disjointness is checked per base, not per context, because worklist pins are per left state, so two bases in one context can admit nested subsets of one fiber. Coverage of the static option list is not checked, because pins exclude unreachable members, as label grain excludes their rows.
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
    /// Runs the check over one product, returning the error message of the first condition that fails.
    fn run(&mut self, product: &FixpointProduct) -> Result<(), String> {
        let index = self.engine.index();
        let classes: HashMap<&str, &[String]> = product
            .deep_classes
            .iter()
            .map(|(token, members)| (token.as_str(), members.as_slice()))
            .collect();
        let mut used: HashSet<&str> = HashSet::default();
        let mut seen3: HashMap<[&str; 4], HashMap<&str, &str>> = HashMap::default();
        let mut seen4: HashMap<([&str; 4], &str), HashMap<&str, &str>> = HashMap::default();
        for row in &product.transitions {
            let key = row.key(&product.labels);
            let family = rune_of(
                index,
                product
                    .labels
                    .text(row.input_glyph)
                    .split('.')
                    .next()
                    .unwrap_or_default(),
            );
            let right1 = rune_of(index, product.labels.text(row.right1));
            let right2 = rune_of(index, product.labels.text(row.right2));
            let letters_window = !boundaryish(product.labels.text(row.right1))
                && !boundaryish(product.labels.text(row.right2));
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
                if &**product.labels.text(row.right3) != NA_LABEL {
                    return Err(format!(
                        "{key:?}: right3 enumerated where the filters say dead"
                    ));
                }
                continue;
            }
            if &**product.labels.text(row.right3) == NA_LABEL {
                return Err(format!("{key:?}: right3 #NA where the filters say live"));
            }
            if product
                .labels
                .text(row.right3)
                .starts_with(DEEP_CLASS_PREFIX)
            {
                if !classes.contains_key(&**product.labels.text(row.right3)) {
                    return Err(format!(
                        "{key:?}: right3 token {} is not in the class map",
                        product.labels.text(row.right3)
                    ));
                }
                used.insert(&**product.labels.text(row.right3));
            }
            if boundaryish(product.labels.text(row.right3)) {
                if &**product.labels.text(row.right4) != NA_LABEL {
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
            let members3 = token_members(&classes, product.labels.text(row.right3));
            let base = [
                &**product.labels.text(row.input_glyph),
                &**product.labels.text(row.left),
                &**product.labels.text(row.right1),
                &**product.labels.text(row.right2),
            ];
            let taken3 = seen3.entry(base).or_default();
            for member in &members3 {
                if let Some(claimed) = taken3.get(member)
                    && *claimed != &**product.labels.text(row.right3)
                {
                    return Err(format!(
                        "{key:?}: r3 member {member} belongs to two tokens at one base: {claimed} and {}",
                        product.labels.text(row.right3)
                    ));
                }
                taken3.insert(member, &**product.labels.text(row.right3));
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
            let mut verdicts: HashSet<bool> = HashSet::default();
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
            if &**product.labels.text(row.right4) == NA_LABEL {
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
            if product
                .labels
                .text(row.right4)
                .starts_with(DEEP_CLASS_PREFIX)
            {
                if !classes.contains_key(&**product.labels.text(row.right4)) {
                    return Err(format!(
                        "{key:?}: right4 token {} is not in the class map",
                        product.labels.text(row.right4)
                    ));
                }
                used.insert(&**product.labels.text(row.right4));
            }
            if boundaryish(product.labels.text(row.right4)) {
                continue;
            }
            let members4 = token_members(&classes, product.labels.text(row.right4));
            let taken4 = seen4
                .entry((base, &**product.labels.text(row.right3)))
                .or_default();
            for member in &members4 {
                if let Some(claimed) = taken4.get(member)
                    && *claimed != &**product.labels.text(row.right4)
                {
                    return Err(format!(
                        "{key:?}: r4 member {member} belongs to two tokens at one base: {claimed} and {}",
                        product.labels.text(row.right4)
                    ));
                }
                taken4.insert(member, &**product.labels.text(row.right4));
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

    /// Fills this context's partition in the cache through the fiber deriver on a miss. The cache is filled lazily because rows whose third slot is a boundary or dead never need it.
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
        let mut static_letters: HashSet<Sym> = HashSet::default();
        let mut fiber_of: HashMap<Sym, usize> = HashMap::default();
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

    /// Fills the computed r4 option list for one context and r3 member. Cached because a class row asks for one per member and the members of different rows overlap.
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
        let letter = |rune: Sym| index.letter(rune).expect("the deriver names modeled runes");
        let options = self
            .options
            .right4_options(letter(right1), letter(right2), letter(third))
            .map_err(complaint)?;
        let labels: Vec<String> = options
            .into_iter()
            .map(|option| right_token_label(index, option))
            .collect();
        self.r4_lists
            .insert((family, right1, right2, third), labels);
        Ok(())
    }

    /// Records one context's partition as given instead of deriving it, so a test can supply the partition a real build's enumeration would already have cached.
    #[cfg(test)]
    fn seed_context(&mut self, index: &SpecIndex, context: [&str; 3], fibers: &[&[&str]]) {
        let named = |name: &str| {
            index
                .sym_of(name)
                .unwrap_or_else(|| panic!("the fixture mentions {name}"))
        };
        let mut static_letters: HashSet<Sym> = HashSet::default();
        let mut fiber_of: HashMap<Sym, usize> = HashMap::default();
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
    use std::path::{Path, PathBuf};
    use std::sync::Arc;

    use super::*;
    use crate::index::fixtures;
    use crate::memo::{Exclusion, MemoHead, memo_path, read_memo, unlocking_runes};
    use crate::stream::emit_transitions;

    /// A scratch directory of this module's own under `target/`, cleared first.
    fn scratch(name: &str) -> PathBuf {
        let directory = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target/test-scratch")
            .join(name);
        let _ = std::fs::remove_dir_all(&directory);
        std::fs::create_dir_all(&directory).expect("the scratch directory is makeable");
        directory
    }

    /// The two heights the ordinary fixtures join at.
    const HEIGHTS: &[(&str, &str)] = &[("baseline", "0"), ("x-height", "5")];

    /// A row is fourteen bytes of fields padded to sixteen, and its optional left index takes four bytes whether present or absent. A `TransitionRow` adds six four-byte labels and is padded to forty.
    #[test]
    fn a_row_is_its_fields_and_no_padding() {
        assert_eq!(std::mem::size_of::<Option<SettledSeat>>(), 4);
        assert_eq!(std::mem::size_of::<Row>(), 16);
        assert_eq!(std::mem::size_of::<TransitionRow>(), 40);
    }

    /// The registry the fixpoint fixtures share: `fixtures::four_family_registry` with the height table passed in, so a test can declare two heights at one y beside the ordinary heights.
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

    /// The pinned candidacy world that every fixture below is read in: `simulated_prospect` and `vote_slots` both off, so there is no deep world, the censuses are the chain censuses, and class grain cannot arise whatever `deep_classes` says.
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
            .filter(|row| {
                &**product.labels.text(row.input_glyph) == input
                    && &**product.labels.text(row.left) == left
            })
            .map(|row| {
                [
                    product.labels.text(row.right1).to_string(),
                    product.labels.text(row.right2).to_string(),
                    product.labels.text(row.right3).to_string(),
                    product.labels.text(row.right4).to_string(),
                ]
            })
            .collect()
    }

    /// The distinct `(input, left)` pairs a product records, in its own row order.
    fn heads(product: &FixpointProduct) -> Vec<(String, String)> {
        let mut pairs: Vec<(String, String)> = Vec::new();
        for row in &product.transitions {
            let pair = (
                product.labels.text(row.input_glyph).to_string(),
                product.labels.text(row.left).to_string(),
            );
            if !pairs.contains(&pair) {
                pairs.push(pair);
            }
        }
        pairs
    }

    /// A one-letter alphabet for checking the closure counts: one stance that neither accepts an entry nor offers an exit, so every window settles into the one cell and the fixpoint reaches one letter left.
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
    /// `qsPea` carries the only deep chain: a `prefer` whose right condition reaches three slots on, so `qsTea qsMay qsPea qsTea` is the one continuation that decides its window. The two stances it chooses between exit at different heights, which makes the choice visible one letter later: `qsTea` accepts an entry at either height, so the left the deep window commits is a different cell for each. `qsMay` accepts a baseline entry and offers no exit, so it ends every chain.
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
        // The input never joins, so every window settles into the one cell and the product lists only that cell.
        assert_eq!(product.cells.len(), 1);
        assert!(
            product
                .transitions
                .iter()
                .all(|row| &**product.outcome(row) == "qsPea.half")
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
            .filter(|row| &**product.labels.text(row.left) == "uni200C")
            .map(|row| &**product.labels.text(row.input_glyph))
            .collect();
        assert!(
            locked.contains(&"qsPea.noentry") && locked.contains(&"qsMay"),
            "the chokepoint twin is the entry-bearing input's label alone: {locked:?}"
        );
        assert!(!locked.contains(&"qsPea"));
        // The lock changes only the row's label: the trace settles the raw letter, whose cell is the one the outcome names.
        assert!(
            product
                .transitions
                .iter()
                .filter(|row| &**product.labels.text(row.input_glyph) == "qsPea.noentry")
                .all(|row| product.outcome(row).starts_with("qsPea.half")),
            "the locked twin still settles as qsPea"
        );
        // Nothing else in the product carries the suffix: a ZWNJ at any other slot is an ordinary boundary.
        assert!(
            product
                .transitions
                .iter()
                .all(|row| &**product.labels.text(row.left) == "uni200C"
                    || !product.labels.text(row.input_glyph).ends_with(".noentry"))
        );
    }

    #[test]
    fn the_deep_slots_split_only_the_windows_the_census_and_both_filters_admit() {
        let index = deep_alphabet();
        let product = product(&index);
        let deep: Vec<&str> = product
            .transitions
            .iter()
            .filter(|row| &**product.labels.text(row.right3) != NA_LABEL)
            .map(|row| &**product.labels.text(row.input_glyph))
            .collect();
        assert!(
            !deep.is_empty() && deep.iter().all(|input| *input == "qsPea"),
            "the censused input carries a third slot and nothing else does"
        );
        assert!(
            product
                .transitions
                .iter()
                .filter(|row| &**product.labels.text(row.right3) != NA_LABEL)
                .all(|row| &**product.labels.text(row.right1) == "qsTea"
                    && &**product.labels.text(row.right2) == "qsMay"),
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
        // The prefer matches only the chain's one full continuation, so the fourth slot changes the cell the window settles into.
        let outcomes: Vec<(&str, &str)> = product
            .transitions
            .iter()
            .filter(|row| {
                &**product.labels.text(row.input_glyph) == "qsPea"
                    && &**product.labels.text(row.left) == "#EDGE"
                    && &**product.labels.text(row.right3) == "qsPea"
            })
            .map(|row| (&**product.labels.text(row.right4), &**product.outcome(row)))
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
        // The left that only the fourth slot's one live token reaches: qsPea commits the x-height seam there and nowhere else, so qsTea's entry at that height identifies that continuation.
        assert_eq!(
            slots_at(&product, "qsTea", "qsPea.full.ex-y5")
                .iter()
                .map(|slots| slots.join(" "))
                .collect::<Vec<String>>(),
            ["qsMay qsPea #NA #NA"],
            "the successor's own second slot is pinned to the third lookahead that was enumerated behind it"
        );
        // The pin this window could not enumerate (qsTea is not deep, so it has no third slot to use it on) is forwarded to its own successor's second slot, which is the raw token one past that window.
        assert_eq!(
            slots_at(&product, "qsMay", "qsTea.plain.en-y5.ex-y0")
                .iter()
                .map(|slots| slots.join(" "))
                .collect::<Vec<String>>(),
            ["qsPea qsTea #NA #NA"],
            "without the forward this left would carry every second-slot option, and the extra windows are ones no text can reach"
        );
        // The sibling left, for contrast: reached by many unpinned items, it carries the whole option list behind the same right1.
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

    /// A right condition testing a list of families per hop, one `then:` hop per entry: [`chain`] with alternatives at each slot, which is how a fixture makes one deep slot live under two different tokens.
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

    /// `deep_alphabet` with two changes the r4 option lists need: `qsPea`'s chain reads its third hop as either `qsPea` or `qsTea`, so the fourth slot is live under both, and a `qsPeaMay` ligature makes `(qsPea, qsMay)` a formation pair. That pair makes `right4_options` differ by third token: the option `qsMay` survives behind `qsTea` but not behind `qsPea`.
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

    /// One hand-built window, kept as text until its product assigns local label ids.
    fn deep_row(labels: [&str; 6]) -> [String; 6] {
        labels.map(str::to_owned)
    }

    /// A product assembled out of hand-built windows and a stated class map. The partition assertion reads labels only, so the fixture omits settled records and outcomes.
    fn hand_product(rows: Vec<[String; 6]>, classes: &[(&str, &[&str])]) -> FixpointProduct {
        let mut labels = LabelPool::default();
        let transitions = rows
            .into_iter()
            .map(|key| {
                let [input_glyph, left, right1, right2, right3, right4] =
                    key.map(|text| labels.intern(&text));
                TransitionRow {
                    input_glyph,
                    left,
                    right1,
                    right2,
                    right3,
                    right4,
                    settled: SettledSeat::at(0),
                    left_settled: None,
                    provenance: NotesSeat::at(0),
                    prospect: 0,
                    joint: false,
                }
            })
            .collect();
        FixpointProduct {
            config: "default".to_owned(),
            transitions,
            labels,
            deep_classes: classes
                .iter()
                .map(|(token, members)| {
                    (
                        (*token).to_owned(),
                        members.iter().map(|member| (*member).to_owned()).collect(),
                    )
                })
                .collect(),
            ..FixpointProduct::default()
        }
    }

    /// The class id of a member list, so a test states the same id the emission would.
    fn class_of(members: &[&str]) -> String {
        let owned: Vec<String> = members.iter().map(|member| (*member).to_owned()).collect();
        deep_class_id(&owned)
    }

    /// One member list as owned strings, as the emission passes it.
    fn owned(members: &[&str]) -> Vec<String> {
        members.iter().map(|member| (*member).to_owned()).collect()
    }

    /// A class of one is labeled by its own letter and records nothing. A class of two gets a content-addressed id, recorded the first time and shared by every later row with the same members.
    #[test]
    fn a_deep_label_is_the_bare_letter_alone_and_a_class_id_otherwise() {
        let mut classes: Vec<(String, Vec<String>)> = Vec::new();
        let mut named: HashSet<String> = HashSet::default();
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

    /// The echo re-traces the last admitted member, or the first one when the last is the representative the row was built from, since a class of two would otherwise echo the window it is checked against.
    #[test]
    fn the_echo_member_is_the_last_admitted_unless_that_is_the_representative() {
        let index = deep_alphabet();
        let [pea, tea, may] =
            ["qsPea", "qsTea", "qsMay"].map(|name| fixtures::letter(&index, name));
        assert_eq!(echo_member(&[pea, tea, may], pea), may);
        assert_eq!(echo_member(&[pea, tea, may], may), pea);
        assert_eq!(echo_member(&[pea, tea], tea), pea);
        assert_eq!(echo_member(&[pea, tea], pea), tea);
    }

    /// Runs the partition check over a hand-built product, with each live context's fiber partition given instead of derived.
    ///
    /// In a real build every live context has already been derived when the check runs, so the cache is filled and the deriver is never called. Giving the partition supplies what the enumeration would have cached, and it lets these checks run in the pinned world, where there is no liveness probe and the filters use their chain branch alone.
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
            contexts: HashMap::default(),
            r4_lists: HashMap::default(),
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

    /// The partition check fails on a class token that the class map does not define.
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

    /// No record reads past a boundary, so nothing follows one inside a window.
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

    /// The partition check fails on a class-map entry that no row uses.
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

    /// A class may hold only members the static option list admits, and only members of one fiber.
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

    /// The fiber key includes the `fourth_slot_matters` result, so two members that disagree about it cannot be in one fiber.
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

    /// The fiber key stores the computed r4 option list, so two members inducing different lists means the key no longer matches the pipeline, which is what a filter added to `right4_options` without a key update would cause.
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

    /// The contract seeds in reverse order, so the seed production pops first is popped last here.
    fn reversed_seeds(options: &WindowOptions<'_>) -> Vec<Item> {
        let mut seeds = contract_seeds(options);
        seeds.reverse();
        seeds
    }

    #[test]
    fn a_permuted_seed_order_reaches_the_same_pinned_world_product() {
        let index = deep_alphabet();
        let contract = enumerate_seeded(
            &index,
            &[],
            PINNED,
            contract_seeds,
            None,
            Seed::default(),
            None,
        )
        .expect("the fixpoint closes")
        .product;
        let reversed = enumerate_seeded(
            &index,
            &[],
            PINNED,
            reversed_seeds,
            None,
            Seed::default(),
            None,
        )
        .expect("the fixpoint closes")
        .product;
        // Compared as streams, not products, because two of the product's fields are sets whose vector order the emitter decides: `cited_provenance` comes from a hash set and has no order of its own.
        assert_eq!(
            emit_transitions(&index, &contract),
            emit_transitions(&index, &reversed)
        );
        assert!(
            contract
                .transitions
                .iter()
                .any(|row| &**contract.labels.text(row.right4) != NA_LABEL),
            "and the product both orders reached is the one carrying the pinned deep windows, not a trivially equal pair"
        );
        // At label grain order-independence follows from the dedup by window key: a re-reached window reuses the settled state a re-trace would return, and the fired set is a union over a window set no traversal changes. The next test covers class grain.
    }

    /// Order-independence at class grain: each fiber's row is traced at its least member, so the row and the windows traced for it do not depend on which item reached the fiber first. The reversed seed order reaches the same stream, including deep classes, cells and fired provenance, from a product that has multi-member class rows.
    #[test]
    fn a_permuted_seed_order_reaches_the_same_class_grain_product() {
        let index = crate::liveness::tests::prospect_spec();
        let modes = EnumerationModes::default();
        let contract = enumerate_seeded(
            &index,
            &[],
            modes,
            contract_seeds,
            None,
            Seed::default(),
            None,
        )
        .expect("the fixpoint closes")
        .product;
        let reversed = enumerate_seeded(
            &index,
            &[],
            modes,
            reversed_seeds,
            None,
            Seed::default(),
            None,
        )
        .expect("the fixpoint closes")
        .product;
        assert!(
            !contract.deep_classes.is_empty(),
            "the product carries class rows, so the claim is about class grain"
        );
        assert_eq!(
            emit_transitions(&index, &contract),
            emit_transitions(&index, &reversed)
        );
    }

    /// An `ss03` enumeration that reads `default`'s finished memo for every window naming no unlocking rune of `ss03` produces the from-scratch product byte for byte (rows, classes, cells and fired provenance, which is what the stream contains) while answering windows from the base. The exclusion is required: the same base read without one gives `ss03` the wrong results for `qsMay`'s windows, which is what makes the equality assertion able to fail.
    #[test]
    fn a_configuration_seeded_from_default_reaches_its_from_scratch_product() {
        let index = fixtures::mini();
        let ss03 = fixtures::sym(&index, "ss03");
        let modes = EnumerationModes::default();
        let memo = enumerate_seeded(
            &index,
            &[],
            modes,
            contract_seeds,
            None,
            Seed {
                bases: Vec::new(),
                keep_memo: true,
            },
            None,
        )
        .expect("default closes")
        .memo;
        let memo = Arc::new(memo.expect("a kept memo comes back"));
        assert!(!memo.is_empty());
        let scratch = enumerate_seeded(
            &index,
            &[ss03],
            modes,
            contract_seeds,
            None,
            Seed::default(),
            None,
        )
        .expect("ss03 closes from scratch")
        .product;
        let seeded_with = |excluded: Exclusion| {
            let mut census: Vec<String> = Vec::new();
            let product = enumerate_seeded(
                &index,
                &[ss03],
                modes,
                contract_seeds,
                Some(&mut census),
                Seed {
                    bases: vec![MemoBase {
                        memo: Arc::clone(&memo),
                        excluded,
                    }],
                    keep_memo: false,
                },
                None,
            )
            .expect("ss03 closes over a base")
            .product;
            let hits = census
                .iter()
                .find_map(|line| line.strip_prefix("[c] ss03 memo_base_hits count="))
                .expect("the census reports the base hits")
                .parse::<u64>()
                .expect("as a count");
            assert!(census.contains(&format!("[c] ss03 memo_base_hits seat=0 count={hits}")));
            (product, hits)
        };
        let (seeded, hits) = seeded_with(Exclusion::of(&index, unlocking_runes(&index, &[ss03])));
        assert!(hits > 0, "the base answered windows");
        assert_eq!(
            emit_transitions(&index, &scratch),
            emit_transitions(&index, &seeded)
        );
        let (unfiltered, _) = seeded_with(Exclusion::none());
        assert_ne!(
            emit_transitions(&index, &scratch),
            emit_transitions(&index, &unfiltered),
            "without the exclusion the base answers qsMay's windows as default settles them"
        );
    }

    /// A configuration that keeps no memo writes it at the release point and returns none: `ss03` enumerated over `default`'s base with a file named and `keep_memo` off returns no snapshot, times the write, and leaves a file that reads back as the memo a kept run of the same enumeration returns, window for window.
    #[test]
    fn a_configuration_keeping_no_memo_files_it_at_the_release_point() {
        let index = fixtures::mini();
        let ss03 = fixtures::sym(&index, "ss03");
        let modes = EnumerationModes::default();
        let base = enumerate_seeded(
            &index,
            &[],
            modes,
            contract_seeds,
            None,
            Seed {
                bases: Vec::new(),
                keep_memo: true,
            },
            None,
        )
        .expect("default closes")
        .memo
        .expect("a kept memo comes back");
        let base = Arc::new(base);
        let bases = || {
            vec![MemoBase {
                memo: Arc::clone(&base),
                excluded: Exclusion::of(&index, unlocking_runes(&index, &[ss03])),
            }]
        };
        let kept = enumerate_seeded(
            &index,
            &[ss03],
            modes,
            contract_seeds,
            None,
            Seed {
                bases: bases(),
                keep_memo: true,
            },
            None,
        )
        .expect("ss03 closes over a base")
        .memo
        .expect("a kept memo comes back");
        assert!(!kept.is_empty(), "ss03 traces windows of its own");
        let dir = scratch("release-point-memo");
        let path = memo_path(&dir, "ss03");
        let head = MemoHead {
            config: "ss03".to_owned(),
            world: modes.world_token(),
            stamp: "release-point".to_owned(),
        };
        let filed = enumerate_seeded(
            &index,
            &[ss03],
            modes,
            contract_seeds,
            None,
            Seed {
                bases: bases(),
                keep_memo: false,
            },
            Some(MemoFile {
                path: path.clone(),
                head: head.clone(),
                carried: Vec::new(),
            }),
        )
        .expect("ss03 closes over a base");
        assert!(
            filed.memo.is_none(),
            "a memo nobody reads is let go of at the release point"
        );
        assert!(filed.memo_write.is_some(), "the write is clocked");
        assert!(
            path.is_file(),
            "the file was written inside the enumeration"
        );
        let back = read_memo(&index, &path, &head, |_| true).expect("the file reads");
        assert_eq!(back.len(), kept.len());
        for (key, entry) in &kept.entries {
            let again = back.entries[key];
            assert_eq!(back.settled(again), kept.settled(*entry));
            assert_eq!(
                back.notes[again.notes.index()],
                kept.notes[entry.notes.index()]
            );
            assert_eq!(back.delta(again), kept.delta(*entry));
            assert_eq!(back.reads(again), kept.reads(*entry));
            assert_eq!(
                (again.prospect(), again.joint_floor(), again.decided_stage()),
                (entry.prospect(), entry.joint_floor(), entry.decided_stage())
            );
        }
        std::fs::remove_dir_all(&dir).expect("the scratch directory is removable");
    }

    #[test]
    fn one_window_label_reached_from_two_left_states_stops_the_build() {
        // Two heights at one y make `cell_label` non-injective, and the prefer picks between them under the one continuation its chain reads, so the deep window commits a cell that has the same label as its siblings' but compares unequal to them.
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
