//! Case replay: one tab-separated question line in, the same line back out with this kernel's answer after a tab. The shape is this crate's own on both halves — `rebuild/pipeline/kernel_exec.py`'s `case_line` writes a question, and its `trace_of` reads the JSON answer or its `_settled_of_fields` the tab-separated one — and the echoed question is what lines a batch's answers up with its questions, which `kernel_exec._settle_cases` checks before a caller decodes any of them.
//!
//! Re-emitting the question rather than only the answer is what makes that check possible: an output line carries the question it answers, so a line the reader skipped, reordered or answered out of turn cannot pass as an answer to the question that was asked. The echo is the input line's own bytes, verbatim, so what proves the inputs were *understood* is not the echo but the reader's refusals: a field count other than [`CASE_FIELDS`], a name the spec never interned, a kind spelling outside the six, and an adjustments token outside the grammar are all refusals rather than answers, so a misread case stops the run instead of diverging on the answer alone, and a field this build cannot place is a refusal rather than a value riding through.
//!
//! A question is thirteen fields: the left's kind and its record — rune, stance, entry, exit, comma-joined adjustments, seam, extension, all seven empty for a left with no record and a height or seam empty where there is none — then the rune under settlement and the four raw slots after it, each a rune name or the kind spelling of a boundary or unknown slot. Nothing in that vocabulary can carry a tab or a newline.
//!
//! The answer comes in one of two shapes, which the command line chooses. The trace ([`Answer::Trace`]) is one JSON object carrying the whole route, not only the row-visible record: the settled cell, the prospect, the joint-floor flag, the notes and the fired delta, then the deciding stage, the runner-up, the ranked ladder and the eliminations. The last four are the route rather than the outcome, and they are what the explain panel and the review surface's explain view read — a window can land on the right cell by the wrong route, and the ladder is where that shows. The settled-only answer ([`Answer::SettledOnly`]) is the record alone as seven tab-separated fields, [`settled_fields`]' spelling, for the conform walker that settles windows by the hundred thousand and keeps only the outcome. A refusal is the same `{"raise":…,"message":…}` object in both shapes, so a reader tells one from a settled record by its first byte.
//!
//! The fired delta is the field no downstream artifact re-derives: a port that settles onto the right cell by the wrong route builds a table whose dead-policy gate reads live records as dead. It comes from the trace memo's journaled delta for this case's own key, which means a *missing* delta is not an empty one — it says this replay's key shape and the memo's have drifted apart, and it stops the run rather than answering. The settled-only answer looks the delta up too and reports none of it, so the drift alarm is the same in both shapes.

use crate::emit::json_string;
use crate::engine::{Engine, Slots};
use crate::error::{SettleError, SettleErrorKind};
use crate::index::SpecIndex;
use crate::model::Sym;
use crate::types::{
    AdjustmentToken, Candidate, CellId, LeftContext, RightToken, Settled, Side, TokenKind,
    TransitionTrace, height_json, provenance_pointer, settled_fields, settled_json,
};

/// The corpus's three raise buckets. `E-UNREACHABLE` takes the stranded window and every plain settle error alike, which is why the message rides beside it — an identity alone cannot tell a stranded exit from a rune that is not modeled.
const RAISE_INCOMPARABLE: &str = "E-INCOMPARABLE";
const RAISE_AMBIGUOUS: &str = "E-AMBIGUOUS";
const RAISE_UNREACHABLE: &str = "E-UNREACHABLE";

/// How many tab-separated fields a question line is: the left's kind, its seven record fields, the input rune, and the four right slots.
pub const CASE_FIELDS: usize = 13;

/// Which answer a replay writes after the echoed question.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Answer {
    /// The whole trace as one JSON object, the shape `kernel_exec.trace_of` reads.
    Trace,
    /// The settled record alone as seven tab-separated fields, the shape `kernel_exec._settled_of_fields` reads.
    SettledOnly,
}

/// One case: the window to settle, beside the question line its answer is written after. The line is kept rather than re-spelled so that the echo is the caller's own bytes.
#[derive(Clone, Debug)]
pub struct Case<'l> {
    pub left: LeftContext,
    pub token: RightToken,
    pub slots: Slots,
    line: &'l str,
}

/// Read one question line, `kernel_exec.case_line`'s inverse. A name the spec never interned is a hard error rather than a settlement outcome: the case was cut against some other spec, and answering it would compare two different questions.
pub fn parse_case<'l>(index: &SpecIndex, line: &'l str) -> Result<Case<'l>, String> {
    let fields: Vec<&str> = line.split('\t').collect();
    let [
        kind,
        rune,
        stance,
        entry,
        exit,
        adjustments,
        seam,
        extension,
        input,
        right1,
        right2,
        right3,
        right4,
    ] = fields[..]
    else {
        return Err(format!(
            "a case is {CASE_FIELDS} tab-separated fields, and this line has {}",
            fields.len()
        ));
    };
    let left = parse_left(
        index,
        kind,
        [rune, stance, entry, exit, adjustments, seam, extension],
    )?;
    let token = RightToken::Letter(symbol(index, input, "the input rune")?);
    let slots = Slots::new(
        parse_token(index, right1)?,
        parse_token(index, right2)?,
        parse_token(index, right3)?,
        parse_token(index, right4)?,
    );
    Ok(Case {
        left,
        token,
        slots,
        line,
    })
}

/// One case's whole output line: the question as it arrived, a tab, and this kernel's answer in the shape asked for.
pub fn replay_case(
    engine: &mut Engine<'_>,
    case: &Case<'_>,
    answer: Answer,
) -> Result<String, String> {
    let result = result_text(engine, case, answer)?;
    Ok(format!("{}\t{result}", case.line))
}

/// A whole case file replayed through one engine in file order — the `settle-cases` verb's body. An optional leading `# ` marker line is a head and is skipped rather than parsed: the modes a file was cut under reach this kernel as CLI flags, so the world a batch is answered in is the caller's word and never the file's.
///
/// The engine is shared across the file, so a batch settles warm. That costs the answers nothing: each memoized evaluation replays its journaled delta on every hit, precisely so a warm answer and a cold one agree down to the fired set.
pub fn replay_cases(
    engine: &mut Engine<'_>,
    text: &str,
    answer: Answer,
) -> Result<Vec<String>, String> {
    let mut lines = Vec::new();
    for (seat, line) in text.lines().enumerate() {
        if seat == 0 && line.starts_with("# ") {
            continue;
        }
        let numbered = |complaint: String| format!("line {}: {complaint}", seat + 1);
        let case = parse_case(engine.index(), line).map_err(numbered)?;
        lines.push(replay_case(engine, &case, answer).map_err(numbered)?);
    }
    Ok(lines)
}

/// This case's answer: the row-visible record with its fired delta and the ladder under the trace shape, the record's seven fields under the settled-only one, or in either shape the raise bucket with the message that came with it, which `settle.SettleError` carries as its `.bucket` and its own text. The delta is looked up in both shapes, because a missing one is the drift alarm and not an empty field.
fn result_text(engine: &mut Engine<'_>, case: &Case<'_>, answer: Answer) -> Result<String, String> {
    let index = engine.index();
    let trace = match engine.transition_trace(&case.left, case.token, case.slots) {
        Ok(trace) => trace,
        Err(error) => return Ok(raise_text(&error)),
    };
    let Some(delta) = engine.trace_delta(&case.left, case.token, case.slots) else {
        return Err("the settled case left no journaled fired delta — the trace memo's key shape has moved and this replay's key must follow".to_owned());
    };
    match answer {
        Answer::SettledOnly => Ok(settled_fields(index, &trace.settled)),
        Answer::Trace => {
            let fired: Vec<String> = delta.iter().map(|pointer| pointer.text(index)).collect();
            Ok(settled_text(index, &trace, &fired))
        }
    }
}

fn raise_text(error: &SettleError) -> String {
    let bucket = match error.kind() {
        SettleErrorKind::Incomparable => RAISE_INCOMPARABLE,
        SettleErrorKind::Ambiguous => RAISE_AMBIGUOUS,
        SettleErrorKind::Stranded | SettleErrorKind::Plain => RAISE_UNREACHABLE,
    };
    format!(
        "{{\"raise\":{},\"message\":{}}}",
        json_string(bucket),
        json_string(error.message())
    )
}

/// The settled trace, in the key order an answer is read in: the row-visible record and its fired delta first, then the deciding stage, the runner-up, the ranked ladder and the eliminations.
fn settled_text(index: &SpecIndex, trace: &TransitionTrace, fired: &[String]) -> String {
    let ladder = trace.ladder();
    let runner_up = match &ladder.runner_up {
        Some(candidate) => candidate_json(index, candidate),
        None => "null".to_owned(),
    };
    let ranked: Vec<String> = ladder
        .ranked
        .iter()
        .map(|entry| {
            format!(
                "[{},{},{}]",
                candidate_json(index, &entry.candidate),
                entry.join_count,
                entry.prospect
            )
        })
        .collect();
    let eliminations: Vec<String> = ladder
        .eliminations
        .iter()
        .map(|elimination| {
            let provenance = match &elimination.provenance {
                Some(provenance) => json_string(&provenance_pointer(index, provenance)),
                None => "null".to_owned(),
            };
            format!(
                "[{},{},{provenance}]",
                json_string(elimination.stage.as_str()),
                json_string(&elimination.description)
            )
        })
        .collect();
    format!(
        "{{\"settled\":{},\"prospect\":{},\"joint_floor\":{},\"notes\":{},\"fired\":{},\"decided_stage\":{},\"runner_up\":{runner_up},\"ranked\":[{}],\"eliminations\":[{}]}}",
        settled_json(index, &trace.settled),
        trace.prospect,
        trace.joint_floor,
        strings_json(&trace.notes),
        strings_json(fired),
        json_string(trace.decided_stage.as_str()),
        ranked.join(","),
        eliminations.join(","),
    )
}

/// One candidate as the trace spells it, and as `kernel_exec._candidate_of` reads it back into a `settle.Candidate`: the stance, its two heights, and the two indices the ranking and the floor sort on. A non-joining candidate carries the sentinel exit index's own value rather than a null, because what a reader wants is the sort key the ranking used — `settle._NO_EXIT_INDEX` is that value's Python spelling.
fn candidate_json(index: &SpecIndex, candidate: &Candidate) -> String {
    format!(
        "[{},{},{},{},{}]",
        json_string(index.resolve(candidate.stance)),
        height_json(index, candidate.entry),
        height_json(index, candidate.seam),
        candidate.order_index,
        candidate.exit_index
    )
}

fn strings_json(values: &[String]) -> String {
    let quoted: Vec<String> = values.iter().map(|value| json_string(value)).collect();
    format!("[{}]", quoted.join(","))
}

fn symbol(index: &SpecIndex, name: &str, what: &str) -> Result<Sym, String> {
    index
        .sym_of(name)
        .ok_or_else(|| format!("{what} names {name}, which this spec never mentions"))
}

fn optional_symbol(index: &SpecIndex, name: &str, what: &str) -> Result<Option<Sym>, String> {
    if name.is_empty() {
        return Ok(None);
    }
    symbol(index, name, what).map(Some)
}

fn kind_of(name: &str, what: &str) -> Result<TokenKind, String> {
    TokenKind::from_text(name).ok_or_else(|| format!("{what} names no known kind: {name}"))
}

/// The left's kind and its seven record fields. A left with no record spells all seven empty; a rune field that is empty beside a record field that is not is a line this reader cannot place, and is refused rather than read as either.
fn parse_left(index: &SpecIndex, kind: &str, record: [&str; 7]) -> Result<LeftContext, String> {
    let kind = kind_of(kind, "the left's kind")?;
    if record[0].is_empty() {
        if record.iter().any(|field| !field.is_empty()) {
            return Err(
                "a left with no rune carries no record, and this one spells one".to_owned(),
            );
        }
        return Ok(LeftContext {
            kind,
            settled: None,
        });
    }
    Ok(LeftContext {
        kind,
        settled: Some(parse_settled(index, record)?),
    })
}

/// The seven record fields — rune, stance, entry, exit, comma-joined adjustments, seam, extension — read back into a settled record: a question's left, in the spelling [`settled_fields`] answers in.
pub(crate) fn parse_settled(
    index: &SpecIndex,
    [rune, stance, entry, exit, adjustments, seam, extension]: [&str; 7],
) -> Result<Settled, String> {
    let adjustments: Result<Vec<AdjustmentToken>, String> = if adjustments.is_empty() {
        Ok(Vec::new())
    } else {
        adjustments
            .split(',')
            .map(|token| parse_adjustment(index, token))
            .collect()
    };
    let extension: i64 = extension
        .parse()
        .map_err(|_| format!("the left's extension is not an integer: {extension:?}"))?;
    Ok(Settled {
        cell: CellId {
            rune: symbol(index, rune, "the left cell's rune")?,
            stance: symbol(index, stance, "the left cell's stance")?,
            entry: optional_symbol(index, entry, "the left cell's entry")?,
            exit: optional_symbol(index, exit, "the left cell's exit")?,
            adjustments: adjustments?,
        },
        seam: optional_symbol(index, seam, "the left's seam")?,
        extension,
    })
}

/// The JSON record spelling — [`settled_json`], which the replay's window memo files one record per line of — read back through [`parse_settled`], for the tests that hold a filed memo to the walk that wrote it. Nothing shipped reads that spelling here: its reader is `kernel_exec.settled_of_row`, on the Python side of the seam.
#[cfg(test)]
pub(crate) fn parse_settled_json(
    index: &SpecIndex,
    value: &serde_json::Value,
) -> Result<Settled, String> {
    let text = |value: &serde_json::Value, what: &str| -> Result<String, String> {
        if value.is_null() {
            return Ok(String::new());
        }
        value
            .as_str()
            .map(str::to_owned)
            .ok_or_else(|| format!("{what} is not a string"))
    };
    let raw = value
        .as_object()
        .ok_or("the settled record is not an object")?;
    let cell = raw
        .get("cell")
        .and_then(serde_json::Value::as_array)
        .ok_or("the record's cell is not an array")?;
    let [rune, stance, entry, exit, adjustments] = cell.as_slice() else {
        return Err(format!(
            "a cell is five fields, and this one has {}",
            cell.len()
        ));
    };
    let adjustments: Vec<String> = adjustments
        .as_array()
        .ok_or("the cell's adjustments are not an array")?
        .iter()
        .map(|token| text(token, "an adjustments token"))
        .collect::<Result<_, _>>()?;
    let seam = text(raw.get("seam").ok_or("no seam field")?, "the seam")?;
    let extension = raw
        .get("extension")
        .and_then(serde_json::Value::as_i64)
        .ok_or("the record's extension is not an integer")?;
    parse_settled(
        index,
        [
            &text(rune, "the cell's rune")?,
            &text(stance, "the cell's stance")?,
            &text(entry, "the cell's entry")?,
            &text(exit, "the cell's exit")?,
            &adjustments.join(","),
            &seam,
            &extension.to_string(),
        ],
    )
}

/// One adjustments token read back into the closed grammar, `model.parse_adjustment`'s refusals included. A left cell's adjustments are load-bearing in exactly one place: the trace memo collapses them away, but a stranded window's E-STRANDED sentence reads the left's whole `cell_label`, which spells every adjustment back out — so a token misread here would surface as a diverging message and nowhere else.
///
/// Three of this reader's refusals are knowingly stricter than `model.parse_adjustment`'s, and one normalization is knowingly looser; none of the four is reachable from a case line, whose tokens are whatever this kernel's own adjustment and withdrawal spellings wrote. Python reads the count with `int()`, which accepts underscore grouping (`en-ext-1_0`), surrounding whitespace, and non-ASCII decimal digits, where Rust's `i64` parse takes none of them; `+1` and leading zeros are the same number on both sides, so those are not divergences. Python's `bind` takes its argument as an arbitrary string, including the empty one `ex-bind-` yields, where this reader demands a name the spec interned — the same call the feature flags make, and for the same reason: a token naming a bitmap this spec never mentions is a case cut against another spec. And a count is *parsed* here rather than kept as text, so `en-ext-01` would re-spell as `en-ext-1` in a `cell_label` where Python's tuple of raw token strings prints it back verbatim.
fn parse_adjustment(index: &SpecIndex, token: &str) -> Result<AdjustmentToken, String> {
    if token == "locked" {
        return Ok(AdjustmentToken::Locked);
    }
    let unrecognized = || format!("unrecognized adjustments token: {token:?}");
    let (prefix, rest) = token.split_once('-').ok_or_else(unrecognized)?;
    let side = match prefix {
        "en" => Side::Entry,
        "ex" => Side::Exit,
        _ => return Err(unrecognized()),
    };
    if rest.is_empty() {
        return Err(unrecognized());
    }
    let (operation, argument) = rest.split_once('-').unwrap_or((rest, ""));
    if operation == "bind" {
        let bitmap = index
            .sym_of(argument)
            .ok_or_else(|| format!("{token} binds {argument}, which this spec never mentions"))?;
        return Ok(AdjustmentToken::Bind(side, bitmap));
    }
    let by: i64 = argument.parse().map_err(|_| unrecognized())?;
    match operation {
        "ext" => Ok(AdjustmentToken::Extend(side, by)),
        "con" => Ok(AdjustmentToken::Contract(side, by)),
        "trim" => Ok(AdjustmentToken::Trim(side, by)),
        _ => Err(unrecognized()),
    }
}

/// One raw slot: the kind spelling of a boundary or unknown slot, or else a rune name. The kind spellings are read first, so `letter` itself is refused — a letter slot spells its rune — and a name that is neither is a refusal rather than a slot.
fn parse_token(index: &SpecIndex, field: &str) -> Result<RightToken, String> {
    match TokenKind::from_text(field) {
        Some(TokenKind::Letter) => {
            Err("a right slot spells its rune name, not the kind `letter`".to_owned())
        }
        Some(kind) => {
            Ok(RightToken::of_kind(kind).expect("every kind but letter has a token of its own"))
        }
        None => Ok(RightToken::Letter(symbol(index, field, "a right slot")?)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::EngineModes;
    use crate::index::fixtures;

    fn replaying_engine(index: &SpecIndex) -> Engine<'_> {
        Engine::with_modes(
            index,
            Vec::new(),
            EngineModes {
                trace_memo: true,
                ..EngineModes::default()
            },
        )
    }

    /// One window over `fixtures::mini()`: the run edge on the left, `qsPea` under settlement, and a `qsTea` follower whose only entry at the height `qsPea` exits is unselectable — so the x-height exit is closed out and the cell settles unjoined.
    const UNJOINED: &str = "edge\t\t\t\t\t\t\t\tqsPea\tqsTea\tedge\tunknown\tunknown";

    /// The window that fills the ladder in: `qsTea` under settlement toward `qsPea`, where the x-height exit has no acceptor and the baseline one is refused by an authored record, so two stances survive exitless and the declared order settles it. Both flavors of elimination are here — one that names no record and one that names the refusal — and the surviving loser is the runner-up.
    const ORDERED: &str = "edge\t\t\t\t\t\t\t\tqsTea\tqsPea\tedge\tunknown\tunknown";

    /// The same follower behind a left that committed an x-height exit `qsTea` cannot accept — the stranded window, which the corpus buckets as `E-UNREACHABLE` and tells apart by its message.
    const STRANDED: &str =
        "letter\tqsPea\thalf\t\tx-height\t\tx-height\t0\tqsTea\tqsMay\tedge\tunknown\tunknown";

    fn answer(line: &str, shape: Answer) -> String {
        let index = fixtures::mini();
        let mut engine = replaying_engine(&index);
        let case = parse_case(&index, line).expect("the case parses");
        replay_case(&mut engine, &case, shape).expect("the case replays")
    }

    /// The answer alone: what follows the echoed question and its tab.
    fn result_of(line: &str, shape: Answer) -> String {
        let answered = answer(line, shape);
        answered
            .strip_prefix(line)
            .and_then(|rest| rest.strip_prefix('\t'))
            .expect("the question is echoed ahead of the answer")
            .to_owned()
    }

    /// The whole trace: the row-visible record and its delta, then the ladder that chose it — the deciding stage, the runner-up, every ranked survivor with its two scores, and the eliminations with their provenance. This window has one survivor, so the stage is `only-candidate` and there is no runner-up; the exit it did not get to keep is the elimination.
    #[test]
    fn a_settled_case_carries_the_record_the_delta_and_the_ladder_that_chose_it() {
        assert_eq!(
            result_of(UNJOINED, Answer::Trace),
            r#"{"settled":{"cell":["qsPea","half",null,null,[]],"seam":null,"extension":0},"prospect":0,"joint_floor":false,"notes":[],"fired":[],"decided_stage":"only-candidate","runner_up":null,"ranked":[[["half",null,null,0,9999],0,0]],"eliminations":[["lookahead-closure","qsPea.half: exit x-height has no refusal-aware acceptor cell on qsTea",null]]}"#
        );
    }

    /// The four ladder fields on a window that exercises all of them: the stage that decided, the survivor that lost to it, both ranked rungs with their join count and prospect, and the two eliminations in enumeration order — the second carrying the refusal's pointer, which is also what the delta and the notes report.
    #[test]
    fn the_ladder_carries_the_stage_the_runner_up_both_rungs_and_each_eliminations_provenance() {
        assert_eq!(
            result_of(ORDERED, Answer::Trace),
            r#"{"settled":{"cell":["qsTea","full",null,null,[]],"seam":null,"extension":0},"prospect":0,"joint_floor":false,"notes":["qsTea.yaml:policy.refuse[0]"],"fired":["qsTea.yaml:policy.refuse[0]"],"decided_stage":"order","runner_up":["half",null,null,2,9999],"ranked":[[["full",null,null,1,9999],0,0],[["half",null,null,2,9999],0,0]],"eliminations":[["lookahead-closure","qsTea.half: exit x-height has no refusal-aware acceptor cell on qsPea",null],["refuse","qsTea.half: exit baseline refused","qsTea.yaml:policy.refuse[0]"]]}"#
        );
    }

    /// The settled-only answer is the trace's own settled record as seven fields, a height empty where the trace spells `null`, and nothing of the ladder.
    #[test]
    fn a_settled_only_answer_is_the_records_seven_fields() {
        assert_eq!(
            result_of(UNJOINED, Answer::SettledOnly),
            "qsPea\thalf\t\t\t\t\t0"
        );
        assert_eq!(
            result_of(ORDERED, Answer::SettledOnly),
            "qsTea\tfull\t\t\t\t\t0"
        );
    }

    #[test]
    fn a_raising_case_carries_its_bucket_and_the_message_byte_for_byte() {
        let refusal = r#"{"raise":"E-UNREACHABLE","message":"E-STRANDED: qsPea.half.ex-y5 committed an exit at x-height but qsTea has no acceptor cell (the lookahead closure should have prevented this commitment)"}"#;
        assert_eq!(result_of(STRANDED, Answer::Trace), refusal);
        assert_eq!(
            result_of(STRANDED, Answer::SettledOnly),
            refusal,
            "a refusal is the same object under either answer shape"
        );
    }

    /// The two specificity raises are unauthored on today's live spec and on the mini one, so no sweep however large reaches this mapping — it is pinned here or nowhere.
    #[test]
    fn the_four_raise_kinds_bucket_into_the_corpuss_three() {
        assert_eq!(
            raise_text(&SettleError::Incomparable("neither dominates".to_owned())),
            r#"{"raise":"E-INCOMPARABLE","message":"neither dominates"}"#
        );
        assert_eq!(
            raise_text(&SettleError::Ambiguous("two left standing".to_owned())),
            r#"{"raise":"E-AMBIGUOUS","message":"two left standing"}"#
        );
        assert_eq!(
            raise_text(&SettleError::Stranded("nothing to settle into".to_owned())),
            r#"{"raise":"E-UNREACHABLE","message":"nothing to settle into"}"#
        );
        assert_eq!(
            raise_text(&SettleError::Plain("will not settle".to_owned())),
            r#"{"raise":"E-UNREACHABLE","message":"will not settle"}"#
        );
    }

    /// `json.dumps` under its default `ensure_ascii`, which is what the Python reader expects — and a tab or newline inside a message is escaped, so a refusal after the tab separator cannot desynchronize the batch.
    #[test]
    fn a_message_is_escaped_the_way_python_writes_it() {
        assert_eq!(
            raise_text(&SettleError::Plain(
                "\u{b7}Pea said \"no\"\tand \\left\n".to_owned()
            )),
            r#"{"raise":"E-UNREACHABLE","message":"\u00b7Pea said \"no\"\tand \\left\n"}"#
        );
    }

    #[test]
    fn the_question_is_echoed_byte_for_byte_ahead_of_the_answer() {
        for line in [UNJOINED, ORDERED, STRANDED] {
            for shape in [Answer::Trace, Answer::SettledOnly] {
                let answered = answer(line, shape);
                assert!(answered.starts_with(&format!("{line}\t")), "{answered}");
            }
        }
    }

    /// A field count other than thirteen is a line this reader cannot place: one short of a slot, and one with a field past the last slot, are both refused rather than read as far as they go.
    #[test]
    fn a_field_count_other_than_thirteen_is_refused() {
        let index = fixtures::mini();
        let short = "edge\t\t\t\t\t\t\t\tqsPea\tqsTea\tedge\tunknown";
        assert_eq!(
            parse_case(&index, short).expect_err("a window is four slots"),
            "a case is 13 tab-separated fields, and this line has 12"
        );
        let long = format!("{UNJOINED}\textra");
        assert_eq!(
            parse_case(&index, &long).expect_err("nothing rides past the last slot"),
            "a case is 13 tab-separated fields, and this line has 14"
        );
        assert_eq!(
            parse_case(&index, "").expect_err("an empty line is one empty field"),
            "a case is 13 tab-separated fields, and this line has 1"
        );
    }

    #[test]
    fn a_left_cells_adjustments_survive_the_round_trip() {
        let index = fixtures::mini();
        let mut engine = replaying_engine(&index);
        let line = "letter\tqsTea\thalf\tbaseline\tx-height\tlocked,en-ext-1,ex-bind-pulled-back,ex-trim-2,en-con-3\tx-height\t1\tqsPea\tedge\tunknown\tunknown\tunknown";
        let case = parse_case(&index, line).expect("the case parses");
        let left = case.left.settled.as_ref().expect("a letter left");
        assert_eq!(
            left.cell
                .adjustments
                .iter()
                .map(|token| crate::types::adjustment_text(&index, *token))
                .collect::<Vec<String>>(),
            [
                "locked",
                "en-ext-1",
                "ex-bind-pulled-back",
                "ex-trim-2",
                "en-con-3"
            ]
        );
        assert_eq!(
            index.resolve(left.cell.entry.expect("an entry")),
            "baseline"
        );
        assert_eq!(index.resolve(left.seam.expect("a seam")), "x-height");
        assert_eq!(left.extension, 1);
        let answered = replay_case(&mut engine, &case, Answer::Trace).expect("the case replays");
        assert!(answered.starts_with(&format!("{line}\t")));
    }

    /// The left's record is spelled in the same seven fields the settled-only answer is, so a settled-only answer can be pasted back in as the next question's left and read as the record it was.
    #[test]
    fn a_settled_only_answer_reads_back_as_a_lefts_record() {
        let index = fixtures::mini();
        let mut engine = replaying_engine(&index);
        let case = parse_case(&index, UNJOINED).expect("the case parses");
        let answered = result_text(&mut engine, &case, Answer::SettledOnly).expect("an answer");
        let fields: Vec<&str> = answered.split('\t').collect();
        let record: [&str; 7] = fields.as_slice().try_into().expect("seven fields");
        let settled = parse_settled(&index, record).expect("reads back");
        let trace = engine
            .transition_trace(&case.left, case.token, case.slots)
            .expect("settles");
        assert_eq!(settled, trace.settled);
    }

    #[test]
    fn a_head_line_is_skipped_and_every_case_after_it_is_answered() {
        let index = fixtures::mini();
        let mut engine = replaying_engine(&index);
        let text = format!("# ams-m1-cases\tdefault\n{UNJOINED}\n{STRANDED}\n");
        let lines =
            replay_cases(&mut engine, &text, Answer::SettledOnly).expect("the file replays");
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[0], format!("{UNJOINED}\tqsPea\thalf\t\t\t\t\t0"));
        assert!(lines[1].starts_with(&format!("{STRANDED}\t{{\"raise\":\"E-UNREACHABLE\"")));
    }

    /// Both shapes look the delta up, so the drift alarm survives the shape that reports no delta.
    #[test]
    fn a_missing_delta_says_the_memo_key_shapes_have_drifted() {
        let index = fixtures::mini();
        let mut engine = Engine::new(&index, Vec::new());
        let case = parse_case(&index, UNJOINED).expect("the case parses");
        for shape in [Answer::Trace, Answer::SettledOnly] {
            let complaint =
                replay_case(&mut engine, &case, shape).expect_err("no journal, no delta");
            assert!(
                complaint.contains("left no journaled fired delta"),
                "{complaint}"
            );
        }
    }

    #[test]
    fn a_name_the_spec_never_mentions_is_a_hard_error_and_not_an_answer() {
        let index = fixtures::mini();
        let line = UNJOINED.replace("qsPea", "qsZoo");
        let complaint = parse_case(&index, &line).expect_err("qsZoo is not in this spec");
        assert_eq!(
            complaint,
            "the input rune names qsZoo, which this spec never mentions"
        );
        let slot = UNJOINED.replace("qsTea", "qsZoo");
        assert_eq!(
            parse_case(&index, &slot).expect_err("a slot naming qsZoo is refused too"),
            "a right slot names qsZoo, which this spec never mentions"
        );
    }

    /// A slot spells its rune name or the kind of a boundary; the word `letter` is neither, and a left with no rune but a stance is a record this reader cannot place.
    #[test]
    fn a_slot_spelled_letter_and_a_half_spelled_left_are_refused() {
        let index = fixtures::mini();
        let slot = UNJOINED.replace("qsTea", "letter");
        assert!(
            parse_case(&index, &slot)
                .expect_err("letter names no rune")
                .contains("not the kind `letter`")
        );
        let half = "edge\t\thalf\t\t\t\t\t0\tqsPea\tqsTea\tedge\tunknown\tunknown";
        assert_eq!(
            parse_case(&index, half).expect_err("a stance without a rune"),
            "a left with no rune carries no record, and this one spells one"
        );
    }

    /// Every spelling below is one `model.parse_adjustment` refuses too; this reader also refuses three that it accepts, which is why the name claims a direction rather than an equivalence. See [`parse_adjustment`].
    #[test]
    fn a_token_outside_the_grammar_is_refused_and_a_well_formed_one_parses() {
        let index = fixtures::mini();
        for token in [
            "",
            "en",
            "en-",
            "up-ext-1",
            "en-ext-",
            "en-ext-x",
            "en-fold-1",
        ] {
            assert!(
                parse_adjustment(&index, token).is_err(),
                "{token} should not parse"
            );
        }
        assert_eq!(
            parse_adjustment(&index, "locked"),
            Ok(AdjustmentToken::Locked)
        );
        assert_eq!(
            parse_adjustment(&index, "ex-con-2"),
            Ok(AdjustmentToken::Contract(Side::Exit, 2))
        );
    }
}
