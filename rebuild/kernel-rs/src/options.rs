//! The per-build tables behind the right-slot option pipelines, and the pipelines themselves: which adjacent rune pairs some ligature's sequence contains, the §5.7 survivable-window maps that say under which followers such a pair still enumerates unformed, and the third- and fourth-slot option lists. The enumeration loop and the class-grain partition assertion in [`crate::fixpoint`] both get their option lists from this code, so a filter added here applies to both.
//!
//! Everything here is a function of the spec and the late-formation guard, so it is computed once per build. The guard is the expensive part, since each allowed set sweeps the whole option alphabet. The guard memoizes its verdicts, so building a [`WindowOptions`] fills the cache the pipelines later hit.
//!
//! Two choices follow from Rust ownership. The guard state lives inside [`WindowOptions`], so every method that can reach a verdict takes `&mut self`. A survivable follower map is returned behind an [`Rc`], so a caller can keep the map its window inherits while it calls the guard-consulting filters that run after it.
//!
//! [`WindowOptions::survivable`] keeps an overwrite that affects output. When two ligatures' sequences end in the same `(lead, trail)` pair, both write at that pair's key, and the one later in the model's stored order replaces the earlier map unless its own map is empty. Which unformed pairs the enumeration admits therefore depends on declaration order.

use std::collections::BTreeSet;
use std::rc::Rc;

use crate::error::SettleError;
use crate::guard::GuardState;
use crate::hash::{HashMap, HashSet};
use crate::index::SpecIndex;
use crate::model::{Rune, Sym};
use crate::types::{EDGE, NAMER_DOT, RightToken, SPACE, TokenKind, ZWNJ};

/// The four non-letter tokens a raw right slot can hold, in the order every option pipeline lists them ahead of the letters. The order is output-visible, because an option list is filtered and never re-sorted.
pub const RIGHT_BOUNDARIES: [RightToken; 4] = [EDGE, SPACE, ZWNJ, NAMER_DOT];

/// One `(lead, trail)` pair that appears adjacently in some rune's sequence. It keys both the formation-pair set and the survivable map.
pub type FormationPair = (Sym, Sym);

/// What one formation pair's survivable window allows, per follower. A plain follower maps to the right2 options under which the pair survives unformed. A follower that is itself a formed ligature fills both guard slots and maps to `None`, which restricts nothing. Under a via-lead pair, every follower maps to `None`. A follower absent from the map is one under which the pair does not survive at all.
pub type FollowerMap = HashMap<Sym, Option<BTreeSet<RightToken>>>;

/// Every adjacent `(lead, trail)` pair in every rune's sequence, plus, for each such pair, `(lead, L)` for every ligature `L` whose first component is `trail`. Callers only test membership.
pub fn formation_pairs(index: &SpecIndex) -> HashSet<FormationPair> {
    let mut pairs = HashSet::default();
    for (_, rune) in index.runes() {
        let Some(sequence) = rune_sequence(rune) else {
            continue;
        };
        for step in sequence.windows(2) {
            pairs.insert((step[0], step[1]));
            // The via-lead pair: in a post-formation stream, a formed ligature whose first component is this pair's trail stands for that trail, so a bare lead directly before it is the same formation-impossible adjacency under the ligature's name. For example, a bare ·Out before qsTea_qsOy is raw ·Out·Tea·Oy, where greedy formation forms qsOut_qsTea first.
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

/// The §5.7 late-formation guard translated into the table's post-formation label space. For each formation pair, including the via-lead pairs of [`formation_pairs`], it maps each follower of the pair's trail to the right2 options under which the pair survives unformed ([`FollowerMap`]). The guard reads raw slots, so a ligature label at either slot is queried through its raw components: [`raw_of`] on the option side, and the follower's own last two sequence entries on the follower side.
///
/// A follower whose allowed set is empty is left out, and a pair whose whole map is empty is not stored. The enumeration reads that absence as "this window is inadmissible outright", which differs from an empty allowance.
pub fn survivable_formation_windows(
    index: &SpecIndex,
    guard: &mut GuardState<'_>,
    right_letters: &[RightToken],
    right_boundaries: &[RightToken],
) -> Result<HashMap<FormationPair, Rc<FollowerMap>>, SettleError> {
    let mut out: HashMap<FormationPair, Rc<FollowerMap>> = HashMap::default();
    for (name, rune) in index.runes() {
        let Some(sequence) = rune_sequence(rune) else {
            continue;
        };
        let pair = (sequence[sequence.len() - 2], sequence[sequence.len() - 1]);
        let mut follower_map = FollowerMap::default();
        for follower in right_letters {
            if let Some(follower_sequence) = sequence_of(index, follower.letter()) {
                let lead = component_token(index, follower_sequence[follower_sequence.len() - 2]);
                let trail = component_token(index, follower_sequence[follower_sequence.len() - 1]);
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
        // The via-lead keys: for a follower ligature whose first component is this pair's trail, a bare lead survives directly before the formed follower only where this pair's own formation is blocked with the follower's second component in the first guard slot (raw lead·trail·second·F). Both guard slots are then filled, so the deeper slot restricts nothing and entries map to None, as for a formed-ligature follower above. The letters-keyed map cannot express survival before a boundary follower, so that case panics instead of silently narrowing.
        for (liga_name, liga_rune) in index.runes() {
            let Some(liga_sequence) = rune_sequence(liga_rune) else {
                continue;
            };
            if liga_sequence[0] != pair.1 || *liga_name == *name {
                continue;
            }
            let second = component_token(index, liga_sequence[1]);
            let mut via_map = FollowerMap::default();
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

/// The letter token for one component of a ligature's sequence. `spec_load` fails on a sequence member that is not a registry family, and [`SpecIndex::letter`] has a token for every registry family, modeled or not, so this panics only on a spec `spec_load` would not produce.
fn component_token(index: &SpecIndex, component: Sym) -> RightToken {
    index.letter(component).unwrap_or_else(|| {
        panic!(
            "{} is a ligature component the spec does not model",
            index.resolve(component)
        )
    })
}

/// The raw token a post-formation label stands for at the guard's second slot. A ligature label becomes the first component of its sequence, because the guard reads the raw stream and a formed ligature is not in it. Every other token is already raw and is returned unchanged.
pub fn raw_of(index: &SpecIndex, token: RightToken) -> RightToken {
    if token.kind() != TokenKind::Letter {
        return token;
    }
    match sequence_of(index, token.letter()) {
        Some(sequence) => component_token(index, sequence[0]),
        None => token,
    }
}

/// The per-build tables the right-slot option pipelines read, built once per spec from the spec and the guard, with the pipelines as its methods.
pub struct WindowOptions<'i> {
    guard: GuardState<'i>,
    /// Every modeled rune name, sorted by resolved string, as Python's `sorted(spec.runes)`. Sorting by symbol would be wrong: interning order depends on what the dump mentioned first, and this order reaches the emitted rows.
    pub letters: Vec<Sym>,
    /// The letter tokens for [`WindowOptions::letters`], in that same order.
    pub right_letters: Vec<RightToken>,
    /// [`RIGHT_BOUNDARIES`] as the list the pipelines concatenate ahead of the letters.
    pub right_boundaries: Vec<RightToken>,
    /// The formation pairs; see [`formation_pairs`].
    pub formation_pairs: HashSet<FormationPair>,
    /// The survivable windows per formation pair; see [`survivable_formation_windows`] and the overwrite described in the module doc.
    pub survivable: HashMap<FormationPair, Rc<FollowerMap>>,
    /// Every sequence-bearing rune's sequence, by name. Below, "this label is a formed ligature" means "this label is a key here".
    pub liga_sequences: HashMap<Sym, &'i [Sym]>,
    /// The options the existential case of [`WindowOptions::liga_formed_before`] ranges over: the boundaries, then the letters that are not ligatures, since a slot beyond the window is raw text and raw text holds no formed label.
    pub raw_second_options: Vec<RightToken>,
}

impl<'i> WindowOptions<'i> {
    /// Build the tables for one spec, filling the guard's verdict cache as it goes.
    pub fn new(index: &'i SpecIndex) -> Result<Self, SettleError> {
        let mut guard = GuardState::new(index);
        let mut letters: Vec<Sym> = index.runes().iter().map(|(name, _)| *name).collect();
        letters.sort_by(|left, right| index.resolve(*left).cmp(index.resolve(*right)));
        let right_letters: Vec<RightToken> = letters
            .iter()
            .map(|rune| index.letter(*rune).expect("a modeled rune has a token"))
            .collect();
        let right_boundaries = RIGHT_BOUNDARIES.to_vec();
        let formation_pairs = formation_pairs(index);
        let survivable =
            survivable_formation_windows(index, &mut guard, &right_letters, &right_boundaries)?;
        let mut liga_sequences: HashMap<Sym, &'i [Sym]> = HashMap::default();
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

    /// Whether a formed `name` ligature can immediately precede `(next1, next2)` in a post-formation stream, meaning its guard does not fire on the raw tokens those neighbors stand for. A boundary at `next1` always permits it. `next2 = None` means the second guard slot lies beyond the window, and the result is true if any raw continuation in [`WindowOptions::raw_second_options`] permits it.
    ///
    /// A ligature at `next1` supplies both raw slots from its own sequence, so `next2` is not read. Its `sequence[1]` is the sequence's second entry, not its last.
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
                component_token(self.guard.index(), sequence[0]),
                Some(component_token(self.guard.index(), sequence[1])),
            ),
            None => {
                let second = match next2 {
                    None => None,
                    Some(token) if token.kind() == TokenKind::Letter => {
                        match self.liga_sequences.get(&token.letter()) {
                            Some(sequence) => {
                                Some(component_token(self.guard.index(), sequence[0]))
                            }
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

    /// The late-formation follower map an `(input, right1)` window inherits: `None` when the pair is not a formation pair, and the survivable map's entry otherwise. A formation pair with no survivable entry also returns `None`, which is safe because the enumeration never reaches such a window: it is inadmissible outright.
    pub fn context_follower_map(&self, rune_name: Sym, right1: Sym) -> Option<Rc<FollowerMap>> {
        if !self.formation_pairs.contains(&(rune_name, right1)) {
            return None;
        }
        self.survivable.get(&(rune_name, right1)).cloned()
    }

    /// The third slot's options for a window whose two nearer slots are letters. Five filters run in this order over the boundaries-then-letters list, which is never re-sorted:
    ///
    /// 1. Drop an option that would form a pair with `right2` that no survivable window admits.
    /// 2. If the inherited follower map has an allowance for `right2`, keep only what it allows.
    /// 3. If `(right1, right2)` is a formation pair, keep only the letters its survivable map names.
    /// 4. If `right1` is a ligature, keep an option only if the ligature can still precede `(right2, option)`.
    /// 5. If `right2` is a ligature, keep an option only if the ligature can still precede it, with the slot after it beyond the window.
    ///
    /// Both nearer slots are read as letters up front, so a non-letter at either slot panics on entry.
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

    /// The fourth slot's options once the third is known. The same five filters as [`WindowOptions::right3_options`], one slot deeper:
    ///
    /// 1. Drop an option that would form a pair with `right3` that no survivable window admits.
    /// 2. If `(right1, right2)` is a formation pair whose map has an allowance for `right3`, keep only what it allows.
    /// 3. If `(right2, right3)` is a formation pair, keep only the letters its survivable map names.
    /// 4. If `right2` is a ligature, keep an option only if the ligature can still precede `(right3, option)`.
    /// 5. If `right3` is a ligature, keep an option only if the ligature can still precede it, with the slot after it beyond the window.
    ///
    /// All three nearer slots are read as letters up front, so a non-letter at any of them panics on entry.
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

    /// Whether putting `option` after `lead` would form a pair that no survivable window admits. This is the first filter of both pipelines.
    pub fn formation_impossible(&self, lead: Sym, option: RightToken) -> bool {
        option.kind() == TokenKind::Letter
            && self.formation_pairs.contains(&(lead, option.letter()))
            && !self.survivable.contains_key(&(lead, option.letter()))
    }

    /// The ligature filters' shared body: keep the options `liga` can still precede, with `slots` giving the two post-formation neighbors each option supplies. It is a loop because the guard call can fail, which `Vec::retain`'s closure cannot report.
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

/// A rune's sequence when it is present and non-empty, matching Python's `if rune.sequence`.
fn rune_sequence(rune: &Rune) -> Option<&[Sym]> {
    rune.sequence
        .as_deref()
        .filter(|sequence| !sequence.is_empty())
}

/// One modeled rune's sequence by name. It panics on a name the spec does not model, as `spec.runes[…]` raises `KeyError`; every caller here passes a token from the modeled alphabet.
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

    /// A JSON object built from already-serialized entries.
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
    /// `qsPea` and `qsTea` join at the baseline on both sides, so the trail of an unformed `qsPea`–`qsTea` pair can reach a follower. `qsMay` accepts a baseline entry and has no exit, so it is a plain follower that forms nothing. `qsPea_qsTea` has no entry and no exit, so it yields to its components almost everywhere, and as a follower its own formation consumes its entry. `qsPea_qsMay` is the mirror case: its trail has no exit, so its own pair survives nowhere, but it accepts a baseline entry, so a bare trail can still reach it as a follower.
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
        fixtures::letter(index, name)
    }

    /// A token as the tests write it: a letter by its rune name, a boundary by its kind.
    fn label(index: &SpecIndex, token: RightToken) -> String {
        match token {
            RightToken::Letter(rune, _) => index.resolve(rune).to_owned(),
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
        // `qsPea_qsMay`'s own trail has no exit, so its pair survives under no follower and is never stored.
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
        // `qsPea_qsTea` is the third case: its own formation consumes the entry the trail would have reached, so the pair does not survive under it and it is absent from the map.
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
        // Both raw slots present: the ligature stands where its own guard does not fire.
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

    /// The two-ligature alphabet for the overwrite test: `qsPea_qsTea` and `qsMay_qsPea_qsTea` both end in the `(qsPea, qsTea)` pair, so both write at that key. Their maps differ because the three-part ligature does reach a follower — it exits at the baseline — everywhere except behind a `qsMay`, which its own refusal rules out.
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
        // Declared last, the three-part ligature's map replaces the other: it survives only behind a `qsMay`, where its own refusal leaves it nothing to reach with.
        let index = shared_pair_alphabet(false);
        assert_eq!(followers(&index), ["qsMay"]);
        // With the two-part ligature declared last, its broader map is kept. The spec is the same; only the stored order differs.
        let index = shared_pair_alphabet(true);
        assert_eq!(followers(&index), ["qsMay", "qsPea", "qsTea"]);
    }
}
