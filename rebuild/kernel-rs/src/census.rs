//! The deep-slot censuses and the two slot filters. Together they answer which windows the table enumerates split by a raw third or fourth lookahead token, and therefore which windows leave those slots at `#NA`. The filters' chain arm is written here; their liveness arm is [`crate::liveness`]'s and is ORed in below.
//!
//! Two gates in series, asking different questions. The census is static and per rune: only an own-rune `prefer` or `resolve` record is ever handed the real deep slots — `settle`'s `_prefer_favors` and `_apply_resolution` discipline — so a rune none of whose records chain that far can never read them, and its windows keep `#NA` without any probing at all. The filter is per window, and it is the sharper gate: even a censused rune settles identically under every third token in a window where its chains have already answered definitely. [`Engine::cond_matches_right`] is what makes that decidable, because its `None` is exactly the verdict that consulted a slot the window does not supply — so an unknown verdict over `(right1, right2, UNKNOWN, UNKNOWN)` is the statement "this window's answer depends on the third token" and a definite `Some(_)` is the statement that it does not.
//!
//! Both worlds land here. In the pinned candidacy world — `simulated_prospect` and `vote_slots` both off, which is the deep-world verdict false — the chain arm is the whole verdict and the two censuses are exactly the depth-3 and depth-4 chain censuses. Under the deep world the raw deep tokens reach any input's window through its follower's replayed cascade or a vote's shifted slots, so both censuses widen to every rune and [`crate::liveness::ProspectLiveness`] ORs in beside the chain arm, consulted only where the chain arm said no. Which world a caller is in is its own knowledge and arrives as arguments — the `deep_world` flag of the two censuses, and a `Some(_)` liveness probe at the filters — because this crate has no environment to read module defaults out of.
//!
//! A filter is a struct with a memo, and its engine arrives per call rather than being captured. That shape is load-bearing: the fixpoint hands in the engine it settles with, because the probes share that engine's memo and its fired-pointer journal, and a second engine would silently change the `cited_provenance` the build reports. Taking `&mut Engine` per call is how the crate says that out loud — the fixpoint owns the one engine and lends it, and the borrow checker rejects the second one. The liveness probe is lent on exactly the same terms and for exactly the same reason.

use std::collections::{HashMap, HashSet};

use crate::engine::{Engine, Slots};
use crate::error::SettleError;
use crate::index::SpecIndex;
use crate::liveness::ProspectLiveness;
use crate::model::{Condition, Sym};
use crate::types::{RightToken, UNKNOWN};

/// How many raw slots past its own a right condition's `then:` chains read: a `then:` hop advances one slot, an `except:` entry tests its parent's slot so its own hops count from there rather than from one deeper, and the reach is the deepest either arm gets to.
pub fn right_chain_reach(cond: &Condition) -> usize {
    let mut reach = 0;
    if let Some(then) = cond.then.as_deref() {
        reach = reach.max(1 + right_chain_reach(then));
    }
    for excepted in &cond.except_ {
        reach = reach.max(right_chain_reach(excepted));
    }
    reach
}

/// The rune names carrying a `prefer` or `resolve` record whose right condition chains at least `reach` slots on. The two policy lists are read in one order — every `prefer`, then every `resolve` — which decides nothing here, where the answer is a set, but is the same traversal [`ThirdSlotFilter`] and [`FourthSlotFilter`] gather their chain lists in, where it decides which chain a verdict short-circuits on.
///
/// A set with no iteration order is the honest type: every reader asks it for membership, so nothing downstream can see an order to depend on.
fn deep_inputs(index: &SpecIndex, reach: usize) -> HashSet<Sym> {
    let mut out = HashSet::new();
    for (name, rune) in index.runes() {
        for record in rune.policy.prefer.iter().chain(&rune.policy.resolve) {
            if let Some(right) = record.when.right.as_ref()
                && right_chain_reach(right) >= reach
            {
                out.insert(*name);
            }
        }
    }
    out
}

/// The rune names whose windows the raw third lookahead can decide: exactly those carrying an own-rune `prefer` or `resolve` whose right condition chains two hops.
pub fn depth3_inputs(index: &SpecIndex) -> HashSet<Sym> {
    deep_inputs(index, 2)
}

/// The rune names whose windows the raw fourth lookahead can decide: a chain of three hops. Always a subset of [`depth3_inputs`], since a reach-3 chain is a reach-2 chain; both gates apply, each opening its own slot.
pub fn depth4_inputs(index: &SpecIndex) -> HashSet<Sym> {
    deep_inputs(index, 3)
}

/// The inputs whose windows can carry a live third slot — the pre-gate the fixpoint applies before asking [`ThirdSlotFilter`] for the per-window verdict.
///
/// In the pinned candidacy world only an own-rune depth-3 chain ever reads the slot, so this is exactly [`depth3_inputs`]. Under the deep world the raw third token can decide any input's window through its follower's replayed cascade or a vote's shifted slots, so every rune is admitted and all the pruning is left to the per-window probe.
pub fn third_slot_inputs(index: &SpecIndex, deep_world: bool) -> HashSet<Sym> {
    if deep_world {
        return index.runes().iter().map(|(name, _)| *name).collect();
    }
    depth3_inputs(index)
}

/// [`third_slot_inputs`] one slot deeper: the depth-4 chain census in the pinned world, every rune under the deep world.
pub fn fourth_slot_inputs(index: &SpecIndex, deep_world: bool) -> HashSet<Sym> {
    if deep_world {
        return index.runes().iter().map(|(name, _)| *name).collect();
    }
    depth4_inputs(index)
}

/// Each input's right conditions that chain at least `reach` slots on, gathered `prefer` before `resolve` and within each list in declaration order — the chain list each filter reads a window against.
///
/// Keeping only the inputs with a non-empty list is the matching census exactly, because a rune is censused at a reach precisely when some record of its qualifies at that reach. An input with no entry here can never be live on this arm.
fn chains_at<'i>(index: &'i SpecIndex, reach: usize) -> HashMap<Sym, Vec<&'i Condition>> {
    let mut out: HashMap<Sym, Vec<&'i Condition>> = HashMap::new();
    for (name, rune) in index.runes() {
        for record in rune.policy.prefer.iter().chain(&rune.policy.resolve) {
            if let Some(right) = record.when.right.as_ref()
                && right_chain_reach(right) >= reach
            {
                out.entry(*name).or_default().push(right);
            }
        }
    }
    out
}

/// Whether the raw third slot can decide an input's window — keyed on the three rune families `(input, right1, right2)`, since a window's deeper slots are what the verdict is about and its left is not read at all.
///
/// The chain arm is true exactly where some depth-3-reach `prefer` or `resolve` chain of the input's own rune is still unknown over `(right1, right2, UNKNOWN, UNKNOWN)`. `resolve` records receive all four raw slots in `_apply_resolution`, which is why they are censused beside the prefers rather than being a separate question. Where that arm says no and the caller lent a liveness probe, [`ProspectLiveness::third_live`] is the second arm — the slot also opens where some candidate shape's simulated follower choice, or some follower vote's verdict, moves with the third token.
pub struct ThirdSlotFilter<'i> {
    chains: HashMap<Sym, Vec<&'i Condition>>,
    verdicts: HashMap<(Sym, Sym, Sym), bool>,
}

impl<'i> ThirdSlotFilter<'i> {
    /// The filter over one spec's depth-3 chains.
    pub fn new(index: &'i SpecIndex) -> Self {
        Self {
            chains: chains_at(index, 2),
            verdicts: HashMap::new(),
        }
    }

    /// Whether this window carries a live third slot, memoized on the window. The engine and the liveness probe are both the fixpoint's own, lent for the probe; see the module doc for why neither is ever a second one.
    ///
    /// `liveness` is `None` exactly in the pinned world, whose deep tokens no mode can read past the chains. Where it is `Some(_)`, it is consulted only after the chain arm has said no, because the chain arm is cheap and its answer is final; a probe that never runs never fires, so consulting it any earlier would journal provenance the build never means to journal.
    pub fn matters(
        &mut self,
        engine: &mut Engine<'_>,
        liveness: Option<&mut ProspectLiveness<'_>>,
        input: Sym,
        right1: Sym,
        right2: Sym,
    ) -> Result<bool, SettleError> {
        let key = (input, right1, right2);
        if let Some(&cached) = self.verdicts.get(&key) {
            return Ok(cached);
        }
        let window = Slots::pair(RightToken::Letter(right1), RightToken::Letter(right2)).as_array();
        let mut verdict = false;
        if let Some(chains) = self.chains.get(&input) {
            for chain in chains {
                if engine
                    .cond_matches_right(Some(input), chain, &window)?
                    .is_none()
                {
                    verdict = true;
                    break;
                }
            }
        }
        if !verdict && let Some(liveness) = liveness {
            verdict = liveness.third_live(engine, input, right1, right2)?;
        }
        self.verdicts.insert(key, verdict);
        Ok(verdict)
    }

    /// How many distinct windows this filter has answered — the size of the verdict memo. The memo is contract rather than an optimization, both because a raise is deliberately not a verdict and because the liveness arm is expensive enough that a second evaluation would be a real cost, so its behavior is readable rather than assumed.
    pub fn answered(&self) -> usize {
        self.verdicts.len()
    }
}

/// Whether the raw fourth slot can decide an input's window — [`ThirdSlotFilter`] one slot deeper, keyed on `(input, right1, right2, right3)` and asking a depth-4-reach chain to be unknown over `(right1, right2, right3, UNKNOWN)`, with [`ProspectLiveness::fourth_live`] as the second arm on the same terms.
///
/// A window the third filter judges definite is definite for this one too, and the fixpoint relies on that: reach-3 chains are reach-2 chains on the chain arm, and on the liveness arm `third_live` ORs in `fourth_live` over every concrete letter third, so a dead third slot cannot hide a live fourth either way.
pub struct FourthSlotFilter<'i> {
    chains: HashMap<Sym, Vec<&'i Condition>>,
    verdicts: HashMap<(Sym, Sym, Sym, Sym), bool>,
}

impl<'i> FourthSlotFilter<'i> {
    /// The filter over one spec's depth-4 chains.
    pub fn new(index: &'i SpecIndex) -> Self {
        Self {
            chains: chains_at(index, 3),
            verdicts: HashMap::new(),
        }
    }

    /// Whether this window carries a live fourth slot at this concrete third, memoized on the window, with `liveness` lent and consulted exactly as [`ThirdSlotFilter::matters`] lends and consults it.
    pub fn matters(
        &mut self,
        engine: &mut Engine<'_>,
        liveness: Option<&mut ProspectLiveness<'_>>,
        input: Sym,
        right1: Sym,
        right2: Sym,
        right3: Sym,
    ) -> Result<bool, SettleError> {
        let key = (input, right1, right2, right3);
        if let Some(&cached) = self.verdicts.get(&key) {
            return Ok(cached);
        }
        let window = Slots::new(
            RightToken::Letter(right1),
            RightToken::Letter(right2),
            RightToken::Letter(right3),
            UNKNOWN,
        )
        .as_array();
        let mut verdict = false;
        if let Some(chains) = self.chains.get(&input) {
            for chain in chains {
                if engine
                    .cond_matches_right(Some(input), chain, &window)?
                    .is_none()
                {
                    verdict = true;
                    break;
                }
            }
        }
        if !verdict && let Some(liveness) = liveness {
            verdict = liveness.fourth_live(engine, input, right1, right2, right3)?;
        }
        self.verdicts.insert(key, verdict);
        Ok(verdict)
    }

    /// How many distinct windows this filter has answered, as [`ThirdSlotFilter::answered`].
    pub fn answered(&self) -> usize {
        self.verdicts.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::EngineModes;
    use crate::index::fixtures;

    /// A right condition testing `families[0]` at its own slot and chaining one `then:` hop per further name — the shape the censuses count hops on and the filters read a window against.
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

    /// One policy record of `kind` gated on a right condition and nothing else.
    fn record(kind: &str, right: &str) -> String {
        fixtures::record(&[
            ("kind", &fixtures::quote(kind)),
            ("when", &fixtures::when(&[("right", right)])),
        ])
    }

    /// One rune carrying `records` in the named policy list and no surface at all, which is every field these tests read.
    fn rune(name: &str, list: &str, records: &[&str]) -> String {
        fixtures::rune(
            name,
            &[(
                "policy",
                &fixtures::policy(&[(list, &fixtures::seq(records))]),
            )],
        )
    }

    fn spec_of(runes: &[(&str, &str)]) -> SpecIndex {
        fixtures::index_of(&fixtures::dump(
            &fixtures::map(runes),
            &fixtures::four_family_registry(),
        ))
    }

    /// The world the fixpoint runs in and the only one this sub-issue ports: both issue-28 flags off, so the filters' chain arm is the whole verdict.
    fn pinned(index: &SpecIndex) -> Engine<'_> {
        Engine::with_modes(
            index,
            Vec::<Sym>::new(),
            EngineModes {
                simulated_prospect: false,
                vote_slots: false,
                trace_memo: true,
                ..EngineModes::default()
            },
        )
    }

    /// A census as resolved names in sorted order, which is the only order a set of symbols may be read in.
    fn sorted(index: &SpecIndex, census: &HashSet<Sym>) -> Vec<String> {
        let mut out: Vec<String> = census
            .iter()
            .map(|name| index.resolve(*name).to_owned())
            .collect();
        out.sort();
        out
    }

    /// The census fixture. `qsPea` chains two hops off a `prefer` and `qsTea` three off a `resolve`, so one is depth-3 only and the other is both; `qsMay` chains one hop and also carries a right-less record, so neither list can admit it; and `qsIt` chains two hops off a `refuse`, which no deep slot is ever handed.
    fn census_spec() -> SpecIndex {
        let pea = rune(
            "qsPea",
            "prefer",
            &[&record("prefer", &chain(&["qsTea", "qsMay", "qsIt"]))],
        );
        let tea = rune(
            "qsTea",
            "resolve",
            &[&record(
                "resolve",
                &chain(&["qsMay", "qsIt", "qsPea", "qsTea"]),
            )],
        );
        let may = rune(
            "qsMay",
            "prefer",
            &[
                &record("prefer", &chain(&["qsTea", "qsPea"])),
                &fixtures::record(&[("kind", "\"prefer\"")]),
            ],
        );
        let it = rune(
            "qsIt",
            "refuse",
            &[&record("refuse", &chain(&["qsTea", "qsMay", "qsIt"]))],
        );
        spec_of(&[
            ("qsPea", &pea),
            ("qsTea", &tea),
            ("qsMay", &may),
            ("qsIt", &it),
        ])
    }

    #[test]
    fn a_then_hop_advances_a_slot_and_an_except_entry_counts_from_its_parents() {
        let flat = chain(&["qsTea"]);
        let two_hops = chain(&["qsTea", "qsMay", "qsIt"]);
        let excepting = fixtures::condition(&[
            ("family", &fixtures::names(&["qsTea"])),
            ("except_", &fixtures::seq(&[&two_hops])),
        ]);
        let both =
            fixtures::condition(&[("then", &flat), ("except_", &fixtures::seq(&[&two_hops]))]);
        let nested = fixtures::condition(&[("then", &excepting)]);
        let index = spec_of(&[(
            "qsPea",
            &rune(
                "qsPea",
                "refuse",
                &[
                    &record("refuse", &flat),
                    &record("refuse", &two_hops),
                    &record("refuse", &excepting),
                    &record("refuse", &both),
                    &record("refuse", &nested),
                ],
            ),
        )]);
        let reaches: Vec<usize> = index
            .rune(fixtures::sym(&index, "qsPea"))
            .expect("qsPea is modeled")
            .policy
            .refuse
            .iter()
            .map(|record| right_chain_reach(record.when.right.as_ref().expect("a right condition")))
            .collect();
        assert_eq!(
            reaches,
            [0, 2, 2, 2, 3],
            "a bare condition reaches nothing; two then: hops reach two; an except: entry's own hops count from the slot its parent tests, so nesting one under a then: is what reaches three"
        );
    }

    #[test]
    fn the_censuses_count_prefer_and_resolve_chains_and_nothing_else() {
        let index = census_spec();
        assert_eq!(
            sorted(&index, &depth3_inputs(&index)),
            ["qsPea", "qsTea"],
            "qsMay's single hop is too shallow and qsIt's chain rides a refuse, which is never handed a deep slot"
        );
        assert_eq!(sorted(&index, &depth4_inputs(&index)), ["qsTea"]);
        assert_eq!(
            sorted(&index, &third_slot_inputs(&index, false)),
            sorted(&index, &depth3_inputs(&index)),
            "the pinned world's pre-gate is the chain census itself"
        );
        assert_eq!(
            sorted(&index, &fourth_slot_inputs(&index, false)),
            sorted(&index, &depth4_inputs(&index))
        );
    }

    /// The widening is the whole of the deep world's pre-gate: every rune, at both depths, whatever its own chains reach — because a deep token reaches an uncensused input's window through its follower's replayed cascade or a vote's shifted slots, and only the per-window probe can say whether it moved anything.
    #[test]
    fn the_deep_world_admits_every_rune_at_both_depths() {
        let index = census_spec();
        let every = ["qsIt", "qsMay", "qsPea", "qsTea"];
        assert_eq!(sorted(&index, &third_slot_inputs(&index, true)), every);
        assert_eq!(sorted(&index, &fourth_slot_inputs(&index, true)), every);
        assert_eq!(third_slot_inputs(&index, true).len(), index.rune_count());
        assert!(
            fourth_slot_inputs(&index, true).is_superset(&fourth_slot_inputs(&index, false)),
            "the pinned census is a subset of the widened one, so no window the pinned world split stops splitting"
        );
    }

    #[test]
    fn a_third_slot_is_live_only_where_the_chain_still_needs_it() {
        let index = census_spec();
        let mut engine = pinned(&index);
        let mut filter = ThirdSlotFilter::new(&index);
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");
        let it = fixtures::sym(&index, "qsIt");

        assert_eq!(
            filter.matters(&mut engine, None, pea, tea, may),
            Ok(true),
            "both nearer hops matched, so the chain's third hop reads the slot the window does not supply"
        );
        assert_eq!(
            filter.matters(&mut engine, None, pea, pea, may),
            Ok(false),
            "the first hop already answered definitely, so every third token settles the same way"
        );
        assert_eq!(
            filter.matters(&mut engine, None, pea, tea, pea),
            Ok(false),
            "the second hop answered definitely and the chain never reached the third slot"
        );
        assert_eq!(
            filter.matters(&mut engine, None, tea, may, it),
            Ok(true),
            "a reach-3 chain is a reach-2 chain, so qsTea's resolve opens the third slot too"
        );
        assert_eq!(
            filter.matters(&mut engine, None, may, tea, may),
            Ok(false),
            "an uncensused input has no chains to consult and is never live on this arm"
        );
    }

    #[test]
    fn a_fourth_slot_reads_one_chain_hop_deeper_than_the_third() {
        let index = census_spec();
        let mut engine = pinned(&index);
        let mut filter = FourthSlotFilter::new(&index);
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");
        let it = fixtures::sym(&index, "qsIt");

        assert_eq!(
            filter.matters(&mut engine, None, tea, may, it, pea),
            Ok(true),
            "three matched hops leave the chain's fourth reading the unsupplied slot"
        );
        assert_eq!(
            filter.matters(&mut engine, None, tea, may, it, tea),
            Ok(false),
            "the third hop answered definitely at this concrete right3"
        );
        assert_eq!(
            filter.matters(&mut engine, None, pea, tea, may, it),
            Ok(false),
            "qsPea's chain reaches two slots, which the depth-4 census does not admit"
        );
    }

    #[test]
    fn a_verdict_is_memoized_per_window_and_a_raise_is_not_a_verdict() {
        let ghost = fixtures::condition(&[
            ("family", &fixtures::names(&["qsTea"])),
            (
                "then",
                &fixtures::condition(&[
                    ("klass", &fixtures::names(&["nowhere-class"])),
                    ("then", &chain(&["qsIt"])),
                ]),
            ),
        ]);
        let index = spec_of(&[(
            "qsPea",
            &rune("qsPea", "prefer", &[&record("prefer", &ghost)]),
        )]);
        let mut engine = pinned(&index);
        let mut filter = ThirdSlotFilter::new(&index);
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");

        assert_eq!(filter.answered(), 0);
        assert_eq!(filter.matters(&mut engine, None, pea, may, tea), Ok(false));
        assert_eq!(filter.answered(), 1);
        assert_eq!(
            filter.matters(&mut engine, None, pea, may, tea),
            Ok(false),
            "the second ask reads the memo"
        );
        assert_eq!(filter.answered(), 1, "and records no second window");
        assert_eq!(filter.matters(&mut engine, None, pea, pea, tea), Ok(false));
        assert_eq!(
            filter.answered(),
            2,
            "a different window is a different key"
        );

        let raised = filter.matters(&mut engine, None, pea, tea, may);
        assert_eq!(
            raised,
            Err(SettleError::Plain(
                "unknown class or group: 'nowhere-class'".to_owned()
            )),
            "the second hop resolves its class only against a letter token"
        );
        assert_eq!(
            filter.answered(),
            2,
            "a raise leaves the memo alone, so the next ask raises again rather than answering"
        );
    }
}
