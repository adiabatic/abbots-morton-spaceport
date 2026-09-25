//! The deep-slot censuses and the two slot filters. Together they decide which windows the table enumerates split by a raw third or fourth lookahead token, and so which windows leave those slots at `#NA`. The filters' chain branch is here; their liveness branch is in [`crate::liveness`] and is ORed in below.
//!
//! The two checks run in series and answer different questions. The census is static and per rune. Only a rune's own `prefer` or `resolve` records receive the real deep slots (`Engine::prefer_favors` and `Engine::apply_resolution`), so a rune with no record chaining that far can never read them, and its windows keep `#NA` without any probing. The filter is per window and more precise: even a censused rune settles the same under every third token in a window where its chains have already answered definitely. [`Engine::cond_matches_right`] makes this decidable, because it returns `None` only when the verdict consulted a slot the window does not supply. So `None` over `(right1, right2, UNKNOWN, UNKNOWN)` means this window's answer depends on the third token, and `Some(_)` means it does not.
//!
//! Both worlds are handled here. In the pinned world (`simulated_prospect` and `vote_slots` both off, so `deep_world` is false), the chain branch is the whole verdict and the two censuses are the depth-3 and depth-4 chain censuses. In the deep world, the raw deep tokens reach any input's window through the follower's replayed settlement or a vote's shifted slots. Both censuses then widen to every rune, and [`crate::liveness::ProspectLiveness`] is consulted wherever the chain branch says no. The caller passes the world in, as the `deep_world` flag of the two censuses and as a `Some(_)` liveness probe at the filters, because this crate has no environment to read defaults from.
//!
//! A filter is a struct with a memo, and it takes the engine per call instead of holding one. The fixpoint passes in the engine it settles with, because the probes share that engine's memo and its fired-pointer journal. A second engine would change the `cited_provenance` the build reports without any error. Taking `&mut Engine` per call makes this explicit: the fixpoint owns the one engine and lends it, and the borrow checker rejects a second mutable borrow. The liveness probe is lent the same way for the same reason.

use crate::engine::{Engine, Slots};
use crate::error::SettleError;
use crate::hash::{HashMap, HashSet};
use crate::index::SpecIndex;
use crate::liveness::ProspectLiveness;
use crate::model::{Condition, Sym};
use crate::types::UNKNOWN;

/// How many raw slots past its own a right condition's `then:` chains read. A `then:` hop advances one slot. An `except:` entry tests its parent's slot, so its own hops count from there. The reach is the deepest either branch gets to.
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

/// The rune names with a `prefer` or `resolve` record whose right condition chains at least `reach` slots on. The records are read every `prefer`, then every `resolve`. That order does not affect this set, but it is the order in which [`ThirdSlotFilter`] and [`FourthSlotFilter`] gather their chain lists, where it decides which chain a verdict short-circuits on.
///
/// The result is an unordered set because every reader only tests membership, so nothing downstream can depend on an order.
fn deep_inputs(index: &SpecIndex, reach: usize) -> HashSet<Sym> {
    let mut out = HashSet::default();
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

/// The rune names whose windows the raw third lookahead can decide: those with an own-rune `prefer` or `resolve` whose right condition chains at least two hops.
pub fn depth3_inputs(index: &SpecIndex) -> HashSet<Sym> {
    deep_inputs(index, 2)
}

/// The rune names whose windows the raw fourth lookahead can decide: a chain of at least three hops. Always a subset of [`depth3_inputs`], since a reach-3 chain is also a reach-2 chain. Both checks apply, each opening its own slot.
pub fn depth4_inputs(index: &SpecIndex) -> HashSet<Sym> {
    deep_inputs(index, 3)
}

/// The inputs whose windows can have a live third slot. The fixpoint applies this before asking [`ThirdSlotFilter`] about each window.
///
/// In the pinned world only an own-rune depth-3 chain reads the slot, so this is [`depth3_inputs`]. In the deep world the raw third token can decide any input's window through the follower's replayed settlement or a vote's shifted slots, so every rune is admitted and the per-window probe does all the pruning.
pub fn third_slot_inputs(index: &SpecIndex, deep_world: bool) -> HashSet<Sym> {
    if deep_world {
        return index.runes().iter().map(|(name, _)| *name).collect();
    }
    depth3_inputs(index)
}

/// [`third_slot_inputs`] one slot deeper: the depth-4 chain census in the pinned world, every rune in the deep world.
pub fn fourth_slot_inputs(index: &SpecIndex, deep_world: bool) -> HashSet<Sym> {
    if deep_world {
        return index.runes().iter().map(|(name, _)| *name).collect();
    }
    depth4_inputs(index)
}

/// Each input's right conditions that chain at least `reach` slots on, `prefer` before `resolve` and in declaration order within each list. Each filter reads a window against this list.
///
/// The inputs with a non-empty list are the census at the same reach, because both apply the same test to the same records. An input with no entry here is never live on the chain branch.
fn chains_at<'i>(index: &'i SpecIndex, reach: usize) -> HashMap<Sym, Vec<&'i Condition>> {
    let mut out: HashMap<Sym, Vec<&'i Condition>> = HashMap::default();
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

/// Whether the raw third slot can decide an input's window, keyed on the three rune families `(input, right1, right2)`. The window's left is not read.
///
/// The chain branch is true where some depth-3-reach `prefer` or `resolve` chain of the input's own rune is still unknown over `(right1, right2, UNKNOWN, UNKNOWN)`. `resolve` records receive all four raw slots in `Engine::apply_resolution`, so they are censused with the prefers. Where the chain branch says no and the caller passed a liveness probe, [`ProspectLiveness::third_live`] is the second branch: the slot is also live where some candidate's simulated follower choice, or some follower vote's verdict, changes with the third token.
pub struct ThirdSlotFilter<'i> {
    chains: HashMap<Sym, Vec<&'i Condition>>,
    verdicts: HashMap<(Sym, Sym, Sym), bool>,
}

impl<'i> ThirdSlotFilter<'i> {
    /// The filter over one spec's depth-3 chains.
    pub fn new(index: &'i SpecIndex) -> Self {
        Self {
            chains: chains_at(index, 2),
            verdicts: HashMap::default(),
        }
    }

    /// Whether this window has a live third slot, memoized per window. The engine and the liveness probe are the fixpoint's own, lent for the call; the module doc says why neither may be a second instance.
    ///
    /// `liveness` is `None` only in the pinned world, where no mode reads deep tokens beyond the chains. When it is `Some(_)`, it is consulted only after the chain branch says no, because the chain branch is cheap and its answer is final. A probe that does not run fires nothing, so consulting it earlier would journal provenance the build should not record.
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
        let letter = |rune: Sym| {
            engine
                .index()
                .letter(rune)
                .expect("the filter is asked about registered runes")
        };
        let window = Slots::pair(letter(right1), letter(right2)).as_array();
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

    /// The number of distinct windows this filter has answered, which is the size of its verdict memo. Tests use it to check that a raise is not memoized and that a repeated window is answered from the memo. The memo matters because the liveness branch is expensive to evaluate twice.
    pub fn answered(&self) -> usize {
        self.verdicts.len()
    }
}

/// Whether the raw fourth slot can decide an input's window: [`ThirdSlotFilter`] one slot deeper, keyed on `(input, right1, right2, right3)`. The chain branch asks a depth-4-reach chain to be unknown over `(right1, right2, right3, UNKNOWN)`, and [`ProspectLiveness::fourth_live`] is the second branch on the same terms.
///
/// A window the third filter finds definite is definite for this one too, and the fixpoint relies on that. On the chain branch, a reach-3 chain is also a reach-2 chain. On the liveness branch, `third_live` ORs in `fourth_live` over every concrete letter in the third slot. So a dead third slot cannot hide a live fourth.
pub struct FourthSlotFilter<'i> {
    chains: HashMap<Sym, Vec<&'i Condition>>,
    verdicts: HashMap<(Sym, Sym, Sym, Sym), bool>,
}

impl<'i> FourthSlotFilter<'i> {
    /// The filter over one spec's depth-4 chains.
    pub fn new(index: &'i SpecIndex) -> Self {
        Self {
            chains: chains_at(index, 3),
            verdicts: HashMap::default(),
        }
    }

    /// Whether this window has a live fourth slot at this concrete third, memoized per window. `liveness` is lent and consulted as in [`ThirdSlotFilter::matters`].
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
        let letter = |rune: Sym| {
            engine
                .index()
                .letter(rune)
                .expect("the filter is asked about registered runes")
        };
        let window = Slots::new(letter(right1), letter(right2), letter(right3), UNKNOWN).as_array();
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

    /// A right condition that tests `families[0]` at its own slot and chains one `then:` hop per further name. The censuses count these hops, and the filters read a window against them.
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

    /// One rune with `records` in the named policy list and no join surface. These tests read no other field.
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

    /// An engine in the pinned world: `simulated_prospect` and `vote_slots` both off, so the filters' chain branch is the whole verdict.
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

    /// A census as resolved names in sorted order, since a set of symbols has no order of its own.
    fn sorted(index: &SpecIndex, census: &HashSet<Sym>) -> Vec<String> {
        let mut out: Vec<String> = census
            .iter()
            .map(|name| index.resolve(*name).to_owned())
            .collect();
        out.sort();
        out
    }

    /// The census fixture. `qsPea` chains two hops off a `prefer` and `qsTea` three off a `resolve`, so `qsPea` is in the depth-3 census only and `qsTea` is in both. `qsMay` chains one hop and also has a record with no right condition, so neither census admits it. `qsIt` chains two hops off a `refuse`, which never receives a deep slot.
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

    /// In the deep world the pre-check admits every rune at both depths, whatever its own chains reach. A deep token reaches an uncensused input's window through the follower's replayed settlement or a vote's shifted slots, and only the per-window probe can tell whether it changed anything.
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
