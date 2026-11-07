//! The table build's worklist fixpoint: it settles every window one configuration's alphabet can reach, once each, and returns the rows [`crate::fold`] folds into the two tables a build persists.
//!
//! A worklist item is a left state together with the slot restrictions it was reached under. A settled left is reachable only alongside the right1 that was the producing window's right2, because an entry refusal or an unlock conditioned on the follower makes any other combination contradictory: the left would never have committed there. The right2 allowed-set carries the producing window's enumerated right3 when it has one, and otherwise the late-formation guard's allowed second slots for an unformed formation pair, intersected with any right3 restriction the producing window could not use. The right3 allowed-set carries a producing window's enumerated right4 the same way, which restricts the successor windows of a left decided at depth 4 to the third lookahead that was actually behind them. `None` means unrestricted in both, and both sets compare by content.
//!
//! The product depends only on the set of rows, not on the traversal order, at either grain. At label grain the dedup is by window key, and a hit reuses the recorded settled state because the left label is injective into the trace's inputs, so the fired set is a union over a window set that no order changes. At class grain a fiber's row is traced at the fiber's representative, its least member under the label order (the first entry of the fiber's sorted member list), whichever item reaches the fiber first and whatever subset of it that item's slot restrictions admit. The admitted members accumulate as a union across items, which is also order-independent. The worklist is LIFO with the `seen` check at pop time, so the traversal is a fixed function of the seeds, and the two permuted-seed tests below check order-independence at each grain.
//!
//! In a deep mode set with the deep-classes flag on, the deep slots enumerate at class grain: the same static option lists, their letters split into the outcome fibers of [`crate::fiber::DeepFiberDeriver`], one in-flight row per base and fiber identity accumulating the admitted members across items, successor slot restrictions carrying those member sets instead of singletons, and a content-addressed id per multi-member set in the product's `deep_classes` map. Two checks run with it. The member cross-check re-traces a second member of every multi-member row at the row's actual left and requires the same stored row fields. `DeepPartitionCheck` runs over the finished product before it is returned. With the flag off, or in the pinned mode set, where class grain cannot arise, only the label-grain path runs, and the deep slots still enumerate: the rune sets and the filters decide which deep slots are live.
//!
//! One engine settles everything, and the two slot filters, the liveness probe and the fiber deriver all borrow it. The trace memo makes a re-reached window free, and `Engine::fired` becomes the product's `cited_provenance`, so a probe running on a second engine would silently drop entries from what the dead-policy gate sees as fired. For the same reason one liveness probe is lent to both filters and to the deriver.

use std::collections::BTreeSet;
use std::num::{NonZeroU16, NonZeroU32};
use std::rc::Rc;
use std::sync::Arc;

use crate::deep_slots::{FourthSlotFilter, ThirdSlotFilter, fourth_slot_inputs, third_slot_inputs};
use crate::engine::{CacheSize, Engine, EngineModes, MemoCeilings, Slots};
use crate::error::SettleError;
use crate::fiber::DeepFiberDeriver;
use crate::hash::{HashMap, HashSet};
use crate::index::{Ordinal, SpecIndex};
use crate::liveness::ProspectLiveness;
use crate::memo::{MemoSnapshot, SharedMemo};
use crate::model::Sym;
use crate::options::{FollowerMap, WindowOptions};
use crate::sha256;
use crate::stream::{FixpointProduct, Label, LabelPool, TransitionRow, feature_config_token};
use crate::types::{
    CellId, EDGE, LeftContext, NAMER_DOT, NotesId, NotesPool, RightToken, SPACE, Settled,
    SettledId, SettledPool, TokenKind, TransitionTrace, UNKNOWN, ZWNJ, cell_label,
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

/// The mode set one enumeration runs under, and at which grain. Python reads the three flags from module-level defaults that environment variables override (`kernel_exec.SIMULATED_PROSPECT_DEFAULT`, `kernel_exec.FOLLOWER_PREFER_SLOTS_DEFAULT` and `kernel_exec.DEEP_CLASSES_DEFAULT`). This crate reads no environment, so the caller passes them, and [`Default`] gives the default modes.
///
/// Either engine mode on makes a deep mode set: both deep-slot rune sets widen to every rune and the filters get their liveness probe. `deep_classes` only takes effect in a deep mode set. With both engine modes off, the mode set is the pinned one: `deep_classes` is accepted there and does nothing, because there is no fiber source to enumerate at class grain.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct EnumerationModes {
    pub simulated_prospect: bool,
    pub follower_prefer_slots: bool,
    pub deep_classes: bool,
}

impl Default for EnumerationModes {
    fn default() -> Self {
        Self {
            simulated_prospect: true,
            follower_prefer_slots: true,
            deep_classes: true,
        }
    }
}

/// The ceilings every table enumeration's engine holds its prospect and candidate memos to: 2^20 buckets for the prospect memo and 2^22 for the candidate memo, which hold 917,504 and 3,670,016 entries before they are cleared ([`MemoCeilings`]). Without them both memos grow with the windows an enumeration settles, beside the trace memo, which has no ceiling. What an engine holds with them is booked in `kernel_exec.SCRATCH_PEAK_BYTES` and `kernel_exec.DELTA_SLOT_BYTES`, whose comments carry the readings. `build-tables` takes `--prospect-memo-buckets=` and `--candidates-memo-buckets=` to set others.
pub const TABLE_MEMO_CEILINGS: MemoCeilings =
    MemoCeilings::new(1 << 20, 1 << 22).expect("both are powers of two of at least eight");

/// What one enumeration may read before settling a window itself, whether it returns its own memo ([`crate::memo`]), and the ceilings on its engine's prospect and candidate memos. The shared memos are consulted in the order given. `keep_memo` is set for the configuration other enumerations will read; it returns the snapshot to the caller, at the cost of holding it through the drain and the sort. Without it, the enumeration releases its memo at the release point ([`enumerate_for_tables`]). `publish_verdicts` names the unlocking-rune sets of the enumerations that will read that snapshot, each once; when it names any, the enumeration also captures the liveness verdicts its probes compute into the snapshot, published for each set, for those enumerations to be served them ([`crate::liveness`]). It costs a capture per probe, so it is left empty wherever no enumeration reads the snapshot. `memo_ceilings` is [`TABLE_MEMO_CEILINGS`] unless the caller sets others, which changes no product or memo.
#[derive(Debug)]
pub struct MemoAccess {
    pub shared_memos: Vec<SharedMemo>,
    pub keep_memo: bool,
    pub publish_verdicts: Vec<HashSet<Sym>>,
    pub memo_ceilings: MemoCeilings,
}

impl Default for MemoAccess {
    fn default() -> Self {
        Self {
            shared_memos: Vec::new(),
            keep_memo: false,
            publish_verdicts: Vec::new(),
            memo_ceilings: TABLE_MEMO_CEILINGS,
        }
    }
}

/// The content-addressed id one deep-slot member set carries, `table.deep_class_id`: `#C` plus the first twelve hex digits of the SHA-256 of the tab-joined members.
///
/// Identical member sets share one id across contexts, configurations and builds, which is what keeps cross-config artifact comparison meaningful. The caller passes the members sorted by letter, as the emission sorts them, because the digest is over the joined text.
pub fn deep_class_id(members: &[String]) -> String {
    let digest = sha256::digest_hex(members.join("\t").as_bytes());
    format!("{DEEP_CLASS_PREFIX}{}", &digest[..12])
}

/// A slot restriction's allowed tokens: a set compared and hashed by content, so two items restricted to the same tokens are one item, behind an [`Rc`] so an item is cheap to clone into the [`ItemTable`]. The `BTreeSet` order is interning order and is never read.
type Allowed = Rc<BTreeSet<RightToken>>;

/// The six labels one window is keyed by, `table.Window.key`: the input glyph, the left, and the four right slots, each as the id the pool minted for its text.
type WindowKey = [Label; 6];

/// The three labels that begin a window key and name its base: the input glyph, the left, and right1. A window is recorded only behind a base some item has reached, so a base no item has reached holds no window.
type BaseKey = [Label; 3];

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

/// What one seed names: the boundary left's kind and the input rune. The seed's item has no slot restriction.
type Seed = (TokenKind, Sym);

/// One worklist item: the left state as its id in the [`LeftTable`], the input rune, the right1 the left was reached alongside, and the two allowed-sets restricting the slots past it. Its equality is the `seen` key. Two lefts share an id exactly when they share a kind and a settled record, and a [`LeftContext`] holds a kind, a settled record and ordinals computed from that record, so comparing ids compares what its derived equality compares.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct Item {
    left: LeftId,
    rune: Sym,
    right1: Option<RightToken>,
    right2_allowed: Option<Allowed>,
    right3_allowed: Option<Allowed>,
}

/// One product-local index narrowed to two bytes for the row log and the pending rows, held as the index plus one so that an absent one takes no byte beside it, which leaves room for 65,535 indexes. Past them it panics, naming the field it narrows for, as [`crate::engine::TraceSettledId`] panics past its range (#266), instead of wrapping to a low id. A configuration reaches hundreds of labels, settled records and notes lists, as its `labels`, `settled_ids` and `notes` `[c]` lines count them.
fn narrow16(index: usize, field: &str) -> NonZeroU16 {
    index
        .checked_add(1)
        .and_then(|raw| u16::try_from(raw).ok())
        .and_then(NonZeroU16::new)
        .unwrap_or_else(|| panic!("{field} {index} is past the 65,535 indexes two bytes hold"))
}

/// [`narrow16`] at four bytes, for the ids of the tables that can outgrow two bytes: fragments, pending rows, member lists and worklist items.
fn narrow32(index: usize, field: &str) -> NonZeroU32 {
    index
        .checked_add(1)
        .and_then(|raw| u32::try_from(raw).ok())
        .and_then(NonZeroU32::new)
        .unwrap_or_else(|| {
            panic!("{field} {index} is past the 4,294,967,295 indexes four bytes hold")
        })
}

/// A [`Label`] narrowed by [`narrow16`]: a row's last three labels, and the three a fragment carries.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct ShortLabel(NonZeroU16);

impl ShortLabel {
    fn of(label: Label, field: &str) -> Self {
        Self(narrow16(label.0 as usize, field))
    }

    fn widen(self) -> Label {
        Label(u32::from(self.0.get()) - 1)
    }
}

/// A [`SettledId`] narrowed by [`narrow16`]: a row's or a pending row's settled record, and a fragment's or a pending row's left_settled.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct ShortSettled(NonZeroU16);

impl ShortSettled {
    fn of(id: SettledId, field: &str) -> Self {
        Self(narrow16(id.index(), field))
    }

    fn widen(self) -> SettledId {
        SettledId::at(usize::from(self.0.get()) - 1)
    }
}

/// A [`NotesId`] narrowed by [`narrow16`]: a row's or a pending row's provenance.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct ShortNotes(NonZeroU16);

impl ShortNotes {
    fn of(id: NotesId, field: &str) -> Self {
        Self(narrow16(id.index(), field))
    }

    fn widen(self) -> NotesId {
        NotesId::at(usize::from(self.0.get()) - 1)
    }
}

/// A window slot's token in two bytes, for a pending row: the five other kinds as one to five, and a letter as its rune-field ordinal plus five, from which [`TokenCode::token`] rebuilds the token [`SpecIndex::letter`] gives that rune. A letter whose ordinal leaves no room panics, naming the field.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct TokenCode(NonZeroU16);

impl TokenCode {
    /// The codes the five other kinds take, below every letter's.
    const KINDS: u16 = 5;

    fn of(token: RightToken, field: &str) -> Self {
        let code = match token {
            RightToken::Edge => 1,
            RightToken::Space => 2,
            RightToken::Zwnj => 3,
            RightToken::NamerDot => 4,
            RightToken::Unknown => Self::KINDS,
            RightToken::Letter(_, ordinal) => {
                ordinal.get().checked_add(Self::KINDS).unwrap_or_else(|| {
                    panic!(
                        "{field} names rune ordinal {ordinal}, past the 65,530 a two-byte token code holds"
                    )
                })
            }
        };
        Self(NonZeroU16::new(code).expect("every code is one or more"))
    }

    fn token(self, index: &SpecIndex) -> RightToken {
        match self.0.get() {
            1 => EDGE,
            2 => SPACE,
            3 => ZWNJ,
            4 => NAMER_DOT,
            Self::KINDS => UNKNOWN,
            code => {
                let ordinal = Ordinal::new(code - Self::KINDS)
                    .expect("a letter's code is past the other kinds'");
                RightToken::Letter(index.rune_at_ordinal(ordinal), ordinal)
            }
        }
    }
}

/// A left state's id in the [`LeftTable`], by [`narrow16`]: there are as many as the settled records a configuration reaches, and four more.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct LeftId(NonZeroU16);

impl LeftId {
    fn at(index: usize) -> Self {
        Self(narrow16(index, "left state id"))
    }

    fn index(self) -> usize {
        usize::from(self.0.get()) - 1
    }
}

/// A fragment's position in [`RowLog::fragments`], by [`narrow32`]: the base index's value, a fragment's next, and a pending row's base.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct FragmentId(NonZeroU32);

impl FragmentId {
    fn at(index: usize) -> Self {
        Self(narrow32(index, "fragment id"))
    }

    fn index(self) -> usize {
        self.0.get() as usize - 1
    }
}

/// A pending row's position in [`PendingRows::rows`], by [`narrow32`].
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct PendingId(NonZeroU32);

impl PendingId {
    fn at(index: usize) -> Self {
        Self(narrow32(index, "pending row id"))
    }

    fn index(self) -> usize {
        self.0.get() as usize - 1
    }
}

/// A member list's id in [`MemberLists`], by [`narrow32`].
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct ListId(NonZeroU32);

impl ListId {
    fn at(index: usize) -> Self {
        Self(narrow32(index, "member list id"))
    }

    fn index(self) -> usize {
        self.0.get() as usize - 1
    }
}

/// An item's id in the [`ItemTable`], by [`narrow32`]: the four bytes the worklist's stack holds for the item.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct ItemId(NonZeroU32);

impl ItemId {
    fn at(index: usize) -> Self {
        Self(narrow32(index, "worklist item id"))
    }

    fn index(self) -> usize {
        self.0.get() as usize - 1
    }
}

/// A row's prospect and joint-tiebreak flag in one byte, the prospect in the low bit and the flag above it. The engine returns the prospect as an `i64` because its join-count arithmetic sums these terms, but the term itself is zero or one (whether the follower's junction is claimed) with the prospect simulated or not. Any other value means the engine is no longer returning a count, and packing it panics.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Flags(u8);

impl Flags {
    fn new(prospect: i64, joint: bool) -> Self {
        let prospect = u8::try_from(prospect)
            .ok()
            .filter(|term| *term <= 1)
            .expect("a prospect is a junction count, zero or one");
        Self(prospect | (u8::from(joint) << 1))
    }

    fn prospect(self) -> i8 {
        (self.0 & 1) as i8
    }

    fn joint(self) -> bool {
        self.0 & 2 != 0
    }
}

/// One recorded window as the row log holds it: its last three labels, its settled record and its provenance, each narrowed to two bytes, and its prospect and joint flag in one byte, twelve bytes with the padding and no heap allocation of its own. The rest of its key and its left_settled are its [`Fragment`]'s. The settled record and the provenance are indexes into the run's [`SettledPool`] and [`NotesPool`]: a configuration reaches millions of rows but only hundreds of distinct records, so holding each record by value would copy its heap allocations into every row. The outcome is not stored: it is the settled cell's label, resolved once per settled record when the product is built.
#[derive(Clone, Copy, Debug)]
struct LogRow {
    right2: ShortLabel,
    right3: ShortLabel,
    right4: ShortLabel,
    settled: ShortSettled,
    provenance: ShortNotes,
    flags: Flags,
}

impl LogRow {
    fn new(slots: [Label; 3], settled: SettledId, provenance: NotesId, flags: Flags) -> Self {
        Self {
            right2: ShortLabel::of(slots[0], "a row's right2 label"),
            right3: ShortLabel::of(slots[1], "a row's right3 label"),
            right4: ShortLabel::of(slots[2], "a row's right4 label"),
            settled: ShortSettled::of(settled, "a row's settled record"),
            provenance: ShortNotes::of(provenance, "a row's provenance"),
            flags,
        }
    }

    /// The three labels past the row's base.
    fn slots(self) -> [Label; 3] {
        [
            self.right2.widen(),
            self.right3.widen(),
            self.right4.widen(),
        ]
    }
}

/// How many rows one chunk of the row log holds: as many as 64 KiB holds.
const ROWS_PER_CHUNK: usize = (64 << 10) / size_of::<LogRow>();

/// One run of one base's rows in its input's chain: the base's three labels and the left_settled its rows share, narrowed as a row's fields are, the chain position of its first row and how many rows follow it there, the base's next fragment, and, on a base's first fragment, the base's latest pending class-grain row, twenty-four bytes in all. A fragment never crosses a chunk boundary, so its rows are one slice of one chunk.
#[derive(Clone, Copy, Debug)]
struct Fragment {
    input: ShortLabel,
    left: ShortLabel,
    right1: ShortLabel,
    left_settled: Option<ShortSettled>,
    start: u32,
    next: Option<FragmentId>,
    pending: Option<PendingId>,
    count: u16,
}

impl Fragment {
    /// A fragment of `base` holding no rows yet, starting at chain position `start`.
    fn new(base: BaseKey, left_settled: Option<SettledId>, start: usize) -> Self {
        Self {
            input: ShortLabel::of(base[0], "a fragment's input label"),
            left: ShortLabel::of(base[1], "a fragment's left label"),
            right1: ShortLabel::of(base[2], "a fragment's right1 label"),
            left_settled: left_settled
                .map(|id| ShortSettled::of(id, "a fragment's left_settled record")),
            start: u32::try_from(start).unwrap_or_else(|_| {
                panic!(
                    "a fragment's chain position {start} is past the 4,294,967,296 positions four bytes hold"
                )
            }),
            next: None,
            pending: None,
            count: 0,
        }
    }

    fn base(&self) -> BaseKey {
        [self.input.widen(), self.left.widen(), self.right1.widen()]
    }
}

/// One input's rows in the order they were appended: its chunks, each allocated at [`ROWS_PER_CHUNK`] rows and filled before the next is allocated, its fragments in chain order, and how many rows it holds.
struct RowChain {
    input: Label,
    chunks: Vec<Vec<LogRow>>,
    fragments: Vec<FragmentId>,
    len: usize,
}

impl RowChain {
    fn new(input: Label) -> Self {
        Self {
            input,
            chunks: Vec::new(),
            fragments: Vec::new(),
            len: 0,
        }
    }

    /// Appends one row at the chain's end, allocating a chunk when the last one is full.
    fn push(&mut self, row: LogRow) {
        if self.len.is_multiple_of(ROWS_PER_CHUNK) {
            self.chunks.push(Vec::with_capacity(ROWS_PER_CHUNK));
        }
        self.chunks
            .last_mut()
            .expect("a chunk has room for this row")
            .push(row);
        self.len += 1;
    }

    /// One fragment's rows, a slice of the one chunk it lies in.
    fn rows(&self, fragment: &Fragment) -> &[LogRow] {
        if fragment.count == 0 {
            return &[];
        }
        let start = fragment.start as usize;
        let offset = start % ROWS_PER_CHUNK;
        &self.chunks[start / ROWS_PER_CHUNK][offset..offset + usize::from(fragment.count)]
    }
}

/// Where one item's pass over one base appends: the base's chain, its first fragment and its last.
#[derive(Clone, Copy, Debug)]
struct Cursor {
    chain: usize,
    first: FragmentId,
    last: FragmentId,
}

/// A revisited base as it stood when the pass began: its rows by their last three labels, each with its settled record and its fragment's left_settled, and its pending rows by [`PendingSlot`].
struct LocalRows {
    rows: HashMap<[Label; 3], (SettledId, Option<SettledId>)>,
    pending: HashMap<PendingSlot, PendingId>,
}

/// One item's pass over one base: where it appends, and, on a revisit, the local maps of the base's rows and pending rows. A pass over a base no item reached before has no local maps, and every window it reaches is new.
struct Visit {
    cursor: Cursor,
    local: Option<LocalRows>,
}

impl Visit {
    /// The settled record of the window behind this base whose last three labels are `slots`, when the base already records it, or `None` when the window is new and must be traced. A record left by another left state is `Err` with that state's record: the left label is injective into the trace's inputs, so the stored state is what a re-trace would return only when the two lefts agree, which is the premise `partition_error` reports the failure of.
    fn recorded(
        &self,
        slots: &[Label; 3],
        left_settled: Option<SettledId>,
    ) -> Result<Option<SettledId>, Option<SettledId>> {
        let Some(&(settled, recorded)) =
            self.local.as_ref().and_then(|local| local.rows.get(slots))
        else {
            return Ok(None);
        };
        if recorded != left_settled {
            return Err(recorded);
        }
        Ok(Some(settled))
    }

    /// The pending row this base already has at `slot`, under [`Visit::recorded`]'s comparison.
    fn pending(
        &self,
        slot: &PendingSlot,
        left_settled: Option<SettledId>,
        rows: &PendingRows,
    ) -> Result<Option<PendingId>, Option<SettledId>> {
        let Some(&id) = self
            .local
            .as_ref()
            .and_then(|local| local.pending.get(slot))
        else {
            return Ok(None);
        };
        let recorded = rows.get(id).left_settled();
        if recorded != left_settled {
            return Err(recorded);
        }
        Ok(Some(id))
    }
}

/// The rows the worklist records, held compactly in place of a map from window key to row: one [`RowChain`] an input, each base's rows a run of [`Fragment`]s in it, and the base index from each base an item has reached to its first fragment. A base's fragments chain from there, and its pending class-grain rows hang off that first fragment.
///
/// An item reaches one base per right1, and every window key begins with its base, so an item reaching a base no item reached before records every window behind it as new and probes nothing past the base index. Only a revisit reads the base's rows back, into the local maps of its [`Visit`], and looks each window up there, finding a window exactly where a map of every window would. One item's pass over one base reaches each window key once, because the slots' option lists repeat no token, so no row enters the log twice.
#[derive(Default)]
struct RowLog {
    chains: Vec<RowChain>,
    chain_of: Vec<Option<usize>>,
    fragments: Vec<Fragment>,
    bases: HashMap<BaseKey, FragmentId>,
    revisits: usize,
}

impl RowLog {
    /// The chain that holds `input`'s rows, started with the input's first base.
    fn chain_for(&mut self, input: Label) -> usize {
        let slot = input.0 as usize;
        if self.chain_of.len() <= slot {
            self.chain_of.resize(slot + 1, None);
        }
        if let Some(chain) = self.chain_of[slot] {
            return chain;
        }
        let chain = self.chains.len();
        self.chains.push(RowChain::new(input));
        self.chain_of[slot] = Some(chain);
        chain
    }

    /// Starts a fragment of `base` at the end of its chain.
    fn open(&mut self, chain: usize, base: BaseKey, left_settled: Option<SettledId>) -> FragmentId {
        let id = FragmentId::at(self.fragments.len());
        self.fragments
            .push(Fragment::new(base, left_settled, self.chains[chain].len));
        self.chains[chain].fragments.push(id);
        id
    }

    /// Starts one item's pass over `base`. A base no item reached before gets its first fragment and its entry in the base index. A base reached before is a revisit, and its rows and pending rows are read back into the visit's local maps.
    fn visit(
        &mut self,
        base: BaseKey,
        left_settled: Option<SettledId>,
        pending: &PendingRows,
    ) -> Visit {
        let chain = self.chain_for(base[0]);
        let Some(&first) = self.bases.get(&base) else {
            let first = self.open(chain, base, left_settled);
            self.bases.insert(base, first);
            return Visit {
                cursor: Cursor {
                    chain,
                    first,
                    last: first,
                },
                local: None,
            };
        };
        self.revisits += 1;
        let mut rows = HashMap::default();
        let mut last = first;
        let mut at = Some(first);
        while let Some(id) = at {
            let fragment = &self.fragments[id.index()];
            let recorded = fragment.left_settled.map(ShortSettled::widen);
            for row in self.chains[chain].rows(fragment) {
                rows.insert(row.slots(), (row.settled.widen(), recorded));
            }
            last = id;
            at = fragment.next;
        }
        let mut slots = HashMap::default();
        let mut at = self.fragments[first.index()].pending;
        while let Some(id) = at {
            let row = pending.get(id);
            slots.insert(row.slot(), id);
            at = row.next;
        }
        Visit {
            cursor: Cursor { chain, first, last },
            local: Some(LocalRows {
                rows,
                pending: slots,
            }),
        }
    }

    /// Appends one row to the base `cursor` is at. The base's last fragment takes it when that fragment ends the chain, shares the row's left_settled and has room in its chunk; otherwise a fragment chained from the last one does.
    fn append(&mut self, cursor: &mut Cursor, row: LogRow, left_settled: Option<SettledId>) {
        let position = self.chains[cursor.chain].len;
        let narrowed =
            left_settled.map(|id| ShortSettled::of(id, "a fragment's left_settled record"));
        let last = &self.fragments[cursor.last.index()];
        let extends = last.left_settled == narrowed
            && last.start as usize + usize::from(last.count) == position
            && (last.count == 0 || !position.is_multiple_of(ROWS_PER_CHUNK));
        if !extends {
            let base = last.base();
            let id = self.open(cursor.chain, base, left_settled);
            self.fragments[cursor.last.index()].next = Some(id);
            cursor.last = id;
        }
        self.fragments[cursor.last.index()].count += 1;
        self.chains[cursor.chain].push(row);
    }

    /// The pending walk's append of one class row to the base whose first fragment is `first`. The base's rows are scanned for the row's key first, its label rows and the class rows appended before it alike, and the row is appended only when none carries that key. Returns whether it was appended.
    fn append_new(
        &mut self,
        first: FragmentId,
        row: LogRow,
        left_settled: Option<SettledId>,
    ) -> bool {
        let chain = self.chain_for(self.fragments[first.index()].input.widen());
        let slots = row.slots();
        let mut last = first;
        let mut at = Some(first);
        while let Some(id) = at {
            let fragment = &self.fragments[id.index()];
            if self.chains[chain]
                .rows(fragment)
                .iter()
                .any(|held| held.slots() == slots)
            {
                return false;
            }
            last = id;
            at = fragment.next;
        }
        self.append(&mut Cursor { chain, first, last }, row, left_settled);
        true
    }

    /// The labels of the base whose first fragment is `first`.
    fn base(&self, first: FragmentId) -> BaseKey {
        self.fragments[first.index()].base()
    }

    /// The latest pending row of the base whose first fragment is `first`.
    fn pending_head(&self, first: FragmentId) -> Option<PendingId> {
        self.fragments[first.index()].pending
    }

    /// Makes `id` the latest pending row of the base whose first fragment is `first`. The row's own `next` names the one it follows.
    fn set_pending_head(&mut self, first: FragmentId, id: PendingId) {
        self.fragments[first.index()].pending = Some(id);
    }

    /// Frees the base index, which nothing after the pending walk reads.
    fn forget_bases(&mut self) {
        self.bases = HashMap::default();
    }

    /// What the log holds: its chunks at their allocated size, each chain's fragment list, the fragment headers, and the base index's buckets at their entry and a control byte each.
    fn bytes(&self) -> usize {
        let chains: usize = self
            .chains
            .iter()
            .map(|chain| {
                chain.chunks.iter().map(Vec::capacity).sum::<usize>() * size_of::<LogRow>()
                    + chain.fragments.capacity() * size_of::<FragmentId>()
            })
            .sum();
        let buckets = if self.bases.capacity() == 0 {
            0
        } else {
            (self.bases.capacity() * 8 / 7).next_power_of_two()
        };
        chains
            + self.fragments.capacity() * size_of::<Fragment>()
            + buckets * (size_of::<(BaseKey, FragmentId)>() + 1)
    }

    /// The final row vector: each input's chain decoded in input-rank order, each input's rows sorted by rank as one slice, and each chain's chunks freed once its rows have moved. Keys are unique and the input leads the key, so the slices in that order are the whole sort.
    fn drain(self, ranks: &[u32]) -> Vec<TransitionRow> {
        let Self {
            mut chains,
            fragments,
            ..
        } = self;
        chains.sort_unstable_by_key(|chain| ranks[chain.input.0 as usize]);
        let mut rows: Vec<TransitionRow> =
            Vec::with_capacity(chains.iter().map(|chain| chain.len).sum());
        for chain in chains {
            let start = rows.len();
            for id in &chain.fragments {
                let fragment = &fragments[id.index()];
                let left = fragment.left.widen();
                let right1 = fragment.right1.widen();
                let left_settled = fragment.left_settled.map(ShortSettled::widen);
                rows.extend(chain.rows(fragment).iter().map(|row| {
                    let [right2, right3, right4] = row.slots();
                    TransitionRow {
                        input_glyph: chain.input,
                        left,
                        right1,
                        right2,
                        right3,
                        right4,
                        settled: row.settled.widen(),
                        left_settled,
                        provenance: row.provenance.widen(),
                        prospect: row.flags.prospect(),
                        joint: row.flags.joint(),
                    }
                }));
            }
            rows[start..]
                .sort_unstable_by_key(|row| row.labels().map(|label| ranks[label.0 as usize]));
        }
        rows
    }
}

/// The distinct left states the worklist reaches, each held once with the settled id it is keyed by: the boundary lefts the seeds start from and one letter left per settled record a row settled into. An item and a pending row name their left by its two-byte id.
#[derive(Default)]
struct LeftTable {
    ids: HashMap<(TokenKind, Option<SettledId>), LeftId>,
    lefts: Vec<(Rc<LeftContext>, Option<SettledId>)>,
}

impl LeftTable {
    /// The id of the boundary left of this kind.
    fn boundary(&mut self, kind: TokenKind) -> LeftId {
        self.intern(kind, None, || LeftContext::boundary(kind))
    }

    /// The id of the letter left a window settling into `settled` hands its successor. [`LeftContext::letter`] builds it the first time.
    fn letter(&mut self, index: &SpecIndex, pool: &SettledPool, settled: SettledId) -> LeftId {
        self.intern(TokenKind::Letter, Some(settled), || {
            LeftContext::letter(index, pool.get(settled).clone())
        })
    }

    fn intern(
        &mut self,
        kind: TokenKind,
        settled: Option<SettledId>,
        build: impl FnOnce() -> LeftContext,
    ) -> LeftId {
        if let Some(&id) = self.ids.get(&(kind, settled)) {
            return id;
        }
        let id = LeftId::at(self.lefts.len());
        self.ids.insert((kind, settled), id);
        self.lefts.push((Rc::new(build()), settled));
        id
    }

    fn get(&self, id: LeftId) -> &Rc<LeftContext> {
        &self.lefts[id.index()].0
    }

    /// The settled record a left is keyed by, which a row reached from it stores as its left_settled.
    fn settled(&self, id: LeftId) -> Option<SettledId> {
        self.lefts[id.index()].1
    }
}

/// The worklist's items, each interned once, which is the `seen` set as a table: the stack holds an item's four-byte id each time the item is reached, and the item is processed the first time its id is popped. So the traversal is the one a stack of items with a `seen` check at pop time makes.
#[derive(Default)]
struct ItemTable {
    ids: HashMap<Item, ItemId>,
    items: Vec<Item>,
    popped: Vec<bool>,
}

impl ItemTable {
    /// Pushes this item's id onto the stack, interning the item the first time it is reached.
    fn push(&mut self, worklist: &mut Vec<ItemId>, item: Item) {
        let id = *self.ids.entry(item).or_insert_with_key(|item| {
            let id = ItemId::at(self.items.len());
            self.items.push(item.clone());
            self.popped.push(false);
            id
        });
        worklist.push(id);
    }

    /// The item one popped id names, the first time it is popped, and `None` every time after.
    fn first_pop(&mut self, id: ItemId) -> Option<Item> {
        let popped = &mut self.popped[id.index()];
        if *popped {
            return None;
        }
        *popped = true;
        Some(self.items[id.index()].clone())
    }

    fn len(&self) -> usize {
        self.items.len()
    }

    fn capacity(&self) -> usize {
        self.ids.capacity()
    }
}

/// The member lists pending rows name, each interned once: a third slot's identity, which is a fiber's whole membership or a boundary alone, and a fourth slot's r4 group.
#[derive(Default)]
struct MemberLists {
    ids: HashMap<Rc<[RightToken]>, ListId>,
    lists: Vec<Rc<[RightToken]>>,
}

impl MemberLists {
    fn intern(&mut self, members: &[RightToken]) -> ListId {
        if let Some(&id) = self.ids.get(members) {
            return id;
        }
        let id = ListId::at(self.lists.len());
        let shared: Rc<[RightToken]> = Rc::from(members);
        self.ids.insert(Rc::clone(&shared), id);
        self.lists.push(shared);
        id
    }

    fn get(&self, id: ListId) -> &[RightToken] {
        &self.lists[id.index()]
    }
}

/// One third-slot entry of a class-grain window: the boundary token when the entry is a boundary, the fiber's index in the context when it is a fiber, and the members this item's slot restrictions admitted.
type Slot3Entry = (Option<RightToken>, Option<usize>, Vec<RightToken>);

/// One fourth-slot entry of a class-grain window: the r4 group, or `None` where the fourth slot is dead and the row carries `#NA` there.
type Slot4Entry = Option<Vec<RightToken>>;

/// What an in-flight class-grain row is found by within its base: its right2, its third slot's identity, and its fourth slot's group. The identity is the fiber's whole membership, not the admitted subset, so two worklist items whose slot restrictions admit different subsets of one fiber accumulate into a single row.
type PendingSlot = (TokenCode, ListId, Option<ListId>);

/// One in-flight class-grain row: the representative trace's stored row fields, the r3 members accumulating across worklist items, and the frame the cross-check traces replay after the worklist, each as a narrowed id. `admitted3` is a set over rune ordinals, the first sixty-four in the row's own word and the rest, past sixty-four runes, in [`PendingRows`]' wide words. `base` is its base's first fragment and `next` the base's pending row before it. Forty-eight bytes in all.
///
/// The representative is the fiber's least member under the label order, the first entry of [`crate::fiber::Fiber::members`], whether or not the first item to reach the row admitted it. The r4 members carry no slot restriction and so are complete from the first item, which is why they are a plain group here while the third slot's are a set.
#[derive(Clone, Copy, Debug)]
struct PendingDeepRow {
    admitted3: u64,
    base: FragmentId,
    next: Option<PendingId>,
    identity3: ListId,
    members4: Option<ListId>,
    left: LeftId,
    token: TokenCode,
    right1: TokenCode,
    right2: TokenCode,
    boundary3: Option<TokenCode>,
    traced_r3_member: TokenCode,
    traced_r4_member: Option<TokenCode>,
    settled: ShortSettled,
    left_settled: Option<ShortSettled>,
    provenance: ShortNotes,
    flags: Flags,
}

impl PendingDeepRow {
    fn slot(&self) -> PendingSlot {
        (self.right2, self.identity3, self.members4)
    }

    fn settled(&self) -> SettledId {
        self.settled.widen()
    }

    fn left_settled(&self) -> Option<SettledId> {
        self.left_settled.map(ShortSettled::widen)
    }

    fn provenance(&self) -> NotesId {
        self.provenance.widen()
    }

    /// Whether a cross-check trace's record matches the representative's in the four fields a row stores: the settled triple and the notes (each read back through its pool), the prospect, and the joint-tiebreak flag. The ranking that reached them is not compared because the row does not store it.
    fn matches_stored_fields(
        &self,
        settled_pool: &SettledPool,
        notes: &NotesPool,
        cross_check: &TransitionTrace,
    ) -> bool {
        cross_check.settled == *settled_pool.get(self.settled())
            && cross_check.prospect == i64::from(self.flags.prospect())
            && cross_check.joint_tiebreak == self.flags.joint()
            && cross_check.notes == notes.get(self.provenance())
    }
}

/// The in-flight class-grain rows in creation order, which the member cross-check walks, with the wide words of their `admitted3` sets: `stride` words a row past its own word, none at sixty-four runes or fewer.
struct PendingRows {
    rows: Vec<PendingDeepRow>,
    stride: usize,
    wide: Vec<u64>,
}

impl PendingRows {
    /// Rows whose sets cover the rune-field ordinals of `runes` modeled runes, which come first in the field.
    fn new(runes: usize) -> Self {
        Self {
            rows: Vec::new(),
            stride: runes.saturating_sub(1) / 64,
            wide: Vec::new(),
        }
    }

    fn get(&self, id: PendingId) -> &PendingDeepRow {
        &self.rows[id.index()]
    }

    /// Adds a row at the end, with `admitted` as its first members.
    fn push(&mut self, row: PendingDeepRow, admitted: &[RightToken]) -> PendingId {
        let id = PendingId::at(self.rows.len());
        self.rows.push(row);
        self.wide.resize(self.wide.len() + self.stride, 0);
        self.admit(id, admitted);
        id
    }

    /// ORs the letters among `members` into a row's `admitted3` in place. A boundary entry's member carries no ordinal and is left out, since only a fiber's row reads its set back.
    fn admit(&mut self, id: PendingId, members: &[RightToken]) {
        for ordinal in members.iter().filter_map(|member| member.ordinal()) {
            let bit = usize::from(ordinal.get()) - 1;
            let mask = 1u64 << (bit % 64);
            match bit / 64 {
                0 => self.rows[id.index()].admitted3 |= mask,
                word => {
                    assert!(
                        word <= self.stride,
                        "admitted3 holds modeled runes' ordinals, and {ordinal} is past the {} its words cover",
                        64 * (self.stride + 1)
                    );
                    self.wide[id.index() * self.stride + word - 1] |= mask;
                }
            }
        }
    }

    /// The ordinals a row's `admitted3` holds, ascending.
    fn admitted(&self, id: PendingId) -> Vec<Ordinal> {
        let own = self.rows[id.index()].admitted3;
        let wide = &self.wide[id.index() * self.stride..(id.index() + 1) * self.stride];
        let mut ordinals = Vec::new();
        for (word, bits) in std::iter::once(own).chain(wide.iter().copied()).enumerate() {
            let mut rest = bits;
            while rest != 0 {
                let ordinal = word * 64 + rest.trailing_zeros() as usize + 1;
                ordinals.push(
                    u16::try_from(ordinal)
                        .ok()
                        .and_then(Ordinal::new)
                        .expect("a set bit is a rune-field ordinal"),
                );
                rest &= rest - 1;
            }
        }
        ordinals
    }
}

/// One configuration's whole fixpoint, the value [`crate::fold::fold_product`] folds: the rows in key order, the deep-class map their class tokens resolve through, the cells they settle into, and the provenance the engine fired while tabulating. Serialized, it is `table.FixpointProduct`, which `kernel_exec.enumerate_transitions` parses. No build stage or tool calls it; only the rebuild suite exercises it.
///
/// The engine is built here from `modes`, which also decide whether the rune sets widen, whether the filters get their liveness probe, and whether the deep slots enumerate at class grain, because none of the three is meaningful without the others.
pub fn enumerate_transitions(
    index: &SpecIndex,
    features: &[Sym],
    modes: EnumerationModes,
) -> Result<FixpointProduct, String> {
    enumerate_from_seeds(
        index,
        features,
        modes,
        contract_seeds,
        None,
        MemoAccess::default(),
    )
    .map(|enumeration| enumeration.product)
}

/// What [`enumerate_for_tables`] returns: the fixpoint, the [`WindowOptions`] it ran over, and the engine's finished memo when `access` asked to keep it.
pub struct TablesEnumeration<'i> {
    pub product: FixpointProduct,
    pub options: WindowOptions<'i>,
    pub memo: Option<Arc<MemoSnapshot>>,
}

/// [`enumerate_transitions`] that also returns the [`WindowOptions`] it ran over, the `--cache-stats` lines when `cache_stats` is given, and the engine's finished memo when `access` asks for it. The table build folds the product it holds, and the fold's certificates read the formation guard through the same options, whose verdict memo the worklist already filled, instead of sweeping the guard again. The memo access lets one configuration's enumeration read another's settled windows ([`crate::memo`]). A kept memo is taken at the release point, after the engine's other memos are freed; a configuration whose memo access does not keep it releases it there, so it holds none through its drain or its sort.
pub fn enumerate_for_tables<'i>(
    index: &'i SpecIndex,
    features: &[Sym],
    modes: EnumerationModes,
    cache_stats: Option<&mut Vec<String>>,
    access: MemoAccess,
) -> Result<TablesEnumeration<'i>, String> {
    enumerate_from_seeds(index, features, modes, contract_seeds, cache_stats, access)
}

/// [`enumerate_transitions`] that also appends the `--cache-stats` lines to `cache_stats`: the bytes the row log holds once the class rows are in it, its fragments, and how many times an item revisited a base (`row_log bytes= fragments= revisited_bases=`), each other collection's length and capacity once the worklist finishes, how many times the prospect and candidate memos were cleared at their ceilings (`memo_clears`), the size of the elimination text the memos hold, the shared memo hits in total and per shared memo, and the process's resident size before the memo release, after it, and after the sort. The caller writes the lines to stderr. None of this is computed unless asked for.
///
/// Memory decisions in this crate come down to entry counts, and a count read from a live alphabet settles in one run what a struct-size argument can only estimate.
pub fn enumerate_with_cache_stats(
    index: &SpecIndex,
    features: &[Sym],
    modes: EnumerationModes,
    cache_stats: &mut Vec<String>,
) -> Result<FixpointProduct, String> {
    enumerate_from_seeds(
        index,
        features,
        modes,
        contract_seeds,
        Some(cache_stats),
        MemoAccess::default(),
    )
    .map(|enumeration| enumeration.product)
}

/// [`enumerate_transitions`] with the seeds passed in, so a test can permute the seed order and check that the product does not change. Production always passes [`contract_seeds`]. The permuted-seed tests cover both the pinned mode set and class grain, where each fiber's row is traced at its least member regardless of which item reaches it first.
fn enumerate_from_seeds<'i>(
    index: &'i SpecIndex,
    features: &[Sym],
    modes: EnumerationModes,
    seeds: fn(&WindowOptions<'_>) -> Vec<Seed>,
    mut cache_stats: Option<&mut Vec<String>>,
    access: MemoAccess,
) -> Result<TablesEnumeration<'i>, String> {
    let mut engine = Engine::with_modes(
        index,
        features.iter().copied(),
        EngineModes {
            simulated_prospect: modes.simulated_prospect,
            follower_prefer_slots: modes.follower_prefer_slots,
            trace_memo: true,
            // The rows read only the settled triple, the prospect, the joint tiebreak and the notes, never how a trace was decided.
            explain_ranking: false,
            memo_ceilings: Some(access.memo_ceilings),
            ..EngineModes::default()
        },
    );
    engine.attach_shared_memos(access.shared_memos);
    if !access.publish_verdicts.is_empty() {
        engine.publish_verdicts(access.publish_verdicts);
    }
    let config = feature_config_token(index, features.iter().copied());
    let mut options = WindowOptions::new(index).map_err(error_message)?;
    // Either engine mode makes a deep mode set. This is the only place the enumeration combines the two flags.
    let deep_modes = modes.simulated_prospect || modes.follower_prefer_slots;
    let deep_inputs = third_slot_inputs(index, deep_modes);
    let deep4_inputs = fourth_slot_inputs(index, deep_modes);
    let mut third_slot_matters = ThirdSlotFilter::new(index);
    let mut fourth_slot_matters = FourthSlotFilter::new(index);
    let mut liveness = deep_modes.then(|| ProspectLiveness::new(index));
    let class_grain = modes.deep_classes && deep_modes;
    let mut deriver = class_grain.then(DeepFiberDeriver::new);

    let mut labels = LabelPool::default();
    let mut settled_pool = SettledPool::default();
    let mut notes = NotesPool::default();
    let mut lefts = LeftTable::default();
    let mut lists = MemberLists::default();
    let mut log = RowLog::default();
    let mut pending = PendingRows::new(index.rune_count());
    let mut items = ItemTable::default();
    let mut worklist: Vec<ItemId> = Vec::new();
    for (kind, rune) in seeds(&options) {
        let left = lefts.boundary(kind);
        items.push(
            &mut worklist,
            Item {
                left,
                rune,
                right1: None,
                right2_allowed: None,
                right3_allowed: None,
            },
        );
    }

    while let Some(id) = worklist.pop() {
        // The check runs at pop time: an item whose id was popped before is skipped.
        let Some(Item {
            left: left_ref,
            rune,
            right1: right1_constraint,
            right2_allowed,
            right3_allowed,
        }) = items.first_pop(id)
        else {
            continue;
        };
        let left = Rc::clone(lefts.get(left_ref));
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
        // A letter left is the settled record of a row already recorded, whose id keys the left, so each window the item reaches compares that id as an integer.
        let left_id: Option<SettledId> = lefts.settled(left_ref);
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
                match options.unformed.get(&(rune, right1.letter())) {
                    Some(map) => Some(Rc::clone(map)),
                    // A formation pair with no unformed window at all is inadmissible outright: the pair always forms, so no window of it enumerates.
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
                        && !options.unformed.contains_key(&(lead, option.letter())))
                });
                if let Some(map) = &follower_map {
                    kept.retain(|option| {
                        option.kind() == TokenKind::Letter && map.contains_key(&option.letter())
                    });
                }
                if let Some(restriction) = &right2_allowed {
                    kept.retain(|option| restriction.contains(option));
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
            // The base behind this right1, started at the first window or class row the item reaches there, where it first looks one up.
            let mut visit: Option<Visit> = None;

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
                        .map_err(error_message)?;

                if deep3_live && let Some(deriver) = deriver.as_mut() {
                    let probe = liveness.as_mut().expect(
                        "class grain needs a deep mode set, where the liveness probe exists",
                    );
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
                        .map_err(error_message)?;
                    let mut slot3_entries: Vec<Slot3Entry> = Vec::new();
                    for &option in &context.boundary_options {
                        if right3_allowed
                            .as_ref()
                            .is_none_or(|restriction| restriction.contains(&option))
                        {
                            slot3_entries.push((Some(option), None, vec![option]));
                        }
                    }
                    for (position, fiber) in context.fibers.iter().enumerate() {
                        let admitted: Vec<RightToken> = fiber
                            .members
                            .iter()
                            .copied()
                            .filter(|member| {
                                right3_allowed
                                    .as_ref()
                                    .is_none_or(|restriction| restriction.contains(member))
                            })
                            .collect();
                        if !admitted.is_empty() {
                            slot3_entries.push((None, Some(position), admitted));
                        }
                    }
                    for (boundary3, fiber3, admitted3) in slot3_entries {
                        // The rune-set check is applied here, not inside the deriver: a fiber's own `fourth_matters` is the raw filter result, and only the enumeration knows whether this input is in the depth-4 rune set.
                        let slot4_entries: Vec<Slot4Entry> = match fiber3 {
                            Some(position)
                                if deep4_inputs.contains(&rune)
                                    && context.fibers[position].fourth_matters =>
                            {
                                context.fibers[position]
                                    .r4_groups
                                    .iter()
                                    .cloned()
                                    .map(Some)
                                    .collect()
                            }
                            _ => vec![None],
                        };
                        // The identity is the fiber's full member list, not the admitted subset, so two items whose slot restrictions admit different subsets of one fiber accumulate into one row.
                        let identity3 = match boundary3 {
                            Some(token) => lists.intern(&[token]),
                            None => lists.intern(fiber3.map_or(&[][..], |position| {
                                context.fibers[position].members.as_slice()
                            })),
                        };
                        // The row is traced at the fiber's least member whether or not this item's slot restrictions admit it. The fiber invariant makes every member's record the same, and tracing a fixed member makes the traced window set, and so the fired set, independent of which item reached the fiber first.
                        let traced_r3_member = match fiber3 {
                            Some(position) => context.fibers[position].members[0],
                            None => admitted3[0],
                        };
                        for members4 in slot4_entries {
                            let traced_r4_member = members4.as_ref().map(|group| group[0]);
                            let right1_label = labels.token(index, right1);
                            let right2_label = labels.token(index, right2);
                            let members4_id = members4.as_deref().map(|group| lists.intern(group));
                            let pass = visit.get_or_insert_with(|| {
                                log.visit(
                                    [input_label, left_label, right1_label],
                                    left_id,
                                    &pending,
                                )
                            });
                            let slot: PendingSlot = (
                                TokenCode::of(right2, "a pending row's right2 token"),
                                identity3,
                                members4_id,
                            );
                            let settled = match pass.pending(&slot, left_id, &pending) {
                                Err(recorded) => {
                                    let display: WindowKey = [
                                        input_label,
                                        left_label,
                                        right1_label,
                                        right2_label,
                                        labels.token(index, traced_r3_member),
                                        labels.slot(index, traced_r4_member),
                                    ];
                                    return Err(partition_error(
                                        index,
                                        &labels.spelled(&display),
                                        recorded.map(|id| settled_pool.get(id)),
                                        left.settled.as_ref(),
                                    ));
                                }
                                Ok(Some(row)) => {
                                    pending.admit(row, &admitted3);
                                    pending.get(row).settled()
                                }
                                Ok(None) => {
                                    let trace = engine
                                        .transition_trace(
                                            &left,
                                            token,
                                            Slots::new(
                                                right1,
                                                right2,
                                                traced_r3_member,
                                                traced_r4_member.unwrap_or(EDGE),
                                            ),
                                        )
                                        .map_err(error_message)?;
                                    let settled = settled_pool.intern(&trace.settled);
                                    let provenance = notes.intern(trace.notes);
                                    let first = pass.cursor.first;
                                    let row = pending.push(
                                        PendingDeepRow {
                                            admitted3: 0,
                                            base: first,
                                            next: log.pending_head(first),
                                            identity3,
                                            members4: members4_id,
                                            left: left_ref,
                                            token: TokenCode::of(
                                                token,
                                                "a pending row's input token",
                                            ),
                                            right1: TokenCode::of(
                                                right1,
                                                "a pending row's right1 token",
                                            ),
                                            right2: slot.0,
                                            boundary3: boundary3.map(|boundary| {
                                                TokenCode::of(
                                                    boundary,
                                                    "a pending row's boundary3 token",
                                                )
                                            }),
                                            traced_r3_member: TokenCode::of(
                                                traced_r3_member,
                                                "a pending row's traced_r3_member token",
                                            ),
                                            traced_r4_member: traced_r4_member.map(|member| {
                                                TokenCode::of(
                                                    member,
                                                    "a pending row's traced_r4_member token",
                                                )
                                            }),
                                            settled: ShortSettled::of(
                                                settled,
                                                "a pending row's settled record",
                                            ),
                                            left_settled: left_id.map(|id| {
                                                ShortSettled::of(
                                                    id,
                                                    "a pending row's left_settled record",
                                                )
                                            }),
                                            provenance: ShortNotes::of(
                                                provenance,
                                                "a pending row's provenance",
                                            ),
                                            flags: Flags::new(trace.prospect, trace.joint_tiebreak),
                                        },
                                        &admitted3,
                                    );
                                    log.set_pending_head(first, row);
                                    settled
                                }
                            };
                            items.push(
                                &mut worklist,
                                Item {
                                    left: lefts.letter(index, &settled_pool, settled),
                                    rune: right1.letter(),
                                    right1: Some(right2),
                                    right2_allowed: Some(Rc::new(
                                        admitted3.iter().copied().collect(),
                                    )),
                                    right3_allowed: members4
                                        .as_ref()
                                        .map(|group| Rc::new(group.iter().copied().collect())),
                                },
                            );
                        }
                    }
                    continue;
                }

                let right3_slots: Vec<Option<RightToken>> = if deep3_live {
                    let mut candidates = options
                        .right3_options(right1, right2, follower_map.as_deref())
                        .map_err(error_message)?;
                    if let Some(restriction) = &right3_allowed {
                        candidates.retain(|option| restriction.contains(option));
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
                                    .map_err(error_message)?
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
                            .map_err(error_message)?
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
                        let pass = visit.get_or_insert_with(|| {
                            log.visit(
                                [window_key[0], window_key[1], window_key[2]],
                                left_id,
                                &pending,
                            )
                        });
                        let slots = [window_key[3], window_key[4], window_key[5]];
                        // A worklist item with different slot restrictions can reach a window key already recorded, behind a base another item reached. The recorded settled state is what a re-trace would return, because the left label is injective into the trace's inputs, so a hit goes straight to the successor enqueue, whose slot restrictions still differ per item. The left-state comparison checks that premise and fails only if `cell_label` stops being injective over settled lefts.
                        let settled = match pass.recorded(&slots, left_id) {
                            Err(recorded) => {
                                return Err(partition_error(
                                    index,
                                    &labels.spelled(&window_key),
                                    recorded.map(|id| settled_pool.get(id)),
                                    left.settled.as_ref(),
                                ));
                            }
                            Ok(Some(settled)) => settled,
                            Ok(None) => {
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
                                    .map_err(error_message)?;
                                let settled = settled_pool.intern(&trace.settled);
                                let row = LogRow::new(
                                    slots,
                                    settled,
                                    notes.intern(trace.notes),
                                    Flags::new(trace.prospect, trace.joint_tiebreak),
                                );
                                log.append(&mut pass.cursor, row, left_id);
                                settled
                            }
                        };

                        if right1.kind() == TokenKind::Letter {
                            let successor_allowed = if let Some(third) = right3 {
                                Some(singleton(third))
                            } else {
                                let from_map = follower_map
                                    .as_ref()
                                    .and_then(|map| map.get(&right2.letter()).cloned().flatten());
                                // A right3 restriction this window could not enumerate (the input is not deep) still names the raw token one past it, which is the successor's right2. Forward it, or a left decided at depth 4 gains follower windows no text can reach and the conform transition gate reports them as dead.
                                match (from_map, &right3_allowed) {
                                    (allowed, None) => allowed.map(Rc::new),
                                    (None, Some(restriction)) => Some(Rc::clone(restriction)),
                                    (Some(allowed), Some(restriction)) => Some(Rc::new(
                                        allowed
                                            .intersection(restriction.as_ref())
                                            .copied()
                                            .collect(),
                                    )),
                                }
                            };
                            items.push(
                                &mut worklist,
                                Item {
                                    left: lefts.letter(index, &settled_pool, settled),
                                    rune: right1.letter(),
                                    right1: Some(right2),
                                    right2_allowed: successor_allowed,
                                    right3_allowed: right4.map(singleton),
                                },
                            );
                        }
                    }
                }
            }
        }
    }

    let mut deep_classes: Vec<(String, Vec<String>)> = Vec::new();
    let mut named_classes: HashSet<String> = HashSet::default();
    // The member cross-check, and the emission of the class rows into the row log: for every multi-member row, the last admitted third-slot member is re-traced at the row's actual left (and the last r4 member at the representative third), and its stored row fields must all equal the representative's. This checks, on every build, the left-class assumption the fibers rely on at actual lefts, entries and adjustments.
    for (position, row) in pending.rows.iter().enumerate() {
        let left_context = lefts.get(row.left);
        let token = row.token.token(index);
        let right1 = row.right1.token(index);
        let right2 = row.right2.token(index);
        let traced_r3_member = row.traced_r3_member.token(index);
        let traced_r4_member = row.traced_r4_member.map(|code| code.token(index));
        let members4 = row.members4.map(|list| lists.get(list));
        let (label3, admitted3) = match row.boundary3 {
            Some(code) => {
                let boundary = code.token(index);
                (labels.token(index, boundary), vec![boundary])
            }
            None => {
                let mut members: Vec<RightToken> = pending
                    .admitted(PendingId::at(position))
                    .into_iter()
                    .map(|ordinal| RightToken::Letter(index.rune_at_ordinal(ordinal), ordinal))
                    .collect();
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
        let label4 = match members4 {
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
        let [input_label, left_label, right1_label] = log.base(row.base);
        let window_key: WindowKey = [
            input_label,
            left_label,
            right1_label,
            labels.token(index, right2),
            label3,
            label4,
        ];
        if row.boundary3.is_none() && admitted3.len() > 1 {
            let last3 = cross_check_member(&admitted3, traced_r3_member);
            let cross_check = engine
                .transition_trace(
                    left_context,
                    token,
                    Slots::new(right1, right2, last3, traced_r4_member.unwrap_or(EDGE)),
                )
                .map_err(error_message)?;
            if !row.matches_stored_fields(&settled_pool, &notes, &cross_check) {
                return Err(member_mismatch(
                    index,
                    &labels.spelled(&window_key),
                    last3,
                    row,
                    settled_pool.get(row.settled()),
                    notes.get(row.provenance()),
                    &cross_check,
                ));
            }
        }
        if let Some(group) = members4
            && group[0].kind() == TokenKind::Letter
            && group.len() > 1
        {
            let last4 = group[group.len() - 1];
            let cross_check = engine
                .transition_trace(
                    left_context,
                    token,
                    Slots::new(right1, right2, traced_r3_member, last4),
                )
                .map_err(error_message)?;
            if !row.matches_stored_fields(&settled_pool, &notes, &cross_check) {
                return Err(member_mismatch(
                    index,
                    &labels.spelled(&window_key),
                    last4,
                    row,
                    settled_pool.get(row.settled()),
                    notes.get(row.provenance()),
                    &cross_check,
                ));
            }
        }
        let class_row = LogRow::new(
            [window_key[3], window_key[4], window_key[5]],
            row.settled(),
            row.provenance(),
            row.flags,
        );
        if !log.append_new(row.base, class_row, row.left_settled()) {
            return Err(format!(
                "deep-class window {:?} collides with an existing row",
                labels.spelled(&window_key)
            ));
        }
    }

    if let Some(lines) = cache_stats.as_mut() {
        lines.push(format!(
            "[c] {config} row_log bytes={} fragments={} revisited_bases={}",
            log.bytes(),
            log.fragments.len(),
            log.revisits
        ));
        lines.push(CacheSize::of("seen", items.len(), items.capacity()).line(&config));
        lines.push(
            CacheSize::of("settled_ids", settled_pool.len(), settled_pool.capacity()).line(&config),
        );
        lines.push(CacheSize::of("notes", notes.len(), notes.capacity()).line(&config));
        lines.push(CacheSize::of("labels", labels.len(), labels.capacity()).line(&config));
        lines.push(
            CacheSize::of("pending_rows", pending.rows.len(), pending.rows.capacity())
                .line(&config),
        );
        for size in engine.cache_stats() {
            lines.push(size.line(&config));
        }
        lines.push(format!(
            "[c] {config} memo_clears prospect={} candidates={}",
            engine.prospect_memo_clears(),
            engine.candidates_memo_clears()
        ));
        lines.push(format!(
            "[c] {config} elimination_text bytes={}",
            engine.elimination_text_bytes()
        ));
        lines.push(format!(
            "[c] {config} shared_memo_hits count={}",
            engine.shared_memo_hits()
        ));
        for (id, count) in engine.shared_memo_hits_by_id().iter().enumerate() {
            lines.push(format!(
                "[c] {config} shared_memo_hits id={id} count={count}"
            ));
        }
        if let Some(bytes) = engine.published_verdict_bytes() {
            lines.push(format!("[c] {config} published_verdicts bytes={bytes}"));
        }
        lines.push(format!(
            "[c] {config} shared_verdicts hits={} diverged={} refused={} misses={}",
            engine.shared_verdict_hits(),
            engine.shared_verdict_divergences(),
            engine.shared_verdict_refusals(),
            engine.shared_verdict_misses()
        ));
        lines.push(format!(
            "[c] {config} resident_before_release kb={}",
            resident_kb()
        ));
    }
    // Nothing reads these past the stats above, so they are freed here instead of at the return, and everything from here to the return runs without them: the snapshot build, the drain, the sort and the partition check. The row log keeps its chains and fragment headers for the drain.
    drop(worklist);
    drop(items);
    drop(pending);
    drop(lists);
    drop(lefts);
    log.forget_bases();

    // Taken here, before the memos are released: the drain, the sort and the partition check that follow do not change what the product reports as fired, and the partition check's re-traces are left out of it. The cross-check traces above are included.
    let cited_provenance = engine
        .fired()
        .iter()
        .map(|pointer| pointer.text(index))
        .collect();
    // The drain and the sort below are the run's other large working set, and they do not need the memos. Releasing the memos here keeps the two from coexisting, which would otherwise be the enumeration's peak memory. Only the configuration other enumerations will read keeps its trace memo, as the snapshot the engine returns after freeing its prospect, candidate and closure memos, and holds it through the drain and the sort.
    let memo = if access.keep_memo {
        engine.take_memo().map(Arc::new)
    } else {
        engine.release_memos();
        None
    };
    if let Some(lines) = cache_stats.as_mut() {
        lines.push(format!(
            "[c] {config} resident_after_release kb={}",
            resident_kb()
        ));
    }

    // A row's outcome is its settled cell's label, so it is interned once per settled record here instead of once per row in the worklist. Every settled record is a cell some row settled into, so no unused label is interned.
    let settled_table = settled_pool.into_table();
    let outcomes: Vec<Label> = settled_table
        .iter()
        .map(|settled| labels.intern_owned(cell_label(index, &settled.cell)))
        .collect();
    // Rank tuples compare in the same order as the label texts, while the rows keep their product-local ids.
    let ranks = labels.ranks();
    let rows = log.drain(&ranks);
    if let Some(lines) = cache_stats.as_mut() {
        lines.push(format!(
            "[c] {config} resident_after_sort kb={}",
            resident_kb()
        ));
    }
    // The product's cells are a set. Deduplicating here instead of in the emitter clones one cell per settled record instead of one per row. A row's settled index is checked first because most rows share an index already seen, and an integer set answers that without touching the cell.
    let mut seen_ids: HashSet<SettledId> = HashSet::default();
    let mut counted: HashSet<&CellId> = HashSet::default();
    let mut cells: Vec<CellId> = Vec::new();
    for row in &rows {
        if seen_ids.insert(row.settled) {
            let cell = &settled_table[row.settled.index()].cell;
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
        settled_records: settled_table,
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
    })
}

/// The seeds the fixpoint starts from: every letter against every boundary left, boundary-major, unrestricted. The worklist pops them from the back.
fn contract_seeds(options: &WindowOptions<'_>) -> Vec<Seed> {
    let mut seeds = Vec::with_capacity(SEED_KINDS.len() * options.letters.len());
    for kind in SEED_KINDS {
        for &rune in &options.letters {
            seeds.push((kind, rune));
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
            .map_err(error_message)?
        {
            kept.push(option);
        }
    }
    Ok(kept)
}

/// This process's resident size in kibibytes, or `0` when `ps` gives no answer. It asks `ps` instead of the C library because the crate has no dependencies and declares no foreign functions for a diagnostic. It runs only on runs that ask for `--cache-stats`: a few times per table configuration, and twice per release plus once at the end of a replay.
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

/// The bytes this process's malloc zones hold allocated, the `All zones: <n> nodes (<bytes> bytes)` line of macOS's `/usr/bin/heap`, or `0` where the tool is missing or gives no such line. Unlike the resident size and the physical footprint, it leaves out what the allocator still holds of freed large allocations, which it returns to the system at a time of its own choosing, so it does not depend on when that happens. It shells out for the reason [`resident_kb`] does.
pub(crate) fn heap_bytes() -> u64 {
    let pid = std::process::id();
    std::process::Command::new("/usr/bin/heap")
        .args(["-q", &pid.to_string()])
        .output()
        .ok()
        .and_then(|out| String::from_utf8(out.stdout).ok())
        .and_then(|text| {
            text.lines().find_map(|line| {
                line.trim()
                    .strip_prefix("All zones:")
                    .and_then(|rest| rest.split_once('('))
                    .and_then(|(_, bytes)| bytes.strip_suffix(" bytes)"))
                    .and_then(|bytes| bytes.parse::<u64>().ok())
            })
        })
        .unwrap_or(0)
}

/// One token as a slot restriction's allowed-set.
fn singleton(token: RightToken) -> Allowed {
    Rc::new(BTreeSet::from([token]))
}

/// The display name of the locked copy of a raw input glyph, `model.locked_glyph_name`. Public because the string replay labels an entry-bearing input after a ZWNJ the same way the enumeration does.
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
fn error_message(error: SettleError) -> String {
    error.to_string()
}

/// The error for one window label reached from two different left states, which means `cell_label` no longer distinguishes those states. The left states are formatted by name, because a `Settled` printed structurally would name its heights by interning id.
fn partition_error(
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

/// One left state as the partition error names it: the cell it settled into, the junction it committed, and the connector pixels on that junction.
fn left_state_text(index: &SpecIndex, settled: Option<&Settled>) -> String {
    match settled {
        None => "a boundary left".to_owned(),
        Some(state) => format!(
            "{} (junction {}, extension {})",
            cell_label(index, &state.cell),
            state
                .junction
                .map_or("none", |height| index.resolve(height)),
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

/// The member a class row's cross-check re-traces: the last admitted member, or the first one when the last is the representative, since cross-checking the representative would re-trace the window the row already carries. A representative the slot restrictions never admitted is cross-checked against an admitted member, which still checks the fiber at an actual left.
fn cross_check_member(members: &[RightToken], representative: RightToken) -> RightToken {
    let last = members[members.len() - 1];
    if last == representative {
        members[0]
    } else {
        last
    }
}

/// The member cross-check's `PartitionError` message: a member of a class row traced a different record than the representative, meaning the left-class assumption fails at an actual left. The representative's settled record and notes are passed in resolved, since the row holds only their indexes.
fn member_mismatch(
    index: &SpecIndex,
    key: &[&str; 6],
    member: RightToken,
    expected: &PendingDeepRow,
    expected_settled: &Settled,
    expected_notes: &[String],
    got: &TransitionTrace,
) -> String {
    format!(
        "deep-class member mismatch at {key:?}: member {} traces {} where the representative traced {}",
        right_token_label(index, member),
        stored_row_text(
            index,
            &got.settled,
            got.prospect,
            got.joint_tiebreak,
            &got.notes
        ),
        stored_row_text(
            index,
            expected_settled,
            i64::from(expected.flags.prospect()),
            expected.flags.joint(),
            expected_notes
        )
    )
}

/// One row's stored fields as the member-mismatch error names them: the four fields the cross-check compares.
fn stored_row_text(
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
/// It checks, per base: the member sets of the observed r3 letter tokens are pairwise disjoint, and each lies inside the recomputed static option list and inside one fiber of its context's partition; right3 is not `#NA` exactly where the rune set and the third-slot filter say live; one slot deeper, r4 member sets are disjoint per base and r3 token, every member of an r3 token gets the same `fourth_slot_matters` result and the same computed r4 option list; and every class id resolves through the product's map, with every map entry used. Disjointness is checked per base, not per context, because slot restrictions are per left state, so two bases in one context can admit nested subsets of one fiber. Coverage of the static option list is not checked, because slot restrictions exclude unreachable members, as label grain excludes their rows.
///
/// The product's rows are in key order, so each base's rows are one run, and the check holds the member claims of one base at a time, clearing them when the next base starts. That is sound only while the bases arrive in order: a base whose rows came back after another base's would be checked against cleared claims. So a base that sorts before the one just checked is an error, raised here because the fold's `assert_key_sorted`, which checks the same order, runs only after this check.
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
        let mut last_base: Option<[&str; 4]> = None;
        let mut seen3: HashMap<&str, &str> = HashMap::default();
        let mut seen4: HashMap<&str, HashMap<&str, &str>> = HashMap::default();
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
                    .map_err(error_message)?;
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
            if let Some(last) = last_base
                && base != last
            {
                if base < last {
                    return Err(format!(
                        "the product's bases are not in key order: {base:?} follows {last:?}"
                    ));
                }
                seen3.clear();
                seen4.clear();
            }
            last_base = Some(base);
            for member in &members3 {
                if let Some(claimed) = seen3.get(member)
                    && *claimed != &**product.labels.text(row.right3)
                {
                    return Err(format!(
                        "{key:?}: r3 member {member} belongs to two tokens at one base: {claimed} and {}",
                        product.labels.text(row.right3)
                    ));
                }
                seen3.insert(member, &**product.labels.text(row.right3));
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
                        .map_err(error_message)?,
                );
            }
            if verdicts.len() > 1 {
                let mut names = members3.clone();
                names.sort_unstable();
                return Err(format!(
                    "{key:?}: members disagree on the fourth_slot_matters verdict: {names:?}"
                ));
            }
            // The rune-set gate is ANDed in here rather than inside the filter, which is the same split the enumeration makes when it decides whether a fiber's r4 groups become slot-4 entries.
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
            let taken4 = seen4.entry(&**product.labels.text(row.right3)).or_default();
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
            .map_err(error_message)?;
        let mut static_letters: HashSet<Sym> = HashSet::default();
        let mut fiber_of: HashMap<Sym, usize> = HashMap::default();
        for (position, fiber) in fibers.fibers.iter().enumerate() {
            for member in &fiber.members {
                static_letters.insert(member.letter());
                fiber_of.insert(member.letter(), position);
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
            .map_err(error_message)?;
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
        for (position, fiber) in fibers.iter().enumerate() {
            for member in *fiber {
                static_letters.insert(named(member));
                fiber_of.insert(named(member), position);
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
    use std::sync::Arc;

    use super::*;
    use crate::index::fixtures;
    use crate::memo::{Exclusion, unlocking_runes};
    use crate::stream::emit_transitions;

    /// The two heights the ordinary fixtures join at.
    const HEIGHTS: &[(&str, &str)] = &[("baseline", "0"), ("x-height", "5")];

    /// A row of the row log is eleven bytes of fields padded to twelve, and a chunk holds the rows 64 KiB holds. A fragment header is twenty-four bytes and a base index entry sixteen, beside its control byte. A pending class-grain row is forty-eight bytes with its first `admitted3` word, and the worklist's stack holds four bytes an item. A product row's optional left index takes four bytes whether present or absent, and a `TransitionRow` adds six four-byte labels and is padded to forty.
    #[test]
    fn a_row_is_its_fields_and_no_padding() {
        assert_eq!(size_of::<LogRow>(), 12);
        assert_eq!(ROWS_PER_CHUNK, 5461);
        assert_eq!(size_of::<Fragment>(), 24);
        assert_eq!(size_of::<(BaseKey, FragmentId)>(), 16);
        assert_eq!(size_of::<PendingDeepRow>(), 48);
        assert_eq!(size_of::<ItemId>(), 4);
        assert_eq!(size_of::<Option<SettledId>>(), 4);
        assert_eq!(size_of::<TransitionRow>(), 40);
    }

    /// One row of the row log behind `slots`, with provenance zero, no prospect and no joint tiebreak.
    fn log_row(slots: [Label; 3], settled: usize) -> LogRow {
        LogRow::new(
            slots,
            SettledId::at(settled),
            NotesId::at(0),
            Flags::new(0, false),
        )
    }

    /// A pending row of the base whose first fragment is `base`, found there by `slot`, with every field the row log does not read at its first id.
    fn pending_row(
        base: FragmentId,
        next: Option<PendingId>,
        slot: PendingSlot,
        left_settled: Option<SettledId>,
    ) -> PendingDeepRow {
        PendingDeepRow {
            admitted3: 0,
            base,
            next,
            identity3: slot.1,
            members4: slot.2,
            left: LeftId::at(0),
            token: TokenCode::of(EDGE, "a test row's token"),
            right1: TokenCode::of(EDGE, "a test row's right1"),
            right2: slot.0,
            boundary3: None,
            traced_r3_member: TokenCode::of(EDGE, "a test row's third member"),
            traced_r4_member: None,
            settled: ShortSettled::of(SettledId::at(0), "a test row's settled record"),
            left_settled: left_settled.map(|id| ShortSettled::of(id, "a test row's left")),
            provenance: ShortNotes::of(NotesId::at(0), "a test row's provenance"),
            flags: Flags::new(0, false),
        }
    }

    /// A pass over a base no item reached before reads nothing back, and every window it reaches is new. A revisit reads the base's rows and pending rows back as they stood when it began: a window or pending row the base holds from the same left state is a hit, the same one from another left state is refused with the state it was recorded from, which the enumeration reports as `partition_error`, and one the base does not hold is a miss, whose row a later revisit finds in a fragment chained from the base's last one.
    #[test]
    fn a_revisited_base_reads_its_rows_back_and_appends_its_misses() {
        let mut labels = LabelPool::default();
        let [pea, edge, tea, may, na, space] =
            ["qsPea", "#EDGE", "qsTea", "qsMay", "#NA", "space"].map(|text| labels.intern(text));
        let mut pending = PendingRows::new(3);
        let mut log = RowLog::default();
        let base = [pea, edge, tea];
        let left = Some(SettledId::at(4));
        let held_slot: PendingSlot = (
            TokenCode::of(EDGE, "a test slot's right2"),
            ListId::at(0),
            None,
        );
        let other_slot: PendingSlot = (held_slot.0, ListId::at(1), None);

        let mut first = log.visit(base, left, &pending);
        assert!(first.local.is_none());
        assert_eq!(first.recorded(&[may, na, na], left), Ok(None));
        log.append(&mut first.cursor, log_row([may, na, na], 1), left);
        log.append(&mut first.cursor, log_row([edge, na, na], 2), left);
        let held = pending.push(
            pending_row(
                first.cursor.first,
                log.pending_head(first.cursor.first),
                held_slot,
                left,
            ),
            &[],
        );
        log.set_pending_head(first.cursor.first, held);
        // Another base of the same input between the two passes, so the revisit cannot extend the base's fragment.
        let mut elsewhere = log.visit([pea, edge, may], None, &pending);
        log.append(&mut elsewhere.cursor, log_row([space, na, na], 3), None);

        let mut second = log.visit(base, left, &pending);
        assert_eq!(
            second.recorded(&[may, na, na], left),
            Ok(Some(SettledId::at(1)))
        );
        assert_eq!(second.recorded(&[may, na, na], None), Err(left));
        assert_eq!(second.recorded(&[space, na, na], left), Ok(None));
        assert_eq!(second.pending(&held_slot, left, &pending), Ok(Some(held)));
        assert_eq!(second.pending(&held_slot, None, &pending), Err(left));
        assert_eq!(second.pending(&other_slot, left, &pending), Ok(None));
        log.append(&mut second.cursor, log_row([space, na, na], 3), left);
        assert_eq!(
            log.fragments.len(),
            3,
            "the miss opens a fragment of the base past the other base's"
        );

        let third = log.visit(base, left, &pending);
        assert_eq!(
            third.recorded(&[space, na, na], left),
            Ok(Some(SettledId::at(3)))
        );
        assert_eq!(third.local.as_ref().map(|local| local.rows.len()), Some(3));
        assert_eq!(log.revisits, 2);
        let rows = log.drain(&labels.ranks());
        assert_eq!(
            rows.iter()
                .filter(|row| row.right1 == tea && row.left_settled == left)
                .count(),
            3
        );
    }

    /// The pending walk appends a class row to its base only when none of the base's rows carries the row's key, whether a label row or a class row appended before it holds that key, and the enumeration reports a refusal as a collision. A row under a new key joins the base.
    #[test]
    fn a_class_row_whose_key_its_base_holds_collides() {
        let mut labels = LabelPool::default();
        let [pea, edge, tea, may, na] =
            ["qsPea", "#EDGE", "qsTea", "qsMay", "#NA"].map(|text| labels.intern(text));
        let pending = PendingRows::new(3);
        let mut log = RowLog::default();
        let mut visit = log.visit([pea, edge, tea], None, &pending);
        log.append(&mut visit.cursor, log_row([tea, may, na], 1), None);
        let first = visit.cursor.first;
        assert!(!log.append_new(first, log_row([tea, may, na], 2), None));
        assert!(log.append_new(first, log_row([tea, pea, na], 2), None));
        assert!(!log.append_new(first, log_row([tea, pea, na], 2), None));
        assert!(log.append_new(first, log_row([tea, pea, may], 2), None));
        assert_eq!(log.drain(&labels.ranks()).len(), 3);
    }

    /// The drain decodes each input's chain in input-rank order and sorts each input's rows as one slice, which is the product's whole key order. Here the rows go to four bases of two inputs in turn and then to one base past a chunk's capacity, under labels interned in an order unlike their texts', and the drain returns every appended row in the order of its key's texts.
    #[test]
    fn the_drain_returns_every_row_in_key_order() {
        let mut labels = LabelPool::default();
        let slots: Vec<Label> = (0..20)
            .rev()
            .map(|slot| labels.intern(&format!("s{slot:02}")))
            .collect();
        let [tea, pea, space, edge] =
            ["qsTea", "qsPea", "space", "#EDGE"].map(|text| labels.intern(text));
        let bases: [BaseKey; 4] = [
            [tea, space, slots[0]],
            [tea, edge, slots[0]],
            [pea, space, slots[1]],
            [pea, edge, slots[1]],
        ];
        let lefts = [None, Some(SettledId::at(9)), None, Some(SettledId::at(8))];
        let pending = PendingRows::new(3);
        let mut log = RowLog::default();
        let mut visits: Vec<Visit> = bases
            .iter()
            .zip(lefts)
            .map(|(base, left)| log.visit(*base, left, &pending))
            .collect();
        let mut expected: Vec<TransitionRow> = Vec::new();
        for step in 0..ROWS_PER_CHUNK + 400 {
            let which = if step < 400 { step % 4 } else { 1 };
            let [right2, right3, right4] = [
                slots[step / 400 % 20],
                slots[step / 20 % 20],
                slots[step % 20],
            ];
            let settled = SettledId::at(step % 7);
            let provenance = NotesId::at(step % 3);
            let flags = Flags::new(i64::from(step % 2 == 1), step % 5 == 0);
            log.append(
                &mut visits[which].cursor,
                LogRow::new([right2, right3, right4], settled, provenance, flags),
                lefts[which],
            );
            let [input_glyph, left, right1] = bases[which];
            expected.push(TransitionRow {
                input_glyph,
                left,
                right1,
                right2,
                right3,
                right4,
                settled,
                left_settled: lefts[which],
                provenance,
                prospect: flags.prospect(),
                joint: flags.joint(),
            });
        }
        assert!(log.chains.iter().any(|chain| chain.chunks.len() > 1));
        expected.sort_by(|one, other| one.key(&labels).cmp(&other.key(&labels)));
        assert_eq!(log.drain(&labels.ranks()), expected);
    }

    /// One capacity case: the text its panic carries, and the narrowing that panics.
    type CapacityCase = (&'static str, Box<dyn Fn()>);

    /// Each field the row log, the pending rows and the worklist narrow panics past its capacity with a message naming the field, instead of wrapping to a low id, as `TraceSettledId` panics past its range (#266).
    #[test]
    fn a_narrowed_field_past_its_capacity_panics_naming_the_field() {
        let index = deep_alphabet();
        let pea = fixtures::sym(&index, "qsPea");
        let fits = Label(0);
        let past = Label(65_535);
        let settled = SettledId::at(0);
        let notes = NotesId::at(0);
        let flags = Flags::new(0, false);
        let four_bytes = u32::MAX as usize;
        let letter = move |ordinal: u16| {
            RightToken::Letter(
                pea,
                Ordinal::new(ordinal).expect("an ordinal is one or more"),
            )
        };
        let cases: Vec<CapacityCase> = vec![
            (
                "a row's right2 label 65535",
                Box::new(move || {
                    let _ = LogRow::new([past, fits, fits], settled, notes, flags);
                }),
            ),
            (
                "a row's right3 label 65535",
                Box::new(move || {
                    let _ = LogRow::new([fits, past, fits], settled, notes, flags);
                }),
            ),
            (
                "a row's right4 label 65535",
                Box::new(move || {
                    let _ = LogRow::new([fits, fits, past], settled, notes, flags);
                }),
            ),
            (
                "a row's settled record 65535",
                Box::new(move || {
                    let _ = LogRow::new([fits; 3], SettledId::at(65_535), notes, flags);
                }),
            ),
            (
                "a row's provenance 65535",
                Box::new(move || {
                    let _ = LogRow::new([fits; 3], settled, NotesId::at(65_535), flags);
                }),
            ),
            (
                "a fragment's input label 65535",
                Box::new(move || {
                    let _ = Fragment::new([past, fits, fits], None, 0);
                }),
            ),
            (
                "a fragment's left label 65535",
                Box::new(move || {
                    let _ = Fragment::new([fits, past, fits], None, 0);
                }),
            ),
            (
                "a fragment's right1 label 65535",
                Box::new(move || {
                    let _ = Fragment::new([fits, fits, past], None, 0);
                }),
            ),
            (
                "a fragment's left_settled record 65535",
                Box::new(move || {
                    let _ = Fragment::new([fits; 3], Some(SettledId::at(65_535)), 0);
                }),
            ),
            (
                "a fragment's chain position 4294967296",
                Box::new(move || {
                    let _ = Fragment::new([fits; 3], None, four_bytes + 1);
                }),
            ),
            (
                "a pending row's right1 token names rune ordinal 65531",
                Box::new(move || {
                    let _ = TokenCode::of(letter(65_531), "a pending row's right1 token");
                }),
            ),
            (
                "left state id 65535",
                Box::new(|| {
                    let _ = LeftId::at(65_535);
                }),
            ),
            (
                "fragment id 4294967295",
                Box::new(move || {
                    let _ = FragmentId::at(four_bytes);
                }),
            ),
            (
                "pending row id 4294967295",
                Box::new(move || {
                    let _ = PendingId::at(four_bytes);
                }),
            ),
            (
                "member list id 4294967295",
                Box::new(move || {
                    let _ = ListId::at(four_bytes);
                }),
            ),
            (
                "worklist item id 4294967295",
                Box::new(move || {
                    let _ = ItemId::at(four_bytes);
                }),
            ),
            (
                "a prospect is a junction count, zero or one",
                Box::new(|| {
                    let _ = Flags::new(2, false);
                }),
            ),
            (
                "admitted3 holds modeled runes' ordinals, and 65 is past the 64",
                Box::new(move || {
                    let mut rows = PendingRows::new(64);
                    let slot = (TokenCode::of(EDGE, "right2"), ListId::at(0), None);
                    let row = rows.push(pending_row(FragmentId::at(0), None, slot, None), &[]);
                    rows.admit(row, &[letter(65)]);
                }),
            ),
        ];
        for (field, case) in cases {
            let payload =
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(case)).expect_err(field);
            let message = payload
                .downcast_ref::<String>()
                .map(String::as_str)
                .or_else(|| payload.downcast_ref::<&str>().copied())
                .unwrap_or_default();
            assert!(
                message.contains(field),
                "{message:?} does not name {field:?}"
            );
        }
    }

    /// Past sixty-four runes a pending row's `admitted3` takes words of its own beyond the one the row holds: an item ORs each ordinal into whichever word holds it, in place, a second row's words stay its own, and each set reads back every ordinal it was given, ascending, with a boundary member left out.
    #[test]
    fn admitted3_widens_past_sixty_four_runes() {
        let index = deep_alphabet();
        let pea = fixtures::sym(&index, "qsPea");
        let letter = |ordinal: u16| {
            RightToken::Letter(
                pea,
                Ordinal::new(ordinal).expect("an ordinal is one or more"),
            )
        };
        let slot: PendingSlot = (TokenCode::of(EDGE, "right2"), ListId::at(0), None);
        let mut rows = PendingRows::new(130);
        let first = rows.push(
            pending_row(FragmentId::at(0), None, slot, None),
            &[letter(70), letter(1)],
        );
        let second = rows.push(
            pending_row(FragmentId::at(0), None, slot, None),
            &[letter(130), EDGE],
        );
        rows.admit(first, &[letter(65), letter(64), letter(70)]);
        let ordinals = |id| {
            rows.admitted(id)
                .iter()
                .map(|ordinal| ordinal.get())
                .collect::<Vec<u16>>()
        };
        assert_eq!(ordinals(first), [1, 64, 65, 70]);
        assert_eq!(ordinals(second), [130]);
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

    /// A right condition testing each family in turn, one `then:` hop per name — the shape the deep-slot rune sets count hops on.
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

    /// Every fixture below is read in the pinned mode set: `simulated_prospect` and `follower_prefer_slots` both off, so the mode set is not a deep one, the rune sets are the chain sets, and class grain cannot arise whatever `deep_classes` says.
    const PINNED: EnumerationModes = EnumerationModes {
        simulated_prospect: false,
        follower_prefer_slots: false,
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

    /// The three-letter alphabet the deep slots and the slot restrictions are read against.
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
            "the locked copy is the entry-bearing input's label alone: {locked:?}"
        );
        assert!(!locked.contains(&"qsPea"));
        // The lock changes only the row's label: the trace settles the raw letter, whose cell is the one the outcome names.
        assert!(
            product
                .transitions
                .iter()
                .filter(|row| &**product.labels.text(row.input_glyph) == "qsPea.noentry")
                .all(|row| product.outcome(row).starts_with("qsPea.half")),
            "the locked copy still settles as qsPea"
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
    fn the_deep_slots_split_only_the_windows_the_rune_set_and_both_filters_admit() {
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
            "the input in the rune set carries a third slot and nothing else does"
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
    fn a_depth_four_left_restricts_the_second_slot_of_the_window_after_its_successor() {
        let index = deep_alphabet();
        let product = product(&index);
        // The left that only the fourth slot's one live token reaches: qsPea commits the x-height junction there and nowhere else, so qsTea's entry at that height identifies that continuation.
        assert_eq!(
            slots_at(&product, "qsTea", "qsPea.full.ex-y5")
                .iter()
                .map(|slots| slots.join(" "))
                .collect::<Vec<String>>(),
            ["qsMay qsPea #NA #NA"],
            "the successor's own second slot is restricted to the third lookahead that was enumerated behind it"
        );
        // The restriction this window could not enumerate (qsTea is not deep, so it has no third slot to use it on) is forwarded to its own successor's second slot, which is the raw token one past that window.
        assert_eq!(
            slots_at(&product, "qsMay", "qsTea.plain.en-y5.ex-y0")
                .iter()
                .map(|slots| slots.join(" "))
                .collect::<Vec<String>>(),
            ["qsPea qsTea #NA #NA"],
            "without the forward this left would carry every second-slot option, and the extra windows are ones no text can reach"
        );
        // The sibling left, for contrast: reached by many unrestricted items, it carries the whole option list behind the same right1.
        let unrestricted: Vec<String> = slots_at(&product, "qsMay", "qsTea.plain.en-y0.ex-y0")
            .iter()
            .filter(|slots| slots[0] == "qsPea")
            .map(|slots| slots[1].clone())
            .collect();
        assert_eq!(
            unrestricted,
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
                    settled: SettledId::at(0),
                    left_settled: None,
                    provenance: NotesId::at(0),
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

    /// The cross-check re-traces the last admitted member, or the first one when the last is the representative the row was built from, since a class of two would otherwise re-trace the window it is checked against.
    #[test]
    fn the_cross_check_member_is_the_last_admitted_unless_that_is_the_representative() {
        let index = deep_alphabet();
        let [pea, tea, may] =
            ["qsPea", "qsTea", "qsMay"].map(|name| fixtures::letter(&index, name));
        assert_eq!(cross_check_member(&[pea, tea, may], pea), may);
        assert_eq!(cross_check_member(&[pea, tea, may], may), pea);
        assert_eq!(cross_check_member(&[pea, tea], tea), pea);
        assert_eq!(cross_check_member(&[pea, tea], pea), tea);
    }

    /// Runs the partition check over a hand-built product, with each live context's fiber partition given instead of derived.
    ///
    /// In a real build every live context has already been derived when the check runs, so the cache is filled and the deriver is never called. Giving the partition supplies what the enumeration would have cached, and it lets these checks run in the pinned mode set, where there is no liveness probe and the filters use their chain branch alone.
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
                follower_prefer_slots: false,
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
        let error = checked(&index, &product, &[]).expect_err("the map is empty");
        assert!(
            error.ends_with(": right3 token #Cfeedfacefeed is not in the class map"),
            "{error}"
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
        let error = checked(&index, &product, &[(LIVE, &[&["qsPea", "qsTea"]])])
            .expect_err("the chain's last hop reads qsPea alone");
        assert!(
            error.ends_with(
                ": members disagree on the fourth_slot_matters verdict: [\"qsPea\", \"qsTea\"]"
            ),
            "{error}"
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
        let error = checked(&index, &product, &[(LIVE, &[&["qsPea", "qsTea"]])]).expect_err(
            "the qsPeaMay formation pair narrows one member's list and not the other's",
        );
        assert!(
            error.ends_with(": members induce different computed r4 option lists: qsPea vs qsTea"),
            "{error}"
        );
    }

    /// The r3 member sets at one base are disjoint, and a member's claim lasts for its base only: the same member under another token at the next base is no conflict.
    #[test]
    fn two_r3_tokens_sharing_a_member_stop_the_build_at_one_base_only() {
        let index = deep_alphabet();
        let token = class_of(&["qsMay", "qsTea"]);
        let partition: &[&[&str]] = &[&["qsMay", "qsTea"]];
        let one_base = hand_product(
            vec![
                deep_row(["qsPea", "#EDGE", "qsTea", "qsMay", &token, "#NA"]),
                deep_row(["qsPea", "#EDGE", "qsTea", "qsMay", "qsTea", "#NA"]),
            ],
            &[(&token, &["qsMay", "qsTea"])],
        );
        let error = checked(&index, &one_base, &[(LIVE, partition)])
            .expect_err("qsTea is claimed by two tokens at one base");
        assert!(
            error.ends_with(&format!(
                ": r3 member qsTea belongs to two tokens at one base: {token} and qsTea"
            )),
            "{error}"
        );
        let two_bases = hand_product(
            vec![
                deep_row(["qsPea", "#EDGE", "qsTea", "qsMay", &token, "#NA"]),
                deep_row(["qsPea", "space", "qsTea", "qsMay", "qsTea", "#NA"]),
            ],
            &[(&token, &["qsMay", "qsTea"])],
        );
        assert_eq!(checked(&index, &two_bases, &[(LIVE, partition)]), Ok(()));
    }

    /// The claims are cleared at each new base, so a base whose rows come back after another base's would be checked against cleared claims. Here the third row repeats the first row's base with a claim that conflicts with the first row's, which a check reading on past the base would miss; the check stops at the base instead.
    #[test]
    fn a_base_out_of_key_order_stops_the_partition_check() {
        let index = deep_alphabet();
        let token = class_of(&["qsMay", "qsTea"]);
        let product = hand_product(
            vec![
                deep_row(["qsPea", "#EDGE", "qsTea", "qsMay", &token, "#NA"]),
                deep_row(["qsPea", "space", "qsTea", "qsMay", "qsTea", "#NA"]),
                deep_row(["qsPea", "#EDGE", "qsTea", "qsMay", "qsTea", "#NA"]),
            ],
            &[(&token, &["qsMay", "qsTea"])],
        );
        assert_eq!(
            checked(&index, &product, &[(LIVE, &[&["qsMay", "qsTea"]])]),
            Err(
                r##"the product's bases are not in key order: ["qsPea", "#EDGE", "qsTea", "qsMay"] follows ["qsPea", "space", "qsTea", "qsMay"]"##
                    .to_owned()
            )
        );
    }

    /// The contract seeds in reverse order, so the seed production pops first is popped last here.
    fn reversed_seeds(options: &WindowOptions<'_>) -> Vec<Seed> {
        let mut seeds = contract_seeds(options);
        seeds.reverse();
        seeds
    }

    #[test]
    fn a_permuted_seed_order_reaches_the_same_product_in_the_pinned_mode_set() {
        let index = deep_alphabet();
        let contract = enumerate_from_seeds(
            &index,
            &[],
            PINNED,
            contract_seeds,
            None,
            MemoAccess::default(),
        )
        .expect("the fixpoint closes")
        .product;
        let reversed = enumerate_from_seeds(
            &index,
            &[],
            PINNED,
            reversed_seeds,
            None,
            MemoAccess::default(),
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
        let contract = enumerate_from_seeds(
            &index,
            &[],
            modes,
            contract_seeds,
            None,
            MemoAccess::default(),
        )
        .expect("the fixpoint closes")
        .product;
        let reversed = enumerate_from_seeds(
            &index,
            &[],
            modes,
            reversed_seeds,
            None,
            MemoAccess::default(),
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

    /// An `ss03` enumeration that reads `default`'s finished memo for every window naming no unlocking rune of `ss03` produces the from-scratch product byte for byte (rows, classes, cells and fired provenance, which is what the stream contains) while answering windows from the shared memo, and liveness probes from the verdicts `default` published there for `ss03`'s unlocking runes. The exclusion is required: the same shared memo read without one gives `ss03` the wrong results for `qsMay`'s windows, which is what makes the equality assertion able to fail.
    #[test]
    fn a_configuration_sharing_defaults_memo_reaches_its_from_scratch_product() {
        let index = fixtures::mini();
        let ss03 = fixtures::sym(&index, "ss03");
        let modes = EnumerationModes::default();
        let memo = enumerate_from_seeds(
            &index,
            &[],
            modes,
            contract_seeds,
            None,
            MemoAccess {
                shared_memos: Vec::new(),
                keep_memo: true,
                publish_verdicts: vec![unlocking_runes(&index, &[ss03])],
                ..MemoAccess::default()
            },
        )
        .expect("default closes")
        .memo
        .expect("a kept memo comes back");
        assert!(!memo.is_empty());
        assert!(memo.verdicts() > 0, "default published its probes");
        let scratch = enumerate_from_seeds(
            &index,
            &[ss03],
            modes,
            contract_seeds,
            None,
            MemoAccess::default(),
        )
        .expect("ss03 closes from scratch")
        .product;
        let shared_with = |excluded: Exclusion| {
            let mut stats: Vec<String> = Vec::new();
            let product = enumerate_from_seeds(
                &index,
                &[ss03],
                modes,
                contract_seeds,
                Some(&mut stats),
                MemoAccess {
                    shared_memos: vec![SharedMemo {
                        memo: Arc::clone(&memo),
                        excluded,
                    }],
                    keep_memo: false,
                    publish_verdicts: Vec::new(),
                    ..MemoAccess::default()
                },
            )
            .expect("ss03 closes over a shared memo")
            .product;
            let hits = stats
                .iter()
                .find_map(|line| line.strip_prefix("[c] ss03 shared_memo_hits count="))
                .expect("the cache stats report the shared memo hits")
                .parse::<u64>()
                .expect("as a count");
            assert!(stats.contains(&format!("[c] ss03 shared_memo_hits id=0 count={hits}")));
            let served = stats
                .iter()
                .find_map(|line| line.strip_prefix("[c] ss03 shared_verdicts hits="))
                .and_then(|rest| rest.split(' ').next())
                .expect("the cache stats report the shared verdicts")
                .parse::<u64>()
                .expect("as a count");
            (product, hits, served)
        };
        let (over_shared, hits, served) =
            shared_with(Exclusion::of(&index, unlocking_runes(&index, &[ss03])));
        assert!(hits > 0, "the shared memo answered windows");
        assert!(
            served > 0,
            "default's verdicts answered probes, each rerunning only the asks that name qsMay"
        );
        assert_eq!(
            emit_transitions(&index, &scratch),
            emit_transitions(&index, &over_shared)
        );
        let (unfiltered, _, _) = shared_with(Exclusion::none());
        assert_ne!(
            emit_transitions(&index, &scratch),
            emit_transitions(&index, &unfiltered),
            "without the exclusion the shared memo answers qsMay's windows as default settles them"
        );
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
        let error = enumerate_transitions(&index, &[], PINNED).expect_err("the labels collide");
        assert!(
            error.starts_with(
                "window [\"qsTea\", \"qsPea.half.ex-y0\", \"qsMay\", \"qsPea\", \"#NA\", \"#NA\"] reached from two left states sharing one label: "
            ),
            "{error}"
        );
        assert!(
            error.contains("qsPea.half.ex-y0 (junction floor, extension 0)")
                && error.contains("qsPea.half.ex-y0 (junction baseline, extension 0)"),
            "{error}"
        );
    }
}
