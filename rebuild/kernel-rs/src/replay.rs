//! The string replay behind the `replay-strings` verb: every text of the sweep universe walked window by window, the folded rules applied first-match with the settled left fed forward, and each window's rule outcome held to this engine's own settlement of it. It is `conform._SettledWindowWalk` and `conform._first_matching_rule` transcribed over the persisted product instead of the compiled font, and what it answers is the one data-dependent fact the enumeration can still get wrong after read-back and the fold's partition assertion have done their work: completeness. A live raw window the fixpoint left at `#NA` or never reached is one the font answers with a wildcard or a default rule, and this walk is where that answer meets the engine's.
//!
//! Three things hold the walk to the belt's own reading of a window, `conform._window_rights`. The slots a window is keyed on are the raw labels of the formed token stream out to the fourth, `#EDGE` past the end and `#NA` from the first slot after a boundary on, because no record peeks past a boundary; the engine is handed the raw tokens themselves, edge-padded, exactly as `_SettledWindowWalk._rights` hands them across the seam; and the left slot is the settled cell's label, which the fixpoint's own partition premise holds injective over settled lefts. Ligatures form before anything else, greedy and longest-first over the modeled sequences, each match yielding to the section 5.7 guard over the two raw tokens past it — `settle.form_ligatures` restated over [`GuardState`], so the token stream the walk settles is the one the emitted formation lookup produces.
//!
//! The memo is also the build's settle memo. `write_window_memo` files it per configuration once the walk is green, one row per distinct window keyed on the input rune, the settled left and the four raw rights after the cascade, with every distinct settled record beside it — the same partition `conform._SettledWindowWalk` keys on, spelled in this crate's own vocabulary: the input as `right_token_label` or, after a ZWNJ, the chokepoint's locked name; the rights as raw labels; the left as a boundary label where the reach stops and otherwise as a seat into the record table, since `cell_label` and `geometry.display_name` are two spellings of one function of the cell and the Python side respells a seat through its own. The marker fold is the Python side's too (`conform.absorb_replay_memo`, over `model.raw_rename_map`): this crate never learns a configuration's marker names, and the seam's contract is that the file names raw labels and seats and nothing renamed. The file is written uncompressed, as every artifact this crate writes is, and is consumed and deleted by `run_m1.run_replay_strings` in the same phase.
//!
//! As a speed device for the walk itself the memo is what the belt's is: a window key answers once per configuration and every recurrence across the universe is a hash probe, and the verdict is the same whether every window misses or every window hits. What makes the universe affordable here rather than in Python is that a miss costs one engine call in the same process instead of a batched round trip and no shaper runs beside it; the walk is still priced in distinct raw windows, which grow as the alphabet to the horizon, so the per-build depth is the belt's own (`run_m1.REPLAY_HORIZON`) and a deeper walk is the periodic sweep's. Under the locality theorem `doc/rebuild-design.md` §10 states, a walk restricted to the texts naming an edited family covers every window whose answer or reachability that edit could have moved, which is the O(delta) form a rune edit takes.

use std::io::Write as _;
use std::path::Path;
use std::rc::Rc;

use crate::emit::json_string;
use crate::engine::{Engine, EngineModes, Slots};
use crate::fixpoint::{EDGE_LABEL, locked_glyph_name, right_token_label};
use crate::fold::{NA_LABEL, Rule};
use crate::guard::GuardState;
use crate::hash::HashMap;
use crate::index::SpecIndex;
use crate::model::Sym;
use crate::types::{
    EDGE, LeftContext, RightToken, Settled, SettledPool, SettledSeat, TokenKind, cell_label,
    settled_json,
};

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
        let mut by_lead: HashMap<Sym, Vec<(Vec<Sym>, Sym)>> = HashMap::default();
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

/// The label pool one walk keys its windows and rules through: every spelling once, its id the key's word for it, and whether that spelling is one of the five that end a window's reach. The shipped-order walk (`shipped_order.rs`) keys through the same pool.
pub(crate) struct Labels {
    ids: HashMap<Rc<str>, u32>,
    texts: Vec<Rc<str>>,
    boundaryish: Vec<bool>,
    edge: u32,
    na: u32,
}

impl Labels {
    pub(crate) fn new() -> Self {
        let mut labels = Self {
            ids: HashMap::default(),
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

    pub(crate) fn intern(&mut self, text: &str) -> u32 {
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

    pub(crate) fn text(&self, id: u32) -> &str {
        &self.texts[id as usize]
    }

    fn id_of(&self, text: &str) -> Option<u32> {
        self.ids.get(text).copied()
    }

    fn len(&self) -> usize {
        self.texts.len()
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
        let mut by_input: HashMap<u32, Vec<IndexedRule>> = HashMap::default();
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

/// The head token of the window memo file `write_window_memo` writes; `kernel_exec.REPLAY_MEMO_FORMAT` is its Python spelling.
pub const MEMO_FORMAT: &str = "ams-m1-replay-memo/1";

/// How many rows the emitter encodes between writes: a block's bytes are the only thing held beyond the memo itself.
const MEMO_BLOCK_ROWS: usize = 1 << 14;

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
            memo: HashMap::default(),
            pool: SettledPool::default(),
            seat_labels: Vec::new(),
            input_labels: HashMap::default(),
            locked_labels: HashMap::default(),
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

    /// The memo filed at `path` for the Python side to absorb: a `# ams-m1-replay-memo/1<tab><head json>` line naming the configuration, the horizon, the row count, the label and record counts and the column width; then the label table, one referenced spelling per line in id order; then one `settled_json` line per seated record in seat order; then the rows, seven little-endian integers each, `u16` where every index fits and `u32` otherwise. A row is the input label, the left, the four rights and the record index. The input is the rune's raw label, or its locked name where the left is the ZWNJ and the chokepoint locks the rune — the `#NA` cascade keeps a post-ZWNJ letter out of every right slot, so the left alone decides it. The left is a label index where its spelling stops the reach (the edge and the three boundaries) and otherwise the record table's seat offset past the label count, so the two kinds share one column and the reader tells them apart by the count in the head. Two seats sharing one `cell_label` collapse to the first, which is the collapse the walk's own key made. Nothing but the block in flight is held beyond the memo.
    pub fn write_window_memo(
        &mut self,
        path: &Path,
        config: &str,
        horizon: usize,
    ) -> Result<(), String> {
        let zwnj = self
            .labels
            .id_of("uni200C")
            .expect("the label pool spells the ZWNJ from birth");
        let mut inputs: HashMap<Sym, (u32, u32)> = HashMap::default();
        let walked: Vec<(Sym, u32)> = self
            .input_labels
            .iter()
            .map(|(rune, label)| (*rune, *label))
            .collect();
        for (rune, raw) in walked {
            let locked = self.locked_label(rune, raw);
            inputs.insert(rune, (raw, locked));
        }
        let mut seat_of_label: HashMap<u32, usize> = HashMap::default();
        for (seat, label) in self.seat_labels.iter().enumerate() {
            seat_of_label.entry(*label).or_insert(seat);
        }
        let input_of = |rune: Sym, left: u32| -> Result<u32, String> {
            let (raw, locked) = inputs.get(&rune).copied().ok_or_else(|| {
                format!("{} was settled but never labeled", self.index.resolve(rune))
            })?;
            Ok(if left == zwnj { locked } else { raw })
        };
        let mut referenced = vec![false; self.labels.len()];
        for (rune, left, rights) in self.memo.keys() {
            referenced[input_of(*rune, *left)? as usize] = true;
            if self.labels.stops_reach(*left) {
                referenced[*left as usize] = true;
            }
            for right in rights {
                referenced[*right as usize] = true;
            }
        }
        let mut compact: Vec<u32> = vec![u32::MAX; self.labels.len()];
        let mut table: Vec<u32> = Vec::new();
        for (id, wanted) in referenced.iter().enumerate() {
            if *wanted {
                compact[id] = u32::try_from(table.len()).expect("fewer labels than ids");
                table.push(u32::try_from(id).expect("an id is a u32"));
            }
        }
        let labels = table.len();
        let records = self.pool.len();
        let rows = self.memo.len();
        let wide = labels + records > usize::from(u16::MAX) + 1;
        let width: usize = if wide { 4 } else { 2 };
        let complain = |error: std::io::Error| format!("{}: {error}", path.display());
        let file = std::fs::File::create(path).map_err(complain)?;
        let mut out = std::io::BufWriter::with_capacity(1 << 20, file);
        writeln!(
            out,
            "# {MEMO_FORMAT}\t{{\"config\":{},\"horizon\":{horizon},\"rows\":{rows},\"labels\":{labels},\"records\":{records},\"width\":{width}}}",
            json_string(config)
        )
        .map_err(complain)?;
        for id in &table {
            writeln!(out, "{}", self.labels.text(*id)).map_err(complain)?;
        }
        for seat in 0..records {
            writeln!(
                out,
                "{}",
                settled_json(self.index, self.pool.get(SettledSeat::at(seat)))
            )
            .map_err(complain)?;
        }
        let label_count = u32::try_from(labels).expect("fewer labels than ids");
        let mut block: Vec<u8> = Vec::with_capacity(MEMO_BLOCK_ROWS * 7 * width);
        let push = |block: &mut Vec<u8>, value: u32| {
            if wide {
                block.extend_from_slice(&value.to_le_bytes());
            } else {
                block.extend_from_slice(
                    &u16::try_from(value)
                        .expect("a narrow file holds only narrow indexes")
                        .to_le_bytes(),
                );
            }
        };
        let mut pending = 0;
        for ((rune, left, rights), outcome) in &self.memo {
            let input = compact[input_of(*rune, *left)? as usize];
            let left_column = if self.labels.stops_reach(*left) {
                compact[*left as usize]
            } else {
                let seat = seat_of_label.get(left).copied().ok_or_else(|| {
                    format!(
                        "the settled left {} names no seated record",
                        self.labels.text(*left)
                    )
                })?;
                label_count + u32::try_from(seat).expect("a seat is a u32")
            };
            push(&mut block, input);
            push(&mut block, left_column);
            for right in rights {
                push(&mut block, compact[*right as usize]);
            }
            push(
                &mut block,
                u32::try_from(outcome.seat.index()).expect("a seat is a u32"),
            );
            pending += 1;
            if pending == MEMO_BLOCK_ROWS {
                out.write_all(&block).map_err(complain)?;
                block.clear();
                pending = 0;
            }
        }
        out.write_all(&block).map_err(complain)?;
        out.flush().map_err(complain)
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

    /// A scratch directory of this module's own under `target/`, cleared first.
    fn scratch(name: &str) -> std::path::PathBuf {
        let directory = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target/test-scratch")
            .join(name);
        let _ = std::fs::remove_dir_all(&directory);
        std::fs::create_dir_all(&directory).expect("the scratch directory is makeable");
        directory
    }

    /// One filed window memo read back whole: the head's JSON, the label table, the record lines, and the rows as seven integers each, decoded at the width the head states.
    struct FiledMemo {
        head: serde_json::Value,
        labels: Vec<String>,
        records: Vec<String>,
        rows: Vec<[u32; 7]>,
    }

    fn read_memo(path: &Path) -> FiledMemo {
        let bytes = std::fs::read(path).expect("the memo was filed");
        let mut at = 0;
        let mut line = || {
            let end = bytes[at..]
                .iter()
                .position(|byte| *byte == b'\n')
                .expect("a line ends");
            let text = std::str::from_utf8(&bytes[at..at + end]).expect("text lines are UTF-8");
            at += end + 1;
            text.to_owned()
        };
        let head_line = line();
        let (marker, json) = head_line.split_once('\t').expect("the head is two fields");
        assert_eq!(marker, format!("# {MEMO_FORMAT}"));
        let head: serde_json::Value = serde_json::from_str(json).expect("the head is JSON");
        let count = |key: &str| head[key].as_u64().expect("a count") as usize;
        let labels: Vec<String> = (0..count("labels")).map(|_| line()).collect();
        let records: Vec<String> = (0..count("records")).map(|_| line()).collect();
        let width = count("width");
        let body = &bytes[at..];
        assert_eq!(
            body.len(),
            count("rows") * 7 * width,
            "the rows fill the tail"
        );
        let rows: Vec<[u32; 7]> = body
            .chunks(7 * width)
            .map(|row| {
                let mut values = [0u32; 7];
                for (slot, value) in row.chunks(width).zip(values.iter_mut()) {
                    *value = match width {
                        2 => u32::from(u16::from_le_bytes([slot[0], slot[1]])),
                        _ => u32::from_le_bytes([slot[0], slot[1], slot[2], slot[3]]),
                    };
                }
                values
            })
            .collect();
        FiledMemo {
            head,
            labels,
            records,
            rows,
        }
    }

    /// The filed memo is complete over what the walk settled: one row per window the report counted, the head's count agreeing, every input and right inside the label table, every left inside the label table or the record table past it, and every record index seated.
    #[test]
    fn the_window_memo_files_one_row_per_settled_window_inside_its_tables() {
        let index = fixtures::mini();
        let rules = folded_rules(&index);
        let mut walk = replay(&index, &rules);
        let report = walk
            .walk_universe(Universe::whole(3))
            .expect("the table is complete");
        let path = scratch("replay-memo-complete").join("replay-windows-default.bin");
        walk.write_window_memo(&path, "default", 3)
            .expect("the memo files");
        let filed = read_memo(&path);
        assert_eq!(filed.head["config"], "default");
        assert_eq!(filed.head["horizon"], 3);
        assert_eq!(filed.rows.len() as u64, report.windows);
        assert_eq!(filed.head["rows"], filed.rows.len());
        assert_eq!(filed.records.len(), walk.pool.len());
        let labels = filed.labels.len() as u32;
        let records = filed.records.len() as u32;
        assert!(labels > 0 && records > 0);
        for row in &filed.rows {
            assert!(row[0] < labels, "the input is a label");
            assert!(row[1] < labels + records, "the left is a label or a seat");
            assert!(row[2..6].iter().all(|right| *right < labels));
            assert!(row[6] < records, "the value is a seated record");
        }
        assert!(!filed.labels.iter().any(String::is_empty));
        assert!(filed.labels.iter().any(|label| label == EDGE_LABEL));
    }

    /// The spellings the Python conversion depends on: an entry-bearing letter after a ZWNJ is filed under its locked name and a letter the chokepoint never locks under its raw one, the left of both is the ZWNJ's label, and the `#NA` cascade holds in the file exactly as it does in the key — every slot past a boundary or the edge is `#NA`.
    #[test]
    fn a_post_zwnj_input_is_filed_locked_and_nothing_reaches_past_a_boundary() {
        let index = fixtures::mini();
        let rules = folded_rules(&index);
        let mut walk = replay(&index, &rules);
        let pea = fixtures::sym(&index, "qsPea");
        let it = fixtures::sym(&index, "qsIt");
        let mut report = Report::default();
        let mut formed = Vec::new();
        for raw in [
            vec![RightToken::Zwnj, RightToken::Letter(pea)],
            vec![RightToken::Zwnj, RightToken::Letter(it)],
            vec![
                RightToken::Letter(pea),
                RightToken::Space,
                RightToken::Letter(pea),
                RightToken::Letter(pea),
            ],
        ] {
            walk.walk_text(&raw, &mut formed, &mut report)
                .expect("the fixture's texts settle");
        }
        let path = scratch("replay-memo-locked").join("replay-windows-default.bin");
        walk.write_window_memo(&path, "default", 4)
            .expect("the memo files");
        let filed = read_memo(&path);
        let spelled = |row: &[u32; 7]| -> Vec<String> {
            let mut words: Vec<String> = vec![filed.labels[row[0] as usize].clone()];
            words.push(if (row[1] as usize) < filed.labels.len() {
                filed.labels[row[1] as usize].clone()
            } else {
                format!("seat:{}", row[1] as usize - filed.labels.len())
            });
            words.extend(
                row[2..6]
                    .iter()
                    .map(|right| filed.labels[*right as usize].clone()),
            );
            words
        };
        let rows: Vec<Vec<String>> = filed.rows.iter().map(spelled).collect();
        let after_zwnj: Vec<&Vec<String>> = rows.iter().filter(|row| row[1] == "uni200C").collect();
        assert_eq!(after_zwnj.len(), 2, "{rows:?}");
        assert!(
            after_zwnj
                .iter()
                .any(|row| row[0] == "qsPea.noentry" && row[2..] == ["#EDGE", "#NA", "#NA", "#NA"]),
            "{rows:?}"
        );
        assert!(
            after_zwnj.iter().any(|row| row[0] == "qsIt"),
            "a rune the chokepoint never locks keeps its raw name: {rows:?}"
        );
        assert!(
            rows.iter()
                .any(|row| row[0] == "qsPea" && row[2..] == ["space", "#NA", "#NA", "#NA"]),
            "{rows:?}"
        );
        assert!(
            rows.iter()
                .any(|row| row[0] == "qsPea" && row[1] == "space" && row[2] == "qsPea"),
            "the letter after the space keys on the space as its left: {rows:?}"
        );
        for row in &rows {
            let mut reach = true;
            for right in &row[2..] {
                if !reach {
                    assert_eq!(right, "#NA", "{row:?}");
                }
                reach = reach
                    && !["#EDGE", "#NA", "space", "uni200C", "periodcentered"]
                        .contains(&right.as_str());
            }
            assert!(!row[0].starts_with('#') && row[1] != "#NA");
        }
    }

    /// The left's transport: a left is filed as a label exactly when its spelling stops the reach, and as a seat otherwise, and a seat's record round-trips through `settled_json` to the record the walk seated under the label the walk keyed on.
    #[test]
    fn a_left_is_a_boundary_label_where_the_reach_stops_and_a_seat_otherwise() {
        let index = fixtures::mini();
        let rules = folded_rules(&index);
        let mut walk = replay(&index, &rules);
        walk.walk_universe(Universe::whole(3))
            .expect("the table is complete");
        let path = scratch("replay-memo-lefts").join("replay-windows-default.bin");
        walk.write_window_memo(&path, "default", 3)
            .expect("the memo files");
        let filed = read_memo(&path);
        let labels = filed.labels.len() as u32;
        let mut seated = 0;
        let mut boundaries = 0;
        for row in &filed.rows {
            if row[1] < labels {
                let text = &filed.labels[row[1] as usize];
                assert!(
                    [EDGE_LABEL, "space", "uni200C", "periodcentered"].contains(&text.as_str()),
                    "{text} is not a left that stops the reach"
                );
                boundaries += 1;
            } else {
                let seat = (row[1] - labels) as usize;
                let value: serde_json::Value =
                    serde_json::from_str(&filed.records[seat]).expect("a record line is JSON");
                let record = crate::cases::parse_settled_json(&index, &value)
                    .expect("a record line reads back as a settled record");
                assert_eq!(&record, walk.pool.get(SettledSeat::at(seat)));
                assert_eq!(
                    cell_label(&index, &record.cell),
                    walk.labels.text(walk.seat_labels[seat])
                );
                seated += 1;
            }
        }
        assert!(seated > 0 && boundaries > 0);
        for (seat, line) in filed.records.iter().enumerate() {
            let value: serde_json::Value = serde_json::from_str(line).expect("JSON");
            let record = crate::cases::parse_settled_json(&index, &value).expect("reads back");
            assert_eq!(&record, walk.pool.get(SettledSeat::at(seat)));
        }
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
