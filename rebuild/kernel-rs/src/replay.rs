//! The string replay behind the `replay-strings` verb: every text of the sweep universe walked window by window, the folded rules applied first-match with the settled left fed forward, and each window's rule outcome held to this engine's own settlement of it. It is `conform._SettledWindowWalk` and `conform._first_matching_rule` transcribed over the persisted product instead of the compiled font, and what it answers is the one data-dependent fact the enumeration can still get wrong after read-back and the fold's partition assertion have done their work: completeness. A live raw window the fixpoint left at `#NA` or never reached is one the font answers with a wildcard or a default rule, and this walk is where that answer meets the engine's.
//!
//! Three things hold the walk to the belt's own reading of a window, `conform._window_rights`. The slots a window is keyed on are the raw labels of the formed token stream out to the fourth, `#EDGE` past the end and `#NA` from the first slot after a boundary on, because no record peeks past a boundary; the engine is handed the raw tokens themselves, edge-padded, exactly as `_SettledWindowWalk._rights` hands them across the seam; and the left slot is the settled cell's label, which the fixpoint's own partition premise holds injective over settled lefts. Ligatures form before anything else, greedy and longest-first over the modeled sequences, each match yielding to the section 5.7 guard over the two raw tokens past it — `settle.form_ligatures` restated over [`GuardState`], so the token stream the walk settles is the one the emitted formation lookup produces.
//!
//! The memo is a speed device and nothing else, as the belt's is: a window key answers once per configuration and every recurrence across the universe is a hash probe, and the verdict is the same whether every window misses or every window hits. What makes the universe affordable here rather than in Python is that a miss costs one engine call in the same process instead of a batched round trip and no shaper runs beside it; the walk is still priced in distinct raw windows, which grow as the alphabet to the horizon, so the per-build depth is the belt's own (`run_m1.REPLAY_HORIZON`) and a deeper walk is the periodic sweep's. Under the locality theorem `doc/rebuild-design.md` §10 states, a walk restricted to the texts naming an edited family covers every window whose answer or reachability that edit could have moved, which is the O(delta) form a rune edit takes.

use std::collections::HashMap;
use std::rc::Rc;

use crate::engine::{Engine, EngineModes, Slots};
use crate::fixpoint::{EDGE_LABEL, locked_glyph_name, right_token_label};
use crate::fold::{NA_LABEL, Rule};
use crate::guard::GuardState;
use crate::index::SpecIndex;
use crate::model::Sym;
use crate::types::{EDGE, LeftContext, RightToken, Settled, SettledPool, TokenKind, cell_label};

/// What one configuration's walk answered: how many texts it walked, how many distinct windows it settled and checked, and how many texts the family filter left out.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Report {
    pub texts: u64,
    pub windows: u64,
    pub skipped: u64,
}

/// How many disagreements a walk names before it stops: enough to see a shape, few enough that the complaint stays one screen.
const NAMED_DISAGREEMENTS: usize = 5;

/// The texts one walk covers: every text of length 1 through `horizon` over the alphabet, narrowed to the texts naming one of `families` when a set is given — the O(delta) form a rune edit takes under the locality theorem — and the whole universe when none is.
#[derive(Clone, Copy, Debug)]
pub struct Universe<'a> {
    pub horizon: usize,
    pub families: Option<&'a [Sym]>,
}

impl<'a> Universe<'a> {
    /// The whole universe to `horizon`.
    pub fn whole(horizon: usize) -> Self {
        Self {
            horizon,
            families: None,
        }
    }

    /// The texts naming one of `families`, to `horizon`.
    pub fn naming(horizon: usize, families: &'a [Sym]) -> Self {
        Self {
            horizon,
            families: Some(families),
        }
    }
}

/// The sweep universe's alphabet, `conform.spec_alphabet`: every modeled letter with a code point and every registered boundary token, in code point order, which is the order the universe is walked in and therefore the order a disagreement is found in. A rune's code point is its own record's, or the registry family's where the record leaves it unspelled — the two agree wherever both are spelled, since `spec_load` refuses a rune whose code point disagrees with its family's.
pub fn alphabet(index: &SpecIndex) -> Result<Vec<RightToken>, String> {
    let mut seated: Vec<(i64, RightToken)> = Vec::new();
    for (name, _) in index.runes() {
        if let Some(codepoint) = codepoint_of(index, *name) {
            seated.push((codepoint, RightToken::Letter(*name)));
        }
    }
    for (name, token) in &index.registry().boundary_tokens {
        let kind = TokenKind::from_text(index.resolve(*name))
            .filter(|kind| kind.is_boundary() && *kind != TokenKind::Edge)
            .ok_or_else(|| {
                format!(
                    "{} is a boundary token the walk has no kind for",
                    index.resolve(*name)
                )
            })?;
        seated.push((
            token.codepoint,
            RightToken::of_kind(kind).expect("a boundary kind has a token"),
        ));
    }
    seated.sort_by_key(|(codepoint, _)| *codepoint);
    Ok(seated.into_iter().map(|(_, token)| token).collect())
}

/// One modeled rune's code point: its own record's, else its registry family's.
fn codepoint_of(index: &SpecIndex, name: Sym) -> Option<i64> {
    let rune = index.rune(name)?;
    rune.codepoint.or_else(|| {
        index
            .registry()
            .families
            .iter()
            .find(|(family, _)| *family == name)
            .and_then(|(_, info)| info.codepoint)
    })
}

/// Which alphabet seats a text has to carry to name one of `families`: a letter's own seat, and for a ligature the seats of its components, since a ligature is named by any text carrying its sequence and a text carrying a component is the superset that is cheap to test. A family with neither a code point nor a sequence is named by no text at all.
fn wanted_seats(index: &SpecIndex, alphabet: &[RightToken], families: &[Sym]) -> Vec<bool> {
    let mut wanted = vec![false; alphabet.len()];
    let mut mark = |rune: Sym| {
        if let Some(seat) = alphabet
            .iter()
            .position(|token| *token == RightToken::Letter(rune))
        {
            wanted[seat] = true;
        }
    };
    for family in families {
        let Some(rune) = index.rune(*family) else {
            continue;
        };
        if codepoint_of(index, *family).is_some() {
            mark(*family);
        }
        if let Some(sequence) = &rune.sequence {
            for part in sequence {
                mark(*part);
            }
        }
    }
    wanted
}

/// `settle.form_ligatures`' order: the modeled ligature sequences longest first, ties in declaration order, grouped by the rune a sequence opens on so a position reads only the sequences its own rune can open.
struct Formation<'i> {
    by_lead: HashMap<Sym, Vec<(Vec<Sym>, Sym)>>,
    guard: GuardState<'i>,
}

impl<'i> Formation<'i> {
    fn new(index: &'i SpecIndex) -> Self {
        let mut sequences: Vec<(Vec<Sym>, Sym)> = index
            .runes()
            .iter()
            .filter_map(|(name, rune)| {
                rune.sequence
                    .as_ref()
                    .map(|sequence| (sequence.clone(), *name))
            })
            .collect();
        sequences.sort_by_key(|(sequence, _)| std::cmp::Reverse(sequence.len()));
        let mut by_lead: HashMap<Sym, Vec<(Vec<Sym>, Sym)>> = HashMap::new();
        for (sequence, name) in sequences {
            by_lead
                .entry(sequence[0])
                .or_default()
                .push((sequence, name));
        }
        Self {
            by_lead,
            guard: GuardState::new(index),
        }
    }

    /// Type-4 formation over one raw token run, greedy left to right and longest sequence first, each match yielding to the guard over the two raw tokens past it.
    fn form(&mut self, tokens: &[RightToken], formed: &mut Vec<RightToken>) -> Result<(), String> {
        formed.clear();
        let mut at = 0;
        while at < tokens.len() {
            let mut matched: Option<(Sym, usize)> = None;
            if let RightToken::Letter(lead) = tokens[at]
                && let Some(candidates) = self.by_lead.get(&lead)
            {
                for (sequence, name) in candidates {
                    let end = at + sequence.len();
                    if end > tokens.len()
                        || !sequence
                            .iter()
                            .zip(&tokens[at..end])
                            .all(|(part, token)| *token == RightToken::Letter(*part))
                    {
                        continue;
                    }
                    let right1 = tokens.get(end).copied().unwrap_or(EDGE);
                    let right2 = tokens.get(end + 1).copied().unwrap_or(EDGE);
                    if self
                        .guard
                        .formation_blocked(*name, right1, right2)
                        .map_err(|error| error.to_string())?
                    {
                        continue;
                    }
                    matched = Some((*name, sequence.len()));
                    break;
                }
            }
            match matched {
                Some((name, width)) => {
                    formed.push(RightToken::Letter(name));
                    at += width;
                }
                None => {
                    formed.push(tokens[at]);
                    at += 1;
                }
            }
        }
        Ok(())
    }
}

/// The label pool one walk keys its windows and rules through: every spelling once, its id the key's word for it, and whether that spelling is one of the five that end a window's reach.
struct Labels {
    ids: HashMap<Rc<str>, u32>,
    texts: Vec<Rc<str>>,
    boundaryish: Vec<bool>,
    edge: u32,
    na: u32,
}

impl Labels {
    fn new() -> Self {
        let mut labels = Self {
            ids: HashMap::new(),
            texts: Vec::new(),
            boundaryish: Vec::new(),
            edge: 0,
            na: 0,
        };
        labels.edge = labels.intern(EDGE_LABEL);
        labels.na = labels.intern(NA_LABEL);
        for boundary in ["space", "uni200C", "periodcentered"] {
            labels.intern(boundary);
        }
        for seat in 0..labels.texts.len() {
            labels.boundaryish[seat] = true;
        }
        labels
    }

    fn intern(&mut self, text: &str) -> u32 {
        if let Some(&id) = self.ids.get(text) {
            return id;
        }
        let id = u32::try_from(self.texts.len())
            .expect("a walk spells far fewer than four billion labels");
        let shared: Rc<str> = Rc::from(text);
        self.ids.insert(Rc::clone(&shared), id);
        self.texts.push(shared);
        self.boundaryish.push(false);
        id
    }

    fn text(&self, id: u32) -> &str {
        &self.texts[id as usize]
    }

    /// `_window_rights`' cascade past `slot`: `#NA` the moment the slot before it was a boundary, the edge, or itself `#NA`.
    fn stops_reach(&self, id: u32) -> bool {
        self.boundaryish[id as usize]
    }
}

/// One rule as the walk matches it: the five constrained slots as sorted id lists, `None` for an unconstrained one, and the outcome's id.
struct IndexedRule {
    slots: [Option<Vec<u32>>; 5],
    outcome: u32,
}

impl IndexedRule {
    fn matches(&self, window: [u32; 5]) -> bool {
        self.slots.iter().zip(window).all(|(slot, label)| {
            slot.as_ref()
                .is_none_or(|members| members.binary_search(&label).is_ok())
        })
    }
}

/// The rules of one configuration keyed by input label, in emission order under each input — the whole of what first-match-wins reads.
struct RuleIndex {
    by_input: HashMap<u32, Vec<IndexedRule>>,
}

impl RuleIndex {
    fn new(labels: &mut Labels, rules: &[Rule]) -> Self {
        let mut by_input: HashMap<u32, Vec<IndexedRule>> = HashMap::new();
        for rule in rules {
            let input = labels.intern(&rule.input_glyph);
            let mut slot = |members: &Option<Vec<Rc<str>>>| {
                members.as_ref().map(|members| {
                    let mut ids: Vec<u32> =
                        members.iter().map(|member| labels.intern(member)).collect();
                    ids.sort_unstable();
                    ids.dedup();
                    ids
                })
            };
            let slots = [
                slot(&rule.backtrack),
                slot(&rule.look1),
                slot(&rule.look2),
                slot(&rule.look3),
                slot(&rule.look4),
            ];
            let outcome = labels.intern(&rule.outcome);
            by_input
                .entry(input)
                .or_default()
                .push(IndexedRule { slots, outcome });
        }
        Self { by_input }
    }

    /// The outcome first-match-wins predicts for one window, or the input label itself where no rule matches — the glyph the font leaves untouched.
    fn predict(&self, input: u32, window: [u32; 5]) -> u32 {
        self.by_input
            .get(&input)
            .and_then(|rules| rules.iter().find(|rule| rule.matches(window)))
            .map_or(input, |rule| rule.outcome)
    }
}

/// One memoized window: the input rune, the settled left's label, and the four right labels after the cascade.
type WindowKey = (Sym, u32, [u32; 4]);

/// What a memoized window answers: the seat of the settled record and the label the next window's left slot reads.
#[derive(Clone, Copy)]
struct Outcome {
    seat: crate::types::SettledSeat,
    label: u32,
}

/// One configuration's walk over the universe, or the disagreements it found spelled as the complaint the verb exits with.
pub struct Replay<'i> {
    index: &'i SpecIndex,
    engine: Engine<'i>,
    formation: Formation<'i>,
    labels: Labels,
    rules: RuleIndex,
    memo: HashMap<WindowKey, Outcome>,
    pool: SettledPool,
    seat_labels: Vec<u32>,
    input_labels: HashMap<Sym, u32>,
    locked_labels: HashMap<Sym, u32>,
    disagreements: Vec<String>,
}

impl<'i> Replay<'i> {
    /// A walk over `rules` in the world `modes` names, for the features one configuration resolved to. The engine keeps its trace memo, since the universe re-reaches windows in the millions and a hit replays its journaled delta so warm and cold owe the same answer.
    pub fn new(
        index: &'i SpecIndex,
        features: Vec<Sym>,
        modes: EngineModes,
        rules: &[Rule],
    ) -> Self {
        let mut labels = Labels::new();
        let rules = RuleIndex::new(&mut labels, rules);
        Self {
            index,
            engine: Engine::with_modes(
                index,
                features,
                EngineModes {
                    trace_memo: true,
                    ..modes
                },
            ),
            formation: Formation::new(index),
            labels,
            rules,
            memo: HashMap::new(),
            pool: SettledPool::default(),
            seat_labels: Vec::new(),
            input_labels: HashMap::new(),
            locked_labels: HashMap::new(),
            disagreements: Vec::new(),
        }
    }

    /// Every text of `universe` walked and checked. A disagreement between the rules and the engine is the error, naming the texts it was found in; a window the engine refuses is one too, since the belt raises on it as well.
    pub fn walk_universe(&mut self, universe: Universe<'_>) -> Result<Report, String> {
        let alphabet = alphabet(self.index)?;
        if alphabet.is_empty() {
            return Err("the spec models no letter with a code point and no boundary token, so there is nothing to walk".to_owned());
        }
        let wanted = universe
            .families
            .map(|families| wanted_seats(self.index, &alphabet, families));
        let mut report = Report::default();
        let mut seats: Vec<usize> = Vec::new();
        let mut raw: Vec<RightToken> = Vec::new();
        let mut formed: Vec<RightToken> = Vec::new();
        for length in 1..=universe.horizon {
            seats.clear();
            seats.resize(length, 0);
            loop {
                let named = wanted
                    .as_ref()
                    .is_none_or(|wanted| seats.iter().any(|seat| wanted[*seat]));
                if named {
                    raw.clear();
                    raw.extend(seats.iter().map(|seat| alphabet[*seat]));
                    self.walk_text(&raw, &mut formed, &mut report)?;
                    if self.disagreements.len() >= NAMED_DISAGREEMENTS {
                        return Err(self.complaint());
                    }
                } else {
                    report.skipped += 1;
                }
                let mut advanced = false;
                for slot in (0..length).rev() {
                    seats[slot] += 1;
                    if seats[slot] < alphabet.len() {
                        advanced = true;
                        break;
                    }
                    seats[slot] = 0;
                }
                if !advanced {
                    break;
                }
            }
        }
        if self.disagreements.is_empty() {
            Ok(report)
        } else {
            Err(self.complaint())
        }
    }

    /// One text: formed, then settled left to right through the memo, every miss checked against the rules as it is answered.
    pub fn walk_text(
        &mut self,
        raw: &[RightToken],
        formed: &mut Vec<RightToken>,
        report: &mut Report,
    ) -> Result<(), String> {
        self.formation.form(raw, formed)?;
        report.texts += 1;
        let mut labels: Vec<u32> = Vec::with_capacity(formed.len());
        for token in formed.iter() {
            labels.push(self.token_label(*token));
        }
        let mut previous: Option<Outcome> = None;
        for (at, token) in formed.iter().enumerate() {
            let RightToken::Letter(rune) = *token else {
                previous = None;
                continue;
            };
            let left_label = match at {
                0 => self.labels.edge,
                _ => match previous {
                    Some(outcome) => outcome.label,
                    None => labels[at - 1],
                },
            };
            let rights = self.window_rights(&labels, at);
            let key = (rune, left_label, rights);
            let outcome = match self.memo.get(&key) {
                Some(outcome) => *outcome,
                None => {
                    let left = match at {
                        0 => LeftContext::boundary(TokenKind::Edge),
                        _ => match previous {
                            Some(outcome) => {
                                LeftContext::letter(self.pool.get(outcome.seat).clone())
                            }
                            None => LeftContext::boundary(formed[at - 1].kind()),
                        },
                    };
                    let slots = Slots::new(
                        formed.get(at + 1).copied().unwrap_or(EDGE),
                        formed.get(at + 2).copied().unwrap_or(EDGE),
                        formed.get(at + 3).copied().unwrap_or(EDGE),
                        formed.get(at + 4).copied().unwrap_or(EDGE),
                    );
                    let settled = self
                        .engine
                        .with_settled(&left, *token, slots, Settled::clone)
                        .map_err(|error| {
                            format!(
                                "the engine refused the window {} reached at position {at} of {}: {error}",
                                self.spell_window(rune, left_label, rights),
                                spell_text(self.index, raw)
                            )
                        })?;
                    let outcome = self.seat(&settled);
                    report.windows += 1;
                    let input = if at > 0 && formed[at - 1] == RightToken::Zwnj {
                        self.locked_label(rune, labels[at])
                    } else {
                        labels[at]
                    };
                    let predicted = self.rules.predict(
                        input,
                        [left_label, rights[0], rights[1], rights[2], rights[3]],
                    );
                    if predicted != outcome.label {
                        self.disagreements.push(format!(
                            "{} at position {at} of {}: settlement says {}, rules say {}",
                            self.spell_window(rune, left_label, rights),
                            spell_text(self.index, raw),
                            self.labels.text(outcome.label),
                            self.labels.text(predicted)
                        ));
                    }
                    self.memo.insert(key, outcome);
                    outcome
                }
            };
            previous = Some(outcome);
        }
        Ok(())
    }

    fn token_label(&mut self, token: RightToken) -> u32 {
        if let RightToken::Letter(rune) = token
            && let Some(&label) = self.input_labels.get(&rune)
        {
            return label;
        }
        let label = self.labels.intern(&right_token_label(self.index, token));
        if let RightToken::Letter(rune) = token {
            self.input_labels.insert(rune, label);
        }
        label
    }

    /// The label an input carries immediately after a ZWNJ: the chokepoint twin's for an entry-bearing rune, whose rows the enumeration keys under that label (`fixpoint`'s locked input), and its raw label for a rune the chokepoint never locks. This is `conform.formed_labels`' `.noentry` rename, applied to the input slot alone — the `#NA` cascade keeps a post-ZWNJ letter out of every right slot.
    fn locked_label(&mut self, rune: Sym, raw: u32) -> u32 {
        if !self.index.is_entry_bearing(rune) {
            return raw;
        }
        if let Some(&label) = self.locked_labels.get(&rune) {
            return label;
        }
        let label = self
            .labels
            .intern(&locked_glyph_name(self.index.resolve(rune)));
        self.locked_labels.insert(rune, label);
        label
    }

    /// `conform._window_rights`: the four raw slots past `at`, `#EDGE` past the end of the stream and `#NA` from the first slot after a boundary on.
    fn window_rights(&self, labels: &[u32], at: usize) -> [u32; 4] {
        let mut rights = [self.labels.na; 4];
        let mut reach = true;
        for (slot, right) in rights.iter_mut().enumerate() {
            if !reach {
                break;
            }
            let label = labels
                .get(at + 1 + slot)
                .copied()
                .unwrap_or(self.labels.edge);
            *right = label;
            reach = !self.labels.stops_reach(label);
        }
        rights
    }

    /// The seat and left label one settled record answers with, the label minted on the record's first seating.
    fn seat(&mut self, settled: &Settled) -> Outcome {
        let seat = self.pool.seat(settled);
        if seat.index() == self.seat_labels.len() {
            let label = self.labels.intern(&cell_label(self.index, &settled.cell));
            self.seat_labels.push(label);
        }
        Outcome {
            seat,
            label: self.seat_labels[seat.index()],
        }
    }

    fn spell_window(&self, rune: Sym, left: u32, rights: [u32; 4]) -> String {
        format!(
            "({}, {}, {}, {}, {}, {})",
            self.index.resolve(rune),
            self.labels.text(left),
            self.labels.text(rights[0]),
            self.labels.text(rights[1]),
            self.labels.text(rights[2]),
            self.labels.text(rights[3])
        )
    }

    fn complaint(&self) -> String {
        format!(
            "{} first-match-wins replay disagreement(s) over the swept texts: {}",
            self.disagreements.len(),
            self.disagreements.join("; ")
        )
    }
}

/// One raw text as a complaint names it: its tokens' spellings joined by spaces, a letter by its rune name and a boundary by its kind.
fn spell_text(index: &SpecIndex, raw: &[RightToken]) -> String {
    let words: Vec<String> = raw
        .iter()
        .map(|token| match token {
            RightToken::Letter(rune) => index.resolve(*rune).to_owned(),
            other => other.kind().as_str().to_owned(),
        })
        .collect();
    words.join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fixpoint::{EnumerationModes, enumerate_transitions};
    use crate::fold::fold_product;
    use crate::index::fixtures;

    fn folded_rules(index: &SpecIndex) -> Vec<Rule> {
        let product = enumerate_transitions(index, &[], EnumerationModes::default())
            .expect("the fixture's fixpoint closes");
        fold_product(index, product)
            .expect("and folds")
            .decision
            .rules
    }

    fn replay<'i>(index: &'i SpecIndex, rules: &[Rule]) -> Replay<'i> {
        Replay::new(index, Vec::new(), EngineModes::default(), rules)
    }

    /// The alphabet is the modeled letters and the boundary tokens in code point order, which for the fixture puts the space first, the ZWNJ next and the four letters after them.
    #[test]
    fn the_alphabet_is_every_letter_and_boundary_in_code_point_order() {
        let index = fixtures::mini();
        let tokens = alphabet(&index).expect("the fixture has an alphabet");
        assert_eq!(tokens.len(), 6);
        assert_eq!(tokens[0], RightToken::Space);
        assert_eq!(tokens[1], RightToken::Zwnj);
        assert_eq!(
            tokens[2],
            RightToken::Letter(fixtures::sym(&index, "qsPea"))
        );
        assert_eq!(tokens[5], RightToken::Letter(fixtures::sym(&index, "qsIt")));
    }

    /// The fixture's own table agrees with its engine over every string to the belt's horizon and one past it, which is the green the build states on every pass.
    #[test]
    fn the_fixtures_table_agrees_with_its_engine_over_the_universe() {
        let index = fixtures::mini();
        let rules = folded_rules(&index);
        let mut walk = replay(&index, &rules);
        let report = walk
            .walk_universe(Universe::whole(5))
            .expect("the table is complete");
        assert_eq!(report.texts, 6 + 36 + 216 + 1296 + 7776);
        assert_eq!(report.skipped, 0);
        assert!(report.windows > 0);
    }

    /// A rule whose outcome disagrees with settlement is found, and the complaint names the text and the window it was found in. The first rule is the one perturbed because every table's first rule wins some window; the outcome is renamed to a spelling no cell carries so the disagreement cannot be masked by a tie.
    #[test]
    fn a_perturbed_rule_is_caught_and_the_offending_text_named() {
        let index = fixtures::mini();
        let mut rules = folded_rules(&index);
        let input = Rc::clone(&rules[0].input_glyph);
        rules[0].outcome = Rc::from(format!("{input}.perturbed").as_str());
        let mut walk = replay(&index, &rules);
        let complaint = walk
            .walk_universe(Universe::whole(4))
            .expect_err("the perturbed rule disagrees");
        assert!(complaint.contains("replay disagreement"), "{complaint}");
        assert!(complaint.contains(".perturbed"), "{complaint}");
        assert!(complaint.contains("at position"), "{complaint}");
    }

    /// The family filter walks exactly the texts naming the family, and a walk so narrowed still finds a disagreement that lives in those texts.
    #[test]
    fn a_family_filter_walks_only_the_texts_naming_it() {
        let index = fixtures::mini();
        let rules = folded_rules(&index);
        let pea = fixtures::sym(&index, "qsPea");
        let mut walk = replay(&index, &rules);
        let report = walk
            .walk_universe(Universe::naming(3, &[pea]))
            .expect("the table is complete");
        let all = 6 + 36 + 216;
        let without_pea = 5 + 25 + 125;
        assert_eq!(report.texts, all - without_pea);
        assert_eq!(report.skipped, without_pea);

        let mut perturbed = folded_rules(&index);
        let seat = perturbed
            .iter()
            .position(|rule| &*rule.input_glyph == "qsPea")
            .expect("qsPea has a rule");
        perturbed[seat].outcome = Rc::from("qsPea.perturbed");
        let mut narrowed = replay(&index, &perturbed);
        let complaint = narrowed
            .walk_universe(Universe::naming(3, &[pea]))
            .expect_err("the disagreement lives in a text naming qsPea");
        assert!(complaint.contains("qsPea.perturbed"), "{complaint}");
        let it = fixtures::sym(&index, "qsIt");
        let mut elsewhere = replay(&index, &perturbed);
        let _ = elsewhere.walk_universe(Universe::naming(3, &[it]));
    }

    /// A ligature is named by its components' seats, so a walk narrowed to the ligature still reaches every text that could form it — and the ligature itself, carrying no code point, takes no seat of its own.
    #[test]
    fn a_ligature_family_is_named_through_its_components() {
        let baseline = fixtures::map(&[("baseline", &fixtures::row("baseline", &[]))]);
        let joining = fixtures::stance(
            "half",
            &[(
                "surface",
                &fixtures::surface(&[("entries", &baseline), ("exits", &baseline)]),
            )],
        );
        let runes = fixtures::map(&[
            (
                "qsPea",
                &fixtures::rune(
                    "qsPea",
                    &[("stances", &fixtures::map(&[("half", &joining)]))],
                ),
            ),
            (
                "qsTea",
                &fixtures::rune(
                    "qsTea",
                    &[("stances", &fixtures::map(&[("half", &joining)]))],
                ),
            ),
            (
                "qsPea_qsTea",
                &fixtures::rune(
                    "qsPea_qsTea",
                    &[
                        ("sequence", &fixtures::names(&["qsPea", "qsTea"])),
                        ("stances", &fixtures::map(&[("half", &joining)])),
                    ],
                ),
            ),
        ]);
        let index = fixtures::index_of(&fixtures::dump(
            &runes,
            &fixtures::ligature_family_registry(),
        ));
        let tokens = alphabet(&index).expect("an alphabet");
        assert_eq!(
            tokens.len(),
            4,
            "two boundaries and two letters; the ligature has no seat"
        );
        let liga = fixtures::sym(&index, "qsPea_qsTea");
        let wanted = wanted_seats(&index, &tokens, &[liga]);
        let pea = tokens
            .iter()
            .position(|token| *token == RightToken::Letter(fixtures::sym(&index, "qsPea")))
            .expect("qsPea is in the alphabet");
        let tea = tokens
            .iter()
            .position(|token| *token == RightToken::Letter(fixtures::sym(&index, "qsTea")))
            .expect("qsTea is in the alphabet");
        assert!(wanted[pea] && wanted[tea]);
        assert_eq!(wanted.iter().filter(|seat| **seat).count(), 2);
    }

    /// `_window_rights`' cascade: a boundary at the first slot blanks every deeper one, the edge does the same, and a letter run reads all four.
    #[test]
    fn the_right_slots_stop_reaching_past_a_boundary_or_the_edge() {
        let index = fixtures::mini();
        let rules = folded_rules(&index);
        let mut walk = replay(&index, &rules);
        let pea = walk.token_label(RightToken::Letter(fixtures::sym(&index, "qsPea")));
        let space = walk.token_label(RightToken::Space);
        let na = walk.labels.na;
        let edge = walk.labels.edge;
        assert_eq!(
            walk.window_rights(&[pea, space, pea, pea, pea], 0),
            [space, na, na, na]
        );
        assert_eq!(walk.window_rights(&[pea, pea], 0), [pea, edge, na, na]);
        assert_eq!(
            walk.window_rights(&[pea, pea, pea, pea, pea, pea], 0),
            [pea, pea, pea, pea]
        );
        assert_eq!(walk.window_rights(&[pea], 0), [edge, na, na, na]);
    }
}
