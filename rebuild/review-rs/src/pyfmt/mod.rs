//! `PyValue`, the crate's model of a Python value, and the typed JSON encoding the stage dumps and the init's vocabulary carry it in (`units_exec.encode` and `decode` are the Python half).
//!
//! The encoding writes an int, a string, a boolean or None as itself, and every other value as a one-key object: `{"tuple": [...]}`, `{"list": [...]}`, `{"dict": [[key, value], ...]}` in insertion order, `{"bytes": hex}`, `{"set": [...]}` with its members sorted by their encoded text, and `{"record": [class name, [[field, value], ...]]}` for a dataclass or named tuple. There is no float, in the model or on the wire.
//!
//! The submodules write a value as Python writes it: [`repr`] as CPython's `repr`, [`json`] as `json.dumps` in every form the worker and the corpus build write, and reads one as `json.loads` does, and [`yaml`] as PyYAML's flow dump of a policy record, resting on the pinned CPython's `str` predicates ([`is_printable`], [`is_space`], [`is_digit`]) from the generated [`unicode_tables`]. [`stage_pyfmt`] and [`stage_yaml`] are the `pyfmt` and `yaml` stages of the stage fixtures, and [`stage_json_forms`] the corpus build's `json-forms` stage.

pub mod json;
pub mod repr;
pub mod unicode_tables;
pub mod yaml;

use std::fmt;
use std::io::{BufRead, Write};
use std::path::Path;

use ams_m1_kernel::emit::json_string;
use serde_json::Value;

/// A value a Python writer would refuse, or a `pyfmt` stage input that cannot be read or answered.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PyFmtError(pub String);

impl fmt::Display for PyFmtError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for PyFmtError {}

/// CPython's default `sys.get_int_max_str_digits()`: `int` and `json.loads` refuse a decimal literal of more digits, sign and underscores aside, with [`int_digits_exceeded`]'s text.
pub const INT_MAX_STR_DIGITS: usize = 4300;

/// The `ValueError` text CPython raises for a decimal literal of `digits` digits, past [`INT_MAX_STR_DIGITS`].
pub fn int_digits_exceeded(digits: usize) -> String {
    format!(
        "Exceeds the limit ({INT_MAX_STR_DIGITS} digits) for integer string conversion: value has {digits} digits; use sys.set_int_max_str_digits() to increase the limit"
    )
}

fn in_ranges(ranges: &[(u32, u32)], letter: char) -> bool {
    let point = u32::from(letter);
    let index = ranges.partition_point(|&(_, end)| end < point);
    ranges.get(index).is_some_and(|&(start, _)| start <= point)
}

/// `str.isprintable` for one character, as the pinned CPython answers it.
pub fn is_printable(letter: char) -> bool {
    in_ranges(unicode_tables::PRINTABLE, letter)
}

/// `str.isspace` for one character, as the pinned CPython answers it; it differs from [`char::is_whitespace`] on several control characters.
pub fn is_space(letter: char) -> bool {
    in_ranges(unicode_tables::SPACE, letter)
}

/// `str.isdigit` for one character, as the pinned CPython answers it.
pub fn is_digit(letter: char) -> bool {
    in_ranges(unicode_tables::DIGIT, letter)
}

/// The digit value of a character `str.isdecimal` accepts, as `int` reads a digit of any script, or None for any other character. Each range of [`unicode_tables::DECIMAL`] is whole runs of ten digits from zero, which the generator checks.
pub fn decimal_value(letter: char) -> Option<u32> {
    let point = u32::from(letter);
    let ranges = unicode_tables::DECIMAL;
    let index = ranges.partition_point(|&(_, end)| end < point);
    ranges
        .get(index)
        .filter(|&&(start, _)| start <= point)
        .map(|&(start, _)| (point - start) % 10)
}

/// One Python value. A set holds its members in the order they were given; [`PyValue::to_typed`] sorts them as `units_exec.encode` does.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PyValue {
    None,
    Bool(bool),
    Int(i64),
    Str(String),
    Bytes(Vec<u8>),
    Tuple(Vec<PyValue>),
    List(Vec<PyValue>),
    Dict(Vec<(PyValue, PyValue)>),
    Set(Vec<PyValue>),
    Record(String, Vec<(String, PyValue)>),
}

impl PyValue {
    /// A `str`.
    pub fn str(text: impl Into<String>) -> Self {
        Self::Str(text.into())
    }

    /// A `str`, or None when absent.
    pub fn optional_str(text: Option<&str>) -> Self {
        text.map_or(Self::None, Self::str)
    }

    /// A tuple of `str`.
    pub fn str_tuple<S: AsRef<str>>(items: &[S]) -> Self {
        Self::Tuple(items.iter().map(|item| Self::str(item.as_ref())).collect())
    }

    /// The value's Python type, as an error message names it.
    pub fn kind(&self) -> &'static str {
        match self {
            Self::None => "None",
            Self::Bool(_) => "a bool",
            Self::Int(_) => "an int",
            Self::Str(_) => "a str",
            Self::Bytes(_) => "bytes",
            Self::Tuple(_) => "a tuple",
            Self::List(_) => "a list",
            Self::Dict(_) => "a dict",
            Self::Set(_) => "a set",
            Self::Record(..) => "a record",
        }
    }

    /// Read one value from the typed encoding, refusing anything `units_exec.encode` never writes.
    pub fn from_typed(data: &Value) -> Result<Self, String> {
        match data {
            Value::Null => Ok(Self::None),
            Value::Bool(flag) => Ok(Self::Bool(*flag)),
            Value::Number(number) => number.as_i64().map(Self::Int).ok_or_else(|| {
                format!("not a typed value: {number} is not an integer that fits 64 bits")
            }),
            Value::String(text) => Ok(Self::Str(text.clone())),
            Value::Object(map) if map.len() == 1 => {
                let (tag, body) = map.iter().next().expect("one entry");
                match (tag.as_str(), body) {
                    ("tuple", Value::Array(items)) => Ok(Self::Tuple(Self::all(items)?)),
                    ("list", Value::Array(items)) => Ok(Self::List(Self::all(items)?)),
                    ("set", Value::Array(items)) => Ok(Self::Set(Self::all(items)?)),
                    ("dict", Value::Array(entries)) => entries
                        .iter()
                        .map(|entry| match entry {
                            Value::Array(pair) if pair.len() == 2 => {
                                Ok((Self::from_typed(&pair[0])?, Self::from_typed(&pair[1])?))
                            }
                            _ => Err(format!("not a typed dict entry: {entry}")),
                        })
                        .collect::<Result<_, _>>()
                        .map(Self::Dict),
                    ("bytes", Value::String(hex)) => decode_hex(hex).map(Self::Bytes),
                    ("record", Value::Array(parts)) if parts.len() == 2 => {
                        let Value::String(name) = &parts[0] else {
                            return Err(format!("not a typed record name: {}", parts[0]));
                        };
                        let Value::Array(fields) = &parts[1] else {
                            return Err(format!("not a typed record's fields: {}", parts[1]));
                        };
                        let fields = fields
                            .iter()
                            .map(|field| match field {
                                Value::Array(pair) if pair.len() == 2 => match &pair[0] {
                                    Value::String(field_name) => {
                                        Ok((field_name.clone(), Self::from_typed(&pair[1])?))
                                    }
                                    other => Err(format!("not a typed field name: {other}")),
                                },
                                _ => Err(format!("not a typed record field: {field}")),
                            })
                            .collect::<Result<_, _>>()?;
                        Ok(Self::Record(name.clone(), fields))
                    }
                    _ => Err(format!("not a typed value: {data}")),
                }
            }
            _ => Err(format!("not a typed value: {data}")),
        }
    }

    fn all(items: &[Value]) -> Result<Vec<Self>, String> {
        items.iter().map(Self::from_typed).collect()
    }

    /// The typed encoding's text, compact and ASCII-only as `units_exec.compact` writes it, with a set's members sorted by their own text.
    pub fn to_typed(&self) -> String {
        let mut out = String::new();
        self.write_typed(&mut out);
        out
    }

    fn write_typed(&self, out: &mut String) {
        match self {
            Self::None => out.push_str("null"),
            Self::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
            Self::Int(number) => out.push_str(&number.to_string()),
            Self::Str(text) => out.push_str(&json_string(text)),
            Self::Bytes(bytes) => {
                out.push_str("{\"bytes\":\"");
                for byte in bytes {
                    out.push_str(&format!("{byte:02x}"));
                }
                out.push_str("\"}");
            }
            Self::Tuple(items) => {
                Self::write_sequence(out, "tuple", items.iter().map(Self::to_typed))
            }
            Self::List(items) => {
                Self::write_sequence(out, "list", items.iter().map(Self::to_typed))
            }
            Self::Set(items) => {
                let mut members: Vec<String> = items.iter().map(Self::to_typed).collect();
                members.sort();
                Self::write_sequence(out, "set", members.into_iter());
            }
            Self::Dict(entries) => Self::write_sequence(
                out,
                "dict",
                entries
                    .iter()
                    .map(|(key, value)| format!("[{},{}]", key.to_typed(), value.to_typed())),
            ),
            Self::Record(name, fields) => {
                out.push_str("{\"record\":[");
                out.push_str(&json_string(name));
                out.push_str(",[");
                for (index, (field, value)) in fields.iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    out.push('[');
                    out.push_str(&json_string(field));
                    out.push(',');
                    value.write_typed(out);
                    out.push(']');
                }
                out.push_str("]]}");
            }
        }
    }

    fn write_sequence(out: &mut String, tag: &str, items: impl Iterator<Item = String>) {
        out.push_str("{\"");
        out.push_str(tag);
        out.push_str("\":[");
        for (index, item) in items.enumerate() {
            if index > 0 {
                out.push(',');
            }
            out.push_str(&item);
        }
        out.push_str("]}");
    }
}

/// The `pyfmt` stage: for each line of `stage_file`, write the line's `input` in the form its `format` names (`repr`, `json-sorted` or `json-fragment`) as one `{"key", "value"}` line, the value a `str` in the typed encoding.
pub fn stage_pyfmt(stage_file: &Path, out: &mut impl Write) -> Result<(), PyFmtError> {
    answer_lines(stage_file, out, |record, input| {
        match record["format"].as_str() {
            Some("repr") => repr::repr(input),
            Some("json-sorted") => json::dumps_sorted(input),
            Some("json-fragment") => json::fragment_text(input),
            Some(other) => Err(PyFmtError(format!("no pyfmt format {other:?}"))),
            None => Err(PyFmtError("the line has no format".into())),
        }
    })
}

/// The `yaml` stage: for each line of `stage_file`, write the line's `input` as `Drafter.draft_policy` ships a policy record ([`yaml::flow_dump`]) in one `{"key", "value"}` line, the value a `str` in the typed encoding.
pub fn stage_yaml(stage_file: &Path, out: &mut impl Write) -> Result<(), PyFmtError> {
    answer_lines(stage_file, out, |_record, input| yaml::flow_dump(input))
}

/// The `json-forms` stage: for each line of `stage_file`, answer the line's `input` in the form its `form` names in one `{"key", "value"}` line, the value in the typed encoding. A writer's form (`default`, `indent`, `sorted`, `unicode` or `compact`, [`json`]'s entry points) answers `("text", text)`; `loads` reads a `str` input with [`json::loads`] and a `bytes` one with [`json::loads_bytes`] and answers `("value", value)`. Either answers `("refused",)` where the writer or the reader refuses the input.
pub fn stage_json_forms(stage_file: &Path, out: &mut impl Write) -> Result<(), PyFmtError> {
    answer_values(stage_file, out, |record, input| {
        let form = record["form"]
            .as_str()
            .ok_or_else(|| PyFmtError("the line has no form".into()))?;
        let answer = match form {
            "default" => json::dumps(input).map(PyValue::Str),
            "indent" => json::dumps_indent(input).map(PyValue::Str),
            "sorted" => json::dumps_sorted(input).map(PyValue::Str),
            "unicode" => json::dumps_unicode(input).map(PyValue::Str),
            "compact" => json::dumps_compact(input).map(PyValue::Str),
            "loads" => match input {
                PyValue::Str(text) => json::loads(text),
                PyValue::Bytes(data) => json::loads_bytes(data),
                other => {
                    return Err(PyFmtError(format!(
                        "json.loads reads a str or bytes, not {}",
                        other.kind()
                    )));
                }
            },
            other => return Err(PyFmtError(format!("no JSON form {other:?}"))),
        };
        let tag = if form == "loads" { "value" } else { "text" };
        Ok(match answer {
            Ok(value) => PyValue::Tuple(vec![PyValue::str(tag), value]),
            Err(_) => PyValue::Tuple(vec![PyValue::str("refused")]),
        })
    })
}

/// Read each line of a stage file, decode its `input` from the typed encoding, and write `answer`'s text for it under the line's key.
fn answer_lines(
    stage_file: &Path,
    out: &mut impl Write,
    answer: impl Fn(&Value, &PyValue) -> Result<String, PyFmtError>,
) -> Result<(), PyFmtError> {
    answer_values(stage_file, out, |record, input| {
        answer(record, input).map(PyValue::Str)
    })
}

/// Read each line of a stage file, decode its `input` from the typed encoding, and write `answer`'s value for it, in the typed encoding, under the line's key.
fn answer_values(
    stage_file: &Path,
    out: &mut impl Write,
    answer: impl Fn(&Value, &PyValue) -> Result<PyValue, PyFmtError>,
) -> Result<(), PyFmtError> {
    let file = std::fs::File::open(stage_file).map_err(|failure| {
        PyFmtError(format!("cannot read {}: {failure}", stage_file.display()))
    })?;
    for line in std::io::BufReader::new(file).lines() {
        let line = line.map_err(|failure| {
            PyFmtError(format!("cannot read {}: {failure}", stage_file.display()))
        })?;
        let record: Value = serde_json::from_str(&line)
            .map_err(|failure| PyFmtError(format!("a stage line is not JSON: {failure}")))?;
        let Some(key) = record["key"].as_str() else {
            return Err(PyFmtError(format!(
                "a stage line has no key: {:.200}",
                line
            )));
        };
        let input = PyValue::from_typed(&record["input"])
            .map_err(|failure| PyFmtError(format!("{key}: {failure}")))?;
        let value =
            answer(&record, &input).map_err(|failure| PyFmtError(format!("{key}: {failure}")))?;
        writeln!(
            out,
            "{{\"key\":{},\"value\":{}}}",
            json_string(key),
            value.to_typed()
        )
        .map_err(|failure| PyFmtError(format!("cannot write a stage line: {failure}")))?;
    }
    Ok(())
}

fn decode_hex(hex: &str) -> Result<Vec<u8>, String> {
    if !hex.len().is_multiple_of(2) || !hex.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(format!("not typed bytes: {hex:?}"));
    }
    (0..hex.len())
        .step_by(2)
        .map(|at| u8::from_str_radix(&hex[at..at + 2], 16).map_err(|error| error.to_string()))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn round_trip(text: &str) -> String {
        let data: Value = serde_json::from_str(text).expect("JSON");
        PyValue::from_typed(&data).expect("typed").to_typed()
    }

    #[test]
    fn every_variant_round_trips_through_the_typed_encoding() {
        let text = r#"{"record":["Unit",[["a",{"tuple":[1,-2,null,true]}],["b",{"list":["x\u00e9"]}],["c",{"dict":[[{"tuple":["k",1]},{"bytes":"00ff"}]]}],["d",{"set":["a","b"]}]]]}"#;
        assert_eq!(round_trip(text), text);
    }

    #[test]
    fn a_set_is_written_with_its_members_sorted_by_their_text() {
        assert_eq!(
            round_trip(r#"{"set":["b",{"tuple":[]},"a"]}"#),
            r#"{"set":["a","b",{"tuple":[]}]}"#
        );
    }

    #[test]
    fn a_float_and_an_unknown_tag_are_refused() {
        for text in [
            r#"1.5"#,
            r#"{"frozenset":[]}"#,
            r#"{"tuple":[],"list":[]}"#,
            r#"{"bytes":"0"}"#,
        ] {
            let data: Value = serde_json::from_str(text).expect("JSON");
            assert!(PyValue::from_typed(&data).is_err(), "{text} was accepted");
        }
    }

    #[test]
    fn the_predicates_answer_as_cpython_where_rust_differs() {
        assert!(is_space('\u{1c}') && !'\u{1c}'.is_whitespace());
        assert!(!is_space('\u{200b}') && is_space('\u{3000}'));
        assert!(is_digit('\u{b2}') && !is_digit('\u{bd}'));
        assert!(is_printable(' ') && !is_printable('\u{a0}') && !is_printable('\u{e650}'));
        assert!(is_printable('\u{1f600}') && !is_printable('\u{10ffff}'));
    }

    #[test]
    fn each_table_holds_disjoint_ascending_ranges_with_gaps_between_them() {
        for table in [
            unicode_tables::PRINTABLE,
            unicode_tables::SPACE,
            unicode_tables::DIGIT,
        ] {
            for (start, end) in table {
                assert!(start <= end);
            }
            for pair in table.windows(2) {
                assert!(pair[0].1 + 1 < pair[1].0, "{pair:?}");
            }
        }
    }

    #[test]
    fn a_character_beyond_the_bmp_is_written_as_a_surrogate_pair() {
        assert_eq!(PyValue::str("\u{1F600}").to_typed(), r#""\ud83d\ude00""#);
    }
}
