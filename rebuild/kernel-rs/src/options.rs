//! The per-build static structures behind the right-slot option pipelines: which adjacent rune pairs some ligature's sequence spells, the section 5.7 survivable-window maps that say under which followers such a pair still enumerates unformed, and the third- and fourth-slot option pipelines themselves. One implementation, deliberately: the enumeration loop and the partition assertion both compute their option lists by running this code, so a filter added to the pipeline cannot be added to one caller and forgotten in the other.
//!
//! Everything here is a pure function of the spec plus the late-formation guard, which is why it is computed once per build and read everywhere afterwards. The guard is the expensive half — every allowed set is a sweep over the whole option alphabet — and it is also the half whose verdicts are memoized, so building a [`WindowOptions`] warms the cache the option pipelines then hit.
//!
//! Two shapes here are forced by ownership rather than by semantics. The guard state lives inside [`WindowOptions`] rather than behind a cache keyed on the spec's identity beside it, so every method that can reach a verdict takes `&mut self`; and a survivable follower map is handed out behind an [`Rc`] rather than as a borrow, so a caller can hold the map its window inherits across the guard-consulting filters that run after it. A garbage-collected language gets both for free.
//!
//! The overwrite in [`WindowOptions::survivable`] is load-bearing and is kept rather than smoothed over: two ligatures whose sequences end in the same `(lead, trail)` pair both write at that pair's seat, and the one that wins is the one the dump declares later, because the loop walks the runes in the model's stored order. The enumeration's admission of an unformed pair therefore depends on which ligature spoke last.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::rc::Rc;

use crate::error::SettleError;
use crate::guard::GuardState;
use crate::index::SpecIndex;
use crate::model::{Rune, Sym};
use crate::types::{EDGE, NAMER_DOT, RightToken, SPACE, TokenKind, ZWNJ};

/// The four non-letter tokens a raw right slot can hold, in the order every option pipeline lists them ahead of the letters. The order is output-visible, because an option list is filtered and never re-sorted.
pub const RIGHT_BOUNDARIES: [RightToken; 4] = [EDGE, SPACE, ZWNJ, NAMER_DOT];

/// One `(lead, trail)` pair that some rune's sequence spells adjacently — the key both the formation-pair set and the survivable map are indexed by.
pub type FormationPair = (Sym, Sym);

/// What one formation pair's survivable window allows, per plain follower: the right2 options under which the pair survives unformed, or `None` where the follower is itself a formed ligature that swallowed both guard slots and so restricts nothing. A follower absent from the map is the third case and the strongest one — the pair does not survive under it at all.
pub type FollowerMap = HashMap<Sym, Option<BTreeSet<RightToken>>>;

/// Every adjacent `(lead, trail)` pair every rune's sequence spells. Membership is the only question ever asked of it, so an unordered set is the honest type.
pub fn formation_pairs(index: &SpecIndex) -> HashSet<FormationPair> {
    let mut pairs = HashSet::new();
    for (_, rune) in index.runes() {
        let Some(sequence) = rune_sequence(rune) else {
            continue;
        };
        for step in sequence.windows(2) {
            pairs.insert((step[0], step[1]));
            // The via-lead twin: a formed ligature token whose first component is this pair's trail stands for that trail in a post-formation stream, so a bare lead directly before it is the same formation-impossible adjacency wearing the follower's ligature name (bare ·Out before qsTea_qsOy spells raw ·Out·Tea·Oy, where greedy formation forms qsOut_qsTea first).
            for (liga_name, liga_rune) in index.runes() {
                let Some(liga_sequence) = rune_sequence(liga_rune) else {
                    continue;
                };
                if liga_sequence[0] == step[1] {
                    pairs.insert((step[0], *liga_name));
                }
            }
        }
    }
    pairs
}

/// The section 5.7 late-formation guard translated into the table's post-formation label space: for each formation pair, the right2 options under which the pair survives unformed, each mapped to the allowed right2 tokens of the trail's own subsequent window. The guard reads raw slots, so a ligature label at either slot is queried through its raw components — [`raw_of`] at the option side, and the follower's own last two sequence entries at the follower side.
///
/// A follower whose allowed set comes out empty is dropped rather than stored empty, and a pair whose whole map comes out empty never lands at all; the enumeration reads that absence as "this window is inadmissible outright", which is a different thing from an empty allowance.
pub fn survivable_formation_windows(
    index: &SpecIndex,
    guard: &mut GuardState<'_>,
    right_letters: &[RightToken],
    right_boundaries: &[RightToken],
) -> Result<HashMap<FormationPair, Rc<FollowerMap>>, SettleError> {
    let mut out: HashMap<FormationPair, Rc<FollowerMap>> = HashMap::new();
    for (name, rune) in index.runes() {
        let Some(sequence) = rune_sequence(rune) else {
            continue;
        };
        let pair = (sequence[sequence.len() - 2], sequence[sequence.len() - 1]);
        let mut follower_map = FollowerMap::new();
        for follower in right_letters {
            if let Some(follower_sequence) = sequence_of(index, follower.letter()) {
                let lead = RightToken::Letter(follower_sequence[follower_sequence.len() - 2]);
                let trail = RightToken::Letter(follower_sequence[follower_sequence.len() - 1]);
                if guard.formation_blocked(*name, lead, trail)? {
                    follower_map.insert(follower.letter(), None);
                }
                continue;
            }
            let mut allowed = BTreeSet::new();
            for option in right_boundaries.iter().chain(right_letters) {
                if guard.formation_blocked(*name, *follower, raw_of(index, *option))? {
                    allowed.insert(*option);
                }
            }
            if !allowed.is_empty() {
                follower_map.insert(follower.letter(), Some(allowed));
            }
        }
        if !follower_map.is_empty() {
            out.insert(pair, Rc::new(follower_map));
        }
        // The via-lead keys: for a follower ligature whose first component is this pair's trail, a bare lead survives directly before the formed follower only where this pair's own formation is blocked reading the follower's second component as its first guard slot (raw lead·trail·second·F). The deeper slot restricts nothing — the guard's two slots are fully consumed — so entries carry None, matching the formed-ligature-follower convention above. A survivable-before-boundary verdict is inexpressible in the letters-keyed map, so it asserts instead of silently narrowing.
        for (liga_name, liga_rune) in index.runes() {
            let Some(liga_sequence) = rune_sequence(liga_rune) else {
                continue;
            };
            if liga_sequence[0] != pair.1 || *liga_name == *name {
                continue;
            }
            let second = RightToken::Letter(liga_sequence[1]);
            let mut via_map = FollowerMap::new();
            for follower in right_letters {
                if guard.formation_blocked(*name, second, raw_of(index, *follower))? {
                    via_map.insert(follower.letter(), None);
                }
            }
            for boundary in right_boundaries {
                assert!(
                    !guard.formation_blocked(*name, second, *boundary)?,
                    "a via-lead formation pair survives before a boundary follower; the survivable map cannot key it"
                );
            }
            if !via_map.is_empty() {
                out.insert((pair.0, *liga_name), Rc::new(via_map));
            }
        }
    }
    Ok(out)
}

/// The raw token a post-formation label stands for at the guard's second slot: a ligature label is queried through the lead of its own sequence, because the guard reads the raw stream and a formed ligature is not in it. Everything else is already raw and passes through.
pub fn raw_of(index: &SpecIndex, token: RightToken) -> RightToken {
    if token.kind() != TokenKind::Letter {
        return token;
    }
    match sequence_of(index, token.letter()) {
        Some(sequence) => RightToken::Letter(sequence[0]),
        None => token,
    }
}

/// The per-build static structures the right-slot option pipelines run out of. Built once per spec; every field is a pure function of the spec and the guard, and every method is the pipeline that reads them.
pub struct WindowOptions<'i> {
    guard: GuardState<'i>,
    /// Every modeled rune name, sorted by resolved string. `sorted(spec.runes)` — by the name, never by the symbol, because interning order is an accident of what the dump mentioned first and this order reaches the emitted rows.
    pub letters: Vec<Sym>,
    /// The letter tokens for [`WindowOptions::letters`], in that same order.
    pub right_letters: Vec<RightToken>,
    /// [`RIGHT_BOUNDARIES`] as the list the pipelines concatenate ahead of the letters.
    pub right_boundaries: Vec<RightToken>,
    /// Every adjacent pair every sequence spells — see [`formation_pairs`].
    pub formation_pairs: HashSet<FormationPair>,
    /// The survivable windows per formation pair — see [`survivable_formation_windows`], including its overwrite.
    pub survivable: HashMap<FormationPair, Rc<FollowerMap>>,
    /// Every sequence-bearing rune's sequence, by name. Membership in this map is what "this label is a formed ligature" means everywhere below.
    pub liga_sequences: HashMap<Sym, &'i [Sym]>,
    /// The options the existential arm of [`WindowOptions::liga_formed_before`] quantifies over: the boundaries, then the letters that are not themselves ligatures, since a beyond-window slot is raw text and raw text holds no formed label.
    pub raw_second_options: Vec<RightToken>,
}

impl<'i> WindowOptions<'i> {
    /// Build the whole static structure for one spec, warming the guard's verdict cache on the way.
    pub fn new(index: &'i SpecIndex) -> Result<Self, SettleError> {
        let mut guard = GuardState::new(index);
        let mut letters: Vec<Sym> = index.runes().iter().map(|(name, _)| *name).collect();
        letters.sort_by(|left, right| index.resolve(*left).cmp(index.resolve(*right)));
        let right_letters: Vec<RightToken> =
            letters.iter().copied().map(RightToken::Letter).collect();
        let right_boundaries = RIGHT_BOUNDARIES.to_vec();
        let formation_pairs = formation_pairs(index);
        let survivable =
            survivable_formation_windows(index, &mut guard, &right_letters, &right_boundaries)?;
        let mut liga_sequences: HashMap<Sym, &'i [Sym]> = HashMap::new();
        for (name, rune) in index.runes() {
            if let Some(sequence) = rune_sequence(rune) {
                liga_sequences.insert(*name, sequence);
            }
        }
        let mut raw_second_options = right_boundaries.clone();
        raw_second_options.extend(
            right_letters
                .iter()
                .copied()
                .filter(|token| !liga_sequences.contains_key(&token.letter())),
        );
        Ok(Self {
            guard,
            letters,
            right_letters,
            right_boundaries,
            formation_pairs,
            survivable,
            liga_sequences,
            raw_second_options,
        })
    }

    /// Whether a formed `name` ligature can immediately precede `(next1, next2)` in a post-formation stream: its own guard, read over the raw tokens those post-formation neighbors stand for, must not fire. `next2 = None` means the second guard slot lies beyond the window, so the verdict is existential over [`WindowOptions::raw_second_options`] — some raw continuation lets the ligature stand.
    ///
    /// A ligature at `next1` supplies both raw slots out of its own sequence, so `next2` is not read at all in that branch — including the `sequence[1]` read, which is the sequence's second entry and not its last.
    pub fn liga_formed_before(
        &mut self,
        name: Sym,
        next1: RightToken,
        next2: Option<RightToken>,
    ) -> Result<bool, SettleError> {
        if next1.kind() != TokenKind::Letter {
            return Ok(true);
        }
        let (first, second) = match self.liga_sequences.get(&next1.letter()) {
            Some(sequence) => (
                RightToken::Letter(sequence[0]),
                Some(RightToken::Letter(sequence[1])),
            ),
            None => {
                let second = match next2 {
                    None => None,
                    Some(token) if token.kind() == TokenKind::Letter => {
                        match self.liga_sequences.get(&token.letter()) {
                            Some(sequence) => Some(RightToken::Letter(sequence[0])),
                            None => Some(token),
                        }
                    }
                    Some(token) => Some(token),
                };
                (next1, second)
            }
        };
        if let Some(second) = second {
            return Ok(!self.guard.formation_blocked(name, first, second)?);
        }
        for &option in &self.raw_second_options {
            if !self.guard.formation_blocked(name, first, option)? {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// The late-formation follower map an `(input, right1)` window inherits: `None` when the pair is not a formation pair and so restricts nothing, and the survivable map's entry otherwise. The enumeration never reaches a pair whose entry is absent, because such windows are inadmissible outright, so both absences collapse to the one answer here.
    pub fn context_follower_map(&self, rune_name: Sym, right1: Sym) -> Option<Rc<FollowerMap>> {
        if !self.formation_pairs.contains(&(rune_name, right1)) {
            return None;
        }
        self.survivable.get(&(rune_name, right1)).cloned()
    }

    /// The third slot's options for a window whose two nearer slots are letters. Five filters over the boundaries-then-letters list, applied in this order and never re-sorted: the pairs `right2` would form that no survivable window admits are out; the inherited follower map restricts to what the trail's own window allows; a formation pair at `(right1, right2)` narrows to the letters its survivable map names; a ligature at `right1` must still stand before `(right2, option)`; and a ligature at `right2` must still stand before the option with the slot past it beyond the window.
    ///
    /// Both nearer slots are read as letters up front rather than inside each filter. The pair tests at the end read both unconditionally, so a non-letter at either slot panics here — the caller error surfaces one step earlier than it otherwise would.
    pub fn right3_options(
        &mut self,
        right1: RightToken,
        right2: RightToken,
        follower_map: Option<&FollowerMap>,
    ) -> Result<Vec<RightToken>, SettleError> {
        let first = right1.letter();
        let second = right2.letter();
        let mut options = self.boundaries_then_letters();
        options.retain(|option| !self.formation_impossible(second, *option));
        if let Some(map) = follower_map
            && let Some(trail_allowed) = map.get(&second).and_then(Option::as_ref)
        {
            options.retain(|option| trail_allowed.contains(option));
        }
        if self.formation_pairs.contains(&(first, second)) {
            let pair_map = self.survivable.get(&(first, second));
            options.retain(|option| {
                option.kind() == TokenKind::Letter
                    && pair_map.is_some_and(|map| map.contains_key(&option.letter()))
            });
        }
        if self.liga_sequences.contains_key(&first) {
            options = self.retain_formed_before(options, first, |option| (right2, Some(option)))?;
        }
        if self.liga_sequences.contains_key(&second) {
            options = self.retain_formed_before(options, second, |option| (option, None))?;
        }
        Ok(options)
    }

    /// The fourth slot's options once the third is concrete. The same shape one slot deeper: the pairs `right3` would form that no survivable window admits are out; a formation pair at `(right1, right2)` restricts through the allowance it records for `right3`; a formation pair at `(right2, right3)` narrows to the letters its own survivable map names; a ligature at `right2` must still stand before `(right3, option)`; and a ligature at `right3` must still stand before the option with the slot past it beyond the window. All three nearer slots are read as letters up front, for the reason [`WindowOptions::right3_options`] gives.
    pub fn right4_options(
        &mut self,
        right1: RightToken,
        right2: RightToken,
        right3: RightToken,
    ) -> Result<Vec<RightToken>, SettleError> {
        let first = right1.letter();
        let second = right2.letter();
        let third = right3.letter();
        let mut options = self.boundaries_then_letters();
        options.retain(|option| !self.formation_impossible(third, *option));
        if self.formation_pairs.contains(&(first, second))
            && let Some(trail_allowed) = self
                .survivable
                .get(&(first, second))
                .and_then(|map| map.get(&third))
                .and_then(Option::as_ref)
        {
            options.retain(|option| trail_allowed.contains(option));
        }
        if self.formation_pairs.contains(&(second, third)) {
            let pair_map = self.survivable.get(&(second, third));
            options.retain(|option| {
                option.kind() == TokenKind::Letter
                    && pair_map.is_some_and(|map| map.contains_key(&option.letter()))
            });
        }
        if self.liga_sequences.contains_key(&second) {
            options =
                self.retain_formed_before(options, second, |option| (right3, Some(option)))?;
        }
        if self.liga_sequences.contains_key(&third) {
            options = self.retain_formed_before(options, third, |option| (option, None))?;
        }
        Ok(options)
    }

    /// The option list every pipeline starts from: the boundaries, then the letters in sorted-name order.
    fn boundaries_then_letters(&self) -> Vec<RightToken> {
        let mut options =
            Vec::with_capacity(self.right_boundaries.len() + self.right_letters.len());
        options.extend_from_slice(&self.right_boundaries);
        options.extend_from_slice(&self.right_letters);
        options
    }

    /// Whether putting `option` after `lead` would form a pair no survivable window admits — the first filter of both pipelines, which drops a letter that the pair's own formation would swallow.
    pub fn formation_impossible(&self, lead: Sym, option: RightToken) -> bool {
        option.kind() == TokenKind::Letter
            && self.formation_pairs.contains(&(lead, option.letter()))
            && !self.survivable.contains_key(&(lead, option.letter()))
    }

    /// The ligature filters' shared body: keep the options `liga` can still stand before, with `slots` saying which two post-formation neighbors each option supplies. Spelled as a loop rather than a retained closure because the verdict consults the guard and can fail.
    fn retain_formed_before(
        &mut self,
        options: Vec<RightToken>,
        liga: Sym,
        slots: impl Fn(RightToken) -> (RightToken, Option<RightToken>),
    ) -> Result<Vec<RightToken>, SettleError> {
        let mut kept = Vec::with_capacity(options.len());
        for option in options {
            let (next1, next2) = slots(option);
            if self.liga_formed_before(liga, next1, next2)? {
                kept.push(option);
            }
        }
        Ok(kept)
    }
}

/// A rune's sequence when it has a non-empty one — Python's `if rune.sequence`, which is false for both the absent and the empty spelling.
fn rune_sequence(rune: &Rune) -> Option<&[Sym]> {
    rune.sequence
        .as_deref()
        .filter(|sequence| !sequence.is_empty())
}

/// One modeled rune's sequence by name, panicking on a name the spec does not model exactly as `spec.runes[…]` raises `KeyError`; every caller here reads a token drawn from the modeled alphabet.
fn sequence_of(index: &SpecIndex, name: Sym) -> Option<&[Sym]> {
    let rune = index.rune(name).unwrap_or_else(|| {
        panic!(
            "{} is not modeled, exactly as spec.runes[…] raises KeyError",
            index.resolve(name)
        )
    });
    rune_sequence(rune)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;

    /// A JSON object over already-built pieces, for the mappings the fixtures compose rather than spell.
    fn object(entries: &[(String, String)]) -> String {
        let pairs: Vec<String> = entries
            .iter()
            .map(|(key, value)| format!("\"{key}\":{value}"))
            .collect();
        format!("{{{}}}", pairs.join(","))
    }

    fn row(height: &str) -> (String, String) {
        (height.to_owned(), fixtures::row(height, &[]))
    }

    fn stance(name: &str, entries: &str, exits: &str) -> (String, String) {
        let surface = fixtures::surface(&[("entries", entries), ("exits", exits)]);
        (
            name.to_owned(),
            fixtures::stance(name, &[("surface", surface.as_str())]),
        )
    }

    fn rune(name: &str, stances: &[(String, String)], extra: &[(&str, &str)]) -> (String, String) {
        let stances = object(stances);
        let mut fields = vec![("stances", stances.as_str())];
        fields.extend_from_slice(extra);
        (name.to_owned(), fixtures::rune(name, &fields))
    }

    fn spec_of(runes: &[(String, String)]) -> SpecIndex {
        fixtures::index_of(&fixtures::dump(
            &object(runes),
            &fixtures::four_family_registry(),
        ))
    }

    /// The little alphabet the option pipelines are read against.
    ///
    /// `qsPea` and `qsTea` join at the baseline on both sides, so the trail of a `qsPea`–`qsTea` ligature left unformed can reach a follower; `qsMay` accepts a baseline entry and offers no exit, so it is a plain follower nothing forms with. `qsPea_qsTea` accepts nothing and offers nothing, which is what makes it a ligature that yields to its components almost everywhere — and also a ligature follower whose own entry its formation consumes. `qsPea_qsMay` is the mirror case: it forms out of a pair whose trail offers no exit, so its own window survives nowhere, but it accepts a baseline entry and is therefore a ligature follower a bare trail can still reach.
    fn alphabet() -> SpecIndex {
        let baseline = object(&[row("baseline")]);
        let pea = rune(
            "qsPea",
            &[stance("half", &baseline, &baseline)],
            &[("codepoint", "58960")],
        );
        let tea = rune(
            "qsTea",
            &[stance("plain", &baseline, &baseline)],
            &[("codepoint", "58962")],
        );
        let may = rune(
            "qsMay",
            &[stance("plain", &baseline, "{}")],
            &[("codepoint", "58981")],
        );
        let pea_tea = rune(
            "qsPea_qsTea",
            &[stance("joined", "{}", "{}")],
            &[("sequence", &fixtures::names(&["qsPea", "qsTea"]))],
        );
        let pea_may = rune(
            "qsPea_qsMay",
            &[stance("joined", &baseline, "{}")],
            &[("sequence", &fixtures::names(&["qsPea", "qsMay"]))],
        );
        spec_of(&[pea, tea, may, pea_tea, pea_may])
    }

    fn letter(index: &SpecIndex, name: &str) -> RightToken {
        RightToken::Letter(fixtures::sym(index, name))
    }

    /// A token as the tests spell it: a letter by its rune name, a boundary by its kind.
    fn label(index: &SpecIndex, token: RightToken) -> String {
        match token {
            RightToken::Letter(rune) => index.resolve(rune).to_owned(),
            other => other.kind().as_str().to_owned(),
        }
    }

    fn labels(index: &SpecIndex, tokens: &[RightToken]) -> Vec<String> {
        tokens.iter().map(|token| label(index, *token)).collect()
    }

    /// One follower map flattened to sorted, resolved prose: the follower, then its allowance or `None` for the unrestricted case.
    fn spelled(index: &SpecIndex, map: &FollowerMap) -> Vec<(String, Option<Vec<String>>)> {
        let mut rows: Vec<(String, Option<Vec<String>>)> = map
            .iter()
            .map(|(follower, allowed)| {
                let allowed = allowed.as_ref().map(|tokens| {
                    let mut spelled: Vec<String> =
                        tokens.iter().map(|token| label(index, *token)).collect();
                    spelled.sort();
                    spelled
                });
                (index.resolve(*follower).to_owned(), allowed)
            })
            .collect();
        rows.sort();
        rows
    }

    fn pair_names(index: &SpecIndex, pairs: &HashSet<FormationPair>) -> Vec<(String, String)> {
        let mut spelled: Vec<(String, String)> = pairs
            .iter()
            .map(|(lead, trail)| {
                (
                    index.resolve(*lead).to_owned(),
                    index.resolve(*trail).to_owned(),
                )
            })
            .collect();
        spelled.sort();
        spelled
    }

    #[test]
    fn the_alphabet_sorts_by_resolved_name_and_the_raw_options_drop_the_ligatures() {
        let index = alphabet();
        let options = WindowOptions::new(&index).expect("the static structures build");
        let letters: Vec<&str> = options
            .letters
            .iter()
            .map(|name| index.resolve(*name))
            .collect();
        assert_eq!(
            letters,
            ["qsMay", "qsPea", "qsPea_qsMay", "qsPea_qsTea", "qsTea"]
        );
        // Declaration order is qsPea, qsTea, qsMay, …, so a symbol-id sort would have led with qsPea.
        let declared: Vec<&str> = index
            .runes()
            .iter()
            .map(|(name, _)| index.resolve(*name))
            .collect();
        assert_eq!(
            declared,
            ["qsPea", "qsTea", "qsMay", "qsPea_qsTea", "qsPea_qsMay"]
        );
        assert_eq!(
            labels(&index, &options.right_letters),
            ["qsMay", "qsPea", "qsPea_qsMay", "qsPea_qsTea", "qsTea"]
        );
        assert_eq!(
            labels(&index, &options.right_boundaries),
            ["edge", "space", "zwnj", "namer-dot"]
        );
        assert_eq!(
            labels(&index, &options.raw_second_options),
            [
                "edge",
                "space",
                "zwnj",
                "namer-dot",
                "qsMay",
                "qsPea",
                "qsTea"
            ]
        );
    }

    #[test]
    fn the_formation_pairs_are_every_adjacent_step_of_every_sequence() {
        let index = alphabet();
        let options = WindowOptions::new(&index).expect("the static structures build");
        assert_eq!(
            pair_names(&index, &options.formation_pairs),
            [
                ("qsPea".to_owned(), "qsMay".to_owned()),
                ("qsPea".to_owned(), "qsTea".to_owned()),
            ]
        );
    }

    #[test]
    fn a_survivable_map_tells_unrestricted_from_restricted_from_absent() {
        let index = alphabet();
        let options = WindowOptions::new(&index).expect("the static structures build");
        // `qsPea_qsMay`'s own trail offers no exit, so its pair survives under no follower at all and never lands.
        let mut keys = pair_names(
            &index,
            &options.survivable.keys().copied().collect::<HashSet<_>>(),
        );
        keys.sort();
        assert_eq!(keys, [("qsPea".to_owned(), "qsTea".to_owned())]);
        let map = options
            .survivable
            .get(&(
                fixtures::sym(&index, "qsPea"),
                fixtures::sym(&index, "qsTea"),
            ))
            .expect("the pea-tea pair survives somewhere");
        let every = [
            "edge",
            "namer-dot",
            "qsMay",
            "qsPea",
            "qsPea_qsMay",
            "qsPea_qsTea",
            "qsTea",
            "space",
            "zwnj",
        ]
        .map(str::to_owned)
        .to_vec();
        let without_tea: Vec<String> = every
            .iter()
            .filter(|name| *name != "qsTea")
            .cloned()
            .collect();
        assert_eq!(
            spelled(&index, map),
            [
                // A plain follower carries the options it survives under; `qsTea` drops out under `qsPea` because that pair itself forms and the formed label accepts nothing.
                ("qsMay".to_owned(), Some(every.clone())),
                ("qsPea".to_owned(), Some(without_tea)),
                // A ligature follower the bare trail can still reach restricts nothing.
                ("qsPea_qsMay".to_owned(), None),
                ("qsTea".to_owned(), Some(every)),
            ]
        );
        // `qsPea_qsTea` is the third case: its own formation consumes the entry the trail would have reached, so the pair does not survive under it and it is absent rather than empty.
        assert!(!map.contains_key(&fixtures::sym(&index, "qsPea_qsTea")));
    }

    #[test]
    fn a_context_follower_map_is_absent_off_a_pair_and_off_an_unadmitted_pair() {
        let index = alphabet();
        let options = WindowOptions::new(&index).expect("the static structures build");
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");
        assert!(options.context_follower_map(pea, tea).is_some());
        // A formation pair whose survivable map is empty reads the same as no pair at all.
        assert!(options.context_follower_map(pea, may).is_none());
        assert!(options.context_follower_map(tea, pea).is_none());
    }

    #[test]
    fn a_formed_ligature_is_read_through_the_raw_tokens_its_neighbors_stand_for() {
        let index = alphabet();
        let mut options = WindowOptions::new(&index).expect("the static structures build");
        let liga = fixtures::sym(&index, "qsPea_qsTea");
        let pea = letter(&index, "qsPea");
        let tea = letter(&index, "qsTea");
        let may = letter(&index, "qsMay");
        let formed = letter(&index, "qsPea_qsTea");
        let stands = |options: &mut WindowOptions, next1, next2| {
            options
                .liga_formed_before(liga, next1, next2)
                .expect("the verdict computes")
        };
        // A boundary at the first slot short-circuits the guard.
        assert!(stands(&mut options, EDGE, None));
        // Both raw slots present: the ligature stands exactly where its own guard does not fire.
        assert!(stands(&mut options, pea, Some(tea)));
        assert!(!stands(&mut options, pea, Some(may)));
        // A ligature at the second slot is read through its lead, so `qsPea_qsTea` there means `qsPea`.
        assert!(!stands(&mut options, may, Some(formed)));
        // A ligature at the first slot supplies both slots from its own sequence and ignores the second argument entirely.
        assert!(stands(&mut options, formed, Some(EDGE)));
        assert!(stands(&mut options, formed, None));
        // Beyond the window the verdict is existential: `qsPea` stands because some raw continuation frees it, `qsMay` stands nowhere.
        assert!(stands(&mut options, pea, None));
        assert!(!stands(&mut options, may, None));
    }

    #[test]
    fn the_third_slot_pipeline_runs_its_filters_in_order() {
        let index = alphabet();
        let mut options = WindowOptions::new(&index).expect("the static structures build");
        let pea = letter(&index, "qsPea");
        let tea = letter(&index, "qsTea");
        let formed = letter(&index, "qsPea_qsTea");
        // Filter one alone: `(qsPea, qsMay)` forms and survives nowhere, so `qsMay` cannot follow a `qsPea` at right2.
        let plain = options
            .right3_options(tea, pea, None)
            .expect("the pipeline runs");
        assert_eq!(
            labels(&index, &plain),
            [
                "edge",
                "space",
                "zwnj",
                "namer-dot",
                "qsPea",
                "qsPea_qsMay",
                "qsPea_qsTea",
                "qsTea"
            ]
        );
        // Filter two: the map the `(qsPea, qsTea)` window inherits allows everything but `qsTea` behind a `qsPea`.
        let inherited = options
            .context_follower_map(
                fixtures::sym(&index, "qsPea"),
                fixtures::sym(&index, "qsTea"),
            )
            .expect("the pea-tea window inherits a map");
        let restricted = options
            .right3_options(tea, pea, Some(&inherited))
            .expect("the pipeline runs");
        assert_eq!(
            labels(&index, &restricted),
            [
                "edge",
                "space",
                "zwnj",
                "namer-dot",
                "qsPea",
                "qsPea_qsMay",
                "qsPea_qsTea"
            ]
        );
        // Filter three: a formation pair at the two nearer slots narrows to the letters its survivable map names.
        let narrowed = options
            .right3_options(pea, tea, None)
            .expect("the pipeline runs");
        assert_eq!(
            labels(&index, &narrowed),
            ["qsMay", "qsPea", "qsPea_qsMay", "qsTea"]
        );
        // Filter five: a ligature at right2 must still stand before whatever follows it, existentially past the window.
        let guarded = options
            .right3_options(tea, formed, None)
            .expect("the pipeline runs");
        assert_eq!(
            labels(&index, &guarded),
            ["edge", "space", "zwnj", "namer-dot", "qsPea", "qsPea_qsTea"]
        );
    }

    #[test]
    fn the_fourth_slot_pipeline_runs_its_filters_in_order() {
        let index = alphabet();
        let mut options = WindowOptions::new(&index).expect("the static structures build");
        let pea = letter(&index, "qsPea");
        let tea = letter(&index, "qsTea");
        let may = letter(&index, "qsMay");
        // Filters one and two: `qsMay` cannot follow the `qsPea` at right3, and the `(qsPea, qsTea)` pair's allowance for a `qsPea` third slot drops `qsTea`.
        let restricted = options
            .right4_options(pea, tea, pea)
            .expect("the pipeline runs");
        assert_eq!(
            labels(&index, &restricted),
            [
                "edge",
                "space",
                "zwnj",
                "namer-dot",
                "qsPea",
                "qsPea_qsMay",
                "qsPea_qsTea"
            ]
        );
        // The same pair's allowance for a `qsMay` third slot restricts nothing, and no pair starts at `qsMay`.
        let open = options
            .right4_options(pea, tea, may)
            .expect("the pipeline runs");
        assert_eq!(
            labels(&index, &open),
            [
                "edge",
                "space",
                "zwnj",
                "namer-dot",
                "qsMay",
                "qsPea",
                "qsPea_qsMay",
                "qsPea_qsTea",
                "qsTea"
            ]
        );
        // Filter three: a formation pair at right2 and right3 narrows to the letters its survivable map names.
        let narrowed = options
            .right4_options(tea, pea, tea)
            .expect("the pipeline runs");
        assert_eq!(
            labels(&index, &narrowed),
            ["qsMay", "qsPea", "qsPea_qsMay", "qsTea"]
        );
    }

    /// The two-ligature alphabet the overwrite is read against: `qsPea_qsTea` and `qsMay_qsPea_qsTea` both end in the `(qsPea, qsTea)` pair, so both write at that seat. Their maps differ because the three-part ligature does reach a follower — it exits at the baseline — everywhere except behind a `qsMay`, which its own refusal rules out.
    fn shared_pair_alphabet(later_first: bool) -> SpecIndex {
        let baseline = object(&[row("baseline")]);
        let pea = rune(
            "qsPea",
            &[stance("half", &baseline, &baseline)],
            &[("codepoint", "58960")],
        );
        let tea = rune(
            "qsTea",
            &[stance("plain", &baseline, &baseline)],
            &[("codepoint", "58962")],
        );
        let may = rune(
            "qsMay",
            &[stance("plain", &baseline, "{}")],
            &[("codepoint", "58981")],
        );
        let pea_tea = rune(
            "qsPea_qsTea",
            &[stance("joined", "{}", "{}")],
            &[("sequence", &fixtures::names(&["qsPea", "qsTea"]))],
        );
        let refuses_after_may = fixtures::policy(&[(
            "refuse",
            &fixtures::seq(&[&fixtures::record(&[
                ("kind", "\"refuse\""),
                (
                    "when",
                    &fixtures::when(&[(
                        "right",
                        &fixtures::condition(&[("family", &fixtures::names(&["qsMay"]))]),
                    )]),
                ),
            ])]),
        )]);
        let may_pea_tea = rune(
            "qsMay_qsPea_qsTea",
            &[stance("joined", "{}", &baseline)],
            &[
                ("sequence", &fixtures::names(&["qsMay", "qsPea", "qsTea"])),
                ("policy", &refuses_after_may),
            ],
        );
        if later_first {
            spec_of(&[pea, tea, may, may_pea_tea, pea_tea])
        } else {
            spec_of(&[pea, tea, may, pea_tea, may_pea_tea])
        }
    }

    #[test]
    fn two_ligatures_sharing_a_trailing_pair_overwrite_in_declaration_order() {
        let followers = |index: &SpecIndex| {
            let options = WindowOptions::new(index).expect("the static structures build");
            let map = options
                .survivable
                .get(&(fixtures::sym(index, "qsPea"), fixtures::sym(index, "qsTea")))
                .expect("the shared pair survives somewhere")
                .clone();
            let mut names: Vec<String> = map
                .keys()
                .map(|follower| index.resolve(*follower).to_owned())
                .collect();
            names.sort();
            names
        };
        // Declared last, the three-part ligature's map wins the seat outright: it survives only behind a `qsMay`, where its own refusal leaves it nothing to reach with.
        let index = shared_pair_alphabet(false);
        assert_eq!(followers(&index), ["qsMay"]);
        // Declare the two-part ligature last and its broader map is the one that stands — same spec, different stored order.
        let index = shared_pair_alphabet(true);
        assert_eq!(followers(&index), ["qsMay", "qsPea", "qsTea"]);
    }
}
