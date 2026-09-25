//! The section 5.7 late-formation guard, the only place its verdict is computed: whether a ligature yields to its components in one window because the trailing component, left unformed, would join toward the follower while the formed ligature could not. The trail side is settled at ranking grain: a full [`Engine::transition_trace`] with the lead's default stance, unjoined, as its left, so follower votes and the runes' prefers count as well as candidacy. The ligature side is checked more generously, at candidacy grain with the run edge as its left.
//!
//! The verdict depends only on the ligature and the two raw slots past its sequence, which is why it can compile into the formation lookup the font ships. That lookup runs before the stylistic-set marker substitutions and so cannot see the configuration, so the verdict is quantified over the powerset of capability-unlock features and blocks only where every configuration blocks. The engines that compute it have `simulated_prospect` and `vote_slots` off and bind every slot past the verdict's two to the window edge: `vote_deep_slot` is [`EDGE`], and the trace's third and fourth slots are `EDGE`. A vote or prefer that needs deeper raw text therefore cannot change a formation verdict as a side effect of a settlement-scoring change. Making the guard follow either flag is a separate reviewed change, so the modes are pinned here ([`GUARD_MODES`]) instead of read from the engine defaults.
//!
//! [`GuardState`] is per-spec state, built once and kept, and it holds the powerset's engines itself. Python's `settle.form_ligatures` reads the verdicts from the complete sweep, which `kernel_exec.guard_sweep` memoizes per spec. No verdict reads a fired delta, so the engines have no trace memo: nothing is journaled and [`Engine::candidates`] runs uncached. [`GuardState::under`] and [`sweep_under`] compute the same guard for one named configuration instead of the powerset. The font does not use them; the rebuild suite uses them to check, per configuration, where that configuration's verdicts differ from the quantified ones.
//!
//! `GuardState::follower_formation` is computed once before the per-engine loop. It reads only the spec and the two slots, so the answer is the same either way, and computing it first keeps the borrow of the verdict memo separate from the borrow of the engines.

use crate::engine::{Engine, EngineModes, Slots};
use crate::error::SettleError;
use crate::hash::{HashMap, HashSet};
use crate::index::SpecIndex;
use crate::model::Sym;
use crate::types::{
    CellId, EDGE, LeftContext, NAMER_DOT, RightToken, SPACE, Settled, TokenKind, UNKNOWN, ZWNJ,
};

/// The non-letter second slots the sweep covers after the letters, in the order it prints them. The first slot has no such tail, because a non-letter first slot makes the verdict free before any engine runs.
const TAIL_TOKENS: [RightToken; 5] = [EDGE, SPACE, ZWNJ, NAMER_DOT, UNKNOWN];

/// The key of one verdict: the ligature being formed and the two raw slots past its sequence. The verdict depends on nothing else, which the emitted formation lookup requires.
type VerdictKey = (Sym, RightToken, RightToken);

/// One spec's guard state: the engines that must all block for a verdict to block (one per subset of the capability features for the verdict the font ships, or one engine for a single named configuration), and the memoized verdicts.
pub struct GuardState<'i> {
    index: &'i SpecIndex,
    engines: Vec<Engine<'i>>,
    verdicts: HashMap<VerdictKey, bool>,
}

/// The guard's engine modes: `simulated_prospect` and `vote_slots` off and the vote's deep slot pinned to the window edge, whatever the engine defaults are.
const GUARD_MODES: EngineModes = EngineModes {
    vote_deep_slot: EDGE,
    simulated_prospect: false,
    vote_slots: false,
    trace_memo: false,
    explain_ladder: true,
};

impl<'i> GuardState<'i> {
    /// The guard's engines for one spec, in [`GUARD_MODES`]: one per subset of the capability-unlock features, in `itertools.combinations` order over the name-sorted features, smallest subsets first.
    pub fn new(index: &'i SpecIndex) -> Self {
        let features = capability_features(index);
        let mut engines = Vec::new();
        for size in 0..=features.len() {
            for combination in combinations(&features, size) {
                engines.push(Engine::with_modes(index, combination, GUARD_MODES));
            }
        }
        Self::over(index, engines)
    }

    /// One configuration's guard: a single engine over `features`, in [`GUARD_MODES`], so its verdicts are that configuration's alone. The shipped verdict is [`GuardState::new`]'s, which blocks only where every configuration blocks. The rebuild suite sweeps this per configuration to compare the two.
    pub fn under(index: &'i SpecIndex, features: Vec<Sym>) -> Self {
        Self::over(
            index,
            vec![Engine::with_modes(index, features, GUARD_MODES)],
        )
    }

    fn over(index: &'i SpecIndex, engines: Vec<Engine<'i>>) -> Self {
        Self {
            index,
            engines,
            verdicts: HashMap::default(),
        }
    }

    /// How many engines must block for a verdict to block: the powerset's size for [`GuardState::new`], and 1 for [`GuardState::under`].
    pub fn engine_count(&self) -> usize {
        self.engines.len()
    }

    /// The spec this guard reads.
    pub fn index(&self) -> &'i SpecIndex {
        self.index
    }

    /// Whether this ligature yields to its components in this window. A non-letter first slot is free without consulting an engine. Every computed verdict is memoized, because `WindowOptions` and the string replay ask for the same key many times.
    pub fn formation_blocked(
        &mut self,
        liga: Sym,
        right1: RightToken,
        right2: RightToken,
    ) -> Result<bool, SettleError> {
        if right1.kind() != TokenKind::Letter {
            return Ok(false);
        }
        let key = (liga, right1, right2);
        if let Some(&verdict) = self.verdicts.get(&key) {
            return Ok(verdict);
        }
        let (right1, right2) = match self.follower_formation(right1, right2)? {
            Some(formed) => (
                self.index
                    .letter(formed)
                    .expect("a formed ligature is a modeled rune"),
                UNKNOWN,
            ),
            None => (right1, right2),
        };
        let mut verdict = true;
        for seat in 0..self.engines.len() {
            if !Self::blocked_under(&mut self.engines[seat], liga, right1, right2)? {
                verdict = false;
                break;
            }
        }
        self.verdicts.insert(key, verdict);
        Ok(verdict)
    }

    /// The ligature the two raw slots themselves form before the guarded ligature's window settles: the modeled rune whose sequence is these two runes and whose own guard does not block. That guard is read with both slots `UNKNOWN`, which is not a letter, so it never blocks. `None` when the slots are not a forming pair, which is the common case.
    ///
    /// The engine checks then use that ligature as the follower instead of the bare first slot. The pair's formation consumes the first slot's entry, so counting it as reachable would make the guard un-form the left ligature for a join that cannot happen.
    fn follower_formation(
        &mut self,
        right1: RightToken,
        right2: RightToken,
    ) -> Result<Option<Sym>, SettleError> {
        if right1.kind() != TokenKind::Letter || right2.kind() != TokenKind::Letter {
            return Ok(None);
        }
        let pair = [right1.letter(), right2.letter()];
        let index = self.index;
        for (name, rune) in index.runes() {
            let Some(sequence) = rune.sequence.as_deref() else {
                continue;
            };
            if sequence == pair && !self.formation_blocked(*name, UNKNOWN, UNKNOWN)? {
                return Ok(Some(*name));
            }
        }
        Ok(None)
    }

    /// One configuration's verdict: blocked only when the unformed trail offers some seam toward the follower, its ranking-grain trace commits one, and the formed ligature offers none. The checks run in that order, and the first that fails makes the verdict free.
    fn blocked_under(
        engine: &mut Engine<'i>,
        liga: Sym,
        right1: RightToken,
        right2: RightToken,
    ) -> Result<bool, SettleError> {
        let index = engine.index();
        let rune = index.rune(liga).unwrap_or_else(|| {
            panic!(
                "{} is not modeled, exactly as spec.runes[…] raises KeyError",
                index.resolve(liga)
            )
        });
        let sequence = rune
            .sequence
            .as_deref()
            .expect("the guard is only ever asked about a ligature rune");
        assert!(
            sequence.len() >= 2,
            "a ligature rune's sequence names at least a lead and a trail, exactly as sequence[-2] demands"
        );
        let lead = sequence[sequence.len() - 2];
        let trail = sequence[sequence.len() - 1];
        let virtual_left = LeftContext::letter(
            index,
            Settled {
                cell: CellId {
                    rune: lead,
                    stance: index.default_stance(lead).unwrap_or_else(|| {
                        panic!(
                            "{} is not modeled, exactly as spec.runes[…] raises KeyError",
                            index.resolve(lead)
                        )
                    }),
                    entry: None,
                    exit: None,
                    adjustments: Vec::new(),
                },
                seam: None,
                extension: 0,
            },
        );
        if !engine
            .candidates(&virtual_left, trail, right1, right2, None)?
            .iter()
            .any(|candidate| candidate.seam.is_some())
        {
            return Ok(false);
        }
        if engine
            .transition_trace(
                &virtual_left,
                index
                    .letter(trail)
                    .expect("a ligature's trail is a modeled rune"),
                Slots::new(right1, right2, EDGE, EDGE),
            )?
            .settled
            .seam
            .is_none()
        {
            return Ok(false);
        }
        Ok(!engine
            .candidates(
                &LeftContext::boundary(TokenKind::Edge),
                liga,
                right1,
                right2,
                None,
            )?
            .iter()
            .any(|candidate| candidate.seam.is_some()))
    }
}

/// Every feature some capability unlock is gated on, sorted by name: the features the verdict is quantified over. The sort is on the resolved text, because symbol order is the order the dump happened to mention them in.
fn capability_features(index: &SpecIndex) -> Vec<Sym> {
    let mut seen: HashSet<Sym> = HashSet::default();
    let mut features: Vec<Sym> = Vec::new();
    for (_, rune) in index.runes() {
        for (_, stance) in &rune.stances {
            for unlock in &stance.surface.unlocks {
                if seen.insert(unlock.feature) {
                    features.push(unlock.feature);
                }
            }
        }
    }
    features.sort_by(|left, right| index.resolve(*left).cmp(index.resolve(*right)));
    features
}

/// Every `size`-element subset of `features`, in `itertools.combinations` order: lexicographic by the features' positions in `features`.
fn combinations(features: &[Sym], size: usize) -> Vec<Vec<Sym>> {
    if size == 0 {
        return vec![Vec::new()];
    }
    if size > features.len() {
        return Vec::new();
    }
    let mut out: Vec<Vec<Sym>> = Vec::new();
    for (seat, feature) in features.iter().enumerate() {
        for tail in combinations(&features[seat + 1..], size - 1) {
            let mut combination = Vec::with_capacity(size);
            combination.push(*feature);
            combination.extend(tail);
            out.push(combination);
        }
    }
    out
}

/// Every late-formation verdict, as the `guard-sweep` subcommand prints them: one tab-separated `liga right1 right2 blocked|free` row per key. Ligatures come in sorted-name order, then every modeled letter at the first raw slot, then every modeled letter followed by the four boundary kinds and `unknown` at the second.
///
/// The sweep covers every key, so one sweep covers every formation question a build can ask. The letters are every modeled rune, ligature runes included, the same alphabet the deep-slot liveness probes use.
pub fn sweep(index: &SpecIndex) -> Result<Vec<String>, SettleError> {
    sweep_with(&mut GuardState::new(index))
}

/// The same sweep in the same order for one configuration alone (`guard-sweep --config=`), so a caller can compare each configuration's verdicts with the quantified ones.
pub fn sweep_under(index: &SpecIndex, features: Vec<Sym>) -> Result<Vec<String>, SettleError> {
    sweep_with(&mut GuardState::under(index, features))
}

fn sweep_with(state: &mut GuardState<'_>) -> Result<Vec<String>, SettleError> {
    let index = state.index;
    let mut letters: Vec<Sym> = index.runes().iter().map(|(name, _)| *name).collect();
    letters.sort_by(|left, right| index.resolve(*left).cmp(index.resolve(*right)));
    let ligatures: Vec<Sym> = letters
        .iter()
        .copied()
        .filter(|name| {
            index
                .rune(*name)
                .and_then(|rune| rune.sequence.as_deref())
                .is_some_and(|sequence| !sequence.is_empty())
        })
        .collect();
    let mut right2_tokens: Vec<(String, RightToken)> = letters
        .iter()
        .map(|name| {
            (
                index.resolve(*name).to_owned(),
                index.letter(*name).expect("a modeled rune has a token"),
            )
        })
        .collect();
    right2_tokens.extend(
        TAIL_TOKENS
            .iter()
            .map(|token| (token.kind().as_str().to_owned(), *token)),
    );
    let mut lines = Vec::with_capacity(ligatures.len() * letters.len() * right2_tokens.len());
    for liga in &ligatures {
        let liga_name = index.resolve(*liga);
        for right1 in &letters {
            let right1_name = index.resolve(*right1);
            for (label, right2) in &right2_tokens {
                let blocked = state.formation_blocked(
                    *liga,
                    index.letter(*right1).expect("a modeled rune has a token"),
                    *right2,
                )?;
                let verdict = if blocked { "blocked" } else { "free" };
                lines.push(format!("{liga_name}\t{right1_name}\t{label}\t{verdict}"));
            }
        }
    }
    Ok(lines)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::fixtures;
    use crate::model::Interner;

    /// A JSON object from already-built key and value strings.
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

    fn stance(name: &str, entries: &str, exits: &str, extra: &[(&str, &str)]) -> (String, String) {
        let mut fields = vec![("entries", entries), ("exits", exits)];
        fields.extend_from_slice(extra);
        let surface = fixtures::surface(&fields);
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

    fn unlock(feature: &str) -> String {
        format!(
            r#"{{"feature":"{feature}","entry":"baseline","exit":null,"pairing":null,"when":null,"why":null,"provenance":null}}"#
        )
    }

    fn exit_unlock(feature: &str) -> String {
        format!(
            r#"{{"feature":"{feature}","entry":null,"exit":"baseline","pairing":null,"when":null,"why":null,"provenance":null}}"#
        )
    }

    fn spec_of(runes: &[(String, String)]) -> SpecIndex {
        fixtures::index_of(&fixtures::dump(
            &object(runes),
            &fixtures::four_family_registry(),
        ))
    }

    /// The little alphabet the formation tests read.
    ///
    /// `qsPea` and `qsTea` are the ligature's components, both joining at the baseline on both sides, so the unformed trail can reach a follower. `qsMay` accepts a baseline entry and has no exit, so it is a plain follower. The ligature `qsPea_qsTea` has no entry, so as a follower it cannot be reached. Its exits are the parameter: with none it yields to its components, and with a baseline exit it forms.
    fn alphabet(liga_exits: &str) -> SpecIndex {
        let baseline = object(&[row("baseline", &[])]);
        let pea = rune(
            "qsPea",
            &[stance("half", &baseline, &baseline, &[])],
            &[("codepoint", "58960")],
        );
        let tea = rune(
            "qsTea",
            &[stance("plain", &baseline, &baseline, &[])],
            &[("codepoint", "58962")],
        );
        let may = rune(
            "qsMay",
            &[stance("plain", &baseline, "{}", &[])],
            &[("codepoint", "58981")],
        );
        let liga = rune(
            "qsPea_qsTea",
            &[stance("joined", "{}", liga_exits, &[])],
            &[("sequence", &fixtures::names(&["qsPea", "qsTea"]))],
        );
        spec_of(&[pea, tea, may, liga])
    }

    /// [`alphabet`] with the ligature's baseline exit granted by an `ss03` unlock instead of declared, so the configurations disagree on its verdicts: the ligature yields with no features on and forms under ss03.
    fn alphabet_unlocking_the_ligature_exit() -> SpecIndex {
        let baseline = object(&[row("baseline", &[])]);
        let unlocks = fixtures::seq(&[&exit_unlock("ss03")]);
        let pea = rune(
            "qsPea",
            &[stance("half", &baseline, &baseline, &[])],
            &[("codepoint", "58960")],
        );
        let tea = rune(
            "qsTea",
            &[stance("plain", &baseline, &baseline, &[])],
            &[("codepoint", "58962")],
        );
        let may = rune(
            "qsMay",
            &[stance("plain", &baseline, "{}", &[])],
            &[("codepoint", "58981")],
        );
        let liga = rune(
            "qsPea_qsTea",
            &[stance(
                "joined",
                "{}",
                "{}",
                &[("unlocks", unlocks.as_str())],
            )],
            &[("sequence", &fixtures::names(&["qsPea", "qsTea"]))],
        );
        spec_of(&[pea, tea, may, liga])
    }

    fn letter(index: &SpecIndex, name: &str) -> RightToken {
        fixtures::letter(index, name)
    }

    /// One printed sweep as `(key, blocked)` pairs, the key being everything before the verdict.
    fn verdicts(lines: &[String]) -> Vec<(&str, bool)> {
        lines
            .iter()
            .map(|line| {
                let (key, verdict) = line.rsplit_once('\t').expect("a verdict line");
                (key, verdict == "blocked")
            })
            .collect()
    }

    #[test]
    fn one_configuration_answers_alone_and_the_quantified_verdict_needs_every_one_to_block() {
        let index = alphabet_unlocking_the_ligature_exit();
        let ss03 = fixtures::sym(&index, "ss03");
        let liga = fixtures::sym(&index, "qsPea_qsTea");
        let may = letter(&index, "qsMay");
        let mut locked = GuardState::under(&index, Vec::new());
        let mut unlocked = GuardState::under(&index, vec![ss03]);
        let mut quantified = GuardState::new(&index);
        assert_eq!(locked.engine_count(), 1);
        assert_eq!(unlocked.engine_count(), 1);
        assert_eq!(quantified.engine_count(), 2);
        assert!(
            locked
                .formation_blocked(liga, may, UNKNOWN)
                .expect("the verdict computes"),
            "with nothing on the ligature has no exit and yields to its trail"
        );
        assert!(
            !unlocked
                .formation_blocked(liga, may, UNKNOWN)
                .expect("the verdict computes"),
            "under ss03 the unlocked exit reaches the follower"
        );
        assert!(
            !quantified
                .formation_blocked(liga, may, UNKNOWN)
                .expect("the verdict computes"),
            "and the shipped verdict fires only where every configuration blocks"
        );
    }

    #[test]
    fn the_quantified_sweep_blocks_exactly_where_every_configuration_sweep_blocks() {
        let index = alphabet_unlocking_the_ligature_exit();
        let ss03 = fixtures::sym(&index, "ss03");
        let quantified = sweep(&index).expect("the sweep runs");
        let surfaces = [
            sweep_under(&index, Vec::new()).expect("the sweep runs"),
            sweep_under(&index, vec![ss03]).expect("the sweep runs"),
        ];
        let quantified = verdicts(&quantified);
        let surfaces = surfaces.each_ref().map(|surface| verdicts(surface));
        let mut disagreements = 0;
        for (seat, (key, blocked)) in quantified.iter().enumerate() {
            let under_each: Vec<bool> = surfaces
                .iter()
                .map(|surface| {
                    assert_eq!(surface[seat].0, *key, "every surface walks the same order");
                    surface[seat].1
                })
                .collect();
            assert_eq!(*blocked, under_each.iter().all(|blocked| *blocked), "{key}");
            if under_each[0] != under_each[1] {
                disagreements += 1;
            }
        }
        assert!(
            disagreements > 0,
            "the fixture is built so the two configurations disagree somewhere"
        );
    }

    #[test]
    fn a_ligature_yields_when_its_trail_could_reach_a_follower_it_cannot() {
        let index = alphabet("{}");
        let mut state = GuardState::new(&index);
        let liga = fixtures::sym(&index, "qsPea_qsTea");
        assert!(
            state
                .formation_blocked(liga, letter(&index, "qsMay"), UNKNOWN)
                .expect("the verdict computes")
        );
    }

    #[test]
    fn a_ligature_that_can_reach_the_follower_itself_forms() {
        let index = alphabet(&object(&[row("baseline", &[])]));
        let mut state = GuardState::new(&index);
        let liga = fixtures::sym(&index, "qsPea_qsTea");
        assert!(
            !state
                .formation_blocked(liga, letter(&index, "qsMay"), UNKNOWN)
                .expect("the verdict computes")
        );
    }

    #[test]
    fn a_non_letter_first_slot_is_free_without_consulting_an_engine() {
        let index = alphabet("{}");
        let mut state = GuardState::new(&index);
        let liga = fixtures::sym(&index, "qsPea_qsTea");
        for right1 in TAIL_TOKENS {
            assert!(
                !state
                    .formation_blocked(liga, right1, UNKNOWN)
                    .expect("the verdict computes")
            );
        }
        assert!(state.verdicts.is_empty());
    }

    #[test]
    fn a_follower_that_is_itself_a_forming_pair_is_faced_as_the_pair() {
        // A bare `qsPea` follower gives the trail a baseline entry to reach, so the ligature yields to its components. With `qsTea` behind it, the first slot is a forming pair, and the checks use `qsPea_qsTea`, which has no entry. The trail has nothing to reach, so the left ligature forms.
        let index = alphabet("{}");
        let mut state = GuardState::new(&index);
        let liga = fixtures::sym(&index, "qsPea_qsTea");
        assert!(
            state
                .formation_blocked(liga, letter(&index, "qsPea"), UNKNOWN)
                .expect("the verdict computes")
        );
        assert!(
            !state
                .formation_blocked(liga, letter(&index, "qsPea"), letter(&index, "qsTea"))
                .expect("the verdict computes")
        );
        assert_eq!(
            state
                .follower_formation(letter(&index, "qsPea"), letter(&index, "qsTea"))
                .expect("the scan runs"),
            Some(fixtures::sym(&index, "qsPea_qsTea"))
        );
        assert_eq!(
            state
                .follower_formation(letter(&index, "qsTea"), letter(&index, "qsPea"))
                .expect("the scan runs"),
            None
        );
    }

    #[test]
    fn the_guard_engines_pin_the_modes_the_shipping_world_leaves_on() {
        let shipping = EngineModes::default();
        assert!(shipping.simulated_prospect, "the shipping world simulates");
        assert!(shipping.vote_slots, "and reads a vote's slots shifted");
        let baseline = object(&[row("baseline", &[])]);
        let unlocks = fixtures::seq(&[&unlock("ss03"), &unlock("ss05")]);
        let pea = rune(
            "qsPea",
            &[stance(
                "half",
                "{}",
                &baseline,
                &[("unlocks", unlocks.as_str())],
            )],
            &[("codepoint", "58960")],
        );
        let tea = rune(
            "qsTea",
            &[stance("plain", &baseline, "{}", &[])],
            &[("codepoint", "58962")],
        );
        let index = spec_of(&[pea, tea]);
        let state = GuardState::new(&index);
        assert_eq!(state.engine_count(), 4);
        for engine in &state.engines {
            assert!(!engine.simulated_prospect());
            assert!(!engine.vote_slots());
            assert_eq!(engine.vote_deep_slot(), EDGE);
            assert!(
                !engine.trace_memo(),
                "no verdict reads a fired delta, so nothing journals"
            );
        }
    }

    #[test]
    fn the_powerset_runs_size_ascending_and_by_seat_within_a_size() {
        // `combinations` orders by position in the list it is given, not by symbol, so the list here is out of interning order.
        let mut symbols = Interner::new();
        let [three, seven, eleven] = ["ss03", "ss07", "ss11"].map(|name| symbols.intern(name));
        let features = [seven, three, eleven];
        let sizes: Vec<usize> = (0..=features.len())
            .flat_map(|size| combinations(&features, size))
            .map(|combination| combination.len())
            .collect();
        assert_eq!(sizes, [0, 1, 1, 1, 2, 2, 2, 3]);
        assert_eq!(
            combinations(&features, 2),
            [vec![seven, three], vec![seven, eleven], vec![three, eleven]]
        );
        assert!(combinations(&features, 4).is_empty());
    }

    #[test]
    fn the_quantified_features_are_the_unlock_features_in_sorted_name_order() {
        let baseline = object(&[row("baseline", &[])]);
        let unlocks = fixtures::seq(&[&unlock("ss05"), &unlock("ss03")]);
        let pea = rune(
            "qsPea",
            &[stance(
                "half",
                "{}",
                &baseline,
                &[("unlocks", unlocks.as_str())],
            )],
            &[("codepoint", "58960")],
        );
        let tea = rune(
            "qsTea",
            &[stance("plain", &baseline, "{}", &[])],
            &[("codepoint", "58962")],
        );
        let index = spec_of(&[pea, tea]);
        let features: Vec<&str> = capability_features(&index)
            .iter()
            .map(|feature| index.resolve(*feature))
            .collect();
        assert_eq!(features, ["ss03", "ss05"]);
        assert_eq!(GuardState::new(&index).engine_count(), 4);
    }

    #[test]
    fn the_sweep_walks_every_ligature_and_every_letter_then_the_boundary_tail() {
        let index = alphabet("{}");
        let lines = sweep(&index).expect("the sweep runs");
        let letters = index.rune_count();
        assert_eq!(lines.len(), letters * (letters + TAIL_TOKENS.len()));
        assert_eq!(
            lines.first().map(String::as_str),
            Some("qsPea_qsTea\tqsMay\tqsMay\tblocked")
        );
        assert_eq!(
            lines.last().map(String::as_str),
            Some("qsPea_qsTea\tqsTea\tunknown\tblocked")
        );
        assert_eq!(
            lines
                .iter()
                .filter(|line| line.ends_with("\tblocked"))
                .count(),
            26
        );
        let first_slots: Vec<&str> = lines
            .iter()
            .step_by(letters + TAIL_TOKENS.len())
            .map(|line| line.split('\t').nth(1).expect("a right1 name"))
            .collect();
        assert_eq!(first_slots, ["qsMay", "qsPea", "qsPea_qsTea", "qsTea"]);
        let labels: Vec<&str> = lines
            .iter()
            .take(letters + TAIL_TOKENS.len())
            .map(|line| line.split('\t').nth(2).expect("a right2 label"))
            .collect();
        assert_eq!(
            labels,
            [
                "qsMay",
                "qsPea",
                "qsPea_qsTea",
                "qsTea",
                "edge",
                "space",
                "zwnj",
                "namer-dot",
                "unknown",
            ]
        );
    }

    #[test]
    fn a_ligature_whose_exit_reaches_the_follower_is_free_across_the_whole_sweep() {
        let index = alphabet(&object(&[row("baseline", &[])]));
        let lines = sweep(&index).expect("the sweep runs");
        assert!(
            lines
                .iter()
                .all(|line| line.ends_with("\tfree") || line.ends_with("\tblocked"))
        );
        let blocked = lines.iter().filter(|line| line.ends_with("\tblocked"));
        assert_eq!(blocked.count(), 0);
    }
}
