//! Case replay for the `settle-cases` subcommand. Each input line is a tab-separated case line, and each output line is that case line, a tab, and this kernel's case result. `rebuild/pipeline/kernel_exec.py` writes case lines with `case_line` and reads case results with `trace_of` (JSON) or `_settled_of_fields` (tab-separated). `kernel_exec._settle_cases` checks that each output line starts with its own case line and a tab before it decodes that line's case result.
//!
//! The echo makes that check possible: a line the reader skipped, reordered, or settled out of turn cannot pass as the result of the case line that was sent. The echo is the input line's bytes unchanged, so it does not show that the input was understood. The parser's errors do that. A field count other than [`CASE_FIELDS`], a name the spec never interned, a kind name outside the six, an adjustments token outside the grammar, and a record field the parser cannot place are all errors, so a misread case stops the run instead of producing a wrong result.
//!
//! A case line has thirteen fields: the left's kind and its record (rune, stance, entry, exit, comma-joined adjustments, seam, extension; all seven empty for a left with no record, and a height or seam empty where there is none), then the rune being settled and the four raw slots after it. Each slot is a rune name or the kind name of a boundary or unknown slot. None of these values can contain a tab or a newline.
//!
//! The command line chooses one of two result shapes. The trace ([`CaseResult::Trace`]) is one JSON object: the settled cell, the prospect, the joint-floor flag, the notes, and the fired delta, then the deciding stage, the runner-up, the ranked candidates, and the eliminations. The last four record how the decision was reached, and `explain` and the review corpus's explain view read them: a window can settle on the right cell for the wrong reason, and the ranking shows that. The settled-only result ([`CaseResult::SettledOnly`]) is the record alone as seven tab-separated fields in the [`settled_fields`] format, for the conform walker, which keeps only outcomes. A settlement error is the same `{"raise":…,"message":…}` object in both shapes. In the settled-only shape a reader tells it from a record by its first byte, `{`; in the trace shape, by its `raise` key.
//!
//! The fired delta is the trace memo's journaled delta for this case's key. A missing delta is an error, not an empty list: it means this replay's key no longer matches the memo's key, or the engine has no trace memo. The settled-only result looks the delta up too without reporting it, so both shapes fail the same way.

use crate::emit::json_string;
use crate::engine::{Engine, Slots};
use crate::error::{SettleError, SettleErrorKind};
use crate::index::SpecIndex;
use crate::model::Sym;
use crate::types::{
    AdjustmentToken, Candidate, CellId, LeftContext, LeftOrdinals, RightToken, Settled, Side,
    TokenKind, TransitionTrace, height_json, provenance_pointer, settled_fields, settled_json,
};

/// The three raise buckets, the values `settle.SettleError.bucket` takes. `E-UNREACHABLE` covers both an unaccepted-exit window and every plain settle error, so the message is sent beside the bucket: the bucket alone cannot tell an unaccepted exit from a rune that is not modeled.
const RAISE_INCOMPARABLE: &str = "E-INCOMPARABLE";
const RAISE_AMBIGUOUS: &str = "E-AMBIGUOUS";
const RAISE_UNREACHABLE: &str = "E-UNREACHABLE";

/// How many tab-separated fields a case line is: the left's kind, its seven record fields, the input rune, and the four right slots.
pub const CASE_FIELDS: usize = 13;

/// Which case result a replay writes after the echoed case line.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CaseResult {
    /// The whole trace as one JSON object, the shape `kernel_exec.trace_of` reads.
    Trace,
    /// The settled record alone as seven tab-separated fields, the shape `kernel_exec._settled_of_fields` reads.
    SettledOnly,
}

/// One case: the window to settle and the case line its result follows. The line is kept as given so the echo is the caller's own bytes.
#[derive(Clone, Debug)]
pub struct Case<'l> {
    pub left: LeftContext,
    pub token: RightToken,
    pub slots: Slots,
    line: &'l str,
}

/// Reads one case line; the inverse of `kernel_exec.case_line`. A name the spec never interned is an error, not a settlement outcome: the case was written against a different spec, and settling it would settle a different window.
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
    let token = letter_of(index, input, "the input rune")?;
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

/// One case's output line: the case line as received, a tab, and this kernel's case result in the requested shape.
pub fn replay_case(
    engine: &mut Engine<'_>,
    case: &Case<'_>,
    shape: CaseResult,
) -> Result<String, String> {
    let result = result_text(engine, case, shape)?;
    Ok(format!("{}\t{result}", case.line))
}

/// Replays a whole case file through one engine in file order; the body of the `settle-cases` subcommand. A first line starting with `# ` is a head and is skipped: the modes a batch is settled under come from CLI flags, never from the file.
///
/// The engine is shared across the file, so later cases reuse its memo. This changes no case result: a memo hit replays the journaled fired delta, so a cached result and an uncached one have the same fired set.
pub fn replay_cases(
    engine: &mut Engine<'_>,
    text: &str,
    shape: CaseResult,
) -> Result<Vec<String>, String> {
    let mut lines = Vec::new();
    for (seat, line) in text.lines().enumerate() {
        if seat == 0 && line.starts_with("# ") {
            continue;
        }
        let numbered = |complaint: String| format!("line {}: {complaint}", seat + 1);
        let case = parse_case(engine.index(), line).map_err(numbered)?;
        lines.push(replay_case(engine, &case, shape).map_err(numbered)?);
    }
    Ok(lines)
}

/// This case's result. The trace shape gives the settled record with its fired delta and the ranking; the settled-only shape gives the record's seven fields. In either shape a settlement error gives its bucket and message, which `settle.SettleError` carries as `.bucket` and its text. The delta is looked up in both shapes because a missing delta is an error.
fn result_text(
    engine: &mut Engine<'_>,
    case: &Case<'_>,
    shape: CaseResult,
) -> Result<String, String> {
    let index = engine.index();
    let trace = match engine.transition_trace(&case.left, case.token, case.slots) {
        Ok(trace) => trace,
        Err(error) => return Ok(raise_text(&error)),
    };
    let Some(delta) = engine.trace_delta(&case.left, case.token, case.slots) else {
        return Err("the settled case left no journaled fired delta — the trace memo's key shape has moved and this replay's key must follow".to_owned());
    };
    match shape {
        CaseResult::SettledOnly => Ok(settled_fields(index, &trace.settled)),
        CaseResult::Trace => {
            let fired: Vec<String> = delta.iter().map(|pointer| pointer.text(index)).collect();
            Ok(settled_text(index, &trace, &fired))
        }
    }
}

fn raise_text(error: &SettleError) -> String {
    let bucket = match error.kind() {
        SettleErrorKind::Incomparable => RAISE_INCOMPARABLE,
        SettleErrorKind::Ambiguous => RAISE_AMBIGUOUS,
        SettleErrorKind::UnacceptedExit | SettleErrorKind::Plain => RAISE_UNREACHABLE,
    };
    format!(
        "{{\"raise\":{},\"message\":{}}}",
        json_string(bucket),
        json_string(error.message())
    )
}

/// The settled trace, in the key order a case result is read in: the stored row fields and their fired delta first, then the deciding stage, the runner-up, the ranked candidates and the eliminations.
fn settled_text(index: &SpecIndex, trace: &TransitionTrace, fired: &[String]) -> String {
    let ranking = trace.ranking();
    let runner_up = match &ranking.runner_up {
        Some(candidate) => candidate_json(index, candidate),
        None => "null".to_owned(),
    };
    let ranked: Vec<String> = ranking
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
    let eliminations: Vec<String> = ranking
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

/// One candidate as the trace writes it and `kernel_exec._candidate_of` reads it back into a `settle.Candidate`: the stance, its two heights, and the two indices the ranking and the floor sort on. A non-joining candidate carries the sentinel exit index (`settle._NO_EXIT_INDEX` in Python) instead of null, because the reader wants the sort key the ranking used.
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

/// The left's kind and its seven record fields. A left with no record has all seven empty. An empty rune beside a non-empty record field is an error.
fn parse_left(index: &SpecIndex, kind: &str, record: [&str; 7]) -> Result<LeftContext, String> {
    let kind = kind_of(kind, "the left's kind")?;
    if record[0].is_empty() {
        if record.iter().any(|field| !field.is_empty()) {
            return Err(
                "a left with no rune carries no record, and this one spells one".to_owned(),
            );
        }
        return Ok(LeftContext::boundary(kind));
    }
    let settled = parse_settled(index, record)?;
    if kind != TokenKind::Letter {
        return Ok(LeftContext {
            kind,
            settled: Some(settled),
            ordinals: LeftOrdinals::default(),
        });
    }
    let Some(ordinals) = LeftOrdinals::of(index, &settled) else {
        return Err(format!(
            "a letter left settles into a cell of a registered family in a declared stance at a height the spec offers, and {}.{} at {} does not",
            index.resolve(settled.cell.rune),
            index.resolve(settled.cell.stance),
            settled.seam.map_or("none", |seam| index.resolve(seam))
        ));
    };
    Ok(LeftContext {
        kind,
        settled: Some(settled),
        ordinals,
    })
}

/// The letter token a field names. It fails on a name the spec never interned and on a name that is not a registered family: case lines name only registry runes, because `settle.tokens_from_codepoints` refuses anything else before a line is written.
fn letter_of(index: &SpecIndex, field: &str, what: &str) -> Result<RightToken, String> {
    let name = symbol(index, field, what)?;
    index
        .letter(name)
        .ok_or_else(|| format!("{what} names no registered family: {field}"))
}

/// Reads the seven record fields (rune, stance, entry, exit, comma-joined adjustments, seam, extension) into a settled record. This is a case line's left, in the format [`settled_fields`] writes.
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

/// Reads the JSON record format of [`settled_json`] through [`parse_settled`]. The replay's window memo writes one such record per line, and tests use this to check a written memo against the walk that wrote it. No shipped code in the crate reads this format; `kernel_exec.settled_of_row` reads it in Python.
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

/// Reads one adjustments token into the closed grammar, refusing everything `model.parse_adjustment` refuses. A left cell's adjustments affect only one output: the trace memo ignores them, but an unaccepted-exit window's E-UNACCEPTED-EXIT message includes the left's full `cell_label`, which lists every adjustment. A misread token here would show up only as a different message.
///
/// This reader differs from `model.parse_adjustment` in ways no case line reaches, because a case line's tokens are ones this kernel's own adjustment and withdrawal formatting wrote. Python reads the count with `int()`, which accepts underscore grouping (`en-ext-1_0`), surrounding whitespace, and non-ASCII decimal digits; Rust's `i64` parse refuses all three. `+1` and leading zeros parse the same on both sides. Python's `bind` accepts any string, including the empty one `ex-bind-` yields; this reader requires a name the spec interned, the same check the feature flags make, because a token naming a bitmap the spec never mentions means the case was written against another spec. Finally, the count is parsed here, not kept as text, so `en-ext-01` is written back as `en-ext-1` in a `cell_label`, where Python's tuple of raw token strings prints it unchanged.
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

/// One raw slot: the kind name of a boundary or unknown slot, or else a rune name. Kind names are checked first, so `letter` is refused (a letter slot names its rune), and a name that is neither is an error.
fn parse_token(index: &SpecIndex, field: &str) -> Result<RightToken, String> {
    match TokenKind::from_text(field) {
        Some(TokenKind::Letter) => {
            Err("a right slot spells its rune name, not the kind `letter`".to_owned())
        }
        Some(kind) => {
            Ok(RightToken::of_kind(kind).expect("every kind but letter has a token of its own"))
        }
        None => letter_of(index, field, "a right slot"),
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

    /// One window over `fixtures::mini()`: the run edge on the left, `qsPea` being settled, and a `qsTea` follower whose only entry at `qsPea`'s exit height cannot be selected, so the x-height exit is eliminated and the cell settles unjoined.
    const UNJOINED: &str = "edge\t\t\t\t\t\t\t\tqsPea\tqsTea\tedge\tunknown\tunknown";

    /// The window that fills in the ranking: `qsTea` being settled before `qsPea`. The x-height exit has no acceptor and an authored record refuses the baseline exit, so two stances survive with no exit and the declared order decides. It has both kinds of elimination, one naming no record and one naming the refusal, and the losing survivor is the runner-up.
    const ORDERED: &str = "edge\t\t\t\t\t\t\t\tqsTea\tqsPea\tedge\tunknown\tunknown";

    /// The same follower after a left that committed an x-height exit `qsTea` cannot accept. This is the unaccepted-exit window, which goes in the `E-UNREACHABLE` bucket and is told apart by its message.
    const UNACCEPTED_EXIT: &str =
        "letter\tqsPea\thalf\t\tx-height\t\tx-height\t0\tqsTea\tqsMay\tedge\tunknown\tunknown";

    fn output_line(line: &str, shape: CaseResult) -> String {
        let index = fixtures::mini();
        let mut engine = replaying_engine(&index);
        let case = parse_case(&index, line).expect("the case parses");
        replay_case(&mut engine, &case, shape).expect("the case replays")
    }

    /// The case result alone: the text after the echoed case line and its tab.
    fn result_of(line: &str, shape: CaseResult) -> String {
        let output = output_line(line, shape);
        output
            .strip_prefix(line)
            .and_then(|rest| rest.strip_prefix('\t'))
            .expect("the case line is echoed ahead of the case result")
            .to_owned()
    }

    /// The full trace: the settled record and its delta, then the ranking: the deciding stage, the runner-up, every ranked survivor with its two scores, and the eliminations with their provenance. This window has one survivor, so the stage is `only-candidate` and there is no runner-up. The elimination is the exit the survivor lost.
    #[test]
    fn a_settled_case_carries_the_record_the_delta_and_the_ranking_that_chose_it() {
        assert_eq!(
            result_of(UNJOINED, CaseResult::Trace),
            r#"{"settled":{"cell":["qsPea","half",null,null,[]],"seam":null,"extension":0},"prospect":0,"joint_floor":false,"notes":[],"fired":[],"decided_stage":"only-candidate","runner_up":null,"ranked":[[["half",null,null,0,9999],0,0]],"eliminations":[["lookahead-closure","qsPea.half: exit x-height has no refusal-aware acceptor cell on qsTea",null]]}"#
        );
    }

    /// The four ranking fields on a window that fills all of them: the deciding stage, the survivor that lost, both ranked entries with their join count and prospect, and the two eliminations in enumeration order. The second elimination carries the refusal's pointer, which the delta and the notes also report.
    #[test]
    fn the_ranking_carries_the_stage_the_runner_up_both_ranked_entries_and_each_eliminations_provenance()
     {
        assert_eq!(
            result_of(ORDERED, CaseResult::Trace),
            r#"{"settled":{"cell":["qsTea","full",null,null,[]],"seam":null,"extension":0},"prospect":0,"joint_floor":false,"notes":["qsTea.yaml:policy.refuse[0]"],"fired":["qsTea.yaml:policy.refuse[0]"],"decided_stage":"order","runner_up":["half",null,null,2,9999],"ranked":[[["full",null,null,1,9999],0,0],[["half",null,null,2,9999],0,0]],"eliminations":[["lookahead-closure","qsTea.half: exit x-height has no refusal-aware acceptor cell on qsPea",null],["refuse","qsTea.half: exit baseline refused","qsTea.yaml:policy.refuse[0]"]]}"#
        );
    }

    /// The settled-only case result is the trace's settled record as seven fields, with an empty height where the trace has `null`, and no ranking.
    #[test]
    fn a_settled_only_case_result_is_the_records_seven_fields() {
        assert_eq!(
            result_of(UNJOINED, CaseResult::SettledOnly),
            "qsPea\thalf\t\t\t\t\t0"
        );
        assert_eq!(
            result_of(ORDERED, CaseResult::SettledOnly),
            "qsTea\tfull\t\t\t\t\t0"
        );
    }

    #[test]
    fn a_raising_case_carries_its_bucket_and_the_message_byte_for_byte() {
        let refusal = r#"{"raise":"E-UNREACHABLE","message":"E-UNACCEPTED-EXIT: qsPea.half.ex-y5 committed an exit at x-height but qsTea has no acceptor cell (the lookahead closure should have prevented this commitment)"}"#;
        assert_eq!(result_of(UNACCEPTED_EXIT, CaseResult::Trace), refusal);
        assert_eq!(
            result_of(UNACCEPTED_EXIT, CaseResult::SettledOnly),
            refusal,
            "a refusal is the same object under either result shape"
        );
    }

    /// Neither the live spec nor the mini spec produces either specificity raise (`E-INCOMPARABLE`, `E-AMBIGUOUS`), so no sweep reaches this mapping and only this test checks it.
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
            raise_text(&SettleError::UnacceptedExit(
                "nothing to settle into".to_owned()
            )),
            r#"{"raise":"E-UNREACHABLE","message":"nothing to settle into"}"#
        );
        assert_eq!(
            raise_text(&SettleError::Plain("will not settle".to_owned())),
            r#"{"raise":"E-UNREACHABLE","message":"will not settle"}"#
        );
    }

    /// Messages are escaped as `json.dumps` escapes them under its default `ensure_ascii`, which the Python reader expects. A tab or newline inside a message is escaped, so a refusal after the tab separator cannot break the batch's line structure.
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
    fn the_case_line_is_echoed_byte_for_byte_ahead_of_the_case_result() {
        for line in [UNJOINED, ORDERED, UNACCEPTED_EXIT] {
            for shape in [CaseResult::Trace, CaseResult::SettledOnly] {
                let output = output_line(line, shape);
                assert!(output.starts_with(&format!("{line}\t")), "{output}");
            }
        }
    }

    /// A field count other than thirteen is an error: a line one slot short and a line with a field past the last slot are both refused, not read as far as they go.
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
        let output = replay_case(&mut engine, &case, CaseResult::Trace).expect("the case replays");
        assert!(output.starts_with(&format!("{line}\t")));
    }

    /// The left's record uses the same seven fields as the settled-only case result, so a settled-only result can be used as the next case line's left and reads back as the same record.
    #[test]
    fn a_settled_only_case_result_reads_back_as_a_lefts_record() {
        let index = fixtures::mini();
        let mut engine = replaying_engine(&index);
        let case = parse_case(&index, UNJOINED).expect("the case parses");
        let result =
            result_text(&mut engine, &case, CaseResult::SettledOnly).expect("a case result");
        let fields: Vec<&str> = result.split('\t').collect();
        let record: [&str; 7] = fields.as_slice().try_into().expect("seven fields");
        let settled = parse_settled(&index, record).expect("reads back");
        let trace = engine
            .transition_trace(&case.left, case.token, case.slots)
            .expect("settles");
        assert_eq!(settled, trace.settled);
    }

    #[test]
    fn a_head_line_is_skipped_and_every_case_after_it_is_settled() {
        let index = fixtures::mini();
        let mut engine = replaying_engine(&index);
        let text = format!("# ams-m1-cases\tdefault\n{UNJOINED}\n{UNACCEPTED_EXIT}\n");
        let lines =
            replay_cases(&mut engine, &text, CaseResult::SettledOnly).expect("the file replays");
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[0], format!("{UNJOINED}\tqsPea\thalf\t\t\t\t\t0"));
        assert!(lines[1].starts_with(&format!("{UNACCEPTED_EXIT}\t{{\"raise\":\"E-UNREACHABLE\"")));
    }

    /// Both shapes look the delta up, so the settled-only shape, which reports no delta, still fails when the delta is missing.
    #[test]
    fn a_missing_delta_says_the_memo_key_shapes_have_drifted() {
        let index = fixtures::mini();
        let mut engine = Engine::new(&index, Vec::new());
        let case = parse_case(&index, UNJOINED).expect("the case parses");
        for shape in [CaseResult::Trace, CaseResult::SettledOnly] {
            let complaint =
                replay_case(&mut engine, &case, shape).expect_err("no journal, no delta");
            assert!(
                complaint.contains("left no journaled fired delta"),
                "{complaint}"
            );
        }
    }

    #[test]
    fn a_name_the_spec_never_mentions_is_a_hard_error_and_not_a_case_result() {
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

    /// A slot names its rune or the kind of a boundary. The word `letter` is neither, and a left with a stance but no rune cannot be read.
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

    /// `model.parse_adjustment` refuses every token below too. This reader also refuses some tokens that `model.parse_adjustment` accepts, so the test name states only one direction. See [`parse_adjustment`].
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
