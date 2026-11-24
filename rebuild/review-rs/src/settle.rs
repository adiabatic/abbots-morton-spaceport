//! Settlement in process, as `rebuild/pipeline/settle.py` settles a text: the tokenizer (`settle.tokens_from_codepoints`), ligature formation against the §5.7 guard verdicts (`settle.form_ligatures`, `_ligature_order`, `guard_blocks`, `kernel_exec.guard_sweep`), the isolated overlay (`model.isolated_overlay_active`, `settle.isolated_overlay_traces`), and the position-by-position settlement `kernel_exec.settle_sequences` runs through `settle-cases` (`explain.explain_many`, `_report_of`), here through the kernel crate's own engine with no process between. The enricher's formed spans read the same formation.
//!
//! [`Settlement`] holds what every thread shares: the spec index, the guard verdicts parsed once from `ams_m1_kernel::guard::sweep`, the ligature order and the code point tables. A [`Settler`] holds one thread's engines, one per configuration, built as the kernel's `engine_for` builds them for `settle-cases`; [`Settler::release`] drops the engines at a batch's end and the next text builds them afresh, which changes no answer because everything an engine keeps between texts is a pure cache, so a thread's footprint stays one batch's whatever the handouts it draws.
//!
//! A letter left is the engine's own settled record (`LeftContext::letter`), which `settle-cases` would read back from a case line through the kernel's `cases::parse_left`; a boundary left is `LeftContext::boundary`.

use std::collections::HashMap;
use std::fmt;

use ams_m1_kernel::engine::{Engine, EngineModes, Slots};
use ams_m1_kernel::index::SpecIndex;
use ams_m1_kernel::model::Sym;
use ams_m1_kernel::token::EDGE;
use ams_m1_kernel::types::{
    CellId, LeftContext, RightToken, Settled, TokenKind, TraceRanking, boundary_settled,
};

use crate::wire::Settlement as Modes;

/// The deciding stage of a letter under the isolated overlay, `settle.ISOLATED_OVERLAY_STAGE`.
pub const ISOLATED_OVERLAY_STAGE: &str = "isolated-overlay";

/// The deciding stage of a boundary position.
const BOUNDARY_STAGE: &str = "boundary";

/// The overlay a feature turns on when nothing settles under it (`model.isolated_overlay_active`).
const ISOLATED_OVERLAY: &str = "isolated";

/// The configuration token of the no-feature configuration (`labels.features_for_config`).
const DEFAULT_CONFIG: &str = "default";

/// A text that cannot be settled.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SettleError(pub String);

impl fmt::Display for SettleError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for SettleError {}

type Result<T> = std::result::Result<T, SettleError>;

fn error<T>(message: impl Into<String>) -> Result<T> {
    Err(SettleError(message.into()))
}

/// The features of a configuration token, `labels.features_for_config`, sorted as `kernel_exec._settle_cases` passes them.
pub fn features_for_config(config: &str) -> Vec<String> {
    if config == DEFAULT_CONFIG {
        return Vec::new();
    }
    let mut features: Vec<String> = config.split('+').map(str::to_owned).collect();
    features.sort();
    features.dedup();
    features
}

/// One settled position: the kernel trace's fields with the deciding stage as its name, so a letter under the isolated overlay can carry [`ISOLATED_OVERLAY_STAGE`], which no kernel stage is.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Trace {
    pub settled: Settled,
    pub joint_tiebreak: bool,
    pub prospect: i64,
    pub decided_stage: &'static str,
    pub notes: Vec<String>,
    pub ranking: TraceRanking,
}

impl Trace {
    fn plain(settled: Settled, decided_stage: &'static str) -> Self {
        Self {
            settled,
            joint_tiebreak: false,
            prospect: 0,
            decided_stage,
            notes: Vec::new(),
            ranking: TraceRanking::default(),
        }
    }
}

/// One text's settlement, `explain.ExplainReport` without its spec: the code points, the configuration's features, and one trace per formed position.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Report {
    pub codepoints: Vec<u32>,
    pub features: Vec<String>,
    pub traces: Vec<Trace>,
}

/// The key of one guard verdict: the ligature, and the two raw slots after its sequence.
type GuardKey = (Sym, RightToken, RightToken);

/// What every thread shares: the spec index and the settlement modes, the guard verdicts, the ligature order, and the code point tables the tokenizer reads.
pub struct Settlement {
    index: SpecIndex,
    modes: Modes,
    guard: HashMap<GuardKey, bool>,
    by_lead: HashMap<Sym, Vec<(Vec<Sym>, Sym)>>,
    boundaries: HashMap<i64, RightToken>,
    families: HashMap<i64, (String, Option<RightToken>)>,
    overlays: Vec<Sym>,
}

impl Settlement {
    /// Index the spec, sweep the guard once and read its verdicts, and order the ligatures, all under the init's settlement modes.
    pub fn new(index: SpecIndex, modes: Modes) -> Result<Self> {
        let guard = guard_verdicts(&index)?;
        let by_lead = ligature_order(&index);
        let registry = index.registry();
        let mut boundaries = HashMap::new();
        for (name, token) in registry.boundary_tokens.iter() {
            let name = index.resolve(*name);
            let kind = TokenKind::from_text(name)
                .and_then(RightToken::of_kind)
                .ok_or_else(|| {
                    SettleError(format!(
                        "the registry's boundary token {name} is no kind the kernel reads"
                    ))
                })?;
            boundaries.insert(token.codepoint, kind);
        }
        let mut families = HashMap::new();
        for (name, family) in registry.families.iter() {
            if let Some(codepoint) = family.codepoint {
                let letter = index
                    .is_modeled(*name)
                    .then(|| index.letter(*name))
                    .flatten();
                families.insert(codepoint, (index.resolve(*name).to_owned(), letter));
            }
        }
        let overlays = registry
            .features
            .iter()
            .filter(|(_, feature)| {
                feature
                    .overlay
                    .is_some_and(|overlay| index.resolve(overlay) == ISOLATED_OVERLAY)
            })
            .map(|(name, _)| *name)
            .collect();
        Ok(Self {
            index,
            modes,
            guard,
            by_lead,
            boundaries,
            families,
            overlays,
        })
    }

    /// The spec index every engine reads.
    pub fn index(&self) -> &SpecIndex {
        &self.index
    }

    /// The raw tokens of a text, `settle.tokens_from_codepoints`: a boundary token by its registry code point, else a registered family's letter, and an error for a code point the registry lacks or a family the spec does not model.
    pub fn tokens(&self, codepoints: &[u32]) -> Result<Vec<RightToken>> {
        codepoints
            .iter()
            .map(|codepoint| {
                let key = i64::from(*codepoint);
                if let Some(boundary) = self.boundaries.get(&key) {
                    return Ok(*boundary);
                }
                match self.families.get(&key) {
                    None => error(format!("U+{codepoint:04X} is not in the registry")),
                    Some((name, None)) => error(format!(
                        "U+{codepoint:04X} ({name}) is registered but not modeled in this spec"
                    )),
                    Some((_, Some(letter))) => Ok(*letter),
                }
            })
            .collect()
    }

    /// Whether the §5.7 guard blocks `liga` before these two raw slots, `settle.guard_blocks`: never before a non-letter, and otherwise the sweep's verdict, whose absence is an error.
    fn guard_blocks(&self, liga: Sym, right1: RightToken, right2: RightToken) -> Result<bool> {
        if right1.kind() != TokenKind::Letter {
            return Ok(false);
        }
        self.guard
            .get(&(liga, right1, right2))
            .copied()
            .ok_or_else(|| {
                SettleError(format!(
                    "the guard verdicts hold no verdict for {} before {} and {}",
                    self.index.resolve(liga),
                    token_text(&self.index, right1),
                    token_text(&self.index, right2)
                ))
            })
    }

    /// Ligature formation, `settle.form_ligatures`: greedy left to right, longest sequence first with ties in the spec's rune order, each match skipped when the guard blocks it over the two raw tokens after it.
    pub fn form(&self, tokens: &[RightToken]) -> Result<Vec<RightToken>> {
        let mut formed = Vec::with_capacity(tokens.len());
        let mut at = 0;
        while at < tokens.len() {
            let mut matched: Option<(Sym, usize)> = None;
            if let Some(lead) = tokens[at].rune()
                && let Some(sequences) = self.by_lead.get(&lead)
            {
                for (sequence, name) in sequences {
                    let end = at + sequence.len();
                    if end > tokens.len()
                        || !sequence
                            .iter()
                            .zip(&tokens[at..end])
                            .all(|(part, token)| token.rune() == Some(*part))
                    {
                        continue;
                    }
                    let right1 = tokens.get(end).copied().unwrap_or(EDGE);
                    let right2 = tokens.get(end + 1).copied().unwrap_or(EDGE);
                    if self.guard_blocks(*name, right1, right2)? {
                        continue;
                    }
                    matched = Some((*name, sequence.len()));
                    break;
                }
            }
            match matched {
                Some((name, width)) => {
                    formed.push(
                        self.index
                            .letter(name)
                            .expect("a ligature is a modeled rune"),
                    );
                    at += width;
                }
                None => {
                    formed.push(tokens[at]);
                    at += 1;
                }
            }
        }
        Ok(formed)
    }

    /// Each formed token's code point span, as the enricher reads it: a ligature covers its sequence's length and every other token one code point, and the spans must cover the text.
    pub fn formed_spans(&self, codepoints: &[u32]) -> Result<Vec<(usize, usize)>> {
        let formed = self.form(&self.tokens(codepoints)?)?;
        let mut spans = Vec::with_capacity(formed.len());
        let mut consumed = 0;
        for token in formed {
            let width = token
                .rune()
                .and_then(|name| self.index.rune(name))
                .and_then(|rune| rune.sequence.as_ref())
                .filter(|sequence| !sequence.is_empty())
                .map_or(1, Vec::len);
            spans.push((consumed, consumed + width));
            consumed += width;
        }
        if consumed != codepoints.len() {
            return error(format!(
                "formation spans cover {consumed} of {} codepoints",
                codepoints.len()
            ));
        }
        Ok(spans)
    }

    /// Whether any of the features is registered with the isolated overlay, `model.isolated_overlay_active`; a name the registry lacks turns nothing on.
    pub fn overlay_active(&self, features: &[String]) -> bool {
        features.iter().any(|name| {
            self.index
                .sym_of(name)
                .is_some_and(|feature| self.overlays.contains(&feature))
        })
    }

    /// The configuration's features as symbols, refusing a name the spec never mentions as the kernel's `settle-cases` refuses it.
    fn feature_syms(&self, features: &[String]) -> Result<Vec<Sym>> {
        features
            .iter()
            .map(|name| {
                self.index.sym_of(name).ok_or_else(|| {
                    SettleError(format!("{name} is a feature this spec never mentions"))
                })
            })
            .collect()
    }

    /// The isolated overlay's traces over the raw tokens, `settle.isolated_overlay_traces`: each letter as its rune's default stance with no heights, decided by [`ISOLATED_OVERLAY_STAGE`], and each boundary as its boundary cell.
    fn overlay_traces(&self, tokens: &[RightToken]) -> Vec<Trace> {
        tokens
            .iter()
            .map(|token| match token.rune() {
                None => Trace::plain(
                    boundary_settled(self.index.vocab(), token.kind()),
                    BOUNDARY_STAGE,
                ),
                Some(letter) => Trace::plain(
                    Settled {
                        cell: CellId {
                            rune: letter,
                            stance: self
                                .index
                                .default_stance(letter)
                                .expect("a letter token's rune is modeled"),
                            entry: None,
                            exit: None,
                            adjustments: Vec::new(),
                        },
                        junction: None,
                        extension: 0,
                    },
                    ISOLATED_OVERLAY_STAGE,
                ),
            })
            .collect()
    }
}

fn token_text(index: &SpecIndex, token: RightToken) -> &str {
    token
        .rune()
        .map_or(token.kind().as_str(), |rune| index.resolve(rune))
}

/// The modeled ligature sequences in `settle._ligature_order`'s order, a stable sort by descending length over the spec's rune order, grouped by first rune.
fn ligature_order(index: &SpecIndex) -> HashMap<Sym, Vec<(Vec<Sym>, Sym)>> {
    let mut sequences: Vec<(Vec<Sym>, Sym)> = index
        .runes()
        .iter()
        .filter_map(|(name, rune)| {
            rune.sequence
                .as_ref()
                .filter(|sequence| !sequence.is_empty())
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
    by_lead
}

/// The kernel's guard sweep read as `kernel_exec._guard_verdicts` reads `guard-sweep`'s output: one verdict per ligature, first-slot rune and second-slot token, the second slot a rune when it names one and otherwise a boundary or the unknown, and a malformed or repeated line refused.
fn guard_verdicts(index: &SpecIndex) -> Result<HashMap<GuardKey, bool>> {
    let lines = ams_m1_kernel::guard::sweep(index)
        .map_err(|failure| SettleError(format!("the guard sweep refused: {failure}")))?;
    let modeled = |name: &str| {
        index
            .sym_of(name)
            .filter(|sym| index.is_modeled(*sym))
            .and_then(|sym| index.letter(sym))
    };
    let mut verdicts = HashMap::with_capacity(lines.len());
    for line in &lines {
        let fields: Vec<&str> = line.split('\t').collect();
        let [ligature, right1, right2, verdict] = fields[..] else {
            return error(format!("a guard verdict line is not four fields: {line:?}"));
        };
        let liga = modeled(ligature)
            .filter(|token| {
                index
                    .rune(token.letter())
                    .and_then(|rune| rune.sequence.as_ref())
                    .is_some_and(|sequence| !sequence.is_empty())
            })
            .ok_or_else(|| SettleError(format!("the guard names non-ligature {ligature:?}")))?;
        let right1 = modeled(right1).ok_or_else(|| {
            SettleError(format!(
                "the guard names unknown first-slot rune {right1:?}"
            ))
        })?;
        let right2 = match modeled(right2) {
            Some(letter) => letter,
            None => TokenKind::from_text(right2)
                .and_then(RightToken::of_kind)
                .ok_or_else(|| {
                    SettleError(format!(
                        "the guard names unknown second-slot token {right2:?}"
                    ))
                })?,
        };
        let blocked = match verdict {
            "blocked" => true,
            "free" => false,
            _ => return error(format!("the guard's verdict {verdict:?} is neither")),
        };
        if verdicts
            .insert((liga.letter(), right1, right2), blocked)
            .is_some()
        {
            return error(format!("the guard repeats a verdict: {line:?}"));
        }
    }
    Ok(verdicts)
}

/// One thread's settlement: an engine per configuration, each with the trace memo on and the init's two modes, as the kernel's `engine_for` builds a `settle-cases` engine.
pub struct Settler<'s> {
    settlement: &'s Settlement,
    engines: HashMap<Vec<Sym>, Engine<'s>>,
}

impl<'s> Settler<'s> {
    pub fn new(settlement: &'s Settlement) -> Self {
        Self {
            settlement,
            engines: HashMap::new(),
        }
    }

    /// Drop every engine, at a batch's end.
    pub fn release(&mut self) {
        self.engines.clear();
    }

    /// Settle one text under one configuration's features, `explain.explain_many` for one request: under the isolated overlay the raw tokens' overlay traces; otherwise the formed tokens settled position by position, a boundary settling to its boundary cell and resetting the left, and a letter settling through the configuration's engine against the left the previous letter settled into.
    pub fn explain(&mut self, codepoints: &[u32], features: &[String]) -> Result<Report> {
        let settlement = self.settlement;
        let tokens = settlement.tokens(codepoints)?;
        let report = |traces| Report {
            codepoints: codepoints.to_vec(),
            features: features.to_vec(),
            traces,
        };
        if settlement.overlay_active(features) {
            return Ok(report(settlement.overlay_traces(&tokens)));
        }
        let syms = settlement.feature_syms(features)?;
        let formed = settlement.form(&tokens)?;
        let index = &settlement.index;
        let modes = &settlement.modes;
        let engine = self.engines.entry(syms.clone()).or_insert_with(|| {
            Engine::with_modes(
                index,
                syms,
                EngineModes {
                    simulated_prospect: modes.simulated_prospect,
                    follower_prefer_slots: modes.follower_prefer_slots,
                    trace_memo: true,
                    ..EngineModes::default()
                },
            )
        });
        let mut traces = Vec::with_capacity(formed.len());
        let mut left = LeftContext::boundary(TokenKind::Edge);
        for (position, token) in formed.iter().enumerate() {
            if token.kind() != TokenKind::Letter {
                traces.push(Trace::plain(
                    boundary_settled(index.vocab(), token.kind()),
                    BOUNDARY_STAGE,
                ));
                left = LeftContext::boundary(token.kind());
                continue;
            }
            let slot = |offset: usize| formed.get(position + offset).copied().unwrap_or(EDGE);
            let slots = Slots::new(slot(1), slot(2), slot(3), slot(4));
            let trace = engine
                .transition_trace(&left, *token, slots)
                .map_err(|failure| {
                    SettleError(format!(
                        "position {position} of {}: {}",
                        hex(codepoints),
                        failure.message()
                    ))
                })?;
            left = LeftContext::letter(index, trace.settled.clone());
            traces.push(Trace {
                ranking: trace.ranking().clone(),
                settled: trace.settled,
                joint_tiebreak: trace.joint_tiebreak,
                prospect: trace.prospect,
                decided_stage: trace.decided_stage.as_str(),
                notes: trace.notes,
            });
        }
        Ok(report(traces))
    }
}

fn hex(codepoints: &[u32]) -> String {
    codepoints
        .iter()
        .map(|value| format!("{value:04X}"))
        .collect::<Vec<_>>()
        .join(":")
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use ams_m1_kernel::parse::parse_spec;

    const STANCES: &str = r#"{"bare":{"name":"bare","motion":"flat","traits":[],"bitmap":{"rows":[],"y_offset":0},"bitmaps":{},"surface":{"entries":{},"exits":{},"pairings":{"never":[],"only":null},"cells":[],"unlocks":[],"require":[]}}}"#;
    const POLICY: &str = r#"{"order":[],"refuse":[],"prefer":[],"extend":[],"contract":[],"resolve":[],"groups":{}}"#;

    fn rune(name: &str, codepoint: &str, sequence: &str) -> String {
        format!(
            r#""{name}":{{"name":"{name}","codepoint":{codepoint},"sequence":{sequence},"ductus":{{}},"notes":null,"mono":null,"stances":{STANCES},"policy":{POLICY}}}"#
        )
    }

    /// Two letters and their ligature, none of which joins, beside a registered family the spec does not model and the isolated overlay's stylistic set.
    pub(crate) fn settlement() -> Settlement {
        let ligature = r#"["qsPea","qsTea"]"#;
        let runes = [
            rune("qsPea", "58960", "null"),
            rune("qsTea", "58962", "null"),
            rune("qsPea_qsTea", "null", ligature),
        ]
        .join(",");
        let text = format!(
            r#"{{"format":"ams-m1-spec/1","runes":{{{runes}}},"registry":{{"heights":{{"baseline":0}},"boundary_tokens":{{"space":{{"codepoint":32,"splits_runs":true}},"zwnj":{{"codepoint":8204,"splits_runs":true}},"namer-dot":{{"codepoint":183,"splits_runs":false}}}},"features":{{"ss10":{{"kind":"taste","description":"","overlay":"isolated"}}}},"interactions":[],"predicate_classes":{{}},"families":{{"qsPea":{{"codepoint":58960,"sequence":null}},"qsTea":{{"codepoint":58962,"sequence":null}},"qsMay":{{"codepoint":58981,"sequence":null}},"qsPea_qsTea":{{"codepoint":null,"sequence":{ligature}}}}}}}}}"#
        );
        let spec = parse_spec(&text).expect("the test spec parses");
        Settlement::new(
            SpecIndex::new(spec),
            Modes {
                simulated_prospect: true,
                follower_prefer_slots: true,
            },
        )
        .expect("the test spec settles")
    }

    fn summary(settlement: &Settlement, report: &Report) -> Vec<(String, String, &'static str)> {
        let index = settlement.index();
        report
            .traces
            .iter()
            .map(|trace| {
                (
                    index.resolve(trace.settled.cell.rune).to_owned(),
                    index.resolve(trace.settled.cell.stance).to_owned(),
                    trace.decided_stage,
                )
            })
            .collect()
    }

    fn owned(rows: &[(&str, &str, &'static str)]) -> Vec<(String, String, &'static str)> {
        rows.iter()
            .map(|(rune, stance, stage)| ((*rune).to_owned(), (*stance).to_owned(), *stage))
            .collect()
    }

    #[test]
    fn a_configuration_token_names_its_features_sorted() {
        assert!(features_for_config("default").is_empty());
        assert_eq!(features_for_config("ss05+ss03"), ["ss03", "ss05"]);
    }

    #[test]
    fn a_text_forms_its_ligature_and_settles_each_position_against_its_left() {
        let settlement = settlement();
        let text = [0xE650, 0xE652, 0x20, 0xE650];
        assert_eq!(
            settlement.formed_spans(&text).expect("spans"),
            [(0, 2), (2, 3), (3, 4)]
        );
        let mut settler = Settler::new(&settlement);
        let report = settler.explain(&text, &[]).expect("settles");
        assert_eq!(
            summary(&settlement, &report),
            owned(&[
                ("qsPea_qsTea", "bare", "only-candidate"),
                ("space", "boundary", "boundary"),
                ("qsPea", "bare", "only-candidate"),
            ])
        );
        settler.release();
        assert_eq!(settler.explain(&text, &[]).expect("settles"), report);
    }

    #[test]
    fn the_isolated_overlay_settles_nothing_and_forms_no_ligature() {
        let settlement = settlement();
        let mut settler = Settler::new(&settlement);
        let report = settler
            .explain(&[0xE650, 0xE652, 0xB7], &features_for_config("ss10"))
            .expect("the overlay");
        assert_eq!(
            summary(&settlement, &report),
            owned(&[
                ("qsPea", "bare", ISOLATED_OVERLAY_STAGE),
                ("qsTea", "bare", ISOLATED_OVERLAY_STAGE),
                ("namer-dot", "boundary", "boundary"),
            ])
        );
    }

    #[test]
    fn a_code_point_the_spec_does_not_model_or_a_feature_it_never_mentions_is_refused() {
        let settlement = settlement();
        let mut settler = Settler::new(&settlement);
        for (text, features, message) in [
            (0xE651, vec![], "U+E651 is not in the registry"),
            (
                0xE665,
                vec![],
                "U+E665 (qsMay) is registered but not modeled in this spec",
            ),
            (
                0xE650,
                vec!["ss99".to_owned()],
                "ss99 is a feature this spec never mentions",
            ),
        ] {
            assert_eq!(
                settler.explain(&[text], &features),
                Err(SettleError(message.to_owned()))
            );
        }
    }
}
