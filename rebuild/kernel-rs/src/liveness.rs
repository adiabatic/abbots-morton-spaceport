//! The simulated-prospect arm of the two deep-slot filters (issue 28 stage 2): whether a raw deep token can move the settled outcome of some reachable window at `(input, right1, right2)`. [`crate::census`] owns the chain arm and reaches in here only where that arm said no, which is the whole of the pinned world's verdict and the cheap half of the shipping world's.
//!
//! Two value-level stages, because every cheaper grain fails in a measured way. Consultation-level tracking over-opens catastrophically — the recursion consults beyond-window slots almost everywhere — and stopping at follower-prospect variance still over-opens fifteenfold on the real spec (1,543 consulted triples carry a token-movable prospect where only 103 ever move a seat outcome), enough to push the emitted settlement lookup through the subtable-offset headroom floor read-back holds the font to. Stage one is the cheap prefilter: for each `(stance, seam)` shape the input can commit — the virtual left's entry is never read, so entry states collapse — the follower's simulated prospect is evaluated per concrete token and compared against the `EDGE` a dead slot bakes, and no variance anywhere means no channel into the seat's ranking at all, because a deep token reaches the flag-on kernel only through prospect values and own-rune chains and the chain arm has already answered for the chains. Stage two, only where stage one fired, probes at outcome grain: the seat's own transition is replayed per token over the collapsed left classes — every `(family, stance, seam)` virtual left plus the four boundary kinds, collapsed by the input-frame signature — and the slot is live only where some class's settled cell varies.
//!
//! The signature that collapses the left classes is `(seam, verdicts)`, where the verdicts are [`Engine::cond_matches_left`] over the follower's own left-reading conditions in the order they are gathered: per-stance entry-row scopes, then unlock left-whens, then the `refuse` + `prefer` + `resolve` left-whens. Those verdicts are plain booleans and not the tri-state a right condition answers with — a left is always already settled or already known to be a boundary, so nothing about it can be outside the window; the only non-answer `cond_matches_left` has is the raise a `then:` on a left condition earns. The left it is asked about is virtual: `CellId(rune=family, stance=stance, entry=None, exit=seam, adjustments=())` inside a `Settled` at that seam with no extension. Extend and contract records shape adjustments only, and neither an extension nor the left cell's entry interacts with a deep token, so the enumerated shapes cover every reachable settled left. A left class the fixpoint can never reach raises E-STRANDED in the replay and is skipped; a prefer conflict raising E-INCOMPARABLE or E-AMBIGUOUS marks the slot live instead, so the enumeration surfaces the conflict properly rather than hiding it behind a dead slot. Those three outcomes stay distinct all the way down — see [`crate::error::SettleError`], whose four variants exist for exactly this reason.
//!
//! With shifted vote slots on (stage 4b) stage one grows a vote arm beside the prospect arm, probing [`Engine::probe_prefer_favors`]' vote branch itself: a vote reads the deep slots twice over, through its record's shifted `when:` chain and through the follower-cell enumeration the vote runs over the shifted window, so a row scope or closure verdict that moves with the token moves which continuations the vote can favor. A same-family seam is skipped — the own branch shadows the vote there and the chain arm already models it — and a follower with no `prefer` records is skipped before any shape loop.
//!
//! [`ProspectLiveness::third_live`] additionally ORs in [`ProspectLiveness::fourth_live`] over every concrete letter third, and that belt is not decoration: a live fourth slot hanging off an unenumerated third would never be consulted, and the per-token comparisons alone cannot see a seat that moves only under a specific `(third, fourth)` letter pair, because unknown-optimism bottoms the recursion identically for an `EDGE` fourth and an `UNKNOWN` one. The recorded counterexample is `·See·No·No·Roe·No·Oy` — seat `qsNo`, window `(qsNo, qsRoe, qsNo, qsOy)`, left `·See` — where the fourth-slot `·Oy` flips the seat through two simulation levels while every EDGE/UNKNOWN-fourth agrees.
//!
//! Evaluation order is output, not style. Every probe journals the pointers it fires into `Engine::fired`, which the fixpoint reports as the product's `cited_provenance`, so a probe that never runs never fires: each short-circuit, each early return, each loop order and each memo key's grain is fixed, ported from the Python fixpoint retired at issue #78 and held ever since. The memo grain in particular is contract — the prospect and vote arms key on the collapsed *signature* rather than the input family, so two families sharing a signature share one verdict and run its side effects once, while the seat replay and the joint34 belt key on the family itself.
//!
//! One instance per build, lent to both filters and to [`crate::fiber::DeepFiberDeriver`], and holding no engine of its own. The filters take the engine per call precisely so a second one cannot exist, which is what lets the probe hold no engine and need no cache keyed on one.

use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use crate::engine::{Engine, Slots};
use crate::error::{SettleError, SettleErrorKind};
use crate::index::SpecIndex;
use crate::model::{Condition, PolicyRecord, Sym};
use crate::types::{
    Candidate, CellId, EDGE, LeftContext, NAMER_DOT, NO_EXIT_INDEX, RightToken, SPACE, Settled,
    TokenKind, UNKNOWN, ZWNJ,
};

/// One shape the input frame can commit: a stance of the input's own rune and the seam it offers there, `None` for the shape that offers none.
type Shape = (Sym, Option<Sym>);

/// The collapsed input-frame signature: the committed seam, then the follower's own left-reading conditions answered against the virtual left in the order [`ProspectLiveness::left_conditions`] gathers them. Behind an [`Rc`] because it is copied into every memo key the prospect and vote arms write.
type Signature = (Option<Sym>, Rc<Vec<bool>>);

/// What the seat replay saw at one probed window. The two sentinels are distinct from each other and from every cell: a raise says the enumeration must surface the conflict and therefore that the slot is live, while an unreachable window says this left class is not the fixpoint's to reach and the replay skips it.
#[derive(Clone, Debug, PartialEq, Eq)]
enum SeatOutcome {
    Cell(CellId),
    Raised,
    Unreachable,
}

/// The liveness probe for one spec. Verdicts and the structures behind them are memoized here; the engine they are probed through arrives per call, so the memo cannot outlive its world or fork across two engines.
pub struct ProspectLiveness<'i> {
    index: &'i SpecIndex,
    tokens: Option<Rc<Vec<RightToken>>>,
    left_classes: HashMap<Sym, Rc<Vec<LeftContext>>>,
    shapes: HashMap<Sym, Rc<Vec<Shape>>>,
    conds: HashMap<Sym, Rc<Vec<&'i Condition>>>,
    sigs: HashMap<(Sym, Sym, Sym, Option<Sym>), Signature>,
    /// `("seat3", family, right1, right2)` — the third-slot seat replay.
    seat3: HashMap<(Sym, Sym, Sym), bool>,
    /// `("joint34", family, right1, right2)` — the belt over every concrete letter third.
    joint34: HashMap<(Sym, Sym, Sym), bool>,
    /// `(right1, right2, signature)` — the third slot's prospect arm.
    prospect3: HashMap<(Sym, Sym, Signature), bool>,
    /// `("vote3", right1, right2, signature)` — the third slot's vote arm.
    vote3: HashMap<(Sym, Sym, Signature), bool>,
    /// `("seat4", family, right1, right2, right3)` — the fourth-slot seat replay.
    seat4: HashMap<(Sym, Sym, Sym, Sym), bool>,
    /// `(right1, right2, right3, signature)` — the fourth slot's prospect arm.
    prospect4: HashMap<(Sym, Sym, Sym, Signature), bool>,
    /// `("vote4", right1, right2, right3, signature)` — the fourth slot's vote arm.
    vote4: HashMap<(Sym, Sym, Sym, Signature), bool>,
}

impl<'i> ProspectLiveness<'i> {
    /// The probe over one spec, with every memo empty.
    pub fn new(index: &'i SpecIndex) -> Self {
        Self {
            index,
            tokens: None,
            left_classes: HashMap::new(),
            shapes: HashMap::new(),
            conds: HashMap::new(),
            sigs: HashMap::new(),
            seat3: HashMap::new(),
            joint34: HashMap::new(),
            prospect3: HashMap::new(),
            vote3: HashMap::new(),
            seat4: HashMap::new(),
            prospect4: HashMap::new(),
            vote4: HashMap::new(),
        }
    }

    /// Whether the raw third slot can move some reachable window at `(family, right1, right2)`.
    ///
    /// Stage one is `(simulated_prospect and _prospect_varies_third) or (vote_slots and _vote_varies_third)`, short-circuiting exactly there; where it fires, the seat replay at `("seat3", family, right1, right2)` answers and a true verdict returns immediately. Where that path did not return — stage one dead, or the seat replay saw nothing move — the joint34 belt at `("joint34", family, right1, right2)` is the verdict: [`ProspectLiveness::fourth_live`] over every letter probe token, in [`ProspectLiveness::probe_tokens`] order, early-exiting on the first live one.
    ///
    /// The slots the prospect arm probes are `(right1, right2, token, EDGE)` against the baseline `(right1, right2, EDGE, EDGE)`, then `(right1, right2, token, UNKNOWN)` against that token's own EDGE-fourth value: the probed token rides the third slot and the belt inside the arm is the EDGE-versus-UNKNOWN fourth.
    pub fn third_live(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        right1: Sym,
        right2: Sym,
    ) -> Result<bool, SettleError> {
        let r1tok = RightToken::Letter(right1);
        let r2tok = RightToken::Letter(right2);
        let mut stage_one = false;
        if engine.simulated_prospect() {
            stage_one = self.prospect_varies_third(engine, family, right1, right2, r1tok, r2tok)?;
        }
        if !stage_one && engine.vote_slots() {
            stage_one = self.vote_varies_third(engine, family, right1, right2, r1tok, r2tok)?;
        }
        if stage_one {
            let key = (family, right1, right2);
            let verdict = match self.seat3.get(&key) {
                Some(&cached) => cached,
                None => {
                    let verdict = self.seat_varies(engine, family, r1tok, r2tok, None)?;
                    self.seat3.insert(key, verdict);
                    verdict
                }
            };
            if verdict {
                return Ok(true);
            }
        }
        let key = (family, right1, right2);
        if let Some(&cached) = self.joint34.get(&key) {
            return Ok(cached);
        }
        let tokens = self.probe_tokens();
        let mut verdict = false;
        for token in tokens.iter() {
            if let RightToken::Letter(third) = *token
                && self.fourth_live(engine, family, right1, right2, third)?
            {
                verdict = true;
                break;
            }
        }
        self.joint34.insert(key, verdict);
        Ok(verdict)
    }

    /// Whether the raw fourth slot can move some reachable window at `(family, right1, right2, right3)`.
    ///
    /// The same two stages one slot deeper, and with no belt: stage one dead is a dead slot outright, and where it fired the seat replay at `("seat4", family, right1, right2, right3)` is the whole verdict. The prospect arm's slots are `(right1, right2, right3, token)` against the baseline `(right1, right2, right3, EDGE)` — the probed token rides the fourth slot alone.
    pub fn fourth_live(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        right1: Sym,
        right2: Sym,
        right3: Sym,
    ) -> Result<bool, SettleError> {
        let r1tok = RightToken::Letter(right1);
        let r2tok = RightToken::Letter(right2);
        let r3tok = RightToken::Letter(right3);
        let mut stage_one = false;
        if engine.simulated_prospect() {
            stage_one = self.prospect_varies_fourth(
                engine, family, right1, right2, right3, r1tok, r2tok, r3tok,
            )?;
        }
        if !stage_one && engine.vote_slots() {
            stage_one = self
                .vote_varies_fourth(engine, family, right1, right2, right3, r1tok, r2tok, r3tok)?;
        }
        if !stage_one {
            return Ok(false);
        }
        let key = (family, right1, right2, right3);
        if let Some(&cached) = self.seat4.get(&key) {
            return Ok(cached);
        }
        let verdict = self.seat_varies(engine, family, r1tok, r2tok, Some(r3tok))?;
        self.seat4.insert(key, verdict);
        Ok(verdict)
    }

    /// The left classes the seat replay and the fiber probes read this family against: the four boundary lefts first, then one virtual `(family, stance, seam)` left per distinct input-frame signature.
    ///
    /// The iteration is over the spec's runes in *collection* order rather than sorted order, and the representative kept per signature is the **first** one encountered — a different order keeps a different virtual left, which can move both a liveness verdict and a fiber key. [`SpecIndex::runes`] preserves the dump's order, which is the order Python's `spec.runes` dict has, so the two agree by construction.
    ///
    /// Memoized per family behind an [`Rc`], because the deriver reads the same list once per candidate third token of every live context.
    pub fn seat_left_classes(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
    ) -> Result<Rc<Vec<LeftContext>>, SettleError> {
        if let Some(cached) = self.left_classes.get(&family) {
            return Ok(Rc::clone(cached));
        }
        let mut out = vec![
            LeftContext::boundary(TokenKind::Edge),
            LeftContext::boundary(TokenKind::Space),
            LeftContext::boundary(TokenKind::Zwnj),
            LeftContext::boundary(TokenKind::NamerDot),
        ];
        let mut seen: HashSet<Signature> = HashSet::new();
        let left_families: Vec<Sym> = self.index.runes().iter().map(|(name, _)| *name).collect();
        for left_family in left_families {
            for (stance, seam) in self.input_shapes(left_family).iter().copied() {
                let signature = self.signature(engine, family, left_family, stance, seam)?;
                if !seen.insert(signature) {
                    continue;
                }
                out.push(virtual_left(left_family, stance, seam));
            }
        }
        let classes = Rc::new(out);
        self.left_classes.insert(family, Rc::clone(&classes));
        Ok(classes)
    }

    /// The alphabet every probe sweeps: the four boundaries in their own order, then one letter token per rune sorted by name. Built once and handed out behind an [`Rc`].
    ///
    /// The order is contract rather than convenience. Every arm that sweeps these tokens early-exits on the first variance it sees, and a probe that never runs never fires, so a different order journals a different `cited_provenance` — and the deriver indexes its probe matrix by position in this list with `UNKNOWN` appended after it, so a different order also moves which r4 tokens group together.
    pub fn probe_tokens(&mut self) -> Rc<Vec<RightToken>> {
        if self.tokens.is_none() {
            let mut tokens = vec![EDGE, SPACE, ZWNJ, NAMER_DOT];
            let mut letters: Vec<Sym> = self.index.runes().iter().map(|(name, _)| *name).collect();
            letters
                .sort_by(|left, right| self.index.resolve(*left).cmp(self.index.resolve(*right)));
            tokens.extend(letters.into_iter().map(RightToken::Letter));
            self.tokens = Some(Rc::new(tokens));
        }
        Rc::clone(self.tokens.as_ref().expect("the token list was just built"))
    }

    /// The `(stance, seam)` shapes this family's input frame can commit, in the stances' declaration order.
    ///
    /// A stance requiring an exit contributes no unentered shape, and every other stance leads with one; then the declared exit rows in their own order, then the exits an unlock adds that the surface does not already declare. The seams are deduped per stance keeping the first occurrence.
    fn input_shapes(&mut self, family: Sym) -> Rc<Vec<Shape>> {
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
            let mut seams: Vec<Option<Sym>> = if surface.require.contains(&vocab.exit) {
                Vec::new()
            } else {
                vec![None]
            };
            seams.extend(surface.exits.iter().map(|(height, _)| Some(*height)));
            for unlock in &surface.unlocks {
                if let Some(exit) = unlock.exit
                    && !surface.exits.iter().any(|(height, _)| *height == exit)
                {
                    seams.push(Some(exit));
                }
            }
            let mut seen: HashSet<Option<Sym>> = HashSet::new();
            for seam in seams {
                if seen.insert(seam) {
                    out.push((*stance_name, seam));
                }
            }
        }
        let shapes = Rc::new(out);
        self.shapes.insert(family, Rc::clone(&shapes));
        shapes
    }

    /// The follower's own left-reading conditions, in the order they are gathered: per stance, every entry row's scope and then every unlock's left `when:`, and after all the stances the `refuse`, `prefer` and `resolve` records' left `when:`s in that order.
    ///
    /// The order is what the signature vector's positions mean, so it is contract rather than convenience — two specs gathering the same conditions differently would collapse different left classes together.
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

    /// The follower's `prefer` records — the records the vote arm probes, in declaration order, and an empty list is the whole reason the arm can answer before any shape loop. The list is already in the model, so this reads it straight through and no memo is needed.
    fn vote_records(&self, follower: Sym) -> &'i [PolicyRecord] {
        let index = self.index;
        &index
            .rune(follower)
            .unwrap_or_else(|| panic!("{} is modeled", index.resolve(follower)))
            .policy
            .prefer
    }

    /// The input frame's collapsed signature at this shape: the seam it commits, and the follower's left conditions answered against the virtual left that shape stands for.
    ///
    /// The verdicts are plain booleans rather than the tri-state a right condition answers with — a left is always already settled or already known to be a boundary, so nothing about it can be outside the window. A condition carrying a `then:` raises here, exactly as [`Engine::cond_matches_left`] refuses one, and the raise leaves no memo entry behind — so a second ask raises again rather than answering.
    fn signature(
        &mut self,
        engine: &mut Engine<'_>,
        follower: Sym,
        family: Sym,
        stance: Sym,
        seam: Option<Sym>,
    ) -> Result<Signature, SettleError> {
        let key = (follower, family, stance, seam);
        if let Some(cached) = self.sigs.get(&key) {
            return Ok(cached.clone());
        }
        let left = virtual_left(family, stance, seam);
        let conds = self.left_conditions(follower);
        let mut verdicts: Vec<bool> = Vec::with_capacity(conds.len());
        for cond in conds.iter() {
            verdicts.push(engine.cond_matches_left(Some(follower), cond, &left, seam)?);
        }
        let signature: Signature = (seam, Rc::new(verdicts));
        self.sigs.insert(key, signature.clone());
        Ok(signature)
    }

    /// Stage one's prospect arm at the third slot: some shape of the input frame whose simulated follower choice moves with the third token, memoized on `(right1, right2, signature)` so two families sharing a signature share one verdict and run its probes once.
    fn prospect_varies_third(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        right1: Sym,
        right2: Sym,
        r1tok: RightToken,
        r2tok: RightToken,
    ) -> Result<bool, SettleError> {
        for (stance, seam) in self.input_shapes(family).iter().copied() {
            let signature = self.signature(engine, right1, family, stance, seam)?;
            let key = (right1, right2, signature);
            let verdict = match self.prospect3.get(&key) {
                Some(&cached) => cached,
                None => {
                    let verdict =
                        self.third_class_live(engine, family, stance, seam, r1tok, r2tok)?;
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

    /// One shape's third-slot prospect probe. The probed token rides the third slot against the `EDGE` a dead slot bakes, and each token's own unknown-fourth evaluation is compared against its edge-fourth one — the belt that catches a prospect the fourth slot moves at this third.
    fn third_class_live(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        stance: Sym,
        seam: Option<Sym>,
        r1tok: RightToken,
        r2tok: RightToken,
    ) -> Result<bool, SettleError> {
        let candidate = frame_candidate(stance, seam);
        let baseline =
            engine.probe_prospect(family, candidate, Slots::new(r1tok, r2tok, EDGE, EDGE))?;
        let tokens = self.probe_tokens();
        for &token in tokens.iter() {
            let edge4 =
                engine.probe_prospect(family, candidate, Slots::new(r1tok, r2tok, token, EDGE))?;
            if edge4 != baseline {
                return Ok(true);
            }
            if engine.probe_prospect(family, candidate, Slots::new(r1tok, r2tok, token, UNKNOWN))?
                != edge4
            {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// Stage one's prospect arm at the fourth slot — [`ProspectLiveness::prospect_varies_third`] with the concrete third in the memo key.
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
        for (stance, seam) in self.input_shapes(family).iter().copied() {
            let signature = self.signature(engine, right1, family, stance, seam)?;
            let key = (right1, right2, right3, signature);
            let verdict = match self.prospect4.get(&key) {
                Some(&cached) => cached,
                None => {
                    let verdict =
                        self.fourth_class_live(engine, family, stance, seam, r1tok, r2tok, r3tok)?;
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

    /// One shape's fourth-slot prospect probe: the probed token rides the fourth slot alone, against the edge-fourth baseline, with no belt to wear at the bottom of the window.
    #[allow(clippy::too_many_arguments)]
    fn fourth_class_live(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        stance: Sym,
        seam: Option<Sym>,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: RightToken,
    ) -> Result<bool, SettleError> {
        let candidate = frame_candidate(stance, seam);
        let baseline =
            engine.probe_prospect(family, candidate, Slots::new(r1tok, r2tok, r3tok, EDGE))?;
        let tokens = self.probe_tokens();
        for &token in tokens.iter() {
            if engine.probe_prospect(family, candidate, Slots::new(r1tok, r2tok, r3tok, token))?
                != baseline
            {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// Stage one's vote arm at the third slot. A same-family seam never votes — `_apply_prefers`' second gather duplicates the owner and the own branch shadows the vote, whose real slots the chain arm already models — and a follower carrying no `prefer` records is answered before any shape loop.
    fn vote_varies_third(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        right1: Sym,
        right2: Sym,
        r1tok: RightToken,
        r2tok: RightToken,
    ) -> Result<bool, SettleError> {
        if right1 == family || self.vote_records(right1).is_empty() {
            return Ok(false);
        }
        for (stance, seam) in self.input_shapes(family).iter().copied() {
            let signature = self.signature(engine, right1, family, stance, seam)?;
            let key = (right1, right2, signature);
            let verdict = match self.vote3.get(&key) {
                Some(&cached) => cached,
                None => {
                    let verdict =
                        self.vote_class_live(engine, family, stance, seam, r1tok, r2tok, None)?;
                    self.vote3.insert(key, verdict);
                    verdict
                }
            };
            if verdict {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// Stage one's vote arm at the fourth slot, on the same two terms one slot deeper.
    #[allow(clippy::too_many_arguments)]
    fn vote_varies_fourth(
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
        if right1 == family || self.vote_records(right1).is_empty() {
            return Ok(false);
        }
        for (stance, seam) in self.input_shapes(family).iter().copied() {
            let signature = self.signature(engine, right1, family, stance, seam)?;
            let key = (right1, right2, right3, signature);
            let verdict = match self.vote4.get(&key) {
                Some(&cached) => cached,
                None => {
                    let verdict = self.vote_class_live(
                        engine,
                        family,
                        stance,
                        seam,
                        r1tok,
                        r2tok,
                        Some(r3tok),
                    )?;
                    self.vote4.insert(key, verdict);
                    verdict
                }
            };
            if verdict {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// Whether some follower vote's verdict at this seat moves with the probed deep token.
    ///
    /// The vote branch of [`Engine::probe_prefer_favors`] is probed directly, because a vote reads the deep slots twice over — through its record's shifted `when:` chain and through the follower-cell enumeration it runs over the shifted window. `r3tok` absent probes the third slot, wearing the same edge-versus-unknown belt the prospect arm wears; a concrete `r3tok` probes the fourth at that third. The records are the outer loop and the probe tokens the inner one, which is the order a verdict short-circuits in.
    #[allow(clippy::too_many_arguments)]
    fn vote_class_live(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        stance: Sym,
        seam: Option<Sym>,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: Option<RightToken>,
    ) -> Result<bool, SettleError> {
        let candidate = frame_candidate(stance, seam);
        let owner = r1tok.letter();
        let edge_left = LeftContext::boundary(TokenKind::Edge);
        let records = self.vote_records(owner);
        let tokens = self.probe_tokens();
        for record in records.iter() {
            match r3tok {
                None => {
                    let baseline = engine.probe_prefer_favors(
                        owner,
                        record,
                        family,
                        candidate,
                        &edge_left,
                        Slots::new(r1tok, r2tok, EDGE, EDGE),
                    )?;
                    for &token in tokens.iter() {
                        let edge4 = engine.probe_prefer_favors(
                            owner,
                            record,
                            family,
                            candidate,
                            &edge_left,
                            Slots::new(r1tok, r2tok, token, EDGE),
                        )?;
                        if edge4 != baseline {
                            return Ok(true);
                        }
                        if engine.probe_prefer_favors(
                            owner,
                            record,
                            family,
                            candidate,
                            &edge_left,
                            Slots::new(r1tok, r2tok, token, UNKNOWN),
                        )? != edge4
                        {
                            return Ok(true);
                        }
                    }
                }
                Some(third) => {
                    let baseline = engine.probe_prefer_favors(
                        owner,
                        record,
                        family,
                        candidate,
                        &edge_left,
                        Slots::new(r1tok, r2tok, third, EDGE),
                    )?;
                    for &token in tokens.iter() {
                        if engine.probe_prefer_favors(
                            owner,
                            record,
                            family,
                            candidate,
                            &edge_left,
                            Slots::new(r1tok, r2tok, third, token),
                        )? != baseline
                        {
                            return Ok(true);
                        }
                    }
                }
            }
        }
        Ok(false)
    }

    /// Stage two: the seat's own transition replayed per probe token over its collapsed left classes, live exactly where some class's settled cell moves.
    ///
    /// A left whose baseline is unreachable is skipped rather than counted — the fixpoint can never reach it either — while a raise at the baseline is live outright, and so is any probe token that raises, becomes unreachable, or lands on a different cell. `r3tok` absent probes the third slot with the fourth held first to `EDGE` and then to `UNKNOWN`; a concrete `r3tok` probes the fourth alone.
    fn seat_varies(
        &mut self,
        engine: &mut Engine<'_>,
        family: Sym,
        r1tok: RightToken,
        r2tok: RightToken,
        r3tok: Option<RightToken>,
    ) -> Result<bool, SettleError> {
        let token = RightToken::Letter(family);
        let lefts = self.seat_left_classes(engine, family)?;
        let tokens = self.probe_tokens();
        for left in lefts.iter() {
            let third = r3tok.unwrap_or(EDGE);
            let baseline = seat_outcome(engine, left, token, Slots::new(r1tok, r2tok, third, EDGE));
            let baseline = match baseline {
                SeatOutcome::Raised => return Ok(true),
                SeatOutcome::Unreachable => continue,
                SeatOutcome::Cell(cell) => cell,
            };
            for &probe in tokens.iter() {
                match r3tok {
                    None => {
                        let edge4 = seat_outcome(
                            engine,
                            left,
                            token,
                            Slots::new(r1tok, r2tok, probe, EDGE),
                        );
                        let SeatOutcome::Cell(edge4) = edge4 else {
                            return Ok(true);
                        };
                        if edge4 != baseline {
                            return Ok(true);
                        }
                        let unknown4 = seat_outcome(
                            engine,
                            left,
                            token,
                            Slots::new(r1tok, r2tok, probe, UNKNOWN),
                        );
                        let SeatOutcome::Cell(unknown4) = unknown4 else {
                            return Ok(true);
                        };
                        if unknown4 != edge4 {
                            return Ok(true);
                        }
                    }
                    Some(third) => {
                        let varied = seat_outcome(
                            engine,
                            left,
                            token,
                            Slots::new(r1tok, r2tok, third, probe),
                        );
                        let SeatOutcome::Cell(varied) = varied else {
                            return Ok(true);
                        };
                        if varied != baseline {
                            return Ok(true);
                        }
                    }
                }
            }
        }
        Ok(false)
    }
}

/// The virtual left one `(family, stance, seam)` shape stands for: the cell with no entry and no adjustments, settled at that seam with no extension. The entry is never read by anything a deep token can reach, which is what lets the whole entry axis collapse.
fn virtual_left(family: Sym, stance: Sym, seam: Option<Sym>) -> LeftContext {
    LeftContext::letter(Settled {
        cell: CellId {
            rune: family,
            stance,
            entry: None,
            exit: seam,
            adjustments: Vec::new(),
        },
        seam,
        extension: 0,
    })
}

/// The bare input-frame candidate every stage-one probe is run for, Python's `Candidate(stance, None, seam, 0)`: no entry, the shape's own seam, the first order index, and the sentinel exit seat, because the frame is a shape the input can commit rather than a candidate the enumeration produced.
fn frame_candidate(stance: Sym, seam: Option<Sym>) -> Candidate {
    Candidate {
        stance,
        entry: None,
        seam,
        order_index: 0,
        exit_index: NO_EXIT_INDEX,
    }
}

/// One replayed seat window's outcome. E-INCOMPARABLE and E-AMBIGUOUS are the raise the enumeration must surface; every other settlement outcome is a window this left cannot reach.
fn seat_outcome(
    engine: &mut Engine<'_>,
    left: &LeftContext,
    token: RightToken,
    slots: Slots,
) -> SeatOutcome {
    match engine.with_settled(left, token, slots, |settled| settled.cell.clone()) {
        Ok(cell) => SeatOutcome::Cell(cell),
        Err(error) => match error.kind() {
            SettleErrorKind::Incomparable | SettleErrorKind::Ambiguous => SeatOutcome::Raised,
            SettleErrorKind::Stranded | SettleErrorKind::Plain => SeatOutcome::Unreachable,
        },
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::census::ThirdSlotFilter;
    use crate::engine::EngineModes;
    use crate::fixpoint::{EnumerationModes, enumerate_transitions};
    use crate::index::fixtures;
    use crate::stream::FixpointProduct;

    /// A JSON object over already-built pieces, for the mappings whose keys these fixtures compose rather than spell.
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
        row(height, &[("withdrawal", "\"safe\"")])
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
    fn prefer(rune: &str, seat: usize, overrides: &[(&str, &str)]) -> String {
        let pointer =
            fixtures::names(&[&format!("{rune}.yaml"), &format!("policy.prefer[{seat}]")]);
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

    /// The engine the probes are lent, in whichever of the four mode worlds the caller names. The trace memo is on because the fixpoint's is, and because the memo's replayed fired delta is what makes a warm probe cost what a cold one journals.
    fn engine_in(index: &SpecIndex, simulated_prospect: bool, vote_slots: bool) -> Engine<'_> {
        Engine::with_modes(
            index,
            Vec::<Sym>::new(),
            EngineModes {
                simulated_prospect,
                vote_slots,
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

    /// The four boundaries lead in their own order, the letters follow sorted by name — not by the order the dump mentioned them in, which this fixture deliberately scrambles.
    #[test]
    fn the_probe_alphabet_is_the_boundaries_then_the_letters_by_name() {
        let index = spec();
        let mut liveness = ProspectLiveness::new(&index);
        let tokens = liveness.probe_tokens();
        let spelled: Vec<String> = tokens
            .iter()
            .map(|token| match token {
                RightToken::Letter(rune) => index.resolve(*rune).to_owned(),
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

    /// The issue-28 shape, `rebuild/pipeline/fixtures.py`'s `prospect_spec`: `qsPea` exits at both heights and prefers the x-height as a yielding tie-break; `qsTea` enters at both, is exitless when entered at the x-height, and yields its own baseline exit exactly where the slots past it spell `qsMay·qsIt`; an entered `qsMay` is exitless, so `qsTea` joining `qsMay` forecloses that onward join while `qsTea` declining buys it. Nothing here chains far enough to be censused, so every verdict below is the liveness arm's alone.
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

    /// The stage-4b shape: `qsPea` offers a baseline exit and an x-height exit that tie at every score, `qsTea` accepts one height per stance and offers no exit at all — so nothing about the seat's *prospect* can move — and `qsTea`'s own `prefer` votes for its `hook` continuation exactly where the slots past the seat spell `qsMay·qsIt`. The vote is the only channel the third token has here.
    fn vote_spec() -> SpecIndex {
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

    /// The four-family registry with a third height, for the fixtures whose whole point is that one rune's exit height is reachable from exactly one other rune's entry.
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

    /// The recorded joint34 shape at fixture scale: a seat whose settled cell moves under one specific `(third, fourth)` letter pair and under nothing else.
    ///
    /// `qsIt` requires an exit, so it has cells at all only where the fourth slot is a letter that accepts its baseline exit — the one r4 dependence that reads `EDGE` and `UNKNOWN` alike, since neither is a letter and the closure asks for a letter. `qsMay` exits at the x-height, which only `qsIt` enters, so `qsMay`'s onward join is available exactly at that third; joining it ties `qsTea`'s two cells, and `qsTea`'s cap-entered prefer yields the join there. That yields the seat's cap exit its prospect, which is the tie its own prefer would otherwise win — so `qsPea` settles into its cap exit everywhere except at `(qsIt, qsPea)`, where it settles into its baseline one.
    fn belt_spec() -> SpecIndex {
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

    /// Two structurally identical runes, declared in an order the sorted alphabet reverses, so that the collapse's kept representative says which order it iterated in.
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

    /// The left classes are the four boundaries and then one virtual left per distinct signature — kept for the *first* rune the spec collected, not the first sorted one, because a different representative can move both a liveness verdict and a fiber key.
    #[test]
    fn the_left_class_collapse_keeps_the_first_representative_in_collection_order() {
        let index = twin_spec();
        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);
        let classes = liveness
            .seat_left_classes(&mut engine, fixtures::sym(&index, "qsPea"))
            .expect("the fixture settles");
        let spelled: Vec<String> = classes
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
                &classes,
                &liveness
                    .seat_left_classes(&mut engine, fixtures::sym(&index, "qsPea"))
                    .expect("the fixture settles")
            ),
            "the collapse is memoized per family, because the deriver reads it once per candidate third of every live context"
        );
    }

    /// A window no chain censuses, opened by the prospect arm alone: `qsPea`'s two exits tie exactly where the third token makes `qsTea` yield its own onward join, and the seat's prefer then decides differently.
    #[test]
    fn a_chain_dead_context_opens_on_the_prospect_arm() {
        let index = prospect_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");

        let mut engine = engine_in(&index, true, true);
        let mut filter = ThirdSlotFilter::new(&index);
        assert_eq!(
            filter.matters(&mut engine, None, pea, tea, may),
            Ok(false),
            "no record on qsPea chains anywhere near the third slot, so the chain arm alone reads the window dead"
        );

        for (prospect, votes, expected) in [
            (true, true, true),
            (true, false, true),
            (false, true, false),
            (false, false, false),
        ] {
            let mut engine = engine_in(&index, prospect, votes);
            let mut liveness = ProspectLiveness::new(&index);
            assert_eq!(
                liveness.third_live(&mut engine, pea, tea, may),
                Ok(expected),
                "the prospect arm is what opens this window, so it opens exactly where the simulated prospect is scored"
            );
        }
    }

    /// The stage-4b arm, opening a window the prospect arm looked at and left shut: `qsTea` offers no exit at all, so nothing about the seat's prospect can move, and only its vote reads the third token.
    #[test]
    fn the_vote_arm_opens_a_slot_the_prospect_arm_leaves_shut() {
        let index = vote_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");

        for (prospect, votes, expected) in [
            (true, true, true),
            (true, false, false),
            (false, true, true),
            (false, false, false),
        ] {
            let mut engine = engine_in(&index, prospect, votes);
            let mut liveness = ProspectLiveness::new(&index);
            assert_eq!(
                liveness.third_live(&mut engine, pea, tea, may),
                Ok(expected),
                "the vote arm is the only channel this fixture's third token has"
            );
        }

        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);
        let r1tok = RightToken::Letter(tea);
        let r2tok = RightToken::Letter(may);
        assert_eq!(
            liveness.prospect_varies_third(&mut engine, pea, tea, may, r1tok, r2tok),
            Ok(false),
            "stage one's prospect arm sees nothing here"
        );
        assert_eq!(
            liveness.vote_varies_third(&mut engine, pea, tea, may, r1tok, r2tok),
            Ok(true),
            "and its vote arm is what fires"
        );
    }

    /// Stage one is `(simulated_prospect and prospect) or (vote_slots and vote)`, and the `or` short-circuits: where the prospect arm has already fired, the vote arm is never asked at all. Both orders reach the same verdict, so nothing about the answer says which ran — but a vote probe journals the pointers its records fire, so evaluating the arms the other way round would put provenance in the product that belongs in no build. The vote arm's own memo is what says it stayed unasked.
    #[test]
    fn a_fired_prospect_arm_leaves_the_vote_arm_unasked() {
        let index = prospect_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");
        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);

        assert_eq!(liveness.third_live(&mut engine, pea, tea, may), Ok(true));
        assert!(
            !liveness.prospect3.is_empty(),
            "the prospect arm is what fired here"
        );
        assert!(
            liveness.vote3.is_empty(),
            "and the vote arm was never reached, though this follower carries a prefer record it would have probed"
        );
        assert!(
            !liveness.vote_records(tea).is_empty(),
            "which is worth saying, because a follower with no votes would have answered empty either way"
        );
    }

    /// The recorded joint34 counterexample at fixture scale: the seat's own probes agree under every third token with an `EDGE` or `UNKNOWN` fourth, and the slot opens only because a fourth slot hanging off one concrete third is live.
    #[test]
    fn the_joint34_belt_opens_a_third_slot_whose_own_seat_probes_agree() {
        let index = belt_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");
        let it = fixtures::sym(&index, "qsIt");
        let r1tok = RightToken::Letter(tea);
        let r2tok = RightToken::Letter(may);
        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);

        assert_eq!(
            liveness.prospect_varies_third(&mut engine, pea, tea, may, r1tok, r2tok),
            Ok(false),
            "no third token moves the seat's simulated prospect at an EDGE or UNKNOWN fourth"
        );
        assert_eq!(
            liveness.vote_varies_third(&mut engine, pea, tea, may, r1tok, r2tok),
            Ok(false),
            "and no vote reads it either"
        );
        assert_eq!(
            liveness.seat_varies(&mut engine, pea, r1tok, r2tok, None),
            Ok(false),
            "the seat replay agrees at the third grain — which is the whole point, since a port without the belt would answer dead here"
        );
        assert_eq!(
            liveness.fourth_live(&mut engine, pea, tea, may, it),
            Ok(true),
            "but the fourth slot is live at the one third whose own cells need a letter past them"
        );
        assert_eq!(
            liveness.third_live(&mut engine, pea, tea, may),
            Ok(true),
            "so the belt opens the third slot the enumeration would otherwise never consult it through"
        );
        assert_eq!(
            liveness.seat3.get(&(pea, tea, may)),
            None,
            "stage one never fired at the third slot, so the seat replay was never asked — and a replay that never runs never fires the pointers it would journal"
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
            "one concrete third carries the live fourth, which is what the belt's any() is for"
        );
    }

    /// A left class the fixpoint can never reach raises in the replay and is skipped — counting it as a raise instead would open every window of a seat that enters at one height only.
    #[test]
    fn an_unreachable_left_class_is_skipped_rather_than_marked_live() {
        let index = belt_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let may = fixtures::sym(&index, "qsMay");
        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);
        let classes = liveness
            .seat_left_classes(&mut engine, pea)
            .expect("the fixture settles");

        let stranded: Vec<SettleErrorKind> = classes
            .iter()
            .filter_map(|left| {
                engine
                    .transition_trace(
                        left,
                        RightToken::Letter(pea),
                        Slots::new(RightToken::Letter(tea), RightToken::Letter(may), EDGE, EDGE),
                    )
                    .err()
                    .map(|error| error.kind())
            })
            .collect();
        assert!(
            stranded.contains(&SettleErrorKind::Stranded),
            "qsPea enters at the baseline alone, so the cap-committing and x-height-committing left classes strand"
        );
        assert!(
            stranded
                .iter()
                .all(|kind| *kind == SettleErrorKind::Stranded)
        );
        assert_eq!(
            liveness.seat_varies(
                &mut engine,
                pea,
                RightToken::Letter(tea),
                RightToken::Letter(may),
                None
            ),
            Ok(false),
            "the replay skips those classes rather than reading their raise as movement"
        );
    }

    /// The other side of the same split: a prefer conflict is a raise the enumeration must surface, so it marks the slot live instead of being skipped.
    #[test]
    fn a_raising_left_class_marks_the_slot_live() {
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
                    RightToken::Letter(pea),
                    Slots::new(RightToken::Letter(tea), RightToken::Letter(may), EDGE, EDGE),
                )
                .map(|trace| trace.settled)
                .map_err(|error| error.kind()),
            Err(SettleErrorKind::Ambiguous)
        );
        assert_eq!(
            liveness.seat_varies(
                &mut engine,
                pea,
                RightToken::Letter(tea),
                RightToken::Letter(may),
                None
            ),
            Ok(true),
            "the baseline window itself raises, and a raise is live rather than skipped"
        );
    }

    /// The prospect and vote arms key on the collapsed signature rather than on the input family, so two families the follower cannot tell apart share one verdict and run its probes once.
    #[test]
    fn two_families_sharing_a_signature_share_one_probe() {
        let index = twin_spec();
        let pea = fixtures::sym(&index, "qsPea");
        let tea = fixtures::sym(&index, "qsTea");
        let mut engine = engine_in(&index, true, true);
        let mut liveness = ProspectLiveness::new(&index);
        let r1tok = RightToken::Letter(tea);
        let r2tok = RightToken::Letter(pea);

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
            "the twins' input frames collapse to one signature, so the second family reads the first's verdict"
        );
        assert_eq!(
            liveness.seat3.len(),
            0,
            "and the seat replay is keyed on the family itself, which nothing above has asked for yet"
        );
    }

    /// The two modules under their real caller: a whole configuration enumerated at class grain expands, member set by member set, to the same window rows the label-grain arm emits — which is the `--deep-classes-off` comparison the exit bar names, and the enumeration-side partition assertion runs over both products on the way out.
    #[test]
    fn a_class_grain_enumeration_expands_to_the_label_grain_one() {
        for index in [prospect_spec(), vote_spec()] {
            let grains: Vec<Vec<String>> = [true, false]
                .into_iter()
                .map(|deep_classes| {
                    let product = enumerate_transitions(
                        &index,
                        &[],
                        EnumerationModes {
                            simulated_prospect: true,
                            vote_slots: true,
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

    /// One product's window rows with every deep-class token replaced by its members, which is `DecisionTable.expanded_transitions` over the two slots a class id can reach.
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
            for third in &members(&row.right3) {
                for fourth in &members(&row.right4) {
                    out.push(format!(
                        "{}\t{}\t{}\t{}\t{third}\t{fourth}\t{}",
                        row.input_glyph, row.left, row.right1, row.right2, row.outcome
                    ));
                }
            }
        }
        out.sort();
        out
    }
}
