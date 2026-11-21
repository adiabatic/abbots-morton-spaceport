//! CPython's `json.dumps` over [`PyValue`], always with `ensure_ascii`, in the two forms the worker writes: the carry projection's `json.dumps(sort_keys=True)` (`unit_cache.carry_projection`), whose sha256 is a unit's content key, and a fragment's shard bytes, `json.dumps([fragment], indent=1, ensure_ascii=True)[1:-2]` (`build._fragment_bytes`).
//!
//! A tuple is written as a list, as `json` writes one. A dict key must be a `str`, and bytes, a set and a record are refused, as `json` refuses them; there is no float to write.

use super::{PyFmtError, PyValue};

/// `json.dumps(value, sort_keys=True)`: `", "` between items, `": "` after a key, and every dict's keys sorted by code point.
pub fn dumps_sorted(value: &PyValue) -> Result<String, PyFmtError> {
    let mut out = String::new();
    Encoder {
        indent: None,
        sort_keys: true,
    }
    .write(&mut out, value, 0)?;
    Ok(out)
}

/// `json.dumps([value], indent=1, ensure_ascii=True)[1:-2]`, a fragment's shard bytes: the value one level deep in a one-element list, with the list's brackets and the newline before its `]` cut away, so the text starts with a newline and one space. Dict keys keep their insertion order.
pub fn fragment_text(value: &PyValue) -> Result<String, PyFmtError> {
    let encoder = Encoder {
        indent: Some(1),
        sort_keys: false,
    };
    let mut out = String::new();
    encoder.newline(&mut out, 1);
    encoder.write(&mut out, value, 1)?;
    Ok(out)
}

/// Append one JSON string as `json`'s ASCII encoder writes it: `\"`, `\\`, `\n`, `\r`, `\t`, `\b` and `\f`; the printable ASCII characters as themselves; and every other character as `\uXXXX` in lowercase hex, beyond the BMP as a surrogate pair.
pub fn write_string(out: &mut String, text: &str) {
    out.push('"');
    for letter in text.chars() {
        match letter {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(letter),
            _ => {
                let mut units = [0u16; 2];
                for unit in letter.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
}

struct Encoder {
    indent: Option<usize>,
    sort_keys: bool,
}

impl Encoder {
    fn item_separator(&self) -> &'static str {
        if self.indent.is_some() { "," } else { ", " }
    }

    fn newline(&self, out: &mut String, level: usize) {
        if let Some(indent) = self.indent {
            out.push('\n');
            out.extend(std::iter::repeat_n(' ', indent * level));
        }
    }

    fn write(&self, out: &mut String, value: &PyValue, level: usize) -> Result<(), PyFmtError> {
        match value {
            PyValue::None => out.push_str("null"),
            PyValue::Bool(true) => out.push_str("true"),
            PyValue::Bool(false) => out.push_str("false"),
            PyValue::Int(number) => out.push_str(&number.to_string()),
            PyValue::Str(text) => write_string(out, text),
            PyValue::Tuple(items) | PyValue::List(items) => {
                if items.is_empty() {
                    out.push_str("[]");
                    return Ok(());
                }
                out.push('[');
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        out.push_str(self.item_separator());
                    }
                    self.newline(out, level + 1);
                    self.write(out, item, level + 1)?;
                }
                self.newline(out, level);
                out.push(']');
            }
            PyValue::Dict(entries) => {
                if entries.is_empty() {
                    out.push_str("{}");
                    return Ok(());
                }
                let mut keyed = entries
                    .iter()
                    .map(|(key, item)| match key {
                        PyValue::Str(text) => Ok((text.as_str(), item)),
                        other => Err(PyFmtError(format!(
                            "a JSON object key must be a str, not {}",
                            other.kind()
                        ))),
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                if self.sort_keys {
                    keyed.sort_by(|left, right| left.0.cmp(right.0));
                }
                out.push('{');
                for (index, (key, item)) in keyed.into_iter().enumerate() {
                    if index > 0 {
                        out.push_str(self.item_separator());
                    }
                    self.newline(out, level + 1);
                    write_string(out, key);
                    out.push_str(": ");
                    self.write(out, item, level + 1)?;
                }
                self.newline(out, level);
                out.push('}');
            }
            PyValue::Bytes(_) | PyValue::Set(_) | PyValue::Record(..) => {
                return Err(PyFmtError(format!(
                    "{} is not JSON serializable",
                    value.kind()
                )));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(value: &str) -> PyValue {
        PyValue::str(value)
    }

    fn sample() -> PyValue {
        PyValue::Dict(vec![
            (text("\u{1f600}"), PyValue::Int(1)),
            (text("\u{ffff}"), PyValue::Tuple(vec![])),
            (
                text("b"),
                PyValue::List(vec![PyValue::None, PyValue::Dict(vec![])]),
            ),
            (text("a"), text("\u{e650}\"\u{1f600}\u{7f}/")),
        ])
    }

    #[test]
    fn sorted_keys_follow_code_points_and_strings_escape_as_ascii() {
        assert_eq!(
            dumps_sorted(&sample()).expect("json"),
            r#"{"a": "\ue650\"\ud83d\ude00\u007f/", "b": [null, {}], "\uffff": [], "\ud83d\ude00": 1}"#
        );
    }

    #[test]
    fn a_fragment_is_indented_one_space_a_level_inside_its_cut_away_list() {
        assert_eq!(
            fragment_text(&sample()).expect("json"),
            "\n {\n  \"\\ud83d\\ude00\": 1,\n  \"\\uffff\": [],\n  \"b\": [\n   null,\n   {}\n  ],\n  \"a\": \"\\ue650\\\"\\ud83d\\ude00\\u007f/\"\n }"
        );
        assert_eq!(
            fragment_text(&PyValue::Dict(vec![])).expect("json"),
            "\n {}"
        );
    }

    #[test]
    fn a_non_str_key_and_a_set_are_refused() {
        assert!(dumps_sorted(&PyValue::Dict(vec![(PyValue::Int(1), PyValue::None)])).is_err());
        assert!(fragment_text(&PyValue::List(vec![PyValue::Set(vec![])])).is_err());
    }
}
