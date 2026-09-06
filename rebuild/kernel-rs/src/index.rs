//! The engine-facing view over a parsed [`Spec`]: every lookup settlement performs, resolved once, so that the kernel never scans a [`Table`] to find a rune, a stance, or a surface row.
//!
//! The model is deliberately lookup-free — `model.rs` says so, and says the sub-issue that needs indexed access should build the index it needs. This is that index. It exists because Python gets these lookups for free from `dict`: `spec.runes[name]`, `stance.surface.entries.get(height)`, `spec.registry.heights[height]` are all constant-time reads of a mapping that also remembers its insertion order, and the Rust model splits those two properties apart — the [`Table`] keeps the order and this module adds the lookup. Nothing here changes a semantic; every accessor answers exactly what the corresponding Python subscript answers, including which answer is "absent".
//!
//! The index also keeps the read journal (issue #184): a thread-local log of every rune whose resolved content and every predicate class whose membership an accessor here handed out while the engine had a capture open. A memo entry across configurations and across builds is invalidated by what its evaluation *read*, and the accessors are the one place every such read passes through, so the journal is kept here rather than at the engine's call sites — a rune's stances, rows, order, strokes, entry-bearing flag and groups all journal the rune, a predicate class's membership journals the class, and the alphabet, the registry's heights and tokens, and a symbol's text journal nothing, being the whole-store structure a memo is stamped with instead. The log is per thread because the index is shared across a fan-out's threads and holds nothing mutable of its own; an engine is confined to one thread, and it is the engine's captures that arm and drain the log ([`crate::engine`]).
//!
//! Two further things live here because they are pure functions of the spec that a per-engine cache would only recompute, and a per-spec answer is the same answer. The stance order index — `policy.order` extended by declaration order, with `order.index(...)`'s exact arithmetic including the seats that names not naming a stance still occupy — is resolved once at build time rather than per engine, and so are `is_entry_bearing` and the per-rune entry-stroke set, both feature-blind reads of the surface. `settle.is_entry_bearing` answers the first of those on the Python side too, for the callers that ask it before a window reaches this crate.
//!
//! The index takes ownership of the [`Spec`] rather than borrowing it, which buys two things. There are no lifetimes to thread through the engine, the guard, and the caches; and the interner is reachable mutably at build time, so [`Vocab`] and the withdrawn-state symbols can be interned into the spec's own pool instead of living in a second one. Interning into that pool cannot disturb emission, which walks the tree and never the pool.

use std::cell::{Cell, RefCell};
use std::collections::{BTreeSet, HashMap};

use crate::error::SettleError;
use crate::model::{
    BoundaryToken, ResolvedSpec, Rune, ScriptRegistry, Spec, Stance, SurfaceRow, Sym, Table,
};
use crate::types::{Vocab, WITHDRAWN_SUFFIX};

/// One stance's identity within a spec: the rune's declaration seat and the stance's seat inside it. This is what the exit-sources and pairing-set caches key on, in place of a stance's address: a seat pair is `Copy`, hashes on two integers, and cannot be recycled the way an address can, which is why an address-keyed cache needs identity re-checks and a small cap and this needs neither.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct StanceId {
    pub rune: u32,
    pub stance: u32,
}

impl StanceId {
    /// The identity of the stance at `stance` inside the rune at `rune`. Both seats are declaration order, which is what the caller has in hand when it iterates `rune.stances` — the intended idiom, since iterating the [`Table`] is the one traversal the index does not replace.
    pub fn new(rune: u32, stance: u32) -> Self {
        Self { rune, stance }
    }
}

struct StanceIndex {
    entries: HashMap<Sym, usize>,
    exits: HashMap<Sym, usize>,
}

struct RuneIndex {
    stances: HashMap<Sym, u32>,
    rows: Vec<StanceIndex>,
    order_index: Vec<usize>,
    groups: HashMap<Sym, BTreeSet<Sym>>,
    entry_strokes: BTreeSet<Sym>,
    entry_bearing: bool,
}

/// One thing a settlement's evaluation read of the spec, at the grain a memo across builds is invalidated at: a rune's resolved content, or a predicate class's membership. The module doc says which accessors journal which.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Read {
    Rune(Sym),
    Class(Sym),
}

thread_local! {
    // The flag sits apart from the log so the accessors' common case — no capture open — is one thread-local `Cell` read and a branch, with the `RefCell` borrow paid only while a capture is journaling.
    static ARMED: Cell<bool> = const { Cell::new(false) };
    static LOG: RefCell<Vec<Read>> = const { RefCell::new(Vec::new()) };
}

/// Switch this thread's read journal on or off. Off, the accessors cost a flag test and log nothing.
pub(crate) fn journal_arm(armed: bool) {
    ARMED.with(|flag| flag.set(armed));
}

/// How many reads the journal holds, which is what a capture records on opening so it can take its own slice on closing.
pub(crate) fn journal_len() -> usize {
    LOG.with(|log| log.borrow().len())
}

/// The reads journaled since `start`, sorted with repeats collapsed — the set a capture's evaluation read. The log keeps them, since an enclosing capture reads the same slice.
pub(crate) fn journal_since(start: usize) -> Box<[Read]> {
    LOG.with(|log| {
        let mut reads: Vec<Read> = log.borrow()[start..].to_vec();
        reads.sort_unstable();
        reads.dedup();
        reads.into_boxed_slice()
    })
}

/// A memo hit's recorded reads replayed into the journal, so an open capture's own slice carries what the hit's evaluation read; a no-op while the journal is off.
pub(crate) fn journal_extend(reads: &[Read]) {
    if ARMED.with(Cell::get) {
        LOG.with(|log| log.borrow_mut().extend_from_slice(reads));
    }
}

/// Empty the journal, which the engine does when its outermost capture closes or is abandoned.
pub(crate) fn journal_clear() {
    LOG.with(|log| log.borrow_mut().clear());
}

fn journal(read: Read) {
    if ARMED.with(Cell::get) {
        LOG.with(|log| log.borrow_mut().push(read));
    }
}

/// The indexed view over one parsed dump. Built once, read everywhere; nothing on it is mutable, so every engine, the guard's whole engine powerset, and the corpus replay share one.
pub struct SpecIndex {
    spec: Spec,
    ids: HashMap<String, Sym>,
    vocab: Vocab,
    runes: HashMap<Sym, u32>,
    rune_index: Vec<RuneIndex>,
    heights: HashMap<Sym, i64>,
    withdrawn: HashMap<Sym, Sym>,
    families: BTreeSet<Sym>,
    predicate_classes: HashMap<Sym, BTreeSet<Sym>>,
    group_owner: HashMap<Sym, u32>,
    boundary_tokens: HashMap<Sym, u32>,
    empty: BTreeSet<Sym>,
}

impl SpecIndex {
    /// Index one parsed dump, interning the closed vocabulary and the withdrawn exit states into its pool on the way.
    pub fn new(mut spec: Spec) -> Self {
        let vocab = Vocab::build(|text| spec.symbols.intern(text));
        let declared_heights: Vec<Sym> = spec
            .root
            .registry
            .heights
            .iter()
            .map(|(height, _)| *height)
            .collect();
        let mut withdrawn = HashMap::with_capacity(declared_heights.len());
        for height in declared_heights {
            let composed = format!("{}{WITHDRAWN_SUFFIX}", spec.symbols.resolve(height));
            withdrawn.insert(height, spec.symbols.intern(&composed));
        }
        let mut ids = HashMap::with_capacity(spec.symbols.len());
        for (symbol, text) in spec.symbols.iter() {
            ids.insert(text.to_owned(), symbol);
        }
        let mut runes = HashMap::with_capacity(spec.root.runes.len());
        let mut rune_index = Vec::with_capacity(spec.root.runes.len());
        let mut group_owner: HashMap<Sym, u32> = HashMap::new();
        for (seat, (name, rune)) in spec.root.runes.iter().enumerate() {
            let seat =
                u32::try_from(seat).expect("a spec dump models far fewer than four billion runes");
            runes.insert(*name, seat);
            let mut groups = HashMap::with_capacity(rune.policy.groups.len());
            for (group, members) in rune.policy.groups.iter() {
                groups.insert(*group, members.iter().copied().collect());
                group_owner.entry(*group).or_insert(seat);
            }
            rune_index.push(RuneIndex {
                stances: rune
                    .stances
                    .iter()
                    .enumerate()
                    .map(|(stance_seat, (stance_name, _))| {
                        (
                            *stance_name,
                            u32::try_from(stance_seat)
                                .expect("a rune declares far fewer than four billion stances"),
                        )
                    })
                    .collect(),
                rows: rune
                    .stances
                    .iter()
                    .map(|(_, stance)| row_index(stance))
                    .collect(),
                order_index: order_indices(rune),
                groups,
                entry_strokes: entry_strokes_of(rune),
                entry_bearing: entry_bearing_of(rune),
            });
        }
        let registry = &spec.root.registry;
        Self {
            ids,
            vocab,
            runes,
            rune_index,
            heights: registry
                .heights
                .iter()
                .map(|(height, y)| (*height, *y))
                .collect(),
            withdrawn,
            families: registry
                .families
                .iter()
                .map(|(family, _)| *family)
                .collect(),
            predicate_classes: registry
                .predicate_classes
                .iter()
                .map(|(name, members)| (*name, members.iter().copied().collect()))
                .collect(),
            group_owner,
            boundary_tokens: registry
                .boundary_tokens
                .iter()
                .enumerate()
                .map(|(seat, (name, _))| {
                    (
                        *name,
                        u32::try_from(seat)
                            .expect("a registry declares far fewer than four billion tokens"),
                    )
                })
                .collect(),
            empty: BTreeSet::new(),
            spec,
        }
    }

    /// The dump this index was built over, for the emitter and for anything that wants the model directly.
    pub fn spec(&self) -> &Spec {
        &self.spec
    }

    /// The resolved spec — `spec_load`'s whole product, the argument every Python function here takes.
    pub fn root(&self) -> &ResolvedSpec {
        &self.spec.root
    }

    /// The script-wide vocabulary the runes are read against.
    pub fn registry(&self) -> &ScriptRegistry {
        &self.spec.root.registry
    }

    /// The closed vocabulary interned against this spec's pool.
    pub fn vocab(&self) -> &Vocab {
        &self.vocab
    }

    /// The text a symbol stands for.
    pub fn resolve(&self, symbol: Sym) -> &str {
        self.spec.symbols.resolve(symbol)
    }

    /// The symbol some text interns to, or `None` when this spec never mentioned it. A name absent from the pool cannot equal any authored value, so `None` is the honest answer to "does this spec know this name" as well as to "what is its symbol".
    pub fn sym_of(&self, text: &str) -> Option<Sym> {
        self.ids.get(text).copied()
    }

    /// The modeled runes in declaration order.
    pub fn runes(&self) -> &Table<Rune> {
        &self.spec.root.runes
    }

    /// How many runes this spec models.
    pub fn rune_count(&self) -> usize {
        self.rune_index.len()
    }

    /// A rune's declaration seat, or `None` when the name is not modeled.
    pub fn rune_seat(&self, name: Sym) -> Option<u32> {
        self.runes.get(&name).copied()
    }

    /// One rune by name — `spec.runes.get(name)`.
    pub fn rune(&self, name: Sym) -> Option<&Rune> {
        self.rune_seat(name).map(|seat| self.rune_at(seat))
    }

    /// One rune by declaration seat.
    pub fn rune_at(&self, seat: u32) -> &Rune {
        let (name, rune) = self.rune_entry(seat);
        journal(Read::Rune(*name));
        rune
    }

    /// One rune's name by declaration seat.
    pub fn rune_name_at(&self, seat: u32) -> Sym {
        self.rune_entry(seat).0
    }

    /// Whether this spec models the rune — `name in spec.runes` as `model.ResolvedSpec` spells it, the check that guards every letter-token read.
    pub fn is_modeled(&self, name: Sym) -> bool {
        self.runes.contains_key(&name)
    }

    /// One stance's identity, or `None` when the rune or the stance is absent.
    pub fn stance_id(&self, rune: Sym, stance: Sym) -> Option<StanceId> {
        let seat = self.rune_seat(rune)?;
        journal(Read::Rune(rune));
        let stance_seat = *self.rune_index[seat as usize].stances.get(&stance)?;
        Some(StanceId::new(seat, stance_seat))
    }

    /// One stance by identity.
    pub fn stance(&self, id: StanceId) -> &Stance {
        journal(Read::Rune(self.rune_name_at(id.rune)));
        &self.stance_entry(id).1
    }

    /// One stance's name by identity.
    pub fn stance_name(&self, id: StanceId) -> Sym {
        journal(Read::Rune(self.rune_name_at(id.rune)));
        self.stance_entry(id).0
    }

    /// How many stances a rune declares.
    pub fn stance_count(&self, seat: u32) -> usize {
        journal(Read::Rune(self.rune_name_at(seat)));
        self.rune_index[seat as usize].order_index.len()
    }

    /// The stance's rank in its rune's declared order — the third stage of the ranking, resolved once when the index is built rather than memoized on first ask.
    ///
    /// The arithmetic is load-bearing: the order list is `policy.order` when it is non-empty and declaration order otherwise, then every stance the list omits is appended in declaration order, and each stance's index is its first position in that list. A name in `policy.order` that is not a stance still occupies its seat, so the stances after it rank one lower than a naive enumeration would give them.
    pub fn order_index(&self, id: StanceId) -> usize {
        journal(Read::Rune(self.rune_name_at(id.rune)));
        self.rune_index[id.rune as usize].order_index[id.stance as usize]
    }

    /// The stance a rune defaults to, `model.Rune.default_stance`: the first name in `policy.order`, or the first declared stance when there is no order. The first branch echoes `policy.order[0]` whether or not it names a stance, exactly as the Python property does.
    pub fn default_stance(&self, rune: Sym) -> Option<Sym> {
        let rune = self.rune(rune)?;
        rune.policy
            .order
            .first()
            .copied()
            .or_else(|| rune.stances.iter().next().map(|(name, _)| *name))
    }

    /// One entry row with its declaration seat — `stance.surface.entries.get(height)`, plus the index the enumeration numbers rows by.
    pub fn entry_row(&self, id: StanceId, height: Sym) -> Option<(usize, &SurfaceRow)> {
        let seat = *self.rows(id).entries.get(&height)?;
        Some((seat, row_at(&self.stance(id).surface.entries, seat)))
    }

    /// One exit row with its declaration seat — `stance.surface.exits.get(height)`, plus the exit index the structural floor's final tiebreak reads.
    pub fn exit_row(&self, id: StanceId, height: Sym) -> Option<(usize, &SurfaceRow)> {
        let seat = *self.rows(id).exits.get(&height)?;
        Some((seat, row_at(&self.stance(id).surface.exits, seat)))
    }

    /// Whether the stance declares an exit at this height — `height in stance.surface.exits` as `model.Surface` spells it, the shadowing test an unlock exit has to pass.
    pub fn declares_exit(&self, id: StanceId, height: Sym) -> bool {
        journal(Read::Rune(self.rune_name_at(id.rune)));
        self.rows(id).exits.contains_key(&height)
    }

    /// A height's glyph-space y, `registry.y_of` — `None` for a height the registry does not declare, where Python raises `KeyError`.
    pub fn y_of(&self, height: Sym) -> Option<i64> {
        self.heights.get(&height).copied()
    }

    /// The state symbol a `cells:` row names this height's withdrawn exit with — the height's own text plus [`WITHDRAWN_SUFFIX`]. Every registry height has one interned at build time; a height from outside the registry falls back to a pool lookup, and `None` then means no authored row can be naming it.
    pub fn withdrawn_state(&self, height: Sym) -> Option<Sym> {
        if let Some(state) = self.withdrawn.get(&height) {
            return Some(*state);
        }
        self.sym_of(&format!("{}{WITHDRAWN_SUFFIX}", self.resolve(height)))
    }

    /// Every family the registry knows about, modeled or not — the set an `except:` carve subtracts from when the condition it carves has no family axis of its own.
    pub fn families(&self) -> &BTreeSet<Sym> {
        &self.families
    }

    /// One boundary token's registry record.
    pub fn boundary_token(&self, name: Sym) -> Option<&BoundaryToken> {
        let seat = *self.boundary_tokens.get(&name)?;
        self.registry()
            .boundary_tokens
            .iter()
            .nth(seat as usize)
            .map(|(_, token)| token)
    }

    /// One registry predicate class's resolved membership.
    pub fn predicate_class(&self, name: Sym) -> Option<&BTreeSet<Sym>> {
        let members = self.predicate_classes.get(&name)?;
        journal(Read::Class(name));
        Some(members)
    }

    /// One rune's local group membership.
    pub fn rune_group(&self, rune: Sym, name: Sym) -> Option<&BTreeSet<Sym>> {
        let seat = self.rune_seat(rune)?;
        journal(Read::Rune(rune));
        self.rune_index[seat as usize].groups.get(&name)
    }

    /// Resolve a `class:` reference to family names: registry predicate classes first, then the owning rune's local groups, then any rune's groups in rune declaration order — `spec_load` lints cross-rune duplicates, so the last step is unambiguous on a linted spec.
    ///
    /// An unresolvable name is a spec defect rather than a settlement outcome: `spec_load` refuses a dangling class reference, so this cannot fire on a spec the pipeline built. It surfaces as [`SettleError::Plain`] because that is the only shape the kernel's callers already handle.
    pub fn class_members(
        &self,
        name: Sym,
        owner: Option<Sym>,
    ) -> Result<&BTreeSet<Sym>, SettleError> {
        if let Some(members) = self.predicate_classes.get(&name) {
            journal(Read::Class(name));
            return Ok(members);
        }
        if let Some(owner) = owner
            && let Some(seat) = self.rune_seat(owner)
            && let Some(members) = self.rune_index[seat as usize].groups.get(&name)
        {
            journal(Read::Rune(owner));
            return Ok(members);
        }
        if let Some(seat) = self.group_owner.get(&name) {
            journal(Read::Rune(self.rune_name_at(*seat)));
            return Ok(&self.rune_index[*seat as usize].groups[&name]);
        }
        Err(SettleError::Plain(format!(
            "unknown class or group: '{}'",
            self.resolve(name)
        )))
    }

    /// Every stroke a rune offers on a selectable entry row, across its stances — the set a right-side `stroke:` condition tests membership in. An unmodeled rune has none rather than raising, because a condition may name one.
    pub fn entry_strokes(&self, rune: Sym) -> &BTreeSet<Sym> {
        match self.rune_seat(rune) {
            Some(seat) => {
                journal(Read::Rune(rune));
                &self.rune_index[seat as usize].entry_strokes
            }
            None => &self.empty,
        }
    }

    /// Whether the ZWNJ chokepoint locks this rune, `settle.is_entry_bearing`: some stance offers a selectable declared entry row, or some stance carries an entry unlock. Feature-blind, like the chokepoint itself. An unmodeled rune answers `false`, where Python raises `KeyError`; every call site checks first.
    pub fn is_entry_bearing(&self, rune: Sym) -> bool {
        self.rune_seat(rune).is_some_and(|seat| {
            journal(Read::Rune(rune));
            self.rune_index[seat as usize].entry_bearing
        })
    }

    fn rune_entry(&self, seat: u32) -> &(Sym, Rune) {
        self.spec
            .root
            .runes
            .iter()
            .nth(seat as usize)
            .expect("a rune seat comes from this index and is in range")
    }

    fn stance_entry(&self, id: StanceId) -> &(Sym, Stance) {
        self.rune_at(id.rune)
            .stances
            .iter()
            .nth(id.stance as usize)
            .expect("a stance seat comes from this index and is in range")
    }

    fn rows(&self, id: StanceId) -> &StanceIndex {
        &self.rune_index[id.rune as usize].rows[id.stance as usize]
    }
}

fn row_at(rows: &Table<SurfaceRow>, seat: usize) -> &SurfaceRow {
    rows.iter()
        .nth(seat)
        .map(|(_, row)| row)
        .expect("a row seat comes from this index and is in range")
}

fn row_index(stance: &Stance) -> StanceIndex {
    StanceIndex {
        entries: stance
            .surface
            .entries
            .iter()
            .enumerate()
            .map(|(seat, (height, _))| (*height, seat))
            .collect(),
        exits: stance
            .surface
            .exits
            .iter()
            .enumerate()
            .map(|(seat, (height, _))| (*height, seat))
            .collect(),
    }
}

fn order_indices(rune: &Rune) -> Vec<usize> {
    let mut order: Vec<Sym> = if rune.policy.order.is_empty() {
        rune.stances.iter().map(|(name, _)| *name).collect()
    } else {
        rune.policy.order.clone()
    };
    for (name, _) in rune.stances.iter() {
        if !order.contains(name) {
            order.push(*name);
        }
    }
    rune.stances
        .iter()
        .map(|(name, _)| {
            order
                .iter()
                .position(|seat| seat == name)
                .expect("every stance is either named in the order or appended to it")
        })
        .collect()
}

fn entry_strokes_of(rune: &Rune) -> BTreeSet<Sym> {
    let mut strokes = BTreeSet::new();
    for (_, stance) in rune.stances.iter() {
        for (_, row) in stance.surface.entries.iter() {
            if row.selectable
                && let Some(stroke) = row.stroke
            {
                strokes.insert(stroke);
            }
        }
    }
    strokes
}

fn entry_bearing_of(rune: &Rune) -> bool {
    rune.stances.iter().any(|(_, stance)| {
        stance.surface.entries.iter().any(|(_, row)| row.selectable)
            || stance
                .surface
                .unlocks
                .iter()
                .any(|unlock| unlock.entry.is_some())
    })
}

/// Hand-authored `ams-m1-spec/1` dumps for the settlement modules' tests, and the two specs they share.
///
/// Strict ingest means a test dump has to spell every field `model.py` declares, which a hand-written JSON literal does at ruinous length. The builders here start from each record's all-defaults spelling and take only the fields a test actually cares about, so a condition or a policy record reads as the two or three things that make it interesting. They live in this module rather than a test-only file because `parse::parse_spec` plus [`SpecIndex::new`] is what every settlement test needs a spec through, and the other modules' tests reach them as `crate::index::fixtures`.
///
/// The `fixtures` feature is how the integration tests reach the same dumps: an integration test links the library as any other caller would, where `cfg(test)` does not hold. It is a test scaffold and not part of the crate's surface — nothing but this crate's own `tests/` enables it, and a release build compiles none of it.
#[cfg(any(test, feature = "fixtures"))]
#[doc(hidden)]
pub mod fixtures {
    use super::SpecIndex;
    use crate::model::{PolicyRecord, Sym};
    use crate::parse::parse_spec;

    const EMPTY_WHEN: &str = r#"{"left":null,"right":null,"self_entry":null,"self_exit":null,"word":null,"feature":null}"#;
    const EMPTY_POLICY: &str = r#"{"order":[],"refuse":[],"prefer":[],"extend":[],"contract":[],"resolve":[],"groups":{}}"#;
    const EMPTY_SURFACE: &str = r#"{"entries":{},"exits":{},"pairings":{"never":[],"only":null},"cells":[],"unlocks":[],"require":[]}"#;
    const EMPTY_BITMAP: &str = r#"{"rows":[],"y_offset":0}"#;

    const CONDITION: &[(&str, &str)] = &[
        ("family", "[]"),
        ("klass", "[]"),
        ("stance", "[]"),
        ("joined_at", "null"),
        ("stroke", "null"),
        ("is_token", "null"),
        ("except_", "[]"),
        ("then", "null"),
    ];
    const WHEN: &[(&str, &str)] = &[
        ("left", "null"),
        ("right", "null"),
        ("self_entry", "null"),
        ("self_exit", "null"),
        ("word", "null"),
        ("feature", "null"),
    ];
    const RECORD: &[(&str, &str)] = &[
        ("kind", "\"extend\""),
        ("when", EMPTY_WHEN),
        ("id", "null"),
        ("stance", "null"),
        ("entry", "null"),
        ("exit", "null"),
        ("cell", "null"),
        ("over", "null"),
        ("mode", "null"),
        ("by", "null"),
        ("ok", "null"),
        ("bind", "null"),
        ("trim", "null"),
        ("split", "null"),
        ("against", "null"),
        ("pick", "null"),
        ("migrated", "null"),
        ("why", "null"),
        ("provenance", "null"),
    ];
    const POLICY: &[(&str, &str)] = &[
        ("order", "[]"),
        ("refuse", "[]"),
        ("prefer", "[]"),
        ("extend", "[]"),
        ("contract", "[]"),
        ("resolve", "[]"),
        ("groups", "{}"),
    ];
    const ROW: &[(&str, &str)] = &[
        ("height", "\"baseline\""),
        ("x", "0"),
        ("stroke", "null"),
        ("joined", "null"),
        ("joined_x", "null"),
        ("withdrawal", "null"),
        ("stub", "null"),
        ("scope", "[]"),
        ("selectable", "true"),
        ("ink_y", "null"),
        ("x_off_convention", "false"),
        ("provenance", "null"),
    ];
    const SURFACE: &[(&str, &str)] = &[
        ("entries", "{}"),
        ("exits", "{}"),
        ("pairings", r#"{"never":[],"only":null}"#),
        ("cells", "[]"),
        ("unlocks", "[]"),
        ("require", "[]"),
    ];
    const STANCE: &[(&str, &str)] = &[
        ("name", "\"half\""),
        ("motion", "\"flat\""),
        ("traits", "[]"),
        ("bitmap", EMPTY_BITMAP),
        ("bitmaps", "{}"),
        ("surface", EMPTY_SURFACE),
    ];
    const RUNE: &[(&str, &str)] = &[
        ("name", "\"qsX\""),
        ("codepoint", "null"),
        ("sequence", "null"),
        ("ductus", "{}"),
        ("notes", "null"),
        ("mono", "null"),
        ("stances", "{}"),
        ("policy", EMPTY_POLICY),
    ];
    const REGISTRY: &[(&str, &str)] = &[
        ("heights", "{}"),
        ("boundary_tokens", "{}"),
        ("features", "{}"),
        ("interactions", "[]"),
        ("predicate_classes", "{}"),
        ("families", "{}"),
    ];

    /// One record's JSON: every field its dataclass declares, in declaration order, with the first override of a field winning. An override naming a field the dataclass does not declare is a typo in the test, and panics rather than producing a dump the parser would refuse for the wrong reason.
    fn object(declared: &[(&str, &str)], overrides: &[(&str, &str)]) -> String {
        for (key, _) in overrides {
            assert!(
                declared.iter().any(|(field, _)| field == key),
                "no such field: {key}"
            );
        }
        let mut out = String::from("{");
        for (field, default) in declared {
            if out.len() > 1 {
                out.push(',');
            }
            let value = overrides
                .iter()
                .find(|(key, _)| key == field)
                .map_or(*default, |(_, value)| *value);
            out.push_str(&format!("\"{field}\":{value}"));
        }
        out.push('}');
        out
    }

    pub fn condition(overrides: &[(&str, &str)]) -> String {
        object(CONDITION, overrides)
    }

    pub fn when(overrides: &[(&str, &str)]) -> String {
        object(WHEN, overrides)
    }

    pub fn record(overrides: &[(&str, &str)]) -> String {
        object(RECORD, overrides)
    }

    pub fn policy(overrides: &[(&str, &str)]) -> String {
        object(POLICY, overrides)
    }

    pub fn row(height: &str, overrides: &[(&str, &str)]) -> String {
        let named = quote(height);
        let mut fields = vec![("height", named.as_str())];
        fields.extend_from_slice(overrides);
        object(ROW, &fields)
    }

    pub fn surface(overrides: &[(&str, &str)]) -> String {
        object(SURFACE, overrides)
    }

    pub fn stance(name: &str, overrides: &[(&str, &str)]) -> String {
        let named = quote(name);
        let mut fields = vec![("name", named.as_str())];
        fields.extend_from_slice(overrides);
        object(STANCE, &fields)
    }

    pub fn rune(name: &str, overrides: &[(&str, &str)]) -> String {
        let named = quote(name);
        let mut fields = vec![("name", named.as_str())];
        fields.extend_from_slice(overrides);
        object(RUNE, &fields)
    }

    pub fn registry(overrides: &[(&str, &str)]) -> String {
        object(REGISTRY, overrides)
    }

    /// A whole dump around a runes mapping and a registry.
    pub fn dump(runes: &str, registry: &str) -> String {
        format!(r#"{{"format":"ams-m1-spec/1","runes":{runes},"registry":{registry}}}"#)
    }

    /// A JSON object over raw values, for the mappings whose keys are load-bearing.
    pub fn map(entries: &[(&str, &str)]) -> String {
        let fields: Vec<String> = entries
            .iter()
            .map(|(key, value)| format!("\"{key}\":{value}"))
            .collect();
        format!("{{{}}}", fields.join(","))
    }

    /// A JSON array over raw values.
    pub fn seq(items: &[&str]) -> String {
        format!("[{}]", items.join(","))
    }

    /// A JSON array of strings.
    pub fn names(items: &[&str]) -> String {
        let quoted: Vec<String> = items.iter().map(|item| quote(item)).collect();
        format!("[{}]", quoted.join(","))
    }

    /// One JSON string. The fixtures spell only plain names, so quoting is all the escaping they need.
    pub fn quote(value: &str) -> String {
        format!("\"{value}\"")
    }

    /// The index one dump builds, panicking on a dump the parser refuses — a refused fixture is a broken test, not a finding.
    pub fn index_of(text: &str) -> SpecIndex {
        SpecIndex::new(parse_spec(text).expect("a fixture dump parses"))
    }

    /// One name's symbol, panicking when the fixture never mentioned it.
    pub fn sym(index: &SpecIndex, text: &str) -> Sym {
        index
            .sym_of(text)
            .unwrap_or_else(|| panic!("the fixture mentions {text}"))
    }

    /// The `extend` record a fixture gave this id, panicking when there is none.
    pub fn extend<'a>(index: &'a SpecIndex, rune: &str, id: &str) -> &'a PolicyRecord {
        by_id(index, rune, id, |policy| &policy.extend)
    }

    /// The `contract` record a fixture gave this id.
    pub fn contract<'a>(index: &'a SpecIndex, rune: &str, id: &str) -> &'a PolicyRecord {
        by_id(index, rune, id, |policy| &policy.contract)
    }

    fn by_id<'a>(
        index: &'a SpecIndex,
        rune: &str,
        id: &str,
        list: impl Fn(&'a crate::model::Policy) -> &'a [PolicyRecord],
    ) -> &'a PolicyRecord {
        let wanted = sym(index, id);
        let rune = index
            .rune(sym(index, rune))
            .expect("the fixture models the rune");
        list(&rune.policy)
            .iter()
            .find(|record| record.id == Some(wanted))
            .unwrap_or_else(|| panic!("the fixture declares a record with id {id}"))
    }

    /// The small four-family spec the vocabulary and lookup tests read.
    ///
    /// `qsPea` is the plain case: one stance, a selectable baseline entry, an x-height exit. `qsTea` is the awkward one on purpose — two stances declared `half` then `full` under a `policy.order` of `["ghost", "full"]`, so the order index has to reproduce the seat a name that is not a stance still occupies; its entry rows carry one selectable stroke and one unselectable one, and it declares a `pulled-back` sibling bitmap and a refusal with provenance. `qsMay` bears its entry only through an unlock, and `qsIt` bears none at all and carries the two local groups the class resolution order is tested against — one of which deliberately shadows a registry predicate class.
    pub fn mini() -> SpecIndex {
        index_of(&mini_dump())
    }

    /// [`mini`] as the dump text it is built from, for the tests that need a spec on disk rather than an index in hand.
    pub fn mini_dump() -> String {
        let pea_stance = stance(
            "half",
            &[(
                "surface",
                &surface(&[
                    ("entries", &map(&[("baseline", &row("baseline", &[]))])),
                    ("exits", &map(&[("x-height", &row("x-height", &[]))])),
                ]),
            )],
        );
        let tea_half = stance(
            "half",
            &[
                ("bitmaps", &map(&[("pulled-back", EMPTY_BITMAP)])),
                (
                    "surface",
                    &surface(&[
                        (
                            "entries",
                            &map(&[
                                (
                                    "baseline",
                                    &row("baseline", &[("stroke", "\"horizontal\"")]),
                                ),
                                (
                                    "x-height",
                                    &row(
                                        "x-height",
                                        &[("stroke", "\"rising\""), ("selectable", "false")],
                                    ),
                                ),
                            ]),
                        ),
                        (
                            "exits",
                            &map(&[
                                ("x-height", &row("x-height", &[])),
                                (
                                    "baseline",
                                    &row("baseline", &[("withdrawal", "\"pulled-back\"")]),
                                ),
                            ]),
                        ),
                    ]),
                ),
            ],
        );
        let tea_full = stance("full", &[]);
        let may_bare = stance(
            "bare",
            &[(
                "surface",
                &surface(&[("exits", &map(&[("baseline", &row("baseline", &[]))]))]),
            )],
        );
        let may_alt = stance(
            "alt",
            &[(
                "surface",
                &surface(&[
                    (
                        "entries",
                        &map(&[("x-height", &row("x-height", &[("selectable", "false")]))]),
                    ),
                    (
                        "unlocks",
                        &seq(&[
                            r#"{"feature":"ss03","entry":"x-height","exit":null,"pairing":null,"when":null,"why":null,"provenance":["qsMay.yaml","stances.alt.unlocks[0]"]}"#,
                        ]),
                    ),
                ]),
            )],
        );
        let it_solo = stance("solo", &[]);
        let runes = map(&[
            (
                "qsPea",
                &rune("qsPea", &[("stances", &map(&[("half", &pea_stance)]))]),
            ),
            (
                "qsTea",
                &rune(
                    "qsTea",
                    &[
                        ("stances", &map(&[("half", &tea_half), ("full", &tea_full)])),
                        (
                            "policy",
                            &policy(&[
                                ("order", &names(&["ghost", "full"])),
                                (
                                    "refuse",
                                    &seq(&[&record(&[
                                        ("kind", "\"refuse\""),
                                        ("provenance", &names(&["qsTea.yaml", "policy.refuse[0]"])),
                                    ])]),
                                ),
                            ]),
                        ),
                    ],
                ),
            ),
            (
                "qsMay",
                &rune(
                    "qsMay",
                    &[("stances", &map(&[("bare", &may_bare), ("alt", &may_alt)]))],
                ),
            ),
            (
                "qsIt",
                &rune(
                    "qsIt",
                    &[
                        ("stances", &map(&[("solo", &it_solo)])),
                        (
                            "policy",
                            &policy(&[(
                                "groups",
                                &map(&[
                                    ("utter-pass-through-vetoes", &names(&["qsMay", "qsPea"])),
                                    ("halves-that-exit-at-x-height", &names(&["qsMay"])),
                                ]),
                            )]),
                        ),
                    ],
                ),
            ),
        ]);
        dump(&runes, &four_family_registry())
    }

    /// The letters the fixture registries share, as a registry spells them.
    const FOUR_FAMILIES: &[(&str, &str)] = &[
        ("qsPea", r#"{"codepoint":58960,"sequence":null}"#),
        ("qsTea", r#"{"codepoint":58962,"sequence":null}"#),
        ("qsMay", r#"{"codepoint":58981,"sequence":null}"#),
        ("qsIt", r#"{"codepoint":58992,"sequence":null}"#),
    ];

    /// The registry the fixture specs share: two heights, the four families, and the predicate class the specificity tests expand.
    pub fn four_family_registry() -> String {
        registry_over(FOUR_FAMILIES)
    }

    /// [`four_family_registry`] plus the one ligature family, `qsPea_qsTea`, which carries a sequence where a letter carries a code point and is an ordinary literal name everywhere an axis reads one.
    pub fn ligature_family_registry() -> String {
        let mut families = FOUR_FAMILIES.to_vec();
        families.push((
            "qsPea_qsTea",
            r#"{"codepoint":null,"sequence":["qsPea","qsTea"]}"#,
        ));
        registry_over(&families)
    }

    fn registry_over(families: &[(&str, &str)]) -> String {
        registry(&[
            ("heights", &map(&[("baseline", "0"), ("x-height", "5")])),
            (
                "boundary_tokens",
                &map(&[
                    ("space", r#"{"codepoint":32,"splits_runs":true}"#),
                    ("zwnj", r#"{"codepoint":8204,"splits_runs":false}"#),
                ]),
            ),
            (
                "predicate_classes",
                &map(&[("halves-that-exit-at-x-height", &names(&["qsPea", "qsTea"]))]),
            ),
            ("families", &map(families)),
        ])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{NONE_STATE, WITHDRAWN_SUFFIX};

    #[test]
    fn a_rune_and_its_stances_are_found_by_name_in_declaration_order() {
        let index = fixtures::mini();
        let tea = fixtures::sym(&index, "qsTea");
        assert!(index.is_modeled(tea));
        assert_eq!(index.rune_count(), 4);
        assert_eq!(index.rune(tea).expect("qsTea is modeled").name, tea);
        let seat = index.rune_seat(tea).expect("qsTea has a seat");
        assert_eq!(index.rune_name_at(seat), tea);
        assert_eq!(index.stance_count(seat), 2);
        let names: Vec<&str> = index
            .rune_at(seat)
            .stances
            .iter()
            .map(|(name, _)| index.resolve(*name))
            .collect();
        assert_eq!(names, ["half", "full"]);
        let half = index
            .stance_id(tea, fixtures::sym(&index, "half"))
            .expect("qsTea declares half");
        assert_eq!(half, StanceId::new(seat, 0));
        assert_eq!(index.resolve(index.stance_name(half)), "half");
        assert_eq!(index.resolve(index.stance(half).motion), "flat");
        assert_eq!(index.stance_id(tea, fixtures::sym(&index, "solo")), None);
        assert_eq!(index.rune(fixtures::sym(&index, "boundary")), None);
    }

    #[test]
    fn a_surface_row_carries_the_seat_the_enumeration_numbers_it_by() {
        let index = fixtures::mini();
        let tea = fixtures::sym(&index, "qsTea");
        let half = index
            .stance_id(tea, fixtures::sym(&index, "half"))
            .expect("qsTea declares half");
        let baseline = fixtures::sym(&index, "baseline");
        let x_height = fixtures::sym(&index, "x-height");
        let (seat, row) = index
            .entry_row(half, baseline)
            .expect("half enters at the baseline");
        assert_eq!(seat, 0);
        assert!(row.selectable);
        assert_eq!(
            index.resolve(row.stroke.expect("the row names a stroke")),
            "horizontal"
        );
        let (seat, row) = index
            .entry_row(half, x_height)
            .expect("half enters at the x-height too");
        assert_eq!(seat, 1);
        assert!(!row.selectable);
        assert_eq!(
            index.exit_row(half, x_height).map(|(seat, _)| seat),
            Some(0)
        );
        assert_eq!(
            index.exit_row(half, baseline).map(|(seat, _)| seat),
            Some(1)
        );
        assert!(index.declares_exit(half, baseline));
        let top = index.sym_of("top");
        assert_eq!(top, None);
        let full = index
            .stance_id(tea, fixtures::sym(&index, "full"))
            .expect("qsTea declares full");
        assert_eq!(index.entry_row(full, baseline), None);
        assert!(!index.declares_exit(full, baseline));
    }

    #[test]
    fn the_order_index_keeps_the_seat_a_name_that_is_not_a_stance_occupies() {
        let index = fixtures::mini();
        let tea = fixtures::sym(&index, "qsTea");
        let seat = index.rune_seat(tea).expect("qsTea has a seat");
        // policy.order is ["ghost", "full"]: ghost takes seat 0 without being a stance, full takes 1, and half is appended.
        assert_eq!(index.order_index(StanceId::new(seat, 1)), 1);
        assert_eq!(index.order_index(StanceId::new(seat, 0)), 2);
        assert_eq!(
            index.resolve(index.default_stance(tea).expect("qsTea has an order")),
            "ghost"
        );
    }

    #[test]
    fn a_rune_with_no_declared_order_ranks_its_stances_in_declaration_order() {
        let index = fixtures::mini();
        let may = fixtures::sym(&index, "qsMay");
        let seat = index.rune_seat(may).expect("qsMay has a seat");
        assert_eq!(index.order_index(StanceId::new(seat, 0)), 0);
        assert_eq!(index.order_index(StanceId::new(seat, 1)), 1);
        assert_eq!(
            index.resolve(index.default_stance(may).expect("qsMay has stances")),
            "bare"
        );
    }

    #[test]
    fn a_class_resolves_registry_first_then_the_owner_then_any_rune() {
        let index = fixtures::mini();
        let halves = fixtures::sym(&index, "halves-that-exit-at-x-height");
        let vetoes = fixtures::sym(&index, "utter-pass-through-vetoes");
        let it = fixtures::sym(&index, "qsIt");
        let tea = fixtures::sym(&index, "qsTea");
        let pea = fixtures::sym(&index, "qsPea");
        let may = fixtures::sym(&index, "qsMay");
        // qsIt declares a group with the predicate class's own name; the registry still wins.
        let registry_members = index
            .class_members(halves, Some(it))
            .expect("the registry declares the class");
        assert_eq!(registry_members, &BTreeSet::from([pea, tea]));
        assert_eq!(
            index.rune_group(it, halves),
            Some(&BTreeSet::from([may])),
            "the shadowed group is still readable on its own rune"
        );
        // A group the owner declares resolves through the owner.
        assert_eq!(
            index
                .class_members(vetoes, Some(it))
                .expect("qsIt declares the group"),
            &BTreeSet::from([may, pea])
        );
        // And with no owner, or an owner that does not declare it, the scan over every rune finds it.
        assert_eq!(
            index
                .class_members(vetoes, None)
                .expect("some rune declares it"),
            &BTreeSet::from([may, pea])
        );
        assert_eq!(
            index
                .class_members(vetoes, Some(tea))
                .expect("some rune declares it"),
            &BTreeSet::from([may, pea])
        );
        assert_eq!(
            index.predicate_class(vetoes),
            None,
            "a rune-local group is not a registry class"
        );
    }

    #[test]
    fn an_unresolvable_class_names_itself_in_the_complaint() {
        let index = fixtures::mini();
        let stray = fixtures::sym(&index, "half");
        let complaint = index
            .class_members(stray, None)
            .expect_err("half is a stance, not a class");
        assert_eq!(complaint.message(), "unknown class or group: 'half'");
    }

    #[test]
    fn the_registry_answers_heights_families_and_boundary_tokens() {
        let index = fixtures::mini();
        let baseline = fixtures::sym(&index, "baseline");
        let x_height = fixtures::sym(&index, "x-height");
        assert_eq!(index.y_of(baseline), Some(0));
        assert_eq!(index.y_of(x_height), Some(5));
        assert_eq!(index.y_of(fixtures::sym(&index, "half")), None);
        let families: Vec<&str> = index
            .families()
            .iter()
            .map(|name| index.resolve(*name))
            .collect();
        assert_eq!(families.len(), 4);
        assert!(families.contains(&"qsMay"));
        let space = index
            .boundary_token(index.vocab().space)
            .expect("the registry declares a space");
        assert_eq!(space.codepoint, 32);
        assert!(space.splits_runs);
        let zwnj = index
            .boundary_token(index.vocab().zwnj)
            .expect("the registry declares a zwnj");
        assert!(!zwnj.splits_runs);
        assert_eq!(index.boundary_token(index.vocab().edge), None);
    }

    #[test]
    fn a_withdrawn_state_is_the_height_plus_the_suffix() {
        let index = fixtures::mini();
        let baseline = fixtures::sym(&index, "baseline");
        let state = index
            .withdrawn_state(baseline)
            .expect("every registry height has a withdrawn state");
        assert_eq!(index.resolve(state), "baseline-withdrawn");
        assert_eq!(
            index.resolve(state),
            format!("{}{WITHDRAWN_SUFFIX}", index.resolve(baseline))
        );
        assert_eq!(index.withdrawn_state(fixtures::sym(&index, "half")), None);
    }

    #[test]
    fn the_vocabulary_is_interned_against_the_specs_own_pool() {
        let index = fixtures::mini();
        let vocab = index.vocab();
        // The dump mentions "space" and "zwnj" as boundary tokens, so the vocabulary must have resolved to those very symbols rather than minting twins.
        assert_eq!(Some(vocab.space), index.sym_of("space"));
        assert_eq!(Some(vocab.zwnj), index.sym_of("zwnj"));
        // Nothing in the dump says "live", and the vocabulary interned it anyway.
        assert_eq!(Some(vocab.live), index.sym_of("live"));
        assert_eq!(index.resolve(vocab.none), NONE_STATE);
        assert_eq!(index.sym_of("qsOoze"), None);
    }

    #[test]
    fn entry_bearing_reads_selectable_rows_and_entry_unlocks() {
        let index = fixtures::mini();
        assert!(
            index.is_entry_bearing(fixtures::sym(&index, "qsPea")),
            "a selectable declared entry bears an entry"
        );
        assert!(
            index.is_entry_bearing(fixtures::sym(&index, "qsMay")),
            "an entry unlock bears one even though no declared row is selectable"
        );
        assert!(
            !index.is_entry_bearing(fixtures::sym(&index, "qsIt")),
            "a rune with no entry surface at all bears none"
        );
        assert!(!index.is_entry_bearing(fixtures::sym(&index, "half")));
    }

    #[test]
    fn entry_strokes_gather_only_the_selectable_rows() {
        let index = fixtures::mini();
        let tea = fixtures::sym(&index, "qsTea");
        let strokes: Vec<&str> = index
            .entry_strokes(tea)
            .iter()
            .map(|stroke| index.resolve(*stroke))
            .collect();
        assert_eq!(
            strokes,
            ["horizontal"],
            "the unselectable rising row is not offered"
        );
        assert!(
            index
                .entry_strokes(fixtures::sym(&index, "qsIt"))
                .is_empty()
        );
        assert!(
            index
                .entry_strokes(fixtures::sym(&index, "half"))
                .is_empty()
        );
    }

    #[test]
    fn the_index_hands_back_the_spec_it_was_built_over() {
        let index = fixtures::mini();
        assert_eq!(index.root().runes.len(), 4);
        assert_eq!(index.registry().heights.len(), 2);
        assert_eq!(index.runes().len(), 4);
        assert_eq!(
            crate::emit::emit_spec(index.spec()),
            crate::emit::emit_spec(index.spec()),
            "emission reads the tree, so the interned vocabulary cannot disturb it"
        );
    }
}
