//! The deep-slot censuses and the chain arm of the two slot filters, `rebuild/pipeline/table.py`'s `right_chain_reach`, `_deep_inputs` / `depth3_inputs` / `depth4_inputs`, `third_slot_inputs` / `fourth_slot_inputs`, and the chain half of `third_slot_filter` / `fourth_slot_filter`. Together they answer which windows the table enumerates split by a raw third or fourth lookahead token, and therefore which windows leave those slots at `#NA`.
//!
//! Two gates in series, asking different questions. The census is static and per rune: only an own-rune `prefer` or `resolve` record is ever handed the real deep slots — `settle`'s `_prefer_favors` and `_apply_resolution` discipline — so a rune none of whose records chain that far can never read them, and its windows keep `#NA` without any probing at all. The filter is per window, and it is the sharper gate: even a censused rune settles identically under every third token in a window where its chains have already answered definitely. [`Engine::cond_matches_right`] is what makes that decidable, because its `None` is exactly the verdict that consulted a slot the window does not supply — so an unknown verdict over `(right1, right2, UNKNOWN, UNKNOWN)` is the statement "this window's answer depends on the third token" and a definite `Some(_)` is the statement that it does not.
//!
//! This is the pinned candidacy world's half, `simulated_prospect` and `vote_slots` both off, which is `table._deep_world` false. There the chain arm is the whole verdict and the two censuses are exactly the depth-3 and depth-4 chain censuses. The `_ProspectLiveness` arm that ORs in beside the chain arm, and the widening of both censuses to every rune, are sub-issue #45's; each of the four places they land is named in the doc or marked in the code below, and none of them is written here, because an engine that could reach them cannot yet be built by this crate's verb.
//!
//! A filter is a struct with a memo where Python has a closure over a `verdicts` dict, and its engine arrives per call rather than being captured. That is the one deliberate shape change and it is load-bearing: Python's `third_slot_filter(spec, features, engine=None)` builds its own engine when the caller passes none, and the table build never takes that branch — it hands in the engine it settles with, because the probes share that engine's memo and, once #45's liveness arm replays whole transitions, its fired-pointer journal too, and a second engine would silently change the `cited_provenance` the build reports. Taking `&mut Engine` per call is how the port says that out loud: the fixpoint owns the one engine and lends it, and the borrow checker rejects the second one Python only discourages by convention.

use std::collections::{HashMap, HashSet};

use crate::engine::{Engine, Slots};
use crate::error::SettleError;
use crate::index::SpecIndex;
use crate::model::{Condition, Sym};
use crate::types::{RightToken, UNKNOWN};

/// How many raw slots past its own a right condition's `then:` chains read, `table.right_chain_reach`: a `then:` hop advances one slot, an `except:` entry tests its parent's slot so its own hops count from there rather than from one deeper, and the reach is the deepest either arm gets to.
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

/// The rune names carrying a `prefer` or `resolve` record whose right condition chains at least `reach` slots on, `table._deep_inputs`. The two policy lists are read in Python's order — every `prefer`, then every `resolve` — which decides nothing here, where the answer is a set, but is the same traversal [`ThirdSlotFilter`] and [`FourthSlotFilter`] gather their chain lists in, where it decides which chain a verdict short-circuits on.
///
/// A set with no iteration order is the honest type: Python's is a `frozenset` and every reader asks it for membership, so nothing downstream can see an order to depend on.
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

/// The rune names whose windows the raw third lookahead can decide, `table.depth3_inputs`: exactly those carrying an own-rune `prefer` or `resolve` whose right condition chains two hops.
pub fn depth3_inputs(index: &SpecIndex) -> HashSet<Sym> {
    deep_inputs(index, 2)
}

/// The rune names whose windows the raw fourth lookahead can decide, `table.depth4_inputs`: a chain of three hops. Always a subset of [`depth3_inputs`], since a reach-3 chain is a reach-2 chain; both gates apply, each opening its own slot.
pub fn depth4_inputs(index: &SpecIndex) -> HashSet<Sym> {
    deep_inputs(index, 3)
}

/// The inputs whose windows can carry a live third slot, `table.third_slot_inputs` — the pre-gate the fixpoint applies before asking [`ThirdSlotFilter`] for the per-window verdict.
///
/// In the pinned candidacy world only an own-rune depth-3 chain ever reads the slot, so this is exactly [`depth3_inputs`]. Under `table._deep_world` the raw third token can decide any input's window through its follower's replayed cascade or a vote's shifted slots, and the census widens to every rune with all the pruning left to the per-window probe; that arm is sub-issue #45's and lands as the other half of this function once an engine may run unpinned.
pub fn third_slot_inputs(index: &SpecIndex) -> HashSet<Sym> {
    depth3_inputs(index)
}

/// [`third_slot_inputs`] one slot deeper, `table.fourth_slot_inputs`: the depth-4 chain census in the pinned world, and every rune under `table._deep_world` once sub-issue #45 lands that arm.
pub fn fourth_slot_inputs(index: &SpecIndex) -> HashSet<Sym> {
    depth4_inputs(index)
}

/// Each input's right conditions that chain at least `reach` slots on, gathered `prefer` before `resolve` and within each list in declaration order — the `chains` dict each Python filter closure builds.
///
/// Python keys that dict on the matching census and re-derives the same per-rune tuple inside the comprehension; keeping only the inputs with a non-empty list is that exactly, because a rune is censused at a reach precisely when some record of its qualifies at that reach. An input with no entry can never be live on this arm, which is what `chains.get(input_family, ())` says on the Python side.
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

/// Whether the raw third slot can decide an input's window, `table.third_slot_filter` — keyed on the three rune families `(input, right1, right2)`, since a window's deeper slots are what the verdict is about and its left is not read at all.
///
/// The chain arm is true exactly where some depth-3-reach `prefer` or `resolve` chain of the input's own rune is still unknown over `(right1, right2, UNKNOWN, UNKNOWN)`. `resolve` records receive all four raw slots in `_apply_resolution`, which is why they are censused beside the prefers rather than being a separate question.
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

    /// Whether this window carries a live third slot, memoized on the window. The engine is the fixpoint's own, lent for the probe; see the module doc for why it is never a second one.
    pub fn matters(
        &mut self,
        engine: &mut Engine<'_>,
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
        // SEAM (sub-issue #45): `_ProspectLiveness.third_live` ORs in here, consulted only when the chain arm said no and only when the engine is unpinned.
        self.verdicts.insert(key, verdict);
        Ok(verdict)
    }

    /// How many distinct windows this filter has answered — the size of the memo Python keeps in its closure's `verdicts` dict. The memo is contract rather than an optimization, both because a raise is deliberately not a verdict and because the arm sub-issue #45 adds is expensive enough that a second evaluation would be a real cost, so its behavior is readable rather than assumed.
    pub fn answered(&self) -> usize {
        self.verdicts.len()
    }
}

/// Whether the raw fourth slot can decide an input's window, `table.fourth_slot_filter` — [`ThirdSlotFilter`] one slot deeper, keyed on `(input, right1, right2, right3)` and asking a depth-4-reach chain to be unknown over `(right1, right2, right3, UNKNOWN)`.
///
/// A window the third filter judges definite is definite for this one too, and the fixpoint relies on that: reach-3 chains are reach-2 chains, so a dead third slot cannot hide a live fourth on this arm.
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

    /// Whether this window carries a live fourth slot at this concrete third, memoized on the window.
    pub fn matters(
        &mut self,
        engine: &mut Engine<'_>,
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
        // SEAM (sub-issue #45): `_ProspectLiveness.fourth_live` ORs in here, on the same terms as the third filter's arm.
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
            sorted(&index, &third_slot_inputs(&index)),
            sorted(&index, &depth3_inputs(&index)),
            "the pinned world's pre-gate is the chain census itself"
        );
        assert_eq!(
            sorted(&index, &fourth_slot_inputs(&index)),
            sorted(&index, &depth4_inputs(&index))
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
            filter.matters(&mut engine, pea, tea, may),
            Ok(true),
            "both nearer hops matched, so the chain's third hop reads the slot the window does not supply"
        );
        assert_eq!(
            filter.matters(&mut engine, pea, pea, may),
            Ok(false),
            "the first hop already answered definitely, so every third token settles the same way"
        );
        assert_eq!(
            filter.matters(&mut engine, pea, tea, pea),
            Ok(false),
            "the second hop answered definitely and the chain never reached the third slot"
        );
        assert_eq!(
            filter.matters(&mut engine, tea, may, it),
            Ok(true),
            "a reach-3 chain is a reach-2 chain, so qsTea's resolve opens the third slot too"
        );
        assert_eq!(
            filter.matters(&mut engine, may, tea, may),
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
            filter.matters(&mut engine, tea, may, it, pea),
            Ok(true),
            "three matched hops leave the chain's fourth reading the unsupplied slot"
        );
        assert_eq!(
            filter.matters(&mut engine, tea, may, it, tea),
            Ok(false),
            "the third hop answered definitely at this concrete right3"
        );
        assert_eq!(
            filter.matters(&mut engine, pea, tea, may, it),
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
        assert_eq!(filter.matters(&mut engine, pea, may, tea), Ok(false));
        assert_eq!(filter.answered(), 1);
        assert_eq!(
            filter.matters(&mut engine, pea, may, tea),
            Ok(false),
            "the second ask reads the memo"
        );
        assert_eq!(filter.answered(), 1, "and records no second window");
        assert_eq!(filter.matters(&mut engine, pea, pea, tea), Ok(false));
        assert_eq!(
            filter.answered(),
            2,
            "a different window is a different key"
        );

        let raised = filter.matters(&mut engine, pea, tea, may);
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
