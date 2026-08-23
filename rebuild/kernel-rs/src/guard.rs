//! The section 5.7 late-formation guard, and the only home the verdict has: whether a ligature yields to its components in one window because the trailing component, left unformed, would realize a seam toward the follower while the formed ligature could realize none. The trail side is settled at ranking grain — a full [`Engine::transition_trace`] with the lead's default unjoined stance as its left, so follower votes and the runes' prefers count and not only candidacy — while the ligature side is kept generously at candidacy grain with the run edge as its left.
//!
//! The verdict is a pure function of the ligature and the two raw slots past its sequence, which is the whole reason it can compile into the formation lookup the font ships: that lookup stages before the stylistic-set marker substitutions and is therefore config-blind, so the verdict is quantified over the powerset of capability-unlock features and fires only where every configuration agrees. The engines that answer it are dedicated ones with both issue-28 flags pinned off and every slot past the two the verdict is keyed on bound to the window edge — `vote_deep_slot` at [`EDGE`], plus `EDGE` in the trace's third and fourth slots — so a vote or a prefer that would need deeper raw text to fire definitively can never flip a formation verdict as a side effect of a settlement-scoring change. Whether the guard should ever follow either flag is its own reviewed change with its own flip inventory, which is why the pins live here rather than being read off the engine defaults.
//!
//! [`GuardState`] is ordinary per-spec state, built once and kept: the powerset lives on it rather than in a cache beside it. `settle.form_ligatures` reads its verdicts from the whole swept mapping, which `kernel_exec.guard_sweep` memoizes per spec identity, so one process sweeps one spec once however many texts it forms. The engines are plain ones — no trace memo, so nothing journals and [`Engine::candidates`] runs uncached — because no verdict reads a fired delta.
//!
//! One structural note: [`GuardState::follower_formation`] is answered once up front rather than inside the per-engine loop. It reads the spec and the two slots and nothing of the engine, so the answer is the same either way, and hoisting it is what lets the verdict memo and the engines be borrowed apart.

use std::collections::{HashMap, HashSet};

use crate::engine::{Engine, EngineModes, Slots};
use crate::error::SettleError;
use crate::index::SpecIndex;
use crate::model::Sym;
use crate::types::{
    CellId, EDGE, LeftContext, NAMER_DOT, RightToken, SPACE, Settled, TokenKind, UNKNOWN, ZWNJ,
};

/// The non-letter second slots the sweep walks after the letters, in the order it prints them. `right1` has no such tail because a non-letter first slot short-circuits the verdict to free before any engine runs.
const TAIL_TOKENS: [RightToken; 5] = [EDGE, SPACE, ZWNJ, NAMER_DOT, UNKNOWN];

/// One verdict's identity: the ligature under formation and the two raw slots past its sequence. Nothing else can reach a verdict, which is exactly the property the emitted lookup depends on.
type VerdictKey = (Sym, RightToken, RightToken);

/// One spec's guard state: the capability-feature powerset as engines, and the verdicts they have already agreed on.
pub struct GuardState<'i> {
    index: &'i SpecIndex,
    engines: Vec<Engine<'i>>,
    verdicts: HashMap<VerdictKey, bool>,
}

impl<'i> GuardState<'i> {
    /// The guard's engines for one spec: one per subset of the capability-unlock features, in `itertools.combinations` order — subset sizes ascending, and within a size the features in sorted-name order — with the two issue-28 flags off and the vote's deep slot pinned to the window edge.
    pub fn new(index: &'i SpecIndex) -> Self {
        let modes = EngineModes {
            vote_deep_slot: EDGE,
            simulated_prospect: false,
            vote_slots: false,
            trace_memo: false,
            explain_ladder: true,
        };
        let features = capability_features(index);
        let mut engines = Vec::new();
        for size in 0..=features.len() {
            for combination in combinations(&features, size) {
                engines.push(Engine::with_modes(index, combination, modes));
            }
        }
        Self {
            index,
            engines,
            verdicts: HashMap::new(),
        }
    }

    /// How many feature configurations a verdict has to survive — the powerset's size, which is what makes the verdict config-blind.
    pub fn engine_count(&self) -> usize {
        self.engines.len()
    }

    /// Whether this ligature yields to its components in this window. A non-letter first slot is free without consulting an engine, and every computed verdict is remembered because the sweep asks for each one many times over.
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
            Some(formed) => (RightToken::Letter(formed), UNKNOWN),
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

    /// The ligature the two raw slots will themselves have formed by the time the guarded rule's own window settles — the modeled rune whose sequence is exactly these two runes and whose own guard, read with its slots unknown-optimistic, does not block. `None` when the slots are not a forming pair, and that is the common case.
    ///
    /// Both tests then face that ligature rather than the bare first slot, because a follower whose entry the pair's own formation is about to consume must not count as reachable: else the guard un-forms the left ligature in service of a seam the settled world cannot contain.
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

    /// One configuration's verdict. Three reads in order, each of which can settle the question free: the unformed trail must offer some seam toward the follower, its ranking-grain trace must actually commit one, and the formed ligature must offer none.
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
        let virtual_left = LeftContext::letter(Settled {
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
        });
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
                RightToken::Letter(trail),
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

/// Every feature some capability unlock is gated on, in sorted-name order — the axes the verdict is quantified over. Sorting is by the resolved text and not by symbol, because interning order is an accident of what the dump mentioned first.
fn capability_features(index: &SpecIndex) -> Vec<Sym> {
    let mut seen: HashSet<Sym> = HashSet::new();
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

/// Every `size`-element subset of `features`, in `itertools.combinations` order: the subsets ordered by the seats they take, so a subset containing an earlier feature comes before every subset that does not.
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

/// The whole late-formation surface as the `guard-sweep` verb prints it: one tab-separated `liga right1 right2 blocked|free` row per triple, ligatures in sorted-name order, then every modeled letter at the first raw slot, then every modeled letter followed by the four boundary kinds and `unknown` at the second.
///
/// The surface is exhaustively enumerable rather than sampled, which is what lets one sweep answer every formation question a build can ask, with no sampling to argue about. The letter vocabulary is every modeled rune, ligature runes included — the same alphabet the deep-slot liveness probes sweep.
pub fn sweep(index: &SpecIndex) -> Result<Vec<String>, SettleError> {
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
        .map(|name| (index.resolve(*name).to_owned(), RightToken::Letter(*name)))
        .collect();
    right2_tokens.extend(
        TAIL_TOKENS
            .iter()
            .map(|token| (token.kind().as_str().to_owned(), *token)),
    );
    let mut state = GuardState::new(index);
    let mut lines = Vec::with_capacity(ligatures.len() * letters.len() * right2_tokens.len());
    for liga in &ligatures {
        let liga_name = index.resolve(*liga);
        for right1 in &letters {
            let right1_name = index.resolve(*right1);
            for (label, right2) in &right2_tokens {
                let blocked =
                    state.formation_blocked(*liga, RightToken::Letter(*right1), *right2)?;
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

    /// A JSON object over already-built pieces, for the mappings the fixtures compose rather than spell.
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

    fn spec_of(runes: &[(String, String)]) -> SpecIndex {
        fixtures::index_of(&fixtures::dump(
            &object(runes),
            &fixtures::four_family_registry(),
        ))
    }

    /// The little alphabet the formation tests read.
    ///
    /// `qsPea` and `qsTea` are the ligature's components, both of them joining at the baseline on both sides, so the trail left unformed can reach a follower. `qsMay` accepts a baseline entry and offers no exit, so it is a plain follower. The ligature `qsPea_qsTea` accepts nothing — which is what makes it a follower whose entry its own formation consumes — and its exit surface is the parameter: with none it yields to its components, with a baseline one it forms.
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

    fn letter(index: &SpecIndex, name: &str) -> RightToken {
        RightToken::Letter(fixtures::sym(index, name))
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
        // A bare `qsPea` follower leaves the trail a baseline entry to reach, so the ligature yields to its components. The same first slot with `qsTea` behind it is a forming pair, and both tests then face `qsPea_qsTea`, whose entry that formation consumes — so there is nothing left for the trail to reach and the left ligature keeps its formation.
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
        // Seats, not symbols: `capability_features` hands over the sorted list, and this is the arithmetic that walks it.
        let features = [Sym(7), Sym(3), Sym(11)];
        let sizes: Vec<usize> = (0..=features.len())
            .flat_map(|size| combinations(&features, size))
            .map(|combination| combination.len())
            .collect();
        assert_eq!(sizes, [0, 1, 1, 1, 2, 2, 2, 3]);
        assert_eq!(
            combinations(&features, 2),
            [
                vec![Sym(7), Sym(3)],
                vec![Sym(7), Sym(11)],
                vec![Sym(3), Sym(11)],
            ]
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
