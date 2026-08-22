//! Case replay: one `ams-m1-corpus/3` case line in, the same line back out with this kernel's answer in it. The shape is this crate's own on both halves — `rebuild/pipeline/kernel_exec.py`'s `case_row` writes a question and its `trace_of` reads an answer's `result` back into a `settle.TransitionTrace` — and the whole re-emitted line is what lines a batch's answers up with its questions, which `kernel_exec._settle_cases` checks before a caller decodes any of them.
//!
//! Re-emitting the *whole* line rather than only the answer is what makes that check possible: an output line carries the question it answers, so a line the reader skipped, reordered or answered out of turn cannot pass as an answer to the question that was asked. The echo is a re-canonicalization of the input's own bytes rather than a spelling of the parsed model — key order is the input line's and a key this build does not know about rides through in its own place — so what proves the inputs were *understood* is not the echo but the reader's refusals: a name the spec never interned, a slot count that is not four, an adjustments token outside the grammar, and a missing `result` are all refusals rather than answers, so a misread case stops the run instead of diverging on the answer alone.
//!
//! What the result carries is the whole trace, not only the row-visible record: the settled cell, the prospect, the joint-floor flag, the notes and the fired delta, then the deciding stage, the runner-up, the ranked ladder and the eliminations. The last four are the route rather than the outcome, and they are what the explain panel and the review surface's explain view read — a window can land on the right cell by the wrong route, and the ladder is where that shows.
//!
//! The fired delta is the field no downstream artifact re-derives: a port that settles onto the right cell by the wrong route builds a table whose dead-policy gate reads live records as dead. It comes from the trace memo's journaled delta for this case's own key, which means a *missing* delta is not an empty one — it says this replay's key shape and the memo's have drifted apart, and it stops the run rather than answering.

use serde_json::{Map, Value};

use crate::emit::json_string;
use crate::engine::{Engine, Slots};
use crate::error::{SettleError, SettleErrorKind};
use crate::index::SpecIndex;
use crate::model::Sym;
use crate::types::{
    AdjustmentToken, Candidate, CellId, LeftContext, RightToken, Settled, Side, TokenKind,
    TransitionTrace, adjustment_text, provenance_pointer,
};

/// The corpus's three raise buckets. `E-UNREACHABLE` takes the stranded window and every plain settle error alike, which is why the message rides beside it — an identity alone cannot tell a stranded exit from a rune that is not modeled.
const RAISE_INCOMPARABLE: &str = "E-INCOMPARABLE";
const RAISE_AMBIGUOUS: &str = "E-AMBIGUOUS";
const RAISE_UNREACHABLE: &str = "E-UNREACHABLE";

/// The one key whose value this kernel replaces. Every other key rides through untouched.
const RESULT_KEY: &str = "result";

/// One corpus case: the window to settle, beside the raw object the answer is re-emitted into. The raw object is kept rather than reconstructed so that a key this build does not know about still lands in the output in its own place.
#[derive(Clone, Debug)]
pub struct Case {
    pub left: LeftContext,
    pub token: RightToken,
    pub slots: Slots,
    raw: Map<String, Value>,
}

/// Read one case line, `kernel_exec.case_row`'s inverse. A name the spec never interned is a hard error rather than a settlement outcome: the case was cut against some other spec, and answering it would compare two different questions.
pub fn parse_case(index: &SpecIndex, line: &str) -> Result<Case, String> {
    let value: Value =
        serde_json::from_str(line).map_err(|error| format!("not a case object: {error}"))?;
    let Value::Object(raw) = value else {
        return Err("not a case object: the line is not a JSON object".to_owned());
    };
    if !raw.contains_key(RESULT_KEY) {
        return Err("no result field to replace".to_owned());
    }
    let left = parse_left(index, field(&raw, "left")?)?;
    let token = RightToken::Letter(symbol(index, field(&raw, "input")?, "the input rune")?);
    let slots = parse_slots(index, field(&raw, "right")?)?;
    Ok(Case {
        left,
        token,
        slots,
        raw,
    })
}

/// One case's whole output line: the case as it arrived, with this kernel's answer in the `result` field.
pub fn replay_case(engine: &mut Engine<'_>, case: &Case) -> Result<String, String> {
    let result = result_text(engine, case)?;
    let mut out = String::from("{");
    for (key, value) in &case.raw {
        if out.len() > 1 {
            out.push(',');
        }
        out.push_str(&json_string(key));
        out.push(':');
        if key == RESULT_KEY {
            out.push_str(&result);
        } else {
            emit_value(&mut out, value)?;
        }
    }
    out.push('}');
    Ok(out)
}

/// A whole case file replayed through one engine in file order — the `settle-cases` verb's body. An optional leading `# ` marker line is the corpus head and is skipped rather than parsed: the modes a file was cut under reach this kernel as CLI flags, so the world a batch is answered in is the caller's word and never the file's.
///
/// The engine is shared across the file, so a batch settles warm. That costs the answers nothing: each memoized evaluation replays its journaled delta on every hit, precisely so a warm answer and a cold one agree down to the fired set.
pub fn replay_cases(engine: &mut Engine<'_>, text: &str) -> Result<Vec<String>, String> {
    let mut lines = Vec::new();
    for (seat, line) in text.lines().enumerate() {
        if seat == 0 && line.starts_with("# ") {
            continue;
        }
        let numbered = |complaint: String| format!("line {}: {complaint}", seat + 1);
        let case = parse_case(engine.index(), line).map_err(numbered)?;
        lines.push(replay_case(engine, &case).map_err(numbered)?);
    }
    Ok(lines)
}

/// This case's `result` value, in the shape `kernel_exec.trace_of` reads: the row-visible record with its fired delta, or the raise bucket with the message that came with it, which `settle.SettleError` carries as its `.bucket` and its own text.
fn result_text(engine: &mut Engine<'_>, case: &Case) -> Result<String, String> {
    let index = engine.index();
    let trace = match engine.transition_trace(&case.left, case.token, case.slots) {
        Ok(trace) => trace,
        Err(error) => return Ok(raise_text(&error)),
    };
    let Some(delta) = engine.trace_delta(&case.left, case.token, case.slots) else {
        return Err("the settled case left no journaled fired delta — the trace memo's key shape has moved and this replay's key must follow".to_owned());
    };
    let fired: Vec<String> = delta.iter().map(|pointer| pointer.text(index)).collect();
    Ok(settled_text(index, &trace, &fired))
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

/// The settled result, in the key order an answer is read in: the row-visible record and its fired delta first, then the four trace fields the corpus/3 bump added.
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

/// One candidate as the corpus spells it, and as `kernel_exec._candidate_of` reads it back into a `settle.Candidate`: the stance, its two heights, and the two indices the ranking and the floor sort on. A non-joining candidate carries the sentinel exit index's own value rather than a null, because what a reader wants is the sort key the ranking used — `settle._NO_EXIT_INDEX` is that value's Python spelling.
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

fn settled_json(index: &SpecIndex, settled: &Settled) -> String {
    let adjustments: Vec<String> = settled
        .cell
        .adjustments
        .iter()
        .map(|token| json_string(&adjustment_text(index, *token)))
        .collect();
    format!(
        "{{\"cell\":[{},{},{},{},[{}]],\"seam\":{},\"extension\":{}}}",
        json_string(index.resolve(settled.cell.rune)),
        json_string(index.resolve(settled.cell.stance)),
        height_json(index, settled.cell.entry),
        height_json(index, settled.cell.exit),
        adjustments.join(","),
        height_json(index, settled.seam),
        settled.extension
    )
}

fn height_json(index: &SpecIndex, height: Option<Sym>) -> String {
    match height {
        Some(height) => json_string(index.resolve(height)),
        None => "null".to_owned(),
    }
}

fn strings_json(values: &[String]) -> String {
    let quoted: Vec<String> = values.iter().map(|value| json_string(value)).collect();
    format!("[{}]", quoted.join(","))
}

/// One already-parsed JSON value in the canonical spelling — `json.dumps(value, separators=(",", ":"))`, which for the fields this re-emits means the bytes the question arrived in. Only integers occur: the corpus carries extensions, prospects and code points and no floats anywhere, so a number that is not one is a corpus this build does not understand rather than something to round-trip approximately.
fn emit_value(out: &mut String, value: &Value) -> Result<(), String> {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
        Value::Number(number) => {
            let Some(integer) = number.as_i64() else {
                return Err(format!("{number} is not an integer within i64"));
            };
            out.push_str(&integer.to_string());
        }
        Value::String(text) => out.push_str(&json_string(text)),
        Value::Array(items) => {
            out.push('[');
            for (seat, item) in items.iter().enumerate() {
                if seat > 0 {
                    out.push(',');
                }
                emit_value(out, item)?;
            }
            out.push(']');
        }
        Value::Object(entries) => {
            out.push('{');
            for (seat, (key, item)) in entries.iter().enumerate() {
                if seat > 0 {
                    out.push(',');
                }
                out.push_str(&json_string(key));
                out.push(':');
                emit_value(out, item)?;
            }
            out.push('}');
        }
    }
    Ok(())
}

fn field<'v>(raw: &'v Map<String, Value>, key: &str) -> Result<&'v Value, String> {
    raw.get(key).ok_or_else(|| format!("no {key} field"))
}

fn text<'v>(value: &'v Value, what: &str) -> Result<&'v str, String> {
    value
        .as_str()
        .ok_or_else(|| format!("{what} is not a string"))
}

fn symbol(index: &SpecIndex, value: &Value, what: &str) -> Result<Sym, String> {
    let name = text(value, what)?;
    index
        .sym_of(name)
        .ok_or_else(|| format!("{what} names {name}, which this spec never mentions"))
}

fn optional_symbol(index: &SpecIndex, value: &Value, what: &str) -> Result<Option<Sym>, String> {
    if value.is_null() {
        return Ok(None);
    }
    symbol(index, value, what).map(Some)
}

fn kind_of(value: &Value, what: &str) -> Result<TokenKind, String> {
    let name = text(value, what)?;
    TokenKind::from_text(name).ok_or_else(|| format!("{what} names no known kind: {name}"))
}

fn parse_left(index: &SpecIndex, value: &Value) -> Result<LeftContext, String> {
    let Value::Object(raw) = value else {
        return Err("the left is not an object".to_owned());
    };
    let kind = kind_of(field(raw, "kind")?, "the left's kind")?;
    let settled = field(raw, "settled")?;
    if settled.is_null() {
        return Ok(LeftContext {
            kind,
            settled: None,
        });
    }
    Ok(LeftContext {
        kind,
        settled: Some(parse_settled(index, settled)?),
    })
}

fn parse_settled(index: &SpecIndex, value: &Value) -> Result<Settled, String> {
    let Value::Object(raw) = value else {
        return Err("the left's settled triple is not an object".to_owned());
    };
    let cell = field(raw, "cell")?
        .as_array()
        .ok_or("the left's cell is not an array")?;
    let [rune, stance, entry, exit, adjustments] = cell.as_slice() else {
        return Err(format!(
            "a cell is five fields, and this one has {}",
            cell.len()
        ));
    };
    let adjustments = adjustments
        .as_array()
        .ok_or("the left cell's adjustments are not an array")?;
    let adjustments: Result<Vec<AdjustmentToken>, String> = adjustments
        .iter()
        .map(|token| parse_adjustment(index, text(token, "an adjustments token")?))
        .collect();
    let extension = field(raw, "extension")?
        .as_i64()
        .ok_or("the left's extension is not an integer")?;
    Ok(Settled {
        cell: CellId {
            rune: symbol(index, rune, "the left cell's rune")?,
            stance: symbol(index, stance, "the left cell's stance")?,
            entry: optional_symbol(index, entry, "the left cell's entry")?,
            exit: optional_symbol(index, exit, "the left cell's exit")?,
            adjustments: adjustments?,
        },
        seam: optional_symbol(index, field(raw, "seam")?, "the left's seam")?,
        extension,
    })
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

fn parse_slots(index: &SpecIndex, value: &Value) -> Result<Slots, String> {
    let slots = value.as_array().ok_or("the right slots are not an array")?;
    let [right1, right2, right3, right4] = slots.as_slice() else {
        return Err(format!(
            "a window reads four right slots, and this one names {}",
            slots.len()
        ));
    };
    Ok(Slots::new(
        parse_token(index, right1)?,
        parse_token(index, right2)?,
        parse_token(index, right3)?,
        parse_token(index, right4)?,
    ))
}

fn parse_token(index: &SpecIndex, value: &Value) -> Result<RightToken, String> {
    let Value::Object(raw) = value else {
        return Err("a right slot is not an object".to_owned());
    };
    let kind = kind_of(field(raw, "kind")?, "a right slot's kind")?;
    if kind == TokenKind::Letter {
        return Ok(RightToken::Letter(symbol(
            index,
            field(raw, "letter")?,
            "a right slot's letter",
        )?));
    }
    Ok(RightToken::of_kind(kind).expect("every kind but letter has a token of its own"))
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
    const UNJOINED: &str = r#"{"left":{"kind":"edge","settled":null},"input":"qsPea","right":[{"kind":"letter","letter":"qsTea"},{"kind":"edge","letter":null},{"kind":"unknown","letter":null},{"kind":"unknown","letter":null}],"result":{"replace":"me"}}"#;

    /// The window that fills the ladder in: `qsTea` under settlement toward `qsPea`, where the x-height exit has no acceptor and the baseline one is refused by an authored record, so two stances survive exitless and the declared order settles it. Both flavors of elimination are here — one that names no record and one that names the refusal — and the surviving loser is the runner-up.
    const ORDERED: &str = r#"{"left":{"kind":"edge","settled":null},"input":"qsTea","right":[{"kind":"letter","letter":"qsPea"},{"kind":"edge","letter":null},{"kind":"unknown","letter":null},{"kind":"unknown","letter":null}],"result":null}"#;

    /// The same follower behind a left that committed an x-height exit `qsTea` cannot accept — the stranded window, which the corpus buckets as `E-UNREACHABLE` and tells apart by its message.
    const STRANDED: &str = r#"{"left":{"kind":"letter","settled":{"cell":["qsPea","half",null,"x-height",[]],"seam":"x-height","extension":0}},"input":"qsTea","right":[{"kind":"letter","letter":"qsMay"},{"kind":"edge","letter":null},{"kind":"unknown","letter":null},{"kind":"unknown","letter":null}],"result":{"replace":"me"}}"#;

    fn answer(line: &str) -> String {
        let index = fixtures::mini();
        let mut engine = replaying_engine(&index);
        let case = parse_case(&index, line).expect("the case parses");
        replay_case(&mut engine, &case).expect("the case replays")
    }

    fn result_of(line: &str) -> String {
        let answered = answer(line);
        let (_, result) = answered
            .split_once(r#","result":"#)
            .expect("the result field is last");
        result
            .strip_suffix('}')
            .expect("the line closes")
            .to_owned()
    }

    /// The whole corpus/3 result: the row-visible record and its delta, then the ladder that chose it — the deciding stage, the runner-up, every ranked survivor with its two scores, and the eliminations with their provenance. This window has one survivor, so the stage is `only-candidate` and there is no runner-up; the exit it did not get to keep is the elimination.
    #[test]
    fn a_settled_case_carries_the_record_the_delta_and_the_ladder_that_chose_it() {
        assert_eq!(
            result_of(UNJOINED),
            r#"{"settled":{"cell":["qsPea","half",null,null,[]],"seam":null,"extension":0},"prospect":0,"joint_floor":false,"notes":[],"fired":[],"decided_stage":"only-candidate","runner_up":null,"ranked":[[["half",null,null,0,9999],0,0]],"eliminations":[["lookahead-closure","qsPea.half: exit x-height has no refusal-aware acceptor cell on qsTea",null]]}"#
        );
    }

    /// The four corpus/3 fields on a window that exercises all of them: the stage that decided, the survivor that lost to it, both ranked rungs with their join count and prospect, and the two eliminations in enumeration order — the second carrying the refusal's pointer, which is also what the delta and the notes report.
    #[test]
    fn the_ladder_carries_the_stage_the_runner_up_both_rungs_and_each_eliminations_provenance() {
        assert_eq!(
            result_of(ORDERED),
            r#"{"settled":{"cell":["qsTea","full",null,null,[]],"seam":null,"extension":0},"prospect":0,"joint_floor":false,"notes":["qsTea.yaml:policy.refuse[0]"],"fired":["qsTea.yaml:policy.refuse[0]"],"decided_stage":"order","runner_up":["half",null,null,2,9999],"ranked":[[["full",null,null,1,9999],0,0],[["half",null,null,2,9999],0,0]],"eliminations":[["lookahead-closure","qsTea.half: exit x-height has no refusal-aware acceptor cell on qsPea",null],["refuse","qsTea.half: exit baseline refused","qsTea.yaml:policy.refuse[0]"]]}"#
        );
    }

    #[test]
    fn a_raising_case_carries_its_bucket_and_the_message_byte_for_byte() {
        assert_eq!(
            result_of(STRANDED),
            r#"{"raise":"E-UNREACHABLE","message":"E-STRANDED: qsPea.half.ex-y5 committed an exit at x-height but qsTea has no acceptor cell (the lookahead closure should have prevented this commitment)"}"#
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

    /// `json.dumps` under its default `ensure_ascii`, which is what the corpus was written with.
    #[test]
    fn a_message_is_escaped_the_way_python_writes_it() {
        assert_eq!(
            raise_text(&SettleError::Plain(
                "\u{b7}Pea said \"no\"\tand \\left".to_owned()
            )),
            r#"{"raise":"E-UNREACHABLE","message":"\u00b7Pea said \"no\"\tand \\left"}"#
        );
    }

    #[test]
    fn everything_but_the_result_is_re_emitted_byte_for_byte() {
        for line in [UNJOINED, STRANDED] {
            let answered = answer(line);
            let (want, _) = line
                .split_once(r#","result":"#)
                .expect("the result field is last");
            let (got, _) = answered
                .split_once(r#","result":"#)
                .expect("the result field is last");
            assert_eq!(got, want);
        }
    }

    #[test]
    fn the_keys_ride_in_the_order_the_case_spelled_them() {
        let index = fixtures::mini();
        let mut engine = replaying_engine(&index);
        let reordered = r#"{"input":"qsPea","right":[{"letter":null,"kind":"edge"},{"kind":"unknown","letter":null},{"kind":"unknown","letter":null},{"kind":"unknown","letter":null}],"result":null,"left":{"settled":null,"kind":"edge"}}"#;
        let case = parse_case(&index, reordered).expect("the case parses");
        let answered = replay_case(&mut engine, &case).expect("the case replays");
        assert!(
            answered.starts_with(r#"{"input":"qsPea","right":[{"letter":null,"kind":"edge"},"#)
        );
        assert!(answered.ends_with(r#","left":{"settled":null,"kind":"edge"}}"#));
        assert!(answered.contains(r#","result":{"settled":"#));
    }

    #[test]
    fn a_left_cells_adjustments_survive_the_round_trip() {
        let index = fixtures::mini();
        let mut engine = replaying_engine(&index);
        let line = r#"{"left":{"kind":"letter","settled":{"cell":["qsTea","half","baseline","x-height",["locked","en-ext-1","ex-bind-pulled-back","ex-trim-2","en-con-3"]],"seam":"x-height","extension":1}},"input":"qsPea","right":[{"kind":"edge","letter":null},{"kind":"unknown","letter":null},{"kind":"unknown","letter":null},{"kind":"unknown","letter":null}],"result":null}"#;
        let case = parse_case(&index, line).expect("the case parses");
        let cell = &case.left.settled.as_ref().expect("a letter left").cell;
        assert_eq!(
            cell.adjustments
                .iter()
                .map(|token| adjustment_text(&index, *token))
                .collect::<Vec<String>>(),
            [
                "locked",
                "en-ext-1",
                "ex-bind-pulled-back",
                "ex-trim-2",
                "en-con-3"
            ]
        );
        let answered = replay_case(&mut engine, &case).expect("the case replays");
        let (prefix, _) = line.split_once(r#","result":"#).expect("a result field");
        assert!(answered.starts_with(prefix));
    }

    #[test]
    fn a_head_line_is_skipped_and_every_case_after_it_is_answered() {
        let index = fixtures::mini();
        let mut engine = replaying_engine(&index);
        let text =
            format!("# ams-m1-corpus/3\t{{\"config\":\"default\"}}\n{UNJOINED}\n{STRANDED}\n");
        let lines = replay_cases(&mut engine, &text).expect("the file replays");
        assert_eq!(lines.len(), 2);
        assert!(lines[0].contains(r#""result":{"settled":"#));
        assert!(lines[1].contains(r#""result":{"raise":"E-UNREACHABLE""#));
    }

    #[test]
    fn a_missing_delta_says_the_memo_key_shapes_have_drifted() {
        let index = fixtures::mini();
        let mut engine = Engine::new(&index, Vec::new());
        let case = parse_case(&index, UNJOINED).expect("the case parses");
        let complaint = replay_case(&mut engine, &case).expect_err("no journal, no delta");
        assert!(
            complaint.contains("left no journaled fired delta"),
            "{complaint}"
        );
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
    }

    #[test]
    fn a_case_with_no_result_field_is_refused_rather_than_answered() {
        let index = fixtures::mini();
        let line = r#"{"left":{"kind":"edge","settled":null},"input":"qsPea","right":[]}"#;
        assert_eq!(
            parse_case(&index, line).expect_err("nothing to replace"),
            "no result field to replace"
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

    #[test]
    fn a_slot_count_other_than_four_is_refused() {
        let index = fixtures::mini();
        let line = r#"{"left":{"kind":"edge","settled":null},"input":"qsPea","right":[{"kind":"edge","letter":null}],"result":null}"#;
        assert_eq!(
            parse_case(&index, line).expect_err("a window is four slots"),
            "a window reads four right slots, and this one names 1"
        );
    }
}
