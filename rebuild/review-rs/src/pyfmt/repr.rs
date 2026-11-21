//! CPython's `repr` over [`PyValue`], for the values the worker hashes (`ink.delta_digest`, the diffs digest, `build._cluster_id_from_repr`) and the `!r` and `{tuple}` fields of the strings it ships.
//!
//! It covers int, str, None, bool, tuple, list and dict, and refuses bytes, a set and a record, whose reprs no ported site reads.

use super::{PyFmtError, PyValue, is_printable};

/// `repr(value)`.
pub fn repr(value: &PyValue) -> Result<String, PyFmtError> {
    let mut out = String::new();
    write_repr(&mut out, value)?;
    Ok(out)
}

/// `repr(text)` for a `str`.
pub fn repr_str(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    write_str(&mut out, text);
    out
}

/// Append `repr(value)` to `out`: `(a,)` for a one-element tuple, `()` for an empty one, `", "` between items and `": "` after a dict key.
pub fn write_repr(out: &mut String, value: &PyValue) -> Result<(), PyFmtError> {
    match value {
        PyValue::None => out.push_str("None"),
        PyValue::Bool(true) => out.push_str("True"),
        PyValue::Bool(false) => out.push_str("False"),
        PyValue::Int(number) => out.push_str(&number.to_string()),
        PyValue::Str(text) => write_str(out, text),
        PyValue::Tuple(items) => {
            out.push('(');
            write_items(out, items)?;
            if items.len() == 1 {
                out.push(',');
            }
            out.push(')');
        }
        PyValue::List(items) => {
            out.push('[');
            write_items(out, items)?;
            out.push(']');
        }
        PyValue::Dict(entries) => {
            out.push('{');
            for (index, (key, item)) in entries.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_repr(out, key)?;
                out.push_str(": ");
                write_repr(out, item)?;
            }
            out.push('}');
        }
        PyValue::Bytes(_) | PyValue::Set(_) | PyValue::Record(..) => {
            return Err(PyFmtError(format!(
                "repr covers int, str, None, bool, tuple, list and dict, not {}",
                value.kind()
            )));
        }
    }
    Ok(())
}

fn write_items(out: &mut String, items: &[PyValue]) -> Result<(), PyFmtError> {
    for (index, item) in items.iter().enumerate() {
        if index > 0 {
            out.push_str(", ");
        }
        write_repr(out, item)?;
    }
    Ok(())
}

/// Append `repr(text)` as CPython's `unicode_repr` writes it: double quotes when the text holds `'` and no `"`, else single quotes; the quote, `\` and `\t`, `\n`, `\r` escaped by a backslash; any other ASCII control character as `\xNN`; and a non-ASCII character `str.isprintable` rejects as `\xNN`, `\uNNNN` or `\UNNNNNNNN`, whichever is the shortest that holds it, in lowercase hex.
pub fn write_str(out: &mut String, text: &str) {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    out.push(quote);
    for letter in text.chars() {
        let point = u32::from(letter);
        match letter {
            _ if letter == quote || letter == '\\' => {
                out.push('\\');
                out.push(letter);
            }
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            ' '..='~' => out.push(letter),
            _ if point >= 0x80 && is_printable(letter) => out.push(letter),
            _ if point <= 0xff => out.push_str(&format!("\\x{point:02x}")),
            _ if point <= 0xffff => out.push_str(&format!("\\u{point:04x}")),
            _ => out.push_str(&format!("\\U{point:08x}")),
        }
    }
    out.push(quote);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(value: &str) -> PyValue {
        PyValue::str(value)
    }

    #[test]
    fn tuples_lists_and_dicts_are_written_as_cpython_writes_them() {
        let one = PyValue::Tuple(vec![PyValue::Int(-3)]);
        let value = PyValue::Tuple(vec![
            PyValue::Tuple(vec![]),
            one,
            PyValue::List(vec![PyValue::None, PyValue::Bool(true)]),
            PyValue::Dict(vec![(text("k"), PyValue::Bool(false))]),
            PyValue::Dict(vec![]),
        ]);
        assert_eq!(
            repr(&value).expect("repr"),
            "((), (-3,), [None, True], {'k': False}, {})"
        );
    }

    #[test]
    fn a_str_takes_the_quote_cpython_picks_and_its_escapes() {
        assert_eq!(repr_str("it's"), "\"it's\"");
        assert_eq!(repr_str("it's \"x\""), "'it\\'s \"x\"'");
        assert_eq!(
            repr_str("a\\b\t\n\r\u{0}\u{7f}"),
            "'a\\\\b\\t\\n\\r\\x00\\x7f'"
        );
        assert_eq!(
            repr_str("\u{a0}\u{e9}\u{e650}\u{1f600}\u{e0001}"),
            "'\\xa0\u{e9}\\ue650\u{1f600}\\U000e0001'"
        );
    }

    #[test]
    fn bytes_a_set_and_a_record_are_refused() {
        for value in [
            PyValue::Bytes(vec![1]),
            PyValue::Set(vec![]),
            PyValue::Record("Unit".into(), vec![]),
        ] {
            assert!(repr(&PyValue::List(vec![value])).is_err());
        }
    }
}
