//! CPython's `json.dumps` and `json.loads` over [`PyValue`].
//!
//! The writers cover every form the worker and the corpus build's parent write (`rebuild/CORPUS-PARENT-PORT-PLAN.md` § JSON forms): `json.dumps(value)` ([`dumps`]), with `sort_keys=True` ([`dumps_sorted`], the carry projection whose sha256 is a unit's content key, and `manifest_sha256`), with `indent=1` ([`dumps_indent`], `manifest.json` and the row-delta heads), with `ensure_ascii=False` ([`dumps_unicode`], the index and app lines), with `separators=(",", ":")` ([`dumps_compact`], `review-facts.json`), and a fragment's shard bytes, `json.dumps([fragment], indent=1, ensure_ascii=True)[1:-2]` ([`fragment_text`]). A tuple is written as a list, as `json` writes one. A dict key must be a `str`: one `json` would stringify (an int, a bool, None) is refused, as are bytes, a set and a record, which `json` refuses; there is no float to write.
//!
//! The reader ([`loads`] for a `str`, [`loads_bytes`] for `bytes`) reads what `json.loads` reads into the value it gives: an object as a dict whose repeated key keeps its first place and its last value, an array as a list, and an int as an int. It refuses what `json.loads` refuses, and what `json.loads` reads but the value model cannot hold or the plan's § Reading what Python wrote has the Rust build refuse: a float, `NaN` and `Infinity` among them, an int beyond 64 bits, a lone surrogate, escaped or in the bytes, and text `json.detect_encoding` takes for UTF-16 or UTF-32.

use super::{PyFmtError, PyValue};

/// `json.dumps(value)`: `", "` between items, `": "` after a key, ASCII, every dict in its insertion order.
pub fn dumps(value: &PyValue) -> Result<String, PyFmtError> {
    Encoder::INLINE.dumps(value)
}

/// `json.dumps(value, sort_keys=True)`: `", "` between items, `": "` after a key, and every dict's keys sorted by code point.
pub fn dumps_sorted(value: &PyValue) -> Result<String, PyFmtError> {
    Encoder {
        sort_keys: true,
        ..Encoder::INLINE
    }
    .dumps(value)
}

/// `json.dumps(value, indent=1)`: each item on its own line, indented one space a level, `","` at a line's end and `": "` after a key, ASCII, every dict in its insertion order.
pub fn dumps_indent(value: &PyValue) -> Result<String, PyFmtError> {
    Encoder::INDENTED.dumps(value)
}

/// `json.dumps(value, ensure_ascii=False)`: [`dumps`]' separators and order, with every string written as [`write_unicode_string`] writes it.
pub fn dumps_unicode(value: &PyValue) -> Result<String, PyFmtError> {
    Encoder {
        ensure_ascii: false,
        ..Encoder::INLINE
    }
    .dumps(value)
}

/// `json.dumps(value, separators=(",", ":"))`: nothing between the items and a key's colon, ASCII, every dict in its insertion order.
pub fn dumps_compact(value: &PyValue) -> Result<String, PyFmtError> {
    Encoder {
        item_separator: ",",
        key_separator: ":",
        ..Encoder::INLINE
    }
    .dumps(value)
}

/// `json.dumps([value], indent=1, ensure_ascii=True)[1:-2]`, a fragment's shard bytes: the value one level deep in a one-element list, with the list's brackets and the newline before its `]` cut away, so the text starts with a newline and one space. Dict keys keep their insertion order.
pub fn fragment_text(value: &PyValue) -> Result<String, PyFmtError> {
    let encoder = Encoder::INDENTED;
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

/// Append one JSON string as `json` writes it with `ensure_ascii=False`: `\"`, `\\`, `\n`, `\r`, `\t`, `\b` and `\f`, every other C0 control as `\u00xx` in lowercase hex, and every other character, DEL and the non-ASCII ones among them, as itself.
pub fn write_unicode_string(out: &mut String, text: &str) {
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
            '\0'..='\u{1f}' => out.push_str(&format!("\\u{:04x}", u32::from(letter))),
            _ => out.push(letter),
        }
    }
    out.push('"');
}

struct Encoder {
    indent: Option<usize>,
    sort_keys: bool,
    ensure_ascii: bool,
    item_separator: &'static str,
    key_separator: &'static str,
}

impl Encoder {
    const INLINE: Self = Self {
        indent: None,
        sort_keys: false,
        ensure_ascii: true,
        item_separator: ", ",
        key_separator: ": ",
    };

    const INDENTED: Self = Self {
        indent: Some(1),
        item_separator: ",",
        ..Self::INLINE
    };

    fn dumps(&self, value: &PyValue) -> Result<String, PyFmtError> {
        let mut out = String::new();
        self.write(&mut out, value, 0)?;
        Ok(out)
    }

    fn newline(&self, out: &mut String, level: usize) {
        if let Some(indent) = self.indent {
            out.push('\n');
            out.extend(std::iter::repeat_n(' ', indent * level));
        }
    }

    fn string(&self, out: &mut String, text: &str) {
        if self.ensure_ascii {
            write_string(out, text);
        } else {
            write_unicode_string(out, text);
        }
    }

    fn write(&self, out: &mut String, value: &PyValue, level: usize) -> Result<(), PyFmtError> {
        match value {
            PyValue::None => out.push_str("null"),
            PyValue::Bool(true) => out.push_str("true"),
            PyValue::Bool(false) => out.push_str("false"),
            PyValue::Int(number) => out.push_str(&number.to_string()),
            PyValue::Str(text) => self.string(out, text),
            PyValue::Tuple(items) | PyValue::List(items) => {
                if items.is_empty() {
                    out.push_str("[]");
                    return Ok(());
                }
                out.push('[');
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        out.push_str(self.item_separator);
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
                        out.push_str(self.item_separator);
                    }
                    self.newline(out, level + 1);
                    self.string(out, key);
                    out.push_str(self.key_separator);
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

/// `json.loads(text)` for a `str`: the one value `text` holds, with only JSON's whitespace around it. A `str` that starts with a byte order mark is refused, as `json.loads` refuses one.
pub fn loads(text: &str) -> Result<PyValue, PyFmtError> {
    if text.starts_with('\u{feff}') {
        return Err(PyFmtError(
            "Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)".into(),
        ));
    }
    Reader::new(text).document()
}

/// `json.loads(data)` for `bytes`: the text decoded as `json.loads` decodes it, then read as [`loads`] reads a `str`. A UTF-8 byte order mark is dropped; bytes `json.detect_encoding` takes for UTF-16 or UTF-32, and bytes that are not UTF-8 (a surrogate `surrogatepass` would let through among them), are refused.
pub fn loads_bytes(data: &[u8]) -> Result<PyValue, PyFmtError> {
    let encoding = detect_encoding(data);
    if encoding != "utf-8" {
        return Err(PyFmtError(format!(
            "the JSON text is {encoding}, which the reader does not read"
        )));
    }
    let data = data.strip_prefix(b"\xef\xbb\xbf").unwrap_or(data);
    let text = std::str::from_utf8(data)
        .map_err(|failure| PyFmtError(format!("the JSON text is not UTF-8: {failure}")))?;
    Reader::new(text).document()
}

/// `json.detect_encoding`, with `utf-8-sig` reported as `utf-8`.
fn detect_encoding(data: &[u8]) -> &'static str {
    if data.starts_with(b"\0\0\xfe\xff") || data.starts_with(b"\xff\xfe\0\0") {
        return "utf-32";
    }
    if data.starts_with(b"\xfe\xff") || data.starts_with(b"\xff\xfe") {
        return "utf-16";
    }
    if data.starts_with(b"\xef\xbb\xbf") {
        return "utf-8";
    }
    match *data {
        [0, second, _, _, ..] => {
            if second != 0 {
                "utf-16-be"
            } else {
                "utf-32-be"
            }
        }
        [_, 0, third, fourth, ..] => {
            if third != 0 || fourth != 0 {
                "utf-16-le"
            } else {
                "utf-32-le"
            }
        }
        [0, _] => "utf-16-be",
        [_, 0] => "utf-16-le",
        _ => "utf-8",
    }
}

/// A scan of one JSON text as `json`'s C scanner walks it.
struct Reader<'a> {
    text: &'a str,
    bytes: &'a [u8],
    at: usize,
}

impl<'a> Reader<'a> {
    fn new(text: &'a str) -> Self {
        Self {
            text,
            bytes: text.as_bytes(),
            at: 0,
        }
    }

    fn fail(&self, message: &str) -> PyFmtError {
        PyFmtError(format!("{message} (byte {})", self.at))
    }

    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.at).copied()
    }

    fn whitespace(&mut self) {
        while let Some(b' ' | b'\t' | b'\n' | b'\r') = self.peek() {
            self.at += 1;
        }
    }

    fn document(mut self) -> Result<PyValue, PyFmtError> {
        self.whitespace();
        let value = self.value()?;
        self.whitespace();
        if self.at != self.bytes.len() {
            return Err(self.fail("Extra data"));
        }
        Ok(value)
    }

    fn literal(&mut self, word: &str, value: PyValue) -> Result<PyValue, PyFmtError> {
        if self.bytes[self.at..].starts_with(word.as_bytes()) {
            self.at += word.len();
            Ok(value)
        } else {
            Err(self.fail("Expecting value"))
        }
    }

    fn value(&mut self) -> Result<PyValue, PyFmtError> {
        match self.peek() {
            Some(b'"') => {
                self.at += 1;
                self.string().map(PyValue::Str)
            }
            Some(b'{') => {
                self.at += 1;
                self.object()
            }
            Some(b'[') => {
                self.at += 1;
                self.array()
            }
            Some(b'n') => self.literal("null", PyValue::None),
            Some(b't') => self.literal("true", PyValue::Bool(true)),
            Some(b'f') => self.literal("false", PyValue::Bool(false)),
            Some(b'-' | b'0'..=b'9') => self.number(),
            Some(b'N' | b'I') => {
                Err(self.fail("NaN and Infinity are floats, which the reader does not hold"))
            }
            _ => Err(self.fail("Expecting value")),
        }
    }

    fn number(&mut self) -> Result<PyValue, PyFmtError> {
        let start = self.at;
        if self.peek() == Some(b'-') {
            self.at += 1;
        }
        match self.peek() {
            Some(b'1'..=b'9') => {
                while let Some(b'0'..=b'9') = self.peek() {
                    self.at += 1;
                }
            }
            Some(b'0') => self.at += 1,
            Some(b'I') => {
                return Err(self.fail("-Infinity is a float, which the reader does not hold"));
            }
            _ => {
                self.at = start;
                return Err(self.fail("Expecting value"));
            }
        }
        if let Some(b'.' | b'e' | b'E') = self.peek() {
            return Err(self.fail(
                "a float, or an int followed by extra data, which the reader does not hold",
            ));
        }
        self.text[start..self.at]
            .parse::<i64>()
            .map(PyValue::Int)
            .map_err(|_| {
                PyFmtError(format!(
                    "the int {} does not fit 64 bits",
                    &self.text[start..self.at]
                ))
            })
    }

    fn string(&mut self) -> Result<String, PyFmtError> {
        let mut out = String::new();
        loop {
            let run = self.at;
            while let Some(byte) = self.peek() {
                if byte == b'"' || byte == b'\\' || byte < 0x20 {
                    break;
                }
                self.at += 1;
            }
            out.push_str(&self.text[run..self.at]);
            match self.peek() {
                None => return Err(self.fail("Unterminated string starting at")),
                Some(b'"') => {
                    self.at += 1;
                    return Ok(out);
                }
                Some(b'\\') => {
                    self.at += 1;
                    let escape = self
                        .peek()
                        .ok_or_else(|| self.fail("Unterminated string starting at"))?;
                    self.at += 1;
                    match escape {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => out.push(self.escaped_character()?),
                        _ => {
                            self.at -= 2;
                            return Err(self.fail("Invalid \\escape"));
                        }
                    }
                }
                Some(_) => return Err(self.fail("Invalid control character at")),
            }
        }
    }

    fn hex_unit(&mut self) -> Result<u32, PyFmtError> {
        let digits = self
            .bytes
            .get(self.at..self.at + 4)
            .filter(|digits| digits.iter().all(u8::is_ascii_hexdigit))
            .ok_or_else(|| self.fail("Invalid \\uXXXX escape"))?;
        let unit = digits.iter().fold(0, |unit, &digit| {
            (unit << 4) | char::from(digit).to_digit(16).unwrap_or(0)
        });
        self.at += 4;
        Ok(unit)
    }

    /// The character a `\u` escape names, read past the `u`: a high surrogate joins the low surrogate a second escape names, and a surrogate left alone is refused.
    fn escaped_character(&mut self) -> Result<char, PyFmtError> {
        let unit = self.hex_unit()?;
        let point = if (0xd800..0xdc00).contains(&unit) && self.bytes[self.at..].starts_with(b"\\u")
        {
            self.at += 2;
            let low = self.hex_unit()?;
            if !(0xdc00..0xe000).contains(&low) {
                return Err(self.fail("a lone surrogate, which a str of the reader does not hold"));
            }
            0x10000 + ((unit - 0xd800) << 10) + (low - 0xdc00)
        } else {
            unit
        };
        char::from_u32(point)
            .ok_or_else(|| self.fail("a lone surrogate, which a str of the reader does not hold"))
    }

    fn array(&mut self) -> Result<PyValue, PyFmtError> {
        let mut items = Vec::new();
        self.whitespace();
        if self.peek() == Some(b']') {
            self.at += 1;
            return Ok(PyValue::List(items));
        }
        loop {
            items.push(self.value()?);
            self.whitespace();
            match self.peek() {
                Some(b']') => {
                    self.at += 1;
                    return Ok(PyValue::List(items));
                }
                Some(b',') => {
                    self.at += 1;
                    self.whitespace();
                    if self.peek() == Some(b']') {
                        return Err(self.fail("Illegal trailing comma before end of array"));
                    }
                }
                _ => return Err(self.fail("Expecting ',' delimiter")),
            }
        }
    }

    fn object(&mut self) -> Result<PyValue, PyFmtError> {
        let mut entries: Vec<(PyValue, PyValue)> = Vec::new();
        self.whitespace();
        if self.peek() == Some(b'}') {
            self.at += 1;
            return Ok(PyValue::Dict(entries));
        }
        loop {
            if self.peek() != Some(b'"') {
                return Err(self.fail("Expecting property name enclosed in double quotes"));
            }
            self.at += 1;
            let key = self.string()?;
            self.whitespace();
            if self.peek() != Some(b':') {
                return Err(self.fail("Expecting ':' delimiter"));
            }
            self.at += 1;
            self.whitespace();
            let item = self.value()?;
            match entries
                .iter_mut()
                .find(|(held, _)| matches!(held, PyValue::Str(text) if *text == key))
            {
                Some(entry) => entry.1 = item,
                None => entries.push((PyValue::Str(key), item)),
            }
            self.whitespace();
            match self.peek() {
                Some(b'}') => {
                    self.at += 1;
                    return Ok(PyValue::Dict(entries));
                }
                Some(b',') => {
                    self.at += 1;
                    self.whitespace();
                    if self.peek() == Some(b'}') {
                        return Err(self.fail("Illegal trailing comma before end of object"));
                    }
                }
                _ => return Err(self.fail("Expecting ',' delimiter")),
            }
        }
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
    fn each_form_writes_its_separators_and_its_strings() {
        let value = PyValue::Dict(vec![
            (
                text("k\u{e9}"),
                PyValue::Tuple(vec![PyValue::Int(-1), text("\u{1}\u{7f}\u{1f600}")]),
            ),
            (text("e"), PyValue::Dict(vec![])),
        ]);
        assert_eq!(
            dumps(&value).expect("json"),
            r#"{"k\u00e9": [-1, "\u0001\u007f\ud83d\ude00"], "e": {}}"#
        );
        assert_eq!(
            dumps_compact(&value).expect("json"),
            r#"{"k\u00e9":[-1,"\u0001\u007f\ud83d\ude00"],"e":{}}"#
        );
        assert_eq!(
            dumps_unicode(&value).expect("json"),
            "{\"k\u{e9}\": [-1, \"\\u0001\u{7f}\u{1f600}\"], \"e\": {}}"
        );
        assert_eq!(
            dumps_indent(&value).expect("json"),
            "{\n \"k\\u00e9\": [\n  -1,\n  \"\\u0001\\u007f\\ud83d\\ude00\"\n ],\n \"e\": {}\n}"
        );
    }

    #[test]
    fn a_non_str_key_and_a_set_are_refused() {
        assert!(dumps_sorted(&PyValue::Dict(vec![(PyValue::Int(1), PyValue::None)])).is_err());
        assert!(dumps(&PyValue::Dict(vec![(PyValue::None, PyValue::None)])).is_err());
        assert!(fragment_text(&PyValue::List(vec![PyValue::Set(vec![])])).is_err());
    }

    #[test]
    fn a_repeated_key_keeps_its_first_place_and_its_last_value() {
        assert_eq!(
            loads(r#" {"a": 1, "b": [true, null], "a": "\u00e9\ud83d\ude00"} "#).expect("read"),
            PyValue::Dict(vec![
                (text("a"), text("\u{e9}\u{1f600}")),
                (
                    text("b"),
                    PyValue::List(vec![PyValue::Bool(true), PyValue::None])
                ),
            ])
        );
        assert_eq!(loads("-0").expect("read"), PyValue::Int(0));
        assert_eq!(
            loads("-9223372036854775808").expect("read"),
            PyValue::Int(i64::MIN)
        );
    }

    #[test]
    fn what_python_reads_but_the_value_model_cannot_hold_is_refused() {
        for text in [
            "1.5",
            "1e5",
            "NaN",
            "-Infinity",
            "9223372036854775808",
            r#""\ud800""#,
            r#""\udc00\ud800""#,
            "\u{feff}{}",
        ] {
            assert!(loads(text).is_err(), "{text:?} was read");
        }
        for data in [
            b"\"\xed\xa0\x80\"".as_slice(),
            b"1\0",
            b"\xff\xfe1\0",
            b"\0\0\0\x31",
        ] {
            assert!(loads_bytes(data).is_err(), "{data:?} was read");
        }
        assert_eq!(
            loads_bytes(b"\xef\xbb\xbf[]").expect("read"),
            PyValue::List(vec![])
        );
    }

    #[test]
    fn what_python_refuses_is_refused() {
        for text in [
            "",
            "[1,]",
            "{\"a\":1,}",
            "{a:1}",
            "01",
            "-",
            "+1",
            ".5",
            "[1 2]",
            "{} x",
            "\"\t\"",
            "\"\\x\"",
            "\"\\u12\"",
            "tru",
            "\u{a0}1",
            "1\u{c}",
        ] {
            assert!(loads(text).is_err(), "{text:?} was read");
        }
    }
}
