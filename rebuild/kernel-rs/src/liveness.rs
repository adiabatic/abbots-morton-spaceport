//! The liveness branch of the two deep-slot filters: whether a raw third or fourth lookahead token can change the settled outcome of some reachable window at `(input, right1, right2)`. [`crate::deep_slots`] holds the chain branch and calls in here only where the chain branch says no. In the pinned mode set the chain branch is the whole verdict.
//!
//! The check has two stages because cheaper checks open far too many windows. Tracking which slots the recursion consults opens nearly everything, since it consults slots past the window almost everywhere. Stopping at follower-prospect variance still opens many times as many as needed: an instrumented run on the real spec found that most consulted triples with a prospect some token changes never change a replay outcome, and that the extra windows push the emitted settlement lookup's subtable-offset headroom below the floor that read-back checks (`SUBTABLE_OFFSET_HEADROOM_FLOOR` in `rebuild/pipeline/readback.py`).
//!
//! Stage one is a cheap prefilter. For each `(stance, junction)` shape the input can commit, it evaluates the follower's simulated prospect for each concrete token and compares it with the value at `EDGE`, which is what a dead slot is given. The synthetic left's entry is never read, so entry states collapse. If nothing varies, the token has no way into the input letter's ranking: a deep token reaches settlement only through prospect values, follower prefers, and own-rune chains, and the chain branch already covers the chains.
//!
//! Stage two runs only where stage one fired. It replays the input letter's own transition for each token over the representative lefts (the four boundary kinds, then one synthetic left per distinct left-condition signature) and reports the slot live only where some representative left's settled cell changes.
//!
//! The left-condition signature that picks the representative lefts is `(junction, verdicts)`. The verdicts are [`Engine::cond_matches_left`] over the follower's own left-reading conditions, in the order [`ProspectLiveness::left_conditions`] gathers them. They are plain booleans, unlike the three-valued answer of a right condition, because a left is always already settled or a known boundary. The synthetic left is `CellId(rune=family, stance=stance, entry=None, exit=junction, adjustments=())` inside a `Settled` at that junction with no extension. Extend and contract records change only adjustments, and neither an extension nor the left cell's entry interacts with a deep token, so these shapes cover every reachable settled left.
//!
//! In stage two, a representative left whose baseline window raises E-UNACCEPTED-EXIT or a plain settlement error is one the fixpoint cannot reach, and it is skipped. A prefer conflict that raises E-INCOMPARABLE or E-AMBIGUOUS marks the slot live instead, so the enumeration reports the conflict instead of hiding it behind a dead slot. [`crate::error::SettleError`] says how its variants map to these outcomes.
//!
//! With shifted follower prefer slots on, stage one also has a follower prefer branch, which calls [`Engine::probe_prefer_favors`] with the follower's `prefer` records. A follower prefer reads the deep slots in two ways: through its record's shifted `when:` chain, and through the follower-cell enumeration the follower prefer runs over the shifted window. So a row scope or closure verdict that changes with the token changes which continuations the follower prefer can favor. The follower prefer branch is skipped when the follower is the input's own family, because `prefer_favors` then takes its own-rune branch, which the chain branch covers. It is also skipped when the follower has no `prefer` records.
//!
//! [`ProspectLiveness::third_live`] also ORs in [`ProspectLiveness::fourth_live`] over every concrete letter in the third slot. This is the fourth-slot fallback. Without it, a live fourth slot behind an unenumerated third would never be consulted. The per-token comparisons alone cannot see an input letter whose cell changes only under a specific `(third, fourth)` letter pair, because the optimistic reading of unknown slots ends the recursion the same way for an `EDGE` fourth and an `UNKNOWN` one. The recorded counterexample is `·See·No·No·Roe·No·Oy`: input `qsNo`, window `(qsNo, qsRoe, qsNo, qsOy)`, left `·See`. The fourth-slot `·Oy` changes the input letter's cell through two levels of simulation, while every probe with an `EDGE` or `UNKNOWN` fourth agrees.
//!
//! Evaluation order affects the output. Every probe records the pointers it fires in `Engine::fired`, which the fixpoint reports as the product's `cited_provenance`, and a probe that never runs fires nothing. So each short-circuit, early return, loop order, and memo key must stay as it is. The prospect and follower prefer branches key their memos on the left-condition signature instead of the input family, so two families with the same signature share one verdict and run its probes once. The input replay and the fourth-slot fallback key on the family.
//!
//! One instance serves a whole fixpoint run and is lent to both filters and to [`crate::fiber::DeepFiberDeriver`]. It holds no engine. Every call takes the caller's engine, so the probes share its trace memo and fired set. The memos are not keyed on engine modes, so every call on one instance must pass the same engine.
//!
//! A delta configuration reads `default`'s probes beside `default`'s windows ([`crate::memo`]). Each probe a verdict memo stores (the third and fourth slots' prospect, follower prefer and input replay probes) is a function of its [`VerdictKey`]: the family that asked, its probe shape, and the slots. An engine that publishes its verdicts ([`Engine::publish_verdicts`], which the table build sets on `default` when some delta reads its memo) captures each probe it runs whole, with the step it stopped at and, for each delta's unlocking-rune set, what it fired outside the asks that name one of those runes, unless the key names one or those other asks read one ([`Engine::end_verdict`]), and the snapshot it returns carries them. A delta's engine looks each probe up there under its own unlocking-rune set ([`Engine::shared_verdict`]), and where `default` published the verdict for that set, runs the probe partially: only the asks that name one of its unlocking runes, which settle and memoize the windows naming those runes as the whole probe would, while every other ask before `default`'s stop is one the shared memo answers entirely ([`Partial`] gives the argument). Where every ask it runs agrees with `default`'s run, it replays the rest's fired delta and returns `default`'s verdict; otherwise it runs the whole probe. The delta's fired set, its memo file and its tables are therefore the bytes it writes without shared verdicts. The verdict memos above the probes fill in the same order either way, which is why the key names the family and shape instead of the signature: a signature-keyed memo holds the verdict of whichever family asked first, and a delta must run, or be served, the probe its own first asker runs. The fourth-slot fallback in [`ProspectLiveness::third_live`] is not shared as one verdict, because which fourth-slot verdicts it computes depends on which ones other callers memoized first; the probes beneath it are shared.

use std::rc::Rc;

use crate::engine::{Engine, Slots, VerdictEntry, VerdictKey, VerdictKind};
use crate::error::{SettleError, SettleErrorKind};
use crate::hash::{HashMap, HashSet};
use crate::index::{Ordinal, SpecIndex};
use crate::model::{Condition, PolicyRecord, Sym};
use crate::types::{
    Candidate, CandidateOrdinals, CellId, EDGE, LeftContext, NAMER_DOT, NO_EXIT_INDEX, RightToken,
    SPACE, Settled, TokenKind, UNKNOWN, ZWNJ,
};

/// One shape a probe candidate can commit: a stance of the input's own rune and the exit junction it offers there, `None` for the shape that offers no exit.
type Shape = (Sym, Option<Sym>);

/// The left-condition signature: the committed junction, then the follower's own left-reading conditions answered against the synthetic left, in the order [`ProspectLiveness::left_conditions`] gathers them. The verdicts are behind an [`Rc`] because the signature is copied into every memo key the prospect and follower prefer branches write.
type LeftConditionSignature = (Option<Sym>, Rc<Vec<bool>>);

/// What the input replay saw at one probed window. `Raised` is a prefer conflict the enumeration must report, so the slot is live. `Unreachable` is a window the fixpoint cannot reach from this representative left; at the baseline the replay skips that left.
#[derive(Clone, Debug, PartialEq, Eq)]
enum ReplayOutcome {
    Cell(CellId),
    Raised,
    Unreachable,
}

/// The liveness probe for one spec. It memoizes its verdicts and the structures behind them. The engine is passed in on every call, and the module doc says why it must be the same engine each time.
pub struct ProspectLiveness<'i> {
    index: &'i SpecIndex,
    tokens: Option<Rc<Vec<RightToken>>>,
    representative_lefts: HashMap<Sym, Rc<Vec<LeftContext>>>,
    shapes: HashMap<Sym, Rc<Vec<Shape>>>,
    conds: HashMap<Sym, Rc<Vec<&'i Condition>>>,
    sigs: HashMap<(Sym, Sym, Sym, Option<Sym>), LeftConditionSignature>,
    /// The third-slot input replay, keyed `(family, right1, right2)`.
    replay3: HashMap<(Sym, Sym, Sym), bool>,
    /// The fourth-slot fallback over every concrete letter third, keyed `(family, right1, right2)`.
    fourth_into_third: HashMap<(Sym, Sym, Sym), bool>,
    /// The third slot's prospect branch, keyed `(right1, right2, signature)`.
    prospect3: HashMap<(Sym, Sym, LeftConditionSignature), bool>,
    /// The third slot's follower prefer branch, keyed `(right1, right2, signature)`.
    follower_prefer3: HashMap<(Sym, Sym, LeftConditionSignature), bool>,
    /// The fourth-slot input replay, keyed `(family, right1, right2, right3)`.
    replay4: HashMap<(Sym, Sym, Sym, Sym), bool>,
    /// The fourth slot's prospect branch, keyed `(right1, right2, right3, signature)`.
    prospect4: HashMap<(Sym, Sym, Sym, LeftConditionSignature), bool>,
    /// The fourth slot's follower prefer branch, keyed `(right1, right2, right3, signature)`.
    follower_prefer4: HashMap<(Sym, Sym, Sym, LeftConditionSignature), bool>,
}

impl<'i> ProspectLiveness<'i> {
    /// The probe over one spec, with every memo empty.
    pub fn new(index: &'i SpecIndex) -> Self {
        Self {
            index,
            tokens: None,
            representative_lefts: HashMap::default(),
            shapes: HashMap::default(),
            conds: HashMap::default(),
            sigs: HashMap::default(),
            replay3: HashMap::default(),
            fourth_into_third: HashMap::default(),
            prospect3: HashMap::default(),
            follower_prefer3: HashMap::default(),
            replay4: HashMap::default(),
            prospect4: HashMap::default(),
            follower_prefer4: HashMap::default(),
        }
    }

    /// The letter token for a rune the probe is asked about.
    fn letter(&self, rune: Sym) -> RightToken {
        self.index
            .letter(rune)
            .expect("the probes are asked about registered runes")
    }

    /// Whether the raw third slot can change the settled outcome of some reachable window at `(family, right1, right2)`.
    ///
    /// Stage one is `(simulated_prospect and prospect_varies_third) or (follower_prefer_slots and follower_prefer_varies_third)`, and the `or` short-circuits. Where stage one fires, the input replay runs, and a true result is returned at once. Otherwise the verdict is the fourth-slot fallback: [`ProspectLiveness::fourth_live`] for each letter token in [`ProspectLiveness::probe_tokens`] order, stopping at the first live one.
    pub fn third_live(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        right1: Sym,
        right2: Sym,
    ) -> Result<bool, SettleError> {
        let r1tok = self.letter(right1);
        let r2tok = self.letter(right2);
        let mut stage_one = false;
        if engine.simulated_prospect() {
            stage_one = self.prospect_varies_third(engine, family, right1, right2, r1tok, r2tok)?;
        }
        if !stage_one && engine.follower_prefer_slots() {
            stage_one =
                self.follower_prefer_varies_third(engine, family, right1, right2, r1tok, r2tok)?;
        }
        if stage_one {
            let key = (family, right1, right2);
            let verdict = match self.replay3.get(&key) {
                Some(&cached) => cached,
                None => {
                    let verdict = self.verdict(
                        engine,
                        Probe::Replay {
                            family,
                            r1tok,
                            r2tok,
                            r3tok: None,
                        },
                    )?;
                    self.replay3.insert(key, verdict);
                    verdict
                }
            };
            if verdict {
                return Ok(true);
            }
        }
        let key = (family, right1, right2);
        if let Some(&cached) = self.fourth_into_third.get(&key) {
            return Ok(cached);
        }
        let tokens = self.probe_tokens();
        let mut verdict = false;
        for token in tokens.iter() {
            if let RightToken::Letter(third, _) = *token
                && self.fourth_live(engine, family, right1, right2, third)?
            {
                verdict = true;
                break;
            }
        }
        self.fourth_into_third.insert(key, verdict);
        Ok(verdict)
    }

    /// Whether the raw fourth slot can change the settled outcome of some reachable window at `(family, right1, right2, right3)`.
    ///
    /// The same two stages one slot deeper, with no fallback. If stage one does not fire, the slot is dead. Where it fires, the input replay is the verdict.
    pub fn fourth_live(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        right1: Sym,
        right2: Sym,
        right3: Sym,
    ) -> Result<bool, SettleError> {
        let r1tok = self.letter(right1);
        let r2tok = self.letter(right2);
        let r3tok = self.letter(right3);
        let mut stage_one = false;
        if engine.simulated_prospect() {
            stage_one = self.prospect_varies_fourth(
                engine, family, right1, right2, right3, r1tok, r2tok, r3tok,
            )?;
        }
        if !stage_one && engine.follower_prefer_slots() {
            stage_one = self.follower_prefer_varies_fourth(
                engine, family, right1, right2, right3, r1tok, r2tok, r3tok,
            )?;
        }
        if !stage_one {
            return Ok(false);
        }
        let key = (family, right1, right2, right3);
        if let Some(&cached) = self.replay4.get(&key) {
            return Ok(cached);
        }
        let verdict = self.verdict(
            engine,
            Probe::Replay {
                family,
                r1tok,
                r2tok,
                r3tok: Some(r3tok),
            },
        )?;
        self.replay4.insert(key, verdict);
        Ok(verdict)
    }

    /// One probe's verdict. Where a shared memo serves this engine a verdict `default` published for the probe ([`Engine::shared_verdict`]), the probe runs partially behind it ([`Partial`]); when every ask it runs agrees with `default`'s run, the verdict is served, with what the other asks fired replayed ([`Engine::serve_verdict`]), and otherwise the whole probe runs. An engine that publishes its verdicts captures the whole run with the step it stopped at ([`Publishing`]). Anywhere else the probe just runs ([`Whole`]). The key is built only where it is read.
    fn verdict(&mut self, engine: &mut Engine<'_>, probe: Probe) -> Result<bool, SettleError> {
        if engine.reads_verdicts() {
            if let Some(served) = engine.shared_verdict(&probe.key(self)) {
                let mut partial = Partial::behind(served.stop, Rc::clone(&served.marked));
                let verdict = probe.run(self, engine, &mut partial)?;
                if !partial.diverged {
                    debug_assert_eq!(verdict, served.stop != VerdictEntry::RAN_OUT);
                    engine.serve_verdict(&served);
                    return Ok(verdict);
                }
                engine.diverged_from_verdict();
            }
            return probe.run(self, engine, &mut Whole);
        }
        if engine.publishes_verdicts() {
            let key = probe.key(self);
            if engine.begin_verdict(&key) {
                let mut publishing = Publishing::new();
                return match probe.run(self, engine, &mut publishing) {
                    Ok(verdict) => {
                        engine.end_verdict(key, publishing.stopped);
                        Ok(verdict)
                    }
                    Err(error) => {
                        engine.abort_verdict();
                        Err(error)
                    }
                };
            }
        }
        probe.run(self, engine, &mut Whole)
    }

    /// The representative lefts the input replay and the fiber probes settle this family against: the four boundary lefts, then one synthetic `(family, stance, junction)` left per distinct left-condition signature.
    ///
    /// The runes are visited in the dump's declaration order ([`SpecIndex::runes`]), not sorted order, and the first left with a given signature is the one kept. A different order keeps a different synthetic left, which can change both a liveness verdict and a fiber key.
    ///
    /// Memoized per family, because the input replay and the fiber deriver each ask for it once per context.
    pub fn representative_lefts(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
    ) -> Result<Rc<Vec<LeftContext>>, SettleError> {
        if let Some(cached) = self.representative_lefts.get(&family) {
            return Ok(Rc::clone(cached));
        }
        let mut out = vec![
            LeftContext::boundary(TokenKind::Edge),
            LeftContext::boundary(TokenKind::Space),
            LeftContext::boundary(TokenKind::Zwnj),
            LeftContext::boundary(TokenKind::NamerDot),
        ];
        let mut seen: HashSet<LeftConditionSignature> = HashSet::default();
        let left_families: Vec<Sym> = self.index.runes().iter().map(|(name, _)| *name).collect();
        for left_family in left_families {
            for (stance, junction) in self.probe_candidate_shapes(left_family).iter().copied() {
                let signature = self.signature(engine, family, left_family, stance, junction)?;
                if !seen.insert(signature) {
                    continue;
                }
                out.push(synthetic_left(self.index, left_family, stance, junction));
            }
        }
        let lefts = Rc::new(out);
        self.representative_lefts.insert(family, Rc::clone(&lefts));
        Ok(lefts)
    }

    /// The tokens every probe sweeps: the four boundaries, then one letter token per rune sorted by name. Built once.
    ///
    /// The order affects the output. Every branch that sweeps these tokens stops at the first variance it sees, and a probe that never runs fires nothing, so a different order records a different `cited_provenance`.
    pub fn probe_tokens(&mut self) -> Rc<Vec<RightToken>> {
        if self.tokens.is_none() {
            let mut tokens = vec![EDGE, SPACE, ZWNJ, NAMER_DOT];
            let mut letters: Vec<Sym> = self.index.runes().iter().map(|(name, _)| *name).collect();
            letters
                .sort_by(|left, right| self.index.resolve(*left).cmp(self.index.resolve(*right)));
            tokens.extend(letters.into_iter().map(|rune| self.letter(rune)));
            self.tokens = Some(Rc::new(tokens));
        }
        Rc::clone(self.tokens.as_ref().expect("the token list was just built"))
    }

    /// The `(stance, junction)` shapes this family's probe candidate can commit, in the stances' declaration order.
    ///
    /// Each stance contributes, in order: the exitless shape, unless the stance requires an exit; its declared exit rows; then the exits an unlock adds that the surface does not declare. Repeated junctions within a stance are dropped after the first.
    fn probe_candidate_shapes(&mut self, family: Sym) -> Rc<Vec<Shape>> {
        if let Some(cached) = self.shapes.get(&family) {
            return Rc::clone(cached);
        }
        let index = self.index;
        let vocab = index.vocab();
        let rune = index
            .rune(family)
            .unwrap_or_else(|| panic!("{} is modeled", index.resolve(family)));
        let mut out: Vec<Shape> = Vec::new();
        for (stance_name, stance) in rune.stances.iter() {
            let surface = &stance.surface;
            let mut junctions: Vec<Option<Sym>> = if surface.require.contains(&vocab.exit) {
                Vec::new()
            } else {
                vec![None]
            };
            junctions.extend(surface.exits.iter().map(|(height, _)| Some(*height)));
            for unlock in &surface.unlocks {
                if let Some(exit) = unlock.exit
                    && !surface.exits.iter().any(|(height, _)| *height == exit)
                {
                    junctions.push(Some(exit));
                }
            }
            let mut seen: HashSet<Option<Sym>> = HashSet::default();
            for junction in junctions {
                if seen.insert(junction) {
                    out.push((*stance_name, junction));
                }
            }
        }
        let shapes = Rc::new(out);
        self.shapes.insert(family, Rc::clone(&shapes));
        shapes
    }

    /// The follower's own left-reading conditions, in the order the signature vector holds their verdicts: for each stance, every entry row's scope and then every unlock's left `when:`; after all the stances, the left `when:` of each `refuse`, `prefer` and `resolve` record, in that order.
    fn left_conditions(&mut self, follower: Sym) -> Rc<Vec<&'i Condition>> {
        if let Some(cached) = self.conds.get(&follower) {
            return Rc::clone(cached);
        }
        let index = self.index;
        let rune = index
            .rune(follower)
            .unwrap_or_else(|| panic!("{} is modeled", index.resolve(follower)));
        let mut gathered: Vec<&'i Condition> = Vec::new();
        for (_, stance) in rune.stances.iter() {
            for (_, row) in stance.surface.entries.iter() {
                gathered.extend(row.scope.iter());
            }
            for unlock in &stance.surface.unlocks {
                if let Some(when) = unlock.when.as_ref()
                    && let Some(left) = when.left.as_ref()
                {
                    gathered.push(left);
                }
            }
        }
        for record in rune
            .policy
            .refuse
            .iter()
            .chain(&rune.policy.prefer)
            .chain(&rune.policy.resolve)
        {
            if let Some(left) = record.when.left.as_ref() {
                gathered.push(left);
            }
        }
        let conds = Rc::new(gathered);
        self.conds.insert(follower, Rc::clone(&conds));
        conds
    }

    /// The follower's `prefer` records, which the follower prefer branch probes in declaration order.
    fn follower_prefer_records(&self, follower: Sym) -> &'i [PolicyRecord] {
        let index = self.index;
        &index
            .rune(follower)
            .unwrap_or_else(|| panic!("{} is modeled", index.resolve(follower)))
            .policy
            .prefer
    }

    /// The probe candidate's left-condition signature at this shape: the junction it commits, and the follower's left conditions evaluated against the synthetic left for that shape.
    ///
    /// A left condition carrying a `then:` makes [`Engine::cond_matches_left`] return an error. The error is not memoized, so a second call returns it again.
    fn signature(
        &mut self,
        engine: &mut Engine<'_>,
        follower: Sym,
        family: Sym,
        stance: Sym,
        junction: Option<Sym>,
    ) -> Result<LeftConditionSignature, SettleError> {
        let key = (follower, family, stance, junction);
        if let Some(cached) = self.sigs.get(&key) {
            return Ok(cached.clone());
        }
        let left = synthetic_left(self.index, family, stance, junction);
        let conds = self.left_conditions(follower);
        let mut verdicts: Vec<bool> = Vec::with_capacity(conds.len());
        for cond in conds.iter() {
            verdicts.push(engine.cond_matches_left(Some(follower), cond, &left, junction)?);
        }
        let signature: LeftConditionSignature = (junction, Rc::new(verdicts));
        self.sigs.insert(key, signature.clone());
        Ok(signature)
    }

    /// Stage one's prospect branch at the third slot: whether some shape of the probe candidate has a simulated follower choice that changes with the third token.
    fn prospect_varies_third(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        right1: Sym,
        right2: Sym,
        r1tok: RightToken,
        r2tok: RightToken,
    ) -> Result<bool, SettleError> {
        for (stance, junction) in self.probe_candidate_shapes(family).iter().copied() {
            let signature = self.signature(engine, right1, family, stance, junction)?;
            let key = (right1, right2, signature);
            let verdict = match self.prospect3.get(&key) {
                Some(&cached) => cached,
                None => {
                    let verdict = self.verdict(
                        engine,
                        Probe::Prospect {
                            family,
                            stance,
                            junction,
                            r1tok,
                            r2tok,
                            r3tok: None,
                        },
                    )?;
                    self.prospect3.insert(key, verdict);
                    verdict
                }
            };
            if verdict {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// One shape's third-slot prospect probe. For each token, `(right1, right2, token, EDGE)` is compared with the baseline `(right1, right2, EDGE, EDGE)`, and then `(right1, right2, token, UNKNOWN)` with `(right1, right2, token, EDGE)`. The second comparison catches a prospect that the fourth slot changes at this third. The baseline is step zero and token `i`'s two asks are steps `1 + 2i` and `2 + 2i` ([`Asks`]).
    #[allow(clippy::too_many_arguments)]
    fn third_class_live(
        &mut self,
        engine: &mut Engine<'_>,
        asks: &mut impl Asks,
        family: Sym,
        stance: Sym,
        junction: Option<Sym>,
        r1tok: RightToken,
        r2tok: RightToken,
    ) -> Result<bool, SettleError> {
        let candidate = probe_candidate(self.index, family, stance, junction);
        let base = Slots::new(r1tok, r2tok, EDGE, EDGE);
        let mut baseline = match asks.take(0, [None, None]) {
            Take::Varies => return Ok(asks.varied(0)),
            Take::Agrees => None,
            Take::Run => Some(asks.ask(engine, [None, None], |engine| {
                engine.probe_prospect(family, candidate, base)
            })?),
        };
        let tokens = self.probe_tokens();
        for (at, &token) in (0u32..).zip(tokens.iter()) {
            let names = [token.ordinal(), None];
            let step = 1 + 2 * at;
            let edge4 = match asks.take(step, names) {
                Take::Varies => return Ok(asks.varied(step)),
                Take::Agrees => None,
                Take::Run => {
                    let reference = known(&mut baseline, || {
                        engine.probe_prospect(family, candidate, base)
                    })?;
                    let edge4 = asks.ask(engine, names, |engine| {
                        engine.probe_prospect(
                            family,
                            candidate,
                            Slots::new(r1tok, r2tok, token, EDGE),
                        )
                    })?;
                    if edge4 != reference {
                        return Ok(asks.varied(step));
                    }
                    Some(edge4)
                }
            };
            match asks.take(step + 1, names) {
                Take::Varies => return Ok(asks.varied(step + 1)),
                Take::Agrees => {}
                Take::Run => {
                    let reference = match edge4 {
                        Some(edge4) => edge4,
                        None => known(&mut baseline, || {
                            engine.probe_prospect(family, candidate, base)
                        })?,
                    };
                    let unknown4 = asks.ask(engine, names, |engine| {
                        engine.probe_prospect(
                            family,
                            candidate,
                            Slots::new(r1tok, r2tok, token, UNKNOWN),
                        )
                    })?;
                    if unknown4 != reference {
                        return Ok(asks.varied(step + 1));
                    }
                }
            }
        }
        Ok(asks.ran_out())
    }

    /// Stage one's prospect branch at the fourth slot: [`ProspectLiveness::prospect_varies_third`] with the concrete third in the memo key.
    #[allow(clippy::too_many_arguments)]
    fn prospect_varies_fourth(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        right1: Sym,
        right2: Sym,
        right3: Sym,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: RightToken,
    ) -> Result<bool, SettleError> {
        for (stance, junction) in self.probe_candidate_shapes(family).iter().copied() {
            let signature = self.signature(engine, right1, family, stance, junction)?;
            let key = (right1, right2, right3, signature);
            let verdict = match self.prospect4.get(&key) {
                Some(&cached) => cached,
                None => {
                    let verdict = self.verdict(
                        engine,
                        Probe::Prospect {
                            family,
                            stance,
                            junction,
                            r1tok,
                            r2tok,
                            r3tok: Some(r3tok),
                        },
                    )?;
                    self.prospect4.insert(key, verdict);
                    verdict
                }
            };
            if verdict {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// One shape's fourth-slot prospect probe: `(right1, right2, right3, token)` compared with the baseline `(right1, right2, right3, EDGE)`. There is no slot past the fourth, so there is no second comparison. The baseline is step zero and token `i`'s ask is step `1 + i`.
    #[allow(clippy::too_many_arguments)]
    fn fourth_class_live(
        &mut self,
        engine: &mut Engine<'_>,
        asks: &mut impl Asks,
        family: Sym,
        stance: Sym,
        junction: Option<Sym>,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: RightToken,
    ) -> Result<bool, SettleError> {
        let candidate = probe_candidate(self.index, family, stance, junction);
        let base = Slots::new(r1tok, r2tok, r3tok, EDGE);
        let mut baseline = match asks.take(0, [None, None]) {
            Take::Varies => return Ok(asks.varied(0)),
            Take::Agrees => None,
            Take::Run => Some(asks.ask(engine, [None, None], |engine| {
                engine.probe_prospect(family, candidate, base)
            })?),
        };
        let tokens = self.probe_tokens();
        for (at, &token) in (0u32..).zip(tokens.iter()) {
            let names = [token.ordinal(), None];
            let step = 1 + at;
            match asks.take(step, names) {
                Take::Varies => return Ok(asks.varied(step)),
                Take::Agrees => {}
                Take::Run => {
                    let reference = known(&mut baseline, || {
                        engine.probe_prospect(family, candidate, base)
                    })?;
                    let varied = asks.ask(engine, names, |engine| {
                        engine.probe_prospect(
                            family,
                            candidate,
                            Slots::new(r1tok, r2tok, r3tok, token),
                        )
                    })?;
                    if varied != reference {
                        return Ok(asks.varied(step));
                    }
                }
            }
        }
        Ok(asks.ran_out())
    }

    /// Stage one's follower prefer branch at the third slot. It returns false at once when the follower is the input's own family or has no `prefer` records (see the module doc).
    fn follower_prefer_varies_third(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        right1: Sym,
        right2: Sym,
        r1tok: RightToken,
        r2tok: RightToken,
    ) -> Result<bool, SettleError> {
        if right1 == family || self.follower_prefer_records(right1).is_empty() {
            return Ok(false);
        }
        for (stance, junction) in self.probe_candidate_shapes(family).iter().copied() {
            let signature = self.signature(engine, right1, family, stance, junction)?;
            let key = (right1, right2, signature);
            let verdict = match self.follower_prefer3.get(&key) {
                Some(&cached) => cached,
                None => {
                    let verdict = self.verdict(
                        engine,
                        Probe::FollowerPrefer {
                            family,
                            stance,
                            junction,
                            r1tok,
                            r2tok,
                            r3tok: None,
                        },
                    )?;
                    self.follower_prefer3.insert(key, verdict);
                    verdict
                }
            };
            if verdict {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// Stage one's follower prefer branch at the fourth slot, with the same two early returns.
    #[allow(clippy::too_many_arguments)]
    fn follower_prefer_varies_fourth(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        right1: Sym,
        right2: Sym,
        right3: Sym,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: RightToken,
    ) -> Result<bool, SettleError> {
        if right1 == family || self.follower_prefer_records(right1).is_empty() {
            return Ok(false);
        }
        for (stance, junction) in self.probe_candidate_shapes(family).iter().copied() {
            let signature = self.signature(engine, right1, family, stance, junction)?;
            let key = (right1, right2, right3, signature);
            let verdict = match self.follower_prefer4.get(&key) {
                Some(&cached) => cached,
                None => {
                    let verdict = self.verdict(
                        engine,
                        Probe::FollowerPrefer {
                            family,
                            stance,
                            junction,
                            r1tok,
                            r2tok,
                            r3tok: Some(r3tok),
                        },
                    )?;
                    self.follower_prefer4.insert(key, verdict);
                    verdict
                }
            };
            if verdict {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// Whether some follower prefer's verdict at this window changes with the probed deep token.
    ///
    /// With `r3tok` absent this probes the third slot, with the same two comparisons as [`ProspectLiveness::third_class_live`]. With a concrete `r3tok` it probes the fourth slot at that third. The records are the outer loop and the probe tokens the inner one, and the first variance returns. Record `j`'s steps start at `j` times one more than its asks per token times the token count, its baseline first, then each token's asks as in the prospect probe at the same slot.
    #[allow(clippy::too_many_arguments)]
    fn follower_prefer_class_live(
        &mut self,
        engine: &mut Engine<'_>,
        asks: &mut impl Asks,
        family: Sym,
        stance: Sym,
        junction: Option<Sym>,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: Option<RightToken>,
    ) -> Result<bool, SettleError> {
        let candidate = probe_candidate(self.index, family, stance, junction);
        let owner = r1tok.letter();
        let edge_left = LeftContext::boundary(TokenKind::Edge);
        let records = self.follower_prefer_records(owner);
        let tokens = self.probe_tokens();
        let span = 1 + if r3tok.is_some() { 1 } else { 2 } * steps_of(&tokens);
        for (at_record, record) in (0u32..).zip(records.iter()) {
            let first = at_record * span;
            let favors = |engine: &mut Engine<'_>, slots: Slots| {
                engine.probe_prefer_favors(owner, record, family, candidate, &edge_left, slots)
            };
            let base = Slots::new(r1tok, r2tok, r3tok.unwrap_or(EDGE), EDGE);
            let mut baseline = match asks.take(first, [None, None]) {
                Take::Varies => return Ok(asks.varied(first)),
                Take::Agrees => None,
                Take::Run => Some(asks.ask(engine, [None, None], |engine| favors(engine, base))?),
            };
            for (at, &token) in (0u32..).zip(tokens.iter()) {
                let names = [token.ordinal(), None];
                match r3tok {
                    None => {
                        let step = first + 1 + 2 * at;
                        let edge4 = match asks.take(step, names) {
                            Take::Varies => return Ok(asks.varied(step)),
                            Take::Agrees => None,
                            Take::Run => {
                                let reference = known(&mut baseline, || favors(engine, base))?;
                                let edge4 = asks.ask(engine, names, |engine| {
                                    favors(engine, Slots::new(r1tok, r2tok, token, EDGE))
                                })?;
                                if edge4 != reference {
                                    return Ok(asks.varied(step));
                                }
                                Some(edge4)
                            }
                        };
                        match asks.take(step + 1, names) {
                            Take::Varies => return Ok(asks.varied(step + 1)),
                            Take::Agrees => {}
                            Take::Run => {
                                let reference = match edge4 {
                                    Some(edge4) => edge4,
                                    None => known(&mut baseline, || favors(engine, base))?,
                                };
                                let unknown4 = asks.ask(engine, names, |engine| {
                                    favors(engine, Slots::new(r1tok, r2tok, token, UNKNOWN))
                                })?;
                                if unknown4 != reference {
                                    return Ok(asks.varied(step + 1));
                                }
                            }
                        }
                    }
                    Some(third) => {
                        let step = first + 1 + at;
                        match asks.take(step, names) {
                            Take::Varies => return Ok(asks.varied(step)),
                            Take::Agrees => {}
                            Take::Run => {
                                let reference = known(&mut baseline, || favors(engine, base))?;
                                let varied = asks.ask(engine, names, |engine| {
                                    favors(engine, Slots::new(r1tok, r2tok, third, token))
                                })?;
                                if varied != reference {
                                    return Ok(asks.varied(step));
                                }
                            }
                        }
                    }
                }
            }
        }
        Ok(asks.ran_out())
    }

    /// Stage two: the input letter's own transition replayed for each probe token over its representative lefts. Returns true where some representative left's settled cell changes.
    ///
    /// A left whose baseline window is unreachable is skipped, because the fixpoint cannot reach it either. A raise at the baseline is live, and so is any probe token whose window raises, is unreachable, or settles to a different cell. With `r3tok` absent this probes the third slot, with the fourth set first to `EDGE` and then to `UNKNOWN`; with a concrete `r3tok` it probes the fourth slot. Left `k`'s steps start at `k` times one more than its asks per token times the token count, its baseline first. A partial run settles a left's baseline before any ask of that left it runs, so it skips the left exactly where the whole run does.
    fn input_varies(
        &mut self,
        engine: &mut Engine<'_>,
        asks: &mut impl Asks,
        family: Sym,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: Option<RightToken>,
    ) -> Result<bool, SettleError> {
        let token = self.letter(family);
        let lefts = self.representative_lefts(engine, family)?;
        let tokens = self.probe_tokens();
        let span = 1 + if r3tok.is_some() { 1 } else { 2 } * steps_of(&tokens);
        'lefts: for (at_left, left) in (0u32..).zip(lefts.iter()) {
            let first = at_left * span;
            let left_rune = left.ordinals.rune;
            let base = Slots::new(r1tok, r2tok, r3tok.unwrap_or(EDGE), EDGE);
            let mut baseline = match asks.take(first, [left_rune, None]) {
                Take::Varies => return Ok(asks.varied(first)),
                Take::Agrees => None,
                Take::Run => match asks.ask(engine, [left_rune, None], |engine| {
                    replay_outcome(engine, left, token, base)
                }) {
                    ReplayOutcome::Raised => return Ok(asks.varied(first)),
                    ReplayOutcome::Unreachable => continue,
                    ReplayOutcome::Cell(cell) => Some(cell),
                },
            };
            for (at, &probe) in (0u32..).zip(tokens.iter()) {
                let names = [left_rune, probe.ordinal()];
                let (step, slots) = match r3tok {
                    None => (first + 1 + 2 * at, Slots::new(r1tok, r2tok, probe, EDGE)),
                    Some(third) => (first + 1 + at, Slots::new(r1tok, r2tok, third, probe)),
                };
                let edge4 = match asks.take(step, names) {
                    Take::Varies => return Ok(asks.varied(step)),
                    Take::Agrees => None,
                    Take::Run => {
                        if baseline.is_none() {
                            match replay_outcome(engine, left, token, base) {
                                ReplayOutcome::Cell(cell) => baseline = Some(cell),
                                ReplayOutcome::Unreachable => continue 'lefts,
                                ReplayOutcome::Raised => return Ok(asks.varied(first)),
                            }
                        }
                        let ReplayOutcome::Cell(cell) = asks.ask(engine, names, |engine| {
                            replay_outcome(engine, left, token, slots)
                        }) else {
                            return Ok(asks.varied(step));
                        };
                        if Some(&cell) != baseline.as_ref() {
                            return Ok(asks.varied(step));
                        }
                        Some(cell)
                    }
                };
                if r3tok.is_some() {
                    continue;
                }
                match asks.take(step + 1, names) {
                    Take::Varies => return Ok(asks.varied(step + 1)),
                    Take::Agrees => {}
                    Take::Run => {
                        if edge4.is_none() && baseline.is_none() {
                            match replay_outcome(engine, left, token, base) {
                                ReplayOutcome::Cell(cell) => baseline = Some(cell),
                                ReplayOutcome::Unreachable => continue 'lefts,
                                ReplayOutcome::Raised => return Ok(asks.varied(first)),
                            }
                        }
                        let ReplayOutcome::Cell(unknown4) = asks.ask(engine, names, |engine| {
                            replay_outcome(
                                engine,
                                left,
                                token,
                                Slots::new(r1tok, r2tok, probe, UNKNOWN),
                            )
                        }) else {
                            return Ok(asks.varied(step + 1));
                        };
                        if Some(&unknown4) != edge4.as_ref().or(baseline.as_ref()) {
                            return Ok(asks.varied(step + 1));
                        }
                    }
                }
            }
        }
        Ok(asks.ran_out())
    }
}

/// How a probe takes one ask ([`Asks::take`]): run it, take it to agree with its reference, or take it to vary there.
enum Take {
    Run,
    Agrees,
    Varies,
}

/// How one run of a probe takes its asks ([`ProspectLiveness::verdict`]). The probes are generic over it, so a run that takes every ask compiles to the loop with no step bookkeeping.
///
/// Every probe compares a fixed sequence of asks, each a settlement or a prospect or prefer evaluation at one window, with a reference: a baseline, or the same token's ask at an `EDGE` fourth. Each ask has a step, its position in the loop order the probe would run with no early return, so the steps of a probe are the same in every configuration. An ask's varying runes are the swept token and, in the input replay, the representative left's rune; the rest it names, the family and the slots, are the key's.
///
/// [`Whole`] and [`Publishing`] take every ask, in order; [`Publishing`] also records the step the probe stopped at and marks each ask for the engine's capture ([`Engine::probe_ask`]). [`Partial`] is a delta's run behind a verdict `default` published for the same key, and the argument for it is there.
trait Asks {
    /// How to take the ask at `step` whose varying runes are `names`.
    fn take(&mut self, step: u32, names: [Option<Ordinal>; 2]) -> Take;

    /// Run an ask this run takes.
    fn ask<'e, T>(
        &mut self,
        engine: &mut Engine<'e>,
        names: [Option<Ordinal>; 2],
        ask: impl FnOnce(&mut Engine<'e>) -> T,
    ) -> T;

    /// The probe varied at `step`, and returns its live verdict.
    fn varied(&mut self, step: u32) -> bool;

    /// The probe ran through its last step, and returns its dead verdict.
    fn ran_out(&mut self) -> bool;
}

/// A run that takes every ask and records nothing.
struct Whole;

impl Asks for Whole {
    #[inline]
    fn take(&mut self, _step: u32, _names: [Option<Ordinal>; 2]) -> Take {
        Take::Run
    }

    #[inline]
    fn ask<'e, T>(
        &mut self,
        engine: &mut Engine<'e>,
        _names: [Option<Ordinal>; 2],
        ask: impl FnOnce(&mut Engine<'e>) -> T,
    ) -> T {
        ask(engine)
    }

    #[inline]
    fn varied(&mut self, _step: u32) -> bool {
        true
    }

    #[inline]
    fn ran_out(&mut self) -> bool {
        false
    }
}

/// A run that takes every ask, through the engine's capture of a published probe, and records the step it stopped at, [`VerdictEntry::RAN_OUT`] when it ran through.
struct Publishing {
    stopped: u32,
}

impl Publishing {
    fn new() -> Self {
        Self {
            stopped: VerdictEntry::RAN_OUT,
        }
    }
}

impl Asks for Publishing {
    fn take(&mut self, _step: u32, _names: [Option<Ordinal>; 2]) -> Take {
        Take::Run
    }

    fn ask<'e, T>(
        &mut self,
        engine: &mut Engine<'e>,
        names: [Option<Ordinal>; 2],
        ask: impl FnOnce(&mut Engine<'e>) -> T,
    ) -> T {
        engine.probe_ask(names, ask)
    }

    fn varied(&mut self, step: u32) -> bool {
        self.stopped = step;
        true
    }

    fn ran_out(&mut self) -> bool {
        false
    }
}

/// A delta's run behind a verdict `default` published for the same key, which names none of the delta's unlocking runes (`marked`, flags by rune-field ordinal).
///
/// It runs the asks with a marked varying rune, and takes every other ask before `default`'s stop to agree with its reference and the one at the stop to vary, as each did in `default`'s run: such an ask names no marked rune and its windows read none (`default` checked what those asks read when it published the verdict for the delta's set), so it settles in the delta as in `default`, and every window beneath it is one the shared memo answers. A reference that a run ask needs and the partial run has not taken is settled first, which settles nothing new for the same reason. The run diverges when a run ask varies before the stop, or when the probe reaches a step past it, because a run ask at the stop agreed; the caller then runs the whole probe.
///
/// Its step numbers count the same asks as `default`'s run because a probe's asks and their order read only the spec and the index, never a configuration's features: [`ProspectLiveness::probe_tokens`], the representative lefts, whose signatures [`Engine::cond_matches_left`] answers, and the probe candidate shapes, which take every unlock's exit whatever the configuration. A left list or token order that read the features would misapply verdicts without failing.
///
/// So a partial run that does not diverge runs exactly the asks the whole run would run that name a marked rune, in the same order, and the asks it takes on trust are the ones the whole run would answer entirely from the shared memo. The delta's memo, which records only windows no shared memo answers, gains the same windows either way, and its fired set gains the same pointers once the served verdict's slot is replayed.
struct Partial {
    stop: u32,
    marked: Rc<[bool]>,
    diverged: bool,
}

impl Partial {
    fn behind(stop: u32, marked: Rc<[bool]>) -> Self {
        Self {
            stop,
            marked,
            diverged: false,
        }
    }
}

impl Asks for Partial {
    fn take(&mut self, step: u32, names: [Option<Ordinal>; 2]) -> Take {
        if step > self.stop {
            self.diverged = true;
            return Take::Varies;
        }
        let run = names
            .into_iter()
            .flatten()
            .any(|ordinal| self.marked.get(usize::from(ordinal.get())).copied() == Some(true));
        if run {
            Take::Run
        } else if step == self.stop {
            Take::Varies
        } else {
            Take::Agrees
        }
    }

    fn ask<'e, T>(
        &mut self,
        engine: &mut Engine<'e>,
        _names: [Option<Ordinal>; 2],
        ask: impl FnOnce(&mut Engine<'e>) -> T,
    ) -> T {
        ask(engine)
    }

    fn varied(&mut self, step: u32) -> bool {
        if step != self.stop {
            self.diverged = true;
        }
        true
    }

    fn ran_out(&mut self) -> bool {
        if self.stop != VerdictEntry::RAN_OUT {
            self.diverged = true;
        }
        false
    }
}

/// One probe a verdict memo stores, with what it runs over: the prospect and follower prefer probes for one shape of the family, and the input replay over its representative lefts, at the third slot with `r3tok` absent and at the fourth with it given.
#[derive(Clone, Copy)]
enum Probe {
    Prospect {
        family: Sym,
        stance: Sym,
        junction: Option<Sym>,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: Option<RightToken>,
    },
    FollowerPrefer {
        family: Sym,
        stance: Sym,
        junction: Option<Sym>,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: Option<RightToken>,
    },
    Replay {
        family: Sym,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: Option<RightToken>,
    },
}

impl Probe {
    /// The key `default` publishes this probe under and a delta looks it up by.
    fn key(self, liveness: &ProspectLiveness<'_>) -> VerdictKey {
        let (kind, family, shape, r1tok, r2tok, r3tok) = match self {
            Probe::Prospect {
                family,
                stance,
                junction,
                r1tok,
                r2tok,
                r3tok,
            } => (
                if r3tok.is_some() {
                    VerdictKind::Prospect4
                } else {
                    VerdictKind::Prospect3
                },
                family,
                Some((stance, junction)),
                r1tok,
                r2tok,
                r3tok,
            ),
            Probe::FollowerPrefer {
                family,
                stance,
                junction,
                r1tok,
                r2tok,
                r3tok,
            } => (
                if r3tok.is_some() {
                    VerdictKind::FollowerPrefer4
                } else {
                    VerdictKind::FollowerPrefer3
                },
                family,
                Some((stance, junction)),
                r1tok,
                r2tok,
                r3tok,
            ),
            Probe::Replay {
                family,
                r1tok,
                r2tok,
                r3tok,
            } => (
                if r3tok.is_some() {
                    VerdictKind::Replay4
                } else {
                    VerdictKind::Replay3
                },
                family,
                None,
                r1tok,
                r2tok,
                r3tok,
            ),
        };
        let (family, stance, junction) = match shape {
            Some((stance, junction)) => {
                let ordinals =
                    CandidateOrdinals::of(liveness.index, family, stance, None, junction);
                (ordinals.rune, Some(ordinals.stance), ordinals.junction)
            }
            None => (liveness.letter(family).letter_ordinal(), None, None),
        };
        VerdictKey {
            kind,
            family,
            stance,
            junction,
            right1: r1tok.letter_ordinal(),
            right2: r2tok.letter_ordinal(),
            right3: r3tok.map(RightToken::letter_ordinal),
        }
    }

    /// Run the probe, taking its asks as `asks` says.
    fn run(
        self,
        liveness: &mut ProspectLiveness<'_>,
        engine: &mut Engine<'_>,
        asks: &mut impl Asks,
    ) -> Result<bool, SettleError> {
        match self {
            Probe::Prospect {
                family,
                stance,
                junction,
                r1tok,
                r2tok,
                r3tok: None,
            } => liveness.third_class_live(engine, asks, family, stance, junction, r1tok, r2tok),
            Probe::Prospect {
                family,
                stance,
                junction,
                r1tok,
                r2tok,
                r3tok: Some(r3tok),
            } => liveness
                .fourth_class_live(engine, asks, family, stance, junction, r1tok, r2tok, r3tok),
            Probe::FollowerPrefer {
                family,
                stance,
                junction,
                r1tok,
                r2tok,
                r3tok,
            } => liveness.follower_prefer_class_live(
                engine, asks, family, stance, junction, r1tok, r2tok, r3tok,
            ),
            Probe::Replay {
                family,
                r1tok,
                r2tok,
                r3tok,
            } => liveness.input_varies(engine, asks, family, r1tok, r2tok, r3tok),
        }
    }
}

/// How many steps a probe token list spans per ask.
fn steps_of(tokens: &[RightToken]) -> u32 {
    u32::try_from(tokens.len()).expect("a probe sweeps fewer than 2^32 tokens")
}

/// The reference `held`, settled by `settle` first when the run has not taken it.
fn known<T: Clone>(
    held: &mut Option<T>,
    settle: impl FnOnce() -> Result<T, SettleError>,
) -> Result<T, SettleError> {
    if let Some(value) = held {
        return Ok(value.clone());
    }
    let value = settle()?;
    *held = Some(value.clone());
    Ok(value)
}

/// The synthetic left for one `(family, stance, junction)` shape: the cell with no entry and no adjustments, settled at that junction with no extension. Nothing a deep token can reach reads the entry, so all entries collapse to this one.
fn synthetic_left(
    index: &SpecIndex,
    family: Sym,
    stance: Sym,
    junction: Option<Sym>,
) -> LeftContext {
    LeftContext::letter(
        index,
        Settled {
            cell: CellId {
                rune: family,
                stance,
                entry: None,
                exit: junction,
                adjustments: Vec::new(),
            },
            junction,
            extension: 0,
        },
    )
}

/// The probe candidate every stage-one probe runs for: no entry, the shape's own junction, order index 0, and the `NO_EXIT_INDEX` sentinel, because it is a shape the input can commit and not a candidate the enumeration produced.
fn probe_candidate(
    index: &SpecIndex,
    family: Sym,
    stance: Sym,
    junction: Option<Sym>,
) -> Candidate {
    Candidate {
        stance,
        entry: None,
        junction,
        order_index: 0,
        exit_index: NO_EXIT_INDEX,
        ordinals: CandidateOrdinals::of(index, family, stance, None, junction),
    }
}

/// One replayed window's outcome. E-INCOMPARABLE and E-AMBIGUOUS are a raise the enumeration must report; every other settlement error is a window this left cannot reach.
fn replay_outcome(
    engine: &mut Engine<'_>,
    left: &LeftContext,
    token: RightToken,
    slots: Slots,
) -> ReplayOutcome {
    match engine.with_settled(left, token, slots, |settled| settled.cell.clone()) {
        Ok(cell) => ReplayOutcome::Cell(cell),
        Err(error) => match error.kind() {
            SettleErrorKind::Incomparable | SettleErrorKind::Ambiguous => ReplayOutcome::Raised,
            SettleErrorKind::UnacceptedExit | SettleErrorKind::Plain => ReplayOutcome::Unreachable,
        },
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::deep_slots::ThirdSlotFilter;
    use crate::engine::EngineModes;
    use crate::fixpoint::{EnumerationModes, enumerate_transitions};
    use crate::index::fixtures;
    use crate::memo::Exclusion;
    use crate::stream::FixpointProduct;

    /// A JSON object over already-built keys and values.
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

    fn safe(height: &str) -> (String, String) {
        row(height, &[("unjoined", "\"safe\"")])
    }

    fn surface(entries: &str, exits: &str, extra: &[(&str, &str)]) -> String {
        let mut fields = vec![("entries", entries), ("exits", exits)];
        fields.extend_from_slice(extra);
        fixtures::surface(&fields)
    }

    fn stance(name: &str, surface: &str) -> (String, String) {
        (
            name.to_owned(),
            fixtures::stance(name, &[("surface", surface)]),
        )
    }

    fn letter(name: &str, stances: &[(String, String)], policy: &str) -> (String, String) {
        let stances = object(stances);
        (
            name.to_owned(),
            fixtures::rune(name, &[("stances", stances.as_str()), ("policy", policy)]),
        )
    }

    fn spec_of(runes: &[(String, String)]) -> SpecIndex {
        fixtures::index_of(&fixtures::dump(
            &object(runes),
            &fixtures::four_family_registry(),
        ))
    }

    fn plain_policy() -> String {
        fixtures::policy(&[])
    }

    /// One `prefer` record with the provenance pointer a real one carries, so the notes a fiber key records are the notes a build would record.
    fn prefer(rune: &str, position: usize, overrides: &[(&str, &str)]) -> String {
        let pointer = fixtures::names(&[
            &format!("{rune}.yaml"),
            &format!("policy.prefer[{position}]"),
        ]);
        let mut fields: Vec<(&str, &str)> =
            vec![("kind", "\"prefer\""), ("provenance", pointer.as_str())];
        fields.extend_from_slice(overrides);
        fixtures::record(&fields)
    }

    /// A right condition naming one family per slot, chained a `then:` hop at a time.
    fn chain(families: &[&str]) -> String {
        let (head, rest) = families.split_first().expect("a chain names a slot");
        let family = fixtures::names(&[*head]);
        if rest.is_empty() {
            return fixtures::condition(&[("family", &family)]);
        }
        fixtures::condition(&[("family", &family), ("then", &chain(rest))])
    }

    /// An engine with the two mode flags the caller names. The trace memo is on, as it is in the fixpoint.
    fn engine_in(
        index: &SpecIndex,
        simulated_prospect: bool,
        follower_prefer_slots: bool,
    ) -> Engine<'_> {
        Engine::with_modes(
            index,
            Vec::<Sym>::new(),
            EngineModes {
                simulated_prospect,
                follower_prefer_slots,
                trace_memo: true,
                ..EngineModes::default()
            },
        )
    }

    fn spec() -> SpecIndex {
        let runes = fixtures::map(&[
            ("qsTea", &fixtures::rune("qsTea", &[])),
            ("qsPea", &fixtures::rune("qsPea", &[])),
            ("qsMay", &fixtures::rune("qsMay", &[])),
        ]);
        fixtures::index_of(&fixtures::dump(&runes, &fixtures::four_family_registry()))
    }

    /// The four boundaries come first, then the letters sorted by name. The fixture declares the letters in a different order.
    #[test]
    fn the_probe_alphabet_is_the_boundaries_then_the_letters_by_name() {
        let index = spec();
        let mut liveness = ProspectLiveness::new(&index);
        let tokens = liveness.probe_tokens();
        let spelled: Vec<String> = tokens
            .iter()
            .map(|token| match token {
                RightToken::Letter(rune, _) => index.resolve(*rune).to_owned(),
                other => other.kind().as_str().to_owned(),
            })
            .collect();
        assert_eq!(
            spelled,
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
        assert!(
            tokens[..4]
                .iter()
                .all(|token| token.kind() != TokenKind::Letter)
        );
    }

    #[test]
    fn the_probe_alphabet_is_built_once_and_handed_out_by_reference() {
        let index = spec();
        let mut liveness = ProspectLiveness::new(&index);
        let first = liveness.probe_tokens();
        let second = liveness.probe_tokens();
        assert!(Rc::ptr_eq(&first, &second));
    }

    /// The same shape as `prospect_spec` in `rebuild/pipeline/fixtures.py`. `qsPea` exits at both heights and prefers the x-height as a yielding tie-break. `qsTea` enters at both, is exitless when entered at the x-height, and gives up its baseline exit where the slots past it are `qsMay·qsIt`. An entered `qsMay` is exitless, so `qsTea` joining `qsMay` prevents that onward join, and `qsTea` declining allows it. No chain here reaches far enough to put its rune in a deep-slot rune set, so every verdict below comes from the liveness branch alone.
    pub(crate) fn prospect_spec() -> SpecIndex {
        let pea = letter(
            "qsPea",
            &[stance(
                "stroke",
                &surface("{}", &object(&[safe("x-height"), safe("baseline")]), &[]),
            )],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&prefer(
                    "qsPea",
                    0,
                    &[
                        ("cell", &fixtures::map(&[("exit", "\"x-height\"")])),
                        ("over", &fixtures::map(&[("exit", "\"baseline\"")])),
                    ],
                )]),
            )]),
        );
        let tea = letter(
            "qsTea",
            &[stance(
                "hook",
                &surface(
                    &object(&[row("x-height", &[]), row("baseline", &[])]),
                    &object(&[safe("baseline")]),
                    &[(
                        "pairings",
                        r#"{"never":[{"entry":"x-height","exit":"baseline"}],"only":null}"#,
                    )],
                ),
            )],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&prefer(
                    "qsTea",
                    0,
                    &[
                        (
                            "when",
                            &fixtures::when(&[("right", &chain(&["qsMay", "qsIt"]))]),
                        ),
                        ("cell", &fixtures::map(&[("exit", "\"none\"")])),
                        ("over", &fixtures::map(&[("exit", "\"baseline\"")])),
                    ],
                )]),
            )]),
        );
        let may = letter(
            "qsMay",
            &[stance(
                "base",
                &surface(
                    &object(&[row("baseline", &[])]),
                    &object(&[safe("baseline")]),
                    &[(
                        "pairings",
                        r#"{"never":[{"entry":"baseline","exit":"baseline"}],"only":null}"#,
                    )],
                ),
            )],
            &plain_policy(),
        );
        let it = letter(
            "qsIt",
            &[stance(
                "base",
                &surface(&object(&[row("baseline", &[])]), "{}", &[]),
            )],
            &plain_policy(),
        );
        spec_of(&[pea, tea, may, it])
    }

    /// The follower-prefer-slots shape. `qsPea` offers a baseline exit and an x-height exit that tie at every score. `qsTea` accepts one height per stance and offers no exit, so the input letter's prospect cannot change. `qsTea`'s own `prefer` favors its `hook` continuation where the slots past the input are `qsMay·qsIt`. The follower prefer is the only way the third token affects the input letter here.
    fn follower_prefer_spec() -> SpecIndex {
        let pea = letter(
            "qsPea",
            &[stance(
                "stroke",
                &surface("{}", &object(&[safe("baseline"), safe("x-height")]), &[]),
            )],
            &plain_policy(),
        );
        let tea = letter(
            "qsTea",
            &[
                stance(
                    "flat",
                    &surface(&object(&[row("baseline", &[])]), "{}", &[]),
                ),
                stance(
                    "hook",
                    &surface(&object(&[row("x-height", &[])]), "{}", &[]),
                ),
            ],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&prefer(
                    "qsTea",
                    0,
                    &[
                        (
                            "when",
                            &fixtures::when(&[("right", &chain(&["qsMay", "qsIt"]))]),
                        ),
                        ("stance", "\"hook\""),
                    ],
                )]),
            )]),
        );
        let may = letter(
            "qsMay",
            &[stance(
                "base",
                &surface(&object(&[row("baseline", &[])]), "{}", &[]),
            )],
            &plain_policy(),
        );
        let it = letter(
            "qsIt",
            &[stance(
                "base",
                &surface(&object(&[row("baseline", &[])]), "{}", &[]),
            )],
            &plain_policy(),
        );
        spec_of(&[pea, tea, may, it])
    }

    /// The four-family registry with a third height, `cap`, so a fixture can give one rune an exit height that exactly one other rune enters at.
    fn three_height_registry() -> String {
        fixtures::registry(&[
            (
                "heights",
                &fixtures::map(&[("baseline", "0"), ("x-height", "5"), ("cap", "9")]),
            ),
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
                    ("qsIt", r#"{"codepoint":58992,"sequence":null}"#),
                ]),
            ),
        ])
    }

    fn tall_spec_of(runes: &[(String, String)]) -> SpecIndex {
        fixtures::index_of(&fixtures::dump(&object(runes), &three_height_registry()))
    }

    /// The recorded fourth-slot fallback shape at fixture scale: an input letter whose settled cell moves under one specific `(third, fourth)` letter pair and under nothing else.
    ///
    /// `qsIt` requires an exit, so it has cells at all only where the fourth slot is a letter that accepts its baseline exit — the one r4 dependence that reads `EDGE` and `UNKNOWN` alike, since neither is a letter and the closure asks for a letter. `qsMay` exits at the x-height, which only `qsIt` enters, so `qsMay`'s onward join is available exactly at that third; joining it ties `qsTea`'s two cells, and `qsTea`'s cap-entered prefer yields the join there. That yields the input letter's cap exit its prospect, which is the tie its own prefer would otherwise win — so `qsPea` settles into its cap exit everywhere except where the third is `qsIt` and the fourth is a letter that accepts `qsIt`'s baseline exit (`qsPea`, `qsTea` or `qsMay`), where it settles into its baseline one.
    fn fourth_slot_fallback_spec() -> SpecIndex {
        let pea = letter(
            "qsPea",
            &[stance(
                "stroke",
                &surface(
                    &object(&[row("baseline", &[])]),
                    &object(&[safe("cap"), safe("baseline")]),
                    &[],
                ),
            )],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&prefer(
                    "qsPea",
                    0,
                    &[
                        ("cell", &fixtures::map(&[("exit", "\"cap\"")])),
                        ("over", &fixtures::map(&[("exit", "\"baseline\"")])),
                    ],
                )]),
            )]),
        );
        let tea = letter(
            "qsTea",
            &[
                stance(
                    "hook",
                    &surface(
                        &object(&[row("cap", &[])]),
                        &object(&[safe("baseline")]),
                        &[],
                    ),
                ),
                stance(
                    "flat",
                    &surface(
                        &object(&[row("baseline", &[])]),
                        &object(&[safe("baseline")]),
                        &[],
                    ),
                ),
            ],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[&prefer(
                    "qsTea",
                    0,
                    &[
                        (
                            "cell",
                            &fixtures::map(&[("entry", "\"cap\""), ("exit", "\"none\"")]),
                        ),
                        (
                            "over",
                            &fixtures::map(&[("entry", "\"cap\""), ("exit", "\"baseline\"")]),
                        ),
                    ],
                )]),
            )]),
        );
        let may = letter(
            "qsMay",
            &[
                stance(
                    "capped",
                    &surface(
                        &object(&[row("baseline", &[])]),
                        "{}",
                        &[("require", &fixtures::names(&["entry"]))],
                    ),
                ),
                stance("free", &surface("{}", &object(&[safe("x-height")]), &[])),
            ],
            &plain_policy(),
        );
        let it = letter(
            "qsIt",
            &[stance(
                "hook",
                &surface(
                    &object(&[row("x-height", &[])]),
                    &object(&[safe("baseline")]),
                    &[("require", &fixtures::names(&["exit"]))],
                ),
            )],
            &plain_policy(),
        );
        tall_spec_of(&[pea, tea, may, it])
    }

    /// One rune carrying two prefer records that demand disjoint stances of it with nothing to tell them apart, over two stances that tie at every score — so every window it settles raises E-AMBIGUOUS.
    fn ambiguous_spec() -> SpecIndex {
        let pea = letter(
            "qsPea",
            &[
                stance("stroke", &surface("{}", "{}", &[])),
                stance("flourish", &surface("{}", "{}", &[])),
            ],
            &fixtures::policy(&[(
                "prefer",
                &fixtures::seq(&[
                    &prefer("qsPea", 0, &[("stance", "\"stroke\"")]),
                    &prefer("qsPea", 1, &[("stance", "\"flourish\"")]),
                ]),
            )]),
        );
        let tea = letter(
            "qsTea",
            &[stance(
                "hook",
                &surface(&object(&[row("x-height", &[])]), "{}", &[]),
            )],
            &plain_policy(),
        );
        let may = letter(
            "qsMay",
            &[stance(
                "base",
                &surface(&object(&[row("baseline", &[])]), "{}", &[]),
            )],
            &plain_policy(),
        );
        spec_of(&[pea, tea, may])
    }

    /// Two structurally identical runes, declared in the reverse of sorted order, so the representative left the probe keeps shows which order it iterated in.
    fn twin_spec() -> SpecIndex {
        let twin = |name: &str| {
            letter(
                name,
                &[stance(
                    "half",
                    &surface(&object(&[row("baseline", &[])]), "{}", &[]),
                )],
                &plain_policy(),
            )
        };
        spec_of(&[twin("qsTea"), twin("qsPea")])
    }

    /// The representative lefts are the four boundaries and then one synthetic left per distinct signature, kept for the first rune in declaration order, not the first in sorted order.
    #[test]
    fn the_representative_lefts_keep_the_first_rune_in_collection_order() {
        let index = twin_spec();
        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);
        let lefts = liveness
            .representative_lefts(&mut engine, fixtures::sym(&index, "qsPea"))
            .expect("the fixture settles");
        let spelled: Vec<String> = lefts
            .iter()
            .map(|left| match left.settled.as_ref() {
                None => left.kind.as_str().to_owned(),
                Some(settled) => format!(
                    "{}.{}",
                    index.resolve(settled.cell.rune),
                    index.resolve(settled.cell.stance)
                ),
            })
            .collect();
        assert_eq!(
            spelled,
            ["edge", "space", "zwnj", "namer-dot", "qsTea.half"],
            "the twins share a signature, and the dump mentioned qsTea first while the alphabet sorts qsPea first"
        );
        assert!(
            Rc::ptr_eq(
                &lefts,
                &liveness
                    .representative_lefts(&mut engine, fixtures::sym(&index, "qsPea"))
                    .expect("the fixture settles")
            ),
            "the representative lefts are memoized per family, because the deriver reads them once per candidate third of every live context"
        );
    }

    /// A window outside every chain's deep-slot rune set, opened by the prospect branch alone: `qsPea`'s two exits tie where the third token makes `qsTea` give up its onward join, and the input letter's prefer then decides differently.
    #[test]
    fn a_chain_dead_context_opens_on_the_prospect_branch() {
        let index = prospect_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");

        let mut engine = engine_in(&index, true, true);
        let mut filter = ThirdSlotFilter::new(&index);
        assert_eq!(
            filter.matters(&mut engine, None, pea, tea, may),
            Ok(false),
            "no record on qsPea chains anywhere near the third slot, so the chain branch alone reads the window dead"
        );

        for (prospect, follower_prefers, expected) in [
            (true, true, true),
            (true, false, true),
            (false, true, false),
            (false, false, false),
        ] {
            let mut engine = engine_in(&index, prospect, follower_prefers);
            let mut liveness = ProspectLiveness::new(&index);
            assert_eq!(
                liveness.third_live(&mut engine, pea, tea, may),
                Ok(expected),
                "the prospect branch is what opens this window, so it opens exactly where the simulated prospect is scored"
            );
        }
    }

    /// The follower prefer branch opens a window the prospect branch leaves dead: `qsTea` offers no exit, so the input letter's prospect cannot change, and only `qsTea`'s follower prefer reads the third token.
    #[test]
    fn the_follower_prefer_branch_opens_a_slot_the_prospect_branch_leaves_shut() {
        let index = follower_prefer_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");

        for (prospect, follower_prefers, expected) in [
            (true, true, true),
            (true, false, false),
            (false, true, true),
            (false, false, false),
        ] {
            let mut engine = engine_in(&index, prospect, follower_prefers);
            let mut liveness = ProspectLiveness::new(&index);
            assert_eq!(
                liveness.third_live(&mut engine, pea, tea, may),
                Ok(expected),
                "the follower prefer branch is the only channel this fixture's third token has"
            );
        }

        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);
        let r1tok = index.letter(tea).expect("the fixture models it");
        let r2tok = index.letter(may).expect("the fixture models it");
        assert_eq!(
            liveness.prospect_varies_third(&mut engine, pea, tea, may, r1tok, r2tok),
            Ok(false),
            "stage one's prospect branch sees nothing here"
        );
        assert_eq!(
            liveness.follower_prefer_varies_third(&mut engine, pea, tea, may, r1tok, r2tok),
            Ok(true),
            "and its follower prefer branch is what fires"
        );
    }

    /// Stage one is `(simulated_prospect and prospect) or (follower_prefer_slots and follower prefer)`, and the `or` short-circuits: where the prospect branch fires, the follower prefer branch does not run. Both orders reach the same verdict, but a follower prefer probe records the pointers its records fire, so running the follower prefer branch first would add provenance to the product. The follower prefer branch's empty memo shows it did not run.
    #[test]
    fn a_fired_prospect_branch_leaves_the_follower_prefer_branch_unasked() {
        let index = prospect_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");
        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);

        assert_eq!(liveness.third_live(&mut engine, pea, tea, may), Ok(true));
        assert!(
            !liveness.prospect3.is_empty(),
            "the prospect branch is what fired here"
        );
        assert!(
            liveness.follower_prefer3.is_empty(),
            "and the follower prefer branch was never reached, though this follower carries a prefer record it would have probed"
        );
        assert!(
            !liveness.follower_prefer_records(tea).is_empty(),
            "which is worth saying, because a follower with no prefer records would have answered empty either way"
        );
    }

    /// The recorded fourth-slot fallback counterexample at fixture scale: the input letter's own probes agree under every third token with an `EDGE` or `UNKNOWN` fourth, and the slot opens only because a fourth slot hanging off one concrete third is live.
    #[test]
    fn the_fourth_slot_fallback_opens_a_third_slot_whose_own_input_probes_agree() {
        let index = fourth_slot_fallback_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");
        let it = fixtures::sym(&index, "qsIt");
        let r1tok = index.letter(tea).expect("the fixture models it");
        let r2tok = index.letter(may).expect("the fixture models it");
        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);

        assert_eq!(
            liveness.prospect_varies_third(&mut engine, pea, tea, may, r1tok, r2tok),
            Ok(false),
            "no third token moves the input letter's simulated prospect at an EDGE or UNKNOWN fourth"
        );
        assert_eq!(
            liveness.follower_prefer_varies_third(&mut engine, pea, tea, may, r1tok, r2tok),
            Ok(false),
            "and no follower prefer reads it either"
        );
        assert_eq!(
            liveness.input_varies(&mut engine, &mut Whole, pea, r1tok, r2tok, None),
            Ok(false),
            "the input replay agrees at the third grain — which is the whole point, since a port without the fallback would answer dead here"
        );
        assert_eq!(
            liveness.fourth_live(&mut engine, pea, tea, may, it),
            Ok(true),
            "but the fourth slot is live at the one third whose own cells need a letter past them"
        );
        assert_eq!(
            liveness.third_live(&mut engine, pea, tea, may),
            Ok(true),
            "so the fallback opens the third slot the enumeration would otherwise never consult it through"
        );
        assert_eq!(
            liveness.replay3.get(&(pea, tea, may)),
            None,
            "stage one never fired at the third slot, so the input replay was never asked — and a replay that never runs never fires the pointers it would journal"
        );

        let mut fourths: Vec<String> = Vec::new();
        for third in ["qsPea", "qsTea", "qsMay", "qsIt"] {
            if liveness
                .fourth_live(&mut engine, pea, tea, may, fixtures::sym(&index, third))
                .expect("the fixture settles")
            {
                fourths.push(third.to_owned());
            }
        }
        assert_eq!(
            fourths,
            ["qsIt"],
            "one concrete third carries the live fourth, which is what the fallback's any() is for"
        );
    }

    /// A representative left the fixpoint can never reach raises in the replay and is skipped. Counting it as a raise would open every window of an input letter that enters at one height only.
    #[test]
    fn an_unreachable_representative_left_is_skipped_rather_than_marked_live() {
        let index = fourth_slot_fallback_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");
        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);
        let lefts = liveness
            .representative_lefts(&mut engine, pea)
            .expect("the fixture settles");

        let unaccepted: Vec<SettleErrorKind> = lefts
            .iter()
            .filter_map(|left| {
                engine
                    .transition_trace(
                        left,
                        index.letter(pea).expect("the fixture models it"),
                        Slots::new(
                            index.letter(tea).expect("the fixture models it"),
                            index.letter(may).expect("the fixture models it"),
                            EDGE,
                            EDGE,
                        ),
                    )
                    .err()
                    .map(|error| error.kind())
            })
            .collect();
        assert!(
            unaccepted.contains(&SettleErrorKind::UnacceptedExit),
            "qsPea enters at the baseline alone, so the cap-committing and x-height-committing representative lefts commit exits it cannot accept"
        );
        assert!(
            unaccepted
                .iter()
                .all(|kind| *kind == SettleErrorKind::UnacceptedExit)
        );
        assert_eq!(
            liveness.input_varies(
                &mut engine,
                &mut Whole,
                pea,
                index.letter(tea).expect("the fixture models it"),
                index.letter(may).expect("the fixture models it"),
                None
            ),
            Ok(false),
            "the replay skips those lefts rather than reading their raise as movement"
        );
    }

    /// A prefer conflict is a raise the enumeration must report, so it marks the slot live instead of being skipped.
    #[test]
    fn a_raising_representative_left_marks_the_slot_live() {
        let index = ambiguous_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");
        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);
        assert_eq!(
            engine
                .transition_trace(
                    &LeftContext::boundary(TokenKind::Edge),
                    index.letter(pea).expect("the fixture models it"),
                    Slots::new(
                        index.letter(tea).expect("the fixture models it"),
                        index.letter(may).expect("the fixture models it"),
                        EDGE,
                        EDGE
                    ),
                )
                .map(|trace| trace.settled)
                .map_err(|error| error.kind()),
            Err(SettleErrorKind::Ambiguous)
        );
        assert_eq!(
            liveness.input_varies(
                &mut engine,
                &mut Whole,
                pea,
                index.letter(tea).expect("the fixture models it"),
                index.letter(may).expect("the fixture models it"),
                None
            ),
            Ok(true),
            "the baseline window itself raises, and a raise is live rather than skipped"
        );
    }

    /// The prospect and follower prefer branches key on the left-condition signature instead of the input family, so two families the follower cannot tell apart share one verdict and run its probes once.
    #[test]
    fn two_families_sharing_a_signature_share_one_probe() {
        let index = twin_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);
        let r1tok = index.letter(tea).expect("the fixture models it");
        let r2tok = index.letter(pea).expect("the fixture models it");

        assert_eq!(
            liveness.prospect_varies_third(&mut engine, pea, tea, pea, r1tok, r2tok),
            Ok(false)
        );
        assert_eq!(liveness.prospect3.len(), 1);
        assert_eq!(
            liveness.prospect_varies_third(&mut engine, tea, tea, pea, r1tok, r2tok),
            Ok(false)
        );
        assert_eq!(
            liveness.prospect3.len(),
            1,
            "the twins' probe candidates share one left-condition signature, so the second family reads the first's verdict"
        );
        assert_eq!(
            liveness.replay3.len(),
            0,
            "and the input replay is keyed on the family itself, which nothing above has asked for yet"
        );
    }

    /// A partial run trusts the run it stands behind for every ask it does not rerun, and checks the ones it does. `prospect_spec`'s third-slot probe at `qsPea·qsTea·qsMay` varies first at the `qsIt` token's `EDGE` ask, step `1 + 2 * 4` among the four boundaries and the four letters sorted by name. Behind that stop, rerunning the asks that name `qsIt` reaches the same verdict without diverging, and so does rerunning none, since the ask at the stop is then taken to vary. Behind a stop that says the probe ran out, the rerun `qsIt` ask varies before it, so the run diverges and the caller runs the whole probe.
    #[test]
    fn a_partial_probe_run_agrees_behind_its_own_stop_and_diverges_behind_another() {
        let index = prospect_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let it = fixtures::sym(&index, "qsIt");
        let r1tok = index.letter(tea).expect("the fixture models it");
        let r2tok = index
            .letter(fixtures::sym(&index, "qsMay"))
            .expect("the fixture models it");
        let mut liveness = ProspectLiveness::new(&index);
        let shapes = liveness.probe_candidate_shapes(pea);
        let mut engine = engine_in(&index, true, true);
        let (stance, junction, stop) = shapes
            .iter()
            .find_map(|&(stance, junction)| {
                let mut publishing = Publishing::new();
                let live = liveness
                    .third_class_live(
                        &mut engine,
                        &mut publishing,
                        pea,
                        stance,
                        junction,
                        r1tok,
                        r2tok,
                    )
                    .expect("the probe settles");
                live.then_some((stance, junction, publishing.stopped))
            })
            .expect("some shape of qsPea varies with the third token");
        assert_eq!(stop, 1 + 2 * 4, "the first variance is at qsIt's EDGE ask");
        let marked: Rc<[bool]> = Rc::from(Exclusion::of(&index, [it]).named());
        let unmarked: Rc<[bool]> = Rc::from(Vec::new());
        for (behind, rerun, verdict, diverged) in [
            (stop, &marked, true, false),
            (stop, &unmarked, true, false),
            (VerdictEntry::RAN_OUT, &marked, true, true),
        ] {
            let mut engine = engine_in(&index, true, true);
            let mut partial = Partial::behind(behind, Rc::clone(rerun));
            assert_eq!(
                liveness.third_class_live(
                    &mut engine,
                    &mut partial,
                    pea,
                    stance,
                    junction,
                    r1tok,
                    r2tok
                ),
                Ok(verdict)
            );
            assert_eq!(partial.diverged, diverged, "behind stop {behind}");
        }
    }

    /// Liveness and fibers under their real caller: a whole configuration enumerated at class grain expands, member set by member set, to the same window rows a label-grain enumeration (`--deep-classes-off`) emits. The fixpoint's partition assertion also runs on both products.
    #[test]
    fn a_class_grain_enumeration_expands_to_the_label_grain_one() {
        for index in [prospect_spec(), follower_prefer_spec()] {
            let grains: Vec<Vec<String>> = [true, false]
                .into_iter()
                .map(|deep_classes| {
                    let product = enumerate_transitions(
                        &index,
                        &[],
                        EnumerationModes {
                            simulated_prospect: true,
                            follower_prefer_slots: true,
                            deep_classes,
                        },
                    )
                    .expect("the fixture enumerates");
                    assert_eq!(deep_classes, !product.deep_classes.is_empty());
                    expanded_windows(&product)
                })
                .collect();
            assert_eq!(grains[0], grains[1]);
        }
    }

    /// One product's window rows with every deep-class token replaced by its members, as `DecisionTable.expanded_transitions` does for the two slots a class id can occupy.
    fn expanded_windows(product: &FixpointProduct) -> Vec<String> {
        let classes: HashMap<&str, &Vec<String>> = product
            .deep_classes
            .iter()
            .map(|(id, members)| (id.as_str(), members))
            .collect();
        let members = |label: &str| -> Vec<String> {
            classes
                .get(label)
                .map_or_else(|| vec![label.to_owned()], |members| (*members).clone())
        };
        let mut out: Vec<String> = Vec::new();
        for row in &product.transitions {
            let key = row.key(&product.labels);
            for third in &members(key[4]) {
                for fourth in &members(key[5]) {
                    out.push(format!(
                        "{}\t{}\t{}\t{}\t{third}\t{fourth}\t{}",
                        key[0],
                        key[1],
                        key[2],
                        key[3],
                        product.outcome(row)
                    ));
                }
            }
        }
        out.sort();
        out
    }
}
